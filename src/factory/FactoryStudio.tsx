/**
 * 影视制作工厂 —— 画布工作台外壳。
 *
 * 外壳只做四件事：项目与预设的读写、跑图作业轮询（把每个岗位的状态喂回画布）、
 * 底部坞台（事件流 / 交付产物 / 放映室）、体检问题回显。画布交互在 ``Canvas``，
 * 参数与交付物预览在 ``Inspector``，试映在 ``Player``。
 *
 * 状态一律来自后端：岗位没跑过就说没跑过，缓存命中标「复用缓存」，失败原样落在
 * 节点与作业上——界面上不出现「假装完成」。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Canvas } from './Canvas.tsx';
import { Inspector } from './Inspector.tsx';
import { Player, type FactoryTimeline } from './Player.tsx';
import type {
  ArtifactRow,
  EventRow,
  Graph,
  GraphIssue,
  NodeOutputs,
  NodeSpec,
  Palette,
  PresetRow,
  Project,
  ProjectRow,
  RunJob,
  RunNodeResult,
} from './api.ts';
import { factoryApi, mediaUrl } from './api.ts';

const TEXT_EXT = /\.(srt|ass|vtt|edl|json|txt|csv)$/i;
const AUDIO_EXT = /\.(wav|mp3|ogg)$/i;
const IMAGE_EXT = /\.(png|jpe?g|webp)$/i;

/** 稳定空引用：job.result 为 null 时每轮渲染都造新对象会反复触发取数 effect。 */
const NO_RESULTS: Record<string, RunNodeResult> = {};

const EVENT_LABEL: Record<string, string> = {
  'run.start': '开工',
  'run.finish': '收尾',
  'run.wait': '排队',
  'node.start': '上岗',
  'node.finish': '交付',
  'node.cached': '复用缓存',
  'node.error': '报错',
  'node.blocked': '被上游挡住',
  'node.skipped': '静音跳过',
  'node.progress': '进度',
  'node.log': '日志',
};

const JOB_LABEL: Record<string, string> = {
  queued: '排队中',
  running: '跑图中',
  ok: '已完成',
  error: '有岗位失败',
  cancelled: '已取消',
};

/**
 * 工作区分页：画布要占满整屏，其余重 UI（岗位库 / 检查器 / 交付产物 / 放映室 / 事件流）
 * 各自一页，靠顶部导航切换——不再挤在同一屏里把画布压成一小块。
 */
type FactoryView = 'canvas' | 'nodes' | 'inspect' | 'files' | 'screen' | 'events';

const VIEWS: { key: FactoryView; label: string; hint: string }[] = [
  { key: 'canvas', label: '⛭ 画布', hint: '整条岗位链的工作面，占满此页' },
  { key: 'nodes', label: '🧑‍🎤 岗位库', hint: '全部岗位的职责、端口与标签，点一下加到画布' },
  { key: 'inspect', label: '🔍 检查器', hint: '选中岗位的参数、交付物与体检问题' },
  { key: 'files', label: '📦 交付产物', hint: '每个岗位落盘的成片素材，可预览文本' },
  { key: 'screen', label: '🎬 放映室', hint: '按时间轴试映成片并导出' },
  { key: 'events', label: '📡 事件流', hint: '作业进度、岗位日志与历史运行' },
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

/** Kahn 拓扑序（与引擎同一口径）：用来挑「下游最深的那条时间轴」给放映室。 */
function topoRank(graph: Graph): Map<string, number> {
  const out = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  for (const node of graph.nodes) {
    indeg.set(node.id, 0);
    out.set(node.id, []);
  }
  for (const edge of graph.edges) {
    if (!indeg.has(edge.dst) || !out.has(edge.src)) continue;
    indeg.set(edge.dst, (indeg.get(edge.dst) ?? 0) + 1);
    out.get(edge.src)?.push(edge.dst);
  }
  const queue = graph.nodes.filter((node) => (indeg.get(node.id) ?? 0) === 0).map((node) => node.id);
  const rank = new Map<string, number>();
  let step = 0;
  while (queue.length) {
    const id = queue.shift() as string;
    rank.set(id, step);
    step += 1;
    for (const next of out.get(id) ?? []) {
      const left = (indeg.get(next) ?? 1) - 1;
      indeg.set(next, left);
      if (left === 0) queue.push(next);
    }
  }
  return rank;
}

export function FactoryStudio({ onBack, onToast }: { onBack: () => void; onToast: (text: string, error?: boolean) => void }) {
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
  const [artifacts, setArtifacts] = useState<ArtifactRow[]>([]);
  const [groups, setGroups] = useState<Record<string, number>>({});
  const [view, setView] = useState<FactoryView>('canvas');
  const [drawers, setDrawers] = useState({ palette: false, inspect: false });
  const [preview, setPreview] = useState<{ rel: string; text: string } | null>(null);
  const [force, setForce] = useState(false);
  const [busy, setBusy] = useState('');
  const [palQuery, setPalQuery] = useState('');
  const [openDept, setOpenDept] = useState<Record<string, boolean>>({});
  const [confirmDelete, setConfirmDelete] = useState(false);
  const pollFails = useRef(0);

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
      setHistory((await factoryApi.listRuns(id, 8)).runs);
    } catch {
      /* 历史运行是附加信息 */
    }
  }, []);

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
      setSelected([]);
      setJob(null);
      try {
        const res = await factoryApi.getProject(id);
        setProject(res.project);
        setGraph(res.project.graph);
        setTitleDraft(res.project.title);
        setIssues(res.issues);
        setDirty(false);
        void loadArtifacts(id);
        void loadHistory(id);
        const lastJobId = res.project.last_run?.job_id;
        if (lastJobId) {
          const restored = (await factoryApi.job(lastJobId).catch(() => null))?.job ?? null;
          setJob(restored);
        }
      } catch (error) {
        onToast(`项目读取失败：${errorText(error)}`, true);
      }
    },
    [loadArtifacts, loadHistory, onToast],
  );

  /* ------------------------------ 运行轮询 ------------------------------ */

  const running = job?.status === 'queued' || job?.status === 'running';

  useEffect(() => {
    if (!job || !running) return;
    const timer = window.setInterval(async () => {
      try {
        const res = await factoryApi.job(job.id);
        pollFails.current = 0;
        setJob(res.job);
        if (res.job.status !== 'queued' && res.job.status !== 'running') {
          const counts = res.job.counts ?? {};
          const bad = (counts.error ?? 0) + (counts.blocked ?? 0);
          onToast(
            res.job.status === 'cancelled'
              ? '运行已取消（已交付的岗位产物保留）'
              : bad
                ? `跑图结束：${counts.ok ?? 0} 交付 · ${counts.cached ?? 0} 复用 · ${bad} 失败/受阻`
                : `跑图结束：${counts.ok ?? 0} 交付 · ${counts.cached ?? 0} 复用缓存 · ${((res.job.elapsed_ms ?? 0) / 1000).toFixed(1)}s`,
            bad > 0,
          );
          if (projectId) {
            void loadArtifacts(projectId);
            void loadHistory(projectId);
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
  }, [job, running, projectId, loadArtifacts, loadHistory, loadProjects, onToast]);

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

  /** 交出时间轴的岗位，按拓扑序从下游往上游排：最靠后的那条才是成片时间轴。 */
  const timelineCandidates = useMemo(() => {
    const rank = topoRank(graph);
    return Object.keys(nodeResults)
      .filter((nodeId) => Boolean((nodeResults[nodeId].outputs as Record<string, unknown> | undefined)?.timeline))
      .map((nodeId) => ({ id: nodeId, rank: rank.get(nodeId) ?? 0 }))
      .sort((a, b) => b.rank - a.rank);
  }, [nodeResults, graph]);

  /* 作业接口只回交付物摘要（全链路四十多个岗位整包太沉），放映室与检查面板要的原值
     在项目缓存里，按岗位单独取。候选集合只随运行结果变，拖动节点不该重新拉取，
     所以 effect 依赖签名、真数据从 ref 里读。 */
  const candidatesRef = useRef(timelineCandidates);
  candidatesRef.current = timelineCandidates;
  const timelineKey = `${projectId ?? ''}|${timelineCandidates.map((item) => item.id).join(',')}`;
  const runToken = `${job?.id ?? ''}:${job?.status ?? ''}`;
  const [timeline, setTimeline] = useState<{ id: string; data: FactoryTimeline } | null>(null);
  const [outputs, setOutputs] = useState<{ node_id: string; value: NodeOutputs | null } | null>(null);

  useEffect(() => {
    if (!projectId) {
      setTimeline(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      for (const candidate of candidatesRef.current) {
        const res = await factoryApi.nodeOutputs(projectId, candidate.id).catch(() => null);
        const value = res?.outputs?.timeline as FactoryTimeline | undefined;
        if (!value || !Array.isArray(value.shots) || !value.shots.length) continue;
        if (!cancelled) setTimeline({ id: candidate.id, data: value });
        return;
      }
      if (!cancelled) setTimeline(null);
    })();
    return () => {
      cancelled = true;
    };
  }, [projectId, timelineKey]);

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

  const startRun = async (payload: { targets?: string[]; node_id?: string }) => {
    if (!projectId) return;
    if (dirty) {
      onToast('画布有未保存改动：先保存再跑（跑的是后端已存的工作流）', true);
      return;
    }
    setBusy('run');
    try {
      const res = await factoryApi.run(projectId, { ...payload, force });
      pollFails.current = 0;
      setJob(res.job);
      onToast(res.job.targets.length ? `运行已受理：目标 ${res.job.targets.join('、')}` : '运行已受理：全图按拓扑序推进');
    } catch (error) {
      onToast(`运行失败：${errorText(error)}`, true);
    } finally {
      setBusy('');
    }
  };

  const cancelRun = async () => {
    if (!job) return;
    try {
      setJob((await factoryApi.cancelJob(job.id)).job);
      onToast('已标记取消：正在跑的岗位会自然收尾');
    } catch (error) {
      onToast(`取消失败：${errorText(error)}`, true);
    }
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
              {openDept[group.dept] ? '▾' : '▸'} {group.label}
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
                    <span className="fc-plus">＋</span>
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
        onRunNode={(id) => void startRun({ node_id: id })}
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
          {history.map((row) => (
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
              {row.status} · {row.counts.ok ?? 0}/{(row.counts.ok ?? 0) + (row.counts.error ?? 0) + (row.counts.cached ?? 0)} · {row.updated_at}
            </button>
          ))}
          {!history.length ? <span className="muted small">还没有运行记录。</span> : null}
        </span>
      </div>
      <div className="fc-events">
        {events.length ? (
          events.map((row, index) => {
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
          <div className="muted small">
            还没有运行：工具栏「运行全图」按拓扑序推进每个岗位，或选中某个岗位「运行选中」只跑它的上游闭包。
          </div>
        )}
      </div>
    </>
  );

  const filesPanel = (
    <>
      <div className="fc-file-groups">
        {Object.entries(groups).map(([group, count]) => (
          <span className="fc-chip" key={group}>
            {group} {count}
          </span>
        ))}
        {!artifacts.length ? <span className="muted small">项目还没有产物。</span> : null}
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
      <Player timeline={timeline?.data ?? null} onToast={onToast} />
    </>
  );

  return (
    <div className="fc-studio">
      <div className="motion-head">
        <button className="btn btn-sm btn-ghost" onClick={onBack}>
          ← 返回任务台
        </button>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>影视制作工厂</div>
          <div className="muted small">
            每个岗位一个节点：{palette ? `${palette.node_count} 个岗位 · ${palette.departments.length} 个部门` : '岗位目录载入中'}，
            从命题到成片走同一张画布
          </div>
        </div>
        <nav className="fc-views">
          {VIEWS.map((item) => (
            <button
              key={item.key}
              className={`fc-view${view === item.key ? ' active' : ''}`}
              title={item.hint}
              onClick={() => setView(item.key)}
            >
              {item.label}
              {item.key === 'screen' && timeline ? <i>{timeline.data.shots?.length ?? 0} 镜</i> : null}
              {item.key === 'files' && artifacts.length ? <i>{artifacts.length}</i> : null}
              {item.key === 'events' && events.length ? <i>{events.length}</i> : null}
              {item.key === 'inspect' && selected.length ? <i>{selected.length}</i> : null}
            </button>
          ))}
        </nav>
      </div>

      {/* ---------------- 项目条 ---------------- */}
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
              {row.title}（{row.node_count} 岗位 · {row.edge_count} 连线）
            </option>
          ))}
        </select>
        <input className="fc-title" value={titleDraft} onChange={(event) => setTitleDraft(event.target.value)} placeholder="项目标题" />

        <div className="fc-bar-actions">
          <input className="fc-title fc-new" value={newTitle} onChange={(event) => setNewTitle(event.target.value)} placeholder="新项目标题" />
          <select className="fc-pick" value={newPreset} onChange={(event) => setNewPreset(event.target.value)}>
            {presets.map((row) => (
              <option key={row.key} value={row.key}>
                {row.title} · {row.node_count} 岗位
              </option>
            ))}
          </select>
          <button className="btn btn-sm btn-primary" onClick={doCreate} disabled={busy === 'create'}>
            新建项目
          </button>
        </div>

        <div className="fc-bar-actions">
          <select className="fc-pick" value={presetPick} onChange={(event) => setPresetPick(event.target.value)}>
            {presets.map((row) => (
              <option key={row.key} value={row.key}>
                {row.title}
              </option>
            ))}
          </select>
          <button className="btn btn-sm btn-ghost" onClick={() => void applyPreset(presetPick)} disabled={!projectId || busy === 'preset'} title="用预设覆盖当前画布（保存后才落盘）">
            铺到画布
          </button>
          <button className="btn btn-sm btn-ghost" onClick={doSave} disabled={!projectId || busy === 'save'} title="保存工作流与标题">
            {dirty ? '保存 ●' : '保存'}
          </button>
          <button className="btn btn-sm btn-primary" onClick={() => void startRun({})} disabled={!projectId || running || busy === 'run'}>
            运行全图
          </button>
          <button
            className="btn btn-sm btn-ghost"
            onClick={() => void startRun({ targets: selected })}
            disabled={!projectId || !selected.length || running || busy === 'run'}
            title="只跑选中岗位及其上游闭包"
          >
            运行选中{selected.length ? ` (${selected.length})` : ''}
          </button>
          <label className="fc-force" title="忽略签名缓存，全部重跑">
            <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
            忽略缓存
          </label>
          {running ? (
            <button className="btn btn-sm btn-ghost" onClick={cancelRun}>
              取消
            </button>
          ) : null}
          <button
            className={`btn btn-sm ${confirmDelete ? 'btn-primary' : 'btn-ghost'}`}
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
            {confirmDelete ? '确认删除?' : '删除项目'}
          </button>
        </div>
      </div>

      <div className="fc-meta">
        <span className="fc-chip">{graph.nodes.length} 岗位</span>
        <span className="fc-chip">{graph.edges.length} 连线</span>
        {project?.last_run ? (
          <span className="muted small mono" title={`作业 ${project.last_run.job_id}${project.last_run.targets.length ? ` · 目标 ${project.last_run.targets.join('、')}` : ''}`}>
            上次运行 {project.last_run.status} @ {project.last_run.finished_at}
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
        {job ? <span className={`fc-chip ${job.status === 'ok' ? 'ok' : job.status === 'error' ? 'bad' : 'warn'}`}>{JOB_LABEL[job.status] ?? job.status}</span> : null}
        {running ? <span className="muted small mono">{Math.round((job?.progress ?? 0) * 100)}%</span> : null}
        {job && !running ? (
          <span className="muted small mono">
            {`ok ${counts.ok ?? 0} · cached ${counts.cached ?? 0} · error ${counts.error ?? 0} · blocked ${counts.blocked ?? 0} · skipped ${counts.skipped ?? 0}`} ·{' '}
            {((job.elapsed_ms ?? 0) / 1000).toFixed(1)}s
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
                        <span className="fc-plus">＋</span>
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
    </div>
  );
}
