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
 */

import { useEffect, useRef, useState } from "react";

import { IconeChat } from "@/components/Icones";
import { BadgeEtat, EnTeteSection } from "@/components/Primitives";
import { formatDateHeure, formatHeure } from "@/lib/format";
import { CHAT_AUTEUR_UTILISATEUR, type MessageChat } from "@/lib/types";
import { useChat } from "@/lib/useChat";

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
  const [brouillon, setBrouillon] = useState("");
  const [erreurEnvoi, setErreurEnvoi] = useState<string | null>(null);
  const fil = useRef<HTMLOListElement | null>(null);

  // Le fil suit la conversation : chaque nouveau message (et l'indicateur
  // d'attente) ramène la vue en bas, comme une messagerie.
  useEffect(() => {
    const conteneur = fil.current;
    if (conteneur !== null) conteneur.scrollTop = conteneur.scrollHeight;
  }, [messages, envoi]);

  const soumettre = async () => {
    const contenu = brouillon.trim();
    if (contenu === "" || envoi) return;
    setErreurEnvoi(null);
    setBrouillon("");
    try {
      await envoyer(contenu);
    } catch (e) {
      setErreurEnvoi(e instanceof Error ? e.message : String(e));
      // Le texte revient dans la zone de saisie (sauf si l'utilisateur a déjà
      // repris la main) : rien ne se perd, relancer reste un simple Entrée.
      setBrouillon((courant) => (courant === "" ? contenu : courant));
    }
  };

  return (
    <section
      aria-label={`Chat avec ${agent}`}
      className="flex min-w-0 flex-1 flex-col gap-3"
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
        className="flex items-end gap-2"
      >
        <textarea
          value={brouillon}
          onChange={(e) => setBrouillon(e.target.value)}
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
        <button
          type="submit"
          disabled={envoi || brouillon.trim() === ""}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {envoi ? "Envoi…" : "Envoyer"}
        </button>
      </form>
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
        <p className="whitespace-pre-wrap break-words">{message.contenu}</p>
        <p
          className={
            "mt-1 text-right text-[10px] " +
            (utilisateur
              ? "text-emerald-100"
              : "text-neutral-400 dark:text-neutral-500")
          }
          title={formatDateHeure(message.horodatage)}
        >
          {utilisateur ? "vous" : message.auteur} ·{" "}
          {formatHeure(message.horodatage)}
        </p>
      </div>
    </li>
  );
}
