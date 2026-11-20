import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { configure, endpoint, get } from "./api";
import "./create-dialog.css";

type Props = {
  ledger: string;
  token: string;
  project?: string;
  onClose: () => void;
  onCreated: (id: string) => void;
};
type Profile = {
  katakuri_goal_available?: boolean;
  name: string;
  digest: string;
  config: {
    project: string;
    owner: string;
    reviewer: string;
    check_groups?: Record<string, string[][]>;
    check_group_descriptions?: Record<string, string>;
    workspace?: { isolate?: boolean };
  };
};
type LedgerState = { ledger: string; read_only: boolean; profiles: Profile[] };
type Receipt = {
  id: string;
  created_at: string;
  profile_digest: string;
  suggested_groups: string[];
};
type GroupsProps = {
  profile: Profile;
  ledger: string;
  typedId: string;
  linked: Receipt | null;
  onTypeId: (id: string) => void;
  onLink: (record: Receipt) => void;
};

function explain(error: unknown) {
  return error instanceof Error ? error.message : "요청을 완료하지 못했습니다.";
}

function RecentPicker({
  profile,
  ledger,
  typedId,
  linked,
  onTypeId,
  onLink,
}: GroupsProps) {
  const [open, setOpen] = useState(false);
  const [records, setRecords] = useState<Receipt[]>([]);
  const [loading, setLoading] = useState(false);
  const [listError, setListError] = useState("");
  const [lookupError, setLookupError] = useState("");
  const [lookupBusy, setLookupBusy] = useState(false);
  const lookup = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!open) return;
    const control = new AbortController();
    setRecords([]);
    setListError("");
    setLoading(true);
    get<{ profile: string; recommendations: Receipt[] }>(
      endpoint("/api/recommendations", ledger, { profile: profile.name }),
      control.signal,
    )
      .then((result) => {
        if (!control.signal.aborted && result.profile === profile.name) {
          setRecords(
            result.recommendations.filter(
              (r) => r.profile_digest === profile.digest,
            ),
          );
        }
      })
      .catch((error) => {
        if (!control.signal.aborted) setListError(explain(error));
      })
      .finally(() => {
        if (!control.signal.aborted) setLoading(false);
      });
    return () => control.abort();
  }, [ledger, profile.digest, profile.name, open]);
  useEffect(() => () => lookup.current?.abort(), []);

  function stopLookup() {
    lookup.current?.abort();
    lookup.current = null;
    setLookupBusy(false);
    setLookupError("");
  }
  async function load() {
    stopLookup();
    const id = typedId.trim();
    if (!id) return;
    const control = new AbortController();
    lookup.current = control;
    setLookupBusy(true);
    try {
      const record = await get<Receipt & { profile: string }>(
        endpoint("/api/recommendations/" + encodeURIComponent(id), ledger),
        control.signal,
      );
      if (control.signal.aborted) return;
      if (
        record.id !== id ||
        record.profile !== profile.name ||
        record.profile_digest !== profile.digest
      ) {
        throw Error("이 추천의 프로필 또는 설정 버전이 다릅니다.");
      }
      onLink(record);
    } catch (error) {
      if (!control.signal.aborted) setLookupError(explain(error));
    } finally {
      if (!control.signal.aborted) setLookupBusy(false);
    }
  }
  return (
    <details
      id="recommendation-picker"
      className="create-choice"
      onToggle={(event) => {
        setOpen(event.currentTarget.open);
        if (!event.currentTarget.open) stopLookup();
      }}
    >
      <summary id="recommendation-summary">
        {linked
          ? `연결한 추천 · ${linked.id.slice(-8)}`
          : "저장한 추천 사용 (선택)"}
      </summary>
      <label htmlFor="recent-recommendation">최근 추천</label>
      <select
        id="recent-recommendation"
        disabled={loading || !records.length}
        value={records.some((r) => r.id === linked?.id) ? linked!.id : ""}
        onChange={(event) => {
          stopLookup();
          const record = records.find((r) => r.id === event.target.value);
          if (record) onLink(record);
          else onTypeId("");
        }}
      >
        <option value="">선택하세요</option>
        {records.map((record) => (
          <option key={record.id} value={record.id}>
            {new Date(record.created_at).toLocaleString("ko-KR")} ·{" "}
            {record.suggested_groups.join(", ") || "기본 검사만"} ·{" "}
            {record.id.slice(-8)}
          </option>
        ))}
      </select>
      <p id="recent-notice" role="status">
        {loading
          ? "최근 추천을 불러오는 중…"
          : listError
            ? "최근 목록을 조회하지 못했습니다. 추천 ID를 직접 입력할 수 있습니다."
            : records.length === 0 && open
              ? "이 프로필에 저장한 최근 추천이 없습니다."
              : ""}
      </p>
      <label htmlFor="create-recommendation">추천 ID 직접 입력</label>
      <input
        id="create-recommendation"
        autoComplete="off"
        value={typedId}
        onChange={(event) => {
          stopLookup();
          onTypeId(event.target.value);
        }}
      />
      {!!typedId.trim() && !linked && (
        <button
          id="load-recommendation"
          type="button"
          disabled={lookupBusy}
          onClick={load}
        >
          {lookupBusy ? "조회 중…" : "추천 묶음 불러오기"}
        </button>
      )}
      {linked && (
        <>
          <p id="recommendation-hint">
            추천 묶음 · {linked.suggested_groups.join(", ") || "기본 검사만"}.
            추천 당시와 같은 완료 조건을 입력하세요.
          </p>
          <button
            type="button"
            onClick={() => {
              stopLookup();
              onTypeId("");
            }}
          >
            추천 연결 해제
          </button>
        </>
      )}
      {lookupError && <p role="alert">{lookupError}</p>}
    </details>
  );
}

type Preview = {
  status: "preview";
  profile: string;
  profile_digest: string;
  input_digest: string;
  available_groups: string[];
  providers: string[];
  input: { change?: { paths: string[] }; [key: string]: unknown };
};
type ModelSelection = {
  provider: string;
  groups?: string[];
  reason?: string;
  error?: string;
};
type Advice = {
  status: "complete" | "review_incomplete";
  profile: string;
  input_digest: string;
  primary: ModelSelection;
  review: ModelSelection | null;
  suggested_groups: string[];
};

function Advisor({
  profile,
  token,
  criteria,
  isolate,
  apply,
}: {
  profile: Profile;
  token: string;
  criteria: string[];
  isolate: boolean;
  apply: (groups: string[]) => void;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [answer, setAnswer] = useState<Advice | null>(null);
  const [busy, setBusy] = useState<"preview" | "run" | null>(null);
  const [message, setMessage] = useState("");
  const request = useRef<AbortController | null>(null);
  const previewButton = useRef<HTMLButtonElement>(null);
  useEffect(() => () => request.current?.abort(), []);

  async function query(mode: "preview" | "run") {
    if (
      request.current ||
      (mode === "run" && (!preview || !preview.providers.length))
    )
      return;
    if (!criteria.length) {
      setMessage("완료 조건을 입력하세요.");
      return;
    }
    const control = new AbortController();
    request.current = control;
    setBusy(mode);
    setAnswer(null);
    setMessage("");
    try {
      const input = { profile: profile.name, criteria };
      if (mode === "preview") {
        setPreview(null);
        const report = await configure<Preview>(
          "/api/check-advisor/preview",
          input,
          token,
          control.signal,
        );
        if (control.signal.aborted) return;
        if (
          report.status !== "preview" ||
          report.profile !== profile.name ||
          !report.input_digest ||
          !report.input ||
          !Array.isArray(report.providers) ||
          !Array.isArray(report.available_groups)
        ) {
          throw Error("추천 미리보기 응답을 확인하지 못했습니다.");
        }
        setPreview(report);
      } else if (preview) {
        const result = await configure<Advice>(
          "/api/check-advisor/run",
          {
            ...input,
            input_digest: preview.input_digest,
            providers: preview.providers,
          },
          token,
          control.signal,
        );
        if (control.signal.aborted) return;
        if (
          !["complete", "review_incomplete"].includes(result.status) ||
          result.profile !== profile.name ||
          result.input_digest !== preview.input_digest ||
          !result.primary ||
          !Array.isArray(result.suggested_groups) ||
          !result.suggested_groups.every((n) =>
            preview.available_groups.includes(n),
          )
        ) {
          throw Error("미리보기와 추천 결과가 일치하지 않습니다.");
        }
        setAnswer(result);
      }
    } catch (error) {
      if (!control.signal.aborted) {
        setPreview(null);
        setMessage(
          explain(error) +
            " 미리보기를 다시 확인하거나 검사 묶음을 직접 선택하세요.",
        );
      }
    } finally {
      if (!control.signal.aborted) {
        request.current = null;
        setBusy(null);
      }
    }
  }

  return (
    <div className="create-advisor">
      <button
        type="button"
        id="advisor-preview"
        ref={previewButton}
        onClick={() => query("preview")}
        disabled={busy !== null}
      >
        보낼 내용 미리보기
      </button>
      {preview && (
        <>
          <pre id="advisor-input" tabIndex={0} aria-label="모델에 보낼 입력">
            {JSON.stringify(preview.input, null, 2)}
          </pre>
          <p id="advisor-providers">
            {preview.providers.length
              ? `${preview.providers.join(" → ")} · 다음 버튼을 누르면 위 입력을 전송하며 계정 사용량이 발생할 수 있습니다.`
              : "이 프로필은 공통 검사만 사용합니다."}
          </p>
          {!!preview.providers.length && !answer && (
            <button
              type="button"
              id="advisor-run"
              disabled={busy !== null}
              onClick={() => query("run")}
            >
              모델에 보내 추천받기
            </button>
          )}
        </>
      )}
      <div id="advisor-result" role="status" aria-live="polite">
        {busy === "preview" && "로컬 Git 변경을 읽고 있습니다."}
        {busy === "run" && "추천 응답을 기다리는 중…"}
        {message}
        {answer && (
          <>
            {[answer.primary, answer.review].map(
              (pass) =>
                pass && (
                  <p key={pass.provider}>
                    {pass.provider} ·{" "}
                    {pass.groups?.join(", ") ||
                      (pass.error ? "검토 미완료" : "기본 검사만")}{" "}
                    — {pass.reason || pass.error}
                  </p>
                ),
            )}
            {answer.status === "review_incomplete" && (
              <p>
                두 번째 검토가 완료되지 않아 첫 모델의 선택만 사용할 수
                있습니다.
              </p>
            )}
            <p>
              제안한 선택 ·{" "}
              {answer.suggested_groups.join(", ") || "기본 검사만"}
            </p>
          </>
        )}
      </div>
      {answer && (
        <button
          id="advisor-apply"
          type="button"
          onClick={() => {
            apply(answer.suggested_groups);
            let applied =
              "선택한 검사 묶음 · " +
              (answer.suggested_groups.join(", ") || "기본 검사만");
            if (isolate && preview?.input.change?.paths?.length)
              applied +=
                ". 미커밋 변경까지 검사하려면 별도 작업 폴더 옵션을 해제하세요.";
            setMessage(applied);
            setAnswer(null);
            setPreview(null);
            previewButton.current?.focus();
          }}
        >
          {answer.status === "review_incomplete"
            ? "첫 모델의 선택 적용"
            : "추천 묶음 선택"}
        </button>
      )}
    </div>
  );
}

function CreateForm({
  ledger,
  token,
  profiles,
  project,
  onCreated,
  onClose,
  onBusy,
}: Props & {
  profiles: Profile[];
  onBusy: (busy: boolean) => void;
}) {
  const [name, setName] = useState(
    () =>
      (profiles.find((p) => p.config.project === project) || profiles[0])
        ?.name || "",
  );
  const selected = profiles.find((p) => p.name === name);
  const [isolate, setIsolate] = useState(
    selected?.config.workspace?.isolate !== false,
  );
  const [groups, setGroups] = useState<string[]>([]);
  const [typedId, setTypedId] = useState("");
  const [linked, setLinked] = useState<Receipt | null>(null);
  const [pickerVersion, resetPicker] = useState(0);
  const [taskId, setTaskId] = useState("");
  const [persistentGoal, setPersistentGoal] = useState(false);
  const [tokenLimit, setTokenLimit] = useState("");
  const [instrument, setInstrument] = useState("");
  const [milestone, setMilestone] = useState("");
  const [goal, setGoal] = useState("");
  const [criteriaText, setCriteriaText] = useState("");
  const [ownerName, setOwnerName] = useState("");
  const [reviewerName, setReviewerName] = useState("");
  const [advisorOpen, setAdvisorOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pending = useRef<AbortController | null>(null);
  const generatedId = useRef("");
  const criteria = criteriaText
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  useEffect(() => () => pending.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || pending.current) return;
    if (!goal.trim() || (!persistentGoal && !criteria.length)) {
      setError("작업 목표와 완료 조건을 입력하세요.");
      return;
    }
    if (typedId.trim() && linked?.id !== typedId.trim()) {
      setError("추천 ID를 조회해 확인하거나 입력을 비워 주세요.");
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    setBusy(true);
    onBusy(true);
    setError("");
    try {
      if (!taskId.trim() && !generatedId.current) {
        const bytes = crypto.getRandomValues(new Uint8Array(12));
        generatedId.current =
          "AW-" +
          Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
            "",
          );
      }
      const id = taskId.trim() || generatedId.current;
      const result = await configure<{ task: { id: string } }>(
        persistentGoal ? "/api/katakuri-goals" : "/api/tasks",
        {
          id,
          profile: selected.name,
          goal: goal.trim(),
          criteria,
          isolate: persistentGoal || isolate,
          ...(persistentGoal && instrument ? { instrument } : {}),
          ...(persistentGoal && milestone ? { milestone } : {}),
          ...(persistentGoal && tokenLimit
            ? { max_reported_tokens: Number(tokenLimit) }
            : {}),
          check_groups: groups,
          agent_names: {
            implement: ownerName.trim(),
            review: reviewerName.trim(),
          },
          ...(linked ? { recommendation_id: linked.id } : {}),
        },
        token,
        controller.signal,
      );
      if (!controller.signal.aborted) onCreated(result.task.id);
    } catch (e) {
      if (!controller.signal.aborted) setError(explain(e));
    } finally {
      if (!controller.signal.aborted) {
        pending.current = null;
        setBusy(false);
        onBusy(false);
      }
    }
  }
  return (
    <form id="create-form" onSubmit={submit}>
      <fieldset className="create-fields" disabled={busy}>
        <label htmlFor="create-profile">프로젝트 설정</label>
        <select
          id="create-profile"
          value={name}
          required
          onChange={(event) => {
            const next = profiles.find((p) => p.name === event.target.value);
            setName(event.target.value);
            setPersistentGoal(false);
            setTokenLimit("");
            setMilestone("");
            setInstrument("");
            setIsolate(next?.config.workspace?.isolate !== false);
            setGroups([]);
            setTypedId("");
            setLinked(null);
            setError("");
            setAdvisorOpen(false);
          }}
        >
          {profiles.map((p) => (
            <option key={p.name} value={p.name}>
              {p.name}
            </option>
          ))}
        </select>
        {selected && (
          <>
            <p className="create-muted">
              {selected.config.project} · 구현 {selected.config.owner} / 리뷰{" "}
              {selected.config.reviewer}
            </p>
            {selected.katakuri_goal_available && (
              <>
                <label className="create-check">
                  <input
                    type="checkbox"
                    checked={persistentGoal}
                    onChange={(e) => setPersistentGoal(e.target.checked)}
                  />
                  Katakuri 목표 연속 실행
                </label>
                {persistentGoal && (
                  <div className="create-name-pair">
                    <label>
                      종목 (선택)
                      <input
                        value={instrument}
                        pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}"
                        maxLength={80}
                        onChange={(e) => setInstrument(e.target.value)}
                      />
                    </label>
                    <label>
                      단계 (선택)
                      <select
                        value={milestone}
                        onChange={(e) => setMilestone(e.target.value)}
                      >
                        <option value="">지정하지 않음</option>
                        {["M1", "M2", "M3", "M4", "M5", "M6"].map((m) => (
                          <option key={m}>{m}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                )}
                {persistentGoal && (
                  <label>
                    보고 토큰 상한 (선택)
                    <input
                      type="number"
                      min={1}
                      max={1000000000000}
                      value={tokenLimit}
                      onChange={(e) => setTokenLimit(e.target.value)}
                    />
                    <small>
                      별도 작업 폴더에서 완료 조건별로 개발하고 검사·독립 리뷰를
                      거칩니다. 외부 근거가 필요하면 대기합니다.
                    </small>
                  </label>
                )}
              </>
            )}
            <label htmlFor="create-goal">작업 목표</label>
            <textarea
              id="create-goal"
              required
              rows={2}
              maxLength={2000}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
            <label htmlFor="create-criteria">
              완료 조건{" "}
              <small>
                {persistentGoal
                  ? "비우면 첫 실행에서 AI가 목표를 검증 가능한 조건으로 나눕니다."
                  : "한 줄에 하나씩 입력"}
              </small>
            </label>
            <textarea
              id="create-criteria"
              required={!persistentGoal}
              rows={3}
              maxLength={3000}
              value={criteriaText}
              onChange={(e) => setCriteriaText(e.target.value)}
            />
            <RecentPicker
              key={JSON.stringify([selected.digest, name, pickerVersion])}
              profile={selected}
              ledger={ledger}
              typedId={typedId}
              linked={linked}
              onTypeId={(value) => {
                setTypedId(value);
                setLinked(null);
                setError("");
              }}
              onLink={(record) => {
                setTypedId(record.id);
                setLinked(record);
                setGroups(record.suggested_groups);
                setError("");
              }}
            />
            {Object.keys(selected.config.check_groups || {}).length > 0 && (
              <fieldset id="create-check-groups" className="create-group-set">
                <legend>작업에 추가할 검사 묶음</legend>
                <div id="check-group-options">
                  {Object.entries(selected.config.check_groups || {}).map(
                    ([n, commands]) => (
                      <label key={n}>
                        <input
                          type="checkbox"
                          value={n}
                          checked={groups.includes(n)}
                          onChange={(e) =>
                            setGroups((current) =>
                              e.target.checked
                                ? [...current, n]
                                : current.filter((x) => x !== n),
                            )
                          }
                        />
                        {n} ({commands.length}개 검사)
                      </label>
                    ),
                  )}
                </div>
              </fieldset>
            )}
            <details
              id="advisor-options"
              className="create-choice"
              open={advisorOpen}
              onToggle={(e) => setAdvisorOpen(e.currentTarget.open)}
            >
              <summary>AI로 검사 묶음 추천받기 (선택)</summary>
              {advisorOpen && (
                <Advisor
                  key={JSON.stringify([selected.digest, name, criteria])}
                  profile={selected}
                  token={token}
                  criteria={criteria}
                  isolate={isolate}
                  apply={(next) => {
                    setGroups(next);
                    setLinked(null);
                    setTypedId("");
                    resetPicker((n) => n + 1);
                    setError("");
                  }}
                />
              )}
            </details>
            <label htmlFor="create-id">
              작업 ID <small>비우면 자동 생성</small>
            </label>
            <input
              id="create-id"
              value={taskId}
              onChange={(e) => setTaskId(e.target.value)}
              maxLength={80}
              pattern="[A-Za-z0-9][A-Za-z0-9_.\-]{0,79}"
              autoComplete="off"
            />
            <div className="create-name-pair">
              <label>
                구현 담당 이름 <small>선택</small>
                <input
                  id="create-agent-name"
                  value={ownerName}
                  maxLength={120}
                  onChange={(e) => setOwnerName(e.target.value)}
                />
              </label>
              <label>
                리뷰 담당 이름 <small>선택</small>
                <input
                  id="create-reviewer-name"
                  value={reviewerName}
                  maxLength={120}
                  onChange={(e) => setReviewerName(e.target.value)}
                />
              </label>
            </div>
            <label className="create-check">
              <input
                id="create-isolate"
                type="checkbox"
                checked={persistentGoal || isolate}
                disabled={persistentGoal}
                onChange={(e) => setIsolate(e.target.checked)}
              />
              별도 작업 폴더와 브랜치에서 진행
            </label>
          </>
        )}
      </fieldset>
      {error && (
        <p id="create-error" role="alert">
          {error}
        </p>
      )}
      <div className="create-submit-row">
        <button
          type="button"
          id="create-close"
          onClick={onClose}
          disabled={busy}
        >
          취소
        </button>
        <button
          type="submit"
          id="create-submit"
          className="connect-primary"
          disabled={!selected || busy}
        >
          {busy ? "등록 중…" : "작업 등록"}
        </button>
      </div>
    </form>
  );
}

function DialogBody(props: Props) {
  const { ledger, token, onClose } = props;
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const initialProject = useRef(props.project);
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const modal = dialog.current!;
    const opener =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    modal.showModal();
    heading.current?.focus();
    return () => {
      modal.close();
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  useEffect(() => {
    const control = new AbortController();
    setProfiles(null);
    setError("");
    get<LedgerState>(
      endpoint("/api/state", ledger, { compact: "1" }),
      control.signal,
    )
      .then((state) => {
        if (control.signal.aborted) return;
        if (
          !token ||
          !ledger ||
          state.read_only !== false ||
          state.ledger !== ledger
        ) {
          throw Error(
            "이 원장은 조회 전용입니다. 기본 관리 원장을 선택하세요.",
          );
        }
        setProfiles(state.profiles);
      })
      .catch((e) => {
        if (!control.signal.aborted) setError(explain(e));
      });
    return () => control.abort();
  }, [ledger, token, retry]);

  return (
    <dialog
      ref={dialog}
      id="create-dialog"
      className="task-create-dialog"
      aria-labelledby="create-title"
      onCancel={(e) => {
        e.preventDefault();
        if (!busy) onClose();
      }}
    >
      <header className="create-heading">
        <h2 id="create-title" tabIndex={-1} ref={heading}>
          새 작업 등록
        </h2>
      </header>
      {error ? (
        <>
          <p role="alert">{error}</p>
          <button type="button" onClick={() => setRetry((n) => n + 1)}>
            다시 불러오기
          </button>
        </>
      ) : profiles === null ? (
        <p role="status">프로젝트 설정을 불러오는 중…</p>
      ) : profiles.length ? (
        <CreateForm
          {...props}
          profiles={profiles}
          project={initialProject.current}
          onBusy={setBusy}
        />
      ) : (
        <p role="status">
          기본 관리 원장에 프로젝트 설정을 등록하세요. CLI의 profile save 명령을
          사용할 수 있습니다.
        </p>
      )}
      {(error || !profiles?.length) && (
        <button id="create-close" type="button" onClick={onClose}>
          닫기
        </button>
      )}
    </dialog>
  );
}

export function CreateTaskDialog(props: Props) {
  return (
    <DialogBody key={JSON.stringify([props.ledger, props.token])} {...props} />
  );
}

export default CreateTaskDialog;
