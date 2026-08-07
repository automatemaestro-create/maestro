"""Prompt système de l'orchestrateur — playbook « Chef de projet » (tickets #3, #298).

Matérialise la fiche `docs/04-specifications-agents.md §3.1` en instructions exécutables,
et fixe le **contrat de sortie** : un tableau JSON de tâches conformes à
`packages/shared/schemas/task.schema.json`. Le prompt est volontairement strict sur la
forme (JSON pur, champs exacts) parce que la sortie est ensuite parsée et validée par
`maestro.orchestrator.schema`.

Le playbook lui-même est un **document Markdown** (`playbook.md`, à côté de ce module)
depuis #298 : structuré, relisable et diffable, comme ceux des cinq rôles exécutants
(#295). Ce module ne fait plus que le charger et y substituer la fourchette de tâches.

⚠ Il vit **ici** et non dans `maestro/agents/playbooks_defaut/`, à dessein — c'était la
décision ouverte du ticket :

- ce dossier-là est le repli du **catalogue** (`PLAYBOOK_DEFAUTS` est construit sur
  `DEFAULT_AGENTS`) et la liste des agents que la Control Tower édite et versionne
  (`core/playbooks/<agent>/`). Le Chef de projet n'est ni dans le catalogue — il n'exécute
  pas de tâche — ni éditable : y déposer son document ferait mentir `roles_du_code()` et
  laisserait croire à un repli versionné qui n'existe pas ;
- il ne pourrait pas prendre le tronc commun `{{socle}}` de toute façon : la section « Ce
  que tu rends » du socle impose deux sections de prose au compte-rendu, ce qui contredit
  frontalement le « réponds UNIQUEMENT par un tableau JSON » d'ici. Sans fragment partagé
  à reprendre, la raison principale de cohabiter disparaît. Son régime sénior est donc
  écrit dans son propre document, adapté à sa seule voie de sortie : ses arbitrages ne se
  rendent pas en prose, ils se lisent dans les tâches qu'il émet.

D'où un chargeur local plutôt qu'un import de `maestro.agents.playbook_du_code`, dont la
racine est celle des rôles. Il en garde le principe : substitution de marqueurs `{{…}}` sur
une table **fermée**, et échec franc sur un marqueur inconnu ou une accolade laissée dans le
texte — mieux vaut un import qui échoue qu'un prompt système servi avec un trou dedans.
"""

from __future__ import annotations

import re
from pathlib import Path

#: Fourchette visée. Guidage, pas une règle de schéma, et depuis #298 le playbook la
#: présente comme une **conséquence** du découpage plutôt que comme un quota à remplir.
#: MIN reste un vrai plancher : le critère d'acceptation du ticket #6 demande que la
#: boucle assigne et exécute **au moins 3** tâches. MAX borne le découpage inutilement fin.
MIN_TASKS = 3
MAX_TASKS = 5

#: Le playbook « du code » du Chef de projet, livré avec le paquet (cf. `package-data`
#: dans `pyproject.toml` — sans quoi une roue s'installerait sans lui).
CHEMIN_PLAYBOOK = Path(__file__).resolve().parent / "playbook.md"

#: Les substitutions admises dans le document. Volontairement fermée : un marqueur hors
#: de cette table lève, plutôt que de partir tel quel dans le prompt système.
_VALEURS = {"min_taches": str(MIN_TASKS), "max_taches": str(MAX_TASKS)}

#: Un marqueur dans le document : `{{min_taches}}`, `{{max_taches}}`.
_MARQUEUR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")


def _substitue(m: re.Match[str]) -> str:
    """Remplace un marqueur `{{…}}` par sa valeur (lève s'il est inconnu)."""
    cle = m.group(1)
    if cle not in _VALEURS:
        raise ValueError(
            f"marqueur de playbook inconnu : {{{{{cle}}}}} (attendus : "
            f"{', '.join(sorted(_VALEURS))})."
        )
    return _VALEURS[cle]


def _lire_playbook() -> str:
    """Le playbook du Chef de projet, marqueurs substitués — le prompt système effectif."""
    if not CHEMIN_PLAYBOOK.is_file():
        raise FileNotFoundError(f"playbook du Chef de projet introuvable : {CHEMIN_PLAYBOOK}")
    texte = _MARQUEUR.sub(_substitue, CHEMIN_PLAYBOOK.read_text(encoding="utf-8").strip())
    if "{{" in texte:
        raise ValueError("marqueur mal formé dans le playbook du Chef de projet.")
    return texte


ORCHESTRATOR_SYSTEM_PROMPT = _lire_playbook()


def build_user_prompt(objective: str) -> str:
    """Compose le message utilisateur transmis au modèle pour un objectif donné."""
    cleaned = objective.strip()
    return (
        "Découpe l'objectif suivant en un plan de tâches raisonné, en respectant "
        "strictement le format JSON imposé par tes instructions. Chaque description "
        "porte ses quatre sections : objectif, périmètre et limites, latitude de "
        "décision, critères de réussite.\n\n"
        f"Objectif :\n{cleaned}"
    )
