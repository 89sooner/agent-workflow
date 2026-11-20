import { useEffect, useRef, useState } from "react";
import { configure, endpoint, get, usePoll } from "./api";
import { label, when } from "./model";
import type { Execution } from "./types";
import "./management.css";

interface TaskRecord {
  id: string;
  goal: string;
  project: string;
  project_source?: string;
  project_key?: string;
  status: string;
  updated_at?: string;
  scope?: string;
  owner?: string;
  reviewer?: string;
  agent_names?: { implement?: string; review?: string };
  criteria?: string[];
  checks?: string[][];
  refs?: string[];
  checkpoint?: { summary?: string; next?: string };
  hold?: { reason?: string } | null;
  blocker?: string | null;
  implementation?: { outcome?: string; summary?: string };
  verification?: {
    passed?: boolean;
    results?: {
      argv: string[];
      exit_code?: number;
      timed_out?: boolean;
      seconds?: number;
    }[];
  };
  review?: { verdict?: string; summary?: string };
  profile?: { name: string; check_groups?: string[] };
  project_config?: { check_groups?: Record<string, string[][]> };
  execution?: {
    max_agent_calls?: number;
    models?: { implement?: string; review?: string };
  };
  usage?: { agent_calls?: number };
  recoveries?: unknown[];
  release?: {
    stage?: string;
    error?: string;
    commit?: string;
    merge_commit?: string;
    config?: { deploy?: unknown; base?: string; branch?: string };
    integration_verification?: { passed?: boolean; revision?: string };
  };
}
interface Job {
  id: string;
  task_id: string;
  status: string;
  action: string;
  source?: string;
  created_at?: string | null;
  log_path?: string | null;
  error?: string;
  cancel_requested?: boolean;
}
export interface LedgerState {
  ledger: string;
  read_only: boolean;
  tasks: TaskRecord[];
  jobs: Job[];
  notifications: {
    id: string;
    at: string;
    delivery: string;
    payload: {
      task_id: string;
      status?: string;
      blocker?: string;
      release_error?: string;
      job?: { status?: string };
    };
  }[];
  profiles: {
    name: string;
    config: {
      project?: string;
      owner?: string;
      reviewer?: string;
      checks?: string[][];
      check_groups?: Record<string, string[][]>;
    };
  }[];
  daemon?: { running?: boolean };
  version?: string;
}
interface Recommendation {
  recommendation_id: string;
  primary: string[] | null;
  reviewer: string[] | null;
  adopted_groups: string[];
  expected_groups: string[] | null;
  available_groups?: string[];
  checks_by_group: Record<
    string,
    {
      planned_count: number;
      executed_count: number;
      passed_count: number;
      failed_count: number;
      seconds: number | null;
    }
  >;
  verification_at?: string | null;
  review_ms?: number | null;
  unnecessary_review_check_seconds?: number | null;
}
interface TaskDetail {
  katakuri_goal?: GoalProgress | null;
  task: TaskRecord;
  observed_git?: {
    error?: string;
    head?: string;
    branch?: string;
    dirty?: boolean;
  };
  freshness?: {
    verification_is_current?: boolean;
    review_is_current?: boolean;
    completion_is_current?: boolean;
  };
  release_status?: Record<string, unknown>;
  recommendation?: Recommendation | null;
  events: { seq: number; at: string; kind: string; data?: unknown }[];
}
type Mutate = (
  path: string,
  body: Record<string, unknown>,
  success: string,
) => Promise<boolean>;
interface Props {
  state: ReturnType<typeof usePoll<LedgerState>>;
  ledger: string;
  taskId: string;
  canManage: boolean;
  token: string;
  refresh: number;
  onRefresh: () => void;
  project?: string;
  projectKey?: string;
  executions?: Execution[];
  onSelectTask?: (id: string) => void;
}

const phaseNames = {
  local_verification: "로컬 검사",
  local_completion: "로컬 완료",
  merge: "병합",
  integration_check: "통합 검사",
  deployment: "배포 적용",
  health: "배포 후 상태 검사",
};
const phaseLabels: Record<string, string> = {
  not_configured: "미설정",
  not_attempted: "시도 전",
  not_recorded: "기록 없음",
  not_completed: "미완료",
  uncertain: "결과 미확인",
  succeeded: "성공",
  applied: "적용됨",
  failed: "실패",
};
const active = (status: string) => status === "running" || status === "queued";
const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
const groupsText = (values: string[] | null) =>
  values === null ? "기록 없음" : values.join(", ") || "추가 묶음 없음";
const evidence = (passed: boolean | undefined, current: boolean | undefined) =>
  passed === undefined
    ? "기록 없음"
    : current === undefined
      ? "현재 유효성 미확인"
      : !current
        ? "코드·입력 변경 · 재검증 필요"
        : passed
          ? "통과"
          : "실패";

function ReleaseReceipt({ data }: { data: Record<string, unknown> }) {
  return (
    <details className="mg-block">
      <summary>원장에 기록된 릴리스 결과</summary>
      <dl className="mg-facts">
        {Object.entries(phaseNames).map(([key, name]) => (
          <div key={key}>
            <dt>{name}</dt>
            <dd>{phaseLabels[String(data[key])] || "미확인"}</dd>
          </div>
        ))}
      </dl>
      {typeof data.merge_commit === "string" && (
        <p>
          병합 커밋 <code>{data.merge_commit}</code>
        </p>
      )}
      {typeof data.deployment_revision === "string" && (
        <p>
          배포 리비전 <code>{data.deployment_revision}</code>
        </p>
      )}
    </details>
  );
}

function RecommendationDetail({
  receipt,
  task,
  writable,
  busy,
  mutate,
}: {
  receipt: Recommendation;
  task: TaskRecord;
  writable: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const [draft, setDraft] = useState<string[] | null>(null);
  const [saved, setSaved] = useState<string[] | null>(null);
  useEffect(() => {
    if (
      saved &&
      receipt.expected_groups &&
      saved.length === receipt.expected_groups.length &&
      saved.every((name) => receipt.expected_groups!.includes(name))
    ) {
      setDraft(null);
      setSaved(null);
    }
  }, [saved, receipt.expected_groups]);
  const chosen = draft ?? receipt.expected_groups ?? [];
  return (
    <details className="mg-block">
      <summary>검사 추천과 실제 작업 결과</summary>
      <p>
        <code>{receipt.recommendation_id}</code>
      </p>
      <dl className="mg-facts">
        <div>
          <dt>첫 모델</dt>
          <dd>{groupsText(receipt.primary)}</dd>
        </div>
        <div>
          <dt>두 번째 모델</dt>
          <dd>{groupsText(receipt.reviewer)}</dd>
        </div>
        <div>
          <dt>채택</dt>
          <dd>{groupsText(receipt.adopted_groups)}</dd>
        </div>
        <div>
          <dt>사람이 필요하다고 판정</dt>
          <dd>{groupsText(receipt.expected_groups)}</dd>
        </div>
      </dl>
      {Object.entries(receipt.checks_by_group || {}).map(([name, r]) => (
        <p key={name}>
          {name} · 실행 {r.executed_count}/{r.planned_count} · 통과{" "}
          {r.passed_count} · 실패 {r.failed_count}
          {r.seconds != null && ` · ${r.seconds.toFixed(2)}초`}
        </p>
      ))}
      {receipt.verification_at && (
        <p>최근 완료된 검사 · {when(receipt.verification_at)}</p>
      )}
      {receipt.review_ms != null && (
        <p>두 번째 모델 응답 · {(receipt.review_ms / 1000).toFixed(2)}초</p>
      )}
      {receipt.unnecessary_review_check_seconds != null && (
        <p>
          두 번째 모델만 추가한 불필요한 검사의 실행 시간 ·{" "}
          {receipt.unnecessary_review_check_seconds.toFixed(2)}초
        </p>
      )}
      {writable && (
        <fieldset className="mg-fieldset" disabled={busy}>
          <legend>실제로 필요했던 검사 묶음</legend>
          <div className="mg-flex">
            {(
              receipt.available_groups ??
              Object.keys(task.project_config?.check_groups || {})
            ).map((name) => (
              <label className="mg-choice" key={name}>
                <input
                  type="checkbox"
                  checked={chosen.includes(name)}
                  onChange={(e) => {
                    setSaved(null);
                    setDraft(
                      e.target.checked
                        ? [...chosen, name]
                        : chosen.filter((x) => x !== name),
                    );
                  }}
                />
                {name}
              </label>
            ))}
          </div>
          <button
            type="button"
            onClick={async () => {
              if (
                await mutate(
                  "/api/recommendations/feedback",
                  { task_id: task.id, expected_groups: chosen },
                  "검사 추천 평가를 저장했습니다.",
                )
              )
                setSaved(chosen);
            }}
          >
            필요 묶음 저장
          </button>
        </fieldset>
      )}
    </details>
  );
}

const limits = [
  { name: "timeout", label: "프로세스 시간 제한(초)", min: 1, max: 86400 },
  { name: "repairs", label: "추가 수정 횟수", min: 0, max: 3 },
  { name: "recoveries", label: "오류 복구 횟수", min: 0, max: 3 },
] as const;

function TaskControls({
  task,
  pending,
  busy,
  mutate,
}: {
  task: TaskRecord;
  pending: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const form = useRef<HTMLFieldSetElement>(null);
  const options = useRef<HTMLDetailsElement>(null);
  const [optionError, setOptionError] = useState("");
  const [holding, setHolding] = useState(false);
  const [reason, setReason] = useState("");
  const release = task.release;
  const integrated =
    release?.stage === "merged" &&
    !release.config?.deploy &&
    release.integration_verification?.passed &&
    release.integration_verification.revision === release.merge_commit;
  function queue(action: string) {
    const payload: Record<string, unknown> = { task_id: task.id, action };
    for (const { name } of limits) {
      const input = form.current?.querySelector<HTMLInputElement>(
        `input[name="${name}"]`,
      );
      if (!input) return;
      if (!input.validity.valid) {
        setOptionError("실행 옵션의 허용 범위를 확인해 주세요.");
        if (options.current) options.current.open = true;
        input.focus();
        return;
      }
      if (input.value !== "") payload[name] = input.valueAsNumber;
    }
    setOptionError("");
    void mutate("/api/queue", payload, "실행 대기열에 등록했습니다.");
  }
  return (
    <div className="mg-controls">
      <details ref={options} className="mg-block">
        <summary>실행 옵션</summary>
        <fieldset
          className="mg-fields"
          ref={form}
          disabled={busy}
          onInput={() => {
            if (
              form.current &&
              Array.from(form.current.querySelectorAll("input")).every(
                (i) => i.validity.valid,
              )
            )
              setOptionError("");
          }}
        >
          {limits.map((o) => (
            <label key={o.name}>
              {o.label}
              <input
                type="number"
                name={o.name}
                min={o.min}
                max={o.max}
                step="1"
                placeholder="프로젝트 기본값"
              />
            </label>
          ))}
        </fieldset>
        {optionError && (
          <p className="mg-error" role="alert">
            {optionError}
          </p>
        )}
      </details>
      <div className="mg-flex">
        <button
          className="mg-primary"
          disabled={
            busy || pending || !!task.hold || task.status === "completed"
          }
          onClick={() => queue("pipeline")}
        >
          워크플로 실행
        </button>
        {release && (
          <button
            disabled={
              busy ||
              pending ||
              !!task.hold ||
              release.stage === "deployed" ||
              !!integrated
            }
            onClick={() => queue("release")}
          >
            통합 · 배포 실행
          </button>
        )}
        {task.hold ? (
          <button
            disabled={busy}
            onClick={() =>
              void mutate(
                "/api/unhold",
                { task_id: task.id },
                "작업 보류를 해제했습니다.",
              )
            }
          >
            보류 해제
          </button>
        ) : (
          <button
            disabled={busy}
            aria-expanded={holding}
            onClick={() => setHolding(!holding)}
          >
            작업 보류
          </button>
        )}
        {release?.stage === "configured" && !release.commit && (
          <button
            disabled={busy || pending}
            onClick={() =>
              void mutate(
                "/api/release-clear",
                { task_id: task.id },
                "릴리스 설정을 해제했습니다.",
              )
            }
          >
            릴리스 설정 해제
          </button>
        )}
      </div>
      {pending && <p>이 작업의 실행이 이미 대기 중이거나 진행 중입니다.</p>}
      {holding && !task.hold && (
        <form
          className="mg-hold"
          onSubmit={async (e) => {
            e.preventDefault();
            if (
              reason.trim() &&
              (await mutate(
                "/api/hold",
                { task_id: task.id, reason: reason.trim() },
                "작업을 보류했습니다.",
              ))
            ) {
              setHolding(false);
              setReason("");
            }
          }}
        >
          <label>
            보류 이유
            <textarea
              autoFocus
              required
              maxLength={2000}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
            />
          </label>
          <div className="mg-flex">
            <button type="submit" disabled={busy || !reason.trim()}>
              보류 적용
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => setHolding(false)}
            >
              취소
            </button>
          </div>
        </form>
      )}
    </div>
  );
}

interface GoalProgress {
  phase: string;
  instrument: string | null;
  milestone: string | null;
  agent_calls: number;
  max_agent_calls: number;
  reported_tokens: number;
  max_reported_tokens: number | null;
  max_seconds: number;
  seconds_used: number;
  uncertain_seconds: number;
  unreported_calls: number;
  criteria: {
    id: number;
    text: string;
    state: string;
    summary: string;
    next_action: string;
  }[];
  last_audit: {
    rows: { criterion: number; accepted: boolean; evidence: string }[];
  } | null;
}

const goalLabels: Record<string, string> = {
  planning: "완료 조건 작성",
  working: "구현",
  checking: "검사",
  reviewing: "독립 검토",
  complete: "완료",
  stopped: "중단",
  waiting: "근거 대기",
  pending: "실행 예정",
  proposed: "검증 요청",
  accepted: "수락",
};

function GoalPanel({
  goal,
  taskId,
  editable,
  mutate,
}: {
  goal: GoalProgress;
  taskId: string;
  editable: boolean;
  mutate: Mutate;
}) {
  const [input, setInput] = useState("");
  const [target, setTarget] = useState("");
  const waiting = goal.criteria.filter((row) => row.state === "waiting");
  const criterion =
    waiting.find((row) => String(row.id) === target)?.id ?? waiting[0]?.id;
  return (
    <section className="mg-block" aria-label="Katakuri 목표 진행">
      <h4>
        Katakuri 목표 ·{" "}
        {[goal.instrument, goal.milestone, goalLabels[goal.phase] ?? goal.phase]
          .filter(Boolean)
          .join(" · ")}
      </h4>
      <p>
        모델 호출 {goal.agent_calls}/{goal.max_agent_calls} · 보고 토큰{" "}
        {goal.reported_tokens}
        {goal.max_reported_tokens !== null && `/${goal.max_reported_tokens}`}
        {goal.unreported_calls > 0 &&
          ` · 사용량 미확인 ${goal.unreported_calls}회`}
        {" · "}실행 시간 {Math.ceil(goal.seconds_used + goal.uncertain_seconds)}
        /{goal.max_seconds}초
      </p>
      <ol>
        {goal.criteria.map((row) => (
          <li key={row.id}>
            {row.text} · {goalLabels[row.state] ?? row.state}
            {row.summary && <p>{row.summary}</p>}
            {row.next_action && row.state !== "accepted" && (
              <p>{row.next_action}</p>
            )}
            {goal.last_audit?.rows.find((r) => r.criterion === row.id)
              ?.evidence && (
              <p>
                최근 검토 근거 ·{" "}
                {
                  goal.last_audit.rows.find((r) => r.criterion === row.id)
                    ?.evidence
                }
              </p>
            )}
          </li>
        ))}
      </ol>
      {editable && criterion !== undefined && (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            if (
              await mutate(
                "/api/katakuri-goals/input",
                { task_id: taskId, criterion, evidence: input },
                "근거를 저장했습니다. 워크플로 실행을 누르면 이어갑니다.",
              )
            )
              setInput("");
          }}
        >
          <label>
            자료를 기다리는 조건
            <select
              value={criterion}
              onChange={(e) => setTarget(e.target.value)}
            >
              {waiting.map((row) => (
                <option value={row.id} key={row.id}>
                  {row.id}. {row.text}
                </option>
              ))}
            </select>
          </label>
          <label>
            제출할 근거
            <textarea
              required
              maxLength={1500}
              value={input}
              onChange={(e) => setInput(e.target.value)}
            />
          </label>
          <button disabled={!input.trim()}>근거 저장</button>
        </form>
      )}
      {editable && (
        <details>
          <summary>누적 예산 조정</summary>
          <form
            onSubmit={async (e) => {
              e.preventDefault();
              const form = e.currentTarget;
              const body: Record<string, unknown> = { task_id: taskId };
              for (const [key, value] of new FormData(form))
                if (value)
                  body[key] =
                    key === "clear_token_limit" ? true : Number(value);
              if (
                await mutate(
                  "/api/katakuri-goals/budget",
                  body,
                  "누적 예산을 변경했습니다.",
                )
              )
                form.reset();
            }}
          >
            <label>
              총 모델 호출 수
              <input
                name="max_agent_calls"
                type="number"
                min={1}
                max={200}
                placeholder={String(goal.max_agent_calls)}
              />
            </label>
            <label>
              총 실행 시간(초)
              <input
                name="max_seconds"
                type="number"
                min={1}
                max={86400}
                placeholder={String(goal.max_seconds)}
              />
            </label>
            <label>
              보고 토큰 한도
              <input
                name="max_reported_tokens"
                type="number"
                min={1}
                max={1e12}
                placeholder={String(goal.max_reported_tokens ?? "선택")}
              />
            </label>
            {goal.max_reported_tokens !== null && (
              <label>
                <input name="clear_token_limit" type="checkbox" />
                토큰 한도 해제
              </label>
            )}
            <button>예산 저장</button>
          </form>
        </details>
      )}
    </section>
  );
}

function TaskDetails({
  data,
  writable,
  pending,
  busy,
  mutate,
}: {
  data: TaskDetail;
  writable: boolean;
  pending: boolean;
  busy: boolean;
  mutate: Mutate;
}) {
  const t = data.task;
  return (
    <article className="mg-task" aria-label="선택한 작업 상세">
      <div className="mg-title">
        <code>{t.id}</code>
        <span>{label(t.status)}</span>
      </div>
      <h3>{t.goal}</h3>
      <p className="mg-path">{t.project}</p>
      {data.katakuri_goal && (
        <GoalPanel
          key={t.id}
          goal={data.katakuri_goal}
          taskId={t.id}
          editable={writable && !pending && !busy && t.status !== "completed"}
          mutate={mutate}
        />
      )}
      <dl className="mg-facts">
        <div>
          <dt>구현 / 검토</dt>
          <dd>
            {t.agent_names?.implement || t.owner || "미지정"} /{" "}
            {t.agent_names?.review ||
              (t.reviewer === "none" ? "미지정" : t.reviewer) ||
              "미지정"}
          </dd>
        </div>
        <div>
          <dt>필수 검사</dt>
          <dd>
            {evidence(
              t.verification?.passed,
              data.freshness?.verification_is_current,
            )}
          </dd>
        </div>
        <div>
          <dt>독립 리뷰</dt>
          <dd>
            {t.reviewer === "none"
              ? "미지정"
              : evidence(
                  t.review?.verdict == null
                    ? undefined
                    : t.review.verdict === "pass",
                  data.freshness?.review_is_current,
                )}
          </dd>
        </div>
        {t.status === "completed" && (
          <div>
            <dt>완료 증거</dt>
            <dd>
              {data.freshness?.completion_is_current === true
                ? "현재 코드에서 유효"
                : data.freshness?.completion_is_current === false
                  ? "변경 후 재검증 필요"
                  : "현재 상태 확인 불가"}
            </dd>
          </div>
        )}
      </dl>
      {data.observed_git?.error && (
        <p className="mg-error" role="status">
          작업 폴더 상태 확인 불가 · {data.observed_git.error}
        </p>
      )}
      {t.hold && (
        <p className="mg-attention">
          수동 보류 · {t.hold.reason || "사유 미기록"}
        </p>
      )}
      {(t.blocker || t.release?.error) && (
        <p className="mg-error">
          {[t.blocker, t.release?.error].filter(Boolean).join(" · ")}
        </p>
      )}
      {writable && (
        <TaskControls task={t} pending={pending} busy={busy} mutate={mutate} />
      )}
      <div className="mg-continuity">
        <h4>최근 체크포인트</h4>
        <p>{t.checkpoint?.summary || "기록 없음"}</p>
        <h4>다음 행동</h4>
        <p>{t.checkpoint?.next || "기록 없음"}</p>
      </div>
      <details className="mg-block">
        <summary>작업 계약과 검사 결과</summary>
        {t.scope && <p>허용 범위 · {t.scope}</p>}
        {t.criteria?.length ? (
          <>
            <h4>완료 조건</h4>
            <ul>
              {t.criteria.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          </>
        ) : null}
        {t.refs?.length ? (
          <>
            <h4>참조 문서</h4>
            <ul>
              {t.refs.map((s, i) => (
                <li key={i}>
                  <code>{s}</code>
                </li>
              ))}
            </ul>
          </>
        ) : null}
        {!!t.checks?.length && (
          <>
            <h4>등록된 검사</h4>
            <ol>
              {t.checks.map((argv, i) => (
                <li key={i}>
                  <code>{JSON.stringify(argv)}</code>
                </li>
              ))}
            </ol>
          </>
        )}
        {!!t.verification?.results?.length && (
          <>
            <h4>최근 검사 결과</h4>
            <ul>
              {t.verification.results.map((r, i) => (
                <li key={i}>
                  <code>{JSON.stringify(r.argv)}</code> ·{" "}
                  {r.timed_out
                    ? "시간 초과"
                    : `종료 코드 ${r.exit_code ?? "미기록"}`}
                  {r.seconds != null && ` · ${r.seconds}초`}
                </li>
              ))}
            </ul>
          </>
        )}
        {t.implementation?.summary && <p>구현 · {t.implementation.summary}</p>}
        {t.review?.summary && <p>리뷰 · {t.review.summary}</p>}
        {data.observed_git?.head && (
          <p>
            현재 HEAD · <code>{data.observed_git.head}</code>{" "}
            {data.observed_git.branch}
          </p>
        )}
        {t.profile?.name && (
          <p>
            프로필 · {t.profile.name}
            {t.profile.check_groups?.length
              ? ` / ${t.profile.check_groups.join(", ")}`
              : ""}
          </p>
        )}
        {t.usage && (
          <p>
            모델 호출 · {t.usage.agent_calls || 0} /{" "}
            {t.execution?.max_agent_calls ?? 12}회
          </p>
        )}
        {Object.entries(t.execution?.models || {}).map(([role, model]) => (
          <p key={role}>
            {label(role)} 모델 · {model}
          </p>
        ))}
        {t.recoveries?.length ? (
          <p>복구 시도 · {t.recoveries.length}회</p>
        ) : null}
      </details>
      {t.release && data.release_status && (
        <ReleaseReceipt data={data.release_status} />
      )}
      {data.recommendation && (
        <RecommendationDetail
          key={data.recommendation.recommendation_id}
          receipt={data.recommendation}
          task={t}
          writable={writable}
          busy={busy}
          mutate={mutate}
        />
      )}
      <details className="mg-block">
        <summary>최근 실행 기록 · {data.events?.length || 0}건</summary>
        {data.events?.length ? (
          <ol className="mg-events">
            {data.events.map((e) => (
              <li key={e.seq}>
                <details>
                  <summary>
                    {when(e.at)} · {label(e.kind)}
                  </summary>
                  <pre>{JSON.stringify(e.data, null, 2)}</pre>
                </details>
              </li>
            ))}
          </ol>
        ) : (
          <p>실행 기록이 없습니다.</p>
        )}
      </details>
    </article>
  );
}

function JobLog({ ledger, id }: { ledger: string; id: string }) {
  const [open, setOpen] = useState(false);
  const [revision, setRevision] = useState(0);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    let current = true;
    const abort = new AbortController();
    setText("");
    setError("");
    get<{ text: string; truncated: boolean }>(
      endpoint("/api/job-log/" + encodeURIComponent(id), ledger),
      abort.signal,
    )
      .then((result) => {
        if (current)
          setText(
            (result.truncated ? "[최근 64 KiB]\n" : "") +
              (result.text || "로그가 비어 있습니다."),
          );
      })
      .catch((e) => {
        if (current) setError(errorText(e));
      });
    return () => {
      current = false;
      abort.abort();
    };
  }, [open, revision, id, ledger]);
  return (
    <details
      className="mg-job-log"
      onToggle={(e) => setOpen(e.currentTarget.open)}
    >
      <summary>실행 로그</summary>
      {error ? (
        <p role="alert">{error}</p>
      ) : (
        <pre>{text || "불러오는 중…"}</pre>
      )}
      <button onClick={() => setRevision((n) => n + 1)}>로그 새로고침</button>
    </details>
  );
}

export default function Management(props: Props) {
  return (
    <Workspace
      key={JSON.stringify([props.ledger, props.taskId, props.project])}
      {...props}
    />
  );
}

function Workspace({
  state,
  ledger,
  taskId,
  canManage,
  token,
  refresh,
  onRefresh,
  project,
  projectKey,
  executions,
  onSelectTask,
}: Props) {
  const [revision, setRevision] = useState(0);
  const detail = usePoll<TaskDetail>(
    taskId
      ? endpoint("/api/tasks/" + encodeURIComponent(taskId), ledger)
      : null,
    7000,
    refresh + revision,
  );
  const [sending, setBusy] = useState(false),
    [notice, setNotice] = useState(""),
    [error, setError] = useState("");
  const [readAfter, setReadAfter] = useState(0);
  const busy =
    sending || state.at < readAfter || (!!taskId && detail.at < readAfter);
  const live = useRef(false),
    writing = useRef<AbortController | null>(null);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
      writing.current?.abort();
    };
  }, []);
  const snapshot = state.data;
  const writable = !!(
    canManage &&
    token &&
    snapshot?.ledger === ledger &&
    snapshot?.read_only === false &&
    !state.error
  );
  const mutate: Mutate = async (path, body, success) => {
    if (!writable || busy || writing.current) return false;
    const abort = new AbortController();
    writing.current = abort;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      await configure(path, body, token, abort.signal);
      if (!live.current) return false;
      setReadAfter(Date.now());
      setNotice(success);
      setRevision((r) => r + 1);
      onRefresh();
      return true;
    } catch (e) {
      if (live.current) setError(errorText(e));
      return false;
    } finally {
      if (live.current) setBusy(false);
      writing.current = null;
    }
  };
  const selected = detail.data?.task.id === taskId ? detail.data : null;
  const tasks = snapshot?.tasks || [];
  const visible = new Set(
    tasks
      .filter(
        (t) =>
          !project ||
          (projectKey && t.project_key === projectKey) ||
          t.project_source === project ||
          t.project === project,
      )
      .map((t) => t.id),
  );
  const allJobs = snapshot?.jobs || [];
  const jobs = allJobs.filter((job) => !project || visible.has(job.task_id));
  const knownJobs = new Set(jobs.map((j) => j.id));
  const rows: Job[] = [...jobs];
  for (const run of executions || []) {
    if (run.job_id && knownJobs.has(run.job_id)) continue;
    if (
      run.source === "direct" &&
      run.task_id &&
      (!project || visible.has(run.task_id))
    ) {
      rows.push({
        id: run.id,
        task_id: run.task_id,
        status: run.status,
        action: run.action,
        created_at: run.started_at,
        source: "direct",
      });
    }
  }
  rows.sort(
    (a, b) =>
      Number(b.status === "running") - Number(a.status === "running") ||
      String(b.created_at || "").localeCompare(String(a.created_at || "")),
  );
  const notices = (snapshot?.notifications || []).filter(
    (n) => !project || visible.has(n.payload.task_id),
  );
  return (
    <section
      className="panel mg-panel"
      id="task-management"
      aria-label="작업 실행 관리"
    >
      <header className="panel-heading">
        <h2>작업 실행 관리</h2>
      </header>
      {state.error && (
        <p className="mg-error" role="alert">
          {state.error}
        </p>
      )}
      {notice && (
        <p role="status" className="mg-success">
          {notice}
        </p>
      )}
      {error && (
        <p role="alert" className="mg-error">
          {error}
        </p>
      )}
      {!snapshot && !state.error && <p role="status">원장을 불러오는 중…</p>}
      {snapshot?.read_only && <p className="mg-muted">조회 전용 원장</p>}
      {taskId && !selected && !detail.error && (
        <p role="status">선택한 작업을 불러오는 중…</p>
      )}
      {detail.error && (
        <p role="alert" className="mg-error">
          {detail.error}
        </p>
      )}
      {selected && (
        <TaskDetails
          data={selected}
          writable={writable && !detail.error}
          busy={busy}
          mutate={mutate}
          pending={rows.some((j) => j.task_id === taskId && active(j.status))}
        />
      )}
      <section className="mg-subsection" aria-label="실행 대기열">
        <h3>실행 대기열 · {rows.length}</h3>
        {rows.length ? (
          <ul className="mg-jobs">
            {rows.slice(0, 25).map((j) => {
              const held = tasks.some((t) => t.id === j.task_id && t.hold);
              const duplicate = allJobs.some(
                (other) => other.task_id === j.task_id && active(other.status),
              );
              return (
                <li key={j.source + ":" + j.id}>
                  <div className="mg-title">
                    {onSelectTask ? (
                      <button onClick={() => onSelectTask(j.task_id)}>
                        {j.task_id}
                      </button>
                    ) : (
                      <strong>{j.task_id}</strong>
                    )}
                    <span>{label(j.status)}</span>
                  </div>
                  <p>
                    {j.source === "direct" ? "직접 실행" : "큐 실행"} ·{" "}
                    {label(j.action)} · {when(j.created_at)}
                  </p>
                  {held && <p>수동 보류</p>}
                  {j.error && <p className="mg-error">{j.error}</p>}
                  {j.log_path && <JobLog ledger={ledger} id={j.id} />}
                  {writable && j.source !== "direct" && (
                    <div className="mg-flex">
                      {j.status === "queued" && (
                        <button
                          aria-label={j.task_id + " 대기 취소"}
                          disabled={busy}
                          onClick={() =>
                            void mutate(
                              "/api/cancel",
                              { job_id: j.id },
                              "실행 대기를 취소했습니다.",
                            )
                          }
                        >
                          대기 취소
                        </button>
                      )}
                      {j.status === "running" && (
                        <button
                          aria-label={j.task_id + " 실행 중단"}
                          disabled={busy || j.cancel_requested}
                          onClick={() =>
                            void mutate(
                              "/api/stop",
                              { job_id: j.id },
                              "실행 중단을 요청했습니다.",
                            )
                          }
                        >
                          실행 중단
                        </button>
                      )}
                      {["failed", "interrupted", "cancelled"].includes(
                        j.status,
                      ) && (
                        <button
                          aria-label={j.task_id + " 실행 재개"}
                          disabled={busy || held || duplicate}
                          onClick={() =>
                            void mutate(
                              "/api/resume",
                              { job_id: j.id },
                              "실행 재개를 요청했습니다.",
                            )
                          }
                        >
                          실행 재개
                        </button>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="mg-muted">실행 기록이 없습니다.</p>
        )}
      </section>
      {notices.length > 0 && (
        <section className="mg-subsection">
          <h3>완료와 확인 요청</h3>
          <ul>
            {notices.slice(0, 10).map((n) => (
              <li key={n.id}>
                <strong>{n.payload.task_id}</strong> ·{" "}
                {label(n.payload.job?.status || n.payload.status || "unknown")}{" "}
                · {when(n.at)}
                {n.payload.blocker && <p>{n.payload.blocker}</p>}
                {n.payload.release_error && <p>{n.payload.release_error}</p>}
                {["failed", "started"].includes(n.delivery) && (
                  <p className="mg-attention">외부 알림 전달 확인 필요</p>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      {snapshot && (
        <details className="mg-block">
          <summary>등록된 프로젝트 설정 · {snapshot.profiles.length}개</summary>
          <p>
            {snapshot.version && `v${snapshot.version} · `}
            {snapshot.read_only
              ? "조회 전용"
              : snapshot.daemon?.running
                ? "실행 서비스 가동 중"
                : "실행 서비스 상태 미확인"}
          </p>
          {snapshot.profiles.length ? (
            <ul>
              {snapshot.profiles.map((p) => (
                <li key={p.name}>
                  <strong>{p.name}</strong> · {p.config.owner} /{" "}
                  {p.config.reviewer}
                  <p className="mg-path">{p.config.project}</p>
                  <p>
                    기본 검사 {p.config.checks?.length ?? 0}개 · 묶음{" "}
                    {Object.keys(p.config.check_groups || {}).join(", ") ||
                      "없음"}
                  </p>
                </li>
              ))}
            </ul>
          ) : (
            <p>저장한 프로필이 없습니다.</p>
          )}
        </details>
      )}
    </section>
  );
}
