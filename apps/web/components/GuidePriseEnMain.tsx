"use client";

/**
 * La visite guidée de la Control Tower (#122, lot 6 de #116) : une surbrillance
 * qui se pose sur l'élément réel de l'interface — la sidebar, le Kanban, la
 * cloche, les coûts — et une carte qui l'explique, pas à pas.
 *
 * Implémentation maison plutôt qu'une bibliothèque d'onboarding : le besoin
 * tient en un rectangle mesuré et une carte positionnée, là où les libs du
 * genre traînent leur propre thème et leurs propres portails à réconcilier avec
 * Tailwind et le mode sombre. Le contenu, lui, vit dans `lib/guide.ts`.
 *
 * Trois points méritent leur explication :
 *
 * - **La mesure est continue** (une boucle `requestAnimationFrame` tant qu'une
 *   étape est affichée) : elle suit le défilement, les changements de taille et
 *   les panneaux qui apparaissent après coup — le tableau de bord ne rend ses
 *   panneaux qu'une fois l'état chargé. Un `setState` n'est émis que lorsque le
 *   rectangle **change** vraiment, sinon la boucle ne coûte qu'une mesure.
 * - **Une étape peut changer de page** (`chemin`) : la visite y navigue
 *   elle-même, puis reprend la mesure quand le chemin a suivi.
 * - **Une ancre peut manquer** (panneau vide, chargement en cours) : passé un
 *   délai de patience, l'étape est présentée au centre plutôt que sautée.
 */

import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";

import {
  ecouterLancementGuide,
  ETAPES_GUIDE,
  lireGuideVu,
  marquerGuideVu,
} from "@/lib/guide";
import { usePiegeDeFocus } from "@/lib/usePiegeDeFocus";

/** Le rectangle de l'ancre, en coordonnées de la fenêtre (position fixe). */
type Rect = { haut: number; gauche: number; largeur: number; hauteur: number };

/** Respiration entre l'élément mis en avant et le bord de la surbrillance. */
const MARGE_SURBRILLANCE = 6;
/** Écart entre la surbrillance et la carte. */
const ECART_CARTE = 12;
/** Marge minimale conservée avec les bords de la fenêtre. */
const MARGE_ECRAN = 12;
/** Largeur de la carte, réduite d'office sur les fenêtres plus étroites. */
const LARGEUR_CARTE = 320;
/** Hauteur supposée de la carte, le temps de choisir son côté. */
const HAUTEUR_CARTE = 210;
/** Patience avant de renoncer à une ancre qui n'arrive pas (ms). */
const DELAI_ANCRE = 2500;
/** Délai avant le lancement automatique — le temps que la page se pose (ms). */
const DELAI_PREMIERE_VISITE = 700;

export function GuidePriseEnMain() {
  const [actif, setActif] = useState(false);
  const [index, setIndex] = useState(0);
  const [cible, setCible] = useState<Rect | null>(null);

  const chemin = usePathname();
  const router = useRouter();
  const carte = useRef<HTMLDivElement>(null);
  /** Ce qui avait le focus avant la visite — on le lui rend à la sortie. */
  const focusInitial = useRef<HTMLElement | null>(null);

  const etape = ETAPES_GUIDE[index];
  const derniere = index === ETAPES_GUIDE.length - 1;

  const demarrer = useCallback(() => {
    focusInitial.current =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    setCible(null);
    setIndex(0);
    setActif(true);
  }, []);

  const arreter = useCallback(() => {
    // Quittée en route ou menée à son terme, la visite ne se relance plus
    // d'elle-même : dans les deux cas l'utilisateur a tranché.
    marquerGuideVu();
    setActif(false);
    setCible(null);
    focusInitial.current?.focus();
  }, []);

  const aller = useCallback(
    (prochain: number) => {
      if (prochain < 0) return;
      if (prochain >= ETAPES_GUIDE.length) {
        arreter();
        return;
      }
      // Remis à zéro avant le changement d'étape : sans quoi la surbrillance
      // resterait un instant sur l'ancre précédente.
      setCible(null);
      setIndex(prochain);
    },
    [arreter],
  );

  // Premier lancement (état persisté) et relances depuis le menu d'aide. Le
  // localStorage n'est lu qu'après l'hydratation : le rendu serveur ne le
  // connaît pas, le lire pendant le rendu ferait diverger les deux arbres.
  useEffect(() => {
    const tick = setTimeout(() => {
      if (!lireGuideVu()) demarrer();
    }, DELAI_PREMIERE_VISITE);
    const detacher = ecouterLancementGuide(demarrer);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, [demarrer]);

  // Navigation vers la page de l'étape, puis mesure continue de son ancre.
  useEffect(() => {
    if (!actif) return;
    if (etape.chemin && chemin !== etape.chemin) {
      router.push(etape.chemin);
      // L'effet est rejoué quand `chemin` a suivi, et la mesure reprend là.
      return;
    }

    let image = 0;
    let centre = false;
    let mesure: Rect | null = null;
    const echeance = Date.now() + DELAI_ANCRE;

    const suivre = () => {
      const element = trouverAncre(etape.ancres);
      if (element) {
        if (!centre) {
          centre = true;
          // Défilement instantané : une surbrillance en transition douce
          // dériverait visiblement derrière un défilement animé.
          element.scrollIntoView({ block: "center", behavior: "auto" });
        }
        const rect = element.getBoundingClientRect();
        const suivant: Rect = {
          haut: rect.top,
          gauche: rect.left,
          largeur: rect.width,
          hauteur: rect.height,
        };
        if (!memeRect(mesure, suivant)) {
          mesure = suivant;
          setCible(suivant);
        }
      } else if (Date.now() > echeance && mesure !== null) {
        mesure = null;
        setCible(null);
      }
      image = requestAnimationFrame(suivre);
    };

    image = requestAnimationFrame(suivre);
    return () => cancelAnimationFrame(image);
  }, [actif, etape, chemin, router]);

  // Le clavier suffit à mener la visite : flèches pour avancer et reculer,
  // Échap pour sortir.
  useEffect(() => {
    if (!actif) return;
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key === "Escape") arreter();
      else if (evenement.key === "ArrowRight") aller(index + 1);
      else if (evenement.key === "ArrowLeft") aller(index - 1);
      else return;
      evenement.preventDefault();
    };
    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [actif, index, aller, arreter]);

  // Le focus suit l'étape : les boutons de la carte sont alors à un Tab, et le
  // lecteur d'écran annonce le titre et le texte de l'étape courante.
  useEffect(() => {
    if (actif) carte.current?.focus();
  }, [actif, index]);

  // La tabulation reste dans la carte tant que la visite dure (#536). Le piège
  // est ici la seule pièce à ajouter : le voile absorbe déjà les clics, `Échap`
  // et la restauration du focus sont tenus plus haut. Conditionné par `actif`,
  // parce que ce composant reste monté entre deux visites.
  usePiegeDeFocus(carte, actif);

  if (!actif) return null;

  return (
    <>
      {/* Le voile absorbe les clics : la page reste lisible sous la visite,
          mais on n'y agit pas par mégarde. */}
      <div className="fixed inset-0 z-40" aria-hidden="true" />
      {cible ? (
        // L'ombre portée démesurée assombrit tout **sauf** le rectangle : un
        // seul élément, pas de découpe en quatre bandes à recoller. Le liseré
        // passe par `outline` et non par `ring` : Tailwind rend les anneaux en
        // `box-shadow`, que l'ombre du voile écraserait.
        <div
          aria-hidden="true"
          className="pointer-events-none fixed z-40 rounded-lg outline-2 outline-sky-500 transition-[top,left,width,height] duration-200 motion-reduce:transition-none"
          style={{
            top: cible.haut - MARGE_SURBRILLANCE,
            left: cible.gauche - MARGE_SURBRILLANCE,
            width: cible.largeur + 2 * MARGE_SURBRILLANCE,
            height: cible.hauteur + 2 * MARGE_SURBRILLANCE,
            boxShadow: "0 0 0 9999px rgba(2, 6, 23, 0.6)",
          }}
        />
      ) : (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-40 bg-slate-950/60"
        />
      )}

      <div
        ref={carte}
        role="dialog"
        aria-modal="true"
        aria-labelledby="guide-titre"
        aria-describedby="guide-texte"
        tabIndex={-1}
        style={{ ...placerCarte(cible), width: LARGEUR_CARTE }}
        className="fixed z-50 flex max-w-[calc(100vw-1.5rem)] flex-col rounded-lg border border-neutral-200 bg-white p-4 shadow-2xl outline-none dark:border-neutral-700 dark:bg-neutral-900"
      >
        <div className="flex items-baseline justify-between gap-3">
          <p className="text-xs font-medium text-sky-700 dark:text-sky-400">
            Étape {index + 1} sur {ETAPES_GUIDE.length}
          </p>
          <button
            type="button"
            onClick={arreter}
            // Le raccourci a rejoint le nom accessible (#536) : dans un `title`
            // il n'était annoncé à personne, alors que c'est précisément le
            // clavier qu'il concerne.
            aria-label="Quitter la visite (Échap)"
            className="-mr-1 rounded px-1.5 py-0.5 text-xs text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
          >
            Quitter
          </button>
        </div>

        <h2 id="guide-titre" className="mt-1 text-sm font-semibold">
          {etape.titre}
        </h2>
        <p
          id="guide-texte"
          className="mt-1.5 min-h-0 overflow-y-auto text-sm text-neutral-600 dark:text-neutral-300"
        >
          {etape.texte}
        </p>

        <div className="mt-4 flex items-center gap-2">
          {/* Décoratif : l'avancement est déjà énoncé en toutes lettres. */}
          <div className="flex flex-1 items-center gap-1" aria-hidden="true">
            {ETAPES_GUIDE.map((autre, rang) => (
              <span
                key={autre.id}
                className={
                  "size-1.5 rounded-full " +
                  (rang === index
                    ? "bg-sky-500"
                    : rang < index
                      ? "bg-sky-500/40"
                      : "bg-neutral-300 dark:bg-neutral-700")
                }
              />
            ))}
          </div>
          <button
            type="button"
            onClick={() => aller(index - 1)}
            disabled={index === 0}
            className="rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-700 hover:bg-neutral-100 disabled:opacity-40 disabled:hover:bg-transparent dark:border-neutral-700 dark:text-neutral-200 dark:hover:bg-neutral-800"
          >
            Précédent
          </button>
          <button
            type="button"
            onClick={() => aller(index + 1)}
            className="rounded-md bg-sky-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-sky-700"
          >
            {derniere ? "Terminer" : "Suivant"}
          </button>
        </div>
      </div>
    </>
  );
}

/**
 * La première ancre présente **et visible**. Un élément masqué par le
 * responsive (`hidden sm:inline`) mesure zéro : il n'éclairerait rien, on passe
 * à la candidate suivante.
 */
function trouverAncre(selecteurs: string[]): HTMLElement | null {
  for (const selecteur of selecteurs) {
    const element = document.querySelector<HTMLElement>(selecteur);
    if (!element) continue;
    const rect = element.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) return element;
  }
  return null;
}

function memeRect(a: Rect | null, b: Rect): boolean {
  return (
    a !== null &&
    a.haut === b.haut &&
    a.gauche === b.gauche &&
    a.largeur === b.largeur &&
    a.hauteur === b.hauteur
  );
}

/**
 * Le placement de la carte autour de la surbrillance : dessous de préférence,
 * puis dessus, puis à droite ou à gauche — la sidebar, haute comme la fenêtre,
 * ne laisse de place qu'à côté. La carte est ancrée par le bord **opposé** à
 * l'ancre (`bottom` plutôt que `top` quand elle est au-dessus) et bornée par un
 * `maxHeight` : elle ne peut donc pas déborder de la fenêtre, quelle que soit
 * la longueur du texte. Sans ancre du tout, la carte se centre — c'est alors
 * une simple boîte de dialogue.
 */
function placerCarte(cible: Rect | null): CSSProperties {
  const centree: CSSProperties = {
    top: "50%",
    left: "50%",
    transform: "translate(-50%, -50%)",
    maxHeight: `calc(100vh - ${2 * MARGE_ECRAN}px)`,
  };
  if (!cible) return centree;

  const fenetreL = window.innerWidth;
  const fenetreH = window.innerHeight;
  const largeur = Math.min(LARGEUR_CARTE, fenetreL - 2 * MARGE_ECRAN);
  const bas = cible.haut + cible.hauteur;
  const droite = cible.gauche + cible.largeur;
  const requis = ECART_CARTE + MARGE_SURBRILLANCE + MARGE_ECRAN;

  const borner = (valeur: number, maximum: number) =>
    Math.min(Math.max(valeur, MARGE_ECRAN), Math.max(MARGE_ECRAN, maximum));
  const centreX = borner(
    cible.gauche + cible.largeur / 2 - largeur / 2,
    fenetreL - MARGE_ECRAN - largeur,
  );
  const centreY = borner(
    cible.haut + cible.hauteur / 2 - HAUTEUR_CARTE / 2,
    fenetreH - MARGE_ECRAN - HAUTEUR_CARTE,
  );
  const hauteurCote = fenetreH - 2 * MARGE_ECRAN;

  if (fenetreH - bas >= HAUTEUR_CARTE + requis) {
    return {
      top: bas + MARGE_SURBRILLANCE + ECART_CARTE,
      left: centreX,
      maxHeight: fenetreH - bas - requis,
    };
  }
  if (cible.haut >= HAUTEUR_CARTE + requis) {
    return {
      bottom: fenetreH - cible.haut + MARGE_SURBRILLANCE + ECART_CARTE,
      left: centreX,
      maxHeight: cible.haut - requis,
    };
  }
  if (fenetreL - droite >= largeur + requis) {
    return {
      left: droite + MARGE_SURBRILLANCE + ECART_CARTE,
      top: centreY,
      maxHeight: hauteurCote,
    };
  }
  if (cible.gauche >= largeur + requis) {
    return {
      right: fenetreL - cible.gauche + MARGE_SURBRILLANCE + ECART_CARTE,
      top: centreY,
      maxHeight: hauteurCote,
    };
  }
  // Une ancre qui remplit la fenêtre — le Kanban, la zone de contenu — ne
  // laisse de place nulle part : la carte se pose alors en bas, où elle masque
  // le moins de ce qu'elle décrit, plutôt qu'en plein milieu.
  return {
    bottom: MARGE_ECRAN,
    left: centreX,
    maxHeight: hauteurCote,
  };
}
