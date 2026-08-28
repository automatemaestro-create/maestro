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
 * ## La réponse s'écrit en direct (#695)
 *
 * L'envoi passe par le **flux** (`POST …/flux`, `lib/api`) et non plus par
 * `POST …/messages` : la réponse arrive en incréments, et la bulle se remplit.
 * C'est ici, et pas dans un écran, parce que c'est ici qu'il n'y en a qu'un :
 * les trois surfaces de fil — chat global, onglet d'une fiche agent, assistant
 * flottant — passent toutes par ce hook, si bien que le canal **remplace** le
 * chemin d'envoi au lieu de s'y ajouter. Le brancher dans `app/chat/page.tsx`
 * aurait donné deux façons de parler à un fil, ce que #269 avait justement
 * refusé.
 *
 * ### Le direct et le persisté ne se dédoublent pas
 *
 * Le `chat.message` du WebSocket continue d'arriver derrière chaque trame, donc
 * la même paire message/réponse arrive **deux fois** : par le flux, tout de
 * suite, et par le fil rechargé, un instant plus tard. Le hook tient donc deux
 * listes et les fusionne : le fil **persisté** (la vérité) et les messages que
 * le flux a rendus, gardés le temps que le REST les rattrape. La fusion écarte
 * un message déjà présent — même auteur, même horodatage, même contenu, c'est le
 * même objet sérialisé deux fois —, si bien qu'aucun ordre d'arrivée ne produit
 * de doublon ni de clignotement.
 *
 * ### Ce qui est reçu ne se perd pas
 *
 * `reponseEnCours` porte le texte en train de s'écrire. Quand le flux se **clôt**
 * (`fin`, ou `interrompu` après un arrêt demandé), il cède la place au message
 * que la trame porte : il est persisté, donc il survit au rechargement. Quand le
 * flux **casse** (trame `erreur`, transport coupé), il reste posé et **figé** —
 * le backend ne persiste rien dans ce cas (#693), et l'effacer ferait disparaître
 * de l'écran ce que l'utilisateur venait d'y lire. Il ne s'efface qu'à deux
 * moments : un nouvel envoi, ou l'arrivée au fil persisté d'une réponse au
 * message qu'il suivait — la seule chose qui puisse légitimement le remplacer.
 *
 * ## Un fil est une suite de conversations (#694, servi à l'écran par #696)
 *
 * Le hook tient trois choses de plus : *laquelle* est ouverte, la liste des
 * autres, et les deux gestes qui les commandent — en ouvrir une neuve, revenir
 * sur une ancienne. Cinq décisions à ne pas défaire :
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
 *   (docs/05 §6.14). Le troisième critère de #696 est donc tenu par les deux
 *   moitiés à la fois, et la mémoire reste ce qui distingue « je relis un vieux
 *   fil » de « je continue » ;
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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  arreterFluxChat,
  chargerConversationsChat,
  chargerFilChat,
  diffuserMessageChat,
  ErreurReponse,
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
  FRAGMENT_CHAT_DEBUT,
  FRAGMENT_CHAT_DELTA,
  FRAGMENT_CHAT_ERREUR,
  FRAGMENT_CHAT_FIN,
  FRAGMENT_CHAT_INTERROMPU,
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

/**
 * L'identité d'un message pour la fusion direct ↔ persisté.
 *
 * Trois champs et pas un identifiant : un `MessageChat` n'en porte pas (#84), et
 * ces trois-là sont posés par le backend avant la sérialisation — la trame du
 * flux et la ligne du REST décrivent le **même** objet, à l'octet près.
 */
function cleMessage(message: MessageChat): string {
  return [message.auteur, message.horodatage, message.contenu].join("\u0000");
}

/**
 * Ce que le drainage d'un flux apprend, au fil des trames (#695).
 *
 * `echange` répond à « y a-t-il quelque chose à arrêter, et sous quel nom ? »
 * **et** à « le message est-il acquis ? » : le backend le nomme sur la trame
 * `debut`, qui suit la persistance. `arretDemande` retient l'arrêt cliqué avant
 * que le flux ne se nomme. `demande` et `recu` sont ce qu'il reste à l'écran si
 * la réponse manque.
 */
type EchangeEnVol = {
  echange: string;
  arretDemande: boolean;
  demande: MessageChat | null;
  cause: string | null;
  recu: string;
};

/** La réponse en train de s'écrire — ou celle qui s'est arrêtée en chemin. */
export type ReponseEnCours = {
  /** Qui écrit : l'`auteur` du flux, celui que la bulle affichera. */
  auteur: string;
  /** Le texte reçu jusqu'ici — la concaténation exacte des `delta`. */
  texte: string;
  /**
   * Le flux s'est arrêté avant sa trame de clôture (coupure, réponse
   * impossible) : ce qui est là est **incomplet**, et rien ne le persiste.
   */
  figee: boolean;
};

export type Chat = {
  /** Le fil, dans l'ordre d'écriture (le plus récent en dernier). */
  messages: MessageChat[];
  /** WebSocket ouverte : les messages arrivent en temps réel. */
  connecte: boolean;
  /** Premier chargement REST encore en cours. */
  chargement: boolean;
  /** Fil illisible au dernier chargement (null si tout va bien). */
  erreur: string | null;
  /** Un échange est en vol : le message est parti, la réponse s'écrit. */
  envoi: boolean;
  /** La réponse en cours d'écriture — `null` quand rien ne coule (#695). */
  reponseEnCours: ReponseEnCours | null;
  /**
   * Envoie un message à l'agent, avec les **sources** qu'il embarque (#482), et
   * consomme sa réponse au fil de l'eau (#695).
   *
   * Rejette de deux façons qui n'appellent pas le même geste : `ErreurSource`
   * quand le message est **refusé** (motif et index compris, rien n'est parti),
   * `ErreurReponse` quand il est acquis mais que la réponse a manqué.
   */
  envoyer: (contenu: string, sources?: SourceDeclaree[]) => Promise<void>;
  /**
   * Arrête la génération en cours (#695) — sans effet s'il n'y en a pas.
   *
   * Le flux **continue d'être lu** jusqu'à sa trame `interrompu` : c'est elle
   * qui porte ce que le backend a persisté de la réponse arrêtée. Abandonner la
   * requête à la place laisserait la production s'achever (#268) et la réponse
   * entière tomber ensuite, c'est-à-dire un arrêt qui n'arrête rien.
   */
  interrompre: () => void;
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
  const [persistes, setPersistes] = useState<MessageChat[]>([]);
  const [directs, setDirects] = useState<MessageChat[]>([]);
  const [connecte, setConnecte] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [envoi, setEnvoi] = useState(false);
  const [reponseEnCours, setReponseEnCours] = useState<ReponseEnCours | null>(
    null,
  );
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
  /**
   * L'échange en vol — une `ref` mutable et non un état : rien de ce qu'il porte
   * n'est rendu à l'écran, et il est écrit par le consommateur du flux, qui ne
   * se remonte pas entre deux trames.
   */
  const enVol = useRef<EchangeEnVol | null>(null);
  /**
   * Le message utilisateur que la réponse figée suivait. C'est ce qui permet de
   * savoir qu'une réponse **persistée** est venue la remplacer : sans lui, un
   * simple compte de messages se tromperait dès qu'un rechargement arrive entre
   * l'envoi et le gel.
   */
  const messageDuGel = useRef<MessageChat | null>(null);

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
      setPersistes(fil.messages);
      setServie((avant) => ({
        agent,
        id: fil.conversation ?? "",
        cartes: avant.agent === agent ? avant.cartes : [],
      }));
      // Ce que le flux a rendu et que le REST porte désormais : le garder
      // doublerait la ligne à chaque fusion.
      const vues = new Set(fil.messages.map(cleMessage));
      setDirects((gardes) => gardes.filter((m) => !vues.has(cleMessage(m))));
      // Une réponse figée n'a plus lieu d'être dès qu'une vraie réponse au même
      // message est au fil — le seul événement qui l'autorise à disparaître.
      const attendu = messageDuGel.current;
      if (attendu !== null) {
        const rang = fil.messages.findIndex(
          (m) => cleMessage(m) === cleMessage(attendu),
        );
        if (rang >= 0 && rang < fil.messages.length - 1) {
          messageDuGel.current = null;
          setReponseEnCours(null);
        }
      }
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
      // Un nouvel envoi solde la réponse figée de l'échange précédent : elle
      // disait « ce qui s'est arrêté là », et on n'est plus là.
      setReponseEnCours(null);
      messageDuGel.current = null;
      // Ce que le drainage apprend, dans un objet plutôt qu'en variables :
      // l'écrire depuis la fonction de trame et le relire ici est précisément ce
      // qu'un `let` capturé rend ambigu à la relecture comme au typage.
      const vol = {
        echange: "",
        arretDemande: false,
        demande: null as MessageChat | null,
        cause: null as string | null,
        recu: "",
      };
      enVol.current = vol;
      try {
        await diffuserMessageChat(
          agent,
          contenu,
          sources,
          projetId,
          // La conversation **servie**, pas celle qu'on a demandée : c'est celle
          // qu'on a sous les yeux, et un message se range là où on l'a tapé.
          conversation,
          (trame) => {
            if (trame.echange !== "" && vol.echange === "") {
              vol.echange = trame.echange;
              // Un arrêt demandé pendant que l'échange n'avait pas encore de nom
              // part maintenant : sans ce rattrapage, le clic le plus pressé —
              // celui d'avant la première trame — serait le seul sans effet.
              if (vol.arretDemande) void arreterFluxChat(agent, trame.echange);
            }
            const porte = trame.message;
            switch (trame.type) {
              case FRAGMENT_CHAT_DEBUT: {
                if (porte !== null) {
                  vol.demande = porte;
                  setDirects((gardes) => [...gardes, porte]);
                }
                setReponseEnCours({
                  auteur: trame.auteur,
                  texte: "",
                  figee: false,
                });
                break;
              }
              case FRAGMENT_CHAT_DELTA: {
                vol.recu += trame.delta;
                setReponseEnCours((courante) => ({
                  auteur: courante?.auteur ?? trame.auteur,
                  texte: (courante?.texte ?? "") + trame.delta,
                  figee: false,
                }));
                break;
              }
              case FRAGMENT_CHAT_FIN:
              case FRAGMENT_CHAT_INTERROMPU: {
                // Les deux clôturent, et ce qu'elles portent est persisté : la
                // bulle en cours cède la place au message du fil, sans
                // clignotement puisque la fusion écarte le doublon.
                if (porte !== null) setDirects((gardes) => [...gardes, porte]);
                setReponseEnCours(null);
                break;
              }
              case FRAGMENT_CHAT_ERREUR: {
                // La cause est retenue, pas levée : la trame `erreur` est la
                // dernière du flux, et sortir d'ici laisserait le drainage en
                // plan. Le gel a lieu au `catch`, avec les coupures de
                // transport — même conséquence, même traitement.
                vol.cause = trame.delta;
                break;
              }
            }
          },
        );
        if (vol.cause !== null) throw new ErreurReponse(vol.cause, vol.recu);
      } catch (e) {
        // Le message est acquis dès que l'échange s'est nommé : la trame `debut`
        // suit la persistance. Avant, rien n'est parti — un refus de source, un
        // backend injoignable — et l'appelant peut rendre le brouillon.
        if (vol.echange === "") throw e;
        messageDuGel.current = vol.demande;
        setReponseEnCours((courante) =>
          courante === null ? null : { ...courante, figee: true },
        );
        throw e instanceof ErreurReponse
          ? e
          : new ErreurReponse(
              e instanceof Error ? e.message : String(e),
              vol.recu,
            );
      } finally {
        enVol.current = null;
        setEnvoi(false);
        // Le rattrapage d'avant ce lot, conservé : la paire arrivera aussi par
        // le WebSocket, mais ce rechargement direct rend l'écran juste même
        // socket coupée.
        await recharger();
      }
    },
    [agent, projetId, conversation, recharger],
  );

  const interrompre = useCallback(() => {
    const vol = enVol.current;
    if (vol === null) return;
    if (vol.echange === "") {
      vol.arretDemande = true;
      return;
    }
    void arreterFluxChat(agent, vol.echange);
  }, [agent]);

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
    setDirects([]);
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
      setDirects([]);
      ecrireConversationOuverte(agent, id);
    },
    [agent],
  );

  const messages = useMemo(() => {
    if (directs.length === 0) return persistes;
    const vues = new Set(persistes.map(cleMessage));
    return [...persistes, ...directs.filter((m) => !vues.has(cleMessage(m)))];
  }, [persistes, directs]);

  return {
    messages,
    connecte,
    chargement,
    erreur,
    envoi,
    reponseEnCours,
    envoyer,
    interrompre,
    conversation,
    conversations,
    nouvelleConversation,
    ouvrirConversation,
  };
}
