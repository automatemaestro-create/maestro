"""Tests de la confrontation des critères d'acceptation au diff, à la clôture (#968).

Les critères d'un ticket étaient **écrits** par `/ticket-create`, **lus** au cadrage par
`/ticket-start` — et plus jamais regardés : `/ticket-finish` cochait la checklist de la PR (le
procédé), `merge-mr` jugeait la mergeabilité, et le merge fermait le ticket sans que personne ait
demandé « fait-il ce qu'il disait ? ». C'est le défaut que `/milestone-bilan` corrige au jalon
(#759), ici à l'échelle du ticket, où l'occasion se détruit au merge.

Ce module garde, sur le modèle de la relecture visuelle (#935, `test_relecture_visuelle.py`) :

* **la question** (`lib.sh criteres`) — les critères numérotés mot pour mot, la case vide du gabarit
  qui n'en est pas un, le **repli bug** sur « Comportement attendu » (arbitré sur #968), et le
  ticket inconnu qui ne se fait pas passer pour un ticket sans critère ;
* **la trace** (`lib.sh criteres-note`) — l'ancre et son compte, l'idempotence, et la **forme** du
  constat : chaque `Cn` répondu, une pièce jamais vide, un **✓ qui nomme sa preuve exercée** —
  un test défini dans l'arbre, un passage du banc que son rapport rend vert, un run, une capture
  (#1240, qui remplace le ✓ « nomme un fichier du diff » de #968). Ce qui se vérifie est une forme,
  jamais un sens (#746) ;
* **le banc** (#1240) — un diff qui touche le chemin des scénarios porte une ligne `Banc` : le
  verdict relu dans le rapport du passage écrit par le **vrai** `maestro.scenarios.rapport`, ou
  « non joué » avec sa raison — un banc rouge ou injouable n'est jamais compté vert ;
* **le signalement** (`criteres-note --aucun`) — un ticket sans critère se signale plutôt que de se
  taire (arbitré sur #968), mais ne se **déclare** pas : le verbe relit le ticket avant d'écrire ;
* **le déclencheur** — l'étape 4ter de `/ticket-finish`, après la relecture visuelle et **avant** le
  filet CI, où un ✗ est nommé sans bloquer le merge, et dont `/ticket-ship` hérite sans une ligne ;
* **le balayage** — le geste vit en un seul endroit, et toute commande qui ouvre une PR le joue.
  Chaque motif de balayage est **prouvé sur un échantillon fautif** avant de balayer : sans cette
  moitié, un motif mal écrit rendrait un vert sur une question jamais posée (méthode de #366).

Chaque refus est éprouvé avec **ce qu'il ne laisse pas derrière lui** : aucune écriture de forge, et
pour les refus gratuits, pas même une lecture (règle de `gl_reste_claude`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from harnais_forge import BASH, GIT, RACINE, Depot, ecritures, monte_depot

from maestro.scenarios import rapport as rapport_du_banc
from maestro.scenarios.modele import VERDICT_ROUGE, VERDICT_VERT, Rapport, Resultat

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

PROMPT_FINISH = RACINE / ".claude" / "commands" / "ticket-finish.md"
PROMPT_SHIP = RACINE / ".claude" / "commands" / "ticket-ship.md"
DOSSIER_CLAUDE = RACINE / ".claude"
LIB = RACINE / "scripts" / "gitlab" / "lib.sh"
RUN = RACINE / "scripts" / "orchestrate" / "run.sh"
DOC_WORKFLOW = RACINE / "docs" / "10-workflow-git.md"

#: Le test que la branche de la fixture livre : c'est lui qu'un ✓ nomme pour être tenu.
TEST_LIVRE = "test_le_verbe_rend_zero"
PREUVE = f"`{TEST_LIVRE}` passé"

#: La description d'un ticket ordinaire : trois cases garnies, une case laissée vide par le gabarit
#: (qui n'est pas un critère), et une case hors de la section (qui n'en est pas un non plus).
CORPS_TROIS = """## Description

Un verbe de plus.

## Critères d'acceptation

- [ ] Le verbe rend `0` sur un ticket connu.
- [x] Son refus tombe **avant** toute écriture.
- [ ]
- [ ] La doc le nomme.

## Notes techniques

- [ ] pas un critère : une note de travail
"""

#: Un bug tel que le gabarit `bug.md` le pose : aucune section de critères, un comportement attendu.
CORPS_BUG = """## Comportement observé

Le verbe rend 1.

## Comportement attendu

Le verbe rend 0,
- et dit pourquoi.

## Étapes de reproduction
1. jouer le verbe
"""

CORPS_SANS_RIEN = """## Description

Rien qui dise ce que le ticket doit tenir.
"""


def regle_criteres(iid: str, corps: str, notes: tuple[str, ...] = (), existe: bool = True) -> dict:
    """Réponse à `gh_criteres_lecture` — UN aller pour les trois questions : le ticket existe-t-il,
    quels sont ses critères, la confrontation y est-elle déjà ? L'ORDRE des clés est celui de la
    requête, comme GitHub le rend : la description se lit AVANT la clé « comments »."""
    issue = (
        None
        if not existe
        else {
            "title": "Un ticket à clore",
            "body": corps,
            "comments": {"nodes": [{"body": note} for note in notes]},
        }
    )
    return {
        "contient": [f"issue(number:{iid})", "title body comments(first: 100)"],
        "reponse": {"data": {"repository": {"issue": issue}}},
    }


@pytest.fixture
def forge(tmp_path: Path) -> Depot:
    """Le dépôt jetable, sur une branche de ticket qui livre `livre.sh` et le test qui l'exerce.

    `.maestro/` est exclu comme dans le vrai dépôt : sans quoi le constat lui-même, fichier non
    suivi, compterait parmi les fichiers « du diff » — et un rapport du banc aussi.
    """
    depot = monte_depot(tmp_path)
    (depot.racine / ".git" / "info" / "exclude").write_text(".maestro/\n", encoding="utf-8")
    depot.git("checkout", "--quiet", "-b", "chore/60-essai")
    depot.commit("livre.sh", "echo livré\n", "feat: livre le verbe\n\nRefs #60")
    (depot.racine / "tests").mkdir()
    depot.commit(
        "tests/test_livre.py",
        f"def {TEST_LIVRE}():\n    assert True\n",
        "test: exerce le verbe\n\nRefs #60",
    )
    return depot


def resultat(identifiant: str, verdict: str = VERDICT_VERT, **reste: object) -> Resultat:
    return Resultat(
        identifiant=identifiant,
        titre=f"Scénario {identifiant}",
        verdict=verdict,
        motif="motif",
        duree_s=85.0,
        run_id="728afd3dae61",
        **reste,  # type: ignore[arg-type]
    )


def passage(forge: Depot, horodatage: str, *resultats: Resultat) -> str:
    """Un passage du banc, écrit par le VRAI rapporteur (`maestro.scenarios.rapport.ecrire`), là où
    le banc l'écrit : si sa forme change, c'est ce test qui le dit, pas une clôture en run."""
    racine = forge.racine / rapport_du_banc.RACINE_RAPPORTS
    rapport_du_banc.ecrire(Rapport(horodatage=horodatage, resultats=resultats), racine=racine)
    return horodatage


def constat(forge: Depot, texte: str, nom: str = "criteres.md") -> str:
    """Écrit un constat dans l'atelier de session et rend son chemin RELATIF — le régime réel d'une
    session, à qui tout chemin absolu est refusé (docs/10 §11.7)."""
    chemin = forge.racine / ".maestro" / "session" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8", newline="\n")
    return str(chemin.relative_to(forge.racine)).replace("\\", "/")


def tableau(*lignes: tuple[str, str, str]) -> str:
    entete = "| Critère | Réponse | Pièce |\n|---|---|---|\n"
    return entete + "".join(f"| {c} | {r} | {p} |\n" for c, r, p in lignes)


CONSTAT_COMPLET = tableau(
    ("C1", "✓ tenu", f"`tests/test_livre.py::{TEST_LIVRE}` passé — le verbe de `livre.sh`"),
    ("C2", "✗ non tenu", "aucun refus n'est écrit"),
    ("C3", "hors diff", "la doc vit dans le wiki"),
)


def notes_postees(forge: Depot, iid: str) -> list[str]:
    return [
        ligne
        for ligne in forge.appels()
        if "-X\tPOST" in ligne and f"issues/{iid}/comments" in ligne
    ]


def corps_poste(forge: Depot, iid: str) -> str:
    """Le texte réellement envoyé : il voyage par `-F body=@<fichier>` (#233), que le double
    résout en journalisant son contenu, sauts de ligne échappés."""
    return notes_postees(forge, iid)[-1].split("\tbody=", 1)[1].replace("\\n", "\n")


def lectures(forge: Depot) -> list[str]:
    return [ligne for ligne in forge.appels() if ligne.startswith("api\tgraphql")]


# =================================================================================================
# La question — `lib.sh criteres`
# =================================================================================================


def test_les_criteres_sont_numerotes_mot_pour_mot(forge: Depot) -> None:
    """C'est le texte du ticket qui fait foi, jamais une reformulation : les numéros `Cn` sont ceux
    auxquels le constat répondra, et le verbe de trace les recompte de la même façon."""
    forge.pose_etat(graphql=[regle_criteres("60", CORPS_TROIS)])
    acheve = forge.lib("criteres", "60")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lignes = acheve.stdout.splitlines()
    assert lignes[0] == "# source\tCritères d'acceptation"
    assert lignes[1:] == [
        "C1\tLe verbe rend `0` sur un ticket connu.",
        "C2\tSon refus tombe **avant** toute écriture.",
        "C3\tLa doc le nomme.",
    ], "la case vide du gabarit et la case des notes techniques ne sont pas des critères"


def test_une_case_vide_du_gabarit_nest_pas_un_critere(forge: Depot) -> None:
    """`/ticket-create` laisse les cases `- [ ]` vides quand personne n'a donné de critères : une
    section restée telle que le gabarit la pose ne tient rien, et compte pour absente — même partage
    que `gl_section_de`, où un titre nu ne répond rien."""
    corps = "## Critères d'acceptation\n- [ ]\n- [ ]\n\n## Notes techniques\n"
    forge.pose_etat(graphql=[regle_criteres("61", corps)])
    acheve = forge.lib("criteres", "61")
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert acheve.stdout == ""
    assert "criteres-note --aucun" in acheve.stderr, "le code 3 nomme le geste qui le signale"


def test_un_bug_se_juge_sur_son_comportement_attendu(forge: Depot) -> None:
    """Le repli arbitré sur #968 : le gabarit `bug.md` n'a pas de section de critères, son contrat
    est « Comportement attendu ». Il compte comme critère UNIQUE, sur une ligne."""
    forge.pose_etat(graphql=[regle_criteres("62", CORPS_BUG)])
    acheve = forge.lib("criteres", "62")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    lignes = acheve.stdout.splitlines()
    assert lignes[0].startswith("# source\tComportement attendu"), lignes[0]
    assert "repli" in lignes[0], "la source dit que c'est un repli, pas une section de critères"
    assert lignes[1:] == ["C1\tLe verbe rend 0, - et dit pourquoi."]


def test_les_cases_garnies_lemportent_sur_le_comportement_attendu(forge: Depot) -> None:
    """Contre-exemple du précédent : le repli n'est qu'un repli. Un bug qui porte des critères
    écrits se juge sur eux, sans quoi deux contrats se disputeraient le même `C1`."""
    corps = CORPS_BUG + "\n## Critères d'acceptation\n- [ ] Le verbe rend 0.\n"
    forge.pose_etat(graphql=[regle_criteres("63", corps)])
    acheve = forge.lib("criteres", "63")
    assert acheve.returncode == 0
    assert acheve.stdout.splitlines() == [
        "# source\tCritères d'acceptation",
        "C1\tLe verbe rend 0.",
    ]


def test_sans_rien_la_reponse_est_aucun_critere(forge: Depot) -> None:
    forge.pose_etat(graphql=[regle_criteres("64", CORPS_SANS_RIEN)])
    acheve = forge.lib("criteres", "64")
    assert acheve.returncode == 3
    assert acheve.stdout == ""


def test_un_ticket_inconnu_ne_se_fait_pas_passer_pour_un_ticket_sans_critere(forge: Depot) -> None:
    """Le `3` est une RÉPONSE, sur laquelle l'appelant écrit un signalement : un iid mal tapé qui le
    rendrait ferait signaler « sans critère » un ticket qui n'existe pas."""
    forge.pose_etat(graphql=[regle_criteres("65", "", existe=False)])
    acheve = forge.lib("criteres", "65")
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert "introuvable" in acheve.stderr


def test_la_question_ne_coute_quun_aller(forge: Depot) -> None:
    """Ce qui se garde est le NOMBRE d'allers, jamais une durée (#577, #602)."""
    forge.pose_etat(graphql=[regle_criteres("66", CORPS_TROIS)])
    forge.lib("criteres", "66")
    assert len(lectures(forge)) == 1


@pytest.mark.parametrize("args", [(), ("chat",)])
def test_un_usage_fautif_est_refuse_sans_rien_lire(forge: Depot, args: tuple[str, ...]) -> None:
    acheve = forge.lib("criteres", *args)
    assert acheve.returncode == 2
    assert forge.appels() == []


# =================================================================================================
# La trace — `lib.sh criteres-note`
# =================================================================================================


def test_le_constat_est_consigne_sous_une_ancre_qui_porte_son_compte(forge: Depot) -> None:
    """Sans en-tête reconnaissable, « ce ticket a-t-il été confronté ? » n'a pas de réponse. Le
    compte est dans le TITRE — là où on le lit sans dérouler —, et un ✗ y est donc visible d'un coup
    d'œil : nommé, jamais fondu dans un « couvert »."""
    forge.pose_etat(graphql=[regle_criteres("70", CORPS_TROIS)])
    acheve = forge.lib("criteres-note", "70", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    corps = corps_poste(forge, "70")
    assert corps.startswith(
        "## Critères confrontés au diff — 1 ✓ · 1 ✗ · 1 hors diff — empreinte "
    ), corps.splitlines()[0]
    assert "aucun refus n'est écrit" in corps, "le constat voyage entier"
    assert "n'a pas bloqué le merge" in corps
    assert "1 ✓ · 1 ✗ · 1 hors diff" in acheve.stdout


def test_rejoue_a_lidentique_il_necrit_rien(forge: Depot) -> None:
    """`/ticket-finish` se rejoue — pipeline rouge, deux passes `/mr-fix`, reprise d'un run. Même
    contrat que `relecture-note` : rejeu à l'identique MUET."""
    fichier = constat(forge, CONSTAT_COMPLET)
    forge.pose_etat(graphql=[regle_criteres("71", CORPS_TROIS)])
    forge.lib("criteres-note", "71", fichier)
    premiere = corps_poste(forge, "71").splitlines()[0]
    empreinte = premiere.rsplit("empreinte ", 1)[1].strip()

    forge.pose_etat(graphql=[regle_criteres("71", CORPS_TROIS, notes=(premiere,))])
    avant = len(notes_postees(forge, "71"))
    acheve = forge.lib("criteres-note", "71", fichier)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "déjà" in acheve.stdout
    assert empreinte in acheve.stdout
    assert len(notes_postees(forge, "71")) == avant, "aucun second commentaire"


def test_un_constat_enrichi_sajoute_au_lieu_decraser(forge: Depot) -> None:
    """Contre-exemple du précédent : un second constat — après correction d'un ✗ — est un second
    fait, pas la correction du premier."""
    forge.pose_etat(graphql=[regle_criteres("72", CORPS_TROIS, notes=("## X — empreinte 111-22",))])
    acheve = forge.lib("criteres-note", "72", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(notes_postees(forge, "72")) == 1


def test_un_critere_sans_reponse_est_refuse_et_nomme(forge: Depot) -> None:
    """Le refus que tout le dispositif porte : une question tue n'est pas une question couverte."""
    forge.pose_etat(graphql=[regle_criteres("73", CORPS_TROIS)])
    incomplet = tableau(("C1", "✓", f"`{TEST_LIVRE}` passé"), ("C3", "hors diff", "le wiki"))
    acheve = forge.lib("criteres-note", "73", constat(forge, incomplet))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "- C2 : sans réponse" in acheve.stderr
    assert "- C1" not in acheve.stderr and "- C3" not in acheve.stderr, (
        "seul le critère sans réponse est nommé"
    )
    assert ecritures(forge) == []


@pytest.mark.parametrize("piece", ["fait", "`livre.sh` — le verbe", "tests/test_livre.py"])
def test_un_coche_sans_preuve_exercee_est_refuse(forge: Depot, piece: str) -> None:
    """La règle de `/milestone-bilan` — « un critère tenu sans pièce nommée n'est pas tenu » —
    portée de l'écrit à l'exercé (#1240). « ✓ | fait | » est le ✓ sur une question jamais posée ;
    `livre.sh`, fichier du diff, était la preuve de #968 et n'en est plus une ; un fichier de suite
    pytest ne nomme pas le test qui passe."""
    forge.pose_etat(graphql=[regle_criteres("74", CORPS_TROIS)])
    fautif = tableau(("C1", "✓", piece), ("C2", "✗", "rien"), ("C3", "hors diff", "le wiki"))
    acheve = forge.lib("criteres-note", "74", constat(forge, fautif))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "C1 : ✓ sans preuve exercée" in acheve.stderr
    assert "jamais ✓ pour faire passer" in acheve.stderr, "le refus dit comment se réparer"
    assert ecritures(forge) == []


@pytest.mark.parametrize(
    ("piece", "code"),
    [
        (f"`{TEST_LIVRE}` passé", 0),
        (f"tests/test_livre.py::{TEST_LIVRE} — 1 passed", 0),
        (f"`{TEST_LIVRE}_bis` passé", 5),
        (f"`mon{TEST_LIVRE}` passé", 5),
    ],
)
def test_un_test_nomme_ne_compte_que_borne(forge: Depot, piece: str, code: int) -> None:
    """Le ✓ avec test nommé ACCEPTÉ, et ses deux contrefaçons : sans borne,
    `test_le_verbe_rend_zero` serait « nommé » par un nom plus long. Les deux premiers cas sont
    les témoins : le motif sait dire oui, y compris sous la forme `fichier::fonction` de pytest."""
    forge.pose_etat(graphql=[regle_criteres("76", CORPS_TROIS)])
    texte = tableau(("C1", "✓", piece), ("C2", "✗", "rien"), ("C3", "hors diff", "le wiki"))
    acheve = forge.lib("criteres-note", "76", constat(forge, texte))
    assert acheve.returncode == code, acheve.stdout + acheve.stderr


def test_un_test_invente_est_refuse_et_nomme(forge: Depot) -> None:
    """Le verbe ne rejoue pas le test, mais un nom qu'aucun `def` ne porte n'a jamais passé : il
    est refusé, et nommé — c'est le ✓ fabriqué le plus facile à écrire."""
    forge.pose_etat(graphql=[regle_criteres("75", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", "`test_le_verbe_invente` passé"),
        ("C2", "✗", "rien"),
        ("C3", "hors diff", "wiki"),
    )
    acheve = forge.lib("criteres-note", "75", constat(forge, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "test_le_verbe_invente n'est défini nulle part" in acheve.stderr
    assert ecritures(forge) == []


def test_une_suite_vitest_se_nomme_par_son_fichier(forge: Depot) -> None:
    """Vitest nomme ses tests en prose (`it("…")`) : son nom de test est le fichier, par chemin ou
    par nom. Un fichier `.test.tsx` absent de l'arbre ne prouve rien."""
    (forge.racine / "apps" / "web" / "tests").mkdir(parents=True)
    forge.commit(
        "apps/web/tests/ecran.test.tsx", "it('rend', () => {})\n", "test: écran\n\nRefs #60"
    )
    forge.pose_etat(graphql=[regle_criteres("77", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", "`apps/web/tests/ecran.test.tsx` — 3 passés"),
        ("C2", "✓", "ecran.test.tsx vert"),
        ("C3", "✓", "`absent.test.tsx` vert"),
    )
    acheve = forge.lib("criteres-note", "77", constat(forge, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "C3 : ✓ sans preuve exercée" in acheve.stderr
    assert "C1" not in acheve.stderr and "C2" not in acheve.stderr


def test_un_test_non_commite_compte_aussi(forge: Depot) -> None:
    """Ce que la clôture va livrer, pas seulement ce qui est déjà commité : un test écrit et joué,
    pas encore commité, est un test de l'arbre."""
    (forge.racine / "tests" / "test_neuf.py").write_text(
        "def test_le_neuf_passe():\n    assert True\n", encoding="utf-8"
    )
    forge.pose_etat(graphql=[regle_criteres("78", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", "`test_le_neuf_passe` passé"), ("C2", "✗", "rien"), ("C3", "hors diff", "wiki")
    )
    assert forge.lib("criteres-note", "78", constat(forge, texte)).returncode == 0


@pytest.mark.parametrize(
    ("piece", "code"),
    [
        ("observé sur la vraie stack, run `728afd3dae61`", 0),
        ("Run 728afd3dae61 : la réponse nomme la cause", 0),
        ("run `728afd` — trop court pour un identifiant", 5),
        ("le fichier 728afd3dae61 sans run", 5),
    ],
)
def test_un_run_de_la_vraie_stack_prouve(forge: Depot, piece: str, code: int) -> None:
    """Une observation sur la vraie stack se nomme par le run qui la montre (#1240). Le verbe ne
    peut pas interroger une API peut-être éteinte : il garde la FORME — le mot et un identifiant de
    run entier —, et la session répond du reste."""
    forge.pose_etat(graphql=[regle_criteres("79", CORPS_TROIS)])
    texte = tableau(("C1", "✓", piece), ("C2", "✗", "rien"), ("C3", "hors diff", "le wiki"))
    acheve = forge.lib("criteres-note", "79", constat(forge, texte))
    assert acheve.returncode == code, acheve.stdout + acheve.stderr


def test_une_capture_de_la_vraie_stack_prouve_si_elle_existe(forge: Depot) -> None:
    capture = forge.racine / ".maestro" / "relecture" / "60" / "ecran-clair.png"
    capture.parent.mkdir(parents=True)
    capture.write_bytes(b"\x89PNG\r\n")
    forge.pose_etat(graphql=[regle_criteres("69", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", "`.maestro/relecture/60/ecran-clair.png` — le bouton est là"),
        ("C2", "✓", "`.maestro/relecture/60/ecran-sombre.png`"),
        ("C3", "hors diff", "le wiki"),
    )
    acheve = forge.lib("criteres-note", "69", constat(forge, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "C2 : ✓ sans preuve exercée" in acheve.stderr, "une capture absente ne prouve rien"
    assert "C1" not in acheve.stderr


# =================================================================================================
# Le banc — une preuve par son passage, et dû quand le diff touche le chemin des scénarios (#1240)
# =================================================================================================


def test_un_coche_prouve_par_un_passage_vert_est_accepte(forge: Depot) -> None:
    """Le ✓ avec passage du banc ACCEPTÉ : l'horodatage nomme un rapport présent, et le scénario
    cité y est vert. Le rapport est écrit par le vrai rapporteur du banc."""
    h = passage(forge, "20260923-154349", resultat("S2"), resultat("S4", VERDICT_ROUGE))
    forge.pose_etat(graphql=[regle_criteres("100", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", f"S2 vert au passage {h}, run `728afd3dae61`"),
        ("C2", "✗", "rien"),
        ("C3", "hors diff", "le wiki"),
    )
    acheve = forge.lib("criteres-note", "100", constat(forge, texte))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr


@pytest.mark.parametrize(
    ("resultats", "cite", "motif"),
    [
        ((resultat("S2", VERDICT_ROUGE),), "S2", "rend S2 rouge"),
        ((resultat("S2", VERDICT_ROUGE, empechement=True),), "S2", "rend S2 rouge"),
        ((resultat("S2"), resultat("S4", VERDICT_ROUGE)), "", "rend S4 rouge"),
        ((resultat("S2"),), "S3", "S3 n'est pas au passage"),
    ],
)
def test_un_banc_rouge_ou_injouable_nest_jamais_compte_vert(
    forge: Depot, resultats: tuple[Resultat, ...], cite: str, motif: str
) -> None:
    """Le cœur du critère 2 : un ✓ qui cite un passage est relu dans son rapport. Un rouge, un
    empêché (rouge au rapport, `empechement` vrai), un passage qui n'est pas vert en entier quand
    aucun scénario n'est cité, un scénario absent du passage : aucun ne tient un ✓."""
    h = passage(forge, "20260923-101010", *resultats)
    forge.pose_etat(graphql=[regle_criteres("101", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", f"{cite} au passage {h}".strip()),
        ("C2", "✗", "rien"),
        ("C3", "hors diff", "le wiki"),
    )
    acheve = forge.lib("criteres-note", "101", constat(forge, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert motif in acheve.stderr
    assert ecritures(forge) == []


def test_un_passage_sans_rapport_ne_prouve_rien(forge: Depot) -> None:
    forge.pose_etat(graphql=[regle_criteres("102", CORPS_TROIS)])
    texte = tableau(
        ("C1", "✓", "S2 vert au passage 20260923-000000"),
        ("C2", "✗", "rien"),
        ("C3", "hors diff", "w"),
    )
    acheve = forge.lib("criteres-note", "102", constat(forge, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "rapport introuvable : .maestro/scenarios/20260923-000000/rapport.json" in acheve.stderr


@pytest.fixture
def forge_produit(forge: Depot) -> Depot:
    """La même branche, qui touche en plus le chemin des scénarios : le moteur du produit."""
    (forge.racine / "maestro").mkdir()
    forge.commit("maestro/moteur.py", "VALEUR = 1\n", "fix: le moteur\n\nRefs #60")
    return forge


def test_la_question_annonce_le_banc_quand_le_diff_touche_son_chemin(forge_produit: Depot) -> None:
    forge_produit.pose_etat(graphql=[regle_criteres("103", CORPS_TROIS)])
    acheve = forge_produit.lib("criteres", "103")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    derniere = acheve.stdout.splitlines()[-1]
    assert derniere.startswith("# banc\tà jouer"), derniere
    assert "maestro/moteur.py" in derniere


@pytest.mark.parametrize(
    "fichier", ["apps/web/app/page.tsx", "scripts/outil.sh", "agents/lisez-moi.md"]
)
def test_hors_du_chemin_la_question_ne_parle_pas_du_banc(forge: Depot, fichier: str) -> None:
    """Le contre-exemple : l'écran (le banc parle à l'API, jamais à l'UI), l'outillage, et
    `agents/`, qui ne porte que des README. Aucune ligne `# banc`, et le constat ordinaire passe
    sans elle."""
    (forge.racine / fichier).parent.mkdir(parents=True, exist_ok=True)
    forge.commit(fichier, "x\n", "chore: hors chemin\n\nRefs #60")
    forge.pose_etat(graphql=[regle_criteres("104", CORPS_TROIS)])
    assert "# banc" not in forge.lib("criteres", "104").stdout
    assert forge.lib("criteres-note", "104", constat(forge, CONSTAT_COMPLET)).returncode == 0


def test_un_banc_du_et_tu_est_refuse(forge_produit: Depot) -> None:
    """« Un ticket qui touche le chemin d'un scénario le joue avant de pousser » : un constat qui
    ne dit rien du banc est refusé, et le refus nomme ce qui l'a rendu dû."""
    forge_produit.pose_etat(graphql=[regle_criteres("105", CORPS_TROIS)])
    acheve = forge_produit.lib("criteres-note", "105", constat(forge_produit, CONSTAT_COMPLET))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "- Banc : le diff touche le chemin des scénarios" in acheve.stderr
    assert "maestro/moteur.py" in acheve.stderr
    assert ecritures(forge_produit) == []


@pytest.mark.parametrize(
    ("ligne", "entete"),
    [
        (("Banc", "non joué", "l'API ne démarre pas : Redis absent"), "banc non joué"),
        (("Banc", "vert", "passage 20260923-154349 (S2)"), "banc vert (passage 20260923-154349)"),
        (
            ("Banc", "rouge", "S4 au passage 20260923-154349"),
            "banc rouge (passage 20260923-154349)",
        ),
    ],
)
def test_le_verdict_du_banc_entre_au_constat(
    forge_produit: Depot, ligne: tuple[str, str, str], entete: str
) -> None:
    """Son verdict entre au constat, et dans le TITRE du commentaire, là où on le lit sans
    dérouler : vert ou rouge relus dans le rapport, ou « non joué » avec sa raison."""
    passage(forge_produit, "20260923-154349", resultat("S2"), resultat("S4", VERDICT_ROUGE))
    forge_produit.pose_etat(graphql=[regle_criteres("106", CORPS_TROIS)])
    texte = CONSTAT_COMPLET + "| {} | {} | {} |\n".format(*ligne)
    acheve = forge_produit.lib("criteres-note", "106", constat(forge_produit, texte))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    titre = corps_poste(forge_produit, "106").splitlines()[0]
    assert f"1 ✓ · 1 ✗ · 1 hors diff · {entete} — empreinte" in titre, titre


@pytest.mark.parametrize(
    ("ligne", "motif"),
    [
        (("Banc", "vert", "passage 20260923-154349"), "le constat dit vert, le passage"),
        (("Banc", "vert", "S2 et S4 joués"), "nomme son passage"),
        (("Banc", "rouge", "passage 20260923-154349 (S2)"), "le constat dit rouge"),
        (("Banc", "non joué", ""), "« non joué » sans sa raison"),
        (("Banc", "ok", "passage 20260923-154349"), "ni « vert », ni « rouge », ni « non joué »"),
    ],
)
def test_une_ligne_banc_se_relit_dans_son_rapport(
    forge_produit: Depot, ligne: tuple[str, str, str], motif: str
) -> None:
    """Le verdict n'est pas cru sur parole : un « vert » sur un passage qui a un rouge — ici S4 —
    est refusé ; un verdict sans passage ne se relit pas ; un « non joué » sans raison ne dit
    rien."""
    passage(forge_produit, "20260923-154349", resultat("S2"), resultat("S4", VERDICT_ROUGE))
    forge_produit.pose_etat(graphql=[regle_criteres("107", CORPS_TROIS)])
    texte = CONSTAT_COMPLET + "| {} | {} | {} |\n".format(*ligne)
    acheve = forge_produit.lib("criteres-note", "107", constat(forge_produit, texte))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert motif in acheve.stderr
    assert ecritures(forge_produit) == []


def test_le_chemin_des_scenarios_nomme_ce_qui_existe() -> None:
    """Les préfixes de `GL_BANC_CHEMINS` sont des dossiers du dépôt : un renommage qui en laisserait
    un orphelin rendrait le banc muet sur ce qu'il devait voir, sans que rien ne rougisse."""
    ligne = re.search(r'^GL_BANC_CHEMINS="([^"]+)"$', LIB.read_text(encoding="utf-8"), re.M)
    assert ligne, "GL_BANC_CHEMINS introuvable dans lib.sh"
    prefixes = ligne.group(1).split()
    assert "maestro/" in prefixes, "le paquet du produit est le premier chemin des scénarios"
    for prefixe in prefixes:
        assert (RACINE / prefixe).is_dir(), f"{prefixe} n'existe plus dans le dépôt"


@pytest.mark.parametrize(
    ("reponse", "piece", "code"),
    [
        ("✓ couvert", PREUVE, 0),
        ("couvert", PREUVE, 5),
        ("oui", PREUVE, 5),
        ("✗ non couvert", "", 5),
        ("hors diff", "", 5),
        ("Hors diff", "le wiki", 0),
    ],
)
def test_seules_trois_reponses_valent_et_jamais_sans_piece(
    forge: Depot, reponse: str, piece: str, code: int
) -> None:
    """La grammaire du constat, prouvée dans les deux sens : ✓, ✗ ou « hors diff » en tête de la
    réponse, et une pièce jamais vide — un ✗ dit pourquoi, un « hors diff » dit ce qui le montre."""
    forge.pose_etat(graphql=[regle_criteres("79", CORPS_TROIS)])
    texte = tableau(("C1", reponse, piece), ("C2", "✗", "rien"), ("C3", "hors diff", "le wiki"))
    acheve = forge.lib("criteres-note", "79", constat(forge, texte))
    assert acheve.returncode == code, acheve.stdout + acheve.stderr
    if code:
        assert ecritures(forge) == []


def test_un_bug_se_confronte_sur_son_critere_unique(forge: Depot) -> None:
    forge.pose_etat(graphql=[regle_criteres("80", CORPS_BUG)])
    texte = tableau(("C1", "✓", f"`{TEST_LIVRE}` passé — `livre.sh` rend 0 et dit pourquoi"))
    acheve = forge.lib("criteres-note", "80", constat(forge, texte))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "1 ✓ · 0 ✗ · 0 hors diff" in corps_poste(forge, "80").splitlines()[0]


def test_un_constat_sur_un_ticket_sans_critere_est_refuse(forge: Depot) -> None:
    """Confronter des critères qui n'existent pas serait les avoir écrits à la clôture — taillés sur
    ce qui a été livré (règle de `/milestone-bilan`). Le refus nomme le geste juste."""
    forge.pose_etat(graphql=[regle_criteres("81", CORPS_SANS_RIEN)])
    acheve = forge.lib("criteres-note", "81", constat(forge, tableau(("C1", "✓", PREUVE))))
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "criteres-note --aucun 81" in acheve.stderr
    assert ecritures(forge) == []


@pytest.mark.parametrize("nom", ["absent.md", "vide.md"])
def test_le_fichier_est_obligatoire_et_son_refus_ne_lit_rien(forge: Depot, nom: str) -> None:
    """Le refus gratuit passe en premier (règle de `gl_reste_claude`) : ni un appel, ni le risque
    d'une écriture partielle."""
    forge.pose_etat(graphql=[regle_criteres("82", CORPS_TROIS)])
    chemin = constat(forge, "", nom) if nom == "vide.md" else ".maestro/session/absent.md"
    acheve = forge.lib("criteres-note", "82", chemin)
    assert acheve.returncode == 4, acheve.stdout + acheve.stderr
    assert forge.appels() == [], "pas même une lecture"


def test_un_constat_sans_aucune_ligne_cn_est_refuse_sans_rien_lire(forge: Depot) -> None:
    """De la prose n'est pas un constat : sans une seule ligne `| Cn |`, aucun critère ne peut y
    avoir de réponse, et cela se sait sans demander le ticket."""
    forge.pose_etat(graphql=[regle_criteres("83", CORPS_TROIS)])
    fichier = constat(forge, "Tout est couvert, rien à signaler.\n")
    acheve = forge.lib("criteres-note", "83", fichier)
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert forge.appels() == []


def test_un_iid_qui_nen_est_pas_un_est_refuse_sans_rien_lire(forge: Depot) -> None:
    acheve = forge.lib("criteres-note", "chat", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 3
    assert forge.appels() == []


def test_un_ticket_inconnu_est_refuse_sans_rien_ecrire(forge: Depot) -> None:
    forge.pose_etat(graphql=[regle_criteres("84", "", existe=False)])
    acheve = forge.lib("criteres-note", "84", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 3, acheve.stdout + acheve.stderr
    assert "introuvable" in acheve.stderr
    assert ecritures(forge) == []


def test_une_forge_muette_ne_fait_pas_semblant_davoir_consigne(forge: Depot) -> None:
    """Un `1` ne bloque pas la clôture (le prompt le dit), mais il ne se fait pas passer pour un
    succès : ce que le dispositif rend difficile est l'absence de TRACE, jamais le merge."""
    forge.pose_etat(graphql=[{"contient": ["issue(number:85)"], "reponse": {"data": None}}])
    acheve = forge.lib("criteres-note", "85", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 1
    assert ecritures(forge) == []


def test_une_base_du_diff_introuvable_ne_juge_rien(forge: Depot) -> None:
    """Sans base, aucun ✓ ne peut être vérifié : refuser en `5` accuserait le constat d'une panne
    de l'outil. C'est un `1`, et rien n'est écrit — un garde-fou qui saute est pire qu'absent."""
    forge.pose_etat(graphql=[regle_criteres("86", CORPS_TROIS)])
    acheve = forge.lib(
        "criteres-note",
        "86",
        constat(forge, CONSTAT_COMPLET),
        reglages={"GL_CRITERES_BASE": "origin/inexistante"},
    )
    assert acheve.returncode == 1, acheve.stdout + acheve.stderr
    assert ecritures(forge) == []


def test_la_consignation_ne_coute_quun_aller_de_lecture(forge: Depot) -> None:
    """Le ticket, ses critères et ses constats déjà posés voyagent dans la même requête (#602)."""
    forge.pose_etat(graphql=[regle_criteres("87", CORPS_TROIS)])
    forge.lib("criteres-note", "87", constat(forge, CONSTAT_COMPLET))
    assert len(lectures(forge)) == 1


# =================================================================================================
# Le signalement — `criteres-note --aucun`
# =================================================================================================


def test_un_ticket_sans_critere_est_signale_sur_le_ticket(forge: Depot) -> None:
    """Arbitré sur #968 : le signaler, pas le taire. « Rien à confronter » ne doit pas ressembler à
    « tout est couvert » — et le titre le dit, là où on le lit sans dérouler."""
    forge.pose_etat(graphql=[regle_criteres("90", CORPS_SANS_RIEN)])
    acheve = forge.lib("criteres-note", "--aucun", "90")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    corps = corps_poste(forge, "90")
    assert corps.startswith("## Critères confrontés au diff — AUCUN CRITÈRE — empreinte ")
    assert "sans critère d'acceptation écrit" in corps
    assert "Aucun critère n'a été écrit à sa place" in corps
    assert "AUCUN CRITÈRE" in acheve.stdout


def test_le_signalement_rejoue_est_muet(forge: Depot) -> None:
    """Le texte est fixe, donc son empreinte aussi : c'est ce qui rend `--aucun` idempotent sans
    fichier."""
    forge.pose_etat(graphql=[regle_criteres("91", CORPS_SANS_RIEN)])
    forge.lib("criteres-note", "--aucun", "91")
    premiere = corps_poste(forge, "91").splitlines()[0]
    forge.pose_etat(graphql=[regle_criteres("91", CORPS_SANS_RIEN, notes=(premiere,))])
    avant = len(notes_postees(forge, "91"))
    acheve = forge.lib("criteres-note", "--aucun", "91")
    assert acheve.returncode == 0
    assert len(notes_postees(forge, "91")) == avant


@pytest.mark.parametrize("corps", [CORPS_TROIS, CORPS_BUG])
def test_un_ticket_ne_se_declare_pas_sans_critere(forge: Depot, corps: str) -> None:
    """`--aucun` est une échappatoire en un mot si le verbe croit l'appelant sur parole. Il relit le
    ticket : un ticket qui porte des critères — ou un bug qui porte son comportement attendu — les
    confronte."""
    forge.pose_etat(graphql=[regle_criteres("92", corps)])
    acheve = forge.lib("criteres-note", "--aucun", "92")
    assert acheve.returncode == 5, acheve.stdout + acheve.stderr
    assert "C1" in acheve.stderr
    assert ecritures(forge) == []


def test_aucun_ne_prend_pas_de_fichier(forge: Depot) -> None:
    """Un fichier passé avec `--aucun` serait soit ignoré — un constat perdu —, soit consigné — un
    constat sur un ticket sans critère. Les deux sont faux : c'est une erreur d'usage."""
    acheve = forge.lib("criteres-note", "--aucun", "93", constat(forge, CONSTAT_COMPLET))
    assert acheve.returncode == 2
    assert forge.appels() == []


def test_sans_argument_le_verbe_rend_son_usage(forge: Depot) -> None:
    acheve = forge.lib("criteres-note")
    assert acheve.returncode == 2
    assert "usage" in acheve.stderr


# =================================================================================================
# Le déclencheur — l'étape 4ter de `/ticket-finish`
# =================================================================================================


def etape_4ter() -> str:
    """L'étape 4ter, espaces normalisés — le prompt est replié à 100 colonnes, une phrase y est
    coupée n'importe où. Bornée des deux côtés : la 4bis qui la précède parle comme elle."""
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    debut = texte.index("4ter. **Le ticket fait-il ce qu'il disait ?**")
    return " ".join(texte[debut : texte.index("5. **Filet CI local**")].split())


def test_la_cloture_nomme_la_question_la_trace_et_le_signalement() -> None:
    etape = etape_4ter()
    assert "bash scripts/gitlab/lib.sh criteres <iid>" in etape
    trace = "bash scripts/gitlab/lib.sh criteres-note <iid> .maestro/session/criteres-<iid>.md"
    assert trace in etape
    assert "bash scripts/gitlab/lib.sh criteres-note --aucun <iid>" in etape


def test_la_confrontation_passe_apres_la_relecture_et_avant_le_filet_ci() -> None:
    """L'ordre EST la décision arbitrée sur #968 : ce qui peut changer le diff passe avant le
    verdict qui le juge, et après la relecture visuelle, dont le correctif change aussi le diff."""
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    relecture = texte.index("4bis. **Le rendu a-t-il été regardé ?**")
    criteres = texte.index("4ter. **Le ticket fait-il ce qu'il disait ?**")
    filet = texte.index("5. **Filet CI local**")
    assert relecture < criteres < filet
    assert "reprends à l'étape 4" in etape_4ter(), "un manque corrigé repasse par le commit"


def test_un_non_tenu_est_nomme_et_ne_bloque_pas_le_merge() -> None:
    etape = etape_4ter()
    assert "Un critère non tenu est nommé, jamais coché" in etape
    assert "Un ✗ n'empêche pas le merge" in etape
    assert "jamais en cochant pour passer" in etape


def test_la_cloture_demande_une_preuve_exercee_par_critere() -> None:
    """Critère 1 de #1240, côté prompt : les deux preuves qui valent, et ce qui n'en est plus
    une."""
    etape = etape_4ter()
    assert "**Exerce chacun** (#1240)" in etape
    assert "plus sur un fichier du diff" in etape
    assert "**un test nommé qui passe**" in etape
    assert "**une observation sur la vraie stack**" in etape
    assert "la pièce **nomme sa preuve exercée**" in etape


def test_la_cloture_joue_le_banc_du_avant_de_pousser() -> None:
    """Critère 2 de #1240, côté prompt : le banc se joue avant de pousser — donc avant l'étape 7 —,
    par la commande qui le rejoue sur la stack du worktree, et son verdict entre au constat."""
    etape = etape_4ter()
    assert "**avant de pousser**" in etape
    assert "bash scripts/controltower/start.sh --etat-banc --rejouer=<S…> --no-browser" in etape
    assert "ligne **Banc**" in etape
    assert "**Un banc injouable est nommé, jamais compté vert**" in etape
    assert "un rouge ne se rejoue pas jusqu'au vert" in etape
    texte = PROMPT_FINISH.read_text(encoding="utf-8")
    assert texte.index("4ter. **Le ticket fait-il") < texte.index("7. **Pousse la branche.**")


def prompt_de_run() -> str:
    """Le prompt d'une session de ticket, tel que `run.sh` le rend — espaces normalisés."""
    texte = RUN.read_text(encoding="utf-8")
    debut = texte.index("prompt_ticket() {")
    return " ".join(texte[debut : texte.index("\nPROMPT\n}", debut)].split())


def test_le_prompt_de_run_demande_la_preuve_exercee_et_le_banc() -> None:
    """Critère 1 de #1240, côté run : personne ne relit une session de run, donc l'exigence y est
    dite en toutes lettres — la conduite, elle, reste celle de l'étape 4ter, jamais recopiée."""
    prompt = prompt_de_run()
    assert "puis EXERCE chacun : un critère se clôt sur une preuve exercée" in prompt
    assert "jamais sur un fichier du diff" in prompt
    assert "joue le banc AVANT de pousser, au premier plan" in prompt
    assert "un banc injouable se dit « non joué » avec sa raison, jamais vert" in prompt
    assert "La clôture que /ticket-ship enchaîne porte la conduite et la commande" in prompt
    assert "start.sh --etat-banc" not in prompt, "la commande vit dans 4ter, une seule source"


def test_la_doc_du_workflow_decrit_la_regle() -> None:
    """Critère 3 de #1240 : docs/10 §6 décrit la règle — la preuve, le banc dû et ce qui ne bouge
    pas. Bornée à la section 6, où vivent les garde-fous."""
    texte = DOC_WORKFLOW.read_text(encoding="utf-8")
    debut, fin = texte.index("## 6. Garde-fous"), texte.index("## 7. Prérequis")
    section = " ".join(texte[debut:fin].split())
    regle = "**Un critère se clôt sur ce qui a été exercé, pas sur ce qui a été écrit** (#1240)"
    assert regle in section
    assert "**test nommé qui passe**" in section
    assert "**observation sur la vraie stack**" in section
    assert "`GL_BANC_CHEMINS`" in section
    assert "**Un banc injouable est nommé, jamais compté vert.**" in section
    assert "un critère **hors diff** garde sa réponse" in section


def test_sans_critere_on_signale_sans_en_ecrire() -> None:
    """Les deux moitiés de l'arbitrage : le signalement (pas l'abstention muette de la relecture
    sans écran), et l'interdit d'écrire des critères à la clôture."""
    etape = etape_4ter()
    assert "code `3` — aucun critère écrit" in etape
    assert "Signale-le" in etape
    assert "N'écris pas les critères toi-même" in etape


def test_la_confrontation_se_joue_a_lidentique_en_run_et_en_interactif() -> None:
    """Confronter est un constat : en run, une question sans répondant ne fait pas attendre, elle
    fait PASSER (#788) — une étape qui demanderait ne se jouerait donc jamais là où elle compte le
    plus, sur les tickets que personne ne relit."""
    etape = etape_4ter()
    assert "On ne demande pas, on joue" in etape
    assert "à l'identique en run et en interactif" in etape


def test_le_resume_rend_la_confrontation() -> None:
    texte = " ".join(PROMPT_FINISH.read_text(encoding="utf-8").split())
    resume = texte[texte.index("15. Termine par un résumé") :]
    assert "confrontation des critères" in resume
    assert "chaque critère ✗ nommé" in resume


def test_le_ship_herite_sans_seconde_implementation() -> None:
    """Comme la relecture visuelle (#935) et le ramassage (#519) : `/ticket-ship` ANNONCE le geste
    et ne le rejoue pas — une seconde implémentation est le premier moyen que les deux divergent."""
    texte = " ".join(PROMPT_SHIP.read_text(encoding="utf-8").split())
    assert "critères d'acceptation au diff" in texte
    assert "sans une ligne à elle" in texte
    assert "lib.sh criteres" not in texte


# =================================================================================================
# Le balayage — le geste vit en un seul endroit, et toute ouverture de PR le joue
# =================================================================================================
# Deux motifs cherchent un USAGE, jamais une mention (règle de `test_cycle_de_vie.py`) : un prompt
# qui parle de `criteres-note` pour l'expliquer ne l'appelle pas. Chacun est prouvé sur un
# échantillon fautif avant de balayer — sans quoi un motif mal écrit balaierait sans rien voir.

APPEL_TRACE = re.compile(r"lib\.sh criteres-note\b")
APPEL_QUESTION = re.compile(r"lib\.sh criteres(?![-\w])")
OUVRE_UNE_PR = re.compile(r"lib\.sh create-mr\b")


def prompts() -> list[Path]:
    fichiers = sorted(DOSSIER_CLAUDE.rglob("*.md"))
    # Les worktrees de tickets vivent sous `.claude/worktrees/` : ce sont d'autres copies du dépôt,
    # pas les prompts de celui-ci.
    return [f for f in fichiers if "worktrees" not in f.relative_to(DOSSIER_CLAUDE).parts]


def test_les_motifs_de_balayage_savent_dire_oui_et_non() -> None:
    """L'échantillon fautif, avant tout balayage."""
    appel = "   bash scripts/gitlab/lib.sh criteres-note <iid> .maestro/session/x.md\n"
    mention = "   la trace (`criteres-note`) consigne le constat\n"
    assert APPEL_TRACE.search(appel), "motif creux : il ne reconnaît pas un appel"
    assert not APPEL_TRACE.search(mention), "motif trop large : il prend une mention pour un appel"
    question = "   bash scripts/gitlab/lib.sh criteres <iid>\n"
    assert APPEL_QUESTION.search(question)
    assert not APPEL_QUESTION.search(appel), "la trace n'est pas la question"
    assert OUVRE_UNE_PR.search("bash scripts/gitlab/lib.sh create-mr <iid> <fichier>")


def test_seule_la_cloture_consigne_la_confrontation() -> None:
    """Un second appelant de la trace serait une seconde implémentation du geste — la façon dont
    deux prompts finissent par ne plus confronter la même chose."""
    appelants = [f for f in prompts() if APPEL_TRACE.search(f.read_text(encoding="utf-8"))]
    assert len(prompts()) > 10, "balayage vide : le dossier .claude n'a pas été lu"
    assert appelants == [PROMPT_FINISH], [str(f.relative_to(RACINE)) for f in appelants]


def test_toute_commande_qui_ouvre_une_pr_confronte_ses_criteres() -> None:
    """Le constat d'origine de #968 : le mot « critère » n'apparaissait dans aucune commande de
    clôture. Balayé plutôt que vérifié sur la seule `/ticket-finish` : une commande de clôture
    nouvelle qui ouvrirait une PR sans confronter referait le défaut sans que rien ne le dise.
    L'échantillon fautif — une PR ouverte sans la question — prouve que le balayage le verrait."""
    fautif = "bash scripts/gitlab/lib.sh create-mr <iid> <fichier>\n"
    assert OUVRE_UNE_PR.search(fautif) and not APPEL_QUESTION.search(fautif)

    ouvrent = [f for f in prompts() if OUVRE_UNE_PR.search(f.read_text(encoding="utf-8"))]
    assert ouvrent, "aucune commande n'ouvre de PR : le balayage ne garde plus rien"
    sans = [f for f in ouvrent if not APPEL_QUESTION.search(f.read_text(encoding="utf-8"))]
    assert sans == [], [str(f.relative_to(RACINE)) for f in sans]
