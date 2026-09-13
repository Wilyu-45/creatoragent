import { useCallback, useEffect, useRef, useState } from 'react';
import { api, type GoldenComparisonView, type GoldenView } from '../lib/api.ts';
import { formatDateTime, scoreTone } from '../lib/format.ts';
import { Chip, Empty, Spinner, Table } from './ui.tsx';

const VERDICT_LABEL: Record<string, string> = {
  ok: '持平',
  improved: '提升',
  regressed: '回退',
  new: '新增',
  missing: '缺失',
  failed: '失败',
};

const VERDICT_TONE: Record<string, string> = {
  ok: 'tone-idle',
  improved: 'tone-ok',
  regressed: 'tone-bad',
  new: 'tone-info',
  missing: 'tone-warn',
  failed: 'tone-bad',
};

const METRIC_LABEL: Record<string, string> = {
  quality_score: '质量分',
  judge_total: '评估分(门禁)',
  judge_final_total: '评估分(交付)',
  revision_round: '返工轮次',
  artifact_count: '产物数',
  fact_accuracy: '事实准确率',
  brand_consistency: '品牌一致性',
};

/**
 * 黄金数据集回归面板：把「我把 Prompt 改好了」从主观判断变成可判定问题。
 *
 * 固定 10 条 Brief（覆盖全部 7 个渠道），把每次运行压缩成质量分、评估分、返工轮次
 * 等标量，与 ``golden/baseline.json`` 逐项对比：
 * 任何一项回退超过容差、返工轮次增加、监管用例未被拦截，都会判为「回退」。
 *
 * 运行是**服务端后台任务**（跑完 10 条约一分钟），因此这里只负责启动与轮询进度。
 */
export function GoldenPanel({ onToast }: { onToast?: (text: string, error?: boolean) => void }) {
  const [view, setView] = useState<GoldenView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.golden();
      setView(data);
      setError('');
      return data;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      return null;
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [load]);

  // 运行中每 2s 轮询一次进度；跑完自动停
  useEffect(() => {
    if (!view?.state.running) {
      if (timer.current) {
        window.clearInterval(timer.current);
        timer.current = null;
      }
      return;
    }
    if (timer.current) return;
    timer.current = window.setInterval(() => void load(), 2000);
    return () => {
      if (timer.current) {
        window.clearInterval(timer.current);
        timer.current = null;
      }
    };
  }, [view?.state.running, load]);

  const start = async (limit?: number): Promise<void> => {
    setBusy(true);
    try {
      const result = await api.runGolden(limit ? { limit } : {});
      setView((prev) => (prev ? { ...prev, state: result.state } : prev));
      onToast?.(`黄金回归已启动（${result.state.total} 条用例）`);
      await load();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      onToast?.(`启动失败：${message}`, true);
    } finally {
      setBusy(false);
    }
  };

  const reset = async (): Promise<void> => {
    try {
      await api.resetGolden();
      await load();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  if (!view) return <div className="muted small">{error || '黄金数据集加载中…'}</div>;

  const { cases, baseline, coverage: cov, state } = view;
  const comparison: GoldenComparisonView | undefined = view.comparison;
  const resultsById = new Map(state.results.map((item) => [item.id, item]));
  const baselineCount = Object.keys(baseline.cases ?? {}).length;
  const progress = state.total ? Math.round((state.completed / state.total) * 100) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
          <div>
            <div className="section-h">黄金数据集回归（固定输入 → 可判定结论）</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Chip tone="tone-info">用例 {cases.length}</Chip>
              <Chip tone="tone-idle">覆盖渠道 {cov.channels.length}/7</Chip>
              <Chip tone="tone-idle">覆盖行业 {cov.industries.length}</Chip>
              <Chip tone={baselineCount ? 'tone-ok' : 'tone-warn'}>
                基线 {baselineCount} 条
              </Chip>
              <Chip tone="tone-idle">容差 ±{view.tolerance}</Chip>
              <Chip tone="tone-idle" mono title="评分口径版本：跨版本分数不可比">
                {view.rubric}
              </Chip>
            </div>
            <div className="muted small" style={{ marginTop: 6 }}>
              基线更新于 {baseline.updated_at ? formatDateTime(baseline.updated_at) : '（尚无基线）'}
              ｜引擎 {baseline.engine || '—'}
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn btn-sm" onClick={() => void start(3)} disabled={busy || state.running}>
              {busy || state.running ? <Spinner /> : null} 快速跑 3 条
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => void start()}
              disabled={busy || state.running}
            >
              跑全量回归
            </button>
            {state.results.length > 0 && !state.running ? (
              <button className="btn btn-sm btn-ghost" onClick={() => void reset()}>
                清除结果
              </button>
            ) : null}
          </div>
        </div>

        {state.running ? (
          <div style={{ marginTop: 10 }}>
            <div className="muted small">
              运行中 {state.completed}/{state.total}
              {state.current ? `｜当前 ${state.current}` : ''}（{progress}%）
            </div>
            <div style={{ height: 6, borderRadius: 3, background: 'var(--line, #2a2f3a)', marginTop: 4 }}>
              <div
                style={{ width: `${progress}%`, height: '100%', background: 'currentColor', opacity: 0.65 }}
              />
            </div>
          </div>
        ) : null}

        {state.scope ? (
          <div className="muted small" style={{ marginTop: 6 }}>
            注意：上一次是**部分运行**（{state.scope.length} 条用例），结论只对这部分成立。
          </div>
        ) : null}

        {state.error ? <div className="muted small" style={{ marginTop: 6 }}>运行出错：{state.error}</div> : null}

        {cov.uncovered_channels.length > 0 ? (
          <div className="muted small" style={{ marginTop: 6 }}>
            尚未覆盖的渠道：{cov.uncovered_channels.join('、')}
          </div>
        ) : null}
      </div>

      {comparison ? (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
            <div className="row" style={{ gap: 8 }}>
              <Chip tone={comparison.ok ? 'tone-ok' : 'tone-bad'}>
                {comparison.ok ? '无回归' : '存在回归'}
              </Chip>
              <Chip tone="tone-idle">持平 {comparison.counts.ok ?? 0}</Chip>
              <Chip tone={(comparison.counts.improved ?? 0) > 0 ? 'tone-ok' : 'tone-idle'}>
                提升 {comparison.counts.improved ?? 0}
              </Chip>
              <Chip tone={(comparison.counts.regressed ?? 0) > 0 ? 'tone-bad' : 'tone-idle'}>
                回退 {comparison.counts.regressed ?? 0}
              </Chip>
              <Chip tone="tone-idle">新增 {comparison.counts.new ?? 0}</Chip>
              <Chip tone={(comparison.counts.missing ?? 0) > 0 ? 'tone-warn' : 'tone-idle'}>
                缺失 {comparison.counts.missing ?? 0}
              </Chip>
              <Chip tone={(comparison.counts.failed ?? 0) > 0 ? 'tone-bad' : 'tone-idle'}>
                失败 {comparison.counts.failed ?? 0}
              </Chip>
            </div>
            {comparison.partial ? <Chip tone="tone-warn">部分运行</Chip> : null}
          </div>
          {comparison.rubric_mismatch ? (
            <div className="law" style={{ marginTop: 8 }}>
              口径不一致：基线 rubric={comparison.baseline_rubric}，当前 rubric={comparison.rubric}。
              分数不可直接比较，请先确认是否需要重建基线。
            </div>
          ) : null}

          <Table head={['用例', '结论', '质量分', '评估分(交付)', '返工', '产物', '说明']}>
            {comparison.differences.map((diff) => {
              const result = resultsById.get(diff.id);
              const base = baseline.cases?.[diff.id];
              const qualityDelta = diff.deltas.quality_score;
              return (
                <tr key={diff.id}>
                  <td className="mono small">{diff.id}</td>
                  <td>
                    <Chip tone={VERDICT_TONE[diff.verdict] ?? 'tone-idle'}>
                      {VERDICT_LABEL[diff.verdict] ?? diff.verdict}
                    </Chip>
                  </td>
                  <td className={scoreTone(result?.quality_score ?? base?.quality_score ?? 0)} style={{ background: 'none', border: 'none' }}>
                    {(result?.quality_score ?? base?.quality_score ?? 0).toFixed(1)}
                    {typeof qualityDelta === 'number' && Math.abs(qualityDelta) >= 0.05 ? (
                      <span className="muted small"> {qualityDelta > 0 ? '+' : ''}{qualityDelta.toFixed(1)}</span>
                    ) : null}
                  </td>
                  <td>
                    {(result?.judge_final_total ?? base?.judge_final_total ?? 0).toFixed(1)}
                  </td>
                  <td>
                    {result?.revision_round ?? base?.revision_round ?? '—'}
                    {result?.first_round_blocked ? (
                      <Chip tone="tone-warn" title="首轮合规被拦截（监管用例的预期行为）">
                        拦
                      </Chip>
                    ) : null}
                  </td>
                  <td>{result?.artifact_count ?? base?.artifact_count ?? '—'}</td>
                  <td className="muted small">{diff.reasons.join('；') || '—'}</td>
                </tr>
              );
            })}
          </Table>

          {comparison.differences.some((item) => Object.keys(item.deltas).length > 0) ? (
            <div className="muted small" style={{ marginTop: 8 }}>
              指标口径：
              {Object.keys(comparison.differences.find((item) => Object.keys(item.deltas).length > 0)!.deltas)
                .map((key) => METRIC_LABEL[key] ?? key)
                .join('、')}
              —— 回退判定同时看分数与**返工轮次**（轮次增加意味着更慢更贵）。
            </div>
          ) : null}
        </div>
      ) : state.results.length > 0 ? (
        <div className="muted small">结果已就绪，正在计算与基线的对比…</div>
      ) : (
        <Empty>
          还没有运行结果。点「跑全量回归」会依次执行 {cases.length} 条固定 Brief，
          并与基线逐项对比。
        </Empty>
      )}

      <div>
        <div className="section-h">用例清单（覆盖矩阵）</div>
        <Table head={['用例', '渠道', '行业', '目标', '关键词', '硬性约束']}>
          {cases.map((item) => (
            <tr key={item.id}>
              <td>
                <div className="mono small">{item.id}</div>
                <div className="muted small">{item.note}</div>
              </td>
              <td>
                <Chip tone="tone-idle">{item.channel}</Chip>
              </td>
              <td className="small">{item.industry}</td>
              <td className="small">{item.objective}</td>
              <td className="small">{item.keywords.join('、') || '—'}</td>
              <td className="small">{item.constraints.join('；') || '—'}</td>
            </tr>
          ))}
        </Table>
        <div className="muted small" style={{ marginTop: 6 }}>
          修改基线等于「把当前分数认定为新的合格线」，请只在确认每条回退都能解释之后，
          用 <code>python scripts/golden_eval.py --update-baseline</code> 更新。
        </div>
      </div>

      {error ? <div className="muted small">出错了：{error}</div> : null}
    </div>
  );
}
