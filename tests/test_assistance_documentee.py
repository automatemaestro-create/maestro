"""Le répondeur documenté de l'assistance : câblage et repli (#764, lot 2 de #748).

Ce que cette suite tient, et ce qu'elle ne tient pas — la frontière est celle du
ticket, et la nommer évite de croire garde ce qui ne l'est pas :

- **elle tient la plomberie** : les deux appels ont bien lieu, la carte entre dans le
  premier, le *texte* des sections choisies dans le second, les sources citées sont
  celles qui y sont entrées, et aucune façon d'être privé de modèle ne rend un 502 ;
- **elle ne tient pas la qualité du jugement** — que le modèle choisisse les bonnes
  sections et sache s'abstenir relève du prompt, se mesure en usage, et l'échantillon
  hors périmètre qui l'éprouve est le lot 3 (#765). Ici le modèle est un double
  scripté : ce qui est vérifié est ce qui l'**atteint** et ce qu'on fait de sa
  réponse.

Le corpus est **jetable** partout où l'assertion porte sur un contenu : écrire les
attentes contre la documentation réelle les ferait rougir au prochain ticket qui
retouche un titre. Deux tests font exception et le disent — ceux du critère 1, qui
ne valent que sur le vrai corpus.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.assistance import (
    AGENT_ASSISTANCE,
    NOM_ASSISTANCE,
    RepondeurAssistance,
    repondre_assistance,
)
from maestro.controltower.assistance_documentee import (
    BUDGET_SECTIONS_TOKENS,
    SECTIONS_MAX,
    RepondeurAssistanceDocumentee,
    Selection,
    bloc_sources,
    identifiants_choisis,
    selection_sections,
)
from maestro.controltower.chat import ChatStore, MessageChat, RepondeurChat
from maestro.controltower.documentation import (
    CarteDocumentation,
    construire_carte,
    oublier_carte,
)
from maestro.controltower.events import InMemoryEventBus
from maestro.providers.base import ModelProvider

UTILISATEUR = "utilisateur"

#: La racine du dépôt — le corpus réel, celui que le produit servira.
RACINE = Path(__file__).resolve().parents[1]

#: La question du critère 1 : l'exemple **mesuré** de #684, celui que le resserrement
#: seul laissait sur l'orientation et que #684 n'avait pu servir qu'en ajoutant une
#: entrée à sa table. C'est celle que le chantier doit désormais servir depuis la
#: documentation.
QUESTION_684 = "Où est le bouton pour relancer un run bloqué ?"


@pytest.fixture(autouse=True)
def _cache_neuf() -> Iterator[None]:
    """Aucun test ne part du cache d'un autre — ni ne le laisse derrière lui.

    Le cache de `carte_documentation` est global à la racine : un corpus jetable
    réécrit sous le même `tmp_path` d'un test à l'autre s'y confondrait.
    """
    oublier_carte()
    yield
    oublier_carte()


def ecrire_corpus(racine: Path, fichiers: Mapping[str, str]) -> Path:
    """Monte un corpus jetable — mêmes emplacements que le vrai, contenu au choix.

    Redéfini ici plutôt qu'importé de `test_documentation` : c'est de la plomberie de
    fixture (deux écritures), et faire dépendre une suite d'une autre créerait un lien
    que rien ne déclare. Ce qui ne doit exister qu'une fois est la **règle** — elle
    est dans `maestro.controltower.documentation`, et les deux suites l'interrogent.
    """
    for relatif, contenu in fichiers.items():
        chemin = racine / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu, encoding="utf-8")
    return racine


#: Un corpus de deux fichiers, dont on connaît les identifiants par cœur. « Runs » y
#: porte une phrase que le déterministe de #123 **n'a pas** : c'est elle qui prouve,
#: quand elle ressort, que la réponse vient de la documentation et non de la table.
CORPUS = {
    "docs/00-runs.md": (
        "# Runs\n\n"
        "## Relancer un run\n\n"
        "Le bouton « Reprendre » vit sur la carte du run.\n\n"
        "## Mettre en pause\n\n"
        "Le bouton « Mettre en pause » vit au même endroit.\n"
    ),
    "apps/web/README.md": ("# Control Tower\n\n## Le thème\n\nSoleil/lune, barre supérieure.\n"),
}

RELANCER = "docs/00-runs.md#Relancer un run"
PAUSE = "docs/00-runs.md#Mettre en pause"
THEME = "apps/web/README.md#Le thème"


class ModeleScripte(ModelProvider):
    """Un fournisseur qui rend ses réponses **dans l'ordre**, et note ce qu'on lui donne.

    L'ordre est le sujet : ce répondeur fait deux appels de nature différente, et un
    double qui rendrait toujours la même chose ne dirait pas lequel a reçu quoi. Il
    note donc prompts et systèmes séparément — c'est la moitié du critère 2 qu'aucune
    assertion sur la réponse ne couvrirait : ce qui compte est ce qui **atteint** le
    modèle.

    Une réponse de trop demandée est une erreur franche et non un dernier élément
    répété : un test qui attend deux appels et en obtient trois doit rougir là, pas
    trois assertions plus loin.
    """

    name = "modele-scripte"

    def __init__(self, *reponses: str) -> None:
        self.reponses = list(reponses)
        self.prompts: list[str] = []
        self.systemes: list[str | None] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.prompts.append(prompt)
        self.systemes.append(system_prompt)
        if not self.reponses:
            raise AssertionError(f"appel modèle n°{len(self.prompts)} non prévu par le script")
        return self.reponses.pop(0)


class ModeleEnPanne(ModelProvider):
    """Un fournisseur qui lève — réseau coupé, authentification refusée."""

    name = "panne"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        raise RuntimeError("fournisseur indisponible")


class ModeleMuet(ModelProvider):
    """Un fournisseur qui répond sans rien dire — la troisième panne (#686)."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return "   \n"


def _fil(*contenus: str) -> list[MessageChat]:
    """Un fil dont les messages alternent utilisateur / assistant, l'utilisateur d'abord."""
    return [
        MessageChat(
            agent=NOM_ASSISTANCE,
            auteur=UTILISATEUR if rang % 2 == 0 else NOM_ASSISTANCE,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


def _repondre(repondeur: RepondeurChat, *contenus: str) -> str:
    """La réponse du répondeur au fil donné — le raccourci de toute la suite."""
    return asyncio.run(repondeur.repondre(AGENT_ASSISTANCE, _fil(*contenus)))


def _monte(
    racine: Path, *reponses: str, **kwargs
) -> tuple[RepondeurAssistanceDocumentee, ModeleScripte]:
    """Le répondeur et son modèle scripté, montés ensemble — les tests ont besoin des deux."""
    modele = ModeleScripte(*reponses)
    return (
        RepondeurAssistanceDocumentee(provider=modele, racine=racine, **kwargs),
        modele,
    )


# ── ① le contrat de sortie du premier appel ───────────────────────────────────
#
# Lire une liste d'identifiants est un **parseur de format connu**, jamais un
# jugement sur du texte humain : ce qui est lu ici vient du modèle, dans la forme
# qu'on lui a demandée. Aucun mot n'y est reconnu — c'est le `#` qui fait la clé,
# ce qui rend la liste vide gratuite et supprime la sentinelle à tenir d'accord
# avec le prompt.


class TestIdentifiantsChoisis:
    def test_une_ligne_par_identifiant_dans_l_ordre_rendu(self) -> None:
        """L'ordre est conservé : c'est lui qui décide de ce que le budget garde."""
        assert identifiants_choisis(f"{PAUSE}\n{RELANCER}") == (PAUSE, RELANCER)

    @pytest.mark.parametrize(
        "rendu",
        [
            pytest.param(f"- {RELANCER}", id="puce-tiret"),
            pytest.param(f"* {RELANCER}", id="puce-etoile"),
            pytest.param(f"1. {RELANCER}", id="numerotation"),
            pytest.param(f"`{RELANCER}`", id="accents-graves"),
            pytest.param(f"```\n{RELANCER}\n```", id="bloc-de-code"),
            pytest.param(f"   {RELANCER}   ", id="indentation"),
        ],
    )
    def test_les_habillages_qu_un_modele_ajoute_sont_retires(self, rendu: str) -> None:
        """On demande une liste nue ; un modèle la décore. Trois tolérances, pas plus."""
        assert identifiants_choisis(rendu) == (RELANCER,)

    def test_un_doublon_ne_compte_qu_une_fois_et_garde_sa_place(self) -> None:
        assert identifiants_choisis(f"{RELANCER}\n{PAUSE}\n{RELANCER}") == (
            RELANCER,
            PAUSE,
        )

    @pytest.mark.parametrize(
        "rendu",
        [
            pytest.param("", id="vide"),
            pytest.param("   \n\n  ", id="blanc"),
            pytest.param("AUCUNE", id="sentinelle-d-un-autre-prompt"),
            pytest.param(
                "Rien dans la documentation ne répond à cette question.",
                id="phrase-de-refus",
            ),
            pytest.param("# Un titre", id="titre-markdown"),
        ],
    )
    def test_ce_qui_ne_porte_pas_de_cle_ne_devient_pas_une_section(self, rendu: str) -> None:
        """La liste vide est **gratuite** : aucun mot n'est reconnu, donc rien à tenir.

        Le dernier cas est le seul piège de forme : un `#` en tête est un titre
        Markdown, et le fichier ouvre toujours la clé.
        """
        assert identifiants_choisis(rendu) == ()


# ── ② ce qui entre dans le second prompt, et ce qui n'y entre pas ─────────────


class TestSelectionSections:
    @pytest.fixture()
    def carte(self, tmp_path: Path) -> CarteDocumentation:
        return construire_carte(ecrire_corpus(tmp_path, CORPUS))

    def test_les_sections_nommees_sont_retenues_dans_l_ordre(
        self, carte: CarteDocumentation
    ) -> None:
        selection = selection_sections(carte, [THEME, RELANCER])

        assert [section.identifiant for section in selection.retenues] == [
            THEME,
            RELANCER,
        ]
        assert selection.ecartees == ()
        assert selection.inconnues == ()

    def test_une_cle_qui_ne_resout_rien_est_ecartee_sans_emporter_les_autres(
        self, carte: CarteDocumentation
    ) -> None:
        """Le modèle peut recopier une clé de travers : c'est une section de moins.

        Ce n'est **pas** une panne — la traiter comme telle ferait replier tout le
        canal sur une faute de frappe du modèle.
        """
        selection = selection_sections(carte, ["docs/00-runs.md#Inventée", RELANCER])

        assert [section.identifiant for section in selection.retenues] == [RELANCER]
        assert selection.inconnues == ("docs/00-runs.md#Inventée",)

    def test_deux_cles_de_la_meme_section_ne_la_paient_qu_une_fois(
        self, carte: CarteDocumentation
    ) -> None:
        """La résolution tolère la casse et l'espacement (#763) — pas le budget."""
        selection = selection_sections(carte, [RELANCER, RELANCER.upper()])

        assert len(selection.retenues) == 1

    def test_au_dela_du_plafond_les_suivantes_sont_ecartees_et_nommees(
        self, carte: CarteDocumentation
    ) -> None:
        """Le plafond borne la dispersion là où le budget borne le coût."""
        selection = selection_sections(carte, [RELANCER, PAUSE, THEME], maximum=2)

        assert [section.identifiant for section in selection.retenues] == [
            RELANCER,
            PAUSE,
        ]
        assert [section.identifiant for section in selection.ecartees] == [THEME]

    def test_une_section_trop_grosse_est_ecartee_entiere_jamais_amputee(
        self, carte: CarteDocumentation
    ) -> None:
        """Rien n'est tronqué : un extrait coupé fait répondre sur ce qu'on lui a retiré.

        Et le parcours **continue** — une petite section qui tient vaut mieux qu'un
        budget laissé vide. Le budget est choisi **entre** les deux coûts mesurés du
        corpus de test (24 et 16 tokens) : au-dessous des deux, le test passerait en
        ne retenant rien, c'est-à-dire sans jamais poser la question.
        """
        selection = selection_sections(carte, [RELANCER, THEME], budget_tokens=20)

        assert [section.identifiant for section in selection.retenues] == [THEME]
        assert [section.identifiant for section in selection.ecartees] == [RELANCER]

    def test_le_budget_du_depot_laisse_passer_le_plafond_de_sections(
        self, carte: CarteDocumentation
    ) -> None:
        """Les deux bornes se tiennent : le cas ordinaire n'en rencontre aucune.

        Mesure du 2026-08-28 sur le corpus réel — six sections au p90 pèsent 10 914
        tokens, contre un budget de 24 000. Un budget qui rognerait le plafond ferait
        de `SECTIONS_MAX` un chiffre décoratif.
        """
        assert BUDGET_SECTIONS_TOKENS > SECTIONS_MAX * 1_819
        selection = selection_sections(carte, [RELANCER, PAUSE, THEME])

        assert len(selection.retenues) == 3


class TestBlocSources:
    def test_une_ligne_par_section_avec_son_chemin_complet(self, tmp_path: Path) -> None:
        """Fichier **et** section, ce que le critère 2 demande — `SectionDoc.chemin`."""
        carte = construire_carte(ecrire_corpus(tmp_path, CORPUS))

        bloc = bloc_sources(selection_sections(carte, [RELANCER]).retenues)

        assert "docs/00-runs.md" in bloc
        assert "Relancer un run" in bloc

    def test_sans_section_il_n_y_a_pas_d_en_tete_orphelin(self) -> None:
        assert bloc_sources(()) == ""
        assert bloc_sources(Selection().retenues) == ""


# ── ③ les deux appels, et ce qui les atteint ─────────────────────────────────


class TestDeuxAppels:
    def test_le_premier_recoit_la_carte_le_second_le_texte_des_sections(
        self, tmp_path: Path
    ) -> None:
        """Le cœur du dispositif : on choisit sur la carte, on répond sur les extraits.

        L'assertion porte sur ce qui **atteint** le modèle, pas sur ce qu'il rend :
        un répondeur qui passerait le corpus entier au premier appel, ou la seule
        carte au second, rendrait la même réponse et serait pourtant faux.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, RELANCER, "Par « Reprendre ».")

        _repondre(repondeur, QUESTION_684)

        choix, reponse = modele.prompts
        assert "Carte de la documentation" in choix
        assert "Relancer un run" in choix
        # La carte porte les titres, jamais le corps : c'est ce qui la rend
        # quarante-sept fois moins chère que le corpus.
        assert "Le bouton « Reprendre » vit sur la carte du run." not in choix
        assert "Le bouton « Reprendre » vit sur la carte du run." in reponse
        # Et le second ne reçoit que ce qui a été choisi.
        assert "Le bouton « Mettre en pause » vit au même endroit." not in reponse

    def test_les_deux_appels_ne_partagent_pas_le_meme_cadre(self, tmp_path: Path) -> None:
        """Trier et répondre sont deux tâches : deux consignes dans un prompt de tri
        rendent un tri commenté."""
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, RELANCER, "Par « Reprendre ».")

        _repondre(repondeur, QUESTION_684)

        cadre_choix, cadre_reponse = modele.systemes
        assert "Ta seule tâche : choisir les sections" in (cadre_choix or "")
        # Le second porte le cadre de la fiche — donc l'aveu d'ignorance prescrit.
        assert "DIS QUE TU NE SAIS PAS" in (cadre_reponse or "")
        assert "à partir d'EUX SEULS" in (cadre_reponse or "")

    def test_la_question_atteint_les_deux_appels(self, tmp_path: Path) -> None:
        """Sans elle, le premier trierait à l'aveugle et le second répondrait à côté."""
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, RELANCER, "Par « Reprendre ».")

        _repondre(repondeur, QUESTION_684)

        assert all(QUESTION_684 in prompt for prompt in modele.prompts)

    def test_une_section_ecartee_est_nommee_au_modele_plutot_que_taue(self, tmp_path: Path) -> None:
        """Un modèle qui sait qu'une section lui manque peut en tenir compte.

        L'omettre lui ferait croire qu'il a tout lu — et répondre avec l'aplomb de
        celui qui a tout lu.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, f"{RELANCER}\n{PAUSE}", "Voilà.", sections_max=1)

        _repondre(repondeur, QUESTION_684)

        _choix, reponse = modele.prompts
        assert "n'ont pas pu être jointes" in reponse
        assert "Mettre en pause" in reponse


# ── ④ les sources citées sont celles qui ont été passées ─────────────────────
#
# Le critère 2, et la seule façon de le tenir : le bloc est **construit** à partir
# des sections retenues, jamais recopié de la réponse du modèle. Un modèle à qui
# l'on demanderait ses sources pourrait en nommer une qu'il n'a pas eue, et la
# propriété deviendrait une espérance.


class TestCitations:
    def test_la_reponse_cite_le_fichier_et_la_section_lus(self, tmp_path: Path) -> None:
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, _modele = _monte(racine, RELANCER, "Par « Reprendre ».")

        reponse = _repondre(repondeur, QUESTION_684)

        assert "Par « Reprendre »." in reponse
        assert "docs/00-runs.md" in reponse
        assert "Relancer un run" in reponse

    def test_une_section_que_le_modele_n_a_pas_recue_n_est_pas_citee(self, tmp_path: Path) -> None:
        """L'invariant, éprouvé par les deux façons de ne pas recevoir une section.

        `THEME` a été demandée mais écartée par le plafond ; `#Inventée` ne résout
        rien. Aucune des deux n'est entrée dans le prompt, donc aucune n'est citée —
        sans quoi la citation dirait avoir lu ce qui n'a jamais été ouvert.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(
            racine,
            f"docs/00-runs.md#Inventée\n{RELANCER}\n{THEME}",
            "Par « Reprendre ».",
            sections_max=1,
        )

        reponse = _repondre(repondeur, QUESTION_684)

        # Écartée, `THEME` est **nommée** au modèle (il doit savoir qu'elle existe)
        # mais son texte n'entre pas — c'est ce texte-là qui ferait une source.
        _choix, prompt_reponse = modele.prompts
        assert "Soleil/lune, barre supérieure." not in prompt_reponse
        assert "Le bouton « Reprendre » vit sur la carte du run." in prompt_reponse
        # Et ni l'écartée ni l'inconnue ne sont citées : la citation dirait avoir lu
        # ce qui n'a jamais été ouvert.
        sources = reponse.split("Sources lues :")[1]
        assert "Le thème" not in sources
        assert "Inventée" not in sources
        assert "Relancer un run" in sources

    def test_une_liste_sans_aucune_cle_vaut_un_aveu_d_ignorance(self, tmp_path: Path) -> None:
        """Le modèle a parlé, et ce qu'il dit est « rien ici ».

        Lui redemander à vide coûterait un appel pour lui faire répéter — d'où
        l'unique appel, et une phrase à nous, sans source puisqu'il n'y a rien eu à
        lire. `ModeleScripte` rougirait de lui-même si un second appel partait.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, "Rien dans la documentation.")

        reponse = _repondre(repondeur, "Quelle est la couleur du ciel ?")

        assert len(modele.prompts) == 1
        assert "Je n'ai rien trouvé dans la documentation" in reponse
        assert "Sources lues" not in reponse

    def test_un_rendu_vide_est_une_indisponibilite_et_non_une_liste_vide(
        self, tmp_path: Path
    ) -> None:
        """Les deux se ressemblent — rien à sélectionner — et se traitent à l'opposé.

        Un modèle qui **dit** ne rien trouver a jugé : on rend son aveu. Un modèle
        qui ne dit **rien** n'a pas jugé : on replie, en nommant la panne. Les
        confondre ferait passer une panne pour un verdict, c'est-à-dire annoncer que
        la documentation ne couvre pas un sujet qu'on n'a jamais consulté.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, "   \n")

        reponse = _repondre(repondeur, QUESTION_684)

        assert len(modele.prompts) == 1
        assert "Je n'ai rien trouvé dans la documentation" not in reponse
        assert "réponse vide" in reponse
        assert repondre_assistance(QUESTION_684) in reponse


# ── ⑤ le repli : sans fournisseur, le canal répond quand même et le dit ──────
#
# Critère 3. Trois façons d'être privé de modèle, plus le corpus illisible, et
# aucune ne rend un 502 : la phrase dit la cause **et** ce qui n'a pas eu lieu,
# puis le déterministe de #123 répond. Les deux, jamais l'une à la place de
# l'autre.


class TestRepli:
    @pytest.mark.parametrize(
        ("modele", "cause"),
        [
            pytest.param(ModeleEnPanne(), "fournisseur indisponible", id="en-panne"),
            pytest.param(ModeleMuet(), "réponse vide", id="muet"),
        ],
    )
    def test_un_modele_injoignable_se_dit_et_laisse_repondre_le_deterministe(
        self, tmp_path: Path, modele: ModelProvider, cause: str
    ) -> None:
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur = RepondeurAssistanceDocumentee(provider=modele, racine=racine)

        reponse = _repondre(repondeur, QUESTION_684)

        assert cause in reponse
        assert "Je ne peux pas consulter la documentation" in reponse
        # Ce qui n'a pas eu lieu, dit : sans quoi la réponse de table passerait pour
        # une réponse documentée.
        assert "sans source" in reponse
        # Et la réponse de l'aide intégrée est là — le canal n'est pas muet (#65).
        assert repondre_assistance(QUESTION_684) in reponse
        assert "Sources lues" not in reponse

    def test_un_fournisseur_absent_est_un_reglage_et_non_une_panne_passagere(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Les deux familles ne se réparent pas pareil, et la **structure** les sépare.

        Ce qui casse en *résolvant* le fournisseur n'a touché aucun réseau : réessayer
        n'y changerait rien. Aucune chaîne n'est lue pour trancher — c'est l'endroit
        de l'échec qui classe (règle de `controltower.causes`).
        """

        def sans_fournisseur() -> ModelProvider:
            raise KeyError("MAESTRO_PROVIDER='inconnu' inconnu.")

        monkeypatch.setattr("maestro.providers.factory.provider_from_settings", sans_fournisseur)
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur = RepondeurAssistanceDocumentee(racine=racine)

        reponse = _repondre(repondeur, QUESTION_684)

        assert "réglage absent" in reponse
        assert "MAESTRO_PROVIDER" in reponse
        # La cause est déballée du `repr` que `KeyError.__str__` ajoute.
        assert "\"MAESTRO_PROVIDER='inconnu' inconnu.\"" not in reponse
        assert repondre_assistance(QUESTION_684) in reponse

    def test_sans_fournisseur_le_corpus_n_est_meme_pas_lu(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """L'ordre porte une décision : on va replier, autant ne pas payer 1,58 Mio.

        La démo (#65) tourne sans fournisseur — analyser le corpus à chaque question
        pour s'en apercevoir ensuite lui ferait payer ce qu'elle n'utilise pas.
        """
        lectures: list[Path | str | None] = []

        def carte_espionne(racine=None, **kwargs):
            lectures.append(racine)
            raise AssertionError("le corpus ne devrait pas être lu")

        monkeypatch.setattr(
            "maestro.providers.factory.provider_from_settings",
            lambda: (_ for _ in ()).throw(KeyError("aucun fournisseur")),
        )
        monkeypatch.setattr(
            "maestro.controltower.assistance_documentee.carte_documentation",
            carte_espionne,
        )
        repondeur = RepondeurAssistanceDocumentee(racine=tmp_path)

        reponse = _repondre(repondeur, QUESTION_684)

        assert lectures == []
        assert "réglage absent" in reponse

    def test_un_corpus_trop_grand_replie_sans_rien_demander_a_l_utilisateur(
        self, tmp_path: Path
    ) -> None:
        """Ni un réglage de l'utilisateur ni une panne du fournisseur : un défaut du
        produit — on ne lui demande donc pas de le réparer."""
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur = RepondeurAssistanceDocumentee(
            provider=ModeleScripte(RELANCER, "Voilà."), racine=racine
        )
        # Le budget de la carte est celui du module ; on le rend intenable.
        object.__setattr__(repondeur, "_racine", racine)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("maestro.controltower.documentation.BUDGET_CARTE_TOKENS", 1)
            patch.setattr(
                "maestro.controltower.assistance_documentee.carte_documentation",
                lambda racine=None: construire_carte(racine, budget_tokens=1),
            )
            reponse = _repondre(repondeur, QUESTION_684)

        assert "dépasse la taille lisible" in reponse
        assert "rien que vous puissiez corriger" in reponse
        assert repondre_assistance(QUESTION_684) in reponse

    def test_le_repli_injecte_est_celui_qui_repond(self, tmp_path: Path) -> None:
        """Le repli est un `RepondeurChat` comme un autre — la démo en met un scripté."""

        class ReplieurEspion(RepondeurChat):
            def __init__(self) -> None:
                self.appels = 0

            async def repondre(self, agent, fil) -> str:
                self.appels += 1
                return "réponse de repli"

        espion = ReplieurEspion()
        repondeur = RepondeurAssistanceDocumentee(
            provider=ModeleEnPanne(), racine=ecrire_corpus(tmp_path, CORPUS), repli=espion
        )

        reponse = _repondre(repondeur, QUESTION_684)

        assert espion.appels == 1
        assert "réponse de repli" in reponse

    def test_le_repli_par_defaut_est_le_deterministe_de_123(self) -> None:
        """`create_app` ne le passe pas par hasard : c'est le défaut du répondeur."""
        assert isinstance(RepondeurAssistanceDocumentee()._repli, RepondeurAssistance)


# ── ⑥ le critère 1, sur le corpus réel ───────────────────────────────────────
#
# Les deux seuls tests de la suite qui lisent la documentation du dépôt, parce
# qu'ils portent précisément sur elle : « à partir du contenu de `docs/` et
# `apps/web/README.md` ». Ils ne fixent aucun titre par cœur — un identifiant
# recopié ici mourrait au premier ticket qui retouche la doc —, ils prennent une
# section **réelle** telle que la carte la rend aujourd'hui.


@pytest.fixture(scope="module")
def corpus_reel() -> CarteDocumentation:
    """La carte du corpus du dépôt, construite une fois pour toute la suite."""
    return construire_carte(RACINE)


def test_une_question_hors_table_est_servie_par_le_contenu_du_corpus_reel(
    corpus_reel: CarteDocumentation,
) -> None:
    """Critère 1 : la réponse vient de la documentation, pas de la table de #684.

    La section est choisie **dans la carte réelle** au moment du test : ce qui est
    vérifié est que le texte de cette section-là — les octets du fichier — atteint le
    modèle, et que la citation la nomme.
    """
    section = corpus_reel.sections_du_fichier("apps/web/README.md")[0]
    repondeur = RepondeurAssistanceDocumentee(
        provider=ModeleScripte(section.identifiant, "Voici ce que dit la doc."),
        racine=RACINE,
    )

    reponse = _repondre(repondeur, QUESTION_684)

    assert "Voici ce que dit la doc." in reponse
    assert section.chemin in reponse
    # La réponse ne vient pas de la table : celle-ci répondrait tout autre chose.
    assert repondre_assistance(QUESTION_684) not in reponse


def test_la_carte_du_corpus_reel_tient_dans_le_premier_prompt(
    corpus_reel: CarteDocumentation,
) -> None:
    """Ce qui rend le dispositif possible, re-dit ici : la carte est petite.

    Sans cette propriété, le premier appel coûterait le corpus entier — 561 838
    tokens estimés au 2026-08-28 — et le chantier n'aurait pas de forme.
    """
    assert corpus_reel.tokens < BUDGET_SECTIONS_TOKENS
    assert "apps/web/README.md" in corpus_reel.markdown


# ── ⑦ le câblage : c'est ce répondeur que `create_app` sert ──────────────────


#: ⚠ Le fournisseur du **poste** est neutralisé dans toute cette section, et ce n'est
#: pas un détail de fixture. `create_app` construit ses répondeurs sans provider : ils
#: résolvent `provider_from_settings()` au premier message, donc sur une machine
#: configurée — celle d'un développeur, où l'accès modèle passe par l'abonnement — un
#: test d'endpoint **appelle réellement le modèle**. Il devient alors lent, payant, et
#: son verdict dépend de ce que le modèle a répondu ce jour-là. C'est ce test-ci qui l'a
#: mesuré (43 s contre 1,4 s), et depuis #782 `tests/conftest.py` tient la règle pour
#: toute la suite : une résolution du poste lève `FournisseurDuPosteRefuse` et rougit
#: le test à sa sortie. Ici, elle se neutralise quand même explicitement, ce qui est de
#: toute façon la bonne forme : le fournisseur *est* le sujet de ces deux tests, et
#: c'est le **repli** qu'ils exercent — la garde, elle, refuserait ce chemin.
def _refuse_tout_fournisseur() -> ModelProvider:
    raise KeyError("aucun fournisseur configuré sur ce poste")


@pytest.fixture()
def app_sans_fournisseur(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """L'app servie **sans** injecter de répondeur d'assistance : celui du défaut.

    C'est tout l'objet du test — un point d'injection utilisé ici ne dirait rien du
    câblage, qui est ce que le ticket demande de vérifier.
    """
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", _refuse_tout_fournisseur
    )
    with TestClient(
        create_app(bus=InMemoryEventBus(), chat_store=ChatStore(tmp_path / "chat"))
    ) as client:
        yield client


def test_le_fil_d_assistance_est_servi_par_le_repondeur_documente(
    app_sans_fournisseur,
) -> None:
    """Critère 3 de bout en bout : sans fournisseur, 201 et non 502.

    C'est exactement la démo #65 — aucun fournisseur, aucune authentification — et le
    canal doit répondre. La réponse porte les deux moitiés : l'empêchement, puis
    l'aide intégrée. La première phrase n'est produite que par le répondeur documenté :
    c'est elle qui prouve que `create_app` sert bien celui-là.
    """
    poste = app_sans_fournisseur.post(
        f"/api/chat/{NOM_ASSISTANCE}/messages", json={"contenu": QUESTION_684}
    )

    assert poste.status_code == 201
    _envoye, repondu = poste.json()["messages"]
    reponse = repondu["contenu"]
    assert "Je ne peux pas consulter la documentation" in reponse
    assert "réglage absent" in reponse
    assert repondre_assistance(QUESTION_684) in reponse


def test_le_fil_garde_la_question_et_la_reponse(app_sans_fournisseur) -> None:
    """La demande est acquise avant le répondeur : un empêchement ne la perd pas."""
    app_sans_fournisseur.post(
        f"/api/chat/{NOM_ASSISTANCE}/messages", json={"contenu": QUESTION_684}
    )

    messages = app_sans_fournisseur.get(f"/api/chat/{NOM_ASSISTANCE}").json()["messages"]

    assert [message["auteur"] for message in messages] == [
        UTILISATEUR,
        NOM_ASSISTANCE,
    ]
    assert messages[0]["contenu"] == QUESTION_684


def test_le_cablage_va_jusqu_aux_sources_quand_un_modele_repond(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, corpus_reel: CarteDocumentation
) -> None:
    """Le tour complet par la route, sur le corpus du dépôt et sans injection.

    Le test précédent prouve le câblage par le chemin du repli ; celui-ci le prouve
    par le chemin nominal — deux appels, une réponse, et la citation de la section
    réellement passée. Le modèle est factice mais il est résolu **comme en
    production**, par la factory : c'est ce qui distingue ce test d'un test de
    répondeur.
    """
    section = corpus_reel.sections_du_fichier("apps/web/README.md")[0]
    modele = ModeleScripte(section.identifiant, "D'après la documentation : oui.")
    monkeypatch.setattr("maestro.providers.factory.provider_from_settings", lambda: modele)
    with TestClient(
        create_app(bus=InMemoryEventBus(), chat_store=ChatStore(tmp_path / "chat"))
    ) as client:
        poste = client.post(f"/api/chat/{NOM_ASSISTANCE}/messages", json={"contenu": QUESTION_684})

    assert poste.status_code == 201
    reponse = poste.json()["messages"][1]["contenu"]
    assert "D'après la documentation : oui." in reponse
    assert section.chemin in reponse
    assert len(modele.prompts) == 2
