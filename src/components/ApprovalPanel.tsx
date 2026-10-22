import { useEffect, useState } from 'react';
import type { TaskRecord } from '../lib/types.ts';
import { PHASE_LABEL } from '../lib/format.ts';
import { Spinner } from './ui.tsx';
import { IconPause } from './icons.tsx';

export function ApprovalPanel({
  task,
  busy,
  onDecide,
}: {
  task: TaskRecord;
  busy: boolean;
  onDecide: (decision: 'approve' | 'revise' | 'reject', comment: string) => void;
}) {
  const [comment, setComment] = useState('');
  const [confirmReject, setConfirmReject] = useState(false);

  useEffect(() => {
    setConfirmReject(false);
    setComment('');
  }, [task.id]);

  if (task.status !== 'awaiting_approval') return null;

  const isEscalation = task.phase !== 'APPROVAL';
  const title = isEscalation ? '门禁升级：需要人工裁决' : '等待人工审批';
  const hint = isEscalation
    ? `自动门禁在「${PHASE_LABEL[task.phase]}」阶段未能收敛（返工 ${task.revision_round} 轮），请裁定继续、返工或驳回。`
    : `内容已通过全部自动审核${task.scorecard ? `（综合质量分 ${task.scorecard.overall}）` : ''}，批准后将生成最终交付物并归档。`;

  const submit = (decision: 'approve' | 'revise' | 'reject') => {
    onDecide(decision, comment.trim());
    setComment('');
    setConfirmReject(false);
  };

  return (
    <div className="panel" style={{ padding: 0, border: 'none', background: 'none' }}>
      <div className="approval-banner">
        <div style={{ flex: 1, minWidth: 260 }}>
          <div className="title approval-title"><IconPause size={15} /> {title}</div>
          <div className="muted small" style={{ marginTop: 4 }}>
            {hint}
          </div>
        </div>
        {confirmReject ? (
          <div className="approval-confirm" role="group" aria-label="确认驳回">
            <div className="approval-confirm-text">
              <strong>确认驳回这个任务？</strong>
              <span className="muted small">驳回后任务将终止为「已驳回」，不可恢复，也不会生成交付物。</span>
            </div>
            <div className="row" style={{ flexWrap: 'nowrap' }}>
              <button className="btn btn-ghost" disabled={busy} onClick={() => setConfirmReject(false)}>
                取消
              </button>
              <button className="btn btn-bad" disabled={busy} onClick={() => submit('reject')}>
                {busy ? <Spinner /> : null} 确认驳回
              </button>
            </div>
          </div>
        ) : (
          <div className="row" style={{ flexWrap: 'nowrap' }}>
            <input
              placeholder="裁决说明（可选）"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
              style={{ width: 220 }}
            />
            <button className="btn btn-ok" disabled={busy} onClick={() => submit('approve')}>
              {busy ? <Spinner /> : null} 通过
            </button>
            <button className="btn btn-primary" disabled={busy} onClick={() => submit('revise')}>
              退回返工
            </button>
            <button className="btn btn-bad" disabled={busy} onClick={() => setConfirmReject(true)}>
              驳回
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
