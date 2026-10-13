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
  /** 服务监听地址（启动期参数，改后随快照持久化、下次启动生效） */
  host: string;
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
  /** 素材研读（document_digest）单任务 LLM 调用配额 */
  digestMaxCalls: number;
  /** 是否已通过 CREATOR_API_TOKENS 开启 API 鉴权 */
  authRequired: boolean;
  llm: {
    provider: 'mock' | 'openai';
    baseUrl: string;
    model: string;
    temperature: number;
    maxTokens: number;
    timeoutMs: number;
    /** 多模态输入总开关（按智能体实际生效 provider 单点判定） */
    vision: boolean;
    /** 单次请求随附图片上限 */
    visionMaxImages: number;
    /** 深度思考模式（enabled / disabled，网关不支持时忽略） */
    thinking: 'enabled' | 'disabled';
    apiKeySet: boolean;
    apiKeyMasked: string;
    /** 每智能体模型覆盖（键 A1–A11；空字段=继承全局，密钥只回掩码） */
    agentModels: Record<
      string,
      { model: string; baseUrl: string; apiKeySet: boolean; apiKeyMasked: string }
    >;
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
  /** 图片生成接入样例设置（sample 清单 / openai 协议 / local 本地图生网关） */
  imageGen: {
    provider: string;
    baseUrl: string;
    model: string;
    size: string;
    maxImages: number;
    apiKeySet: boolean;
    apiKeyMasked: string;
    timeoutMs: number;
    configured: boolean;
  };
  /** 视频生成接入样例设置（sample 清单 / http 适配网关，含成本熔断预算口径） */
  videoGen: {
    provider: string;
    apiUrl: string;
    apiKeySet: boolean;
    apiKeyMasked: string;
    timeoutMs: number;
  };
  /** 视频理解接入样例设置（sample 占位骨架 / real 整集交给原生视频理解网关，一集一次调用） */
  videoUnderstand: {
    provider: string;
    apiUrl: string;
    apiKey: string;
    apiKeySet: boolean;
    apiKeyMasked: string;
    timeoutMs: number;
    configured: boolean;
  };
  /** 全自动配音设置（真实功能：引擎逐句合成真音频；sample 为离线联调通道） */
  dubbing: {
    provider: 'sample' | 'auto' | 'sapi' | 'sovits' | 'elevenlabs' | 'real';
    apiUrl: string;
    apiKey: string;
    apiKeySet: boolean;
    apiKeyMasked: string;
    /** sovits：自建 GPT-SoVITS/CosyVoice 克隆服务地址 */
    sovitsBaseUrl: string;
    /** sovits：文本语种（text_lang） */
    sovitsLanguage: string;
    /** elevenlabs：API 地址与密钥 */
    elevenlabsBaseUrl: string;
    elevenlabsApiKey: string;
    elevenlabsApiKeySet: boolean;
    /** elevenlabs：合成模型 */
    elevenlabsModel: string;
    /** sapi：限定使用的系统音色（逗号分隔，空 = 全部已安装） */
    sapiVoices: string;
    /** 各真实引擎是否已配置（auto 择优的依据，面板展示可用性提示） */
    engines: { elevenlabs: boolean; sovits: boolean; real: boolean };
    timeoutMs: number;
    /** 单作业最多配音的台词行数（超出直接拒绝，防整本剧本一次烧出去） */
    maxLines: number;
    /** 时间轴估算语速（字/秒），合成完成后按音频实长重建 */
    charsPerSecond: number;
    configured: boolean;
  };
  /** 联网检索网关设置（web_search / page_fetch 工具的上游） */
  search: {
    provider: 'none' | 'http';
    apiUrl: string;
    apiKeySet: boolean;
    apiKeyMasked: string;
    maxResults: number;
    timeoutMs: number;
    /** 是否抓取页面正文（page_fetch 路径） */
    fetchPages: boolean;
    maxPages: number;
    siteUrls: string[];
    configured: boolean;
  };
  /** 桌面宠物样例设置（sample 标准库画帧 / imagegen 复用图片通道逐帧出真图） */
  petGen: {
    provider: string;
    frameSize: number;
    maxFrames: number;
    timeoutMs: number;
    configured: boolean;
  };
  /** 创作技能提炼设置（rules 零依赖量化 / llm 复用 LLM_* 网关做风格判断） */
  skillGen: {
    provider: 'rules' | 'llm';
    /** 单次作业最多纳入几份作品 */
    maxWorks: number;
    /** 当前默认通道是否可用：llm 需 LLM_* 已配真实网关，否则界面提示改走 rules */
    configured: boolean;
  };
  /** 灵感冲浪设置（像真人上网刷信息/看视频 → 消化成灵感卡 → 沉淀跨任务 idea 库） */
  surf: {
    provider: 'sample' | 'auto' | 'browser' | 'http';
    headless: boolean;
    /** browser 通道只访问这些域名白名单站点 */
    allowedDomains: string[];
    /** browser 通道的起点站 URL */
    seedSites: string[];
    /** 自主冲浪按这些主题各囤一轮 */
    topics: string[];
    maxPages: number;
    scrollRounds: number;
    settleMs: number;
    timeoutMs: number;
    queriesPerTopic: number;
    cardsPerSession: number;
    /** 灵感池最多沉淀多少张卡（超出裁最旧的） */
    poolMax: number;
    autoEnabled: boolean;
    tickSeconds: number;
    /** 看懂视频开关（仅 browser 通道真播放+截图抽帧→交 VLM） */
    watchVideos: boolean;
    /** 每个视频播放中截取的画面帧数 */
    videoFrames: number;
    /** 抽帧间隔毫秒 */
    videoFrameIntervalMs: number;
    /** 每组交 VLM 的截图张数（真人一段一段看：每组一次多模态调用，多组再汇总） */
    vlmGroupFrames: number;
    /** 单次会话最多看懂几个视频 */
    maxVideosPerSession: number;
    /** 是否抓取视频页的标题/简介/评论文字 */
    scrapeComments: boolean;
    /** 每个视频页最多收集多少条评论 */
    maxComments: number;
    /** 截图只截播放器画面元素（false = 整页截图） */
    cropToVideo: boolean;
    /** 像真人一样从信息流点进视频页在线看 */
    clickIntoVideos: boolean;
    /** 真人节奏：每轮滚动幅度与停留随机浮动（false = 等距滚 + 固定等的机器节奏） */
    humanPace: boolean;
    /** 用起点站自己的搜索框搜主题（没搜索框就如实不搜） */
    searchOnSeeds: boolean;
    /** 信息流多跳：每页点进几条普通内容页继续读（0 = 不点进） */
    hopsPerPage: number;
    /** 列表翻页：点站点自己的「下一页/加载更多」，一个列表最多刷几页（1 = 只看第一页） */
    listPages: number;
    /** 正文页也点开「展开全文」并逐条抓评论区（抓不到就空，绝不编造） */
    pageComments: boolean;
    /** 开新页之前的停顿基准毫秒（真人节奏下按它随机浮动，0 = 不停顿） */
    pageGapMs: number;
    /** 挑条目先扫标题：同域条目按「标题认不认得出主题词」排序点进（关 = 退回列表原顺序） */
    topicFocus: boolean;
    /** 站内搜索结果不贴主题时，把关键词说短再搜几次（0 = 整句搜一次就完） */
    searchRetries: number;
    /** 打字时看站点自己弹出的搜索联想：只点认得出本次主题词的那条（关 = 一律按原词回车） */
    useSuggestions: boolean;
    /** 一次最多读回几条联想候选 */
    suggestionsMax: number;
    /** 读到门口就退出来：认出登录墙/订阅墙/人机验证/维护页时如实标注，不绕墙也不把墙话当正文（关 = 照旧当正文读） */
    avoidWalls: boolean;
    /** 跨会话记住读过哪些页（下一轮挑条目就跳过它们改读新的；关 = 每轮都当第一次刷） */
    rememberRead: boolean;
    /** 每个租户最多记多少条读过的页（超了就先忘最旧的，忘了的下轮会重新读） */
    historyMax: number;
    /** Playwright 是否已安装（browser 通道能否真跑） */
    browserAvailable: boolean;
    /** 在线看视频的三个前置是否齐备（Playwright + 白名单 + 起点站/指定视频链接） */
    watchReady: boolean;
    /** LLM 网关是否声明支持图像输入（LLM_VISION）；否则画面只如实标「未看懂」 */
    visionConfigured: boolean;
    /** 当前默认通道是否可用（browser 需装 Playwright + 配白名单；http 需配搜索网关） */
    configured: boolean;
  };
  /** 分布式追踪设置（进程内追踪始终完整；OTLP 只作用于导出面） */
  tracing: {
    otlpEndpoint: string;
    serviceName: string;
    otlpConfigured: boolean;
    /** 是否已配置 OTLP 鉴权头（值不回传） */
    otlpHeadersSet: boolean;
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
  provider: { name: string; model: string; simulated: boolean; agentOverrides?: number };
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
  providers: { name: string; model: string; simulated: boolean; agents?: string[] }[];
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

/* ------------------------------------------------------------------ */
/* 图片 / 视频生成（开发样例）                                          */
/* ------------------------------------------------------------------ */

/** 图片生成清单中的一条（由 visual_brief 的 image_prompts 透传而来）。 */
export interface ImageGenSegment {
  index: number;
  id: string;
  usage: string;
  scene: string;
  prompt: string;
  negative: string;
  aspect_ratio: string;
}

/** 图片生成作业产出的一张图（sample 为模拟地址；local 为 assets/ 引用；openai 为网关 URL）。 */
export interface ImageGenOutput {
  index: number;
  id: string;
  url: string;
  b64?: boolean;
}

export interface ImageGenJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | openai | local */
  provider: string;
  /** queued | generating | done | failed */
  status: string;
  progress: number;
  visual_artifact_id: string;
  size: string;
  requested: number;
  render_seconds: number;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  images: ImageGenOutput[];
  error: string;
  attempts: number;
  manifest: {
    provider: string;
    size: string;
    requested: number;
    segments: ImageGenSegment[];
    warnings: string[];
  };
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface ImageGenView {
  task_id: string;
  has_visual_brief: boolean;
  jobs: ImageGenJobView[];
}

/** 视频生成按镜产出的画面段。 */
export interface VideoGenSegmentOut {
  shot: number;
  role: string;
  duration_seconds: number;
  url: string;
}

export interface VideoGenJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | http */
  provider: string;
  /** queued | generating | done | failed */
  status: string;
  progress: number;
  script_artifact_id: string;
  channel: string;
  aspect_ratio: string;
  duration_seconds: number;
  shot_count: number;
  render_seconds: number;
  estimated_cost_usd: number;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  segments_out: VideoGenSegmentOut[];
  error: string;
  attempts: number;
  remote_id: string;
  submitted: boolean;
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

export interface VideoGenView {
  task_id: string;
  has_video_script: boolean;
  jobs: VideoGenJobView[];
}

/** 视频理解产出的结构化视觉摘要（单集「看懂画面」的结果）。 */
export interface VideoUnderstandSummary {
  theme: string;
  logline: string;
  scenes: string[];
  characters: string[];
  actions: string[];
  mood: string;
  camera_language: string;
  pacing: string;
  notable_moments: string[];
  text_brief: string;
  /** true = sample 占位骨架（未真实理解）；false = real 基于画面理解 */
  simulated: boolean;
  sources: string[];
  issues: string[];
  stats: { episode_calls: number; cost_usd: number };
}

export interface VideoUnderstandJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | real */
  provider: string;
  /** queued | understanding | done | failed */
  status: string;
  progress: number;
  video_ref: string;
  video_title: string;
  video_source: string;
  channel: string;
  estimated_cost_usd: number;
  issues: string[];
  remote_id: string;
  attempts: number;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  summary: VideoUnderstandSummary | null;
  error: string;
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface VideoUnderstandView {
  task_id: string;
  has_video_asset: boolean;
  jobs: VideoUnderstandJobView[];
}

/* ------------------------------------------------------------------ */
/* 灵感冲浪（像真人上网刷信息/看视频 → 消化成灵感卡 → 沉淀跨任务 idea 库）  */
/* ------------------------------------------------------------------ */

/** 一张灵感卡（冲浪消化所得的点子/切入角度/开场钩子/为何现在火/参考链接）。 */
export interface SurfCard {
  id: string;
  topic: string;
  idea: string;
  angle: string;
  hook: string;
  why_now: string;
  sources: string[];
  /** 产出该卡的通道：sample | browser | http（auto 已解析为具体通道） */
  provider: string;
  /** true = 离线/降级引擎的占位灵感（非真实冲浪所得） */
  simulated: boolean;
  /** 来源任务（跨任务 idea 库，可能为空） */
  task_id: string;
  created_at: string;
}

/** 一条从页面 DOM 直抓的评论（视频页与正文页都抓；作者/点赞抓不到就留空，画面里的评论文字另由截图 OCR 兜底）。 */
export interface SurfComment {
  author: string;
  text: string;
  likes: string;
}

/** 一组截图交 VLM 的画面结论（真人一段一段看，一组一次多模态调用）。 */
export interface SurfSegment {
  index: number;
  frames: number;
  /** 该组首帧的播放时刻（秒） */
  from: number;
  /** 该组末帧的播放时刻（秒） */
  to: number;
  summary: string;
  on_screen_text: string;
  is_playing: boolean;
  confidence: string;
  simulated: boolean;
}

/** 一条冲浪信号：page/hot/search 为文本信号；video 为「在线看视频」看懂所得（含画面摘要/OCR/评论洞察）。 */
export interface SurfSignal {
  kind?: string;
  /** 这条信号是怎么来的：seed=起点站首页 / search=站内搜索结果页 / feed=信息流点进的条目页 / direct=指定链接 */
  via?: string;
  title?: string;
  url?: string;
  /** 跳转后实际落在的页面（被重定向时与 url 不同） */
  landed_on?: string;
  /** 信息流里看到的条目标题（真人是照着它点进去的，页面标题可能不同） */
  listed_as?: string;
  /** 点进这条正文或视频是扫标题时认出了哪几个主题词（没有这个字段 = 按页面顺序顺路看的） */
  picked_for?: string;
  /** 站内搜索这一页时用的关键词（真人先搜整句，搜不到对题的再把话说短） */
  query?: string;
  /** 这一版关键词是**站点打字时自己弹出来的联想词**（没有这个字段 = 用的是我们自己切的词） */
  suggested?: string;
  /** 为读到这一页依次搜过哪几版关键词（多于一条 = 换过说法再搜） */
  search_tried?: string[];
  /** 这一页被什么挡住了（登录墙/订阅墙/人机验证/维护出错页）：摘录只是门口那几句话，**没读到正文、也没绕过去** */
  wall?: string;
  /** 文本信号的滚动/停留证据（真人式浏览：滚了几轮、原地停了几次、合计停留毫秒） */
  scrolls?: number;
  pauses?: number;
  dwell_ms?: number;
  /** 开这页之前像真人那样先停的毫秒（同会话连续操作为 0） */
  gap_ms?: number;
  /** 这是列表的第几页（点了站点自己的「下一页/加载更多」才读到的后续页） */
  list_page?: number;
  /** 是靠点哪个控件翻到这一页的（如「下一页」「加载更多」） */
  paged_by?: string;
  /** 点开了几处「展开全文」——不点只抓得到被截断的第一段 */
  expanded?: number;
  excerpt?: string;
  /** video 信号专有字段 */
  frames?: number;
  /** 是否确认播放器真的在播（点播放后播放时刻有推进） */
  played?: boolean;
  /** 分组看懂：组数与每组张数 */
  groups?: number;
  group_frames?: number;
  segments?: SurfSegment[];
  /** 多组结论是否再经一次文本汇总 */
  merged?: boolean;
  /** 观看证据：播放推进的帧数与观看秒数（截图只在内存，不落盘、不下载视频） */
  advancing_frames?: number;
  watch_seconds?: number;
  duration?: number;
  /** 截了多帧但播放时刻没推进 = 其实没播起来 */
  stalled?: boolean;
  /** 网关未启用图像输入：抽了帧但没有画面结论 */
  vision_skipped?: boolean;
  /** 页面文字（标题/简介/评论）里含整页截图 OCR 的逐字转写（区别于 DOM 直抓） */
  ocr_texts?: boolean;
  visual_summary?: string;
  on_screen_text?: string;
  comment_insights?: string;
  description?: string;
  uploader?: string;
  views?: string;
  publish_date?: string;
  /** DOM 逐条直抓的评论（视频页与正文页都有；旧数据可能是字符串，展示两者都兼容，抓不到就没有 = 绝不编造） */
  comments?: SurfComment[] | string;
  confidence?: string;
  simulated?: boolean;
  /** 这条视频的诚实边界告警（没播起来 / 评论没抓到 / 未看懂画面） */
  warnings?: string[];
}

/** 一次冲浪会话（作业整机）：生命周期 queued→surfing→digesting→done|failed。 */
export interface SurfSessionView {
  id: string;
  task_id: string;
  tenant: string;
  /** 解析后的具体通道 sample | browser | http */
  provider: string;
  /** 用户请求的通道（可能为 auto/sample） */
  requested_provider: string;
  topic: string;
  /** 本会话是否在线看视频（仅 browser 会真播放与抽帧） */
  watch_videos: boolean;
  /** 指定要在线看的视频页链接（越界的会如实记为 skipped 信号） */
  video_urls?: string[];
  /** queued | surfing | digesting | done | failed */
  status: string;
  progress: number;
  signals: SurfSignal[];
  cards: SurfCard[];
  simulated: boolean;
  estimated_cost_usd: number;
  warnings: string[];
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  error: string;
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface SurfTaskView {
  task_id: string;
  sessions: SurfSessionView[];
}

export interface SurfPoolView {
  cards: SurfCard[];
}

/** 浏览历史里的一条：上几轮**真读进去过**的条目页 / 在线看完的视频页（列表页与首页不算）。 */
export interface SurfHistoryItem {
  id: string;
  tenant: string;
  url: string;
  topic: string;
  title: string;
  /** 当时是怎么进来的：feed=信息流点进 / search=站内搜索结果 / direct=用户指定 */
  via?: string;
  first_seen: string;
  last_seen: string;
  /** 累计读过几遍（同一页再读只 bump 这个数与 last_seen，不重复记账） */
  reads: number;
}

export interface SurfHistoryView {
  /** SURF_REMEMBER_READ 是否开着；关着时 items 恒空（没记就是没记） */
  remember: boolean;
  /** 每个租户最多记多少条（SURF_HISTORY_MAX） */
  max: number;
  items: SurfHistoryItem[];
}

/* ------------------------------------------------------------------ */
/* 全自动配音（真实功能：剧情文本 + 参考音频 → 引擎逐句合成 → 时间轴汇总） */
/* ------------------------------------------------------------------ */

/** 时间轴汇总中的一段台词（源文本 + 所用参考音频 + 配音音频）。 */
export interface DubbingSegment {
  index: number;
  speaker: string;
  text: string;
  /** 跨语言译制时的原句（未译制为空）；text 已是目标语言译文 */
  source_text?: string;
  /** 该说话人的参考音频引用（assets/ 相对路径或 http(s)） */
  voice_ref: string;
  /** 已合成音频的落盘引用（dubbing/<job_id>/seg_xxxx.wav）；空 = 未出音频/模拟 */
  audio_ref?: string;
  start_second: number;
  duration_seconds: number;
  end_second: number;
  audio_url: string;
  /** true = 时长为估算值（real 完成后按真实时长重建） */
  estimated: boolean;
  /** 如实标注（如「未识别到说话人标记，已归入旁白」） */
  issue: string;
}

/** 配音完成后的时间轴汇总（每段含源文本与配音）。 */
export interface DubbingSummary {
  job_id: string;
  task_id: string;
  provider: string;
  /** true = 模拟音频（sample:// 占位，非真实合成） */
  simulated: boolean;
  /** true = 已跨语言译制（text 为目标语言，原句见 source_text） */
  translated?: boolean;
  source_lang?: string;
  target_lang?: string;
  /** true = 译文为离线/降级引擎产出的占位串，非真实翻译 */
  translation_simulated?: boolean;
  line_count: number;
  speakers: string[];
  voices: Record<string, string>;
  duration_seconds: number;
  segments: DubbingSegment[];
  /** 整轨音频引用（全段同参数 wav 时后端拼接）；缺省 = 未拼 */
  full_audio?: string;
  full_audio_url?: string;
  warnings: string[];
  issues: string[];
}

export interface DubbingJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | auto | sapi | sovits | elevenlabs | real（auto 已解析为具体引擎） */
  provider: string;
  /** 用户创建时选的通道（与 provider 不同时即 auto 择优结果） */
  requested_provider?: string;
  /** queued | synthesizing | done | failed */
  status: string;
  progress: number;
  channel: string;
  script_text: string;
  voices: Record<string, string>;
  target_lang?: string;
  source_lang?: string;
  translated?: boolean;
  translation_simulated?: boolean;
  line_count: number;
  speakers: string[];
  duration_seconds: number;
  estimated_cost_usd: number;
  /** 时间轴汇总 txt 的相对引用；空 = 未导出 */
  timeline_path: string;
  remote_id: string;
  attempts: number;
  summary: DubbingSummary | null;
  manifest: {
    provider: string;
    line_count: number;
    speakers: string[];
    voices: Record<string, string>;
    missing_voices: string[];
    chars_per_second: number;
    duration_seconds: number;
    target_lang?: string;
    source_lang?: string;
    translated?: boolean;
    translation_simulated?: boolean;
    segments: DubbingSegment[];
    warnings: string[];
  };
  error: string;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface DubbingView {
  task_id: string;
  jobs: DubbingJobView[];
}

/* ------------------------------------------------------------------ */
/* 桌面宠物（开发样例：动作帧 → 可运行宠物包）                            */
/* ------------------------------------------------------------------ */

/** 宠物清单中的一帧（一个动作的第 index 帧）。 */
export interface PetGenSegment {
  action: string;
  index: number;
  fps: number;
  loop: boolean;
  prompt: string;
}

/** 已落盘的一帧。ref 是 data/pets 下的相对路径，预览要经带鉴权的帧路由。 */
export interface PetGenFrame {
  action: string;
  index: number;
  ref: string;
  bytes: number;
  /** true = 样例引擎画的简笔帧（非真图），必须让使用者看得见 */
  simulated: boolean;
}

export interface PetGenJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** sample | imagegen */
  provider: string;
  /** queued | generating | done | failed */
  status: string;
  progress: number;
  name: string;
  size: number;
  visual_artifact_id: string;
  actions: string[];
  frame_count: number;
  render_seconds: number;
  estimated_cost_usd: number;
  /** 宠物包目录引用（pets/<job_id>） */
  dir: string;
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  frames: PetGenFrame[];
  /** pet.json 的相对引用；空 = 未产出 */
  pet_ref: string;
  error: string;
  attempts: number;
  manifest: {
    provider: string;
    name: string;
    size: number;
    fps: number;
    key_color: string;
    actions: string[];
    frame_count: number;
    segments: PetGenSegment[];
    palette: Record<string, string>;
    palette_source: string;
    brand: string;
    simulated: boolean;
    warnings: string[];
    generated_at: string;
  };
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface PetGenView {
  task_id: string;
  has_visual_brief: boolean;
  jobs: PetGenJobView[];
}

/* ------------------------------------------------------------------ */
/* 创作技能提炼（开发样例：用户作品 → 可复用 SKILL.md）                   */
/* ------------------------------------------------------------------ */

/** 技能里的一步：detail 是做法，evidence 指向本组作品的观察值（无证据的结论不采信）。 */
export interface SkillStep {
  step: string;
  detail: string;
  evidence: string;
}

/** 逐字样例：只能是从作品里摘的原句，带来源标注。 */
export interface SkillExample {
  source: string;
  quote: string;
}

export interface SkillBody {
  name: string;
  title: string;
  description: string;
  /** rules | llm */
  method: string;
  /** true = 未经真实模型推理（rules 恒为 true），界面必须显出来 */
  simulated: boolean;
  when_to_use: string[];
  inputs: string[];
  steps: SkillStep[];
  templates: { hook: string; outline: string[]; closing: string };
  checklist: string[];
  anti_patterns: string[];
  examples: SkillExample[];
}

/** 一份纳入提炼的作品（只留标题与字数，正文不回传）。 */
export interface SkillSource {
  title: string;
  /** document | artifact:<type> | video_understanding */
  origin: string;
  chars: number;
}

/** 跨作品的量化画像：全部是真统计出来的数字。 */
export interface SkillStats {
  works: number;
  chars_median: number;
  sentences_median: number;
  paragraphs_median: number;
  sentence_len_median: number;
  sentence_len_p90: number;
  hook_len_median: number;
  question_ratio: number;
  second_person_ratio: number;
  digit_ratio: number;
  subtitled_works: number;
  cue_len_median: number;
  chars_per_minute: number;
}

export interface SkillGenJobView {
  id: string;
  task_id: string;
  tenant: string;
  /** rules | llm */
  provider: string;
  /** queued | distilling | done | failed */
  status: string;
  progress: number;
  channel: string;
  sample_seconds: number;
  estimated_cost_usd: number;
  stats: SkillStats;
  sources: SkillSource[];
  /** rules 通道受理时即算好；llm 完成后回填 */
  skill: SkillBody | null;
  /** SKILL.md 的相对引用（data/skills/<job_id>/SKILL.md）；空 = 未落盘 */
  skill_ref: string;
  dir: string;
  issues: string[];
  created_at: string;
  updated_at: string;
  started_at: string;
  finished_at: string;
  error: string;
  attempts: number;
  history: { ts: string; from: string; to: string; note: string }[];
}

export interface SkillGenView {
  task_id: string;
  has_materials: boolean;
  jobs: SkillGenJobView[];
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

/** 带鉴权头取一段二进制（宠物帧、导出文本、zip 都不是 JSON，裸链接无法携带 token）。 */
async function fetchBinary(url: string, label: string): Promise<Blob> {
  const response = await fetch(url, { headers: { ...authHeaders() } });
  if (!response.ok) {
    let message = `${label}失败 (${response.status})`;
    try {
      const parsed = (await response.json()) as { detail?: string };
      if (parsed.detail) message = parsed.detail;
    } catch {
      /* 非 JSON 错误体，保留默认文案 */
    }
    if (response.status === 401) message = 'API Token 无效或缺失，请在「运行时设置」中填写';
    throw new Error(message);
  }
  return response.blob();
}

/** 取二进制并触发浏览器下载，用完立即释放 object URL。 */
async function downloadBinary(url: string, filename: string, label: string): Promise<void> {
  const blob = await fetchBinary(url, label);
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = href;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(href);
}

/** 下载任务成品导出 txt：带鉴权头走 blob 触发下载（裸链接无法携带 token）。 */
export async function downloadExport(id: string): Promise<void> {
  await downloadBinary(`/api/tasks/${encodeURIComponent(id)}/export`, `${id}.txt`, '导出');
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
  images: (id: string) => request<ImageGenView>(`/api/tasks/${id}/images`),
  createImage: (id: string, payload: { provider?: string } = {}) =>
    request<{ task_id: string; job: ImageGenJobView }>(`/api/tasks/${id}/images`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  videos: (id: string) => request<VideoGenView>(`/api/tasks/${id}/videos`),
  createVideo: (id: string, payload: { provider?: string } = {}) =>
    request<{ task_id: string; job: VideoGenJobView }>(`/api/tasks/${id}/videos`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  videoUnderstanding: (id: string) =>
    request<VideoUnderstandView>(`/api/tasks/${id}/video-understanding`),
  createVideoUnderstanding: (id: string, payload: { provider?: string } = {}) =>
    request<{ task_id: string; job: VideoUnderstandJobView }>(`/api/tasks/${id}/video-understanding`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  applyVideoUnderstanding: (id: string, payload: { job_id: string }) =>
    request<{ ok: boolean; task_id: string; applied: string; constraints: string[] }>(
      `/api/tasks/${id}/video-understanding/apply`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  /* ---------------- 灵感冲浪（会话 + 跨任务 idea 库） ---------------- */
  surfTask: (id: string) => request<SurfTaskView>(`/api/tasks/${id}/surf`),
  createSurf: (
    id: string,
    payload: { provider?: string; topic?: string; watchVideos?: boolean; videoUrls?: string[] } = {},
  ) =>
    request<{ task_id: string; session: SurfSessionView }>(`/api/tasks/${id}/surf`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 无任务归属的一次冲浪（供「灵感库」页主动囤灵感）；topic 缺省用 SURF_TOPICS 首个。 */
  startSurfSession: (
    payload: { provider?: string; topic?: string; watchVideos?: boolean; videoUrls?: string[] } = {},
  ) =>
    request<{ session: SurfSessionView }>('/api/surf/session', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  surfPool: (topic = '', limit = 0) =>
    request<SurfPoolView>(
      `/api/surf/pool?topic=${encodeURIComponent(topic)}&limit=${limit}`,
    ),
  dropSurfCard: (cardId: string) =>
    request<{ ok: boolean; id: string }>(`/api/surf/pool/${encodeURIComponent(cardId)}`, {
      method: 'DELETE',
    }),
  /** 本租户的冲浪浏览历史（跨会话「读过哪些页」的记忆，最近读过→最早）。 */
  surfHistory: (limit = 0) =>
    request<SurfHistoryView>(`/api/surf/history?limit=${limit}`),
  /** 清掉本租户的浏览记忆：让冲浪手「忘掉」读过的页，下一轮就当第一次刷这些站。 */
  clearSurfHistory: () =>
    request<{ ok: boolean; removed: number; tenant: string }>('/api/surf/history', {
      method: 'DELETE',
    }),
  /** 把用户选选的灵感卡注入 Brief（不自动交付智能体，由用户挑选后触发）。 */
  applySurf: (id: string, payload: { cardIds: string[] }) =>
    request<{ ok: boolean; task_id: string; applied: string; injected: number; constraints: string[] }>(
      `/api/tasks/${id}/surf/apply`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  pets: (id: string) => request<PetGenView>(`/api/tasks/${id}/pets`),
  createPet: (
    id: string,
    payload: { provider?: string; name?: string; actions?: string[] } = {},
  ) =>
    request<{ task_id: string; job: PetGenJobView }>(`/api/tasks/${id}/pets`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 取一帧 PNG 字节：宠物帧在 data/pets 下、不对外静态托管，只能走带鉴权头的路由 */
  petFrame: (id: string, jobId: string, action: string, index: number) =>
    fetchBinary(
      `/api/tasks/${encodeURIComponent(id)}/pets/${encodeURIComponent(jobId)}/frames/${encodeURIComponent(action)}/${index}`,
      `第 ${index} 帧读取`,
    ),
  /** 下载宠物包 zip（pet.json + frames + README + 运行器） */
  petPackage: (id: string, jobId: string, name: string) =>
    downloadBinary(
      `/api/tasks/${encodeURIComponent(id)}/pets/${encodeURIComponent(jobId)}/package`,
      `${name || 'pet'}-${jobId}.zip`,
      '宠物包下载',
    ),
  skills: (id: string) => request<SkillGenView>(`/api/tasks/${id}/skills`),
  createSkill: (id: string, payload: { provider?: string } = {}) =>
    request<{ task_id: string; job: SkillGenJobView }>(`/api/tasks/${id}/skills`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 取 SKILL.md 正文（预览 / 复制用）：文件在 data/skills 下，只能走带鉴权头的路由 */
  skillDocument: async (id: string, jobId: string): Promise<string> => {
    const blob = await fetchBinary(
      `/api/tasks/${encodeURIComponent(id)}/skills/${encodeURIComponent(jobId)}/document`,
      '技能正文读取',
    );
    return blob.text();
  },
  /** 下载技能包 zip（SKILL.md + skill.json + README.md），可直接放进插件的 skills 目录 */
  skillPackage: (id: string, jobId: string, name: string) =>
    downloadBinary(
      `/api/tasks/${encodeURIComponent(id)}/skills/${encodeURIComponent(jobId)}/package`,
      `${name || 'skill'}-${jobId}.zip`,
      '技能包下载',
    ),
  /** 把技能沉淀成记忆库 template 卡片（显式动作，幂等由内容指纹保证） */
  applySkill: (id: string, payload: { job_id: string }) =>
    request<{ ok: boolean; task_id: string; added: number; title: string }>(
      `/api/tasks/${id}/skills/apply`,
      { method: 'POST', body: JSON.stringify(payload) },
    ),
  dubbing: (id: string) => request<DubbingView>(`/api/tasks/${id}/dubbing`),
  dubbingLanguages: () =>
    request<{ languages: { code: string; name: string }[] }>(`/api/dubbing/languages`),
  createDubbing: (
    id: string,
    payload: {
      scriptText: string;
      voices?: Record<string, string>;
      provider?: string;
      targetLang?: string;
      sourceLang?: string;
    },
  ) =>
    request<{ task_id: string; job: DubbingJobView }>(`/api/tasks/${id}/dubbing`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 上传说话人参考音频（base64）到 assets/，返回相对引用 */
  uploadDubbingVoice: (name: string, data: string) =>
    request<{ name: string; ref: string; bytes: number }>('/api/dubbing/voices', {
      method: 'POST',
      body: JSON.stringify({ name, data }),
    }),
  /** 取一份参考音频供试听：走带鉴权头的路由，裸链接无法携带 token */
  dubbingVoice: (id: string, ref: string) =>
    fetchBinary(
      `/api/tasks/${encodeURIComponent(id)}/dubbing/voices?ref=${encodeURIComponent(ref)}`,
      '参考音频读取',
    ),
  /** 取一段已合成的配音音频供试听（引擎通道落盘的真 wav/mp3 或整轨 full.wav） */
  dubbingAudio: (id: string, jobId: string, ref: string) =>
    fetchBinary(
      `/api/tasks/${encodeURIComponent(id)}/dubbing/${encodeURIComponent(jobId)}/audio?ref=${encodeURIComponent(ref)}`,
      '配音音频读取',
    ),
  /** 下载整轨配音（后端拼接的 full.wav） */
  dubbingAudioDownload: (id: string, jobId: string, ref: string, filename: string) =>
    downloadBinary(
      `/api/tasks/${encodeURIComponent(id)}/dubbing/${encodeURIComponent(jobId)}/audio?ref=${encodeURIComponent(ref)}`,
      filename,
      '配音音频下载',
    ),
  /** 下载时间轴汇总 txt（源文本 + 配音对照） */
  dubbingTimeline: (id: string, jobId: string) =>
    downloadBinary(
      `/api/tasks/${encodeURIComponent(id)}/dubbing/${encodeURIComponent(jobId)}/timeline`,
      `dubbing-${jobId}-timeline.txt`,
      '时间轴汇总下载',
    ),
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
