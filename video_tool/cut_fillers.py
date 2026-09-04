"""cut_fillers: detect and remove silence + filler words from a video.

Adapted from tightcut (github.com/AndreaGiulianini/tightcut, MIT).
Key changes for radxa_utlra:
  - Chinese filler word list (嗯/啊/呃/那个/就是说/然后/这个/那个/就是)
  - Reuses transcribe.py's get_model() for whisper model caching
  - Uses full re-encode mode (A733 has no hardware encoder; smart mode's
    keyframe probing adds complexity for little gain at short clip durations)
  - Verbatim prompt for Chinese to counter Whisper's tendency to strip disfluencies
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import Config
from .transcribe import get_model

# Chinese filler words / hesitation markers
# These are what Whisper transcribes when it hears "嗯"、"啊"、"呃" etc.
ZH_FILLERS = {
    "嗯", "啊", "呃", "额", "喔", "哎", "唉",
    "那个", "这个", "就是", "就是说", "然后", "对吧",
    "嘛", "吧", "呢", "哦", "噢", "哈", "哈哈",
}

# English fillers (for bilingual content)
EN_FILLERS = {
    "ehm", "ehmm", "ehmmm", "uhm", "uhmm", "mh", "mhm", "mmm", "mmmm", "mmh",
    "hmm", "eh", "ehh", "ah", "ahh", "uh", "uhh", "eee", "ee", "umm",
    "um", "uh", "er", "erm", "like", "basically", "literally",
}

# Verbatim prompt: tell Whisper to transcribe exactly what it hears,
# including hesitation sounds. Without this, Whisper tends to "clean up"
# speech by omitting fillers.
VERBATIM_PROMPT_ZH = "请逐字转写,包括嗯、啊、呃等语气词和停顿。"
VERBATIM_PROMPT_EN = (
    "Verbatim word-by-word transcription, "
    "including hesitations like ehm, uhm, eh, ah, mh."
)


@dataclass
class Word:
    text: str
    start: float
    end: float


def _normalize_token(s: str) -> str:
    """Strip punctuation/whitespace for filler matching."""
    return s.strip(" .,?!\"'…-—–:;()，。！？、""''…（）").lower()


def _probe_duration(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        text=True,
    )
    return float(out.strip())


def _extract_audio(video: Path, wav: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-i", str(video),
         "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        check=True,
    )


def _transcribe_words(
    video: Path,
    model: str,
    language: str | None,
    device: str,
    compute_type: str,
) -> list[Word]:
    """Transcribe video audio with word-level timestamps."""
    whisper = get_model(model, device, compute_type)

    is_chinese = language in ("zh", "chinese", "Chinese", None, "")
    prompt = VERBATIM_PROMPT_ZH if is_chinese else VERBATIM_PROMPT_EN

    with tempfile.TemporaryDirectory() as td:
        wav = Path(td) / "audio.wav"
        _extract_audio(video, wav)
        segments, _meta = whisper.transcribe(
            str(wav),
            language=language,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 250},
            beam_size=5,
            initial_prompt=prompt,
            condition_on_previous_text=False,
        )
        words: list[Word] = []
        for seg in segments:
            if seg.words:
                for w in seg.words:
                    text = w.word.strip()
                    if text:
                        words.append(Word(text, float(w.start), float(w.end)))
    return words


def _build_cuts(
    words: list[Word],
    duration: float,
    fillers: set[str],
    max_silence: float,
    pad: float,
) -> list[tuple[float, float, str]]:
    """Return list of (start, end, reason) intervals to REMOVE."""
    cuts: list[tuple[float, float, str]] = []
    prev_end = 0.0
    for w in words:
        gap = w.start - prev_end
        if gap > max_silence:
            s = prev_end + pad
            e = w.start - pad
            if e - s > 0.05:
                cuts.append((s, e, "silence"))
        if _normalize_token(w.text) in fillers:
            cuts.append(
                (max(0.0, w.start - pad / 2),
                 min(duration, w.end + pad / 2),
                 f"filler:{w.text.strip()}")
            )
        prev_end = max(prev_end, w.end)
    if duration - prev_end > max_silence:
        cuts.append((prev_end + pad, duration, "silence"))
    return _merge(sorted(cuts, key=lambda x: x[0]))


def _merge(
    intervals: list[tuple[float, float, str]],
) -> list[tuple[float, float, str]]:
    if not intervals:
        return []
    out = [intervals[0]]
    for s, e, r in intervals[1:]:
        ps, pe, pr = out[-1]
        if s <= pe + 0.01:
            out[-1] = (ps, max(pe, e), pr if pr == r else "mixed")
        else:
            out.append((s, e, r))
    return out


def _invert(
    cuts: list[tuple[float, float, str]], duration: float,
) -> list[tuple[float, float]]:
    """Convert cut segments to keep segments."""
    keeps: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e, _ in cuts:
        if s > cursor:
            keeps.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < duration:
        keeps.append((cursor, duration))
    return [(s, e) for s, e in keeps if e - s > 0.05]


def _assemble_full(
    video: Path,
    keeps: list[tuple[float, float]],
    output: Path,
    cfg: Config,
) -> None:
    """Re-encode kept segments and concatenate."""
    parts: list[str] = []
    for i, (s, e) in enumerate(keeps):
        parts.append(
            f"[0:v]trim=start={s:.3f}:end={e:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        parts.append(
            f"[0:a]atrim=start={s:.3f}:end={e:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
    chain = "".join(f"[v{i}][a{i}]" for i in range(len(keeps)))
    parts.append(f"{chain}concat=n={len(keeps)}:v=1:a=1[outv][outa]")

    with tempfile.TemporaryDirectory() as td:
        script_path = Path(td) / "filter.txt"
        script_path.write_text(";\n".join(parts))
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(video),
            "-filter_complex_script", str(script_path),
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", cfg.preset, "-crf", str(cfg.crf),
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart", str(output),
        ]
        subprocess.run(cmd, check=True)


def cut_fillers(
    video: Path,
    output: Path,
    cfg: Config,
) -> Path:
    """Remove silence and filler words from a video.

    Args:
        video: input video path
        output: output video path
        cfg: config with remove_fillers, max_silence, filler_pad, whisper settings

    Returns:
        Path to the output video (or input if no cuts needed).
    """
    duration = _probe_duration(video)
    if duration < 1.0:
        return video  # too short to bother

    language = cfg.whisper_language
    fillers = ZH_FILLERS | EN_FILLERS if not language or language.startswith("zh") else EN_FILLERS

    words = _transcribe_words(
        video,
        model=cfg.whisper_model,
        language=language,
        device=cfg.whisper_device,
        compute_type=cfg.whisper_compute_type,
    )

    if not words:
        print(f"  cut_fillers: no speech detected in {video.name}, skipping")
        return video

    cuts = _build_cuts(words, duration, fillers, cfg.max_silence, cfg.filler_pad)
    keeps = _invert(cuts, duration)

    if not keeps:
        print(f"  cut_fillers: everything would be cut from {video.name}, skipping")
        return video

    cut_total = sum(e - s for s, e, _ in cuts)
    if cut_total < 0.5:
        print(f"  cut_fillers: only {cut_total:.2f}s to cut from {video.name}, skipping")
        return video

    n_silence = sum(1 for c in cuts if c[2] in ("silence", "mixed"))
    n_filler = len(cuts) - n_silence
    print(f"  cut_fillers: removing {len(cuts)} segments "
          f"(~{n_silence} silences, ~{n_filler} fillers): "
          f"{cut_total:.2f}s cut from {duration:.2f}s")

    _assemble_full(video, keeps, output, cfg)
    return output
