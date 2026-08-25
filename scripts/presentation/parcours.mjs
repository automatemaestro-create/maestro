/**
 * Les parcours de démonstration filmés sur la stack de démo (#545, lot 2 de #543).
 *
 * Une capture fixe ne montre pas ce qu'une fonctionnalité **fait**. Ce fichier
 * déclare les parcours que `captures.mjs` filme — des **données**, pas du code :
 * ajouter une démonstration se fait ici, sans toucher au moteur qui la joue.
 *
 * Un parcours :
 *
 *   cle           le nom du clip et sa clé dans le manifeste (`<cle>.webm`)
 *   libelle       ce que la démonstration montre, tel qu'on le lira sous la vidéo
 *   route         la page où le clip commence (un chemin, comme au menu)
 *   duree_max_ms  le plafond du clip — navigation et attente de la page comprises,
 *                 parce que c'est bien la durée du CLIP qu'on plafonne, pas celle
 *                 des seuls gestes. `DUREE_MAX_MS_DEFAUT` sinon.
 *   gestes        la suite de gestes, jouée dans l'ordre
 *
 * Trois gestes, et pas un de plus — ce qui demanderait un quatrième verbe demande
 * en général un écran de démonstration, pas une extension du vocabulaire :
 *
 *   { type: "attendre", texte: "État des runs", ms: 1500 }
 *       `texte` attend que ce texte soit **visible** ; `ms` marque ensuite un
 *       temps, pour que l'œil suive. L'un, l'autre, ou les deux.
 *
 *   { type: "cliquer", texte: "Approuver" }
 *   { type: "cliquer", selecteur: "a[href^=\"/agents/\"]", ms: 800 }
 *       `texte` vise le premier élément cliquable qui le porte — lien, bouton,
 *       onglet, entrée de menu, case à cocher —, en cherchant d'abord une
 *       correspondance **exacte** puis, à défaut, un contenu qui l'inclut.
 *       `selecteur` est la porte de sortie CSS, pour ce qui n'a pas de texte
 *       stable. `ms` marque un temps après le clic.
 *
 *   { type: "defiler", vers: 520, ms: 2200 }
 *       défile la colonne de contenu jusqu'à `vers` (un nombre de pixels, ou
 *       `"bas"` / `"haut"`) en `ms`, de façon **animée** : un défilement se
 *       filme, un saut ne se voit pas.
 *
 * ⚠ **Jamais de délai fixe pour attendre un état.** Le scénario de démo
 * (`maestro/controltower/demo.py`) est rejoué depuis zéro à chaque démarrage de
 * l'API et sa rafale initiale n'arrive pas à heure fixe : ce qui doit être là
 * s'attend par `texte`. Le `ms` d'`attendre` ne sert qu'à **laisser voir** ce qui
 * est déjà là.
 *
 * ⚠ **L'ordre compte.** Un parcours qui tranche une validation la retire des
 * suivants, un parcours qui met un run en pause change son badge. Ceux qui
 * montrent un état passent donc avant ceux qui le changent — d'où la place de
 * « validation » et « runs » en fin de liste.
 *
 * ⚠ **Les textes ci-dessous sont ceux de l'UI**, pas des `aria-label` : un
 * `<section aria-label="Indicateurs de tête">` n'est pas à l'écran, et l'attendre
 * ferait échouer le parcours sans rien apprendre. Un parcours qui échoue n'est
 * jamais fatal (il laisse sa ligne et son erreur au manifeste), mais un parcours
 * qui échoue est un parcours qui ne démontre rien.
 */

/** Plafond d'un clip quand le parcours n'en déclare pas (ms). */
export const DUREE_MAX_MS_DEFAUT = 16_000;

/** Durée d'un défilement quand le geste n'en déclare pas (ms). */
export const DEFILEMENT_MS_DEFAUT = 1_500;

export const PARCOURS = [
  {
    cle: "tableau-de-bord",
    libelle: "Le tableau de bord, en direct",
    route: "/",
    duree_max_ms: 20_000,
    gestes: [
      // La demande d'arbitrage du scénario arrive vers t+8 s : c'est elle qu'on
      // attend, pas un délai — l'API peut avoir démarré il y a dix secondes
      // comme il y a dix minutes.
      { type: "attendre", texte: "Validations en attente", ms: 1500 },
      { type: "defiler", vers: 420, ms: 2200 },
      { type: "attendre", texte: "État des runs", ms: 1800 },
      { type: "defiler", vers: "bas", ms: 2600 },
      { type: "attendre", texte: "Activité en direct", ms: 2000 },
      { type: "defiler", vers: "haut", ms: 1800 },
    ],
  },
  {
    cle: "couts",
    libelle: "Coûts & analytics : la dépense, période par période",
    route: "/couts",
    duree_max_ms: 18_000,
    gestes: [
      { type: "attendre", texte: "Répartition par agent", ms: 1500 },
      { type: "cliquer", texte: "24 heures", ms: 1800 },
      { type: "cliquer", texte: "Tout", ms: 1500 },
      { type: "defiler", vers: 520, ms: 2200 },
      { type: "attendre", texte: "Par tâche", ms: 1800 },
      { type: "defiler", vers: "haut", ms: 1500 },
    ],
  },
  {
    cle: "agents",
    libelle: "Une fiche d'agent et ses facettes",
    route: "/agents",
    duree_max_ms: 18_000,
    gestes: [
      { type: "attendre", texte: "Nouvel agent", ms: 1200 },
      // Par le sélecteur et non par un nom d'agent : le catalogue dépend du
      // poste, `a[href^="/agents/"]` désigne la première fiche quelle qu'elle
      // soit (l'entrée de menu, elle, est `/agents` sans barre finale).
      { type: "cliquer", selecteur: 'a[href^="/agents/"]', ms: 1500 },
      { type: "cliquer", texte: "Playbook", ms: 2000 },
      { type: "cliquer", texte: "MCP & permissions", ms: 2200 },
    ],
  },
  {
    cle: "validation",
    libelle: "Trancher une validation en attente",
    route: "/validations",
    duree_max_ms: 16_000,
    gestes: [
      { type: "attendre", texte: "Validations en attente", ms: 2000 },
      { type: "cliquer", texte: "Approuver" },
      { type: "attendre", texte: "Déjà tranchées", ms: 2500 },
      { type: "defiler", vers: 240, ms: 1600 },
      { type: "attendre", ms: 1500 },
    ],
  },
  {
    cle: "runs",
    libelle: "Mettre un run en pause, puis le reprendre",
    route: "/runs",
    duree_max_ms: 18_000,
    gestes: [
      { type: "attendre", texte: "Runs de", ms: 1500 },
      { type: "cliquer", texte: "Mettre en pause" },
      { type: "attendre", texte: "En pause", ms: 2500 },
      { type: "cliquer", texte: "Reprendre", ms: 2000 },
    ],
  },
];
