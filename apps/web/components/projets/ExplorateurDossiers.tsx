"use client";

/**
 * L'explorateur de dossiers de l'écran Projets (#225), servi par l'API (#223).
 *
 * Un navigateur ne livre jamais de chemin absolu — ni un `<input type="file">`,
 * ni un glisser-déposer : c'est donc le backend, qui tourne sur le poste, qui
 * énumère (docs/05 §2.7). Ce composant ne fabrique aucun chemin : il n'affiche
 * et ne rend que des chemins **énumérés par l'API**, ce qui est exactement ce
 * que le critère « aucun chemin absolu saisi à la main » demande.
 *
 * Le parti pris qui structure le rendu : **un refus n'est pas une liste vide**.
 * « ce dossier n'a pas de sous-dossier » et « je refuse de regarder là » sont
 * deux réponses différentes, et les confondre rend un explorateur inutilisable.
 * Un refus garde donc la page précédente à l'écran, s'affiche avec son motif et
 * laisse toujours une porte de sortie (remonter, revenir aux racines) — l'erreur
 * ne casse pas la navigation (critère #225).
 *
 * Un composant **partagé**, enfin, et rendu aussi bien seul (écran Projets,
 * #225) qu'à l'intérieur du formulaire de projet (porte d'entrée, #279) : il ne
 * contient donc **aucun `<form>`** (#312). HTML interdit de les imbriquer —
 * l'analyseur jette le formulaire interne du HTML rendu côté serveur là où
 * React le crée côté client, d'où une erreur d'hydratation et une soumission
 * qui n'appartient à personne.
 */

import { useCallback, useEffect, useState } from "react";

import { IconeDossier, IconeFlecheHaut } from "@/components/Icones";
import { BadgeEtat } from "@/components/Primitives";
import {
  chargerDisponibiliteSelecteur,
  chargerExplorateur,
  ErreurProjet,
  ouvrirSelecteurNatif,
} from "@/lib/api";
import { conseilMotif } from "@/lib/projets";
import type {
  DisponibiliteSelecteur,
  OrigineDossier,
  PageExplorateur,
  RefusProjet,
} from "@/lib/types";

/** Le refus porté par une exception — une panne réseau en est un aussi. */
export function refusDepuis(erreur: unknown): RefusProjet {
  if (erreur instanceof ErreurProjet) {
    return { motif: erreur.motif, message: erreur.message };
  }
  return {
    motif: "api-injoignable",
    message: erreur instanceof Error ? erreur.message : String(erreur),
  };
}

const CLASSE_BOUTON_DOUX =
  "rounded-md border border-neutral-300 px-2.5 py-1 text-xs font-medium text-neutral-600 " +
  "hover:bg-neutral-50 disabled:opacity-40 dark:border-neutral-700 dark:text-neutral-300 " +
  "dark:hover:bg-neutral-800";

/**
 * Ce que dit la pastille d'un point d'entrée (#278). Le libellé répond à
 * « pourquoi ce dossier m'est-il proposé ? » — sans lui, le dossier utilisateur,
 * un disque et le parent d'un projet se ressemblent au point de brouiller la
 * lecture de la page d'entrée.
 */
const LIBELLE_ORIGINE: Record<OrigineDossier, string> = {
  utilisateur: "dossier utilisateur",
  recent: "récent",
  projet: "projet déclaré",
  volume: "disque",
  configuree: "racine configurée",
};

/** Le bandeau d'un refus : sa phrase, le geste qui en sort, et son code. */
export function RefusMotive({
  refus,
  titre,
}: {
  refus: RefusProjet;
  titre: string;
}) {
  const conseil = conseilMotif(refus.motif);
  return (
    <div
      role="alert"
      className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
    >
      <p className="font-medium">
        {titre} — {refus.message}
      </p>
      {conseil && <p className="mt-1">{conseil}</p>}
      <p className="mt-1 text-amber-700 dark:text-amber-400">
        motif : <code className="font-mono">{refus.motif}</code>
      </p>
    </div>
  );
}

export function ExplorateurDossiers({
  cheminInitial,
  onChoisir,
  onFermer,
}: {
  /** Le dossier ouvert à l'arrivée — `null` : les racines explorables. */
  cheminInitial: string | null;
  onChoisir: (chemin: string) => void;
  onFermer: () => void;
}) {
  const [page, setPage] = useState<PageExplorateur | null>(null);
  const [chargement, setChargement] = useState(true);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [selecteur, setSelecteur] = useState<DisponibiliteSelecteur | null>(null);
  const [ouvertureNative, setOuvertureNative] = useState(false);
  const [saisie, setSaisie] = useState("");

  /**
   * Ouvre `chemin`, et décide de ce qu'il advient du bandeau de refus.
   *
   * `refusConserve` est ce qui reste affiché **quand l'ouverture réussit** :
   * `null` dans le cas courant (une navigation efface le refus précédent, qui
   * ne parle plus du dossier affiché), le refus du sélecteur natif quand on
   * ouvre l'explorateur *sur* un dossier lisible mais non déclarable. Sans ce
   * paramètre, le motif posé juste avant l'appel était **effacé par le succès
   * de l'ouverture** : le seul cas où le refus et la page qu'on regarde parlent
   * du même dossier était aussi le seul où le refus disparaissait.
   *
   * Une ouverture qui **échoue** garde son propre refus : il explique pourquoi
   * on ne peut même pas regarder, ce qui prime sur « ce dossier n'est pas
   * déclarable ».
   */
  const ouvrir = useCallback(
    async (chemin: string | null, refusConserve: RefusProjet | null = null) => {
      setChargement(true);
      try {
        setPage(await chargerExplorateur(chemin));
        setRefus(refusConserve);
      } catch (erreur) {
        // La page précédente reste affichée : un refus ne doit pas laisser
        // l'utilisateur devant un panneau vide dont il ne peut plus sortir.
        setRefus(refusDepuis(erreur));
      } finally {
        setChargement(false);
      }
    },
    [],
  );

  useEffect(() => {
    // Chargement différé d'un tick, comme partout dans le shell : l'effet
    // lui-même ne déclenche aucun setState synchrone.
    const tick = setTimeout(() => void ouvrir(cheminInitial), 0);
    return () => clearTimeout(tick);
  }, [ouvrir, cheminInitial]);

  useEffect(() => {
    // L'état du sélecteur natif est demandé une fois, à l'arrivée : il dépend
    // du poste et du backend, pas du dossier ouvert. Son échec n'est pas une
    // panne de l'explorateur — on retombe simplement sur « pas de bouton ».
    let vivant = true;
    const tick = setTimeout(() => {
      void chargerDisponibiliteSelecteur()
        .then((etat) => vivant && setSelecteur(etat))
        .catch(() => vivant && setSelecteur(null));
    }, 0);
    return () => {
      vivant = false;
      clearTimeout(tick);
    };
  }, []);

  const courant = page?.chemin ?? null;
  const dossiers = page?.dossiers ?? [];

  /**
   * Le dialogue natif, et ce qu'on fait de son verdict. Trois issues, toutes
   * sans cul-de-sac : annulé, on ne touche à rien ; déclarable, on le choisit ;
   * lisible mais non déclarable (une racine de disque, le dossier utilisateur
   * nu), on **ouvre l'explorateur dessus** avec le motif affiché — de quoi
   * descendre d'un cran plutôt que de recommencer.
   */
  const parcourirNatif = useCallback(async () => {
    setOuvertureNative(true);
    try {
      const choix = await ouvrirSelecteurNatif(courant);
      if (choix.annule || choix.chemin === null) return;
      if (choix.racine_valide) {
        onChoisir(choix.chemin);
        return;
      }
      await ouvrir(choix.chemin, choix.refus);
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
    } finally {
      setOuvertureNative(false);
    }
  }, [courant, onChoisir, ouvrir]);

  /** Le chemin saisi, ouvert — par le bouton « Aller » comme par `Entrée`. */
  const allerAuChemin = useCallback(() => {
    const chemin = saisie.trim();
    if (chemin) void ouvrir(chemin);
  }, [saisie, ouvrir]);

  return (
    <section
      aria-label="Explorateur de dossiers"
      className="rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-xs text-neutral-500 dark:text-neutral-400">
          Dossier courant :{" "}
          <code className="font-mono break-all text-neutral-800 dark:text-neutral-200">
            {courant ?? "dossiers explorables"}
          </code>
        </p>
        <div className="flex shrink-0 flex-wrap gap-2">
          <button
            type="button"
            onClick={() => void ouvrir(page?.parent ?? null)}
            disabled={chargement || courant === null}
            // Même raison qu'au bouton « Choisir » plus bas (#536) : sur un
            // bouton désactivé, le `title` n'atteignait personne.
            aria-label={
              page?.parent === null && courant !== null
                ? "Dossiers explorables — remonter sortirait des dossiers explorables"
                : undefined
            }
            className={`inline-flex items-center gap-1 ${CLASSE_BOUTON_DOUX}`}
          >
            <IconeFlecheHaut className="size-3.5 shrink-0" />
            {page?.parent === null && courant !== null
              ? "Dossiers explorables"
              : "Remonter"}
          </button>
          <button
            type="button"
            onClick={() => courant !== null && onChoisir(courant)}
            disabled={chargement || courant === null}
            className="rounded-md bg-emerald-600 px-2.5 py-1 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-40"
          >
            Choisir ce dossier
          </button>
          <button
            type="button"
            onClick={onFermer}
            className={CLASSE_BOUTON_DOUX}
          >
            Fermer
          </button>
        </div>
      </div>

      {/* Les deux raccourcis vers un dossier lointain (#278). Le dialogue natif
          est un confort — il n'apparaît que là où il peut s'ouvrir —, la saisie
          d'un chemin est le repli qui marche partout, y compris en mode
          serveur : c'est l'API qui la vérifie, jamais le navigateur. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {selecteur?.disponible && (
          <button
            type="button"
            onClick={() => void parcourirNatif()}
            disabled={ouvertureNative || chargement}
            className={CLASSE_BOUTON_DOUX}
          >
            {ouvertureNative
              ? "Fenêtre ouverte sur votre poste…"
              : "Parcourir sur mon poste…"}
          </button>
        )}
        {/* Un `<div>`, et la soumission tenue à la main : voir l'en-tête du
            fichier — un `<form>` ici serait imbriqué dans celui du formulaire
            de projet (#312). */}
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <label className="sr-only" htmlFor="explorateur-chemin">
            Aller à un chemin absolu
          </label>
          <input
            id="explorateur-chemin"
            type="text"
            value={saisie}
            onChange={(evenement) => setSaisie(evenement.target.value)}
            onKeyDown={(evenement) => {
              if (evenement.key !== "Enter") return;
              // Coupé **même sur une saisie vide** : sans ce `preventDefault`,
              // la soumission implicite du navigateur remonterait au
              // formulaire porteur et déclarerait le projet.
              evenement.preventDefault();
              allerAuChemin();
            }}
            placeholder="Aller à un chemin absolu (ex. D:/depots)"
            className="min-w-0 flex-1 rounded-md border border-neutral-300 bg-white px-2 py-1 font-mono text-xs text-neutral-800 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
          />
          <button
            type="button"
            onClick={allerAuChemin}
            disabled={chargement || saisie.trim() === ""}
            className={CLASSE_BOUTON_DOUX + " shrink-0"}
          >
            Aller
          </button>
        </div>
      </div>

      {/* Le mode serveur (et tout autre empêchement) se **dit**, à la place du
          bouton : un bouton mort ferait croire à une panne, un silence ferait
          croire que la fonction n'existe pas. */}
      {selecteur && !selecteur.disponible && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          {selecteur.message}
        </p>
      )}

      {refus && (
        <div className="mt-3">
          <RefusMotive refus={refus} titre="Dossier non exploré" />
          <button
            type="button"
            onClick={() => void ouvrir(null)}
            className={CLASSE_BOUTON_DOUX + " mt-2"}
          >
            Revenir aux dossiers explorables
          </button>
        </div>
      )}

      <ul className="mt-3 flex max-h-72 flex-col gap-1 overflow-y-auto">
        {dossiers.map((dossier) => (
          <li
            key={dossier.chemin}
            className="flex items-center justify-between gap-2 rounded-md px-1 hover:bg-white dark:hover:bg-neutral-900"
          >
            {/* Deux gestes distincts sur la même ligne — entrer dans le
                dossier, ou le prendre pour racine —, donc deux noms
                accessibles explicites : sans eux, les pastilles feraient du
                nom du bouton d'ouverture un « depensio dépôt Git » que rien ne
                distingue du « Choisir depensio » voisin. */}
            <button
              type="button"
              onClick={() => void ouvrir(dossier.chemin)}
              disabled={chargement}
              aria-label={`Ouvrir ${dossier.nom}`}
              className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-corps disabled:opacity-50"
            >
              <IconeDossier className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
              <span className="truncate">{dossier.nom}</span>
              {dossier.origine !== null && (
                <span className="shrink-0 rounded-full border border-neutral-300 px-1.5 text-[10px] font-medium text-neutral-500 dark:border-neutral-700 dark:text-neutral-400">
                  {LIBELLE_ORIGINE[dossier.origine]}
                </span>
              )}
              {dossier.depot_git && (
                <BadgeEtat ton="info" contour className="shrink-0 text-micro">
                  dépôt Git
                </BadgeEtat>
              )}
              {dossier.projet_id !== null && (
                <BadgeEtat contour className="shrink-0 text-micro">
                  déjà déclaré
                </BadgeEtat>
              )}
            </button>
            <button
              type="button"
              onClick={() => onChoisir(dossier.chemin)}
              disabled={chargement || dossier.projet_id !== null}
              // Le motif du blocage est passé du `title` au nom accessible
              // (#536) : sur un bouton `disabled`, plusieurs navigateurs
              // n'affichent aucun `title` — il n'était donc lisible ni à la
              // souris, ni au clavier.
              aria-label={
                dossier.projet_id !== null
                  ? `Choisir ${dossier.nom} — indisponible : ce dossier est déjà la racine d'un projet`
                  : `Choisir ${dossier.nom}`
              }
              className={CLASSE_BOUTON_DOUX + " shrink-0"}
            >
              Choisir
            </button>
          </li>
        ))}
      </ul>

      {/* Les trois états de la liste sont distincts, à dessein : rien ici, pas
          encore lu, ou refus (au-dessus). Les afficher pareil ferait passer une
          frontière pour un dossier vide. */}
      {!chargement && refus === null && dossiers.length === 0 && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Aucun sous-dossier ici — « Choisir ce dossier » reste possible.
        </p>
      )}
      {chargement && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Lecture du dossier…
        </p>
      )}
      {page?.tronque && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Liste coupée : ce dossier contient plus de 500 sous-dossiers.
        </p>
      )}
    </section>
  );
}
