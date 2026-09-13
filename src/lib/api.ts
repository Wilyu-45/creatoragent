import type { AgentEvent, AgentResult, BlackboardSnapshot, Brief, TaskRecord } from './types.ts';

export interface TaskSummary {
  id: string;
  brief: Brief;
  status: TaskRecord['status'];
  phase: TaskRecord['phase'];
  phase_label: string;
  revision_round: number;
  turn_used: number;
  scorecard: TaskRecord['scorecard'];
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  tokens: TaskRecord['tokens'];
  artifact_count: number;
  approval: TaskRecord['approval'];
  error: string | null;
  /** 黑板租约冲突次数 */
  intent_conflicts: number;
  /** 发布排期生效时间 */
  published_at: string | null;
}

export interface AgentMetaView {
  id: string;
  name: string;
  role: string;
  kind: string;
  phase: string;
  produces: string;
  description: string;
  veto: boolean;
  capabilities: string[];
  implemented: boolean;
}

export interface AgentsResponse {
  implemented: AgentMetaView[];
  planned: { id: string; name: string; role: string; description: string }[];
  pipeline: { phase: string; label: string }[];
}

export interface PublicConfigView {
  port: number;
  turnBudget: number;
  maxRevisions: number;
  qualityThreshold: number;
  autoApprove: boolean;
  /** 单任务成本上限（美元），超出即熔断到离线引擎 */
  costBudgetUsd: number;
  /** 单任务 token 上限 */
  tokenBudget: number;
  /** 是否启用 LLM 响应缓存 */
  llmCache: boolean;
  llm: {
    provider: 'mock' | 'openai';
    baseUrl: string;
    model: string;
    temperature: number;
    maxTokens: number;
    timeoutMs: number;
    apiKeySet: boolean;
    apiKeyMasked: string;
  };
}

export interface HealthView {
  ok: boolean;
  time: string;
  config: PublicConfigView;
  provider: { name: string; model: string; simulated: boolean };
}

/** 成本核算视图（/api/metrics → system.cost）。 */
export interface CostView {
  total_cost_usd: number;
  avg_cost_per_task_usd: number;
  budget_usd: number;
  token_budget: number;
  cut_off_tasks: number;
  cached_calls: number;
  /** cost_guard 全局统计：累计熔断任务数与存活账本数 */
  cutOffTasks: number;
  activeLedgers: number;
}

/** 响应缓存视图（/api/metrics → system.cache） */
export interface CacheView {
  entries: number;
  capacity: number;
  ttlSeconds: number;
  hits: number;
  misses: number;
  hitRate: number;
}

/** 黑板租约视图（/api/metrics → system.leases） */
export interface LeaseView {
  active: number;
  conflicts: number;
}

export interface MetricsView {
  system: Record<string, number> & {
    tokens: { prompt: number; completion: number; cost_usd: number };
    cost: CostView;
    cache: CacheView;
    leases: LeaseView;
  };
  providers: { name: string; model: string; simulated: boolean }[];
  agents: {
    id: string;
    name: string;
    runs: number;
    avg_confidence: number;
    avg_latency_ms: number;
    gate_failures: number;
    veto_used: number;
  }[];
}

export interface KnowledgeView {
  lexicon: { category: string; severity: string; law: string; term_count: number; terms: string[] }[];
  industry_rules: { industry: string; rule_count: number; categories: string[] }[];
  channels: { channel: string; format: string; length_hint: string; blocks: string[] }[];
  industries: string[];
}

export interface TaskDetail {
  task: TaskRecord;
  events: AgentEvent[];
  blackboard: BlackboardSnapshot;
}

/** A11 记忆库的一条知识卡片（跨任务存活）。 */
export interface MemoryCardView {
  id: string;
  task_id: string;
  brand: string;
  channel: string;
  industry: string;
  kind: string;
  kind_label: string;
  title: string;
  content: string;
  tags: string[];
  reuse_hint: string;
  revision: number;
  created_at: string;
  hits: number;
}

/** 检索命中：在卡片基础上带分数与命中理由。 */
export interface MemoryHitView extends MemoryCardView {
  score: number;
  reasons: string[];
  /** 卡片年龄（天）与新鲜度 */
  age_days: number;
  fresh: boolean;
}

export interface MemoryView {
  stats: {
    total: number;
    capacity: number;
    by_kind: { kind: string; label: string; count: number }[];
    brands: string[];
    tasks: number;
    reused: number;
    /** 知识新鲜度：过期下线阈值 / 最旧卡片 / 新鲜与陈旧数量 */
    max_age_days: number;
    fresh_days: number;
    oldest_days: number;
    fresh: number;
    stale: number;
  };
  kinds: { kind: string; label: string }[];
  cards: MemoryCardView[];
}

/** 发布排期中的单个渠道条目。 */
export interface PublishScheduleItem {
  order: number;
  channel: string;
  slot: string;
  recommended_slots: string[];
  title: string;
  keywords: string[];
  /** scheduled | published */
  status: string;
  published_at: string | null;
  url: string;
}

export interface PublishScheduleView {
  task_id: string;
  phase: TaskRecord['phase'];
  status: TaskRecord['status'];
  published_at: string | null;
  artifact_id: string | null;
  schedule: PublishScheduleItem[];
}

export interface MarkPublishedView {
  task_id: string;
  published: string[];
  published_at: string;
  artifact_id: string;
  schedule: PublishScheduleItem[];
}

export interface FeedbackView {
  task: TaskSummary;
  actuals: Record<string, unknown>;
  result: AgentResult;
  artifact_id: string | null;
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    let message = `请求失败 (${response.status})`;
    try {
      const parsed = JSON.parse(detail) as { error?: string };
      if (parsed.error) message = parsed.error;
    } catch {
      if (detail) message = detail.slice(0, 200);
    }
    throw new Error(message);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<HealthView>('/api/health'),
  agents: () => request<AgentsResponse>('/api/agents'),
  knowledge: () => request<KnowledgeView>('/api/knowledge'),
  memory: () => request<MemoryView>('/api/memory'),
  searchMemory: (query: string, topK = 5) =>
    request<{ query: string; hits: MemoryHitView[] }>('/api/memory/search', {
      method: 'POST',
      body: JSON.stringify({ query, topK }),
    }),
  metrics: () => request<MetricsView>('/api/metrics'),
  settings: () => request<PublicConfigView>('/api/settings'),
  updateSettings: (patch: Record<string, unknown>) =>
    request<PublicConfigView>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  listTasks: () => request<{ tasks: TaskSummary[] }>('/api/tasks'),
  getTask: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),
  createTask: (brief: Partial<Brief>, autoApprove?: boolean) =>
    request<{ task: TaskRecord }>('/api/tasks', {
      method: 'POST',
      body: JSON.stringify({ brief, autoApprove }),
    }),
  decide: (id: string, decision: 'approve' | 'revise' | 'reject', comment: string) =>
    request<{ task: TaskSummary }>(`/api/tasks/${id}/decide`, {
      method: 'POST',
      body: JSON.stringify({ decision, comment }),
    }),
  publishSchedule: (id: string) => request<PublishScheduleView>(`/api/tasks/${id}/publish`),
  markPublished: (id: string, channel = '', url = '') =>
    request<MarkPublishedView>(`/api/tasks/${id}/publish`, {
      method: 'POST',
      body: JSON.stringify({ channel, url }),
    }),
  submitFeedback: (
    id: string,
    payload: { channel?: string; window?: string; url?: string; metrics: Record<string, number | string> },
  ) =>
    request<FeedbackView>(`/api/tasks/${id}/feedback`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
};

/** 订阅任务实时事件流（SSE），返回取消订阅函数。 */
export function subscribeTask(
  taskId: string,
  since: number,
  onEvent: (event: AgentEvent) => void,
  onError?: () => void,
): () => void {
  const source = new EventSource(`/api/tasks/${taskId}/events?since=${since}`);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as AgentEvent);
    } catch {
      /* 忽略无法解析的事件 */
    }
  };
  source.onerror = () => onError?.();
  return () => source.close();
}
