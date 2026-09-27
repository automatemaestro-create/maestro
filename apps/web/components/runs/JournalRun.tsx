"use client";

/**
 * Le journal **d'un run** (#478, lot 6 de #472) : ce que ce run a dit, dans
 * l'ordre où il l'a dit, et qui survit à un rechargement.
 *
 * La vue d'un run devait porter « son Kanban, sa progression et **son journal** »
 * (docs/29 §3) ; les deux premiers sont arrivés avec #475, le troisième
 * manquait — faute d'une source. Il n'y en avait pas : le fil du shell ne
 * contient que ce qui est passé par le WebSocket depuis l'ouverture de la page,
 * donc ouvrir la vue d'un run terminé la veille ne montrait rien du tout.
 * `GET /api/journal?run_id=…` (contrat #183, servi par #478) est cette source.
 *
 * Trois décisions, les mêmes que pour le Kanban de #475 et pour les mêmes
 * raisons :
 *
 * - **L'appartenance au run vient de l'API** — le filtre `run_id` du contrat —,
 *   et non d'un tri sur le fil du shell : ce fil est borné aux derniers
 *   événements reçus, donc un run un peu bavard s'y serait tronqué lui-même.
 * - **Aucune seconde WebSocket** : la lecture suit le **pouls** du shell
 *   (`revision`), et le direct que l'historique n'a pas encore rattrapé se
 *   superpose (`fusionnerJournal`) — filtré sur ce run, lui.
 * - **La ligne n'est pas réécrite** : `FilActivite` rend ici exactement ce qu'il
 *   rend au tableau de bord et sur la page Journal. Seuls son titre et son vide
 *   sont nommés — un fil de run vide ne s'explique pas comme un projet sans
 *   activité.
 *
 * **Il s'ouvre aussi sur des entrées nommées** (#1285) : une pièce du bilan du run
 * mène ici, **sur les entrées qu'elle cite** — lues par l'API (`?ids=`), où
 * qu'elles soient dans le journal, et non cherchées dans la page de 200 qu'on lit
 * d'ordinaire. Le fil ne montre alors qu'elles, dit d'où l'on vient, et rend le
 * journal entier d'un geste. Le direct n'y est pas superposé : il ne peut rien
 * apporter à une liste d'entrées déjà consignées.
 */

import { useMemo } from "react";

import { useEcranEnPanne } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { Bouton } from "@/components/Primitives";
import type { PorteeProjet } from "@/lib/api";
import type { Citation } from "@/lib/bilan";
import { fusionnerJournal } from "@/lib/journal";
import type { Evenement } from "@/lib/types";
import { useJournal } from "@/lib/useJournal";

export function JournalRun({
  portee,
  runId,
  /** Le fil temps réel du shell — filtré sur ce run avant d'être superposé. */
  direct,
  revision,
  citation = null,
  toutLeJournal,
}: {
  portee: PorteeProjet;
  runId: string;
  direct: Evenement[];
  revision: number;
  /** Les entrées qu'une pièce du bilan cite, et elles seules (#1285) — null : tout. */
  citation?: Citation | null;
  /** Revenir au journal entier depuis une citation. */
  toutLeJournal?: () => void;
}) {
  const cite = citation !== null && citation.entrees.length > 0 ? citation : null;
  const historique = useJournal(
    portee,
    { runId, ids: cite?.entrees ?? null },
    revision,
  );
  // Le magasin perdu en route compte aussi (#1217) : cette lecture-ci n'a
  // peut-être pas encore échoué, mais son « aucun événement » ne dirait rien.
  const enPanne = useEcranEnPanne(historique.erreur);

  const evenements = useMemo(
    () =>
      cite !== null
        ? historique.evenements
        : fusionnerJournal(
            historique.evenements,
            direct.filter((evenement) => evenement.run_id === runId),
          ),
    [cite, historique.evenements, direct, runId],
  );

  return (
    <FilActivite
      evenements={evenements}
      titre="Journal du run"
      // Sous le titre, comme dans la frise (relevé par le regard neuf : au-dessus
      // ici, en dessous là-bas, pour le même geste).
      bandeau={
        cite !== null && (
          <p className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-annexe text-texte-secondaire">
            <span>
              {cite.libelle} du bilan · {cite.entrees.length} entrée
              {cite.entrees.length > 1 ? "s citées" : " citée"}
            </span>
            {toutLeJournal && (
              <Bouton variante="contour" ton="neutre" taille="petite" onClick={toutLeJournal}>
                Tout le journal
              </Bouton>
            )}
          </p>
        )
      }
      messageVide={
        historique.chargement
          ? "Lecture du journal de ce run…"
          : enPanne
            ? "Journal indisponible — la lecture a échoué."
            : cite !== null
              ? "Aucune de ces entrées n'est au journal de ce run."
              : "Aucun événement consigné pour ce run."
      }
    />
  );
}
