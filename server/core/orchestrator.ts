import { getAgent } from '../agents/registry.ts';
import type { AgentRunContext } from '../agents/base.ts';
import { getConfig } from '../config.ts';
import { createLogger } from '../logger.ts';
import { blackboard } from './blackboard.ts';
import { eventBus, newId } from './events.ts';
import { buildScorecard, decideGate } from './gatekeeper.ts';
import { taskStore } from './store.ts';
import type {
  A2AMessage,
  AgentId,
  AgentResult,
  Artifact,
  ArtifactType,
  Brief,
  Phase,
  PipelineNode,
  QualityScorecard,
  TaskPacket,
  TaskRecord,
} from './types.ts';
import { PHASE_LABEL } from './types.ts';

const log = createLogger('orchestrator');

/* ------------------------------------------------------------------ */
/* 流水线定义                                                          */
/* ------------------------------------------------------------------ */

interface StageDef {
  agent: AgentId;
  phase: Phase;
}

const PIPELINE: StageDef[] = [
  { agent: 'A0', phase: 'INIT' },
  { agent: 'A1', phase: 'STRATEGY' },
  { agent: 'A2', phase: 'CREATIVE' },
  { agent: 'A3', phase: 'PLANNING' },
  { agent: 'A4', phase: 'DRAFTING' },
  { agent: 'A5', phase: 'EDITING' },
  { agent: 'A6', phase: 'FACT_CHECK' },
  { agent: 'A7', phase: 'COMPLIANCE' },
  { agent: 'A10', phase: 'ANALYZED' },
];

const REVIEW_AGENTS: AgentId[] = ['A5', 'A6', 'A7'];

export interface HumanDecision {
  decision: 'approve' | 'revise' | 'reject';
  comment: string;
}

class TurnBudgetExceeded extends Error {
  constructor(limit: number) {
    super(`Turn Budget（${limit} 次）已耗尽，任务转入人工处理`);
    this.name = 'TurnBudgetExceeded';
  }
}

/* ------------------------------------------------------------------ */
/* 编排器                                                              */
/* ------------------------------------------------------------------ */

export class Orchestrator {
  private waiters = new Map<string, (decision: HumanDecision) => void>();
  private running = new Set<string>();

  /* ---------------------------- 任务创建 ---------------------------- */

  createTask(brief: Brief, options?: { autoApprove?: boolean }): TaskRecord {
    const now = new Date().toISOString();
    const task: TaskRecord = {
      id: newId('task'),
      brief,
      status: 'running',
      phase: 'INIT',
      revision_round: 0,
      turn_used: 0,
      created_at: now,
      updated_at: now,
      finished_at: null,
      packets: [],
      results: [],
      artifacts: [],
      pipeline: PIPELINE.map((stage) => ({
        agent_id: stage.agent,
        phase: stage.phase,
        status: 'pending',
        runs: 0,
        confidence: null,
        gate_result: null,
        summary: '',
        started_at: null,
        finished_at: null,
      })),
      revisions: [],
      gates: [],
      scorecard: null,
      tokens: { prompt: 0, completion: 0, cost_usd: 0 },
      approval: {
        required: true,
        decision: 'pending',
        comment: '',
        decided_at: null,
      },
      error: null,
    };

    taskStore.scheduleSave(task, true);
    this.publish(task, 'task.created', `已创建任务 ${task.id}，开始解析 Brief`, {});
    log.info(`任务 ${task.id} 已创建：${brief.brand} / ${brief.product} / ${brief.channel}`);

    if (options?.autoApprove) this.autoApproveAll.add(task.id);
    void this.run(task).catch((error: unknown) => this.handleFatal(task, error));
    return task;
  }

  private autoApproveAll = new Set<string>();

  /* ---------------------------- 主流程 ------------------------------ */

  private async run(task: TaskRecord): Promise<void> {
    this.running.add(task.id);
    try {
      await this.stageA0(task);

      const planned: AgentId[] = ['A1', 'A2', 'A3'];
      for (const agent of planned) await this.runAgentStep(task, agent);

      let carriedFeedback: string[] = [];
      let approvalCycles = 0;

      for (;;) {
        // ---------- 创作 + 审核门禁循环 ----------
        let gateCleared = false;
        for (;;) {
          await this.runAgentStep(task, 'A4', { feedback: carriedFeedback });

          const reviewResults: AgentResult[] = [];
          for (const reviewer of REVIEW_AGENTS) {
            reviewResults.push(await this.runAgentStep(task, reviewer));
          }

          const scorecard = this.currentScorecard(task);
          task.scorecard = scorecard;

          const decision = decideGate({
            results: reviewResults,
            revision: task.revision_round,
            maxRevisions: getConfig().maxRevisions,
            qualityThreshold: getConfig().qualityThreshold,
            scorecard,
          });

          for (const record of decision.records) {
            task.gates.push({ ...record, round: task.revision_round, created_at: new Date().toISOString() });
          }

          this.publish(task, 'gate.decision', `门禁裁决：${decision.reason}`, {
            verdict: decision.verdict,
            scorecard,
            blocking: decision.blocking,
          }, decision.verdict === 'pass' ? 'info' : 'warn');
          this.save(task);

          if (decision.verdict === 'pass') {
            gateCleared = true;
            break;
          }

          if (decision.verdict === 'escalate') {
            const human = await this.waitForHuman(
              task,
              'escalation',
              `需要人工裁决：${decision.reason}`,
            );
            this.publish(task, 'approval.decided', `人工裁决：${human.decision}${human.comment ? `（${human.comment}）` : ''}`, {});

            if (human.decision === 'approve') {
              this.publish(task, 'log', '人工覆盖门禁：允许继续进入发布流程', {
                overridden: decision.blocking,
              }, 'warn');
              gateCleared = true;
              break;
            }
            if (human.decision === 'reject') {
              this.finishRejected(task, human.comment || '人工驳回');
              return;
            }
            carriedFeedback = this.mergeFeedback(human.comment, decision.requests);
            task.revision_round += 1;
            this.recordRevision(task, 'REVIEW', decision.reason, carriedFeedback);
            continue;
          }

          // revise
          carriedFeedback = decision.requests;
          task.revision_round += 1;
          this.recordRevision(task, 'REVIEW', decision.reason, carriedFeedback);
        }

        if (!gateCleared) throw new Error('审核门禁未通过且无法继续');

        // ---------- 效果预估 ----------
        await this.runAgentStep(task, 'A10');

        // ---------- 人工审批 ----------
        const approval = await this.waitForHuman(
          task,
          'approval',
          `内容已通过全部审核，等待人工审批（综合质量分 ${task.scorecard?.overall ?? '—'}）`,
        );
        this.publish(task, 'approval.decided', `人工审批：${approval.decision}${approval.comment ? `（${approval.comment}）` : ''}`, {});

        if (approval.decision === 'approve') {
          await this.publishDelivery(task, approval.comment);
          return;
        }

        if (approval.decision === 'reject') {
          this.finishRejected(task, approval.comment || '人工驳回');
          return;
        }

        // 审批退回 → 再走一轮创作
        approvalCycles += 1;
        if (approvalCycles > getConfig().maxRevisions) {
          this.finishRejected(task, `审批连续退回 ${approvalCycles} 次，任务终止`);
          return;
        }
        carriedFeedback = this.mergeFeedback(approval.comment, []);
        task.revision_round += 1;
        this.recordRevision(task, 'APPROVAL', `人工审批退回：${approval.comment}`, carriedFeedback);
      }
    } finally {
      this.running.delete(task.id);
    }
  }

  /* ---------------------------- A0 阶段 ----------------------------- */

  private async stageA0(task: TaskRecord): Promise<void> {
    const node = this.node(task, 'A0');
    node.status = 'running';
    node.started_at = new Date().toISOString();
    task.turn_used += 1;
    this.enterPhase(task, 'INIT');
    this.publish(task, 'agent.start', 'A0 总控：解析 Brief、拆解任务卡、编排流水线', { agent_id: 'A0' });

    const brief = task.brief;
    const objectiveBase = `${brief.brand}｜${brief.product}｜${brief.channel}｜${brief.objective}`;

    const stages: { agent: AgentId; phase: Phase; schema: string; objective: string }[] = [
      { agent: 'A1', phase: 'STRATEGY', schema: 'StrategyBrief', objective: `为「${objectiveBase}」输出受众洞察与核心信息屋` },
      { agent: 'A2', phase: 'CREATIVE', schema: 'CreativeConcept', objective: '基于策略给出 Big Idea 与 2-3 个创意方向' },
      { agent: 'A3', phase: 'PLANNING', schema: 'ContentPlan', objective: '拆解选题、内容大纲与渠道适配表' },
      { agent: 'A4', phase: 'DRAFTING', schema: 'CopyDraft', objective: `撰写 ${brief.channel} 多版本文案` },
      { agent: 'A5', phase: 'EDITING', schema: 'EditedCopy', objective: '审校结构、表达与品牌语气，输出质量评分' },
      { agent: 'A6', phase: 'FACT_CHECK', schema: 'FactCheckReport', objective: '核查数据、引用与来源，行使否决权' },
      { agent: 'A7', phase: 'COMPLIANCE', schema: 'ComplianceReport', objective: '检查广告法与品牌规范，行使否决权' },
      { agent: 'A10', phase: 'ANALYZED', schema: 'EffectReport', objective: '预估效果区间并给出优化建议' },
    ];

    const packets: TaskPacket[] = stages.map((item, index) => ({
      task_id: `${task.id}_p${index + 1}`,
      parent_id: task.id,
      phase: item.phase,
      assigned_agent: item.agent,
      objective: item.objective,
      context: {
        brand: brief.brand,
        audience: brief.audience,
        channel: brief.channel,
        tone: brief.tone,
        industry: brief.industry,
        keywords: brief.keywords,
        objective: brief.objective,
        product: brief.product,
      },
      constraints: brief.constraints,
      expected_output_schema: item.schema,
      priority: brief.priority,
      deadline: brief.deadline,
      created_at: new Date().toISOString(),
    }));

    task.packets = packets;

    const planContent: Record<string, unknown> = {
      brief_summary: objectiveBase,
      goal: brief.objective,
      audience: brief.audience,
      channel: brief.channel,
      stages: PIPELINE.filter((s) => s.agent !== 'A0').map((s) => ({
        agent: s.agent,
        phase: s.phase,
        label: PHASE_LABEL[s.phase],
        parallel_group: REVIEW_AGENTS.includes(s.agent) ? 'REVIEW' : 'SEQUENTIAL',
      })),
      gates: [
        { after: 'A5', rule: `质量分 ≥ ${getConfig().qualityThreshold} 方可通过` },
        { after: 'A6', rule: '高事实风险未修正则否决，禁止发布' },
        { after: 'A7', rule: '合规不通过则否决，禁止发布' },
        { after: 'A10', rule: '必须人工审批通过方可发布' },
      ],
      constraints: brief.constraints,
      deliverables: brief.deliverables,
      turn_budget: getConfig().turnBudget,
      max_revisions: getConfig().maxRevisions,
    };

    const artifact = this.writeArtifact(task, {
      agent_id: 'A0',
      type: 'task_plan',
      title: '任务卡与流程编排',
      content: planContent,
      text: packets.map((p) => `${p.assigned_agent} ${p.phase}｜${p.objective}`).join('\n'),
      tags: ['编排', '任务卡'],
    });

    node.status = 'done';
    node.finished_at = new Date().toISOString();
    node.summary = `拆解出 ${packets.length} 张任务卡，编排 ${PIPELINE.length - 1} 个阶段`;

    task.results.push({
      task_id: task.id,
      agent_id: 'A0',
      status: 'success',
      summary: node.summary,
      artifacts: [artifact],
      evidence: [],
      confidence: 0.95,
      risks: [],
      needs_human_review: false,
      gate_result: null,
      revision_requests: [],
      handoff: { to: 'A1', reason: '任务卡已下发，进入策略洞察' },
      metrics: {
        latency_ms: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
        provider: 'orchestrator',
        model: 'builtin',
        simulated: true,
      },
      created_at: new Date().toISOString(),
    });

    this.publish(task, 'agent.finish', node.summary, { agent_id: 'A0' });
    this.save(task);
  }

  /* ---------------------------- 单步执行 ---------------------------- */

  private async runAgentStep(
    task: TaskRecord,
    agentId: AgentId,
    options?: { feedback?: string[] },
  ): Promise<AgentResult> {
    const definition = getAgent(agentId);
    if (!definition) throw new Error(`智能体 ${agentId} 未实现`);

    const config = getConfig();
    if (task.turn_used >= config.turnBudget) throw new TurnBudgetExceeded(config.turnBudget);

    const node = this.node(task, agentId);
    task.turn_used += 1;
    node.status = 'running';
    node.started_at = new Date().toISOString();
    node.runs += 1;

    this.enterPhase(task, definition.meta.phase);
    const feedback = options?.feedback ?? [];
    this.publish(
      task,
      'agent.start',
      `${definition.meta.id} ${definition.meta.name}：${definition.meta.description}`,
      { agent_id: agentId, round: task.revision_round, feedback_count: feedback.length },
    );

    const upstream = this.upstreamFor(task, agentId);
    if (agentId === 'A4' && feedback.length) {
      blackboard.addActivity(task.id, 'A4', '收到返工意见', `revision:${task.revision_round}`);
    }
    blackboard.declareIntent(
      task.id,
      agentId,
      `${definition.meta.produces}@r${task.revision_round}`,
      180_000,
    );

    const ctx: AgentRunContext = {
      task_id: task.id,
      brief: task.brief,
      phase: definition.meta.phase,
      revision: task.revision_round,
      feedback,
      upstream,
      nextVersion: (type: ArtifactType) => blackboard.nextVersion(task.id, type),
      emit: (message, payload) => {
        this.publish(task, 'agent.progress', message, { ...payload, agent_id: agentId });
      },
    };

    let result: AgentResult;
    try {
      result = await this.withRetry(() => definition.run(ctx), agentId);
    } catch (error) {
      node.status = 'blocked';
      node.finished_at = new Date().toISOString();
      const message = error instanceof Error ? error.message : String(error);
      node.summary = `执行失败：${message}`;
      this.publish(task, 'agent.finish', `${agentId} 执行失败：${message}`, { agent_id: agentId }, 'error');
      this.save(task);
      throw error;
    }

    // 黑板写入
    for (const artifact of result.artifacts) {
      blackboard.putArtifact(artifact);
      task.artifacts.push(artifact);
      this.publish(task, 'blackboard.write', `产物写入黑板：${artifact.title} v${artifact.version}`, {
        artifact_id: artifact.id,
        artifact_type: artifact.type,
      });
    }
    this.indexFacts(task, result);
    blackboard.addActivity(task.id, agentId, result.summary, `${agentId}:r${task.revision_round}:${result.status}`);

    task.results.push(result);
    task.tokens.prompt += result.metrics.prompt_tokens;
    task.tokens.completion += result.metrics.completion_tokens;
    task.tokens.cost_usd += result.metrics.cost_usd;

    node.status = result.gate_result === 'pass' || !result.gate_result ? 'done' : 'revise';
    if (result.gate_result === 'reject') node.status = 'blocked';
    node.finished_at = new Date().toISOString();
    node.confidence = result.confidence;
    node.gate_result = result.gate_result;
    node.summary = result.summary;

    if (result.artifacts.length > 0) {
      const primary = result.artifacts[result.artifacts.length - 1]!;
      blackboard.addReview({
        taskId: task.id,
        agentId,
        targetArtifactId: primary.id,
        verdict: result.gate_result ?? 'pass',
        score: Math.round(result.confidence * 100),
        items: [],
      });
    }

    this.publish(
      task,
      'agent.finish',
      `${definition.meta.id} 完成：${result.summary}`,
      {
        agent_id: agentId,
        confidence: result.confidence,
        gate_result: result.gate_result,
        risks: result.risks,
        latency_ms: result.metrics.latency_ms,
        provider: result.metrics.provider,
        simulated: result.metrics.simulated,
        handoff: result.handoff,
      },
      result.needs_human_review ? 'warn' : 'info',
    );

    // A2A 交接消息（可观测性：Agent 之间怎么说话）
    if (result.handoff?.to) {
      const message: A2AMessage = {
        a2a_version: '1.0',
        message_id: newId('msg'),
        from_agent: agentId,
        to_agent: result.handoff.to,
        intent: result.gate_result && result.gate_result !== 'pass' ? 'request_revision' : 'handoff',
        payload: {
          task_id: task.id,
          artifact_type: result.artifacts[0]?.type ?? null,
          artifact_ref: result.artifacts[0] ? `blackboard://artifacts/${task.id}/${result.artifacts[0].type}_v${result.artifacts[0].version}` : null,
          handoff_reason: result.handoff.reason,
          priority: task.brief.priority,
        },
        trace_id: `trace_${task.id}`,
        timestamp: new Date().toISOString(),
      };
      this.publish(task, 'log', `${agentId} → ${result.handoff.to}：${result.handoff.reason}`, {
        level: 'debug',
        a2a: message,
      });
    }

    this.save(task);
    return result;
  }

  /** 单次重试：真实模型偶发返回非 JSON 时，再给一次机会（plan.md D10 容错）。 */
  private async withRetry<T>(fn: () => Promise<T>, agentId: AgentId): Promise<T> {
    try {
      return await fn();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      log.warn(`${agentId} 首次执行失败，重试一次：${message}`);
      return await fn();
    }
  }

  /* ---------------------------- 上下文装配 -------------------------- */

  private upstreamFor(task: TaskRecord, agentId: AgentId): Record<string, Record<string, unknown>> {
    const get = (type: ArtifactType): Record<string, unknown> =>
      (blackboard.latestArtifact(task.id, type)?.content as Record<string, unknown> | undefined) ?? {};

    switch (agentId) {
      case 'A1':
        return {};
      case 'A2':
        return { strategy: get('strategy_brief') };
      case 'A3':
        return { strategy: get('strategy_brief'), creative: get('creative_concept') };
      case 'A4':
        return {
          strategy: get('strategy_brief'),
          creative: get('creative_concept'),
          plan: get('content_plan'),
        };
      case 'A5':
        return { plan: get('content_plan'), draft: this.effectiveDraft(task, false) };
      case 'A6':
      case 'A7':
        return { draft: this.effectiveDraft(task, true) };
      case 'A10':
        return {
          strategy: get('strategy_brief'),
          plan: get('content_plan'),
          draft: this.effectiveDraft(task, true),
        };
      default:
        return {};
    }
  }

  /**
   * 装配「当前有效文案」：
   * A5 的修订稿会覆盖 A4 草稿中被选中的那个版本，
   * 保证 A6/A7/A10 审查的是真正要发布的文本。
   */
  private effectiveDraft(task: TaskRecord, includeEditor: boolean): Record<string, unknown> {
    const copy = blackboard.latestArtifact(task.id, 'copy_draft');
    if (!copy) return {};
    const content = structuredClone(copy.content) as Record<string, unknown>;

    if (!includeEditor) return content;

    const edit = blackboard.latestArtifact(task.id, 'edited_copy');
    if (!edit || edit.revision !== copy.revision) return content;

    const revised = (edit.content as Record<string, unknown>).revised as Record<string, unknown> | undefined;
    if (!revised || !revised.body) return content;

    const targetId = String((edit.content as Record<string, unknown>).target_version ?? content.recommended_version ?? 'V1');
    const versions = Array.isArray(content.versions) ? (content.versions as Record<string, unknown>[]) : [];
    const index = versions.findIndex((v) => String(v.id) === targetId);
    const merged: Record<string, unknown> = {
      ...(index >= 0 ? versions[index] : {}),
      id: targetId,
      title: revised.title,
      body: revised.body,
      cta: revised.cta,
      hashtags: revised.hashtags,
    };
    if (index >= 0) versions[index] = merged;
    else versions.push(merged);

    content.versions = versions;
    content.recommended_version = targetId;
    return content;
  }

  private currentScorecard(task: TaskRecord): QualityScorecard {
    const pick = (type: ArtifactType) =>
      (blackboard.latestArtifact(task.id, type)?.content as Record<string, unknown> | undefined) ?? null;
    return buildScorecard({
      editor: pick('edited_copy'),
      factCheck: pick('fact_check_report'),
      compliance: pick('compliance_report'),
    });
  }

  private indexFacts(task: TaskRecord, result: AgentResult): void {
    if (result.agent_id === 'A1') {
      const house = (result.artifacts[0]?.content as Record<string, unknown> | undefined)?.message_house;
      const points = (house as Record<string, unknown> | undefined)?.support_points;
      if (Array.isArray(points)) {
        for (const point of points.slice(0, 4)) {
          blackboard.addFact({
            taskId: task.id,
            agentId: 'A1',
            claim: String(point),
            source: 'A1 策略推断（待验证）',
            status: 'candidate',
            confidence: 0.6,
          });
        }
      }
    }
    if (result.agent_id === 'A6') {
      const checks = (result.artifacts[0]?.content as Record<string, unknown> | undefined)?.checks;
      if (Array.isArray(checks)) {
        for (const check of checks as Record<string, unknown>[]) {
          const status = String(check.status);
          blackboard.addFact({
            taskId: task.id,
            agentId: 'A6',
            claim: String(check.claim),
            source: String(check.source ?? '未提供'),
            status: status === 'verified' ? 'verified' : status === 'contradicted' ? 'rejected' : 'candidate',
            confidence: Number(check.confidence) || 0.6,
          });
        }
      }
    }
  }

  /* ---------------------------- 最终交付 ---------------------------- */

  private async publishDelivery(task: TaskRecord, comment: string): Promise<void> {
    const draft = this.effectiveDraft(task, true);
    const versions = Array.isArray(draft.versions) ? (draft.versions as Record<string, unknown>[]) : [];
    const target =
      versions.find((v) => String(v.id) === String(draft.recommended_version)) ?? versions[0] ?? {};

    const content: Record<string, unknown> = {
      channel: task.brief.channel,
      brand: task.brief.brand,
      product: task.brief.product,
      title: target.title ?? '',
      body: target.body ?? '',
      cta: target.cta ?? '',
      hashtags: target.hashtags ?? [],
      scorecard: task.scorecard,
      review_summary: {
        editor: this.summaryOf(task, 'A5'),
        fact_check: this.summaryOf(task, 'A6'),
        compliance: this.summaryOf(task, 'A7'),
      },
      revision_rounds: task.revision_round,
      approved_by: 'human',
      approval_comment: comment,
    };

    const artifact = this.writeArtifact(task, {
      agent_id: 'A0',
      type: 'final_delivery',
      title: '最终交付物',
      content,
      text: `${target.title ?? ''}\n\n${target.body ?? ''}\n\n${Array.isArray(target.hashtags) ? (target.hashtags as string[]).join(' ') : ''}`,
      tags: ['交付', '已审批'],
    });

    task.results.push({
      task_id: task.id,
      agent_id: 'A0',
      status: 'success',
      summary: '人工审批通过，生成最终交付物并归档',
      artifacts: [artifact],
      evidence: [],
      confidence: 0.95,
      risks: [],
      needs_human_review: false,
      gate_result: null,
      revision_requests: [],
      handoff: null,
      metrics: {
        latency_ms: 0,
        prompt_tokens: 0,
        completion_tokens: 0,
        cost_usd: 0,
        provider: 'orchestrator',
        model: 'builtin',
        simulated: true,
      },
      created_at: new Date().toISOString(),
    });

    task.approval = {
      required: true,
      decision: 'approved',
      comment,
      decided_at: new Date().toISOString(),
    };

    task.status = 'completed';
    task.phase = 'ARCHIVED';
    task.finished_at = new Date().toISOString();
    task.updated_at = task.finished_at;

    const node = this.node(task, 'A10');
    if (node) node.status = 'done';

    this.publish(task, 'task.status', '任务已完成并归档', { status: 'completed' });
    this.publish(task, 'task.completed', `任务完成：${task.brief.brand} / ${task.brief.channel}，共 ${task.revision_round} 轮返工`, {
      scorecard: task.scorecard,
      tokens: task.tokens,
      turns: task.turn_used,
    });
    this.save(task, true);
    await blackboard.flush();
    log.info(`任务 ${task.id} 完成，综合质量分 ${task.scorecard?.overall ?? '—'}`);
  }

  private summaryOf(task: TaskRecord, agentId: AgentId): string {
    const results = task.results.filter((r) => r.agent_id === agentId);
    return results[results.length - 1]?.summary ?? '';
  }

  private finishRejected(task: TaskRecord, reason: string): void {
    task.status = 'rejected';
    task.phase = 'REJECTED';
    task.finished_at = new Date().toISOString();
    task.error = reason;
    task.approval = {
      required: true,
      decision: 'rejected',
      comment: reason,
      decided_at: task.finished_at,
    };
    this.publish(task, 'task.status', `任务被驳回：${reason}`, { status: 'rejected' }, 'warn');
    this.save(task, true);
  }

  private handleFatal(task: TaskRecord, error: unknown): void {
    const message = error instanceof Error ? error.message : String(error);
    task.status = 'failed';
    task.phase = 'FAILED';
    task.error = message;
    task.finished_at = new Date().toISOString();
    this.publish(task, 'task.failed', `任务执行失败：${message}`, { error: message }, 'error');
    log.error(`任务 ${task.id} 失败`, error);
    this.save(task, true);
  }

  /* ---------------------------- 人工介入 ---------------------------- */

  private async waitForHuman(
    task: TaskRecord,
    milestone: 'escalation' | 'approval',
    message: string,
  ): Promise<HumanDecision> {
    if (this.autoApproveAll.has(task.id)) {
      this.publish(task, 'approval.required', `${message}（自动审批已开启，直接通过）`, { milestone, auto: true });
      return { decision: 'approve', comment: '自动审批模式' };
    }

    task.status = 'awaiting_approval';
    task.approval.required = true;
    task.approval.decision = 'pending';
    this.save(task, true);
    this.publish(task, 'approval.required', message, { milestone });

    return new Promise<HumanDecision>((resolve) => {
      this.waiters.set(task.id, resolve);
    });
  }

  decide(taskId: string, decision: HumanDecision['decision'], comment = ''): TaskRecord {
    const task = taskStore.get(taskId);
    if (!task) throw new Error(`任务不存在：${taskId}`);
    const resolver = this.waiters.get(taskId);
    if (!resolver) throw new Error('当前任务不处于等待人工裁决状态');
    this.waiters.delete(taskId);
    if (task.status === 'awaiting_approval') task.status = 'running';
    this.update(task);
    resolver({ decision, comment });
    return task;
  }

  /* ---------------------------- 工具方法 ---------------------------- */

  private recordRevision(task: TaskRecord, phase: Phase, reason: string, requests: string[]): void {
    task.revisions.push({
      round: task.revision_round,
      phase,
      reason,
      requests,
      created_at: new Date().toISOString(),
    });

    for (const node of task.pipeline) {
      if (node.phase === 'DRAFTING' || REVIEW_AGENTS.includes(node.agent_id)) {
        node.status = 'pending';
        node.gate_result = null;
      }
    }

    this.publish(task, 'revision.requested', `第 ${task.revision_round} 轮返工：${reason}`, {
      round: task.revision_round,
      requests: requests.slice(0, 6),
    }, 'warn');
    this.save(task, true);
  }

  private mergeFeedback(comment: string, requests: string[]): string[] {
    const merged = [...requests];
    if (comment.trim()) merged.unshift(`[人工意见] ${comment.trim()}`);
    return merged;
  }

  private enterPhase(task: TaskRecord, phase: Phase): void {
    if (task.phase === phase) return;
    task.phase = phase;
    this.publish(task, 'phase.enter', `进入阶段：${PHASE_LABEL[phase]}`, { phase });
    this.save(task);
  }

  private node(task: TaskRecord, agentId: AgentId): PipelineNode {
    const node = task.pipeline.find((n) => n.agent_id === agentId);
    if (!node) throw new Error(`流水线缺少节点 ${agentId}`);
    return node;
  }

  private writeArtifact(
    task: TaskRecord,
    input: {
      agent_id: AgentId;
      type: ArtifactType;
      title: string;
      content: Record<string, unknown>;
      text: string;
      tags: string[];
    },
  ): Artifact {
    const artifact: Artifact = {
      id: newId('art'),
      task_id: task.id,
      agent_id: input.agent_id,
      type: input.type,
      version: blackboard.nextVersion(task.id, input.type),
      title: input.title,
      content: input.content,
      text: input.text,
      revision: task.revision_round,
      created_at: new Date().toISOString(),
      tags: input.tags,
    };
    blackboard.putArtifact(artifact);
    task.artifacts.push(artifact);
    this.publish(task, 'blackboard.write', `产物写入黑板：${artifact.title} v${artifact.version}`, {
      artifact_id: artifact.id,
      artifact_type: artifact.type,
    });
    return artifact;
  }

  private update(task: TaskRecord): void {
    task.updated_at = new Date().toISOString();
    this.save(task);
  }

  private save(task: TaskRecord, immediate = false): void {
    task.updated_at = new Date().toISOString();
    taskStore.scheduleSave(task, immediate);
  }

  private publish(
    task: TaskRecord,
    type: Parameters<typeof eventBus.publish>[0]['type'],
    message: string,
    payload: Record<string, unknown>,
    level: 'debug' | 'info' | 'warn' | 'error' = 'info',
  ): void {
    eventBus.publish({
      task_id: task.id,
      type,
      message,
      level,
      agent_id: (payload.agent_id as AgentId | undefined) ?? null,
      phase: task.phase,
      payload,
    });
  }
}

export const orchestrator = new Orchestrator();
