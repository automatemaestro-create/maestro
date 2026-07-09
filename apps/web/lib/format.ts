/** Formatages partagés de l'UI : coûts, heures, libellés de statut. */

const FORMAT_COUT = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 4,
});

/** Un coût en dollars, ou « — » si aucun coût n'a été rapporté (inconnu ≠ nul). */
export function formatCout(cout: number | null): string {
  return cout === null ? "—" : FORMAT_COUT.format(cout);
}

/** L'heure locale d'un horodatage ISO du backend (chaîne vide si absent). */
export function formatHeure(horodatage: string): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  return date.toLocaleTimeString("fr-FR");
}

/** Libellés français des statuts de tâche (machine à états docs/03 §3). */
const LIBELLES_STATUT: Record<string, string> = {
  assignee: "Assignée",
  en_cours: "En cours",
  bloquee: "Bloquée",
  terminee: "Terminée",
  echec: "Échec",
  approuve: "Approuvée",
  refuse: "Refusée",
};

/** Le libellé d'un statut, ou le statut brut si le flux s'est enrichi. */
export function libelleStatut(statut: string): string {
  return LIBELLES_STATUT[statut] ?? statut;
}
