"""Independent playback process with bounded, asynchronous disk prefetch."""
from collections import deque
import multiprocessing as mp
import threading
import time
import numpy as np
import sounddevice as sd
from .core import RATE, HOP
from .sessions import SessionReader


class PlaybackBuffer:
    """One decoder producer, one audio consumer; callbacks never read files."""
    def __init__(self, reader):
        self.reader = reader
        self.blocks = deque()
        self.generation = 0
        self.position = 0
        self.track = 1
        self.decode_cursor = 0
        self.playing = False
        self.gain = 0.6
        self.underruns = 0
        self.error = ""
        self.finished = False
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._prefetch, daemon=True, name="session-prefetch")
        self.thread.start()

    def seek(self, frame, track=None):
        with self.lock:
            self.generation += 1
            self.position = max(0, min(int(frame), self.reader.frames))
            if track is not None:
                self.track = int(track)
            self.decode_cursor = self.position
            self.blocks.clear()
            self.finished = False

    def _prefetch(self):
        while not self.closed.is_set():
            if len(self.blocks) >= 64 or self.decode_cursor >= self.reader.frames:
                self.closed.wait(0.005)
                continue
            with self.lock:
                generation, cursor, track = self.generation, self.decode_cursor, self.track
            try:
                data = self.reader.read_range(cursor, min(cursor + HOP * 32, self.reader.frames), (track,))[:, 0]
                with self.lock:
                    if generation != self.generation:
                        continue
                    for offset in range(0, len(data), HOP):
                        self.blocks.append((generation, cursor + offset, data[offset:offset + HOP].copy()))
                    self.decode_cursor = cursor + len(data)
            except Exception as exc:
                self.error = str(exc)
                return

    def render(self, output):
        output.fill(0)
        if not self.playing:
            return
        if not self.lock.acquire(blocking=False):
            self.underruns += 1
            return
        try:
            self._render_ready(output)
        finally:
            self.lock.release()

    def _render_ready(self, output):
        while self.blocks:
            generation, start, data = self.blocks.popleft()
            if generation != self.generation or start != self.position:
                continue
            output[:len(data), 0] = np.clip(data * self.gain, -0.98, 0.98)
            self.position += len(data)
            if self.position >= self.reader.frames:
                self.finished = True
                self.playing = False
            return
        if self.position < self.reader.frames:
            self.underruns += 1

    def close(self):
        self.closed.set()
        self.thread.join(timeout=3)


def playback_worker(pipe, folder, device, position, track, gain):
    buffer = stream = None
    try:
        buffer = PlaybackBuffer(SessionReader(folder))
        buffer.seek(position, track)
        buffer.gain = gain
        def callback(output, frames, timing, status):
            if frames != HOP:
                output.fill(0)
                raise sd.CallbackAbort
            buffer.render(output)
        stream = sd.OutputStream(samplerate=RATE, blocksize=HOP, channels=1, dtype="float32", device=device, callback=callback, latency="low")
        stream.start()
        pipe.send({"kind": "ready"})
        published = 0.0
        revision = 0
        while True:
            if pipe.poll(0.02):
                message = pipe.recv()
                if message[0] == "stop":
                    break
                if message[0] == "playing":
                    buffer.playing = message[1]
                    revision = message[2]
                elif message[0] == "seek":
                    buffer.seek(message[1], message[2])
                    revision = message[3]
                elif message[0] == "gain":
                    buffer.gain = message[1]
            if buffer.error:
                raise RuntimeError(buffer.error)
            if time.monotonic() - published >= 0.1:
                pipe.send({"kind": "status", "position": buffer.position, "playing": buffer.playing,
                           "finished": buffer.finished, "underruns": buffer.underruns, "revision": revision})
                published = time.monotonic()
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            pipe.send({"kind": "error", "error": str(exc)})
        except (EOFError, OSError):
            pass
    finally:
        if stream:
            stream.stop()
            stream.close()
        if buffer:
            buffer.close()
        pipe.close()


class PlaybackClient:
    def __init__(self):
        self.context = mp.get_context("spawn")
        self.process = self.pipe = None
        self.position = 0
        self.track = 1
        self.playing = False
        self.finished = False
        self.error = ""
        self.underruns = 0
        self.revision = 0

    def _send(self, message):
        if self.pipe and self.process and self.process.is_alive():
            try:
                self.pipe.send(message)
            except OSError as exc:
                self.error = str(exc)

    def play(self, folder, device, gain):
        if self.process is not None and not self.process.is_alive():
            self.stop()
        if self.process is None:
            parent, child = self.context.Pipe()
            self.pipe = parent
            self.error = ""
            self.revision = 0
            self.process = self.context.Process(target=playback_worker, args=(child, str(folder), device, self.position, self.track, gain), daemon=True, name="DSP-Playback")
            self.process.start()
            child.close()
            if not parent.poll(10):
                self.stop()
                raise RuntimeError("历史播放启动超时")
            message = parent.recv()
            if message["kind"] == "error":
                self.stop()
                raise RuntimeError(message["error"])
        self.playing = True
        self.finished = False
        self.revision += 1
        self._send(("playing", True, self.revision))

    def pause(self):
        self.playing = False
        self.revision += 1
        self._send(("playing", False, self.revision))

    def seek(self, frame, track=None):
        self.position = int(frame)
        if track is not None:
            self.track = track
        self.finished = False
        self.revision += 1
        self._send(("seek", self.position, self.track, self.revision))

    def set_gain(self, gain):
        self._send(("gain", gain))

    def poll(self):
        if self.pipe:
            try:
                while self.pipe.poll():
                    message = self.pipe.recv()
                    if message["kind"] == "error":
                        self.error = message["error"]
                        self.playing = False
                    elif message["kind"] == "status" and message["revision"] >= self.revision:
                        self.position, self.playing = message["position"], message["playing"]
                        self.finished, self.underruns = message["finished"], message["underruns"]
            except (EOFError, OSError):
                pass
        if self.process is not None and not self.process.is_alive():
            self.playing = False
            self.error = self.error or "播放进程已退出"

    def stop(self):
        if self.process:
            self._send(("stop",))
            deadline = time.monotonic() + 4
            while self.process.is_alive() and time.monotonic() < deadline:
                self.poll()
                self.process.join(0.025)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join()
            self.process.close()
            self.process = None
            self.pipe.close()
            self.pipe = None
        self.playing = False
