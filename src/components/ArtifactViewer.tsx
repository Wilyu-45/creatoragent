import { useMemo, useState } from 'react';
import type { ReactElement } from 'react';
import type { ArtifactType, TaskRecord } from '../lib/types.ts';
import { diffLines, formatTime, gateTone, scoreTone, severityTone } from '../lib/format.ts';
import { asNumber, asRecord, asRecordArray, asText, asTextArray } from '../lib/value.ts';
import { Bullets, Card, Chip, Empty, Kv, Section, Table } from './ui.tsx';

const TYPE_LABEL: Record<ArtifactType, string> = {
  task_plan: '任务卡与流程编排',
  strategy_brief: '策略洞察简报',
  creative_concept: '创意概念',
  content_plan: '内容策划案',
  copy_draft: '文案草稿',
  edited_copy: '编辑审校稿',
  fact_check_report: '事实核查报告',
  compliance_report: '品牌合规报告',
  visual_brief: '视觉美术指导',
  video_script: '视频脚本',
  channel_adaptation: '渠道适配与发布策略',
  publish_plan: '多平台发布排期',
  effect_report: '效果预估报告',
  knowledge_card: '知识沉淀卡片',
  final_delivery: '最终交付物',
};

/** 知识卡片类型 → 展示标签与色调。 */
const CARD_LABEL: Record<string, string> = {
  brand: '品牌',
  case: '案例',
  template: '模板',
  lesson: '经验',
};
const CARD_TONE: Record<string, string> = {
  brand: 'tone-info',
  case: 'tone-ok',
  template: 'tone-warn',
  lesson: 'tone-bad',
};

const FACT_STATUS: Record<string, { label: string; tone: string }> = {
  verified: { label: '已验证', tone: 'tone-ok' },
  unverified: { label: '无来源', tone: 'tone-bad' },
  exaggerated: { label: '夸大', tone: 'tone-bad' },
  contradicted: { label: '矛盾', tone: 'tone-bad' },
  expired: { label: '过期', tone: 'tone-warn' },
};

const VERDICT_LABEL: Record<string, string> = { pass: '通过', revise: '返工', reject: '否决' };
const RISK_LABEL: Record<string, string> = { low: '低', medium: '中', high: '高' };
const RISK_TONE: Record<string, string> = { low: 'tone-ok', medium: 'tone-warn', high: 'tone-bad' };
const IMPACT_TONE: Record<string, string> = { high: 'tone-warn', medium: 'tone-info', low: 'tone-idle' };

function ScoreBars({ scores }: { scores: Record<string, unknown> }) {
  const dims: [string, string][] = [
    ['structure', '结构'],
    ['clarity', '表达'],
    ['brand_voice', '品牌语气'],
    ['appeal', '吸引力'],
  ];
  return (
    <div className="score-row">
      {dims.map(([key, label]) => {
        const value = Math.round(asNumber(scores[key]));
        return (
          <div className="score-cell" key={key}>
            <div className="score-label">
              <span>{label}</span>
              <span>{value || '—'}</span>
            </div>
            <div className="score-bar">
              <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

function CopyBlock({ title, body, cta, hashtags }: { title: string; body: string; cta: string; hashtags: string[] }) {
  return (
    <div>
      {title ? <div className="card-title" style={{ fontSize: 15 }}>{title}</div> : null}
      <div className="copy-view" style={{ marginTop: 8 }}>
        {body}
      </div>
      {cta ? <div className="muted small" style={{ marginTop: 8 }}>行动引导：{cta}</div> : null}
      {hashtags.length ? (
        <div className="hashtag-row">
          {hashtags.map((tag) => (
            <Chip key={tag} mono>
              {tag}
            </Chip>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 各产物类型的结构化渲染                                              */
/* ------------------------------------------------------------------ */

function TaskPlanView({ content }: { content: Record<string, unknown> }) {
  return (
    <>
      <Section title="任务概要">
        <Kv
          pairs={[
            ['目标', asText(content.goal)],
            ['受众', asText(content.audience)],
            ['渠道', asText(content.channel)],
            ['Turn Budget', asText(content.turn_budget)],
            ['最大返工', asText(content.max_revisions)],
          ]}
        />
      </Section>
      <Section title="阶段编排">
        <Table head={['智能体', '阶段', '标签', '调度']}>
          {asRecordArray(content.stages).map((stage, index) => (
            <tr key={index}>
              <td>
                <strong>{asText(stage.agent)}</strong>
              </td>
              <td>{asText(stage.phase)}</td>
              <td>{asText(stage.label)}</td>
              <td>{asText(stage.parallel_group) === 'REVIEW' ? '审核并行' : '顺序执行'}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="门禁规则">
        <Bullets items={asRecordArray(content.gates).map((gate) => `${asText(gate.after)}：${asText(gate.rule)}`)} />
      </Section>
      <Section title="硬性约束">
        <Bullets items={asTextArray(content.constraints)} />
      </Section>
      <Section title="交付物清单">
        <Bullets items={asTextArray(content.deliverables)} />
      </Section>
    </>
  );
}

function StrategyView({ content }: { content: Record<string, unknown> }) {
  const audience = asRecord(content.audience_profile);
  const house = asRecord(content.message_house);
  const tone = asRecord(content.tone_guide);
  return (
    <>
      <Section title="受众画像">
        <Card>
          <Kv pairs={[['人群', asText(audience.segment)], ['规模提示', asText(audience.size_hint)]]} />
          <div style={{ marginTop: 8 }}>
            <strong className="small">核心痛点</strong>
            <Bullets items={asTextArray(audience.pain_points)} />
            <strong className="small">典型场景</strong>
            <Bullets items={asTextArray(audience.scenarios)} />
            <strong className="small">动机</strong>
            <Bullets items={asTextArray(audience.motivations)} />
            <strong className="small">顾虑</strong>
            <Bullets items={asTextArray(audience.objections)} />
          </div>
        </Card>
      </Section>
      <Section title="核心信息屋">
        <div className="copy-view">{asText(house.proposition)}</div>
        <div style={{ marginTop: 10 }}>
          <strong className="small">支撑点</strong>
          <Bullets items={asTextArray(house.support_points)} />
          <strong className="small">用户收益</strong>
          <Bullets items={asTextArray(house.benefits)} />
        </div>
        <Section title="证据素材">
          <Table head={['类型', '状态', '说明']}>
            {asRecordArray(house.evidence).map((item, index) => (
              <tr key={index}>
                <td>{asText(item.type)}</td>
                <td>
                  <Chip tone={asText(item.status).includes('需') ? 'tone-warn' : 'tone-idle'}>{asText(item.status)}</Chip>
                </td>
                <td>{asText(item.note)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      </Section>
      <Section title="传播目标">
        <Table head={['类型', '指标', '目标']}>
          {asRecordArray(content.objectives).map((item, index) => (
            <tr key={index}>
              <td>{asText(item.type)}</td>
              <td>{asText(item.metric)}</td>
              <td>{asText(item.target)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="渠道优先级">
        <Table head={['渠道', '权重', '理由']}>
          {asRecordArray(content.channel_priority).map((item, index) => (
            <tr key={index}>
              <td>
                <strong>{asText(item.channel)}</strong>
              </td>
              <td>{Math.round(asNumber(item.weight) * 100)}%</td>
              <td>{asText(item.why)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="调性规范">
        <div className="hashtag-row">
          {asTextArray(tone.keywords).map((word) => (
            <Chip key={word} tone="tone-info">
              {word}
            </Chip>
          ))}
        </div>
        <div style={{ marginTop: 8 }}>
          <strong className="small">需规避</strong>
          <Bullets items={asTextArray(tone.avoid)} />
        </div>
      </Section>
      <Section title="关键结论">
        <Bullets items={asTextArray(content.key_takeaways)} />
      </Section>
      {asTextArray(content.risks).length ? (
        <Section title="风险提示">
          <Bullets items={asTextArray(content.risks)} />
        </Section>
      ) : null}
    </>
  );
}

function CreativeView({ content }: { content: Record<string, unknown> }) {
  const bigIdea = asRecord(content.big_idea);
  const tone = asRecord(content.tone_guide);
  const recommended = asText(content.recommended_direction);
  return (
    <>
      <Section title="Big Idea">
        <Card title={asText(bigIdea.title)}>
          <div className="copy-view" style={{ padding: '10px 12px' }}>
            {asText(bigIdea.statement)}
          </div>
          <div className="muted small" style={{ marginTop: 8 }}>
            依据：{asText(bigIdea.rationale)}
          </div>
        </Card>
      </Section>
      <Section title="创意方向">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {asRecordArray(content.directions).map((dir, index) => {
            const isRecommended = asText(dir.id) === recommended;
            return (
              <Card
                key={index}
                title={`${asText(dir.id)} · ${asText(dir.name)}`}
                extra={
                  <span className="row" style={{ gap: 6 }}>
                    {isRecommended ? <Chip tone="tone-ok">推荐</Chip> : null}
                    <Chip tone={scoreTone(asNumber(dir.fit_score))}>{Math.round(asNumber(dir.fit_score))} 分</Chip>
                  </span>
                }
              >
                <Kv
                  pairs={[
                    ['切入角度', asText(dir.angle)],
                    ['钩子', asText(dir.hook)],
                    ['示例标题', asText(dir.sample_headline)],
                    ['成立理由', asText(dir.rationale)],
                    ['风险', asText(dir.risk)],
                  ]}
                />
              </Card>
            );
          })}
        </div>
      </Section>
      <Section title="调性规范">
        <Kv pairs={[['语调', asText(tone.voice)], ['视觉建议', asText(tone.visual_suggestion)]]} />
        <div style={{ marginTop: 8 }}>
          <strong className="small">应该</strong>
          <Bullets items={asTextArray(tone.dos)} />
          <strong className="small">避免</strong>
          <Bullets items={asTextArray(tone.donts)} />
        </div>
      </Section>
      <Section title="参考案例">
        <Table head={['案例', '借鉴点', '来源']}>
          {asRecordArray(content.reference_cases).map((item, index) => (
            <tr key={index}>
              <td>{asText(item.name)}</td>
              <td>{asText(item.why)}</td>
              <td className="muted">{asText(item.source)}</td>
            </tr>
          ))}
        </Table>
      </Section>
    </>
  );
}

function PlanView({ content }: { content: Record<string, unknown> }) {
  const keywords = asRecord(content.keywords);
  const selected = asText(content.selected_topic);
  return (
    <>
      <Section title="选题池">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {asRecordArray(content.topics).map((topic, index) => (
            <Card
              key={index}
              title={`${asText(topic.id)} · ${asText(topic.title)}`}
              extra={asText(topic.id) === selected ? <Chip tone="tone-ok">选中</Chip> : null}
            >
              <Kv
                pairs={[
                  ['角度', asText(topic.angle)],
                  ['形态', asText(topic.format)],
                  ['预估字数', asText(topic.estimated_words)],
                  ['行动引导', asText(topic.cta)],
                ]}
              />
              <div style={{ marginTop: 8 }}>
                <strong className="small">内容大纲</strong>
                <Bullets items={asTextArray(topic.outline)} ordered />
              </div>
            </Card>
          ))}
        </div>
      </Section>
      <Section title={`标题备选（${asTextArray(content.headline_candidates).length} 条）`}>
        <Bullets items={asTextArray(content.headline_candidates)} ordered />
      </Section>
      <Section title="内容结构">
        <Table head={['段落', '目标', '字数']}>
          {asRecordArray(content.structure).map((row, index) => (
            <tr key={index}>
              <td>
                <strong>{asText(row.section)}</strong>
              </td>
              <td>{asText(row.goal)}</td>
              <td>{asText(row.words)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="渠道适配与发布节奏">
        <Table head={['渠道', '形态', '说明']}>
          {asRecordArray(content.channel_adaptation).map((row, index) => (
            <tr key={index}>
              <td>{asText(row.channel)}</td>
              <td>{asText(row.format)}</td>
              <td>{asText(row.notes)}</td>
            </tr>
          ))}
        </Table>
        <Table head={['时间点', '动作', '备注']}>
          {asRecordArray(content.publishing_rhythm).map((row, index) => (
            <tr key={index}>
              <td>{asText(row.slot)}</td>
              <td>{asText(row.action)}</td>
              <td className="muted">{asText(row.note)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="关键词布局">
        <Kv
          pairs={[
            [
              '核心词',
              <span className="hashtag-row">
                {asTextArray(keywords.primary).map((word) => (
                  <Chip key={word} mono>
                    {word}
                  </Chip>
                ))}
              </span>,
            ],
            ['长尾词', asTextArray(keywords.long_tail).join('，')],
            ['话题标签', asText(keywords.hashtags)],
          ]}
        />
      </Section>
    </>
  );
}

function CopyView({ content }: { content: Record<string, unknown> }) {
  const recommended = asText(content.recommended_version);
  return (
    <>
      <Section title="多版本文案">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {asRecordArray(content.versions).map((version, index) => (
            <Card
              key={index}
              title={`${asText(version.id)} · ${asText(version.style)}`}
              extra={
                <span className="row" style={{ gap: 6 }}>
                  {asText(version.id) === recommended ? <Chip tone="tone-ok">推荐</Chip> : null}
                  <Chip tone="tone-idle">{asText(version.word_count)} 字</Chip>
                </span>
              }
            >
              <CopyBlock
                title=""
                body={asText(version.body)}
                cta={asText(version.cta)}
                hashtags={asTextArray(version.hashtags)}
              />
            </Card>
          ))}
        </div>
      </Section>
      <Section title="主张与来源">
        <Table head={['主张', '来源']}>
          {asRecordArray(content.claims).map((claim, index) => (
            <tr key={index}>
              <td>{asText(claim.text)}</td>
              <td>
                {asText(claim.source) ? (
                  asText(claim.source)
                ) : (
                  <Chip tone="tone-bad">未标注</Chip>
                )}
              </td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="渠道要素清单">
        <div className="hashtag-row">
          {asRecordArray(content.channel_checklist).map((item, index) => (
            <Chip key={index} tone={item.covered ? 'tone-ok' : 'tone-warn'}>
              {item.covered ? '✓' : '×'} {asText(item.block)}
            </Chip>
          ))}
        </div>
      </Section>
      {asRecordArray(content.revision_notes).length ? (
        <Section title="返工处理记录">
          <Bullets
            items={asRecordArray(content.revision_notes).map(
              (note) => `${asText(note.from_feedback)} → ${asText(note.action)}`,
            )}
          />
        </Section>
      ) : null}
    </>
  );
}

function EditedView({ content }: { content: Record<string, unknown> }) {
  const revised = asRecord(content.revised);
  const scorecard = asRecord(content.scorecard);
  const metrics = asRecord(content.read_metrics);
  return (
    <>
      <Section title="审校评分">
        <ScoreBars scores={scorecard} />
      </Section>
      <Section title="修订稿">
        <CopyBlock
          title={asText(revised.title)}
          body={asText(revised.body)}
          cta={asText(revised.cta)}
          hashtags={asTextArray(revised.hashtags)}
        />
      </Section>
      {asRecordArray(content.change_log).length ? (
        <Section title="修改记录">
          <Table head={['类型', '说明', '修改前', '修改后']}>
            {asRecordArray(content.change_log).map((row, index) => (
              <tr key={index}>
                <td>{asText(row.type)}</td>
                <td>{asText(row.detail)}</td>
                <td className="muted">{asText(row.before)}</td>
                <td>{asText(row.after)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      {asRecordArray(content.issues).length ? (
        <Section title="问题清单">
          <Table head={['级别', '类别', '问题', '建议']}>
            {asRecordArray(content.issues).map((issue, index) => (
              <tr key={index}>
                <td>
                  <Chip tone={severityTone(asText(issue.severity))}>{asText(issue.severity)}</Chip>
                </td>
                <td>{asText(issue.category)}</td>
                <td>{asText(issue.detail)}</td>
                <td>{asText(issue.suggestion)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      <Section title="阅读指标">
        <Kv
          pairs={[
            ['总字数', asText(metrics.char_count)],
            ['段落数', asText(metrics.paragraph_count)],
            ['平均句长', asText(metrics.avg_sentence_length)],
          ]}
        />
      </Section>
    </>
  );
}

function FactCheckView({ content }: { content: Record<string, unknown> }) {
  const verdict = asText(asRecord(content).verdict);
  return (
    <>
      <Section title="核查结论">
        <div className="row">
          <Chip tone={gateTone(verdict)}>{VERDICT_LABEL[verdict] ?? verdict}</Chip>
          <Chip tone={RISK_TONE[asText(content.risk_level)] ?? 'tone-idle'}>
            风险 {RISK_LABEL[asText(content.risk_level)] ?? asText(content.risk_level)}
          </Chip>
        </div>
        <div className="muted small" style={{ marginTop: 8 }}>
          {asText(content.verdict_reason)}
        </div>
        <div style={{ marginTop: 8 }}>
          <strong className="small">核查范围</strong>
          <Bullets items={asTextArray(content.verification_scope)} />
        </div>
      </Section>
      <Section title="核查项">
        <Table head={['主张', '状态', '来源', '说明']}>
          {asRecordArray(content.checks).map((check, index) => {
            const status = asText(check.status);
            const meta = FACT_STATUS[status] ?? { label: status, tone: 'tone-idle' };
            return (
              <tr key={index}>
                <td>{asText(check.claim)}</td>
                <td>
                  <Chip tone={meta.tone}>{meta.label}</Chip>
                </td>
                <td className="muted">{asText(check.source)}</td>
                <td>{asText(check.note)}</td>
              </tr>
            );
          })}
        </Table>
      </Section>
      {asTextArray(content.required_fixes).length ? (
        <Section title="必改项">
          <Bullets items={asTextArray(content.required_fixes)} />
        </Section>
      ) : null}
      {asRecordArray(content.safe_rewrites).length ? (
        <Section title="安全改写建议">
          <Table head={['原文', '改写', '理由']}>
            {asRecordArray(content.safe_rewrites).map((row, index) => (
              <tr key={index}>
                <td>{asText(row.original)}</td>
                <td>{asText(row.rewrite)}</td>
                <td className="muted">{asText(row.reason)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
    </>
  );
}

function ComplianceView({ content }: { content: Record<string, unknown> }) {
  const summary = asRecord(content.summary);
  const brand = asRecord(content.brand_consistency);
  const verdict = asText(content.verdict);
  return (
    <>
      <Section title="合规结论">
        <div className="row">
          <Chip tone={gateTone(verdict)}>{VERDICT_LABEL[verdict] ?? verdict}</Chip>
          <Chip tone={scoreTone(asNumber(content.compliance_score))}>
            合规分 {Math.round(asNumber(content.compliance_score))}
          </Chip>
          <Chip tone={RISK_TONE[asText(content.risk_level)] ?? 'tone-idle'}>
            风险 {RISK_LABEL[asText(content.risk_level)] ?? asText(content.risk_level)}
          </Chip>
          {asNumber(summary.blocker) > 0 ? <Chip tone="tone-bad">阻断 {asText(summary.blocker)}</Chip> : null}
          {asNumber(summary.major) > 0 ? <Chip tone="tone-warn">重要 {asText(summary.major)}</Chip> : null}
          {asNumber(summary.minor) > 0 ? <Chip tone="tone-info">建议 {asText(summary.minor)}</Chip> : null}
          {asNumber(summary.missing_required) > 0 ? (
            <Chip tone="tone-warn">缺必备要素 {asText(summary.missing_required)}</Chip>
          ) : null}
        </div>
        <div className="muted small" style={{ marginTop: 8 }}>
          {asText(content.verdict_reason)}
        </div>
      </Section>
      <Section title="命中项">
        <Table head={['级别', '类别', '词条', '详情', '建议', '依据']}>
          {asRecordArray(content.hits).map((hit, index) => (
            <tr key={index}>
              <td>
                <Chip tone={severityTone(asText(hit.severity))}>{asText(hit.severity)}</Chip>
              </td>
              <td>{asText(hit.category)}</td>
              <td>
                {asText(hit.term) ? (
                  <span className="mono">{asText(hit.term)}</span>
                ) : (
                  <span className="muted">—</span>
                )}
              </td>
              <td>{asText(hit.detail)}</td>
              <td>{asText(hit.suggestion)}</td>
              <td className="law">{asText(hit.law)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="品牌一致性">
        <Kv
          pairs={[
            ['品牌语气分', `${asNumber(brand.score)}/5`],
            ['术语规范', brand.terminology_ok ? '✓ 通过' : '× 需修正'],
            ['风险提示', brand.disclaimer_ok ? '✓ 已包含' : '× 缺失'],
          ]}
        />
        {asRecordArray(brand.issues).length ? (
          <Bullets
            items={asRecordArray(brand.issues).map((issue) => `${asText(issue.type)}：${asText(issue.detail)}`)}
          />
        ) : null}
      </Section>
      {asTextArray(content.required_fixes).length ? (
        <Section title="必改项">
          <Bullets items={asTextArray(content.required_fixes)} />
        </Section>
      ) : null}
      {asRecordArray(content.safe_rewrites).length ? (
        <Section title="自动改写建议">
          <Table head={['原文', '改写']}>
            {asRecordArray(content.safe_rewrites).map((row, index) => (
              <tr key={index}>
                <td>{asText(row.original)}</td>
                <td>{asText(row.rewrite)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      <Section title="审查依据">
        <Bullets items={asTextArray(content.checked_against)} />
      </Section>
    </>
  );
}

function EffectView({ content }: { content: Record<string, unknown> }) {
  const predicted = asRecord(content.predicted);
  const alignment = asRecord(content.objective_alignment);
  const bands: [string, string][] = [
    ['曝光', 'exposure'],
    ['点击率', 'ctr'],
    ['互动率', 'engagement'],
    ['转化率', 'conversion'],
  ];
  return (
    <>
      <Section title="效果预估区间">
        <div className="grid-3">
          {bands.map(([label, key]) => {
            const band = asRecord(predicted[key]);
            return (
              <div className="card" key={key}>
                <div className="muted small">{label}</div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>
                  {Math.round(asNumber(band.mid))}
                  <span className="muted small">{asText(band.unit)}</span>
                </div>
                <div className="muted small">
                  {Math.round(asNumber(band.low))} ~ {Math.round(asNumber(band.high))}
                </div>
              </div>
            );
          })}
        </div>
        <div className="muted small" style={{ marginTop: 8 }}>
          {asText(predicted.basis)}
        </div>
      </Section>
      <Section title="目标匹配度">
        <Kv
          pairs={[
            ['目标', asText(alignment.objective)],
            ['匹配分', `${Math.round(asNumber(alignment.score))}/100`],
            ['说明', asText(alignment.note)],
          ]}
        />
      </Section>
      <Section title="效果归因">
        <Table head={['因子', '影响', '说明']}>
          {asRecordArray(content.attribution).map((row, index) => (
            <tr key={index}>
              <td>{asText(row.factor)}</td>
              <td>
                <Chip tone={IMPACT_TONE[asText(row.impact)] ?? 'tone-idle'}>{asText(row.impact)}</Chip>
              </td>
              <td>{asText(row.note)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="优化建议">
        <Table head={['优先级', '动作', '预期收益', '成本']}>
          {asRecordArray(content.optimizations).map((row, index) => (
            <tr key={index}>
              <td>
                <Chip tone={IMPACT_TONE[asText(row.priority)] ?? 'tone-idle'}>{asText(row.priority)}</Chip>
              </td>
              <td>{asText(row.action)}</td>
              <td>{asText(row.expected_gain)}</td>
              <td className="muted">{asText(row.effort)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="A/B 测试建议">
        <Table head={['假设', '版本 A', '版本 B', '指标']}>
          {asRecordArray(content.ab_tests).map((row, index) => (
            <tr key={index}>
              <td>{asText(row.hypothesis)}</td>
              <td>{asText(row.variant_a)}</td>
              <td>{asText(row.variant_b)}</td>
              <td>{asText(row.metric)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="下一条选题建议">
        <Bullets items={asTextArray(content.next_brief_suggestions)} />
      </Section>
      <Section title="注意事项">
        <Bullets items={asTextArray(content.cautions)} />
      </Section>
    </>
  );
}

function DeliveryView({ content }: { content: Record<string, unknown> }) {
  const review = asRecord(content.review_summary);
  const visual = asRecord(content.visual);
  const channels = asRecordArray(content.channels);
  const cards = asRecordArray(content.knowledge_cards);
  return (
    <>
      <Section title="交付内容">
        <div className="row" style={{ marginBottom: 10 }}>
          <Chip tone="tone-ok">{asText(content.channel)}</Chip>
          <Chip tone="tone-info">{asText(content.brand)}</Chip>
          <Chip tone="tone-idle">{asText(content.product)}</Chip>
          <Chip tone="tone-warn">返工 {asText(content.revision_rounds)} 轮</Chip>
        </div>
        <CopyBlock
          title={asText(content.title)}
          body={asText(content.body)}
          cta={asText(content.cta)}
          hashtags={asTextArray(content.hashtags)}
        />
      </Section>
      <Section title="终审要点">
        <Kv
          pairs={[
            ['编辑审校', asText(review.editor)],
            ['事实核查', asText(review.fact_check)],
            ['品牌合规', asText(review.compliance)],
            ['视觉指导', asText(review.visual) || '—'],
            ['渠道适配', asText(review.channel) || '—'],
            ['知识沉淀', asText(review.memory) || '—'],
            ['审批人', asText(content.approved_by)],
            ['审批意见', asText(content.approval_comment) || '—'],
          ]}
        />
      </Section>
      {channels.length ? (
        <Section title="渠道版本">
          <Table head={['渠道', '投放标题', '关键词', '发布时段']}>
            {channels.map((row, index) => (
              <tr key={index}>
                <td>
                  <strong>{asText(row.channel)}</strong>
                </td>
                <td>{asText(row.title)}</td>
                <td className="muted">{asTextArray(row.keywords).join('，')}</td>
                <td className="muted">{asText(row.publish_slot)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      {asText(visual.style) ? (
        <Section title="视觉方案">
          <Kv
            pairs={[
              ['风格', asText(visual.style)],
              ['情绪', asText(visual.mood)],
              ['封面画幅', asText(visual.cover_ratio)],
              ['配图数', `${asText(visual.image_prompt_count)} 张`],
              ['图文一致性', visual.copy_aligned === false ? '需确认' : '✓ 一致'],
            ]}
          />
          <div className="hashtag-row" style={{ marginTop: 8 }}>
            {asRecordArray(visual.palette).map((color, index) => (
              <Chip key={index} tone="tone-idle">
                {asText(color.name)} {asText(color.hex)}
              </Chip>
            ))}
          </div>
        </Section>
      ) : null}
      {cards.length ? (
        <Section title="沉淀的知识卡片">
          <Bullets
            items={cards.map(
              (card) => `${asText(card.title)}（${CARD_LABEL[asText(card.type)] ?? asText(card.type)}）`,
            )}
          />
        </Section>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* A8 视觉美术指导                                                     */
/* ------------------------------------------------------------------ */

function VisualBriefView({ content }: { content: Record<string, unknown> }) {
  const direction = asRecord(content.visual_direction);
  const assets = asRecord(content.assets);
  const layout = asRecord(content.layout);
  const check = asRecord(content.copy_visual_check);
  const prompts = asRecordArray(content.image_prompts);
  const storyboard = asRecordArray(content.storyboard);
  const palette = asRecordArray(direction.palette);
  return (
    <>
      <Section title="视觉方向">
        <Kv
          pairs={[
            ['风格', asText(direction.style)],
            ['情绪', asText(direction.mood)],
            ['构图', asText(direction.composition)],
            ['光线', asText(direction.lighting)],
            ['依据', asText(direction.rationale)],
          ]}
        />
      </Section>
      <Section title="色彩方案">
        <div className="hashtag-row">
          {palette.map((color, index) => (
            <span key={index} className="chip">
              <span
                style={{
                  display: 'inline-block',
                  width: 10,
                  height: 10,
                  borderRadius: 3,
                  marginRight: 6,
                  background: asText(color.hex),
                  border: '1px solid rgba(0,0,0,.18)',
                }}
              />
              {asText(color.name)} <span className="mono muted">{asText(color.hex)}</span>
              {asText(color.usage) ? <span className="muted"> · {asText(color.usage)}</span> : null}
            </span>
          ))}
        </div>
      </Section>
      <Section title="画幅与张数">
        <Kv
          pairs={[
            ['主画幅', asText(assets.main_ratio)],
            ['封面画幅', asText(assets.cover_ratio)],
            ['配图张数', asText(assets.shot_count)],
          ]}
        />
      </Section>
      <Section title={`配图 Prompt（${prompts.length} 条）`}>
        <Table head={['编号', '用途', '画幅', 'Prompt', '负面词']}>
          {prompts.map((item, index) => (
            <tr key={index}>
              <td className="mono">{asText(item.id)}</td>
              <td>
                <strong>{asText(item.usage)}</strong>
              </td>
              <td className="mono">{asText(item.aspect_ratio)}</td>
              <td className="small">{asText(item.prompt)}</td>
              <td className="muted small">{asText(item.negative)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      {storyboard.length ? (
        <Section title={`分镜脚本（${storyboard.length} 镜）`}>
          <Table head={['镜头', '时长', '画面', '字幕', '转场']}>
            {storyboard.map((shot, index) => (
              <tr key={index}>
                <td>
                  <strong>{asText(shot.shot)}</strong>
                </td>
                <td className="mono">{asText(shot.duration)}</td>
                <td>{asText(shot.visual)}</td>
                <td>{asText(shot.copy_overlay)}</td>
                <td className="muted">{asText(shot.transition)}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      <Section title="版式建议">
        <Kv
          pairs={[
            ['封面', asText(layout.cover)],
            ['内页', asText(layout.body)],
            ['字体层级', asText(layout.typography)],
          ]}
        />
      </Section>
      <Section title="图文一致性校验">
        <div className="row" style={{ marginBottom: 8 }}>
          <Chip tone={check.aligned ? 'tone-ok' : 'tone-bad'}>
            {check.aligned ? '✓ 图文一致' : '× 存在冲突'}
          </Chip>
        </div>
        {asTextArray(check.conflicts).length ? (
          <Bullets items={asTextArray(check.conflicts).map((item) => `冲突：${item}`)} />
        ) : null}
        <Bullets items={asTextArray(check.notes)} />
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* A9 渠道运营与 SEO                                                   */
/* ------------------------------------------------------------------ */

const CHECK_TONE: Record<string, string> = {
  pass: 'tone-ok',
  warn: 'tone-warn',
  fail: 'tone-bad',
};
const CHECK_LABEL: Record<string, string> = {
  pass: '通过',
  warn: '待确认',
  fail: '未满足',
};

function ChannelView({ content }: { content: Record<string, unknown> }) {
  const seo = asRecord(content.seo);
  const platforms = asRecordArray(content.platforms);
  const checklist = asRecordArray(content.channel_checklist);
  return (
    <>
      <Section title={`平台版本（${platforms.length} 个）`}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {platforms.map((item, index) => {
            const length = asNumber(item.title_length);
            const limit = asNumber(item.title_limit);
            const over = limit > 0 && length > limit;
            return (
              <Card
                key={index}
                title={`${asText(item.channel)} · ${asText(item.format)}`}
                extra={
                  <Chip tone={over ? 'tone-warn' : 'tone-ok'}>
                    标题 {length}/{limit} 字
                  </Chip>
                }
              >
                <Kv
                  pairs={[
                    ['投放标题', asText(item.title)],
                    ['搜索变体', asText(item.seo_variant) || '—'],
                    ['原标题', asText(item.title_original) || '—'],
                    ['发布时段', asText(item.publish_slot) || '—'],
                    ['适配说明', asText(item.notes) || '—'],
                  ]}
                />
                <div className="hashtag-row" style={{ marginTop: 8 }}>
                  {asTextArray(item.hashtags).map((tag) => (
                    <Chip key={tag} mono tone="tone-idle">
                      {tag}
                    </Chip>
                  ))}
                </div>
              </Card>
            );
          })}
        </div>
      </Section>
      <Section title="SEO 关键词布局">
        <Kv
          pairs={[
            ['主关键词', asTextArray(seo.primary_keywords).join('，') || '—'],
            ['搜索意图', asText(seo.search_intent)],
            ['竞争难度', asText(seo.difficulty)],
            ['密度建议', asText(seo.density_hint)],
          ]}
        />
        <div className="hashtag-row" style={{ marginTop: 8 }}>
          {asTextArray(seo.long_tail).map((word) => (
            <Chip key={word} tone="tone-info">
              {word}
            </Chip>
          ))}
        </div>
        <Table head={['位置', '关键词', '说明']}>
          {asRecordArray(seo.placement).map((row, index) => (
            <tr key={index}>
              <td>
                <strong>{asText(row.position)}</strong>
              </td>
              <td>{asText(row.keyword)}</td>
              <td className="muted">{asText(row.note)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="发布计划">
        <Table head={['时间点', '动作', '备注']}>
          {asRecordArray(content.publish_plan).map((row, index) => (
            <tr key={index}>
              <td className="mono">{asText(row.slot)}</td>
              <td>
                <strong>{asText(row.action)}</strong>
              </td>
              <td className="muted">{asText(row.note)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="A/B 测试方案">
        <Table head={['假设', '版本 A', '版本 B', '观察指标']}>
          {asRecordArray(content.ab_tests).map((row, index) => (
            <tr key={index}>
              <td>{asText(row.hypothesis)}</td>
              <td>{asText(row.variant_a)}</td>
              <td>{asText(row.variant_b)}</td>
              <td className="muted">{asText(row.metric)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="渠道规范核对">
        <Table head={['规则', '状态', '说明']}>
          {checklist.map((row, index) => {
            const status = asText(row.status);
            return (
              <tr key={index}>
                <td>{asText(row.rule)}</td>
                <td>
                  <Chip tone={CHECK_TONE[status] ?? 'tone-idle'}>
                    {CHECK_LABEL[status] ?? status}
                  </Chip>
                </td>
                <td className="muted">{asText(row.note)}</td>
              </tr>
            );
          })}
        </Table>
      </Section>
    </>
  );
}

/* ------------------------------------------------------------------ */
/* A9 发布排期（审批通过后生成）                                        */
/* ------------------------------------------------------------------ */

const SCHEDULE_STATUS: Record<string, { label: string; tone: string }> = {
  scheduled: { label: '待发布', tone: 'tone-warn' },
  published: { label: '已发布', tone: 'tone-ok' },
};

function PublishPlanView({ content }: { content: Record<string, unknown> }) {
  const schedule = asRecordArray(content.schedule);
  const mode = asText(content.mode);
  return (
    <>
      <Section title="发布概要">
        <div className="row" style={{ marginBottom: 8 }}>
          <Chip tone="tone-info">{asText(content.brand)}</Chip>
          <Chip tone="tone-idle">主渠道 {asText(content.primary_channel)}</Chip>
          <Chip tone="tone-idle">{mode === 'manual' ? '人工投放' : mode || '人工投放'}</Chip>
        </div>
        <Kv
          pairs={[
            ['审批生效时间', asText(content.approved_at)],
            ['最近登记发布', asText(content.last_published_at) || '—'],
            ['说明', asText(content.notes) || '—'],
          ]}
        />
      </Section>
      <Section title={`渠道 × 时段排期（${schedule.length} 条）`}>
        <Table head={['序号', '渠道', '建议时段', '状态', '投放标题', '发布链接']}>
          {schedule.map((row, index) => {
            const status = asText(row.status);
            const meta = SCHEDULE_STATUS[status] ?? { label: status, tone: 'tone-idle' };
            return (
              <tr key={index}>
                <td className="mono">{asText(row.order)}</td>
                <td>
                  <strong>{asText(row.channel)}</strong>
                </td>
                <td className="mono">{asText(row.slot) || '—'}</td>
                <td>
                  <Chip tone={meta.tone}>{meta.label}</Chip>
                </td>
                <td>{asText(row.title) || '—'}</td>
                <td className="muted small">{asText(row.url) || '—'}</td>
              </tr>
            );
          })}
        </Table>
      </Section>
      {schedule.some((row) => asTextArray(row.recommended_slots).length) ? (
        <Section title="各渠道推荐时段">
          <Table head={['渠道', '备选时段']}>
            {schedule.map((row, index) => (
              <tr key={index}>
                <td>{asText(row.channel)}</td>
                <td className="muted small">{asTextArray(row.recommended_slots).join(' / ') || '—'}</td>
              </tr>
            ))}
          </Table>
        </Section>
      ) : null}
      {asTextArray(content.checklist).length ? (
        <Section title="投放前检查清单">
          <Bullets items={asTextArray(content.checklist)} ordered />
        </Section>
      ) : null}
    </>
  );
}

/* ------------------------------------------------------------------ */
/* A11 记忆与知识库                                                    */
/* ------------------------------------------------------------------ */

function KnowledgeView({ content }: { content: Record<string, unknown> }) {
  const archive = asRecord(content.archive);
  const cards = asRecordArray(content.knowledge_cards);
  const gaps = asTextArray(content.gaps);
  return (
    <>
      <Section title={`知识卡片（${cards.length} 张）`}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {cards.map((card, index) => {
            const type = asText(card.type);
            return (
              <Card
                key={index}
                title={asText(card.title)}
                extra={<Chip tone={CARD_TONE[type] ?? 'tone-idle'}>{CARD_LABEL[type] ?? type}</Chip>}
              >
                <div className="small">{asText(card.content)}</div>
                {asText(card.reuse_hint) ? (
                  <div className="muted small" style={{ marginTop: 8 }}>
                    复用建议：{asText(card.reuse_hint)}
                  </div>
                ) : null}
                <div className="hashtag-row" style={{ marginTop: 8 }}>
                  {asTextArray(card.tags).map((tag) => (
                    <Chip key={tag} mono tone="tone-idle">
                      {tag}
                    </Chip>
                  ))}
                </div>
              </Card>
            );
          })}
        </div>
      </Section>
      <Section title="可复用模板">
        <Table head={['模板', '用途', '内容']}>
          {asRecordArray(content.templates).map((row, index) => (
            <tr key={index}>
              <td>
                <strong>{asText(row.name)}</strong>
              </td>
              <td>{asText(row.usage)}</td>
              <td className="muted">{asText(row.body)}</td>
            </tr>
          ))}
        </Table>
      </Section>
      <Section title="归档信息">
        <Kv
          pairs={[
            ['产物总数', asText(archive.artifact_count)],
            ['返工轮次', asText(archive.revision_rounds)],
            ['产物类型', asTextArray(archive.artifact_types).join('、') || '—'],
          ]}
        />
        <div style={{ marginTop: 10 }}>
          <strong className="small">关键决策</strong>
          <Bullets items={asTextArray(archive.key_decisions)} />
        </div>
        <div style={{ marginTop: 10 }}>
          <strong className="small">可复用资产</strong>
          <Bullets items={asTextArray(archive.reusable_assets)} />
        </div>
      </Section>
      <Section title="复用建议">
        <Bullets items={asTextArray(content.reuse_suggestions)} />
      </Section>
      {gaps.length ? (
        <Section title={`知识缺口（${gaps.length} 项）`}>
          <Bullets items={gaps} />
        </Section>
      ) : null}
    </>
  );
}

function GenericView({ content }: { content: Record<string, unknown> }) {
  return <pre className="pre">{JSON.stringify(content, null, 2)}</pre>;
}

const RENDERERS: Partial<Record<ArtifactType, (props: { content: Record<string, unknown> }) => ReactElement>> = {
  task_plan: TaskPlanView,
  strategy_brief: StrategyView,
  creative_concept: CreativeView,
  content_plan: PlanView,
  copy_draft: CopyView,
  edited_copy: EditedView,
  fact_check_report: FactCheckView,
  compliance_report: ComplianceView,
  visual_brief: VisualBriefView,
  video_script: VideoScriptView,
  channel_adaptation: ChannelView,
  publish_plan: PublishPlanView,
  effect_report: EffectView,
  knowledge_card: KnowledgeView,
  final_delivery: DeliveryView,
};

/* ------------------------------------------------------------------ */
/* 视频脚本                                                            */
/* ------------------------------------------------------------------ */

/**
 * 视频脚本视图：按**时间轴**呈现分镜，而不是按字段平铺。
 *
 * 脚本的可用性取决于「时间轴是否连贯、每镜是否都有画面与口播」，
 * 因此这里以表格 + 时间轴为主视图，让「哪一镜缺口播」「时长是否超」一眼可见。
 */
function VideoScriptView({ content }: { content: Record<string, unknown> }) {
  const shots = asRecordArray(content.shots);
  const voiceover = asRecordArray(content.voiceover);
  const subtitles = asRecordArray(content.subtitles);
  const productionNotes = asTextArray(content.production_notes);
  const complianceNotes = asTextArray(content.compliance_notes);
  const duration = asNumber(content.duration_seconds, 0);
  const ratio = asText(content.aspect_ratio);
  const hook = asText(content.hook);

  // 时间轴覆盖率：分镜时长之和 / 目标时长，用来发现「脚本总时长对不上」
  const covered = shots.reduce((sum, shot) => sum + asNumber(shot.duration_seconds, 0), 0);
  const coverage = duration ? Math.round((covered / duration) * 100) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <Chip tone="tone-info" mono>
          {ratio || '9:16'}
        </Chip>
        <Chip tone="tone-idle">目标 {duration || '—'} 秒</Chip>
        <Chip tone="tone-idle">分镜 {shots.length} 镜</Chip>
        <Chip tone="tone-idle">口播 {voiceover.length} 段</Chip>
        <Chip tone="tone-idle">字幕 {subtitles.length} 条</Chip>
        <Chip tone={coverage >= 95 && coverage <= 105 ? 'tone-ok' : 'tone-warn'}>
          时间轴覆盖 {coverage}%
        </Chip>
        {shots.some((shot) => !asText(shot.voiceover)) ? (
          <Chip tone="tone-warn">存在无口播分镜</Chip>
        ) : null}
      </div>

      {hook ? (
        <div className="card">
          <div className="muted small">黄金 3 秒钩子</div>
          <div style={{ marginTop: 4, fontWeight: 600 }}>{hook}</div>
        </div>
      ) : null}

      {shots.length > 0 ? (
        <div>
          <div className="section-h">分镜时间轴</div>
          {/* 时间轴条：每镜按占比着色，一眼看出节奏是否头重脚轻 */}
          <div style={{ display: 'flex', height: 10, borderRadius: 3, overflow: 'hidden', marginTop: 4 }}>
            {shots.map((shot, index) => (
              <div
                key={`bar-${index}`}
                title={`#${asNumber(shot.shot, index + 1)} ${asText(shot.role)} ${asNumber(shot.duration_seconds, 0)}s`}
                style={{
                  flex: Math.max(1, asNumber(shot.duration_seconds, 1)),
                  background: 'currentColor',
                  opacity: 0.3 + Math.min(0.6, index * 0.15),
                }}
              />
            ))}
          </div>

          <Table head={['镜', '作用', '时间', '画面', '口播 / 字幕', '机位']}>
            {shots.map((shot, index) => (
              <tr key={`shot-${index}`}>
                <td>{asNumber(shot.shot, index + 1)}</td>
                <td>
                  <Chip tone="tone-idle">{asText(shot.role)}</Chip>
                </td>
                <td className="mono small">
                  {asNumber(shot.start_second, 0)}-{asNumber(shot.end_second, 0)}s
                  <div className="muted small">{asNumber(shot.duration_seconds, 0)}s</div>
                </td>
                <td className="small">
                  <div>{asText(shot.visual) || '—'}</div>
                  {asText(shot.intent) ? (
                    <div className="muted small">意图：{asText(shot.intent)}</div>
                  ) : null}
                </td>
                <td className="small">
                  <div>{asText(shot.voiceover) || '（无口播）'}</div>
                  {asText(shot.subtitle) ? (
                    <div className="muted small">字幕：{asText(shot.subtitle)}</div>
                  ) : null}
                </td>
                <td className="small">{asText(shot.camera) || '—'}</td>
              </tr>
            ))}
          </Table>
        </div>
      ) : (
        <Empty>该脚本没有分镜内容</Empty>
      )}

      {productionNotes.length > 0 ? (
        <div>
          <div className="section-h">拍摄要点</div>
          <ul className="small" style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {productionNotes.map((note, index) => (
              <li key={`pn-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}

      {complianceNotes.length > 0 ? (
        <div>
          <div className="section-h">合规注意</div>
          <ul className="small" style={{ margin: '4px 0 0 16px', padding: 0 }}>
            {complianceNotes.map((note, index) => (
              <li key={`cn-${index}`}>{note}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* 主体                                                                */
/* ------------------------------------------------------------------ */

export function ArtifactViewer({ task }: { task: TaskRecord }) {
  const artifacts = useMemo(
    () => task.artifacts.slice().sort((a, b) => (a.created_at < b.created_at ? 1 : -1)),
    [task.artifacts],
  );

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showDiff, setShowDiff] = useState(false);

  const selected = useMemo(() => {
    if (selectedId) {
      const hit = artifacts.find((a) => a.id === selectedId);
      if (hit) return hit;
    }
    return artifacts.find((a) => a.type === 'final_delivery') ?? artifacts[0] ?? null;
  }, [artifacts, selectedId]);

  const previous = useMemo(() => {
    if (!selected) return null;
    const sameType = task.artifacts
      .filter((a) => a.type === selected.type)
      .sort((a, b) => a.version - b.version);
    const index = sameType.findIndex((a) => a.id === selected.id);
    return index > 0 ? sameType[index - 1]! : null;
  }, [task.artifacts, selected]);

  if (!artifacts.length) {
    return <Empty>暂无产物。流水线开始运行后，产物会实时写入共享黑板。</Empty>;
  }

  const Renderer = selected ? RENDERERS[selected.type] : undefined;

  return (
    <div className="art-layout">
      <div className="art-list">
        {artifacts.map((artifact) => (
          <button
            key={artifact.id}
            className={`art-item${artifact.id === selected?.id ? ' active' : ''}`}
            onClick={() => {
              setSelectedId(artifact.id);
              setShowDiff(false);
            }}
          >
            <span className="v">v{artifact.version}</span>
            <div>
              <span className="mono muted">{artifact.agent_id}</span> {TYPE_LABEL[artifact.type] ?? artifact.type}
            </div>
            <div className="muted" style={{ fontSize: 10.5, marginTop: 2 }}>
              {formatTime(artifact.created_at)}
              {artifact.revision > 0 ? ` · 返工 r${artifact.revision}` : ''}
            </div>
          </button>
        ))}
      </div>

      <div className="art-body">
        {selected ? (
          <div className="panel">
            <div className="row" style={{ justifyContent: 'space-between', marginBottom: 12 }}>
              <div>
                <div style={{ fontWeight: 700, fontSize: 15 }}>
                  {selected.title}
                  <span className="muted small"> · {TYPE_LABEL[selected.type] ?? selected.type}</span>
                </div>
                <div className="row small muted" style={{ marginTop: 4 }}>
                  <span>{selected.agent_id}</span>
                  <span>版本 v{selected.version}</span>
                  <span>返工轮次 r{selected.revision}</span>
                  <span>{formatTime(selected.created_at)}</span>
                </div>
              </div>
              {previous ? (
                <button className="btn btn-sm" onClick={() => setShowDiff((value) => !value)}>
                  {showDiff ? '返回正文' : `对比 v${previous.version}`}
                </button>
              ) : null}
            </div>

            {selected.tags.length ? (
              <div className="hashtag-row" style={{ marginBottom: 12 }}>
                {selected.tags.map((tag) => (
                  <Chip key={tag} tone="tone-idle">
                    {tag}
                  </Chip>
                ))}
              </div>
            ) : null}

            {showDiff && previous ? (
              <div className="diff">
                {diffLines(previous.text, selected.text).map((line, index) => (
                  <div
                    className={`diff-line${line.type === 'add' ? ' add' : line.type === 'del' ? ' del' : ''}`}
                    key={index}
                  >
                    {line.type === 'add' ? '+ ' : line.type === 'del' ? '- ' : '  '}
                    {line.text || ' '}
                  </div>
                ))}
              </div>
            ) : Renderer ? (
              <Renderer content={asRecord(selected.content)} />
            ) : (
              <GenericView content={asRecord(selected.content)} />
            )}
          </div>
        ) : null}
      </div>
    </div>
  );
}
