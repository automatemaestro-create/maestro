"""Secret redaction before logging (ticket #8, hardened by #109).

Defence in depth for the "no secret in the logs" criterion: even if a secret
slips into a prompt or a deliverable, it is masked before it reaches the
journal. Three complementary nets:

1. the **values** of sensitive environment variables (API key, tokens,
   connection URLs) are replaced wherever they appear;
2. known key **patterns** (`sk-ant-…`, `sk-…`, private key blocks — #1264) are
   masked even when the value does not come from the current environment;
3. **served secrets** (#109): any value that went through the per-agent vault
   (`maestro.agents.secrets.SecretStore`) or was resolved from a `${VAR}`
   reference of an MCP declaration is registered here (`register_secret`) and
   masked wherever it shows up again — the list is not frozen in the code, it
   follows what was actually entrusted to the agents.
"""

from __future__ import annotations

import os
import re

#: Marker substituted for every detected secret.
SECRET_MARKER = "[secret masqué]"

#: Environment variables whose value must never reach the logs.
#: Connection URLs (database, Redis) can embed a password.
_SENSITIVE_ENV: tuple[str, ...] = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "DATABASE_URL",
    "FIGMA_OAUTH_TOKEN",
    # Both forges: GitHub equips the QA agent (#412), GitLab stays in the catalog
    # and serves the archive. A key that stops being the default does not stop
    # being a secret — it stays in this list.
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "REDIS_URL",
    "SLACK_BOT_TOKEN",
)

#: Minimum length of an environment value to be masked: below it, substitution
#: would cause more false positives than protection.
_MIN_LENGTH = 8

#: Registry of *served* secrets (#109): values that went through the per-agent
#: vault or were resolved from a `${VAR}` reference at MCP mount time. Lives as
#: long as the process — never persisted, never re-emitted: consulted only to
#: mask.
_SERVED_SECRETS: set[str] = set()


def register_secret(value: str) -> None:
    """Registers a served value as a secret: masked by `redact_secrets`.

    Called by the per-agent vault (`maestro.agents.secrets`) and by the
    resolution of MCP references (`maestro.agents.mcp.resolus`): every secret
    actually entrusted to an agent is masked if it reappears in an output, even
    when it is not in the frozen `_SENSITIVE_ENV` list. Idempotent; values that
    are too short are ignored (same false-positive threshold).
    """
    if value and len(value) >= _MIN_LENGTH:
        _SERVED_SECRETS.add(value)

#: Secret patterns recognizable outside the environment (Anthropic keys
#: `sk-ant-…`, OAuth tokens `sk-ant-oat…` included, long enough generic keys
#: `sk-…`, GitLab tokens `glpat-…` — ticket MCP driver #106 —, GitHub tokens
#: `ghp_…`/`github_pat_…` — forge MCP server #412 — and Slack tokens
#: `xoxb-…`/`xoxp-…` — Slack MCP driver #105).
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # A private key (SSH, TLS, PGP), whole block, first: it is not recognized by
    # a prefix but by its framing, and the pattern masks it in one piece before
    # the others bite into it. Since sources are read by content (#1163), an
    # extensionless `id_rsa` — which no `porte_des_secrets` name flags — comes in
    # as text: this is where it stops (#1264). A block cut before its `END`
    # (truncated read) is masked to the end. Certificates and public keys stay
    # readable: they are not secret, and a deliverable publishes them on purpose.
    re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----.*?"
        r"(?:-----END [A-Z0-9 ]*PRIVATE KEY[A-Z ]*-----|\Z)",
        re.DOTALL,
    ),
    re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_-]{8,}"),
    # GitHub, two families: `github_pat_…` (fine-grained, the form
    # core/mcp/README.md recommends) and the classic `ghp_`/`gho_`/`ghu_`/
    # `ghs_`/`ghr_`. Fine-grained first — `gh[pousr]_` does not cover it.
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[a-z]-[A-Za-z0-9-]{10,}"),
)


def redact_secrets(text: str) -> str:
    """Returns `text` with known secrets removed (env values + key patterns)."""
    if not text:
        return text
    for variable in _SENSITIVE_ENV:
        value = os.getenv(variable)
        if value and len(value) >= _MIN_LENGTH:
            text = text.replace(value, SECRET_MARKER)
    # Served secrets (#109), longest first: a secret that is a prefix of another
    # must not leave a clear-text suffix after substitution.
    for value in sorted(_SERVED_SECRETS, key=len, reverse=True):
        text = text.replace(value, SECRET_MARKER)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(SECRET_MARKER, text)
    return text
