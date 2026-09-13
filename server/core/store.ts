import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { TASK_DIR } from '../config.ts';
import { createLogger } from '../logger.ts';
import type { TaskRecord } from './types.ts';

const log = createLogger('store');

/**
 * 任务持久化：每个任务一个 JSON 文件，内存保留索引。
 * MVP 用文件存储代替 A11 记忆知识库（见 plan.md 4.2）。
 */
export class TaskStore {
  private tasks = new Map<string, TaskRecord>();
  private flushTimers = new Map<string, NodeJS.Timeout>();

  async loadAll(): Promise<void> {
    await mkdir(TASK_DIR, { recursive: true });
    const files = await readdir(TASK_DIR).catch(() => [] as string[]);
    let loaded = 0;
    let interrupted = 0;
    for (const file of files) {
      if (!file.endsWith('.json')) continue;
      try {
        const raw = await readFile(path.join(TASK_DIR, file), 'utf8');
        const task = JSON.parse(raw) as TaskRecord;
        // 服务重启后，处于执行中/等待人工的任务其内存态已丢失（未实现断点续跑）
        if (task.status === 'running' || task.status === 'awaiting_approval') {
          task.status = 'failed';
          task.phase = 'FAILED';
          task.error = '服务重启导致任务中断（当前版本未实现断点续跑）';
          task.finished_at = new Date().toISOString();
          interrupted += 1;
        }
        this.tasks.set(task.id, task);
        loaded += 1;
      } catch (error) {
        log.warn(`跳过损坏的任务文件 ${file}`, error);
      }
    }
    log.info(`已加载 ${loaded} 个历史任务${interrupted ? `，其中 ${interrupted} 个因重启被标记为中断` : ''}`);
  }

  get(id: string): TaskRecord | undefined {
    return this.tasks.get(id);
  }

  list(): TaskRecord[] {
    return [...this.tasks.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
  }

  set(task: TaskRecord): void {
    this.tasks.set(task.id, task);
  }

  /** 防抖落盘，避免高频事件导致磁盘 IO 抖动。 */
  scheduleSave(task: TaskRecord, immediate = false): void {
    this.tasks.set(task.id, task);
    const pending = this.flushTimers.get(task.id);
    if (pending) clearTimeout(pending);
    if (immediate) {
      this.flushTimers.delete(task.id);
      void this.save(task);
      return;
    }
    const timer = setTimeout(() => {
      this.flushTimers.delete(task.id);
      void this.save(task);
    }, 250);
    this.flushTimers.set(task.id, timer);
  }

  async save(task: TaskRecord): Promise<void> {
    await mkdir(TASK_DIR, { recursive: true });
    const file = path.join(TASK_DIR, `${task.id}.json`);
    await writeFile(file, JSON.stringify(task, null, 2), 'utf8').catch((error: unknown) => {
      log.error(`任务 ${task.id} 落盘失败`, error);
    });
  }

  /** 进程退出前强制刷盘。 */
  async flushAll(): Promise<void> {
    for (const timer of this.flushTimers.values()) clearTimeout(timer);
    this.flushTimers.clear();
    await Promise.all([...this.tasks.values()].map((t) => this.save(t)));
    log.info(`已刷盘 ${this.tasks.size} 个任务`);
  }
}

export const taskStore = new TaskStore();
