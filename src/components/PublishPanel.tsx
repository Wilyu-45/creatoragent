import { useCallback, useEffect, useState } from 'react';
import type { PublishScheduleView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime, formatNumber } from '../lib/format.ts';
import { Chip, Spinner, Table } from './ui.tsx';

interface FeedbackForm {
  channel: string;
  window: string;
  url: string;
  exposure: string;
  clicks: string;
  interactions: string;
  conversions: string;
}

const EMPTY_FORM: FeedbackForm = {
  channel: '',
  window: '发布后 72 小时',
  url: '',
  exposure: '',
  clicks: '',
  interactions: '',
  conversions: '',
};

type ReviewArtifact = TaskRecord['artifacts'][number];

/** 从复盘版效果报告中读取该次回填的实绩（每次回填都会生成一版）。 */
function reviewActuals(artifact: ReviewArtifact): {
  channel: string;
  window: string;
  exposure: number | null;
  clicks: number | null;
  ctr: number | null;
} {
  const content = artifact.content;
  const actuals =
    content.actuals && typeof content.actuals === 'object' && !Array.isArray(content.actuals)
      ? (content.actuals as Record<string, unknown>)
      : {};
  const num = (value: unknown): number | null => {
    const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
  };
  return {
    channel:
      typeof actuals.channel === 'string'
        ? actuals.channel
        : typeof content.channel === 'string'
          ? content.channel
          : '',
    window: typeof content.window === 'string' ? content.window : '',
    exposure: num(actuals.exposure),
    clicks: num(actuals.clicks),
    ctr: num(actuals.ctr),
  };
}

/** 上次回填过的数据直接带回表单，便于按 24h → 72h 的节奏改数重填。 */
function feedbackFormFromTask(task: TaskRecord): FeedbackForm {
  const actuals = task.feedback_actuals;
  if (!actuals) return EMPTY_FORM;
  const text = (key: string): string => {
    const value = actuals[key];
    return typeof value === 'number' || typeof value === 'string' ? String(value) : '';
  };
  return {
    channel: typeof actuals.channel === 'string' ? actuals.channel : '',
    window: typeof actuals.window === 'string' && actuals.window.trim() ? actuals.window : EMPTY_FORM.window,
    url: '',
    exposure: text('exposure'),
    clicks: text('clicks'),
    interactions: text('interactions'),
    conversions: text('conversions'),
  };
}

/** 把输入框文本转成正数；空串或非法值返回 null。 */
function toNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

/** 发布前预估的 CTR 区间：回填时用来做即时对照，不作为承诺值。 */
function predictedCtr(artifacts: TaskRecord['artifacts']): {
  version: number;
  low: number | null;
  mid: number | null;
  high: number | null;
} | null {
  const prediction = artifacts
    .filter((artifact) => artifact.type === 'effect_report' && artifact.content.mode !== 'post_publish_review')
    .sort((a, b) => b.version - a.version)[0];
  if (!prediction) return null;
  const predicted = prediction.content.predicted;
  const predictedObj =
    predicted && typeof predicted === 'object' && !Array.isArray(predicted)
      ? (predicted as Record<string, unknown>)
      : {};
  const ctr = predictedObj.ctr;
  const band = ctr && typeof ctr === 'object' && !Array.isArray(ctr) ? (ctr as Record<string, unknown>) : {};
  const num = (value: unknown): number | null => {
    const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
  };
  return {
    version: prediction.version,
    low: num(band.low),
    mid: num(band.mid),
    high: num(band.high),
  };
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 投递回执状态 → 展示文案与色调。 */
const DISPATCH_META: Record<string, { label: string; tone: string }> = {
  pending: { label: '待投递', tone: 'tone-idle' },
  dispatched: { label: '已投递', tone: 'tone-ok' },
  skipped: { label: '已登记', tone: 'tone-ok' },
  failed: { label: '投递失败', tone: 'tone-warn' },
};

/**
 * 发布与效果回填面板。
 *
 * 审批通过后编排层会自动生成 `publish_plan`（渠道 × 时段排期）；本面板提供三种操作：
 * 1. **立即投递**：按排期 POST 到发布 webhook（未配置 webhook 时等价于登记发布），
 *    由网关去调用各平台开放接口——系统不内置平台私有 SDK；
 * 2. **登记发布**：人工确认渠道已投出去，形成效果对齐的基线；
 * 3. **回填效果**：填曝光 / 点击 / 互动 / 转化，触发 A10 从「发布前预估」切到「发布后复盘」。
 */
export function PublishPanel({
  task,
  onChanged,
  onToast,
  onOpenArtifact,
}: {
  task: TaskRecord;
  onChanged: () => void;
  onToast: (text: string, error?: boolean) => void;
  onOpenArtifact?: (artifactId: string) => void;
}) {
  const [plan, setPlan] = useState<PublishScheduleView | null>(null);
  const [planError, setPlanError] = useState('');
  const [working, setWorking] = useState('');
  const [form, setForm] = useState<FeedbackForm>(EMPTY_FORM);

  const hasPlan = task.artifacts.some((artifact) => artifact.type === 'publish_plan');

  const load = useCallback(async () => {
    if (!hasPlan) {
      setPlan(null);
      setPlanError('');
      return;
    }
    try {
      setPlan(await api.publishSchedule(task.id));
      setPlanError('');
    } catch (cause) {
      /* 保留上一次排期快照，同时把失败原因显式展示出来 */
      setPlanError(errorText(cause));
    }
  }, [hasPlan, task.id]);

  useEffect(() => {
    setForm(feedbackFormFromTask(task));
    setPlan(null);
    setPlanError('');
    void load();
  }, [load, task.id]);

  if (!hasPlan) return null;

  const schedule = plan?.schedule ?? [];
  const pending = schedule.filter((item) => item.status !== 'published');
  const publishedCount = schedule.length - pending.length;
  const now = Date.now();
  const isDue = (item: { due_at: string | null }): boolean => {
    if (!item.due_at) return false;
    const ts = new Date(item.due_at).getTime();
    return Number.isFinite(ts) && ts <= now;
  };
  const dueCount = pending.filter(isDue).length;
  const hasFeedback = Boolean(task.feedback_actuals);
  const actuals = hasFeedback ? task.feedback_actuals ?? null : null;
  const reviewReports = task.artifacts
    .filter((artifact) => artifact.type === 'effect_report' && artifact.content.mode === 'post_publish_review')
    .sort((a, b) => b.version - a.version);
  const reviewArtifact = reviewReports[0] ?? null;

  const metric = (key: string): number | null => {
    const value = actuals?.[key];
    const parsed = typeof value === 'number' ? value : typeof value === 'string' ? Number(value) : NaN;
    return Number.isFinite(parsed) ? parsed : null;
  };
  const rate = (numerator: number | null, denominator: number | null): number | null =>
    numerator !== null && denominator !== null && denominator > 0 ? (numerator / denominator) * 100 : null;
  const exposure = metric('exposure');
  const clicks = metric('clicks');
  const interactions = metric('interactions');
  const conversions = metric('conversions');
  const ctr = rate(clicks, exposure);
  const actualChannel = typeof actuals?.channel === 'string' ? actuals.channel : '';
  const actualWindow = typeof actuals?.window === 'string' ? actuals.window : '';

  const draftExposure = toNumber(form.exposure);
  const draftClicks = toNumber(form.clicks);
  const draftInteractions = toNumber(form.interactions);
  const draftConversions = toNumber(form.conversions);
  const draftCtr = rate(draftClicks, draftExposure);
  const draftEngagement = rate(draftInteractions, draftExposure);
  const draftConversion = rate(draftConversions, draftClicks);
  const baseline = predictedCtr(task.artifacts);
  const baselineRange = baseline
    ? baseline.low !== null && baseline.high !== null
      ? `${formatNumber(baseline.low, 1)}%~${formatNumber(baseline.high, 1)}%`
      : baseline.mid !== null
        ? `中位 ${formatNumber(baseline.mid, 1)}%`
        : ''
    : '';
  const ctrVerdict: { label: string; tone: string } | null = (() => {
    if (draftCtr === null || !baseline) return null;
    if (baseline.low !== null && draftCtr < baseline.low) return { label: '低于预估下限', tone: 'tone-warn' };
    if (baseline.high !== null && draftCtr > baseline.high) return { label: '高于预估上限', tone: 'tone-ok' };
    if (baseline.low !== null && baseline.high !== null) return { label: '落在预估区间', tone: 'tone-ok' };
    if (baseline.mid !== null) {
      return draftCtr >= baseline.mid
        ? { label: '高于预估中位', tone: 'tone-ok' }
        : { label: '低于预估中位', tone: 'tone-warn' };
    }
    return null;
  })();
  const inputWarning =
    draftExposure === null || draftClicks === null
      ? null
      : draftExposure === 0 && draftClicks > 0
        ? '曝光量为 0 但点击量大于 0，请核对填写的口径。'
        : draftClicks > draftExposure
          ? '点击量大于曝光量，CTR 会超过 100%，请确认是否填反了字段。'
          : null;
  const missingCore = draftExposure === null || draftClicks === null;
  const pctText = (value: number | null): string => (value === null ? '—' : `${formatNumber(value, 2)}%`);

  const setField = (key: keyof FeedbackForm, value: string) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const markPublished = async (channel: string) => {
    setWorking(channel || '__all__');
    try {
      const result = await api.markPublished(task.id, channel, form.url.trim());
      onToast(
        channel
          ? `已登记发布：${channel}`
          : `已登记发布 ${result.published.length} 个渠道：${result.published.join('、')}`,
      );
      await load();
      onChanged();
    } catch (error) {
      onToast(`登记发布失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const refreshSchedule = async () => {
    setWorking('__refresh__');
    try {
      await load();
    } finally {
      setWorking('');
    }
  };

  /** 投递：force=true 立即投递；force=false 只处理已到期条目（与后台定时器同口径）。 */
  const dispatchPublish = async (channel: string, force = true) => {
    setWorking(force ? `d:${channel || '__all__'}` : 'd:__due__');
    try {
      const result = await api.dispatchPublish(task.id, channel, force);
      const parts: string[] = [];
      if (result.dispatched.length) parts.push(`已投递 ${result.dispatched.join('、')}`);
      if (result.failed.length) parts.push(`失败 ${result.failed.join('、')}`);
      if (!force && result.skipped.length) parts.push(`未到期 ${result.skipped.length} 个`);
      const prefix = force ? '发布投递' : '到期投递';
      onToast(
        parts.length ? `${prefix}：${parts.join('｜')}` : force ? '没有需要投递的渠道' : '没有到期的待发布项',
        result.failed.length > 0,
      );
      await load();
      onChanged();
    } catch (error) {
      onToast(`投递失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const submitFeedback = async () => {
    const metrics: Record<string, number> = {};
    const exposureValue = toNumber(form.exposure);
    const clicksValue = toNumber(form.clicks);
    const interactionsValue = toNumber(form.interactions);
    const conversionsValue = toNumber(form.conversions);
    if (exposureValue !== null) metrics.exposure = exposureValue;
    if (clicksValue !== null) metrics.clicks = clicksValue;
    if (interactionsValue !== null) metrics.interactions = interactionsValue;
    if (conversionsValue !== null) metrics.conversions = conversionsValue;

    if (exposureValue === null || clicksValue === null) {
      onToast('请填写曝光量与点击量（互动量与转化量可选）', true);
      return;
    }

    setWorking('__feedback__');
    try {
      const channel = form.channel.trim();
      await api.submitFeedback(task.id, {
        channel: channel || undefined,
        window: form.window.trim() || undefined,
        url: form.url.trim() || undefined,
        metrics,
      });
      onToast('效果数据已回填，A10 完成复盘并给出 A/B 结论');
      setForm((prev) => ({ ...prev, url: '' }));
      await load();
      onChanged();
    } catch (error) {
      onToast(`回填失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="panel">
      <div className="panel-title">
        发布与效果回填
        <span className="count">
          · 已发布 {publishedCount} / {schedule.length} 个渠道
          {dueCount ? ` · ${dueCount} 个已到期` : ''}
          {plan?.published_at ? ` · 排期生成于 ${formatDateTime(plan.published_at)}` : ''}
        </span>
      </div>

      {actuals ? (
        <div className="feedback-summary">
          <div className="feedback-summary-head">
            <div>
              <strong>最近一次效果回填</strong>
              <span className="muted small">
                {' · '}
                {actualChannel || task.brief.channel}
                {actualWindow ? ` · ${actualWindow}` : ''}
                {reviewArtifact ? ` · 复盘报告 v${reviewArtifact.version}` : ''}
              </span>
            </div>
            {reviewArtifact && onOpenArtifact ? (
              <button type="button" className="btn btn-sm btn-ghost" onClick={() => onOpenArtifact(reviewArtifact.id)}>
                查看复盘报告 →
              </button>
            ) : null}
          </div>
          <div className="feedback-metrics">
            <div className="feedback-metric">
              <span>曝光</span>
              <b>{formatNumber(exposure)}</b>
            </div>
            <div className="feedback-metric">
              <span>点击</span>
              <b>{formatNumber(clicks)}</b>
            </div>
            <div className="feedback-metric">
              <span>CTR</span>
              <b>{ctr !== null ? `${ctr.toFixed(2)}%` : '—'}</b>
            </div>
            <div className="feedback-metric">
              <span>互动</span>
              <b>{formatNumber(interactions)}</b>
            </div>
            <div className="feedback-metric">
              <span>转化</span>
              <b>{formatNumber(conversions)}</b>
            </div>
          </div>
          {reviewReports.length > 1 ? (
            <details className="feedback-history">
              <summary>历史回填 · {reviewReports.length} 次</summary>
              <div className="feedback-history-list">
                {reviewReports.map((artifact) => {
                  const info = reviewActuals(artifact);
                  const artifactCtr = info.ctr ?? rate(info.clicks, info.exposure);
                  return (
                    <button
                      key={artifact.id}
                      type="button"
                      className="feedback-history-item"
                      onClick={() => onOpenArtifact?.(artifact.id)}
                      disabled={!onOpenArtifact}
                      title="打开该版本复盘报告"
                    >
                      <b>v{artifact.version}</b>
                      <span>
                        {info.window || '观察窗口未记录'}
                        {info.channel ? ` · ${info.channel}` : ''}
                      </span>
                      <span className="muted small">
                        曝光 {formatNumber(info.exposure)} · CTR{' '}
                        {artifactCtr === null ? '—' : `${formatNumber(artifactCtr, 2)}%`}
                      </span>
                      <span className="feedback-history-time muted small">{formatDateTime(artifact.created_at)}</span>
                    </button>
                  );
                })}
              </div>
            </details>
          ) : null}
        </div>
      ) : null}

      {planError ? (
        <div className="row" style={{ color: 'var(--bad)', gap: 8, marginBottom: 10, flexWrap: 'wrap' }}>
          <span className="small">排期读取失败：{planError}</span>
          <button
            type="button"
            className="btn btn-sm btn-ghost"
            disabled={working !== ''}
            onClick={() => void refreshSchedule()}
          >
            {working === '__refresh__' ? <Spinner /> : null} 重试
          </button>
        </div>
      ) : null}
      {!plan && !planError ? <div className="muted small" style={{ marginBottom: 10 }}>排期加载中…</div> : null}

      {plan ? (
        <Table head={['序号', '渠道', '建议时段', '到期时间', '状态', '投放标题', '操作']}>
        {schedule.map((item, index) => {
          const dispatch = DISPATCH_META[item.dispatch_status] ?? {
            label: item.dispatch_status || '待投递',
            tone: 'tone-idle',
          };
          return (
            <tr key={`${item.channel}-${index}`}>
              <td className="mono">{item.order}</td>
              <td>
                <strong>{item.channel}</strong>
              </td>
              <td className="mono">{item.slot || '—'}</td>
              <td className="mono small">{item.due_at ? formatDateTime(item.due_at) : '—'}</td>
              <td>
                <Chip tone={item.status === 'published' ? 'tone-ok' : 'tone-warn'}>
                  {item.status === 'published' ? '已发布' : '待发布'}
                </Chip>{' '}
                <Chip tone={dispatch.tone} title={item.last_error || undefined}>
                  {dispatch.label}
                  {item.attempts > 0 ? ` ×${item.attempts}` : ''}
                </Chip>{' '}
                {item.status !== 'published' && isDue(item) ? (
                  <Chip tone="tone-info" title="已到建议投递时间">
                    已到期
                  </Chip>
                ) : null}
              </td>
              <td>{item.title || '—'}</td>
              <td>
                {item.status === 'published' ? (
                  <span className="muted small">{formatDateTime(item.published_at)}</span>
                ) : (
                  <span className="row" style={{ gap: 6 }}>
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={working !== ''}
                      onClick={() => void dispatchPublish(item.channel)}
                    >
                      {working === `d:${item.channel}` ? <Spinner /> : null} 投递
                    </button>
                    <button
                      className="btn btn-sm"
                      disabled={working !== ''}
                      onClick={() => void markPublished(item.channel)}
                    >
                      {working === item.channel ? <Spinner /> : null} 登记
                    </button>
                  </span>
                )}
              </td>
            </tr>
          );
        })}
      </Table>
      ) : null}

      <div className="row" style={{ justifyContent: 'space-between', marginTop: 12, alignItems: 'flex-end' }}>
        <span className="muted small">
          发布链接（可选，登记时一并记录）：
          <input
            value={form.url}
            placeholder="https://…"
            onChange={(event) => setField('url', event.target.value)}
            style={{ width: 260, marginLeft: 8 }}
          />
        </span>
        <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button
            className="btn btn-sm btn-ghost"
            disabled={working !== ''}
            onClick={() => void refreshSchedule()}
          >
            {working === '__refresh__' ? <Spinner /> : null} 刷新排期
          </button>
          <button
            className="btn btn-sm"
            disabled={working !== '' || pending.length === 0}
            onClick={() => void markPublished('')}
          >
            {working === '__all__' ? <Spinner /> : null} 全部登记发布
          </button>
          <button
            className="btn btn-sm"
            disabled={working !== '' || pending.length === 0}
            title="忽略到期时间，立即投递所有待发布渠道"
            onClick={() => void dispatchPublish('', true)}
          >
            {working === 'd:__all__' ? <Spinner /> : null} 全部投递
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={working !== '' || dueCount === 0}
            title="只投递已到期且未发布的渠道（与后台定时器同口径）"
            onClick={() => void dispatchPublish('', false)}
          >
            {working === 'd:__due__' ? <Spinner /> : null} 投递到期项（{dueCount}）
          </button>
        </span>
      </div>

      <div className="section-h" style={{ marginTop: 18 }}>
        效果数据回填（触发 A10 复盘）{hasFeedback ? ' · 已回填，可再次回填覆盖' : ''}
      </div>
      <div className="form-grid">
        <div className="field">
          <label>渠道</label>
          <select value={form.channel} onChange={(event) => setField('channel', event.target.value)}>
            <option value="">默认（{task.brief.channel}）</option>
            {schedule.map((item) => (
              <option key={item.channel} value={item.channel}>
                {item.channel}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label>观察窗口</label>
          <input value={form.window} onChange={(event) => setField('window', event.target.value)} />
        </div>
        <div className="field">
          <label>曝光量 *</label>
          <input
            type="number"
            min="0"
            value={form.exposure}
            onChange={(event) => setField('exposure', event.target.value)}
          />
        </div>
        <div className="field">
          <label>点击量 *</label>
          <input type="number" min="0" value={form.clicks} onChange={(event) => setField('clicks', event.target.value)} />
        </div>
        <div className="field">
          <label>互动量</label>
          <input
            type="number"
            min="0"
            value={form.interactions}
            onChange={(event) => setField('interactions', event.target.value)}
          />
        </div>
        <div className="field">
          <label>转化量</label>
          <input
            type="number"
            min="0"
            value={form.conversions}
            onChange={(event) => setField('conversions', event.target.value)}
          />
        </div>
      </div>

      <div className="feedback-check">
        <div className="feedback-check-head">
          <strong>回填自检</strong>
          <span className="muted small">按当前输入实时换算；提交后由 A10 用沙箱重新核算</span>
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
          <Chip tone={draftCtr === null ? 'tone-idle' : 'tone-info'}>CTR {pctText(draftCtr)}</Chip>
          <Chip tone={draftEngagement === null ? 'tone-idle' : 'tone-idle'}>
            互动率 {pctText(draftEngagement)}
          </Chip>
          <Chip tone={draftConversion === null ? 'tone-idle' : 'tone-idle'}>
            转化率 {pctText(draftConversion)}
          </Chip>
          {baseline ? (
            <Chip
              tone={ctrVerdict?.tone ?? 'tone-idle'}
              title="发布前预估的 CTR 区间，仅用于对照、不构成承诺"
            >
              预估 CTR {baselineRange || '未给出区间'}（v{baseline.version}）
              {ctrVerdict ? ` · ${ctrVerdict.label}` : ''}
            </Chip>
          ) : (
            <Chip tone="tone-idle">没有发布前预估基线，复盘只做绝对表现解读</Chip>
          )}
        </div>
        {missingCore ? (
          <div className="muted small" style={{ marginTop: 6 }}>
            曝光量与点击量为必填；补齐后显示 CTR 并与预估区间对照。
          </div>
        ) : null}
        {inputWarning ? <div className="feedback-check-warn">{inputWarning}</div> : null}
      </div>

      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void submitFeedback()}>
          {working === '__feedback__' ? <Spinner /> : null} 回填并复盘
        </button>
      </div>
    </div>
  );
}
