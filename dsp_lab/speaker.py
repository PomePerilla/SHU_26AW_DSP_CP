"""CPU speaker embeddings; never invoked from an audio callback."""
from pathlib import Path
import json
import threading
import numpy as np
from .core import RATE, dbfs

MODEL_NAME = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"


def normalize(vector):
    vector = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm < 1e-8:
        raise ValueError("无法提取有效声纹")
    return vector / norm


def voice_samples(samples, min_active_seconds=1.5):
    """Energy gate, not a learned VAD; reject silence and obviously clipped input."""
    x = np.asarray(samples, dtype=np.float32)
    if len(x) < RATE * 2:
        raise ValueError("至少需要2秒语音；建议连续说话6秒")
    if not np.isfinite(x).all() or dbfs(x) < -45:
        raise ValueError("音量过低，请靠近麦克风并连续说话")
    if np.mean(np.abs(x) >= 0.99) > 0.02:
        raise ValueError("录音削波过多，请降低麦克风增益")
    size = 320
    frames = x[:len(x) // size * size].reshape(-1, size)
    energies = np.mean(frames ** 2, axis=1)
    threshold = max(1e-6, float(np.max(energies)) * 0.015)
    active = frames[energies > threshold].ravel()
    if len(active) < RATE * min_active_seconds:
        raise ValueError("有效语音不足，请延长连续讲话时间")
    return np.ascontiguousarray(active)


class SpeakerService:
    def __init__(self, project_root):
        self.root = Path(project_root)
        self.model_path = self.root / "models" / MODEL_NAME
        self.profile_path = self.root / "data" / "profiles.json"
        self.extractor = None
        self.profiles = {}
        self.lock = threading.RLock()
        if self.profile_path.exists():
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
            if data.get("model") != MODEL_NAME:
                raise ValueError("声纹档案模型不匹配")
            self.profiles = data.get("speakers", {})

    def load(self):
        import sherpa_onnx
        if not self.model_path.is_file():
            raise FileNotFoundError("未安装声纹模型，请运行 setup.ps1")
        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(self.model_path), num_threads=2, debug=False, provider="cpu",
        )
        if not config.validate():
            raise ValueError("声纹模型配置无效")
        self.extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        return {"model": MODEL_NAME, "dimension": self.extractor.dim}

    def embedding(self, samples, min_active_seconds=1.5):
        samples = voice_samples(samples, min_active_seconds)
        if self.extractor is None:
            self.load()
        stream = self.extractor.create_stream()
        stream.accept_waveform(sample_rate=RATE, waveform=samples)
        stream.input_finished()
        if not self.extractor.is_ready(stream):
            raise ValueError("语音不足以生成声纹")
        return normalize(self.extractor.compute(stream))

    def enroll(self, name, samples):
        name = name.strip()
        if not name or len(name) > 30:
            raise ValueError("请输入1至30字的组员姓名")
        vector = self.embedding(samples)
        with self.lock:
            previous = self.profiles.get(name)
            if previous:
                count = previous["count"]
                total = np.asarray(previous["sum"], dtype=np.float32) + vector
            else:
                count, total = 0, vector
            self.profiles[name] = {"count": count + 1, "sum": total.tolist()}
            self._save()
        return {"name": name, "count": count + 1}

    def identify(self, samples, threshold=0.55, margin=0.05):
        # Registration is stricter; a sliding identification window may contain pauses.
        vector = self.embedding(samples, min_active_seconds=1.0)
        with self.lock:
            rankings = sorted(
                ({"name": name, "similarity": float(np.dot(vector, normalize(p["sum"])))}
                 for name, p in self.profiles.items()),
                key=lambda row: row["similarity"], reverse=True,
            )
        if not rankings:
            return {"name": "尚未注册组员", "rankings": [], "accepted": False}
        top = rankings[0]["similarity"]
        gap = top - rankings[1]["similarity"] if len(rankings) > 1 else top
        accepted = top >= threshold and gap >= margin
        return {"name": rankings[0]["name"] if accepted else "未知说话人", "accepted": accepted,
                "similarity": top, "margin": gap, "rankings": rankings,
                "threshold": threshold, "margin_threshold": margin}

    def remove(self, name):
        with self.lock:
            self.profiles.pop(name, None)
            self._save()

    def _save(self):
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"model": MODEL_NAME, "speakers": self.profiles}
        temp = self.profile_path.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.profile_path)
