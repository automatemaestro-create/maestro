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
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  arreterFluxChat,
  chargerFilChat,
  diffuserMessageChat,
  ErreurReponse,
  urlEvenements,
  PORTEE_TOUS,
} from "./api";
import {
  EVENEMENT_CHAT_MESSAGE,
  FRAGMENT_CHAT_DEBUT,
  FRAGMENT_CHAT_DELTA,
  FRAGMENT_CHAT_ERREUR,
  FRAGMENT_CHAT_FIN,
  FRAGMENT_CHAT_INTERROMPU,
  type Evenement,
  type MessageChat,
  type SourceDeclaree,
} from "./types";

/** Fenêtre de coalescence des rechargements sur rafale d'événements (ms). */
const DELAI_RECHARGEMENT_MS = 150;

/** Plafond du backoff de reconnexion WebSocket (ms). */
const RECONNEXION_MAX_MS = 10_000;

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
      const fil = await chargerFilChat(agent);
      setPersistes(fil.messages);
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
  }, [agent]);

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
    [agent, projetId, recharger],
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
  };
}
