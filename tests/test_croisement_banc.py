"""Le banc dû au ticket se dérive de la carte (#1464) : `maestro.scenarios.croisement`.

Le préfixe `GL_BANC_CHEMINS` faisait de tout ticket du produit un ticket « sur le
chemin » ; la carte (#1463) dit ce que chaque scénario exécute. Ici :

① ce que le diff **touche** — la fonction qui contient une ligne changée, nommée
  comme la sonde la nomme (`co_qualname`), le niveau module, le fichier entier ;
② la **carte composée** de l'historique — la dernière empreinte de chaque scénario,
  une empreinte empêchée à défaut, une stack non mesurée sautée ;
③ le **croisement** — la couverture la moins chère, le moins cher en tête, le plus
  cher (S12) seulement quand il est seul à couvrir, le démarrage pour ce qu'aucun
  scénario n'exécute, et rien quand rien ne croise ;
④ la **ligne de commande** — ses codes, que `lib.sh criteres` lit.

Les cartes et l'historique sont écrits par les **vrais** écrivains
(`CarteDuPassage.to_dict`, `historique.consigner`) : si leur forme change, c'est ici
que ça se voit. Les diffs sont ceux de vrais dépôts Git jetables.
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from maestro.scenarios import croisement, historique
from maestro.scenarios.carte import CarteDuPassage, Empreinte, Processus
from maestro.scenarios.croisement import (
    CODE_CROISE,
    CODE_RIEN,
    CODE_SANS_CARTE,
    CODE_USAGE,
    NIVEAU_MODULE,
    CarteComposee,
    Touche,
    composer,
    croiser,
    portee_de_la_ligne,
    portees_de,
    touches_du_diff,
)
from maestro.scenarios.modele import VERDICT_ROUGE, VERDICT_VERT, Rapport, Resultat

_IDENTITE = ("-c", "user.name=banc", "-c", "user.email=banc@example.invalid")

#: Les coûts médians d'un historique : S4 le moins cher, S12 le plus cher (le poste au 2026-10-13).
COUTS = {"S2": 0.35, "S4": 0.20, "S9": 2.27, "S12": 2.66}
CATALOGUE = ("S1", "S2", "S3", "S4", "S9", "S12")

#: La stack d'un passage mesuré : un processus qui n'est pas le banc.
API = Processus(pid=10, commande="-m maestro.controltower.cli --port 8063 --etat-banc")


def _git(racine: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *_IDENTITE, "-C", str(racine), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.strip()


def _ecrire(racine: Path, relatif: str, texte: str) -> None:
    chemin = racine / relatif
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(texte, encoding="utf-8", newline="\n")


MOTEUR = '''\
"""Le moteur."""

GABARIT = "Tu es un agent."


def ouvrir(chemin):
    return chemin


class Executeur:
    TOURS = 3

    @staticmethod
    def jouer(tache):
        def tour(n):
            return n + 1

        return tour(tache)

    def finir(self):
        return None
'''


@pytest.fixture
def depot(tmp_path: Path) -> tuple[Path, str]:
    """Un dépôt jetable, sa base commitée : un moteur Python, un playbook, un fichier à retirer."""
    racine = tmp_path / "depot"
    racine.mkdir()
    _git(racine, "init", "-q", "-b", "main")
    _git(racine, "config", "core.autocrlf", "false")
    _ecrire(racine, "maestro/moteur.py", MOTEUR)
    _ecrire(racine, "maestro/vieux.py", "def ancien():\n    return 1\n")
    _ecrire(racine, "core/playbooks/dev.md", "# Développeur\n")
    _git(racine, "add", "-A")
    _git(racine, "commit", "-q", "-m", "base")
    return racine, _git(racine, "rev-parse", "HEAD")


def _remplace(racine: Path, relatif: str, avant: str, apres: str) -> None:
    chemin = racine / relatif
    texte = chemin.read_text(encoding="utf-8")
    assert avant in texte
    chemin.write_text(texte.replace(avant, apres), encoding="utf-8", newline="\n")


# =================================================================================================
# ① Ce que le diff touche
# =================================================================================================


def test_les_portees_se_nomment_comme_la_sonde_les_nomme() -> None:
    """`co_qualname` : `C.m` dans une classe, `f.<locals>.g` dans une fonction — la sonde relève
    ces noms-là, et un nom fabriqué autrement ne croiserait jamais rien."""
    portees = portees_de(MOTEUR)
    noms = {p.nom for p in portees}
    assert noms == {
        "ouvrir",
        "Executeur",
        "Executeur.jouer",
        "Executeur.jouer.<locals>.tour",
        "Executeur.finir",
    }
    lignes = MOTEUR.splitlines()

    def ligne(fragment: str) -> int:
        return next(i for i, texte in enumerate(lignes, 1) if fragment in texte)

    assert portee_de_la_ligne(portees, ligne("return n + 1")) == "Executeur.jouer.<locals>.tour"
    assert portee_de_la_ligne(portees, ligne("return tour(tache)")) == "Executeur.jouer"
    assert portee_de_la_ligne(portees, ligne("@staticmethod")) == "Executeur.jouer"
    assert portee_de_la_ligne(portees, ligne("TOURS = 3")) == "Executeur"
    assert portee_de_la_ligne(portees, ligne("GABARIT =")) == NIVEAU_MODULE


def test_le_diff_se_range_en_fonctions_niveau_module_et_fichiers(depot: tuple[Path, str]) -> None:
    """Une ligne changée dans une fonction la touche ; une constante en tête de fichier touche le
    niveau module ; un fichier de données, un Python retiré ou nouveau se touchent en entier —
    arbre de travail et fichiers non suivis compris."""
    racine, base = depot
    _remplace(racine, "maestro/moteur.py", "return n + 1", "return n + 2")
    _remplace(racine, "maestro/moteur.py", "Tu es un agent.", "Tu es un agent sobre.")
    _remplace(racine, "core/playbooks/dev.md", "# Développeur", "# Développeur outillé")
    (racine / "maestro/vieux.py").unlink()
    _ecrire(racine, "maestro/neuf.py", "def neuve():\n    return 2\n")

    touches = touches_du_diff(racine, base)

    assert set(touches) == {
        Touche("maestro/moteur.py", "Executeur.jouer.<locals>.tour"),
        Touche("maestro/moteur.py", NIVEAU_MODULE),
        Touche("core/playbooks/dev.md"),
        Touche("maestro/vieux.py"),
        Touche("maestro/neuf.py"),
    }


def test_une_fonction_retiree_se_lit_dans_la_version_d_avant(depot: tuple[Path, str]) -> None:
    """Une ligne retirée n'existe plus après : c'est la version de la base qui dit où elle était."""
    racine, base = depot
    _remplace(racine, "maestro/moteur.py", "    def finir(self):\n        return None\n", "")
    assert touches_du_diff(racine, base) == (Touche("maestro/moteur.py", "Executeur.finir"),)


def test_un_diff_vide_ne_touche_rien(depot: tuple[Path, str]) -> None:
    racine, base = depot
    assert touches_du_diff(racine, base) == ()


def test_une_base_inconnue_est_un_diff_illisible(depot: tuple[Path, str]) -> None:
    racine, _base = depot
    with pytest.raises(croisement.DiffIllisible):
        touches_du_diff(racine, "0" * 40)


# =================================================================================================
# ② La carte composée
# =================================================================================================


def _empreinte(fonctions: Mapping[str, Sequence[str]], fichiers: Sequence[str] = ()) -> Empreinte:
    return Empreinte(
        fonctions={f: tuple(n) for f, n in fonctions.items()},
        fichiers=tuple(fichiers),
        processus=(API,),
    )


def _passage(
    chemin: Path,
    horodatage: str,
    scenarios: Mapping[str, Empreinte],
    *,
    demarrage: Empreinte | None = None,
    empeches: Sequence[str] = (),
    couts: Mapping[str, float] = COUTS,
) -> None:
    """Un passage consigné par le VRAI historique, sa carte écrite par la VRAIE carte."""
    carte = CarteDuPassage(
        passage=horodatage,
        copie="/copie",
        demarrage=demarrage or _empreinte({}),
        scenarios=dict(scenarios),
    )
    fichier = chemin.parent / f"couverture-{horodatage}.json"
    fichier.parent.mkdir(parents=True, exist_ok=True)
    fichier.write_text(json.dumps(carte.to_dict()), encoding="utf-8")
    rapport = Rapport(
        horodatage=horodatage,
        resultats=tuple(
            Resultat(
                identifiant=ident,
                titre=ident,
                verdict=VERDICT_ROUGE if ident in empeches else VERDICT_VERT,
                motif="m",
                duree_s=60.0 * (1 + list(COUTS).index(ident)) if ident in COUTS else 30.0,
                cout_usd=couts.get(ident),
                empechement=ident in empeches,
            )
            for ident in scenarios
        ),
    )
    historique.consigner(rapport, historique.Copie(racine="/copie"), chemin=chemin, carte=fichier)


def _composee(chemin: Path) -> CarteComposee:
    carte = composer(historique.lire(chemin).lignes, chemin)
    assert carte is not None
    return carte


def test_la_carte_composee_prend_la_derniere_empreinte_de_chaque_scenario(tmp_path: Path) -> None:
    """Un passage n'écrit la carte que de ce qu'il a joué : S2 vient du plus récent, S4 du seul
    passage qui l'a mesuré."""
    chemin = tmp_path / "historique.jsonl"
    _passage(
        chemin,
        "20261011-100000",
        {"S2": _empreinte({"a.py": ["vieux"]}), "S4": _empreinte({"a.py": ["f"]})},
    )
    _passage(chemin, "20261012-100000", {"S2": _empreinte({"a.py": ["neuf"]})})

    carte = _composee(chemin)

    assert carte.scenarios["S2"].fonctions == {"a.py": ("neuf",)}
    assert carte.passages == {"S2": "20261012-100000", "S4": "20261011-100000"}


def test_une_empreinte_empechee_ne_sert_qu_a_defaut(tmp_path: Path) -> None:
    """Un scénario coupé en route (limite d'usage) n'a exécuté qu'une partie de son chemin."""
    chemin = tmp_path / "historique.jsonl"
    _passage(chemin, "20261011-100000", {"S2": _empreinte({"a.py": ["complet"]})})
    _passage(chemin, "20261012-100000", {"S2": _empreinte({"a.py": ["coupe"]})}, empeches=["S2"])
    _passage(chemin, "20261012-110000", {"S9": _empreinte({"b.py": ["seul"]})}, empeches=["S9"])

    carte = _composee(chemin)

    assert carte.scenarios["S2"].fonctions == {"a.py": ("complet",)}
    assert carte.scenarios["S9"].fonctions == {"b.py": ("seul",)}, "à défaut d'autre, elle sert"


def test_sans_carte_mesuree_il_n_y_a_pas_de_carte(tmp_path: Path) -> None:
    """Un historique sans carte, ou dont la stack n'a pas chargé la sonde, ne dit rien : « rien ne
    croise » serait un mensonge — l'appelant retombe sur le préfixe."""
    chemin = tmp_path / "historique.jsonl"
    rapport = Rapport(horodatage="20261011-100000", resultats=())
    historique.consigner(rapport, historique.Copie(racine="/copie"), chemin=chemin)
    assert composer(historique.lire(chemin).lignes, chemin) is None

    non_mesuree = Empreinte(fonctions={"a.py": ("f",)})  # aucun processus de la stack
    _passage(chemin, "20261012-100000", {"S2": non_mesuree}, demarrage=Empreinte())
    assert composer(historique.lire(chemin).lignes, chemin) is None


# =================================================================================================
# ③ Le croisement
# =================================================================================================


def _croise(chemin: Path, *touches: Touche) -> croisement.Croisement:
    lecture = historique.lire(chemin)
    carte = composer(lecture.lignes, chemin)
    assert carte is not None
    return croiser(touches, carte, CATALOGUE, historique.series(lecture.lignes))


@pytest.fixture
def carte_du_poste(tmp_path: Path) -> Path:
    """S2, S4 et S12 mesurés : `commun` passe par les trois, `s2` et `s12` sont propres."""
    chemin = tmp_path / "historique.jsonl"
    _passage(
        chemin,
        "20261011-181132",
        {
            "S2": _empreinte(
                {"m.py": ["commun", "s2", "s2_et_s12", "Classe.methode"]}, ["core/dev.md"]
            ),
            "S4": _empreinte({"m.py": ["commun"]}, ["core/dev.md"]),
        },
        demarrage=_empreinte({"m.py": ["<module>", "au_demarrage"]}),
    )
    _passage(
        chemin,
        "20261013-095059",
        {"S12": _empreinte({"m.py": ["commun", "s12", "s2_et_s12", "Classe.methode"]})},
    )
    return chemin


def test_le_moins_cher_qui_couvre_est_propose_seul(carte_du_poste: Path) -> None:
    """Une fonction que S2, S4 et S12 exécutent : S4, le moins cher, et les deux autres comptés."""
    resultat = _croise(carte_du_poste, Touche("m.py", "commun"))
    assert resultat.proposes == ("S4",)
    assert resultat.autres == ("S2", "S12")


def test_s12_n_est_propose_que_s_il_est_seul_a_couvrir(carte_du_poste: Path) -> None:
    """Le plus cher n'est proposé que pour ce que lui seul exécute — et alors avec le moins cher
    de ce qui reste, le moins cher en tête."""
    assert _croise(carte_du_poste, Touche("m.py", "s2_et_s12")).proposes == ("S2",)
    assert _croise(carte_du_poste, Touche("m.py", "s12")).proposes == ("S12",)
    resultat = _croise(carte_du_poste, Touche("m.py", "s2"), Touche("m.py", "s12"))
    assert resultat.proposes == ("S2", "S12")
    assert resultat.couvre == {
        "S2": (Touche("m.py", "s2"),),
        "S12": (Touche("m.py", "s12"),),
    }


def test_un_scenario_que_les_autres_couvrent_deja_est_retire(carte_du_poste: Path) -> None:
    """Pour `commun` le moins cher est S4, pour `s2` c'est S2 — mais S2 passe aussi par `commun` :
    jouer S2 seul couvre tout, et coûte moins que S4 et S2."""
    resultat = _croise(carte_du_poste, Touche("m.py", "commun"), Touche("m.py", "s2"))
    assert resultat.proposes == ("S2",)
    assert resultat.couvre["S2"] == (Touche("m.py", "commun"), Touche("m.py", "s2"))


def test_une_classe_couvre_ses_methodes_et_un_module_son_fichier(carte_du_poste: Path) -> None:
    """Un attribut de classe changé croise les scénarios qui en appellent les méthodes ; une
    constante de module, ceux qui exécutent une fonction du fichier ; un fichier de données,
    ceux qui l'ont lu."""
    assert _croise(carte_du_poste, Touche("m.py", "Classe")).proposes == ("S2",)
    assert _croise(carte_du_poste, Touche("m.py", NIVEAU_MODULE)).proposes == ("S4",)
    assert _croise(carte_du_poste, Touche("core/dev.md")).proposes == ("S4",)


def test_ce_qu_aucun_scenario_n_execute_mais_le_demarrage_si_est_au_moins_cher(
    carte_du_poste: Path,
) -> None:
    """Ce qui ne tourne qu'au démarrage tourne avant chacun : le moins cher du catalogue le rejoue,
    et l'annonce le dit."""
    touche = Touche("m.py", "au_demarrage")
    resultat = _croise(carte_du_poste, touche)
    assert resultat.proposes == ("S4",)
    assert resultat.au_demarrage == {touche}
    assert "m.py:au_demarrage (au démarrage)" in "\n".join(croisement.annonce(resultat))


def test_rien_ne_croise_rien_n_est_propose(carte_du_poste: Path) -> None:
    """Le contre-exemple du préfixe : un fichier du produit qu'aucun scénario n'exécute."""
    resultat = _croise(
        carte_du_poste, Touche("m.py", "jamais_appelee"), Touche("autre.py", NIVEAU_MODULE)
    )
    assert resultat.proposes == ()


def test_un_cout_jamais_rapporte_passe_apres_les_couts_connus(tmp_path: Path) -> None:
    chemin = tmp_path / "historique.jsonl"
    commun = _empreinte({"m.py": ["f"]})
    _passage(chemin, "20261011-100000", {"S1": commun, "S12": commun}, couts={"S12": 2.66})
    assert _croise(chemin, Touche("m.py", "f")).proposes == ("S12",)


def test_l_annonce_dit_le_cout_l_origine_et_ce_qui_n_a_jamais_ete_mesure(
    carte_du_poste: Path,
) -> None:
    lignes = croisement.annonce(
        _croise(carte_du_poste, Touche("m.py", "s2"), Touche("m.py", "s12"))
    )
    assert lignes[0].startswith("la carte croise le diff — si le changement touche ce que le ")
    assert lignes[0].endswith(
        "le moins cher en tête : S2 (0,3500 $ · 1 min 00 s), S12 (2,6600 $ · 4 min 00 s)"
    )
    assert "S2 passe par m.py:s2" in lignes
    assert (
        "carte : S2, S4 du passage 20261011-181132 ; S12 du passage 20261013-095059 ; "
        "jamais mesurés : S1, S3, S9 — le banc par lot sur main les mesure"
    ) in lignes
    assert lignes[-1] == (
        "sinon : | Banc | non joué | mécanique couverte par <tests nommés> ; "
        "banc par lot sur main |"
    )


# =================================================================================================
# ④ La ligne de commande
# =================================================================================================


def _main(*argv: str, chemin: Path) -> tuple[int, str, str]:
    sortie, erreur = io.StringIO(), io.StringIO()
    code = croisement.main(
        list(argv), catalogue=CATALOGUE, chemin=chemin, sortie=sortie, erreur=erreur
    )
    return code, sortie.getvalue(), erreur.getvalue()


def test_la_commande_rend_ses_codes(depot: tuple[Path, str], tmp_path: Path) -> None:
    """`0` et l'annonce quand la carte croise, `--court` en une ligne ; `3` et rien quand elle ne
    croise pas ; `4` et le motif sans carte ; `2` sans base."""
    racine, base = depot
    chemin = tmp_path / "historique.jsonl"
    code, sortie, _ = _main("--base", base, "--depot", str(racine), chemin=chemin)
    assert (code, sortie.startswith("aucune carte lisible")) == (CODE_SANS_CARTE, True)

    _passage(chemin, "20261011-100000", {"S2": _empreinte({"maestro/moteur.py": ["ouvrir"]})})
    assert _main("--base", base, "--depot", str(racine), chemin=chemin)[:2] == (CODE_RIEN, "")

    _remplace(racine, "maestro/moteur.py", "return chemin", "return str(chemin)")
    code, sortie, _ = _main("--base", base, "--depot", str(racine), chemin=chemin)
    assert code == CODE_CROISE
    assert "S2 passe par maestro/moteur.py:ouvrir" in sortie
    code, sortie, _ = _main("--base", base, "--depot", str(racine), "--court", chemin=chemin)
    assert (code, sortie) == (CODE_CROISE, "la carte des scénarios croise le diff : S2 à jouer\n")

    assert _main("--depot", str(racine), chemin=chemin)[0] == CODE_USAGE
