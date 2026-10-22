import { useMemo, useState } from 'react';
import type { AgentEvent } from '../lib/types.ts';
import { EVENT_ICON, formatTime, levelTone } from '../lib/format.ts';
import { IconClose, IconSearch } from './icons.tsx';

/** 进度、黑板写入与调试日志数量最多，归档在「全部」里，默认视图看关键事件。 */
const NOISY_TYPES = new Set<AgentEvent['type']>(['agent.progress', 'blackboard.write', 'log']);

function isKey(event: AgentEvent): boolean {
  return event.level === 'warn' || event.level === 'error' || !NOISY_TYPES.has(event.type);
}

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
  const [scope, setScope] = useState<'all' | 'key'>('all');
  const [query, setQuery] = useState('');

  const keyCount = useMemo(() => events.filter(isKey).length, [events]);

  const visible = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    return events.filter((event) => {
      if (scope === 'key' && !isKey(event)) return false;
      if (!tokens.length) return true;
      const text = `${event.agent_id ?? ''} ${event.type} ${event.message} ${detailOf(event)}`.toLowerCase();
      return tokens.every((token) => text.includes(token));
    });
  }, [events, scope, query]);

  const filtered = scope === 'key' || query.trim().length > 0;
  const clearFilters = () => {
    setScope('all');
    setQuery('');
  };

  return (
    <div>
      <div className="tl-tools">
        <div className="tl-scope" role="group" aria-label="事件范围">
          <button type="button" className={scope === 'all' ? 'active' : ''} onClick={() => setScope('all')}>
            全部 {events.length}
          </button>
          <button type="button" className={scope === 'key' ? 'active' : ''} onClick={() => setScope('key')}>
            关键 {keyCount}
          </button>
        </div>
        <div className="task-search tl-search">
          <IconSearch size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜事件 / 角色 / 裁决"
            aria-label="搜索事件"
          />
          {query ? (
            <button type="button" className="task-search-clear" aria-label="清空搜索" onClick={() => setQuery('')}>
              <IconClose size={12} />
            </button>
          ) : null}
        </div>
        <div className="tl-summary">
          <span>{filtered ? `显示 ${visible.length} / ${events.length} 条` : `${events.length} 条事件`}</span>
          {filtered ? (
            <button type="button" onClick={clearFilters}>
              清除筛选
            </button>
          ) : null}
        </div>
      </div>
      <div className="timeline">
        {visible.length ? (
          visible
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
            ))
        ) : (
          <div className="empty">{events.length ? '没有匹配的事件，试试清除筛选' : '暂无事件'}</div>
        )}
      </div>
    </div>
  );
}
