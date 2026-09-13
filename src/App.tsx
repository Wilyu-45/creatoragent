import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { AgentEvent, Brief } from './lib/types.ts';
import { ApprovalPanel } from './components/ApprovalPanel.tsx';
import { ArtifactViewer } from './components/ArtifactViewer.tsx';
import { BriefForm } from './components/BriefForm.tsx';
import { MetricsPanel } from './components/MetricsPanel.tsx';
import { Pipeline } from './components/Pipeline.tsx';
import { Scorecard } from './components/Scorecard.tsx';
import { SettingsDrawer } from './components/SettingsDrawer.tsx';
import { TaskList } from './components/TaskList.tsx';
import { Timeline } from './components/Timeline.tsx';
import { Topbar } from './components/Topbar.tsx';
import { Chip, Empty, Spinner } from './components/ui.tsx';
import type {
  AgentsResponse,
  HealthView,
  KnowledgeView,
  MetricsView,
  TaskDetail,
  TaskSummary,
} from './lib/api.ts';
import { api, subscribeTask } from './lib/api.ts';
import { formatCost, STATUS_LABEL, statusTone } from './lib/format.ts';

type Tab = 'pipeline' | 'artifacts' | 'timeline' | 'metrics';

const TABS: { key: Tab; label: string }[] = [
  { key: 'pipeline', label: '流水线看板' },
  { key: 'artifacts', label: '共享黑板产物' },
  { key: 'timeline', label: '事件时间线' },
  { key: 'metrics', label: '运行指标' },
];

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function App() {
  const [tasks, setTasks] = useState<TaskSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [agents, setAgents] = useState<AgentsResponse | null>(null);
  const [metrics, setMetrics] = useState<MetricsView | null>(null);
  const [knowledge, setKnowledge] = useState<KnowledgeView | null>(null);
  const [health, setHealth] = useState<HealthView | null>(null);

  const [tab, setTab] = useState<Tab>('pipeline');
  const [showBrief, setShowBrief] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deciding, setDeciding] = useState(false);
  const [saving, setSaving] = useState(false);
  const [toast, setToast] = useState<{ text: string; error?: boolean } | null>(null);

  const toastTimer = useRef<number | null>(null);

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

  // 任务列表 + 指标轮询
  useEffect(() => {
    const timer = window.setInterval(() => {
      void loadTasks();
      void loadMetrics();
    }, 3000);
    return () => window.clearInterval(timer);
  }, [loadTasks, loadMetrics]);

  // 当前任务详情轮询 + SSE 实时事件
  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      setEvents([]);
      return;
    }

    let cancelled = false;

    const loadDetail = async () => {
      try {
        const data = await api.getTask(activeId);
        if (cancelled) return;
        setDetail(data);
        setEvents(data.events);
      } catch {
        /* 轮询失败时保留上一次快照 */
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
  }, [activeId]);

  /* ------------------------------ 交互动作 ------------------------------ */

  const handleCreate = async (brief: Partial<Brief>, autoApprove: boolean): Promise<void> => {
    setCreating(true);
    try {
      const response = await api.createTask(brief, autoApprove);
      setShowBrief(false);
      showToast('任务已创建，流水线启动');
      await loadTasks();
      setActiveId(response.task.id);
      setTab('pipeline');
    } catch (error) {
      showToast(`创建失败：${errorText(error)}`, true);
    } finally {
      setCreating(false);
    }
  };

  const handleDecide = async (
    decision: 'approve' | 'revise' | 'reject',
    comment: string,
  ): Promise<void> => {
    if (!activeId) return;
    setDeciding(true);
    try {
      await api.decide(activeId, decision, comment);
      showToast('人工裁决已提交');
      const data = await api.getTask(activeId);
      setDetail(data);
      setEvents(data.events);
      await loadTasks();
      void loadMetrics();
    } catch (error) {
      showToast(`裁决失败：${errorText(error)}`, true);
    } finally {
      setDeciding(false);
    }
  };

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

  const agentNames = useMemo(() => {
    const map: Record<string, { name: string; role: string }> = {};
    for (const agent of agents?.implemented ?? []) {
      map[agent.id] = { name: agent.name, role: agent.role };
    }
    return map;
  }, [agents]);

  const runningCount = useMemo(
    () => tasks.filter((task) => task.status === 'running' || task.status === 'awaiting_approval').length,
    [tasks],
  );

  const industries = knowledge?.industries ?? [];
  const config = health?.config ?? null;

  return (
    <div className="app">
      <Topbar
        health={health}
        totalCount={tasks.length}
        runningCount={runningCount}
        onNewTask={() => setShowBrief(true)}
        onOpenSettings={() => setShowSettings(true)}
      />

      <div className="layout">
        <aside className="sidebar">
          <div className="sidebar-head">
            <button className="btn btn-sm btn-primary" style={{ flex: 1 }} onClick={() => setShowBrief(true)}>
              ＋ 新建任务
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
          <div className="task-list">
            <TaskList tasks={tasks} activeId={activeId} onSelect={setActiveId} />
          </div>
        </aside>

        <main className="main">
          {!detail ? (
            <Empty>
              <div style={{ fontSize: 15, marginBottom: 6 }}>选择左侧任务查看流水线，或新建一个创作任务</div>
              <div className="muted small">
                系统会编排 A1 策略 → A2 创意 → A3 策划 → A4 文案，并由 A5 / A6 / A7 门禁审核，最后交由人工审批。
              </div>
            </Empty>
          ) : (
            <>
              <div className="panel">
                <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start' }}>
                  <div>
                    <div style={{ fontSize: 17, fontWeight: 700 }}>
                      {detail.task.brief.brand}
                      <span className="muted"> · {detail.task.brief.product}</span>
                    </div>
                    <div className="row small muted" style={{ marginTop: 6 }}>
                      <span>渠道 {detail.task.brief.channel}</span>
                      <span>目标 {detail.task.brief.objective}</span>
                      <span>行业 {detail.task.brief.industry}</span>
                      <span>受众 {detail.task.brief.audience}</span>
                    </div>
                    <div className="row" style={{ marginTop: 8 }}>
                      <Chip tone={statusTone(detail.task.status)}>{STATUS_LABEL[detail.task.status]}</Chip>
                      <Chip tone="tone-idle">{detail.task.phase}</Chip>
                      <Chip tone="tone-idle">返工 {detail.task.revision_round} 轮</Chip>
                      <Chip tone="tone-idle">Turn {detail.task.turn_used}</Chip>
                      <Chip tone="tone-idle">{detail.task.artifacts.length} 产物</Chip>
                      <Chip tone="tone-idle" mono>
                        {formatCost(detail.task.tokens.cost_usd)}
                      </Chip>
                    </div>
                  </div>
                </div>

                <div style={{ marginTop: 14 }}>
                  <Scorecard scorecard={detail.task.scorecard} />
                </div>
              </div>

              <ApprovalPanel task={detail.task} busy={deciding} onDecide={handleDecide} />

              <div className="tabs" style={{ marginTop: 16 }}>
                {TABS.map((item) => (
                  <button
                    key={item.key}
                    className={`tab${tab === item.key ? ' active' : ''}`}
                    onClick={() => setTab(item.key)}
                  >
                    {item.label}
                    {item.key === 'timeline' ? ` (${events.length})` : ''}
                    {item.key === 'artifacts' ? ` (${detail.task.artifacts.length})` : ''}
                  </button>
                ))}
              </div>

              {tab === 'pipeline' ? (
                <div className="panel">
                  <div className="panel-title">
                    流水线看板 <span className="count">· {detail.task.packets.length} 张任务卡</span>
                  </div>
                  <Pipeline task={detail.task} agentNames={agentNames} />
                </div>
              ) : null}

              {tab === 'artifacts' ? (
                <div className="panel">
                  <div className="panel-title">共享黑板产物</div>
                  <ArtifactViewer task={detail.task} />
                </div>
              ) : null}

              {tab === 'timeline' ? (
                <div className="panel">
                  <div className="panel-title">
                    事件时间线
                    <span className="count">
                      · 事实 {detail.blackboard.stats.fact_count}（已验证 {detail.blackboard.stats.verified_fact_count}）
                      · 评审 {detail.blackboard.stats.review_count}
                    </span>
                  </div>
                  <div className="timeline">
                    <Timeline events={events} />
                  </div>
                </div>
              ) : null}

              {tab === 'metrics' ? <MetricsPanel metrics={metrics} /> : null}
            </>
          )}
        </main>
      </div>

      {showBrief ? (
        <BriefForm
          industries={industries}
          submitting={creating}
          onClose={() => setShowBrief(false)}
          onSubmit={handleCreate}
        />
      ) : null}

      {showSettings && config ? (
        <SettingsDrawer
          config={config}
          knowledge={knowledge}
          saving={saving}
          onClose={() => setShowSettings(false)}
          onSave={handleSaveSettings}
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
