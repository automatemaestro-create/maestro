"""Lecture des playbooks « du code » — les documents Markdown livrés avec le paquet (#295).

Le playbook d'un rôle (docs/04 §1) est un **document Markdown structuré** : mission, entrées
attendues, méthode, critères de « terminé », garde-fous, format de sortie. Jusqu'ici les
prompts systèmes effectifs en étaient une version dégradée de trois paragraphes, écrite en dur
dans le module de chaque rôle (`maestro.agents.developer` et consorts) : le modèle du produit
et le code n'étaient pas au même endroit, et une chaîne Python ne se relit ni ne se diffe
comme un document. Ce module lit ces documents là où ils vivent désormais —
`maestro/agents/playbooks_defaut/<agent>.md`, livrés avec le paquet.

Deux fragments sont **partagés par tous les rôles**, pour n'exister qu'une fois :

- `_socle.md` — le **régime sénior** (#293) : ce que l'agent décide seul, ce qu'il remonte au
  lieu de le décider, et ce qu'il rend (décisions & arbitrages, recommandations). Il vaut pour
  les **deux** chemins d'exécution — runtime outillé et exécution texte du catalogue.
- `_cadre_outille.md` — le cadre propre à l'exécution **outillée** : répertoire de travail
  isolé, livrable matérialisé en fichiers, rien qui survive à la tâche. L'exécution texte du
  catalogue n'a pas d'outils : elle ne le charge pas.

Les documents de rôle les appellent par les marqueurs `{{socle}}` et `{{cadre}}`, substitués à
la lecture. Un marqueur inconnu (ou une accolade double laissée dans le texte) est une erreur
franche : mieux vaut un import qui échoue qu'un prompt système servi avec un trou dedans.

Ce module **n'importe rien du paquet** — ni le catalogue, ni les profils de rôle, ni le
stockage versionné. C'est ce qui lui permet d'être appelé par les uns comme par les autres
sans boucle d'import : `maestro.agents.playbooks` construit `PLAYBOOK_DEFAUTS` dessus, chaque
module de rôle y prend son `prompt_systeme`, et `maestro.agents.catalog` y prend le socle.

⚠ À ne pas confondre avec `core/playbooks/` (voir `maestro.agents.playbooks`), le dépôt
**versionné** des éditions humaines : ce qui est ici est le **repli** — ce que le moteur charge
tant que personne n'a rien publié.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

#: Racine des documents livrés avec le paquet (un fichier par rôle, plus les fragments
#: partagés préfixés d'un souligné).
RACINE = Path(__file__).resolve().parent / "playbooks_defaut"

#: Le tronc commun du régime sénior, partagé par les deux chemins d'exécution.
FRAGMENT_SOCLE = "_socle"

#: Le cadre d'exécution outillée (espace de travail isolé), réservé aux runtimes.
FRAGMENT_CADRE = "_cadre_outille"

#: Les marqueurs de substitution admis dans un document de rôle, et le fragment qu'ils
#: appellent. Volontairement fermé : un marqueur hors de cette table lève.
_MARQUEURS = {"socle": FRAGMENT_SOCLE, "cadre": FRAGMENT_CADRE}

#: Un marqueur dans le texte d'un rôle : `{{socle}}`, `{{cadre}}`.
_MARQUEUR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

#: La clause de rendu de compte, commune à tous les rôles : le pendant, dans le **message
#: de tâche** (`RoleProfile.consigne_finale`), de la section « Ce que tu rends » du socle.
#: Elle y est répétée à dessein — le prompt système cadre le rôle, la consigne finale cadre
#: le livrable attendu, et c'est celle-là que l'agent relit juste avant de conclure.
CONSIGNE_RENDU_COMPTE = (
    "puis rends deux sections : « Décisions & arbitrages » (ce que tu as tranché, pourquoi, "
    "les options écartées) et « Recommandations » (suite conseillée, angles morts, ce qui "
    "t'a manqué, ce qui demande un arbitrage humain)."
)


@cache
def fragment(nom: str) -> str:
    """Le fragment partagé `nom` (`_socle`, `_cadre_outille`), texte brut sans substitution.

    Lève `FileNotFoundError` si le fichier manque — un playbook amputé de son régime ne
    doit pas partir en prompt système.
    """
    return _lire(nom)


def socle() -> str:
    """Le tronc commun du régime sénior (#293), tel quel.

    Servi à part pour l'exécution **texte** du catalogue (`maestro.agents.catalog`), qui
    n'a ni outils ni espace de travail : elle prend le régime sans le cadre outillé.
    """
    return fragment(FRAGMENT_SOCLE)


@cache
def playbook_du_code(agent: str) -> str:
    """Le playbook « du code » de `agent` : son document, marqueurs partagés substitués.

    C'est le repli du rôle : ce que `PLAYBOOK_DEFAUTS` expose et ce que le moteur charge
    tant qu'aucune version n'a été publiée dans `core/playbooks/` (#76). Lève
    `FileNotFoundError` si le document manque, `ValueError` sur un marqueur inconnu.
    """
    texte = _MARQUEUR.sub(_substitue, _lire(agent))
    if "{{" in texte:
        raise ValueError(f"marqueur mal formé dans le playbook du rôle {agent!r}.")
    return texte


@cache
def roles_du_code() -> tuple[str, ...]:
    """Les rôles qui ont un playbook livré avec le paquet, par ordre alphabétique.

    Les fragments partagés (préfixés d'un souligné) n'en sont pas : ils n'ont pas de rôle.
    """
    return tuple(sorted(c.stem for c in RACINE.glob("*.md") if not c.name.startswith("_")))


def _substitue(m: re.Match[str]) -> str:
    """Remplace un marqueur `{{…}}` par son fragment partagé (lève s'il est inconnu)."""
    cle = m.group(1)
    if cle not in _MARQUEURS:
        raise ValueError(
            f"marqueur de playbook inconnu : {{{{{cle}}}}} (attendus : "
            f"{', '.join(sorted(_MARQUEURS))})."
        )
    return fragment(_MARQUEURS[cle]).strip()


def _lire(nom: str) -> str:
    """Le texte d'un document du dossier, normalisé (UTF-8, sans blancs de fin)."""
    chemin = RACINE / f"{nom}.md"
    if not chemin.is_file():
        raise FileNotFoundError(f"playbook du code introuvable : {chemin}")
    return chemin.read_text(encoding="utf-8").strip()
