"use client";

/**
 * Le fil de chat utilisateur ↔ agent (ticket #85) : les bulles du fil
 * persisté, la saisie et l'envoi — branché sur l'API du lot 1 (#84,
 * `/api/chat`) via `useChat` (historique REST + temps réel WebSocket).
 *
 * La réponse est produite dans la requête d'envoi (POC mono-process) : le
 * message utilisateur apparaît dès sa diffusion sur le bus, l'indicateur
 * « répond… » couvre l'attente de la réponse. Un échec de réponse (502) ne
 * perd rien : le message est déjà au fil, l'erreur invite à relancer.
 *
 * **Depuis #482 un message peut porter des sources** — fichiers glissés ou
 * choisis, dossier du poste, adresse collée —, premier lot du déménagement de
 * `/composer` dans le fil (#481). Trois choses à ne pas défaire :
 *
 * - la matière passe par la **chaîne d'ingestion existante** et par elle seule :
 *   les octets partent à `POST /api/sources` (#317), le message ne porte que les
 *   identifiants rendus, et c'est le backend qui résout, plafonne et lit
 *   (`maestro.sources.composer_sources`). L'écran ne juge rien ;
 * - un **refus reste dans le fil**, sur la source qu'il vise quand l'API en donne
 *   l'index. C'est le critère 2 : un plafond dépassé ne doit pas finir dans une
 *   console. La composition est **conservée** dans tous les cas — un cahier des
 *   charges de trois documents effacé par un refus est un dépôt qu'on ne refait
 *   pas ;
 * - le **glisser-déposer vise toute la conversation**, pas une zone à trouver.
 *   L'écouteur est posé sur la section entière, et la surbrillance ne s'allume
 *   que si le glissé porte des fichiers (`types` contient `Files`) : sans ce
 *   filtre, sélectionner du texte dans une bulle et le faire glisser allumerait
 *   une zone de dépôt qui n'accepterait rien.
 */

import { useEffect, useRef, useState } from "react";

import { SourcesDuFil } from "@/components/chat/SourcesDuFil";
import { SourcesDuMessage } from "@/components/chat/SourcesDuMessage";
import { RefusSource } from "@/components/composer/RefusSource";
import { IconeChat } from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { BadgeEtat, Bouton, EnTeteSection } from "@/components/Primitives";
import { RegionLive } from "@/components/RegionLive";
import { mesureDesMessages } from "@/lib/annonces";
import { ErreurSource } from "@/lib/api";
import { formatDateHeure, formatHeure } from "@/lib/format";
import { CHAT_AUTEUR_UTILISATEUR, type MessageChat } from "@/lib/types";
import { useChat } from "@/lib/useChat";
import { useSourcesComposees } from "@/lib/useSourcesComposees";

export function FilChat({
  agent,
  role,
}: {
  agent: string;
  /**
   * Le rôle de l'agent, quand l'appelant le connaît déjà. Facultatif depuis
   * #190 : l'onglet Chat d'une fiche agent n'a que le nom en main, et le rôle
   * s'y lit sur l'onglet Profil — le charger ici ne vaudrait pas la requête.
   */
  role?: string;
}) {
  const { messages, connecte, chargement, erreur, envoi, envoyer } =
    useChat(agent);
  const composition = useSourcesComposees();
  const [brouillon, setBrouillon] = useState("");
  const [erreurEnvoi, setErreurEnvoi] = useState<string | null>(null);
  const [refusSource, setRefusSource] = useState<ErreurSource | null>(null);
  const [sourcesOuvertes, setSourcesOuvertes] = useState(false);
  const [survol, setSurvol] = useState(false);
  const fil = useRef<HTMLOListElement | null>(null);

  // Le fil suit la conversation : chaque nouveau message (et l'indicateur
  // d'attente) ramène la vue en bas, comme une messagerie.
  useEffect(() => {
    const conteneur = fil.current;
    if (conteneur !== null) conteneur.scrollTop = conteneur.scrollHeight;
  }, [messages, envoi]);

  const soumettre = async () => {
    const contenu = brouillon.trim();
    // Un message fait de **sources seules** est légitime : déposer un cahier des
    // charges *est* le message. Sans texte ni source, il n'y a rien à envoyer.
    if ((contenu === "" && composition.sources.length === 0) || envoi) return;
    setErreurEnvoi(null);
    setRefusSource(null);
    setBrouillon("");
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

  return (
    <section
      aria-label={`Chat avec ${agent}`}
      className={
        "flex min-w-0 flex-1 flex-col gap-3 rounded-md " +
        (survol ? "outline-dashed outline-2 outline-offset-4 outline-emerald-500" : "")
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
      {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
      <EnTeteSection
        niveau={3}
        icone={IconeChat}
        titre={`Conversation${role ? ` · ${role}` : ""}`}
        aside={
          <BadgeEtat
            ton={connecte ? "positif" : "attention"}
            pastille
            pulse={!connecte}
          >
            {connecte ? "Temps réel connecté" : "Reconnexion…"}
          </BadgeEtat>
        }
      />
      {/* La région live de l'écran (#538). Elle compte les messages, elle ne les
          relit pas : un agent qui déroule son travail en pousse des rafales, et
          faire lire chaque bulle à voix haute rendrait l'écran impraticable —
          c'est exactement le « journal » que le ticket refuse. Le fil lui-même
          reste lisible à la demande, dans sa liste juste en dessous.
          Elle n'est pas montée sous `chargement` : le fil est chargé par le même
          hook que les messages, donc le premier relevé est celui d'un fil vide et
          l'historique qui arrive s'annoncerait comme du direct. */}
      {!chargement && (
        <RegionLive
          libelle={`Activité du fil avec ${agent}`}
          mesures={[mesureDesMessages(messages.length)]}
        />
      )}
      {erreur && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Fil illisible : {erreur}
        </p>
      )}
      <ol
        ref={fil}
        aria-label={`Messages échangés avec ${agent}`}
        className="flex max-h-[60vh] min-h-64 flex-col gap-2 overflow-y-auto rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950"
      >
        {chargement && (
          <li className="text-sm text-neutral-500">Chargement du fil…</li>
        )}
        {!chargement && messages.length === 0 && (
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
            {agent} répond…
          </li>
        )}
      </ol>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void soumettre();
        }}
        className="flex flex-col gap-2"
      >
        <div className="flex items-end gap-2">
          <textarea
            value={brouillon}
            onChange={(e) => setBrouillon(e.target.value)}
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
                void soumettre();
              }
            }}
            rows={2}
            placeholder={`Écrire à ${agent}… (Entrée envoie, Maj+Entrée saute une ligne)`}
            aria-label={`Message à ${agent}`}
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

/** Une bulle du fil : l'utilisateur à droite, l'agent à gauche. */
function Bulle({ message }: { message: MessageChat }) {
  const utilisateur = message.auteur === CHAT_AUTEUR_UTILISATEUR;
  return (
    <li className={"flex " + (utilisateur ? "justify-end" : "justify-start")}>
      <div
        className={
          "max-w-[85%] rounded-lg px-3 py-2 text-sm shadow-sm sm:max-w-[70%] " +
          (utilisateur
            ? "bg-emerald-600 text-white dark:bg-emerald-700"
            : "border border-neutral-200 bg-white text-neutral-900 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100")
        }
      >
        {message.contenu !== "" && (
          <p className="whitespace-pre-wrap break-words">{message.contenu}</p>
        )}
        {/* Ce que le message a porté, et ce qui en a été lu (#482, critères 1 et
            3) — sous le texte parce que c'est la pièce jointe qui accompagne le
            propos, et non l'inverse. Rend `null` quand il n'y a aucune source :
            une bulle ordinaire est exactement celle d'avant ce lot. */}
        <SourcesDuFil message={message} />
        <p
          className={
            "mt-1 text-right text-[10px] " +
            (utilisateur
              ? "text-emerald-100"
              : "text-neutral-400 dark:text-neutral-500")
          }
        >
          {utilisateur ? "vous" : message.auteur} ·{" "}
          <Infobulle texte={formatDateHeure(message.horodatage)}>
            <time dateTime={message.horodatage}>
              {formatHeure(message.horodatage)}
            </time>
          </Infobulle>
        </p>
      </div>
    </li>
  );
}
