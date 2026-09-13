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
  id: 'A10',
  name: '数据分析与复盘智能体',
  role: '数据分析师 / 增长顾问',
  kind: 'analyst',
  phase: 'ANALYZED',
  produces: 'effect_report',
  description: '发布前预估效果区间，发布后回收数据做归因分析，输出优化建议与 A/B 方案',
  veto: false,
  capabilities: ['效果预估', '归因分析', '优化建议', 'A/B 方案'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 发布前给出曝光、点击、互动、转化的区间预估，并说明预估依据
2. 归因内容表现的驱动因素（标题、开头、标签、时段）
3. 输出可执行的优化建议与 A/B 测试方案
4. 给出下一轮 Brief 建议

硬性规则：
- 预估必须是区间，并明确「不构成效果承诺」
- 不得编造具体的历史数据或平台内幕数据
- 优化建议要给出预期收益方向与实施成本
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "predicted": {"exposure": {"low": 0, "mid": 0, "high": 0, "unit": "", "basis": ""}, "ctr": {}, "engagement": {}, "conversion": {}},
  "objective_alignment": {"objective": "", "score": 0, "note": ""},
  "attribution": [{"factor": "", "impact": "high|medium|low", "note": ""}],
  "optimizations": [{"priority": "high|medium|low", "action": "", "expected_gain": "", "effort": ""}],
  "ab_tests": [{"hypothesis": "", "variant_a": "", "variant_b": "", "metric": ""}],
  "next_brief_suggestions": [""],
  "cautions": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  return {
    mode: 'pre_publish_estimate',
    predicted: obj(data.predicted),
    objective_alignment: obj(data.objective_alignment),
    attribution: objArray(data.attribution),
    optimizations: objArray(data.optimizations),
    ab_tests: objArray(data.ab_tests),
    next_brief_suggestions: strArray(data.next_brief_suggestions),
    cautions: strArray(data.cautions).length
      ? strArray(data.cautions)
      : ['以上为区间预估，不构成效果承诺'],
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const draft = ctx.upstream.draft ?? {};
  const plan = ctx.upstream.plan ?? {};
  const strategy = ctx.upstream.strategy ?? {};

  ctx.emit('基于内容版本与渠道规则做效果预估（发布前）');

  const recommended = str(draft.recommended_version, 'V1');
  const target = objArray(draft.versions).find((v) => str(v.id) === recommended) ?? {};

  const user = `【预估对象】${brief.channel} 主推版本
标题：${str(target.title)}
目标：${brief.objective}｜受众：${brief.audience}
选题：${str(plan.selected_topic)}｜标签：${str(plan.keywords ? obj(plan.keywords).hashtags : '')}

【策略目标】
${objArray(strategy.objectives).map((o) => `${str(o.type)}：${str(o.metric)} → ${str(o.target)}`).join('\n') || '（未指定）'}

请给出效果预估区间、归因、优化建议与 A/B 方案，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A10.analyze', {
    brief,
    strategy,
    plan,
    draft,
    revision: ctx.revision,
  });

  const content = normalize(data);
  const predicted = obj(content.predicted);
  const ctr = obj(predicted.ctr);

  ctx.emit('效果预估完成', {
    ctr_mid: ctr.mid ?? null,
    optimizations: objArray(content.optimizations).length,
  });

  const artifact = buildArtifact(ctx, META, {
    type: 'effect_report',
    title: '效果预估与优化报告',
    content,
    text: contentToText(content),
    tags: ['数据分析', brief.channel],
  });

  return buildResult(ctx, META, metrics, {
    summary: `完成发布前效果预估与 ${objArray(content.optimizations).length} 条优化建议，预估 CTR 区间 ${str(ctr.low)}%~${str(ctr.high)}%（非承诺值）`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.66),
    risks: readRisks(data),
    evidence: readEvidence(data),
    handoff: { to: null, reason: '分析完成，等待人工审批' },
  });
}

export const a10Analyst: AgentDefinition = { meta: META, run };
