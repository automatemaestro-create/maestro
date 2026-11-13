"""Le fournisseur par défaut, côté Control Tower : le lire, l'éprouver, l'enregistrer (#1609).

Les Paramètres montrent chaque accès que Maestro sait emprunter, et ce que la
sonde du poste en a vu (`GET /api/fournisseurs`). Ce service sert le reste :

- **l'état** : le choix enregistré, ce que l'exécution utilise **maintenant**
  (qui peut différer du choix quand l'environnement l'impose), les variables de
  l'environnement qui l'imposent, et les clés gardées dans le coffre du poste ;
- **l'épreuve** : un tour de modèle sur le choix saisi, avant tout
  enregistrement, et son verdict typé (`maestro.providers.base.Epreuve`) ;
- **l'enregistrement** : seulement d'un choix éprouvé avec succès. Une épreuve
  réussie est retenue un moment par son empreinte, pour que « Éprouver » puis
  « Enregistrer » ne paient pas deux appels de modèle.

Une clé ne ressort jamais d'ici : ni dans l'état, qui ne dit que sa présence, ni
dans une erreur. Elle va au coffre du poste, chiffrée, et le registre de
rédaction la retire de tout journal dès qu'elle est lue.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from maestro.config import Settings
from maestro.fournisseur_par_defaut import (
    VARIABLE_FOURNISSEUR,
    VARIABLE_MODELE,
    DepotFournisseurParDefaut,
    FournisseurParDefaut,
    environnement_du_poste,
    imposees,
)
from maestro.telemetry.redact import register_secret

if TYPE_CHECKING:  # typage seul — la couche fournisseur tire le SDK, payé au premier appel
    from maestro.agents.secrets import SecretStore
    from maestro.providers.base import AccesFournisseur, Epreuve, ModelProvider

#: Ce qui éprouve un choix : des réglages explicites et un modèle, un verdict.
Eprouveur = Callable[[Settings, str], Awaitable["Epreuve"]]

#: Combien de temps une épreuve réussie vaut pour l'enregistrement du même choix.
DUREE_EPREUVE_S = 15 * 60


@dataclass(frozen=True)
class DemandeFournisseur:
    """Un choix saisi à l'écran. `cle` n'est présente que si la personne vient de la taper."""

    fournisseur: str
    acces: str
    modele: str | None = None
    adresse: str | None = None
    cle: str | None = None


class ChoixRefuse(ValueError):
    """Le choix saisi ne se tient pas (fournisseur ou accès inconnu, modèle ou clé manquant)."""


class EpreuveEchouee(ValueError):
    """On ne peut enregistrer qu'un choix éprouvé avec succès — le verdict voyage avec le refus."""

    def __init__(self, epreuve: Epreuve) -> None:
        super().__init__(f"épreuve en échec : {epreuve.motif}")
        self.epreuve = epreuve


class ServiceFournisseurParDefaut:
    """Lit, éprouve et enregistre le fournisseur par défaut du poste."""

    def __init__(
        self,
        *,
        coffre: SecretStore,
        depot: DepotFournisseurParDefaut | None = None,
        eprouveur: Eprouveur | None = None,
        environ: Callable[[], Mapping[str, str]] | None = None,
        horloge: Callable[[], float] = time.monotonic,
    ) -> None:
        self._coffre = coffre
        self._depot = depot
        self._eprouveur = eprouveur
        self._environ = environ or (lambda: os.environ)
        self._horloge = horloge
        self._reussies: dict[str, float] = {}

    # ------------------------------------------------------------------ lecture

    def etat(self) -> dict[str, Any]:
        """Le choix enregistré, ce qui est utilisé maintenant, ce que l'environnement impose."""
        environ = self._environ()
        choix = self._depot_courant(environ).lire()
        return {
            "choix": _choix_public(choix),
            "effectif": self._effectif(environ),
            "environnement": self._environnement(environ),
            "cles": sorted(etat.cle for etat in self._coffre.etat_poste()),
        }

    def _effectif(self, environ: Mapping[str, str]) -> dict[str, Any]:
        """Ce que l'exécution résout maintenant — la lecture de `load_settings`, sur ce dépôt-ci."""
        settings = Settings.from_env(
            environnement_du_poste(
                environ, depot=self._depot_courant(environ), secret=self._secret_du_coffre
            )
        )
        classe = _classes().get(settings.provider)
        return {
            "fournisseur": settings.provider,
            "acces": classe.acces_retenu(settings) if classe is not None else None,
            "modele": settings.model,
            "modele_par_defaut": _modele_par_defaut(settings),
        }

    def _environnement(self, environ: Mapping[str, str]) -> list[dict[str, Any]]:
        """Les variables du choix que l'environnement renseigne — la valeur, sauf pour une clé."""
        secrets = {acces.secret for classe in _classes().values() for acces in classe.ACCES}
        return [
            {"variable": nom, "valeur": None if nom in secrets else environ.get(nom, "").strip()}
            for nom in imposees(_variables_connues(), environ)
        ]

    # ------------------------------------------------------------------ épreuve

    async def eprouver(self, demande: DemandeFournisseur) -> dict[str, Any]:
        """Éprouve le choix saisi et rend le verdict, avec le modèle effectivement interrogé."""
        settings, modele, _ = self._reglages(demande)
        epreuve = await self._eprouver(settings, modele)
        if epreuve.ok:
            self._reussies[_empreinte(demande)] = self._horloge()
        return {"epreuve": epreuve.to_dict(), "modele": modele}

    async def _eprouver(self, settings: Settings, modele: str) -> Epreuve:
        if self._eprouveur is not None:
            return await self._eprouveur(settings, modele)
        from maestro.providers.factory import eprouver_reglages

        return await eprouver_reglages(settings, modele)

    # ------------------------------------------------------------- enregistrement

    async def enregistrer(self, demande: DemandeFournisseur) -> dict[str, Any]:
        """Enregistre le choix, s'il a été éprouvé avec succès — sinon l'éprouve d'abord.

        Lève `EpreuveEchouee` quand l'épreuve échoue : rien n'est écrit, ni le
        choix ni la clé.
        """
        settings, modele, acces = self._reglages(demande)
        empreinte = _empreinte(demande)
        reussie = self._reussies.get(empreinte)
        if reussie is None or self._horloge() - reussie > DUREE_EPREUVE_S:
            epreuve = await self._eprouver(settings, modele)
            if not epreuve.ok:
                raise EpreuveEchouee(epreuve)
        if acces.secret and demande.cle:
            self._coffre.enregistrer_poste(acces.secret, demande.cle)
        self._depot_courant(self._environ()).ecrire(
            FournisseurParDefaut(
                fournisseur=demande.fournisseur,
                acces=acces.id,
                modele=_nettoye(demande.modele),
                adresse=_adresse(demande, acces),
                variables=_variables(demande, acces),
                secret=acces.secret,
                eprouve_le=datetime.now(UTC).isoformat(timespec="seconds"),
            )
        )
        self._reussies.pop(empreinte, None)
        return self.etat()

    def effacer(self) -> dict[str, Any]:
        """Retire le choix du poste : l'environnement seul décide de nouveau."""
        self._depot_courant(self._environ()).effacer()
        return self.etat()

    def retirer_cle(self, variable: str) -> dict[str, Any]:
        """Retire une clé du coffre du poste. Une variable qu'aucun accès ne porte est refusée."""
        secrets = {acces.secret for classe in _classes().values() for acces in classe.ACCES}
        if variable not in secrets:
            raise ChoixRefuse(f"{variable} n'est la clé d'aucun accès.")
        self._coffre.supprimer_poste(variable)
        return self.etat()

    # ---------------------------------------------------------------- interne

    def _reglages(self, demande: DemandeFournisseur) -> tuple[Settings, str, AccesFournisseur]:  # noqa: C901
        """Les réglages qu'aurait l'exécution avec ce choix, le modèle à éprouver, l'accès retenu.

        Ici le choix saisi l'emporte sur l'environnement : on éprouve ce que la
        personne a saisi. La clé est celle qu'elle vient de taper, sinon celle
        du coffre du poste, sinon celle de l'environnement.
        """
        classe = _classes().get(demande.fournisseur)
        if classe is None:
            raise ChoixRefuse(f"Fournisseur inconnu : {demande.fournisseur!r}.")
        acces = next((a for a in classe.ACCES if a.id == demande.acces), None)
        if acces is None:
            raise ChoixRefuse(f"Accès inconnu pour {demande.fournisseur} : {demande.acces!r}.")
        modele = _nettoye(demande.modele)
        if modele is None and not classe.MODELES:
            raise ChoixRefuse("Ce fournisseur demande le nom d'un modèle servi à son adresse.")
        if demande.cle:
            register_secret(demande.cle)
        environ = dict(self._environ())
        for autre in classe.ACCES:
            if autre.secret and autre.secret != acces.secret:
                environ[autre.secret] = ""
        for nom, valeur in _variables(demande, acces):
            environ[nom] = valeur
        if modele is None:
            environ[VARIABLE_MODELE] = ""
        if acces.secret:
            cle = demande.cle or self._coffre.lire_poste().get(acces.secret) or ""
            cle = cle or (environ.get(acces.secret) or "").strip()
            if not cle and acces.cle_requise:
                raise ChoixRefuse("Cet accès demande une clé.")
            environ[acces.secret] = cle
        settings = Settings.from_env(environ)
        return settings, modele or _modele_par_defaut(settings) or "", acces

    def _depot_courant(self, environ: Mapping[str, str]) -> DepotFournisseurParDefaut:
        return self._depot or DepotFournisseurParDefaut.default(environ)

    def _secret_du_coffre(self, nom: str) -> str | None:
        """La clé `nom` du coffre du poste — None s'il ne se lit pas (cf. `secret_du_poste`)."""
        try:
            return self._coffre.lire_poste().get(nom)
        except Exception:  # noqa: BLE001 — un coffre illisible vaut « pas de clé »
            return None


def _classes() -> Mapping[str, type[ModelProvider]]:
    from maestro.providers.factory import classes_configurables

    return classes_configurables()


def _modele_par_defaut(settings: Settings) -> str | None:
    """Le modèle qu'on interroge quand aucun n'est choisi — None quand le fournisseur n'en a pas."""
    from maestro.config import ConfigError
    from maestro.providers.factory import default_model

    try:
        return default_model(settings)
    except ConfigError:
        return None


def _variables_connues() -> tuple[str, ...]:
    """Les variables qu'un choix peut poser : celles que l'écran regarde dans l'environnement."""
    noms = [VARIABLE_FOURNISSEUR, VARIABLE_MODELE]
    for classe in _classes().values():
        for acces in classe.ACCES:
            noms.extend(nom for nom, _ in acces.variables)
            noms.extend(nom for nom in (acces.adresse, acces.secret) if nom)
    return tuple(dict.fromkeys(noms))


def _variables(demande: DemandeFournisseur, acces: AccesFournisseur) -> tuple[tuple[str, str], ...]:
    """Les variables que le choix pose — la clé n'en est jamais : elle va au coffre."""
    variables = [(VARIABLE_FOURNISSEUR, demande.fournisseur), *acces.variables]
    modele = _nettoye(demande.modele)
    if modele is not None:
        variables.append((VARIABLE_MODELE, modele))
    adresse = _adresse(demande, acces)
    if acces.adresse and adresse:
        variables.append((acces.adresse, adresse))
    return tuple(variables)


def _adresse(demande: DemandeFournisseur, acces: AccesFournisseur) -> str | None:
    if not acces.adresse:
        return None
    return _nettoye(demande.adresse) or acces.adresse_defaut


def _empreinte(demande: DemandeFournisseur) -> str:
    """Ce qui identifie un choix éprouvé — la clé n'y entre que hachée."""
    cle = hashlib.sha256((demande.cle or "").encode("utf-8")).hexdigest()
    brut = json.dumps(
        [
            demande.fournisseur,
            demande.acces,
            _nettoye(demande.modele),
            _nettoye(demande.adresse),
            cle,
        ]
    )
    return hashlib.sha256(brut.encode("utf-8")).hexdigest()


def _nettoye(valeur: str | None) -> str | None:
    texte = (valeur or "").strip()
    return texte or None


def _choix_public(choix: FournisseurParDefaut | None) -> dict[str, Any] | None:
    """Le choix tel que l'écran le lit : sans les variables, qui sont la cuisine de l'exécution."""
    if choix is None:
        return None
    return {
        "fournisseur": choix.fournisseur,
        "acces": choix.acces,
        "modele": choix.modele,
        "adresse": choix.adresse,
        "eprouve_le": choix.eprouve_le,
        "pose_le": choix.pose_le,
    }


__all__ = [
    "DUREE_EPREUVE_S",
    "ChoixRefuse",
    "DemandeFournisseur",
    "EpreuveEchouee",
    "Eprouveur",
    "ServiceFournisseurParDefaut",
]
