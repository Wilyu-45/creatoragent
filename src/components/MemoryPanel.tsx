import { useCallback, useEffect, useState } from 'react';
import { api, type MemoryHitView, type MemoryView } from '../lib/api.ts';
import { formatDateTime, scoreTone } from '../lib/format.ts';
import { Chip, Empty, Spinner, Table } from './ui.tsx';

const KIND_TONE: Record<string, string> = {
  brand: 'tone-ok',
  case: 'tone-info',
  template: 'tone-warn',
  lesson: 'tone-bad',
};

/**
 * A11 记忆库面板：展示跨任务沉淀的知识卡片，并提供与智能体同源的检索入口。
 *
 * 「检索」走的是 ``POST /api/memory/search``，与编排层给 A1/A2/A4 注入召回
 * 用的是同一套打分逻辑，因此这里看到的结果就是智能体实际复用的资产。
 */
export function MemoryPanel() {
  const [view, setView] = useState<MemoryView | null>(null);
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<MemoryHitView[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const reload = useCallback(async () => {
    try {
      setView(await api.memory());
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const search = async (): Promise<void> => {
    const keyword = query.trim();
    if (!keyword) {
      setHits(null);
      return;
    }
    setBusy(true);
    try {
      const result = await api.searchMemory(keyword, 5);
      setHits(result.hits);
      setError('');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };

  if (!view) return <div className="muted small">{error || '记忆库加载中…'}</div>;

  const { stats, cards } = view;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div className="section-h">团队记忆库（A11 沉淀，跨任务复用）</div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
          <Chip tone="tone-info">卡片 {stats.total}</Chip>
          <Chip>容量上限 {stats.capacity}</Chip>
          <Chip tone="tone-ok">已被复用 {stats.reused}</Chip>
          <Chip>覆盖任务 {stats.tasks}</Chip>
          {stats.brands.slice(0, 4).map((brand) => (
            <Chip key={brand} mono>
              {brand}
            </Chip>
          ))}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
          {stats.by_kind.map((item) => (
            <Chip key={item.kind} tone={KIND_TONE[item.kind] ?? 'tone-idle'}>
              {item.label} {item.count}
            </Chip>
          ))}
        </div>
        <div className="row" style={{ gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
          <Chip tone="tone-ok" title={`${stats.fresh_days} 天内入库视为新鲜`}>
            新鲜 {stats.fresh}
          </Chip>
          <Chip tone={stats.stale > 0 ? 'tone-warn' : 'tone-idle'}>陈旧 {stats.stale}</Chip>
          <Chip tone="tone-idle">最旧 {stats.oldest_days} 天</Chip>
          <Chip tone="tone-idle" title="超过该天数会在写入时自动下线">
            过期阈值 {stats.max_age_days} 天
          </Chip>
        </div>
      </div>

      <div>
        <div className="section-h">检索召回（与智能体同源）</div>
        <div className="row" style={{ gap: 8 }}>
          <input
            value={query}
            placeholder="例如：冷萃 咖啡 早八通勤 小红书"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') void search();
            }}
          />
          <button className="btn btn-sm" onClick={() => void search()} disabled={busy}>
            {busy ? <Spinner /> : null} 检索
          </button>
        </div>

        {hits === null ? (
          <div className="muted small" style={{ marginTop: 8 }}>
            输入描述即可查看会召回哪些历史资产。
          </div>
        ) : hits.length === 0 ? (
          <Empty>没有命中任何历史资产</Empty>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8, marginTop: 8 }}>
            {hits.map((hit) => (
              <div className="card" key={hit.id}>
                <div className="row" style={{ justifyContent: 'space-between', gap: 8 }}>
                  <strong>{hit.title}</strong>
                  <span className="row" style={{ gap: 6 }}>
                    {hit.reasons.map((reason) => (
                      <Chip key={reason}>{reason}</Chip>
                    ))}
                    <Chip tone={scoreTone(hit.score * 100)}>score {hit.score}</Chip>
                  </span>
                </div>
                <div className="muted small" style={{ marginTop: 4, whiteSpace: 'pre-wrap' }}>
                  {hit.content}
                </div>
                {hit.reuse_hint ? (
                  <div className="law" style={{ marginTop: 4 }}>
                    复用建议：{hit.reuse_hint}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        )}
      </div>

      <div>
        <div className="section-h">全部卡片</div>
        {cards.length === 0 ? (
          <Empty>还没有沉淀任何知识，完成任务后 A11 会自动写入</Empty>
        ) : (
          <Table head={['类型', '标题', '品牌 / 渠道', '来源任务', '入库时间']}>
            {cards.map((card) => (
              <tr key={card.id}>
                <td>
                  <Chip tone={KIND_TONE[card.kind] ?? 'tone-idle'}>{card.kind_label}</Chip>
                </td>
                <td>
                  <div>{card.title}</div>
                  <div className="muted small" style={{ whiteSpace: 'pre-wrap' }}>
                    {card.content.length > 120 ? `${card.content.slice(0, 120)}…` : card.content}
                  </div>
                </td>
                <td>
                  {card.brand || '—'} / {card.channel || '—'}
                </td>
                <td className="mono small">{card.task_id}</td>
                <td className="small">{formatDateTime(card.created_at)}</td>
              </tr>
            ))}
          </Table>
        )}
      </div>

      {error ? <div className="muted small">出错了：{error}</div> : null}
    </div>
  );
}
