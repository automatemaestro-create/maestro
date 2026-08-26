"""Runtime outillé générique — un seul moteur d'exécution pour tous les rôles (ticket #35).

Factorise ce que les runtimes du Développeur (#4) et de la Base de données (#5)
dupliquaient : l'ouverture d'un **espace de travail isolé** (`maestro.sandbox`),
l'exécution **agentique outillée** du fournisseur (`ModelProvider.run_agent`), la
capture des fichiers produits et leur mise en forme en livrable exploitable.

Ce qui distingue un rôle d'un autre tient dans un `RoleProfile` (identité, modèle,
outils, prompts) : ajouter un agent outillé = déclarer un profil, sans copier de code.
Les profils existants vivent dans leur module de rôle (`maestro.agents.developer`,
`maestro.agents.database`).

Reste **agnostique du fournisseur** : il ne dépend que de `ModelProvider`. Un
fournisseur sans exécution outillée lève `UnsupportedCapability` — le runtime la
propage sans la simuler (c'est l'appelant, ex. la boucle d'orchestration, qui décide
d'un éventuel repli).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.agents.mcp import ServeurMcp, resolus
from maestro.agents.permissions import PolitiqueOutils
from maestro.config import Settings, load_settings
from maestro.deliberation import CreditArbitrage
from maestro.detail_tache import EtapeTache
from maestro.projets.modele import Projet
from maestro.projets.secrets import enregistre_secrets_du_projet
from maestro.providers.arbitrage import Arbitre, ArbitreActe
from maestro.providers.base import PLAFOND_TOURS_DEFAUT, ModelProvider
from maestro.sandbox import ProducedFile, espace_de_travail

#: Outils confiés par défaut à un rôle outillé : lire/écrire/éditer des fichiers,
#: explorer, shell, **tenir sa liste de travail**. Volontairement restreint
#: (docs/02 §7 : permissions scopées) — pas d'outils réseau ni MCP au POC.
#:
#: `TodoWrite` (#489) est le seul de la liste qui n'agisse sur rien : il ne lit,
#: n'écrit ni n'exécute quoi que ce soit, il **dit** où l'agent en est. C'est ce
#: qui en fait le canal de la checklist d'une tâche (`maestro.providers.checklist`)
#: — la moitié « cochée par l'agent » de l'arbitrage de #489 — sans rien lui
#: demander qu'il ne fasse déjà, et sans élargir d'un pouce ce qu'il peut faire.
#: Un rôle dont la politique de permissions le refuse (#110) travaille comme
#: avant : sa tâche n'a simplement pas de checklist.
DEFAULT_TOOLS: tuple[str, ...] = (
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "Bash",
    "TodoWrite",
)


@dataclass(frozen=True)
class RoleProfile:
    """Paramétrage d'un rôle outillé : tout ce qui varie d'un agent à l'autre.

    `nom` est l'identifiant du catalogue (`maestro.agents.catalog`) — la clé de
    routage de la boucle d'orchestration ; `role` le libellé humain (titres des
    synthèses). Les trois fragments de prompt (`intro_tache`, `consignes`,
    `consigne_finale`) encadrent la description de la tâche dans le message confié
    à l'agent ; `prompt_systeme` porte l'identité et les garde-fous du rôle.

    `plafond_tours` (#239) est le garde-fou anti-boucle du rôle, exprimé en tours
    de boucle agentique. Il vit **ici** et non dans le fournisseur parce qu'un tour
    n'a pas de coût comparable d'un rôle à l'autre — ~10 000 tokens pour une tâche
    de validation, ~70 000 pour une tâche de conception : une borne unique protège
    mal les uns en bridant les autres.

    Il vaut `None` par défaut et **aucun profil du dépôt n'en déclare** (#494) : un
    agent n'est plus borné en tours, la boucle s'arrêtant quand il a fini, quand il
    échoue ou quand on l'annule. Le champ reste pour qu'une borne puisse être posée
    — c'est le retrait du *défaut*, pas du réglage.
    """

    nom: str
    role: str
    modele: str
    outils: tuple[str, ...]
    prompt_systeme: str
    intro_tache: str
    consignes: str
    consigne_finale: str
    workspace_prefix: str
    plafond_tours: int | None = PLAFOND_TOURS_DEFAUT


@dataclass(frozen=True)
class AgentOutcome:
    """Résultat exploitable d'une exécution outillée.

    `role` est le libellé du rôle qui a produit le livrable ; `resume` le
    compte-rendu final de l'agent ; `fichiers` les livrables réellement écrits dans
    l'espace isolé (chemin relatif + contenu), capturés avant nettoyage ;
    `workspace` le chemin de cet espace (conservé seulement si l'exécution l'a
    demandé — sinon il n'existe plus sur le disque).
    """

    role: str
    resume: str
    fichiers: tuple[ProducedFile, ...]
    workspace: str

    @property
    def a_produit(self) -> bool:
        """L'agent a-t-il écrit au moins un fichier (livrable non vide) ?"""
        return bool(self.fichiers)

    def synthese(self) -> str:
        """Rend le résultat en Markdown : compte-rendu puis liste des fichiers produits."""
        lignes = [
            f"# Livrable — agent {self.role}",
            "",
            f"{len(self.fichiers)} fichier(s) produit(s) dans un espace de travail isolé.",
            "",
            "## Compte-rendu",
            "",
            self.resume or "(aucun compte-rendu)",
            "",
            "## Fichiers produits",
        ]
        if not self.fichiers:
            lignes += ["", "(aucun fichier)"]
        else:
            lignes += [f"- `{f.chemin}`" for f in self.fichiers]
        return "\n".join(lignes).rstrip() + "\n"

    def to_dict(self) -> dict[str, Any]:
        """Réémet le résultat en dict JSON-sérialisable."""
        return {
            "role": self.role,
            "resume": self.resume,
            "workspace": self.workspace,
            "a_produit": self.a_produit,
            "fichiers": [f.to_dict() for f in self.fichiers],
        }


class AgentRuntime:
    """Runtime d'un rôle outillé : exécute une tâche de bout en bout dans un espace isolé."""

    def __init__(
        self,
        provider: ModelProvider,
        profile: RoleProfile,
        *,
        model: str | None = None,
        tools: Sequence[str] | None = None,
        system_prompt: str | None = None,
        plafond_tours: int | None = None,
    ) -> None:
        self._provider = provider
        self._profile = profile
        self._model = model or profile.modele
        self._tools = tuple(tools) if tools is not None else profile.outils
        self._system_prompt = system_prompt or profile.prompt_systeme
        # Surcharge du plafond du profil, comme `model`/`tools` : le câblage peut
        # poser une borne sur un rôle qui n'en a pas — plus aucun n'en a (#494) —
        # sans toucher à son profil. `None` des deux côtés = pas de borne.
        self._plafond_tours = plafond_tours if plafond_tours is not None else profile.plafond_tours

    @property
    def profile(self) -> RoleProfile:
        """Profil du rôle exécuté par ce runtime."""
        return self._profile

    @classmethod
    def default(cls, profile: RoleProfile, settings: Settings | None = None) -> AgentRuntime:
        """Runtime par défaut pour `profile` : fournisseur et modèle issus de la config (#69).

        Importe la fabrique ici (et non en tête de module) pour ne pas lier le
        runtime agnostique à un fournisseur concret : le choix vit dans la config
        (`MAESTRO_PROVIDER`), plus dans le code. Un fournisseur sans exécution
        outillée lèvera `UnsupportedCapability` à l'exécution — propagé tel quel.
        """
        from maestro.providers.factory import provider_from_settings

        settings = settings or load_settings()
        return cls(provider_from_settings(settings), profile, model=settings.model)

    async def execute(
        self,
        description: str,
        *,
        format_sortie: str | None = None,
        keep_workspace: bool = False,
        system_prompt: str | None = None,
        mcp_serveurs: Sequence[ServeurMcp] = (),
        environ: Mapping[str, str] | None = None,
        politique: PolitiqueOutils | None = None,
        on_refus: Callable[[str, str], None] | None = None,
        on_arbitrage_acte: ArbitreActe | None = None,
        on_activite: Callable[[str], None] | None = None,
        on_etapes: Callable[[Sequence[EtapeTache]], None] | None = None,
        on_arbitrage: Arbitre | None = None,
        credit_arbitrage: CreditArbitrage | None = None,
        projet: Projet | None = None,
        tache_id: str = "",
    ) -> AgentOutcome:
        """Réalise la tâche `description` de bout en bout et renvoie le livrable.

        Ouvre l'espace de travail de la tâche (jetable, ou dérivé de `projet` —
        cf. plus bas), y lance l'exécution agentique du fournisseur,
        puis **capture les fichiers produits** avant que l'espace ne soit nettoyé (sauf
        `keep_workspace=True`). Lève `ValueError` si la description est vide ; propage
        `UnsupportedCapability` si le fournisseur n'exécute pas d'agent outillé.

        `system_prompt` remplace, pour **cette exécution**, le prompt système du
        runtime : c'est le canal de l'application à chaud des playbooks (#78) —
        l'exécuteur passe la version courante du playbook stocké, sans reconstruire
        le runtime. None : le prompt câblé à la construction (comportement d'origine).

        `mcp_serveurs` (#104) sont les serveurs MCP déclarés par l'agent pour
        **cette exécution** (relus à chaud par l'exécuteur, comme le playbook) :
        leurs références `${VAR}` sont résolues ici — les secrets n'existent
        qu'en mémoire, jamais dans la déclaration — puis la liste est confiée au
        fournisseur, qui la monte via sa couche SDK. Un serveur non montable ou
        injoignable lève `McpServerUnavailable` (jamais relancé, ENF-06).

        `environ` (#109) est l'environnement de résolution de ces références :
        le **coffre scopé** de l'agent quand un `SecretStore` est câblé en
        amont (l'agent ne résout que ses propres secrets) ; None :
        l'environnement du process (comportement historique #104).

        `politique` (#110) est la politique allow/deny de l'agent, appliquée
        au **montage** : les outils intégrés refusés sont retirés de la
        session, un serveur MCP refusé n'est jamais monté (ses secrets ne
        sont pas même résolus). Elle est aussi confiée au fournisseur pour le
        **refus au vol** du reste (outil MCP refusé individuellement) —
        chaque refus est signalé via `on_refus(outil, raison)`, le canal de
        traçage de l'appelant. None : aucune politique (comportement
        historique).

        `on_arbitrage_acte` (#583) traverse ce runtime sans qu'il en fasse rien,
        comme `on_refus` : c'est le fournisseur qui suspend l'appel classé `ask`
        (son hook est le seul point de contrôle avant l'acte) et l'appelant qui
        compose la demande, la soumet au validateur et hérite du fail-safe. Le
        runtime n'est ni l'un ni l'autre — il relie les deux. None : aucun canal,
        et un outil `ask` est alors refusé par le fournisseur, jamais approuvé.
        À ne pas confondre avec `on_arbitrage` (#582) plus bas : l'un intercepte
        un acte, l'autre relaie une demande de l'agent.

        `on_activite` (#479) est le second canal de traçage, et il traverse ce
        runtime sans qu'il en fasse rien : ce que l'agent fait **pendant** sa
        tâche est observé par le fournisseur, seul à voir le flux du SDK, et
        consigné par l'appelant, seul à connaître la tâche et son run. Le runtime
        n'est ni l'un ni l'autre — il ne fait que relier les deux, comme pour
        `on_refus`.

        `on_etapes` (#489) est le troisième, et il traverse ce runtime pour la
        même raison : la **checklist** de l'agent est observée par le fournisseur
        (dans l'entrée de ses appels `TodoWrite`) et réconciliée par l'appelant,
        seul à connaître l'ossature que le plan avait annoncée. Le runtime ne
        tient aucun état de checklist — il n'en verrait qu'une exécution, et
        l'avancement doit survivre aux relances.

        `on_arbitrage` (#582) est le quatrième et traverse de même — mais dans
        l'autre sens : c'est l'agent qui **demande** l'arbitrage, le fournisseur
        qui lui expose l'outil, et l'appelant qui tient le garde-fou et le
        journal. Le runtime n'est ni l'un ni l'autre, et surtout il n'est pas
        celui qui décide : la décision appartient au `Guardrails` du moteur, seul
        endroit où vit le fail-safe.

        `credit_arbitrage` (#584) traverse aussi, et c'est le seul des cinq qui
        ne porte ni observation ni décision mais du **temps** : le fournisseur y
        ouvre une fenêtre autour de chaque attente d'arbitrage, l'appelant en
        déduit le délai qu'il a posé sur la tâche. Le runtime, une fois de plus,
        n'est ni celui qui mesure ni celui qui décompte. None : le temps
        d'arbitrage reste compté dans celui de la tâche, comme avant #584.

        `projet` (#224, EF-36) est le **projet dans lequel la tâche travaille** :
        l'espace de travail en est alors dérivé — worktree Git sur la branche
        `maestro/<tache_id>` si le projet est versionné, copie de son périmètre
        sinon — et jamais sa racine elle-même (`maestro.sandbox.projet`). None
        (une tâche sans `projet_id`) : le répertoire temporaire vide d'avant.
        `tache_id` ne sert qu'à nommer cette branche et ce répertoire.

        Deux autres choses en dépendent (#226), inertes elles aussi sans lui :
        les **secrets du projet** sont enregistrés auprès de la rédaction (#109)
        avant que l'agent ne démarre, et le projet est passé au fournisseur, qui
        en a besoin pour **monter** cet espace en mode isolé sans jamais monter
        la racine (`maestro.sandbox.container`).
        """
        description = description.strip()
        if not description:
            raise ValueError(
                f"La description de la tâche confiée au rôle {self._profile.role} est vide."
            )

        prompt = _build_prompt(self._profile, description, format_sortie)
        outils = self._tools if politique is None else politique.filtre_outils(self._tools)
        if politique is not None:
            mcp_serveurs = [s for s in mcp_serveurs if politique.serveur_autorise(s.nom)]
        # Résolution avant d'ouvrir l'espace : une déclaration non montable
        # échoue proprement sans créer (ni nettoyer) de répertoire de travail.
        montables = resolus(mcp_serveurs, os.environ if environ is None else environ)
        # Avant d'ouvrir l'espace, jamais après (#226) : les valeurs des gisements
        # de secrets du projet sont enregistrées auprès de la rédaction (#109),
        # faute de quoi le `.env` d'un projet tiers — que ni les variables de
        # Maestro ni les motifs de clés connus n'attrapent — ressortirait en clair
        # dans un résumé d'agent ou une trace.
        if projet is not None:
            enregistre_secrets_du_projet(projet)
        with espace_de_travail(
            projet,
            tache_id=tache_id,
            prefix=self._profile.workspace_prefix,
            keep=keep_workspace,
        ) as ws:
            resume = await self._provider.run_agent(
                prompt,
                model=self._model,
                system_prompt=system_prompt or self._system_prompt,
                workspace=ws.path,
                tools=outils,
                mcp_serveurs=montables,
                politique=politique,
                on_refus=on_refus,
                on_arbitrage_acte=on_arbitrage_acte,
                on_activite=on_activite,
                on_etapes=on_etapes,
                on_arbitrage=on_arbitrage,
                credit_arbitrage=credit_arbitrage,
                plafond_tours=self._plafond_tours,
                projet=projet,
            )
            # Capture *dans* le contexte : hors `keep`, l'espace disparaît à la sortie.
            fichiers = ws.produced_files()
            return AgentOutcome(
                role=self._profile.role,
                resume=resume.strip(),
                fichiers=fichiers,
                workspace=str(ws.path),
            )


def _build_prompt(profile: RoleProfile, description: str, format_sortie: str | None) -> str:
    """Compose le message confié à l'agent : la tâche encadrée par les consignes du rôle."""
    lignes = [profile.intro_tache, "", description, "", profile.consignes]
    if format_sortie:
        lignes += ["", f"Format de sortie attendu : {format_sortie}"]
    lignes += ["", profile.consigne_finale]
    return "\n".join(lignes)
