import type { TaskSummary } from '../lib/api.ts';
import { formatDateTime, STATUS_LABEL, statusTone } from '../lib/format.ts';
import { Chip, Empty } from './ui.tsx';

export function TaskList({
  tasks,
  activeId,
  onSelect,
}: {
  tasks: TaskSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
}) {
  if (!tasks.length) {
    return <Empty>还没有任务，点击右上角「新建创作任务」开始</Empty>;
  }

  return (
    <>
      {tasks.map((task) => (
        <div
          key={task.id}
          className={`task-item${task.id === activeId ? ' active' : ''}`}
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
            <Chip tone={statusTone(task.status)}>{STATUS_LABEL[task.status]}</Chip>
          </div>
          <div className="task-item-meta">
            <span>{task.brief.channel}</span>
            <span>{task.phase_label}</span>
            <span>返工 {task.revision_round} 轮</span>
            <span>{task.artifact_count} 产物</span>
          </div>
          <div className="task-item-meta">
            <span>{formatDateTime(task.updated_at)}</span>
            <span>Turn {task.turn_used}</span>
            {task.scorecard ? <span>质量 {task.scorecard.overall}</span> : null}
          </div>
        </div>
      ))}
    </>
  );
}
