"""Démarrer, arrêter, dire où on en est — les trois gestes du lanceur (#640).

Ce module ne touche ni au disque hors de `.maestro/lanceur/`, ni au réseau, ni aux
process : tout passe par le `Systeme` qu'il reçoit (`maestro.lanceur.systeme`). C'est
ce qui rend le lanceur exerçable par la suite, et ce qui rassemble en un seul endroit
ce que le poste a de particulier.

## Les codes de sortie, et pourquoi ils se distinguent

| code | ce qu'il dit |
|---|---|
| `0` | fait |
| `1` | **panne nommée** — front absent, service mort, arrêt non prouvé complet |
| `3` | **rien à faire** — aucune session inscrite (arrêt, état) |
| `4` | **collision de port signalée** — le port de l'API est pris |

`3` n'est pas un échec : arrêter ce qui est déjà arrêté est un non-événement, et un
appelant (la coque, l'installeur) doit pouvoir le distinguer d'une panne. `4` se
distingue de `1` parce qu'il se lève d'un geste — libérer le port, ou en désigner un
autre — et qu'un appelant peut vouloir réessayer plutôt que d'abandonner.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from maestro.lanceur import session as etat_session
from maestro.lanceur.emplacement import Emplacement
from maestro.lanceur.session import Service, Session
from maestro.lanceur.systeme import Processus, Sortie, Systeme

#: Les ports par défaut, ceux de `scripts/controltower/start.sh` : un poste qui a déjà
#: lancé Maestro d'une façon retrouve la même adresse de l'autre.
PORT_API_DEFAUT = 8000
PORT_UI_DEFAUT = 3000
HOTE_DEFAUT = "127.0.0.1"

#: Attentes de démarrage, en secondes. L'API ouvre son bus et rejoue son journal ; le
#: front, lui, n'a plus qu'à servir un build — mais un premier démarrage sur un disque
#: froid reste long. Les deux s'arrêtent **à la mort** du service, jamais seulement au
#: bout du délai : c'est la mort qui porte la cause.
DELAI_API = 45.0
DELAI_UI = 90.0

#: Plafond d'attente du soldage des runs en vol. L'API borne l'extinction de chaque run
#: et les solde ensemble : ce délai n'est pas proportionnel à leur nombre, et l'atteindre
#: veut dire que l'API ne répond plus (#486).
DELAI_EXTINCTION = 20.0

#: Combien de temps on laisse un port se libérer après avoir arrêté la session qu'on
#: remplace. Un serveur qui vient de mourir peut tenir son port une poignée de secondes.
DELAI_LIBERATION = 10.0

#: Combien de temps on laisse un service mourir avant de dire qu'il résiste.
DELAI_MORT = 8.0

#: Les dernières lignes d'un journal qui portent la cause, et la longueur au-delà de
#: laquelle on coupe par la gauche (la fin d'une trace nomme la panne).
LIGNES_CAUSE = 5
LONGUEUR_CAUSE = 500


@dataclass(frozen=True)
class Options:
    """Ce que la ligne de commande a demandé, résolu."""

    action: str = "demarrer"
    navigateur: bool = True
    hote: str = HOTE_DEFAUT
    port_api: int = PORT_API_DEFAUT
    port_ui: int = PORT_UI_DEFAUT
    delai_api: float = DELAI_API
    delai_ui: float = DELAI_UI


#: Le préflight de l'API : ce qui doit répondre avant qu'on démarre quoi que ce soit.
#: Injectable pour la suite ; par défaut, celui de l'API elle-même — la résolution de
#: `REDIS_URL` et le geste exact qui lève l'absence vivent là-bas, et nulle part ici.
Preflight = Callable[[], int]


def preflight_api() -> int:
    from maestro.controltower.cli import verifier_redis

    return verifier_redis()


def demarrer(
    options: Options,
    *,
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
    preflight: Preflight = preflight_api,
) -> int:
    """Monte la stack : API, front, puis le navigateur si on le demande."""
    front = emplacement.front
    if not front.servable or front.cible is None:
        sortie.alerter(f"Front introuvable ou non construit — {front.manque}")
        return 1
    node = emplacement.node
    if node is None:
        sortie.alerter(emplacement.manque_node)
        return 1

    if preflight() != 0:
        sortie.alerter("")
        sortie.alerter("Maestro ne peut pas démarrer — rien n'a été démarré ni arrêté.")
        return 1

    _remplacer_session(emplacement, systeme, sortie, environ, options)

    port_api = _port_api(options, systeme, sortie)
    if port_api is None:
        return 4
    port_ui = _port_ui(options, systeme, sortie)
    if port_ui is None:
        sortie.alerter(f"Aucun port libre pour le front à partir de :{options.port_ui}")
        return 1

    url_api = f"http://{options.hote}:{port_api}"
    url_ui = f"http://{options.hote}:{port_ui}"
    inscrite = Session(demarre_a=systeme.maintenant(), url=url_ui)

    argv_api = [
        sys.executable,
        "-m",
        "maestro.controltower.cli",
        "--hote",
        options.hote,
        "--port",
        str(port_api),
    ]
    journal_api = emplacement.journal("api")
    sortie.dire(
        f"[api] démarrage sur :{port_api} (journal : {emplacement.relatif(journal_api)})"
    )
    # Le journal doit rester lisible : sans cette variable, un Python sous Windows
    # écrirait sa sortie dans l'encodage du poste, et la cause d'une panne — relue en
    # UTF-8 par `cause` — arriverait en mojibake le jour où on en a besoin (#141).
    environ_api = dict(environ)
    environ_api.setdefault("PYTHONIOENCODING", "utf-8")
    process_api = systeme.demarrer(
        argv_api,
        cwd=emplacement.racine,
        journal=journal_api,
        environ=environ_api,
    )
    service_api = Service(
        nom="api",
        pid=process_api.pid,
        hote=options.hote,
        port=port_api,
        # Un jeton **sans espace** : une ligne de commande est requotée différemment
        # sur chaque système, et un fragment à deux mots n'y survit pas.
        marqueur="maestro.controltower.cli",
        journal=emplacement.relatif(journal_api),
        sonde=f"{url_api}/api/sante",
    )
    inscrite = inscrite.avec(service_api)
    etat_session.ecrire(emplacement.session, inscrite)

    echec = _attendre(
        service_api,
        process_api,
        options.delai_api,
        emplacement=emplacement,
        systeme=systeme,
    )
    if echec:
        sortie.alerter(echec)
        _defaire(inscrite, emplacement, systeme, sortie)
        return 1

    # Le serveur autonome lit son port dans l'environnement (c'est le contrat de la
    # sortie « standalone » de Next), `next start` sur sa ligne de commande. On pose les
    # deux : l'URL de l'API, elle, n'a d'effet qu'au rendu serveur — celle du navigateur
    # est figée dans le build, et c'est la raison pour laquelle le port de l'API ne se
    # déplace pas (voir `_port_api`).
    environ_front = dict(environ)
    environ_front["PORT"] = str(port_ui)
    environ_front["HOSTNAME"] = options.hote
    environ_front["NEXT_PUBLIC_MAESTRO_API_URL"] = url_api
    if front.forme == "standalone":
        argv_ui = [node, str(front.cible)]
        dossier_ui = front.cible.parent
    else:
        argv_ui = [
            node,
            str(front.cible),
            "start",
            "--port",
            str(port_ui),
            "--hostname",
            options.hote,
        ]
        dossier_ui = front.dossier or emplacement.racine
    journal_ui = emplacement.journal("ui")
    sortie.dire(
        f"[front] démarrage sur :{port_ui} — {front.forme} "
        f"(journal : {emplacement.relatif(journal_ui)})"
    )
    process_ui = systeme.demarrer(
        argv_ui,
        cwd=dossier_ui,
        journal=journal_ui,
        environ=environ_front,
    )
    service_ui = Service(
        nom="ui",
        pid=process_ui.pid,
        hote=options.hote,
        port=port_ui,
        marqueur=str(front.cible),
        journal=emplacement.relatif(journal_ui),
        sonde=url_ui,
    )
    inscrite = inscrite.avec(service_ui)
    etat_session.ecrire(emplacement.session, inscrite)

    echec = _attendre(
        service_ui,
        process_ui,
        options.delai_ui,
        emplacement=emplacement,
        systeme=systeme,
    )
    if echec:
        sortie.alerter(echec)
        _defaire(inscrite, emplacement, systeme, sortie)
        return 1

    if options.navigateur:
        if systeme.ouvrir_navigateur(url_ui):
            sortie.dire(f"[navigateur] {url_ui} ouvert dans le navigateur du poste")
        else:
            sortie.dire(f"[navigateur] ouverture impossible — ouvrir {url_ui} à la main")

    sortie.dire("")
    sortie.dire(f"Maestro est prêt : {url_ui}")
    sortie.dire(f"  API : {url_api}  ·  journaux : {emplacement.relatif(emplacement.journaux)}/")
    sortie.dire("  arrêt : python -m maestro.lanceur --stop")
    if _extinction_desactivee(environ):
        sortie.dire(
            "  ⚠ runs en vol : MAESTRO_EXTINCTION=0 — l'arrêt ne les soldera PAS, "
            "ils continueront sans écran pour les suivre"
        )
    else:
        sortie.dire(
            "  runs en vol : soldés par l'arrêt, reprenables au redémarrage "
            "(MAESTRO_EXTINCTION=0 pour laisser tourner)"
        )
    return 0


def arreter(
    options: Options,
    *,
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
) -> int:
    """Arrête la stack inscrite : ses runs soldés, ses services éteints, et vérifiés."""
    inscrite = etat_session.lire(emplacement.session)
    if inscrite is None:
        sortie.dire("Aucune session du lanceur à arrêter.")
        _signaler_les_intrus(options, systeme, sortie)
        return 3

    service_api = inscrite.service("api")
    if service_api is not None:
        _solder_les_runs(service_api, systeme, sortie, environ)

    complet = True
    # Le front d'abord : personne ne frappe plus à une API qu'on est en train
    # d'éteindre, et l'ordre inverse ferait rendre des erreurs à un écran ouvert.
    for nom in ("ui", "api"):
        service = inscrite.service(nom)
        if service is None:
            continue
        complet = _eteindre_service(service, systeme, sortie) and complet

    etat_session.effacer(emplacement.session)
    if complet:
        sortie.dire("Maestro est arrêté.")
        return 0
    sortie.alerter("Arrêt incomplet — voir les lignes ci-dessus.")
    return 1


def etat(
    options: Options,
    *,
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
) -> int:
    """Dit ce qui tourne — et, pour ce qui est mort, **sa cause** (3e critère)."""
    inscrite = etat_session.lire(emplacement.session)
    if inscrite is None:
        sortie.dire("Aucune session du lanceur inscrite.")
        return 3

    tout_va_bien = True
    for service in inscrite.services:
        journal = emplacement.racine / service.journal
        if not systeme.vivant(service.pid):
            tout_va_bien = False
            sortie.dire(
                f"[{service.nom}] arrêté (pid {service.pid}) — "
                f"{cause(journal, affiche=service.journal)}"
            )
            continue
        if systeme.sonder(service.sonde, 2.0):
            sortie.dire(f"[{service.nom}] en marche sur :{service.port} (pid {service.pid})")
            continue
        tout_va_bien = False
        sortie.dire(
            f"[{service.nom}] vivant (pid {service.pid}) mais muet sur :{service.port} — "
            f"{cause(journal, affiche=service.journal)}"
        )
    sortie.dire(f"  interface : {inscrite.url} (démarrée {_depuis(inscrite.demarre_a)})")
    return 0 if tout_va_bien else 1


def diagnostic(
    options: Options,
    *,
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
) -> int:
    """Ce que le lanceur servirait, et avec quoi — sans rien démarrer ni arrêter."""
    front = emplacement.front
    sortie.dire(f"racine   : {emplacement.racine}")
    sortie.dire(f"front    : {front.forme} — {front.cible or front.manque}")
    sortie.dire(f"node     : {emplacement.node or emplacement.manque_node}")
    sortie.dire(f"python   : {sys.executable}")
    for nom, port in (("api", options.port_api), ("ui", options.port_ui)):
        tenu = "occupé" if systeme.port_tenu(options.hote, port) else "libre"
        sortie.dire(f"port {nom:<4}: {port} ({tenu})")
    sortie.dire(f"journaux : {emplacement.relatif(emplacement.journaux)}/")
    inscrite = etat_session.lire(emplacement.session)
    if inscrite is None:
        sortie.dire("session  : aucune")
    else:
        pids = ", ".join(f"{s.nom}={s.pid}" for s in inscrite.services)
        sortie.dire(f"session  : {inscrite.url} ({pids})")
    return 0 if front.servable and emplacement.node else 1


def cause(
    journal: Path,
    *,
    affiche: str | None = None,
    lignes: int = LIGNES_CAUSE,
    longueur: int = LONGUEUR_CAUSE,
) -> str:
    """La cause d'une mort, tirée des **dernières** lignes du journal.

    Les dernières, parce que c'est là qu'une trace nomme sa panne. Le chemin voyage
    avec : cinq lignes ne suffisent pas toujours, et « allez voir là » est ce qui
    manque alors. Même forme que `maestro.controltower.hote_detache._cause`, pour que
    ce qu'on lit d'un run mort et ce qu'on lit d'un service mort se ressemblent.

    `affiche` est le chemin **tel qu'on le dit** — relatif à la racine du produit
    (docs/10 §8.5), pendant qu'on lit le fichier par son chemin réel.
    """
    dit = affiche if affiche is not None else str(journal)
    try:
        texte = journal.read_text(encoding="utf-8", errors="replace")
    except OSError as erreur:
        return f"journal illisible ({dit}) : {erreur}"
    utiles = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    if not utiles:
        return f"rien écrit (journal vide : {dit})"
    extrait = " | ".join(utiles[-lignes:])
    if len(extrait) > longueur:
        extrait = f"…{extrait[-longueur:]}"
    return f"{extrait} (journal : {dit})"


# ------------------------------------------------------------------ les ports


def _port_api(options: Options, systeme: Systeme, sortie: Sortie) -> int | None:
    """Le port de l'API : celui qu'on demande, ou rien — **jamais** un autre.

    Il ne se déplace pas tout seul, et ce n'est pas une facilité qu'on s'est refusée :
    l'URL de l'API est **inlinée dans le front au build** (`NEXT_PUBLIC_MAESTRO_API_URL`,
    `apps/web/lib/api.ts`). Servir l'API ailleurs sans reconstruire le front ouvrirait
    une interface qui se charge et ne répond à rien — la « collision silencieuse » que
    le critère interdit. Pris, il est donc **signalé**, avec ce qui le lève.
    """
    if not systeme.port_tenu(options.hote, options.port_api):
        return options.port_api
    sortie.alerter(f"Le port de l'API (:{options.port_api}) est déjà tenu.")
    sortie.alerter(
        "  Il ne peut pas être déplacé en silence : le front porte l'URL de l'API, "
        "figée à sa construction."
    )
    sortie.alerter("  · libérer ce port (arrêter ce qui l'occupe), puis relancer ;")
    sortie.alerter(
        "  · ou le choisir ailleurs — python -m maestro.lanceur --port-api <port> —, "
        "le front devant être construit pour cette URL."
    )
    return None


def _port_ui(options: Options, systeme: Systeme, sortie: Sortie) -> int | None:
    """Le port du front : celui qu'on demande, sinon le premier libre au-dessus.

    Déplaçable, lui, parce que rien n'en dépend : c'est l'adresse qu'on vient
    d'annoncer qu'on ouvre. Le déplacement est **dit**, jamais subi.
    """
    if not systeme.port_tenu(options.hote, options.port_ui):
        return options.port_ui
    choisi = systeme.port_libre_depuis(options.hote, options.port_ui + 1)
    if choisi is None:
        return None
    sortie.dire(f"[port] :{options.port_ui} est pris — le front écoute sur :{choisi}")
    return choisi


# --------------------------------------------------------------- le démarrage


def _remplacer_session(
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
    options: Options,
) -> None:
    """Range la session précédente avant d'en monter une neuve.

    ⚠ **Sans solder ses runs.** La ligne de partage passe entre *arrêter* et
    *remplacer la session en place* (#441, #700, docs/28 §11) : un run survit à son
    API et la relance le retrouve, alors qu'un soldage ici en ferait le prix du geste
    le plus fréquent qui soit. Le soldage vit dans `arreter`, et là seulement.
    """
    inscrite = etat_session.lire(emplacement.session)
    if inscrite is None:
        return
    sortie.dire("[session] une stack du lanceur est déjà inscrite — elle est remplacée")
    for nom in ("ui", "api"):
        service = inscrite.service(nom)
        if service is not None:
            _eteindre_service(service, systeme, sortie)
    etat_session.effacer(emplacement.session)
    _attendre_liberation(inscrite, systeme, sortie)


def _attendre_liberation(inscrite: Session, systeme: Systeme, sortie: Sortie) -> None:
    """Laisse les ports de la session remplacée se libérer avant de les redemander.

    Un serveur qui vient de mourir tient encore son port une poignée de secondes ; sans
    cette attente, le remplacement d'une session buterait sur sa propre trace et
    signalerait une collision qui n'en est pas une.
    """
    debut = systeme.horloge()
    while systeme.horloge() - debut < DELAI_LIBERATION:
        if not any(systeme.port_tenu(s.hote, s.port) for s in inscrite.services):
            return
        systeme.dormir(0.25)
    sortie.dire("[session] les ports de la session précédente ne se libèrent pas")


def _attendre(
    service: Service,
    processus: Processus,
    delai: float,
    *,
    emplacement: Emplacement,
    systeme: Systeme,
) -> str:
    """Attend que le service réponde. Rend `""` s'il répond, **sa cause** sinon.

    La mort est regardée **avant** la sonde, et c'est tout l'intérêt : un service mort
    ne répondra jamais, et attendre le délai entier pour l'annoncer volerait à
    l'utilisateur le seul moment où la cause est fraîche.
    """
    journal = emplacement.racine / service.journal
    debut = systeme.horloge()
    while systeme.horloge() - debut < delai:
        code = processus.code()
        if code is not None:
            return (
                f"[{service.nom}] s'est arrêté au démarrage (code {code}) — "
                f"{cause(journal, affiche=service.journal)}"
            )
        if systeme.sonder(service.sonde, 2.0):
            return ""
        systeme.dormir(0.5)
    return (
        f"[{service.nom}] n'a pas répondu sur :{service.port} en {delai:.0f} s — "
        f"{cause(journal, affiche=service.journal)}"
    )


def _defaire(
    inscrite: Session,
    emplacement: Emplacement,
    systeme: Systeme,
    sortie: Sortie,
) -> None:
    """Défait un démarrage raté — c'est le cas « un des deux services a échoué ».

    Aucun témoin à confronter ici, contrairement à `_eteindre_service` : ces process
    viennent d'être lancés par **nous**, leurs pids ne peuvent pas avoir été recyclés,
    et laisser une API vivante derrière un front mort serait exactement l'orphelin que
    le critère interdit.
    """
    for nom in ("ui", "api"):
        service = inscrite.service(nom)
        if service is None:
            continue
        sortie.dire(f"[arrêt] {service.nom} (pid {service.pid}) — démarrage défait")
        systeme.eteindre(service.pid)
    etat_session.effacer(emplacement.session)


# ------------------------------------------------------------------- l'arrêt


def _eteindre_service(service: Service, systeme: Systeme, sortie: Sortie) -> bool:
    """Éteint un service inscrit, **après l'avoir reconnu**. `False` si l'arrêt n'est pas prouvé.

    Trois témoins, et la règle est celle du dépôt (#456, #213) : *un témoin concordant
    reconnaît le process, un témoin divergent conclut au recyclage, un témoin illisible
    fait s'abstenir*.

    1. **vivant ?** Non : il n'y a rien à faire, et c'est un arrêt propre.
    2. **sa ligne de commande porte-t-elle son marqueur ?** Non : ce pid a été recyclé,
       il appartient à quelqu'un d'autre — on ne le touche pas.
    3. **illisible ?** On se rabat sur le port : tenu, c'est bien lui ; libre, on
       s'abstient et on le dit — un arrêt qu'on ne peut pas prouver ne se raconte pas
       comme s'il avait eu lieu.
    """
    if not systeme.vivant(service.pid):
        sortie.dire(f"[arrêt] {service.nom} déjà arrêté (pid {service.pid})")
        if systeme.port_tenu(service.hote, service.port):
            sortie.dire(
                f"[arrêt] ⚠ :{service.port} reste tenu par un autre process — "
                "rien n'est tué au jugé"
            )
        return True

    ligne = systeme.ligne_de_commande(service.pid)
    if ligne is not None:
        if service.marqueur.lower() not in ligne.lower():
            sortie.dire(
                f"[arrêt] le pid {service.pid} n'est plus {service.nom} "
                "(pid recyclé) — rien n'est tué au jugé"
            )
            return True
    elif not systeme.port_tenu(service.hote, service.port):
        sortie.alerter(
            f"[arrêt] {service.nom} (pid {service.pid}) : ni ligne de commande lisible, "
            f"ni :{service.port} tenu — abstention, à vérifier à la main"
        )
        return False

    systeme.eteindre(service.pid)
    debut = systeme.horloge()
    while systeme.horloge() - debut < DELAI_MORT:
        if not systeme.vivant(service.pid):
            sortie.dire(f"[arrêt] {service.nom} arrêté avec sa descendance (pid {service.pid})")
            return True
        systeme.dormir(0.25)
    sortie.alerter(
        f"[arrêt] {service.nom} (pid {service.pid}) résiste à l'extinction — "
        "le terminer à la main"
    )
    return False


def _solder_les_runs(
    service: Service,
    systeme: Systeme,
    sortie: Sortie,
    environ: dict[str, str],
) -> None:
    """Solde les runs en vol — l'arrêt **volontaire**, et lui seul (#486, #700).

    Appelé avant qu'on touche à l'API : c'est elle qui tient les hôtes détachés et sait
    les éteindre avec leur descendance. Best-effort de bout en bout — une API muette
    n'empêche pas d'arrêter le reste —, mais **jamais silencieux** : un run qui
    continue sans écran pour le suivre est précisément ce que ce geste supprime, et le
    taire ferait chercher plus tard d'où vient la dépense.
    """
    if _extinction_desactivee(environ):
        sortie.dire(
            "[extinction] désactivée (MAESTRO_EXTINCTION=0) — les runs en vol "
            "continuent sans écran pour les suivre"
        )
        return
    url = f"http://{service.hote}:{service.port}/api/extinction"
    reponse = systeme.poster(url, _delai_extinction(environ))
    if reponse is None:
        sortie.dire(
            f"[extinction] l'API ne répond pas sur :{service.port} — rien à solder par ici"
        )
        return
    runs = _runs_soldes(reponse)
    if not runs:
        sortie.dire("[extinction] aucun run en vol")
        return
    for run in runs:
        sortie.dire(
            f"[extinction] run {run} interrompu — reprenable au redémarrage "
            "(bouton « Reprendre »)"
        )


def _runs_soldes(reponse: str) -> tuple[str, ...]:
    """Les identifiants des runs soldés, lus dans la réponse de l'API.

    Du JSON lu comme du JSON : le lanceur est en Python, là où `start.sh` devait
    extraire ces identifiants au `grep` faute de `jq`. Une réponse inattendue rend un
    tuple vide — on ne fabrique pas d'identifiant à partir de ce qu'on n'a pas compris.
    """
    try:
        charge: Any = json.loads(reponse)
    except ValueError:
        return ()
    if not isinstance(charge, dict):
        return ()
    runs = charge.get("runs")
    if not isinstance(runs, list):
        return ()
    trouves = []
    for entree in runs:
        if isinstance(entree, dict) and isinstance(entree.get("run_id"), str):
            trouves.append(entree["run_id"])
    return tuple(trouves)


def _signaler_les_intrus(options: Options, systeme: Systeme, sortie: Sortie) -> None:
    """Sans session inscrite, dit ce qui occupe quand même les ports du produit.

    Le lanceur n'arrête que ce qu'il a démarré — le reste, il le **nomme**. C'est la
    différence entre un arrêt et un ménage : `scripts/controltower/start.sh` libère les
    ports de qui s'y trouve, ce qu'un outil de développement peut se permettre et un
    produit non.
    """
    occupes = [
        f":{port} ({nom})"
        for nom, port in (("API", options.port_api), ("front", options.port_ui))
        if systeme.port_tenu(options.hote, port)
    ]
    if not occupes:
        return
    # Ce message s'adresse à qui **utilise** Maestro : il nomme ce qu'on voit (des ports
    # tenus) et ce qu'on peut faire (aller l'arrêter là où il a été lancé), jamais une
    # commande du dépôt — G6 du retex du 2026-09-11, gardé par
    # `tests/test_registre_de_langue.py`.
    sortie.dire(
        "  ⚠ ces ports sont pourtant tenus : "
        + ", ".join(occupes)
        + " — par une application que ce lanceur n'a pas démarrée"
    )
    sortie.dire("    (l'arrêter là où elle a été lancée, puis relancer Maestro)")


def _depuis(horodatage: float) -> str:
    """L'heure de démarrage, dite comme on la lit — ou « heure inconnue » si elle ne l'est pas."""
    try:
        return datetime.fromtimestamp(horodatage).strftime("le %d/%m/%Y à %H:%M")
    except (OSError, OverflowError, ValueError):
        return "à une heure inconnue"


def _extinction_desactivee(environ: dict[str, str]) -> bool:
    return environ.get("MAESTRO_EXTINCTION", "1") == "0"


def _delai_extinction(environ: dict[str, str]) -> float:
    try:
        return float(environ.get("MAESTRO_EXTINCTION_DELAI", "") or DELAI_EXTINCTION)
    except ValueError:
        return DELAI_EXTINCTION
