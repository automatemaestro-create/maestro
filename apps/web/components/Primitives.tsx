/**
 * Les primitives visuelles de la Control Tower (#245, lot 1 de #242 ; #535,
 * lot 3 de #532 pour `Bouton` et `Champ`).
 *
 * Avant ce lot, chaque écran recopiait ses classes : la carte du Kanban, celle
 * du grand livre, celle d'un projet et celle d'une section de Paramètres
 * disaient la même chose en quatre variantes — et un écran « épuré » ne
 * différait d'un écran « brouillon » que par la vigilance de qui l'avait
 * écrit. Les briques ci-dessous portent ces décisions **une fois** :
 *
 * - `Carte` — la surface : bord, fond, ombre, arrondi, densité, ton ;
 * - `Bouton` / `BoutonLien` — l'action : variante, ton, taille, occupé ;
 * - `Champ` / `ChampListe` / `ChampTexte` — la saisie : libellé lié, aide,
 *   erreur, `aria-invalid` ;
 * - `TuileChiffre` — un chiffre de tête, son libellé, son détail, son renvoi ;
 * - `EnTeteSection` — le titre d'une zone, avec son icône et son compte ;
 * - `BadgeEtat` — la pastille d'état (compte, statut, provenance) ;
 * - `EtatVide` — ce qui manque, et par où l'obtenir.
 *
 * Trois règles tiennent l'ensemble :
 *
 * - **Les deux thèmes viennent avec la brique.** Les briques d'origine portent
 *   leur variant `dark:` ici ; celles de #535 n'en portent aucun — elles sont
 *   écrites sur les **tokens** de #533 (`bg-surface`, `text-texte-secondaire`),
 *   qui *sont* les deux thèmes. C'est la même promesse, une couche plus bas :
 *   un appelant n'a jamais à y penser, et aucun écran ne peut oublier le sombre.
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
import type {
  ButtonHTMLAttributes,
  ComponentType,
  HTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  SVGProps,
  TextareaHTMLAttributes,
} from "react";

import { Infobulle } from "@/components/Infobulle";

import { IconeFlecheDroite } from "@/components/Icones";

/** Une icône du jeu (`components/Icones`) : décorative, à `currentColor`. */
export type Icone = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * Le plancher d'une **cible interactive** (#537) — 24 px, le minimum de WCAG 2.2
 * §2.5.8. `min-h-6` vaut `1.5rem`, soit exactement 24 px au pas par défaut.
 *
 * Il vit ici, et pas recopié en six endroits, pour la raison qui a fait naître
 * ce fichier (#245) : une valeur écrite six fois est une valeur qui divergera.
 * Ce qu'il corrige est mesuré (docs/30 §3.4) — les liens de renvoi et les liens
 * en petit corps du produit sortaient à **22 px**, deux pixels sous la barre, et
 * rien ne les en avertissait.
 *
 * ⚠ `min-h-` et non `h-` : un libellé qui passe à la ligne doit pouvoir grandir.
 * Et un plancher, jamais un `py-*` : la hauteur d'un `py-1.5` dépend du pas
 * typographique de l'élément, donc changerait sous lui le jour où le libellé
 * passe de `text-annexe` à `text-micro`.
 */
export const CIBLE_MINIMALE = "min-h-6";

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
  /**
   * La balise rendue — `article` par défaut, `li` dans une liste, `p` quand la
   * carte n'est qu'un paragraphe encadré, `form` quand elle encadre une saisie
   * (#535 : les recopies reprises étaient de ces quatre sortes).
   */
  balise?: "article" | "section" | "div" | "li" | "p" | "form";
  densite?: DensiteCarte;
  ton?: TonCarte;
  className?: string;
  children: ReactNode;
} & Omit<HTMLAttributes<HTMLElement>, "className" | "children">;

/**
 * Les classes de la surface, **sans** la balise qui les porte. C'est ce que
 * `Carte` rend, et le seul recours de ce qui ne peut pas en être une : un
 * `<Link>` (le composant de Next, pas une balise) et un `<button>` (dont le
 * `type` et le `disabled` ne vivent pas dans `HTMLAttributes`). Les exposer
 * plutôt que de rendre `Carte` polymorphe garde **une** source à la décision —
 * et c'est bien la recopie qui disparaît, pas seulement sa forme.
 */
export function classesCarte({
  densite = "normale",
  ton = "pleine",
  className = "",
}: {
  densite?: DensiteCarte;
  ton?: TonCarte;
  className?: string;
} = {}): string {
  return ["rounded-lg border", SURFACE[ton], DENSITE[densite], className]
    .filter(Boolean)
    .join(" ");
}

/** La surface commune à tout ce qui se pose sur le fond de page. */
export function Carte({
  balise: Balise = "article",
  densite = "normale",
  ton = "pleine",
  className = "",
  children,
  ...reste
}: ProprietesCarte) {
  return (
    <Balise className={classesCarte({ densite, ton, className })} {...reste}>
      {children}
    </Balise>
  );
}

/* ------------------------------------------------------------------ *
 * Bouton
 * ------------------------------------------------------------------ */

/**
 * Les trois formes d'un bouton. Elles disent le **rang** de l'action, pas son
 * apparence : `plein` pour ce qu'on vient faire sur l'écran (un seul par zone),
 * `contour` pour ce qui l'accompagne (annuler, fermer), `discret` pour ce qui
 * ne doit pas peser (une action de ligne, une bascule d'affichage).
 */
export type VarianteBouton = "plein" | "contour" | "discret";

/**
 * L'aplat d'un bouton `plein` : le fond, ce qui s'écrit dessus, et le fond du
 * survol. Les trois viennent des tokens de #533 — **jamais un `bg-*` brut** :
 * c'est ce qui a fait passer le bouton d'action de **3,65:1** (le
 * `bg-emerald-600` + blanc recopié dans 18 fichiers, docs/30 §3.2) à 5,36:1 en
 * clair et 8,00:1 en sombre, sans qu'un seul écran ait à le savoir.
 *
 * Le survol **s'écarte** de `sur-ton` dans les deux thèmes (plus sombre en
 * clair, plus clair en sombre), donc le libellé ne peut que gagner en
 * contraste : 7,09:1 au pire en clair, 10,33:1 au pire en sombre. Un
 * `hover:opacity-90` aurait fait l'inverse, en silence.
 */
const BOUTON_PLEIN = {
  accent: "bg-accent text-sur-ton hover:bg-accent-appui",
  info: "bg-info text-sur-ton hover:bg-info-appui",
  attention: "bg-attention text-sur-ton hover:bg-attention-appui",
  alerte: "bg-alerte text-sur-ton hover:bg-alerte-appui",
  /** Sans appelant aujourd'hui : la table est complète pour qu'un ton n'ait
      jamais de trou selon la variante choisie. */
  neutre: "bg-texte text-surface hover:bg-texte-secondaire",
} as const;

/**
 * Le même ton, **écrit** : `contour` et `discret` partagent la couleur du
 * libellé (le pas `-texte`, celui qui tient 4,5:1 sur les deux surfaces) et ne
 * diffèrent que par le filet.
 */
const BOUTON_ECRIT = {
  accent: "text-accent-texte",
  info: "text-info-texte",
  attention: "text-attention-texte",
  alerte: "text-alerte-texte",
  neutre: "text-texte-secondaire",
} as const;

export type TonBouton = keyof typeof BOUTON_PLEIN;

/**
 * Deux tailles, et pas trois : le produit n'en emploie que deux — la taille
 * courante d'un formulaire, et celle d'une action posée dans une ligne. Une
 * troisième se discute ici, pas dans un composant.
 */
const TAILLE_BOUTON = {
  petite: "gap-1 rounded-md px-2.5 py-1 text-annexe",
  normale: "gap-1.5 rounded-md px-3 py-1.5 text-annexe",
} as const;

export type TailleBouton = keyof typeof TAILLE_BOUTON;

/**
 * Ce que toute forme de bouton porte. Le contour de focus est ici et pas dans
 * l'appelant : c'est le seul endroit d'où l'on peut promettre qu'aucune action
 * du produit n'est invisible au clavier (WCAG 2.2, 2.4.7).
 *
 * `CIBLE_MINIMALE` y a rejoint le contour de focus (#269), et pour exactement la
 * même raison : c'est d'ici, et de nulle part ailleurs, qu'on peut promettre
 * qu'aucune action du produit n'est sous 24 px (WCAG 2.2 §2.5.8). La taille
 * `petite` écrit son propre pas typographique (`text-annexe`) avec un `py-1`, et
 * ne déclarait donc **aucun plancher** — le balayage de `a11y.test.tsx` la juge
 * sur ce qu'elle promet, pas sur ce qu'elle mesure, et il a raison : la hauteur
 * réelle y dépend d'un interligne que rien ne fixe. Le défaut n'était visible
 * d'aucun des dix écrans jusqu'à ce que le fil de conversation arrive sur
 * `/chat` avec les boutons de sources de #482 — c'est le filet qui a servi, pas
 * la relecture. Un plancher, jamais une hauteur : un libellé qui passe à la
 * ligne doit pouvoir grandir.
 */
const BOUTON_SOCLE =
  `inline-flex ${CIBLE_MINIMALE} shrink-0 cursor-pointer items-center justify-center font-medium ` +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
  "disabled:cursor-not-allowed disabled:opacity-50";

function classesBouton(
  variante: VarianteBouton,
  ton: TonBouton,
  taille: TailleBouton,
  className: string,
): string {
  const forme =
    variante === "plein"
      ? BOUTON_PLEIN[ton]
      : variante === "contour"
        ? `border border-bord-fort ${BOUTON_ECRIT[ton]} hover:bg-survol`
        : `${BOUTON_ECRIT[ton]} hover:bg-survol`;
  return [BOUTON_SOCLE, TAILLE_BOUTON[taille], forme, className]
    .filter(Boolean)
    .join(" ");
}

/** L'anneau qui tourne d'un bouton `occupe` — décoratif, l'état est dit par `aria-busy`. */
function AnneauOccupe() {
  return (
    <span
      aria-hidden="true"
      className="size-3 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent motion-reduce:animate-none"
    />
  );
}

type ApparenceBouton = {
  variante?: VarianteBouton;
  ton?: TonBouton;
  taille?: TailleBouton;
  /** Une icône du jeu, posée devant le libellé — décorative, jamais seule. */
  icone?: Icone;
  /** Mise en page seulement (marge, largeur, ordre) — jamais une couleur. */
  className?: string;
  children: ReactNode;
};

/**
 * L'action du produit. Le **ton** et la **variante** sont des choix nommés, pas
 * un `bg-*` passé en `className` : deux règles de fond dans le même attribut ne
 * se départagent pas par l'ordre d'écriture mais par celui de la feuille
 * générée, donc une surcharge au cas par cas est silencieusement instable
 * (même raison que le `ton` d'une `Carte`).
 *
 * `occupe` n'est pas un synonyme de `disabled` : il dit qu'une action **est en
 * cours**, la rend inerte le temps qu'elle dure et l'annonce (`aria-busy`) — un
 * bouton simplement désactivé, lui, dit qu'il n'y a rien à faire.
 */
export function Bouton({
  variante = "plein",
  ton = "accent",
  taille = "normale",
  icone: Icone,
  occupe = false,
  className = "",
  children,
  type = "button",
  disabled,
  ...reste
}: ApparenceBouton & {
  /** L'action est en vol : bouton inerte, anneau qui tourne, `aria-busy`. */
  occupe?: boolean;
} & Omit<
    ButtonHTMLAttributes<HTMLButtonElement>,
    "className" | "children"
  >) {
  return (
    <button
      // `button` et non `submit` par défaut : dans un formulaire, un bouton sans
      // `type` explicite le soumet — c'est le piège que le produit évite déjà à
      // la main dans chaque appel.
      type={type}
      disabled={disabled || occupe}
      aria-busy={occupe || undefined}
      className={classesBouton(variante, ton, taille, className)}
      {...reste}
    >
      {occupe ? <AnneauOccupe /> : Icone && <Icone className="size-3.5 shrink-0" />}
      {children}
    </button>
  );
}

/**
 * Le même bouton, quand l'action est une **navigation** : une porte de sortie
 * d'un état vide, un renvoi vers l'écran qui fait le travail. C'est un lien —
 * il s'ouvre dans un onglet, il se copie —, il en a seulement l'allure.
 */
export function BoutonLien({
  href,
  variante = "plein",
  ton = "accent",
  taille = "normale",
  icone: Icone,
  className = "",
  children,
}: ApparenceBouton & { href: string }) {
  return (
    <Link
      href={href}
      className={classesBouton(variante, ton, taille, className)}
    >
      {Icone && <Icone className="size-3.5 shrink-0" />}
      {children}
    </Link>
  );
}

/* ------------------------------------------------------------------ *
 * Champ
 * ------------------------------------------------------------------ */

/**
 * Le contrôle lui-même — saisie, liste, zone de texte. Écrit sur les tokens :
 * `bord` est le filet au repos, `bord-fort` celui du focus (le bord qui
 * **identifie un contrôle**, soumis à WCAG 1.4.11, là où le premier est
 * décoratif), et le contour d'accent double le tout pour qui navigue au clavier.
 */
const CLASSE_CONTROLE =
  "w-full rounded-md border border-bord bg-surface px-3 py-1.5 text-corps " +
  "text-texte shadow-sm placeholder:text-texte-secondaire " +
  "focus:border-bord-fort focus-visible:outline-2 focus-visible:outline-offset-1 " +
  "focus-visible:outline-accent disabled:opacity-50";

function classesControle(monospace?: boolean): string {
  return monospace ? `${CLASSE_CONTROLE} font-mono` : CLASSE_CONTROLE;
}

type ApparenceChamp = {
  /**
   * L'identifiant du contrôle — **obligatoire** : c'est lui qui rattache l'aide
   * et l'erreur (`aria-describedby`). Il n'est pas dérivé d'un `useId` parce que
   * ce module est partagé avec des composants serveur, où aucun hook ne peut
   * tourner.
   */
  id: string;
  libelle: ReactNode;
  /** Ce qu'il faut savoir avant de saisir — annoncé avec le champ. */
  aide?: ReactNode;
  /** Ce qui ne va pas — annoncé avec le champ, et pose `aria-invalid`. */
  erreur?: ReactNode;
  /**
   * La saisie est en chasse fixe : un chemin, un motif, un identifiant — ce qui
   * se compare caractère à caractère. Même nom que sur `TuileChiffre`, et même
   * raison : c'est un choix nommé, pas un `font-mono` recollé au `className`.
   */
  monospace?: boolean;
  /** Mise en page du bloc (largeur, colonne) — jamais une couleur. */
  className?: string;
};

/** Ce que l'aide et l'erreur ajoutent au contrôle : de quoi être annoncées. */
function liaisonsChamp({ id, aide, erreur }: ApparenceChamp) {
  const decrit = [aide ? `${id}-aide` : "", erreur ? `${id}-erreur` : ""]
    .filter(Boolean)
    .join(" ");
  return {
    "aria-describedby": decrit || undefined,
    "aria-invalid": erreur ? true : undefined,
  };
}

/**
 * Le libellé, le contrôle, puis ce qui l'explique — toujours dans cet ordre.
 *
 * Le libellé **entoure** le contrôle au lieu de le viser par `htmlFor`, et ce
 * n'est pas un détail de style : `label.control` résout un `for` par
 * `getElementById`, donc **le premier** identifiant de ce nom dans le document.
 * Deux instances du même écran montées ensemble — ce que fait déjà
 * `projet-cadre.test.tsx` — et la seconde perd son nom accessible en silence.
 * L'association implicite, elle, ne peut désigner que le contrôle qu'elle
 * contient. L'aide et l'erreur restent **hors** du libellé : tout texte à
 * l'intérieur entrerait dans le nom accessible du champ.
 */
function CadreChamp({
  id,
  libelle,
  aide,
  erreur,
  className = "",
  children,
}: ApparenceChamp & { children: ReactNode }) {
  return (
    <div className={["flex flex-col gap-1", className].filter(Boolean).join(" ")}>
      <label className="flex flex-col gap-1">
        <span className="text-annexe font-medium text-texte-secondaire">
          {libelle}
        </span>
        {children}
      </label>
      {aide && (
        <p id={`${id}-aide`} className="text-annexe text-texte-secondaire">
          {aide}
        </p>
      )}
      {erreur && (
        // Pas de `role="alert"` ici : l'erreur d'un champ est annoncée par le
        // champ lui-même (`aria-describedby`), et une seconde annonce ferait
        // parler l'écran deux fois pour une seule faute.
        <p id={`${id}-erreur`} className="text-annexe font-medium text-alerte-texte">
          {erreur}
        </p>
      )}
    </div>
  );
}

/** Une saisie sur une ligne. */
export function Champ({
  id,
  libelle,
  aide,
  erreur,
  monospace,
  className,
  ...reste
}: ApparenceChamp &
  Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "className">) {
  const cadre = { id, libelle, aide, erreur, monospace, className };
  return (
    <CadreChamp {...cadre}>
      <input
        id={id}
        className={classesControle(monospace)}
        {...liaisonsChamp(cadre)}
        {...reste}
      />
    </CadreChamp>
  );
}

/** Une liste déroulante — les `<option>` sont l'affaire de l'appelant. */
export function ChampListe({
  id,
  libelle,
  aide,
  erreur,
  monospace,
  className,
  children,
  ...reste
}: ApparenceChamp &
  Omit<SelectHTMLAttributes<HTMLSelectElement>, "id" | "className">) {
  const cadre = { id, libelle, aide, erreur, monospace, className };
  return (
    <CadreChamp {...cadre}>
      <select
        id={id}
        // Les options d'un `select` natif héritent du fond du système, pas de
        // celui du champ : sans cette règle, une liste ouverte repasse en clair
        // sous le thème sombre.
        className={`${classesControle(monospace)} [&>option]:bg-surface`}
        {...liaisonsChamp(cadre)}
        {...reste}
      >
        {children}
      </select>
    </CadreChamp>
  );
}

/** Une saisie sur plusieurs lignes. */
export function ChampTexte({
  id,
  libelle,
  aide,
  erreur,
  monospace,
  className,
  ...reste
}: ApparenceChamp &
  Omit<TextareaHTMLAttributes<HTMLTextAreaElement>, "id" | "className">) {
  const cadre = { id, libelle, aide, erreur, monospace, className };
  return (
    <CadreChamp {...cadre}>
      <textarea
        id={id}
        className={classesControle(monospace)}
        {...liaisonsChamp(cadre)}
        {...reste}
      />
    </CadreChamp>
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
 *
 * `data-chiffre` (#539) : c'est **ici** que « la première des trois places »
 * devient comptable. La règle des trois places plafonne le bandeau de tête à
 * quatre chiffres (docs/30 §4), et `tests/sobriete.test.tsx` reconnaît ce
 * bandeau à ce marqueur plutôt qu'à une liste d'écrans recopiée. Il est posé
 * sur la primitive et non sur les appelants : un cinquième chiffre se compte
 * du seul fait d'être une tuile, sans que personne ait à le déclarer.
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
  /**
   * Ce que la valeur affichée ne dit pas : l'identifiant du run derrière un
   * chiffre, la ventilation derrière un total. Rendu par `Infobulle` depuis
   * #536 — c'était un `title=`, donc rien pour qui n'a pas de souris.
   */
  titre?: string;
  renvoi?: Renvoi;
  icone?: Icone;
}) {
  return (
    // Densité `compacte` et non `normale` : depuis #248 le tableau des tâches
    // prend la hauteur que la page lui laisse, donc tout ce que cette rangée
    // garde en hauteur, il le perd. Un pas nommé plutôt qu'un `py-*` improvisé.
    <Carte densite="compacte" data-chiffre="" className="flex flex-col">
      <p className="flex items-center gap-1.5 text-annexe text-neutral-500 dark:text-neutral-400">
        {Icone && <Icone className="size-3.5 shrink-0" />}
        {libelle}
      </p>
      <p
        className={
          "chiffre mt-1 truncate font-semibold " +
          (monospace ? "font-mono text-corps" : "text-chiffre")
        }
      >
        {titre ? <Infobulle texte={titre}>{valeur}</Infobulle> : valeur}
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
        `inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-annexe font-medium text-sky-700 ` +
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
  icone: Glyphe,
  pulse = false,
  className = "",
  children,
}: {
  ton?: TonBadge;
  /** Contour plutôt qu'aplat : pour ce qui qualifie sans alerter. */
  contour?: boolean;
  /** Une pastille de couleur devant le libellé (états en direct). */
  pastille?: boolean;
  /**
   * Un **glyphe d'état** à la place de la pastille (#709) — la forme dit ce que
   * la couleur seule ne dit pas : ✓ cerclé, ◉ en marche, ✗ barré se distinguent
   * en noir et blanc comme pour qui ne sépare pas le vert du rouge, là où deux
   * pastilles ne diffèrent que par leur teinte. C'est le premier des trois
   * manques mesurés par docs/30 §1.6, et il se comble **à empreinte égale** :
   * le glyphe occupe la place de la pastille, il ne s'ajoute pas à elle.
   *
   * L'emporte sur `pastille` quand les deux sont donnés — en montrer deux
   * ferait chercher lequel porte l'état.
   */
  icone?: Icone;
  /** La pastille — ou le glyphe — bat : ce qui travaille, pas un état stable. */
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
      {Glyphe ? (
        <Glyphe
          aria-hidden="true"
          className={`size-3.5 shrink-0 ${
            pulse ? "animate-pulse motion-reduce:animate-none" : ""
          }`}
        />
      ) : (
        pastille && (
          <span
            aria-hidden="true"
            className={`size-1.5 shrink-0 rounded-full ${TON_PASTILLE[ton]} ${
              pulse ? "animate-pulse motion-reduce:animate-none" : ""
            }`}
          />
        )
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
