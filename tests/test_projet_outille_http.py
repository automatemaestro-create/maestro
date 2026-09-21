"""Les routes du jalon « équipe sur mesure », jouées par HTTP — l'appel exact de l'écran (#1100).

Cinq routes de ce jalon n'avaient **aucun test HTTP** : l'analyse, la génération,
le report, la proposition d'équipe et la création d'équipe. Leur logique n'était
éprouvée qu'au niveau des fonctions Python — et c'est entre la route et la
fonction que le défaut de #1100 vivait. La génération d'un projet **neuf**
dérivait son outillage de l'analyse d'une racine vide, pas des réponses que
l'écran venait de donner. Les quatre skills recommandés n'étaient pas écrits, le
rapport rendait `retires: []`, et `AGENTS.md` contredisait les réponses (« ni
langage dominant, ni gestionnaire de paquets »). Chaque fonction, testée seule,
faisait pourtant ce qu'elle disait.

D'où la règle de cette suite : **on appelle ce que l'écran appelle, avec le corps
qu'il envoie**, puis on regarde le **disque** — jamais seulement la réponse :

① **l'analyse** d'un projet existant ne lit que la racine déclarée et n'écrit rien ;
② **la génération** d'un projet existant écrit ce que son analyse recommande, et
   celle d'un projet **neuf** écrit ce que **ses réponses** recommandent — skills sur
   le disque, `AGENTS.md` fidèle, manifeste qui dit `source.type: "choix"` ;
③ un chemin retenu qui ne désigne rien est **nommé** dans le rapport, jamais perdu ;
④ **le report** ne touche pas au dossier, se répète sans effet et se tait une fois
   l'outillage généré ;
⑤ **la proposition d'équipe** ne crée rien et branche les skills des réponses ;
⑥ **la création d'équipe** écrit ce qui a été montré, dans le projet, ou rien.

Ni réseau ni modèle : les playbooks d'équipe passent par un générateur « hors
ligne », qui fait retomber chaque rôle sur le playbook de son gabarit — le repli
que la route promet elle-même.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.generation_agent import GenerateurDefinitionAgent
from maestro.controltower.projets import ServiceProjets
from maestro.equipe.modele import ORIGINE_PLAYBOOK_GABARIT
from maestro.outillage import CHEMIN_MANIFESTE
from maestro.projets import ProjetStore

#: Les réponses du bouclage du 2026-09-21 (#1100), telles que l'écran les a données.
REPONSES_DU_BILAN: dict[str, str] = {
    "nature": "application-web",
    "langages": "typescript",
    "tests": "vitest",
    "forge": "github",
    "ci": "github-actions",
    "conventions": "conventional-commits",
}


class _GenerateurHorsLigne(GenerateurDefinitionAgent):
    """Un générateur de playbooks qui ne répond jamais — zéro appel modèle.

    La route rattrape toute panne et fait retomber le rôle sur le playbook de son
    gabarit : c'est donc aussi le chemin nominal d'un poste sans quota.
    """

    async def proposer(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("hors ligne (test)")


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
def gabarits(tmp_path: Path) -> ConfigurationAgents:
    """Les six dépôts d'agents, montés dans `tmp_path` — jamais sous `core/` du dépôt."""
    racine = tmp_path / "core"
    return ConfigurationAgents(
        agents=AgentStore(racine / "agents"),
        surcharges=SurchargeStore(racine / "surcharges"),
        playbooks=PlaybookStore(racine / "playbooks"),
        permissions=PermissionStore(racine / "permissions"),
        mcp=McpStore(racine / "mcp"),
        capacites=CapacityStore(racine / "capacite"),
    )


@pytest.fixture()
def client(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> Iterator[TestClient]:
    """L'app réelle, projets bornés à l'atelier, dépôts d'agents temporaires."""
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(
            ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)
        ),
        agents_store=gabarits.agents,
        surcharges=gabarits.surcharges,
        playbooks=gabarits.playbooks,
        permissions=gabarits.permissions,
        mcp=gabarits.mcp,
        capacites=gabarits.capacites,
        generateur_agent=_GenerateurHorsLigne(),
    )
    with TestClient(app) as client:
        yield client


def _declarer(client: TestClient, racine: Path, *, origine: str) -> str:
    """Déclare un projet comme l'écran de création — l'identifiant est tout ce qu'on en veut."""
    reponse = client.post(
        "/api/projets", json={"nom": racine.name, "racine": str(racine), "origine": origine}
    )
    assert reponse.status_code == 201, reponse.text
    return str(reponse.json()["id"])


def _projet_existant(client: TestClient, atelier: Path) -> tuple[str, Path]:
    """Un petit projet Python réel, non versionné — ce qu'on importe pour l'outiller."""
    racine = atelier / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print('salut')\n", encoding="utf-8")
    (racine / "src" / "modele.py").write_text("X = 1\n", encoding="utf-8")
    (racine / "pyproject.toml").write_text(
        '[project]\nname = "depensio"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (racine / "README.md").write_text("# Dépensio\n", encoding="utf-8")
    return _declarer(client, racine, origine="existant"), racine


def _projet_neuf(client: TestClient, atelier: Path) -> tuple[str, Path]:
    """Un projet déclaré `nouveau` : le dossier naît vide."""
    racine = atelier / "vitrine"
    return _declarer(client, racine, origine="nouveau"), racine


def _repondre_au_questionnaire(client: TestClient, projet: str) -> list[dict[str, Any]]:
    """Le questionnaire joué comme l'écran : une question, sa réponse, la suite.

    Les réponses acquises repartent **entières** à chaque appel (le questionnaire
    est sans état côté serveur), déductions comprises — c'est le `tous` que
    `EtapeOutillage` tient et renvoie à la génération.
    """
    acquis: list[dict[str, Any]] = []
    for _ in range(len(REPONSES_DU_BILAN) + 1):
        etape = client.post(
            f"/api/projets/{projet}/outillage/questionnaire", json={"choix": acquis}
        )
        assert etape.status_code == 200, etape.text
        corps = etape.json()
        acquis = [*acquis, *corps["deductions"]]
        if corps["terminee"]:
            return acquis
        cle = corps["question"]["cle"]
        acquis.append({"cle": cle, "valeur": REPONSES_DU_BILAN[cle]})
    raise AssertionError("le questionnaire ne s'est pas conclu")


def _manifeste(racine: Path) -> dict[str, Any]:
    return json.loads((racine / CHEMIN_MANIFESTE).read_text(encoding="utf-8"))


def _fichiers(racine: Path) -> set[str]:
    """Tout ce que la racine porte, en chemins relatifs — pour prouver qu'on n'y a rien écrit."""
    return {p.relative_to(racine).as_posix() for p in racine.rglob("*")}


# --- ① L'analyse ---------------------------------------------------------------


def test_l_analyse_rend_les_constats_et_la_recommandation_sans_rien_ecrire(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_existant(client, atelier)
    avant = _fichiers(racine)

    reponse = client.get(f"/api/projets/{projet}/outillage/analyse")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["projet_id"] == projet
    assert "Python" in {langage["nom"] for langage in corps["constats"]["langages"]}
    assert corps["recommandation"]["entrees"], "une analyse de projet réel recommande"
    assert all(e["raison"] for e in corps["recommandation"]["entrees"])
    assert _fichiers(racine) == avant


def test_l_analyse_d_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.get("/api/projets/prj-00000000/outillage/analyse")

    assert reponse.status_code == 404, reponse.text


# --- ② La génération : l'existant par son analyse, le neuf par ses réponses ----


def test_la_generation_d_un_projet_existant_ecrit_ce_que_son_analyse_recommande(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_existant(client, atelier)
    analyse = client.get(f"/api/projets/{projet}/outillage/analyse").json()
    retenus = [
        e["chemin"] for e in analyse["recommandation"]["entrees"] if e["etat"] != "deja-present"
    ]

    reponse = client.post(
        f"/api/projets/{projet}/outillage/generation", json={"retenus": retenus}
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["regime"] == "en-place"
    assert corps["retenus_inconnus"] == []
    assert set(retenus) <= set(corps["rapport"]["ecrits"])
    for chemin in retenus:
        assert (racine / chemin).is_file(), chemin
    # La provenance dit **quelle analyse** : celle que la génération a faite.
    assert corps["source"]["type"] == "analyse"
    assert _manifeste(racine)["source"]["reference"] == corps["analyse"] != ""
    # Une analyse dit sa provenance ligne par ligne : pas de ligne d'origine.
    assert "**Origine**" not in (racine / "AGENTS.md").read_text(encoding="utf-8")


def test_la_generation_d_un_projet_neuf_ecrit_l_outillage_de_ses_reponses(
    client: TestClient, atelier: Path
) -> None:
    """Le critère C2 du jalon, rejoué par l'appel exact de l'écran (#1100)."""
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]
    retenus = [e["chemin"] for e in reco["entrees"]]
    skills = [e for e in reco["entrees"] if e["type"] == "skill"]
    assert len(skills) == 4, "les réponses du bilan recommandent quatre skills"

    reponse = client.post(
        f"/api/projets/{projet}/outillage/generation",
        json={"retenus": retenus, "choix": choix},
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["retenus_inconnus"] == []
    assert set(corps["rapport"]["ecrits"]) == set(retenus)
    for skill in skills:
        assert (racine / skill["chemin"]).is_file(), skill["chemin"]
    tests = (racine / next(s["chemin"] for s in skills if s["nom"] == "lancer-les-tests"))
    assert "npx vitest run" in tests.read_text(encoding="utf-8")


def test_l_agents_md_d_un_projet_neuf_dit_ses_reponses_et_ses_skills(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]

    client.post(
        f"/api/projets/{projet}/outillage/generation",
        json={"retenus": [e["chemin"] for e in reco["entrees"]], "choix": choix},
    )

    agents_md = (racine / "AGENTS.md").read_text(encoding="utf-8")
    # D'où vient l'outillage : des réponses, et le texte ne les fait pas passer pour
    # une lecture d'un dossier où `package.json` n'existe pas encore.
    assert "**Origine** : cet outillage vient des réponses données à Maestro" in agents_md
    assert "TypeScript" in agents_md
    assert "`npx vitest run`" in agents_md
    assert "`npm run dev`" in agents_md
    assert "lancer-les-tests" in agents_md
    # Les deux phrases que le bilan a relevées dans l'`AGENTS.md` écrit à tort.
    assert "ni langage dominant" not in agents_md
    assert "n'a pas encore de skill" not in agents_md


def test_le_manifeste_d_un_projet_neuf_dit_que_l_outillage_vient_des_reponses(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)

    corps = client.post(
        f"/api/projets/{projet}/outillage/generation", json={"choix": choix}
    ).json()

    source = _manifeste(racine)["source"]
    assert source["type"] == "choix"
    assert source["projet_id"] == projet
    assert "langages=typescript" in source["reference"]
    assert "tests=vitest" in source["reference"]
    # La réponse le redit, et elle n'invente pas une analyse qui n'a pas eu lieu.
    assert corps["source"] == source
    assert corps["analyse"] == ""


def test_la_generation_rederive_des_reponses_ce_que_la_recommandation_a_montre(
    client: TestClient, atelier: Path
) -> None:
    """Un seul chemin de dérivation : ce qui s'écrit est ce que l'écran a lu, entrée pour entrée."""
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]

    corps = client.post(
        f"/api/projets/{projet}/outillage/generation", json={"choix": choix}
    ).json()

    assert set(corps["rapport"]["ecrits"]) == {e["chemin"] for e in reco["entrees"]}
    declares = {entree["chemin"] for entree in _manifeste(racine)["entrees"]}
    assert declares == {e["chemin"] for e in reco["entrees"]}


def test_regenerer_avec_les_memes_reponses_ne_reecrit_rien(
    client: TestClient, atelier: Path
) -> None:
    """La ligne d'origine est stable pour des réponses inchangées : rien ne bouge."""
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    client.post(f"/api/projets/{projet}/outillage/generation", json={"choix": choix})
    agents_md = (racine / "AGENTS.md").read_text(encoding="utf-8")

    corps = client.post(
        f"/api/projets/{projet}/outillage/generation", json={"choix": choix}
    ).json()

    assert corps["rapport"]["ecrits"] == []
    assert corps["rapport"]["refuses"] == [] and corps["rapport"]["retires"] == []
    assert (racine / "AGENTS.md").read_text(encoding="utf-8") == agents_md


# --- ③ Un chemin retenu qui ne désigne rien est nommé --------------------------


def test_un_chemin_retenu_inconnu_est_nomme_dans_le_rapport(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_existant(client, atelier)

    corps = client.post(
        f"/api/projets/{projet}/outillage/generation",
        json={"retenus": ["AGENTS.md", "nulle/part.md", "nulle/part.md"]},
    ).json()

    assert corps["retenus_inconnus"] == ["nulle/part.md"]
    assert "AGENTS.md" in corps["rapport"]["ecrits"]
    assert not (racine / "nulle").exists()


def test_generer_un_projet_neuf_sans_ses_reponses_nomme_les_skills_non_ecrits(
    client: TestClient, atelier: Path
) -> None:
    """L'appel d'avant #1100, rejoué : il n'écrit toujours pas les skills — mais il le dit.

    Sans réponses, la génération n'a que l'analyse d'une racine vide pour
    dériver ; les chemins que l'écran avait lus n'y correspondent pas. C'est la
    perte silencieuse du bilan, désormais nommée.
    """
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]
    skills = [e["chemin"] for e in reco["entrees"] if e["type"] == "skill"]

    corps = client.post(
        f"/api/projets/{projet}/outillage/generation",
        json={"retenus": [e["chemin"] for e in reco["entrees"]]},
    ).json()

    assert set(skills) <= set(corps["retenus_inconnus"])
    for chemin in skills:
        assert not (racine / chemin).exists()


def test_une_generation_sans_corps_ne_nomme_aucun_inconnu(
    client: TestClient, atelier: Path
) -> None:
    projet, _ = _projet_existant(client, atelier)

    reponse = client.post(f"/api/projets/{projet}/outillage/generation")

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["retenus_inconnus"] == []


def test_la_generation_d_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.post("/api/projets/prj-00000000/outillage/generation", json={})

    assert reponse.status_code == 404, reponse.text


# --- ④ Le report ---------------------------------------------------------------


def test_le_report_date_la_decision_sans_toucher_au_dossier(
    client: TestClient, atelier: Path
) -> None:
    projet, racine = _projet_existant(client, atelier)
    avant = _fichiers(racine)

    reponse = client.post(f"/api/projets/{projet}/outillage/report")

    assert reponse.status_code == 200, reponse.text
    outillage = reponse.json()["outillage"]
    assert outillage["reporte_le"] != ""
    assert outillage["a_faire"] is True and outillage["genere"] is False
    assert _fichiers(racine) == avant


def test_le_report_est_idempotent_la_premiere_date_gagne(
    client: TestClient, atelier: Path
) -> None:
    projet, _ = _projet_existant(client, atelier)
    premiere = client.post(f"/api/projets/{projet}/outillage/report").json()

    seconde = client.post(f"/api/projets/{projet}/outillage/report").json()

    assert seconde["outillage"] == premiere["outillage"]


def test_generer_apres_un_report_fait_taire_le_rappel(
    client: TestClient, atelier: Path
) -> None:
    projet, _ = _projet_neuf(client, atelier)
    client.post(f"/api/projets/{projet}/outillage/report")
    choix = _repondre_au_questionnaire(client, projet)

    client.post(f"/api/projets/{projet}/outillage/generation", json={"choix": choix})

    fiche = client.post(f"/api/projets/{projet}/outillage/report").json()
    assert fiche["outillage"]["genere"] is True
    assert fiche["outillage"]["a_faire"] is False


def test_le_report_d_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.post("/api/projets/prj-00000000/outillage/report")

    assert reponse.status_code == 404, reponse.text


# --- ⑤ La proposition d'équipe -------------------------------------------------


def test_la_proposition_d_un_projet_neuf_branche_les_skills_de_ses_reponses(
    client: TestClient, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    reco = client.post(
        f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
    ).json()["recommandation"]

    reponse = client.post(
        f"/api/projets/{projet}/equipe/proposition", json={"choix": choix}
    )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["cree"] is False
    assert corps["source"]["type"] == "choix"
    assert corps["roles"], "un projet neuf outillé appelle au moins un rôle"
    branches = {skill["chemin"] for role in corps["roles"] for skill in role["skills"]}
    recommandes = {e["chemin"] for e in reco["entrees"] if e["type"] == "skill"}
    assert branches and branches <= recommandes
    # Générateur hors ligne : chaque rôle retombe sur son gabarit, et le dit.
    assert {r["playbook_origine"] for r in corps["roles"]} == {ORIGINE_PLAYBOOK_GABARIT}
    assert all(r["playbook"] for r in corps["roles"])
    # Rien n'est créé : ni agent dans le projet, ni fichier dans sa racine.
    assert gabarits.pour_projet(projet).agents.noms() == ()
    assert _fichiers(racine) == set()


def test_la_proposition_d_un_projet_existant_part_de_son_analyse(
    client: TestClient, atelier: Path
) -> None:
    projet, _ = _projet_existant(client, atelier)

    reponse = client.post(f"/api/projets/{projet}/equipe/proposition")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["source"]["type"] == "analyse"
    assert corps["roles"]


def test_la_proposition_d_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.post("/api/projets/prj-00000000/equipe/proposition")

    assert reponse.status_code == 404, reponse.text


# --- ⑥ La création d'équipe ----------------------------------------------------


def _validee(proposition: dict[str, Any]) -> dict[str, Any]:
    """L'équipe validée comme `EtapeEquipe` la rapporte : ce qui a été servi, tel quel."""
    return {
        "proposition_id": proposition["id"],
        "roles": [
            {
                "nom": r["nom"],
                "role": r["role"],
                "competences": r["competences"],
                "playbook": r["playbook"],
                "instances": r["instances"],
                "gabarit": r["gabarit"],
                "skills": [
                    {"nom": s["nom"], "chemin": s["chemin"], "commandes": s["commandes"]}
                    for s in r["skills"]
                ],
                "politique": r["politique"],
            }
            for r in proposition["roles"]
        ],
    }


def test_la_creation_ecrit_dans_le_projet_l_equipe_qui_a_ete_montree(
    client: TestClient, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    projet, _ = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    proposition = client.post(
        f"/api/projets/{projet}/equipe/proposition", json={"choix": choix}
    ).json()

    reponse = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))

    assert reponse.status_code == 201, reponse.text
    rapport = reponse.json()
    assert rapport["cree"] is True
    assert rapport["proposition_id"] == proposition["id"]
    noms = {r["nom"] for r in proposition["roles"]}
    assert {a["nom"] for a in rapport["agents"]} == noms
    cfg = gabarits.pour_projet(projet)
    assert set(cfg.agents.noms()) == noms
    # Les gabarits n'appartiennent à personne : rien n'y est écrit.
    assert gabarits.agents.noms() == ()
    # Le catalogue du projet, servi par l'API, est exactement l'équipe validée.
    catalogue = client.get("/api/catalogue", params={"projet": projet})
    assert catalogue.status_code == 200, catalogue.text
    assert {a["nom"] for a in catalogue.json()} == noms


def test_une_equipe_vide_est_refusee_et_rien_n_est_cree(
    client: TestClient, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    projet, _ = _projet_existant(client, atelier)

    reponse = client.post(f"/api/projets/{projet}/equipe", json={"roles": []})

    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["motif"] == "equipe-vide"
    assert gabarits.pour_projet(projet).agents.noms() == ()


def test_une_equipe_deja_creee_est_refusee_a_la_seconde_validation(
    client: TestClient, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    projet, _ = _projet_existant(client, atelier)
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    validee = _validee(proposition)
    assert client.post(f"/api/projets/{projet}/equipe", json=validee).status_code == 201

    reponse = client.post(f"/api/projets/{projet}/equipe", json=validee)

    assert reponse.status_code == 422, reponse.text
    assert reponse.json()["detail"]["motif"] == "equipe-refusee"
    assert set(gabarits.pour_projet(projet).agents.noms()) == {
        r["nom"] for r in proposition["roles"]
    }


def test_la_creation_dans_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.post(
        "/api/projets/prj-00000000/equipe",
        json={"roles": [{"nom": "dev", "role": "Développeur", "playbook": "Code."}]},
    )

    assert reponse.status_code == 404, reponse.text
