"""Vérifie que l'environnement de dev est prêt (critères du ticket #2).

Exécuter avec :  python -m maestro.check_env   (ou : maestro-check-env)

Contrôles :
  1. le Claude Agent SDK est importable ;
  2. la clé API Anthropic est lue depuis l'environnement.

Sort en code 0 si tout est vert, 1 sinon. N'affiche jamais la clé.
"""

from __future__ import annotations

from collections.abc import Callable

from maestro.config import ConfigError, load_settings


def _check_sdk_importable() -> tuple[bool, str]:
    try:
        import claude_agent_sdk
    except ImportError as exc:
        return False, f"import claude_agent_sdk a échoué ({exc})"
    version = getattr(claude_agent_sdk, "__version__", "inconnue")
    return True, f"claude_agent_sdk importable (version {version})"


def _check_api_key() -> tuple[bool, str]:
    try:
        load_settings().require_api_key()
    except ConfigError as exc:
        return False, str(exc)
    return True, "ANTHROPIC_API_KEY lue depuis l'environnement"


def main() -> int:
    checks: list[tuple[str, Callable[[], tuple[bool, str]]]] = [
        ("SDK importable", _check_sdk_importable),
        ("Clé API", _check_api_key),
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
