from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Copy markers appended by Drive, Windows and macOS when a file is duplicated.
_COPY_MARKER_RE = re.compile(r"\b(copie|copy|kopie|copia)\b")
_VERSION_MARKER_RE = re.compile(
    r"\b(final|finale|v\d+|ver\d+|version\s*\d+|export|render|cut|edit)\b"
)
# A bare parenthesised number is nearly always a copy index: "Culte (1).mp4".
_PAREN_INDEX_RE = re.compile(r"\(\s*\d+\s*\)")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

# Two durations count as equal when they differ by less than this many seconds:
# Drive and ffprobe disagree slightly on the same media.
DURATION_TOLERANCE_SECONDS = 1.0

# Match tiers, strongest first. The tier index doubles as the sort key.
_TIER_NAME_SIZE_DURATION = 0
_TIER_SIZE_DURATION = 1
_TIER_NAME = 2

_TIER_LABELS: dict[int, tuple[str, str]] = {
    _TIER_NAME_SIZE_DURATION: ("Nom, taille et durée identiques", "Très élevée"),
    _TIER_SIZE_DURATION: ("Taille et durée identiques (nom différent)", "Élevée"),
    _TIER_NAME: ("Nom identique après normalisation", "Moyenne"),
}


@dataclass(slots=True)
class DuplicateMember:
    file_id: str
    file_name: str
    folder_path: str
    drive_url: str
    internal_video_id: str
    file_size: int
    duration_seconds: float | None
    modified_at: str


@dataclass(slots=True)
class DuplicateGroup:
    key: str
    reason: str
    confidence: str
    members: list[DuplicateMember] = field(default_factory=list)

    @property
    def wasted_bytes(self) -> int:
        """Bytes that would be freed by keeping a single copy."""
        if len(self.members) < 2:
            return 0
        sizes = sorted((member.file_size for member in self.members), reverse=True)
        return sum(sizes[1:])


def normalize_for_comparison(file_name: str) -> str:
    """
    Reduce a file name to a comparison key: no extension, no accents, no copy or
    version markers, no punctuation. "Culte 12-01 (1) - Copie.MP4" and
    "culte_12_01_final.mp4" both collapse to "culte 12 01".

    Punctuation is collapsed before markers are stripped, so that separators like
    the underscore in "..._final" do not hide the word boundary.
    """
    stem = _PAREN_INDEX_RE.sub(" ", Path(file_name).stem)
    decomposed = unicodedata.normalize("NFKD", stem.casefold())
    without_accents = "".join(char for char in decomposed if not unicodedata.combining(char))
    collapsed = _NON_ALNUM_RE.sub(" ", without_accents)
    without_markers = _VERSION_MARKER_RE.sub(" ", _COPY_MARKER_RE.sub(" ", collapsed))
    return " ".join(without_markers.split())


def _duration_bucket(duration: float | None) -> int | None:
    if duration is None:
        return None
    return int(round(float(duration) / DURATION_TOLERANCE_SECONDS))


def _to_member(row: dict[str, Any]) -> DuplicateMember:
    return DuplicateMember(
        file_id=str(row.get("file_id") or ""),
        file_name=str(row.get("file_name") or ""),
        folder_path=str(row.get("folder_path") or ""),
        drive_url=str(row.get("drive_url") or ""),
        internal_video_id=str(row.get("internal_video_id") or ""),
        file_size=int(row.get("file_size") or 0),
        duration_seconds=row.get("duration_seconds"),
        modified_at=str(row.get("modified_at") or ""),
    )


class _UnionFind:
    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def add(self, item: str) -> None:
        self._parent.setdefault(item, item)

    def find(self, item: str) -> str:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def find_duplicate_groups(rows: Iterable[dict[str, Any]]) -> list[DuplicateGroup]:
    """
    Group videos that are likely to be the same asset stored more than once.

    Three signals link two videos, strongest first:
      1. same normalized name, same size and same duration  -> "Très élevée"
      2. same size and same duration (renamed copy)         -> "Élevée"
      3. same normalized name only (re-encoded copy)        -> "Moyenne"

    Signals are merged transitively, so a file that matches one sibling by name
    and another by size lands in a single group rather than being split across
    two. The group is reported at the strongest signal that links any pair
    inside it at the *weakest* level, so a group held together by a single
    name match is never advertised as a certain duplicate.

    Videos with an unknown size (0) never match on size alone, otherwise every
    unmeasured file would collide.
    """
    members = {member.file_id: member for member in (_to_member(row) for row in rows) if member.file_id}

    buckets: list[tuple[int, dict[Any, list[str]]]] = [
        (_TIER_NAME_SIZE_DURATION, defaultdict(list)),
        (_TIER_SIZE_DURATION, defaultdict(list)),
        (_TIER_NAME, defaultdict(list)),
    ]
    name_size_duration, size_duration, by_name = (bucket for _, bucket in buckets)

    for file_id, member in members.items():
        name_key = normalize_for_comparison(member.file_name)
        bucket = _duration_bucket(member.duration_seconds)
        if name_key:
            by_name[name_key].append(file_id)
        if member.file_size > 0:
            size_duration[(member.file_size, bucket)].append(file_id)
            if name_key:
                name_size_duration[(name_key, member.file_size, bucket)].append(file_id)

    union = _UnionFind()
    for file_id in members:
        union.add(file_id)

    # Strongest tier that links each pair, tracked per component root afterwards.
    linked_tier: dict[str, int] = {}
    for tier, bucket in buckets:
        for candidates in bucket.values():
            if len(candidates) < 2:
                continue
            first = candidates[0]
            for other in candidates[1:]:
                union.union(first, other)
            for file_id in candidates:
                linked_tier[file_id] = min(linked_tier.get(file_id, tier), tier)

    components: dict[str, list[str]] = defaultdict(list)
    for file_id in members:
        if file_id in linked_tier:
            components[union.find(file_id)].append(file_id)

    groups: list[DuplicateGroup] = []
    for root, file_ids in components.items():
        if len(file_ids) < 2:
            continue
        # Report the group at its weakest link: the least well attached member
        # decides how much the whole group can be trusted.
        tier = max(linked_tier[file_id] for file_id in file_ids)
        reason, confidence = _TIER_LABELS[tier]
        group_members = sorted(
            (members[file_id] for file_id in file_ids),
            key=lambda item: item.file_name.casefold(),
        )
        groups.append(
            DuplicateGroup(
                key=normalize_for_comparison(members[root].file_name) or members[root].file_id,
                reason=reason,
                confidence=confidence,
                members=group_members,
            )
        )

    groups.sort(key=lambda group: (-group.wasted_bytes, group.key))
    return groups
