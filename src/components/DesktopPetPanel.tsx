import { useCallback, useEffect, useRef, useState } from 'react';
import type { PetGenJobView, PetGenView } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Spinner, Table } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

const JOB_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  generating: { label: '出帧中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'generating']);

const PROVIDER_LABEL: Record<string, string> = {
  sample: 'sample 样例引擎（标准库画帧）',
  imagegen: 'imagegen 图片通道（逐帧真图）',
};

/**
 * 可选动作的中文名。动作目录与帧数/帧率由服务端清单决定（作业卡从
 * ``manifest.segments`` 现算），这里只维护展示用文案；服务端新增动作时
 * 作业卡仍会完整呈现，只是选择器里暂时点不到。
 */
const ACTION_LABEL: Record<string, string> = {
  idle: '待机',
  walk: '走动',
  sleep: '睡觉',
  happy: '开心',
  drag: '被拖拽',
};

const ACTION_ORDER = ['idle', 'walk', 'sleep', 'happy', 'drag'];

/** 运行器入口：宠物包解压后按此命令跑起来（标准库 tkinter，无需额外依赖）。 */
const RUN_HINT = 'python -m app.desktop_pet <宠物包 zip 或解压目录>';

/** 按动作聚合清单里的帧数与帧率（不复制服务端数值口径）。 */
function actionStats(job: PetGenJobView): { name: string; label: string; frames: number; fps: number }[] {
  const byAction = new Map<string, { frames: number; fps: number }>();
  for (const segment of job.manifest.segments) {
    const found = byAction.get(segment.action);
    if (found) found.frames += 1;
    else byAction.set(segment.action, { frames: 1, fps: segment.fps });
  }
  return [...byAction.entries()].map(([name, spec]) => ({
    name,
    label: ACTION_LABEL[name] ?? name,
    frames: spec.frames,
    fps: spec.fps,
  }));
}

/**
 * 桌面宠物面板（**开发样例**）。
 *
 * 把 A8 的视觉指导（visual_brief）变成一个**能真跑在桌面上的宠物包**：
 * - ``sample``（默认，零依赖）：用纯标准库画出各动作帧，产出 pet.json + frames + README + 运行器；
 *   帧是简笔形象而非美术成品，清单里的 ``simulated`` 会如实标注；
 * - ``imagegen``：复用 ``IMAGEGEN_*`` 端点逐帧出真图（配 ``IMAGEGEN_BASE_URL`` 后可用），
 *   逐帧计费、受理前按帧数做成本熔断；未配置端点时显式失败，不假装出图。
 *
 * 系统不做抠图 / sprite sheet 切片：背景统一是 ``key_color`` 纯色，运行器用窗口
 * ``-transparentcolor`` 抠掉它，因此预览也按同一规则把该色抠成透明。
 */
export function DesktopPetPanel({
  task,
  onToast,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
}) {
  const [view, setView] = useState<PetGenView | null>(null);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const [name, setName] = useState('');
  const [actions, setActions] = useState<string[]>([]);
  const pollTimer = useRef<number | null>(null);

  const hasVisual = task.artifacts.some((artifact) => artifact.type === 'visual_brief');

  const load = useCallback(async () => {
    if (!hasVisual) return;
    try {
      const data = await api.pets(task.id);
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
    setName('');
    setActions([]);
    void load();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [load]);

  if (!hasVisual) return null;

  const jobs = view?.jobs ?? [];

  const toggleAction = (action: string) => {
    setActions((current) =>
      current.includes(action) ? current.filter((item) => item !== action) : [...current, action],
    );
  };

  const createJob = async () => {
    setWorking('__create__');
    try {
      await api.createPet(task.id, {
        provider: provider || undefined,
        name: name.trim() || undefined,
        actions: actions.length ? actions : undefined,
      });
      onToast('桌面宠物作业已创建');
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
        桌面宠物（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'}
          {' '}· 产出可运行宠物包，内置桌宠运行器用标准库窗口
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        清单来自视觉指导（visual_brief）的风格与色板。<code>sample</code> 通道离线确定性推进并用纯标准库
        画出各动作帧（简笔形象，非美术成品）；<code>imagegen</code> 通道复用
        <code> IMAGEGEN_BASE_URL</code> 的出图端点逐帧生成真图，逐帧计费、超预算即熔断。完成后下载宠物包，
        按 <code>{RUN_HINT}</code> 就能在桌面上跑起来。
      </div>

      <div className="row" style={{ gap: 8, marginBottom: 10, alignItems: 'flex-end', flexWrap: 'wrap' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>出帧通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="sample">sample 标准库画帧</option>
            <option value="imagegen">imagegen 图片通道逐帧真图</option>
          </select>
        </div>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>宠物名（可选）</label>
          <input
            value={name}
            placeholder="留空则用品牌·吉祥物"
            onChange={(event) => setName(event.target.value)}
          />
        </div>
        <button className="btn btn-primary" disabled={working !== ''} onClick={() => void createJob()}>
          {working === '__create__' ? <Spinner /> : null} 创建宠物作业
        </button>
      </div>

      <div className="row" style={{ gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <label className="muted small">动作：</label>
        {ACTION_ORDER.map((action) => (
          <label key={action} className="small" style={{ display: 'inline-flex', gap: 4, alignItems: 'center' }}>
            <input
              type="checkbox"
              checked={actions.includes(action)}
              onChange={() => toggleAction(action)}
            />
            {ACTION_LABEL[action] ?? action}
          </label>
        ))}
        <span className="muted small">（不勾选＝全部动作）</span>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">还没有宠物作业。创建后会产出动作帧与 pet.json，下载即可运行。</div>
      ) : null}

      {jobs.map((job) => (
        <JobCard
          key={job.id}
          job={job}
          busy={working === job.id}
          disabled={working !== ''}
          onDownload={() => {
            setWorking(job.id);
            api
              .petPackage(task.id, job.id, job.name)
              .then(() => onToast('宠物包已下载'))
              .catch((error) => onToast(`下载失败：${errorText(error)}`, true))
              .finally(() => setWorking(''));
          }}
        />
      ))}
    </div>
  );
}

/** 单个宠物作业卡片：状态 / 进度 / 动画预览 / 清单 / 下载宠物包。 */
function JobCard({
  job,
  busy,
  disabled,
  onDownload,
}: {
  job: PetGenJobView;
  busy: boolean;
  disabled: boolean;
  onDownload: () => void;
}) {
  const meta = JOB_META[job.status] ?? { label: job.status, tone: 'tone-idle' };
  const manifest = job.manifest;
  const lastNote = job.history.length ? job.history[job.history.length - 1].note : '';
  const stats = actionStats(job);
  const palette = Object.entries(manifest.palette);

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        className="card-title"
        style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
      >
        <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <Chip tone={meta.tone}>{meta.label}</Chip>
          <Chip tone="tone-idle">{PROVIDER_LABEL[job.provider] ?? job.provider}</Chip>
          <Chip tone="tone-idle" mono title={`尺寸 ${job.size}px｜透明键 ${manifest.key_color}`}>
            {manifest.name} · {job.frame_count} 帧
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

      {manifest.warnings.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {manifest.warnings.map((warning) => (
            <div key={warning}>⚠ {warning}</div>
          ))}
        </div>
      ) : null}

      {job.status === 'done' ? (
        <div style={{ marginTop: 10 }}>
          {manifest.simulated ? (
            <div className="muted small" style={{ marginBottom: 8 }}>
              ⚠ 这些帧由 sample 通道用标准库画出（清单 <code>simulated=true</code>）：形状与节拍是真的，
              美术表现不是。接入 <code>imagegen</code> 通道后即换为逐帧真图，包结构与运行方式不变。
            </div>
          ) : null}
          <div className="row" style={{ gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
            {stats.map((action) => (
              <PetPreview
                key={action.name}
                jobId={job.id}
                taskId={job.task_id}
                action={action.name}
                frames={action.frames}
                fps={action.fps}
                size={job.size}
                keyColor={manifest.key_color}
                label={action.label}
              />
            ))}
          </div>
          <div className="muted small" style={{ marginTop: 4 }}>
            色板来源：{manifest.palette_source} ·{' '}
            {palette.map(([role, hex]) => (
              <span key={role} className="mono" style={{ marginRight: 8 }}>
                <span
                  style={{
                    display: 'inline-block',
                    width: 10,
                    height: 10,
                    background: hex,
                    border: '1px solid var(--line, #2a2f3a)',
                    marginRight: 3,
                  }}
                />
                {role}
              </span>
            ))}
          </div>
          <div className="row" style={{ gap: 8, marginTop: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            <button className="btn btn-primary" disabled={disabled} onClick={onDownload}>
              {busy ? <Spinner /> : null} 下载宠物包 zip
            </button>
            <code className="muted small">{RUN_HINT}</code>
          </div>
          <div className="muted small" style={{ marginTop: 6 }}>
            包内含 <code>pet.json</code> + <code>frames/</code> + <code>README.md</code>
            ，并在导出时内嵌 <code>runner/pet.py</code>（服务读不到运行器源码时该文件会缺席，
            此时用仓库内的 <code>app/desktop_pet.py</code> 运行）。
          </div>
        </div>
      ) : null}

      <div className="section-h" style={{ marginTop: 12 }}>
        动作帧清单{manifest.size ? ` · ${manifest.size}px｜${manifest.fps}fps` : ''}
      </div>
      <Table head={['动作', '帧号', '帧率', '循环', '出帧 prompt']}>
        {manifest.segments.map((segment) => (
          <tr key={`${segment.action}-${segment.index}`}>
            <td>{ACTION_LABEL[segment.action] ?? segment.action}</td>
            <td className="mono">{segment.index}</td>
            <td className="mono">{segment.fps}</td>
            <td>{segment.loop ? '循环' : '单次'}</td>
            <td className="small">{segment.prompt || '—'}</td>
          </tr>
        ))}
      </Table>

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
        {job.frames.length ? ` · 已落盘 ${job.frames.length} 帧 → ${job.dir}/` : ''}
      </div>
    </div>
  );
}

/**
 * 一个动作的实时预览：取该动作全部帧，按清单里的 ``key_color`` 抠成透明后逐帧播放，
 * 与桌面运行器（窗口 ``-transparentcolor``）同一套观感。
 */
function PetPreview({
  taskId,
  jobId,
  action,
  frames,
  fps,
  size,
  keyColor,
  label,
}: {
  taskId: string;
  jobId: string;
  action: string;
  frames: number;
  fps: number;
  size: number;
  keyColor: string;
  label: string;
}) {
  const canvas = useRef<HTMLCanvasElement | null>(null);
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading');

  useEffect(() => {
    let cancelled = false;
    let drawn: ImageData[] = [];
    let timer: number | null = null;

    const keyRgb = [
      Number.parseInt(keyColor.slice(1, 3), 16),
      Number.parseInt(keyColor.slice(3, 5), 16),
      Number.parseInt(keyColor.slice(5, 7), 16),
    ];

    void (async () => {
      const loaded: ImageData[] = [];
      for (let index = 1; index <= frames; index += 1) {
        try {
          const blob = await api.petFrame(taskId, jobId, action, index);
          const bitmap = await createImageBitmap(blob);
          const surface = document.createElement('canvas');
          surface.width = bitmap.width;
          surface.height = bitmap.height;
          const ctx = surface.getContext('2d');
          if (!ctx) throw new Error('画布不可用');
          ctx.drawImage(bitmap, 0, 0);
          bitmap.close();
          const image = ctx.getImageData(0, 0, surface.width, surface.height);
          // 与运行器同一原则：背景就是那一个纯色，逐像素抠成透明
          for (let p = 0; p < image.data.length; p += 4) {
            if (image.data[p] === keyRgb[0] && image.data[p + 1] === keyRgb[1] && image.data[p + 2] === keyRgb[2]) {
              image.data[p + 3] = 0;
            }
          }
          loaded.push(image);
        } catch {
          break;
        }
      }
      if (cancelled) return;
      if (!loaded.length) {
        setState('failed');
        return;
      }
      drawn = loaded;
      setState('ready');
      const view = canvas.current;
      const ctx = view?.getContext('2d');
      if (!view || !ctx) return;
      view.width = drawn[0].width;
      view.height = drawn[0].height;
      let cursor = 0;
      const step = () => {
        ctx.clearRect(0, 0, view.width, view.height);
        ctx.putImageData(drawn[cursor % drawn.length], 0, 0);
        cursor += 1;
      };
      step();
      timer = window.setInterval(step, Math.max(40, 1000 / Math.max(1, fps)));
    })();

    return () => {
      cancelled = true;
      if (timer) window.clearInterval(timer);
      drawn = [];
    };
  }, [taskId, jobId, action, frames, fps, keyColor]);

  return (
    <div style={{ textAlign: 'center' }}>
      <canvas
        ref={canvas}
        width={size}
        height={size}
        style={{
          width: size,
          height: size,
          display: 'block',
          background: 'repeating-conic-gradient(#20242c 0% 25%, #2a2f3a 0% 50%) 50% / 12px 12px',
          borderRadius: 6,
          visibility: state === 'ready' ? 'visible' : 'hidden',
        }}
      />
      <div className="muted small" style={{ marginTop: 4 }}>
        {state === 'loading' ? `${label} 载入中…` : state === 'failed' ? `${label} 帧读取失败` : `${label} · ${frames} 帧 ${fps}fps`}
      </div>
    </div>
  );
}
