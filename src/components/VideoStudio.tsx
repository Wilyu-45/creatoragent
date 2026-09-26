/**
 * 视频工场 —— 字幕 → 智能视频生成的完整工作台。
 *
 * 流程：粘贴/拖入字幕（SRT/VTT/ASS/纯文本）→ 自动分幕建轴（去 AI 味随机化 +
 * 卡点吸附 + AI 味词 lint）→ DOM 预览台实时播放 → MediaRecorder 导出真实视频
 * （720p/1080p/4K，可暂停/取消，BGM + 旁白混音闪避）。
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { usePlayerClock, type PlayerClock } from '../motion/engine.tsx';
import {
  buildTimeline,
  CUE_FX_LABEL,
  FILTER_LABEL,
  THEMES,
  TRANSITION_LABEL,
  type BuildOptions,
  type CueFx,
  type FilterId,
  type ThemeId,
  type TransitionId,
} from '../vg/timeline.ts';
import { parseSubtitles, type SubtitleFormat } from '../vg/subtitles.ts';
import { VgStage } from '../vg/stage.tsx';
import { exporterSupported, VideoExporter, type ExportProgress } from '../vg/exporter.ts';

/* ------------------------------ 示例字幕 ------------------------------ */

const EXAMPLE_SUBTITLES = `1
00:00:00,400 --> 00:00:03,400
《玩乐关系》第四卷杂谈，先说结论：这一卷是规则的胜利。

2
00:00:03,700 --> 00:00:06,900
「匣庭」游戏进入第二轮，Kingmaker 的筹码开始流动。

3
00:00:07,200 --> 00:00:10,000
牌桌上的每一次沉默，都是一次定价。

4
00:00:13,000 --> 00:00:16,200
前情提要：信件在第三幕被调包，读者其实早看过答案。

5
00:00:16,500 --> 00:00:19,600
三个人的同盟，在第 7 回合出现第一道裂缝。

6
00:00:19,900 --> 00:00:22,800
胜率 62% 的玩家，输给了愿意掀桌的人。

7
00:00:26,000 --> 00:00:29,200
反转不靠巧合，靠的是规则里早就写好的伏笔。

8
00:00:29,500 --> 00:00:32,400
首先，她让所有人都以为自己在赢。

9
00:00:32,700 --> 00:00:35,900
其次，真正的赌注从来不在桌面上。

10
00:00:36,200 --> 00:00:39,600
下一期，我们拆解终局的三个细节。记得关注。
`;

/* ------------------------------ 设置 ------------------------------ */

interface Settings {
  theme: ThemeId;
  filter: FilterId | 'auto';
  transition: TransitionId | 'auto';
  cueFx: CueFx | 'auto';
  title: string;
  watermark: string;
  beatsText: string;
  grain: number;
  vignette: number;
  highlight: boolean;
  showTitles: boolean;
  titleCard: boolean;
  humanize: boolean;
  beatPulse: boolean;
}

const DEFAULT_SETTINGS: Settings = {
  theme: 'aurora',
  filter: 'auto',
  transition: 'auto',
  cueFx: 'auto',
  title: '',
  watermark: '',
  beatsText: '',
  grain: 0.6,
  vignette: 0.55,
  highlight: true,
  showTitles: true,
  titleCard: true,
  humanize: true,
  beatPulse: true,
};

const RESOLUTIONS: Record<string, { w: number; h: number; label: string; note: string }> = {
  '720p': { w: 1280, h: 720, label: '720p', note: '流畅' },
  '1080p': { w: 1920, h: 1080, label: '1080p', note: '推荐' },
  '4k': { w: 3840, h: 2160, label: '4K', note: '依赖设备性能' },
};

/* ------------------------------ 播放控制条 ------------------------------ */

function ControlBar({ clock, total }: { clock: PlayerClock; total: number }) {
  return (
    <div className="motion-controls">
      <button className="btn btn-sm" onClick={clock.reset} title="回到起点并重置场景">
        ⏮
      </button>
      <button className="btn btn-sm btn-primary" onClick={clock.toggle} style={{ minWidth: 92 }}>
        {clock.playing ? '⏸ 暂停' : '▶ 播放'}
      </button>
      <input
        type="range"
        min={0}
        max={total}
        step={0.05}
        value={clock.t}
        onChange={(e) => clock.seek(Number(e.target.value))}
        style={{ flex: 1, minWidth: 140 }}
      />
      <span className="mono small" style={{ whiteSpace: 'nowrap' }}>
        {clock.t.toFixed(1)}s / {total.toFixed(1)}s
      </span>
      <select value={clock.speed} onChange={(e) => clock.setSpeed(Number(e.target.value))} style={{ width: 84 }}>
        {[0.5, 0.75, 1, 1.5, 2].map((s) => (
          <option key={s} value={s}>
            {s}×
          </option>
        ))}
      </select>
    </div>
  );
}

/* ------------------------------ 面板 ------------------------------ */

export function VideoStudio({ onBack }: { onBack: () => void }) {
  const [subtitleText, setSubtitleText] = useState('');
  const [subtitleName, setSubtitleName] = useState('');
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 1e9));
  const [bgm, setBgm] = useState<File | null>(null);
  const [voice, setVoice] = useState<File | null>(null);
  const [volume, setVolume] = useState(0.55);
  const [resolution, setResolution] = useState<'720p' | '1080p' | '4k'>('1080p');
  const [fps, setFps] = useState(30);
  const [progress, setProgress] = useState<ExportProgress | null>(null);
  const [result, setResult] = useState<{ url: string; name: string; size: number } | null>(null);
  const exporterRef = useRef<VideoExporter | null>(null);

  const supported = useMemo(() => exporterSupported(), []);

  const parsed = useMemo(() => {
    if (subtitleText.trim().length === 0) return { cues: [], format: null as SubtitleFormat | null, error: null as string | null };
    try {
      const r = parseSubtitles(subtitleText, subtitleName);
      return { cues: r.cues, format: r.format as SubtitleFormat, error: null };
    } catch (error) {
      return { cues: [], format: null, error: (error as Error).message };
    }
  }, [subtitleText, subtitleName]);

  const timeline = useMemo(() => {
    if (parsed.cues.length === 0) return null;
    const beats = settings.beatsText
      .split(/[，,\s]+/)
      .filter((s) => s.length > 0)
      .map(Number)
      .filter((n) => Number.isFinite(n) && n >= 0);
    const opts: BuildOptions = {
      title: settings.title,
      watermark: settings.watermark,
      theme: settings.theme,
      filter: settings.filter,
      transition: settings.transition,
      cueFx: settings.cueFx,
      highlight: settings.highlight,
      showTitles: settings.showTitles,
      titleCard: settings.titleCard,
      humanize: settings.humanize,
      beatPulse: settings.beatPulse,
      beats,
      grain: settings.grain,
      vignette: settings.vignette,
      seed,
    };
    return buildTimeline(parsed.cues, opts);
  }, [parsed, settings, seed]);

  const clock = usePlayerClock(timeline?.duration ?? 1);

  // 卸载时清理导出器与对象 URL
  useEffect(() => {
    return () => {
      exporterRef.current?.cancel();
      exporterRef.current = null;
    };
  }, []);
  useEffect(() => () => {
    if (result) URL.revokeObjectURL(result.url);
  }, [result]);

  const patch = (p: Partial<Settings>) => setSettings((prev) => ({ ...prev, ...p }));

  const loadFile = async (file: File): Promise<void> => {
    const text = await file.text();
    setSubtitleText(text);
    setSubtitleName(file.name);
  };

  const rebuild = () => {
    setSeed(Math.floor(Math.random() * 1e9));
    clock.reset();
  };

  const startExport = async (): Promise<void> => {
    if (!timeline) return;
    const dim = RESOLUTIONS[resolution];
    exporterRef.current?.cancel();
    const exporter = new VideoExporter();
    exporterRef.current = exporter;
    if (result) {
      URL.revokeObjectURL(result.url);
      setResult(null);
    }
    setProgress({ t: 0, total: timeline.duration, state: 'preparing', message: '正在准备…' });
    try {
      await exporter.start({
        timeline,
        width: dim.w,
        height: dim.h,
        fps,
        bgm: bgm ?? null,
        voice: voice ?? null,
        volume,
        onProgress: (p) => setProgress(p),
        onDone: (r) => {
          setResult({ url: URL.createObjectURL(r.blob), name: `${(settings.title || timeline.title || '视频工场').slice(0, 24)}.${r.ext}`, size: r.blob.size });
          setProgress(null);
          exporterRef.current = null;
        },
        onError: () => setProgress(null),
      });
    } catch (error) {
      setProgress({ t: 0, total: timeline.duration, state: 'error', message: (error as Error).message });
    }
  };

  const exporting = progress != null && ['preparing', 'rendering', 'paused'].includes(progress.state);

  return (
    <div className="vs-studio">
      <div className="motion-head">
        <button className="btn btn-sm btn-ghost" onClick={onBack}>
          ← 返回任务台
        </button>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>视频工场</div>
          <div className="muted small">
            字幕 → 智能分幕 → 动效成片：卡点切镜 ±0.1s、18 项剪辑操作、去 AI 味随机化，导出真实视频文件
          </div>
        </div>
      </div>

      <div className="vs-grid">
        {/* ---------------- 左侧：素材与设置 ---------------- */}
        <aside className="vs-side">
          <div className="panel">
            <div className="panel-title">
              字幕
              {parsed.format ? (
                <span className="count">· {parsed.format.toUpperCase()} · {parsed.cues.length} 条</span>
              ) : null}
            </div>
            <div className="row" style={{ gap: 8, marginBottom: 8 }}>
              <label className="btn btn-sm btn-ghost" style={{ cursor: 'pointer' }}>
                打开文件
                <input
                  type="file"
                  accept=".srt,.vtt,.ass,.ssa,.txt"
                  style={{ display: 'none' }}
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) void loadFile(file);
                    e.target.value = '';
                  }}
                />
              </label>
              <button className="btn btn-sm btn-ghost" onClick={() => { setSubtitleText(EXAMPLE_SUBTITLES); setSubtitleName('示例.srt'); }}>
                载入示例
              </button>
              <button className="btn btn-sm btn-ghost" onClick={() => { setSubtitleText(''); setSubtitleName(''); }}>
                清空
              </button>
            </div>
            <textarea
              className="vs-subtitle"
              value={subtitleText}
              onChange={(e) => setSubtitleText(e.target.value)}
              placeholder={'粘贴字幕内容，支持 SRT / WebVTT / ASS / 纯文本（纯文本按标点自动断句与计时）'}
              spellCheck={false}
            />
            {parsed.error ? <div className="motion-error">{parsed.error}</div> : null}
            {timeline && timeline.lint.length > 0 ? (
              <div className="vs-lint">
                <div className="small" style={{ marginBottom: 4 }}>
                  <strong>AI 味提示</strong>（建议改写后重新粘贴）
                </div>
                {timeline.lint.slice(0, 8).map((issue, i) => (
                  <div key={i} className="vs-lint-item small">
                    「{issue.word}」· {issue.hint}
                  </div>
                ))}
              </div>
            ) : null}
          </div>

          <div className="panel">
            <div className="panel-title">音频</div>
            <div className="field">
              <label>BGM（循环播放，自动淡入淡出）</label>
              <input type="file" accept="audio/*" onChange={(e) => setBgm(e.target.files?.[0] ?? null)} />
              {bgm ? <div className="muted small">{bgm.name}</div> : null}
            </div>
            <div className="field">
              <label>旁白配音（可选，出现时 BGM 自动压低 65%）</label>
              <input type="file" accept="audio/*" onChange={(e) => setVoice(e.target.files?.[0] ?? null)} />
              {voice ? <div className="muted small">{voice.name}</div> : null}
            </div>
            <div className="field">
              <label>BGM 音量 {(volume * 100).toFixed(0)}%</label>
              <input type="range" min={0} max={1} step={0.05} value={volume} onChange={(e) => setVolume(Number(e.target.value))} />
            </div>
          </div>

          <div className="panel">
            <div className="panel-title">风格</div>
            <div className="vs-form-grid">
              <div className="field">
                <label>主题</label>
                <select value={settings.theme} onChange={(e) => patch({ theme: e.target.value as ThemeId })}>
                  {Object.entries(THEMES).map(([id, t]) => (
                    <option key={id} value={id}>{t.label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>调色</label>
                <select value={settings.filter} onChange={(e) => patch({ filter: e.target.value as FilterId | 'auto' })}>
                  <option value="auto">智能轮换</option>
                  {Object.entries(FILTER_LABEL).map(([id, label]) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>转场</label>
                <select value={settings.transition} onChange={(e) => patch({ transition: e.target.value as TransitionId | 'auto' })}>
                  <option value="auto">智能轮换</option>
                  {Object.entries(TRANSITION_LABEL).map(([id, label]) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>字幕动效</label>
                <select value={settings.cueFx} onChange={(e) => patch({ cueFx: e.target.value as CueFx | 'auto' })}>
                  <option value="auto">智能轮换</option>
                  {Object.entries(CUE_FX_LABEL).map(([id, label]) => (
                    <option key={id} value={id}>{label}</option>
                  ))}
                </select>
              </div>
              <div className="field">
                <label>作品标题（空 = 取首句）</label>
                <input value={settings.title} onChange={(e) => patch({ title: e.target.value })} placeholder={timeline ? `默认：${timeline.title}` : ''} />
              </div>
              <div className="field">
                <label>水印 / 角标</label>
                <input value={settings.watermark} onChange={(e) => patch({ watermark: e.target.value })} placeholder="如 @我的频道" />
              </div>
              <div className="field">
                <label>节拍点（秒，逗号分隔；场景边界自动吸附）</label>
                <input value={settings.beatsText} onChange={(e) => patch({ beatsText: e.target.value })} placeholder="如 3.4, 13.0, 26.0" />
              </div>
              <div className="field">
                <label>胶片颗粒 {(settings.grain * 100).toFixed(0)}%</label>
                <input type="range" min={0} max={1} step={0.05} value={settings.grain} onChange={(e) => patch({ grain: Number(e.target.value) })} />
              </div>
              <div className="field">
                <label>暗角 {(settings.vignette * 100).toFixed(0)}%</label>
                <input type="range" min={0} max={1} step={0.05} value={settings.vignette} onChange={(e) => patch({ vignette: Number(e.target.value) })} />
              </div>
            </div>
            <div className="row" style={{ gap: 14, flexWrap: 'wrap', marginTop: 4 }}>
              {([
                ['highlight', '关键词高亮'],
                ['showTitles', '幕别章标'],
                ['titleCard', '标题卡'],
                ['humanize', '去 AI 味随机化'],
                ['beatPulse', '节拍脉冲'],
              ] as [keyof Settings, string][]).map(([key, label]) => (
                <label key={key} className="small" style={{ display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
                  <input
                    type="checkbox"
                    checked={Boolean(settings[key])}
                    onChange={(e) => patch({ [key]: e.target.checked } as Partial<Settings>)}
                    style={{ width: 'auto' }}
                  />
                  {label}
                </label>
              ))}
            </div>
            <button className="btn btn-sm" style={{ marginTop: 10 }} onClick={rebuild} disabled={!timeline}>
              🎲 换一批动效（重新随机）
            </button>
          </div>

          <div className="panel">
            <div className="panel-title">导出</div>
            {!supported ? (
              <div className="motion-error">当前浏览器不支持 MediaRecorder 视频录制，请使用新版 Chrome / Edge。</div>
            ) : (
              <>
                <div className="vs-form-grid">
                  <div className="field">
                    <label>分辨率</label>
                    <select value={resolution} onChange={(e) => setResolution(e.target.value as typeof resolution)} disabled={exporting}>
                      {Object.entries(RESOLUTIONS).map(([key, r]) => (
                        <option key={key} value={key}>{r.label}（{r.note}）</option>
                      ))}
                    </select>
                  </div>
                  <div className="field">
                    <label>帧率</label>
                    <select value={fps} onChange={(e) => setFps(Number(e.target.value))} disabled={exporting}>
                      {[24, 30, 60].map((f) => (
                        <option key={f} value={f}>{f} fps</option>
                      ))}
                    </select>
                  </div>
                </div>
                {!exporting ? (
                  <button className="btn btn-primary" style={{ width: '100%' }} onClick={() => void startExport()} disabled={!timeline}>
                    ⏺ 开始导出（实时编码，约 {timeline ? Math.round(timeline.duration) : 0}s）
                  </button>
                ) : (
                  <div className="vs-export-bar">
                    <div className="vs-progress">
                      <div
                        className="vs-progress-fill"
                        style={{ width: `${progress ? Math.min(100, (progress.t / Math.max(progress.total, 0.1)) * 100).toFixed(1) : 0}%` }}
                      />
                    </div>
                    <div className="row" style={{ justifyContent: 'space-between', marginTop: 6 }}>
                      <span className="mono small">
                        {progress?.state === 'paused' ? '已暂停' : progress?.state === 'preparing' ? '准备中…' : '录制中'}{' '}
                        {(progress?.t ?? 0).toFixed(1)}s / {(progress?.total ?? 0).toFixed(1)}s
                      </span>
                      <span className="row" style={{ gap: 6 }}>
                        {progress?.state === 'paused' ? (
                          <button className="btn btn-sm btn-primary" onClick={() => exporterRef.current?.resume()}>继续</button>
                        ) : (
                          <button className="btn btn-sm" onClick={() => exporterRef.current?.pause()} disabled={progress?.state !== 'rendering'}>暂停</button>
                        )}
                        <button className="btn btn-sm btn-bad" onClick={() => { exporterRef.current?.cancel(); }}>取消</button>
                      </span>
                    </div>
                    {progress?.message ? <div className="muted small" style={{ marginTop: 4 }}>{progress.message}</div> : null}
                  </div>
                )}
                {progress?.state === 'error' ? <div className="motion-error" style={{ marginTop: 8 }}>{progress.message}</div> : null}
                {progress?.state === 'cancelled' ? <div className="muted small" style={{ marginTop: 8 }}>已取消，可重新导出。</div> : null}
                {result ? (
                  <div className="vs-result">
                    <div className="small" style={{ marginBottom: 6 }}>
                      完成 · {(result.size / 1024 / 1024).toFixed(1)} MB · 实时编码完成
                    </div>
                    <a className="btn btn-ok" style={{ width: '100%', textAlign: 'center' }} href={result.url} download={result.name}>
                      ⬇ 下载 {result.name}
                    </a>
                  </div>
                ) : null}
                <div className="muted small" style={{ marginTop: 8, lineHeight: 1.6 }}>
                  导出为实时编码（MP4 优先，浏览器不支持时回退 WebM）。导出期间请保持本页在前台——切到后台会自动暂停；
                  跨会话续传因容器格式限制不可靠，中断后请重新导出。4K 分辨率对设备性能要求较高。
                </div>
              </>
            )}
          </div>
        </aside>

        {/* ---------------- 右侧：预览台 ---------------- */}
        <section className="vs-main">
          <div className="vs-stage">
            {timeline ? (
              <VgStage tl={timeline} t={clock.t} generation={clock.generation} />
            ) : (
              <div className="vs-empty">
                <div style={{ fontSize: 15, marginBottom: 6 }}>左侧粘贴字幕或点「载入示例」开始</div>
                <div className="muted small">自动分幕 · 动效轮换 · 卡点切镜 · 关键词高亮 · 真实导出</div>
              </div>
            )}
            {timeline && clock.ended ? (
              <div className="motion-ended">
                <div className="motion-ended-title">已播完</div>
                <div className="motion-ended-btns">
                  <button className="btn btn-sm btn-primary" onClick={clock.play}>↻ 重新播放</button>
                  <button className="btn btn-sm btn-ghost" onClick={clock.reset}>⌂ 回到起点</button>
                </div>
              </div>
            ) : null}
          </div>
          <ControlBar clock={clock} total={timeline?.duration ?? 0} />
          {timeline ? (
            <div className="vs-ops">
              <span className="muted small" style={{ marginRight: 4 }}>剪辑操作（{timeline.ops.length}）：</span>
              {timeline.ops.map((op) => (
                <span key={op} className="chip tone-info" style={{ fontSize: 11 }}>{op}</span>
              ))}
            </div>
          ) : null}
          {timeline ? (
            <div className="vs-meta muted small">
              {timeline.scenes.length} 幕 · {timeline.cues.length} 条字幕 · 总时长 {timeline.duration.toFixed(1)}s
              {timeline.beats.length > 0 ? ` · ${timeline.beats.length} 个节拍点（边界吸附偏差 0）` : ''}
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}
