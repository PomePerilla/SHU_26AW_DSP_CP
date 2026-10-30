import json
import multiprocessing as mp
from pathlib import Path
import time
import threading
import numpy as np
import pytest
from dsp_lab.audio import History, HistoryOverrun
from dsp_lab.core import RATE, HOP
from dsp_lab.sessions import SessionWriter, SessionReader, RecorderClient, RecordingOptions, reduce_peaks, atomic_json, read_json
from dsp_lab.playback import PlaybackBuffer
from dsp_lab.waveforms import read_view, WaveformBrowser
from dsp_lab.preferences import Preferences


def signal(start, stop):
    t = np.arange(start, stop, dtype=np.float32)
    raw = (t % 10000) / 20000
    return np.column_stack((raw, -raw, raw * 0.5, raw * 0.25))


def wait_until(predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition timed out")


def test_absolute_ring_reads_attach_without_reset():
    context = mp.get_context("spawn")
    shared = (context.RawArray("f", RATE * 4), context.RawArray("q", 3))
    history = History(seconds=1, shared=shared)
    for start in range(0, RATE * 2, HOP):
        data = signal(start, start + HOP)
        history.append(*data.T)
    attached = History(seconds=1, shared=shared, reset=False)
    assert attached.total == history.total
    np.testing.assert_array_equal(attached.read_range(history.total - 500, history.total), signal(history.total - 500, history.total))
    with pytest.raises(HistoryOverrun):
        attached.read_range(0, HOP)
    with pytest.raises(ValueError):
        attached.read_range(history.total, history.total + 1)


def test_segmented_full_session_preserves_samples_and_extrema(tmp_path):
    folder = tmp_path / "long"
    writer = SessionWriter(folder)
    end = RATE * 600 + 37
    for start in range(0, end, RATE * 7):
        writer.append(signal(start, min(end, start + RATE * 7)))
    writer.event(RATE * 300, {"echo": True})
    writer.close()
    reader = SessionReader(folder)
    assert reader.frames == end and len(reader.meta["segments"]) == 2
    assert reader.meta["segments"][-1]["frames"] == 37
    start = RATE * 600 - 127
    np.testing.assert_array_equal(reader.read_range(start, end, (0, 1, 2, 3)), signal(start, end))
    x, y = reader.waveform(0, end, 200)
    assert len(x) <= 200 * 8 + 4
    assert np.max(y[:, 0]) == np.float32(9999 / 20000)
    assert reader.meta["status"] == "complete"
    assert reader.meta["settings_events"][-1]["frame"] == RATE * 300
    assert max(len(pending) for pending in writer.pending) < 4


def test_peak_reduction_does_not_skip_a_transient():
    data = np.zeros((RATE * 5, 4), np.float32)
    data[157, 0] = 0.9
    data[1001, 1] = -0.8
    x, y = reduce_peaks(data, 200, 100)
    assert y[:, 0].max() == np.float32(0.9)
    assert y[:, 1].min() == np.float32(-0.8)
    assert len(x) <= 200


def test_recorder_process_and_final_partial_block(tmp_path):
    context = mp.get_context("spawn")
    shared = (context.RawArray("f", RATE * 30 * 4), context.RawArray("q", 3))
    history = History(shared=shared)
    client = RecorderClient(context)
    client.start(shared, RecordingOptions(str(tmp_path), segment_seconds=1), {}, {})
    try:
        for start in range(0, RATE * 35, RATE):
            history.append(*signal(start, start + RATE).T)
            if start % (RATE * 10) == 0:
                target = history.total
                def caught_up():
                    client.poll()
                    return client.status["frames"] >= target
                wait_until(caught_up)
        history.append(*signal(RATE * 35, RATE * 35 + 37).T)
        client.event(RATE, {"enhance": False})
    finally:
        client.stop(history.total)
    assert client.status["status"] == "complete"
    reader = SessionReader(client.status["folder"])
    assert reader.frames == RATE * 35 + 37
    np.testing.assert_array_equal(reader.read_range(RATE * 34, reader.frames, (0, 1, 2, 3)), signal(RATE * 34, reader.frames))


def test_recorder_reports_disk_failure_and_overrun(tmp_path):
    context = mp.get_context("spawn")
    shared = (context.RawArray("f", RATE * 30 * 4), context.RawArray("q", 3))
    history = History(shared=shared)
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    client = RecorderClient(context)
    client.start(shared, RecordingOptions(str(blocked)), {}, {})
    client.stop(0)
    assert client.status["status"] == "error"
    for start in range(0, RATE * 31, RATE):
        history.append(*signal(start, start + RATE).T)
    client = RecorderClient(context)
    client.start(shared, RecordingOptions(str(tmp_path / "overrun")), {}, {})
    def failed():
        client.poll()
        return client.status["status"] == "error"
    wait_until(failed)
    client.stop(history.total)
    meta = SessionReader(client.status["folder"]).meta
    assert meta["status"] == "error" and meta["frames"] == 0
    assert "覆盖" in meta["error"]


def test_playback_pause_seek_track_and_end_without_callback_io(tmp_path):
    folder = tmp_path / "play"
    writer = SessionWriter(folder, segment_seconds=1)
    writer.append(signal(0, RATE + 37))
    writer.close()
    reader = SessionReader(folder)
    original_read = reader.read_range
    def read(*args):
        assert threading.current_thread().name == "session-prefetch"
        return original_read(*args)
    reader.read_range = read
    buffer = PlaybackBuffer(reader)
    try:
        wait_until(lambda: len(buffer.blocks) > 0)
        output = np.ones((HOP, 1), np.float32)
        buffer.render(output)
        assert not np.any(output) and buffer.position == 0
        buffer.seek(RATE - 100, 0)
        wait_until(lambda: len(buffer.blocks) > 0)
        buffer.playing = True
        buffer.gain = 1
        buffer.render(output)
        np.testing.assert_array_equal(output[:137, 0], signal(RATE - 100, RATE + 37)[:, 0])
        assert not np.any(output[137:]) and buffer.finished
        buffer.seek(123, 1)
        wait_until(lambda: len(buffer.blocks) > 0)
        buffer.playing = True
        buffer.render(output)
        np.testing.assert_array_equal(output[:, 0], signal(123, 123 + HOP)[:, 1])
    finally:
        buffer.close()


def test_live_archive_and_tail_share_one_time_axis(tmp_path):
    folder = tmp_path / "live"
    writer = SessionWriter(folder)
    history = History()
    for start in range(0, RATE * 40, RATE):
        data = signal(start, start + RATE)
        history.append(*data.T)
        if start < RATE * 39:
            writer.append(data)
    writer.flush()
    x, y, f, spectrum = read_view(history, folder, 0, RATE * 40, 500, RATE * 40)
    assert x[0] == 0 and x[-1] > 39.99 and len(x) < 500 * 8 + 10
    assert len(f) == len(spectrum) and np.isfinite(spectrum).all()
    writer.close("interrupted", "test")


def test_preferences_round_trip_and_invalid_fallback(tmp_path):
    preferences = Preferences.load(tmp_path)
    assert preferences.span == 10 and preferences.auto_record
    preferences.span = 300
    preferences.wave_locked = False
    preferences.save(tmp_path)
    assert Preferences.load(tmp_path).span == 300
    assert not Preferences.load(tmp_path).wave_locked
    (tmp_path / "data/ui-settings.json").write_text('{"span": -1, "wave_locked": "false"}')
    assert Preferences.load(tmp_path).span == 10 and Preferences.load(tmp_path).wave_locked


def test_atomic_metadata_survives_concurrent_readers(tmp_path):
    path = tmp_path / "session.json"
    atomic_json(path, {"frame": 0})
    stop = threading.Event()
    errors = []
    def read():
        while not stop.is_set():
            try:
                assert isinstance(read_json(path)["frame"], int)
            except Exception as exc:
                errors.append(str(exc))
                return
    thread = threading.Thread(target=read)
    thread.start()
    try:
        for i in range(200):
            atomic_json(path, {"frame": i})
    finally:
        stop.set()
        thread.join()
    assert not errors
    assert json.loads(path.read_text())["frame"] == 199


def test_missing_summary_uses_bounded_audio_reads(tmp_path):
    writer = SessionWriter(tmp_path / "rebuild")
    for start in range(0, RATE * 31, RATE):
        writer.append(signal(start, start + RATE))
    writer.close()
    for file in writer.folder.glob("peaks-*.bin"):
        file.unlink()
    reader = SessionReader(writer.folder)
    read = reader.read_range
    sizes = []
    def limited(start, stop, columns=(0, 1)):
        sizes.append(stop - start)
        return read(start, stop, columns)
    reader.read_range = limited
    x, y = reader.waveform(0, reader.frames, 10)
    assert max(sizes) <= RATE * 2 and len(x) <= 20
    assert y[:, 0].max() == np.float32(9999 / 20000)


def test_corrupt_session_is_rejected_without_silent_padding(tmp_path):
    writer = SessionWriter(tmp_path / "broken")
    writer.append(signal(0, 100))
    writer.close()
    metadata = read_json(writer.folder / "session.json")
    metadata["segments"][0]["start"] = 12
    atomic_json(writer.folder / "session.json", metadata)
    with pytest.raises(ValueError, match="连续"):
        SessionReader(writer.folder)


def test_async_results_from_old_source_are_discarded(monkeypatch):
    import dsp_lab.waveforms as module
    entered, release = threading.Event(), threading.Event()
    def slow(*args):
        if args[0] == "old":
            entered.set()
            release.wait(2)
        return args[0]
    monkeypatch.setattr(module, "read_view", slow)
    browser = WaveformBrowser()
    try:
        browser.request((1, 0), "old")
        assert entered.wait(2)
        browser.invalidate()
        browser.request((2, 0), "new")
        release.set()
        values = []
        def ready():
            value = browser.poll()
            if value is not None:
                values.append(value)
            return bool(values)
        wait_until(ready)
        assert values == ["new"]
    finally:
        release.set()
        browser.close()
