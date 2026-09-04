from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

from .config import Config

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".webm", ".m4v", ".avi"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def _run(command: list[str]) -> None:
    print("+", " ".join(str(part) for part in command))
    subprocess.run(command, check=True)


def discover_media(
    directory: str | Path,
    extensions: Optional[Iterable[str]] = None,
) -> list[Path]:
    folder = Path(directory).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"Input directory not found: {folder}")
    wanted = {ext.lower() for ext in (extensions or [])}
    return sorted(
        p
        for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in wanted
    )


def _scale_filter(cfg: Config) -> str:
    return (
        f"scale={cfg.width}:{cfg.height}:force_original_aspect_ratio=decrease,"
        f"pad={cfg.width}:{cfg.height}:(ow-iw)/2:(oh-ih)/2,"
        f"fps={cfg.fps},format=yuv420p"
    )


def _audio_filter(cfg: Config) -> str | None:
    """Build audio filter chain for denoise + loudnorm."""
    parts: list[str] = []
    if cfg.denoise:
        parts.append("afftdn=nr=10:nf=-20")
    if cfg.loudnorm:
        parts.append("loudnorm=I=-16:TP=-1.5:LRA=11")
    return ",".join(parts) if parts else None


def _normalize(source: Path, destination: Path, cfg: Config) -> None:
    af = _audio_filter(cfg)
    if source.suffix.lower() in IMAGE_EXTS:
        command = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(source),
            "-t", str(cfg.image_duration),
            "-vf", _scale_filter(cfg),
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p",
            str(destination),
        ]
    else:
        command = [
            "ffmpeg", "-y", "-i", str(source),
            "-vf", _scale_filter(cfg),
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        ]
        if af:
            command += ["-af", af]
        command += ["-c:a", "aac", "-b:a", "128k", "-shortest", str(destination)]
    _run(command)


def _concat(clips: list[Path], output: Path) -> None:
    list_file = output.with_name(f"{output.stem}.txt")
    with list_file.open("w", encoding="utf-8") as fh:
        for clip in clips:
            path = str(clip).replace("\\", "/").replace("'", "'\\''")
            fh.write(f"file '{path}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
          "-c", "copy", str(output)])


def _overlay(video: Path, watermark: Path, output: Path, cfg: Config) -> None:
    command = [
        "ffmpeg", "-y", "-i", str(video), "-i", str(watermark),
        "-filter_complex", "[0:v][1:v]overlay=W-w-20:H-h-20",
        "-map", "0:v", "-map", "0:a?",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        "-c:a", "copy",
        str(output),
    ]
    _run(command)


def _burn_subtitles(video: Path, srt: Path, output: Path, cfg: Config) -> None:
    path = srt.resolve().as_posix().replace("'", "'\\''")
    if os.name == "nt":
        path = path.replace(":", "\\:")
    _run([
        "ffmpeg", "-y", "-i", str(video),
        "-vf", f"subtitles='{path}'",
        "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
        "-c:a", "copy",
        str(output),
    ])


def edit(
    cfg: Config,
    input_dir: Optional[str | Path] = None,
    output_dir: Optional[str | Path] = None,
    files: Optional[Iterable[str | Path]] = None,
) -> Path:
    input_path = Path(input_dir or cfg.input_dir).expanduser()
    output_path = Path(output_dir or cfg.output_dir).expanduser()

    sources = (
        [Path(item).expanduser() for item in files]
        if files is not None
        else discover_media(input_path, cfg.extensions)
    )
    if not sources:
        raise FileNotFoundError(f"No media found in {input_path}")

    output_path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="radxa-video-") as tmp:
        tmp_path = Path(tmp)
        normalized: list[Path] = []
        for index, source in enumerate(sources, 1):
            suffix = source.suffix.lower() or ".mp4"
            destination = tmp_path / f"clip_{index:03d}{suffix}"
            _normalize(source, destination, cfg)
            normalized.append(destination)

        # 去口水词/静音删除: 在 normalize 之后、concat 之前对每个片段处理
        if cfg.remove_fillers and len(normalized) > 0:
            from .cut_fillers import cut_fillers
            cleaned: list[Path] = []
            for clip in normalized:
                cut_path = clip.with_name(f"cut_{clip.name}")
                result = cut_fillers(clip, cut_path, cfg)
                cleaned.append(result)
            normalized = cleaned

        concat_path = tmp_path / "concat.mp4"
        _concat(normalized, concat_path)
        current = concat_path

        if cfg.watermark:
            watermark = Path(cfg.watermark).expanduser()
            if not watermark.is_file():
                raise FileNotFoundError(f"Watermark not found: {watermark}")
            marked = tmp_path / "watermarked.mp4"
            _overlay(current, watermark, marked, cfg)
            current = marked

        final = output_path / f"edit_{time.strftime('%Y%m%d_%H%M%S')}.mp4"

        if cfg.captions in ("srt", "burn"):
            srt_path = tmp_path / "captions.srt"
            from .transcribe import generate_srt

            generate_srt(
                current,
                srt_path,
                model=cfg.whisper_model,
                language=cfg.whisper_language,
                device=cfg.whisper_device,
                compute_type=cfg.whisper_compute_type,
            )
            if cfg.captions == "burn":
                if srt_path.stat().st_size > 0:
                    burned = tmp_path / "burned.mp4"
                    _burn_subtitles(current, srt_path, burned, cfg)
                    current = burned
                else:
                    print("! 字幕为空 (视频中无人声?), 跳过硬烧字幕")
            shutil.copy2(srt_path, final.with_suffix(".srt"))

        shutil.move(str(current), str(final))
        return final
