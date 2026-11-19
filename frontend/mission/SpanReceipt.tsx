import type { Span } from "./types";

export function SpanReceipt({
  span,
  replay,
}: {
  span?: Span;
  replay: boolean;
}) {
  if (!span) return null;
  // The native replay projection has redacted duration and result data. Final process
  // and provider usage receipts belong to the live/history record, outside replay.
  if (replay) return null;
  const seconds =
    span.duration_ms != null ? span.duration_ms / 1000 : span.process?.seconds;
  return (
    <details className="evidence">
      <summary>실행 근거와 사용량</summary>
      <dl className="facts">
        <dt>실행 ID</dt>
        <dd>
          <code>{span.run_id}</code>
        </dd>
        <dt>단계 ID</dt>
        <dd>
          <code>{span.id}</code>
        </dd>
        <dt>소요 시간</dt>
        <dd>{seconds == null ? "미기록" : `${seconds.toFixed(2)}초`}</dd>
        <dt>종료 코드</dt>
        <dd>{span.process?.exit_code ?? "미기록"}</dd>
        <dt>시간 초과</dt>
        <dd>
          {span.process?.timed_out == null
            ? "미기록"
            : span.process.timed_out
              ? "예"
              : "아니요"}
        </dd>
        <dt>보고된 토큰</dt>
        <dd>{span.usage?.tokens?.toLocaleString("ko-KR") ?? "미보고"}</dd>
        <dt>보고된 비용</dt>
        <dd>
          {span.usage?.reported_cost_usd == null
            ? "미보고"
            : `$${span.usage.reported_cost_usd.toFixed(6)}`}
        </dd>
      </dl>
    </details>
  );
}
