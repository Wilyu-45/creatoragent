/**
 * 字幕解析 —— SRT / WebVTT / ASS / 纯文本 → 统一 Cue 轨。
 *
 * 输入宽容：自动嗅探格式，容忍 \r\n、BOM、缺失序号行、VTT 头部块；
 * 纯文本按标点智能断句并按阅读速度（≈4.2 字/秒）编排时间。
 * 时间单位一律为秒（与 motion 引擎一致）。
 */

export interface Cue {
  /** 起始秒。 */
  start: number;
  /** 结束秒。 */
  end: number;
  /** 已去除标签/控制符的纯文本（单行）。 */
  text: string;
}

export type SubtitleFormat = 'srt' | 'vtt' | 'ass' | 'txt';

export interface SubtitleParseResult {
  cues: Cue[];
  format: SubtitleFormat;
}

/* ------------------------------ 时间解析 ------------------------------ */

/** 解析 HH:MM:SS,mmm / MM:SS.mmm / H:MM:SS:CC（ASS 厘秒）等写法，失败返回 null。 */
function parseTime(raw: string): number | null {
  const m = /^(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,:](\d{1,3})$/.exec(raw.trim());
  if (!m) return null;
  const hours = m[1] ? Number(m[1]) : 0;
  const minutes = Number(m[2]);
  const seconds = Number(m[3]);
  const fracRaw = m[4];
  // 分隔为 ':' 时是 ASS 的厘秒（两位），其余按毫秒处理（补齐三位）
  const frac = m[0].includes(':') && m[0].split(':').length === 4
    ? Number(fracRaw.padEnd(2, '0')) / 100
    : Number(fracRaw.padEnd(3, '0').slice(0, 3)) / 1000;
  if (!Number.isFinite(frac)) return null;
  return hours * 3600 + minutes * 60 + seconds + frac;
}

/** 去除内嵌标签：<i>、{\an8}、VTT voice span 等。 */
function stripTags(text: string): string {
  return text
    .replace(/\{\\[^}]*\}/g, '')
    .replace(/<[^>]+>/g, '')
    .replace(/\\N|\\n/g, ' ')
    .replace(/\\h/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

/* ------------------------------ SRT / VTT ------------------------------ */

function parseTimestamped(raw: string): Cue[] | null {
  const blocks = raw.replace(/\uFEFF/g, '').split(/\n{2,}/);
  const cues: Cue[] = [];
  const timeRe = /(\d{1,3}:\d{1,2}:\d{2}[.,:]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})\s*-->\s*(\d{1,3}:\d{1,2}:\d{2}[.,:]\d{1,3}|\d{1,2}:\d{2}[.,]\d{1,3})/;
  for (const block of blocks) {
    const lines = block.split('\n').filter((l) => l.trim().length > 0);
    if (lines.length === 0) continue;
    const timeIdx = lines.findIndex((l) => timeRe.test(l));
    if (timeIdx < 0) continue;
    const m = timeRe.exec(lines[timeIdx]);
    if (!m) continue;
    const start = parseTime(m[1]);
    const end = parseTime(m[2]);
    if (start == null || end == null || end <= start) continue;
    const text = stripTags(lines.slice(timeIdx + 1).join(' '));
    if (text.length === 0) continue;
    cues.push({ start, end, text });
  }
  return cues.length > 0 ? cues : null;
}

/* ------------------------------ ASS ------------------------------ */

function parseAss(raw: string): Cue[] | null {
  const lines = raw.split(/\r?\n/);
  // 默认 V4+ 字段顺序；若声明了 Format: 则按声明解析
  let startIdx = 1;
  let endIdx = 2;
  let textIdx = 9;
  let inEvents = false;
  const cues: Cue[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (/^\[Events\]/i.test(trimmed)) {
      inEvents = true;
      continue;
    }
    if (/^\[/.test(trimmed)) {
      inEvents = false;
      continue;
    }
    if (!inEvents) continue;
    if (/^Format:/i.test(trimmed)) {
      const fields = trimmed.slice(7).split(',').map((f) => f.trim().toLowerCase());
      startIdx = fields.indexOf('start');
      endIdx = fields.indexOf('end');
      textIdx = fields.indexOf('text');
      continue;
    }
    if (!/^Dialogue:/i.test(trimmed)) continue;
    const parts = trimmed.slice(9).split(',');
    if (parts.length <= textIdx) continue;
    const start = parseTime(parts[startIdx] ?? '');
    const end = parseTime(parts[endIdx] ?? '');
    if (start == null || end == null || end <= start) continue;
    const text = stripTags(parts.slice(textIdx).join(','));
    if (text.length === 0) continue;
    cues.push({ start, end, text });
  }
  return cues.length > 0 ? cues : null;
}

/* ------------------------------ 纯文本 ------------------------------ */

const READ_SPEED = 4.2; // 字/秒（中文口播常见语速）

/** 按标点断句 → 按阅读速度线性编排时间。 */
function parsePlainText(raw: string): Cue[] {
  const sentences: string[] = [];
  for (const para of raw.split(/\r?\n/)) {
    const trimmed = para.trim();
    if (trimmed.length === 0) continue;
    // 在句末标点后断句；无标点的长段按 18 字分块
    const pieces = trimmed.split(/(?<=[。！？；!?;…])/);
    for (const piece of pieces) {
      const text = piece.trim();
      if (text.length === 0) continue;
      if (text.length <= 20) {
        sentences.push(text);
        continue;
      }
      for (let i = 0; i < text.length; i += 18) {
        const chunk = text.slice(i, i + 18).trim();
        if (chunk.length > 0) sentences.push(chunk);
      }
    }
  }
  let t = 0.4;
  const cues: Cue[] = [];
  for (const text of sentences) {
    const dur = Math.min(6.5, Math.max(1.2, Array.from(text).length / READ_SPEED));
    cues.push({ start: Number(t.toFixed(3)), end: Number((t + dur).toFixed(3)), text });
    t += dur + 0.12;
  }
  return cues;
}

/* ------------------------------ 入口 ------------------------------ */

/** 嗅探格式并解析；解析失败抛出带原因的错误。 */
export function parseSubtitles(raw: string, hintName = ''): SubtitleParseResult {
  const text = raw.replace(/\r\n?/g, '\n');
  if (text.trim().length === 0) throw new Error('内容为空');

  const ext = hintName.split('.').pop()?.toLowerCase() ?? '';
  const isVtt = /^\uFEFF?WEBVTT/m.test(text) || ext === 'vtt';
  const isAss = /\[Script Info\]/i.test(text) || /^\s*Dialogue:/m.test(text) || ext === 'ass' || ext === 'ssa';
  const isTimed = /-->/.test(text) || ext === 'srt';

  if (isVtt) {
    const cues = parseTimestamped(text.replace(/^WEBVTT[^\n]*\n/, ''));
    if (cues) return { cues, format: 'vtt' };
    throw new Error('WebVTT 解析失败：未找到有效的时间轴行（-->）');
  }
  if (isAss) {
    const cues = parseAss(text);
    if (cues) return { cues, format: 'ass' };
    throw new Error('ASS 解析失败：[Events] 中没有有效 Dialogue 行');
  }
  if (isTimed) {
    const cues = parseTimestamped(text);
    if (cues) return { cues, format: 'srt' };
    throw new Error('SRT 解析失败：时间轴行格式无法识别');
  }
  const cues = parsePlainText(text);
  if (cues.length === 0) throw new Error('未解析出任何字幕内容');
  return { cues, format: 'txt' };
}
