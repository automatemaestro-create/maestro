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
 * Ce que dit une étape non cochée d'une tâche **soldée** (#1112).
 *
 * « à faire » et « en cours » promettent une suite qui ne viendra pas : la tâche
 * ne travaille plus. Ce troisième mot n'est pas un état de plus dans le
 * contrat — l'agent n'en émet jamais —, c'est la **lecture** d'un état connu à
 * un instant connu, exactement comme `skipped` s'ajoute chez GitHub Actions aux
 * trois issues d'un job. Le relevé, lui, n'est pas réécrit : #944 a tranché que
 * l'écart se dit, jamais qu'il se comble.
 */
export const ETAPE_NON_RAPPORTEE = "non rapportée à la clôture";

/**
 * Les hachures d'une case **non rapportée** — dessinées avec la couleur de la
 * surface, donc justes dans les deux thèmes sans un `dark:` ni un token de plus.
 *
 * Le motif est celui de la barre d'avancement d'un run (`components/runs/EtatRun`,
 * #709), et il est repris pour ce qu'il y dit déjà : *présent, compté,
 * visiblement pas fini*. Statique, donc sans `motion-reduce:` à prévoir.
 */
const HACHURE =
  "repeating-linear-gradient(135deg, var(--surface) 0 2px, transparent 2px 5px)";

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
 *
 * `soldee` (#1112) ajoute la seule chose que la rangée ne savait pas dire : la
 * tâche **ne travaille plus**. Une case grise « à faire » sous un badge
 * « Terminée » annonce une suite qui ne viendra pas, et une case ambre « en
 * cours » contredit franchement le verdict. Soldée, elles portent donc toutes la
 * même forme — hachurée — et le même mot (`ETAPE_NON_RAPPORTEE`) : le relevé
 * n'est pas réécrit, il est **daté**.
 */
export function AvancementEtapes({
  etapes,
  faites,
  taille = "ample",
  soldee = false,
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
  /**
   * La tâche est **soldée** : ce qui n'est pas coché ne le sera plus (#1112).
   * `false` par défaut — une tâche qui travaille rend exactement la rangée
   * d'avant ce ticket.
   */
  soldee?: boolean;
}) {
  const total = etapes.length;
  const restantes = total - faites;
  const marque = soldee && restantes > 0;
  return (
    <div
      role="progressbar"
      aria-label="Avancement des étapes"
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={faites}
      aria-valuetext={
        `${faites} étape${faites > 1 ? "s" : ""} terminée${faites > 1 ? "s" : ""} sur ${total}` +
        (marque
          ? ` — ${restantes} ${restantes > 1 ? "non rapportées" : "non rapportée"} à la clôture`
          : "")
      }
      className="flex w-full gap-0.5"
    >
      {etapes.map((etape, rang) => {
        const faite = etape.etat === ETAPE_FAITE;
        return (
          <span
            key={`${rang}-${etape.libelle}`}
            aria-hidden="true"
            // La hachure est un fond, pas une classe : le motif porte
            // `var(--surface)` et suit donc le thème sans second jeu de règles.
            style={!faite && marque ? { backgroundImage: HACHURE } : undefined}
            className={
              (taille === "ample" ? "h-1 " : "h-0.5 ") +
              "flex-1 rounded-full transition-colors motion-reduce:transition-none " +
              (faite
                ? "bg-emerald-500"
                : marque
                  ? "bg-attention"
                  : etape.etat === ETAPE_EN_COURS
                    ? "bg-amber-500"
                    : "bg-neutral-200 dark:bg-neutral-800")
            }
          />
        );
      })}
    </div>
  );
}

/**
 * Une ligne de checklist. La case n'est pas un `<input>` : l'avancement vient du
 * moteur, il ne se coche pas à la main — un contrôle cliquable promettrait une
 * action qui n'existe pas.
 *
 * `soldee` (#1112) est le pendant exact de celui de la rangée, et c'est le
 * partage que le ticket a retenu : le nœud du graphe dit **combien** d'étapes ne
 * sont pas rapportées, cette liste dit **lesquelles** — un nœud de 16 rem n'ayant
 * pas la place de les nommer.
 */
export function LigneEtape({
  etape,
  soldee = false,
}: {
  etape: EtapeAffichee;
  /** La tâche est soldée : une étape non cochée ne le sera plus. */
  soldee?: boolean;
}) {
  const faite = etape.etat === ETAPE_FAITE;
  const nonRapportee = !faite && soldee;
  const enCours = !nonRapportee && etape.etat === ETAPE_EN_COURS;
  return (
    <li className="flex items-start gap-2 text-corps">
      <span
        aria-hidden="true"
        className={
          "mt-0.5 flex size-4 shrink-0 items-center justify-center rounded border " +
          (faite
            ? "border-emerald-500 bg-emerald-500 text-white"
            : nonRapportee
              ? "border-attention text-attention-texte"
              : enCours
                ? "border-amber-500 text-amber-500"
                : "border-neutral-300 dark:border-neutral-600")
        }
      >
        {faite && <IconeCoche className="size-3" />}
        {enCours && <span className="size-1.5 rounded-full bg-amber-500" />}
        {/* Une barre, et non une croix : rien n'a échoué — la ligne est
            simplement restée sans réponse. */}
        {nonRapportee && <span className="h-px w-2 bg-attention" />}
      </span>
      <span
        className={
          faite
            ? "text-neutral-400 line-through dark:text-neutral-500"
            : "text-neutral-700 dark:text-neutral-300"
        }
      >
        {etape.libelle}
        {/* Écrit à l'œil **et** entendu : le mot remplace ici la mention
            `sr-only`, il ne s'y ajoute pas — la répéter la ferait lire deux
            fois. */}
        {nonRapportee ? (
          <span className="ml-1.5 text-annexe text-attention-texte">
            — {ETAPE_NON_RAPPORTEE}
          </span>
        ) : (
          <span className="sr-only">
            {" "}
            — {ETAT_ETAPE_EN_TOUTES_LETTRES[etape.etat]}
          </span>
        )}
      </span>
    </li>
  );
}
