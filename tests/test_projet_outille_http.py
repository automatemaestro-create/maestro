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
⑥ **la création d'équipe** écrit ce qui a été montré, dans le projet, ou rien ;
⑦ **la vue des agents suit l'équipe** (#1101) : l'équipe validée, avec la capacité
   du projet, juste après la création **comme après un redémarrage** — réserve C4
   du même bouclage. Cette section-là rouvre l'API sur les mêmes dépôts, parce que
   c'est le seul moyen de voir la moitié du défaut qu'un process ne montre pas ;
⑧ **le fil propose l'équipe** d'un projet qui n'en a pas (#1146), la crée au geste
   par la même voie que ⑥, puis le run demandé aboutit — l'oracle du scénario S3.

Ni réseau ni modèle : les playbooks d'équipe passent par un générateur « hors
ligne », qui fait retomber chaque rôle sur le playbook de son gabarit — le repli
que la route promet elle-même.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.store import AgentStore, SurchargeStore
from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app
from maestro.controltower.chat import ChatStore
from maestro.controltower.generation_agent import GenerateurDefinitionAgent
from maestro.controltower.projets import ServiceProjets
from maestro.engine import RunReport
from maestro.equipe.modele import ORIGINE_PLAYBOOK_GABARIT
from maestro.outillage import CHEMIN_MANIFESTE
from maestro.projets import ProjetStore
from maestro.providers.base import ModelProvider

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


class _Lecteur(ModelProvider):
    """Le modèle qui lit un projet existant (#1158) : il rejoue un script, et compte ses appels.

    Sans script, il ne répond jamais — le lecteur « hors ligne » de cette suite :
    l'analyse reste alors celle des tables, ce que la route promet elle-même, et
    les tests d'avant #1158 gardent leur vérité sans appeler de modèle.
    """

    name = "lecteur-factice"

    def __init__(self, *reponses: str | BaseException) -> None:
        self._reponses = list(reponses) or [RuntimeError("hors ligne (test)")]
        self.appels = 0

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None, effort: str | None = None
    ) -> str:
        self.appels += 1
        reponse = self._reponses.pop(0) if len(self._reponses) > 1 else self._reponses[0]
        if isinstance(reponse, BaseException):
            raise reponse
        return reponse


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


def _app(
    tmp_path: Path,
    atelier: Path,
    gabarits: ConfigurationAgents,
    lecteur: ModelProvider | None = None,
) -> FastAPI:
    """L'app réelle, projets bornés à l'atelier, dépôts d'agents temporaires.

    Une **projection neuve** à chaque appel, les dépôts restant les mêmes : c'est
    exactement ce qu'est un redémarrage de l'API, et c'est la moitié du défaut de
    #1101 qu'un seul process ne peut pas voir.

    `lecteur` est le modèle qui lit un projet existant (#1158) ; le défaut ne
    répond jamais, et l'analyse reste celle des tables.
    """
    return create_app(
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
        lecteur_outillage=lecteur if lecteur is not None else _Lecteur(),
    )


@pytest.fixture()
def client(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> Iterator[TestClient]:
    """L'app réelle, ouverte pour la durée du test."""
    with TestClient(_app(tmp_path, atelier, gabarits)) as client:
        yield client


@pytest.fixture()
def relancer(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> Iterator[Callable[[], TestClient]]:
    """Rouvre l'API sur les **mêmes dépôts**, projection neuve — le redémarrage (#1101)."""
    with ExitStack() as pile:
        yield lambda: pile.enter_context(TestClient(_app(tmp_path, atelier, gabarits)))


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
    # Le modèle de cette suite ne répond pas : l'analyse reste aux tables, et le dit.
    assert corps["lecture"]["etat"] == "indisponible"
    assert "hors ligne" in corps["lecture"]["motif"]


def test_l_analyse_d_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.get("/api/projets/prj-00000000/outillage/analyse")

    assert reponse.status_code == 404, reponse.text


#: Ce que le modèle répond sur la solution .NET de `_projet_dotnet` (#1158).
LECTURE_DOTNET: tuple[str, ...] = (
    "LIRE: Depensio.sln\nLIRE: tests/Api.Tests/Api.Tests.csproj",
    "GESTIONNAIRE: dotnet | Depensio.sln |\n"
    "COMMANDE: construire | Depensio.sln | convention | solution .NET | dotnet build\n"
    "COMMANDE: tester | tests/Api.Tests/Api.Tests.csproj | convention | xunit | dotnet test\n"
    "FIN",
)


def _projet_dotnet(client: TestClient, atelier: Path) -> tuple[str, Path]:
    """Une solution .NET non versionnée — une pile qu'aucune table de détection ne connaît."""
    racine = atelier / "depensio-net"
    fichiers = {
        "Depensio.sln": 'Project("{FAE0}") = "Api", "src\\Api\\Api.csproj", "{1}"\n',
        "src/Api/Api.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web" />\n',
        "src/Api/Program.cs": "var app = WebApplication.Create();\n",
        "tests/Api.Tests/Api.Tests.csproj": '<PackageReference Include="xunit" />\n',
    }
    for chemin, contenu in fichiers.items():
        (racine / chemin).parent.mkdir(parents=True, exist_ok=True)
        (racine / chemin).write_text(contenu, encoding="utf-8")
    return _declarer(client, racine, origine="existant"), racine


def test_l_analyse_d_une_solution_dotnet_nomme_ses_commandes_par_la_lecture(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """#1158, par la route de l'écran : la lecture nomme ce qu'aucune table ne connaissait."""
    lecteur = _Lecteur(*LECTURE_DOTNET)
    with TestClient(_app(tmp_path, atelier, gabarits, lecteur)) as client:
        projet, racine = _projet_dotnet(client, atelier)
        avant = _fichiers(racine)

        reponse = client.get(f"/api/projets/{projet}/outillage/analyse")

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    commandes = {c["usage"]: c for c in corps["constats"]["commandes"]}
    assert commandes["tester"]["commande"] == "dotnet test"
    assert commandes["tester"]["chemin"] == "tests/Api.Tests/Api.Tests.csproj"
    assert commandes["construire"]["chemin"] == "Depensio.sln"
    assert [g["nom"] for g in corps["constats"]["gestionnaires"]] == ["dotnet"]
    assert corps["lecture"]["etat"] == "lue"
    assert set(corps["lecture"]["lus"]) == {"Depensio.sln", "tests/Api.Tests/Api.Tests.csproj"}
    skills = {e["nom"] for e in corps["recommandation"]["entrees"] if e["type"] == "skill"}
    assert {"lancer-les-tests", "construire-le-projet"} <= skills
    assert _fichiers(racine) == avant


def test_la_generation_ecrit_ce_que_la_lecture_a_montre_sans_relire_le_projet(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """Ce que l'écran a lu est ce qui s'écrit : la lecture est gardée, pas redemandée au modèle.

    Un modèle n'est pas tenu de répondre deux fois pareil ; relire entre l'analyse
    et l'écriture ferait disparaître des skills que l'écran avait montrés — le
    défaut de #1100, par une autre porte.
    """
    lecteur = _Lecteur(*LECTURE_DOTNET)
    with TestClient(_app(tmp_path, atelier, gabarits, lecteur)) as client:
        projet, racine = _projet_dotnet(client, atelier)
        analyse = client.get(f"/api/projets/{projet}/outillage/analyse").json()
        appels = lecteur.appels
        retenus = [
            e["chemin"]
            for e in analyse["recommandation"]["entrees"]
            if e["etat"] != "deja-present"
        ]

        reponse = client.post(
            f"/api/projets/{projet}/outillage/generation", json={"retenus": retenus}
        )

    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert lecteur.appels == appels == 2, "la génération a relu le projet au lieu de le reprendre"
    assert corps["analyse"] == analyse["id"]
    assert corps["retenus_inconnus"] == []
    skill = (racine / ".agents/skills/lancer-les-tests/SKILL.md").read_text(encoding="utf-8")
    assert "dotnet test" in skill


def test_un_projet_qui_a_bouge_est_relu(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """La lecture gardée ne survit pas à un fichier lu qui change — même sans changer de nom."""
    lecteur = _Lecteur(*LECTURE_DOTNET, *LECTURE_DOTNET)
    with TestClient(_app(tmp_path, atelier, gabarits, lecteur)) as client:
        projet, racine = _projet_dotnet(client, atelier)
        premiere = client.get(f"/api/projets/{projet}/outillage/analyse").json()
        csproj = racine / "tests/Api.Tests/Api.Tests.csproj"
        csproj.write_text('<PackageReference Include="NUnit" />\n' * 3, encoding="utf-8")

        seconde = client.get(f"/api/projets/{projet}/outillage/analyse").json()

    assert lecteur.appels == 4
    assert seconde["id"] != premiere["id"]


def test_une_lecture_manquee_n_est_pas_gardee(
    tmp_path: Path, atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """Un quota épuisé une fois ne condamne pas le projet aux tables : le prochain appel relit."""
    lecteur = _Lecteur(RuntimeError("quota épuisé"), *LECTURE_DOTNET)
    with TestClient(_app(tmp_path, atelier, gabarits, lecteur)) as client:
        projet, _ = _projet_dotnet(client, atelier)
        manquee = client.get(f"/api/projets/{projet}/outillage/analyse").json()

        reprise = client.get(f"/api/projets/{projet}/outillage/analyse").json()

    assert manquee["lecture"]["etat"] == "indisponible"
    assert "quota épuisé" in manquee["lecture"]["motif"]
    assert not any(c["usage"] == "tester" for c in manquee["constats"]["commandes"])
    assert reprise["lecture"]["etat"] == "lue"
    assert any(c["commande"] == "dotnet test" for c in reprise["constats"]["commandes"])


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


def _playbooks_crees(gabarits: ConfigurationAgents, projet: str) -> str:
    """Les playbooks écrits dans le projet, bout à bout — ce que les agents liront."""
    cfg = gabarits.pour_projet(projet)
    fiches = [cfg.agents.lire(nom) for nom in cfg.agents.noms()]
    return "\n".join(fiche.playbook for fiche in fiches if fiche is not None)


@pytest.mark.parametrize("outille", [False, True], ids=["outillage-non-ecrit", "outille"])
def test_la_creation_ne_branche_que_les_skills_que_le_projet_porte(
    client: TestClient, atelier: Path, gabarits: ConfigurationAgents, outille: bool
) -> None:
    """#1212, par l'appel exact de l'écran : la proposition branche les skills que
    l'outillage **recommande** ; la création ne nomme que ceux qui sont écrits.

    Les deux cas partagent tout sauf la génération de l'outillage, et c'est ce qui
    fait du second l'échantillon fautif du premier : la même équipe validée nomme
    les chemins dès qu'ils existent. Le fil passe par la même création
    (`creer_equipe_du_projet`), ce test la prend par la route."""
    projet, racine = _projet_neuf(client, atelier)
    choix = _repondre_au_questionnaire(client, projet)
    proposition = client.post(
        f"/api/projets/{projet}/equipe/proposition", json={"choix": choix}
    ).json()
    branches = {s["chemin"] for r in proposition["roles"] for s in r["skills"]}
    assert branches, "les réponses du bilan branchent des skills"
    if outille:
        reco = client.post(
            f"/api/projets/{projet}/outillage/recommandation", json={"choix": choix}
        ).json()["recommandation"]
        ecrit = client.post(
            f"/api/projets/{projet}/outillage/generation",
            json={"retenus": [e["chemin"] for e in reco["entrees"]], "choix": choix},
        )
        assert ecrit.status_code == 200, ecrit.text

    reponse = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))

    assert reponse.status_code == 201, reponse.text
    playbooks = _playbooks_crees(gabarits, projet)
    for chemin in branches:
        assert (chemin in playbooks) is outille, chemin
        assert (racine / chemin).is_file() is outille, chemin
    skills_rapportes = [s for a in reponse.json()["agents"] for s in a["skills"]]
    assert bool(skills_rapportes) is outille


def test_la_creation_dans_un_projet_inconnu_est_un_404(client: TestClient) -> None:
    reponse = client.post(
        "/api/projets/prj-00000000/equipe",
        json={"roles": [{"nom": "dev", "role": "Développeur", "playbook": "Code."}]},
    )

    assert reponse.status_code == 404, reponse.text


# --- ⑦ La vue des agents suit l'équipe du projet (#1101) -----------------------
#
# Réserve C4 du même bouclage (2026-09-21) : « types, nombre et **instances** sont
# dérivés de l'analyse ». La donnée était juste — sur le disque, et en run, où
# l'exécuteur lit déjà la capacité du projet ; c'est la **vue** qui ne la montrait
# pas, parce qu'elle filtrait une projection que rien ne peuple hors du flux. Deux
# symptômes, et le second ne se voit qu'en rouvrant l'API : une instance au lieu de
# deux juste après la création, puis plus personne au redémarrage suivant.


def _equipe_a_deux_instances(client: TestClient, atelier: Path) -> tuple[str, dict[str, int]]:
    """Un projet dont l'équipe validée porte un rôle à **2 instances** — le cas du bilan."""
    projet, _ = _projet_existant(client, atelier)
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    validee = _validee(proposition)
    validee["roles"][0]["instances"] = 2
    creation = client.post(f"/api/projets/{projet}/equipe", json=validee)
    assert creation.status_code == 201, creation.text
    return projet, {r["nom"]: r["instances"] for r in validee["roles"]}


def _instances(reponse: Any) -> dict[str, int]:
    """La vue du parc réduite à ce qui se lit à l'écran : qui en est, et combien d'instances."""
    assert reponse.status_code == 200, reponse.text
    return {a["nom"]: a["instances"] for a in reponse.json()}


def test_la_vue_des_agents_rend_l_equipe_validee_avec_ses_instances(
    client: TestClient, atelier: Path
) -> None:
    """Le premier symptôme : la création rendait `instances: 1` sur un rôle validé à 2."""
    projet, attendues = _equipe_a_deux_instances(client, atelier)

    assert _instances(client.get("/api/agents", params={"projet": projet})) == attendues


def test_l_equipe_d_un_projet_survit_a_un_redemarrage_de_l_api(
    client: TestClient, atelier: Path, relancer: Callable[[], TestClient]
) -> None:
    """Le second, et le pire : l'équipe validée disparaissait de l'écran, qui rendait `[]`.

    Le catalogue, lui, la gardait — d'où la forme du correctif : la vue se dérive
    de ce que le projet a sur le disque, et non de ce que la projection a vu.
    """
    projet, attendues = _equipe_a_deux_instances(client, atelier)

    apres_redemarrage = relancer()

    assert _instances(apres_redemarrage.get("/api/agents", params={"projet": projet})) == attendues


def test_un_projet_sans_equipe_rend_toujours_une_vue_vide(
    client: TestClient, atelier: Path
) -> None:
    """#1042 tient : dériver la vue du catalogue n'y fait entrer aucun gabarit."""
    projet, _ = _projet_existant(client, atelier)

    assert client.get("/api/agents", params={"projet": projet}).json() == []


def test_la_capacite_se_regle_encore_apres_un_redemarrage(
    client: TestClient,
    atelier: Path,
    gabarits: ConfigurationAgents,
    relancer: Callable[[], TestClient],
) -> None:
    """Ce que la vue montre est **réglable** : le 404 se prononçait sur la projection.

    Montrer l'équipe sans pouvoir toucher ses curseurs aurait laissé la réserve à
    moitié levée — « sa capacité ne se règle plus » est le second membre du
    constat, et il tient au même 404.
    """
    projet, attendues = _equipe_a_deux_instances(client, atelier)
    nom = next(n for n, instances in attendues.items() if instances == 2)
    apres_redemarrage = relancer()

    reponse = apres_redemarrage.post(
        f"/api/agents/{nom}/capacite", params={"projet": projet}, json={"instances": 3}
    )

    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["instances"] == 3
    assert gabarits.pour_projet(projet).capacites.lire(nom).instances == 3
    vue = _instances(apres_redemarrage.get("/api/agents", params={"projet": projet}))
    assert vue[nom] == 3


def test_lire_le_parc_d_un_projet_ne_reecrit_pas_la_projection(
    client: TestClient, atelier: Path
) -> None:
    """La capacité est rangée par projet ; la fiche de la projection est une, et partagée.

    D'où la copie (`EtatAgent.avec_capacite`) plutôt qu'un réglage posé sur la
    fiche : poser 2 instances en lisant ce projet-ci les ferait lire à tout autre
    lecteur de la même fiche — un second projet qui nomme son agent pareil, et le
    parc transverse ci-dessous, qui n'a aucun projet où lire une capacité.
    """
    projet, attendues = _equipe_a_deux_instances(client, atelier)
    nom = next(n for n, instances in attendues.items() if instances == 2)
    assert _instances(client.get("/api/agents", params={"projet": projet}))[nom] == 2

    assert _instances(client.get("/api/agents"))[nom] == 1


# --- ⑧ Le fil propose l'équipe d'un projet qui n'en a pas, puis le run aboutit ---
#
# #1146, l'oracle du scénario S3 (docs/40 §5). L'essai du 2026-09-21 : sur un
# projet sans agent, le fil proposait un run qui payait cadrage et plan puis
# échouait au routage. Ici l'app est **entière** — vrai répondeur d'orchestration,
# vraie sonde d'équipe sur `catalogue_du_projet`, vraie création par
# `ServiceEquipe.creer`, vrai service d'exécutions —, et seuls le juge (#195) et
# le moteur sont des doubles. On regarde le **disque** : l'équipe écrite dans le
# projet, et le run qui descend jusqu'au moteur avec son projet.

#: L'objectif que le juge reformule, et que le fil doit reprendre après le recrutement.
OBJECTIF_S3 = "Vider le dossier du projet p1"


class _JugeQuiPropose(ModelProvider):
    """Le juge du fil, sans modèle : toute demande est une proposition sur `OBJECTIF_S3`."""

    name = "juge-s3"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        return json.dumps(
            {"verdict": "proposition", "objectif": OBJECTIF_S3, "reponse": "Je lance ?"},
            ensure_ascii=False,
        )


class _MoteurQuiNote:
    """Le moteur d'un run lancé : il ne fait rien, il note ce qu'il a reçu."""

    def __init__(self) -> None:
        self.runs: list[tuple[str, str | None]] = []

    def __call__(self, **reglages: Any) -> _MoteurQuiNote:
        return self

    async def run(self, objectif: str, *, projet_id: str | None = None, **reste: Any) -> RunReport:
        self.runs.append((objectif, projet_id))
        return RunReport(objectif=objectif, resultats=())


def _attendre_la_fin(client: TestClient, run_id: str, projet: str) -> None:
    """Attend que le run lancé en fond soit soldé — par l'API, comme `test_executions`.

    Le lancement rend la main **avant** que le moteur ne tourne : lire le double
    tout de suite mesurerait l'ordonnancement, pas le câblage.
    """
    limite = time.monotonic() + 10
    while True:
        statut = client.get(f"/api/executions/{run_id}", params={"projet": projet}).json()
        if statut.get("statut") not in (None, "en_cours"):
            return
        if time.monotonic() > limite:  # pragma: no cover - filet anti-blocage
            pytest.fail(f"run {run_id} resté en cours")
        time.sleep(0.02)


@pytest.fixture()
def fil_reel(
    tmp_path: Path,
    atelier: Path,
    gabarits: ConfigurationAgents,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[TestClient, _MoteurQuiNote]]:
    """L'app réelle avec son fil d'orchestration, sur des dépôts jetables."""
    monkeypatch.setattr(
        "maestro.providers.factory.provider_from_settings", lambda *a, **k: _JugeQuiPropose()
    )
    moteur = _MoteurQuiNote()
    app = create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        projets=ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(atelier,)),
        agents_store=gabarits.agents,
        surcharges=gabarits.surcharges,
        playbooks=gabarits.playbooks,
        permissions=gabarits.permissions,
        mcp=gabarits.mcp,
        capacites=gabarits.capacites,
        generateur_agent=_GenerateurHorsLigne(),
        chat_store=ChatStore(tmp_path / "chat"),
        fabrique_moteur=moteur,
    )
    with TestClient(app) as client:
        yield client, moteur


def test_s3_le_fil_propose_l_equipe_puis_le_run_aboutit(
    fil_reel: tuple[TestClient, _MoteurQuiNote], atelier: Path, gabarits: ConfigurationAgents
) -> None:
    """Critère 1 et oracle de S3, par les appels exacts de l'écran.

    1. la demande de travail sur un projet sans agent reçoit **l'équipe**, pas un
       run — et rien n'a été lancé ;
    2. l'écran demande la proposition (`…/equipe/proposition`, la route de #1039)
       et la rapporte validée, instances ajustées, au geste du fil ;
    3. l'équipe est écrite **dans le projet**, telle qu'ajustée — capacité comprise ;
    4. le fil repropose la demande d'origine, et le run proposé part au moteur
       avec son projet — là où les tâches trouveront désormais quelqu'un.
    """
    client, moteur = fil_reel
    projet, _ = _projet_existant(client, atelier)
    chat = "/api/chat/orchestrateur"

    envoi = client.post(
        f"{chat}/messages",
        json={"contenu": "Peux-tu vider le dossier du projet ?", "projet_id": projet},
    )

    assert envoi.status_code == 201, envoi.text
    demande = envoi.json()["messages"][1]
    # Les quatre champs de #1227 sont vides : aucun run n'attend cette demande —
    # c'est un projet sans agent, pas un plan qui appelle un rôle absent.
    assert demande["recrutement"] == {
        "objectif": OBJECTIF_S3,
        "projet_id": projet,
        "run_id": "",
        "role": "",
        "gabarit": "",
        "raison": "",
        "taches": [],
    }
    assert demande["proposition"] == ""
    assert moteur.runs == []

    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    assert proposition["roles"], "l'analyse d'un projet Python appelle au moins un rôle"
    validee = _validee(proposition)
    validee["roles"][0]["instances"] = 2

    recrutement = client.post(
        f"{chat}/recrutement", json={"approuve": True, "conversation": None, **validee}
    )

    assert recrutement.status_code == 201, recrutement.text
    _, reponse = recrutement.json()["messages"]
    attendues = {r["nom"]: r["instances"] for r in validee["roles"]}
    cfg = gabarits.pour_projet(projet)
    assert set(cfg.agents.noms()) == set(attendues)
    assert {nom: cfg.capacites.lire(nom).instances for nom in attendues} == attendues
    assert reponse["proposition"] == OBJECTIF_S3
    assert _instances(client.get("/api/agents", params={"projet": projet})) == attendues

    lance = client.post(f"{chat}/cadrage", json={"approuve": True, "projet_id": projet})

    assert lance.status_code == 201, lance.text
    run_id = lance.json()["messages"][1]["run_id"]
    assert run_id != ""
    _attendre_la_fin(client, run_id, projet)
    assert moteur.runs == [(OBJECTIF_S3, projet)]


def test_s3_un_projet_equipe_se_voit_proposer_le_run_directement(
    fil_reel: tuple[TestClient, _MoteurQuiNote], atelier: Path
) -> None:
    """Le témoin du câblage réel : la sonde compte l'équipe créée, et le run est proposé."""
    client, _ = fil_reel
    projet, _ = _projet_existant(client, atelier)
    proposition = client.post(f"/api/projets/{projet}/equipe/proposition").json()
    creation = client.post(f"/api/projets/{projet}/equipe", json=_validee(proposition))
    assert creation.status_code == 201, creation.text

    envoi = client.post(
        "/api/chat/orchestrateur/messages",
        json={"contenu": "Peux-tu vider le dossier du projet ?", "projet_id": projet},
    )

    demande = envoi.json()["messages"][1]
    assert demande["proposition"] == OBJECTIF_S3
    assert demande["recrutement"] is None
