import { useEffect, useMemo, useState } from 'react';
import type { TaskSummary } from '../lib/api.ts';
import { formatDateTime, formatDeadline, parseDeadline, PRIORITY_LABEL, STATUS_LABEL, statusTone } from '../lib/format.ts';
import { Chip, Empty } from './ui.tsx';
import { IconClose, IconSearch } from './icons.tsx';

type StatusFilter = 'all' | TaskSummary['status'];
type SortKey = 'updated' | 'created' | 'quality';

const STATUS_ORDER: TaskSummary['status'][] = [
  'running',
  'awaiting_approval',
  'completed',
  'rejected',
  'failed',
  'cancelled',
];

const SORT_LABEL: Record<SortKey, string> = {
  updated: '最近更新',
  created: '最近创建',
  quality: '质量分高到低',
};

function haystack(task: TaskSummary): string {
  const brief = task.brief;
  return [
    brief.brand,
    brief.product,
    brief.channel,
    brief.industry,
    brief.audience,
    brief.objective,
    brief.tone,
    ...(brief.keywords ?? []),
  ]
    .join(' ')
    .toLowerCase();
}

function sortValue(task: TaskSummary, key: SortKey): number {
  if (key === 'quality') return task.scorecard?.overall ?? -1;
  const stamp = key === 'created' ? task.created_at : task.updated_at;
  const value = Date.parse(stamp);
  return Number.isNaN(value) ? 0 : value;
}

export function TaskList({
  tasks,
  activeId,
  resetKey,
  onSelect,
}: {
  tasks: TaskSummary[];
  activeId: string | null;
  /** 新建 / 删除后由上层自增，清掉检索与状态筛选，避免新任务被筛选隐藏。 */
  resetKey?: number;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StatusFilter>('all');
  const [sort, setSort] = useState<SortKey>('updated');

  useEffect(() => {
    if (resetKey === undefined) return;
    setQuery('');
    setStatus('all');
  }, [resetKey]);

  const counts = useMemo(() => {
    const result: Record<StatusFilter, number> = {
      all: tasks.length,
      running: 0,
      awaiting_approval: 0,
      completed: 0,
      rejected: 0,
      failed: 0,
      cancelled: 0,
    };
    for (const task of tasks) result[task.status] += 1;
    return result;
  }, [tasks]);

  const visible = useMemo(() => {
    const tokens = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    const rows = tasks.filter((task) => {
      if (status !== 'all' && task.status !== status) return false;
      if (!tokens.length) return true;
      const text = haystack(task);
      return tokens.every((token) => text.includes(token));
    });
    return [...rows].sort((a, b) => sortValue(b, sort) - sortValue(a, sort));
  }, [tasks, query, status, sort]);

  const filtered = query.trim().length > 0 || status !== 'all';
  const clearFilters = () => {
    setQuery('');
    setStatus('all');
  };

  return (
    <div className="task-browser">
      <div className="task-tools">
        <div className="task-search">
          <IconSearch size={14} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜品牌 / 产品 / 关键词"
            aria-label="搜索任务"
          />
          {query ? (
            <button type="button" className="task-search-clear" aria-label="清空搜索" onClick={() => setQuery('')}>
              <IconClose size={12} />
            </button>
          ) : null}
        </div>
        <div className="task-filter-row">
          <select
            value={status}
            aria-label="按状态筛选"
            onChange={(event) => setStatus(event.target.value as StatusFilter)}
          >
            <option value="all">全部状态 · {counts.all}</option>
            {STATUS_ORDER.map((key) => (
              <option key={key} value={key}>
                {STATUS_LABEL[key]} · {counts[key]}
              </option>
            ))}
          </select>
          <select value={sort} aria-label="任务排序" onChange={(event) => setSort(event.target.value as SortKey)}>
            {(Object.keys(SORT_LABEL) as SortKey[]).map((key) => (
              <option key={key} value={key}>
                {SORT_LABEL[key]}
              </option>
            ))}
          </select>
        </div>
        <div className="task-list-summary">
          <span>
            {filtered ? `筛出 ${visible.length} / ${tasks.length} 个任务` : `共 ${tasks.length} 个任务`}
          </span>
          {filtered ? (
            <button type="button" onClick={clearFilters}>
              清除筛选
            </button>
          ) : null}
        </div>
      </div>

      <div className="task-list">
        {visible.length ? (
          visible.map((task) => {
            const deadline = parseDeadline(task.brief.deadline);
            const overdue = deadline !== null && deadline < Date.now() && task.status !== 'completed';
            const urgent = task.brief.priority === 'high' || task.brief.priority === 'urgent';
            return (
              <div
                key={task.id}
                className={`task-item is-${task.status}${task.id === activeId ? ' active' : ''}`}
                onClick={() => onSelect(task.id)}
                role="button"
                tabIndex={0}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') onSelect(task.id);
                }}
              >
                <div className="task-item-title">
                  <span>
                    {task.brief.brand}
                    <span className="muted"> / {task.brief.product}</span>
                  </span>
                  <span className="row" style={{ gap: 4 }}>
                    {urgent ? (
                      <span className="task-priority-tag" title={`Brief 优先级：${PRIORITY_LABEL[task.brief.priority]}`}>
                        {PRIORITY_LABEL[task.brief.priority]}
                      </span>
                    ) : null}
                    <Chip tone={statusTone(task.status)}>{STATUS_LABEL[task.status]}</Chip>
                  </span>
                </div>
                <div className="task-item-meta">
                  <span>{task.brief.channel}</span>
                  <span>{task.phase_label}</span>
                  <span>返工 {task.revision_round} 轮</span>
                  <span>{task.artifact_count} 件产物</span>
                </div>
                <div className="task-item-meta">
                  <span>{formatDateTime(task.updated_at)}</span>
                  <span>第 {task.turn_used} 轮</span>
                  {task.scorecard ? <span>质量 {task.scorecard.overall}</span> : null}
                  {deadline !== null ? (
                    <span className={overdue ? 'task-deadline overdue' : 'task-deadline'}>
                      {overdue ? '已逾期' : '截止'} {formatDeadline(task.brief.deadline)}
                    </span>
                  ) : null}
                </div>
              </div>
            );
          })
        ) : tasks.length ? (
          <Empty>
            <div style={{ marginBottom: 8 }}>没有符合筛选条件的任务</div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={clearFilters}>
              清除筛选
            </button>
          </Empty>
        ) : (
          <Empty>还没有任务，点击右上角「新建任务」开始</Empty>
        )}
      </div>
    </div>
  );
}
