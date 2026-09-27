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
échapper. Le titre en double dans deux fichiers différents, lui, a ses témoins
réels (« 0. Ce que ce document décide », que six notes de décision portent).

**La carte doit être utilisable** (`TestIdentiteStable`) : tout ce qui se lit dans
la carte se résout en une section. C'est la propriété dont dépend le lot 2 — un
modèle ne peut citer que ce qu'il voit, donc ce qu'il voit doit être exactement ce
qui se cite.

**Le cache se compte, il ne se chronomètre pas** (`TestCacheEtInvalidation`) : on
compte les constructions, jamais des millisecondes — une durée en CI mesure la charge
de la machine, un compteur mesure la règle.

**Deux niveaux, et rien de perdu entre eux** (`TestDeuxNiveaux`, #1316) : le sommaire
ne montre que les niveaux 1 et 2, et ce qu'il tait se montre au détail du titre qui
le porte. La propriété qui compte est celle de la carte à plat, gardée : tout ce que
l'index porte se lit à l'un des deux niveaux, et tout ce qui s'y lit se résout.

**Un trimestre de croissance, rejoué** (`TestCroissanceDUnTrimestre`, #1316) : la
carte à plat a franchi son budget le 2026-09-24, à 14 tokens de marge une fois des
titres raccourcis. La sonde rejoue le rythme mesuré sur le corpus réel, et elle est
**prouvée fautive d'abord** : la carte à plat ne tient pas ce trimestre, le sommaire
le tient — et une année de croissance le fait encore lever, parce qu'un budget qui
ne lèverait jamais ne bornerait plus rien.

**Le corpus est déclaré, jamais deviné** (`TestCorpusDuProduit`, #1321) : le
2026-09-25, l'assistant a cité la Roadmap — un document du **développement** du
dépôt — pour répondre à une question d'usage. Un document n'entre plus dans le
corpus qu'en se déclarant du produit, en première ligne, et chaque document du dépôt
se déclare. La sonde lit elle-même la déclaration et elle est **prouvée fautive
d'abord** : sur le corpus d'avant ce ticket, elle trouve la Roadmap citée ce jour-là ;
sur le corpus d'aujourd'hui, plus rien.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
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
    documents_du_depot,
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


#: Les deux déclarations qu'un document du dépôt porte en première ligne (#1321),
#: écrites ici en toutes lettres : c'est la forme qu'un auteur recopie, et c'est
#: sous elle que la suite doit les reconnaître.
PRODUIT = "<!-- documentation: produit -->\n"
DEVELOPPEMENT = "<!-- documentation: développement -->\n"


def ecrire_corpus(racine: Path, fichiers: Mapping[str, str]) -> Path:
    """Monte un corpus jetable — mêmes emplacements que le vrai, contenu au choix.

    Chaque document s'y déclare **du produit** (#1321), sauf s'il ouvre déjà sur une
    déclaration : c'est ce qui laisse un test poser un document du développement à
    côté des autres. Un document qui ne se déclare pas du tout s'écrit à la main.
    """
    for relatif, contenu in fichiers.items():
        chemin = racine / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        entete = "" if contenu.startswith("<!--") else PRODUIT
        chemin.write_text(entete + contenu, encoding="utf-8")
    return racine


def titres_naifs(texte: str) -> list[str]:
    """Les titres qu'une lecture aveugle aux blocs de code trouverait."""
    return [
        correspondance.group(2).strip()
        for ligne in texte.splitlines()
        if (correspondance := _TITRE_NAIF.match(ligne)) is not None
    ]


def lignes_de_carte(markdown: str) -> list[tuple[str, str, str]]:
    """Ce qu'un modèle lit sur un niveau de la carte : `(fichier, marque, clé du titre)`.

    Relue comme le modèle la lira — le fichier vient de l'en-tête `## `, le titre de
    la ligne, la marque (`-` ou `+`) de sa puce —, et non depuis les objets qui l'ont
    rendue : c'est ce qui est **écrit** qui doit se résoudre.
    """
    lues: list[tuple[str, str, str]] = []
    fichier = ""
    for ligne in markdown.splitlines():
        if ligne.startswith("## "):
            fichier = ligne[3:].strip()
            continue
        nu = ligne.lstrip()
        if fichier and nu[:2] in ("- ", "+ "):
            lues.append((fichier, nu[0], nu[2:]))
    return lues


def carte_a_plat(carte: CarteDocumentation) -> str:
    """La carte d'avant #1316 — tous les titres H1-H3 sur une page — rendue sur `carte`.

    Elle n'entre plus dans aucun prompt : elle sert à **mesurer** ce que la forme
    abandonnée coûterait, c'est-à-dire l'échantillon fautif de la sonde de croissance.
    Le rendu est celui du module, sans ouverture, jamais une copie de sa logique.
    """
    par_fichier = {relatif: carte.sections_du_fichier(relatif) for relatif in carte.fichiers}
    return documentation._rendre_carte(carte.fichiers, par_fichier, len(carte.sections), {})


class TestCarteDuCorpusReel:
    """Ce que la carte couvre, et ce qu'elle coûte — sur le corpus du dépôt."""

    def test_la_carte_couvre_tout_le_corpus(self, corpus_reel: CarteDocumentation) -> None:
        """Les fichiers de la carte sont **exactement** ceux que le corpus désigne.

        Comparés à un parcours indépendant plutôt qu'à un nombre figé : « 36 » est le
        compte du 2026-08-28, et le figer ferait rougir la suite le jour où le dépôt
        gagne une page de doc — un rouge qui ne signale aucun défaut. Ce qui est tenu
        ici est la **définition** du corpus, et un plancher l'ancre : 29 documents du
        produit sur 46 le 2026-09-27, une fois ceux du développement sortis (#1321).
        """
        attendus = {relatif for relatif, _chemin in fichiers_corpus(RACINE)}
        assert set(corpus_reel.fichiers) == attendus
        assert len(corpus_reel.fichiers) >= 25
        assert "docs/00-cahier-des-charges.md" in corpus_reel.fichiers
        assert "docs/05-interface-control-tower.md" in corpus_reel.fichiers

    def test_chaque_fichier_a_son_bloc_dans_la_carte(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Y compris un fichier sans titre : la carte couvre le corpus, pas ce qu'elle y a lu."""
        for relatif in corpus_reel.fichiers:
            assert f"\n## {relatif}\n" in corpus_reel.markdown

    def test_la_carte_tient_sous_son_budget(self, corpus_reel: CarteDocumentation) -> None:
        """Le budget est annoncé (`BUDGET_CARTE_TOKENS`) et le corpus réel passe dessous.

        Le plancher n'est pas décoratif : une carte quasi vide passerait le plafond
        sans rien prouver. Mesurée à 11 869 tokens pour 639 sections le 2026-08-28,
        à plat ; depuis #1316 c'est le sommaire qui est borné — 6 779 tokens pour 842
        sections le 2026-09-26, puis 4 204 pour 489 le 2026-09-27, une fois le corpus
        réduit aux documents du produit (#1321). La marge, elle, se tient par la sonde
        de croissance.
        """
        assert corpus_reel.tokens <= BUDGET_CARTE_TOKENS
        assert corpus_reel.tokens > 3_000
        assert len(corpus_reel.sections) > 400

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
        assert len(corpus_reel.sections) > 400
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
        """Le critère, sur ses témoins réels : plusieurs notes de décision portent ce titre.

        Le nom du fichier ouvre l'identifiant, donc chaque extraction rend le texte de
        **son** fichier. Sans lui, une des réponses aurait été rendue pour toutes, et
        rien ne l'aurait montré. Les témoins d'origine — « 1. Prérequis et mise en
        place », dans trois démos datées — ont quitté le corpus avec #1321.
        """
        homonymes = [
            section
            for section in corpus_reel.sections
            if section.titre == "0. Ce que ce document décide"
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

        Depuis #1316 la carte a deux niveaux, et la relecture suit le modèle : le
        sommaire, puis le détail de chaque titre marqué `+`. Ce qui s'y lit, **les
        deux niveaux réunis**, est exactement l'index — pas une section de moins,
        sans quoi le modèle ne pourrait plus la choisir, et pas une de plus.
        """
        lues: list[SectionDoc] = []
        for fichier, marque, titre in lignes_de_carte(corpus_reel.markdown):
            section = corpus_reel.section(f"{fichier}#{titre}")
            assert section is not None, f"{fichier} / {titre!r}"
            assert section.fichier == fichier
            lues.append(section)
            if marque == "+":
                detail = corpus_reel.detail([section.identifiant])
                for sous_fichier, _marque, sous_titre in lignes_de_carte(detail)[1:]:
                    sous = corpus_reel.section(f"{sous_fichier}#{sous_titre}")
                    assert sous is not None, f"{sous_fichier} / {sous_titre!r}"
                    lues.append(sous)
        assert sorted(section.identifiant for section in lues) == sorted(
            section.identifiant for section in corpus_reel.sections
        )

    def test_le_chemin_porte_les_ancetres_pour_un_lecteur(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le besoin de l'utilisateur : retrouver le passage, donc le chemin entier."""
        section = corpus_reel.section(
            "docs/04-specifications-agents.md#1.3 Structure d'un document de rôle"
        )
        assert section is not None
        assert section.ancetres
        assert section.chemin.startswith("docs/04-specifications-agents.md › ")
        assert section.chemin.endswith(section.titre)
        for ancetre in section.ancetres:
            assert ancetre in section.chemin

    def test_la_citation_nomme_le_document_et_jamais_son_fichier(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Ce qui sort vers l'utilisateur ne nomme aucun fichier du dépôt (#939).

        `chemin` reste ce qu'il était — il sert le prompt et qui relit — mais il ouvre
        sur un chemin de fichier, et c'est ce que le produit a cessé d'afficher. La
        citation prend la même chaîne de titres et remplace sa tête par le **titre du
        document**, qui est le seul nom qu'un utilisateur puisse reconnaître puis
        redemander.
        """
        section = corpus_reel.section(
            "docs/04-specifications-agents.md#1.3 Structure d'un document de rôle"
        )
        assert section is not None
        assert section.document
        assert section.citation.startswith(f"{section.document} › ")
        assert section.citation.endswith(section.titre)
        assert "docs/" not in section.citation
        assert ".md" not in section.citation

    def test_le_document_est_derive_de_son_titre_de_niveau_1(self, tmp_path: Path) -> None:
        """Dérivé du corpus, jamais listé à côté — la règle de tout ce module.

        Une table `fichier → nom` tenue à la main finirait par nommer un document qui
        a changé de titre, et l'écran citerait alors une source qui n'existe plus sous
        ce nom-là.
        """
        carte = construire_carte(
            ecrire_corpus(tmp_path, {"docs/00-a.md": "# Guide de démarrage\n\n## Prérequis\n\nx\n"})
        )

        section = carte.section("docs/00-a.md#Prérequis")
        assert section is not None
        assert section.document == "Guide de démarrage"
        assert section.citation == "Guide de démarrage › Prérequis"

    def test_la_section_de_tete_ne_se_nomme_pas_deux_fois(self, tmp_path: Path) -> None:
        """Le seul cas où ajouter le document retirerait de l'information (#939)."""
        carte = construire_carte(
            ecrire_corpus(tmp_path, {"docs/00-a.md": "# Guide de démarrage\n\nx\n"})
        )

        section = carte.section("docs/00-a.md#Guide de démarrage")
        assert section is not None
        assert section.citation == "Guide de démarrage"

    def test_un_fichier_sans_titre_de_niveau_1_n_invente_aucun_nom(self, tmp_path: Path) -> None:
        """Pas de repli sur le nom de fichier : ce serait remettre le dépôt à l'écran."""
        carte = construire_carte(
            ecrire_corpus(tmp_path, {"docs/00-a.md": "## Prérequis\n\nx\n"})
        )

        section = carte.section("docs/00-a.md#Prérequis")
        assert section is not None
        assert section.document == ""
        assert section.citation == "Prérequis"

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


class TestDeuxNiveaux:
    """Le sommaire montre les niveaux 1 et 2, le détail ce qu'un titre choisi porte (#1316)."""

    #: Un document à deux chapitres : l'un porte deux sous-sections, l'autre aucune.
    CONTENU = (
        "# Guide\n\nintro\n\n"
        "## Les écrans\n\nchapeau\n\n"
        "### L'écran Runs\n\ncorps runs\n\n"
        "### L'écran Agents\n\ncorps agents\n\n"
        "## Le glossaire\n\ncorps glossaire\n"
    )

    @pytest.fixture()
    def carte(self, tmp_path: Path) -> CarteDocumentation:
        return construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": self.CONTENU}))

    def test_le_sommaire_tait_le_niveau_3_et_marque_qui_le_porte(
        self, carte: CarteDocumentation
    ) -> None:
        """Le `+` dit au modèle qu'en choisissant ce titre il en verra davantage."""
        assert lignes_de_carte(carte.markdown) == [
            ("docs/00-a.md", "-", "Guide"),
            ("docs/00-a.md", "+", "Les écrans"),
            ("docs/00-a.md", "-", "Le glossaire"),
        ]
        assert "L'écran Runs" not in carte.markdown

    def test_le_sommaire_se_dit_tel_dans_sa_legende(self, carte: CarteDocumentation) -> None:
        """Une marque qu'aucune ligne n'explique serait lue comme une puce de plus."""
        assert "niveaux 1 et 2" in carte.markdown
        assert "`+`" in carte.markdown

    def test_le_detail_montre_le_titre_choisi_puis_ce_qu_il_porte(
        self, carte: CarteDocumentation
    ) -> None:
        """Le chapeau garde sa ligne : il reste un passage qu'on peut vouloir lire."""
        detail = carte.detail(["docs/00-a.md#Les écrans"])

        assert lignes_de_carte(detail) == [
            ("docs/00-a.md", "-", "Les écrans"),
            ("docs/00-a.md", "-", "L'écran Runs"),
            ("docs/00-a.md", "-", "L'écran Agents"),
        ]
        assert [s.titre for s in carte.sous_sections("docs/00-a.md#Les écrans")] == [
            "L'écran Runs",
            "L'écran Agents",
        ]

    def test_ce_qui_se_lit_au_detail_s_extrait_comme_le_reste(
        self, carte: CarteDocumentation
    ) -> None:
        """Le second niveau n'est qu'une autre page de la même carte : même index."""
        assert carte.texte("docs/00-a.md#L'écran Runs") == "### L'écran Runs\n\ncorps runs"
        assert carte.texte("docs/00-a.md#Les écrans") == "## Les écrans\n\nchapeau"

    @pytest.mark.parametrize(
        "identifiants",
        [
            pytest.param([], id="rien-de-choisi"),
            pytest.param(["docs/00-a.md#Le glossaire"], id="un-titre-qui-ne-porte-rien"),
            pytest.param(["docs/00-a.md#Inventé", "n'importe quoi"], id="rien-ne-resout"),
        ],
    )
    def test_rien_a_ouvrir_rend_un_detail_vide(
        self, carte: CarteDocumentation, identifiants: list[str]
    ) -> None:
        """Le vide est le signal : le répondeur lit alors sans détour, en deux appels."""
        assert carte.detail(identifiants) == ""

    def test_un_titre_qui_ne_porte_rien_reste_au_detail_s_il_accompagne(
        self, carte: CarteDocumentation
    ) -> None:
        """Le détail reprend **tous** les titres choisis, dans l'ordre du modèle.

        Sa liste est la sélection finale : un titre qui ne s'ouvre pas mais que le
        modèle avait choisi doit pouvoir y être gardé, donc y être lu.
        """
        detail = carte.detail(["docs/00-a.md#Le glossaire", "docs/00-a.md#Les écrans"])

        assert [titre for _f, _m, titre in lignes_de_carte(detail)] == [
            "Le glossaire",
            "Les écrans",
            "L'écran Runs",
            "L'écran Agents",
        ]

    def test_le_plafond_compte_les_titres_qui_resolvent(
        self, carte: CarteDocumentation
    ) -> None:
        """Une clé de travers ne prend pas la place d'un titre réel."""
        detail = carte.detail(
            ["docs/00-a.md#Inventé", "docs/00-a.md#Les écrans", "docs/00-a.md#Le glossaire"],
            maximum=1,
        )

        assert [titre for _f, _m, titre in lignes_de_carte(detail)] == [
            "Les écrans",
            "L'écran Runs",
            "L'écran Agents",
        ]

    def test_un_document_qui_saute_un_niveau_s_ouvre_depuis_son_titre(
        self, tmp_path: Path
    ) -> None:
        """Un `###` sous un `#` se range sous son plus proche ancêtre **visible**."""
        contenu = "# Guide\n\n### Directement\n\nx\n\n## Chapitre\n\ny\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))

        assert lignes_de_carte(carte.markdown) == [
            ("docs/00-a.md", "+", "Guide"),
            ("docs/00-a.md", "-", "Chapitre"),
        ]
        assert [s.titre for s in carte.sous_sections("docs/00-a.md#Guide")] == ["Directement"]

    def test_une_section_sans_ancetre_visible_reste_au_sommaire(self, tmp_path: Path) -> None:
        """Sans ancêtre visible, aucun des deux niveaux ne la montrerait : elle monte.

        Un fichier qui ouvre sur des `###` n'existe pas dans le corpus d'aujourd'hui ;
        le jour où il existe, ses sections doivent rester choisissables.
        """
        contenu = "### Un\n\nx\n\n### Deux\n\ny\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))

        assert [titre for _f, _m, titre in lignes_de_carte(carte.markdown)] == ["Un", "Deux"]

    def test_les_deux_niveaux_coutent_moins_que_la_carte_a_plat(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le troisième appel se paie en allers, pas en tokens — mesuré sur le corpus réel.

        Le pire détail est celui des six chapitres qui portent le plus : réunis au
        sommaire, ils coûtent encore moins que la carte à plat qu'on payait à chaque
        question.
        """
        porteurs = sorted(
            (
                section
                for section in corpus_reel.sections
                if corpus_reel.sous_sections(section.identifiant)
            ),
            key=lambda section: estimer_tokens(corpus_reel.detail([section.identifiant])),
            reverse=True,
        )
        pire = corpus_reel.detail([section.identifiant for section in porteurs[:6]])

        assert len(porteurs) >= 6
        assert corpus_reel.tokens + estimer_tokens(pire) < estimer_tokens(
            carte_a_plat(corpus_reel)
        )


class TestCroissanceDUnTrimestre:
    """La sonde de #1316 : la carte ne se retrouve plus à la merci d'un titre de plus.

    Le rythme est celui de `main` : 641 sections le 2026-08-29, 842 le 2026-09-26 —
    **≈ 200 par mois**. La croissance est rejouée sur le corpus réel lui-même, et la
    sonde est prouvée fautive avant d'être crue. Ce rythme a été mesuré sur tout
    `docs/` : depuis #1321, le corpus n'en est que la part du produit, et le rejouer tel
    quel le majore — la sonde reste du côté de la prudence.
    """

    SECTIONS_PAR_MOIS = 200

    @staticmethod
    def corpus_gonfle(
        reel: CarteDocumentation, racine: Path, sections_en_plus: int
    ) -> CarteDocumentation:
        """La carte du corpus réel réécrit en titres seuls, plus `sections_en_plus` sections.

        **Titres seuls** : la carte ne lit que les titres, et c'est ce qui rend la copie
        fidèle (vérifié ci-dessous) sans relire 1,6 Mio par test. La croissance
        **rejoue les fichiers du corpus** sous un autre nom, dans l'ordre, jusqu'au
        compte : mêmes titres, mêmes niveaux, un `#` par document. C'est la forme de
        ce qui s'écrit, plutôt qu'un titre inventé dont la longueur serait un choix.

        La borne est levée à la construction : la sonde mesure, c'est le test qui juge.
        """
        assert reel.sections, "un corpus sans section ne se rejoue pas"

        def ecrire(relatif: str, sections: tuple[SectionDoc, ...]) -> None:
            titres = "".join(f"{'#' * section.niveau} {section.titre}\n\n" for section in sections)
            ecrire_corpus(racine, {relatif: titres})

        for relatif in reel.fichiers:
            ecrire(relatif, reel.sections_du_fichier(relatif))
        reste, tour = sections_en_plus, 0
        while reste > 0:
            for relatif in reel.fichiers:
                if reste <= 0:
                    break
                sections = reel.sections_du_fichier(relatif)[:reste]
                ecrire(f"docs/zz-croissance-{tour:02d}-{Path(relatif).stem}.md", sections)
                reste -= len(sections)
            tour += 1
        return construire_carte(racine, budget_tokens=None)

    def test_la_copie_en_titres_seuls_est_fidele(
        self, corpus_reel: CarteDocumentation, tmp_path: Path
    ) -> None:
        """Sans croissance, la copie rend **exactement** le sommaire du dépôt.

        Sans cette moitié, la sonde mesurerait un corpus qui n'est pas le nôtre.
        """
        copie = self.corpus_gonfle(corpus_reel, tmp_path, 0)

        assert copie.markdown == corpus_reel.markdown

    def test_la_sonde_mord_sur_la_carte_a_plat(
        self, corpus_reel: CarteDocumentation, tmp_path: Path
    ) -> None:
        """L'échantillon fautif d'abord : la forme d'avant #1316 ne tient pas le trimestre.

        27 469 tokens rejoués contre un budget de 16 000 — et c'est sur le corpus du
        2026-09-26 qu'elle cassait déjà, à 14 tokens près. Sur le seul corpus du
        produit (#1321), 20 332 le 2026-09-27 : toujours au-delà. Une sonde qui ne
        verrait pas ce défaut-là ne prouverait rien du sommaire.
        """
        gonfle = self.corpus_gonfle(corpus_reel, tmp_path, 3 * self.SECTIONS_PAR_MOIS)

        assert len(gonfle.sections) == len(corpus_reel.sections) + 3 * self.SECTIONS_PAR_MOIS
        assert estimer_tokens(carte_a_plat(gonfle)) > BUDGET_CARTE_TOKENS

    def test_un_trimestre_de_croissance_tient_sous_le_budget(
        self, corpus_reel: CarteDocumentation, tmp_path: Path
    ) -> None:
        """Le critère de #1316 : le sommaire absorbe trois mois au rythme mesuré.

        11 280 tokens rejoués le 2026-09-26, 8 937 le 2026-09-27 sur le corpus du
        produit (#1321) : la construction passe **avec le budget du module**, celui que
        le répondeur applique.
        """
        racine = tmp_path / "trimestre"
        self.corpus_gonfle(corpus_reel, racine, 3 * self.SECTIONS_PAR_MOIS)

        assert construire_carte(racine).tokens <= BUDGET_CARTE_TOKENS

    def test_une_annee_de_croissance_leve_encore(
        self, corpus_reel: CarteDocumentation, tmp_path: Path
    ) -> None:
        """Le budget reste une borne : un sommaire qui quadruplerait n'est plus petit.

        Sans cette moitié, on aurait pu « réparer » en relevant le budget sans fin —
        et la carte ne serait plus « assez petite pour tenir dans un prompt ».
        """
        racine = tmp_path / "annee"
        self.corpus_gonfle(corpus_reel, racine, 12 * self.SECTIONS_PAR_MOIS)

        with pytest.raises(CarteTropGrande):
            construire_carte(racine)


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

    def test_seul_un_document_qui_se_declare_du_produit_entre(self, tmp_path: Path) -> None:
        """Le corpus est **déclaré** (#1321) : ni l'emplacement ni le contenu n'y suffisent.

        Quatre documents aux emplacements du corpus : l'un se déclare du produit,
        deux du développement, le dernier ne se déclare pas. Un seul entre. Celui qui
        se tait reste dehors : le faire entrer serait deviner à sa place, et c'est ce
        que le ticket retire.
        """
        racine = ecrire_corpus(
            tmp_path,
            {
                "docs/00-guide.md": "# Guide\n\n## Ouvrir une conversation\n\nx\n",
                "docs/06-roadmap.md": DEVELOPPEMENT + "# Roadmap\n\n## Le fil\n\ny\n",
                "apps/web/README.md": DEVELOPPEMENT + "# Interface\n\n## Lancer en local\n\nz\n",
            },
        )
        (racine / "docs" / "07-muet.md").write_text("# Muet\n\n## Rien\n\nw\n", encoding="utf-8")

        carte = construire_carte(racine)

        assert carte.fichiers == ("docs/00-guide.md",)
        assert [section.identifiant for section in carte.sections] == [
            "docs/00-guide.md#Guide",
            "docs/00-guide.md#Ouvrir une conversation",
        ]
        assert "docs/06-roadmap.md" not in carte.markdown
        assert carte.section("docs/06-roadmap.md#Le fil") is None
        assert carte.section("docs/07-muet.md#Rien") is None

    @pytest.mark.parametrize(
        "entete",
        [
            pytest.param("<!-- documentation: utilisateur -->\n", id="public-inconnu"),
            pytest.param("<!-- documentation: Produit -->\n", id="casse-differente"),
            pytest.param("\n" + PRODUIT, id="pas-en-premiere-ligne"),
            pytest.param("<!-- produit -->\n", id="sans-la-cle"),
        ],
    )
    def test_une_declaration_qui_n_est_pas_la_forme_reste_dehors(
        self, tmp_path: Path, entete: str
    ) -> None:
        """La déclaration est un **contrat de forme**, pas une intention à reconnaître.

        Une ligne presque juste ne vaut pas déclaration : l'accepter reviendrait à
        interpréter ce que l'auteur a voulu dire. Le document reste dehors, et c'est
        `TestCorpusDuProduit` qui fait rougir le dépôt tant qu'il ne s'est pas déclaré.
        Écrit à la main : l'aide de la suite déclarerait à sa place.
        """
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "00-a.md").write_text(entete + "# A\n\n## Un\n\nx\n", encoding="utf-8")

        assert construire_carte(tmp_path).fichiers == ()

    def test_un_chapitre_du_developpement_sort_avec_ce_qu_il_porte(self, tmp_path: Path) -> None:
        """Un document du produit écarte un chapitre écrit pour qui le construit.

        C'est la forme du README de l'interface : les écrans d'abord, puis comment la
        lancer en local. Le chapitre déclaré sort, sa sous-section avec lui — elle n'a
        pas à le redire —, et le chapitre qui suit revient au produit.
        """
        contenu = (
            "# Interface\n\nLes écrans.\n\n"
            "## Le thème\n\nClair, sombre ou système.\n\n"
            "## Lancer en local\n" + DEVELOPPEMENT + "\n`npm run dev`\n\n"
            "### Le backend\n\n`maestro-api`\n\n"
            "## Le glossaire\n\nUn run est une exécution.\n"
        )
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))

        assert [section.titre for section in carte.sections] == [
            "Interface",
            "Le thème",
            "Le glossaire",
        ]
        assert "npm run dev" not in "".join(carte.textes.values())
        assert "Lancer en local" not in carte.markdown
        assert carte.section("docs/00-a.md#Le backend") is None

    @pytest.mark.parametrize(
        ("chapitre", "sort"),
        [
            pytest.param(
                "## Outils\n\n" + DEVELOPPEMENT + "\nx\n", True, id="apres-une-ligne-vide"
            ),
            pytest.param(
                "## Outils\n\nUn paragraphe.\n\n" + DEVELOPPEMENT, False, id="dans-le-corps"
            ),
            pytest.param("## Outils\n" + PRODUIT + "\nx\n", False, id="deja-du-produit"),
        ],
    )
    def test_la_declaration_d_un_chapitre_est_sa_premiere_ligne_non_vide(
        self, tmp_path: Path, chapitre: str, sort: bool
    ) -> None:
        """Plus bas, une ligne de même forme est du corps : un exemple, une citation."""
        contenu = "# A\n\nx\n\n" + chapitre + "\n## Suite\n\ny\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))

        assert (carte.section("docs/00-a.md#Outils") is None) is sort
        assert carte.section("docs/00-a.md#Suite") is not None

    def test_une_sous_section_ne_fait_pas_rentrer_ce_que_son_chapitre_a_sorti(
        self, tmp_path: Path
    ) -> None:
        """La règle ne va que dans un sens : sortir un chapitre sort tout ce qu'il porte."""
        contenu = "# A\n\n## Outils\n" + DEVELOPPEMENT + "\n### Détail\n" + PRODUIT + "\nx\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": contenu}))

        assert [section.titre for section in carte.sections] == ["A"]

    def test_un_document_du_developpement_ne_se_lit_pas_par_ses_chapitres(
        self, tmp_path: Path
    ) -> None:
        """Déclarer un chapitre du produit dans un document du développement ne fait rien."""
        contenu = DEVELOPPEMENT + "# Roadmap\n\n## Un écran\n" + PRODUIT + "\nx\n"
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/06-roadmap.md": contenu}))

        assert carte.fichiers == ()
        assert carte.sections == ()

    def test_la_declaration_ne_fait_pas_partie_d_une_section(self, tmp_path: Path) -> None:
        """Elle précède le premier titre : ni le texte lu, ni la ligne citée n'en portent trace."""
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/00-a.md": "# A\n\ncorps\n"}))

        section = carte.section("docs/00-a.md#A")
        assert section is not None
        assert carte.texte(section.identifiant) == "# A\n\ncorps"
        assert section.ligne == 2


class TestCorpusDuProduit:
    """L'assistant ne lit que la documentation du produit — déclarée, jamais devinée (#1321).

    Le 2026-09-25, à la question « Comment ouvrir une nouvelle conversation avec
    l'orchestrateur, et retrouver les précédentes ? », l'assistant a répondu juste ;
    mais l'une de ses quatre sources était la Roadmap, un document du **développement**
    du dépôt, dont quelqu'un qui a installé Maestro n'a que faire.

    La suite est écrite dans l'ordre du dossier :

    - **chaque document du dépôt se déclare**, produit ou développement. Un document
      qui se tait n'est rangé d'aucun côté par défaut : il fait rougir, avec la ligne
      à écrire. Un chapitre qui se déclare le fait dans un document du produit, et
      pour en sortir — ailleurs, sa déclaration ne ferait rien ;
    - **la sonde lit les déclarations elle-même** (`DECLARATION`) au lieu d'importer
      la règle du module — une sonde qui reprendrait ce qu'elle contrôle validerait sa
      faute avec lui ;
    - **elle est prouvée fautive d'abord** : sur le corpus d'avant ce ticket — tout
      `docs/` et le README de l'interface —, elle trouve la Roadmap citée ce jour-là,
      et les chapitres du README que le ticket nomme ; sur le corpus d'aujourd'hui,
      elle ne trouve plus rien ;
    - **rien du produit ne se perd** : chaque document qui se déclare du produit est
      lu, le passage qui répond à la question du 2026-09-25 se joint toujours, et le
      README reste lu pour ses écrans — il est seul à dire comment changer de thème.
    """

    #: La déclaration telle que la suite la lit — écrite ici, jamais importée.
    DECLARATION = re.compile(r"^<!--\s*documentation\s*:\s*(\S+)\s*-->\s*$")
    PUBLICS = ("produit", "développement")

    #: Les documents que le ticket nomme comme ceux du développement : la roadmap, le
    #: guide du POC, le workflow git, les démos et pilotes, l'audit des commandes, la
    #: migration de forge et l'outillage du design. Ils épinglent son jugement : en
    #: voir un revenir au produit défairait #1321 au lieu de le compléter.
    DEVELOPPEMENT_NOMMES = (
        "docs/06-roadmap.md",
        "docs/07-guide-de-demarrage.md",
        "docs/10-workflow-git.md",
        "docs/11-demo-poc.md",
        "docs/12-demo-mvp.md",
        "docs/13-demo-v1.md",
        "docs/14-run-fournisseur-non-anthropic.md",
        "docs/15-pilote-mcp-slack.md",
        "docs/16-pilote-mcp-tickets-gitlab.md",
        "docs/20-pilote-mcp-figma.md",
        "docs/23-demo-v2.md",
        "docs/25-audit-commandes-claude.md",
        "docs/27-decision-gitlab-vers-github.md",
        "docs/36-outillage-du-design.md",
    )

    #: Les chapitres du README de l'interface que le ticket nomme comme du
    #: développement : comment la lancer en local, et comment la vérifier.
    CHAPITRES_NOMMES = ("apps/web/README.md#Lancer en local", "apps/web/README.md#Vérifications")

    #: Le chapitre du README qui décrit les écrans — le seul à dire comment passer
    #: l'interface en thème sombre. Il doit rester lu.
    ECRANS_DU_README = "apps/web/README.md#L'interface de la Control Tower"

    #: L'échantillon fautif : la source que l'assistant a montrée le 2026-09-25,
    #: recopiée telle quelle. Figée ici, elle garde sa valeur de preuve le jour où la
    #: Roadmap renommera ce chapitre.
    CITEE_LE_2026_09_25 = "Roadmap — Maestro › « Le fil, un vrai interlocuteur » (2026-09-23)"
    ROADMAP = SectionDoc(
        fichier="docs/06-roadmap.md",
        titre="« Le fil, un vrai interlocuteur » (2026-09-23)",
        niveau=2,
        ancetres=("Roadmap — Maestro",),
        document="Roadmap — Maestro",
    )

    #: Le passage qui répond à la question du 2026-09-25 : l'écran des conversations,
    #: dans le document de l'interface.
    CONVERSATIONS = (
        "docs/05-interface-control-tower.md#2.11 💬 Conversations — en ouvrir une neuve, "
        "retrouver les précédentes *(#696 — **livré**)*"
    )

    @classmethod
    def public(cls, chemin: Path) -> str:
        """Le public que le document déclare en première ligne — `""` s'il se tait."""
        with chemin.open(encoding="utf-8") as flux:
            premiere = flux.readline().rstrip("\r\n")
        capture = cls.DECLARATION.match(premiere)
        return capture.group(1) if capture else ""

    @classmethod
    def chapitres_declares(cls, chemin: Path) -> list[tuple[int, int, str, str]]:
        """Les chapitres qui déclarent un public : `(ligne du titre, fin, titre, public)`.

        La déclaration d'un chapitre est la première ligne non vide qui suit son
        titre ; il court jusqu'au titre suivant de niveau égal ou supérieur, exclu.
        Les titres viennent du lecteur de titres du module — éprouvé à part, sur les
        blocs de code — mais la déclaration, elle, est relue ici.
        """
        lignes = chemin.read_text(encoding="utf-8").splitlines()
        titres = list(documentation._titres(lignes))
        declares: list[tuple[int, int, str, str]] = []
        for position, (index, niveau, titre) in enumerate(titres):
            premiere = next((ligne for ligne in lignes[index + 1 :] if ligne.strip()), "")
            capture = cls.DECLARATION.match(premiere)
            if capture is None:
                continue
            fin = next(
                (suivant for suivant, prof, _titre in titres[position + 1 :] if prof <= niveau),
                len(lignes),
            )
            declares.append((index + 1, fin, titre, capture.group(1)))
        return declares

    @classmethod
    def sonde(cls, sections: Iterable[SectionDoc]) -> list[SectionDoc]:
        """Les sections qui ne sont pas du produit : leur document, ou leur chapitre.

        Une section est fautive si son document ne se déclare pas du produit, ou si
        son titre tombe dans un chapitre qui se déclare d'un autre public.
        """
        publics: dict[str, str] = {}
        ecartees: dict[str, list[tuple[int, int]]] = {}
        fautives: list[SectionDoc] = []
        for section in sections:
            if section.fichier not in publics:
                chemin = RACINE / section.fichier
                publics[section.fichier] = cls.public(chemin)
                ecartees[section.fichier] = [
                    (debut, fin)
                    for debut, fin, _titre, public in cls.chapitres_declares(chemin)
                    if public != "produit"
                ]
            dans_un_chapitre_ecarte = any(
                debut <= section.ligne <= fin for debut, fin in ecartees[section.fichier]
            )
            if publics[section.fichier] != "produit" or dans_un_chapitre_ecarte:
                fautives.append(section)
        return fautives

    @staticmethod
    def corpus_d_avant() -> list[SectionDoc]:
        """Les sections que l'assistant lisait avant #1321 : tous les documents du dépôt."""
        sections: list[SectionDoc] = []
        for relatif, chemin in documents_du_depot(RACINE):
            texte = chemin.read_text(encoding="utf-8", errors="replace")
            sections.extend(
                section for section, _corps in documentation._sections_du_fichier(relatif, texte)
            )
        return sections

    def test_chaque_document_du_depot_declare_son_public(self) -> None:
        """Le geste écrit du critère 2 : aucun document n'est rangé par défaut.

        Un document neuf qui se tait fait rougir ici, et le message dit quoi écrire :
        c'est son auteur qui juge s'il sert à se servir de Maestro, jamais une règle
        qui le devinerait à sa place (#746). Les planchers disent que la frontière a
        deux côtés — 29 documents du produit et 17 du développement le 2026-09-27.
        """
        documents = documents_du_depot(RACINE)
        muets = [
            relatif for relatif, chemin in documents if self.public(chemin) not in self.PUBLICS
        ]
        assert muets == [], (
            "ces documents ne déclarent pas leur public en première ligne — "
            "« <!-- documentation: produit --> » s'ils disent ce que Maestro fait pour "
            "qui s'en sert, « <!-- documentation: développement --> » s'ils disent comment "
            f"le dépôt se construit : {muets}"
        )
        publics = Counter(self.public(chemin) for _relatif, chemin in documents)
        assert len(documents) >= 46
        assert publics["produit"] >= 25
        assert publics["développement"] >= 15

    @pytest.mark.parametrize("relatif", DEVELOPPEMENT_NOMMES)
    def test_les_documents_que_le_ticket_nomme_sont_du_developpement(self, relatif: str) -> None:
        assert self.public(RACINE / relatif) == "développement"

    def test_un_chapitre_ne_se_declare_que_pour_sortir_d_un_document_du_produit(self) -> None:
        """Ailleurs, sa déclaration ne ferait rien — et se lirait comme si elle faisait.

        Dans un document du développement, rien n'est lu : un chapitre qui s'y dirait
        du produit tromperait son lecteur. Dans un document du produit, un chapitre
        qui s'y dirait du produit ne dit rien de plus. Il reste un seul geste utile,
        et c'est le seul admis.
        """
        vus: list[tuple[str, str, str]] = []
        for relatif, chemin in documents_du_depot(RACINE):
            document = self.public(chemin)
            for _debut, _fin, titre, public in self.chapitres_declares(chemin):
                vus.append((relatif, titre, public))
                assert document == "produit", f"{relatif} › {titre} : document du {document}"
                assert public == "développement", f"{relatif} › {titre} : « {public} »"
        # Le README de l'interface en porte quatre : sans eux, cette garde ne dit rien.
        assert ("apps/web/README.md", "Lancer en local", "développement") in vus

    def test_la_sonde_voit_la_roadmap_citee_le_2026_09_25(self) -> None:
        """L'échantillon fautif, avant le balayage — sinon le vert qui suit ne dit rien.

        Deux moitiés. L'échantillon figé est bien la source citée ce jour-là, et la
        sonde le rejette. Et sur le corpus d'avant ce ticket, rejoué document par
        document, elle mord : la Roadmap y était lue, le workflow git avec elle, et les
        chapitres du README qui disent comment lancer et vérifier l'interface.
        """
        assert self.ROADMAP.citation == self.CITEE_LE_2026_09_25
        assert self.sonde([self.ROADMAP]) == [self.ROADMAP]

        avant = self.sonde(self.corpus_d_avant())
        documents = {section.fichier for section in avant}
        assert "docs/06-roadmap.md" in documents
        assert "docs/10-workflow-git.md" in documents
        assert set(self.CHAPITRES_NOMMES) <= {section.identifiant for section in avant}
        assert self.ECRANS_DU_README not in {section.identifiant for section in avant}
        assert len(avant) > 100

    def test_aucune_section_du_corpus_ne_vient_du_developpement(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le balayage, une fois la sonde prouvée : pas une section, pas un fichier."""
        assert self.sonde(corpus_reel.sections) == []
        assert [
            relatif
            for relatif in corpus_reel.fichiers
            if self.public(RACINE / relatif) != "produit"
        ] == []

    def test_la_roadmap_ne_se_joint_plus(self, corpus_reel: CarteDocumentation) -> None:
        """Ni au sommaire, ni par son identifiant : le modèle ne peut plus la nommer."""
        assert "docs/06-roadmap.md" not in corpus_reel.markdown
        assert corpus_reel.section(self.ROADMAP.identifiant) is None
        assert corpus_reel.sections_du_fichier("docs/06-roadmap.md") == ()

    @pytest.mark.parametrize("identifiant", CHAPITRES_NOMMES)
    def test_les_chapitres_du_developpement_du_readme_ne_se_joignent_plus(
        self, corpus_reel: CarteDocumentation, identifiant: str
    ) -> None:
        """Le README reste lu, mais pas ce qu'il dit à qui code l'interface."""
        fichier, titre = identifiant.split("#", 1)
        assert corpus_reel.section(identifiant) is None
        assert (fichier, titre) not in {
            (lu, cle) for lu, _marque, cle in lignes_de_carte(corpus_reel.markdown)
        }

    def test_le_readme_reste_lu_pour_ses_ecrans(self, corpus_reel: CarteDocumentation) -> None:
        """Critère 3, sur la réponse que sortir le README entier aurait perdue.

        « Comment passer l'interface en thème sombre ? » ne trouve sa réponse que
        dans le premier chapitre du README : l'interface (`docs/05`) n'en dit rien.
        C'est ce qui a fait écarter le README par chapitres plutôt qu'en entier.
        """
        texte = corpus_reel.texte(self.ECRANS_DU_README)
        assert texte is not None
        assert "Thème clair / sombre" in texte
        assert "apps/web/README.md" in corpus_reel.fichiers

    def test_chaque_document_du_produit_reste_lu(self, corpus_reel: CarteDocumentation) -> None:
        """Critère 3 : ce qui décrit le produit ne sort pas avec le reste.

        La comparaison est faite contre la déclaration relue par la suite, pas contre
        `fichiers_corpus` : un filtre qui laisserait tomber un document du produit en
        chemin rougit ici.
        """
        produit = {
            relatif
            for relatif, chemin in documents_du_depot(RACINE)
            if self.public(chemin) == "produit"
        }
        assert set(corpus_reel.fichiers) == produit
        for relatif in produit:
            assert f"\n## {relatif}\n" in corpus_reel.markdown

    def test_la_question_du_2026_09_25_garde_sa_reponse(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le passage qui y répond se joint toujours, par le chemin que prend le modèle.

        Il est de niveau 3 : son chapitre est au sommaire, marqué `+`, et il se lit
        au détail de ce chapitre (#1316). Sa citation ne nomme que le produit.
        """
        section = corpus_reel.section(self.CONVERSATIONS)
        assert section is not None
        texte = corpus_reel.texte(section.identifiant)
        assert texte is not None
        assert "Nouvelle conversation" in texte

        chapitre = corpus_reel.section(f"{section.fichier}#{section.ancetres[-1]}")
        assert chapitre is not None
        assert ("+", chapitre.cle_titre) in {
            (marque, titre)
            for fichier, marque, titre in lignes_de_carte(corpus_reel.markdown)
            if fichier == section.fichier
        }
        assert f"- {section.cle_titre}" in corpus_reel.detail([chapitre.identifiant])
        assert section.citation.startswith("Interface — Control Tower — Maestro › ")
        assert self.sonde([section]) == []


class TestResolutionDIdentifiant:
    """Résoudre un identifiant est une recherche dans un index — jamais un accès disque."""

    def test_un_identifiant_inconnu_rend_none(self, corpus_reel: CarteDocumentation) -> None:
        """`None` est nominal : l'identifiant vient du modèle, qui peut se tromper.

        C'est ce qui laisse le lot 3 avouer une ignorance au lieu de lever — le budget,
        lui, lève, parce qu'il constate un défaut du corpus que personne ne verrait.
        """
        inconnue = "docs/05-interface-control-tower.md#section qui n'existe pas"
        assert corpus_reel.texte(inconnue) is None
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
        reecrit = "# A\n\n## Un\n\ncorps réécrit et plus long\n\n## Deux\n\nneuve\n"
        ecrire_corpus(racine, {"docs/00-a.md": reecrit})
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

    def test_l_empreinte_couvre_aussi_les_documents_du_developpement(
        self, tmp_path: Path
    ) -> None:
        """Elle porte sur **tout** ce qui peut entrer, pas seulement sur ce qui est entré.

        C'est le prix d'une déclaration lue dans le document : pour savoir qu'un
        document a changé de public, il faut surveiller aussi ceux qui sont dehors —
        et c'est toujours un `stat` par fichier, jamais une lecture (#1321).
        """
        racine = ecrire_corpus(
            tmp_path, {"docs/00-a.md": "# A\n", "docs/01-b.md": DEVELOPPEMENT + "# B\n"}
        )
        carte = construire_carte(racine)

        assert carte.fichiers == ("docs/00-a.md",)
        assert [relatif for relatif, _date, _taille in carte.empreinte] == [
            "docs/00-a.md",
            "docs/01-b.md",
        ]

    def test_un_document_qui_change_de_public_refait_la_carte(self, tmp_path: Path) -> None:
        """Se déclarer du produit fait entrer, se déclarer du développement fait sortir."""
        racine = ecrire_corpus(
            tmp_path,
            {"docs/00-a.md": "# A\n", "docs/01-b.md": DEVELOPPEMENT + "# B\n\n## B1\n\nx\n"},
        )
        assert carte_documentation(racine).fichiers == ("docs/00-a.md",)

        ecrire_corpus(racine, {"docs/01-b.md": "# B\n\n## B1\n\nx\n"})
        entree = carte_documentation(racine)
        assert entree.fichiers == ("docs/00-a.md", "docs/01-b.md")
        assert entree.section("docs/01-b.md#B1") is not None

        ecrire_corpus(racine, {"docs/01-b.md": DEVELOPPEMENT + "# B\n\n## B1\n\nx\n"})
        assert carte_documentation(racine).fichiers == ("docs/00-a.md",)

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


class TestCitationSansRenvoiInterne:
    """Ce que l'assistant affiche de ses lectures ne renvoie jamais à l'intérieur (#1199).

    #939 avait retiré le **nom du fichier** de la citation ; il restait le **chemin
    des titres**, et les titres portent eux-mêmes ce qu'on venait d'ôter. La suite
    est écrite dans l'ordre du dossier :

    - **la sonde d'abord, et prouvée fautive** (`test_la_sonde_trouve_bien_les_titres_fautifs`).
      Un balayage qui ne trouve rien ne prouve rien : elle est passée sur les titres
      **bruts** du corpus, où elle doit trouver les trois familles, avant d'être
      passée sur les citations, où elle ne doit plus rien trouver ;
    - **les échantillons sont réels et figés** (`ECHANTILLONS`) : recopiés du corpus
      du 2026-09-22, ils gardent leur valeur de preuve le jour où le corpus se
      nettoie — un test qui cherche un titre fautif dans le corpus rougirait alors
      sans qu'aucun défaut n'existe ;
    - **les faux positifs sont des cas, pas des exceptions** (`TITRES_INTACTS`) :
      « Phases 5/6 » n'est pas un chemin, `prio::` n'est pas un deux-points à
      recoudre, et `/mr-fix` est une commande. Chacun a été vu sur le corpus réel,
      chacun est tenu par la **forme** du motif.
    """

    #: Ce qui renvoie l'utilisateur là où il ne peut pas aller — trois familles,
    #: nommées séparément pour que l'échantillon fautif se cherche avec la sonde
    #: elle-même plutôt qu'avec un motif voisin qui lui mentirait.
    #:
    #: Elles sont écrites ici et **non importées** du module : une sonde qui
    #: reprendrait le motif qu'elle contrôle validerait sa faute avec lui. Un chemin
    #: s'y reconnaît à un dossier du dépôt ou à une extension de fichier — un `/`
    #: entre deux mots (« activer/désactiver », « Phases 5/6 ») n'en fait pas un.
    LIEN = r"\[[^\]]*\]\([^)]*\)"
    TICKET = r"#\d+"
    FICHIER = (
        r"(?:docs|scripts|tests|maestro|apps|core|agents|infra|components|lib|app"
        r"|\.claude|\.maestro|\.agents)/"
        r"|[\w.@+-]+\.(?:md|py|sh|json|tsx|ts|yml|mjs|toml|css)\b"
    )
    SONDE = re.compile(f"{LIEN}|{TICKET}|{FICHIER}")

    #: Des titres **du corpus réel** au 2026-09-22 — un par famille, et deux mixtes.
    ECHANTILLONS = (
        (
            "2.7 📁 Projets et composition d'un objectif *(retenu — "
            "[docs/24](./24-projets-locaux-et-poste-de-travail.md), **Phases 7 et 8**)*",
            "2.7 📁 Projets et composition d'un objectif",
        ),
        (
            "Projets, ressources locales et poste de travail — cadrage (ticket #215)",
            "Projets, ressources locales et poste de travail — cadrage",
        ),
        (
            "6.7 Projets de l'utilisateur — CRUD et explorateur de dossiers (#223) — **livré**",
            "6.7 Projets de l'utilisateur — CRUD et explorateur de dossiers — livré",
        ),
        (
            "3.9 Ce qui garde le dispositif — "
            "[`tests/test_cycle_de_vie.py`](../tests/test_cycle_de_vie.py) (#366)",
            "3.9 Ce qui garde le dispositif",
        ),
        ("11.3 Un ticket, une session — `run.sh`", "11.3 Un ticket, une session"),
        ("Le jeu d'icônes — `components/Icones.tsx`", "Le jeu d'icônes"),
        ("3. Le cas réel, chiffré : `scripts/orchestrate/`", "3. Le cas réel, chiffré"),
        (
            "2.1 Ce que l'ouverture aux projets locaux ajoute *(en vigueur — "
            "[docs/24 §2.5](./24-projets-locaux-et-poste-de-travail.md), **Phase 7** livrée)*",
            "2.1 Ce que l'ouverture aux projets locaux ajoute",
        ),
    )

    #: Ce que le motif ne doit **pas** prendre, vu sur le corpus réel : un rapport de
    #: nombres, un identifiant à double deux-points, une commande, une route, un
    #: module Python, une paire de verbes.
    TITRES_INTACTS = (
        "6. Contrats d'API v2 (Phases 5/6) — formes JSON figées",
        "3.2 Les labels de catégorisation — type::, agent::, prio::",
        "8.3 PR non mergeable — remédiation (/mr-fix, anciennement /pipeline-fix)",
        "4.2 O2 — Passer par la file (maestro.queue) : l'option n'existe pas",
        "6.5 — Contrôle de capacité : activer/désactiver, instances",
        "2.1 Le quatrième mode : sans_secret",
    )

    def test_la_sonde_trouve_bien_les_titres_fautifs(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """L'échantillon fautif, avant le balayage — sinon le vert ne dit rien.

        Le corpus du 2026-09-22 portait 10 titres à lien, 127 à numéro de ticket et
        31 à chemin de fichier, sur 801 sections. Les planchers sont bas à dessein :
        ils prouvent que la sonde mord, sans rougir le jour où la documentation se
        nettoie d'elle-même.
        """
        titres = [section.titre for section in corpus_reel.sections]
        liens = [titre for titre in titres if re.search(self.LIEN, titre)]
        tickets = [titre for titre in titres if re.search(self.TICKET, titre)]
        chemins = [titre for titre in titres if re.search(self.FICHIER, titre)]
        assert liens, "le corpus ne porte plus de titre à lien : l'échantillon a disparu"
        assert len(tickets) >= 20
        assert len(chemins) >= 5
        assert all(self.SONDE.search(titre) for titre in liens + tickets + chemins)

    def test_aucune_citation_du_corpus_reel_ne_renvoie_a_l_interieur(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le balayage, une fois la sonde prouvée : 801 sections, zéro renvoi.

        C'est la cinquième surface du constat G6, et la seule qui se vérifie d'un
        coup : la citation est **dérivée** des titres, donc tout titre fautif du
        corpus passe ici sans qu'on ait à l'y chercher.
        """
        fautives = [
            (section.identifiant, section.citation)
            for section in corpus_reel.sections
            if self.SONDE.search(section.citation)
        ]
        assert fautives == []

    def test_aucune_citation_ne_perd_le_nom_de_sa_section(
        self, corpus_reel: CarteDocumentation
    ) -> None:
        """Le critère 2 : une source citée reste **redemandable**, donc nommée.

        Assainir ne doit pas vider : sur le corpus réel, aucune citation n'est vide,
        et aucune ne se réduit à l'aveu d'un trou.
        """
        for section in corpus_reel.sections:
            citation = section.citation
            assert citation.strip(), section.identifiant
            assert citation.replace(documentation.ELLIPSE, "").strip(), section.identifiant

    @pytest.mark.parametrize(("brut", "attendu"), ECHANTILLONS)
    def test_les_titres_reels_fautifs_se_citent_proprement(self, brut: str, attendu: str) -> None:
        """Ce que le ticket demandait, titre par titre — et le rendu exact, pas « sans `#` ».

        Vérifier l'absence de la faute laisserait passer un titre charcuté ; c'est la
        chaîne entière qui est attendue, donc aussi la recouture.
        """
        assert documentation.titre_citable(brut) == attendu

    @pytest.mark.parametrize("titre", TITRES_INTACTS)
    def test_ce_qui_n_est_pas_un_renvoi_reste_entier(self, titre: str) -> None:
        """Les faux positifs mesurés — chacun tenu par la forme du motif.

        `activer/désactiver` est celui qui a coûté : un `/` entre deux mots, dont le
        second commence par un accent que nul segment de chemin ne porte — sans le
        `(?!\\w)` qui ferme le motif, la citation rendait « …ésactiver ».
        """
        assert documentation.titre_citable(titre) == titre

    def test_une_mention_au_milieu_avoue_son_trou(self) -> None:
        """L'ellipse plutôt qu'une phrase recousue : la citation ne ment pas.

        « 7.1 Ce que a laissé derrière lui » se lirait comme le titre exact. Avec
        l'ellipse, le lecteur voit qu'un mot a été retiré — et ce mot ne lui aurait
        rien appris, puisqu'il n'a pas la forge.
        """
        assert documentation.titre_citable("7.1 Ce que #647 a laissé derrière lui") == (
            "7.1 Ce que … a laissé derrière lui"
        )

    def test_un_groupe_parenthese_qui_cite_un_ticket_tombe_en_entier(self) -> None:
        """Une annotation qui a besoin d'un numéro de ticket est de l'appareil de suivi.

        La garder à moitié — « (disponible — ticket …) » — rendrait la citation plus
        bavarde et moins informative que si elle tombait.
        """
        brut = "6.1 — File de tâches et workers parallèles (disponible — ticket #41)"
        assert documentation.titre_citable(brut) == "6.1 — File de tâches et workers parallèles"

    def test_un_segment_qui_n_a_plus_rien_a_dire_disparait(self) -> None:
        """Le tiret cadratin sépare le sujet de ce qui le qualifie : la queue tombe seule.

        Elle ne tombe que si elle n'a **plus rien** à dire : « — `journal.sh audit` »
        garde son « audit », et la citation le montre plutôt que de trancher à la
        place de qui l'a écrit.
        """
        assert documentation.titre_citable("11.5 Savoir où en est un run — `status.sh`") == (
            "11.5 Savoir où en est un run"
        )
        assert documentation.titre_citable("11.2 L'ordre, figé une fois — `queue.sh`") == (
            "11.2 L'ordre, figé une fois"
        )

    def test_le_balisage_markdown_ne_sort_pas_vers_l_utilisateur(self) -> None:
        """Une citation est du **texte** : `**livré**` s'afficherait en gras ou en brut.

        Le retrait vaut aussi pour les `*` d'un groupe en italique dont l'intérieur
        vient de tomber — sans lui, la citation porterait des astérisques orphelins.
        """
        assert documentation.titre_citable("2.0 Le projet actif (#281) — **livré**") == (
            "2.0 Le projet actif — livré"
        )

    def test_un_titre_entierement_fait_de_renvois_est_omis_du_chemin(self) -> None:
        """Le repli : omettre le maillon, jamais rendre le titre brut.

        Le corpus n'en porte aucun aujourd'hui — et c'est justement pourquoi le cas
        se tient ici : le jour où il apparaît, la citation doit perdre un maillon,
        pas afficher `docs/10-workflow-git.md`.
        """
        section = SectionDoc(
            fichier="docs/00-a.md",
            titre="`docs/10-workflow-git.md` (#366)",
            niveau=3,
            ancetres=("Guide", "2. Le cycle de vie"),
            document="Guide",
        )
        assert documentation.titre_citable(section.titre) == ""
        assert section.citation == "Guide › 2. Le cycle de vie"

    def test_la_citation_assainit_aussi_le_nom_du_document(self, tmp_path: Path) -> None:
        """Le nom du document est un titre de niveau 1 : il porte les mêmes renvois.

        C'est le deuxième exemple du ticket — « Projets… — cadrage (ticket #215) »
        ouvrait la citation, donc le renvoi s'affichait en tête de ligne.
        """
        contenu = (
            "# Projets, ressources locales et poste de travail — cadrage (ticket #215)\n\n"
            "## 2.3 L'entité Projet\n\nx\n"
        )
        carte = construire_carte(ecrire_corpus(tmp_path, {"docs/24-p.md": contenu}))

        section = carte.section("docs/24-p.md#2.3 L'entité Projet")
        assert section is not None
        assert section.citation == (
            "Projets, ressources locales et poste de travail — cadrage › 2.3 L'entité Projet"
        )
