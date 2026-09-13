import type { HealthView } from '../lib/api.ts';
import { Chip } from './ui.tsx';

export function Topbar({
  health,
  totalCount,
  runningCount,
  onNewTask,
  onOpenSettings,
}: {
  health: HealthView | null;
  totalCount: number;
  runningCount: number;
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
      <div className="brand-mark">
        <div className="brand-logo">CA</div>
        <div>
          <div className="brand-title">Creator Agent Studio</div>
          <div className="brand-sub">多智能体协作创作台 · 共享黑板 + 阶段门禁</div>
        </div>
      </div>

      <div className="topbar-spacer" />

      <Chip tone={provider?.simulated ? 'tone-warn' : 'tone-ok'} mono>
        {provider ? `${provider.name} · ${provider.model}` : '连接中…'}
      </Chip>
      <Chip tone="tone-info">任务 {totalCount}</Chip>
      {runningCount > 0 ? <Chip tone="tone-run">运行中 {runningCount}</Chip> : null}
      {otlp?.enabled ? (
        <Chip tone="tone-ok" title={`OTLP → ${otlp.endpoint}（已导出 ${otlp.exported} 个 span）`}>
          OTLP 已接入
        </Chip>
      ) : null}
      {ephemeralCheckpoint ? (
        <Chip tone="tone-bad" title={checkpointer?.error || '检查点未能落到 SQLite'}>
          断点续跑不可用
        </Chip>
      ) : null}

      <button className="btn btn-ghost" onClick={onOpenSettings}>
        设置
      </button>
      <button className="btn btn-primary" onClick={onNewTask}>
        ＋ 新建创作任务
      </button>
    </header>
  );
}
