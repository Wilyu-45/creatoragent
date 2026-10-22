/** 三个工场共用的页头：面包屑、定位、来源任务与右侧动作区。 */
import type { ReactNode } from 'react';
import type { WorkbenchMeta } from '../lib/workbenches.ts';
import { WorkbenchIcon } from './icons.tsx';

export function WorkbenchHead({
  meta,
  subtitle,
  chain,
  onHome,
  sourceTask,
  onOpenTask,
  children,
}: {
  meta: WorkbenchMeta;
  subtitle?: string;
  chain?: { upstream: string; downstream: string };
  onHome: () => void;
  sourceTask?: { id: string; label: string } | null;
  onOpenTask?: () => void;
  children?: ReactNode;
}) {
  return (
    <header className={`workbench-head ${meta.accent}`}>
      <span className={`workbench-mark ${meta.accent}`}>
        <WorkbenchIcon workbench={meta.key} size={23} />
      </span>
      <div className="workbench-id">
        <nav className="crumbs" aria-label="面包屑">
          <button type="button" onClick={onHome}>
            首页
          </button>
          <span>/</span>
          <span>{meta.name}</span>
        </nav>
        <div className="workbench-title-row">
          <h1>{meta.name}</h1>
          <span className={`workbench-role ${meta.accent}`}>{meta.role}</span>
          {sourceTask ? (
            <button
              type="button"
              className="workbench-source"
              onClick={onOpenTask}
              title="从任务进入：共享任务上下文，不自动搬运素材"
            >
              来源任务 · {sourceTask.label} <b>回到任务 →</b>
            </button>
          ) : null}
        </div>
        <p className="workbench-sub">{subtitle ?? meta.purpose}</p>
        {chain ? (
          <div className="workbench-chain">
            <span>
              <b>上游</b> {chain.upstream}
            </span>
            <span>
              <b>下游</b> {chain.downstream}
            </span>
          </div>
        ) : null}
      </div>
      {children ? <div className="workbench-extra">{children}</div> : null}
    </header>
  );
}
