/** Brief 档案：把创建任务时输入的目标、约束与交付要求完整摊开，避免只记在创建弹窗里。 */
import type { ReactNode } from 'react';
import type { Brief, BriefAsset, TaskRecord } from '../lib/types.ts';
import { formatDeadline, parseDeadline, PRIORITY_LABEL } from '../lib/format.ts';
import { Chip } from './ui.tsx';

const LANGUAGE_LABEL: Record<string, string> = {
  zh: '中文',
  en: '英文',
  ja: '日文',
  ko: '韩文',
  fr: '法文',
  de: '德文',
  es: '西班牙文',
};

const ASSET_LABEL: Record<BriefAsset['kind'], string> = {
  image: '图片',
  video: '视频',
  document: '文档',
  link: '链接',
};

function BriefBlock({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section className="brief-block">
      <h4>
        {title}
        {typeof count === 'number' ? <span>{count}</span> : null}
      </h4>
      {children}
    </section>
  );
}

function BriefList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="brief-empty">{empty}</p>;
  return (
    <ul className="brief-list">
      {items.map((item, index) => (
        <li key={`${index}-${item.slice(0, 12)}`}>{item}</li>
      ))}
    </ul>
  );
}

export function TaskBrief({ brief, status }: { brief: Brief; status: TaskRecord['status'] }) {
  const deadline = parseDeadline(brief.deadline);
  const overdue = deadline !== null && deadline < Date.now() && status !== 'completed';
  const assets = brief.assets ?? [];
  const fields: [string, string][] = [
    ['品牌 / 产品', `${brief.brand} · ${brief.product}`],
    ['传播目标', brief.objective],
    ['目标受众', brief.audience],
    ['主渠道', brief.channel],
    ['行业', brief.industry],
    ['内容调性', brief.tone],
    ['语种', LANGUAGE_LABEL[brief.language] ?? brief.language],
    ['优先级', PRIORITY_LABEL[brief.priority]],
    ['截止时间', brief.deadline ? `${formatDeadline(brief.deadline)}${overdue ? '（已过期）' : ''}` : '未设定'],
  ];

  return (
    <div className="panel task-brief">
      <div className="panel-title">
        任务档案 <span className="count">· Brief 与交付要求</span>
      </div>
      <dl className="brief-grid">
        {fields.map(([label, value]) => (
          <div className="brief-field" key={label}>
            <dt>{label}</dt>
            <dd className={value === '未设定' ? 'muted' : undefined}>{value || '未填写'}</dd>
          </div>
        ))}
      </dl>

      <div className="brief-columns">
        <BriefBlock title="关键词" count={brief.keywords.length}>
          {brief.keywords.length ? (
            <div className="brief-keywords">
              {brief.keywords.map((keyword) => (
                <Chip key={keyword} tone="tone-idle">
                  {keyword}
                </Chip>
              ))}
            </div>
          ) : (
            <p className="brief-empty">未指定关键词</p>
          )}
        </BriefBlock>
        <BriefBlock title="硬性约束" count={brief.constraints.length}>
          <BriefList items={brief.constraints} empty="未填写硬性约束" />
        </BriefBlock>
        <BriefBlock title="期望交付物" count={brief.deliverables.length}>
          <BriefList items={brief.deliverables} empty="未填写期望交付物" />
        </BriefBlock>
      </div>

      {brief.notes ? (
        <div className="brief-note">
          <b>附加说明</b>
          <p>{brief.notes}</p>
        </div>
      ) : null}

      <div className="brief-assets">
        <b>参考素材 · {assets.length} 件</b>
        {assets.length ? (
          <div className="brief-asset-list">
            {assets.map((asset, index) => (
              <span className="brief-asset" key={`${index}-${asset.ref}`} title={asset.ref}>
                <i>{ASSET_LABEL[asset.kind] ?? asset.kind}</i>
                {asset.title || asset.ref}
              </span>
            ))}
          </div>
        ) : (
          <span className="brief-empty">未附参考素材</span>
        )}
      </div>
    </div>
  );
}
