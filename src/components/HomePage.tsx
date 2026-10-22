/** 首页：四个工作台的统一入口，以及「策划 → 生产 → 包装 → 成片 / 复盘」的链路总图。 */
import { useEffect, useMemo, useState } from 'react';
import type { HealthView, TaskSummary } from '../lib/api.ts';
import { factoryApi } from '../factory/api.ts';
import { PRESET_META } from '../motion/presets.tsx';
import { formatDateTime, STATUS_LABEL, statusTone } from '../lib/format.ts';
import { WORKBENCHES, type AppView, type TaskSectionKey, type WorkbenchKey, workbenchMeta } from '../lib/workbenches.ts';
import { IconActivity, IconArrowRight, IconCompass, IconPlus, IconShieldCheck, WorkbenchIcon } from './icons.tsx';
import { Chip } from './ui.tsx';

export function HomePage({
  tasks,
  health,
  onOpenView,
  onOpenTask,
  onNewTask,
}: {
  tasks: TaskSummary[];
  health: HealthView | null;
  onOpenView: (view: AppView) => void;
  onOpenTask: (id: string, section?: TaskSectionKey) => void;
  onNewTask: () => void;
}) {
  const [factoryStats, setFactoryStats] = useState<{ projects: number; nodes: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void Promise.all([factoryApi.listProjects(), factoryApi.palette()])
      .then(([projects, palette]) => {
        if (!cancelled) setFactoryStats({ projects: projects.projects.length, nodes: palette.node_count });
      })
      .catch(() => {
        /* 首页统计失败不阻塞入口；卡片退回静态能力描述 */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const runningCount = tasks.filter((task) => task.status === 'running').length;
  const awaitingCount = tasks.filter((task) => task.status === 'awaiting_approval').length;
  const recent = useMemo(
    () => [...tasks].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 6),
    [tasks],
  );
  const latest = recent[0] ?? null;

  const statFor = (key: WorkbenchKey): string => {
    if (key === 'tasks') return `${tasks.length} 个任务 · ${runningCount} 个执行中`;
    if (key === 'factory') return factoryStats ? `${factoryStats.projects} 个项目 · ${factoryStats.nodes} 个岗位` : '60 个岗位 · 画布生产';
    if (key === 'video') return '字幕 → 分幕 → MP4';
    return `${PRESET_META.length} 个动效预设`;
  };

  const flowNode = (key: WorkbenchKey, sub: string) => {
    const meta = workbenchMeta(key);
    return (
      <button type="button" className={`home-flow-node ${meta.accent}`} onClick={() => onOpenView(key)}>
        <span className="home-flow-node-title">
          <WorkbenchIcon workbench={key} size={15} />
          <b>{meta.short}</b>
        </span>
        <span>{sub}</span>
      </button>
    );
  };

  const arrow = (label: string) => (
    <span className="home-flow-arrow">
      <i>{label}</i>
      <IconArrowRight size={16} />
    </span>
  );

  return (
    <div className="home">
      <section className="home-hero">
        <div className="home-hero-copy">
          <div className="home-eyebrow">CREATOR AGENT STUDIO · 创作总图</div>
          <h1>
            四个工作台
            <br />
            <span>一条创作主线</span>
          </h1>
          <p>
            先策划，再按需要走重生产或快成片；动效随时试演，成片后统一回到创作任务发布与复盘。
            每个工作台只负责一段，输入与产出写在入口上。
          </p>
          <div className="home-hero-actions">
            <button className="btn btn-primary" onClick={onNewTask}>
              <IconPlus size={15} strokeWidth={2.2} /> 新建创作任务
            </button>
            {latest ? (
              <button className="btn btn-ghost" onClick={() => onOpenTask(latest.id)}>
                继续最近任务 · {latest.brief.brand}
              </button>
            ) : (
              <button className="btn btn-ghost" onClick={() => onOpenView('factory')}>
                先看看影视制作工厂
              </button>
            )}
          </div>
        </div>

        <aside className="home-hero-panel">
          <div className="home-hero-panel-title">当前状态</div>
          <div className="home-stat-row">
            <button type="button" className="is-plan" onClick={() => onOpenView('tasks')}>
              <span className="home-stat-icon"><IconCompass size={14} /></span>
              <b>{tasks.length}</b>
              <span>创作任务</span>
            </button>
            <button type="button" className="is-run" onClick={() => onOpenView('tasks')}>
              <span className="home-stat-icon"><IconActivity size={14} /></span>
              <b>{runningCount}</b>
              <span>运行中</span>
            </button>
            <button type="button" className="is-await" onClick={() => onOpenView('tasks')}>
              <span className="home-stat-icon"><IconShieldCheck size={14} /></span>
              <b>{awaitingCount}</b>
              <span>待审批</span>
            </button>
          </div>
          <div className="home-hero-route">
            <span className="muted small">默认主线</span>
            <div>
              创作任务 <b>→</b> 影视制作工厂 / 视频工场 <b>→</b> 发布复盘
            </div>
          </div>
          <div className="home-hero-foot">
            <Chip tone={health?.provider.simulated ? 'tone-warn' : 'tone-ok'} mono>
              {health ? `${health.provider.name} · ${health.provider.model}` : '连接中…'}
            </Chip>
            <span className="muted small">动效工场横切支撑两条产线</span>
          </div>
        </aside>
      </section>

      <section className="home-section">
        <div className="home-section-head">
          <div>
            <h2>选择工作台</h2>
            <p>四个入口，各自对一段结果负责。点击卡片进入，顶部导航随时切换。</p>
          </div>
          <span className="muted small">共 {WORKBENCHES.length} 个工作台</span>
        </div>
        <div className="home-wb-grid">
          {WORKBENCHES.map((meta, index) => (
            <article className={`home-wb ${meta.accent}`} key={meta.key}>
              <div className="home-wb-top">
                <span className={`home-wb-mark ${meta.accent}`}>
                  <WorkbenchIcon workbench={meta.key} size={22} />
                </span>
                <div className="home-wb-name">
                  <span className="home-wb-role">{meta.role}</span>
                  <h3>{meta.name}</h3>
                </div>
                <span className="home-wb-index">{String(index + 1).padStart(2, '0')}</span>
              </div>
              <p className="home-wb-purpose">{meta.purpose}</p>
              <dl className="home-wb-io">
                <div>
                  <dt>输入</dt>
                  <dd>{meta.input}</dd>
                </div>
                <div>
                  <dt>产出</dt>
                  <dd>{meta.output}</dd>
                </div>
              </dl>
              <div className="home-wb-foot">
                <span className="home-wb-stat">{statFor(meta.key)}</span>
                <button type="button" className="btn btn-sm home-wb-enter" onClick={() => onOpenView(meta.key)}>
                  进入工作台 <IconArrowRight size={14} />
                </button>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="home-section">
        <div className="home-section-head">
          <div>
            <h2>创作主线</h2>
            <p>两条产线共用同一套策划与复盘；动效工场不直接出片，只提供包装与时间轴预演。</p>
          </div>
        </div>
        <div className="home-flow">
          <div className="home-lane">
            <div className="home-lane-label">
              <b>产线 A</b>
              <span>快成片</span>
            </div>
            <div className="home-lane-track">
              {flowNode('tasks', 'Brief → 策略 / 文案 / 脚本')}
              {arrow('脚本 / 字幕')}
              {flowNode('video', '字幕 → 分幕 → 成片')}
              {arrow('成片')}
              <button
                type="button"
                className="home-flow-node home-flow-end"
                onClick={() => (latest ? onOpenTask(latest.id, 'deliver') : onOpenView('tasks'))}
              >
                <b>发布复盘</b>
                <span>{latest ? `最近：${latest.brief.brand}` : '先创建任务'}</span>
              </button>
            </div>
          </div>

          <div className="home-lane">
            <div className="home-lane-label">
              <b>产线 B</b>
              <span>重生产</span>
            </div>
            <div className="home-lane-track">
              {flowNode('tasks', '分镜 / 镜头脚本')}
              {arrow('分镜 / 岗位')}
              {flowNode('factory', '60 个岗位 → 交付包')}
              {arrow('交付包')}
              <button
                type="button"
                className="home-flow-node home-flow-end"
                onClick={() => (latest ? onOpenTask(latest.id, 'deliver') : onOpenView('tasks'))}
              >
                <b>发布复盘</b>
                <span>统一回到创作任务</span>
              </button>
            </div>
          </div>

          <div className="home-lane home-lane-support">
            <div className="home-lane-label">
              <b>横切支撑</b>
              <span>包装</span>
            </div>
            <div className="home-lane-track">
              {flowNode('motion', '预设试演 / 时间轴脚本')}
              {arrow('动效参考')}
              <div className="home-flow-targets">
                <button type="button" onClick={() => onOpenView('video')}>
                  视频工场 · 包装成片
                </button>
                <button type="button" onClick={() => onOpenView('factory')}>
                  影视制作工厂 · 包装镜头
                </button>
              </div>
            </div>
          </div>
        </div>
        <p className="home-flow-note">
          链路说明：目前只有「任务视频脚本 → 视频工场字幕」是一键带入；其余跳转只共享任务上下文，不自动搬运素材。
        </p>
      </section>

      <div className="home-lower">
        <section className="home-recent">
          <div className="home-section-head">
            <div>
              <h2>最近任务</h2>
              <p>从任何一个工作台回来，都先在这里确认进度与人工裁决。</p>
            </div>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => onOpenView('tasks')}>
              全部任务 →
            </button>
          </div>
          {recent.length ? (
            <div className="home-task-list">
              {recent.map((task) => (
                <button type="button" className="home-task" key={task.id} onClick={() => onOpenTask(task.id)}>
                  <span className="home-task-main">
                    <b>{task.brief.brand}</b>
                    <span>{task.brief.product}</span>
                  </span>
                  <span className="home-task-meta">
                    {task.phase_label} · {task.artifact_count} 件产物 · {formatDateTime(task.updated_at)}
                  </span>
                  <Chip tone={statusTone(task.status)}>{STATUS_LABEL[task.status]}</Chip>
                </button>
              ))}
            </div>
          ) : (
            <div className="home-empty">
              <div>还没有创作任务。</div>
              <button type="button" className="btn btn-sm btn-primary" onClick={onNewTask}>
                从一份 Brief 开始
              </button>
            </div>
          )}
        </section>

        <section className="home-boundaries">
          <div className="home-section-head">
            <div>
              <h2>能力边界速查</h2>
              <p>能力有重叠时，按“谁对最终结果负责”来选择。</p>
            </div>
          </div>
          <ul>
            <li>
              <b>创作任务 vs 影视制作工厂</b>
              <span>创作任务做策划、审核与发布；工厂只负责岗位化生产，不在工厂里改 Brief。</span>
            </li>
            <li>
              <b>影视制作工厂 vs 视频工场</b>
              <span>工厂是多岗位、多镜头、交付包；视频工场是字幕驱动的单文件快成片。</span>
            </li>
            <li>
              <b>动效工场 vs 视频工场</b>
              <span>动效工场只试演组件与时间轴，不导出成片；成片与导出在视频工场。</span>
            </li>
            <li>
              <b>发布与复盘</b>
              <span>统一回到创作任务工作台；影视制作工厂与视频工场都不做发布和效果回填。</span>
            </li>
          </ul>
        </section>
      </div>
    </div>
  );
}
