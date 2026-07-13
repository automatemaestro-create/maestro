"use client";

/**
 * L'éditeur du playbook d'un agent (ticket #77) : le contenu courant en
 * édition libre, publié comme nouvelle version (`PUT /api/playbooks/{agent}`),
 * et l'historique des versions consultable avec restauration d'une version
 * antérieure (`POST /api/playbooks/{agent}/restaurer`, EF-24/EF-25).
 *
 * Le dépôt est append-only (#76) : publier comme restaurer créent une version
 * de plus, rien n'est jamais réécrit — la restauration est donc toujours
 * réversible, aucune confirmation n'est demandée.
 */

import { useCallback, useEffect, useState } from "react";

import {
  chargerPlaybook,
  chargerVersionPlaybook,
  chargerVersionsPlaybook,
  ecrirePlaybook,
  restaurerPlaybook,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type PlaybookDetail,
  type VersionPlaybook,
} from "@/lib/types";

export function EditeurPlaybook({
  agent,
  onPublication,
}: {
  agent: string;
  /** Prévenir la page qu'une version a été publiée (la liste des fiches change). */
  onPublication: () => void | Promise<void>;
}) {
  const [fiche, setFiche] = useState<PlaybookDetail | null>(null);
  const [versions, setVersions] = useState<VersionPlaybook[]>([]);
  const [contenu, setContenu] = useState("");
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    const [nouvelleFiche, nouvellesVersions] = await Promise.all([
      chargerPlaybook(agent),
      chargerVersionsPlaybook(agent),
    ]);
    setFiche(nouvelleFiche);
    setVersions(nouvellesVersions);
    return nouvelleFiche;
  }, [agent]);

  // Chargement différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    let abandonne = false;
    const tick = setTimeout(() => {
      setChargement(true);
      setErreur(null);
      recharger()
        .then((nouvelleFiche) => {
          if (!abandonne) setContenu(nouvelleFiche.contenu);
        })
        .catch((e) => {
          if (!abandonne) setErreur(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!abandonne) setChargement(false);
        });
    }, 0);
    return () => {
      abandonne = true;
      clearTimeout(tick);
    };
  }, [recharger]);

  // La publication et la restauration partagent la même mécanique : l'action,
  // puis rechargement (fiche + historique) et resynchronisation de l'éditeur
  // sur la nouvelle version courante.
  const executer = async (action: () => Promise<void>) => {
    setEnCours(true);
    setErreur(null);
    try {
      await action();
      const nouvelleFiche = await recharger();
      setContenu(nouvelleFiche.contenu);
      await onPublication();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement du playbook…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-sm text-rose-600 dark:text-rose-400" role="alert">
        Playbook illisible : {erreur}
      </p>
    );
  }

  const modifie = contenu !== fiche.contenu;
  const jamaisEdite = fiche.source === PLAYBOOK_SOURCE_DEFAUT;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Playbook de ${agent}`}>
        <div className="mb-2 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            📖 Playbook · {agent}
            {fiche.role ? ` (${fiche.role})` : ""}
          </h2>
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {jamaisEdite
              ? "version du code (jamais édité)"
              : `version ${fiche.version}` +
                (fiche.cree_le ? ` · ${formatDateHeure(fiche.cree_le)}` : "")}
          </span>
        </div>
        <textarea
          value={contenu}
          onChange={(e) => setContenu(e.target.value)}
          disabled={enCours}
          aria-label="Contenu du playbook"
          spellCheck={false}
          className="h-96 w-full resize-y rounded-md border border-neutral-200 bg-white p-3 font-mono text-xs leading-relaxed shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:focus:border-neutral-600"
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <button
            type="button"
            disabled={enCours || !modifie || !contenu.trim()}
            onClick={() => void executer(() => ecrirePlaybook(agent, contenu))}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {enCours
              ? "Envoi…"
              : `Publier la version ${fiche.version + 1}`}
          </button>
          {modifie && !enCours && (
            <button
              type="button"
              onClick={() => setContenu(fiche.contenu)}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
            >
              Annuler les modifications
            </button>
          )}
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {modifie
              ? "Modifications non publiées."
              : "Une publication crée une nouvelle version ; les moteurs construits ensuite la chargent."}
          </span>
        </div>
        {erreur && (
          <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
            {erreur}
          </p>
        )}
      </section>

      <section aria-label={`Historique du playbook de ${agent}`}>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
          🕘 Historique
          <span className="ml-2 rounded-full bg-neutral-200 px-2 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            {versions.length}
          </span>
        </h3>
        {versions.length === 0 ? (
          <p className="text-sm text-neutral-500">
            Aucune version publiée : l&apos;agent suit encore le playbook du code.
          </p>
        ) : (
          <ul className="flex flex-col gap-1">
            {[...versions].reverse().map((version) => (
              <LigneVersion
                key={version.version}
                agent={agent}
                version={version}
                courante={version.version === fiche.version}
                enCours={enCours}
                restaurer={(numero) =>
                  void executer(() => restaurerPlaybook(agent, numero))
                }
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function LigneVersion({
  agent,
  version,
  courante,
  enCours,
  restaurer,
}: {
  agent: string;
  version: VersionPlaybook;
  /** La version courante ne se restaure pas : elle est déjà en vigueur. */
  courante: boolean;
  enCours: boolean;
  restaurer: (version: number) => void;
}) {
  const [contenu, setContenu] = useState<string | null>(null);
  const [ouverte, setOuverte] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Le contenu d'une version passée se charge à la première consultation
  // (l'historique REST ne porte que les métadonnées).
  const basculer = async () => {
    if (!ouverte && contenu === null) {
      try {
        setContenu((await chargerVersionPlaybook(agent, version.version)).contenu);
      } catch (e) {
        setErreur(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setErreur(null);
    setOuverte(!ouverte);
  };

  return (
    <li className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-xs font-medium">v{version.version}</span>
        {courante && (
          <span className="rounded-full bg-emerald-100 px-2 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
            courante
          </span>
        )}
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {formatDateHeure(version.cree_le)}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => void basculer()}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
          >
            {ouverte ? "Masquer" : "Voir"}
          </button>
          {!courante && (
            <button
              type="button"
              disabled={enCours}
              onClick={() => restaurer(version.version)}
              className="rounded-md border border-amber-300 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-800 dark:text-amber-400 dark:hover:bg-amber-950"
            >
              Restaurer
            </button>
          )}
        </div>
      </div>
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
      {ouverte && contenu !== null && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {contenu}
        </pre>
      )}
    </li>
  );
}
