import { getConfig } from '../config.ts';
import type { LLMSettings } from '../config.ts';
import { createLogger } from '../logger.ts';
import { MockProvider } from './mock.ts';
import { OpenAICompatibleProvider } from './openai.ts';
import type { LLMProvider, LLMRequest, LLMResponse } from './types.ts';

const log = createLogger('llm');
const mock = new MockProvider();

/** 本地推理服务通常不需要密钥，这里放宽判定。 */
function needsApiKey(baseUrl: string): boolean {
  return /api\.openai\.com|dashscope|volces|moonshot|deepseek\.com/i.test(baseUrl);
}

export function resolveProvider(settings?: LLMSettings): LLMProvider {
  const cfg = settings ?? getConfig().llm;
  if (cfg.provider !== 'openai') return mock;

  const baseUrl = cfg.baseUrl?.trim();
  if (!baseUrl) return mock;
  if (needsApiKey(baseUrl) && !cfg.apiKey) {
    log.warn('LLM_PROVIDER=openai 但未配置 API Key，已回退到内置离线引擎');
    return mock;
  }
  try {
    return new OpenAICompatibleProvider(cfg);
  } catch (error) {
    log.error('初始化 OpenAI 兼容提供方失败，回退到离线引擎', error);
    return mock;
  }
}

const MAX_ATTEMPTS = 3;

/**
 * 统一调用入口，内置两级容错：
 *   1. 网络/5xx/超时 → 指数退避重试（最多 3 次）
 *   2. 仍然失败 → 降级到内置离线引擎，保证流程不中断（plan.md D10 降级策略）
 */
export async function chat(request: LLMRequest): Promise<LLMResponse> {
  const provider = resolveProvider();
  if (provider.simulated) return mock.chat(request);

  let lastError: unknown = null;
  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      return await provider.chat(request);
    } catch (error) {
      lastError = error;
      const message = error instanceof Error ? error.message : String(error);
      const retriable = /HTTP (429|5\d\d)|abort|timeout|network|fetch failed|ECONN/i.test(message);
      log.warn(`[${request.purpose}] 第 ${attempt}/${MAX_ATTEMPTS} 次调用失败：${message}`);
      if (!retriable || attempt === MAX_ATTEMPTS) break;
      await new Promise((resolve) => setTimeout(resolve, 400 * 2 ** (attempt - 1)));
    }
  }

  const reason = lastError instanceof Error ? lastError.message : String(lastError);
  log.error(`[${request.purpose}] 真实模型不可用，降级到内置离线引擎：${reason}`);
  const fallback = await mock.chat(request);
  return { ...fallback, degraded_reason: reason.slice(0, 200) };
}

export { mock as mockProvider };
export type { LLMProvider, LLMRequest, LLMResponse };
