"use client";

/**
 * Section « Général » des Paramètres (#121) : à quel backend l'interface est
 * branchée, et est-ce qu'il répond.
 *
 * Les deux URL sont celles que le lanceur a données au serveur du front
 * (`lib/configPoste`, #641), sinon celles du build : elles se lisent ici mais ne
 * s'éditent pas — les rendre modifiables supposerait un stockage côté client et
 * un rechargement du flux, hors du périmètre de ce lot.
 * Le bouton, lui, est bien un contrôle : il sonde `GET /api/sante`, ce que le
 * badge temps réel ne dit pas (le WebSocket peut être coupé alors que le REST
 * répond, et réciproquement).
 *
 * « Revoir le premier lancement » (#642) rouvre le parcours d'un poste neuf — le
 * modèle, le dossier des projets, le premier projet — d'après les walkthroughs de
 * VS Code, qui se rouvrent depuis l'aide. Il quitte le projet ouvert pour la porte
 * d'entrée, où le parcours vit, et n'écrit rien : chaque étape y montre son
 * réglage tel qu'il est, et il ne se rejoue pas au démarrage suivant.
 */

import { useState } from "react";

import { Bouton } from "@/components/Primitives";
import { chargerSante, urlApi, urlEvenements } from "@/lib/api";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { useProjetActifFacultatif } from "@/lib/etatProjetActif";
import { demanderPremierLancement } from "@/lib/premierLancement";

import { LigneReglage } from "./SectionParametres";

type Verdict = { ok: boolean; message: string };

export function ParametresGeneral() {
  const { connecte, portee } = useEtatGlobal();
  // Hors du shell (un test qui monte la section seule), personne à quitter : le
  // geste ne se montre pas plutôt que de ne rien faire.
  const projetActif = useProjetActifFacultatif();
  const [enCours, setEnCours] = useState(false);
  const [verdict, setVerdict] = useState<Verdict | null>(null);

  const tester = async () => {
    setEnCours(true);
    setVerdict(null);
    try {
      const sante = await chargerSante();
      setVerdict({ ok: true, message: `API joignable (statut « ${sante.statut} »).` });
    } catch (e) {
      setVerdict({ ok: false, message: e instanceof Error ? e.message : String(e) });
    } finally {
      setEnCours(false);
    }
  };

  return (
    <div className="flex flex-col">
      <LigneReglage
        libelle="API Control Tower"
        aide="Celle que le lanceur a donnée à l'interface à son démarrage."
      >
        <code className="font-mono text-sm break-all">{urlApi()}</code>
      </LigneReglage>

      <LigneReglage
        libelle="Flux temps réel"
        aide="Le WebSocket qui pousse les événements — dérivé de l'URL de l'API, et cadré sur le projet actif : c'est l'URL réellement ouverte, portée comprise, pas un gabarit."
      >
        <div className="flex flex-wrap items-center justify-end gap-2">
          <code className="font-mono text-sm break-all">
            {urlEvenements(portee)}
          </code>
          <span
            className={
              "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium " +
              (connecte
                ? "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
                : "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300")
            }
          >
            <span
              className={
                "size-1.5 rounded-full " +
                (connecte
                  ? "bg-emerald-500"
                  : "animate-pulse motion-reduce:animate-none bg-amber-500")
              }
            />
            {connecte ? "Connecté" : "Reconnexion…"}
          </span>
        </div>
      </LigneReglage>

      <LigneReglage
        libelle="Vitalité du service"
        aide={
          verdict === null ? (
            "Interroge GET /api/sante — de quoi distinguer un backend éteint d'un flux coupé."
          ) : (
            <span
              className={
                verdict.ok
                  ? "text-emerald-700 dark:text-emerald-400"
                  : "text-rose-600 dark:text-rose-400"
              }
            >
              {verdict.message}
            </span>
          )
        }
      >
        <button
          type="button"
          onClick={() => void tester()}
          disabled={enCours}
          className="rounded border border-neutral-300 px-3 py-1 text-sm font-medium hover:bg-neutral-100 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          {enCours ? "Test…" : "Tester la connexion"}
        </button>
      </LigneReglage>

      {projetActif !== null && (
        <LigneReglage
          libelle="Premier lancement"
          aide="Le parcours du premier démarrage : le modèle, le dossier des projets, puis un premier projet. Le revoir ne change rien : chaque réglage s'y montre tel qu'il est."
        >
          <Bouton
            variante="contour"
            ton="neutre"
            onClick={() => {
              demanderPremierLancement();
              projetActif.quitter();
            }}
          >
            Revoir le premier lancement
          </Bouton>
        </LigneReglage>
      )}
    </div>
  );
}
