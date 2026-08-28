"""Le canal d'assistance juge une question sur des MOTS, pas sur des sous-chaînes (#684).

`repondre_assistance` marquait chaque sujet par le nombre de ses mots-clés
**contenus** dans la question normalisée. Un mot-clé court se retrouvait alors au
milieu d'un mot qui n'a rien à voir — `api` dans « r**api**de », `cout` dans
« é**cout**er », `tour` dans « re**tour** », `version` dans « con**version** »,
`connexion` dans « **re**connexion » —, et l'assistant répondait avec aplomb sur
un sujet que personne n'avait évoqué.

Trois moitiés, et la première est ce qui donne son sens aux autres :

- **l'échantillon fautif** (`TestPiegeDeLaSousChaine`) prouve que le piège est
  bien là — chaque cas y est joué **sous l'ancienne règle** (`mot in question`)
  avant d'être joué sous la nouvelle. Sans cette moitié, un jour où quelqu'un
  aurait adouci l'échantillon, la suite rendrait un ✓ sur une question jamais
  posée ;
- les **témoins positifs** (`TestTemoinsPositifs`) tiennent l'autre bord : le
  resserrement ne doit pas transformer en « je ne sais pas » ce à quoi la table
  répondait juste. C'est ce qui a fait garder une tolérance de flexion — sans
  elle, « combien ça coûte ? » ne trouvait plus le sujet « couts » ;
- les **invariants de la table** (`TestTableBienFormee`) gardent ce que la règle
  suppose d'elle : des mots-clés déjà normalisés (un accent ou une majuscule ne
  matcherait plus jamais rien), et aucune entrée qui soit la flexion d'une autre
  — elle compterait deux fois dans le score, en silence.

Et par-dessus tout cela, la propriété que rien n'avait le droit de coûter :
l'assistant **dit quand il ne sait pas** (`TestDitQuandIlNeSaitPas`) au lieu
d'inventer. C'est déjà ce que faisait `_ORIENTATION` ; resserrer la comparaison
rend cette réponse plus fréquente, et c'est le bon sens de l'échange — « je ne
sais pas » est un aveu, la réponse d'à côté était un mensonge.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from maestro.controltower.assistance import (
    _FLEXIONS,
    _ORIENTATION,
    AGENT_ASSISTANCE,
    SUJETS_ASSISTANCE,
    RepondeurAssistance,
    _mot_present,
    repondre_assistance,
    sujet_assistance,
)
from maestro.controltower.chat import MessageChat, normaliser

#: Le fichier où l'interface déclare les questions qu'elle **propose** d'elle-même
#: sur un fil vide (`AMORCES_ASSISTANCE`). Elles sont lues là plutôt que recopiées
#: ici : recopiées, elles dériveraient, et le test garderait une liste que
#: personne ne propose plus.
AMORCES_TS = Path(__file__).resolve().parents[1] / "apps" / "web" / "lib" / "assistance.ts"


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

    Une réponse se réécrit (elles l'ont toutes été ici, l'interface ayant bougé
    sous la table depuis #123) ; un identifiant est le contrat.
    """
    for sujet in SUJETS_ASSISTANCE:
        if sujet.identifiant == identifiant:
            return sujet
    raise AssertionError(f"sujet inconnu : {identifiant}")


# --------------------------------------------------------------------------- #
# 1. L'échantillon fautif                                                      #
# --------------------------------------------------------------------------- #

#: Les pièges mesurés sur `main` : la question, le mot-clé qui s'y cachait, et le
#: sujet que l'assistant servait à sa place. Le premier est celui du ticket ; le
#: dernier a été trouvé en écrivant les témoins positifs — « Reconnexion… » est
#: une question sur le temps réel, à laquelle « Paramètres » répondait parce que
#: `connexion` s'y cache.
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
        """« Comment rendre la page plus rapide ? » : l'assistant ne sait pas.

        C'est la bonne réponse — la question ne parle ni de paramètres, ni de
        backend, ni d'URL, et aucun sujet de la table ne traite de performance.
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
            ("comment rendre la page plus rapide", "api"),  # le cas du ticket
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
        """Limite assumée : « les prises en main » ne matche pas « prise en main ».

        La porter sur les mots précédents ouvrirait la règle sans qu'on sache
        dire jusqu'où, pour un gain qu'aucun mot-clé de la table ne réclame.
        """
        assert not _mot_present("les prises en main".split(), "prise en main")

    def test_un_mot_cle_vide_ne_matche_rien(self) -> None:
        assert not _mot_present("une question quelconque".split(), "")


# --------------------------------------------------------------------------- #
# 2. Les témoins positifs                                                      #
# --------------------------------------------------------------------------- #

#: Ce à quoi la table répondait juste, et doit continuer de répondre juste. Les
#: trois dernières lignes ne passaient que par la sous-chaîne (« coute » contient
#: « cout », « validations » contient « validation ») : ce sont elles qui ont fait
#: garder la tolérance de flexion plutôt qu'une égalité stricte.
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

#: Ce que la table ne couvrait pas et couvre désormais — la moitié « compléter la
#: table » de l'option (a). La première ligne est le **second exemple du ticket**,
#: qui tombait sur l'orientation : le resserrement seul ne l'aurait pas corrigé.
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


class TestTemoinsPositifs:
    """Resserrer la comparaison ne doit pas vider la table de sa couverture."""

    @pytest.mark.parametrize(("question", "sujet"), TEMOINS + NOUVEAUX_SUJETS)
    def test_la_question_trouve_son_sujet(self, question: str, sujet: str) -> None:
        trouve = sujet_assistance(question)
        assert trouve is not None, f"« {question} » ne trouve plus aucun sujet"
        assert trouve.identifiant == sujet
        assert repondre_assistance(question) == sujet_nomme(sujet).reponse

    def test_les_amorces_de_l_interface_trouvent_toutes_un_sujet(self) -> None:
        """Ce que le panneau propose sur un fil vide doit trouver une réponse.

        Ces quatre questions ne sont pas des exemples choisis ici : l'interface
        les **affiche** sur un fil vide, c'est-à-dire qu'elle invite à les poser.
        Une amorce qui retombe sur « je ne sais pas » est la pire réponse
        possible, l'assistant s'étant lui-même tendu le piège.
        """
        amorces = amorces_de_l_interface()
        assert amorces, "aucune amorce lue : le motif d'extraction ne mord plus"
        for amorce in amorces:
            assert sujet_assistance(amorce) is not None, (
                f"l'interface propose « {amorce} », à quoi l'assistant ne sait pas répondre"
            )

    def test_chaque_mot_cle_atteint_un_sujet_qui_le_declare(self) -> None:
        """Aucun mot-clé mort : posé seul, il ramène un sujet qui le porte.

        C'est le filet de la règle nouvelle — un mot-clé mal orthographié, resté
        au pluriel ou accentué ne matcherait plus rien, et la table perdrait de
        la couverture sans que rien ne rougisse.
        """
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


# --------------------------------------------------------------------------- #
# 3. Les invariants de la table                                                #
# --------------------------------------------------------------------------- #


class TestTableBienFormee:
    """Ce que la comparaison par mots suppose de la table, et que rien ne dit."""

    def test_les_mots_cles_sont_deja_normalises(self) -> None:
        """Un accent, une majuscule ou une apostrophe ne matcherait plus jamais.

        La question, elle, passe par `normaliser` : un mot-clé qui n'est pas déjà
        sous cette forme est un mot-clé mort, et rien d'autre ne le dirait.
        """
        for sujet in SUJETS_ASSISTANCE:
            for mot in sujet.mots:
                assert normaliser(mot) == mot, (
                    f"« {mot} » ({sujet.identifiant}) n'est pas normalisé : "
                    f"attendu « {normaliser(mot)} »"
                )

    def test_aucun_mot_cle_n_est_la_flexion_d_un_autre(self) -> None:
        """Deux entrées qu'une seule forme satisfait compteraient deux fois.

        « cout » et « couts » dans le même sujet donnent 2 à « les couts » : le
        score cesse de mesurer la couverture, et un sujet gagne un ex æquo qu'il
        n'a pas mérité.
        """
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


# --------------------------------------------------------------------------- #
# 4. La propriété à ne pas perdre                                              #
# --------------------------------------------------------------------------- #


class TestDitQuandIlNeSaitPas:
    """Orienter plutôt qu'inventer — la seule chose que #684 ne pouvait pas coûter."""

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


class TestRepondeur:
    """Le répondeur reste déterministe, sans modèle ni réseau."""

    def test_seul_le_dernier_message_est_lu(self) -> None:
        fil = [
            MessageChat(agent="assistance", auteur="utilisateur", contenu="Où sont les coûts ?"),
            MessageChat(agent="assistance", auteur="assistance", contenu="…"),
            MessageChat(
                agent="assistance", auteur="utilisateur", contenu="Comment changer le thème ?"
            ),
        ]
        reponse = asyncio.run(RepondeurAssistance().repondre(AGENT_ASSISTANCE, fil))
        assert reponse == sujet_nomme("theme").reponse

    def test_un_fil_vide_oriente(self) -> None:
        reponse = asyncio.run(RepondeurAssistance().repondre(AGENT_ASSISTANCE, []))
        assert reponse == _ORIENTATION

    def test_la_meme_question_donne_la_meme_reponse(self) -> None:
        question = "Comment approuver une validation ?"
        assert repondre_assistance(question) == repondre_assistance(question)
