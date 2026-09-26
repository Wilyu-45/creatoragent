/**
 * 视频时间轴 —— 字幕轨 → 可渲染时间轴的「大脑」。
 *
 * 职责：智能分幕、动效/转场/调色分配（去 AI 味随机化）、卡点吸附（±0.1s → 0 偏差）、
 * 关键词强调、AI 味词 lint。产出 VgTimeline 同时驱动 DOM 预览（stage.tsx）
 * 与 Canvas 导出（canvas.ts）——两套渲染器消费同一份参数，保证所见即所得。
 */
import type { Cue } from './subtitles';

/* ------------------------------ 随机源（可复现） ------------------------------ */

export function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/* ------------------------------ 主题（背景库） ------------------------------ */

export type ThemeId = 'aurora' | 'ocean' | 'ember' | 'sunset' | 'matcha' | 'mono';

export interface Glow {
  x: number;
  y: number;
  r: number;
  color: string;
}

/** 一张背景：CSS 角度线性渐变 + 一团径向辉光（DOM 与 Canvas 各自绘制同参数）。 */
export interface BgSpec {
  angle: number;
  stops: [string, string, string];
  glow: Glow;
}

interface Theme {
  label: string;
  accent: string;
  bgs: BgSpec[];
}

export const THEMES: Record<ThemeId, Theme> = {
  aurora: {
    label: '极夜极光',
    accent: '#22d3ee',
    bgs: [
      { angle: 165, stops: ['#101a36', '#1a2c5e', '#0a0f1e'], glow: { x: 24, y: 12, r: 720, color: 'rgba(34,211,238,0.34)' } },
      { angle: 200, stops: ['#121531', '#241b58', '#0a0d1c'], glow: { x: 78, y: 20, r: 660, color: 'rgba(129,140,248,0.36)' } },
      { angle: 150, stops: ['#0c1a2e', '#14405c', '#0a0f1e'], glow: { x: 62, y: 82, r: 740, color: 'rgba(45,212,191,0.30)' } },
      { angle: 185, stops: ['#0f1734', '#202a58', '#090c18'], glow: { x: 40, y: 30, r: 620, color: 'rgba(96,165,250,0.32)' } },
    ],
  },
  ocean: {
    label: '深海',
    accent: '#38bdf8',
    bgs: [
      { angle: 170, stops: ['#0a1a2c', '#123a58', '#050d16'], glow: { x: 30, y: 18, r: 700, color: 'rgba(56,189,248,0.32)' } },
      { angle: 210, stops: ['#0b2033', '#175066', '#050d16'], glow: { x: 72, y: 76, r: 680, color: 'rgba(20,184,166,0.30)' } },
      { angle: 155, stops: ['#0c2036', '#1a4a6a', '#050d16'], glow: { x: 50, y: 10, r: 740, color: 'rgba(103,232,249,0.28)' } },
      { angle: 190, stops: ['#091a2e', '#123c62', '#050c15'], glow: { x: 82, y: 40, r: 620, color: 'rgba(59,130,246,0.32)' } },
    ],
  },
  ember: {
    label: '余烬',
    accent: '#fb923c',
    bgs: [
      { angle: 165, stops: ['#241114', '#45202a', '#0e0709'], glow: { x: 26, y: 20, r: 700, color: 'rgba(251,146,60,0.34)' } },
      { angle: 200, stops: ['#201114', '#4e2330', '#0d0708'], glow: { x: 74, y: 78, r: 660, color: 'rgba(244,114,182,0.28)' } },
      { angle: 150, stops: ['#22141a', '#3f2130', '#0d0708'], glow: { x: 58, y: 12, r: 720, color: 'rgba(248,113,113,0.30)' } },
      { angle: 185, stops: ['#231219', '#46263c', '#0e0709'], glow: { x: 42, y: 66, r: 640, color: 'rgba(253,186,116,0.30)' } },
    ],
  },
  sunset: {
    label: '暮紫',
    accent: '#f472b6',
    bgs: [
      { angle: 165, stops: ['#211530', '#442355', '#0d0916'], glow: { x: 30, y: 16, r: 720, color: 'rgba(244,114,182,0.32)' } },
      { angle: 200, stops: ['#1d1536', '#3a2566', '#0c0a18'], glow: { x: 76, y: 74, r: 680, color: 'rgba(167,139,250,0.34)' } },
      { angle: 150, stops: ['#251830', '#4c2c4e', '#0e0a17'], glow: { x: 54, y: 86, r: 700, color: 'rgba(251,191,36,0.22)' } },
      { angle: 185, stops: ['#1f162f', '#3c2758', '#0d0917'], glow: { x: 44, y: 28, r: 640, color: 'rgba(248,113,113,0.28)' } },
    ],
  },
  matcha: {
    label: '抹茶夜',
    accent: '#a3e635',
    bgs: [
      { angle: 165, stops: ['#152216', '#284528', '#080e09'], glow: { x: 28, y: 18, r: 700, color: 'rgba(163,230,53,0.26)' } },
      { angle: 200, stops: ['#142019', '#2e5030', '#080e09'], glow: { x: 74, y: 78, r: 660, color: 'rgba(52,211,153,0.28)' } },
      { angle: 150, stops: ['#172617', '#335430', '#080e09'], glow: { x: 58, y: 12, r: 720, color: 'rgba(190,242,100,0.24)' } },
      { angle: 185, stops: ['#152216', '#274a28', '#080e09'], glow: { x: 40, y: 62, r: 640, color: 'rgba(45,212,191,0.26)' } },
    ],
  },
  mono: {
    label: '墨黑',
    accent: '#e5e7eb',
    bgs: [
      { angle: 165, stops: ['#16191f', '#262b36', '#08090c'], glow: { x: 30, y: 18, r: 700, color: 'rgba(229,231,235,0.16)' } },
      { angle: 200, stops: ['#15181e', '#222733', '#08090c'], glow: { x: 72, y: 76, r: 660, color: 'rgba(148,163,184,0.20)' } },
      { angle: 150, stops: ['#181b21', '#2a2f3c', '#08090c'], glow: { x: 56, y: 12, r: 720, color: 'rgba(203,213,225,0.15)' } },
      { angle: 185, stops: ['#15181d', '#242a38', '#08090c'], glow: { x: 44, y: 64, r: 640, color: 'rgba(226,232,240,0.16)' } },
    ],
  },
};

/** 背景对应的 CSS background 值（DOM 预览用）。 */
export function bgCss(spec: BgSpec): string {
  return `radial-gradient(${spec.glow.r}px at ${spec.glow.x}% ${spec.glow.y}%, ${spec.glow.color} 0%, transparent 68%), linear-gradient(${spec.angle}deg, ${spec.stops[0]} 0%, ${spec.stops[1]} 52%, ${spec.stops[2]} 100%)`;
}

/* ------------------------------ 剪辑操作枚举 ------------------------------ */

export type FilterId = 'none' | 'warm' | 'cool' | 'mono' | 'vintage' | 'punch' | 'soft';

export const FILTER_CSS: Record<FilterId, string> = {
  none: 'none',
  warm: 'saturate(1.12) sepia(0.14) hue-rotate(-8deg) brightness(1.03)',
  cool: 'saturate(0.92) hue-rotate(10deg) brightness(1.02)',
  mono: 'grayscale(1) contrast(1.12)',
  vintage: 'sepia(0.42) contrast(0.94) saturate(0.85) brightness(1.04)',
  punch: 'contrast(1.22) saturate(1.25)',
  soft: 'blur(0.6px) brightness(1.06) saturate(0.95)',
};

export const FILTER_LABEL: Record<FilterId, string> = {
  none: '原色',
  warm: '暖调',
  cool: '冷调',
  mono: '黑白',
  vintage: '复古胶片',
  punch: '高对比',
  soft: '柔光',
};

export type TransitionId = 'crossfade' | 'push' | 'wipe' | 'zoom' | 'blur' | 'glitch';

export const TRANSITION_LABEL: Record<TransitionId, string> = {
  crossfade: '交叉溶解',
  push: '推移',
  wipe: '擦除',
  zoom: '缩放',
  blur: '模糊溶解',
  glitch: '故障',
};

export type CueFx = 'fade' | 'slide' | 'pop' | 'type' | 'mask' | 'wave';

export const CUE_FX_LABEL: Record<CueFx, string> = {
  fade: '淡入',
  slide: '上滑',
  pop: '弹出',
  type: '打字机',
  mask: '擦除揭示',
  wave: '波浪',
};

/* ------------------------------ 时间轴 schema ------------------------------ */

export interface KenBurns {
  dir: 'in' | 'out' | 'left' | 'right';
  amount: number;
}

export interface VgScene {
  index: number;
  from: number;
  duration: number;
  bg: BgSpec;
  kenburns: KenBurns;
  transition: TransitionId;
  transitionDuration: number;
  filter: FilterId;
  seed: number;
  cueCount: number;
}

/** 字符区间（按 Array.from 字符序）。 */
export interface EmphasisSpan {
  start: number;
  end: number;
}

export interface VgCue {
  start: number;
  end: number;
  text: string;
  fx: CueFx;
  /** 入场抖动延迟（去 AI 味）。 */
  delay: number;
  scene: number;
  emphasis: EmphasisSpan[];
}

export interface LintIssue {
  cue: number;
  word: string;
  hint: string;
}

export interface VgTimeline {
  duration: number;
  scenes: VgScene[];
  cues: VgCue[];
  beats: number[];
  title: string;
  watermark: string;
  theme: ThemeId;
  accent: string;
  grain: number;
  vignette: number;
  showTitles: boolean;
  beatPulse: boolean;
  titleCard: boolean;
  lint: LintIssue[];
  /** 实际启用的剪辑操作清单（供 UI 如实展示）。 */
  ops: string[];
}

export interface BuildOptions {
  title?: string;
  watermark?: string;
  theme: ThemeId;
  filter: FilterId | 'auto';
  transition: TransitionId | 'auto';
  cueFx: CueFx | 'auto';
  highlight: boolean;
  showTitles: boolean;
  titleCard: boolean;
  /** 去 AI 味随机化：入场抖动 + 动效池轮换。 */
  humanize: boolean;
  beatPulse: boolean;
  beats?: number[];
  grain: number;
  vignette: number;
  seed?: number;
}

/* ------------------------------ 关键词强调 ------------------------------ */

const QUOTE_PAIRS: [string, string][] = [
  ['「', '」'],
  ['『', '』'],
  ['“', '”'],
  ['《', '》'],
];

/** 从文本提取强调区间：「引号内容」+ 数字/百分比/倍数。 */
export function findEmphasis(text: string): EmphasisSpan[] {
  const chars = Array.from(text);
  const spans: EmphasisSpan[] = [];
  for (const [open, close] of QUOTE_PAIRS) {
    let depth = -1;
    for (let i = 0; i < chars.length; i += 1) {
      if (chars[i] === open && depth < 0) depth = i;
      else if (chars[i] === close && depth >= 0) {
        if (i - depth > 1) spans.push({ start: depth, end: i + 1 });
        depth = -1;
      }
    }
  }
  const numRe = /\d+(?:\.\d+)?[%％倍]?/g;
  let m: RegExpExecArray | null;
  while ((m = numRe.exec(text)) !== null) {
    const start = Array.from(text.slice(0, m.index)).length;
    spans.push({ start, end: start + Array.from(m[0]).length });
  }
  return spans;
}

/** 切分文本为普通/强调段（供 DOM 与 Canvas 共用）。 */
export function splitEmphasis(
  text: string,
  spans: EmphasisSpan[],
): { text: string; emph: boolean }[] {
  const chars = Array.from(text);
  const emphFlags = chars.map((_, i) => spans.some((s) => i >= s.start && i < s.end));
  const segs: { text: string; emph: boolean }[] = [];
  for (let i = 0; i < chars.length; i += 1) {
    if (segs.length > 0 && segs[segs.length - 1].emph === emphFlags[i]) {
      segs[segs.length - 1].text += chars[i];
    } else {
      segs.push({ text: chars[i], emph: emphFlags[i] });
    }
  }
  return segs;
}

/* ------------------------------ AI 味 lint ------------------------------ */

const AI_FLAVOR: [RegExp, string][] = [
  [/(首先|其次|再者|另外)[，,、]/, '排序词开头显生硬，可直接进入观点'],
  [/(总而言之|综上所述|总的来说|总之)[，,]?$/, '总结套话，删掉更利落'],
  [/(值得注意的是|需要指出的是|值得一提的是|不得不说)/, '提示语套话'],
  [/(不可否认|毋庸置疑|众所周知)/, '断言套话，容易显得空'],
  [/随着.{0,12}的(发展|进步|普及)/, 'AI 高频开头，建议具象化改写'],
  [/在这个.{0,10}(时代|社会)/, 'AI 高频开头，建议落到具体场景'],
  [/(赋能|闭环|抓手|颗粒度|底层逻辑)/, '互联网黑话，口播易出戏'],
  [/!!{2,}|！{3,}/, '感叹号堆叠，情绪外露'],
  [/(深深地|强烈地|非常非常)/, '程度副词堆砌'],
];

function lintCues(cues: Cue[]): LintIssue[] {
  const issues: LintIssue[] = [];
  cues.forEach((cue, index) => {
    for (const [re, hint] of AI_FLAVOR) {
      const m = re.exec(cue.text);
      if (m) issues.push({ cue: index, word: m[0], hint });
    }
  });
  return issues;
}

/* ------------------------------ 建轴 ------------------------------ */

const SCENE_GAP = 1.5; // 静默间隔 > 1.5s 视为换幕
const SCENE_MAX = 9.5;
const SCENE_MIN = 2.2;
const BEAT_SNAP = 0.45; // 场景边界向最近的节拍吸附（吸附后偏差为 0）

function nearestBeat(beats: number[], t: number): number | null {
  let best: number | null = null;
  let bestDist = Infinity;
  for (const b of beats) {
    const d = Math.abs(b - t);
    if (d < bestDist) {
      bestDist = d;
      best = b;
    }
  }
  return bestDist <= BEAT_SNAP ? best : null;
}

function pickAvoiding<T>(pool: T[], prev: T | null, rng: () => number): T {
  if (pool.length === 1) return pool[0];
  const candidates = prev == null ? pool : pool.filter((v) => v !== prev);
  return candidates[Math.floor(rng() * candidates.length) % candidates.length];
}

const TRANSITION_POOL: TransitionId[] = ['crossfade', 'push', 'wipe', 'zoom', 'blur', 'glitch'];
const FILTER_AUTO_POOL: FilterId[] = ['none', 'none', 'warm', 'cool', 'none', 'punch', 'vintage', 'soft'];
const CUE_FX_POOL: CueFx[] = ['fade', 'slide', 'pop', 'type', 'mask', 'wave'];

const TRANSITION_DURATION: Record<TransitionId, number> = {
  crossfade: 0.6,
  push: 0.55,
  wipe: 0.55,
  zoom: 0.5,
  blur: 0.6,
  glitch: 0.45,
};

/** 从字幕自动构建完整时间轴。同 seed + 同输入 → 结果可复现。 */
export function buildTimeline(cues: Cue[], opts: BuildOptions): VgTimeline {
  const seed = opts.seed ?? Math.floor(Math.random() * 1e9);
  const rng = mulberry32(seed);
  const theme = THEMES[opts.theme];
  const beats = [...(opts.beats ?? [])].sort((a, b) => a - b);
  const jitter = () => (opts.humanize ? (rng() - 0.5) * 0.08 : 0);

  /* ---- 1. 分幕 ---- */
  const groups: Cue[][] = [];
  for (const cue of cues) {
    const last = groups[groups.length - 1];
    if (!last) {
      groups.push([cue]);
      continue;
    }
    const groupStart = last[0].start;
    const gap = cue.start - last[last.length - 1].end;
    if (gap > SCENE_GAP || cue.start - groupStart > SCENE_MAX) groups.push([cue]);
    else last.push(cue);
  }
  // 过短的场景并入前一幕
  for (let i = groups.length - 1; i > 0; i -= 1) {
    const span = groups[i][groups[i].length - 1].end - groups[i][0].start;
    if (span < SCENE_MIN && groups[i - 1].length < 6) {
      groups[i - 1].push(...groups[i]);
      groups.splice(i, 1);
    }
  }

  /* ---- 2. 场景时间与分配 ---- */
  const scenes: VgScene[] = [];
  let prevBg: BgSpec | null = null;
  let prevKb: KenBurns | null = null;
  let prevTr: TransitionId | null = null;
  let prevFilter: FilterId | null = null;
  for (let i = 0; i < groups.length; i += 1) {
    const group = groups[i];
    let from = i === 0 ? Math.max(0, group[0].start - 0.35) : group[0].start - 0.3;
    if (i > 0) from = Math.max(from, scenes[i - 1].from + 0.5);
    const snap = nearestBeat(beats, from);
    if (snap != null) from = snap;
    const nextFrom =
      i + 1 < groups.length
        ? Math.max(group[group.length - 1].end + 0.4, groups[i + 1][0].start - 0.3)
        : group[group.length - 1].end + 1.1;
    const nextSnap = i + 1 < groups.length ? nearestBeat(beats, nextFrom) : null;
    const duration = Math.max(1.2, (nextSnap ?? nextFrom) - from);

    const bg: BgSpec = pickAvoiding<BgSpec>(theme.bgs, prevBg, rng);
    const dir = pickAvoiding<KenBurns['dir']>(['in', 'out', 'left', 'right'], prevKb?.dir ?? null, rng);
    const kenburns: KenBurns = { dir, amount: 0.05 + rng() * 0.04 };
    const transition: TransitionId =
      opts.transition === 'auto' ? pickAvoiding<TransitionId>(TRANSITION_POOL, prevTr, rng) : opts.transition;
    const filter: FilterId =
      opts.filter === 'auto' ? pickAvoiding<FilterId>(FILTER_AUTO_POOL, prevFilter, rng) : opts.filter;
    prevBg = bg;
    prevKb = kenburns;
    prevTr = transition;
    prevFilter = filter;
    scenes.push({
      index: i,
      from: Number(from.toFixed(3)),
      duration: Number(duration.toFixed(3)),
      bg,
      kenburns,
      transition,
      transitionDuration: TRANSITION_DURATION[transition],
      filter,
      seed: Math.floor(rng() * 1e9),
      cueCount: group.length,
    });
  }

  /* ---- 3. 字幕轨分配 ---- */
  const cueList: VgCue[] = [];
  let prevFx: CueFx | null = null;
  let repeatRun = 0;
  cues.forEach((cue) => {
    const scene = scenes.find((s) => cue.start >= s.from && cue.start < s.from + s.duration) ?? scenes[scenes.length - 1];
    let fx: CueFx;
    if (opts.cueFx !== 'auto') {
      fx = opts.cueFx;
    } else {
      // 同一动效最多连续出现 2 次（去 AI 味：避免千篇一律）
      const pool = CUE_FX_POOL.filter((f) => !(repeatRun >= 2 && f === prevFx));
      fx = pool[Math.floor(rng() * pool.length) % pool.length];
      repeatRun = fx === prevFx ? repeatRun + 1 : 1;
      prevFx = fx;
    }
    cueList.push({
      start: cue.start,
      end: cue.end,
      text: cue.text,
      fx,
      delay: Number(jitter().toFixed(3)),
      scene: scene.index,
      emphasis: opts.highlight ? findEmphasis(cue.text) : [],
    });
  });

  /* ---- 4. 元信息 ---- */
  const firstText = cues[0]?.text ?? '';
  const autoTitle = firstText.split(/[。！？；：:，,]/)[0] ?? firstText;
  const title = (opts.title ?? '').trim() || Array.from(autoTitle).slice(0, 16).join('') || '未命名';

  /* ---- 5. 剪辑操作清单（如实统计） ---- */
  const ops: string[] = [];
  const usedTransitions = [...new Set(scenes.map((s) => s.transition))];
  const usedFilters = [...new Set(scenes.map((s) => s.filter).filter((f) => f !== 'none'))];
  ops.push(...usedTransitions.map((t) => `转场·${TRANSITION_LABEL[t]}`));
  if (scenes.some((s) => s.kenburns.amount > 0)) ops.push('Ken Burns 推拉');
  ops.push(...usedFilters.map((f) => `调色·${FILTER_LABEL[f]}`));
  const usedFx = [...new Set(cueList.map((c) => c.fx))];
  ops.push(...usedFx.map((f) => `字幕动效·${CUE_FX_LABEL[f]}`));
  if (cueList.some((c) => c.emphasis.length > 0)) ops.push('关键词高亮');
  if (beats.length > 0) ops.push('卡点切镜');
  if (opts.beatPulse && beats.length > 0) ops.push('节拍脉冲');
  if (opts.grain > 0) ops.push('胶片颗粒');
  if (opts.vignette > 0) ops.push('暗角');
  if ((opts.watermark ?? '').trim().length > 0) ops.push('水印角标');
  if (opts.titleCard) ops.push('标题卡');
  if (opts.showTitles) ops.push('幕别章标');
  ops.push('变速预览');

  return {
    duration: scenes.length > 0 ? scenes[scenes.length - 1].from + scenes[scenes.length - 1].duration : 0,
    scenes,
    cues: cueList,
    beats,
    title,
    watermark: (opts.watermark ?? '').trim(),
    theme: opts.theme,
    accent: theme.accent,
    grain: opts.grain,
    vignette: opts.vignette,
    showTitles: opts.showTitles,
    beatPulse: opts.beatPulse,
    titleCard: opts.titleCard,
    lint: lintCues(cues),
    ops,
  };
}
