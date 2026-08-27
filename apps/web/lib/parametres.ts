/**
 * Le sommaire de la page Paramètres (#121, lot 5 de #116) : une seule liste,
 * consommée par le sous-menu (ancres, section courante au défilement) et par la
 * page (en-tête de chaque section). Ajouter une section se fait ici, pas dans
 * deux endroits — même principe que `lib/navigation.ts` pour le menu principal.
 *
 * L'ordre est celui de la page : du plus général (à quoi l'UI est branchée) au
 * plus spécifique (ce que chaque agent consomme).
 *
 * ⚠ **Trois familles depuis #539** (docs/30 §4). L'écran alignait **sept**
 * sections de plein format pour un plafond de trois, et c'est le plus gros
 * dépassement du produit. La règle des trois places donne deux réponses à un
 * corps qui déborde ; celle qui convient ici est le **second niveau**, parce que
 * la page en avait déjà la moitié — son sous-menu. Les sept sections ne bougent
 * pas : elles se rangent sous trois familles, et ce sont les familles qui sont
 * désormais les blocs. Chaque ancre reste la sienne (`/parametres#agents`
 * fonctionne toujours), la page reste imprimable et cherchable au Ctrl+F, et le
 * sous-menu gagne le niveau qui lui manquait.
 *
 * `SECTIONS_PARAMETRES` est **dérivé** des familles et non écrit à côté : deux
 * listes à tenir d'accord seraient le premier moyen qu'une section entre au
 * sommaire sans entrer dans une famille — donc sans être rendue nulle part.
 *
 * ⚠ **Six sections depuis #270**, et non plus sept : « Intégrations MCP » est
 * partie tenir son propre écran (`/integrations`), une intégration décidant de
 * ce qu'un agent sait faire plutôt que de la façon dont ce poste-ci est réglé.
 * Ce n'est pas un épurement de plus au sens de #539 — le corps tenait déjà dans
 * ses trois familles —, c'est un déménagement : rien n'a été retiré au produit,
 * et l'ancre `/parametres#mcp` reste servie par
 * `components/parametres/RedirectionAncreMcp`.
 */

/**
 * Les ancres possibles. Union fermée à dessein : la page associe un composant à
 * chacune (`Record<IdSection, …>`), donc ajouter une section ici sans écrire son
 * contenu ne compile pas — pas de section fantôme dans le sommaire.
 */
export type IdSection =
  | "general"
  | "apparence"
  | "agents"
  | "fournisseurs"
  | "couts"
  | "notifications";

export type SectionParametres = {
  /** Ancre de la section — l'`id` du `<section>` et la cible du sous-menu. */
  id: IdSection;
  libelle: string;
  /** Ce que la section règle, en une phrase — sous-titre de son en-tête. */
  description: string;
};

/** Les trois familles de réglages — les blocs de plein format de l'écran. */
export type IdFamille = "poste" | "execution" | "depense";

export type FamilleParametres = {
  /** Ancre de la famille — l'`id` du bloc, et le groupe du sous-menu. */
  id: IdFamille;
  libelle: string;
  /** Ce que la famille rassemble, en une phrase. */
  description: string;
  sections: SectionParametres[];
};

/**
 * Les familles, dans l'ordre de la page. Le critère de regroupement est **ce
 * que le réglage engage**, et non sa mécanique : le poste (ce qui vaut pour
 * cette installation-ci), l'exécution (ce qui décide comment le travail se
 * fait), la dépense (ce qu'il a le droit de coûter). Une famille d'une seule
 * section n'est pas un défaut d'équilibre : « Coûts & plafonds » n'engage rien
 * de ce que les deux autres engagent, et l'y ranger pour faire nombre rendrait
 * les trois titres faux.
 */
export const FAMILLES_PARAMETRES: FamilleParametres[] = [
  {
    id: "poste",
    libelle: "Le poste",
    description:
      "Ce qui vaut pour cette installation : à quoi l'interface est branchée, comment elle se présente, ce qu'elle vous signale.",
    sections: [
      {
        id: "general",
        libelle: "Général",
        description: "Backend visé par l'interface et vitalité du service.",
      },
      {
        id: "apparence",
        libelle: "Apparence",
        description: "Thème de l'interface et navigation latérale.",
      },
      {
        id: "notifications",
        libelle: "Notifications",
        description: "Ce qui remonte dans la cloche de la barre supérieure.",
      },
    ],
  },
  {
    id: "execution",
    libelle: "L'exécution",
    description:
      "Ce qui décide comment le travail se fait : quels agents tournent, sur quels modèles, avec quels outils.",
    sections: [
      {
        id: "agents",
        libelle: "Agents & capacité",
        description:
          "Activer un agent et borner ses exécutions simultanées (#86, EF-21).",
      },
      {
        id: "fournisseurs",
        libelle: "Fournisseurs & modèles",
        description: "Le fournisseur et le modèle de chaque agent du catalogue.",
      },
    ],
  },
  {
    id: "depense",
    libelle: "La dépense",
    description: "Ce que le travail a le droit de coûter, et ce qu'il a coûté.",
    sections: [
      {
        id: "couts",
        libelle: "Coûts & plafonds",
        description: "Dépense cumulée et garde-fous de budget.",
      },
    ],
  },
];

/**
 * Les sections à plat, dans l'ordre de la page — **dérivées** des familles.
 * C'est ce que lisent le sous-menu (une ancre par section), le repère de
 * défilement et la réserve de fin de page : ils n'ont pas à connaître les
 * familles, seulement l'ordre des ancres.
 */
export const SECTIONS_PARAMETRES: SectionParametres[] =
  FAMILLES_PARAMETRES.flatMap((famille) => famille.sections);

/**
 * Décalage du repère de « section courante », en pixels : la barre supérieure
 * est collante (`h-14`), une section n'est donc lue qu'une fois son haut passé
 * dessous.
 *
 * **Doit valoir exactement le `scroll-mt-20` des sections** (5rem = 80 px) :
 * c'est là que l'ancre dépose la section visée, et c'est donc là que le
 * sous-menu doit la reconnaître comme courante. Les désaccorder de quelques
 * pixels suffit à ce qu'un clic sur une entrée en surligne une autre.
 */
export const DECALAGE_ANCRE_PX = 80;
