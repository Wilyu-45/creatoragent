import { useMemo, useState } from 'react';
import type { GateRecord, PipelineNode, TaskRecord } from '../lib/types.ts';
import { formatDateTime, gateTone, PHASE_LABEL } from '../lib/format.ts';
import { Chip } from './ui.tsx';

type StatusKey = PipelineNode['status'];
type FilterKey = 'all' | StatusKey;

const STATUS_META: Record<StatusKey, { label: string; tone: string }> = {
  done: { label: '已完成', tone: 'tone-ok' },
  running: { label: '执行中', tone: 'tone-run' },
  revise: { label: '待返工', tone: 'tone-warn' },
  blocked: { label: '已阻塞', tone: 'tone-bad' },
  pending: { label: '待执行', tone: 'tone-idle' },
  skipped: { label: '已跳过', tone: 'tone-idle' },
};

const FILTER_ORDER: StatusKey[] = ['done', 'running', 'revise', 'blocked', 'pending', 'skipped'];

const NODE_CLASS: Record<StatusKey, string> = {
  done: 'is-done',
  running: 'is-running',
  revise: 'is-revise',
  blocked: 'is-blocked',
  pending: 'is-pending',
  skipped: 'is-skipped',
};

const GATE_LABEL: Record<string, string> = {
  pass: '通过',
  revise: '返工',
  reject: '否决',
};

/** pending 但跑过一轮的节点，实际含义是被退回等待重跑。 */
function effectiveStatus(node: PipelineNode): StatusKey {
  return node.status === 'pending' && node.runs > 0 ? 'revise' : node.status;
}

function countByStatus(nodes: PipelineNode[]): Record<StatusKey, number> {
  const stats: Record<StatusKey, number> = { done: 0, running: 0, revise: 0, blocked: 0, pending: 0, skipped: 0 };
  for (const node of nodes) stats[effectiveStatus(node)] += 1;
  return stats;
}

function latestGateByAgent(gates: GateRecord[]): Record<string, GateRecord> {
  const map: Record<string, GateRecord> = {};
  for (const gate of gates) {
    const prev = map[gate.agent_id];
    if (!prev || gate.round >= prev.round) map[gate.agent_id] = gate;
  }
  return map;
}

export function Pipeline({
  task,
  agentNames,
  onOpenArtifacts,
}: {
  task: TaskRecord;
  agentNames: Record<string, { name: string; role: string }>;
  onOpenArtifacts?: (agentId: string) => void;
}) {
  const [filter, setFilter] = useState<FilterKey>('all');

  const stats = useMemo(() => countByStatus(task.pipeline), [task.pipeline]);
  const artifactCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const artifact of task.artifacts) counts[artifact.agent_id] = (counts[artifact.agent_id] ?? 0) + 1;
    return counts;
  }, [task.artifacts]);
  const gates = useMemo(() => latestGateByAgent(task.gates), [task.gates]);
  const gateStats = useMemo(() => {
    const counts: Record<string, number> = { pass: 0, revise: 0, reject: 0 };
    for (const gate of task.gates) counts[gate.verdict] = (counts[gate.verdict] ?? 0) + 1;
    return counts;
  }, [task.gates]);
  const latestRevision = useMemo(
    () => task.revisions.reduce<typeof task.revisions[number] | null>(
      (latest, item) => (!latest || item.round >= latest.round ? item : latest),
      null,
    ),
    [task.revisions],
  );

  const total = task.pipeline.length;
  const completed = stats.done;
  const percent = total ? Math.round((completed / total) * 100) : 0;
  const activeFilter = filter !== 'all' && stats[filter] === 0 ? 'all' : filter;
  const visible = activeFilter === 'all' ? task.pipeline : task.pipeline.filter((node) => effectiveStatus(node) === activeFilter);

  return (
    <div className="pipeline">
      <div className="pl-summary">
        <div className="pl-summary-main">
          <div className="pl-progress-head">
            <span>流水线进度</span>
            <b>
              {completed}/{total} · {percent}%
            </b>
          </div>
          <div
            className="pl-progress-track"
            role="progressbar"
            aria-label="流水线完成度"
            aria-valuemin={0}
            aria-valuemax={total}
            aria-valuenow={completed}
          >
            <span style={{ width: `${percent}%` }} />
          </div>
          <div className="pl-summary-meta">
            <span>
              门禁 {task.gates.length} 次
              {task.gates.length
                ? `：通过 ${gateStats.pass ?? 0} 次 · 返工 ${gateStats.revise ?? 0} 次${
                    gateStats.reject ? ` · 否决 ${gateStats.reject} 次` : ''
                  }`
                : ''}
            </span>
            {task.revision_round > 0 ? <span>编排返工 {task.revision_round} 轮</span> : null}
            {stats.running ? <span>{stats.running} 个节点执行中</span> : null}
            {stats.blocked ? <span className="tone-bad">{stats.blocked} 个节点阻塞</span> : null}
          </div>
        </div>
        <div className="pl-filters" role="group" aria-label="按节点状态筛选">
          <button
            type="button"
            className={`pl-filter${activeFilter === 'all' ? ' active' : ''}`}
            onClick={() => setFilter('all')}
          >
            全部 <b>{total}</b>
          </button>
          {FILTER_ORDER.map((key) => (
            <button
              type="button"
              key={key}
              className={`pl-filter${activeFilter === key ? ' active' : ''}`}
              disabled={stats[key] === 0}
              title={stats[key] === 0 ? `没有${STATUS_META[key].label}的节点` : undefined}
              onClick={() => setFilter(key)}
            >
              {STATUS_META[key].label} <b>{stats[key]}</b>
            </button>
          ))}
        </div>
      </div>

      {latestRevision ? (
        <details className="pl-revision">
          <summary>
            <Chip tone="tone-warn">返工记录 R{latestRevision.round}</Chip>
            <span className="pl-revision-reason">{latestRevision.reason}</span>
            <span className="pl-revision-time">{formatDateTime(latestRevision.created_at)}</span>
          </summary>
          <ul className="pl-revision-list">
            {latestRevision.requests.map((request, index) => (
              <li key={index}>{request}</li>
            ))}
          </ul>
        </details>
      ) : null}

      <div className="pipeline-list">
        {visible.map((node) => {
          const meta = agentNames[node.agent_id];
          const status = effectiveStatus(node);
          const gate = gates[node.agent_id];
          const className = ['pl-node', NODE_CLASS[status]].filter(Boolean).join(' ');
          const fallback =
            status === 'pending'
              ? '等待上游节点完成'
              : status === 'skipped'
                ? '本次编排未启用该节点'
                : status === 'running'
                  ? '执行中…'
                  : '';

          return (
            <div
              className={className}
              key={node.agent_id}
              title={`开始 ${formatDateTime(node.started_at)} · 完成 ${formatDateTime(node.finished_at)}`}
            >
              <div className="pl-badge">{node.agent_id}</div>
              <div className="pl-main">
                <div className="pl-name">
                  {meta?.name ?? node.agent_id}
                  <span className="pl-role"> · {meta?.role ?? PHASE_LABEL[node.phase]}</span>
                </div>
                <div className="pl-summary-line">{node.summary || fallback}</div>
              </div>
              <div className="pl-right">
                <span className="pl-chips">
                  <Chip tone={STATUS_META[status].tone}>{STATUS_META[status].label}</Chip>
                  <Chip tone="tone-idle">{PHASE_LABEL[node.phase]}</Chip>
                  {gate ? (
                    <Chip
                      tone={gateTone(gate.verdict)}
                      title={`最近一次门禁：${GATE_LABEL[gate.verdict] ?? gate.verdict} · ${gate.score} 分 · 第 ${gate.round} 轮`}
                    >
                      门禁 {gate.score} · R{gate.round}
                    </Chip>
                  ) : node.gate_result ? (
                    <Chip tone={gateTone(node.gate_result)}>门禁 {GATE_LABEL[node.gate_result] ?? node.gate_result}</Chip>
                  ) : null}
                </span>
                <span>
                  运行 {node.runs} 次
                  {node.confidence !== null ? ` · 置信 ${(node.confidence * 100).toFixed(0)}%` : ''}
                  {onOpenArtifacts && artifactCounts[node.agent_id] ? (
                    <>
                      {' · '}
                      <button
                        type="button"
                        className="pl-art-link"
                        title={`在产物面板中查看 ${node.agent_id} 的产物`}
                        onClick={() => onOpenArtifacts(node.agent_id)}
                      >
                        产物 {artifactCounts[node.agent_id]} 件 →
                      </button>
                    </>
                  ) : null}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
