"""Capacité des agents : activer/désactiver et plafond d'instances (ticket #86, EF-21).

Matérialise les champs `actif` et `instances_max` de l'entité AGENT (docs/03 §2) :
la **capacité** d'un agent — reçoit-il des tâches, et combien en même temps — se
règle depuis la Control Tower et a un **effet réel** sur l'exécution :

- un agent **désactivé** est écarté des candidats du routage
  (`maestro.router.Router.route`, paramètre `exclus`) : il ne reçoit plus de
  tâches, ni par auto-assignation ni par réassignation manuelle ;
- le nombre d'**instances** plafonne ses exécutions simultanées : la
  `JaugeInstances` retient une tâche routée vers un agent au complet jusqu'à ce
  qu'un créneau se libère.

Le réglage est **persisté hors du code** (`CapacityStore`, un fichier JSON par
agent — même pattern que `maestro.agents.store`) et relu **à chaud** à chaque
tâche, comme les playbooks (#78) : un réglage posé depuis l'UI vaut pour la
tâche suivante, sans redémarrage. Au POC le dépôt est sur fichiers
(`core/capacite/`, ou `MAESTRO_CAPACITE_DIR`) ; en V1 il passera en base
(champs `actif`/`instances_max` de la table AGENT) sans changer ce contrat.

Limite POC assumée : la jauge borne les exécutions simultanées **par process**
(par boucle asyncio) — exacte pour le moteur en process, elle ne coordonne pas
encore plusieurs workers entre eux (la coordination distribuée viendra avec la
persistance partagée, EF-16).

## Le plafond d'un agent que personne n'a réglé se dérive du plan (#1299)

`INSTANCES_DEFAUT = 1` a longtemps été le plafond de tout agent jamais réglé : un
défaut arbitraire au sens de docs/41, qui faisait passer en file quatre tâches
indépendantes du même rôle — le retex du 2026-09-24 (*« je n'ai jamais remarqué un
parallélisme »*). Sur un projet **versionné**, où chaque tâche travaille dans son
propre worktree, le plafond d'un tel agent est désormais **dérivé de la largeur du
plan** (`InstancesDerivees`), borné par un plafond global
(`PLAFOND_INSTANCES_DERIVEES`), et annoncé au journal du run avec son origine.

Deux choses ne bougent pas. Un **réglage explicite** de la personne l'emporte
toujours (`CapaciteAgent.fixe_ses_instances`) ; et un projet **non versionné**
garde une tâche à la fois (#839), parce que deux agents y écriraient dans le même
dossier.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from weakref import WeakKeyDictionary

from maestro.agents.rangement import RangeParProjet
from maestro.config import Settings, load_settings

#: Plafond d'instances par défaut : un agent = une exécution à la fois (docs/09 :
#: on *augmente* les instances pour absorber la charge). Depuis #1299, c'est le
#: plafond d'un agent jamais réglé **hors** d'un run qui en dérive un autre — sur un
#: projet versionné, le run le remplace par `InstancesDerivees`.
INSTANCES_DEFAUT = 1

#: Le plafond global des instances qu'un run **dérive de son plan** (#1299). Une
#: décision, pas une mesure — la même que celle de la concurrence d'un run
#: d'outillage (#626) et du plafond qu'une proposition d'équipe pose
#: (`maestro.equipe.gabarits.INSTANCES_MAX_PROPOSEES`) : trois. La seule mesure qui
#: existe est celle d'une concurrence de **deux** (démo V1, #88 : livrables compacts,
#: −43 % de coût), et elle ne dit rien de trois ; ce chiffre ne s'appuie donc sur
#: aucune. Il borne ce que Maestro ouvre de lui-même : la personne en règle un autre
#: agent par agent (écran de capacité, jusqu'à ce qu'elle veut) ou run par run
#: (`parallelisme`, #100), et les deux l'emportent.
PLAFOND_INSTANCES_DERIVEES = 3

#: Le statut de la ligne de journal qui annonce le plafond dérivé d'un run (#1299) —
#: une étape de run `equipe`, l'équipe confrontée au plan (`maestro.engine.loop`).
STATUT_INSTANCES_DERIVEES = "instances_derivees"

#: D'où vient le plafond dérivé : la largeur du plan, ou le plafond global qui la borne.
ORIGINE_LARGEUR = "largeur_du_plan"
ORIGINE_PLAFOND_GLOBAL = "plafond_global"

#: Nom d'agent admissible comme fichier de stockage — même verrou que
#: `maestro.agents.store` : slug sûr, sans séparateur ni point.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


@dataclass(frozen=True)
class CapaciteAgent:
    """Le réglage de capacité d'un agent : reçoit-il des tâches, et combien à la fois.

    Miroir des champs `actif`/`instances_max` de l'entité AGENT (docs/03).
    `modifie_le` est posé par le dépôt à l'écriture (ISO 8601, UTC) — vide pour
    une capacité jamais réglée (les défauts du code).

    `instances_fixees` (#1299) dit si le nombre d'instances est un **choix** — celui
    que la personne pose à l'écran de capacité — ou seulement la valeur par défaut,
    que le run remplace alors par un plafond dérivé de son plan. `None` : on ne l'a
    pas dit, et la valeur en décide (`fixe_ses_instances`).
    """

    nom: str
    actif: bool = True
    instances: int = INSTANCES_DEFAUT
    modifie_le: str = ""
    instances_fixees: bool | None = None

    @property
    def fixe_ses_instances(self) -> bool:
        """Le nombre d'instances est-il un réglage explicite, que le run doit garder (#1299) ?

        Dit, il fait foi. Tu, la **valeur** en décide : une instance était le défaut
        partout — celui de la création d'équipe comme celui d'un agent qu'on a
        seulement désactivé puis réactivé —, donc l'avoir écrite ne disait rien d'un
        choix, et c'est précisément ce défaut que #1299 remplace ; toute autre valeur
        a été choisie, à l'écran de capacité ou avec l'équipe validée, et reste
        fixée. C'est aussi la lecture des fichiers écrits avant #1299, qui ne
        portent pas le drapeau.
        """
        if self.instances_fixees is not None:
            return self.instances_fixees
        return self.instances != INSTANCES_DEFAUT

    def to_dict(self) -> dict[str, Any]:
        """Réémet la capacité en dict JSON-sérialisable (le fichier stocké).

        Le drapeau sort **tranché** (`fixe_ses_instances`) : un fichier écrit depuis
        #1299 dit ce qu'il veut dire, sans renvoyer son lecteur à la règle de valeur.
        """
        return {
            "nom": self.nom,
            "actif": self.actif,
            "instances": self.instances,
            "instances_fixees": self.fixe_ses_instances,
            "modifie_le": self.modifie_le,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapaciteAgent:
        """Reconstruit une capacité depuis sa forme `to_dict` (le fichier stocké)."""
        fixees = data.get("instances_fixees")
        return cls(
            nom=data["nom"],
            actif=data.get("actif", True),
            instances=data.get("instances", INSTANCES_DEFAUT),
            modifie_le=data.get("modifie_le", ""),
            instances_fixees=fixees if isinstance(fixees, bool) else None,
        )


@dataclass(frozen=True)
class InstancesDerivees:
    """Le plafond d'instances qu'un run **dérive de son plan**, et d'où il vient (#1299).

    `largeur` est ce que le plan laisse partir de front (`maestro.plan_run.largeur_du_plan`,
    la même mesure que « jusqu'à N de front » à l'écran). Le plafond qui en sort vaut
    pour chaque agent du run **qui n'a pas fixé ses instances** : un agent réglé à la
    main garde son réglage (`reglees`, nommés dans l'annonce pour qu'une file qu'ils
    imposent ne reste pas inexpliquée).

    Pourquoi la largeur et pas le nombre de tâches qu'un agent recevra : le routage se
    décide tâche par tâche, au démarrage de chacune (#42), donc prédire qu'un agent
    prendra trois tâches d'un même niveau serait deviner. La largeur, elle, est un
    fait du plan, figé au départ — et un agent ne peut jamais avoir plus de tâches de
    front que le plan n'en laisse partir.
    """

    largeur: int
    plafond_global: int = PLAFOND_INSTANCES_DERIVEES
    reglees: tuple[CapaciteAgent, ...] = ()

    @property
    def instances(self) -> int:
        """Le plafond appliqué : la largeur, bornée par le plafond global — jamais moins d'un."""
        return max(INSTANCES_DEFAUT, min(self.largeur, self.plafond_global))

    @property
    def origine(self) -> str:
        """`ORIGINE_PLAFOND_GLOBAL` quand le plafond global a borné la largeur, sinon la largeur."""
        return ORIGINE_PLAFOND_GLOBAL if self.largeur > self.plafond_global else ORIGINE_LARGEUR

    def phrase(self, *, parallelisme: int | None = None) -> str:
        """L'annonce du journal : le chiffre, son origine, et ce qui le borne encore.

        `parallelisme` est le plafond **du run** (#100), tous agents confondus : posé,
        il borne en dessous de tout le reste, et l'annonce le dit.
        """
        if self.instances <= INSTANCES_DEFAUT:
            texte = (
                "Une tâche à la fois par agent : le plan n'en laisse partir qu'une de "
                "front, chaque tâche attendant celle qui la précède."
            )
        elif self.origine == ORIGINE_PLAFOND_GLOBAL:
            texte = (
                f"Jusqu'à {self.instances} tâches de front par agent : le plan en laisse "
                f"partir jusqu'à {self.largeur} de front, et le plafond global des "
                f"instances qu'un run ouvre de lui-même est de {self.plafond_global}."
            )
        else:
            texte = (
                f"Jusqu'à {self.instances} tâches de front par agent : c'est la largeur "
                f"du plan, {self.largeur} tâches indépendantes au même niveau. Chacune "
                "travaille dans sa copie du projet."
            )
        if self.reglees:
            gardes = ", ".join(
                f"« {c.nom} » {c.instances} instance{'s' if c.instances > 1 else ''}"
                for c in self.reglees
            )
            texte += f" Réglés à la main, et gardés : {gardes}."
        if parallelisme is not None:
            tache = "tâche" if parallelisme <= 1 else "tâches"
            texte += (
                f" Le run est borné à {parallelisme} {tache} à la fois, tous agents "
                "confondus."
            )
        return texte


class CapacityStore(RangeParProjet):
    """Dépôt des capacités d'agents, sur fichiers (`<racine>/<nom>.json`).

    Un fichier par agent, écrit atomiquement ; un agent sans fichier a la
    capacité **par défaut** (actif, une instance) — `lire` ne rend jamais None.
    Un seul écrivain à la fois au POC (l'API Control Tower) : le dépôt ne porte
    pas de verrou de concurrence. Les lecteurs (moteur, workers) relisent à
    chaque tâche — l'application est à chaud, comme les playbooks (#78).

    Cadré sur un projet (#1038), il **recouvre** le gabarit : un agent que le
    projet n'a pas réglé garde le réglage du gabarit, et un agent **désactivé**
    au gabarit le reste. Retomber sur « actif, une instance » serait ici un
    garde-fou qui saute, pas un défaut.
    """

    @classmethod
    def default(cls, settings: Settings | None = None) -> CapacityStore:
        """Le dépôt configuré : `MAESTRO_CAPACITE_DIR`, sinon `core/capacite/` du dépôt."""
        settings = settings or load_settings()
        if settings.capacite_dir:
            return cls(Path(settings.capacite_dir))
        return cls(Path(__file__).resolve().parents[2] / "core" / "capacite")

    def lire(self, nom: str) -> CapaciteAgent:
        """La capacité de `nom` — celle du gabarit, sinon celle par défaut, à défaut."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            if self._gabarits is not None:
                return self._gabarits.lire(nom)
            return CapaciteAgent(nom=nom)
        capacite = CapaciteAgent.from_dict(json.loads(chemin.read_text(encoding="utf-8")))
        # Le nom fait foi côté fichier, comme pour les définitions d'agents.
        return replace(capacite, nom=nom)

    def lister(self) -> tuple[CapaciteAgent, ...]:
        """Les capacités **réglées**, par nom — celles du projet par-dessus celles du gabarit.

        L'union, et non le remplacement : c'est d'elle que sortent `inactifs()`
        et l'état initial de la Control Tower, donc la seule façon qu'un réglage
        hérité pèse encore sur le routage.
        """
        reglees = {capacite.nom: capacite for capacite in self._stockees()}
        if self._gabarits is not None:
            heritees = {c.nom: c for c in self._gabarits.lister()}
            heritees.update(reglees)
            reglees = heritees
        return tuple(reglees[nom] for nom in sorted(reglees))

    def _stockees(self) -> tuple[CapaciteAgent, ...]:
        """Les capacités réglées **dans ce dépôt-ci**, sans rien hériter."""
        if not self._racine.is_dir():
            return ()
        return tuple(
            self.lire(chemin.stem)
            for chemin in sorted(self._racine.glob("*.json"))
            if _NOM_AGENT.match(chemin.stem)
        )

    def ecrire(self, capacite: CapaciteAgent) -> CapaciteAgent:
        """Persiste `capacite` (remplacement intégral) et la renvoie datée.

        Écriture atomique (fichier temporaire puis renommage). Lève `ValueError`
        si le réglage est invalide (nom hors slug, instances < 1) — le dépôt ne
        stocke jamais une capacité inapplicable.
        """
        if capacite.instances < 1:
            raise ValueError(
                f"instances doit être ≥ 1 (reçu : {capacite.instances}) — pour ne plus "
                "recevoir de tâches, désactiver l'agent."
            )
        chemin = self._chemin(capacite.nom)
        propre = replace(capacite, modifie_le=_maintenant())
        self._racine.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(".json.tmp")
        temporaire.write_text(
            json.dumps(propre.to_dict(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propre

    def supprimer(self, nom: str) -> bool:
        """Retire le réglage de `nom` (retour aux défauts) ; False s'il n'y était pas."""
        chemin = self._chemin(nom)
        if not chemin.is_file():
            return False
        chemin.unlink()
        return True

    def inactifs(self) -> frozenset[str]:
        """Les noms des agents désactivés — les exclus du routage, relus à chaque tâche."""
        return frozenset(c.nom for c in self.lister() if not c.actif)

    def _chemin(self, nom: str) -> Path:
        """Le fichier de stockage de `nom`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(nom):
            raise ValueError(f"nom d'agent invalide : {nom!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{nom}.json"


class JaugeInstances:
    """Borne les exécutions simultanées d'un agent à son plafond d'instances.

    `creneau(nom, plafond)` s'utilise en contexte : l'entrée attend qu'un
    créneau se libère si l'agent est au complet, la sortie le rend et réveille
    les tâches en attente. `plafond` est un rappel (et pas un entier) pour que
    chaque prise — réveils compris — relise le réglage courant : une capacité
    augmentée depuis l'UI s'applique au prochain créneau disputé, sans
    redémarrage.

    La comptabilité est **par boucle asyncio** : exacte pour le moteur en
    process (toutes les tâches d'un run partagent la boucle), inerte côté
    worker Celery (une boucle par message — la coordination inter-process
    viendra avec la persistance partagée, cf. docstring du module).
    """

    def __init__(self) -> None:
        self._boucles: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[asyncio.Condition, dict[str, int]]
        ] = WeakKeyDictionary()

    @asynccontextmanager
    async def creneau(
        self,
        nom: str,
        plafond: Callable[[], int],
        *,
        on_attente: Callable[[int], None] | None = None,
    ) -> AsyncIterator[None]:
        """Occupe un créneau d'exécution de l'agent `nom` le temps du bloc.

        `on_attente` (#1298) est prévenu, **une fois**, quand la tâche doit attendre
        — l'agent est au complet —, avec le plafond qui la retient. C'est l'instant
        où le moteur sait qu'une instance fait passer des tâches une à une ; une
        tâche servie tout de suite ne le prévient pas.
        """
        condition, en_vol = self._etat_de_la_boucle()
        async with condition:
            prevenu = False
            while en_vol.get(nom, 0) >= (borne := max(1, plafond())):
                if on_attente is not None and not prevenu:
                    prevenu = True
                    on_attente(borne)
                await condition.wait()
            en_vol[nom] = en_vol.get(nom, 0) + 1
        try:
            yield
        finally:
            async with condition:
                en_vol[nom] -= 1
                condition.notify_all()

    def _etat_de_la_boucle(self) -> tuple[asyncio.Condition, dict[str, int]]:
        """La condition et les compteurs de la boucle courante (créés au premier usage).

        Une `asyncio.Condition` ne s'attend que depuis sa boucle de création :
        l'état est donc tenu **par boucle** (clé faible — l'état d'une boucle
        disparaît avec elle). Un exécuteur partagé entre boucles (worker à
        threads) compte ainsi chaque boucle séparément, sans jamais se bloquer
        sur la condition d'une autre.
        """
        boucle = asyncio.get_running_loop()
        etat = self._boucles.get(boucle)
        if etat is None:
            etat = (asyncio.Condition(), {})
            self._boucles[boucle] = etat
        return etat


def _maintenant() -> str:
    """L'horodatage d'écriture d'un réglage (ISO 8601, UTC, à la seconde)."""
    return datetime.now(tz=UTC).isoformat(timespec="seconds")
