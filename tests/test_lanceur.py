"""Le lanceur du produit : démarrer et arrêter Maestro d'un seul geste (#640).

Trois critères d'acceptation, et cette suite les prend un par un :

1. **un geste démarre, les ports sont choisis ou signalés** — le front se déplace et
   le dit, l'API ne se déplace **jamais** en silence (son URL est figée dans le front
   au build) ; le navigateur s'ouvre en mode web et pas sous `--no-browser` ;
2. **l'arrêt est propre et complet** — les runs en vol soldés d'abord, chaque service
   éteint **avec sa descendance**, un démarrage raté défait, et **rien tué au jugé** :
   un pid recyclé ne meurt pas de notre main ;
3. **un service qui meurt est signalé avec sa cause** — le code de sortie, les
   dernières lignes de son journal, et le chemin **relatif** de ce journal.

Aucun test n'ouvre de port, ne lance de process ni n'attend une seconde : le lanceur
reçoit un `Systeme` (`maestro.lanceur.systeme`), et c'est un double qui le joue ici.
Ce qui a été éprouvé sur la **vraie stack** est dit dans la PR ; ce qui est éprouvé
ici est ce qui doit rester vrai demain.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from maestro.lanceur import cli, lanceur
from maestro.lanceur import emplacement as lieu
from maestro.lanceur import session as etat_session
from maestro.lanceur.emplacement import Emplacement, Front
from maestro.lanceur.lanceur import Options
from maestro.lanceur.session import Service, Session
from maestro.lanceur.systeme import Processus, Sortie, Systeme

# --------------------------------------------------------------------- les doubles


class ProcessusDouble(Processus):
    """Un service dont la suite décide s'il vit, et avec quel code il est mort."""

    def __init__(self, pid: int, code_sortie: int | None = None) -> None:
        super().__init__(pid)
        self.code_sortie = code_sortie

    def code(self) -> int | None:
        return self.code_sortie


class SystemeDouble(Systeme):
    """Le poste, joué. Chaque geste est enregistré, aucun n'atteint la machine."""

    def __init__(
        self,
        *,
        ports_tenus: set[int] | None = None,
        sondes_muettes: set[str] | None = None,
        codes_sortie: dict[str, int] | None = None,
        lignes: dict[int, str | None] | None = None,
        reponse_post: str | None = '{"runs": [], "nb": 0}',
        morts_immediates: bool = True,
        journaux: dict[str, str] | None = None,
    ) -> None:
        self.ports_tenus = set(ports_tenus or ())
        self.sondes_muettes = set(sondes_muettes or ())
        self.codes_sortie = dict(codes_sortie or {})
        self.lignes_forcees = dict(lignes or {})
        self.reponse_post = reponse_post
        self.morts_immediates = morts_immediates
        self.journaux = dict(journaux or {})

        self.demarrages: list[tuple[tuple[str, ...], Path, Path]] = []
        self.eteints: list[int] = []
        self.postes: list[str] = []
        self.navigateurs: list[str] = []
        self.vivants: set[int] = set()
        self.marqueurs: dict[int, str] = {}
        self._prochain_pid = 1000
        self._horloge = 0.0

    # les ports

    def port_tenu(self, hote: str, port: int) -> bool:
        return port in self.ports_tenus

    def attachable(self, hote: str, port: int) -> bool:
        # Jouer celle-ci suffit : `port_libre_depuis` s'appuie dessus, et son balayage
        # est donc celui du vrai `Systeme`, pas une seconde version écrite ici.
        return port not in self.ports_tenus

    # les process

    def demarrer(
        self,
        argv: Any,
        *,
        cwd: Path,
        journal: Path,
        environ: Any,
    ) -> Processus:
        self._prochain_pid += 1
        pid = self._prochain_pid
        self.demarrages.append((tuple(argv), cwd, journal))
        contenu = self.journaux.get(journal.name)
        if contenu is not None:
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(contenu, encoding="utf-8")
        self.vivants.add(pid)
        self.marqueurs[pid] = " ".join(str(morceau) for morceau in argv)
        return ProcessusDouble(pid, self.codes_sortie.get(journal.name))

    def vivant(self, pid: int) -> bool:
        return pid in self.vivants

    def ligne_de_commande(self, pid: int) -> str | None:
        if pid in self.lignes_forcees:
            return self.lignes_forcees[pid]
        return self.marqueurs.get(pid)

    def eteindre(self, pid: int) -> None:
        self.eteints.append(pid)
        if self.morts_immediates:
            self.vivants.discard(pid)

    # le réseau

    def sonder(self, url: str, delai: float) -> bool:
        return url not in self.sondes_muettes

    def poster(self, url: str, delai: float) -> str | None:
        self.postes.append(url)
        return self.reponse_post

    def ouvrir_navigateur(self, url: str) -> bool:
        self.navigateurs.append(url)
        return True

    # le temps

    def dormir(self, secondes: float) -> None:
        self._horloge += secondes

    def horloge(self) -> float:
        self._horloge += 0.1
        return self._horloge

    def maintenant(self) -> float:
        return 1_700_000_000.0


class SortieDouble(Sortie):
    """Ce que le lanceur a dit, gardé plutôt qu'imprimé."""

    def __init__(self) -> None:
        self.lignes: list[str] = []
        self.alertes: list[str] = []

    def dire(self, message: str = "") -> None:
        self.lignes.append(message)

    def alerter(self, message: str) -> None:
        self.alertes.append(message)

    @property
    def tout(self) -> str:
        return "\n".join([*self.lignes, *self.alertes])


@pytest.fixture
def racine(tmp_path: Path) -> Path:
    """Une racine de produit avec un front **construit** — la forme servable ordinaire."""
    front = tmp_path / "apps" / "web"
    (front / ".next").mkdir(parents=True)
    (front / ".next" / "BUILD_ID").write_text("abc", encoding="utf-8")
    binaire = front / "node_modules" / "next" / "dist" / "bin"
    binaire.mkdir(parents=True)
    (binaire / "next").write_text("#!/usr/bin/env node\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def emplacement(racine: Path) -> Emplacement:
    """Un emplacement complet, et **indépendant du poste** : le Node est nommé, pas cherché.

    Le laisser résoudre ferait dépendre le verdict de la machine — vert ici, rouge dans
    le conteneur Linux du filet, qui n'a pas de Node (mesuré).
    """
    return Emplacement(racine=racine, front=lieu.resoudre_front(racine, {}), node="/usr/bin/node")


def _options(**champs: Any) -> Options:
    defauts: dict[str, Any] = {"navigateur": False, "port_api": 8000, "port_ui": 3000}
    defauts.update(champs)
    return Options(**defauts)


def _demarrer(
    emplacement: Emplacement,
    systeme: SystemeDouble,
    sortie: SortieDouble,
    *,
    options: Options | None = None,
    environ: dict[str, str] | None = None,
    preflight: int = 0,
) -> int:
    return lanceur.demarrer(
        options or _options(),
        emplacement=emplacement,
        systeme=systeme,
        sortie=sortie,
        environ=environ if environ is not None else {},
        preflight=lambda: preflight,
    )


# ------------------------------------------------------------------ l'emplacement


def test_racine_produit_suit_la_variable(tmp_path: Path) -> None:
    """`MAESTRO_RACINE_PRODUIT` passe devant toute détection — c'est la porte de l'installeur."""
    assert lieu.racine_produit({"MAESTRO_RACINE_PRODUIT": str(tmp_path)}) == tmp_path


def test_racine_produit_remonte_jusqu_au_front(tmp_path: Path) -> None:
    """Le paquet peut vivre sous la racine (installation) : on remonte jusqu'à un front."""
    (tmp_path / "web").mkdir()
    profond = tmp_path / "runtime" / "lib" / "site-packages"
    profond.mkdir(parents=True)
    assert lieu.racine_produit({}, depart=profond) == tmp_path


def test_front_construit_est_servi_par_next(racine: Path) -> None:
    front = lieu.resoudre_front(racine, {})
    assert front.forme == "build"
    assert front.servable
    assert front.cible is not None and front.cible.name == "next"


def test_front_autonome_est_prefere(racine: Path) -> None:
    """La sortie « standalone » gagne : c'est la forme qu'une installation embarquera."""
    autonome = racine / "apps" / "web" / ".next" / "standalone" / "apps" / "web"
    autonome.mkdir(parents=True)
    (autonome / "server.js").write_text("", encoding="utf-8")
    front = lieu.resoudre_front(racine, {})
    assert front.forme == "standalone"
    assert front.cible == autonome / "server.js"


def test_front_non_construit_nomme_le_geste(tmp_path: Path) -> None:
    """Pas de repli silencieux sur `next dev` : on dit ce qui manque, et comment le poser."""
    (tmp_path / "apps" / "web").mkdir(parents=True)
    front = lieu.resoudre_front(tmp_path, {})
    assert front.forme == "absent"
    assert not front.servable
    assert "npm run build" in front.manque


def test_front_construit_sans_serveur_est_nomme(tmp_path: Path) -> None:
    dossier = tmp_path / "apps" / "web" / ".next"
    dossier.mkdir(parents=True)
    (dossier / "BUILD_ID").write_text("abc", encoding="utf-8")
    front = lieu.resoudre_front(tmp_path, {})
    assert front.forme == "absent"
    assert "sans serveur" in front.manque


def test_front_absent_est_nomme(tmp_path: Path) -> None:
    front = lieu.resoudre_front(tmp_path, {})
    assert front.forme == "absent"
    assert "MAESTRO_FRONT" in front.manque


def test_node_embarque_passe_avant_le_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Une installation sert son propre Node : celui du poste ne doit pas le doubler."""
    monkeypatch.setattr(lieu.shutil, "which", lambda _nom: "/usr/bin/node")
    embarque = tmp_path / "runtime"
    embarque.mkdir()
    for nom in ("node", "node.exe"):
        (embarque / nom).write_text("", encoding="utf-8")
    trouve, manque = lieu.resoudre_node(tmp_path, {})
    assert manque == ""
    assert trouve is not None and Path(trouve).parent == embarque


def test_node_impose_introuvable_est_refuse(tmp_path: Path) -> None:
    trouve, manque = lieu.resoudre_node(tmp_path, {"MAESTRO_NODE": str(tmp_path / "absent")})
    assert trouve is None
    assert "MAESTRO_NODE introuvable" in manque


def test_node_du_path_en_dernier_recours(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lieu.shutil, "which", lambda _nom: "/usr/bin/node")
    assert lieu.resoudre_node(tmp_path, {}) == ("/usr/bin/node", "")


def test_node_absent_nomme_son_geste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lieu.shutil, "which", lambda _nom: None)
    trouve, manque = lieu.resoudre_node(tmp_path, {})
    assert trouve is None
    assert "MAESTRO_NODE" in manque
    assert "scripts/" not in manque  # le produit ne parle pas son dépôt (#939)


def test_les_chemins_dits_sont_relatifs(emplacement: Emplacement) -> None:
    """docs/10 §8.5 : ce qu'on invite à lire se dit en relatif — une session autonome le lira."""
    assert emplacement.relatif(emplacement.journal("api")) == ".maestro/lanceur/api.log"
    assert emplacement.relatif(Path("/ailleurs/api.log")).endswith("api.log")


# ---------------------------------------------------------------------- la session


def test_session_aller_et_retour(tmp_path: Path) -> None:
    inscrite = Session(demarre_a=12.0, url="http://127.0.0.1:3000").avec(
        Service(
            nom="api",
            pid=42,
            hote="127.0.0.1",
            port=8000,
            marqueur="maestro.controltower.cli",
            journal=".maestro/lanceur/api.log",
            sonde="http://127.0.0.1:8000/api/sante",
        )
    )
    chemin = tmp_path / "session.json"
    etat_session.ecrire(chemin, inscrite)
    relue = etat_session.lire(chemin)
    assert relue == inscrite
    assert relue is not None and relue.service("api") is not None
    assert relue.service("ui") is None


def test_session_abimee_ne_leve_pas(tmp_path: Path) -> None:
    """Un arrêt doit pouvoir se tenter quoi qu'il arrive : « je ne sais pas » se dit,
    jamais une trace de `JSONDecodeError`."""
    chemin = tmp_path / "session.json"
    assert etat_session.lire(chemin) is None
    chemin.write_text("{ pas du json", encoding="utf-8")
    assert etat_session.lire(chemin) is None
    chemin.write_text(json.dumps({"version": 99}), encoding="utf-8")
    assert etat_session.lire(chemin) is None
    chemin.write_text(
        json.dumps({"version": etat_session.VERSION, "services": [{"nom": "api"}]}),
        encoding="utf-8",
    )
    assert etat_session.lire(chemin) is None


def test_session_remplace_un_service_du_meme_nom() -> None:
    def service(pid: int) -> Service:
        return Service(
            nom="api",
            pid=pid,
            hote="127.0.0.1",
            port=8000,
            marqueur="m",
            journal="j",
            sonde="s",
        )

    inscrite = Session(demarre_a=0.0, url="u").avec(service(1)).avec(service(2))
    assert [s.pid for s in inscrite.services] == [2]


# --------------------------------------------------------------- critère 1 : démarrer


def test_un_geste_demarre_les_deux_services(
    emplacement: Emplacement, racine: Path
) -> None:
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 0

    assert len(systeme.demarrages) == 2
    argv_api, _, journal_api = systeme.demarrages[0]
    assert "maestro.controltower.cli" in argv_api
    assert "8000" in argv_api
    assert journal_api.name == "api.log"
    argv_ui, _, _ = systeme.demarrages[1]
    assert argv_ui[-4:] == ("--port", "3000", "--hostname", "127.0.0.1")

    inscrite = etat_session.lire(emplacement.session)
    assert inscrite is not None
    assert {s.nom for s in inscrite.services} == {"api", "ui"}
    assert inscrite.url == "http://127.0.0.1:3000"
    assert "Maestro est prêt : http://127.0.0.1:3000" in sortie.tout


def test_le_mode_web_ouvre_le_navigateur(emplacement: Emplacement) -> None:
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie, options=_options(navigateur=True)) == 0
    assert systeme.navigateurs == ["http://127.0.0.1:3000"]


def test_sous_la_coque_rien_ne_s_ouvre(emplacement: Emplacement) -> None:
    """Le contrat de `apps/desktop/main.js` : `--no-browser` rend la main sans fenêtre."""
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 0
    assert systeme.navigateurs == []


def test_le_port_du_front_se_deplace_et_se_dit(emplacement: Emplacement) -> None:
    systeme = SystemeDouble(ports_tenus={3000})
    sortie = SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 0
    assert ":3000 est pris — le front écoute sur :3001" in sortie.tout
    inscrite = etat_session.lire(emplacement.session)
    assert inscrite is not None
    service_ui = inscrite.service("ui")
    assert service_ui is not None and service_ui.port == 3001


def test_le_port_de_l_api_ne_se_deplace_jamais_en_silence(emplacement: Emplacement) -> None:
    """L'URL de l'API est figée dans le front au build : la déplacer servirait un écran muet."""
    systeme = SystemeDouble(ports_tenus={8000})
    sortie = SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 4
    assert systeme.demarrages == []
    assert "Le port de l'API (:8000) est déjà tenu." in sortie.tout
    assert "--port-api" in sortie.tout
    assert not emplacement.session.exists()


def test_aucun_port_libre_pour_le_front(emplacement: Emplacement) -> None:
    systeme = SystemeDouble(ports_tenus=set(range(3000, 3100)))
    sortie = SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 1
    assert "Aucun port libre" in sortie.tout


def test_front_absent_ne_demarre_rien(tmp_path: Path) -> None:
    emplacement = Emplacement(
        racine=tmp_path,
        front=Front(dossier=None, forme="absent", cible=None, manque="rien à servir"),
        node="node",
    )
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 1
    assert systeme.demarrages == []
    assert "rien à servir" in sortie.tout


def test_node_absent_ne_demarre_rien(racine: Path) -> None:
    emplacement = Emplacement(
        racine=racine,
        front=lieu.resoudre_front(racine, {}),
        node=None,
        manque_node="Node introuvable — l'installer",
    )
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 1
    assert systeme.demarrages == []
    assert "Node introuvable" in sortie.tout


def test_le_preflight_refuse_avant_tout(emplacement: Emplacement) -> None:
    """Ce qui manque au démarrage se découvre AVANT d'avoir touché à quoi que ce soit."""
    systeme, sortie = SystemeDouble(), SortieDouble()
    assert _demarrer(emplacement, systeme, sortie, preflight=1) == 1
    assert systeme.demarrages == []
    assert "rien n'a été démarré ni arrêté" in sortie.tout


def test_le_journal_de_l_api_est_force_en_utf8(emplacement: Emplacement) -> None:
    """Une cause en mojibake n'est plus une cause (#141)."""
    systeme, sortie = SystemeDouble(), SortieDouble()
    environs: list[dict[str, str]] = []

    def demarrer_espion(argv: Any, *, cwd: Path, journal: Path, environ: Any) -> Processus:
        environs.append(dict(environ))
        return SystemeDouble.demarrer(systeme, argv, cwd=cwd, journal=journal, environ=environ)

    systeme.demarrer = demarrer_espion  # type: ignore[method-assign]
    assert _demarrer(emplacement, systeme, sortie) == 0
    assert environs[0]["PYTHONIOENCODING"] == "utf-8"
    assert environs[1]["NEXT_PUBLIC_MAESTRO_API_URL"] == "http://127.0.0.1:8000"
    assert environs[1]["PORT"] == "3000"


# ------------------------------------------------- critère 3 : la mort et sa cause


def test_une_api_morte_est_dite_avec_sa_cause(emplacement: Emplacement) -> None:
    systeme = SystemeDouble(
        codes_sortie={"api.log": 3},
        journaux={"api.log": "chargement…\nRedisError: connexion refusée\n"},
    )
    sortie = SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 1
    dit = sortie.tout
    assert "s'est arrêté au démarrage (code 3)" in dit
    assert "RedisError: connexion refusée" in dit
    assert ".maestro/lanceur/api.log" in dit


def test_un_front_mort_defait_le_demarrage(emplacement: Emplacement) -> None:
    """« Y compris quand un des deux services a échoué » : l'API ne reste pas orpheline."""
    systeme = SystemeDouble(
        codes_sortie={"ui.log": 1},
        journaux={"ui.log": "Error: cannot find module\n"},
    )
    sortie = SortieDouble()
    assert _demarrer(emplacement, systeme, sortie) == 1
    assert len(systeme.eteints) == 2
    assert systeme.vivants == set()
    assert not emplacement.session.exists()
    assert "cannot find module" in sortie.tout


def test_un_service_muet_finit_par_etre_dit(emplacement: Emplacement) -> None:
    systeme = SystemeDouble(sondes_muettes={"http://127.0.0.1:8000/api/sante"})
    sortie = SortieDouble()
    options = _options(delai_api=1.0)
    assert _demarrer(emplacement, systeme, sortie, options=options) == 1
    assert "n'a pas répondu sur :8000" in sortie.tout
    assert systeme.eteints  # l'API lancée est ramassée


def test_cause_dit_un_journal_vide_ou_illisible(tmp_path: Path) -> None:
    vide = tmp_path / "vide.log"
    vide.write_text("\n\n", encoding="utf-8")
    assert "rien écrit" in lanceur.cause(vide, affiche="j.log")
    assert "journal illisible" in lanceur.cause(tmp_path / "absent.log")


def test_cause_garde_la_fin_de_la_trace(tmp_path: Path) -> None:
    """La fin d'une trace nomme la panne : c'est par la gauche qu'on coupe."""
    journal = tmp_path / "api.log"
    journal.write_text("\n".join(f"ligne {n}" for n in range(200)), encoding="utf-8")
    dit = lanceur.cause(journal, longueur=30)
    assert dit.startswith("…")
    assert "ligne 199" in dit


# ----------------------------------------------------- critère 2 : l'arrêt complet


def _stack_inscrite(emplacement: Emplacement, *, pids: tuple[int, int] = (11, 22)) -> Session:
    inscrite = Session(demarre_a=1_700_000_000.0, url="http://127.0.0.1:3000")
    inscrite = inscrite.avec(
        Service(
            nom="api",
            pid=pids[0],
            hote="127.0.0.1",
            port=8000,
            marqueur="maestro.controltower.cli",
            journal=".maestro/lanceur/api.log",
            sonde="http://127.0.0.1:8000/api/sante",
        )
    )
    inscrite = inscrite.avec(
        Service(
            nom="ui",
            pid=pids[1],
            hote="127.0.0.1",
            port=3000,
            marqueur="/produit/web/server.js",
            journal=".maestro/lanceur/ui.log",
            sonde="http://127.0.0.1:3000",
        )
    )
    etat_session.ecrire(emplacement.session, inscrite)
    return inscrite


def _stack_vivante(systeme: SystemeDouble) -> None:
    """Les deux pids de `_stack_inscrite`, vivants et **reconnaissables** à leur marqueur."""
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {
        11: "python -m maestro.controltower.cli --port 8000",
        22: "node /produit/web/server.js",
    }


def _arreter(
    emplacement: Emplacement,
    systeme: SystemeDouble,
    sortie: SortieDouble,
    environ: dict[str, str] | None = None,
) -> int:
    return lanceur.arreter(
        _options(action="arreter"),
        emplacement=emplacement,
        systeme=systeme,
        sortie=sortie,
        environ=environ or {},
    )


def test_l_arret_solde_les_runs_puis_eteint_le_front_d_abord(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(
        reponse_post='{"runs": [{"run_id": "run-7"}, {"run_id": "run-9"}], "nb": 2}'
    )
    _stack_vivante(systeme)
    sortie = SortieDouble()

    assert _arreter(emplacement, systeme, sortie) == 0
    assert systeme.postes == ["http://127.0.0.1:8000/api/extinction"]
    assert systeme.eteints == [22, 11]  # le front d'abord, l'API ensuite
    assert "run-7" in sortie.tout and "run-9" in sortie.tout
    assert not emplacement.session.exists()


def test_l_arret_sans_session_ne_touche_a_rien(emplacement: Emplacement) -> None:
    """Le lanceur n'arrête que ce qu'il a démarré — le reste, il le nomme."""
    systeme = SystemeDouble(ports_tenus={8000, 3000})
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 3
    assert systeme.eteints == []
    assert systeme.postes == []
    assert "ces ports sont pourtant tenus" in sortie.tout
    # Et ce qu'on dit alors ne renvoie à aucune commande du dépôt : ce lanceur parle à
    # qui utilise Maestro (#939, `tests/test_registre_de_langue.py`).
    assert "scripts/" not in sortie.tout


def test_l_extinction_se_desactive_en_le_disant(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble()
    _stack_vivante(systeme)
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie, {"MAESTRO_EXTINCTION": "0"}) == 0
    assert systeme.postes == []
    assert "MAESTRO_EXTINCTION=0" in sortie.tout
    assert systeme.eteints == [22, 11]


def test_une_api_muette_n_empeche_pas_l_arret(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(reponse_post=None)
    _stack_vivante(systeme)
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 0
    assert "rien à solder par ici" in sortie.tout
    assert systeme.eteints == [22, 11]


def test_une_reponse_d_extinction_illisible_ne_fabrique_aucun_run(
    emplacement: Emplacement,
) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(reponse_post="<html>502</html>")
    _stack_vivante(systeme)
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 0
    assert "aucun run en vol" in sortie.tout


def test_un_pid_recycle_n_est_pas_tue(emplacement: Emplacement) -> None:
    """Témoin divergent : ce pid n'est plus le nôtre. Rien n'est tué au jugé (#213, #456)."""
    _stack_inscrite(emplacement)
    systeme = SystemeDouble()
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: "C:/Windows/notepad.exe", 22: "node /produit/web/server.js"}
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 0
    assert systeme.eteints == [22]
    assert "pid recyclé" in sortie.tout


def test_un_temoin_illisible_sans_port_tenu_fait_s_abstenir(emplacement: Emplacement) -> None:
    """Un arrêt qu'on ne peut pas prouver ne se raconte pas comme s'il avait eu lieu."""
    _stack_inscrite(emplacement)
    systeme = SystemeDouble()
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: None, 22: None}
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 1
    assert systeme.eteints == []
    assert "abstention" in sortie.tout
    assert "Arrêt incomplet" in sortie.tout


def test_un_temoin_illisible_mais_le_port_tenu_suffit(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(ports_tenus={8000, 3000})
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: None, 22: None}
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 0
    assert systeme.eteints == [22, 11]


def test_un_service_deja_mort_est_un_arret_propre(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(ports_tenus={8000})
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 0
    assert systeme.eteints == []
    assert "déjà arrêté" in sortie.tout
    assert "reste tenu par un autre process" in sortie.tout


def test_un_service_qui_resiste_rend_l_arret_incomplet(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(morts_immediates=False)
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: "maestro.controltower.cli", 22: "/produit/web/server.js"}
    sortie = SortieDouble()
    assert _arreter(emplacement, systeme, sortie) == 1
    assert "résiste à l'extinction" in sortie.tout


def test_le_redemarrage_ne_solde_jamais_les_runs(emplacement: Emplacement) -> None:
    """La ligne de partage de #441/#700 : elle passe entre *arrêter* et *remplacer*."""
    _stack_inscrite(emplacement)
    systeme = SystemeDouble()
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: "maestro.controltower.cli", 22: "/produit/web/server.js"}
    sortie = SortieDouble()

    assert _demarrer(emplacement, systeme, sortie) == 0
    assert systeme.postes == []  # aucune extinction demandée
    assert systeme.eteints[:2] == [22, 11]  # l'ancienne session est bien rangée
    assert "elle est remplacée" in sortie.tout


def test_le_remplacement_attend_que_les_ports_se_liberent(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(ports_tenus={8000, 3000})
    systeme.vivants = {11, 22}
    systeme.lignes_forcees = {11: "maestro.controltower.cli", 22: "/produit/web/server.js"}
    sortie = SortieDouble()
    # Les ports restent tenus : le lanceur le dit, puis signale la collision de l'API.
    assert _demarrer(emplacement, systeme, sortie) == 4
    assert "ne se libèrent pas" in sortie.tout


# ------------------------------------------------------------------- l'état et le diagnostic


def _etat(emplacement: Emplacement, systeme: SystemeDouble, sortie: SortieDouble) -> int:
    return lanceur.etat(
        _options(action="etat"),
        emplacement=emplacement,
        systeme=systeme,
        sortie=sortie,
        environ={},
    )


def test_l_etat_dit_ce_qui_tourne(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble()
    systeme.vivants = {11, 22}
    sortie = SortieDouble()
    assert _etat(emplacement, systeme, sortie) == 0
    assert "[api] en marche sur :8000" in sortie.tout
    assert "démarrée le" in sortie.tout


def test_l_etat_dit_la_cause_d_un_service_mort(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    journal = emplacement.journal("api")
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("OSError: port déjà pris\n", encoding="utf-8")
    systeme = SystemeDouble()
    systeme.vivants = {22}
    sortie = SortieDouble()
    assert _etat(emplacement, systeme, sortie) == 1
    assert "[api] arrêté (pid 11)" in sortie.tout
    assert "port déjà pris" in sortie.tout


def test_l_etat_dit_un_service_vivant_mais_muet(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    systeme = SystemeDouble(sondes_muettes={"http://127.0.0.1:3000"})
    systeme.vivants = {11, 22}
    sortie = SortieDouble()
    assert _etat(emplacement, systeme, sortie) == 1
    assert "muet sur :3000" in sortie.tout


def test_l_etat_sans_session(emplacement: Emplacement) -> None:
    assert _etat(emplacement, SystemeDouble(), SortieDouble()) == 3


def test_le_diagnostic_ne_demarre_rien(emplacement: Emplacement) -> None:
    systeme, sortie = SystemeDouble(ports_tenus={8000}), SortieDouble()
    code = lanceur.diagnostic(
        _options(action="diagnostic"),
        emplacement=emplacement,
        systeme=systeme,
        sortie=sortie,
        environ={},
    )
    assert code == 0
    assert systeme.demarrages == []
    assert "port api : 8000 (occupé)" in sortie.tout
    assert "session  : aucune" in sortie.tout


def test_le_diagnostic_signale_ce_qui_manque(tmp_path: Path) -> None:
    emplacement = Emplacement(
        racine=tmp_path,
        front=Front(dossier=None, forme="absent", cible=None, manque="rien à servir"),
        node=None,
        manque_node="Node introuvable",
    )
    sortie = SortieDouble()
    code = lanceur.diagnostic(
        _options(action="diagnostic"),
        emplacement=emplacement,
        systeme=SystemeDouble(),
        sortie=sortie,
        environ={},
    )
    assert code == 1
    assert "Node introuvable" in sortie.tout


def test_le_diagnostic_dit_la_session_inscrite(emplacement: Emplacement) -> None:
    _stack_inscrite(emplacement)
    sortie = SortieDouble()
    lanceur.diagnostic(
        _options(action="diagnostic"),
        emplacement=emplacement,
        systeme=SystemeDouble(),
        sortie=sortie,
        environ={},
    )
    assert "api=11" in sortie.tout and "ui=22" in sortie.tout


# ------------------------------------------------------------------ la ligne de commande


def test_les_gestes_de_la_ligne_de_commande() -> None:
    assert cli.analyser([], {}).action == "demarrer"
    assert cli.analyser(["--stop"], {}).action == "arreter"
    assert cli.analyser(["--etat"], {}).action == "etat"
    assert cli.analyser(["--diagnostic"], {}).action == "diagnostic"
    assert cli.analyser(["--no-browser"], {}).navigateur is False
    assert cli.analyser(["--sans-navigateur"], {}).navigateur is False


def test_les_ports_du_worktree_sont_les_defauts() -> None:
    """`worktree.sh ensure` pose ces variables par copie (#152) : les ignorer ferait collision."""
    options = cli.analyser([], {"MAESTRO_PORT_API": "8040", "MAESTRO_PORT_UI": "3040"})
    assert (options.port_api, options.port_ui) == (8040, 3040)
    impose = cli.analyser(["--port-ui", "4000"], {"MAESTRO_PORT_UI": "3040"})
    assert impose.port_ui == 4000


@pytest.mark.parametrize(
    "arguments",
    [
        ["--inconnue"],
        ["--port-api"],
        ["--port-api", "zero"],
        ["--port-api", "0"],
        ["--stop", "--etat"],
    ],
)
def test_les_arguments_refuses(arguments: list[str]) -> None:
    with pytest.raises(cli.UsageRefuse):
        cli.analyser(arguments, {})


def test_une_variable_de_port_illisible_est_refusee() -> None:
    with pytest.raises(cli.UsageRefuse):
        cli.analyser([], {"MAESTRO_PORT_API": "beaucoup"})


def test_l_aide_ne_fait_rien_d_autre(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--help"]) == 0
    assert "maestro-lanceur --stop" in capsys.readouterr().out


def test_un_usage_refuse_sort_en_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--pas-une-option"]) == 2
    assert "option inconnue" in capsys.readouterr().err


def test_le_geste_demande_est_celui_qui_est_joue(
    monkeypatch: pytest.MonkeyPatch, racine: Path
) -> None:
    """La CLI ne décide de rien : elle route, et c'est tout ce qu'on lui demande."""
    joues: list[str] = []

    def espion(nom: str) -> Any:
        def geste(options: Options, **_cadre: Any) -> int:
            joues.append(nom)
            return 0

        return geste

    for nom in ("demarrer", "arreter", "etat", "diagnostic"):
        monkeypatch.setattr(cli, nom, espion(nom))
    monkeypatch.setattr(
        cli, "resoudre_emplacement", lambda _environ: lieu.emplacement({}, depart=racine)
    )
    monkeypatch.setattr(cli.os, "environ", {})
    for arguments in ([], ["--stop"], ["--etat"], ["--diagnostic"]):
        assert cli.main(arguments) == 0
    assert joues == ["demarrer", "arreter", "etat", "diagnostic"]


# ------------------------------------------------------- le poste, pour de vrai
#
# Ce que `Systeme` fait, il le fait sur la vraie machine : ces quelques tests-là
# l'exercent donc réellement — un port qu'on tient, un process qu'on lance et qu'on
# éteint. Rien de coûteux, rien de réseau, et c'est le seul endroit de la suite où le
# poste est touché.


def test_un_port_tenu_est_vu_tenu() -> None:
    systeme = Systeme()
    prise = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    prise.bind(("127.0.0.1", 0))
    prise.listen(1)
    port = prise.getsockname()[1]
    try:
        assert systeme.port_tenu("127.0.0.1", port) is True
        # Le port suivant **libre** : lequel, on ne le promet pas — la machine qui joue
        # la suite est partagée, et ce port-là est pris dans la plage éphémère.
        choisi = systeme.port_libre_depuis("127.0.0.1", port)
        assert choisi is not None and choisi > port
    finally:
        prise.close()
    assert systeme.port_tenu("127.0.0.1", port) is False
    assert systeme.port_libre_depuis("127.0.0.1", port) == port


def test_aucun_port_libre_rend_none() -> None:
    """Le cas réel : `--port-ui 65535` occupé, donc un « suivant » qui n'existe pas."""
    assert Systeme().port_libre_depuis("127.0.0.1", 65536, essais=3) is None


def test_une_sonde_sur_le_vide_ne_repond_pas() -> None:
    systeme = Systeme()
    prise = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    prise.bind(("127.0.0.1", 0))
    port = prise.getsockname()[1]
    prise.close()
    assert systeme.sonder(f"http://127.0.0.1:{port}/api/sante", 0.5) is False
    assert systeme.poster(f"http://127.0.0.1:{port}/api/extinction", 0.5) is None


def test_la_vitalite_se_lit_sans_tuer_personne() -> None:
    """⚠ Sous Windows, `os.kill(pid, 0)` **termine** le process : la sonde passe ailleurs."""
    systeme = Systeme()
    assert systeme.vivant(os.getpid()) is True
    assert systeme.vivant(0) is False
    # Le test lui-même est la preuve qu'on n'a tué personne : il continue.
    assert systeme.vivant(os.getpid()) is True


def test_la_ligne_de_commande_est_un_temoin_ou_rien() -> None:
    ligne = Systeme().ligne_de_commande(os.getpid())
    assert ligne is None or "pytest" in ligne or "python" in ligne.lower()


def test_le_systeme_demarre_puis_eteint_un_vrai_process(tmp_path: Path) -> None:
    script = tmp_path / "dormeur.py"
    # `flush=True` : un service tué net n'a pas le temps de vider son tampon, et ce
    # test-ci veut vérifier le journal, pas la bufferisation de Python.
    script.write_text(
        "import time\nprint('ici', flush=True)\ntime.sleep(120)\n", encoding="utf-8"
    )
    journal = tmp_path / "journal.log"
    systeme = Systeme()

    processus = systeme.demarrer(
        [sys.executable, str(script)],
        cwd=tmp_path,
        journal=journal,
        environ=dict(os.environ),
    )
    assert processus.code() is None
    for _ in range(100):  # le temps que Python démarre et écrive sa première ligne
        if "ici" in journal.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    assert "ici" in journal.read_text(encoding="utf-8")
    assert systeme.vivant(processus.pid)

    systeme.eteindre(processus.pid)
    for _ in range(80):
        # `code()` appelle `poll()`, qui ramasse le fils : sans lui, un process tué
        # reste zombie tant que son PÈRE ne l'a pas relevé, et `vivant` le verrait
        # encore. Le lanceur n'est pas concerné — celui qui arrête n'est jamais celui
        # qui a démarré —, mais ce test-ci l'est, puisqu'il est le père.
        if processus.code() is not None and not systeme.vivant(processus.pid):
            break
        time.sleep(0.05)
    assert processus.code() is not None


def test_le_journal_repart_a_zero_a_chaque_demarrage(tmp_path: Path) -> None:
    """Une cause ne doit jamais citer la vie précédente du service."""
    journal = tmp_path / "journal.log"
    journal.write_text("succès du démarrage précédent\n", encoding="utf-8")
    script = tmp_path / "muet.py"
    script.write_text("pass\n", encoding="utf-8")
    systeme = Systeme()
    processus = systeme.demarrer(
        [sys.executable, str(script)],
        cwd=tmp_path,
        journal=journal,
        environ=dict(os.environ),
    )
    for _ in range(80):
        if processus.code() is not None:
            break
        time.sleep(0.05)
    assert "précédent" not in journal.read_text(encoding="utf-8")


def test_le_navigateur_est_celui_du_poste(monkeypatch: pytest.MonkeyPatch) -> None:
    ouvertes: list[str] = []
    monkeypatch.setattr(
        "webbrowser.open", lambda url: ouvertes.append(url) is None  # noqa: ARG005
    )
    assert Systeme().ouvrir_navigateur("http://127.0.0.1:3000") is True
    assert ouvertes == ["http://127.0.0.1:3000"]


def test_un_navigateur_qui_leve_n_est_jamais_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(_url: str) -> bool:
        raise RuntimeError("pas de bureau ici")

    monkeypatch.setattr("webbrowser.open", refuse)
    assert Systeme().ouvrir_navigateur("http://127.0.0.1:3000") is False


def test_la_sortie_ne_casse_pas_sur_un_flux_detourne(capsys: pytest.CaptureFixture[str]) -> None:
    """Les accents et le « ⚠ » ne doivent jamais tuer la commande au dernier moment."""
    sortie = Sortie()
    sortie.dire("⚠ prêt")
    sortie.alerter("échec")
    capture = capsys.readouterr()
    assert "prêt" in capture.out
    assert "échec" in capture.err


def test_le_module_s_appelle_aussi_par_m() -> None:
    """`python -m maestro.lanceur` : le chemin qu'un clone emprunte (contrepartie du `omit`)."""
    rendu = subprocess.run(
        [sys.executable, "-m", "maestro.lanceur", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rendu.returncode == 0
    assert "maestro-lanceur --stop" in rendu.stdout
