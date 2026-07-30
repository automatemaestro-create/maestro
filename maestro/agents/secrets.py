"""Coffre local des secrets par agent, **chiffré au repos** (tickets #109/#132, parent #102).

Sort les tokens d'intégration (serveurs MCP : Slack, tickets, Figma…) de
l'environnement global du process : chaque agent dispose de son **coffre** —
un fichier local `<racine>/<agent>.json`, jamais versionné — et la résolution
des références `${VARIABLE}` de ses déclarations MCP (#104) se fait dans **ce
coffre seulement**. Un agent ne voit que ses propres secrets : le token GitLab
du QA n'est pas résolvable par le DevOps, et réciproquement.

Le coffre est **opt-in par provisionnement** : tant qu'aucun fichier de coffre
`<agent>.json` n'existe sous la racine (`provisionne` faux — un README seul ne
compte pas), la résolution retombe sur l'environnement du process — le
comportement historique du #104, rien ne casse. Dès le premier coffre écrit,
le scoping est **strict pour tous les agents** : un secret absent du coffre de
l'agent rend le serveur indisponible (`McpServerUnavailable`), même si la
variable traîne dans l'environnement — c'est le contrat du ticket (« un agent
ne voit que les siens »), pas un oubli.

**Chiffrement au repos (#132).** Un secret est stocké **chiffré** dans le
fichier du coffre (`maestro.agents.chiffrement.Chiffreur`, Fernet), jamais en
clair : la déclaration versionnée n'a jamais porté de secret (`${VAR}`, #104),
et le fichier local du coffre — gitignoré mais susceptible de fuiter — ne
contient plus de valeur lisible sans la clé maîtresse (côté serveur :
`MAESTRO_SECRETS_KEY`, sinon une clé locale `<racine>/.cle`). Les coffres
**hérités** au format `{"secrets": {"VAR": "valeur en clair"}}` restent lisibles
(rétro-compat #109) : une valeur en clair (chaîne) est servie telle quelle, une
**entrée structurée** (objet) porte le chiffré, le mode d'auth et l'échéance.

**Trois modes d'auth (#132, docs/21 §3.2).** Chaque entrée structurée déclare
son `mode_auth` (`maestro.agents.mcp_registry.MODES_AUTH`) :

- **token statique** (`token_statique`, GitLab/Slack) : un vrai secret, chiffré ;
- **appairage** (`appairage`, pont Figma communautaire) : une valeur **éphémère
  non secrète** (canal), stockée en clair et **non** enregistrée au registre de
  rédaction — l'UI la présente comme jetable, pas comme un secret à conserver ;
- **token OAuth importé** (`oauth_importe`, Figma officiel) : chiffré et
  **expirable** — une échéance dépassée rend l'entrée **absente de la résolution**
  (`lire` l'omet), donc le serveur est **refusé au montage** (`McpServerUnavailable`
  pour un serveur requis, omission pour un serveur `optionnel` #125). L'état de
  validité est lisible sans déchiffrer (`etat`), et le **renouvellement** est une
  ré-importation (`renouveler`).

Toute valeur **secrète** servie est enregistrée au registre de rédaction
(`maestro.telemetry.redact.enregistre_secret`) : si elle réapparaît dans une
sortie (journal, trace Langfuse, rapport, ticket GitLab), elle est masquée.

Au POC le dépôt est sur fichiers (`core/secrets/`, couvert par `.gitignore` —
seul son README est versionné) ; en V1 il pourra passer sur un vrai gestionnaire
de secrets (Vault, SOPS…) sans changer ce contrat — c'est l'indirection.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.chiffrement import Chiffreur
from maestro.agents.mcp_registry import MODES_AUTH
from maestro.config import Settings, load_settings
from maestro.telemetry.redact import enregistre_secret

#: Nom d'agent admissible comme fichier de stockage — même verrou que les
#: dépôts voisins (`maestro.agents.mcp`, `store`) : slug sûr, jamais un chemin.
_NOM_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Nom de variable admissible dans un coffre : la même forme que les références
#: `${VARIABLE}` des déclarations MCP qu'il sert à résoudre.
_NOM_VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Fichier de la clé maîtresse locale (repli du POC quand `MAESTRO_SECRETS_KEY`
#: est absente) : gitignoré avec le reste du coffre (`core/secrets/*`). Exclu du
#: listing des coffres — ce n'est pas un `<agent>.json`.
_FICHIER_CLE = ".cle"


@dataclass(frozen=True)
class EtatSecret:
    """L'**état** d'un secret du coffre, sans sa valeur — la forme lisible par l'UI (#132).

    C'est ce qu'une page de configuration (lot #133) affiche pour guider la
    saisie et signaler ce qui doit être renouvelé : le mode d'auth, s'il s'agit
    d'un secret ou d'une valeur d'appairage éphémère, et — pour un token OAuth
    importé — sa validité (`valide`) et son échéance (`expire_le`). Aucune valeur
    en clair ni chiffrée n'y figure : lister l'état ne déchiffre rien.
    """

    cle: str
    mode_auth: str
    secret: bool
    #: Valeur d'appairage jetable (mode `appairage`) plutôt que secret à conserver.
    ephemere: bool
    #: Résolvable au montage : faux pour un token OAuth expiré (serveur refusé).
    valide: bool
    #: Échéance ISO 8601 d'un token expirable, ou None si l'entrée n'expire pas.
    expire_le: str | None

    def to_dict(self) -> dict[str, Any]:
        """Réémet l'état en dict JSON-sérialisable (forme publique API/UI, aucune valeur)."""
        return {
            "cle": self.cle,
            "mode_auth": self.mode_auth,
            "secret": self.secret,
            "ephemere": self.ephemere,
            "valide": self.valide,
            "expire_le": self.expire_le,
        }


@dataclass(frozen=True)
class _Entree:
    """Une entrée du coffre, normalisée à la lecture (secret déchiffrable à la demande).

    Interne : `lire` et `etat` la construisent depuis la forme stockée (chaîne
    héritée ou objet structuré) sans déchiffrer — le déchiffrement du `chiffre`
    n'a lieu que si `lire` doit servir la valeur.
    """

    cle: str
    mode_auth: str
    secret: bool
    #: Jeton chiffré (secret structuré), ou None si la valeur est déjà en clair.
    chiffre: str | None
    #: Valeur en clair (coffre hérité, ou valeur d'appairage non secrète), ou None.
    clair: str | None
    #: Échéance normalisée (aware UTC) pour la comparaison, ou None si aucune.
    echeance: datetime | None
    #: Échéance telle que stockée (ISO 8601), pour l'affichage, ou "" si aucune.
    echeance_iso: str

    def est_expire(self, maintenant: datetime) -> bool:
        """L'entrée est-elle échue à `maintenant` (échéance dépassée) ?"""
        return self.echeance is not None and maintenant >= self.echeance


class SecretStore:
    """Coffre des secrets par agent, sur fichiers (`<racine>/<agent>.json`), chiffré au repos.

    Un fichier par agent : `{"secrets": {"VARIABLE": <entrée>}}`, où `<entrée>`
    est soit une **chaîne** (valeur en clair, coffre hérité #109), soit un
    **objet** structuré (chiffré + mode d'auth + échéance, #132). Le nom d'agent
    fait foi côté fichier. Relu à chaque usage (application à chaud, comme les
    déclarations MCP #104) : un secret ajouté, tourné ou expiré vaut pour la
    tâche suivante, sans redémarrage. Toute lecture valide sa source et lève
    `ValueError` (cause exacte) si elle est douteuse — on ne résout jamais depuis
    un coffre invalide.

    Écriture (#132) : `enregistrer` chiffre et pose un secret avec son mode
    d'auth, `renouveler` ré-importe un token OAuth expirable, `supprimer` retire
    une entrée — écritures atomiques, comme le pool MCP.
    """

    def __init__(self, racine: Path, *, chiffreur: Chiffreur | None = None) -> None:
        self._racine = racine
        # Chiffreur injecté (clé de `MAESTRO_SECRETS_KEY`, ou un chiffreur de
        # test) ; None ⇒ clé locale auto-gérée sous la racine (`.cle`), résolue
        # paresseusement (une lecture purement héritée n'a pas besoin de clé).
        self._chiffreur_fixe = chiffreur

    @property
    def racine(self) -> Path:
        """La racine du coffre (un fichier JSON par agent)."""
        return self._racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> SecretStore:
        """Le coffre configuré : `MAESTRO_SECRETS_DIR`, sinon `core/secrets/` du dépôt.

        La **clé maîtresse** vient de `MAESTRO_SECRETS_KEY` si elle est posée
        (chiffreur injecté) ; sinon le coffre gère une clé locale `<racine>/.cle`
        (repli du POC, #132).
        """
        settings = settings or load_settings()
        cle = getattr(settings, "secrets_key", None)
        chiffreur = Chiffreur(cle) if cle else None
        if settings.secrets_dir:
            return cls(Path(settings.secrets_dir), chiffreur=chiffreur)
        racine = Path(__file__).resolve().parents[2] / "core" / "secrets"
        return cls(racine, chiffreur=chiffreur)

    @property
    def provisionne(self) -> bool:
        """Le coffre est-il provisionné (au moins un fichier `<agent>.json`) ?

        C'est la bascule du scoping : provisionné, chaque agent ne résout que
        dans son coffre ; sinon, la résolution garde l'environnement du
        process (comportement historique #104). Le seuil est le **premier
        coffre écrit**, pas l'existence de la racine : le dépôt versionne un
        README sous `core/secrets/`, la racine existe donc partout. Le fichier
        de clé (`.cle`) n'est pas un coffre — il ne compte pas.
        """
        return any(self._racine.glob("*.json"))

    def lire(self, agent: str) -> dict[str, str]:
        """Les secrets **résolvables** du coffre de `agent` — vide s'il n'a pas de fichier.

        Chaque entrée est servie en clair : une valeur héritée telle quelle, un
        secret structuré **déchiffré** (clé maîtresse requise). Les entrées
        **expirées** (token OAuth échu, #132) sont **omises** — d'où l'échec
        propre au montage (serveur requis refusé, serveur `optionnel` omis).
        Chaque valeur **secrète** servie est enregistrée au registre de rédaction
        (masquée où qu'elle réapparaisse) ; une valeur d'appairage non secrète ne
        l'est pas. Lève `ValueError` (cause exacte, agent nommé) si le fichier est
        illisible, invalide, ou chiffré sans clé disponible.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return {}
        maintenant = datetime.now(UTC)
        secrets: dict[str, str] = {}
        for entree in self._entrees(agent, chemin):
            if entree.est_expire(maintenant):
                continue  # token expiré : absent de la résolution (refus au montage)
            if entree.chiffre is not None:
                valeur = self._chiffreur(creer=False).dechiffrer(entree.chiffre)
            else:
                # clair non None par construction (voir _parse_entree)
                valeur = entree.clair or ""
            secrets[entree.cle] = valeur
            if entree.secret:
                enregistre_secret(valeur)
        return secrets

    def etat(self, agent: str) -> tuple[EtatSecret, ...]:
        """L'**état** de chaque secret du coffre de `agent`, sans valeur (#132) — () si absent.

        La vue que sert l'UI (lot #133) : mode d'auth, secret ou appairage
        éphémère, validité et échéance d'un token expirable. Ne déchiffre rien.
        Lève `ValueError` si le fichier est illisible ou invalide.
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return ()
        maintenant = datetime.now(UTC)
        etats: list[EtatSecret] = []
        for entree in self._entrees(agent, chemin):
            etats.append(
                EtatSecret(
                    cle=entree.cle,
                    mode_auth=entree.mode_auth,
                    secret=entree.secret,
                    ephemere=entree.mode_auth == "appairage",
                    valide=not entree.est_expire(maintenant),
                    expire_le=entree.echeance_iso or None,
                )
            )
        return tuple(etats)

    def environ(self, agent: str) -> Mapping[str, str]:
        """L'environnement de résolution des références `${VAR}` de `agent` (#104).

        Le cœur du scoping : coffre **provisionné** → les secrets de `agent`
        seuls (un agent ne voit que les siens — fichier absent = aucun secret,
        les serveurs à référence deviennent indisponibles, échec propre) ;
        coffre **absent** → l'environnement du process, comportement
        historique. Propage le `ValueError` d'un coffre invalide.
        """
        if not self.provisionne:
            return os.environ
        return self.lire(agent)

    def enregistrer(
        self,
        agent: str,
        cle: str,
        valeur: str,
        *,
        mode_auth: str,
        secret: bool = True,
        expire_le: datetime | str | None = None,
    ) -> EtatSecret:
        """Pose (ou remplace) le secret `cle` de `agent`, **chiffré** si `secret` (#132).

        Le point d'écriture du coffre — c'est ce qu'appellera l'UI de
        configuration (lot #133) pour chacun des trois parcours d'auth :

        - `mode_auth="token_statique"`, `secret=True` : token chiffré au repos ;
        - `mode_auth="appairage"`, `secret=False` : valeur d'appairage éphémère,
          stockée en clair et non enregistrée à la rédaction (ce n'est pas un
          secret) — l'UI la présente comme jetable ;
        - `mode_auth="oauth_importe"`, `secret=True`, `expire_le=<échéance>` :
          token OAuth importé, chiffré et expirable (voir `renouveler`).

        Les autres entrées de l'agent sont préservées (fusion), l'écriture est
        atomique. Lève `ValueError` **sans rien écrire** si le nom d'agent, la
        clé, le mode d'auth ou l'échéance sont invalides.
        """
        self._chemin(agent)  # valide le nom d'agent
        if not _NOM_VARIABLE.match(cle):
            raise ValueError(
                f"nom de variable invalide : {cle!r} "
                "(forme [A-Za-z_][A-Za-z0-9_]* attendue)."
            )
        if mode_auth not in MODES_AUTH:
            raise ValueError(
                f"mode d'auth invalide : {mode_auth!r} (attendu : {', '.join(MODES_AUTH)})."
            )
        if not isinstance(valeur, str):
            raise ValueError(f"la valeur de {cle} doit être une chaîne.")
        entree: dict[str, Any] = {"mode_auth": mode_auth, "secret": secret}
        if secret:
            entree["chiffre"] = self._chiffreur(creer=True).chiffrer(valeur)
        else:
            entree["valeur"] = valeur
        echeance_iso = ""
        if expire_le is not None:
            _, echeance_iso = _horodatage(expire_le, contexte=f"pour {cle}")
            entree["expire_le"] = echeance_iso
        brut = self._lire_brut(agent)
        brut[cle] = entree
        self._ecrire(agent, brut)
        # Relit l'entrée qu'on vient d'écrire pour rendre son état (validité comprise).
        (etat,) = (e for e in self.etat(agent) if e.cle == cle)
        return etat

    def renouveler(
        self, agent: str, cle: str, valeur: str, expire_le: datetime | str
    ) -> EtatSecret:
        """Ré-importe le token OAuth `cle` de `agent` avec une nouvelle échéance (#132).

        La **procédure de renouvellement** du parcours OAuth importé : un token
        expiré (serveur refusé au montage) se remplace par un token frais et sa
        date d'expiration — l'entrée redevient valide et le serveur remonte. Un
        raccourci lisible autour d'`enregistrer(mode_auth="oauth_importe")` :
        `expire_le` est ici **obligatoire** (un token importé sans échéance
        connue n'aurait pas d'état de validité à afficher).
        """
        return self.enregistrer(
            agent, cle, valeur, mode_auth="oauth_importe", secret=True, expire_le=expire_le
        )

    def supprimer(self, agent: str, cle: str) -> bool:
        """Retire le secret `cle` du coffre de `agent`. Renvoie s'il était présent (#132).

        Les autres entrées sont préservées ; le fichier reste (coffre toujours
        provisionné, scoping actif) même vidé de cette clé. Lève `ValueError` si
        le nom d'agent est invalide.
        """
        brut = self._lire_brut(agent)
        if cle not in brut:
            return False
        del brut[cle]
        self._ecrire(agent, brut)
        return True

    # --- Interne ------------------------------------------------------------------------

    def _entrees(self, agent: str, chemin: Path) -> tuple[_Entree, ...]:
        """Les entrées normalisées du fichier de `agent`, validées (jamais déchiffrées ici)."""
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"coffre de secrets illisible pour l'agent {agent!r} ({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
            raise ValueError(
                f"coffre de secrets invalide pour l'agent {agent!r} ({chemin.name}) : "
                'objet {"secrets": {...}} attendu.'
            )
        return tuple(
            _parse_entree(agent, nom, brut) for nom, brut in data["secrets"].items()
        )

    def _lire_brut(self, agent: str) -> dict[str, Any]:
        """La table `secrets` **brute** de `agent` (forme stockée), {} si absente.

        Sert les écritures (`enregistrer`/`supprimer`) : on relit la forme
        stockée telle quelle pour fusionner sans toucher aux autres entrées.
        Valide juste l'enveloppe `{"secrets": {...}}` — la validation fine des
        entrées reste à la lecture (`_entrees`).
        """
        chemin = self._chemin(agent)
        if not chemin.is_file():
            return {}
        try:
            data = json.loads(chemin.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"coffre de secrets illisible pour l'agent {agent!r} ({chemin.name}) : {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
            raise ValueError(
                f"coffre de secrets invalide pour l'agent {agent!r} ({chemin.name}) : "
                'objet {"secrets": {...}} attendu.'
            )
        return dict(data["secrets"])

    def _ecrire(self, agent: str, secrets: Mapping[str, Any]) -> None:
        """Écrit atomiquement le coffre `{"secrets": {...}}` de `agent`, racine créée au besoin."""
        chemin = self._chemin(agent)
        self._racine.mkdir(parents=True, exist_ok=True)
        temporaire = chemin.with_suffix(chemin.suffix + ".tmp")
        temporaire.write_text(
            json.dumps({"secrets": dict(secrets)}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)

    def _chiffreur(self, *, creer: bool) -> Chiffreur:
        """Le chiffreur du coffre : celui injecté, sinon la clé locale `<racine>/.cle`.

        `creer` (écriture) génère et persiste la clé locale si elle manque ;
        sans (lecture), une clé absente lève `ValueError` — on ne déchiffre pas
        un coffre chiffré sans sa clé.
        """
        if self._chiffreur_fixe is not None:
            return self._chiffreur_fixe
        return Chiffreur(self._cle_locale(creer=creer))

    def _cle_locale(self, *, creer: bool) -> bytes:
        """La clé maîtresse locale (`<racine>/.cle`) — générée au premier besoin si `creer`."""
        chemin = self._racine / _FICHIER_CLE
        if chemin.is_file():
            return chemin.read_bytes()
        if not creer:
            raise ValueError(
                "clé de chiffrement absente : coffre chiffré illisible sans "
                f"MAESTRO_SECRETS_KEY ni fichier de clé ({_FICHIER_CLE}) sous la racine."
            )
        self._racine.mkdir(parents=True, exist_ok=True)
        cle = Chiffreur.generer_cle()
        temporaire = chemin.with_suffix(".tmp")
        temporaire.write_bytes(cle)
        os.replace(temporaire, chemin)
        try:  # best-effort : restreindre l'accès à la clé (no-op partiel sous Windows)
            os.chmod(chemin, 0o600)
        except OSError:
            pass
        return cle

    def _chemin(self, agent: str) -> Path:
        """Le fichier de coffre de `agent`, nom validé (jamais un chemin arbitraire)."""
        if not _NOM_AGENT.match(agent):
            raise ValueError(f"nom d'agent invalide : {agent!r} (slug [a-z0-9_-] attendu).")
        return self._racine / f"{agent}.json"


def _parse_entree(agent: str, nom: str, brut: Any) -> _Entree:
    """Normalise une entrée stockée (chaîne héritée ou objet structuré) en `_Entree`.

    Rétro-compat #109 : une **chaîne** est une valeur en clair héritée (token
    statique). Un **objet** est une entrée structurée #132 (mode d'auth, secret
    chiffré ou valeur d'appairage, échéance). Lève `ValueError` (agent nommé,
    cause exacte) sur une forme invalide — la « validation à la lecture » du
    socle.
    """
    if not isinstance(nom, str) or not _NOM_VARIABLE.match(nom):
        raise ValueError(
            f"coffre de secrets invalide pour l'agent {agent!r} : nom de "
            f"variable {nom!r} (forme [A-Za-z_][A-Za-z0-9_]* attendue)."
        )
    if isinstance(brut, str):
        return _Entree(
            cle=nom, mode_auth="token_statique", secret=True,
            chiffre=None, clair=brut, echeance=None, echeance_iso="",
        )
    if not isinstance(brut, dict):
        raise ValueError(
            f"coffre de secrets invalide pour l'agent {agent!r} : la valeur de "
            f"{nom} doit être une chaîne (secret en clair hérité) ou un objet "
            "d'entrée structurée."
        )
    mode_auth = brut.get("mode_auth")
    if mode_auth not in MODES_AUTH:
        raise ValueError(
            f"coffre de secrets invalide pour l'agent {agent!r} : mode d'auth "
            f"{mode_auth!r} de {nom} (attendu : {', '.join(MODES_AUTH)})."
        )
    secret = brut.get("secret", True)
    if not isinstance(secret, bool):
        raise ValueError(
            f"coffre de secrets invalide pour l'agent {agent!r} : le champ "
            f"'secret' de {nom} doit être un booléen."
        )
    chiffre: str | None = None
    clair: str | None = None
    if secret:
        jeton = brut.get("chiffre")
        if not isinstance(jeton, str) or not jeton:
            raise ValueError(
                f"coffre de secrets invalide pour l'agent {agent!r} : entrée "
                f"secrète {nom} sans jeton 'chiffre' (chaîne non vide attendue)."
            )
        chiffre = jeton
    else:
        val = brut.get("valeur")
        if not isinstance(val, str):
            raise ValueError(
                f"coffre de secrets invalide pour l'agent {agent!r} : la 'valeur' "
                f"non secrète de {nom} doit être une chaîne."
            )
        clair = val
    echeance: datetime | None = None
    echeance_iso = ""
    if brut.get("expire_le") is not None:
        echeance, echeance_iso = _horodatage(
            brut["expire_le"], contexte=f"pour {nom} (agent {agent!r})"
        )
    return _Entree(
        cle=nom, mode_auth=mode_auth, secret=secret,
        chiffre=chiffre, clair=clair, echeance=echeance, echeance_iso=echeance_iso,
    )


def _horodatage(brut: Any, *, contexte: str) -> tuple[datetime, str]:
    """`(échéance aware UTC, ISO stocké)` depuis un `datetime` ou une chaîne ISO 8601.

    Une échéance naïve (sans fuseau) est interprétée en **UTC** — la comparaison
    d'expiration se fait toujours sur un instant aware. Lève `ValueError` sur une
    forme non parsable.
    """
    if isinstance(brut, datetime):
        dt = brut
        iso = brut.isoformat()
    elif isinstance(brut, str):
        try:
            dt = datetime.fromisoformat(brut)
        except ValueError as exc:
            raise ValueError(
                f"échéance invalide {contexte} : {brut!r} (ISO 8601 attendu)."
            ) from exc
        iso = brut
    else:
        raise ValueError(
            f"échéance invalide {contexte} : chaîne ISO 8601 ou datetime attendu."
        )
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt, iso
