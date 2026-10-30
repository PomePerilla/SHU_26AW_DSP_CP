"""User presentation/recording choices, separate from DSP algorithm settings."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json

SPANS = (2, 5, 10, 30, 60, 300, 600)


@dataclass
class Preferences:
    span: int = 10
    wave_locked: bool = True
    spectrum_locked: bool = True
    auto_record: bool = True
    recording_directory: str = ""

    @classmethod
    def load(cls, root):
        default = cls(recording_directory=str(root / "data" / "recordings"))
        try:
            values = json.loads((root / "data" / "ui-settings.json").read_text(encoding="utf-8"))
            if values.get("span") in SPANS:
                default.span = values["span"]
            for key in ("wave_locked", "spectrum_locked", "auto_record"):
                if type(values.get(key)) is bool:
                    setattr(default, key, values[key])
            if isinstance(values.get("recording_directory"), str) and values["recording_directory"]:
                default.recording_directory = values["recording_directory"]
        except (OSError, ValueError, TypeError):
            pass
        return default

    def save(self, root):
        path = Path(root) / "data" / "ui-settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
