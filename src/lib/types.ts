/**
 * 核心数据模型 —— 与《creator.md》第 6 章「核心接口与数据模型」保持一致。
 * 所有智能体的输入输出都必须满足这里的契约。
 *
 * 说明：后端已迁移为 Python（app/），本文件是**前端的契约声明**，
 * 与后端 `app/core/types.py` 逐字段对齐。前端不再依赖旧的 `server/` 目录。
 */

/* ------------------------------------------------------------------ */
/* 智能体标识                                                          */
/* ------------------------------------------------------------------ */

export type AgentId =
  | 'A0'
  | 'A1'
  | 'A2'
  | 'A3'
  | 'A4'
  | 'A5'
  | 'A6'
  | 'A7'
  | 'A8'
  | 'A9'
  | 'A10'
  | 'A11';

/** 智能体的分组：负责生产、负责审核、负责编排。 */
export type AgentKind = 'orchestrator' | 'producer' | 'reviewer' | 'analyst';

/* ------------------------------------------------------------------ */
/* 流程状态机                                                          */
/* ------------------------------------------------------------------ */

export type Phase =
  | 'INIT'
  | 'STRATEGY'
  | 'CREATIVE'
  | 'PLANNING'
  | 'DRAFTING'
  | 'REVIEW'
  | 'EDITING'
  | 'FACT_CHECK'
  | 'COMPLIANCE'
  | 'VISUAL_ADAPT'
  | 'CHANNEL_ADAPT'
  | 'REVISION'
  | 'APPROVAL'
  | 'PUBLISHED'
  | 'ANALYZED'
  | 'MEMORY'
  | 'ARCHIVED'
  | 'REJECTED'
  | 'FAILED'
  | 'PAUSED';

/** 状态机的规范顺序（用于 UI 展示进度）。 */
export const PHASE_ORDER: Phase[] = [
  'INIT',
  'STRATEGY',
  'CREATIVE',
  'PLANNING',
  'DRAFTING',
  'REVIEW',
  'EDITING',
  'FACT_CHECK',
  'COMPLIANCE',
  'VISUAL_ADAPT',
  'CHANNEL_ADAPT',
  'APPROVAL',
  'PUBLISHED',
  'ANALYZED',
  'MEMORY',
  'ARCHIVED',
];

export const PHASE_LABEL: Record<Phase, string> = {
  INIT: '需求接入',
  STRATEGY: '策略洞察',
  CREATIVE: '创意方向',
  PLANNING: '内容策划',
  DRAFTING: '文案创作',
  REVIEW: '审核链路',
  EDITING: '编辑审校',
  FACT_CHECK: '事实核查',
  COMPLIANCE: '品牌合规',
  VISUAL_ADAPT: '视觉方向',
  CHANNEL_ADAPT: '渠道适配',
  REVISION: '返工修订',
  APPROVAL: '人工审批',
  PUBLISHED: '已发布',
  ANALYZED: '效果复盘',
  MEMORY: '知识沉淀',
  ARCHIVED: '已归档',
  REJECTED: '已驳回',
  FAILED: '执行失败',
  PAUSED: '等待人工',
};

/* ------------------------------------------------------------------ */
/* Brief —— 用户输入                                                   */
/* ------------------------------------------------------------------ */

export type Priority = 'low' | 'normal' | 'high' | 'urgent';

export interface Brief {
  /** 品牌名 */
  brand: string;
  /** 产品或服务 */
  product: string;
  /** 本次传播目标：曝光 / 互动 / 转化 / 教育 / 信任 */
  objective: string;
  /** 目标受众 */
  audience: string;
  /** 主渠道：小红书 / 抖音 / 公众号 / 知乎 / 电商详情页 / 官网 / PR */
  channel: string;
  /** 内容调性 */
  tone: string;
  /** 行业（影响合规规则强度） */
  industry: string;
  /** 需要布局的关键词 */
  keywords: string[];
  /** 硬性约束 */
  constraints: string[];
  /** 期望交付物 */
  deliverables: string[];
  /** 附加说明 */
  notes: string;
  priority: Priority;
  deadline: string | null;
}

export function createEmptyBrief(): Brief {
  return {
    brand: '',
    product: '',
    objective: '曝光',
    audience: '',
    channel: '小红书',
    tone: '轻松、真实、有种草感',
    industry: '消费品',
    keywords: [],
    constraints: [],
    deliverables: ['图文笔记 1 篇', '标题备选 5 条'],
    notes: '',
    priority: 'normal',
    deadline: null,
  };
}

/* ------------------------------------------------------------------ */
/* TaskPacket —— A0 拆解出的任务卡                                     */
/* ------------------------------------------------------------------ */

export interface TaskContext {
  brand: string;
  audience: string;
  channel: string;
  tone: string;
  industry: string;
  keywords: string[];
  objective: string;
  product: string;
}

export interface TaskPacket {
  task_id: string;
  parent_id: string | null;
  phase: Phase;
  assigned_agent: AgentId;
  objective: string;
  context: TaskContext;
  constraints: string[];
  expected_output_schema: string;
  priority: Priority;
  deadline: string | null;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/* Artifact —— 写入共享黑板的产物                                      */
/* ------------------------------------------------------------------ */

export type ArtifactType =
  | 'task_plan'
  | 'strategy_brief'
  | 'creative_concept'
  | 'content_plan'
  | 'copy_draft'
  | 'edited_copy'
  | 'fact_check_report'
  | 'compliance_report'
  | 'visual_brief'
  | 'channel_adaptation'
  | 'effect_report'
  | 'knowledge_card'
  | 'final_delivery';

export interface Artifact {
  id: string;
  task_id: string;
  agent_id: AgentId;
  type: ArtifactType;
  /** 同一 type 的版本号，从 1 开始 */
  version: number;
  title: string;
  /** 结构化内容，UI 按 type 渲染 */
  content: Record<string, unknown>;
  /** 纯文本，便于检索与 diff */
  text: string;
  revision: number;
  created_at: string;
  tags: string[];
}

/* ------------------------------------------------------------------ */
/* AgentResult —— 智能体统一返回结构                                   */
/* ------------------------------------------------------------------ */

export type GateResult = 'pass' | 'revise' | 'reject';

export interface Evidence {
  claim: string;
  source: string;
  /** 可信度 0-1 */
  reliability: number;
}

export interface AgentMetrics {
  latency_ms: number;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  provider: string;
  model: string;
  simulated: boolean;
}

export interface AgentResult {
  task_id: string;
  agent_id: AgentId;
  status: 'success' | 'revise' | 'error';
  summary: string;
  artifacts: Artifact[];
  evidence: Evidence[];
  confidence: number;
  risks: string[];
  needs_human_review: boolean;
  gate_result: GateResult | null;
  /** 返工时给上游的修改要求 */
  revision_requests: string[];
  handoff: { to: AgentId | null; reason: string } | null;
  metrics: AgentMetrics;
  created_at: string;
}

/* ------------------------------------------------------------------ */
/* 共享黑板                                                            */
/* ------------------------------------------------------------------ */

export type FactStatus = 'candidate' | 'verified' | 'rejected';

export interface FactEntry {
  id: string;
  task_id: string;
  agent_id: AgentId;
  claim: string;
  source: string;
  status: FactStatus;
  confidence: number;
  created_at: string;
}

export interface IntentEntry {
  key: string;
  task_id: string;
  agent_id: AgentId;
  direction: string;
  expires_at: string;
  created_at: string;
}

export interface ActivityEntry {
  id: string;
  task_id: string;
  agent_id: AgentId;
  action: string;
  signature: string;
  created_at: string;
}

export interface ReviewEntry {
  id: string;
  task_id: string;
  agent_id: AgentId;
  target_artifact_id: string;
  verdict: GateResult;
  score: number;
  items: ReviewItem[];
  created_at: string;
}

export interface ReviewItem {
  severity: 'blocker' | 'major' | 'minor' | 'info';
  category: string;
  detail: string;
  suggestion: string;
  location?: string;
}

export interface BlackboardSnapshot {
  facts: FactEntry[];
  intents: IntentEntry[];
  activities: ActivityEntry[];
  reviews: ReviewEntry[];
  stats: {
    fact_count: number;
    verified_fact_count: number;
    artifact_count: number;
    review_count: number;
    active_intents: number;
  };
}

/* ------------------------------------------------------------------ */
/* 任务记录                                                            */
/* ------------------------------------------------------------------ */

export type TaskStatus =
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'rejected'
  | 'failed'
  | 'cancelled';

export interface RevisionRecord {
  round: number;
  phase: Phase;
  reason: string;
  requests: string[];
  created_at: string;
}

export interface GateRecord {
  phase: Phase;
  agent_id: AgentId;
  verdict: GateResult;
  score: number;
  round: number;
  blocking: string[];
  created_at: string;
}

export interface QualityScorecard {
  structure: number;
  clarity: number;
  brand_voice: number;
  appeal: number;
  fact_safety: number;
  compliance: number;
  overall: number;
}

export interface PipelineNode {
  agent_id: AgentId;
  phase: Phase;
  status: 'pending' | 'running' | 'done' | 'revise' | 'blocked' | 'skipped';
  runs: number;
  confidence: number | null;
  gate_result: GateResult | null;
  summary: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface TaskRecord {
  id: string;
  brief: Brief;
  status: TaskStatus;
  phase: Phase;
  revision_round: number;
  turn_used: number;
  created_at: string;
  updated_at: string;
  finished_at: string | null;
  packets: TaskPacket[];
  results: AgentResult[];
  artifacts: Artifact[];
  pipeline: PipelineNode[];
  revisions: RevisionRecord[];
  gates: GateRecord[];
  scorecard: QualityScorecard | null;
  tokens: { prompt: number; completion: number; cost_usd: number };
  approval: {
    required: boolean;
    decision: 'pending' | 'approved' | 'rejected';
    comment: string;
    decided_at: string | null;
  };
  error: string | null;
}

/* ------------------------------------------------------------------ */
/* 事件（SSE 推送）                                                    */
/* ------------------------------------------------------------------ */

export type AgentEventType =
  | 'task.created'
  | 'task.status'
  | 'task.completed'
  | 'task.failed'
  | 'phase.enter'
  | 'agent.start'
  | 'agent.progress'
  | 'agent.finish'
  | 'gate.decision'
  | 'revision.requested'
  | 'approval.required'
  | 'approval.decided'
  | 'blackboard.write'
  | 'log';

export interface AgentEvent {
  seq: number;
  task_id: string;
  ts: string;
  type: AgentEventType;
  agent_id: AgentId | null;
  phase: Phase | null;
  level: 'debug' | 'info' | 'warn' | 'error';
  message: string;
  payload?: Record<string, unknown>;
}

/* ------------------------------------------------------------------ */
/* 抓包：A2A 消息                                                      */
/* ------------------------------------------------------------------ */

export interface A2AMessage {
  a2a_version: '1.0';
  message_id: string;
  from_agent: AgentId;
  to_agent: AgentId;
  intent: 'handoff' | 'request_revision' | 'notify' | 'veto';
  payload: Record<string, unknown>;
  trace_id: string;
  timestamp: string;
}
