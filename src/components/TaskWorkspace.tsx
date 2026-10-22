/** 任务工作台：一个任务的分区工作面，替代原先「所有面板堆在一页」。 */
import type { ComponentType } from 'react';
import { useMemo, useState } from 'react';
import type { AgentEvent, Brief } from '../lib/types.ts';
import type { AgentsResponse, MetricsView, TaskDetail } from '../lib/api.ts';
import { downloadExport } from '../lib/api.ts';
import { buildVideoDraft, type VideoDraft } from '../lib/handoff.ts';
import type { TaskSectionKey } from '../lib/workbenches.ts';
import {
  formatCost,
  formatDateTime,
  formatDeadline,
  parseDeadline,
  PHASE_LABEL,
  PRIORITY_LABEL,
  STATUS_LABEL,
  statusTone,
} from '../lib/format.ts';
import { artifactLabel } from '../lib/artifactLabels.ts';
import { ArtifactViewer } from './ArtifactViewer.tsx';
import type { ArtifactFocus } from './ArtifactViewer.tsx';
import { ApprovalPanel } from './ApprovalPanel.tsx';
import { DesktopPetPanel } from './DesktopPetPanel.tsx';
import { DigitalHumanPanel } from './DigitalHumanPanel.tsx';
import { DubbingPanel } from './DubbingPanel.tsx';
import { ImageGenPanel } from './ImageGenPanel.tsx';
import { JudgePanel } from './JudgePanel.tsx';
import { MetricsPanel } from './MetricsPanel.tsx';
import { Pipeline } from './Pipeline.tsx';
import { PublishPanel } from './PublishPanel.tsx';
import { Scorecard } from './Scorecard.tsx';
import { SkillStudioPanel } from './SkillStudioPanel.tsx';
import { SurfPanel } from './SurfPanel.tsx';
import { Timeline } from './Timeline.tsx';
import { TracePanel } from './TracePanel.tsx';
import { VideoGenPanel } from './VideoGenPanel.tsx';
import { VideoUnderstandPanel } from './VideoUnderstandPanel.tsx';
import { Chip, Empty, Spinner } from './ui.tsx';
import { TaskBrief } from './TaskBrief.tsx';
import {
  IconActivity,
  IconArrowRight,
  IconCopy,
  IconDownload,
  IconEye,
  IconGauge,
  IconGraduation,
  IconImage,
  IconMic,
  IconPackage,
  IconPaw,
  IconSend,
  IconShieldCheck,
  IconTrash,
  IconUser,
  IconVideo,
  IconWand,
  IconWaves,
  IconWorkflow,
  WorkbenchIcon,
} from './icons.tsx';

type GlyphComponent = ComponentType<{ size?: number; className?: string }>;

const SECTIONS: { key: TaskSectionKey; label: string; hint: string; icon: GlyphComponent }[] = [
  { key: 'overview', label: '总览', hint: '状态、质量分与下一步动作', icon: IconGauge },
  { key: 'pipeline', label: '流水线', hint: 'A1–A11 任务卡与门禁', icon: IconWorkflow },
  { key: 'artifacts', label: '产物', hint: '共享黑板上的全部交付物', icon: IconPackage },
  { key: 'tools', label: '创作工具', hint: '图像 / 视频 / 配音 / 扩展能力', icon: IconWand },
  { key: 'deliver', label: '发布复盘', hint: '排期、投递与效果回填', icon: IconSend },
  { key: 'quality', label: '质量评估', hint: 'Judge 报告与运行指标', icon: IconShieldCheck },
  { key: 'observe', label: '运行观测', hint: '调用轨迹与事件时间线', icon: IconActivity },
];

type ToolKey = 'image' | 'video' | 'human' | 'dub' | 'understand' | 'surf' | 'pet' | 'skill';

const TOOLS: { key: ToolKey; icon: GlyphComponent; label: string; hint: string }[] = [
  { key: 'image', icon: IconImage, label: '图像生成', hint: '按视觉方向为分镜与封面出图' },
  { key: 'video', icon: IconVideo, label: '视频生成', hint: '基于脚本与画面生成镜头片段' },
  { key: 'human', icon: IconUser, label: '数字人', hint: '把口播脚本变成数字人视频' },
  { key: 'dub', icon: IconMic, label: '配音', hint: '台词转语音，支持多角色与语种' },
  { key: 'understand', icon: IconEye, label: '视频理解', hint: '读取参考视频的画面与结构' },
  { key: 'surf', icon: IconWaves, label: '灵感冲浪', hint: '从外部话题里收集创作灵感' },
  { key: 'pet', icon: IconPaw, label: '桌面宠物', hint: '生成任务陪伴与提醒角色' },
  { key: 'skill', icon: IconGraduation, label: '技能工坊', hint: '把本轮经验沉淀为可复用技能' },
];

const ARTIFACT_RECENT = 5;

function nextStep(task: TaskDetail['task']): { label: string; hint: string; section: TaskSectionKey } {
  switch (task.status) {
    case 'awaiting_approval':
      return { label: '先完成人工裁决', hint: '自动门禁已交人，通过后才能进入发布', section: 'overview' };
    case 'running':
      return { label: '查看实时流水线', hint: '任务执行中，可观察岗位卡与事件', section: 'pipeline' };
    case 'completed': {
      const feedback = task.feedback_actuals;
      if (feedback) {
        const channel = typeof feedback.channel === 'string' ? ` ${feedback.channel}` : '';
        return {
          label: '查看复盘结论',
          hint: `已回填${channel}效果，A10 已产出复盘报告`,
          section: 'deliver',
        };
      }
      return { label: '进入发布复盘', hint: '审批已通过，登记投递并回填效果', section: 'deliver' };
    }
    case 'failed':
      return { label: '定位失败原因', hint: '从运行观测与调用轨迹查最后现场', section: 'observe' };
    case 'rejected':
      return { label: '查看评估意见', hint: '确认驳回原因后再决定是否重开', section: 'quality' };
    default:
      return { label: '查看已有产物', hint: '任务已停止，产物仍可读取', section: 'artifacts' };
  }
}

function deliverEmptyState(task: TaskDetail['task']): {
  title: string;
  hint: string;
  action: string;
  section: TaskSectionKey;
} {
  switch (task.status) {
    case 'awaiting_approval':
      return {
        title: '等待人工裁决，发布排期尚未生成',
        hint: '审批通过后编排层会自动生成渠道排期；先完成裁决即可继续。',
        action: '去完成人工裁决',
        section: 'overview',
      };
    case 'running':
      return {
        title: '流水线执行中，发布排期尚未生成',
        hint: '任务通过门禁与人工审批后才会进入发布阶段。',
        action: '查看流水线进度',
        section: 'pipeline',
      };
    case 'failed':
      return {
        title: '任务执行失败，没有可登记的排期',
        hint: '先定位失败原因；修复后重开任务才会重新生成排期。',
        action: '查看失败现场',
        section: 'observe',
      };
    case 'rejected':
      return {
        title: '任务已被驳回，不会生成发布排期',
        hint: '可查看评估意见，或用「复制 Brief」重开一条任务。',
        action: '查看评估意见',
        section: 'quality',
      };
    case 'completed':
      return {
        title: '已完成，但未找到发布排期产物',
        hint: '可能排期生成失败；可在产物中确认 publish_plan，或重新运行渠道适配。',
        action: '查看已有产物',
        section: 'artifacts',
      };
    default:
      return {
        title: '发布排期尚未生成',
        hint: '审批通过后编排层会自动生成渠道排期。',
        action: '查看已有产物',
        section: 'artifacts',
      };
  }
}

export function TaskWorkspace({
  detail,
  events,
  agents,
  metrics,
  section,
  onSectionChange,
  deciding,
  onDecide,
  onPublishChanged,
  onToast,
  onOpenVideo,
  onOpenView,
  onCloneBrief,
  onDeleteTask,
}: {
  detail: TaskDetail;
  events: AgentEvent[];
  agents: AgentsResponse | null;
  metrics: MetricsView | null;
  section: TaskSectionKey;
  onSectionChange: (section: TaskSectionKey) => void;
  deciding: boolean;
  onDecide: (decision: 'approve' | 'revise' | 'reject', comment: string) => void;
  onPublishChanged: () => void;
  onToast: (text: string, error?: boolean) => void;
  onOpenVideo: (draft: VideoDraft) => void;
  onOpenView: (view: 'motion' | 'factory') => void;
  onCloneBrief: (brief: Brief) => void;
  onDeleteTask: (id: string) => Promise<void>;
}) {
  const [tool, setTool] = useState<ToolKey>('image');
  const [qualityTab, setQualityTab] = useState<'judge' | 'metrics'>('judge');
  const [observeTab, setObserveTab] = useState<'trace' | 'timeline'>('trace');
  const [exporting, setExporting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [artifactFocus, setArtifactFocus] = useState<{ taskId: string; focus: ArtifactFocus } | null>(null);

  const task = detail.task;
  const activeArtifactFocus = artifactFocus?.taskId === task.id ? artifactFocus.focus : null;
  const step = nextStep(task);
  const isAwaiting = task.status === 'awaiting_approval';
  const hasPublishPlan = task.artifacts.some((artifact) => artifact.type === 'publish_plan');
  const deliverEmpty = hasPublishPlan ? null : deliverEmptyState(task);
  const hasFinalDelivery = task.artifacts.some((artifact) => artifact.type === 'final_delivery');
  const canDelete = task.status !== 'running' && task.status !== 'awaiting_approval';
  const deadlineMs = parseDeadline(task.brief.deadline);
  const deadlineOverdue = deadlineMs !== null && deadlineMs < Date.now() && task.status !== 'completed';

  const exportDelivery = async (): Promise<void> => {
    setExporting(true);
    try {
      await downloadExport(task.id);
      onToast('成品已导出');
    } catch (error) {
      onToast(`导出失败：${error instanceof Error ? error.message : String(error)}`, true);
    } finally {
      setExporting(false);
    }
  };

  const removeTask = async (): Promise<void> => {
    setDeleting(true);
    try {
      await onDeleteTask(task.id);
      setConfirmDelete(false);
    } catch {
      /* App 已把失败原因 toast 出来，保留确认框让用户重试 */
    } finally {
      setDeleting(false);
    }
  };

  const agentNames = useMemo(() => {
    const map: Record<string, { name: string; role: string }> = {};
    for (const agent of agents?.implemented ?? []) {
      map[agent.id] = { name: agent.name, role: agent.role };
    }
    return map;
  }, [agents]);

  /** 最近一次评估结论（来自 SSE/轮询事件流），直接暴露在任务头部。 */
  const latestJudge = useMemo(() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (event.type !== 'judge.scored') continue;
      const payload = (event.payload ?? {}) as Record<string, unknown>;
      const total = typeof payload.total === 'number' ? payload.total : null;
      if (total === null) continue;
      return { total, verdict: String(payload.verdict ?? 'review'), mode: String(payload.mode ?? '') };
    }
    return null;
  }, [events]);

  const videoDraft = useMemo(() => buildVideoDraft(task), [task]);

  const recentArtifacts = useMemo(
    () => [...task.artifacts].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, ARTIFACT_RECENT),
    [task.artifacts],
  );

  const openArtifacts = (focus: ArtifactFocus) => {
    setArtifactFocus({ taskId: task.id, focus });
    onSectionChange('artifacts');
  };

  const activeTool = TOOLS.find((item) => item.key === tool) ?? TOOLS[0];

  const renderTool = () => {
    switch (tool) {
      case 'image':
        return <ImageGenPanel task={task} onToast={onToast} />;
      case 'video':
        return <VideoGenPanel task={task} onToast={onToast} />;
      case 'human':
        return <DigitalHumanPanel task={task} onToast={onToast} />;
      case 'dub':
        return <DubbingPanel task={task} onToast={onToast} />;
      case 'understand':
        return <VideoUnderstandPanel task={task} onToast={onToast} onChanged={onPublishChanged} />;
      case 'surf':
        return <SurfPanel task={task} onToast={onToast} onChanged={onPublishChanged} />;
      case 'pet':
        return <DesktopPetPanel task={task} onToast={onToast} />;
      case 'skill':
        return <SkillStudioPanel task={task} onToast={onToast} onChanged={onPublishChanged} />;
      default:
        return null;
    }
  };

  return (
    <>
      <div className="panel task-header">
        <div className="task-header-main">
          <div className="task-header-copy">
            <div className="task-title">
              {task.brief.brand}
              <span>· {task.brief.product}</span>
            </div>
            <div className="row task-brief-meta">
              <span>渠道 {task.brief.channel}</span>
              <span>目标 {task.brief.objective}</span>
              <span>行业 {task.brief.industry}</span>
              <span>受众 {task.brief.audience}</span>
            </div>
            <div className="row task-chips">
              <Chip tone={statusTone(task.status)}>{STATUS_LABEL[task.status]}</Chip>
              <Chip tone="tone-idle">{PHASE_LABEL[task.phase] ?? task.phase}</Chip>
              <Chip tone="tone-idle">返工 {task.revision_round} 轮</Chip>
              <Chip tone="tone-idle">第 {task.turn_used} 轮</Chip>
              <Chip tone="tone-idle">{task.artifacts.length} 件产物</Chip>
              <Chip tone="tone-idle" mono>
                {formatCost(task.tokens.cost_usd)}
              </Chip>
              {task.tokens.cached > 0 ? (
                <Chip tone="tone-ok" title="命中 LLM 缓存的调用次数">
                  缓存 {task.tokens.cached}
                </Chip>
              ) : null}
              {task.brief.priority === 'high' || task.brief.priority === 'urgent' ? (
                <Chip tone="tone-warn" title="Brief 优先级">
                  优先级 {PRIORITY_LABEL[task.brief.priority]}
                </Chip>
              ) : null}
              {task.brief.deadline ? (
                <Chip tone={deadlineOverdue ? 'tone-bad' : 'tone-idle'} title="Brief 截止时间">
                  截止 {formatDeadline(task.brief.deadline)}
                  {deadlineOverdue ? ' · 已过期' : ''}
                </Chip>
              ) : null}
              {task.tokens.cut_off ? <Chip tone="tone-warn">成本熔断</Chip> : null}
              {task.published_at ? <Chip tone="tone-ok">已排期 {formatDateTime(task.published_at)}</Chip> : null}
              {task.approval.decision !== 'pending' ? (
                <Chip
                  tone={task.approval.decision === 'approved' ? 'tone-ok' : 'tone-bad'}
                  title={task.approval.comment || undefined}
                >
                  人工裁决 {task.approval.decision === 'approved' ? '已通过' : '已驳回'}
                  {task.approval.decided_at ? ` · ${formatDateTime(task.approval.decided_at)}` : ''}
                </Chip>
              ) : null}
              {task.intent_conflicts > 0 ? (
                <Chip tone="tone-warn" title="黑板意图租约冲突次数">
                  租约冲突 {task.intent_conflicts}
                </Chip>
              ) : null}
              {latestJudge ? (
                <Chip
                  tone={latestJudge.verdict === 'pass' ? 'tone-ok' : latestJudge.verdict === 'reject' ? 'tone-bad' : 'tone-warn'}
                  title={`LLM-as-a-Judge 评估（${latestJudge.mode}）`}
                >
                  评估 {latestJudge.total.toFixed(1)}
                </Chip>
              ) : null}
            </div>
          </div>
          <div className="task-next">
            <span className="task-next-label">下一步</span>
            <button
              type="button"
              className={`btn btn-sm ${isAwaiting ? 'btn-ok' : 'btn-primary'}`}
              onClick={() => onSectionChange(step.section)}
            >
              {step.label} <IconArrowRight size={14} />
            </button>
            <span className="task-next-hint">{step.hint}</span>
          </div>
        </div>

        <div className="task-header-footer">
          <div className="task-times">
            <span>创建 {formatDateTime(task.created_at)}</span>
            <span>更新 {formatDateTime(task.updated_at)}</span>
            {task.finished_at ? <span>完成 {formatDateTime(task.finished_at)}</span> : null}
          </div>
          <div className="task-actions">
            {hasFinalDelivery ? (
              <button
                type="button"
                className="btn btn-sm"
                disabled={exporting}
                onClick={() => void exportDelivery()}
              >
                <IconDownload size={14} /> {exporting ? '导出中…' : '导出成品'}
              </button>
            ) : null}
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              title="以当前 Brief 为起点新建任务，不修改原任务"
              onClick={() => onCloneBrief(task.brief)}
            >
              <IconCopy size={14} /> 复制 Brief
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger-ghost"
              disabled={!canDelete}
              title={canDelete ? '删除任务记录、黑板产物与全部生成作业' : '执行中或待人工裁决的任务不能删除'}
              onClick={() => setConfirmDelete(true)}
            >
              <IconTrash size={14} /> 删除任务
            </button>
          </div>
        </div>
      </div>

      <div className="task-nav" role="tablist" aria-label="任务工作面">
        {SECTIONS.map((item) => {
          const Icon = item.icon;
          return (
            <button
              type="button"
              key={item.key}
              role="tab"
              aria-selected={section === item.key}
              className={`task-nav-item${section === item.key ? ' active' : ''}`}
              title={item.hint}
              onClick={() => {
                if (item.key === 'artifacts') setArtifactFocus(null);
                onSectionChange(item.key);
              }}
            >
              <Icon size={15} />
              <span>{item.label}</span>
              {item.key === 'artifacts' && task.artifacts.length ? <i>{task.artifacts.length}</i> : null}
              {item.key === 'observe' && events.length ? <i>{events.length}</i> : null}
            </button>
          );
        })}
      </div>

      <ApprovalPanel task={task} busy={deciding} onDecide={onDecide} />

      {section === 'overview' ? (
        <div className="task-overview">
          <div className="task-overview-grid">
            <div className="panel">
              <div className="panel-title">
                质量评分 <span className="count">· A5 / A6 / A7 门禁</span>
              </div>
              <Scorecard scorecard={task.scorecard} />
            </div>

            <div className="panel task-route">
              <div className="panel-title">
                下游去向 <span className="count">· 只在有产物时显式交接</span>
              </div>
              <div className="task-route-list">
                <div className="task-route-row">
                  <span className="task-route-mark accent-fast">
                    <WorkbenchIcon workbench="video" size={17} />
                  </span>
                  <div className="task-route-copy">
                    <b>视频工场</b>
                    <p>把最新视频脚本转成 SRT 字幕，直接进入分幕成片线。</p>
                    <small>
                      {videoDraft ? `可带入：${videoDraft.cues} 条字幕 · ${videoDraft.source}` : '等 A8 产出视频脚本后，这里会亮起「带入字幕」'}
                    </small>
                  </div>
                  <button
                    type="button"
                    className="btn btn-sm btn-primary"
                    disabled={!videoDraft}
                    title={videoDraft ? '只带入脚本已有时间轴与口播' : '任务还没有可用的视频脚本'}
                    onClick={() => videoDraft && onOpenVideo(videoDraft)}
                  >
                    带入字幕 →
                  </button>
                </div>

                <div className="task-route-row">
                  <span className="task-route-mark accent-produce">
                    <WorkbenchIcon workbench="factory" size={17} />
                  </span>
                  <div className="task-route-copy">
                    <b>影视制作工厂</b>
                    <p>按分镜与岗位生产镜头、配音与交付包；项目在工厂内独立创建。</p>
                    <small>只共享任务上下文，不自动搬运分镜或素材。</small>
                  </div>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => onOpenView('factory')}>
                    进入工厂 →
                  </button>
                </div>

                <div className="task-route-row">
                  <span className="task-route-mark accent-motion">
                    <WorkbenchIcon workbench="motion" size={17} />
                  </span>
                  <div className="task-route-copy">
                    <b>动效工场</b>
                    <p>试演标题、关键词与转场动效，为包装找参考；不直接出片。</p>
                    <small>可先记下预设名，再回视频工场套用。</small>
                  </div>
                  <button type="button" className="btn btn-sm btn-ghost" onClick={() => onOpenView('motion')}>
                    试演动效 →
                  </button>
                </div>
              </div>
            </div>
          </div>

          <TaskBrief brief={task.brief} status={task.status} />

          <div className="panel">
            <div className="panel-title">
              最近产物 <span className="count">· 最新 {recentArtifacts.length} 件</span>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                style={{ marginLeft: 'auto' }}
                onClick={() => {
                  setArtifactFocus(null);
                  onSectionChange('artifacts');
                }}
              >
                查看全部产物 →
              </button>
            </div>
            {recentArtifacts.length ? (
              <div className="task-artifact-list">
                {recentArtifacts.map((artifact) => (
                  <button
                    type="button"
                    className="task-artifact-row"
                    key={artifact.id}
                    title={`在产物面板中打开「${artifact.title}」`}
                    onClick={() => openArtifacts({ artifactId: artifact.id })}
                  >
                    <span className="task-artifact-type">{artifactLabel(artifact)}</span>
                    <span className="task-artifact-title">{artifact.title}</span>
                    <span className="task-artifact-meta">
                      {artifact.agent_id} · v{artifact.version} · {formatDateTime(artifact.created_at)}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <Empty>流水线还没有写入产物；等 A1 策略阶段完成后会出现在这里。</Empty>
            )}
          </div>
        </div>
      ) : null}

      {section === 'pipeline' ? (
        <div className="panel">
          <div className="panel-title">
            流水线看板{' '}
            <span className="count">
              · {task.pipeline.length} 个节点 · {task.packets.length} 张任务卡
            </span>
          </div>
          <Pipeline
            key={task.id}
            task={task}
            agentNames={agentNames}
            onOpenArtifacts={(agentId) => openArtifacts({ agentId })}
          />
        </div>
      ) : null}

      {section === 'artifacts' ? (
        <div className="panel">
          <div className="panel-title">
            全部产物 <span className="count">· {task.artifacts.length} 件</span>
          </div>
          <ArtifactViewer
            key={task.id}
            task={task}
            focus={activeArtifactFocus}
            agentNames={agentNames}
          />
        </div>
      ) : null}

      {section === 'tools' ? (
        <div className="tool-layout">
          <aside className="panel tool-menu">
            <div className="panel-title">创作工具</div>
            {TOOLS.map((item) => {
              const Icon = item.icon;
              return (
                <button
                  type="button"
                  key={item.key}
                  className={`tool-menu-item${tool === item.key ? ' active' : ''}`}
                  onClick={() => setTool(item.key)}
                >
                  <span className="tool-menu-mark">
                    <Icon size={15} />
                  </span>
                  <span className="tool-menu-copy">
                    <b>{item.label}</b>
                    <small>{item.hint}</small>
                  </span>
                </button>
              );
            })}
          </aside>
          <div className="tool-body">
            <div className="tool-intro">
              <div>
                <b>{activeTool.label}</b>
                <p>{activeTool.hint}</p>
              </div>
              <span className="muted small">作用域：当前任务 · {task.brief.brand} / {task.brief.product}</span>
            </div>
            {renderTool()}
          </div>
        </div>
      ) : null}

      {section === 'deliver' ? (
        <div className="task-deliver">
          <div className="panel">
            <div className="panel-title">发布与复盘路径</div>
            <div className="task-deliver-steps">
              <span>审批通过</span>
              <b>→</b>
              <span>渠道排期</span>
              <b>→</b>
              <span>投递 / 登记发布</span>
              <b>→</b>
              <span>回填效果</span>
              <b>→</b>
              <span>A10 复盘</span>
            </div>
          </div>
          <PublishPanel
            task={task}
            onChanged={onPublishChanged}
            onToast={onToast}
            onOpenArtifact={(artifactId) => openArtifacts({ artifactId })}
          />
          {deliverEmpty ? (
            <Empty>
              <div style={{ fontWeight: 600 }}>{deliverEmpty.title}</div>
              <div className="muted small" style={{ marginTop: 6 }}>
                {deliverEmpty.hint}
              </div>
              <div className="row" style={{ justifyContent: 'center', marginTop: 12 }}>
                <button
                  type="button"
                  className="btn btn-sm btn-primary"
                  onClick={() => onSectionChange(deliverEmpty.section)}
                >
                  {deliverEmpty.action} <IconArrowRight size={14} />
                </button>
              </div>
            </Empty>
          ) : null}
        </div>
      ) : null}

      {section === 'quality' ? (
        <div>
          <div className="sub-tabs">
            <button type="button" className={`sub-tab${qualityTab === 'judge' ? ' active' : ''}`} onClick={() => setQualityTab('judge')}>
              Judge 评估报告
            </button>
            <button type="button" className={`sub-tab${qualityTab === 'metrics' ? ' active' : ''}`} onClick={() => setQualityTab('metrics')}>
              运行指标
            </button>
          </div>
          {qualityTab === 'judge' ? (
            <div className="panel">
              <div className="panel-title">
                评估报告 <span className="count">· 与门禁解耦的质量度量</span>
              </div>
              <JudgePanel taskId={task.id} onToast={onToast} />
            </div>
          ) : (
            <MetricsPanel metrics={metrics} />
          )}
        </div>
      ) : null}

      {section === 'observe' ? (
        <div>
          <div className="sub-tabs">
            <button type="button" className={`sub-tab${observeTab === 'trace' ? ' active' : ''}`} onClick={() => setObserveTab('trace')}>
              调用轨迹
            </button>
            <button type="button" className={`sub-tab${observeTab === 'timeline' ? ' active' : ''}`} onClick={() => setObserveTab('timeline')}>
              事件时间线（{events.length}）
            </button>
          </div>
          {observeTab === 'trace' ? (
            <div className="panel">
              <div className="panel-title">
                调用轨迹 <span className="count">· span 树与耗时分布</span>
              </div>
              <TracePanel taskId={task.id} />
            </div>
          ) : (
            <div className="panel">
              <div className="panel-title">
                事件时间线
                <span className="count">
                  · 事实 {detail.blackboard.stats.fact_count}（已验证 {detail.blackboard.stats.verified_fact_count}）
                  · 评审 {detail.blackboard.stats.review_count}
                </span>
              </div>
              <Timeline key={task.id} events={events} />
            </div>
          )}
        </div>
      ) : null}

      {confirmDelete ? (
        <div className="modal-mask" onClick={() => (deleting ? undefined : setConfirmDelete(false))}>
          <div className="modal modal-sm" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
            <h2>删除任务？</h2>
            <p className="muted small" style={{ marginTop: 8 }}>
              将删除「{task.brief.brand} · {task.brief.product}」的任务记录、黑板产物与全部生成作业，删除后不可恢复。
            </p>
            <p className="muted small">正在执行或等待人工裁决的任务不能删除；如需删除请先完成裁决。</p>
            <div className="row" style={{ justifyContent: 'flex-end', marginTop: 18 }}>
              <button
                type="button"
                className="btn btn-ghost"
                disabled={deleting}
                onClick={() => setConfirmDelete(false)}
              >
                取消
              </button>
              <button type="button" className="btn btn-bad" disabled={deleting} onClick={() => void removeTask()}>
                {deleting ? <Spinner /> : null} 确认删除
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
