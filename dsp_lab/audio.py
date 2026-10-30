"""Duplex PortAudio transport, bounded history, and non-blocking status snapshots."""
from dataclasses import asdict
from pathlib import Path
import json
import time
import numpy as np
from scipy.signal import resample_poly
import sounddevice as sd
import soundfile as sf
from .core import RATE, HOP, Settings, ProcessingChain, dbfs, warm_up


class HistoryOverrun(ValueError):
    """Requested absolute samples have already been overwritten."""


class History:
    def __init__(self, seconds=30, shared=None, reset=True):
        self.capacity = RATE * seconds
        if shared is None:
            self.values = np.zeros((self.capacity, 4), dtype=np.float32)
            self._state = np.zeros(3, dtype=np.int64)
        else:
            self.values = np.frombuffer(shared[0], dtype=np.float32).reshape(self.capacity, 4)
            self._state = np.frombuffer(shared[1], dtype=np.int64)
        if reset:
            self.position = 0
            self.total = 0
            self.version = 0

    @property
    def position(self):
        return int(self._state[0])

    @position.setter
    def position(self, value):
        self._state[0] = value

    @property
    def total(self):
        return int(self._state[1])

    @total.setter
    def total(self, value):
        self._state[1] = value

    @property
    def version(self):
        return int(self._state[2])

    @version.setter
    def version(self, value):
        self._state[2] = value

    def append(self, raw, processed, reference, residual):
        self.version += 1
        n = len(raw)
        end = min(n, self.capacity - self.position)
        for col, samples in enumerate((raw, processed, reference, residual)):
            self.values[self.position:self.position + end, col] = samples[:end]
            if end < n:
                self.values[:n - end, col] = samples[end:]
        self.position = (self.position + n) % self.capacity
        self.total += n
        self.version += 1

    def snapshot(self, seconds=30):
        for _ in range(5):
            version = self.version
            if version % 2:
                continue
            end, total = self.position, self.total
            count = min(total, self.capacity, round(seconds * RATE))
            start = (end - count) % self.capacity
            if count == 0:
                result = np.zeros((0, 4), dtype=np.float32)
            elif start < end:
                result = self.values[start:end].copy()
            else:
                result = np.concatenate((self.values[start:], self.values[:end]))
            if self.version == version:
                return result
        raise RuntimeError("音频正在更新，请稍后再试")

    def read_range(self, start_frame, stop_frame):
        start, stop = int(start_frame), int(stop_frame)
        if start < 0 or stop < start:
            raise ValueError("无效的样本区间")
        for _ in range(8):
            version = self.version
            if version % 2:
                continue
            total = self.total
            if start < max(0, total - self.capacity):
                raise HistoryOverrun("所需音频已被实时缓存覆盖")
            if stop > total:
                raise ValueError("所需音频尚未采集")
            count = stop - start
            if not count:
                return np.empty((0, 4), np.float32)
            offset = start % self.capacity
            first = min(count, self.capacity - offset)
            data = np.empty((count, 4), np.float32)
            data[:first] = self.values[offset:offset + first]
            if first < count:
                data[first:] = self.values[:count - first]
            if self.version == version:
                return data
        raise RuntimeError("音频正在更新，请稍后再试")


def load_wave(path):
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    if len(data) > rate * 600:
        raise ValueError("首版支持最长10分钟的参考音频")
    mono = np.mean(data, axis=1)
    if rate != RATE:
        from math import gcd
        common = gcd(rate, RATE)
        mono = resample_poly(mono, RATE // common, rate // common).astype(np.float32)
    if not len(mono) or not np.isfinite(mono).all():
        raise ValueError("音频为空或包含非有限值")
    return np.ascontiguousarray(mono)


class AudioEngine:
    def __init__(self, shared_history=None):
        self.shared_history = shared_history
        self.settings = Settings()
        self.chain = ProcessingChain()
        self.history = History(shared=self.shared_history)
        self.stream = None
        self.reference_audio = np.zeros(0, dtype=np.float32)
        self.reference_position = 0
        self.reference_enabled = False
        self.previous_render = np.zeros(HOP, dtype=np.float32)
        self.xruns = 0
        self.deadline_misses = 0
        self.clips = 0
        self.error = ""
        self.stats = {}
        self.started_at = 0
        self.latest = (np.zeros(HOP), np.zeros(HOP))

    @property
    def running(self):
        return self.stream is not None and self.stream.active

    def start(self, input_device=None, output_device=None):
        if self.running:
            return
        warm_up()
        self.chain = ProcessingChain()
        self.history = History(shared=self.shared_history)
        self.previous_render.fill(0)
        self.xruns = self.deadline_misses = self.clips = 0
        self.error = ""
        sd.check_input_settings(device=input_device, channels=1, samplerate=RATE, dtype="float32")
        sd.check_output_settings(device=output_device, channels=1, samplerate=RATE, dtype="float32")
        stream = sd.Stream(
            samplerate=RATE, blocksize=HOP, device=(input_device, output_device),
            channels=(1, 1), dtype="float32", latency="low", callback=self._callback,
        )
        try:
            stream.start()
        except Exception:
            stream.close()
            raise
        self.stream = stream
        self.started_at = time.perf_counter()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.reference_enabled = False

    def _reference_block(self, settings):
        track = self.reference_audio
        if not self.reference_enabled or not len(track):
            return np.zeros(HOP, dtype=np.float32)
        pos = self.reference_position
        # Wrap once per block, including audio shorter than one block.
        indices = (np.arange(HOP) + pos) % len(track)
        block = track[indices] * settings.reference_gain
        self.reference_position = (pos + HOP) % len(track)
        return block

    def _callback(self, indata, outdata, frames, timing, status):
        outdata.fill(0)
        if frames != HOP:
            self.error = "音频设备未提供固定帧长，已停止输出"
            raise sd.CallbackAbort
        begin = time.perf_counter()
        if status:
            self.xruns += 1
        try:
            settings = self.settings
            mic = indata[:, 0]
            reference = self.previous_render.copy()
            result, residual, _ = self.chain.process(mic, reference, settings)
            render = self._reference_block(settings)
            if settings.monitor:
                render = render + result * settings.output_gain
            peak = float(np.max(np.abs(render)))
            if peak > 0.98:
                self.clips += 1
            np.clip(render, -0.98, 0.98, out=render)
            outdata[:, 0] = render
            self.previous_render[:] = render
            self.history.append(mic, result, reference, residual)
            stats = {
                "input_dbfs": dbfs(mic),
                "output_dbfs": dbfs(result), "reference_dbfs": dbfs(reference),
                "xruns": self.xruns, "deadline_misses": self.deadline_misses,
                "clipped_blocks": self.clips, "noise_profile": self.chain.enhancer.has_profile,
                "noise_learning": self.chain.enhancer.learning > 0,
                "aec_adapt": settings.aec and settings.adapt,
            }
            elapsed = (time.perf_counter() - begin) * 1000
            if elapsed > HOP / RATE * 1000:
                self.deadline_misses += 1
            stats["processing_ms"] = elapsed
            stats["deadline_misses"] = self.deadline_misses
            self.stats = stats
        except Exception as exc:
            self.error = f"实时处理停止：{exc}"
            raise sd.CallbackAbort from exc

    def set_reference(self, samples):
        # Assigned as one immutable array reference, never changed in the callback.
        self.reference_audio = np.ascontiguousarray(samples, dtype=np.float32)
        self.reference_position = 0

    def export(self, folder):
        data = self.history.snapshot(30)
        if not len(data):
            raise ValueError("尚无采集音频，请先启动实时处理")
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=False)
        for col, name in enumerate(("microphone", "processed", "render_reference", "aec_residual")):
            sf.write(folder / f"{name}.wav", data[:, col], RATE, subtype="FLOAT")
        meta = {
            "sample_rate": RATE, "frames": len(data), "settings_at_export": asdict(self.settings),
            "stats_at_export": self.stats, "source": "live microphone",
            "notes": "设置可能在窗口内变化；render_reference是AEC实际使用的上一回调块输出，不代表声学回环实测对齐；processed固定延迟256样本。",
        }
        (folder / "session.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return folder
