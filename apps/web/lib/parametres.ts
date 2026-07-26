/**
 * Le sommaire de la page Paramètres (#121, lot 5 de #116) : une seule liste,
 * consommée par le sous-menu (ancres, section courante au défilement) et par la
 * page (en-tête de chaque section). Ajouter une section se fait ici, pas dans
 * deux endroits — même principe que `lib/navigation.ts` pour le menu principal.
 *
 * L'ordre est celui de la page : du plus général (à quoi l'UI est branchée) au
 * plus spécifique (ce que chaque agent consomme).
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

export const SECTIONS_PARAMETRES: SectionParametres[] = [
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
  {
    id: "couts",
    libelle: "Coûts & plafonds",
    description: "Dépense cumulée et garde-fous de budget.",
  },
  {
    id: "notifications",
    libelle: "Notifications",
    description: "Ce qui remonte dans la cloche de la barre supérieure.",
  },
];

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
