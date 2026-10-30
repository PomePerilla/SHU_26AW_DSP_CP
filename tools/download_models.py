"""Download only public upstream assets, with recorded URLs, sizes and hashes."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/"
MODEL = "3dspeaker_speech_campplus_sv_zh-cn_16k-common.onnx"
ASSETS = {
    MODEL: (28281138, None),
    "fangjun-test-sr-1.wav": (178374, "9175e523081bf6a630ce72a55b05f92148eaafaf58cbbbe743686cd81c50848e"),
    "fangjun-test-sr-2.wav": (69890, "c55912144a28817a67d5eac806e5952ffcd29b82db3925f46d33df4c5f038907"),
    "leijun-test-sr-1.wav": (263878, "36cda04ee4d10e38095de73b77c99e8e7c54347232967a9cde4cf54f7d496bab"),
    "leijun-test-sr-2.wav": (217872, "84b85e7413b5348303a95bd78813e4d669b4dc70fa5a99990b9860ca6fda91e8"),
    "liudehua-test-sr-1.wav": (119442, "a855706ad8e8253e341e3f65892c66d7dfb4bdc83675bcf38b863f2f8d167553"),
}


def download(name, folder):
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / name
    size, expected = ASSETS[name]
    if not target.exists():
        part = target.with_suffix(target.suffix + ".part")
        print(f"Downloading {name}", flush=True)
        request = urllib.request.Request(BASE + name, headers={"User-Agent": "SHU_26AW_DSP_CP/0.1"})
        with urllib.request.urlopen(request, timeout=60) as response, part.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        if part.stat().st_size != size:
            raise RuntimeError(f"Unexpected download size: {name}")
        part.replace(target)
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if target.stat().st_size != size or expected and digest != expected:
        raise RuntimeError(f"Integrity check failed: {name}")
    return {"file": name, "url": BASE + name, "bytes": size, "sha256": digest,
            "publisher_sha256_available": expected is not None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", action="store_true")
    args = parser.parse_args()
    model = download(MODEL, ROOT / "models")
    (ROOT / "models" / "manifest.json").write_text(json.dumps(model, indent=2), encoding="utf-8")
    if args.fixtures:
        assets = [download(name, ROOT / "validation" / "fixtures") for name in ASSETS if name != MODEL]
        (ROOT / "validation" / "fixtures" / "manifest.json").write_text(
            json.dumps({"purpose": "public upstream smoke-test audio, NOT project members", "assets": assets}, indent=2),
            encoding="utf-8",
        )
    print("Model ready. All inference runs locally.")


if __name__ == "__main__":
    main()
