import { useEffect, useRef, useState } from "react";
import { configure } from "./api";
import type { Capabilities, Ledger } from "./types";
import { pathName } from "./model";

export function diagnosis(error: string) {
  if (/401|403|인증 환경변수/.test(error))
    return {
      title: "인증을 확인해 주세요",
      action:
        "연결 설정의 인증 환경변수 이름과 관제실 서버에 전달된 인증 정보를 확인하세요.",
    };
  if (/토큰/.test(error))
    return {
      title: "연결 설정 권한을 다시 확인해 주세요",
      action: "관제실을 새로고침한 뒤 다시 연결하세요.",
    };
  if (/HTTP 5\d\d/.test(error))
    return {
      title: "서버 연결을 확인해 주세요",
      action:
        "OpenCode 서버가 오류를 반환했습니다. 서버 상태를 확인한 후 다시 조회하세요. 작업은 재실행하지 않습니다.",
    };
  if (/버전|지원되지|지원하지/.test(error))
    return {
      title: "지원 범위를 확인해 주세요",
      action:
        "OpenCode 1.x 서버의 세션 API가 필요합니다. 다른 도구는 아래 지원 범위를 확인하세요.",
    };
  if (/프로젝트|디렉터리|경로/.test(error))
    return {
      title: "프로젝트 경로를 확인해 주세요",
      action:
        "관제실 서버에서 접근할 수 있는 실제 프로젝트 폴더의 절대 경로를 입력하세요.",
    };
  if (/응답|Failed to fetch|NetworkError|fetch|연결/.test(error))
    return {
      title: "서버 연결을 확인해 주세요",
      action:
        "사용 중인 서버가 실행 중인지, 연결 주소가 맞는지 확인한 후 다시 조회하세요. 다시 조회해도 작업은 재실행하지 않습니다.",
    };
  return {
    title: "기록을 읽지 못했습니다",
    action:
      "아래 오류와 연결 정보를 확인한 후 다시 조회하세요. 기존 작업과 기록은 유지됩니다.",
  };
}

export function ConnectionDialog({
  capabilities,
  initialProject,
  onClose,
  onConnected,
}: {
  capabilities: Capabilities;
  initialProject: string;
  onClose: () => void;
  onConnected: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const opener = useRef(document.activeElement);
  const request = useRef<AbortController | null>(null);
  const [project, setProject] = useState(initialProject);
  const [tool, setTool] = useState("opencode");
  const [url, setUrl] = useState("http://127.0.0.1:4096");
  const [name, setName] = useState("");
  const [passwordEnv, setPasswordEnv] = useState("");
  const [username, setUsername] = useState("opencode");
  const [candidates, setCandidates] = useState<Ledger[] | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    const el = dialog.current!;
    el.showModal();
    return () => {
      request.current?.abort();
      el.close();
      if (opener.current instanceof HTMLElement && opener.current.isConnected)
        opener.current.focus();
    };
  }, []);
  const reset = () => {
    setCandidates(null);
    setChosen([]);
    setError("");
  };
  const submit = async (discover: boolean) => {
    if (busy) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    setError("");
    const timeout = setTimeout(() => controller.abort(), 30000);
    try {
      if (discover) {
        const result = await configure<{ ledgers: Ledger[] }>(
          "/api/connections/discover",
          { project },
          capabilities.csrf_token,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setCandidates(result.ledgers);
          setChosen([]);
        }
      } else {
        const data =
          tool === "opencode"
            ? {
                kind: "opencode",
                project,
                url,
                name: name.trim() || `${pathName(project)} · OpenCode`,
                ...(passwordEnv.trim()
                  ? { password_env: passwordEnv.trim(), username }
                  : {}),
              }
            : { kind: "workflow", project, ledger_ids: chosen };
        const result = await configure<{ source_id: string }>(
          "/api/connections/connect",
          data,
          capabilities.csrf_token,
          controller.signal,
        );
        if (!controller.signal.aborted) onConnected(result.source_id);
      }
    } catch (e) {
      // Closing cancels the UI request; the server may already have saved the connection.
      if (dialog.current?.open)
        setError(
          controller.signal.aborted
            ? "연결 확인 응답 시간이 초과되었습니다. 연결 목록을 새로고침해 저장 여부를 확인하세요."
            : e instanceof Error
              ? e.message
              : "연결 설정 실패",
        );
    } finally {
      clearTimeout(timeout);
      setBusy(false);
    }
  };
  const issue = diagnosis(error);
  return (
    <dialog
      ref={dialog}
      className="connection-dialog"
      aria-labelledby="connection-title"
      onCancel={onClose}
      onClose={onClose}
    >
      <div className="connection-heading">
        <div>
          <span className="eyebrow">CONNECT YOUR WORK</span>
          <h2 id="connection-title">프로젝트 연결</h2>
        </div>
        <button type="button" onClick={onClose} aria-label="연결 설정 닫기">
          닫기
        </button>
      </div>
      <p>
        프로젝트와 도구를 한 번 연결하면, 지원되는 새 기록을 자동으로
        관측합니다.
      </p>
      <div className="support-grid" aria-label="도구별 관측 지원 범위">
        {capabilities.tools.map((item) => (
          <div key={item.id}>
            <strong>{item.label}</strong>
            <span>
              {item.support === "native_sessions"
                ? "새 세션 자동 관측"
                : "workflow 실행 기록"}
            </span>
            <p>{item.description}</p>
          </div>
        ))}
      </div>
      {!capabilities.can_configure_connections ? (
        <div className="notice" role="status">
          <h3>이 관제실은 읽기 전용입니다</h3>
          <p>
            현재 서버에서는 연결 설정과 작업 실행을 변경할 수 없습니다. 연결
            설정이 가능한 관측 전용 관제실(watch)을 열어 프로젝트를 연결하세요.
            기존 운영 서버를 종료할 필요는 없습니다.
          </p>
        </div>
      ) : (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit(tool !== "opencode" && candidates === null);
          }}
        >
          <fieldset disabled={busy} className="connection-fields">
            <label htmlFor="connection-project">프로젝트 폴더 경로</label>
            <input
              id="connection-project"
              autoFocus
              required
              value={project}
              placeholder="/home/me/my-project"
              onChange={(e) => {
                setProject(e.target.value);
                reset();
              }}
              aria-describedby="project-path-help"
            />
            <p id="project-path-help" className="muted">
              관제실 서버에서 접근할 수 있는 폴더의 절대 경로를 입력하세요.
              WSL에서 실행 중이면 WSL 경로를 사용합니다.
            </p>
            <label htmlFor="connection-tool">사용 도구</label>
            <select
              id="connection-tool"
              value={tool}
              onChange={(e) => {
                setTool(e.target.value);
                reset();
              }}
            >
              <option value="opencode">OpenCode · 새 세션 자동 관측</option>
              <option value="codex">Codex · workflow 기록 연결</option>
              <option value="claude">Claude · workflow 기록 연결</option>
            </select>
            {tool === "opencode" ? (
              <>
                <p className="connection-hint">
                  이미 사용 중인 OpenCode 서버에 연결합니다. 연결 후 같은
                  프로젝트의 새 세션이 자동 표시됩니다. 서버가 아직 없다면
                  OpenCode에서 서버를 먼저 준비하세요.
                </p>
                <label htmlFor="connection-name">
                  연결 이름 <span className="muted">선택 사항</span>
                </label>
                <input
                  id="connection-name"
                  maxLength={120}
                  value={name}
                  placeholder="예: 나의 프로젝트 · OpenCode"
                  onChange={(e) => setName(e.target.value)}
                />
                <details className="connection-advanced">
                  <summary>서버 주소·인증 설정</summary>
                  <label htmlFor="connection-url">OpenCode 서버 주소</label>
                  <input
                    id="connection-url"
                    type="url"
                    required
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                  <p className="muted">
                    기본 주소는 http://127.0.0.1:4096입니다. 다른 주소로 실행
                    중이면 변경하세요.
                  </p>
                  <label htmlFor="connection-password-env">
                    인증 환경변수 이름{" "}
                    <span className="muted">인증 사용 시</span>
                  </label>
                  <input
                    id="connection-password-env"
                    value={passwordEnv}
                    placeholder="MY_OPENCODE_PASSWORD"
                    autoComplete="off"
                    onChange={(e) => setPasswordEnv(e.target.value)}
                  />
                  <p className="muted">
                    비밀번호 자체를 입력하지 마세요. 관제실 서버에 전달된
                    환경변수 이름을 사용합니다.
                  </p>
                  <label htmlFor="connection-username">인증 사용자 이름</label>
                  <input
                    id="connection-username"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </details>
              </>
            ) : (
              <>
                <p className="connection-hint">
                  {tool === "codex" ? "Codex" : "Claude"}의 일반 대화는 자동
                  수집하지 않습니다. agent-workflow로 실행해 기록된 작업을
                  연결합니다. 프로젝트에 해당하는 알려진 기록만 찾으며, 발견 후
                  직접 선택합니다.
                </p>
                {candidates !== null && (
                  <div className="candidate-list" aria-label="발견한 작업 기록">
                    <h3>
                      {candidates.length
                        ? "연결할 기록을 선택하세요"
                        : "이 프로젝트의 기록을 찾지 못했습니다"}
                    </h3>
                    {!candidates.length && (
                      <p>
                        프로젝트 경로가 실제 작업 폴더와 같은지 확인하세요. 아직
                        workflow로 기록한 작업이 없거나, 기록이 알려진 위치 밖에
                        있을 수 있습니다. 일반 대화를 연결해도 과거 대화가
                        가져와지지는 않습니다.
                      </p>
                    )}
                    {candidates.map((item) => (
                      <label key={item.id} className="candidate-option">
                        <input
                          type="checkbox"
                          checked={chosen.includes(item.id)}
                          onChange={(e) =>
                            setChosen((v) =>
                              e.target.checked
                                ? [...v, item.id]
                                : v.filter((id) => id !== item.id),
                            )
                          }
                        />
                        <span>
                          <strong>{item.label || pathName(item.home)}</strong>
                          <small>
                            {item.matching_task_count ?? item.task_count}개
                            프로젝트 작업
                          </small>
                          <code>{item.home}</code>
                        </span>
                      </label>
                    ))}
                    <button type="button" onClick={() => void submit(true)}>
                      기록 다시 찾기
                    </button>
                  </div>
                )}
              </>
            )}
          </fieldset>
          {error && (
            <div role="alert" className="notice error">
              <h3>{issue.title}</h3>
              <p>{issue.action}</p>
              <details open>
                <summary>확인한 오류</summary>
                <p>{error}</p>
              </details>
            </div>
          )}
          <div className="connection-footer">
            <p>
              연결은 관측 설정만 저장합니다. 작업을 만들거나 실행하지 않습니다.
            </p>
            <button
              type="submit"
              className="connect-primary"
              disabled={
                busy ||
                (tool !== "opencode" && candidates !== null && !chosen.length)
              }
            >
              {busy
                ? "연결 확인 중…"
                : tool !== "opencode" && candidates === null
                  ? "프로젝트 기록 찾기"
                  : "연결하고 관측하기"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}
