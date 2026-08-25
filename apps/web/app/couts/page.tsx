"use client";

/**
 * La page Coûts & analytics de la Control Tower (ticket #87) : piloter la
 * dépense au quotidien (critère de sortie V1 « coûts maîtrisés »). Branchée
 * sur `GET /api/analytics/couts` : coût total de la période, évolution dans
 * le temps, répartition par agent, détail par tâche et par exécution — le
 * tout sur une période sélectionnable et rafraîchi en temps réel par le
 * WebSocket. Pendant un rechargement, la vue précédente reste affichée,
 * estompée (pas de squelette, pas de saut de mise en page).
 *
 * Depuis #191, la page héberge aussi le **grand livre par exécution** (#58)
 * que le tableau de bord empilait : le détail ligne à ligne, sous les agrégats
 * qui le résument. Il vient du contexte partagé du shell et non de
 * `useAnalyticsCouts` — il n'est donc pas borné par le filtre de période, d'où
 * sa place à part, hors de la zone estompée pendant un rafraîchissement.
 *
 * Les deux sources sont cadrées sur le **projet actif** (#281) : la vue agrégée
 * par sa portée (`?projet=`, #277), les grands livres parce qu'ils sont dérivés
 * des tâches du projet. Elles ne peuvent donc pas se contredire — un total de
 * période plus petit que la somme des grands livres se lirait comme un bug, là
 * où ce ne serait qu'un mélange de périmètres.
 *
 * ⚠ **Les trois places** (#539, docs/30 §4). Cet écran comptait **cinq blocs de
 * plein format** pour un plafond de trois — évolution, répartition, table par
 * tâche, table par exécution, grand livre —, et c'est ici que la règle a été
 * appliquée plutôt qu'énoncée. Aucune information n'est partie ; chacune a
 * changé de place :
 *
 * 1. **le bandeau de tête** garde ses quatre chiffres, et ce sont désormais des
 *    `TuileChiffre` (#245) et non plus une carte recopiée sur place — c'est ce
 *    marqueur que la sonde de sobriété compte ;
 * 2. **le corps** tient en trois : l'évolution, le **détail de la période** —
 *    un seul bloc à **second niveau**, où les deux tables se prennent par une
 *    bascule (`BasculeDeVues`, la même que les lectures d'un run) — et le grand
 *    livre, qui reste à part parce que la période ne le borne pas ;
 * 3. **la colonne de propriétés** reçoit la répartition par agent : une
 *    ventilation de la période, pas un sujet à elle. Elle défile à côté du
 *    corps et ne lui dispute plus la largeur.
 *
 * Ce qui aurait été facile et faux : retirer une des deux tables. La règle ne
 * dit pas « moins », elle dit **où** — et `tests/sobriete.test.tsx` la garde.
 */

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { BasculeDeVues, type VueBascule } from "@/components/BasculeDeVues";
import {
  IconeAgents,
  IconeGrandLivre,
  IconeJetons,
  IconeMessage,
  IconeMonnaie,
  IconeRuns,
  IconeTache,
} from "@/components/Icones";
import { GraphiqueEvolutionCout } from "@/components/GraphiqueEvolutionCout";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { PanneauCouts } from "@/components/PanneauCouts";
import { Carte, EnTeteSection, TuileChiffre } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { RepartitionAgents } from "@/components/RepartitionAgents";
import { mesureDeLaDepense } from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  formatCout,
  formatDateHeure,
  formatDuree,
  formatTokens,
  libelleStatut,
} from "@/lib/format";
import type {
  AnalyticsCouts,
  CoutExecutionResume,
  CoutTacheAgregee,
  PasSerie,
} from "@/lib/types";
import { PERIODES, useAnalyticsCouts, type Periode } from "@/lib/useAnalyticsCouts";
import { useState } from "react";

/** Les deux lectures du détail de la période — le second niveau du bloc. */
const VUE_TACHES = "taches";
const VUE_EXECUTIONS = "executions";
type VueDetail = typeof VUE_TACHES | typeof VUE_EXECUTIONS;

const VUES_DETAIL: VueBascule<VueDetail>[] = [
  {
    cle: VUE_TACHES,
    libelle: "Par tâche",
    question:
      "Ce que chaque tâche a coûté sur la période, toutes ses exécutions confondues",
    icone: IconeTache,
  },
  {
    cle: VUE_EXECUTIONS,
    libelle: "Par exécution",
    question: "Ce que chaque run de la période a coûté, de son début à sa fin",
    icone: IconeRuns,
  },
];

export default function PageCouts() {
  const [periode, setPeriode] = useState<Periode>(
    PERIODES.find((p) => p.id === "tout") ?? PERIODES[0],
  );
  const [vueDetail, setVueDetail] = useState<VueDetail>(VUE_TACHES);
  // Les grands livres sont déjà chargés par le contexte partagé (#117) : les
  // relire ici ne coûte ni requête ni connexion supplémentaire. C'est aussi de
  // là que vient la portée projet (#281), pour que les deux sources de cette
  // page portent le même périmètre sans qu'on ait à le rappeler deux fois.
  const {
    couts,
    coutTotal,
    portee,
    projet,
    // Le chargement **du shell**, à ne pas confondre avec celui des agrégats
    // ci-dessous : c'est lui qui porte la dépense cumulée, donc lui qui dit
    // quand la région live peut prendre son premier relevé.
    chargement: chargementProjet,
  } = useEtatGlobal();
  // Le statut du flux temps réel est porté par la barre supérieure du shell
  // (#117) : la page n'a plus qu'à consommer les agrégats.
  const { vue, chargement, rafraichissement, erreur } = useAnalyticsCouts(
    periode,
    portee,
  );

  // L'estompage du rafraîchissement s'applique à **tout ce qui vient des
  // agrégats** — le corps comme la colonne de propriétés —, et à rien d'autre :
  // le grand livre, qui ne dépend pas de la période, garde sa pleine opacité.
  // Une seule chaîne, posée aux deux endroits, plutôt que deux qui pourraient
  // se désaccorder.
  const estompe =
    "transition-opacity motion-reduce:transition-none " +
    (rafraichissement ? "opacity-60" : "opacity-100");

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {/* La région live de l'écran (#538). Elle ne parle **que** de la dépense,
          et pas à chaque rafraîchissement : cette page se relit à tout événement
          du bus (`useAnalyticsCouts` ne filtre rien), donc annoncer le
          rafraîchissement serait annoncer le flux. Ce qui vaut d'être dit est le
          franchissement d'un dollar — un seuil, pas un rechargement.
          Le total vient du contexte du shell et non de `vue.total` : celui-ci
          suit la période choisie, et changer de période n'est pas une dépense. */}
      {!chargementProjet && (
        <RegionLive
          libelle="Dépense du projet"
          mesures={[mesureDeLaDepense(coutTotal)]}
        />
      )}
      {/* La rangée de filtres : une seule, au-dessus de tout ce qu'elle borne —
          chaque vue en dessous (compteurs, graphiques, tables) suit la même
          fenêtre, les chiffres concordent toujours. Ce n'est pas un bloc au
          sens des trois places (#539) : c'est le réglage de tout l'écran, et il
          reste donc en tête plutôt que dans la colonne de propriétés. */}
      <nav aria-label="Période" className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-neutral-500 dark:text-neutral-400">
          Période :
        </span>
        {PERIODES.map((p) => (
          <button
            key={p.id}
            type="button"
            onClick={() => setPeriode(p)}
            aria-pressed={p.id === periode.id}
            className={
              "rounded-full border px-3 py-1 text-sm " +
              (p.id === periode.id
                ? "border-neutral-400 bg-neutral-100 font-medium dark:border-neutral-600 dark:bg-neutral-800"
                : "border-neutral-200 bg-white text-neutral-600 hover:bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400 dark:hover:bg-neutral-800")
            }
          >
            {p.libelle}
          </button>
        ))}
      </nav>
      {chargement || vue === null ? (
        <p className="text-sm text-neutral-500">Chargement des agrégats…</p>
      ) : (
        <>
          <Compteurs vue={vue} className={estompe} />
          {vue.executions.length === 0 && vue.taches.length === 0 && (
            // Les compteurs restent : « 0 $ sur la période » est une réponse.
            // Ce qu'ils ne disent pas, c'est de quel périmètre ce zéro est le
            // zéro — et sans les tables, qui s'effacent quand elles sont vides,
            // l'écran se lirait comme une page à moitié chargée (#281).
            <p className="text-sm text-neutral-500 dark:text-neutral-400">
              Rien encore sur {projet.nom} : aucune exécution de ce projet
              n&apos;a de dépense à comptabiliser sur cette période.
            </p>
          )}
          {/* Le corps à gauche, la colonne de propriétés à droite (#539).
              `items-start` : la colonne se cale en haut plutôt que de s'étirer
              sur la hauteur du corps, ce qui est aussi ce qui rend son
              `sticky` utile. En dessous de `@4xl` la colonne repasse sous le
              corps — une colonne de propriétés étroite n'a pas de sens sur un
              écran étroit, et elle y redevient un bloc de fin de page. */}
          <div className="grid gap-6 @4xl:grid-cols-3 @4xl:items-start">
            <div
              className={"flex min-w-0 flex-col gap-6 @4xl:col-span-2 " + estompe}
            >
              <Carte
                balise="section"
                densite="aeree"
                aria-label="Évolution du coût"
              >
                <EnTeteSection titre="Évolution du coût" icone={IconeMonnaie} />
                <div className="mt-3">
                  <GraphiqueEvolutionCout
                    serie={vue.serie}
                    pas={vue.pas as PasSerie}
                  />
                </div>
              </Carte>
              <DetailPeriode
                vue={vue}
                lecture={vueDetail}
                choisir={setVueDetail}
              />
            </div>
            {/* Collante, mais **bornée**, et les deux vont ensemble : une
                colonne collante plus haute que la fenêtre voit son bas rester
                sous le pli, définitivement — aucun défilement ne le ramène,
                puisque c'est le défilement qui la fige. C'est exactement la
                classe de bug de #306, et elle arrive ici dès qu'un projet
                compte assez d'agents. Le plafond lui rend son propre
                ascenseur ; il ne s'applique qu'au-delà de `@4xl`, où la colonne
                existe — en dessous elle repasse sous le corps et défile avec la
                page, où la borner découperait une liste sans raison. */}
            <aside
              aria-label="Propriétés de la période"
              className={
                "flex min-w-0 flex-col gap-6 @4xl:sticky @4xl:top-20 " +
                "@4xl:max-h-[calc(100dvh-6rem)] @4xl:overflow-y-auto " +
                estompe
              }
            >
              <Carte densite="aeree">
                <EnTeteSection
                  titre="Répartition par agent"
                  niveau={2}
                  icone={IconeAgents}
                />
                <div className="mt-3">
                  <RepartitionAgents agents={vue.agents} />
                </div>
              </Carte>
            </aside>
          </div>
        </>
      )}
      <PanneauCouts couts={couts} />
    </>
  );
}

/** La rangée de compteurs : les totaux de la fenêtre, d'un coup d'œil. */
function Compteurs({
  vue,
  className = "",
}: {
  vue: AnalyticsCouts;
  className?: string;
}) {
  return (
    // Quatre, et le plafond de la première place est **quatre** (docs/30 §4) :
    // un cinquième chiffre ne s'ajoute pas ici, il en remplace un.
    <section
      data-guide="couts"
      aria-label="Totaux de la période"
      // Les colonnes du bandeau du tableau de bord, au pas près : deux bandeaux
      // de tête qui ne se replient pas au même endroit se liraient comme deux
      // écrans différents.
      className={
        "grid grid-cols-1 gap-3 @sm:grid-cols-2 @3xl:grid-cols-4 " + className
      }
    >
      <TuileChiffre
        libelle="Coût total"
        valeur={formatCout(vue.total.cout_usd)}
        icone={IconeMonnaie}
      />
      <TuileChiffre
        libelle="Tokens"
        valeur={formatTokens(vue.total.tokens_total)}
        detail={`${formatTokens(vue.total.tokens_entree)} entrée · ${formatTokens(vue.total.tokens_sortie)} sortie`}
        icone={IconeJetons}
      />
      <TuileChiffre
        libelle="Appels modèle"
        valeur={formatTokens(vue.total.appels)}
        icone={IconeMessage}
      />
      <TuileChiffre
        libelle="Exécutions"
        valeur={formatTokens(vue.executions.length)}
        detail={`${formatTokens(vue.taches.length)} tâche(s) comptabilisée(s)`}
        icone={IconeRuns}
      />
    </section>
  );
}

/**
 * Le détail de la période — **un** bloc, deux lectures (#539).
 *
 * C'était deux blocs de plein format empilés, dont le second n'était presque
 * jamais lu sans le premier. Les mettre derrière une bascule ne cache rien : la
 * table absente est à un clic, et le bloc dit lequel des deux on regarde. La
 * bascule est celle de la vue d'un run (`BasculeDeVues`) — un geste appris une
 * fois se reconnaît partout.
 *
 * Il disparaît quand les deux tables sont vides : la page dit alors « rien
 * encore sur ce projet », et un bloc à deux onglets vides serait la page à
 * moitié chargée que #281 a précisément voulu éviter.
 */
function DetailPeriode({
  vue,
  lecture,
  choisir,
}: {
  vue: AnalyticsCouts;
  lecture: VueDetail;
  choisir: (lecture: VueDetail) => void;
}) {
  if (vue.taches.length === 0 && vue.executions.length === 0) return null;
  return (
    <Carte
      balise="section"
      densite="aeree"
      aria-label="Détail de la période"
      className="flex flex-col gap-3"
    >
      <EnTeteSection titre="Détail de la période" icone={IconeGrandLivre} />
      <BasculeDeVues
        etiquette="Lectures du détail"
        vues={VUES_DETAIL}
        courante={lecture}
        choisir={choisir}
      />
      {lecture === VUE_TACHES ? (
        <TableTaches taches={vue.taches} />
      ) : (
        <TableExecutions executions={vue.executions} />
      )}
    </Carte>
  );
}

/** Le détail par tâche : cumul toutes exécutions confondues, tri par coût (API). */
function TableTaches({ taches }: { taches: CoutTacheAgregee[] }) {
  if (taches.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Aucune tâche comptabilisée sur cette période.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            <th className="py-1 pr-3 font-medium">Tâche</th>
            <th className="py-1 pr-3 font-medium">Agent</th>
            <th className="py-1 pr-3 font-medium">Statut</th>
            <th className="py-1 pr-3 text-right font-medium">Exécutions</th>
            <th className="py-1 pr-3 text-right font-medium">Tokens</th>
            <th className="py-1 pr-3 text-right font-medium">Coût</th>
            <th className="py-1 text-right font-medium">Durée</th>
          </tr>
        </thead>
        <tbody>
          {taches.map((tache) => (
            <tr
              key={tache.tache_id}
              className="border-b border-neutral-100 dark:border-neutral-800/60"
            >
              <td className="max-w-64 py-1 pr-3">
                <span className="block truncate" title={tache.tache_id}>
                  {tache.nom || tache.tache_id}
                </span>
                {/* Le ticket externe (#192) sous le nom : la colonne garde sa
                    largeur, et la ligne d'une tâche sans référence ne bouge pas. */}
                <LienTicketExterne
                  reference={tache.ticket}
                  tache={tache.nom || tache.tache_id}
                />
              </td>
              <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                {tache.agent
                  ? `${tache.agent}${tache.role ? ` · ${tache.role}` : ""}`
                  : "—"}
              </td>
              <td className="py-1 pr-3">{libelleStatut(tache.statut) || "—"}</td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {tache.executions}
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {formatTokens(tache.usage.tokens_total)}
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {formatCout(tache.usage.cout_usd)}
              </td>
              <td className="py-1 text-right tabular-nums">
                {formatDuree(tache.usage.duree_ms)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Le détail par exécution : bornes, tâches et usage cumulé de chaque run. */
function TableExecutions({ executions }: { executions: CoutExecutionResume[] }) {
  if (executions.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Aucune exécution comptabilisée sur cette période.
      </p>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
            <th className="py-1 pr-3 font-medium">Exécution</th>
            <th className="py-1 pr-3 text-right font-medium">Tâches</th>
            <th className="py-1 pr-3 font-medium">Début</th>
            <th className="py-1 pr-3 font-medium">Fin</th>
            <th className="py-1 pr-3 text-right font-medium">Tokens</th>
            <th className="py-1 text-right font-medium">Coût</th>
          </tr>
        </thead>
        <tbody>
          {executions.map((execution) => (
            <tr
              key={execution.run_id}
              className="border-b border-neutral-100 dark:border-neutral-800/60"
            >
              <td className="py-1 pr-3 font-mono">{execution.run_id}</td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {execution.nb_taches}
              </td>
              <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                {formatDateHeure(execution.debut) || "—"}
              </td>
              <td className="py-1 pr-3 text-neutral-500 dark:text-neutral-400">
                {formatDateHeure(execution.fin) || "—"}
              </td>
              <td className="py-1 pr-3 text-right tabular-nums">
                {formatTokens(execution.usage.tokens_total)}
              </td>
              <td className="py-1 text-right tabular-nums">
                {formatCout(execution.usage.cout_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
