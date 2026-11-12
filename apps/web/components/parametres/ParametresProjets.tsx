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
 *
 * La même section est l'**étape du dossier** du premier lancement (#642) : montée
 * avec ses gestes et ses dossiers autorisés, elle dit chaque répertoire lu ou réglé
 * par `onRepertoire`, pour que l'étape se coche sur le fait. En étape (`enEtape`,
 * #1617), elle parle à qui découvre Maestro : ce que ce dossier change pour lui,
 * sans libellé de réglage ni le chemin, que le résumé de l'étape dit déjà.
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

import { LigneReglage, ProlongementReglage } from "./SectionParametres";

/**
 * Les dossiers autorisés depuis l'explorateur, et leur retrait (#1610).
 *
 * `version` est incrémentée par le parent quand l'explorateur de la section
 * vient d'en autoriser un : la liste se relit, au lieu d'attendre un
 * rechargement de la page. `onRetire` dit l'inverse au parent, qui fait relire
 * l'explorateur ouvert — les deux vues du même réglage ne se contredisent pas.
 */
function DossiersAutorises({
  version,
  onRetire,
  enEtape,
}: {
  version: number;
  onRetire: () => void;
  enEtape: boolean;
}) {
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
      onRetire();
    } catch (erreur) {
      // L'autorisation reste en place : la route n'écrit rien quand elle refuse.
      setRefusRetrait(refusDepuis(erreur));
    } finally {
      setRetraitEnCours(null);
    }
  }, [onRetire]);

  return (
    <>
      <LigneReglage
        libelle="Dossiers autorisés"
        // La portée, dite **une fois** et invariante — comme l'aide du
        // répertoire : elle décrit le réglage, pas son état du moment. Dans
        // l'étape, elle dit ce qui y mène à qui n'a encore rien autorisé.
        aide={
          enEtape
            ? "Si l'explorateur refuse un dossier, vous pouvez l'y autoriser : Maestro le parcourt alors, sous-dossiers compris, et il s'ajoute ici. Les zones protégées du poste restent fermées."
            : "Autorisés depuis l'explorateur quand il les refusait : Maestro peut parcourir ces dossiers et leurs sous-dossiers sur ce poste. Les zones protégées y restent refusées."
        }
      >
        {dossiers === null ? (
          <p className="text-corps text-texte-secondaire">
            {echecLecture !== null ? "—" : "…"}
          </p>
        ) : dossiers.length === 0 ? (
          <p className="text-annexe text-texte-secondaire">
            {enEtape ? "Aucun pour l'instant." : "Aucun dossier autorisé."}
          </p>
        ) : (
          // Alignée au début, comme les contrôles des autres lignes quand la
          // ligne se replie sous son libellé (téléphone) : un chemin long ne
          // pousse pas la liste contre le bord droit.
          <ul className="flex flex-col items-start gap-2">
            {dossiers.map((dossier) => (
              <li
                key={dossier.chemin}
                className="flex flex-wrap items-center gap-2"
              >
                <code className="font-mono text-corps wrap-anywhere">
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

export function ParametresProjets({
  onRepertoire,
  enEtape = false,
}: {
  /** Chaque répertoire lu ou réglé — ce sur quoi l'étape du dossier se coche. */
  onRepertoire?: (repertoire: RepertoireProjets) => void;
  /** Montée dans l'étape du dossier : dans les mots de qui découvre Maestro. */
  enEtape?: boolean;
} = {}) {
  const [repertoire, setRepertoire] = useState<RepertoireProjets | null>(null);
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const [enCours, setEnCours] = useState(false);
  // Deux échecs, deux états — et c'est un constat de la relecture visuelle : les
  // confondre faisait dire « Réglage refusé » à une **lecture** en panne, c'est-à-dire
  // à quelqu'un qui n'avait rien réglé.
  const [echecLecture, setEchecLecture] = useState<RefusProjet | null>(null);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [versionAutorises, setVersionAutorises] = useState(0);
  const [versionRetraits, setVersionRetraits] = useState(0);
  const retire = useCallback(() => setVersionRetraits((v) => v + 1), []);

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

  useEffect(() => {
    if (repertoire !== null) onRepertoire?.(repertoire);
  }, [repertoire, onRepertoire]);

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

  const gestes = (
    <div className="flex flex-wrap items-center gap-2">
      {/* « … » dit « ça arrive », et il ne doit donc pas rester à l'écran
          quand plus rien n'arrive : une lecture en panne rend « — », le
          tiret de l'inconnu qu'emploie déjà la dépense cumulée. Dans l'étape,
          le chemin n'est pas redit : le résumé de l'étape le porte. */}
      {!enEtape && (
        <code className="font-mono text-corps break-all">
          {repertoire !== null
            ? repertoire.chemin
            : echecLecture !== null
              ? "—"
              : "…"}
        </code>
      )}
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
  );

  return (
    <div className="flex flex-col">
      {enEtape ? (
        // Dans l'étape, ce que ce dossier change pour qui découvre Maestro, puis
        // le geste : le titre de l'étape nomme déjà le réglage.
        <div className="flex flex-col items-start gap-3 pb-3">
          <p className="text-corps text-texte-secondaire">
            Chaque projet que Maestro crée pour vous y reçoit son dossier. Pour un
            projet donné, vous pourrez toujours en choisir un autre.
          </p>
          {gestes}
        </div>
      ) : (
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
          {gestes}
        </LigneReglage>
      )}

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

      {/* L'explorateur prolonge la ligne du répertoire. */}
      {explorateurOuvert && (
        <ProlongementReglage>
          <ExplorateurDossiers
            cheminInitial={repertoire?.chemin ?? null}
            onChoisir={(chemin) => void regler(chemin)}
            onFermer={() => setExplorateurOuvert(false)}
            onAutorise={() => setVersionAutorises((v) => v + 1)}
            actualisation={versionRetraits}
          />
        </ProlongementReglage>
      )}

      <DossiersAutorises version={versionAutorises} onRetire={retire} enEtape={enEtape} />
    </div>
  );
}
