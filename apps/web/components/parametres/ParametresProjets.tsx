"use client";

/**
 * Section « Projets » des Paramètres (#1022) : **le répertoire où naît un projet
 * neuf**.
 *
 * Un réglage du **poste**, et c'est pourquoi il est ici et non sur l'écran
 * Projets : il ne dit rien d'un projet déclaré — il dit dans quel dossier le
 * prochain sera créé. L'écran Projets, lui, reste la liste de ce qui est
 * déclaré (docs/05 §2.7.1) ; le réglage ne s'y range pas plus que l'URL de
 * l'API ne se range dans la conversation.
 *
 * Deux partis pris, repris de ceux que la veille de #1022 a rendus :
 *
 * 1. **Le chemin ne se tape pas** (#225). Le réglage se choisit dans le même
 *    explorateur que la racine d'un projet — `ExplorateurDossiers`, qui porte
 *    déjà le dialogue natif du poste (#278) —, jamais dans un champ texte. Il
 *    n'y a donc pas de « valider » : choisir **est** l'écriture, et le bouton
 *    qui reste est celui qui **revient au défaut**.
 * 2. **Le défaut porte un nom.** `par_defaut` ne se lit pas comme « aucun
 *    répertoire » mais comme « celui que Maestro propose », et la ligne le dit
 *    — c'est ce qui distingue « je n'ai rien réglé » de « j'ai réglé ceci ».
 *
 * Un échec de lecture n'est pas une panne de la page : la section dit ce qui
 * manque et laisse les autres réglages intacts.
 */

import { useCallback, useEffect, useState } from "react";

import { Bouton } from "@/components/Primitives";
import {
  ExplorateurDossiers,
  refusDepuis,
  RefusMotive,
} from "@/components/projets/ExplorateurDossiers";
import { chargerRepertoireProjets, reglerRepertoireProjets } from "@/lib/api";
import type { RefusProjet, RepertoireProjets } from "@/lib/types";

import { LigneReglage } from "./SectionParametres";

export function ParametresProjets() {
  const [repertoire, setRepertoire] = useState<RepertoireProjets | null>(null);
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);

  useEffect(() => {
    let vivant = true;
    void chargerRepertoireProjets()
      .then((lu) => {
        if (vivant) setRepertoire(lu);
      })
      .catch((erreur) => {
        if (vivant) setRefus(refusDepuis(erreur));
      });
    return () => {
      vivant = false;
    };
  }, []);

  const regler = useCallback(async (chemin: string | null) => {
    setEnCours(true);
    setRefus(null);
    try {
      setRepertoire(await reglerRepertoireProjets(chemin));
      setExplorateurOuvert(false);
    } catch (erreur) {
      // Le réglage précédent reste en place : la route n'écrit rien quand elle
      // refuse. L'écran montre le motif plutôt que de laisser croire au succès.
      setRefus(refusDepuis(erreur));
    } finally {
      setEnCours(false);
    }
  }, []);

  return (
    <div className="flex flex-col">
      <LigneReglage
        libelle="Répertoire des projets"
        aide={
          repertoire?.par_defaut === true
            ? "Le dossier où naît un projet neuf — celui que Maestro propose, créé à la première utilisation. Le dossier parent d'une déclaration s'en remplit d'office, et s'y remplace sans rien régler."
            : "Le dossier où naît un projet neuf. Le dossier parent d'une déclaration s'en remplit d'office, et s'y remplace sans rien régler."
        }
      >
        <div className="flex flex-wrap items-center justify-end gap-2">
          <code className="font-mono text-corps break-all">
            {repertoire === null ? "…" : repertoire.chemin}
          </code>
          <Bouton
            variante="contour"
            ton="neutre"
            onClick={() => setExplorateurOuvert(!explorateurOuvert)}
            disabled={enCours || repertoire === null}
            aria-expanded={explorateurOuvert}
          >
            Changer de dossier…
          </Bouton>
          {repertoire !== null && !repertoire.par_defaut && (
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => void regler(null)}
              disabled={enCours}
            >
              Revenir au dossier proposé
            </Bouton>
          )}
        </div>
      </LigneReglage>

      {repertoire !== null && repertoire.refus !== null && (
        <RefusMotive
          refus={repertoire.refus}
          titre="Répertoire indisponible"
        />
      )}
      {refus && <RefusMotive refus={refus} titre="Réglage refusé" />}

      {explorateurOuvert && (
        <div className="pt-3">
          <ExplorateurDossiers
            cheminInitial={repertoire?.chemin ?? null}
            onChoisir={(chemin) => void regler(chemin)}
            onFermer={() => setExplorateurOuvert(false)}
          />
        </div>
      )}
    </div>
  );
}
