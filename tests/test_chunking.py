"""Découpage bilingue. La règle centrale — ne jamais couper à cheval sur un
changement de langue — est ce qui distingue ce corpus d'un corpus ordinaire.
"""

from __future__ import annotations

import pytest

from knowledge.chunking import (
    Segment,
    chunk_segments,
    infer_speaker_roles,
    language_runs,
)


def seg(i, lang, text="x" * 100, dur=10.0):
    return Segment(start=i * dur, end=(i + 1) * dur, text=text, language=lang)


def alternance(n, langues=("fr", "en"), texte="x" * 100):
    """Interprétation consécutive : les langues alternent segment par segment."""
    return [seg(i, langues[i % len(langues)], texte) for i in range(n)]


class TestPlagesDeLangue:
    def test_langue_unique(self):
        assert list(language_runs([seg(0, "fr"), seg(1, "fr")])) == [(0, 2, "fr")]

    def test_alternance(self):
        runs = list(language_runs(alternance(4)))
        assert runs == [(0, 1, "fr"), (1, 2, "en"), (2, 3, "fr"), (3, 4, "en")]

    def test_liste_vide(self):
        assert list(language_runs([])) == []


class TestRegleCentrale:
    def test_aucun_fragment_ne_melange_deux_langues(self):
        # Un fragment bilingue produit un plongement incohérent : il ne
        # ressemble à rien et ne remonte jamais en recherche.
        chunks = chunk_segments(alternance(20), chunk_size=5000, overlap=0)
        assert len(chunks) == 20, "des segments de langues différentes ont fusionné"
        for c in chunks:
            assert c.language in {"fr", "en"}

    def test_les_segments_dune_meme_langue_se_regroupent(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(10)]
        chunks = chunk_segments(segs, chunk_size=450, overlap=0)
        assert 1 < len(chunks) < 10

    def test_la_langue_voyage_avec_le_fragment(self):
        # Sans cette étiquette, impossible de dédupliquer un enseignement
        # de sa traduction à la récupération.
        for c in chunk_segments(alternance(6), chunk_size=5000, overlap=0):
            assert c.language


class TestDecoupage:
    def test_respecte_la_taille_visee(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(20)]
        chunks = chunk_segments(segs, chunk_size=500, overlap=0)
        for c in chunks[:-1]:
            assert len(c.text) <= 700

    def test_un_segment_plus_long_que_la_cible_reste_entier(self):
        # On ne coupe pas au milieu d'une phrase pour gagner des caractères.
        long = Segment(start=0, end=60, text="y" * 3000, language="fr")
        chunks = chunk_segments([long], chunk_size=500, overlap=0)
        assert len(chunks) == 1 and len(chunks[0].text) == 3000

    def test_recouvrement(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(12)]
        sans = chunk_segments(segs, chunk_size=500, overlap=0)
        avec = chunk_segments(segs, chunk_size=500, overlap=200)
        assert len(avec) >= len(sans)

    def test_recouvrement_invalide(self):
        with pytest.raises(ValueError):
            chunk_segments([seg(0, "fr")], chunk_size=100, overlap=100)

    def test_segments_vides_ignores(self):
        segs = [seg(0, "fr"), Segment(1, 2, "   ", "fr"), seg(2, "fr")]
        assert all(c.text.strip() for c in chunk_segments(segs))

    def test_liste_vide(self):
        assert chunk_segments([]) == []


class TestProvenance:
    def test_les_horodatages_encadrent_le_fragment(self):
        # C'est ce qui permet de citer « 08:52 → 09:58 » dans une réponse.
        segs = [seg(i, "fr", "x" * 100) for i in range(6)]
        for c in chunk_segments(segs, chunk_size=250, overlap=0):
            assert c.start_time < c.end_time
            assert c.duration > 0

    def test_les_indices_de_segments_sont_conserves(self):
        segs = [seg(i, "fr", "x" * 100) for i in range(6)]
        for c in chunk_segments(segs, chunk_size=250, overlap=0):
            assert c.segment_indices

    def test_le_premier_fragment_part_du_debut(self):
        segs = [seg(i, "fr") for i in range(4)]
        assert chunk_segments(segs, chunk_size=5000)[0].start_time == 0.0


class TestRolesDeLocuteur:
    def test_lalternance_designe_orateur_et_interprete(self):
        segs = infer_speaker_roles(alternance(6))
        assert segs[0].speaker_role == "primary"
        assert segs[1].speaker_role == "interpreter"
        assert segs[2].speaker_role == "primary"

    def test_une_seule_langue_ne_permet_rien_de_deduire(self):
        # L'heuristique repose entièrement sur l'alternance : sans deux
        # langues, elle doit se taire plutôt que d'inventer.
        segs = infer_speaker_roles([seg(i, "fr") for i in range(4)])
        assert all(s.speaker_role == "unknown" for s in segs)

    def test_le_role_voyage_avec_le_fragment(self):
        chunks = chunk_segments(alternance(6), chunk_size=5000, overlap=0)
        assert {c.speaker_role for c in chunks} == {"primary", "interpreter"}
