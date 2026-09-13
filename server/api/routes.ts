import { Router } from 'express';
import type { Request, Response } from 'express';
import { allAgentMeta, PLANNED_AGENTS } from '../agents/registry.ts';
import { getConfig, publicConfig, updateConfig } from '../config.ts';
import type { LLMSettings } from '../config.ts';
import { blackboard } from '../core/blackboard.ts';
import { eventBus } from '../core/events.ts';
import { orchestrator } from '../core/orchestrator.ts';
import { taskStore } from '../core/store.ts';
import type { AgentEvent, Brief, Priority, TaskRecord } from '../core/types.ts';
import { createEmptyBrief, PHASE_LABEL, PHASE_ORDER } from '../core/types.ts';
import { INDUSTRY_RULES, LEXICON_GROUPS } from '../knowledge/compliance.ts';
import { CHANNEL_RULES, INDUSTRY_PROFILES } from '../knowledge/industry.ts';
import { resolveProvider } from '../llm/index.ts';
import { createLogger } from '../logger.ts';

const log = createLogger('api');

function asStringArray(value: unknown): string[] {
  if (Array.isArray(value)) return value.map((v) => String(v)).filter(Boolean);
  if (typeof value === 'string') {
    return value
      .split(/[\n,，、;；]/)
      .map((s) => s.trim())
      .filter(Boolean);
  }
  return [];
}

const PRIORITIES: Priority[] = ['low', 'normal', 'high', 'urgent'];

function parseBrief(input: unknown): Brief {
  const base = createEmptyBrief();
  if (typeof input !== 'object' || input === null) return base;
  const raw = input as Record<string, unknown>;
  const pick = (key: keyof Brief, fallback: string): string =>
    typeof raw[key] === 'string' && (raw[key] as string).trim() ? (raw[key] as string).trim() : fallback;

  return {
    brand: pick('brand', '未命名品牌'),
    product: pick('product', '未命名产品'),
    objective: pick('objective', base.objective),
    audience: pick('audience', '目标人群'),
    channel: pick('channel', base.channel),
    tone: pick('tone', base.tone),
    industry: pick('industry', base.industry),
    keywords: asStringArray(raw.keywords),
    constraints: asStringArray(raw.constraints),
    deliverables: asStringArray(raw.deliverables),
    notes: typeof raw.notes === 'string' ? raw.notes : '',
    priority: PRIORITIES.includes(raw.priority as Priority) ? (raw.priority as Priority) : 'normal',
    deadline: typeof raw.deadline === 'string' && raw.deadline ? raw.deadline : null,
  };
}

function taskSummary(task: TaskRecord) {
  return {
    id: task.id,
    brief: task.brief,
    status: task.status,
    phase: task.phase,
    phase_label: PHASE_LABEL[task.phase],
    revision_round: task.revision_round,
    turn_used: task.turn_used,
    scorecard: task.scorecard,
    created_at: task.created_at,
    updated_at: task.updated_at,
    finished_at: task.finished_at,
    tokens: task.tokens,
    artifact_count: task.artifacts.length,
    approval: task.approval,
    error: task.error,
  };
}

export function createApiRouter(): Router {
  const router = Router();

  /* ------------------------------ 基础信息 ------------------------------ */

  router.get('/health', (_req: Request, res: Response) => {
    const provider = resolveProvider();
    res.json({
      ok: true,
      time: new Date().toISOString(),
      config: publicConfig(),
      provider: { name: provider.name, model: provider.model, simulated: provider.simulated },
    });
  });

  router.get('/agents', (_req: Request, res: Response) => {
    res.json({
      implemented: allAgentMeta(),
      planned: PLANNED_AGENTS,
      pipeline: PHASE_ORDER.map((phase) => ({ phase, label: PHASE_LABEL[phase] })),
    });
  });

  router.get('/knowledge', (_req: Request, res: Response) => {
    res.json({
      lexicon: LEXICON_GROUPS.map((group) => ({
        category: group.category,
        severity: group.severity,
        law: group.law,
        term_count: group.terms.length,
        terms: group.terms.slice(0, 12),
      })),
      industry_rules: Object.entries(INDUSTRY_RULES).map(([industry, rules]) => ({
        industry,
        rule_count: rules.length,
        categories: rules.map((r) => r.category),
      })),
      channels: Object.entries(CHANNEL_RULES).map(([channel, rule]) => ({
        channel,
        format: rule.format,
        length_hint: rule.length_hint,
        blocks: rule.blocks,
      })),
      industries: Object.keys(INDUSTRY_PROFILES),
    });
  });

  router.get('/settings', (_req: Request, res: Response) => {
    res.json(publicConfig());
  });

  router.put('/settings', (req: Request, res: Response) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const llmPatch: Partial<LLMSettings> = {};
    if (typeof body.provider === 'string' && ['mock', 'openai'].includes(body.provider)) {
      llmPatch.provider = body.provider as LLMSettings['provider'];
    }
    for (const key of ['baseUrl', 'model', 'apiKey'] as const) {
      if (typeof body[key] === 'string') llmPatch[key] = body[key];
    }
    for (const key of ['temperature', 'maxTokens', 'timeoutMs'] as const) {
      if (typeof body[key] === 'number' && Number.isFinite(body[key])) llmPatch[key] = body[key];
    }

    updateConfig({
      turnBudget: typeof body.turnBudget === 'number' ? body.turnBudget : undefined,
      maxRevisions: typeof body.maxRevisions === 'number' ? body.maxRevisions : undefined,
      qualityThreshold: typeof body.qualityThreshold === 'number' ? body.qualityThreshold : undefined,
      autoApprove: typeof body.autoApprove === 'boolean' ? body.autoApprove : undefined,
      llm: llmPatch,
    });

    log.info('运行时配置已更新', { provider: getConfig().llm.provider, model: getConfig().llm.model });
    res.json(publicConfig());
  });

  /* ------------------------------- 任务 -------------------------------- */

  router.get('/tasks', (_req: Request, res: Response) => {
    res.json({ tasks: taskStore.list().map(taskSummary) });
  });

  router.post('/tasks', (req: Request, res: Response) => {
    const body = (req.body ?? {}) as Record<string, unknown>;
    const brief = parseBrief(body.brief ?? body);
    if (!brief.brand || brief.brand === '未命名品牌') {
      // 允许，但在界面上会以「未命名品牌」展示
      log.warn('创建任务时未提供品牌名');
    }
    const autoApprove =
      typeof body.autoApprove === 'boolean' ? body.autoApprove : undefined;
    const task = orchestrator.createTask(brief, { autoApprove });
    res.status(201).json({ task });
  });

  router.get('/tasks/:id', (req: Request, res: Response) => {
    const task = taskStore.get(String(req.params.id));
    if (!task) {
      res.status(404).json({ error: '任务不存在' });
      return;
    }
    res.json({
      task,
      events: eventBus.history(task.id),
      blackboard: blackboard.snapshot(task.id),
    });
  });

  router.delete('/tasks/:id', (req: Request, res: Response) => {
    const id = String(req.params.id);
    const task = taskStore.get(id);
    if (!task) {
      res.status(404).json({ error: '任务不存在' });
      return;
    }
    if (task.status === 'running' || task.status === 'awaiting_approval') {
      res.status(409).json({ error: '任务仍在执行中，无法删除' });
      return;
    }
    res.json({ ok: true, id });
  });

  router.post('/tasks/:id/decide', (req: Request, res: Response) => {
    const id = String(req.params.id);
    const body = (req.body ?? {}) as Record<string, unknown>;
    const decision = String(body.decision ?? '');
    if (!['approve', 'revise', 'reject'].includes(decision)) {
      res.status(400).json({ error: 'decision 必须是 approve / revise / reject' });
      return;
    }
    try {
      const task = orchestrator.decide(id, decision as 'approve' | 'revise' | 'reject', String(body.comment ?? ''));
      res.json({ task: taskSummary(task) });
    } catch (error) {
      res.status(409).json({ error: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get('/tasks/:id/blackboard', (req: Request, res: Response) => {
    res.json(blackboard.snapshot(String(req.params.id)));
  });

  /* ---------------------------- SSE 实时事件 ---------------------------- */

  router.get('/tasks/:id/events', (req: Request, res: Response) => {
    const taskId = String(req.params.id);
    const since = Number(req.query.since ?? 0) || 0;

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache, no-transform',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });
    res.write('retry: 3000\n\n');

    const send = (event: AgentEvent): void => {
      res.write(`id: ${event.seq}\n`);
      res.write(`event: message\n`);
      res.write(`data: ${JSON.stringify(event)}\n\n`);
    };

    for (const event of eventBus.replay(taskId, since)) send(event);

    const unsubscribe = eventBus.subscribe(taskId, send);
    const ping = setInterval(() => res.write(': ping\n\n'), 15_000);

    req.on('close', () => {
      clearInterval(ping);
      unsubscribe();
      res.end();
    });
  });

  /* ------------------------------- 指标 -------------------------------- */

  router.get('/metrics', (_req: Request, res: Response) => {
    const tasks = taskStore.list();
    const completed = tasks.filter((t) => t.status === 'completed');

    const avg = (values: number[]): number =>
      values.length ? Math.round((values.reduce((a, b) => a + b, 0) / values.length) * 10) / 10 : 0;

    const gates = tasks.flatMap((t) => t.gates);
    const passGates = gates.filter((g) => g.verdict === 'pass');

    const facts = tasks.flatMap((t) => blackboard.facts(t.id));
    const verifiedFacts = facts.filter((f) => f.status === 'verified');

    const brandScores = tasks
      .map((t) => t.artifacts.find((a) => a.type === 'compliance_report'))
      .filter((a): a is NonNullable<typeof a> => Boolean(a))
      .map((a) => Number((a.content as Record<string, unknown>).brand_consistency && ((a.content as Record<string, unknown>).brand_consistency as Record<string, unknown>).score))
      .filter((n) => Number.isFinite(n) && n > 0);

    const latencies = tasks.flatMap((t) => t.results.map((r) => r.metrics.latency_ms)).filter((n) => n > 0);
    latencies.sort((a, b) => a - b);
    const p99 = latencies.length ? latencies[Math.min(latencies.length - 1, Math.floor(latencies.length * 0.99))] : 0;

    const tokens = tasks.reduce(
      (acc, t) => ({
        prompt: acc.prompt + t.tokens.prompt,
        completion: acc.completion + t.tokens.completion,
        cost_usd: acc.cost_usd + t.tokens.cost_usd,
      }),
      { prompt: 0, completion: 0, cost_usd: 0 },
    );

    const simulated = tasks.flatMap((t) => t.results).filter((r) => r.metrics.simulated).length;
    const totalResults = tasks.flatMap((t) => t.results).length;

    res.json({
      system: {
        total_tasks: tasks.length,
        completed: completed.length,
        rejected: tasks.filter((t) => t.status === 'rejected').length,
        failed: tasks.filter((t) => t.status === 'failed').length,
        running: tasks.filter((t) => t.status === 'running').length,
        awaiting_approval: tasks.filter((t) => t.status === 'awaiting_approval').length,
        first_pass_rate: tasks.length
          ? Math.round((tasks.filter((t) => t.revision_round === 0).length / tasks.length) * 1000) / 10
          : 0,
        avg_revision_rounds: avg(tasks.map((t) => t.revision_round)),
        avg_overall_score: avg(tasks.filter((t) => t.scorecard).map((t) => t.scorecard!.overall)),
        gate_pass_rate: gates.length ? Math.round((passGates.length / gates.length) * 1000) / 10 : 0,
        fact_accuracy: facts.length ? Math.round((verifiedFacts.length / facts.length) * 1000) / 10 : 0,
        brand_consistency: avg(brandScores),
        turn_budget_hit_rate: tasks.length
          ? Math.round((tasks.filter((t) => t.turn_used >= getConfig().turnBudget).length / tasks.length) * 1000) / 10
          : 0,
        p99_agent_latency_ms: p99,
        tokens,
        simulated_ratio: totalResults ? Math.round((simulated / totalResults) * 1000) / 10 : 0,
      },
      providers: [
        {
          name: resolveProvider().name,
          model: resolveProvider().model,
          simulated: resolveProvider().simulated,
        },
      ],
      agents: allAgentMeta().map((meta) => {
        const results = tasks.flatMap((t) => t.results).filter((r) => r.agent_id === meta.id);
        return {
          id: meta.id,
          name: meta.name,
          runs: results.length,
          avg_confidence: avg(results.map((r) => r.confidence * 100)),
          avg_latency_ms: avg(results.map((r) => r.metrics.latency_ms)),
          gate_failures: results.filter((r) => r.gate_result && r.gate_result !== 'pass').length,
          veto_used: results.filter((r) => r.needs_human_review).length,
        };
      }),
    });
  });

  return router;
}
