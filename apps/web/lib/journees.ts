/**
 * Le découpage d'un fil en **journées** (#697, lot 7 de #690).
 *
 * Une conversation qui dure porte des messages de plusieurs jours, et rien ne
 * les séparait : deux bulles à trois jours d'intervalle se suivaient comme deux
 * répliques, l'heure seule (« 10:02 ») ne disant pas laquelle. C'est ce que
 * n'importe quelle messagerie règle par un trait daté, et ce que le critère
 * appelle « séparateurs de journée ».
 *
 * Deux règles reprises telles quelles au produit, parce qu'elles y sont déjà
 * tranchées ailleurs :
 *
 * - **un horodatage illisible ne se devine pas** (`EtatDesRuns.soldeAujourdHui`,
 *   #712). On ne sait pas quel jour c'était : le message garde sa place dans le
 *   fil, sans changer de journée et sans en ouvrir une. Le fil reste complet, ce
 *   qui vaut mieux qu'un séparateur inventé ;
 * - **sans horloge, personne n'est d'aujourd'hui** (`useHorloge`, #250).
 *   `Date.now()` ne vaut pas la même chose au rendu serveur et dans le
 *   navigateur : « Aujourd'hui » posé trop tôt ferait diverger l'HTML hydraté.
 *   Le séparateur porte donc sa **date absolue** — identique des deux côtés —
 *   puis passe à « Aujourd'hui » / « Hier » au premier battement, exactement
 *   comme un « il y a 3 min » remplace une heure absolue.
 *
 * La clé d'une journée est **locale** (`2026-08-28`) et construite à partir des
 * composantes de la date, jamais d'un `toISOString()` : celui-ci rend le jour
 * **UTC**, donc un message reçu à 01 h en France tomberait la veille et ouvrirait
 * une journée que personne n'a vécue.
 */

function cleDe(date: Date): string {
  const mois = String(date.getMonth() + 1).padStart(2, "0");
  const jour = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${mois}-${jour}`;
}

/**
 * La journée locale d'un horodatage ISO — `null` s'il est absent ou illisible.
 *
 * `null` est un **verdict** et pas une valeur de repli : deux messages sans
 * horodatage ne sont pas « du même jour », ils sont de jour inconnu, et c'est ce
 * qui empêche le fil d'ouvrir une journée sur eux.
 */
export function jourDe(horodatage: string | undefined | null): string | null {
  if (!horodatage) return null;
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return null;
  return cleDe(date);
}

/** La journée locale d'un instant (`Date.now()`), pour comparer à `jourDe`. */
export function jourDInstant(instant: number): string {
  return cleDe(new Date(instant));
}

const FORMAT_LONG = new Intl.DateTimeFormat("fr-FR", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});

const FORMAT_COURT = new Intl.DateTimeFormat("fr-FR", {
  weekday: "long",
  day: "numeric",
  month: "long",
});

/**
 * Ce qu'un séparateur de journée affiche.
 *
 * L'année tombe quand elle est celle qu'on vit — « vendredi 28 août » suffit à
 * situer une conversation de la semaine, et la répéter sur chaque séparateur
 * ferait du bruit sur la seule ligne qui doit s'effacer. Elle revient dès que la
 * journée n'est pas de l'année courante, où elle redevient l'information qui
 * situe.
 *
 * `maintenant` vient de l'appelant (`useHorloge`) : `null` veut dire « pas
 * encore d'horloge », et l'on rend alors la date complète, année comprise —
 * la seule forme qui ne dépende d'aucun instant.
 */
export function libelleDuJour(cle: string, maintenant: number | null): string {
  const [annee, mois, jour] = cle.split("-").map(Number);
  const date = new Date(annee, mois - 1, jour);
  if (maintenant === null) return FORMAT_LONG.format(date);
  if (cle === jourDInstant(maintenant)) return "Aujourd'hui";
  // « Hier » se calcule sur le **calendrier** et non en retranchant 24 h : les
  // deux journées de changement d'heure durent 23 h et 25 h, et l'écart en
  // millisecondes y désignerait tantôt le jour même, tantôt l'avant-veille.
  const veille = new Date(maintenant);
  veille.setDate(veille.getDate() - 1);
  if (cle === cleDe(veille)) return "Hier";
  return new Date(maintenant).getFullYear() === annee
    ? FORMAT_COURT.format(date)
    : FORMAT_LONG.format(date);
}
