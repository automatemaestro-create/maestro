"""Le questionnaire qui décide de l'outillage d'un projet neuf (#1031).

Le lot 3/7 de #1020 avait livré sans tests (convention de découpage,
[docs/10 §5.1](../docs/10-workflow-git.md)) ; cette suite les rend, et elle couvre les
deux critères du ticket.

① le **catalogue et ses formes** : une question voyage entière (options, recommandation,
   cause, rang/plafond) et se relit **sans rejuger** — une forme écrite par un catalogue
   antérieur se relit telle quelle, options inconnues comprises ;
② la **réduction**, qui est le sujet : « une question dont la réponse se déduit d'une
   autre ne se pose pas » n'est pas une liste de questions à sauter mais une réduction
   des options, et une question dont il ne reste qu'une option est **déduite** — avec sa
   cause, et comptée comme une réponse à part entière ;
③ les **recommandations** : chaque question en porte une, celle du langage dépend de la
   nature et la **nomme** ; une recommandation rendue intenable est ramenée dans les
   options plutôt que d'armer un bouton sur une valeur absente de la liste ; le compteur
   ne compte que les questions **réellement posées** ;
④ la **jonction avec le lot 2**, le second critère : les réponses deviennent des
   `Constats` et c'est `recommandation.recommander` — la fonction de #1030, inchangée —
   qui en tire la recommandation. Il n'y a donc pas deux chemins à tenir d'accord. S'y
   mesurent les trois propriétés que le module s'interdit de défaire : toutes les
   commandes en `convention`, rien de constaté sur une question sans réponse, aucun
   outillage présent ;
⑤ le **conducteur dans le fil** : il ne retient rien (l'état du questionnaire *est* la
   suite des messages), rouvrir reprend là où l'on en est, et chaque geste dit ce qu'il
   vient d'entraîner avant de poser la suite ;
⑥ les **routes**, les deux voies : celle du fil (`POST /api/chat/{agent}/outillage…`,
   avec ses refus — `409` quand rien n'attend, `422` sur une valeur hors des options
   posées) et celle **sans état** rangée sous le projet
   (`POST /api/projets/{id}/outillage/…`), dont la recommandation est comparée à celle
   du domaine pour prouver qu'il n'y en a qu'une.

Ni réseau, ni Redis, ni modèle : le questionnaire est une fonction pure, et les routes
tournent sur le bus mémoire via le TestClient de Starlette, répondeur scripté.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import (
    UTILISATEUR,
    ChatStore,
    MessageChat,
    RepondeurChat,
    RepondeurScripte,
)
from maestro.controltower.outillage import ConducteurOutillage, _phrase_des_deductions
from maestro.controltower.projets import ServiceProjets
from maestro.outillage.questionnaire import (
    CATALOGUE,
    QUESTIONS_MAX,
    SOURCE_CHOIX,
    Choix,
    Option,
    QuestionOutillage,
    cles_connues,
    constats_depuis_choix,
    deductions,
    options_admissibles,
    question_suivante,
    recommandation_depuis_choix,
    resume_des_choix,
    source_manifeste_des_choix,
    valeur_admissible,
)
from maestro.outillage.recommandation import recommander
from maestro.projets import ProjetStore


def _du_catalogue(cle: str) -> QuestionOutillage:
    """La question du catalogue qui porte cette clé — jamais un indice de position."""
    return next(q for q in CATALOGUE if q.cle == cle)


def _geste(cle: str, valeur: str) -> MessageChat:
    """Le message d'utilisateur qu'un clic sur une option écrit dans le fil."""
    return MessageChat(
        agent="orchestrateur",
        auteur=UTILISATEUR,
        contenu=f"{cle} → {valeur}",
        choix=Choix(cle=cle, valeur=valeur),
    )


def _parcours_python() -> list[Choix]:
    """Un questionnaire complet — réponses données **et** ce qu'elles ont entraîné."""
    donnes = [
        Choix("nature", "service-api"),
        Choix("langages", "python"),
        Choix("forge", "github"),
        Choix("ci", "github-actions"),
        Choix("conventions", "conventional-commits"),
    ]
    return [*donnes, *deductions(donnes)]


def _donnes(choix: Sequence[Choix]) -> list[dict[str, str]]:
    """Les réponses **données** d'un parcours, à la forme du corps des routes sans état."""
    return [{"cle": c.cle, "valeur": c.valeur} for c in choix if not c.deduit]


# --- ① Le catalogue et ses formes --------------------------------------------


def test_une_option_porte_sa_propre_raison() -> None:
    option = Option("pytest", "pytest", "Le lanceur de tests de référence en Python.")

    assert option.to_dict() == {
        "valeur": "pytest",
        "libelle": "pytest",
        "raison": "Le lanceur de tests de référence en Python.",
    }


def test_la_question_voyage_entiere_et_se_relit_a_l_identique() -> None:
    question = question_suivante([])
    assert question is not None

    forme = question.to_dict()

    assert forme["cle"] == "nature"
    assert forme["rang"] == 1 and forme["total"] == QUESTIONS_MAX
    assert [o["valeur"] for o in forme["options"]] == [o.valeur for o in question.options]
    assert forme["recommande"] and forme["pourquoi"]
    # La relecture ne repasse pas par le catalogue : elle rend ce qui a été posé.
    assert QuestionOutillage.from_dict(forme) == question


def test_une_question_relue_ne_repasse_pas_par_le_catalogue() -> None:
    """Le catalogue a pu changer : une ligne du fil se relit telle qu'elle a été vue."""
    relue = QuestionOutillage.from_dict(
        {
            "cle": "tests",
            "intitule": "Comment ce projet jouait-il ses tests ?",
            "options": [
                {"valeur": "nose", "libelle": "Nose (retiré depuis)", "raison": "d'un autre temps"},
                "une entrée qui n'est pas une carte",
            ],
            "recommande": "nose",
            "pourquoi": "",
            "rang": 3,
        }
    )

    assert [o.valeur for o in relue.options] == ["nose"]  # l'entrée informe est écartée
    assert relue.recommande == "nose"  # une valeur hors catalogue survit à la relecture
    assert relue.total == QUESTIONS_MAX  # absent de la forme : le plafond fait défaut
    assert relue.libelle_de("nose") == "Nose (retiré depuis)"
    assert relue.libelle_de("pytest") == "pytest"  # inconnue : rendue telle quelle


def test_une_question_vide_se_relit_sur_ses_defauts() -> None:
    vide = QuestionOutillage.from_dict({})

    assert (vide.cle, vide.intitule, vide.recommande, vide.pourquoi) == ("", "", "", "")
    assert vide.options == () and vide.rang == 0 and vide.total == QUESTIONS_MAX


def test_un_choix_dit_s_il_a_ete_donne_ou_conclu() -> None:
    donne = Choix(cle="forge", valeur="github")
    conclu = Choix(
        cle="ci", valeur="aucune", deduit=True, parce_que="rien n'hébergerait un pipeline"
    )

    assert donne.to_dict() == {
        "cle": "forge",
        "valeur": "github",
        "deduit": False,
        "parce_que": "",
    }
    assert Choix.from_dict(conclu.to_dict()) == conclu
    assert Choix.from_dict({}) == Choix(cle="", valeur="")


# --- ② La réduction, et les questions qu'elle ne pose pas ---------------------


def test_une_question_qui_ne_depend_de_rien_offre_toutes_ses_options() -> None:
    for cle in ("nature", "forge", "conventions"):
        question = _du_catalogue(cle)
        assert options_admissibles(question, []) == question.options


def test_sans_contrainte_connue_rien_n_est_reduit() -> None:
    """Une réponse hors table n'invente pas une contrainte : elle n'en pose aucune."""
    tests = _du_catalogue("tests")

    assert options_admissibles(tests, []) == tests.options
    assert options_admissibles(tests, [Choix("langages", "cobol")]) == tests.options


def test_le_langage_reduit_les_tests_et_la_forge_reduit_la_ci() -> None:
    def valeurs(cle: str, choix: Sequence[Choix]) -> list[str]:
        return [o.valeur for o in options_admissibles(_du_catalogue(cle), choix)]

    assert valeurs("tests", [Choix("langages", "typescript")]) == ["vitest", "jest"]
    assert valeurs("tests", [Choix("langages", "python")]) == ["pytest"]
    assert valeurs("ci", [Choix("forge", "gitlab")]) == ["gitlab-ci", "aucune"]
    assert valeurs("ci", [Choix("forge", "aucune")]) == ["aucune"]


def test_revenir_sur_une_reponse_la_corrige() -> None:
    """La dernière l'emporte : garder la première ignorerait la correction en silence."""
    corrige = [Choix("langages", "python"), Choix("langages", "typescript")]

    assert [o.valeur for o in options_admissibles(_du_catalogue("tests"), corrige)] == [
        "vitest",
        "jest",
    ]


def test_rien_ne_se_deduit_d_un_questionnaire_vide() -> None:
    assert deductions([]) == ()


def test_une_seule_option_restante_est_une_reponse_conclue_qui_porte_sa_cause() -> None:
    (conclue,) = deductions([Choix("langages", "python")])

    assert (conclue.cle, conclue.valeur) == ("tests", "pytest")
    assert conclue.deduit is True
    assert conclue.parce_que.startswith("Déduit de « Python »")


def test_un_langage_que_maestro_n_outille_pas_conclut_a_aucun_test_et_l_avoue() -> None:
    """« Aucun » n'est pas une recommandation, c'est un aveu — la phrase doit le dire."""
    (conclue,) = deductions([Choix("langages", "autre")])

    assert (conclue.cle, conclue.valeur) == ("tests", "aucun")
    assert "ne connaît pas de lanceur de tests" in conclue.parce_que
    assert "il écrira les instructions, pas le script" in conclue.parce_que


def test_sans_forge_la_question_de_la_ci_ne_se_pose_pas() -> None:
    (conclue,) = deductions([Choix("forge", "aucune")])

    assert (conclue.cle, conclue.valeur) == ("ci", "aucune")
    assert "rien n'hébergerait un pipeline" in conclue.parce_que


def test_les_deductions_sont_idempotentes() -> None:
    donnes = [Choix("langages", "python")]
    une_fois = deductions(donnes)

    assert une_fois != ()
    assert deductions([*donnes, *une_fois]) == ()


def test_deux_reponses_independantes_entrainent_chacune_la_leur() -> None:
    conclues = deductions([Choix("langages", "autre"), Choix("forge", "aucune")])

    assert [(c.cle, c.valeur) for c in conclues] == [("tests", "aucun"), ("ci", "aucune")]


# --- ③ Les recommandations, et le compteur ------------------------------------


def test_la_premiere_question_est_la_plus_large_et_ne_se_deduit_de_rien() -> None:
    question = question_suivante([])
    assert question is not None

    assert question.cle == "nature" and question.rang == 1
    assert question.recommande == "application-web"
    assert "rien ne la décide encore" in question.pourquoi.lower()


def test_la_recommandation_du_langage_depend_de_la_nature_et_la_nomme() -> None:
    question = question_suivante([Choix("nature", "service-api")])
    assert question is not None

    assert question.cle == "langages" and question.recommande == "python"
    assert question.pourquoi.startswith("Déduit de « un service ou une api » —")
    # La cause renvoie à ce que Maestro sait outiller, jamais à une statistique.
    assert "le langage du moteur de Maestro" in question.pourquoi


def test_une_nature_hors_catalogue_retombe_sur_le_defaut_et_le_dit() -> None:
    question = question_suivante([Choix("nature", "quelque-chose-a-part")])
    assert question is not None

    assert question.cle == "langages" and question.recommande == "typescript"
    assert question.pourquoi.startswith("Rien ne le décide encore")


def test_une_recommandation_intenable_est_ramenee_dans_les_options() -> None:
    """Sinon le bouton serait armé sur une valeur que la liste n'offre plus."""
    acquis = [
        Choix("nature", "service-api"),
        Choix("langages", "python"),
        Choix("forge", "gitlab"),
    ]
    question = question_suivante([*acquis, *deductions(acquis)])
    assert question is not None

    assert question.cle == "ci"
    assert [o.valeur for o in question.options] == ["gitlab-ci", "aucune"]
    assert question.recommande == "gitlab-ci"  # « github-actions » n'est plus offert
    assert question.pourquoi == (
        "Déduit de « GitLab » — c'est la CI que cette forge porte nativement."
    )


def test_le_compteur_ne_compte_que_les_questions_reellement_posees() -> None:
    """`tests` est conclu, donc jamais posé : la barre ne saute pas de cran pour autant."""
    donnes = [Choix("nature", "service-api"), Choix("langages", "python")]
    question = question_suivante([*donnes, *deductions(donnes)])
    assert question is not None

    assert question.cle == "forge"  # quatrième du catalogue…
    assert question.rang == 3  # …mais troisième question posée
    assert question.total == QUESTIONS_MAX


def test_le_questionnaire_converge_et_ne_depasse_jamais_son_plafond() -> None:
    """On répond toujours la recommandation : elle est toujours l'une des options."""
    acquis: list[Choix] = []
    posees: list[str] = []

    while (question := question_suivante(acquis)) is not None:
        posees.append(question.cle)
        assert question.rang == len(posees)
        assert question.rang <= QUESTIONS_MAX
        assert any(o.valeur == question.recommande for o in question.options)
        assert question.pourquoi  # une recommandation ne voyage jamais sans sa cause
        acquis.append(Choix(question.cle, question.recommande))
        acquis.extend(deductions(acquis))

    assert posees == ["nature", "langages", "tests", "forge", "ci", "conventions"]


def test_le_questionnaire_peut_finir_avant_le_plafond() -> None:
    acquis = _parcours_python()

    assert question_suivante(acquis) is None
    assert len([c for c in acquis if not c.deduit]) == 5 < QUESTIONS_MAX


def test_une_valeur_hors_catalogue_ou_rendue_intenable_est_refusee() -> None:
    assert valeur_admissible("nature", "application-web", []) is True
    assert valeur_admissible("nature", "quelque-chose-a-part", []) is False
    assert valeur_admissible("question-qui-n-existe-pas", "peu importe", []) is False
    assert valeur_admissible("tests", "pytest", [Choix("langages", "python")]) is True
    assert valeur_admissible("tests", "jest", [Choix("langages", "python")]) is False


def test_les_cles_connues_sont_celles_du_catalogue_dans_l_ordre() -> None:
    assert cles_connues() == ("nature", "langages", "tests", "forge", "ci", "conventions")
    assert cles_connues() == tuple(q.cle for q in CATALOGUE)
    assert len(cles_connues()) == QUESTIONS_MAX


# --- ④ Des réponses aux constats, puis à la recommandation du lot 2 -----------


def test_les_reponses_deviennent_les_constats_d_un_projet_neuf() -> None:
    constats = constats_depuis_choix(_parcours_python())

    (langage,) = constats.langages
    assert (langage.nom, langage.exemple) == ("Python", "pyproject.toml")
    assert (langage.fichiers, langage.part) == (0, 1.0)  # rien n'a été compté : rien n'existe
    (gestionnaire,) = constats.gestionnaires
    assert (gestionnaire.nom, gestionnaire.chemin) == ("uv", "pyproject.toml")
    assert gestionnaire.installer == "uv sync"
    assert [c.usage for c in constats.commandes] == [
        "installer",
        "tester",
        "lint",
        "formater",
        "demarrer",
    ]
    tester = constats.commande_de("tester")
    assert tester is not None and tester.commande == "pytest"
    demarrer = constats.commande_de("demarrer")
    assert demarrer is not None and demarrer.commande == "uv run python -m app"


def test_sur_un_projet_neuf_aucune_commande_n_est_declaree() -> None:
    """Le projet n'écrit rien nulle part : le marquer `declaree` ferait passer une
    réponse pour une lecture."""
    constats = constats_depuis_choix(_parcours_python())

    assert {c.origine for c in constats.commandes} == {"convention"}
    # Le chemin est l'endroit où la commande *vivra*, pas un fichier qu'on a ouvert.
    assert {c.chemin for c in constats.commandes} == {"pyproject.toml"}
    tester = constats.commande_de("tester")
    assert tester is not None and tester.extrait == "réponse « tests » = pytest"


def test_rien_n_est_reconnu_dans_un_projet_qui_n_existe_pas_encore() -> None:
    """C'est ce qui fait sortir toutes les entrées en « à générer »."""
    constats = constats_depuis_choix(_parcours_python())

    assert constats.outillage_present == ()
    assert constats.dossier_scripts.constate is False
    assert constats.dossier_scripts.chemin == "scripts"


def test_la_ci_la_forge_et_la_convention_portent_la_reponse_qui_les_a_decidees() -> None:
    constats = constats_depuis_choix(_parcours_python())

    (ci,) = constats.ci
    assert ci.chemin == ".github/workflows/ci.yml"
    assert ci.role == "workflow GitHub Actions — réponse « ci » = github-actions"
    assert constats.forge is not None and constats.forge.nom == "github"
    (convention,) = constats.conventions
    assert convention.chemin == "CONTRIBUTING.md"
    assert "réponse « conventions » = conventional-commits" in convention.role


def test_une_reponse_negative_ne_constate_rien() -> None:
    constats = constats_depuis_choix(
        [
            Choix("nature", "bibliotheque"),
            Choix("langages", "autre"),
            Choix("tests", "aucun"),
            Choix("forge", "aucune"),
            Choix("ci", "aucune"),
            Choix("conventions", "aucune"),
        ]
    )

    assert constats.langages == () and constats.gestionnaires == ()
    assert constats.commandes == ()
    assert constats.ci == () and constats.conventions == ()
    assert constats.forge is None


def test_une_question_sans_reponse_ne_justifie_aucun_fichier() -> None:
    constats = constats_depuis_choix([])

    assert constats.langages == () and constats.commandes == ()
    assert constats.forge is None and constats.ci == ()


def test_le_resume_ne_dit_que_ce_qui_a_ete_repondu() -> None:
    assert resume_des_choix([]) == "aucun choix encore donné"
    assert resume_des_choix(_parcours_python()) == (
        "Un service ou une API ; Python ; tests : pytest ; GitHub Actions"
    )
    assert resume_des_choix(
        [
            Choix("nature", "outil-cli"),
            Choix("langages", "go"),
            Choix("tests", "aucun"),  # un aveu ne figure pas au résumé
            Choix("ci", "aucune"),
        ]
    ) == "Un outil en ligne de commande ; Go ; aucune CI"


def test_la_provenance_du_manifeste_dit_d_ou_sort_l_outillage() -> None:
    choix = _parcours_python()

    source = source_manifeste_des_choix("prj-neuf", choix)

    assert source["type"] == SOURCE_CHOIX == "choix"
    assert source["projet_id"] == "prj-neuf"
    assert source["reference"] == " ; ".join(f"{c.cle}={c.valeur}" for c in choix)
    assert source["reference"].startswith("nature=service-api ; langages=python")
    assert source["resume"] == resume_des_choix(choix)


def test_les_reponses_rendent_la_recommandation_du_lot_2_sans_second_chemin() -> None:
    """Le critère du ticket : une seule fonction, deux façons d'en remplir l'entrée."""
    choix = _parcours_python()

    reco = recommandation_depuis_choix(choix)

    assert reco == recommander(constats_depuis_choix(choix))
    assert reco.entrees[0].type == "instructions" and reco.entrees[0].nom == "AGENTS.md"
    assert any(e.type == "skill" for e in reco.entrees)
    assert {e.etat for e in reco.entrees} == {"a-generer"}


def test_un_skill_sans_commande_est_ecarte_avec_sa_raison() -> None:
    reco = recommandation_depuis_choix(
        [Choix("nature", "bibliotheque"), Choix("langages", "autre")]
    )

    ecartes = [e for e in reco.ecartes if e.type == "skill"]
    assert ecartes
    assert all("aucune commande de" in e.raison for e in ecartes)


# --- ⑤ Le conducteur : le fil est la seule mémoire ----------------------------


def test_le_conducteur_ne_retient_rien() -> None:
    assert vars(ConducteurOutillage()) == {}


def test_ouvrir_pose_la_premiere_question_et_la_redit_en_toutes_lettres() -> None:
    reponse = asyncio.run(ConducteurOutillage().ouvrir([]))

    assert reponse.question is not None and reponse.question.cle == "nature"
    assert reponse.question.intitule in reponse.contenu
    assert "Je propose « Une application web » —" in reponse.contenu


def test_rouvrir_un_questionnaire_en_cours_le_reprend_ou_il_en_est() -> None:
    fil = [_geste("nature", "service-api")]

    reponse = asyncio.run(ConducteurOutillage().ouvrir(fil))

    assert reponse.question is not None and reponse.question.cle == "langages"
    # Deux ouvertures rendent la même chose : il n'y a pas de second questionnaire.
    assert asyncio.run(ConducteurOutillage().ouvrir(fil)) == reponse


def test_le_conducteur_lit_le_fil_puis_y_ajoute_ce_qui_en_decoule() -> None:
    fil = [_geste("nature", "service-api"), _geste("langages", "python")]

    acquis = ConducteurOutillage().acquis(fil)

    assert [(c.cle, c.valeur, c.deduit) for c in acquis] == [
        ("nature", "service-api", False),
        ("langages", "python", False),
        ("tests", "pytest", True),
    ]


def test_le_geste_dit_ce_qu_il_vient_d_entrainer_avant_de_poser_la_suite() -> None:
    fil = [_geste("nature", "service-api"), _geste("langages", "python")]

    reponse = asyncio.run(
        ConducteurOutillage().repondre(fil, _du_catalogue("langages"), "python")
    )

    assert reponse.contenu.startswith(
        "Du coup, une question ne se pose pas :\n— Déduit de « Python »"
    )
    assert reponse.question is not None and reponse.question.cle == "forge"
    assert reponse.question.intitule in reponse.contenu


def test_un_geste_qui_n_entraine_rien_ne_dit_rien() -> None:
    """Une phrase « aucune déduction » à chaque tour apprendrait à ne plus la lire."""
    fil = [_geste("nature", "service-api")]

    reponse = asyncio.run(
        ConducteurOutillage().repondre(fil, _du_catalogue("nature"), "service-api")
    )

    assert not reponse.contenu.startswith("Du coup")
    assert reponse.question is not None and reponse.question.cle == "langages"


def test_les_deductions_deja_dites_ne_se_redisent_pas_a_chaque_tour() -> None:
    fil = [_geste("langages", "autre"), _geste("forge", "aucune")]

    reponse = asyncio.run(ConducteurOutillage().repondre(fil, _du_catalogue("forge"), "aucune"))

    assert "rien n'hébergerait un pipeline" in reponse.contenu  # la nouvelle
    assert "lanceur de tests" not in reponse.contenu  # celle du tour d'avant


def test_la_phrase_des_deductions_se_tait_quand_il_n_y_a_rien_a_dire() -> None:
    """Appelée directement : les tables du catalogue ne produisent aujourd'hui qu'une
    déduction par geste, et le pluriel doit tenir le jour où elles en produiront deux."""
    assert _phrase_des_deductions([]) == ""
    assert _phrase_des_deductions([Choix("tests", "pytest", deduit=True)]) == ""
    assert _phrase_des_deductions(
        [Choix("tests", "pytest", deduit=True, parce_que="parce que Python")]
    ) == "Du coup, une question ne se pose pas :\n— parce que Python"
    assert _phrase_des_deductions(
        [
            Choix("tests", "aucun", deduit=True, parce_que="parce que ce langage"),
            Choix("ci", "aucune", deduit=True, parce_que="parce qu'aucune forge"),
        ]
    ) == (
        "Du coup, 2 questions ne se posent pas :\n"
        "— parce que ce langage\n— parce qu'aucune forge"
    )


def test_le_questionnaire_se_conclut_sur_l_outillage_qu_il_recommande() -> None:
    acquis = _parcours_python()
    fil = [_geste(c["cle"], c["valeur"]) for c in _donnes(acquis)]

    reponse = asyncio.run(ConducteurOutillage().ouvrir(fil))

    assert reponse.question is None  # plus rien à demander
    attendu = f"C'est tout ce qu'il me fallait — {resume_des_choix(acquis)}."
    assert reponse.contenu.startswith(attendu)
    reco = recommandation_depuis_choix(acquis)
    assert f"{len(reco.entrees)} entrée(s)" in reponse.contenu
    assert "Rien n'est écrit dans le projet tant que vous ne l'avez pas validé." in reponse.contenu


def test_la_conclusion_dit_ou_se_donne_la_validation_qu_elle_promet() -> None:
    """La phrase de conclusion ne promet rien que l'écran ne permette (#1104).

    « Rien n'est écrit tant que vous ne l'avez pas validé » était vraie et pourtant
    trompeuse : aucune surface n'offrait de quoi valider, le pied du fil redevenant
    vide dès que le dernier message ne portait plus de question. Le geste existe
    désormais au pied de la conversation, et la promesse dit **où** il est — sans
    quoi elle renvoie à un endroit que personne ne trouve.
    """
    fil = [_geste(c["cle"], c["valeur"]) for c in _donnes(_parcours_python())]

    reponse = asyncio.run(ConducteurOutillage().ouvrir(fil))

    assert reponse.question is None
    assert "au pied de cette conversation" in reponse.contenu
    # Le geste est nommé **après** la promesse : on dit d'abord que rien n'est
    # écrit, puis où l'on décide que ça le soit.
    assert reponse.contenu.index(
        "pas validé."
    ) < reponse.contenu.index("au pied de cette conversation")


# --- ⑥ Les routes : la voie du fil, et la voie sans état ----------------------


class _RepondeurMuet(RepondeurChat):
    """Un répondeur qui ne conduit aucun questionnaire — le cas du `409`."""

    async def repondre(self, agent: object, fil: object) -> str:
        return "je ne pose pas de question."


@pytest.fixture()
def client_chat(tmp_path: Path) -> Iterator[TestClient]:
    """L'app sur un fil temporaire, répondeur scripté (zéro modèle)."""
    app = create_app(
        bus=InMemoryEventBus(),
        chat_store=ChatStore(tmp_path / "chat"),
        chat_repondeur=RepondeurScripte(),
    )
    with TestClient(app) as client:
        yield client


def test_la_route_ouvre_le_questionnaire_dans_le_fil(client_chat: TestClient) -> None:
    reponse = client_chat.post("/api/chat/qa/outillage/questionnaire")

    assert reponse.status_code == 201, reponse.text
    corps = reponse.json()
    assert corps["agent"] == "qa" and corps["conversation"]
    (message,) = corps["messages"]  # aucun message d'utilisateur : personne n'a demandé
    assert message["question"]["cle"] == "nature"
    assert message["choix"] is None


def test_rouvrir_la_route_repose_la_question_la_ou_le_fil_en_est(
    client_chat: TestClient,
) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")
    client_chat.post("/api/chat/qa/outillage", json={"valeur": "service-api"})

    encore = client_chat.post("/api/chat/qa/outillage/questionnaire")

    assert encore.status_code == 201, encore.text
    (message,) = encore.json()["messages"]
    assert message["question"]["cle"] == "langages"  # et non un second questionnaire


def test_le_geste_ecrit_la_reponse_au_fil_puis_enchaine(client_chat: TestClient) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "service-api"})

    assert reponse.status_code == 201, reponse.text
    geste, suite = reponse.json()["messages"]
    assert geste["contenu"] == "Quelle sorte de projet est-ce ? → Un service ou une API"
    assert geste["choix"] == {
        "cle": "nature",
        "valeur": "service-api",
        "deduit": False,
        "parce_que": "",
    }
    assert suite["question"]["cle"] == "langages"
    assert suite["question"]["recommande"] == "python"


def test_une_valeur_hors_des_options_posees_est_refusee(client_chat: TestClient) -> None:
    client_chat.post("/api/chat/qa/outillage/questionnaire")

    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "quelque-chose-a-part"})

    assert reponse.status_code == 422, reponse.text
    assert "hors des options posées" in reponse.json()["detail"]


def test_un_geste_sans_question_en_attente_est_un_conflit(client_chat: TestClient) -> None:
    reponse = client_chat.post("/api/chat/qa/outillage", json={"valeur": "service-api"})

    assert reponse.status_code == 409, reponse.text
    assert "aucune question d'outillage en attente" in reponse.json()["detail"]


def test_le_questionnaire_se_deroule_dans_le_fil_jusqu_a_sa_conclusion(
    client_chat: TestClient,
) -> None:
    ouverture = client_chat.post("/api/chat/qa/outillage/questionnaire")
    question = ouverture.json()["messages"][0]["question"]
    posees: list[str] = []
    suite: dict[str, object] = {}

    while question is not None:
        posees.append(question["cle"])
        assert len(posees) <= QUESTIONS_MAX
        reponse = client_chat.post(
            "/api/chat/qa/outillage", json={"valeur": question["recommande"]}
        )
        assert reponse.status_code == 201, reponse.text
        suite = reponse.json()["messages"][1]
        question = suite["question"]

    assert posees == ["nature", "langages", "tests", "forge", "ci", "conventions"]
    assert str(suite["contenu"]).startswith("C'est tout ce qu'il me fallait — ")
    # Plus rien n'attend : un geste tardif tombe sur le conflit.
    tardif = client_chat.post("/api/chat/qa/outillage", json={"valeur": "github"})
    assert tardif.status_code == 409, tardif.text


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
def client_projets(tmp_path: Path, atelier: Path) -> Iterator[TestClient]:
    """L'app avec un service des projets borné à l'atelier."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
    )
    with TestClient(app) as client:
        yield client


def _projet_neuf(client: TestClient, atelier: Path) -> str:
    """Un projet déclaré sur un dossier vide — l'identifiant est tout ce qu'on en veut."""
    racine = atelier / "neuf"
    racine.mkdir(exist_ok=True)
    reponse = client.post("/api/projets", json={"nom": "Neuf", "racine": str(racine)})
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def test_la_voie_sans_etat_rend_la_premiere_question(
    client_projets: TestClient, atelier: Path
) -> None:
    projet = _projet_neuf(client_projets, atelier)

    reponse = client_projets.post(
        f"/api/projets/{projet}/outillage/questionnaire", json={"choix": []}
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["question"]["cle"] == "nature"
    assert corps["deductions"] == []
    assert corps["terminee"] is False


def test_les_reponses_acquises_decident_de_la_suite_et_de_ce_qui_se_deduit(
    client_projets: TestClient, atelier: Path
) -> None:
    projet = _projet_neuf(client_projets, atelier)

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/questionnaire",
        json={
            "choix": [
                {"cle": "nature", "valeur": "service-api"},
                {"cle": "langages", "valeur": "python"},
            ]
        },
    ).json()

    assert corps["question"]["cle"] == "forge" and corps["question"]["rang"] == 3
    assert [(d["cle"], d["valeur"], d["deduit"]) for d in corps["deductions"]] == [
        ("tests", "pytest", True)
    ]
    assert corps["terminee"] is False


def test_deduit_et_parce_que_envoyes_par_le_client_ne_sont_pas_crus(
    client_projets: TestClient, atelier: Path
) -> None:
    """`deductions` les recalcule : un client n'a jamais à les tenir à jour."""
    projet = _projet_neuf(client_projets, atelier)

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/questionnaire",
        json={
            "choix": [
                {
                    "cle": "langages",
                    "valeur": "python",
                    "deduit": True,
                    "parce_que": "une cause inventée",
                }
            ]
        },
    ).json()

    (deduite,) = corps["deductions"]
    assert (deduite["cle"], deduite["valeur"]) == ("tests", "pytest")
    assert deduite["parce_que"].startswith("Déduit de « Python »")
    # La réponse envoyée compte comme **donnée** : la nature reste à poser, en rang 2.
    assert corps["question"]["cle"] == "nature" and corps["question"]["rang"] == 2


def test_un_questionnaire_termine_n_a_plus_de_question(
    client_projets: TestClient, atelier: Path
) -> None:
    projet = _projet_neuf(client_projets, atelier)

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/questionnaire",
        json={"choix": _donnes(_parcours_python())},
    ).json()

    assert corps["question"] is None
    assert corps["terminee"] is True


def test_la_recommandation_servie_est_celle_du_domaine(
    client_projets: TestClient, atelier: Path
) -> None:
    """Le second critère du ticket, vérifié contre la fonction elle-même."""
    projet = _projet_neuf(client_projets, atelier)
    acquis = _parcours_python()

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/recommandation",
        json={"choix": _donnes(acquis)},
    ).json()

    assert corps["projet_id"] == projet
    assert corps["source"] == source_manifeste_des_choix(projet, acquis)
    assert corps["source"]["type"] == "choix"
    assert [c["cle"] for c in corps["choix"]] == [c.cle for c in acquis]  # déductions comprises
    assert corps["recommandation"] == recommandation_depuis_choix(acquis).to_dict()


def test_la_recommandation_se_sert_avant_la_derniere_question(
    client_projets: TestClient, atelier: Path
) -> None:
    """Un client qui montre ce qui se dessine n'a pas à attendre la fin."""
    projet = _projet_neuf(client_projets, atelier)

    corps = client_projets.post(
        f"/api/projets/{projet}/outillage/recommandation",
        json={"choix": [{"cle": "nature", "valeur": "service-api"}]},
    ).json()

    assert corps["recommandation"]["entrees"]
    assert corps["recommandation"]["ecartes"]  # ce qui n'a pas été répondu est écarté, motivé


def test_un_projet_inconnu_est_refuse_par_les_deux_routes(client_projets: TestClient) -> None:
    for route in ("questionnaire", "recommandation"):
        reponse = client_projets.post(
            f"/api/projets/prj-fantome/outillage/{route}", json={"choix": []}
        )
        assert reponse.status_code == 404, reponse.text
        assert reponse.json()["detail"]["motif"] == "projet-inconnu"
