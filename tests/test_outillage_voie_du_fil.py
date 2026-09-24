"""La **voie du fil** mène à l'écriture, comme celle du parcours de création (#1104).

Réserve R7 du bouclage de « L'équipe sur mesure », verdict du 2026-09-21. Le
questionnaire d'outillage d'un projet neuf a deux voies, et une seule était
éprouvée jusqu'au disque :

- **l'étape du parcours de création** (#1034) écrit depuis #1100, et
  `tests/test_projet_outille_http.py` le prouve en jouant la voie **sans état**
  (`POST /api/projets/{id}/outillage/questionnaire`) puis la génération ;
- **le fil de conversation** (#1031) conduisait le même questionnaire, le
  concluait sur « rien n'est écrit tant que vous ne l'avez pas validé »… et
  personne n'avait jamais suivi ce qu'il devenait. Le premier bouclage notait que
  cette voie « conclut, elle aussi, sans rien écrire » ; #1100 n'a pas repris le
  constat, et le rebouclage ne l'a pas rejouée.

Cette suite la **joue**, de bout en bout et par HTTP : on ouvre le questionnaire
dans le fil, on répond au geste comme l'écran le fait (`POST /api/chat/{agent}/outillage`),
on relit les réponses **là où elles vivent** — le champ `choix` des messages, seule
mémoire du canal —, et on les porte à la génération avec le corps de l'étape de
création (`{retenus, choix}`). Puis on regarde le **disque**.

Trois propriétés, et la troisième est celle que la réserve nomme :

① le fil conclut, et ce qu'il a recueilli se relit **structurellement** sur ses
   messages — jamais dans le texte d'une phrase ;
② la conclusion **dit où** se donne la validation qu'elle promet : une promesse
   sans surface est le défaut qu'on corrige ;
③ les réponses du fil écrivent **le même outillage** que celles de l'écran de
   création. Deux voies, un seul résultat — sans quoi la voie qu'on emprunte
   déciderait de ce qu'on reçoit.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import ChatStore
from maestro.controltower.outillage import ComprehensionModele
from maestro.controltower.projets import ServiceProjets
from maestro.outillage import CHEMIN_MANIFESTE
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

#: Le fil de l'orchestration : le seul qui conduise un questionnaire d'outillage.
FIL = "orchestrateur"

#: Ce qu'un modèle comprend d'une vitrine web, selon ce qui a déjà été répondu.
_CONSTATS = [
    {"cle": "nature", "valeur": "Un site vitrine", "parce_que": "vous l'avez décrit ainsi"},
    {"cle": "langages", "valeur": "TypeScript", "parce_que": "un site Astro"},
    {"cle": "manifeste", "valeur": "package.json", "parce_que": "le manifeste de npm"},
    {"cle": "gestionnaire", "valeur": "npm", "parce_que": "le gestionnaire d'Astro"},
    {"cle": "installer", "valeur": "npm ci", "parce_que": "installe les dépendances"},
    {"cle": "tester", "valeur": "npx vitest run", "parce_que": "le lanceur d'Astro"},
    {"cle": "demarrer", "valeur": "npm run dev", "parce_que": "le serveur de dev d'Astro"},
]
_FORGE = {
    "cle": "forge",
    "intitule": "Où le code vivra-t-il ?",
    "options": [
        {"valeur": "github", "libelle": "GitHub", "raison": "Pull Requests et Actions."},
        {"valeur": "aucun", "libelle": "Nulle part", "raison": "Le projet reste local."},
    ],
    "recommande": "github",
    "pourquoi": "Vous avez parlé de le publier : il lui faut une forge.",
}


class _Comprend(ModelProvider):
    """Un faux modèle qui comprend **selon ce qui a été répondu** — les deux voies d'accord.

    La réponse dépend du prompt, jamais de l'ordre des appels : le fil et l'étape de
    création posent la même question au même point, et c'est ce que ③ compare.
    """

    name = "faux-comprend"

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
        if "- forge (" not in prompt:
            return json.dumps({"constats": _CONSTATS, "questions": [_FORGE]})
        forge = {"cle": "forge", "valeur": "github", "parce_que": "votre réponse"}
        return json.dumps({"constats": [*_CONSTATS, forge], "questions": []})


@pytest.fixture()
def atelier(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Le dossier où naissent les projets, sous un dossier utilisateur factice.

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
def client(tmp_path: Path, atelier: Path) -> Iterator[TestClient]:
    """L'app réelle : fil persisté dans `tmp_path`, projets bornés à l'atelier.

    Depuis #1147 le questionnaire **comprend** par le modèle : un faux fournisseur
    le remplace, injecté une fois pour les deux voies (`create_app(comprehension=…)`)
    — le répondeur de l'orchestration ne consulte toujours pas son juge pour la
    suite d'un questionnaire (`ouvrir_questionnaire`, `repondre_question`).
    """
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        chat_store=ChatStore(tmp_path / "chat"),
        projets=ServiceProjets(
            ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)
        ),
        comprehension=ComprehensionModele(_Comprend()),
    )
    with TestClient(app) as client:
        yield client


def _projet_neuf(client: TestClient, atelier: Path) -> tuple[str, Path]:
    """Un projet déclaré `nouveau` : le dossier naît vide, et non versionné."""
    racine = atelier / "vitrine"
    reponse = client.post(
        "/api/projets",
        json={"nom": racine.name, "racine": str(racine), "origine": "nouveau"},
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"]), racine


def _repondre_dans_le_fil(client: TestClient) -> str:
    """Le questionnaire joué **dans le fil**, jusqu'à sa conclusion ; rend son texte.

    On répond ce que Maestro propose — le geste par défaut de la carte (« Garder
    ce choix ») —, et avec ses mots quand la question n'a pas d'options (la
    première, « Qu'est-ce que ce projet ? », #1147). Ce que ce test garde est le
    **chemin**, pas les questions, qui ont leurs propres suites.
    """
    ouverture = client.post(f"/api/chat/{FIL}/outillage/questionnaire")
    assert ouverture.status_code == 201, ouverture.text
    (message,) = ouverture.json()["messages"]
    # Une borne franche plutôt qu'un `while` : un questionnaire qui ne se
    # conclurait pas ferait tourner la suite sans fin au lieu d'échouer.
    for _ in range(20):
        question = message.get("question")
        if question is None:
            return str(message["contenu"])
        corps = (
            {"valeur": question["recommande"]}
            if question["options"]
            else {"valeur": "Un site vitrine pour un club de padel", "libre": True}
        )
        geste = client.post(f"/api/chat/{FIL}/outillage", json=corps)
        assert geste.status_code == 201, geste.text
        _, message = geste.json()["messages"]
    raise AssertionError("le questionnaire du fil ne s'est pas conclu")


def _choix_du_fil(client: TestClient) -> list[dict[str, Any]]:
    """Les réponses et ce qui en a été compris — ce que l'écran relit (`choixAValider`).

    Lues sur les champs `choix` et `comprehension` des messages et **jamais dans
    leur texte** : c'est la règle du canal (#685). Depuis #1147 la conclusion
    porte ce que le modèle a compris, et c'est **elle** qui part à la génération —
    sans rappeler le modèle, donc sans risquer qu'il comprenne autre chose.
    """
    fil = client.get(f"/api/chat/{FIL}").json()["messages"]
    donnees = [m["choix"] for m in fil if m.get("choix")]
    compris = next((m["comprehension"] for m in reversed(fil) if m.get("comprehension")), [])
    return [*donnees, *compris]


def _manifeste(racine: Path) -> dict[str, Any]:
    return json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


# --- ① Le fil conclut, et ses réponses se relisent ------------------------------


def test_le_fil_conduit_le_questionnaire_et_porte_ses_reponses(
    client: TestClient, atelier: Path
) -> None:
    _projet_neuf(client, atelier)

    conclusion = _repondre_dans_le_fil(client)

    choix = _choix_du_fil(client)
    assert choix, "le fil doit porter les réponses données au geste"
    assert all(c["cle"] and c["valeur"] for c in choix)
    assert "L'outillage recommandé" in conclusion


# --- ② La promesse de la conclusion dit où se donne la validation ---------------


def test_la_conclusion_du_fil_dit_ou_se_valide_ce_qu_elle_annonce(
    client: TestClient, atelier: Path
) -> None:
    """Le défaut que la réserve nomme : une promesse sans surface.

    « Rien n'est écrit dans le projet tant que vous ne l'avez pas validé » était
    vraie et pourtant trompeuse — le pied du fil redevenait vide dès que le
    dernier message ne portait plus de question, et rien ne validait nulle part.
    """
    _projet_neuf(client, atelier)

    conclusion = _repondre_dans_le_fil(client)

    assert "Rien n'est écrit dans le projet tant que vous ne l'avez pas validé." in conclusion
    assert "au pied de cette conversation" in conclusion


# --- ③ Les réponses du fil écrivent, et écrivent la même chose ------------------


def test_les_reponses_du_fil_ecrivent_l_outillage_du_projet(
    client: TestClient, atelier: Path
) -> None:
    """La réserve R7, tenue : « choisi dans le fil, puis écrit dans le dossier ».

    Le corps est **celui de l'étape de création** — `{retenus, choix}` sur la même
    route : une seule route écrit l'outillage d'un projet, et elle ne sait pas de
    quelle surface viennent les réponses.
    """
    projet, racine = _projet_neuf(client, atelier)
    _repondre_dans_le_fil(client)
    choix = _choix_du_fil(client)

    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    )
    assert reco.status_code == 200, reco.text
    retenus = [e["chemin"] for e in reco.json()["recommandation"]["entrees"]]

    generation = client.post(
        f"/api/projets/{projet}/outillage/generation",
        json={"retenus": retenus, "choix": choix},
    )

    assert generation.status_code == 200, generation.text
    corps = generation.json()
    # Le disque, jamais la seule réponse : c'est ce que la réserve demandait de
    # regarder, et c'est là que le défaut de #1100 se voyait.
    assert set(corps["rapport"]["ecrits"]) == set(retenus)
    assert retenus, "des réponses doivent recommander quelque chose"
    for chemin in retenus:
        assert (racine / chemin).exists(), f"{chemin} n'a pas été écrit"
    # Rien n'a été perdu en route, et l'outillage dit d'où il vient.
    assert corps["retenus_inconnus"] == []
    assert _manifeste(racine)["source"]["type"] == "choix"


def test_le_fil_et_l_ecran_de_creation_ecrivent_le_meme_outillage(
    client: TestClient, atelier: Path
) -> None:
    """Deux voies, un seul résultat — sinon la voie empruntée déciderait du reçu.

    La voie **sans état** est celle du parcours de création (#1034) : le client dit
    ce qu'il a, l'API dit ce qui en découle. La voie du **fil** tient les mêmes
    réponses sur ses messages. Les deux passent par la même dérivation, et c'est ce
    qui doit se vérifier sur la recommandation servie, entrée pour entrée.
    """
    projet, _ = _projet_neuf(client, atelier)
    _repondre_dans_le_fil(client)
    choix = _choix_du_fil(client)

    # Les mêmes réponses, rejouées par la voie sans état : ce que l'étape de
    # création accumule à l'écran — les réponses données, puis ce qui en a été
    # compris au dernier tour.
    donnees: list[dict[str, Any]] = []
    for c in (c for c in choix if not c["deduit"]):
        etape = client.post(
            f"/api/projets/{projet}/outillage/questionnaire", json={"choix": donnees}
        ).json()
        assert etape["question"] is not None, "le fil a posé plus de questions"
        assert etape["question"]["cle"] == c["cle"]
        donnees = [*donnees, {"cle": c["cle"], "valeur": c["valeur"], "libre": c["libre"]}]
    fin = client.post(
        f"/api/projets/{projet}/outillage/questionnaire", json={"choix": donnees}
    ).json()
    assert fin["terminee"]
    acquis = [*donnees, *fin["deductions"]]

    par_le_fil = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]
    par_l_ecran = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": acquis}
    ).json()["recommandation"]

    assert par_le_fil == par_l_ecran
