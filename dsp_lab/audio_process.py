"""Keep Qt rendering and model inference out of the audio interpreter/GIL.

Only a small control/status pipe crosses processes. Audio callback data is written
to a bounded shared-memory ring, with a single-writer sequence counter.
"""
import multiprocessing as mp
from dataclasses import asdict
import time
from types import SimpleNamespace
import numpy as np
from .audio import AudioEngine, History
from .core import RATE, Settings
from .sessions import RecorderClient


def audio_worker(connection, shared, input_device, output_device, settings, track, enabled):
    engine = AudioEngine(shared_history=shared)
    engine.settings = settings
    engine.set_reference(track)
    engine.reference_enabled = enabled
    try:
        engine.start(input_device, output_device)
        connection.send({"kind": "ready", "latency": engine.stream.latency})
        stop = False
        published = 0.0
        while not stop:
            if connection.poll(0.025):
                message = connection.recv()
                command = message[0]
                if command == "stop":
                    stop = True
                elif command == "settings":
                    engine.settings = message[1]
                elif command == "reference":
                    engine.set_reference(message[1])
                elif command == "play":
                    engine.reference_enabled = message[1]
            now = time.monotonic()
            if now - published > 0.1:
                connection.send({"kind": "status", "stats": engine.stats,
                                 "error": engine.error, "running": bool(engine.running)})
                published = now
    except (EOFError, BrokenPipeError):
        pass
    except Exception as exc:
        try:
            connection.send({"kind": "error", "error": str(exc)})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        engine.stop()
        try:
            connection.send({"kind": "final", "error": engine.error})
        except (EOFError, BrokenPipeError, OSError):
            pass
        connection.close()


class AudioClient:
    def __init__(self):
        self.context = mp.get_context("spawn")
        self._settings = Settings()
        self._enabled = False
        self.reference_audio = np.zeros(0, dtype=np.float32)
        self.history = History()
        self.process = None
        self.connection = None
        self.stream = None
        self.stats = {}
        self.error = ""
        self.started_at = 0.0
        self.recorder = RecorderClient(self.context)

    @property
    def recording_status(self):
        return self.recorder.status

    @property
    def settings(self):
        return self._settings

    @settings.setter
    def settings(self, settings):
        self._settings = settings
        self._send(("settings", settings))
        self.recorder.event(self.history.total, asdict(settings))

    @property
    def reference_enabled(self):
        return self._enabled

    @reference_enabled.setter
    def reference_enabled(self, enabled):
        self._enabled = bool(enabled)
        self._send(("play", self._enabled))

    @property
    def running(self):
        return bool(self.stream is not None and self.stream.active and
                    self.process is not None and self.process.is_alive())

    def _send(self, message):
        if self.connection is not None and self.process is not None and self.process.is_alive():
            try:
                self.connection.send(message)
            except (EOFError, BrokenPipeError, OSError) as exc:
                self.error = f"音频进程连接中断：{exc}"

    def start(self, input_device=None, output_device=None, recording=None):
        if self.running:
            return
        enabled = self._enabled
        self.stop()
        self._enabled = enabled
        shared = (self.context.RawArray("f", RATE * 30 * 4), self.context.RawArray("q", 3))
        self.history = History(shared=shared)
        parent, child = self.context.Pipe()
        self.connection = parent
        self.error = ""
        self.stats = {}
        self.recorder = RecorderClient(self.context)
        if recording is not None:
            try:
                devices = {"input": input_device, "output": output_device, **(recording.device_names or {})}
                self.recorder.start(shared, recording, asdict(self.settings), devices)
            except Exception as exc:
                self.recorder.status.update(status="error", error=str(exc))
        self.process = self.context.Process(
            target=audio_worker,
            args=(child, shared, input_device, output_device, self.settings,
                  self.reference_audio, self._enabled),
            daemon=True, name="DSP-Audio",
        )
        self.process.start()
        child.close()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if parent.poll(0.05):
                message = parent.recv()
                if message["kind"] == "ready":
                    self.stream = SimpleNamespace(active=True, latency=message["latency"])
                    self.started_at = time.perf_counter()
                    return
                if message["kind"] == "error":
                    self.stop()
                    raise RuntimeError(message["error"])
            if not self.process.is_alive():
                self.stop()
                raise RuntimeError("音频进程未能启动，请检查所选设备")
        self.stop()
        raise TimeoutError("音频设备启动超时")

    def poll(self):
        self.recorder.poll()
        if self.connection is None:
            return
        try:
            while self.connection.poll():
                message = self.connection.recv()
                if message["kind"] == "status":
                    self.stats = message["stats"]
                    self.error = message["error"]
                    if self.stream is not None:
                        self.stream.active = message["running"]
                elif message["kind"] == "error":
                    self.error = message["error"]
                    if self.stream is not None:
                        self.stream.active = False
        except (EOFError, BrokenPipeError, OSError):
            if self.stream is not None:
                self.stream.active = False
        if self.process is not None and not self.process.is_alive() and self.stream is not None:
            self.stream.active = False
            self.error = self.error or "音频进程已退出"

    def stop(self):
        self._send(("stop",))
        complete = not self.error
        if self.process is not None:
            self.process.join(timeout=3)
            if self.process.is_alive():
                complete = False
                self.process.terminate()
                self.process.join(timeout=1)
            complete = complete and self.process.exitcode == 0
            if self.connection:
                try:
                    while self.connection.poll():
                        message = self.connection.recv()
                        if message.get("kind") in ("final", "error") and message.get("error"):
                            self.error = message["error"]
                            complete = False
                except (EOFError, OSError):
                    pass
            self.process.close()
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        self.stream = None
        self._enabled = False
        self.recorder.stop(self.history.total, complete)

    def set_reference(self, samples):
        self.reference_audio = np.ascontiguousarray(samples, dtype=np.float32)
        self._send(("reference", self.reference_audio))

    def export(self, folder):
        return AudioEngine.export(self, folder)
