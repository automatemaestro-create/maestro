"use client";

/**
 * **Entrer dans le projet que la conversation vient de faire naître** (#1294,
 * #1340, docs/43 §2.2).
 *
 * Un projet naît dans le fil de l'orchestration, sur accord ; la réponse porte le
 * fait (`projet_cree`), et le modèle parle depuis lui : « C'est désormais le projet
 * ouvert, et cette conversation continue avec lui » (`_faits_d_un_projet_declare`,
 * `maestro/controltower/orchestration.py`). Ce module est ce qui rend la phrase
 * **vraie**, d'où que la naissance parte :
 *
 * - depuis la **porte d'entrée** (`NaissanceProjet`), où aucun projet n'est encore
 *   ouvert — le relais y ouvre en plus la colonne, sur la conversation où il est né ;
 * - depuis le **fil d'un projet ouvert** (`/chat`, la colonne de droite), où l'on
 *   tape « J'ai déjà un projet dans C:/…, importe-le ». Vu sur la vraie stack à la
 *   relecture de #1161 : le fil disait le carnet de recettes ouvert, l'en-tête
 *   gardait « Projet neuf ». Et ce n'était pas qu'un mot : le projet de la fenêtre
 *   part avec chaque envoi (`useChat`, #683), donc la demande suivante — un run, une
 *   correction de l'outillage qui venait de commencer — allait au projet quitté.
 *
 * Entrer, et non taire le fait : c'est ce que la porte faisait déjà, et le fil ne
 * parle plus que du projet né — son outillage, pièce par pièce, commence dans la
 * même réponse (#1161).
 *
 * ## Ce qui ne se défait pas
 *
 * - **la fiche est relue de l'API**, jamais reconstruite du fait : racine
 *   canonicalisée, VCS constaté, périmètre — tout ce que le shell lit ensuite ;
 * - **l'effet dépend de l'identifiant, jamais de l'objet** : chaque relecture du
 *   fil rend des messages neufs portant le même fait, et dépendre de l'objet
 *   annulait la lecture de la liste en vol — le projet était déclaré et la porte
 *   restait fermée (vu sur la vraie stack, gardé par `projet-actif.test.tsx`) ;
 * - **le fil d'un projet ouvert n'entre que dans une naissance à laquelle il
 *   assiste.** Une conversation garde ses naissances pour toujours : y entrer à la
 *   lecture ramènerait de force dans le projet qu'on vient de quitter d'un choix,
 *   et rouvrir la conversation d'hier ferait changer de projet sans rien demander
 *   (`naissance-dans-le-fil.test.tsx`).
 */

import { useEffect, useState } from "react";

import { chargerProjets } from "@/lib/api";
import { useProjetActifFacultatif } from "@/lib/etatProjetActif";
import { projetNeHorsDe, projetsNesDuFil } from "@/lib/naissance";
import { ecrireConversationOuverte } from "@/lib/preferences";
import type { ProjetCree } from "@/lib/types";
import type { Chat } from "@/lib/useChat";

/**
 * Entre dans `ne` dès qu'il est donné — le projet que le fil a fait naître —, et
 * rend la cause si on n'a pas pu (`null` sinon).
 *
 * `relais` ouvre en plus la colonne de conversation du projet : c'est le cas de la
 * porte, qui n'en a pas. Le fil d'un projet ouvert s'en passe — la colonne où l'on
 * parlait est déjà ouverte, et sur `/chat` l'écrire éteindrait un réglage que la
 * page ne possède pas (`Shell`, « une seule conversation à l'écran »).
 *
 * Hors du shell — un écran de fil rendu seul —, personne n'est là pour entrer :
 * l'entrée se tait (`useProjetActifFacultatif`).
 */
export function useEntreeDansLeProjetNe(
  ne: ProjetCree | null,
  { relais = false }: { relais?: boolean } = {},
): string | null {
  const choisir = useProjetActifFacultatif()?.choisir ?? null;
  const neId = ne?.id ?? null;
  const neNom = ne?.nom ?? "";
  const [refus, setRefus] = useState<string | null>(null);
  useEffect(() => {
    if (neId === null || choisir === null) return;
    let vivant = true;
    void chargerProjets()
      .then((projets) => {
        if (!vivant) return;
        const fiche = projets.find((projet) => projet.id === neId);
        if (fiche === undefined) {
          setRefus(
            `Le projet « ${neNom} » est déclaré, mais je ne le retrouve pas dans la liste — rechargez la page.`,
          );
          return;
        }
        if (relais) ecrireConversationOuverte(true);
        choisir(fiche);
      })
      .catch((e: unknown) => {
        if (vivant) setRefus(e instanceof Error ? e.message : String(e));
      });
    return () => {
      vivant = false;
    };
  }, [neId, neNom, relais, choisir]);
  return refus;
}

/**
 * La conversation telle qu'une surface l'a lue d'abord : laquelle, et les projets
 * qu'elle avait déjà fait naître.
 */
type Lecture = { conversation: string; nes: string[] };

/**
 * Le fil d'un projet ouvert entre dans le projet qu'il **voit naître** (#1340).
 *
 * La première lecture de chaque conversation sert de référence et se tait — la
 * règle de `useAnnonce` : arriver sur un fil n'est pas un changement d'état. Une
 * naissance est un `projet_cree` qui n'y était pas, et qu'un accord — un clic sur
 * la carte, un « oui » tapé — vient d'y écrire. La référence est **par
 * conversation** : en rouvrir une autre la relit, elle n'y assiste à rien. Un fil
 * pas encore servi (`conversation` vide) n'en prend aucune : ce qu'il portera à sa
 * première lecture, il le portait déjà.
 *
 * La référence est un état ajusté **pendant le rendu** plutôt qu'un `ref` écrit
 * dans un effet : la naissance se lit alors dans le rendu même qui la montre, et
 * l'entrée part de l'effet de `useEntreeDansLeProjetNe`, sans second tour.
 *
 * Une fiche introuvable laisse la fenêtre où elle est : le projet né reste dit
 * sous la bulle (`Conversation`, « Projet importé : »), et rien n'est deviné.
 */
export function useEntreeDepuisLeFil(fil: Chat): void {
  const projet = useProjetActifFacultatif()?.projet ?? null;
  const [lue, setLue] = useState<Lecture | null>(null);
  const servie = !fil.chargement && fil.conversation !== "";
  const relue = servie && lue?.conversation !== fil.conversation;
  if (relue) {
    setLue({
      conversation: fil.conversation,
      nes: projetsNesDuFil(fil.messages),
    });
  }
  const ne =
    servie && !relue && lue !== null
      ? projetNeHorsDe(fil.messages, lue.nes)
      : null;
  // Déjà dedans — la fenêtre vient d'y entrer : rien à refaire.
  useEntreeDansLeProjetNe(ne !== null && ne.id !== projet?.id ? ne : null);
}
