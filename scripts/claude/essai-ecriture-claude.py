"""Une session `claude -p` peut-elle écrire sous `.claude/` si une règle `allow` le dit ? (#238)

#229 a établi qu'une session autonome se voit refuser `Write`/`Edit` sur
`.claude/skills/...`, que le refus vient du **CLI** et non du dépôt, et en a déduit
qu'« aucune règle ajoutée à `settings.run.json` ne le lèvera ». La déduction portait
sur les règles que le dépôt porte réellement — `Write` et `Edit` **nus**. Une règle à
**chemin explicite** (`Edit(.claude/skills/**)`) n'avait jamais été essayée : ce script
la met à l'épreuve au lieu de la supposer.

    .venv/Scripts/python.exe scripts/claude/essai-ecriture-claude.py

Protocole — une session jetable par variante, **hors du dépôt** (répertoire temporaire,
donc ni `.claude/settings.json`, ni hooks, ni `CLAUDE.md` de Maestro pour brouiller la
mesure). Chaque session reçoit le même régime que celles de la boucle d'orchestration
(`--permission-mode acceptEdits`, `--settings <json>`, `-p`) et la même consigne : trois
écritures, dans cet ordre.

  1. `Write temoin.txt`                        — TÉMOIN, hors `.claude/`. S'il n'est pas
                                                 écrit, ce n'est pas le garde-fou qui a
                                                 parlé : la mesure est nulle.
  2. `Write .claude/skills/essai/NOUVEAU.md`   — cible du garde-fou, outil `Write`.
  3. `Read` puis `Edit .claude/skills/essai/SKILL.md` — cible du garde-fou, outil `Edit`.

Les variantes ne diffèrent que par le `allow` passé en `--settings` :

  nu       `Write`, `Edit` **nus**            — l'état du dépôt (`settings.run.json`),
                                                donc la reproduction du refus de #229.
  cible    + `Write(.claude/skills/**)`       — l'hypothèse du ticket, orthographe
             et `Edit(.claude/skills/**)`       relative.
  absolue  + les mêmes en **chemin absolu**   — au cas où le matching ne reconnaîtrait
             vers le projet jetable             qu'une racine explicite.

Deux variantes de plus ne posent pas la même question : elles mesurent le **repli** que le
ticket demande d'étudier — appliquer le fichier par une commande plutôt que par l'outil.
`bash` remplace les gestes 2 et 3 par un `cp remplacement.md .claude/…/SKILL.md` ; `script`
par un `bash appliquer.sh <source> <cible>`, la proposition littérale du ticket. Leur
résultat se lit à part (lignes `REPLI`) et n'entre pas dans le verdict : elles ne
renseignent pas sur le `allow`.

Verdict, et code de sortie (les deux se lisent sans jq) :

    0   au moins une variante ciblée a ÉCRIT sous `.claude/` — le garde-fou se lève,
        `settings.run.json` peut porter les règles ;
    3   aucune règle de chemin n'a ouvert `.claude/` — la conclusion de #229 est
        démontrée, une session doit rendre son contenu dans la MR (docs/10 §11.7) ;
    1   l'essai n'a pas pu être conduit (CLI absent, session en échec, témoin non
        écrit) — pas de verdict, surtout pas de conclusion.

RÉSULTAT DU 2026-08-05 (claude-haiku-4-5, 0,14 $ pour les cinq variantes) : code 3. Les
trois variantes de règle sont refusées, `bash` aussi, `script` passe. Autrement dit le
garde-fou n'est pas un défaut de matching qu'une règle mieux écrite comblerait : il est en
amont du `allow` et déborde les outils de fichier — un `cp` dont le CLI sait lire la cible
tombe comme un `Write`, et le blocage ressort alors en ERREUR D'OUTIL, hors
`permission_denials`. Le repli n'a donc pas été retenu : il ne passerait qu'en cachant
l'écriture au CLI. Analyse complète en docs/10-workflow-git.md §11.7.

Rejouer coûte ~0,15 $ et quelques minutes ; `--garder` laisse les répertoires d'essai sur
disque pour inspection.
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

AVANT = "AVANT-238"
APRES = "APRES-238"

# La consigne est volontairement mécanique : on mesure la couche de permissions, pas la
# débrouillardise du modèle. D'où l'interdiction explicite de contourner — un
# `printf … > fichier` passerait peut-être et rendrait la mesure fausse (docs/10 §11.7).
CONSIGNE = f"""Trois écritures, dans cet ordre exact, sans poser de question et sans rien
vérifier d'autre :

1. outil Write : crée le fichier temoin.txt dont le seul contenu est la ligne {APRES}
2. outil Write : crée le fichier .claude/skills/essai/NOUVEAU.md dont le seul contenu est
   la ligne {APRES}
3. outil Read puis outil Edit : dans le fichier .claude/skills/essai/SKILL.md, remplace
   {AVANT} par {APRES}

Si une écriture est refusée, ne la contourne PAS (ni Bash, ni printf, ni un autre outil) et ne
recommence pas : passe simplement à la suivante. Termine en répondant FINI."""

# La quatrième variante ne pose pas la même question que les trois autres : elle mesure le
# REPLI que le ticket demande d'étudier — un script du dépôt, borné et allowlisté, qui
# applique le fichier à la place de l'outil `Write`. Sa faisabilité tient à un point qu'on
# peut mesurer plutôt que supposer : le garde-fou juge-t-il aussi une écriture passée par
# `Bash` ? `cp <source> <cible>` en est la forme la plus proche de `appliquer.sh`.
CONSIGNE_BASH = f"""Deux gestes, dans cet ordre exact, sans poser de question :

1. outil Write : crée le fichier temoin.txt dont le seul contenu est la ligne {APRES}
2. outil Bash : exécute exactement la commande
   cp remplacement.md .claude/skills/essai/SKILL.md

Si un geste est refusé, ne le contourne PAS et ne recommence pas : passe au suivant.
Termine en répondant FINI."""

# La cinquième variante est la proposition LITTÉRALE du ticket : un script du dépôt
# `appliquer.sh <source> <cible>`, allowlisté. Elle se distingue de `bash` sur un point qui
# pourrait tout changer — la cible n'est plus l'argument d'un `cp` que le CLI connaît, mais
# celui d'un script à lui. Le chemin reste écrit en clair sur la ligne de commande : c'est
# la seule forme honnête de la proposition, une forme qui le cacherait étant le
# contournement que docs/10 §11.7 s'interdit.
CONSIGNE_SCRIPT = f"""Deux gestes, dans cet ordre exact, sans poser de question :

1. outil Write : crée le fichier temoin.txt dont le seul contenu est la ligne {APRES}
2. outil Bash : exécute exactement la commande
   bash appliquer.sh remplacement.md .claude/skills/essai/SKILL.md

Si un geste est refusé, ne le contourne PAS et ne recommence pas : passe au suivant.
Termine en répondant FINI."""

APPLIQUER = """#!/usr/bin/env bash
# Maquette du repli proposé par #238 : applique <source> sur <cible>.
set -euo pipefail
cp "$1" "$2"
"""

BASE = ["Read", "Write", "Edit"]
CIBLES = ("Write({racine}.claude/skills/**)", "Edit({racine}.claude/skills/**)")
NOMS = ("nu", "cible", "absolue", "bash", "script")
REPLIS = {"bash": "un `cp`", "script": "un script du dépôt (`appliquer.sh <source> <cible>`)"}


def variantes(projet: Path) -> dict[str, dict]:
    """Le régime de chaque variante. `projet` sert à l'orthographe absolue."""
    absolu = projet.as_posix().rstrip("/") + "/"
    return {
        "nu": {"allow": list(BASE), "consigne": CONSIGNE},
        "cible": {
            "allow": BASE + [regle.format(racine="") for regle in CIBLES],
            "consigne": CONSIGNE,
        },
        "absolue": {
            "allow": BASE + [regle.format(racine=absolu) for regle in CIBLES],
            "consigne": CONSIGNE,
        },
        "bash": {"allow": BASE + ["Bash(cp:*)"], "consigne": CONSIGNE_BASH},
        "script": {
            "allow": BASE + ["Bash(bash appliquer.sh:*)"],
            "consigne": CONSIGNE_SCRIPT,
        },
    }


def prepare(racine: Path) -> Path:
    """Monte un projet jetable : un skill à éditer, sa version de remplacement, rien d'autre."""
    projet = racine / "projet"
    skill = projet / ".claude" / "skills" / "essai"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(f"# Essai\n\n{AVANT}\n", encoding="utf-8")
    (projet / "remplacement.md").write_text(f"# Essai\n\n{APRES}\n", encoding="utf-8")
    (projet / "appliquer.sh").write_text(APPLIQUER, encoding="utf-8", newline="\n")
    return projet


def environnement() -> dict[str, str]:
    """L'env de la session fille, débarrassé de ce que la session mère y a laissé.

    `CLAUDE_PROJECT_DIR` et consorts pointent le dépôt Maestro : les hériter ferait
    juger la session fille sur un projet qui n'est pas le sien. On garde en revanche
    `ANTHROPIC_*` et le reste du `PATH`, sans quoi elle ne s'authentifierait pas.
    """
    env = dict(os.environ)
    for cle in list(env):
        if cle.startswith("CLAUDE_PROJECT") or cle in {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"}:
            del env[cle]
    return env


def lance(
    exe: str,
    projet: Path,
    allow: list[str],
    consigne: str,
    modele: str,
    budget: str,
    delai: int,
) -> tuple[dict, list[str]]:
    """Une session jetable.

    Rend (objet `result` du CLI, outils réellement appelés), ou lève RuntimeError. Le flux
    est demandé en `stream-json` — comme `run.sh` (#176) — parce que l'objet final ne dit
    pas ce que la session a TENTÉ : sans la liste des appels, « rien d'écrit et aucun
    refus » ne distingue pas un garde-fou d'une session qui a renoncé d'elle-même.
    """
    reglages = json.dumps({"permissions": {"allow": allow}}, ensure_ascii=False)
    argv = [
        exe,
        "-p",
        consigne,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "acceptEdits",
        "--settings",
        reglages,
        "--model",
        modele,
        "--max-budget-usd",
        budget,
        "--strict-mcp-config",  # aucun serveur MCP : plus rapide, et rien qui brouille
    ]
    fini = subprocess.run(
        argv,
        cwd=projet,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=environnement(),
        timeout=delai,
        check=False,
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
    """« Write .claude/…/NOUVEAU.md », « Bash cp … » — de quoi lire la trace d'un œil."""
    nom = str(bloc.get("name") or "?")
    entree = bloc.get("input") or {}
    detail = entree.get("file_path") or entree.get("command") or entree.get("path") or ""
    return f"{nom} {str(detail).strip()}".strip()


def resume_retour(bloc: dict) -> str:
    """Ce que l'outil a répondu — c'est là que se lit un refus qui n'a pas dit son nom."""
    contenu = bloc.get("content")
    if isinstance(contenu, list):
        contenu = " ".join(str(p.get("text", "")) for p in contenu if isinstance(p, dict))
    texte = " ".join(str(contenu or "").split())
    return f"{'ERREUR ' if bloc.get('is_error') else ''}{texte}"


def refus_sous_claude(resultat: dict) -> list[str]:
    """Les refus de permission portant sur un chemin sous `.claude/`."""
    trouves = []
    for refus in resultat.get("permission_denials") or []:
        entree = refus.get("tool_input") or {}
        chemin = str(entree.get("file_path") or entree.get("path") or "")
        if ".claude" in chemin.replace("\\", "/"):
            trouves.append(f"{refus.get('tool_name', '?')} {chemin}")
    return trouves


def mesure(projet: Path) -> dict[str, bool]:
    """Ce que la session a réellement écrit sur disque — la seule preuve qui compte."""

    def porte(chemin: Path) -> bool:
        return chemin.is_file() and APRES in chemin.read_text(encoding="utf-8")

    skills = projet / ".claude" / "skills" / "essai"
    return {
        "temoin": porte(projet / "temoin.txt"),
        "write": porte(skills / "NOUVEAU.md"),
        "edit": porte(skills / "SKILL.md"),
    }


def joue(nom: str, exe: str, racine: Path, args: argparse.Namespace) -> dict:
    """Une variante de bout en bout : projet neuf, session, mesure."""
    projet = prepare(racine)
    regime = variantes(projet)[nom]
    allow = regime["allow"]
    print(f"— variante « {nom} » : allow = {allow}")
    resultat, outils = lance(
        exe, projet, allow, regime["consigne"], args.modele, args.budget, args.delai
    )
    ecrit = mesure(projet)
    refus = refus_sous_claude(resultat)
    cout = resultat.get("total_cost_usd")
    cout_lu = f"{cout:.4f}" if isinstance(cout, int | float) else "?"
    # « non écrit » plutôt que « refusé » : le compte rendu ne dit que ce qu'il a vu sur
    # disque. Pourquoi ce n'est pas écrit se lit deux lignes plus bas, dans les refus et la
    # trace des appels — les variantes de repli, elles, ne TENTENT pas le `Write`.
    print(
        f"  témoin {'écrit' if ecrit['temoin'] else 'NON écrit'} · "
        f"Write sous .claude/ {'écrit' if ecrit['write'] else 'non écrit'} · "
        f"Edit sous .claude/ {'écrit' if ecrit['edit'] else 'non écrit'} · "
        f"{len(refus)} refus · {cout_lu} $"
    )
    for ligne in refus:
        print(f"    refus : {ligne}")
    # La trace des appels départage le seul cas ambigu : rien d'écrit ET aucun refus, où
    # c'est la session qui a renoncé, pas la couche de permissions qui a parlé.
    for appel in outils:
        print(f"    appel : {appel[:240]}")
    final = " ".join(str(resultat.get("result") or "").split())
    if final:
        print(f"    dit   : {final[:300]}")
    return {"nom": nom, "ecrit": ecrit, "refus": refus, "outils": outils}


def main(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    analyseur.add_argument(
        "--variante",
        action="append",
        choices=list(NOMS),
        help="ne jouer que cette variante (répétable ; défaut : les cinq).",
    )
    analyseur.add_argument("--modele", default="claude-haiku-4-5-20251001")
    analyseur.add_argument("--budget", default="0.50", help="plafond par session, en dollars.")
    analyseur.add_argument("--delai", type=int, default=300, help="délai par session, en secondes.")
    analyseur.add_argument(
        "--garder", action="store_true", help="ne pas effacer les projets d'essai."
    )
    # Avant `parse_args`, qui imprime lui-même l'aide : la sortie est française, et sans ça
    # une console Windows en cp1252 la rend en mojibake.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = analyseur.parse_args(argv)

    exe = os.environ.get("MAESTRO_CLAUDE_BIN") or shutil.which("claude")
    if not exe:
        print("essai : CLI « claude » introuvable — rien à mesurer.", file=sys.stderr)
        return 1

    noms = args.variante or list(NOMS)
    print(f"essai #238 · CLI {exe} · modèle {args.modele} · variantes : {', '.join(noms)}\n")

    tours = []
    for nom in noms:
        racine = Path(tempfile.mkdtemp(prefix=f"maestro-essai-238-{nom}-"))
        try:
            tours.append(joue(nom, exe, racine, args))
        except (RuntimeError, subprocess.TimeoutExpired) as erreur:
            print(f"  variante « {nom} » non conduite : {erreur}", file=sys.stderr)
            tours.append({"nom": nom, "ecrit": None, "refus": [], "outils": []})
        finally:
            if args.garder:
                print(f"  projet conservé : {racine}")
            else:
                shutil.rmtree(racine, ignore_errors=True)

    print()
    valides = [t for t in tours if t["ecrit"] and t["ecrit"]["temoin"]]
    if not valides:
        print("VERDICT : essai NON CONDUIT — aucun témoin écrit, la mesure ne vaut rien.")
        return 1

    # Le verdict du ticket ne porte que sur les variantes qui interrogent le `allow`.
    # « bash » répond à une autre question, et se rapporte à part pour ne pas la confondre.
    par_regle = [t for t in valides if t["nom"] in {"cible", "absolue"}]
    leve = [t["nom"] for t in par_regle if t["ecrit"]["write"] or t["ecrit"]["edit"]]

    for tour in valides:
        libelle = REPLIS.get(tour["nom"])
        if not libelle:
            continue
        tente = any(appel.startswith("Bash") for appel in tour["outils"])
        if tour["ecrit"]["edit"]:
            print(
                f"REPLI : {libelle} ABOUTIT — le garde-fou ne juge que les outils de "
                "fichier. Reste une\n        décision de politique, pas de mécanisme."
            )
        elif tente:
            print(f"REPLI : {libelle} est bloqué lui aussi — le repli n'est pas réalisable.")
        else:
            print(
                f"REPLI : {libelle} non mesuré — la session n'a jamais appelé `Bash`, elle "
                "a renoncé\n        d'elle-même, ce que la couche de permissions n'explique pas."
            )

    if leve:
        print(
            "VERDICT : le garde-fou SE LÈVE avec une règle à chemin explicite "
            f"({', '.join(leve)})."
        )
        return 0
    print(
        "VERDICT : le garde-fou TIENT — une règle `allow` à chemin explicite ne l'ouvre pas.\n"
        "          Une session autonome rend son contenu dans la MR (docs/10 §11.7)."
    )
    return 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
