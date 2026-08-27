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

- **registre de configuration** — réglages produit éditables, versionnés côté
  serveur (couche 1 du cadrage sécurité #182) ;
- **propositions de playbook globales** — l'agrégat transverse qui alimente
  badge et notifications (chantier *Journal*, item 8/9).

Trois contrats sont **partis** d'ici, dans l'ordre où leur lot a été livré : les
**exécutions** (#185, `maestro.controltower.executions`), le **journal
requêtable** (#478, `maestro.controltower.journal`) puis le **flux SSE d'un fil
de chat** (#268, `maestro.controltower.chat.ServiceChat.diffuser`). C'est le
cycle de vie normal d'une fixture — elle tient la place d'une implémentation,
puis lui cède la sienne : garder les deux ferait de la démo un écran nourri de
faux à côté d'un vrai, et de la forme figée une seconde source à tenir d'accord.

Les quatre types de trame du flux sont **réexportés** ici (`FRAGMENT_CHAT_*`) :
ils vivent désormais avec le canal qui les émet, et ce module en garde le nom
d'import pour ne pas casser ce qui les lisait à cette adresse.

La référence de ticket externe (#187) portée par une tâche n'est **pas** ici :
c'est un champ de données (`Event.ticket`, `EtatTache.ticket`) servi par
`GET /api/taches`, que la démo pose sur une tâche du scénario. Il en va de même
du `projet_id` (#222).
"""

from __future__ import annotations

from typing import Any

from maestro.controltower.chat import (
    FRAGMENT_CHAT_DEBUT,
    FRAGMENT_CHAT_DELTA,
    FRAGMENT_CHAT_ERREUR,
    FRAGMENT_CHAT_FIN,
)

#: Les types de trame du flux de chat, définis avec le canal qui les émet
#: (`maestro.controltower.chat`) depuis #268 et réexportés ici : le nom d'import
#: de #183 continue de répondre, sans qu'il existe deux vocabulaires.
__all__ = [
    "FRAGMENT_CHAT_DEBUT",
    "FRAGMENT_CHAT_DELTA",
    "FRAGMENT_CHAT_ERREUR",
    "FRAGMENT_CHAT_FIN",
    "FixturesControlTower",
]


class FixturesControlTower:
    """Les données factices des routes de contrat v2 (#183), servies par la démo.

    Chaque méthode rend une forme **JSON-sérialisable** conforme au contrat
    documenté (docs/05 §6) et typé (`apps/web/lib/types.ts`). Sans état : on fige
    la forme de la réponse, pas le comportement réel (qui vient dans les lots
    dédiés de la Phase 5). La voie front code contre ces formes ; le backend réel
    les remplira à contrat identique — les **exécutions** (#185) puis le
    **journal requêtable** (#478) l'ont déjà fait, d'où leur absence ici.
    """

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
