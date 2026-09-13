import { useState } from 'react';
import type { Brief, Priority } from '../lib/types.ts';
import { createEmptyBrief } from '../lib/types.ts';
import { Spinner } from './ui.tsx';

const CHANNELS = ['小红书', '抖音', '公众号', '知乎', '电商详情页', '官网', 'PR'];
const OBJECTIVES = ['曝光', '互动', '转化', '教育', '信任'];
const PRIORITY_LABEL: Record<Priority, string> = { low: '低', normal: '普通', high: '高', urgent: '紧急' };

function fromLines(value: string): string[] {
  return value
    .split(/[\n,，、;；]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function BriefForm({
  industries,
  submitting,
  onClose,
  onSubmit,
}: {
  industries: string[];
  submitting: boolean;
  onClose: () => void;
  onSubmit: (brief: Partial<Brief>, autoApprove: boolean) => void;
}) {
  const [form, setForm] = useState<Brief>(() => createEmptyBrief());
  const [keywords, setKeywords] = useState('冷萃咖啡, 办公室, 提神');
  const [constraints, setConstraints] = useState('不使用绝对化用语\n不承诺功效');
  const [deliverables, setDeliverables] = useState('图文笔记 1 篇\n标题备选 5 条');
  const [autoApprove, setAutoApprove] = useState(false);

  const patch = (key: keyof Brief, value: string): void => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const submit = (): void => {
    onSubmit(
      {
        ...form,
        keywords: fromLines(keywords),
        constraints: fromLines(constraints),
        deliverables: fromLines(deliverables),
      },
      autoApprove,
    );
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h2>新建创作任务</h2>
        <div className="muted small">
          提交后 A0 总控会拆解任务卡，编排「策略 → 创意 → 策划 → 文案 → 审校 → 核查 → 合规 → 审批」流水线。
        </div>

        <div className="form-grid">
          <div className="field">
            <label>品牌名</label>
            <input value={form.brand} onChange={(e) => patch('brand', e.target.value)} placeholder="例如：三顿半" />
          </div>
          <div className="field">
            <label>产品 / 服务</label>
            <input
              value={form.product}
              onChange={(e) => patch('product', e.target.value)}
              placeholder="例如：超即溶冷萃咖啡"
            />
          </div>
          <div className="field">
            <label>传播目标</label>
            <select value={form.objective} onChange={(e) => patch('objective', e.target.value)}>
              {OBJECTIVES.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>目标受众</label>
            <input
              value={form.audience}
              onChange={(e) => patch('audience', e.target.value)}
              placeholder="例如：一线城市 25-35 岁通勤白领"
            />
          </div>
          <div className="field">
            <label>主渠道</label>
            <select value={form.channel} onChange={(e) => patch('channel', e.target.value)}>
              {CHANNELS.map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>行业</label>
            <input
              value={form.industry}
              onChange={(e) => patch('industry', e.target.value)}
              list="industry-options"
              placeholder="例如：食品饮料"
            />
            <datalist id="industry-options">
              {industries.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
          </div>
          <div className="field">
            <label>内容调性</label>
            <input value={form.tone} onChange={(e) => patch('tone', e.target.value)} />
          </div>
          <div className="field">
            <label>优先级</label>
            <select value={form.priority} onChange={(e) => patch('priority', e.target.value)}>
              {(Object.keys(PRIORITY_LABEL) as Priority[]).map((item) => (
                <option key={item} value={item}>
                  {PRIORITY_LABEL[item]}
                </option>
              ))}
            </select>
          </div>
        </div>

        <div className="form-grid">
          <div className="field">
            <label>关键词（逗号或换行分隔）</label>
            <textarea value={keywords} onChange={(e) => setKeywords(e.target.value)} />
          </div>
          <div className="field">
            <label>硬性约束（逐行）</label>
            <textarea value={constraints} onChange={(e) => setConstraints(e.target.value)} />
          </div>
          <div className="field">
            <label>期望交付物（逐行）</label>
            <textarea value={deliverables} onChange={(e) => setDeliverables(e.target.value)} />
          </div>
        </div>

        <div className="field" style={{ marginTop: 12 }}>
          <label>附加说明</label>
          <textarea value={form.notes} onChange={(e) => patch('notes', e.target.value)} placeholder="任何补充背景、禁用表达、必含信息…" />
        </div>

        <label className="row small" style={{ marginTop: 12, cursor: 'pointer' }}>
          <input
            type="checkbox"
            checked={autoApprove}
            onChange={(e) => setAutoApprove(e.target.checked)}
            style={{ width: 'auto' }}
          />
          自动审批（跳过人工裁决，用于演示/压测）
        </label>

        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
          <button className="btn btn-ghost" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={submitting || !form.brand || !form.product}>
            {submitting ? <Spinner /> : null} 提交并启动流水线
          </button>
        </div>
      </div>
    </div>
  );
}
