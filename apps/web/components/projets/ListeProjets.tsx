"use client";

/**
 * L'écran Projets (#225, docs/05 §2.7) : déclarer où Maestro travaille, et
 * gérer ces déclarations. Branché sur les six routes de #223.
 *
 * Ce que la page tient en propre — et qui explique sa forme :
 *
 * - **la liste est l'état réel du disque**, pas un cache optimiste : chaque
 *   écriture est suivie d'un rechargement, parce que le backend canonicalise la
 *   racine et **constate** le VCS. Afficher ce qu'on a envoyé plutôt que ce qui
 *   a été enregistré ferait diverger l'écran du dossier ;
 * - **un refus est une réponse, pas une panne** (EF-38) : il s'affiche avec son
 *   motif à l'endroit du geste refusé — dans le formulaire, ou sur la carte du
 *   projet — et le reste de l'écran continue de fonctionner ;
 * - **la suppression s'arme en deux temps**, comme celle d'un agent (#72) :
 *   pas de boîte de dialogue, un second bouton qui dit ce qu'il fait. Elle
 *   n'efface que la déclaration, jamais le dossier.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeDossier, IconePlus } from "@/components/Icones";
import { BadgeEtat, Carte, EtatVide } from "@/components/Primitives";
import { chargerProjets, creerProjet, modifierProjet, supprimerProjet } from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import { libelleOrigine } from "@/lib/projets";
import type { DeclarationProjet, Projet, RefusProjet } from "@/lib/types";

import { refusDepuis, RefusMotive } from "./ExplorateurDossiers";
import { FormulaireProjet } from "./FormulaireProjet";

const CLASSE_BOUTON_SECONDAIRE =
  "rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 " +
  "hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 " +
  "dark:hover:bg-neutral-800";

/** Ce qu'un projet expose aux agents, en une ligne lisible. */
function Perimetre({ projet }: { projet: Projet }) {
  return (
    <p className="text-xs text-neutral-500 dark:text-neutral-400">
      Périmètre — inclus :{" "}
      <code className="font-mono">{projet.perimetre.inclus.join(", ")}</code>
      {projet.perimetre.exclus.length > 0 && (
        <>
          {" · exclus : "}
          <code className="font-mono">{projet.perimetre.exclus.join(", ")}</code>
        </>
      )}
    </p>
  );
}

function CarteProjet({
  projet,
  onModifier,
  onSupprime,
}: {
  projet: Projet;
  onModifier: () => void;
  onSupprime: () => Promise<void>;
}) {
  const [armee, setArmee] = useState(false);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);

  const supprimer = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      await supprimerProjet(projet.id);
      await onSupprime();
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
      setEnCours(false);
      setArmee(false);
    }
  };

  return (
    <Carte
      balise="li"
      densite="aeree"
      aria-label={`Projet ${projet.nom}`}
      className="flex flex-col gap-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-corps font-semibold">{projet.nom}</h3>
        <BadgeEtat contour>{libelleOrigine(projet.origine)}</BadgeEtat>
        {projet.vcs === null ? (
          <BadgeEtat contour>Non versionné</BadgeEtat>
        ) : (
          <BadgeEtat ton="info" contour>
            {projet.vcs.type}
            {projet.vcs.branche_base !== "" && ` · ${projet.vcs.branche_base}`}
          </BadgeEtat>
        )}
      </div>
      <code className="font-mono text-annexe break-all text-neutral-800 dark:text-neutral-200">
        {projet.racine}
      </code>
      <Perimetre projet={projet} />
      <p className="text-annexe text-neutral-400 dark:text-neutral-500">
        Déclaré le {formatDateHeure(projet.cree_le)} · modifié le{" "}
        {formatDateHeure(projet.modifie_le)}
      </p>
      {refus && <RefusMotive refus={refus} titre="Suppression refusée" />}
      <div className="mt-1 flex flex-wrap gap-2">
        <button
          type="button"
          onClick={onModifier}
          disabled={enCours}
          className={CLASSE_BOUTON_SECONDAIRE}
        >
          Modifier
        </button>
        {armee ? (
          <>
            <button
              type="button"
              onClick={() => void supprimer()}
              disabled={enCours}
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
            >
              {enCours ? "Suppression…" : "Confirmer la suppression"}
            </button>
            <button
              type="button"
              onClick={() => setArmee(false)}
              disabled={enCours}
              className={CLASSE_BOUTON_SECONDAIRE}
            >
              Garder le projet
            </button>
            <span className="self-center text-xs text-neutral-500 dark:text-neutral-400">
              Seule la déclaration part : le dossier reste sur le disque.
            </span>
          </>
        ) : (
          <button
            type="button"
            onClick={() => setArmee(true)}
            disabled={enCours}
            className="rounded-md border border-rose-300 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900 dark:text-rose-400 dark:hover:bg-rose-950"
          >
            Supprimer
          </button>
        )}
      </div>
    </Carte>
  );
}

export function ListeProjets() {
  const [projets, setProjets] = useState<Projet[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [creationOuverte, setCreationOuverte] = useState(false);
  const [editionId, setEditionId] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    try {
      setProjets(await chargerProjets());
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, []);

  useEffect(() => {
    // Chargement différé d'un tick (même mécanique que le shell) : l'effet
    // lui-même ne déclenche aucun setState synchrone.
    const tick = setTimeout(() => void recharger(), 0);
    return () => clearTimeout(tick);
  }, [recharger]);

  const declarer = async (declaration: DeclarationProjet) => {
    await creerProjet(declaration);
    setCreationOuverte(false);
    await recharger();
  };

  const modifier = async (id: string, declaration: DeclarationProjet) => {
    await modifierProjet(id, declaration);
    setEditionId(null);
    await recharger();
  };

  return (
    <>
      {/* Le bandeau d'abord : une API injoignable se lit avant la liste vide
          qu'elle explique. */}
      <BanniereErreurApi erreur={erreur} />

      <section aria-label="Projets déclarés" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <p className="max-w-2xl text-sm text-neutral-500 dark:text-neutral-400">
            Un projet, c&apos;est une <strong>racine sur le disque</strong> et ce
            qu&apos;elle expose aux agents. Le dossier se choisit dans
            l&apos;explorateur servi par le backend — jamais en tapant un chemin.
          </p>
          {!creationOuverte && (
            <button
              type="button"
              onClick={() => {
                setCreationOuverte(true);
                setEditionId(null);
              }}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-emerald-600 px-3 py-1.5 text-annexe font-medium text-white hover:bg-emerald-700"
            >
              <IconePlus className="size-3.5 shrink-0" />
              Nouveau projet
            </button>
          )}
        </div>

        {creationOuverte && (
          <FormulaireProjet
            enregistrer={declarer}
            onAnnuler={() => setCreationOuverte(false)}
          />
        )}

        {chargement && (
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            Chargement des projets…
          </p>
        )}
        {/* « Aucun projet » n'est dit que si la liste a vraiment été lue :
            l'afficher sur une API injoignable ferait passer une panne pour un
            backlog vide, et inviterait à re-déclarer des projets déjà là. */}
        {!chargement && erreur === null && projets.length === 0 && (
          <EtatVide
            icone={IconeDossier}
            message="Aucun projet déclaré. Les exécutions travaillent alors dans un espace jetable et leurs livrables restent dans le dossier de sortie — déclarer un projet, c'est leur donner une adresse."
          />
        )}
        {!chargement && projets.length > 0 && (
          <ul className="flex flex-col gap-3">
            {projets.map((projet) =>
              editionId === projet.id ? (
                <li key={projet.id}>
                  <FormulaireProjet
                    projet={projet}
                    enregistrer={(declaration) =>
                      modifier(projet.id, declaration)
                    }
                    onAnnuler={() => setEditionId(null)}
                  />
                </li>
              ) : (
                <CarteProjet
                  key={projet.id}
                  projet={projet}
                  onModifier={() => {
                    setEditionId(projet.id);
                    setCreationOuverte(false);
                  }}
                  onSupprime={recharger}
                />
              ),
            )}
          </ul>
        )}
      </section>
    </>
  );
}
