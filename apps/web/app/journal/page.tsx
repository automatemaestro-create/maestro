"use client";

/**
 * La page Journal (#249, lot 5 de la vague Control Tower v3 #242) : le fil
 * d'activité en **plein format**, avec de quoi s'y retrouver — filtres par type
 * d'événement, par agent et par tâche, et recherche texte.
 *
 * Le déménagement était prévu de longue date : le tableau de bord épuré (#191)
 * n'a jamais gardé le fil que faute de page où le mettre, `FilActivite` portant
 * depuis lors un `renvoi` resté éteint. Ce lot crée la page ; l'entrée de menu
 * (`lib/navigation`) allume le renvoi toute seule, sans une ligne de plus dans
 * le composant — c'est tout l'intérêt de `entreeParLibelle`.
 *
 * Trois partis pris qui expliquent ce qu'on ne trouve pas ici :
 *
 * - **La ligne d'activité n'est pas réécrite.** La page rend `FilActivite` sans
 *   `limite` : aperçu et fil complet partagent la même mise en forme de ligne
 *   (heure, icône, résumé) et le même vocabulaire (`lib/evenements`). Une page
 *   qui se serait fait sa propre liste aurait figé deux rendus à faire vivre —
 *   là, ce que le lot 6 (#250) apportera aux lignes profite aux deux écrans.
 * - **« Notable » n'est pas un second tri.** La case reprend
 *   `estNotableNotification`, l'exacte fonction sur laquelle le centre de
 *   notifications (#119) filtre déjà : la cloche et le journal ne peuvent pas
 *   diverger sur ce qui mérite l'attention.
 * - **Les options des listes sortent du fil lui-même.** Rien n'est figé en dur :
 *   pas de liste à maintenir quand le backend enrichit le flux, et aucune option
 *   morte qui ne rendrait jamais un résultat.
 *
 * Enfin le fil est **éphémère** par construction — il ne contient que ce qui est
 * passé par le WebSocket depuis l'ouverture de la page (`MAX_EVENEMENTS` au
 * plus), l'état de référence restant le REST. L'écran le dit : un journal
 * persisté et requêtable existe côté contrat (`GET /api/journal`, #183) mais
 * n'est pas encore servi par le backend, et cette page ne le promet pas.
 *
 * Depuis #281 le fil est celui **du projet actif** : la socket déclare sa portée
 * à l'ouverture (#277), le tri se fait donc côté backend et il n'y a rien à
 * refiltrer ici. Les listes de filtres, dérivées du fil, en héritent — elles ne
 * proposent que des agents et des tâches de ce projet, sans une ligne de plus.
 * Un changement de projet remonte cette page (`key` du shell) : les filtres
 * repartent à zéro plutôt que de rester posés sur une tâche qui n'est plus.
 */

import { useMemo, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  estNotableNotification,
  libelleTypeEvenement,
  resumeEvenement,
} from "@/lib/evenements";
import { type Evenement } from "@/lib/types";
import { MAX_EVENEMENTS } from "@/lib/useControlTower";

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm font-normal " +
  "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none " +
  "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 " +
  "dark:focus:border-neutral-600 dark:[&>option]:bg-neutral-900";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-xs font-medium text-neutral-600 dark:text-neutral-400";

/** La valeur du choix « tout » d'une liste déroulante — jamais un vrai filtre. */
const TOUS = "";

/** Un choix de liste déroulante : ce qu'on filtre, et comment on le nomme. */
type Option = { valeur: string; libelle: string };

export default function PageJournal() {
  const { projet, evenements, connecte, erreur } = useEtatGlobal();

  const [recherche, setRecherche] = useState("");
  const [type, setType] = useState(TOUS);
  const [agent, setAgent] = useState(TOUS);
  const [tache, setTache] = useState(TOUS);
  const [notableSeul, setNotableSeul] = useState(false);

  const types = useMemo(() => optionsType(evenements), [evenements]);
  const agents = useMemo(() => optionsAgent(evenements), [evenements]);
  const taches = useMemo(() => optionsTache(evenements), [evenements]);

  const texte = recherche.trim().toLocaleLowerCase("fr");
  const filtres = useMemo(
    () =>
      evenements.filter((evenement) => {
        if (type !== TOUS && evenement.type !== type) return false;
        if (agent !== TOUS && evenement.agent !== agent) return false;
        if (tache !== TOUS && evenement.tache_id !== tache) return false;
        if (notableSeul && !estNotableNotification(evenement)) return false;
        return texte === "" || porteLeTexte(evenement, texte);
      }),
    [evenements, type, agent, tache, notableSeul, texte],
  );

  const filtre =
    texte !== "" ||
    type !== TOUS ||
    agent !== TOUS ||
    tache !== TOUS ||
    notableSeul;

  const reinitialiser = () => {
    setRecherche("");
    setType(TOUS);
    setAgent(TOUS);
    setTache(TOUS);
    setNotableSeul(false);
  };

  return (
    <>
      <BanniereErreurApi erreur={erreur} />

      {/* Ce que la page montre, et ce qu'elle ne montre pas : la promesse est
          faite ici plutôt que devinée d'une liste courte. */}
      <p className="rounded-lg border border-neutral-200 bg-white p-3 text-sm text-neutral-600 shadow-sm dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400">
        Le fil de la <strong className="font-medium">session</strong>, sur{" "}
        <strong className="font-medium">{projet.nom}</strong> : les{" "}
        {MAX_EVENEMENTS} derniers événements reçus en temps réel depuis
        l&apos;ouverture de la Control Tower, du plus récent au plus ancien. Il
        repart à zéro au rechargement de la page — et au changement de projet,
        dont il ne mélange jamais les fils. L&apos;état de référence, lui, reste
        celui des tâches, des agents et des coûts.
      </p>

      {!connecte && (
        // Toute cette page vient du WebSocket : coupé, elle ne se remplira plus.
        // La barre supérieure porte déjà l'indicateur, mais nulle part ailleurs
        // il n'explique un écran vide.
        <p className="text-sm text-amber-700 dark:text-amber-400">
          Flux temps réel interrompu — le fil reprendra à la reconnexion.
        </p>
      )}

      <section
        aria-label="Filtres du journal"
        className="flex flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
      >
        <div className="grid gap-3 @md:grid-cols-2 @3xl:grid-cols-4">
          <label className={CLASSE_LIBELLE}>
            Rechercher
            <input
              type="search"
              name="recherche-journal"
              autoComplete="off"
              value={recherche}
              onChange={(e) => setRecherche(e.target.value)}
              placeholder="tâche, agent, détail…"
              className={CLASSE_CHAMP}
            />
          </label>
          <ListeFiltre
            libelle="Type d'événement"
            tout="Tous les types"
            options={types}
            valeur={type}
            surChoix={setType}
          />
          <ListeFiltre
            libelle="Agent"
            tout="Tous les agents"
            options={agents}
            valeur={agent}
            surChoix={setAgent}
          />
          <ListeFiltre
            libelle="Tâche"
            tout="Toutes les tâches"
            options={taches}
            valeur={tache}
            surChoix={setTache}
          />
        </div>

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <label className="flex items-center gap-2 text-xs font-medium text-neutral-600 dark:text-neutral-400">
            <input
              type="checkbox"
              checked={notableSeul}
              onChange={(e) => setNotableSeul(e.target.checked)}
              className="size-4 rounded border-neutral-300 dark:border-neutral-700"
            />
            Notable seulement
            <span className="font-normal text-neutral-500 dark:text-neutral-500">
              (ce que remonte la cloche)
            </span>
          </label>
          {filtre && (
            <button
              type="button"
              onClick={reinitialiser}
              className="rounded-md border border-neutral-200 px-2 py-1 text-xs font-medium text-neutral-600 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-400 dark:hover:bg-neutral-800"
            >
              Réinitialiser les filtres
            </button>
          )}
        </div>

        {/* Pas de région « live » : le flux temps réel ferait de ce compteur un
            bavard permanent pour un lecteur d'écran. */}
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          {filtre
            ? `${filtres.length} événement(s) sur ${evenements.length}`
            : `${evenements.length} événement(s)`}
        </p>
      </section>

      {evenements.length === 0 ? (
        // Le vide du projet, et non celui d'un filtre : le distinguer est ce
        // qui évite de chercher une panne (le bandeau ci-dessus dit si le flux
        // est coupé) ou de croire que rien ne tourne nulle part (#281).
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Rien encore sur {projet.nom} : aucun événement de ce projet n&apos;est
          arrivé depuis l&apos;ouverture de la Control Tower.
        </p>
      ) : filtres.length === 0 ? (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Aucun événement ne correspond à ces filtres.
        </p>
      ) : (
        // Sans `limite` : le fil entier, dans la mise en forme de l'aperçu.
        <FilActivite evenements={filtres} />
      )}
    </>
  );
}

/** Une liste déroulante de filtre, choix « tout » en tête. */
function ListeFiltre({
  libelle,
  tout,
  options,
  valeur,
  surChoix,
}: {
  libelle: string;
  /** Le libellé du choix neutre — « Tous les agents », « Toutes les tâches »… */
  tout: string;
  options: Option[];
  valeur: string;
  surChoix: (valeur: string) => void;
}) {
  return (
    <label className={CLASSE_LIBELLE}>
      {libelle}
      <select
        value={valeur}
        onChange={(e) => surChoix(e.target.value)}
        // Rien à filtrer tant que le fil est vide : la liste n'aurait que son
        // choix neutre, autant la désigner comme inerte.
        disabled={options.length === 0}
        className={CLASSE_CHAMP + " disabled:opacity-50"}
      >
        <option value={TOUS}>{tout}</option>
        {options.map((option) => (
          <option key={option.valeur} value={option.valeur}>
            {option.libelle}
          </option>
        ))}
      </select>
    </label>
  );
}

/** Les types présents dans le fil, nommés en français et triés par libellé. */
function optionsType(evenements: Evenement[]): Option[] {
  const presents = new Set(
    evenements.map((evenement) => evenement.type).filter(Boolean),
  );
  return [...presents]
    .map((valeur) => ({ valeur, libelle: libelleTypeEvenement(valeur) }))
    .sort((a, b) => a.libelle.localeCompare(b.libelle, "fr"));
}

/** Les agents ayant émis quelque chose, par ordre alphabétique. */
function optionsAgent(evenements: Evenement[]): Option[] {
  const presents = new Set(
    evenements.map((evenement) => evenement.agent).filter(Boolean),
  );
  return [...presents]
    .sort((a, b) => a.localeCompare(b, "fr"))
    .map((valeur) => ({ valeur, libelle: valeur }));
}

/**
 * Les tâches apparues dans le fil. Le titre n'accompagne pas tous les
 * événements d'une même tâche (`tache.reference` n'en porte pas) : le premier
 * rencontré fait foi, l'identifiant sert de repli en attendant.
 */
function optionsTache(evenements: Evenement[]): Option[] {
  const parId = new Map<string, string>();
  for (const evenement of evenements) {
    const id = evenement.tache_id;
    if (!id) continue;
    const connu = parId.get(id);
    if (connu === undefined || (connu === id && evenement.titre)) {
      parId.set(id, evenement.titre || id);
    }
  }
  return [...parId.entries()]
    .map(([valeur, libelle]) => ({ valeur, libelle }))
    .sort((a, b) => a.libelle.localeCompare(b.libelle, "fr"));
}

/**
 * La recherche texte porte sur ce que la ligne **montre** (`resumeEvenement`)
 * et sur ce qu'elle porte sans l'afficher — détail, description, identifiants.
 * Chercher « T-12 » ou un mot du détail doit retrouver la ligne, même quand le
 * résumé n'en dit rien.
 */
function porteLeTexte(evenement: Evenement, texte: string): boolean {
  const champs = [
    resumeEvenement(evenement),
    libelleTypeEvenement(evenement.type),
    evenement.detail,
    evenement.description,
    evenement.titre,
    evenement.tache_id,
    evenement.agent,
    evenement.role,
    evenement.run_id,
  ];
  return champs.some((champ) =>
    (champ || "").toLocaleLowerCase("fr").includes(texte),
  );
}
