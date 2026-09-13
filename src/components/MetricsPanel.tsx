import type { MetricsView } from '../lib/api.ts';
import { formatCost, formatNumber, scoreTone } from '../lib/format.ts';
import { Chip, Table } from './ui.tsx';

const SYSTEM_LABELS: [string, string, string][] = [
  ['total_tasks', '总任务', ''],
  ['completed', '已完成', ''],
  ['running', '执行中', ''],
  ['awaiting_approval', '待审批', ''],
  ['rejected', '已驳回', ''],
  ['failed', '失败', ''],
  ['first_pass_rate', '一次通过率', '%'],
  ['avg_revision_rounds', '平均返工轮次', ''],
  ['avg_overall_score', '平均质量分', ''],
  ['gate_pass_rate', '门禁通过率', '%'],
  ['fact_accuracy', '事实准确率', '%'],
  ['brand_consistency', '品牌一致性', ''],
  ['turn_budget_hit_rate', 'Turn Budget 触顶率', '%'],
  ['p99_agent_latency_ms', 'P99 智能体延迟', 'ms'],
  ['simulated_ratio', '离线引擎占比', '%'],
];

export function MetricsPanel({ metrics }: { metrics: MetricsView | null }) {
  if (!metrics) {
    return <div className="empty">指标加载中…</div>;
  }

  const { system, providers, agents } = metrics;
  const cost = system.cost;
  const cache = system.cache;
  const leases = system.leases;

  return (
    <div>
      <div className="panel">
        <div className="panel-title">
          系统指标 <span className="count">· 全量任务聚合</span>
        </div>
        <div className="grid-3">
          {SYSTEM_LABELS.map(([key, label, unit]) => (
            <div className="card" key={key}>
              <div className="muted small">{label}</div>
              <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>
                {formatNumber(system[key] ?? 0, key === 'avg_revision_rounds' || key === 'avg_overall_score' ? 1 : 0)}
                {unit ? <span className="muted small"> {unit}</span> : null}
              </div>
            </div>
          ))}
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <Chip tone="tone-info">
            输入 {formatNumber(system.tokens.prompt)} tokens
          </Chip>
          <Chip tone="tone-info">
            输出 {formatNumber(system.tokens.completion)} tokens
          </Chip>
          <Chip tone="tone-ok">
            累计成本 {formatCost(system.tokens.cost_usd)}
          </Chip>
          {providers.map((provider) => (
            <Chip key={provider.name} tone={provider.simulated ? 'tone-warn' : 'tone-ok'} mono>
              {provider.name} · {provider.model}
              {provider.simulated ? '（离线）' : ''}
            </Chip>
          ))}
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">
          成本 · 缓存 · 并发 <span className="count">· 预算熔断与黑板租约</span>
        </div>
        <div className="grid-3">
          <div className="card">
            <div className="muted small">累计成本</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatCost(cost.total_cost_usd)}</div>
          </div>
          <div className="card">
            <div className="muted small">平均单篇成本</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatCost(cost.avg_cost_per_task_usd)}</div>
          </div>
          <div className="card">
            <div className="muted small">单任务预算</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatCost(cost.budget_usd)}</div>
          </div>
          <div className="card">
            <div className="muted small">缓存命中</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatNumber(cache.hits)}</div>
            <div className="muted small">未命中 {formatNumber(cache.misses)}</div>
          </div>
          <div className="card">
            <div className="muted small">租约冲突</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatNumber(leases.conflicts)}</div>
            <div className="muted small">活跃租约 {formatNumber(leases.active)}</div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 14 }}>
          <Chip tone={cost.cut_off_tasks > 0 ? 'tone-warn' : 'tone-ok'}>熔断任务 {cost.cut_off_tasks}</Chip>
          <Chip tone="tone-info">token 预算 {formatNumber(cost.token_budget)}</Chip>
          <Chip tone="tone-info">命中缓存调用 {cost.cached_calls}</Chip>
          <Chip tone={cache.hitRate > 0 ? 'tone-ok' : 'tone-idle'}>
            缓存命中率 {formatNumber(cache.hitRate * 100, 1)}%
          </Chip>
          <Chip tone="tone-idle">
            缓存条目 {formatNumber(cache.entries)} / {formatNumber(cache.capacity)}
          </Chip>
          <Chip tone="tone-idle">存活账本 {formatNumber(cost.activeLedgers)}</Chip>
        </div>
      </div>

      <div className="panel">
        <div className="panel-title">
          智能体运行指标 <span className="count">· 按运行次数排序</span>
        </div>
        <Table head={['智能体', '运行次数', '平均置信度', '平均延迟', '门禁未通过', '触发人工']}>
          {agents
            .slice()
            .sort((a, b) => b.runs - a.runs)
            .map((agent) => (
              <tr key={agent.id}>
                <td>
                  <strong>{agent.id}</strong> <span className="muted">{agent.name}</span>
                </td>
                <td>{agent.runs}</td>
                <td className={scoreTone(agent.avg_confidence)} style={{ background: 'none', border: 'none' }}>
                  {agent.runs ? `${formatNumber(agent.avg_confidence, 1)}%` : '—'}
                </td>
                <td>{agent.runs ? `${formatNumber(agent.avg_latency_ms)} ms` : '—'}</td>
                <td>{agent.gate_failures}</td>
                <td>{agent.veto_used}</td>
              </tr>
            ))}
        </Table>
      </div>
    </div>
  );
}
