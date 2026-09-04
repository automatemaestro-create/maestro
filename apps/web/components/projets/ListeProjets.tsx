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
 *   n'efface que la déclaration, jamais le dossier ;
 * - **la mise sous Git s'arme de la même façon** (#855) — sur un projet « Non
 *   versionné » seulement, et derrière une confirmation qui **dit ce qui va
 *   être fait** : `git init` puis un premier commit de toute la racine, le
 *   `.gitignore` du projet respecté. C'est le seul geste de l'écran qui écrive
 *   dans le dossier de l'utilisateur, et c'est ce qui fait passer le projet du
 *   régime « écriture en place » au régime « worktree + fusion sous accord »
 *   (docs/24 §2.4) — d'où une confirmation, là où déclarer ou modifier n'en
 *   demandent pas. Le `vcs` n'est pas envoyé : il est **constaté** au retour
 *   (EF-38), et la liste se relit comme après toute écriture.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeDossier, IconePlus } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte, EtatVide } from "@/components/Primitives";
import {
  chargerProjets,
  creerProjet,
  modifierProjet,
  supprimerProjet,
  versionnerProjet,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import { libelleOrigine } from "@/lib/projets";
import type { DeclarationProjet, Projet, RefusProjet } from "@/lib/types";

import { refusDepuis, RefusMotive } from "./ExplorateurDossiers";
import { FormulaireProjet } from "./FormulaireProjet";

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

/**
 * Les deux gestes de la carte qui s'arment en deux temps. Un seul à la fois :
 * armer l'un désarme l'autre, sans quoi la carte porterait deux confirmations
 * qui ne parlent pas de la même chose.
 */
type GesteArme = "supprimer" | "versionner";

/** Un refus affiché sur la carte, sous le titre du geste qu'il a refusé. */
type RefusCarte = { titre: string; detail: RefusProjet };

function CarteProjet({
  projet,
  onModifier,
  onSupprime,
  onVersionne,
}: {
  projet: Projet;
  onModifier: () => void;
  onSupprime: () => Promise<void>;
  onVersionne: () => Promise<void>;
}) {
  const [geste, setGeste] = useState<GesteArme | null>(null);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusCarte | null>(null);

  /** Joue un geste armé : la carte se fige, un refus revient à son titre. */
  const jouer = async (titreRefus: string, action: () => Promise<void>) => {
    setEnCours(true);
    setRefus(null);
    try {
      await action();
    } catch (erreur) {
      setRefus({ titre: titreRefus, detail: refusDepuis(erreur) });
      setEnCours(false);
      setGeste(null);
    }
  };

  const supprimer = () =>
    jouer("Suppression refusée", async () => {
      await supprimerProjet(projet.id);
      await onSupprime();
    });

  // Le `vcs` rendu par la route n'est pas gardé : la liste se relit, comme
  // après toute écriture — c'est elle qui montre « git · <branche> ».
  const versionner = () =>
    jouer("Mise sous Git refusée", async () => {
      await versionnerProjet(projet.id);
      await onVersionne();
    });

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
      {refus && <RefusMotive refus={refus.detail} titre={refus.titre} />}
      {geste === "versionner" ? (
        <Carte
          balise="div"
          ton="attention"
          densite="compacte"
          role="group"
          aria-label="Confirmer la mise sous Git"
          className="mt-1 flex flex-col gap-2 text-xs text-neutral-800 dark:text-neutral-200"
        >
          <p>
            <strong>Ce qui va être fait :</strong>{" "}
            <code className="font-mono">git init</code> dans{" "}
            <code className="font-mono break-all">{projet.racine}</code>, puis
            un premier commit « Maestro : état initial du projet » qui enregistre{" "}
            <strong>toute la racine</strong> telle qu&apos;elle est — le{" "}
            <code className="font-mono">.gitignore</code> du projet est
            respecté, rien d&apos;autre n&apos;est écrit. Dès la tâche suivante,
            les agents travailleront dans un espace dérivé de ce dépôt et la
            fusion de leur travail demandera votre accord.
          </p>
          <div className="flex flex-wrap gap-2">
            <Bouton ton="info" occupe={enCours} onClick={() => void versionner()}>
              {enCours ? "Mise sous Git…" : "Confirmer la mise sous Git"}
            </Bouton>
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => setGeste(null)}
              disabled={enCours}
            >
              Garder non versionné
            </Bouton>
          </div>
        </Carte>
      ) : (
        <div className="mt-1 flex flex-wrap gap-2">
          <Bouton
            variante="contour"
            ton="neutre"
            onClick={onModifier}
            disabled={enCours}
          >
            Modifier
          </Bouton>
          {projet.vcs === null && geste === null && (
            <Bouton
              variante="contour"
              ton="info"
              onClick={() => setGeste("versionner")}
              disabled={enCours}
            >
              Mettre sous Git
            </Bouton>
          )}
          {geste === "supprimer" ? (
            <>
              <Bouton
                ton="alerte"
                occupe={enCours}
                onClick={() => void supprimer()}
              >
                {enCours ? "Suppression…" : "Confirmer la suppression"}
              </Bouton>
              <Bouton
                variante="contour"
                ton="neutre"
                onClick={() => setGeste(null)}
                disabled={enCours}
              >
                Garder le projet
              </Bouton>
              <span className="self-center text-xs text-neutral-500 dark:text-neutral-400">
                Seule la déclaration part : le dossier reste sur le disque.
              </span>
            </>
          ) : (
            <Bouton
              variante="contour"
              ton="alerte"
              onClick={() => setGeste("supprimer")}
              disabled={enCours}
            >
              Supprimer
            </Bouton>
          )}
        </div>
      )}
    </Carte>
  );
}

type Props = {
  /**
   * Appelé après **chaque écriture** (déclaration, modification, suppression,
   * mise sous Git) — l'écran ne connaît toujours pas le projet actif, il
   * signale seulement que la liste réelle a bougé.
   *
   * C'est ce qui rend l'écran atteignable depuis le sélecteur (#280) sans
   * régression : supprimer la racine sur laquelle la Control Tower est ouverte
   * doit ramener à la porte d'entrée avec son motif (#279), là où sans ce
   * signal le shell resterait le cadre d'un projet qui n'existe plus. Le rappel
   * est **optionnel** pour que l'écran continue de se tester seul, hors de tout
   * fournisseur.
   */
  apresEcriture?: () => void;
};

export function ListeProjets({ apresEcriture }: Props = {}) {
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

  // La relecture d'après écriture, distincte de celle du montage : seule
  // celle-ci porte un changement, et donc seule elle a quelqu'un à prévenir.
  const rechargerApresEcriture = useCallback(async () => {
    await recharger();
    apresEcriture?.();
  }, [recharger, apresEcriture]);

  const declarer = async (declaration: DeclarationProjet) => {
    await creerProjet(declaration);
    setCreationOuverte(false);
    await rechargerApresEcriture();
  };

  const modifier = async (id: string, declaration: DeclarationProjet) => {
    await modifierProjet(id, declaration);
    setEditionId(null);
    await rechargerApresEcriture();
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
            <Bouton
              icone={IconePlus}
              onClick={() => {
                setCreationOuverte(true);
                setEditionId(null);
              }}
            >
              Nouveau projet
            </Bouton>
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
                  onSupprime={rechargerApresEcriture}
                  onVersionne={rechargerApresEcriture}
                />
              ),
            )}
          </ul>
        )}
      </section>
    </>
  );
}
