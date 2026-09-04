"""Un worktree sous `.claude/worktrees/` s'entre-t-il SANS question, et y écrit-on encore ? (#847)

Depuis le CLI Claude Code 2.1.206, `EnterWorktree path=<chemin>` vers un worktree situé hors de
`<dépôt>/.claude/worktrees/` déclenche une demande de validation interactive que la règle
`EnterWorktree` de l'`allow` (#199) ne lève pas : c'est un contrôle de sûreté du CLI
(`decisionReason: safetyCheck`, « permission-root relocation … a model-supplied worktree outside
.claude/worktrees/ »), pas une permission. Or `worktree.sh` montait les worktrees dans un dossier
frère du dépôt — chaque `/ticket-start` interactif s'arrêtait donc sur cette question.

Ce script mesure, avant qu'on déplace quoi que ce soit, ce que le critère du CLI vaut vraiment :

    .venv/Scripts/python.exe scripts/claude/essai-worktree-gere.py

Protocole — un dépôt git jetable (hors de Maestro : ni `.claude/settings.json`, ni hooks, ni
`CLAUDE.md` pour brouiller la mesure) portant DEUX worktrees liés, l'un sous `.claude/worktrees/`
(« géré » au sens du CLI), l'autre dans un dossier frère (l'emplacement d'avant #847). Trois
sessions `claude -p` jetables, au régime du banc #238 (`acceptEdits`, `--settings`, `-p`) :

  A  entree-geree     cwd = dépôt ; `EnterWorktree path=<géré>`, puis `Write temoin.txt`.
                      Attendu : ENTRÉ, aucun refus — c'est la question du ticket.
  B  entree-ailleurs  cwd = dépôt ; même consigne vers le worktree frère.
                      Attendu : REFUSÉ — en `-p`, l'« ask » n'a personne pour répondre. C'est le
                      témoin : sans lui, un A qui passe ne distingue pas « le CLI ne demande rien
                      ici » de « le CLI ne demande jamais rien ».
  C  ecriture-geree   cwd = <géré> ; `Write temoin.txt`, `Write sous/dossier/NOUVEAU.md`,
                      `Read` + `Edit README.md`, puis `Write .claude/skills/essai/NOUVEAU.md`.
                      Attendu : les trois premiers ÉCRITS (les chemins absolus contiennent
                      `.claude/worktrees/`, et c'est tout l'enjeu : un garde-fou qui jugerait le
                      chemin entier bloquerait chaque édition du ticket), le quatrième REFUSÉ —
                      le garde-fou `.claude/` (#229/#238) reste actif sur le `.claude/` DU
                      worktree, et un banc qui ne le voit pas tirer mesure autre chose.

Verdict, et code de sortie :

    0   les trois attentes tiennent — l'emplacement `.claude/worktrees/` est la réponse ;
    3   au moins une ne tient pas — le CLI a changé de critère, le dossier est à rouvrir ;
    1   l'essai n'a pas pu être conduit (CLI absent, session en échec) — pas de verdict.

RÉSULTAT DU 2026-09-04 (CLI 2.1.215, claude-haiku-4-5, 0,10 $ pour les trois sessions) : code 0.
A entré, `temoin.txt` écrit dans le worktree géré, `permission_denials` vide. B refusé, le message
même du contrôle en retour d'outil (« Enter the worktree at … ? This moves the session's working
directory and write access there… »), `temoin.txt` absent du worktree frère. C : `temoin.txt`,
`sous/dossier/NOUVEAU.md` et le `README.md` édité sont sur le disque, `.claude/skills/essai/
NOUVEAU.md` n'y est pas (refus « Claude requested permissions to write to … »). C'est sur cette
mesure que `worktree.sh` monte sous `.claude/worktrees/` — voir docs/10 §9.1.

Rejouer coûte ~0,10 $ et quelques minutes ; `--garder` laisse le dépôt d'essai sur disque.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APRES = "APRES-847"
AVANT = "AVANT-847"
MODELE = "claude-haiku-4-5-20251001"
ENTREE_REUSSIE = "Entered worktree"

# La consigne est volontairement mécanique : on mesure la couche du CLI, pas la débrouillardise du
# modèle — d'où l'interdiction explicite de contourner (même raison qu'au banc #238).
CONSIGNE_ENTREE = """Deux gestes, dans cet ordre exact, sans poser de question et sans rien vérifier
d'autre :

1. outil EnterWorktree avec le paramètre path = {cible}
2. outil Write : crée le fichier temoin.txt (chemin relatif au répertoire courant) dont le seul
   contenu est la ligne {apres}

Ce projet est un bac à sable jetable créé pour cet essai. Si un geste est refusé, ne le contourne
PAS et ne recommence pas : passe simplement au suivant. Termine en répondant FINI."""

CONSIGNE_ECRITURE = """Quatre gestes, dans cet ordre exact, sans poser de question et sans rien
vérifier d'autre :

1. outil Write : crée le fichier temoin.txt dont le seul contenu est la ligne {apres}
2. outil Write : crée le fichier sous/dossier/NOUVEAU.md dont le seul contenu est la ligne {apres}
3. outil Read puis outil Edit : dans le fichier README.md, remplace {avant} par {apres}
4. outil Write : crée le fichier .claude/skills/essai/NOUVEAU.md dont le seul contenu est la
   ligne {apres}

Ce projet est un bac à sable jetable créé pour cet essai : rien de ce qu'il contient n'est en
service. Si un geste est refusé, ne le contourne PAS (ni Bash, ni printf, ni un autre outil) et ne
recommence pas : passe simplement au suivant. Termine en répondant FINI."""

ALLOW_ENTREE = ["EnterWorktree", "Read", "Write"]
ALLOW_ECRITURE = ["Read", "Write", "Edit"]


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def prepare(racine: Path) -> tuple[Path, Path, Path]:
    """Monte le dépôt jetable et ses deux worktrees. Rend (dépôt, worktree géré, worktree frère)."""
    depot = racine / "depot"
    depot.mkdir()
    git("init", "-b", "main", cwd=depot)
    git("config", "user.email", "banc@example.invalid", cwd=depot)
    git("config", "user.name", "banc", cwd=depot)
    (depot / "README.md").write_text(f"# Essai\n\n{AVANT}\n", encoding="utf-8")
    git("add", ".", cwd=depot)
    git("commit", "-q", "-m", "init", cwd=depot)
    gere = depot / ".claude" / "worktrees" / "essai"
    frere = racine / "ailleurs-worktrees" / "essai2"
    gere.parent.mkdir(parents=True)
    frere.parent.mkdir(parents=True)
    git("worktree", "add", str(gere), "-b", "essai", cwd=depot)
    git("worktree", "add", str(frere), "-b", "essai2", cwd=depot)
    # Le témoin du garde-fou en C : un `.claude/` DANS le worktree géré, à ne pas confondre avec
    # celui du dépôt qui l'héberge.
    skill = gere / ".claude" / "skills" / "essai"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# Essai\n\n{AVANT}\n", encoding="utf-8")
    return depot, gere, frere


def environnement() -> dict[str, str]:
    """L'env de la session fille, débarrassé de ce que la session mère y a laissé (cf. #238)."""
    env = dict(os.environ)
    for cle in list(env):
        if cle.startswith("CLAUDE_PROJECT") or cle in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}:
            del env[cle]
    return env


def lance(
    exe: str,
    cwd: Path,
    allow: list[str],
    consigne: str,
    modele: str,
    budget: str,
    delai: int,
) -> tuple[dict, list[str]]:
    """Une session jetable. Rend (objet `result`, appels et retours d'outils), ou lève.

    Le flux est demandé en `stream-json` parce que l'objet final ne dit pas ce que la session a
    TENTÉ ni ce que l'outil lui a répondu — et c'est dans le retour d'`EnterWorktree` que se lit
    « Entered worktree » ou le texte de la question.
    """
    reglages = json.dumps({"permissions": {"allow": allow}}, ensure_ascii=False)
    argv = [
        exe, "-p", consigne,
        "--output-format", "stream-json", "--verbose",
        "--permission-mode", "acceptEdits",
        "--settings", reglages,
        "--model", modele,
        "--max-budget-usd", budget,
        "--strict-mcp-config",  # aucun serveur MCP : plus rapide, et rien qui brouille
    ]
    fini = subprocess.run(
        argv, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=environnement(), timeout=delai, check=False,
    )
    resultat: dict = {}
    outils: list[str] = []
    for ligne in fini.stdout.splitlines():
        ligne = ligne.strip()
        if not ligne.startswith("{"):
            continue
        try:
            objet = json.loads(ligne)
        except json.JSONDecodeError:
            continue
        if objet.get("type") == "result":
            resultat = objet
        elif objet.get("type") == "assistant":
            for bloc in objet.get("message", {}).get("content", []) or []:
                if bloc.get("type") == "tool_use":
                    outils.append(resume_appel(bloc))
        elif objet.get("type") == "user":
            for bloc in objet.get("message", {}).get("content", []) or []:
                if bloc.get("type") == "tool_result":
                    outils.append(f"  ↳ {resume_retour(bloc)}")
    if not resultat:
        raise RuntimeError(
            f"le CLI n'a rendu aucun objet `result` (code {fini.returncode}) : "
            f"{(fini.stderr or fini.stdout or '').strip()[:400]}"
        )
    return resultat, outils


def resume_appel(bloc: dict) -> str:
    """« EnterWorktree <chemin> », « Write <fichier> » — de quoi lire la trace d'un œil."""
    nom = str(bloc.get("name") or "?")
    entree = bloc.get("input") or {}
    detail = entree.get("file_path") or entree.get("path") or entree.get("command") or ""
    return f"{nom} {str(detail).strip()}".strip()


def resume_retour(bloc: dict) -> str:
    """Ce que l'outil a répondu — c'est là que se lit « Entered worktree », ou la question."""
    contenu = bloc.get("content")
    if isinstance(contenu, list):
        contenu = " ".join(str(p.get("text", "")) for p in contenu if isinstance(p, dict))
    texte = " ".join(str(contenu or "").split())[:240]
    return f"{'ERREUR ' if bloc.get('is_error') else ''}{texte}"


def refus(resultat: dict) -> list[tuple[str, str]]:
    """Les refus de permission, en (outil, cible)."""
    trouves = []
    for r in resultat.get("permission_denials") or []:
        entree = r.get("tool_input") or {}
        cible = str(entree.get("file_path") or entree.get("path") or entree.get("command") or "")
        trouves.append((str(r.get("tool_name") or "?"), cible))
    return trouves


def entre(outils: list[str]) -> bool:
    """La session est-elle entrée ? Se lit dans le retour d'outil, pas dans la prose finale."""
    return any(ENTREE_REUSSIE in o for o in outils)


def verdict_entree_geree(resultat: dict, outils: list[str], gere: Path) -> tuple[bool, str]:
    """A : entré, sans refus d'`EnterWorktree`, et le témoin écrit dans le worktree géré."""
    refuses = [c for o, c in refus(resultat) if o == "EnterWorktree"]
    temoin = (gere / "temoin.txt").is_file()
    ok = entre(outils) and not refuses and temoin
    return ok, (
        f"entré : {entre(outils)} · refus EnterWorktree : {refuses or 'aucun'} · "
        f"temoin.txt dans le worktree géré : {temoin}"
    )


def verdict_entree_ailleurs(resultat: dict, outils: list[str], frere: Path) -> tuple[bool, str]:
    """B : PAS entré, un refus d'`EnterWorktree` consigné, rien d'écrit dans le worktree frère."""
    refuses = [c for o, c in refus(resultat) if o == "EnterWorktree"]
    temoin = (frere / "temoin.txt").is_file()
    ok = (not entre(outils)) and bool(refuses) and not temoin
    return ok, (
        f"entré : {entre(outils)} · refus EnterWorktree : {refuses or 'aucun'} · "
        f"temoin.txt dans le worktree frère : {temoin}"
    )


def verdict_ecriture(resultat: dict, gere: Path) -> tuple[bool, str]:
    """C : les trois écritures ordinaires sur le disque, celle sous `.claude/` du worktree absente.

    Lu sur le DISQUE et non dans la prose de la session : « fait » ne vaut rien, le fichier si.
    """
    temoin = (gere / "temoin.txt").is_file() and \
        (gere / "temoin.txt").read_text(encoding="utf-8").strip() == APRES
    imbrique = (gere / "sous" / "dossier" / "NOUVEAU.md").is_file()
    edite = (gere / "README.md").is_file() and \
        APRES in (gere / "README.md").read_text(encoding="utf-8")
    garde = not (gere / ".claude" / "skills" / "essai" / "NOUVEAU.md").exists()
    ok = temoin and imbrique and edite and garde
    return ok, (
        f"temoin.txt : {temoin} · sous/dossier/NOUVEAU.md : {imbrique} · README édité : {edite} · "
        f"garde-fou .claude/ du worktree intact : {garde}"
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parseur = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parseur.add_argument("--modele", default=MODELE)
    parseur.add_argument("--budget", default="0.60", help="plafond par session, en dollars")
    parseur.add_argument("--delai", type=int, default=420, help="par session, en secondes")
    parseur.add_argument(
        "--garder", action="store_true", help="laisser le dépôt d'essai sur disque",
    )
    options = parseur.parse_args(argv)

    exe = shutil.which("claude")
    if not exe:
        print("CLI `claude` introuvable dans le PATH — essai non conduit.", file=sys.stderr)
        return 1

    racine = Path(tempfile.mkdtemp(prefix="essai-worktree-gere-"))
    print(f"CLI {exe} · modèle {options.modele} · dépôt d'essai {racine}")
    cout = 0.0
    verdicts: dict[str, bool] = {}
    try:
        depot, gere, frere = prepare(racine)

        def session(nom: str, cwd: Path, allow: list[str], consigne: str) -> tuple[dict, list[str]]:
            nonlocal cout
            print(f"\n=== {nom} ===")
            resultat, outils = lance(
                exe, cwd, allow, consigne, options.modele, options.budget, options.delai,
            )
            cout += float(resultat.get("total_cost_usd") or 0)
            print("\n".join(outils))
            return resultat, outils

        consigne_a = CONSIGNE_ENTREE.format(cible=gere.as_posix(), apres=APRES)
        resultat, outils = session("A · entree-geree", depot, ALLOW_ENTREE, consigne_a)
        verdicts["A"], detail = verdict_entree_geree(resultat, outils, gere)
        print(detail)

        consigne_b = CONSIGNE_ENTREE.format(cible=frere.as_posix(), apres=APRES)
        resultat, outils = session("B · entree-ailleurs", depot, ALLOW_ENTREE, consigne_b)
        verdicts["B"], detail = verdict_entree_ailleurs(resultat, outils, frere)
        print(detail)

        consigne_c = CONSIGNE_ECRITURE.format(apres=APRES, avant=AVANT)
        resultat, _ = session("C · ecriture-geree", gere, ALLOW_ECRITURE, consigne_c)
        verdicts["C"], detail = verdict_ecriture(resultat, gere)
        print(detail)
    except (RuntimeError, subprocess.SubprocessError, OSError) as erreur:
        print(f"\nEssai non conduit : {erreur}", file=sys.stderr)
        return 1
    finally:
        if options.garder:
            print(f"\n(conservé) {racine}")
        else:
            shutil.rmtree(racine, ignore_errors=True)

    tenues = all(verdicts.values())
    print(f"\nVerdicts : {verdicts} · coût {cout:.2f} $")
    if tenues:
        print(
            "→ 0 : le CLI entre sans question sous .claude/worktrees/, refuse ailleurs, "
            "et on y écrit."
        )
        return 0
    print("→ 3 : au moins une attente ne tient plus — le critère du CLI a changé, à rouvrir.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
