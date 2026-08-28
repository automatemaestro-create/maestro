"use client";

/**
 * Le fil de chat d'un agent, côté navigateur (ticket #85).
 *
 * Même modèle que `useControlTower` : l'historique se charge par le REST
 * (`GET /api/chat/{agent}`) puis le WebSocket signale chaque `chat.message`
 * du fil. Le backend persiste **avant** de diffuser (maestro/controltower/
 * chat.py) : à réception d'un événement le REST est déjà à jour, le hook
 * recharge donc le fil (rechargements coalescés) plutôt que de projeter
 * l'événement. La connexion se rétablit seule (backoff plafonné) et chaque
 * reconnexion recharge le fil — les messages manqués pendant une coupure
 * sont rattrapés.
 *
 * **Le seul flux de l'application qui reste transverse** (#281), et c'est le
 * contrat de #277 qui l'impose : un `chat.message` ne porte pas de `projet_id`
 * (maestro/controltower/chat.py), donc une socket cadrée sur un projet ne le
 * recevrait **jamais** — le fil se figerait sans rien dire, chaque message
 * n'apparaissant qu'au rechargement suivant. Ce n'est pas une exception de
 * confort : le chat parle de l'**outil** et non du projet (voir le répondeur
 * d'assistance, `maestro/controltower/app.py`), il n'a donc pas de périmètre à
 * respecter. Toute vue qui, elle, montre le **travail** passe par
 * `useControlTower` et son projet actif.
 *
 * **Ce que le fil ne cadre pas, il le transporte** (#683). `projetId` est le
 * projet de la fenêtre : il part avec **l'envoi**, jamais avec la lecture du fil
 * ni avec la socket — rien de ce qui précède ne change. Il ne sert qu'à ce qu'un
 * message **ouvre** : un run dicté à l'orchestration appartient au projet où on
 * l'a demandé, faute de quoi il n'entre dans la vue d'aucun projet et devient
 * introuvable à l'écran (le défaut de #683, devenu le cas nominal depuis que le
 * chat est la seule porte d'entrée, #666). Le hook ne va pas le chercher
 * lui-même : il le reçoit de l'appelant, seul à savoir s'il est monté sous un
 * projet — l'assistant flottant et l'onglet Chat d'un agent n'ouvrent aucun run
 * et n'ont donc rien à passer.
 *
 * **Un fil est une suite de conversations** (#694), et c'est ici que l'écran s'en
 * sert (#696). Le hook tient trois choses de plus : *laquelle* est ouverte, la
 * liste des autres, et les deux gestes qui les commandent — en ouvrir une neuve,
 * revenir sur une ancienne. Cinq décisions à ne pas défaire :
 *
 * - **la conversation servie n'est pas celle qu'on demande.** `demandee` est ce
 *   que la mémoire du poste réclame, `""` valant « la plus récente » ;
 *   `conversation` est ce que l'API a **servi**, qu'elle nomme dans sa réponse.
 *   Les deux ne se confondent pas : la seconde est la seule qui désigne toujours
 *   quelque chose, et c'est elle qui décide de tout — où part l'envoi, laquelle
 *   porte la marque dans l'historique ;
 * - **la mémoire ne retient qu'un CHOIX, jamais un défaut.** On n'y écrit pas la
 *   conversation servie à chaque chargement : on n'y écrit que lorsque quelqu'un
 *   en désigne une (ouvrir une neuve, rouvrir une ancienne). Sans choix, un
 *   rechargement de la page retombe sur « la plus récente », qui *est* celle
 *   qu'on avait sous les yeux — écrire dans une conversation la ramène en tête
 *   (§6.14). Le troisième critère est donc tenu par les deux moitiés à la fois,
 *   et la mémoire reste ce qui distingue « je relis un vieux fil » de « je
 *   continue » ;
 * - **une mémoire périmée n'est pas une panne.** Une conversation retenue d'une
 *   visite passée a pu disparaître (fil purgé, poste rebranché sur une autre
 *   API) : l'API répond alors `404`, et laisser l'écran sur « fil illisible »
 *   pour un souvenir périmé serait le pire des deux. On l'oublie et on relit la
 *   plus récente — une fois, jamais en boucle ;
 * - **la liste se recharge quand le fil se recharge**, et c'est un fait de
 *   conception : titre, dernière activité et nombre de messages sont **dérivés**
 *   des messages (§6.14), donc ils changent exactement aux instants où le fil
 *   change. Les découpler donnerait un historique qui retarde d'un message ;
 * - **l'envoi part dans la conversation affichée**, pas dans « la plus
 *   récente » : sans ce paramètre, écrire depuis un fil ancien rangerait le
 *   message ailleurs que là où on l'a tapé.
 *
 * Prix assumé plutôt que masqué : changer de conversation **rouvre la socket**,
 * la lecture dont l'effet dépend ayant changé d'identité. C'est exactement ce que
 * fait déjà un changement de destinataire — le geste voisin, dans la même colonne
 * — et le bus écouté est commun à toute la Control Tower : la reconnexion est
 * immédiate et ne perd rien, chaque ouverture rechargeant le fil.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import {
  chargerConversationsChat,
  chargerFilChat,
  envoyerMessageChat,
  ouvrirConversationChat,
  urlEvenements,
  PORTEE_TOUS,
} from "./api";
import {
  ecrireConversationOuverte,
  useConversationOuverte,
} from "./conversationOuverte";
import {
  EVENEMENT_CHAT_MESSAGE,
  type ConversationChat,
  type Evenement,
  type MessageChat,
  type SourceDeclaree,
} from "./types";

/** Fenêtre de coalescence des rechargements sur rafale d'événements (ms). */
const DELAI_RECHARGEMENT_MS = 150;

/** Plafond du backoff de reconnexion WebSocket (ms). */
const RECONNEXION_MAX_MS = 10_000;

/**
 * Ce que l'API a servi, **et pour quel agent** (#696). L'agent est dans l'état
 * plutôt qu'à côté parce que la question qu'on pose au rendu est « ceci
 * appartient-il encore au destinataire affiché ? » — un couple répond, deux états
 * séparés se désaccordent le temps d'un aller-retour.
 */
type Servie = { agent: string; id: string; cartes: ConversationChat[] };

const RIEN_DE_SERVI: Servie = { agent: "", id: "", cartes: [] };

/** Un tableau constant : le rendre neuf à chaque rendu ferait travailler les listes pour rien. */
const AUCUNE_CARTE: ConversationChat[] = [];

export type Chat = {
  /** Le fil persisté, dans l'ordre d'écriture (le plus récent en dernier). */
  messages: MessageChat[];
  /** WebSocket ouverte : les messages arrivent en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** Fil illisible au dernier chargement (null si tout va bien). */
  erreur: string | null;
  /** Envoi en cours : le message est parti, la réponse se fait attendre. */
  envoi: boolean;
  /**
   * Envoie un message à l'agent, avec les **sources** qu'il embarque (#482).
   * Rejette si la réponse n'a pas pu être produite, ou sur une `ErreurSource`
   * quand une source est refusée — motif et index compris, pour que le fil
   * l'affiche à l'endroit du geste refusé.
   */
  envoyer: (contenu: string, sources?: SourceDeclaree[]) => Promise<void>;
  /**
   * La conversation **servie** (#696) — celle qu'on lit et où part l'envoi.
   * `""` tant que l'API n'a pas répondu : personne ne peut la nommer avant.
   */
  conversation: string;
  /** Les conversations du fil, la plus récente d'abord (#696). Jamais filtrées. */
  conversations: ConversationChat[];
  /**
   * Le geste « Nouvelle conversation » : ouvre un fil vierge chez le même agent
   * et bascule dessus, la précédente restant intacte et listée. Idempotent —
   * l'API rend la conversation vierge déjà en tête plutôt que d'en empiler une.
   */
  nouvelleConversation: () => Promise<void>;
  /** Revient sur une conversation existante, désignée par son identifiant. */
  ouvrirConversation: (id: string) => void;
};

export function useChat(agent: string, projetId: string | null = null): Chat {
  const [messages, setMessages] = useState<MessageChat[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [envoi, setEnvoi] = useState(false);
  // Ce qu'on **demande** — `""` valant « la plus récente ». Lu de la mémoire du
  // poste, jamais recopié dans un état d'ici : c'est ce qui le rend insensible au
  // remontage de la `key` de projet du `Shell` (#281), et ce qui évite d'avoir
  // deux versions du même choix à tenir d'accord (`lib/conversationOuverte`).
  const demandee = useConversationOuverte(agent);
  // Ce que l'API a **servi**, avec l'agent auquel ça appartient : les deux
  // voyagent ensemble pour qu'un changement de destinataire ne laisse jamais
  // l'historique de l'un sous le nom de l'autre, fût-ce le temps d'un rendu.
  const [servie, setServie] = useState<Servie>(RIEN_DE_SERVI);
  const conversation = servie.agent === agent ? servie.id : "";
  const conversations = servie.agent === agent ? servie.cartes : AUCUNE_CARTE;

  const rechargementPrevu = useRef<ReturnType<typeof setTimeout> | null>(null);

  const recharger = useCallback(async () => {
    try {
      const fil = await chargerFilChat(agent, demandee).catch(
        async (echec: unknown) => {
          // Mémoire périmée : la conversation retenue n'existe plus (fil purgé,
          // poste rebranché sur une autre API). On l'oublie et on relit la plus
          // récente, **une** fois — un second échec est une vraie panne et
          // remonte comme telle.
          if (demandee === "") throw echec;
          ecrireConversationOuverte(agent, "");
          return chargerFilChat(agent);
        },
      );
      setMessages(fil.messages);
      setServie((avant) => ({
        agent,
        id: fil.conversation ?? "",
        cartes: avant.agent === agent ? avant.cartes : [],
      }));
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
    // La liste suit le fil (voir l'en-tête) mais ne décide pas de sa lisibilité :
    // un historique qui n'arrive pas laisse le précédent en place et se rattrape
    // au rechargement suivant. Déclarer « fil illisible » par-dessus une
    // conversation parfaitement lisible serait le pire des deux verdicts.
    try {
      const { conversations: cartes } = await chargerConversationsChat(agent);
      setServie((avant) => (avant.agent === agent ? { ...avant, cartes } : avant));
    } catch {
      // Transitoire : même API que le fil, qui vient de répondre.
    }
  }, [agent, demandee]);

  const planifierRechargement = useCallback(() => {
    if (rechargementPrevu.current !== null) return;
    rechargementPrevu.current = setTimeout(() => {
      rechargementPrevu.current = null;
      void recharger();
    }, DELAI_RECHARGEMENT_MS);
  }, [recharger]);

  useEffect(() => {
    let abandonne = false;
    let socket: WebSocket | null = null;
    let reconnexion: ReturnType<typeof setTimeout> | null = null;
    let tentatives = 0;

    // Chargement initial différé d'un tick (même mécanique que les rafales) :
    // l'effet lui-même ne déclenche aucun setState synchrone.
    planifierRechargement();

    const connecter = () => {
      if (abandonne) return;
      // Transverse à dessein — voir l'en-tête : `chat.message` ne porte pas de
      // projet, une socket cadrée ne le verrait pas passer.
      socket = new WebSocket(urlEvenements(PORTEE_TOUS));
      socket.onopen = () => {
        tentatives = 0;
        setConnecte(true);
        // Rattrape ce qui a pu se passer entre le REST initial et l'ouverture
        // de la socket (ou pendant une coupure).
        void recharger();
      };
      socket.onmessage = (message: MessageEvent<string>) => {
        let evenement: Evenement;
        try {
          evenement = JSON.parse(message.data) as Evenement;
        } catch {
          return; // trame illisible : on l'ignore, le REST reste la vérité
        }
        // Le bus est commun à toute la Control Tower : seul un message de ce
        // fil justifie un rechargement.
        if (
          evenement.type !== EVENEMENT_CHAT_MESSAGE ||
          evenement.agent !== agent
        )
          return;
        planifierRechargement();
      };
      socket.onclose = () => {
        setConnecte(false);
        if (abandonne) return;
        tentatives += 1;
        const delai = Math.min(1000 * 2 ** (tentatives - 1), RECONNEXION_MAX_MS);
        reconnexion = setTimeout(connecter, delai);
      };
      socket.onerror = () => {
        socket?.close();
      };
    };

    connecter();

    return () => {
      abandonne = true;
      if (reconnexion !== null) clearTimeout(reconnexion);
      if (rechargementPrevu.current !== null) {
        clearTimeout(rechargementPrevu.current);
        rechargementPrevu.current = null;
      }
      socket?.close();
    };
  }, [agent, recharger, planifierRechargement]);

  const envoyer = useCallback(
    async (contenu: string, sources: SourceDeclaree[] = []) => {
      setEnvoi(true);
      try {
        // La conversation **servie**, pas celle qu'on a demandée : c'est celle
        // qu'on a sous les yeux, et un message se range là où on l'a tapé.
        await envoyerMessageChat(agent, contenu, sources, projetId, conversation);
        // La paire message/réponse arrivera aussi par le WebSocket ; ce
        // rechargement direct rend l'UI réactive même si la socket est coupée.
        await recharger();
      } finally {
        setEnvoi(false);
      }
    },
    [agent, projetId, conversation, recharger],
  );

  /**
   * Le geste « Nouvelle conversation » (#696). L'API décide **quelle** est la
   * conversation neuve — elle rend celle déjà vierge en tête plutôt que d'en
   * empiler une —, et c'est son identifiant qu'on retient : réclamer autre chose
   * que ce qu'elle vient de nommer serait ré-inventer sa règle d'idempotence de
   * ce côté-ci. Écrire dans la mémoire **est** la bascule : `demandee` en est
   * abonné, la lecture suit toute seule.
   */
  const nouvelleConversation = useCallback(async () => {
    const carte = await ouvrirConversationChat(agent);
    ecrireConversationOuverte(agent, carte.id);
  }, [agent]);

  /**
   * Revient sur une conversation existante. On **demande**, on ne pose pas : la
   * lecture qui suit dira ce qui a réellement été servi, et c'est elle qui fait
   * foi partout ailleurs — jusqu'à l'oublier si l'identifiant ne désigne plus
   * rien.
   */
  const ouvrirConversation = useCallback(
    (id: string) => {
      ecrireConversationOuverte(agent, id);
    },
    [agent],
  );

  return {
    messages,
    connecte,
    chargement,
    erreur,
    envoi,
    envoyer,
    conversation,
    conversations,
    nouvelleConversation,
    ouvrirConversation,
  };
}
