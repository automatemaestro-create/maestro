"""La porte d'entrée réelle du banc : l'API de la Control Tower, en HTTP (#1148).

Le banc **n'importe pas** l'application. Il parle à l'API qui tourne, par les
appels exacts que l'écran envoie — `POST /api/chat/orchestrateur/messages`,
l'accord, le suivi du run. C'est le point du ticket : ce qu'un utilisateur fait
passe par le réseau, par le fil, par le vrai fournisseur ; une `TestClient`
monterait une pile *ressemblante*, et c'est précisément ce qui n'a rien vu du
défaut de #1146 pendant dix jours.

Deux couches, et la frontière entre elles est ce qui rend le banc testable sans
réseau :

- `Transport` — « envoie cette requête, rends ce statut et ce corps », et rien de
  plus. `TransportHTTP` en est l'implémentation réelle (`httpx2`, déjà au socle) ;
  les tests en substituent une fausse API qui répond ce qu'on lui dit ;
- `ClientAPI` — les **verbes du banc** au-dessus d'un transport : déclarer un
  projet, ouvrir une conversation, envoyer un message, valider l'équipe, donner
  l'accord, lire un run. C'est ici que vivent les chemins et les formes de corps,
  une seule fois, et c'est tout ce que les scénarios connaissent de l'API.

⚠ **Aucun nom de canal n'est recopié.** Le fil de l'orchestration s'adresse par
`NOM_ORCHESTRATION`, le port par `port_api` de la purge : la leçon de #830 est
qu'une constante recopiée de l'autre côté d'une frontière finit par désigner
autre chose sans que rien ne le dise.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from maestro.controltower.orchestration import NOM_ORCHESTRATION
from maestro.controltower.purge import port_api
from maestro.controltower.state import (
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_EN_ATTENTE,
)
from maestro.deliberation import cle_acte

#: Le chemin du fil de l'orchestrateur — la seule porte de lancement qu'un écran
#: offre depuis #666, donc la seule que le banc a le droit d'emprunter.
FIL = f"/api/chat/{NOM_ORCHESTRATION}"

#: Délai d'une requête HTTP ordinaire. Le suivi d'un run, lui, ne dépend pas de
#: ce délai : il repose sur des lectures courtes répétées (`attendre_le_run`).
DELAI_REQUETE_S = 30.0

#: L'intervalle entre deux lectures d'un run en vol. Une seconde : le run dure
#: des minutes, et interroger plus souvent ne ferait que charger l'API qu'on
#: mesure.
INTERVALLE_SUIVI_S = 1.0


class ErreurAPI(RuntimeError):
    """L'API a refusé, ou n'a pas répondu ce que le banc attendait.

    Porte le `statut` HTTP (0 quand rien n'a répondu) et le `chemin` visé : un
    scénario rouge doit pouvoir dire *où* il s'est arrêté, pas seulement qu'il
    s'est arrêté.
    """

    def __init__(self, message: str, *, statut: int = 0, chemin: str = "") -> None:
        super().__init__(message)
        self.statut = statut
        self.chemin = chemin


@dataclass(frozen=True)
class Reponse:
    """Ce qu'un transport rend : le statut, le corps décodé, le texte brut."""

    statut: int
    corps: Any = None
    texte: str = ""

    @property
    def ok(self) -> bool:
        """La requête a-t-elle abouti (2xx) ?"""
        return 200 <= self.statut < 300


class Transport(Protocol):
    """Ce que le banc demande à un transport HTTP — et rien de plus.

    Un protocole plutôt qu'un client concret, pour la raison qui a fait celui de
    `maestro.controltower.purge` : c'est ce qui permet aux tests de jouer le
    déroulé entier contre une fausse API, sans réseau ni serveur.
    """

    def demander(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Reponse: ...


class TransportHTTP:
    """Le transport réel : `httpx2` sur la boucle locale, en synchrone.

    Synchrone à dessein : le banc est un déroulé, pas un serveur — il fait une
    chose après l'autre et attend chaque fois de savoir ce qui s'est passé.
    """

    def __init__(self, base: str, *, delai_s: float = DELAI_REQUETE_S) -> None:
        self._base = base.rstrip("/")
        self._delai_s = delai_s

    @property
    def base(self) -> str:
        """L'adresse de l'API que ce transport vise."""
        return self._base

    def demander(  # pragma: no cover - la couche réseau ; les tests jouent une fausse API
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Reponse:
        """Joue la requête et rend sa réponse — une panne réseau devient `ErreurAPI`."""
        import httpx2

        try:
            with httpx2.Client(timeout=self._delai_s) as client:
                brute = client.request(
                    methode,
                    f"{self._base}{chemin}",
                    json=None if corps is None else dict(corps),
                    params=dict(params or {}),
                )
        except httpx2.HTTPError as echec:
            raise ErreurAPI(f"API injoignable ({echec})", chemin=chemin) from echec
        texte = brute.text
        try:
            corps_decode = json.loads(texte) if texte else None
        except ValueError:
            corps_decode = None
        return Reponse(statut=brute.status_code, corps=corps_decode, texte=texte)


def base_locale(environnement: Mapping[str, str] | None = None) -> str:
    """L'adresse de l'API locale — le port de la purge, jamais un second réglage."""
    env = None if environnement is None else dict(environnement)
    return f"http://127.0.0.1:{port_api(env)}"


class ClientAPI:
    """Les verbes du banc au-dessus d'un transport : un seul endroit qui connaît l'API."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    # --- Le socle -------------------------------------------------------

    def _appel(
        self,
        methode: str,
        chemin: str,
        *,
        corps: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
        attendus: Sequence[int] = (200, 201),
    ) -> Any:
        """Joue la requête et rend son corps — tout statut inattendu lève."""
        reponse = self._transport.demander(methode, chemin, corps=corps, params=params)
        if reponse.statut not in attendus:
            raise ErreurAPI(
                f"{methode} {chemin} → {reponse.statut} : {reponse.texte[:400]}",
                statut=reponse.statut,
                chemin=chemin,
            )
        return reponse.corps

    def sante(self) -> bool:
        """L'API répond-elle ? Une réponse d'erreur compte : ce qu'on veut savoir
        est si un process sert ce port, pas s'il va bien (même règle que la purge)."""
        try:
            return self._transport.demander("GET", "/api/sante").statut > 0
        except ErreurAPI:
            return False

    # --- Les projets ----------------------------------------------------

    def declarer_projet(self, nom: str, racine: str, *, origine: str) -> str:
        """Déclare un projet jetable et rend son identifiant."""
        fiche = self._appel(
            "POST", "/api/projets", corps={"nom": nom, "racine": racine, "origine": origine}
        )
        return str(fiche["id"])

    def retirer_projet(self, projet_id: str) -> None:
        """Oublie la déclaration — le dossier sur le disque, lui, reste (#221)."""
        self._appel("DELETE", f"/api/projets/{projet_id}", attendus=(200, 404))

    # --- L'équipe -------------------------------------------------------

    def proposition_equipe(self, projet_id: str) -> dict[str, Any]:
        """L'équipe que l'analyse du projet appelle (#1039) — rien n'est créé ici."""
        propose: dict[str, Any] = self._appel(
            "POST", f"/api/projets/{projet_id}/equipe/proposition"
        )
        return propose

    def creer_equipe(self, projet_id: str, validee: Mapping[str, Any]) -> dict[str, Any]:
        """Crée l'équipe validée par la voie de l'étape d'équipe (#1040)."""
        rapport: dict[str, Any] = self._appel(
            "POST", f"/api/projets/{projet_id}/equipe", corps=validee
        )
        return rapport

    # --- Le fil ---------------------------------------------------------

    def ouvrir_conversation(self) -> str:
        """Une conversation neuve sur le fil de l'orchestrateur.

        Neuve à chaque scénario, et c'est une condition de l'oracle : la demande
        de cadrage comme celle de recrutement sont des propriétés de la **suite**
        des messages (`proposition_en_attente`), donc un fil déjà chargé ferait
        trancher une demande qui n'est pas celle du scénario.
        """
        corps = self._appel("POST", f"{FIL}/conversations")
        return str(corps["conversation"]["id"])

    def envoyer(self, contenu: str, *, projet_id: str, conversation: str) -> dict[str, Any]:
        """Envoie un message d'utilisateur et rend **la réponse de l'agent**."""
        corps = self._appel(
            "POST",
            f"{FIL}/messages",
            corps={"contenu": contenu, "projet_id": projet_id, "conversation": conversation},
        )
        return _reponse_de(corps, chemin=f"{FIL}/messages")

    def recruter(
        self, *, conversation: str, validee: Mapping[str, Any], approuve: bool = True
    ) -> dict[str, Any]:
        """Valide l'équipe que le fil propose (#1146) et rend la réponse qui suit."""
        corps = self._appel(
            "POST",
            f"{FIL}/recrutement",
            corps={"approuve": approuve, "conversation": conversation, **dict(validee)},
        )
        return _reponse_de(corps, chemin=f"{FIL}/recrutement")

    def trancher_cadrage(
        self,
        *,
        conversation: str,
        projet_id: str,
        approuve: bool = True,
        bornes: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Donne l'accord au cadrage proposé, bornes comprises (#990), et rend la réponse."""
        corps = self._appel(
            "POST",
            f"{FIL}/cadrage",
            corps={
                "approuve": approuve,
                "projet_id": projet_id,
                "conversation": conversation,
                **dict(bornes or {}),
            },
        )
        return _reponse_de(corps, chemin=f"{FIL}/cadrage")

    # --- Les runs -------------------------------------------------------

    def execution(self, run_id: str, *, projet_id: str) -> dict[str, Any]:
        """L'état d'un run : statut, cause, coût, trace."""
        detail: dict[str, Any] = self._appel(
            "GET", f"/api/executions/{run_id}", params={"projet": projet_id}
        )
        return detail

    def validations(self, *, projet_id: str) -> list[dict[str, Any]]:
        """Les demandes d'arbitrage du projet (#48) — celles qui suspendent un run."""
        demandes: list[dict[str, Any]] = self._appel(
            "GET", "/api/validations", params={"projet": projet_id}
        )
        return demandes

    def decider(self, tache_id: str, *, approuve: bool = True) -> None:
        """Tranche un arbitrage en attente — 409 si quelqu'un l'a déjà tranché."""
        self._appel(
            "POST",
            f"/api/validations/{tache_id}/decision",
            corps={"approuve": approuve, "motif": ""},
            attendus=(200, 409),
        )


def _reponse_de(corps: Any, *, chemin: str) -> dict[str, Any]:
    """Le message **de l'agent** dans la paire que rendent les routes du fil.

    Les trois routes rendent `messages: [ce que l'utilisateur a fait, la réponse]`.
    Le banc lit toujours la seconde moitié : c'est elle qui porte la demande de
    cadrage, la demande d'équipe et le `run_id`.
    """
    messages = (corps or {}).get("messages") or []
    if len(messages) < 2:
        raise ErreurAPI(
            f"réponse inattendue de {chemin} : {len(messages)} message(s) au lieu de 2",
            chemin=chemin,
        )
    reponse: dict[str, Any] = messages[-1]
    return reponse


def equipe_validee(proposition: Mapping[str, Any]) -> dict[str, Any]:
    """L'équipe proposée, **rapportée telle quelle** au geste de validation.

    La forme de `POST /api/projets/{id}/equipe`, parce que c'est la même création
    (#1040) : ce qui repart est ce qui a été montré. Le banc ne retouche aucun
    rôle — il joue un utilisateur qui accepte la proposition, et tout ajustement
    inventé ici ferait juger autre chose que ce que l'analyse a recommandé.
    """
    return {
        "proposition_id": proposition.get("id", ""),
        "roles": [
            {
                "nom": role["nom"],
                "role": role["role"],
                "competences": role["competences"],
                "playbook": role["playbook"],
                "instances": role["instances"],
                "gabarit": role["gabarit"],
                "skills": [
                    {"nom": s["nom"], "chemin": s["chemin"], "commandes": s["commandes"]}
                    for s in role["skills"]
                ],
                "politique": role["politique"],
            }
            for role in proposition.get("roles") or []
        ],
    }


def attendre_le_run(
    client: ClientAPI,
    run_id: str,
    *,
    projet_id: str,
    delai_s: float,
    note: Callable[[str, str], None],
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
    intervalle_s: float = INTERVALLE_SUIVI_S,
) -> dict[str, Any]:
    """Suit un run jusqu'à son issue et rend son détail — arbitrages tranchés au passage.

    Le banc **approuve** les arbitrages d'action sensible (#571) de *son* run, et
    de lui seul (`run_id` porté par la demande depuis #570). Ce n'est pas une
    liberté prise : le projet est jetable, il vient d'être déclaré par le banc, et
    le geste attendu d'un utilisateur qui a demandé « vide ce dossier » est
    exactement celui-là. Ne pas le poser laisserait tous les scénarios d'action
    expirer sur une attente — ce qui dirait « le produit ne sait pas agir » là où
    il attend simplement qu'on lui réponde. Chaque approbation est **notée** au
    déroulé : le rapport dit ce que le banc a tranché.

    ⚠ **Une décision par acte, et non une par tâche** (#1197). Le premier jet
    retenait le `tache_id`, si bien qu'une tâche qui demandait deux gestes voyait
    le second expirer : mesuré sur le run `5508ebb01cb8`, où `python --version` a
    consommé l'unique approbation et le `mkdir -p src/depensio` qui suivait est
    resté en attente — un rouge qui ne disait rien du produit, seulement de
    l'utilisateur qu'on simulait. Or l'utilisateur simulé est quelqu'un qui
    **regarde son run** : il répond à chaque demande, pas à la première. Ce qui
    est tenu, c'est qu'il ne réponde jamais **deux fois au même acte**
    (`maestro.deliberation.cle_acte`, l'identité que le moteur utilise déjà pour
    ne pas rouvrir une demande) : un agent qui rejouerait sa commande à
    l'identique ne fabrique donc pas une boucle d'approbations.

    À l'expiration du délai, le dernier état lu est rendu tel quel : c'est à
    l'oracle de juger qu'un run encore en vol n'est pas un run abouti.
    """
    limite = horloge() + delai_s
    tranchees: set[tuple[str, str]] = set()
    detail = client.execution(run_id, projet_id=projet_id)
    while True:
        statut = str(detail.get("statut") or "")
        if statut in STATUTS_EXECUTION_TERMINAUX:
            return detail
        if statut == EXECUTION_EN_ATTENTE_ARBITRAGE:
            for demande in client.validations(projet_id=projet_id):
                acte = (
                    str(demande.get("tache_id") or ""),
                    cle_acte(
                        str(demande.get("outil") or ""), demande.get("arguments") or {}
                    ),
                )
                if (
                    str(demande.get("run_id") or "") == run_id
                    and str(demande.get("statut") or "") == VALIDATION_EN_ATTENTE
                    and acte not in tranchees
                ):
                    tache = str(demande["tache_id"])
                    tranchees.add(acte)
                    client.decider(tache, approuve=True)
                    note(
                        "arbitrage approuvé",
                        f"{tache} — {demande.get('titre') or demande.get('outil') or ''}",
                    )
        if horloge() >= limite:
            return detail
        dormir(intervalle_s)
        detail = client.execution(run_id, projet_id=projet_id)
