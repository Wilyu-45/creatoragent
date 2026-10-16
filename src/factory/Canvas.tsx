/**
 * 工厂画布 —— 平移/缩放、岗位拖动、端口拉线、连线与状态着色、小地图。
 *
 * 几何是纯函数（``nodeHeight`` / ``portPoint``）：连线的贝塞尔控制点和节点 DOM
 * 必须用同一套数字，否则缩放后线会飘。视图变换只作用在一个 ``layer`` 上
 * （translate + scale，原点 0,0），屏幕坐标与世界坐标的换算就走这一处。
 *
 * 连线时按后端 ``PORT_COMPAT`` 的口径做兼容判断（前端复制一份矩阵，
 * 用来即时拒绝非法连线；最终裁决仍在服务端 ``validate_graph``）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { Graph, GraphEdge, GraphNode, NodeRunState, NodeSpec, PortSpec } from './api.ts';
import { NODE_STATUS_LABEL } from './api.ts';

export const NODE_W = 252;
const HEAD_H = 36;
const ROW_H = 21;
const BODY_H = 30;
const GRID = 20;

/** 源端口类型可以流向哪些目标端口（与 app/factory/spec.py 的 PORT_COMPAT 对齐）。 */
const PORT_COMPAT: Record<string, string[]> = {
  brief: ['brief', 'any'],
  text: ['text', 'any'],
  outline: ['outline', 'text', 'any'],
  screenplay: ['screenplay', 'any'],
  characters: ['characters', 'any'],
  locations: ['locations', 'any'],
  shots: ['shots', 'timeline', 'any'],
  look: ['look', 'any'],
  camera_plan: ['camera_plan', 'shots', 'any'],
  lighting_plan: ['lighting_plan', 'any'],
  images: ['images', 'clips', 'timeline', 'any'],
  clips: ['clips', 'timeline', 'any'],
  audio: ['audio', 'timeline', 'any'],
  subtitles: ['subtitles', 'timeline', 'any'],
  model: ['model', 'any'],
  report: ['report', 'any'],
  plan: ['plan', 'any'],
  timeline: ['timeline', 'any'],
  any: ['any'],
};

export function compatible(source: string, target: string): boolean {
  if (source === target || target === 'any' || source === 'any') return true;
  return (PORT_COMPAT[source] ?? []).includes(target);
}

export function portColor(type: string): string {
  const hue = 172 + (type.length * 47) % 330;
  return `hsl(${hue} 62% 58%)`;
}

export function nodeHeight(spec: NodeSpec | undefined): number {
  const rows = Math.max(spec?.inputs.length ?? 0, spec?.outputs.length ?? 0, 1);
  return HEAD_H + rows * ROW_H + BODY_H;
}

export interface PortRef {
  node: GraphNode;
  port: PortSpec;
  kind: 'in' | 'out';
  index: number;
}

/** 端口圆点的世界坐标：节点几何（头部高度 + 行高）固定，与规格无关。 */
export function portPoint(ref: PortRef): { x: number; y: number } {
  const [nx, ny] = ref.node.pos;
  const y = ny + HEAD_H + ref.index * ROW_H + ROW_H / 2;
  const side = ref.kind === 'in' ? -7 : NODE_W + 7;
  return { x: nx + side, y };
}

function portIndex(spec: NodeSpec | undefined, kind: 'in' | 'out', name: string): number {
  const list = (kind === 'in' ? spec?.inputs : spec?.outputs) ?? [];
  return Math.max(0, list.findIndex((item) => item.name === name));
}

export function edgePath(from: { x: number; y: number }, to: { x: number; y: number }): string {
  const span = Math.max(46, Math.min(200, Math.abs(to.x - from.x) * 0.55, Math.abs(to.y - from.y) * 0.9 + 60));
  return `M ${from.x} ${from.y} C ${from.x + span} ${from.y}, ${to.x - span} ${to.y}, ${to.x} ${to.y}`;
}

export interface CanvasProps {
  graph: Graph;
  specs: Record<string, NodeSpec>;
  selected: string[];
  runs: Record<string, NodeRunState>;
  badNodes: Set<string>;
  badEdges: Set<string>;
  onSelect: (ids: string[]) => void;
  onChange: (next: Graph) => void;
  onOpenNode: (id: string) => void;
}

export function Canvas({ graph, specs, selected, runs, badNodes, badEdges, onSelect, onChange, onOpenNode }: CanvasProps) {
  const hostRef = useRef<HTMLDivElement | null>(null);
  const [view, setView] = useState({ x: 40, y: 40, k: 0.75 });
  const [drag, setDrag] = useState<{ id: string; dx: number; dy: number } | null>(null);
  const [pan, setPan] = useState<{ x: number; y: number; start: number[] } | null>(null);
  const [wire, setWire] = useState<{ from: PortRef; x: number; y: number; bad: boolean } | null>(null);
  const [hoverEdge, setHoverEdge] = useState('');

  const toWorld = useCallback(
    (clientX: number, clientY: number) => {
      const rect = hostRef.current?.getBoundingClientRect();
      if (!rect) return { x: 0, y: 0 };
      return { x: (clientX - rect.left - view.x) / view.k, y: (clientY - rect.top - view.y) / view.k };
    },
    [view],
  );

  const nodesById = useMemo(() => Object.fromEntries(graph.nodes.map((node) => [node.id, node])), [graph.nodes]);

  /** 连线端点：找不到岗位规格时退回节点中心，避免整条线消失。 */
  const endpoint = useCallback(
    (nodeId: string, kind: 'in' | 'out', portName: string) => {
      const node = nodesById[nodeId];
      if (!node) return null;
      const spec = specs[node.type];
      const ref: PortRef = { node, port: { name: portName, label: '', type: 'any', required: false, many: false }, kind, index: portIndex(spec, kind, portName) };
      return portPoint(ref);
    },
    [nodesById, specs],
  );

  useEffect(() => {
    if (!drag && !pan && !wire) return;
    const move = (event: PointerEvent) => {
      const world = toWorld(event.clientX, event.clientY);
      if (drag) {
        const node = nodesById[drag.id];
        if (!node) return;
        const snapped = graph.nodes.map((item) =>
          item.id === drag.id
            ? { ...item, pos: [Math.round((world.x - drag.dx) / GRID) * GRID, Math.round((world.y - drag.dy) / GRID) * GRID] }
            : item,
        );
        onChange({ ...graph, nodes: snapped });
      } else if (pan) {
        setView((prev) => ({ ...prev, x: pan.start[0] + (event.clientX - pan.x), y: pan.start[1] + (event.clientY - pan.y) }));
      } else {
        setWire((prev) => (prev ? { ...prev, x: world.x, y: world.y } : prev));
      }
    };
    const up = () => {
      setDrag(null);
      setPan(null);
      setWire(null);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
  }, [drag, pan, wire, graph, nodesById, onChange, toWorld]);

  /** 滚轮以光标为锚点缩放：世界坐标下光标点住的部位在缩放后保持不动。 */
  const onWheel = (event: React.WheelEvent) => {
    const rect = hostRef.current?.getBoundingClientRect();
    if (!rect) return;
    const factor = Math.exp(-event.deltaY * 0.0015);
    const next = Math.min(2.2, Math.max(0.15, view.k * factor));
    const cx = event.clientX - rect.left;
    const cy = event.clientY - rect.top;
    setView((prev) => ({ k: next, x: cx - ((cx - prev.x) / prev.k) * next, y: cy - ((cy - prev.y) / prev.k) * next }));
  };

  const startWire = (ref: PortRef, event: React.PointerEvent) => {
    event.stopPropagation();
    const world = toWorld(event.clientX, event.clientY);
    setWire({ from: ref, x: world.x, y: world.y, bad: false });
  };

  const dropWire = (ref: PortRef) => {
    if (!wire) return;
    const a = wire.from;
    const b = ref;
    setWire(null);
    if (a.kind === b.kind || a.node.id === b.node.id) return;
    const out = a.kind === 'out' ? a : b;
    const inp = a.kind === 'out' ? b : a;
    const outSpec = specs[out.node.type];
    const inSpec = specs[inp.node.type];
    const sourcePort = outSpec?.outputs.find((item) => item.name === out.port.name);
    const targetPort = inSpec?.inputs.find((item) => item.name === inp.port.name);
    if (!sourcePort || !targetPort) return;
    if (!compatible(sourcePort.type, targetPort.type)) return;
    // 防环：目标岗位的任意下游已经指向源岗位时，这条线会造出环路，直接拒绝
    if (reaches(graph, inp.node.id, out.node.id)) return;
    const kept = targetPort.many ? graph.edges : graph.edges.filter((edge) => !(edge.dst === inp.node.id && edge.dst_port === inp.port.name));
    const edge: GraphEdge = {
      id: `e-${inp.node.id}-${inp.port.name}-${Math.random().toString(36).slice(2, 7)}`,
      src: out.node.id,
      src_port: out.port.name,
      dst: inp.node.id,
      dst_port: inp.port.name,
    };
    onChange({ ...graph, edges: [...kept, edge] });
  };

  const moveNode = (node: GraphNode, event: React.PointerEvent) => {
    event.stopPropagation();
    const world = toWorld(event.clientX, event.clientY);
    setDrag({ id: node.id, dx: world.x - node.pos[0], dy: world.y - node.pos[1] });
    const next = selected.includes(node.id) ? selected : [node.id];
    if (next.length !== selected.length || next.some((id, index) => id !== selected[index])) onSelect(next);
  };

  const removeEdge = (id: string) => onChange({ ...graph, edges: graph.edges.filter((edge) => edge.id !== id) });

  const deleteSelected = () => {
    if (!selected.length) return;
    onChange({
      nodes: graph.nodes.filter((node) => !selected.includes(node.id)),
      edges: graph.edges.filter((edge) => !selected.includes(edge.src) && !selected.includes(edge.dst)),
    });
    onSelect([]);
  };

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && /input|textarea|select/i.test(target.tagName)) return;
      if (event.key === 'Delete' || event.key === 'Backspace') {
        event.preventDefault();
        deleteSelected();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  });

  const fitView = () => {
    const rect = hostRef.current?.getBoundingClientRect();
    if (!rect || !graph.nodes.length) return;
    const xs = graph.nodes.map((node) => node.pos[0]);
    const ys = graph.nodes.map((node) => node.pos[1]);
    const minX = Math.min(...xs);
    const minY = Math.min(...ys);
    const maxX = Math.max(...xs) + NODE_W;
    const maxY = Math.max(...ys) + 200;
    const k = Math.min(1.1, Math.max(0.12, Math.min((rect.width - 40) / (maxX - minX), (rect.height - 40) / (maxY - minY))));
    setView({ k, x: (rect.width - (maxX - minX) * k) / 2 - minX * k, y: (rect.height - (maxY - minY) * k) / 2 - minY * k });
  };

  const world = useMemo(() => {
    const xs = graph.nodes.map((node) => node.pos[0]);
    const ys = graph.nodes.map((node) => node.pos[1]);
    return {
      minX: xs.length ? Math.min(...xs) - 60 : 0,
      minY: ys.length ? Math.min(...ys) - 60 : 0,
      maxX: xs.length ? Math.max(...xs) + NODE_W + 60 : 800,
      maxY: ys.length ? Math.max(...ys) + 320 : 600,
    };
  }, [graph.nodes]);

  const minimap = useMemo(() => {
    const w = 168;
    const spanX = Math.max(1, world.maxX - world.minX);
    const spanY = Math.max(1, world.maxY - world.minY);
    const scale = Math.min(w / spanX, 120 / spanY);
    return { w, h: spanY * scale, scale, minX: world.minX, minY: world.minY };
  }, [world]);

  return (
    <div
      className="fc-canvas"
      ref={hostRef}
      onWheel={onWheel}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        setPan({ x: event.clientX, y: event.clientY, start: [view.x, view.y] });
        onSelect([]);
      }}
    >
      <div className="fc-grid" style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${GRID * 5 * view.k}px ${GRID * 5 * view.k}px` }} />
      <div className="fc-layer" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.k})`, transformOrigin: '0 0' }}>
        <svg className="fc-wires" width={Math.max(1, world.maxX)} height={Math.max(1, world.maxY)} style={{ overflow: 'visible' }}>
          {graph.edges.map((edge) => {
            const from = endpoint(edge.src, 'out', edge.src_port);
            const to = endpoint(edge.dst, 'in', edge.dst_port);
            if (!from || !to) return null;
            const srcNode = nodesById[edge.src];
            const dstNode = nodesById[edge.dst];
            const spec = srcNode ? specs[srcNode.type] : undefined;
            const outPort = spec?.outputs.find((item) => item.name === edge.src_port);
            const muted = dstNode ? Boolean(dstNode.muted) : false;
            const bad = badEdges.has(edge.id);
            return (
              <g key={edge.id || `${edge.src}-${edge.dst}`}>
                <path
                  d={edgePath(from, to)}
                  className={`fc-wire${hoverEdge === edge.id ? ' hover' : ''}${bad ? ' bad' : ''}${muted ? ' muted' : ''}`}
                  stroke={bad ? '#e0566b' : portColor(outPort?.type ?? 'any')}
                  onPointerDown={(event) => event.stopPropagation()}
                  onPointerEnter={() => setHoverEdge(edge.id)}
                  onPointerLeave={() => setHoverEdge('')}
                />
                {hoverEdge === edge.id ? (
                  <g
                    className="fc-wire-cut"
                    onPointerDown={(event) => {
                      event.stopPropagation();
                      removeEdge(edge.id);
                      setHoverEdge('');
                    }}
                  >
                    <circle cx={(from.x + to.x) / 2} cy={(from.y + to.y) / 2} r={9} />
                    <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 + 4} textAnchor="middle">
                      ×
                    </text>
                  </g>
                ) : null}
              </g>
            );
          })}
          {wire ? (
            <path
              d={edgePath(portPoint(wire.from), { x: wire.x, y: wire.y })}
              className={`fc-wire draft${wire.bad ? ' bad' : ''}`}
              stroke={portColor(wire.from.port.type)}
            />
          ) : null}
        </svg>

        {graph.nodes.map((node) => {
          const spec = specs[node.type];
          const run = runs[node.id];
          const height = nodeHeight(spec);
          const isSelected = selected.includes(node.id);
          return (
            <div
              key={node.id}
              className={`fc-node${isSelected ? ' sel' : ''}${run?.status ? ` st-${run.status}` : ''}${node.muted ? ' muted' : ''}${badNodes.has(node.id) ? ' bad' : ''}`}
              style={{ left: node.pos[0], top: node.pos[1], width: NODE_W, height }}
              onPointerDown={(event) => moveNode(node, event)}
              onDoubleClick={() => onOpenNode(node.id)}
            >
              <div className="fc-node-head">
                <span className="fc-node-icon">{spec?.icon ?? '❔'}</span>
                <span className="fc-node-title">{node.title || spec?.title || node.type}</span>
                <span className={`fc-node-badge st-${run?.status ?? 'idle'}`}>{run?.status ? NODE_STATUS_LABEL[run.status] : ''}</span>
              </div>
              <div className="fc-node-ports">
                {(spec?.inputs ?? []).map((port, index) => (
                  <div className="fc-port in" key={port.name} style={{ top: index * ROW_H }} onPointerUp={() => dropWire({ node, port, kind: 'in', index })}>
                    <span
                      className="fc-dot"
                      style={{ background: portColor(port.type) }}
                      title={`${port.label}（${port.type}）${port.many ? ' · 可多条汇入' : port.required ? ' · 必需' : ' · 可选'}`}
                    />
                    <span className="fc-port-label">{port.label}</span>
                  </div>
                ))}
                {(spec?.outputs ?? []).map((port, index) => (
                  <div
                    className="fc-port out"
                    key={port.name}
                    style={{ top: index * ROW_H }}
                    onPointerDown={(event) => startWire({ node, port, kind: 'out', index }, event)}
                    onPointerUp={() => dropWire({ node, port, kind: 'out', index })}
                  >
                    <span className="fc-port-label">{port.label}</span>
                    <span
                      className="fc-dot"
                      style={{ background: portColor(port.type) }}
                      title={`${port.label}（${port.type}）`}
                    />
                  </div>
                ))}
              </div>
              <div className="fc-node-foot">
                <span className="fc-node-summary">{spec ? `${spec.dept_label} · ${paramBrief(node, spec)}` : '未知岗位'}</span>
                {run?.status === 'running' ? <span className="fc-node-bar"><i style={{ width: `${Math.round((run.progress ?? 0) * 100)}%` }} /></span> : null}
              </div>
              <button
                className="fc-node-mute"
                title={node.muted ? '取消静音' : '静音：跳过执行，下游用上次缓存'}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={() => onChange({ ...graph, nodes: graph.nodes.map((item) => (item.id === node.id ? { ...item, muted: !node.muted } : item)) })}
              >
                {node.muted ? '🔇' : '🔊'}
              </button>
            </div>
          );
        })}
      </div>

      <div className="fc-viewbar">
        <button className="btn btn-sm btn-ghost" onClick={fitView}>
          适应画布
        </button>
        <button className="btn btn-sm btn-ghost" onClick={() => setView((prev) => ({ ...prev, k: Math.max(0.15, prev.k / 1.2) }))}>
          －
        </button>
        <span className="muted small">{Math.round(view.k * 100)}%</span>
        <button className="btn btn-sm btn-ghost" onClick={() => setView((prev) => ({ ...prev, k: Math.min(2.2, prev.k * 1.2) }))}>
          ＋
        </button>
        <button className="btn btn-sm btn-ghost" onClick={deleteSelected} disabled={!selected.length} title="删除选中岗位（Del）">
          删除选中
        </button>
      </div>

      <div className="fc-minimap" style={{ width: minimap.w, height: minimap.h }}>
        {graph.nodes.map((node) => (
          <i
            key={node.id}
            style={{
              left: (node.pos[0] - minimap.minX) * minimap.scale,
              top: (node.pos[1] - minimap.minY) * minimap.scale,
              width: NODE_W * minimap.scale,
              height: nodeHeight(specs[node.type]) * minimap.scale,
              background: runs[node.id]?.status === 'error' ? '#e0566b' : runs[node.id]?.status === 'ok' ? '#4ec9a1' : '#5a6478',
            }}
          />
        ))}
        <span
          style={{
            left: -view.x / view.k * minimap.scale - minimap.minX * minimap.scale,
            top: -view.y / view.k * minimap.scale - minimap.minY * minimap.scale,
            width: (hostRef.current?.clientWidth ?? 0) / view.k * minimap.scale,
            height: (hostRef.current?.clientHeight ?? 0) / view.k * minimap.scale,
          }}
        />
      </div>
    </div>
  );
}

function paramBrief(node: GraphNode, spec: NodeSpec): string {
  const rows = spec.params
    .filter((param) => node.params[param.name] !== undefined && node.params[param.name] !== param.default)
    .slice(0, 2)
    .map((param) => `${param.label}=${String(node.params[param.name])}`);
  return rows.length ? rows.join(' · ') : spec.role.slice(0, 18);
}

/** DFS：target 的下游里是否已经存在 source（存在则新连线会成环）。 */
function reaches(graph: Graph, from: string, target: string): boolean {
  const seen = new Set<string>();
  const stack = [from];
  while (stack.length) {
    const current = stack.pop() as string;
    if (current === target) return true;
    if (seen.has(current)) continue;
    seen.add(current);
    for (const edge of graph.edges) if (edge.src === current) stack.push(edge.dst);
  }
  return false;
}
