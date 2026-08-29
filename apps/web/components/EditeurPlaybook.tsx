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
 *
 * L'historique porte aussi les **propositions** d'auto-amélioration en attente
 * (#111/#140) : des brouillons suggérés à partir des échecs d'un run, affichés
 * à part des versions humaines, avec leur justification. Ils ne sont jamais
 * chargés par le moteur tant qu'on ne les a pas appliqués au clic (le contenu
 * devient alors la version courante, chargée à chaud #78) ; un rejet les retire
 * sans toucher à la version courante.
 */

import { useCallback, useEffect, useState } from "react";

import { IconeHistorique, IconePlaybooks } from "@/components/Icones";
import { BadgeEtat, Bouton, EnTeteSection } from "@/components/Primitives";
import {
  appliquerPropositionPlaybook,
  chargerPlaybook,
  chargerPropositionPlaybook,
  chargerPropositionsPlaybook,
  chargerVersionPlaybook,
  chargerVersionsPlaybook,
  ecrirePlaybook,
  rejeterPropositionPlaybook,
  restaurerPlaybook,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type PlaybookDetail,
  type PropositionPlaybook,
  type VersionPlaybook,
} from "@/lib/types";

export function EditeurPlaybook({
  agent,
  onPublication,
}: {
  agent: string;
  /**
   * Prévenir la page qu'une version a été publiée. Facultatif depuis #190 :
   * l'onglet Playbook d'une fiche agent n'a pas de liste à rafraîchir, l'éditeur
   * se resynchronisant déjà tout seul.
   */
  onPublication?: () => void | Promise<void>;
}) {
  const [fiche, setFiche] = useState<PlaybookDetail | null>(null);
  const [versions, setVersions] = useState<VersionPlaybook[]>([]);
  const [propositions, setPropositions] = useState<PropositionPlaybook[]>([]);
  const [contenu, setContenu] = useState("");
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    const [nouvelleFiche, nouvellesVersions, nouvellesPropositions] =
      await Promise.all([
        chargerPlaybook(agent),
        chargerVersionsPlaybook(agent),
        chargerPropositionsPlaybook(agent),
      ]);
    setFiche(nouvelleFiche);
    setVersions(nouvellesVersions);
    setPropositions(nouvellesPropositions);
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

  // Publication, restauration et application d'une proposition partagent la même
  // mécanique : l'action, puis rechargement (fiche + historique + propositions)
  // et resynchronisation de l'éditeur sur la nouvelle version courante.
  // `resynchroniser: false` pour le rejet, qui ne change pas la version courante :
  // l'éditeur garde alors les modifications en cours de l'utilisateur.
  const executer = async (
    action: () => Promise<void>,
    { resynchroniser = true }: { resynchroniser?: boolean } = {},
  ) => {
    setEnCours(true);
    setErreur(null);
    try {
      await action();
      const nouvelleFiche = await recharger();
      if (resynchroniser) setContenu(nouvelleFiche.contenu);
      await onPublication?.();
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
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
        <EnTeteSection
          niveau={3}
          icone={IconePlaybooks}
          titre={`Playbook${fiche.role ? ` · ${fiche.role}` : ""}`}
          className="mb-2"
          aside={
            <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
              {/* « version d'origine » et non « du code » depuis #259 :
                  l'onglet sert aussi les agents personnalisés, dont l'origine
                  est le playbook de leur définition et non un document du
                  dépôt — le mot doit valoir pour les deux. */}
              {jamaisEdite
                ? "version d’origine (jamais éditée)"
                : `version ${fiche.version}` +
                  (fiche.cree_le ? ` · ${formatDateHeure(fiche.cree_le)}` : "")}
            </span>
          }
        />
        <textarea
          value={contenu}
          onChange={(e) => setContenu(e.target.value)}
          disabled={enCours}
          aria-label="Contenu du playbook"
          spellCheck={false}
          className="h-96 w-full resize-y rounded-md border border-neutral-200 bg-white p-3 font-mono text-xs leading-relaxed shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:focus:border-neutral-600"
        />
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <Bouton
            disabled={!modifie || !contenu.trim()}
            occupe={enCours}
            onClick={() => void executer(() => ecrirePlaybook(agent, contenu))}
          >
            {enCours
              ? "Envoi…"
              : `Publier la version ${fiche.version + 1}`}
          </Bouton>
          {modifie && !enCours && (
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => setContenu(fiche.contenu)}
            >
              Annuler les modifications
            </Bouton>
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
        <EnTeteSection
          niveau={3}
          icone={IconeHistorique}
          className="mb-2"
          titre={
            <>
              Historique
              <BadgeEtat className="chiffre">{versions.length}</BadgeEtat>
              {propositions.length > 0 && (
                <BadgeEtat ton="accent" className="chiffre normal-case">
                  {propositions.length} en attente
                </BadgeEtat>
              )}
            </>
          }
        />
        {versions.length === 0 && (
          <p className="mb-1 text-sm text-neutral-500">
            Aucune version publiée : l&apos;agent suit encore son playbook
            d&apos;origine.
          </p>
        )}
        {(propositions.length > 0 || versions.length > 0) && (
          <ul className="flex flex-col gap-1">
            {/* Les propositions d'abord : elles attendent une décision. */}
            {[...propositions].reverse().map((proposition) => (
              <LigneProposition
                key={`p${proposition.version}`}
                agent={agent}
                proposition={proposition}
                enCours={enCours}
                appliquer={(numero) =>
                  void executer(() => appliquerPropositionPlaybook(agent, numero))
                }
                rejeter={(numero) =>
                  void executer(
                    () => rejeterPropositionPlaybook(agent, numero),
                    { resynchroniser: false },
                  )
                }
              />
            ))}
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

/**
 * Une proposition en attente : visuellement distincte des versions (cadre violet,
 * étiquette de provenance, justification en clair), et tranchée au clic —
 * appliquer (elle devient la version courante) ou rejeter (elle disparaît).
 */
function LigneProposition({
  agent,
  proposition,
  enCours,
  appliquer,
  rejeter,
}: {
  agent: string;
  proposition: PropositionPlaybook;
  enCours: boolean;
  appliquer: (numero: number) => void;
  rejeter: (numero: number) => void;
}) {
  const [contenu, setContenu] = useState<string | null>(null);
  const [ouverte, setOuverte] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Comme pour une version, le contenu candidat se charge à la première
  // consultation (la liste des propositions ne porte que les métadonnées).
  const basculer = async () => {
    if (!ouverte && contenu === null) {
      try {
        setContenu(
          (await chargerPropositionPlaybook(agent, proposition.version)).contenu,
        );
      } catch (e) {
        setErreur(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setErreur(null);
    setOuverte(!ouverte);
  };

  return (
    <li className="rounded-md border border-violet-300 bg-violet-50 px-3 py-2 text-sm shadow-sm dark:border-violet-900 dark:bg-violet-950">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-xs font-medium">
          p{proposition.version}
        </span>
        <span className="rounded-full bg-violet-200 px-2 text-xs text-violet-900 dark:bg-violet-900 dark:text-violet-200">
          {proposition.provenance}
        </span>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {formatDateHeure(proposition.cree_le)}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => void basculer()}
            className="rounded-md border border-violet-300 px-2 py-1 text-xs text-violet-800 hover:bg-violet-100 dark:border-violet-800 dark:text-violet-300 dark:hover:bg-violet-900"
          >
            {ouverte ? "Masquer" : "Voir"}
          </button>
          <Bouton
            taille="petite"
            disabled={enCours}
            onClick={() => appliquer(proposition.version)}
          >
            Appliquer
          </Bouton>
          <Bouton
            variante="contour"
            ton="alerte"
            taille="petite"
            disabled={enCours}
            onClick={() => rejeter(proposition.version)}
          >
            Rejeter
          </Bouton>
        </div>
      </div>
      {proposition.justification && (
        <p className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-violet-900 dark:text-violet-200">
          {proposition.justification}
        </p>
      )}
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
      {ouverte && contenu !== null && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-white p-2 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {contenu}
        </pre>
      )}
    </li>
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
