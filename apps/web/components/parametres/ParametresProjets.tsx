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
 *
 * Le second réglage de la section : **les dossiers autorisés** depuis
 * l'explorateur (#1610), quand il refusait un dossier hors des dossiers
 * explorables. Ils se relisent ici, la portée dite une fois en tête, et se
 * retirent ligne à ligne — la liste des dossiers de confiance de VS Code, celle
 * des partages de Docker Desktop (veille du ticket).
 */

import { useCallback, useEffect, useState } from "react";

import { Bouton } from "@/components/Primitives";
import {
  ExplorateurDossiers,
  refusDepuis,
  RefusMotive,
} from "@/components/projets/ExplorateurDossiers";
import {
  chargerDossiersAutorises,
  chargerRepertoireProjets,
  reglerRepertoireProjets,
  retirerDossierAutorise,
} from "@/lib/api";
import { libelleMotif } from "@/lib/projets";
import type {
  DossierAutorise,
  RefusProjet,
  RepertoireProjets,
} from "@/lib/types";

import { LigneReglage } from "./SectionParametres";

/**
 * Les dossiers autorisés depuis l'explorateur, et leur retrait (#1610).
 *
 * `version` est incrémentée par le parent quand l'explorateur de la section
 * vient d'en autoriser un : la liste se relit, au lieu d'attendre un
 * rechargement de la page.
 */
function DossiersAutorises({ version }: { version: number }) {
  const [dossiers, setDossiers] = useState<DossierAutorise[] | null>(null);
  const [echecLecture, setEchecLecture] = useState<RefusProjet | null>(null);
  const [refusRetrait, setRefusRetrait] = useState<RefusProjet | null>(null);
  const [retraitEnCours, setRetraitEnCours] = useState<string | null>(null);

  useEffect(() => {
    let vivant = true;
    void chargerDossiersAutorises()
      .then((lu) => {
        if (!vivant) return;
        setDossiers(lu.dossiers);
        setEchecLecture(null);
      })
      .catch((erreur) => {
        if (vivant) setEchecLecture(refusDepuis(erreur));
      });
    return () => {
      vivant = false;
    };
  }, [version]);

  const retirer = useCallback(async (chemin: string) => {
    setRetraitEnCours(chemin);
    setRefusRetrait(null);
    try {
      setDossiers((await retirerDossierAutorise(chemin)).dossiers);
    } catch (erreur) {
      // L'autorisation reste en place : la route n'écrit rien quand elle refuse.
      setRefusRetrait(refusDepuis(erreur));
    } finally {
      setRetraitEnCours(null);
    }
  }, []);

  return (
    <>
      <LigneReglage
        libelle="Dossiers autorisés"
        // La portée, dite **une fois** et invariante — comme l'aide du
        // répertoire : elle décrit le réglage, pas son état du moment.
        aide="Autorisés depuis l'explorateur quand il les refusait : Maestro peut parcourir ces dossiers et leurs sous-dossiers sur ce poste. Les zones protégées y restent refusées."
      >
        {dossiers === null ? (
          <p className="text-corps text-texte-secondaire">
            {echecLecture !== null ? "—" : "…"}
          </p>
        ) : dossiers.length === 0 ? (
          <p className="text-annexe text-texte-secondaire">
            Aucun dossier autorisé.
          </p>
        ) : (
          <ul className="flex flex-col items-end gap-2">
            {dossiers.map((dossier) => (
              <li
                key={dossier.chemin}
                className="flex flex-wrap items-center justify-end gap-2"
              >
                <code className="font-mono text-corps break-all">
                  {dossier.chemin}
                </code>
                {/* Une autorisation qui ne sert plus (disque débranché,
                    dossier supprimé) se dit, et se retire comme les autres. */}
                {dossier.refus !== null && (
                  <span className="text-annexe text-attention-texte">
                    inutilisable — {libelleMotif(dossier.refus.motif)}
                  </span>
                )}
                <Bouton
                  variante="contour"
                  ton="neutre"
                  taille="petite"
                  occupe={retraitEnCours === dossier.chemin}
                  disabled={retraitEnCours !== null}
                  aria-label={`Retirer ${dossier.chemin}`}
                  onClick={() => void retirer(dossier.chemin)}
                >
                  Retirer
                </Bouton>
              </li>
            ))}
          </ul>
        )}
      </LigneReglage>
      {echecLecture && (
        <RefusMotive refus={echecLecture} titre="Dossiers autorisés illisibles" />
      )}
      {refusRetrait && (
        <RefusMotive refus={refusRetrait} titre="Retrait refusé" />
      )}
    </>
  );
}

export function ParametresProjets() {
  const [repertoire, setRepertoire] = useState<RepertoireProjets | null>(null);
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const [enCours, setEnCours] = useState(false);
  // Deux échecs, deux états — et c'est un constat de la relecture visuelle : les
  // confondre faisait dire « Réglage refusé » à une **lecture** en panne, c'est-à-dire
  // à quelqu'un qui n'avait rien réglé.
  const [echecLecture, setEchecLecture] = useState<RefusProjet | null>(null);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [versionAutorises, setVersionAutorises] = useState(0);

  useEffect(() => {
    let vivant = true;
    void chargerRepertoireProjets()
      .then((lu) => {
        if (vivant) setRepertoire(lu);
      })
      .catch((erreur) => {
        if (vivant) setEchecLecture(refusDepuis(erreur));
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
        // Une phrase, **invariante** : elle décrit le réglage, pas son état du
        // moment. La faire changer selon que la valeur est le défaut ou non
        // donnait deux libellés pour un même réglage — et, quand la lecture
        // échouait, la description changeait sans que rien n'ait changé
        // (constat de la relecture visuelle). Ce que le défaut a de particulier
        // se dit là où c'est actionnable : le bouton « Revenir au dossier
        // proposé », qui n'apparaît que lorsqu'il y a quelque chose à défaire.
        aide="Le dossier parent d'un projet neuf en est rempli d'office. En choisir un autre pour un projet ne change pas ce réglage."
      >
        <div className="flex flex-wrap items-center justify-end gap-2">
          {/* « … » dit « ça arrive », et il ne doit donc pas rester à l'écran
              quand plus rien n'arrive : une lecture en panne rend « — », le
              tiret de l'inconnu qu'emploie déjà la dépense cumulée. */}
          <code className="font-mono text-corps break-all">
            {repertoire !== null
              ? repertoire.chemin
              : echecLecture !== null
                ? "—"
                : "…"}
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

      {echecLecture && (
        <RefusMotive refus={echecLecture} titre="Répertoire illisible" />
      )}
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
            onAutorise={() => setVersionAutorises((v) => v + 1)}
          />
        </div>
      )}

      <DossiersAutorises version={versionAutorises} />
    </div>
  );
}
