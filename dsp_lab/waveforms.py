"""Asynchronous, generation-bound views over a live ring and a session archive."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import numpy as np
from .core import RATE
from .sessions import SessionReader, reduce_peaks


def read_view(history, folder, start, stop, pixels, spectrum_at):
    reader = SessionReader(folder) if folder and (Path(folder) / "session.json").exists() else None
    total = history.total if history is not None else reader.frames
    start, stop = max(0, int(start)), min(total, int(stop))
    xs, ys = [], []
    earliest = max(0, total - history.capacity) if history is not None else total
    if history is not None and start >= earliest:
        x, values = reduce_peaks(history.read_range(start, max(start, stop)), start, pixels)
        xs.append(x)
        ys.append(values)
    else:
        persisted = min(stop, reader.frames) if reader else 0
        if reader and persisted > start:
            x, values = reader.waveform(start, persisted, pixels)
            xs.append(x)
            ys.append(values)
        if history is not None and stop > max(start, persisted, earliest):
            tail = max(start, persisted, earliest)
            if xs and persisted < earliest:
                xs.append(np.array([np.nan]))
                ys.append(np.full((1, 2), np.nan, np.float32))
            x, values = reduce_peaks(history.read_range(tail, stop), tail, pixels)
            xs.append(x)
            ys.append(values)
    x = np.concatenate(xs) if xs else np.empty(0)
    values = np.concatenate(ys) if ys else np.empty((0, 2), np.float32)
    end = min(total, max(0, int(spectrum_at)))
    if history is None and end < 2048:
        end = min(total, 2048)
    begin = max(0, end - 2048)
    if history is not None and begin >= earliest:
        samples = history.read_range(begin, end)[:, :2]
    elif reader:
        end = min(end, reader.frames)
        samples = reader.read_range(max(0, end - 2048), end)
    else:
        samples = np.empty((0, 2), np.float32)
    if len(samples):
        window = np.hanning(len(samples))
        amplitude = 2 * np.abs(np.fft.rfft(samples * window[:, None], axis=0)) / max(1, window.sum())
        frequency = np.fft.rfftfreq(len(samples), 1 / RATE)
        spectrum = 20 * np.log10(np.maximum(amplitude, 1e-6))
    else:
        frequency, spectrum = np.empty(0), np.empty((0, 2))
    return x, values, frequency, spectrum


class WaveformBrowser:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="waveform-reader")
        self.future = None
        self.key = None
        self.future_key = None
        self.pending = None
        self.error = ""

    def request(self, key, *args):
        self.key = key
        if self.future is None:
            self.future_key = key
            self.future = self.pool.submit(read_view, *args)
        else:
            self.pending = (key, args)

    def poll(self):
        result = None
        if self.future is not None and self.future.done():
            try:
                value = self.future.result()
                if self.future_key == self.key:
                    result = value
                    self.error = ""
            except (RuntimeError, ValueError, OSError) as exc:
                # Live ring updates / file publication races are retried next tick.
                if self.future_key == self.key:
                    self.error = str(exc)
            self.future = None
            if self.pending:
                key, args = self.pending
                self.pending = None
                self.request(key, *args)
        return result

    def invalidate(self):
        self.key = None
        self.pending = None
        self.error = ""

    def close(self):
        self.invalidate()
        self.pool.shutdown(wait=True, cancel_futures=True)
