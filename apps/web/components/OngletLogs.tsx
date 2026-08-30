"use client";

/**
 * L'onglet **Logs** d'une fiche agent (#266, lot 14 de la vague « Control Tower
 * v3 — agents » #243) : ce que cet agent fait, et ce qu'il a fait.
 *
 * Ce que le ticket corrige tient en une phrase : l'activité d'un agent ne se
 * lisait que dans le fil **global** du tableau de bord, tous agents confondus, et
 * disparaissait au rechargement de la page. Les quatre autres facettes disent qui
 * il est, ce qu'on lui a appris, ce qu'on lui a permis et ce qu'on lui dit —
 * aucune ne dit ce qu'il en a fait.
 *
 * ## Trois décisions, reprises de #478 et pour les mêmes raisons
 *
 * - **L'appartenance à l'agent vient de l'API** (`GET /api/journal?agent=…`,
 *   filtre déjà au contrat #183), jamais d'un tri sur le fil du shell. C'est le
 *   point qui ne se négocie pas : une page de journal est plafonnée à 200
 *   entrées, donc refiltrer une page du **projet entier** ne montrerait d'un
 *   agent discret que le silence des autres. Le direct du shell, lui, est bien
 *   filtré ici — mais il ne fait que se superposer à l'historique le temps que
 *   celui-ci le rattrape.
 * - **Aucune seconde WebSocket** : la lecture suit le pouls du shell
 *   (`revision`), comme la vue d'un run et la page Journal.
 * - **La ligne n'est pas réécrite.** `FilActivite` rend ici exactement ce qu'il
 *   rend au tableau de bord, sur la page Journal et dans la vue d'un run — donc
 *   les résumés lisibles de #250, le repli des rafales et le dépli qui rend les
 *   identifiants. C'est le seul moyen que « les lignes sont celles du Journal »
 *   reste vrai demain : un onglet qui se serait fait sa propre liste aurait figé
 *   un second rendu à faire vivre.
 *
 * ## Ce qui est propre à cet onglet
 *
 * **Le groupement par tâche**, que le ticket demande et qu'aucun autre écran ne
 * fait. Un fil par tâche plutôt qu'une colonne unique : `grouperParTache`
 * (`lib/journal`) range les lignes, l'ordre des groupes reste celui du fil — la
 * tâche dont la ligne la plus récente est la plus récente ouvre la liste, donc
 * celle sur laquelle l'agent travaille en ce moment. Ce qui ne relève d'aucune
 * tâche (planification, capacité, proposition de playbook) tombe dans un groupe
 * « Hors tâche » qui prend sa place dans le même ordre, au lieu d'être relégué ou
 * tu.
 *
 * **Le filtre par niveau** (`lib/evenements`), qui est le seul vrai ajout de
 * vocabulaire du lot — voir `NIVEAUX_LOG` pour l'arbitrage : le niveau est la
 * *famille* d'une ligne (erreur, refus, décision, info) et non une sévérité de
 * plus, parce que « qu'est-ce qu'on lui a refusé ? » est la question qu'on pose
 * le plus souvent à un journal d'agent et qu'aucune échelle de sévérité ne
 * permet de l'isoler.
 *
 * **Les options des deux listes sortent du fil**, jamais d'une table écrite en
 * dur (règle de #249) : pas de liste à tenir à jour quand le backend enrichit le
 * flux, et aucune option morte qui ne rendrait jamais un résultat. Les niveaux
 * gardent en revanche l'ordre de `NIVEAUX_LOG` — le plus pressant d'abord — là où
 * les tâches se trient par nom : une liste de niveaux triée alphabétiquement
 * mettrait « Décision » avant « Erreur » et perdrait la seule chose que cet ordre
 * dit.
 */

import { useMemo, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { IconeJournal } from "@/components/Icones";
import {
  Bouton,
  Carte,
  EnTeteSection,
  ListeFiltre,
  type OptionFiltre,
} from "@/components/Primitives";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { NIVEAUX_LOG, niveauEvenement } from "@/lib/evenements";
import { fusionnerJournal, grouperParTache, optionsTache } from "@/lib/journal";
import type { Evenement } from "@/lib/types";
import { useJournal } from "@/lib/useJournal";

/** La valeur du choix « tout » d'une liste déroulante — jamais un vrai filtre. */
const TOUS = "";

export function OngletLogs({ nom }: { nom: string }) {
  const {
    projet,
    portee,
    evenements: direct,
    connecte,
    erreur,
    revision,
  } = useEtatGlobal();
  // Le filtre `agent` est passé à l'API, pas appliqué après coup : voir l'en-tête.
  const historique = useJournal(portee, { agent: nom }, revision);

  // L'historique d'abord, le direct qu'il n'a pas encore rattrapé par-dessus.
  // Celui-ci vient du shell, donc de tout le projet : c'est le seul endroit du
  // fichier où le tri par agent se fait ici, et il le peut — le fil du shell est
  // ce qui vient d'arriver, pas une page bornée sur laquelle on chercherait.
  const evenements = useMemo(
    () =>
      fusionnerJournal(
        historique.evenements,
        direct.filter((evenement) => evenement.agent === nom),
      ),
    [historique.evenements, direct, nom],
  );

  const [niveau, setNiveau] = useState(TOUS);
  const [tache, setTache] = useState(TOUS);

  const niveaux = useMemo(() => optionsNiveau(evenements), [evenements]);
  const taches = useMemo(() => optionsTache(evenements), [evenements]);

  const retenus = useMemo(
    () =>
      evenements.filter((evenement) => {
        if (niveau !== TOUS && niveauEvenement(evenement) !== niveau) return false;
        return tache === TOUS || evenement.tache_id === tache;
      }),
    [evenements, niveau, tache],
  );

  const groupes = useMemo(() => grouperParTache(retenus), [retenus]);

  const filtre = niveau !== TOUS || tache !== TOUS;
  // Le backend plafonne une page à 200 entrées : au-delà, l'onglet en montre une
  // partie et doit le dire (`total` est le compte avant pagination).
  const tronque = historique.total > historique.evenements.length;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      {/* Celle du shell d'abord : elle couvre l'API entière, là où la seconde ne
          dit que la lecture du journal — mais un journal illisible sur une API
          par ailleurs debout ne doit pas passer pour un agent qui n'a rien fait. */}
      <BanniereErreurApi erreur={erreur ?? historique.erreur} />

      <Carte balise="p" className="text-annexe text-neutral-600 dark:text-neutral-400">
        Ce que <strong className="font-medium">{nom}</strong> a fait sur{" "}
        <strong className="font-medium">{projet.nom}</strong>, groupé par tâche et
        du plus récent au plus ancien : appels d&apos;outil, refus de permission,
        décisions et erreurs. L&apos;historique est relu à l&apos;ouverture de
        l&apos;onglet, et le temps réel s&apos;y ajoute au fil de l&apos;eau
        {tronque
          ? ` — les ${historique.evenements.length} plus récentes des ${historique.total} lignes de cet agent sont affichées.`
          : ", donc un rechargement ne perd rien."}
      </Carte>

      {!connecte && (
        // L'historique, lui, est là : ce qui s'arrête est l'ajout des lignes
        // suivantes. La barre supérieure porte déjà l'indicateur, mais nulle part
        // ailleurs il n'explique un fil qui cesse d'avancer.
        <p className="text-annexe text-amber-700 dark:text-amber-400">
          Flux temps réel interrompu — les lignes ci-dessous restent lisibles,
          elles reprendront leur avance à la reconnexion.
        </p>
      )}

      <Carte
        balise="section"
        densite="aeree"
        aria-label={`Filtres des logs de ${nom}`}
        className="flex flex-col gap-3"
      >
        <div className="grid gap-3 @md:grid-cols-2">
          <ListeFiltre
            id="logs-niveau"
            libelle="Niveau"
            tout="Tous les niveaux"
            options={niveaux}
            valeur={niveau}
            surChoix={setNiveau}
          />
          <ListeFiltre
            id="logs-tache"
            libelle="Tâche"
            tout="Toutes les tâches"
            options={taches}
            valeur={tache}
            surChoix={setTache}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <p className="text-annexe text-neutral-500 dark:text-neutral-400">
            {filtre
              ? `${retenus.length} ligne(s) sur ${evenements.length}`
              : `${evenements.length} ligne(s)`}
          </p>
          {filtre && (
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              onClick={() => {
                setNiveau(TOUS);
                setTache(TOUS);
              }}
            >
              Réinitialiser les filtres
            </Bouton>
          )}
        </div>
      </Carte>

      <section aria-label={`Logs de ${nom}`} className="flex flex-col gap-4">
        <EnTeteSection titre="Journal de l'agent" icone={IconeJournal} />
        {evenements.length === 0 && historique.chargement ? (
          // La première lecture est encore en vol : un « rien encore » affiché
          // ici serait faux la moitié du temps, l'historique arrivant juste après.
          <p className="text-corps text-neutral-500 dark:text-neutral-400">
            Lecture du journal de {nom}…
          </p>
        ) : evenements.length === 0 ? (
          // Le silence de l'agent, et non celui d'un filtre : le distinguer est
          // ce qui évite de chercher une panne (le bandeau ci-dessus dit si le
          // flux est coupé) ou de croire que l'onglet est cassé.
          <p className="text-corps text-neutral-500 dark:text-neutral-400">
            Rien encore : aucune ligne de {nom} n&apos;a été consignée sur{" "}
            {projet.nom}.
          </p>
        ) : groupes.length === 0 ? (
          <p className="text-corps text-neutral-500 dark:text-neutral-400">
            Aucune ligne ne correspond à ces filtres.
          </p>
        ) : (
          groupes.map((groupe) => (
            <FilActivite
              key={groupe.tacheId || "hors-tache"}
              evenements={groupe.evenements}
              titre={groupe.libelle}
              renvoi={groupe.renvoi}
              // Une sous-partie du bloc ci-dessus, pas une section de page.
              niveau={3}
            />
          ))
        )}
      </section>
    </div>
  );
}

/**
 * Les niveaux **présents** dans le fil, dans l'ordre de `NIVEAUX_LOG`.
 *
 * Dérivés du fil comme les tâches (règle de #249 : aucune option morte), mais
 * **non triés par libellé** : l'ordre des quatre valeurs dit lequel est le plus
 * pressant, et le perdre mettrait « Décision » avant « Erreur » pour une raison
 * qui n'est que celle de l'alphabet.
 */
function optionsNiveau(evenements: Evenement[]): OptionFiltre[] {
  const presents = new Set<string>(evenements.map(niveauEvenement));
  return NIVEAUX_LOG.filter(({ cle }) => presents.has(cle)).map(
    ({ cle, libelle }) => ({ valeur: cle, libelle }),
  );
}
