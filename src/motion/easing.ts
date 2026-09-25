/**
 * 缓动函数库 —— 动效组件的节奏内核。
 *
 * 所有动效组件都只做一件事：把「播放头时间」换算成 0..1 的进度，
 * 再用这里的缓动函数把线性进度映射成有节奏的进度。
 * 纯时间驱动（无内部定时器），因此天然支持 seek / 倍速 / 逐帧渲染（Remotion 兼容）。
 */

/** 缓动：预置名（'easeOut'…）或 cubic-bezier 控制点 [x1,y1,x2,y2]。 */
export type Easing = string | [number, number, number, number];

/** cubic-bezier 求解：给定时间比例 x，返回进度 y（牛顿迭代 + 二分兜底）。 */
export function cubicBezier(x1: number, y1: number, x2: number, y2: number): (x: number) => number {
  const cx = 3 * x1;
  const bx = 3 * (x2 - x1) - cx;
  const ax = 1 - cx - bx;
  const cy = 3 * y1;
  const by = 3 * (y2 - y1) - cy;
  const ay = 1 - cy - by;
  const sampleX = (t: number) => ((ax * t + bx) * t + cx) * t;
  const sampleY = (t: number) => ((ay * t + by) * t + cy) * t;
  const sampleDX = (t: number) => (3 * ax * t + 2 * bx) * t + cx;
  const solveX = (x: number) => {
    let t = x;
    for (let i = 0; i < 8; i += 1) {
      const dx = sampleX(t) - x;
      if (Math.abs(dx) < 1e-6) return t;
      const d = sampleDX(t);
      if (Math.abs(d) < 1e-6) break;
      t -= dx / d;
    }
    let lo = 0;
    let hi = 1;
    t = x;
    while (lo < hi) {
      if (sampleX(t) < x) lo = t + 1e-5;
      else hi = t - 1e-5;
      t = (lo + hi) / 2;
    }
    return t;
  };
  return (x: number) => {
    if (x <= 0) return 0;
    if (x >= 1) return 1;
    return sampleY(solveX(x));
  };
}

const bounceOut = (x: number): number => {
  const n1 = 7.5625;
  const d1 = 2.75;
  if (x < 1 / d1) return n1 * x * x;
  if (x < 2 / d1) return n1 * (x -= 1.5 / d1) * x + 0.75;
  if (x < 2.5 / d1) return n1 * (x -= 2.25 / d1) * x + 0.9375;
  return n1 * (x -= 2.625 / d1) * x + 0.984375;
};

/** 预置缓动表。 */
export const EASINGS: Record<string, (x: number) => number> = {
  linear: (x) => x,
  ease: cubicBezier(0.25, 0.1, 0.25, 1),
  easeIn: cubicBezier(0.42, 0, 1, 1),
  easeOut: cubicBezier(0, 0, 0.58, 1),
  easeInOut: cubicBezier(0.42, 0, 0.58, 1),
  easeInQuad: (x) => x * x,
  easeOutQuad: (x) => 1 - (1 - x) * (1 - x),
  easeOutBack: (x) => 1 + 2.70158 * Math.pow(x - 1, 3) + 1.70158 * Math.pow(x - 1, 2),
  easeOutElastic: (x) =>
    x === 0 ? 0 : x === 1 ? 1 : Math.pow(2, -10 * x) * Math.sin((x * 10 - 0.75) * ((2 * Math.PI) / 3)) + 1,
  easeOutBounce: bounceOut,
};

/** 把 Easing 描述解析为可调用函数（未知名字回退 easeOut）。 */
export function resolveEasing(easing: Easing = 'easeOut'): (x: number) => number {
  if (Array.isArray(easing)) return cubicBezier(easing[0], easing[1], easing[2], easing[3]);
  return EASINGS[easing] ?? EASINGS.easeOut;
}

/** 线性插值（数值 / 颜色 #rrggbb 均可）。 */
export function interp(from: number | string, to: number | string, p: number): number | string {
  if (typeof from === 'number' && typeof to === 'number') return from + (to - from) * p;
  const hex = (v: number | string): [number, number, number] => {
    const s = String(v).replace('#', '');
    const n = s.length === 3 ? s.split('').map((c) => c + c).join('') : s;
    return [parseInt(n.slice(0, 2), 16), parseInt(n.slice(2, 4), 16), parseInt(n.slice(4, 6), 16)];
  };
  const a = hex(from);
  const b = hex(to);
  const mix = a.map((v, i) => Math.round(v + (b[i] - v) * p));
  return `#${mix.map((v) => v.toString(16).padStart(2, '0')).join('')}`;
}
