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

/**
 * 发布与效果回填面板。
 *
 * 审批通过后编排层会自动生成 `publish_plan`（渠道 × 时段排期）；本面板让运营：
 * 1. 把实际投放出去的渠道登记为「已发布」（形成效果对齐的基线）；
 * 2. 回填曝光 / 点击 / 互动 / 转化数据，触发 A10 从「发布前预估」切换到「发布后复盘」。
 *
 * 系统不代运营点发布按钮（各平台开放接口差异大且需授权），只负责记录事实与闭环复盘。
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

      <Table head={['序号', '渠道', '建议时段', '状态', '投放标题', '操作']}>
        {schedule.map((item, index) => (
          <tr key={`${item.channel}-${index}`}>
            <td className="mono">{item.order}</td>
            <td>
              <strong>{item.channel}</strong>
            </td>
            <td className="mono">{item.slot || '—'}</td>
            <td>
              <Chip tone={item.status === 'published' ? 'tone-ok' : 'tone-warn'}>
                {item.status === 'published' ? '已发布' : '待发布'}
              </Chip>
            </td>
            <td>{item.title || '—'}</td>
            <td>
              {item.status === 'published' ? (
                <span className="muted small">{formatDateTime(item.published_at)}</span>
              ) : (
                <button
                  className="btn btn-sm"
                  disabled={working !== ''}
                  onClick={() => void markPublished(item.channel)}
                >
                  {working === item.channel ? <Spinner /> : null} 登记发布
                </button>
              )}
            </td>
          </tr>
        ))}
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
        <button
          className="btn btn-sm btn-primary"
          disabled={working !== '' || pending.length === 0}
          onClick={() => void markPublished('')}
        >
          {working === '__all__' ? <Spinner /> : null} 全部登记发布
        </button>
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
