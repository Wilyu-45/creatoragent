import type { AgentEvent, TaskRecord } from './types.ts';
import { PHASE_LABEL } from './types.ts';

export { PHASE_LABEL };

export const STATUS_LABEL: Record<TaskRecord['status'], string> = {
  running: '执行中',
  awaiting_approval: '待人工裁决',
  completed: '已完成',
  rejected: '已驳回',
  failed: '执行失败',
  cancelled: '已取消',
};

export function statusTone(status: TaskRecord['status']): string {
  switch (status) {
    case 'running':
      return 'tone-run';
    case 'awaiting_approval':
      return 'tone-warn';
    case 'completed':
      return 'tone-ok';
    case 'rejected':
      return 'tone-warn';
    case 'failed':
      return 'tone-bad';
    default:
      return 'tone-idle';
  }
}

export function gateTone(gate: string | null | undefined): string {
  switch (gate) {
    case 'pass':
      return 'tone-ok';
    case 'revise':
      return 'tone-warn';
    case 'reject':
      return 'tone-bad';
    default:
      return 'tone-idle';
  }
}

export function scoreTone(score: number): string {
  if (score >= 85) return 'tone-ok';
  if (score >= 75) return 'tone-info';
  if (score >= 60) return 'tone-warn';
  return 'tone-bad';
}

export function severityTone(severity: string): string {
  switch (severity) {
    case 'blocker':
      return 'tone-bad';
    case 'major':
      return 'tone-warn';
    case 'minor':
      return 'tone-info';
    default:
      return 'tone-idle';
  }
}

export const SEVERITY_LABEL: Record<string, string> = {
  blocker: '阻断',
  major: '重要',
  minor: '建议',
  info: '提示',
};

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '—';
  return `${date.getMonth() + 1}/${date.getDate()} ${date.toLocaleTimeString('zh-CN', { hour12: false }).slice(0, 5)}`;
}

export function formatNumber(value: number | undefined | null, digits = 0): string {
  if (value === undefined || value === null || Number.isNaN(value)) return '—';
  return value.toLocaleString('zh-CN', { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function formatCost(usd: number): string {
  if (!usd) return '$0.0000';
  return `$${usd.toFixed(4)}`;
}

export const EVENT_ICON: Record<AgentEvent['type'], string> = {
  'task.created': '◆',
  'task.status': '●',
  'task.completed': '★',
  'task.failed': '✕',
  'phase.enter': '▶',
  'agent.start': '◇',
  'agent.progress': '·',
  'agent.finish': '✓',
  'gate.decision': '⚖',
  'revision.requested': '↺',
  'approval.required': '⏸',
  'approval.decided': '✔',
  'blackboard.write': '▤',
  log: '·',
};

export function levelTone(level: AgentEvent['level']): string {
  switch (level) {
    case 'error':
      return 'tone-bad';
    case 'warn':
      return 'tone-warn';
    case 'debug':
      return 'tone-idle';
    default:
      return 'tone-info';
  }
}

/** 极简文本 diff：按行输出 keep/added/removed，用于产物版本对比。 */
export interface DiffLine {
  type: 'same' | 'add' | 'del';
  text: string;
}

export function diffLines(before: string, after: string): DiffLine[] {
  const a = before.split('\n');
  const b = after.split('\n');
  const table: number[][] = Array.from({ length: a.length + 1 }, () => new Array<number>(b.length + 1).fill(0));

  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i]![j] = a[i] === b[j] ? table[i + 1]![j + 1]! + 1 : Math.max(table[i + 1]![j]!, table[i]![j + 1]!);
    }
  }

  const out: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      out.push({ type: 'same', text: a[i]! });
      i += 1;
      j += 1;
    } else if (table[i + 1]![j]! >= table[i]![j + 1]!) {
      out.push({ type: 'del', text: a[i]! });
      i += 1;
    } else {
      out.push({ type: 'add', text: b[j]! });
      j += 1;
    }
  }
  while (i < a.length) out.push({ type: 'del', text: a[i++]! });
  while (j < b.length) out.push({ type: 'add', text: b[j++]! });
  return out;
}
