/** 「本轮改参」弹窗：只生成本轮补丁，相对保存值做 diff，不改画布。 */
import { useState } from 'react';
import type { GraphNode, NodeSpec } from './api.ts';
import { ParamFields } from './ParamFields.tsx';

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a === null || b === null || a === undefined || b === undefined) return false;
  if (typeof a === 'object' || typeof b === 'object') return JSON.stringify(a) === JSON.stringify(b);
  return false;
}

export function RunOverrideDialog({
  node,
  spec,
  savedValues,
  currentPatch,
  onSave,
  onClear,
  onClose,
}: {
  node: GraphNode;
  spec: NodeSpec;
  /** 保存图的有效参数（岗位默认值 + 已存参数）。 */
  savedValues: Record<string, unknown>;
  currentPatch: Record<string, unknown> | undefined;
  onSave: (patch: Record<string, unknown>) => void;
  onClear: () => void;
  onClose: () => void;
}) {
  const [values, setValues] = useState<Record<string, unknown>>(() => ({ ...savedValues, ...(currentPatch ?? {}) }));

  const patch: Record<string, unknown> = {};
  for (const param of spec.params) {
    const value = values[param.name] ?? param.default;
    if (!sameValue(value, savedValues[param.name] ?? param.default)) patch[param.name] = value;
  }
  const keys = Object.keys(patch);

  return (
    <div className="modal-mask" role="presentation" onClick={onClose}>
      <div className="modal fc-override-modal" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
        <h2>本轮改参 · {node.title || spec.title}</h2>
        <div className="muted small">
          只影响这一次运行，不写入画布。改参会改变岗位签名，该岗位及下游按新参数重算；未改的上游仍复用缓存。
        </div>
        <div className="fc-override-warn">
          缓存与产物按岗位单槽同名覆盖：本轮成功后该岗位的旧缓存/同名产物会被覆盖；若本轮失败，旧缓存会被清掉。历史版本不单独保留。
        </div>
        <ParamFields spec={spec} values={values} onPatch={(key, value) => setValues((prev) => ({ ...prev, [key]: value }))} />
        <div className="fc-override-foot">
          <span className="muted small">
            {keys.length ? `本轮覆盖 ${keys.length} 项：${keys.join('、')}` : '没有偏离保存值的改动'}
          </span>
          <span className="fc-dock-side">
            {currentPatch && Object.keys(currentPatch).length ? (
              <button className="btn btn-sm btn-ghost" onClick={onClear}>
                清除本轮改参
              </button>
            ) : null}
            <button className="btn btn-sm btn-ghost" onClick={() => setValues({ ...savedValues })} disabled={!keys.length}>
              还原
            </button>
            <button className="btn btn-sm" onClick={onClose}>
              取消
            </button>
            <button
              className="btn btn-sm btn-primary"
              onClick={() => onSave(patch)}
              title={keys.length ? '保存后随下一次预检/运行生效' : '没有改动：保存会清除这个岗位的本轮改参'}
            >
              保存改参
            </button>
          </span>
        </div>
      </div>
    </div>
  );
}
