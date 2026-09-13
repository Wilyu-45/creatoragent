/**
 * 产物结构化内容的取值助手。
 * 真实模型返回的字段常有漂移，这里统一兜底，避免渲染层到处做类型判断。
 */

export function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function asArray(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

export function asRecordArray(value: unknown): Record<string, unknown>[] {
  return asArray(value).map(asRecord);
}

export function asText(value: unknown, fallback = ''): string {
  if (typeof value === 'string') return value;
  if (value === undefined || value === null) return fallback;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
}

export function asNumber(value: unknown, fallback = 0): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function asTextArray(value: unknown): string[] {
  return asArray(value)
    .map((item) => asText(item).trim())
    .filter(Boolean);
}

/** 读取可能为 undefined 的嵌套字段。 */
export function pick(source: unknown, ...keys: string[]): unknown {
  let current: unknown = source;
  for (const key of keys) {
    const record = asRecord(current);
    current = record[key];
  }
  return current;
}
