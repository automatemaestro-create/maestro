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
 * ## Ce qu'il ne fait pas
 *
 * Il ne charge rien : le fil lui est **passé** (`useChat`, historique REST +
 * temps réel WebSocket). Il ne connaît donc ni les endpoints, ni le nom du
 * canal — seulement comment le rendre.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { BulleFil } from "@/components/chat/BulleFil";
import { SourcesDuFil } from "@/components/chat/SourcesDuFil";
import { SourcesDuMessage } from "@/components/chat/SourcesDuMessage";
import { RefusSource } from "@/components/composer/RefusSource";
import { IconeRuns, IconeTache, IconeValidations } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  EnTeteSection,
  LienRenvoi,
  type Icone,
  type Renvoi,
} from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesMessages } from "@/lib/annonces";
import { ErreurSource } from "@/lib/api";
import { ascenseurDe, estEnBas } from "@/lib/defilement";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle, hrefRun } from "@/lib/navigation";
import {
  CHAT_AUTEUR_UTILISATEUR,
  VALIDATION_EN_ATTENTE,
  type MessageChat,
} from "@/lib/types";
import type { Chat } from "@/lib/useChat";
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
  const { messages, connecte, chargement, erreur, envoi, envoyer } = fil;
  const composition = useSourcesComposees();
  const [brouillon, setBrouillon] = useState("");
  const [erreurEnvoi, setErreurEnvoi] = useState<string | null>(null);
  const [refusSource, setRefusSource] = useState<ErreurSource | null>(null);
  const [sourcesOuvertes, setSourcesOuvertes] = useState(false);
  const [survol, setSurvol] = useState(false);
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

  // Le fil suit la conversation : chaque nouveau message (et l'indicateur
  // d'attente) ramène la vue en bas, comme une messagerie — **sauf** si le
  // lecteur est remonté lire, auquel cas il garde sa place (note de #265).
  useEffect(() => {
    if (suit.current) collerEnBas();
  }, [messages, envoi, collerEnBas]);

  const soumettre = async (texte: string) => {
    const contenu = texte.trim();
    // Un message fait de **sources seules** est légitime : déposer un cahier des
    // charges *est* le message. Sans texte ni source, il n'y a rien à envoyer.
    if ((contenu === "" && composition.sources.length === 0) || envoi) return;
    setErreurEnvoi(null);
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
      // Deux régimes qui ne s'affichent pas au même endroit : un refus **de
      // source** porte un motif et souvent un index — il se rend sur la ligne
      // fautive, sous la saisie —, là où un échec d'envoi ordinaire (502, panne
      // réseau) reste une phrase sous le formulaire.
      if (e instanceof ErreurSource) setRefusSource(e);
      else setErreurEnvoi(e instanceof Error ? e.message : String(e));
      // Le texte revient dans la zone de saisie (sauf si l'utilisateur a déjà
      // repris la main) : rien ne se perd, relancer reste un simple Entrée. Les
      // sources, elles, n'ont pas bougé — c'est le sens de « la saisie est
      // conservée » quand la matière représente le plus gros du geste.
      setBrouillon((courant) => (courant === "" ? contenu : courant));
      if (composition.sources.length > 0) setSourcesOuvertes(true);
    }
  };

  /** Le glissé porte-t-il des fichiers ? (un texte sélectionné n'en est pas un) */
  const porteDesFichiers = (transfert: DataTransfer | null) =>
    transfert !== null && Array.from(transfert.types).includes("Files");

  const filVide = !chargement && messages.length === 0;

  return (
    <section
      aria-label={libelle}
      className={
        "flex min-w-0 flex-1 flex-col gap-3 rounded-md " +
        (survol
          ? "outline-dashed outline-2 outline-offset-4 outline-emerald-500 "
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
      {erreur && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Fil illisible : {erreur}
        </p>
      )}
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
        {chargement && (
          <li className="text-sm text-neutral-500">Chargement du fil…</li>
        )}
        {filVide && accueil !== undefined && (
          <li className="rounded-lg bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-200">
            {accueil}
          </li>
        )}
        {filVide && accueil === undefined && (
          <li className="text-sm text-neutral-500">
            Aucun message pour l&apos;instant — écrire ci-dessous engage la
            conversation.
          </li>
        )}
        {messages.map((message, index) => (
          <Bulle key={`${message.horodatage}-${index}`} message={message} />
        ))}
        {envoi && (
          <li className="text-sm italic text-neutral-500">
            {interlocuteur} répond…
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
          <textarea
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
            aria-label={`Message à ${interlocuteur}`}
            className={
              "w-full resize-y rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm " +
              "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none " +
              "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600"
            }
          />
          <Bouton
            type="submit"
            disabled={brouillon.trim() === "" && composition.sources.length === 0}
            occupe={envoi}
          >
            {envoi ? "Envoi…" : "Envoyer"}
          </Bouton>
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
      {erreurEnvoi && (
        <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreurEnvoi}
        </p>
      )}
    </section>
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
      {message.contenu !== "" && (
        <p className="whitespace-pre-wrap break-words">{message.contenu}</p>
      )}
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

  return (
    <div className="mt-2 flex flex-col gap-1 border-t border-neutral-200/70 pt-2 dark:border-neutral-700/70">
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-neutral-500 dark:text-neutral-400">
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
          <span className="inline-flex items-center gap-1 text-amber-700 dark:text-amber-300">
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
