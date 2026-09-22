/**
 * Les parcours de démonstration filmés sur la vraie stack (#545, lot 2 de #543 ;
 * passés de la démo au réel par #1166).
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
 * en général un écran à montrer, pas une extension du vocabulaire :
 *
 *   { type: "attendre", texte: "État des runs", ms: 1500 }
 *       `texte` attend que ce texte soit **visible** ; `ms` marque ensuite un
 *       temps, pour que l'œil suive. L'un, l'autre, ou les deux.
 *
 *   { type: "cliquer", texte: "Kanban" }
 *   { type: "cliquer", selecteur: "a[href^=\"/runs/\"]", ms: 800 }
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
 * ⚠ **Le réel, pas un scénario** (#1166). Les parcours tournent sur l'état qu'un
 * passage du banc des scénarios de référence a laissé, servi par l'API réelle.
 * Personne n'a écrit cet état pour eux : un parcours s'ancre donc sur ce que
 * l'écran rend **toujours** (un titre de section, un onglet, une liste) et
 * désigne les objets du passage par leur **forme** (`a[href^="/runs/"]` : le
 * premier run, quel qu'il soit), jamais par un texte qu'un seul état contient.
 * Un parcours dont la cible n'existe pas dans l'état rouvert — aucune validation
 * demandée, par exemple — **garde sa ligne au manifeste et le dit** : c'est un
 * fait sur ce que le passage a laissé, pas une panne à masquer.
 *
 * ⚠ **Un parcours montre, il n'exerce rien.** Du temps de la démo, deux films
 * tranchaient une validation et mettaient un run en pause : sur le réel, ce
 * seraient une vraie décision et un vrai run. Aucun geste n'écrit donc, et ce
 * n'est pas qu'une convention — `captures.mjs` refuse toute requête d'écriture
 * vers l'API pendant le tournage, et la ligne du clip nomme ce qu'il a tenté.
 * Ce qui se démontre en agissant se démontre par le banc des scénarios
 * (`python -m maestro.scenarios`), dont c'est le métier, pas par un film.
 *
 * ⚠ **Jamais de délai fixe pour attendre un état.** L'API rejoue son journal au
 * démarrage et l'écran se peuple quand il se peuple : ce qui doit être là
 * s'attend par `texte`. Le `ms` d'`attendre` ne sert qu'à **laisser voir** ce
 * qui est déjà là.
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
    libelle: "Le tableau de bord d'un projet",
    route: "/",
    duree_max_ms: 18_000,
    gestes: [
      { type: "attendre", texte: "État des runs", ms: 1800 },
      { type: "defiler", vers: 420, ms: 2200 },
      { type: "attendre", texte: "Activité en direct", ms: 1800 },
      { type: "defiler", vers: "bas", ms: 2400 },
      { type: "defiler", vers: "haut", ms: 1800 },
    ],
  },
  {
    cle: "conversation",
    libelle: "Le fil de l'orchestrateur : la demande, l'accord, le run",
    route: "/chat",
    duree_max_ms: 20_000,
    gestes: [
      { type: "attendre", texte: "Conversations", ms: 1500 },
      { type: "defiler", vers: "haut", ms: 1800 },
      { type: "defiler", vers: "bas", ms: 3000 },
      // Le lien que le fil pose sous le message qui a ouvert un run : c'est le
      // chemin qu'un utilisateur emprunte, du fil à ce que le run a fait.
      { type: "cliquer", texte: "Voir le run", ms: 1200 },
      { type: "attendre", texte: "Pipeline", ms: 2000 },
    ],
  },
  {
    cle: "runs",
    libelle: "Un run, vu sous ses quatre angles",
    route: "/runs",
    duree_max_ms: 20_000,
    gestes: [
      { type: "attendre", texte: "Runs de", ms: 1200 },
      // Par sa forme et non par son objectif : le passage du banc a choisi ses
      // runs, ce fichier n'en connaît aucun.
      { type: "cliquer", selecteur: 'a[href^="/runs/"]', ms: 1200 },
      { type: "attendre", texte: "Pipeline", ms: 1800 },
      { type: "cliquer", texte: "Kanban", ms: 2000 },
      { type: "cliquer", texte: "Frise", ms: 2200 },
      { type: "cliquer", texte: "Décisions", ms: 2000 },
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
      { type: "cliquer", texte: "Par exécution", ms: 1800 },
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
      // La première FICHE, quelle qu'elle soit : le catalogue dépend de la copie.
      // « Nouvel agent » vit sous la même racine (`/agents/nouveau`) et passe
      // avant les fiches dans le document — il est donc écarté nommément.
      { type: "cliquer", selecteur: 'a[href^="/agents/"]:not([href="/agents/nouveau"])', ms: 1500 },
      { type: "cliquer", texte: "Playbook", ms: 2000 },
      { type: "cliquer", texte: "MCP & permissions", ms: 2200 },
    ],
  },
  {
    cle: "validations",
    libelle: "Les validations : ce qui a été tranché, et par qui",
    route: "/validations",
    duree_max_ms: 14_000,
    gestes: [
      // Un passage où personne n'a été sollicité n'en laisse aucune : le
      // parcours le dit alors (« … n'est pas à l'écran ») au lieu d'en montrer
      // une qu'on aurait fabriquée.
      { type: "attendre", texte: "Déjà tranchées", ms: 2000 },
      { type: "defiler", vers: 240, ms: 1600 },
      { type: "attendre", ms: 1500 },
    ],
  },
];
