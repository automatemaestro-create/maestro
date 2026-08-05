"""Backend de fixtures des contrats d'API v2 (ticket #183).

Le cadrage #182 découpe les prochaines améliorations en deux voies **parallèles**
— Phase 5 (backend) et Phase 6 (front). Pour qu'elles soient réellement
indépendantes, la voie front ne doit pas attendre que les routes existent : ce
module **fige les formes JSON** des routes à venir et les **sert en données
factices**, cohérentes avec le scénario de la démo (`maestro.controltower.demo`).

Les routes correspondantes sont déclarées dans `create_app` (le contrat est
stable) mais **répondent 501 tant que leur lot n'est pas livré** ; branchées sur
ces fixtures — ce que fait la démo — elles rendent des données réalistes, et la
voie front peut coder contre elles le jour même (cadrage #182).

Périmètre figé ici (aucune implémentation réelle — elle vient dans les lots de la
Phase 5, #184+) :

- **exécutions** — lancement, suivi (liste + résumé) et annulation (`#185`) ;
- **journal requêtable** — filtres (agent / type / run / période), tri et
  pagination (chantier *Journal*, Phase 5) ;
- **registre de configuration** — réglages produit éditables, versionnés côté
  serveur (couche 1 du cadrage sécurité #182) ;
- **propositions de playbook globales** — l'agrégat transverse qui alimente
  badge et notifications (chantier *Journal*, item 8/9) ;
- **flux SSE d'un fil de chat** — les fragments d'une réponse en streaming
  (chantier *Conversation*, items 2/4/12).

La référence de ticket externe (#187) portée par une tâche n'est **pas** ici :
c'est un champ de données (`Event.ticket`, `EtatTache.ticket`) servi par
`GET /api/taches`, que la démo pose sur une tâche du scénario. Il en va de même
du `projet_id` (#222) — sauf pour le **journal**, qui n'est servi que d'ici :
ses entrées le portent, et son filtre `projet` est implémenté ci-dessous.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from maestro.controltower.chat import AUTEUR_AGENT
from maestro.controltower.events import (
    EVENEMENT_AGENT_ACTIVITE,
    EVENEMENT_MESSAGE_INTER_AGENTS,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
)

#: Clés de tri du journal requêtable (chantier *Journal*, Phase 5) et sens.
TRI_JOURNAL_HORODATAGE = "horodatage"
TRI_JOURNAL_AGENT = "agent"
TRI_JOURNAL_TYPE = "type"
TRIS_JOURNAL = (TRI_JOURNAL_HORODATAGE, TRI_JOURNAL_AGENT, TRI_JOURNAL_TYPE)
ORDRE_ASC = "asc"
ORDRE_DESC = "desc"
ORDRES_JOURNAL = (ORDRE_ASC, ORDRE_DESC)
#: Taille de page par défaut et plafond dur (au-delà, 422 : pas de scan illimité).
TAILLE_PAGE_DEFAUT = 50
TAILLE_PAGE_MAX = 200

#: Types de fragments d'un flux SSE de chat (chantier *Conversation*, Phase 5) :
#: ouverture du flux, incrément de texte, clôture (message complet), erreur.
FRAGMENT_CHAT_DEBUT = "debut"
FRAGMENT_CHAT_DELTA = "fragment"
FRAGMENT_CHAT_FIN = "fin"
FRAGMENT_CHAT_ERREUR = "erreur"

#: Le run du scénario de la démo (`maestro.controltower.demo`) — les fixtures
#: s'y rattachent pour rester cohérentes avec ce que sert déjà `GET /api/taches`.
_RUN_DEMO = "demo-live"

#: Le projet du même scénario (#222) : le run de la démo travaille dans un
#: projet, ce qui rend le filtre `?projet=` démontrable dès les fixtures. Une
#: entrée peut le porter à None — un travail sans projet reste normal, c'est le
#: comportement d'avant ce lot.
_PROJET_DEMO = "prj-demo"


class FixturesControlTower:
    """Les données factices des routes de contrat v2 (#183), servies par la démo.

    Chaque méthode rend une forme **JSON-sérialisable** conforme au contrat
    documenté (docs/05 §6) et typé (`apps/web/lib/types.ts`). Sans état : on fige
    la forme de la réponse, pas le comportement réel (qui vient dans les lots
    dédiés de la Phase 5). La voie front code contre ces formes ; le backend réel
    les remplira à contrat identique — les **exécutions** l'ont déjà fait (#185,
    `maestro.controltower.executions`), d'où leur absence ici.
    """

    # -------------------------------------------------------------------- journal

    def journal(
        self,
        *,
        agent: str | None = None,
        type: str | None = None,
        run_id: str | None = None,
        projet: str | None = None,
        depuis: str | None = None,
        jusqua: str | None = None,
        tri: str = TRI_JOURNAL_HORODATAGE,
        ordre: str = ORDRE_DESC,
        page: int = 1,
        taille: int = TAILLE_PAGE_DEFAUT,
    ) -> dict[str, Any]:
        """Une page du journal requêtable (`GET /api/journal`) : filtres, tri, pagination.

        Filtre les entrées par `agent`, `type`, `run_id`, `projet` (#222) et fenêtre temporelle
        (`depuis`/`jusqua`, ISO-8601, bornes incluses, comparaison lexicale des
        horodatages ISO), trie sur `tri`/`ordre`, puis découpe en pages de
        `taille` (1-indexé). `total` est le compte **après filtres, avant
        pagination** ; `pages` le nombre de pages. Les paramètres sont réputés
        déjà validés par la route (tri/ordre connus, page ≥ 1, taille bornée).
        """
        entrees = list(_ENTREES_JOURNAL)
        if agent:
            entrees = [e for e in entrees if e["agent"] == agent]
        if type:
            entrees = [e for e in entrees if e["type"] == type]
        if run_id:
            entrees = [e for e in entrees if e["run_id"] == run_id]
        if projet:
            # Une entrée sans projet (`None`) ne relève d'aucun : elle sort de
            # toute vue filtrée plutôt que d'être rattachée au hasard (#222).
            entrees = [e for e in entrees if e["projet_id"] == projet]
        if depuis:
            entrees = [e for e in entrees if e["horodatage"] >= depuis]
        if jusqua:
            entrees = [e for e in entrees if e["horodatage"] <= jusqua]
        entrees.sort(key=lambda e: (e[tri], e["id"]), reverse=(ordre == ORDRE_DESC))
        total = len(entrees)
        pages = (total + taille - 1) // taille if total else 0
        debut = (page - 1) * taille
        return {
            "entrees": entrees[debut : debut + taille],
            "total": total,
            "page": page,
            "taille": taille,
            "pages": pages,
        }

    # -------------------------------------------------------------- configuration

    def configuration(self) -> dict[str, Any]:
        """Le registre de configuration éditable (`GET /api/configuration`).

        Les **réglages produit** (couche 1 du cadrage sécurité #182) : fournisseur,
        modèle, plafonds, isolation, intégrations, rétention — chacun avec son
        type, sa valeur courante (masquée si secret), sa valeur par défaut, et s'il
        est modifiable depuis l'UI (liste blanche stricte : aucune écriture
        arbitraire de variable d'environnement). `version` est celle du registre
        versionné (append-only) ; `erreur` porte la cause si le stockage est
        illisible (même contrat de visibilité que `mcp_erreur`).
        """
        return {"reglages": list(_REGLAGES_CONFIGURATION), "version": 3, "erreur": None}

    # ----------------------------------------------------- propositions de playbook

    def propositions_playbook(self) -> list[dict[str, Any]]:
        """Les propositions d'auto-amélioration **tous agents confondus** (route globale).

        L'agrégat transverse (#111 exposé par agent, ici global) qui alimente le
        badge d'attente et les notifications (cadrage #182, items 8/9). Chaque
        entrée est une `PropositionPlaybook` (numéro de brouillon, provenance,
        justification) enrichie du `role` de l'agent — de quoi l'afficher sans un
        aller-retour par le catalogue.
        """
        return list(_PROPOSITIONS_PLAYBOOK)

    # ----------------------------------------------------------------- flux de chat

    def flux_chat(self, agent: str, role: str, contenu: str) -> Iterator[dict[str, Any]]:
        """Les fragments d'une réponse de chat en streaming (`GET /api/chat/{agent}/flux`, SSE).

        Rend la séquence de trames d'un flux `text/event-stream` : un `debut`
        (ouverture), des `fragment` (incréments de texte, `delta`), puis un `fin`
        portant le `MessageChat` complet reconstitué. La démo répond par un texte
        scripté découpé en mots — la forme des trames est le contrat ; le contenu
        réel (modèle en streaming) vient dans le lot *Conversation* de la Phase 5.
        """
        reponse = (
            f"Bonjour, ici {role or agent}. J'ai bien reçu votre message et je le "
            "traite morceau par morceau — ceci est un flux de démonstration."
        )
        yield {
            "type": FRAGMENT_CHAT_DEBUT,
            "agent": agent,
            "auteur": AUTEUR_AGENT,
            "delta": "",
            "message": None,
        }
        mots = reponse.split(" ")
        for i, mot in enumerate(mots):
            yield {
                "type": FRAGMENT_CHAT_DELTA,
                "agent": agent,
                "auteur": AUTEUR_AGENT,
                "delta": mot if i == 0 else f" {mot}",
                "message": None,
            }
        yield {
            "type": FRAGMENT_CHAT_FIN,
            "agent": agent,
            "auteur": AUTEUR_AGENT,
            "delta": "",
            "message": {
                "agent": agent,
                "auteur": AUTEUR_AGENT,
                "contenu": reponse,
                "horodatage": "2026-07-31T10:10:00+00:00",
            },
        }


def _entree(
    id: str,
    *,
    type: str,
    horodatage: str,
    agent: str = "",
    role: str = "",
    run_id: str = _RUN_DEMO,
    tache_id: str = "",
    statut: str = "",
    detail: str = "",
    projet_id: str | None = _PROJET_DEMO,
) -> dict[str, Any]:
    """Une entrée de journal figée — un événement persisté doté d'un id stable."""
    return {
        "id": id,
        "type": type,
        "run_id": run_id,
        "tache_id": tache_id,
        "agent": agent,
        "role": role,
        "statut": statut,
        "detail": detail,
        "projet_id": projet_id,
        "horodatage": horodatage,
    }


#: Le journal figé : la trace du scénario de la démo, requêtable en fixtures.
_ENTREES_JOURNAL: tuple[dict[str, Any], ...] = (
    _entree(
        "j-0001",
        type=EVENEMENT_AGENT_ACTIVITE,
        horodatage="2026-07-30T09:00:04+00:00",
        agent="orchestrateur",
        role="Orchestrateur",
        detail="Objectif décomposé en 4 tâches (mini-CRM : schéma, API, CI, maquette)",
    ),
    _entree(
        "j-0002",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:00:12+00:00",
        agent="bdd",
        role="Base de données",
        tache_id="demo-t1",
        statut="en_cours",
        detail="Concevoir le schéma SQL de la table contacts",
    ),
    _entree(
        "j-0003",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:00:50+00:00",
        agent="bdd",
        role="Base de données",
        tache_id="demo-t1",
        statut="terminee",
        detail="Schéma prêt (table contacts + migration)",
    ),
    _entree(
        "j-0004",
        type=EVENEMENT_MESSAGE_INTER_AGENTS,
        horodatage="2026-07-30T09:00:52+00:00",
        agent="bdd",
        role="Base de données",
        detail="→ developpeur : schéma prêt, la table `contacts` est disponible",
    ),
    _entree(
        "j-0005",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:01:05+00:00",
        agent="developpeur",
        role="Développeur",
        tache_id="demo-t2",
        statut="en_cours",
        detail="Implémenter l'API REST des contacts (créer / lister)",
    ),
    _entree(
        "j-0006",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:02:06+00:00",
        agent="developpeur",
        role="Développeur",
        tache_id="demo-t2",
        statut="terminee",
        detail="API créer/lister livrée",
    ),
    _entree(
        "j-0007",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:02:20+00:00",
        agent="devops",
        role="DevOps",
        tache_id="demo-t3",
        statut="en_cours",
        detail="Pipeline CI et déploiement de l'API",
    ),
    _entree(
        "j-0008",
        type=EVENEMENT_VALIDATION_DEMANDE,
        horodatage="2026-07-30T09:02:25+00:00",
        agent="devops",
        role="DevOps",
        tache_id="demo-t3",
        statut="en_attente",
        detail="validation requise avant déploiement",
    ),
    _entree(
        "j-0009",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:02:40+00:00",
        agent="designer",
        role="Designer",
        tache_id="demo-t4",
        statut="assignee",
        detail="Maquette de l'écran de gestion des contacts",
    ),
    _entree(
        "j-0010",
        type=EVENEMENT_TACHE_STATUT,
        horodatage="2026-07-30T09:03:00+00:00",
        agent="qa",
        role="QA / Testeur",
        tache_id="demo-qa",
        statut="terminee",
        detail="Vérification de santé de l'API (QA)",
    ),
)


def _reglage(
    cle: str,
    valeur: str,
    *,
    type: str,
    description: str,
    categorie: str,
    valeur_defaut: str,
    modifiable: bool = True,
    secret: bool = False,
    source: str = "defaut",
    version: int = 0,
    modifie_le: str | None = None,
) -> dict[str, Any]:
    """Un réglage produit figé (couche 1 du cadrage sécurité #182)."""
    return {
        "cle": cle,
        "valeur": valeur,
        "type": type,
        "description": description,
        "categorie": categorie,
        "valeur_defaut": valeur_defaut,
        "modifiable": modifiable,
        "secret": secret,
        "source": source,
        "version": version,
        "modifie_le": modifie_le,
    }


#: Les réglages produit éditables figés — la liste blanche stricte du cadrage #182.
_REGLAGES_CONFIGURATION: tuple[dict[str, Any], ...] = (
    _reglage(
        "fournisseur",
        "anthropic",
        type="chaine",
        description="Fournisseur de modèle par défaut des exécutants.",
        categorie="modele",
        valeur_defaut="anthropic",
        source="stockage",
        version=2,
        modifie_le="2026-07-28T11:20:00+00:00",
    ),
    _reglage(
        "modele",
        "claude-opus-4-8",
        type="chaine",
        description="Modèle par défaut des exécutants (vide : le défaut du fournisseur).",
        categorie="modele",
        valeur_defaut="claude-opus-4-8",
    ),
    _reglage(
        "plafond_cout_usd",
        "5.0",
        type="decimal",
        description="Plafond de coût (USD) d'une exécution avant arrêt du moteur.",
        categorie="plafonds",
        valeur_defaut="10.0",
        source="stockage",
        version=3,
        modifie_le="2026-07-29T08:05:00+00:00",
    ),
    _reglage(
        "plafond_tokens",
        "200000",
        type="entier",
        description="Plafond de tokens cumulés d'une exécution.",
        categorie="plafonds",
        valeur_defaut="500000",
    ),
    _reglage(
        "parallelisme",
        "3",
        type="entier",
        description="Nombre maximal de tâches exécutées en parallèle.",
        categorie="execution",
        valeur_defaut="4",
    ),
    _reglage(
        "timeout_tache_s",
        "600",
        type="entier",
        description="Time-out (secondes) d'une tâche avant abandon.",
        categorie="execution",
        valeur_defaut="900",
    ),
    _reglage(
        "isolation",
        "docker",
        type="chaine",
        description="Mode d'isolation de l'exécution des agents.",
        categorie="execution",
        valeur_defaut="docker",
    ),
    _reglage(
        "canal_slack",
        "#maestro-poc",
        type="chaine",
        description="Canal Slack des notifications (vide : notifications désactivées).",
        categorie="integrations",
        valeur_defaut="",
    ),
    _reglage(
        "retention_jours",
        "30",
        type="entier",
        description="Rétention (jours) des journaux et traces avant purge.",
        categorie="retention",
        valeur_defaut="30",
    ),
    _reglage(
        "cle_api_fournisseur",
        "••••••••",
        type="secret",
        description="Clé d'API du fournisseur — write-only, jamais renvoyée en clair (#132).",
        categorie="integrations",
        valeur_defaut="",
        secret=True,
        source="stockage",
        version=1,
        modifie_le="2026-07-20T16:00:00+00:00",
    ),
)


#: Les propositions de playbook figées, tous agents confondus (badge/notifications).
_PROPOSITIONS_PLAYBOOK: tuple[dict[str, Any], ...] = (
    {
        "agent": "qa",
        "role": "QA / Testeur",
        "version": 1,
        "cree_le": "2026-07-30T18:42:00+00:00",
        "provenance": "proposition",
        "justification": (
            "Deux tâches du run demo-live ont échoué faute d'avoir relancé les tests "
            "après correction : ajouter une étape de re-vérification systématique."
        ),
    },
    {
        "agent": "developpeur",
        "role": "Développeur",
        "version": 2,
        "cree_le": "2026-07-31T07:15:00+00:00",
        "provenance": "proposition",
        "justification": (
            "Réponses trop verbeuses relevées à l'analyse : préférer des livrables "
            "compacts et un résumé en tête."
        ),
    },
)
