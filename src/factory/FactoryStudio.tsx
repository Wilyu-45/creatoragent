/**
 * 影视制作工厂 —— 画布工作台外壳。
 *
 * 外壳只做四件事：项目与预设的读写、跑图作业轮询（把每个岗位的状态喂回画布）、
 * 底部坞台（事件流 / 产物 / 放映室）、体检问题回显。画布交互在 ``Canvas``，
 * 参数与交付物预览在 ``Inspector``，试映在 ``Player``。
 *
 * 状态一律来自后端：岗位没跑过就说没跑过，缓存命中标「复用缓存」，失败原样落在
 * 节点与作业上——界面上不出现「假装完成」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas } from './Canvas.tsx';
import { Inspector } from './Inspector.tsx';
import { mergedValues } from './ParamFields.tsx';
import { Player, type FactoryTimeline } from './Player.tsx';
import { RunOverrideDialog } from './RunOverrideDialog.tsx';
import { workbenchMeta } from '../lib/workbenches.ts';
import { WorkbenchHead } from '../components/WorkbenchHead.tsx';
import {
  IconActivity,
  IconChart,
  IconChevronRight,
  IconCompare,
  IconCompass,
  IconDownload,
  IconFolder,
  IconHistory,
  IconLayers,
  IconPackage,
  IconPlay,
  IconPlus,
  IconSave,
  IconSearch,
  IconTrash,
  IconUpload,
  IconWorkflow,
} from '../components/icons.tsx';
import type {
  AcceptanceReport,
  ArtifactRow,
  DeliveryLog,
  EventRow,
  FilmDelivery,
  Graph,
  GraphIssue,
  NodeOutputs,
  NodeSpec,
  Palette,
  ParamOverrides,
  PresetRow,
  PreviewNodeState,
  Project,
  ProjectRow,
  RunChangeKind,
  RunCompare,
  RunCompareSide,
  RunJob,
  RunNodeResult,
  RunPreview,
  RunStats,
} from './api.ts';
import { factoryApi, mediaUrl, ACCEPTANCE_VERDICT_LABEL, NODE_STATUS_LABEL } from './api.ts';

const TEXT_EXT = /\.(srt|ass|vtt|edl|json|txt|csv)$/i;
const AUDIO_EXT = /\.(wav|mp3|ogg)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;

/** 稳定空引用：job.result 为 null 时每轮渲染都造新对象会反复触发取数 effect。 */
const NO_RESULTS: Record<string, RunNodeResult> = {};

const EVENT_LABEL: Record<string, string> = {
  'run.start': '开工',
  'run.finish': '运行结束',
  'run.cancel_requested': '取消请求',
  'run.cancel_missed': '取消未生效',
  'run.interrupted': '重启中断',
  'run.wait': '排队等待',
  'run.override': '本轮改参',
  'run.override_skipped': '改参未应用',
  'node.start': '上岗',
  'node.finish': '交付',
  'node.cached': '复用缓存',
  'node.error': '报错',
  'node.blocked': '被上游挡住',
  'node.skipped': '静音跳过',
  'node.cancelled': '取消未执行',
  'node.progress': '进度',
  'node.log': '日志',
};

const JOB_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '跑图中',
  cancelling: '取消中…',
  ok: '已完成',
  error: '有岗位失败',
  cancelled: '已取消',
};

/** 作业状态的色调：成功绿、失败红、取消/进行中黄，其余中性。 */
function jobTone(status: string): string {
  if (status === 'ok') return 'ok';
  if (status === 'error') return 'bad';
  if (status === 'cancelled') return '';
  return 'warn';
}

/** 历史续跑针对的岗位状态；与后端 public_job 的 retry_nodes 口径一致。 */
const RETRY_STATE = new Set(['error', 'blocked', 'cancelled']);

/** 毫秒 -> 人能扫的时长；0 表示没有可统计的执行。 */
function formatMs(ms: number): string {
  if (!ms) return '—';
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function percent(rate: number): string {
  return `${Math.round((rate || 0) * 100)}%`;
}

/** 历史作业可能没有计划规模（早期作业或受理即失败），不拿「? 岗」冒充数字。 */
function planSizeLabel(size: number | null | undefined): string {
  return size ? `${size} 岗` : '计划未记录';
}

/** 稳定序列化参数补丁：键顺序不影响「预检是否跟得上当前意图」的判断。 */
function stableOverrides(overrides: ParamOverrides): string {
  const sorted: ParamOverrides = {};
  for (const nodeId of Object.keys(overrides).sort()) {
    const patch = overrides[nodeId] ?? {};
    const row: Record<string, unknown> = {};
    for (const key of Object.keys(patch).sort()) row[key] = patch[key];
    sorted[nodeId] = row;
  }
  return JSON.stringify(sorted);
}

/** 带符号的耗时差：负数 = 目标更快。 */
function signedMs(ms: number): string {
  if (!ms) return '0ms';
  return `${ms > 0 ? '+' : '-'}${formatMs(Math.abs(ms))}`;
}

/** 已结束运行才进对比选择器；排队/跑图中/取消中的结果还会变。 */
const TERMINAL_STATE = new Set(['ok', 'error', 'cancelled']);

/** 产物按落盘目录分组，界面用中文名，未知目录原样显示。 */
const GROUP_LABEL: Record<string, string> = {
  audio: '音频',
  images: '图片',
  videos: '视频',
  files: '文件',
};

const CHANGE_LABEL: Record<RunChangeKind, { label: string; tone: string }> = {
  regressed: { label: '回退', tone: 'bad' },
  still_failed: { label: '仍失败', tone: 'bad' },
  recovered: { label: '恢复', tone: 'ok' },
  cache_hit: { label: '命中缓存', tone: 'ok' },
  cache_lost: { label: '缓存失效', tone: 'warn' },
  added: { label: '新增岗位', tone: '' },
  removed: { label: '移除岗位', tone: 'warn' },
  status_changed: { label: '状态变化', tone: 'warn' },
  same: { label: '相同', tone: '' },
};

function nodeTone(status: string): string {
  if (status === 'ok' || status === 'cached') return 'ok';
  if (status === 'error' || status === 'blocked') return 'bad';
  if (status === 'cancelled') return 'warn';
  return '';
}

/** 对比表的一侧：状态、耗时、产物端口、摘要或错误，缺一侧就如实留空。 */
function CompareSide({ side }: { side: RunCompareSide | null }) {
  if (!side) return <div className="fc-compare-side muted small">—</div>;
  return (
    <div className="fc-compare-side">
      <div className="fc-run-top">
        <span className={`fc-chip ${nodeTone(side.status)}`}>{NODE_STATUS_LABEL[side.status] ?? side.status}</span>
        {side.cached ? <span className="fc-chip ok">缓存</span> : null}
        {side.elapsed_ms ? <span className="muted small mono">{formatMs(side.elapsed_ms)}</span> : null}
        {side.output_ports.length ? <span className="muted small mono">{side.output_ports.join('/')}</span> : null}
      </div>
      {side.summary ? (
        <div className="fc-compare-summary" title={side.summary}>
          {side.summary}
        </div>
      ) : null}
      {side.error ? <div className="fc-hnode-error">{side.error}</div> : null}
    </div>
  );
}

/** 预检行状态：文案与色调跟它会不会真的执行保持一致。 */
const PREVIEW_LABEL: Record<PreviewNodeState, { label: string; cls: string }> = {
  cached: { label: '复用缓存', cls: 'ok' },
  run: { label: '将重跑', cls: 'warn' },
  muted: { label: '静音跳过', cls: '' },
  error: { label: '类型未知', cls: 'bad' },
};

/**
 * 工作区分页：画布要占满整屏，其余重 UI（岗位库 / 检查器 / 产物 / 放映室 / 事件流 / 预检）
 * 各自一页，靠顶部导航切换——不再挤在同一屏里把画布压成一小块。
 */
type FactoryView = 'canvas' | 'nodes' | 'inspect' | 'files' | 'screen' | 'events' | 'history' | 'compare' | 'stats' | 'plan';

const VIEWS: { key: FactoryView; label: string; short: string; hint: string; icon: typeof IconWorkflow }[] = [
  { key: 'canvas', label: '画布', short: '画布', hint: '整条岗位链的工作面，占满此页', icon: IconWorkflow },
  { key: 'nodes', label: '岗位库', short: '岗位库', hint: '全部岗位的职责、端口与标签，点一下加到画布', icon: IconLayers },
  { key: 'inspect', label: '检查器', short: '检查器', hint: '选中岗位的参数、交付物与体检问题', icon: IconSearch },
  { key: 'files', label: '产物', short: '产物', hint: '每个岗位落盘的产物与成片素材，可预览文本', icon: IconPackage },
  { key: 'screen', label: '放映室', short: '放映', hint: '按时间轴试映成片并导出', icon: IconPlay },
  { key: 'events', label: '事件流', short: '事件流', hint: '作业进度、岗位日志与历史运行', icon: IconActivity },
  { key: 'history', label: '运行历史', short: '历史', hint: '找回失败现场，一键续跑未完成岗位', icon: IconHistory },
  { key: 'compare', label: '运行对比', short: '对比', hint: '两次运行的岗位迁移、缓存命中与耗时差', icon: IconCompare },
  { key: 'stats', label: '运行统计', short: '统计', hint: '缓存复用率、岗位耗时与失败热点', icon: IconChart },
  { key: 'plan', label: '运行预检', short: '预检', hint: '跑图前看清哪些岗位复用缓存、哪些会重跑', icon: IconCompass },
];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 新节点 id：按岗位类型起名字，冲突就自增，画布 JSON 里可读。 */
function makeNodeId(graph: Graph, type: string): string {
  const base = type.replace(/[^a-z0-9]+/gi, '').slice(0, 14) || 'node';
  let index = 1;
  while (graph.nodes.some((node) => node.id === `${base}-${index}`)) index += 1;
  return `${base}-${index}`;
}

/** 新岗位落在右下角空白处，用户自己拖去排版。 */
function freeSpot(graph: Graph): number[] {
  if (!graph.nodes.length) return [40, 40];
  const xs = graph.nodes.map((node) => node.pos[0]);
  const ys = graph.nodes.map((node) => node.pos[1]);
  return [Math.max(...xs) + 300, Math.max(...ys)];
}

/** 下载文件名：保留中文，只把路径分隔符等换掉。 */
function fileStem(title: string): string {
  return (title || '工厂项目').replace(/[\\/:*?"<>|\r\n]+/g, '_').trim().slice(0, 60) || '工厂项目';
}

/** 台账里交付包大小的人读格式（B / KB / MB）。 */
function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function downloadFile(name: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 导入的工作流：接受裸 ``{nodes, edges}``，也接受带 ``graph`` / ``title`` 的导出包。 */
function readWorkflow(payload: unknown): { graph: Graph; title: string } | null {
  if (!payload || typeof payload !== 'object') return null;
  const root = payload as Record<string, unknown>;
  const nested = root.graph && typeof root.graph === 'object' ? (root.graph as Record<string, unknown>) : root;
  if (!Array.isArray(nested.nodes) || !Array.isArray(nested.edges)) return null;
  const project = root.project && typeof root.project === 'object' ? (root.project as Record<string, unknown>) : {};
  const title = typeof root.title === 'string' ? root.title : typeof project.title === 'string' ? project.title : '';
  return { graph: nested as unknown as Graph, title };
}

export function FactoryStudio({
  onBack,
  onToast,
  sourceTask,
  onOpenTask,
}: {
  onBack: () => void;
  onToast: (text: string, error?: boolean) => void;
  sourceTask?: { id: string; label: string } | null;
  onOpenTask?: () => void;
}) {
  const [palette, setPalette] = useState<Palette | null>(null);
  const [presets, setPresets] = useState<PresetRow[]>([]);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [graph, setGraph] = useState<Graph>({ nodes: [], edges: [] });
  const [issues, setIssues] = useState<GraphIssue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [titleDraft, setTitleDraft] = useState('');
  const [newTitle, setNewTitle] = useState('');
  const [newPreset, setNewPreset] = useState('full');
  const [presetPick, setPresetPick] = useState('full');
  const [selected, setSelected] = useState<string[]>([]);
  const [job, setJob] = useState<RunJob | null>(null);
  const [history, setHistory] = useState<RunJob[]>([]);
  const [historyDetail, setHistoryDetail] = useState<RunJob | null>(null);
  const [historyBusy, setHistoryBusy] = useState('');
  const [stats, setStats] = useState<RunStats | null>(null);
  const [statsLimit, setStatsLimit] = useState(50);
  const [statsBusy, setStatsBusy] = useState(false);
  const [statsError, setStatsError] = useState('');
  const [statsSort, setStatsSort] = useState<'time' | 'fail' | 'cache'>('time');
  const [compareBase, setCompareBase] = useState('');
  const [compareTarget, setCompareTarget] = useState('');
  const [comparison, setComparison] = useState<RunCompare | null>(null);
  const [compareBusy, setCompareBusy] = useState(false);
  const [compareError, setCompareError] = useState('');
  const [compareFilter, setCompareFilter] = useState<'changed' | 'issues' | 'all'>('changed');
  const [artifacts, setArtifacts] = useState<ArtifactRow[]>([]);
  const [groups, setGroups] = useState<Record<string, number>>({});
  const [view, setView] = useState<FactoryView>('canvas');
  const [drawers, setDrawers] = useState({ palette: false, inspect: false });
  const [preview, setPreview] = useState<{ rel: string; text: string } | null>(null);
  const [acceptance, setAcceptance] = useState<AcceptanceReport | null>(null);
  const [acceptanceBusy, setAcceptanceBusy] = useState(false);
  const [deliveries, setDeliveries] = useState<DeliveryLog | null>(null);
  const [runPreview, setRunPreview] = useState<RunPreview | null>(null);
  const [runPreviewError, setRunPreviewError] = useState('');
  const [runPreviewBusy, setRunPreviewBusy] = useState(false);
  const [force, setForce] = useState(false);
  const [forceNodes, setForceNodes] = useState<string[]>([]);
  const [runOverrides, setRunOverrides] = useState<ParamOverrides>({});
  const [overrideTarget, setOverrideTarget] = useState<string | null>(null);
  const [busy, setBusy] = useState('');
  const [palQuery, setPalQuery] = useState('');
  const [openDept, setOpenDept] = useState<Record<string, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pollFails = useRef(0);
  const acceptanceSeq = useRef(0);
  const deliveriesSeq = useRef(0);
  const previewSeq = useRef(0);
  const statsSeq = useRef(0);
  const compareSeq = useRef(0);
  const importRef = useRef<HTMLInputElement | null>(null);

  const specs = useMemo<Record<string, NodeSpec>>(() => {
    const rows: NodeSpec[] = [...(palette?.departments ?? []).flatMap((group) => group.nodes), ...(palette?.orphan_nodes ?? [])];
    return Object.fromEntries(rows.map((spec) => [spec.type, spec]));
  }, [palette]);

  /* ------------------------------ 数据装载 ------------------------------ */

  const loadProjects = useCallback(async (): Promise<ProjectRow[]> => {
    const rows = (await factoryApi.listProjects()).projects;
    setProjects(rows);
    return rows;
  }, []);

  const loadArtifacts = useCallback(async (id: string): Promise<void> => {
    try {
      const res = await factoryApi.artifacts(id);
      setArtifacts(res.artifacts);
      setGroups(res.groups);
    } catch {
      /* 产物清单失败不打断编辑，下一次跑图完再刷 */
    }
  }, []);

  const loadHistory = useCallback(async (id: string): Promise<void> => {
    try {
      setHistory((await factoryApi.listRuns(id, 50)).runs);
    } catch {
      /* 历史运行是附加信息 */
    }
  }, []);

  /** 统计只算已结束运行；切项目/改窗口时用序号挡住迟到的旧响应。 */
  const loadStats = useCallback(
    async (id: string, limit = statsLimit): Promise<void> => {
      const seq = ++statsSeq.current;
      setStatsBusy(true);
      try {
        const res = await factoryApi.stats(id, limit);
        if (seq !== statsSeq.current) return;
        setStats(res.stats);
        setStatsError('');
      } catch (error) {
        if (seq !== statsSeq.current) return;
        setStatsError(`统计读取失败：${errorText(error)}`);
      } finally {
        if (seq === statsSeq.current) setStatsBusy(false);
      }
    },
    [statsLimit],
  );

  /** 对比两次已结束运行；换项目/换基准目标时用序号挡住迟到的旧响应。 */
  const loadCompare = useCallback(
    async (id: string, base: string, target: string): Promise<void> => {
      const seq = ++compareSeq.current;
      setCompareBusy(true);
      try {
        const res = await factoryApi.compareRuns(id, base, target);
        if (seq !== compareSeq.current) return;
        setComparison(res.compare);
        setCompareError('');
      } catch (error) {
        if (seq !== compareSeq.current) return;
        setComparison(null);
        setCompareError(`对比失败：${errorText(error)}`);
      } finally {
        if (seq === compareSeq.current) setCompareBusy(false);
      }
    },
    [],
  );

  useEffect(() => {
    void (async () => {
      try {
        const [pal, pre, rows] = await Promise.all([factoryApi.palette(), factoryApi.presets(), loadProjects()]);
        setPalette(pal);
        setPresets(pre.presets);
        setOpenDept(Object.fromEntries(pal.departments.map((group, index) => [group.dept, index === 0])));
        if (rows[0]) await openProject(rows[0].id);
      } catch (error) {
        onToast(`工厂初始化失败：${errorText(error)}`, true);
      }
    })();
  }, []);

  const openProject = useCallback(
    async (id: string): Promise<void> => {
      setProjectId(id);
      setConfirmDelete(false);
      setPreview(null);
      setRunPreview(null);
      setRunPreviewError('');
      setSelected([]);
      setForceNodes([]);
      setRunOverrides({});
      setOverrideTarget(null);
      setJob(null);
      setHistory([]);
      setHistoryDetail(null);
      setHistoryBusy('');
      statsSeq.current += 1;
      setStats(null);
      setStatsBusy(false);
      setStatsError('');
      compareSeq.current += 1;
      setComparison(null);
      setCompareBusy(false);
      setCompareError('');
      setCompareBase('');
      setCompareTarget('');
      try {
        const res = await factoryApi.getProject(id);
        setProject(res.project);
        setGraph(res.project.graph);
        setTitleDraft(res.project.title);
        setIssues(res.issues);
        setDirty(false);
        void loadArtifacts(id);
        void loadHistory(id);
        void loadStats(id);
        const lastJobId = res.project.last_run?.job_id;
        if (lastJobId) {
          const restored = (await factoryApi.job(lastJobId).catch(() => null))?.job ?? null;
          setJob(restored);
        }
      } catch (error) {
        onToast(`项目读取失败：${errorText(error)}`, true);
      }
    },
    [loadArtifacts, loadHistory, loadStats, onToast],
  );

  /* ------------------------------ 运行轮询 ------------------------------ */

  const running = job?.status === 'queued' || job?.status === 'running' || job?.status === 'cancelling';
  const terminalRuns = useMemo(() => history.filter((row) => TERMINAL_STATE.has(row.status)), [history]);

  /* 进入对比页时把基准/目标落到最近两次已结束运行；用户手选过就只校正失效值。 */
  useEffect(() => {
    if (view !== 'compare') return;
    const ids = terminalRuns.map((row) => row.id);
    if (!ids.length) {
      setCompareBase('');
      setCompareTarget('');
      setComparison(null);
      return;
    }
    const target = compareTarget && ids.includes(compareTarget) ? compareTarget : ids[0];
    const base = compareBase && ids.includes(compareBase) && compareBase !== target
      ? compareBase
      : ids.find((id) => id !== target) ?? '';
    if (target !== compareTarget) setCompareTarget(target);
    if (base !== compareBase) setCompareBase(base);
  }, [view, terminalRuns, compareBase, compareTarget]);

  useEffect(() => {
    if (view !== 'compare' || !projectId || !compareBase || !compareTarget || compareBase === compareTarget) return;
    void loadCompare(projectId, compareBase, compareTarget);
  }, [view, projectId, compareBase, compareTarget, loadCompare, history]);

  useEffect(() => {
    if (!job || !running) return;
    const timer = window.setInterval(async () => {
      try {
        const res = await factoryApi.job(job.id);
        pollFails.current = 0;
        setJob(res.job);
        setHistoryDetail((prev) => (prev && prev.id === res.job.id ? res.job : prev));
        if (res.job.status !== 'queued' && res.job.status !== 'running' && res.job.status !== 'cancelling') {
          const counts = res.job.counts ?? {};
          const bad = (counts.error ?? 0) + (counts.blocked ?? 0);
          onToast(
            res.job.status === 'cancelled'
              ? `运行已取消：已交付 ${(counts.ok ?? 0) + (counts.cached ?? 0)} 个岗位，${counts.cancelled ?? 0} 个未执行（产物保留）`
              : bad
                ? `跑图结束：${counts.ok ?? 0} 个岗位交付 · ${counts.cached ?? 0} 个岗位复用 · ${bad} 个岗位失败或受阻`
                : `跑图结束：${counts.ok ?? 0} 个岗位交付 · ${counts.cached ?? 0} 个岗位复用缓存 · ${((res.job.elapsed_ms ?? 0) / 1000).toFixed(1)}s`,
            bad > 0 && res.job.status !== 'cancelled',
          );
          if (projectId) {
            void loadArtifacts(projectId);
            void loadHistory(projectId);
            void loadStats(projectId);
            void loadProjects();
          }
        }
      } catch (error) {
        pollFails.current += 1;
        if (pollFails.current >= 3) {
          onToast(`轮询中断：${errorText(error)}`, true);
          setJob(null);
        }
      }
    }, 800);
    return () => window.clearInterval(timer);
  }, [job, running, projectId, loadArtifacts, loadHistory, loadStats, loadProjects, onToast]);

  /* ------------------------------ 运行预检 ------------------------------ */

  /** 预检取后端已存工作流；有未保存改动时先不查，避免把旧图当成当前图。 */
  const loadRunPreview = useCallback(async (): Promise<void> => {
    if (!projectId || dirty) return;
    const seq = previewSeq.current + 1;
    previewSeq.current = seq;
    setRunPreviewBusy(true);
    try {
      const res = await factoryApi.previewProject(projectId, {
        ...(selected.length ? { targets: selected } : {}),
        force,
        ...(forceNodes.length ? { force_nodes: forceNodes } : {}),
        ...(Object.keys(runOverrides).length ? { param_overrides: runOverrides } : {}),
      });
      if (seq !== previewSeq.current) return;
      setRunPreview(res.preview);
      setRunPreviewError('');
    } catch (error) {
      if (seq !== previewSeq.current) return;
      setRunPreview(null);
      setRunPreviewError(errorText(error));
    } finally {
      if (seq === previewSeq.current) setRunPreviewBusy(false);
    }
  }, [projectId, dirty, selected, force, forceNodes, runOverrides]);

  useEffect(() => {
    if (!projectId || dirty) {
      previewSeq.current += 1; // 作废在途请求，别让旧图的结果回填
      setRunPreview(null);
      setRunPreviewError('');
      setRunPreviewBusy(false);
      return;
    }
    if (running) return;
    const timer = window.setTimeout(() => {
      void loadRunPreview();
    }, 250);
    return () => window.clearTimeout(timer);
  }, [projectId, dirty, running, loadRunPreview, project?.updated_at, job?.status]);

  /* 画布上已删除的岗位不能继续挂着强跑标记，否则下一次预览会以未知岗位拒绝。 */
  useEffect(() => {
    const ids = new Set(graph.nodes.map((node) => node.id));
    setForceNodes((prev) => {
      const next = prev.filter((id) => ids.has(id));
      return next.length === prev.length ? prev : next;
    });
  }, [graph.nodes]);

  /* 已删除或已静音的岗位不能继续挂改参补丁：静音岗位改参会被后端点名拒绝。 */
  useEffect(() => {
    const nodes = new Map(graph.nodes.map((node) => [node.id, node]));
    setRunOverrides((prev) => {
      const next: ParamOverrides = {};
      for (const [id, patch] of Object.entries(prev)) {
        const node = nodes.get(id);
        if (node && !node.muted) next[id] = patch;
      }
      return Object.keys(next).length === Object.keys(prev).length ? prev : next;
    });
    setOverrideTarget((prev) => {
      if (!prev) return prev;
      const node = nodes.get(prev);
      return node && !node.muted ? prev : null;
    });
  }, [graph.nodes]);

  /* ------------------------------ 草稿体检 ------------------------------ */

  useEffect(() => {
    if (!projectId || !graph.nodes.length) {
      if (!graph.nodes.length) setIssues([]);
      return;
    }
    const timer = window.setTimeout(() => {
      void factoryApi
        .validateDraft(projectId, graph)
        .then((res) => setIssues(res.issues))
        .catch(() => {
          /* 体检失败保留上一次问题清单，别把画布刷成「没问题」 */
        });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [projectId, graph]);

  /* ------------------------------ 派生数据 ------------------------------ */

  const errors = issues.filter((issue) => issue.level === 'error');
  const warnings = issues.filter((issue) => issue.level === 'warn');
  const badNodes = useMemo(() => new Set(errors.map((issue) => issue.node_id).filter(Boolean)), [errors]);
  const badEdges = useMemo(() => new Set(issues.map((issue) => issue.edge_id).filter(Boolean)), [issues]);
  const activeId = selected.length ? selected[selected.length - 1] : null;
  const activeNode = graph.nodes.find((node) => node.id === activeId) ?? null;
  const activeSpec = activeNode ? (specs[activeNode.type] ?? null) : null;
  const overrideNode = overrideTarget ? (graph.nodes.find((node) => node.id === overrideTarget) ?? null) : null;
  const overrideSpec = overrideNode ? (specs[overrideNode.type] ?? null) : null;
  const nodeResults = job?.result?.nodes ?? NO_RESULTS;
  const events = (job?.events ?? []) as unknown as EventRow[];
  const nodeLogs = useMemo(() => events.filter((row) => row.node_id === activeId && row.type === 'node.log'), [events, activeId]);

  /** 画布上的可读名：用户标题 → 岗位默认名 → id（预设节点常不填标题）。 */
  const titleOf = useCallback(
    (nodeId: string) => {
      const node = graph.nodes.find((item) => item.id === nodeId);
      return node?.title || specs[node?.type ?? '']?.title || nodeId;
    },
    [graph, specs],
  );

  /** 事件流与历史详情共用同一份呈现：时间、岗位、事件类型、说明与进度/耗时。 */
  const renderEventRows = (rows: EventRow[], empty: string) =>
    rows.length ? (
      rows.map((row, index) => {
        const label = EVENT_LABEL[row.type ?? ''] ?? String(row.type ?? '');
        const text = row.message ?? row.summary ?? row.error ?? '';
        return (
          <div className={`fc-event ${row.level === 'error' || row.type === 'node.error' ? 'bad' : ''}`} key={index}>
            <span className="mono muted">{String(row.ts ?? '').slice(11)}</span>
            <span className="fc-ev-who">{row.node_id ? titleOf(row.node_id) : '工厂'}</span>
            <span className={`fc-chip ${row.type === 'node.finish' ? 'ok' : ''}`}>{label}</span>
            <span className="fc-ev-text">{text}</span>
            {typeof row.ratio === 'number' ? <span className="mono small muted">{Math.round(row.ratio * 100)}%</span> : null}
            {typeof row.elapsed_ms === 'number' ? <span className="mono small muted">{(row.elapsed_ms / 1000).toFixed(2)}s</span> : null}
          </div>
        );
      })
    ) : (
      <div className="muted small">{empty}</div>
    );

  /* 成片时间轴：后端 ``select_film`` 单一口径选出拓扑序最下游那条，一次取回（原先前端
     逐岗位 N+1 拉 nodeOutputs 的启发式选轴去掉，与验收 film / 包内 timeline.json 同一真相）。
     随运行结束（runToken 变化）重取；迟到响应用序号守卫，切项目立即清空。 */
  const runToken = `${job?.id ?? ''}:${job?.status ?? ''}`;
  const [timeline, setTimeline] = useState<{ id: string; data: FactoryTimeline } | null>(null);
  const [filmDelivery, setFilmDelivery] = useState<FilmDelivery | null>(null);
  const [filmNonce, setFilmNonce] = useState(0); // 交付包下载后自增，触发重取对账（屏幕这条 vs 刚装箱那条）
  const [outputs, setOutputs] = useState<{ node_id: string; value: NodeOutputs | null } | null>(null);
  const filmSeq = useRef(0);

  useEffect(() => {
    if (!projectId) {
      filmSeq.current += 1;
      setTimeline(null);
      setFilmDelivery(null);
      return;
    }
    const seq = ++filmSeq.current;
    void (async () => {
      const res = await factoryApi.film(projectId).catch(() => null);
      if (seq !== filmSeq.current) return;
      if (res?.film && res.timeline) {
        setTimeline({ id: res.film.node_id, data: res.timeline as unknown as FactoryTimeline });
        setFilmDelivery(res.delivery ?? null);
      } else {
        setTimeline(null);
        setFilmDelivery(null);
      }
    })();
  }, [projectId, runToken, filmNonce]);

  useEffect(() => {
    if (!projectId || !activeId) {
      setOutputs(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      const res = await factoryApi.nodeOutputs(projectId, activeId).catch(() => null);
      if (!cancelled) setOutputs({ node_id: activeId, value: res });
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, activeId, runToken]);

  const activeOutputs = outputs?.node_id === activeId ? outputs.value?.outputs ?? null : null;
  const outputsPending = Boolean(activeId && outputs?.node_id !== activeId);

  /* ------------------------------ 动作 ------------------------------ */

  const mutate = (next: Graph) => {
    setGraph(next);
    setDirty(true);
  };

  const addNode = (spec: NodeSpec) => {
    const node = { id: makeNodeId(graph, spec.type), type: spec.type, title: spec.title, pos: freeSpot(graph), params: {} };
    mutate({ ...graph, nodes: [...graph.nodes, node] });
    setSelected([node.id]);
    setView('canvas');
  };

  const doCreate = async () => {
    setBusy('create');
    try {
      const res = await factoryApi.createProject({ title: newTitle.trim() || `工厂项目 ${projects.length + 1}`, preset: newPreset });
      await loadProjects();
      setNewTitle('');
      await openProject(String(res.project.id));
      const bad = res.issues.filter((issue) => issue.level === 'error').length;
      onToast(bad ? `项目已建好，但体检有 ${bad} 个错误` : `项目已建好：${res.project.graph.nodes.length} 个岗位就位`, bad > 0);
    } catch (error) {
      onToast(`新建失败：${errorText(error)}`, true);
    } finally {
      setBusy('');
    }
  };

  const doSave = async () => {
    if (!projectId) return;
    setBusy('save');
    try {
      const res = await factoryApi.saveGraph(projectId, graph, { title: titleDraft.trim() || undefined });
      setProject(res.project);
      setIssues(res.issues);
      setDirty(false);
      await loadProjects();
      const bad = res.issues.filter((issue) => issue.level === 'error').length;
      onToast(bad ? `工作流已保存（体检 ${bad} 个错误，跑图会被挡住）` : '工作流已保存', bad > 0);
    } catch (error) {
      onToast(`保存失败：${errorText(error)}`, true);
    } finally {
      setBusy('');
    }
  };

  const applyPreset = async (key: string) => {
    setBusy('preset');
    try {
      const info = await factoryApi.preset(key);
      if (!info.graph) {
        onToast('预设没有带回工作流', true);
        return;
      }
      mutate(info.graph);
      setSelected([]);
      onToast(`已铺上「${info.title}」：${info.node_count} 个岗位 · ${info.edge_count} 条连线`);
    } catch (error) {
      onToast(`载入预设失败：${errorText(error)}`, true);
    } finally {
      setBusy('');
    }
  };

  const startRun = async (
    payload: {
      targets?: string[];
      node_id?: string;
      force_nodes?: string[];
      param_overrides?: ParamOverrides;
    },
    options: { force?: boolean } = {},
  ): Promise<boolean> => {
    if (!projectId) return false;
    if (dirty) {
      onToast('画布有未保存改动：先保存再跑（跑的是后端已存的工作流）', true);
      return false;
    }
    setBusy('run');
    try {
      const res = await factoryApi.run(projectId, { ...payload, force: options.force ?? force });
      pollFails.current = 0;
      setJob(res.job);
      void loadHistory(projectId);
      const forced = res.job.force_nodes?.length ?? 0;
      const ignored = res.job.ignored_force_nodes?.length ?? 0;
      const overridden = Object.keys(res.job.param_overrides ?? {}).length;
      const ignoredOverride = res.job.ignored_param_overrides?.length ?? 0;
      const extra = `${forced ? ` · 强跑 ${forced} 个岗位` : ''}${ignored ? ` · 忽略计划外强跑 ${ignored} 个岗位` : ''}`
        + `${overridden ? ` · 改参 ${overridden} 个岗位` : ''}`
        + `${ignoredOverride ? ` · 忽略计划外改参 ${ignoredOverride} 个岗位` : ''}`;
      onToast(
        (res.job.targets.length ? `运行已受理：目标 ${res.job.targets.join('、')}` : '运行已受理：全部岗位按拓扑序推进') + extra,
      );
      return true;
    } catch (error) {
      onToast(`运行失败：${errorText(error)}`, true);
      return false;
    } finally {
      setBusy('');
    }
  };

  const clearOverride = (nodeId: string) => {
    setRunOverrides((prev) => {
      if (!(nodeId in prev)) return prev;
      const next = { ...prev };
      delete next[nodeId];
      return next;
    });
  };

  const saveOverride = (nodeId: string, patch: Record<string, unknown>) => {
    setRunOverrides((prev) => {
      const next = { ...prev };
      if (Object.keys(patch).length) next[nodeId] = patch;
      else delete next[nodeId];
      return next;
    });
    setOverrideTarget(null);
  };

  const cancelRun = async () => {
    if (!job) return;
    try {
      const res = await factoryApi.cancelJob(job.id);
      setJob(res.job);
      onToast(
        res.job.status === 'cancelling'
          ? '已请求取消：当前岗位收尾后停止，未开工岗位不再执行'
          : '运行已结束，无需取消',
      );
    } catch (error) {
      onToast(`取消失败：${errorText(error)}`, true);
    }
  };

  const openHistoryRun = async (runId: string): Promise<RunJob | null> => {
    setHistoryBusy(runId);
    try {
      const res = await factoryApi.job(runId);
      setHistoryDetail(res.job);
      return res.job;
    } catch (error) {
      onToast(`运行详情读取失败：${errorText(error)}`, true);
      return null;
    } finally {
      setHistoryBusy('');
    }
  };

  /**
   * 历史页的续跑只做「补齐未完成」或「沿用原目标」，且始终允许复用有效缓存：
   * 它不是强制重跑入口，避免把已经交付的岗位无谓推翻。
   */
  const rerunHistory = async (row: RunJob, scope: 'retry' | 'same') => {
    if (running) {
      onToast('正在跑图：等这一轮结束再续跑', true);
      return;
    }
    let detail = historyDetail?.id === row.id ? historyDetail : null;
    if (!detail) detail = await openHistoryRun(row.id);
    if (!detail) return;
    const targets = scope === 'retry' ? detail.retry_nodes ?? [] : detail.targets ?? [];
    if (scope === 'retry' && !targets.length) {
      onToast('这次运行没有未完成岗位：要整轮再跑请用「沿用目标再跑」', true);
      return;
    }
    await startRun(targets.length ? { targets } : {}, { force: false });
  };

  /** 历史页的快捷入口：以更早一次已结束运行为基准，和这一轮对照。 */
  const compareWithPrevious = (row: RunJob) => {
    const index = terminalRuns.findIndex((item) => item.id === row.id);
    const base = index >= 0 ? terminalRuns[index + 1] : undefined;
    if (!base) {
      onToast('这是当前最早的一次已结束运行，没有更早的基准', true);
      return;
    }
    setCompareBase(base.id);
    setCompareTarget(row.id);
    setView('compare');
  };

  const doDelete = async () => {
    if (!projectId) return;
    setBusy('delete');
    try {
      await factoryApi.deleteProject(projectId);
      const rows = await loadProjects();
      setProject(null);
      setGraph({ nodes: [], edges: [] });
      setJob(null);
      setArtifacts([]);
      setGroups({});
      setHistory([]);
      setHistoryDetail(null);
      statsSeq.current += 1;
      setStats(null);
      setStatsBusy(false);
      compareSeq.current += 1;
      setComparison(null);
      setCompareBusy(false);
      setCompareError('');
      setCompareBase('');
      setCompareTarget('');
      setConfirmDelete(false);
      const next = rows[0];
      if (next) await openProject(next.id);
      else setProjectId(null);
      onToast('项目已删除（工作流与产物一起清掉）');
    } catch (error) {
      onToast(`删除失败：${errorText(error)}`, true);
      setConfirmDelete(false);
    } finally {
      setBusy('');
    }
  };

  const showText = async (row: ArtifactRow) => {
    try {
      setPreview({ rel: row.rel, text: (await factoryApi.artifactText(row.url)).slice(0, 40000) });
    } catch (error) {
      onToast(errorText(error), true);
    }
  };

  /** 交付验收：拉一份只读报告（终端岗位是否交付、引用文件是否在盘、时间轴是否为空）。 */
  const runAcceptance = useCallback(
    async (id: string): Promise<void> => {
      const seq = ++acceptanceSeq.current;
      setAcceptanceBusy(true);
      try {
        const res = await factoryApi.acceptance(id);
        if (seq !== acceptanceSeq.current) return;
        setAcceptance(res.acceptance);
      } catch (error) {
        if (seq !== acceptanceSeq.current) return;
        setAcceptance(null);
        onToast(errorText(error), true);
      } finally {
        if (seq === acceptanceSeq.current) setAcceptanceBusy(false);
      }
    },
    [onToast],
  );

  /** 交付台账：拉一份只读的放行/拦截留痕（新在前）。 */
  const runDeliveries = useCallback(
    async (id: string): Promise<void> => {
      const seq = ++deliveriesSeq.current;
      try {
        const res = await factoryApi.deliveries(id);
        if (seq !== deliveriesSeq.current) return;
        setDeliveries(res.deliveries);
      } catch (error) {
        if (seq !== deliveriesSeq.current) return;
        setDeliveries(null);
        onToast(errorText(error), true);
      }
    },
    [onToast],
  );

  /* 切到产物页就自动验收一次；换项目先清空，避免拿着旧项目的报告看新图。 */
  useEffect(() => {
    if (!projectId) {
      acceptanceSeq.current += 1;
      setAcceptance(null);
      deliveriesSeq.current += 1;
      setDeliveries(null);
      return;
    }
    if (view !== 'files') return;
    void runAcceptance(projectId);
    void runDeliveries(projectId);
  }, [projectId, view, runAcceptance, runDeliveries]);

  /** 交付包取的是后端已存工作流 + 已落盘产物；有未保存改动时先拦一下，别让人误以为包里是新版。 */
  const downloadBundle = async () => {
    if (!projectId) return;
    if (dirty) {
      onToast('画布有未保存改动：先保存再下载交付包（包里的工作流取后端已存版本）', true);
      return;
    }
    setBusy('bundle');
    try {
      // 直接开链接的话，409/500 会整页变成错误 JSON；fetch 后转 blob 才能把正文当提示
      const res = await fetch(factoryApi.bundleUrl(projectId));
      if (!res.ok) {
        let detail = `交付包下载失败 (${res.status})`;
        try {
          const body = (await res.json()) as { detail?: unknown };
          if (body?.detail) detail = String(body.detail);
        } catch {
          /* 错误体不是 JSON 就保留状态码 */
        }
        throw new Error(detail);
      }
      const title = titleDraft.trim() || project?.title || '工厂项目';
      downloadFile(`${fileStem(title)}-${projectId}-delivery.zip`, await res.blob());
      onToast('交付包已下载：工作流 + 全部产物 + manifest.json');
    } catch (error) {
      onToast(errorText(error), true);
    } finally {
      setBusy('');
      // 无论放行还是被验收拦下，后端都往台账记了一笔；拉回来让面板反映这次尝试
      void runDeliveries(projectId);
      // 刚交付完，重取一次回看对账（屏幕这条 vs 刚装箱那条），让放映室的提示同步更新
      setFilmNonce((n) => n + 1);
    }
  };

  const exportWorkflow = () => {
    const title = titleDraft.trim() || project?.title || '未命名项目';
    const payload = {
      format: 'creatoragent.factory.workflow',
      version: 1,
      exported_at: new Date().toISOString(),
      title,
      note: project?.note ?? '',
      graph,
    };
    downloadFile(`${fileStem(title)}.workflow.json`, new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }));
    onToast(`工作流已导出：${graph.nodes.length} 个岗位 · ${graph.edges.length} 条连线${dirty ? '（含未保存改动）' : ''}`);
  };

  const importWorkflow = async (file: File) => {
    setBusy('import');
    try {
      const parsed = readWorkflow(JSON.parse(await file.text()));
      if (!parsed) throw new Error('不是工作流 JSON：需要 nodes / edges（可包在 graph 字段里）');
      const fallback = file.name.replace(/\.(workflow\.)?json$/i, '');
      const res = await factoryApi.createProject({ title: parsed.title || fallback || '导入项目', graph: parsed.graph });
      await loadProjects();
      await openProject(String(res.project.id));
      const bad = res.issues.filter((issue) => issue.level === 'error').length;
      onToast(
        bad
          ? `工作流已导入为新项目，但体检有 ${bad} 个错误`
          : `工作流已导入：${res.project.graph.nodes.length} 个岗位 · ${res.project.graph.edges.length} 条连线`,
        bad > 0,
      );
    } catch (error) {
      onToast(`导入失败：${errorText(error)}`, true);
    } finally {
      setBusy('');
      if (importRef.current) importRef.current.value = '';
    }
  };

  /* ------------------------------ 面板渲染 ------------------------------ */

  // 选中岗位就把检查器抽屉摊开：抽屉是浮层，不挤画布，用户手动关掉后本次选中不再弹
  useEffect(() => {
    if (selected.length) setDrawers((prev) => (prev.inspect ? prev : { ...prev, inspect: true }));
  }, [selected]);

  const paletteGroups = useMemo(() => {
    const q = palQuery.trim().toLowerCase();
    const hit = (spec: NodeSpec) =>
      !q ||
      spec.title.toLowerCase().includes(q) ||
      spec.role.toLowerCase().includes(q) ||
      spec.type.includes(q) ||
      spec.tags.some((tag) => tag.toLowerCase().includes(q));
    return (palette?.departments ?? [])
      .map((group) => ({ ...group, nodes: group.nodes.filter(hit) }))
      .filter((group) => group.nodes.length);
  }, [palette, palQuery]);

  const counts = job?.counts ?? {};

  /* ---------------- 可复用面板：抽屉与整页共用同一份实现，不各写一套 ---------------- */

  const palettePanel = (
    <>
      <div className="panel-title">
        岗位面板 <span className="count">· 点一下加到画布</span>
      </div>
      <input className="fc-search" value={palQuery} onChange={(event) => setPalQuery(event.target.value)} placeholder="搜岗位 / 职责 / 标签" />
      <div className="fc-pal-scroll">
        {paletteGroups.map((group) => (
          <div className="fc-pal-group" key={group.dept}>
            <button className="fc-pal-head" onClick={() => setOpenDept((prev) => ({ ...prev, [group.dept]: !prev[group.dept] }))}>
              <IconChevronRight size={13} className={`fc-fold${openDept[group.dept] ? ' is-open' : ''}`} /> {group.label}
              <span className="muted small">{group.nodes.length}</span>
            </button>
            {openDept[group.dept] ? (
              <>
                {group.note ? <div className="fc-pal-note">{group.note}</div> : null}
                {group.nodes.map((spec) => (
                  <button className="fc-pal-node" key={spec.type} onClick={() => addNode(spec)} title={`${spec.role}\n${spec.tags.join(' / ')}`}>
                    <span className="fc-pal-icon">{spec.icon}</span>
                    <span className="fc-pal-text">
                      <b>{spec.title}</b>
                      <em>
                        {spec.inputs.length} 入 · {spec.outputs.length} 出{spec.needs_service ? ' · 需服务商' : ''}
                      </em>
                    </span>
                    <span className="fc-plus"><IconPlus size={12} strokeWidth={2.4} /></span>
                  </button>
                ))}
              </>
            ) : null}
          </div>
        ))}
        {!paletteGroups.length ? <div className="muted small">没有匹配的岗位。</div> : null}
      </div>
    </>
  );

  const inspectPanel = (
    <>
      <Inspector
        node={activeNode}
        spec={activeSpec}
        result={activeId ? (nodeResults[activeId] ?? null) : null}
        outputs={activeOutputs}
        pending={outputsPending}
        logs={nodeLogs}
        issues={issues.filter((issue) => issue.node_id === activeId)}
        graph={graph}
        onChange={mutate}
        onRunNode={(id) => void startRun({ node_id: id, ...overridePayload })}
        overridePatch={activeId ? runOverrides[activeId] : undefined}
        onOverride={setOverrideTarget}
        onClearOverride={clearOverride}
      />
      <div className="panel fc-legend">
        <div className="panel-title">端口类型</div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          {(palette?.port_types ?? []).map((type) => (
            <span className="fc-chip mono" key={type}>
              {type}
            </span>
          ))}
        </div>
        <div className="muted small" style={{ marginTop: 8 }}>
          拉线只认兼容端口（如「分镜」不能直接喂给「配音」）；环路与必填缺失会在体检里点名。
        </div>
      </div>
    </>
  );

  const eventsPanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          岗位事件流 <span className="count">{events.length} 条</span>
        </span>
        <span className="fc-dock-side">
          {history.slice(0, 8).map((row) => (
            <button
              key={row.id}
              className={`fc-chip ${row.id === job?.id ? 'ok' : ''}`}
              title={`${row.id} · ${row.created_at}${row.targets.length ? ` · 目标 ${row.targets.join('、')}` : ''}`}
              onClick={() => {
                void factoryApi
                  .job(row.id)
                  .then((res) => setJob(res.job))
                  .catch((error) => onToast(errorText(error), true));
              }}
            >
              {JOB_LABEL[row.status] ?? row.status} · 交付 {row.counts.ok ?? 0}/{(row.counts.ok ?? 0) + (row.counts.error ?? 0) + (row.counts.cached ?? 0)} · {row.updated_at}
            </button>
          ))}
          {!history.length ? <span className="muted small">还没有运行记录。</span> : null}
          <button className="btn btn-sm btn-ghost" onClick={() => setView('history')}>
            全部历史 →
          </button>
        </span>
      </div>
      <div className="fc-events">
        {renderEventRows(
          events,
          '还没有运行：工具栏「运行全图」按拓扑序推进每个岗位，或选中某个岗位「运行选中」只跑它的上游闭包。',
        )}
      </div>
    </>
  );

  const historyPanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          运行历史 <span className="count">· {history.length} 次（最近 50 次）</span>
        </span>
        <span className="fc-dock-side">
          <span className="muted small">续跑会复用仍有效的缓存</span>
          <button
            className="btn btn-sm btn-ghost"
            disabled={!projectId || historyBusy !== ''}
            onClick={() => {
              if (projectId) void loadHistory(projectId);
            }}
          >
            刷新历史
          </button>
        </span>
      </div>

      {!projectId ? <div className="muted small">先选择或新建一个项目。</div> : null}
      {projectId && !history.length ? (
        <div className="muted small">还没有运行记录：回画布跑一次，这里会留下目标、耗时、失败岗位与完整事件。</div>
      ) : null}

      {history.length ? (
        <div className="fc-history">
          <div className="fc-history-list">
            {history.map((row) => {
              const counts = row.counts ?? {};
              const retryCount = row.retry_nodes?.length ?? 0;
              const active = historyDetail?.id === row.id;
              const rowLive = ['queued', 'running', 'cancelling'].includes(row.status);
              return (
                <div
                  key={row.id}
                  className={`fc-run-row state-${row.status} ${active ? 'is-active' : ''}`}
                  role="button"
                  tabIndex={0}
                  onClick={() => void openHistoryRun(row.id)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') void openHistoryRun(row.id);
                  }}
                >
                  <div className="fc-run-top">
                    <span className={`fc-chip ${jobTone(row.status)}`}>{JOB_LABEL[row.status] ?? row.status}</span>
                    {row.id === job?.id ? <span className="fc-chip ok">当前</span> : null}
                    <span className="mono small">{row.id}</span>
                    <span className="muted small">{row.created_at}</span>
                    <span className="muted small">{(row.elapsed_ms / 1000).toFixed(1)}s</span>
                    <span className="muted small">
                      {row.targets.length ? `选中闭包 · ${planSizeLabel(row.plan_size)}` : `整图 · ${planSizeLabel(row.plan_size)}`}
                    </span>
                    {row.force ? <span className="fc-chip">忽略缓存</span> : null}
                    {row.force_nodes?.length ? (
                      <span className="fc-chip warn" title={`强跑：${row.force_nodes.join('、')}`}>
                        强跑 {row.force_nodes.length} 岗
                      </span>
                    ) : null}
                    {Object.keys(row.param_overrides ?? {}).length ? (
                      <span className="fc-chip warn" title={`本轮改参：${Object.keys(row.param_overrides).join('、')}`}>
                        改参 {Object.keys(row.param_overrides).length} 岗
                      </span>
                    ) : null}
                    {row.ignored_param_overrides?.length ? (
                      <span className="fc-chip" title={`计划外改参未应用：${row.ignored_param_overrides.join('、')}`}>
                        忽略改参 {row.ignored_param_overrides.length} 岗
                      </span>
                    ) : null}
                  </div>
                  <div className="fc-run-meta">
                    <span>交付 {counts.ok ?? 0} 岗</span>
                    <span>复用 {counts.cached ?? 0} 岗</span>
                    <span className={counts.error ? 'bad-text' : ''}>失败 {counts.error ?? 0} 岗</span>
                    <span className={counts.blocked ? 'bad-text' : ''}>受阻 {counts.blocked ?? 0} 岗</span>
                    <span>取消 {counts.cancelled ?? 0} 岗</span>
                    {retryCount ? <span className="fc-chip warn">待续跑 {retryCount} 岗</span> : null}
                  </div>
                  {row.targets.length ? <div className="muted small mono">目标：{row.targets.join('、')}</div> : null}
                  {row.error ? <div className="fc-plan-note bad">{row.error}</div> : null}
                  <div className="fc-run-actions" onClick={(event) => event.stopPropagation()}>
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={historyBusy === row.id}
                      onClick={() => void openHistoryRun(row.id)}
                    >
                      {active ? '已展开' : '查看详情'}
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={!retryCount || running || rowLive}
                      title="只跑失败/受阻/取消的岗位及其上游闭包，仍有效的缓存照常复用"
                      onClick={() => void rerunHistory(row, 'retry')}
                    >
                      续跑未完成 {retryCount ? `(${retryCount})` : ''}
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={running || rowLive}
                      title={row.targets.length ? '沿用这次的目标岗位（含上游闭包）再跑' : '再跑整图'}
                      onClick={() => void rerunHistory(row, 'same')}
                    >
                      沿用目标再跑
                    </button>
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={rowLive}
                      title="以更早一次已结束运行为基准，对照这一轮的岗位迁移与耗时"
                      onClick={() => compareWithPrevious(row)}
                    >
                      与上一次对比
                    </button>
                  </div>
                </div>
              );
            })}
          </div>

          <aside className="fc-history-detail">
            {historyDetail ? (
              <>
                <div className="panel-title" style={{ marginBottom: 0 }}>
                  运行详情 <span className="count mono">{historyDetail.id}</span>
                </div>
                <div className="muted small">
                  {historyDetail.created_at} · {JOB_LABEL[historyDetail.status] ?? historyDetail.status} ·{' '}
                  {(historyDetail.elapsed_ms / 1000).toFixed(1)}s · {planSizeLabel(historyDetail.plan_size)}
                  {historyDetail.targets.length ? ` · 目标 ${historyDetail.targets.join('、')}` : ' · 整图'}
                  {historyDetail.force_nodes?.length ? ` · 强跑 ${historyDetail.force_nodes.join('、')}` : ''}
                  {Object.keys(historyDetail.param_overrides ?? {}).length
                    ? ` · 改参 ${Object.keys(historyDetail.param_overrides).join('、')}`
                    : ''}
                </div>
                {historyDetail.ignored_param_overrides?.length ? (
                  <div className="fc-plan-note warn">
                    这些改参岗位不在本次计划闭包内，未应用：{historyDetail.ignored_param_overrides.join('、')}
                  </div>
                ) : null}
                {historyDetail.error ? <div className="fc-plan-note bad">{historyDetail.error}</div> : null}
                {historyDetail.retry_nodes?.length ? (
                  <div className="fc-plan-note warn">
                    有 {historyDetail.retry_nodes.length} 个岗位未完成：{historyDetail.retry_nodes.join('、')}
                    <button
                      className="btn btn-sm btn-ghost"
                      disabled={running}
                      onClick={() => void rerunHistory(historyDetail, 'retry')}
                    >
                      续跑未完成
                    </button>
                  </div>
                ) : (
                  <div className="fc-plan-note">这次运行的岗位都已完成或复用，没有待续跑岗位。</div>
                )}
                <div className="muted small">岗位（{Object.keys(historyDetail.nodes ?? {}).length}）</div>
                <div className="fc-history-nodes">
                  {Object.entries(historyDetail.nodes ?? {}).map(([nodeId, state]) => {
                    const result = historyDetail.result?.nodes?.[nodeId];
                    const status = result?.status ?? state.status ?? 'idle';
                    const retry = RETRY_STATE.has(status);
                    return (
                      <div className={`fc-hnode ${retry ? 'bad' : ''}`} key={nodeId}>
                        <span className={`fc-chip ${status === 'ok' || status === 'cached' ? 'ok' : retry ? 'bad' : ''}`}>
                          {NODE_STATUS_LABEL[status] ?? status}
                        </span>
                        <span className="fc-hnode-title">{titleOf(nodeId)}</span>
                        <span className="muted small mono">{nodeId}</span>
                        {result?.cached ? <span className="fc-chip ok">缓存</span> : null}
                        {result?.overridden ? <span className="fc-chip warn">改参执行</span> : null}
                        {result?.elapsed_ms ? <span className="muted small mono">{result.elapsed_ms}ms</span> : null}
                        {result?.error ? <span className="fc-hnode-error">{result.error}</span> : null}
                      </div>
                    );
                  })}
                  {!Object.keys(historyDetail.nodes ?? {}).length ? (
                    <div className="muted small">这次作业还没有岗位记录（可能在受理阶段就失败了）。</div>
                  ) : null}
                </div>
                <div className="muted small">事件（最近 {(historyDetail.events ?? []).length} 条）</div>
                <div className="fc-events">
                  {renderEventRows((historyDetail.events ?? []) as unknown as EventRow[], '这次作业没有留下事件。')}
                </div>
              </>
            ) : (
              <div className="muted small">点左侧任意一次运行，查看失败岗位、每岗耗时与事件。</div>
            )}
          </aside>
        </div>
      ) : null}
    </>
  );

  const compareFresh = !!comparison
    && comparison.base.job_id === compareBase
    && comparison.target.job_id === compareTarget;

  const compareRows = useMemo(() => {
    const rows = comparison?.nodes ?? [];
    if (compareFilter === 'all') return rows;
    if (compareFilter === 'issues') {
      return rows.filter(
        (row) => ['regressed', 'still_failed', 'status_changed'].includes(row.change) || !!row.target?.error,
      );
    }
    return rows.filter(
      (row) => row.change !== 'same' || row.output_changed || row.summary_changed || row.error_changed,
    );
  }, [comparison, compareFilter]);

  const comparePanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          运行对比 <span className="count">· 基准 → 目标</span>
        </span>
        <span className="fc-dock-side">
          <span className="muted small">基准</span>
          <select
            className="fc-compare-pick"
            value={compareBase}
            onChange={(event) => setCompareBase(event.target.value)}
          >
            <option value="">选一次运行…</option>
            {terminalRuns.map((row) => (
              <option key={row.id} value={row.id}>
                {row.created_at} · {JOB_LABEL[row.status] ?? row.status} · 交付 {row.counts.ok ?? 0}/复用{' '}
                {row.counts.cached ?? 0} · {row.id}
              </option>
            ))}
          </select>
          <span className="muted small">→</span>
          <select
            className="fc-compare-pick"
            value={compareTarget}
            onChange={(event) => setCompareTarget(event.target.value)}
          >
            <option value="">选一次运行…</option>
            {terminalRuns.map((row) => (
              <option key={row.id} value={row.id}>
                {row.created_at} · {JOB_LABEL[row.status] ?? row.status} · 交付 {row.counts.ok ?? 0}/复用{' '}
                {row.counts.cached ?? 0} · {row.id}
              </option>
            ))}
          </select>
          <button
            className="btn btn-sm btn-ghost"
            disabled={!compareBase || !compareTarget || compareBase === compareTarget}
            onClick={() => {
              setCompareBase(compareTarget);
              setCompareTarget(compareBase);
            }}
            title="交换基准与目标"
          >
            交换
          </button>
          <button
            className="btn btn-sm btn-ghost"
            disabled={!projectId || !compareBase || !compareTarget || compareBase === compareTarget || compareBusy}
            onClick={() => {
              if (projectId) void loadCompare(projectId, compareBase, compareTarget);
            }}
          >
            {compareBusy ? '对比中…' : '刷新对比'}
          </button>
        </span>
      </div>

      {!projectId ? <div className="muted small">先选择或新建一个项目。</div> : null}
      {projectId && terminalRuns.length < 2 ? (
        <div className="muted small">
          至少需要两次已结束运行才能对比：先跑一轮，改点参数或直接二跑，再回来看岗位迁移与缓存命中。
        </div>
      ) : null}
      {compareBase && compareTarget && compareBase === compareTarget ? (
        <div className="fc-plan-note warn">基准与目标是同一次运行：换一个再比。</div>
      ) : null}
      {compareError ? <div className="fc-plan-note bad">{compareError}</div> : null}
      {compareBusy && !compareFresh ? <div className="muted small">对比读取中…</div> : null}

      {comparison && compareFresh ? (
        <div className="fc-compare">
          <div className="fc-compare-runs">
            <div className={`fc-compare-run ${comparison.base.status === 'error' ? 'bad' : ''}`}>
              <div className="fc-run-top">
                <span className="fc-chip">基准</span>
                <span className={`fc-chip ${jobTone(comparison.base.status)}`}>
                  {JOB_LABEL[comparison.base.status] ?? comparison.base.status}
                </span>
                <span className="mono small">{comparison.base.job_id}</span>
                <span className="muted small">{comparison.base.created_at}</span>
                {comparison.base.force ? <span className="fc-chip">忽略缓存</span> : null}
                {comparison.base.force_nodes?.length ? (
                  <span className="fc-chip warn" title={`强跑：${comparison.base.force_nodes.join('、')}`}>
                    强跑 {comparison.base.force_nodes.length} 岗
                  </span>
                ) : null}
                {comparison.base.override_nodes?.length ? (
                  <span className="fc-chip warn" title={`改参：${comparison.base.override_nodes.join('、')}`}>
                    改参 {comparison.base.override_nodes.length} 岗
                  </span>
                ) : null}
              </div>
              <div className="fc-run-meta">
                <span>耗时 {formatMs(comparison.base.elapsed_ms)}</span>
                <span>{planSizeLabel(comparison.base.plan_size)}</span>
                <span>交付 {comparison.base.delivered}</span>
                <span>复用 {comparison.base.cached}（{percent(comparison.base.cache_rate)}）</span>
                <span className={comparison.base.failed ? 'bad-text' : ''}>未完成 {comparison.base.failed}</span>
                <span>{comparison.base.targets.length ? `目标 ${comparison.base.targets.join('、')}` : '整图'}</span>
              </div>
            </div>
            <div className={`fc-compare-run ${comparison.target.status === 'error' ? 'bad' : ''}`}>
              <div className="fc-run-top">
                <span className="fc-chip">目标</span>
                <span className={`fc-chip ${jobTone(comparison.target.status)}`}>
                  {JOB_LABEL[comparison.target.status] ?? comparison.target.status}
                </span>
                <span className="mono small">{comparison.target.job_id}</span>
                <span className="muted small">{comparison.target.created_at}</span>
                {comparison.target.force ? <span className="fc-chip">忽略缓存</span> : null}
                {comparison.target.force_nodes?.length ? (
                  <span className="fc-chip warn" title={`强跑：${comparison.target.force_nodes.join('、')}`}>
                    强跑 {comparison.target.force_nodes.length} 岗
                  </span>
                ) : null}
                {comparison.target.override_nodes?.length ? (
                  <span className="fc-chip warn" title={`改参：${comparison.target.override_nodes.join('、')}`}>
                    改参 {comparison.target.override_nodes.length} 岗
                  </span>
                ) : null}
              </div>
              <div className="fc-run-meta">
                <span>耗时 {formatMs(comparison.target.elapsed_ms)}</span>
                <span>{planSizeLabel(comparison.target.plan_size)}</span>
                <span>交付 {comparison.target.delivered}</span>
                <span>复用 {comparison.target.cached}（{percent(comparison.target.cache_rate)}）</span>
                <span className={comparison.target.failed ? 'bad-text' : ''}>未完成 {comparison.target.failed}</span>
                <span>{comparison.target.targets.length ? `目标 ${comparison.target.targets.join('、')}` : '整图'}</span>
              </div>
            </div>
          </div>

          <div className="fc-stats-kpis">
            <div className={`fc-kpi ${comparison.changes.regressed ? 'bad' : ''}`}>
              <span>岗位变化</span>
              <strong>{comparison.changes.changed}</strong>
              <small>
                共 {comparison.nodes.length} 个岗位 · 新增 {comparison.changes.added} / 移除 {comparison.changes.removed}
              </small>
            </div>
            <div className={`fc-kpi ${comparison.changes.regressed || comparison.changes.still_failed ? 'bad' : ''}`}>
              <span>恢复 / 回退</span>
              <strong>
                {comparison.changes.recovered} / {comparison.changes.regressed}
              </strong>
              <small>
                仍失败 {comparison.changes.still_failed} · 状态变化 {comparison.changes.status_changed}
              </small>
            </div>
            <div className="fc-kpi">
              <span>缓存命中 / 失效</span>
              <strong>
                {comparison.changes.cache_hit} / {comparison.changes.cache_lost}
              </strong>
              <small>
                目标复用率 {percent(comparison.target.cache_rate)} · 较基准{' '}
                {comparison.delta.cache_rate > 0 ? '+' : ''}
                {Math.round(comparison.delta.cache_rate * 100)}pp
              </small>
            </div>
            <div className={`fc-kpi ${comparison.delta.elapsed_ms < 0 ? 'ok' : comparison.delta.elapsed_ms > 0 ? 'bad' : ''}`}>
              <span>耗时差（目标 − 基准）</span>
              <strong>{signedMs(comparison.delta.elapsed_ms)}</strong>
              <small>
                {formatMs(comparison.base.elapsed_ms)} → {formatMs(comparison.target.elapsed_ms)}
              </small>
            </div>
            <div className={`fc-kpi ${comparison.changes.output_changed ? 'bad' : ''}`}>
              <span>产物结构变化</span>
              <strong>{comparison.changes.output_changed}</strong>
              <small>摘要变化 {comparison.changes.summary_changed} · 只比结构指纹</small>
            </div>
          </div>

          <div className="fc-runbar">
            <span className="panel-title" style={{ marginBottom: 0 }}>
              岗位对照{' '}
              <span className="count">
                · 显示 {compareRows.length}/{comparison.nodes.length}
              </span>
            </span>
            <span className="fc-dock-side">
              {(
                [
                  ['changed', '只看变化'],
                  ['issues', '只看问题'],
                  ['all', '全部岗位'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  className={`btn btn-sm ${compareFilter === key ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setCompareFilter(key)}
                >
                  {label}
                </button>
              ))}
            </span>
          </div>

          <div className="fc-compare-table">
            <div className="fc-compare-row head">
              <span>岗位</span>
              <span>基准</span>
              <span>目标</span>
              <span>变化 / 耗时差</span>
              <span>差异</span>
            </div>
            {compareRows.map((row) => {
              const change = CHANGE_LABEL[row.change];
              return (
                <div className={`fc-compare-row change-${row.change}`} key={row.node_id}>
                  <div className="fc-compare-node">
                    <span className="fc-hnode-title">{row.title}</span>
                    <span className="muted small mono">{row.node_id}</span>
                    <span className="fc-chip">{row.dept_label}</span>
                    {!row.in_graph ? <span className="fc-chip warn">不在当前画布</span> : null}
                  </div>
                  <CompareSide side={row.base} />
                  <CompareSide side={row.target} />
                  <div className="fc-compare-change">
                    <span className={`fc-chip ${change.tone}`}>{change.label}</span>
                    {row.elapsed_delta_ms ? (
                      <span className="muted small mono">{signedMs(row.elapsed_delta_ms)}</span>
                    ) : null}
                  </div>
                  <div className="fc-compare-flags">
                    {row.output_changed ? <span className="fc-chip warn">产物结构变化</span> : null}
                    {row.summary_changed ? <span className="fc-chip">摘要变化</span> : null}
                    {row.error_changed ? (
                      <span className={`fc-chip ${row.target?.error ? 'bad' : 'ok'}`}>
                        {row.target?.error ? '错误变化' : '错误消失'}
                      </span>
                    ) : null}
                    {!row.output_changed && !row.summary_changed && !row.error_changed && row.change === 'same' ? (
                      <span className="muted small">无差异</span>
                    ) : null}
                  </div>
                </div>
              );
            })}
            {!compareRows.length ? (
              <div className="muted small">当前筛选下没有岗位：切到「全部岗位」看看两次运行的全貌。</div>
            ) : null}
          </div>
        </div>
      ) : null}
    </>
  );

  const statsNodes = useMemo(() => {
    const rows = [...(stats?.nodes ?? [])];
    if (statsSort === 'fail') {
      rows.sort((a, b) => b.error + b.blocked - (a.error + a.blocked) || b.total_ms - a.total_ms);
    } else if (statsSort === 'cache') {
      rows.sort((a, b) => a.cache_rate - b.cache_rate || b.appearances - a.appearances);
    } else {
      rows.sort((a, b) => b.total_ms - a.total_ms);
    }
    return rows;
  }, [stats, statsSort]);

  const statsPanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          运行统计 <span className="count">· 最近 {stats?.window ?? 0} 次已结束运行</span>
        </span>
        <span className="fc-dock-side">
          {stats?.runs.overridden ? (
            <span className="fc-chip warn" title="最近窗口内带本轮参数覆盖的运行轮数">
              含改参 {stats.runs.overridden} 轮
            </span>
          ) : null}
          <span className="muted small">统计窗口</span>
          <select
            className="fc-stats-window"
            value={statsLimit}
            onChange={(event) => {
              const next = Number(event.target.value);
              setStatsLimit(next);
              if (projectId) void loadStats(projectId, next);
            }}
          >
            <option value={20}>20 次</option>
            <option value={50}>50 次</option>
            <option value={100}>100 次</option>
          </select>
          <button
            className="btn btn-sm btn-ghost"
            disabled={!projectId || statsBusy}
            onClick={() => {
              if (projectId) void loadStats(projectId);
            }}
          >
            {statsBusy ? '读取中…' : '刷新统计'}
          </button>
        </span>
      </div>

      {!projectId ? <div className="muted small">先选择或新建一个项目。</div> : null}
      {statsError ? <div className="fc-plan-note bad">{statsError}</div> : null}
      {projectId && stats && !stats.runs.total ? (
        <div className="muted small">这个项目还没有已结束的运行：先跑一轮，统计会按岗位聚合成败、复用与耗时。</div>
      ) : null}

      {stats && stats.runs.total ? (
        <div className="fc-stats">
          <div className="fc-stats-kpis">
            <div className="fc-kpi">
              <span>运行</span>
              <strong>{stats.runs.total}</strong>
              <small>成功 {stats.runs.ok} · 失败 {stats.runs.error} · 取消 {stats.runs.cancelled}</small>
            </div>
            <div className="fc-kpi">
              <span>成功率</span>
              <strong>{percent(stats.runs.success_rate)}</strong>
              <small>
                {stats.runs.forced || stats.runs.partial_forced
                  ? `整轮忽略缓存 ${stats.runs.forced} 次 · 岗位强跑 ${stats.runs.partial_forced} 次`
                  : '按作业口径'}
              </small>
            </div>
            <div className="fc-kpi">
              <span>缓存复用率</span>
              <strong>{percent(stats.runs.cache_rate)}</strong>
              <small>复用 {stats.runs.cached} · 交付 {stats.runs.delivered}</small>
            </div>
            <div className="fc-kpi">
              <span>平均耗时</span>
              <strong>{formatMs(stats.runs.elapsed_avg_ms)}</strong>
              <small>P95 {formatMs(stats.runs.elapsed_p95_ms)} · 计划 {stats.runs.plan_nodes} 岗次</small>
            </div>
            <div className={`fc-kpi ${stats.runs.node_errors + stats.runs.node_blocked ? 'bad' : ''}`}>
              <span>未完成岗位</span>
              <strong>{stats.runs.node_errors + stats.runs.node_blocked + stats.runs.node_cancelled}</strong>
              <small>
                失败 {stats.runs.node_errors} · 受阻 {stats.runs.node_blocked} · 取消 {stats.runs.node_cancelled}
              </small>
            </div>
          </div>
          <div className="muted small">
            窗口 {stats.runs.oldest_at || '—'} → {stats.runs.latest_at || '—'} · 生成于 {stats.generated_at}
          </div>

          {stats.departments.length ? (
            <section className="fc-stats-block">
              <div className="panel-title">
                部门画像 <span className="count">· {stats.departments.length} 个部门</span>
              </div>
              <div className="fc-dept-table">
                <div className="fc-dept-row head">
                  <span>部门</span>
                  <span>岗位数</span>
                  <span>出现次数</span>
                  <span>交付/复用/未完成</span>
                  <span>复用率</span>
                  <span>平均耗时</span>
                </div>
                {stats.departments.map((row) => (
                  <div className="fc-dept-row" key={row.dept || 'unknown'}>
                    <span>
                      {row.label} <span className="muted small mono">{row.dept || '—'}</span>
                    </span>
                    <span className="mono">{row.nodes}</span>
                    <span className="mono">{row.appearances}</span>
                    <span className="mono">
                      {row.ok} / {row.cached} /{' '}
                      <span className={row.error + row.blocked ? 'bad-text' : ''}>{row.error + row.blocked}</span>
                    </span>
                    <span className="fc-rate" title={`复用 ÷（交付 + 复用）= ${percent(row.cache_rate)}`}>
                      <span className="fc-rate-track">
                        <i style={{ width: percent(row.cache_rate) }} />
                      </span>
                      <b>{percent(row.cache_rate)}</b>
                    </span>
                    <span className="mono">{formatMs(row.avg_ms)}</span>
                  </div>
                ))}
              </div>
            </section>
          ) : null}

          <section className="fc-stats-block">
            <div className="fc-runbar">
              <span className="panel-title" style={{ marginBottom: 0 }}>
                岗位热点 <span className="count">· {stats.nodes.length} 个岗位</span>
              </span>
              <span className="fc-dock-side">
                {(
                  [
                    ['time', '耗时优先'],
                    ['fail', '失败优先'],
                    ['cache', '低复用优先'],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    className={`btn btn-sm ${statsSort === key ? 'btn-primary' : 'btn-ghost'}`}
                    onClick={() => setStatsSort(key)}
                  >
                    {label}
                  </button>
                ))}
              </span>
            </div>
            <div className="fc-stats-nodes">
              {statsNodes.map((row) => (
                <div className={`fc-stat-node ${row.error + row.blocked ? 'has-error' : ''}`} key={row.node_id}>
                  <div className="fc-run-top">
                    <span className="fc-hnode-title">{row.title}</span>
                    <span className="muted small mono">{row.node_id}</span>
                    <span className="fc-chip">{row.dept_label}</span>
                    {row.error + row.blocked ? (
                      <span className="fc-chip bad">失败 {row.error + row.blocked}</span>
                    ) : null}
                    {row.skipped ? <span className="fc-chip">静音 {row.skipped}</span> : null}
                  </div>
                  <div className="fc-run-meta">
                    <span>出现 {row.appearances} 轮</span>
                    <span>交付 {row.ok}</span>
                    <span>
                      复用 {row.cached}（{percent(row.cache_rate)}）
                    </span>
                    <span>取消 {row.cancelled}</span>
                    <span>平均 {formatMs(row.avg_ms)}</span>
                    <span>P95 {formatMs(row.p95_ms)}</span>
                    <span>累计 {formatMs(row.total_ms)}</span>
                  </div>
                </div>
              ))}
              {!statsNodes.length ? <div className="muted small">窗口内没有岗位执行记录。</div> : null}
            </div>
          </section>

          <section className="fc-stats-block">
            <div className="panel-title">
              最近失败 <span className="count">· 最多 10 条</span>
            </div>
            {stats.failures.length ? (
              <div className="fc-stats-failures">
                {stats.failures.map((row) => (
                  <div className="fc-stat-failure" key={`${row.job_id}-${row.node_id}`}>
                    <span className={`fc-chip ${row.status === 'blocked' ? 'warn' : 'bad'}`}>
                      {row.status === 'blocked' ? '受阻' : '失败'}
                    </span>
                    <span className="fc-hnode-title">{row.title}</span>
                    <span className="muted small mono">{row.node_id}</span>
                    <span className="muted small">{row.created_at}</span>
                    <span className="fc-hnode-error">{row.error || '（没有留下错误文本）'}</span>
                    <button
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        setView('history');
                        void openHistoryRun(row.job_id);
                      }}
                    >
                      查看现场
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="muted small">窗口内没有失败岗位。</div>
            )}
          </section>
        </div>
      ) : null}
    </>
  );

  const toggleForceNode = (nodeId: string) => {
    setForceNodes((prev) => (prev.includes(nodeId) ? prev.filter((id) => id !== nodeId) : [...prev, nodeId]));
  };

  /* 选择、force、强跑名单或改参刚变、新预检还没回来时，旧计划已经不代表当前意图，不能直接开跑。 */
  const plannedOverrides = useMemo(() => {
    if (!runPreview) return runOverrides;
    const planned = new Set(runPreview.plan);
    const applied: ParamOverrides = {};
    for (const [nodeId, patch] of Object.entries(runOverrides)) {
      if (planned.has(nodeId)) applied[nodeId] = patch;
    }
    return applied;
  }, [runOverrides, runPreview]);

  const ignoredOverrideIds = useMemo(() => {
    if (!runPreview) return [];
    const planned = new Set(runPreview.plan);
    return Object.keys(runOverrides).filter((nodeId) => !planned.has(nodeId));
  }, [runOverrides, runPreview]);

  const previewStale = !!runPreview
    && (runPreview.force !== force
      || [...runPreview.force_nodes, ...runPreview.ignored_force_nodes].sort().join('|')
        !== [...forceNodes].sort().join('|')
      || [...runPreview.targets].sort().join('|') !== [...selected].sort().join('|')
      || stableOverrides(plannedOverrides) !== stableOverrides(runPreview.param_overrides)
      || [...ignoredOverrideIds].sort().join('|') !== [...runPreview.ignored_param_overrides].sort().join('|'));

  const overridePayload = Object.keys(runOverrides).length ? { param_overrides: runOverrides } : {};

  const planPanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          运行预检 <span className="count">· {runPreview ? `${runPreview.counts.total} 个岗位` : '尚未预检'}</span>
        </span>
        <span className="fc-dock-side">
          {runPreview ? (
            <>
              <span className="fc-chip ok">复用 {runPreview.counts.cached} 岗</span>
              <span className={`fc-chip ${runPreview.counts.run ? 'warn' : ''}`}>重跑 {runPreview.counts.run} 岗</span>
              {runPreview.force_nodes.length ? (
                <span className="fc-chip warn">强跑 {runPreview.force_nodes.length} 岗</span>
              ) : null}
              {Object.keys(runPreview.param_overrides).length ? (
                <span className="fc-chip warn">改参 {Object.keys(runPreview.param_overrides).length} 岗</span>
              ) : null}
              {runPreview.counts.muted ? <span className="fc-chip">静音 {runPreview.counts.muted} 岗</span> : null}
              {runPreview.counts.error ? <span className="fc-chip bad">未知类型 {runPreview.counts.error} 岗</span> : null}
            </>
          ) : null}
          {forceNodes.length ? (
            <button className="btn btn-sm btn-ghost" onClick={() => setForceNodes([])} title="取消所有岗位级强跑标记">
              清空强跑
            </button>
          ) : null}
          {Object.keys(runOverrides).length ? (
            <button className="btn btn-sm btn-ghost" onClick={() => setRunOverrides({})} title="取消所有本轮参数覆盖">
              清空改参
            </button>
          ) : null}
          <button className="btn btn-sm btn-ghost" onClick={() => void loadRunPreview()} disabled={!projectId || dirty || running || runPreviewBusy}>
            {runPreviewBusy ? '预检中…' : '刷新预检'}
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={() =>
              void startRun({
                ...(runPreview?.targets.length ? { targets: runPreview.targets } : {}),
                ...(forceNodes.length ? { force_nodes: forceNodes } : {}),
                ...overridePayload,
              })
            }
            disabled={!projectId || dirty || running || !runPreview || runPreviewBusy || previewStale}
            title="按当前预检的计划运行；只读预检本身不会执行任何岗位，勾选的强跑岗位会跳过缓存"
          >
            按此计划运行
          </button>
        </span>
      </div>

      {dirty ? (
        <div className="fc-plan-note warn">
          画布有未保存改动：预检与运行都基于后端已存的工作流。<button className="btn btn-sm btn-ghost" onClick={() => void doSave()}>先保存</button>
        </div>
      ) : null}
      {!dirty && running ? <div className="fc-plan-note">正在跑图：下面是开工前的预检快照，跑完会自动刷新。</div> : null}
      {!dirty && !running && previewStale ? <div className="fc-plan-note">预检正在跟随当前选择 / 缓存开关刷新，回来前不按旧计划开跑。</div> : null}
      {!dirty && runPreviewError ? <div className="fc-plan-note bad">预检失败：{runPreviewError}</div> : null}
      {!dirty && runPreview?.ignored_force_nodes.length ? (
        <div className="fc-plan-note warn">
          这些强跑岗位不在本次计划闭包内，不会执行：{runPreview.ignored_force_nodes.join('、')}
        </div>
      ) : null}
      {!dirty && runPreview?.ignored_param_overrides.length ? (
        <div className="fc-plan-note warn">
          这些改参岗位不在本次计划闭包内，本轮不会应用：{runPreview.ignored_param_overrides.join('、')}
        </div>
      ) : null}
      {!dirty && !running && !runPreview && !runPreviewError ? (
        <div className="muted small">{projectId ? '预检载入中…' : '先选择或新建一个项目。'}</div>
      ) : null}

      {runPreview ? (
        <>
          <div className="muted small">
            {runPreview.targets.length
              ? `目标是 ${runPreview.targets.length} 个岗位，按上游闭包共 ${runPreview.counts.total} 岗`
              : `整图 ${runPreview.counts.total} 岗`}
            {runPreview.force ? ' · 已勾选忽略缓存' : ''}
            {runPreview.force_nodes.length ? ` · 强跑 ${runPreview.force_nodes.slice(0, 4).join('、')}${runPreview.force_nodes.length > 4 ? '…' : ''}` : ''}
            {Object.keys(runPreview.param_overrides).length ? ` · 改参 ${Object.keys(runPreview.param_overrides).slice(0, 4).join('、')}` : ''}
            {' '}· 生成于 {runPreview.generated_at}
          </div>
          <div className="fc-plan-list">
            {runPreview.nodes.map((row, index) => {
              const meta = PREVIEW_LABEL[row.state];
              return (
                <div
                  className={`fc-plan-row state-${row.state}${forceNodes.includes(row.node_id) ? ' forced' : ''}${row.override_keys.length ? ' overridden' : ''}`}
                  key={row.node_id}
                  role="button"
                  tabIndex={0}
                  title={`${row.node_id} · 指纹 ${row.signature}`}
                  onClick={() => setSelected([row.node_id])}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' || event.key === ' ') setSelected([row.node_id]);
                  }}
                >
                  <span className="mono muted">{String(index + 1).padStart(2, '0')}</span>
                  <span className="fc-plan-icon">{specs[row.type]?.icon ?? '·'}</span>
                  <span className="fc-plan-title">{row.title}</span>
                  <span className={`fc-chip ${meta.cls}`}>{meta.label}</span>
                  <label
                    className="fc-plan-force"
                    onClick={(event) => event.stopPropagation()}
                    title={
                      force
                        ? '已勾选全局忽略缓存：所有岗位都会重跑，无需岗位级强跑'
                        : '只跳过这个岗位的缓存；其余岗位仍按签名复用'
                    }
                  >
                    <input
                      type="checkbox"
                      checked={forceNodes.includes(row.node_id)}
                      disabled={force || running || row.state === 'muted' || row.state === 'error'}
                      onChange={() => toggleForceNode(row.node_id)}
                    />
                    强跑
                  </label>
                  {row.override_keys.length ? (
                    <span className="fc-chip warn" title={`本轮改参：${row.override_keys.join('、')}`}>
                      改参 {row.override_keys.length} 项
                    </span>
                  ) : null}
                  <button
                    className="btn btn-sm btn-ghost fc-plan-override"
                    onClick={(event) => {
                      event.stopPropagation();
                      setOverrideTarget(row.node_id);
                    }}
                    disabled={running || row.state === 'muted' || row.state === 'error'}
                    title={row.state === 'muted' ? '已静音的岗位不能改参' : '只改这一次运行，不写入画布'}
                  >
                    改参
                  </button>
                  <span className="fc-plan-reason">
                    {row.reason}
                    {row.cached_at ? ` · 交付于 ${row.cached_at}` : ''}
                  </span>
                  <span className="muted small mono">{row.node_id}</span>
                </div>
              );
            })}
          </div>
        </>
      ) : null}

      <div className="muted small">
        预检是只读的：不建作业、不跑岗位、不写缓存。它只回答「缓存会不会命中、为什么」；
        运行时错误与上游受阻无法预知，实际结果仍以跑图为准。岗位级强跑只跳过指定岗位的缓存；
        若其产物结构未变，下游仍会按签名复用。改参只叠加到本轮，签名变化会让该岗位及下游重算，
        未改的上游仍复用；缓存与同名产物会被本轮覆盖，历史版本不单独保留。
      </div>
    </>
  );

  const filesPanel = (
    <>
      <div className="fc-runbar">
        <span className="panel-title" style={{ marginBottom: 0 }}>
          产物清单 <span className="count">· {artifacts.length} 个文件</span>
        </span>
        <span className="fc-dock-side">
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => projectId && void runAcceptance(projectId)}
            disabled={!projectId || acceptanceBusy || running}
            title="只读验收：终端岗位是否交付、引用文件是否在盘、时间轴是否为空（与打包同一口径）"
          >
            {acceptanceBusy ? '验收中…' : '交付验收'}
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => void downloadBundle()}
            disabled={!projectId || !artifacts.length || running || busy === 'bundle'}
            title="把工作流、全部产物与 manifest.json 打成一个 zip（验收未过会被后端拒绝）"
          >
            {busy === 'bundle' ? '打包中…' : '下载交付包 (.zip)'}
          </button>
        </span>
      </div>
      {acceptance ? (
        <div className={`fc-banner ${acceptance.verdict}`}>
          <span className="fc-banner-title">
            {ACCEPTANCE_VERDICT_LABEL[acceptance.verdict]}
            <span className="count">
              {' · '}阻断 {acceptance.counts.error} · 警告 {acceptance.counts.warn} · 信息 {acceptance.counts.info}
            </span>
          </span>
          <span className="fc-banner-stats muted small">
            终端 {acceptance.stats.terminals_ready}/{acceptance.stats.terminals} 已交付 · 引用核对 {acceptance.stats.refs_checked}（缺失 {acceptance.stats.refs_missing}·空 {acceptance.stats.refs_empty}）
            {acceptance.stats.stale ? ` · 过期 ${acceptance.stats.stale}` : ''}
            {acceptance.stats.simulated_nodes ? ` · 占位 ${acceptance.stats.simulated_nodes}` : ''}
            {acceptance.film ? ` · 成片 ${acceptance.film.shots} 镜/${acceptance.film.duration_s.toFixed(1)}s（随包附 timeline.json）` : ''}
          </span>
          {acceptance.issues.length ? (
            <ul className="fc-banner-issues">
              {acceptance.issues.slice(0, 8).map((issue, i) => (
                <li key={`${issue.code}-${i}`} className={`fc-issue ${issue.level}`}>
                  <span className="fc-issue-msg">{issue.message}</span>
                  {issue.node_id ? <span className="mono small muted">{issue.node_id}</span> : null}
                </li>
              ))}
              {acceptance.issues.length > 8 ? (
                <li className="fc-issue muted small">其余 {acceptance.issues.length - 8} 条略</li>
              ) : null}
            </ul>
          ) : (
            <span className="muted small">工作流体检无错、终端岗位均有可复用交付、引用文件逐个在盘——可以打包。</span>
          )}
        </div>
      ) : null}
      {deliveries ? (
        <div className="fc-ledger">
          <div className="fc-ledger-head">
            <b>交付台账</b>
            <span className="count">
              {' · '}放行 {deliveries.summary.delivered} · 拦下 {deliveries.summary.blocked} 次
              {deliveries.summary.last_delivered_at ? ` · 最近交付 ${deliveries.summary.last_delivered_at}` : ' · 尚未交付过'}
            </span>
            <button className="btn btn-sm btn-ghost" onClick={() => projectId && void runDeliveries(projectId)} disabled={!projectId} title="重读台账（只读）">
              刷新
            </button>
          </div>
          {!deliveries.entries.length ? (
            <div className="muted small">还没有打包记录：每次点「下载交付包」都会在这里留下一笔（无论放行还是被拦）。</div>
          ) : (
            <ul className="fc-ledger-list">
              {deliveries.entries.slice(0, 12).map((row, i) => (
                <li key={`${row.at}-${i}`} className={`fc-ledger-row ${row.event}`}>
                  <span className={`fc-ledger-tag ${row.event}`}>{row.event === 'delivered' ? '已交付' : '已拦下'}</span>
                  <span className="mono small muted">{row.at}</span>
                  <span className="fc-ledger-meta">
                    {row.event === 'delivered' ? (
                      <>
                        {row.artifact_count} 件产物 · {formatBytes(row.archive_bytes || row.total_bytes)}
                        {row.film_shots ? ` · ${row.film_shots} 镜成片` : ''}
                        {row.simulated_nodes ? ` · 占位 ${row.simulated_nodes}` : ''}
                        {row.warn_count ? ` · 警告 ${row.warn_count}` : ''}
                        {row.archive_sha256 ? <span className="mono small" title={row.archive_sha256}> sha256:{row.archive_sha256.slice(0, 12)}</span> : null}
                      </>
                    ) : (
                      <>
                        验收结论 {ACCEPTANCE_VERDICT_LABEL[row.verdict]} · 终端 {row.terminals_ready}/{row.terminals} · 阻断 {row.error_count}
                        {row.refs_missing ? ` · 缺引用 ${row.refs_missing}` : ''}
                      </>
                    )}
                  </span>
                  {row.blockers && row.blockers.length ? <div className="fc-ledger-blockers muted small">{row.blockers[0]}</div> : null}
                </li>
              ))}
              {deliveries.entries.length > 12 ? <li className="fc-ledger-row muted small">其余 {deliveries.entries.length - 12} 条略（台账只留最近 {deliveries.summary.capped} 条）</li> : null}
            </ul>
          )}
        </div>
      ) : null}
      <div className="fc-file-groups">
        {Object.entries(groups).map(([group, count]) => (
          <span className="fc-chip" key={group}>
            {GROUP_LABEL[group] ?? group} {count}
          </span>
        ))}
        {!artifacts.length ? <span className="muted small">项目还没有产物：先跑一次图，交付包会连同工作流一起打包。</span> : null}
      </div>
      <div className="fc-files-body">
        <div className="fc-file-list">
          {artifacts.map((row) => (
            <div className="fc-file" key={row.rel}>
              {IMAGE_EXT.test(row.name) ? <img src={mediaUrl(row.url)} alt={row.name} loading="lazy" /> : null}
              <span className="fc-file-name" title={row.rel}>
                {row.rel}
              </span>
              <span className="mono small muted">{row.size_kb} KB</span>
              {AUDIO_EXT.test(row.name) ? <audio controls preload="none" src={mediaUrl(row.url)} /> : null}
              {TEXT_EXT.test(row.name) ? (
                <button className="btn btn-sm btn-ghost" onClick={() => void showText(row)}>
                  预览
                </button>
              ) : (
                <a className="btn btn-sm btn-ghost" href={mediaUrl(row.url)} target="_blank" rel="noreferrer">
                  打开
                </a>
              )}
            </div>
          ))}
        </div>
        {preview ? (
          <div className="fc-preview">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="mono small">{preview.rel}</span>
              <button className="btn btn-sm btn-ghost" onClick={() => setPreview(null)}>
                收起
              </button>
            </div>
            <pre className="fc-text">{preview.text}</pre>
          </div>
        ) : null}
      </div>
    </>
  );

  const playerPanel = (
    <>
      {timeline ? (
        <div className="muted small">来源岗位：{titleOf(timeline.id)}（画布上最下游的那条时间轴）· 试映的是岗位产物，不是渲染回写后的成片</div>
      ) : null}
      {timeline ? (
        <div
          className={`fc-reconcile ${filmDelivery?.matches === true ? 'ok' : filmDelivery?.matches === false ? 'warn' : 'idle'}`}
          title="回看对账：拿现在这条成片的内容指纹，与最近一次交付装箱的那条比——不回放旧包，只说清内容变了没"
        >
          {filmDelivery?.matches === true ? (
            <>✓ 与最近一次交付一致（{filmDelivery.delivered_at}）{filmDelivery.archive_sha256 ? ` · 包 sha256 ${filmDelivery.archive_sha256.slice(0, 12)}` : ''}</>
          ) : filmDelivery?.matches === false ? (
            <>⚠ 这条片子在最近一次交付后又改过，交付包里的成片已过时——需重新下载交付包</>
          ) : (
            <>这条成片尚未随交付包装箱：到「产物」页下载交付包后即可回看对账</>
          )}
        </div>
      ) : null}
      <Player timeline={timeline?.data ?? null} onToast={onToast} />
    </>
  );

  return (
    <div className="fc-studio">
      <input
        ref={importRef}
        type="file"
        accept=".json,application/json"
        style={{ display: 'none' }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void importWorkflow(file);
        }}
      />
      <WorkbenchHead
        meta={workbenchMeta('factory')}
        subtitle={`每个岗位一个节点：${palette ? `${palette.node_count} 个岗位 · ${palette.departments.length} 个部门` : '岗位目录载入中'}，从命题到成片走同一张画布`}
        chain={{ upstream: '创作任务 · 分镜 / 镜头脚本', downstream: '创作任务 · 发布与复盘（交付包下载）' }}
        onHome={onBack}
        sourceTask={sourceTask}
        onOpenTask={onOpenTask}
      >
        <nav className="fc-views">
          {VIEWS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.key}
                className={`fc-view${view === item.key ? ' active' : ''}`}
                title={item.hint}
                onClick={() => setView(item.key)}
              >
                <Icon size={14} strokeWidth={2} />
                <span className="fc-view-label">{item.label}</span>
                <span className="fc-view-label-short">{item.short}</span>
                {item.key === 'screen' && timeline ? <i>{timeline.data.shots?.length ?? 0} 镜</i> : null}
                {item.key === 'files' && artifacts.length ? <i>{artifacts.length}</i> : null}
                {item.key === 'events' && events.length ? <i>{events.length}</i> : null}
                {item.key === 'inspect' && selected.length ? <i>{selected.length}</i> : null}
                {item.key === 'history' && history.some((row) => row.retry_nodes?.length) ? (
                  <i>{history.reduce((sum, row) => sum + (row.retry_nodes?.length ?? 0), 0)}</i>
                ) : null}
                {item.key === 'plan' && runPreview && runPreview.counts.run ? <i>{runPreview.counts.run}</i> : null}
              </button>
            );
          })}
        </nav>
      </WorkbenchHead>

      {/* ---------------- 项目与生产命令台 ---------------- */}
      <div className="fc-command">
        <section className="fc-command-row">
          <span className="fc-command-label" title="选择已有项目，或从预设 / 工作流新建">
            <IconFolder size={13} strokeWidth={2} /> 项目
          </span>
          <div className="fc-bar">
            <select
              className="fc-pick"
              value={projectId ?? ''}
              onChange={(event) => {
                if (event.target.value) void openProject(event.target.value);
              }}
            >
              <option value="" disabled>
                选择项目…
              </option>
              {projects.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.title}（{row.node_count} 个岗位 · {row.edge_count} 条连线）
                </option>
              ))}
            </select>
            <input className="fc-title" value={titleDraft} onChange={(event) => setTitleDraft(event.target.value)} placeholder="项目标题" />

            <div className="fc-bar-actions">
              <input className="fc-title fc-new" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} placeholder="新项目标题" />
              <select className="fc-pick" value={newPreset} onChange={(event) => setNewPreset(event.target.value)}>
                {presets.map((row) => (
                  <option key={row.key} value={row.key}>
                    {row.title} · {row.node_count} 个岗位
                  </option>
                ))}
              </select>
              <button className={`btn btn-sm ${projectId ? 'btn-ghost' : 'btn-primary'}`} onClick={doCreate} disabled={busy === 'create'}>
                <IconPlus size={13} strokeWidth={2.2} /> 新建项目
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => importRef.current?.click()}
                disabled={busy === 'import'}
                title="从工作流 JSON 新建项目（导出的 .workflow.json 或裸 nodes/edges 都认）"
              >
                <IconUpload size={13} /> {busy === 'import' ? '导入中…' : '导入工作流'}
              </button>
            </div>
          </div>
        </section>

        <section className="fc-command-row fc-command-produce">
          <span className="fc-command-label" title="铺预设、保存画布，然后运行全图或选中闭包">
            <IconPlay size={13} strokeWidth={2} /> 生产
          </span>
          <div className="fc-bar">
            <div className="fc-bar-actions">
              <select className="fc-pick" value={presetPick} onChange={(event) => setPresetPick(event.target.value)}>
                {presets.map((row) => (
                  <option key={row.key} value={row.key}>
                    {row.title}
                  </option>
                ))}
              </select>
              <button className="btn btn-sm btn-ghost" onClick={() => void applyPreset(presetPick)} disabled={!projectId || busy === 'preset'} title="用预设覆盖当前画布（保存后才落盘）">
                <IconLayers size={13} /> 铺到画布
              </button>
              <button className="btn btn-sm btn-ghost" onClick={doSave} disabled={!projectId || busy === 'save'} title="保存工作流与标题">
                <IconSave size={13} /> 保存{dirty ? <span className="save-dot" title="有未保存改动" /> : null}
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={exportWorkflow}
                disabled={!graph.nodes.length}
                title="导出画布工作流 JSON（可再次导入；含未保存改动）"
              >
                <IconDownload size={13} /> 导出工作流
              </button>
              <button className="btn btn-sm btn-primary" onClick={() => void startRun({ ...overridePayload })} disabled={!projectId || running || busy === 'run'}>
                <IconPlay size={13} /> 运行全图
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => void startRun({ targets: selected, ...overridePayload })}
                disabled={!projectId || !selected.length || running || busy === 'run'}
                title="只跑选中岗位及其上游闭包"
              >
                运行选中{selected.length ? `（${selected.length} 岗）` : ''}
              </button>
              {!running && runPreview ? (
                <button
                  className={`fc-chip ${runPreview.counts.run ? 'warn' : 'ok'}`}
                  onClick={() => setView('plan')}
                  title="打开运行预检：看清哪些岗位复用缓存、哪些会重跑"
                >
                  预检 · {runPreview.targets.length ? '选中闭包' : '全图'} · 复用 {runPreview.counts.cached} 岗 · 重跑 {runPreview.counts.run} 岗
                  {runPreview.counts.muted ? ` · 静音 ${runPreview.counts.muted} 岗` : ''}
                </button>
              ) : null}
              {!running && forceNodes.length ? (
                <button
                  className="fc-chip warn"
                  onClick={() => setView('plan')}
                  title={`岗位级强跑在预检页设置，按此计划运行生效：${forceNodes.join('、')}`}
                >
                  待强跑 {forceNodes.length} 岗
                </button>
              ) : null}
              {!running && Object.keys(runOverrides).length ? (
                <button
                  className="fc-chip warn"
                  onClick={() => setView('plan')}
                  title={`本轮参数覆盖，按此计划运行生效（不写入画布）：${Object.keys(runOverrides).join('、')}`}
                >
                  改参 {Object.keys(runOverrides).length} 岗
                </button>
              ) : null}
              <label className="fc-force" title="忽略签名缓存，全部重跑">
                <input
                  type="checkbox"
                  checked={force}
                  onChange={(event) => {
                    setForce(event.target.checked);
                    if (event.target.checked) setForceNodes([]);
                  }}
                />
                忽略缓存
              </label>
              {job?.status === 'cancelling' ? (
                <button className="btn btn-sm btn-ghost" disabled title="当前岗位收尾后停止，未开工岗位不再执行">
                  取消中…
                </button>
              ) : running ? (
                <button className="btn btn-sm btn-ghost" onClick={cancelRun}>
                  取消
                </button>
              ) : null}
            </div>
            <button
              className={`btn btn-sm fc-delete-project${confirmDelete ? ' btn-bad' : ''}`}
              onClick={() => {
                if (!confirmDelete) {
                  setConfirmDelete(true);
                  window.setTimeout(() => setConfirmDelete(false), 4000);
                  return;
                }
                void doDelete();
              }}
              disabled={!projectId || busy === 'delete'}
              title="删除项目目录（工作流 + 产物）"
            >
              <IconTrash size={13} />
              {confirmDelete ? '确认删除?' : '删除项目'}
            </button>
          </div>
        </section>
      </div>

      <div className="fc-meta">
        <span className="fc-chip">{graph.nodes.length} 个岗位</span>
        <span className="fc-chip">{graph.edges.length} 条连线</span>
        {project?.last_run ? (
          <span
            className="muted small mono"
            title={`作业 ${project.last_run.job_id}${project.last_run.targets.length ? ` · 目标 ${project.last_run.targets.join('、')}` : ''}${
              project.last_run.force_nodes?.length ? ` · 强跑 ${project.last_run.force_nodes.join('、')}` : ''
            }${project.last_run.override_nodes?.length ? ` · 改参 ${project.last_run.override_nodes.join('、')}` : ''}`}
          >
            上次运行 {JOB_LABEL[project.last_run.status] ?? project.last_run.status} · {project.last_run.finished_at}
          </span>
        ) : null}
        {dirty ? <span className="fc-chip warn">未保存改动</span> : null}
        {errors.length ? (
          <span className="fc-chip bad" title={errors.map((issue) => issue.message).join('\n')}>
            体检错误 {errors.length}
          </span>
        ) : (
          <span className="fc-chip ok">体检通过</span>
        )}
        {warnings.length ? (
          <span className="fc-chip warn" title={warnings.map((issue) => issue.message).join('\n')}>
            提醒 {warnings.length}
          </span>
        ) : null}
        {job ? (
          <span className={`fc-chip ${job.status === 'ok' ? 'ok' : job.status === 'error' ? 'bad' : 'warn'}`}>
            作业{JOB_LABEL[job.status] ?? job.status}
          </span>
        ) : null}
        {running ? (
          <span className="muted small mono">
            {Math.round((job?.progress ?? 0) * 100)}%{job?.plan_size ? ` · 计划 ${job.plan_size} 岗` : ''}
          </span>
        ) : null}
        {job && !running ? (
          <span className="muted small mono">
            {`交付 ${counts.ok ?? 0} · 复用 ${counts.cached ?? 0} · 失败 ${counts.error ?? 0} · 受阻 ${counts.blocked ?? 0} · 跳过 ${counts.skipped ?? 0}`}
            {counts.cancelled ? ` · 取消 ${counts.cancelled}` : ''} · {((job.elapsed_ms ?? 0) / 1000).toFixed(1)}s
          </span>
        ) : null}
        {job?.error ? <span className="fc-chip bad">{job.error}</span> : null}
        {timeline ? (
          <button className="btn btn-sm btn-ghost" onClick={() => setView('screen')} title={`来源岗位：${titleOf(timeline.id)}`}>
            试映 {timeline.data.shots?.length ?? 0} 镜 · {Number(timeline.data.duration_s ?? 0).toFixed(1)}s
          </button>
        ) : null}
        {view === 'canvas' ? (
          <span className="fc-drawer-toggles">
            <button
              className={`btn btn-sm ${drawers.palette ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setDrawers((prev) => ({ ...prev, palette: !prev.palette }))}
              title="在画布上叠一层岗位面板（不离开画布）"
            >
              岗位面板
            </button>
            <button
              className={`btn btn-sm ${drawers.inspect ? 'btn-primary' : 'btn-ghost'}`}
              onClick={() => setDrawers((prev) => ({ ...prev, inspect: !prev.inspect }))}
              title="在画布上叠一层检查器"
            >
              检查器
            </button>
          </span>
        ) : null}
      </div>

      {/* ---------------- 工作区：画布占满整页，其余面板各自一页 ---------------- */}
      {view === 'canvas' ? (
        <div className="fc-work">
          {projectId ? (
            <Canvas
              graph={graph}
              specs={specs}
              selected={selected}
              runs={job?.nodes ?? {}}
              badNodes={badNodes}
              badEdges={badEdges}
              onSelect={setSelected}
              onChange={mutate}
              onOpenNode={(id) => {
                setSelected([id]);
                setDrawers((prev) => ({ ...prev, inspect: true }));
              }}
            />
          ) : (
            <div className="panel fc-noproject">
              <div className="panel-title">还没有项目</div>
              <div className="muted small">
                上方填标题、选一个生产脚本预设，点「新建项目」——{presets.length ? presets.map((row) => row.title).join(' / ') : '预设载入中'}。
                建好后画布上就是从命题到成片的整条岗位链，可以直接运行。
              </div>
            </div>
          )}

          {drawers.palette ? <aside className="fc-palette fc-drawer is-left">{palettePanel}</aside> : null}
          {drawers.inspect ? <aside className="fc-inspect fc-drawer is-right">{inspectPanel}</aside> : null}
        </div>
      ) : null}

      {view === 'nodes' ? (
        <section className="fc-page fc-page-nodes">
          <div className="panel-title">
            岗位库{' '}
            <span className="count">· {palette ? `${palette.node_count} 个岗位 · ${palette.departments.length} 个部门` : '岗位目录载入中'}</span>
          </div>
          <input className="fc-search" value={palQuery} onChange={(event) => setPalQuery(event.target.value)} placeholder="搜岗位 / 职责 / 标签" />
          <div className="fc-cat">
            {paletteGroups.map((group) => (
              <div className="fc-cat-dept" key={group.dept}>
                <div className="fc-cat-head">
                  <b>{group.label}</b>
                  <span className="muted small">{group.note}</span>
                </div>
                <div className="fc-cat-grid">
                  {group.nodes.map((spec) => (
                    <div className="fc-cat-card" key={spec.type}>
                      <button className="fc-cat-add" onClick={() => addNode(spec)} title={`加到画布：${spec.role}`}>
                        <span className="fc-pal-icon">{spec.icon}</span>
                        <b>{spec.title}</b>
                        <span className="fc-plus"><IconPlus size={12} strokeWidth={2.4} /></span>
                      </button>
                      <div className="fc-cat-role">{spec.role}</div>
                      <div className="fc-cat-ports">
                        <span className="muted small">入</span>
                        {spec.inputs.map((port) => (
                          <span className="fc-chip mono" key={port.name} title={`${port.label} · ${port.type}${port.required ? ' · 必填' : ' · 可空'}`}>
                            {port.name}
                          </span>
                        ))}
                        <span className="muted small">出</span>
                        {spec.outputs.map((port) => (
                          <span className="fc-chip mono ok" key={port.name} title={`${port.label} · ${port.type}`}>
                            {port.name}
                          </span>
                        ))}
                      </div>
                      <div className="row" style={{ gap: 5, flexWrap: 'wrap' }}>
                        {spec.tags.map((tag) => (
                          <span className="fc-chip" key={tag}>
                            {tag}
                          </span>
                        ))}
                        {spec.needs_service ? <span className="fc-chip warn">需服务商</span> : null}
                        {spec.source ? <span className="fc-chip">源头岗位</span> : null}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
            {!paletteGroups.length ? <div className="muted small">没有匹配的岗位。</div> : null}
          </div>
        </section>
      ) : null}

      {view === 'inspect' ? (
        <section className="fc-page fc-page-inspect">
          <div className="fc-runbar">
            <span className="panel-title" style={{ marginBottom: 0 }}>
              检查器 <span className="count">· 已选中 {selected.length} 个岗位</span>
            </span>
            <span className="fc-dock-side">
              {graph.nodes.map((node) => (
                <button
                  key={node.id}
                  className={`fc-chip ${selected.includes(node.id) ? 'ok' : ''}`}
                  title={`${node.id} · ${specs[node.type]?.title ?? node.type}`}
                  onClick={() => setSelected([node.id])}
                >
                  {specs[node.type]?.icon ?? '·'} {titleOf(node.id)}
                </button>
              ))}
              {!graph.nodes.length ? <span className="muted small">画布上还没有岗位：去「岗位库」点一下，或回画布铺一个生产脚本预设。</span> : null}
            </span>
          </div>
          {inspectPanel}
        </section>
      ) : null}

      {view === 'files' ? <section className="fc-page fc-page-files">{filesPanel}</section> : null}
      {view === 'screen' ? <section className="fc-page fc-page-screen">{playerPanel}</section> : null}
      {view === 'events' ? <section className="fc-page fc-page-events">{eventsPanel}</section> : null}
      {view === 'history' ? <section className="fc-page fc-page-history">{historyPanel}</section> : null}
      {view === 'compare' ? <section className="fc-page fc-page-compare">{comparePanel}</section> : null}
      {view === 'stats' ? <section className="fc-page fc-page-stats">{statsPanel}</section> : null}
      {view === 'plan' ? <section className="fc-page fc-page-plan">{planPanel}</section> : null}

      {overrideTarget && overrideNode && overrideSpec ? (
        <RunOverrideDialog
          key={`${overrideTarget}:${overrideNode.type}`}
          node={overrideNode}
          spec={overrideSpec}
          savedValues={mergedValues(overrideSpec, overrideNode.params)}
          currentPatch={runOverrides[overrideTarget]}
          onSave={(patch) => saveOverride(overrideTarget, patch)}
          onClear={() => {
            clearOverride(overrideTarget);
            setOverrideTarget(null);
          }}
          onClose={() => setOverrideTarget(null)}
        />
      ) : null}
    </div>
  );
}
