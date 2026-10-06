import { useCallback, useEffect, useRef, useState } from 'react';
import type { SkillGenJobView, SkillGenView, SkillStats } from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Kv, Spinner } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 作业状态 → 展示文案与色调。 */
const JOB_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  distilling: { label: '提炼中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'distilling']);

const PROVIDER_LABEL: Record<string, string> = {
  rules: 'rules 量化提炼',
  llm: 'llm 模型网关提炼',
};

/** 作品来源：三类输入面（文档素材 / 已产出文本 / 视频理解摘要）的中文名。 */
function originLabel(origin: string): string {
  if (origin === 'document') return '文档素材';
  if (origin === 'video_understanding') return '视频理解摘要';
  if (origin.startsWith('artifact:')) return '文本产物';
  return origin;
}

/** 量化画像的展示口径：都是真统计出来的数，不含任何模型判断。 */
function statPairs(stats: SkillStats): [string, string][] {
  return [
    ['作品', `${stats.works} 份`],
    ['成稿字数中位', `${stats.chars_median}`],
    ['段落数中位', `${stats.paragraphs_median}`],
    ['句长中位 / P90', `${stats.sentence_len_median} / ${stats.sentence_len_p90}`],
    ['首句长度中位', `${stats.hook_len_median}`],
    ['疑问句 / 第二人称 / 含数字句', `${stats.question_ratio} / ${stats.second_person_ratio} / ${stats.digit_ratio}`],
    ...(stats.subtitled_works
      ? ([['字幕语速', `${stats.chars_per_minute} 字/分钟`] as [string, string]] as [string, string][])
      : []),
  ];
}

/**
 * 创作技能提炼面板（**开发样例**）。
 *
 * 把使用者**自己的创作作品**（往期成稿 / 文案文档 / 字幕 / 视频脚本 / 整集视频的理解摘要）
 * 提炼成可复用的「创作技能」：结构骨架 + 节奏参数 + 检查清单 + 反例，导出扩展插件
 * skills 目录直接可用的 ``SKILL.md``，并可一键沉淀成记忆库 ``template`` 卡片。
 *
 * 「什么样的写法值得复用」是判断不是查表，所以两条通道按诚实边界分工，界面把边界显示出来：
 * - ``rules``（默认，零 token）：句长 / 段落 / 疑问句与人称占比 / 字幕语速**真统计**自作品
 *   正文，每条结论带证据；不含模型推理，故 ``simulated=true`` 要显式标出；
 * - ``llm``：复用系统既有 ``LLM_*`` 网关做一次带判断力的提炼，未配真实网关**显式失败、
 *   绝不假装提炼**（失败原因原样显示，不隐藏）。
 */
export function SkillStudioPanel({
  task,
  onToast,
  onChanged,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
  onChanged?: () => void | Promise<void>;
}) {
  const [view, setView] = useState<SkillGenView | null>(null);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const [doc, setDoc] = useState<{ jobId: string; text: string } | null>(null);
  const pollTimer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const data = await api.skills(task.id);
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
    setDoc(null);
    void load();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [load]);

  if (!view) return null;
  const jobs = view.jobs;

  const createJob = async () => {
    setWorking('__create__');
    try {
      await api.createSkill(task.id, { provider: provider || undefined });
      onToast('技能提炼作业已创建');
      await load();
    } catch (error) {
      onToast(`创建失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const toggleDoc = async (job: SkillGenJobView) => {
    if (doc?.jobId === job.id) {
      setDoc(null);
      return;
    }
    setWorking(job.id);
    try {
      setDoc({ jobId: job.id, text: await api.skillDocument(task.id, job.id) });
    } catch (error) {
      onToast(`正文读取失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const download = async (job: SkillGenJobView) => {
    setWorking(job.id);
    try {
      await api.skillPackage(task.id, job.id, job.skill?.name ?? 'skill');
      onToast('技能包已开始下载');
    } catch (error) {
      onToast(`下载失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const applyMemory = async (job: SkillGenJobView) => {
    setWorking(job.id);
    try {
      const result = await api.applySkill(task.id, { job_id: job.id });
      onToast(result.added ? '技能已沉淀进记忆库（template 卡片）' : '该技能已在记忆库里（内容指纹相同）');
      if (onChanged) await onChanged();
    } catch (error) {
      onToast(`沉淀失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        创作技能提炼（开发样例）
        <span className="count">
          · {jobs.length ? `${jobs.length} 个作业` : '尚无作业'} · 从你自己的作品里提炼可复用的写法
        </span>
      </div>

      {view.has_materials ? (
        <div className="muted small" style={{ marginBottom: 10 }}>
          作品取自本任务的<strong>文档素材 / 字幕 / 成稿</strong>、已产出的<strong>文案与视频脚本</strong>、
          以及已完成的<strong>视频理解摘要</strong>。<code>rules</code> 通道离线把这些正文的
          <strong>结构与节奏真统计</strong>成技能（零 token，但<em>不含风格判断</em>）；
          <code>llm</code> 通道走 <code>LLM_*</code> 网关做带判断力的提炼，未配真实网关时如实失败。
        </div>
      ) : (
        <div className="muted small" style={{ marginBottom: 10 }}>
          这个任务里还没有可提炼的<strong>作品</strong>。请在 Brief 的参考素材里上传往期成稿 / 文案文档 /
          字幕文件（<code>.md</code> / <code>.txt</code> / <code>.srt</code> / <code>.vtt</code>），
          或先跑一次创作、或先做一次视频理解——技能是从<strong>你写过的东西</strong>里提炼出来的，没有作品就无从提炼。
        </div>
      )}

      <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end' }}>
        <div className="field" style={{ marginBottom: 0 }}>
          <label>提炼通道</label>
          <select value={provider} onChange={(event) => setProvider(event.target.value)}>
            <option value="">自动（跟随服务配置）</option>
            <option value="rules">rules 量化提炼（零 token，不含风格判断）</option>
            <option value="llm">llm 模型网关提炼（需配 LLM_* 真实网关）</option>
          </select>
        </div>
        <button
          className="btn btn-primary"
          disabled={!view.has_materials || working !== ''}
          onClick={() => void createJob()}
        >
          {working === '__create__' ? <Spinner /> : null} 提炼创作技能
        </button>
      </div>

      {jobs.length === 0 ? (
        <div className="muted small">
          还没有提炼作业。一次作业会把该任务的作品正文读一遍，产出{' '}
          <code>SKILL.md</code> + <code>skill.json</code> + 技能包 zip。
        </div>
      ) : null}

      {jobs.map((job) => (
        <JobCard
          key={job.id}
          job={job}
          busy={working === job.id}
          disabled={working !== ''}
          doc={doc?.jobId === job.id ? doc.text : ''}
          onToggleDoc={() => void toggleDoc(job)}
          onDownload={() => void download(job)}
          onApply={() => void applyMemory(job)}
        />
      ))}
    </div>
  );
}

/** 单个提炼作业卡片：状态 / 进度 / 结构化技能 / 正文预览与下载 / 沉淀记忆库。 */
function JobCard({
  job,
  busy,
  disabled,
  doc,
  onToggleDoc,
  onDownload,
  onApply,
}: {
  job: SkillGenJobView;
  busy: boolean;
  disabled: boolean;
  doc: string;
  onToggleDoc: () => void;
  onDownload: () => void;
  onApply: () => void;
}) {
  const meta = JOB_META[job.status] ?? { label: job.status, tone: 'tone-idle' };
  const skill = job.skill;
  const lastNote = job.history.length ? job.history[job.history.length - 1].note : '';

  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        className="card-title"
        style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
      >
        <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <Chip tone={meta.tone}>{meta.label}</Chip>
          <Chip tone="tone-idle">{PROVIDER_LABEL[job.provider] ?? job.provider}</Chip>
          <Chip tone="tone-idle" mono title={`渠道 ${job.channel || '未指定'}`}>
            {skill?.name || job.channel || job.id}
          </Chip>
          <Chip tone="tone-idle">{job.stats.works} 份作品</Chip>
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

      {job.issues.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {job.issues.map((issue) => (
            <div key={issue}>⚠ {issue}</div>
          ))}
        </div>
      ) : null}

      {job.status === 'done' && skill ? (
        <div style={{ marginTop: 10 }}>
          {skill.simulated ? (
            <div className="muted small" style={{ marginBottom: 8 }}>
              ⚠ 本技能的数字来自作品正文的<strong>真实统计</strong>，但「为什么这样写有效」属风格判断，
              <strong>未经模型推理</strong>（<code>simulated=true</code>）。需要判断力版请选 <code>llm</code> 通道重跑。
            </div>
          ) : null}
          <div className="small" style={{ fontWeight: 600 }}>{skill.title}</div>
          <div className="muted small" style={{ marginTop: 2 }}>{skill.description}</div>

          <div className="small" style={{ marginTop: 10 }}>
            <div className="section-h" style={{ marginBottom: 4 }}>步骤（每条都带依据）</div>
            <ol style={{ margin: 0, paddingLeft: 20 }}>
              {skill.steps.map((step) => (
                <li key={step.step} style={{ marginBottom: 3 }}>
                  <strong>{step.step}</strong>：{step.detail}
                  {step.evidence ? <span className="muted">（依据：{step.evidence}）</span> : null}
                </li>
              ))}
            </ol>
          </div>

          {skill.checklist.length ? (
            <div className="small" style={{ marginTop: 8 }}>
              <span className="muted">交付前自检：</span>
              {skill.checklist.join('；')}
            </div>
          ) : null}
          {skill.anti_patterns.length ? (
            <div className="small" style={{ marginTop: 6 }}>
              <span className="muted">反例：</span>
              {skill.anti_patterns.join('；')}
            </div>
          ) : null}
          {skill.examples.length ? (
            <div className="small" style={{ marginTop: 6 }}>
              <div className="section-h" style={{ marginBottom: 4 }}>来自作品的逐字样例</div>
              {skill.examples.map((item) => (
                <div key={`${item.source}-${item.quote}`} style={{ marginBottom: 2 }}>
                  「{item.quote}」
                  <span className="muted">——《{item.source}》</span>
                </div>
              ))}
            </div>
          ) : null}

          <div className="section-h" style={{ marginTop: 10, marginBottom: 4 }}>
            量化画像（真统计，不含模型判断）
          </div>
          <Kv pairs={statPairs(job.stats)} />
          <div className="muted small" style={{ marginTop: 6 }}>
            素材：{job.sources.map((item) => `${item.title}（${originLabel(item.origin)}，${item.chars} 字）`).join('、')}
          </div>

          <div className="row" style={{ gap: 8, marginTop: 12, flexWrap: 'wrap' }}>
            <button className="btn" disabled={busy} onClick={onToggleDoc}>
              {doc ? '收起 SKILL.md' : '预览 SKILL.md'}
            </button>
            <button className="btn" disabled={disabled || busy} onClick={onDownload}>
              {busy && !doc ? <Spinner /> : null} 下载技能包 zip
            </button>
            <button className="btn" disabled={disabled || busy} onClick={onApply}>
              {busy && !doc ? <Spinner /> : null} 沉淀进记忆库
            </button>
          </div>

          {doc ? <pre className="pre" style={{ marginTop: 10 }}>{doc}</pre> : null}
        </div>
      ) : null}

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(job.created_at)}
        {job.updated_at !== job.created_at ? ` · 最近推进 ${formatDateTime(job.updated_at)}` : ''}
        {job.skill_ref ? ` · ${job.skill_ref}` : ''}
      </div>
    </div>
  );
}
