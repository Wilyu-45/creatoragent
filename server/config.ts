import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';

/* ------------------------------------------------------------------ */
/* 极简 .env 加载器（避免引入 dotenv 依赖）                            */
/* ------------------------------------------------------------------ */

function loadEnvFile(file: string): void {
  if (!existsSync(file)) return;
  const raw = readFileSync(file, 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    const key = trimmed.slice(0, eq).trim();
    let value = trimmed.slice(eq + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = value;
  }
}

export const ROOT_DIR = path.resolve(import.meta.dirname, '..');
loadEnvFile(path.join(ROOT_DIR, '.env'));

export const DATA_DIR = path.join(ROOT_DIR, 'data');
export const TASK_DIR = path.join(DATA_DIR, 'tasks');
export const BLACKBOARD_FILE = path.join(DATA_DIR, 'blackboard.json');
export const SETTINGS_FILE = path.join(DATA_DIR, 'settings.json');

/* ------------------------------------------------------------------ */
/* 运行时可配置项                                                       */
/* ------------------------------------------------------------------ */

export type LLMProviderName = 'mock' | 'openai';

export interface LLMSettings {
  provider: LLMProviderName;
  baseUrl: string;
  apiKey: string;
  model: string;
  temperature: number;
  maxTokens: number;
  timeoutMs: number;
}

export interface RuntimeConfig {
  port: number;
  turnBudget: number;
  maxRevisions: number;
  qualityThreshold: number;
  autoApprove: boolean;
  llm: LLMSettings;
}

function num(value: string | undefined, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

function bool(value: string | undefined, fallback: boolean): boolean {
  if (value === undefined) return fallback;
  return ['1', 'true', 'yes', 'on'].includes(value.toLowerCase());
}

const config: RuntimeConfig = {
  port: num(process.env.PORT, 8787),
  turnBudget: num(process.env.TURN_BUDGET, 25),
  maxRevisions: num(process.env.MAX_REVISIONS, 2),
  qualityThreshold: num(process.env.QUALITY_THRESHOLD, 75),
  autoApprove: bool(process.env.AUTO_APPROVE, false),
  llm: {
    provider: (process.env.LLM_PROVIDER as LLMProviderName) || 'mock',
    baseUrl: process.env.OPENAI_BASE_URL || 'https://api.openai.com/v1',
    apiKey: process.env.OPENAI_API_KEY || '',
    model: process.env.OPENAI_MODEL || 'gpt-4o-mini',
    temperature: num(process.env.LLM_TEMPERATURE, 0.7),
    maxTokens: num(process.env.LLM_MAX_TOKENS, 2048),
    timeoutMs: num(process.env.LLM_TIMEOUT_MS, 60_000),
  },
};

export function getConfig(): RuntimeConfig {
  return config;
}

export function updateConfig(
  patch: Omit<Partial<RuntimeConfig>, 'llm'> & { llm?: Partial<LLMSettings> },
): RuntimeConfig {
  if (patch.turnBudget !== undefined) config.turnBudget = patch.turnBudget;
  if (patch.maxRevisions !== undefined) config.maxRevisions = patch.maxRevisions;
  if (patch.qualityThreshold !== undefined) config.qualityThreshold = patch.qualityThreshold;
  if (patch.autoApprove !== undefined) config.autoApprove = patch.autoApprove;
  if (patch.llm) Object.assign(config.llm, patch.llm);
  return config;
}

/** 对外输出时隐藏密钥明文。 */
export function publicConfig(): RuntimeConfig & { llm: LLMSettings & { apiKeySet: boolean; apiKeyMasked: string } } {
  const masked = config.llm.apiKey
    ? `${config.llm.apiKey.slice(0, 4)}${'*'.repeat(Math.max(0, config.llm.apiKey.length - 8))}${config.llm.apiKey.slice(-4)}`
    : '';
  return {
    ...config,
    llm: {
      ...config.llm,
      apiKey: '',
      apiKeySet: config.llm.apiKey.length > 0,
      apiKeyMasked: masked,
    },
  };
}
