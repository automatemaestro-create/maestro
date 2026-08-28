"""La carte du corpus de documentation, et l'extraction d'une section (#763, lot 1 de #748).

Le lot construit ce sur quoi le répondeur du lot 2 s'appuiera : une **carte** du
corpus assez petite pour tenir dans un prompt, et l'**extraction** exacte d'une
section que le modèle aura nommée. Trois critères, et cette suite est écrite pour
qu'aucun ne puisse passer au vert sans avoir été posé.

**Le budget est un contrat, pas un vœu** (`TestBudgetFranc`). Il est mesuré sur
l'artefact qui entre réellement dans le prompt — `carte.markdown`, et pas une
approximation à côté —, et son dépassement **lève**. La paire qui le prouve est le
cœur du dossier : à un token en dessous du coût réel la construction refuse, à ce
coût exact elle rend la carte **entière**. Une troncature aurait rendu les deux
appels verts et personne n'aurait vu la différence.

**L'échantillon fautif d'abord** (`TestBlocsDeCode`). Un `#` en tête de ligne dans un
bloc de code n'est pas un titre, et la suite le **prouve avant** de vérifier la
correction : le corpus d'aujourd'hui porte 42 titres apparents à l'intérieur de blocs
de code, dont le gabarit de playbook de `docs/04-specifications-agents.md` — un
« ## Mission », un « ## Garde-fous », et jusqu'à un `# Playbook — <Libellé du rôle>`
de niveau 1 qui rangerait sous lui toutes les sections suivantes du fichier. Sans
cette moitié, un jour où quelqu'un aurait adouci l'échantillon, la suite rendrait un
✓ sur une question jamais posée.

**L'exactitude se vérifie sur le corpus réel, pas sur un exemple choisi**
(`TestExtractionExacte`). L'invariant est rejouable sur les 639 sections d'un coup :
le texte d'une section, **réanalysé**, ne porte qu'un seul titre — le sien, en
première ligne. C'est littéralement « son titre, son corps, et rien de la section
suivante », et c'est plus fort qu'un cas d'école parce qu'aucune section ne peut y
échapper. Le titre en double dans deux fichiers différents, lui, a trois témoins
réels (« 1. Prérequis et mise en place », dans `docs/12`, `docs/13` et `docs/23`).

**La carte doit être utilisable** (`TestIdentiteStable`) : tout ce qui se lit dans
la carte se résout en une section. C'est la propriété dont dépend le lot 2 — un
modèle ne peut citer que ce qu'il voit, donc ce qu'il voit doit être exactement ce
qui se cite.

**Le cache se compte, il ne se chronomètre pas** (`TestCacheEtInvalidation`) : on
compte les constructions, jamais des millisecondes — une durée en CI mesure la charge
de la machine, un compteur mesure la règle.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from maestro.controltower import documentation
from maestro.controltower.documentation import (
    BUDGET_CARTE_TOKENS,
    NIVEAU_MAX,
    CarteDocumentation,
    CarteTropGrande,
    SectionDoc,
    carte_documentation,
    construire_carte,
    empreinte_corpus,
    extraire_section,
    fichiers_corpus,
    oublier_carte,
)
from maestro.sources.extraction import estimer_tokens

#: La racine du dépôt — le corpus réel, celui que le produit servira.
RACINE = Path(__file__).resolve().parents[1]

#: Un titre apparent, **sans** la moindre notion de bloc de code : la règle d'avant,
#: gardée ici pour prouver que le piège existe avant de vérifier qu'il est traité.
_TITRE_NAIF = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")

#: Les titres du gabarit de playbook de `docs/04-specifications-agents.md`. Ils sont
#: **dans un bloc de code** : ce sont des sections que ce fichier n'a pas.
_FAUX_TITRES_DOCS_04 = (
    "Playbook — <Libellé du rôle>",
    "Mission",
    "Entrées attendues",
    "Méthode",
    "Ce que tu tranches",
    "Garde-fous",
    "Format de sortie",
)


@pytest.fixture(autouse=True)
def _cache_neuf() -> Iterator[None]:
    """Aucun test ne part du cache d'un autre — ni ne le laisse derrière lui."""
    oublier_carte()
    yield
    oublier_carte()


@pytest.fixture(scope="module")
def corpus_reel() -> CarteDocumentation:
    """La carte du corpus du dépôt, construite une fois pour toute la suite."""
    return construire_carte(RACINE)


def ecrire_corpus(racine: Path, fichiers: Mapping[str, str]) -> Path:
    """Monte un corpus jetable — mêmes emplacements que le vrai, contenu au choix."""
    for relatif, contenu in fichiers.items():
        chemin = racine / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu, encoding="utf-8")
    return racine


def titres_naifs(texte: str) -> list[str]:
    """Les titres qu'une lecture aveugle aux blocs de code trouverait."""
    return [
        correspondance.group(2).strip()
        for ligne in texte.splitlines()
        if (correspondance := _TITRE_NAIF.match(ligne)) is not None
    ]


class TestCarteDuCorpusReel:
    """Ce que la carte couvre, et ce qu'elle coûte — sur le corpus du dépôt."""

    def test_la_carte_couvre_tout_le_corpus(self, corpus_reel: CarteDocumentation) -> None:
        """Les fichiers de la carte sont **exactement** ceux que le corpus désigne.

        Comparés à un parcours indépendant plutôt qu'à un nombre figé : « 36 » est le
        compte du 2026-08-28, et le figer ferait rougir la suite le jour où le dépôt
        gagne une page de doc — un rouge qui ne signale aucun défaut. Ce qui est tenu
        ici est la **définition** du corpus, et le plancher de 36 l'ancre.
        """
        attendus = {relatif for relatif, _chemin in fichiers_corpus(RACINE)}
        assert set(corpus_reel.fichiers) == attendus
        assert len(corpus_reel.fichiers) >= 36
        assert "docs/00-cahier-des-charges.md" in corpus_reel.fichiers
        assert "docs/10-workflow-git.md" in corpus_reel.fichiers
        assert "apps/web/README.md" in corpus_reel.fichiers

    def test_chaque_fichier_a_son_bloc_dans_la_carte(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Y compris un fichier sans titre : la carte couvre le corpus, pas ce qu'elle y a lu."""
        for relatif in corpus_reel.fichiers:
            assert f"\n## {relatif}\n" in corpus_reel.markdown

    def test_la_carte_tient_sous_son_budget(self, corpus_reel: CarteDocumentation) -> None:
        """Le budget est annoncé (`BUDGET_CARTE_TOKENS`) et le corpus réel passe dessous.

        Le plancher n'est pas décoratif : une carte quasi vide passerait le plafond
        sans rien prouver. Mesurée à 11 869 tokens pour 639 sections le 2026-08-28.
        """
        assert corpus_reel.tokens <= BUDGET_CARTE_TOKENS
        assert corpus_reel.tokens > 5_000
        assert len(corpus_reel.sections) > 500

    def test_le_budget_porte_sur_ce_qui_entre_dans_le_prompt(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """`tokens` chiffre `markdown` — l'artefact, jamais une approximation à côté.

        C'est ce qui empêche le budget de dériver de ce qu'il borne : mesurer les
        titres seuls, ou le corpus, laisserait le plafond vrai sur une grandeur qui
        n'est pas celle qu'on paie.
        """
        assert corpus_reel.tokens == estimer_tokens(corpus_reel.markdown)

    def test_le_corpus_entier_ne_tiendrait_pas_dans_un_prompt(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le motif du dispositif, re-mesuré : la carte tient, le corpus non.

        ~559 000 tokens contre ~11 900 — c'est ce rapport, et lui seul, qui interdit
        de passer la documentation au modèle à chaque question et impose de la lui
        faire choisir sur une carte.
        """
        corpus = sum(estimer_tokens(texte) for texte in corpus_reel.textes.values())
        assert corpus > 100_000
        assert corpus > 20 * corpus_reel.tokens


class TestBudgetFranc:
    """Le dépassement lève — il ne tronque pas, et il ne se tait pas."""

    def test_a_un_token_pres_elle_leve_au_lieu_de_retrecir(self, tmp_path: Path) -> None:
        """La paire qui prouve l'absence de troncature.

        Sous le coût réel, la construction **refuse** ; au coût exact, elle rend la
        carte **entière**. Une troncature silencieuse aurait rendu les deux appels
        verts, avec deux cartes différentes et aucun moyen de le savoir.
        """
        racine = ecrire_corpus(
            tmp_path, {"docs/00-a.md": "# A\n\n## A1\n\ntexte\n", "apps/web/README.md": "# W\n"}
        )
        complete = construire_carte(racine, budget_tokens=None)
        with pytest.raises(CarteTropGrande):
            construire_carte(racine, budget_tokens=complete.tokens - 1)
        juste = construire_carte(racine, budget_tokens=complete.tokens)
        assert juste.markdown == complete.markdown
        assert juste.tokens == complete.tokens

    def test_le_message_nomme_le_cout_et_le_budget(self, tmp_path: Path) -> None:
        """Une erreur franche dit **de combien** : sans les deux nombres, elle n'aide personne."""
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n\n## A1\n"})
        with pytest.raises(CarteTropGrande) as leve:
            construire_carte(racine, budget_tokens=3)
        message = str(leve.value)
        assert "3" in message
        assert "tokens" in message
        assert "BUDGET_CARTE_TOKENS" in message

    def test_le_corpus_du_depot_passe_le_budget_par_defaut(self) -> None:
        """Le défaut du module est celui qui sert : personne n'a à passer un budget."""
        assert construire_carte(RACINE).tokens <= BUDGET_CARTE_TOKENS


class TestBlocsDeCode:
    """Un `#` dans un bloc de code n'est pas un titre — le piège, puis la règle."""

    def test_le_piege_est_bien_la_dans_le_corpus(self) -> None:
        """L'échantillon fautif : une lecture naïve trouve des titres qui n'existent pas.

        42 le 2026-08-28. On vérifie ici que le piège est **encore** dans le corpus
        avant d'aller vérifier qu'il est traité : sans cette moitié, un corpus adouci
        rendrait le test suivant vert sans rien garder.
        """
        chemin = RACINE / "docs" / "04-specifications-agents.md"
        naifs = titres_naifs(chemin.read_text(encoding="utf-8"))
        for faux in _FAUX_TITRES_DOCS_04:
            assert faux in naifs, f"le gabarit de playbook a bougé : {faux!r} introuvable"

    def test_la_carte_ne_porte_aucun_titre_de_bloc_de_code(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Aucun des titres du gabarit n'est devenu une section de `docs/04`."""
        titres = {
            section.titre
            for section in corpus_reel.sections_du_fichier("docs/04-specifications-agents.md")
        }
        assert titres  # le fichier a de vraies sections, sinon le test ne dit rien
        for faux in _FAUX_TITRES_DOCS_04:
            assert faux not in titres

    def test_la_lecture_naive_trouverait_plus_de_sections_que_la_carte(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le compte d'ensemble : 681 titres apparents pour 639 sections réelles."""
        naifs = sum(
            len(titres_naifs(chemin.read_text(encoding="utf-8", errors="replace")))
            for _relatif, chemin in fichiers_corpus(RACINE)
        )
        assert naifs > len(corpus_reel.sections)

    def test_un_titre_en_bloc_de_code_reste_dans_le_corps_de_sa_section(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Il n'est pas seulement écarté de la carte : il ne **coupe** pas non plus.

        Le gabarit vit sous « 1.3 Structure d'un document de rôle » ; son texte doit
        donc le porter entier. L'écarter de la carte tout en coupant dessus aurait
        perdu le contenu au lieu de le ranger.
        """
        texte = corpus_reel.texte(
            "docs/04-specifications-agents.md#1.3 Structure d'un document de rôle"
        )
        assert texte is not None
        assert "## Mission" in texte
        assert "## Garde-fous" in texte

    def test_les_deux_formes_de_barriere_sont_suivies(self, tmp_path: Path) -> None:
        """Accents graves et tildes, la clôture pouvant être plus longue que l'ouverture."""
        contenu = (
            "# Racine\n\n"
            "## Vraie\n\n"
            "```bash\n# Faux titre en accents graves\n````\n\n"
            "~~~\n## Faux titre en tildes\n~~~~\n\n"
            "## Vraie suivante\n"
        )
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert [section.titre for section in carte.sections] == [
            "Racine",
            "Vraie",
            "Vraie suivante",
        ]


class TestExtractionExacte:
    """« Son titre, son corps, et rien de la section suivante »."""

    def test_aucune_section_ne_deborde_sur_la_suivante(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """L'invariant rejoué sur les 639 sections du corpus, d'un seul coup.

        Le texte d'une section, **réanalysé par la même règle**, ne porte qu'un titre :
        le sien, en première ligne. Un débordement d'une seule section ferait rougir —
        aucun cas d'école ne donne cette garantie-là.
        """
        assert len(corpus_reel.sections) > 500
        for section in corpus_reel.sections:
            texte = corpus_reel.texte(section.identifiant)
            assert texte is not None
            titres = list(documentation._titres(texte.splitlines()))
            assert titres == [(0, section.niveau, section.titre)], section.identifiant

    def test_la_section_rend_son_titre_puis_son_corps(self, tmp_path: Path) -> None:
        contenu = "# Racine\n\nintro\n\n## Un\n\ncorps de un\n\n## Deux\n\ncorps de deux\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert carte.texte("docs/00-a.md#Un") == "## Un\n\ncorps de un"
        assert carte.texte("docs/00-a.md#Deux") == "## Deux\n\ncorps de deux"

    def test_une_sous_section_est_la_section_suivante(self, tmp_path: Path) -> None:
        """Les sections **partitionnent** : un `##` ne réémet pas ses `###`.

        C'est la décision 1 du module, et elle a un prix visible ici — « Un » ne porte
        que son chapeau. Le prix payé en face est celui qu'on ne voit pas : emboîter
        ferait rendre trois fois les mêmes octets à qui demande un chapitre et ses
        parties, c'est-à-dire dépenser le budget qu'on vient de mesurer.
        """
        contenu = "# R\n\n## Un\n\nchapeau\n\n### Un.a\n\ndétail\n\n## Deux\n\nfin\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert carte.texte("docs/00-a.md#Un") == "## Un\n\nchapeau"
        assert carte.texte("docs/00-a.md#Un.a") == "### Un.a\n\ndétail"

    def test_un_titre_plus_profond_que_la_carte_reste_du_corps(self, tmp_path: Path) -> None:
        """`####` ne se cite pas et ne coupe pas — c'est du corps, par définition de la carte."""
        contenu = "# R\n\n## Un\n\n#### Trop profond\n\ndétail\n\n## Deux\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert [section.titre for section in carte.sections] == ["R", "Un", "Deux"]
        texte = carte.texte("docs/00-a.md#Un")
        assert texte is not None
        assert "#### Trop profond" in texte
        assert max(section.niveau for section in carte.sections) <= NIVEAU_MAX

    def test_la_derniere_section_va_jusqu_au_bout_du_fichier(self, tmp_path: Path) -> None:
        contenu = "# R\n\n## Fin\n\ndernier mot\n\n\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert carte.texte("docs/00-a.md#Fin") == "## Fin\n\ndernier mot"

    def test_un_meme_titre_dans_deux_fichiers_extrait_deux_textes(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le critère, sur ses témoins réels : trois fichiers portent ce titre.

        Le nom du fichier ouvre l'identifiant, donc chaque extraction rend le texte de
        **son** fichier. Sans lui, une des trois réponses aurait été rendue pour les
        trois, et rien ne l'aurait montré.
        """
        homonymes = [
            section
            for section in corpus_reel.sections
            if section.titre == "1. Prérequis et mise en place"
        ]
        assert len(homonymes) >= 3
        assert len({section.fichier for section in homonymes}) == len(homonymes)
        textes = [corpus_reel.texte(section.identifiant) for section in homonymes]
        assert all(texte is not None for texte in textes)
        assert len(set(textes)) == len(textes)

    def test_deux_fichiers_de_contenu_different_sous_le_meme_titre(self, tmp_path: Path) -> None:
        """Le même cas, tenu sur pièces : deux corps qui ne se confondent pas."""
        racine = ecrire_corpus(
            tmp_path,
            {
                "docs/00-a.md": "# A\n\n## Prérequis\n\ncelui de A\n",
                "docs/01-b.md": "# B\n\n## Prérequis\n\ncelui de B\n",
            },
        )
        carte = construire_carte(racine)
        assert carte.texte("docs/00-a.md#Prérequis") == "## Prérequis\n\ncelui de A"
        assert carte.texte("docs/01-b.md#Prérequis") == "## Prérequis\n\ncelui de B"


class TestIdentiteStable:
    """L'identifiant est court et exact ; le chemin de titres est complet."""

    def test_l_identifiant_ouvre_par_le_fichier(self, corpus_reel: CarteDocumentation) -> None:
        for section in corpus_reel.sections:
            assert section.identifiant.startswith(f"{section.fichier}#")

    def test_les_identifiants_sont_uniques(self, corpus_reel: CarteDocumentation) -> None:
        """La propriété dont dépend l'extraction : une clé, une section."""
        identifiants = [section.identifiant for section in corpus_reel.sections]
        assert len(set(identifiants)) == len(identifiants)

    def test_tout_ce_qui_se_lit_dans_la_carte_se_resout(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """La carte est **utilisable** : chaque ligne y désigne une section joignable.

        C'est ce dont le lot 2 dépendra — un modèle ne peut citer que ce qu'il voit,
        donc ce qu'il voit doit être exactement ce qui se cite. Le test relit la carte
        comme le modèle la lira : le fichier vient de son en-tête, le titre de la ligne.
        """
        fichier = ""
        lus = 0
        for ligne in corpus_reel.markdown.splitlines():
            if ligne.startswith("## "):
                fichier = ligne[3:].strip()
                continue
            nu = ligne.lstrip()
            if not fichier or not nu.startswith("- "):
                continue
            section = corpus_reel.section(f"{fichier}#{nu[2:]}")
            assert section is not None, f"{fichier} / {nu[2:]!r}"
            assert section.fichier == fichier
            lus += 1
        assert lus == len(corpus_reel.sections)

    def test_le_chemin_porte_les_ancetres_pour_un_lecteur(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le besoin de l'utilisateur : retrouver le passage, donc le chemin entier."""
        section = corpus_reel.section(
            "docs/10-workflow-git.md#3.3 Dates & time tracking — renseignés automatiquement"
        )
        assert section is not None
        assert section.ancetres
        assert section.chemin.startswith("docs/10-workflow-git.md › ")
        assert section.chemin.endswith(section.titre)
        for ancetre in section.ancetres:
            assert ancetre in section.chemin

    def test_le_chemin_complet_couterait_trois_fois_la_carte(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Pourquoi la carte n'affiche pas le chemin d'ancêtres : la mesure, pas l'avis.

        Elle passerait de 11 869 à 35 421 tokens — trois fois le prix, au-delà du
        budget — pour zéro information nouvelle, l'indentation le portant déjà.
        """
        plat = "\n".join(section.chemin for section in corpus_reel.sections)
        assert estimer_tokens(plat) > 2 * corpus_reel.tokens

    def test_aucun_titre_en_double_dans_un_meme_fichier_du_corpus(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """La mesure qui autorise l'identifiant court : 0 collision sur 639 sections."""
        assert all(section.rang == 1 for section in corpus_reel.sections)

    def test_un_titre_en_double_dans_un_fichier_prend_un_rang_affiche(
        self, tmp_path: Path
    ) -> None:
        """Le cas absent du corpus, tenu quand même — et **affiché** dans la carte.

        Le rang ne peut pas rester un détail interne : ce qu'on lit sur une ligne de
        carte doit être l'identifiant exact, sinon le modèle recopie une clé qui
        n'existe pas et l'assistance avoue une ignorance qu'elle a fabriquée.
        """
        contenu = "# R\n\n## Exemple\n\npremier\n\n## Autre\n\n## Exemple\n\nsecond\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert carte.texte("docs/00-a.md#Exemple") == "## Exemple\n\npremier"
        assert carte.texte("docs/00-a.md#Exemple ~2") == "## Exemple\n\nsecond"
        assert "- Exemple ~2" in carte.markdown

    def test_la_citation_se_serialise(self, corpus_reel: CarteDocumentation) -> None:
        """`to_dict` est la forme qu'une réponse du lot 2 rendra à l'interface."""
        section = corpus_reel.sections[0]
        forme = section.to_dict()
        assert forme["identifiant"] == section.identifiant
        assert forme["chemin"] == section.chemin
        assert forme["ancetres"] == list(section.ancetres)
        assert forme["ligne"] >= 1

    def test_la_ligne_designe_bien_le_titre_dans_le_fichier(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Une citation vérifiable : `ligne` pointe le titre, 1-indexé."""
        for relatif, chemin in fichiers_corpus(RACINE):
            lignes = chemin.read_text(encoding="utf-8", errors="replace").splitlines()
            for section in corpus_reel.sections_du_fichier(relatif):
                brute = lignes[section.ligne - 1]
                assert brute.startswith("#" * section.niveau + " ")
                assert section.titre in brute


class TestPerimetreDuCorpus:
    """Ce qui entre dans le corpus, et ce qui en reste dehors."""

    def test_les_presentations_et_les_assets_restent_dehors(self, tmp_path: Path) -> None:
        """`docs/*.md` ne descend pas — c'est ce qui les écarte, sans avoir à les nommer.

        Les présentations de milestone sont des pages autonomes et datées ; les
        laisser entrer ferait citer à l'assistance un instantané au lieu de la doc.
        """
        racine = ecrire_corpus(
            tmp_path,
            {
                "docs/00-a.md": "# A\n",
                "docs/presentations/phase-3.md": "# Présentation\n",
                "docs/assets/note.md": "# Asset\n",
                "apps/web/README.md": "# Web\n",
            },
        )
        carte = construire_carte(racine)
        assert carte.fichiers == ("docs/00-a.md", "apps/web/README.md")

    def test_un_readme_absent_ne_fait_pas_echouer(self, tmp_path: Path) -> None:
        """Le corpus est ce qui est là : un motif sans correspondance ne rend rien."""
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n"}))
        assert carte.fichiers == ("docs/00-a.md",)

    def test_un_fichier_sans_titre_reste_dans_la_carte(self, tmp_path: Path) -> None:
        """La carte couvre le **corpus**, pas ce qu'elle a su y lire."""
        racine = ecrire_corpus(
            tmp_path, {"docs/00-a.md": "juste un paragraphe\n", "docs/01-b.md": "# B\n"}
        )
        carte = construire_carte(racine)
        assert carte.fichiers == ("docs/00-a.md", "docs/01-b.md")
        assert "## docs/00-a.md" in carte.markdown
        assert carte.sections_du_fichier("docs/00-a.md") == ()


class TestResolutionDIdentifiant:
    """Résoudre un identifiant est une recherche dans un index — jamais un accès disque."""

    def test_un_identifiant_inconnu_rend_none(self, corpus_reel: CarteDocumentation) -> None:
        """`None` est nominal : l'identifiant vient du modèle, qui peut se tromper.

        C'est ce qui laisse le lot 3 avouer une ignorance au lieu de lever — le budget,
        lui, lève, parce qu'il constate un défaut du corpus que personne ne verrait.
        """
        assert corpus_reel.texte("docs/10-workflow-git.md#section qui n'existe pas") is None
        assert corpus_reel.section("") is None
        assert corpus_reel.section("n'importe quoi") is None

    def test_un_fichier_hors_corpus_reste_injoignable(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Y compris quand il existe : la carte est l'index, pas un chemin d'accès.

        C'est ce qui rend la traversée de chemin **sans objet** plutôt que gardée — un
        identifiant ne désigne rien en dehors de ce que la carte porte déjà.
        """
        assert (RACINE / "CLAUDE.md").is_file()
        assert corpus_reel.texte("CLAUDE.md#Règles Git obligatoires") is None
        assert corpus_reel.texte("../CLAUDE.md#x") is None
        assert corpus_reel.texte("docs/../CLAUDE.md#x") is None
        assert corpus_reel.texte("/etc/passwd#x") is None

    def test_la_casse_et_les_espaces_ne_font_pas_perdre_une_section(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Un identifiant recopié à un espace près désigne toujours sa section."""
        section = corpus_reel.sections[0]
        assert corpus_reel.section(f"  {section.identifiant}  ") is section
        assert corpus_reel.section(section.identifiant.upper()) is section

    def test_une_forme_relachee_ne_masque_jamais_une_section_exacte(
        self, tmp_path: Path
    ) -> None:
        """Les clés exactes sont posées **toutes** avant la première clé normalisée.

        Deux sections qui ne diffèrent que par la casse existent séparément ; si le
        repli était rangé au fil de l'eau, la seconde serait masquée par la forme
        relâchée de la première et rendrait le mauvais texte, sans rien signaler.
        """
        contenu = "# R\n\n## Mission\n\nen bas de casse\n\n## MISSION\n\nen capitales\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))
        assert carte.texte("docs/00-a.md#Mission") == "## Mission\n\nen bas de casse"
        assert carte.texte("docs/00-a.md#MISSION") == "## MISSION\n\nen capitales"

    def test_extraire_section_passe_par_la_carte_du_depot(self) -> None:
        """Le raccourci du lot 2 : nommer une section suffit, la carte est celle en cache."""
        carte = carte_documentation()
        section = carte.sections[0]
        assert extraire_section(section.identifiant) == carte.texte(section.identifiant)
        assert extraire_section("docs/00-cahier-des-charges.md#pas une section") is None


class TestCacheEtInvalidation:
    """La carte se construit une fois, et se refait quand un fichier change."""

    def _compteur(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        """Compte les constructions réelles — on compte, on ne chronomètre pas."""
        appels = [0]
        vraie = documentation.construire_carte

        def espion(*args: object, **kwargs: object) -> CarteDocumentation:
            appels[0] += 1
            return vraie(*args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(documentation, "construire_carte", espion)
        return appels

    def test_elle_ne_se_recalcule_pas_a_chaque_appel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n\n## Un\n\ncorps\n"})
        appels = self._compteur(monkeypatch)
        premiere = carte_documentation(racine)
        deuxieme = carte_documentation(racine)
        troisieme = carte_documentation(racine)
        assert appels[0] == 1
        assert premiere is deuxieme is troisieme

    def test_elle_se_refait_quand_un_fichier_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n\n## Un\n\ncorps\n"})
        appels = self._compteur(monkeypatch)
        avant = carte_documentation(racine)
        (racine / "docs" / "00-a.md").write_text(
            "# A\n\n## Un\n\ncorps réécrit et plus long\n\n## Deux\n\nneuve\n", encoding="utf-8"
        )
        apres = carte_documentation(racine)
        assert appels[0] == 2
        assert apres is not avant
        assert "docs/00-a.md#Deux" in {section.identifiant for section in apres.sections}
        assert carte_documentation(racine) is apres

    def test_un_fichier_ajoute_ou_retire_refait_la_carte(self, tmp_path: Path) -> None:
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n"})
        avant = empreinte_corpus(racine)
        premiere = carte_documentation(racine)
        ecrire_corpus(racine, {"docs/01-b.md": "# B\n"})
        assert empreinte_corpus(racine) != avant
        avec = carte_documentation(racine)
        assert avec is not premiere
        assert avec.fichiers == ("docs/00-a.md", "docs/01-b.md")
        (racine / "docs" / "01-b.md").unlink()
        sans = carte_documentation(racine)
        assert sans.fichiers == ("docs/00-a.md",)

    def test_l_empreinte_de_la_carte_est_celle_du_corpus(self, tmp_path: Path) -> None:
        """Prise **avant** la lecture : une carte ne certifie jamais plus qu'elle n'a lu."""
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n"})
        carte = construire_carte(racine)
        assert carte.empreinte == empreinte_corpus(racine)
        assert [relatif for relatif, _date, _taille in carte.empreinte] == list(carte.fichiers)

    def test_oublier_carte_force_la_reconstruction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Le geste explicite, pour ce que la date de modification ne montrerait pas."""
        racine = ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n"})
        appels = self._compteur(monkeypatch)
        premiere = carte_documentation(racine)
        oublier_carte(racine)
        seconde = carte_documentation(racine)
        assert appels[0] == 2
        assert seconde is not premiere
        assert seconde.markdown == premiere.markdown


class TestSectionDoc:
    """Les propriétés dérivées d'une section, hors de tout corpus."""

    def test_le_rang_un_ne_s_affiche_pas(self) -> None:
        section = SectionDoc(fichier="docs/00-a.md", titre="Un", niveau=2)
        assert section.cle_titre == "Un"
        assert section.identifiant == "docs/00-a.md#Un"

    def test_un_titre_portant_un_diese_se_coupe_au_bon_endroit(self) -> None:
        """Le fichier ouvre l'identifiant, et un chemin ne contient jamais `#`."""
        section = SectionDoc(fichier="docs/00-a.md", titre="Le run #123", niveau=2)
        assert section.identifiant.split("#", 1) == ["docs/00-a.md", "Le run #123"]

    def test_le_chemin_enchaine_fichier_ancetres_et_titre(self) -> None:
        section = SectionDoc(
            fichier="docs/00-a.md", titre="1.1 Le constat", niveau=3, ancetres=("A", "1. Contexte")
        )
        assert section.chemin == "docs/00-a.md › A › 1. Contexte › 1.1 Le constat"
