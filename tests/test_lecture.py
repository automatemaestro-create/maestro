"""« Lire n'est pas exécuter » : la règle, et le hook qui l'applique (#1197).

#1102 avait posé la règle dans le prompt d'une fiche outillée, et la réserve a été
fermée là-dessus. Le 2026-09-22, le banc des scénarios de référence a montré que le
comportement ne suivait pas : le premier geste de l'agent de S3 est un `find` au
shell, donc une demande de validation ; personne ne répond pendant une tâche
(EF-08), et le run reste « en attente d'arbitrage ». Cette suite tient le
renversement — la règle est exécutée, pas seulement écrite — et les **trois bornes**
qui l'empêchent d'ouvrir quoi que ce soit.

Quatre parties :

① ce que le module reconnaît comme une lecture, en partant des commandes
  **réellement observées** dans le passage rouge ;
② ce qu'il refuse de reconnaître, et le fait qu'il se trompe toujours du même
  côté — vers l'arbitrage, jamais vers le laissez-passer ;
③ chaque option « écrivante » déclarée, prouvée sur un échantillon fautif : une
  entrée de la liste qui ne servirait à rien serait un garde-fou de façade ;
④ le hook du fournisseur, où la dispense s'arrête à la politique et au périmètre.
"""

from __future__ import annotations

import asyncio

import pytest

from maestro.agents.permissions import EntreeArbitrage, PolitiqueOutils
from maestro.decideur import Decideur
from maestro.lecture import (
    GIT_LECTURE,
    OUTIL_LECTURE,
    VERBES_LECTURE,
    commande_de,
    est_lecture,
    lecture_sans_arbitrage,
)
from maestro.providers import claude as claude_mod

#: Les gestes **réellement observés** sur la vraie stack, chacun avec son passage
#: du banc. Ce ne sont pas des exemples inventés : ce sont les commandes qui ont
#: fait attendre un humain, et chacune doit passer.
#:
#: - `S3`/`S1` : passage `20260922-100639`, celui qui a ouvert le ticket ;
#: - `PRUNE_*` et `DEV_NULL` : passage `20260922-115803`, le premier joué **avec**
#:   la règle. Elles ont appris deux choses qu'aucun exemple écrit à la table
#:   n'aurait données — un agent nomme les chemins exclus *pour les éviter*, et
#:   il jette le bruit d'un `find` dans `/dev/null` ;
#: - `TEST_F` : passage `20260922-164402`, la réserve R1 du bouclage (#1211) —
#:   l'agent vérifie qu'un skill existe, et `test` n'était pas un verbe connu.
COMMANDE_S3 = (
    "find . -not -path './.git*' -not -path './.maestro*' | sort; "
    'echo "---agents skills---"; find .agents -maxdepth 4'
)
COMMANDE_S1 = (
    'ls -la .git; ls -la notes; ls -la .maestro; git status; find . -maxdepth 1 -name ".*"'
)
COMMANDE_PRUNE_FICHIERS = (
    "find . -path ./.git -prune -o -type f -print | grep -v node_modules | sort | head -200"
)
COMMANDE_PRUNE_DOSSIERS = (
    "find . -path ./.git -prune -o -type d -print | grep -v node_modules | sort"
)
COMMANDE_DEV_NULL = (
    'find / -maxdepth 2 -iname "*.agents*" 2>/dev/null; '
    'find / -iname "SKILL.md" 2>/dev/null | head -20'
)
COMMANDE_LS_ATELIER = "ls -la src; echo '---'; ls -la .maestro"
COMMANDE_TEST_F = (
    "ls -la .agents/skills/ 2>/dev/null; "
    'test -f .agents/skills/mettre-en-route/SKILL.md && echo "skill present"'
)
COMMANDE_CD_RACINE = (
    'cd "/c/Users/Sam25/maestro-scenarios/20260922-121246/s3-sans-equipe" '
    "&& ls -la .maestro 2>/dev/null; find .maestro -maxdepth 3 2>/dev/null"
)


# --- ① Ce qui est une lecture ----------------------------------------------


@pytest.mark.parametrize(
    ("releve", "commande"),
    [
        ("S3 (20260922-100639)", COMMANDE_S3),
        ("S1 (20260922-100639)", COMMANDE_S1),
        ("prune fichiers (c4a4c14806a3)", COMMANDE_PRUNE_FICHIERS),
        ("prune dossiers (c4a4c14806a3)", COMMANDE_PRUNE_DOSSIERS),
        ("find / 2>/dev/null (c4a4c14806a3)", COMMANDE_DEV_NULL),
        ("ls atelier (c4a4c14806a3)", COMMANDE_LS_ATELIER),
        ("cd racine && ls (a71c7db011a4)", COMMANDE_CD_RACINE),
        ("test -f du skill (20260922-164402)", COMMANDE_TEST_F),
    ],
)
def test_les_gestes_observes_sur_la_vraie_stack_sont_des_lectures(
    releve: str, commande: str
) -> None:
    """Le cœur du ticket, sur pièces : chacune de ces commandes a **réellement**
    fait attendre un humain pendant un run, et aucune n'écrit quoi que ce soit.

    Deux d'entre elles nomment un chemin que le périmètre exclut (`.git`,
    `node_modules`) — et le nomment **pour l'éviter**. C'est ce relevé qui a fait
    écarter une garde « aucun chemin exclu nommé » : elle aurait refusé le geste
    juste sans fermer le mauvais (`grep -rn motif .` lit le `.env` sans le
    nommer)."""
    assert est_lecture(commande), releve


@pytest.mark.parametrize(
    "commande",
    [
        "ls",
        "ls -la",
        "pwd",
        "cat .agents/skills/mettre-en-route/SKILL.md",
        "head -n 40 README.md",
        "tail -n 5 CHANGELOG.md",
        "wc -l src/app.py",
        "find . -maxdepth 2 -name '*.py'",
        "grep -rn 'def ' src",
        "rg --files src",
        "tree -L 2",
        "du -sh src",
        "stat pyproject.toml",
        "file src/app.py",
        "git status",
        "git --no-pager log --oneline -5",
        "git diff --stat",
        "git ls-files",
        "ls src | sort | head -n 3",
        "cat README.md && ls src",
        "cat manquant.txt || ls",
        "test -f .agents/skills/mettre-en-route/SKILL.md",
        "[ -f .agents/skills/mettre-en-route/SKILL.md ] && echo ok",
        "test -d src || ls",
        "[ -e pyproject.toml ] && cat pyproject.toml",
        "test -x scripts/run.sh",  # demander si l'on pourrait n'est pas le faire
        "[ -w notes ]",
        "[ ! -s notes.md ]",
    ],
)
def test_lister_chercher_ouvrir_ne_demande_personne(commande: str) -> None:
    """Le comportement attendu du ticket, verbe par verbe : *lister, chercher,
    ouvrir un fichier*. Les enchaînements en font partie — un agent compose sa
    reconnaissance en une commande, et juger chaque maillon est ce qui permet de
    la laisser passer entière."""
    assert est_lecture(commande), commande


def test_chaque_verbe_declare_lit_vraiment() -> None:
    """Aucune entrée de la liste n'est décorative : appelée nue, chacune est une
    lecture. Un verbe ajouté sans que le module sache le juger se verrait ici."""
    for verbe in VERBES_LECTURE:
        commande = "git status" if verbe == "git" else verbe
        assert est_lecture(commande), verbe


def test_chaque_sous_commande_git_declaree_lit_vraiment() -> None:
    for sous in GIT_LECTURE:
        assert est_lecture(f"git {sous}"), sous


# --- ② Ce qui n'en est pas — et le sens dans lequel on se trompe ------------


@pytest.mark.parametrize(
    "commande",
    [
        "rm -rf /srv",
        "pip install -e .",
        "python app.py",
        "npm test",
        "mkdir notes",
        "mv src dist",
        "./script.sh",
        "/bin/ls",
        "FOO=bar ls",  # un préfixe d'environnement n'est pas un verbe de lecture
        "sudo ls",
        "xargs ls",
        "sh -c 'ls'",
        "sed -n '1,10p' README.md",  # `sed -i` écrit : le verbe entier reste dehors
        "awk '{print > \"f\"}' x",  # ce qu'awk écrit est à l'abri des guillemets
        "uniq entree sortie",  # le second argument positionnel est un fichier écrit
        "less README.md",  # ne rend jamais la main sans terminal
        "env",  # l'environnement d'un agent porte ses secrets (#109)
        "printenv",
        "[[ -f notes.md ]]",  # mot-clé qui évalue ses opérandes (mesuré, #1211)
        "[[ $x -eq 0 ]]",
    ],
)
def test_ce_qui_n_est_pas_une_lecture_garde_son_regime(commande: str) -> None:
    assert not est_lecture(commande), commande


@pytest.mark.parametrize(
    "commande",
    [
        "echo coucou > fichier.txt",  # redirection vers un fichier
        "echo coucou >> fichier.txt",
        "ls > 1",  # un chiffre en cible reste un fichier nommé « 1 »
        "ls &",  # arrière-plan
        "(ls)",  # sous-shell
        "cat < fichier",  # entrée redirigée : on ne sait pas lire, donc on n'affirme pas
        "cat $(ls)",  # substitution : ce qu'elle exécute n'est pas dans le texte
        'cat "$(ls)"',  # … même cachée dans des guillemets
        "cat `ls`",
        "cat 'fichier",  # guillemets déséquilibrés
        "",
        "   ",
    ],
)
def test_un_texte_qu_on_ne_sait_pas_lire_repart_vers_l_arbitrage(commande: str) -> None:
    """Le défaut est l'arbitrage, jamais le laissez-passer : c'est l'asymétrie
    d'EF-08. Tout ce que le module ne **reconnaît** pas rend faux, donc laisse le
    régime exactement là où il était."""
    assert not est_lecture(commande), commande


@pytest.mark.parametrize(
    "commande",
    ["ls && rm -rf x", "rm -rf x && ls", "ls | xargs rm", "git status; git push"],
)
def test_un_maillon_qui_agit_emporte_toute_la_commande(commande: str) -> None:
    """Même règle que la couche permissions : une commande composée vaut son
    maillon le plus faible."""
    assert not est_lecture(commande), commande


@pytest.mark.parametrize(
    "commande",
    [
        "find / -iname 'SKILL.md' 2>/dev/null",
        "ls -la 2> /dev/null",
        "ls >/dev/null 2>&1",
        "grep -rn motif src &> /dev/null",
    ],
)
def test_jeter_ce_qui_sort_n_est_pas_ecrire(commande: str) -> None:
    """`2>/dev/null` est l'idiome d'un `find` qui traverse un disque : il n'écrit
    nulle part. Le refuser faisait attendre un humain sur la commande la plus
    ordinaire qui soit — mesuré sur le run `c4a4c14806a3`."""
    assert est_lecture(commande), commande


def test_le_descripteur_d_une_redirection_ne_devient_pas_un_argument() -> None:
    """Le `2` de `2>/dev/null` repart avec sa redirection : laissé dans les
    arguments du verbe, il s'y lirait comme un chemin."""
    assert est_lecture("sort fichier 2>/dev/null")
    # … et la redirection écrivante reste refusée, chiffre ou pas.
    assert not est_lecture("sort fichier 2>trace.log")


@pytest.mark.parametrize("sous", ["push", "commit", "add", "config", "remote", "tag", "clean"])
def test_une_sous_commande_git_non_declaree_n_est_pas_une_lecture(sous: str) -> None:
    """`git` est le seul verbe dont le sens tient au second mot : `status` et
    `push` n'ont en commun que leur exécutable."""
    assert not est_lecture(f"git {sous}")


@pytest.mark.parametrize(
    "commande",
    [
        "git --no-pager status",
        "git -C . status",
        "git -C /autre/depot log --oneline",
        "git --git-dir=.git ls-files",
        "git --work-tree . status",
        # Le `-C` d'une globale n'est pas le `-C` qui copie une branche : les
        # options se jugent sur ce qui **suit** la sous-commande.
        "git -C . branch -a",
    ],
)
def test_les_options_globales_de_git_se_franchissent_pour_aller_a_la_sous_commande(
    commande: str,
) -> None:
    """La valeur d'une globale occupe la place où l'on cherche la sous-commande :
    `git -C . status | head -20` s'est fait arbitrer pour cette seule raison sur
    le run `5508ebb01cb8`, alors qu'il ne fait que lire."""
    assert est_lecture(commande), commande


@pytest.mark.parametrize(
    "commande",
    [
        "git -c core.pager=rm log",  # `-c` choisit une commande que git lancera
        "git -c alias.x=status x",
        "git --exec-path=/tmp status",  # globale inconnue : on ne devine pas
        "git -C",  # une valeur manquante n'est pas une sous-commande
        "git",
    ],
)
def test_une_globale_qu_on_ne_sait_pas_lire_renvoie_a_l_arbitrage(commande: str) -> None:
    """`-c core.pager=<commande>` fait **exécuter** : une option qui lance n'est
    pas une option de lecture. Ne pas savoir n'est jamais une raison de laisser
    passer."""
    assert not est_lecture(commande), commande


# --- ③ Les options écrivantes, prouvées sur un échantillon fautif ----------


@pytest.mark.parametrize(
    ("lisant", "ecrivant"),
    [
        ("sort fichier", "sort -o sortie fichier"),
        ("sort fichier", "sort --output=sortie fichier"),
        ("sort -u fichier", "sort -uo sortie fichier"),  # option courte groupée
        ("tree -L 2", "tree -o arbre.txt"),
        ("find . -name '*.py'", "find . -name '*.py' -delete"),
        ("find . -type f", "find . -type f -exec rm {} ;"),
        ("find .", "find . -fprint releve.txt"),
        ("git log -p", "git log -p --output=patch.diff"),
        ("git diff", "git diff -o patch.diff"),
        ("git show HEAD", "git show HEAD --output=patch.diff"),
        ("git grep motif", "git grep -O motif"),
        ("git branch -a", "git branch -d vieille"),
        ("git branch --list", "git branch --delete vieille"),
        ("git branch -vv", "git branch -M principale"),
        # `-v` teste une variable, et bash évalue l'indice d'un tableau : la
        # valeur de `y` fait exécuter ce qu'elle contient (mesuré, #1211).
        ("test -f fichier", "test -v 'a[y]'"),
        ("[ -e fichier ]", "[ -v 'a[y]' ]"),
    ],
)
def test_une_option_qui_ferait_ecrire_retire_la_dispense(lisant: str, ecrivant: str) -> None:
    """Chaque entrée des listes d'options est armée : la forme qui lit passe, la
    forme fautive non. Sans cette paire, une option recopiée de travers ne se
    verrait jamais."""
    assert est_lecture(lisant), lisant
    assert not est_lecture(ecrivant), ecrivant


def test_une_option_de_lecture_qui_ressemble_a_une_option_ecrivante_passe() -> None:
    """`grep -o` (seulement la part trouvée) et `grep -i` n'ont rien à voir avec
    le `-o` de `sort` : les options se jugent **par verbe**, jamais globalement —
    une liste unique aurait fermé la recherche la plus courante qui soit."""
    assert est_lecture("grep -o motif fichier")
    assert est_lecture("grep -rin motif src")
    assert est_lecture("ls -o")


# --- ④ Le hook du fournisseur : où la dispense s'arrête --------------------


def _hook(politique, *, arbitrages=None):
    """Le hook PreToolUse armé sur `politique`, qui consigne les arbitrages demandés.

    L'arbitre rend toujours « non approuvé » : c'est le régime d'un run réel, où
    personne ne répond pendant une tâche (EF-08). Une commande qui part à
    l'arbitrage se reconnaît donc à son `deny`, et une lecture dispensée à sa
    sortie vide **et** à la liste d'arbitrages restée vide.
    """

    async def arbitre(outil, arguments, motif):
        if arbitrages is not None:
            arbitrages.append((outil, arguments))
        return False, "personne n'a répondu"

    return claude_mod._hook_permissions(politique, None, arbitre)


def _joue(hook, commande: str, outil: str = "Bash"):
    return asyncio.run(
        hook({"tool_name": outil, "tool_input": {"command": commande}}, "tu-1", None)
    )


@pytest.mark.parametrize(
    "commande",
    [
        COMMANDE_S3,
        COMMANDE_S1,
        COMMANDE_PRUNE_FICHIERS,
        COMMANDE_PRUNE_DOSSIERS,
        COMMANDE_DEV_NULL,
        COMMANDE_LS_ATELIER,
        COMMANDE_CD_RACINE,
        COMMANDE_TEST_F,
    ],
)
def test_le_hook_laisse_lire_un_agent_dont_les_commandes_attendent_un_humain(
    commande: str,
) -> None:
    """Le cœur du ticket : `Bash` classé `ask`/`humain` — le cran qu'un projet sans
    commande déclarée fait proposer (`maestro.equipe.proposition`) —, et chacun des
    gestes qui ont bloqué un run réel passe, sans qu'aucune demande ne parte."""
    arbitrages: list[tuple[str, object]] = []
    hook = _hook(PolitiqueOutils(ask=("Bash",)), arbitrages=arbitrages)

    assert _joue(hook, commande) == {}
    assert arbitrages == []


def test_le_hook_fait_toujours_arbitrer_une_commande_qui_agit() -> None:
    arbitrages: list[tuple[str, object]] = []
    hook = _hook(PolitiqueOutils(ask=("Bash",)), arbitrages=arbitrages)

    sortie = _joue(hook, "rm -rf notes")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert arbitrages and arbitrages[0][0] == "Bash"


def test_une_lecture_sous_le_cran_auto_garde_sa_trace() -> None:
    """La dispense se pose **en dernier**, après `auto`. Un `Bash` en `auto` — le
    cran des cinq politiques livrées avec le dépôt — passait déjà sans déranger
    personne, mais **en étant tracé**, et c'est tout ce qui le distingue d'un
    `allow` (#586). La dispense retire l'attente d'une personne, pas une trace."""
    tracees: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),)),
        lambda outil, motif: tracees.append((outil, motif)),
    )

    assert _joue(hook, "ls -la") == {}
    assert tracees and tracees[0][0] == "Bash"


def test_la_dispense_ne_leve_jamais_un_refus() -> None:
    """Elle retire une **attente**, pas une interdiction : la politique tranche
    d'abord, et un `deny` reste un `deny`."""
    hook = _hook(PolitiqueOutils(deny=("Bash",)))

    sortie = _joue(hook, "ls -la")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "deny" in sortie["hookSpecificOutput"]["permissionDecisionReason"]


def test_la_dispense_ne_donne_pas_plus_que_l_outil_de_lecture() -> None:
    """Une politique qui met `Read` en arbitrage veut que les lectures soient
    tranchées. Un `cat` ne doit pas la contourner : la dispense tombe avec elle."""
    arbitrages: list[tuple[str, object]] = []
    hook = _hook(PolitiqueOutils(ask=("Bash", OUTIL_LECTURE)), arbitrages=arbitrages)

    sortie = _joue(hook, "cat README.md")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert arbitrages and arbitrages[0][0] == "Bash"


def test_la_dispense_ne_borne_pas_ce_que_la_commande_lit() -> None:
    """Ce que ce module **n'affirme pas**, dit une fois pour qu'on ne le suppose
    pas : un `cat .env` est une lecture, donc il passe — la dispense dit qu'une
    commande n'agit pas, jamais qu'elle lit peu.

    Le premier jet bornait la dispense aux commandes ne nommant aucun chemin
    exclu ; le premier passage réel du banc l'a réfuté (voir
    `test_les_gestes_observes_sur_la_vraie_stack_sont_des_lectures`). Ce qui borne
    les secrets vit ailleurs et n'a pas bougé : la frontière d'écriture sur les
    **outils de fichiers** (`Read` sur un `.env` est refusé, pas arbitré), les
    exclusions du périmètre, et la rédaction des valeurs (#109). docs/24 §2.5 dit
    pourquoi une commande shell ne se borne pas par l'analyse de son texte."""
    hook = _hook(PolitiqueOutils(ask=("Bash",)))
    assert _joue(hook, "cat .env") == {}


# --- La forme d'entrée : ce qui arrive du SDK -------------------------------


@pytest.mark.parametrize("brut", [None, "ls", 42, {"command": 7}, {}, {"autre": "ls"}])
def test_un_tool_input_sans_commande_lisible_ne_dispense_de_rien(brut: object) -> None:
    assert commande_de(brut) == ""
    assert not lecture_sans_arbitrage("Bash", brut)


def test_seul_l_outil_d_execution_est_juge_ici() -> None:
    """Un `ask` posé sur `Read`, `Glob` ou `Grep` est un choix de politique : le
    lever au nom de « ce n'est qu'une lecture » défairait la politique appliquée."""
    for outil in ("Read", "Glob", "Grep", "Write"):
        assert not lecture_sans_arbitrage(outil, {"command": "ls"}), outil
