"""影视制作工厂 —— 纯标准库音频合成（不引 numpy / ffmpeg）。

离线通道要真的能发出声音：配音（共振峰人声示意）、音效（呼啸/撞击/雨声…）、
配乐（和弦垫 + 低音 + 琶音 + 打击）与混音（增益 + 闪避 + 归一化）都在这里，
产出 16bit PCM WAV。它们不是「专业配音」，而是**可试听的示意轨**——
节点会如实标注 ``simulated``，接了真实 TTS/音频服务时由 ``media.py`` 覆盖。
"""

from __future__ import annotations

import io
import math
import wave
from array import array
from typing import Any, Sequence

SR = 22050
MAX_SECONDS = 180.0

Sample = array


def _clamp(value: float) -> float:
    return -1.0 if value < -1.0 else 1.0 if value > 1.0 else value


def seconds_of(samples: Sequence[float]) -> float:
    return round(len(samples) / SR, 3)


def silence(seconds: float) -> Sample:
    count = int(max(0.0, min(MAX_SECONDS, seconds)) * SR)
    return array("d", [0.0] * count)


def _envelope(total: int, shape: str) -> list[float]:
    if total <= 0:
        return []
    if shape == "flat":
        return [1.0] * total
    edge = max(1, int(total * 0.08))
    out: list[float] = []
    for index in range(total):
        if shape == "out":
            value = 1.0 if index < edge else math.exp(-4.2 * (index - edge) / max(1, total - edge))
        elif shape == "in":
            value = index / max(1, total - 1)
            value = value**2.2
        else:  # in_out
            rise = min(1.0, index / max(1, edge))
            fall = min(1.0, (total - 1 - index) / max(1, edge))
            value = rise * fall
        out.append(value)
    return out


def _lowpass(buf: Sample, alpha: float = 0.25) -> Sample:
    result = array("d", [0.0] * len(buf))
    previous = 0.0
    for index, value in enumerate(buf):
        previous = previous + alpha * (value - previous)
        result[index] = previous
    return result


def tone(
    freq: float,
    seconds: float,
    gain: float = 0.5,
    envelope: str = "out",
    timbre: str = "sine",
    vibrato: float = 0.0,
) -> Sample:
    count = int(seconds * SR)
    buf = array("d", [0.0] * count)
    shapes = _envelope(count, envelope)
    harmonics = {"sine": (1.0,), "soft": (1.0, 0.32, 0.12), "bright": (1.0, 0.55, 0.3, 0.16), "deep": (1.0, 0.18)}[timbre]
    for index in range(count):
        t = index / SR
        f = freq * (1.0 + vibrato * math.sin(2 * math.pi * 5.2 * t)) if vibrato else freq
        value = 0.0
        for order, weight in enumerate(harmonics, start=1):
            value += weight * math.sin(2 * math.pi * f * order * t)
        buf[index] = value / len(harmonics) * shapes[index] * gain
    return buf


def noise_burst(seconds: float, sweep: tuple[float, float], gain: float = 0.4, envelope: str = "out", seed: int = 11) -> Sample:
    """带通噪声：呼啸 / 雨 / 纸张。用一阶带通近似扫频。"""
    count = int(seconds * SR)
    buf = array("d", [0.0] * count)
    shapes = _envelope(count, envelope)
    state = (seed * 1103515245 + 12345) & 0xFFFFFFFF
    band_prev = 0.0
    low_prev = 0.0
    for index in range(count):
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        white = ((state >> 8) & 0xFFFF) / 32768.0 - 1.0
        t = index / max(1, count - 1)
        cutoff = sweep[0] + (sweep[1] - sweep[0]) * t
        alpha = min(0.95, max(0.02, cutoff / SR))
        low_prev = low_prev + alpha * (white - low_prev)
        band_prev = band_prev + 0.6 * (low_prev - band_prev)
        buf[index] = band_prev * shapes[index] * gain * 2.4
    return _lowpass(buf, 0.4)


def sfx(kind: str, seconds: float | None = None, gain: float = 0.7, seed: int = 11) -> Sample:
    """音效库（``craft.SFX_LIBRARY`` 的键）。"""
    from .craft import SFX_LIBRARY

    spec = SFX_LIBRARY.get(kind)
    if spec is None:
        return silence(seconds or 0.3)
    duration = seconds or float(spec["dur"])
    if spec["kind"] == "noise":
        return noise_burst(duration, tuple(spec["sweep"]), gain, str(spec["envelope"]), seed)
    if spec["kind"] == "boom":
        count = int(duration * SR)
        buf = array("d", [0.0] * count)
        freq = float(spec["freq"])
        shapes = _envelope(count, str(spec["envelope"]))
        for index in range(count):
            t = index / SR
            drop = freq * math.exp(-6.0 * t) + 32.0
            buf[index] = math.sin(2 * math.pi * drop * t) * shapes[index] * gain
        return _lowpass(buf, 0.5)
    if spec["kind"] == "arp":
        total = silence(duration)
        notes = (0, 4, 7, 12)
        step = duration / len(notes)
        for offset, semitone in enumerate(notes):
            piece = tone(float(spec["freq"]) * 2 ** (semitone / 12), step * 0.9, gain * 0.6, "out", "bright")
            start = int(offset * step * SR)
            for index, value in enumerate(piece):
                if start + index < len(total):
                    total[start + index] += value
        return total
    return tone(float(spec.get("freq", 800)), duration, gain, str(spec["envelope"]), "sine")


def voice(text: str, preset: dict[str, Any], seconds: float | None = None, gain: float = 0.85, seed: int = 5) -> Sample:
    """共振峰近似的人声示意轨：逐字一个音节包络 + 基频曲线 + 两个共振峰加权。

    只用于占位试听：真实配音由 ``media.py`` 走 TTS 通道覆盖。
    """
    from .craft import VOICE_TAIL_S, split_dialogue

    pitch = float(preset.get("pitch", 1.0))
    timbre = str(preset.get("timbre", "soft"))
    base_f0 = (118.0 if timbre in {"deep", "firm"} else 176.0 if timbre == "mellow" else 208.0 if timbre == "bright" else 156.0) * max(0.6, min(1.8, pitch))
    rate = float(preset.get("rate", 1.0)) or 1.0
    sentences = split_dialogue(text) or ([text.strip()] if text and text.strip() else [])
    syllables: list[str] = []
    for sentence in sentences:
        chars = [ch for ch in sentence if not ch.isspace()]
        syllables.extend(chars if chars else [])
        syllables.append("，")  # 句间停顿
    if not syllables:
        syllables = ["嗯"] * 6

    per_syllable = 1.0 / (4.6 * rate)
    total_seconds = seconds if seconds else min(MAX_SECONDS, len(syllables) * per_syllable + VOICE_TAIL_S)
    scale = min(per_syllable, total_seconds / max(1, len(syllables)))
    count = int(total_seconds * SR)
    buf = array("d", [0.0] * count)
    state = (seed * 2654435761) & 0xFFFFFFFF
    f1 = 620.0 * pitch if timbre in {"deep", "firm", "mellow"} else 760.0 * pitch
    f2 = 1720.0 * (1.0 if timbre != "bright" else 1.18)
    for step, char in enumerate(syllables):
        start = int(step * scale * SR)
        is_pause = char in "，。、；： "
        length = int((scale * (0.5 if is_pause else 0.82)) * SR)
        if start + length >= count:
            break
        if is_pause:
            continue
        state = (1103515245 * state + 12345) & 0xFFFFFFFF
        contour = ((state >> 9) & 0xFF) / 255.0 - 0.5
        shapes = _envelope(length, "in_out")
        for index in range(length):
            t = index / SR
            voice_f0 = base_f0 * (1.0 + contour * 0.35 * (1 - 2 * index / max(1, length)) + 0.012 * math.sin(2 * math.pi * 5.0 * t))
            value = 0.0
            harmonic = 1
            while voice_f0 * harmonic < 5200:
                amplitude = 1.0 / harmonic
                resonance = math.exp(-(((voice_f0 * harmonic - f1) / 220.0) ** 2)) + 0.7 * math.exp(
                    -(((voice_f0 * harmonic - f2) / 320.0) ** 2)
                )
                value += amplitude * resonance * math.sin(2 * math.pi * voice_f0 * harmonic * t)
                harmonic += 1
            hiss = 0.0
            if index % 7 == 0:
                state = (1103515245 * state + 12345) & 0xFFFFFFFF
            hiss = (((state >> 7) & 0xFF) / 255.0 - 0.5) * (0.25 if char in "sxzhrcf" else 0.06)
            buf[start + index] += (value * 0.5 + hiss) * shapes[index] * gain
    return _lowpass(buf, 0.55)


def music(seconds: float, mood_spec: dict[str, Any], gain: float = 0.55, seed: int = 3) -> Sample:
    """和弦垫 + 低音 + 琶音 + 基础打击，按 BPM 铺满指定时长。"""
    seconds = max(1.0, min(MAX_SECONDS, seconds))
    count = int(seconds * SR)
    buf = array("d", [0.0] * count)
    bpm = float(mood_spec.get("bpm", 100))
    beat = 60.0 / bpm
    bar = beat * 4
    root = int(mood_spec.get("root", 57))
    progression = list(mood_spec.get("progression") or [0, 5, 7, 0])
    minor = mood_spec.get("scale") == "minor"

    def freq_of(semiTone: int) -> float:
        return 440.0 * 2 ** ((semiTone - 9) / 12)

    def add(piece: Sample, start_seconds: float, weight: float = 1.0) -> None:
        start = int(start_seconds * SR)
        for index, value in enumerate(piece):
            target = start + index
            if target >= count:
                break
            buf[target] += value * weight

    total_bars = int(math.ceil(seconds / bar))
    for bar_index in range(total_bars):
        chord_offset = progression[bar_index % len(progression)]
        third = 3 if minor else 4
        notes = [root + chord_offset, root + chord_offset + third, root + chord_offset + 7]
        pad = tone(freq_of(notes[0]), bar * 0.98, 0.22, "flat", "soft")
        for note in notes[1:]:
            piece = tone(freq_of(note), bar * 0.98, 0.2, "flat", "soft")
            for index, value in enumerate(piece):
                pad[index] += value
        add(pad, bar_index * bar, 1.0)
        add(tone(freq_of(notes[0] - 12), beat * 0.9, 0.3, "out", "sine"), bar_index * bar, 1.0)
        for step in range(4):
            add(tone(freq_of(notes[step % 3] + 12), beat * 0.42, 0.16, "out", "bright"), bar_index * bar + step * beat, 1.0)
            add(tone(freq_of(notes[(step + 1) % 3] + 12), beat * 0.42, 0.14, "out", "bright"), bar_index * bar + step * beat + beat / 2, 0.8)
        kick = array("d", [0.0] * int(0.18 * SR))
        for index in range(len(kick)):
            t = index / SR
            kick[index] = math.sin(2 * math.pi * (110 * math.exp(-9 * t) + 44) * t) * math.exp(-7 * t) * 0.6
        hat = noise_burst(0.06, (7200, 5200), 0.12, "out", seed + bar_index)
        for step in range(4):
            add(kick, bar_index * bar + step * beat, 1.0)
            add(hat, bar_index * bar + step * beat + beat / 2, 1.0)
    return _lowpass(buf, 0.7)


def mix(tracks: Sequence[dict[str, Any]], total_seconds: float | None = None, normalize: bool = True) -> Sample:
    """混音：按偏移叠加 + 增益 + 人声闪避（sidechain）+ 峰值归一化。"""
    lengths = [float(track.get("offset", 0.0)) + seconds_of(track.get("samples") or []) for track in tracks]
    length = total_seconds or (max(lengths) if lengths else 1.0)
    length = max(0.5, min(MAX_SECONDS, length))
    count = int(length * SR)
    out = array("d", [0.0] * count)

    vocals = [track for track in tracks if str(track.get("kind")) in {"voice", "dialogue"}]
    block = 256
    duck: list[float] = []
    if vocals:
        voice_buffer = array("d", [0.0] * count)
        for track in vocals:
            start = int(float(track.get("offset", 0.0)) * SR)
            gain = float(track.get("gain", 1.0))
            for index, value in enumerate(track.get("samples") or []):
                target = start + index
                if target >= count:
                    break
                voice_buffer[target] += value * gain
        for block_start in range(0, count, block):
            window = voice_buffer[block_start:block_start + block]
            peak = max((abs(value) for value in window), default=0.0)
            duck.append(1.0 if peak < 0.035 else 0.38)
        for index, value in enumerate(voice_buffer):
            out[index] += value
    else:
        duck = [1.0] * (count // block + 1)

    for track in tracks:
        if str(track.get("kind")) in {"voice", "dialogue"}:
            continue
        start = int(float(track.get("offset", 0.0)) * SR)
        gain = float(track.get("gain", 1.0))
        samples = track.get("samples") or []
        sidechain = float(track.get("sidechain", 1.0))
        for index, value in enumerate(samples):
            target = start + index
            if target >= count:
                break
            level = gain * (duck[target // block] ** sidechain)
            out[target] += value * level

    if normalize:
        peak = max((abs(value) for value in out), default=0.0)
        if peak > 1e-6:
            factor = 0.92 / peak
            out = array("d", [value * factor for value in out])
    return array("d", [_clamp(value) for value in out])


def wav_bytes(samples: Sequence[float], stereo: bool = False) -> bytes:
    """float 样本 → 16bit PCM WAV 字节。"""
    mono = array("h", [int(_clamp(value) * 32767) for value in samples])
    if stereo:
        interleaved = array("h", [0] * (len(mono) * 2))
        interleaved[0::2] = mono
        interleaved[1::2] = mono
        payload = interleaved.tobytes()
        channels = 2
    else:
        payload = mono.tobytes()
        channels = 1
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.setnframes(len(mono))
        handle.writeframes(payload)
    return buffer.getvalue()


def peaks_of(samples: Sequence[float], count: int = 900) -> list[float]:
    if not samples:
        return []
    step = max(1, len(samples) // count)
    out: list[float] = []
    for start in range(0, len(samples), step):
        window = samples[start:start + step]
        out.append(max((abs(value) for value in window), default=0.0))
    return out[:count]


def read_wav(blob: bytes) -> Sample:
    """WAV 字节 → float 样本：混音师把各条轨读回同一条时间线（含采样率对齐）。"""
    with wave.open(io.BytesIO(blob), "rb") as handle:
        channels = max(1, handle.getnchannels())
        width = handle.getsampwidth()
        rate = handle.getframerate() or SR
        data = handle.readframes(handle.getnframes())
    if width == 2:
        raw = array("h")
        raw.frombytes(data[: len(data) - (len(data) % 2)])
        frames = [value / 32768.0 for value in raw]
    elif width == 1:
        raw = array("B")
        raw.frombytes(data)
        frames = [(value - 128) / 128.0 for value in raw]
    else:
        raise ValueError(f"暂不支持 {width * 8}bit 音频，请转成 16bit PCM WAV")
    if channels > 1:
        frames = [
            sum(frames[index:index + channels]) / channels
            for index in range(0, len(frames) - (len(frames) % channels), channels)
        ]
    if rate == SR or not frames:
        return array("d", frames)
    ratio = SR / rate
    count = int(len(frames) * ratio)
    out = array("d", [0.0] * count)
    for index in range(count):
        position = index / ratio
        left = int(position)
        right = min(left + 1, len(frames) - 1)
        weight = position - left
        out[index] = frames[left] * (1.0 - weight) + frames[right] * weight
    return out


def rms_of(samples: Sequence[float]) -> float:
    if not samples:
        return 0.0
    total = 0.0
    for value in samples:
        total += value * value
    return math.sqrt(total / len(samples))


def db_of(value: float, floor: float = 1e-4) -> float:
    """电平转 dBFS（供混音师报一个可核对的响度口径）。"""
    return 20.0 * math.log10(max(floor, abs(float(value))))
