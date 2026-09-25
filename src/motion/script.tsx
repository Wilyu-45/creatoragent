/**
 * JSON 动效脚本 —— 声明式时间轴编排的数据契约。
 *
 * 让非开发者用一份 JSON 就能编排一段「视频脚本」：每条 track 声明
 * 「用哪个预设、什么时候出现、停留多久」，由渲染器映射到 Sequence + 预设组件。
 * 该 schema 也是未来 video_script 产物 → 动效渲染的桥接格式。
 */
import type { CSSProperties } from 'react';
import type { Easing } from './easing.ts';
import { Sequence } from './engine.tsx';
import { PRESET_MAP } from './presets.tsx';

/** 一条动效轨道：在脚本时间轴 at 秒处，用预设播放一段动效并定格 hold 秒。 */
export interface ScriptTrack {
  /** 预设名（PRESET_MAP 的 key，如 FadeIn / Typewriter / Glitch）。 */
  preset: string;
  /** 起始秒（相对脚本时间轴）。 */
  at: number;
  /** 动画时长（秒），缺省用预设默认值。 */
  duration?: number;
  /** 动画播完后的定格秒数（超时后该轨道从画面消失）。 */
  hold?: number;
  /** 文本内容（作为组件 children）。 */
  text?: string;
  /** 缓动（预置名或 cubic-bezier 数组）。 */
  easing?: Easing;
  /** 片段进出淡入淡出秒数（Sequence fade）。 */
  fade?: number;
  /** 预设特有参数（如 SlideIn 的 direction、ColorShift 的 from/to）。 */
  params?: Record<string, unknown>;
  /** 内联样式（常用于 absolute 定位、字号、颜色）。 */
  style?: CSSProperties;
}

/** 一份完整动效脚本。 */
export interface MotionScript {
  /** 总时长（秒）。 */
  duration: number;
  /** 预览台背景（CSS background 值）。 */
  background?: string;
  tracks: ScriptTrack[];
}

/** 解析并校验 JSON 脚本文本；失败时给出人话错误。 */
export function parseMotionScript(text: string): { script?: MotionScript; error?: string } {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (e) {
    return { error: `JSON 语法错误：${(e as Error).message}` };
  }
  if (typeof raw !== 'object' || raw == null || Array.isArray(raw)) return { error: '脚本必须是 JSON 对象' };
  const obj = raw as Record<string, unknown>;
  const duration = Number(obj.duration);
  if (!Number.isFinite(duration) || duration <= 0) return { error: 'duration 必须是正数（总时长，秒）' };
  if (!Array.isArray(obj.tracks)) return { error: 'tracks 必须是数组' };
  const tracks: ScriptTrack[] = [];
  for (let i = 0; i < obj.tracks.length; i += 1) {
    const t = obj.tracks[i] as Record<string, unknown>;
    if (typeof t !== 'object' || t == null) return { error: `tracks[${i}] 不是对象` };
    if (typeof t.preset !== 'string' || !(t.preset in PRESET_MAP)) {
      return { error: `tracks[${i}].preset「${String(t.preset)}」不是有效预设名` };
    }
    const at = Number(t.at);
    if (!Number.isFinite(at) || at < 0) return { error: `tracks[${i}].at 必须是 ≥0 的秒数` };
    tracks.push({
      preset: t.preset,
      at,
      duration: t.duration == null ? undefined : Number(t.duration),
      hold: t.hold == null ? undefined : Number(t.hold),
      text: typeof t.text === 'string' ? t.text : undefined,
      easing: t.easing as Easing | undefined,
      fade: t.fade == null ? undefined : Number(t.fade),
      params: (typeof t.params === 'object' && t.params != null ? t.params : undefined) as Record<string, unknown> | undefined,
      style: (typeof t.style === 'object' && t.style != null ? t.style : undefined) as CSSProperties | undefined,
    });
  }
  return { script: { duration, background: typeof obj.background === 'string' ? obj.background : undefined, tracks } };
}

/**
 * 脚本渲染器：把 MotionScript 投影到 16:9 舞台上。
 * 外层请用 clock.generation 作 key 强制 remount，保证重播时全部从头演。
 */
export function MotionScriptStage({ script }: { script: MotionScript }) {
  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: '100%',
        overflow: 'hidden',
        background: script.background ?? 'radial-gradient(900px 540px at 50% -10%, #1b2b52 0%, #0a0f1e 62%)',
      }}
    >
      {script.tracks.map((track, i) => {
        const Preset = PRESET_MAP[track.preset];
        const anim = track.duration ?? 0.6;
        return (
          <Sequence key={i} from={track.at} duration={anim + (track.hold ?? 0)} fade={track.fade}>
            <Preset
              duration={track.duration}
              delay={0}
              easing={track.easing}
              style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: '4% 6%', textAlign: 'center', ...track.style }}
              {...track.params}
            >
              {track.text}
            </Preset>
          </Sequence>
        );
      })}
    </div>
  );
}

/** 内置示例：开场标题 → 打字机口播 → 转场抛问 → 关键词错峰 → 高亮收尾。 */
export const EXAMPLE_SCRIPT: MotionScript = {
  duration: 12,
  tracks: [
    {
      preset: 'ScaleIn',
      at: 0.3,
      duration: 0.7,
      hold: 3.0,
      text: '《玩乐关系》第四卷',
      style: { fontSize: 34, fontWeight: 800, letterSpacing: 2 },
      params: { from: 0.7 },
    },
    {
      preset: 'UnderlineReveal',
      at: 1.1,
      duration: 0.6,
      hold: 2.2,
      text: '桌游 · 阶级 · 反转',
      style: { fontSize: 16, color: '#a3aec4', marginTop: 70 },
      params: { color: '#22d3ee', thickness: 3 },
    },
    {
      preset: 'Typewriter',
      at: 4.2,
      duration: 2.2,
      hold: 1.4,
      text: '这一卷的关键词是「制度」——当规则本身成为武器，胜负就不再只看牌技。',
      style: { fontSize: 18, maxWidth: '72%', lineHeight: 1.8 },
    },
    {
      preset: 'Glitch',
      at: 7.9,
      duration: 0.6,
      hold: 3.5,
      text: '谁在规则之外？',
      style: { fontSize: 30, fontWeight: 800 },
    },
    {
      preset: 'CharStagger',
      at: 8.6,
      duration: 1.0,
      hold: 2.4,
      text: '伏笔回收 · 立场反转 · 终局',
      style: { fontSize: 15, color: '#a3aec4', marginTop: 70 },
    },
    {
      preset: 'HighlightSweep',
      at: 9.9,
      duration: 0.9,
      hold: 1.2,
      text: '尽在下回拆解',
      style: { fontSize: 20, fontWeight: 700, marginTop: 130 },
      params: { color: 'rgba(99,102,241,.5)' },
    },
  ],
};
