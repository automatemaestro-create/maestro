"use client";

/**
 * La vue Kanban des tâches par statut (docs/05 §2.2) et la **réassignation
 * manuelle** (EF-11/EF-20) : chaque carte porte un sélecteur d'agent qui
 * appelle `POST /api/taches/{id}/reassigner`. Les colonnes suivent la machine
 * à états du moteur (docs/03 §3) ; un statut inconnu du front tombe dans une
 * colonne « Autres » plutôt que de disparaître.
 *
 * Depuis #248, c'est **la section qui prend la place** : elle absorbe la hauteur
 * que la page lui laisse (le `<main>` du shell est une colonne flex, #117) et
 * chaque colonne défile chez elle. Sa largeur, elle, se règle par une **largeur
 * minimale de colonne** plutôt que par un nombre de colonnes : les colonnes
 * s'élargissent jusqu'à 2 560 px et se replient en lignes en dessous de la
 * largeur où elles tiennent toutes de front.
 */

import { useState } from "react";

import {
  IconeAgent,
  IconeChrono,
  IconeJetons,
  IconePuce,
  IconeStatutAssignee,
  IconeStatutBloquee,
  IconeStatutEchec,
  IconeStatutEnCours,
  IconeStatutTerminee,
  IconeTache,
} from "@/components/Icones";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import {
  BadgeEtat,
  Carte,
  EnTeteSection,
  type Icone,
  type TonBadge,
} from "@/components/Primitives";
import {
  formatCout,
  formatDuree,
  formatHeure,
  formatTokens,
  libelleStatut,
} from "@/lib/format";
import type { EtatAgent, Tache } from "@/lib/types";

type Reassigner = (tacheId: string, agent: string) => Promise<void>;

type Props = {
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
};

/**
 * Les colonnes du Kanban, dans l'ordre du flux de travail. Chaque statut porte
 * son **icône** en plus de son ton (#245) : la pastille de couleur seule ne
 * distinguait pas « bloquée » de « échec » pour qui ne sépare pas le violet du
 * rouge, et disparaissait à l'impression.
 */
const COLONNES: {
  statut: string;
  titre: string;
  ton: TonBadge;
  icone: Icone;
}[] = [
  {
    statut: "assignee",
    titre: "Assignées",
    ton: "info",
    icone: IconeStatutAssignee,
  },
  {
    statut: "en_cours",
    titre: "En cours",
    ton: "attention",
    icone: IconeStatutEnCours,
  },
  {
    statut: "bloquee",
    titre: "Bloquées",
    ton: "accent",
    icone: IconeStatutBloquee,
  },
  {
    statut: "terminee",
    titre: "Terminées",
    ton: "positif",
    icone: IconeStatutTerminee,
  },
  { statut: "echec", titre: "Échecs", ton: "alerte", icone: IconeStatutEchec },
];

export function Kanban({ taches, agents, reassigner }: Props) {
  const connus = new Set(COLONNES.map((c) => c.statut));
  const autres = taches.filter((t) => !connus.has(t.statut));
  const colonnes = [
    ...COLONNES.map((colonne) => ({
      ...colonne,
      taches: taches.filter((t) => t.statut === colonne.statut),
    })),
    ...(autres.length > 0
      ? [
          {
            statut: "",
            titre: "Autres",
            ton: "neutre" as TonBadge,
            icone: IconePuce,
            taches: autres,
          },
        ]
      : []),
  ];

  // #248 — les tâches sont l'objet du tableau de bord : elles en prennent la
  // place. La borne `max-h-96` de #191 protégeait la densité d'un écran qui
  // portait encore cinq panneaux de plein format ; ceux-ci sont partis avec le
  // même lot, et la borne est restée — sur un grand écran les tâches tenaient
  // dans le tiers supérieur pendant que le reste était vide.
  //
  // L'étirement est une **chaîne** : chaque maillon doit pouvoir rétrécir sous
  // son contenu (`min-h-0`, faute de quoi le `min-height:auto` d'un élément
  // flex l'en empêche) et prendre le reste (`flex-1`). Elle se pose donc en
  // entier ou pas du tout — un maillon manquant et le débordement remonte à la
  // zone de contenu au lieu de rester dans la colonne. Elle commence plus haut
  // que ce fichier : c'est le `<body>` (layout) qui pose la hauteur définie,
  // sans laquelle il n'y aurait rien à prendre.
  //
  // Pas de tâche, pas d'étirement : cinq colonnes « Aucune tâche. » n'ont pas
  // besoin de tout l'écran, et sans hauteur à partager la chaîne n'aurait rien
  // à distribuer.
  const etire = taches.length > 0;
  const maillon = etire ? "min-h-0 flex-1" : "";

  return (
    <section
      data-guide="kanban"
      aria-label="Tâches (Kanban)"
      // Plancher à `min-h-96` : sur une fenêtre courte, le tableau garde les
      // 24 rem qu'il avait avant ce lot et c'est la zone de contenu qui
      // défile, plutôt qu'un tableau écrasé à quelques pixels.
      className={`flex flex-col ${etire ? "min-h-96 flex-1" : ""}`}
    >
      <EnTeteSection titre="Tâches" icone={IconeTache} className="mb-2" />
      {/* Une largeur MINIMALE par colonne, pas un nombre de colonnes (#248) :
          au-delà, les colonnes s'élargissent jusqu'aux 2 560 px d'un grand
          écran ; en dessous, elles se replient en lignes au lieu d'être
          comprimées — l'ancien `md:grid-flow-col` les tassait toutes de front
          dès 768 px, où une carte n'avait plus que ~120 px.
          `auto-fit` et non `auto-fill` : les pistes que personne n'occupe se
          referment, sinon les colonnes réelles rétréciraient au profit de
          pistes vides. Aucune colonne rendue ne disparaît pour autant — elles
          portent toutes un élément, même sans tâche.
          `min(…,100%)` : sous 11,5 rem de large (mobile étroit), la piste suit
          le conteneur au lieu de le faire déborder.
          `minmax(11rem,1fr)` en hauteur de ligne : quand ça se replie, les
          lignes se partagent la hauteur — mais jamais en dessous de 11 rem,
          sans quoi elles se l'arrachent. Mesuré sur un mobile de 390 px, où
          les cinq colonnes s'empilent : 20 px de zone utile par colonne avec
          un simple `1fr`, 132 px avec ce plancher (et c'est la zone de contenu
          qui défile, ce qui est le bon compromis). */}
      <div
        className={`grid auto-rows-[minmax(11rem,1fr)] grid-cols-[repeat(auto-fit,minmax(min(11.5rem,100%),1fr))] gap-3 ${maillon}`}
      >
        {colonnes.map((colonne) => (
          <Carte
            balise="div"
            ton="creuse"
            densite="compacte"
            key={colonne.titre}
            /* Le fond, le bord, l'arrondi et la densité viennent de `Carte`
               (#245) ; ne reste ici que le maillon de la chaîne d'étirement
               (#248), qui est propre à cet écran. */
            className="flex min-h-0 min-w-0 flex-col"
          >
            <h3 className="mb-2 flex items-center gap-2 px-1 text-corps font-medium">
              <colonne.icone className="size-4 shrink-0 text-neutral-500 dark:text-neutral-400" />
              {colonne.titre}
              <BadgeEtat ton={colonne.ton} className="chiffre ml-auto">
                {colonne.taches.length}
              </BadgeEtat>
            </h3>
            {/* Chaque colonne défile chez elle (#191) plutôt que d'étirer la
                page — mais dans la hauteur que la fenêtre lui donne (#248) et
                non plus dans les 24 rem d'une borne fixe. */}
            <div className={`space-y-2 overflow-y-auto ${maillon}`}>
              {colonne.taches.map((tache) => (
                <CarteTache
                  key={tache.id}
                  tache={tache}
                  agents={agents}
                  reassigner={reassigner}
                />
              ))}
              {colonne.taches.length === 0 && (
                <p className="px-1 pb-1 text-annexe text-neutral-400 dark:text-neutral-600">
                  Aucune tâche.
                </p>
              )}
            </div>
          </Carte>
        ))}
      </div>
      {taches.length === 0 && (
        <p className="mt-2 text-corps text-neutral-500">
          Aucune tâche pour l&apos;instant — elles apparaîtront dès qu&apos;un run
          publiera ses événements.
        </p>
      )}
    </section>
  );
}

function CarteTache({
  tache,
  agents,
  reassigner,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const surReassignation = async (agent: string) => {
    if (!agent) return;
    setEnCours(true);
    setErreur(null);
    try {
      await reassigner(tache.id, agent);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  // Un agent désactivé ne reçoit plus de tâches (#86) : il n'est pas proposé
  // à la réassignation — l'API la refuserait de toute façon (422).
  const candidats = agents.filter((a) => a.nom !== tache.agent && a.actif);

  return (
    <Carte densite="compacte" className="text-corps">
      <p className="font-medium" title={tache.id}>
        {tache.titre || tache.id}
      </p>
      {/* Le ticket qui a motivé la tâche (#192) — absent : la carte est
          exactement celle d'avant, la marge partant avec le composant. */}
      <LienTicketExterne
        reference={tache.ticket}
        tache={tache.titre || tache.id}
        className="mt-1"
      />
      {/* « Agent » en toutes lettres : l'émoji 🤖 portait seul l'information,
          et une tâche non assignée ne disait pas de quoi elle manquait. */}
      <p className="mt-1 flex items-center gap-1 text-annexe text-neutral-500 dark:text-neutral-400">
        <IconeAgent className="size-3.5 shrink-0" />
        Agent {tache.agent || "non assigné"}
        {tache.role ? ` · ${tache.role}` : ""}
      </p>
      <p className="chiffre mt-0.5 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400">
        <span>{libelleStatut(tache.statut)}</span>
        <span>
          {formatCout(tache.cout_usd)}
          {tache.horodatage ? ` · ${formatHeure(tache.horodatage)}` : ""}
        </span>
      </p>
      {tache.usage && (
        <p
          className="chiffre mt-0.5 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400"
          title={`${formatTokens(tache.usage.tokens_entree)} tokens en entrée / ${formatTokens(tache.usage.tokens_sortie)} en sortie`}
        >
          <span className="inline-flex items-center gap-1">
            <IconeJetons className="size-3.5 shrink-0" />
            {formatTokens(tache.usage.tokens_total)} tokens
          </span>
          <span className="inline-flex items-center gap-1">
            <IconeChrono className="size-3.5 shrink-0" />
            {formatDuree(tache.usage.duree_ms)}
          </span>
        </p>
      )}
      <select
        aria-label={`Réassigner la tâche ${tache.titre || tache.id}`}
        className="mt-2 w-full rounded border border-neutral-300 bg-transparent px-1.5 py-1 text-annexe text-neutral-600 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:[&>option]:bg-neutral-900"
        value=""
        disabled={enCours || candidats.length === 0}
        onChange={(e) => void surReassignation(e.target.value)}
      >
        <option value="" disabled>
          {enCours ? "Réassignation…" : "Réassigner à…"}
        </option>
        {candidats.map((agent) => (
          <option key={agent.nom} value={agent.nom}>
            {agent.nom}
            {agent.role ? ` — ${agent.role}` : ""}
          </option>
        ))}
      </select>
      {erreur && (
        <p className="mt-1 text-annexe text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
    </Carte>
  );
}
