"use client";

/**
 * L'assistant flottant de la Control Tower (#123, lot 7 de #116) : un bouton
 * discret en bas à droite, présent sur toutes les pages via le shell (#117), et
 * le panneau d'aide qu'il ouvre — sans navigation, sans perdre la page en cours.
 *
 * Le canal est un fil de chat ordinaire côté API (`/api/chat/assistance`) : le
 * panneau se branche sur `useChat` comme la page Chat (#85), et hérite donc de
 * son temps réel (WebSocket, réponse poussée dès qu'elle tombe) et de son
 * historique persisté — rouvrir le panneau, changer de page ou recharger
 * retrouve la conversation. Ce qui distingue l'assistant du chat des agents est
 * son interlocuteur : il répond sur **l'outil**, pas sur le projet.
 *
 * Ne pas masquer les actions de la page est une contrainte de fond ici : le
 * bouton fermé reste petit, le shell réserve la bande qu'il occupe (`pb-24` sur
 * la zone de contenu) pour qu'aucun contenu ne se termine dessous, et le panneau
 * ouvert est une carte bornée (jamais plein écran sur grand écran) qui se ferme
 * par Échap. Les surfaces de la visite guidée (#122, `z-40`/`z-50`) passent
 * au-dessus : pendant la visite, l'assistant est couvert comme le reste.
 */

import { useEffect, useRef, useState } from "react";

import { IconeAssistant, IconeFermer } from "@/components/Icones";
import {
  ACCUEIL_ASSISTANCE,
  AGENT_ASSISTANCE,
  AMORCES_ASSISTANCE,
  ecouterOuvertureAssistant,
} from "@/lib/assistance";
import { formatDateHeure, formatHeure } from "@/lib/format";
import { CHAT_AUTEUR_UTILISATEUR, type MessageChat } from "@/lib/types";
import { useChat } from "@/lib/useChat";

export function AssistantFlottant() {
  const [ouvert, setOuvert] = useState(false);
  const declencheur = useRef<HTMLButtonElement>(null);

  // Le menu d'aide ouvre le panneau sans connaître ce composant (même contrat
  // que la relance de la visite guidée).
  useEffect(() => ecouterOuvertureAssistant(() => setOuvert(true)), []);

  // Échap ferme et rend le focus au bouton — sans quoi il retomberait sur le
  // document. Pas de fermeture au clic extérieur, contrairement aux menus de la
  // barre supérieure : on consulte l'assistant *en même temps* qu'on agit sur la
  // page, le fermer au premier clic ailleurs irait contre son usage.
  useEffect(() => {
    if (!ouvert) return;
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key !== "Escape") return;
      setOuvert(false);
      declencheur.current?.focus();
    };
    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [ouvert]);

  return (
    <div className="fixed right-4 bottom-4 z-30 flex flex-col items-end gap-3 print:hidden">
      {ouvert && <PanneauAssistance fermer={() => setOuvert(false)} />}
      <button
        ref={declencheur}
        type="button"
        data-guide="assistant"
        onClick={() => setOuvert((avant) => !avant)}
        aria-expanded={ouvert}
        aria-label={ouvert ? "Fermer l'assistant" : "Ouvrir l'assistant"}
        title={ouvert ? "Fermer l'assistant" : "Assistant — poser une question"}
        className={
          "flex size-12 items-center justify-center rounded-full border border-neutral-200 " +
          "bg-white text-neutral-600 shadow-lg transition hover:text-neutral-900 " +
          "focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-500 " +
          "dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-300 dark:hover:text-neutral-100"
        }
      >
        {ouvert ? (
          <IconeFermer className="size-5" />
        ) : (
          <IconeAssistant className="size-6" />
        )}
      </button>
    </div>
  );
}

/** Le panneau : en-tête, fil des échanges, amorces sur fil vide, saisie. */
function PanneauAssistance({ fermer }: { fermer: () => void }) {
  const { messages, connecte, chargement, erreur, envoi, envoyer } =
    useChat(AGENT_ASSISTANCE);
  const [brouillon, setBrouillon] = useState("");
  const [erreurEnvoi, setErreurEnvoi] = useState<string | null>(null);
  const fil = useRef<HTMLOListElement | null>(null);
  const saisie = useRef<HTMLTextAreaElement | null>(null);

  // Le panneau s'ouvre prêt à recevoir la question : ouvrir puis devoir cliquer
  // dans le champ serait un geste de trop pour une aide qu'on veut immédiate.
  useEffect(() => saisie.current?.focus(), []);

  // Le fil suit la conversation, comme celui de la page Chat.
  useEffect(() => {
    const conteneur = fil.current;
    if (conteneur !== null) conteneur.scrollTop = conteneur.scrollHeight;
  }, [messages, envoi]);

  const soumettre = async (texte: string) => {
    const contenu = texte.trim();
    if (contenu === "" || envoi) return;
    setErreurEnvoi(null);
    setBrouillon("");
    try {
      await envoyer(contenu);
    } catch (e) {
      setErreurEnvoi(e instanceof Error ? e.message : String(e));
      // Le texte revient dans la zone de saisie (sauf si l'utilisateur a déjà
      // repris la main) : relancer reste un simple Entrée.
      setBrouillon((courant) => (courant === "" ? contenu : courant));
    }
  };

  const filVide = !chargement && messages.length === 0;

  return (
    <section
      aria-label="Assistant de la Control Tower"
      className={
        "flex max-h-[min(70vh,34rem)] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden " +
        "rounded-xl border border-neutral-200 bg-white shadow-2xl " +
        "dark:border-neutral-700 dark:bg-neutral-900"
      }
    >
      <header className="flex items-start gap-2 border-b border-neutral-200 px-4 py-3 dark:border-neutral-800">
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">
            Assistant
          </h2>
          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-neutral-500 dark:text-neutral-400">
            <span
              aria-hidden="true"
              className={
                "size-1.5 shrink-0 rounded-full " +
                (connecte ? "bg-emerald-500" : "animate-pulse bg-amber-500")
              }
            />
            {connecte ? "Vos questions sur la Control Tower" : "Reconnexion…"}
          </p>
        </div>
        <button
          type="button"
          onClick={fermer}
          aria-label="Fermer l'assistant"
          className="-mr-1 rounded-md p-1.5 text-neutral-400 hover:bg-neutral-100 hover:text-neutral-900 dark:hover:bg-neutral-800 dark:hover:text-neutral-100"
        >
          <IconeFermer className="size-4" />
        </button>
      </header>

      {erreur && (
        <p
          role="alert"
          className="border-b border-rose-200 bg-rose-50 px-4 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Fil illisible : {erreur}
        </p>
      )}

      <ol
        ref={fil}
        aria-label="Échanges avec l'assistant"
        aria-live="polite"
        className="flex flex-1 flex-col gap-2 overflow-y-auto p-3"
      >
        {chargement && (
          <li className="text-xs text-neutral-500">Chargement…</li>
        )}
        {filVide && (
          <li className="rounded-lg bg-neutral-100 px-3 py-2 text-sm text-neutral-700 dark:bg-neutral-800 dark:text-neutral-200">
            {ACCUEIL_ASSISTANCE}
          </li>
        )}
        {messages.map((message, index) => (
          <Bulle key={`${message.horodatage}-${index}`} message={message} />
        ))}
        {envoi && (
          <li className="text-xs italic text-neutral-500">L&apos;assistant répond…</li>
        )}
      </ol>

      {filVide && (
        <div className="flex flex-wrap gap-1.5 px-3 pb-2">
          {AMORCES_ASSISTANCE.map((amorce) => (
            <button
              key={amorce}
              type="button"
              onClick={() => void soumettre(amorce)}
              className={
                "rounded-full border border-neutral-200 px-2.5 py-1 text-xs text-neutral-600 " +
                "hover:border-neutral-400 hover:text-neutral-900 " +
                "dark:border-neutral-700 dark:text-neutral-300 dark:hover:border-neutral-500 dark:hover:text-neutral-100"
              }
            >
              {amorce}
            </button>
          ))}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void soumettre(brouillon);
        }}
        className="flex items-end gap-2 border-t border-neutral-200 p-3 dark:border-neutral-800"
      >
        <textarea
          ref={saisie}
          value={brouillon}
          onChange={(e) => setBrouillon(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void soumettre(brouillon);
            }
          }}
          rows={1}
          placeholder="Poser une question…"
          aria-label="Question à l'assistant"
          className={
            "min-h-9 w-full resize-y rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm " +
            "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none " +
            "dark:border-neutral-700 dark:bg-neutral-950 dark:text-neutral-100 dark:focus:border-neutral-500"
          }
        />
        <button
          type="submit"
          disabled={envoi || brouillon.trim() === ""}
          className="shrink-0 rounded-md bg-sky-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-sky-700 disabled:opacity-50"
        >
          {envoi ? "…" : "Envoyer"}
        </button>
      </form>

      {erreurEnvoi && (
        <p
          role="alert"
          className="px-3 pb-3 text-xs text-rose-600 dark:text-rose-400"
        >
          {erreurEnvoi}
        </p>
      )}
    </section>
  );
}

/** Une bulle du fil : l'utilisateur à droite, l'assistant à gauche. */
function Bulle({ message }: { message: MessageChat }) {
  const utilisateur = message.auteur === CHAT_AUTEUR_UTILISATEUR;
  return (
    <li className={"flex " + (utilisateur ? "justify-end" : "justify-start")}>
      <div
        className={
          "max-w-[85%] rounded-lg px-3 py-2 text-sm shadow-sm " +
          (utilisateur
            ? "bg-sky-600 text-white dark:bg-sky-700"
            : "bg-neutral-100 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100")
        }
      >
        <p className="whitespace-pre-wrap break-words">{message.contenu}</p>
        <p
          className={
            "mt-1 text-right text-[10px] " +
            (utilisateur
              ? "text-sky-100"
              : "text-neutral-400 dark:text-neutral-500")
          }
          title={formatDateHeure(message.horodatage)}
        >
          {formatHeure(message.horodatage)}
        </p>
      </div>
    </li>
  );
}
