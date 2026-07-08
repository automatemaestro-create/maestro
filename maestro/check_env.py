"""Vérifie que l'environnement de dev est prêt (critères du ticket #2, étendu #30).

Exécuter avec :  python -m maestro.check_env   (ou : maestro-check-env)

Contrôles :
  1. le Claude Agent SDK est importable ;
  2. l'authentification Claude est configurée selon le **mode** retenu (clé API
     ou abonnement Claude Code — cf. règle de précédence du ticket #30).

Sort en code 0 si tout est vert, 1 sinon. N'affiche jamais la clé.
"""

from __future__ import annotations

from collections.abc import Callable

from maestro.config import ConfigError, load_settings
from maestro.providers import AuthMode, ClaudeProvider


def _check_sdk_importable() -> tuple[bool, str]:
    try:
        import claude_agent_sdk
    except ImportError as exc:
        return False, f"import claude_agent_sdk a échoué ({exc})"
    version = getattr(claude_agent_sdk, "__version__", "inconnue")
    return True, f"claude_agent_sdk importable (version {version})"


def _check_auth() -> tuple[bool, str]:
    """Vérifie la config d'auth via le fournisseur, sans appel réseau ni secret affiché."""
    try:
        creds = ClaudeProvider.from_settings(load_settings()).credentials
    except ConfigError as exc:
        return False, str(exc)
    if creds.auth_mode is AuthMode.API_KEY:
        return True, "mode 'api_key' — ANTHROPIC_API_KEY lue depuis l'environnement"
    source = "CLAUDE_CODE_OAUTH_TOKEN" if creds.oauth_token else "connexion `claude` (/login)"
    return True, (
        f"mode 'subscription' — aucune clé requise ; auth par abonnement Claude Code "
        f"({source})"
    )


def main() -> int:
    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("SDK importable", _check_sdk_importable),
        ("Authentification", _check_auth),
    ]
    all_ok = True
    for label, check in checks:
        ok, detail = check()
        symbol = "OK  " if ok else "FAIL"
        print(f"[{symbol}] {label} — {detail}")
        all_ok = all_ok and ok

    print()
    if all_ok:
        print("Environnement prêt.")
        return 0
    print("Environnement incomplet — voir les messages ci-dessus.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
