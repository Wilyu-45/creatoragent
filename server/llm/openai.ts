import type { LLMSettings } from '../config.ts';
import { estimateTokens } from './types.ts';
import type { LLMProvider, LLMRequest, LLMResponse } from './types.ts';

interface ChatCompletionResponse {
  choices?: { message?: { content?: string } }[];
  usage?: { prompt_tokens?: number; completion_tokens?: number };
  model?: string;
}

/**
 * 任意 OpenAI 兼容 /chat/completions 接口。
 * 已验证可对接：OpenAI、DeepSeek、通义千问、豆包、Moonshot、vLLM、Ollama、LM Studio。
 *
 * 刻意不使用 response_format=json_object —— 部分兼容实现不支持该字段会直接报错，
 * 统一改为在提示词中约束 + 客户端稳健解析（见 json.ts）。
 */
export class OpenAICompatibleProvider implements LLMProvider {
  readonly name = 'openai';
  readonly simulated = false;
  readonly model: string;

  private readonly baseUrl: string;
  private readonly apiKey: string;
  private readonly temperature: number;
  private readonly maxTokens: number;
  private readonly timeoutMs: number;

  constructor(settings: LLMSettings) {
    this.model = settings.model;
    this.baseUrl = settings.baseUrl.replace(/\/+$/, '');
    this.apiKey = settings.apiKey;
    this.temperature = settings.temperature;
    this.maxTokens = settings.maxTokens;
    this.timeoutMs = settings.timeoutMs;
  }

  async chat(request: LLMRequest): Promise<LLMResponse> {
    const started = Date.now();
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), this.timeoutMs);

    try {
      const response = await fetch(`${this.baseUrl}/chat/completions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${this.apiKey}`,
        },
        body: JSON.stringify({
          model: this.model,
          messages: request.messages,
          temperature: request.temperature ?? this.temperature,
          max_tokens: request.maxTokens ?? this.maxTokens,
          stream: false,
        }),
        signal: controller.signal,
      });

      if (!response.ok) {
        const detail = await response.text().catch(() => '');
        throw new Error(`HTTP ${response.status} ${response.statusText} ${detail.slice(0, 300)}`);
      }

      const data = (await response.json()) as ChatCompletionResponse;
      const content = data.choices?.[0]?.message?.content ?? '';
      if (!content.trim()) throw new Error('模型返回空内容');

      const promptText = request.messages.map((m) => m.content).join('\n');
      return {
        content,
        provider: this.name,
        model: data.model ?? this.model,
        usage: {
          prompt_tokens: data.usage?.prompt_tokens ?? estimateTokens(promptText),
          completion_tokens: data.usage?.completion_tokens ?? estimateTokens(content),
        },
        latency_ms: Date.now() - started,
        simulated: false,
      };
    } finally {
      clearTimeout(timer);
    }
  }
}
