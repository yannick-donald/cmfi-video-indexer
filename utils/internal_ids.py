from __future__ import annotations

import re

INTERNAL_ID_PREFIX = "CHR-VID"

# Accepts the canonical CHR-VID-000123 and the shapes a human actually types:
# lower case, spaces or underscores instead of hyphens, and fewer than six
# digits. It must not be preceded or followed by a word character, so that
# "XCHR-VID-1" or "CHR-VID-1234567" are not silently truncated to a match.
_INTERNAL_ID_RE = re.compile(
    r"(?<![0-9A-Za-z])CHR[-_ ]?VID[-_ ]?(\d{1,6})(?![0-9A-Za-z])",
    re.IGNORECASE,
)


def format_internal_id(number: int) -> str:
    """Canonical form of an internal ID: CHR-VID-000123."""
    return f"{INTERNAL_ID_PREFIX}-{number:06d}"


def extract_internal_id(text: str) -> str | None:
    """
    Find an internal video ID inside a file name and return it canonicalised.

    "CHR-VID-000199 - Extrait louange.mp4" -> "CHR-VID-000199"
    "chr_vid_199_extrait.mp4"              -> "CHR-VID-000199"
    "Culte 2026.mp4"                       -> None

    Returns None when the text carries no ID, or more than one: two different
    IDs in a name is ambiguous, and guessing which is the source would be worse
    than leaving it to a human.
    """
    if not text:
        return None
    matches = _INTERNAL_ID_RE.findall(text)
    if not matches:
        return None
    canonical = {format_internal_id(int(match)) for match in matches}
    if len(canonical) != 1:
        return None
    return canonical.pop()
