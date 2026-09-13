import type { AgentEvent, BlackboardSnapshot, Brief, TaskRecord } from './types.ts';

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

export interface MetricsView {
  system: Record<string, number> & {
    tokens: { prompt: number; completion: number; cost_usd: number };
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
}

export interface MemoryView {
  stats: {
    total: number;
    capacity: number;
    by_kind: { kind: string; label: string; count: number }[];
    brands: string[];
    tasks: number;
    reused: number;
  };
  kinds: { kind: string; label: string }[];
  cards: MemoryCardView[];
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
