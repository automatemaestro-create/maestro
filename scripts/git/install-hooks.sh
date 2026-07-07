#!/usr/bin/env bash
# Active les hooks git versionnés du dépôt (scripts/git/hooks/) via core.hooksPath.
# Idempotent — à lancer une fois par clone :  bash scripts/git/install-hooks.sh
# Désactivation :  git config --unset core.hooksPath
set -euo pipefail

repo_root="$(git rev-parse --show-toplevel)"
hooks_dir="scripts/git/hooks"

if [ ! -d "$repo_root/$hooks_dir" ]; then
  echo "Répertoire de hooks introuvable : $hooks_dir" >&2
  exit 1
fi

# core.hooksPath relatif est résolu depuis la racine du dépôt (les hooks tournent avec le
# working tree top-level comme cwd), donc les commits depuis n'importe quel sous-dossier marchent.
git -C "$repo_root" config core.hooksPath "$hooks_dir"

# Bit exécutable (utile hors Windows ; sans effet notable sous Git Bash/Windows).
chmod +x "$repo_root/$hooks_dir"/* 2>/dev/null || true

echo "✓ Hooks git activés (core.hooksPath = $hooks_dir)."
for hook in "$repo_root/$hooks_dir"/*; do
  [ -f "$hook" ] && printf '  - %s\n' "$(basename "$hook")"
done
echo "  Désactivation : git config --unset core.hooksPath"
