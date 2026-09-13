import type { AgentId, AgentResult, GateRecord, Phase, QualityScorecard } from './types.ts';

/**
 * 门禁控制器（Gatekeeper）
 *
 * 对应《creator.md》第 7 章「权限与门禁设计」，以及《plan.md》2.2.1 的条件边逻辑。
 * 规则：
 *   - A5 质量分低于阈值 → 退回 A4
 *   - A6 高风险未修正 → 否决，禁止发布
 *   - A7 合规不通过 → 否决，禁止发布
 *   - 返工次数用尽仍不通过 → 升级为人工介入
 *   - Turn Budget 命中 → 人工介入
 */

export type GateVerdict = 'pass' | 'revise' | 'escalate';

export interface GateDecision {
  verdict: GateVerdict;
  reason: string;
  /** 合并后的返工要求，直接交给 A4 */
  requests: string[];
  /** 阻断项描述，用于界面展示 */
  blocking: string[];
  /** 每个审核智能体的裁决，写入 TaskRecord.gates */
  records: Omit<GateRecord, 'round' | 'created_at'>[];
}

export interface GateInput {
  results: AgentResult[];
  revision: number;
  maxRevisions: number;
  qualityThreshold: number;
  scorecard: QualityScorecard;
}

const REVIEWER_PHASE: Record<string, Phase> = {
  A5: 'EDITING',
  A6: 'FACT_CHECK',
  A7: 'COMPLIANCE',
};

export function decideGate(input: GateInput): GateDecision {
  const { results, revision, maxRevisions, qualityThreshold, scorecard } = input;

  const records: GateDecision['records'] = [];
  const requests: string[] = [];
  const blocking: string[] = [];

  let hasReject = false;
  let hasRevise = false;

  for (const result of results) {
    const phase = REVIEWER_PHASE[result.agent_id];
    if (!phase) continue;

    let verdict = result.gate_result ?? 'pass';

    // A5 额外应用质量分阈值门禁（creator.md 第 7 章：质量分低于阈值退回 A4）
    if (result.agent_id === 'A5' && verdict === 'pass' && scorecard.overall < qualityThreshold) {
      verdict = 'revise';
      result.revision_requests.push(
        `[编辑·质量门禁] 综合质量分 ${scorecard.overall.toFixed(0)} 低于阈值 ${qualityThreshold}，需整体重写`,
      );
    }

    if (verdict === 'reject') hasReject = true;
    if (verdict === 'revise') hasRevise = true;

    if (verdict !== 'pass') {
      requests.push(...result.revision_requests);
      blocking.push(
        ...result.revision_requests
          .filter((r) => r.includes('必改') || r.includes('否决') || r.includes('阻断'))
          .slice(0, 3),
      );
    }

    records.push({
      phase,
      agent_id: result.agent_id as AgentId,
      verdict,
      score:
        result.agent_id === 'A5'
          ? scorecard.overall
          : result.agent_id === 'A6'
            ? scorecard.fact_safety
            : scorecard.compliance,
      blocking: result.revision_requests.slice(0, 5),
    });
  }

  if (hasReject) {
    return {
      verdict: 'escalate',
      reason: '事实核查或合规智能体行使否决权，需人工复核后方可继续',
      requests,
      blocking,
      records,
    };
  }

  if (hasRevise) {
    if (revision >= maxRevisions) {
      return {
        verdict: 'escalate',
        reason: `已用完 ${maxRevisions} 轮返工额度仍未通过审核，升级人工处理`,
        requests,
        blocking,
        records,
      };
    }
    return {
      verdict: 'revise',
      reason: `存在 ${requests.length} 条需修订项，退回文案智能体修订（第 ${revision + 1}/${maxRevisions} 轮）`,
      requests,
      blocking,
      records,
    };
  }

  return {
    verdict: 'pass',
    reason: '编辑、事实核查、合规三项审核全部通过',
    requests: [],
    blocking: [],
    records,
  };
}

/** 把各审核智能体的结论合并为统一质量记分卡。 */
export function buildScorecard(input: {
  editor: Record<string, unknown> | null;
  factCheck: Record<string, unknown> | null;
  compliance: Record<string, unknown> | null;
}): QualityScorecard {
  const card = input.editor?.scorecard as Record<string, unknown> | undefined;
  const num = (value: unknown, fallback: number): number =>
    typeof value === 'number' && Number.isFinite(value) ? value : fallback;

  const structure = num(card?.structure, 80);
  const clarity = num(card?.clarity, 80);
  const brandVoice = num(card?.brand_voice, 80);
  const appeal = num(card?.appeal, 80);

  const factSummary = input.factCheck?.summary as Record<string, unknown> | undefined;
  const unverified = num(factSummary?.unverified, 0);
  const exaggerated = num(factSummary?.exaggerated, 0);
  const factSafety = Math.max(0, 100 - unverified * 15 - exaggerated * 12);

  const complianceSummary = input.compliance?.summary as Record<string, unknown> | undefined;
  const blocker = num(complianceSummary?.blocker, 0);
  const major = num(complianceSummary?.major, 0);
  const complianceScore =
    num(input.compliance?.compliance_score, 0) ||
    Math.max(0, 100 - blocker * 30 - major * 12);

  const overall =
    card?.overall !== undefined
      ? num(card.overall, 80)
      : Math.round((structure + clarity + brandVoice + appeal) / 4);

  return {
    structure,
    clarity,
    brand_voice: brandVoice,
    appeal,
    fact_safety: factSafety,
    compliance: complianceScore,
    overall: Math.round(overall * 0.5 + factSafety * 0.25 + complianceScore * 0.25),
  };
}
