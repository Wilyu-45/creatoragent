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
  id: 'A6',
  name: '事实核查智能体',
  role: '事实核查员',
  kind: 'reviewer',
  phase: 'FACT_CHECK',
  produces: 'fact_check_report',
  description: '核查数据、时间、引用与案例真实性，标记无来源、过期、矛盾或夸大的信息',
  veto: true,
  capabilities: ['主张核验', '数值断言扫描', '来源追溯', '否决权'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 逐条核查文案中的主张：数据、时间、人物、参数、引用、案例
2. 标记状态：verified（可核实）/ unverified（无来源）/ exaggerated（夸大）/ expired（过期）/ contradicted（矛盾）
3. 要求文案补充来源，或改为安全表达
4. 输出核查报告与置信度；高风险未修正时行使否决权

硬性规则：
- 你没有联网能力时，不得假装查证过；无法核实的应标为 unverified 而不是 verified
- 数值型断言（百分比、倍数、天数、样本量）若无来源，一律视为高风险
- 情绪化、主观的最高级表述应标记为 exaggerated
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "checks": [{"claim": "", "status": "verified|unverified|exaggerated|expired|contradicted", "source": "", "note": "", "confidence": 0.0}],
  "risk_level": "low|medium|high",
  "verdict": "pass|revise|reject",
  "verdict_reason": "",
  "required_fixes": [""],
  "safe_rewrites": [{"original": "", "rewrite": "", "reason": ""}],
  "verification_scope": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const checks = objArray(data.checks).map((item) => ({
    claim: str(item.claim),
    status: str(item.status, 'unverified'),
    source: str(item.source, '未提供'),
    note: str(item.note),
    confidence: Number(item.confidence) || 0.6,
  }));
  return {
    checks,
    risk_level: str(data.risk_level, 'medium'),
    verdict: str(data.verdict, 'revise'),
    verdict_reason: str(data.verdict_reason),
    required_fixes: strArray(data.required_fixes),
    safe_rewrites: objArray(data.safe_rewrites),
    verification_scope: strArray(data.verification_scope),
    summary: {
      total: checks.length,
      verified: checks.filter((c) => c.status === 'verified').length,
      unverified: checks.filter((c) => c.status === 'unverified').length,
      exaggerated: checks.filter((c) => c.status === 'exaggerated').length,
    },
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const draft = ctx.upstream.draft ?? {};
  ctx.emit('开始核查文案中的数据、引用与主张来源');

  const recommended = str(draft.recommended_version, 'V1');
  const target = objArray(draft.versions).find((v) => str(v.id) === recommended) ?? {};
  const claims = objArray(draft.claims);

  const user = `【核查对象】${brief.channel} 主推版本
标题：${str(target.title)}

正文：
${str(target.body)}

【作者声明的主张及来源】
${claims.map((c, i) => `${i + 1}. 主张：${str(c.text)}｜来源：${str(c.source) || '（未提供）'}`).join('\n') || '（作者未声明主张，请自行从正文中抽取）'}

行业：${brief.industry}
请逐条核查并给出风险等级与修改要求，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A6.factcheck', {
    brief,
    draft,
    revision: ctx.revision,
  });

  const content = normalize(data);
  const summary = obj(content.summary);
  const riskLevel = str(content.risk_level, 'medium');
  const verdict = (['pass', 'revise', 'reject'].includes(str(content.verdict))
    ? str(content.verdict)
    : riskLevel === 'high'
      ? 'revise'
      : 'pass') as 'pass' | 'revise' | 'reject';

  ctx.emit(
    `核查完成：${Number(summary.total) || 0} 项，无来源 ${Number(summary.unverified) || 0} 项，风险等级 ${riskLevel}`,
    { risk_level: riskLevel, verdict },
  );

  const artifact = buildArtifact(ctx, META, {
    type: 'fact_check_report',
    title: '事实核查报告',
    content,
    text: contentToText(content),
    tags: ['核查', `风险:${riskLevel}`],
  });

  const requiredFixes = strArray(content.required_fixes);
  const needsHuman = riskLevel === 'high' && (ctx.revision > 0 || verdict === 'reject');

  return buildResult(ctx, META, metrics, {
    summary: `核查 ${Number(summary.total) || 0} 项主张：可核实 ${Number(summary.verified) || 0} 项、无来源 ${Number(summary.unverified) || 0} 项、夸大 ${Number(summary.exaggerated) || 0} 项，风险等级 ${riskLevel}`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.84),
    risks: readRisks(data),
    evidence: readEvidence(data),
    needs_human_review: needsHuman,
    gate_result: verdict,
    revision_requests:
      verdict === 'pass'
        ? []
        : requiredFixes.map((fix) => `[事实核查] ${fix}`).concat(
            objArray(content.safe_rewrites).map(
              (item) => `[事实核查·建议改写] 「${str(item.original)}」→「${str(item.rewrite)}」`,
            ),
          ),
    handoff:
      verdict === 'pass'
        ? { to: 'A7', reason: '事实核查通过，进入品牌合规审查' }
        : { to: 'A4', reason: '存在无来源或夸大表述，需补充来源或改写' },
  });
}

export const a6FactChecker: AgentDefinition = { meta: META, run };
