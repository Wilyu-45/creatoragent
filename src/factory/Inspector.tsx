/**
 * 岗位检查器 —— 选中节点的参数编辑、交付物预览与岗位日志。
 *
 * 参数控件由后端 ``Param.kind`` 决定（text/textarea/number/range/select/toggle），
 * ``when`` 决定条件显示（如 ``task=i2v`` 时才出现首尾帧选项）；只有偏离默认值的
 * 参数才写进图里，保持画布 JSON 干净、也让引擎签名（缓存判定）不被无意义改动打扰。
 *
 * 交付物预览按端口类型分派：图像铺九宫格、音频给播放器、字幕列条目、时间轴给规模、
 * 报告给分数与问题清单——其余一律折叠成 JSON 文本，不猜结构。
 */
import { useMemo, useState } from 'react';
import type { EventRow, Graph, GraphIssue, GraphNode, NodeSpec, ParamSpec, RunNodeResult } from './api.ts';
import { mediaUrl, NODE_STATUS_LABEL } from './api.ts';

function visible(param: ParamSpec, values: Record<string, unknown>, spec: NodeSpec): boolean {
  if (!param.when) return true;
  const [key, expected] = param.when.split('=');
  const current = String(values[key] ?? spec.params.find((item) => item.name === key)?.default ?? '');
  return current === (expected ?? '');
}

function briefValue(value: unknown): string {
  if (value && typeof value === 'object') {
    const row = value as Record<string, unknown>;
    if (typeof row.url === 'string') return String(row.label ?? row.ref ?? row.kind ?? row.url);
    return JSON.stringify(row).slice(0, 60);
  }
  return String(value);
}

export function Inspector({
  node,
  spec,
  result,
  outputs,
  pending,
  logs,
  issues,
  graph,
  onChange,
  onRunNode,
}: {
  node: GraphNode | null;
  spec: NodeSpec | null;
  result: RunNodeResult | null;
  /** 岗位完整交付物原值（作业接口只回摘要，原值从项目缓存按节点取）。 */
  outputs: Record<string, unknown> | null;
  pending: boolean;
  logs: EventRow[];
  issues: GraphIssue[];
  graph: Graph;
  onChange: (next: Graph) => void;
  onRunNode: (id: string) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({});

  const patchParams = (patch: Record<string, unknown>) => {
    if (!node || !spec) return;
    const merged = { ...node.params };
    for (const [key, value] of Object.entries(patch)) {
      const param = spec.params.find((item) => item.name === key);
      if (param && (value === param.default || value === '')) delete merged[key];
      else merged[key] = value;
    }
    onChange({ ...graph, nodes: graph.nodes.map((item) => (item.id === node.id ? { ...item, params: merged } : item)) });
  };

  const rows = useMemo(() => {
    if (!spec || !node) return [];
    const merged: Record<string, unknown> = {};
    for (const param of spec.params) merged[param.name] = node.params[param.name] ?? param.default;
    return spec.params.filter((param) => visible(param, merged, spec)).map((param) => ({ param, value: merged[param.name] }));
  }, [spec, node]);

  if (!node || !spec) {
    return (
      <div className="fc-inspector">
        <div className="fc-insp-empty">在画布上选择一个岗位，这里会给出它的参数与交付物。</div>
      </div>
    );
  }

  return (
    <div className="fc-inspector">
      <div className="fc-insp-head">
        <span className="fc-insp-icon">{spec.icon}</span>
        <input
          className="fc-insp-title"
          value={node.title ?? ''}
          placeholder={spec.title}
          onChange={(event) => onChange({ ...graph, nodes: graph.nodes.map((item) => (item.id === node.id ? { ...item, title: event.target.value } : item)) })}
        />
        <button className="btn btn-sm btn-primary" onClick={() => onRunNode(node.id)} title="只跑这个岗位（含其全部上游）">
          单跑
        </button>
      </div>
      <div className="fc-insp-role">{spec.role}</div>
      <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
        <span className="fc-chip">{spec.dept_label}</span>
        {spec.source ? <span className="fc-chip ok">可独立运行</span> : null}
        {spec.needs_service ? <span className="fc-chip warn">需接真服务商</span> : null}
        {node.muted ? <span className="fc-chip bad">已静音</span> : null}
        {spec.tags.map((tag) => (
          <span className="fc-chip" key={tag}>
            {tag}
          </span>
        ))}
      </div>

      {issues.length ? (
        <div className="fc-insp-issues">
          {issues.map((issue, index) => (
            <div className={`fc-issue ${issue.level}`} key={index}>
              {issue.level === 'error' ? '⛔' : '⚠'} {issue.message}
            </div>
          ))}
        </div>
      ) : null}

      <div className="fc-insp-section">参数</div>
      {rows.length ? (
        <div className="fc-params">
          {rows.map(({ param, value }) => (
            <label className="fc-param" key={param.name} title={param.hint || undefined}>
              <span className="fc-param-label">
                {param.label}
                {param.unit ? <em>（{param.unit}）</em> : null}
              </span>
              {param.kind === 'toggle' ? (
                <input type="checkbox" checked={Boolean(value)} onChange={(event) => patchParams({ [param.name]: event.target.checked })} />
              ) : param.kind === 'select' ? (
                <select value={String(value ?? '')} onChange={(event) => patchParams({ [param.name]: event.target.value })}>
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
                    onChange={(event) => patchParams({ [param.name]: Number(event.target.value) })}
                  />
                  <b className="mono">{Number(value ?? 0).toFixed(param.step && param.step < 1 ? 2 : 0)}</b>
                </span>
              ) : param.kind === 'textarea' ? (
                <textarea rows={3} value={String(value ?? '')} onChange={(event) => patchParams({ [param.name]: event.target.value })} />
              ) : param.kind === 'number' ? (
                <input
                  type="number"
                  min={param.minimum ?? undefined}
                  max={param.maximum ?? undefined}
                  step={param.step ?? undefined}
                  value={String(value ?? '')}
                  onChange={(event) => patchParams({ [param.name]: Number(event.target.value) })}
                />
              ) : (
                <input type="text" value={String(value ?? '')} onChange={(event) => patchParams({ [param.name]: event.target.value })} />
              )}
              {param.hint ? <small className="muted">{param.hint}</small> : null}
            </label>
          ))}
        </div>
      ) : (
        <div className="muted small">这个岗位没有可调参数。</div>
      )}

      <div className="fc-insp-section">
        交付物{result ? <span className="muted small"> · {NODE_STATUS_LABEL[result.status] ?? result.status} · {result.elapsed_ms ?? 0}ms</span> : null}
      </div>
      {result?.error ? <div className="fc-issue error">{result.error}</div> : null}
      {result?.summary ? <div className="fc-summary">{result.summary}</div> : null}
      {!result ? <div className="muted small">还没有产物：跑一次（或单跑这个岗位）。</div> : null}
      {result && pending ? <div className="muted small">完整交付物读取中…</div> : null}
      {result && !pending && !outputs ? <div className="muted small">这个岗位没有可读的完整交付物：没跑成功、被跳过，或缓存已被新签名清掉。</div> : null}
      <div className="fc-outputs">
        {spec.outputs.map((port) => {
          const value = outputs?.[port.name];
          if (value === undefined || value === null) return null;
          const isOpen = open[port.name] ?? true;
          return (
            <div className="fc-out" key={port.name}>
              <button className="fc-out-head" onClick={() => setOpen((prev) => ({ ...prev, [port.name]: !isOpen }))}>
                {isOpen ? '▾' : '▸'} {port.label}
                <span className="muted small">{port.type}</span>
              </button>
              {isOpen ? <OutputBody value={value} /> : null}
            </div>
          );
        })}
      </div>

      <div className="fc-insp-section">
        岗位日志 <span className="muted small">{logs.length} 条</span>
      </div>
      <div className="fc-logs">
        {logs.length ? (
          logs.map((row, index) => (
            <div className={`fc-log ${String(row.level ?? 'info')}`} key={index}>
              <span className="mono muted">{String(row.ts ?? '').slice(11)}</span> {String(row.message ?? '')}
            </div>
          ))
        ) : (
          <div className="muted small">暂无日志。</div>
        )}
      </div>
    </div>
  );
}

function OutputBody({ value }: { value: unknown }) {
  const row = (value ?? {}) as Record<string, unknown>;
  const items = Array.isArray(row.items) ? row.items : [];
  if (items.length) {
    const images = items.filter((item) => String((item as Record<string, unknown>).kind ?? '').match(/key|frame|concept|cover|poster|storyboard|grade|waveform|card/i));
    const sounds = items.filter((item) => /\.(wav|mp3|ogg)$/i.test(String((item as Record<string, unknown>).url ?? '')));
    const others = items.filter((item) => !images.includes(item) && !sounds.includes(item));
    return (
      <div className="fc-out-body">
        {typeof row.total_s === 'number' ? <div className="muted small">{Number(row.total_s).toFixed(2)}s · {items.length} 项 · {String(row.provider ?? '')}{row.simulated ? ' · 示意' : ''}</div> : null}
        {images.length ? (
          <div className="fc-thumbs">
            {images.slice(0, 12).map((item, index) => {
              const cell = item as Record<string, unknown>;
              return (
                <a href={mediaUrl(String(cell.url ?? ''))} target="_blank" rel="noreferrer" key={index} title={String(cell.ref ?? cell.kind ?? '')}>
                  <img src={mediaUrl(String(cell.url ?? ''))} alt={String(cell.kind ?? 'image')} loading="lazy" />
                </a>
              );
            })}
          </div>
        ) : null}
        {sounds.length ? (
          <div className="fc-audio">
            {sounds.slice(0, 6).map((item, index) => {
              const cell = item as Record<string, unknown>;
              return (
                <div key={index}>
                  <span className="small">{String(cell.character ?? cell.kind ?? '')} {Number(cell.duration_s ?? 0).toFixed(2)}s</span>
                  <audio controls preload="none" src={mediaUrl(String(cell.url ?? ''))} />
                </div>
              );
            })}
          </div>
        ) : null}
        {others.length ? (
          <ul className="fc-list">
            {others.slice(0, 8).map((item, index) => (
              <li key={index}>{briefValue(item)}</li>
            ))}
            {others.length > 8 ? <li className="muted">…另 {others.length - 8} 项</li> : null}
          </ul>
        ) : null}
      </div>
    );
  }

  if (Array.isArray(row.shots)) {
    const shots = row.shots as Record<string, unknown>[];
    return (
      <ul className="fc-list tight">
        {shots.slice(0, 10).map((shot, index) => (
          <li key={index}>
            <span className="mono">{String(shot.slate ?? shot.id)}</span> {String(shot.size_label ?? '')} {Number(shot.duration_s ?? 0).toFixed(2)}s{' '}
            {String(shot.description ?? '').slice(0, 40)}
          </li>
        ))}
        {shots.length > 10 ? <li className="muted">…另 {shots.length - 10} 镜</li> : null}
      </ul>
    );
  }

  if (Array.isArray(row.cues)) {
    const cues = row.cues as Record<string, unknown>[];
    return (
      <ul className="fc-list tight">
        {cues.slice(0, 12).map((cue, index) => (
          <li key={index}>
            <span className="mono">
              {Number(cue.start ?? 0).toFixed(2)}–{Number(cue.end ?? 0).toFixed(2)}
            </span>{' '}
            {String(cue.text_cn ?? cue.text ?? '')}
          </li>
        ))}
        {cues.length > 12 ? <li className="muted">…另 {cues.length - 12} 条</li> : null}
      </ul>
    );
  }

  if (Array.isArray(value)) {
    return (
      <ul className="fc-list">
        {(value as unknown[]).slice(0, 10).map((item, index) => (
          <li key={index}>{briefValue(item)}</li>
        ))}
      </ul>
    );
  }

  if (typeof value === 'string') {
    return <pre className="fc-text">{value}</pre>;
  }

  if (typeof row.score === 'number' || row.verdict !== undefined) {
    const issues = Array.isArray(row.issues) ? (row.issues as Record<string, unknown>[]) : [];
    return (
      <div className="fc-out-body">
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <b style={{ fontSize: 20 }}>{Number(row.score ?? 0).toFixed(1)}</b>
          <span className={`fc-chip ${String(row.verdict) === 'pass' ? 'ok' : 'warn'}`}>{String(row.verdict ?? '')}</span>
          {typeof row.coverage === 'number' ? <span className="muted small">覆盖 {(row.coverage * 100).toFixed(0)}%</span> : null}
        </div>
        {issues.length ? (
          <ul className="fc-list tight">
            {issues.slice(0, 8).map((issue, index) => (
              <li key={index}>
                <span className={`fc-chip ${String(issue.severity) === 'blocker' || String(issue.severity) === 'major' ? 'bad' : 'warn'}`}>
                  {String(issue.severity ?? 'info')}
                </span>{' '}
                {String(issue.item ?? issue.message ?? issue.rule ?? JSON.stringify(issue).slice(0, 60))}
              </li>
            ))}
          </ul>
        ) : (
          <div className="muted small">没有发现问题。</div>
        )}
      </div>
    );
  }

  if (Array.isArray(row.timeline) || row.duration_s !== undefined || row.count !== undefined) {
    return (
      <div className="fc-out-body">
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <span className="fc-chip">{String(row.label ?? '时间轴')}</span>
          <span className="fc-chip">{Number(row.duration_s ?? 0).toFixed(2)}s</span>
          <span className="fc-chip">{Number(row.count ?? 0)} 镜</span>
          <span className="fc-chip">{String(row.aspect ?? '')} @{String(row.fps ?? '')}fps</span>
          {typeof row.cuts === 'number' ? <span className="fc-chip">{row.cuts} 刀</span> : null}
          {Array.isArray(row.missing_visual) && row.missing_visual.length ? <span className="fc-chip bad">缺画面 {row.missing_visual.length}</span> : null}
          {row.simulated ? <span className="fc-chip warn">示意产物</span> : null}
        </div>
        {typeof row.note === 'string' && row.note ? <pre className="fc-text">{row.note}</pre> : null}
      </div>
    );
  }

  return <pre className="fc-text">{JSON.stringify(value, null, 1).slice(0, 1600)}</pre>;
}
