/**
 * 时间轴引擎 —— 播放时钟、时序容器与进度 hook。
 *
 * 设计原则（Remotion-ready）：所有动效都是「当前时间 → 渲染状态」的纯函数，
 * 引擎只负责推进播放头 t；seek / 倍速 / 循环 / 逐帧渲染都是免费的。
 * 时长单位一律用**秒**（迁移 Remotion 时乘以 fps 即为帧）。
 */
import {
  cloneElement,
  createContext,
  useContext,
  useEffect,
  isValidElement,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { resolveEasing, type Easing } from './easing.ts';

/* ------------------------------ 播放时钟 ------------------------------ */

export interface PlayerClock {
  /** 播放头（秒）。 */
  t: number;
  playing: boolean;
  ended: boolean;
  speed: number;
  play: () => void;
  pause: () => void;
  toggle: () => void;
  seek: (seconds: number) => void;
  setSpeed: (value: number) => void;
  /** 完整重置：播放头归零、场景重建、回到未开始状态。 */
  reset: () => void;
}

/**
 * 播放时钟 hook：rAF 推进 t，到达 total 时停住并置 ended（提供重播/重置）。
 * `generation` 在 reset 时自增，调用方可把它当 React key 强制重建场景，
 * 确保重播时所有动效从头播放（这就是「播完后重置状态」的正确姿势）。
 */
export function usePlayerClock(total: number, loop = false): PlayerClock & { generation: number } {
  const [t, setT] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [ended, setEnded] = useState(false);
  const [speed, setSpeedState] = useState(1);
  const [generation, setGeneration] = useState(0);
  const state = useRef({ t: 0, playing: false, ended: false, speed: 1, total, loop });
  state.current = { ...state.current, total, loop };

  useEffect(() => {
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      const dt = (now - last) / 1000;
      last = now;
      const s = state.current;
      if (s.playing) {
        let next = s.t + dt * s.speed;
        if (next >= s.total - 1e-3) {
          if (s.loop) {
            next = next % s.total;
          } else {
            next = s.total - 1e-3;
            s.playing = false;
            s.ended = true;
            setPlaying(false);
            setEnded(true);
          }
        }
        s.t = next;
        setT(next);
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  const clock = useMemo<Omit<PlayerClock, 'reset'> & { generation: number }>(() => {
    const sync = (patch: Partial<typeof state.current>) => Object.assign(state.current, patch);
    return {
      generation,
      t,
      playing,
      ended,
      speed,
      play: () => {
        if (state.current.ended) {
          // 播完后再播放：从头开始（重置 ended 与播放头，并重建场景）
          sync({ t: 0, playing: true, ended: false });
          setT(0);
          setEnded(false);
          setGeneration((g) => g + 1);
          setPlaying(true);
          return;
        }
        sync({ playing: true });
        setPlaying(true);
      },
      pause: () => {
        sync({ playing: false });
        setPlaying(false);
      },
      toggle: () => (state.current.playing ? clock.pause() : clock.play()),
      seek: (seconds: number) => {
        const next = Math.min(state.current.total - 1e-3, Math.max(0, seconds));
        sync({ t: next, ended: false });
        setT(next);
        setEnded(false);
      },
      setSpeed: (value: number) => {
        sync({ speed: value });
        setSpeedState(value);
      },
    };
  }, [t, playing, ended, speed, generation]);

  const reset = useMemo(
    () => () => {
      Object.assign(state.current, { t: 0, playing: false, ended: false });
      setT(0);
      setPlaying(false);
      setEnded(false);
      setGeneration((g) => g + 1); // key 递增 → 场景整体 remount，动效从头播
    },
    [],
  );

  return { ...clock, toggle: () => (state.current.playing ? clock.pause() : clock.play()), reset };
}

/* ------------------------------ 局部时间 ------------------------------ */

const LocalTimeContext = createContext<number>(0);

/** 当前局部时间（秒）：位于 <Sequence from={s}> 内时返回 t - s，否则返回全局播放头。 */
export function useLocalTime(): number {
  return useContext(LocalTimeContext);
}

/**
 * 动效进度 hook：把局部时间换算成 0..1 的缓动后进度。
 * fill 语义与 CSS 一致——`both`：开始前保持起点状态，结束后保持终点状态。
 */
export function useProgress(
  start: number,
  duration: number,
  easing: Easing = 'easeOut',
  fill: 'both' | 'none' = 'both',
): number {
  const local = useLocalTime();
  const p = (local - start) / Math.max(duration, 1e-6);
  if (p <= 0) return fill === 'both' ? 0 : -1;
  if (p >= 1) return fill === 'both' ? 1 : -1;
  return resolveEasing(easing)(p);
}

/* ------------------------------ 时序容器 ------------------------------ */

export interface SequenceProps {
  /** 相对父时间轴的起始秒。 */
  from: number;
  /** 持续秒数；到点后子元素卸载（相当于出场即消失）。 */
  duration: number;
  /** 场景切换淡入淡出的秒数（0 = 直接切换）。 */
  fade?: number;
  children: ReactNode;
}

/**
 * 时间轴片段：仅在 [from, from+duration) 区间内渲染子元素。
 * 嵌套安全——子元素里的 useLocalTime() 拿到的是减去 from 的局部时间。
 */
export function Sequence({ from, duration, fade = 0, children }: SequenceProps) {
  const local = useLocalTime();
  const inner = local - from;
  if (inner < 0 || inner >= duration) return null;
  const opacity = fade > 0 ? Math.min(1, inner / fade, (duration - inner) / fade) : 1;
  return (
    <LocalTimeContext.Provider value={inner}>
      <div style={fade > 0 ? { opacity, width: '100%', height: '100%' } : undefined}>{children}</div>
    </LocalTimeContext.Provider>
  );
}

/** 并行容器：所有子片段共享同一条父时间轴（语义标注用，行为等同 Fragment）。 */
export function Parallel({ children }: { children: ReactNode }) {
  return <>{children}</>;
}

/**
 * 错峰容器：给每个直接子元素（动效组件）的 delay 注入 i*gap。
 * 子元素必须是接受 delay 的动效组件（FadeIn / SlideIn / ScaleIn …）；
 * 子元素已显式给 delay 时保持原值。
 */
export function Stagger({ gap = 0.12, children }: { gap?: number; children: ReactNode }) {
  const items = Array.isArray(children) ? children : [children];
  return (
    <>
      {items.map((child, i) => {
        if (!isValidElement(child)) return child;
        const props = child.props as { delay?: number };
        return cloneElement(child, {
          ...props,
          delay: props.delay ?? i * gap,
        } as Partial<unknown>);
      })}
    </>
  );
}
