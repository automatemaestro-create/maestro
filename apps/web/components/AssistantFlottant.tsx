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
 * ## Le fil est celui du produit (#945)
 *
 * Ce panneau montait jusqu'ici **sa propre** conversation : ses bulles, ses
 * amorces, son composeur, son bouton « Envoyer » — en `bg-sky-600`, quand la
 * couleur d'action du produit est `--accent` (emerald-700). C'était le constat
 * C6 du retex du 2026-09-11, et la cause en était que l'assistant avait été
 * écrit **à côté** du produit plutôt que dedans : 30 paires de couleurs brutes
 * dans ce seul fichier (`couleurs.test.ts`), et un composeur de plus à tenir.
 *
 * Il monte désormais `components/Conversation`, le composant de fil du produit —
 * celui de `/chat`, de l'onglet Chat d'un agent et de la colonne de droite. Ce
 * n'est pas un alignement de classes mais un **retrait de recopie** : le
 * composeur, ses bulles, ses amorces, sa région live et son « Dernier message »
 * arrivent avec les décisions qui les ont formés (#726, #727, #877, #891, #908,
 * #918, #926, veilles #820/#866/#873/#899) et ne peuvent plus en diverger. Ce
 * fichier n'est plus que **le branchement d'un fil sur le canal d'aide**, comme
 * `FilChat` l'est sur un agent.
 *
 * Ce qui lui reste en propre est ce qui est propre à un panneau flottant : la
 * carte bornée, son en-tête à bouton de fermeture, Échap, et le fait que le
 * composeur n'y réserve **pas** la bande du bouton flottant (voir plus bas).
 *
 * Ne pas masquer les actions de la page est une contrainte de fond ici : le
 * bouton fermé reste petit, le shell réserve la bande qu'il occupe (`after:h-24`
 * sur `main` — un élément du flux, pas un padding, #888) pour qu'aucun contenu
 * ne se termine dessous, et le panneau
 * ouvert est une carte bornée (jamais plein écran sur grand écran) qui se ferme
 * par Échap. Les surfaces de la visite guidée (#122, `z-40`/`z-50`) passent
 * au-dessus : pendant la visite, l'assistant est couvert comme le reste.
 *
 * **Le coin est arbitré, composeur compris** (#885, 2026-09-06). Sur `/chat` et
 * sur l'onglet Chat d'un agent, le composeur à quai s'arrête au-dessus de la
 * bande que ce bouton occupe (`sticky bottom-16`, #726) plutôt que de lui céder
 * 56 px à droite. La veille #866 proposait l'inverse — le bouton au-dessus du
 * composeur, dans le fil, comme le « aller en bas » de ChatGPT et de Zulip — et
 * c'est refusé sur mesure : ces références y posent un flottant **de fil**,
 * transitoire, quand celui-ci est un flottant **d'outil**, permanent, qui y
 * couvrirait le dernier message aux six fenêtres du banc sur les deux surfaces.
 * La seule porte qui supprimerait la bande et le `pb-24` d'un coup est une
 * décision à dix écrans — le point d'entrée dans la barre supérieure, où
 * `MenuAide` ouvre déjà le panneau —, hors de ce ticket. La réserve, elle, est
 * le **dernier élément du flux** de `main` depuis #888 (`after:h-24`) : en
 * padding d'une boîte à hauteur fixée (#248), elle ne tenait pas au bas d'une
 * page qui déborde.
 *
 * ⚠ **Et cette bande-là n'existe pas ICI** (`bandeDuFlottant={false}`, #945).
 * Elle vaut partout où ce bouton, calé sur la fenêtre, recouvre le bas de
 * l'ascenseur d'un fil ; or ce panneau est la carte que ce bouton **ouvre**, et
 * il se tient au-dessus de lui dans la même colonne flottante. Rien ne le
 * recouvre, donc il n'y a rien à réserver — la garder coûterait 96 px sur une
 * carte qui en fait 544 au plus, et poserait le composeur 64 px au-dessus du
 * bord de sa propre carte. C'est la même règle que #885 a tranchée, lue dans
 * l'autre sens.
 */

import { useEffect, useRef, useState } from "react";

import { Conversation } from "@/components/Conversation";
import { IconeAssistant, IconeFermer } from "@/components/Icones";
import { Bouton } from "@/components/Primitives";
import {
  ACCUEIL_ASSISTANCE,
  AGENT_ASSISTANCE,
  AMORCES_ASSISTANCE,
  ecouterOuvertureAssistant,
} from "@/lib/assistance";
import { useChat } from "@/lib/useChat";

/**
 * L'interlocuteur, tel qu'il se nomme à l'écran : tous les libellés du fil en
 * dérivent (région live, liste des messages, indicateur d'attente), pour qu'un
 * lecteur d'écran entende le même nom partout. « l'assistant » et non
 * « assistance » — le nom du canal côté API n'est pas un nom de personne.
 */
const INTERLOCUTEUR_ASSISTANCE = "l'assistant";

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
      {/* Écrit à la main, et c'est le seul bouton du fichier qui le reste :
          `Bouton` porte la forme d'une action de formulaire (`rounded-md`, deux
          tailles), pas une pastille de 48 px calée sur la fenêtre. Ce qu'il lui
          prend en revanche, ce sont les **jetons** — plus un `bg-white` ni un
          `ring-sky-500` ici (#945) : le filet de focus est celui du socle
          (`outline-accent`), et la pastille suit le thème sans une variante
          `dark:` à tenir. */}
      <button
        ref={declencheur}
        type="button"
        data-guide="assistant"
        onClick={() => setOuvert((avant) => !avant)}
        aria-expanded={ouvert}
        aria-label={ouvert ? "Fermer l'assistant" : "Ouvrir l'assistant"}
        className={
          "flex size-12 cursor-pointer items-center justify-center rounded-full border border-bord " +
          "bg-surface text-texte-secondaire shadow-lg transition motion-reduce:transition-none " +
          "hover:text-texte focus-visible:outline-2 focus-visible:outline-offset-2 " +
          "focus-visible:outline-accent"
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

/** Le panneau : la carte, son en-tête, et le fil du produit dedans. */
function PanneauAssistance({ fermer }: { fermer: () => void }) {
  const fil = useChat(AGENT_ASSISTANCE);

  return (
    <section
      aria-label="Assistant de la Control Tower"
      className={
        "flex max-h-[min(70vh,34rem)] w-[min(24rem,calc(100vw-2rem))] flex-col overflow-hidden " +
        "rounded-xl border border-bord bg-surface shadow-2xl"
      }
    >
      {/* L'en-tête de la **carte**, pas du fil : il porte le nom du panneau et
          le geste qui le ferme. Le titre du fil, lui, reste au document mais
          quitte l'écran (`titreMasque`) — deux titres empilés dans 384 px sont
          une ligne payée deux fois, exactement le patron de la colonne de
          droite (#926). Le badge de coupure, lui, reste visible : c'est la
          seule chose de cet en-tête-là qui apprenne quelque chose (#691). */}
      <header className="flex items-center gap-2 border-b border-bord px-4 py-3">
        <h2 className="min-w-0 flex-1 text-corps font-semibold text-texte">
          Assistant
        </h2>
        <Bouton
          variante="discret"
          ton="neutre"
          taille="petite"
          icone={IconeFermer}
          onClick={fermer}
          className="-me-1.5"
        >
          <span className="sr-only">Fermer l&apos;assistant</span>
        </Bouton>
      </header>

      {/* L'ascenseur de la carte. `min-h-0` est ce qui lui donne sa hauteur :
          sans lui, `min-height:auto` laisserait la boîte grandir sous le fil et
          plus rien ne défilerait. Pas d'`after:h-24` en regard de la colonne de
          droite (#888) : la réserve couvre la bande du bouton flottant, et
          celui-ci ne passe pas sur cette carte — voir l'en-tête du fichier. */}
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pt-3">
        <Conversation
          fil={fil}
          interlocuteur={INTERLOCUTEUR_ASSISTANCE}
          libelle="Échanges avec l'assistant"
          titre="Assistant"
          niveauTitre={3}
          titreMasque
          accueil={ACCUEIL_ASSISTANCE}
          amorces={AMORCES_ASSISTANCE}
          bandeDuFlottant={false}
          focusAuMontage
        />
      </div>
    </section>
  );
}
