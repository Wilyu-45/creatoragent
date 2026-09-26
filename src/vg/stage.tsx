/**
 * DOM 预览台 —— 与 Canvas 导出渲染器同参数的实时预览。
 *
 * 消费同一份 VgTimeline：背景渐变 + Ken Burns、六类转场、七档调色（CSS filter）、
 * 六种字幕动效 + 关键词高亮、标题卡、幕别章标、节拍脉冲、颗粒/暗角/水印。
 * 全部为「t → 样式」的纯函数（组件无内部定时器），字号用 cqh 随舞台等比缩放。
 */
import type { CSSProperties } from 'react';
import { EASINGS } from '../motion/easing.ts';
import {
  FILTER_CSS,
  bgCss,
  splitEmphasis,
  type KenBurns,
  type VgCue,
  type VgScene,
  type VgTimeline,
} from './timeline.ts';

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

const GRAIN_URI =
  "url(\"data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='96' height='96'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='2'/><feColorMatrix type='saturate' values='0'/></filter><rect width='96' height='96' filter='url(%23n)' opacity='0.55'/></svg>\")";

function kenburnsStyle(kb: KenBurns, p: number): { transform: string } {
  const amt = kb.amount;
  let scale = 1;
  let dx = 0;
  let dy = 0;
  if (kb.dir === 'in') scale = 1 + amt * p;
  else if (kb.dir === 'out') scale = 1 + amt * (1 - p);
  else {
    scale = 1 + amt * 0.8;
    dx = (kb.dir === 'left' ? -1 : 1) * amt * 60 * p;
    dy = (kb.dir === 'left' ? 1 : -1) * amt * 12 * p;
  }
  return { transform: `translate(${dx.toFixed(2)}%, ${dy.toFixed(2)}%) scale(${scale.toFixed(4)})` };
}

/* ------------------------------ 转场覆盖层 ------------------------------ */

function TransitionOverlay({ prev, p, accent }: { prev: VgScene; p: number; accent: string }) {
  const kb = kenburnsStyle(prev.kenburns, 1);
  const bg = <div style={{ position: 'absolute', inset: '-8%', background: bgCss(prev.bg), ...kb }} />;
  const base: CSSProperties = { position: 'absolute', inset: 0 };

  switch (prev.transition) {
    case 'crossfade':
      return <div style={{ ...base, opacity: 1 - p }}>{bg}</div>;
    case 'push':
      return <div style={{ ...base, transform: `translateX(${(-p * 100).toFixed(2)}%)` }}>{bg}</div>;
    case 'wipe':
      return (
        <div style={base}>
          <div style={{ ...base, clipPath: `inset(0 0 0 ${(p * 100).toFixed(2)}%)` }}>{bg}</div>
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: `${(p * 100).toFixed(2)}%`, width: 3, background: accent, opacity: 1 - p }} />
        </div>
      );
    case 'zoom':
      return (
        <div style={{ ...base, opacity: 1 - p, transform: `scale(${(1 + 0.4 * p).toFixed(3)})` }}>{bg}</div>
      );
    case 'blur':
      return <div style={{ ...base, opacity: 1 - p, filter: `blur(${(14 * p).toFixed(1)}px)` }}>{bg}</div>;
    case 'glitch': {
      const alpha = p < 0.65 ? 1 : (1 - p) / 0.35;
      const bands = [0, 1, 2].map((i) => {
        const seed = (prev.seed + i * 7919) % 1000;
        const off = (((seed / 1000) - 0.5) * 6 * (1 - p)).toFixed(2);
        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              inset: 0,
              clipPath: `inset(${i * 30 + 4}% 0 ${58 - i * 30}% 0)`,
              transform: `translateX(${off}%)`,
              opacity: alpha * (0.7 + (seed % 30) / 100),
            }}
          >
            {bg}
          </div>
        );
      });
      return (
        <div style={{ ...base, opacity: alpha }}>
          {bands}
          <div style={{ position: 'absolute', left: 0, right: 0, top: `${(seedTop(prev.seed, p) * 100).toFixed(1)}%`, height: 4, background: accent, opacity: alpha * 0.12 }} />
        </div>
      );
    }
  }
}

function seedTop(seed: number, p: number): number {
  return ((seed % 97) / 97) * 0.9 * (1 - p) + p * 0.4;
}

/* ------------------------------ 字幕 ------------------------------ */

const CUE_EASING: Record<string, (x: number) => number> = {
  fade: EASINGS.easeOut,
  slide: EASINGS.easeOut,
  pop: EASINGS.easeOutBack,
  type: EASINGS.linear,
  mask: EASINGS.easeOut,
  wave: EASINGS.easeOut,
};

function EmphText({ text, spans, accent }: { text: string; spans: VgCue['emphasis']; accent: string }) {
  return (
    <>
      {splitEmphasis(text, spans).map((seg, i) =>
        seg.emph ? (
          <span key={i} style={{ color: accent, textShadow: `0 0 18px ${accent}` }}>
            {seg.text}
          </span>
        ) : (
          <span key={i}>{seg.text}</span>
        ),
      )}
    </>
  );
}

function CueView({ cue, local, accent }: { cue: VgCue; local: number; accent: string }) {
  const rawP = clamp01((local - cue.delay) / 0.45);
  const p = (CUE_EASING[cue.fx] ?? EASINGS.easeOut)(rawP);
  const out = clamp01((cue.end - (cue.start + local)) / 0.35);
  if (rawP <= 0 || out <= 0) return null;
  const alpha = (cue.fx === 'pop' ? clamp01(p * 2) : p) * out;

  const baseStyle: CSSProperties = {
    position: 'absolute',
    left: '8cqw',
    right: '8cqw',
    bottom: '12cqh',
    textAlign: 'center',
    fontSize: '5.2cqh',
    fontWeight: 700,
    lineHeight: 1.42,
    color: '#f2f5fa',
    textShadow: '0 2px 14px rgba(0,0,0,0.6)',
    opacity: alpha,
  };

  if (cue.fx === 'slide') baseStyle.transform = `translateY(${((1 - p) * 0.8).toFixed(3)}em)`;
  if (cue.fx === 'pop') {
    baseStyle.transform = `scale(${(0.86 + 0.14 * p).toFixed(3)})`;
    baseStyle.transformOrigin = '50% 100%';
  }

  if (cue.fx === 'type') {
    const chars = Array.from(cue.text);
    const visible = chars.slice(0, Math.ceil(p * chars.length)).join('');
    return (
      <div style={baseStyle}>
        <EmphText text={visible} spans={cue.emphasis} accent={accent} />
        {p < 1 && Math.floor(local * 2.6) % 2 === 0 ? (
          <span style={{ color: accent, fontWeight: 400 }}>▌</span>
        ) : null}
      </div>
    );
  }

  if (cue.fx === 'mask') {
    return (
      <div style={{ ...baseStyle, clipPath: `inset(0 ${((1 - p) * 100).toFixed(2)}% 0 0)` }}>
        <EmphText text={cue.text} spans={cue.emphasis} accent={accent} />
      </div>
    );
  }

  if (cue.fx === 'wave') {
    const chars = Array.from(cue.text);
    const spans = cue.emphasis;
    return (
      <div style={{ ...baseStyle, textShadow: undefined }}>
        {chars.map((ch, i) => {
          const emph = spans.some((s) => i >= s.start && i < s.end);
          const ca = clamp01(p * 2.2 - (i / Math.max(chars.length, 1)) * 1.2);
          return (
            <span
              key={i}
              style={{
                display: 'inline-block',
                transform: `translateY(${(Math.sin(i * 0.55 + local * 2.4) * 0.22 * clamp01(p * 2)).toFixed(3)}em)`,
                opacity: ca,
                color: emph ? accent : undefined,
                textShadow: emph ? `0 0 18px ${accent}` : '0 2px 14px rgba(0,0,0,0.6)',
              }}
            >
              {ch === ' ' ? '\u00A0' : ch}
            </span>
          );
        })}
      </div>
    );
  }

  return (
    <div style={baseStyle}>
      <EmphText text={cue.text} spans={cue.emphasis} accent={accent} />
    </div>
  );
}

/* ------------------------------ 幕 ------------------------------ */

function SceneView({ tl, scene, local }: { tl: VgTimeline; scene: VgScene; local: number }) {
  const kbP = clamp01(local / Math.max(scene.duration, 0.1));
  const filter = FILTER_CSS[scene.filter] === 'none' ? undefined : FILTER_CSS[scene.filter];
  const showTransition = scene.index > 0 && local < scene.transitionDuration;
  const trP = EASINGS.easeOut(clamp01(local / Math.max(scene.transitionDuration, 0.01)));
  const kickerAlpha = tl.showTitles && scene.cueCount >= 2 && !(scene.index === 0 && tl.titleCard)
    ? clamp01(local / 0.4) * 0.85
    : 0;

  return (
    <div style={{ position: 'absolute', inset: 0, filter, overflow: 'hidden' }}>
      <div style={{ position: 'absolute', inset: '-8%', background: bgCss(scene.bg), ...kenburnsStyle(scene.kenburns, kbP) }} />
      {showTransition ? <TransitionOverlay prev={tl.scenes[scene.index - 1]} p={trP} accent={tl.accent} /> : null}
      {kickerAlpha > 0 ? (
        <div style={{ position: 'absolute', left: '5.5cqw', top: '8.5cqh', display: 'flex', alignItems: 'center', gap: '1.2cqw', opacity: kickerAlpha }}>
          <span style={{ width: '0.35cqw', height: '3cqh', background: tl.accent, boxShadow: `0 0 10px ${tl.accent}`, borderRadius: 2 }} />
          <span style={{ fontSize: '2.6cqh', fontWeight: 600, color: 'rgba(231,236,245,0.92)', textShadow: '0 1px 8px rgba(0,0,0,0.6)' }}>
            第 {scene.index + 1} 幕
          </span>
        </div>
      ) : null}
      {tl.cues
        .filter((c) => c.scene === scene.index)
        .map((cue, i) => (
          <CueView key={`${cue.start}-${i}`} cue={cue} local={local - (cue.start - scene.from)} accent={tl.accent} />
        ))}
    </div>
  );
}

/* ------------------------------ 标题卡 ------------------------------ */

function TitleCard({ tl, t }: { tl: VgTimeline; t: number }) {
  const total = 2.8;
  if (t < 0 || t >= total) return null;
  const pIn = EASINGS.easeOutBack(clamp01((t - 0.15) / 0.7));
  const alpha = clamp01((t - 0.15) / 0.35) * clamp01((total - t) / 0.45);
  if (alpha <= 0) return null;
  const sweep = EASINGS.easeInOut(clamp01((t - 0.7) / 0.7));
  const kicker = `共 ${tl.scenes.length} 幕 · ${Math.round(tl.duration)} 秒`;
  const kp = clamp01((t - 0.55) / 1.2);

  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        display: 'grid',
        placeItems: 'center',
        opacity: alpha,
        pointerEvents: 'none',
      }}
    >
      <div style={{ textAlign: 'center', transform: `scale(${(0.92 + 0.08 * clamp01(pIn)).toFixed(3)})` }}>
        <div
          style={{
            fontSize: '8.2cqh',
            fontWeight: 800,
            letterSpacing: '0.06em',
            color: '#f2f5fa',
            textShadow: '0 4px 28px rgba(0,0,0,0.6)',
          }}
        >
          {tl.title}
        </div>
        <div
          style={{
            margin: '1.8cqh auto 0',
            width: `${(sweep * 46).toFixed(1)}cqw`,
            height: '0.5cqh',
            borderRadius: 4,
            background: tl.accent,
            boxShadow: `0 0 16px ${tl.accent}`,
          }}
        />
        <div style={{ marginTop: '2.2cqh', fontSize: '2.8cqh', color: 'rgba(226,232,240,0.78)', letterSpacing: '0.14em' }}>
          {Array.from(kicker).slice(0, Math.ceil(kp * kicker.length)).join('')}
        </div>
      </div>
    </div>
  );
}

/* ------------------------------ 舞台根 ------------------------------ */

export function VgStage({ tl, t, generation }: { tl: VgTimeline; t: number; generation: number }) {
  const frame = Math.floor(t * 30);
  let lastBeat = -1;
  if (tl.beatPulse && tl.beats.length > 0) {
    for (const b of tl.beats) {
      if (b <= t) lastBeat = b;
      else break;
    }
  }
  const pulse = lastBeat >= 0 ? Math.pow(clamp01(1 - (t - lastBeat) / 0.32), 2) * 0.10 : 0;

  return (
    <div
      key={generation}
      style={{ position: 'absolute', inset: 0, overflow: 'hidden', background: '#000', containerType: 'size' }}
    >
      {tl.scenes.map((scene) =>
        t >= scene.from && t < scene.from + scene.duration ? (
          <SceneView key={scene.index} tl={tl} scene={scene} local={t - scene.from} />
        ) : null,
      )}
      {tl.titleCard ? <TitleCard tl={tl} t={t} /> : null}

      {/* 覆盖层：不受调色影响 */}
      {pulse > 0 ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            mixBlendMode: 'screen',
            background: `radial-gradient(circle at 50% 50%, rgba(255,255,255,${pulse.toFixed(3)}) 0%, rgba(255,255,255,0) 70%)`,
          }}
        />
      ) : null}
      {tl.grain > 0 ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage: GRAIN_URI,
            backgroundSize: '96px 96px',
            opacity: tl.grain * 0.07,
            backgroundPosition: `${(frame * 7) % 96}px ${(frame * 13) % 96}px`,
          }}
        />
      ) : null}
      {tl.vignette > 0 ? (
        <div
          style={{
            position: 'absolute',
            inset: 0,
            background: `radial-gradient(ellipse at center, rgba(0,0,0,0) 42%, rgba(0,0,0,${(tl.vignette * 0.55).toFixed(3)}) 100%)`,
          }}
        />
      ) : null}
      {tl.watermark.length > 0 ? (
        <div
          style={{
            position: 'absolute',
            right: '3cqw',
            bottom: '2.8cqh',
            fontSize: '2.4cqh',
            color: '#e7ecf5',
            opacity: 0.5,
            textShadow: '0 1px 6px rgba(0,0,0,0.6)',
          }}
        >
          {tl.watermark}
        </div>
      ) : null}
    </div>
  );
}
