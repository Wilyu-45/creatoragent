import { useCallback, useEffect, useRef, useState } from 'react';
import type { VideoGenJobView, VideoGenView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Spinner, Table } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const JOB_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  generating: { label: '生成中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'generating']);

/**
 * 视频生成面板（**开发样例**）。
 *
 * 消费 ``video_script`` 的分镜清单，按镜产出画面段（B-roll / 分镜画面），逼近「可开拍
 * 素材」；**不做多镜拼接合成**（合成归视频工场 / 人工）。视频生成贵，纳入成本预算：
 * 超预算时受理即 **fail-loud**，绝不静默降级成「假装已生成」。
 * - ``sample``：离线模拟生命周期并按分镜生成渲染清单（不产真片）；
 * - ``http``：配置 ``VIDEOGEN_API_URL`` 后对接「POST 建任务 → GET 查状态」网关
 *   （云端可灵/即梦/Runway 经网关，或自建本地渲染农场；本地私网端点计 0 元）。
 */
export function VideoGenPanel({
  task,
  onToast,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<VideoGenView | null>(null);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const pollTimer = useRef<number | null>(null);

  const hasScript = task.artifacts.some((artifact) => artifact.type === 'video_script');

  const load = useCallback(async () => {
    if (!hasScript) return;
    try {
      const data = await api.videos(task.id);
      setView(data);
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
      await api.createVideo(task.id, { provider: provider || undefined });
      onToast('视频生成任务已创建');
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
        视频生成（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 按镜出画面段，不做拼接合成
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        样例引擎离线模拟生命周期并按分镜生成渲染清单；配置 <code>VIDEOGEN_API_URL</code> 后走 http 网关。
        视频生成昂贵，纳入 <code>COST_BUDGET_USD</code> 预算：<strong>超预算即 fail-loud，不假装已生成</strong>。
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>生成通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sample">sample 内置样例引擎</option>
            <option value="http">http 适配网关</option>
          </select>
        </div>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void createJob()}>
          {working === '__create__' ? <Spinner /> : null} 创建生成作业
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有生成作业。创建后会从视频脚本分镜按镜产出画面段清单。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard key={job.id} job={job} />
      ))}
    </div>
  );
}

/** 单个生成作业卡片：状态 / 进度 / 成本 / 分镜渲染清单 / 画面段产出。 */
function JobCard({ job }: { job: VideoGenJobView }) {
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
          {job.estimated_cost_usd > 0 ? (
            <Chip tone="tone-warn" mono title="按镜数保守估算，仅用于预算熔断的事前判断">
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

      {job.status === 'done' && job.segments_out.length ? (
        <div className="small" style={{ marginTop: 8 }}>
          已产出 {job.segments_out.length} 段画面：
          {job.segments_out.map((segment) => (
            <div key={`${segment.shot}-${segment.url}`} style={{ marginTop: 4 }}>
              {segment.url.startsWith('http') ? (
                <a href={segment.url} target="_blank" rel="noreferrer">
                  第 {segment.shot} 镜：{segment.url}
                </a>
              ) : (
                <code>
                  第 {segment.shot} 镜：{segment.url}
                </code>
              )}
            </div>
          ))}
          {job.provider === 'sample' ? (
            <span className="muted">（样例引擎输出的是模拟地址，接入真实网关后即为其返回的画面段 URL）</span>
          ) : null}
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
      <Table head={['镜号', '角色', '时间轴', '画面', '口播']}>
        {manifest.segments.map((segment) => (
          <tr key={segment.shot}>
            <td className="mono">{segment.shot}</td>
            <td>{segment.role || '—'}</td>
            <td className="mono small">
              {segment.start_second}s – {segment.end_second}s
            </td>
            <td className="small">{segment.visual || '—'}</td>
            <td>
              <Chip tone={segment.spoken ? 'tone-ok' : 'tone-idle'}>{segment.spoken ? '有' : '画面段'}</Chip>
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
