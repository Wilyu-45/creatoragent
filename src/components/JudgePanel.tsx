import { useCallback, useEffect, useState } from 'react';
import {
  api,
  type EvaluationRecordView,
  type EvaluationsView,
  type JudgeReportView,
} from '../lib/api.ts';
import { formatDateTime, scoreTone } from '../lib/format.ts';
import { Chip, Empty, Spinner, Table } from './ui.tsx';

const VERDICT_LABEL: Record<string, string> = {
  pass: '通过',
  review: '待改进',
  reject: '不合格',
};

const VERDICT_TONE: Record<string, string> = {
  pass: 'tone-ok',
  review: 'tone-warn',
  reject: 'tone-bad',
};

const TRIGGER_LABEL: Record<string, string> = {
  review: '门禁时刻',
  delivery: '交付前',
  manual: '人工触发',
  feedback: '复盘回填',
};

/** 维度得分条（0–5 分归一化到百分比宽度）。 */
function AxisBar({ axis }: { axis: JudgeReportView['axes'][number] }) {
  const pct = Math.max(0, Math.min(100, (axis.score / 5) * 100));
  const tone = axis.score >= 4 ? 'tone-ok' : axis.score >= 3 ? 'tone-warn' : 'tone-bad';
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
        <span className="small">
          <strong>{axis.label}</strong>{' '}
          <span className="muted" style={{ fontSize: 11 }}>
            权重 {axis.weight}
          </span>
        </span>
        <Chip tone={tone}>{axis.score.toFixed(1)} / 5</Chip>
      </div>
      <div
        style={{
          height: 6,
          borderRadius: 3,
          background: 'var(--line, #2a2f3a)',
          marginTop: 4,
          overflow: 'hidden',
        }}
      >
        <div style={{ width: `${pct}%`, height: '100%', background: 'currentColor', opacity: 0.65 }} />
      </div>
      {axis.rationale ? (
        <div className="muted small" style={{ marginTop: 4 }}>
          {axis.rationale}
        </div>
      ) : null}
      {axis.evidence.length > 0 ? (
        <ul className="muted small" style={{ margin: '4px 0 0 16px', padding: 0 }}>
          {axis.evidence.slice(0, 3).map((item, index) => (
            <li key={`${axis.key}-${index}`}>{item}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * LLM-as-a-Judge 评估面板：把「这篇稿子有多好」与「下一轮 Prompt 该怎么改」量化出来。
 *
 * 与门禁的区别：门禁是**否决式**（能不能发），评估是**度量式**（有多好、差在哪）。
 * 面板同时展示两种评估器（offline 规则 / llm 模型）的结果与回退情况，
 * 方便判断某个分数是「模型判的」还是「规则判的」。
 */
export function JudgePanel({
  taskId,
  onToast,
}: {
  taskId: string;
  onToast?: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<EvaluationsView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    try {
      setView(await api.evaluations(taskId, 20));
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, [taskId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runNow = async (provider: 'offline' | 'llm'): Promise<void> => {
    setBusy(true);
    try {
      const result = await api.evaluateTask(taskId, provider);
      onToast?.(`评估完成：${result.record.total}/100（${VERDICT_LABEL[result.record.verdict] ?? result.record.verdict}）`);
      await reload();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setError(message);
      onToast?.(`评估失败：${message}`, true);
    } finally {
      setBusy(false);
    }
  };

  if (!view) return <div className="muted small">{error || '评估报告加载中…'}</div>;

  const { stats, config, records } = view;
  const latest = records[0] as EvaluationRecordView | undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
          <div>
            <div className="section-h">质量评估（LLM-as-a-Judge）</div>
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Chip tone={config.mode === 'off' ? 'tone-idle' : 'tone-info'}>
                模式 {config.mode}
              </Chip>
              <Chip tone="tone-idle">评估器 {config.provider}</Chip>
              <Chip tone="tone-idle">通过线 {config.passThreshold}</Chip>
              <Chip tone="tone-idle" title="评估分在综合质量分中的权重">
                权重 {config.weight}
              </Chip>
              <Chip tone="tone-idle" mono title="评分口径版本：同一批数据跨版本对比时必须先对齐它">
                {config.rubric}
              </Chip>
            </div>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn btn-sm" onClick={() => void runNow('offline')} disabled={busy}>
              {busy ? <Spinner /> : null} 规则评估
            </button>
            <button className="btn btn-sm" onClick={() => void runNow('llm')} disabled={busy}>
              模型评估
            </button>
          </div>
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          规则评估器零依赖、可离线、同一输入永远同一分数（用于回归对比）；模型评估走当前网关，
          <strong>失败会自动回退规则评估器</strong>并在报告里标注。
        </div>
      </div>

      {latest ? (
        <div className="card">
          <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
            <div className="row" style={{ gap: 8 }}>
              <span style={{ fontSize: 26, fontWeight: 700 }}>{latest.total.toFixed(1)}</span>
              <span className="muted small" style={{ alignSelf: 'flex-end' }}>
                / 100
              </span>
              <Chip tone={VERDICT_TONE[latest.verdict] ?? 'tone-idle'}>
                {VERDICT_LABEL[latest.verdict] ?? latest.verdict}
              </Chip>
              <Chip tone="tone-idle">{TRIGGER_LABEL[latest.trigger] ?? latest.trigger}</Chip>
              <Chip tone="tone-idle">返工 {latest.revision} 轮</Chip>
            </div>
            <span className="muted small">{formatDateTime(latest.created_at)}</span>
          </div>

          <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
            <Chip tone={latest.mode === 'llm' ? 'tone-ok' : 'tone-idle'} mono>
              {latest.mode}
              {latest.model ? ` · ${latest.model}` : ''}
            </Chip>
            <Chip tone="tone-idle" title="评估器对自身结论的把握程度">
              置信度 {(latest.confidence * 100).toFixed(0)}%
            </Chip>
            {latest.fallback ? (
              <Chip tone="tone-warn" title={latest.fallback_reason}>
                已回退规则评估器
              </Chip>
            ) : null}
            <Chip tone="tone-idle" mono title="评分口径版本">
              {latest.rubric}
            </Chip>
          </div>

          {latest.summary ? (
            <div className="small" style={{ marginTop: 8 }}>
              {latest.summary}
            </div>
          ) : null}

          <div style={{ marginTop: 12 }}>
            {latest.axes.map((axis) => (
              <AxisBar key={axis.key} axis={axis} />
            ))}
          </div>

          {latest.issues.length > 0 ? (
            <div style={{ marginTop: 4 }}>
              <div className="small" style={{ fontWeight: 600 }}>
                问题
              </div>
              <ul className="muted small" style={{ margin: '4px 0 0 16px', padding: 0 }}>
                {latest.issues.map((item, index) => (
                  <li key={`issue-${index}`}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          {latest.suggestions.length > 0 ? (
            <div style={{ marginTop: 8 }}>
              <div className="small" style={{ fontWeight: 600 }}>
                改进建议
              </div>
              <ul className="muted small" style={{ margin: '4px 0 0 16px', padding: 0 }}>
                {latest.suggestions.map((item, index) => (
                  <li key={`sug-${index}`}>{item}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : (
        <Empty>本任务还没有评估记录（门禁通过后会自动评估一次，交付前再评估一次）</Empty>
      )}

      <div>
        <div className="section-h">本任务评估历史</div>
        {records.length === 0 ? (
          <Empty>暂无记录</Empty>
        ) : (
          <Table head={['时间', '来源', '评估器', '总分', '裁决', '最弱维度', '返工轮次']}>
            {records.map((record) => {
              const weakest = record.axes.slice().sort((a, b) => a.score - b.score)[0];
              return (
                <tr key={record.id}>
                  <td className="small">{formatDateTime(record.created_at)}</td>
                  <td>
                    <Chip tone="tone-idle">{TRIGGER_LABEL[record.trigger] ?? record.trigger}</Chip>
                  </td>
                  <td className="small">
                    {record.mode}
                    {record.fallback ? <Chip tone="tone-warn">回退</Chip> : null}
                  </td>
                  <td className={scoreTone(record.total)} style={{ background: 'none', border: 'none' }}>
                    {record.total.toFixed(1)}
                  </td>
                  <td>
                    <Chip tone={VERDICT_TONE[record.verdict] ?? 'tone-idle'}>
                      {VERDICT_LABEL[record.verdict] ?? record.verdict}
                    </Chip>
                  </td>
                  <td className="small">
                    {weakest ? `${weakest.label} ${weakest.score.toFixed(1)}` : '—'}
                  </td>
                  <td>{record.revision}</td>
                </tr>
              );
            })}
          </Table>
        )}
      </div>

      <div>
        <div className="section-h">全库评估（当前租户）</div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <Chip tone="tone-info">记录 {stats.total}</Chip>
          <Chip tone="tone-idle">覆盖任务 {stats.tasks}</Chip>
          <Chip tone={scoreTone(stats.avg_total)}>均分 {stats.avg_total}</Chip>
          <Chip tone={stats.pass_rate >= 80 ? 'tone-ok' : 'tone-warn'}>
            通过率 {stats.pass_rate}%
          </Chip>
          <Chip tone="tone-ok">通过 {stats.verdicts.pass ?? 0}</Chip>
          <Chip tone="tone-warn">待改进 {stats.verdicts.review ?? 0}</Chip>
          <Chip tone="tone-bad">不合格 {stats.verdicts.reject ?? 0}</Chip>
          {stats.fallbacks > 0 ? (
            <Chip tone="tone-warn" title="模型评估失败并回退规则评估器的次数">
              回退 {stats.fallbacks}
            </Chip>
          ) : null}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
          {stats.axis_avg.map((axis) => (
            <Chip key={axis.key} tone={axis.score >= 4 ? 'tone-ok' : axis.score >= 3 ? 'tone-warn' : 'tone-bad'}>
              {axis.label} {axis.score}
            </Chip>
          ))}
        </div>
        <div className="muted small" style={{ marginTop: 6 }}>
          维度均分是 Prompt 调优的抓手：优先补最弱的那个维度，比「整体重写提示词」更容易看到分数变化。
        </div>
      </div>

      {error ? <div className="muted small">出错了：{error}</div> : null}
    </div>
  );
}
