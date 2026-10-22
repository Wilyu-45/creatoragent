/**
 * 放映室 —— 直接消费剪辑师的成片时间轴契约，把岗位产物放成一条片子。
 *
 * 为什么自己画而不是用 ``src/vg`` 的 VgStage：vg 的场景是程序化背景，工厂的镜头
 * 是真实产物（关键帧 PNG、调色回写、缺画面镜头）。这里按 ``shots[].frames`` 逐镜
 * 插值画帧、按 ``shot.grade`` 套 CSS 滤镜、按 ``voice/masters`` 对齐音轨、按
 * ``subtitles.cues.rect`` 叠中文字幕（本机点阵字体不含汉字，中文一律由播放器叠加）。
 *
 * **不假装**：没有画面的镜头（``missing_visual``）画成占位板并写明"缺画面"；
 * 离线样片带 ``simulated``，界面如实标注"示意帧/样例通道"。导出走 MediaRecorder
 * 实时录制，耗时≈片长，失败与不支持都直接说明。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { pickRecorderMime } from '../vg/exporter.ts';
import { mediaUrl } from './api.ts';
import { IconDownload, IconPause, IconPlay, IconRewind } from '../components/icons.tsx';

export interface ShotFrame {
  url: string;
  ref?: string;
  t: number;
  zoom?: number;
}

export interface FactoryShot {
  id: string;
  slate?: string;
  index?: number;
  start_s: number;
  duration_s: number;
  frames?: ShotFrame[];
  poster_url?: string;
  graded_url?: string;
  clip_url?: string;
  image_url?: string;
  grade?: { exposure_ev?: number; contrast?: number; saturation?: number; temp_k?: number; label?: string };
  kenburns?: { dir?: string; amount?: number };
  transition?: string;
  transition_label?: string;
  transition_duration?: number;
  size_label?: string;
  description?: string;
  speaker?: string;
  mood?: string;
}

export interface FactoryTimeline {
  label?: string;
  note?: string;
  aspect?: string;
  fps?: number;
  duration_s?: number;
  shots?: FactoryShot[];
  voice?: { items?: { text?: string; offset?: number; duration_s?: number; character?: string; url?: string }[] };
  masters?: { url?: string; total_s?: number; label?: string; kind?: string }[];
  overlays?: { kind: string; start_s: number; end_s: number; rect: number[]; text_cn: string; url: string; layer: number }[];
  subtitles?: { cues?: { start: number; end: number; text_cn?: string; text?: string; rect?: number[]; highlight?: unknown[] }[] };
  missing_visual?: string[];
  cuts?: number;
  transitions?: { at_s: number; kind: string; label: string }[];
  delivery?: Record<string, unknown>;
  simulated?: boolean;
  provider?: string;
}

const ACCENT = '#22d3ee';

function sizeOf(aspect: string | undefined, base: number): { w: number; h: number } {
  const [rawW, rawH] = String(aspect ?? '9:16').split(':').map((item) => Number(item) || 1);
  const ratio = rawW / rawH;
  return ratio >= 1 ? { w: Math.round(base * ratio), h: base } : { w: base, h: Math.round(base / ratio) };
}

function gradeFilter(shot: FactoryShot): string {
  const grade = shot.grade ?? {};
  const exposure = 1 + Math.max(-0.6, Math.min(0.6, Number(grade.exposure_ev ?? 0) * 0.55));
  const contrast = Math.max(0.6, Math.min(1.8, Number(grade.contrast ?? 1)));
  const saturate = Math.max(0, Math.min(2.2, Number(grade.saturation ?? 1)));
  const temp = Number(grade.temp_k ?? 6500);
  const warm = Math.max(-0.25, Math.min(0.25, (temp - 6500) / 9000));
  const hue = warm * -22;
  return `brightness(${exposure.toFixed(3)}) contrast(${contrast.toFixed(3)}) saturate(${saturate.toFixed(3)}) hue-rotate(${hue.toFixed(1)}deg)`;
}

function shotImage(shot: FactoryShot, local: number): { src: string; zoom: number } | null {
  const frames = shot.frames ?? [];
  if (!frames.length) {
    const still = shot.graded_url || shot.poster_url || shot.image_url || shot.clip_url;
    return still ? { src: still, zoom: 1.08 } : null;
  }
  let pick = frames[0];
  for (const frame of frames) if (Number(frame.t ?? 0) <= local + 1e-6) pick = frame;
  const next = frames[Math.min(frames.length - 1, frames.indexOf(pick) + 1)];
  const span = Math.max(0.001, Number(next?.t ?? pick.t) - Number(pick.t));
  const zoom = Number(pick.zoom ?? 1.06) + Math.max(0, Math.min(1, local / span)) * (Number(next?.zoom ?? pick.zoom) - Number(pick.zoom));
  return { src: pick.url, zoom };
}

export function Player({ timeline, onToast }: { timeline: FactoryTimeline | null; onToast: (text: string, error?: boolean) => void }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const images = useRef<Map<string, HTMLImageElement>>(new Map());
  const [loaded, setLoaded] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [exporting, setExporting] = useState<{ state: string; ratio: number } | null>(null);

  const shots = useMemo(() => [...(timeline?.shots ?? [])].sort((a, b) => a.start_s - b.start_s), [timeline]);
  const total = Number(timeline?.duration_s ?? shots.reduce((sum, shot) => sum + Number(shot.duration_s ?? 0), 0) ?? 0);
  const master = timeline?.masters?.[0]?.url ?? '';
  const cues = timeline?.subtitles?.cues ?? [];
  const overlays = timeline?.overlays ?? [];
  const size = sizeOf(timeline?.aspect, 540);
  const missing = new Set((timeline?.missing_visual ?? []).map(String));

  /** 产物 URL 归一：后端带 url_prefix 时已是绝对路径，兼容相对引用。 */
  const resolve = useCallback((url?: string) => (url ? mediaUrl(url.startsWith('/') ? url : `artifacts/${url}`) : ''), []);

  useEffect(() => {
    const wanted = new Set<string>();
    for (const shot of shots) {
      for (const frame of shot.frames ?? []) if (frame.url) wanted.add(resolve(frame.url));
      for (const key of ['graded_url', 'poster_url', 'image_url', 'clip_url'] as const) if (shot[key]) wanted.add(resolve(shot[key]));
    }
    for (const overlay of overlays) if (overlay.url) wanted.add(resolve(overlay.url));
    for (const src of wanted) {
      if (images.current.has(src)) continue;
      const image = new Image();
      image.onload = () => setLoaded((prev) => prev + 1);
      image.src = src;
      images.current.set(src, image);
    }
  }, [shots, overlays, resolve]);

  const draw = useCallback(
    (at: number) => {
      const canvas = canvasRef.current;
      const ctx = canvas?.getContext('2d');
      if (!canvas || !ctx) return;
      const w = canvas.width;
      const h = canvas.height;
      ctx.fillStyle = '#080b12';
      ctx.fillRect(0, 0, w, h);
      const current = shots.find((shot) => at >= shot.start_s && at < shot.start_s + Number(shot.duration_s ?? 0)) ?? shots[shots.length - 1];
      if (!current) return;
      const local = Math.max(0, at - current.start_s);
      const slate = String(current.slate ?? current.id ?? '');
      const missingShot = missing.has(slate) || !shotImage(current, local);

      const paint = (shot: FactoryShot, alpha: number, slideY = 0) => {
        const pick = shotImage(shot, Math.max(0, at - shot.start_s));
        ctx.save();
        ctx.globalAlpha = alpha;
        if (!pick) {
          ctx.restore();
          return;
        }
        const image = images.current.get(resolve(pick.src));
        const kb = shot.kenburns ?? {};
        const drift = Math.max(0, Math.min(1, (at - shot.start_s) / Math.max(0.4, Number(shot.duration_s ?? 1))));
        const zoom = (pick.zoom ?? 1.06) * (kb.dir === 'out' ? 1 + 0.06 * (1 - drift) : 1 + 0.06 * drift);
        const dx = kb.dir === 'left' ? -0.03 * drift : kb.dir === 'right' ? 0.03 * drift : 0;
        const dy = kb.dir === 'up' ? -0.03 * drift : kb.dir === 'down' ? 0.03 * drift : 0;
        if (image?.complete && image.naturalWidth) {
          const scale = Math.max((w * zoom) / image.naturalWidth, (h * zoom) / image.naturalHeight);
          const dw = image.naturalWidth * scale;
          const dh = image.naturalHeight * scale;
          ctx.filter = gradeFilter(shot);
          ctx.drawImage(image, (w - dw) / 2 + dx * w, (h - dh) / 2 + dy * h + slideY, dw, dh);
          ctx.filter = 'none';
        } else {
          ctx.fillStyle = '#10151f';
          ctx.fillRect(0, 0, w, h);
        }
        ctx.restore();
      };

      paint(current, 1);
      // 转场：下一镜在前一镜末尾按 transition_duration 交叉溶解（push 额外从下方推入）
      const next = shots[shots.indexOf(current) + 1];
      const tail = Number(current.duration_s ?? 0) - local;
      const blend = Math.min(Number(current.transition_duration ?? 0) || 0.4, Math.max(0, tail));
      if (next && tail <= blend && blend > 0.02) {
        const ratio = 1 - tail / blend;
        paint(next, ratio, current.transition === 'push' ? (1 - ratio) * h * 0.35 : 0);
      }

      if (missingShot) {
        ctx.save();
        ctx.fillStyle = 'rgba(8,11,18,.82)';
        ctx.fillRect(0, h * 0.34, w, h * 0.26);
        ctx.strokeStyle = '#e0566b';
        ctx.lineWidth = 2;
        ctx.strokeRect(w * 0.06, h * 0.35, w * 0.88, h * 0.24);
        ctx.fillStyle = '#f2d4da';
        ctx.font = `${Math.round(h * 0.032)}px system-ui, "Microsoft YaHei", sans-serif`;
        ctx.textAlign = 'center';
        ctx.fillText('缺画面：该镜没有关键帧/片段', w / 2, h * 0.45);
        ctx.fillStyle = '#c9d3e6';
        ctx.font = `${Math.round(h * 0.024)}px system-ui, "Microsoft YaHei", sans-serif`;
        ctx.fillText(`${slate}｜${current.size_label ?? ''}｜${String(current.description ?? '').slice(0, 26)}`, w / 2, h * 0.51);
        ctx.restore();
      }

      for (const overlay of overlays) {
        if (at < Number(overlay.start_s) || at > Number(overlay.end_s)) continue;
        const rect = overlay.rect ?? [0.08, 0.4, 0.84, 0.18];
        const [rx, ry, rw, rh] = rect.map((value, index) => Number(value) * (index % 2 === 0 ? w : h));
        const image = overlay.url ? images.current.get(resolve(overlay.url)) : undefined;
        const full = overlay.kind === 'head' || overlay.kind === 'tail';
        ctx.save();
        const fade = Math.min(1, (at - Number(overlay.start_s)) / 0.3, (Number(overlay.end_s) - at) / 0.3);
        ctx.globalAlpha = Math.max(0.2, fade);
        // 包装卡是整幅版式（安全区边框 + rect 处的色块），所以整幅铺；卡没加载时只补 rect 的底板，别把整条片子糊黑
        if (image?.naturalWidth) ctx.drawImage(image, 0, 0, w, h);
        else if (full) {
          ctx.fillStyle = 'rgba(8,10,18,.9)';
          ctx.fillRect(0, 0, w, h);
        } else {
          ctx.fillStyle = 'rgba(10,12,20,.8)';
          ctx.fillRect(rx, ry, rw, rh);
        }
        ctx.fillStyle = '#f4f8ff';
        ctx.font = `${Math.round(overlay.kind === 'badge' ? h * 0.022 : h * 0.036)}px system-ui, "Microsoft YaHei", sans-serif`;
        ctx.textAlign = overlay.kind === 'badge' ? 'right' : 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(String(overlay.text_cn ?? ''), overlay.kind === 'badge' ? rx + rw - 8 : rx + rw / 2, ry + rh / 2);
        ctx.restore();
      }

      const cue = cues.find((item) => at >= Number(item.start) && at <= Number(item.end));
      if (cue) {
        const rect = cue.rect ?? [0.06, 0.8, 0.88, 0.13];
        const [cx, cy, cw, ch] = rect.map((value, index) => Number(value) * (index % 2 === 0 ? w : h));
        const text = String(cue.text_cn ?? cue.text ?? '');
        ctx.save();
        ctx.fillStyle = 'rgba(6,8,14,.62)';
        ctx.fillRect(cx, cy, cw, ch);
        ctx.strokeStyle = ACCENT;
        ctx.strokeRect(cx, cy, cw, ch);
        ctx.fillStyle = '#f6f9ff';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const font = Math.round(ch * 0.52);
        ctx.font = `${font}px system-ui, "Microsoft YaHei", sans-serif`;
        const perLine = Math.max(1, Math.floor(cw / (font * 1.05)));
        const lines: string[] = [];
        for (let index = 0; index < text.length; index += perLine) lines.push(text.slice(index, index + perLine));
        lines.slice(0, 2).forEach((line, index) => {
          const offset = cy + ch / 2 + (index - (Math.min(lines.length, 2) - 1) / 2) * font * 1.15;
          ctx.fillText(line, cx + cw / 2, offset);
        });
        ctx.restore();
      }

      ctx.save();
      ctx.fillStyle = 'rgba(244,248,255,.7)';
      ctx.font = `${Math.round(h * 0.02)}px ui-monospace, Menlo, monospace`;
      ctx.textAlign = 'left';
      ctx.fillText(`${at.toFixed(2)}s / ${total.toFixed(2)}s · ${slate} · ${current.grade?.label ?? '未调色'}`, 10, 18);
      ctx.restore();
    },
    [cues, missing, overlays, resolve, shots, total],
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = size.w;
    canvas.height = size.h;
    draw(time);
  }, [draw, loaded, size.h, size.w, time]);

  /** 时间基准：有混音总轨就用音频时钟（天然音画同步），否则用墙钟。 */
  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const step = () => {
      const audio = audioRef.current;
      if (master && audio && !audio.paused) setTime(audio.currentTime);
      else {
        const now = performance.now();
        setTime((prev) => Math.min(total, prev + (now - last) / 1000));
        last = now;
      }
      raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [playing, master, total]);

  const toggle = () => {
    const audio = audioRef.current;
    if (playing) {
      setPlaying(false);
      audio?.pause();
      return;
    }
    if (time >= total - 0.02) setTime(0);
    if (audio && master) {
      audio.currentTime = time;
      void audio.play().catch(() => onToast('浏览器拦了自动播放，再点一次播放', true));
    }
    setPlaying(true);
  };

  const seek = (value: number) => {
    setTime(value);
    const audio = audioRef.current;
    if (audio && master) audio.currentTime = value;
  };

  /** 录制当前画布 + 音轨：实时导出，耗时≈片长。 */
  const exportWebm = async () => {
    const canvas = canvasRef.current;
    const picked = pickRecorderMime();
    if (!canvas || !picked) {
      onToast('当前浏览器不支持 MediaRecorder 录制', true);
      return;
    }
    if (!total) {
      onToast('没有可导出的时间轴', true);
      return;
    }
    setExporting({ state: 'preparing', ratio: 0 });
    try {
      const stream = canvas.captureStream(Math.max(12, Math.min(30, Number(timeline?.fps ?? 24))));
      const audio = audioRef.current;
      if (master && audio) {
        const context = new AudioContext();
        const source = context.createMediaElementSource(audio);
        const sink = context.createMediaStreamDestination();
        source.connect(sink);
        source.connect(context.destination);
        for (const track of sink.stream.getAudioTracks()) stream.addTrack(track);
      }
      const recorder = new MediaRecorder(stream, { mimeType: picked.mime, videoBitsPerSecond: 6_000_000 });
      const chunks: Blob[] = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size) chunks.push(event.data);
      };
      const stopped = new Promise<void>((resolve) => {
        recorder.onstop = () => resolve();
      });
      recorder.start(400);
      seek(0);
      setPlaying(true);
      setExporting({ state: 'rendering', ratio: 0 });
      const began = Date.now();
      while (Date.now() - began < (total + 1.2) * 1000) {
        await new Promise((done) => setTimeout(done, 200));
        const ratio = Math.min(1, (timeRef.current ?? 0) / total);
        setExporting({ state: 'rendering', ratio });
        if (ratio >= 1) break;
      }
      recorder.stop();
      await stopped;
      setPlaying(false);
      const blob = new Blob(chunks, { type: picked.mime });
      const href = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = href;
      anchor.download = `${String(timeline?.label ?? 'factory')}.${picked.ext}`;
      anchor.click();
      URL.revokeObjectURL(href);
      setExporting(null);
      onToast(`已导出 ${(blob.size / 1048576).toFixed(1)} MB ${picked.ext}`);
    } catch (error) {
      setExporting(null);
      onToast(`导出失败：${error instanceof Error ? error.message : String(error)}`, true);
    }
  };

  const timeRef = useRef(0);
  useEffect(() => {
    timeRef.current = time;
  }, [time]);

  if (!timeline || !shots.length) {
    return <div className="fc-empty">接上剪辑师（或渲染导出）的时间轴后，这里就能试映。</div>;
  }

  const drift = Math.abs(Number(master ? timeline.masters?.[0]?.total_s ?? 0 : total) - total);

  return (
    <div className="fc-player">
      <div className="fc-stage">
        <canvas ref={canvasRef} width={size.w} height={size.h} />
        {exporting ? <div className="fc-rec">● 录制中 {Math.round(exporting.ratio * 100)}%</div> : null}
      </div>
      <div className="fc-side">
        <div className="row" style={{ gap: 8, alignItems: 'center' }}>
          <button className="btn btn-sm btn-primary" onClick={toggle}>
            {playing ? <IconPause size={14} /> : <IconPlay size={14} />}
            {playing ? '暂停' : '播放'}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => seek(0)}>
            <IconRewind size={14} /> 回到开头
          </button>
          <button className="btn btn-sm btn-ghost" onClick={exportWebm} disabled={Boolean(exporting)}>
            <IconDownload size={14} /> {exporting ? '导出中…' : '导出视频'}
          </button>
        </div>
        <div className="fc-scrub">
          <input type="range" min={0} max={Math.max(0.1, total)} step={0.05} value={time} onChange={(event) => seek(Number(event.target.value))} />
          <span className="mono small">
            {time.toFixed(2)} / {total.toFixed(2)}s
          </span>
        </div>
        <div className="fc-track">
          {shots.map((shot, index) => (
            <button
              key={shot.id ?? index}
              className={`fc-tile${time >= shot.start_s && time < shot.start_s + Number(shot.duration_s ?? 0) ? ' on' : ''}`}
              style={{ flexGrow: Math.max(0.3, Number(shot.duration_s ?? 1)) }}
              title={`${shot.slate ?? shot.id}｜${shot.size_label ?? ''}｜${(shot.description ?? '').slice(0, 40)}`}
              onClick={() => seek(shot.start_s)}
            >
              <span className="mono">{shot.slate ?? shot.id}</span>
              <span className="small">{Number(shot.duration_s ?? 0).toFixed(2)}s</span>
              {missing.has(String(shot.slate ?? '')) ? <i className="fc-lack">缺画面</i> : null}
            </button>
          ))}
        </div>
        <div className="fc-facts">
          <div>
            <span className="muted">音轨</span> {master ? `${timeline.masters?.[0]?.label ?? '成片音轨'} ${(timeline.masters?.[0]?.total_s ?? 0).toFixed(2)}s` : '无混音总轨（只有分轨）'}
            {drift > 0.05 ? <span className="warn"> · 与画面差 {drift.toFixed(2)}s，需回混音</span> : null}
          </div>
          <div>
            <span className="muted">字幕</span> {cues.length} 条 · <span className="muted">刀口</span> {timeline.cuts ?? 0} 处 · <span className="muted">包装图层</span> {overlays.length}
          </div>
          <div>
            <span className="muted">缺画面</span> {(timeline.missing_visual ?? []).length} 镜
            {timeline.simulated ? <span className="warn"> · 示意产物（{timeline.provider ?? 'sample'}）</span> : null}
          </div>
          {timeline.delivery ? (
            <div className="small muted">
              交付：{String(timeline.delivery.resolution ?? '')} {String(timeline.delivery.fps ?? '')}fps 约 {String(timeline.delivery.est_total_mb ?? '?')}MB
            </div>
          ) : null}
        </div>
        {master ? <audio ref={audioRef} src={resolve(master)} preload="auto" /> : <audio ref={audioRef} preload="none" />}
      </div>
    </div>
  );
}
