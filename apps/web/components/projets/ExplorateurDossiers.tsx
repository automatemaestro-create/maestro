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
 */

import { useCallback, useEffect, useState } from "react";

import { chargerExplorateur, ErreurProjet } from "@/lib/api";
import { conseilMotif } from "@/lib/projets";
import type { PageExplorateur, RefusProjet } from "@/lib/types";

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

  const ouvrir = useCallback(async (chemin: string | null) => {
    setChargement(true);
    try {
      setPage(await chargerExplorateur(chemin));
      setRefus(null);
    } catch (erreur) {
      // La page précédente reste affichée : un refus ne doit pas laisser
      // l'utilisateur devant un panneau vide dont il ne peut plus sortir.
      setRefus(refusDepuis(erreur));
    } finally {
      setChargement(false);
    }
  }, []);

  useEffect(() => {
    // Chargement différé d'un tick, comme partout dans le shell : l'effet
    // lui-même ne déclenche aucun setState synchrone.
    const tick = setTimeout(() => void ouvrir(cheminInitial), 0);
    return () => clearTimeout(tick);
  }, [ouvrir, cheminInitial]);

  const courant = page?.chemin ?? null;
  const dossiers = page?.dossiers ?? [];

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
            title={
              page?.parent === null && courant !== null
                ? "Remonter sortirait des dossiers explorables"
                : undefined
            }
            className={CLASSE_BOUTON_DOUX}
          >
            {page?.parent === null && courant !== null
              ? "↑ Dossiers explorables"
              : "↑ Remonter"}
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
              className="flex min-w-0 flex-1 items-center gap-2 py-1.5 text-left text-sm disabled:opacity-50"
            >
              <span aria-hidden="true">📁</span>
              <span className="truncate">{dossier.nom}</span>
              {dossier.depot_git && (
                <span className="shrink-0 rounded-full border border-sky-300 px-1.5 text-[10px] font-medium text-sky-700 dark:border-sky-800 dark:text-sky-400">
                  dépôt Git
                </span>
              )}
              {dossier.projet_id !== null && (
                <span className="shrink-0 rounded-full border border-neutral-300 px-1.5 text-[10px] font-medium text-neutral-500 dark:border-neutral-700 dark:text-neutral-400">
                  déjà déclaré
                </span>
              )}
            </button>
            <button
              type="button"
              onClick={() => onChoisir(dossier.chemin)}
              disabled={chargement || dossier.projet_id !== null}
              title={
                dossier.projet_id !== null
                  ? "Ce dossier est déjà la racine d'un projet"
                  : undefined
              }
              aria-label={`Choisir ${dossier.nom}`}
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
