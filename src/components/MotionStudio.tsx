/**
 * 动效工场 —— 视频动效组件的试演与编排面板。
 *
 * 两个工作模式：
 * - 预设试演：左侧清单挑一个预设，预览台立即播放单条演示；
 * - JSON 脚本：编辑声明式时间轴脚本（MotionScript），应用到预览台整体预演。
 * 播放控制由 usePlayerClock 驱动，与独立演示页一致：播完后提供「重播 / 回到起点」，
 * 重播时 generation 递增强制场景 remount，保证所有动效从头播放。
 */
import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { usePlayerClock, type PlayerClock } from '../motion/engine.tsx';
import { PRESET_MAP, PRESET_META, type PresetGroup } from '../motion/presets.tsx';
import {
  EXAMPLE_SCRIPT,
  MotionScriptStage,
  parseMotionScript,
  type MotionScript,
} from '../motion/script.tsx';

/* ------------------------------ 演示素材 ------------------------------ */

const DEMO_TEXT: Record<PresetGroup, string> = {
  入场: '制度即武器',
  强调: '关键词 · 反转',
  出场: '本段到此为止',
  转场: '下一幕开场',
};

/** 演示时长（秒）：个别预设需要更长的时间轴。 */
const DEMO_DURATION: Record<string, number> = {
  Typewriter: 2.2,
  CharStagger: 1.6,
  StrokeDraw: 1.6,
};

const GALLERY_TOTAL = 5;

function demoChildren(group: PresetGroup, name: string): ReactNode {
  if (name === 'StrokeDraw') {
    return (
      <svg width="240" height="96" viewBox="0 0 240 96">
        <path d="M12,72 Q66,6 120,46 T228,22" stroke="#22d3ee" strokeWidth="4" fill="none" strokeLinecap="round" />
      </svg>
    );
  }
  return DEMO_TEXT[group];
}

/* ------------------------------ 播放控制条 ------------------------------ */

function ControlBar({ clock, total, loop, onLoop }: { clock: PlayerClock; total: number; loop: boolean; onLoop: (v: boolean) => void }) {
  return (
    <div className="motion-controls">
      <button className="btn btn-sm" onClick={clock.reset} title="回到起点并重置场景">
        ⏮ 重置
      </button>
      <button className="btn btn-sm btn-primary" onClick={clock.toggle} style={{ minWidth: 92 }}>
        {clock.playing ? '⏸ 暂停' : '▶ 播放'}
      </button>
      <input
        type="range"
        min={0}
        max={total}
        step={0.05}
        value={clock.t}
        onChange={(e) => clock.seek(Number(e.target.value))}
        style={{ flex: 1, minWidth: 140 }}
      />
      <span className="mono small" style={{ whiteSpace: 'nowrap' }}>
        {clock.t.toFixed(1)}s / {total}s
      </span>
      <select value={clock.speed} onChange={(e) => clock.setSpeed(Number(e.target.value))} style={{ width: 86 }}>
        {[0.5, 0.75, 1, 1.5, 2].map((s) => (
          <option key={s} value={s}>
            {s}×
          </option>
        ))}
      </select>
      <label className="small" style={{ display: 'flex', alignItems: 'center', gap: 5, whiteSpace: 'nowrap' }}>
        <input type="checkbox" checked={loop} onChange={(e) => onLoop(e.target.checked)} style={{ width: 'auto' }} />
        循环
      </label>
    </div>
  );
}

/** 播完覆盖层：重播（从头）与回到起点（归零待命）。 */
function EndedOverlay({ onReplay, onReset }: { onReplay: () => void; onReset: () => void }) {
  return (
    <div className="motion-ended">
      <div className="motion-ended-title">已播完</div>
      <div className="motion-ended-btns">
        <button className="btn btn-sm btn-primary" onClick={onReplay}>
          ↻ 重新播放
        </button>
        <button className="btn btn-sm btn-ghost" onClick={onReset}>
          ⌂ 回到起点
        </button>
      </div>
    </div>
  );
}

/* ------------------------------ 预设试演 ------------------------------ */

function GalleryTab({ name, loop, onLoop }: { name: string; loop: boolean; onLoop: (v: boolean) => void }) {
  const clock = usePlayerClock(GALLERY_TOTAL, loop);
  const meta = PRESET_META.find((m) => m.name === name);
  const Preset = PRESET_MAP[name];

  // 选中预设即自动播放（组件随 preset 切换 remount，useEffect 只在挂载时触发一次）
  useEffect(() => {
    clock.play();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!meta || !Preset) return null;
  return (
    <div className="motion-stage-col">
      <div className="motion-stage">
        <div key={clock.generation} style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center', padding: '4% 6%' }}>
          <Preset duration={DEMO_DURATION[name] ?? 1.0} delay={0.5} style={{ fontSize: 30, fontWeight: 800, letterSpacing: 2, textAlign: 'center' }}>
            {demoChildren(meta.group, name)}
          </Preset>
        </div>
        {clock.ended ? <EndedOverlay onReplay={clock.play} onReset={clock.reset} /> : null}
      </div>
      <ControlBar clock={clock} total={GALLERY_TOTAL} loop={loop} onLoop={onLoop} />
      <div className="motion-desc">
        <span className="chip tone-info">{meta.group}</span>
        <strong>{meta.label}</strong>
        <span className="muted small">{meta.hint}</span>
      </div>
    </div>
  );
}

/* ------------------------------ JSON 脚本 ------------------------------ */

function ScriptTab({ loop, onLoop }: { loop: boolean; onLoop: (v: boolean) => void }) {
  const [text, setText] = useState(() => JSON.stringify(EXAMPLE_SCRIPT, null, 2));
  const [script, setScript] = useState<MotionScript>(EXAMPLE_SCRIPT);
  const [error, setError] = useState<string | null>(null);
  const clock = usePlayerClock(script.duration, loop);

  const apply = () => {
    const parsed = parseMotionScript(text);
    if (parsed.error || !parsed.script) {
      setError(parsed.error ?? '未知错误');
      return;
    }
    setError(null);
    setScript(parsed.script);
    clock.reset();
    clock.play();
  };

  return (
    <div className="motion-script-grid">
      <div className="motion-editor">
        <div className="motion-desc" style={{ justifyContent: 'space-between' }}>
          <span className="muted small">
            每条 track：preset（预设名）· at（起始秒）· duration / hold / text / easing / fade / params / style
          </span>
          <span className="row" style={{ gap: 8 }}>
            <button className="btn btn-sm btn-ghost" onClick={() => setText(JSON.stringify(EXAMPLE_SCRIPT, null, 2))}>
              载入示例
            </button>
            <button className="btn btn-sm btn-primary" onClick={apply}>
              应用到预览
            </button>
          </span>
        </div>
        <textarea
          className="motion-json"
          value={text}
          onChange={(e) => setText(e.target.value)}
          spellCheck={false}
        />
        {error ? <div className="motion-error">{error}</div> : null}
      </div>
      <div className="motion-stage-col">
        <div className="motion-stage">
          <MotionScriptStage key={clock.generation} script={script} />
          {clock.ended ? <EndedOverlay onReplay={clock.play} onReset={clock.reset} /> : null}
        </div>
        <ControlBar clock={clock} total={script.duration} loop={loop} onLoop={onLoop} />
      </div>
    </div>
  );
}

/* ------------------------------ 面板 ------------------------------ */

const GROUPS: PresetGroup[] = ['入场', '强调', '出场', '转场'];

export function MotionStudio({ onBack }: { onBack: () => void }) {
  const [mode, setMode] = useState<'gallery' | 'script'>('gallery');
  const [preset, setPreset] = useState('FadeIn');
  const [loop, setLoop] = useState(false);

  const grouped = useMemo(() => {
    const map = new Map<PresetGroup, typeof PRESET_META>();
    for (const meta of PRESET_META) {
      const list = map.get(meta.group) ?? [];
      list.push(meta);
      map.set(meta.group, list);
    }
    return map;
  }, []);

  return (
    <div className="motion-studio">
      <div className="motion-head">
        <button className="btn btn-sm btn-ghost" onClick={onBack}>
          ← 返回任务台
        </button>
        <div>
          <div style={{ fontSize: 17, fontWeight: 700 }}>动效工场</div>
          <div className="muted small">
            视频动效组件库试演与时间轴编排 —— 纯时间驱动（Remotion-ready），时长单位为秒
          </div>
        </div>
      </div>

      <div className="tabs">
        <button className={`tab${mode === 'gallery' ? ' active' : ''}`} onClick={() => setMode('gallery')}>
          预设试演（{PRESET_META.length} 个）
        </button>
        <button className={`tab${mode === 'script' ? ' active' : ''}`} onClick={() => setMode('script')}>
          JSON 脚本
        </button>
      </div>

      {mode === 'gallery' ? (
        <div className="motion-grid">
          <aside className="motion-list">
            {GROUPS.map((group) => (
              <div key={group}>
                <div className="motion-list-group">{group}</div>
                {(grouped.get(group) ?? []).map((meta) => (
                  <button
                    key={meta.name}
                    className={`motion-item${preset === meta.name ? ' active' : ''}`}
                    onClick={() => setPreset(meta.name)}
                  >
                    <span>{meta.label}</span>
                    <span className="muted small">{meta.name}</span>
                  </button>
                ))}
              </div>
            ))}
          </aside>
          <GalleryTab key={preset} name={preset} loop={loop} onLoop={setLoop} />
        </div>
      ) : (
        <ScriptTab loop={loop} onLoop={setLoop} />
      )}
    </div>
  );
}
