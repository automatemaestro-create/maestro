/**
 * Les primitives visuelles de la Control Tower (#245, lot 1 de #242).
 *
 * Avant ce lot, chaque écran recopiait ses classes : la carte du Kanban, celle
 * du grand livre, celle d'un projet et celle d'une section de Paramètres
 * disaient la même chose en quatre variantes — et un écran « épuré » ne
 * différait d'un écran « brouillon » que par la vigilance de qui l'avait
 * écrit. Les cinq briques ci-dessous portent ces décisions **une fois** :
 *
 * - `Carte` — la surface : bord, fond, ombre, arrondi, densité, ton ;
 * - `TuileChiffre` — un chiffre de tête, son libellé, son détail, son renvoi ;
 * - `EnTeteSection` — le titre d'une zone, avec son icône et son compte ;
 * - `BadgeEtat` — la pastille d'état (compte, statut, provenance) ;
 * - `EtatVide` — ce qui manque, et par où l'obtenir.
 *
 * Trois règles tiennent l'ensemble :
 *
 * - **Les deux thèmes viennent avec la brique.** Chaque classe porte son
 *   variant `dark:` ici ; un appelant qui compose n'a plus à y penser, et c'est
 *   la seule façon de garantir qu'aucun écran n'oublie le thème sombre.
 * - **La densité est un choix nommé, pas un `p-*` improvisé** : trois pas
 *   (`compacte`, `normale`, `aeree`) couvrent tout le produit. Un quatrième se
 *   discute ici, pas dans un composant.
 * - **Les chiffres sont tabulaires** (`chiffre`, posé dans `globals.css`) : une
 *   valeur qui change en direct ne doit pas faire sauter la ligne autour d'elle.
 *
 * L'échelle typographique et le pas de densité sont documentés dans
 * `apps/web/README.md`.
 */

import Link from "next/link";
import type { ComponentType, HTMLAttributes, ReactNode, SVGProps } from "react";

import { IconeFlecheDroite } from "@/components/Icones";

/** Une icône du jeu (`components/Icones`) : décorative, à `currentColor`. */
export type Icone = ComponentType<SVGProps<SVGSVGElement>>;

/* ------------------------------------------------------------------ *
 * Carte
 * ------------------------------------------------------------------ */

/**
 * Les trois pas de densité du produit. `compacte` pour ce qui s'empile en
 * nombre (cartes du Kanban, lignes de liste), `normale` par défaut, `aeree`
 * pour une section de plein format qu'on lit posément.
 */
const DENSITE = {
  compacte: "p-2.5",
  normale: "p-3",
  aeree: "p-4",
  /** Sans padding : la carte encadre un contenu qui gère le sien (tableau…). */
  aucune: "",
} as const;

export type DensiteCarte = keyof typeof DENSITE;

/**
 * Les surfaces. `pleine` est le défaut — ce sur quoi on lit du contenu.
 *
 * Les variantes sont **nommées ici** plutôt que passées en `className` par
 * l'appelant : deux `bg-*` dans le même attribut ne se départagent pas par
 * l'ordre d'écriture mais par l'ordre de la feuille générée, ce qui rend un
 * « je surcharge le fond au cas par cas » silencieusement instable.
 */
const SURFACE = {
  /** Le contenu. */
  pleine:
    "border-neutral-200 bg-white shadow-sm dark:border-neutral-800 dark:bg-neutral-900",
  /**
   * Un contenant qui *reçoit* des cartes pleines (colonne du Kanban, encart
   * d'exemple) : en retrait du fond au lieu de s'en détacher.
   */
  creuse:
    "border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950",
  /** Une zone qui réclame un arbitrage — teintée de bout en bout. */
  attention:
    "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40",
  /**
   * Une carte d'arbitrage posée *dans* une zone `attention` : elle garde le
   * fond du contenu pour rester lisible, et n'emprunte que le bord.
   */
  attentionClaire:
    "border-amber-200 bg-white shadow-sm dark:border-amber-900 dark:bg-neutral-900",
} as const;

export type TonCarte = keyof typeof SURFACE;

type ProprietesCarte = {
  /** La balise rendue — `article` par défaut, `li` dans une liste, etc. */
  balise?: "article" | "section" | "div" | "li";
  densite?: DensiteCarte;
  ton?: TonCarte;
  className?: string;
  children: ReactNode;
} & Omit<HTMLAttributes<HTMLElement>, "className" | "children">;

/** La surface commune à tout ce qui se pose sur le fond de page. */
export function Carte({
  balise: Balise = "article",
  densite = "normale",
  ton = "pleine",
  className = "",
  children,
  ...reste
}: ProprietesCarte) {
  const classes = [
    "rounded-lg border",
    SURFACE[ton],
    DENSITE[densite],
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <Balise className={classes} {...reste}>
      {children}
    </Balise>
  );
}

/* ------------------------------------------------------------------ *
 * Tuile de chiffre
 * ------------------------------------------------------------------ */

/** Le lien « le détail est là-bas » d'une tuile ou d'un en-tête. */
export type Renvoi = { href: string; libelle: string };

/**
 * Un chiffre de tête : la valeur, ce qu'elle compte, ce qu'elle recouvre, et
 * la page où le détail se trouve. C'est le format des indicateurs du tableau
 * de bord — un résumé qui renvoie, jamais un cul-de-sac.
 */
export function TuileChiffre({
  libelle,
  valeur,
  detail,
  monospace = false,
  titre,
  renvoi,
  icone: Icone,
}: {
  libelle: string;
  /**
   * Un `ReactNode` et non une chaîne : une valeur peut porter son unité
   * (« 2 occupé(s) · 3 libre(s) », #247), rendue en petit pour que le chiffre
   * reste ce qu'on voit en premier. L'appelant compose, la tuile met en page.
   */
  valeur: ReactNode;
  detail?: string;
  /** Rendu en chasse fixe : un identifiant, pas un compte. */
  monospace?: boolean;
  /** Infobulle quand la valeur peut être tronquée. */
  titre?: string;
  renvoi?: Renvoi;
  icone?: Icone;
}) {
  return (
    // Densité `compacte` et non `normale` : depuis #248 le tableau des tâches
    // prend la hauteur que la page lui laisse, donc tout ce que cette rangée
    // garde en hauteur, il le perd. Un pas nommé plutôt qu'un `py-*` improvisé.
    <Carte densite="compacte" className="flex flex-col">
      <p className="flex items-center gap-1.5 text-annexe text-neutral-500 dark:text-neutral-400">
        {Icone && <Icone className="size-3.5 shrink-0" />}
        {libelle}
      </p>
      <p
        className={
          "chiffre mt-1 truncate font-semibold " +
          (monospace ? "font-mono text-corps" : "text-chiffre")
        }
        title={titre}
      >
        {valeur}
      </p>
      {detail && (
        <p className="chiffre mt-0.5 text-annexe text-neutral-500 dark:text-neutral-400">
          {detail}
        </p>
      )}
      {renvoi && <LienRenvoi renvoi={renvoi} className="mt-2" />}
    </Carte>
  );
}

/**
 * Le renvoi vers la page qui porte le détail. La flèche est l'icône du jeu,
 * pas un « → » de texte : elle suit la graisse du libellé et ne dépend plus du
 * rendu de la police par plateforme.
 */
export function LienRenvoi({
  renvoi,
  className = "",
}: {
  renvoi: Renvoi;
  className?: string;
}) {
  return (
    <Link
      href={renvoi.href}
      className={
        "inline-flex items-center gap-1 text-annexe font-medium text-sky-700 " +
        "hover:underline dark:text-sky-400 " +
        className
      }
    >
      {renvoi.libelle}
      <IconeFlecheDroite className="size-3.5 shrink-0" />
    </Link>
  );
}

/* ------------------------------------------------------------------ *
 * En-tête de section
 * ------------------------------------------------------------------ */

/**
 * Le titre d'une zone de l'écran : petites capitales grises, l'icône du sujet
 * à gauche, ce qui l'accompagne (compte, renvoi, bouton) à droite.
 *
 * `niveau` suit la hiérarchie du document, pas l'apparence : une section de
 * page est un `h2`, une sous-partie un `h3`. Les deux se ressemblent — c'est
 * le sens qui change, et c'est lui que le lecteur d'écran annonce.
 */
export function EnTeteSection({
  titre,
  icone: Icone,
  niveau = 2,
  id,
  ton = "neutre",
  aside,
  className = "",
}: {
  titre: ReactNode;
  icone?: Icone;
  niveau?: 2 | 3;
  id?: string;
  /** `attention` pour une zone qui réclame un geste (validations en attente). */
  ton?: "neutre" | "attention";
  /** Ce qui se pose à droite : compte, renvoi, bouton. */
  aside?: ReactNode;
  className?: string;
}) {
  const Titre = niveau === 2 ? "h2" : "h3";
  const couleur =
    ton === "attention"
      ? "text-amber-800 dark:text-amber-300"
      : "text-neutral-500 dark:text-neutral-400";
  return (
    <div
      className={
        "flex flex-wrap items-center justify-between gap-x-3 gap-y-1 " + className
      }
    >
      <Titre
        id={id}
        className={`flex items-center gap-2 text-corps font-semibold tracking-wide uppercase ${couleur}`}
      >
        {Icone && <Icone className="size-4 shrink-0" />}
        {titre}
      </Titre>
      {aside}
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Badge d'état
 * ------------------------------------------------------------------ */

/**
 * Les tons d'un badge — un sens, pas une couleur : `positif` pour ce qui va
 * bien, `attention` pour ce qui attend un geste, `alerte` pour ce qui a
 * échoué, `info` pour un fait neutre mis en avant, `accent` pour une
 * provenance (proposition, personnalisation).
 */
const TON_PLEIN = {
  neutre: "bg-neutral-200 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300",
  info: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  positif:
    "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  attention: "bg-amber-100 text-amber-900 dark:bg-amber-900 dark:text-amber-200",
  alerte: "bg-rose-100 text-rose-700 dark:bg-rose-950 dark:text-rose-300",
  accent: "bg-violet-200 text-violet-900 dark:bg-violet-900 dark:text-violet-200",
} as const;

const TON_CONTOUR = {
  neutre: "border border-neutral-300 text-neutral-600 dark:border-neutral-700 dark:text-neutral-400",
  info: "border border-sky-300 text-sky-700 dark:border-sky-800 dark:text-sky-400",
  positif:
    "border border-emerald-300 text-emerald-700 dark:border-emerald-800 dark:text-emerald-400",
  attention:
    "border border-amber-300 text-amber-700 dark:border-amber-800 dark:text-amber-300",
  alerte: "border border-rose-300 text-rose-700 dark:border-rose-800 dark:text-rose-400",
  accent:
    "border border-violet-300 text-violet-700 dark:border-violet-800 dark:text-violet-300",
} as const;

/** La couleur de la pastille — elle suit le ton, pas le contraste du fond. */
const TON_PASTILLE = {
  neutre: "bg-neutral-400",
  info: "bg-sky-500",
  positif: "bg-emerald-500",
  attention: "bg-amber-500",
  alerte: "bg-rose-500",
  accent: "bg-violet-500",
} as const;

export type TonBadge = keyof typeof TON_PLEIN;

/**
 * Une pastille d'état : un compte, un statut, une provenance. Toujours du
 * texte — la couleur appuie le sens, elle ne le porte jamais seule (une
 * pastille rouge et une verte se ressemblent pour qui ne distingue pas les
 * deux, et disparaissent en impression noir et blanc).
 */
export function BadgeEtat({
  ton = "neutre",
  contour = false,
  pastille = false,
  pulse = false,
  className = "",
  children,
}: {
  ton?: TonBadge;
  /** Contour plutôt qu'aplat : pour ce qui qualifie sans alerter. */
  contour?: boolean;
  /** Une pastille de couleur devant le libellé (états en direct). */
  pastille?: boolean;
  /** La pastille bat — une reconnexion en cours, pas un état stable. */
  pulse?: boolean;
  className?: string;
  children: ReactNode;
}) {
  const classes = [
    "inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-annexe font-medium",
    contour ? TON_CONTOUR[ton] : TON_PLEIN[ton],
    className,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <span className={classes}>
      {pastille && (
        <span
          aria-hidden="true"
          className={`size-1.5 shrink-0 rounded-full ${TON_PASTILLE[ton]} ${
            pulse ? "animate-pulse" : ""
          }`}
        />
      )}
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * État vide
 * ------------------------------------------------------------------ */

/**
 * Une zone qui n'a rien à montrer : ce qui manque, et le chemin qui existe
 * aujourd'hui — une page de l'interface (`lien`) ou un relais hors interface
 * (variable d'environnement, option de lancement).
 *
 * Le bord en pointillés est ce qui le distingue d'une carte : la place est
 * réservée, elle n'est pas remplie. Un état vide dit toujours **pourquoi**
 * c'est vide — jamais un « aucune donnée » sec, jamais un lien mort (#121).
 */
export function EtatVide({
  message,
  icone: Icone,
  releve,
  lien,
  children,
}: {
  message: ReactNode;
  icone?: Icone;
  /** Ce qui tient lieu de réglage en attendant (env, option CLI…). */
  releve?: ReactNode;
  /** Une page réelle de l'interface, quand il y en a une. */
  lien?: Renvoi;
  /** Un geste proposé sur place (bouton de création…). */
  children?: ReactNode;
}) {
  return (
    <div className="rounded-lg border border-dashed border-neutral-300 p-4 text-corps dark:border-neutral-700">
      <div className="flex gap-2.5">
        {Icone && (
          <Icone className="mt-0.5 size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
        )}
        <div className="min-w-0">
          <p className="text-neutral-600 dark:text-neutral-300">{message}</p>
          {releve && (
            <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
              {releve}
            </p>
          )}
          {lien && (
            <Link
              href={lien.href}
              className="mt-3 inline-block text-corps font-medium text-emerald-700 underline underline-offset-2 hover:text-emerald-800 dark:text-emerald-400 dark:hover:text-emerald-300"
            >
              {lien.libelle}
            </Link>
          )}
          {children && <div className="mt-3">{children}</div>}
        </div>
      </div>
    </div>
  );
}
