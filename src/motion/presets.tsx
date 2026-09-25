/**
 * 动效组件库 —— 入场 / 强调 / 出场 / 转场 四组 22 个预设。
 *
 * 统一约定：
 * - 全部为「时间 → 渲染状态」的纯函数组件（无内部定时器），Remotion-ready；
 * - `duration` / `delay` 单位为秒；`easing` 见 ./easing.ts；
 * - 入场组件在进度前保持隐藏、完成后保持定格（fill both）；
 * - 强调组件在元素已可见的前提下播放（结束回到常态）；
 * - 转场组件用于场景容器整体的进场表现，配合 <Sequence> 切换场景。
 */
import {
  cloneElement,
  isValidElement,
  type CSSProperties,
  type ReactElement,
  type ReactNode,
} from 'react';
import { interp, type Easing } from './easing.ts';
import { useLocalTime, useProgress } from './engine.tsx';

export interface MotionProps {
  /** 动画时长（秒）。 */
  duration?: number;
  /** 延迟（秒）——在局部时间轴上的起点。 */
  delay?: number;
  easing?: Easing;
  style?: CSSProperties;
  className?: string;
  children?: ReactNode;
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/* ============================== 入场（Entrance） ============================== */

export function FadeIn({ duration = 0.6, delay = 0, easing = 'easeOut', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: p }}>
      {children}
    </div>
  );
}

export function SlideIn({
  duration = 0.6,
  delay = 0,
  easing = 'easeOut',
  direction = 'up',
  distance = 36,
  style,
  className,
  children,
}: MotionProps & { direction?: 'left' | 'right' | 'up' | 'down'; distance?: number }) {
  const p = useProgress(delay, duration, easing);
  const dx = direction === 'left' ? distance : direction === 'right' ? -distance : 0;
  const dy = direction === 'up' ? distance : direction === 'down' ? -distance : 0;
  return (
    <div
      className={className}
      style={{ ...style, opacity: p, transform: `translate(${dx * (1 - p)}px, ${dy * (1 - p)}px)` }}
    >
      {children}
    </div>
  );
}

export function ScaleIn({
  duration = 0.6,
  delay = 0,
  easing = 'easeOutBack',
  from = 0.55,
  style,
  className,
  children,
}: MotionProps & { from?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: Math.min(1, p * 2), transform: `scale(${from + (1 - from) * p})` }}>
      {children}
    </div>
  );
}

export function RotateIn({
  duration = 0.7,
  delay = 0,
  easing = 'easeOutBack',
  degrees = 180,
  clockwise = false,
  style,
  className,
  children,
}: MotionProps & { degrees?: number; clockwise?: boolean }) {
  const p = useProgress(delay, duration, easing);
  const deg = (clockwise ? 1 : -1) * degrees * (1 - p);
  return (
    <div className={className} style={{ ...style, opacity: Math.min(1, p * 2), transform: `rotate(${deg}deg) scale(${0.6 + 0.4 * p})` }}>
      {children}
    </div>
  );
}

export function BlurIn({ duration = 0.7, delay = 0, easing = 'easeOut', blur = 12, style, className, children }: MotionProps & { blur?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: p, filter: `blur(${blur * (1 - p)}px)` }}>
      {children}
    </div>
  );
}

/** 打字机：children 必须是纯文本；光标按播放头闪烁。 */
export function Typewriter({
  duration = 1.6,
  delay = 0,
  easing = 'linear',
  cursor = true,
  cursorColor = '#22d3ee',
  style,
  className,
  children,
}: MotionProps & { cursor?: boolean; cursorColor?: string }) {
  const local = useLocalTime();
  const p = useProgress(delay, duration, easing);
  const text = typeof children === 'string' ? children : String(children ?? '');
  const shown = text.slice(0, Math.ceil(clamp01(p) * text.length));
  const caretOn = cursor && p < 1 ? Math.floor(local * 2.6) % 2 === 0 : false;
  return (
    <div className={className} style={{ ...style, whiteSpace: 'pre-wrap' }}>
      {shown}
      {cursor ? <span style={{ color: cursorColor, opacity: caretOn || p >= 1 ? 1 : 0 }}>▌</span> : null}
    </div>
  );
}

/** 逐字弹出：每个字符依次上浮淡入（列表 / 关键词）。 */
export function CharStagger({ duration = 1.2, delay = 0, easing = 'linear', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  const text = typeof children === 'string' ? children : String(children ?? '');
  const chars = Array.from(text);
  return (
    <div className={className} style={{ ...style, whiteSpace: 'pre-wrap' }}>
      {chars.map((ch, i) => {
        const start = (i / Math.max(chars.length, 1)) * 0.6;
        const cp = clamp01((p - start) / 0.4);
        return (
          <span key={i} style={{ display: 'inline-block', opacity: cp, transform: `translateY(${(1 - cp) * 10}px)` }}>
            {ch === ' ' ? '\u00A0' : ch}
          </span>
        );
      })}
    </div>
  );
}

/** 下划线从一端划过来（标题强调）。 */
export function UnderlineReveal({
  duration = 0.6,
  delay = 0,
  easing = 'easeInOut',
  direction = 'left-to-right',
  color = '#22d3ee',
  thickness = 3,
  style,
  className,
  children,
}: MotionProps & { direction?: 'left-to-right' | 'right-to-left'; color?: string; thickness?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <span className={className} style={{ ...style, position: 'relative', display: 'inline-block' }}>
      {children}
      <i
        aria-hidden
        style={{
          position: 'absolute',
          bottom: -4,
          [direction === 'left-to-right' ? 'left' : 'right']: 0,
          width: `${p * 100}%`,
          height: thickness,
          borderRadius: thickness,
          background: color,
        }}
      />
    </span>
  );
}

/**
 * 描边绘制：children 必须是单个 SVG `<path>`（或 `<g>`），
 * 组件注入 pathLength=1 + dashoffset 驱动描边，请自备 stroke 属性。
 */
export function StrokeDraw({ duration = 1.2, delay = 0, easing = 'easeInOut', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  if (!isValidElement(children)) return null;
  const el = children as ReactElement<Record<string, unknown>>;
  return cloneElement(el, {
    ...(el.props as object),
    pathLength: 1,
    strokeDasharray: 1,
    strokeDashoffset: 1 - p,
    style: { ...(el.props.style as CSSProperties | undefined), ...(style ?? {}) },
    className,
  });
}

/** 遮罩揭示：擦除式出现（ltr/rtl/ttb/btt）。 */
export function MaskReveal({
  duration = 0.7,
  delay = 0,
  easing = 'easeInOut',
  direction = 'ltr',
  style,
  className,
  children,
}: MotionProps & { direction?: 'ltr' | 'rtl' | 'ttb' | 'btt' }) {
  const p = useProgress(delay, duration, easing);
  const inset =
    direction === 'ltr'
      ? `inset(0 ${(1 - p) * 100}% 0 0)`
      : direction === 'rtl'
        ? `inset(0 0 0 ${(1 - p) * 100}%)`
        : direction === 'ttb'
          ? `inset(0 0 ${(1 - p) * 100}% 0)`
          : `inset(${(1 - p) * 100}% 0 0 0)`;
  return (
    <div className={className} style={{ ...style, clipPath: inset }}>
      {children}
    </div>
  );
}

/* ============================== 强调（Emphasis） ============================== */

export function Pulse({
  duration = 0.9,
  delay = 0,
  easing = 'linear',
  amplitude = 0.08,
  cycles = 2,
  style,
  className,
  children,
}: MotionProps & { amplitude?: number; cycles?: number }) {
  const p = useProgress(delay, duration, easing);
  const scale = 1 + amplitude * Math.sin(p * Math.PI * 2 * cycles) * (p < 1 ? 1 : 0);
  return (
    <div className={className} style={{ ...style, transform: `scale(${scale})` }}>
      {children}
    </div>
  );
}

export function Shake({ duration = 0.5, delay = 0, easing = 'linear', amplitude = 8, cycles = 5, style, className, children }: MotionProps & { amplitude?: number; cycles?: number }) {
  const p = useProgress(delay, duration, easing);
  const dx = p < 1 ? amplitude * Math.sin(p * Math.PI * 2 * cycles) * (1 - p) : 0;
  return (
    <div className={className} style={{ ...style, transform: `translateX(${dx}px)` }}>
      {children}
    </div>
  );
}

export function Blink({ duration = 0.8, delay = 0, easing = 'linear', cycles = 3, min = 0.25, style, className, children }: MotionProps & { cycles?: number; min?: number }) {
  const p = useProgress(delay, duration, easing);
  const opacity = p < 1 ? 1 - (1 - min) * (0.5 + 0.5 * Math.sin(p * Math.PI * 2 * cycles)) : 1;
  return (
    <div className={className} style={{ ...style, opacity }}>
      {children}
    </div>
  );
}

/** 高亮扫过：一道光带从左到右掠过文本。 */
export function HighlightSweep({
  duration = 0.9,
  delay = 0,
  easing = 'easeInOut',
  color = 'rgba(34,211,238,.45)',
  style,
  className,
  children,
}: MotionProps & { color?: string }) {
  const p = useProgress(delay, duration, easing);
  return (
    <span className={className} style={{ ...style, position: 'relative', display: 'inline-block' }}>
      {children}
      <i
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          borderRadius: 6,
          background: `linear-gradient(100deg, transparent 20%, ${color} 50%, transparent 80%)`,
          transform: `translateX(${(p - 0.5) * 240}%)`,
          opacity: p >= 1 ? 0 : 1,
          pointerEvents: 'none',
        }}
      />
    </span>
  );
}

export function ColorShift({
  duration = 0.8,
  delay = 0,
  easing = 'easeInOut',
  from = '#e7ecf5',
  to = '#22d3ee',
  style,
  className,
  children,
}: MotionProps & { from?: string; to?: string }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, color: interp(from, to, p) as string }}>
      {children}
    </div>
  );
}

/** 弹跳：元素从上方落下并弹跳稳定（easeOutBounce）。 */
export function Bounce({ duration = 0.9, delay = 0, easing = 'easeOutBounce', distance = 120, style, className, children }: MotionProps & { distance?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, transform: `translateY(${(1 - p) * -distance}px)` }}>
      {children}
    </div>
  );
}

/* ============================== 出场（Exit） ============================== */

export function FadeOut({ duration = 0.6, delay = 0, easing = 'easeIn', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: 1 - p }}>
      {children}
    </div>
  );
}

export function SlideOut({
  duration = 0.6,
  delay = 0,
  easing = 'easeIn',
  direction = 'up',
  distance = 36,
  style,
  className,
  children,
}: MotionProps & { direction?: 'left' | 'right' | 'up' | 'down'; distance?: number }) {
  const p = useProgress(delay, duration, easing);
  const dx = (direction === 'left' ? -1 : direction === 'right' ? 1 : 0) * distance * p;
  const dy = (direction === 'up' ? -1 : direction === 'down' ? 1 : 0) * distance * p;
  return (
    <div className={className} style={{ ...style, opacity: 1 - p, transform: `translate(${dx}px, ${dy}px)` }}>
      {children}
    </div>
  );
}

export function ScaleOut({ duration = 0.6, delay = 0, easing = 'easeIn', to = 0.55, style, className, children }: MotionProps & { to?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: 1 - p, transform: `scale(${1 + (to - 1) * p})` }}>
      {children}
    </div>
  );
}

export function BlurOut({ duration = 0.6, delay = 0, easing = 'easeIn', blur = 12, style, className, children }: MotionProps & { blur?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: 1 - p, filter: `blur(${blur * p}px)` }}>
      {children}
    </div>
  );
}

/** 遮罩隐藏：与 MaskReveal 相反，擦除式消失。 */
export function MaskHide({
  duration = 0.7,
  delay = 0,
  easing = 'easeInOut',
  direction = 'rtl',
  style,
  className,
  children,
}: MotionProps & { direction?: 'ltr' | 'rtl' | 'ttb' | 'btt' }) {
  const p = useProgress(delay, duration, easing);
  const inset =
    direction === 'ltr'
      ? `inset(0 ${p * 100}% 0 0)`
      : direction === 'rtl'
        ? `inset(0 0 0 ${p * 100}%)`
        : direction === 'ttb'
          ? `inset(0 0 ${p * 100}% 0)`
          : `inset(${p * 100}% 0 0 0)`;
  return (
    <div className={className} style={{ ...style, clipPath: inset }}>
      {children}
    </div>
  );
}

/* ============================== 转场（Transition，场景级进场） ============================== */

export function CrossFade({ duration = 0.6, delay = 0, easing = 'easeOut', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: p, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}

export function Push({ duration = 0.6, delay = 0, easing = 'easeOut', direction = 'left', style, className, children }: MotionProps & { direction?: 'left' | 'right' | 'up' | 'down' }) {
  const p = useProgress(delay, duration, easing);
  const dx = (direction === 'left' ? 1 : direction === 'right' ? -1 : 0) * (1 - p) * 100;
  const dy = (direction === 'up' ? 1 : direction === 'down' ? -1 : 0) * (1 - p) * 100;
  return (
    <div className={className} style={{ ...style, transform: `translate(${dx}%, ${dy}%)`, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}

export function Wipe({ duration = 0.6, delay = 0, easing = 'easeInOut', direction = 'ltr', style, className, children }: MotionProps & { direction?: 'ltr' | 'rtl' | 'ttb' | 'btt' }) {
  const p = useProgress(delay, duration, easing);
  const inset =
    direction === 'ltr'
      ? `inset(0 ${(1 - p) * 100}% 0 0)`
      : direction === 'rtl'
        ? `inset(0 0 0 ${(1 - p) * 100}%)`
        : direction === 'ttb'
          ? `inset(0 0 ${(1 - p) * 100}% 0)`
          : `inset(${(1 - p) * 100}% 0 0 0)`;
  return (
    <div className={className} style={{ ...style, clipPath: inset, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}

export function Zoom({ duration = 0.6, delay = 0, easing = 'easeOut', from = 0.82, style, className, children }: MotionProps & { from?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: p, transform: `scale(${from + (1 - from) * p})`, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}

export function BlurDissolve({ duration = 0.7, delay = 0, easing = 'easeOut', blur = 14, style, className, children }: MotionProps & { blur?: number }) {
  const p = useProgress(delay, duration, easing);
  return (
    <div className={className} style={{ ...style, opacity: p, filter: `blur(${blur * (1 - p)}px)`, width: '100%', height: '100%' }}>
      {children}
    </div>
  );
}

/** 故障感：色差 + 抖动 + 斜切，播完回到常态。 */
export function Glitch({ duration = 0.6, delay = 0, easing = 'linear', style, className, children }: MotionProps) {
  const p = useProgress(delay, duration, easing);
  const live = p < 1 ? 1 - p : 0;
  const dx = live > 0 ? Math.sin(p * 47) * 6 * live : 0;
  const skew = live > 0 ? Math.sin(p * 23) * 3 * live : 0;
  return (
    <div
      className={className}
      style={{
        ...style,
        width: '100%',
        height: '100%',
        transform: `translateX(${dx}px) skewX(${skew}deg)`,
        textShadow: live > 0 ? `${2 * live + 1}px 0 rgba(251,113,133,.85), ${-2 * live - 1}px 0 rgba(96,165,250,.85)` : undefined,
      }}
    >
      {children}
    </div>
  );
}

/* ============================== 预设清单（供工场界面枚举） ============================== */

export type PresetGroup = '入场' | '强调' | '出场' | '转场';

export interface PresetMeta {
  name: string;
  label: string;
  group: PresetGroup;
  hint: string;
}

export const PRESET_META: PresetMeta[] = [
  { name: 'FadeIn', label: '淡入', group: '入场', hint: '通用' },
  { name: 'SlideIn', label: '滑入', group: '入场', hint: '标题、卡片' },
  { name: 'ScaleIn', label: '缩放入场', group: '入场', hint: '强调' },
  { name: 'RotateIn', label: '旋转入场', group: '入场', hint: '徽章' },
  { name: 'BlurIn', label: '模糊到清晰', group: '入场', hint: '转场前后' },
  { name: 'Typewriter', label: '打字机', group: '入场', hint: '口播字幕' },
  { name: 'CharStagger', label: '逐字弹出', group: '入场', hint: '关键词' },
  { name: 'UnderlineReveal', label: '下划线划入', group: '入场', hint: '标题强调' },
  { name: 'StrokeDraw', label: '描边绘制', group: '入场', hint: 'SVG 路径' },
  { name: 'MaskReveal', label: '遮罩揭示', group: '入场', hint: '图片、色块' },
  { name: 'Pulse', label: '脉冲', group: '强调', hint: 'CTA' },
  { name: 'Shake', label: '抖动', group: '强调', hint: '警告、惊喜' },
  { name: 'Blink', label: '闪烁', group: '强调', hint: '提示' },
  { name: 'HighlightSweep', label: '高亮扫过', group: '强调', hint: '关键词' },
  { name: 'ColorShift', label: '颜色切换', group: '强调', hint: '状态变化' },
  { name: 'Bounce', label: '弹跳', group: '强调', hint: '活泼元素' },
  { name: 'FadeOut', label: '淡出', group: '出场', hint: '通用' },
  { name: 'SlideOut', label: '滑出', group: '出场', hint: '通用' },
  { name: 'ScaleOut', label: '缩小消失', group: '出场', hint: '通用' },
  { name: 'BlurOut', label: '模糊消失', group: '出场', hint: '通用' },
  { name: 'MaskHide', label: '遮罩隐藏', group: '出场', hint: '擦除式消失' },
  { name: 'Glitch', label: '故障转场', group: '转场', hint: '潮酷切换' },
];

export const PRESET_MAP: Record<string, (props: MotionProps & Record<string, unknown>) => ReactNode> = {
  FadeIn,
  SlideIn: SlideIn as never,
  ScaleIn: ScaleIn as never,
  RotateIn: RotateIn as never,
  BlurIn: BlurIn as never,
  Typewriter: Typewriter as never,
  CharStagger: CharStagger as never,
  UnderlineReveal: UnderlineReveal as never,
  StrokeDraw: StrokeDraw as never,
  MaskReveal: MaskReveal as never,
  Pulse: Pulse as never,
  Shake: Shake as never,
  Blink: Blink as never,
  HighlightSweep: HighlightSweep as never,
  ColorShift: ColorShift as never,
  Bounce: Bounce as never,
  FadeOut,
  SlideOut: SlideOut as never,
  ScaleOut: ScaleOut as never,
  BlurOut: BlurOut as never,
  MaskHide: MaskHide as never,
  CrossFade,
  Push: Push as never,
  Wipe: Wipe as never,
  Zoom: Zoom as never,
  BlurDissolve: BlurDissolve as never,
  Glitch,
};
