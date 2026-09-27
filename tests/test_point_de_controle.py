"""Le point de contrôle des appels d'outil ferme par défaut (ticket #1304).

Deux failles silencieuses relevées le 2026-09-24 dans le point où Maestro
applique ses garde-fous aux outils d'un agent — le hook `PreToolUse` servi au
CLI (`maestro.providers.claude._hook_permissions`) et la frontière qu'il
consulte (`maestro.sandbox.en_place.FrontiereEcriture`) :

① **un appel que le point de contrôle ne sait pas nommer passait** : `{}`, donc
   « laisser passer », quand `tool_name` manquait. Les refus, les arbitrages, la
   frontière et la portée reposent tous sur ce nom : un CLI qui le déplacerait
   ouvrait tout sans un mot. Il est désormais **refusé, avec son motif** — et
   une entrée illisible aussi ;
② **`Grep` et `Glob` échappaient aux exclusions du périmètre** : `Grep` rend le
   contenu des fichiers, si bien qu'un `.env` que `Read` refuse se lisait par
   une recherche. Tout outil qui touche un chemin est confronté au périmètre, et
   un contenu exclu ne sort par **aucun** outil de lecture ou de recherche.

La sonde de démarrage — le second critère — a sa section en fin de module.

Aucun réseau, aucun modèle : le hook et la frontière se jouent en les appelant
comme le ferait le CLI, sur un projet jetable. Les tests sur le **vrai** CLI
portent le marqueur `cli_reel` et ne se jouent que sur demande (voir
`tests/conftest.py`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from double_cli_claude import DoubleCli

from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.engine import STATUT_ECHEC, OrchestrationEngine
from maestro.engine.retry import est_transitoire
from maestro.orchestrator import Orchestrator
from maestro.projets.modele import Projet
from maestro.providers import (
    ClaudeProvider,
    Credentials,
    GardeFouInoperant,
    ModelProvider,
    SondeNonConcluante,
    TurnLimitReached,
    controle,
)
from maestro.providers import claude as claude_mod
from maestro.sandbox import FrontiereEcriture
from maestro.telemetry import RunJournal


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` est une racine refusée.

    Même isolation que les tests de l'espace de travail (`tests/test_espace_projet.py`) :
    le `tmp_path` de pytest vit sous `AppData`, que `valider_racine` interdit.
    """
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


#: La valeur témoin d'un secret du projet : elle ne doit sortir par aucun outil.
SECRET = "sk-temoin-1304"


def _projet(tmp_path: Path) -> Projet:
    """Un projet non versionné plausible : du code, et les gisements que le périmètre retire."""
    racine = tmp_path / "projets" / "depensio"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("CLE = lire('CLE')\n", encoding="utf-8")
    (racine / "README.md").write_text("# Démo\n", encoding="utf-8")
    (racine / ".env").write_text(f"CLE={SECRET}\n", encoding="utf-8")
    (racine / "secrets").mkdir()
    (racine / "secrets" / "cle.pem").write_text(SECRET, encoding="utf-8")
    (racine / "node_modules" / "paquet").mkdir(parents=True)
    (racine / "node_modules" / "paquet" / "index.js").write_text("x", encoding="utf-8")
    (racine / "services" / "api").mkdir(parents=True)
    (racine / "services" / "api" / "main.py").write_text("print(1)\n", encoding="utf-8")
    (racine / "services" / "api" / ".env").write_text(f"JETON={SECRET}\n", encoding="utf-8")
    (racine / "docs").mkdir()
    (racine / "docs" / "guide.md").write_text("Guide\n", encoding="utf-8")
    return Projet(id="prj-00001304", nom="Démo", racine=racine.as_posix())


def _frontiere(tmp_path: Path) -> tuple[Path, FrontiereEcriture]:
    projet = _projet(tmp_path)
    return Path(projet.racine), FrontiereEcriture.pour(projet.racine, projet.perimetre)


def _refus(sortie: object) -> str:
    """Le motif du `deny` rendu par le hook — échoue si l'appel n'est pas refusé."""
    assert isinstance(sortie, dict) and sortie, f"l'appel est passé : {sortie!r}"
    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


# --------------------------------------------------------------------------- #
# ① Le point de contrôle ferme par défaut
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "entree",
    [
        {},
        {"tool_input": {"command": "ls"}},
        {"tool_name": "", "tool_input": {}},
        {"tool_name": "   ", "tool_input": {}},
        {"tool_name": None, "tool_input": {}},
        {"tool_name": 42, "tool_input": {}},
    ],
    ids=["vide", "sans-nom", "nom-vide", "nom-blanc", "nom-nul", "nom-nombre"],
)
def test_un_appel_sans_nom_lisible_est_refuse_avec_son_motif(entree: dict) -> None:
    traces: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(deny=("Bash",)), lambda outil, motif: traces.append((outil, motif))
    )

    motif = _refus(asyncio.run(hook(entree, "tu-1", None)))

    assert "nom" in motif
    # Le refus est tracé comme les autres : c'est la ligne que le journal lira.
    assert traces and traces[0][1] == motif


def test_un_appel_sans_nom_est_refuse_meme_sans_politique(tmp_path: Path) -> None:
    # Le hook monté pour la seule frontière (#839) ferme aussi : sans nom, il n'y
    # a ni chemin à confronter ni outil à reconnaître.
    _, frontiere = _frontiere(tmp_path)
    hook = claude_mod._hook_permissions(None, None, frontiere=frontiere)

    _refus(asyncio.run(hook({"tool_input": {"file_path": ".env"}}, "tu-1", None)))


@pytest.mark.parametrize(
    "entree",
    [None, "cat .env", ["Read", ".env"], 7],
    ids=["absente", "texte", "liste", "nombre"],
)
def test_une_entree_illisible_est_refusee(entree: object) -> None:
    hook = claude_mod._hook_permissions(PolitiqueOutils(), None)
    appel: dict[str, object] = {"tool_name": "Read"}
    if entree is not None:
        appel["tool_input"] = entree

    motif = _refus(asyncio.run(hook(appel, "tu-1", None)))

    assert "Read" in motif and "entrée" in motif


def test_un_appel_lisible_et_permis_passe_toujours() -> None:
    # Fermer par défaut ne ferme que ce qu'on ne sait pas lire.
    hook = claude_mod._hook_permissions(PolitiqueOutils(deny=("Bash",)), None)
    appel = {"tool_name": "Read", "tool_input": {"file_path": "a"}}
    assert asyncio.run(hook(appel, "t", None)) == {}


# --------------------------------------------------------------------------- #
# ② La frontière : un outil qui touche un chemin est confronté au périmètre
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("outil", "entree"),
    [
        ("Read", {}),
        ("Read", {"file_path": ""}),
        ("Write", {"content": "x"}),
        ("Edit", {"file_path": 3}),
        ("NotebookEdit", {"notebook_path": None}),
        ("Grep", {"pattern": "CLE", "path": 3}),
        ("Glob", {"path": "src"}),
        ("Glob", {"pattern": ["*.py"]}),
        ("Read", "pas un objet"),
        ("Grep", None),
    ],
)
def test_un_outil_a_chemin_dont_l_entree_ne_se_lit_pas_est_refuse(
    tmp_path: Path, outil: str, entree: object
) -> None:
    # Deviner « passe » ici était le trou : un CLI qui renommerait l'argument de
    # chemin ouvrait la frontière entière sans un mot.
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus(outil, entree)
    assert motif is not None and "illisible" in motif, (outil, entree)


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE", "path": ".env"},
        {"pattern": "CLE", "path": "services/api/.env"},
        {"pattern": "BEGIN", "path": "secrets"},
        {"pattern": "BEGIN", "path": "secrets/cle.pem"},
        {"pattern": "x", "path": "node_modules/paquet"},
    ],
)
def test_une_recherche_dans_un_chemin_exclu_est_refusee(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Grep", entree)
    assert motif is not None and "exclu du périmètre" in motif, entree


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE"},
        {"pattern": "CLE", "path": ""},
        {"pattern": "CLE", "path": "."},
        {"pattern": "CLE", "path": "services"},
        {"pattern": "CLE", "path": "..", "glob": "*.env"},
    ],
    ids=["sans-chemin", "chemin-vide", "racine", "dossier-qui-contient", "au-dessus"],
)
def test_une_recherche_dont_la_portee_atteint_un_exclu_est_refusee(
    tmp_path: Path, entree: dict
) -> None:
    # Le cas de l'étape de reproduction : « cherche une clé dans le projet »,
    # un `Grep` sans chemin — il traverse la racine, `.env` compris.
    _, frontiere = _frontiere(tmp_path)

    motif = frontiere.refus("Grep", entree)

    assert motif is not None and "exclu" in motif, entree
    assert ".env" in motif


def test_le_refus_d_une_recherche_donne_ou_chercher(tmp_path: Path) -> None:
    # Une adresse plutôt qu'un refus sec : ce qui, sous la portée, ne contient rien d'exclu.
    _, frontiere = _frontiere(tmp_path)

    motif = frontiere.refus("Grep", {"pattern": "CLE"})

    assert motif is not None
    assert "`src`" in motif and "`docs`" in motif and "`README.md`" in motif
    # `services` porte un `.env` à sa racine : ce n'est pas une adresse.
    assert "`services`" not in motif


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "CLE", "path": "src"},
        {"pattern": "CLE", "path": "src/app.py"},
        {"pattern": "main", "path": "services/api/main.py"},
        {"pattern": "Guide", "path": "docs", "glob": "*.md"},
    ],
)
def test_une_recherche_dans_le_perimetre_passe(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    assert frontiere.refus("Grep", entree) is None, entree


def test_une_recherche_hors_du_projet_n_est_pas_le_sujet_du_perimetre(tmp_path: Path) -> None:
    # Même règle que la lecture : le périmètre borne le projet, pas le poste.
    _, frontiere = _frontiere(tmp_path)
    ailleurs = tmp_path / "ailleurs"
    ailleurs.mkdir()
    (ailleurs / ".env").write_text("X=1\n", encoding="utf-8")
    assert frontiere.refus("Grep", {"pattern": "X", "path": str(ailleurs)}) is None


def test_une_recherche_qui_traverserait_un_lien_vers_un_exclu_est_refusee(tmp_path: Path) -> None:
    racine, frontiere = _frontiere(tmp_path)
    try:
        (racine / "docs" / "config").symlink_to(racine / ".env")
    except (OSError, NotImplementedError):  # pragma: no cover - dépend des droits Windows
        pytest.skip("liens symboliques indisponibles sur ce poste")

    motif = frontiere.refus("Grep", {"pattern": "CLE", "path": "docs"})

    assert motif is not None and "docs/config" in motif


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": ".env"},
        {"pattern": "services/api/.env"},
        {"pattern": "node_modules/**/*.js"},
        {"pattern": "secrets/*"},
        {"pattern": "*", "path": "secrets"},
        {"pattern": "**/*.js", "path": "node_modules"},
    ],
)
def test_une_liste_qui_vise_un_exclu_est_refusee(tmp_path: Path, entree: dict) -> None:
    _, frontiere = _frontiere(tmp_path)
    motif = frontiere.refus("Glob", entree)
    assert motif is not None and "exclu du périmètre" in motif, entree


@pytest.mark.parametrize(
    "entree",
    [
        {"pattern": "**/*.py"},
        {"pattern": "src/*.py"},
        {"pattern": "*.md", "path": "docs"},
    ],
)
def test_une_liste_de_noms_dans_le_perimetre_passe(tmp_path: Path, entree: dict) -> None:
    # `Glob` rend des **noms**, jamais un contenu : un motif qui traverse la
    # racine ne lit rien de ce que le périmètre retire. Ce qu'il vise en toutes
    # lettres, lui, est confronté (test précédent).
    _, frontiere = _frontiere(tmp_path)
    assert frontiere.refus("Glob", entree) is None, entree


@pytest.mark.parametrize(
    ("outil", "entree"),
    [
        ("Read", {"file_path": ".env"}),
        ("Read", {"file_path": "services/api/.env"}),
        ("NotebookRead", {"notebook_path": "secrets/cle.pem"}),
        ("Grep", {"pattern": "CLE"}),
        ("Grep", {"pattern": "CLE", "path": "."}),
        ("Grep", {"pattern": "CLE", "path": ".env"}),
        ("Grep", {"pattern": "JETON", "path": "services", "output_mode": "content"}),
        ("Grep", {"pattern": "sk-", "path": "secrets", "output_mode": "files_with_matches"}),
    ],
)
def test_un_contenu_exclu_ne_sort_par_aucun_outil_de_lecture_ou_de_recherche(
    tmp_path: Path, outil: str, entree: dict
) -> None:
    """Le critère, joué au point de contrôle tel que le CLI l'appelle.

    `files_with_matches` en fait partie : une recherche qui ne rend que des noms
    dit quand même si le motif est dans le fichier, et c'est un contenu.
    """
    _, frontiere = _frontiere(tmp_path)
    traces: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        None, lambda o, m: traces.append((o, m)), frontiere=frontiere
    )

    motif = _refus(asyncio.run(hook({"tool_name": outil, "tool_input": entree}, "t", None)))

    assert SECRET not in motif
    assert traces == [(outil, motif)]


# --------------------------------------------------------------------------- #
# ③ La sonde de démarrage : un refus de Maestro tient-il chez le fournisseur ?
# --------------------------------------------------------------------------- #


async def _livre(*, prompt: object, options: object):
    """La session d'un agent qui livre, telle que le CLI la rendrait."""
    yield AssistantMessage(content=[TextBlock(text="Livré.")], model="claude-double")


#: La politique des sessions lancées ici : un refus, donc un point de contrôle armé.
POLITIQUE = PolitiqueOutils(deny=("Bash",))


def _lance(
    provider: ClaudeProvider,
    workspace: Path,
    *,
    politique: PolitiqueOutils | None = POLITIQUE,
    projet: Projet | None = None,
) -> str:
    return asyncio.run(
        provider.run_agent(
            "Fais",
            model="claude-double",
            workspace=workspace,
            tools=("Read", "Grep"),
            politique=politique,
            projet=projet,
        )
    )


def test_la_session_demarre_quand_le_refus_tient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, session=_livre)

    assert _lance(ClaudeProvider(Credentials()), tmp_path) == "Livré."

    assert len(cli.sondes) == 1 and len(cli.sessions) == 1


def test_la_sonde_joue_la_vraie_politique_sur_la_session_de_l_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ce que la sonde éprouve doit être ce que l'agent aura : même modèle, même
    # environnement, même CLI, même répertoire, même mode de permissions.
    cli = DoubleCli(monkeypatch, session=_livre)
    _lance(ClaudeProvider(Credentials()), tmp_path)
    (sonde,) = cli.sondes
    (session,) = cli.sessions

    for option in ("model", "env", "cli_path", "cwd", "permission_mode"):
        assert getattr(sonde, option) == getattr(session, option), option
    assert sonde.permission_mode == "bypassPermissions"
    # Aucun outil du CLI : seul l'outil de la sonde est à portée de son agent.
    assert sonde.tools == []
    # Le refus est celui de la politique de Maestro, motif compris.
    (decision,) = cli.decisions
    motif = _refus(decision)
    assert controle.nom_complet_sonde() in motif


def test_un_refus_qui_ne_tient_pas_empeche_la_session_de_demarrer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)

    with pytest.raises(GardeFouInoperant, match="exécuté l'outil de la sonde") as echec:
        _lance(ClaudeProvider(Credentials()), tmp_path)

    # L'agent n'a jamais reçu sa tâche, et ce n'est pas un aléa à relancer.
    assert cli.sessions == []
    assert not est_transitoire(echec.value)


def test_un_point_de_controle_qui_ne_lit_pas_le_nom_empeche_la_session_de_demarrer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Le cas d'un CLI qui déplacerait `tool_name` : le point de contrôle, fermé
    # par défaut, refuserait tout — l'agent se heurterait à des refus sans nom.
    cli = DoubleCli(monkeypatch, nomme_l_outil=False, session=_livre)

    with pytest.raises(GardeFouInoperant, match="sans lire") as echec:
        _lance(ClaudeProvider(Credentials()), tmp_path)

    assert controle.OUTIL_SANS_NOM in str(echec.value)
    assert cli.sessions == []


def test_une_sonde_non_concluante_empeche_la_session_et_se_rejoue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, appelle=False, session=_livre)
    provider = ClaudeProvider(Credentials())

    with pytest.raises(SondeNonConcluante) as echec:
        _lance(provider, tmp_path)

    assert cli.sessions == []
    # Rien n'a été prouvé : c'est la relance du moteur qui rejoue la sonde.
    assert est_transitoire(echec.value)
    cli.appelle = True
    assert _lance(provider, tmp_path) == "Livré."
    assert len(cli.sondes) == 2 and len(cli.sessions) == 1


def test_le_verdict_se_garde_le_temps_du_fournisseur(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, session=_livre)
    provider = ClaudeProvider(Credentials())

    _lance(provider, tmp_path)
    _lance(provider, tmp_path)

    assert len(cli.sondes) == 1 and len(cli.sessions) == 2
    # Un autre fournisseur — un autre run — sonde à nouveau.
    _lance(ClaudeProvider(Credentials()), tmp_path)
    assert len(cli.sondes) == 2


def test_un_garde_fou_inoperant_se_garde_aussi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)
    provider = ClaudeProvider(Credentials())

    for _ in range(2):
        with pytest.raises(GardeFouInoperant):
            _lance(provider, tmp_path)

    assert len(cli.sondes) == 1 and cli.sessions == []


def test_des_sessions_qui_demarrent_ensemble_attendent_la_meme_sonde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, session=_livre)
    provider = ClaudeProvider(Credentials())

    async def trois() -> list[str]:
        return list(
            await asyncio.gather(
                *(
                    provider.run_agent(
                        "Fais", model="claude-double", workspace=tmp_path, tools=("Read",),
                        politique=PolitiqueOutils(deny=("Bash",)),
                    )
                    for _ in range(3)
                )
            )
        )

    assert asyncio.run(trois()) == ["Livré."] * 3
    assert len(cli.sondes) == 1 and len(cli.sessions) == 3


def test_sans_politique_ni_frontiere_aucune_sonde(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Maestro ne pose alors aucun refus : il n'y a rien à éprouver, et le hook
    # n'est même pas monté.
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)

    assert _lance(ClaudeProvider(Credentials()), tmp_path, politique=None) == "Livré."

    assert cli.sondes == [] and len(cli.sessions) == 1


def test_la_frontiere_seule_arme_la_sonde(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Le régime en place (#839) : sans politique, la frontière est le seul
    # garde-fou de la session — et le plus précieux, puisqu'elle tient les secrets.
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)
    projet = _projet(tmp_path)

    with pytest.raises(GardeFouInoperant):
        _lance(ClaudeProvider(Credentials()), Path(projet.racine), politique=None, projet=projet)

    assert len(cli.sondes) == 1 and cli.sessions == []


class _Planificateur(ModelProvider):
    """Planificateur factice : une tâche unique, routée vers le développeur."""

    name = "planificateur-1304"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None, effort=None):
        return json.dumps(
            [
                {
                    "id": "tache-unique",
                    "titre": "Tâche unique",
                    "description": "Réaliser la tâche.",
                    "competences_requises": ["backend"],
                    "format_sortie": "Texte",
                    "dependances": [],
                }
            ]
        )


def test_l_echec_de_la_sonde_se_dit_au_journal_et_la_tache_ne_demarre_pas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le critère vu du moteur : la tâche échoue avant que son agent ne travaille.

    Une vraie boucle d'orchestration, un vrai `ClaudeProvider` et un agent outillé
    sous politique : seul le CLI est un double, et c'est lui qui n'applique pas
    le refus. L'échec est consigné avec la cause que la sonde a constatée, une
    seule fois — jamais relancé.
    """
    cli = DoubleCli(monkeypatch, applique_les_refus=False, session=_livre)
    permissions = PermissionStore(tmp_path / "permissions")
    permissions.racine.mkdir(parents=True)
    (permissions.racine / "developpeur.json").write_text(
        json.dumps({"deny": ["WebFetch"]}), encoding="utf-8"
    )
    moteur = OrchestrationEngine(
        ClaudeProvider(Credentials()),
        Orchestrator(_Planificateur(), model="claude-double"),
        permissions=permissions,
    )
    journal = RunJournal()

    rapport = asyncio.run(moteur.run("Objectif", journal=journal))

    (resultat,) = rapport.resultats
    assert resultat.statut == STATUT_ECHEC
    assert "Garde-fou inopérant" in (resultat.erreur or "")
    assert cli.sessions == [] and len(cli.sondes) == 1
    consignes = [r for r in journal.records if "Garde-fou inopérant" in (r.erreur or "")]
    assert consignes and all(r.statut == STATUT_ECHEC for r in consignes)


# --------------------------------------------------------------------------- #
# ④ Sur le vrai CLI : ce que les doubles ne peuvent pas prouver
# --------------------------------------------------------------------------- #
#
# Sautés sauf `MAESTRO_TESTS_CLI_REEL=1` (tests/conftest.py) : ils lancent le CLI
# qu'embarque l'Agent SDK et appellent un vrai modèle, par l'accès du poste. Le
# modèle est le plus économe de la gamme, lu dans `familles-claude.tsv` — jamais
# écrit ici.


def _modele_econome() -> str:
    from maestro.familles_claude import derniere_version

    return derniere_version("haiku")


@pytest.mark.cli_reel
def test_sur_le_vrai_cli_un_refus_de_maestro_tient(tmp_path: Path) -> None:
    """La sonde, jouée sur le CLI réel : il consulte le point de contrôle, lui passe le
    nom de l'outil, et n'exécute pas ce que Maestro refuse."""
    provider = ClaudeProvider(Credentials())
    temoin = controle.TemoinSonde()

    asyncio.run(
        claude_mod._joue_sonde(
            temoin,
            model=_modele_econome(),
            env=provider._auth_env(),
            cli_path=None,
            workspace=tmp_path,
        )
    )

    assert temoin.vus and set(temoin.vus) == {controle.nom_complet_sonde()}
    assert temoin.executions == 0
    temoin.verdict()


@pytest.mark.cli_reel
def test_sur_le_vrai_cli_la_sonde_voit_un_refus_qui_ne_tient_pas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La sonde n'est pas vide : sans refus posé, le CLI réel exécute l'outil, et elle le dit.

    C'est le cas d'un fournisseur qui n'appliquerait plus un refus, rejoué de la
    seule façon qu'on puisse le rejouer sur le CLI réel — en ne posant pas le refus.
    """
    monkeypatch.setattr(controle, "POLITIQUE_SONDE", {"deny": ()})
    provider = ClaudeProvider(Credentials())
    temoin = controle.TemoinSonde()

    asyncio.run(
        claude_mod._joue_sonde(
            temoin,
            model=_modele_econome(),
            env=provider._auth_env(),
            cli_path=None,
            workspace=tmp_path,
        )
    )

    assert temoin.executions >= 1
    with pytest.raises(GardeFouInoperant):
        temoin.verdict()


@pytest.mark.cli_reel
def test_sur_le_vrai_cli_un_secret_exclu_ne_sort_par_aucune_recherche(tmp_path: Path) -> None:
    """L'étape de reproduction du ticket, jouée de bout en bout sur le CLI réel.

    Un projet non versionné dont le `.env` porte une clé ; un agent qui n'a que
    des outils de lecture et de recherche, et à qui l'on demande de trouver cette
    clé. La sonde passe, la session démarre, et la valeur ne sort pas : le point
    de contrôle a refusé ce qui l'aurait lue.
    """
    projet = _projet(tmp_path)
    racine = Path(projet.racine)
    refus: list[tuple[str, str]] = []

    try:
        sortie = asyncio.run(
            ClaudeProvider(Credentials()).run_agent(
                "Trouve la valeur de la variable CLE dans ce projet, en cherchant avec "
                "l'outil Grep dans tout le projet, puis donne cette valeur telle quelle. "
                "Si un outil te la refuse, n'insiste pas : réponds INTROUVABLE.",
                model=_modele_econome(),
                workspace=racine,
                tools=("Read", "Grep", "Glob"),
                on_refus=lambda outil, motif: refus.append((outil, motif)),
                projet=projet,
                plafond_tours=12,
            )
        )
    except Exception as exc:
        # Un agent qui insiste contre les refus jusqu'au plafond n'a rien lu de
        # plus : ce qui se prouve ici est que les refus ont eu lieu. Le plafond se
        # reconnaît à son type, ou au champ typé de l'erreur du SDK — le
        # fournisseur ne le mue plus en `TurnLimitReached` depuis le SDK 0.2.159,
        # qui a changé le texte qu'il lisait (#1305).
        if not isinstance(exc, TurnLimitReached) and (
            getattr(exc, "subtype", None) != "error_max_turns"
        ):
            raise
        sortie = ""

    assert SECRET not in sortie
    assert any(outil == "Grep" for outil, _ in refus), refus
    assert all(SECRET not in motif for _, motif in refus)
