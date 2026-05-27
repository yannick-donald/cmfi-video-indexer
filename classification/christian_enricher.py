from __future__ import annotations

import re
from dataclasses import dataclass

from metadata_cleaning.title_normalizer import NormalizedTitle, normalize_title


THEME_KEYWORDS: dict[str, list[str]] = {
    "Prayer": ["prayer", "pray", "intercession", "intercede", "fasting"],
    "Faith": ["faith", "believe", "trust"],
    "Grace": ["grace", "mercy"],
    "Salvation": ["salvation", "saved", "gospel", "born again", "repent"],
    "Worship": ["worship", "praise", "adoration"],
    "Holy Spirit": ["holy spirit", "spirit", "pentecost", "baptism"],
    "Revival": ["revival", "awakening"],
    "Healing": ["healing", "heal", "deliverance"],
    "Spiritual Warfare": ["warfare", "battle", "stronghold", "armor"],
    "Leadership": ["leadership", "pastor", "elder", "ministry"],
    "Holiness": ["holiness", "sanctification", "purity"],
    "End Times": ["end times", "last days", "revelation", "tribulation"],
}

TEACHING_TYPES: dict[str, list[str]] = {
    "Sermon": ["sermon", "message", "preaching"],
    "Bible Study": ["bible study", "study", "verse by verse", "exposition"],
    "Conference": ["conference", "summit", "convention"],
    "Worship Session": ["worship", "praise", "song", "session"],
    "Prayer Session": ["prayer session", "intercession", "prayer"],
    "Teaching Series": ["series", "part", "episode", "session"],
    "Testimony": ["testimony", "testimonies", "story"],
    "Interview": ["interview", "q&a", "qa", "questions"],
}

# A pragmatic (not perfect) bible reference matcher.
# It’s designed for filenames and folder names, not full natural-language parsing.
BOOKS = [
    "Genesis",
    "Exodus",
    "Leviticus",
    "Numbers",
    "Deuteronomy",
    "Joshua",
    "Judges",
    "Ruth",
    "1 Samuel",
    "2 Samuel",
    "1 Kings",
    "2 Kings",
    "1 Chronicles",
    "2 Chronicles",
    "Ezra",
    "Nehemiah",
    "Esther",
    "Job",
    "Psalm",
    "Psalms",
    "Proverbs",
    "Ecclesiastes",
    "Song of Solomon",
    "Isaiah",
    "Jeremiah",
    "Lamentations",
    "Ezekiel",
    "Daniel",
    "Hosea",
    "Joel",
    "Amos",
    "Obadiah",
    "Jonah",
    "Micah",
    "Nahum",
    "Habakkuk",
    "Zephaniah",
    "Haggai",
    "Zechariah",
    "Malachi",
    "Matthew",
    "Mark",
    "Luke",
    "John",
    "Acts",
    "Romans",
    "1 Corinthians",
    "2 Corinthians",
    "Galatians",
    "Ephesians",
    "Philippians",
    "Colossians",
    "1 Thessalonians",
    "2 Thessalonians",
    "1 Timothy",
    "2 Timothy",
    "Titus",
    "Philemon",
    "Hebrews",
    "James",
    "1 Peter",
    "2 Peter",
    "1 John",
    "2 John",
    "3 John",
    "Jude",
    "Revelation",
]

_BOOK_ALT = "|".join(sorted([re.escape(b) for b in BOOKS], key=len, reverse=True))
_BIBLE_REF_RE = re.compile(
    rf"\b(?:{_BOOK_ALT})\s+(\d{{1,3}})(?::(\d{{1,3}}))?(?:\s*[-–]\s*(\d{{1,3}}))?\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class ChristianEnrichment:
    clean_title: str
    normalized_name: str
    speaker: str
    ministry: str
    main_theme: str
    biblical_topics: list[str]
    bible_references: list[str]
    teaching_type: str
    keywords: list[str]
    semantic_tags: list[str]


def enrich_from_context(file_name: str, folder_path: str) -> ChristianEnrichment:
    norm: NormalizedTitle = normalize_title(file_name, folder_path)
    context = f"{file_name} {folder_path}".lower()

    main_theme = _infer_main_theme(context)
    teaching_type = _infer_teaching_type(context)
    bible_refs = _extract_bible_refs(file_name + " " + folder_path)

    biblical_topics: list[str] = []
    if "holy spirit" in context or "spirit" in context:
        biblical_topics.append("Holy Spirit")
    if "kingdom" in context:
        biblical_topics.append("Kingdom of God")
    if "gospel" in context:
        biblical_topics.append("Gospel")
    if "resurrection" in context:
        biblical_topics.append("Resurrection")
    if "covenant" in context:
        biblical_topics.append("Covenant")
    if "redemption" in context or "redeem" in context:
        biblical_topics.append("Redemption")

    speaker = norm.speaker
    ministry = _infer_ministry(folder_path)

    semantic_tags = sorted(
        {
            *( [main_theme] if main_theme else [] ),
            *( [teaching_type] if teaching_type else [] ),
            *biblical_topics,
        }
    )

    return ChristianEnrichment(
        clean_title=norm.clean_title,
        normalized_name=norm.normalized_name,
        speaker=speaker,
        ministry=ministry,
        main_theme=main_theme,
        biblical_topics=biblical_topics,
        bible_references=bible_refs,
        teaching_type=teaching_type,
        keywords=norm.keywords,
        semantic_tags=semantic_tags,
    )


def _infer_main_theme(text: str) -> str:
    for theme, keys in THEME_KEYWORDS.items():
        for k in keys:
            if k in text:
                return theme
    return ""


def _infer_teaching_type(text: str) -> str:
    for ttype, keys in TEACHING_TYPES.items():
        for k in keys:
            if k in text:
                return ttype
    return ""


def _extract_bible_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in _BIBLE_REF_RE.finditer(text):
        book = m.group(0).split()[0]
        # Keep original substring for readability
        refs.append(m.group(0).strip())
    # Deduplicate while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        key = r.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out[:25]


def _infer_ministry(folder_path: str) -> str:
    if not folder_path:
        return ""
    parts = [p for p in folder_path.split("/") if p]
    # heuristic: the top-level folder often encodes a ministry/org
    if parts:
        candidate = parts[0].strip()
        if len(candidate) <= 60:
            return candidate
    return ""

