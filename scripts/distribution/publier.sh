#!/usr/bin/env bash
# Publie une version de Maestro sur son canal de mises à jour (#644, docs/54 §6.3).
#
#   bash scripts/distribution/publier.sh [--essai] [--sans-construire]
#
# Le canal est celui que la coque installée interroge (`build.publish` de apps/desktop/package.json) :
# les publications GitHub du dépôt, lues à `releases/latest/download/`. Publier, c'est y poser une
# publication `v<version>` qui porte deux fichiers — `latest.yml` (la version, le nom de
# l'installeur, son empreinte sha512) et l'installeur lui-même. Chaque installation la trouve à sa
# vérification suivante, la télécharge et propose de redémarrer dessus.
#
# ⚠ C'EST UN GESTE HUMAIN, jamais celui de la CI ni d'un run : ce qui part ici s'installe chez
# chacun. La version se décide dans une Pull Request, qui change `version` dans
# apps/desktop/package.json ; une fois mergée, on publie depuis le clone principal, sur `main` à
# jour. Le script le vérifie et refuse autrement :
#
#   - la branche est `main`, l'arbre est propre, et HEAD est `origin/main` (relu à l'instant) —
#     ce qui part est ce qui a été vérifié et mergé, rien d'autre ;
#   - la version n'est pas déjà publiée — une publication ne se remplace pas : les installations
#     qui l'ont prise ne la reprendraient pas, et deux paquets porteraient le même numéro ;
#   - le paquet construit dit cette version (`latest.yml`), nomme cet installeur, et son empreinte
#     est bien celle de l'installeur qu'on envoie.
#
# `--essai` joue tout sauf l'envoi, et dit ce qui serait publié. `--sans-construire` reprend le
# paquet déjà construit sous .maestro/distribution/paquet (les mêmes vérifications s'y appliquent).
#
# Codes : 0 publié (ou essai concluant) · 1 échec · 2 usage · 4 refus (état du dépôt, version déjà
# publiée, paquet incohérent).
set -uo pipefail

RACINE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PAQUET="$RACINE/.maestro/distribution/paquet"
ESSAI=0
CONSTRUIRE=1

while [ $# -gt 0 ]; do
  case "$1" in
    --essai) ESSAI=1; shift ;;
    --sans-construire) CONSTRUIRE=0; shift ;;
    -h | --help) sed -n '2,4p' "$0"; exit 0 ;;
    *) echo "[publication] argument inconnu : $1" >&2; exit 2 ;;
  esac
done

refus() {
  echo "[publication] refusée : $1" >&2
  exit 4
}

cd "$RACINE" || exit 1

# --- le dépôt : main, propre, à jour ---------------------------------------------------------------
branche="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ "$branche" = main ] || refus "on publie depuis main, pas depuis « ${branche:-?} »"
[ -z "$(git status --porcelain)" ] || refus "l'arbre porte des changements non commités"
git fetch --quiet origin main 2>/dev/null || refus "origin/main ne se relit pas"
tete="$(git rev-parse HEAD)"
[ "$tete" = "$(git rev-parse origin/main)" ] || refus "HEAD n'est pas origin/main — bash scripts/gitlab/lib.sh sync-main"

# --- la version, et qu'elle soit neuve ----------------------------------------------------------------
# La première clé « version » du fichier est la sienne : les dépendances n'en portent pas.
version="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' apps/desktop/package.json | head -n 1)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] ||
  refus "apps/desktop/package.json ne porte pas une version x.y.z (« ${version:-rien} »)"
etiquette="v$version"
if gh release view "$etiquette" >/dev/null 2>&1; then
  refus "$etiquette est déjà publiée — une version se publie une fois ; changez-la par une Pull Request"
fi

# --- le paquet ---------------------------------------------------------------------------------------
if [ "$CONSTRUIRE" = 1 ]; then
  bash "$RACINE/scripts/distribution/construire.sh" || {
    echo "[publication] la construction a échoué" >&2
    exit 1
  }
fi
CANAL="$PAQUET/latest.yml"
[ -f "$CANAL" ] || refus "aucun latest.yml sous ${PAQUET#"$RACINE"/} — bash scripts/distribution/construire.sh"
annoncee="$(sed -n 's/^version: *//p' "$CANAL" | tr -d '\r' | head -n 1)"
[ "$annoncee" = "$version" ] ||
  refus "latest.yml annonce « $annoncee », package.json dit « $version » — le paquet n'est pas celui de cette version"
nom="$(sed -n 's/^path: *//p' "$CANAL" | tr -d '\r' | head -n 1)"
INSTALLEUR="$PAQUET/$nom"
if [ -z "$nom" ] || [ ! -f "$INSTALLEUR" ]; then
  refus "l'installeur que latest.yml nomme (« $nom ») n'est pas sous ${PAQUET#"$RACINE"/}"
fi
# L'empreinte que la coque vérifiera après le téléchargement : la mesurer ici, avant l'envoi.
attendue="$(sed -n 's/^sha512: *//p' "$CANAL" | tr -d '\r' | head -n 1)"
mesuree="$(openssl dgst -sha512 -binary "$INSTALLEUR" | openssl base64 -A)"
if [ -z "$attendue" ] || [ "$attendue" != "$mesuree" ]; then
  refus "l'empreinte de $nom n'est pas celle que latest.yml annonce — l'installeur a changé depuis sa construction"
fi

echo "[publication] $etiquette — $nom ($(($(wc -c <"$INSTALLEUR") / 1000000)) Mo) et latest.yml, depuis ${tete:0:12}"
if [ "$ESSAI" = 1 ]; then
  echo "[publication] essai : rien n'est envoyé"
  exit 0
fi

# --- l'envoi -----------------------------------------------------------------------------------------
# `--latest` : c'est elle que `releases/latest/download/` sert désormais. Les notes se composent des
# Pull Requests mergées depuis la publication précédente.
gh release create "$etiquette" "$INSTALLEUR" "$CANAL" \
  --target "$tete" --title "Maestro $version" --generate-notes --latest || {
  echo "[publication] l'envoi a échoué — gh release view $etiquette dit ce qui en est arrivé" >&2
  exit 1
}
echo "[publication] $etiquette publiée : chaque installation la trouvera à sa prochaine vérification"
