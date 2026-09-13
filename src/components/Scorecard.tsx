import type { QualityScorecard } from '../lib/types.ts';
import { scoreTone } from '../lib/format.ts';

const DIMENSIONS: { key: keyof QualityScorecard; label: string }[] = [
  { key: 'structure', label: '结构完整' },
  { key: 'clarity', label: '表达清晰' },
  { key: 'brand_voice', label: '品牌语气' },
  { key: 'appeal', label: '吸引力' },
  { key: 'fact_safety', label: '事实安全' },
  { key: 'compliance', label: '合规性' },
];

export function Scorecard({ scorecard, showHero = true }: { scorecard: QualityScorecard | null; showHero?: boolean }) {
  if (!scorecard) {
    return <div className="muted small">暂无质量评分（需完成 A5 / A6 / A7 审核）</div>;
  }

  return (
    <div className="row" style={{ alignItems: 'center', gap: 20 }}>
      {showHero ? (
        <div style={{ textAlign: 'center', minWidth: 86 }}>
          <div className="score-hero">{Math.round(scorecard.overall)}</div>
          <div className="muted small" style={{ marginTop: 4 }}>
            综合质量分
          </div>
        </div>
      ) : null}
      <div className="score-row" style={{ flex: 1 }}>
        {DIMENSIONS.map((dim) => {
          const value = Math.round(Number(scorecard[dim.key]) || 0);
          return (
            <div className="score-cell" key={dim.key}>
              <div className="score-label">
                <span>{dim.label}</span>
                <span className={scoreTone(value)} style={{ background: 'none', border: 'none' }}>
                  {value || '—'}
                </span>
              </div>
              <div className="score-bar">
                <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
