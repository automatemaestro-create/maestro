"""Rédaction des secrets avant journalisation (ticket #8).

Défense en profondeur pour le critère « aucun secret dans les logs » : même si un
secret se glisse dans un prompt ou un livrable, il est masqué avant d'atteindre le
journal. Deux filets complémentaires :

1. les **valeurs** des variables d'environnement sensibles (clé API, tokens, URLs
   de connexion) sont remplacées où qu'elles apparaissent ;
2. les **motifs** de clés connus (`sk-ant-…`, `sk-…`) sont masqués même si la
   valeur ne vient pas de l'environnement courant.
"""

from __future__ import annotations

import os
import re

#: Marqueur substitué à chaque secret détecté.
MARQUEUR_SECRET = "[secret masqué]"

#: Variables d'environnement dont la valeur ne doit jamais atteindre les logs.
#: Les URLs de connexion (BDD, Redis) peuvent embarquer un mot de passe.
_ENV_SENSIBLES: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "DATABASE_URL",
    "REDIS_URL",
)

#: Longueur minimale d'une valeur d'environnement pour être masquée : en deçà,
#: la substitution ferait plus de faux positifs que de protection.
_LONGUEUR_MIN = 8

#: Motifs de secrets reconnaissables hors environnement (clés Anthropic `sk-ant-…`,
#: dont les tokens OAuth `sk-ant-oat…`, et clés génériques `sk-…` assez longues).
_MOTIFS_SECRETS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
)


def redact_secrets(text: str) -> str:
    """Renvoie `text` expurgé des secrets connus (valeurs d'env + motifs de clés)."""
    if not text:
        return text
    for variable in _ENV_SENSIBLES:
        valeur = os.getenv(variable)
        if valeur and len(valeur) >= _LONGUEUR_MIN:
            text = text.replace(valeur, MARQUEUR_SECRET)
    for motif in _MOTIFS_SECRETS:
        text = motif.sub(MARQUEUR_SECRET, text)
    return text
