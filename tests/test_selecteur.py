"""Tests du sélecteur de dossier natif (#278), et de son ouverture au-dessus (#311).

Le sujet du ticket #311 est **où** la fenêtre s'ouvre, pas ce qu'elle rend :
`ShowDialog()` sans propriétaire ouvrait la boîte derrière toutes les fenêtres,
sans bouton de barre des tâches pour aller la chercher — invisible, donc
injoignable, et le verrou « un dialogue à la fois » la laissait tenir le bouton
mort pendant les cinq minutes de `DUREE_MAX_S`.

Une position à l'écran ne se teste pas sans écran. Ce qui se teste — et ce qui
suffit, la cause étant une ligne de script — c'est la **commande construite** :
qu'elle fabrique un propriétaire `TopMost`, qu'elle le passe à `ShowDialog`, et
qu'elle le referme. D'où des tests qui n'ouvrent **aucune** fenêtre et tournent
sur les trois OS : ils lisent `_argv` / `_script_*`, jamais `choisir_dossier`.

S'y ajoutent les invariants que la correction ne devait pas emporter — `-STA`,
sortie UTF-8, littéraux échappés, dossier de départ — parce que c'est le même
script qui les porte et qu'une réécriture est exactement l'occasion de les
perdre.
"""

from __future__ import annotations

import pytest

from maestro.controltower import selecteur

WINDOWS = selecteur.Outil("powershell", "C:/Windows/powershell.exe")
MACOS = selecteur.Outil("osascript", "/usr/bin/osascript")
ZENITY = selecteur.Outil("zenity", "/usr/bin/zenity")


def script_windows(depart: str | None = None, titre: str = "Choisir le dossier") -> str:
    """La commande PowerShell telle que `_lancer` la passerait — dernier argv."""
    return selecteur._argv(WINDOWS, depart=depart, titre=titre)[-1]


# --- #311 : la fenêtre s'ouvre au-dessus ---------------------------------


def test_le_dialogue_windows_recoit_un_proprietaire_topmost() -> None:
    """Le cœur du correctif : une fenêtre porteuse `TopMost`, et le dialogue dessus.

    Les trois propriétés vont ensemble et se justifient l'une l'autre : `TopMost`
    place la fenêtre au-dessus sans passer au premier plan (ce que Windows
    refuserait à un process d'arrière-plan), `Opacity=0` et 1 px la rendent
    invisible, `ShowInTaskbar=$false` évite d'ajouter un bouton pour une fenêtre
    qui n'existe que comme support.
    """
    script = script_windows()
    assert "$p=New-Object System.Windows.Forms.Form;" in script
    assert "$p.TopMost=$true;" in script
    assert "$p.ShowInTaskbar=$false;" in script
    assert "$p.Opacity=0;" in script
    assert "$p.Width=1;$p.Height=1;" in script


def test_topmost_est_pose_apres_show_et_une_seule_fois() -> None:
    """Le piège du correctif, et le seul test qui l'attrape.

    Posée **avant** `Show()`, la propriété vaut `$true` côté objet sans jamais
    atteindre la fenêtre : `ExStyle` mesuré à `0x00090000` — pas de
    `WS_EX_TOPMOST` (`0x8`) — contre `0x00090008` posée après. La boîte se
    rouvrait donc derrière tout, exactement comme avant #311, pendant que le
    script *avait l'air* correct.

    L'unicité compte autant que l'ordre : une pose avant **et** après serait
    pire que rien, le setter court-circuitant sur une valeur inchangée — le
    second appel ne ferait alors plus rien du tout.
    """
    script = script_windows()
    assert script.count("$p.TopMost=") == 1
    assert script.index("$p.Show();") < script.index("$p.TopMost=$true;")
    assert script.index("$p.TopMost=$true;") < script.index("$d.ShowDialog($p);")


def test_le_proprietaire_est_materialise_avant_la_boite_modale() -> None:
    """`Show()` puis `DoEvents()` : sans pompe de messages, la fenêtre n'existe pas encore.

    `ShowDialog` s'ouvrirait alors contre un propriétaire non créé — c'est-à-dire
    sans propriétaire, donc exactement le défaut qu'on corrige.
    """
    script = script_windows()
    assert "$p.Show();" in script
    assert "[System.Windows.Forms.Application]::DoEvents();" in script
    assert script.index("$p.Show();") < script.index("$d.ShowDialog($p);")


def test_le_dialogue_windows_n_est_jamais_ouvert_sans_proprietaire() -> None:
    """La régression à barrer : `ShowDialog()` nu, la forme d'avant #311."""
    script = script_windows()
    assert "$d.ShowDialog($p);" in script
    assert "$d.ShowDialog()" not in script


def test_le_proprietaire_est_referme() -> None:
    """Une fenêtre laissée ouverte fuirait — le sous-process ne rendrait pas la main."""
    script = script_windows()
    assert "$p.Close();" in script
    assert script.index("$d.ShowDialog($p);") < script.index("$p.Close();")


def test_macos_active_l_application_avant_de_choisir() -> None:
    """Le pendant macOS : `activate` en tête, sinon la boîte s'ouvre derrière."""
    script = selecteur._script_osascript(None, "Choisir le dossier")
    assert script.startswith("activate\n")
    assert "choose folder with prompt" in script


def test_zenity_reste_inchange() -> None:
    """Aucune règle de premier plan sous X11/Wayland : rien à ajouter, rien à défaire."""
    argv = selecteur._argv(ZENITY, depart=None, titre="Choisir le dossier")
    assert argv == [
        ZENITY.binaire,
        "--file-selection",
        "--directory",
        "--title=Choisir le dossier",
    ]


def test_un_dialogue_deja_ouvert_refuse_sans_en_lancer_un_second(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le verrou tient, et le refus est **motivé** : l'écran a de quoi le dire.

    C'est ce qui se passait sur le poste tant que la boîte s'ouvrait derrière :
    invisible, elle ne pouvait pas être fermée, donc le verrou restait pris et
    chaque nouveau clic tombait ici. La correction rend la fenêtre atteignable —
    ce garde-fou, lui, ne bouge pas, et son motif reste celui que
    `conseilMotif('selecteur-en-cours')` traduit côté UI.
    """
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: WINDOWS)
    lancements: list[object] = []
    monkeypatch.setattr(selecteur, "_lancer", lambda *a, **k: lancements.append(k))

    with selecteur._verrou:
        with pytest.raises(selecteur.SelecteurRefuse) as capture:
            selecteur.choisir_dossier()

    assert capture.value.motif == "selecteur-en-cours"
    assert lancements == [], "aucune seconde fenêtre ne doit partir"


def test_le_verrou_est_rendu_apres_un_dialogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un dialogue terminé libère la place — sinon le premier clic tuerait tous les suivants."""
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: WINDOWS)
    monkeypatch.setattr(selecteur, "_lancer", lambda *a, **k: "E:/projets/demo")

    assert selecteur.choisir_dossier() == "E:/projets/demo"
    assert selecteur.choisir_dossier() == "E:/projets/demo"


# --- Invariants que la correction ne devait pas emporter ------------------


def test_powershell_garde_sta_et_la_sortie_utf8() -> None:
    """`-STA` est exigé par WinForms ; l'UTF-8 tient l'exactitude d'un chemin accentué."""
    argv = selecteur._argv(WINDOWS, depart=None, titre="Choisir le dossier")
    assert argv[:4] == [WINDOWS.binaire, "-NoProfile", "-STA", "-Command"]
    assert argv[-1].startswith("[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;")


def test_le_dossier_de_depart_est_pose_s_il_existe() -> None:
    """Rouvrir là où on en était — mais seulement si le chemin est encore là."""
    script = script_windows(depart="E:/projets")
    assert "$s='E:\\projets';" in script or "$s='E:/projets';" in script
    assert "if($s -and (Test-Path -LiteralPath $s)){$d.SelectedPath=$s};" in script


@pytest.mark.parametrize(
    ("valeur", "attendu"),
    [("Choisir", "'Choisir'"), ("L'atelier", "'L''atelier'"), ("", "''")],
)
def test_les_litteraux_powershell_doublent_les_quotes(valeur: str, attendu: str) -> None:
    """Une apostrophe dans un titre ou un chemin ne doit pas casser la commande."""
    assert selecteur._litteral_powershell(valeur) == attendu


def test_le_titre_avec_apostrophe_reste_une_commande_valide() -> None:
    """Le cas réel : un titre passé tel quel dans le script, sans échappement perdu."""
    assert "$d.Description='L''atelier';" in script_windows(titre="L'atelier")
