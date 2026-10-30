"""Streaming DSP. State persists between blocks; no files or models in this path."""
from dataclasses import dataclass
import numpy as np
from numba import njit
from scipy import signal

RATE = 16000
HOP = 256
FFT_SIZE = 512


@njit(cache=True, nogil=True)
def nlms_block(mic, reference, weights, history, position, mu, adapt):
    """Normalized LMS with a circular FIR history. Reference is actual playback."""
    output = np.empty_like(mic)
    estimate = np.empty_like(mic)
    length = len(weights)
    for n in range(len(mic)):
        history[position] = reference[n]
        predicted = 0.0
        energy = 1e-7
        j = position
        for k in range(length):
            x = history[j]
            predicted += weights[k] * x
            energy += x * x
            j -= 1
            if j < 0:
                j = length - 1
        error = mic[n] - predicted
        if adapt and energy > 1e-5:
            update = mu * error / energy
            j = position
            for k in range(length):
                weights[k] = weights[k] * (1.0 - 1e-7) + update * history[j]
                j -= 1
                if j < 0:
                    j = length - 1
        output[n] = error
        estimate[n] = predicted
        position += 1
        if position == length:
            position = 0
    return output, estimate, position


@njit(cache=True, nogil=True)
def delay_block(samples, history, position, delay, gain, inverse):
    output = np.empty_like(samples)
    for n in range(len(samples)):
        delayed = history[(position - delay) % len(history)]
        y = samples[n] - gain * delayed if inverse else samples[n] + gain * delayed
        history[position] = y if inverse else samples[n]
        output[n] = y
        position = (position + 1) % len(history)
    return output, position


class AdaptiveEchoCanceller:
    """A teaching baseline; update in far-end-only periods, freeze for double-talk."""

    def __init__(self, taps=2048, step=0.25):
        self.weights = np.zeros(taps, dtype=np.float32)
        self.history = np.zeros(taps, dtype=np.float32)
        self.position = 0
        self.step = step

    def process(self, microphone, reference, adapt=True):
        result, echo, self.position = nlms_block(
            microphone, reference, self.weights, self.history,
            self.position, self.step, adapt,
        )
        return result, echo

    def reset(self):
        self.weights.fill(0)
        self.history.fill(0)
        self.position = 0


class EchoEffect:
    """FIR echo y[n]=x[n]+g*x[n-D], and its known-path causal inverse."""

    def __init__(self, inverse=False):
        self.history = np.zeros(RATE * 2, dtype=np.float32)
        self.position = 0
        self.inverse = inverse

    def process(self, samples, delay_ms=180, gain=0.45):
        delay = max(1, min(len(self.history) - 1, round(RATE * delay_ms / 1000)))
        result, self.position = delay_block(
            samples, self.history, self.position, delay, gain, self.inverse,
        )
        return result


class SpectralEnhancer:
    """512-point sqrt-Hann WOLA, 50% overlap; fixed 256-sample delay."""

    def __init__(self):
        self.window = np.sqrt(signal.windows.hann(FFT_SIZE, sym=False))
        self.previous = np.zeros(HOP, dtype=np.float32)
        self.overlap = np.zeros(HOP, dtype=np.float64)
        self.noise = np.full(FFT_SIZE // 2 + 1, 1e-8)
        self.gain = np.ones(FFT_SIZE // 2 + 1)
        self.learning = 0
        self.learned = 0
        self.has_profile = False

    def learn_noise(self, seconds=1.0):
        self.learning = max(1, round(seconds * RATE / HOP))
        self.learned = 0
        self.has_profile = False

    def process(self, samples, enabled=True, strength=1.2):
        frame = np.concatenate((self.previous, samples))
        self.previous[:] = samples
        spectrum = np.fft.rfft(frame * self.window)
        power = np.abs(spectrum) ** 2
        if self.learning:
            self.learned += 1
            self.noise += (power - self.noise) / self.learned
            self.learning -= 1
            self.has_profile = self.learning == 0
        if enabled and self.has_profile:
            target = np.sqrt(np.maximum(0.025, 1.0 - strength * self.noise / (power + 1e-12)))
            self.gain = 0.65 * self.gain + 0.35 * target
        else:
            self.gain.fill(1)
        reconstructed = np.fft.irfft(spectrum * self.gain, n=FFT_SIZE) * self.window
        output = reconstructed[:HOP] + self.overlap
        self.overlap[:] = reconstructed[HOP:]
        return output.astype(np.float32)


@dataclass(frozen=True)
class Settings:
    enhance: bool = True
    strength: float = 1.2
    echo: bool = False
    inverse_echo: bool = False
    echo_ms: int = 180
    echo_gain: float = 0.45
    aec: bool = False
    adapt: bool = True
    monitor: bool = False
    output_gain: float = 0.6
    reference_gain: float = 0.2
    learn_token: int = 0
    reset_token: int = 0


class ProcessingChain:
    def __init__(self):
        self.aec = AdaptiveEchoCanceller()
        self.enhancer = SpectralEnhancer()
        self.echo = EchoEffect()
        self.inverse = EchoEffect(inverse=True)
        self.last_learn = 0
        self.last_reset = 0
        self.last_echo_parameters = None

    def process(self, microphone, reference, settings):
        if settings.reset_token != self.last_reset:
            self.aec.reset()
            self.last_reset = settings.reset_token
        if settings.learn_token != self.last_learn:
            self.enhancer.learn_noise()
            self.last_learn = settings.learn_token
        if settings.aec:
            clean, estimate = self.aec.process(microphone, reference, settings.adapt)
        else:
            clean, estimate = microphone, np.zeros_like(microphone)
        output = self.enhancer.process(clean, settings.enhance, settings.strength)
        parameters = (settings.echo, settings.inverse_echo, settings.echo_ms, settings.echo_gain)
        if parameters != self.last_echo_parameters:
            self.echo.history.fill(0)
            self.inverse.history.fill(0)
            self.last_echo_parameters = parameters
        if settings.echo:
            output = self.echo.process(output, settings.echo_ms, settings.echo_gain)
            if settings.inverse_echo:
                output = self.inverse.process(output, settings.echo_ms, settings.echo_gain)
        # Emergency bounded output. The UI reports clipping rather than hiding it.
        output = np.nan_to_num(output, nan=0.0, posinf=0.0, neginf=0.0)
        return output.astype(np.float32), clean, estimate


def warm_up():
    """Compile all callback kernels before opening audio hardware."""
    z = np.zeros(HOP, dtype=np.float32)
    aec = AdaptiveEchoCanceller()
    aec.process(z, z, True)
    EchoEffect().process(z)
    EchoEffect(inverse=True).process(z)


def dbfs(samples):
    return float(20 * np.log10(max(1e-8, np.sqrt(np.mean(np.asarray(samples) ** 2)))))
