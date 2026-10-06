import { useCallback, useEffect, useRef, useState } from 'react';
import type { VideoUnderstandJobView, VideoUnderstandView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Spinner } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 作业状态 → 展示文案与色调。 */
const JOB_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  understanding: { label: '理解中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'understanding']);

const PROVIDER_LABEL: Record<string, string> = {
  sample: 'sample 样例引擎',
  real: 'real 整集视频理解网关',
};

/**
 * 视频理解面板（**开发样例**，方案一「多模态理解 + 文本创作」的摄取侧落地）。
 *
 * 让 UP 主能把一集番剧/影视「看懂」：把**整集视频**交给**原生支持视频输入的理解网关**
 * （一集=一次调用），取回该集的**结构化视觉摘要**，再一键「注入 Brief」驱动下游创作。
 * 视觉理解推理不在系统内实现，这里提供可回归的接入样例：
 * - ``sample`` 内置引擎：离线模拟「提交整集 → 轮询 → 完成」，产出带 simulated 标记的
 *   占位摘要骨架（不调网关）；
 * - ``real``：按 ``VIDEOUNDERSTAND_API_URL`` 提交整集视频、轮询取回摘要；任一前置不满足
 *   即**显式失败、绝不假装看懂**（未配网关 / 非本地文件 / 成本超预算）。
 */
export function VideoUnderstandPanel({
  task,
  onToast,
  onChanged,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
  onChanged?: () => void | Promise<void>;
}) {
  const [view, setView] = useState<VideoUnderstandView | null>(null);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const pollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.videoUnderstanding(task.id);
      setView(data);
      if (data.jobs.some((job) => ACTIVE.has(job.status))) schedulePoll();
    } catch {
      /* 读取失败不阻塞主流程 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id]);

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

  // 任务未带视频素材时不呈现本面板（视频理解没有可「看懂」的对象）
  if (!view || !view.has_video_asset) return null;

  const jobs = view.jobs;

  const createJob = async () => {
    setWorking('__create__');
    try {
      await api.createVideoUnderstanding(task.id, { provider: provider || undefined });
      onToast('视频理解作业已创建');
      await load();
    } catch (error) {
      onToast(`创建失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const applyToBrief = async (job: VideoUnderstandJobView) => {
    setWorking(job.id);
    try {
      const result = await api.applyVideoUnderstanding(task.id, { job_id: job.id });
      onToast(result.applied === 'updated' ? '视觉摘要已更新到 Brief' : '视觉摘要已注入 Brief');
      if (onChanged) await onChanged();
      await load();
    } catch (error) {
      onToast(`注入失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        视频理解（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 看懂画面靠原生视频理解模型，这里只提供整集接入样例
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        样例引擎离线模拟「提交整集 → 轮询 → 完成」并产出**占位视觉摘要骨架**；选择 <code>real</code> 通道会把
        整集视频提交给**原生支持视频输入的理解网关**（配 <code>VIDEOUNDERSTAND_API_URL</code>，一集一次调用），
        任一前置不满足即**如实失败、不假装看懂**。完成后点「注入 Brief」驱动解说创作。
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>理解通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sample">sample 内置样例引擎</option>
            <option value="real">real 整集视频 → 原生视频理解网关</option>
          </select>
        </div>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void createJob()}>
          {working === '__create__' ? <Spinner /> : null} 创建理解作业
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有理解作业。创建后会把该任务的整集视频提交给网关并产出该集的视觉摘要。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard key={job.id} job={job} busy={working === job.id} disabled={working !== ''} onApply={() => void applyToBrief(job)} />
      ))}
    </div>
  );
}

/** 单个理解作业卡片：状态 / 进度 / 结构化视觉摘要 / 注入 Brief。 */
function JobCard({
  job,
  busy,
  disabled,
  onApply,
}: {
  job: VideoUnderstandJobView;
  busy: boolean;
  disabled: boolean;
  onApply: () => void;
}) {
  const meta = JOB_META[job.status] ?? { label: job.status, tone: 'tone-idle' };
  const summary = job.summary;
  const lastNote = job.history.length ? job.history[job.history.length - 1].note : '';

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
            {job.video_title || job.video_ref}
          </Chip>
          {job.estimated_cost_usd > 0 ? (
            <Chip tone="tone-warn" mono>
              预估 ${job.estimated_cost_usd.toFixed(3)}
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

      {job.status === 'done' && summary ? (
        <div style={{ marginTop: 10 }}>
          {summary.simulated ? (
            <div className="muted small" style={{ marginBottom: 8 }}>
              ⚠ 这是 sample 通道的**占位骨架**（未调用视频理解网关）；接真实网关后即产出基于画面的理解。
            </div>
          ) : null}
          <div className="small" style={{ fontWeight: 600 }}>{summary.theme || '（无主题）'}</div>
          {summary.logline ? <div className="muted small">{summary.logline}</div> : null}
          <SummaryList label="场景时间线" items={summary.scenes} />
          <SummaryList label="人物 / 角色" items={summary.characters} />
          <SummaryList label="关键事件" items={summary.actions} />
          <SummaryList label="名场面" items={summary.notable_moments} />
          {summary.mood || summary.camera_language || summary.pacing ? (
            <div className="muted small" style={{ marginTop: 6 }}>
              {[summary.mood && `情绪：${summary.mood}`, summary.camera_language && `镜头：${summary.camera_language}`, summary.pacing && `节奏：${summary.pacing}`]
                .filter(Boolean)
                .join('　·　')}
            </div>
          ) : null}
          {summary.text_brief ? (
            <div className="small" style={{ marginTop: 8, whiteSpace: 'pre-wrap' }}>
              <div className="section-h" style={{ marginBottom: 4 }}>可注入摘要</div>
              {summary.text_brief}
            </div>
          ) : null}
          {summary.issues.length ? (
            <div className="muted small" style={{ marginTop: 8 }}>
              {summary.issues.map((issue) => (
                <div key={issue}>⚠ {issue}</div>
              ))}
            </div>
          ) : null}

          <div className="row" style={{ gap: 8, marginTop: 12 }}>
            <button className="btn" disabled={disabled || !summary.text_brief} onClick={onApply}>
              {busy ? <Spinner /> : null} 注入 Brief（供创作引用）
            </button>
            <span className="muted small">
              整集 {summary.stats.episode_calls} 次调用 · 成本 ${summary.stats.cost_usd.toFixed(3)}
            </span>
          </div>
        </div>
      ) : null}

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
      </div>
    </div>
  );
}

function SummaryList({ label, items }: { label: string; items: string[] }) {
  if (!items || !items.length) return null;
  return (
    <div className="small" style={{ marginTop: 6 }}>
      <span className="muted">{label}：</span>
      {items.join('；')}
    </div>
  );
}
