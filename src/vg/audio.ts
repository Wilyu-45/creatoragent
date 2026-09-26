/**
 * WebAudio 混音器 —— 导出时的 BGM / 旁白合成与主时钟。
 *
 * 设计：以 AudioContext.currentTime 为唯一时间基准——suspend() 时音频与
 * 计时同时冻结，因此「暂停/继续导出」天然保持音画同步，无需手动补偿。
 * 增益包络在 start() 时按相对时间排布（避免绝对时间漂移）。
 */

export interface AudioMixOptions {
  bgm?: File | null;
  voice?: File | null;
  /** 视频时长（秒）。 */
  duration: number;
  /** BGM 音量 0..1。 */
  volume: number;
  /** 淡入秒数。 */
  fadeIn: number;
  /** 淡出秒数。 */
  fadeOut: number;
}

interface PendingSource {
  src: AudioBufferSourceNode;
  gain: GainNode;
  /** 从播放起点起算的停止秒数。 */
  stopAt: number;
  /** BGM 淡入淡出包络（相对播放起点）。 */
  envelope?: { fadeIn: number; fadeOut: number; volume: number; duration: number };
}

export class AudioMixer {
  readonly ctx: AudioContext;
  readonly dest: MediaStreamAudioDestinationNode;
  private startedAt = 0;
  private pending: PendingSource[] = [];

  private constructor(ctx: AudioContext, dest: MediaStreamAudioDestinationNode) {
    this.ctx = ctx;
    this.dest = dest;
  }

  static async create(opts: AudioMixOptions): Promise<AudioMixer | null> {
    if (!opts.bgm && !opts.voice) return null;
    const ctx = new AudioContext();
    const dest = ctx.createMediaStreamDestination();
    const master = ctx.createGain();
    master.gain.value = 1;
    master.connect(dest);
    master.connect(ctx.destination); // 导出时同步监听

    const vol = Math.min(1, Math.max(0, opts.volume));
    const mixer = new AudioMixer(ctx, dest);

    if (opts.bgm) {
      const buffer = await ctx.decodeAudioData(await opts.bgm.arrayBuffer());
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.loop = buffer.duration < opts.duration - 0.1;
      const gain = ctx.createGain();
      // 有旁白时 BGM 自动闪避（ducking）：恒定压低到 35%
      const bgmVol = opts.voice ? vol * 0.35 : vol;
      src.connect(gain).connect(master);
      mixer.pending.push({
        src,
        gain,
        stopAt: opts.duration,
        envelope: { fadeIn: opts.fadeIn, fadeOut: opts.fadeOut, volume: bgmVol, duration: opts.duration },
      });
    }

    if (opts.voice) {
      const buffer = await ctx.decodeAudioData(await opts.voice.arrayBuffer());
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      const gain = ctx.createGain();
      gain.gain.value = 1.0;
      src.connect(gain).connect(master);
      mixer.pending.push({ src, gain, stopAt: opts.duration });
    }

    return mixer;
  }

  /** 启动全部源并按相对时间排布 BGM 包络。 */
  start(): void {
    const at = this.ctx.currentTime + 0.08;
    this.startedAt = at;
    for (const item of this.pending) {
      if (item.envelope) {
        const { fadeIn, fadeOut, volume, duration } = item.envelope;
        const g = item.gain.gain;
        g.setValueAtTime(0.0001, at);
        if (fadeIn > 0.01) g.linearRampToValueAtTime(volume, at + fadeIn);
        else g.setValueAtTime(volume, at);
        const fo = Math.max(fadeIn + 0.1, duration - fadeOut);
        g.setValueAtTime(volume, at + fo);
        g.linearRampToValueAtTime(0.0001, at + Math.max(fo + 0.05, duration));
      }
      item.src.start(at);
      item.src.stop(at + item.stopAt + 0.05);
    }
  }

  /** 播放头（秒），以音频时钟为准。 */
  get t(): number {
    return Math.max(0, this.ctx.currentTime - this.startedAt);
  }

  suspend(): Promise<void> {
    return this.ctx.suspend();
  }

  resume(): Promise<void> {
    return this.ctx.resume();
  }

  async close(): Promise<void> {
    for (const item of this.pending) {
      try {
        item.src.stop();
      } catch {
        /* 已停止 */
      }
    }
    this.pending = [];
    try {
      await this.ctx.close();
    } catch {
      /* 已关闭 */
    }
  }
}
