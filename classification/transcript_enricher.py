"""Déduction des métadonnées éditoriales à partir du texte transcrit.

`christian_enricher` travaille sur le nom de fichier et le dossier. Sur ce
corpus, la moitié des noms sont des identifiants Drive opaques
(`0000001-I3Mzg5...mp4`) : il n'y a rien à en tirer. Ce module lit la
transcription à la place.

Deux exigences ont dicté la forme :

**Traçable.** Chaque terme retenu sort avec la citation horodatée qui l'a
produit. `video_lexicon_terms` a les colonnes `source`, `confidence` et
`evidence` — un opérateur doit pouvoir contester une étiquette et voir
sur quoi elle repose.

**Tolérant à un texte abîmé.** Les transcriptions du corpus rendent environ
un tiers des mots prononcés et détruisent les noms propres. On ne peut donc
pas exiger une occurrence exacte : le score monte avec le nombre de mentions
*et* avec leur étalement dans la durée, parce qu'un thème réellement traité
revient tout au long du message, alors qu'un faux positif est ponctuel.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field

# ── Vocabulaire ─────────────────────────────────────────────────────────────
# Bilingue : l'orateur parle anglais, l'interprète français, et les deux
# apparaissent selon la langue forcée à la transcription.

THEMES: dict[str, list[str]] = {
    "Prayer": ["prayer", "pray", "praying", "intercession", "fasting",
               "priere", "prier", "prions", "intercession", "jeune"],
    "Faith": ["faith", "believe", "believer", "trust",
              "foi", "croire", "croyant", "confiance"],
    "Grace": ["grace", "mercy", "merciful", "misericorde"],
    "Salvation": ["salvation", "saved", "gospel", "born again", "repent",
                  "repentance", "sin", "sinner", "sins",
                  "salut", "sauve", "evangile", "repentir", "peche", "pecheur"],
    "Worship": ["worship", "praise", "adoration", "louange", "adorer"],
    "Holy Spirit": ["holy spirit", "pentecost", "saint esprit", "pentecote"],
    "Revival": ["revival", "awakening", "reveil"],
    "Healing": ["healing", "healed", "heal", "cured", "deliverance", "sick",
                "sickness", "guerison", "gueri", "guerir", "delivrance", "malade"],
    "Spiritual Warfare": ["warfare", "stronghold", "bondage", "devil", "satan",
                          "combat", "forteresse", "esclavage", "diable"],
    "Leadership": ["leadership", "pastor", "elder", "ministry",
                   "pasteur", "ancien", "ministere"],
    "Holiness": ["holiness", "sanctification", "purity", "saintete", "purete"],
    "End Times": ["end times", "last days", "tribulation", "derniers jours"],
    "Marriage": ["marriage", "married", "marry", "wife", "husband", "wedding",
                 "mariage", "marier", "epouse", "epoux", "femme", "mari"],
    "Cross": ["cross", "blood of jesus", "crucified", "atonement",
              "croix", "sang de jesus", "crucifie"],
}

TEACHING_TYPES: dict[str, list[str]] = {
    "Sermon": ["sermon", "message", "preach", "preaching", "predication"],
    "Bible Study": ["bible study", "verse by verse", "exposition", "etude biblique"],
    "Conference": ["conference", "convention", "crusade", "campagne"],
    "Worship Session": ["worship session", "song", "hymn", "cantique", "chant"],
    "Prayer Session": ["prayer session", "let us pray", "prions ensemble"],
    "Testimony": ["testimony", "testify", "my story", "temoignage", "temoigner"],
    "Interview": ["interview", "question and answer", "entretien"],
}

# Livres bibliques, anglais et français, pour la détection de références.
BOOKS: dict[str, list[str]] = {
    "Genesis": ["genesis", "genese"], "Exodus": ["exodus", "exode"],
    "Leviticus": ["leviticus", "levitique"], "Numbers": ["numbers", "nombres"],
    "Deuteronomy": ["deuteronomy", "deuteronome"], "Joshua": ["joshua", "josue"],
    "Judges": ["judges", "juges"], "Ruth": ["ruth"], "Samuel": ["samuel"],
    "Kings": ["kings", "rois"], "Chronicles": ["chronicles", "chroniques"],
    "Ezra": ["ezra", "esdras"], "Nehemiah": ["nehemiah", "nehemie"],
    "Esther": ["esther"], "Job": ["job"], "Psalms": ["psalm", "psalms", "psaume", "psaumes"],
    "Proverbs": ["proverbs", "proverbes"], "Ecclesiastes": ["ecclesiastes", "ecclesiaste"],
    "Isaiah": ["isaiah", "esaie", "isaie"], "Jeremiah": ["jeremiah", "jeremie"],
    "Ezekiel": ["ezekiel", "ezechiel"], "Daniel": ["daniel"], "Hosea": ["hosea", "osee"],
    "Joel": ["joel"], "Amos": ["amos"], "Jonah": ["jonah", "jonas"],
    "Micah": ["micah", "michee"], "Habakkuk": ["habakkuk", "habacuc"],
    "Zechariah": ["zechariah", "zacharie"], "Malachi": ["malachi", "malachie"],
    "Matthew": ["matthew", "matthieu"], "Mark": ["mark", "marc"],
    "Luke": ["luke", "luc"], "John": ["john", "jean"], "Acts": ["acts", "actes"],
    "Romans": ["romans", "romains"], "Corinthians": ["corinthians", "corinthiens"],
    "Galatians": ["galatians", "galates"], "Ephesians": ["ephesians", "ephesiens"],
    "Philippians": ["philippians", "philippiens"], "Colossians": ["colossians", "colossiens"],
    "Thessalonians": ["thessalonians", "thessaloniciens"], "Timothy": ["timothy", "timothee"],
    "Titus": ["titus", "tite"], "Hebrews": ["hebrews", "hebreux"], "James": ["james", "jacques"],
    "Peter": ["peter", "pierre"], "Jude": ["jude"],
    "Revelation": ["revelation", "apocalypse"],
}

# Épisodes bibliques reconnaissables au récit, quand la référence chiffrée
# n'est jamais prononcée. Chaque entrée exige plusieurs indices concordants :
# un seul mot ne suffit pas à conclure.
NARRATIVES: dict[str, tuple[str, list[str], int]] = {
    "Pool of Bethesda": ("John 5", ["bethesda", "pool", "colonnades", "porches",
                                    "paralyzed", "thirty-eight", "mat", "stirred",
                                    "piscine", "portiques", "paralytique", "natte"], 3),
    "Prodigal Son": ("Luke 15", ["prodigal", "younger son", "famine", "fattened calf",
                                 "prodigue", "veau gras"], 2),
    "Good Samaritan": ("Luke 10", ["samaritan", "jericho", "robbers", "innkeeper",
                                   "samaritain", "brigands"], 2),
    "Great Commission": ("Matthew 28", ["make all nations disciples", "make disciples",
                                        "all nations", "faites des disciples"], 1),
    "New Birth": ("John 3", ["born again", "nicodemus", "born of water",
                             "nouvelle naissance", "nicodeme"], 2),
}

REF_PATTERN = re.compile(r"\b(\d+)\s*[:.]\s*(\d+)\b")

# Ces noms de livres sont aussi des mots courants ou des prenoms : « a certain
# job », « mark my words », « the acts of the apostles ». On ne les retient que
# si un chapitre:verset est prononce dans le meme segment.
AMBIGUS = {"Job", "Mark", "James", "Acts", "Jude", "Ruth", "Amos", "Kings",
           "Numbers", "Judges", "Titus", "Peter", "John", "Joel", "Daniel", "Esther"}

SEUIL_MENTIONS = 3          # en deçà, un terme isolé n'est pas retenu…
SEUIL_MENTIONS_ETALE = 2    # …sauf s'il revient dans plusieurs parties du message
BUCKET_SECONDES = 600       # granularité de l'étalement : 10 minutes


def _plier(texte: str) -> str:
    """Minuscules sans accents : le modèle les rend de façon instable."""
    sans = unicodedata.normalize("NFD", texte.lower())
    return "".join(c for c in sans if unicodedata.category(c) != "Mn")


@dataclass(slots=True)
class Terme:
    """Un terme retenu, avec ce qui permet de le contester."""
    category: str
    term: str
    confidence: float
    mentions: int
    spread: int
    evidence: str

    def as_row(self, source: str = "transcript") -> dict:
        return {
            "category": self.category, "term": self.term, "source": source,
            "confidence": round(self.confidence, 3), "evidence": self.evidence,
        }


@dataclass(slots=True)
class TranscriptEnrichment:
    main_theme: str = ""
    teaching_type: str = ""
    spiritual_themes: list[str] = field(default_factory=list)
    bible_references: list[str] = field(default_factory=list)
    biblical_topics: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    terms: list[Terme] = field(default_factory=list)
    word_count: int = 0
    covered_seconds: float = 0.0

    @property
    def is_empty(self) -> bool:
        """Vrai quand le lexique n'a rien su prouver — à passer au LLM."""
        return not self.main_theme and not self.bible_references


def _score(mentions: int, spread: int) -> float:
    """Confiance croissante avec les mentions, mais surtout avec l'étalement."""
    return min(0.95, 0.30 + 0.10 * math.log2(1 + mentions) + 0.09 * spread)


def _chercher(segments, vocabulaire: dict[str, list[str]], category: str) -> list[Terme]:
    motifs = {
        etiquette: [re.compile(rf"(?<!\w){re.escape(_plier(m))}(?!\w)") for m in mots]
        for etiquette, mots in vocabulaire.items()
    }
    trouves: list[Terme] = []

    for etiquette, regexes in motifs.items():
        mentions = 0
        buckets: set[int] = set()
        meilleur = (0, "")
        for seg in segments:
            texte = _plier(seg["text"])
            n = sum(len(r.findall(texte)) for r in regexes)
            if not n:
                continue
            mentions += n
            buckets.add(int(seg["start"] // BUCKET_SECONDES))
            if n > meilleur[0]:
                meilleur = (n, _citation(seg))
        spread = len(buckets)
        retenu = mentions >= SEUIL_MENTIONS or (mentions >= SEUIL_MENTIONS_ETALE and spread >= 2)
        if retenu:
            trouves.append(Terme(category, etiquette, _score(mentions, spread),
                                 mentions, spread, meilleur[1]))

    trouves.sort(key=lambda t: (-t.confidence, -t.mentions, t.term))
    return trouves


def _citation(seg) -> str:
    h, reste = divmod(int(seg["start"]), 3600)
    m, s = divmod(reste, 60)
    texte = " ".join(seg["text"].split())
    if len(texte) > 180:
        texte = texte[:177] + "…"
    return f"[{h:02d}:{m:02d}:{s:02d}] {texte}"


def _references(segments) -> list[Terme]:
    """Références chiffrées (« John 5:8 ») et épisodes reconnus au récit."""
    livres = {
        nom: [re.compile(rf"(?<!\w){re.escape(_plier(v))}(?!\w)") for v in variantes]
        for nom, variantes in BOOKS.items()
    }
    trouves: list[Terme] = []

    compte: dict[str, list] = {}
    for seg in segments:
        texte = _plier(seg["text"])
        for nom, regexes in livres.items():
            if not any(r.search(texte) for r in regexes):
                continue
            chapitre = REF_PATTERN.search(seg["text"])
            if chapitre is None and nom in AMBIGUS:
                continue        # « a certain job » n'est pas le livre de Job
            ref = f"{nom} {chapitre.group(1)}:{chapitre.group(2)}" if chapitre else nom
            entree = compte.setdefault(ref, [0, set(), ""])
            entree[0] += 1
            entree[1].add(int(seg["start"] // BUCKET_SECONDES))
            if not entree[2]:
                entree[2] = _citation(seg)

    for ref, (mentions, buckets, evidence) in compte.items():
        if mentions >= SEUIL_MENTIONS or (mentions >= SEUIL_MENTIONS_ETALE and len(buckets) >= 2):
            trouves.append(Terme("scripture", ref, _score(mentions, len(buckets)),
                                 mentions, len(buckets), evidence))

    # Épisodes : plusieurs indices concordants exigés.
    entier = " ".join(_plier(s["text"]) for s in segments)
    for episode, (reference, indices, minimum) in NARRATIVES.items():
        touches = [i for i in indices if re.search(rf"(?<!\w){re.escape(i)}(?!\w)", entier)]
        if len(touches) < minimum:
            continue
        meilleur = (0, "")
        for seg in segments:
            texte = _plier(seg["text"])
            n = sum(1 for i in touches if i in texte)
            if n > meilleur[0]:
                meilleur = (n, _citation(seg))
        evidence = meilleur[1]
        conf = min(0.9, 0.45 + 0.12 * len(touches))
        trouves.append(Terme("scripture", reference, conf, len(touches), len(touches), evidence))
        trouves.append(Terme("topic", episode, conf, len(touches), len(touches), evidence))

    trouves.sort(key=lambda t: (-t.confidence, t.term))
    return trouves


def _normaliser(seg) -> dict:
    """Accepte indifféremment un dict ou un `knowledge.chunking.Segment`."""
    if isinstance(seg, dict):
        return {"start": float(seg.get("start") or 0.0),
                "end": float(seg.get("end") or 0.0),
                "text": str(seg.get("text") or "")}
    return {"start": float(getattr(seg, "start", 0.0)),
            "end": float(getattr(seg, "end", 0.0)),
            "text": str(getattr(seg, "text", ""))}


def enrich_from_transcript(segments) -> TranscriptEnrichment:
    """Déduit les métadonnées éditoriales d'une liste de segments transcrits.

    `segments` : itérable de dicts `{"start": float, "end": float, "text": str}`
    — la forme de `transcript_segments`.
    """
    segments = [_normaliser(s) for s in segments]
    segments = [s for s in segments if s["text"].strip()]
    if not segments:
        return TranscriptEnrichment()

    themes = _chercher(segments, THEMES, "theme")
    types = _chercher(segments, TEACHING_TYPES, "teaching_type")
    refs = _references(segments)

    ecriture = [t for t in refs if t.category == "scripture"]
    sujets = [t for t in refs if t.category == "topic"]

    return TranscriptEnrichment(
        main_theme=themes[0].term if themes else "",
        teaching_type=types[0].term if types else "",
        spiritual_themes=[t.term for t in themes],
        bible_references=[t.term for t in ecriture],
        biblical_topics=[t.term for t in sujets],
        keywords=[t.term for t in themes[:5]] + [t.term for t in sujets[:3]],
        terms=themes + types + refs,
        word_count=sum(len(s["text"].split()) for s in segments),
        covered_seconds=sum(s["end"] - s["start"] for s in segments),
    )
