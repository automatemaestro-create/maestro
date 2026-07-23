/**
 * Le bandeau « API injoignable », commun à toutes les pages (#117) : chacune
 * garde son propre chargement, donc sa propre erreur, mais le message et son
 * habillage ne se réécrivent plus page par page.
 */

export function BanniereErreurApi({ erreur }: { erreur: string | null }) {
  if (erreur === null) return null;
  return (
    <p
      role="alert"
      className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
    >
      API injoignable : {erreur} — vérifier que le backend tourne (
      <code className="font-mono">maestro-api</code>).
    </p>
  );
}
