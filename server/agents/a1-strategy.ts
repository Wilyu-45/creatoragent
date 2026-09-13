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
  id: 'A1',
  name: '策略与洞察智能体',
  role: '策略总监 / 用户研究',
  kind: 'producer',
  phase: 'STRATEGY',
  produces: 'strategy_brief',
  description: '分析受众、场景、竞品与传播目标，输出核心信息屋与内容 KPI 建议',
  veto: false,
  capabilities: ['受众洞察', '核心信息屋', '渠道优先级', '证据标注'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 分析目标受众：人群特征、核心痛点、使用场景、购买动机、决策阻力
2. 构建核心信息屋：品牌主张（proposition）、支撑点、利益点、证据
3. 确定内容目标（曝光/互动/转化/教育/信任）及衡量口径
4. 给出渠道优先级权重与内容基调建议

硬性规则：
- 严格区分「事实」「假设」「创意建议」，不得把假设写成事实
- 不得编造调研数据、用户数量、百分比或来源；无法确认的信息必须放入 risks
- 受众结论要说明推断依据
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "audience_profile": {"segment": "", "pain_points": [""], "scenarios": [""], "motivations": [""], "objections": [""]},
  "message_house": {"proposition": "", "support_points": [""], "benefits": [""], "evidence": [{"type": "", "status": "", "note": ""}]},
  "objectives": [{"type": "", "metric": "", "target": ""}],
  "channel_priority": [{"channel": "", "weight": 0.6, "why": ""}],
  "tone_guide": {"keywords": [""], "avoid": [""]},
  "key_takeaways": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>, fallbackAudience: string): Record<string, unknown> {
  const audience = obj(data.audience_profile);
  const house = obj(data.message_house);
  return {
    audience_profile: {
      segment: str(audience.segment, fallbackAudience),
      pain_points: strArray(audience.pain_points),
      scenarios: strArray(audience.scenarios),
      motivations: strArray(audience.motivations),
      objections: strArray(audience.objections),
    },
    message_house: {
      proposition: str(house.proposition),
      support_points: strArray(house.support_points),
      benefits: strArray(house.benefits),
      evidence: objArray(house.evidence),
    },
    objectives: objArray(data.objectives),
    channel_priority: objArray(data.channel_priority),
    tone_guide: {
      keywords: strArray(obj(data.tone_guide).keywords),
      avoid: strArray(obj(data.tone_guide).avoid),
    },
    key_takeaways: strArray(data.key_takeaways),
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  ctx.emit('开始分析受众、场景与传播目标');

  const user = `【创作 Brief】
品牌：${brief.brand}
产品/服务：${brief.product}
传播目标：${brief.objective}
目标受众：${brief.audience}
主渠道：${brief.channel}
内容调性：${brief.tone}
所属行业：${brief.industry}
关键词：${brief.keywords.join('、') || '（未指定）'}
硬性约束：${brief.constraints.join('；') || '（未指定）'}
补充说明：${brief.notes || '（无）'}

请输出策略简报，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A1.strategy', { brief });
  const content = normalize(data, brief.audience);

  ctx.emit('策略简报已生成', {
    pain_points: strArray(obj(content.audience_profile).pain_points).length,
  });

  const artifact = buildArtifact(ctx, META, {
    type: 'strategy_brief',
    title: '策略简报',
    content,
    text: contentToText(content),
    tags: ['策略', brief.industry, brief.channel],
  });

  return buildResult(ctx, META, metrics, {
    summary: `完成受众洞察与核心信息屋，主张：「${str(obj(content.message_house).proposition)}」`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.8),
    risks: readRisks(data),
    evidence: readEvidence(data),
    handoff: { to: 'A2', reason: '策略已产出，请创意总监确认创意方向' },
  });
}

export const a1Strategy: AgentDefinition = { meta: META, run };
