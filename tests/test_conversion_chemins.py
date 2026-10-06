"""Un texte qui commence par « / » arrive intact à `gh` (#1439).

Sous Git Bash, le runtime MSYS réécrit en chemin Windows tout argument qui RESSEMBLE à un chemin
POSIX avant de le passer à un exécutable natif comme `gh.exe` — les guillemets n'y peuvent rien, la
conversion a lieu au lancement du processus, après le shell. Le 2026-10-06, `/ticket-create` a ainsi
créé #1415 sous le titre « C:/Program Files/Git/idee présente un plan… », et le titre d'un ticket
devient le slug de sa branche (#181 avait déjà donné `chore/181-c-program-files-git-ticket-start…`).

Le remède a DEUX moitiés, et ces tests gardent les deux :

* le TEXTE voyage intact — `lib.sh` appelle `gh` par une enveloppe qui neutralise la conversion ;
* les FICHIERS voyagent toujours — `-F body=@/tmp/…` reposait sur cette même conversion pour que
  `gh.exe` trouve le fichier. Neutraliser sans les convertir soi-même aurait cassé `issue-note`,
  `set-description` et le suivi : c'est le correctif naïf (`export MSYS_NO_PATHCONV=1` en tête de
  `lib.sh`), et il est écarté pour cette raison.

**Où le défaut est réel, et où il est doublé.** Sous Git Bash, le double de `gh` reçoit ce que le
vrai recevrait : le runtime convertit à l'`exec` de son lanceur (`ecrit_lanceur`). Sous Linux — le
conteneur du filet, la CI —, rien n'est converti : un test du titre y serait vert avant comme après
le correctif. `emule_msys` y pose donc un double du runtime, et le premier test prouve qu'il
réécrit bien ce que Git Bash réécrit. La moitié FICHIERS, elle, n'a de prise que sous Git Bash :
sous Linux un chemin `/tmp/…` est déjà le bon, et le test passe sans rien prouver — c'est dit.
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest
from harnais_forge import BASH, GIT, RACINE, Depot, emule_msys, monte_depot

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

#: Le titre de #1415 tel qu'il devait naître : il commence par une commande du dépôt.
TITRE = "/idee présente un plan à valider avant toute écriture dans la forge"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    depot = monte_depot(tmp_path)
    emule_msys(depot)
    return depot


def champ_envoye(depot: Depot, cle: str) -> str:
    """La valeur du champ `<cle>=…` du DERNIER appel reçu par le double, lue en octets UTF-8."""
    lignes = depot.journal.read_bytes().decode("utf-8").splitlines()
    assert lignes, "le double n'a reçu aucun appel"
    for champ in lignes[-1].split("\t"):
        nom, sep, valeur = champ.partition("=")
        if sep and nom == cle:
            return valeur
    raise AssertionError(f"aucun champ {cle}= dans {lignes[-1]!r}")


def corps_de_ticket(depot: Depot, contenu: str = "Corps du ticket.\n") -> str:
    """Un corps en chemin RELATIF, la forme que les commandes emploient (`.maestro/session/`)."""
    chemin = depot.racine / ".maestro" / "session" / "corps.md"
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return ".maestro/session/corps.md"


# =================================================================================================
# Le texte
# =================================================================================================


def test_le_runtime_reecrit_un_titre_qui_commence_par_une_commande(depot: Depot) -> None:
    """L'échantillon fautif d'abord : sans ce test, les suivants pourraient être verts à vide.

    `gh` appelé EN DIRECT, hors de `lib.sh` : sous Git Bash c'est le vrai runtime qui réécrit,
    ailleurs son double. La racine d'installation de Git n'est pas épinglée — elle dépend du poste.
    """
    depot.bash_inline(f'gh api -X POST repos/o/r/issues -f "title={TITRE}"\n')
    recu = champ_envoye(depot, "title")
    assert recu != TITRE and recu.endswith(TITRE), recu

    depot.bash_inline(f'MSYS_NO_PATHCONV=1 gh api -X POST repos/o/r/issues -f "title={TITRE}"\n')
    assert champ_envoye(depot, "title") == TITRE, "la neutralisation documentée l'éteint"


def test_un_titre_qui_commence_par_une_commande_arrive_intact_a_gh(depot: Depot) -> None:
    """Le défaut de #1415, par le verbe que `/ticket-create` emploie désormais.

    Vérifié en OCTETS : le journal du double est relu comme tel, puis décodé — jamais comparé à
    l'œil, une substitution de chemin pouvant laisser un affichage plausible.
    """
    acheve = depot.lib("issue-create", TITRE, corps_de_ticket(depot), "type::bug,prio::moyenne")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.strip() == "1", "le verbe rend l'iid créé"

    assert champ_envoye(depot, "title") == TITRE
    assert f"title={TITRE}".encode() in depot.journal.read_bytes()


# =================================================================================================
# Les fichiers
# =================================================================================================


def test_un_corps_designe_par_un_chemin_posix_arrive_toujours(depot: Depot) -> None:
    """La moitié que le correctif naïf casse : `-F body=@/tmp/…` doit encore atteindre son fichier.

    Le fichier est créé comme `lib.sh` crée les siens — `mktemp` sous `${TMPDIR:-/tmp}`, donc un
    chemin POSIX que `gh.exe` ne sait pas lire tel quel. Sous Git Bash, seule la conversion faite
    par l'enveloppe le rend lisible ; sous Linux il l'est déjà (cf. l'en-tête du module).
    """
    depot.bash_inline(
        'f="$(mktemp "${TMPDIR:-/tmp}/maestro-essai.XXXXXX")"\n'
        "printf 'Corps venu par un chemin POSIX.\\n' > \"$f\"\n"
        f'bash scripts/gitlab/lib.sh issue-create "{TITRE}" "$f"\n'
        'rm -f "$f"\n'
    )
    assert champ_envoye(depot, "body") == "Corps venu par un chemin POSIX.\\n"
    assert champ_envoye(depot, "title") == TITRE


def test_gh_absent_reste_nomme_malgre_l_enveloppe(depot: Depot) -> None:
    """L'enveloppe est une FONCTION nommée `gh` : `command -v gh` la trouverait toujours.

    Le pré-requis cherche donc le binaire seul (`type -P`). Sans ça, un poste sans `gh` passerait
    `require` et échouerait au premier appel, sous un message qui ne nomme plus la cause.
    """
    assert BASH is not None and GIT is not None
    chemin = os.pathsep.join(dict.fromkeys([str(Path(BASH).parent), str(Path(GIT).parent)]))
    if shutil.which("gh", path=chemin):
        pytest.skip("un gh est installé à côté de bash ou de git : son absence ne se fabrique pas")

    acheve = depot.lib("require", reglages={"PATH": chemin})
    assert acheve.returncode == 1
    assert "gh n'est pas installé" in acheve.stderr


# =================================================================================================
# Les commandes : aucun appel direct à `gh` avec un texte libre en tête d'argument
# =================================================================================================
# Une commande qui appelle `gh` elle-même échappe à l'enveloppe de `lib.sh`. Le motif cherche un
# USAGE — une ligne de code qui commence par `gh`, continuations `\` jointes — dont un argument
# entre guillemets commence par un gabarit (`"<titre>"`, `"<concept> …"`) : un texte libre, qui peut
# commencer par « / ». Une mention dans la prose (« jamais par un `gh issue create` recopié ») n'en
# est pas une.

GH_TEXTE_EN_TETE = re.compile(r'^\s*gh\s.*[\s=]"<', re.M)


def appels_fautifs(texte: str) -> list[str]:
    """Les appels fautifs, chacun rendu sur sa ligne entière (continuations jointes)."""
    joint = re.sub(r"\\\n\s*", " ", texte)
    return [
        joint[m.start() :].split("\n", 1)[0].strip() for m in GH_TEXTE_EN_TETE.finditer(joint)
    ]


def test_le_motif_sait_dire_oui_et_non() -> None:
    """L'échantillon fautif avant le balayage — un motif creux balaierait sans rien voir."""
    for fautif in (
        '   gh issue create \\\n     --title "<titre>" \\\n     --body-file <fichier>',
        'gh issue list --state all --search "<concept> in:title,body" --limit 20',
        'gh api -X POST repos/o/r/issues -f title="<titre>"',
    ):
        assert appels_fautifs(fautif), fautif
    for licite in (
        'gh issue list --state all --search "in:title,body <concept>" --limit 20',
        "gh issue view <iid> --comments",
        'bash scripts/gitlab/lib.sh issue-create "<titre>" <fichier>',
        "et jamais par un `gh issue create` recopié",
    ):
        assert not appels_fautifs(licite), licite


def test_aucune_commande_n_appelle_gh_avec_un_texte_libre_en_tete() -> None:
    fautifs = [
        f"{prompt.relative_to(RACINE).as_posix()} : {appel}"
        for prompt in sorted((RACINE / ".claude").rglob("*.md"))
        for appel in appels_fautifs(prompt.read_text(encoding="utf-8"))
    ]
    assert not fautifs, "texte libre en tête d'un argument de gh :\n" + "\n".join(fautifs)


def test_ticket_create_cree_par_le_verbe() -> None:
    texte = (RACINE / ".claude/commands/ticket-create.md").read_text(encoding="utf-8")
    assert "bash scripts/gitlab/lib.sh issue-create " in texte
