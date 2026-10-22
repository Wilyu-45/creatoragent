import type { HealthView } from '../lib/api.ts';
import { WORKBENCHES, type AppView } from '../lib/workbenches.ts';
import { IconHome, IconPlus, IconSettings, LogoMark, WorkbenchIcon } from './icons.tsx';
import { Chip } from './ui.tsx';

export function Topbar({
  view,
  health,
  totalCount,
  runningCount,
  awaitingCount,
  onNavigate,
  onNewTask,
  onOpenSettings,
}: {
  view: AppView;
  health: HealthView | null;
  totalCount: number;
  runningCount: number;
  awaitingCount: number;
  onNavigate: (view: AppView) => void;
  onNewTask: () => void;
  onOpenSettings: () => void;
}) {
  const provider = health?.provider;
  const checkpointer = health?.checkpointer;
  const otlp = health?.otlp;
  // 断点续跑退化为内存实现时，进程重启会导致运行中任务永久卡住 —— 必须显式告警
  const ephemeralCheckpoint = checkpointer != null && checkpointer.kind !== 'sqlite';

  return (
    <header className="topbar">
      <button type="button" className="brand-mark brand-link" onClick={() => onNavigate('home')} aria-label="回到首页">
        <span className="brand-logo">
          <LogoMark size={21} />
        </span>
        <span className="brand-text">
          <span className="brand-title">Creator Agent Studio</span>
          <span className="brand-sub">策划 · 生产 · 包装 · 成片</span>
        </span>
      </button>

      <nav className="topnav" aria-label="工作台导航">
        <button
          type="button"
          className={`topnav-item${view === 'home' ? ' active' : ''}`}
          aria-current={view === 'home' ? 'page' : undefined}
          onClick={() => onNavigate('home')}
        >
          <IconHome size={15} />
          <span>首页</span>
        </button>
        {WORKBENCHES.map((item) => (
          <button
            type="button"
            key={item.key}
            className={`topnav-item${view === item.key ? ' active' : ''}`}
            aria-current={view === item.key ? 'page' : undefined}
            onClick={() => onNavigate(item.key)}
          >
            <WorkbenchIcon workbench={item.key} size={15} />
            <span>{item.short}</span>
            {item.key === 'tasks' && totalCount > 0 ? <i>{totalCount}</i> : null}
          </button>
        ))}
      </nav>

      <div className="topbar-spacer" />

      <div className="topbar-status">
        <Chip
          className="topbar-provider"
          tone={provider?.simulated ? 'tone-warn' : 'tone-ok'}
          mono
          title={provider?.agentOverrides ? `${provider.agentOverrides} 个智能体使用自定义模型覆盖（详见运行指标）` : undefined}
        >
          {provider ? `${provider.name} · ${provider.model}` : '连接中…'}
        </Chip>
        {runningCount > 0 ? <Chip tone="tone-run">运行中 {runningCount}</Chip> : null}
        {awaitingCount > 0 ? <Chip tone="tone-warn">待裁决 {awaitingCount}</Chip> : null}
        {otlp?.enabled ? (
          <Chip tone="tone-ok" title={`OTLP → ${otlp.endpoint}（已导出 ${otlp.exported} 个 span）`}>
            OTLP
          </Chip>
        ) : null}
        {ephemeralCheckpoint ? (
          <Chip tone="tone-bad" title={checkpointer?.error || '检查点未能落到 SQLite'}>
            续跑不可用
          </Chip>
        ) : null}
      </div>

      <button type="button" className="btn btn-ghost topbar-settings" onClick={onOpenSettings}>
        <IconSettings size={15} />
        <span>设置</span>
      </button>
      <button type="button" className="btn btn-primary topbar-new" onClick={onNewTask}>
        <IconPlus size={15} strokeWidth={2.2} />
        <span>新建任务</span>
      </button>
    </header>
  );
}
