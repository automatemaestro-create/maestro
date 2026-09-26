"use client";

/**
 * **Un projet naît dans la conversation** (#1294, docs/43 §2.2) — la porte
 * d'entrée en mode création.
 *
 * « Nouveau projet » n'ouvre plus un formulaire à étapes (nom, origine, dossier,
 * périmètre, puis l'outillage) : il ouvre le fil de l'orchestration, sans projet.
 * On dit ce qu'on veut construire, avec ses mots ; l'orchestration pose au plus
 * les questions qui manquent, puis propose un nom, un dossier et le versionnement
 * sur une carte (`DemandeDeProjet`), et l'accord le déclare. Le projet né devient
 * alors le projet actif, et **la même conversation continue** dans la colonne de
 * droite du projet.
 *
 * ## La forme retenue, sur pièces
 *
 * Variante A de #1294, retenue par le regard neuf contre une conversation en
 * colonne de droite à côté de la liste (commentaire « Variante retenue » du
 * ticket), d'après v0, Lovable et ChatGPT : **une question en titre et un seul
 * composeur**. Ce que l'implémentation reprend des manques qu'il a relevés :
 *
 * - le retour à la liste est un **vrai bouton**, et il n'est absent que si la
 *   liste est **vide** — sur un poste neuf, il n'y a rien à choisir. Une liste
 *   illisible (API coupée) n'est pas vide : le retour y reste, pour retrouver la
 *   panne et son « Réessayer » ;
 * - importer un dossier est un **second chemin de la même conversation**, d'après
 *   Bolt : une amorce, pas un onglet ;
 * - **un seul geste principal à la fois** : quand une carte attend, c'est elle qui
 *   le porte ; le composeur garde son bouton d'envoi, une icône désarmée tant
 *   qu'on n'a rien écrit (celui de `Conversation`, tel quel) ;
 * - le **relais** : la colonne de conversation est ouverte à l'entrée dans le
 *   projet, sur la conversation où il est né.
 *
 * ## Ce qui n'est pas ici
 *
 * Rien n'est déclaré depuis ce composant : il **observe** le fil. Ce qui ouvre le
 * projet est le fait que la réponse d'un accord porte (`projet_cree`), relu du fil
 * — qu'on ait accepté d'un clic sur la carte ou d'un « oui » tapé.
 */

import { useEffect, useState } from "react";

import { useGestesDuFil } from "@/components/chat/GestesDuFil";
import { Conversation } from "@/components/Conversation";
import { IconeFlecheGauche } from "@/components/Icones";
import { Bouton } from "@/components/Primitives";
import { chargerProjets } from "@/lib/api";
import { lireConversationOuverte as lireConversationDuFil } from "@/lib/conversationOuverte";
import { useProjetActif } from "@/lib/etatProjetActif";
import { projetNeDuFil } from "@/lib/naissance";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
} from "@/lib/orchestration";
import { ecrireConversationOuverte } from "@/lib/preferences";
import { useChat } from "@/lib/useChat";

/**
 * Le mot d'accueil du fil de création — jamais persisté. Un **exemple** de
 * phrase, et aucune catégorie à cliquer : la personne a dit ne pas vouloir de
 * « choix proposés au début » (retex du 2026-09-24, docs/43 §1).
 */
export const ACCUEIL_NAISSANCE =
  "Par exemple : « un site vitrine pour mon kombucha », « un outil pour suivre mes factures ». Vous avez déjà un dossier ? Donnez-moi son chemin : je l'importerai.";

/**
 * La seule amorce : l'**autre** chemin, partir d'un dossier qu'on a déjà (d'après
 * Bolt, « or start from »). Envoyée, elle fait demander le chemin du dossier.
 */
export const AMORCE_IMPORT = "J'ai déjà un dossier à importer";

export function NaissanceProjet({
  retour,
  panne = null,
  parUnGeste = false,
}: {
  /** Revenir à la liste des projets — `undefined` quand il n'y en a aucun. */
  retour?: () => void;
  /**
   * La panne de l'API et de quoi relire, **sous** la question : placée au-dessus,
   * elle la faisait tomber au quatrième rang, et son « Réessayer » s'empilait
   * contre le retour, du même dessin (relecture de clôture).
   */
  panne?: React.ReactNode;
  /**
   * Ouverte par « Nouveau projet » plutôt que d'office (poste sans projet) : la
   * saisie prend alors le focus, comme un panneau qu'on vient de demander. Ouverte
   * d'office, elle ne le vole pas — la règle de `Conversation.focusAuMontage`.
   */
  parUnGeste?: boolean;
}) {
  const { choisir } = useProjetActif();
  // Le fil de l'orchestration **sans projet** : le backend l'accepte, et c'est le
  // projet que cette conversation fait naître qui lui en donnera un.
  const fil = useChat(AGENT_ORCHESTRATION, null);
  const gestes = useGestesDuFil(fil, AGENT_ORCHESTRATION);

  // Une conversation **neuve** à l'entrée : créer un projet ne se mêle pas à la
  // conversation d'un autre. L'API rend la conversation vierge déjà en tête plutôt
  // que d'en empiler une (#696), donc revenir ici n'en fabrique pas une série.
  const { nouvelleConversation } = fil;
  // La conversation ouverte ici : `null` tant qu'elle n'est pas née, `""` si l'API
  // n'a pas pu en ouvrir une (on reste alors sur la courante).
  const [neuve, setNeuve] = useState<string | null>(null);
  useEffect(() => {
    let vivant = true;
    void nouvelleConversation()
      .then(() => {
        if (vivant) setNeuve(lireConversationDuFil(AGENT_ORCHESTRATION));
      })
      .catch(() => {
        // Sans conversation neuve, on reste sur la courante : la création y
        // marche aussi, elle y est seulement mêlée à ce qui précède.
        if (vivant) setNeuve("");
      });
    return () => {
      vivant = false;
    };
  }, [nouvelleConversation]);
  // ⚠ Le fil ne se relit sur la conversation neuve qu'**après** sa naissance :
  // entre les deux, il sert encore la précédente, et un projet né là-bas ouvrait
  // ce projet-là (vu sur la vraie stack : « Nouveau projet » juste après un import
  // rentrait dans le projet importé). Seul compte le fil de la conversation neuve.
  const ouverte =
    neuve !== null && (neuve === "" || fil.conversation === neuve);

  // Le projet né **dans cette conversation** : on l'ouvre. Relu de la fiche que
  // l'API sert (racine canonicalisée, VCS constaté), jamais reconstruit.
  //
  // ⚠ L'effet dépend de l'**identifiant**, jamais de l'objet : après le geste,
  // `useChat` relit le fil, et chaque relecture rend des messages neufs portant
  // le même fait. Dépendre de l'objet annulait la lecture de la liste en vol à
  // chaque relecture — le projet était déclaré et la porte restait fermée
  // (constaté sur la vraie stack, gardé par `projet-actif.test.tsx`).
  const ne = ouverte && !fil.chargement ? projetNeDuFil(fil.messages) : null;
  const neId = ne?.id ?? null;
  const neNom = ne?.nom ?? "";
  const [refus, setRefus] = useState<string | null>(null);
  useEffect(() => {
    if (neId === null) return;
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
        // Le relais (manque relevé par le regard neuf) : la conversation où le
        // projet est né continue dans la colonne de droite du projet.
        ecrireConversationOuverte(true);
        choisir(fiche);
      })
      .catch((e: unknown) => {
        if (vivant) setRefus(e instanceof Error ? e.message : String(e));
      });
    return () => {
      vivant = false;
    };
  }, [neId, neNom, choisir]);

  return (
    <div className="flex flex-col gap-4">
      {retour !== undefined && (
        <div>
          {/* À contour, pas discret : la relecture de clôture a vu le discret se
              lire comme un lien pâle, en retrait du bord de la colonne — ce que
              la variante retenue écartait. */}
          <Bouton
            variante="contour"
            ton="neutre"
            icone={IconeFlecheGauche}
            onClick={retour}
          >
            Choisir un projet existant
          </Bouton>
        </div>
      )}
      <div className="flex flex-col gap-2">
        <h1 className="text-page font-semibold tracking-tight text-texte">
          Que voulez-vous construire ?
        </h1>
        <p className="text-corps text-texte-secondaire">
          Dites-le avec vos mots. Je vous pose les questions qui manquent, puis je
          vous propose un nom, un dossier et le versionnement — rien n&apos;est
          créé sans votre accord.
        </p>
      </div>
      {panne}
      {refus !== null && (
        <p className="text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
      <Conversation
        fil={fil}
        interlocuteur={INTERLOCUTEUR_ORCHESTRATION}
        libelle="Nouveau projet"
        titre="Conversation"
        titreMasque
        accueil={ACCUEIL_NAISSANCE}
        amorces={[AMORCE_IMPORT]}
        pied={gestes}
        // La porte est **hors du shell** : aucun bouton flottant n'y recouvre le
        // bas du fil, il n'y a donc rien à réserver sous le composeur.
        bandeDuFlottant={false}
        focusAuMontage={parUnGeste}
      />
    </div>
  );
}
