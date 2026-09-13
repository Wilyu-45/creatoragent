import { mkdir, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { BLACKBOARD_FILE } from '../config.ts';
import { newId } from './events.ts';
import type {
  ActivityEntry,
  AgentId,
  Artifact,
  BlackboardSnapshot,
  FactEntry,
  FactStatus,
  IntentEntry,
  ReviewEntry,
  ReviewItem,
  GateResult,
} from './types.ts';

interface BlackboardState {
  facts: FactEntry[];
  intents: IntentEntry[];
  activities: ActivityEntry[];
  reviews: ReviewEntry[];
  artifacts: Artifact[];
}

/**
 * 共享黑板（Blackboard）
 *
 * 参照《plan.md》2.2.2 的「五类共享状态」设计，用单进程内存 + JSON 落盘实现：
 *   Fact     事实   —— 候选/已验证结论，防止把猜想当事实
 *   Intent   意图   —— 带租约的工作声明，防止重复劳动
 *   Artifact 产物   —— 文案、报告等交付物，带版本
 *   Activity 活动   —— 已完成动作的去重签名，防止重复执行
 *   Review   审核   —— 审核记录与整改指令
 *
 * 生产环境可替换为 PostgreSQL + Redis，接口保持不变。
 */
export class Blackboard {
  private state: BlackboardState = {
    facts: [],
    intents: [],
    activities: [],
    reviews: [],
    artifacts: [],
  };

  private dirty = false;
  private flushTimer: NodeJS.Timeout | null = null;

  /* --------------------------- 持久化 --------------------------- */

  async load(): Promise<void> {
    try {
      const raw = await readFile(BLACKBOARD_FILE, 'utf8');
      const parsed = JSON.parse(raw) as Partial<BlackboardState>;
      this.state = {
        facts: parsed.facts ?? [],
        intents: parsed.intents ?? [],
        activities: parsed.activities ?? [],
        reviews: parsed.reviews ?? [],
        artifacts: parsed.artifacts ?? [],
      };
    } catch {
      /* 首次运行无文件，忽略 */
    }
    this.sweepExpiredIntents();
  }

  private markDirty(): void {
    this.dirty = true;
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      void this.flush();
    }, 300);
  }

  async flush(): Promise<void> {
    if (!this.dirty) return;
    this.dirty = false;
    await mkdir(path.dirname(BLACKBOARD_FILE), { recursive: true });
    await writeFile(BLACKBOARD_FILE, JSON.stringify(this.state, null, 2), 'utf8');
  }

  /* ----------------------------- 意图 ---------------------------- */

  /** 声明工作方向并获取租约；若方向已被他人持有则返回 null。 */
  declareIntent(taskId: string, agentId: AgentId, direction: string, ttlMs = 120_000): IntentEntry | null {
    this.sweepExpiredIntents();
    const key = `${taskId}::${direction}`;
    const existing = this.state.intents.find((i) => i.key === key);
    if (existing) return null;
    const entry: IntentEntry = {
      key,
      task_id: taskId,
      agent_id: agentId,
      direction,
      expires_at: new Date(Date.now() + ttlMs).toISOString(),
      created_at: new Date().toISOString(),
    };
    this.state.intents.push(entry);
    this.markDirty();
    return entry;
  }

  releaseIntent(key: string): void {
    const idx = this.state.intents.findIndex((i) => i.key === key);
    if (idx >= 0) {
      this.state.intents.splice(idx, 1);
      this.markDirty();
    }
  }

  activeIntents(taskId?: string): IntentEntry[] {
    this.sweepExpiredIntents();
    return taskId ? this.state.intents.filter((i) => i.task_id === taskId) : [...this.state.intents];
  }

  private sweepExpiredIntents(): void {
    const now = Date.now();
    const before = this.state.intents.length;
    this.state.intents = this.state.intents.filter((i) => Date.parse(i.expires_at) > now);
    if (this.state.intents.length !== before) this.markDirty();
  }

  /* ----------------------------- 事实 ---------------------------- */

  addFact(input: {
    taskId: string;
    agentId: AgentId;
    claim: string;
    source: string;
    status?: FactStatus;
    confidence?: number;
  }): FactEntry {
    const normalized = input.claim.trim();
    const existing = this.state.facts.find(
      (f) => f.task_id === input.taskId && f.claim.trim() === normalized,
    );
    if (existing) {
      // 同一事实被更高可信来源再次确认 → 升级状态
      if (input.status === 'verified' && existing.status !== 'verified') {
        existing.status = 'verified';
        existing.source = input.source;
        existing.confidence = Math.max(existing.confidence, input.confidence ?? 0.8);
        this.markDirty();
      }
      return existing;
    }
    const entry: FactEntry = {
      id: newId('fact'),
      task_id: input.taskId,
      agent_id: input.agentId,
      claim: normalized,
      source: input.source,
      status: input.status ?? 'candidate',
      confidence: input.confidence ?? 0.6,
      created_at: new Date().toISOString(),
    };
    this.state.facts.push(entry);
    this.markDirty();
    return entry;
  }

  facts(taskId: string): FactEntry[] {
    return this.state.facts.filter((f) => f.task_id === taskId);
  }

  /* ----------------------------- 活动 ---------------------------- */

  /** 若该签名活动已存在则返回 true（表示重复，应跳过）。 */
  hasActivity(taskId: string, signature: string): boolean {
    return this.state.activities.some((a) => a.task_id === taskId && a.signature === signature);
  }

  addActivity(taskId: string, agentId: AgentId, action: string, signature: string): ActivityEntry {
    const entry: ActivityEntry = {
      id: newId('act'),
      task_id: taskId,
      agent_id: agentId,
      action,
      signature,
      created_at: new Date().toISOString(),
    };
    this.state.activities.push(entry);
    this.markDirty();
    return entry;
  }

  activities(taskId: string): ActivityEntry[] {
    return this.state.activities.filter((a) => a.task_id === taskId);
  }

  /* ----------------------------- 产物 ---------------------------- */

  putArtifact(artifact: Artifact): Artifact {
    this.state.artifacts.push(artifact);
    this.markDirty();
    return artifact;
  }

  artifacts(taskId: string): Artifact[] {
    return this.state.artifacts
      .filter((a) => a.task_id === taskId)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
  }

  latestArtifact(taskId: string, type: Artifact['type']): Artifact | undefined {
    const list = this.artifacts(taskId).filter((a) => a.type === type);
    return list[list.length - 1];
  }

  nextVersion(taskId: string, type: Artifact['type']): number {
    return this.artifacts(taskId).filter((a) => a.type === type).length + 1;
  }

  /* ----------------------------- 审核 ---------------------------- */

  addReview(input: {
    taskId: string;
    agentId: AgentId;
    targetArtifactId: string;
    verdict: GateResult;
    score: number;
    items: ReviewItem[];
  }): ReviewEntry {
    const entry: ReviewEntry = {
      id: newId('rev'),
      task_id: input.taskId,
      agent_id: input.agentId,
      target_artifact_id: input.targetArtifactId,
      verdict: input.verdict,
      score: input.score,
      items: input.items,
      created_at: new Date().toISOString(),
    };
    this.state.reviews.push(entry);
    this.markDirty();
    return entry;
  }

  reviews(taskId: string): ReviewEntry[] {
    return this.state.reviews.filter((r) => r.task_id === taskId);
  }

  /* ----------------------------- 快照 ---------------------------- */

  snapshot(taskId: string): BlackboardSnapshot {
    const facts = this.facts(taskId);
    return {
      facts,
      intents: this.activeIntents(taskId),
      activities: this.activities(taskId),
      reviews: this.reviews(taskId),
      stats: {
        fact_count: facts.length,
        verified_fact_count: facts.filter((f) => f.status === 'verified').length,
        artifact_count: this.artifacts(taskId).length,
        review_count: this.reviews(taskId).length,
        active_intents: this.activeIntents(taskId).length,
      },
    };
  }
}

export const blackboard = new Blackboard();
