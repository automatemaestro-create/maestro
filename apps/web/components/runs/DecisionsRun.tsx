"use client";

/**
 * Les décisions qu'un agent a tranchées **seul** pendant un run (#1026, lot 4 de
 * #1019, docs/05 §6.18) : la cinquième lecture de la vue d'un run.
 *
 * Le parent pose une condition à l'autonomie — *elle n'est acceptable que si elle
 * se vérifie après coup*. Le lot 2 (#1024) a fait consigner ces décisions ; sans
 * cet écran elles étaient **écrites puis invisibles**, noyées dans le journal et
 * rendues par la branche `default` de `resumeEvenement`, qui affichait le statut
 * brut du bus. La question à laquelle un coup d'œil doit répondre est donc
 * **« qu'est-ce que cet agent a décidé sans moi, et pourquoi ? »**.
 *
 * ## La forme, et d'où elle vient
 *
 * Trois variantes ont été rendues sur la vraie stack et jugées sur pièces par le
 * sous-agent `regard-neuf` (#980, #1009) contre les références capturées en
 * direct par la veille — Grafana (Alerting › History), le bloc « Annotations »
 * d'un run GitHub Actions, la timeline d'une Pull Request. Le choix et les deux
 * écartées sont consignés sur le ticket, commentaire `## Variante retenue` ;
 * voici ce qu'il engage ici :
 *
 * - **une ligne par décision, à plat et dans le temps, jamais une carte** : un
 *   seul conteneur, des lignes séparées par un filet. Une carte par décision
 *   aurait été la recopie que docs/30 §2.2 a mesurée, et le groupement par tâche
 *   — la variante C — ouvrait **trois blocs** dans le corps, ce que le second
 *   critère du ticket refuse ;
 * - **la décision d'abord, la raison dessous, en second plan** : deux pas déjà au
 *   barème (`text-corps`, puis `text-annexe` en gris), jamais un troisième. La
 *   variante B les mettait en colonnes jumelles — on lisait alors l'acte et son
 *   motif en aller-retour, pour des rangées deux fois plus hautes ;
 * - **l'hypothèse se distingue par une forme et un mot, jamais par une teinte** :
 *   une icône dédiée dans la gouttière *et* la pastille « Hypothèse », parce que
 *   la couleur ne porte jamais le sens seule (docs/30 §3.4, `a11y.test.tsx`) ;
 * - **aucun bloc de plus dans le corps** : cette lecture vit dans la bascule de
 *   vues, qui est une `<nav>` et n'occupe aucune des trois places (docs/30 §4.2 —
 *   la vue d'un run tient en 2 blocs + onglets, et elle y tient toujours).
 *
 * Deux défauts du brouillon retenu, relevés par le regard neuf et corrigés ici :
 * l'heure relative tenait sur deux lignes dans la gouttière (elle ne s'y coupe
 * plus), et le renvoi vers la tâche était tronqué sans que son nom complet soit
 * récupérable (il le porte désormais en infobulle de souris).
 *
 * ## Ce que l'écran ne fait pas
 *
 * Il ne **juge** rien : une décision autonome n'y est ni approuvée ni refusée.
 * Répondre à une question reste l'affaire du fil (#1025), et un **acte** soumis à
 * validation reste refusé sans réponse (EF-08) — cette liste dit ce qui est
 * arrivé, elle n'ouvre aucun geste dessus. Il ne **réécrit** rien non plus :
 * décision et motif sortent des deux champs que le moteur sépare à l'écriture
 * (#1024), et les redécouper ici serait deviner par la forme ce que le journal
 * savait déjà.
 *
 * Le **renvoi vers la tâche** ouvre son panneau de détail sur place
 * (`PanneauDetailTache`, #251), comme le fait une carte du Kanban : c'est le seul
 * « lien vers la tâche » que ce produit ait — il n'y a pas de route par tâche —,
 * et en inventer un autre ici en ferait deux.
 */

import { useRef, useState } from "react";

import { IconeDecision, IconeHypothese } from "@/components/Icones";
import { PanneauDetailTache } from "@/components/PanneauDetailTache";
import {
  BadgeEtat,
  Carte,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import type { Reassigner } from "@/components/SelecteurReassignation";
import { formatDateHeure, formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import type { Decision, EtatAgent, Tache } from "@/lib/types";
import { useDecisionsRun } from "@/lib/useDecisionsRun";

export function DecisionsRun({
  runId,
  /** Les tâches **de ce run**, déjà chargées par la vue : de quoi ouvrir un détail. */
  taches,
  agents,
  reassigner,
  revision,
}: {
  runId: string;
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
  revision: number;
}) {
  const { decisions, chargement, erreur } = useDecisionsRun(runId, revision);
  // La tâche dont le panneau de détail est ouvert, et le bouton qui l'a ouvert —
  // c'est l'appelant qui rend le focus à la fermeture, lui seul connaissant le
  // déclencheur (`PanneauDetailTache`).
  const [ouverte, setOuverte] = useState<string | null>(null);
  const declencheur = useRef<HTMLButtonElement | null>(null);
  const affichee = taches.find((tache) => tache.id === ouverte) ?? null;

  const entrees = decisions?.entrees ?? [];

  return (
    <section data-guide="decisions" aria-label="Décisions autonomes du run">
      <EnTeteSection
        titre="Décisions"
        icone={IconeDecision}
        className="mb-2"
        aside={<Compte decisions={decisions} />}
      />

      {entrees.length === 0 ? (
        <EtatVide
          icone={IconeDecision}
          message={
            chargement
              ? "Lecture des décisions de ce run…"
              : erreur !== null
                ? "Décisions indisponibles — la lecture a échoué."
                : "Aucune décision tranchée seule sur ce run : aucun agent n'a eu à trancher hors de son brief, et aucune question n'est restée sans réponse."
          }
        />
      ) : (
        <Carte balise="div" densite="aucune">
          {/* `<ol>` : l'ordre est une information — du plus récent au plus
              ancien, comme le journal du run, et le backend l'a déjà trié. */}
          <ol className="divide-y divide-bord">
            {entrees.map((prise) => (
              <LigneDecision
                key={prise.id}
                prise={prise}
                ouvrir={(bouton) => {
                  declencheur.current = bouton;
                  setOuverte(prise.tache_id);
                }}
              />
            ))}
          </ol>
        </Carte>
      )}

      {decisions?.tronquee && (
        <p className="mt-2 text-annexe text-texte-secondaire">
          Les {decisions.plafond} décisions les plus récentes sur {decisions.total} —
          les précédentes restent au journal du run.
        </p>
      )}

      {affichee !== null && (
        <PanneauDetailTache
          tache={affichee}
          agents={agents}
          reassigner={reassigner}
          fermer={() => {
            setOuverte(null);
            declencheur.current?.focus();
          }}
        />
      )}
    </section>
  );
}

/**
 * Le compte, à droite du titre : combien de décisions, dont combien prises faute
 * de réponse.
 *
 * Les deux chiffres viennent du backend et comptent **avant** le plafond : les
 * recompter sur `entrees` donnerait faux dès que la liste est tronquée. Rien ne
 * s'affiche quand il n'y a rien à compter — un « 0 décision » à côté d'un état
 * vide qui dit déjà la même chose serait la dire deux fois.
 */
function Compte({ decisions }: { decisions: { total: number; hypotheses: number } | null }) {
  if (decisions === null || decisions.total === 0) return null;
  return (
    <p className="chiffre text-annexe text-texte-secondaire">
      {decisions.total} décision{decisions.total > 1 ? "s" : ""}
      {decisions.hypotheses > 0 &&
        ` · dont ${decisions.hypotheses} sans réponse`}
    </p>
  );
}

/**
 * Une ligne : quand, quoi, pourquoi, par qui, sur quelle tâche.
 *
 * La gouttière de gauche porte l'heure **et** l'icône de famille : c'est ce que
 * Grafana (History) et la timeline d'une PR GitHub font toutes deux, et c'est ce
 * qui permet de balayer la colonne sans lire les lignes. L'heure ne s'y coupe
 * jamais (`whitespace-nowrap`), et la gouttière s'élargit plutôt que de replier
 * son contenu — un bord gauche haché rendrait la colonne illisible précisément
 * là où on la balaie.
 *
 * Le titre de la tâche est **tronqué à l'œil** et entier partout ailleurs : le
 * texte du bouton reste complet pour un lecteur d'écran (une troncature CSS ne
 * cache rien), et le `title` le rend à la souris. Pas d'`Infobulle` ici, et c'est
 * l'exception que `LigneActivite` a déjà écrite : son enveloppe focusable
 * créerait un contrôle **dans** un contrôle.
 */
function LigneDecision({
  prise,
  ouvrir,
}: {
  prise: Decision;
  ouvrir: (bouton: HTMLButtonElement) => void;
}) {
  const maintenant = useHorloge();
  const Glyphe = prise.hypothese ? IconeHypothese : IconeDecision;
  return (
    <li className="flex items-start gap-3 px-3 py-2.5">
      {/* `chiffre` : l'âge se réévalue sous les yeux, et la classe empêche le
          changement de largeur de faire sauter la ligne autour de lui (#245). */}
      <time
        dateTime={prise.horodatage}
        title={formatDateHeure(prise.horodatage)}
        className="chiffre min-w-24 shrink-0 pt-0.5 text-right font-mono text-annexe whitespace-nowrap text-texte-secondaire"
      >
        {formatHeureRelative(prise.horodatage, maintenant)}
        <span className="sr-only"> ({formatDateHeure(prise.horodatage)})</span>
      </time>

      {/* Décorative : la pastille « Hypothèse » et la phrase portent le sens. */}
      <Glyphe
        aria-hidden="true"
        className={`mt-0.5 size-4 shrink-0 ${
          prise.hypothese ? "text-attention-texte" : "text-texte-secondaire"
        }`}
      />

      <div className="min-w-0 flex-1">
        <p className="text-corps">
          {prise.hypothese && (
            <BadgeEtat ton="attention" contour className="mr-1.5 align-middle">
              Hypothèse
            </BadgeEtat>
          )}
          {prise.decision}
        </p>
        {/* Le motif, tel que l'agent l'a écrit. Une décision sans lui n'est pas
            consignée du tout (#1024) : cette ligne n'est donc jamais vide. */}
        <p className="mt-0.5 text-annexe text-texte-secondaire">{prise.raison}</p>
      </div>

      <p className="w-48 shrink-0 text-right text-annexe text-texte-secondaire">
        <span className="block truncate font-medium text-texte">{prise.agent}</span>
        <button
          type="button"
          title={prise.tache || prise.tache_id}
          onClick={(evenement) => ouvrir(evenement.currentTarget)}
          className={`block w-full truncate text-right ${CIBLE_MINIMALE} text-info-texte hover:underline`}
        >
          {prise.tache || prise.tache_id}
        </button>
      </p>
    </li>
  );
}
