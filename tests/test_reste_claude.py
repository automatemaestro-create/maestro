"""Tests du reste à appliquer sous `.claude/` (#613, chantier #608, docs/10 §11.7).

Une session autonome ne peut pas écrire sous `.claude/` — garde-fou du **CLI**, en amont de
l'allowlist comme des hooks (#229, mesuré par #238, re-mesuré par #614) —, et la conduite prescrite
(#188) est de **rendre** le correctif au lieu de contourner. Cette conduite avait un lecteur tant
qu'un humain mergeait ; #418/#419 le lui ont retiré. Le pilote merge, la PR se ferme, et le « reste
à appliquer » finit dans le corps d'une PR que plus personne n'ouvre : rien n'échoue, rien n'est
rouge, et **c'est ce qui rend la perte invisible**. Run `20260827-094044` : trois tickets, deux
résidus (#599, #595), mergés en vingt minutes, encore en place le lendemain.

Ce que ce module garde, **un garde à la fois** — un test qui les vérifierait ensemble ne dirait pas
lequel garde :

* **le verbe** (#610) — le ticket de reprise naît **assigné** et **pourvu d'un état**, son corps
  vient du **fichier**, il est **idempotent** (rejoué à l'identique il n'écrit rien ; rejoué sur un
  autre correctif il **ajoute** au lieu d'écraser), et ses deux refus tombent **avant** toute
  écriture ;
* **la détection du filet** (#611) — `journal.sh refus --claude`, la famille « blocage dur
  `.claude/` » rendue en TSV **par ticket**, muette (code 3) quand il n'y en a pas, et agrégée sur
  un run et le run qu'il reprend ;
* **le contrôle sur le dépôt** — aucun prompt ne prescrit de **contourner** le blocage, et la
  conduite prescrite reste « rendre dans la PR **et** créer le ticket de reprise ». Chaque contrôle
  qui conclut d'une **absence** prouve d'abord son motif sur un **échantillon fautif** (méthode de
  #366, #537 et #578) : sans cette moitié, un motif mal branché rendrait un ✓ sur une question
  jamais posée.

⚠ **Les deux autres surfaces du chantier vivent dans `tests/test_orchestrate.py`**, et ce n'est pas
un oubli : le signalement amont de `queue.sh --touche-claude` (#612) et le bloc de fin de run
`residus_claude` (#611) se jouent sur le **double de `test_orchestrate.py`**, seul à savoir monter
un plan et dérouler un run. Un second double à tenir d'accord serait le premier moyen de rendre une
suite verte sur une forme de réponse que l'autre a corrigée depuis — c'est la raison même pour
laquelle `harnais_forge.py` existe, et elle vaut dans les deux sens.

**Ni réseau ni compte de forge** : harnais de [`harnais_forge.py`](harnais_forge.py), partagé avec
`test_collaboration.py`, `test_cycle_de_vie.py`, `test_decoupage_natif.py`,
`test_merge_automatique.py` et `test_design_veille.py`.
"""

from __future__ import annotations

import json
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

JOURNAL_SH = RACINE / "scripts" / "orchestrate" / "journal.sh"
RUN_SH = RACINE / "scripts" / "orchestrate" / "run.sh"
PROMPT_FINISH = RACINE / ".claude" / "commands" / "ticket-finish.md"
PROMPT_SHIP = RACINE / ".claude" / "commands" / "ticket-ship.md"
PROMPT_ORCHESTRATE = RACINE / ".claude" / "commands" / "orchestrate.md"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


# =================================================================================================
# Le verbe — `lib.sh reste-claude` (#610)
# =================================================================================================
# Le support du résidu passe de la description de PR au BACKLOG : un ticket vit après le merge, il
# remonte dans `/backlog`, il se démarre par `/ticket-start`, et aucun `Closes` ne le ferme par
# inadvertance. Ce qui se garde ici est ce qui fait de lui un ticket UTILISABLE — assigné (donc hors
# des plans), pourvu d'un état (donc visible), et porteur du correctif AU COMPLET.


def regle_source(
    iid: str, titre: str, commentaires: tuple[str, ...] = (), existe: bool = True
) -> dict:
    """Réponse à `gh_reste_source` — la lecture UNIQUE qui répond aux deux questions du verbe.

    « Ce ticket existe-t-il ? » et « a-t-il déjà son ticket de reprise ? » tiennent dans un seul
    aller, et c'est ce qui permet aux refus de tomber avant toute écriture sans rien coûter de plus.
    Le marqueur (« ticket de reprise #<n> ») est cherché dans les COMMENTAIRES : c'est la forme que
    `gl_reste_ancre` pose, et les deux moitiés du contrat se lisent au même endroit.
    """
    issue = None if not existe else {
        "title": titre,
        "comments": {"nodes": [{"body": corps} for corps in commentaires]},
    }
    return {
        "contient": [f"issue(number:{iid})", "comments(first: 100)"],
        "reponse": {"data": {"repository": {"issue": issue}}},
    }


def regle_jalons(courant: str = "Outillage de la forge", numero: int = 17) -> list[dict]:
    """Les DEUX règles que coûte un jalon, et non une (constat de #610).

    `gl_current_milestone` lit les milestones avec leur `description` (le rail y est marqué, #617)
    et leurs compteurs (« non soldé », #619) ; `gh_milestone_number` en relit le NUMÉRO, l'API REST
    ne connaissant pas les titres. Deux requêtes, deux formes, donc deux règles.
    """
    return [
        {
            "contient": ["orderBy: {field: DUE_DATE"],
            "reponse": {"data": {"repository": {"milestones": {"nodes": [
                {
                    "title": courant,
                    "description": "rail: outillage",
                    "state": "OPEN",
                    "dueOn": "2027-09-15T00:00:00Z",
                    "total": {"totalCount": 32},
                    "fermes": {"totalCount": 25},
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
    """De quoi jouer `gl_project_add` sur le ticket qui vient de naître (#361)."""
    return [
        {"contient": ["options{id name}"], "brut": "\n".join(lignes_projet()) + "\n"},
        {
            "contient": [f"issue(number:{iid_cree}) {{ id }}"],
            "reponse": {"data": {"repository": {"issue": {"id": "I_reprise"}}}},
        },
        {
            "contient": ["addProjectV2ItemById"],
            "reponse": {"data": {"addProjectV2ItemById": {"item": {"id": "PVTI_reprise"}}}},
        },
        regle_pose_status(),
    ]


def instruit(depot: Depot, *, source: str = "612", reprise: str = "1", **plus: object) -> None:
    """Le `gh` factice prêt pour une CRÉATION nominale : source lisible, jalon, projet."""
    depot.pose_etat(
        graphql=[
            regle_source(source, "queue.sh signale les tickets qui touchent .claude/"),
            *regle_jalons(),
            *regles_projet(reprise),
        ],
        **plus,
    )


def correctif(depot: Depot, nom: str, texte: str) -> str:
    """Écrit un correctif dans l'atelier de session et rend son chemin RELATIF.

    Relatif parce que c'est le régime réel : une session appelle ses commandes depuis son worktree,
    et tout chemin absolu lui est refusé (§11.7).
    """
    chemin = depot.racine / ".maestro" / "session" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8", newline="\n")
    return str(chemin.relative_to(depot.racine)).replace("\\", "/")


def creations(depot: Depot) -> list[str]:
    """Les appels qui CRÉENT un ticket — c'est leur nombre qui atteste l'absence de doublon."""
    return [ligne for ligne in depot.appels() if "\tapi\t-X\tPOST\t" in "\t" + ligne
            and ligne.endswith("issues") or "/issues\t" in ligne]


def _post_issues(depot: Depot) -> list[str]:
    return [ligne for ligne in depot.appels()
            if "-X\tPOST" in ligne and "\trepos/equipe-test/maestro/issues\t" in ligne + "\t"]


def _patch_issue(depot: Depot, iid: str) -> list[str]:
    return [ligne for ligne in depot.appels()
            if "-X\tPATCH" in ligne and f"repos/equipe-test/maestro/issues/{iid}" in ligne]


def _commentaires(depot: Depot, iid: str) -> list[str]:
    return [ligne for ligne in depot.appels()
            if "-X\tPOST" in ligne and f"issues/{iid}/comments" in ligne]


def test_le_ticket_de_reprise_nait_assigne_et_pourvu_dun_etat(depot: Depot) -> None:
    """Les deux propriétés sans lesquelles le ticket ne remplacerait pas la description de PR.

    **Assigné** : c'est ce qui le tient hors des plans de `queue.sh`, qui filtre sur « À faire ET
    libre » — un run qui le prendrait se ferait refuser la même écriture et reproduirait le résidu.
    **Pourvu d'un état** : rien côté forge n'en pose, et un ticket sans état ne remonte dans aucune
    vue (#361/#363) — il serait aussi perdu que dans la PR qu'on vient de lui préférer.
    """
    instruit(depot)
    chemin = correctif(depot, "correctif.md", "Remplacer la ligne 12 de orchestrate.md.\n")

    acheve = depot.lib("reste-claude", "612", chemin)
    assert acheve.returncode == 0, acheve.stderr

    creation = _post_issues(depot)
    assert len(creation) == 1, f"une création et une seule — {creation}"
    assert f"assignees[]={MOI}" in creation[0], "un ticket LIBRE serait prenable par un run"
    assert "labels[]=type::infra" in creation[0]
    assert "labels[]=agent::orchestrateur" in creation[0]
    assert "labels[]=prio::haute" in creation[0]
    assert "milestone=17" in creation[0], "le rail est SU : `.claude/` est de l'outillage (#617)"

    mutations = [ligne for ligne in depot.appels() if "updateProjectV2ItemFieldValue" in ligne]
    assert len(mutations) == 1, "l'état est posé dans la foulée de la création (#361)"
    assert "À faire" in acheve.stdout


def test_le_corps_du_ticket_est_le_fichier_a_loctet_pres(depot: Depot) -> None:
    """Le correctif EST le corps : il voyage par un fichier, jamais sur la ligne de commande (#233).

    La couche permissions découpe un appel sur ses sauts de ligne et ne matche aucune substitution
    `$(…)` — un correctif multi-ligne passé en argument serait refusé sur la dernière action du
    ticket. Le double résout `-F body=@<fichier>` en son contenu, donc les octets transmis se lisent
    directement dans le journal.
    """
    instruit(depot)
    texte = "Ligne accentuée : « é — ⚠ »\n\n```diff\n-avant\n+après\n```\n"
    chemin = correctif(depot, "accents.md", texte)

    assert depot.lib("reste-claude", "612", chemin).returncode == 0
    corps = _post_issues(depot)[0]
    for morceau in texte.strip().split("\n"):
        if morceau:
            assert morceau in corps.replace("\\n", "\n"), f"« {morceau} » n'a pas été transmis"
    assert "Reprise de #612" in corps.replace("\\n", "\n"), "l'en-tête nomme la source"


def test_la_source_nomme_son_ticket_de_reprise(depot: Depot) -> None:
    """L'ancre, et c'est un CONTRAT : « ticket de reprise #<n> » est ce que le verbe relit.

    Sans elle, un rejeu ne retrouverait pas le ticket déjà ouvert et en ouvrirait un second — la
    propriété d'idempotence tient tout entière à ce commentaire.
    """
    instruit(depot)
    chemin = correctif(depot, "c.md", "correctif\n")

    assert depot.lib("reste-claude", "612", chemin).returncode == 0
    ancres = _commentaires(depot, "612")
    assert len(ancres) == 1, f"une ancre et une seule — {ancres}"
    assert "ticket de reprise #1" in ancres[0].replace("\\n", "\n")


def test_rejoue_sur_un_autre_correctif_il_ajoute_au_lieu_decraser(depot: Depot) -> None:
    """LE test du verbe : deux refus dans un même ticket, ce sont DEUX correctifs.

    Remplacer le corps perdrait le premier — c'est-à-dire exactement ce que ce chantier veut
    sauver. Chaque correctif entre donc dans sa propre section, reconnue à l'empreinte `cksum` de
    son fichier.
    """
    ancien = "## Correctif — empreinte 111-22\n\nle premier correctif\n"
    depot.pose_etat(
        graphql=[
            regle_source("612", "Le lot", commentaires=("… ticket de reprise #653 …",)),
            {
                "contient": ["issue(number:653)", "body }"],
                "reponse": {"data": {"repository": {"issue": {"body": ancien}}}},
            },
        ]
    )
    chemin = correctif(depot, "second.md", "le second correctif\n")

    acheve = depot.lib("reste-claude", "612", chemin)
    assert acheve.returncode == 0, acheve.stderr
    assert _post_issues(depot) == [], "aucun doublon : le ticket de reprise existait déjà"

    patch = _patch_issue(depot, "653")
    assert len(patch) == 1, f"une seule mise à jour — {patch}"
    corps = patch[0].replace("\\n", "\n")
    assert "le premier correctif" in corps, "le correctif d'avant est PERDU si l'on écrase"
    assert "le second correctif" in corps
    assert "complété" in acheve.stdout


def test_rejoue_a_lidentique_il_necrit_rien(depot: Depot) -> None:
    """L'empreinte reconnue : zéro écriture, et le verbe le dit.

    C'est ce qui rend l'appel sans risque dans un prompt — une session qui bute deux fois sur le
    même fichier ne fabrique ni doublon, ni section en double.
    """
    # L'empreinte est celle du FICHIER, calculée par `cksum` : on relit le corps que le verbe vient
    # d'écrire plutôt que de figer un chiffre ici, qui se périmerait au premier octet changé.
    chemin = correctif(depot, "meme.md", "le même correctif\n")
    pose_reprise_deja_a_jour(depot, chemin)

    acheve = depot.lib("reste-claude", "612", chemin)
    assert acheve.returncode == 0, acheve.stderr
    assert ecritures(depot) == [], f"un correctif déjà là ne s'écrit pas — {ecritures(depot)}"
    assert "rien à écrire" in acheve.stdout


def pose_reprise_deja_a_jour(depot: Depot, chemin: str) -> None:
    """Pose l'état du double pour un ticket de reprise portant DÉJÀ le correctif de `chemin`.

    L'empreinte est obtenue du verbe lui-même — un premier passage, dont on relit le corps
    transmis. C'est la seule façon de l'écrire sans figer un `cksum` dans le test.
    """
    instruit(depot)
    assert depot.lib("reste-claude", "612", chemin).returncode == 0
    corps = _post_issues(depot)[0].split("body=", 1)[1].replace("\\n", "\n")
    depot.journal.write_text("", encoding="utf-8")
    depot.pose_etat(
        graphql=[
            regle_source("612", "Le lot", commentaires=("ticket de reprise #653",)),
            {
                "contient": ["issue(number:653)", "body }"],
                "reponse": {"data": {"repository": {"issue": {"body": corps}}}},
            },
        ]
    )


@pytest.mark.parametrize(
    ("cas", "fichier", "code"),
    [
        ("fichier absent", "*absent*", 4),
        ("fichier vide", "*vide*", 4),
        ("source inconnue", "*ok*", 3),
    ],
)
def test_les_refus_tombent_avant_toute_ecriture(
    depot: Depot, cas: str, fichier: str, code: int
) -> None:
    """Éprouvés UN PAR UN, et sur la seule propriété qui compte : « refus » et « écriture
    partielle » sont **mutuellement exclusifs**.

    Un verbe qui créerait le ticket puis échouerait sur son corps laisserait un ticket de reprise
    VIDE de la seule chose qui compte — pire que pas de ticket, puisqu'il aurait l'air d'un
    rattrapage. Le contrôle gratuit (le fichier) passe donc avant le réseau, et l'iid source est
    validé avant la première écriture.
    """
    depot.pose_etat(graphql=[regle_source("612", "Le lot", existe=(cas != "source inconnue"))])
    if fichier == "*absent*":
        cible = ".maestro/session/jamais-ecrit.md"
    elif fichier == "*vide*":
        cible = correctif(depot, "vide.md", "")
    else:
        cible = correctif(depot, "ok.md", "un correctif\n")

    acheve = depot.lib("reste-claude", "612", cible)
    assert acheve.returncode == code, f"{cas} : {acheve.stdout}{acheve.stderr}"
    assert ecritures(depot) == [], f"{cas} a écrit quelque chose : {ecritures(depot)}"


def test_le_fichier_est_juge_avant_le_moindre_appel_de_forge(depot: Depot) -> None:
    """Le contrôle GRATUIT passe en premier — pas une écriture de moins, un aller de moins.

    C'est ce qui garantit qu'un refus ne laisse rien derrière lui, y compris sur une forge muette :
    il n'y a rien à défaire quand on n'a rien demandé.
    """
    depot.pose_etat(graphql=[])
    acheve = depot.lib("reste-claude", "612", ".maestro/session/jamais-ecrit.md")
    assert acheve.returncode == 4
    assert depot.appels() == [], (
        f"un appel a été fait avant de regarder le fichier : {depot.appels()}"
    )


def test_le_verbe_de_lecture_rend_liid_et_ne_confond_pas_ses_trois_reponses(depot: Depot) -> None:
    """`reste-claude-de` : 0 il en a un · 3 aucun · 1 illisible — le 1 couvrant « source inconnue ».

    L'appelant a **trois** conduites et pas quatre : nommer le ticket de reprise, dire qu'il
    manque, ou dire qu'il n'a pas pu regarder. Confondre « pas de reprise » et « je n'ai pas su
    lire » ferait annoncer un résidu perdu sur une forge momentanément muette — c'est-à-dire crier
    au loup depuis un signalement best-effort.
    """
    ancre = ("ticket de reprise #653",)
    depot.pose_etat(graphql=[regle_source("612", "Le lot", commentaires=ancre)])
    acheve = depot.lib("reste-claude-de", "612")
    assert acheve.returncode == 0 and acheve.stdout.strip() == "653"

    depot.pose_etat(graphql=[regle_source("612", "Le lot")])
    assert depot.lib("reste-claude-de", "612").returncode == 3, "aucun n'est une RÉPONSE"

    depot.pose_etat(graphql=[regle_source("612", "Le lot", existe=False)])
    assert depot.lib("reste-claude-de", "612").returncode == 1, "source inconnue vaut « illisible »"


def test_le_verbe_de_lecture_necrit_jamais(depot: Depot) -> None:
    """C'est lui que le pilote appelle une fois par ticket ayant buté : il ne doit rien changer."""
    ancre = ("ticket de reprise #653",)
    depot.pose_etat(graphql=[regle_source("612", "Le lot", commentaires=ancre)])
    depot.lib("reste-claude-de", "612")
    assert ecritures(depot) == [], f"lecture seule — {ecritures(depot)}"


# =================================================================================================
# La détection du filet — `journal.sh refus --claude` (#611)
# =================================================================================================
# La famille de refus se LIT, elle ne se redéfinit pas : `refus --claude` se branche sur le
# classement DÉJÀ rendu par #307, jamais sur un second motif. Un `grep .claude/` recopié chez
# l'appelant aurait marché le premier jour et divergé le suivant — et c'est cette version-là, la
# muette, qui se serait trompée sans le dire.


def journal(depot: Depot, run_id: str, refus: dict[str, list[tuple[str, str]]]) -> None:
    """Écrit un journal de run : un `<iid>.json` par ticket, chacun portant ses refus.

    La forme est celle que le CLI rend — un objet `result` minifié sur une ligne, dont
    `permission_denials` porte des `{tool_name, tool_input}`. C'est ce que `run.sh` dépose et ce que
    `journal.sh` relit.
    """
    dossier = depot.racine / ".maestro" / "orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    for iid, refuses in refus.items():
        charge = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "total_cost_usd": 1.0,
            "permission_denials": [
                {"tool_name": outil, "tool_input": ({"file_path": cible}
                                                    if outil != "Bash" else {"command": cible})}
                for outil, cible in refuses
            ],
        }
        (dossier / f"{iid}.json").write_text(
            json.dumps(charge, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )


def refus_claude(depot: Depot, *runs: str) -> tuple[list[list[str]], int]:
    acheve = depot.journal_sh("refus", "--claude", *runs)
    lignes = [ligne.split("\t") for ligne in acheve.stdout.splitlines() if ligne]
    return lignes, acheve.returncode


def test_la_famille_claude_sort_en_tsv_par_ticket(depot: Depot) -> None:
    """Une ligne par TICKET — « iid, refus, exemple de cible » — et non une par refus.

    Le pilote en fait une ligne d'écran par ticket : c'est le ticket qui a un résidu, pas chacun de
    ses fichiers. La cible d'exemple est là parce que sans savoir QUEL fichier a été refusé,
    « poser un ticket de reprise » n'est pas une instruction exécutable.
    """
    journal(depot, "20260827-094044", {
        "595": [("Edit", "/wt/.claude/commands/ticket-finish.md"),
                ("Write", "/wt/.claude/commands/orchestrate.md")],
        "599": [("Edit", "/wt/.claude/commands/orchestrate.md")],
    })
    lignes, code = refus_claude(depot, "20260827-094044")
    assert code == 0
    assert lignes == [
        ["595", "2", "/wt/.claude/commands/ticket-finish.md"],
        ["599", "1", "/wt/.claude/commands/orchestrate.md"],
    ], lignes


def test_un_run_sans_residu_est_muet_et_rend_3(depot: Depot) -> None:
    """Le silence est le CAS NOMINAL, et il porte un code à lui.

    Un appelant best-effort doit distinguer « aucun » (3) de « je n'ai pas pu lire » (1, 2) :
    sans cette distinction, le pilote se tairait de la même façon sur un run sain et sur un journal
    illisible.
    """
    journal(depot, "20260828-000000", {"700": [("Bash", "rm -rf /")], "701": []})
    lignes, code = refus_claude(depot, "20260828-000000")
    assert lignes == [] and code == 3, f"{lignes} / {code}"


def test_seuls_les_outils_de_fichier_visant_claude_comptent(depot: Depot) -> None:
    """L'échantillon fautif du motif : ce qui ressemble à un résidu sans en être un.

    Trois voisins, tous refusés dans le même run — un `Bash` dont la commande NOMME `.claude/`
    (c'est un refus de commande, pas une écriture bloquée), un `Edit` hors `.claude/`, et un `Read`
    sur `.claude/` (lire n'a jamais été le geste bloqué). Aucun n'est un résidu, et si l'un d'eux
    remontait, le pilote enverrait ouvrir un ticket de reprise pour un correctif qui n'existe pas.
    """
    journal(depot, "20260828-111111", {
        "800": [("Bash", "cat .claude/settings.json"),
                ("Edit", "/wt/docs/10-workflow-git.md"),
                ("Read", "/wt/.claude/settings.json")],
    })
    assert refus_claude(depot, "20260828-111111") == ([], 3)

    # …et le motif attrape bien ce qu'il doit attraper, sur le même run enrichi d'un vrai résidu :
    # sans cette moitié, le ✓ ci-dessus serait un ✓ sur une question jamais posée.
    journal(depot, "20260828-222222", {
        "800": [("Bash", "cat .claude/settings.json"),
                ("Edit", "/wt/.claude/commands/ticket-finish.md")],
    })
    lignes, code = refus_claude(depot, "20260828-222222")
    assert code == 0 and [ligne[:2] for ligne in lignes] == [["800", "1"]], lignes


def test_un_run_repris_et_son_run_dorigine_sagregent(depot: Depot) -> None:
    """LE cas que le filet doit attraper, et non un cas de bord (leçon de #593).

    Un run tué n'imprime aucun résumé, donc ses résidus n'ont jamais été nommés ; la reprise rouvre
    les sessions en vol, qui peuvent finir sans rebuter sur `.claude/`. Le résidu passerait alors
    ENTRE les deux runs — d'où les deux journaux demandés ensemble.
    """
    journal(depot, "20260828-100000", {"595": [("Edit", "/wt/.claude/commands/ticket-finish.md")]})
    journal(depot, "20260828-200000", {"599": [("Write", "/wt/.claude/commands/orchestrate.md")]})

    seul, _ = refus_claude(depot, "20260828-200000")
    assert [ligne[0] for ligne in seul] == ["599"], "le run repris, seul, ignore le résidu d'avant"

    ensemble, code = refus_claude(depot, "20260828-200000", "20260828-100000")
    assert code == 0
    assert sorted(ligne[0] for ligne in ensemble) == ["595", "599"], ensemble


def test_le_mode_machine_ne_porte_ni_entete_ni_portee(depot: Depot) -> None:
    """Sa sortie est lue par un `while read` : une ligne d'en-tête deviendrait un faux ticket."""
    journal(depot, "20260828-333333", {"595": [("Edit", "/wt/.claude/commands/x.md")]})
    acheve = depot.journal_sh("refus", "--claude", "20260828-333333")
    assert acheve.stdout == "595\t1\t/wt/.claude/commands/x.md\n", repr(acheve.stdout)


# =================================================================================================
# Le contrôle sur le dépôt — ce que les prompts prescrivent
# =================================================================================================
# Le mécanisme ne tient que si la conduite reste écrite là où la session la lit. Deux choses à
# garder, et elles ne sont pas la même : qu'aucun prompt ne prescrive de CONTOURNER le blocage, et
# que la conduite prescrite reste les DEUX gestes — rendre dans la PR ET créer le ticket de reprise.

# Les formes du contournement, telles que le prompt de run les nomme pour les interdire. Un prompt
# qui les prescrirait dirait à la session de faire exactement ce que le garde-fou empêche — et le
# ferait passer, puisque le CLI ne voit pas à travers un script (#238).
CONTOURNEMENTS = ("printf >", "cp", "script tiers")

# Les deux gestes, dans la forme qui les rend vérifiables : le rendu dans la PR (#188) et l'appel au
# verbe (#610). C'est leur CONJONCTION qui est la conduite — l'un sans l'autre est le défaut que ce
# chantier corrige, dans un sens comme dans l'autre.
GESTES = ("Reste à appliquer à la main", "lib.sh reste-claude")


def _bloc_prompt(nom: str) -> str:
    """Le bloc `.claude/` du prompt de session de `run.sh` — celui que la session lit en dernier."""
    texte = RUN_SH.read_text(encoding="utf-8")
    return texte.split(f"{nom}()", 1)[1].split("PROMPT\n}", 1)[0]


def _gestes_manquants(bloc: str) -> list[str]:
    return [geste for geste in GESTES if geste not in bloc]


def test_le_motif_des_deux_gestes_attrape_un_prompt_a_moitie() -> None:
    """Prouver le motif sur un échantillon fautif avant de balayer.

    L'échantillon n'est pas inventé : « rendre dans la PR, sans ticket de reprise » est EXACTEMENT
    l'état d'avant #610, celui qui a laissé deux résidus du run `20260827-094044` en place. Un
    contrôle qui ne le distinguerait pas de la conduite complète rendrait un ✓ sur une question
    jamais posée.
    """
    assert _gestes_manquants(" ".join(GESTES)) == []
    assert _gestes_manquants("… rends le correctif sous Reste à appliquer à la main …") == [
        "lib.sh reste-claude"
    ], "l'état d'avant #610 doit ressortir, et nommer CE QUI manque"
    assert _gestes_manquants("… bash scripts/gitlab/lib.sh reste-claude 612 fichier …") == [
        "Reste à appliquer à la main"
    ], "le ticket SANS le rendu dans la PR est l'autre moitié — la revue perdrait son objet"
    assert _gestes_manquants("") == list(GESTES)


def test_le_prompt_de_session_prescrit_les_deux_gestes_jamais_lun_pour_lautre() -> None:
    """La PR reste le lieu de la revue, le ticket est ce qui lui survit.

    Les deux prompts, parce qu'ils couvrent les deux moments où une session écrit : l'implémentation
    et la remédiation. Une moitié seule laisserait une session de déblocage reproduire le résidu.
    """
    for nom in ("prompt_ticket", "prompt_mrfix"):
        bloc = _bloc_prompt(nom)
        if ".claude/" not in bloc:
            continue
        assert _gestes_manquants(bloc) == [], (
            f"{nom} ne prescrit que {[g for g in GESTES if g in bloc]} — "
            "l'un sans l'autre est le défaut que #608 corrige"
        )


def test_le_prompt_de_session_interdit_le_contournement() -> None:
    """« Ne le contourne pas » est la seule ligne qui empêche la session de réussir *à tort*.

    Le garde-fou déborde les outils de fichier (#238 : un `cp` dont le CLI lit la cible tombe comme
    un `Write`), mais il ne voit pas à travers un script — donc la retenue est prescrite, pas
    imposée.
    """
    bloc = _bloc_prompt("prompt_ticket")
    assert "Ne le contourne pas" in bloc
    for forme in CONTOURNEMENTS:
        assert forme in bloc, f"la forme « {forme} » n'est pas nommée : elle sera tentée"


def test_aucun_prompt_ne_prescrit_de_contourner_le_blocage() -> None:
    """Le balayage, et sa moitié qui prouve le motif.

    Le motif cherche un USAGE — une commande qui écrirait sous `.claude/` en la cachant au CLI — et
    jamais une MENTION : ce dépôt nomme le blocage une dizaine de fois pour l'interdire, et un motif
    qui ne ferait pas la différence rendrait rouge la documentation de sa propre règle.
    """
    fautifs = [
        "cp .maestro/session/x.md .claude/commands/ticket-finish.md",
        "printf '%s' \"$corps\" > .claude/commands/orchestrate.md",
        "bash scripts/claude/appliquer.sh source .claude/skills/verify",
    ]
    for exemple in fautifs:
        assert _ecrit_sous_claude(exemple), f"le motif laisse passer « {exemple} »"
    for innocent in (
        "Le blocage vient du CLI : écrire sous `.claude/` t'est refusé.",
        "bash scripts/orchestrate/journal.sh refus --claude",
        "grep -n 'claude' .claude/settings.json",
    ):
        assert not _ecrit_sous_claude(innocent), f"le motif rougit sur une mention : « {innocent} »"

    trouves = []
    for prompt in sorted((RACINE / ".claude" / "commands").glob("*.md")):
        for numero, ligne in enumerate(prompt.read_text(encoding="utf-8").splitlines(), 1):
            if _ecrit_sous_claude(ligne):
                trouves.append(f"{prompt.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not trouves, "prompt(s) prescrivant une écriture sous .claude/ :\n" + "\n".join(trouves)


def _ecrit_sous_claude(ligne: str) -> bool:
    """Une commande qui ÉCRIT sous `.claude/` en cachant sa cible au CLI.

    Trois formes, et les trois sont celles que le prompt de session interdit nommément : la copie,
    la redirection, et le script tiers. La lecture (`grep`, `cat`, `journal.sh refus --claude`) n'en
    est pas une — c'est le geste bloqué qui compte, et lire ne l'a jamais été.
    """
    cible = ".claude/"
    if cible not in ligne:
        return False
    nu = ligne.strip().lstrip("-*> ").strip("`")
    if nu.startswith(("cp ", "mv ", "install ")) and cible in nu.split(" ", 1)[1]:
        return True
    if ">" in nu and cible in nu.split(">", 1)[1] and not nu.startswith(("#", "Le ", "La ")):
        return True
    return "scripts/claude/appliquer.sh" in nu


def test_la_clotures_dit_quoi_faire_dun_refus_claude() -> None:
    """`/ticket-finish` est le dernier moment où la session peut encore poser le ticket de reprise.

    Après lui la PR est mergée — depuis #418 par la commande elle-même —, et la description où le
    correctif était rendu se ferme. Le prompt de clôture doit donc nommer les deux gestes, faute de
    quoi le mécanisme repose entièrement sur la mémoire d'une session qui n'en a pas.
    """
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    assert ".claude/" in texte, (
        "le prompt de clôture ne dit rien du blocage dur : le résidu part avec la PR qu'il merge"
    )
    assert "reste-claude" in texte, "il ne nomme pas le verbe qui fait survivre le correctif (#610)"
    assert "Reste à appliquer à la main" in texte, "ni le rendu dans la PR, qui reste la revue"
