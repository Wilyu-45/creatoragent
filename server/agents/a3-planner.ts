import type { AgentResult } from '../core/types.ts';
import {
  buildArtifact,
  buildResult,
  callWithPrompts,
  contentToText,
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
  id: 'A3',
  name: '内容策划智能体',
  role: '内容策划 / 选题',
  kind: 'producer',
  phase: 'PLANNING',
  produces: 'content_plan',
  description: '把创意方向拆成具体选题、内容大纲、标题备选与渠道适配表',
  veto: false,
  capabilities: ['选题清单', '内容大纲', '标题备选', '排期建议'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 将创意方向拆解为 3 个具体选题，每个选题给出结构化的内容大纲
2. 给出标题备选（不少于 5 条）
3. 按内容结构给出分段目标与字数分配
4. 输出渠道适配表与发布节奏建议

硬性规则：
- 大纲必须与选定创意方向一致，不得另起炉灶
- 标题不得使用绝对化用语、不得承诺效果
- 排期建议只描述动作与节奏，不承诺流量结果
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "topics": [{"id": "T1", "title": "", "angle": "", "format": "", "outline": [""], "cta": "", "estimated_words": 0}],
  "selected_topic": "T1",
  "selection_reason": "",
  "headline_candidates": [""],
  "structure": [{"section": "", "goal": "", "words": 0}],
  "channel_adaptation": [{"channel": "", "format": "", "notes": ""}],
  "publishing_rhythm": [{"slot": "", "action": "", "note": ""}],
  "keywords": {"primary": [""], "long_tail": [""], "hashtags": ""},
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const keywords = obj(data.keywords);
  return {
    topics: objArray(data.topics).map((item, index) => ({
      id: str(item.id, `T${index + 1}`),
      title: str(item.title),
      angle: str(item.angle),
      format: str(item.format),
      outline: strArray(item.outline),
      cta: str(item.cta),
      estimated_words: Number(item.estimated_words) || 0,
    })),
    selected_topic: str(data.selected_topic, 'T1'),
    selection_reason: str(data.selection_reason),
    headline_candidates: strArray(data.headline_candidates),
    structure: objArray(data.structure),
    channel_adaptation: objArray(data.channel_adaptation),
    publishing_rhythm: objArray(data.publishing_rhythm),
    keywords: {
      primary: strArray(keywords.primary),
      long_tail: strArray(keywords.long_tail),
      hashtags: str(keywords.hashtags),
    },
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const strategy = ctx.upstream.strategy ?? {};
  const creative = ctx.upstream.creative ?? {};
  const audience = obj(strategy.audience_profile);
  const bigIdea = obj(creative.big_idea);

  ctx.emit('把创意方向拆解为选题与内容大纲');

  const user = `【创作 Brief】
品牌：${brief.brand}｜产品：${brief.product}｜渠道：${brief.channel}｜目标：${brief.objective}
关键词：${brief.keywords.join('、') || '（未指定）'}
期望交付物：${brief.deliverables.join('；') || '（未指定）'}

【A1 策略要点】
受众痛点：${strArray(audience.pain_points).join('；')}
核心主张：${str(obj(strategy.message_house).proposition)}

【A2 创意方向】
Big Idea：${str(bigIdea.title)} —— ${str(bigIdea.statement)}
推荐方向：${str(creative.recommended_direction)}

请输出选题清单、内容大纲与渠道适配表，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A3.plan', {
    brief,
    strategy,
    creative,
  });
  const content = normalize(data);
  const topics = objArray(content.topics);

  ctx.emit(`已产出 ${topics.length} 个选题，选定 ${str(content.selected_topic, 'T1')}`);

  const artifact = buildArtifact(ctx, META, {
    type: 'content_plan',
    title: '选题与内容大纲',
    content,
    text: contentToText(content),
    tags: ['策划', brief.channel],
  });

  return buildResult(ctx, META, metrics, {
    summary: `产出 ${topics.length} 个选题、${strArray(content.headline_candidates).length} 条标题备选`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.8),
    risks: readRisks(data),
    evidence: readEvidence(data),
    handoff: { to: 'A4', reason: '选题与大纲已确定，请文案撰写初稿' },
  });
}

export const a3Planner: AgentDefinition = { meta: META, run };
