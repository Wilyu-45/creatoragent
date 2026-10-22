import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentEvent, Brief } from './lib/types.ts';
import type { AppView, TaskSectionKey } from './lib/workbenches.ts';
import type { VideoDraft } from './lib/handoff.ts';
import { BriefForm } from './components/BriefForm.tsx';
import { HomePage } from './components/HomePage.tsx';
import { MotionStudio } from './components/MotionStudio.tsx';
import { SettingsDrawer } from './components/SettingsDrawer.tsx';
import { TaskList } from './components/TaskList.tsx';
import { TaskWorkspace } from './components/TaskWorkspace.tsx';
import { Topbar } from './components/Topbar.tsx';
import { VideoStudio } from './components/VideoStudio.tsx';
import { FactoryStudio } from './factory/FactoryStudio.tsx';
import { Empty, Spinner } from './components/ui.tsx';
import { IconArrowRight, IconPlus } from './components/icons.tsx';
import type {
  AgentsResponse,
  HealthView,
  KnowledgeView,
  MetricsView,
  TaskDetail,
  TaskSummary,
} from './lib/api.ts';
import { api, subscribeTask } from './lib/api.ts';

/** 工场来源上下文：只带任务身份，不自动搬运素材。 */
interface TaskContext {
  id: string;
  label: string;
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<AgentsResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [knowledge, setKnowledge] = useState<KnowledgeView | null>(null);
  const [health, setHealth] = useState<HealthView | null>(null);

  const [view, setView] = useState<AppView>('home');
  const [taskSection, setTaskSection] = useState<TaskSectionKey>('overview');
  const [taskContext, setTaskContext] = useState<TaskContext | null>(null);
  const [videoDraft, setVideoDraft] = useState<VideoDraft | null>(null);
  const [showBrief, setShowBrief] = useState(false);
  const [briefSeed, setBriefSeed] = useState<Brief | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [creating, setCreating] = useState(false);
  const [taskListReset, setTaskListReset] = useState(0);
  const [deciding, setDeciding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);

  const toastTimer = useRef<number | null>(null);
  const activeIdRef = useRef<string | null>(null);

  const showToast = useCallback((text: string, error = false) => {
    setToast({ text, error });
    if (toastTimer.current) window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), 3800);
  }, []);

  /* ------------------------------ 数据装载 ------------------------------ */

  const loadTasks = useCallback(async (): Promise<TaskSummary[]> => {
    const response = await api.listTasks();
    setTasks(response.tasks);
    setActiveId((prev) => prev ?? response.tasks[0]?.id ?? null);
    return response.tasks;
  }, []);

  const loadMetrics = useCallback(async () => {
    try {
      setMetrics(await api.metrics());
    } catch {
      /* 指标失败不阻塞主流程 */
    }
  }, []);

  useEffect(() => {
    void (async () => {
      try {
        const [healthRes, agentsRes, knowledgeRes] = await Promise.all([
          api.health(),
          api.agents(),
          api.knowledge(),
        ]);
        setHealth(healthRes);
        setAgents(agentsRes);
        setKnowledge(knowledgeRes);
      } catch (error) {
        showToast(`初始化失败：${errorText(error)}`, true);
      }
      await Promise.all([loadTasks(), loadMetrics()]);
    })();
  }, [loadTasks, loadMetrics, showToast]);

  // 任务列表 + 指标轮询（顶栏与首页都要用到计数）
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadTasks();
      void loadMetrics();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadTasks, loadMetrics]);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  // 只有进入任务工作台才拉详情与 SSE；在工场/首页时不为后台任务空转
  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      setDetailError(null);
      setEvents([]);
      return;
    }
    if (view !== 'tasks') return;

    // 切换任务先清空旧快照：不能让上一个任务的详情/事件在下一次拉取前被继续操作
    setDetail(null);
    setDetailError(null);
    setEvents([]);

    let cancelled = false;

    const loadDetail = async () => {
      try {
        const data = await api.getTask(activeId);
        if (cancelled) return;
        setDetail(data);
        setDetailError(null);
        setEvents((prev) => {
          const seqs = new Set(data.events.map((event) => event.seq));
          return [...data.events, ...prev.filter((event) => !seqs.has(event.seq))].sort((a, b) => a.seq - b.seq);
        });
      } catch (error) {
        if (!cancelled) setDetailError(errorText(error));
      }
    };

    void loadDetail();
    const timer = window.setInterval(() => void loadDetail(), 2500);
    const unsubscribe = subscribeTask(activeId, 0, (event) => {
      setEvents((prev) => (prev.some((item) => item.seq === event.seq) ? prev : [...prev, event]));
    });

    return () => {
      cancelled = true;
      window.clearInterval(timer);
      unsubscribe();
    };
  }, [activeId, view]);

  /* ------------------------------ 导航 ------------------------------ */

  const navigate = useCallback((next: AppView) => {
    setView(next);
    setTaskContext(null);
    if (next !== 'video') setVideoDraft(null);
  }, []);

  const goHome = useCallback(() => {
    setView('home');
    setTaskContext(null);
    setVideoDraft(null);
  }, []);

  const openTask = useCallback((id: string, section: TaskSectionKey = 'overview') => {
    setActiveId(id);
    setTaskSection(section);
    setTaskContext(null);
    setView('tasks');
  }, []);

  const selectTask = useCallback(
    (id: string) => {
      if (id !== activeId) setTaskSection('overview');
      setActiveId(id);
    },
    [activeId],
  );

  const activeLabel = useMemo(() => {
    if (detail?.task.id === activeId) return `${detail.task.brief.brand} · ${detail.task.brief.product}`;
    const row = tasks.find((task) => task.id === activeId);
    return row ? `${row.brief.brand} · ${row.brief.product}` : null;
  }, [activeId, detail, tasks]);

  const openStudioFromTask = useCallback(
    (next: 'motion' | 'factory') => {
      if (activeId && activeLabel) setTaskContext({ id: activeId, label: activeLabel });
      setVideoDraft(null);
      setView(next);
    },
    [activeId, activeLabel],
  );

  const openVideoFromTask = useCallback(
    (draft: VideoDraft) => {
      setTaskContext({ id: draft.taskId, label: draft.source.split('｜')[0] || activeLabel || '当前任务' });
      setVideoDraft(draft);
      setView('video');
    },
    [activeLabel],
  );

  const openContextTask = useCallback(() => {
    if (!taskContext) return;
    setActiveId(taskContext.id);
    setTaskSection('overview');
    setTaskContext(null);
    setView('tasks');
  }, [taskContext]);

  /* ------------------------------ 交互动作 ------------------------------ */

  const openNewTask = useCallback(() => {
    setBriefSeed(null);
    setShowBrief(true);
  }, []);

  const openCloneTask = useCallback((brief: Brief) => {
    setBriefSeed(brief);
    setShowBrief(true);
  }, []);

  const handleCreate = async (brief: Partial<Brief>, autoApprove: boolean): Promise<void> => {
    setCreating(true);
    try {
      const response = await api.createTask(brief, autoApprove);
      setShowBrief(false);
      setBriefSeed(null);
      showToast('任务已创建，流水线启动');
      await loadTasks();
      setActiveId(response.task.id);
      setTaskListReset((prev) => prev + 1);
      setTaskSection('overview');
      setTaskContext(null);
      setView('tasks');
    } catch (error) {
      showToast(`创建失败：${errorText(error)}`, true);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteTask = useCallback(
    async (id: string): Promise<void> => {
      try {
        await api.deleteTask(id);
        const response = await api.listTasks();
        setTasks(response.tasks);
        if (id === activeId) {
          setActiveId(response.tasks[0]?.id ?? null);
          setDetail(null);
          setEvents([]);
          setTaskSection('overview');
        }
        setTaskListReset((prev) => prev + 1);
        showToast('任务已删除');
        void loadMetrics();
      } catch (error) {
        showToast(`删除失败：${errorText(error)}`, true);
        throw error;
      }
    },
    [activeId, loadMetrics, showToast],
  );

  const handleDecide = async (
    decision: 'approve' | 'revise' | 'reject',
    comment: string,
  ): Promise<void> => {
    if (!activeId) return;
    const taskId = activeId;
    setDeciding(true);
    try {
      await api.decide(taskId, decision, comment);
      showToast('人工裁决已提交');
      // 裁决期间切走任务时，不把旧任务的新快照写进当前工作面
      if (activeIdRef.current === taskId) {
        const data = await api.getTask(taskId);
        if (activeIdRef.current === taskId) {
          setDetail(data);
          setEvents(data.events);
        }
      }
      await loadTasks();
      void loadMetrics();
    } catch (error) {
      showToast(`裁决失败：${errorText(error)}`, true);
    } finally {
      setDeciding(false);
    }
  };

  /** 发布登记 / 效果回填后刷新当前任务与列表。 */
  const handlePublishChanged = useCallback(async (): Promise<void> => {
    if (!activeId) return;
    const taskId = activeId;
    try {
      const data = await api.getTask(taskId);
      if (activeIdRef.current === taskId) {
        setDetail(data);
        setEvents(data.events);
      }
      await loadTasks();
      void loadMetrics();
    } catch {
      /* 刷新失败时保留上一次快照 */
    }
  }, [activeId, loadTasks, loadMetrics]);

  const handleSaveSettings = async (patch: Record<string, unknown>): Promise<void> => {
    setSaving(true);
    try {
      const config = await api.updateSettings(patch);
      setHealth((prev) => (prev ? { ...prev, config } : prev));
      showToast('配置已更新');
    } catch (error) {
      showToast(`保存失败：${errorText(error)}`, true);
    } finally {
      setSaving(false);
    }
  };

  /* ------------------------------ 派生数据 ------------------------------ */

  const runningCount = useMemo(
    () => tasks.filter((task) => task.status === 'running').length,
    [tasks],
  );
  const awaitingCount = useMemo(
    () => tasks.filter((task) => task.status === 'awaiting_approval').length,
    [tasks],
  );

  const industries = knowledge?.industries ?? [];
  const config = health?.config ?? null;
  // 详情必须与当前选中任务同 id 才可渲染，避免切换瞬间把上一个任务继续摆在主工作面
  const currentDetail = detail && detail.task.id === activeId ? detail : null;

  return (
    <div className="app">
      <Topbar
        view={view}
        health={health}
        totalCount={tasks.length}
        runningCount={runningCount}
        awaitingCount={awaitingCount}
        onNavigate={navigate}
        onNewTask={openNewTask}
        onOpenSettings={() => setShowSettings(true)}
      />

      {view === 'home' ? (
        <HomePage
          tasks={tasks}
          health={health}
          onOpenView={navigate}
          onOpenTask={openTask}
          onNewTask={openNewTask}
        />
      ) : view === 'motion' ? (
        <MotionStudio
          onBack={goHome}
          sourceTask={taskContext}
          onOpenTask={taskContext ? openContextTask : undefined}
        />
      ) : view === 'video' ? (
        <VideoStudio
          onBack={goHome}
          initialDraft={videoDraft}
          sourceTask={taskContext}
          onOpenTask={taskContext ? openContextTask : undefined}
        />
      ) : view === 'factory' ? (
        <FactoryStudio
          onBack={goHome}
          onToast={showToast}
          sourceTask={taskContext}
          onOpenTask={taskContext ? openContextTask : undefined}
        />
      ) : (
        <div className="layout">
          <aside className="sidebar">
            <div className="sidebar-head">
              <button className="btn btn-sm btn-primary" style={{ flex: 1 }} onClick={openNewTask}>
                <IconPlus size={14} strokeWidth={2.2} /> 新建任务
              </button>
              <button
                className="btn btn-sm btn-ghost"
                onClick={() => {
                  void loadTasks();
                  void loadMetrics();
                }}
              >
                刷新
              </button>
            </div>
            <div className="sidebar-caption">
              <span>
                创作任务 · {tasks.length} 个任务
                {awaitingCount ? ` · ${awaitingCount} 待裁决` : ''}
              </span>
              <button type="button" onClick={goHome}>
                首页总图 <IconArrowRight size={12} />
              </button>
            </div>
            <TaskList tasks={tasks} activeId={activeId} resetKey={taskListReset} onSelect={selectTask} />
          </aside>

          <main className="main">
            {!currentDetail ? (
              activeId ? (
                <Empty>
                  {detailError ? (
                    <>
                      <div style={{ fontSize: 15, marginBottom: 6 }}>任务详情加载失败</div>
                      <div className="muted small" style={{ maxWidth: 520 }}>
                        {detailError}
                      </div>
                      <div className="muted small" style={{ marginTop: 6 }}>
                        正在每 2.5 秒自动重试；也可以切换任务后再回来。
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="row" style={{ justifyContent: 'center', gap: 8, fontSize: 15 }}>
                        <Spinner /> 正在载入任务…
                      </div>
                      <div className="muted small" style={{ marginTop: 6 }}>
                        同步任务详情、黑板与事件流
                      </div>
                    </>
                  )}
                </Empty>
              ) : (
                <Empty>
                  <div style={{ fontSize: 15, marginBottom: 6 }}>
                    {tasks.length ? '选择左侧任务查看工作面，或新建一个创作任务' : '还没有创作任务'}
                  </div>
                  <div className="muted small">
                    创作任务负责策划、审核、发布与复盘；成片生产可交给影视制作工厂或视频工场。
                  </div>
                  <button className="btn btn-sm btn-ghost" style={{ marginTop: 14 }} onClick={goHome}>
                    回到首页总图
                  </button>
                </Empty>
              )
            ) : (
              <TaskWorkspace
                detail={currentDetail}
                events={events}
                agents={agents}
                metrics={metrics}
                section={taskSection}
                onSectionChange={setTaskSection}
                deciding={deciding}
                onDecide={handleDecide}
                onPublishChanged={() => {
                  void handlePublishChanged();
                }}
                onToast={showToast}
                onOpenVideo={openVideoFromTask}
                onOpenView={openStudioFromTask}
                onCloneBrief={openCloneTask}
                onDeleteTask={handleDeleteTask}
              />
            )}
          </main>
        </div>
      )}

      {showBrief ? (
        <BriefForm
          industries={industries}
          submitting={creating}
          initial={briefSeed ?? undefined}
          onClose={() => setShowBrief(false)}
          onSubmit={handleCreate}
        />
      ) : null}

      {showSettings && config ? (
        <SettingsDrawer
          config={config}
          knowledge={knowledge}
          agents={agents}
          saving={saving}
          onClose={() => setShowSettings(false)}
          onSave={handleSaveSettings}
          onToast={showToast}
        />
      ) : null}

      {toast ? (
        <div className={`toast${toast.error ? ' error' : ''}`}>
          {toast.error ? null : <Spinner />} {toast.text}
        </div>
      ) : null}
    </div>
  );
}
