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

export type NodeRunStatus = 'idle' | 'queued' | 'running' | 'ok' | 'error' | 'blocked' | 'skipped' | 'cached';

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
  status: 'queued' | 'running' | 'ok' | 'error' | 'cancelled';
  progress: number;
  counts: Record<string, number>;
  elapsed_ms: number;
  error: string;
  created_at: string;
  updated_at: string;
  targets: string[];
  force: boolean;
  nodes: Record<string, NodeRunState>;
  events: Record<string, unknown>[];
  has_result?: boolean;
  result: RunResult | null;
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

export interface EventRow {
  ts: string;
  type: string;
  node_id?: string;
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
  run: (id: string, payload: { targets?: string[]; node_id?: string; force?: boolean } = {}) =>
    request<{ job: RunJob }>(`${BASE}/projects/${encodeURIComponent(id)}/run`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
  listRuns: (id: string, limit = 20) =>
    request<{ runs: RunJob[] }>(`${BASE}/projects/${encodeURIComponent(id)}/runs?limit=${limit}`),
  job: (jobId: string) => request<{ job: RunJob }>(`${BASE}/jobs/${encodeURIComponent(jobId)}`),
  cancelJob: (jobId: string) => request<{ job: RunJob }>(`${BASE}/jobs/${encodeURIComponent(jobId)}/cancel`, { method: 'POST' }),
  artifacts: (id: string) =>
    request<{ artifacts: ArtifactRow[]; groups: Record<string, number>; count: number }>(
      `${BASE}/projects/${encodeURIComponent(id)}/artifacts`,
    ),
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
  cached: '复用缓存',
};
