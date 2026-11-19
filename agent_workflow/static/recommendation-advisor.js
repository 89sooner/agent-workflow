'use strict';

window.WorkflowCheckAdvisor = {
  mount({ profile, criteria, request, applyGroups, canManage }) {
    const byId = id => document.getElementById(id);
    const panel = byId('advisor-options');
    const previewButton = byId('advisor-preview'), runButton = byId('advisor-run'), applyButton = byId('advisor-apply');
    const packet = byId('advisor-input'), providers = byId('advisor-providers'), result = byId('advisor-result');
    const inputs = () => ({ profile: profile(), criteria: criteria() });
    const names = list => list.length ? list.join(', ') : '기본 검사만';
    let generation = 0, preview = null, previewKey = '', selection = null;
    let previewPending = false, runPending = false;

    function controls() {
      previewButton.disabled = !canManage() || previewPending || runPending;
      runButton.hidden = !preview?.providers.length || selection !== null;
      runButton.disabled = !canManage() || runPending;
      applyButton.hidden = selection === null;
      applyButton.disabled = !canManage() || selection === null;
    }
    function reset() {
      generation++;
      preview = null; previewKey = ''; selection = null; previewPending = false;
      packet.textContent = ''; packet.hidden = true;
      providers.textContent = ''; providers.hidden = true;
      result.textContent = '';
      controls();
    }
    function current(version, key) {
      return generation === version && key === JSON.stringify(inputs()) && canManage();
    }
    function message(text) {
      const line = document.createElement('p');
      line.textContent = text;
      result.append(line);
    }

    previewButton.addEventListener('click', async () => {
      if (!canManage() || previewPending || runPending) return;
      reset();
      const form = inputs(), key = JSON.stringify(form), version = generation;
      if (!form.profile || !form.criteria.length) {
        result.textContent = '프로젝트 설정을 선택하고 완료 조건을 입력하세요.';
        return;
      }
      previewPending = true; controls();
      result.textContent = '로컬 Git 변경을 읽고 있습니다.';
      try {
        const report = await request('/api/check-advisor/preview', form);
        if (!current(version, key)) return;
        if (report.status !== 'preview' || report.profile !== form.profile || !report.input ||
            !report.input_digest || !Array.isArray(report.providers) || !Array.isArray(report.available_groups)) {
          throw new Error('미리보기 응답을 확인할 수 없습니다.');
        }
        preview = report; previewKey = key;
        packet.textContent = JSON.stringify(report.input, null, 2); packet.hidden = false;
        providers.textContent = report.providers.length
          ? report.providers.join(' → ') + ' · 요청하면 위 입력이 전달됩니다. 계정 사용량이 발생할 수 있습니다.'
          : '이 프로필은 공통 검사만 사용합니다.';
        providers.hidden = false;
        result.textContent = '';
      } catch (error) {
        if (current(version, key)) result.textContent = error.message;
      } finally {
        if (generation === version) previewPending = false;
        controls();
      }
    });

    runButton.addEventListener('click', async () => {
      const form = inputs(), key = JSON.stringify(form);
      if (!canManage() || !preview || !preview.providers.length || runPending || selection !== null) return;
      if (key !== previewKey) { reset(); return; }
      const version = generation, confirmed = preview;
      runPending = true; controls();
      result.textContent = '추천 응답을 기다리고 있습니다.';
      try {
        const report = await request('/api/check-advisor/run', { ...form, input_digest: confirmed.input_digest,
          providers: confirmed.providers });
        if (!current(version, key)) return;
        const partial = report.status === 'review_incomplete';
        if ((!partial && report.status !== 'complete') || report.profile !== form.profile ||
            report.input_digest !== confirmed.input_digest || !report.primary ||
            !Array.isArray(report.suggested_groups) || report.suggested_groups.some(n => !confirmed.available_groups.includes(n))) {
          throw new Error('추천 결과와 미리보기의 검사 묶음을 확인할 수 없습니다.');
        }
        result.textContent = '';
        for (const pass of [report.primary, report.review]) {
          if (pass && Array.isArray(pass.groups)) message(pass.provider + ' · ' + names(pass.groups) + ' — ' + pass.reason);
        }
        if (partial) message('두 번째 검토 미완료 · ' + (report.review?.error || '첫 모델의 선택만 받았습니다.'));
        selection = report.suggested_groups;
        message('제안한 선택 · ' + names(selection));
        applyButton.textContent = partial ? '첫 모델의 선택 적용' : '추천 묶음 선택';
      } catch (error) {
        if (current(version, key)) {
          reset();
          result.textContent = error.message + ' 미리보기를 다시 확인하거나 검사 묶음을 직접 선택하세요.';
        }
      } finally {
        runPending = false;
        controls();
      }
    });

    applyButton.addEventListener('click', () => {
      if (!canManage() || selection === null) return;
      if (JSON.stringify(inputs()) !== previewKey) { reset(); return; }
      applyGroups(selection.slice());
      result.textContent = '선택한 검사 묶음 · ' + names(selection);
      if (preview.input.change?.paths?.length && byId('create-isolate').checked) {
        message('별도 작업 폴더는 커밋된 HEAD에서 시작합니다. 미리 본 미커밋 변경까지 검사하려면 별도 작업 폴더 옵션을 해제하세요.');
      }
      selection = null; preview = null; controls();
      previewButton.focus();
    });

    result.setAttribute('role', 'status');
    result.setAttribute('aria-live', 'polite');
    panel.closest('dialog')?.addEventListener('close', reset);
    panel.closest('form')?.addEventListener('reset', reset);
    byId('create-profile')?.addEventListener('change', reset);
    byId('create-criteria')?.addEventListener('input', reset);
    byId('workspace-ledger')?.addEventListener('change', reset);
    panel.addEventListener('toggle', () => { if (panel.open) controls(); else reset(); });
    reset();
    return { reset };
  }
};
