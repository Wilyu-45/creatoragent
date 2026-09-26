/**
 * 视频导出器 —— Canvas 帧渲染 + WebAudio 混音 → MediaRecorder 真实视频文件。
 *
 * 管线：canvas.captureStream(fps) +（可选）音频 MediaStreamDestination 合流，
 * 实时绘制逐帧画面并录制。时间基准取音频时钟（无音频时退化为墙钟），
 * 因此暂停/继续天然音画同步。优先 MP4（H.264），浏览器不支持时回退 WebM。
 *
 * 已知局限（如实标注）：录制是实时编码，导出耗时≈视频时长；跨会话续传不可靠
 * （WebM/MP4 分段拼接不被播放器普遍支持），中断后建议重新导出。标签页隐藏时
 * rAF 冻结会破坏实时编码，检测到即自动暂停。
 */
import { AudioMixer } from './audio.ts';
import { createVgRenderer } from './canvas.ts';
import type { VgTimeline } from './timeline.ts';

export type ExportState = 'idle' | 'preparing' | 'rendering' | 'paused' | 'done' | 'cancelled' | 'error';

export interface ExportProgress {
  t: number;
  total: number;
  state: ExportState;
  message?: string;
}

export interface ExportResult {
  blob: Blob;
  ext: string;
  mime: string;
}

export interface ExportOptions {
  timeline: VgTimeline;
  width: number;
  height: number;
  fps: number;
  bgm?: File | null;
  voice?: File | null;
  /** BGM 音量 0..1。 */
  volume: number;
  onProgress?: (p: ExportProgress) => void;
  onDone?: (result: ExportResult) => void;
  onError?: (message: string) => void;
}

/** 按优先级探测可用容器：MP4(H.264) → WebM(VP9) → WebM(VP8) → WebM。 */
export function pickRecorderMime(): { mime: string; ext: string } | null {
  if (typeof MediaRecorder === 'undefined') return null;
  const candidates: { mime: string; ext: string }[] = [
    { mime: 'video/mp4;codecs="avc1.640028,mp4a.40.2"', ext: 'mp4' },
    { mime: 'video/mp4', ext: 'mp4' },
    { mime: 'video/webm;codecs=vp9,opus', ext: 'webm' },
    { mime: 'video/webm;codecs=vp8,opus', ext: 'webm' },
    { mime: 'video/webm', ext: 'webm' },
  ];
  for (const c of candidates) {
    if (MediaRecorder.isTypeSupported(c.mime)) return c;
  }
  return null;
}

export function exporterSupported(): boolean {
  return (
    typeof MediaRecorder !== 'undefined' &&
    typeof HTMLCanvasElement !== 'undefined' &&
    'captureStream' in HTMLCanvasElement.prototype &&
    pickRecorderMime() != null
  );
}

/** 分辨率 → 视频码率（bps）。 */
function bitrateFor(height: number): number {
  if (height >= 2160) return 35_000_000;
  if (height >= 1080) return 12_000_000;
  return 6_000_000;
}

export class VideoExporter {
  private opts: ExportOptions | null = null;
  private canvas: HTMLCanvasElement | null = null;
  private audio: AudioMixer | null = null;
  private recorder: MediaRecorder | null = null;
  private chunks: Blob[] = [];
  private raf = 0;
  private state: ExportState = 'idle';
  private wallStart = 0;
  private wallPausedTotal = 0;
  private wallPauseAt = 0;
  private lastT = 0;
  private stopped = false;

  private onVisibility = (): void => {
    if (document.hidden && this.state === 'rendering') {
      this.pause('检测到标签页隐藏，已自动暂停——回到本页后点「继续」');
    }
  };

  static supported(): boolean {
    return exporterSupported();
  }

  getState(): ExportState {
    return this.state;
  }

  async start(opts: ExportOptions): Promise<void> {
    if (this.state === 'rendering' || this.state === 'paused' || this.state === 'preparing') {
      throw new Error('已有导出任务在进行');
    }
    const picked = pickRecorderMime();
    if (!picked) throw new Error('当前浏览器不支持 MediaRecorder 视频录制');
    this.opts = opts;
    this.chunks = [];
    this.stopped = false;
    this.wallPausedTotal = 0;
    this.setState('preparing', '正在准备音频与录制器…');

    try {
      // 1. 离屏画布 + 渲染器
      const canvas = document.createElement('canvas');
      canvas.width = opts.width;
      canvas.height = opts.height;
      canvas.style.position = 'fixed';
      canvas.style.left = '-99999px';
      document.body.appendChild(canvas);
      this.canvas = canvas;
      const ctx = canvas.getContext('2d', { alpha: false });
      if (!ctx) throw new Error('无法创建 2D 画布上下文');
      const renderer = createVgRenderer(opts.timeline);

      // 2. 音频混音（可选）
      this.audio = await AudioMixer.create({
        bgm: opts.bgm,
        voice: opts.voice,
        duration: opts.timeline.duration,
        volume: opts.volume,
        fadeIn: 1.2,
        fadeOut: 1.6,
      });

      // 3. 合流 + 录制器
      const stream = canvas.captureStream(opts.fps);
      if (this.audio) {
        for (const track of this.audio.dest.stream.getAudioTracks()) stream.addTrack(track);
      }
      const recorder = new MediaRecorder(stream, {
        mimeType: picked.mime,
        videoBitsPerSecond: bitrateFor(opts.height),
        audioBitsPerSecond: 128_000,
      });
      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) this.chunks.push(e.data);
      };
      recorder.onstop = () => this.assemble(picked);
      this.recorder = recorder;

      // 4. 标签页隐藏保护
      document.addEventListener('visibilitychange', this.onVisibility);

      // 5. 启动：先起录制器与时钟，再画首帧
      this.state = 'rendering';
      this.wallStart = performance.now();
      recorder.start(1000);
      this.audio?.start();
      this.emit();
      renderer.draw(ctx, 0, opts.width, opts.height);
      const total = opts.timeline.duration;

      const tick = (): void => {
        if (this.state !== 'rendering' || this.stopped) return;
        const t = this.audio
          ? this.audio.t
          : Math.max(0, (performance.now() - this.wallStart) / 1000 - this.wallPausedTotal);
        renderer.draw(ctx, Math.min(t, total), opts.width, opts.height);
        this.emit(t);
        if (t >= total - 1e-3) {
          this.finish();
          return;
        }
        this.raf = requestAnimationFrame(tick);
      };
      this.raf = requestAnimationFrame(tick);
    } catch (error) {
      this.cleanup();
      this.setState('error', error instanceof Error ? error.message : String(error));
      opts.onError?.(this.state === 'error' ? (error instanceof Error ? error.message : String(error)) : '导出失败');
      throw error;
    }
  }

  pause(message?: string): void {
    if (this.state !== 'rendering') return;
    if (this.audio) void this.audio.suspend();
    else this.wallPauseAt = performance.now();
    this.recorder?.pause();
    cancelAnimationFrame(this.raf);
    this.setState('paused', message ?? '已暂停');
  }

  resume(): void {
    if (this.state !== 'paused') return;
    if (this.audio) void this.audio.resume();
    else {
      // 墙钟按秒计，暂停时长需从毫秒换算
      this.wallPausedTotal += (performance.now() - this.wallPauseAt) / 1000;
    }
    this.recorder?.resume();
    this.setState('rendering');
    // 重新拉起 tick（复用 start 里的循环逻辑：直接 requestAnimationFrame 一次）
    const total = this.opts?.timeline.duration ?? 0;
    const ctx = this.canvas?.getContext('2d', { alpha: false });
    const opts = this.opts;
    if (!opts || !ctx) return;
    const renderer = createVgRenderer(opts.timeline);
    const tick = (): void => {
      if (this.state !== 'rendering' || this.stopped) return;
      const t = this.audio
        ? this.audio.t
        : Math.max(0, (performance.now() - this.wallStart) / 1000 - this.wallPausedTotal);
      renderer.draw(ctx, Math.min(t, total), opts.width, opts.height);
      this.emit(t);
      if (t >= total - 1e-3) {
        this.finish();
        return;
      }
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  cancel(): void {
    if (this.state !== 'rendering' && this.state !== 'paused') return;
    this.stopped = true;
    cancelAnimationFrame(this.raf);
    this.chunks = [];
    try {
      this.recorder?.stop();
    } catch {
      /* 已停止 */
    }
    this.setState('cancelled', '已取消导出');
    this.cleanup();
  }

  private finish(): void {
    if (this.stopped) return;
    this.stopped = true;
    cancelAnimationFrame(this.raf);
    this.setState('preparing', '正在封装文件…');
    try {
      this.recorder?.stop();
    } catch {
      this.assemble(pickRecorderMime() ?? { mime: 'video/webm', ext: 'webm' });
    }
  }

  private assemble(picked: { mime: string; ext: string }): void {
    if (this.state === 'cancelled' || this.chunks.length === 0) {
      this.cleanup();
      return;
    }
    const blob = new Blob(this.chunks, { type: picked.mime.split(';')[0] });
    this.setState('done', `导出完成 · ${(blob.size / 1024 / 1024).toFixed(1)} MB`);
    this.opts?.onDone?.({ blob, ext: picked.ext, mime: picked.mime });
    this.cleanup();
  }

  private setState(state: ExportState, message?: string): void {
    this.state = state;
    this.emit(undefined, message);
  }

  private emit(t?: number, message?: string): void {
    const opts = this.opts;
    if (!opts) return;
    if (t != null) this.lastT = t;
    opts.onProgress?.({
      t: t ?? this.lastT,
      total: opts.timeline.duration,
      state: this.state,
      message,
    });
  }

  private cleanup(): void {
    cancelAnimationFrame(this.raf);
    document.removeEventListener('visibilitychange', this.onVisibility);
    if (this.canvas) {
      this.canvas.remove();
      this.canvas = null;
    }
    void this.audio?.close();
    this.audio = null;
    this.recorder = null;
  }
}
