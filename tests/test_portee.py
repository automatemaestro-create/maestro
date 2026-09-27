"""« Agir dans son projet n'est pas agir dehors » : la portée d'un cran (#1226).

Le fait qui a ouvert le ticket est mesuré, et c'est lui qui ouvre cette suite :
un projet neuf, une équipe proposée par Maestro et validée telle quelle, et
**14 demandes de validation `Bash` pour 14 approbations** (run `96d0c3482649`,
2026-09-22). Les commandes sont reprises ici **telles qu'elles ont été jouées** :
ce ne sont pas des exemples inventés, ce sont les appels qui ont fait attendre
une personne pour qu'un agent lance le code qu'il venait d'écrire.

Huit parties :

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
   critère du ticket ;
⑦ **sous Windows** (#1278) : le projet nommé par son chemin absolu est dans le
   projet, quelle que soit son orthographe, et un exécutable garde son verdict
   avec son `.exe`. Les commandes sont, là encore, celles de deux runs réels ;
⑧ **les formes ordinaires d'une commande d'agent** (#1348) : les 55 commandes
   que le passage `20260927-070605` a rendues à une personne, rejouées sur la
   racine de leur tâche. 44 restaient dans leur projet et y restent ; 11 en
   sortaient vraiment et remontent toujours, chacune avec sa raison.

⑦ et ⑧ se jouent sur une racine **à la Windows** (`E:/…`, `C:/…`) quel que soit
l'OS du test : la portée rend le même verdict des deux côtés, et c'est ce qui
permet au conteneur Linux du filet d'éprouver ce que seul un poste Windows
produisait.
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
from maestro.portee import PORTEE_PROJET, PorteeProjet, hors_de_portee
from maestro.projets.modele import Perimetre, Projet
from maestro.providers import claude as claude_mod
from maestro.sandbox.en_place import portee_de, presents_de
from maestro.shell import lis

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
        "echo 'guillemet non fermé",
        'python app.py "$(cat cible.txt',
        "select x in a b; do rm notes.md; done",
        "nettoie() { rm notes.md; }; nettoie",
        "liste=(a b); rm ${liste[0]}",
    ],
)
def test_un_texte_qu_on_ne_sait_pas_lire_remonte(tmp_path: Path, commande: str) -> None:
    """Des guillemets qui ne se ferment pas, et la syntaxe que le lecteur ne lit
    pas (`select`, fonction, tableau) : le seul verdict honnête est « je ne sais
    pas », qui se rend en demandant à une personne. Depuis #1348 la liste est
    courte — substitution, sous-shell, bloc, boucle, `case` et heredoc se
    **lisent**, et sont jugés ci-dessous."""
    assert "illisible" in _portee(tmp_path).commande_hors_portee(commande)


def test_ce_qu_une_substitution_execute_est_juge_comme_le_reste(tmp_path: Path) -> None:
    """Ce qu'une substitution exécute **est** dans le texte : on le juge, maillon
    par maillon, comme le reste (#1348). Ce qu'on ne sait pas, c'est la **valeur**
    qu'elle rend — et une cible de destruction qu'on ne connaît pas ne se juge pas."""
    portee = _portee(tmp_path, ("", "notes.md", "build", "build/app.py"))

    assert portee.commande_hors_portee("python app.py $(cat cible.txt)") == ""
    assert "ne l'a pas produit" in portee.commande_hors_portee("python app.py $(rm notes.md)")
    assert "ne l'a pas produit" in portee.commande_hors_portee("echo `rm notes.md`")
    assert "ne peut pas juger" in portee.commande_hors_portee("rm -rf `cat cible.txt`")
    assert "ne peut pas juger" in portee.commande_hors_portee('rm -rf "$CIBLE"')


def test_un_sous_shell_se_lit_et_garde_son_dossier(tmp_path: Path) -> None:
    """Un `cd` dans un sous-shell ne déplace que lui : ce qui suit la parenthèse
    repart du dossier d'avant."""
    portee = _portee(tmp_path, ("", "notes.md", "build", "build/app.py"))

    assert "ne l'a pas produit" in portee.commande_hors_portee("(cd build && rm -rf .)")
    assert portee.commande_hors_portee("(cd build && rm -rf __pycache__)") == ""
    assert "ne l'a pas produit" in portee.commande_hors_portee("(cd build && ls); rm notes.md")


def test_chaque_branche_d_un_case_est_jugee(tmp_path: Path) -> None:
    """Le motif choisit une branche au moment de l'exécution : le texte, lui, les
    porte toutes, et une seule qui détruit suffit."""
    portee = _portee(tmp_path, ("", "notes.md"))

    assert portee.commande_hors_portee('case "$x" in a|b) ls ;; *) echo ;; esac') == ""
    assert "ne l'a pas produit" in portee.commande_hors_portee(
        'case "$x" in a) ls ;; *) rm notes.md ;; esac'
    )


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
    (simple,) = lis("python app.py 2>/dev/null > sortie.txt").commandes

    assert [mot.rendu() for mot in simple.mots] == ["python", "app.py"]
    assert (">", "sortie.txt") in [(r.operateur, r.cible.rendu()) for r in simple.redirections]


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

    async def arbitre(outil, arguments, motif, decideur):
        if arbitrages is not None:
            arbitrages.append((outil, motif, decideur))
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
    # Et le décideur qui part avec la demande est ce défaut, pas le cran écrit
    # (#1278) : c'est lui que le garde-fou appliquera, sans le redemander.
    assert arbitrages[0][2] is Decideur.HUMAIN


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


# --- ⑦ Sous Windows, le projet est dans le projet (#1278) --------------------

#: La racine du run `3fe501fc0878` (projet `p3`, 2026-09-24), écrite comme la
#: Control Tower la déclare. Une racine **à la Windows**, sur tous les OS : le
#: verdict ne dépend pas de celui qui juge.
RACINE_P3 = Path("E:/Projects Solutions/maestro-projects/p3")

#: Les quatre commandes que ce run a vu juger « hors de la portée », telles
#: qu'elles ont été jouées — la troisième est tronquée là où le journal l'est.
#: Aucune ne sort du projet : chacune y entre par son chemin absolu, sous les
#: deux orthographes que l'agent emploie (Windows, et MSYS pour Git Bash).
GESTES_DU_RUN_P3 = (
    'cd "E:\\Projects Solutions\\maestro-projects\\p3" && node -v; npm -v; python --version',
    'mkdir -p "E:\\Projects Solutions\\maestro-projects\\p3\\maquette"',
    'cd "E:\\Projects Solutions\\maestro-projects\\p3" && '
    'node ".maestro/maquette-sections/cdp-shot.mjs"',
    'cd "/e/Projects Solutions/maestro-projects/p3" && rmdir .maestro/contenu-produit',
)


def _portee_p3(presents: tuple[str, ...] = ("", "README.md")) -> PorteeProjet:
    """La portée de `p3` en écriture en place : sa racine, et ce qui y était déjà."""
    return PorteeProjet(racine=RACINE_P3, presents=frozenset(presents))


@pytest.mark.parametrize("commande", GESTES_DU_RUN_P3)
def test_les_gestes_du_run_p3_restent_dans_le_projet(commande: str) -> None:
    """Le premier défaut du ticket : la racine elle-même jugée hors de la racine.
    Corriger l'escalade sans lui réveillerait une personne à chaque `cd` dans le
    projet — la régression même que #1226 avait fermée."""
    assert _portee_p3().commande_hors_portee(commande) == ""


@pytest.mark.parametrize(
    "chemin",
    [
        "E:\\Projects Solutions\\maestro-projects\\p3",
        "E:/Projects Solutions/maestro-projects/p3",
        "e:\\projects solutions\\MAESTRO-PROJECTS\\p3",
        "E:\\Projects Solutions\\maestro-projects\\p3\\",
        "E:\\Projects Solutions\\maestro-projects\\p3\\maquette\\index.html",
        "/e/Projects Solutions/maestro-projects/p3",
        "/E/Projects Solutions/maestro-projects/p3/src",
        "E:\\Projects Solutions\\maestro-projects\\autre\\..\\p3\\src",
    ],
)
def test_toutes_les_orthographes_de_la_racine_sont_dans_le_projet(chemin: str) -> None:
    assert _portee_p3().commande_hors_portee(f'touch "{chemin}/x"') == "", chemin


@pytest.mark.parametrize(
    "chemin",
    [
        "C:\\Windows\\System32",
        "E:\\Projects Solutions\\maestro-projects\\autre",
        # Le préfixe n'est pas l'appartenance : `p3-bis` commence comme `p3`.
        "E:\\Projects Solutions\\maestro-projects\\p3-bis",
        "E:\\Projects Solutions\\maestro-projects\\p3\\..\\autre",
        "/c/Users/Sam25/AppData/Local/Temp",
        "/e/Projects Solutions/maestro-projects/autre",
        # La racine de Git Bash n'est pas celle du lecteur : `/tmp` est `%TEMP%`.
        "/tmp/edge-shot",
        "\\\\serveur\\partage\\p3",
    ],
)
def test_un_chemin_absolu_d_ailleurs_reste_dehors_sous_windows(chemin: str) -> None:
    assert "sort du" in _portee_p3().commande_hors_portee(f'touch "{chemin}/x"'), chemin


def test_ce_que_le_run_p3_a_joue_hors_du_projet_remonte() -> None:
    """Les actes qui, dans le même run, ont vraiment quitté le projet : écrire
    dans `%TEMP%`, et y lancer un navigateur qui ouvre un port de débogage."""
    portee = _portee_p3()

    assert portee.commande_hors_portee(
        "cd /tmp && mkdir -p edge-shot && msedge.exe --headless "
        "--user-data-dir=/tmp/edge-profile --screenshot=shot.png index.html"
    ) != ""
    assert portee.commande_hors_portee(
        "cd /tmp/edge-shot && (msedge.exe --headless=new --remote-debugging-port=9333 x)"
    ) != ""


def test_un_chemin_windows_sort_de_la_racine_de_l_os(tmp_path: Path) -> None:
    """Le cas pour lequel le motif Windows avait été ajouté : un test joué sous
    Linux — le conteneur du filet — doit pouvoir prouver qu'un `C:\\Windows`
    sort de la racine. Corriger la racine Windows ne doit pas le défaire."""
    portee = PorteeProjet(racine=tmp_path)

    assert "sort du" in portee.commande_hors_portee('touch "C:\\Windows\\x"')
    assert "sort du" in portee.commande_hors_portee("touch C:/Windows/x")


def test_ce_qui_etait_la_se_reconnait_sous_toutes_ses_orthographes() -> None:
    """Sous Windows, `NOTES.md` **est** `notes.md` : la même casse près que la
    racine, faute de quoi l'effacement de ce que la personne avait posé
    passerait pour celui d'un fichier que l'agent a produit."""
    portee = _portee_p3(("", "notes.md", "src", "src/app.py"))

    for commande in (
        "rm NOTES.md",
        'rm "E:\\Projects Solutions\\maestro-projects\\p3\\notes.md"',
        'rm -rf "/e/Projects Solutions/maestro-projects/p3/SRC"',
        'rm "src\\app.py"',
    ):
        assert "ne l'a pas produit" in portee.commande_hors_portee(commande), commande
    assert portee.commande_hors_portee('rm -rf "src\\__pycache__"') == ""


@pytest.mark.parametrize(
    "commande",
    [
        # Run réel `e5e1a7058fc5` (banc, `banc-s4-pourquoi`, 2026-09-25) : `rich`
        # s'est installé dans le Python du poste sans que personne le voie.
        '"/c/Users/Sam25/AppData/Local/Programs/Python/Python313/python.exe" -m pip install rich',
        "python.exe -m pip install rich",
        "pip.exe install rich",
        "winget.exe install foo",
        '"C:\\Python313\\python.exe" -m pip install rich',
        "PIP.EXE install rich",
        "Python.Exe -m pip install rich",
        "npm.cmd install -g typescript",
        "sudo.exe rm -rf build",
        "choco.bat install jq",
        "python3.12 -m pip install rich",
        "pip3.12 install rich",
    ],
)
def test_un_executable_windows_garde_son_verdict(commande: str) -> None:
    """Le quatrième défaut : `python.exe`, `pip.exe` et `winget.exe` gardaient
    leur suffixe, et la portée ne voyait jamais l'acte."""
    assert _portee_p3().commande_hors_portee(commande) != ""
    assert hors_de_portee(
        PORTEE_PROJET, _portee_p3(), "Bash", {"command": commande}
    ) != ""


def test_un_verbe_destructeur_se_reconnait_sous_toutes_ses_orthographes() -> None:
    """Ce qui valait pour l'installation vaut pour la destruction : `/bin/rm` et
    `rm.exe` effacent ce que la personne avait posé autant qu'un `rm`."""
    portee = _portee_p3(("", "notes.md"))

    for commande in ("/bin/rm notes.md", "rm.exe notes.md", "/usr/bin/find . -delete"):
        assert portee.commande_hors_portee(commande) != "", commande


@pytest.mark.parametrize(
    "commande", ["python.exe app.py", "npm.cmd install", "node.exe build.js", "py -3 app.py"]
)
def test_un_executable_windows_qui_reste_dans_le_projet_passe(commande: str) -> None:
    """Le témoin : reconnaître le suffixe ne fait pas sortir ce qui ne sortait pas."""
    assert _portee_p3().commande_hors_portee(commande) == ""


# --- ⑧ Les formes ordinaires d'une commande d'agent (#1348) ------------------

#: Les 55 commandes `Bash` que le passage `20260927-070605` du banc a rendues à
#: une personne (journal `_etat/journal.jsonl`, événements `validation.demande`),
#: chacune avec la racine de sa tâche. Le journal tronque une commande à 1000
#: caractères : quand la coupe tombe dans un heredoc, son corps court jusqu'à la
#: fin ; quand elle tombe dans une chaîne ou une boucle, le texte s'arrête à la
#: dernière commande complète (`tronquee` le dit).
PASSAGE_20260927 = json.loads(
    (Path(__file__).parent / "fixtures" / "portee" / "passage-20260927-070605.json").read_text(
        encoding="utf-8"
    )
)


def _du_passage(attendu: str) -> list[dict]:
    return [entree for entree in PASSAGE_20260927 if entree["attendu"] == attendu]


def _portee_de_la_tache(entree: dict) -> PorteeProjet:
    return PorteeProjet(racine=Path(entree["racine"]), presents=frozenset(entree["presents"]))


def test_le_passage_rejoue_est_celui_qui_a_rendu_55_demandes() -> None:
    """L'échantillon est le vrai : chaque commande porte le motif qui l'a fait
    remonter ce jour-là, et le compte est celui du rapport de bouclage."""
    assert len(PASSAGE_20260927) == 55
    assert all(entree["motif_avant"] for entree in PASSAGE_20260927)
    assert sum("illisible" in entree["motif_avant"] for entree in PASSAGE_20260927) == 42


@pytest.mark.parametrize(
    "entree", _du_passage("dedans"), ids=lambda e: f"{e['scenario']}-{e['demande']}"
)
def test_ce_qu_un_agent_jouait_dans_son_projet_y_reste(entree: dict) -> None:
    """Substitution, heredoc, bloc, boucle, `cd` dans un sous-dossier, dossier
    créé par `mktemp`, lecture hors du projet : les formes ordinaires d'une
    commande d'agent. Aucune ne sort du projet, et aucune ne dérange personne."""
    assert _portee_de_la_tache(entree).commande_hors_portee(entree["commande"]) == ""


@pytest.mark.parametrize(
    "entree", _du_passage("dehors"), ids=lambda e: f"{e['scenario']}-{e['demande']}"
)
def test_ce_qui_sortait_vraiment_du_projet_remonte_toujours(entree: dict) -> None:
    """Le prix d'une décision, nommé : un nom **fixe** dans le temporaire n'est
    pas à l'agent — Maestro y range les espaces de travail des autres tâches
    (`racine_des_espaces`) —, et seul ce qu'il crée par `mktemp` dans la commande
    même est à lui. Le motif dit laquelle des deux familles a joué."""
    motif = _portee_de_la_tache(entree).commande_hors_portee(entree["commande"])

    assert entree["motif_attendu"] in motif, entree["raison"]


#: La racine de S2 dans ce passage, et la commande que le ticket cite à son étape
#: de reproduction.
RACINE_S2 = Path("C:/Users/Sam25/maestro-scenarios/20260927-070605/s2-application")


def test_s2_verifie_son_livrable_sans_deranger_personne() -> None:
    """Le premier critère : le run du retex vérifie son application dans un
    dossier qu'il crée lui-même, et le range en partant."""
    commande = (
        f'cd "{RACINE_S2.as_posix()}" && T=$(mktemp -d) && python app.py >"$T/out"; rm -rf "$T"'
    )

    evaluateur = PorteeProjet(RACINE_S2)
    assert hors_de_portee(PORTEE_PROJET, evaluateur, "Bash", {"command": commande}) == ""


@pytest.mark.parametrize(
    "commande",
    [
        'T=$(mktemp -d); cp app.py "$T" && cd "$T" && python app.py; cd /; rm -rf "$T"',
        'O=$(mktemp) && python app.py >"$O" 2>&1; cat "$O"; rm -f "$O"',
        'W=$(mktemp -d "$TMP/recette-XXXX"); git clone -q . "$W/clone"; rm -rf "$W"',
        'T=$(mktemp -d); for x in a b; do mkdir "$T/$x"; done; rm -rf "$T"',
    ],
)
def test_ce_que_l_agent_cree_par_mktemp_est_a_lui(commande: str) -> None:
    assert _portee_p3().commande_hors_portee(commande) == ""


@pytest.mark.parametrize(
    "commande",
    [
        "sha256sum app.py > /tmp/avant.sha",  # la vérification V3 du bouclage
        "rm -rf /tmp/vr-raw",
        'T=$(mktemp -d); rm -rf "$T/../voisin"',  # remonter hors de son dossier
        'rm -rf "$(cygpath -m "$TEMP")/vr-raw"',
        'cd "$TMP/recette-d3I5/clone" && cp carnet.md ../avant.md',
        # Passage `20260927-104414`, S9 : le cadre disait « dans le répertoire
        # temporaire du système » — il dit maintenant `mktemp -d`.
        'rm -f "$TEMP/verif_carnet.py"; ls scripts',
    ],
)
def test_un_nom_fixe_du_temporaire_n_est_pas_a_l_agent(commande: str) -> None:
    assert "sort du" in _portee_p3().commande_hors_portee(commande)


@pytest.mark.parametrize(
    "commande",
    [
        "cd src && python -B -c 'import app'; cd ..; ls -la src",
        "cd tests && python -m pytest -q; cd ..; git status --short",
        "mkdir -p .verif && cd .verif && dotnet test ../Depensio.sln | tee ../verif.log",
    ],
)
def test_un_cd_deplace_le_pied_des_chemins(commande: str) -> None:
    """Le `..` qui suit un `cd` dans un sous-dossier ramène à la racine."""
    assert _portee_p3().commande_hors_portee(commande) == ""


def test_un_cd_hors_du_projet_emporte_les_chemins_qui_le_suivent() -> None:
    """Le pendant, dans l'autre sens : après un `cd` dehors, un nom nu désigne un
    fichier de là-bas — y compris pour une destruction."""
    portee = _portee_p3(("", "notes.md"))

    assert "sort du" in portee.commande_hors_portee("cd /tmp && rm -rf notes.md")
    assert "sort du" in portee.commande_hors_portee("cd .. && touch autre/x")
    assert "ne l'a pas produit" in portee.commande_hors_portee("cd src; rm ../notes.md")


@pytest.mark.parametrize(
    "commande",
    [
        'which -a python py 2>&1; py --version; ls "/c/Program Files/Python313" 2>&1',
        "ls ~/AppData/Local/ms-playwright 2>&1; where firefox; npm ls -g --depth=0",
        "find . \\( -name __pycache__ -o -name .pytest_cache \\) -not -path './.git*'",
    ],
)
def test_une_lecture_dans_un_enchainement_ne_sort_de_nulle_part(commande: str) -> None:
    """Une lecture n'agit pas (#1197), et cela vaut maillon par maillon : un `ls`
    hors du projet, au milieu de commandes qui restent dedans, ne fait rien sortir."""
    assert _portee_p3().commande_hors_portee(commande) == ""


@pytest.mark.parametrize(
    "commande",
    [
        ".venv/Scripts/python.exe -m pip install rich",
        "python -m pip install rich --target ./vendor",
        "python -m venv .venv && . .venv/Scripts/activate && pip install -r requirements.txt",
        "source .venv/bin/activate && python -m pip install rich",
    ],
)
def test_installer_dans_le_projet_n_est_pas_en_sortir(commande: str) -> None:
    """Les deux formes que le bouclage a relevées (P6), et leurs voisines : un
    interpréteur du projet installe dans le projet."""
    assert _portee_p3().commande_hors_portee(commande) == ""


@pytest.mark.parametrize(
    "commande",
    [
        "python -m pip install rich --target ../ailleurs",
        "source .venv/bin/activate && pip install --user rich",
        "cd /tmp && python -m venv v && . v/bin/activate && pip install rich",
        '"$PY" -m pip install rich',
    ],
)
def test_une_installation_hors_du_projet_remonte_toujours(commande: str) -> None:
    assert _portee_p3().commande_hors_portee(commande) != ""


@pytest.mark.parametrize(
    "commande",
    [
        # Le saut de ligne est un séparateur : `shlex` le lisait comme un blanc, et
        # la seconde ligne devenait un argument de la première.
        "echo ok\nrm notes.md",
        "python --version\npip install rich",
        # Ce qu'une enveloppe lance est ce qu'on juge.
        "env PYTHONPATH=. pip install rich",
        "timeout 60 pip install rich",
        "nohup rm notes.md",
        "find . -name '*.md' | xargs rm",
        # Une commande donnée à un autre shell est une commande.
        "bash -c 'pip install rich'",
        'sh -c "rm notes.md"',
        "bash <<'EOF'\nrm notes.md\nEOF",
        'powershell.exe -NoProfile -Command "pip install rich"',
        'cmd /c "winget install jq"',
        # Et un heredoc non cité exécute ce qu'il substitue.
        "cat > sortie.txt <<EOF\n$(rm notes.md)\nEOF",
    ],
)
def test_ce_qui_sort_ou_detruit_sous_une_autre_forme_remonte(commande: str) -> None:
    """Sans rouvrir #1278 : lire plus de formes ne fait rien passer de ce qui
    sort vraiment du projet — et en ferme quelques-unes qui passaient."""
    assert _portee_p3(("", "notes.md")).commande_hors_portee(commande) != "", commande


#: Les deux commandes que le passage suivant (`20260927-104414`, joué sur ce
#: correctif) a encore rendues à une personne, telles qu'elles ont été jouées.
#: La première vérifie, par un `case`, les chemins que les notes du projet citent
#: (S3) ; la seconde réutilise en toutes lettres le dossier qu'un `mktemp -d` de
#: l'appel précédent avait créé (S6) — chaque appel `Bash` est un shell neuf.
GESTES_DU_PASSAGE_104414 = (
    "grep -o '`[^`]*`' NOTES.md | tr -d '`' | sort -u | while read -r p; do "
    'case "$p" in *[/.]*) set -- $p; for w in "$@"; do case "$w" in '
    '*.py|*.md|*.toml|*/) [ -e "$w" ] && echo "OK  $w" || echo "ABSENT $w";; esac; '
    "done;; esac; done | sort -u\n"
    'echo "--- dossiers racine:"; for d in */ .*/; do case "$d" in ./|../) ;; '
    '*) grep -q "\\`$d\\`" NOTES.md && echo "cité $d" || echo "NON cité $d";; esac; done',
    "T=/tmp/tmp.gv9kMtoQdP && find . \\( -path ./.git -o -path ./node_modules -o -name .env "
    "-o -path '*/secrets' \\) -prune -o -type f -print0 | sort -z | xargs -0 sha1sum > "
    '$T/apres.sha1 && diff $T/avant.sha1 $T/apres.sha1 && echo "empreintes identiques" && '
    "find . \\( -path ./.git -o -path ./node_modules \\) -prune -o -print | sort | "
    'diff $T/avant.list - && echo "arborescence identique"; ls -A .maestro/verification-logo '
    "| wc -l; rm -rf $T/profil $T/profil2; ls $T; du -sh $T",
)


@pytest.mark.parametrize("commande", GESTES_DU_PASSAGE_104414)
def test_les_gestes_du_passage_suivant_restent_dans_le_projet(commande: str) -> None:
    assert _portee_p3(("", "NOTES.md", "README.md")).commande_hors_portee(commande) == ""


#: La troisième, en S9 : un venv créé **dans** le projet, puis rempli par son
#: propre interpréteur — le tout donné à PowerShell.
POWERSHELL_S9_104414 = (
    "powershell -NoProfile -Command 'python -m venv .venv; .venv\\Scripts\\python -m pip "
    'install -q -r requirements.txt; "install=$LASTEXITCODE"; .venv\\Scripts\\python '
    'scripts\\verifier_carnet.py; "verif_sans_pdf=$LASTEXITCODE"; .venv\\Scripts\\python '
    'scripts\\assembler_carnet.py; "gen=$LASTEXITCODE"; .venv\\Scripts\\python '
    "scripts\\verifier_carnet.py; \"verif=$LASTEXITCODE\"' 2>&1"
)


def test_powershell_installe_dans_le_projet_par_l_interpreteur_du_projet() -> None:
    portee = _portee_p3()

    assert portee.commande_hors_portee(POWERSHELL_S9_104414) == ""
    assert "installe" in portee.commande_hors_portee(
        "powershell -Command 'C:\\Python313\\python -m pip install rich'"
    )


@pytest.mark.parametrize(
    "commande",
    [
        "rm -rf /tmp/tmp.court",  # pas le nom de `mktemp`
        "rm -rf /tmp/maestro-dev-carnet-v0iwvq4r",  # l'espace d'une autre tâche
        "rm -rf /tmp/tmp.gv9kMtoQdP/../voisin",  # remonter hors de son dossier
        "rm -rf /c/Users/moi/projets/tmp.gv9kMtoQdP",  # pas sous un dossier temporaire
    ],
)
def test_seul_le_nom_de_mktemp_sous_le_temporaire_est_a_l_agent(commande: str) -> None:
    assert "sort du" in _portee_p3().commande_hors_portee(commande)


def test_le_nom_de_mktemp_se_reconnait_sous_toutes_ses_orthographes() -> None:
    portee = _portee_p3()

    for dossier in (
        "/tmp/tmp.gv9kMtoQdP",
        "C:/Users/Sam25/AppData/Local/Temp/tmp.gv9kMtoQdP",
        "/c/Users/Sam25/AppData/Local/Temp/tmp.gv9kMtoQdP",
    ):
        assert portee.commande_hors_portee(f'rm -rf "{dossier}/profil"') == "", dossier


#: Les deux commandes que le passage joué sur le code final (`20260927-131322`)
#: a encore rendues à une personne : le ménage des caches de S3, et un `cp` sur le
#: puits dans S6.
MENAGE_S3_131322 = (
    "for p in README.md pyproject.toml src src/depensio src/depensio/app.py "
    'src/depensio/modele.py .maestro; do test -e "$p" && echo "OK $p" || echo "MANQUE $p"; '
    'done\ntest -e src/app.py && echo "ancien existe" || echo "ancien absent (attendu)"\n'
    'python src/depensio/app.py; echo "code $?"\n'
    'PYTHONPATH=src python -B -m depensio.app; echo "code $?"\n'
    'PYTHONPATH=src python -B -c "from depensio import modele; print(modele.X)"; '
    'echo "code $?"\n'
    "find src -name __pycache__ -type d -exec rm -rf {} +; "
    "find . -name __pycache__ -not -path './.git/*'"
)
PUITS_S6_131322 = (
    'W=$(mktemp -d) && echo "$W" > /dev/null && cp /dev/null /dev/null; '
    'cd "E:/Projects Solutions/maestro-projects/p3" && sha256sum logo-anime.svg'
)


def test_les_gestes_du_passage_final_restent_dans_le_projet() -> None:
    portee = _portee_p3(("", "README.md", "src", "src/depensio", "src/depensio/app.py"))

    assert portee.commande_hors_portee(MENAGE_S3_131322) == ""
    assert portee.commande_hors_portee(PUITS_S6_131322) == ""


def test_un_find_qui_efface_se_confronte_a_ce_qui_etait_la() -> None:
    """Le geste de #1226 — effacer les caches que ses exécutions ont produits —
    écrit par `find` : ses cibles se décrivent (sous ces racines, ce nom), et si
    rien de présent avant la tâche n'y répond, l'agent n'efface que son travail."""
    portee = _portee_p3(("", "notes.md", "src", "src/app.py", "src/vieux/__pycache__"))

    assert portee.commande_hors_portee("find build -name __pycache__ -exec rm -rf {} +") == ""
    assert portee.commande_hors_portee("find . -name '*.pyc' -delete") == ""
    assert "ne l'a pas produit" in portee.commande_hors_portee(
        "find src -name __pycache__ -exec rm -rf {} +"
    )
    assert "ne l'a pas produit" in portee.commande_hors_portee("find . -iname 'NOTES.*' -delete")
    # Ce qui ne se confronte pas remonte, comme avant.
    for commande in (
        "find . -type f -exec rm {} +",  # aucun nom : tout le projet
        "find . -name a -o -name b -delete",  # un test qu'on ne sait pas lire
        "find /tmp -name '*.log' -delete",  # une racine hors du projet
    ):
        assert portee.commande_hors_portee(commande) != "", commande


def test_un_find_qui_lance_une_lecture_ne_detruit_rien() -> None:
    portee = _portee_p3(("", "notes.md"))

    assert portee.commande_hors_portee("find . -name '*.md' -exec wc -l {} +") == ""
    assert "sort du" in portee.commande_hors_portee("find . -name '*.md' -exec cp {} /tmp/x \\;")


def test_l_acte_de_s8_remonte_toujours() -> None:
    """La commande que le README de S8 fait jouer (`CONVENTION_REGISTRE`) : une
    ligne ajoutée à un registre tenu **hors** de la racine."""
    racine = Path("C:/Users/Sam25/maestro-scenarios/20260927-070605/s8-hors-projet")
    registre = "C:/Users/Sam25/maestro-scenarios/20260927-070605/s8-registre/livraisons.txt"
    commande = f'echo "2026-09-27 depensio : total()" >> "{registre}"'

    assert "sort du" in PorteeProjet(racine).commande_hors_portee(commande)
