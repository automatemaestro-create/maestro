"use client";

/**
 * Les briques communes aux sections de la page Paramètres (#121) : le cadre
 * d'une section (ancre + en-tête), la ligne « libellé / aide / contrôle » qui
 * porte un réglage, et l'état vide des sections dont le réglage n'existe pas
 * encore côté API.
 *
 * L'état vide est explicite par construction : il dit **pourquoi** c'est vide et
 * **où** la chose se règle aujourd'hui — jamais un lien mort (critère #121).
 */

import { useEffect, useState, type ReactNode } from "react";

import { Carte } from "@/components/Primitives";
import {
  DECALAGE_ANCRE_PX,
  SECTIONS_PARAMETRES,
  type SectionParametres as Section,
} from "@/lib/parametres";

export function SectionParametres({
  section,
  children,
}: {
  section: Section;
  children: ReactNode;
}) {
  return (
    // `scroll-mt-20` : l'ancre dépose la section juste sous la barre supérieure
    // collante, pas dessous — et exactement sur le repère du sous-menu
    // (DECALAGE_ANCRE_PX, à garder égal à ces 5rem).
    <Carte
      balise="section"
      densite="aeree"
      id={section.id}
      aria-labelledby={`${section.id}-titre`}
      className="scroll-mt-20"
    >
      <h2
        id={`${section.id}-titre`}
        className="text-corps font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400"
      >
        {section.libelle}
      </h2>
      <p className="mt-0.5 text-annexe text-neutral-500 dark:text-neutral-400">
        {section.description}
      </p>
      <div className="mt-4">{children}</div>
    </Carte>
  );
}

/**
 * Un réglage : son libellé, son aide, et le contrôle (ou la valeur) à droite.
 * En colonne étroite le contrôle passe dessous plutôt que d'écraser le texte.
 */
export function LigneReglage({
  libelle,
  aide,
  children,
}: {
  libelle: string;
  aide?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-neutral-100 py-3 first:pt-0 last:border-b-0 last:pb-0 dark:border-neutral-800/60">
      <div className="min-w-48 flex-1">
        <p className="text-sm font-medium">{libelle}</p>
        {aide && (
          <p className="mt-0.5 text-xs text-neutral-500 dark:text-neutral-400">
            {aide}
          </p>
        )}
      </div>
      {/* `shrink-0` : un interrupteur ou un bouton garde sa taille plutôt que
          de s'écraser. `max-w-full` le rattrape quand le contrôle est en fait
          une valeur longue (une URL) : elle se replie et se coupe (`break-all`)
          au lieu d'élargir la page. */}
      {children && <div className="max-w-full shrink-0">{children}</div>}
    </div>
  );
}

/**
 * La place qui manque à la dernière section pour atteindre le repère d'ancre,
 * réservée sous elle. Sans cette réserve, un clic sur les dernières entrées du
 * sous-menu défile jusqu'en butée sans amener la section visée sous la barre
 * supérieure : l'ancre rate sa cible et le sous-menu désigne la section
 * précédente, celle qui occupe encore le repère.
 *
 * Hauteur mesurée, pas forfaitaire : nulle dès que la dernière section remplit
 * l'écran (grande fenêtre, section longue), donc aucun vide gratuit en bas de
 * page.
 */
export function EspaceDefilement() {
  const [hauteur, setHauteur] = useState(0);

  useEffect(() => {
    const derniere = document.getElementById(
      SECTIONS_PARAMETRES[SECTIONS_PARAMETRES.length - 1].id,
    );
    if (derniere === null) return;

    const mesurer = () =>
      setHauteur(
        Math.max(
          0,
          window.innerHeight -
            DECALAGE_ANCRE_PX -
            derniere.getBoundingClientRect().height,
        ),
      );

    // Différé d'un tick (même mécanique que le shell) : l'effet lui-même ne
    // déclenche aucun setState synchrone.
    const tick = setTimeout(mesurer, 0);
    // La dernière section respire (état vide qui se remplit, message d'erreur) :
    // la réserve suit sa hauteur, pas seulement celle de la fenêtre.
    const observateur = new ResizeObserver(mesurer);
    observateur.observe(derniere);
    window.addEventListener("resize", mesurer);
    return () => {
      clearTimeout(tick);
      observateur.disconnect();
      window.removeEventListener("resize", mesurer);
    };
  }, []);

  return <div aria-hidden="true" style={{ height: hauteur }} />;
}

/**
 * Un interrupteur binaire, au clavier comme à la souris (`role="switch"` : le
 * lecteur d'écran annonce « activé / désactivé », pas « bouton »).
 */
export function Interrupteur({
  libelle,
  actif,
  desactive,
  basculer,
}: {
  /** Ce que l'interrupteur commande — l'étiquette accessible. */
  libelle: string;
  actif: boolean;
  desactive?: boolean;
  basculer: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={actif}
      aria-label={libelle}
      disabled={desactive}
      onClick={basculer}
      className={
        "relative inline-flex h-6 w-11 shrink-0 rounded-full border transition-colors motion-reduce:transition-none disabled:opacity-40 " +
        (actif
          ? "border-emerald-600 bg-emerald-600"
          : "border-neutral-300 bg-neutral-200 dark:border-neutral-700 dark:bg-neutral-800")
      }
    >
      <span
        aria-hidden="true"
        className={
          "m-0.5 size-4.5 rounded-full bg-white shadow transition-transform motion-reduce:transition-none dark:bg-neutral-100 " +
          (actif ? "translate-x-5" : "translate-x-0")
        }
      />
    </button>
  );
}

/**
 * Une section (ou un réglage) que l'API ne porte pas encore. La brique est
 * partagée depuis #245 (`components/Primitives`) — la page Projets affichait la
 * même chose avec ses propres classes ; elle reste ré-exportée ici parce que
 * c'est de Paramètres que l'idée vient et que les cinq sections l'importent
 * ainsi.
 */
export { EtatVide } from "@/components/Primitives";
