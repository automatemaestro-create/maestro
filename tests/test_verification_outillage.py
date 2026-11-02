"""La vérification d'une livraison fait valoir l'outillage choisi (#1507).

Sur p5 (bilan du 2026-10-14, critère C2), le livrable ne suivait pas l'outillage que la
personne avait choisi : cinquante fichiers hors du formatage Prettier, des commits
« Maestro : … » quand Conventional Commits était retenu, un typage qui ne passait que
sur l'arbre de l'agent. Chaque tâche avait pourtant été vérifiée — sur ses seuls
critères. Cette suite garde ce que le vérificateur sait désormais :

- il lit l'**outillage** du projet, tel que l'agent l'a reçu, et l'**état de la
  branche** de la copie de travail (ses commits, ce qui n'est pas commité) ;
- il sait jouer une commande sur un **clone neuf** de ce que la branche a commité —
  ce que la personne récupère —, jamais dans l'espace de l'agent ;
- l'exécuteur lui donne l'un et l'autre, depuis le manifeste du projet.

Les commandes sont jouées par un joueur doublé, sauf le clone, qui est un vrai `git
clone` (sauté sans `git`).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from maestro.agents import DEVELOPER_PROFILE, AgentRuntime
from maestro.engine.executor import LocalExecutor
from maestro.engine.guardrails import Guardrails
from maestro.engine.verification import (
    CONSTAT_NON_JOUE,
    CONSTAT_NON_TENU,
    CONSTAT_TENU,
    SYSTEME,
    Livraison,
    VerificateurTaches,
    _lire_etablissement,
    _lire_reecriture,
)
from maestro.orchestrator.schema import Task
from maestro.outillage import CHEMIN_MANIFESTE, VERSION_MANIFESTE
from maestro.portee import PorteeProjet
from maestro.projets import ProjetStore
from maestro.projets.application import commiter_en_attente, message_de_sauvegarde
from maestro.providers.base import ModelProvider
from maestro.sandbox import etat_de_la_branche
from maestro.sandbox.verification import Execution
from maestro.telemetry import RunJournal

GIT = shutil.which("git")

avec_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

#: Un interpréteur quelconque : le joueur doublé ne le lance pas.
_INTERPRETE = ("bash", "-c")

_TACHE = Task(
    id="page",
    titre="Écrire la page d'accueil",
    description="Critères de réussite : la page s'affiche ; le typage passe sur un clone neuf.",
    competences_requises=("frontend",),
    format_sortie="Une page",
)

_OUTILLAGE = (
    "# AGENTS.md\n\n## Vérifier\n\n- **Formatage** : `npx prettier --write .`\n\n"
    "## Conventions\n\n- `CONTRIBUTING.md`, prévu — convention de message de commit : "
    "Conventional Commits.\n"
)


@pytest.fixture(autouse=True)
def _sans_identite_git_ambiante(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Coupe l'identité Git du poste (#333) : les commits du test posent la leur."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig-absent"))


class _Reponses(ModelProvider):
    """Le vérificateur doublé : ses réponses dans l'ordre, et chaque prompt reçu."""

    name = "reponses"

    def __init__(self, *reponses: str) -> None:
        self._reponses = list(reponses)
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.prompts.append(prompt)
        return self._reponses[min(len(self.prompts), len(self._reponses)) - 1]


def _git(cwd: Path, *arguments: str) -> str:
    resultat = subprocess.run(
        [GIT or "git", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert resultat.returncode == 0, f"git {' '.join(arguments)} : {resultat.stderr}"
    return resultat.stdout


def _commiter(cwd: Path, message: str) -> None:
    _git(cwd, "add", "-A")
    _git(cwd, "-c", "user.email=t@maestro", "-c", "user.name=T", "commit", "-q", "-m", message)


def _depot(racine: Path) -> Path:
    """Un dépôt d'une application : un fichier commité, une sortie de build ignorée."""
    racine.mkdir(parents=True)
    (racine / ".gitignore").write_text(".next/\n", encoding="utf-8")
    (racine / "page.tsx").write_text("export default null\n", encoding="utf-8")
    _git(racine, "init", "-q")
    _git(racine, "symbolic-ref", "HEAD", "refs/heads/main")
    _commiter(racine, "chore: socle")
    return racine


# --------------------------------------------------------------------------- #
# ① Ce que le vérificateur lit
# --------------------------------------------------------------------------- #


def _une_lecture_tenue() -> str:
    return json.dumps(
        {"controles": [{"critere": "la page s'affiche", "lecture": "x", "tenu": True}]}
    )


def test_le_verificateur_lit_l_outillage_et_l_etat_de_la_branche(tmp_path: Path) -> None:
    provider = _Reponses(_une_lecture_tenue())
    livraison = Livraison(
        sortie="fait",
        espace=tmp_path,
        portee=PorteeProjet(racine=tmp_path),
        outillage=_OUTILLAGE,
        branche="La branche porte 1 commit(s) depuis `main` :\n- feat: ajoute la page",
    )

    asyncio.run(VerificateurTaches(provider, interprete=_INTERPRETE).verifier(
        _TACHE, livraison, modele="m"
    ))

    (prompt,) = provider.prompts
    assert "<outillage>" in prompt and "Conventional Commits" in prompt
    assert "<branche>" in prompt and "- feat: ajoute la page" in prompt
    # L'outillage avant ce que l'agent a rendu : il dit ce qui vaut pour la livraison.
    assert prompt.index("<outillage>") < prompt.index("<compte_rendu>")


def test_sans_outillage_ni_branche_le_prompt_est_celui_d_avant(tmp_path: Path) -> None:
    provider = _Reponses(_une_lecture_tenue())

    asyncio.run(VerificateurTaches(provider, interprete=_INTERPRETE).verifier(
        _TACHE, Livraison(sortie="fait", espace=tmp_path), modele="m"
    ))

    assert "<outillage>" not in provider.prompts[0]
    assert "<branche>" not in provider.prompts[0]


def test_le_verificateur_fait_valoir_le_formatage_le_style_et_la_convention() -> None:
    """Ce que le prompt dit au juge — ce qu'il en fait se mesure au banc (docs/47)."""
    texte = " ".join(SYSTEME.split())

    assert "Quand l'outillage du projet t'est donné" in texte
    assert "jamais en écriture" in texte
    assert "sur les fichiers que la tâche a produits ou modifiés" in texte
    assert "convention de message de commit" in texte
    assert "des fichiers restés hors de tout commit" in texte
    assert '"clone_neuf": true' in texte


# --------------------------------------------------------------------------- #
# ② Un contrôle sur un clone neuf
# --------------------------------------------------------------------------- #


def test_un_controle_sur_un_clone_neuf_se_lit_et_se_garde_a_la_reecriture() -> None:
    texte = json.dumps(
        {
            "controles": [
                {"critere": "le typage passe", "commande": "npm ci && npm run typecheck",
                 "clone_neuf": True},
                {"critere": "le serveur démarre", "commande": "npm start",
                 "demarrage": True, "clone_neuf": True},
                {"critere": "les tests passent", "commande": "npm test"},
            ]
        }
    )

    controles, _, _, empechement = _lire_etablissement(texte, ())

    assert not empechement
    assert [c.clone_neuf for c in controles] == [True, False, False]
    assert controles[0].en_ligne() == "`npm ci && npm run typecheck` (sur un clone neuf)"
    assert controles[0].to_dict()["clone_neuf"] is True
    reecrit = _lire_reecriture(
        json.dumps({"commandes": [{"n": 1, "commande": "npm ci && npx tsc --noEmit"}]}),
        controles,
        {0: "refusée"},
    )
    assert reecrit[0].clone_neuf


@avec_git
def test_un_controle_sur_un_clone_neuf_ne_voit_que_ce_que_la_branche_a_commite(
    tmp_path: Path,
) -> None:
    """Le défaut de p5 : `tsc --noEmit` passait sur l'arbre de l'agent, où un build avait
    laissé ses types, et échouait sur le clone. Le contrôle joue là où joue la personne."""
    espace = _depot(tmp_path / "copie")
    (espace / ".next").mkdir()
    (espace / ".next" / "types.d.ts").write_text("// généré\n", encoding="utf-8")
    (espace / "oubli.ts").write_text("// pas commité\n", encoding="utf-8")
    vus: dict[str, Any] = {}

    def joueur(commande, cwd, *, interprete, delai_s, environnement=None):
        vus["cwd"] = Path(cwd)
        vus["fichiers"] = sorted(p.name for p in Path(cwd).iterdir() if p.name != ".git")
        return Execution(code=0, sortie="", duree_s=0.1)

    reponse = json.dumps(
        {"controles": [{"critere": "le typage passe sur un clone neuf",
                        "commande": "npm ci && npm run typecheck", "clone_neuf": True}]}
    )
    _, verdict = asyncio.run(
        VerificateurTaches(_Reponses(reponse), joueur=joueur, interprete=_INTERPRETE).verifier(
            _TACHE,
            Livraison(sortie="fait", espace=espace, portee=PorteeProjet(racine=espace)),
            modele="m",
        )
    )

    assert verdict.tenue
    assert vus["cwd"] != espace
    assert vus["fichiers"] == [".gitignore", "page.tsx"]
    assert not vus["cwd"].exists()  # le clone est retiré en sortie
    # Un vert n'a rien à expliquer : la preuve ne dit le clone qu'à un échec.
    (constat,) = verdict.constats
    assert constat.etat == CONSTAT_TENU
    assert not constat.preuve.startswith("jouée sur un clone neuf")


def test_un_clone_impossible_ne_joue_rien_et_le_dit(tmp_path: Path) -> None:
    """Un espace qui n'est pas une copie versionnée : le contrôle n'est jamais joué ailleurs."""
    joues: list[Path] = []

    def joueur(commande, cwd, *, interprete, delai_s, environnement=None):
        joues.append(Path(cwd))
        return Execution(code=0, sortie="", duree_s=0.1)

    reponse = json.dumps(
        {"controles": [{"critere": "le typage passe sur un clone neuf",
                        "commande": "npm run typecheck", "clone_neuf": True}]}
    )
    _, verdict = asyncio.run(
        VerificateurTaches(_Reponses(reponse), joueur=joueur, interprete=_INTERPRETE).verifier(
            _TACHE,
            Livraison(sortie="fait", espace=tmp_path, portee=PorteeProjet(racine=tmp_path)),
            modele="m",
        )
    )

    (constat,) = verdict.constats
    assert constat.etat == CONSTAT_NON_JOUE
    assert "pas une copie versionnée" in constat.preuve
    assert joues == []


@avec_git
def test_un_clone_neuf_qui_echoue_dit_ou_il_a_joue(tmp_path: Path) -> None:
    """L'agent lit pourquoi sa commande, verte chez lui, échoue : le clone n'a que le commité."""
    espace = _depot(tmp_path / "copie")

    def joueur(commande, cwd, *, interprete, delai_s, environnement=None):
        return Execution(code=2, sortie="error TS2304: Cannot find name 'PageProps'.", duree_s=1)

    reponse = json.dumps(
        {"controles": [{"critere": "le typage passe sur un clone neuf",
                        "commande": "npm ci && npm run typecheck", "clone_neuf": True}]}
    )
    _, verdict = asyncio.run(
        VerificateurTaches(
            _Reponses(reponse, json.dumps({"commandes": []})),
            joueur=joueur,
            interprete=_INTERPRETE,
        ).verifier(
            _TACHE,
            Livraison(sortie="fait", espace=espace, portee=PorteeProjet(racine=espace)),
            modele="m",
        )
    )

    (constat,) = verdict.constats
    assert constat.etat == CONSTAT_NON_TENU
    assert constat.preuve.startswith("jouée sur un clone neuf de ce que la branche a commité")
    assert "PageProps" in constat.preuve


# --------------------------------------------------------------------------- #
# ③ L'état de la branche
# --------------------------------------------------------------------------- #


@avec_git
def test_l_etat_de_la_branche_dit_ses_commits_et_ce_qui_n_est_pas_commite(
    tmp_path: Path,
) -> None:
    racine = _depot(tmp_path / "projet")
    copie = tmp_path / "copie"
    _git(racine, "worktree", "add", "-q", "-b", "maestro/page", str(copie))
    (copie / "accueil.tsx").write_text("export {}\n", encoding="utf-8")
    _commiter(copie, "feat(accueil): ajoute la page d'accueil")
    (copie / "brouillon.md").write_text("notes\n", encoding="utf-8")

    etat = etat_de_la_branche(copie, "main")

    assert "La branche porte 1 commit(s) depuis `main`" in etat
    assert "- feat(accueil): ajoute la page d'accueil" in etat
    assert "Non commités : 1 fichier(s)" in etat and "`brouillon.md`" in etat
    _commiter(copie, "docs: notes")
    assert "Rien n'est en attente de commit." in etat_de_la_branche(copie, "main")


@avec_git
def test_une_branche_sans_commit_le_dit(tmp_path: Path) -> None:
    racine = _depot(tmp_path / "projet")
    copie = tmp_path / "copie"
    _git(racine, "worktree", "add", "-q", "-b", "maestro/page", str(copie))
    (copie / "accueil.tsx").write_text("export {}\n", encoding="utf-8")

    etat = etat_de_la_branche(copie, "main")

    assert "La branche ne porte aucun commit depuis `main`." in etat
    assert "`accueil.tsx`" in etat
    assert etat_de_la_branche(copie, "") == ""


@avec_git
def test_une_sauvegarde_de_maestro_est_marquee_a_garder(tmp_path: Path) -> None:
    """Le rouge de S12 : l'agent avait amendé la sauvegarde pour la mettre dans la convention.

    Le juge lit qu'un commit est de Maestro, et le prompt lui dit qu'il ne compte pas et
    ne se réécrit pas : il ne renvoie plus l'agent le réécrire.
    """
    racine = _depot(tmp_path / "projet")
    copie = tmp_path / "copie"
    _git(racine, "worktree", "add", "-q", "-b", "maestro/page", str(copie))
    (copie / "accueil.tsx").write_text("export {}\n", encoding="utf-8")
    commiter_en_attente(copie, "maestro/page")  # ce que fait l'extinction
    (copie / "style.css").write_text("body {}\n", encoding="utf-8")
    _commiter(copie, "feat(accueil): ajoute le style")

    etat = etat_de_la_branche(copie, "main")

    assert "- feat(accueil): ajoute le style\n" in etat + "\n"
    assert (
        f"- {message_de_sauvegarde('maestro/page')} — commit de Maestro (sauvegarde du "
        "travail), à garder tel quel" in etat
    )
    assert "ne se réécrit jamais" in " ".join(SYSTEME.split())


def _branche_outillee(tmp_path: Path) -> Path:
    """La copie de travail d'un projet dont le manifeste de Maestro est versionné."""
    racine = _depot(tmp_path / "projet")
    manifeste = racine / CHEMIN_MANIFESTE
    manifeste.parent.mkdir(parents=True)
    manifeste.write_text('{"manifeste": 1}\n', encoding="utf-8")
    _commiter(racine, "chore: outillage")
    copie = tmp_path / "copie"
    _git(racine, "worktree", "add", "-q", "-b", "maestro/socle", str(copie))
    return copie


@avec_git
def test_une_branche_qui_retire_la_comptabilite_de_maestro_le_dit(tmp_path: Path) -> None:
    """Le défaut de S12 (2026-10-26) : l'agent du socle a ignoré `/.maestro/` et retiré le
    manifeste du dépôt, dans le commit même de son travail. La fusion l'a effacé de la
    racine, et les tâches suivantes n'ont plus reçu l'outillage. Le juge le lit, et le
    prompt lui dit qu'une telle branche ne tient pas : elle se rétablit avant de fusionner."""
    copie = _branche_outillee(tmp_path)
    (copie / ".gitignore").write_text(".next/\n/.maestro/\n", encoding="utf-8")
    _git(copie, "rm", "-q", "--cached", CHEMIN_MANIFESTE)
    (copie / "package.json").write_text("{}\n", encoding="utf-8")
    _commiter(copie, "chore: initialiser le projet Next.js")

    etat = etat_de_la_branche(copie, "main")

    assert "La branche touche à `.maestro/`, la comptabilité de Maestro" in etat
    assert f"- `{CHEMIN_MANIFESTE}` (supprimé)" in etat
    texte = " ".join(SYSTEME.split())
    assert "la comptabilité de Maestro est intacte sur la branche" in texte
    assert "se rétablit tel qu'il était" in texte


@avec_git
def test_une_suppression_non_commitee_de_la_comptabilite_se_dit_aussi(tmp_path: Path) -> None:
    """Ce qui n'est pas commité l'est au démontage : le relevé dit l'état qui fusionnera."""
    copie = _branche_outillee(tmp_path)
    (copie / CHEMIN_MANIFESTE).unlink()

    assert f"- `{CHEMIN_MANIFESTE}` (supprimé)" in etat_de_la_branche(copie, "main")


@avec_git
def test_une_comptabilite_intacte_ne_se_dit_pas(tmp_path: Path) -> None:
    """Rien n'est dit de `.maestro/` tant que la branche n'y touche pas — même quand la base
    l'a changé depuis : c'est le point de départ de la branche qui compte."""
    copie = _branche_outillee(tmp_path)
    racine = tmp_path / "projet"
    (racine / CHEMIN_MANIFESTE).write_text('{"manifeste": 2}\n', encoding="utf-8")
    _commiter(racine, "chore: outillage régénéré")
    (copie / "accueil.tsx").write_text("export {}\n", encoding="utf-8")
    _commiter(copie, "feat(accueil): ajoute la page d'accueil")

    assert ".maestro/" not in etat_de_la_branche(copie, "main")


# --------------------------------------------------------------------------- #
# ④ L'exécuteur les donne au vérificateur, depuis le manifeste du projet
# --------------------------------------------------------------------------- #


class _Ecrivain(ModelProvider):
    """L'agent doublé : il écrit sa page dans son espace, sans commiter."""

    name = "ecrivain"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **_):
        (Path(workspace) / "accueil.tsx").write_text("export {}\n", encoding="utf-8")
        return "Fait."


def _projet_outille(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, convention: str, manifeste: bool = True
):
    """Un projet versionné dont le manifeste retient (ou non) une convention de commit —
    ou qui n'a pas de manifeste du tout (`manifeste=False`) : Maestro ne l'a pas outillé."""
    maison = tmp_path / "maison"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    racine = _depot(maison / "projets" / "recettes")
    (racine / "AGENTS.md").write_text(_OUTILLAGE, encoding="utf-8")
    choix = [{"cle": "conventions", "valeur": convention}] if convention else []
    if manifeste:
        chemin = racine / CHEMIN_MANIFESTE
        chemin.parent.mkdir(parents=True)
        chemin.write_text(
            json.dumps(
                {
                    "manifeste": VERSION_MANIFESTE,
                    "source": {"type": "choix", "choix": choix},
                    "entrees": [{"chemin": "AGENTS.md", "role": "instructions"}],
                }
            ),
            encoding="utf-8",
        )
    _commiter(racine, "chore: outillage")
    depot = ProjetStore(tmp_path / "depot")
    return depot, depot.creer("Recettes", racine)


async def _accord(demande):
    return True


def _verifie(depot: ProjetStore, projet_id: str) -> _Reponses:
    juge = _Reponses(_une_lecture_tenue())
    executeur = LocalExecutor(
        _Ecrivain(),
        runtimes={DEVELOPER_PROFILE.nom: AgentRuntime(_Ecrivain(), DEVELOPER_PROFILE)},
        projets=depot,
        guardrails=Guardrails(validateur=_accord),
        verificateur=VerificateurTaches(juge, interprete=_INTERPRETE),
    )
    tache = Task(
        id="page",
        titre="Écrire la page d'accueil",
        description="Critères de réussite : la page s'affiche.",
        competences_requises=("backend",),
        format_sortie="Une page",
        projet_id=projet_id,
    )
    resultat = asyncio.run(executeur.execute(tache, [], RunJournal(run_id="run-1507")))
    assert resultat.ok, resultat.erreur
    return juge


@avec_git
def test_l_executeur_donne_au_juge_l_outillage_et_la_branche_a_juger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depot, projet = _projet_outille(tmp_path, monkeypatch, convention="Conventional Commits")

    juge = _verifie(depot, projet.id)

    prompt = juge.prompts[0]
    assert "<outillage>" in prompt and "`npx prettier --write .`" in prompt
    assert "<branche>" in prompt
    assert "La branche ne porte aucun commit depuis `main`." in prompt
    assert "`accueil.tsx`" in prompt


@avec_git
def test_sans_convention_le_juge_lit_encore_la_branche_pour_la_comptabilite_de_maestro(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le manifeste se garde sur tout projet que Maestro a outillé, convention ou non."""
    depot, projet = _projet_outille(tmp_path, monkeypatch, convention="")

    juge = _verifie(depot, projet.id)

    assert "<outillage>" in juge.prompts[0]
    assert "<branche>" in juge.prompts[0]


@avec_git
def test_un_projet_que_maestro_n_a_pas_outille_n_a_pas_de_branche_a_juger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    depot, projet = _projet_outille(tmp_path, monkeypatch, convention="", manifeste=False)

    juge = _verifie(depot, projet.id)

    assert "<branche>" not in juge.prompts[0]
