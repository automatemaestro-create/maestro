"use client";

/**
 * Le centre de notifications déroulant de la barre supérieure (#119, lot 3 de
 * #116). Une cloche, présente sur toutes les pages via le shell, signale d'un
 * badge le nombre de validations humaines (#48) en attente et ouvre un panneau
 * qui les liste — chacune approuvable / refusable sur place, sans quitter la
 * page courante — puis rappelle l'activité récente notable.
 *
 * Le badge suit les `validations` du contexte global (rechargées à chaque
 * événement WebSocket) : il se met à jour en temps réel. Une demande **traitée**
 * (approuvée ou refusée) quitte l'état « en attente » et disparaît donc du badge
 * de lui-même ; le panneau, lui, reste consultable pour les événements récents
 * même quand plus rien n'attend d'arbitrage.
 *
 * Le comportement du menu (clic à l'extérieur, Échap, focus rendu au bouton)
 * reprend celui de la bascule de thème (#118, `BasculeTheme`).
 *
 * Validations et événements viennent du contexte, donc **du projet actif**
 * (#281) : la cloche ne réclame jamais un arbitrage qui appartient à un projet
 * qu'on n'a pas sous les yeux, et le badge ne compte pas ce qu'on ne peut pas
 * trancher depuis cet écran.
 *
 * Depuis #322 le badge compte **deux** familles d'attente : les validations
 * humaines (#48) et les **briefs** sur lesquels un run s'est arrêté (#320, #321).
 * Une seule pastille pour les deux, et c'est le point : ce qu'elle répond n'est
 * pas « combien de validations » mais « combien de choses m'attendent » — deux
 * compteurs côte à côte obligeraient à faire la somme soi-même, et un brief
 * suspendu resterait invisible tant que la file de validations n'est pas vide.
 * Les briefs y sont **acheminés, pas tranchés** : la carte compacte mène à
 * l'écran, là où une validation se décide sur place — sept sections, des
 * questions et un coût ne tiennent pas dans un panneau de 20 rem, et approuver
 * sans lire est exactement ce que le point de contrôle empêche.
 */

import Link from "next/link";
import { useCallback, useRef, useState } from "react";

import {
  IconeAgent,
  IconeBrief,
  IconeFlecheDroite,
  IconeNotifications,
} from "@/components/Icones";
import { LigneActivite } from "@/components/LigneActivite";
import { BadgeEtat, Carte, CIBLE_MINIMALE } from "@/components/Primitives";
import { resumeArbitrages } from "@/lib/annonces";
import { runsEnAttente } from "@/lib/brief";
import { estNotableNotification, grouperEvenements } from "@/lib/evenements";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle, PAGE_DU_FIL } from "@/lib/navigation";
import { useSurfaceDeroulee } from "@/lib/useSurfaceDeroulee";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  VALIDATION_EN_ATTENTE,
  type ResumeExecution,
  type Validation,
} from "@/lib/types";

/** Décideur d'une validation, tel que fourni par le contexte global (#48). */
type Decider = (tacheId: string, approuve: boolean) => Promise<void>;

/**
 * Nombre de lignes d'activité récente rappelées dans le panneau — des lignes
 * depuis #250, où une rafale repliée n'en occupe qu'une.
 */
const MAX_EVENEMENTS_NOTABLES = 8;

/**
 * Ce que la cloche annonce — le seul endroit où le compte est **nommé**, la
 * pastille n'étant qu'un chiffre décoratif (`aria-hidden`).
 *
 * La formule elle-même vit dans `lib/annonces` depuis #538 : la région assertive
 * du shell dit la **même** file, avec les mêmes mots, et deux formulations
 * auraient fini par diverger — c'est déjà la raison d'être de `lib/brief`. Ce qui
 * reste ici est le cadrage propre à la cloche : le nom du bouton quand rien
 * n'attend.
 */
function etiquetteCloche(validations: number, briefs: number): string {
  const resume = resumeArbitrages(validations, briefs);
  return resume === null ? "Notifications" : `Notifications — ${resume}`;
}

export function CentreNotifications() {
  const { validations, executions, evenements, decider } = useEtatGlobal();
  const [ouvert, setOuvert] = useState(false);
  const conteneur = useRef<HTMLDivElement>(null);
  const declencheur = useRef<HTMLButtonElement>(null);
  const surface = useRef<HTMLDivElement>(null);

  const enAttente = validations.filter(
    (v) => v.statut === VALIDATION_EN_ATTENTE,
  );
  const briefs = runsEnAttente(executions);
  const nb = enAttente.length + briefs.length;
  const notables = grouperEvenements(
    evenements.filter(estNotableNotification),
  ).slice(0, MAX_EVENEMENTS_NOTABLES);

  // Clic à l'extérieur, `Échap` et focus d'entrée viennent du hook partagé
  // (#536). Les flèches, elles, ne s'y appliquent pas — et c'est le hook qui
  // le constate, en ne trouvant aucune entrée de menu : voir le changement de
  // rôle plus bas.
  const fermer = useCallback(() => setOuvert(false), []);
  useSurfaceDeroulee({ ouvert, fermer, conteneur, declencheur, surface });

  const etiquette = etiquetteCloche(enAttente.length, briefs.length);

  return (
    // `data-guide` : la visite guidée (#122) éclaire la cloche — et s'y replie
    // quand aucune validation n'est en attente sur le tableau de bord.
    <div ref={conteneur} data-guide="notifications" className="relative">
      <button
        ref={declencheur}
        type="button"
        onClick={() => setOuvert((avant) => !avant)}
        aria-haspopup="dialog"
        aria-expanded={ouvert}
        aria-label={etiquette}
        className="relative block rounded-md p-2 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        <IconeNotifications className="size-5" />
        {nb > 0 && (
          // Décoratif : le compte est déjà dans l'`aria-label` du bouton.
          <span
            aria-hidden="true"
            className="absolute -top-0.5 -right-0.5 inline-flex min-w-4 items-center justify-center rounded-full bg-rose-600 px-1 text-[0.625rem] leading-4 font-semibold text-white"
          >
            {nb > 9 ? "9+" : nb}
          </span>
        )}
      </button>

      {ouvert && (
        // `dialog` et non `menu` (#536). Un `role="menu"` engage un contenu fait
        // d'entrées `menuitem` — le motif ARIA l'exige, et l'audit du lot 5
        // (#537) le vérifiera. Or ce panneau porte des sections, des titres, des
        // listes et des cartes à **deux boutons d'arbitrage chacune** : ce n'est
        // pas un menu, ça n'en a jamais été un, et le déclarer tel promettait au
        // lecteur d'écran une navigation aux flèches qui ne pouvait pas exister.
        // Non modal à dessein : on y prend une décision sans que la page se
        // fige derrière.
        <div
          ref={surface}
          role="dialog"
          aria-label="Notifications"
          tabIndex={-1}
          className="absolute top-full right-0 z-20 mt-2 flex max-h-[min(70vh,32rem)] w-80 max-w-[calc(100vw-1.5rem)] flex-col overflow-hidden rounded-lg border border-neutral-200 bg-white shadow-lg dark:border-neutral-800 dark:bg-neutral-900"
        >
          <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2 dark:border-neutral-800">
            <span className="text-corps font-semibold">Notifications</span>
            {nb > 0 && (
              <BadgeEtat ton="alerte" className="chiffre">
                {nb} à valider
              </BadgeEtat>
            )}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {/* Les briefs d'abord : un run suspendu bloque **tout** le run,
                là où une validation ne retient qu'une tâche. */}
            {briefs.length > 0 && (
              <section aria-label="Briefs en attente" className="p-2">
                <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                  Briefs à trancher
                </h3>
                <ul className="space-y-2">
                  {briefs.map((run) => (
                    <li key={run.run_id}>
                      <CarteBriefCompacte
                        run={run}
                        surOuverture={() => setOuvert(false)}
                      />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            <section aria-label="Validations en attente" className="p-2">
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                À valider
              </h3>
              {enAttente.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Aucune validation en attente.
                </p>
              ) : (
                <ul className="space-y-2">
                  {enAttente.map((validation) => (
                    <li key={validation.tache_id}>
                      <CarteValidationCompacte
                        validation={validation}
                        decider={decider}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </section>

            {/* L'activité récente notable : le panneau reste consultable même
                quand plus aucune validation n'est en attente (critère #119). */}
            <section
              aria-label="Activité récente"
              className="border-t border-neutral-200 p-2 dark:border-neutral-800"
            >
              <h3 className="px-1 pb-1 text-xs font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
                Activité récente
              </h3>
              {notables.length === 0 ? (
                <p className="px-1 py-1 text-xs text-neutral-500 dark:text-neutral-400">
                  Rien de notable pour l&apos;instant.
                </p>
              ) : (
                <ol className="space-y-0.5">
                  {notables.map((groupe) => (
                    <LigneActivite key={groupe.cle} groupe={groupe} compact />
                  ))}
                </ol>
              )}
            </section>
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Un brief en attente, en version compacte : ce qu'il attend, depuis quand, et
 * le chemin vers l'écran qui le tranche.
 *
 * **Aucun bouton de décision ici**, contrairement à la carte de validation
 * ci-dessous, et c'est la seule différence qui compte : on n'approuve pas sept
 * sections, des questions et un coût depuis une pastille de 20 rem. Le panneau
 * se referme au clic — laisser une cloche ouverte par-dessus l'écran qu'elle
 * vient d'ouvrir masque justement ce qu'on est venu lire.
 *
 * Le chemin est celui du **fil** depuis #484, où le brief se décide (#483) —
 * même raison qu'au panneau du tableau de bord : un renvoi résolu par le menu
 * s'éteint le jour où l'entrée part, et une cloche muette sur un run bloqué est
 * précisément le défaut contre lequel elle existe.
 */
function CarteBriefCompacte({
  run,
  surOuverture,
}: {
  run: ResumeExecution;
  surOuverture: () => void;
}) {
  // ⚠ #483 remplace cette ligne par `entreeParLibelle(PAGE_DU_CADRAGE)`
  // (`lib/brief`) — même valeur. Au merge des deux lots : prendre sa version.
  const page = entreeParLibelle(PAGE_DU_FIL);
  const reponses = run.statut === EXECUTION_EN_ATTENTE_REPONSES;
  if (page === undefined) return null;

  return (
    <Carte densite="compacte" ton="attention">
      <p className="line-clamp-2 text-annexe font-medium" title={run.objectif}>
        {run.objectif || run.run_id}
      </p>
      <p className="mt-0.5 flex items-center gap-1 text-micro text-neutral-500 dark:text-neutral-400">
        <IconeBrief className="size-3 shrink-0" />
        {reponses
          ? "Des questions attendent vos réponses"
          : "Le brief attend votre décision"}
      </p>
      <Link
        href={page.href}
        onClick={surOuverture}
        className={`mt-2 inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-micro font-medium text-amber-800 hover:underline dark:text-amber-300`}
      >
        {reponses ? "Répondre" : "Relire le brief"}
        <IconeFlecheDroite className="size-3 shrink-0" />
      </Link>
    </Carte>
  );
}

/**
 * Une demande de validation en version compacte, taillée pour la largeur du
 * panneau : mêmes informations et même flux de décision que la carte pleine du
 * tableau de bord (`PanneauValidations`), mais resserrés. La décision passe par
 * le `decider` du contexte — le moteur reprend ou annule la tâche —, la demande
 * quitte alors l'état « en attente » et disparaît de la liste (donc du badge).
 */
function CarteValidationCompacte({
  validation,
  decider,
}: {
  validation: Validation;
  decider: Decider;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surDecision = async (approuve: boolean) => {
    setEnCours(true);
    setErreur(null);
    try {
      await decider(validation.tache_id, approuve);
      // Succès : la demande sort de « en attente » au rechargement et la carte
      // se démonte — inutile de rétablir `enCours`. En cas d'échec seulement,
      // on rend la main pour réessayer.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <Carte densite="compacte" ton="attention">
      <p className="text-annexe font-medium" title={validation.tache_id}>
        {validation.titre || validation.tache_id}
      </p>
      {/* L'icône double le mot « Agent » : l'émoji 🤖 le portait seul, et une
          ligne « 🤖 dev » ne disait rien à qui ne voyait pas le pictogramme. */}
      <p className="mt-0.5 flex items-center gap-1 text-micro text-neutral-500 dark:text-neutral-400">
        <IconeAgent className="size-3 shrink-0" />
        Agent {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
      </p>
      {validation.description && (
        <p className="mt-1 line-clamp-2 text-micro whitespace-pre-wrap text-neutral-600 dark:text-neutral-300">
          {validation.description}
        </p>
      )}
      {validation.raison && (
        <p className="mt-1 text-micro text-amber-700 italic dark:text-amber-400">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-2 flex gap-1.5">
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(true)}
          className="rounded bg-emerald-600 px-2 py-1 text-micro font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enCours ? "Envoi…" : "Approuver"}
        </button>
        <button
          type="button"
          disabled={enCours}
          onClick={() => void surDecision(false)}
          className="rounded border border-rose-300 px-2 py-1 text-micro font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-800 dark:text-rose-400 dark:hover:bg-rose-950"
        >
          Refuser
        </button>
      </div>
      {erreur && (
        <p className="mt-1 text-micro text-rose-600 dark:text-rose-400">
          {erreur}
        </p>
      )}
    </Carte>
  );
}
