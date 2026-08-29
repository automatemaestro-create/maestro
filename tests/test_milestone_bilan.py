"""Le bouclage de fin de milestone : support, convocation, bilan, verdict (#761, parent #756).

Lot final : les lots #757 à #760 ont différé leurs tests ici (docs/10 §5.1). Ce module couvre ce
qu'ils ont fait du bouclage — le **support** de ses deux moitiés, la **convocation** qui le
réclame, et les deux **commandes** qui l'exercent puis l'enregistrent — sur
[`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh),
[`scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) et les deux prompts de
`.claude/commands/`.

CE QUI EST GARDÉ ICI, LOT PAR LOT :

| Lot | Ce que ce module garde |
|---|---|
| #757 | l'aller-retour des deux sections, l'abstention **muette**, l'idempotence, le `rail:` |
| #758 | un jalon soldé sans verdict est nommé, un jalon bouclé ne l'est plus, rien n'est écrit |
| #759 | la commande **s'arrête** sans critères ; un critère non couvert est nommé, jamais coché |
| #760 | le verdict consigné **éteint** la convocation ; aucune réserve sans « oui » |
| transversal | aucune commande ne ferme un jalon ni ne pose de cycle de vie |

⚠ **CE QUE LE BOUCLAGE EST, ET CE QU'IL N'EST PAS.** Il ne ferme aucun milestone (`docs/10 §3.4`,
inchangé depuis toujours) : il produit ce qui manquait **avant** la décision. Et il ne rend aucun
verdict de sa propre autorité — ce qui est outillé est la **détection du manque**, le verdict reste
un jugement humain (partage de #562, #612 et #714). Aucun test ci-dessous ne demande à une machine
de conclure ; ils demandent qu'elle ne conclue **pas** à la place de quelqu'un.

⚠ **CHAQUE CONTRÔLE QUI CONCLUT D'UNE ABSENCE PORTE SON CONTRE-EXEMPLE.** C'est la méthode de
`tests/contraste.test.ts` (#534), du test d'audit de #578 et de `tests/test_design_veille.py` : un
motif mal branché, un décor mal posé ou un fichier mal lu rendraient sinon un ✓ sur une question
jamais posée — c'est-à-dire exactement le défaut que ce chantier corrige, quatorze jalons s'étant
fermés sur « ça a été écrit ». Un test qui dit « ce jalon n'est plus convoqué » vérifie donc
**d'abord** qu'il l'était.

⚠ **LE DOUBLE JOUE `jq`, ET C'EST À DIRE.** Les deux lectures de jalons de `lib.sh` passent par
`gh api --jq` ; le `gh` factice n'exécute pas jq, donc `jalons_rest` (dans `harnais_forge.py`)
refait sa sélection en Python. Ce que cette suite éprouve est donc la **décision du shell** sur des
réponses fidèles, jamais le texte du programme jq — lequel est gardé à part, par lecture de
`lib.sh` (`test_le_double_rejoue_la_selection_du_verbe`). Sans cette seconde moitié, une sélection
modifiée dans le verbe laisserait le double rendre l'ancienne et la suite verte.

**Ni réseau ni compte de forge** : harnais de [`harnais_forge.py`](harnais_forge.py), partagé avec
`test_collaboration.py`, `test_cycle_de_vie.py`, `test_decoupage_natif.py`,
`test_design_veille.py` et `test_merge_automatique.py` — deux doubles à tenir d'accord seraient le
premier moyen de rendre une suite verte sur une forme de réponse que l'autre a corrigée depuis.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    RACINE,
    Depot,
    colonnes,
    description_du_jalon,
    ecritures,
    jalon,
    monte_depot,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

LIB = RACINE / "scripts" / "gitlab" / "lib.sh"
DOCTOR = RACINE / "scripts" / "gitlab" / "doctor.sh"
COMMANDES = RACINE / ".claude" / "commands"
BILAN = COMMANDES / "milestone-bilan.md"
VERDICT = COMMANDES / "milestone-verdict.md"
BACKLOG = COMMANDES / "backlog.md"
DOC_WORKFLOW = RACINE / "docs" / "10-workflow-git.md"
DOC_ROADMAP = RACINE / "docs" / "06-roadmap.md"
CLAUDE_MD = RACINE / "CLAUDE.md"

#: Le seul jalon du dépôt réel qui porte un `rail:` — donc le décor où le critère « le marqueur
#: survit » se prouve. Le reprendre ici garde le test lisible à côté du banc manuel du lot 1.
JALON = "Outillage de la forge"

#: Un cadrage de jalon ORDINAIRE : le marqueur de rail en TÊTE (#617), puis de la prose. C'est
#: exactement ce que l'écriture d'une section ne doit pas déranger.
CADRAGE = "rail: outillage\n\nCe jalon porte l'outillage de la forge et rien d'autre.\n"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


@pytest.fixture
def solde(tmp_path: Path) -> Depot:
    """Un dépôt dont l'unique jalon est ACTIF, ENTIÈREMENT SOLDÉ et marqué `rail: outillage`.

    C'est le décor du bouclage : la phase est finie, sa fermeture reste à décider, et rien n'a
    encore été conclu. Les trois quarts des tests partent de là.
    """
    depot = monte_depot(tmp_path)
    depot.pose_etat(jalons=[jalon(JALON, CADRAGE, fermes=12)])
    return depot


def pose(depot: Depot, nom: str, contenu: str) -> str:
    """Écrit un corps de section dans l'atelier de session et rend son chemin RELATIF.

    Relatif parce que c'est le régime réel (docs/10 §11.7) : un chemin absolu hors du répertoire de
    travail demanderait une approbation qu'une session autonome n'a personne pour donner.
    """
    chemin = depot.racine / ".maestro" / "session" / nom
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return f".maestro/session/{nom}"


def convoques(depot: Depot) -> tuple[int, list[list[str]]]:
    """`milestones-a-boucler` : son code, et ses lignes en colonnes (en-tête `#` écartée)."""
    acheve = depot.lib("milestones-a-boucler")
    return acheve.returncode, colonnes(acheve.stdout)


def prose(chemin: Path) -> str:
    """Le texte d'un fichier, blancs repliés — pour asserter sur une phrase, pas sur sa coupe.

    Ces prompts sont enveloppés à 100 colonnes : une phrase citée ici traverse presque toujours un
    retour à la ligne et une indentation de liste. Épingler la coupe ferait rougir le test sur un
    simple ré-enveloppement, c'est-à-dire sur une édition qui ne change rien à ce qu'il garde.
    """
    return re.sub(r"\s+", " ", chemin.read_text(encoding="utf-8"))


def blocs_de_code(chemin: Path) -> list[str]:
    """Les blocs de code d'un prompt — c'est là que vit une commande QU'IL JOUE.

    ⚠ CETTE DISTINCTION PORTE UN CONTRÔLE, et elle n'est pas un détail de forme. La forme d'appel
    départage ailleurs l'usage de la mention (`_PRESCRIT` de `tests/test_merge_automatique.py`, et
    les motifs transversaux en fin de module) ; elle ne le peut pas ici : `/milestone-bilan` doit
    NOMMER, mot pour mot et à deux arguments, le geste qu'il s'interdit de jouer — « nomme le geste
    qui débloque : `… milestone-criteres "<titre>" <fichier>` ». Prescription et interdiction ont
    donc exactement la même forme, et seul l'ENDROIT les sépare. Les assertions de prose qui
    accompagnent chacun de ces contrôles en sont l'autre moitié.

    Le fence est cherché avec son indentation : dans ces prompts, les blocs vivent sous une étape
    numérotée, donc en retrait de trois espaces.
    """
    texte = chemin.read_text(encoding="utf-8")
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", texte, re.M | re.S)


# =================================================================================================
# Lot #757 — Le support : deux sections dans la description du jalon
# =================================================================================================
# Le support est la description du jalon, comme le marqueur `rail:` de #617 : lue par les humains,
# déjà lue par une machine, et elle vit avec le jalon qu'elle décrit.


def test_l_aller_retour_des_deux_sections(solde: Depot) -> None:
    """Poser puis relire rend le corps au caractère près, et les deux sections cohabitent.

    C'est la promesse minimale du support, et elle porte plus loin qu'il n'y paraît : le lot 4
    consigne un verdict par ce même chemin, et c'est cette relecture-là que la convocation du lot 2
    interroge pour cesser de signaler.
    """
    corps_criteres = "- C1 — le verbe rend 0.\n- C2 — le rail survit."
    corps_verdict = "**GO avec réserves** — 2026-08-29."

    pose_criteres = solde.lib("milestone-criteres", JALON, pose(solde, "c.md", corps_criteres))
    pose_verdict = solde.lib("milestone-verdict", JALON, pose(solde, "v.md", corps_verdict))
    assert pose_criteres.returncode == 0, pose_criteres.stderr
    assert pose_verdict.returncode == 0, pose_verdict.stderr

    relu_criteres = solde.lib("milestone-criteres", JALON)
    relu_verdict = solde.lib("milestone-verdict", JALON)
    assert relu_criteres.returncode == 0, relu_criteres.stderr
    assert relu_criteres.stdout.rstrip("\n") == corps_criteres
    assert relu_verdict.stdout.rstrip("\n") == corps_verdict, "poser l'une n'écrase pas l'autre"


def test_une_section_absente_est_une_abstention_muette(solde: Depot) -> None:
    """Code 3, RIEN sur stdout — un jalon sans critères est le cas nominal, pas une panne.

    Quatorze jalons fermés n'en portent aucun : c'est le partage de `gl_touche_claude_de`, où
    « il ne le nomme pas » est une RÉPONSE et non un silence embarrassé.
    """
    absent = solde.lib("milestone-criteres", JALON)
    assert absent.returncode == 3
    assert absent.stdout == "", "l'abstention est muette : rien, pas même une ligne d'explication"

    # Contre-exemple : le verbe SAIT parler — sans quoi le silence ci-dessus ne prouverait rien.
    solde.lib("milestone-criteres", JALON, pose(solde, "c.md", "- C1"))
    assert solde.lib("milestone-criteres", JALON).returncode == 0


def test_un_titre_nu_ne_compte_pas_pour_une_section(depot: Depot) -> None:
    """L'invariant à ne pas relâcher : une section VIDE compte pour ABSENTE.

    Ce n'est pas un détail de mise en forme. Le lot 2 convoque les jalons « soldés ET sans
    verdict » : si un titre nu suffisait, il suffirait d'écrire « ## Verdict » et rien dessous pour
    éteindre la convocation POUR TOUJOURS, sans que rien ne le dise.
    """
    depot.pose_etat(jalons=[jalon(JALON, "## Verdict\n\n   \n\n", fermes=3)])
    assert depot.lib("milestone-verdict", JALON).returncode == 3

    # Contre-exemple : la même section, un mot dessous — le titre est bien reconnu, c'est le
    # CORPS qui manquait. Sans cette moitié, le 3 ci-dessus pourrait venir d'un titre mal lu.
    depot.pose_etat(jalons=[jalon(JALON, "## Verdict\n\nGO.\n", fermes=3)])
    assert depot.lib("milestone-verdict", JALON).returncode == 0


def test_un_verdict_en_prose_ne_fait_pas_une_section(depot: Depot) -> None:
    """Le mot ne suffit pas : le support est une SECTION, pas une occurrence du mot « verdict ».

    C'est ce qui sépare ce dispositif d'un lexique — et le dépôt a tranché ailleurs qu'on ne juge
    jamais une intention humaine par mots-clés (#746, #585, #749).
    """
    en_prose = "Le verdict de ce jalon sera rendu plus tard.\n"
    depot.pose_etat(jalons=[jalon(JALON, en_prose, fermes=3)])
    assert depot.lib("milestone-verdict", JALON).returncode == 3


def test_la_section_va_jusquau_prochain_titre_de_rang_egal(depot: Depot) -> None:
    """Une `### Réserves` sous `## Verdict` lui appartient ; une `## Autre chose` la ferme.

    C'est la lecture markdown ordinaire, et c'est elle qui autorise la forme prescrite par le lot 4
    — un verdict qui porte ses réserves en sous-section. Une lecture qui s'arrêterait au premier
    titre venu rendrait un verdict amputé de la seule partie qu'on suit ensuite.
    """
    description = (
        "## Verdict\n\nGO avec réserves.\n\n"
        "### Réserves\n\n- R1 → #999\n\n"
        "## Autre chose\n\nHors sujet.\n"
    )
    depot.pose_etat(jalons=[jalon(JALON, description, fermes=3)])
    rendu = depot.lib("milestone-verdict", JALON).stdout
    assert "### Réserves" in rendu and "#999" in rendu
    assert "Hors sujet" not in rendu, "une section de rang égal ferme la précédente"


def test_le_marqueur_rail_survit_a_l_ecriture(solde: Depot) -> None:
    """LA RÉGRESSION QUE CE SUPPORT REND POSSIBLE — et la raison du critère.

    La description d'un jalon porte déjà le marqueur `rail:` de #617 **en tête**, et l'écraser
    reclasserait le jalon dans le mauvais rail sans que rien ne le signale : `/ticket-create` y
    rangerait des tickets de produit, `current-milestone outillage` ne le trouverait plus. D'où
    l'écriture qui n'ajoute qu'en QUEUE.

    Le rail est relu par le verbe qui le rend en colonne, jamais par une inspection à la main : une
    seconde formulation de « quel rail ? » finirait par ne plus rendre le même verdict (#617).
    """
    code, avant = convoques(solde)
    assert code == 0 and avant[0][1] == "outillage", "décor : le jalon EST sur le rail outillage"

    solde.lib("milestone-criteres", JALON, pose(solde, "c.md", "- C1 — un critère."))

    description = description_du_jalon(solde, JALON)
    assert description.startswith("rail: outillage"), description[:80]
    assert "Ce jalon porte l'outillage" in description, "le cadrage en prose survit aussi"
    _, apres = convoques(solde)
    assert apres[0][1] == "outillage", "le rail lu par le verbe n'a pas bougé"


def test_une_description_vide_ne_prend_pas_le_numero_pour_un_corps(depot: Depot) -> None:
    """Le départage du lot 1, sans lequel une PREMIÈRE pose écraserait « 17 ».

    `gl_milestone_numero_desc` rend « numéro\\ndescription » en un aller ; une description vide ne
    laisse aucun saut de ligne dans la réponse. Sans le `case` qui départage, le numéro du jalon
    serait pris pour son corps — et la section nouvelle s'ajouterait derrière lui.
    """
    depot.pose_etat(jalons=[jalon(JALON, "", fermes=3, numero=17)])
    assert depot.lib("milestone-criteres", JALON, pose(depot, "c.md", "- C1")).returncode == 0

    description = description_du_jalon(depot, JALON)
    assert description.startswith("## Critères de sortie"), description[:60]
    assert "17" not in description, "le numéro du jalon n'est pas son cadrage"


def test_la_pose_est_idempotente(solde: Depot) -> None:
    """Reposer le même contenu ne réécrit rien, et le DIT — deux fois posé, une fois écrit.

    Le compte d'écritures est ce qui le prouve : « déjà à jour » pourrait être un message posé
    après coup sur un PATCH qui a bien eu lieu.
    """
    fichier = pose(solde, "c.md", "- C1 — un critère.")
    premiere = solde.lib("milestone-criteres", JALON, fichier)
    seconde = solde.lib("milestone-criteres", JALON, fichier)

    assert premiere.returncode == 0 and seconde.returncode == 0
    assert "section créée" in premiere.stdout
    assert "rien à écrire" in seconde.stdout
    assert len(ecritures(solde)) == 1, "la seconde pose ne doit produire aucun PATCH"


def test_une_section_existante_est_remplacee_sur_place(solde: Depot) -> None:
    """Mise à jour et création sont deux gestes, et le verbe les NOMME séparément."""
    solde.lib("milestone-criteres", JALON, pose(solde, "c1.md", "- C1 — première version."))
    seconde = solde.lib(
        "milestone-criteres", JALON, pose(solde, "c2.md", "- C1 — seconde version.")
    )

    assert "section mise à jour" in seconde.stdout
    description = description_du_jalon(solde, JALON)
    assert description.count("## Critères de sortie") == 1, "remplacée, jamais empilée"
    assert "première version" not in description
    assert description.startswith("rail: outillage")


def test_les_refus_gratuits_tombent_avant_la_forge(solde: Depot) -> None:
    """Un fichier absent ou blanc se voit sans rien demander à personne — donc rien n'est écrit.

    Règle de `gl_reste_claude` : refuser LÀ garantit qu'un refus ne laisse rien derrière lui. Un
    corps vide est refusé et non « posé vide », le verbe ne sachant pas RETIRER une section (#757)
    — inventer un retrait offrirait un moyen d'effacer un verdict rendu.
    """
    absent = solde.lib("milestone-verdict", JALON, ".maestro/session/nulle-part.md")
    blanc = solde.lib("milestone-verdict", JALON, pose(solde, "vide.md", "   \n\n\t\n"))

    assert absent.returncode == 2 and blanc.returncode == 2
    assert not ecritures(solde), "un refus gratuit ne laisse rien derrière lui"
    assert not solde.appels(), "il ne coûte même pas une lecture"


def test_un_jalon_inconnu_est_nomme_et_rien_n_est_ecrit(solde: Depot) -> None:
    """Le titre est la clé de l'API : une faute de frappe doit se voir, pas créer un jalon."""
    acheve = solde.lib("milestone-criteres", "Phase inexistante", pose(solde, "c.md", "- C1"))
    assert acheve.returncode == 1
    assert "Phase inexistante" in acheve.stderr
    assert not ecritures(solde)


def test_une_description_a_retours_windows_se_lit_comme_les_autres(depot: Depot) -> None:
    """La forge rend ce qu'on lui a donné : un `\\r` collé au titre ferait échouer la comparaison.

    Et il échouerait EN SILENCE — le verbe rendrait « aucune section » sur un jalon qui en porte
    une, c'est-à-dire le pire des deux échecs pour une convocation qui doit dire « il en manque ».
    """
    windows = "rail: outillage\r\n\r\n## Verdict\r\n\r\nGO.\r\n"
    depot.pose_etat(jalons=[jalon(JALON, windows, fermes=3)])
    acheve = depot.lib("milestone-verdict", JALON)
    assert acheve.returncode == 0
    assert acheve.stdout.rstrip("\n") == "GO.", "aucun CR ne colle au corps rendu"


def test_la_casse_est_tolereee_mais_jamais_l_orthographe(depot: Depot) -> None:
    """La casse ASCII se replie, un titre mal orthographié ne se rattrape pas.

    ⚠ ET LA CASSE D'UNE LETTRE ACCENTUÉE N'EST PAS PORTABLE — c'est une mesure de ce ticket, et
    elle CORRIGE le banc manuel du lot 1, qui donnait « `tolower()` ne replie pas les accents, ni
    sous mawk ni sous gawk » pour une limite assumée. Elle n'en est pas une : c'est un **écart entre
    implémentations**, mesuré le 2026-08-29 sous la même locale `C.UTF-8` — `gawk 5.2.1` replie
    `CRITÈRES` en `critères`, `mawk 1.3.4` le laisse tel quel, parce qu'il replie octet par octet.
    Le job `pytest` de la PR #798 l'a rendu visible en échouant : il a rendu `0` là où le conteneur
    du filet local attendait `3`.

    Ce test n'assert donc RIEN sur ce cas-là : pincer l'une ou l'autre branche le rendrait rouge sur
    la moitié des machines, et pincer la « bonne » n'existe pas. Il assert ce qui est **vrai des
    deux côtés**, et c'est le contrat qui compte — la forme documentée (les constantes de `lib.sh`)
    est reconnue partout, sa variante en casse ASCII aussi, et l'accent perdu ne l'est nulle part.

    La portée du désaccord est étroite et vaut d'être sue : seul « Critères de sortie » porte un
    accent, donc seul lui diverge — « ## VERDICT » est reconnu partout. Le remède n'est pas dans ce
    lot (tests + doc) : écrire les deux sections dans leur forme documentée suffit, et `docs/10
    §3.4` le dit.
    """
    depot.pose_etat(jalons=[jalon(JALON, "## critères de sortie\n\n- C1\n", fermes=3)])
    assert depot.lib("milestone-criteres", JALON).returncode == 0, "casse ASCII : repliée"

    # Sans accent, la MAJUSCULE se replie des deux côtés — c'est ce qui isole la variable : ce qui
    # diverge plus haut est l'accent, jamais la casse.
    depot.pose_etat(jalons=[jalon(JALON, "## VERDICT\n\nGO.\n", fermes=3)])
    assert depot.lib("milestone-verdict", JALON).returncode == 0, "majuscule ASCII : repliée"

    # Et un titre dont l'accent MANQUE n'est pas le même titre : le repli de casse n'est pas un
    # rattrapage d'orthographe, sous aucun awk.
    depot.pose_etat(jalons=[jalon(JALON, "## Criteres de sortie\n\n- C1\n", fermes=3)])
    assert depot.lib("milestone-criteres", JALON).returncode == 3, "accent absent : autre titre"


# =================================================================================================
# Lot #758 — La convocation : qui attend son bouclage
# =================================================================================================
# Un jalon soldé n'était signalé qu'à un seul endroit — `doctor.sh` — et cet endroit nommait la
# MAUVAISE action : « à fermer », c'est-à-dire la décision finale, proposée en sautant le geste qui
# doit la précéder.


def test_un_jalon_solde_sans_verdict_est_nomme(solde: Depot) -> None:
    """Le cas nominal, avec ses cinq colonnes — le titre en tête, c'est la clé qu'on repasse."""
    code, lignes = convoques(solde)
    assert code == 0
    assert lignes == [[JALON, "outillage", "non", "12", "12"]]


def test_le_verdict_consigne_eteint_la_convocation(solde: Depot) -> None:
    """LE PIVOT DU DISPOSITIF (#758 critère 1, #760 critère 1) — et son motif se prouve d'abord.

    Sans la première moitié, « plus convoqué » serait vrai d'un jalon qui ne l'a jamais été : le
    décor pourrait être mal posé, le verbe muet pour une autre raison, et le test rendrait un ✓ sur
    une question jamais posée. C'est la seule moitié OBSERVABLE de tout le bouclage — le reste est
    du texte —, et c'est elle que l'étape 8 de `/milestone-verdict` fait constater.
    """
    avant, _ = convoques(solde)
    assert avant == 0, "décor : ce jalon EST convoqué tant que rien n'est conclu"

    solde.lib("milestone-verdict", JALON, pose(solde, "v.md", "**GO** — 2026-08-29."))

    apres = solde.lib("milestones-a-boucler")
    assert apres.returncode == 3
    assert apres.stdout == "", "muet : pas même la ligne d'en-tête"


def test_la_convocation_est_muette_quand_il_n_y_a_rien(depot: Depot) -> None:
    """Règle de `gc --auto`, et elle compte ici : ce verbe est relayé par deux sorties lues en
    entier. Signaler l'abstention nominale apprend à ne plus lire les signalements.
    """
    depot.pose_etat(jalons=[jalon("Phase 9 — vide", "", fermes=0, ouverts=0)])
    silencieux = depot.lib("milestones-a-boucler")
    assert silencieux.returncode == 3
    assert silencieux.stdout == ""

    # Contre-exemple : avec un candidat, l'en-tête EST là — donc le vide ci-dessus est un verdict
    # et non une sortie qu'on n'a pas su lire.
    depot.pose_etat(jalons=[jalon(JALON, CADRAGE, fermes=12)])
    assert depot.lib("milestones-a-boucler").stdout.startswith("# titre\t")


@pytest.mark.parametrize(
    ("cas", "decor"),
    [
        ("en cours", jalon(JALON, CADRAGE, ouverts=3, fermes=9)),
        ("vide", jalon(JALON, CADRAGE, ouverts=0, fermes=0)),
        ("déjà fermé", jalon(JALON, CADRAGE, fermes=12, etat="closed")),
    ],
)
def test_seul_un_jalon_actif_et_entierement_solde_est_convoque(
    depot: Depot, cas: str, decor: dict
) -> None:
    """Les trois voisins qui ne sont pas le cas — et chacun pour sa raison.

    Un jalon **en cours** n'a pas fini de se construire ; un jalon **vide** n'est pas découpé (#619
    — les confondre avec le soldé est ce qui faisait tomber tout ticket produit dans un contenant
    gardé vide exprès) ; un jalon **fermé** n'attend plus rien, son moment est passé.
    """
    depot.pose_etat(jalons=[decor])
    assert depot.lib("milestones-a-boucler").returncode == 3, cas


def test_la_colonne_criteres_est_informative_et_ne_filtre_rien(solde: Depot) -> None:
    """Un jalon SANS critères est convoqué comme les autres — l'absence est un manque à combler AU
    bouclage (#757), jamais une raison de ne pas convoquer.
    """
    _, sans = convoques(solde)
    assert sans[0][2] == "non"

    solde.lib("milestone-criteres", JALON, pose(solde, "c.md", "- C1 — un critère."))
    code, avec = convoques(solde)
    assert code == 0, "des critères ne font pas taire la convocation — seul un verdict le fait"
    assert avec[0][2] == "oui"


def test_la_convocation_n_ecrit_rien(solde: Depot) -> None:
    """« Lecture seule intégrale » est sa promesse, et elle se prouve sur le journal d'appels."""
    assert convoques(solde)[0] == 0
    assert not ecritures(solde)


def test_le_detecteur_d_ecritures_voit_bien_un_patch_de_jalon(solde: Depot) -> None:
    """Le contre-exemple du test ci-dessus, et il n'est pas de pure forme (#761).

    `ecritures()` ne connaissait que `gh api -X <MÉTHODE>` ; or les deux verbes qui écrivent la
    description d'un jalon emploient `--method`, la forme longue. Un `assert not ecritures(...)`
    posé sur eux aurait donc été vrai **quoi qu'ils écrivent** — un ✓ sur une question jamais
    posée, sur la garde même qui doit l'empêcher. Ce test tient la liste ouverte.
    """
    solde.lib("milestone-criteres", JALON, pose(solde, "c.md", "- C1"))
    vues = ecritures(solde)
    assert len(vues) == 1, vues
    assert "--method\tPATCH" in vues[0] and "/milestones/" in vues[0]


def test_le_double_rejoue_la_selection_du_verbe() -> None:
    """La moitié que le dépôt jetable ne peut pas garder : le texte du programme jq.

    Le `gh` factice n'exécute pas jq — `jalons_rest` refait sa sélection en Python. Toute la suite
    ci-dessus serait donc verte sur une sélection PÉRIMÉE si le verbe changeait la sienne. Ce
    contrôle ancre les quatre décisions que le double reproduit, à l'endroit où elles vivent.
    """
    texte = LIB.read_text(encoding="utf-8")
    for fragment, raison in (
        ("milestones?state=open&per_page=100", "la convocation narrow à la source : actif"),
        ("select((.closed_issues // 0) > 0 and (.open_issues // 0) == 0)", "entièrement soldé"),
        ("@base64", "la description tient sur une ligne de TSV"),
        ("milestones?state=all&per_page=100", "la lecture d'un jalon, fermé compris"),
        ("env.GL_MS_TITRE", "la sélection par titre, faite côté API"),
        ("--raw-field description=", "pas `--field`, qui ferait de la coercition de type"),
    ):
        assert fragment in texte, (
            f"{raison} : « {fragment} » a disparu de lib.sh — le double le rejoue encore"
        )


def test_doctor_convoque_au_bouclage_et_nomme_la_commande(solde: Depot) -> None:
    """Il disait « à fermer » ; il dit « à boucler, puis à fermer », et imprime le geste.

    Nommer la fermeture en sautant le bilan est la deuxième raison pour laquelle le bouclage a
    disparu du dépôt : personne n'était CONVOQUÉ. Et comme le jalon n'a pas de critères, il dit
    aussi ce qui doit venir d'abord — un bilan sans critère n'est qu'une opinion.
    """
    bilan = solde.doctor().stdout
    assert "à boucler, puis à fermer" in bilan
    assert f'/milestone-bilan "{JALON}"' in bilan
    assert "aucun critère de sortie consigné" in bilan
    assert "milestone-criteres" in bilan
    assert not ecritures(solde), "doctor.sh est en lecture seule, même sur ce qu'il sait réparer"


def test_doctor_se_tait_sur_un_jalon_deja_boucle(solde: Depot) -> None:
    """Le contre-test compte autant : un bilan qui alerte à vide n'est plus lu.

    Re-signaler « à fermer » derrière un bouclage rendu ferait de la convocation un bruit
    permanent — c'est la conséquence assumée du lot 2, et elle se garde ici.
    """
    solde.lib("milestone-criteres", JALON, pose(solde, "c.md", "- C1"))
    solde.lib("milestone-verdict", JALON, pose(solde, "v.md", "**GO** — 2026-08-29."))

    bilan = solde.doctor().stdout
    assert "aucun jalon actif à boucler" in bilan
    assert "à boucler, puis à fermer" not in bilan
    assert "/milestone-bilan" not in bilan, "aucun geste n'est plus proposé sur ce jalon"


def test_doctor_dit_les_jalons_illisibles_au_lieu_de_les_declarer_conformes(depot: Depot) -> None:
    """Un jalon qu'on n'a pas su lire n'est pas un jalon bouclé — la panne de #341, encore.

    C'est le seul fichier du dépôt dont le métier est de détecter les dérives : un ✓ sur une
    question jamais posée y coûte plus cher qu'ailleurs.
    """
    bilan = depot.doctor().stdout + depot.doctor().stderr   # aucune règle de jalon posée
    assert "jalons illisibles" in bilan
    assert "aucun jalon actif à boucler" not in bilan


def test_backlog_relaie_la_convocation_sans_jamais_la_jouer() -> None:
    """Le prompt lit le verbe, dit ce qu'il faut faire, et ne boucle rien lui-même."""
    texte = prose(BACKLOG)
    assert "milestones-a-boucler" in texte
    assert "/milestone-bilan" in texte
    assert "ne le mentionne pas du tout" in texte, "le muet du verbe doit rester muet à l'écran"
    assert "ne fermes aucun jalon" in texte


# =================================================================================================
# Lot #759 — `/milestone-bilan` : exercer sur pièces, proposer un verdict
# =================================================================================================
# Ce lot est un PROMPT : ce qui se garde ici sont ses interdits, c'est-à-dire ce qu'un prompt
# réécrit à la légère lui ferait perdre. Chaque motif est prouvé sur un échantillon fautif avant de
# conclure de son absence.


#: Une invocation du verbe sous sa FORME À DEUX ARGUMENTS — celle qui écrit dans le jalon.
ECRIT_LE_JALON = re.compile(r"milestone-(?:criteres|verdict)\s+(?:\"[^\"]*\"|\S+)\s+\S")


def test_le_motif_d_ecriture_reconnait_une_invocation_fautive() -> None:
    """Le motif est prouvé sur un échantillon fautif AVANT de balayer.

    Sans cette moitié, les deux contrôles qui suivent rendraient un ✓ sur une question jamais
    posée : un motif qui ne matche rien est vert sur n'importe quel dépôt.
    """
    assert ECRIT_LE_JALON.search(
        'bash scripts/gitlab/lib.sh milestone-criteres "<titre-exact>" <fichier>'
    )
    assert ECRIT_LE_JALON.search("bash scripts/gitlab/lib.sh milestone-verdict Phase-3 corps.md")
    assert not ECRIT_LE_JALON.search('bash scripts/gitlab/lib.sh milestone-verdict "<titre-exact>"')


def test_le_bilan_ne_joue_jamais_les_verbes_sous_leur_forme_qui_ecrit() -> None:
    """`/milestone-bilan` LIT les deux sections et n'en écrit aucune — les deux pour la même raison.

    Écrire les critères à l'heure du bilan, c'est rédiger l'examen après l'épreuve : il rendra
    toujours un `GO`. Écrire le verdict, c'est consigner une conclusion que personne n'a arbitrée.
    L'amendement du lot 4 documente que le second interdit **a une fin** — quand une personne a
    tranché — sans le lever ici : c'est `/milestone-verdict` qui joue le verbe.
    """
    fautifs = [bloc for bloc in blocs_de_code(BILAN) if ECRIT_LE_JALON.search(bloc)]
    assert not fautifs, "/milestone-bilan joue un verbe qui écrit :\n" + "\n".join(fautifs)

    texte = prose(BILAN)
    assert "Tu les **nommes** à qui doit les jouer ; tu ne les joues pas." in texte
    assert "N'écris pas les critères toi-même" in texte
    # Contre-exemple : les blocs de code sont bien lus, et il y en a.
    assert any("milestone-criteres" in bloc for bloc in blocs_de_code(BILAN))


def test_le_bilan_s_arrete_sans_criteres_au_lieu_de_conclure() -> None:
    """Un bouclage sans critère de sortie n'est pas un verdict, c'est une opinion.

    Et c'est la PREMIÈRE cause de la disparition du geste : les quatre démos de bilan avaient leur
    question à trancher, les Phases 4 à 8 n'en ont aucune.
    """
    texte = prose(BILAN)
    assert "aucun critère) → ARRÊTE-TOI" in texte
    assert "aucun verdict n'est possible" in texte
    assert "milestone-criteres" in texte, "l'arrêt nomme le geste qui débloque"


def test_un_critere_non_couvert_est_nomme_et_jamais_coche() -> None:
    """L'erreur que cette commande ne doit jamais commettre — et celle qui a fermé 14 jalons.

    « Lire le code n'est pas l'exercer » : un diff dit ce qui a été écrit, jamais que ça marche.
    Un critère non couvert est une réserve à lui seul, donc il ne laisse jamais un `GO` nu.
    """
    texte = prose(BILAN)
    assert "jamais coché" in texte
    assert "non couvert" in texte and "dis pourquoi" in texte
    assert "Lire le code n'est pas l'exercer" in texte
    assert "non couvert est une réserve à lui seul" in texte


def test_le_bilan_propose_le_verdict_et_ne_le_rend_pas() -> None:
    """Ce qui est automatique est la détection du manque, jamais le verdict (#562, #612, #714).

    L'abstention y est distincte du `NO-GO`, et ce n'est pas une nuance : un livrable qu'on n'a pas
    su éprouver n'est pas un livrable jugé mauvais — les confondre ferait rejeter une phase pour
    une panne de stack.
    """
    texte = prose(BILAN)
    assert "Le verdict est PROPOSÉ, jamais rendu" in texte
    assert "Aucun verdict (abstention)" in texte
    assert "Une abstention n'est pas un `NO-GO`" in texte


def test_le_bilan_n_a_aucun_navigateur_en_propre() -> None:
    """Quatre exécutants existent, ce sont les seuls — il les APPELLE, il n'en réécrit aucun.

    Déclarer `mcp__chrome-maestro` l'inviterait à tenir une seconde version de ce que
    `captures.sh`, `verify` et `banc-mise-en-page` font déjà. La décision est écrite dans le
    prompt ; ce test la garde d'un renversement par distraction.
    """
    entete = BILAN.read_text(encoding="utf-8").split("---")[1]
    assert "mcp__chrome-maestro" not in entete
    assert "allowed-tools:" in entete and "Skill" in entete, "l'en-tête est bien lu, et non vide"


def test_le_bilan_ne_commite_pas_son_rapport() -> None:
    """Le rapport est un DOCUMENT — c'est ce qui l'a fait survivre quatre fois (docs/11 à 23) —,
    mais le commiter reste une décision humaine, comme pour `/milestone-presentation`.
    """
    texte = prose(BILAN)
    assert "docs/bilans/" in texte
    assert "n'est pas commité" in texte


# =================================================================================================
# Lot #760 — `/milestone-verdict` : enregistrer ce qu'une personne a arbitré
# =================================================================================================


def test_la_section_prescrite_est_rejouable_par_le_verbe(solde: Depot) -> None:
    """La forme de l'étape 7, posée pour de vrai — et relue au caractère près.

    Elle doit se suffire à elle-même : le rapport n'est pas commité, donc son chemin est un renvoi
    local que personne d'autre n'ouvrira. Ce qui survit est ce qui est écrit LÀ — verdict, date,
    état du jalon, compte de critères, chaque réserve **avec son sort**.
    """
    section = (
        "**GO avec réserves** — 2026-08-29.\n\n"
        "Jalon soldé 12/12. Critères : 4 tenus · 0 en défaut · 1 non couvert.\n\n"
        "### Réserves\n\n"
        "- R1 (C3) — la stack n'a pas démarré → #999\n"
        "- R2 (C4) — capture incomplète, acceptée telle quelle\n\n"
        "Rapport : `docs/bilans/outillage-de-la-forge.md` (non commité)."
    )
    assert solde.lib("milestone-verdict", JALON, pose(solde, "v.md", section)).returncode == 0

    relu = solde.lib("milestone-verdict", JALON)
    assert relu.stdout.rstrip("\n") == section, "la section se relit telle qu'elle a été posée"
    assert description_du_jalon(solde, JALON).startswith("rail: outillage")
    assert convoques(solde)[0] == 3, "et elle éteint la convocation"


def test_aucune_reserve_ne_se_cree_sans_un_oui() -> None:
    """Une réserve **acceptée telle quelle** est une décision, pas un oubli.

    Un ticket ouvert d'office sur une réserve assumée est un ticket que personne ne fermera. Un
    « oui » global vaut pour toutes — mais il se DEMANDE, il ne se suppose pas.
    """
    texte = prose(VERDICT)
    assert "Un « oui » par réserve" in texte
    assert "il se demande" in texte
    assert "Rien ne s'enregistre sans un « oui » explicite" in texte


def test_les_reserves_passent_par_ticket_create_et_jamais_par_gh() -> None:
    """La création a une source unique : corps de template, labels, milestone, état, item de projet.

    Un `gh issue create` recopié ici perdrait les cinq d'un coup — et `tests/test_cycle_de_vie.py`
    interdit déjà les écritures de forge sous `.claude/commands/**`.
    """
    texte = prose(VERDICT)
    assert "`/ticket-create`" in texte
    assert "jamais par un `gh issue create` recopié ici" in texte


def test_un_ticket_de_reserve_ne_va_jamais_au_jalon_boucle() -> None:
    """L'y inscrire le DÉ-SOLDERAIT : `open_issues > 0`, donc plus fermable.

    Le bouclage se retournerait alors contre lui-même — et la convocation le renommerait au tour
    suivant. La règle tient par le DÉFAUT de `/ticket-create` (il pose le courant tout seul) et non
    par une consigne : ce qu'on lui indique est le RAIL, jamais un `--milestone`.

    ⚠ Le contrôle porte sur les blocs de code et non sur le texte : le prompt écrit `--milestone`
    en toutes lettres pour dire de **ne pas** le passer, et un `grep` à plat rougirait sur la
    phrase même qui pose l'interdit.
    """
    fautifs = [ligne for ligne in VERDICT.read_text(encoding="utf-8").splitlines()
               if FORCE_UN_MILESTONE.search(ligne)]
    assert not fautifs, "/milestone-verdict force un milestone :\n" + "\n".join(fautifs)

    texte = prose(VERDICT)
    assert "Le milestone est le COURANT, jamais le jalon qu'on vient de boucler" in texte
    assert "Ne lui passe donc pas `--milestone`" in texte
    assert "current-milestone" in texte, "le geste sûr est nommé : ne rien forcer"


def test_le_renvoi_va_dans_les_deux_sens_avec_un_support_chacun() -> None:
    """Un fait, un support — et jamais une recherche plein texte pour retrouver un ticket.

    Le renvoi **ticket → verdict** est une phrase pour la PERSONNE qui tombe sur le ticket sans son
    contexte ; le renvoi **verdict → tickets** est la section `## Verdict`, seul chemin machine, et
    c'est lui qui rend le rebouclage idempotent.
    """
    texte = prose(VERDICT)
    assert "Réserve du bouclage de « <titre exact du jalon> » — critère C<n>, verdict du" in texte
    assert "ne fais jamais dépendre l'idempotence d'une recherche plein texte" in texte
    assert "N'improvise aucune recherche dans le texte des tickets" in texte


def test_une_reserve_sans_son_sort_est_proscrite() -> None:
    """Muette, elle est indiscernable d'un oubli — c'est précisément ce qu'on cherche à ne plus
    perdre (leçon de #608, où un correctif rendu dans une PR mergée devenait invisible).
    """
    texte = prose(VERDICT)
    assert "Jamais une réserve sans" in texte and "son sort" in texte
    assert "acceptée telle quelle" in texte


def test_l_enregistrement_fait_constater_que_la_convocation_a_cesse() -> None:
    """La moitié OBSERVABLE du geste — tout le reste est du texte.

    Si le jalon y figure encore, la consignation n'a pas pris : le prompt exige de le dire
    franchement plutôt que de conclure au bouclage.
    """
    texte = prose(VERDICT)
    assert "milestones-a-boucler" in texte
    assert "ne conclus pas au bouclage" in texte


def test_le_verdict_ne_se_fabrique_pas_en_l_absence_de_rapport() -> None:
    """Symétrie exacte du refus de `/milestone-bilan` d'écrire les critères qui lui manquent."""
    texte = prose(VERDICT)
    assert "Aucun verdict ne se fabrique ici" in texte
    assert "/milestone-bilan" in texte, "le geste qui débloque est nommé"
    assert "rapport : absent" in texte, "l'exception étroite est écrite, et son marquage aussi"


# =================================================================================================
# Transversal — ce qu'aucune des quatre pièces ne doit faire
# =================================================================================================
# Deux interdits pèsent sur tout le chantier, et ils se gardent sur le dépôt entier plutôt que sur
# les seuls fichiers du chantier : le prochain à les enfreindre sera un fichier qui n'existe pas
# encore.


# ⚠ CE QUI EST CHERCHÉ EST UN USAGE, JAMAIS UNE MENTION — et ces trois motifs seraient inutilisables
# autrement. Les deux prompts du chantier NOMMENT en toutes lettres ce qu'ils s'interdisent : « ne
# lance aucune commande d'écriture (`gh issue edit`, `set-workflow`…) », « ne lui passe donc pas
# `--milestone` », « jamais par un `gh issue create` recopié ici ». Compter ces lignes-là pour des
# fautes rendrait les contrôles impossibles à satisfaire autrement qu'en EFFAÇANT les interdits
# qu'ils gardent — l'inverse exact du but. C'est la leçon du `_PRESCRIT` de
# `tests/test_merge_automatique.py`, et celle du motif « usage jamais mention » de
# `tests/test_cycle_de_vie.py`.
#
# Un usage se reconnaît à sa FORME D'APPEL : la commande SUIVIE DE SON ARGUMENT (un numéro, un
# drapeau, une variable, un gabarit `<…>`, une chaîne), ou seule en fin de ligne comme dans un bloc
# de code. Les mentions du dépôt, elles, sont toutes suivies d'un guillemet fermant, d'une virgule
# ou d'un point.

#: L'argument qui fait d'une commande citée une commande APPELÉE.
_ARGUMENT = r"""(?:\s+(?:--?\w|\d|\$|<[a-z]|"|'|repos/)|\s*`?\s*$)"""

#: Fermer un jalon, sous les formes que l'API rend possibles.
FERME_UN_JALON = re.compile(r"""(?:-f|-F|--field|--raw-field)\s+["']?state=["']?closed""")

#: Une écriture de forge appelée directement — ce qu'un PROMPT ne fait jamais (#562, #617, #714).
GH_EN_ECRITURE = re.compile(
    r"gh\s+api\s+(?:-X|--method)\s+(?:POST|PATCH|PUT|DELETE)"
    r"|gh\s+(?:issue\s+(?:edit|create|close)|pr\s+(?:merge|edit))" + _ARGUMENT
)

#: Poser un cycle de vie de ticket — hors de `/ticket-create`, qui en est la source unique.
POSE_UN_CYCLE_DE_VIE = re.compile(r"(?:set-workflow|log-time|project-add)" + _ARGUMENT)

#: Imposer un milestone à un ticket — ce qui enverrait une réserve au jalon qu'on vient de boucler.
FORCE_UN_MILESTONE = re.compile(r"--milestone" + _ARGUMENT)


def test_les_motifs_transversaux_departagent_l_usage_de_la_mention() -> None:
    """Prouvés sur un échantillon fautif ET sur un échantillon licite, avant de balayer.

    La première moitié est la règle du dépôt — un `grep` qui ne trouve rien rend un ✓ sur une
    question jamais posée. La seconde lui est propre : sans elle, ces motifs rougiraient sur les
    phrases mêmes qui posent les interdits, et le seul moyen de les faire taire serait de les
    effacer.
    """
    for fautif in (
        'gh api --method PATCH "repos/o/r/milestones/17" -f state=closed',
        "gh api -X PATCH repos/o/r/milestones/17 --field state='closed'",
    ):
        assert FERME_UN_JALON.search(fautif), fautif
    for licite in ("repos/o/r/milestones?state=all&per_page=100", "compte les jalons state=closed"):
        assert not FERME_UN_JALON.search(licite), licite

    for fautif in (
        "gh issue edit 761 --add-label lot::arbitre",
        "   gh api --method PATCH repos/o/r/milestones/17",
        "- `gh issue create --title <titre>`",
        "```\ngh pr edit\n```".splitlines()[1],
    ):
        assert GH_EN_ECRITURE.search(fautif), fautif
    for licite in (
        "Ne lance aucune commande d'écriture côté forge (`gh issue edit`, `gh pr create`…)",
        "jamais par un `gh issue create` recopié ici : le corps de template en dépend",
        "gh issue view 761 --json body",
    ):
        assert not GH_EN_ECRITURE.search(licite), licite

    for fautif in ('bash scripts/gitlab/lib.sh set-workflow 761 "En revue"', "log-time 761 2h"):
        assert POSE_UN_CYCLE_DE_VIE.search(fautif), fautif
    for licite in (
        "aucune commande d'écriture (`gh issue edit`, `set-workflow`, `log-time`…)",
        "ni Status, ni PR, ni merge",
    ):
        assert not POSE_UN_CYCLE_DE_VIE.search(licite), licite

    # Et le départage se rejoue sur un fichier RÉEL du dépôt, qui porte les deux à la fois :
    # `/ticket-finish` APPELLE le verbe (« set-workflow <iid> "En revue" ») et le MENTIONNE
    # ailleurs (« ne repasse pas `set-workflow` »). Un échantillon inventé prouve que le motif
    # sait ; celui-ci prouve qu'il sait sur ce qu'il balaie.
    reel = (COMMANDES / "ticket-finish.md").read_text(encoding="utf-8").splitlines()
    vus = [ligne for ligne in reel if POSE_UN_CYCLE_DE_VIE.search(ligne)]
    assert any("En revue" in ligne for ligne in vus), "l'appel réel n'est pas vu"
    assert not any("ne repasse pas" in ligne for ligne in vus), "une mention réelle est comptée"

    for fautif in ('/ticket-create --milestone "<titre du jalon>"', "--milestone $JALON"):
        assert FORCE_UN_MILESTONE.search(fautif), fautif
    for licite in ("**Ne lui passe donc pas `--milestone`** — le seul cas où l'on nommerait",):
        assert not FORCE_UN_MILESTONE.search(licite), licite


def fichiers_du_depot(*dossiers: Path):
    """Les fichiers de `scripts/` et `.claude/`, bytecode exclu PAR RÉPERTOIRE.

    Python écrit son bytecode sous `<nom>.pyc.<pid>` avant de le renommer, et un worker xdist tué
    en laisse derrière lui : filtrer sur le suffixe seul laisserait passer un `.2594` (#345).
    """
    for dossier in dossiers:
        for fichier in dossier.rglob("*"):
            if "__pycache__" in fichier.parts or not fichier.is_file():
                continue
            if fichier.suffix in (".pyc", ".png", ".jpg", ".gz", ".ico"):
                continue
            yield fichier


def test_aucune_commande_ne_ferme_un_milestone() -> None:
    """`docs/10 §3.4`, inchangé depuis toujours : la fermeture est une DÉCISION HUMAINE.

    Le bouclage produit ce qui manquait avant elle ; il ne la prend pas. Le rétablir en donnant à
    une machine le geste final serait défaire, du même mouvement, le jalon go/no-go de la roadmap.
    """
    fautifs = []
    for fichier in fichiers_du_depot(RACINE / "scripts", RACINE / ".claude"):
        texte = fichier.read_text(encoding="utf-8", errors="replace")
        for numero, ligne in enumerate(texte.splitlines(), start=1):
            if "milestone" in ligne.lower() and FERME_UN_JALON.search(ligne):
                fautifs.append(f"{fichier.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "un jalon se ferme à la main, jamais ici :\n" + "\n".join(fautifs)


def test_aucun_prompt_du_chantier_n_appelle_gh_en_ecriture() -> None:
    """Les écritures passent par un VERBE, et cette garde est plus large que son motif à dessein.

    C'est celle de `tests/test_cycle_de_vie.py` (`--add-label`), appliquée aux deux prompts neufs :
    le support du bouclage peut bouger — un prompt qui appellerait `gh` directement serait à
    retrouver, un verbe non.
    """
    fautifs = []
    for prompt in (BILAN, VERDICT):
        for numero, ligne in enumerate(prompt.read_text(encoding="utf-8").splitlines(), start=1):
            if GH_EN_ECRITURE.search(ligne):
                fautifs.append(f"{prompt.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "écriture de forge dans un prompt :\n" + "\n".join(fautifs)


def test_aucune_commande_du_chantier_ne_pose_de_cycle_de_vie() -> None:
    """Le bouclage juge un JALON, jamais un ticket — sauf par `/ticket-create`, sa source unique.

    Poser un état ici mettrait deux mains sur le cycle de vie d'un ticket, et le premier symptôme
    de deux supports est un ticket qui porte deux états (#365).
    """
    fautifs = []
    for prompt in (BILAN, VERDICT):
        for numero, ligne in enumerate(prompt.read_text(encoding="utf-8").splitlines(), start=1):
            if POSE_UN_CYCLE_DE_VIE.search(ligne):
                fautifs.append(f"{prompt.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "pose de cycle de vie hors de /ticket-create :\n" + "\n".join(fautifs)

    # Contre-exemple : les deux prompts DISENT bien qu'ils ne touchent pas au cycle de vie — sans
    # quoi l'absence ci-dessus dirait seulement qu'ils ne parlent pas du sujet.
    assert "set-workflow" in prose(BILAN), "l'interdit est nommé, pas seulement respecté"
    assert "ne touche à aucun cycle de vie" in prose(VERDICT)


# =================================================================================================
# La documentation — un mécanisme que la doc ne nomme pas redevient une règle lue
# =================================================================================================
# C'est le défaut d'origine, et il n'était pas d'implémentation : le bouclage a été fait QUATRE
# fois à la main, puis perdu sans que personne ne le décide, faute qu'aucune ligne ne l'appelle.


def section_de(chemin: Path, titre: str, suivante: str) -> str:
    """Une section d'un document, de son titre au titre suivant — puis blancs repliés.

    ⚠ LE DÉCOUPAGE PASSE AVANT LE REPLI, et l'ordre porte tout : replier d'abord ferait disparaître
    les `\\n` qui bornent la section, `split` rendrait tout le reste du fichier, et l'assertion
    serait vraie d'une phrase écrite trente sections plus loin — c'est-à-dire verte pour la mauvaise
    raison, ce que ce module reproche partout ailleurs.
    """
    texte = chemin.read_text(encoding="utf-8")
    assert titre in texte, f"{chemin.name} : « {titre} » introuvable — le test ne garde plus rien"
    reste = texte.split(titre, 1)[1]
    if suivante:
        assert suivante in reste, f"{chemin.name} : « {suivante} » introuvable — borne perdue"
        reste = reste.split(suivante, 1)[0]
    return re.sub(r"\s+", " ", reste)


def test_docs_10_place_le_bouclage_avant_la_fermeture() -> None:
    """§3.4 cessait de renvoyer à un tableau ; il nomme désormais un geste — et garde l'ordre.

    L'ordre EST le contenu de la décision : boucler puis fermer. Nommer la fermeture seule est ce
    que `doctor.sh` faisait, et c'est la raison pour laquelle personne n'a jamais été convoqué.
    """
    section = section_de(DOC_WORKFLOW, "\n### 3.4 ", "\n### 3.5 ")
    assert "/milestone-bilan" in section and "/milestone-verdict" in section
    assert "milestones-a-boucler" in section
    assert "décision humaine" in section, "la fermeture n'a pas changé de main"
    assert "aucune commande ne ferme un milestone" in section
    assert "milestone-criteres" in section and "docs/bilans/" in section


def test_docs_06_dit_ou_se_lit_le_verdict_d_un_jalon() -> None:
    """Le tableau des jalons s'arrêtait à la Phase 3 — quatre verdicts rendus, puis plus rien.

    Il doit dire où le verdict d'aujourd'hui se lit : la section `## Verdict` du jalon, que la
    convocation interroge, et le rapport local qui l'a produit.
    """
    section = section_de(DOC_ROADMAP, "\n## Jalons de décision", "")
    assert "/milestone-bilan" in section
    assert "## Verdict" in section
    assert "docs/bilans/" in section
    assert "Critères de sortie" in section
    assert "Fin Phase 7" in section, "le jalon dont le verdict n'a jamais été rendu reste nommé"


def test_claude_md_nomme_les_deux_commandes_de_supervision() -> None:
    """`CLAUDE.md` est ce que l'agent lit en premier : une commande qui n'y est pas n'existe pas.

    C'est la panne exacte de `/design-veille`, livrée puis appelée par rien pendant quinze jours
    (#714) — et celle du bouclage, quatre fois fait puis jamais redemandé.
    """
    texte = prose(CLAUDE_MD)
    assert "/milestone-bilan" in texte
    assert "/milestone-verdict" in texte
    assert "milestones-a-boucler" in texte
