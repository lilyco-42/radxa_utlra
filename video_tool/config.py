from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency
    yaml = None

DEFAULT_EXTENSIONS = [
    ".mp4",
    ".mkv",
    ".mov",
    ".webm",
    ".m4v",
    ".avi",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
]

PATH_FIELDS = {"input_dir", "output_dir", "done_dir"}
INT_FIELDS = {"width", "height", "fps", "crf", "interval", "radxa_port"}
FLOAT_FIELDS = {"image_duration"}
LIST_FIELDS = {"extensions", "upload_targets"}


@dataclass
class Config:
    input_dir: Path = Path("~/Videos/raw")
    output_dir: Path = Path("~/Videos/edited")
    done_dir: Path = Path("~/Videos/done")
    extensions: list[str] = field(default_factory=lambda: list(DEFAULT_EXTENSIONS))
    width: int = 1080
    height: int = 1920
    fps: int = 30
    crf: int = 23
    preset: str = "medium"
    image_duration: float = 3.0
    watermark: Optional[str] = None
    captions: str = "srt"
    whisper_model: str = "small"
    whisper_language: Optional[str] = None
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    upload_targets: list[str] = field(default_factory=list)
    keep_raw: bool = True
    interval: int = 10
    radxa_host: str = "lain42.top"
    radxa_user: str = "root"
    radxa_key: str = "~/.ssh/lain42.pem"
    radxa_port: int = 22
    # 可选资源协调闸门: 非空时每轮处理前执行该命令, 退出码 0=放行, 非 0=暂停
    # (例如 MC 有玩家在线时让出 CPU). 配合 scripts/mc-gate.sh 使用.
    gate_command: str = ""

    @classmethod
    def load(
        cls,
        path: Optional[str | Path] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> "Config":
        if path is None:
            env_path = os.environ.get("RADXA_VIDEO_CONFIG")
            if env_path:
                path = env_path

        data = asdict(cls())
        if path:
            config_path = Path(path).expanduser()
            if not config_path.is_file():
                raise FileNotFoundError(f"Config not found: {config_path}")
            loaded = cls._read_file(config_path)
            data.update(cls._pick(loaded, data))

        if overrides:
            data.update(cls._pick(overrides, data))

        data = cls._coerce(data)
        return cls(**{name: data[name] for name in data if name in cls._field_names()})

    @staticmethod
    def _field_names() -> set[str]:
        return set(asdict(Config()).keys())

    @staticmethod
    def _read_file(path: Path) -> dict[str, Any]:
        if path.suffix == ".json":
            return json.loads(path.read_text(encoding="utf-8"))
        if yaml is None:
            raise RuntimeError(
                "PyYAML is required to load YAML configs: pip install -r requirements.txt"
            )
        with path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"Config must be a mapping, got {type(loaded).__name__}")
        return loaded

    @staticmethod
    def _pick(mapping: dict[str, Any], known: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in mapping.items() if key in known}

    @classmethod
    def _coerce(cls, data: dict[str, Any]) -> dict[str, Any]:
        for name in PATH_FIELDS:
            if data.get(name):
                data[name] = Path(data[name]).expanduser()
        for name in INT_FIELDS:
            if name in data and data[name] is not None:
                data[name] = int(data[name])
        for name in FLOAT_FIELDS:
            if name in data and data[name] is not None:
                data[name] = float(data[name])
        for name in LIST_FIELDS:
            value = data.get(name)
            if value is None:
                data[name] = []
            elif isinstance(value, str):
                data[name] = [value]
        return data
