// Exercise the real task-registration form. No provider, ledger write or timer is involved.
import assert from 'node:assert/strict';

function gate() {
  let open;
  const promise = new Promise(resolve => { open = resolve; });
  return { promise, open };
}

export async function exerciseAdvisor(page) {
  const $ = id => page.locator('#' + id);
  const calls = [], delayed = gate(), oldStarted = gate();
  const tag = '<img src=x onerror="window.advisorInjected=true">';
  const digest = 'a'.repeat(64);
  const routePattern = '**/api/check-advisor/*';
  const handle = async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    assert.equal(req.method(), 'POST');
    assert.equal(req.headers()['x-workflow-token'], 'fixture-token');
    const data = req.postDataJSON();
    calls.push({ path, data });
    const text = data.criteria[0];
    const base = { profile:data.profile, input_digest:digest, providers:['codex','claude'], available_groups:['ui','docs'] };
    if (path.endsWith('/preview')) {
      assert.deepEqual(Object.keys(data).sort(), ['criteria','profile']);
      const input = { criteria:data.criteria, change:{ diff:tag, paths:['web/sample'] } };
      return route.fulfill({ json:{ ...base, status:'preview', input,
        providers:text === '기본 검사만' ? [] : base.providers,
        available_groups:text === '기본 검사만' ? [] : base.available_groups } });
    }
    assert.deepEqual(Object.keys(data).sort(), ['criteria','input_digest','profile','providers']);
    assert.equal(data.input_digest, digest);
    assert.deepEqual(data.providers, base.providers);
    if (text === '닫기 전 요청') { oldStarted.open(); await delayed.promise; }
    if (text === '새 입력 필요') return route.fulfill({ status:400, json:{ error:'변경된 입력을 다시 확인하세요.' } });
    const partial = text === '한쪽 공급자 오류';
    return route.fulfill({ json:{...base, status:partial ? 'review_incomplete' : 'complete',
      primary:{provider:'codex',groups:['docs'],reason:'문서 변경 '+tag},
      review:partial ? {provider:'claude',error:'검토용 계정 사용량 부족'} : {provider:'claude',groups:['ui'],reason:'화면 변경'},
      suggested_groups:partial ? ['docs'] : ['docs','ui']} });
  };
  const preview = async text => {
    await $('create-criteria').fill(text);
    await $('advisor-preview').click();
    await $('advisor-input').waitFor({state:'visible'});
  };
  await page.route(routePattern, handle);
  await $('new-task').click();
  const initial = await $('create-criteria').inputValue();
  const groups = page.locator('#check-group-options input');
  const original = await groups.evaluateAll(boxes => boxes.filter(b=>b.checked).map(b=>b.value));
  const submitCount = () => calls.filter(c => c.path.endsWith('/run')).length;
  try {
    await page.locator('#advisor-options summary').click();
    assert.equal(calls.length, 0);
    await $('create-criteria').fill('');
    await $('advisor-preview').click();
    assert.match(await $('advisor-result').textContent(), /완료 조건/);
    assert.equal(calls.length, 0);

    await preview('기본 검사만');
    assert.ok(await $('advisor-run').isHidden());
    assert.equal(submitCount(), 0);
    await preview('새 입력 필요');
    assert.ok((await $('advisor-input').textContent()).includes('web/sample'));
    assert.ok((await $('advisor-providers').textContent()).includes('claude'));
    await $('advisor-run').click();
    await page.getByText(/변경된 입력을 다시 확인하세요/).waitFor();
    assert.ok(await $('advisor-run').isHidden());

    await preview('닫기 전 요청');
    await Promise.all([oldStarted.promise, $('advisor-run').click()]);
    await $('create-close').click();
    await $('new-task').click();
    await $('create-criteria').fill('다른 작업');
    const completed = page.waitForResponse(r => r.url().endsWith('/api/check-advisor/run'));
    delayed.open();
    await (await completed).finished();
    await page.waitForFunction(() => !document.getElementById('advisor-preview').disabled);
    assert.ok(await $('advisor-apply').isHidden());

    if (!(await $('advisor-options').evaluate(el=>el.open))) await page.locator('#advisor-options summary').click();
    await preview('양쪽 공급자 확인');
    await $('advisor-run').click();
    await $('advisor-apply').waitFor({state:'visible'});
    assert.match(await $('advisor-result').textContent(), /claude.*ui/);
    assert.deepEqual(await groups.evaluateAll(boxes => boxes.filter(b=>b.checked).map(b=>b.value)), original);
    await $('create-criteria').fill('완료 조건 수정');
    assert.ok(await $('advisor-apply').isHidden());

    await preview('한쪽 공급자 오류');
    await $('advisor-run').click();
    await page.getByRole('button', {name:'첫 모델의 선택 적용'}).waitFor();
    assert.match(await $('advisor-result').textContent(), /두 번째 검토 미완료/);
    assert.equal(await $('advisor-result').locator('img').count(), 0);
    assert.equal(await page.evaluate(()=>Boolean(window.advisorInjected)), false);
    await $('create-isolate').check();
    await $('advisor-apply').click();
    assert.match(await $('advisor-result').textContent(), /미커밋 변경/);
    assert.deepEqual(await groups.evaluateAll(boxes => boxes.filter(b=>b.checked).map(b=>b.value)), ['docs']);
    assert.equal(submitCount(), 4);
    console.log('PASS optional advisor preview, explicit provider call, stale input, partial review and temporary group selection');
  } finally {
    delayed.open();
    for (const box of await groups.all()) await box.setChecked(original.includes(await box.inputValue()));
    await $('create-criteria').fill(initial);
    if (await $('advisor-options').evaluate(el=>el.open)) await page.locator('#advisor-options summary').click();
    await $('create-close').click();
    await page.unroute(routePattern, handle);
  }
}
