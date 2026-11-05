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


def transcribe(media: Path, out_srt: Path, start: float = 0.0, length: float | None = None) -> Path | None:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    model = WhisperModel("small", compute_type="int8")
    segments, _ = model.transcribe(str(media), word_timestamps=True, vad_filter=True)
    words = []
    for seg in segments:
        for w in seg.words or []:
            if w.end < start or (length and w.start > start + length):
                continue
            words.append((max(0.0, w.start - start), w.end - start, w.word))
    if not words:
        return None
    out_srt.write_text(to_srt(chunk_words(words)))
    return out_srt
