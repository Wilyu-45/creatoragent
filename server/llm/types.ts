export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface LLMRequest {
  /** 调用用途，形如 `A4.copy`。Mock 引擎据此选择生成器，真实模型据此打点。 */
  purpose: string;
  messages: ChatMessage[];
  /** 结构化上下文，Mock 引擎从中取材 */
  context: Record<string, unknown>;
  temperature?: number;
  maxTokens?: number;
  /** 期望返回 JSON */
  json?: boolean;
}

export interface LLMUsage {
  prompt_tokens: number;
  completion_tokens: number;
}

export interface LLMResponse {
  content: string;
  provider: string;
  model: string;
  usage: LLMUsage;
  latency_ms: number;
  /** true 表示来自内置离线模拟引擎或降级兜底 */
  simulated: boolean;
  /** 降级说明（例如真实模型不可用） */
  degraded_reason?: string;
}

export interface LLMProvider {
  readonly name: string;
  readonly model: string;
  readonly simulated: boolean;
  chat(request: LLMRequest): Promise<LLMResponse>;
}

/** 粗略 token 估算：中文按字符、英文按 4 字符/token。 */
export function estimateTokens(text: string): number {
  if (!text) return 0;
  const cjk = (text.match(/[\u4e00-\u9fff]/g) ?? []).length;
  const rest = text.length - cjk;
  return Math.max(1, Math.round(cjk + rest / 4));
}
