"""Tests du câblage de `/design-veille` au cycle d'un ticket (#714, docs/30 §5.2).

`/design-veille` (#708) est le maillon 0 de la chaîne d'outillage visuel — les tokens, les
primitives, `sobriete.test.tsx` et `banc-mise-en-page` **gardent** ce qu'on a tenu, aucun ne dit ce
qu'on **vise**. Livrée, elle n'était appelée par rien : son déclencheur était une phrase de
`CLAUDE.md`, c'est-à-dire une **règle lue** et non un mécanisme — le défaut même que docs/30 §3.6
nomme pour écarter la checklist (« une checklist qu'aucune machine ne vérifie ne tient pas »).

Ce que ce module garde tient en quatre familles, et **aucune n'est le verdict de la veille** — le
verdict est un jugement humain, ce qui est outillé est la *détection du manque* (partage de #562 et
#612) :

* **le motif** — ce qui le déclenche, ce qui ne le déclenche pas, et ses bornes. Chaque contrôle
  qui conclut d'une ABSENCE porte son contre-exemple : sans lui, un motif mal branché rendrait un ✓
  sur une question jamais posée (méthode de `tests/contraste.test.ts`, #534, et du test d'audit de
  #578) ;
* **les trois verdicts** — `touche` / `arbitre` / `-`, et surtout le fait que les deux derniers ne
  se confondent pas : « déjà arbitré » n'est pas « pas de surface visible » ;
* **la dérive de la liste des routes** — elle est copiée des répertoires de `apps/web/app/`, donc
  elle dérive au premier écran ajouté si personne ne la vérifie ;
* **les décisions écrites** — le label posé quel que soit le verdict, le verbe (et non un `gh` dans
  un prompt), et l'accès web d'une session de run, tranché par #714 et gardé ici pour qu'il ne soit
  pas renversé par distraction.

**Ni réseau ni compte de forge** : harnais de [`harnais_forge.py`](harnais_forge.py), partagé avec
`test_collaboration.py`, `test_cycle_de_vie.py`, `test_decoupage_natif.py` et
`test_merge_automatique.py`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    RACINE,
    Depot,
    corps_ticket,
    ecritures,
    monte_depot,
    regle_owner,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

LIB = RACINE / "scripts" / "gitlab" / "lib.sh"
PROMPT_START = RACINE / ".claude" / "commands" / "ticket-start.md"
RUN_SH = RACINE / "scripts" / "orchestrate" / "run.sh"
BOOTSTRAP = RACINE / "scripts" / "gitlab" / "bootstrap.sh"
REGLAGES_RUN = RACINE / "scripts" / "orchestrate" / "settings.run.json"
REGLAGES_DEPOT = RACINE / ".claude" / "settings.json"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


def verdict(depot: Depot, vue: str) -> tuple[str, int, str, int]:
    """Joue `gl_touche_surface_de` sur une vue, comme le fera `gl_start_brief`.

    Rend « verdict, lignes, source, code ». Le fragment passe par `bash_inline` parce que le verbe
    lit STDIN : c'est le régime réel — la vue est celle que l'appelant a déjà en main, et aucune
    lecture de forge n'a lieu.
    """
    chemin = depot.racine / ".maestro" / "session" / "vue.txt"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(vue, encoding="utf-8", newline="\n")
    acheve = depot.bash_inline(
        ". scripts/gitlab/lib.sh\n"
        "gl_touche_surface_de <.maestro/session/vue.txt\n"
        'printf "CODE=%s\\n" "$?"\n'
    )
    lignes = [ligne for ligne in acheve.stdout.splitlines() if ligne.strip()]
    assert lignes, acheve.stdout + acheve.stderr
    code = int(lignes[-1].removeprefix("CODE="))
    champs = lignes[0].split("\t")
    return champs[0], int(champs[1]), champs[2], code


# =================================================================================================
# Le motif : ce qui le déclenche, ce qui ne le déclenche pas
# =================================================================================================


def test_le_label_de_conception_declenche(depot: Depot) -> None:
    """`agent::design` est le signal le plus précis de la mesure : 88 % pour 45 % de rappel."""
    vue = corps_ticket("La carte d'un run", "agent::design, prio::moyenne, type::feature", "Corps.")
    assert verdict(depot, vue) == ("touche", 1, "label", 0)


def test_une_route_nommee_declenche(depot: Depot) -> None:
    """Une route de `apps/web/app/` nommée dans le texte suffit — sans label de conception.

    C'est la moitié du motif qui attrape les tickets dont l'auteur n'a pas pensé « conception » :
    seule, elle vaut 82 % de précision pour 42 % de rappel.
    """
    vue = corps_ticket(
        "Les deux tables de /couts sous une bascule",
        "agent::dev, prio::moyenne, type::feature",
        "La répartition passe en colonne de propriétés.",
    )
    assert verdict(depot, vue) == ("touche", 1, "route", 0)


def test_sans_signal_le_verbe_se_tait(depot: Depot) -> None:
    """L'immense majorité des tickets ne touche aucune surface — et le verbe doit y être muet.

    C'est ce qui sépare ce motif du vocabulaire de la surface (« écran », « interface »,
    « Control Tower »), qui a le meilleur rappel de tous — 91 % — mais parle sur 249 des 562
    tickets du dépôt : un signalement qui se déclenche partout n'est plus lu.
    """
    vue = corps_ticket(
        "Le journal d'un run se lit sans jq",
        "agent::orchestrateur, prio::moyenne, type::infra",
        "`journal.sh audit` apparie les tool_use et leurs tool_result par identifiant.",
    )
    assert verdict(depot, vue) == ("-", 0, "-", 3)


def test_le_label_ne_se_lit_que_dans_len_tete(depot: Depot) -> None:
    """Un `agent::design` CITÉ EN PROSE n'est pas un ticket de conception.

    Ce dépôt cite ses propres labels à longueur de description — c'est même le cas de #714, dont la
    mesure compare `agent::design` aux autres variantes. Sans l'ancre `^labels:`, toute note
    technique parlant de conception déclencherait le signal.

    ⚠ LE CONTRE-EXEMPLE FAIT LA MOITIÉ DU TEST : on vérifie d'abord que le MÊME corps, une fois le
    label réellement posé, déclenche bien. Sans lui, un motif cassé (qui ne matcherait jamais rien)
    rendrait ce test vert en n'ayant rien gardé.
    """
    corps = "La mesure compare le label agent::design aux autres variantes du motif."
    muet = corps_ticket("Mesurer un motif", "agent::qa, prio::basse, type::infra", corps)
    assert verdict(depot, muet) == ("-", 0, "-", 3)

    # Contre-exemple : même corps, label posé pour de bon.
    parlant = corps_ticket("Mesurer un motif", "agent::design, prio::basse, type::infra", corps)
    assert verdict(depot, parlant)[0] == "touche", "le motif ne matche plus rien : test creux"


@pytest.mark.parametrize(
    ("texte", "attendu"),
    [
        ("La commande /run-audit dit où est passé le temps.", "-"),
        ("Lire GET /api/runs pour l'état du run.", "-"),
        ("Les routes vivent sous apps/web/app/runs/page.tsx.", "-"),
        ("L'écran /runs gagne une carte par run.", "touche"),
        ("Trois blocs sur /parametres, pas sept.", "touche"),
    ],
)
def test_les_bornes_de_la_route(depot: Depot, texte: str, attendu: str) -> None:
    """La borne est le contenu du motif, pas une précaution : sans elle il parle partout.

    `/run-audit` est une COMMANDE, `/api/runs` un chemin d'API, `app/runs/` un chemin de fichier —
    aucun des trois ne dit qu'on s'apprête à retoucher un écran. Les trois cas muets et les deux
    parlants sont dans le même test : c'est leur écart qui prouve que la borne borne quelque chose.
    """
    vue = corps_ticket("Un ticket", "agent::dev, prio::moyenne, type::feature", texte)
    assert verdict(depot, vue)[0] == attendu


# =================================================================================================
# Les trois verdicts — et le fait que deux d'entre eux ne se confondent pas
# =================================================================================================


def test_deja_arbitre_nest_pas_labsence_de_surface(depot: Depot) -> None:
    """Un ticket arbitré rend `arbitre`/4, jamais `-`/3 — et c'est un contrat, pas un détail.

    Les deux verdicts font TAIRE `/ticket-start` de la même façon, ce qui rend la confusion
    tentante et sans conséquence visible… jusqu'au jour où quelqu'un se sert du verbe pour autre
    chose que proposer. Rendre `-` sur un ticket arbitré ferait dire au verbe « pas de surface
    visible » là où la vérité est « surface visible, question déjà réglée ».
    """
    vue = corps_ticket(
        "La carte d'un run",
        "agent::design, prio::moyenne, type::feature, veille::arbitree",
        "Corps.",
    )
    v, _, source, code = verdict(depot, vue)
    assert (v, code) == ("arbitre", 4)
    assert source == "label", "la source du signal ne change pas parce qu'on l'a arbitré"

    # Contre-exemple : le même ticket sans le label d'arbitrage parle.
    sans = corps_ticket(
        "La carte d'un run", "agent::design, prio::moyenne, type::feature", "Corps."
    )
    assert verdict(depot, sans)[0] == "touche"


def test_le_verbe_de_lecture_ne_parle_pas_a_la_forge(depot: Depot) -> None:
    """`gl_touche_surface_de` rejoue le verdict sur une vue DÉJÀ LUE : zéro aller vers la forge.

    C'est toute la raison d'être de la moitié `_de` (comme `gl_touche_claude_de` et
    `gl_arbitrage_de`) : `gl_start_brief` a déjà la vue du ticket qu'il vient de lire, et #602 vient
    de faire descendre le pré-vol de `/ticket-start` de 30 allers à 5 — on ne les rend pas un
    par un.
    """
    vue = corps_ticket("La carte d'un run", "agent::design, prio::moyenne, type::feature", "Corps.")
    verdict(depot, vue)
    assert depot.appels() == [], "le verdict s'obtient sans une seule lecture de forge"


# =================================================================================================
# La couture chez l'appelant : `start-brief` propose, et se tait deux fois
# =================================================================================================

TICKET_SURFACE = corps_ticket(
    "La carte d'un run : une barre pleine qui dit vrai",
    "agent::design, prio::moyenne, type::feature",
    "## Critères d'acceptation\n\n- [ ] La carte porte la durée du run\n",
)
TICKET_ARBITRE = corps_ticket(
    "La carte d'un run : une barre pleine qui dit vrai",
    "agent::design, prio::moyenne, type::feature, veille::arbitree",
    "## Critères d'acceptation\n\n- [ ] La carte porte la durée du run\n",
)
TICKET_OUTILLAGE = corps_ticket(
    "Le journal d'un run se lit sans jq",
    "agent::orchestrateur, prio::moyenne, type::infra",
    "## Critères d'acceptation\n\n- [ ] Hors ligne, lecture seule\n",
)


def test_start_brief_propose_la_veille_sur_une_surface(depot: Depot) -> None:
    depot.pose_etat(issues={"709": TICKET_SURFACE}, graphql=[regle_owner("À faire", [])])
    acheve = depot.lib("start-brief", "709")
    assert acheve.returncode == 0, acheve.stderr
    assert "surface visible" in acheve.stdout
    assert "veille-arbitre 709" in acheve.stdout, "le geste d'enregistrement est nommé, avec l'iid"
    assert "jamais à lancer d'office" in acheve.stdout, (
        "ce qui est automatique est la détection du manque, jamais le verdict (#562, #612)"
    )
    # Le signal N'ÉCRIT RIEN : proposer n'est pas décider.
    assert ecritures(depot) == []


@pytest.mark.parametrize(
    ("iid", "ticket", "raison"),
    [
        ("714", TICKET_OUTILLAGE, "aucune surface visible : l'abstention nominale est muette"),
        ("709", TICKET_ARBITRE, "déjà arbitré : la question ne se repose pas à chaque démarrage"),
    ],
)
def test_start_brief_se_tait_deux_fois(depot: Depot, iid: str, ticket: str, raison: str) -> None:
    """Les deux silences n'ont pas la même cause, mais le brief doit être muet dans les deux cas.

    Le premier est la règle de `gc --auto` et de #517 : signaler ce qui ne se passe pas apprend à ne
    plus lire les signalements. Le second EST la promesse du ticket — sans lui, « une veille est
    inutile ici » serait indiscernable de « personne n'y a pensé », et la question reviendrait pour
    toujours (le défaut symétrique de celui qu'on corrige, exactement comme pour `lot::arbitre`).
    """
    depot.pose_etat(issues={iid: ticket}, graphql=[regle_owner("À faire", [])])
    acheve = depot.lib("start-brief", iid)
    assert acheve.returncode == 0, acheve.stderr
    assert "surface visible" not in acheve.stdout, raison
    # Contre-exemple : le brief est bien rendu — le silence est celui du signal, pas du verbe.
    assert f"#{iid}" in acheve.stdout


def test_le_signal_seteint(depot: Depot) -> None:
    """`MAESTRO_VEILLE_SIGNAL=0` — même sortie que tous les signalements greffés du dépôt."""
    depot.pose_etat(issues={"709": TICKET_SURFACE}, graphql=[regle_owner("À faire", [])])
    acheve = depot.lib("start-brief", "709", reglages={"MAESTRO_VEILLE_SIGNAL": "0"})
    assert "surface visible" not in acheve.stdout


# =================================================================================================
# L'enregistrement de l'arbitrage
# =================================================================================================


def test_veille_arbitre_pose_le_label(depot: Depot) -> None:
    depot.pose_etat(issues={"709": TICKET_SURFACE})
    acheve = depot.lib("veille-arbitre", "709")
    assert acheve.returncode == 0, acheve.stderr
    assert "veille::arbitree" in acheve.stdout
    assert any("veille::arbitree" in appel for appel in ecritures(depot)), (
        "le label est réellement posé côté forge"
    )


def test_veille_arbitre_est_idempotent(depot: Depot) -> None:
    """Rejouer sur un ticket déjà arbitré est un succès qui n'écrit rien."""
    depot.pose_etat(issues={"709": TICKET_ARBITRE})
    acheve = depot.lib("veille-arbitre", "709")
    assert acheve.returncode == 0, acheve.stderr
    assert "déjà enregistré" in acheve.stdout
    assert ecritures(depot) == []


def test_veille_arbitre_accepte_un_ticket_que_le_motif_ne_voit_pas(depot: Depot) -> None:
    """Il ENREGISTRE quand même, et le DIT — c'est la différence avec `gl_arbitre`, qui refuse.

    La raison est dans la mesure et non dans le goût : le motif rate 12 des 33 tickets qui ont
    touché la surface, tous des tickets décrivant une fonctionnalité par son COMPORTEMENT (#477
    « mettre un run en pause », #482 « le fil accepte fichiers et images »). Refuser d'enregistrer
    un arbitrage rendu sur l'un d'eux traiterait le trou connu du motif comme une erreur de
    l'utilisateur.
    """
    depot.pose_etat(issues={"477": TICKET_OUTILLAGE})
    acheve = depot.lib("veille-arbitre", "477")
    assert acheve.returncode == 0, acheve.stderr
    assert "enregistré" in acheve.stdout
    assert "ne voyait aucune surface visible" in acheve.stdout, "il le dit, il ne l'empêche pas"
    assert any("veille::arbitree" in appel for appel in ecritures(depot))


# =================================================================================================
# La dérive : la liste des routes suit les écrans, le label est provisionné
# =================================================================================================


def routes_du_motif() -> set[str]:
    texte = LIB.read_text(encoding="utf-8")
    trouve = re.search(r'^GL_SURFACE_ROUTES="\$\{GL_SURFACE_ROUTES:-([^}]+)\}"', texte, re.M)
    assert trouve, "GL_SURFACE_ROUTES introuvable dans lib.sh"
    return set(trouve.group(1).split("|"))


def test_la_liste_des_routes_suit_les_ecrans() -> None:
    """Une liste recopiée à la main dérive au premier écran ajouté — celle-ci est vérifiée.

    C'est la réponse que le dépôt fait partout ailleurs à « une règle que personne ne vérifie » —
    et la raison d'être de ce ticket, `/design-veille` ayant vécu quinze jours sans qu'aucun
    mécanisme ne l'appelle.

    ⚠ Ce test rougit dans les DEUX sens, et le second compte autant : une route disparue laisserait
    dans le motif un nom qui ne désigne plus rien, donc un faux positif pour toujours.
    """
    ecrans = {p.parent.name for p in (RACINE / "apps" / "web" / "app").glob("*/page.tsx")}
    assert ecrans, "aucune route trouvée : le test ne garde plus rien"
    manquantes = ecrans - routes_du_motif()
    disparues = routes_du_motif() - ecrans
    assert not manquantes, (
        f"écrans absents du motif de lib.sh : {sorted(manquantes)} — un ticket qui les nomme "
        "ne déclencherait aucune proposition de veille (GL_SURFACE_ROUTES)"
    )
    assert not disparues, (
        f"routes du motif qui n'existent plus sous apps/web/app/ : {sorted(disparues)}"
    )


def test_le_label_est_provisionne() -> None:
    """Un label qu'aucun bootstrap ne pose est un label absent du prochain clone."""
    texte = BOOTSTRAP.read_text(encoding="utf-8")
    assert 'create_label "veille::arbitree"' in texte
    # Contre-exemple : le motif trouve bien ses voisins, donc il cherche au bon endroit.
    assert 'create_label "lot::arbitre"' in texte


# =================================================================================================
# Les décisions écrites — pour qu'elles ne soient pas renversées par distraction
# =================================================================================================


def test_le_prompt_nomme_le_verbe_et_jamais_gh() -> None:
    """L'écriture passe par un VERBE, pour la raison exacte de `gl_arbitre` (#562).

    `tests/test_cycle_de_vie.py` interdit déjà `--add-label` sous `.claude/commands/**` — c'est par
    là qu'un prompt remettrait le cycle de vie sur l'issue, et la garde est plus large que son motif
    à dessein. Ce test-ci garde l'autre moitié : que le prompt nomme bien le verbe qui remplace le
    `gh` interdit, faute de quoi l'étape n'aurait aucun geste à proposer.
    """
    texte = PROMPT_START.read_text(encoding="utf-8")
    assert "veille-arbitre" in texte
    assert "/design-veille" in texte
    assert "--add-label" not in texte
    assert "surface visible" in texte, "le prompt reconnaît le bloc que start-brief imprime"


def test_le_prompt_ne_lance_jamais_la_veille_doffice() -> None:
    """Ce qui est automatique est la détection du manque, jamais le verdict (#562, #612).

    Lancer la veille d'office est le mauvais calcul évident — recherches web, captures et quota sur
    un ticket qui n'en a peut-être pas besoin — et c'est nommément hors du périmètre de #714.
    """
    texte = PROMPT_START.read_text(encoding="utf-8")
    assert "jamais" in texte and "d'office" in texte
    assert "un « oui » explicite" in texte or "oui » explicite" in texte


def test_lacces_web_reste_hors_des_allowlists_dun_run() -> None:
    """La veille est un GESTE INTERACTIF, et c'est la décision du critère 4 de #714.

    Trois raisons, dont une seule est technique. Une session de run n'a personne pour répondre au
    « oui » que la proposition attend, donc l'ouvrir reviendrait à la lancer d'office — ce que le
    ticket exclut nommément. Une veille rend des PARTIS PRIS, c'est-à-dire un jugement, du même
    bois que l'arbitrage de #562 et le rail de #617. Et `mcp__chrome-maestro` passant déjà l'union
    des deux allowlists, ouvrir la seule recherche donnerait une veille à moitié — captures sans
    références vérifiées —, or la règle du §3 de la commande est que ce qui n'est pas vérifié n'est
    pas cité.

    ⚠ IL REGARDE LES DEUX FICHIERS PARCE QUE L'`allow` D'UN RUN EST LEUR UNION (docs/10 §11.7), et
    c'est là que ce test gagne sa place : le changement plausible n'est pas « ouvrir le web aux
    runs » — personne ne le demandera — mais « ouvrir `WebSearch` dans `.claude/settings.json` pour
    éviter une confirmation à chaque `/design-veille` interactive ». Geste légitime, effet non
    voulu : il ouvre le run du même coup, sans que rien ne le dise. Une confirmation dans une
    session interactive n'est pas un défaut — il y a quelqu'un pour la donner, et c'est le régime
    dans lequel #708 vit déjà.

    ⚠ Il n'interdit pas d'y revenir : il demande qu'on le fasse EXPRÈS. Le renverser, c'est éditer
    ce test avec sa raison — pas découvrir six mois plus tard qu'un run fait des recherches web que
    personne n'a décidées.
    """
    for chemin in (REGLAGES_RUN, REGLAGES_DEPOT):
        allow = json.loads(chemin.read_text(encoding="utf-8"))["permissions"]["allow"]
        assert not [r for r in allow if r.startswith(("WebSearch", "WebFetch"))], (
            f"{chemin.name} ouvre l'accès web : c'est le renversement d'une décision de #714 "
            "(docs/30 §5.2) — à faire expressément, pas par distraction"
        )
        # Contre-exemple : la liste lue est bien la bonne, et le motif y trouve ce qu'il doit.
        assert any(r.startswith("Bash(") for r in allow), "allowlist vide ou mal lue : test creux"


def test_le_prompt_de_run_ecarte_la_veille() -> None:
    """Une session de run doit savoir ne pas tenter ce qui lui sera refusé.

    Sans cette ligne, elle lit la proposition de `/ticket-start`, tente une recherche, se la fait
    refuser, et dépense un tour à découvrir une règle qui est écrite. Pire : elle pourrait
    enregistrer l'arbitrage pour « faire propre », fermant la question sans que personne l'ait jugée
    — exactement le « marquer d'office » que #562 a écarté.
    """
    texte = RUN_SH.read_text(encoding="utf-8")
    assert "GESTE INTERACTIF" in texte
    assert "N'enregistre AUCUN arbitrage" in texte
    assert "WebSearch et WebFetch ne sont dans aucune" in texte
