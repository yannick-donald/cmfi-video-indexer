from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TOKEN_SPLIT_RE = re.compile(r"[_\-\.\s]+")
_TRAILING_GARBAGE_RE = re.compile(r"\b(final|v\d+|ver\d+|cut|edit|export|render|x264|x265|h264|h265)\b", re.I)


@dataclass(slots=True)
class NormalizedTitle:
    clean_title: str
    year: str
    speaker: str
    keywords: list[str]
    normalized_name: str


def normalize_title(file_name: str, folder_path: str = "") -> NormalizedTitle:
    """
    Heuristic title normalization designed for Christian teaching libraries.
    This is rules-based and deterministic (safe for production) and can be enriched later with AI.
    """
    stem = Path(file_name).stem
    raw = stem.replace("__", "_")

    year = ""
    m = _YEAR_RE.search(raw)
    if m:
        year = m.group(1)

    # Split tokens and drop obvious noise.
    tokens = [t for t in _TOKEN_SPLIT_RE.split(raw) if t]
    tokens = [t for t in tokens if not _TRAILING_GARBAGE_RE.fullmatch(t)]
    tokens_lower = [t.lower() for t in tokens]

    speaker = _infer_speaker(tokens)

    # Build a "normalized_name" for similarity/duplicate heuristics
    normalized_name = re.sub(r"[^a-z0-9]+", " ", " ".join(tokens_lower)).strip()

    # Remove year and speaker tokens from title candidates
    title_tokens: list[str] = []
    for t in tokens:
        if year and t == year:
            continue
        if speaker and t.lower() in speaker.lower().split():
            continue
        if t.lower() in {"the", "a", "an", "and", "of", "to", "for", "in", "on"}:
            title_tokens.append(t.lower())
        else:
            title_tokens.append(t)

    # If folder path contains a strong hint like "/Sermons/Prayer", keep it as keyword not title.
    folder_keywords = _folder_keywords(folder_path)
    keywords = sorted(set([*folder_keywords, *[k for k in tokens_lower if len(k) >= 4]]))

    # Title-case, but keep small words lowercase unless first.
    clean_title = _title_case(" ".join(title_tokens))
    clean_title = clean_title.strip(" -_")
    clean_title = re.sub(r"\s{2,}", " ", clean_title).strip()

    # If title still looks messy, fallback to the stem in a safer way.
    if len(clean_title) < 3:
        clean_title = _title_case(stem.replace("_", " ").replace("-", " ").strip())

    return NormalizedTitle(
        clean_title=clean_title,
        year=year,
        speaker=speaker,
        keywords=keywords[:25],
        normalized_name=normalized_name[:512],
    )


def _infer_speaker(tokens: list[str]) -> str:
    # Common style: 2021_JohnPiper_TITLE... or John_Piper_TITLE...
    joined = " ".join(tokens)
    # If there is an ALLCAPS style speaker token near start
    candidates: list[str] = []
    for i in range(min(4, len(tokens))):
        t = tokens[i]
        if t.isalpha() and t[:1].isupper() and len(t) >= 3:
            candidates.append(t)
    if len(candidates) >= 2:
        return f"{candidates[0]} {candidates[1]}"
    if candidates:
        # Try to split CamelCase like JohnPiper
        cc = re.findall(r"[A-Z][a-z]+", candidates[0])
        if len(cc) >= 2:
            return " ".join(cc[:3])
        return candidates[0]

    # Known patterns in joined string
    m = re.search(r"\b([A-Z][a-z]+)([A-Z][a-z]+)\b", joined)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return ""


def _title_case(text: str) -> str:
    small = {"and", "or", "the", "a", "an", "of", "to", "for", "in", "on", "with", "by"}
    words = [w for w in text.split() if w]
    if not words:
        return ""
    out: list[str] = []
    for i, w in enumerate(words):
        wl = w.lower()
        if i > 0 and wl in small:
            out.append(wl)
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


def _folder_keywords(folder_path: str) -> list[str]:
    if not folder_path:
        return []
    tokens = [t.lower() for t in _TOKEN_SPLIT_RE.split(folder_path.replace("/", " ")) if t]
    drop = {"my", "drive", "shared", "with", "me", "videos", "video", "media"}
    return [t for t in tokens if t not in drop and len(t) >= 4][:10]

