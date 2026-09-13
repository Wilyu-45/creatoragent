import { useCallback, useEffect, useRef, useState } from 'react';
import type { DigitalHumanJobView, DigitalHumanView } from '../lib/api.ts';
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
  rendering: { label: '渲染中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'rendering']);

/**
 * 数字人渲染面板（**开发样例**）。
 *
 * 数字人渲染不在系统内实现——HeyGen / D-ID / 腾讯智影等服务的授权、形象库、
 * 计费与回调协议差异极大，接哪家、要不要接由使用者决定。本面板展示的是接入样例：
 * - ``sample`` 内置引擎：离线确定性模拟「排队 → 渲染 → 完成」，按 ``video_script``
 *   产物生成渲染清单（每镜台词 / 字幕 / 机位 / 起止时间），购买任何第三方服务
 *   之前即可联调 API、UI 与下游流程；
 * - ``http`` 适配样例：配置 ``DIGITAL_HUMAN_API_URL`` 后对接「POST 建任务 →
 *   GET 查状态」最小契约的自建渲染网关。
 */
export function DigitalHumanPanel({
  task,
  onToast,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<DigitalHumanView | null>(null);
  const [working, setWorking] = useState('');
  const [avatar, setAvatar] = useState('');
  const [provider, setProvider] = useState('');
  const pollTimer = useRef<number | null>(null);

  const hasScript = task.artifacts.some((artifact) => artifact.type === 'video_script');

  const load = useCallback(async () => {
    if (!hasScript) return;
    try {
      const data = await api.digitalHuman(task.id);
      setView(data);
      // 有活跃作业时加快轮询，全部终态后停表（惰性推进由后端读取时计算）
      if (data.jobs.some((job) => ACTIVE.has(job.status))) schedulePoll();
    } catch {
      /* 读取失败不阻塞主流程 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasScript, task.id]);

  const schedulePoll = useCallback(() => {
    if (pollTimer.current) return;
    pollTimer.current = window.setTimeout(() => {
      pollTimer.current = null;
      void load();
    }, 2000);
  }, [load]);

  useEffect(() => {
    setView(null);
    setAvatar('');
    setProvider('');
    void load();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [load]);

  if (!hasScript) return null;

  const jobs = view?.jobs ?? [];

  const createJob = async () => {
    setWorking('__create__');
    try {
      await api.createDigitalHuman(task.id, {
        avatar: avatar.trim() || undefined,
        provider: provider || undefined,
      });
      onToast('数字人渲染任务已创建（样例引擎）');
      setAvatar('');
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
        数字人渲染（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 接哪家第三方服务由你决定，这里只提供接入样例
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        样例引擎离线模拟「排队 → 渲染 → 完成」并按视频脚本生成渲染清单；配置{' '}
        <code>DIGITAL_HUMAN_API_URL</code> 可切换为 http 适配样例，对接你自己的渲染网关。
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>形象 ID（可选）</label>
          <input
            value={avatar}
            placeholder="留空用默认形象"
            onChange={(event) => setAvatar(event.target.value)}
            style={{ width: 200 }}
          />
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>渲染通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sample">sample 内置样例引擎</option>
            <option value="http">http 适配网关</option>
          </select>
        </div>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void createJob()}>
          {working === '__create__' ? <Spinner /> : null} 创建渲染作业
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有渲染作业。创建后会从视频脚本分镜生成渲染清单（台词 / 字幕 / 机位 / 时间轴）。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard key={job.id} job={job} />
      ))}
    </div>
  );
}

/** 单个渲染作业卡片：状态 / 进度 / 渲染清单 / 分镜表。 */
function JobCard({ job }: { job: DigitalHumanJobView }) {
  const meta = JOB_META[job.status] ?? { label: job.status, tone: 'tone-idle' };
  const manifest = job.manifest;
  const lastNote = job.history.length ? job.history[job.history.length - 1].note : '';

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        className="card-title"
        style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
      >
        <span className="row" style={{ gap: 8 }}>
          <Chip tone={meta.tone}>{meta.label}</Chip>
          <Chip tone="tone-idle">{job.provider === 'http' ? 'http 网关' : 'sample 样例引擎'}</Chip>
          <Chip tone="tone-idle" mono title={`作业 ID ${job.id}`}>
            {job.shot_count} 镜 · {job.duration_seconds}s
          </Chip>
          {job.avatar ? <Chip tone="tone-idle">{job.avatar}</Chip> : null}
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

      {job.status === 'done' && job.video_url ? (
        <div className="small" style={{ marginTop: 8 }}>
          成片地址：
          {job.video_url.startsWith('http') ? (
            <a href={job.video_url} target="_blank" rel="noreferrer">
              {job.video_url}
            </a>
          ) : (
            <code>{job.video_url}</code>
          )}
          <span className="muted">（样例引擎输出的是模拟地址，接入真实服务后即为其返回的 URL）</span>
        </div>
      ) : null}

      {job.status === 'failed' ? (
        <div className="small" style={{ marginTop: 8, color: 'var(--bad, #e5484d)' }}>
          失败原因：{job.error || '未知'}
        </div>
      ) : null}

      {manifest.warnings.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {manifest.warnings.map((warning) => (
            <div key={warning}>⚠ {warning}</div>
          ))}
        </div>
      ) : null}

      <div className="section-h" style={{ marginTop: 12 }}>
        渲染清单（来自视频脚本分镜）{manifest.aspect_ratio ? ` · ${manifest.aspect_ratio}` : ''}
      </div>
      <Table head={['镜号', '角色', '时间轴', '台词 / 字幕', '机位', '口播']}>
        {manifest.segments.map((segment) => (
          <tr key={segment.shot}>
            <td className="mono">{segment.shot}</td>
            <td>{segment.role || '—'}</td>
            <td className="mono small">
              {segment.start_second}s – {segment.end_second}s
            </td>
            <td>{segment.subtitle || segment.voiceover || '—'}</td>
            <td className="small">{segment.camera || '—'}</td>
            <td>
              <Chip tone={segment.spoken ? 'tone-ok' : 'tone-idle'}>{segment.spoken ? '有' : '画面演出'}</Chip>
            </td>
          </tr>
        ))}
      </Table>

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
        {job.remote_id ? ` · 远端 job_id：${job.remote_id}` : ''}
      </div>
    </div>
  );
}
