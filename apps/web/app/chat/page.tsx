"use client";

/**
 * La page Chat de la Control Tower — le chat **global**, non lié à un agent
 * (#269, lot 2 de #244).
 *
 * Le chat *par agent* (#85) a rejoint la fiche agent en onglet (#190) : c'était
 * la même intention que « Agents » vue par une autre facette, et le menu n'en
 * garde qu'une par intention. Le chat global, lui, en est une distincte —
 * s'adresser à l'orchestration plutôt qu'à un exécutant, « poser une demande
 * sans avoir à choisir d'abord à qui la poser ». L'entrée de menu et cette route
 * lui gardaient leur place depuis #190, annoncées et inertes ; c'est ce lot qui
 * les remplit, sur le canal `orchestrateur` livré par le lot 1 (#268).
 *
 * ## Trois choix, et ce qu'ils écartent
 *
 * **Un seul composant de fil.** La mise en page conversationnelle vit dans
 * `components/Conversation`, que l'onglet Chat d'un agent monte aussi
 * (`components/FilChat`). C'est l'arbitrage de #620 rendu concret : ce milestone
 * passe le premier, le lot 13 de « Control Tower v3 — agents » (#265) réutilisera
 * ce composant au lieu de le fournir.
 *
 * **La mention change de destinataire, elle ne duplique rien.** `@dev …` envoie
 * dans le fil de `dev` — celui-là même que sert sa fiche —, et l'écran bascule
 * dessus sans navigation. Copier le message dans les deux fils aurait donné deux
 * historiques d'une même conversation, désaccordés dès le premier rechargement :
 * c'est exactement ce que le critère « les deux ne divergent pas » interdit
 * (voir `lib/orchestration`).
 *
 * **Le direct passe par le WebSocket, pas encore par le flux SSE.** Le lot 1 a
 * construit le canal de streaming (`GET /api/chat/{agent}/flux`, docs/05 §6.5) et
 * il attend son consommateur. Le brancher **ici** serait un second chemin d'envoi
 * côté navigateur, donc deux façons de parler à un fil — l'inverse de ce que ce
 * lot unifie —, et surtout ce chemin-là **ne sait pas porter de sources** : le
 * flux prend son `contenu` en paramètre d'URL, quand `POST …/messages` accepte
 * les `sources[]` de #482. Y basculer le fil perdrait les pièces jointes en
 * silence, c'est-à-dire échangerait un rendu incrémental contre une
 * fonctionnalité. La réponse arrive donc dès qu'elle tombe (`chat.message` sur le
 * bus, `useChat` recharge), l'attente étant dite par « … répond… » ; le rendu
 * incrémental est pour le lot qui consommera le canal, dans le composant de fil
 * partagé — une fois que le canal saura porter ce qu'un message porte.
 */

import { useMemo, useState } from "react";

import { Conversation } from "@/components/Conversation";
import {
  IconeAgent,
  IconeChat,
  IconeFermer,
  IconeRuns,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
  EtatVide,
  LienRenvoi,
} from "@/components/Primitives";
import { cheminOnglet } from "@/lib/agents";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { hrefRun } from "@/lib/navigation";
import {
  ACCUEIL_ORCHESTRATION,
  AGENT_ORCHESTRATION,
  AMORCES_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  mentionEnTete,
} from "@/lib/orchestration";
import type { MessageChat } from "@/lib/types";
import { useChat } from "@/lib/useChat";

export default function PageChat() {
  const { agents, taches } = useEtatGlobal();
  const [destinataire, setDestinataire] = useState(AGENT_ORCHESTRATION);
  const fil = useChat(destinataire);

  // L'orchestration en tête, puis les agents du parc : c'est l'ordre du menu
  // (`/chat` avant `/agents`) et celui de l'usage — on s'adresse à
  // l'orchestration par défaut, à un exécutant par exception.
  const destinataires = useMemo(
    () => [AGENT_ORCHESTRATION, ...agents.map((agent) => agent.nom)],
    [agents],
  );

  const global = destinataire === AGENT_ORCHESTRATION;
  const interlocuteur = global ? INTERLOCUTEUR_ORCHESTRATION : destinataire;

  /**
   * Chaque frappe passe ici : une mention close par une espace change le
   * destinataire et quitte le brouillon, tout le reste passe tel quel. Écrire
   * `@` sans nom connu ne fait donc rien — le texte reste visible, ce qui vaut
   * mieux qu'une mention avalée en silence.
   */
  const detacherLaMention = (texte: string) => {
    const mention = mentionEnTete(texte, destinataires);
    if (mention === null) return texte;
    setDestinataire(mention.agent);
    return mention.reste;
  };

  return (
    // Le corps à gauche, la colonne de propriétés à droite (#539) — une seule
    // place de corps, le fil, et tout ce qui l'accompagne dans la troisième
    // place, qui est la seule sans plafond. `items-start` : la colonne se cale
    // en haut plutôt que de s'étirer sur la hauteur du fil.
    <div className="grid gap-6 @4xl:grid-cols-3 @4xl:items-start">
      <Conversation
        className="@4xl:col-span-2"
        fil={fil}
        interlocuteur={interlocuteur}
        libelle="Chat global"
        titre={global ? "Chat global" : `Aparté avec ${destinataire}`}
        icone={IconeChat}
        accueil={global ? ACCUEIL_ORCHESTRATION : undefined}
        amorces={global ? AMORCES_ORCHESTRATION : []}
        surSaisie={detacherLaMention}
        entete={
          !global && (
            <BadgeEtat ton="info" contour>
              @{destinataire}
            </BadgeEtat>
          )
        }
        bandeau={
          !global && (
            <div className="flex flex-wrap items-center gap-3 rounded-md border border-sky-200 bg-sky-50 px-3 py-2 text-sm text-sky-900 dark:border-sky-900 dark:bg-sky-950 dark:text-sky-200">
              <span className="min-w-0">
                Ce message part dans le fil de <strong>{destinataire}</strong> —
                le même que sert sa fiche. Rien n&apos;est recopié ici.
              </span>
              <span className="ml-auto flex flex-wrap items-center gap-3">
                <LienRenvoi
                  renvoi={{
                    href: cheminOnglet(destinataire, "chat"),
                    libelle: "Vue détaillée",
                  }}
                />
                <Bouton
                  variante="contour"
                  ton="neutre"
                  icone={IconeFermer}
                  onClick={() => setDestinataire(AGENT_ORCHESTRATION)}
                >
                  Revenir à l&apos;orchestration
                </Bouton>
              </span>
            </div>
          )
        }
      />
      <aside
        aria-label="Propriétés du fil"
        className="flex min-w-0 flex-col gap-6"
      >
        <Carte densite="aeree">
          <EnTeteSection titre="Parler à" icone={IconeAgent} />
          <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
            Au clavier, sans quitter l&apos;écran : commencez le message par
            <span className="font-mono"> @nom </span> suivi d&apos;une espace.
          </p>
          <ul className="mt-3 flex flex-wrap gap-2">
            {destinataires.map((nom) => (
              <li key={nom}>
                <Bouton
                  variante={nom === destinataire ? "plein" : "contour"}
                  ton={nom === AGENT_ORCHESTRATION ? "accent" : "neutre"}
                  aria-pressed={nom === destinataire}
                  onClick={() => setDestinataire(nom)}
                >
                  {nom === AGENT_ORCHESTRATION ? "Orchestration" : `@${nom}`}
                </Bouton>
              </li>
            ))}
          </ul>
        </Carte>
        <Carte densite="aeree">
          <EnTeteSection titre="Ouvert depuis ce fil" icone={IconeRuns} />
          <SuitesDuFil messages={fil.messages} taches={taches} />
        </Carte>
      </aside>
    </div>
  );
}

/**
 * Le récapitulatif de ce que la conversation a ouvert — les runs nommés par les
 * messages du fil, du plus récent au plus ancien.
 *
 * C'est la moitié « écran » du troisième critère : chaque bulle porte déjà son
 * renvoi (`components/Conversation`), mais un fil de cinquante messages ne se
 * relit pas pour retrouver le run d'avant-hier. La liste ne **déduit** rien : un
 * run y figure parce qu'un message le rattache (`run_id`, #268), jamais parce
 * qu'il a tourné pendant qu'on avait l'écran ouvert.
 */
function SuitesDuFil({
  messages,
  taches,
}: {
  messages: MessageChat[];
  taches: { run_id: string }[];
}) {
  const runs = useMemo(() => {
    const vus = new Set<string>();
    const ouverts: string[] = [];
    for (const message of messages) {
      const run = message.run_id ?? "";
      if (run === "" || vus.has(run)) continue;
      vus.add(run);
      ouverts.push(run);
    }
    return ouverts.reverse();
  }, [messages]);

  if (runs.length === 0) {
    return (
      <div className="mt-3">
        <EtatVide
          icone={IconeRuns}
          message="Rien encore. Une demande de travail — « ajoute la pagination à la liste des projets » — ouvre un run, et il apparaît ici avec ses tâches."
        />
      </div>
    );
  }

  return (
    <ul className="mt-3 flex flex-col gap-2">
      {runs.map((run) => {
        const nombre = taches.filter((tache) => tache.run_id === run).length;
        const href = hrefRun(run);
        return (
          <li
            key={run}
            className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1"
          >
            <span className="min-w-0">
              <span className="font-mono text-annexe">{run}</span>
              <span className="ml-2 text-annexe text-neutral-500 dark:text-neutral-400">
                {nombre === 0
                  ? "décomposition en cours"
                  : nombre === 1
                    ? "1 tâche"
                    : `${nombre} tâches`}
              </span>
            </span>
            {href !== undefined && (
              <LienRenvoi renvoi={{ href, libelle: "Voir le run" }} />
            )}
          </li>
        );
      })}
    </ul>
  );
}
