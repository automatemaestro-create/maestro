/**
 * Le vocabulaire du thème clair / sombre (#118) : clé de persistance, attribut
 * porté par `<html>` et résolution du choix « système ».
 *
 * Ce module est partagé par deux mondes — le **script d'init** du layout, qui
 * s'exécute avant le premier rendu (composant serveur, `app/layout.tsx`), et la
 * **bascule** de la barre supérieure côté client. Il ne dépend donc ni de React
 * ni du DOM au chargement : les deux lisent la même clé et écrivent le même
 * attribut, ce qui garantit qu'ils s'accordent (sans quoi le thème appliqué par
 * le script et l'état React divergeraient dès la première hydratation).
 */

/** Ce que l'utilisateur choisit — « système » suit la préférence de l'OS. */
export type ChoixTheme = "clair" | "sombre" | "systeme";

/** Ce qui est réellement appliqué, une fois « système » résolu. */
export type ThemeApplique = "clair" | "sombre";

/** Clé localStorage — même espace de noms que le repli de la sidebar (#117). */
export const CLE_THEME = "maestro.theme";

/** Attribut porté par `<html>` ; `globals.css` y accroche le variant `dark:`. */
const ATTRIBUT_THEME = "data-theme";

const REQUETE_SOMBRE = "(prefers-color-scheme: dark)";

/** Le choix persisté, ou « système » à défaut (première visite, stockage bloqué). */
export function lireChoix(): ChoixTheme {
  try {
    const brut = window.localStorage.getItem(CLE_THEME);
    return brut === "clair" || brut === "sombre" || brut === "systeme"
      ? brut
      : "systeme";
  } catch {
    // Stockage indisponible (navigation privée, cookies bloqués) : le thème
    // reste celui de la session, il ne sera simplement pas mémorisé.
    return "systeme";
  }
}

/** Mémorise le choix pour les sessions suivantes. Silencieux si impossible. */
export function ecrireChoix(choix: ChoixTheme): void {
  try {
    window.localStorage.setItem(CLE_THEME, choix);
  } catch {
    // Voir `lireChoix` : l'absence de persistance ne doit pas casser la bascule.
  }
}

/** Résout « système » contre la préférence courante de l'OS. */
export function resoudre(choix: ChoixTheme): ThemeApplique {
  if (choix !== "systeme") return choix;
  return window.matchMedia(REQUETE_SOMBRE).matches ? "sombre" : "clair";
}

/** Applique le choix au document — toute l'UI suit par le variant `dark:`. */
export function appliquer(choix: ChoixTheme): void {
  document.documentElement.setAttribute(ATTRIBUT_THEME, resoudre(choix));
}

/** Suit les changements de préférence de l'OS. Rend la fonction de retrait. */
export function ecouterSysteme(rappel: () => void): () => void {
  const media = window.matchMedia(REQUETE_SOMBRE);
  media.addEventListener("change", rappel);
  return () => media.removeEventListener("change", rappel);
}

/**
 * Le script d'init, exécuté **pendant l'analyse du HTML** — donc avant le
 * premier rendu et bien avant l'hydratation (docs Next.js, « Preventing flash
 * before hydration ») : sans lui, la page s'afficherait dans le thème rendu par
 * le serveur avant de sauter au thème choisi.
 *
 * Il est écrit ici, à partir des mêmes constantes que les fonctions ci-dessus,
 * pour qu'un renommage de la clé ou de l'attribut ne puisse pas les désaccorder.
 * Volontairement minimal (pas de dépendance, `try/catch` global) : il bloque
 * l'analyse du document.
 */
export const SCRIPT_INIT_THEME = `(function(){try{var c=localStorage.getItem(${JSON.stringify(
  CLE_THEME,
)});if(c!=="clair"&&c!=="sombre")c=matchMedia(${JSON.stringify(
  REQUETE_SOMBRE,
)}).matches?"sombre":"clair";document.documentElement.setAttribute(${JSON.stringify(
  ATTRIBUT_THEME,
)},c)}catch(e){}})()`;
