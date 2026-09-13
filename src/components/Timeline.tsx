import type { AgentEvent } from '../lib/types.ts';
import { EVENT_ICON, formatTime, levelTone } from '../lib/format.ts';

/** 从事件负载中提取可读的补充信息。 */
function detailOf(event: AgentEvent): string {
  const payload = event.payload ?? {};
  const parts: string[] = [];
  if (typeof payload.gate_result === 'string') parts.push(`门禁：${payload.gate_result}`);
  if (typeof payload.confidence === 'number') parts.push(`置信度 ${(Number(payload.confidence) * 100).toFixed(0)}%`);
  if (typeof payload.verdict === 'string') parts.push(`裁决：${payload.verdict}`);
  if (Array.isArray(payload.requests) && payload.requests.length) {
    parts.push(`返工项 ${payload.requests.length} 条`);
  }
  if (payload.simulated === true) parts.push('离线引擎');
  if (typeof payload.latency_ms === 'number' && payload.latency_ms > 0) {
    parts.push(`${payload.latency_ms}ms`);
  }
  return parts.join(' · ');
}

export function Timeline({ events }: { events: AgentEvent[] }) {
  if (!events.length) {
    return <div className="empty">暂无事件</div>;
  }

  return (
    <div>
      {events
        .slice()
        .reverse()
        .map((event) => (
          <div className="tl-item" key={event.seq}>
            <div className="tl-time">{formatTime(event.ts)}</div>
            <div className={`tl-icon ${levelTone(event.level)}`} style={{ background: 'none', border: 'none' }}>
              {EVENT_ICON[event.type] ?? '·'}
            </div>
            <div className="tl-text">
              <strong>{event.agent_id ? `${event.agent_id} ` : ''}</strong>
              {event.message}
              {detailOf(event) ? <span className="muted small">（{detailOf(event)}）</span> : null}
            </div>
          </div>
        ))}
    </div>
  );
}
