"use client";

/**
 * La page Chat de la Control Tower — le chat **global**, non lié à un agent
 * (#269, lot 2 de #244), et le **cadrage d'un run** (#483, lot 2 de #481).
 *
 * Le chat *par agent* (#85) a rejoint la fiche agent en onglet (#190) : c'était
 * la même intention que « Agents » vue par une autre facette, et le menu n'en
 * garde qu'une par intention. Le chat global, lui, en est une distincte —
 * s'adresser à l'orchestration plutôt qu'à un exécutant, « poser une demande
 * sans avoir à choisir d'abord à qui la poser ». L'entrée de menu et cette route
 * lui gardaient leur place depuis #190, annoncées et inertes ; c'est ce lot qui
 * les remplit, sur le canal `orchestrateur` livré par le lot 1 (#268).
 *
 * **Le cadrage se décide ici aussi**, et les deux lots se sont rejoints sur
 * cette page comme `components/chat/CadrageDansLeFil` l'annonçait — « les deux
 * fils se rejoindront sur cette page, ils ne se remplacent pas ». Le brief
 * (#320), ses questions de clarification (#321) et la décision qui les clôt ont
 * quitté `/brief` pour la conversation (arbitrage du 2026-08-24, revue #470,
 * docs/29 §4) : un point de contrôle ne vaut que s'il est **lu**, et une
 * décision se lit mieux là où on a la conversation qui l'a produite que dans un
 * écran qu'il faut aller ouvrir. C'est un déménagement, pas une levée : la
 * décision **D5** de #218 tient, rien n'est décomposé avant validation humaine,
 * et les deux routes de §6.10 sont celles d'avant.
 *
 * **Le cadrage passe avant le fil quand il a quelque chose à dire**, et l'ordre
 * est le contenu de la décision : c'est un run **arrêté** qui attend là, et les
 * trois surfaces d'acheminement (§2.1 — panneau du tableau de bord, cloche,
 * carte de run) mènent ici par `PAGE_DU_CADRAGE`. Y arriver pour trouver le
 * brief sous le pli reviendrait à éteindre le renvoi qui vient de nous amener.
 *
 * ⚠ **Ce que #691 a changé, et pourquoi.** Jusqu'à la revue du 2026-08-28 ce
 * bloc occupait le haut de l'écran **même la file vide**, où il disait *pourquoi*
 * elle l'était — c'est-à-dire dans le cas nominal, où il repoussait vers le bas
 * le seul élément que cette page existe pour porter. Le chat est la porte
 * d'entrée du produit depuis #666 ; il ne pouvait pas rester le second bloc de
 * son propre écran. La file vide n'est donc plus **effacée** mais **déplacée**,
 * dans la colonne de propriétés — la seule des trois places sans plafond
 * (docs/30 §4), et l'une des deux seules réponses admises à un corps qui déborde
 * avec le second niveau ; jamais un retrait d'information. Ce qui n'a pas bougé :
 * la page ne **recopie** pas la règle de `runsEnAttente`, elle l'**appelle** —
 * deux formulations de « ce qui attend » finiraient par ne plus désigner la même
 * file, et l'écran montrerait le cadrage au moment où il n'y en a pas.
 *
 * ## La conversation prend l'écran (#691)
 *
 * Le fil n'a **plus d'ascenseur à lui**. `components/Conversation` le bornait à
 * `max-h-[60vh]` dans une boîte défilante : deux ascenseurs pour un contenu,
 * dont l'intérieur ne bougeait pas quand on tournait la molette sur la page, et
 * ~270 px de vide sous le composeur. Il s'étend désormais, et c'est l'ascenseur
 * du `Shell` qui le parcourt — d'où le `flex-1` **sans** `min-h-0` sur la racine
 * de cette page (voir le commentaire du rendu : c'est le couple qui fait à la
 * fois « remplir » et « déborder », et l'ajout de `min-h-0` rendrait l'inverse).
 *
 * ## Trois choix, et ce qu'ils écartent
 *
 * **Un seul composant de fil.** La mise en page conversationnelle vit dans
 * `components/Conversation`, que l'onglet Chat d'un agent monte aussi
 * (`components/FilChat`). C'est l'arbitrage de #620 rendu concret : ce milestone
 * passe le premier, le lot 13 de « Control Tower v3 — agents » (#265) réutilisera
 * ce composant au lieu de le fournir. Et jusqu'à la **bulle** : celle du fil et
 * celle du cadrage sont la même (`components/chat/BulleFil`), sans quoi un seul
 * écran donnerait deux conversations à l'œil.
 *
 * **La mention change de destinataire, elle ne duplique rien.** `@dev …` envoie
 * dans le fil de `dev` — celui-là même que sert sa fiche —, et l'écran bascule
 * dessus sans navigation. Copier le message dans les deux fils aurait donné deux
 * historiques d'une même conversation, désaccordés dès le premier rechargement :
 * c'est exactement ce que le critère « les deux ne divergent pas » interdit
 * (voir `lib/orchestration`).
 *
 * **Le direct passe par le flux SSE — et pas par cette page (#695).** Le lot 1
 * avait construit le canal de streaming (docs/05 §6.5) ; deux raisons l'ont tenu
 * sans consommateur, et les deux sont levées, chacune à sa façon. Il ne savait
 * pas porter de **sources** — son `contenu` voyageant en paramètre d'URL quand
 * `POST …/messages` accepte les `sources[]` de #482, si bien qu'y basculer le fil
 * aurait perdu les pièces jointes en silence, c'est-à-dire échangé un rendu
 * incrémental contre une fonctionnalité : #692 lui a donné `POST …/flux`, dont le
 * corps est exactement celui du POST. Et le brancher **ici** aurait été un second
 * chemin d'envoi côté navigateur, donc deux façons de parler à un fil : il est
 * donc branché dans **`lib/useChat`**, par où passent les trois surfaces de fil,
 * si bien qu'il **remplace** le chemin d'envoi au lieu de s'y ajouter. La règle
 * n'a pas bougé — c'est l'endroit du branchement qui la respecte, pas
 * l'abstention. Cette page, elle, n'a rien eu à changer : elle passe un fil à
 * `Conversation`, et c'est le fil qui sait désormais s'écrire.
 *
 * ## Repartir de zéro, retrouver ce qui précède (#696)
 *
 * Le fil était un JSONL éternel par agent avant #694 ; l'API sait depuis le
 * découper en **conversations**, et c'est ici que l'écran s'en sert. Deux gestes,
 * une seule place : « Nouvelle conversation » et l'historique vont dans la
 * **colonne de propriétés**, parce qu'une conversation ouverte est une propriété
 * du fil et que c'est la seule des trois places sans plafond (docs/30 §4). En
 * faire un quatrième bloc de corps ferait rougir `sobriete.test.tsx`, et ce
 * serait le bon signal.
 *
 * Trois choses à ne pas défaire :
 *
 * - **l'historique est celui du destinataire courant.** Une mention `@dev`
 *   change de fil (voir plus haut), donc de conversations : les mélanger
 *   donnerait une liste où l'on ouvrirait le fil d'un autre agent sans le savoir.
 *   C'est `useChat(destinataire)` qui le tient — l'écran ne trie rien ;
 * - **rien n'est filtré par le projet actif.** Le fil reste transverse (#281), et
 *   la conversation ouverte survit au changement de projet bien que la `key` du
 *   `Shell` remonte tout ce qui est dessous : elle est relue de la mémoire du
 *   poste (`lib/conversationOuverte`) et non tenue dans l'état de cette page, qui
 *   ne survivrait pas au remontage ;
 * - **la liste ne se recopie pas.** Titre, date et nombre de messages viennent de
 *   la carte servie par l'API (`ConversationChat`, §6.14), jamais d'un décompte
 *   refait ici sur les messages chargés — on n'en a qu'un fil sur N.
 */

import { useMemo, useState } from "react";

import { FilDeCadrage } from "@/components/chat/FilDeCadrage";
import { Conversation } from "@/components/Conversation";
import {
  IconeAgent,
  IconeChat,
  IconeFermer,
  IconeHistorique,
  IconeObjectif,
  IconePlus,
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
import { runsEnAttente } from "@/lib/brief";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { hrefRun } from "@/lib/navigation";
import {
  ACCUEIL_ORCHESTRATION,
  AGENT_ORCHESTRATION,
  AMORCES_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  destinatairesDuFil,
  mentionEnTete,
} from "@/lib/orchestration";
import type { ConversationChat, MessageChat } from "@/lib/types";
import { useChat, type Chat } from "@/lib/useChat";

export default function PageChat() {
  const { agents, taches, projet, executions } = useEtatGlobal();
  const [destinataire, setDestinataire] = useState(AGENT_ORCHESTRATION);
  // Le projet **de cette fenêtre** part avec chaque message (#683) : c'est ce
  // qui rattache au projet actif le run que l'orchestration ouvre, et donc ce
  // qui le fait apparaître dans « Runs » au lieu de nulle part. Il voyage avec
  // l'envoi, pas avec le fil, qui reste transverse (`lib/useChat`).
  const fil = useChat(destinataire, projet.id);

  // L'orchestration en tête, puis les agents du parc — d'où elle est retirée,
  // le parc réel la portant comme acteur du journal (`destinatairesDuFil` en
  // donne la raison, #671).
  const destinataires = useMemo(
    () => destinatairesDuFil(agents.map((agent) => agent.nom)),
    [agents],
  );

  const global = destinataire === AGENT_ORCHESTRATION;
  const interlocuteur = global ? INTERLOCUTEUR_ORCHESTRATION : destinataire;

  // Y a-t-il un run **arrêté** qui attend un geste ? La même règle que le fil de
  // cadrage lui-même (`runsEnAttente`, `lib/brief`) — appelée, jamais recopiée :
  // deux formulations de « ce qui attend » finiraient par ne plus désigner la
  // même file, et l'écran montrerait le cadrage au moment où il n'y en a pas.
  const cadrageEnAttente = runsEnAttente(executions).length > 0;

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
    // Le corps à gauche, la colonne de propriétés à droite (#539) — mais
    // **la conversation seule** occupe désormais le corps, et elle prend la
    // hauteur (#691).
    //
    // `flex-1` sans `min-h-0` : c'est ce couple qui donne les deux comportements
    // demandés d'un seul coup. `flex-1` fait remplir la hauteur du cadre quand la
    // conversation est courte — plus de vide sous le composeur ; l'absence de
    // `min-h-0` (donc `min-height: auto`) l'empêche de se comprimer sous son
    // contenu quand elle est longue, si bien qu'elle déborde et que c'est
    // l'ascenseur du `Shell` qui la parcourt. Ajouter `min-h-0` ici, par symétrie
    // avec la chaîne du Kanban (#248), rendrait exactement l'inverse.
    <div className="flex flex-1 flex-col gap-6 @4xl:flex-row">
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {/* Le cadrage garde la **première place quand il a quelque chose à
            dire**, et la rend quand il n'a rien (#691) : c'est un run arrêté qui
            attend là, et les trois surfaces d'acheminement de §2.1 mènent ici par
            `PAGE_DU_CADRAGE` — y arriver pour trouver le brief sous le pli
            éteindrait le renvoi qui vient de nous amener.
            ⚠ Ce qui change par rapport à #483, et la raison : ce bloc occupait le
            haut de l'écran **même vide**, pour dire pourquoi il l'était — c'est-
            à-dire le cas nominal, où il repoussait vers le bas le seul élément
            que la page existe pour porter. Il ne disparaît pas pour autant : à
            file vide il passe dans la colonne de propriétés, la seule des trois
            places sans plafond (docs/30 §4). Les deux réponses à un corps qui
            déborde sont une colonne ou un second niveau, jamais un retrait
            d'information — et « pourquoi la file est vide » reste écrit. */}
        {cadrageEnAttente && (
          <section
            aria-label="Cadrage en attente"
            className="flex min-w-0 flex-col gap-3"
          >
            <EnTeteSection titre="Cadrage en attente" icone={IconeObjectif} />
            <p className="text-corps text-neutral-600 dark:text-neutral-400">
              Avant de parler du travail, on le cadre : le brief se relit et se
              corrige ici même, les questions du Chef de projet s&apos;y
              répondent, et l&apos;accord — ou le refus — s&apos;y donne. Rien
              n&apos;est décomposé avant.
            </p>
            <FilDeCadrage />
          </section>
        )}
        <Conversation
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
                  Ce message part dans le fil de <strong>{destinataire}</strong>{" "}
                  — le même que sert sa fiche. Rien n&apos;est recopié ici.
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
      </div>
      {/* La troisième place — celle qui s'allonge sans plafond (docs/30 §4).
          Largeur fixe en grand format : la conversation garde le reste, au lieu
          du tiers que lui laissait la grille de #269.
          **Collante et bornée**, exactement comme celle de `/couts` et pour la
          raison qu'y garde `sobriete.test.tsx` : depuis que le fil prend
          l'écran, la page défile pour de bon, et une colonne posée en haut
          d'une page de 1400 px s'en irait avec elle — on perdrait le choix du
          destinataire au bout de dix messages. La collante appelle le plafond :
          une surface collante plus haute que la fenêtre voit son bas rester
          définitivement sous le pli, aucun défilement ne le ramenant puisque
          c'est le défilement qui la fige (classe de bug de #306). Les deux
          utilitaires vont donc ensemble, et jamais l'un sans l'autre. */}
      <aside
        aria-label="Propriétés du fil"
        className={
          "flex min-w-0 flex-col gap-6 @4xl:w-80 @4xl:shrink-0 " +
          "@4xl:sticky @4xl:top-20 @4xl:max-h-[calc(100dvh-6rem)] @4xl:self-start @4xl:overflow-y-auto"
        }
      >
        {/* Le cadrage à file vide (#691) : il ne disparaît pas, il change de
            place — voir le corps ci-dessus. Ce qu'il dit ici est ce qu'il disait
            là-bas : pourquoi la file est vide, et par où on y met quelque chose. */}
        {!cadrageEnAttente && (
          <Carte densite="aeree">
            <EnTeteSection titre="Cadrage en attente" icone={IconeObjectif} />
            <div className="mt-3">
              <FilDeCadrage />
            </div>
          </Carte>
        )}
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
        {/* Après « Parler à », et l'ordre est causal : on choisit d'abord à qui
            l'on parle, la liste ci-dessous étant celle de *son* fil (#696). */}
        <Carte densite="aeree">
          <EnTeteSection titre="Conversations" icone={IconeHistorique} />
          <ConversationsDuFil fil={fil} />
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
 * Le nom d'une conversation dont personne n'a encore rien dit — celui que
 * l'écran donne, l'API laissant le titre **vide** tant que rien n'a été dit
 * (`titre_conversation`, §6.14). Nommer ici plutôt que là-bas est le bon
 * partage : le backend ne peut pas inventer une phrase qui n'a pas été
 * prononcée, l'écran, lui, sait de quoi il s'agit — et il n'y en a jamais
 * qu'une, l'ouverture étant idempotente sur une conversation déjà vierge.
 *
 * Ce n'est **pas** « Nouvelle conversation », qui est le nom du bouton juste
 * au-dessus : deux commandes voisines sous un même nom accessible ne se
 * distinguent plus à l'oreille, et un test qui viserait l'une prendrait l'autre.
 */
const CONVERSATION_VIERGE = "Conversation vierge";

/**
 * L'historique du fil et le geste qui en ouvre un neuf (#696) — les deux moitiés
 * de « démarrer un nouveau chat et voir l'historique », dans la colonne de
 * propriétés (voir l'en-tête de la page).
 *
 * Le composant ne **décide** de rien : `useChat` tient la conversation ouverte,
 * la liste et les deux verbes ; l'API tient l'ordre (la plus récente d'abord) et
 * l'idempotence de l'ouverture. Ce qui reste ici est ce qui se voit — comment on
 * appelle un fil vierge, et lequel porte la marque du fil ouvert.
 */
function ConversationsDuFil({ fil }: { fil: Chat }) {
  const maintenant = useHorloge();
  const [ouverture, setOuverture] = useState(false);

  const ouvrirNeuve = async () => {
    setOuverture(true);
    try {
      await fil.nouvelleConversation();
    } finally {
      setOuverture(false);
    }
  };

  return (
    <>
      <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
        Chaque conversation garde son fil. En ouvrir une neuve ne touche pas à la
        précédente, qui reste listée ici et se rouvre d&apos;un clic.
      </p>
      <Bouton
        variante="contour"
        ton="accent"
        icone={IconePlus}
        occupe={ouverture}
        onClick={() => void ouvrirNeuve()}
        className="mt-3 w-full"
      >
        Nouvelle conversation
      </Bouton>
      {/* Liste vide = pas encore chargée : l'API n'en rend jamais aucune, un
          agent ayant toujours au moins sa conversation `origine` (§6.14). On ne
          rend donc rien plutôt qu'un « aucune conversation » qui serait faux. */}
      {fil.conversations.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1">
          {fil.conversations.map((carte) => (
            <li key={carte.id}>
              <LigneConversation
                carte={carte}
                ouverte={carte.id === fil.conversation}
                maintenant={maintenant}
                onClick={() => fil.ouvrirConversation(carte.id)}
              />
            </li>
          ))}
        </ul>
      )}
    </>
  );
}

/**
 * Une conversation dans l'historique : son sujet, quand elle a bougé pour la
 * dernière fois, et combien elle porte de messages.
 *
 * `aria-current` et non un simple fond coloré : « celle que je lis » doit
 * s'entendre autant qu'elle se voit, et c'est l'attribut que les lecteurs
 * d'écran annoncent pour l'élément courant d'une liste.
 */
function LigneConversation({
  carte,
  ouverte,
  maintenant,
  onClick,
}: {
  carte: ConversationChat;
  ouverte: boolean;
  maintenant: number | null;
  onClick: () => void;
}) {
  const titre = carte.titre === "" ? CONVERSATION_VIERGE : carte.titre;
  const quand = formatHeureRelative(carte.derniere, maintenant);
  const combien =
    carte.messages === 0
      ? "vide"
      : carte.messages === 1
        ? "1 message"
        : `${carte.messages} messages`;
  return (
    <button
      type="button"
      onClick={onClick}
      aria-current={ouverte ? "true" : undefined}
      className={
        "flex w-full cursor-pointer flex-col items-start gap-0.5 rounded-md px-2 py-1.5 text-left " +
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent " +
        (ouverte
          ? "bg-sky-50 text-sky-900 dark:bg-sky-950 dark:text-sky-100"
          : "hover:bg-survol")
      }
    >
      <span className="w-full truncate text-corps font-medium">{titre}</span>
      <span className="text-annexe text-neutral-500 dark:text-neutral-400">
        {quand === "" ? combien : `${quand} · ${combien}`}
      </span>
    </button>
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
          message="Rien encore. Dites le travail à faire — « ajoute la pagination à la liste des projets » — et l'orchestrateur vous proposera un run : une fois que vous l'aurez approuvé, il apparaît ici avec ses tâches."
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
