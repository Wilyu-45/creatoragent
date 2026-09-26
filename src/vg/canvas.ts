/**
 * Canvas 帧渲染器 —— 导出管线的画笔。
 *
 * draw(ctx, t, w, h) 是「时间 → 画面」的纯函数（除胶片颗粒的抖动外全部确定性），
 * 因此逐帧渲染天然支持 seek / 暂停 / 倍速与实时录制。参数与 DOM 预览台
 * （stage.tsx）同源：背景渐变、Ken Burns、六类转场、七档调色、六种字幕动效、
 * 关键词高亮、节拍脉冲、颗粒/暗角/水印。
 */
import { EASINGS } from '../motion/easing.ts';
import { FILTER_CSS, type VgCue, type VgScene, type VgTimeline } from './timeline.ts';

const FONT_STACK = "'PingFang SC', 'Microsoft YaHei', 'Noto Sans SC', sans-serif";
const TEXT_MAIN = '#f2f5fa';

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

export interface VgRenderer {
  draw(ctx: CanvasRenderingContext2D, t: number, w: number, h: number): void;
}

/* ------------------------------ 背景绘制 ------------------------------ */

/** 按 CSS 角度约定画线性渐变 + 辉光团。 */
function drawGradientBg(ctx: CanvasRenderingContext2D, scene: VgScene, w: number, h: number, kbP: number): void {
  const spec = scene.bg;
  const overscan = Math.max(w, h) * 0.1;
  ctx.save();
  // Ken Burns：推拉或横移（横移时保持恒定放大避免露边）
  const amt = scene.kenburns.amount;
  let scale = 1;
  let dx = 0;
  let dy = 0;
  if (scene.kenburns.dir === 'in') scale = 1 + amt * kbP;
  else if (scene.kenburns.dir === 'out') scale = 1 + amt * (1 - kbP);
  else {
    scale = 1 + amt * 0.8;
    dx = (scene.kenburns.dir === 'left' ? -1 : 1) * w * amt * 0.6 * kbP;
    dy = (scene.kenburns.dir === 'left' ? 1 : -1) * h * amt * 0.12 * kbP;
  }
  ctx.translate(w / 2 + dx, h / 2 + dy);
  ctx.scale(scale, scale);
  ctx.translate(-w / 2, -h / 2);

  const a = (spec.angle * Math.PI) / 180;
  const gdx = Math.sin(a);
  const gdy = -Math.cos(a);
  const len = Math.abs(w * gdx) + Math.abs(h * gdy);
  const grad = ctx.createLinearGradient(
    w / 2 - (gdx * len) / 2,
    h / 2 - (gdy * len) / 2,
    w / 2 + (gdx * len) / 2,
    h / 2 + (gdy * len) / 2,
  );
  grad.addColorStop(0, spec.stops[0]);
  grad.addColorStop(0.52, spec.stops[1]);
  grad.addColorStop(1, spec.stops[2]);
  ctx.fillStyle = grad;
  ctx.fillRect(-overscan, -overscan, w + overscan * 2, h + overscan * 2);

  const glow = ctx.createRadialGradient(
    (spec.glow.x / 100) * w,
    (spec.glow.y / 100) * h,
    0,
    (spec.glow.x / 100) * w,
    (spec.glow.y / 100) * h,
    spec.glow.r * scale,
  );
  glow.addColorStop(0, spec.glow.color);
  glow.addColorStop(1, 'rgba(0,0,0,0)');
  ctx.fillStyle = glow;
  ctx.fillRect(-overscan, -overscan, w + overscan * 2, h + overscan * 2);
  ctx.restore();
}

/* ------------------------------ 转场覆盖层 ------------------------------ */

function mulberry(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 画「前一幕背景」的离场覆盖层（进场转场的前半段）。 */
function drawTransition(ctx: CanvasRenderingContext2D, prev: VgScene, p: number, frame: number, accent: string, w: number, h: number): void {
  ctx.save();
  const prevFilter = FILTER_CSS[prev.filter] === 'none' ? '' : `${FILTER_CSS[prev.filter]} `;
  switch (prev.transition) {
    case 'crossfade':
      ctx.globalAlpha = 1 - p;
      drawGradientBg(ctx, prev, w, h, 1);
      break;
    case 'push':
      ctx.translate(-p * w, 0);
      drawGradientBg(ctx, prev, w, h, 1);
      break;
    case 'wipe': {
      ctx.beginPath();
      ctx.rect(p * w, 0, w - p * w, h);
      ctx.clip();
      drawGradientBg(ctx, prev, w, h, 1);
      ctx.restore();
      ctx.save();
      ctx.fillStyle = accent;
      ctx.globalAlpha = 1 - p;
      ctx.fillRect(p * w - 2, 0, 3, h);
      break;
    }
    case 'zoom': {
      ctx.globalAlpha = 1 - p;
      const s = 1 + 0.4 * p;
      ctx.translate(w / 2, h / 2);
      ctx.scale(s, s);
      ctx.translate(-w / 2, -h / 2);
      drawGradientBg(ctx, prev, w, h, 1);
      break;
    }
    case 'blur':
      ctx.globalAlpha = 1 - p;
      ctx.filter = `${prevFilter}blur(${(14 * p).toFixed(1)}px)`;
      drawGradientBg(ctx, prev, w, h, 1);
      break;
    case 'glitch': {
      const rng = mulberry(prev.seed + frame);
      const alpha = p < 0.65 ? 1 : (1 - p) / 0.35;
      const slices = 6;
      const sliceH = h / slices;
      for (let i = 0; i < slices; i += 1) {
        const off = (rng() - 0.5) * w * 0.06 * (1 - p);
        ctx.save();
        ctx.beginPath();
        ctx.rect(0, i * sliceH, w, sliceH);
        ctx.clip();
        ctx.translate(off, 0);
        ctx.globalAlpha = alpha * (0.65 + rng() * 0.35);
        drawGradientBg(ctx, prev, w, h, 1);
        ctx.restore();
      }
      // 色散条
      ctx.globalAlpha = alpha * 0.10;
      ctx.fillStyle = accent;
      for (let i = 0; i < 3; i += 1) {
        ctx.fillRect(0, rng() * h, w, 2 + rng() * 6);
      }
      break;
    }
  }
  ctx.restore();
}

/* ------------------------------ 字幕文本引擎 ------------------------------ */

/** 按字符贪心换行（CJK 逐字断行，Latin 优先在空格断），返回每行的字符下标区间。 */
function wrapLines(ctx: CanvasRenderingContext2D, chars: string[], maxWidth: number): [number, number][] {
  const lines: [number, number][] = [];
  let start = 0;
  let width = 0;
  let lastSpace = -1;
  for (let i = 0; i < chars.length; i += 1) {
    const cw = ctx.measureText(chars[i]).width;
    if (width + cw > maxWidth && i > start) {
      let end = i;
      if (lastSpace > start) {
        end = lastSpace + 1;
        i = lastSpace; // 回退到空格后
      }
      lines.push([start, end]);
      start = end;
      width = 0;
      lastSpace = -1;
      continue;
    }
    width += cw;
    if (chars[i] === ' ') lastSpace = i;
  }
  if (start < chars.length) lines.push([start, chars.length]);
  return lines;
}

/** 把一行字符区间切分为 普通/强调 段。 */
function lineSegments(
  chars: string[],
  spans: { start: number; end: number }[],
  from: number,
  to: number,
): { text: string; emph: boolean }[] {
  const segs: { text: string; emph: boolean }[] = [];
  for (let i = from; i < to; i += 1) {
    const emph = spans.some((s) => i >= s.start && i < s.end);
    const last = segs[segs.length - 1];
    if (last && last.emph === emph) last.text += chars[i];
    else segs.push({ text: chars[i], emph });
  }
  return segs;
}

const CUE_FX_EASING: Record<string, (x: number) => number> = {
  fade: EASINGS.easeOut,
  slide: EASINGS.easeOut,
  pop: EASINGS.easeOutBack,
  type: EASINGS.linear,
  mask: EASINGS.easeOut,
  wave: EASINGS.easeOut,
};

function drawCue(ctx: CanvasRenderingContext2D, cue: VgCue, t: number, accent: string, w: number, h: number): void {
  const dur = cue.end - cue.start;
  const local = t - cue.start;
  if (local < 0 || local > dur) return;
  const chars = Array.from(cue.text);
  if (chars.length === 0) return;

  const rawP = clamp01((local - cue.delay) / 0.45);
  const ease = CUE_FX_EASING[cue.fx] ?? EASINGS.easeOut;
  const p = ease(rawP);
  const outAlpha = clamp01((cue.end - t) / 0.35);
  if (rawP <= 0 || outAlpha <= 0) return;

  const fontSize = Math.round(h * 0.052);
  ctx.font = `700 ${fontSize}px ${FONT_STACK}`;
  ctx.textBaseline = 'alphabetic';

  // 打字机先截断可见字符
  const visible = cue.fx === 'type' ? chars.slice(0, Math.ceil(p * chars.length)) : chars;
  const maxWidth = w * 0.84;
  const lines = wrapLines(ctx, visible, maxWidth);
  const lineHeight = fontSize * 1.4;
  const blockH = lines.length * lineHeight;
  const baseY = h - h * 0.13;

  ctx.save();
  const alpha = (cue.fx === 'pop' ? clamp01(p * 2) : p) * outAlpha;
  ctx.globalAlpha = alpha;
  ctx.shadowColor = 'rgba(0,0,0,0.55)';
  ctx.shadowBlur = h * 0.022;
  ctx.shadowOffsetY = h * 0.005;

  // 整块变换：上滑 / 弹出
  if (cue.fx === 'slide') {
    ctx.translate(0, (1 - p) * h * 0.055);
  } else if (cue.fx === 'pop') {
    const s = 0.86 + 0.14 * p;
    ctx.translate(w / 2, baseY - blockH / 2);
    ctx.scale(s, s);
    ctx.translate(-w / 2, -(baseY - blockH / 2));
  }

  lines.forEach(([from, to], lineIdx) => {
    const segs = lineSegments(visible, cue.emphasis, from, to);
    const lineWidth = segs.reduce((acc, s) => acc + ctx.measureText(s.text).width, 0);
    let x = (w - lineWidth) / 2;
    const y = baseY - blockH + (lineIdx + 1) * lineHeight - lineHeight * 0.22;

    // 擦除揭示：每行错峰裁切
    if (cue.fx === 'mask') {
      const lp = clamp01(p * 1.3 - lineIdx * 0.12);
      ctx.save();
      ctx.beginPath();
      ctx.rect(x - 8, y - fontSize, (lineWidth + 16) * lp, lineHeight + fontSize * 0.4);
      ctx.clip();
    }

    if (cue.fx === 'wave') {
      // 波浪：逐字绘制（y 随字符序波动）
      let cx = x;
      segs.forEach((seg) => {
        for (const ch of Array.from(seg.text)) {
          const cw = ctx.measureText(ch).width;
          const dy = Math.sin((cx - x) * 0.02 + local * 2.4) * h * 0.012 * clamp01(p * 2);
          const ca = clamp01(p * 2.2 - (cx - x) / Math.max(lineWidth, 1));
          ctx.save();
          ctx.globalAlpha = alpha * clamp01(ca);
          ctx.fillStyle = seg.emph ? accent : TEXT_MAIN;
          if (seg.emph) {
            ctx.shadowColor = accent;
            ctx.shadowBlur = h * 0.018;
          } else {
            ctx.shadowColor = 'rgba(0,0,0,0.55)';
            ctx.shadowBlur = h * 0.022;
          }
          ctx.fillText(ch, cx, y + dy);
          ctx.restore();
          cx += cw;
        }
      });
    } else {
      segs.forEach((seg) => {
        ctx.fillStyle = seg.emph ? accent : TEXT_MAIN;
        if (seg.emph) {
          ctx.shadowColor = accent;
          ctx.shadowBlur = h * 0.018;
        } else {
          ctx.shadowColor = 'rgba(0,0,0,0.55)';
          ctx.shadowBlur = h * 0.022;
        }
        ctx.fillText(seg.text, x, y);
        x += ctx.measureText(seg.text).width;
      });
      // 打字机光标
      if (cue.fx === 'type' && p < 1 && lineIdx === lines.length - 1) {
        const caretOn = Math.floor(t * 2.6) % 2 === 0;
        if (caretOn) {
          ctx.fillStyle = accent;
          ctx.fillRect(x + 4, y - fontSize * 0.86, Math.max(2, fontSize * 0.06), fontSize * 1.05);
        }
      }
    }

    if (cue.fx === 'mask') ctx.restore();
  });
  ctx.restore();
}

/* ------------------------------ 覆盖物 ------------------------------ */

let noiseTile: HTMLCanvasElement | null = null;
function getNoiseTile(): HTMLCanvasElement {
  if (noiseTile) return noiseTile;
  const tile = document.createElement('canvas');
  tile.width = 96;
  tile.height = 96;
  const tctx = tile.getContext('2d');
  if (tctx) {
    const img = tctx.createImageData(96, 96);
    for (let i = 0; i < img.data.length; i += 4) {
      const v = Math.floor(Math.random() * 255);
      img.data[i] = v;
      img.data[i + 1] = v;
      img.data[i + 2] = v;
      img.data[i + 3] = 26;
    }
    tctx.putImageData(img, 0, 0);
  }
  noiseTile = tile;
  return tile;
}

function drawTitleCard(ctx: CanvasRenderingContext2D, tl: VgTimeline, t: number, w: number, h: number): void {
  const total = 2.8;
  if (t < 0 || t >= total) return;
  const pIn = EASINGS.easeOutBack(clamp01((t - 0.15) / 0.7));
  const alpha = clamp01((t - 0.15) / 0.35) * clamp01((total - t) / 0.45);
  if (alpha <= 0) return;
  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const s = 0.92 + 0.08 * clamp01(pIn);
  ctx.translate(w / 2, h * 0.42);
  ctx.scale(s, s);

  const fontSize = Math.round(h * 0.082);
  ctx.font = `800 ${fontSize}px ${FONT_STACK}`;
  ctx.shadowColor = 'rgba(0,0,0,0.6)';
  ctx.shadowBlur = h * 0.03;
  ctx.fillStyle = TEXT_MAIN;
  ctx.fillText(tl.title, 0, 0);

  // 强调下划线扫入
  const lineW = Math.min(w * 0.5, ctx.measureText(tl.title).width * 1.05);
  const sweep = EASINGS.easeInOut(clamp01((t - 0.7) / 0.7));
  ctx.fillStyle = tl.accent;
  ctx.shadowBlur = h * 0.02;
  ctx.shadowColor = tl.accent;
  ctx.fillRect(-lineW / 2, fontSize * 0.78, lineW * sweep, Math.max(3, h * 0.004));

  // 副标题打字机
  const kicker = `共 ${tl.scenes.length} 幕 · ${Math.round(tl.duration)} 秒`;
  const kp = clamp01((t - 0.55) / 1.2);
  const kChars = Array.from(kicker).slice(0, Math.ceil(kp * kicker.length)).join('');
  ctx.font = `500 ${Math.round(h * 0.028)}px ${FONT_STACK}`;
  ctx.fillStyle = 'rgba(226,232,240,0.78)';
  ctx.shadowColor = 'rgba(0,0,0,0.6)';
  ctx.fillText(kChars, 0, fontSize * 1.5);
  ctx.restore();
}

/* ------------------------------ 渲染器 ------------------------------ */

export function createVgRenderer(tl: VgTimeline): VgRenderer {
  const sceneAt = (t: number): VgScene => {
    for (const s of tl.scenes) {
      if (t >= s.from && t < s.from + s.duration) return s;
    }
    return tl.scenes[tl.scenes.length - 1];
  };

  const draw = (ctx: CanvasRenderingContext2D, t: number, w: number, h: number): void => {
    const frame = Math.floor(t * 30);
    const scene = sceneAt(t);
    const local = t - scene.from;

    ctx.save();
    ctx.clearRect(0, 0, w, h);

    // 1. 当前幕背景（含调色）
    ctx.filter = FILTER_CSS[scene.filter] === 'none' ? 'none' : FILTER_CSS[scene.filter];
    drawGradientBg(ctx, scene, w, h, clamp01(local / Math.max(scene.duration, 0.1)));

    // 2. 进场转场覆盖层（前幕背景离场）
    if (scene.index > 0 && local < scene.transitionDuration && local >= 0) {
      const p = EASINGS.easeOut(clamp01(local / scene.transitionDuration));
      drawTransition(ctx, tl.scenes[scene.index - 1], p, frame, tl.accent, w, h);
    }

    // 3. 幕别章标
    if (tl.showTitles && scene.cueCount >= 2 && !(scene.index === 0 && tl.titleCard)) {
      const a = clamp01(local / 0.4) * 0.85;
      if (a > 0) {
        ctx.save();
        ctx.globalAlpha = a;
        const x = w * 0.055;
        const y = h * 0.095;
        ctx.fillStyle = tl.accent;
        ctx.fillRect(x, y - h * 0.02, Math.max(3, w * 0.003), h * 0.03);
        ctx.font = `600 ${Math.round(h * 0.026)}px ${FONT_STACK}`;
        ctx.fillStyle = 'rgba(231,236,245,0.92)';
        ctx.textAlign = 'left';
        ctx.textBaseline = 'middle';
        ctx.fillText(`第 ${scene.index + 1} 幕`, x + w * 0.012, y);
        ctx.restore();
      }
    }

    // 4. 标题卡（覆盖在第一幕之上）
    if (tl.titleCard) drawTitleCard(ctx, tl, t, w, h);

    // 5. 字幕（同幕内的 cue）
    for (const cue of tl.cues) {
      if (cue.scene !== scene.index) continue;
      drawCue(ctx, cue, t, tl.accent, w, h);
    }

    // 6. 覆盖层（不受调色影响）
    ctx.filter = 'none';

    // 6a. 节拍脉冲
    if (tl.beatPulse && tl.beats.length > 0) {
      let lastBeat = -1;
      for (const b of tl.beats) {
        if (b <= t) lastBeat = b;
        else break;
      }
      if (lastBeat >= 0) {
        const dt = t - lastBeat;
        if (dt < 0.32) {
          const a = Math.pow(1 - dt / 0.32, 2) * 0.10;
          ctx.save();
          ctx.globalCompositeOperation = 'lighter';
          const g = ctx.createRadialGradient(w / 2, h / 2, 0, w / 2, h / 2, Math.max(w, h) * 0.7);
          g.addColorStop(0, `rgba(255,255,255,${a.toFixed(3)})`);
          g.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = g;
          ctx.fillRect(0, 0, w, h);
          ctx.restore();
        }
      }
    }

    // 6b. 胶片颗粒
    if (tl.grain > 0) {
      const tile = getNoiseTile();
      const pattern = ctx.createPattern(tile, 'repeat');
      if (pattern) {
        ctx.save();
        ctx.globalAlpha = tl.grain * 0.07;
        ctx.translate((frame * 7) % 96, (frame * 13) % 96);
        ctx.fillStyle = pattern;
        ctx.fillRect(-96, -96, w + 192, h + 192);
        ctx.restore();
      }
    }

    // 6c. 暗角
    if (tl.vignette > 0) {
      const g = ctx.createRadialGradient(w / 2, h / 2, Math.min(w, h) * 0.38, w / 2, h / 2, Math.hypot(w, h) * 0.62);
      g.addColorStop(0, 'rgba(0,0,0,0)');
      g.addColorStop(1, `rgba(0,0,0,${(tl.vignette * 0.55).toFixed(3)})`);
      ctx.fillStyle = g;
      ctx.fillRect(0, 0, w, h);
    }

    // 6d. 水印角标
    if (tl.watermark.length > 0) {
      ctx.save();
      ctx.globalAlpha = 0.5;
      ctx.font = `500 ${Math.round(h * 0.024)}px ${FONT_STACK}`;
      ctx.fillStyle = '#e7ecf5';
      ctx.textAlign = 'right';
      ctx.textBaseline = 'bottom';
      ctx.fillText(tl.watermark, w - w * 0.03, h - h * 0.028);
      ctx.restore();
    }

    ctx.restore();
  };

  return { draw };
}
