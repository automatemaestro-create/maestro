/** Formatages partagés de l'UI : coûts, heures, libellés de statut. */

import {
  CAUSE_ANNULATION,
  CAUSE_EXTINCTION,
  CAUSE_HOTE_NON_DEMARRE,
  CAUSE_LIMITE_USAGE,
  CAUSE_PLAFOND_COUT,
  CAUSE_PLAFOND_TOURS,
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_ATTENTE_ARBITRAGE,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
} from "./types";

/**
 * Les montants se lisent à **deux décimales** (#247) : c'est la précision d'un
 * relevé de dépense. Quatre décimales rendaient « 1,2345 $US » partout — tuile
 * Dépense, cartes du Kanban, Coûts & analytics, répartition par agent —, un
 * chiffre qu'on déchiffre au lieu de le lire.
 *
 * Ce module est la **source unique** du rendu des montants : un composant qui
 * reformate dans son coin fait diverger l'écran de lui-même.
 */
const FORMAT_COUT = new Intl.NumberFormat("fr-FR", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/**
 * Le plus petit montant que deux décimales savent écrire. En dessous de sa
 * moitié, l'arrondi rendrait « 0,00 $US » — indiscernable d'une dépense nulle,
 * alors que le cas est **courant** sur un fournisseur local (#113), où un appel
 * coûte quelques dix-millièmes de dollar.
 */
const CENTIME = 0.01;

/**
 * Un coût en dollars, ou « — » si aucun coût n'a été rapporté (inconnu ≠ nul).
 *
 * Trois cas distincts, et c'est le point : « — » (rien n'a été rapporté),
 * « 0,00 $US » (zéro rapporté, une vraie mesure) et « < 0,01 $US » (une dépense
 * réelle mais trop petite pour ce format). Les confondre ferait passer un
 * fournisseur bon marché pour un fournisseur gratuit.
 */
export function formatCout(cout: number | null): string {
  if (cout === null) return "—";
  if (cout > 0 && cout < CENTIME / 2) return `< ${FORMAT_COUT.format(CENTIME)}`;
  return FORMAT_COUT.format(cout);
}

/**
 * Les graduations d'un axe de coûts vivent ici aussi, bien qu'elles ne suivent
 * pas la règle des deux décimales : c'est le seul endroit où elle ne tient pas —
 * sur une série de quelques millièmes de dollar, **toutes** les graduations
 * tomberaient sur « 0,00 » et l'axe ne dirait plus rien. L'exception est
 * déclarée ici plutôt que laissée au composant qui dessine (#247).
 */
const FORMAT_MONTANT_AXE = new Intl.NumberFormat("fr-FR", {
  maximumFractionDigits: 4,
});

/**
 * La graduation d'un axe de coûts, symbole compris (« 0,25 $ »). L'espace y est
 * insécable : une graduation ne se coupe pas en deux.
 */
export function formatCoutAxe(valeur: number): string {
  return `${FORMAT_MONTANT_AXE.format(valeur)} $`;
}

const FORMAT_TOKENS = new Intl.NumberFormat("fr-FR");

/** Un compte de tokens avec séparateur de milliers (« 12 345 »). */
export function formatTokens(tokens: number): string {
  return FORMAT_TOKENS.format(tokens);
}

const FORMAT_SECONDES = new Intl.NumberFormat("fr-FR", {
  maximumFractionDigits: 1,
});

/** Une durée en millisecondes rendue lisible, ou « — » si non rapportée. */
export function formatDuree(dureeMs: number | null): string {
  if (dureeMs === null) return "—";
  if (dureeMs < 1000) return `${dureeMs} ms`;
  const secondesTotales = Math.round(dureeMs / 1000);
  if (secondesTotales < 60) return `${FORMAT_SECONDES.format(dureeMs / 1000)} s`;
  const minutes = Math.floor(secondesTotales / 60);
  const secondes = secondesTotales % 60;
  return `${minutes} min ${String(secondes).padStart(2, "0")} s`;
}

/** L'heure locale d'un horodatage ISO du backend (chaîne vide si absent). */
export function formatHeure(horodatage: string): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  return date.toLocaleTimeString("fr-FR");
}

/** La date et l'heure locales d'un horodatage ISO du backend (chaîne vide si absent). */
export function formatDateHeure(horodatage: string): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  return date.toLocaleString("fr-FR");
}

const MINUTE_MS = 60_000;
const HEURE_MS = 60 * MINUTE_MS;
const JOUR_MS = 24 * HEURE_MS;
const SEMAINE_MS = 7 * JOUR_MS;

/**
 * L'âge d'un horodatage, dit **relativement au-delà de la minute** (#250) — « il
 * y a 3 min », « il y a 2 h ». Sous la minute l'heure exacte reste plus parlante
 * qu'un « à l'instant » (c'est la ligne qui vient d'arriver, on veut son heure) ;
 * au-delà de la semaine on repasse à la date complète, « il y a 23 j » ne
 * situant plus rien.
 *
 * `maintenant` est passé par l'appelant plutôt que lu ici : c'est ce qui rend la
 * fonction pure (donc testable sans horloge truquée) et ce qui permet à une
 * liste entière de partager le même instant. `null` signifie « pas encore
 * d'horloge » — rendu serveur ou première image avant l'hydratation, où
 * `Date.now()` diffère des deux côtés (voir `useHorloge`) : on rend alors
 * l'heure absolue, qui, elle, est la même partout.
 */
export function formatHeureRelative(
  horodatage: string,
  maintenant: number | null,
): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  if (maintenant === null) return formatHeure(horodatage);
  // Un âge négatif (horloges désaccordées entre le poste et le backend) tombe
  // dans le même cas que « à la minute » : on n'écrit pas « il y a -2 min ».
  const age = maintenant - date.getTime();
  if (age < MINUTE_MS) return formatHeure(horodatage);
  if (age < HEURE_MS) return `il y a ${Math.floor(age / MINUTE_MS)} min`;
  if (age < JOUR_MS) return `il y a ${Math.floor(age / HEURE_MS)} h`;
  if (age < SEMAINE_MS) return `il y a ${Math.floor(age / JOUR_MS)} j`;
  return formatDateHeure(horodatage);
}

/**
 * Depuis **combien de temps** quelque chose attend (#272) — « depuis 3 min »,
 * « depuis 2 h ».
 *
 * Voisine de `formatHeureRelative` et pourtant distincte, parce que la question
 * n'est pas la même : « il y a 3 min » situe un fait passé (une ligne du fil, un
 * événement), « depuis 3 min » mesure une attente **qui dure**. Sur une demande
 * de validation, qui met un moteur en pause, c'est la seconde qui décide — et
 * écrire « il y a 3 min » à côté de « en attente » ferait lire l'heure d'arrivée
 * là où on cherche l'ancienneté.
 *
 * Sous la minute, on dit « depuis moins d'une minute » plutôt que l'heure exacte
 * (l'inverse du choix de `formatHeureRelative`) : sur une file, l'heure absolue
 * oblige à faire la soustraction soi-même, et c'est justement ce que ce format
 * évite. Au-delà de la semaine on repasse à la date complète, une attente de
 * plusieurs semaines n'étant plus une attente mais un oubli qu'il faut dater.
 *
 * `maintenant` vient de l'appelant (`useHorloge`) pour les mêmes raisons qu'au-
 * dessus : fonction pure, un seul instant partagé par toute une liste, et `null`
 * tant que l'horloge n'a pas démarré — on rend alors l'heure absolue, identique
 * sur le serveur et dans le navigateur.
 */
export function formatAttente(
  horodatage: string,
  maintenant: number | null,
): string {
  if (!horodatage) return "";
  const date = new Date(horodatage);
  if (Number.isNaN(date.getTime())) return horodatage;
  if (maintenant === null) return `depuis ${formatHeure(horodatage)}`;
  // Une attente négative (horloges désaccordées entre le poste et le backend)
  // tombe dans le même cas que « à la minute » : on n'écrit pas « depuis -2 min ».
  const age = maintenant - date.getTime();
  if (age < MINUTE_MS) return "depuis moins d'une minute";
  if (age < HEURE_MS) return `depuis ${Math.floor(age / MINUTE_MS)} min`;
  if (age < JOUR_MS) return `depuis ${Math.floor(age / HEURE_MS)} h`;
  if (age < SEMAINE_MS) return `depuis ${Math.floor(age / JOUR_MS)} j`;
  return `depuis le ${formatDateHeure(horodatage)}`;
}

/**
 * **Combien de temps un run a tourné** (#709) — « 12 min », « 1 h 04 », « 3 j 04 h ».
 *
 * Troisième format de temps du module, et le seul qui mesure un **intervalle
 * entre deux faits du run** plutôt qu'un écart à maintenant : `formatHeureRelative`
 * situe un fait passé (« il y a 1 h »), `formatAttente` mesure une attente qui
 * dure (« depuis 3 min »), celui-ci répond à « combien de temps ». La carte d'un
 * run portait les deux premiers et pas le troisième, si bien que « démarré il y a
 * 1 h » tenait lieu de durée — vrai par accident sur un run en cours, faux dès
 * qu'il est soldé, où l'âge du **départ** n'a plus rien à voir avec le temps passé.
 *
 * Le terme est `fin` quand le run est soldé — la durée est alors un **fait figé**,
 * calculable sans horloge, ce qui est le cas de la majorité d'une liste — et
 * `maintenant` tant qu'il tourne. D'où le seul cas qui rend la **chaîne vide** :
 * un run **en cours** avant que l'horloge n'ait démarré (rendu serveur, première
 * image). `formatHeureRelative` retombe là sur l'heure absolue ; ici il n'existe
 * aucun repli — une durée vivante n'a pas d'équivalent immobile —, donc on n'écrit
 * rien plutôt qu'un zéro, et la durée paraît au premier battement.
 *
 * Sous la minute on écrit « < 1 min » et jamais « 0 min » : les deux se lisent
 * différemment, et un run qui vient de partir n'a pas tourné zéro minute. Au-delà
 * de l'heure les minutes sont **sur deux chiffres** (« 1 h 04 ») — c'est une durée
 * qu'on lit comme une horloge, pas un nombre qu'on additionne.
 */
export function formatDureeRun(
  debut: string,
  fin: string | null,
  maintenant: number | null,
): string {
  if (!debut) return "";
  const depart = new Date(debut).getTime();
  if (Number.isNaN(depart)) return "";
  const terme = fin ? new Date(fin).getTime() : maintenant;
  if (terme === null || Number.isNaN(terme)) return "";
  // Une durée négative (horloges désaccordées entre le poste et le backend, ou
  // une `fin` antérieure au `debut`) tombe dans le même cas que « à la minute ».
  const duree = terme - depart;
  if (duree < MINUTE_MS) return "< 1 min";
  if (duree < HEURE_MS) return `${Math.floor(duree / MINUTE_MS)} min`;
  if (duree < JOUR_MS) {
    const heures = Math.floor(duree / HEURE_MS);
    const minutes = Math.floor((duree % HEURE_MS) / MINUTE_MS);
    return `${heures} h ${String(minutes).padStart(2, "0")}`;
  }
  const jours = Math.floor(duree / JOUR_MS);
  const heures = Math.floor((duree % JOUR_MS) / HEURE_MS);
  return `${jours} j ${String(heures).padStart(2, "0")} h`;
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
  refus_outil: "Outil refusé",
  arbitrage_outil: "Outil arbitré",
};

/** Le libellé d'un statut, ou le statut brut si le flux s'est enrichi. */
export function libelleStatut(statut: string): string {
  return LIBELLES_STATUT[statut] ?? statut;
}

/**
 * Libellés français des statuts d'**exécution** (`EXECUTION_*`, #185/#320/#321).
 *
 * Distincts de ceux des tâches ci-dessus, et pas seulement par prudence : les deux
 * machines à états partagent le mot `en_cours` sans partager sa portée — une tâche
 * en cours est portée par un agent, un run en cours peut n'attendre qu'un humain.
 * Une seule table les confondrait au premier statut ajouté d'un côté.
 */
const LIBELLES_STATUT_EXECUTION: Record<string, string> = {
  [EXECUTION_EN_COURS]: "En cours",
  [EXECUTION_TERMINEE]: "Terminée",
  [EXECUTION_ANNULEE]: "Annulée",
  [EXECUTION_ECHEC]: "Échec",
  [EXECUTION_EN_ATTENTE_BRIEF]: "Brief à valider",
  [EXECUTION_EN_ATTENTE_REPONSES]: "Questions en attente",
  // Le libellé de la table `ATTENTES` (`components/runs/EtatRun`) au mot près
  // (#571) : c'est le même fait, et deux formulations pour un run selon qu'on lit
  // son badge ou son statut brut feraient chercher deux états.
  [EXECUTION_EN_ATTENTE_ARBITRAGE]: "Validation en attente",
};

/** Le libellé d'un statut de run, ou le statut brut si le flux s'est enrichi. */
export function libelleStatutExecution(statut: string): string {
  return LIBELLES_STATUT_EXECUTION[statut] ?? statut;
}

/**
 * Ce que **dit** chaque cause d'arrêt d'un run (#479, `CAUSE_*`).
 *
 * Une phrase et non une étiquette : la question à laquelle cette ligne répond est
 * « pourquoi s'est-il arrêté ? », et « Plafond de tours » y répond moins bien que
 * « Plafond de tours atteint ». C'est le même parti pris que la table `ATTENTES`
 * (`components/runs/EtatRun`), qui porte une `phrase` à côté de son `libelle`.
 *
 * Ces phrases restent **génériques** : le chiffre — quelle borne, quel montant —
 * vit dans le `detail` de l'événement d'issue, que le fil d'activité rend. Les
 * recopier ici demanderait de faire voyager un second champ pour un gain nul.
 */
const LIBELLES_CAUSE: Record<string, string> = {
  [CAUSE_PLAFOND_TOURS]: "Plafond de tours atteint",
  [CAUSE_PLAFOND_COUT]: "Plafond de dépense atteint",
  [CAUSE_LIMITE_USAGE]: "Limite d'usage du fournisseur",
  [CAUSE_HOTE_NON_DEMARRE]: "L'hôte du run n'a pas démarré",
  [CAUSE_ANNULATION]: "Interrompu",
  // #486 — la phrase dit **qui** a arrêté, parce que c'est ce qui distingue cette
  // cause de la précédente : le statut consigné est le même (« annulée »), et
  // « Interrompu » tout court ferait chercher qui a cliqué sur quoi.
  [CAUSE_EXTINCTION]: "Maestro s'est éteint",
};

/**
 * Le libellé d'une cause d'arrêt — `null` quand il n'y a rien à dire.
 *
 * `null` couvre **deux** cas qu'on ne cherche pas à distinguer : le backend n'a
 * pas su classer l'échec (chaîne vide), ou il a émis un code que ce front ne
 * connaît pas encore. Dans les deux cas la bonne conduite est la même — ne rien
 * afficher plutôt qu'un code brut, le `detail` de l'issue restant lisible au fil
 * d'activité. Rendre `cause` tel quel afficherait « hote_non_demarre » à
 * quelqu'un le jour où le backend prendrait de l'avance sur le front.
 */
export function libelleCause(cause: string | undefined): string | null {
  return cause ? (LIBELLES_CAUSE[cause] ?? null) : null;
}
