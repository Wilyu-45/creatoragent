/**
 * 从模型输出中稳健地抽取 JSON。
 * 真实模型常见的三种脏输出：
 *   1. ```json ... ``` 代码块
 *   2. 前后带解释性文字
 *   3. 结尾多余逗号
 */
export function extractJson<T>(raw: string): T | null {
  if (!raw) return null;

  const candidates: string[] = [];
  const fence = raw.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fence?.[1]) candidates.push(fence[1]);
  candidates.push(raw);

  for (const candidate of candidates) {
    const trimmed = candidate.trim();
    const direct = tryParse<T>(trimmed);
    if (direct !== null) return direct;

    const start = trimmed.search(/[[{]/);
    if (start === -1) continue;
    const end = findMatchingEnd(trimmed, start);
    if (end === -1) continue;
    const sliced = trimmed.slice(start, end + 1);
    const parsed = tryParse<T>(sliced);
    if (parsed !== null) return parsed;
  }
  return null;
}

function tryParse<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    /* 尝试修复尾随逗号 */
  }
  try {
    const repaired = text.replace(/,\s*([}\]])/g, '$1');
    return JSON.parse(repaired) as T;
  } catch {
    return null;
  }
}

function findMatchingEnd(text: string, start: number): number {
  const open = text[start];
  const close = open === '{' ? '}' : ']';
  let depth = 0;
  let inString = false;
  let escaped = false;

  for (let i = start; i < text.length; i += 1) {
    const ch = text[i];
    if (inString) {
      if (escaped) escaped = false;
      else if (ch === '\\') escaped = true;
      else if (ch === '"') inString = false;
      continue;
    }
    if (ch === '"') {
      inString = true;
      continue;
    }
    if (ch === open) depth += 1;
    else if (ch === close) {
      depth -= 1;
      if (depth === 0) return i;
    }
  }
  return -1;
}

/* ------------------------------------------------------------------ */
/* 宽松取值工具：真实模型字段名/类型可能漂移                            */
/* ------------------------------------------------------------------ */

export function str(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}

export function num(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[^\d.-]/g, ''));
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

export function strArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => str(v)).filter(Boolean);
  if (typeof value === 'string' && value.trim()) {
    return value
      .split(/[\n,，、;；]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

export function objArray(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is Record<string, unknown> => typeof v === 'object' && v !== null);
}

export function obj(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : {};
}

/** 钳制到 [min, max]，并处理 0-1 与 0-100 两套量纲混用。 */
export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function normalizeScore(value: unknown, fallback: number): number {
  const n = num(value, fallback);
  if (n > 0 && n <= 1) return clamp(n * 100, 0, 100);
  return clamp(n, 0, 100);
}

export function normalizeConfidence(value: unknown, fallback = 0.75): number {
  const n = num(value, fallback);
  if (n > 1) return clamp(n / 100, 0, 1);
  return clamp(n, 0, 1);
}
