import { randomUUID } from 'node:crypto';
import type { AgentEvent, AgentEventType, AgentId, Phase } from './types.ts';

type Listener = (event: AgentEvent) => void;

const REPLAY_LIMIT = 400;

/**
 * 任务级事件总线。所有智能体的动作都会变成事件：
 * - 写入环形缓冲，供 SSE 客户端「断线重连 + 回放」
 * - 广播给所有订阅者（Web 界面实时进度）
 */
export class EventBus {
  private seq = 0;
  private readonly buffers = new Map<string, AgentEvent[]>();
  private readonly listeners = new Map<string, Set<Listener>>();

  publish(input: {
    task_id: string;
    type: AgentEventType;
    message: string;
    agent_id?: AgentId | null;
    phase?: Phase | null;
    level?: AgentEvent['level'];
    payload?: Record<string, unknown>;
  }): AgentEvent {
    const event: AgentEvent = {
      seq: ++this.seq,
      task_id: input.task_id,
      ts: new Date().toISOString(),
      type: input.type,
      agent_id: input.agent_id ?? null,
      phase: input.phase ?? null,
      level: input.level ?? 'info',
      message: input.message,
      payload: input.payload,
    };
    this.append(event);
    return event;
  }

  private append(event: AgentEvent): void {
    let buf = this.buffers.get(event.task_id);
    if (!buf) {
      buf = [];
      this.buffers.set(event.task_id, buf);
    }
    buf.push(event);
    if (buf.length > REPLAY_LIMIT) buf.splice(0, buf.length - REPLAY_LIMIT);

    const set = this.listeners.get(event.task_id);
    if (set) {
      for (const listener of [...set]) {
        try {
          listener(event);
        } catch {
          /* 单个订阅者异常不影响其他订阅者 */
        }
      }
    }
  }

  /** 返回 sinceSeq 之后的事件（用于 SSE 回放）。 */
  replay(taskId: string, sinceSeq = 0): AgentEvent[] {
    const buf = this.buffers.get(taskId) ?? [];
    return buf.filter((e) => e.seq > sinceSeq);
  }

  /** 返回全量事件（用于任务详情页一次性渲染时间线）。 */
  history(taskId: string): AgentEvent[] {
    return [...(this.buffers.get(taskId) ?? [])];
  }

  subscribe(taskId: string, listener: Listener): () => void {
    let set = this.listeners.get(taskId);
    if (!set) {
      set = new Set();
      this.listeners.set(taskId, set);
    }
    set.add(listener);
    return () => {
      set?.delete(listener);
      if (set && set.size === 0) this.listeners.delete(taskId);
    };
  }

  drop(taskId: string): void {
    this.buffers.delete(taskId);
    this.listeners.delete(taskId);
  }
}

export const eventBus = new EventBus();

export function newId(prefix: string): string {
  return `${prefix}_${randomUUID().slice(0, 8)}`;
}
