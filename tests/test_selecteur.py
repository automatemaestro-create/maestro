"""Tests du sélecteur de dossier natif (#278), et de son ouverture au-dessus (#311).

Deux couches, et deux tickets. La seconde moitié du fichier — **la disponibilité
et les deux routes** — est le lot 6 de #276 (#282) : #278 avait livré le
sélecteur sans tests (tests différés, [docs/10 §5.1](../docs/10-workflow-git.md))
et #311 n'en avait écrit que pour son correctif. Ce qui s'y mesure est le
critère du lot — « repli quand le sélecteur natif n'est pas disponible » — plus
que l'ouverture elle-même : une fenêtre qui s'ouvre ne se teste pas sans écran,
mais **tout ce qui décide de ne pas l'ouvrir** se teste, et c'est là que vivent
les garde-fous (boucle locale, réglage, absence d'outil, verrou, attente).

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

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import ControlTowerState, InMemoryEventBus, create_app, selecteur
from maestro.controltower.projets import ServiceProjets
from maestro.projets import ProjetStore

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


# --- #282 : la disponibilité, et le repli quand elle manque ---------------
#
# « Un confort, jamais un passage obligé » : ce que ces tests mesurent n'est pas
# l'ouverture d'une fenêtre mais **les quatre façons de ne pas en ouvrir**, et
# le fait qu'aucune ne laisse un cul-de-sac — un motif, une phrase, et
# l'explorateur servi par l'API qui reste la voie complète.


@pytest.mark.parametrize(
    "hote",
    ["127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1", "LOCALHOST", " 127.0.0.1 "],
)
def test_la_boucle_locale_vaut_le_poste(hote: str) -> None:
    """Les quatre écritures de « cette machine », à la casse et aux espaces près."""
    assert selecteur.hote_du_poste(hote) is True


@pytest.mark.parametrize("hote", ["192.168.1.20", "10.0.0.1", "exemple.test", "", None])
def test_tout_le_reste_est_distant_y_compris_l_inconnu(hote: str | None) -> None:
    """Un hôte inconnu est traité comme **distant** : dans le doute, aucune fenêtre.

    Le cas `None` n'est pas théorique — un transport ASGI peut ne pas donner de
    client. Le traiter comme local ouvrirait une fenêtre sur le serveur au
    premier appel venu.
    """
    assert selecteur.hote_du_poste(hote) is False


def test_le_reglage_a_zero_eteint_le_selecteur_avant_tout_le_reste(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`MAESTRO_SELECTEUR_NATIF=0` : franc, et prioritaire sur le poste comme sur l'outil."""
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: WINDOWS)

    etat = selecteur.disponibilite(poste=True, reglage="0")

    assert etat["disponible"] is False
    assert etat["motif"] == "selecteur-desactive"
    assert etat["outil"] is None
    assert "l'explorateur ci-dessous reste disponible" in str(etat["message"])


def test_un_backend_distant_le_dit_plutot_que_d_ouvrir_chez_lui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le mode serveur, troisième critère de #278 : pas de bouton mort, une phrase."""
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: WINDOWS)

    etat = selecteur.disponibilite(poste=False)

    assert etat["motif"] == "selecteur-hors-poste"
    assert "explorateur" in str(etat["message"])


def test_sans_outil_sur_le_poste_l_indisponibilite_est_une_reponse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ni PowerShell, ni osascript, ni zenity — ou pas de session graphique."""
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: None)

    etat = selecteur.disponibilite(poste=True)

    assert etat["motif"] == "selecteur-sans-outil"


def test_disponible_nomme_l_outil_qui_ouvrira_la_fenetre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le cas nominal — et `auto` est bien le défaut, sans réglage à poser."""
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: MACOS)

    etat = selecteur.disponibilite(poste=True)

    assert etat["disponible"] is True
    assert etat["motif"] is None
    assert etat["outil"] == "osascript"


def test_sans_session_graphique_linux_n_a_pas_d_outil(monkeypatch: pytest.MonkeyPatch) -> None:
    """`zenity` installé sur un serveur sans écran n'ouvrirait rien — et bloquerait 5 min.

    L'absence de `DISPLAY`/`WAYLAND_DISPLAY` vaut donc absence d'outil, ce qui
    transforme une attente de `DUREE_MAX_S` en une réponse immédiate.
    """
    monkeypatch.setattr(selecteur.sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setattr(selecteur.shutil, "which", lambda nom: f"/usr/bin/{nom}")

    assert selecteur.outil_disponible() is None


# --- #282 : les deux routes (docs/05 §6.7) --------------------------------


@pytest.fixture()
def maison(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un dossier utilisateur factice — même raison qu'en #221 : `tmp_path` est sous `AppData`."""
    maison = tmp_path / "maison"
    maison.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


@pytest.fixture()
def service(tmp_path: Path, maison: Path) -> ServiceProjets:
    """Service des projets sur un dépôt jetable, borné au dossier utilisateur factice."""
    return ServiceProjets(ProjetStore(tmp_path / "depot"), racines_exploration=(maison,))


@pytest.fixture()
def dialogue_disponible(monkeypatch: pytest.MonkeyPatch) -> selecteur.Outil:
    """Un outil de dialogue présent, quoi qu'en dise la machine qui joue le test.

    Les tests du `POST` mesurent le **contrat de la route**, pas l'inventaire du
    poste : la route interroge `disponibilite()` avant d'appeler
    `choisir_dossier`, donc monkeypatcher le seul dialogue laisse le verdict à
    `outil_disponible()`. Sur un poste Windows il trouve PowerShell et les tests
    passent ; dans le conteneur Linux de la CI — ni `DISPLAY`, ni `zenity` — il
    rend `None` et tout sort en 403 `selecteur-sans-outil`. C'est le même piège
    que `git` absent du job `pytest` : vert en local, rouge en CI, pour une
    raison qui n'est pas celle qu'on teste.
    """
    monkeypatch.setattr(selecteur, "outil_disponible", lambda: WINDOWS)
    return WINDOWS


def _client(service: ServiceProjets, *, hote: str) -> TestClient:
    """Un TestClient dont l'adresse cliente est choisie — le discriminant poste/serveur.

    C'est le seul moyen de jouer le mode serveur sans réseau : `_poste` lit
    l'hôte sur le **client TCP** (jamais sur un en-tête, qu'un client pose
    lui-même), donc c'est le client TCP qu'il faut déplacer.
    """
    app = create_app(bus=InMemoryEventBus(), state=ControlTowerState(), projets=service)
    return TestClient(app, client=(hote, 51000))


def test_la_route_selecteur_n_est_pas_avalee_par_la_capture_d_identifiant(
    service: ServiceProjets,
) -> None:
    """« selecteur » est un identifiant de projet valide au regard du slug — l'ordre compte.

    Même piège que `/api/projets/explorateur` : enregistrée après la capture
    `/api/projets/{id_projet}`, la route rendrait un 404 « projet inconnu ».
    """
    with _client(service, hote="127.0.0.1") as client:
        reponse = client.get("/api/projets/selecteur")

    assert reponse.status_code == 200
    assert "disponible" in reponse.json()


def test_l_indisponibilite_est_un_200_jamais_une_erreur(service: ServiceProjets) -> None:
    """Le contrat du `GET` : une indisponibilité est une **réponse**, pas une panne.

    La rendre en 4xx obligerait l'écran à traiter en erreur le cas le plus
    banal — un backend joint depuis une autre machine.
    """
    with _client(service, hote="192.168.1.20") as client:
        reponse = client.get("/api/projets/selecteur")

    assert reponse.status_code == 200
    assert reponse.json() == {
        "disponible": False,
        "motif": "selecteur-hors-poste",
        "message": reponse.json()["message"],
        "outil": None,
    }


def test_un_appel_distant_n_ouvre_aucune_fenetre_sur_le_serveur(
    service: ServiceProjets, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le garde-fou n° 1, mesuré par ce qu'il **empêche** : le dialogue n'est pas appelé.

    Sans lui, un client distant piloterait l'écran d'une autre machine — une
    fenêtre modale devant personne, et le verrou pris pour cinq minutes.
    """
    ouvertures: list[object] = []
    monkeypatch.setattr(
        selecteur, "choisir_dossier", lambda **kw: ouvertures.append(kw) or "/tmp"
    )

    with _client(service, hote="10.0.0.1") as client:
        reponse = client.post("/api/projets/selecteur")

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "selecteur-hors-poste"
    assert ouvertures == []


def test_annuler_n_est_pas_une_erreur(
    service: ServiceProjets, dialogue_disponible: selecteur.Outil, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fermer la fenêtre est un geste normal : 200 et `annule`, pas un 4xx."""
    monkeypatch.setattr(selecteur, "choisir_dossier", lambda **kw: None)

    with _client(service, hote="127.0.0.1") as client:
        reponse = client.post("/api/projets/selecteur")

    assert reponse.status_code == 200
    assert reponse.json() == {
        "annule": True,
        "chemin": None,
        "racine_valide": False,
        "refus": None,
    }


def test_un_dossier_declarable_revient_canonicalise_et_valide(
    service: ServiceProjets,
    maison: Path,
    dialogue_disponible: selecteur.Outil,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le cas nominal : le chemin de l'OS confronté à EF-38, et accepté."""
    choisi = maison / "depots" / "depensio"
    choisi.mkdir(parents=True)
    monkeypatch.setattr(selecteur, "choisir_dossier", lambda **kw: str(choisi))

    with _client(service, hote="127.0.0.1") as client:
        corps = client.post("/api/projets/selecteur").json()

    assert corps == {
        "annule": False,
        "chemin": choisi.resolve().as_posix(),
        "racine_valide": True,
        "refus": None,
    }


def test_un_dossier_lisible_mais_non_declarable_revient_avec_son_motif(
    service: ServiceProjets,
    maison: Path,
    dialogue_disponible: selecteur.Outil,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deux questions distinctes, et c'est tout l'intérêt de les séparer.

    Le dossier utilisateur nu est **lisible** (l'explorateur y descend) et
    **non déclarable** comme racine de projet. Le rendre en 4xx ferait traiter
    en panne un choix parfaitement compréhensible ; l'écran a besoin de
    l'afficher dans le formulaire et d'ouvrir l'explorateur dessus.
    """
    monkeypatch.setattr(selecteur, "choisir_dossier", lambda **kw: str(maison))

    with _client(service, hote="127.0.0.1") as client:
        reponse = client.post("/api/projets/selecteur")

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["annule"] is False
    assert corps["racine_valide"] is False
    assert corps["chemin"] == maison.resolve().as_posix()
    assert corps["refus"]["motif"] == "dossier-utilisateur-nu"


def test_un_empechement_franc_sort_en_403_motive(
    service: ServiceProjets, dialogue_disponible: selecteur.Outil, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le verrou « un dialogue à la fois », vu de la route : un motif que l'UI sait traduire."""

    def refuse(**_: object) -> str:
        raise selecteur.SelecteurRefuse("selecteur-en-cours", "Un dialogue est déjà ouvert.")

    monkeypatch.setattr(selecteur, "choisir_dossier", refuse)

    with _client(service, hote="127.0.0.1") as client:
        reponse = client.post("/api/projets/selecteur")

    assert reponse.status_code == 403
    assert reponse.json()["detail"]["motif"] == "selecteur-en-cours"


def test_le_dossier_de_depart_du_corps_atteint_le_dialogue(
    service: ServiceProjets,
    maison: Path,
    dialogue_disponible: selecteur.Outil,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`{"depart": …}` sert à rouvrir là où on en était — sinon l'option ne sert à rien."""
    recu: dict[str, object] = {}
    monkeypatch.setattr(
        selecteur,
        "choisir_dossier",
        lambda **kw: recu.update(kw) or str(maison),
    )

    with _client(service, hote="127.0.0.1") as client:
        client.post("/api/projets/selecteur", json={"depart": "D:/projets"})

    assert recu == {"depart": "D:/projets"}


def test_le_reglage_du_backend_est_relu_a_chaque_appel(
    service: ServiceProjets, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`MAESTRO_SELECTEUR_NATIF=0` barre aussi le `POST`, sans redémarrer le backend."""
    monkeypatch.setenv("MAESTRO_SELECTEUR_NATIF", "0")
    monkeypatch.setattr(selecteur, "choisir_dossier", lambda **kw: "/nulle/part")

    with _client(service, hote="127.0.0.1") as client:
        disponibilite = client.get("/api/projets/selecteur").json()
        ouverture = client.post("/api/projets/selecteur")

    assert disponibilite["motif"] == "selecteur-desactive"
    assert ouverture.status_code == 403
    assert ouverture.json()["detail"]["motif"] == "selecteur-desactive"
