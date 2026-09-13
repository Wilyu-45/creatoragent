import type { TaskRecord } from '../lib/types.ts';
import { gateTone, PHASE_LABEL } from '../lib/format.ts';
import { Chip } from './ui.tsx';

const NODE_CLASS: Record<string, string> = {
  running: 'is-running',
  done: 'is-done',
  revise: 'is-revise',
  blocked: 'is-blocked',
};

const GATE_LABEL: Record<string, string> = {
  pass: '通过',
  revise: '返工',
  reject: '否决',
};

export function Pipeline({
  task,
  agentNames,
}: {
  task: TaskRecord;
  agentNames: Record<string, { name: string; role: string }>;
}) {
  return (
    <div className="pipeline">
      {task.pipeline.map((node) => {
        const meta = agentNames[node.agent_id];
        const className = [
          'pl-node',
          NODE_CLASS[node.status] ?? '',
          node.status === 'pending' && node.runs > 0 ? 'is-revise' : '',
        ]
          .filter(Boolean)
          .join(' ');

        return (
          <div className={className} key={node.agent_id}>
            <div className="pl-badge">{node.agent_id}</div>
            <div>
              <div className="pl-name">
                {meta?.name ?? node.agent_id}
                <span className="pl-role"> · {meta?.role ?? PHASE_LABEL[node.phase]}</span>
              </div>
              {node.summary ? <div className="pl-summary">{node.summary}</div> : null}
            </div>
            <div className="pl-right">
              <Chip tone={node.status === 'running' ? 'tone-run' : 'tone-idle'}>{PHASE_LABEL[node.phase]}</Chip>
              {node.gate_result ? (
                <Chip tone={gateTone(node.gate_result)}>{GATE_LABEL[node.gate_result] ?? node.gate_result}</Chip>
              ) : null}
              <span>
                运行 {node.runs} 次
                {node.confidence !== null ? ` · 置信 ${(node.confidence * 100).toFixed(0)}%` : ''}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}
