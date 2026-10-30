import numpy as np
from scipy.signal import lfilter
from dsp_lab.core import (
    RATE, HOP, SpectralEnhancer, EchoEffect, AdaptiveEchoCanceller,
    ProcessingChain, Settings, warm_up,
)
from dsp_lab.audio import History, AudioEngine


def test_wola_identity_has_exactly_one_hop_delay():
    rng = np.random.default_rng(7)
    source = rng.normal(0, 0.1, HOP * 50).astype(np.float32)
    enhancer = SpectralEnhancer()
    output = np.concatenate([enhancer.process(block, enabled=False)
                             for block in source.reshape(-1, HOP)])
    np.testing.assert_allclose(output[HOP:], source[:-HOP], atol=1e-6)


def test_known_echo_inverse_preserves_dry_voice_across_blocks():
    rng = np.random.default_rng(8)
    source = rng.normal(0, 0.08, HOP * 200).astype(np.float32)
    effect, inverse = EchoEffect(), EchoEffect(inverse=True)
    output, echoed = [], []
    for block in source.reshape(-1, HOP):
        wet = effect.process(block, delay_ms=177, gain=0.65)
        echoed.append(wet)
        output.append(inverse.process(wet, delay_ms=177, gain=0.65))
    np.testing.assert_allclose(np.concatenate(output), source, atol=1e-6)
    assert np.sqrt(np.mean((np.concatenate(echoed) - source) ** 2)) > 0.01


def test_nlms_learns_echo_then_preserves_near_voice_when_frozen():
    rng = np.random.default_rng(9)
    reference = rng.normal(0, 0.15, HOP * 240).astype(np.float32)
    path = np.zeros(128)
    path[21], path[55], path[101] = 0.6, 0.2, -0.1
    echo = lfilter(path, [1], reference).astype(np.float32)
    aec = AdaptiveEchoCanceller(taps=256, step=0.4)
    result = []
    for mic, ref in zip(echo.reshape(-1, HOP), reference.reshape(-1, HOP)):
        result.append(aec.process(mic, ref)[0])
    residual = np.concatenate(result)
    assert np.mean(residual[-RATE:] ** 2) < np.mean(echo[-RATE:] ** 2) * 1e-3
    near = rng.normal(0, 0.08, HOP).astype(np.float32)
    ref = rng.normal(0, 0.15, HOP).astype(np.float32)
    saved = aec.weights.copy()
    # Run the same known room with continuous reference history.
    combined = np.concatenate((reference, ref))
    next_echo = lfilter(path, [1], combined)[-HOP:].astype(np.float32)
    output, _ = aec.process(next_echo + near, ref, adapt=False)
    np.testing.assert_allclose(output, near, atol=1e-3)
    np.testing.assert_array_equal(saved, aec.weights)


def test_spectral_enhancement_improves_known_noisy_signal():
    rng = np.random.default_rng(10)
    blocks = 250
    t = np.arange(blocks * HOP) / RATE
    clean = (0.12 * np.sin(2 * np.pi * 430 * t) + 0.08 * np.sin(2 * np.pi * 980 * t)).astype(np.float32)
    noise = rng.normal(0, 0.065, len(t)).astype(np.float32)
    enhancer = SpectralEnhancer()
    enhancer.learn_noise(1)
    for _ in range(63):
        enhancer.process(rng.normal(0, 0.065, HOP).astype(np.float32))
    output = np.concatenate([enhancer.process(block, True, 1.4)
                             for block in (clean + noise).reshape(-1, HOP)])
    desired = clean[:-HOP]
    in_error = np.mean(noise[:-HOP] ** 2)
    out_error = np.mean((output[HOP:] - desired) ** 2)
    assert 10 * np.log10(in_error / out_error) > 2


def test_history_wrap_keeps_aligned_channels():
    history = History(seconds=1)
    for i in range(90):
        x = np.arange(i * HOP, (i + 1) * HOP, dtype=np.float32)
        history.append(x, x + 1, x + 2, x + 3)
    data = history.snapshot(1)
    expected = np.arange(90 * HOP - RATE, 90 * HOP, dtype=np.float32)
    for col in range(4):
        np.testing.assert_array_equal(data[:, col], expected + col)


def test_callback_records_actual_limited_playback_reference():
    warm_up()
    engine = AudioEngine()
    engine.settings = Settings(reference_gain=0.8)
    engine.set_reference(np.full(HOP, 3, dtype=np.float32))
    engine.reference_enabled = True
    mic = np.zeros((HOP, 1), dtype=np.float32)
    output = np.zeros_like(mic)
    engine._callback(mic, output, HOP, None, False)
    previous = output[:, 0].copy()
    engine._callback(mic, output, HOP, None, False)
    np.testing.assert_allclose(engine.history.snapshot(1)[-HOP:, 2], previous)
    assert engine.clips == 2
    assert np.max(np.abs(output)) <= 0.981


def test_full_chain_handles_silence_with_all_modules_enabled():
    chain = ProcessingChain()
    settings = Settings(aec=True, echo=True, inverse_echo=True, learn_token=1)
    z = np.zeros(HOP, dtype=np.float32)
    for _ in range(100):
        output, _, _ = chain.process(z, z, settings)
        assert np.isfinite(output).all()
        np.testing.assert_array_equal(output, z)


def test_monitor_routes_continuous_processed_microphone_to_output():
    engine = AudioEngine()
    engine.settings = Settings(enhance=False, monitor=True, output_gain=0.5)
    rng = np.random.default_rng(24)
    previous = np.zeros(HOP, dtype=np.float32)
    output = np.zeros((HOP, 1), dtype=np.float32)
    for _ in range(30):
        microphone = rng.normal(0, 0.1, (HOP, 1)).astype(np.float32)
        engine._callback(microphone, output, HOP, None, False)
        np.testing.assert_allclose(output[:, 0], previous * 0.5, atol=1e-6)
        previous = microphone[:, 0].copy()
