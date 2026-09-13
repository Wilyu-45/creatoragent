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
  id: 'A7',
  name: '品牌与合规智能体',
  role: '品牌守护者 / 法务合规',
  kind: 'reviewer',
  phase: 'COMPLIANCE',
  produces: 'compliance_report',
  description: '检查品牌一致性、广告法禁用词、行业特殊限制与版权风险，输出合规等级与强制修改项',
  veto: true,
  capabilities: ['敏感词库', '行业法规', '品牌一致性', '否决权'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 检查绝对化用语、虚假宣传、效果承诺（《广告法》第九条/第十七条/第二十五条/第二十八条）
2. 检查行业特殊限制：医疗、金融、教育、食品、化妆品的专门规定
3. 检查品牌调性与术语一致性
4. 输出命中项、法规依据、强制修改要求与合规等级

硬性规则：
- 每条命中必须给出法规依据与可执行的替换方案，不能只说「违规」
- 区分阻断项（blocker）与建议项（minor），不要过度扩大打击面
- 合规不通过时行使否决权，禁止进入发布阶段
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "hits": [{"severity": "blocker|major|minor", "category": "", "term": "", "detail": "", "suggestion": "", "law": ""}],
  "summary": {"blocker": 0, "major": 0, "minor": 0},
  "brand_consistency": {"score": 0.0, "issues": [{"type": "", "detail": "", "suggestion": ""}]},
  "risk_level": "low|medium|high",
  "verdict": "pass|revise|reject",
  "verdict_reason": "",
  "required_fixes": [""],
  "safe_rewrites": [{"from": "", "to": ""}],
  "checked_against": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const brand = obj(data.brand_consistency);
  return {
    hits: objArray(data.hits).map((item) => ({
      severity: str(item.severity, 'minor'),
      category: str(item.category, '一般'),
      term: str(item.term),
      detail: str(item.detail),
      suggestion: str(item.suggestion),
      law: str(item.law, '—'),
    })),
    summary: {
      blocker: Number(obj(data.summary).blocker) || 0,
      major: Number(obj(data.summary).major) || 0,
      minor: Number(obj(data.summary).minor) || 0,
    },
    brand_consistency: {
      score: Number(brand.score) || 0,
      issues: objArray(brand.issues),
    },
    risk_level: str(data.risk_level, 'medium'),
    verdict: str(data.verdict, 'revise'),
    verdict_reason: str(data.verdict_reason),
    required_fixes: strArray(data.required_fixes),
    safe_rewrites: objArray(data.safe_rewrites),
    checked_against: strArray(data.checked_against),
    compliance_score: Number(data.compliance_score) || 0,
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const draft = ctx.upstream.draft ?? {};
  ctx.emit('开始品牌一致性与广告法合规扫描');

  const recommended = str(draft.recommended_version, 'V1');
  const target = objArray(draft.versions).find((v) => str(v.id) === recommended) ?? {};

  const user = `【审查对象】${brief.channel} 主推版本
标题：${str(target.title)}

正文：
${str(target.body)}

话题标签：${strArray(target.hashtags).join(' ')}

【审查基准】
品牌：${brief.brand}｜行业：${brief.industry}｜要求调性：${brief.tone}
品牌约束：${brief.constraints.join('；') || '（未指定）'}

请输出合规报告，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A7.compliance', {
    brief,
    draft,
    revision: ctx.revision,
  });

  const content = normalize(data);
  const summary = obj(content.summary);
  const riskLevel = str(content.risk_level, 'medium');
  const verdict = (['pass', 'revise', 'reject'].includes(str(content.verdict))
    ? str(content.verdict)
    : 'revise') as 'pass' | 'revise' | 'reject';

  ctx.emit(
    `合规扫描完成：阻断 ${Number(summary.blocker) || 0} 项、重要 ${Number(summary.major) || 0} 项，风险等级 ${riskLevel}`,
    { risk_level: riskLevel, verdict },
  );

  const artifact = buildArtifact(ctx, META, {
    type: 'compliance_report',
    title: '品牌与合规报告',
    content,
    text: contentToText(content),
    tags: ['合规', `风险:${riskLevel}`],
  });

  const requiredFixes = strArray(content.required_fixes);
  const needsHuman = riskLevel === 'high' && ctx.revision > 0;

  return buildResult(ctx, META, metrics, {
    summary: `合规等级 ${riskLevel}：阻断 ${Number(summary.blocker) || 0} 项、重要 ${Number(summary.major) || 0} 项、建议 ${Number(summary.minor) || 0} 项`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.9),
    risks: readRisks(data),
    evidence: readEvidence(data),
    needs_human_review: needsHuman,
    gate_result: verdict,
    revision_requests:
      verdict === 'pass' ? [] : requiredFixes.map((fix) => `[品牌合规] ${fix}`),
    handoff:
      verdict === 'pass'
        ? { to: 'A10', reason: '合规通过，进入效果预估与人工审批' }
        : { to: 'A4', reason: '存在合规风险，必须修改后方可发布' },
  });
}

export const a7Compliance: AgentDefinition = { meta: META, run };
