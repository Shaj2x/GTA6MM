"""Optional word-timed captions via faster-whisper (pip install .[captions])."""
from __future__ import annotations

from pathlib import Path


def _ts(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(chunks: list[tuple[float, float, str]]) -> str:
    return "\n".join(f"{i}\n{_ts(a)} --> {_ts(b)}\n{t.strip()}\n"
                     for i, (a, b, t) in enumerate(chunks, 1))


def chunk_words(words: list[tuple[float, float, str]], max_words: int = 4):
    """Short punchy caption groups (the style that retains on Shorts/TikTok)."""
    out, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) >= max_words or w[2].rstrip().endswith((".", "!", "?", ",")):
            out.append((cur[0][0], cur[-1][1], " ".join(x[2].strip() for x in cur).upper()))
            cur = []
    if cur:
        out.append((cur[0][0], cur[-1][1], " ".join(x[2].strip() for x in cur).upper()))
    return out


def words(media: Path) -> list[tuple[float, float, str]] | None:
    """Word-level timestamps for a speech file, or None if whisper isn't available."""
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    import subprocess

    import numpy as np

    try:
        # Decode with ffmpeg ourselves: faster-whisper's own decoder (PyAV) breaks
        # across PyAV versions, and captions must never block a render.
        pcm = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(media), "-f", "s16le",
                              "-ac", "1", "-ar", "16000", "-"], capture_output=True, check=True).stdout
        audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
        model = WhisperModel("small", compute_type="int8")
        segments, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True)
        out = [(w.start, w.end, w.word) for seg in segments for w in (seg.words or [])]
    except Exception as exc:
        print(f"[captions] skipped: {exc}")
        return None
    return out or None


def transcribe(media: Path, out_srt: Path, start: float = 0.0, length: float | None = None,
               max_words: int = 4) -> Path | None:
    ws = words(media)
    if not ws:
        return None
    ws = [(max(0.0, a - start), b - start, w) for a, b, w in ws
          if b >= start and not (length and a > start + length)]
    if not ws:
        return None
    out_srt.write_text(to_srt(chunk_words(ws, max_words)))
    return out_srt
