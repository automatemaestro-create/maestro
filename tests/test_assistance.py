"""L'assistance après le retrait de la table de mots-clés (#765, lot 3 de #748).

Cette suite est celle de #684 **reprise**, et le déplacement dit tout le chantier :
ce qui gardait un **juge** garde désormais un **repli**. La table de #123 n'a pas
disparu — elle répond encore quand aucun fournisseur n'est joignable (la démo #65,
un poste non configuré) — mais plus rien ne la consulte pour décider du sujet d'une
question. C'est le modèle qui lit la documentation du produit (#763/#764) et répond
à partir d'elle.

Quatre moitiés, dans l'ordre où elles se lisent :

① **la table ne juge plus** — les témoins de #684, c'est-à-dire les questions
  réelles auxquelles elle savait répondre, sont rejoués contre le répondeur
  documenté : chacune **atteint le modèle**, et aucune ne reçoit plus la réponse de
  la table. C'est la moitié comportementale du critère 1, celle qu'aucune garde
  structurelle ne peut rendre ;
② **le banc hors périmètre** — un échantillon de questions auxquelles la
  documentation ne répond pas, et le verdict qui dit si la réponse est un aveu. Le
  verdict **prouve son motif** avant de servir : quatre réponses fabriquées, dont
  une qui prend soin de dire « je ne suis pas certain » avant de répondre quand
  même, y sont détectées ;
③ **la garde structurelle** — aucun module de production hors `assistance.py` ne
  nomme le juge. Cherchée dans l'**arbre syntaxique** et jamais par un `grep` : ce
  module *doit* citer `SUJETS_ASSISTANCE` pour raconter son retrait, et une garde
  textuelle se déclencherait sur la docstring même qui le documente (construction
  de #688 sur le fil global) ;
④ **le repli, et ce qu'il garde encore** — l'ancienne suite, recadrée. Le piège de
  la sous-chaîne, les frontières de mot, les invariants de la table : ils gardent la
  qualité de ce qui répond quand personne d'autre ne peut, ce qui n'est pas une
  raison de le laisser se dégrader.

⚠ **Ce que cette suite ne tient pas, et l'assume.** Le modèle y est un double
scripté : que le vrai modèle s'abstienne effectivement sur les questions du banc
relève du **prompt** (`_PROMPT_ASSISTANCE`, dernier paragraphe, écrit au lot 2) et
se mesure en usage. Ce qui est tenu ici est ce qui l'entoure, et qui suffit à ce que
l'aveu soit possible : la question **atteint** le modèle sans que rien ne tranche
avant lui, il ne reçoit que les sections qu'il a demandées, et quand il s'abstient
son aveu arrive **intact** à l'utilisateur — jamais rattrapé par une réponse de
table qui se ferait passer pour documentée. C'est la moitié qu'on nomme plutôt que
de la masquer (même partage qu'à #688).
"""

from __future__ import annotations

import ast
import asyncio
import re
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from maestro.controltower.assistance import (
    _FLEXIONS,
    _ORIENTATION,
    AGENT_ASSISTANCE,
    NOM_ASSISTANCE,
    SUJETS_ASSISTANCE,
    RepondeurAssistance,
    _mot_present,
    repondre_assistance,
    sujet_assistance,
)
from maestro.controltower.assistance_documentee import (
    _AVEU_IGNORANCE,
    RepondeurAssistanceDocumentee,
)
from maestro.controltower.chat import MessageChat, normaliser
from maestro.controltower.documentation import oublier_carte
from maestro.providers.base import ModelProvider

#: La racine du dépôt — le Python que la garde structurelle balaie.
RACINE_DEPOT = Path(__file__).resolve().parents[1]

#: Le fichier où l'interface déclare les questions qu'elle **propose** d'elle-même
#: sur un fil vide (`AMORCES_ASSISTANCE`). Elles sont lues là plutôt que recopiées
#: ici : recopiées, elles dériveraient, et le test garderait une liste que
#: personne ne propose plus.
AMORCES_TS = RACINE_DEPOT / "apps" / "web" / "lib" / "assistance.ts"


def amorces_de_l_interface() -> list[str]:
    """Les amorces déclarées côté UI, extraites de leur littéral TypeScript."""
    source = AMORCES_TS.read_text(encoding="utf-8")
    bloc = re.search(r"AMORCES_ASSISTANCE:\s*string\[\]\s*=\s*\[(.*?)\];", source, re.DOTALL)
    assert bloc is not None, f"AMORCES_ASSISTANCE introuvable dans {AMORCES_TS}"
    amorces = re.findall(r'"([^"]+)"', bloc.group(1))
    # L'extraction doit être **complète**, pas seulement non vide : une amorce
    # écrite en apostrophes ou sur plusieurs lignes passerait sous le motif, et
    # le test rendrait un ✓ sur une question qu'il n'a jamais posée.
    assert bloc.group(1).count('"') == 2 * len(amorces), (
        f"{AMORCES_TS} porte des amorces que le motif ne capte pas"
    )
    return amorces


def sujet_nomme(identifiant: str):
    """Le sujet `identifiant` — les tests visent le **sujet**, jamais le texte.

    Une réponse se réécrit (elles l'ont toutes été en #684, l'interface ayant bougé
    sous la table depuis #123) ; un identifiant est le contrat.
    """
    for sujet in SUJETS_ASSISTANCE:
        if sujet.identifiant == identifiant:
            return sujet
    raise AssertionError(f"sujet inconnu : {identifiant}")


# --------------------------------------------------------------------------- #
# Le harnais du répondeur documenté                                            #
# --------------------------------------------------------------------------- #
#
# Redéfini ici plutôt qu'importé de `test_assistance_documentee` : c'est de la
# plomberie de fixture (deux écritures et un double), et faire dépendre une suite
# d'une autre créerait un lien que rien ne déclare — la règle que le lot 2 s'était
# déjà donnée en ne l'important pas de `test_documentation`. Ce qui ne doit exister
# qu'une fois est la **règle**, et elle est dans le module.


@pytest.fixture(autouse=True)
def _cache_neuf() -> Iterator[None]:
    """Aucun test ne part du cache d'un autre — ni ne le laisse derrière lui."""
    oublier_carte()
    yield
    oublier_carte()


def ecrire_corpus(racine: Path, fichiers: Mapping[str, str]) -> Path:
    """Monte un corpus jetable — mêmes emplacements que le vrai, contenu au choix."""
    for relatif, contenu in fichiers.items():
        chemin = racine / relatif
        chemin.parent.mkdir(parents=True, exist_ok=True)
        chemin.write_text(contenu, encoding="utf-8")
    return racine


#: Un corpus minuscule dont on connaît les identifiants par cœur. Il ne porte
#: **rien** sur les sujets du banc (Jenkins, nginx, Python…) : c'est ce qui fait de
#: ces questions-là des questions hors périmètre, ici comme sur le corpus réel.
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

#: Ce qu'un modèle rend quand la carte ne porte rien qui réponde — le contrat de
#: sortie du premier appel dit « rends une liste vide ». Aucun mot n'est reconnu :
#: cette phrase ne porte pas de `#`, donc elle ne désigne aucune section.
AUCUNE_SECTION = "Rien dans la documentation ne répond à cette question."


class ModeleScripte(ModelProvider):
    """Un fournisseur qui rend ses réponses **dans l'ordre**, et note ce qu'on lui donne.

    Une réponse de trop demandée est une erreur franche : un test qui attend un seul
    appel et en obtient deux doit rougir là, pas trois assertions plus loin. C'est ce
    qui rend gratuite, tout au long du banc, l'assertion « le second appel n'a pas
    eu lieu ».
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


def _fil(*contenus: str) -> list[MessageChat]:
    """Un fil dont les messages alternent utilisateur / assistant, l'utilisateur d'abord."""
    return [
        MessageChat(
            agent=NOM_ASSISTANCE,
            auteur="utilisateur" if rang % 2 == 0 else NOM_ASSISTANCE,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


def _monte(racine: Path, *reponses: str) -> tuple[RepondeurAssistanceDocumentee, ModeleScripte]:
    """Le répondeur documenté et son modèle scripté — les tests ont besoin des deux."""
    modele = ModeleScripte(*reponses)
    return RepondeurAssistanceDocumentee(provider=modele, racine=racine), modele


def _repondre(repondeur, *contenus: str) -> str:
    """La réponse du répondeur au fil donné — le raccourci de toute la suite."""
    return asyncio.run(repondeur.repondre(AGENT_ASSISTANCE, _fil(*contenus)))


# --------------------------------------------------------------------------- #
# ① La table ne juge plus : les témoins de #684 atteignent le modèle           #
# --------------------------------------------------------------------------- #
#
# La moitié **comportementale** du critère 1. Les questions ci-dessous ne sont pas
# des exemples choisis pour ce lot : ce sont les témoins de #684, c'est-à-dire
# exactement ce que la table savait servir — et qu'elle servait donc **sans appeler
# personne**. Les rejouer contre le répondeur documenté est la seule façon de dire
# que la voie rapide a disparu : un test qui n'observerait que la réponse finale
# passerait sur un canal qui aurait gardé la table en tête de chemin.

#: Ce à quoi la table répondait juste (#684). Le sujet est conservé en regard : il
#: sert au ④, où ces questions gardent le repli.
TEMOINS = (
    ("Où sont les coûts ?", "couts"),
    ("Comment j'approuve une validation ?", "validations"),
    ("C'est quoi Maestro ?", "maestro"),
    ("Comment passer en thème sombre ?", "theme"),
    ("Comment revoir la visite guidée ?", "guide"),
    ("Comment désactiver un agent ?", "agents"),
    ("Où règle-t-on l'URL du backend ?", "parametres"),
    ("À quoi sert la cloche ?", "notifications"),
    ("Pourquoi ça affiche Reconnexion… ?", "temps-reel"),
    ("Combien ça coûte ?", "couts"),
    ("Les validations en attente ?", "validations"),
    ("Mes dépenses du mois ?", "couts"),
)

#: Ce que #684 avait dû **ajouter** à la table pour le couvrir — la moitié
#: « compléter la table » de son option (a). C'est la limite que #748 lève : ces
#: sujets-là ne s'ajoutent plus à une table, ils se lisent dans la documentation.
NOUVEAUX_SUJETS = (
    ("Où est le bouton pour relancer un run bloqué ?", "runs"),
    ("Comment mettre un run en pause ?", "runs"),
    ("Où voir le journal d'activité ?", "journal"),
    ("Comment ajouter un serveur MCP ?", "integrations"),
    ("Comment changer de projet ?", "projets"),
    ("À quoi sert le cadrage ?", "cadrage"),
    ("Comment lancer du travail ?", "chat"),
    ("Comment je lance un nouveau travail ?", "chat"),
    ("Où je dépose un fichier pour que l'agent le lise ?", "chat"),
    # L'ex æquo « approuver », tranché par l'ordre : les deux décisions portent
    # les mêmes mots mais pas le même écran — le brief se tranche dans le fil,
    # l'action sensible sur la page Validations.
    ("Comment approuver le brief ?", "cadrage"),
    ("Comment approuver une validation ?", "validations"),
    ("Comment publier une nouvelle version de playbook ?", "playbooks"),
    ("Que montre le tableau de bord ?", "taches"),
    # L'ex æquo « agent », tranché par l'ordre de la table : une intention
    # conversationnelle va au fil, l'agent comme objet de réglage va à sa page.
    ("Comment parler à un agent ?", "chat"),
    ("Comment plafonner les instances d'un agent ?", "agents"),
)

QUESTIONS_REELLES = TEMOINS + NOUVEAUX_SUJETS


class TestLaTableNeJugePlus:
    """Ce que la table savait servir passe désormais par le modèle, sans exception."""

    @pytest.mark.parametrize(("question", "sujet"), QUESTIONS_REELLES)
    def test_une_question_que_la_table_sert_atteint_quand_meme_le_modele(
        self, tmp_path: Path, question: str, sujet: str
    ) -> None:
        """Aucune voie rapide : le modèle est appelé, sur la carte, avec la question.

        La première assertion tient l'**échantillon** — si la table cessait de
        répondre à cette question, le cas ne prouverait plus rien, et le vert serait
        celui d'une question jamais posée.
        """
        assert sujet_assistance(question) is not None, (
            f"« {question} » ne trouve plus aucun sujet : le témoin de #684 a fondu"
        )
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, AUCUNE_SECTION)

        _repondre(repondeur, question)

        assert len(modele.prompts) == 1, "le modèle n'a pas été consulté"
        assert question in modele.prompts[0]
        assert "Carte de la documentation" in modele.prompts[0]

    @pytest.mark.parametrize(("question", "sujet"), QUESTIONS_REELLES)
    def test_la_reponse_de_la_table_n_est_plus_celle_de_l_utilisateur(
        self, tmp_path: Path, question: str, sujet: str
    ) -> None:
        """Le modèle a répondu : c'est **sa** réponse qui sort, pas celle du sujet.

        Le pendant du test au-dessus, côté sortie. Les deux sont nécessaires : le
        premier dit que le modèle a été appelé, celui-ci que sa réponse n'est pas
        ensuite écrasée ou complétée par la table.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, _modele = _monte(racine, RELANCER, "D'après la documentation : par ici.")

        reponse = _repondre(repondeur, question)

        assert "D'après la documentation : par ici." in reponse
        assert sujet_nomme(sujet).reponse not in reponse
        assert _ORIENTATION not in reponse


# --------------------------------------------------------------------------- #
# ② Le banc hors périmètre : l'aveu d'ignorance, et son verdict                #
# --------------------------------------------------------------------------- #

#: Des questions étrangères au produit, mais **écrites dans son vocabulaire** :
#: chacune porte un mot-clé de `SUJETS_ASSISTANCE`, et recevait donc une réponse
#: d'écran assurée — la page Runs pour Jenkins, la page Coûts pour le Bitcoin.
#: C'est la famille qui compte : le hors-sujet franc, la table le laissait déjà
#: passer à l'orientation, tandis que celles-ci sont **exactement** ce qu'elle
#: fabriquait. Le sujet servi est noté en regard, et `test_la_table_y_repondait`
#: tient l'échantillon.
#:
#: Mesure du 2026-08-29 sur `main` : sept questions candidates sur douze recevaient
#: une réponse d'écran ; ce sont les sept.
HORS_PERIMETRE_TROMPEUR = (
    ("Comment créer une pipeline Jenkins ?", "runs"),
    ("Quelle version de Python faut-il pour ce dépôt ?", "playbooks"),
    ("Combien coûte un abonnement GitHub Copilot ?", "couts"),
    ("Comment configurer un serveur nginx en reverse proxy ?", "integrations"),
    ("Où sont les paramètres de mon éditeur VS Code ?", "parametres"),
    ("Comment mettre un thème sombre dans Slack ?", "theme"),
    ("Quel est le prix du Bitcoin aujourd'hui ?", "couts"),
)

#: Le hors-sujet franc — rien dans la question ne ressemble au produit. La table y
#: répondait déjà honnêtement (`_ORIENTATION`) : ces cas ne mesurent pas un défaut
#: corrigé, ils tiennent l'autre bord du banc, où le nouveau répondeur ne doit pas
#: faire **moins** bien que l'ancien.
HORS_PERIMETRE_FRANC = (
    "Quelle est la capitale de l'Australie ?",
    "Écris-moi un poème sur la mer",
    "Quel temps fera-t-il demain à Lyon ?",
    "Comment je fais un git rebase interactif ?",
    "Peux-tu relire mon CV ?",
)

HORS_PERIMETRE = tuple(question for question, _sujet in HORS_PERIMETRE_TROMPEUR) + (
    HORS_PERIMETRE_FRANC
)


def est_un_aveu(reponse: str) -> bool:
    """La réponse est-elle l'aveu d'ignorance du produit, et **rien d'autre** ?

    Une **égalité** à la phrase que le produit rend quand il s'abstient, jamais une
    recherche de mots dans le texte. Ce n'est pas une précaution de style : juger un
    texte à son vocabulaire est exactement ce que ce chantier retire du produit, et
    un banc qui le ferait validerait une réponse fabriquée ayant pris soin de dire
    « je ne suis pas certain » avant de répondre quand même — le cas
    `poli-mais-fabrique` ci-dessous, qui est là pour le prouver.

    L'égalité porte aussi tout ce que le banc n'aurait pas à vérifier autrement :
    une réponse qui serait l'aveu **suivi** d'un bloc de sources, ou l'aveu **suivi**
    d'une réponse de table, n'est pas l'aveu du produit et ne passe pas.
    """
    return reponse.strip() == _AVEU_IGNORANCE


#: L'échantillon fautif du **verdict** : quatre réponses qu'un banc mal écrit
#: laisserait passer pour un aveu. Sans cette moitié, `est_un_aveu` pourrait ne rien
#: chercher du tout et rendre un ✓ vert sur tout le banc.
REPONSES_FABRIQUEES = (
    pytest.param(
        "Le bouton « Relancer » se trouve en haut à droite de la page Runs.",
        id="ecran-invente",
    ),
    pytest.param(
        "Je ne sais pas vraiment, mais essayez le bouton « Relancer » en haut à droite.",
        id="poli-mais-fabrique",
    ),
    pytest.param(
        repondre_assistance("Comment créer une pipeline Jenkins ?"),
        id="reponse-de-table",
    ),
    pytest.param(
        f"{_AVEU_IGNORANCE}\n\nSources lues :\n- docs/00-runs.md › Relancer un run",
        id="aveu-mais-sources",
    ),
)


class TestVerdictDuBanc:
    """Le verdict prouve son motif avant de servir — sinon le banc ne prouve rien."""

    def test_le_verdict_reconnait_l_aveu_du_produit(self) -> None:
        """La moitié positive : ce que le produit rend quand il s'abstient passe."""
        assert est_un_aveu(_AVEU_IGNORANCE)
        assert est_un_aveu(f"  {_AVEU_IGNORANCE}  ")

    @pytest.mark.parametrize("fabriquee", REPONSES_FABRIQUEES)
    def test_le_verdict_detecte_une_reponse_fabriquee(self, fabriquee: str) -> None:
        """La moitié qui donne son sens au banc : une réponse plausible est refusée.

        `poli-mais-fabrique` est celui qui compte : il dit « je ne sais pas » **et**
        invente un bouton. Un verdict écrit en cherchant des mots d'excuse
        l'accepterait, et le banc entier deviendrait un ✓ sur une question jamais
        posée.
        """
        assert not est_un_aveu(fabriquee)

    def test_l_aveu_du_produit_ne_renvoie_pas_vers_la_table(self) -> None:
        """Il oriente vers la page Chat, jamais vers un écran qu'il n'a pas lu.

        Ce qui sépare l'aveu de l'orientation de #123 : celle-ci **énumère les
        sujets couverts**, c'est-à-dire la table elle-même. L'aveu documenté ne
        promet rien de tel — il dit ce qu'il a cherché et où.
        """
        assert "documentation de Maestro" in _AVEU_IGNORANCE
        assert _ORIENTATION not in _AVEU_IGNORANCE


class TestBancHorsPerimetre:
    """Une question sans réponse dans la documentation reçoit un aveu, jamais une réponse."""

    @pytest.mark.parametrize(("question", "sujet"), HORS_PERIMETRE_TROMPEUR)
    def test_la_table_y_repondait_avec_aplomb(self, question: str, sujet: str) -> None:
        """L'échantillon, tenu : ces questions sont bien celles que la table servait.

        Ce test ne juge pas le chemin nominal — il tient la **mesure** qui donne son
        sens au banc. S'il rougit, c'est que la question a été adoucie ou le mot-clé
        retiré, et les cas d'à côté ne prouvent plus qu'un défaut a été corrigé.
        """
        trouve = sujet_assistance(question)
        assert trouve is not None, f"« {question} » ne piège plus la table"
        assert trouve.identifiant == sujet
        assert repondre_assistance(question) == sujet_nomme(sujet).reponse

    @pytest.mark.parametrize("question", HORS_PERIMETRE)
    def test_la_question_hors_perimetre_atteint_le_modele(
        self, tmp_path: Path, question: str
    ) -> None:
        """Rien ne tranche avant lui — pas même pour une question manifestement à côté.

        C'est la propriété qui rend l'aveu possible : un canal qui écarterait
        lui-même les questions hors périmètre déciderait du sujet par du code, ce
        que le chantier supprime.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, AUCUNE_SECTION)

        _repondre(repondeur, question)

        assert len(modele.prompts) == 1
        assert question in modele.prompts[0]

    @pytest.mark.parametrize("question", HORS_PERIMETRE)
    def test_un_modele_qui_s_abstient_rend_l_aveu_et_rien_d_autre(
        self, tmp_path: Path, question: str
    ) -> None:
        """Le cœur du banc : ce que l'utilisateur lit est l'aveu, seul.

        Trois façons de le trahir sont fermées d'un coup par l'égalité du verdict :
        y ajouter la réponse de la table, y ajouter des sources qu'on n'a pas lues,
        ou remplacer l'aveu par l'une ou l'autre.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, AUCUNE_SECTION)

        reponse = _repondre(repondeur, question)

        assert est_un_aveu(reponse), f"réponse fabriquée sur « {question} » : {reponse!r}"
        # Le second appel n'a pas eu lieu : rien n'a été lu, donc il n'y a rien à
        # dire de plus. `ModeleScripte` aurait rougi de lui-même.
        assert len(modele.prompts) == 1
        assert repondre_assistance(question) not in reponse

    @pytest.mark.parametrize("question", HORS_PERIMETRE)
    def test_des_sections_inventees_ne_donnent_pas_une_reponse_quand_meme(
        self, tmp_path: Path, question: str
    ) -> None:
        """La façon la plus plausible dont un modèle fabrique : nommer ce qui n'existe pas.

        Aucune de ces clés ne résout, donc rien n'entre dans le second prompt, donc
        il n'a pas lieu. Une réponse **serait** fabriquée ici, au sens strict : elle
        porterait sur des sections que personne n'a ouvertes.
        """
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(
            racine, "docs/00-runs.md#Jenkins et nginx\ndocs/99-inexistant.md#Tout savoir"
        )

        reponse = _repondre(repondeur, question)

        assert len(modele.prompts) == 1
        assert est_un_aveu(reponse)

    def test_le_banc_tient_aussi_sur_la_documentation_reelle(self) -> None:
        """« Hors périmètre » doit l'être du **produit**, pas du corpus de test.

        Tout le banc ci-dessus monte un corpus jetable de deux fichiers, où
        n'importe quelle question serait hors périmètre : l'échantillon n'y prouve
        que la plomberie. Rejoué une fois sur `docs/` et `apps/web/README.md` — la
        documentation que le produit sert vraiment —, il dit que ces questions-là
        sont sans réponse **dans Maestro**, et que le canal le reconnaît à cette
        échelle comme à l'autre.

        Une seule construction de carte pour les douze : le cache de
        `carte_documentation` n'est vidé qu'aux bornes du test, et c'est ce qui rend
        ce témoin gratuit plutôt que douze fois payé.
        """
        for question in HORS_PERIMETRE:
            modele = ModeleScripte(AUCUNE_SECTION)
            repondeur = RepondeurAssistanceDocumentee(provider=modele, racine=RACINE_DEPOT)

            reponse = _repondre(repondeur, question)

            assert len(modele.prompts) == 1, f"« {question} » n'a pas atteint le modèle"
            assert question in modele.prompts[0]
            # La carte passée est bien celle du dépôt, et pas une carte vide : sur
            # un `docs/` introuvable, tout serait hors périmètre et le test rendrait
            # un ✓ sur une question jamais posée.
            assert "apps/web/README.md" in modele.prompts[0]
            assert "docs/05-interface-control-tower.md" in modele.prompts[0]
            assert est_un_aveu(reponse), f"réponse fabriquée sur « {question} » : {reponse!r}"

    def test_l_aveu_du_modele_arrive_intact_a_l_utilisateur(self, tmp_path: Path) -> None:
        """L'autre forme de l'aveu : le modèle a lu, et dit que ça ne répond pas.

        Elle est plus exposée que la première, parce qu'ici il y a **une réponse** —
        et donc la tentation de la compléter. Le canal n'en fait rien : la phrase du
        modèle sort telle quelle, avec les sections qu'il a réellement lues. « Voici
        ce que j'ai lu, et ça ne répond pas » est une réponse honnête et contrôlable.
        """
        question = "Comment créer une pipeline Jenkins ?"
        aveu_du_modele = "Les extraits que j'ai lus ne parlent pas de Jenkins."
        racine = ecrire_corpus(tmp_path, CORPUS)
        repondeur, modele = _monte(racine, RELANCER, aveu_du_modele)

        reponse = _repondre(repondeur, question)

        assert aveu_du_modele in reponse
        assert len(modele.prompts) == 2
        # Ce qu'il a lu est cité — l'aveu reste vérifiable.
        assert "Sources lues" in reponse
        assert "Relancer un run" in reponse
        # Et la table ne vient pas « compléter » : ce serait rendre une réponse
        # d'écran sous un bloc de sources qui ne la porte pas.
        assert repondre_assistance(question) not in reponse
        assert _ORIENTATION not in reponse


# --------------------------------------------------------------------------- #
# ③ La garde structurelle : plus aucun chemin de production ne consulte la table #
# --------------------------------------------------------------------------- #
#
# La moitié que le comportement ne peut pas tenir : un juge lexical remis quelque
# part dans `maestro/` ne se verrait pas depuis les tests d'au-dessus tant qu'il ne
# servirait qu'un cas de bord. Cherchée dans l'arbre syntaxique, pour la raison de
# #688 : ce module *doit* nommer `SUJETS_ASSISTANCE` en prose pour raconter son
# retrait, et un `grep` le condamnerait sur la docstring même qui le documente.

#: Les noms par lesquels la table **juge**. `RepondeurAssistance` n'en est pas : il
#: est le **repli**, son existence est voulue, et `assistance_documentee`/`app` le
#: nomment à dessein. C'est toute la distinction que ce lot rend vérifiable — le
#: répondeur de secours est permis partout, le juge nulle part.
JUGE_LEXICAL = (
    "SUJETS_ASSISTANCE",
    "SujetAssistance",
    "sujet_assistance",
    "repondre_assistance",
    "_mot_present",
    "_FLEXIONS",
    "_ORIENTATION",
)

#: Le seul module de production qui a le droit de les écrire : leur maison, qui est
#: aussi celle du repli. Ailleurs dans `maestro/`, les nommer est le geste par
#: lequel la table reviendrait sur le chemin d'une question.
MAISON_DU_REPLI = "maestro/controltower/assistance.py"

#: ⚠ `score` n'est **pas** cherché, et l'omission est délibérée : c'est la méthode
#: de `SujetAssistance`, mais le mot est trop commun dans le dépôt pour désigner
#: quoi que ce soit (le routage en a un, les analytics aussi). Un motif qui parle
#: partout ne se lit plus — même arbitrage que `intention` à #688, cherché dans son
#: module et nulle part ailleurs.


def _identifiants_python(source: str) -> set[str]:
    """Les noms **effectivement écrits en code** dans `source` (jamais en prose).

    Un `grep` ne distingue pas un usage d'une mention. L'arbre syntaxique tranche :
    un nom cité dans une chaîne ou un commentaire n'y est pas un identifiant.

    Une différence assumée avec #688, et elle tient à ce que les deux chantiers ne
    retirent pas la même chose : là-bas le lexique avait **disparu**, donc il n'y
    avait rien à importer ; ici la table **existe encore** — c'est le repli — si
    bien que l'`import` est précisément le geste par lequel un autre module la
    remettrait sur son chemin. Les noms importés (`ast.alias`) comptent donc, au
    même titre que les noms lus.
    """
    arbre = ast.parse(source)
    noms: set[str] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Name):
            noms.add(noeud.id)
        elif isinstance(noeud, ast.Attribute):
            noms.add(noeud.attr)
        elif isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            noms.add(noeud.name)
        elif isinstance(noeud, ast.arg):
            noms.add(noeud.arg)
        elif isinstance(noeud, ast.alias):
            noms.add(noeud.asname or noeud.name.rsplit(".", 1)[-1])
    return noms


def test_le_motif_du_juge_reconnait_un_echantillon_fautif() -> None:
    """Avant de balayer : la sonde attrape-t-elle ce qu'elle prétend chercher ?

    Sans cette preuve, un motif mal écrit rendrait un ✓ vert sur tout `maestro/` en
    ne cherchant rien. L'échantillon porte les quatre formes par lesquelles la table
    reviendrait sur un chemin de production — un import, un appel, une lecture de la
    table, un accès par attribut — et, en regard, la **mention** en docstring, qui
    elle doit passer.
    """
    fautif = _identifiants_python(
        '"""Ce module raconte le retrait de `_ORIENTATION`, et ne l\'utilise pas."""\n'
        "from maestro.controltower.assistance import sujet_assistance\n"
        "from maestro.controltower import assistance\n"
        "def aide(question):\n"
        "    if sujet_assistance(question) is not None:\n"
        "        return assistance.repondre_assistance(question)\n"
        "    return SUJETS_ASSISTANCE[0]\n"
    )

    assert {"sujet_assistance", "repondre_assistance", "SUJETS_ASSISTANCE"} <= fautif
    # Et la moitié qui sépare l'usage de la mention : `_ORIENTATION` n'est ici que
    # cité dans la docstring, donc la sonde ne le voit pas.
    assert "_ORIENTATION" not in fautif


def test_la_maison_du_repli_porte_bien_le_juge() -> None:
    """Le pendant du test au-dessus, côté cible : il y a bien quelque chose à trouver.

    Sans lui, la garde d'à côté passerait tout aussi bien sur un dépôt d'où la table
    aurait entièrement disparu — ce qui n'est pas ce que le ticket demande. Le repli
    doit **rester**, et rester complet.
    """
    ecrits = _identifiants_python((RACINE_DEPOT / MAISON_DU_REPLI).read_text(encoding="utf-8"))

    assert set(JUGE_LEXICAL) <= ecrits, f"{MAISON_DU_REPLI} a perdu {set(JUGE_LEXICAL) - ecrits}"


def test_aucun_chemin_de_production_ne_consulte_la_table() -> None:
    """Critère 1 : hors de sa maison, plus rien dans `maestro/` ne nomme le juge.

    Le balayage porte sur la **production** et pas sur les tests : cette suite-ci
    consulte la table à chaque ligne du ④, et c'est son travail — garder le repli
    suppose de l'interroger. La différence avec #688, où les tests étaient balayés
    eux aussi, est la même que plus haut : là-bas le lexique n'existait plus, ici il
    a un usage légitime et un seul.
    """
    fautifs: list[str] = []
    balayes: list[str] = []
    for chemin in sorted(RACINE_DEPOT.glob("maestro/**/*.py")):
        relatif = chemin.relative_to(RACINE_DEPOT).as_posix()
        balayes.append(relatif)
        if relatif == MAISON_DU_REPLI:
            continue
        ecrits = _identifiants_python(chemin.read_text(encoding="utf-8"))
        for nom in sorted(set(JUGE_LEXICAL) & ecrits):
            fautifs.append(f"{relatif} : {nom}")

    # Le balayage a bien eu lieu, et il a vu les deux modules par lesquels la table
    # reviendrait le plus naturellement : celui qui la remplace, et celui qui câble.
    assert "maestro/controltower/assistance_documentee.py" in balayes
    assert "maestro/controltower/app.py" in balayes
    assert len(balayes) > 50
    assert fautifs == []


def test_le_paquet_n_offre_plus_la_table_a_ses_appelants() -> None:
    """La surface publique le dit aussi : le juge n'est plus un service du paquet.

    `dir()` plutôt que la source — c'est ce qu'un appelant peut atteindre, donc ce
    par quoi la table reviendrait sans qu'aucun import ne la nomme. Le **repli**,
    lui, reste exporté : `create_app` le passe à chaque démarrage, et c'est la seule
    moitié du déterministe qui soit encore un service.
    """
    from maestro import controltower

    surface = set(dir(controltower))

    assert surface & set(JUGE_LEXICAL) == set()
    assert {"RepondeurAssistance", "AGENT_ASSISTANCE", "NOM_ASSISTANCE"} <= surface


# --------------------------------------------------------------------------- #
# ④ Le repli, et ce qu'il garde encore                                         #
# --------------------------------------------------------------------------- #
#
# L'ancienne suite de #684, recadrée. Ce qui suit ne garde plus un juge : il garde
# ce qui répond quand aucun fournisseur n'est joignable — la démo #65, un poste non
# configuré. Le déclassement ne rend rien caduc, au contraire : le repli est
# consulté **quand personne ne regarde**, donc c'est le seul endroit du canal où une
# régression pourrait vivre longtemps sans être vue.
#
# ⚠ Ce qui n'a **pas** été repris de #684 : l'idée de compléter la table quand un
# sujet manque. Le corollaire du lot 2 le dit — un sujet mal servi se corrige dans
# la documentation, qui est la source du répondeur. Ces tests gardent la table telle
# qu'elle est, ils n'invitent pas à la nourrir.

#: Les pièges de sous-chaîne mesurés en #684 : la question, le mot-clé qui s'y
#: cachait, et le sujet que le répondeur servait à sa place.
PIEGES = (
    ("Comment rendre la page plus rapide ?", "api", "parametres"),
    ("Peux-tu m'écouter ?", "cout", "couts"),
    ("Je voudrais un retour d'expérience", "tour", "guide"),
    ("Comment se passe la conversion ?", "version", "playbooks"),
    ("Où est le chapitre sur la sécurité ?", "api", "parametres"),
    ("Pourquoi ça affiche Reconnexion… ?", "connexion", "parametres"),
)


class TestPiegeDeLaSousChaine:
    """Le motif est prouvé avant d'être corrigé — sinon on garde un ✓ pour rien."""

    @pytest.mark.parametrize(("question", "cache", "sujet"), PIEGES)
    def test_le_piege_est_bien_la(self, question: str, cache: str, sujet: str) -> None:
        """L'ancienne règle — `mot in question` — trouve bien le mot-clé caché.

        Ce test ne juge pas le code courant : il tient l'**échantillon**. S'il
        rougit, c'est que la question a été adoucie ou le mot-clé retiré de la
        table, et les tests d'à côté ne prouvent plus rien.
        """
        assert cache in sujet_nomme(sujet).mots, (
            f"« {cache} » n'est plus un mot-clé de « {sujet} » : l'échantillon a fondu"
        )
        assert cache in normaliser(question), (
            f"« {cache} » ne se cache plus dans « {question} » : l'échantillon a fondu"
        )

    @pytest.mark.parametrize(("question", "cache", "sujet"), PIEGES)
    def test_la_sous_chaine_ne_declenche_plus_le_sujet(
        self, question: str, cache: str, sujet: str
    ) -> None:
        """Un mot-clé au milieu d'un autre mot ne fait plus répondre son sujet."""
        trouve = sujet_assistance(question)
        assert trouve is None or trouve.identifiant != sujet

    def test_le_cas_du_ticket_retombe_sur_l_orientation(self) -> None:
        """« Comment rendre la page plus rapide ? » : le repli ne sait pas.

        C'est la bonne réponse — la question ne parle ni de paramètres, ni de
        backend, ni d'URL, et aucun sujet de la table ne traite de performance. Sur
        le chemin nominal, cette question-là est désormais servie par la
        documentation ; ici, c'est la démo qui répond, et elle avoue.
        """
        assert repondre_assistance("Comment rendre la page plus rapide ?") == _ORIENTATION


class TestFrontieresDeMot:
    """La règle elle-même, éprouvée hors de la table : où elle s'arrête."""

    @pytest.mark.parametrize(
        ("phrase", "cle"),
        (
            ("les couts du mois", "cout"),
            ("combien ca coute", "cout"),  # flexion : pluriel, féminin
            ("les taches bloquees", "bloque"),
            ("l url de l api", "api"),
            ("la prise en main du produit", "prise en main"),  # mots-clés multiples
        ),
    )
    def test_le_mot_entier_est_trouve(self, phrase: str, cle: str) -> None:
        assert _mot_present(phrase.split(), cle)

    @pytest.mark.parametrize(
        ("phrase", "cle"),
        (
            ("comment rendre la page plus rapide", "api"),  # le cas de #684
            ("peux tu m ecouter", "cout"),
            ("je voudrais un retour", "tour"),
            ("la conversion des tokens", "version"),
            ("le flux est en reconnexion", "connexion"),
            ("prise de main", "prise en main"),  # les mots doivent se suivre
        ),
    )
    def test_la_sous_chaine_n_est_pas_trouvee(self, phrase: str, cle: str) -> None:
        assert not _mot_present(phrase.split(), cle)

    def test_la_flexion_ne_porte_que_sur_le_dernier_mot(self) -> None:
        """Limite assumée : « les prises en main » ne matche pas « prise en main »."""
        assert not _mot_present("les prises en main".split(), "prise en main")

    def test_un_mot_cle_vide_ne_matche_rien(self) -> None:
        assert not _mot_present("une question quelconque".split(), "")


class TestCouvertureDuRepli:
    """Ce que le repli sait servir — la démo (#65) n'a que lui."""

    @pytest.mark.parametrize(("question", "sujet"), QUESTIONS_REELLES)
    def test_la_question_trouve_son_sujet(self, question: str, sujet: str) -> None:
        trouve = sujet_assistance(question)
        assert trouve is not None, f"« {question} » ne trouve plus aucun sujet"
        assert trouve.identifiant == sujet
        assert repondre_assistance(question) == sujet_nomme(sujet).reponse

    def test_les_amorces_de_l_interface_trouvent_toutes_un_sujet(self) -> None:
        """Ce que le panneau propose sur un fil vide doit trouver une réponse.

        Le test **gagne** au changement de régime plutôt que de le perdre : ces
        questions sont affichées sur un fil vide, donc aussi en démo — là où il n'y
        a pas de fournisseur, donc pas de documentation lue, donc rien d'autre que
        le repli. Une amorce qui y retombe sur « je ne sais pas » est la pire
        réponse possible, l'assistant s'étant lui-même tendu le piège.
        """
        amorces = amorces_de_l_interface()
        assert amorces, "aucune amorce lue : le motif d'extraction ne mord plus"
        for amorce in amorces:
            assert sujet_assistance(amorce) is not None, (
                f"l'interface propose « {amorce} », à quoi le repli ne sait pas répondre"
            )

    def test_chaque_mot_cle_atteint_un_sujet_qui_le_declare(self) -> None:
        """Aucun mot-clé mort : posé seul, il ramène un sujet qui le porte."""
        for sujet in SUJETS_ASSISTANCE:
            for mot in sujet.mots:
                trouve = sujet_assistance(mot)
                assert trouve is not None, (
                    f"« {mot} » ({sujet.identifiant}) ne déclenche plus rien"
                )
                assert mot in trouve.mots, (
                    f"« {mot} » ({sujet.identifiant}) est capté par "
                    f"« {trouve.identifiant} », qui ne le déclare pas"
                )


class TestTableBienFormee:
    """Ce que la comparaison par mots suppose de la table, et que rien ne dit."""

    def test_les_mots_cles_sont_deja_normalises(self) -> None:
        """Un accent, une majuscule ou une apostrophe ne matcherait plus jamais."""
        for sujet in SUJETS_ASSISTANCE:
            for mot in sujet.mots:
                assert normaliser(mot) == mot, (
                    f"« {mot} » ({sujet.identifiant}) n'est pas normalisé : "
                    f"attendu « {normaliser(mot)} »"
                )

    def test_aucun_mot_cle_n_est_la_flexion_d_un_autre(self) -> None:
        """Deux entrées qu'une seule forme satisfait compteraient deux fois."""
        for sujet in SUJETS_ASSISTANCE:
            for mot in sujet.mots:
                formes = {mot + flexion for flexion in _FLEXIONS}
                doublons = [autre for autre in sujet.mots if autre != mot and autre in formes]
                assert not doublons, (
                    f"« {sujet.identifiant} » : {doublons} est déjà couvert par « {mot} »"
                )

    def test_chaque_mot_cle_n_est_declare_qu_une_fois_par_sujet(self) -> None:
        for sujet in SUJETS_ASSISTANCE:
            assert len(set(sujet.mots)) == len(sujet.mots), (
                f"« {sujet.identifiant} » répète un mot-clé"
            )

    def test_les_identifiants_sont_uniques(self) -> None:
        identifiants = [sujet.identifiant for sujet in SUJETS_ASSISTANCE]
        assert len(set(identifiants)) == len(identifiants)

    def test_chaque_sujet_a_des_mots_et_une_reponse(self) -> None:
        for sujet in SUJETS_ASSISTANCE:
            assert sujet.mots, f"« {sujet.identifiant} » n'a aucun mot-clé"
            assert sujet.reponse.strip(), f"« {sujet.identifiant} » n'a pas de réponse"


class TestLeRepliAvoueAussi:
    """Orienter plutôt qu'inventer — la propriété que #684 ne pouvait pas coûter.

    Elle survit au changement de régime, et c'est ce qui rend le repli acceptable :
    un répondeur de secours qui **inventerait** là où le titulaire avoue serait la
    pire des combinaisons, puisqu'il n'est consulté que quand personne ne regarde.
    """

    @pytest.mark.parametrize(
        "question",
        (
            "Quel temps fait-il demain ?",
            "Écris-moi un poème sur la mer",
            "",
            "   ",
        ),
    )
    def test_hors_sujet_retombe_sur_l_orientation(self, question: str) -> None:
        assert sujet_assistance(question) is None
        assert repondre_assistance(question) == _ORIENTATION

    def test_l_orientation_avoue_son_ignorance(self) -> None:
        """Le texte doit dire qu'il ne sait pas, pas broder autour."""
        assert "pas sur de savoir repondre" in normaliser(_ORIENTATION)


class TestRepondeurDeRepli:
    """Le répondeur de repli reste déterministe, sans modèle ni réseau."""

    def test_seul_le_dernier_message_est_lu(self) -> None:
        fil = _fil("Où sont les coûts ?", "…", "Comment changer le thème ?")
        reponse = asyncio.run(RepondeurAssistance().repondre(AGENT_ASSISTANCE, fil))
        assert reponse == sujet_nomme("theme").reponse

    def test_un_fil_vide_oriente(self) -> None:
        reponse = asyncio.run(RepondeurAssistance().repondre(AGENT_ASSISTANCE, []))
        assert reponse == _ORIENTATION

    def test_la_meme_question_donne_la_meme_reponse(self) -> None:
        question = "Comment approuver une validation ?"
        assert repondre_assistance(question) == repondre_assistance(question)
