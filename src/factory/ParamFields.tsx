/** 岗位参数控件：检查器与「本轮改参」弹窗共用，避免两套控件行为漂移。 */
import type { NodeSpec, ParamSpec } from './api.ts';

export function paramVisible(param: ParamSpec, values: Record<string, unknown>, spec: NodeSpec): boolean {
  if (!param.when) return true;
  const [key, expected] = param.when.split('=');
  const current = String(values[key] ?? spec.params.find((item) => item.name === key)?.default ?? '');
  return current === (expected ?? '');
}

/** 保存图的有效参数 = 规格默认值 + 节点已存参数。 */
export function mergedValues(spec: NodeSpec, params: Record<string, unknown> | undefined): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const param of spec.params) merged[param.name] = params?.[param.name] ?? param.default;
  return merged;
}

export function ParamFields({
  spec,
  values,
  onPatch,
}: {
  spec: NodeSpec;
  values: Record<string, unknown>;
  onPatch: (key: string, value: unknown) => void;
}) {
  const rows = spec.params
    .filter((param) => paramVisible(param, values, spec))
    .map((param) => ({ param, value: values[param.name] }));

  if (!rows.length) return <div className="muted small">这个岗位没有可调参数。</div>;

  return (
    <div className="fc-params">
      {rows.map(({ param, value }) => (
        <label className="fc-param" key={param.name} title={param.hint || undefined}>
          <span className="fc-param-label">
            {param.label}
            {param.unit ? <em>（{param.unit}）</em> : null}
          </span>
          {param.kind === 'toggle' ? (
            <input type="checkbox" checked={Boolean(value)} onChange={(event) => onPatch(param.name, event.target.checked)} />
          ) : param.kind === 'select' ? (
            <select value={String(value ?? '')} onChange={(event) => onPatch(param.name, event.target.value)}>
              {param.options.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          ) : param.kind === 'range' ? (
            <span className="fc-range">
              <input
                type="range"
                min={param.minimum ?? 0}
                max={param.maximum ?? 1}
                step={param.step ?? 0.01}
                value={Number(value ?? param.default ?? 0)}
                onChange={(event) => onPatch(param.name, Number(event.target.value))}
              />
              <b className="mono">{Number(value ?? 0).toFixed(param.step && param.step < 1 ? 2 : 0)}</b>
            </span>
          ) : param.kind === 'textarea' ? (
            <textarea rows={3} value={String(value ?? '')} onChange={(event) => onPatch(param.name, event.target.value)} />
          ) : param.kind === 'number' ? (
            <input
              type="number"
              min={param.minimum ?? undefined}
              max={param.maximum ?? undefined}
              step={param.step ?? undefined}
              value={String(value ?? '')}
              onChange={(event) => onPatch(param.name, Number(event.target.value))}
            />
          ) : (
            <input type="text" value={String(value ?? '')} onChange={(event) => onPatch(param.name, event.target.value)} />
          )}
          {param.hint ? <small className="muted">{param.hint}</small> : null}
        </label>
      ))}
    </div>
  );
}
