"use client";

/**
 * L'écran Validations (#117, lot 1 de #116 ; refondu par #272, lot 5 de #244) :
 * les demandes de validation humaine (#48) en **plein format** — celles qui
 * attendent d'abord, la plus ancienne en tête, puis la trace de celles qui ont
 * été tranchées.
 *
 * Il ne porte **aucune présentation à lui** : la file est rendue par
 * `FileValidations`, jumelle de l'aperçu du tableau de bord (`PanneauValidations`,
 * critère 3 de #272). Ce qui change entre les deux surfaces est la place —
 * ici toute la file, là-bas la plus urgente et un renvoi —, jamais ce qu'on lit
 * pour trancher. Deux rendus divergents de la même demande, c'était l'état
 * d'avant : on décidait sur moins d'information selon l'écran d'où l'on venait.
 *
 * Les demandes viennent du contexte du shell, cadrées sur le projet actif
 * (`?projet=`, #277/#281) : ce qui vaut pour la file en attente vaut pour
 * l'historique, sans quoi on trancherait ici les arbitrages d'un autre projet.
 * L'écran vide le dit **en nommant le projet** — « aucune validation en
 * attente » tout court se lit comme un état de l'orchestration entière.
 *
 * ⚠ **Il a quitté `app/validations/page.tsx` avec #1228**, où il était écrit en
 * entier. Deux raisons, et la seconde est celle du ticket : la page est une
 * **coquille** comme les autres du dépôt (le contenu vit dans `components/`,
 * parce que c'est lui qui se teste) ; et elle a désormais quelque chose à lire
 * dans l'URL — `?retour=`, le chemin d'où l'on vient —, ce qu'un composant
 * serveur fait sans qu'on ait à ceinturer l'écran d'un `Suspense` pour le rendu
 * statique. Même partage que `/agents` et son `?onglet=` (#190).
 */

import Link from "next/link";

import {
  BanniereErreurApi,
  ContenuIndisponible,
  useEcranEnPanne,
} from "@/components/BanniereErreurApi";
import {
  IconeFlecheGauche,
  IconeHistorique,
  IconeValidations,
} from "@/components/Icones";
import { FileValidations } from "@/components/PanneauValidations";
import {
  Carte,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesValidationsTranchees } from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatDateHeure } from "@/lib/format";
import { libelleDeRetour } from "@/lib/navigation";
import {
  VALIDATION_APPROUVEE,
  VALIDATION_EN_ATTENTE,
  type Validation,
} from "@/lib/types";

export function EcranValidations({ retour }: { retour?: string }) {
  const { projet, validations, chargement, erreur, decider } = useEtatGlobal();

  const enAttente = validations.filter(
    (v) => v.statut === VALIDATION_EN_ATTENTE,
  );
  const tranchees = validations.filter(
    (v) => v.statut !== VALIDATION_EN_ATTENTE,
  );
  const enPanne = useEcranEnPanne(erreur);

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      <LienDeRetour retour={retour} />
      {chargement ? (
        <p className="text-corps text-texte-secondaire">
          Chargement des demandes…
        </p>
      ) : enPanne ? (
        // En panne (#1217) : « rien encore » ou « aucune en attente » se
        // liraient comme la réponse de la lecture qui vient d'échouer, et
        // l'historique comme la trace à jour. Les demandes déjà lues restent,
        // elles : on y tranche, et un refus motivé en cours de frappe ne doit
        // pas partir avec une panne de quelques secondes.
        <>
          {enAttente.length > 0 && (
            <FileValidations validations={validations} decider={decider} />
          )}
          <ContenuIndisponible
            quoi={
              enAttente.length > 0
                ? `les arbitrages déjà tranchés sur ${projet.nom}`
                : `les demandes d'arbitrage de ${projet.nom}`
            }
          />
        </>
      ) : (
        <>
          {/* La région live de l'écran (#538) : elle annonce les décisions
              **prises** — depuis un autre onglet, ou par quelqu'un d'autre. Les
              demandes qui arrivent, elles, coupent la parole depuis la région
              assertive du shell : les dire ici aussi les dirait deux fois sur le
              seul écran où elles sont déjà sous les yeux. */}
          <RegionLive
            libelle="Arbitrages tranchés"
            mesures={[mesureDesValidationsTranchees(validations)]}
          />
          {enAttente.length === 0 ? (
            <EtatVide
              icone={IconeValidations}
              message={
                validations.length === 0
                  ? `Rien encore sur ${projet.nom} : aucune demande d'arbitrage n'y a été faite.`
                  : `Aucune validation en attente sur ${projet.nom} — les moteurs y tournent sans demander d'arbitrage.`
              }
            />
          ) : (
            <FileValidations validations={validations} decider={decider} />
          )}
          <HistoriqueValidations validations={tranchees} />
        </>
      )}
    </>
  );
}

/**
 * **D'où l'on vient**, quand on vient de quelque part (#1228, critère 2).
 *
 * Le second critère du ticket ne dit pas « plus aucun lien vers cet écran » — la
 * file entière et l'historique restent des raisons légitimes d'y venir depuis un
 * run. Il dit qu'aucun geste n'y **abandonne** : « ça me redirige vers la page
 * validation et je ne sais pas comment revenir directement sur le run pipeline »
 * (retour d'usage du 2026-09-22). Le retour porte donc la lecture ouverte autant
 * que le run, et le lien est en tête — au même endroit que le « ← Tous les
 * runs » de la vue d'un run, qui est le patron du dépôt pour cette place.
 *
 * Il ne rend rien quand on est arrivé par le menu : un écran atteint par sa
 * propre entrée n'a pas de « d'où l'on vient » à proposer.
 */
function LienDeRetour({ retour }: { retour?: string }) {
  if (retour === undefined) return null;
  return (
    <p className="mb-2">
      {/* `text-info-texte` et non la paire `sky-700`/`sky-400` que le « ← Tous
          les runs » de la vue d'un run écrit encore : la couleur se choisit une
          fois, les deux thèmes viennent avec le token (`tests/couleurs.test.ts`)
          — et en clair le token est un cran plus sombre, donc plus lisible. Le
          résidu du produit ne grandit pas d'une ligne pour un lien neuf. */}
      <Link
        href={retour}
        className={`inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-annexe font-medium text-info-texte hover:underline`}
      >
        <IconeFlecheGauche className="size-3.5 shrink-0" />
        {libelleDeRetour(retour)}
      </Link>
    </p>
  );
}

/**
 * Ce qui a déjà été tranché : la trace des arbitrages, **la plus récente en
 * tête** — ce que le commentaire de cet écran promettait depuis #117 sans que
 * rien ne le fasse, la liste sortant dans l'ordre du backend.
 *
 * Chaque ligne porte sa `decision` (#272), la phrase que le backend a écrite au
 * moment de trancher : c'est elle qui dit *d'où* la décision est venue et, pour
 * un refus motivé, **pourquoi**. Sans elle un refus revenait comme un fait sans
 * cause, et rien ne distinguait un arbitrage réfléchi d'une erreur de clic.
 */
function HistoriqueValidations({ validations }: { validations: Validation[] }) {
  if (validations.length === 0) return null;
  // Copie avant tri : `validations` vient du contexte partagé, et le trier sur
  // place réordonnerait la liste que les autres écrans lisent.
  const recentes = [...validations].sort((a, b) =>
    a.horodatage === b.horodatage ? 0 : a.horodatage < b.horodatage ? 1 : -1,
  );
  return (
    <Carte balise="section" densite="aeree" aria-label="Validations tranchées">
      <EnTeteSection
        titre="Déjà tranchées"
        icone={IconeHistorique}
        className="mb-2"
      />
      <div className="overflow-x-auto">
        <table className="w-full text-annexe">
          <thead>
            <tr className="border-b border-bord text-left text-texte-secondaire">
              <th className="py-1 pr-3 font-medium">Tâche</th>
              <th className="py-1 pr-3 font-medium">Agent</th>
              <th className="py-1 pr-3 font-medium">Issue</th>
              <th className="py-1 font-medium">Quand</th>
            </tr>
          </thead>
          <tbody>
            {recentes.map((validation) => (
              <tr key={validation.tache_id} className="border-b border-bord">
                <td
                  className="max-w-64 truncate py-1 pr-3"
                  title={validation.tache_id}
                >
                  {validation.titre || validation.tache_id}
                </td>
                <td className="py-1 pr-3 text-texte-secondaire">
                  {validation.agent}
                  {validation.role ? ` · ${validation.role}` : ""}
                </td>
                <td className="py-1 pr-3">
                  {validation.statut === VALIDATION_APPROUVEE ? (
                    <span className="text-positif-texte">Approuvée</span>
                  ) : (
                    <span className="text-alerte-texte">Refusée</span>
                  )}
                  {validation.decision && (
                    <span className="block text-micro text-texte-secondaire">
                      {validation.decision}
                    </span>
                  )}
                </td>
                <td className="py-1 text-texte-secondaire">
                  {formatDateHeure(validation.horodatage) || "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Carte>
  );
}
