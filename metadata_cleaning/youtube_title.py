"""
Build a publishable video title from what is already known about a video.

Format, validated against the real library:

    Sujet | Intervenant — Lieu Année (Partie)

The subject comes first because it is what a reader's eye lands on and what
YouTube truncates last. Everything the file name reveals along the way - date,
place, speaker, part number, source medium - is returned separately so it can
populate the structured fields rather than being buried in a string.

Nothing is invented: when a name carries no usable subject the proposal is
empty, and the title is meant to be regenerated later from the metadata a human
will have entered.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# YouTube rejects titles longer than this.
MAX_TITLE_LENGTH = 100
# Beyond this, the tail is usually clipped in listings, so optional context is
# dropped rather than shown truncated.
TARGET_TITLE_LENGTH = 80
# Au-dela, un mot vient forcement de mots colles dans le nom d'origine.
_GLUED_WORD_LENGTH = 18

_NOISE = [
    r"r[ée]alis[ée]e? avec clipchamp", r"clipchamp", r"rogner\s*[\d:\s]+",
    r"\bmovie\s*\d*\b", r"\bhd\b", r"\bfhd\b", r"\bfinal\b", r"\bok\b",
    r"\bbis\b", r"\bcopie\b", r"\bcopy\b", r"\b\d{3,4}p\b", r"\bx?26[45]\b",
    r"\(\s*\d+\s*\)", r"\d{2}h\d{2}m\d{2}",
]

_RE_INTERNAL_ID = re.compile(r"(?<![0-9A-Za-z])CHR[-_ ]?VID[-_ ]?\d{1,6}(?![0-9A-Za-z])", re.I)
_RE_SUPPORT = re.compile(r"\b(VHS|K7)\s*n?[°ºo]?\s*(\d+)", re.I)
_RE_LOT = re.compile(r"(?<!\d)(\d{4})\s+(?=\S)")
_RE_DATE_ISO = re.compile(r"\b((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})\b")
_RE_DATE_SPACED = re.compile(r"\b((?:19|20)\d{2})\s+(\d{1,2})\s+(\d{1,2})\b")
_RE_YEAR = re.compile(r"\b((?:19|20)\d{2})\b")
_RE_PART = re.compile(r"\b(?:part(?:ie)?|pt)\s*(\d{1,2})\b", re.I)
_RE_NUMBER = re.compile(r"\bn\s*[°ºo]\s*(\d{1,2})\b", re.I)
_RE_CONVENTION = re.compile(r"\b(\d)\s*(?:[eè]re|[eè]me|e)?\s*conven\w*", re.I)
_RE_WORD_HYPHEN = re.compile(r"(?<=[\w\u00c0-\u00ff])-(?=[\w\u00c0-\u00ff])")

PLACES = [
    "Kigali", "Yaoundé", "Yaounde", "Lagos", "Abidjan", "Nkolbisson", "Gahini",
    "Bandjoun", "Bamako", "Rwanda", "Douala", "Cameroun", "Inde",
]

# Abbreviations used in file names, expanded for a public audience.
SPEAKERS = {
    "ztf": "Zacharias Tanee Fomum",
    "damase": "Damase",
    "tsafack": "Tsafack",
    "mbafor": "Mbafor",
}

CONTENT_TYPES = {
    "enseignement": "Enseignement", "louange": "Louange", "chant": "Chant",
    "danse": "Danse", "témoignage": "Témoignage", "temoignage": "Témoignage",
    "témoigange": "Témoignage", "prière": "Prière", "priere": "Prière",
    "adoration": "Adoration", "compile": "Compilation", "send off": "Send-off",
    "ordination": "Ordination", "guérison": "Guérison", "guerison": "Guérison",
}

_MINOR_WORDS = {
    "de", "du", "des", "la", "le", "les", "et", "a", "à", "au", "aux", "un",
    "une", "dans", "pour", "par", "sur", "en", "d", "l", "the", "of", "est",
    "ses", "son", "sa", "ne", "pas", "que", "qui",
}
# Organisational words that identify the event, never the subject.
_NON_SUBJECT = {
    "cmci", "cmfi", "upm", "ump", "afrique", "est", "convention", "conven",
    "reunion", "commune",
}


@dataclass(slots=True)
class TitleProposal:
    title: str = ""
    confidence: str = "none"  # "high" | "partial" | "none"
    year: str = ""
    event_date: str = ""
    location: str = ""
    speaker: str = ""
    content_type: str = ""
    session_number: str = ""
    source_medium: str = ""
    notes: list[str] = field(default_factory=list)


def _fix_case(text: str) -> str:
    """Shouting file names read as spam on YouTube, so ALL CAPS is folded."""
    letters = [char for char in text if char.isalpha()]
    if letters and sum(char.isupper() for char in letters) / len(letters) > 0.6:
        words = text.lower().split()
        text = " ".join(
            word if (index and word in _MINOR_WORDS) else (word[:1].upper() + word[1:])
            for index, word in enumerate(words)
            if word
        )
    return text[:1].upper() + text[1:] if text else text


_VOWELS = set("aeiouy\u00e0\u00e2\u00e9\u00e8\u00ea\u00eb\u00ee\u00ef\u00f4\u00f6\u00f9\u00fb\u00fc")


def _looks_encoded(text: str) -> bool:
    """
    Detect a machine-generated name such as "0000001-I3Mzg5YmJiYmJiY2xiaGRpZWp".

    Base64-ish blobs survive every cleaning rule and would otherwise be proposed
    as a title with full confidence. Two signals give them away: capitals in the
    middle of a token, which French words never have, and a vowel ratio no real
    word sustains.
    """
    for token in re.findall(r"[A-Za-z0-9]{8,}", text):
        letters = [char for char in token if char.isalpha()]
        if len(letters) < 6:
            continue
        vowel_ratio = sum(char.lower() in _VOWELS for char in letters) / len(letters)
        internal_capitals = sum(1 for char in token[1:] if char.isupper())
        if internal_capitals >= 2 and vowel_ratio < 0.45:
            return True
        if len(letters) >= 10 and vowel_ratio < 0.28:
            return True
    return False


def _meaningful_words(text: str) -> list[str]:
    return [
        word
        for word in re.split(r"[^a-z\u00e0-\u00ff]+", text.lower())
        if len(word) > 2 and word not in _MINOR_WORDS and word not in _NON_SUBJECT
    ]


def _parse(file_name: str) -> tuple[TitleProposal, str, str]:
    proposal = TitleProposal()
    text = re.sub(r"\.[A-Za-z0-9]{2,4}$", "", file_name)
    # The internal ID did its job at linking time; it has no place in a title.
    text = _RE_INTERNAL_ID.sub(" ", text)
    # Underscores and dots must go before anything else: they are word
    # characters, so they silently break every \b anchor below.
    text = re.sub(r"[_.]+", " ", text)
    text = re.sub(r"\bN-(?=[A-Za-z\u00c0-\u00ff])", "N'", text)
    # A name written in kebab-case uses hyphens as separators; a lone hyphen
    # ("Jésus-Christ") is a real one.
    if len(_RE_WORD_HYPHEN.findall(text)) >= 2:
        text = _RE_WORD_HYPHEN.sub(" ", text)
    text = re.sub(r"\s*[-–]\s*", " ", text)
    # "Lagos1998" : sans espace, le \b devant l'annee n'existe pas.
    text = re.sub(r"(?<=[A-Za-z\u00c0-\u00ff])(?=(?:19|20)\d{2}(?!\d))", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    match = _RE_SUPPORT.search(text)
    if match:
        proposal.source_medium = f"{match.group(1).upper()} n°{int(match.group(2))}"
        text = _RE_SUPPORT.sub(" ", text)

    match = _RE_DATE_ISO.search(text) or _RE_DATE_SPACED.search(text)
    if match:
        proposal.event_date = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
        proposal.year = match.group(1)
        text = text.replace(match.group(0), " ")

    match = _RE_PART.search(text) or _RE_NUMBER.search(text)
    if match:
        proposal.session_number = f"{int(match.group(1)):02d}"
    text = _RE_NUMBER.sub(" ", _RE_PART.sub(" ", text))

    for pattern in _NOISE:
        text = re.sub(pattern, " ", text, flags=re.I)

    match = _RE_LOT.search(text)
    if match:
        text = text.replace(match.group(0), " ", 1)

    if not proposal.year:
        match = _RE_YEAR.search(text)
        if match:
            proposal.year = match.group(1)
    text = _RE_YEAR.sub(" ", text)

    lowered = text.lower()
    for place in PLACES:
        if place.lower() in lowered:
            proposal.location = "Yaoundé" if place.lower().startswith("yaound") else place
            break
    for abbreviation, full_name in SPEAKERS.items():
        if re.search(rf"\b{abbreviation}\b", lowered):
            proposal.speaker = full_name
            break
    for keyword, label in CONTENT_TYPES.items():
        if keyword in lowered:
            proposal.content_type = label
            break

    match = _RE_CONVENTION.search(text)
    event = f"{match.group(1)}e convention" if match else ("Convention" if "conven" in lowered else "")

    subject = _RE_CONVENTION.sub(" ", text)
    subject = re.sub(r"\bconven\w*\b", " ", subject, flags=re.I)
    subject = re.sub(r"\bafrique( de l'?| )?est\b", " ", subject, flags=re.I)
    for word in PLACES + list(SPEAKERS) + list(_NON_SUBJECT):
        subject = re.sub(rf"\b{re.escape(word)}\b", " ", subject, flags=re.I)
    subject = re.sub(r"[^\w\u00c0-\u00ff'’ !?-]", " ", subject)
    subject = re.sub(r"\b\d+\b", " ", subject)
    subject = re.sub(r"\s+", " ", subject).strip(" -")
    # Un sujet finissant par "par", "de", "et"... reste en suspens une fois le
    # reste du nom retire. On coupe ces mots-outils orphelins.
    words = subject.split()
    while words and words[-1].lower().strip("'’") in _MINOR_WORDS:
        words.pop()
    subject = " ".join(words)
    return proposal, _fix_case(subject), event


def _assemble(subject: str, proposal: TitleProposal, suffix: str = "") -> str:
    """
    Compose the title, shedding optional context rather than cutting mid-word.

    Order of sacrifice: year, then place, then the speaker. The subject and the
    part number always survive - without them a viewer cannot tell two episodes
    of a series apart.
    """
    def build(with_year: bool, with_place: bool, with_speaker: bool) -> str:
        context = " ".join(
            part
            for part in [proposal.location if with_place else "", proposal.year if with_year else ""]
            if part
        )
        pieces = [subject]
        if with_speaker and proposal.speaker:
            pieces.append(f"| {proposal.speaker}")
        if context:
            pieces.append(f"— {context}")
        if suffix:
            pieces.append(suffix)
        elif proposal.session_number:
            pieces.append(f"({int(proposal.session_number)})")
        return re.sub(r"\s+", " ", " ".join(pieces)).strip()

    for options in ((1, 1, 1), (0, 1, 1), (0, 0, 1), (0, 0, 0)):
        title = build(*options)
        if len(title) <= TARGET_TITLE_LENGTH:
            return title

    title = build(1, 1, 1)
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    title = build(0, 0, 0)
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 1].rsplit(" ", 1)[0] + "…"
        proposal.notes.append("titre tronqué à la limite YouTube")
    return title


def propose_title(
    file_name: str,
    metadata: dict[str, str] | None = None,
    source: TitleProposal | None = None,
) -> TitleProposal:
    """
    Propose a title for one video.

    metadata: structured fields already entered by a human. They always win over
    anything guessed from the file name, so a video can be re-titled properly
    once somebody has described it.

    source: the proposal for the raw video a cut came from. A cut keeps its own
    focus as the subject and inherits speaker, place and year from its source,
    which is the whole point of naming it after the original.
    """
    metadata = metadata or {}
    proposal, subject, event = _parse(file_name)

    proposal.speaker = metadata.get("speaker") or metadata.get("preacher") or proposal.speaker
    proposal.location = metadata.get("location") or proposal.location
    proposal.event_date = metadata.get("event_date") or proposal.event_date
    proposal.year = (metadata.get("event_date") or "")[:4] or proposal.year
    proposal.content_type = metadata.get("content_type") or proposal.content_type
    proposal.session_number = metadata.get("session_number") or proposal.session_number
    stated_subject = (metadata.get("main_theme") or "").strip()
    if stated_subject:
        subject = stated_subject
    elif _looks_encoded(subject):
        # Nom généré par une machine : il ne doit atteindre aucun titre, ni pour
        # une originale ni pour une découpe. Seules les métadonnées le sauveront.
        subject = ""
        proposal.notes.append("nom de fichier encodé — aucun titre déductible")

    if source is not None:
        focus = subject if _meaningful_words(subject) else (proposal.content_type or "")
        inherited = TitleProposal(
            speaker=proposal.speaker or source.speaker,
            location=proposal.location or source.location,
            year=proposal.year or source.year,
            event_date=proposal.event_date or source.event_date,
            content_type=proposal.content_type,
            session_number=proposal.session_number,
            source_medium=proposal.source_medium,
        )
        if not focus:
            # No focus of its own: fall back to the source's subject so the cut
            # is still identifiable, rather than titling it "Extrait (extrait)".
            focus = source.title.split(" | ")[0].split(" — ")[0] or "Extrait"
            inherited.confidence = "partial"
        else:
            inherited.confidence = "high" if len(_meaningful_words(focus)) >= 2 else "partial"
        part = f" {int(inherited.session_number)}" if inherited.session_number else ""
        inherited.title = _assemble(focus, inherited, suffix=f"(extrait{part})")
        return inherited

    if not _meaningful_words(subject):
        fallback = proposal.content_type or (event[:1].upper() + event[1:] if event else "")
        if fallback:
            proposal.confidence = "partial"
            proposal.title = _assemble(fallback, proposal)
        elif proposal.speaker and proposal.location:
            speaker_only = TitleProposal(
                location=proposal.location, year=proposal.year, session_number=proposal.session_number
            )
            proposal.confidence = "partial"
            proposal.title = _assemble(proposal.speaker, speaker_only)
        else:
            proposal.confidence = "none"
            proposal.notes.append(
                "aucun sujet exploitable — titre laissé vide, à régénérer après saisie des métadonnées"
            )
        return proposal

    proposal.confidence = "high" if len(_meaningful_words(subject)) >= 3 else "partial"
    if any(len(word) >= _GLUED_WORD_LENGTH for word in _meaningful_words(subject)):
        # "LaprièreetlacroixN" : mots colles dans le nom d'origine. Rien ici ne
        # peut les separer, mais le titre ne doit pas etre annonce comme pret.
        proposal.confidence = "partial"
        proposal.notes.append("mots collés dans le nom d'origine — titre à relire")
    proposal.title = _assemble(subject, proposal)
    return proposal
