"""Rapprochement d'un passage et de sa traduction.

Le corpus est de l'interprétation **consécutive** : chaque idée existe deux
fois, une fois dans la bouche de l'orateur, une fois dans celle de son
interprète. Les deux langues étant indexées à parité, une recherche remonte
volontiers les deux, et le même enseignement occupe alors deux fois la fenêtre
de contexte.

Le signal n'est **pas** le recouvrement temporel. En interprétation
consécutive, l'interprète parle *après* l'orateur : les deux passages ne se
chevauchent jamais, ils se suivent. Chercher un recouvrement ne trouve donc
rien — c'est le piège de cette mécanique, et il ne se voit qu'à l'essai.

Le vrai signal est la **succession** : deux passages d'une même source, dans
deux langues différentes, séparés par un silence court et de durées
comparables.

La succession seule ne suffit pourtant pas. Dans la suite FR₁ EN₁ FR₂ EN₂,
EN₁ jouxte FR₁ *et* FR₂ : rien dans les horodatages ne dit lequel des deux il
traduit. C'est le rôle du locuteur qui tranche — le découpage l'a déjà déduit
de l'alternance des langues — et l'on n'apparie qu'un original suivi de son
interprétation, jamais l'inverse.

Tout ceci reste une heuristique. Elle regroupe ce qui se ressemble beaucoup ;
elle ne jette jamais rien, donc son pire défaut est de laisser passer un
doublon, pas de perdre un passage.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from database.knowledge_repository import ChunkHit

# Silence maximal entre un passage et sa traduction. L'interprète reprend vite,
# mais rarement à la seconde près.
ECART_MAX_SECONDES = 12.0

# Une traduction dure à peu près aussi longtemps que l'original. Au-delà de ce
# rapport, il s'agit d'autre chose.
RAPPORT_DUREE_MAX = 2.5

# Pour l'interprétation simultanée, où les deux voix se superposent.
SEUIL_RECOUVREMENT = 0.5


@dataclass(slots=True)
class Result:
    """Un résultat, avec ses éventuelles traductions."""

    hit: ChunkHit
    variants: list[ChunkHit] = field(default_factory=list)

    @property
    def languages(self) -> list[str]:
        return sorted({self.hit.language, *(v.language for v in self.variants)} - {""})


def _horodate(h: ChunkHit) -> bool:
    return h.start_time is not None and h.end_time is not None


def _duree(h: ChunkHit) -> float:
    return max((h.end_time or 0) - (h.start_time or 0), 0.0)


def _recouvrement(a: ChunkHit, b: ChunkHit) -> float:
    debut, fin = max(a.start_time, b.start_time), min(a.end_time, b.end_time)
    commun = max(fin - debut, 0.0)
    plus_court = min(_duree(a), _duree(b))
    return commun / plus_court if plus_court > 0 else 0.0


def _ecart(a: ChunkHit, b: ChunkHit) -> float:
    """Silence entre les deux passages. Zéro s'ils se touchent ou se croisent."""
    if a.start_time <= b.start_time:
        premier, second = a, b
    else:
        premier, second = b, a
    return max(second.start_time - premier.end_time, 0.0)


def sont_traductions(a: ChunkHit, b: ChunkHit) -> bool:
    """Dit si deux passages sont vraisemblablement le même propos, traduit."""
    if a.source_uid != b.source_uid or not (_horodate(a) and _horodate(b)):
        return False
    # Deux passages dans la même langue ne sont pas l'un la traduction de l'autre.
    if a.language and b.language and a.language == b.language:
        return False

    da, db = _duree(a), _duree(b)
    if da <= 0 or db <= 0:
        return False
    if max(da, db) / min(da, db) > RAPPORT_DUREE_MAX:
        return False

    # Interprétation simultanée : les voix se superposent.
    if _recouvrement(a, b) >= SEUIL_RECOUVREMENT:
        return True

    if _ecart(a, b) > ECART_MAX_SECONDES:
        return False

    # Interprétation consécutive. Quand les rôles sont connus, on exige
    # l'ordre original → interprétation ; sans quoi EN₁ s'apparierait aussi
    # bien à FR₂, qui n'est pas sa traduction mais le propos suivant.
    premier, second = (a, b) if a.start_time <= b.start_time else (b, a)
    if premier.speaker_role in ("primary", "interpreter") and \
       second.speaker_role in ("primary", "interpreter"):
        return premier.speaker_role == "primary" and second.speaker_role == "interpreter"
    return True


def deduplicate(hits: list[ChunkHit]) -> list[Result]:
    """Regroupe chaque passage avec sa traduction.

    L'ordre d'entrée est préservé : le premier d'un groupe, donc le mieux
    classé, en devient le représentant. Rien n'est jeté — la variante reste
    disponible, car c'est peut-être elle que l'utilisateur voudra citer.
    """
    resultats: list[Result] = []
    for hit in hits:
        for res in resultats:
            if sont_traductions(res.hit, hit):
                res.variants.append(hit)
                break
        else:
            resultats.append(Result(hit=hit))
    return resultats
