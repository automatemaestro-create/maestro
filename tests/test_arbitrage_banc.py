"""Le banc d'arbitrage sur l'acte se rejoue, et son verdict garde quelque chose (#716).

`scripts/arbitrage/banc-arbitrage.py` joue le parcours entier — la politique
**versionnée** classe, le vrai hook `PreToolUse` suspend, la demande atteint
`GET /api/validations`, la décision est rendue par l'API, le journal garde
l'étape `:refus-outil` sous le statut `arbitrage_outil` — et c'est lui qui a
produit le relevé consigné au ticket. Ce fichier existe pour que ce relevé reste
vrai : un banc joué une fois et jamais rejoué est une capture d'écran.

Deux moitiés, et la seconde compte autant que la première :

① **le parcours passe**, sur les politiques du dépôt et non sur une politique
   écrite pour l'occasion — c'est tout l'objet de #716, où la chaîne était
   complète et dormante faute de fichier qui la déclenche ;
② **le banc sait rendre un verdict négatif**. Sans cette moitié, son ✓ ne dirait
   rien : un banc qui ne peut pas échouer ne mesure pas, il affirme. C'est la
   même précaution que le parseur de motifs de `tests/test_permissions.py` ②bis,
   éprouvé sur un échantillon fautif avant de balayer le vrai README.

⚠ Ce qui n'est **pas** couvert, ici comme au banc : le modèle qui *choisit*
d'appeler l'outil. Le banc joue le hook sur des actes écrits dans son propre
module — c'est le seul maillon substitué, et le seul qu'on ne peut pas jouer
sans quota ni aléa. Les relevés où un vrai agent appelle réellement ses outils
MCP sont ailleurs : docs/15 (#105) et docs/20 (#128).

Ni réseau, ni Redis, ni quota : bus mémoire, plan constant, fournisseur local.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
SCRIPT = RACINE / "scripts" / "arbitrage" / "banc-arbitrage.py"


def _module():
    """Le script porte un tiret dans son nom : il s'importe par son chemin."""
    spec = importlib.util.spec_from_file_location("banc_arbitrage", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Enregistré **avant** l'exécution : `dataclasses` résout les annotations
    # différées (`from __future__ import annotations`) en cherchant le module
    # dans `sys.modules`, si bien que la seule déclaration d'une dataclass lève
    # sans cette ligne — et lève dans `dataclasses`, loin d'ici.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def banc():
    return _module()


@pytest.fixture(scope="module")
def releve(banc):
    """Le parcours, joué **une** fois pour tout le fichier (il monte une app FastAPI)."""
    return banc.joue()


# --- ① Le parcours passe, sur le dépôt versionné ----------------------------------------


def test_le_banc_lit_les_politiques_versionnees_du_depot(banc):
    """La prémisse : ce qui est exercé est ce qui partira en production.

    Un banc qui écrirait sa propre politique dans un répertoire temporaire
    prouverait que le moteur sait arbitrer — ce que #573 avait déjà prouvé —, et
    pas que le dépôt, lui, classe quoi que ce soit. C'est exactement la
    différence que #716 a trouvée : chaîne complète, dépôt muet.
    """
    assert banc.RACINE == RACINE
    politique = banc._politique()
    assert politique is not None, f"aucune politique versionnée pour « {banc.AGENT} »"
    assert politique.ask


def test_les_trois_regimes_sont_joues_et_pas_seulement_l_arbitrage(banc):
    """Le banc joue `humain`, `auto` **et** `allow` — sinon il ne montre pas la différence.

    C'est la question du ticket, pas une commodité de couverture : `auto` ne se
    distingue d'`allow` que par la trace qu'il laisse, et on ne voit une trace
    qu'en la comparant à une absence de trace.
    """
    from maestro.decideur import Decideur

    crans = {cran for _, _, cran in banc.ACTES}
    assert crans == {Decideur.HUMAIN, Decideur.AUTO, None}


def test_la_chaine_est_exercee_de_bout_en_bout(banc, releve):
    """Les cinq stations répondent — le relevé du ticket, rejoué."""
    assert releve.ok, banc.rend(releve)
    assert [station.nom for station in releve.stations] == list(banc.ORDRE)
    assert not releve.erreur


def test_l_acte_arbitre_atteint_la_file_avec_son_outil_et_son_cran(banc, releve):
    """Ce que la carte doit porter : l'**acte**, pas le titre du livrable (#573/#581)."""
    (file,) = [station for station in releve.stations if station.nom == "file"]

    assert file.detail["outil"] == banc.ACTE_ARBITRE
    assert file.detail["decideur"] == "humain"
    assert file.detail["arguments"]
    # La raison nomme l'outil et la liste qui l'a mis en arbitrage — jamais le
    # livrable : c'est l'inversion que le parent #573 a opérée.
    assert banc.ACTE_ARBITRE in file.detail["raison"]


def test_le_journal_nomme_le_decideur_de_chaque_acte_arbitre(banc, releve):
    """Qui a tranché se **lit** au journal, il ne se déduit pas (#586)."""
    (journal,) = [station for station in releve.stations if station.nom == "journal"]

    assert journal.detail[banc.ACTE_ARBITRE].startswith("Outil arbitré (humain)")
    # Et l'acte `auto` laisse la même trace sous son propre cran : c'est toute sa
    # différence d'avec un `allow`, qui n'en laisse aucune.
    auto = "mcp__figma-officiel__get_metadata"
    assert journal.detail[auto].startswith("Outil arbitré (auto)")
    assert "Read" not in journal.detail


# --- ② Le banc sait rendre un verdict négatif -------------------------------------------


def test_sans_politique_le_banc_le_dit_au_lieu_de_passer(banc):
    """Un dépôt muet doit sortir en KO : c'est l'état d'avant #716, pas un succès."""
    releve = banc.Releve()
    banc._station_politique(releve, None)

    assert not releve.ok
    assert "aucune politique versionnée" in releve.stations[0].constat


def test_une_demande_qui_n_atteint_jamais_la_file_est_un_echec(banc):
    """Le mode de panne le plus plausible — et le plus silencieux si on l'ignorait."""
    releve = banc.Releve()
    banc._station_file(releve, None)

    assert not releve.ok
    assert "NON exercée" in banc.rend(releve)


def test_un_journal_sans_trace_d_arbitrage_est_un_echec(banc):
    """La trace est la moitié du contrat : arbitrer sans consigner ne garde rien."""
    from maestro.telemetry import RunJournal

    releve = banc.Releve()
    banc._station_journal(releve, RunJournal(run_id="vide"), None)

    assert not releve.ok
