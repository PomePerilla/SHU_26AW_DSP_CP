"""Two-minute real-device/session integration; all recordings are temporary.

Live and historical output stay silent. This tests routing and I/O continuity,
not listening quality, room AEC effectiveness, or real-member identification.
"""
from pathlib import Path
import ctypes as ct
import json
import os
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def private_bytes(pid):
    from ctypes import wintypes as wt
    class Counters(ct.Structure):
        _fields_ = [("cb", wt.DWORD), ("faults", wt.DWORD)] + [(name, ct.c_size_t) for name in ("peak", "working", "peak_paged", "paged", "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    psapi = ct.WinDLL("psapi")
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ct.POINTER(Counters), wt.DWORD]
    handle = kernel.OpenProcess(0x410, False, pid)
    if not handle:
        return None
    try:
        values = Counters()
        values.cb = ct.sizeof(values)
        return values.private if psapi.GetProcessMemoryInfo(handle, ct.byref(values), ct.sizeof(values)) else None
    finally:
        kernel.CloseHandle(handle)


def main():
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    import numpy as np
    from dsp_lab.ui import MainWindow
    from dsp_lab.audio import load_wave
    from dsp_lab.sessions import SessionReader
    from dsp_lab.core import RATE

    app = QApplication([])
    app.setStyle("Fusion")
    temporary = tempfile.TemporaryDirectory(prefix="dsp-hardware-recording-")
    window = MainWindow(ROOT, test_mode=True)
    window.preferences.auto_record = True
    window.auto_record.setChecked(True)
    window.preferences.recording_directory = temporary.name
    window.record_directory.setText(temporary.name)
    window.showMaximized()
    began = None
    deadline = time.monotonic() + 170
    count = 0
    next_job = 0
    samples, memory, phases = [], [], []
    fixture = load_wave(ROOT / "validation/fixtures/fangjun-test-sr-1.wav")
    result = {}

    def finish(error=""):
        timer.stop()
        nonlocal result
        try:
            window.engine.poll()
            stats = dict(window.engine.stats)
            elapsed = time.monotonic() - began if began else 0
            if window.engine.stream is not None:
                window._start_stop()
            frames = window.engine.history.total
            rec = dict(window.engine.recording_status)
            if error:
                raise RuntimeError(error)
            assert rec["status"] == "complete", rec
            reader = SessionReader(rec["folder"])
            assert reader.frames == frames
            tail = window.engine.history.snapshot(30)
            np.testing.assert_array_equal(reader.read_range(frames - len(tail), frames, (0, 1, 2, 3)), tail)
            window._load_session(rec["folder"])
            # Hardware output is opened at gain zero for both tracks.
            window.player.play(rec["folder"], window.output_combo.currentData(), 0)
            until = time.monotonic() + 1.5
            while time.monotonic() < until:
                app.processEvents()
                window._tick()
                time.sleep(0.01)
            assert window.player.position > 0, window.player.error
            window.player.pause()
            window.player.seek(frames // 2, 0)
            window.player.play(rec["folder"], window.output_combo.currentData(), 0)
            until = time.monotonic() + 1.5
            while time.monotonic() < until:
                app.processEvents()
                window._tick()
                time.sleep(0.01)
            assert window.player.position > frames // 2, window.player.error
            assert not window.engine.running
            playback_stats = {"position_frames": window.player.position, "underruns": window.player.underruns, "error": window.player.error, "gain": 0}
            result = {"passed": elapsed >= 120 and stats.get("xruns") == 0 and stats.get("deadline_misses") == 0 and not playback_stats["error"],
                      "duration_seconds": elapsed, "frames": frames, "written_frames": reader.frames,
                      "recording_status": rec["status"], "four_track_tail_exact": True,
                      "embedding_jobs": count, "stats": stats, "playback": playback_stats,
                      "peak_observed_processing_ms": max(samples) if samples else None,
                      "memory_samples": memory, "phases": phases,
                      "recording_files_temporary": True, "captured_audio_retained": False,
                      "scope": "real microphone, silent live output, complete session recording, UI + public-clip embeddings; silent historical output/seek/track switch"}
        except Exception as exc:
            result = {"passed": False, "error": str(exc), "phases": phases, "memory_samples": memory,
                      "recording_files_temporary": True, "captured_audio_retained": False}
        finally:
            window.close()
            app.processEvents()
            temporary.cleanup()
            result["temporary_recordings_removed"] = not Path(temporary.name).exists()
            folder = ROOT / "validation/long-session"
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "hardware-120s.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
            app.quit()

    def tick():
        nonlocal began, count, next_job
        try:
            now = time.monotonic()
            if now > deadline:
                finish("完整会话联测超时")
                return
            if began is None:
                if not window.prepared:
                    return
                window._start_stop()
                if not window.engine.running:
                    finish("音频未能启动")
                    return
                window.aec.setChecked(True)
                window.echo.setChecked(True)
                window.inverse_echo.setChecked(True)
                began = time.monotonic()
                print("Live recording started; temporary audio, silent output", flush=True)
                return
            elapsed = now - began
            window.engine.poll()
            samples.append(window.engine.stats.get("processing_ms", 0))
            if not window.engine.running:
                finish("实时音频意外停止")
                return
            if window.engine.recording_status["status"] != "recording":
                finish(str(window.engine.recording_status))
                return
            if elapsed >= 25 and "browse" not in phases:
                window.follow.setChecked(False)
                window._set_wave_range(0, 10)
                phases.append("browse")
            if elapsed >= 40 and "entire" not in phases:
                assert window.engine.recording_status["frames"] > RATE * 35
                window._show_entire()
                phases.append("entire")
            if elapsed >= 60 and "follow" not in phases:
                window.span_combo.setCurrentIndex(4)  # 60 s
                window.follow.setChecked(True)
                phases.append("follow")
                print("60 seconds: recording continued during browsing", flush=True)
            if elapsed >= (len(memory) + 1) * 20:
                memory.append({"seconds": elapsed, "gui": private_bytes(os.getpid()),
                               "audio": private_bytes(window.engine.process.pid),
                               "recorder": private_bytes(window.engine.recorder.process.pid)})
            if now >= next_job and window.future is None:
                window.future = window.pool.submit(window.service.embedding, fixture)
                window.job = "stress"
                count += 1
                next_job = now + 2
            if elapsed >= 120:
                finish()
        except Exception as exc:
            finish(str(exc))

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()
    if not result.get("passed"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
