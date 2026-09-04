"use client";

/**
 * **Le** composant de fil du produit (#269, lot 2 de #244) : les bulles, la
 * saisie, la matière qu'un message embarque, et ce qu'une réponse a ouvert.
 *
 * Il est né d'un arbitrage écrit dans le ticket : la mise en page
 * conversationnelle se construit **de ce côté-ci**, et le lot 13 de « Control
 * Tower v3 — agents » (#265) la réutilise plutôt que l'inverse (#620). Un seul
 * composant, donc, pour les deux fils que l'utilisateur peut lire en plein
 * format — le **chat global** (`app/chat/page.tsx`, fil `orchestrateur`) et le
 * **chat d'un agent** (`components/FilChat`, onglet de sa fiche). C'est ce qui
 * donne au critère « les deux ne divergent pas » un support autre qu'une
 * intention : ils ne peuvent pas diverger de mise en page, il n'y en a qu'une —
 * et c'est aussi ce que `lib/useSourcesComposees` annonçait en se posant hors des
 * composants (#482, « les deux surfaces de fil n'auront pas à s'accorder sur une
 * copie chacune »).
 *
 * ⚠ Ce qui **n'y est pas fondu**, et à dessein : le panneau d'assistance
 * flottant (#123, `components/AssistantFlottant`). Ce n'est pas le même objet —
 * une carte bornée qui se pose *par-dessus* la page qu'on est en train
 * d'utiliser, sans région live ni en-tête de section, et dont la contrainte
 * première est de ne pas masquer l'écran. Les fondre reviendrait à donner à ce
 * composant un mode « petit », c'est-à-dire deux mises en page dans un fichier
 * qui existe pour n'en porter qu'une.
 *
 * ## Ce qu'il porte, et de qui il le tient
 *
 * - **les sources d'un message** (#482) : fichiers glissés ou choisis, dossier du
 *   poste, adresse collée. Trois choses à ne pas défaire — la matière passe par
 *   la **chaîne d'ingestion existante** et par elle seule (les octets partent à
 *   `POST /api/sources`, le message ne porte que les identifiants rendus, l'écran
 *   ne juge rien) ; un **refus reste dans le fil**, sur la source qu'il vise, et
 *   la composition est **conservée** dans tous les cas ; le **glisser-déposer
 *   vise toute la conversation**, l'écouteur étant posé sur la section entière,
 *   et la surbrillance ne s'allume que si le glissé porte des fichiers — sans ce
 *   filtre, faire glisser du texte sélectionné dans une bulle allumerait une zone
 *   de dépôt qui n'accepterait rien ;
 * - **ce qu'une réponse a ouvert** (#268/#269) : voir `Suite` en bas de fichier.
 *
 * ## La réponse s'écrit sous les yeux (#695)
 *
 * `fil.reponseEnCours` est le texte que le canal SSE est en train de rendre : il
 * s'affiche dans une bulle d'interlocuteur ordinaire, à sa place dans le fil,
 * pendant que le curseur dit que ça continue. L'indicateur « … répond… » ne
 * couvre donc plus que l'attente **avant** le premier mot, au lieu de couvrir
 * toute la génération — c'est ce qui distinguait mal une réponse longue d'un
 * blocage.
 *
 * Trois propriétés que le composant tient, et qui se défont facilement :
 *
 * - **le suivi du bas reste un choix du lecteur**. Le fil recolle en bas à
 *   chaque incrément, donc plusieurs fois par seconde, ce qui rendrait
 *   impossible de remonter lire pendant que ça écrit ; c'est le `suit` de
 *   `lib/defilement`, posé sur le geste de défilement et non sur l'arrivée du
 *   contenu, qui l'en empêche — et il valait déjà pour les messages ;
 * - **une réponse figée se voit**. Un flux cassé laisse un texte arrêté que rien
 *   ne distinguerait d'une réponse courte (#693) : la bulle le dit, sous le
 *   texte, à l'endroit où on vient de lire ;
 * - **un échec après l'envoi ne rend pas le brouillon**. Le message est au fil ;
 *   le remettre dans la zone de saisie inviterait à l'envoyer deux fois. C'est
 *   `ErreurReponse` qui sépare ce cas d'un refus, où rien n'est parti.
 *
 * ## Le fil se lit (#697)
 *
 * Trois choses, et la troisième est celle qu'on ne voit pas :
 *
 * - **le Markdown est rendu, du seul côté de l'agent** — voir
 *   `chat/TexteMarkdown`, qui porte les deux décisions de fond (aucune chaîne de
 *   HTML nulle part, et un titre de message n'est pas un titre du document). Ce
 *   que l'utilisateur a tapé se relit tel qu'il l'a tapé ;
 * - **les journées sont séparées** (`lib/journees`) : sans le trait daté, deux
 *   bulles à trois jours d'écart se suivaient comme deux répliques ;
 * - **les états transitoires ont une place, et elle est au PIED du fil.** C'est
 *   le critère « lisibles sans faire sauter le fil », et le défaut n'était pas
 *   qu'ils manquaient — c'est qu'ils s'inséraient **ailleurs que là où on
 *   lit** : « Fil illisible » se posait *au-dessus* de la conversation, donc son
 *   apparition poussait tous les messages vers le bas d'un coup ; l'échec d'envoi
 *   se posait *sous* le composeur, c'est-à-dire sous une barre `sticky`, donc
 *   hors de l'écran et en allongeant la page. Les deux rejoignent l'attente et la
 *   réponse en cours à la fin du `<ol>`, dans l'ordre où ils arrivent : le fil
 *   grandit **par le bas**, ce que le recollement suit déjà, et rien de ce qui
 *   est déjà lu ne bouge. Aucun de ces états n'est annoncé deux fois — la région
 *   live compte les messages (#538), les `role="alert"` disent les fautes.
 *
 * ## Ce qu'il ne fait pas
 *
 * Il ne charge rien : le fil lui est **passé** (`useChat`, historique REST +
 * temps réel WebSocket). Il ne connaît donc ni les endpoints, ni le nom du
 * canal — seulement comment le rendre.
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { BulleFil } from "@/components/chat/BulleFil";
import { SeparateurDeJour } from "@/components/chat/SeparateurDeJour";
import { SourcesDuFil } from "@/components/chat/SourcesDuFil";
import { SourcesDuMessage } from "@/components/chat/SourcesDuMessage";
import { TexteMarkdown } from "@/components/chat/TexteMarkdown";
import { RefusSource } from "@/components/composer/RefusSource";
import {
  IconeFermer,
  IconeRuns,
  IconeTache,
  IconeValidations,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  ChampTexte,
  EnTeteSection,
  LienRenvoi,
  type Icone,
  type Renvoi,
} from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesMessages } from "@/lib/annonces";
import { ErreurReponse, ErreurSource } from "@/lib/api";
import { ascenseurDe, estEnBas } from "@/lib/defilement";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { useHorloge } from "@/lib/horloge";
import { jourDe, libelleDuJour } from "@/lib/journees";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import {
  CHAT_AUTEUR_UTILISATEUR,
  VALIDATION_EN_ATTENTE,
  type MessageChat,
} from "@/lib/types";
import type { Chat, ReponseEnCours } from "@/lib/useChat";
import { useSourcesComposees } from "@/lib/useSourcesComposees";

export function Conversation({
  fil,
  interlocuteur,
  libelle,
  titre,
  icone,
  niveauTitre = 2,
  accueil,
  amorces = [],
  entete,
  bandeau,
  surSaisie,
  className = "",
}: {
  /** Le fil, tel que `useChat` le rend. */
  fil: Chat;
  /**
   * Celui à qui l'on parle, tel qu'il se nomme à l'écran (« dev »,
   * « l'orchestration »). Tous les libellés en dérivent — région live, liste des
   * messages, zone de saisie, indicateur d'attente — pour qu'un lecteur d'écran
   * entende le même nom partout.
   */
  interlocuteur: string;
  /** Le nom du bloc : l'`aria-label` de la section, et ce sous quoi la règle des trois places le recense (#539). */
  libelle: string;
  titre: ReactNode;
  icone?: Icone;
  /** `2` pour un écran, `3` pour un fil posé dans une fiche à onglets. */
  niveauTitre?: 2 | 3;
  /** Le mot d'accueil d'un fil vide — jamais persisté (voir `lib/orchestration`). */
  accueil?: string;
  /** Des amorces proposées tant que la conversation n'a pas commencé. */
  amorces?: string[];
  /** Ce qui se pose à droite de l'en-tête, à côté du badge de connexion. */
  entete?: ReactNode;
  /** Ce qui se pose entre l'en-tête et le fil (la barre de destinataire de `/chat`). */
  bandeau?: ReactNode;
  /**
   * Filtre appliqué à chaque frappe : rend le texte à **garder** dans la zone de
   * saisie. `/chat` s'en sert pour détacher une mention `@agent` du brouillon —
   * un effet de bord assumé, la mention changeant le destinataire au passage.
   */
  surSaisie?: (texte: string) => string;
  className?: string;
}) {
  const {
    messages,
    connecte,
    chargement,
    erreur,
    envoi,
    reponseEnCours,
    envoyer,
    interrompre,
  } = fil;
  const composition = useSourcesComposees();
  const idSaisie = useId();
  const [brouillon, setBrouillon] = useState("");
  /**
   * Ce qui a manqué au dernier envoi, et si le message a **quand même** rejoint
   * le fil. Les deux ensemble parce qu'ils ne se déduisent pas l'un de l'autre
   * et que l'écran a besoin des deux : la cause à afficher, et le fait que la
   * relance consiste à redemander plutôt qu'à renvoyer (#695).
   */
  const [echecEnvoi, setEchecEnvoi] = useState<{
    cause: string;
    acquis: boolean;
  } | null>(null);
  const [refusSource, setRefusSource] = useState<ErreurSource | null>(null);
  const [sourcesOuvertes, setSourcesOuvertes] = useState(false);
  const [survol, setSurvol] = useState(false);
  // L'horloge partagée (#250) : elle ne sert qu'aux séparateurs de journée, qui
  // disent « Aujourd'hui » et « Hier » — donc `null` tant qu'elle n'a pas
  // démarré, et une date absolue en attendant (`lib/journees`).
  const maintenant = useHorloge();
  // La sentinelle de fin de fil : elle ne sert qu'à désigner l'ascenseur qui
  // porte la conversation (`lib/defilement`). Depuis #691 le fil n'a plus de
  // conteneur défilant à lui, donc plus rien à tenir par une `ref`.
  const pied = useRef<HTMLDivElement | null>(null);
  const ascenseur = useRef<HTMLElement | null>(null);
  // Le lecteur suit-il encore la conversation ? Une `ref` et non un état : sa
  // valeur ne change rien à ce qui est rendu, et la relire à chaque événement de
  // défilement ferait un rendu par cran de molette.
  const suit = useRef(true);

  /** Ramène la vue au bas du fil — sans condition, l'appelant ayant tranché. */
  const collerEnBas = useCallback(() => {
    const cadre = ascenseur.current;
    if (cadre === null) return;
    cadre.scrollTop = cadre.scrollHeight;
  }, []);

  // Qui défile, et le lecteur suit-il ? Résolu une fois au montage : l'ascenseur
  // est celui du cadre (`Shell`), il ne change pas sous les pieds du fil.
  useEffect(() => {
    const cadre = ascenseurDe(pied.current);
    ascenseur.current = cadre;
    if (cadre === null) return;
    // Le suivi se décide **avant** l'arrivée du message, sur le geste du
    // lecteur : mesurer après coup dirait toujours « trop loin du bas », le
    // nouveau contenu venant précisément d'allonger la page.
    const surDefilement = () => {
      suit.current = estEnBas(cadre);
    };
    cadre.addEventListener("scroll", surDefilement, { passive: true });
    return () => cadre.removeEventListener("scroll", surDefilement);
  }, []);

  // Le fil suit la conversation : chaque nouveau message, l'indicateur d'attente
  // et **chaque incrément de la réponse en cours** (#695) ramènent la vue en
  // bas, comme une messagerie — **sauf** si le lecteur est remonté lire, auquel
  // cas il garde sa place (note de #265). C'est ce qui fait qu'une réponse qui
  // s'écrit n'arrache pas la lecture de celui qui est remonté : le suivi se
  // décide sur son geste, pas sur l'arrivée du texte.
  useEffect(() => {
    if (suit.current) collerEnBas();
  }, [messages, envoi, reponseEnCours?.texte, collerEnBas]);

  const soumettre = async (texte: string) => {
    const contenu = texte.trim();
    // Un message fait de **sources seules** est légitime : déposer un cahier des
    // charges *est* le message. Sans texte ni source, il n'y a rien à envoyer.
    if ((contenu === "" && composition.sources.length === 0) || envoi) return;
    setEchecEnvoi(null);
    setRefusSource(null);
    setBrouillon("");
    // Écrire, c'est reprendre le fil : quel que soit l'endroit où on lisait, on
    // veut voir partir son propre message. Le suivi reprend donc ici, et c'est
    // le seul endroit où il se rétablit sans geste de défilement.
    suit.current = true;
    try {
      // Le téléversement **avant** l'envoi : le message ne porte que des
      // identifiants, ce qui garantit qu'un fichier n'atterrit jamais ailleurs
      // que dans l'emplacement d'ingestion. Un dépôt refusé (plafond, nom) sort
      // ici, donc avant qu'une ligne ne rejoigne le fil.
      const sources = await composition.declarer();
      await envoyer(contenu, sources);
      // Le succès seul efface la composition : le message est parti, ce qui l'a
      // produit n'a plus de sens dans la zone de saisie.
      composition.vider();
      setSourcesOuvertes(false);
    } catch (e) {
      // Trois régimes, et le troisième est venu avec le direct (#695) — ce qui
      // les sépare n'est pas la gravité mais **ce qu'il reste à faire** :
      //
      // - un refus **de source** porte un motif et souvent un index : il se rend
      //   sur la ligne fautive, sous la saisie ;
      // - un refus d'envoi ordinaire (422, backend injoignable) reste une phrase
      //   sous le formulaire. Dans ces deux cas rien n'est parti, donc le texte
      //   revient dans la zone de saisie (sauf si l'utilisateur a déjà repris la
      //   main) : rien ne se perd, relancer reste un simple Entrée, et les
      //   sources n'ont pas bougé — c'est le sens de « la saisie est conservée »
      //   quand la matière représente le plus gros du geste ;
      // - une **réponse manquée** (`ErreurReponse`) est d'un autre ordre : le
      //   message est **au fil**, la portion reçue est restée à l'écran, et
      //   remettre le texte dans la saisie inviterait à envoyer deux fois la
      //   même demande. On dit ce qui manque, et on invite à relancer — sans
      //   rien recomposer à la place de l'utilisateur.
      if (e instanceof ErreurSource) setRefusSource(e);
      else
        setEchecEnvoi({
          cause: e instanceof Error ? e.message : String(e),
          acquis: e instanceof ErreurReponse,
        });
      if (e instanceof ErreurReponse) {
        // Le message est parti **avec sa matière** : la composition se vide donc
        // comme après un envoi réussi. La garder inviterait à joindre une
        // seconde fois des sources que le fil porte déjà — la même faute que
        // rendre le brouillon, sur l'autre moitié du geste.
        composition.vider();
        setSourcesOuvertes(false);
        return;
      }
      setBrouillon((courant) => (courant === "" ? contenu : courant));
      if (composition.sources.length > 0) setSourcesOuvertes(true);
    }
  };

  /** Le glissé porte-t-il des fichiers ? (un texte sélectionné n'en est pas un) */
  const porteDesFichiers = (transfert: DataTransfer | null) =>
    transfert !== null && Array.from(transfert.types).includes("Files");

  const filVide = !chargement && messages.length === 0;
  // La réponse a-t-elle **commencé** à s'écrire ? Le texte fait foi, pas la
  // présence du flux : entre la trame d'ouverture et le premier incrément, il
  // n'y a rien à montrer, et une bulle vide dirait « il a commencé » alors que
  // c'est encore l'attente que l'indicateur nomme mieux.
  const enTrainDEcrire =
    reponseEnCours !== null && reponseEnCours.texte !== "" ? reponseEnCours : null;

  /**
   * La journée que chaque message **ouvre**, ou `null` s'il ne fait que
   * continuer celle du précédent (#697).
   *
   * Calculé ici plutôt que dans la boucle de rendu : c'est une propriété de la
   * **suite** des messages et non de l'un d'eux, et un message ne peut pas
   * répondre seul à « suis-je le premier de mon jour ? ». Deux règles y sont
   * lisibles d'un coup — jamais de trait devant le premier message (il n'ouvre
   * rien, il occupe une ligne), et un horodatage illisible ne compte pour aucune
   * journée, donc n'en ouvre ni n'en ferme aucune (`lib/journees`).
   */
  const ouvertures = messages.map((message, index) => {
    const jour = jourDe(message.horodatage);
    if (jour === null || index === 0) return null;
    return jour === jourDe(messages[index - 1].horodatage) ? null : jour;
  });

  return (
    <section
      aria-label={libelle}
      className={
        "flex min-w-0 flex-1 flex-col gap-3 rounded-md " +
        (survol
          ? "outline-dashed outline-2 outline-offset-4 outline-accent "
          : "") +
        className
      }
      onDragOver={(e) => {
        if (!porteDesFichiers(e.dataTransfer)) return;
        // Sans `preventDefault`, le navigateur refuse le dépôt et ouvre le
        // fichier dans l'onglet à la place.
        e.preventDefault();
        setSurvol(true);
      }}
      onDragLeave={(e) => {
        // Seul le départ de la **section** compte : sans ce test, passer d'une
        // bulle à sa voisine éteindrait la surbrillance à chaque frontière.
        if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
        setSurvol(false);
      }}
      onDrop={(e) => {
        if (!porteDesFichiers(e.dataTransfer)) return;
        e.preventDefault();
        setSurvol(false);
        composition.deposer(e.dataTransfer.files);
        // Le panneau s'ouvre sur un dépôt : des fichiers ajoutés sous un volet
        // fermé seraient invisibles jusqu'à l'envoi.
        setSourcesOuvertes(true);
      }}
    >
      <EnTeteSection
        niveau={niveauTitre}
        icone={icone}
        titre={titre}
        aside={
          /* Ce qui va **bien** ne s'affiche plus (#691). Le badge disait
             « Temps réel connecté » en permanence, dans l'en-tête du bloc
             principal de l'écran, pour n'apprendre rien — et il le disait une
             seconde fois, la barre supérieure du cadre portant déjà l'état de la
             même socket. Seule la **coupure** reste dite : c'est elle qui
             explique un fil qui ne bouge plus, et elle seule justifie d'occuper
             la place. Même règle que le reste de l'écran (docs/30 §4) : une
             place se gagne, elle ne se garde pas parce qu'on l'avait. */
          <span className="flex flex-wrap items-center gap-2">
            {entete}
            {!connecte && (
              <BadgeEtat ton="attention" pastille pulse>
                Reconnexion…
              </BadgeEtat>
            )}
          </span>
        }
      />
      {/* La région live de l'écran (#538). Elle compte les messages, elle ne les
          relit pas : un agent qui déroule son travail en pousse des rafales, et
          faire lire chaque bulle à voix haute rendrait l'écran impraticable.
          Elle n'est pas montée sous `chargement` : le fil est chargé par le même
          hook que les messages, donc le premier relevé est celui d'un fil vide et
          l'historique qui arrive s'annoncerait comme du direct. */}
      {!chargement && (
        <RegionLive
          libelle={`Activité du fil avec ${interlocuteur}`}
          mesures={[mesureDesMessages(messages.length)]}
        />
      )}
      {bandeau}
      {/* Le fil **n'a plus d'ascenseur à lui** (#691) : plus de `max-h`, plus
          d'`overflow-y`, plus de cadre. Il s'étend, et c'est la page qui le
          parcourt — un seul ascenseur pour un seul contenu, là où la boîte en
          donnait deux, dont l'intérieur ne bougeait pas quand on tournait la
          molette sur la page. Le `flex-1` est ce qui lui fait occuper la
          hauteur disponible quand la conversation est courte : sans lui, un fil
          de deux messages laisserait le composeur au milieu de l'écran et un
          vide dessous (~270 px mesurés avant ce lot).
          La bordure et le fond partent avec la boîte : un cadre autour de ce
          qui occupe déjà tout l'écran ne délimite plus rien. */}
      <ol
        aria-label={`Messages échangés avec ${interlocuteur}`}
        // `justify-end` : la conversation s'empile **depuis le bas**, comme une
        // messagerie — deux messages se posent au-dessus du composeur au lieu de
        // flotter en haut d'un écran vide. Sans effet dès que le fil dépasse la
        // hauteur disponible : il n'y a alors plus d'espace libre à distribuer,
        // et rien n'est donc jamais coupé en haut.
        className="flex flex-1 flex-col justify-end gap-3 py-1"
      >
        {/* Les trois états d'un fil sans messages, sur les jetons du socle comme
            les bulles (#697) : `text-neutral-500` valait 4,83:1 et
            `text-neutral-400` 2,58:1 ailleurs dans le produit — c'est ce que
            `texte-secondaire` remplace partout, avec ses deux thèmes. */}
        {chargement && (
          <li className="text-corps text-texte-secondaire">Chargement du fil…</li>
        )}
        {filVide && accueil !== undefined && (
          <li className="rounded-lg bg-surface-creuse px-3 py-2 text-corps text-texte">
            {accueil}
          </li>
        )}
        {filVide && accueil === undefined && (
          <li className="text-corps text-texte-secondaire">
            Aucun message pour l&apos;instant — écrire ci-dessous engage la
            conversation.
          </li>
        )}
        {messages.map((message, index) => {
          const ouverture = ouvertures[index];
          return (
            <Fragment key={`${message.horodatage}-${index}`}>
              {ouverture !== null && (
                <SeparateurDeJour
                  libelle={libelleDuJour(ouverture, maintenant)}
                />
              )}
              <Bulle message={message} />
            </Fragment>
          );
        })}
        {/* La réponse qui s'écrit (#695) : une bulle d'interlocuteur ordinaire,
            à sa place dans le fil — c'est ce que « on voit la réponse
            s'écrire » veut dire. Elle disparaît sur la trame de clôture, où le
            message persisté prend le relais sans clignotement (`useChat` : la
            fusion écarte le doublon). */}
        {enTrainDEcrire !== null && <BulleEnCours reponse={enTrainDEcrire} />}
        {/* « … répond… » ne couvre plus que l'attente **avant le premier
            mot** : dès qu'un incrément arrive, c'est le texte lui-même qui dit
            que ça travaille. C'était le défaut de départ — un indicateur
            immobile sur toute la génération, où rien ne distinguait une réponse
            longue d'un blocage. Une bulle vide à curseur aurait pu tenir ce
            rôle, mais elle dit « il a commencé » quand rien n'est encore venu.
            `min-h-6` : la ligne réserve sa place et la rend d'un bloc quand la
            bulle la remplace, au lieu de la céder par à-coups (#697). */}
        {envoi && enTrainDEcrire === null && (
          <li className="flex min-h-6 items-center text-corps italic text-texte-secondaire">
            {interlocuteur} répond…
          </li>
        )}
        {/* Les deux fautes, au **pied du fil** et non de part et d'autre de lui
            (#697) : « Fil illisible » se posait au-dessus de la conversation et
            poussait tous les messages d'un coup ; l'échec d'envoi se posait sous
            un composeur `sticky`, donc hors de l'écran. Ici, elles arrivent là
            où l'œil est déjà, et le fil ne grandit que par le bas. */}
        {erreur && (
          <li
            role="alert"
            className="rounded-md border border-alerte bg-alerte-creux px-3 py-2 text-corps text-alerte-texte"
          >
            Fil illisible : {erreur}
          </li>
        )}
        {echecEnvoi !== null && (
          <li className="text-annexe text-alerte-texte" role="alert">
            {echecEnvoi.cause}
            {/* L'invitation à relancer (#695), et **seulement** quand le message
                est acquis : ailleurs, le brouillon est déjà revenu dans la zone
                de saisie et un Entrée suffit — le dire deux fois, dont une à
                côté, ferait douter de ce qui est parti. Elle nomme le geste au
                lieu de l'offrir : renvoyer d'ici recomposerait une demande que
                le fil porte déjà, donc deux fois la même question. */}
            {echecEnvoi.acquis && (
              <>
                {" "}
                Votre message est resté au fil, et ce qui a été reçu de la
                réponse aussi — redemandez pour relancer.
              </>
            )}
          </li>
        )}
      </ol>
      {/* La sentinelle de fin de fil : elle ne rend rien, elle **désigne**
          l'ascenseur qui porte la conversation (`lib/defilement`). Hors du
          `<ol>` à dessein — un `<li>` vide y serait annoncé comme un message de
          plus par les lecteurs d'écran. */}
      <div ref={pied} aria-hidden="true" />
      {filVide && amorces.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {amorces.map((amorce) => (
            <Bouton
              key={amorce}
              variante="contour"
              ton="neutre"
              onClick={() => void soumettre(amorce)}
            >
              {amorce}
            </Bouton>
          ))}
        </div>
      )}
      {/* Le composeur reste **à quai** (#691) : le fil défilant désormais avec la
          page, le laisser en fin de flux obligerait à redescendre tout
          l'historique avant de pouvoir écrire. `sticky bottom-0` le colle au bas
          de l'ascenseur du cadre tant qu'il y a du fil sous lui, et le rend à sa
          place naturelle une fois le bas atteint — donc rien ne recouvre jamais
          le dernier message.
          Le fond opaque n'est pas décoratif : sans lui, les bulles défileraient
          **sous** la zone de saisie, lisibles au travers. */}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void soumettre(brouillon);
        }}
        className="sticky bottom-0 z-10 flex flex-col gap-2 border-t border-bord bg-background pt-3 pb-2"
      >
        {/* `pe-14` : la réserve du bouton flottant de l'assistant (#123), qui
            occupe les 64 derniers pixels de la fenêtre en bas à droite. Tant que
            le composeur était en fin de flux, le `pb-24` du `Shell` l'en tenait
            à distance ; à quai (#691), c'est à cette hauteur-là qu'il vit, et le
            bouton « Envoyer » passait **sous** le flottant — mesuré à 375 px :
            33 px de recouvrement, moitié droite du bouton inerte (le flottant est
            en `z-30`, le composeur en `z-10`).
            La réserve est **inconditionnelle** à dessein : le flottant est calé
            sur la fenêtre, pas sur la colonne, si bien qu'un point de rupture
            aurait raison sur `/chat` — où la colonne de propriétés éloigne le
            composeur — et tort sur l'onglet Chat d'une fiche agent, où il court
            jusqu'au bord (mesuré : bord droit à 1416 px pour un flottant qui
            commence à 1376). Deux surfaces, un seul composant : la règle qui vaut
            pour les deux est celle qui ne dépend pas de la mise en page. */}
        <div className="flex items-end gap-2 pe-14">
          {/* Un `ChampTexte` du socle, au libellé **masqué** (#832) : le nom
              accessible reste « Message à … », la question est portée par le
              `placeholder`. C'est le même contrôle que les champs du produit —
              bord de focus `bord-fort`, contour d'accent au clavier — et non plus
              une recopie de ses classes ; le comportement (Entrée envoie,
              une capture collée devient une source) reste ici. La poignée de
              redimensionnement n'est pas déclarée : le preflight de Tailwind
              rend déjà tout `<textarea>` en `resize: vertical`. */}
          <ChampTexte
            id={idSaisie}
            libelle={`Message à ${interlocuteur}`}
            libelleMasque
            className="min-w-0 flex-1"
            value={brouillon}
            onChange={(e) =>
              setBrouillon(
                surSaisie ? surSaisie(e.target.value) : e.target.value,
              )
            }
            onPaste={(e) => {
              // Coller une image (capture d'écran) est le geste jumeau du
              // glisser-déposer, et le seul par lequel une capture arrive sans
              // passer par un fichier du disque. Le collage de **texte** n'est
              // pas touché : `files` est alors vide.
              const colles = Array.from(e.clipboardData?.files ?? []);
              if (colles.length === 0) return;
              e.preventDefault();
              composition.deposer(colles);
              setSourcesOuvertes(true);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void soumettre(brouillon);
              }
            }}
            rows={2}
            placeholder={`Écrire à ${interlocuteur}… (Entrée envoie, Maj+Entrée saute une ligne)`}
          />
          {/* Pendant qu'une réponse s'écrit, le bouton d'envoi **cède la
              place** à l'arrêt plutôt que de s'y ajouter : l'envoi est de toute
              façon refusé tant qu'un échange est en vol (`soumettre`), donc un
              bouton inerte à côté d'une action possible ne ferait qu'occuper la
              seule place que la main vise. Et l'arrêt arrête pour de bon — il
              annule la génération côté canal (#695) et ce qui a été reçu
              rejoint le fil ; ce n'est pas un simple « je cesse de regarder ». */}
          {envoi ? (
            <Bouton
              variante="contour"
              ton="neutre"
              icone={IconeFermer}
              onClick={interrompre}
            >
              Interrompre
            </Bouton>
          ) : (
            <Bouton
              type="submit"
              disabled={
                brouillon.trim() === "" && composition.sources.length === 0
              }
            >
              Envoyer
            </Bouton>
          )}
        </div>
        <SourcesDuMessage
          composition={composition}
          refus={refusSource}
          occupe={envoi}
          ouvert={sourcesOuvertes}
          onBasculer={() => setSourcesOuvertes(!sourcesOuvertes)}
        />
      </form>
      {/* Le refus qui ne vise **aucune** source en particulier (trop de sources,
          backend injoignable) reste au geste qui l'a produit ; celui qui en vise
          une est rendu sur sa ligne par `SourcesDuMessage`. */}
      {refusSource !== null && refusSource.index === null && (
        <RefusSource refus={refusSource} titre="Sources refusées" />
      )}
    </section>
  );
}

/**
 * La réponse en train de s'écrire (#695) — la bulle de l'interlocuteur, avant
 * qu'elle ne soit un message du fil.
 *
 * Elle emprunte `BulleFil` comme les autres, et c'est le point : ce qui s'écrit
 * doit se poser exactement là où le message se posera, sans quoi la clôture du
 * flux ferait sauter la bulle d'un cadre à l'autre. Elle n'a **pas
 * d'horodatage** — un message en cours n'en a pas encore, et `BulleFil` le
 * prévoit.
 *
 * Le curseur est décoratif (`aria-hidden`) : il dit « ça continue » à l'œil,
 * là où le lecteur d'écran a la région live du fil (#538), qui compte les
 * messages au lieu de relire chaque incrément. Et la mention d'interruption est
 * la moitié qui compte : un texte arrêté ne se distingue pas d'une réponse
 * courte, c'est ce que `FluxInterrompu` a nommé côté canal (#693) et il faut le
 * dire à l'endroit où on vient de lire.
 *
 * ⚠ Elle rend le **même Markdown** que la bulle qui la remplacera (#697), et
 * c'est ce qui la garde muette : rendre le texte brut pendant le flux puis le
 * mettre en forme à la clôture reformaterait la réponse sous les yeux —
 * paragraphes qui se recomposent, blocs de code qui apparaissent, hauteur qui
 * change d'un coup. C'est exactement le saut que le second critère interdit. Le
 * curseur est donc **passé** au rendu, qui le fond dans le dernier bloc plutôt
 * que de l'ajouter dessous.
 */
function BulleEnCours({ reponse }: { reponse: ReponseEnCours }) {
  return (
    <BulleFil auteur={reponse.auteur}>
      <TexteMarkdown
        texte={reponse.texte}
        curseur={
          reponse.figee ? undefined : (
            <span
              aria-hidden="true"
              className={
                "ms-0.5 inline-block h-3.5 w-0.5 translate-y-0.5 rounded-full bg-texte-secondaire " +
                "animate-pulse motion-reduce:animate-none"
              }
            />
          )
        }
      />
      {reponse.figee && (
        <p className="mt-1 text-micro italic text-attention-texte">
          Réponse interrompue — ce qui précède est incomplet.
        </p>
      )}
    </BulleFil>
  );
}

/**
 * Une bulle du fil : l'utilisateur à droite, son interlocuteur à gauche.
 *
 * L'enveloppe — côté, surface, pied — ne vit pas ici mais dans
 * `components/chat/BulleFil` (#483) : le **cadrage** d'un run se lit dans le
 * fil lui aussi, sur cette même page, et deux enveloppes auraient donné deux
 * conversations à l'œil sur un seul écran. Ce qui reste ici est ce qu'un
 * *message* porte, et lui seul — c'est la même raison qui a fait sortir la mise
 * en page de `FilChat` vers ce fichier, appliquée d'un cran plus bas.
 */
function Bulle({ message }: { message: MessageChat }) {
  const utilisateur = message.auteur === CHAT_AUTEUR_UTILISATEUR;
  return (
    <BulleFil
      auteur={message.auteur}
      utilisateur={utilisateur}
      horodatage={message.horodatage}
    >
      {/* Le Markdown du **seul** côté de l'agent (#697) : c'est lui qui produit
          des titres, des listes et du code, et c'est ce que le critère nomme.
          Ce que l'utilisateur a tapé se relit tel qu'il l'a tapé — astérisques
          comprises —, sur la seule surface du produit où il est l'auteur : le
          reformater lui ferait dire autre chose que ce qu'il a écrit. */}
      {message.contenu !== "" &&
        (utilisateur ? (
          <p className="whitespace-pre-wrap break-words">{message.contenu}</p>
        ) : (
          <TexteMarkdown texte={message.contenu} />
        ))}
      {/* Ce que le message a porté, et ce qui en a été lu (#482, critères 1 et
          3) — sous le texte parce que c'est la pièce jointe qui accompagne le
          propos, et non l'inverse. Rend `null` quand il n'y a aucune source :
          une bulle ordinaire est exactement celle d'avant ce lot. */}
      <SourcesDuFil message={message} />
      {/* Ce que la réponse a ouvert (#269), du seul côté de l'interlocuteur :
          c'est sa réponse qui ouvre un run, jamais la demande
          (`ServiceChat._repondre`, #268) — et le fond plein de la bulle
          utilisateur n'est pas une surface pour du texte secondaire et des
          liens. */}
      {!utilisateur && <Suite message={message} />}
    </BulleFil>
  );
}

/**
 * Ce qu'un message a **ouvert** — le troisième critère de #269, et le seul qui
 * ne se lise pas dans le texte de la réponse.
 *
 * Deux sources, et l'ordre entre elles est le contenu du dessin. Le message
 * porte lui-même `run_id`/`tache_id` (#268) : c'est le rattachement, il est
 * persisté, il survit au rechargement — c'est lui qui décide s'il y a quelque
 * chose à montrer. Le reste (combien de tâches ce run a produites, s'il attend
 * un arbitrage) se lit dans l'**état temps réel du projet actif**, qui est vivant
 * là où le message est figé : une réponse écrite il y a dix minutes ne pouvait
 * pas savoir qu'une validation serait demandée depuis.
 *
 * Rien à montrer ⇒ rien à rendre : un message ordinaire ne rattache rien (les
 * deux champs sont vides), et ne doit pas laisser un cadre vide sous sa bulle —
 * même règle que le détail d'une tâche (`lib/detailTache`) et que les sources
 * d'un message (#482).
 */
function Suite({ message }: { message: MessageChat }) {
  const { taches, validations } = useEtatGlobal();
  const runId = message.run_id ?? "";
  const tacheId = message.tache_id ?? "";
  if (runId === "" && tacheId === "") return null;

  const duRun = taches.filter((tache) => tache.run_id === runId);
  const enAttente = validations.filter(
    (validation) =>
      validation.statut === VALIDATION_EN_ATTENTE &&
      (validation.run_id === runId ||
        (tacheId !== "" && validation.tache_id === tacheId)),
  );
  const run = runId === "" ? undefined : hrefRun(runId);
  const arbitrages = entreeParLibelle("Validations");

  // Le renvoi vers la tâche, c'est le renvoi vers son run : les trois lectures
  // d'un run (pipeline, Kanban, journal) sont une bascule et non trois routes
  // (`lib/vuesRun`), il n'y a donc pas d'URL qui ouvre une tâche.
  const renvois: Renvoi[] = [];
  if (run !== undefined) renvois.push({ href: run, libelle: "Voir le run" });
  if (enAttente.length > 0 && arbitrages !== undefined) {
    renvois.push({
      href: arbitrages.href,
      libelle:
        enAttente.length === 1
          ? "Trancher la validation"
          : `Trancher les ${enAttente.length} validations`,
    });
  }

  // Sur les jetons du socle depuis #697, comme le reste de la bulle. Le
  // `text-[11px]` d'origine **était** le pas `text-micro` (0,6875 rem), écrit à
  // la main : c'est le symptôme que la doc du socle nomme pour dire qu'un pas
  // manque — sauf qu'ici il ne manquait pas.
  return (
    <div className="mt-2 flex flex-col gap-1 border-t border-bord pt-2">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-texte-secondaire">
        {runId !== "" && (
          <span className="inline-flex items-center gap-1">
            <IconeRuns className="size-3.5 shrink-0" />
            Run <span className="font-mono">{runId}</span>
          </span>
        )}
        {duRun.length > 0 && (
          <span className="inline-flex items-center gap-1">
            <IconeTache className="size-3.5 shrink-0" />
            {duRun.length === 1 ? "1 tâche ouverte" : `${duRun.length} tâches ouvertes`}
          </span>
        )}
        {tacheId !== "" && duRun.length === 0 && (
          <span className="inline-flex items-center gap-1">
            <IconeTache className="size-3.5 shrink-0" />
            Tâche <span className="font-mono">{tacheId}</span>
          </span>
        )}
        {enAttente.length > 0 && (
          <span className="inline-flex items-center gap-1 text-attention-texte">
            <IconeValidations className="size-3.5 shrink-0" />
            {enAttente.length === 1
              ? "1 validation attend votre arbitrage"
              : `${enAttente.length} validations attendent votre arbitrage`}
          </span>
        )}
      </p>
      {renvois.length > 0 && (
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1">
          {renvois.map((renvoi) => (
            <LienRenvoi key={renvoi.href} renvoi={renvoi} />
          ))}
        </p>
      )}
    </div>
  );
}
