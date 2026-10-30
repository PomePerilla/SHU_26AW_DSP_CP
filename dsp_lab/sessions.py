"""Bounded-memory, sample-indexed sessions and peak-preserving waveform reads."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import math
import multiprocessing as mp
import os
import time
import numpy as np
import soundfile as sf
from .core import RATE, HOP
from .audio import History

TRACKS = ("microphone", "processed", "render_reference", "aec_residual")
PEAK_BLOCK = 256


def read_json(path):
    for attempt in range(50):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (PermissionError, FileNotFoundError):
            if attempt == 49:
                raise
            time.sleep(min(0.005 * (attempt + 1), 0.025))


def atomic_json(path, values):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows readers/antivirus can briefly deny delete-sharing on the old file.
    # Retry only this transient replacement; all disk I/O stays off the callback.
    for attempt in range(50):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 49:
                raise
            time.sleep(min(0.005 * (attempt + 1), 0.025))


def reduce_peaks(data, start, budget):
    """Min/max pairs preserve transients instead of striding past them."""
    if not len(data):
        return np.empty(0), np.empty((0, 2), np.float32)
    size = max(1, math.ceil(len(data) / max(1, budget)))
    if size == 1:
        return (np.arange(len(data)) + start) / RATE, data[:, :2].copy()
    indices = np.arange(0, len(data), size)
    low = np.minimum.reduceat(data[:, :2], indices)
    high = np.maximum.reduceat(data[:, :2], indices)
    values = np.stack((low, high), axis=1).reshape(-1, 2)
    positions = np.column_stack((indices, np.minimum(indices + size - 1, len(data) - 1))).reshape(-1)
    return (positions + start) / RATE, values


@dataclass(frozen=True)
class RecordingOptions:
    directory: str
    segment_seconds: int = 600
    device_names: dict | None = None


class SessionWriter:
    def __init__(self, folder, settings=None, devices=None, segment_seconds=600):
        if type(segment_seconds) is not int or segment_seconds <= 0:
            raise ValueError("分段时长必须是正整数秒")
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=False)
        self.segment_frames = RATE * segment_seconds
        self.handles = []
        self.peaks = []
        self.peak_counts = []
        self.pending = []
        self.tail = np.empty((0, 2), np.float32)
        self.meta = {"version": 1, "id": self.folder.name, "sample_rate": RATE, "tracks": list(TRACKS),
                     "writer_pid": os.getpid(),
                     "started_at": datetime.now().astimezone().isoformat(), "ended_at": None,
                     "status": "recording", "frames": 0, "segments": [], "error": "",
                     "devices": devices or {}, "settings_events": [{"frame": 0, "settings": settings or {}, "timing": "initial"}],
                     "notes": "参数变化位置为请求时缓存样本位置，实际应用受音频进程轮询影响；processed固定延迟256样本；render_reference为AEC使用的上一回调块输出。"}
        self.closed = False
        self.flush()

    def _segment(self):
        if self.handles:
            for handle in self.handles:
                handle.close()
        index = len(self.meta["segments"])
        segment = {"start": self.meta["frames"], "frames": 0,
                   "files": [f"{index:05d}-{name}.wav" for name in TRACKS]}
        self.meta["segments"].append(segment)
        self.handles = []
        for name in segment["files"]:
            self.handles.append(sf.SoundFile(self.folder / name, mode="w", samplerate=RATE, channels=1, subtype="FLOAT"))

    def _emit(self, level, rows):
        if not len(rows):
            return
        if level == len(self.peaks):
            self.peaks.append(open(self.folder / f"peaks-{level}.bin", "wb"))
            self.pending.append(np.empty((0, 4), np.float32))
            self.peak_counts.append(0)
        self.peaks[level].write(np.asarray(rows, dtype="<f4").tobytes())
        self.peak_counts[level] += len(rows)
        merged = np.concatenate((self.pending[level], rows))
        count = len(merged) // 4 * 4
        self.pending[level] = merged[count:].copy()
        if count:
            groups = merged[:count].reshape(-1, 4, 4)
            upper = np.column_stack((groups[:, :, 0].min(1), groups[:, :, 1].max(1),
                                     groups[:, :, 2].min(1), groups[:, :, 3].max(1)))
            self._emit(level + 1, upper)

    def append(self, data):
        data = np.asarray(data, np.float32)
        if data.ndim != 2 or data.shape[1] != 4 or not np.isfinite(data).all():
            raise ValueError("会话必须包含四路有限音频样本")
        offset = 0
        while offset < len(data):
            if not self.handles or self.meta["segments"][-1]["frames"] == self.segment_frames:
                self._segment()
            segment = self.meta["segments"][-1]
            count = min(len(data) - offset, self.segment_frames - segment["frames"])
            for col, handle in enumerate(self.handles):
                handle.write(data[offset:offset + count, col])
            segment["frames"] += count
            self.meta["frames"] += count
            offset += count
        merged = np.concatenate((self.tail, data[:, :2]))
        count = len(merged) // PEAK_BLOCK * PEAK_BLOCK
        self.tail = merged[count:].copy()
        if count:
            groups = merged[:count].reshape(-1, PEAK_BLOCK, 2)
            self._emit(0, np.column_stack((groups[:, :, 0].min(1), groups[:, :, 0].max(1),
                                           groups[:, :, 1].min(1), groups[:, :, 1].max(1))))

    def event(self, frame, settings):
        self.meta["settings_events"].append({"frame": int(frame), "settings": settings, "timing": "requested"})

    def flush(self):
        for handle in self.handles:
            handle.flush()
        for handle in self.peaks:
            handle.flush()
        atomic_json(self.folder / "session.json", self.meta)

    def close(self, status="complete", error=""):
        if self.closed:
            return
        try:
            if len(self.tail):
                self._emit(0, np.array([[self.tail[:, 0].min(), self.tail[:, 0].max(),
                                         self.tail[:, 1].min(), self.tail[:, 1].max()]], np.float32))
                self.tail = self.tail[:0]
            level = 0
            while level < len(self.pending) and self.peak_counts[level] > 1:
                rows = self.pending[level]
                if len(rows):
                    self.pending[level] = rows[:0]
                    self._emit(level + 1, np.array([[rows[:, 0].min(), rows[:, 1].max(), rows[:, 2].min(), rows[:, 3].max()]], np.float32))
                level += 1
            self.meta.update(status=status, error=error, ended_at=datetime.now().astimezone().isoformat())
            self.flush()
        finally:
            for handle in (*self.handles, *self.peaks):
                handle.close()
            self.closed = True


class SessionReader:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.refresh()

    def refresh(self):
        self.meta = read_json(self.folder / "session.json")
        if not isinstance(self.meta, dict) or self.meta.get("version") != 1 or self.meta.get("sample_rate") != RATE:
            raise ValueError("不支持的会话格式")
        self.frames = int(self.meta["frames"])
        if self.frames < 0:
            raise ValueError("会话长度无效")
        try:
            cursor = 0
            for segment in self.meta["segments"]:
                if type(segment["start"]) is not int or type(segment["frames"]) is not int or segment["start"] != cursor or segment["frames"] < 0:
                    raise ValueError("会话分段索引不连续")
                if len(segment["files"]) != 4 or any(not isinstance(name, str) or Path(name).name != name for name in segment["files"]):
                    raise ValueError("会话音轨索引无效")
                cursor += segment["frames"]
            if cursor != self.frames:
                raise ValueError("会话分段长度不匹配")
        except (KeyError, TypeError) as exc:
            raise ValueError("会话索引格式无效") from exc
        return self

    def read_range(self, start, stop, columns=(0, 1)):
        start, stop = max(0, int(start)), min(self.frames, int(stop))
        if stop <= start:
            return np.empty((0, len(columns)), np.float32)
        result = np.empty((stop - start, len(columns)), np.float32)
        cursor = start
        for segment in self.meta["segments"]:
            lo, hi = segment["start"], segment["start"] + segment["frames"]
            left, right = max(start, lo), min(stop, hi)
            if right <= left:
                continue
            if left != cursor:
                raise ValueError("会话音频不连续")
            for out_col, col in enumerate(columns):
                file = (self.folder / segment["files"][col]).resolve()
                if file.parent != self.folder.resolve():
                    raise ValueError("会话文件路径无效")
                with sf.SoundFile(file) as handle:
                    handle.seek(left - lo)
                    samples = handle.read(right - left, dtype="float32")
                if len(samples) != right - left:
                    raise ValueError("会话文件尚未写完或已损坏")
                result[left - start:right - start, out_col] = samples
            cursor = right
        if cursor != stop:
            raise ValueError("会话音频不连续")
        return result

    def waveform(self, start, stop, pixels):
        start, stop = max(0, int(start)), min(int(stop), self.frames)
        if stop <= start:
            return np.empty(0), np.empty((0, 2), np.float32)
        target = (stop - start) / max(1, pixels)
        level = -1
        while PEAK_BLOCK * 4 ** (level + 1) <= target and (self.folder / f"peaks-{level + 1}.bin").exists():
            level += 1
        if level < 0:
            if stop - start > pixels * PEAK_BLOCK:
                return self._stream_waveform(start, stop, pixels)
            return reduce_peaks(self.read_range(start, stop), start, pixels)
        block = PEAK_BLOCK * 4 ** level
        file = self.folder / f"peaks-{level}.bin"
        count = file.stat().st_size // 16
        left, right = start // block, min(count, math.ceil(stop / block))
        with open(file, "rb") as handle:
            handle.seek(left * 16)
            peaks = np.frombuffer(handle.read(max(0, right - left) * 16), dtype="<f4").reshape(-1, 4)
        x = np.column_stack((np.arange(left, right) * block, np.minimum(np.arange(left, right) * block + block - 1, self.frames - 1))).reshape(-1) / RATE
        values = np.stack((peaks[:, (0, 2)], peaks[:, (1, 3)]), axis=1).reshape(-1, 2)
        # A live session may have a partial base bucket that is not yet published.
        tail_start = max(start, right * block)
        if tail_start < stop:
            tx, ty = reduce_peaks(self.read_range(tail_start, stop), tail_start, pixels)
            x, values = np.concatenate((x, tx)), np.concatenate((values, ty))
        return x, values

    def _stream_waveform(self, start, stop, pixels):
        """Missing summaries are recoverable without loading the whole session."""
        step = max(1, math.ceil((stop - start) / max(1, pixels)))
        xs, values = [], []
        for lo in range(start, stop, step):
            hi = min(stop, lo + step)
            low = np.full(2, np.inf, np.float32)
            high = np.full(2, -np.inf, np.float32)
            for position in range(lo, hi, RATE * 2):
                data = self.read_range(position, min(hi, position + RATE * 2))
                low = np.minimum(low, data.min(0))
                high = np.maximum(high, data.max(0))
            xs.extend((lo / RATE, (hi - 1) / RATE))
            values.extend((low, high))
        return np.array(xs), np.array(values, np.float32)


def recording_worker(pipe, shared, folder, options, settings, devices):
    writer = None
    try:
        writer = SessionWriter(folder, settings, devices, options.segment_seconds)
        history = History(shared=shared, reset=False)
        pipe.send({"status": "recording", "folder": str(folder), "frames": 0, "error": ""})
        cursor, ending, complete, published = 0, None, True, 0.0
        while True:
            while pipe.poll():
                message = pipe.recv()
                if message[0] == "stop":
                    ending, complete = message[1:]
                elif message[0] == "settings":
                    writer.event(message[1], message[2])
            stop = min(history.total, cursor + RATE)
            if ending is not None:
                stop = min(stop, ending)
            if stop > cursor:
                try:
                    data = history.read_range(cursor, stop)
                except RuntimeError:
                    time.sleep(0.005)
                    continue
                writer.append(data)
                cursor = stop
            if ending is not None and cursor >= ending:
                writer.close("complete" if complete else "interrupted", "" if complete else "音频进程异常结束")
                pipe.send({"status": writer.meta["status"], "folder": str(folder), "frames": cursor, "error": writer.meta["error"]})
                return
            if time.monotonic() - published >= 0.25:
                writer.flush()
                pipe.send({"status": "recording", "folder": str(folder), "frames": cursor, "error": ""})
                published = time.monotonic()
            time.sleep(0.02)
    except (EOFError, BrokenPipeError):
        if writer:
            writer.close("interrupted", "主程序连接中断")
    except Exception as exc:
        if writer:
            try:
                writer.close("error", str(exc))
            except Exception:
                pass
        try:
            pipe.send({"status": "error", "folder": str(folder), "frames": writer.meta["frames"] if writer else 0, "error": str(exc)})
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        pipe.close()


class RecorderClient:
    def __init__(self, context=None):
        self.context = context or mp.get_context("spawn")
        self.process = self.pipe = None
        self.status = {"status": "off", "folder": "", "frames": 0, "error": ""}

    def start(self, shared, options, settings, devices):
        folder = Path(options.directory) / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        parent, child = self.context.Pipe()
        self.pipe = parent
        self.process = self.context.Process(target=recording_worker, args=(child, shared, folder, options, settings, devices), daemon=True, name="DSP-Recorder")
        self.process.start()
        child.close()
        if parent.poll(10):
            self.status = parent.recv()
        else:
            self.status = {"status": "error", "folder": str(folder), "frames": 0, "error": "录音进程准备超时"}
            self.stop(0, False)

    def poll(self):
        if self.pipe:
            try:
                while self.pipe.poll():
                    self.status = self.pipe.recv()
            except (EOFError, OSError):
                pass
        if self.process is not None and not self.process.is_alive() and self.status["status"] == "recording":
            self.status.update(status="interrupted", error="录音进程异常退出")

    def event(self, frame, settings):
        if self.pipe and self.status["status"] == "recording":
            try:
                self.pipe.send(("settings", frame, settings))
            except (BrokenPipeError, OSError):
                pass

    def stop(self, frames, complete=True):
        if self.process is None:
            return
        self.poll()
        if self.process.is_alive():
            try:
                self.pipe.send(("stop", int(frames), complete))
            except (BrokenPipeError, OSError):
                pass
            # Drain status messages while waiting, so the writer cannot block on IPC.
            deadline = time.monotonic() + 10
            while self.process.is_alive() and time.monotonic() < deadline:
                self.poll()
                self.process.join(0.025)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join()
                self.status.update(status="interrupted", error="录音收尾超时，文件标记为不完整")
        self.poll()
        if self.status["status"] in ("interrupted", "error"):
            path = Path(self.status["folder"]) / "session.json"
            try:
                values = json.loads(path.read_text(encoding="utf-8"))
                values.update(status=self.status["status"], error=self.status["error"])
                atomic_json(path, values)
            except (OSError, ValueError):
                pass
        self.process.close()
        self.process = None
        self.pipe.close()
        self.pipe = None


def list_sessions(directory):
    result = []
    for path in sorted(Path(directory).glob("*/session.json"), reverse=True):
        try:
            reader = SessionReader(path.parent)
            if reader.meta["status"] == "recording" and not process_alive(reader.meta.get("writer_pid", 0)):
                reader.meta.update(status="interrupted", error="上次录音未正常收尾，保留已提交的样本")
                try:
                    atomic_json(path, reader.meta)
                except OSError:
                    pass
            result.append((path.parent, reader.meta))
        except (OSError, ValueError, KeyError):
            continue
    return result


def process_alive(pid):
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes as ct
        from ctypes import wintypes as wt
        kernel = ct.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wt.HANDLE, ct.POINTER(wt.DWORD)]
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied does not establish that a process has exited.
            return ct.get_last_error() == 5
        try:
            code = wt.DWORD()
            return bool(kernel.GetExitCodeProcess(handle, ct.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
