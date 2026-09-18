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
  const judge = system.judge;
  const tracing = system.tracing;

  // 模型调用耗时占比：这是「钱和时间的分布」最直观的一个数字
  const llmTotalMs = (tracing?.by_name ?? [])
    .filter((row) => row.name.startsWith('llm.'))
    .reduce((sum, row) => sum + row.total_ms, 0);
  const allTotalMs = (tracing?.by_name ?? []).reduce((sum, row) => sum + row.total_ms, 0);
  const llmShare = allTotalMs ? (llmTotalMs / allTotalMs) * 100 : 0;

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
            <Chip
              key={`${provider.name}:${provider.model}`}
              tone={provider.simulated ? 'tone-warn' : 'tone-ok'}
              mono
              title={provider.agents?.length ? `覆盖来源：${provider.agents.join('、')}` : undefined}
            >
              {provider.name} · {provider.model}
              {provider.agents?.length ? `（${provider.agents.join('、')}）` : ''}
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
          质量评估 · LLM-as-a-Judge <span className="count">· 与门禁解耦的度量口径</span>
        </div>
        <div className="grid-3">
          <div className="card">
            <div className="muted small">评估次数</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatNumber(judge.total)}</div>
            <div className="muted small">覆盖任务 {formatNumber(judge.tasks)}</div>
          </div>
          <div className="card">
            <div className="muted small">平均评估分</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>
              {formatNumber(judge.avg_total, 1)}
            </div>
            <div className="muted small">满分 100</div>
          </div>
          <div className="card">
            <div className="muted small">评估通过率</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>
              {formatNumber(judge.pass_rate, 1)}
              <span className="muted small"> %</span>
            </div>
            <div className="muted small">
              待改进 {formatNumber(judge.verdicts.review ?? 0)}｜不合格 {formatNumber(judge.verdicts.reject ?? 0)}
            </div>
          </div>
        </div>

        <div className="row" style={{ marginTop: 14, flexWrap: 'wrap' }}>
          <Chip tone={judge.mode === 'off' ? 'tone-idle' : 'tone-info'}>评估模式 {judge.mode ?? '—'}</Chip>
          {Object.entries(judge.modes ?? {}).map(([mode, count]) => (
            <Chip key={mode} tone="tone-idle" mono>
              {mode} {count}
            </Chip>
          ))}
          {judge.fallbacks > 0 ? (
            <Chip tone="tone-warn" title="模型评估失败并回退到规则评估器的次数">
              回退规则评估器 {judge.fallbacks}
            </Chip>
          ) : null}
          {judge.latest_at ? <Chip tone="tone-idle">最近 {judge.latest_at.slice(0, 19)}</Chip> : null}
        </div>

        {judge.axis_avg.length > 0 ? (
          <div className="row" style={{ marginTop: 8, flexWrap: 'wrap' }}>
            {judge.axis_avg.map((axis) => (
              <Chip
                key={axis.key}
                tone={axis.score >= 4 ? 'tone-ok' : axis.score >= 3 ? 'tone-warn' : 'tone-bad'}
                title="该维度的历史平均分（0–5）"
              >
                {axis.label} {axis.score}
              </Chip>
            ))}
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="panel-title">
          调用轨迹 · 分布式追踪 <span className="count">· span 耗时排行（哪一层最贵）</span>
        </div>
        <div className="grid-3">
          <div className="card">
            <div className="muted small">已追踪任务</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatNumber(tracing?.traces ?? 0)}</div>
            <div className="muted small">span {formatNumber(tracing?.spans ?? 0)} 个</div>
          </div>
          <div className="card">
            <div className="muted small">span 错误数</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>{formatNumber(tracing?.errors ?? 0)}</div>
            <div className="muted small">降级与缓存命中不计为错误</div>
          </div>
          <div className="card">
            <div className="muted small">模型调用耗时占比</div>
            <div style={{ fontSize: 22, fontWeight: 700, marginTop: 2 }}>
              {formatNumber(llmShare, 1)}
              <span className="muted small"> %</span>
            </div>
            <div className="muted small">累计 {formatNumber(llmTotalMs)} ms</div>
          </div>
        </div>

        {(tracing?.by_name ?? []).length > 0 ? (
          <Table head={['span', '次数', '总耗时', '平均', '峰值', '错误']}>
            {tracing.by_name.slice(0, 10).map((row) => (
              <tr key={row.name}>
                <td>
                  <span className="mono small">{row.name}</span>
                </td>
                <td>{row.count}</td>
                <td className="mono small">{formatNumber(row.total_ms)} ms</td>
                <td className="mono small">{formatNumber(row.avg_ms)} ms</td>
                <td className="mono small">{formatNumber(row.max_ms)} ms</td>
                <td className={row.errors > 0 ? 'mono small' : 'muted small'}>{row.errors}</td>
              </tr>
            ))}
          </Table>
        ) : (
          <div className="muted small">还没有追踪数据（跑一个任务后即可看到 span 耗时分布）。</div>
        )}

        <div className="row" style={{ marginTop: 12, flexWrap: 'wrap' }}>
          {tracing ? (
            <>
              <Chip tone="tone-idle" mono title={`${tracing.exportPath}（${tracing.exportFormat}）`}>
                导出 {tracing.exportFormat}
              </Chip>
              {tracing.exportFailures > 0 ? (
                <Chip tone="tone-warn" title="trace 落盘失败次数（不影响任务执行）">
                  落盘失败 {tracing.exportFailures}
                </Chip>
              ) : null}
              {tracing.sampling ? (
                <Chip
                  tone="tone-idle"
                  mono
                  title="采样只作用于导出面（OTLP + 落盘）；未采样的 trace 在 Jaeger 查不到是预期行为"
                >
                  {tracing.sampling.sampler}
                  {tracing.sampling.sampler.includes('traceidratio')
                    ? ` @ ${tracing.sampling.sampleRatio}`
                    : ''}{' '}
                  · 导出 {tracing.sampling.traces_sampled} / 未导出 {tracing.sampling.traces_unsampled}
                </Chip>
              ) : null}
            </>
          ) : null}
          <Chip tone="tone-idle">
            点任务详情的「调用轨迹」查看单个任务的 span 瀑布图
          </Chip>
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
