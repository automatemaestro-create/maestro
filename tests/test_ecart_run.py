"""L'écart entre une session de run et une session interactive (#793, lot 6 de #788).

Le chantier #788 n'a pas allongé une liste : il a **arbitré**. Sur ses cinq écarts, **trois se
soldent par « on ne change rien »** — les cinq `ask` du dépôt restent refusés (#790), le régime de
permission d'un run ne s'ouvre pas (#791), l'accès web reste fermé des deux côtés (#792) — et c'est
précisément ce que rien ne garde : un verdict « on ne change rien » ne laisse aucun diff derrière
lui, donc rien ne le distingue, six mois plus tard, d'une question que personne n'a posée. Le seul
moyen de le tenir est de le garder **comme verdict**, avec sa raison, là où quelqu'un le lirait
avant de le défaire.

Ce module garde donc, **un garde à la fois** — un test qui les vérifierait ensemble ne dirait pas
lequel garde :

* **le lot 1 — l'inventaire rejouable** (`ecart-run.sh`, #789). Son motif se prouve sur les
  **échantillons fautifs versionnés** avant qu'on croie son « aucun écart » : une allowlist qui
  couvre tout doit faire basculer le verdict, une qui ne couvre rien doit laisser les écarts, et un
  `ask` ne doit pas dire la même chose qu'un `deny`. Sans cette épreuve, un rapport mal branché
  rendrait le plus rassurant des verdicts sur une question jamais posée ;
* **le lot 2 — les règles** (#790). `guard.sh --check` ne rougit pas sur le dépôt réel, les huit
  trous mesurés sont couverts, et **aucune des règles ajoutées n'élargit au-delà de sa raison
  écrite** : les refus mérités de #307/#528 restent refusés, et les cinq `ask` restent des `ask` ;
* **les lots 3 et 4 — deux verdicts, gardés comme verdicts** (#791, #792). Le régime des sessions
  de run n'ouvre pas `.claude/` (pas de `bypassPermissions`), le banc qui rouvrirait le dossier est
  dans le dépôt, et l'accès web reste hors des **deux** allowlists — ce dernier point vit dans
  [`test_design_veille.py`](test_design_veille.py), qui visait déjà les deux fichiers et qui dit
  désormais **les deux raisons**, celle de `WebFetch` n'étant pas celle de la veille ;
* **le lot 5 — la survie d'une question** (#795). Une veille rencontrée par un run **se consigne**
  dans un ticket qui survit à la fermeture du sien, et un run **sans** question n'écrit rien ;
* **G5 — l'interdit voulu** (#788). `merge-mr` et `pipeline-wait` refusés en run ne sont pas un
  écart à combler : ils sont refusés par une règle, avec leur raison, et le rapport les range en
  « voulu » et **jamais** parmi les manquants. C'est le seul point du parent qui risque d'être
  « corrigé » par erreur, donc le seul dont l'absence de garde coûterait une régression.

**Ni réseau ni compte de forge** : les épreuves du rapport lisent des fichiers versionnés, celles du
verbe passent par le harnais de [`harnais_forge.py`](harnais_forge.py) (dépôt jetable + `gh`
factice), partagé avec `test_collaboration.py`, `test_cycle_de_vie.py`, `test_reste_claude.py` et
`test_design_veille.py`. **On compte des règles et des gestes, jamais des durées** (règle de #577) :
un chronomètre en CI mesure la charge de la machine.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    MOI,
    RACINE,
    Depot,
    ecritures,
    lignes_projet,
    monte_depot,
    regle_pose_status,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

ECART_RUN = RACINE / "scripts" / "orchestrate" / "ecart-run.sh"
GUARD_SH = RACINE / "scripts" / "orchestrate" / "guard.sh"
RUN_SH = RACINE / "scripts" / "orchestrate" / "run.sh"
REGLAGES_RUN = RACINE / "scripts" / "orchestrate" / "settings.run.json"
REGLAGES_DEPOT = RACINE / ".claude" / "settings.json"
BANC = RACINE / "scripts" / "claude" / "essai-ecriture-claude.py"
DOC10 = RACINE / "docs" / "10-workflow-git.md"
DOC30 = RACINE / "docs" / "30-cible-visuelle-control-tower.md"
CLAUDE_MD = RACINE / "CLAUDE.md"
FIXTURES = RACINE / "tests" / "fixtures" / "ecart_run"

# Les huit règles que le lot 2 a ajoutées, et RIEN D'AUTRE : leur liste est le contrat du lot.
TROUS_COMBLES = (
    "pwd",
    "cut -f2",
    "tr -d ' '",
    "chmod +x scripts/x.sh",
    "git mv a b",
    "git merge-base main HEAD",
    "git check-ignore -v x",
)
# Ce qui doit RESTER refusé — refus mérités de #307/#528, plus les deux gestes de G5. Un test qui
# ne vérifierait que les ajouts laisserait passer une règle trop large qui les avalerait au passage.
REFUS_MERITES = (
    "python -c 'print(1)'",
    "for f in a b; do echo $f; done",
    "curl -s http://x/y",
    "python - <<'PY'",
    "rm -rf build",
    "bash /c/tmp/x.sh",
    "PYTHONPATH=. .venv/Scripts/python.exe x.py",
)
G5 = (
    "bash scripts/gitlab/lib.sh merge-mr 42",
    "bash scripts/gitlab/lib.sh pipeline-wait main",
)


# =================================================================================================
# Outillage — jouer le rapport, lire son TSV
# =================================================================================================


def joue(*args: str) -> subprocess.CompletedProcess[str]:
    """`ecart-run.sh` depuis la racine du dépôt, hors ligne et sans écriture."""
    assert BASH is not None
    return subprocess.run(  # noqa: S603
        [BASH, str(ECART_RUN), *args],
        cwd=str(RACINE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "LC_ALL": "C.UTF-8"},
        timeout=120,
    )


def rapport(*args: str) -> tuple[dict[str, str], int]:
    """Le TSV réduit à « geste → verdict », plus le code de sortie.

    Les colonnes sont celles du mode machine : question, verdict, geste, origine, détail,
    contradiction. On n'en retient ici que ce dont les verdicts dépendent — la forme complète est
    éprouvée par `test_le_mode_machine_rend_la_contradiction`.
    """
    acheve = joue("--tsv", *args)
    verdicts: dict[str, str] = {}
    for ligne in acheve.stdout.splitlines():
        champs = ligne.split("\t")
        if len(champs) >= 3:
            verdicts[champs[2]] = champs[1]
    return verdicts, acheve.returncode


def contradictions(*args: str) -> dict[str, str]:
    """Les gestes dont la classe écrite est démentie par les règles lues."""
    acheve = joue("--tsv", *args)
    trouvees: dict[str, str] = {}
    for ligne in acheve.stdout.splitlines():
        champs = ligne.split("\t")
        if len(champs) >= 6 and champs[5]:
            trouvees[champs[2]] = champs[5]
    return trouvees


def regles(chemin: Path) -> dict[str, list[str]]:
    return json.loads(chemin.read_text(encoding="utf-8"))["permissions"]


def prompt_de_session() -> str:
    """Le texte que la session REÇOIT, sans les commentaires du script qui l'entourent.

    La distinction n'est pas cosmétique : `run.sh` commente abondamment ses propres interdits — il
    nomme `veille-arbitre` pour dire qu'il reste hors de portée d'un run —, si bien qu'un `in` sur
    le fichier entier ne dirait pas ce que la session lit. Ce qu'elle lit est le corps du premier
    heredoc `PROMPT`, celui de `prompt_ticket`.
    """
    lignes = RUN_SH.read_text(encoding="utf-8").splitlines()
    debut = next(i for i, ligne in enumerate(lignes) if ligne.strip() == "cat <<PROMPT")
    fin = next(i for i, ligne in enumerate(lignes[debut:], debut) if ligne == "PROMPT")
    return "\n".join(lignes[debut + 1:fin])


# =================================================================================================
# Le lot 1 — l'inventaire rejouable (#789)
# =================================================================================================
# Ce que ce verbe existe pour empêcher est une lecture faite de mémoire, qui se périme au premier
# ticket touchant les listes. Ce que les tests existent pour empêcher, eux, est un rapport MAL
# BRANCHÉ : « aucun écart » est le verdict le plus rassurant qui soit, donc celui qu'il faut
# refuser de croire sur parole.


def test_le_depot_ne_porte_plus_aucun_ecart_imputable_aux_listes() -> None:
    """L'état d'arrivée de #788, et il se lit dans un CODE DE SORTIE, pas dans une prose.

    Le rapport rendait 15 écarts au lot 1, 13 après le lot 4, et 0 depuis que le lot 2 a instruit
    les trous et tranché les cinq `ask`. Ce zéro n'est pas « il n'y a plus d'écart entre un run et
    une session interactive » — l'écart demeure, un run n'ayant toujours pas de répondant — mais
    « plus aucun écart n'est imputable aux LISTES », c'est-à-dire à ce qu'une règle peut changer.

    Le test qui suit prouve que ce zéro n'est pas un artefact : les échantillons fautifs le font
    basculer dans les deux sens.
    """
    verdicts, code = rapport()
    restants = sorted(geste for geste, v in verdicts.items() if v == "ecart")
    assert code == 0, f"des écarts subsistent : {restants}"
    assert verdicts, "aucune ligne rendue — le rapport n'a rien lu"


@pytest.mark.parametrize(
    ("echantillon", "code_attendu"),
    [("tout-couvert", 0), ("rien-couvert", 3)],
)
def test_le_motif_se_prouve_sur_un_echantillon_fautif(echantillon: str, code_attendu: int) -> None:
    """Le verdict BASCULE quand les règles changent — sans quoi il ne les lirait pas.

    C'est la moitié qui manque à tout audit mal branché (méthode de #534/#537, reprise par #366 et
    #578) : un rapport qui rendrait « aucun écart » sans regarder les règles rendrait exactement ce
    que rend le dépôt aujourd'hui. Les deux échantillons sont versionnés et portent leur contrat
    dans leur propre `$comment` ; ce qu'on éprouve ici est que le verbe les LIT.
    """
    _, code = rapport("--regles", str(FIXTURES / f"{echantillon}.json"))
    assert code == code_attendu


def test_une_allowlist_qui_couvre_tout_ne_beni_pas_les_refus_merites() -> None:
    """Le second versant de l'échantillon n°1, et le plus utile des deux.

    Faire basculer le verdict est facile ; ce qui compte est que le rapport ne se TAISE pas quand
    une règle bénit ce qui doit rester refusé. `rm`, `curl` et les deux gestes de G5 sortent alors
    en « contradiction à lever » — la classe écrite dément la règle lue, et c'est le premier
    symptôme d'une raison écrite qui a survécu à la règle qu'elle justifiait.
    """
    trouvees = contradictions("--regles", str(FIXTURES / "tout-couvert.json"))
    for geste in (*REFUS_MERITES, *G5):
        assert trouvees.get(geste) == "voulu", (
            f"« {geste} » est couvert par cette allowlist sans que le rapport le signale : "
            "un refus mérité avalé en silence est ce que la colonne « contradiction » existe pour "
            "empêcher"
        )
    # Contre-épreuve : sur le dépôt, ces mêmes gestes ne sont PAS en contradiction — le signalement
    # dit quelque chose, il ne s'allume pas tout seul.
    assert not contradictions(), "le dépôt ne doit porter aucune contradiction"


def test_un_ask_et_un_deny_ne_disent_pas_la_meme_chose_de_lecart() -> None:
    """La distinction que ce verbe porte, et que `journal.sh refus` a raison de ne pas faire.

    Pour classer un refus DÉJÀ survenu, `ask` et `deny` sont équivalents : personne n'était là dans
    les deux cas. Pour dire ce qu'un run NE PEUT PAS FAIRE, ils s'opposent — un `deny` est un
    interdit des deux côtés, donc pas un écart ; un `ask` est approuvable en interactif, donc
    l'écart lui-même. Les confondre rangerait G1 tout entier parmi les interdits voulus,
    c'est-à-dire hors de ce que le chantier vient corriger.
    """
    verdicts, code = rapport("--regles", str(FIXTURES / "ask-contre-deny.json"))
    assert verdicts["pwd"] == "ecart", "un `ask` est approuvable en interactif : c'est l'écart"
    assert verdicts["rm -rf build"] == "voulu", "un `deny` refuse des deux côtés : pas un écart"
    assert code == 3


def test_des_regles_illisibles_ne_rendent_jamais_aucun_ecart(tmp_path: Path) -> None:
    """Un corpus vide est une LECTURE RATÉE, et le dire est tout l'objet du code 1.

    C'est le pire mode de panne possible pour ce verbe : rendre 0 — « rien à corriger » — parce
    qu'il n'a rien lu. Le code doit donc distinguer « aucun écart » de « pas de verdict », et le
    message nommer le fichier en cause.
    """
    vide = tmp_path / "aucune-regle.json"
    vide.write_text("{}\n", encoding="utf-8")
    acheve = joue("--tsv", "--regles", str(vide))
    assert acheve.returncode == 1, "un corpus vide ne doit jamais valoir « aucun écart »"
    assert "aucune règle lue" in acheve.stderr
    assert not acheve.stdout.strip(), "aucun inventaire ne doit être rendu sans règles"


def test_les_regles_se_lisent_la_ou_elles_vivent() -> None:
    """Les DEUX fichiers, jamais une copie qui dériverait en silence (règle de #307).

    L'`allow` d'une session de run est l'UNION de `.claude/settings.json` et de
    `settings.run.json` — c'est la raison pour laquelle une règle ajoutée d'un seul côté ouvre les
    deux régimes. Le rapport doit donc les nommer tous les deux et compter leur union, faute de
    quoi il jugerait un régime qui n'existe pas.
    """
    acheve = joue()
    assert acheve.returncode == 0, acheve.stderr
    assert ".claude/settings.json" in acheve.stdout
    assert "scripts/orchestrate/settings.run.json" in acheve.stdout
    assert "union, le régime réel d'une session de run" in acheve.stdout


def test_q3_ne_compte_pas_dans_le_code_de_sortie() -> None:
    """Le blocage `.claude/` est un CONSTAT, et l'y compter rendrait le code constant.

    Aucune règle ne le comble — il est en amont des deux listes —, donc un code qui l'inclurait ne
    pourrait jamais descendre à 0 et n'apprendrait plus rien, ni à un humain ni à ce test. Il est
    rendu quel que soit le code, et le dépôt sort aujourd'hui en 0 **avec** sa ligne Q3.
    """
    acheve = joue("--tsv")
    assert acheve.returncode == 0
    lignes_q3 = [x for x in acheve.stdout.splitlines() if x.startswith("Q3\t")]
    assert len(lignes_q3) == 1, "Q3 est rendu quel que soit le code"
    assert lignes_q3[0].split("\t")[1] == "constat", (
        "Q3 n'a pas de verdict : le banc est là, donc le constat est appuyé"
    )


def test_le_rapport_ecrit_le_constat_central_du_chantier() -> None:
    """« Interactif = allow + un répondant. Run = allow, point final. »

    C'est la phrase que #788 existe pour poser, et elle doit vivre dans le rapport lui-même : sans
    elle, les comptes de règles se lisent comme la description d'un seul et même objet, et la
    conclusion évidente — « allongeons la liste » — est fausse.
    """
    texte = joue().stdout
    assert "point final" in texte
    assert "plus contraint" in texte


def test_le_verbe_est_en_lecture_seule_et_hors_ligne() -> None:
    """Il compte des règles et des gestes ; il n'écrit rien, et surtout pas sous `.maestro/`.

    Sa seule sortie est celle qu'il imprime — c'est ce qui le rend rejouable dans n'importe quel
    ordre, y compris deux fois de suite, y compris pendant qu'un run tourne.
    """
    aide = joue("--help")
    assert aide.returncode == 0
    assert "Sans réseau, sans écriture." in aide.stdout


# =================================================================================================
# Le lot 2 — les règles (#790)
# =================================================================================================
# Huit trous comblés, cinq `ask` tranchés. Ce qui se garde n'est pas « la liste a grossi » — un
# diff le dit déjà — mais que chaque ajout reste BORNÉ à sa raison écrite, et que les cinq verdicts
# « on ne change rien » n'aient pas été défaits au passage.


def test_guard_check_ne_rougit_pas_sur_le_depot() -> None:
    """Le `deny` du dépôt est intégralement repris par celui du run, et le hook le double.

    `--check` ne contrôle que le sens dépôt → run : une règle EN PLUS dans `settings.run.json` ne
    le fait pas rougir, mais une règle du dépôt PERDUE, si. C'est ce qui garantit qu'élargir
    l'`allow` d'un run n'a jamais retiré un interdit au passage.
    """
    assert BASH is not None
    acheve = subprocess.run(  # noqa: S603
        [BASH, str(GUARD_SH), "--check"],
        cwd=str(RACINE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert acheve.returncode == 0, acheve.stderr + acheve.stdout


def test_les_huit_trous_mesures_sont_couverts_par_une_regle() -> None:
    """Les gestes que le journal a vus refuser passent désormais — mesure du 2026-08-29.

    Ils sont éprouvés PAR LE RAPPORT et non par une relecture de la liste : c'est le même matching
    que celui du CLI (`permissions.awk`, partagé avec `journal.sh refus`), donc la question posée
    est bien « ce geste passerait-il ? » et non « la chaîne est-elle dans le fichier ? ».
    """
    verdicts, _ = rapport()
    for geste in TROUS_COMBLES:
        assert verdicts.get(geste) == "couvert", f"« {geste} » n'est plus couvert : trou rouvert"


def test_aucune_regle_ajoutee_nelargit_au_dela_de_sa_raison_ecrite() -> None:
    """Le vrai risque du lot 2, et il ne se lit pas dans un diff : une règle TROP LARGE.

    `Bash(rm:*)`, `Bash(bash:*)` ou `Bash(python:*)` combleraient les trous mesurés en avalant au
    passage des refus tranchés ailleurs, chacun avec sa raison. Le rapport le dirait — c'est
    l'objet de sa colonne « contradiction » —, mais le dire n'est pas l'empêcher : ce test-ci
    l'empêche, en exigeant que chacun de ces gestes reste refusé.
    """
    verdicts, _ = rapport()
    for geste in REFUS_MERITES:
        assert verdicts.get(geste) == "voulu", (
            f"« {geste} » est passé de « refusé avec sa raison » à « {verdicts.get(geste)} » : "
            "une règle ajoutée l'a avalé"
        )


def test_les_cinq_ask_du_depot_restent_des_ask() -> None:
    """Le verdict « on ne change rien » du lot 2, gardé COMME VERDICT.

    Les cinq gestes que le dépôt met en `ask` ont été instruits un par un et restent refusés :
    lever la règle ne donnerait pas le geste (`gh issue close`), il contourne la convention de
    commit (`--no-verify`), il jette du travail que plus rien ne retrouve (`reset --hard`,
    `git clean`), ou sa forme couverte est déjà autorisée (`browser_run_code_unsafe`). Un lot qui
    ne laisse aucun diff derrière lui n'est gardé que par un test qui nomme ce qu'il a décidé.
    """
    ask = regles(REGLAGES_DEPOT)["ask"]
    attendus = {
        "Bash(gh issue close:*)",
        "Bash(git commit --no-verify:*)",
        "Bash(git reset --hard:*)",
        "Bash(git clean:*)",
        "mcp__chrome-maestro__browser_run_code_unsafe",
    }
    assert attendus <= set(ask), (
        f"un `ask` du dépôt a disparu : {sorted(attendus - set(ask))} — c'est le renversement d'un "
        "verdict de #790, à faire expressément"
    )


def test_les_cinq_ask_sont_assumes_et_ne_comptent_plus_parmi_les_ecarts() -> None:
    """« Assumé » n'est pas « comblé », et la distinction porte le code de sortie.

    Un `ask` assumé s'affiche encore — la règle est toujours là, l'écart run ↔ interactif aussi —
    mais ne compte plus parmi ce qui ATTEND un arbitrage. Sans cette nuance, cinq refus voulus
    tiendraient le code à 3 pour toujours, et un code qui ne peut pas descendre n'apprend rien.

    Le défaut, lui, reste « écart » : un sixième `ask` ajouté demain sort à trancher sans que
    personne ait à y penser. C'est ce que prouve l'échantillon `ask-contre-deny`, où `Bash(pwd:*)`
    — sans note au catalogue — sort en Q1 avec le verdict « ecart ».
    """
    acheve = joue("--tsv")
    q1 = [x.split("\t") for x in acheve.stdout.splitlines() if x.startswith("Q1\t")]
    assert len(q1) == 5, f"cinq règles `ask`, {len(q1)} lues"
    assert {champs[1] for champs in q1} == {"assume"}, "les cinq sont tranchés (#790)"

    verdicts, _ = rapport("--regles", str(FIXTURES / "ask-contre-deny.json"))
    assert verdicts.get("Bash(pwd:*)") == "ecart", (
        "un `ask` sans verdict écrit doit sortir À TRANCHER — sinon une règle ajoutée demain "
        "entrerait en silence parmi les refus bénis"
    )


def test_lallowlist_interactive_na_pas_bouge_et_cest_une_consequence() -> None:
    """Les huit règles sont dans le fichier du RUN, pas dans celui du dépôt.

    Une session interactive continue donc de demander ces huit gestes : c'est de la friction, et
    elle a un répondant. `settings.run.json` ne sert qu'aux sessions qui n'en ont pas — et
    l'élargissement d'un seul côté est ce qui distingue « combler un trou de run » d'« ouvrir un
    geste à tout le monde ».
    """
    allow_depot = regles(REGLAGES_DEPOT)["allow"]
    allow_run = regles(REGLAGES_RUN)["allow"]
    ajouts = {"Bash(pwd:*)", "Bash(cut:*)", "Bash(tr:*)", "Bash(chmod:*)", "Bash(git mv:*)",
              "Bash(git restore:*)", "Bash(git merge-base:*)", "Bash(git check-ignore:*)"}
    assert ajouts <= set(allow_run), f"règle perdue côté run : {sorted(ajouts - set(allow_run))}"
    assert not (ajouts & set(allow_depot)), (
        "ces huit règles ont été ajoutées au régime SANS RÉPONDANT ; les porter aussi côté dépôt "
        "changerait le régime interactif, ce que #790 n'a pas décidé"
    )


def test_le_prompt_de_run_interdit_dentamer_ticket_abandon() -> None:
    """La seule pièce mobile du verdict sur `gh issue close`, et elle est dans le prompt.

    Le refus tombe à l'ÉTAPE 7 de `/ticket-abandon`, après que l'étape 6 a posé « Abandonné » : une
    session qui entame la séquence laisse un ticket abandonné ET ouvert, que `ferme-parent` compte
    comme un lot encore ouvert — son parent ne se refermerait plus jamais. Un refus franc vaut
    mieux qu'un refus tardif.
    """
    prompt = prompt_de_session()
    assert "N'ENTAME PAS /ticket-abandon" in prompt
    assert "ferme-parent" in prompt, "la conséquence est dite, pas seulement l'interdit"
    assert "ORCHESTRATE: ECHEC" in prompt, "la sortie prescrite à la place, jamais un contournement"


def test_le_prompt_de_run_nomme_le_seul_rattrapage_ouvert() -> None:
    """Refuser trois gestes sans en désigner un quatrième déplace le refus, il ne le résout pas.

    C'est la règle de l'atelier de session (#307) appliquée au rattrapage : `reset --hard`,
    `git clean` et `git stash` sont refusés, donc le prompt doit dire ce qui reste — `git restore
    <fichier>`, qui vise un fichier NOMMÉ — et pourquoi le stash est le pire des trois : il vit
    dans le dépôt commun, invisible pour le ramassage des worktrees, qui tient alors le worktree
    pour propre et l'emporte avec le travail.
    """
    prompt = prompt_de_session()
    assert "git restore <fichier>" in prompt
    assert "git stash" in prompt
    assert "COMMITE-LE" in prompt


# =================================================================================================
# Le lot 3 — « on n'ouvre pas », gardé comme verdict (#791)
# =================================================================================================
# Le lot n'a livré aucun code : il a mesuré à nouveau, chiffré les deux coûts côte à côte, et
# décidé de ne rien changer. C'est le verdict le plus fragile du chantier — rien ne le distingue
# d'un oubli —, donc celui qui demande le plus de gardes.


def test_le_regime_des_sessions_de_run_nouvre_pas_claude() -> None:
    """`bypassPermissions` ouvrirait `.claude/`, au prix de vider l'`allow` de son sens.

    #614 a mesuré que c'est le seul régime qui lève le blocage ; #791 a chiffré ce qu'il coûte —
    86 règles `allow` ramenées à 0, et 3 des 5 `ask` du dépôt devenus des oui silencieux, `guard.sh`
    ne jugeant que les appels `Bash`. Ce n'est pas un réglage, c'est un renversement de politique :
    une liste blanche échangée contre une liste noire.

    ⚠ Ce test n'interdit pas d'y revenir : il demande qu'on le fasse EXPRÈS, sur un fait nouveau —
    des butées qui reprennent, ou un `guard.sh` qui jugerait autre chose que `Bash`.
    """
    texte = RUN_SH.read_text(encoding="utf-8")
    assert "bypassPermissions" not in texte, (
        "le régime de permission d'un run a été ouvert : c'est le renversement du verdict de #791, "
        "à instruire par un ticket et non par une ligne"
    )
    for chemin in (REGLAGES_RUN, REGLAGES_DEPOT):
        assert "bypassPermissions" not in chemin.read_text(encoding="utf-8"), chemin.name
    # Contre-épreuve : le fichier lu est bien celui qui porte le régime des sessions.
    assert "--permission-mode" in texte or "--settings" in texte, (
        "run.sh ne règle plus le régime des sessions : test creux"
    )


def test_le_banc_qui_rouvrirait_le_dossier_est_dans_le_depot() -> None:
    """Un verdict « on n'ouvre pas » ne tient que si l'on peut remesurer sans refaire l'enquête.

    C'est la règle de #614 et de tout ce chantier : mesurer plutôt que raisonner à distance. Le
    banc est versionné, il porte son résultat daté, et le rapport le VÉRIFIE — sa ligne Q3 bascule
    en « inconnu » si le fichier disparaît, une citation dont le banc a disparu étant orpheline.
    """
    assert BANC.exists(), "le banc de #614/#791 a disparu : plus rien n'appuie le verdict"
    texte = BANC.read_text(encoding="utf-8")
    assert "ON N'OUVRE PAS" in texte, "le banc mesure ; le verdict qu'il éclaire doit y être écrit"
    assert "#791" in texte and "2026-08-30" in texte, "la mesure porte sa date"


def test_le_rapport_dit_que_le_blocage_claude_reste_entier() -> None:
    """Q3 est un constat, et son texte est ce qu'on lira avant de retenter l'ouverture.

    Il nomme les quatre voies mesurées et fermées — règle nue, règle à chemin explicite (relative
    comme absolue), hook rendant `allow` — pour qu'une session qui bute n'aille pas les réessayer
    une à une.
    """
    texte = joue().stdout
    assert "en amont" in texte and "hooks" in texte
    assert "bypassPermissions" in texte, "la seule voie qui ouvre est nommée, avec son prix"
    assert "reste-claude" in texte, "le support qui fait survivre le résidu est vérifié sur disque"


# =================================================================================================
# Le lot 5 — une question rencontrée par un run lui survit (#795)
# =================================================================================================
# Décalque de `reste-claude` (#610), et la parenté n'est pas une coquetterie : le contenant qui a
# échoué pour le résidu `.claude/` est le même que celui qui échouait pour la veille — un résumé de
# fin de session, puis une PR mergée dans l'heure. Ce qui se garde ici est la propriété qui
# distingue les deux supports : le ticket SURVIT, le résumé non.


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


def regle_source(
    iid: str, titre: str, commentaires: tuple[str, ...] = (), existe: bool = True
) -> dict:
    """Réponse à la lecture UNIQUE du verbe : « ce ticket existe-t-il, et a-t-il déjà sa veille ? ».

    Les deux questions tiennent dans un aller, et c'est ce qui permet aux refus de tomber avant
    toute écriture sans rien coûter de plus. Le marqueur (« ticket de veille #<n> ») est cherché
    dans les COMMENTAIRES — la forme que pose l'ancre, et le contrat que relit le rejeu.
    """
    issue = None if not existe else {
        "title": titre,
        "comments": {"nodes": [{"body": corps} for corps in commentaires]},
    }
    return {
        "contient": [f"issue(number:{iid})", "comments(first: 100)"],
        "reponse": {"data": {"repository": {"issue": issue}}},
    }


def regle_jalons(courant: str = "Phase 8 — Control Tower", numero: int = 21) -> list[dict]:
    """Le jalon du rail PRODUIT — une veille porte sur un écran, donc sur le produit (#617)."""
    return [
        {
            "contient": ["orderBy: {field: DUE_DATE"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {
                    "title": courant,
                    "description": "Control Tower",
                    "state": "OPEN",
                    "dueOn": "2027-09-15T00:00:00Z",
                    "total": {"totalCount": 12},
                    "fermes": {"totalCount": 4},
                },
            ]}}}},
        },
        {
            "contient": ["milestones(first: 50) { nodes { number title }"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {"number": numero, "title": courant},
            ]}}}},
        },
    ]


def regles_projet(iid_cree: str) -> list[dict]:
    """De quoi jouer `gl_project_add` sur le ticket de veille qui vient de naître (#361)."""
    return [
        {"contient": ["options{id name}"], "brut": "\n".join(lignes_projet()) + "\n"},
        {
            "contient": [f"issue(number:{iid_cree}) {{ id }}"],
            "reponse": {"data": {"repository": {"issue": {"id": "I_veille"}}}},
        },
        {
            "contient": ["addProjectV2ItemById"],
            "reponse": {"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_veille"}}}},
        },
        regle_pose_status(),
    ]


def instruit(depot: Depot, *, source: str = "698", veille: str = "1", **plus: object) -> None:
    """Le `gh` factice prêt pour une CRÉATION nominale : source lisible, jalon produit, projet."""
    depot.pose_etat(
        graphql=[
            regle_source(source, "Le fil accepte fichiers et images"),
            *regle_jalons(),
            *regles_projet(veille),
        ],
        **plus,
    )


def constat(depot: Depot, nom: str, texte: str) -> str:
    """Écrit le constat dans l'atelier de session, chemin RELATIF — le régime réel (§11.7)."""
    chemin = depot.racine / ".maestro" / "session" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8", newline="\n")
    return str(chemin.relative_to(depot.racine)).replace("\\", "/")


def _post_issues(depot: Depot) -> list[str]:
    return [ligne for ligne in depot.appels()
            if "-X\tPOST" in ligne and "\trepos/equipe-test/maestro/issues\t" in ligne + "\t"]


def _commentaires(depot: Depot, iid: str) -> list[str]:
    return [ligne for ligne in depot.appels()
            if "-X\tPOST" in ligne and f"issues/{iid}/comments" in ligne]


def test_une_question_rencontree_par_un_run_lui_survit(depot: Depot) -> None:
    """Le ticket de veille naît ASSIGNÉ et POURVU D'UN ÉTAT — les deux mêmes propriétés que #610.

    **Assigné** : c'est ce qui le tient hors des plans de `queue.sh` (« À faire ET libre »), et ça
    compte double ici — un run qui le prendrait ne pourrait pas jouer la veille non plus, il
    échouerait et ferait sauter les lots suivants de son parent (mesuré, #724). **Pourvu d'un
    état** : rien côté forge n'en pose, et un ticket sans état ne remonte dans aucune vue.

    Le rail est SU et non deviné : une veille porte sur un écran, donc sur le produit (#617).
    """
    instruit(depot)
    chemin = constat(depot, "veille.md", "Fil de conversation : pas de référence vérifiée.\n")

    acheve = depot.lib("veille-differe", "698", chemin)
    assert acheve.returncode == 0, acheve.stderr

    creation = _post_issues(depot)
    assert len(creation) == 1, f"une création et une seule — {creation}"
    assert f"assignees[]={MOI}" in creation[0], "un ticket LIBRE serait prenable par un run"
    assert "labels[]=agent::design" in creation[0]
    assert "labels[]=type::doc" in creation[0]
    assert "milestone=21" in creation[0], "le rail est produit, pas outillage"

    mutations = [ligne for ligne in depot.appels() if "updateProjectV2ItemFieldValue" in ligne]
    assert len(mutations) == 1, "l'état est posé dans la foulée de la création (#361)"

    ancres = _commentaires(depot, "698")
    assert len(ancres) == 1, (
        "la source nomme sa veille — sans l'ancre, un rejeu ouvrirait un doublon"
    )
    assert "ticket de veille #1" in ancres[0].replace("\\n", "\n")


def test_consigner_nest_pas_arbitrer(depot: Depot) -> None:
    """LE partage du lot, et il se prouve par une absence : `veille::arbitree` n'est PAS posé.

    Poser le label fermerait la question sans que personne l'ait jugée — le « marquer d'office »
    que #562 a écarté nommément. Le run diffère ; l'arbitrage reste un geste humain, et
    `/ticket-start` reposera la question au prochain démarrage sur cette surface.
    """
    instruit(depot)
    chemin = constat(depot, "veille.md", "constat\n")
    assert depot.lib("veille-differe", "698", chemin).returncode == 0

    for appel in depot.appels():
        assert "veille::arbitree" not in appel, (
            "le verbe a posé l'arbitrage : consigner une question et la trancher sont deux gestes, "
            "et le second n'appartient pas à un run"
        )
    # Contre-épreuve : le label existe bien et le verbe qui le pose est ailleurs — le motif
    # cherché n'est pas une chaîne morte.
    assert "veille::arbitree" in (RACINE / "scripts" / "gitlab" / "lib.sh").read_text(
        encoding="utf-8"
    ), "le label a disparu du dépôt : ce test ne garde plus rien"


def test_un_run_sans_question_necrit_rien(depot: Depot) -> None:
    """L'abstention est le cas NOMINAL, et elle doit être gratuite autant que muette.

    Un run qui ne rencontre aucune veille ne doit rien écrire du tout : le verbe n'est appelé que
    sur le signalement de `start-brief`. Ce qui s'éprouve ici est l'autre moitié — les deux refus
    (fichier absent, fichier vide) tombent AVANT le moindre appel de forge, si bien qu'un appel
    fautif ne laisse jamais un demi-ticket derrière lui.
    """
    instruit(depot)
    acheve = depot.lib("veille-differe", "698", ".maestro/session/jamais-ecrit.md")
    assert acheve.returncode == 4, acheve.stderr
    assert not depot.appels(), "un refus doit tomber sans rien demander à la forge"

    vide = constat(depot, "vide.md", "")
    acheve = depot.lib("veille-differe", "698", vide)
    assert acheve.returncode == 4
    assert not ecritures(depot), "un constat vide n'apprend rien de plus que `touche-surface`"


def test_rejoue_a_lidentique_il_necrit_rien(depot: Depot) -> None:
    """L'idempotence, ancrée sur le TICKET SOURCE et non sur une recherche.

    L'index de GitHub est asynchrone : deux appels rapprochés y trouveraient « rien », c'est-à-dire
    le doublon que la propriété interdit. L'ancre posée en commentaire, elle, est lisible tout de
    suite — et c'est elle que relit le rejeu.
    """
    texte = "Fil de conversation : pas de référence vérifiée.\n"
    chemin = constat(depot, "veille.md", texte)
    # L'empreinte est « <somme cksum>-<taille> » : la taille en fait partie, et c'est ce qui
    # distingue deux constats que le CRC seul rapprocherait.
    somme, taille = subprocess.run(  # noqa: S603
        [BASH or "bash", "-c", f'cksum < "{chemin}"'],
        cwd=str(depot.racine), capture_output=True, text=True, check=False,
    ).stdout.split()[:2]
    empreinte = f"{somme}-{taille}"

    depot.pose_etat(graphql=[
        regle_source("698", "Le fil accepte fichiers et images",
                     ("Question différée dans son ticket de veille #7 (#795).",)),
        {
            "contient": ["issue(number:7)"],
            "reponse": {"data": {"repository": {"issue": {
                "title": "Veille de conception à jouer (#698)",
                "body": f"## Constat — empreinte {empreinte}\n\n{texte}",
            }}}},
        },
    ])
    acheve = depot.lib("veille-differe", "698", chemin)
    assert acheve.returncode == 0, acheve.stderr
    assert not ecritures(depot), "le même constat rejoué ne doit rien réécrire"


def test_le_prompt_de_run_prescrit_de_differer_en_plus_du_resume(depot: Depot) -> None:
    """« En plus », jamais « à la place » — et c'est ce qui a changé au lot 5.

    Le prompt disait déjà « nomme ce ticket dans ton résumé final », c'est-à-dire le contenant que
    #608 venait de juger insuffisant : les sessions ont TENU cette conduite (le résumé de #698 le
    dit en toutes lettres) et personne ne l'a lu. Le résumé reste utile — il se lit dans la console
    d'un run —, mais il ne survit pas ; le ticket, si.
    """
    prompt = prompt_de_session()
    assert "veille-differe" in prompt, (
        "le verbe n'est prescrit nulle part : le support est inatteignable"
    )
    assert "Nomme-le quand même dans ton résumé final" in prompt, "le résumé n'est pas remplacé"
    # Les deux verbes se ressemblent et ne font pas la même chose : le prompt doit prescrire l'un
    # ET interdire l'autre dans la même phrase, faute de quoi une session « ferait propre » en
    # enregistrant un arbitrage que personne n'a rendu.
    assert "N'enregistre AUCUN arbitrage" in prompt
    assert "veille-arbitre" in prompt and "fermerait la" in prompt, (
        "l'interdit doit NOMMER le verbe qu'il vise — « n'arbitre pas » sans son nom se relit mal "
        "à côté d'un « veille-differe » prescrit deux lignes plus bas"
    )
    assert "Tu traites intégralement le ticket" in prompt, "ce n'est pas le prompt qui a été lu"


# =================================================================================================
# G5 — l'interdit voulu, écrit là où on le lirait avant de le défaire (#788)
# =================================================================================================
# C'est le seul des cinq écarts qui risque d'être « corrigé » par erreur : il ressemble à un trou
# — deux verbes du dépôt refusés à une session — et c'en est l'inverse. La parité serait ici une
# régression, et trois endroits doivent le dire : la règle, le rapport, la doc.


def test_g5_est_refuse_par_une_regle_et_non_par_un_oubli() -> None:
    """Les deux verbes sont refusés PAR UNE RÈGLE, donc l'interdit est explicite et se lit.

    `Bash(bash scripts/gitlab/lib.sh:*)` couvre le préfixe ; c'est le `deny` du run qui reprend
    nommément les deux verbes, et l'ordre de décision du rapport (deny avant ask avant allow) est
    ce qui fait que le plus restrictif l'emporte.
    """
    deny = regles(REGLAGES_RUN)["deny"]
    for verbe in ("merge-mr", "pipeline-wait"):
        assert any(verbe in regle for regle in deny), (
            f"« {verbe} » n'est plus refusé en session de run : ce n'est pas un trou comblé, c'est "
            "G5 défait — le merge appartient au pilote (#419)"
        )

    # Et la raison vit À CÔTÉ de la règle, dans le `$comment` du fichier : c'est le premier endroit
    # que lira quelqu'un venu retirer les deux lignes du `deny`, avant même d'ouvrir la doc.
    commentaire = "\n".join(json.loads(REGLAGES_RUN.read_text(encoding="utf-8"))["$comment"])
    assert "brûle du quota" in commentaire and "périment mutuellement" in commentaire, (
        "les deux raisons ne sont plus écrites à côté de la règle : un `deny` sans motif se retire "
        "sans qu'on sache ce qu'on défait"
    )
    assert "N'EXISTENT QUE POUR UN RUN" in commentaire, (
        "l'asymétrie est le cœur du sujet — les deux verbes sont légitimes en interactif, et c'est "
        "ce qui fait ressembler leur refus à un trou"
    )


def test_le_rapport_range_g5_en_interdit_voulu_jamais_parmi_les_manquants() -> None:
    """Ranger G5 parmi les écarts enverrait quelqu'un « corriger » ce qui est juste.

    C'est la raison d'être de la colonne « voulu » : le rapport DISTINGUE l'écart de l'interdit
    décidé, et il rend la raison avec le verdict — le quota brûlé à attendre, et les verdicts de
    conflit que N sessions qui mergent périment mutuellement.
    """
    verdicts, _ = rapport()
    for geste in G5:
        assert verdicts.get(geste) == "voulu", f"« {geste} » rangé en « {verdicts.get(geste)} »"

    texte = joue().stdout
    assert "G5" in texte and "INTERDIT VOULU" in texte
    assert "la parité serait ici une régression" in texte.lower()


def test_g5_est_ecrit_dans_la_doc_avec_sa_raison() -> None:
    """La règle refuse, le rapport le dit — mais c'est la DOC qu'on lit avant de changer une règle.

    Les deux raisons doivent y être, parce que chacune suffit et qu'aucune ne se devine : une
    session qui attend un pipeline brûle du quota à ne rien faire en tenant un worktree et un
    créneau de concurrence ; à N tickets en vol, N sessions qui mergent périment mutuellement leur
    verdict de conflit. Le pilote, lui, sérialise et attend hors quota.
    """
    texte = DOC10.read_text(encoding="utf-8")
    bloc = texte[texte.index("### 11.7"):texte.index("### 11.8")]
    assert "G5" in bloc, "G5 n'est nommé nulle part dans §11.7 : il se lira comme un trou"
    assert "brûle du quota" in bloc or "brûler du quota" in bloc
    assert "périment mutuellement" in bloc
    assert "régression" in bloc


# =================================================================================================
# La doc — les trois documents disent la même chose (#793)
# =================================================================================================


def test_le_constat_central_est_ecrit_en_tete_de_11_7() -> None:
    """La phrase qui manquait — son absence faisait lire les deux listes comme un seul objet.

    §11.7 explique depuis #235 comment instruire les refus d'un run. Ce qu'elle ne disait pas est
    POURQUOI la même liste ne décrit pas le même objet des deux côtés — donc pourquoi l'écart ne se
    comble pas en l'allongeant. Ce qui manque à un run n'est pas une permission, c'est un
    répondant.
    """
    texte = DOC10.read_text(encoding="utf-8")
    bloc = texte[texte.index("### 11.7"):texte.index("### 11.8")]
    assert "point final" in bloc, "le constat central de #788 n'est pas écrit"
    assert "répondant" in bloc
    assert "ecart-run.sh" in bloc, "le verbe qui rejoue l'inventaire doit être nommé là"


def test_les_trois_documents_ne_se_contredisent_pas_sur_le_web() -> None:
    """docs/10 §11.7, docs/30 §5.2 et CLAUDE.md disent le même verdict, et le même partage.

    Le risque n'est pas qu'un document se taise — c'est qu'il garde la version d'avant : #714
    rangeait `WebFetch` sous la veille, et #792 a établi que son seul usage mesuré n'en est pas
    une. Un document resté sur la première lecture enverrait rouvrir le dossier par le mauvais
    bout.
    """
    for chemin in (DOC10, DOC30, CLAUDE_MD):
        texte = chemin.read_text(encoding="utf-8")
        assert "WebFetch" in texte and "WebSearch" in texte, chemin.name
        assert "#792" in texte, f"{chemin.name} ne cite pas l'arbitrage qui a tranché"
    # Les deux gestes sont arbitrés SÉPARÉMENT : un document qui n'en parlerait qu'au pluriel
    # aurait perdu ce que #792 a apporté.
    assert "séparément" in DOC10.read_text(encoding="utf-8")


def test_claude_md_porte_le_constat_central_et_g5() -> None:
    """CLAUDE.md est ce qu'une session lit en premier — et c'est là qu'on ira défaire G5.

    Les deux ajouts vont ensemble : le constat central explique pourquoi l'écart ne se comble pas
    en allongeant une liste, et G5 dit lequel des cinq écarts ne se comble pas DU TOUT. Séparés,
    le second se lit comme un trou qu'on n'aurait pas encore eu le temps de boucher.
    """
    texte = CLAUDE_MD.read_text(encoding="utf-8")
    assert "point final" in texte and "répondant" in texte, "le constat central de #788 manque"
    assert "ecart-run.sh" in texte, "le verbe qui rejoue l'inventaire n'est nommé nulle part"

    garde_fous = texte[texte.index("## Garde-fous (autonomie sous supervision)"):]
    assert "ne sont PAS un écart à combler" in garde_fous, (
        "G5 doit vivre dans les GARDE-FOUS : c'est la section qu'on relit avant d'élargir une "
        "permission, et la seule où l'interdit se lira comme voulu plutôt que comme un oubli"
    )
    assert "brûler du quota" in garde_fous and "périment mutuellement" in garde_fous


def test_claude_md_dit_le_verdict_du_regime_de_permission() -> None:
    """« On n'ouvre pas » est une décision, pas une question laissée en suspens (#791).

    CLAUDE.md est ce qu'une session lit en premier : c'est là que doit se lire ce qui rouvrirait le
    dossier — un fait, jamais une intuition — et le prix chiffré de l'ouverture.
    """
    texte = CLAUDE_MD.read_text(encoding="utf-8")
    assert "#791" in texte
    assert "bypassPermissions" in texte, "la voie mesurée est nommée, avec son prix"
    assert "86 règles" in texte or "86 règles `allow`" in texte
