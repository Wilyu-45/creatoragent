import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, type SpanView, type TraceView } from '../lib/api.ts';
import { formatNumber } from '../lib/format.ts';
import { Chip, Empty, Table } from './ui.tsx';

/** span 名前缀 → 视觉归类，便于在一屏里快速分辨「谁贵」。 */
function spanTone(name: string): string {
  if (name.startsWith('llm.')) return 'tone-info';
  if (name.startsWith('gate.')) return 'tone-warn';
  if (name.startsWith('judge.')) return 'tone-ok';
  if (name.startsWith('graph.')) return 'tone-run';
  if (name.startsWith('human.')) return 'tone-idle';
  if (name.startsWith('memory.')) return 'tone-ok';
  if (name.startsWith('publish.')) return 'tone-warn';
  return 'tone-idle';
}

/** 把 attributes 里的关键信息压成一行可读文本。 */
function describe(span: SpanView): string {
  const a = span.attributes ?? {};
  const parts: string[] = [];
  const push = (label: string, value: unknown, suffix = '') => {
    if (value === undefined || value === null || value === '') return;
    parts.push(`${label} ${value}${suffix}`);
  };
  push('模型', a['llm.model']);
  push('provider', a['llm.provider']);
  if (a['llm.prompt_tokens'] !== undefined) {
    parts.push(`token ${a['llm.prompt_tokens']}+${a['llm.completion_tokens']}`);
  }
  if (a['llm.cached']) parts.push('命中缓存');
  if (a['llm.simulated']) parts.push('离线引擎');
  push('降级', a['llm.degraded']);
  push('裁决', a['agent.gate_result']);
  if (a['agent.confidence'] !== undefined) {
    parts.push(`置信度 ${formatNumber(Number(a['agent.confidence']) * 100, 1)}%`);
  }
  push('门禁', a['gate.verdict']);
  if (a['gate.overall_score'] !== undefined) parts.push(`质量分 ${a['gate.overall_score']}`);
  if (a['judge.total'] !== undefined) parts.push(`评估 ${a['judge.total']}`);
  if (a['recall.hits'] !== undefined) parts.push(`召回 ${a['recall.hits']} 条`);
  push('渠道', a['publish.channel']);
  push('投递', a['publish.result']);
  if (a['graph.interrupted']) parts.push('在此挂起等待人工');
  return parts.join(' · ');
}

interface Row {
  span: SpanView;
  depth: number;
  /** 在 trace 总时长中的起止百分比，用于瀑布图定位 */
  left: number;
  width: number;
}

/**
 * 分布式追踪面板：把一次任务画成一棵可下钻的 span 树 + 一条瀑布图。
 *
 * 与「事件时间线」的分工：事件流回答「发生了什么」，span 树回答
 * 「耗时与花费**分布在哪一层**」——例如「门禁审核占了大头」这种结论，
 * 靠翻事件是拼不出来的。
 */
export function TracePanel({ taskId }: { taskId: string }) {
  const [view, setView] = useState<TraceView | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    try {
      setView(await api.trace(taskId));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [taskId]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [load]);

  /** 把扁平 span 列表折叠成带缩进的行（按父子关系与起始时间排序）。 */
  const rows = useMemo<Row[]>(() => {
    if (!view || view.spans.length === 0) return [];
    const spans = view.spans.slice().sort((a, b) => {
      if (a.started_at === b.started_at) return b.duration_ms - a.duration_ms;
      return a.started_at < b.started_at ? -1 : 1;
    });
    const byId = new Map(spans.map((span) => [span.span_id, span]));
    const childrenOf = new Map<string | null, SpanView[]>();
    for (const span of spans) {
      const key = span.parent_span_id && byId.has(span.parent_span_id) ? span.parent_span_id : null;
      const list = childrenOf.get(key) ?? [];
      list.push(span);
      childrenOf.set(key, list);
    }

    const traceStart = Math.min(
      ...spans.map((span) => new Date(span.started_at).getTime()).filter((n) => !Number.isNaN(n)),
    );
    const total = Math.max(1, view.summary?.duration_ms || (view as { duration_ms?: number }).duration_ms || 1);

    const out: Row[] = [];
    const walk = (parent: string | null, depth: number): void => {
      for (const span of childrenOf.get(parent) ?? []) {
        const start = new Date(span.started_at).getTime();
        const offset = Number.isNaN(start) ? 0 : Math.max(0, start - traceStart);
        out.push({
          span,
          depth,
          left: (offset / total) * 100,
          width: Math.max(0.4, (span.duration_ms / total) * 100),
        });
        if (!collapsed.has(span.span_id)) walk(span.span_id, depth + 1);
      }
    };
    walk(null, 0);

    // 防御：若存在环或断链导致有 span 未纳入遍历，兜底追加（不能让条目凭空消失）
    const seen = new Set(out.map((row) => row.span.span_id));
    for (const span of spans) {
      if (!seen.has(span.span_id)) {
        out.push({ span, depth: 0, left: 0, width: Math.max(0.4, (span.duration_ms / total) * 100) });
      }
    }
    return out;
  }, [view, collapsed]);

  const toggle = (spanId: string): void => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(spanId)) next.delete(spanId);
      else next.add(spanId);
      return next;
    });
  };

  if (!view) return <div className="muted small">{error || '轨迹加载中…'}</div>;

  const byName = view.summary?.by_name ?? [];
  const totalMs = view.summary?.duration_ms || (view as { duration_ms?: number }).duration_ms || 0;
  const llmMs = byName
    .filter((row) => row.name.startsWith('llm.'))
    .reduce((sum, row) => sum + row.total_ms, 0);
  const llmShare = totalMs ? (llmMs / totalMs) * 100 : 0;

  return (
    <div>
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <div className="section-h">调用轨迹（trace / span 树）</div>
          <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
            <Chip tone="tone-info" mono title="W3C trace-id（32 位十六进制），与 OTel 语义一致">
              {view.trace_id ? view.trace_id.slice(0, 16) : '无 trace'}
            </Chip>
            <Chip tone="tone-idle">span {view.span_count ?? view.spans.length}</Chip>
            <Chip tone="tone-idle">总耗时 {formatNumber(totalMs)} ms</Chip>
            <Chip tone={llmShare > 50 ? 'tone-warn' : 'tone-ok'} title="模型调用耗时占 trace 总耗时的比例">
              模型占比 {formatNumber(llmShare, 1)}%
            </Chip>
            {view.export ? (
              <Chip tone="tone-idle" title={`${view.export.path}\\${view.export.file}（${view.export.format}）`}>
                已导出 OTel 形状 JSON
              </Chip>
            ) : null}
          </div>
        </div>
        <button className="btn btn-sm btn-ghost" onClick={() => void load()}>
          刷新
        </button>
      </div>

      {view.notes ? <div className="muted small" style={{ marginTop: 6 }}>{view.notes}</div> : null}

      {rows.length === 0 ? (
        <Empty>该任务暂无轨迹记录</Empty>
      ) : (
        <div style={{ marginTop: 12 }}>
          {rows.map((row) => {
            const { span } = row;
            const hasChildren = span.children > 0;
            return (
              <div
                key={span.span_id}
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'minmax(240px, 34%) 1fr 76px',
                  gap: 10,
                  alignItems: 'center',
                  padding: '3px 0',
                  borderBottom: '1px solid var(--line, #22262f)',
                }}
              >
                <div style={{ paddingLeft: row.depth * 14, minWidth: 0 }}>
                  <div className="row" style={{ gap: 6 }}>
                    {hasChildren ? (
                      <button
                        className="btn btn-sm btn-ghost"
                        style={{ padding: '0 4px', minWidth: 18 }}
                        onClick={() => toggle(span.span_id)}
                        title={collapsed.has(span.span_id) ? '展开' : '折叠'}
                      >
                        {collapsed.has(span.span_id) ? '▸' : '▾'}
                      </button>
                    ) : (
                      <span style={{ display: 'inline-block', width: 18 }} />
                    )}
                    <Chip tone={spanTone(span.name)} mono>
                      {span.name}
                    </Chip>
                    {span.status === 'error' ? (
                      <Chip tone="tone-bad" title={span.status_message}>
                        错误
                      </Chip>
                    ) : null}
                  </div>
                  {/* span 属性是排障时真正要看的东西：模型、token、裁决、降级原因 */}
                  {describe(span) ? (
                    <div
                      className="muted small"
                      style={{
                        marginLeft: 18 + row.depth * 0,
                        fontSize: 11,
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                      }}
                    >
                      {describe(span)}
                    </div>
                  ) : null}
                </div>

                <div
                  style={{
                    position: 'relative',
                    height: 14,
                    background: 'var(--line, #22262f)',
                    borderRadius: 3,
                    overflow: 'hidden',
                  }}
                  title={`${span.duration_ms} ms（自身占比 ${(span.self_ratio * 100).toFixed(1)}%）`}
                >
                  <div
                    style={{
                      position: 'absolute',
                      left: `${Math.min(99, row.left)}%`,
                      width: `${row.width}%`,
                      height: '100%',
                      background: 'currentColor',
                      opacity: span.kind === 'client' ? 0.85 : 0.5,
                    }}
                  />
                </div>

                <div className="small mono" style={{ textAlign: 'right' }}>
                  {formatNumber(span.duration_ms)} ms
                </div>
              </div>
            );
          })}
        </div>
      )}

      {byName.length > 0 ? (
        <div style={{ marginTop: 18 }}>
          <div className="section-h">耗时排行（哪一层最贵）</div>
          <Table head={['span', '类型', '次数', '总耗时', '平均', '峰值', '占比']}>
            {byName.slice(0, 15).map((row) => (
              <tr key={row.name}>
                <td>
                  <Chip tone={spanTone(row.name)} mono>
                    {row.name}
                  </Chip>
                </td>
                <td className="small">{row.kind}</td>
                <td>{row.count}</td>
                <td className="mono small">{formatNumber(row.total_ms)} ms</td>
                <td className="mono small">{formatNumber(row.avg_ms ?? 0)} ms</td>
                <td className="mono small">{formatNumber(row.max_ms)} ms</td>
                <td className="small">
                  {row.share !== undefined ? `${formatNumber(row.share * 100, 1)}%` : '—'}
                </td>
              </tr>
            ))}
          </Table>
          <div className="muted small" style={{ marginTop: 6 }}>
            占比是「该 span 累计耗时 / trace 总耗时」，**会因嵌套而重叠**（父 span 包含子 span），
            因此它用于定位瓶颈层级，而不是求和。要看单层实际开销请看瀑布图标题里的「自身占比」。
          </div>
        </div>
      ) : null}

      {error ? <div className="muted small">出错了：{error}</div> : null}
    </div>
  );
}
