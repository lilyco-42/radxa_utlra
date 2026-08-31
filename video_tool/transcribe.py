from __future__ import annotations

from pathlib import Path


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    total_ms = int(round(seconds * 1000))
    ms = total_ms % 1000
    total_s = total_ms // 1000
    hours, rem = divmod(total_s, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def generate_srt(
    media: str | Path,
    srt_path: str | Path,
    model: str = "small",
    language: str | None = None,
    device: str = "cpu",
    compute_type: str = "int8",
) -> Path:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper is required for captions (pip install -r requirements.txt)"
        ) from exc

    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    segments, _info = whisper.transcribe(str(media), language=language, vad_filter=True)

    output = Path(srt_path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as fh:
        for index, segment in enumerate(segments, 1):
            start = format_timestamp(segment.start)
            end = format_timestamp(segment.end)
            text = segment.text.strip()
            fh.write(f"{index}\n")
            fh.write(f"{start} --> {end}\n")
            fh.write(f"{text}\n\n")
    return output
