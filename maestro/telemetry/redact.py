"""Rédaction des secrets avant journalisation (ticket #8, renforcée par #109).

Défense en profondeur pour le critère « aucun secret dans les logs » : même si un
secret se glisse dans un prompt ou un livrable, il est masqué avant d'atteindre le
journal. Trois filets complémentaires :

1. les **valeurs** des variables d'environnement sensibles (clé API, tokens, URLs
   de connexion) sont remplacées où qu'elles apparaissent ;
2. les **motifs** de clés connus (`sk-ant-…`, `sk-…`) sont masqués même si la
   valeur ne vient pas de l'environnement courant ;
3. les **secrets servis** (#109) : toute valeur ayant transité par le coffre par
   agent (`maestro.agents.secrets.SecretStore`) ou résolue depuis une référence
   `${VAR}` d'une déclaration MCP est enregistrée ici (`enregistre_secret`) et
   masquée où qu'elle réapparaisse — la liste n'est plus figée au code, elle
   suit ce qui a réellement été confié aux agents.
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
    "FIGMA_OAUTH_TOKEN",
    # Les deux forges : GitHub équipe l'agent QA depuis #412, GitLab reste au
    # catalogue et sert l'archive. Une clé qui cesse d'être le défaut ne cesse
    # pas d'être un secret — on ne la retire pas de cette liste.
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "REDIS_URL",
    "SLACK_BOT_TOKEN",
)

#: Longueur minimale d'une valeur d'environnement pour être masquée : en deçà,
#: la substitution ferait plus de faux positifs que de protection.
_LONGUEUR_MIN = 8

#: Registre des secrets *servis* (#109) : valeurs passées par le coffre par agent
#: ou résolues depuis une référence `${VAR}` au montage MCP. Vit le temps du
#: process — jamais persisté, jamais réémis : consulté seulement pour masquer.
_SECRETS_SERVIS: set[str] = set()


def enregistre_secret(valeur: str) -> None:
    """Enregistre une valeur servie comme secret : masquée par `redact_secrets`.

    Appelé par le coffre par agent (`maestro.agents.secrets`) et par la
    résolution des références MCP (`maestro.agents.mcp.resolus`) : tout secret
    réellement confié à un agent est masqué s'il réapparaît en sortie, même
    s'il ne figure pas dans la liste figée `_ENV_SENSIBLES`. Idempotent ; les
    valeurs trop courtes sont ignorées (même seuil anti-faux-positifs).
    """
    if valeur and len(valeur) >= _LONGUEUR_MIN:
        _SECRETS_SERVIS.add(valeur)

#: Motifs de secrets reconnaissables hors environnement (clés Anthropic `sk-ant-…`,
#: dont les tokens OAuth `sk-ant-oat…`, clés génériques `sk-…` assez longues,
#: tokens GitLab `glpat-…` — pilote MCP tickets #106 —, tokens GitHub
#: `ghp_…`/`github_pat_…` — serveur MCP de forge #412 — et tokens Slack
#: `xoxb-…`/`xoxp-…` — pilote MCP Slack #105).
_MOTIFS_SECRETS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{8,}"),
    # GitHub, deux familles : `github_pat_…` (fine-grained, la forme que
    # core/mcp/README.md recommande) et les classiques `ghp_`/`gho_`/`ghu_`/
    # `ghs_`/`ghr_`. Le fine-grained d'abord — `gh[pousr]_` ne le couvre pas.
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[a-z]-[A-Za-z0-9-]{10,}"),
)


def redact_secrets(text: str) -> str:
    """Renvoie `text` expurgé des secrets connus (valeurs d'env + motifs de clés)."""
    if not text:
        return text
    for variable in _ENV_SENSIBLES:
        valeur = os.getenv(variable)
        if valeur and len(valeur) >= _LONGUEUR_MIN:
            text = text.replace(valeur, MARQUEUR_SECRET)
    # Secrets servis (#109), les plus longs d'abord : un secret préfixe d'un
    # autre ne doit pas laisser de suffixe en clair après substitution.
    for valeur in sorted(_SECRETS_SERVIS, key=len, reverse=True):
        text = text.replace(valeur, MARQUEUR_SECRET)
    for motif in _MOTIFS_SECRETS:
        text = motif.sub(MARQUEUR_SECRET, text)
    return text
