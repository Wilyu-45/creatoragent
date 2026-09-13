import { newId } from '../core/events.ts';
import type {
  AgentId,
  AgentKind,
  AgentMetrics,
  AgentResult,
  Artifact,
  ArtifactType,
  Brief,
  Evidence,
  GateResult,
  Phase,
  ReviewItem,
} from '../core/types.ts';
import {
  extractJson,
  normalizeConfidence,
  normalizeScore,
  num,
  obj,
  objArray,
  str,
  strArray,
} from '../llm/json.ts';
import { chat } from '../llm/index.ts';

/* ------------------------------------------------------------------ */
/* 运行时上下文                                                        */
/* ------------------------------------------------------------------ */

export interface AgentRunContext {
  task_id: string;
  brief: Brief;
  phase: Phase;
  /** 当前返工轮次，0 表示首轮 */
  revision: number;
  /** 本轮需要处理的返工意见 */
  feedback: string[];
  /** 上游产物内容，key 为 up_ 前缀约定名（strategy / creative / plan / draft ...） */
  upstream: Record<string, Record<string, unknown>>;
  /** 由编排层注入，保证产物版本号在黑板内单调递增 */
  nextVersion: (type: ArtifactType) => number;
  emit: (message: string, payload?: Record<string, unknown>) => void;
}

export interface AgentMeta {
  id: AgentId;
  name: string;
  /** 对应团队职位 */
  role: string;
  kind: AgentKind;
  phase: Phase;
  produces: ArtifactType;
  description: string;
  /** 是否拥有否决权（A6/A7） */
  veto: boolean;
  capabilities: string[];
}

export interface AgentDefinition {
  meta: AgentMeta;
  run(ctx: AgentRunContext): Promise<AgentResult>;
}

/* ------------------------------------------------------------------ */
/* 统一系统提示词：所有智能体共享的输出契约                            */
/* ------------------------------------------------------------------ */

export const SHARED_RULES = [
  '你是多智能体创作团队中的一员，只做职责范围内的事，不越权代替其他智能体。',
  '必须区分「事实」「假设」「创意建议」，不得编造数据、来源或用户证言。',
  '所有输出必须是合法 JSON，不要输出 Markdown 代码块以外的任何解释性文字。',
  '每个结论都要能说明依据；不确定时降低 confidence，而不是编造依据。',
  '涉及价格、功效、收益、数据引用时，必须在 risks 中标注风险。',
].join('\n');

export function systemPrompt(meta: AgentMeta, body: string): string {
  return `${SHARED_RULES}\n\n【你的角色】${meta.id} ${meta.name}（${meta.role}）\n${body}`;
}

/* ------------------------------------------------------------------ */
/* 结构化调用                                                          */
/* ------------------------------------------------------------------ */

const PRICING: Record<string, { in: number; out: number }> = {
  'gpt-4o-mini': { in: 0.15, out: 0.6 },
  'gpt-4o': { in: 2.5, out: 10 },
  'gpt-4.1-mini': { in: 0.4, out: 1.6 },
  'deepseek-chat': { in: 0.27, out: 1.1 },
  'qwen-plus': { in: 0.4, out: 1.2 },
  'qwen-max': { in: 1.6, out: 6.4 },
};

function priceOf(model: string): { in: number; out: number } {
  const hit = Object.keys(PRICING).find((key) => model.toLowerCase().includes(key));
  return hit ? PRICING[hit]! : { in: 0.5, out: 1.5 };
}

export interface StructuredResult {
  data: Record<string, unknown>;
  raw: string;
  metrics: AgentMetrics;
}

/**
 * 智能体唯一的结构化调用入口。
 * `context` 同时服务于两条路径：
 *   - 真实模型：由 system/user 提示词驱动
 *   - 离线引擎：由 context 中的结构化数据驱动
 * 两条路径产出同一份 JSON 契约，上层无需感知差异。
 */
export async function callWithPrompts(
  _ctx: AgentRunContext,
  meta: AgentMeta,
  system: string,
  user: string,
  purpose: string,
  context: Record<string, unknown>,
): Promise<StructuredResult> {
  const response = await chat({
    purpose,
    messages: [
      { role: 'system', content: system },
      { role: 'user', content: user },
    ],
    context,
    json: true,
  });

  const parsed = extractJson<Record<string, unknown>>(response.content);
  if (!parsed) {
    throw new Error(`${meta.id} 返回内容无法解析为 JSON：${response.content.slice(0, 200)}`);
  }

  const price = priceOf(response.model);
  const metrics: AgentMetrics = {
    latency_ms: response.latency_ms,
    prompt_tokens: response.usage.prompt_tokens,
    completion_tokens: response.usage.completion_tokens,
    cost_usd:
      (response.usage.prompt_tokens / 1_000_000) * price.in +
      (response.usage.completion_tokens / 1_000_000) * price.out,
    provider: response.provider,
    model: response.model,
    simulated: response.simulated,
  };

  return { data: parsed, raw: response.content, metrics };
}

/* ------------------------------------------------------------------ */
/* 产物与结果构造                                                      */
/* ------------------------------------------------------------------ */

export interface ArtifactDraft {
  type: ArtifactType;
  title: string;
  content: Record<string, unknown>;
  text: string;
  tags?: string[];
}

export function buildArtifact(ctx: AgentRunContext, meta: AgentMeta, draft: ArtifactDraft): Artifact {
  return {
    id: newId('art'),
    task_id: ctx.task_id,
    agent_id: meta.id,
    type: draft.type,
    version: ctx.nextVersion(draft.type),
    title: draft.title,
    content: draft.content,
    text: draft.text,
    revision: ctx.revision,
    created_at: new Date().toISOString(),
    tags: draft.tags ?? [],
  };
}

export interface ResultDraft {
  summary: string;
  artifacts: Artifact[];
  confidence: number;
  risks?: string[];
  evidence?: Evidence[];
  needs_human_review?: boolean;
  gate_result?: GateResult | null;
  revision_requests?: string[];
  handoff?: { to: AgentId | null; reason: string } | null;
  status?: AgentResult['status'];
}

export function buildResult(
  ctx: AgentRunContext,
  meta: AgentMeta,
  metrics: AgentMetrics,
  draft: ResultDraft,
): AgentResult {
  return {
    task_id: ctx.task_id,
    agent_id: meta.id,
    status: draft.status ?? 'success',
    summary: draft.summary,
    artifacts: draft.artifacts,
    evidence: draft.evidence ?? [],
    confidence: draft.confidence,
    risks: draft.risks ?? [],
    needs_human_review: draft.needs_human_review ?? false,
    gate_result: draft.gate_result ?? null,
    revision_requests: draft.revision_requests ?? [],
    handoff: draft.handoff ?? null,
    metrics,
    created_at: new Date().toISOString(),
  };
}

/* ------------------------------------------------------------------ */
/* 解析辅助：真实模型的字段名常有漂移，统一在此兜底                     */
/* ------------------------------------------------------------------ */

export function readConfidence(data: Record<string, unknown>, fallback = 0.78): number {
  return normalizeConfidence(data.confidence, fallback);
}

export function readRisks(data: Record<string, unknown>): string[] {
  return strArray(data.risks);
}

export function readEvidence(data: Record<string, unknown>): Evidence[] {
  return objArray(data.evidence).map((item) => ({
    claim: str(item.claim),
    source: str(item.source, '未标注'),
    reliability: normalizeConfidence(item.reliability, 0.6),
  }));
}

export function readGate(data: Record<string, unknown>, fallback: GateResult): GateResult {
  const raw = str(data.verdict ?? data.gate_result, '').toLowerCase();
  if (['pass', 'revise', 'reject'].includes(raw)) return raw as GateResult;
  return fallback;
}

export function readReviews(data: Record<string, unknown>, key = 'issues'): ReviewItem[] {
  const severityOf = (value: string): ReviewItem['severity'] => {
    const v = value.toLowerCase();
    if (['blocker', 'high', 'critical', '阻断'].includes(v)) return 'blocker';
    if (['major', 'medium', '重要'].includes(v)) return 'major';
    if (['minor', 'low', '建议'].includes(v)) return 'minor';
    return 'info';
  };
  return objArray(data[key]).map((item) => ({
    severity: severityOf(str(item.severity, 'minor')),
    category: str(item.category ?? item.type, '一般问题'),
    detail: str(item.detail ?? item.description),
    suggestion: str(item.suggestion ?? item.fix),
    location: str(item.location || item.snippet, undefined as unknown as string) || undefined,
  }));
}

/**
 * 把结构化产物压平成可读文本，用于：
 *   - 版本 diff
 *   - 关键词检索
 *   - 合规/事实核查的全文扫描
 */
export function contentToText(content: Record<string, unknown>): string {
  const lines: string[] = [];

  const walk = (value: unknown, path: string, depth: number): void => {
    if (depth > 5) return;
    if (typeof value === 'string') {
      if (value.trim()) lines.push(path ? `${path}：${value}` : value);
      return;
    }
    if (typeof value === 'number' || typeof value === 'boolean') {
      lines.push(`${path}: ${String(value)}`);
      return;
    }
    if (Array.isArray(value)) {
      value.forEach((item, index) => walk(item, path ? `${path}[${index + 1}]` : `${index + 1}`, depth + 1));
      return;
    }
    if (value && typeof value === 'object') {
      for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
        walk(child, path ? `${path}.${key}` : key, depth + 1);
      }
    }
  };

  walk(content, '', 1);
  return lines.join('\n');
}

export { num, obj, objArray, normalizeConfidence, normalizeScore, str, strArray };
