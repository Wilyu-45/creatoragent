/**
 * 影视制作工厂 —— 前端接口层。
 *
 * 复用 ``lib/api.ts`` 的 ``request``：鉴权头、错误文案、401 提示与全站保持一致。
 * 产物是二进制（PNG/WAV/SRT/ASS/EDL/JSON），``<img>`` / ``<audio>`` 带不上请求头，
 * 所以启用鉴权时要把 token 拼进 query（后端中间件专门为此开了 ``?token=``）。
 */
import { getApiToken, request } from '../lib/api.ts';

export interface PortSpec {
  name: string;
  label: string;
  type: string;
  required: boolean;
  many: boolean;
}

export interface ParamSpec {
  name: string;
  label: string;
  kind: 'text' | 'number' | 'range' | 'select' | 'toggle' | 'textarea' | string;
  default: unknown;
  options: string[];
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  unit: string;
  hint: string;
  when: string;
}

export interface NodeSpec {
  type: string;
  title: string;
  dept: string;
  dept_label: string;
  role: string;
  icon: string;
  source: boolean;
  needs_service: boolean;
  tags: string[];
  inputs: PortSpec[];
  outputs: PortSpec[];
  params: ParamSpec[];
}

export interface DeptGroup {
  dept: string;
  label: string;
  note: string;
  nodes: NodeSpec[];
}

export interface Palette {
  departments: DeptGroup[];
  orphan_nodes: NodeSpec[];
  port_types: string[];
  node_count: number;
}

export interface GraphNode {
  id: string;
  type: string;
  title?: string;
  pos: number[];
  params: Record<string, unknown>;
  muted?: boolean;
  collapsed?: boolean;
}

export interface GraphEdge {
  id: string;
  src: string;
  src_port: string;
  dst: string;
  dst_port: string;
}

export interface Graph {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface GraphIssue {
  level: 'error' | 'warn';
  node_id: string;
  edge_id: string;
  port: string;
  message: string;
}

/** 本轮参数补丁：岗位 id → 参数键值；只对这一次 preview/run 生效。 */
export type ParamOverrides = Record<string, Record<string, unknown>>;

export interface ProjectRow {
  id: string;
  title: string;
  note: string;
  created_at: string;
  updated_at: string;
  node_count: number;
  edge_count: number;
  last_run: LastRun | null;
  running: number;
}

export interface LastRun {
  job_id: string;
  status: string;
  counts: Record<string, number>;
  elapsed_ms: number;
  finished_at: string;
  targets: string[];
  force?: boolean;
  force_nodes?: string[];
  /** 本轮改参的岗位（只记岗位名，完整补丁在作业历史里）。 */
  override_nodes?: string[];
  ignored_override_nodes?: string[];
}

export interface Project {
  id: string;
  tenant: string;
  title: string;
  note: string;
  created_at: string;
  updated_at: string;
  graph: Graph;
  last_run: LastRun | null;
}

export interface PresetRow {
  key: string;
  title: string;
  note: string;
  node_count: number;
  edge_count: number;
  graph?: Graph;
}

export type NodeRunStatus = 'idle' | 'queued' | 'running' | 'ok' | 'error' | 'blocked' | 'skipped' | 'cancelled' | 'cached';

export interface NodeRunState {
  status?: NodeRunStatus;
  progress?: number;
}

export interface OutputShape {
  kind: string;
  keys?: string[];
  count?: number | null;
  preview?: Record<string, unknown>;
}

export interface RunNodeResult {
  status: NodeRunStatus;
  summary: string;
  error: string;
  elapsed_ms: number;
  cached: boolean;
  forced?: boolean;
  /** 本轮真的在该岗位的参数补丁下执行（缓存命中/静音/被挡住为 false）。 */
  overridden?: boolean;
  outputs: Record<string, unknown>;
}

export interface RunResult {
  ok: boolean;
  counts: Record<string, number>;
  elapsed_ms: number;
  stale_nodes: string[];
  nodes: Record<string, RunNodeResult>;
}

export interface RunJob {
  id: string;
  project_id: string;
  status: 'queued' | 'running' | 'cancelling' | 'ok' | 'error' | 'cancelled';
  progress: number;
  /** 本轮实际计划执行的岗位数（目标运行只算闭包，不是画布全量）。 */
  plan_size: number;
  counts: Record<string, number>;
  elapsed_ms: number;
  error: string;
  created_at: string;
  updated_at: string;
  targets: string[];
  force: boolean;
  /** 本轮实际强跑的岗位（计划外的不在此列，见 ignored_force_nodes）。 */
  force_nodes: string[];
  /** 请求强跑但不在本次计划闭包内、因而未执行的岗位。 */
  ignored_force_nodes: string[];
  /** 本轮实际生效的参数补丁（只含计划内岗位）。 */
  param_overrides: ParamOverrides;
  /** 请求了但不在本次计划闭包内、因而未应用的岗位。 */
  ignored_param_overrides: string[];
  nodes: Record<string, NodeRunState>;
  /** 失败 / 被上游挡住 / 取消未执行的岗位：历史页据此一键续跑。 */
  retry_nodes: string[];
  events: Record<string, unknown>[];
  has_result?: boolean;
  result: RunResult | null;
}

export type PreviewNodeState = 'cached' | 'run' | 'muted' | 'error';

/** 只读预检的一行：这个岗位本轮会复用缓存、重跑、静音还是直接报错。 */
export interface PreviewNode {
  node_id: string;
  type: string;
  title: string;
  state: PreviewNodeState;
  reason: string;
  signature: string;
  cached_at: string;
  cached_summary: string;
  /** 该岗位是否被指定强跑（静音/类型未知时仍为 true，但状态优先）。 */
  forced: boolean;
  /** 本轮带到这个岗位上的参数补丁键；空数组表示没改参。 */
  override_keys: string[];
}

export interface RunPreview {
  project: string;
  targets: string[];
  plan: string[];
  force: boolean;
  /** 计划内被指定强跑的岗位。 */
  force_nodes: string[];
  /** 指定了但不在本次计划闭包内、不会执行的岗位。 */
  ignored_force_nodes: string[];
  /** 本轮生效的参数补丁（只含计划内岗位）。 */
  param_overrides: ParamOverrides;
  /** 指定了改参但不在本次计划闭包内、不会应用的岗位。 */
  ignored_param_overrides: string[];
  counts: Record<PreviewNodeState | 'total', number>;
  nodes: PreviewNode[];
  generated_at: string;
}

/** 岗位画像的一行：最近 N 次已结束运行里，这个岗位执行/复用/失败与耗时。 */
export interface RunStatsNode {
  node_id: string;
  title: string;
  type: string;
  dept: string;
  dept_label: string;
  appearances: number;
  ok: number;
  cached: number;
  error: number;
  blocked: number;
  cancelled: number;
  skipped: number;
  executed: number;
  cache_rate: number;
  avg_ms: number;
  p95_ms: number;
  total_ms: number;
  last_error: string;
  last_failed_at: string;
}

export interface RunStatsDept {
  dept: string;
  label: string;
  nodes: number;
  appearances: number;
  ok: number;
  cached: number;
  error: number;
  blocked: number;
  total_ms: number;
  avg_ms: number;
  cache_rate: number;
}

export interface RunStatsFailure {
  job_id: string;
  created_at: string;
  node_id: string;
  title: string;
  dept: string;
  dept_label: string;
  status: string;
  error: string;
}

/** 只读派生：没有新增存储，所有数字都能回放到带 job_id 的运行。 */
export interface RunStats {
  project_id: string;
  window: number;
  generated_at: string;
  runs: {
    total: number;
    ok: number;
    error: number;
    cancelled: number;
    success_rate: number;
    forced: number;
    partial_forced: number;
    overridden: number;
    latest_at: string;
    oldest_at: string;
    elapsed_avg_ms: number;
    elapsed_p95_ms: number;
    plan_nodes: number;
    delivered: number;
    cached: number;
    cache_rate: number;
    node_errors: number;
    node_blocked: number;
    node_cancelled: number;
  };
  nodes: RunStatsNode[];
  departments: RunStatsDept[];
  failures: RunStatsFailure[];
}

export type RunChangeKind =
  | 'same'
  | 'added'
  | 'removed'
  | 'recovered'
  | 'regressed'
  | 'still_failed'
  | 'cache_hit'
  | 'cache_lost'
  | 'status_changed';

export interface RunCompareSide {
  type: string;
  title: string;
  status: NodeRunStatus;
  cached: boolean;
  elapsed_ms: number;
  summary: string;
  error: string;
  /** 产物结构指纹：按端口/规模/预览算，用来发现「结构变了」而不是比全文。 */
  output_digest: string;
  output_ports: string[];
}

export interface RunCompareNode {
  node_id: string;
  title: string;
  type: string;
  dept: string;
  dept_label: string;
  /** 当前画布上还有没有这个岗位；历史作业里的已移除岗位也能对比。 */
  in_graph: boolean;
  base: RunCompareSide | null;
  target: RunCompareSide | null;
  change: RunChangeKind;
  elapsed_delta_ms: number;
  output_changed: boolean;
  summary_changed: boolean;
  error_changed: boolean;
}

export interface RunCompareMeta {
  job_id: string;
  status: string;
  created_at: string;
  elapsed_ms: number;
  plan_size: number;
  force: boolean;
  force_nodes: string[];
  override_nodes: string[];
  targets: string[];
  node_count: number;
  ok: number;
  cached: number;
  error: number;
  blocked: number;
  skipped: number;
  cancelled: number;
  delivered: number;
  failed: number;
  cache_rate: number;
}

export interface RunCompare {
  project_id: string;
  generated_at: string;
  base: RunCompareMeta;
  target: RunCompareMeta;
  delta: {
    elapsed_ms: number;
    delivered: number;
    cached: number;
    failed: number;
    cache_rate: number;
  };
  changes: Record<RunChangeKind | 'changed' | 'output_changed' | 'summary_changed', number>;
  nodes: RunCompareNode[];
}

export interface ArtifactRow {
  rel: string;
  group: string;
  name: string;
  size_kb: number;
  url: string;
}

/** 岗位完整交付物：``RunNodeResult.outputs`` 只是摘要，原值在项目缓存里。 */
export interface NodeOutputs {
  node_id: string;
  type: string;
  title: string;
  summary: string;
  finished_at: string;
  outputs: Record<string, unknown>;
}

/** 交付验收：打包前那道门量出的事实（后端 ``acceptance.inspect`` 同口径）。 */
export interface AcceptanceIssue {
  level: 'error' | 'warn' | 'info';
  code: string;
  message: string;
  node_id: string;
}

export interface AcceptanceDeliverable {
  node_id: string;
  title: string;
  type: string;
  terminal: boolean;
  delivered_at: string;
  summary: string;
  ports: string[];
  refs: string[];
  simulated: boolean;
}

export interface AcceptanceReport {
  ok: boolean;
  verdict: 'pass' | 'warn' | 'blocked';
  project: string;
  title: string;
  checked_at: string;
  counts: { error: number; warn: number; info: number };
  issues: AcceptanceIssue[];
  stats: {
    nodes: number;
    edges: number;
    delivered: number;
    terminals: number;
    terminals_ready: number;
    muted: number;
    stale: number;
    needs_run: number;
    artifact_files: number;
    artifact_bytes: number;
    refs_checked: number;
    refs_missing: number;
    refs_empty: number;
    simulated_nodes: number;
  };
  deliverables: AcceptanceDeliverable[];
  /** 最下游那条成片时间轴（与放映室/交付包同口径）；交付包会内含它引用的素材。 */
  film?: FilmMeta | null;
}

/** 成片时间轴元数据（``acceptance.select_film`` 选出，与验收报告、包内 timeline.json 一致）。 */
export interface FilmMeta {
  node_id: string;
  port: string;
  title: string;
  shots: number;
  duration_s: number;
  refs: string[];
}

/** 回看对账：现在这条成片与最新一次交付装箱的那条内容是否一致（只读台账派生）。 */
export interface FilmDelivery {
  delivered_at: string;
  archive_sha256: string;
  download_name: string;
  /** 最新一次交付装箱时那条成片的内容指纹（无成片时为空串）。 */
  film_fingerprint: string;
  /** 现在放映室这条成片的内容指纹。 */
  live_fingerprint: string;
  /** 一致 true / 之后又改过 false / 无成片或从未交付 null（无从比对）。 */
  matches: boolean | null;
}

/** `GET .../film` 回值：选中的成片元数据 + 原始时间轴对象 + 回看对账（无成片轴时 film/timeline 为 null）。 */
export interface FilmResult {
  film: FilmMeta | null;
  timeline: Record<string, unknown> | null;
  delivery: FilmDelivery;
}

export const ACCEPTANCE_VERDICT_LABEL: Record<AcceptanceReport['verdict'], string> = {
  pass: '验收通过',
  warn: '可交付（带警告）',
  blocked: '不可交付',
};

/** 交付台账的一条留痕：一次打包的放行（delivered）或被拦（blocked）。 */
export interface DeliveryEntry {
  at: string;
  event: 'delivered' | 'blocked';
  ok: boolean;
  verdict: AcceptanceReport['verdict'];
  error_count: number;
  warn_count: number;
  info_count: number;
  nodes: number;
  edges: number;
  terminals: number;
  terminals_ready: number;
  delivered: number;
  simulated_nodes: number;
  refs_missing: number;
  artifact_count: number;
  total_bytes: number;
  /** 仅放行时有：交付包内容寻址，用于自证「交出去的就是这一版」。 */
  archive_sha256?: string;
  archive_bytes?: number;
  download_name?: string;
  /** 放行/拦下都记：这版交付带的成片（几镜 / 多久 / 来自哪个岗位）。 */
  film_node?: string;
  film_shots?: number;
  film_duration?: number;
  /** 仅放行且带成片时记：那次装箱成片的内容指纹，供放映室回看对账。 */
  film_fingerprint?: string;
  /** 仅拦下时有：阻断项原文。 */
  blockers?: string[];
}

export interface DeliveryLog {
  project: { id: string; title: string };
  entries: DeliveryEntry[];
  summary: {
    total: number;
    delivered: number;
    blocked: number;
    last_delivered_at: string;
    last_archive_sha256: string;
    capped: number;
  };
}

export interface EventRow {
  ts: string;
  type: string;
  node_id?: string;
  /** 无单节点归属的事件（如本轮改参提示）用数组点名，避免为节点建行。 */
  node_ids?: string[];
  level?: string;
  message?: string;
  title?: string;
  summary?: string;
  error?: string;
  ratio?: number;
  elapsed_ms?: number;
}

const BASE = '/api/factory';

/** 给产物 URL 补上鉴权 token（``<img>``/``<audio>``/下载都用它）。 */
export function mediaUrl(url: string): string {
  if (!url) return url;
  const token = getApiToken();
  if (!token) return url;
  return `${url}${url.includes('?') ? '&' : '?'}token=${encodeURIComponent(token)}`;
}

export const factoryApi = {
  palette: () => request<Palette>(`${BASE}/palette`),
  presets: () => request<{ presets: PresetRow[] }>(`${BASE}/presets`),
  /** 单个预设的完整工作流（catalogue 只有统计，带上 graph 才能直接铺到画布上）。 */
  preset: (key: string) => request<PresetRow>(`${BASE}/presets/${encodeURIComponent(key)}`),
  listProjects: () => request<{ projects: ProjectRow[] }>(`${BASE}/projects`),
  createProject: (payload: { title: string; preset?: string; graph?: Graph; note?: string }) =>
    request<{ project: Project; issues: GraphIssue[] }>(`${BASE}/projects`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  getProject: (id: string) => request<{ project: Project; issues: GraphIssue[] }>(`${BASE}/projects/${encodeURIComponent(id)}`),
  saveGraph: (id: string, graph: Graph, meta: { title?: string; note?: string } = {}) =>
    request<{ project: Project; issues: GraphIssue[] }>(`${BASE}/projects/${encodeURIComponent(id)}/graph`, {
      method: 'PUT',
      body: JSON.stringify({ graph, ...meta }),
    }),
  validateDraft: (id: string, graph: Graph) =>
    request<{ issues: GraphIssue[] }>(`${BASE}/projects/${encodeURIComponent(id)}/validate`, {
      method: 'POST',
      body: JSON.stringify({ graph }),
    }),
  renameProject: (id: string, title: string) =>
    request<{ project: Project }>(`${BASE}/projects/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: JSON.stringify({ title }),
    }),
  deleteProject: (id: string) =>
    request<{ deleted: string }>(`${BASE}/projects/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  run: (
    id: string,
    payload: {
      targets?: string[];
      node_id?: string;
      force?: boolean;
      force_nodes?: string[];
      param_overrides?: ParamOverrides;
    } = {},
  ) =>
    request<{ job: RunJob }>(`${BASE}/projects/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  /** 只读预检：计划、缓存命中与重跑原因；不会建作业或写缓存。 */
  previewProject: (
    id: string,
    payload: {
      targets?: string[];
      node_id?: string;
      force?: boolean;
      force_nodes?: string[];
      param_overrides?: ParamOverrides;
    } = {},
  ) =>
    request<{ preview: RunPreview }>(`${BASE}/projects/${encodeURIComponent(id)}/preview`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listRuns: (id: string, limit = 20) =>
    request<{ runs: RunJob[] }>(`${BASE}/projects/${encodeURIComponent(id)}/runs?limit=${limit}`),
  /** 最近 N 次已结束运行的岗位画像；只读，不影响作业与缓存。 */
  stats: (id: string, limit = 50) =>
    request<{ stats: RunStats }>(`${BASE}/projects/${encodeURIComponent(id)}/stats?limit=${limit}`),
  compareRuns: (id: string, base: string, target: string) =>
    request<{ compare: RunCompare }>(
      `${BASE}/projects/${encodeURIComponent(id)}/runs/compare?base=${encodeURIComponent(base)}&target=${encodeURIComponent(target)}`,
    ),
  job: (jobId: string) => request<{ job: RunJob }>(`${BASE}/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) => request<{ job: RunJob }>(`${BASE}/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  artifacts: (id: string) =>
    request<{ artifacts: ArtifactRow[]; groups: Record<string, number>; count: number }>(
      `${BASE}/projects/${encodeURIComponent(id)}/artifacts`,
    ),
  /** 交付验收（只读）：终端岗位、产物引用与占位标记；也是交付包的放行条件。 */
  acceptance: (id: string) =>
    request<{ acceptance: AcceptanceReport }>(`${BASE}/projects/${encodeURIComponent(id)}/acceptance`),
  /** 交付台账（只读、新的在前）：每次打包的放行/拦截留痕。 */
  deliveries: (id: string) =>
    request<{ deliveries: DeliveryLog }>(`${BASE}/projects/${encodeURIComponent(id)}/deliveries`),
  /** 成片时间轴（只读）：与验收 film / 包内 timeline.json 同口径，放映室直接回看。 */
  film: (id: string) => request<FilmResult>(`${BASE}/projects/${encodeURIComponent(id)}/film`),
  /** 交付包（工作流 + 全部产物 + manifest）的下载地址；带 token 才能直接 fetch。 */
  bundleUrl: (id: string) => mediaUrl(`${BASE}/projects/${encodeURIComponent(id)}/bundle`),
  /** 岗位最近一次成功交付的完整产物（404 = 这个岗位还没有可读缓存）。 */
  nodeOutputs: (id: string, nodeId: string) =>
    request<NodeOutputs>(`${BASE}/projects/${encodeURIComponent(id)}/outputs/${encodeURIComponent(nodeId)}`),
  /** 读文本产物（剧本、字幕、EDL、时间轴 JSON）——带鉴权头取，裸 fetch 会被 401。 */
  artifactText: async (url: string): Promise<string> => {
    const response = await fetch(mediaUrl(url));
    if (!response.ok) throw new Error(`产物读取失败 (${response.status})`);
    return response.text();
  },
};

export const NODE_STATUS_LABEL: Record<NodeRunStatus, string> = {
  idle: '待命',
  queued: '排队',
  running: '作业中',
  ok: '已交付',
  error: '报错',
  blocked: '被上游挡住',
  skipped: '静音跳过',
  cancelled: '取消未执行',
  cached: '复用缓存',
};
