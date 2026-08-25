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
 * Enfin le fil **part de l'historique persisté** (#478) et non plus du seul
 * WebSocket. C'était le défaut que cette page portait depuis sa création : elle
 * ne contenait que ce qui était passé par la socket depuis son ouverture, donc
 * un rechargement pendant un run d'une heure effaçait tout ce qu'on avait sous
 * les yeux. `GET /api/journal` — figé au contrat depuis #183, jamais servi
 * jusque-là — rend désormais le journal du projet ; le direct du shell se
 * superpose par-dessus le temps que la lecture suivante le rattrape
 * (`lib/journal`, `lib/useJournal`).
 *
 * Depuis #281 le fil est celui **du projet actif** : la socket déclare sa portée
 * à l'ouverture (#277) et l'historique se lit à la même portée, le tri se fait
 * donc côté backend et il n'y a rien à refiltrer ici. Les listes de filtres,
 * dérivées du fil, en héritent — elles ne proposent que des agents et des tâches
 * de ce projet, sans une ligne de plus. Un changement de projet remonte cette
 * page (`key` du shell) : les filtres repartent à zéro plutôt que de rester
 * posés sur une tâche qui n'est plus.
 */

import { useMemo, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { FilActivite } from "@/components/FilActivite";
import { Bouton, Carte, Champ, ChampListe } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesEvenements } from "@/lib/annonces";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  estNotableNotification,
  libelleTypeEvenement,
  resumeEvenement,
} from "@/lib/evenements";
import { fusionnerJournal } from "@/lib/journal";
import { type Evenement } from "@/lib/types";
import { useJournal } from "@/lib/useJournal";

/** La valeur du choix « tout » d'une liste déroulante — jamais un vrai filtre. */
const TOUS = "";

/** Un choix de liste déroulante : ce qu'on filtre, et comment on le nomme. */
type Option = { valeur: string; libelle: string };

export default function PageJournal() {
  const { projet, portee, evenements: direct, connecte, erreur, revision } =
    useEtatGlobal();
  const historique = useJournal(portee, null, revision);

  // L'historique d'abord, le direct qu'il n'a pas encore rattrapé par-dessus :
  // c'est ce qui fait qu'un rechargement ne perd rien **et** qu'un événement
  // reçu à l'instant s'affiche sans attendre la lecture suivante.
  const evenements = useMemo(
    () => fusionnerJournal(historique.evenements, direct),
    [historique.evenements, direct],
  );

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

  // Le backend plafonne une page à 200 entrées : au-delà, l'écran en montre une
  // partie et doit le dire (`total` est le compte avant pagination).
  const tronque = historique.total > historique.evenements.length;

  const reinitialiser = () => {
    setRecherche("");
    setType(TOUS);
    setAgent(TOUS);
    setTache(TOUS);
    setNotableSeul(false);
  };

  return (
    <>
      {/* Celle du shell d'abord : elle couvre l'API entière, là où la seconde ne
          dit que la lecture du journal — mais un journal illisible sur une API
          par ailleurs debout ne doit pas passer pour un projet sans activité. */}
      <BanniereErreurApi erreur={erreur ?? historique.erreur} />

      {/* La région live de l'écran (#538) : le fil **non filtré**, pour que ce
          qui s'annonce soit l'arrivée d'événements et non le résultat d'une
          frappe dans la zone de recherche.
          Montée une fois l'historique lu — les deux lectures qui alimentent ce
          fil sont monotones —, sinon les cent entrées relues à l'ouverture
          s'annonceraient comme du direct. */}
      {!historique.chargement && (
        <RegionLive
          libelle="Activité du journal"
          mesures={[mesureDesEvenements(evenements.length)]}
        />
      )}

      {/* Ce que la page montre, et ce qu'elle ne montre pas : la promesse est
          faite ici plutôt que devinée d'une liste courte. Le compte tronqué se
          dit — une page bornée qui se tait passe pour un inventaire. */}
      <Carte balise="p" className="text-sm text-neutral-600 dark:text-neutral-400">
        Le <strong className="font-medium">journal persisté</strong> de{" "}
        <strong className="font-medium">{projet.nom}</strong>, du plus récent au
        plus ancien : il est relu à l&apos;ouverture de la page, et le temps réel
        s&apos;y ajoute au fil de l&apos;eau.{" "}
        {tronque
          ? `Les ${evenements.length} plus récents des ${historique.total} événements du projet sont affichés.`
          : "Un rechargement ne perd donc rien."}{" "}
        Il ne mélange jamais les fils de deux projets. L&apos;état de référence,
        lui, reste celui des tâches, des agents et des coûts.
      </Carte>

      {!connecte && (
        // L'historique, lui, est là : ce qui s'arrête est l'ajout des lignes
        // suivantes. La barre supérieure porte déjà l'indicateur, mais nulle
        // part ailleurs il n'explique un fil qui cesse d'avancer.
        <p className="text-sm text-amber-700 dark:text-amber-400">
          Flux temps réel interrompu — le journal ci-dessous reste lisible, il
          reprendra son avance à la reconnexion.
        </p>
      )}

      <Carte
        balise="section"
        densite="aeree"
        aria-label="Filtres du journal"
        className="flex flex-col gap-3"
      >
        <div className="grid gap-3 @md:grid-cols-2 @3xl:grid-cols-4">
          <Champ
            id="journal-recherche"
            libelle="Rechercher"
            type="search"
            name="recherche-journal"
            autoComplete="off"
            value={recherche}
            onChange={(e) => setRecherche(e.target.value)}
            placeholder="tâche, agent, détail…"
          />
          <ListeFiltre
            id="journal-type"
            libelle="Type d'événement"
            tout="Tous les types"
            options={types}
            valeur={type}
            surChoix={setType}
          />
          <ListeFiltre
            id="journal-agent"
            libelle="Agent"
            tout="Tous les agents"
            options={agents}
            valeur={agent}
            surChoix={setAgent}
          />
          <ListeFiltre
            id="journal-tache"
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
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              onClick={reinitialiser}
            >
              Réinitialiser les filtres
            </Bouton>
          )}
        </div>

        {/* Le compteur reste muet, mais l'écran ne l'est plus (#538) : la région
            live est **au-dessus**, et elle n'annonce pas ce compteur-ci — elle
            annonce le fil, agrégé sur la fenêtre de `lib/useAnnonce`. Ce que
            refusait la note d'origine (« un bavard permanent ») était d'annoncer
            chaque ligne ; « 12 nouveaux événements » toutes les cinq secondes est
            l'inverse exact, et c'est ce que le ticket appelle annoncer un état
            plutôt qu'un journal. Ce compteur-là suit les **filtres**, donc il
            bougerait à chaque frappe : il n'a rien à faire dans une annonce. */}
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          {filtre
            ? `${filtres.length} événement(s) sur ${evenements.length}`
            : `${evenements.length} événement(s)`}
        </p>
      </Carte>

      {evenements.length === 0 && historique.chargement ? (
        // La première lecture est encore en vol : un « rien encore » affiché ici
        // serait faux la moitié du temps, l'historique arrivant juste après.
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Lecture du journal…
        </p>
      ) : evenements.length === 0 ? (
        // Le vide du projet, et non celui d'un filtre : le distinguer est ce
        // qui évite de chercher une panne (le bandeau ci-dessus dit si le flux
        // est coupé) ou de croire que rien ne tourne nulle part (#281).
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Rien encore sur {projet.nom} : aucun événement de ce projet n&apos;a
          été consigné.
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
  id,
  libelle,
  tout,
  options,
  valeur,
  surChoix,
}: {
  id: string;
  libelle: string;
  /** Le libellé du choix neutre — « Tous les agents », « Toutes les tâches »… */
  tout: string;
  options: Option[];
  valeur: string;
  surChoix: (valeur: string) => void;
}) {
  return (
    <ChampListe
      id={id}
      libelle={libelle}
      value={valeur}
      onChange={(e) => surChoix(e.target.value)}
      // Rien à filtrer tant que le fil est vide : la liste n'aurait que son
      // choix neutre, autant la désigner comme inerte.
      disabled={options.length === 0}
    >
      <option value={TOUS}>{tout}</option>
      {options.map((option) => (
        <option key={option.valeur} value={option.valeur}>
          {option.libelle}
        </option>
      ))}
    </ChampListe>
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
