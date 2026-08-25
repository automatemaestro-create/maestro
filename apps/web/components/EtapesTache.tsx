"use client";

/**
 * La **checklist d'une tâche**, telle qu'elle se lit : la rangée de cases qui
 * dit *combien*, et la ligne qui dit *quoi*.
 *
 * Ces deux briques sont nées dans le panneau de détail (#251, puis #489 pour la
 * rangée de cases) ; #491 les en sort le jour où un second écran a eu à dire la
 * même chose — **la vue pipeline**, dont chaque nœud porte sa checklist qui se
 * coche en direct. Même raison que l'extraction de `components/runs/EtatRun`
 * (#475) : deux formulations du même état finiraient par diverger, et une
 * checklist qu'on lit d'une façon dans un panneau et d'une autre sur un nœud est
 * une checklist dont on doute.
 *
 * Rien n'a changé de comportement au passage — c'est l'extraction de ce que
 * `PanneauDetailTache` portait, à la seule addition de `taille` sur la rangée :
 * un panneau lui donne toute sa largeur, un nœud de graphe la lit à 16 rem.
 */

import { IconeCoche } from "@/components/Icones";
import type { EtapeAffichee } from "@/lib/detailTache";
import { ETAPE_EN_COURS, ETAPE_FAITE } from "@/lib/types";

/** Ce que l'état d'une étape dit à voix haute (lecteurs d'écran, `title`). */
export const ETAT_ETAPE_EN_TOUTES_LETTRES: Record<
  EtapeAffichee["etat"],
  string
> = {
  faite: "terminée",
  en_cours: "en cours",
  a_faire: "à faire",
};

/**
 * L'avancement de la checklist, **une case par étape** — la même lecture d'un
 * coup d'œil, mais qui ne peut pas reculer (#489).
 *
 * C'était une barre unique remplie à `faites / total`, et c'est le dénominateur
 * qui a changé de nature : la checklist n'est plus déclarée une fois pour toutes
 * par le plan, elle est **complétée par l'agent en cours de route**
 * (`maestro.detail_tache`, l'arbitrage). Un agent qui découvre une étape de plus
 * fait donc grandir le total — et sur une barre proportionnelle, « 3/5 » qui
 * devient « 3/8 » se voit comme un recul : la barre se rétracte alors que rien
 * n'a été perdu. Une progression qui redescend est pire que pas de progression
 * du tout, c'est le critère du ticket.
 *
 * Une case par étape retire au dénominateur son pouvoir de rétracter : ce qui
 * est acquis reste allumé, la rangée s'**allonge**. C'est aussi ce que montre un
 * pipeline d'intégration continue, pour la même raison — on y lit des étapes
 * franchies, jamais un pourcentage. Le compteur `3/8` du titre dit, lui, que le
 * dénominateur a bougé : les deux moitiés du critère se répondent.
 */
export function AvancementEtapes({
  etapes,
  faites,
  taille = "ample",
}: {
  etapes: EtapeAffichee[];
  faites: number;
  /**
   * `ample` dans un panneau, `compacte` sur un nœud de graphe — où la rangée
   * partage 16 rem avec le titre, l'agent et le coût. Seule l'épaisseur change :
   * une case reste une case, sans quoi les deux écrans ne compteraient pas
   * pareil.
   */
  taille?: "ample" | "compacte";
}) {
  const total = etapes.length;
  return (
    <div
      role="progressbar"
      aria-label="Avancement des étapes"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={faites}
      aria-valuetext={`${faites} étape${faites > 1 ? "s" : ""} terminée${faites > 1 ? "s" : ""} sur ${total}`}
      className="flex w-full gap-0.5"
    >
      {etapes.map((etape, rang) => (
        <span
          key={`${rang}-${etape.libelle}`}
          aria-hidden="true"
          className={
            (taille === "ample" ? "h-1 " : "h-0.5 ") +
            "flex-1 rounded-full transition-colors motion-reduce:transition-none " +
            (etape.etat === ETAPE_FAITE
              ? "bg-emerald-500"
              : etape.etat === ETAPE_EN_COURS
                ? "bg-amber-500"
                : "bg-neutral-200 dark:bg-neutral-800")
          }
        />
      ))}
    </div>
  );
}

/**
 * Une ligne de checklist. La case n'est pas un `<input>` : l'avancement vient du
 * moteur, il ne se coche pas à la main — un contrôle cliquable promettrait une
 * action qui n'existe pas.
 */
export function LigneEtape({ etape }: { etape: EtapeAffichee }) {
  const faite = etape.etat === ETAPE_FAITE;
  const enCours = etape.etat === ETAPE_EN_COURS;
  return (
    <li className="flex items-start gap-2 text-corps">
      <span
        aria-hidden="true"
        className={
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border " +
          (faite
            ? "border-emerald-500 bg-emerald-500 text-white"
            : enCours
              ? "border-amber-500 text-amber-500"
              : "border-neutral-300 dark:border-neutral-600")
        }
      >
        {faite && <IconeCoche className="size-3" />}
        {enCours && <span className="size-1.5 rounded-full bg-amber-500" />}
      </span>
      <span
        className={
          faite
            ? "text-neutral-400 line-through dark:text-neutral-500"
            : "text-neutral-700 dark:text-neutral-300"
        }
      >
        {etape.libelle}
        <span className="sr-only">
          {" "}
          — {ETAT_ETAPE_EN_TOUTES_LETTRES[etape.etat]}
        </span>
      </span>
    </li>
  );
}
