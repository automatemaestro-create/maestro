"""Ce que Maestro comprend d'un projet neuf, et ce qu'il demande encore (#1031, #1147).

#1031 posait six questions à options fermées — quatre natures, des tests déduits du
langage, trois forges — et un projet d'une autre sorte (« p2 ») n'avait aucune réponse
honnête. #1147 retire le catalogue : le modèle comprend ce que la personne dit, en tire
les constats, ne pose que les questions qui comblent un vrai manque, avec des options
écrites pour ce projet, et une réponse tapée devient un choix enregistré. Cette suite
porte les deux critères du ticket, **sur un faux fournisseur de modèle** : ce qu'on
éprouve est la mécanique réelle — prompt, lecture vérifiée de la compréhension, fil,
routes —, et le faux ne fait que rendre le texte qu'un modèle rendrait.

① la **lecture de la compréhension** : ce que le modèle rend est lu et vérifié, jamais
   cru — une recommandation absente des options est ramenée dedans, une question déjà
   répondue n'est pas reposée, la question ouverte passe en tête tant qu'on ne sait pas
   ce qu'est le projet ;
② des **constats à la recommandation du lot 2** : un projet Flutter, une infrastructure
   Terraform en ressortent avec leur pile, leurs tests, leur forge — par la même
   `recommander` que l'analyse d'un projet existant ;
③ le **conducteur** : un fil muet reçoit la question ouverte sans appel au modèle, ce
   qui a été dit dans la conversation est repris, le prompt porte le registre et
   interdit de justifier un choix par la pile de Maestro, et on s'arrête quand plus
   rien ne manque — sans plafond ;
④ les **routes du fil** : une réponse avec ses mots, et une phrase tapée dans la zone de
   saisie pendant qu'une question attend, deviennent des choix enregistrés ;
⑤ les **routes sans état** du parcours de création, dont la recommandation ne rappelle
   jamais le modèle.

Ni réseau, ni Redis, ni vrai modèle : les routes tournent sur le bus mémoire via le
TestClient de Starlette.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.playbook_du_code import registre
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import (
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurChat,
    RepondeurScripte,
)
from maestro.controltower.outillage import (
    ComprehensionModele,
    ConducteurOutillage,
)
from maestro.controltower.projets import ServiceProjets
from maestro.outillage.questionnaire import (
    SOURCE_CHOIX,
    SUJETS,
    VALEUR_MAX,
    Choix,
    ComprehensionIllisible,
    QuestionOutillage,
    acquis_de,
    comprehension_depuis_texte,
    constats_depuis_choix,
    question_ouverte,
    recommandation_depuis_choix,
    resume_des_choix,
    source_manifeste_des_choix,
)
from maestro.outillage.recommandation import recommander
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

# --- Le faux fournisseur, et ce qu'un modèle rendrait ---------------------------------


class FauxModele(ModelProvider):
    """Rend, dans l'ordre, les compréhensions qu'on lui a données — et note chaque appel.

    Au-delà de la dernière, il rend la dernière : un tour de plus ne change pas ce qu'un
    modèle a compris d'un projet dont on ne lui a rien dit de neuf.
    """

    name = "faux-modele"

    def __init__(self, *reponses: Mapping[str, Any] | str) -> None:
        self._reponses = [r if isinstance(r, str) else json.dumps(r) for r in reponses]
        self.appels: list[dict[str, Any]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        self.appels.append({"prompt": prompt, "model": model, "systeme": system_prompt})
        rang = min(len(self.appels), len(self._reponses)) - 1
        return self._reponses[rang]


def _constat(cle: str, valeur: str, parce_que: str = "") -> dict[str, str]:
    return {"cle": cle, "valeur": valeur, "parce_que": parce_que or f"compris : {valeur}"}


def _option(valeur: str, libelle: str, raison: str) -> dict[str, str]:
    return {"valeur": valeur, "libelle": libelle, "raison": raison}


#: Ce qu'un modèle comprend de « une application mobile Flutter pour réserver des
#: terrains de padel » — la sorte de projet qu'aucune des quatre natures ne prévoyait.
CONSTATS_FLUTTER = [
    _constat("nature", "Une application mobile Flutter", "vous l'avez décrite ainsi"),
    _constat("langages", "Dart", "Flutter s'écrit en Dart"),
    _constat("manifeste", "pubspec.yaml", "le manifeste d'un projet Flutter"),
    _constat("gestionnaire", "flutter pub", "le gestionnaire du SDK Flutter"),
    _constat("installer", "flutter pub get", "la commande d'installation du SDK"),
    _constat("tester", "flutter test", "le lanceur de tests livré avec Flutter"),
    _constat("lint", "flutter analyze", "l'analyseur livré avec Flutter"),
    _constat("demarrer", "flutter run", "lance l'application sur un appareil"),
]

QUESTION_FORGE = {
    "cle": "forge",
    "intitule": "Où le code de l'application vivra-t-il ?",
    "options": [
        _option("github", "GitHub", "Issues, Pull Requests et GitHub Actions."),
        _option("gitlab", "GitLab", "Issues, Merge Requests et GitLab CI."),
        _option("aucun", "Nulle part pour l'instant", "Le projet reste sur ce poste."),
    ],
    "recommande": "aucun",
    "pourquoi": "Rien de ce que vous avez dit ne désigne une forge : je ne choisis pas pour vous.",
}

QUESTION_CI = {
    "cle": "ci",
    "intitule": "Qu'est-ce qui vérifiera l'application à chaque changement ?",
    "options": [
        _option(".github/workflows/ci.yml", "GitHub Actions", "flutter test sur chaque PR."),
        _option("aucun", "Rien pour l'instant", "Les tests se jouent à la main."),
    ],
    "recommande": ".github/workflows/ci.yml",
    "pourquoi": "Le code vivra sur GitHub, qui porte cette CI nativement.",
}

FLUTTER_TOUR_1 = {"message": "", "constats": CONSTATS_FLUTTER, "questions": [QUESTION_FORGE]}
FLUTTER_TOUR_2 = {
    "message": "",
    "constats": [*CONSTATS_FLUTTER, _constat("forge", "github", "votre réponse")],
    "questions": [QUESTION_CI],
}
FLUTTER_FINI = {
    "message": "",
    "constats": [
        *CONSTATS_FLUTTER,
        _constat("forge", "github", "votre réponse"),
        _constat("ci", ".github/workflows/ci.yml", "votre réponse"),
    ],
    "questions": [],
}

#: Ce qu'un modèle comprend de « une infrastructure Terraform pour nos comptes AWS ».
TERRAFORM = {
    "message": "",
    "constats": [
        _constat("nature", "Une infrastructure Terraform", "vous l'avez décrite ainsi"),
        _constat("langages", "HCL", "Terraform s'écrit en HCL"),
        _constat("manifeste", "main.tf", "le point d'entrée d'une configuration"),
        _constat("installer", "terraform init", "télécharge les fournisseurs"),
        _constat("tester", "terraform validate", "vérifie la configuration sans rien créer"),
        _constat("lint", "tflint", "l'analyseur de référence pour Terraform"),
        _constat("formater", "terraform fmt -check", "le format canonique de HCL"),
    ],
    "questions": [QUESTION_FORGE],
}

DESCRIPTION_FLUTTER = "Une application mobile Flutter pour réserver des terrains de padel"


def _flutter_acquis() -> list[Choix]:
    """Le questionnaire Flutter conclu : les réponses données, puis ce qui a été compris."""
    donnees = [
        Choix("nature", DESCRIPTION_FLUTTER, libre=True),
        Choix("forge", "github"),
        Choix("ci", ".github/workflows/ci.yml"),
    ]
    return [*donnees, *comprehension_depuis_texte(json.dumps(FLUTTER_FINI), donnees).constats]


# --- ① La lecture de la compréhension ---------------------------------------------------


def test_la_premiere_question_est_ouverte_et_ne_propose_aucune_case() -> None:
    """Le symptôme du ticket : « Quelle sorte de projet est-ce ? » n'offrait que quatre natures."""
    question = question_ouverte()

    assert question.cle == "nature"
    assert question.options == ()  # aucune liste de sortes de projet où il faudrait entrer
    assert question.recommande == ""
    assert "avec vos mots" in question.pourquoi


def test_une_comprehension_se_lit_en_constats_et_en_questions() -> None:
    comprise = comprehension_depuis_texte(json.dumps(FLUTTER_TOUR_1), [])

    constats = {c.cle: c for c in comprise.constats}
    assert constats["langages"].valeur == "Dart" and constats["langages"].deduit is True
    assert constats["tester"].parce_que == "le lanceur de tests livré avec Flutter"
    (question,) = comprise.questions
    assert question.cle == "forge"
    assert [o.valeur for o in question.options] == ["github", "gitlab", "aucun"]
    assert comprise.terminee is False


def test_une_comprehension_en_bloc_de_code_se_lit_quand_meme() -> None:
    texte = "Voici :\n```json\n" + json.dumps(FLUTTER_TOUR_1) + "\n```"

    assert comprehension_depuis_texte(texte, []).questions[0].cle == "forge"


@pytest.mark.parametrize("texte", ["", "je ne sais pas", "{pas du json}", "[1, 2]"])
def test_une_comprehension_illisible_est_dite_jamais_remplacee(texte: str) -> None:
    """Une compréhension qu'on ne sait pas lire ne devient pas une question inventée."""
    with pytest.raises(ComprehensionIllisible):
        comprehension_depuis_texte(texte, [])


def test_une_recommandation_hors_des_options_est_ramenee_dans_la_liste() -> None:
    """Sinon le bouton serait armé sur une valeur que la liste n'offre pas."""
    brut = {**FLUTTER_TOUR_1, "questions": [{**QUESTION_FORGE, "recommande": "bitbucket"}]}

    (question,) = comprehension_depuis_texte(json.dumps(brut), []).questions

    assert question.recommande == "github"


def test_un_sujet_tranche_d_un_clic_n_est_pas_redemande() -> None:
    """Un clic tranche : c'est ce qui fait converger le questionnaire sans plafond."""
    reponses = [Choix("nature", DESCRIPTION_FLUTTER, libre=True), Choix("forge", "github")]
    brut = {**FLUTTER_TOUR_1, "questions": [QUESTION_FORGE, QUESTION_CI]}

    comprise = comprehension_depuis_texte(json.dumps(brut), reponses)

    assert [q.cle for q in comprise.questions] == ["ci"]


def test_des_mots_qui_ne_tranchent_pas_laissent_le_modele_redemander() -> None:
    """Vu sur la vraie stack : tapé pendant « Où déclarer pytest ? », « pas de forge pour
    l'instant » répondait à autre chose. Le modèle l'a dit — « je repose cette question »
    — et le code, qui fermait tout sujet répondu, posait une autre question que celle
    annoncée. Des mots se comprennent ; le modèle juge s'ils ont tranché."""
    reponses = [
        Choix("nature", DESCRIPTION_FLUTTER, libre=True),
        Choix("forge", "on verra", libre=True),
    ]
    brut = {**FLUTTER_TOUR_1, "questions": [QUESTION_FORGE, QUESTION_CI]}

    comprise = comprehension_depuis_texte(json.dumps(brut), reponses)

    assert [q.cle for q in comprise.questions] == ["forge", "ci"]


def test_tant_que_la_sorte_de_projet_n_est_pas_dite_seule_la_question_ouverte_se_pose() -> None:
    """Demander la CI d'un projet dont on ne sait pas ce qu'il est, c'est demander au hasard."""
    brut = {"message": "", "constats": [], "questions": [QUESTION_CI]}

    (question,) = comprehension_depuis_texte(json.dumps(brut), []).questions

    assert question == question_ouverte()


def test_une_question_mal_formee_est_ecartee_et_ses_options_nettoyees() -> None:
    brut = {
        **FLUTTER_TOUR_1,
        "questions": [
            {"cle": "", "intitule": "sans clé"},
            {"cle": "forge", "intitule": ""},
            {
                "cle": "forge",
                "intitule": "Où ?",
                "options": [{"valeur": ""}, {"valeur": "github"}, {"valeur": "github"}, "x"],
            },
        ],
    }

    (question,) = comprehension_depuis_texte(json.dumps(brut), []).questions

    assert question.intitule == "Où ?"
    assert [(o.valeur, o.libelle) for o in question.options] == [("github", "github")]


def test_un_sujet_nomme_par_son_libelle_retrouve_sa_cle() -> None:
    """Vu sur la vraie stack : le modèle a rangé `flutter analyze` sous « verification ».

    Rangée sous le libellé, la commande ne nourrissait plus aucun constat, et le skill
    de vérification disparaissait de la recommandation sans que personne le voie.
    """
    brut = {
        "constats": [
            _constat("nature", "Une application mobile"),
            _constat("verification", "flutter analyze"),
            _constat("Tests", "flutter test"),
            _constat("langage", "Dart"),
            _constat("intégration continue", ".github/workflows/ci.yml"),
            _constat("plateforme", "Android d'abord"),
        ],
        "questions": [{**QUESTION_FORGE, "cle": "Forge"}],
    }

    comprise = comprehension_depuis_texte(json.dumps(brut), [])

    assert {c.cle: c.valeur for c in comprise.constats} == {
        "nature": "Une application mobile",
        "lint": "flutter analyze",
        "tester": "flutter test",
        "langages": "Dart",
        "ci": ".github/workflows/ci.yml",
    }
    assert comprise.questions[0].cle == "forge"
    constats = constats_depuis_choix(comprise.constats)
    lint = constats.commande_de("lint")
    assert lint is not None and lint.commande == "flutter analyze"


def test_un_constat_hors_du_schema_ne_se_montre_pas() -> None:
    """Vu à la relecture de #1147 : « point_entree » s'affichait tel quel dans la carte.

    Hors du schéma, un constat ne nourrit aucune entrée de l'outillage, et l'écran n'en
    aurait que la clé à montrer. « Ce que j'ai compris » dit ce qui sera écrit : il ne
    le porte pas. Une réponse **cliquée** sur un tel sujet, elle, est une réponse donnée
    — son nom se lit alors en mots, jamais en clé.
    """
    brut = {
        "constats": [
            _constat("nature", "Une application mobile"),
            _constat("point_entree", "lib/main.dart"),
            _constat("plateforme", "Android d'abord"),
        ]
    }

    comprise = comprehension_depuis_texte(json.dumps(brut), [])

    assert [c.cle for c in comprise.constats] == ["nature"]
    assert Choix("point_entree", "lib/main.dart").to_dict()["sujet"] == "point entree"


def test_un_constat_qui_est_une_commande_le_dit() -> None:
    """L'écran rend une commande en chasse fixe, comme dans les options : « dart format . »
    en romain gras se lisait comme une fin de phrase (relecture de #1147)."""
    assert Choix("tester", "flutter test", deduit=True).to_dict()["commande"] is True
    assert Choix("installer", "flutter pub get").to_dict()["commande"] is True
    assert Choix("langages", "Dart", deduit=True).to_dict()["commande"] is False
    # Une phrase tapée sur ce sujet n'est pas une commande : ce sont les mots de la personne.
    tapee = Choix("construire", "Le code sera sur GitHub", libre=True)
    assert tapee.to_dict()["commande"] is False


def test_les_sujets_se_nomment_d_une_seule_forme() -> None:
    """Des noms, pas un mélange de noms et d'infinitifs (relecture de #1147 : « manifeste »,
    « gestionnaire » à côté d'« installer », « construire », « démarrer »)."""
    assert SUJETS["installer"] == "installation"
    assert SUJETS["construire"] == "construction"
    assert SUJETS["demarrer"] == "démarrage"


def test_un_constat_tient_sur_une_ligne_bornee() -> None:
    """Il finit entre accents graves dans un skill : trois paragraphes n'en sont pas un."""
    brut = {"constats": [_constat("nature", "x"), _constat("tester", "a\n\nb" + "c" * 500)]}

    constats = comprehension_depuis_texte(json.dumps(brut), []).constats
    tester = next(c for c in constats if c.cle == "tester")

    assert "\n" not in tester.valeur
    assert len(tester.valeur) <= VALEUR_MAX


def test_la_question_voyage_sans_plafond_et_se_relit_sans_rejuger() -> None:
    """Le plafond de #1031 est retiré : une forme stockée qui en porte un se relit sans lui."""
    question = comprehension_depuis_texte(json.dumps(FLUTTER_TOUR_1), []).question_suivante(rang=2)
    assert question is not None

    forme = question.to_dict()

    assert "total" not in forme and forme["rang"] == 2
    assert QuestionOutillage.from_dict(forme) == question
    ancienne = {**forme, "total": 6, "options": [*forme["options"], "?"]}
    assert QuestionOutillage.from_dict(ancienne) == question


def test_un_choix_dit_sa_provenance_et_nomme_son_sujet() -> None:
    tape = Choix("forge", "on verra, sans doute GitHub", libre=True)

    assert tape.to_dict() == {
        "cle": "forge",
        "valeur": "on verra, sans doute GitHub",
        "deduit": False,
        "parce_que": "",
        "libre": True,
        "sujet": SUJETS["forge"],
        "commande": False,
    }
    assert Choix.from_dict(tape.to_dict()) == tape
    assert Choix.from_dict({"cle": "x", "valeur": "y"}).to_dict()["sujet"] == "x"


# --- ② Des constats à la recommandation du lot 2 -----------------------------------------


def test_un_projet_flutter_en_ressort_avec_sa_pile_ses_tests_et_sa_forge() -> None:
    """Critère 1 : une sorte qu'aucune liste ne prévoyait, des constats pertinents."""
    constats = constats_depuis_choix(_flutter_acquis())

    (langage,) = constats.langages
    assert (langage.nom, langage.exemple, langage.fichiers) == ("Dart", "pubspec.yaml", 0)
    (gestionnaire,) = constats.gestionnaires
    assert (gestionnaire.nom, gestionnaire.installer) == ("flutter pub", "flutter pub get")
    tester = constats.commande_de("tester")
    assert tester is not None and tester.commande == "flutter test"
    assert tester.chemin == "pubspec.yaml" and tester.origine == "convention"
    assert tester.extrait == "compris : le lanceur de tests livré avec Flutter"
    demarrer = constats.commande_de("demarrer")
    assert demarrer is not None and demarrer.commande == "flutter run"
    assert constats.forge is not None and constats.forge.nom == "github"
    (ci,) = constats.ci
    assert ci.chemin == ".github/workflows/ci.yml"


def test_une_infrastructure_terraform_aussi() -> None:
    donnees = [Choix("nature", "Une infra Terraform pour nos comptes AWS", libre=True)]
    acquis = [*donnees, *comprehension_depuis_texte(json.dumps(TERRAFORM), donnees).constats]

    constats = constats_depuis_choix(acquis)

    assert [lang.nom for lang in constats.langages] == ["HCL"]
    assert [(c.usage, c.commande) for c in constats.commandes] == [
        ("installer", "terraform init"),
        ("tester", "terraform validate"),
        ("lint", "tflint"),
        ("formater", "terraform fmt -check"),
    ]
    assert constats.gestionnaires == ()  # rien n'a été dit ni compris d'un gestionnaire


def test_les_constats_rendent_la_recommandation_du_lot_2_sans_second_chemin() -> None:
    acquis = _flutter_acquis()

    reco = recommandation_depuis_choix(acquis)

    assert reco == recommander(constats_depuis_choix(acquis))
    assert {e.nom for e in reco.entrees if e.type == "skill"} >= {
        "mettre-en-route",
        "lancer-les-tests",
        "verifier-le-style",
        "lancer-en-local",
    }
    assert {e.etat for e in reco.entrees} == {"a-generer"}


def test_aucun_est_une_reponse_qui_ne_constate_rien() -> None:
    constats = constats_depuis_choix(
        [
            Choix("tester", "aucun", deduit=True),
            Choix("forge", "Aucune"),
            Choix("ci", "aucun"),
        ]
    )

    assert constats.commandes == () and constats.forge is None and constats.ci == ()


def test_une_reponse_tapee_n_est_jamais_un_constat_par_elle_meme() -> None:
    """« On verra, sans doute GitHub » n'est pas un nom de forge : le modèle le comprend."""
    assert acquis_de([Choix("forge", "on verra, sans doute GitHub", libre=True)]) == ()
    assert constats_depuis_choix([Choix("forge", "on verra", libre=True)]).forge is None


def test_la_comprehension_fait_foi_et_les_clics_ne_font_que_completer() -> None:
    """Une correction tapée (« finalement GitLab ») ne perd pas contre le clic qu'elle corrige."""
    choix = [
        Choix("forge", "github"),
        Choix("tests", "pytest"),
        Choix("ci", "finalement GitLab", libre=True),
        Choix("forge", "gitlab", deduit=True, parce_que="corrigé : finalement GitLab"),
    ]

    acquis = {c.cle: c.valeur for c in acquis_de(choix)}

    assert acquis == {"forge": "gitlab", "tests": "pytest"}


def test_le_resume_et_la_provenance_disent_ce_qui_est_acquis() -> None:
    acquis = _flutter_acquis()

    assert resume_des_choix(acquis) == (
        "Une application mobile Flutter ; Dart ; tests : flutter test ; "
        "CI : .github/workflows/ci.yml"
    )
    assert resume_des_choix([]) == "aucun choix encore donné"
    source = source_manifeste_des_choix("prj-neuf", acquis)
    assert source["type"] == SOURCE_CHOIX and source["projet_id"] == "prj-neuf"
    assert "tester=flutter test" in source["reference"]
    assert DESCRIPTION_FLUTTER not in source["reference"]  # une phrase tapée n'est pas un constat


# --- ③ Le conducteur ---------------------------------------------------------------------


def _message(auteur: str, contenu: str, **champs: Any) -> MessageChat:
    return MessageChat(agent="orchestrateur", auteur=auteur, contenu=contenu, **champs)


def _conducteur(faux: FauxModele) -> ConducteurOutillage:
    return ConducteurOutillage(ComprehensionModele(faux))


def test_un_fil_muet_recoit_la_question_ouverte_sans_appel_au_modele() -> None:
    faux = FauxModele(FLUTTER_TOUR_1)

    reponse = asyncio.run(_conducteur(faux).ouvrir([]))

    assert reponse.question == question_ouverte()
    assert reponse.question.intitule in reponse.contenu
    assert faux.appels == []


def test_ce_qui_a_ete_dit_dans_la_conversation_est_repris() -> None:
    """« Le fil demande ce qu'est le projet, ou reprend ce qui a déjà été dit »."""
    faux = FauxModele(FLUTTER_TOUR_1)
    fil = [_message(UTILISATEUR, f"Je voudrais créer {DESCRIPTION_FLUTTER.lower()}.")]

    reponse = asyncio.run(_conducteur(faux).ouvrir(fil))

    assert reponse.question is not None and reponse.question.cle == "forge"
    (appel,) = faux.appels
    assert "terrains de padel" in appel["prompt"]
    assert "(aucune réponse pour l'instant)" in appel["prompt"]


def test_le_prompt_porte_le_registre_et_interdit_la_pile_de_maestro() -> None:
    """Critère 2 : des options pour ce projet, « sans défaut justifié par la pile de Maestro »."""
    faux = FauxModele(FLUTTER_TOUR_1)

    asyncio.run(_conducteur(faux).ouvrir([_message(UTILISATEUR, DESCRIPTION_FLUTTER)]))

    systeme = faux.appels[0]["systeme"]
    assert registre() in systeme
    assert "Ne justifie jamais un choix par ce que Maestro utilise lui-même" in systeme
    assert "écrites POUR CE PROJET" in systeme
    assert "Il n'y a pas de nombre de questions à atteindre" in systeme
    # Relecture de #1147 : une question de trois lignes et une justification de cinq
    # faisaient déborder la carte de la colonne de conversation.
    assert "une question courte" in systeme and "une seule phrase courte" in systeme
    for cle in SUJETS:
        assert f'"{cle}"' in systeme  # le schéma est cité, une seule fois écrit


def test_les_reponses_cliquees_et_tapees_sont_dites_comme_telles_au_modele() -> None:
    faux = FauxModele(FLUTTER_TOUR_2)
    decrite = Choix("nature", DESCRIPTION_FLUTTER, libre=True)
    fil = [
        _message("orchestrateur", "Qu'est-ce que ce projet ?", question=question_ouverte()),
        _message(UTILISATEUR, DESCRIPTION_FLUTTER, choix=decrite),
        _message("orchestrateur", "Où ?", question=QuestionOutillage.from_dict(QUESTION_FORGE)),
        _message(UTILISATEUR, "Où ? → GitHub", choix=Choix("forge", "github")),
    ]

    asyncio.run(_conducteur(faux).ouvrir(fil))

    prompt = faux.appels[0]["prompt"]
    assert f"- nature (tapée) : « {DESCRIPTION_FLUTTER} »" in prompt
    assert "- forge (cliquée) : « github »" in prompt


def test_la_question_suivante_porte_ce_qui_a_ete_compris() -> None:
    """« Maestro l'a-t-il compris ? » — ça se lit avant la question suivante."""
    faux = FauxModele(FLUTTER_TOUR_1)
    decrite = Choix("nature", DESCRIPTION_FLUTTER, libre=True)
    fil = [_message(UTILISATEUR, DESCRIPTION_FLUTTER, choix=decrite)]

    reponse = asyncio.run(_conducteur(faux).ouvrir(fil))

    assert reponse.question is not None and reponse.question.rang == 2
    compris = {c.cle: c.valeur for c in reponse.comprehension}
    assert compris["langages"] == "Dart" and compris["tester"] == "flutter test"
    # Elle voyage sur le message, et c'est la carte qui la rend — le texte de la
    # bulle ne la recopie pas (relecture de #1147 : le même flot de clés, recopié à
    # chaque question du fil, noyait la question elle-même).
    assert "Ce que j'ai compris" not in reponse.contenu
    assert reponse.contenu.startswith(reponse.question.intitule)


def test_le_message_du_modele_precede_la_question() -> None:
    """Une question en retour (« c'est quoi une CI ? ») reçoit sa réponse avant d'être reposée."""
    faux = FauxModele({**FLUTTER_TOUR_2, "message": "Une CI rejoue vos tests à chaque changement."})

    reponse = asyncio.run(_conducteur(faux).ouvrir([_message(UTILISATEUR, DESCRIPTION_FLUTTER)]))

    assert reponse.contenu.startswith("Une CI rejoue vos tests à chaque changement.")


def test_un_message_sans_rien_de_compris_ne_laisse_pas_de_blanc() -> None:
    """Vu sur la vraie stack : message du modèle, rien de compris, question ouverte."""
    faux = FauxModele({"message": "Dites-moi d'abord ce qu'est ce projet.", "constats": []})

    reponse = asyncio.run(_conducteur(faux).ouvrir([_message(UTILISATEUR, "Bonjour")]))

    assert reponse.question == question_ouverte()
    assert "\n\n\n" not in reponse.contenu
    assert reponse.contenu.startswith(
        "Dites-moi d'abord ce qu'est ce projet.\n\nQu'est-ce que ce projet ?"
    )


def test_on_s_arrete_quand_plus_rien_ne_manque_et_il_n_y_a_pas_de_plafond() -> None:
    """Critère 2 : le plafond fixe disparaît — huit questions, puis la conclusion."""
    sujets = ["forge", "ci", "conventions", "types", "construire", "lint", "formater", "demarrer"]
    tours = [
        {
            "constats": CONSTATS_FLUTTER[:1],
            "questions": [
                {
                    "cle": sujet,
                    "intitule": f"Et {sujet} ?",
                    "options": [_option("aucun", "Rien", "—")],
                }
                for sujet in sujets[rang:]
            ],
        }
        for rang in range(len(sujets) + 1)
    ]
    faux = FauxModele(*tours)
    fil: list[MessageChat] = [_message(UTILISATEUR, DESCRIPTION_FLUTTER)]
    posees: list[tuple[str, int]] = []
    conducteur = _conducteur(faux)

    while (reponse := asyncio.run(conducteur.ouvrir(fil))).question is not None:
        question = reponse.question
        posees.append((question.cle, question.rang))
        fil += [
            _message("orchestrateur", reponse.contenu, question=question),
            _message(UTILISATEUR, "Rien", choix=Choix(question.cle, "aucun")),
        ]

    assert [cle for cle, _ in posees] == sujets  # huit, soit plus que les six de #1031
    assert [rang for _, rang in posees] == list(range(1, len(sujets) + 1))
    assert reponse.contenu.startswith("C'est tout ce qu'il me fallait")


def test_la_conclusion_porte_la_comprehension_et_dit_ou_valider() -> None:
    faux = FauxModele(FLUTTER_FINI)
    fil = [_message(UTILISATEUR, c.valeur, choix=c) for c in _flutter_acquis() if not c.deduit]

    reponse = asyncio.run(_conducteur(faux).ouvrir(fil))

    assert reponse.question is None
    assert {c.cle for c in reponse.comprehension} >= {"langages", "tester", "forge", "ci"}
    reco = recommandation_depuis_choix(reponse.comprehension)
    assert f"{len(reco.entrees)} entrée(s)" in reponse.contenu
    assert "au pied de cette conversation" in reponse.contenu


def test_le_conducteur_ne_garde_aucun_etat_du_questionnaire() -> None:
    """Ses seuls attributs sont ses collaborateurs — celui qui comprend, et celui qui trouve
    les clients du poste (#1295) : jamais une réponse ni un tour."""
    assert set(vars(ConducteurOutillage())) == {"_comprehension", "_clients"}


# --- ④ Les routes du fil -------------------------------------------------------------


class _RepondeurMuet(RepondeurChat):
    """Un répondeur qui ne conduit aucun questionnaire — le cas du `409`."""

    async def repondre(self, agent: object, fil: object) -> str:
        return "je ne pose pas de question."


def _client_chat(tmp_path: Path, faux: FauxModele) -> TestClient:
    app = create_app(
        bus=InMemoryEventBus(),
        chat_store=ChatStore(tmp_path / "chat"),
        chat_repondeur=RepondeurScripte(conducteur=_conducteur(faux)),
    )
    return TestClient(app)


@pytest.fixture()
def faux_flutter() -> FauxModele:
    return FauxModele(FLUTTER_TOUR_1, FLUTTER_TOUR_2, FLUTTER_FINI)


@pytest.fixture()
def client_chat(tmp_path: Path, faux_flutter: FauxModele) -> Iterator[TestClient]:
    with _client_chat(tmp_path, faux_flutter) as client:
        yield client


def test_la_route_ouvre_le_questionnaire_par_la_question_ouverte(
    client_chat: TestClient, faux_flutter: FauxModele
) -> None:
    reponse = client_chat.post("/api/chat/qa/outillage/questionnaire")

    assert reponse.status_code == 201, reponse.text
    (message,) = reponse.json()["messages"]
    assert message["question"]["cle"] == "nature"
    assert message["question"]["options"] == []
    assert "total" not in message["question"]
    assert faux_flutter.appels == []


def test_une_reponse_avec_ses_mots_devient_un_choix_enregistre(
    client_chat: TestClient, faux_flutter: FauxModele
) -> None:
    """Critère 2 : la réponse libre part, s'enregistre telle quelle, et le modèle la lit."""
    client_chat.post("/api/chat/qa/outillage/questionnaire")

    reponse = client_chat.post(
        "/api/chat/qa/outillage", json={"valeur": DESCRIPTION_FLUTTER, "libre": True}
    )

    assert reponse.status_code == 201, reponse.text
    geste, suite = reponse.json()["messages"]
    assert geste["choix"]["valeur"] == DESCRIPTION_FLUTTER
    assert geste["choix"]["libre"] is True and geste["choix"]["deduit"] is False
    # Les mots de la personne, tels quels — comme une phrase tapée dans la zone de
    # saisie. Relecture de #1147 : écrit « Question → … », le fil prenait pour titre la
    # question de Maestro, et la description du projet disparaissait sous « … ».
    assert geste["contenu"] == DESCRIPTION_FLUTTER
    (conversation,) = client_chat.get("/api/chat/qa/conversations").json()["conversations"]
    assert conversation["titre"].startswith("Une application mobile Flutter")
    assert suite["question"]["cle"] == "forge"
    assert [o["valeur"] for o in suite["question"]["options"]] == ["github", "gitlab", "aucun"]
    assert {c["cle"] for c in suite["comprehension"]} >= {"langages", "tester"}
    assert DESCRIPTION_FLUTTER in faux_flutter.appels[0]["prompt"]
    # Enregistrée : le fil relu la porte encore.
    fil = client_chat.get("/api/chat/qa").json()["messages"]
    assert any((m.get("choix") or {}).get("valeur") == DESCRIPTION_FLUTTER for m in fil)


def test_une_phrase_tapee_dans_le_fil_repond_a_la_question_qui_attend(
    client_chat: TestClient, faux_flutter: FauxModele
) -> None:
    """Critère 2, le défaut du ticket : la correction tapée faisait disparaître la carte, perdue."""
    client_chat.post("/api/chat/qa/outillage/questionnaire")
    client_chat.post("/api/chat/qa/outillage", json={"valeur": DESCRIPTION_FLUTTER, "libre": True})

    reponse = client_chat.post(
        "/api/chat/qa/messages", json={"contenu": "Sur GitHub, dans l'organisation du club"}
    )

    assert reponse.status_code == 201, reponse.text
    message, suite = reponse.json()["messages"]
    assert message["contenu"] == "Sur GitHub, dans l'organisation du club"
    assert message["choix"] == {
        "cle": "forge",
        "valeur": "Sur GitHub, dans l'organisation du club",
        "deduit": False,
        "parce_que": "",
        "libre": True,
        "sujet": SUJETS["forge"],
        "commande": False,
    }
    # Confiée au questionnaire, pas au juge : la suite est la question comprise d'après.
    assert suite["question"]["cle"] == "ci"
    assert "- forge (tapée) : « Sur GitHub, dans l'organisation du club »" in (
        faux_flutter.appels[-1]["prompt"]
    )


def test_une_phrase_tapee_hors_questionnaire_reste_un_message_ordinaire(
    client_chat: TestClient, faux_flutter: FauxModele
) -> None:
    reponse = client_chat.post("/api/chat/qa/messages", json={"contenu": "Bonjour"})

    message, suite = reponse.json()["messages"]
    assert message["choix"] is None and suite["question"] is None
    assert faux_flutter.appels == []


def test_une_valeur_hors_des_options_posees_est_refusee(client_chat: TestClient) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")
    client_chat.post("/api/chat/qa/outillage", json={"valeur": DESCRIPTION_FLUTTER, "libre": True})

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "bitbucket"})

    assert reponse.status_code == 422, reponse.text
    assert "hors des options posées" in reponse.json()["detail"]
    assert "avec vos mots" in reponse.json()["detail"]


def test_une_question_ouverte_ne_se_repond_pas_par_une_option(client_chat: TestClient) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "application-web"})

    assert reponse.status_code == 422, reponse.text
    assert "avec vos mots" in reponse.json()["detail"]


def test_une_reponse_libre_vide_est_refusee(client_chat: TestClient) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "   ", "libre": True})

    assert reponse.status_code == 422, reponse.text


def test_un_geste_sans_question_en_attente_est_un_conflit(client_chat: TestClient) -> None:
    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "x", "libre": True})

    assert reponse.status_code == 409, reponse.text
    assert "aucune question d'outillage en attente" in reponse.json()["detail"]


def test_le_questionnaire_se_deroule_dans_le_fil_jusqu_a_sa_conclusion(
    client_chat: TestClient,
) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")
    client_chat.post("/api/chat/qa/outillage", json={"valeur": DESCRIPTION_FLUTTER, "libre": True})
    client_chat.post("/api/chat/qa/outillage", json={"valeur": "github"})

    fin = client_chat.post("/api/chat/qa/outillage", json={"valeur": ".github/workflows/ci.yml"})

    assert fin.status_code == 201, fin.text
    suite = fin.json()["messages"][1]
    assert suite["question"] is None
    assert suite["contenu"].startswith(
        "C'est tout ce qu'il me fallait — Une application mobile Flutter"
    )
    compris = {c["cle"]: c["valeur"] for c in suite["comprehension"]}
    assert compris["ci"] == ".github/workflows/ci.yml" and compris["forge"] == "github"
    tardif = client_chat.post("/api/chat/qa/outillage", json={"valeur": "x", "libre": True})
    assert tardif.status_code == 409


def test_une_comprehension_illisible_est_une_502_et_la_reponse_reste_acquise(
    tmp_path: Path,
) -> None:
    with _client_chat(tmp_path, FauxModele("je n'ai pas compris")) as client:
        client.post("/api/chat/qa/outillage/questionnaire")
        reponse = client.post(
            "/api/chat/qa/outillage", json={"valeur": DESCRIPTION_FLUTTER, "libre": True}
        )
        fil = client.get("/api/chat/qa").json()["messages"]

    assert reponse.status_code == 502, reponse.text
    assert fil[-1]["choix"]["valeur"] == DESCRIPTION_FLUTTER


def test_un_fil_hors_catalogue_reste_un_404(client_chat: TestClient) -> None:
    assert client_chat.post("/api/chat/personne/outillage/questionnaire").status_code == 404


def test_un_repondeur_qui_ne_pose_pas_de_question_rend_un_conflit(tmp_path: Path) -> None:
    app = create_app(
        bus=InMemoryEventBus(),
        chat_store=ChatStore(tmp_path / "chat"),
        chat_repondeur=_RepondeurMuet(),
    )
    with TestClient(app) as client:
        reponse = client.post("/api/chat/qa/outillage/questionnaire")

    assert reponse.status_code == 409, reponse.text
    assert "ne conduit pas de questionnaire" in reponse.json()["detail"]


# --- ⑤ Les routes sans état du parcours de création -----------------------------------


@pytest.fixture()
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le dossier explorable des tests, sous un dossier utilisateur factice.

    Même raison qu'en #221 : sous Windows le `tmp_path` de pytest vit dans
    `AppData/Local/Temp`, que `valider_racine` refuse à raison.
    """
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    atelier = tmp_path / "atelier"
    atelier.mkdir()
    return atelier


@pytest.fixture()
def faux_terraform() -> FauxModele:
    return FauxModele(TERRAFORM)


@pytest.fixture()
def client_projets(
    tmp_path: Path, atelier: Path, faux_terraform: FauxModele
) -> Iterator[TestClient]:
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        comprehension=ComprehensionModele(faux_terraform),
    )
    with TestClient(app) as client:
        yield client


def _projet_neuf(client: TestClient, atelier: Path) -> str:
    racine = atelier / "neuf"
    racine.mkdir(exist_ok=True)
    reponse = client.post("/api/projets", json={"nom": "Neuf", "racine": str(racine)})
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _question(client: TestClient, projet: str, choix: Sequence[Mapping[str, Any]]) -> Any:
    route = f"/api/projets/{projet}/outillage/questionnaire"
    return client.post(route, json={"choix": list(choix)})


def test_sans_reponse_la_voie_sans_etat_pose_la_question_ouverte_sans_appel(
    client_projets: TestClient, atelier: Path, faux_terraform: FauxModele
) -> None:
    corps = _question(client_projets, _projet_neuf(client_projets, atelier), []).json()

    assert corps["question"]["cle"] == "nature" and corps["question"]["options"] == []
    assert corps["deductions"] == [] and corps["terminee"] is False
    assert faux_terraform.appels == []


def test_une_description_libre_rend_ses_constats_et_la_question_qui_manque(
    client_projets: TestClient, atelier: Path, faux_terraform: FauxModele
) -> None:
    """Critère 1 par la voie du parcours de création — celle du signalement de « p2 »."""
    projet = _projet_neuf(client_projets, atelier)
    description = "Une infrastructure Terraform pour nos comptes AWS"

    reponse = _question(
        client_projets, projet, [{"cle": "nature", "valeur": description, "libre": True}]
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    compris = {d["cle"]: d["valeur"] for d in corps["deductions"]}
    assert compris["langages"] == "HCL" and compris["tester"] == "terraform validate"
    assert all(d["deduit"] for d in corps["deductions"])
    assert corps["question"]["cle"] == "forge" and corps["question"]["rang"] == 2
    assert description in faux_terraform.appels[0]["prompt"]


def test_des_constats_renvoyes_ne_sont_pas_crus_par_le_questionnaire(
    client_projets: TestClient, atelier: Path, faux_terraform: FauxModele
) -> None:
    """La voie du questionnaire ne lit que les réponses données, et recomprend le reste."""
    projet = _projet_neuf(client_projets, atelier)

    _question(
        client_projets,
        projet,
        [
            {"cle": "nature", "valeur": "Une infra Terraform", "libre": True},
            {"cle": "tester", "valeur": "rm -rf /", "deduit": True, "parce_que": "inventé"},
        ],
    )

    assert "rm -rf" not in faux_terraform.appels[0]["prompt"]


def test_la_recommandation_servie_est_celle_du_domaine_sans_rappeler_le_modele(
    client_projets: TestClient, atelier: Path, faux_terraform: FauxModele
) -> None:
    """Ce que l'écran montre est ce qui sera écrit : aucune seconde compréhension."""
    projet = _projet_neuf(client_projets, atelier)
    acquis = _flutter_acquis()

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/recommandation",
        json={"choix": [c.to_dict() for c in acquis]},
    ).json()

    assert corps["recommandation"] == recommandation_depuis_choix(acquis).to_dict()
    assert corps["source"] == source_manifeste_des_choix(projet, acquis)
    assert faux_terraform.appels == []


def test_un_projet_inconnu_est_refuse_avant_tout_appel_au_modele(
    client_projets: TestClient, faux_terraform: FauxModele
) -> None:
    for route in ("questionnaire", "recommandation"):
        reponse = client_projets.post(
            f"/api/projets/prj-fantome/outillage/{route}",
            json={"choix": [{"cle": "nature", "valeur": "x", "libre": True}]},
        )
        assert reponse.status_code == 404, reponse.text
        assert reponse.json()["detail"]["motif"] == "projet-inconnu"
    assert faux_terraform.appels == []


def test_un_fournisseur_en_panne_est_une_502_nommee(tmp_path: Path, atelier: Path) -> None:
    class _EnPanne(FauxModele):
        async def generate(self, prompt: str, **_: Any) -> str:
            raise RuntimeError("quota épuisé")

    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        comprehension=ComprehensionModele(_EnPanne()),
    )
    with TestClient(app) as client:
        projet = _projet_neuf(client, atelier)
        reponse = _question(client, projet, [{"cle": "nature", "valeur": "x", "libre": True}])

    assert reponse.status_code == 502, reponse.text
    assert "quota épuisé" in reponse.json()["detail"]
