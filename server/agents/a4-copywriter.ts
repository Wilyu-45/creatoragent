import type { AgentResult } from '../core/types.ts';
import { channelRule } from '../knowledge/industry.ts';
import {
  buildArtifact,
  buildResult,
  callWithPrompts,
  obj,
  objArray,
  readConfidence,
  readEvidence,
  readRisks,
  str,
  strArray,
  systemPrompt,
} from './base.ts';
import type { AgentDefinition, AgentMeta, AgentRunContext } from './base.ts';

const META: AgentMeta = {
  id: 'A4',
  name: '文案创作智能体',
  role: '文案 / 撰稿人',
  kind: 'producer',
  phase: 'DRAFTING',
  produces: 'copy_draft',
  description: '按大纲撰写标题、正文、脚本与 CTA，产出多版本文案并声明全部关键主张',
  veto: false,
  capabilities: ['多版本文案', '渠道语言适配', '主张声明', '按意见返工'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 依据内容大纲与创意调性撰写文案，产出 3 个版本（主推版 / 理性版 / 感性版）
2. 适配渠道语言风格与内容形态（图文 / 短视频分镜 / 长文 / 详情页）
3. 提供标题备选与多种 CTA
4. 通过 claims 字段声明文案中所有关键主张及其来源

硬性规则：
- 绝不编造事实、数据、用户证言；无法确认的信息不要写进正文
- 不承诺效果与收益
- claims 中每一条主张必须给出 source；如果暂时没有来源，source 留空字符串，由事实核查智能体处理
- 收到 revision_feedback 时，必须逐条落实修改，不得仅做措辞替换
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "versions": [{"id": "V1", "style": "", "title": "", "body": "", "cta": "", "hashtags": [""], "word_count": 0}],
  "recommended_version": "V1",
  "headlines": [""],
  "claims": [{"text": "", "source": ""}],
  "revision_notes": [{"from_feedback": "", "action": ""}],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const versions = objArray(data.versions).map((item, index) => {
    const hashtags = strArray(item.hashtags).map((h) => (h.startsWith('#') ? h : `#${h}`));
    const body = str(item.body);
    return {
      id: str(item.id, `V${index + 1}`),
      style: str(item.style, `版本 ${index + 1}`),
      title: str(item.title),
      body,
      cta: str(item.cta),
      hashtags,
      word_count: Number(item.word_count) || `${str(item.title)}${body}`.replace(/\s/g, '').length,
    };
  });
  return {
    versions,
    recommended_version: str(data.recommended_version, 'V1'),
    headlines: strArray(data.headlines),
    claims: objArray(data.claims).map((item) => ({
      text: str(item.text),
      source: str(item.source),
    })),
    revision_notes: objArray(data.revision_notes),
  };
}

function renderText(content: Record<string, unknown>): string {
  return objArray(content.versions)
    .map((v) => {
      const hashtags = strArray(v.hashtags).join(' ');
      return `【${str(v.style)}】${str(v.title)}\n\n${str(v.body)}\n\n${hashtags}`;
    })
    .join('\n\n————————\n\n');
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const strategy = ctx.upstream.strategy ?? {};
  const creative = ctx.upstream.creative ?? {};
  const plan = ctx.upstream.plan ?? {};
  const house = obj(strategy.message_house);
  const rule = channelRule(brief.channel);
  const isRevision = ctx.feedback.length > 0;

  ctx.emit(isRevision ? `按 ${ctx.feedback.length} 条审校意见修订文案` : '开始撰写多版本文案');

  const user = `【创作 Brief】
品牌：${brief.brand}｜产品：${brief.product}｜渠道：${brief.channel}｜调性：${brief.tone}
目标受众：${brief.audience}
关键词：${brief.keywords.join('、') || '（未指定）'}
硬性约束：${brief.constraints.join('；') || '（未指定）'}

【渠道规范】
形态：${rule.format}｜长度：${rule.length_hint}
必备结构：${rule.blocks.join(' → ')}
标签策略：${rule.hashtag_policy}
合规注意：${rule.compliance_notes.join('；')}

【核心主张】${str(house.proposition)}
【利益点】${strArray(house.benefits).join('；')}

【选题与大纲】
选定选题：${str(plan.selected_topic)}
标题备选：${strArray(plan.headline_candidates).join(' / ')}

${isRevision ? `【本轮必须落实的修订意见】\n${ctx.feedback.map((f, i) => `${i + 1}. ${f}`).join('\n')}\n` : ''}
请撰写 3 个版本文案，并声明全部关键主张，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A4.copy', {
    brief,
    strategy,
    creative,
    plan,
    feedback: ctx.feedback,
    revision: ctx.revision,
  });

  const content = normalize(data);
  const versions = objArray(content.versions);
  const recommended = str(content.recommended_version, 'V1');
  const target = versions.find((v) => str(v.id) === recommended) ?? versions[0] ?? {};

  ctx.emit(`已生成 ${versions.length} 版文案，主推 ${recommended}`, {
    versions: versions.map((v) => ({ id: str(v.id), style: str(v.style), title: str(v.title) })),
  });

  const artifact = buildArtifact(ctx, META, {
    type: 'copy_draft',
    title: isRevision ? `文案初稿（第 ${ctx.revision + 1} 轮修订）` : '文案初稿',
    content,
    text: renderText(content),
    tags: ['文案', brief.channel, isRevision ? '返工稿' : '初稿'],
  });

  return buildResult(ctx, META, metrics, {
    summary: isRevision
      ? `已按 ${ctx.feedback.length} 条意见完成修订，输出 ${versions.length} 版`
      : `已生成 ${versions.length} 版${brief.channel}文案，主推「${str(target.title)}」`,
    artifacts: [artifact],
    confidence: readConfidence(data, isRevision ? 0.86 : 0.8),
    risks: readRisks(data),
    evidence: readEvidence(data),
    handoff: { to: 'A5', reason: '初稿完成，进入编辑审校' },
  });
}

export const a4Copywriter: AgentDefinition = { meta: META, run };
