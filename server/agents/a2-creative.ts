import type { AgentResult } from '../core/types.ts';
import {
  buildArtifact,
  buildResult,
  callWithPrompts,
  contentToText,
  normalizeConfidence,
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
  id: 'A2',
  name: '创意总监智能体',
  role: '创意总监',
  kind: 'producer',
  phase: 'CREATIVE',
  produces: 'creative_concept',
  description: '基于策略提出 Big Idea、创意方向与调性指南，决定「怎么说才吸引人」',
  veto: false,
  capabilities: ['Big Idea', '多方向创意', '调性指南', '案例参考'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 基于策略简报提出 1 个 Big Idea 与 2-3 个可执行创意方向
2. 每个方向必须包含：切入角度、开场钩子、示例标题、推荐理由、潜在风险
3. 明确调性指南（该做什么、不该做什么）
4. 给出可参考的同类案例方向

硬性规则：
- 创意方向之间必须有实质差异，不能是同一方向的措辞变化
- 不得承诺效果、收益，不得使用绝对化用语
- 每个方向都要说明适配度与风险，不要把创意建议写成事实
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "big_idea": {"title": "", "statement": "", "rationale": ""},
  "directions": [{"id": "D1", "name": "", "angle": "", "hook": "", "sample_headline": "", "rationale": "", "risk": "", "fit_score": 0}],
  "tone_guide": {"voice": "", "dos": [""], "donts": [""], "visual_suggestion": ""},
  "reference_cases": [{"name": "", "why": "", "source": ""}],
  "recommended_direction": "D1",
  "recommendation_reason": "",
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const bigIdea = obj(data.big_idea);
  const toneGuide = obj(data.tone_guide);
  return {
    big_idea: {
      title: str(bigIdea.title),
      statement: str(bigIdea.statement),
      rationale: str(bigIdea.rationale),
    },
    directions: objArray(data.directions).map((item, index) => ({
      id: str(item.id, `D${index + 1}`),
      name: str(item.name),
      angle: str(item.angle),
      hook: str(item.hook),
      sample_headline: str(item.sample_headline),
      rationale: str(item.rationale),
      risk: str(item.risk),
      fit_score: normalizeConfidence(item.fit_score, 0.7) * 100,
    })),
    tone_guide: {
      voice: str(toneGuide.voice),
      dos: strArray(toneGuide.dos),
      donts: strArray(toneGuide.donts),
      visual_suggestion: str(toneGuide.visual_suggestion),
    },
    reference_cases: objArray(data.reference_cases),
    recommended_direction: str(data.recommended_direction, 'D1'),
    recommendation_reason: str(data.recommendation_reason),
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const strategy = ctx.upstream.strategy ?? {};
  const house = obj(strategy.message_house);
  const audience = obj(strategy.audience_profile);

  ctx.emit('基于策略简报推导 Big Idea 与创意方向');

  const user = `【创作 Brief】
品牌：${brief.brand}｜产品：${brief.product}｜渠道：${brief.channel}｜调性：${brief.tone}

【A1 策略简报】
核心主张：${str(house.proposition)}
支撑点：${strArray(house.support_points).join('；')}
利益点：${strArray(house.benefits).join('；')}
受众痛点：${strArray(audience.pain_points).join('；')}
使用场景：${strArray(audience.scenarios).join('；')}
核心动机：${strArray(audience.motivations).join('；')}

请给出 Big Idea、2-3 个创意方向与调性指南，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A2.creative', {
    brief,
    strategy,
  });
  const content = normalize(data);
  const directions = objArray(content.directions);
  const recommended = str(content.recommended_direction, 'D1');

  ctx.emit(`已产出 ${directions.length} 个创意方向，推荐 ${recommended}`);

  const artifact = buildArtifact(ctx, META, {
    type: 'creative_concept',
    title: '创意概念与调性指南',
    content,
    text: contentToText(content),
    tags: ['创意', brief.channel],
  });

  return buildResult(ctx, META, metrics, {
    summary: `Big Idea：「${str(obj(content.big_idea).title)}」；共 ${directions.length} 个方向，推荐 ${recommended}`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.78),
    risks: readRisks(data),
    evidence: readEvidence(data),
    handoff: { to: 'A3', reason: '创意方向已收敛，请策划选题与内容大纲' },
  });
}

export const a2Creative: AgentDefinition = { meta: META, run };
