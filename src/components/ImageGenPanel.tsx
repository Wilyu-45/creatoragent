import { useCallback, useEffect, useRef, useState } from 'react';
import type { ImageGenJobView, ImageGenView } from '../lib/api.ts';
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
  generating: { label: '生成中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'generating']);

const PROVIDER_LABEL: Record<string, string> = {
  sample: 'sample 样例引擎',
  openai: 'openai 协议网关',
  local: 'local 本地图生网关',
};

/**
 * 图片生成面板（**开发样例**）。
 *
 * 让 A8 从「只出 image_prompts」升级到「真出封面 / 分镜画面」，服务解说视频的
 * 封面与 B-roll 静帧。图片生成不在系统内实现，这里提供可回归的接入样例：
 * - ``sample`` 内置引擎：离线模拟「排队 → 生成 → 完成」，按 ``visual_brief`` 的
 *   image_prompts 生成**生成清单**（不产真图）；
 * - ``openai``：配置 ``IMAGEGEN_BASE_URL`` 后对接 OpenAI 协议 /images/generations；
 * - ``local``：对接本地 A1111 / SD-WebUI 的 /sdapi/v1/txt2img 同步接口，图片落 data/assets。
 */
export function ImageGenPanel({
  task,
  onToast,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<ImageGenView | null>(null);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const pollTimer = useRef<number | null>(null);

  const hasVisual = task.artifacts.some((artifact) => artifact.type === 'visual_brief');

  const load = useCallback(async () => {
    if (!hasVisual) return;
    try {
      const data = await api.images(task.id);
      setView(data);
      if (data.jobs.some((job) => ACTIVE.has(job.status))) schedulePoll();
    } catch {
      /* 读取失败不阻塞主流程 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasVisual, task.id]);

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

  if (!hasVisual) return null;

  const jobs = view?.jobs ?? [];

  const createJob = async () => {
    setWorking('__create__');
    try {
      await api.createImage(task.id, { provider: provider || undefined });
      onToast('图片生成任务已创建');
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
        图片生成（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 接哪家文生图服务由你决定，这里只提供接入样例
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        样例引擎离线模拟「排队 → 生成 → 完成」并按视觉指导（visual_brief）的 image_prompts 生成
        生成清单；配置 <code>IMAGEGEN_BASE_URL</code> 后 <code>openai</code> / <code>local</code> 通道真实出图。
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>生成通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sample">sample 内置样例引擎</option>
            <option value="openai">openai 协议网关</option>
            <option value="local">local 本地图生网关</option>
          </select>
        </div>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void createJob()}>
          {working === '__create__' ? <Spinner /> : null} 创建生成作业
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有生成作业。创建后会从视觉指导的 image_prompts 生成清单（封面 / B-roll / 分镜静帧）。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard key={job.id} job={job} />
      ))}
    </div>
  );
}

/** 单个生成作业卡片：状态 / 进度 / 生成清单 / 出图。 */
function JobCard({ job }: { job: ImageGenJobView }) {
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
          <Chip tone="tone-idle">{PROVIDER_LABEL[job.provider] ?? job.provider}</Chip>
          <Chip tone="tone-idle" mono title={`作业 ID ${job.id}`}>
            {manifest.segments.length} 张 · {job.size}
          </Chip>
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

      {job.status === 'done' && job.images.length ? (
        <div className="small" style={{ marginTop: 8 }}>
          已产出 {job.images.length} 张：
          {job.images.map((image) => (
            <div key={image.id} style={{ marginTop: 4 }}>
              {image.url.startsWith('http') ? (
                <a href={image.url} target="_blank" rel="noreferrer">
                  {image.id}：{image.url}
                </a>
              ) : (
                <code>
                  {image.id}：{image.url}
                </code>
              )}
            </div>
          ))}
          {job.provider === 'sample' ? (
            <span className="muted">（样例引擎输出的是模拟地址，接入真实服务后即为其返回的图片 / assets 引用）</span>
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
        生成清单（来自视觉指导 image_prompts）{manifest.size ? ` · ${manifest.size}` : ''}
      </div>
      <Table head={['序号', '用途', '画面', '提示词', '负向词']}>
        {manifest.segments.map((segment) => (
          <tr key={segment.id}>
            <td className="mono">{segment.index}</td>
            <td>{segment.usage || '—'}</td>
            <td className="small">{segment.scene || '—'}</td>
            <td className="small">{segment.prompt || '—'}</td>
            <td className="small">{segment.negative || '—'}</td>
          </tr>
        ))}
      </Table>

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
      </div>
    </div>
  );
}
