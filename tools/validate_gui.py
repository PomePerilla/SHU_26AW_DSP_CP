"""Live GUI + isolated audio + embedding stress check. No captured audio saved."""
from pathlib import Path
from datetime import datetime, timezone, timedelta
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    import json
    import os
    import time
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from dsp_lab.ui import MainWindow
    from dsp_lab.audio import load_wave

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    window = MainWindow(ROOT, test_mode=True)
    window.showMaximized()
    began = None
    samples = []
    embedding_jobs = 0
    next_embedding = 0
    timed_out = time.monotonic() + 50
    fixture = load_wave(ROOT / "validation" / "fixtures" / "fangjun-test-sr-1.wav")

    def finish(error=""):
        window.engine.poll()
        result = {
            "date": datetime.now(timezone(timedelta(hours=8))).date().isoformat(), "gui_pid": os.getpid(),
            "audio_pid": window.engine.process.pid if window.engine.process else None,
            "duration_seconds": time.monotonic() - began if began else 0,
            "model_embedding_jobs_submitted": embedding_jobs,
            "stats": window.engine.stats, "error": error or window.engine.error,
            "audio_samples_seen": window.engine.history.total,
            "peak_observed_processing_ms": max(samples) if samples else None,
            "scope": "live Qt plots, background public-clip embeddings, actual microphone, silent output",
            "raw_audio_saved": False, "monitor_or_reference_played": False,
        }
        result["passed"] = bool(
            not result["error"] and result["duration_seconds"] >= 25 and
            result["audio_samples_seen"] >= 24 * 16000 and
            result["stats"].get("deadline_misses", 1) == 0 and
            result["stats"].get("xruns", 1) == 0 and embedding_jobs >= 5
        )
        (ROOT / "validation" / "gui-live.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        timer.stop()
        window.close()
        app.quit()

    def tick():
        nonlocal began, embedding_jobs, next_embedding
        now = time.monotonic()
        if now > timed_out:
            finish("GUI validation timed out")
            return
        if began is None:
            if not window.prepared:
                return
            try:
                window._start_stop()
                if not window.engine.running:
                    finish("Audio process did not start")
                    return
                window.aec.setChecked(True)
                window.echo.setChecked(True)
                window.inverse_echo.setChecked(True)
                window._learn_noise()
                began = time.monotonic()
            except Exception as exc:
                finish(str(exc))
            return
        window.engine.poll()
        samples.append(window.engine.stats.get("processing_ms", 0))
        if not window.engine.running:
            finish("Audio process stopped during GUI validation")
            return
        if now >= next_embedding and window.future is None:
            window.future = window.pool.submit(window.service.embedding, fixture)
            window.job = "stress"
            embedding_jobs += 1
            next_embedding = now + 2
        if now - began >= 25:
            finish()

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    app.exec()


if __name__ == "__main__":
    main()
