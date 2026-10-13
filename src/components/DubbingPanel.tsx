import { useCallback, useEffect, useRef, useState } from 'react';
import type { DubbingJobView, DubbingSegment, DubbingView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Spinner, Table } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 作业状态 → 展示文案与色调。 */
const JOB_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  synthesizing: { label: '合成中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

/** 通道 → 展示名（引擎通道出真音频；sample 仅供联调回归）。 */
const PROVIDER_LABEL: Record<string, string> = {
  sample: 'sample 模拟通道',
  auto: 'auto 自动择优',
  sapi: 'sapi 系统音色（真音频，不克隆）',
  sovits: 'sovits 自建克隆（真音频）',
  elevenlabs: 'elevenlabs 云端克隆（真音频）',
  real: 'real 配音网关',
};

const ACTIVE = new Set(['queued', 'synthesizing']);

/** 把浏览器本地文件读成 base64（去掉 data URL 前缀，后端自会解码）。 */
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result ?? '').split(',', 2)[1] ?? '');
    reader.onerror = () => reject(new Error(`读取文件失败：${file.name}`));
    reader.readAsDataURL(file);
  });
}

/** 一行「说话人 → 参考音频」的编辑控件（上传到 assets/ 或直接填地址）。 */
function VoiceRow({
  taskId,
  speaker,
  voiceRef,
  onChange,
  onToast,
}: {
  taskId: string;
  speaker: string;
  /** 当前参考音频引用；不叫 ref——那是 React 的保留 prop */
  voiceRef: string;
  onChange: (ref: string) => void;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [previewUrl, setPreviewUrl] = useState('');
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  const upload = async (file: File) => {
    setBusy(true);
    try {
      const data = await readFileAsBase64(file);
      const saved = await api.uploadDubbingVoice(file.name, data);
      onChange(saved.ref);
      onToast(`参考音频已上传：${saved.ref}`);
    } catch (error) {
      onToast(`上传失败：${errorText(error)}`, true);
    } finally {
      setBusy(false);
    }
  };

  /** 试听走带鉴权头的路由取字节，裸链接（assets/ 不在静态目录）拿不到。 */
  const preview = async () => {
    if (!voiceRef || voiceRef.startsWith('http')) return;
    if (previewUrl) {
      setPreviewUrl('');
      return;
    }
    setBusy(true);
    try {
      const blob = await api.dubbingVoice(taskId, voiceRef);
      setPreviewUrl(URL.createObjectURL(blob));
    } catch (error) {
      onToast(`试听失败：${errorText(error)}`, true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="row" style={{ gap: 8, alignItems: 'center', marginBottom: 6 }}>
      <span className="mono small" style={{ width: 88, flexShrink: 0 }} title="说话人">
        {speaker}
      </span>
      <input
        value={voiceRef}
        placeholder="assets/ 文件名或 https://… 音频地址"
        onChange={(event) => onChange(event.target.value)}
        style={{ flex: 1 }}
      />
      <button
        className="btn btn-sm btn-ghost"
        disabled={busy}
        title="选择本地音频文件上传到 assets/"
        onClick={() => fileInput.current?.click()}
      >
        {busy ? <Spinner /> : null} 上传
      </button>
      <input
        ref={fileInput}
        type="file"
        accept=".wav,.mp3,.m4a,.aac,.ogg,.flac,audio/*"
        style={{ display: 'none' }}
        onChange={(event) => {
          const file = event.target.files?.[0];
          if (file) void upload(file);
          event.target.value = '';
        }}
      />
      {voiceRef && !voiceRef.startsWith('http') ? (
        <button className="btn btn-sm btn-ghost" onClick={() => void preview()}>
          {previewUrl ? '收起' : '试听'}
        </button>
      ) : null}
      {previewUrl ? <audio controls src={previewUrl} style={{ height: 28 }} /> : null}
    </div>
  );
}

/**
 * 全自动配音面板（**真实功能**：引擎逐句合成真音频）。
 *
 * 台词切分与说话人识别在系统内真做；声音合成按通道交给引擎：
 * - `sapi`：Windows 系统音色本机真合成（零依赖零费用，**不消费参考音频做克隆**，
 *   汇总里如实标注）；`sovits`：自建 GPT-SoVITS 类服务，参考音频零样本克隆；
 * - `elevenlabs`：云端 Instant Voice Clone（上传参考音频建音色再逐句合成）；
 * - `real`：提交你自己的配音网关；`auto`：按 elevenlabs→sovits→sapi→real 择优；
 * - `sample`：离线模拟通道（不出真音频，专供联调/回归）。
 * 输入：剧情文本（「说话人：台词」「【说话人】台词」、小说式「甲说：“台词”」
 * /「「台词」乙低声说。」都认；无标记行归旁白如实标注）+ 各说话人参考音频。
 * 完成后逐句可试听，时间轴按音频实长重建，可下载整轨与时间轴 txt。
 * 选定「译制目标语言」时走**跨语言译制配音**：先用系统 LLM 网关逐句译成目标
 * 语言再合成，原句留在每段 source_text（离线引擎只能产出占位译文，如实标注）。
 */
export function DubbingPanel({
  task,
  onToast,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<DubbingView | null>(null);
  const [working, setWorking] = useState('');
  const [script, setScript] = useState('');
  const [voices, setVoices] = useState<{ speaker: string; ref: string }[]>([
    { speaker: '旁白', ref: '' },
  ]);
  const [provider, setProvider] = useState('');
  const [targetLang, setTargetLang] = useState('');
  const [langs, setLangs] = useState<{ code: string; name: string }[]>([]);
  const pollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.dubbing(task.id);
      setView(data);
      // 有活跃作业时加快轮询，全部终态后停表（惰性推进由后端读取时计算）
      if (data.jobs.some((job) => ACTIVE.has(job.status))) schedulePoll();
    } catch {
      /* 读取失败不阻塞主流程 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id]);

  // 目标语言清单（后端支持哪些语种）只需拉一次
  useEffect(() => {
    void api
      .dubbingLanguages()
      .then((res) => setLangs(res.languages))
      .catch(() => setLangs([]));
  }, []);

  const schedulePoll = useCallback(() => {
    if (pollTimer.current) return;
    pollTimer.current = window.setTimeout(() => {
      pollTimer.current = null;
      void load();
    }, 2000);
  }, [load]);

  useEffect(() => {
    setView(null);
    void load();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [load]);

  const jobs = view?.jobs ?? [];

  /** 从剧情文本里即时提取说话人列表（口径对齐后端 parse_script：冒号/括号前缀、
   *  「甲说：」前置与「「台词」乙说。」后置），与已录入的音频映射合并（不丢已上传的引用）。 */
  const detectedSpeakers = (() => {
    const tone = '(?:轻声|低声|大声|冷冷|淡淡|缓缓|急忙)?';
    const verb = '(?:说道|问道|喊道|叫道|答道|笑道|开口说|开口|说|道|问|喊|叫|答|笑|吼|念|喃喃)';
    const verbHead = new RegExp(`^\\s*([^：:】)\\s]{1,12}?)${tone}${verb}\\s*[：:]`);
    const quoteTail = new RegExp(
      `^\\s*[“"「『](.+?)[”"」』]\\s*[，,]?\\s*([\\u4e00-\\u9fa5A-Za-z0-9_·]{1,12}?)${tone}${verb}\\s*[。！!？?…]*\\s*$`,
    );
    const clean = (name: string) => name.replace(/[（(].*$/, '').trim();
    // 与后端 _is_narration_name 同防护：名字段混着「了/着/过」= 叙述句，不冒充说话人
    const narration = (name: string) => /[了着过]/.test(name);
    const found = new Set<string>();
    for (const raw of script.split(/\r?\n/)) {
      const line = raw.trim().replace(/^\s*[-*·>]+\s*/, '');
      if (!line) continue;
      const head = verbHead.exec(line);
      if (head?.[1]?.trim() && !narration(head[1])) {
        found.add(clean(head[1]));
        continue;
      }
      const tail = quoteTail.exec(line);
      if (tail?.[2] && !narration(tail[2])) {
        found.add(clean(tail[2]));
        continue;
      }
      const match = /^[（(【\[]?([^：:】)）\]]{1,24}?)\s*[）)】\]]?\s*[：:]/.exec(line);
      if (match && match[1].trim() && !narration(match[1])) found.add(clean(match[1]));
      else if (/^[【[(]/.test(line)) {
        const bracket = /^[【[(]([^)】\]]{1,24}?)[】\])]/.exec(line);
        if (bracket && !narration(bracket[1])) found.add(clean(bracket[1]));
      }
    }
    if (found.size === 0 && script.trim()) found.add('旁白');
    return [...found];
  })();

  const mergedVoices = detectedSpeakers.map((speaker) => {
    const existing = voices.find((item) => item.speaker === speaker);
    return existing ?? { speaker, ref: '' };
  });

  const setVoiceRef = (speaker: string, ref: string) => {
    setVoices((prev) => {
      const others = prev.filter((item) => item.speaker !== speaker && !detectedSpeakers.includes(item.speaker));
      const current = mergedVoices.map((item) =>
        item.speaker === speaker ? { speaker, ref } : item,
      );
      return [...current, ...others];
    });
  };

  const createJob = async () => {
    const voiceMap: Record<string, string> = {};
    for (const item of mergedVoices) {
      if (item.ref.trim()) voiceMap[item.speaker] = item.ref.trim();
    }
    setWorking('__create__');
    try {
      await api.createDubbing(task.id, {
        scriptText: script,
        voices: voiceMap,
        provider: provider || undefined,
        targetLang: targetLang || undefined,
      });
      onToast(
        targetLang
          ? `配音作业已创建（先译制为 ${targetLang} 再逐句合成）`
          : '配音作业已创建（识别台词与说话人后开始合成）',
      );
      await load();
    } catch (error) {
      onToast(`创建失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        全自动配音
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 识别说话人后逐句真合成音频，完成后按实长重建时间轴
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        输入剧情文本（每行 <code>说话人：台词</code>、<code>【说话人】台词</code>，小说式
        <code>甲说：“台词”</code> / <code>「台词」乙低声说。</code> 也认；完全没有标记时整段归旁白并如实标注）
        与各说话人的参考音频。引擎通道（<code>sapi</code> 本机系统音色 / <code>sovits</code> 自建克隆 /
        <code>elevenlabs</code> 云端克隆）逐句<strong>真合成并落盘</strong>，完成后可逐句试听、下载整轨；
        <code>auto</code> 按已配置引擎择优；缺 key/端点/参考音频时<strong>显式失败、不假装配音</strong>。
        选定<strong>译制目标语言</strong>时先逐句翻译成该语言再配音（<strong>跨语言译制</strong>，复用 LLM 网关），
        时间轴里原文与译配对照；离线引擎只能产出占位译文并如实标注。
      </div>

      <div className="field">
        <label>剧情文本</label>
        <textarea
          rows={6}
          value={script}
          placeholder={'甲：你终于来了。\n乙（低声）：别出声，跟我走。\n甲说：“从密道走。”\n「雨停了。」旁白道。\n没有标记的行会归入旁白。'}
          onChange={(event) => setScript(event.target.value)}
          style={{ width: '100%', fontFamily: 'inherit' }}
        />
      </div>

      <div className="field">
        <label>说话人参考音频（识别出的说话人会自动出现在这里）</label>
        {mergedVoices.map((item) => (
          <VoiceRow
            key={item.speaker}
            taskId={task.id}
            speaker={item.speaker}
            voiceRef={item.ref}
            onChange={(ref) => setVoiceRef(item.speaker, ref)}
            onToast={onToast}
          />
        ))}
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>配音通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sapi">sapi — 本机系统音色（Windows 零依赖真出声，不克隆）</option>
            <option value="sovits">sovits — 自建 GPT-SoVITS 克隆（参考音频→真克隆）</option>
            <option value="elevenlabs">elevenlabs — 云端声音克隆（需 API key）</option>
            <option value="real">real — 自定义配音网关（POST/GET 契约）</option>
            <option value="sample">sample — 离线模拟（不出真音频，联调用）</option>
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label title="跨语言译制：先逐句译成目标语言再合成（复用 LLM 网关）">译制目标语言</label>
          <select value={targetLang} onChange={(event) => setTargetLang(event.target.value)}>
            <option value="">不译制（直接配原文）</option>
            {langs.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {lang.code} — {lang.name}
              </option>
            ))}
          </select>
        </div>
        <button
          className="btn btn-primary"
          disabled={working !== '' || !script.trim()}
          onClick={() => void createJob()}
        >
          {working === '__create__' ? <Spinner /> : null} 创建配音作业
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有配音作业。创建后会先切分台词、识别说话人，再由引擎逐句合成真音频并给出时间轴汇总。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard key={job.id} taskId={task.id} job={job} onToast={onToast} />
      ))}
    </div>
  );
}

/** 单句配音音频：落盘真音频走带鉴权路由取字节试听；网关 URL 给链接；模拟地址只展示。 */
function SegmentAudio({
  taskId,
  jobId,
  segment,
  onToast,
}: {
  taskId: string;
  jobId: string;
  segment: DubbingSegment;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [url, setUrl] = useState('');
  const [busy, setBusy] = useState(false);

  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);

  const toggle = async () => {
    if (url) {
      setUrl('');
      return;
    }
    setBusy(true);
    try {
      const blob = await api.dubbingAudio(taskId, jobId, segment.audio_ref ?? '');
      setUrl(URL.createObjectURL(blob));
    } catch (error) {
      onToast(`试听失败：${errorText(error)}`, true);
    } finally {
      setBusy(false);
    }
  };

  if (segment.audio_ref) {
    return (
      <span className="row" style={{ gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-sm btn-ghost" disabled={busy} onClick={() => void toggle()}>
          {busy ? <Spinner /> : null} {url ? '收起' : '试听'}
        </button>
        {url ? <audio controls src={url} style={{ height: 28 }} /> : null}
        <span className="muted small mono" title={segment.audio_ref}>
          {segment.audio_ref.split('/').pop()}
        </span>
      </span>
    );
  }
  if (segment.audio_url?.startsWith('http')) {
    return (
      <a href={segment.audio_url} target="_blank" rel="noreferrer">
        音频
      </a>
    );
  }
  if (segment.audio_url) {
    return <code>{segment.audio_url}</code>;
  }
  return <span className="muted">—</span>;
}

/** 单个配音作业卡片：状态 / 进度 / 时间轴汇总（源文本 + 配音）/ 试听与下载。 */
function JobCard({
  taskId,
  job,
  onToast,
}: {
  taskId: string;
  job: DubbingJobView;
  onToast: (text: string, error?: boolean) => void;
}) {
  const meta = JOB_META[job.status] ?? { label: job.status, tone: 'tone-idle' };
  const lastNote = job.history.length ? job.history[job.history.length - 1].note : '';
  const summary = job.summary;
  const segments = summary?.segments ?? job.manifest.segments ?? [];
  const translated = Boolean(summary?.translated ?? job.translated) || segments.some((s) => s.source_text);
  const srcLabel = job.source_lang ?? summary?.source_lang ?? '';
  const tgtLabel = job.target_lang ?? summary?.target_lang ?? '';

  const download = async () => {
    try {
      await api.dubbingTimeline(taskId, job.id);
      onToast('时间轴汇总已下载');
    } catch (error) {
      onToast(`下载失败：${errorText(error)}`, true);
    }
  };

  const downloadFull = async () => {
    if (!summary?.full_audio) return;
    try {
      await api.dubbingAudioDownload(taskId, job.id, summary.full_audio, `dubbing-${job.id}-full.wav`);
      onToast('整轨配音已下载');
    } catch (error) {
      onToast(`整轨下载失败：${errorText(error)}`, true);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        className="card-title"
        style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
      >
        <span className="row" style={{ gap: 8 }}>
          <Chip tone={meta.tone}>{meta.label}</Chip>
          <Chip tone="tone-idle">{PROVIDER_LABEL[job.provider] ?? job.provider}</Chip>
          <Chip tone="tone-idle" mono title={`作业 ID ${job.id}`}>
            {job.line_count} 句 · {job.speakers.length} 人 · {job.duration_seconds}s
          </Chip>
          {job.estimated_cost_usd > 0 ? (
            <Chip tone="tone-warn" mono title="按引擎计费口径保守估算（事前熔断用）">
              预估 ${job.estimated_cost_usd.toFixed(3)}
            </Chip>
          ) : null}
          {summary?.simulated ? <Chip tone="tone-idle">模拟音频</Chip> : null}
          {summary && !summary.simulated ? <Chip tone="tone-ok">真音频</Chip> : null}
          {translated ? (
            <Chip tone="tone-warn" title="跨语言译制配音：先逐句译成目标语言再合成">
              译制 {srcLabel || '原'}→{tgtLabel || '译'}
              {summary?.translation_simulated || job.translation_simulated ? '（占位）' : ''}
            </Chip>
          ) : null}
        </span>
        <span className="muted small mono">{job.id}</span>
      </div>

      {ACTIVE.has(job.status) ? (
        <div style={{ marginTop: 8 }}>
          <div className="muted small">
            {lastNote}（{job.progress}%）
          </div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--line, #2a2f3a)', marginTop: 4 }}>
            <div style={{ width: `${job.progress}%`, height: '100%', background: 'currentColor', opacity: 0.65 }} />
          </div>
        </div>
      ) : null}

      {job.status === 'failed' ? (
        <div className="small" style={{ marginTop: 8, color: 'var(--bad, #e5484d)' }}>
          失败原因：{job.error || '未知'}
        </div>
      ) : null}

      {job.manifest.warnings.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {job.manifest.warnings.map((warning) => (
            <div key={warning}>⚠ {warning}</div>
          ))}
        </div>
      ) : null}

      {summary && summary.issues.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {summary.issues.map((issue) => (
            <div key={issue}>· {issue}</div>
          ))}
        </div>
      ) : null}

      {segments.length ? (
        <>
          <div className="section-h" style={{ marginTop: 12 }}>
            {translated ? '时间轴汇总（原文 → 译配 → 配音）' : '时间轴汇总（源文本 → 配音）'}
            {summary ? ` · 总时长 ${summary.duration_seconds}s` : ' · 合成前估算时间轴'}
          </div>
          <Table head={['时间轴', '说话人', translated ? '原文 → 译配' : '源文本', '参考音频', '配音', '标注']}>
            {segments.map((segment) => (
              <tr key={segment.index}>
                <td className="mono small">
                  {segment.start_second}s – {segment.end_second}s
                </td>
                <td>{segment.speaker}</td>
                <td>
                  {segment.source_text ? (
                    <>
                      <div className="muted small">{segment.source_text}</div>
                      <div>{segment.text}</div>
                    </>
                  ) : (
                    segment.text
                  )}
                </td>
                <td className="small" style={{ maxWidth: 180, wordBreak: 'break-all' }}>
                  {segment.voice_ref || <span className="muted">（缺）</span>}
                </td>
                <td className="small" style={{ maxWidth: 260, wordBreak: 'break-all' }}>
                  <SegmentAudio taskId={taskId} jobId={job.id} segment={segment} onToast={onToast} />
                  {segment.estimated ? <span className="muted small"> · 估算</span> : null}
                </td>
                <td className="small">{segment.issue || '—'}</td>
              </tr>
            ))}
          </Table>
        </>
      ) : null}

      {job.status === 'done' ? (
        <div className="row" style={{ marginTop: 10, gap: 8 }}>
          <button className="btn btn-sm" disabled={!job.timeline_path} onClick={() => void download()}>
            下载时间轴汇总 txt
          </button>
          {summary?.full_audio ? (
            <button className="btn btn-sm" onClick={() => void downloadFull()}>
              下载整轨配音（full.wav）
            </button>
          ) : null}
          <span className="muted small">
            含每段起止时间、说话人、源文本、所用参考音频与配音音频{summary?.full_audio ? '；整轨为句序直拼（未加句间静音）' : ''}
          </span>
        </div>
      ) : null}

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
        {job.remote_id ? ` · 远端 job_id：${job.remote_id}` : ''}
      </div>
    </div>
  );
}
