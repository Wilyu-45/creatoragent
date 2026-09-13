import { useCallback, useEffect, useState } from 'react';
import type { PublishScheduleView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
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

/** 把输入框文本转成正数；空串或非法值返回 null。 */
function toNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
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
}: {
  task: TaskRecord;
  onChanged: () => void;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [plan, setPlan] = useState<PublishScheduleView | null>(null);
  const [working, setWorking] = useState('');
  const [form, setForm] = useState<FeedbackForm>(EMPTY_FORM);

  const hasPlan = task.artifacts.some((artifact) => artifact.type === 'publish_plan');

  const load = useCallback(async () => {
    if (!hasPlan) {
      setPlan(null);
      return;
    }
    try {
      setPlan(await api.publishSchedule(task.id));
    } catch {
      /* 排期读取失败不阻塞主流程 */
    }
  }, [hasPlan, task.id]);

  useEffect(() => {
    setForm(EMPTY_FORM);
    setPlan(null);
    void load();
  }, [load, task.id]);

  if (!hasPlan) return null;

  const schedule = plan?.schedule ?? [];
  const pending = schedule.filter((item) => item.status !== 'published');
  const publishedCount = schedule.length - pending.length;
  const hasFeedback = Boolean(task.feedback_actuals);

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

  /** 立即投递：走发布 webhook（未配置 webhook 时后端退化为登记发布）。 */
  const dispatchPublish = async (channel: string) => {
    setWorking(`d:${channel || '__all__'}`);
    try {
      const result = await api.dispatchPublish(task.id, channel, true);
      const parts: string[] = [];
      if (result.dispatched.length) parts.push(`已投递 ${result.dispatched.join('、')}`);
      if (result.failed.length) parts.push(`失败 ${result.failed.join('、')}`);
      onToast(parts.length ? `发布投递：${parts.join('｜')}` : '没有需要投递的渠道', result.failed.length > 0);
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
    const exposure = toNumber(form.exposure);
    const clicks = toNumber(form.clicks);
    const interactions = toNumber(form.interactions);
    const conversions = toNumber(form.conversions);
    if (exposure !== null) metrics.exposure = exposure;
    if (clicks !== null) metrics.clicks = clicks;
    if (interactions !== null) metrics.interactions = interactions;
    if (conversions !== null) metrics.conversions = conversions;

    if (!Object.keys(metrics).length) {
      onToast('请至少填写曝光量与点击量', true);
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
      setForm((prev) => ({ ...EMPTY_FORM, channel: prev.channel }));
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
          {plan?.published_at ? ` · 排期生成于 ${formatDateTime(plan.published_at)}` : ''}
        </span>
      </div>

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
                </Chip>
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
        <span className="row" style={{ gap: 8 }}>
          <button
            className="btn btn-sm"
            disabled={working !== '' || pending.length === 0}
            onClick={() => void markPublished('')}
          >
            {working === '__all__' ? <Spinner /> : null} 全部登记发布
          </button>
          <button
            className="btn btn-sm btn-primary"
            disabled={working !== '' || pending.length === 0}
            onClick={() => void dispatchPublish('')}
          >
            {working === 'd:__all__' ? <Spinner /> : null} 全部投递
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
      <div className="row" style={{ justifyContent: 'flex-end', marginTop: 12 }}>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void submitFeedback()}>
          {working === '__feedback__' ? <Spinner /> : null} 回填并复盘
        </button>
      </div>
    </div>
  );
}
