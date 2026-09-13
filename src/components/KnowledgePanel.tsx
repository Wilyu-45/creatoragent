import type { KnowledgeView } from '../lib/api.ts';
import { severityTone } from '../lib/format.ts';
import { Chip, Table } from './ui.tsx';

const SEVERITY_LABEL: Record<string, string> = {
  blocker: '阻断',
  major: '重要',
  minor: '建议',
};

export function KnowledgePanel({ knowledge }: { knowledge: KnowledgeView | null }) {
  if (!knowledge) return <div className="muted small">知识库加载中…</div>;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div className="section-h">合规词库（广告法 / 平台规范）</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {knowledge.lexicon.map((group) => (
            <div className="card" key={group.category}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <strong>{group.category}</strong>
                <Chip tone={severityTone(group.severity)}>
                  {SEVERITY_LABEL[group.severity] ?? group.severity} · {group.term_count} 词
                </Chip>
              </div>
              <div className="law" style={{ marginTop: 4 }}>
                依据：{group.law}
              </div>
              <div className="hashtag-row">
                {group.terms.map((term) => (
                  <Chip key={term} mono>
                    {term}
                  </Chip>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="section-h">行业专项规则</div>
        <Table head={['行业', '规则数', '覆盖类别']}>
          {knowledge.industry_rules.map((rule) => (
            <tr key={rule.industry}>
              <td>
                <strong>{rule.industry}</strong>
              </td>
              <td>{rule.rule_count}</td>
              <td>{rule.categories.join('、')}</td>
            </tr>
          ))}
        </Table>
      </div>

      <div>
        <div className="section-h">渠道内容规范</div>
        <Table head={['渠道', '内容形态', '长度建议', '推荐结构']}>
          {knowledge.channels.map((channel) => (
            <tr key={channel.channel}>
              <td>
                <strong>{channel.channel}</strong>
              </td>
              <td>{channel.format}</td>
              <td>{channel.length_hint}</td>
              <td>{channel.blocks.join(' / ')}</td>
            </tr>
          ))}
        </Table>
      </div>
    </div>
  );
}
