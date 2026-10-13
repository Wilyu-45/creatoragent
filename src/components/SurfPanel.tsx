import { useCallback, useEffect, useRef, useState } from 'react';
import type {
  SurfCard,
  SurfComment,
  SurfHistoryView,
  SurfSessionView,
  SurfSignal,
} from '../lib/api.ts';
import { api } from '../lib/api.ts';
import type { TaskRecord } from '../lib/types.ts';
import { formatDateTime } from '../lib/format.ts';
import { Chip, Spinner } from './ui.tsx';

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** 会话状态 → 展示文案与色调。 */
const SESSION_META: Record<string, { label: string; tone: string }> = {
  queued: { label: '排队中', tone: 'tone-idle' },
  surfing: { label: '冲浪中', tone: 'tone-warn' },
  digesting: { label: '消化中', tone: 'tone-warn' },
  done: { label: '已完成', tone: 'tone-ok' },
  failed: { label: '失败', tone: 'tone-bad' },
};

const ACTIVE = new Set(['queued', 'surfing', 'digesting']);

const PROVIDER_LABEL: Record<string, string> = {
  sample: 'sample 离线占位',
  auto: 'auto 自动择优',
  browser: 'browser 真无头浏览器',
  http: 'http 联网检索网关',
};

/** 看起来像视频站的来源（据链接域名粗判），仅用于提示「去理解」，不假装看懂。 */
function looksLikeVideo(url: string): boolean {
  return /youtube\.com|youtu\.be|bilibili\.com|\/video|\.mp4|douyin|tiktok/i.test(url);
}

/**
 * 灵感冲浪面板：像真人一样上网刷信息 / 看视频，把外部信号消化成**灵感卡**，
 * 沉淀成跨任务的**灵感 idea 库**，由用户自行挑选、调整后再开始创作。
 *
 * 严守旁路作业铁律：
 * - ``sample``（默认）离线零依赖跑通全链路，产**占位灵感**并自报 ``simulated`` 徽标；
 * - ``browser``（Playwright 真无头）/ ``http``（联网检索网关）为真引擎可选通道，
 *   缺依赖 / 缺配置时**受理即如实失败并点名**具体安装命令与环境变量，绝不假装浏览过；
 * - 勾选「在线看视频」（仅 browser）时像真人一样进视频页：关遮罩→点播放→**校验真的在播**→
 *   播放中**分组截图抽帧**→逐组交 VLM 看懂（再把各组结论 + DOM 直抓的标题/简介/评论汇总成整段理解）；
 *   全程**不下载视频文件**，截图只在内存里交模型。未装 Playwright / 未开 LLM_VISION 时如实标
 *   「未确认在播」「未交 VLM」「占位看懂」，绝不假装看懂；不勾选时视频只作**信号**记录（标题 / 链接）。
 * - 灵感**不自动交付智能体**：入 idea 库后由用户勾选「注入 Brief」再驱动创作。
 */
export function SurfPanel({
  task,
  onToast,
  onChanged,
}: {
  task: TaskRecord;
  onToast: (text: string, error?: boolean) => void;
  onChanged?: () => void | Promise<void>;
}) {
  const [tab, setTab] = useState<'surf' | 'pool'>('surf');
  const [sessions, setSessions] = useState<SurfSessionView[]>([]);
  const [pool, setPool] = useState<SurfCard[]>([]);
  const [working, setWorking] = useState('');
  const [provider, setProvider] = useState('');
  const [topic, setTopic] = useState('');
  const [watchVideos, setWatchVideos] = useState(false);
  const [videoUrlsText, setVideoUrlsText] = useState('');
  const [selected, setSelected] = useState<Set<string>>(new Set());
  /** 跨会话「读过哪些页」的记忆（browser 通道攒下的；下一轮挑条目就跳过它们） */
  const [history, setHistory] = useState<SurfHistoryView | null>(null);
  const pollTimer = useRef<number | null>(null);

  const schedulePoll = useCallback(() => {
    if (pollTimer.current) return;
    pollTimer.current = window.setTimeout(() => {
      pollTimer.current = null;
      void loadSurf();
    }, 2000);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadSurf = useCallback(async () => {
    try {
      const data = await api.surfTask(task.id);
      setSessions(data.sessions);
      if (data.sessions.some((session) => ACTIVE.has(session.status))) schedulePoll();
    } catch {
      /* 读取失败不阻塞主流程 */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [task.id, schedulePoll]);

  const loadPool = useCallback(async () => {
    try {
      const data = await api.surfPool();
      setPool(data.cards);
    } catch {
      /* 读取失败不阻塞主流程 */
    }
  }, []);

  /** 本租户「读过哪些页」的记忆（只取最近几条给面板看；关掉开关时后端恒返回空，不拿别处的凑） */
  const loadHistory = useCallback(async () => {
    try {
      setHistory(await api.surfHistory());
    } catch {
      /* 读取失败不阻塞主流程 */
    }
  }, []);

  const forgetHistory = async () => {
    setWorking('__forget__');
    try {
      const result = await api.clearSurfHistory();
      onToast(`已清掉 ${result.removed} 条读过的页面记忆，下一轮冲浪就当第一次刷这些站`);
      await loadHistory();
    } catch (error) {
      onToast(`清除浏览记忆失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  useEffect(() => {
    void loadSurf();
    void loadPool();
    void loadHistory();
    return () => {
      if (pollTimer.current) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, [loadSurf, loadPool, loadHistory]);

  const startSurf = async () => {
    setWorking('__create__');
    const videoUrls = videoUrlsText
      .split('\n')
      .map((line) => line.trim())
      .filter((line) => /^https?:\/\//i.test(line));
    try {
      await api.createSurf(task.id, {
        provider: provider || undefined,
        topic: topic.trim() || undefined,
        watchVideos: watchVideos || undefined,
        videoUrls: videoUrls.length ? videoUrls : undefined,
      });
      onToast('冲浪会话已受理，开始上网找灵感');
      await loadSurf();
    } catch (error) {
      onToast(`发起冲浪失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const toggleSelect = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const injectSelected = async () => {
    if (!selected.size) {
      onToast('请先在灵感库勾选要采用的灵感卡', true);
      return;
    }
    setWorking('__inject__');
    try {
      const result = await api.applySurf(task.id, { cardIds: [...selected] });
      onToast(
        `${result.applied === 'updated' ? '已更新' : '已注入'} ${result.injected} 张灵感卡到 Brief，可回到流水线开始创作`,
      );
      setSelected(new Set());
      if (onChanged) await onChanged();
    } catch (error) {
      onToast(`注入失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  const dropCard = async (card: SurfCard) => {
    setWorking(card.id);
    try {
      await api.dropSurfCard(card.id);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(card.id);
        return next;
      });
      await loadPool();
    } catch (error) {
      onToast(`删除失败：${errorText(error)}`, true);
    } finally {
      setWorking('');
    }
  };

  return (
    <div className="panel" style={{ marginTop: 16 }}>
      <div className="panel-title">
        灵感冲浪（idea 库）
        <span className="count">
          · {tab === 'surf' ? `${sessions.length} 个会话` : `${pool.length} 张灵感卡`}
          {' '}· 像真人上网刷信息 / 看视频 → 消化成灵感，由你挑选后再创作
        </span>
      </div>

      <div className="muted small" style={{ marginBottom: 10 }}>
        灵感**不会自动喂给智能体**：系统上网冲浪把外部信号消化成灵感卡后沉淀进 idea 库，
        你在「灵感库」里勾选、调整，再「注入 Brief」驱动创作。<code>sample</code> 通道离线产占位灵感（带徽标）；
        <code>browser</code>（Playwright）/ <code>http</code>（检索网关）为真引擎可选通道，缺依赖 / 缺配置时受理即如实失败并点名。
      </div>

      <div className="tabs" style={{ marginBottom: 12 }}>
        <button
          className={`tab${tab === 'surf' ? ' active' : ''}`}
          onClick={() => void (setTab('surf'), loadHistory())}
        >
          本任务冲浪
        </button>
        <button className={`tab${tab === 'pool' ? ' active' : ''}`} onClick={() => void (setTab('pool'), loadPool())}>
          灵感库 ({pool.length})
        </button>
      </div>

      {tab === 'surf' ? (
        <>
          <div className="row" style={{ gap: 8, marginBottom: 14, alignItems: 'flex-end', flexWrap: 'wrap' }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>冲浪通道</label>
              <select value={provider} onChange={(event) => setProvider(event.target.value)}>
                <option value="">自动（跟随服务配置）</option>
                <option value="sample">sample 离线占位（零依赖）</option>
                <option value="auto">auto 择优（browser→http→sample）</option>
                <option value="browser">browser 真无头浏览器（需 Playwright + 白名单）</option>
                <option value="http">http 联网检索网关（需搜索配置）</option>
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0, flex: 1, minWidth: 180 }}>
              <label>主题（留空用 Brief）</label>
              <input
                value={topic}
                onChange={(event) => setTopic(event.target.value)}
                placeholder="如：夏季露营装备测评"
              />
            </div>
            <label
              className="muted small"
              style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2, cursor: 'pointer' }}
              title="仅 browser 通道会像真人在线看：关遮罩→点播放→校验真的在播→播放中分组截图→逐组交 VLM→整段汇总（不下载视频）"
            >
              <input
                type="checkbox"
                checked={watchVideos}
                onChange={(event) => setWatchVideos(event.target.checked)}
              />
              在线看视频（需 browser）
            </label>
            <button className="btn btn-primary" disabled={working !== ''} onClick={() => void startSurf()}>
              {working === '__create__' ? <Spinner /> : null} 立即冲浪
            </button>
          </div>

          <div className="field" style={{ marginBottom: 14 }}>
            <label>指定要在线看的视频页（一行一个 URL，选了 browser 即自动视为要在线看；越界链接会如实记为「已拒绝」）</label>
            <textarea
              rows={3}
              value={videoUrlsText}
              onChange={(event) => setVideoUrlsText(event.target.value)}
              placeholder={'https://www.example.com/video/12345\nhttps://www.example.com/watch?v=abc'}
            />
            <span className="muted small">
              在线视频通常下载不下来，所以走「真人在线看」：进页面点播放→播放中抽帧截图→按组交 VLM 看懂，
              标题 / 简介 / 评论另从页面 DOM 直抓（抓不到的靠截图 OCR）。
            </span>
          </div>

          {sessions.length === 0 ? (
            <div className="muted small">还没有冲浪会话。点「立即冲浪」让系统上网刷一轮信息，把灵感沉淀进 idea 库。</div>
          ) : null}

          {sessions.map((session) => (
            <SessionCard key={session.id} session={session} />
          ))}

          <ReadHistoryBlock
            history={history}
            busy={working !== ''}
            onForget={() => void forgetHistory()}
          />
        </>
      ) : (
        <>
          <div className="row" style={{ gap: 8, marginBottom: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <button
              className="btn btn-primary"
              disabled={working !== '' || !selected.size}
              onClick={() => void injectSelected()}
            >
              {working === '__inject__' ? <Spinner /> : null} 注入选中到 Brief（{selected.size}）
            </button>
            <button
              className="btn btn-ghost"
              disabled={working !== ''}
              onClick={() => setSelected(new Set(pool.map((card) => card.id)))}
            >
              全选
            </button>
            <button className="btn btn-ghost" disabled={working !== ''} onClick={() => setSelected(new Set())}>
              清空选择
            </button>
            <span className="muted small">勾选灵感 → 注入当前任务 Brief → 回到流水线开始创作</span>
          </div>

          {pool.length === 0 ? (
            <div className="muted small">
              灵感库还是空的。到「本任务冲浪」发起一次冲浪，或开启「自主冲浪」让系统按主题定时囤灵感。
            </div>
          ) : null}

          {pool.map((card) => (
            <PoolCard
              key={card.id}
              card={card}
              checked={selected.has(card.id)}
              busy={working === card.id}
              disabled={working !== ''}
              onToggle={() => toggleSelect(card.id)}
              onDrop={() => void dropCard(card)}
            />
          ))}
        </>
      )}
    </div>
  );
}

/** 跨会话「读过哪些页」的记忆：下一轮真人式浏览就照这份列表跳过读过的条目、改读没见过的。 */
function ReadHistoryBlock({
  history,
  busy,
  onForget,
}: {
  history: SurfHistoryView | null;
  busy: boolean;
  onForget: () => void;
}) {
  if (!history) return null;
  if (!history.remember) {
    return (
      <div className="muted small" style={{ marginTop: 12 }}>
        没开「记住读过哪些页」（<code>SURF_REMEMBER_READ=false</code>）：每一轮都当第一次刷这些站，
        同一批条目每轮都可能被重读一遍。
      </div>
    );
  }
  const items = history.items;
  const shown = items.slice(0, 8);
  return (
    <div style={{ marginTop: 12 }}>
      <div className="row small" style={{ gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>读过的页（跨会话记忆）</strong>
        <Chip
          tone="tone-idle"
          title={`上限 SURF_HISTORY_MAX=${history.max}：超了就先忘最旧的，忘了的下一轮会重新读`}
        >
          已记住 {items.length} 条 / 上限 {history.max}
        </Chip>
        <button className="btn btn-ghost" disabled={busy || !items.length} onClick={onForget}>
          {busy ? <Spinner /> : null} 让它忘掉这些
        </button>
        <span className="muted">
          下一轮挑条目会跳过这里列出的页（整版都读过时照样重读并如实说明；你指定的视频链接永远照看）
        </span>
      </div>
      {shown.length ? (
        <div className="muted small" style={{ marginTop: 6 }}>
          {shown.map((row) => (
            <div key={row.id} style={{ marginTop: 2 }}>
              · {row.title || row.url}
              {row.reads > 1 ? `（读过 ${row.reads} 遍）` : ''} · {formatDateTime(row.last_seen)} ·{' '}
              <a href={row.url} target="_blank" rel="noreferrer noopener" className="small" title={row.url}>
                {row.url}
              </a>
            </div>
          ))}
          {items.length > shown.length ? (
            <div style={{ marginTop: 2 }}>（另有 {items.length - shown.length} 条更早读过的页未列出）</div>
          ) : null}
        </div>
      ) : (
        <div className="muted small" style={{ marginTop: 6 }}>
          还没读过任何条目页——跑一轮 <code>browser</code> 冲浪后，这里列出它真读进去的页（列表页与首页不算读过）。
        </div>
      )}
    </div>
  );
}

/** 单个冲浪会话卡：状态 / 进度 / 告警 / 失败原因 / 产出灵感卡数。 */
function SessionCard({ session }: { session: SurfSessionView }) {
  const meta = SESSION_META[session.status] ?? { label: session.status, tone: 'tone-idle' };
  const lastNote = session.history.length ? session.history[session.history.length - 1].note : '';
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div
        className="card-title"
        style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}
      >
        <span className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <Chip tone={meta.tone}>{meta.label}</Chip>
          <Chip tone="tone-idle" title={session.requested_provider}>
            {PROVIDER_LABEL[session.provider] ?? session.provider}
          </Chip>
          <Chip tone="tone-idle">{session.topic || '（无主题）'}</Chip>
          {session.simulated ? (
            <Chip tone="tone-warn" title="离线 / 降级引擎产出的占位灵感，非真实冲浪所得">
              占位灵感
            </Chip>
          ) : null}
          {session.estimated_cost_usd > 0 ? (
            <Chip tone="tone-warn" mono>
              预估 ${session.estimated_cost_usd.toFixed(3)}
            </Chip>
          ) : null}
        </span>
        <span className="muted small mono">{session.id}</span>
      </div>

      {ACTIVE.has(session.status) ? (
        <div style={{ marginTop: 8 }}>
          <div className="muted small">
            {lastNote}（{session.progress}%）
          </div>
          <div style={{ height: 6, borderRadius: 3, background: 'var(--line, #2a2f3a)', marginTop: 4 }}>
            <div style={{ width: `${session.progress}%`, height: '100%', background: 'currentColor', opacity: 0.65 }} />
          </div>
        </div>
      ) : null}

      {session.status === 'failed' ? (
        <div className="small" style={{ marginTop: 8, color: 'var(--bad, #e5484d)' }}>
          失败原因：{session.error || '未知'}
        </div>
      ) : null}

      {session.status === 'done' ? (
        <div className="small" style={{ marginTop: 8 }}>
          已产出 {session.cards.length} 张灵感卡并沉淀进灵感库
          {session.signals.length ? `（采集 ${session.signals.length} 条外部信号）` : ''}。
        </div>
      ) : null}

      {session.signals.some((s) => s.kind === 'video' || s.kind === 'video_error') ? (
        <div style={{ marginTop: 10 }}>
          <div className="muted small" style={{ marginBottom: 6 }}>
            本会话在线看的视频（先扫标题挑出贴主题的，再像真人进页点播放、播放中分组抽帧交 VLM，非下载文件）：
          </div>
          {session.signals
            .filter((s) => s.kind === 'video' || s.kind === 'video_error')
            .map((signal, index) => (
              <VideoSignal key={`${signal.url || 'video'}-${index}`} signal={signal} />
            ))}
        </div>
      ) : null}

      {session.signals.some((s) => TEXT_KINDS.includes(s.kind || '')) ? (
        <div style={{ marginTop: 10 }}>
          <div className="muted small" style={{ marginBottom: 4 }}>
            这一轮浏览读到的页面与条目（像真人一样刷到 / 站内搜到 / 点「下一页」翻到 / 扫标题认出主题词点进去）：
          </div>
          {session.signals
            .filter((s) => TEXT_KINDS.includes(s.kind || ''))
            .map((signal, index) => (
              <TextSignal key={`${signal.url || 'signal'}-${index}`} signal={signal} />
            ))}
        </div>
      ) : null}

      {session.signals.some((s) => s.kind === 'skipped') ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          按安全策略拒绝的越界链接：
          {session.signals
            .filter((s) => s.kind === 'skipped')
            .map((signal, index) => (
              <div key={`${signal.url || 'skipped'}-${index}`}>
                {signal.url}（{signal.excerpt}）
              </div>
            ))}
        </div>
      ) : null}

      {session.warnings.length ? (
        <div className="muted small" style={{ marginTop: 8 }}>
          {session.warnings.map((warning) => (
            <div key={warning}>⚠ {warning}</div>
          ))}
        </div>
      ) : null}

      <div className="muted small" style={{ marginTop: 8 }}>
        创建于 {formatDateTime(session.created_at)}
        {session.updated_at !== session.created_at ? ` · 最近推进 ${formatDateTime(session.updated_at)}` : ''}
      </div>
    </div>
  );
}

/** 看视频信号：播放证据 + 分组画面结论 + 页面信息（标题/简介/UP 主/评论）+ simulated 徽标。 */
const CONFIDENCE_LABEL: Record<string, string> = {
  high: '高置信',
  medium: '中置信',
  low: '低置信',
};

/** 兼容旧数据：评论曾经是字符串，现在是一条条结构。 */
function commentRows(comments: SurfSignal['comments']): SurfComment[] {
  if (Array.isArray(comments)) return comments;
  const text = String(comments || '').trim();
  return text ? [{ author: '', text, likes: '' }] : [];
}

/** 这条信号是真人式浏览的哪一步拿到的（browser 通道逐条标注，供核对来源）。 */
const VIA_LABEL: Record<string, string> = {
  seed: '起点站首页',
  search: '站内搜索结果',
  feed: '信息流点进',
  direct: '指定链接',
};

const TEXT_KIND_LABEL: Record<string, string> = {
  page: '页面',
  hot: '热榜',
  search: '搜索页',
  item: '条目页',
  error: '访问失败',
};

const TEXT_KINDS = ['page', 'hot', 'search', 'item', 'error'];

/** 一条文本信号：入口 + 标题（可点原页）+ 浏览证据（滚了几轮、停了多久、翻到第几页、凭什么点进来）+ 诚实告警。 */
function TextSignal({ signal }: { signal: SurfSignal }) {
  const isError = signal.kind === 'error';
  const via = VIA_LABEL[signal.via || ''] ?? signal.via;
  const dwelled = signal.scrolls != null || signal.pauses != null || signal.dwell_ms != null;
  const comments = commentRows(signal.comments);
  return (
    <div
      className="muted small"
      style={{
        marginTop: 3,
        paddingLeft: 8,
        borderLeft: `2px solid ${isError ? 'var(--bad, #e5484d)' : 'var(--line, #2a2f3a)'}`,
      }}
    >
      <Chip tone={isError ? 'tone-bad' : 'tone-idle'}>
        {TEXT_KIND_LABEL[signal.kind || ''] ?? signal.kind}
      </Chip>
      {via ? (
        <Chip tone="tone-idle" title="这条情报是真人式浏览的哪一步拿到的">
          入口：{via}
        </Chip>
      ) : null}
      {signal.list_page && signal.list_page > 1 ? (
        <Chip tone="tone-idle" title={signal.paged_by ? `点了「${signal.paged_by}」翻到这一页` : undefined}>
          列表第 {signal.list_page} 页
          {signal.paged_by ? `（点了「${signal.paged_by}」）` : ''}
        </Chip>
      ) : null}
      {signal.picked_for ? (
        <Chip tone="tone-idle" title="冲浪时扫标题认出这些主题词才点进来的；没标这条就是按列表顺序顺路读的">
          因标题含「{signal.picked_for}」点进
        </Chip>
      ) : null}
      {signal.query ? (
        <Chip
          tone="tone-idle"
          title={
            signal.search_tried && signal.search_tried.length > 1
              ? `依次搜过：${signal.search_tried.join(' → ')}`
              : '这一页是站内搜索这些关键词搜出来的'
          }
        >
          搜「{signal.query}」搜到
          {signal.search_tried && signal.search_tried.length > 1
            ? `（换了 ${signal.search_tried.length - 1} 次说法）`
            : ''}
        </Chip>
      ) : null}
      {signal.suggested ? (
        <Chip tone="tone-idle" title="这版关键词不是我们切的词，是打字时站点自己弹出来的搜索联想里认得出主题词的那条">
          站点联想「{signal.suggested}」
        </Chip>
      ) : null}
      {signal.wall ? (
        <Chip tone="tone-warn" title="读到门口就被挡住（要登录/要订阅/要验证/页面已不存在）：摘录只是门口那几句话，没有绕墙，这页不算读到了内容">
          被{signal.wall}挡住（没读到正文）
        </Chip>
      ) : null}
      {dwelled && !isError ? (
        <Chip tone="tone-idle" title="下滑轮数 / 原地读完的停顿次数 / 合计停留时长 / 开这页之前的犹豫">
          滚 {signal.scrolls ?? 0} 次
          {signal.pauses ? ` · 停 ${signal.pauses} 次` : ''}
          {signal.dwell_ms ? ` · ${Math.round(signal.dwell_ms / 100) / 10}s` : ''}
          {signal.gap_ms ? ` · 开页前停 ${Math.round(signal.gap_ms / 100) / 10}s` : ''}
          {signal.expanded ? ` · 展开 ${signal.expanded} 处` : ''}
        </Chip>
      ) : null}
      {' '}
      {signal.url ? (
        <a
          href={signal.url}
          target="_blank"
          rel="noreferrer noopener"
          className="small"
          title={signal.excerpt}
          style={{ color: 'inherit' }}
        >
          {signal.title || signal.url}
        </a>
      ) : (
        <span>{signal.title}</span>
      )}
      {signal.listed_as ? <>（信息流里写：{signal.listed_as}）</> : null}
      {signal.landed_on ? <>（被带到：{signal.landed_on}）</> : null}
      {comments.length ? (
        <div style={{ marginTop: 3 }}>
          <span style={{ opacity: 0.75 }}>评论区（DOM 逐条抓到 {comments.length} 条，是网友原话不是总结）：</span>
          {comments.map((row, index) => (
            <div key={`${index}-${row.text.slice(0, 12)}`} style={{ marginTop: 2 }}>
              {row.author ? `${row.author}：` : ''}
              {row.text}
              {row.likes ? `（👍 ${row.likes}）` : ''}
            </div>
          ))}
        </div>
      ) : null}
      {(signal.warnings ?? []).map((note) => (
        <div key={note} style={{ marginTop: 2, color: 'var(--warn, #d29922)' }}>
          ⚠ {note}
        </div>
      ))}
    </div>
  );
}

function VideoSignal({ signal }: { signal: SurfSignal }) {
  const isError = signal.kind === 'video_error';
  const comments = commentRows(signal.comments);
  const segments = signal.segments ?? [];
  return (
    <div
      className="card"
      style={{
        marginBottom: 8,
        padding: 10,
        borderLeft: `3px solid ${isError ? 'var(--bad, #e5484d)' : 'var(--line, #2a2f3a)'}`,
      }}
    >
      <div className="row" style={{ gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <span className="small" style={{ fontWeight: 600 }}>{signal.title || '（无标题）'}</span>
        {VIA_LABEL[signal.via || ''] ?? signal.via ? (
          <Chip tone="tone-idle" title="这个视频页是真人式浏览的哪一步找到的（direct = 请求里指定的链接）">
            入口：{VIA_LABEL[signal.via || ''] ?? signal.via}
          </Chip>
        ) : null}
        {signal.picked_for ? (
          <Chip tone="tone-idle" title="刷信息流时扫标题认出这些主题词才点进去看的；没标就是按页面顺序顺路看到的">
            因标题含「{signal.picked_for}」点进来看
          </Chip>
        ) : null}
        {signal.listed_as ? (
          <Chip tone="tone-idle" title="信息流里看到的标题（页面标题可能与之不同）">
            列表标题：{signal.listed_as}
          </Chip>
        ) : null}
        {isError ? (
          <Chip tone="tone-bad">看懂失败</Chip>
        ) : (
          <>
            <Chip tone={signal.played ? 'tone-ok' : 'tone-warn'} title="点播放后是否确认播放器真的在推进">
              {signal.played ? '播放中' : '未确认在播'}
            </Chip>
            <Chip tone="tone-idle">
              {signal.frames ?? 0} 帧 / {signal.groups ?? 0} 组
              {signal.group_frames ? `（每组 ${signal.group_frames}）` : ''}
            </Chip>
            {signal.watch_seconds ? (
              <Chip tone="tone-idle" title="抽帧覆盖到的播放时长">
                看了 {signal.watch_seconds}s{signal.duration ? ` / ${signal.duration}s` : ''}
              </Chip>
            ) : null}
            {signal.stalled ? <Chip tone="tone-bad">画面未推进</Chip> : null}
            {signal.vision_skipped ? (
              <Chip tone="tone-warn" title="网关未启用图像输入（LLM_VISION）：抽了帧但没有画面结论">
                未交 VLM
              </Chip>
            ) : null}
            {signal.ocr_texts ? (
              <Chip tone="tone-idle" title="DOM 没抓到的标题/简介/评论，改由整页截图逐字转写补齐（只补空缺，不覆盖 DOM 文字）">
                含截图 OCR 文字
              </Chip>
            ) : null}
            <Chip tone="tone-idle">
              {CONFIDENCE_LABEL[signal.confidence || ''] ?? signal.confidence ?? '置信—'}
            </Chip>
            {signal.simulated ? (
              <Chip tone="tone-warn" title="画面摘要由离线/无视觉网关产出，未真实看懂">
                占位看懂
              </Chip>
            ) : null}
          </>
        )}
        {signal.url ? (
          <a
            href={signal.url}
            target="_blank"
            rel="noreferrer noopener"
            className="muted small"
            style={{ color: 'inherit' }}
          >
            打开视频页
          </a>
        ) : null}
      </div>

      {isError ? (
        <div className="muted small" style={{ marginTop: 4 }}>{signal.excerpt}</div>
      ) : (
        <>
          {signal.visual_summary ? (
            <div className="small" style={{ marginTop: 6 }}>画面：{signal.visual_summary}</div>
          ) : null}
          {segments.length > 1 ? (
            <div style={{ marginTop: 6 }}>
              <span className="muted small">分组看懂（每组一次多模态调用）：</span>
              {segments.map((seg) => (
                <div key={seg.index} className="muted small" style={{ marginTop: 2 }}>
                  第 {seg.index} 组（{seg.from}s–{seg.to}s，{seg.frames} 帧）：{seg.summary}
                  {seg.on_screen_text ? `｜画面文字：${seg.on_screen_text}` : ''}
                  {seg.simulated ? '（占位）' : ''}
                </div>
              ))}
            </div>
          ) : null}
          {signal.on_screen_text ? (
            <div className="muted small" style={{ marginTop: 4 }}>屏上文字/OCR：{signal.on_screen_text}</div>
          ) : null}
          {signal.comment_insights ? (
            <div className="muted small" style={{ marginTop: 4 }}>评论洞察：{signal.comment_insights}</div>
          ) : null}
          {signal.description ? (
            <div className="muted small" style={{ marginTop: 4 }}>简介：{signal.description}</div>
          ) : null}
          {signal.uploader || signal.views || signal.publish_date ? (
            <div className="muted small" style={{ marginTop: 4 }}>
              {[signal.uploader && `UP 主：${signal.uploader}`, signal.views && `播放量：${signal.views}`, signal.publish_date && `发布：${signal.publish_date}`]
                .filter(Boolean)
                .join('　')}
            </div>
          ) : null}
          {comments.length ? (
            <div style={{ marginTop: 6 }}>
              <span className="muted small">
                页面评论（{signal.ocr_texts ? 'DOM 缺口由整页截图 OCR 补齐' : 'DOM 直抓'} {comments.length} 条）：
              </span>
              {comments.map((row, index) => (
                <div key={`${index}-${row.text.slice(0, 12)}`} className="muted small" style={{ marginTop: 2 }}>
                  {row.author ? `${row.author}：` : ''}
                  {row.text}
                  {row.likes ? `（${row.likes}）` : ''}
                </div>
              ))}
            </div>
          ) : null}
          {(signal.warnings ?? []).map((note) => (
            <div key={note} className="small" style={{ marginTop: 4, color: 'var(--warn, #d29922)' }}>
              ⚠ {note}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/** 灵感库里的一张卡：勾选框 + 点子 / 角度 / 钩子 / 为什么现在 / 来源链接 + simulated 徽标 + 删除。 */
function PoolCard({
  card,
  checked,
  busy,
  disabled,
  onToggle,
  onDrop,
}: {
  card: SurfCard;
  checked: boolean;
  busy: boolean;
  disabled: boolean;
  onToggle: () => void;
  onDrop: () => void;
}) {
  return (
    <div className="card" style={{ marginBottom: 12 }}>
      <div className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
        <input type="checkbox" checked={checked} onChange={onToggle} style={{ marginTop: 4 }} />
        <div style={{ flex: 1 }}>
          <div className="row" style={{ gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
            <Chip tone="tone-idle">{card.topic || '（无主题）'}</Chip>
            <Chip tone="tone-idle" title={card.provider}>
              {PROVIDER_LABEL[card.provider] ?? card.provider}
            </Chip>
            {card.simulated ? (
              <Chip tone="tone-warn" title="离线 / 降级引擎产出的占位灵感，非真实冲浪所得">
                占位
              </Chip>
            ) : null}
          </div>

          <div className="small" style={{ fontWeight: 600 }}>{card.idea || '（缺点子）'}</div>
          {card.angle ? (
            <div className="muted small" style={{ marginTop: 2 }}>
              角度：{card.angle}
            </div>
          ) : null}
          {card.hook ? (
            <div className="muted small" style={{ marginTop: 2 }}>
              钩子：{card.hook}
            </div>
          ) : null}
          {card.why_now ? (
            <div className="muted small" style={{ marginTop: 2 }}>
              为什么现在：{card.why_now}
            </div>
          ) : null}

          {card.sources.length ? (
            <div className="muted small" style={{ marginTop: 6 }}>
              参考：
              {card.sources.map((src, index) => (
                <span key={`${src}-${index}`}>
                  {index > 0 ? '　' : ''}
                  <a href={src} target="_blank" rel="noreferrer noopener" style={{ color: 'inherit' }}>
                    {src.replace(/^https?:\/\//, '').slice(0, 48)}
                  </a>
                  {looksLikeVideo(src) ? (
                    <span title="视频链接：仅作信号记录，未逐帧观看；要「看懂」请到「视频理解」面板">（视频·未看）</span>
                  ) : null}
                </span>
              ))}
            </div>
          ) : null}

          <div className="muted small" style={{ marginTop: 6 }}>
            入池于 {formatDateTime(card.created_at)}
          </div>
        </div>
        <button className="btn btn-sm btn-ghost" disabled={disabled} onClick={onDrop} title="从灵感库删除这张卡">
          {busy ? <Spinner /> : null} 删除
        </button>
      </div>
    </div>
  );
}
