/** 任务视频脚本 → 视频工场字幕草稿：只搬运真实存在的时间轴与口播，不补造内容。 */
import type { TaskRecord } from './types.ts';

export interface VideoDraft {
  taskId: string;
  source: string;
  name: string;
  text: string;
  cues: number;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

function asNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() !== '') {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value.trim() : '';
}

function timecode(seconds: number): string {
  const total = Math.max(0, seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  const millis = Math.round((total - Math.floor(total)) * 1000);
  const pad = (value: number, size = 2) => String(value).padStart(size, '0');
  return `${pad(hours)}:${pad(minutes)}:${pad(secs)},${pad(millis, 3)}`;
}

/** 取最新版本的 video_script 产物；没有就返回 null，让入口如实禁用。 */
export function latestVideoScript(task: TaskRecord) {
  const rows = task.artifacts.filter((artifact) => artifact.type === 'video_script');
  if (!rows.length) return null;
  return [...rows].sort((a, b) => b.version - a.version || b.created_at.localeCompare(a.created_at))[0];
}

/**
 * 把脚本分镜转成 SRT：优先用每镜的起止时间，缺时间就按时长顺排；
 * 字幕优先于口播，两者都没有的分镜直接跳过。
 */
export function scriptToSrt(content: Record<string, unknown>): { text: string; cues: number } {
  const shots = Array.isArray(content.shots) ? content.shots.map(asRecord).filter((row): row is Record<string, unknown> => row !== null) : [];
  const cues: { start: number; end: number; text: string }[] = [];
  let cursor = 0;

  for (const shot of shots) {
    const text = asText(shot.subtitle) || asText(shot.voiceover);
    if (!text) continue;
    const duration = asNumber(shot.duration_seconds) ?? 3;
    const start = asNumber(shot.start_second) ?? cursor;
    const end = asNumber(shot.end_second) ?? start + Math.max(0.6, duration);
    if (end <= start) continue;
    cues.push({ start, end, text });
    cursor = Math.max(cursor, end);
  }

  if (!cues.length) {
    const subtitles = Array.isArray(content.subtitles)
      ? content.subtitles.map(asRecord).filter((row): row is Record<string, unknown> => row !== null)
      : [];
    let fallbackCursor = 0;
    for (const cue of subtitles) {
      const text = asText(cue.text) || asText(cue.line) || asText(cue.voiceover);
      if (!text) continue;
      const start = asNumber(cue.start_second) ?? fallbackCursor;
      const end = asNumber(cue.end_second) ?? start + 3;
      if (end <= start) continue;
      cues.push({ start, end, text });
      fallbackCursor = Math.max(fallbackCursor, end);
    }
  }

  const text = cues
    .map((cue, index) => `${index + 1}\n${timecode(cue.start)} --> ${timecode(cue.end)}\n${cue.text}`)
    .join('\n\n');
  return { text, cues: cues.length };
}

export function buildVideoDraft(task: TaskRecord): VideoDraft | null {
  const artifact = latestVideoScript(task);
  if (!artifact) return null;
  const { text, cues } = scriptToSrt(artifact.content);
  if (!cues) return null;
  const rawName = `${task.brief.brand}-${task.brief.product}`.replace(/[\\/:*?"<>|\s]+/g, '-').replace(/-+/g, '-');
  return {
    taskId: task.id,
    source: `${task.brief.brand} · ${task.brief.product}｜${artifact.title} v${artifact.version}`,
    name: `${rawName || '任务脚本'}-${cues}镜.srt`,
    text,
    cues,
  };
}
