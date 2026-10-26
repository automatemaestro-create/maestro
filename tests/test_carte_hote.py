"""La carte d'un hôte de run — et le seul verdict qu'elle sait rendre : « éteint » (#1503).

Le défaut : un run dont l'hôte a été **tué** restait « En cours » trente minutes, le
temps que son dernier battement vieillisse jusqu'au seuil d'orphelinat. La carte dit
quel process porte le run ; ce fichier garde ce qu'elle a le droit d'en conclure, et
surtout ce qu'elle n'a **pas** le droit d'en conclure.

① **La forme** — une carte se range et se relit ; une carte illisible n'est rien, et
   jamais une levée.

② **La sonde, sur de vrais process** — ce process tourne, avec une naissance stable ;
   un process né puis sorti est mort. Pas de pid inventé : c'est l'appel système du
   poste qu'on exerce, celui qui a parlé sur p5.

③ **Le verdict** — « éteint » seulement quand c'est certain (pid mort sur cette
   machine, ou recyclé : un autre process né à un autre instant). Tout le reste se
   tait : autre machine, carte illisible, naissance inconnue, sonde qui doute ou qui
   casse. Se taire n'est pas dire « vivant » : c'est laisser la règle du seuil juger.

Les gestes de l'écran qui en dépendent — le run lu `orphelin`, la pause refusée, la
reprise sur son plan — sont gardés au niveau du service et de ses routes, dans
`tests/test_reprise_du_plan.py`.
"""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from maestro.controltower.carte_hote import (
    SONDE_INCONNUE,
    SONDE_MORT,
    SONDE_VIVANT,
    CarteHote,
    Sonde,
    carte_de_ce_process,
    hote_eteint,
    machine_courante,
    sonder,
)

#: Les plateformes où le système donne la naissance d'un process — Windows
#: (`GetProcessTimes`) et Linux (`/proc`). Ailleurs elle vaut `None`, et la carte ne
#: démasque pas un pid recyclé : le seuil reprend la main.
NAISSANCE_LISIBLE = sys.platform == "win32" or sys.platform.startswith("linux")


def _pid_d_un_process_sorti() -> int:
    """Le pid d'un vrai process, né puis sorti de lui-même — rien ne reste derrière."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])  # noqa: S603 - argv fixe
    process.wait(timeout=60)
    return process.pid


# ------------------------------------------------------------------- ① la forme


def test_une_carte_se_range_et_se_relit_a_l_identique():
    carte = CarteHote(pid=4312, machine="POSTE", naissance=133_700_000_000_000_000)
    assert CarteHote.depuis_texte(carte.vers_texte()) == carte
    assert CarteHote.depuis_texte(CarteHote(pid=7, machine="m").vers_texte()) == CarteHote(
        pid=7, machine="m", naissance=None
    )


@pytest.mark.parametrize(
    "brut",
    [
        None,
        "",
        "pas du json",
        "[1, 2]",
        '{"machine": "POSTE"}',
        '{"pid": "4312", "machine": "POSTE"}',
        '{"pid": true, "machine": "POSTE"}',
        '{"pid": 0, "machine": "POSTE"}',
        '{"pid": 4312, "machine": ""}',
    ],
)
def test_une_carte_illisible_n_est_rien(brut):
    assert CarteHote.depuis_texte(brut) is None


def test_une_naissance_illisible_retombe_sur_none_sans_perdre_la_carte():
    carte = CarteHote.depuis_texte('{"pid": 12, "machine": "m", "naissance": "hier"}')
    assert carte == CarteHote(pid=12, machine="m", naissance=None)


# ------------------------------------------------- ② la sonde, sur de vrais process


def test_ce_process_tourne_et_sa_naissance_est_stable():
    premiere, seconde = sonder(os.getpid()), sonder(os.getpid())
    assert premiere.etat == SONDE_VIVANT
    if NAISSANCE_LISIBLE:
        assert premiere.naissance is not None
    assert premiere.naissance == seconde.naissance


def test_un_process_sorti_est_mort():
    assert sonder(_pid_d_un_process_sorti()).etat == SONDE_MORT


def test_un_pid_impossible_est_mort():
    assert sonder(0).etat == SONDE_MORT
    assert sonder(-1).etat == SONDE_MORT


def test_la_carte_de_ce_process_le_designe():
    carte = carte_de_ce_process()
    assert carte.pid == os.getpid()
    assert carte.machine == machine_courante()
    assert carte.naissance == sonder(os.getpid()).naissance


# ------------------------------------------------------------------- ③ le verdict


def test_l_hote_d_un_process_sorti_est_eteint():
    """Le cas de p5 : le process qui portait le run n'existe plus sur ce poste."""
    carte = CarteHote(pid=_pid_d_un_process_sorti(), machine=machine_courante())
    assert hote_eteint(carte.vers_texte()) is True


def test_l_hote_qui_tourne_n_est_pas_eteint():
    assert hote_eteint(carte_de_ce_process().vers_texte()) is False


def test_une_carte_d_une_autre_machine_ne_conclut_rien():
    """Un pid n'a de sens que sur sa machine : un Redis partagé n'y change rien."""
    carte = CarteHote(pid=_pid_d_un_process_sorti(), machine=f"{machine_courante()}-ailleurs")
    assert hote_eteint(carte.vers_texte()) is False


def test_sans_carte_lisible_rien_n_est_conclu():
    assert hote_eteint(None) is False
    assert hote_eteint("pas une carte") is False


def _sonde(etat: str, naissance: int | None = None):
    return lambda _pid: Sonde(etat, naissance)


def test_un_pid_recycle_par_un_autre_process_est_un_hote_eteint():
    """Le numéro tourne, mais le process est né à un autre instant : ce n'est plus l'hôte."""
    carte = CarteHote(pid=4312, machine="POSTE", naissance=100).vers_texte()
    assert hote_eteint(carte, machine="POSTE", sonde=_sonde(SONDE_VIVANT, 200)) is True
    assert hote_eteint(carte, machine="POSTE", sonde=_sonde(SONDE_VIVANT, 100)) is False


def test_une_naissance_inconnue_d_un_cote_ne_condamne_pas():
    """Un pid qui tourne, sans naissance à confronter : on ne sait pas — le seuil jugera."""
    sans = CarteHote(pid=4312, machine="POSTE").vers_texte()
    avec = CarteHote(pid=4312, machine="POSTE", naissance=100).vers_texte()
    assert hote_eteint(sans, machine="POSTE", sonde=_sonde(SONDE_VIVANT, 200)) is False
    assert hote_eteint(avec, machine="POSTE", sonde=_sonde(SONDE_VIVANT, None)) is False


def test_une_sonde_qui_doute_ou_qui_casse_ne_condamne_pas():
    carte = CarteHote(pid=4312, machine="POSTE", naissance=100).vers_texte()
    assert hote_eteint(carte, machine="POSTE", sonde=_sonde(SONDE_INCONNUE)) is False

    def casse(_pid: int) -> Sonde:
        raise OSError("accès refusé")

    assert hote_eteint(carte, machine="POSTE", sonde=casse) is False
