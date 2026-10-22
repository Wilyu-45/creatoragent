import { useState } from 'react';
import type { AssetKind, Brief, BriefAsset, Priority } from '../lib/types.ts';
import { createEmptyBrief } from '../lib/types.ts';
import { PRIORITY_LABEL } from '../lib/format.ts';
import { Spinner } from './ui.tsx';

const CHANNELS = ['小红书', '抖音', '公众号', '知乎', '电商详情页', '官网', 'PR'];
const OBJECTIVES = ['曝光', '互动', '转化', '教育', '信任'];
const LANGUAGES: [string, string][] = [
  ['zh', '中文'],
  ['en', 'English'],
  ['ja', '日本語'],
  ['ko', '한국어'],
  ['es', 'Español'],
];

function fromLines(value: string): string[] {
  return value
    .split(/[\n,，、;；]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

/** 素材行 → BriefAsset：支持「地址」或「标题=地址」两种写法，形态按扩展名推断。 */
function toAssets(value: string): BriefAsset[] {
  const KIND_BY_EXT: Record<string, AssetKind> = {
    png: 'image', jpg: 'image', jpeg: 'image', webp: 'image', gif: 'image', bmp: 'image', svg: 'image',
    mp4: 'video', mov: 'video', webm: 'video',
    pdf: 'document', md: 'document', docx: 'document', txt: 'document',
  };
  return value
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [maybeTitle, maybeRef] = line.includes('=') ? line.split('=') : ['', line];
      const ref = (maybeRef ?? '').trim();
      const ext = ref.split('?')[0].split('.').pop()?.toLowerCase() ?? '';
      if (ref.startsWith('data:')) return { kind: 'image' as AssetKind, ref };
      return {
        kind: KIND_BY_EXT[ext] ?? 'link',
        ref,
        ...(maybeTitle.trim() ? { title: maybeTitle.trim() } : {}),
      };
    });
}

function assetLines(assets: BriefAsset[] | undefined): string {
  return (assets ?? [])
    .map((asset) => (asset.title ? `${asset.title}=${asset.ref}` : asset.ref))
    .join('\n');
}

export function BriefForm({
  industries,
  submitting,
  initial,
  onClose,
  onSubmit,
}: {
  industries: string[];
  submitting: boolean;
  /** 传入现有任务 Brief 时作为「复制新建」的起点；不传则是空白新建。 */
  initial?: Brief;
  onClose: () => void;
  onSubmit: (brief: Partial<Brief>, autoApprove: boolean) => void;
}) {
  const [form, setForm] = useState<Brief>(() => ({ ...createEmptyBrief(), ...initial }));
  const [keywords, setKeywords] = useState(() =>
    initial ? initial.keywords.join(', ') : '冷萃咖啡, 办公室, 提神',
  );
  const [constraints, setConstraints] = useState(() =>
    initial ? initial.constraints.join('\n') : '不使用绝对化用语\n不承诺功效',
  );
  const [deliverables, setDeliverables] = useState(() =>
    initial ? initial.deliverables.join('\n') : '图文笔记 1 篇\n标题备选 5 条',
  );
  const [assets, setAssets] = useState(() => (initial ? assetLines(initial.assets) : ''));
  const [autoApprove, setAutoApprove] = useState(false);

  const patch = (key: keyof Brief, value: string): void => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const submit = (): void => {
    onSubmit(
      {
        ...form,
        deadline: form.deadline?.trim() ? form.deadline : null,
        keywords: fromLines(keywords),
        constraints: fromLines(constraints),
        deliverables: fromLines(deliverables),
        assets: toAssets(assets),
      },
      autoApprove,
    );
  };

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(event) => event.stopPropagation()}>
        <h2>{initial ? '以现有 Brief 新建任务' : '新建创作任务'}</h2>
        <div className="muted small">
          {initial
            ? `以「${initial.brand} · ${initial.product}」的 Brief 为起点创建全新任务，提交不会修改原任务；确认或调整内容后即可启动流水线。`
            : '提交后 A0 总控会拆解任务卡，编排「策略 → 创意 → 策划 → 文案 → 审校 → 核查 → 合规 → 审批」流水线。'}
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
            <input
              value={form.objective}
              onChange={(e) => patch('objective', e.target.value)}
              list="objective-options"
              placeholder="例如：曝光"
            />
            <datalist id="objective-options">
              {OBJECTIVES.map((item) => (
                <option key={item} value={item} />
              ))}
            </datalist>
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
            <label>目标语言</label>
            <select value={form.language} onChange={(e) => patch('language', e.target.value)}>
              {LANGUAGES.map(([code, label]) => (
                <option key={code} value={code}>
                  {label}
                </option>
              ))}
            </select>
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
          <div className="field">
            <label>截止时间（可选）</label>
            <input
              type="date"
              value={form.deadline ?? ''}
              onChange={(e) => patch('deadline', e.target.value)}
            />
            <div className="muted small">过期后会在任务列表与页头标红提示。</div>
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
          <label>参考素材（逐行；支持「标题=地址」，本地图片放 data/assets/ 后写文件名）</label>
          <textarea
            value={assets}
            onChange={(e) => setAssets(e.target.value)}
            placeholder={'产品正面图=product.png\nhttps://cdn.example.com/competitor.jpg'}
          />
          <div className="muted small">
            素材清单对所有智能体可见（标题、画幅、用途）；多模态读图需模型支持并在
            .env 里打开 LLM_VISION。
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
            {submitting ? <Spinner /> : null} {initial ? '创建新任务' : '提交并启动流水线'}
          </button>
        </div>
      </div>
    </div>
  );
}
