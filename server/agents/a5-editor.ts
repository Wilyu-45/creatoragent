import type { AgentResult } from '../core/types.ts';
import {
  buildArtifact,
  buildResult,
  callWithPrompts,
  obj,
  objArray,
  readConfidence,
  readEvidence,
  readRisks,
  normalizeScore,
  readGate,
  readReviews,
  str,
  strArray,
  systemPrompt,
} from './base.ts';
import type { AgentDefinition, AgentMeta, AgentRunContext } from './base.ts';

const META: AgentMeta = {
  id: 'A5',
  name: '编辑审校智能体',
  role: '主编 / 校对 / 质量门禁',
  kind: 'reviewer',
  phase: 'EDITING',
  produces: 'edited_copy',
  description: '检查逻辑、结构、语病与品牌语气，输出修订稿、修改说明与质量评分',
  veto: false,
  capabilities: ['结构评审', '语言润色', '质量评分', '退回意见'],
};

const SYSTEM = systemPrompt(
  META,
  `你的职责：
1. 检查逻辑结构、语病、错别字、标点、重复表达
2. 优化表达节奏与可读性，统一品牌语气与术语
3. 给出四维质量评分（结构 / 表达 / 品牌语气 / 吸引力，0-100）
4. 输出修订稿与逐条修改说明；存在重要问题时有权退回 A4 重写

硬性规则：
- 修订不得改变事实主张、不得新增未经验证的数据
- 每个问题必须给出可执行的修改建议，不要只说「建议优化」
- 只做编辑职责内的事，不做合规与事实判定
- 只输出 JSON，不输出任何解释性文字`,
);

const SCHEMA = `{
  "revised": {"title": "", "body": "", "cta": "", "hashtags": [""]},
  "change_log": [{"type": "", "detail": "", "before": "", "after": ""}],
  "scorecard": {"structure": 0, "clarity": 0, "brand_voice": 0, "appeal": 0, "overall": 0},
  "issues": [{"severity": "blocker|major|minor", "category": "", "detail": "", "suggestion": "", "location": ""}],
  "verdict": "pass|revise",
  "verdict_reason": "",
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}`;

function normalize(data: Record<string, unknown>): Record<string, unknown> {
  const card = obj(data.scorecard);
  const structure = normalizeScore(card.structure, 80);
  const clarity = normalizeScore(card.clarity, 80);
  const brandVoice = normalizeScore(card.brand_voice, 80);
  const appeal = normalizeScore(card.appeal, 80);
  const overall = normalizeScore(card.overall, Math.round((structure + clarity + brandVoice + appeal) / 4));
  const revised = obj(data.revised);
  return {
    revised: {
      title: str(revised.title),
      body: str(revised.body),
      cta: str(revised.cta),
      hashtags: strArray(revised.hashtags),
    },
    change_log: objArray(data.change_log),
    scorecard: { structure, clarity, brand_voice: brandVoice, appeal, overall },
    issues: objArray(data.issues),
    verdict: str(data.verdict, 'pass'),
    verdict_reason: str(data.verdict_reason),
  };
}

async function run(ctx: AgentRunContext): Promise<AgentResult> {
  const { brief } = ctx;
  const draft = ctx.upstream.draft ?? {};
  ctx.emit('开始审校：结构、表达、品牌语气');

  const recommended = str(draft.recommended_version, 'V1');
  const target = objArray(draft.versions).find((v) => str(v.id) === recommended) ?? {};

  const user = `【审校对象】${brief.channel} 主推版本「${recommended}」
品牌：${brief.brand}｜调性要求：${brief.tone}｜行业：${brief.industry}

标题：${str(target.title)}

正文：
${str(target.body)}

CTA：${str(target.cta)}
话题标签：${strArray(target.hashtags).join(' ')}

请完成审校并输出修订稿、修改说明与质量评分，严格要求 JSON 结构如下：
${SCHEMA}`;

  const { data, metrics } = await callWithPrompts(ctx, META, SYSTEM, user, 'A5.edit', {
    brief,
    draft,
    revision: ctx.revision,
  });

  const content = normalize(data);
  const card = obj(content.scorecard);
  const overall = normalizeScore(card.overall, 80);
  const verdict = readGate(data, overall >= 75 ? 'pass' : 'revise');
  const issues = readReviews(data, 'issues');
  const revised = obj(content.revised);
  const renderedText = `${str(revised.title)}\n\n${str(revised.body)}\n\n${strArray(revised.hashtags).join(' ')}`;

  ctx.emit(`审校完成：质量分 ${overall.toFixed(0)}，判定 ${verdict}`, {
    overall,
    blockers: issues.filter((i) => i.severity === 'blocker' || i.severity === 'major').length,
  });

  const artifact = buildArtifact(ctx, META, {
    type: 'edited_copy',
    title: '编辑修订稿',
    content: { ...content, target_version: recommended },
    text: renderedText,
    tags: ['审校', `质量分:${overall.toFixed(0)}`],
  });

  const revisionRequests = issues
    .filter((i) => i.severity === 'blocker' || i.severity === 'major')
    .map((i) => `[编辑·${i.category}] ${i.detail}${i.suggestion ? `；建议：${i.suggestion}` : ''}`);

  return buildResult(ctx, META, metrics, {
    summary: `质量评分 ${overall.toFixed(0)}/100，${issues.length} 条问题，判定：${verdict === 'pass' ? '通过' : '退回修改'}`,
    artifacts: [artifact],
    confidence: readConfidence(data, 0.85),
    risks: readRisks(data),
    evidence: readEvidence(data),
    gate_result: verdict,
    revision_requests: verdict === 'pass' ? [] : revisionRequests,
    handoff:
      verdict === 'pass'
        ? { to: 'A6', reason: '编辑通过，进入事实核查' }
        : { to: 'A4', reason: '质量问题需退回文案重写' },
  });
}

export const a5Editor: AgentDefinition = { meta: META, run };
