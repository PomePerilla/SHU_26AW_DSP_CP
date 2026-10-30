"""Evidence with separate synthetic, public-speech and hardware boundaries."""
import argparse
import json
import platform
from pathlib import Path
import sys
import tempfile
import time
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from dsp_lab.core import RATE, HOP, AdaptiveEchoCanceller, EchoEffect, Settings, warm_up
from dsp_lab.audio import AudioEngine, load_wave
from dsp_lab.speaker import SpeakerService


def benchmark():
    warm_up()
    rng = np.random.default_rng(20261028)
    engine = AudioEngine()
    engine.settings = Settings(aec=True, echo=True, inverse_echo=True, learn_token=1)
    engine.set_reference(rng.normal(0, 0.1, RATE * 4).astype(np.float32))
    engine.reference_enabled = True
    output = np.zeros((HOP, 1), dtype=np.float32)
    samples = rng.normal(0, 0.05, (1200, HOP, 1)).astype(np.float32)
    timings = []
    for block in samples:
        start = time.perf_counter()
        engine._callback(block, output, HOP, None, False)
        timings.append((time.perf_counter() - start) * 1000)
    times = np.asarray(timings)
    return {
        "kind": "synthetic callback loop, not paced by audio hardware",
        "blocks": len(times), "simulated_audio_seconds": len(times) * HOP / RATE,
        "block_budget_ms": HOP / RATE * 1000, "mean_ms": float(np.mean(times)),
        "p95_ms": float(np.percentile(times, 95)), "p99_ms": float(np.percentile(times, 99)),
        "max_ms": float(np.max(times)), "over_budget_blocks": int(np.sum(times > 16)),
        "all_modules_enabled": True,
    }


def signal_metrics():
    from scipy.signal import lfilter
    rng = np.random.default_rng(11)
    ref = rng.normal(0, 0.12, HOP * 800).astype(np.float32)
    h = np.zeros(1400)
    h[110], h[380], h[990] = 0.65, 0.2, -0.12
    echoed = lfilter(h, [1], ref).astype(np.float32)
    aec = AdaptiveEchoCanceller()
    residual = np.concatenate([aec.process(mic, far)[0] for mic, far in
                               zip(echoed.reshape(-1, HOP), ref.reshape(-1, HOP))])
    erle = 10 * np.log10(np.mean(echoed[-RATE * 2:] ** 2) / max(1e-15, np.mean(residual[-RATE * 2:] ** 2)))
    effect, inverse = EchoEffect(), EchoEffect(inverse=True)
    recovered = np.concatenate([inverse.process(effect.process(block)) for block in ref.reshape(-1, HOP)])
    return {"source": "seeded Gaussian signal and linear synthetic echo path",
            "aec_erle_db_last_2s": float(erle), "near_end_speech_present": False,
            "digital_inverse_max_abs_error": float(np.max(np.abs(recovered - ref))),
            "not_physical_room_acceptance": True}


def public_speaker_check():
    fixture = ROOT / "validation" / "fixtures"
    names = ["fangjun-test-sr-1.wav", "fangjun-test-sr-2.wav", "leijun-test-sr-1.wav", "leijun-test-sr-2.wav", "liudehua-test-sr-1.wav"]
    if not all((fixture / name).is_file() for name in names):
        return {"executed": False, "reason": "Run tools/download_models.py --fixtures first"}
    with tempfile.TemporaryDirectory(prefix="dsp-speaker-validation-") as temp:
        service = SpeakerService(ROOT)
        service.profiles = {}
        service.profile_path = Path(temp) / "profiles.json"
        info = service.load()
        source = load_wave(fixture / names[0])
        start = time.perf_counter()
        service.embedding(source)
        inference = time.perf_counter() - start
        service.enroll("公开样本A", source)
        service.enroll("公开样本B", load_wave(fixture / names[2]))
        checks = []
        for file, expected in ((names[1], "公开样本A"), (names[3], "公开样本B"), (names[4], "未知说话人")):
            samples = load_wave(fixture / file)
            result = service.identify(samples)
            checks.append({"file": file, "expected": expected, "result": result,
                           "passed": result["name"] == expected})
        return {"executed": True, "model": info, "source": "public upstream recordings, not team members",
                "registration_and_test_files_disjoint": True,
                "example_embedding_seconds": inference, "example_audio_seconds": len(source) / RATE,
                "checks": checks, "passed": all(check["passed"] for check in checks)}


def hardware_check():
    import sounddevice as sd
    engine = AudioEngine()
    try:
        inp, out = sd.default.device
        devices = {"input": dict(sd.query_devices(inp)), "output": dict(sd.query_devices(out))}
        engine.settings = Settings(aec=True, echo=True, inverse_echo=True, monitor=False)
        engine.start(inp, out)
        sd.sleep(3000)
        stats = dict(engine.stats)
        result = {
            "executed": True, "duration_seconds": time.perf_counter() - engine.started_at,
            "devices": devices, "sample_count": engine.history.total, "stats": stats,
            "error": engine.error, "stream_was_active": bool(engine.running),
            "reported_io_latency_seconds": engine.stream.latency,
            "output": "silence; no reference playback or monitoring",
            "microphone_audio_saved": False,
            "scope": "transport/processing short run only; not speech quality or real room AEC",
        }
        return result
    except Exception as exc:
        return {"executed": False, "error": str(exc), "scope": "hardware short-run attempt"}
    finally:
        engine.stop()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hardware", action="store_true")
    args = parser.parse_args()
    results = {"date": "2026-10-28", "platform": platform.platform(), "python": sys.version,
               "callback_benchmark": benchmark(), "synthetic_signals": signal_metrics()}
    try:
        results["public_speaker"] = public_speaker_check()
    except Exception as exc:
        results["public_speaker"] = {"executed": True, "passed": False, "error": str(exc)}
    results["hardware"] = hardware_check() if args.hardware else {"executed": False, "reason": "not requested by this invocation"}
    results["not_executed"] = ["real project-member identification", "real-room echo attenuation and double-talk", "10-minute live endurance", "acoustic loopback end-to-end latency"]
    target = ROOT / "validation" / "results.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
