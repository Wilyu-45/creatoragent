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
  /** 归属租户（启用鉴权时按 token 隔离） */
  tenant: string;
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
  /** 是否已通过 CREATOR_API_TOKENS 开启 API 鉴权 */
  authRequired: boolean;
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
  /** A11 记忆库向量化设置 */
  embedding: {
    provider: 'local' | 'openai';
    baseUrl: string;
    model: string;
    dim: number;
    weight: number;
    apiKeySet: boolean;
    apiKeyMasked: string;
  };
  /** 多平台发布投递设置 */
  publish: {
    webhookUrl: string;
    retry: number;
    autoDispatch: boolean;
    tickSeconds: number;
    webhookSet: boolean;
  };
  /** LLM-as-a-Judge 评估设置 */
  judge: {
    mode: 'off' | 'advisory' | 'blocking';
    provider: 'offline' | 'llm';
    model: string;
    passThreshold: number;
    weight: number;
    rubric: string;
  };
  /** 数字人渲染接入样例设置（sample 内置引擎 / http 适配网关） */
  digitalHuman: {
    provider: string;
    apiUrl: string;
    avatar: string;
    apiKeySet: boolean;
    apiKeyMasked: string;
    timeoutMs: number;
  };
  /** 分布式追踪设置（OTLP 导出为只读配置，来自环境变量） */
  tracing: {
    otlpEndpoint: string;
    serviceName: string;
    otlpConfigured: boolean;
    /** 采样器：parentbased_always_on / parentbased_traceidratio / … */
    sampler: string;
    /** traceidratio 的采样比例（0-1） */
    sampleRatio: number;
  };
}

export interface HealthView {
  ok: boolean;
  time: string;
  config: PublicConfigView;
  provider: { name: string; model: string; simulated: boolean };
  /** 断点续跑后端：sqlite = 可跨重启；memory = 重启后无法续跑 */
  checkpointer: { kind: 'sqlite' | 'memory' | string; error: string };
  /** OTLP 导出状态（未配置时进程内追踪仍完整可用） */
  otlp: {
    enabled: boolean;
    exported: number;
    failed: number;
    error: string;
    endpoint: string;
  };
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

/** 评估维度明细（LLM-as-a-Judge 的一个评分项）。 */
export interface JudgeAxisView {
  key: string;
  label: string;
  /** 0–5 分 */
  score: number;
  weight: number;
  rationale: string;
  evidence: string[];
}

/** 一次评估的结论（/api/evaluations 与门禁事件里的 judge 字段）。 */
export interface JudgeReportView {
  total: number;
  verdict: 'pass' | 'review' | 'reject';
  mode: string;
  model: string;
  summary: string;
  axes: JudgeAxisView[];
  issues: string[];
  suggestions: string[];
  confidence: number;
  fallback: boolean;
  fallback_reason: string;
  rubric: string;
  kind: string;
  revision: number;
}

/** 落库后的评估记录：在报告基础上带任务与来源信息。 */
export interface EvaluationRecordView extends JudgeReportView {
  id: string;
  task_id: string;
  tenant: string;
  created_at: string;
  trigger: string;
  brand: string;
  channel: string;
  industry: string;
}

/** 评估聚合指标（/api/metrics → system.judge 与 /api/evaluations → stats）。 */
export interface JudgeMetricsView {
  total: number;
  tasks: number;
  avg_total: number;
  pass_rate: number;
  verdicts: Record<string, number>;
  modes: Record<string, number>;
  fallbacks: number;
  axis_avg: { key: string; label: string; score: number }[];
  latest_at: string | null;
  mode?: string;
}

export interface EvaluationsView {
  stats: JudgeMetricsView;
  config: {
    mode: PublicConfigView['judge']['mode'];
    provider: PublicConfigView['judge']['provider'];
    passThreshold: number;
    weight: number;
    rubric: string;
  };
  records: EvaluationRecordView[];
}

/* ------------------------------------------------------------------ */
/* 黄金数据集（回归门禁）                                              */
/* ------------------------------------------------------------------ */

/** 一条黄金用例。 */
export interface GoldenCaseView {
  id: string;
  note: string;
  brand: string;
  channel: string;
  industry: string;
  objective: string;
  audience: string;
  keywords: string[];
  constraints: string[];
  brief: Brief;
}

/** 一条用例的运行结果（已压缩为可比较的标量）。 */
export interface GoldenResultView {
  id: string;
  status: string;
  quality_score: number;
  judge_total: number;
  judge_final_total: number;
  axis_scores: Record<string, number>;
  revision_round: number;
  artifact_count: number;
  fact_accuracy: number;
  brand_consistency: number;
  compliance_verdicts: string[];
  first_round_blocked: boolean;
  predicted_ctr: number;
  duration_ms: number;
  task_id: string;
  error: string;
}

/** 基线 vs 当前的单条对比结论。 */
export interface GoldenDiffView {
  id: string;
  verdict: 'ok' | 'improved' | 'regressed' | 'new' | 'missing' | 'failed';
  deltas: Record<string, number>;
  reasons: string[];
}

export interface GoldenComparisonView {
  ok: boolean;
  tolerance: number;
  rubric: string;
  baseline_rubric: string | null;
  rubric_mismatch: boolean;
  baseline_updated_at: string | null;
  baseline_engine: string | null;
  /** true 表示这是一次部分运行，结论只对 scope 内用例成立 */
  partial?: boolean;
  scope?: string[] | null;
  counts: Record<string, number>;
  differences: GoldenDiffView[];
}

export interface GoldenView {
  cases: GoldenCaseView[];
  baseline: {
    updated_at: string | null;
    rubric: string | null;
    engine: string | null;
    note: string | null;
    cases: Record<string, GoldenResultView>;
  };
  coverage: {
    total: number;
    channels: string[];
    industries: string[];
    matrix: { channel: string; industries: string[]; cases: string[] }[];
    uncovered_channels: string[];
  };
  rubric: string;
  tolerance: number;
  state: {
    running: boolean;
    started_at: string;
    finished_at: string;
    total: number;
    completed: number;
    current: string;
    scope: string[] | null;
    error: string;
    results: GoldenResultView[];
  };
  comparison?: GoldenComparisonView;
}

export interface MetricsView {
  system: Record<string, number> & {
    tokens: { prompt: number; completion: number; cost_usd: number };
    cost: CostView;
    cache: CacheView;
    leases: LeaseView;
    judge: JudgeMetricsView;
    tracing: TracingMetricsView;
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

/* ------------------------------------------------------------------ */
/* 分布式追踪（span 树）                                               */
/* ------------------------------------------------------------------ */

/** 一个追踪片段（与 OpenTelemetry 的 span 语义一致）。 */
export interface SpanView {
  trace_id: string;
  span_id: string;
  parent_span_id: string | null;
  name: string;
  /** internal | client | server | producer | consumer */
  kind: string;
  agent_id: string | null;
  started_at: string;
  finished_at: string;
  duration_ms: number;
  /** ok | error | unset */
  status: string;
  status_message: string;
  attributes: Record<string, unknown>;
  children: number;
  /** 自身耗时（扣除子 span）占 trace 总耗时的比例 */
  self_ratio: number;
}

export interface TraceView {
  task_id: string;
  trace_id: string | null;
  started_at?: string;
  finished_at?: string;
  duration_ms?: number;
  span_count?: number;
  roots?: string[];
  spans: SpanView[];
  summary: {
    span_count: number;
    duration_ms: number;
    by_name: {
      name: string;
      kind: string;
      count: number;
      total_ms: number;
      max_ms: number;
      avg_ms?: number;
      share?: number;
    }[];
  };
  export?: { path: string; format: string; file: string };
  notes?: string;
}

/** 全局追踪聚合（/api/metrics → system.tracing）。 */
export interface TracingMetricsView {
  traces: number;
  spans: number;
  errors: number;
  by_name: {
    name: string;
    kind: string;
    count: number;
    total_ms: number;
    max_ms: number;
    avg_ms: number;
    errors: number;
  }[];
  exportPath: string;
  exportFormat: string;
  exportFailures: number;
  /** 采样只作用于导出面（OTLP + 落盘），进程内轨迹始终完整 */
  sampling: {
    sampler: string;
    sampleRatio: number;
    traces_sampled: number;
    traces_unsampled: number;
  };
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
  /** 归属租户（启用鉴权时按租户隔离，未启用时固定 default） */
  tenant: string;
}

/** 检索命中：在卡片基础上带分数与命中理由。 */
export interface MemoryHitView extends MemoryCardView {
  score: number;
  reasons: string[];
  /** 卡片年龄（天）与新鲜度 */
  age_days: number;
  fresh: boolean;
  /** 语义分量的余弦相似度（0 表示未启用向量检索） */
  vector_score: number;
}

export interface MemoryView {
  stats: {
    total: number;
    /** 全局卡片数（跨租户计数，不含内容） */
    global_total: number;
    capacity: number;
    /** 当前租户（未启用鉴权时固定为 `default`） */
    tenant: string | null;
    /** 库中已出现过的租户名 */
    tenants: string[];
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
    /** 向量检索方式与已建索引的卡片数 */
    embedding: { provider: string; model: string; dim: number; weight: number };
    vector_indexed: number;
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
  /** 由建议时段换算出的到期时间（用于自动投递） */
  due_at: string | null;
  /** pending | dispatched | skipped | failed */
  dispatch_status: string;
  attempts: number;
  last_error: string;
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

/** 自动投递结果（POST /api/tasks/{id}/publish/dispatch）。 */
export interface PublishDispatchView {
  task_id: string;
  dispatched: string[];
  failed: string[];
  skipped: string[];
  published_at: string | null;
  artifact_id: string;
  schedule: PublishScheduleItem[];
}

/** 跨任务待发布队列中的一条。 */
export interface PublishQueueItem {
  task_id: string;
  brand: string;
  product: string;
  channel: string;
  slot: string;
  due_at: string | null;
  dispatch_status: string;
  attempts: number;
  last_error: string;
  title: string;
  task_status: TaskRecord['status'];
}

export interface PublishQueueView {
  total: number;
  items: PublishQueueItem[];
}

/* ------------------------------------------------------------------ */
/* 数字人渲染（开发样例）                                               */
/* ------------------------------------------------------------------ */

/** 渲染清单中的一段（由 video_script 分镜透传而来）。 */
export interface DigitalHumanSegment {
  shot: number;
  role: string;
  start_second: number;
  end_second: number;
  duration_seconds: number;
  voiceover: string;
  subtitle: string;
  visual: string;
  camera: string;
  /** 是否有口播台词（无口播分镜数字人仅作画面演出） */
  spoken: boolean;
}

/** 数字人渲染作业（sample 内置引擎按流逝时间惰性推进；http 走远端网关）。 */
export interface DigitalHumanJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | http */
  provider: string;
  avatar: string;
  /** queued | rendering | done | failed */
  status: string;
  progress: number;
  script_artifact_id: string;
  channel: string;
  duration_seconds: number;
  shot_count: number;
  render_seconds: number;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  video_url: string;
  error: string;
  attempts: number;
  remote_id: string;
  manifest: {
    channel: string;
    aspect_ratio: string;
    duration_seconds: number;
    shot_count: number;
    hook: string;
    cta: string;
    segments: DigitalHumanSegment[];
    warnings: string[];
  };
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface DigitalHumanView {
  task_id: string;
  has_video_script: boolean;
  jobs: DigitalHumanJobView[];
}

const TOKEN_KEY = 'creator-api-token';

/** 读取本地保存的 API Token（后端启用 ``CREATOR_API_TOKENS`` 时需要）。 */
export function getApiToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? '';
  } catch {
    return '';
  }
}

/** 保存 / 清除 API Token（传空串表示清除）。 */
export function setApiToken(token: string): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* 隐私模式下 localStorage 不可用，忽略 */
  }
}

function authHeaders(): Record<string, string> {
  const token = getApiToken();
  return token ? { 'X-API-Token': token } : {};
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...authHeaders(),
      ...((init?.headers as Record<string, string>) ?? {}),
    },
  });
  if (!response.ok) {
    const detail = await response.text().catch(() => '');
    let message = `请求失败 (${response.status})`;
    try {
      const parsed = JSON.parse(detail) as { detail?: string; error?: string };
      const text = parsed.detail ?? parsed.error;
      if (text) message = text;
    } catch {
      if (detail) message = detail.slice(0, 200);
    }
    if (response.status === 401) message = 'API Token 无效或缺失，请在「运行时设置」中填写';
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
  evaluations: (taskId?: string, limit = 20) =>
    request<EvaluationsView>(
      `/api/evaluations?limit=${limit}${taskId ? `&taskId=${encodeURIComponent(taskId)}` : ''}`,
    ),
  evaluateTask: (id: string, provider?: 'offline' | 'llm') =>
    request<{ task_id: string; record: EvaluationRecordView }>(`/api/tasks/${id}/evaluate`, {
      method: 'POST',
      body: JSON.stringify(provider ? { provider } : {}),
    }),
  golden: () => request<GoldenView>('/api/golden'),
  runGolden: (payload: { limit?: number; caseIds?: string[] } = {}) =>
    request<{ started: boolean; state: GoldenView['state'] }>('/api/golden/run', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  resetGolden: () =>
    request<{ ok: boolean; state: GoldenView['state'] }>('/api/golden/reset', { method: 'POST' }),
  settings: () => request<PublicConfigView>('/api/settings'),
  updateSettings: (patch: Record<string, unknown>) =>
    request<PublicConfigView>('/api/settings', { method: 'PUT', body: JSON.stringify(patch) }),
  listTasks: () => request<{ tasks: TaskSummary[] }>('/api/tasks'),
  getTask: (id: string) => request<TaskDetail>(`/api/tasks/${id}`),
  trace: (id: string) => request<TraceView>(`/api/tasks/${id}/trace`),
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
  dispatchPublish: (id: string, channel = '', force = true) =>
    request<PublishDispatchView>(`/api/tasks/${id}/publish/dispatch`, {
      method: 'POST',
      body: JSON.stringify({ channel, force }),
    }),
  publishQueue: (dueOnly = false) =>
    request<PublishQueueView>(`/api/publish/queue?dueOnly=${dueOnly ? 'true' : 'false'}`),
  tickPublish: () => request<{ due: number; dispatched: string[]; failed: string[] }>('/api/publish/tick', {
    method: 'POST',
  }),
  digitalHuman: (id: string) => request<DigitalHumanView>(`/api/tasks/${id}/digital-human`),
  createDigitalHuman: (id: string, payload: { avatar?: string; provider?: string } = {}) =>
    request<{ task_id: string; job: DigitalHumanJobView }>(`/api/tasks/${id}/digital-human`, {
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
  // EventSource 无法自定义请求头，鉴权开启时用 query 参数携带 token
  const token = getApiToken();
  const suffix = token ? `&token=${encodeURIComponent(token)}` : '';
  const source = new EventSource(`/api/tasks/${taskId}/events?since=${since}${suffix}`);
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
