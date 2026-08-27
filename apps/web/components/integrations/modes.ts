/**
 * Le vocabulaire partagé par les deux blocs de l'écran « Intégrations » (#270).
 *
 * `LIBELLE_MODE` était écrit dans `ParametresMcp.tsx`, d'où le pool et la
 * bibliothèque le lisaient ensemble ; les deux vivant désormais dans leur propre
 * fichier, le recopier serait le premier moyen qu'un mode d'auth s'affiche sous
 * deux noms selon l'endroit de l'écran où on le lit.
 */

/** Libellé lisible d'un mode d'auth (docs/21 §2) — l'inconnu retombe sur sa clé. */
export const LIBELLE_MODE: Record<string, string> = {
  token_statique: "Token statique",
  appairage: "Appairage (sans token)",
  oauth_importe: "Token OAuth importé",
  sans_secret: "Sans secret",
};

/** Le mode d'auth d'une intégration, dit en clair. */
export function libelleMode(mode: string): string {
  return LIBELLE_MODE[mode] ?? mode;
}
