"""« Agir dans son projet n'est pas agir dehors » : la portée d'un cran (#1226).

Le fait qui a ouvert le ticket est mesuré, et c'est lui qui ouvre cette suite :
un projet neuf, une équipe proposée par Maestro et validée telle quelle, et
**14 demandes de validation `Bash` pour 14 approbations** (run `96d0c3482649`,
2026-09-22). Les commandes sont reprises ici **telles qu'elles ont été jouées** :
ce ne sont pas des exemples inventés, ce sont les appels qui ont fait attendre
une personne pour qu'un agent lance le code qu'il venait d'écrire.

Six parties :

① les quatorze gestes observés, qui doivent tous passer ;
② ce qui **sort du projet** et remonte — chemin hors racine, installation
   ailleurs, machine ;
③ ce qui **détruit ce que l'agent n'a pas produit** et remonte ; et ce qu'il a
   produit, qui passe ;
④ la relecture du texte : ce qu'on ne sait pas lire remonte, et chaque liste
   déclarée est prouvée sur un échantillon fautif — une entrée qui ne servirait à
   rien serait un garde-fou de façade ;
⑤ le hook, où la portée **borne** le cran : dedans il vaut ce qu'il dit, dehors le
   défaut reprend la main ;
⑥ la politique — la portée s'écrit, se relit, se refuse quand on ne sait pas la
   lire —, et la correction des équipes **déjà créées**, qui est le second
   critère du ticket.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maestro.agents.permissions import (
    EntreeArbitrage,
    PermissionStore,
    PolitiqueOutils,
    execution_cadree_au_projet,
    politique_validee,
)
from maestro.decideur import Decideur
from maestro.portee import PORTEE_PROJET, PorteeProjet, decoupe, hors_de_portee
from maestro.projets.modele import Perimetre, Projet
from maestro.providers import claude as claude_mod
from maestro.sandbox.en_place import portee_de, presents_de

#: Les quatorze commandes du run `96d0c3482649`, dans l'ordre où elles ont été
#: soumises à la personne. Toutes ont été approuvées, aucune ne sortait du
#: projet : c'est le taux d'approbation de 1 que docs/32 §8 désignait d'avance
#: comme la preuve que ces actes-là méritaient `auto`.
GESTES_DU_RUN = (
    "mkdir -p .maestro/logo-maestro",
    "python maestro_logo.py",
    "python animation_maestro.py",
    "python .maestro/logo-maestro/verify.py",
    "python -m pytest test_animation_maestro.py -v",
    "python --version",
    "python -c \"import ast; ast.parse(open('animation_maestro.py').read())\"",
    "rm -rf __pycache__ && ls -la",
    "rm -rf .pytest_cache __pycache__ && ls -la",
)


def _portee(racine: Path, presents: tuple[str, ...] = ()) -> PorteeProjet:
    """La portée d'un projet dont `presents` était là avant la tâche."""
    return PorteeProjet(racine=racine, presents=frozenset(presents))


# --- ① Les gestes observés passent -----------------------------------------


@pytest.mark.parametrize("commande", GESTES_DU_RUN)
def test_les_gestes_du_run_mesure_restent_dans_le_projet(
    tmp_path: Path, commande: str
) -> None:
    """Le projet du run était **neuf** : rien n'y était avant l'agent, donc rien
    de ce qu'il efface n'est à quelqu'un d'autre."""
    assert _portee(tmp_path).commande_hors_portee(commande) == ""


def test_un_projet_neuf_n_a_rien_a_proteger(tmp_path: Path) -> None:
    """Le relevé d'un dossier vide est vide, y compris sa racine : `rm -rf .` sur
    un projet neuf ne détruit rien, et faire attendre une personne pour cela
    serait exactement le défaut qu'on corrige."""
    assert presents_de(tmp_path, Perimetre()) == frozenset()


def test_la_portee_ne_referme_pas_ce_que_la_dispense_de_lecture_a_ouvert(
    tmp_path: Path,
) -> None:
    """Une lecture ne sort de nulle part : elle n'agit pas. La refuser ici
    reviendrait à reprendre d'une main ce que #1197 a donné de l'autre, et ce qui
    borne les secrets est ailleurs (frontière, périmètre, rédaction)."""
    assert _portee(tmp_path).commande_hors_portee("cat /etc/hosts") == ""
    assert _portee(tmp_path).commande_hors_portee("ls -la ..") == ""


# --- ② Ce qui sort du projet remonte ---------------------------------------


@pytest.mark.parametrize(
    "commande",
    [
        "python ../ailleurs/script.py",
        "cp rapport.md ~/rapport.md",
        "node --experimental-vm-modules /opt/outils/build.js",
        "touch /tmp/marqueur",
        "python app.py --sortie=../hors/journal.txt",
        "gcc -I/usr/include/foo app.c",
    ],
)
def test_un_chemin_hors_de_la_racine_remonte(tmp_path: Path, commande: str) -> None:
    motif = _portee(tmp_path).commande_hors_portee(commande)

    assert "sort du" in motif


@pytest.mark.parametrize(
    "commande",
    [
        "sudo rm -rf build",
        "apt-get install -y ffmpeg",
        "brew install jq",
        "systemctl restart nginx",
        "ssh serveur 'ls'",
        "pip install requests",
        "python -m pip install requests",
        "npm install -g typescript",
        "pipx install ruff",
    ],
)
def test_un_acte_qui_sort_sans_nommer_de_chemin_remonte(
    tmp_path: Path, commande: str
) -> None:
    """Élévation, gestionnaire du système, installation globale, machine
    distante : quatre façons de sortir du projet sans écrire un seul chemin."""
    assert _portee(tmp_path).commande_hors_portee(commande) != ""


@pytest.mark.parametrize(
    "commande",
    ["npm install", "npm ci", "npm run build", "cargo build", "pytest -q"],
)
def test_remplir_le_dossier_du_projet_n_est_pas_en_sortir(
    tmp_path: Path, commande: str
) -> None:
    """Un gestionnaire de paquets **sans** drapeau global remplit le `node_modules`
    du projet, ce qui est exactement son travail. Le prendre pour une sortie
    ferait attendre une personne sur le geste le plus ordinaire d'un projet web."""
    assert _portee(tmp_path).commande_hors_portee(commande) == ""


def test_l_executable_lui_meme_n_est_jamais_un_chemin_qui_sort(tmp_path: Path) -> None:
    """Un interpréteur vit hors du projet par construction : compter le verbe
    parmi les chemins ferait sortir la commande la plus banale qui soit."""
    assert _portee(tmp_path).commande_hors_portee("/usr/bin/python3 app.py") == ""


# --- ③ Ce que l'agent n'a pas produit ---------------------------------------


def test_effacer_ce_qui_etait_la_avant_la_tache_remonte(tmp_path: Path) -> None:
    portee = _portee(tmp_path, ("", "notes.md", "src", "src/app.py"))

    assert "ne l'a pas produit" in portee.commande_hors_portee("rm -rf src")
    assert "ne l'a pas produit" in portee.commande_hors_portee("rm notes.md")
    assert "ne l'a pas produit" in portee.commande_hors_portee("mv src/app.py vieux.py")


def test_nettoyer_ce_que_ses_propres_executions_ont_produit_passe(
    tmp_path: Path,
) -> None:
    """Le geste exact du run mesuré : les caches n'existaient pas quand la tâche a
    commencé, c'est l'agent qui les a faits en lançant son code."""
    portee = _portee(tmp_path, ("", "notes.md"))

    assert portee.commande_hors_portee("rm -rf __pycache__ .pytest_cache") == ""


def test_une_cible_que_le_shell_etendra_ne_se_juge_pas(tmp_path: Path) -> None:
    """`rm -rf *` ne nomme rien : on ne sait pas ce que l'étoile recouvrira. Au
    moindre doute sur une destruction, c'est une personne qui tranche."""
    portee = _portee(tmp_path, ("", "notes.md"))

    assert "ne peut pas juger" in portee.commande_hors_portee("rm -rf *")
    assert portee.commande_hors_portee("rm -rf build/*") != ""


def test_une_redirection_qui_ecrase_du_preexistant_remonte(tmp_path: Path) -> None:
    """Écraser un fichier est une destruction : elle ne passe pas par un verbe,
    elle passe par un `>`. L'ajout (`>>`), lui, ne fait disparaître aucun contenu."""
    portee = _portee(tmp_path, ("", "README.md"))

    assert "écraserait" in portee.commande_hors_portee("echo x > README.md")
    assert portee.commande_hors_portee("echo x >> README.md") == ""
    assert portee.commande_hors_portee("python app.py > sortie.txt") == ""


def test_hors_du_regime_en_place_rien_n_est_a_proteger(tmp_path: Path) -> None:
    """Un worktree et un répertoire jetable sont des **copies** : ce qu'on y
    détruit ne se perd pas. Y faire remonter un `rm` ferait attendre une personne
    pour rien — et c'est la raison pour laquelle `presents` y est vide."""
    assert _portee(tmp_path).commande_hors_portee("rm -rf src") == ""


def test_find_qui_agit_remonte_faute_de_cible_lisible(tmp_path: Path) -> None:
    portee = _portee(tmp_path, ("", "notes.md"))

    assert portee.commande_hors_portee("find . -name '*.md' -delete") != ""
    assert portee.commande_hors_portee("find . -name '*.md' -exec rm {} ;") != ""


# --- ④ Ce qu'on ne sait pas lire remonte ------------------------------------


@pytest.mark.parametrize(
    "commande",
    [
        "python app.py $(cat cible.txt)",
        "rm -rf `cat cible.txt`",
        "echo 'guillemet non fermé",
        "(cd build && rm -rf .)",
    ],
)
def test_un_texte_qu_on_ne_sait_pas_lire_remonte(tmp_path: Path, commande: str) -> None:
    """Ce qu'une substitution exécute n'est pas dans le texte qu'on juge, et un
    sous-shell n'est pas une commande simple. Le seul verdict honnête est « je ne
    sais pas », qui se rend en demandant à une personne."""
    assert _portee(tmp_path).commande_hors_portee(commande) != ""


def test_un_appel_sans_commande_remonte(tmp_path: Path) -> None:
    assert _portee(tmp_path).hors_portee("Bash", {"command": "   "}) != ""
    assert _portee(tmp_path).hors_portee("Bash", None) != ""


def test_seul_l_outil_d_execution_est_juge(tmp_path: Path) -> None:
    """Un `ask` posé sur `Write` ou sur un serveur MCP est un choix de politique,
    et la frontière d'écriture tient déjà les outils de fichiers à la racine : le
    lever au nom d'une portée reviendrait à défaire la politique qu'on applique."""
    portee = _portee(tmp_path, ("", "README.md"))

    assert portee.hors_portee("Write", {"file_path": "/etc/passwd"}) == ""
    assert portee.hors_portee("mcp__slack__send_message", {"texte": "coucou"}) == ""


def test_le_decoupage_garde_les_redirections_a_part_des_arguments() -> None:
    """`python app.py > sortie.txt` lance `app.py` et écrit `sortie.txt` :
    confondre les deux ferait juger `sortie.txt` comme un argument de `python`."""
    (simple,) = decoupe("python app.py 2>/dev/null > sortie.txt")

    assert simple.jetons == ("python", "app.py")
    assert (">", "sortie.txt") in simple.redirections


@pytest.mark.parametrize(
    ("verbe", "fautif"),
    [
        ("rm", "rm cible"),
        ("rmdir", "rmdir cible"),
        ("unlink", "unlink cible"),
        ("mv", "mv cible ailleurs"),
        ("shred", "shred cible"),
        ("truncate", "truncate -s 0 cible"),
        ("dd", "dd if=/dev/zero of=cible"),
        ("del", "del cible"),
        ("erase", "erase cible"),
    ],
)
def test_chaque_verbe_destructeur_declare_est_prouve(
    tmp_path: Path, verbe: str, fautif: str
) -> None:
    """Chaque entrée de la liste est prouvée sur un échantillon fautif : une
    entrée qui ne changerait rien serait un garde-fou de façade."""
    portee = _portee(tmp_path, ("", "cible"))

    assert portee.commande_hors_portee(fautif) != "", verbe


# --- ⑤ Le hook : la portée borne le cran ------------------------------------


def _hook(politique, portee, *, arbitrages=None, tracees=None):
    """Le hook PreToolUse armé sur `politique` et `portee`.

    L'arbitre rend toujours « non approuvé » : c'est le régime d'un run réel, où
    personne ne répond pendant une tâche (EF-08). Un appel qui remonte se
    reconnaît donc à son `deny` **et** à la demande consignée.
    """

    async def arbitre(outil, arguments, motif):
        if arbitrages is not None:
            arbitrages.append((outil, motif))
        return False, "personne n'a répondu"

    return claude_mod._hook_permissions(
        politique,
        (lambda outil, motif: tracees.append((outil, motif))) if tracees is not None else None,
        arbitre,
        portee=portee,
    )


def _joue(hook, commande: str, outil: str = "Bash"):
    return asyncio.run(
        hook({"tool_name": outil, "tool_input": {"command": commande}}, "tu-1", None)
    )


def _politique_de_projet() -> PolitiqueOutils:
    """La politique qu'une équipe proposée écrit désormais pour son `dev`."""
    return PolitiqueOutils(
        ask=(EntreeArbitrage("Bash", Decideur.AUTO, PORTEE_PROJET),)
    )


@pytest.mark.parametrize("commande", GESTES_DU_RUN)
def test_le_hook_laisse_l_agent_lancer_son_travail(tmp_path: Path, commande: str) -> None:
    """Le cœur du ticket, bout en bout : la politique qu'une équipe validée
    reçoit, et les quatorze gestes qui réveillaient une personne."""
    arbitrages: list[tuple[str, str]] = []
    hook = _hook(_politique_de_projet(), _portee(tmp_path), arbitrages=arbitrages)

    assert _joue(hook, commande) == {}
    assert arbitrages == []


def test_le_hook_trace_ce_qu_il_laisse_passer(tmp_path: Path) -> None:
    """La portée retire l'attente d'une **personne**, jamais la trace : c'est
    tout ce qui distingue un `ask`/`auto` d'un `allow` (#586)."""
    tracees: list[tuple[str, str]] = []
    hook = _hook(_politique_de_projet(), _portee(tmp_path), tracees=tracees)

    assert _joue(hook, "python app.py") == {}
    assert tracees and tracees[0][0] == "Bash"


def test_hors_de_la_portee_le_defaut_reprend_la_main(tmp_path: Path) -> None:
    """Une portée **borne** un cran, elle n'en ajoute pas un troisième : dehors,
    c'est le décideur par défaut — donc une personne — qui tranche."""
    arbitrages: list[tuple[str, str]] = []
    hook = _hook(
        _politique_de_projet(),
        _portee(tmp_path, ("", "notes.md")),
        arbitrages=arbitrages,
    )

    sortie = _joue(hook, "rm notes.md")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert arbitrages and arbitrages[0][0] == "Bash"
    # Les deux moitiés du motif : la règle écrite, et ce que cet appel-ci fait.
    motif = arbitrages[0][1]
    assert "liste ask" in motif and PORTEE_PROJET in motif
    assert "ne l'a pas produit" in motif


def test_une_portee_qu_on_ne_peut_pas_evaluer_fait_retomber_sur_le_defaut() -> None:
    """Sans racine montée sur la session, on ne sait pas juger — et ne pas savoir
    n'est jamais une raison de laisser passer."""
    arbitrages: list[tuple[str, str]] = []
    hook = _hook(_politique_de_projet(), None, arbitrages=arbitrages)

    sortie = _joue(hook, "python app.py")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert arbitrages


def test_sans_portee_declaree_le_cran_vaut_partout(tmp_path: Path) -> None:
    """Le régime d'avant ce lot, au bit près : les cinq politiques du dépôt sont
    en `Bash: auto` sans portée, et rien de ce qu'elles font ne change."""
    tracees: list[tuple[str, str]] = []
    hook = _hook(
        PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),)),
        _portee(tmp_path, ("", "notes.md")),
        tracees=tracees,
    )

    assert _joue(hook, "rm notes.md") == {}
    assert tracees


def test_la_portee_ne_leve_jamais_un_refus(tmp_path: Path) -> None:
    """Elle borne une **attente**, pas une interdiction : `deny` tranche avant."""
    hook = _hook(
        PolitiqueOutils(
            ask=(EntreeArbitrage("Bash", Decideur.AUTO, PORTEE_PROJET),), deny=("Bash",)
        ),
        _portee(tmp_path),
    )

    sortie = _joue(hook, "python app.py")

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "deny" in sortie["hookSpecificOutput"]["permissionDecisionReason"]


def test_hors_de_portee_rend_son_motif_sans_evaluateur() -> None:
    assert hors_de_portee("", None, "Bash", {"command": "rm -rf /"}) == ""
    assert "inconnue" in hors_de_portee("lune", None, "Bash", {"command": "ls"})


# --- ⑥ La politique : écrire, relire, corriger ------------------------------


def test_la_portee_s_ecrit_et_se_relit() -> None:
    politique = PolitiqueOutils(
        ask=(EntreeArbitrage("Bash", Decideur.AUTO, PORTEE_PROJET),)
    )

    servie = politique.to_dict()

    assert servie["ask"] == {"Bash": "auto"}
    assert servie["portees"] == {"Bash": PORTEE_PROJET}
    relue = PolitiqueOutils.from_dict(servie)
    assert relue.decide("Bash").portee == PORTEE_PROJET


def test_une_portee_inconnue_est_refusee_avec_ce_qui_est_admis() -> None:
    """Une portée qu'on ne sait pas évaluer ne borne rien : elle **élargirait** le
    cran qu'elle prétend resserrer. Un garde-fou ne s'applique jamais à moitié."""
    with pytest.raises(ValueError, match="portée 'lune' de l'entrée"):
        politique_validee(
            {"ask": {"Bash": "auto"}, "portees": {"Bash": "lune"}}, agent="dev"
        )


def test_une_portee_sans_entree_ask_est_refusee() -> None:
    """Elle ne s'appliquerait à rien, et laisser passer une règle sans effet est
    la façon la plus sûre de croire qu'un garde-fou est posé quand il ne l'est pas."""
    with pytest.raises(ValueError, match="qui n'est pas une entrée ask"):
        politique_validee({"deny": ["Bash"], "portees": {"Bash": PORTEE_PROJET}}, agent="dev")


def test_une_equipe_deja_creee_se_corrige_sans_etre_recreee(tmp_path: Path) -> None:
    """Le second critère du ticket. Toutes les équipes proposées avant ce lot ont
    reçu `{"ask": {"Bash": "humain"}}` faute de commande lue — c'est-à-dire
    toujours, sur un projet neuf. La correction se fait à la lecture, sur les
    seules politiques **de projet**."""
    gabarits = PermissionStore(tmp_path)
    projet = gabarits.pour_projet("prj-1")
    projet.racine.mkdir(parents=True, exist_ok=True)
    (projet.racine / "dev.json").write_text(
        json.dumps({"allow": [], "ask": {"Bash": "humain"}, "deny": []}), encoding="utf-8"
    )

    politique = projet.lire("dev")

    assert politique is not None
    assert politique.decideur("Bash") is Decideur.AUTO
    assert politique.decide("Bash").portee == PORTEE_PROJET


def test_un_humain_choisi_depuis_ce_lot_n_est_plus_corrige(tmp_path: Path) -> None:
    """Le marqueur est la **présence** de `portees` : une politique passée par
    l'écran depuis ce lot porte la clé, même vide. Une personne qui choisit
    `humain` aujourd'hui garde son `humain`."""
    gabarits = PermissionStore(tmp_path)
    projet = gabarits.pour_projet("prj-1")
    projet.racine.mkdir(parents=True, exist_ok=True)
    (projet.racine / "dev.json").write_text(
        json.dumps({"ask": {"Bash": "humain"}, "portees": {}}), encoding="utf-8"
    )

    politique = projet.lire("dev")

    assert politique is not None
    assert politique.decideur("Bash") is Decideur.HUMAIN


def test_les_gabarits_du_depot_ne_sont_jamais_corriges(tmp_path: Path) -> None:
    """La correction vise les équipes d'un projet. Les cinq politiques livrées
    avec le dépôt sont une décision prise à froid et versionnée (#716)."""
    gabarits = PermissionStore(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "qa.json").write_text(
        json.dumps({"ask": {"Bash": "humain"}}), encoding="utf-8"
    )

    politique = gabarits.lire("qa")

    assert politique is not None
    assert politique.decideur("Bash") is Decideur.HUMAIN


def test_la_correction_ne_touche_que_l_outil_d_execution() -> None:
    """Étroite par construction : un `deny`, un `auto` et un serveur MCP en
    `humain` ne sont pas le défaut qu'on répare."""
    politique = PolitiqueOutils(
        ask=(
            EntreeArbitrage("mcp__slack", Decideur.HUMAIN),
            EntreeArbitrage("Bash", Decideur.HUMAIN),
        ),
        deny=("WebFetch",),
    )

    corrigee = execution_cadree_au_projet(politique)

    assert corrigee.decideur("mcp__slack") is Decideur.HUMAIN
    assert corrigee.decideur("Bash") is Decideur.AUTO
    assert corrigee.deny == ("WebFetch",)
    # Idempotente : une politique déjà corrigée porte sa portée, donc elle sort telle quelle.
    assert execution_cadree_au_projet(corrigee) is corrigee


# --- La portée armée par la position ----------------------------------------


def test_la_portee_releve_ce_qui_etait_la_en_ecriture_en_place(tmp_path: Path) -> None:
    racine = tmp_path / "projet"
    (racine / "src").mkdir(parents=True)
    (racine / "src" / "app.py").write_text("print()", encoding="utf-8")
    projet = Projet(id="prj-1", nom="p", racine=racine.as_posix())

    portee = portee_de(racine, projet)

    assert "src" in portee.presents and "src/app.py" in portee.presents
    assert "" in portee.presents
    assert portee.commande_hors_portee("rm -rf src") != ""


def test_hors_de_la_racine_du_projet_rien_n_est_releve(tmp_path: Path) -> None:
    """Un worktree n'est pas la racine : son régime ne bouge pas, et la portée s'y
    réduit à « reste dans l'espace de travail »."""
    racine = tmp_path / "projet"
    (racine / "notes.md").parent.mkdir(parents=True)
    (racine / "notes.md").write_text("x", encoding="utf-8")
    espace = tmp_path / "worktree"
    espace.mkdir()
    projet = Projet(id="prj-1", nom="p", racine=racine.as_posix())

    portee = portee_de(espace, projet)

    assert portee.presents == frozenset()
    assert portee.commande_hors_portee("rm -rf notes.md") == ""
    assert portee.commande_hors_portee("rm -rf ../projet") != ""
