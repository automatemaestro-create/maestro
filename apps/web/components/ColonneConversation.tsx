"use client";

/**
 * La troisième zone du shell (#925, lot 4 de #921) et **le fil qui l'occupe**
 * (#926, lot 5) : la colonne de droite, disponible depuis n'importe quel écran.
 *
 * ── Pourquoi elle n'est PAS modale ──────────────────────────────────────────
 *
 * Le dépôt a déjà une surface latérale droite — `PanneauDetailTache` — et elle
 * est modale (voile, `aria-modal`, piège de focus). Sa propre doc dit pourquoi,
 * et cette raison-là **ne vaut pas ici** : « on ne le consulte pas *en même
 * temps* qu'on agit sur le Kanban — on ouvre une tâche, on la lit, on referme ».
 * La conversation est l'exact opposé : on parle et on regarde par gestes
 * alternés (docs/35 §1.1), c'est même la demande qui fonde le chantier. Une
 * modale imposerait de refermer pour agir, c'est-à-dire de refaire l'aller-retour
 * qu'on vient de supprimer.
 *
 * D'où : **ni voile, ni `aria-modal`, ni `usePiegeDeFocus`, ni vol de focus à
 * l'ouverture**. C'est aussi ce que fait la barre secondaire de VS Code, vérifié
 * à la veille de ce ticket. Ne pas « durcir » cela plus tard par réflexe : ce
 * serait retirer au fil la seule propriété qui le rend utile à droite.
 *
 * ── Les deux régimes, et le seuil qui les sépare ────────────────────────────
 *
 * Au-delà de `lg`, la colonne **prend sa place** dans le flux, à côté du
 * contenu, comme la barre latérale à gauche. En dessous, elle ne la prend
 * **jamais** : ouverte, elle **recouvre** (`fixed`, au-dessus de la barre
 * supérieure) ; fermée, elle n'occupe rien du tout.
 *
 * Mesuré à la veille sur Zulip, qui résout la même question : sa colonne de
 * droite fait 285 px au large, passe à une largeur de **zéro** sous son seuil,
 * et y reste en `position: fixed` — donc au-dessus du centre plutôt qu'en train
 * de le rétrécir ; à 420 px la page ne déborde d'aucun pixel. Le seuil retenu
 * est **`lg`**, celui que `BarreLaterale` emploie déjà : un troisième point de
 * rupture dans le même shell serait une règle de plus à tenir pour rien.
 *
 * ⚠ La veille de **#926** a re-mesuré VS Code, qui tranche l'inverse — à 640 px
 * il **comprime ses trois zones** (170 / 216 / 194) au lieu de recouvrir. C'est
 * écarté, et la raison tient en une phrase : VS Code est une fenêtre de bureau
 * qui n'est jamais vraiment étroite, la Control Tower est servie dans un
 * navigateur et doit tenir à 420 px. Le régime de #925 ne bouge pas.
 *
 * Les deux régimes s'écrivent en `max-lg:` / `lg:` et **ne s'annulent pas l'un
 * l'autre** : chacun pose ses propres propriétés, si bien qu'aucune ne dépend
 * de l'ordre dans lequel Tailwind les émet.
 *
 * ── Ce que #926 y installe, et les trois choses à ne pas défaire ────────────
 *
 * 1. **Le fil est monté TEL QUEL** (parti pris 1 de la veille). `Conversation`
 *    borne sa colonne de lecture à `max-w-3xl` (48 rem) depuis #876, et cette
 *    borne est **inerte** ici : `w-full max-w-3xl` dans 320 px rend 320. Il n'y
 *    a donc ni mode « étroit », ni prop de largeur, ni seconde mise en page —
 *    ce que le fichier de `Conversation` refuse nommément (« deux mises en page
 *    dans un fichier qui existe pour n'en porter qu'une »). Vérifié dehors :
 *    chez VS Code le composeur prend **90 % de la colonne** à 260 px (233 px) et
 *    se **borne à 926** quand la zone passe à 1384 — la borne est une contrainte
 *    de large, jamais une propriété du fil.
 *
 * 2. **Le fil n'est monté que si la colonne est ouverte.** `useChat` ouvre une
 *    **WebSocket par instance** : le laisser dans le DOM sous `hidden` ferait
 *    tourner un second socket sur tous les écrans, et deux sur `/chat`. C'est
 *    aussi ce qui rend le repli sur `/chat` (`Shell`) réel et pas cosmétique.
 *    ⚠ Ce qu'on écrit **survit quand même** à ces démontages : le brouillon vit
 *    dans `lib/brouillons`, hors du composant, précisément pour ça.
 *
 * 3. **L'ascenseur est ICI, pas dans le fil.** Depuis #691 le fil n'a plus
 *    d'ascenseur à lui — « il s'étend, et c'est la page qui le parcourt » —, ce
 *    qui est juste sur `/chat`, où la page défile. Dans une colonne à hauteur
 *    fixée (`h-dvh`), personne ne le parcourrait : c'est donc le conteneur
 *    ci-dessous qui défile, et `Conversation` reste inchangée. La chaîne
 *    `min-h-0` est ce qui donne sa hauteur à cet ascenseur (même règle que le
 *    `Shell`, #248) — un seul maillon manquant et la colonne s'étire sous son
 *    contenu au lieu de le faire défiler.
 *
 * ── Ce que #1106 y ajoute : les gestes, pas une surface ─────────────────────
 *
 * Le fil montait ses **messages** et s'arrêtait là. Les gestes qui répondent à
 * ce qu'on vient d'y lire — la question d'un agent (#1025), la question
 * d'outillage (#1031), « Je lance ? » (#943) — vivaient au pied du fil de
 * `/chat`, et cette page seule savait les composer : la colonne affichait donc
 * la proposition sans rien pour lancer, corriger ou refuser, et répondre
 * obligeait à changer de page. C'est la réserve C2 du bouclage de « L'atelier ».
 *
 * La composition a donc quitté la page pour `chat/GestesDuFil`, et les deux
 * surfaces l'appellent. Rien d'autre n'a bougé, et c'est le point à ne pas
 * défaire : **aucun rendu propre à la colonne**. Le point 1 ci-dessus vaut pour
 * le pied comme pour le fil — pas de mode « étroit », pas de carte abrégée, pas
 * de renvoi vers `/chat` à la place du geste. Ce qui tiendrait mal à 320 px est
 * une **mesure** (`/banc-mise-en-page`), pas une seconde mise en page.
 *
 * ⚠ Fermée, la colonne reste **dans le DOM** — c'est ce qui permet à
 * l'`aria-controls` du bouton de la barre supérieure de désigner un élément qui
 * existe, ouverte comme fermée. Elle est alors masquée **deux fois**, et il faut
 * les deux : l'utilitaire `hidden` de Tailwind (le rendu réel) et l'attribut
 * HTML `hidden` (ce que voient le DOM, les lecteurs d'écran et les sondes de
 * test, jsdom ne chargeant aucune feuille Tailwind). Les deux ne se contredisent
 * jamais : l'attribut n'est posé que lorsque la classe `flex` ne l'est pas —
 * dans l'autre ordre il perdrait contre elle, une classe l'emportant sur la
 * feuille de style du navigateur.
 */

import Link from "next/link";

import { useGestesDuFil } from "@/components/chat/GestesDuFil";
import { Conversation } from "@/components/Conversation";
import { IconeAgrandir, IconeFermer } from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle } from "@/lib/navigation";
import {
  ACCUEIL_ORCHESTRATION,
  AGENT_ORCHESTRATION,
  AMORCES_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
} from "@/lib/orchestration";
import { useChat } from "@/lib/useChat";

/**
 * L'ancre de la colonne, visée par l'`aria-controls` de son bouton.
 *
 * Exportée plutôt qu'écrite deux fois, pour la raison exacte de
 * `ID_CONTENU_PRINCIPAL` : le bouton et sa cible vivent dans deux fichiers, et
 * une faute de frappe entre eux ne se verrait ni au lint, ni au build, ni dans
 * un rendu — le bouton annoncerait simplement commander quelque chose qui
 * n'existe pas.
 */
export const ID_COLONNE_CONVERSATION = "colonne-conversation";

/** Le titre visible de la zone, qui lui sert aussi de nom accessible. */
const ID_TITRE = "colonne-conversation-titre";

/**
 * Le grand format du même fil — `/chat`, jamais une seconde conversation.
 *
 * Dérivé de `lib/navigation` comme partout ailleurs plutôt qu'écrit en dur, ce
 * qui donne les deux propriétés du helper : le renvoi suit si « Chat » déménage,
 * et il **ne s'allume pas** si l'entrée disparaît du menu (`undefined` plutôt
 * qu'un lien mort) — voir `hrefRun`, même contrat.
 */
const HREF_CHAT = entreeParLibelle("Chat")?.href;

export function ColonneConversation({
  ouverte,
  fermer,
}: {
  ouverte: boolean;
  fermer: () => void;
}) {
  return (
    <aside
      id={ID_COLONNE_CONVERSATION}
      // Le nom vient du titre affiché (`aria-labelledby`) plutôt que d'un
      // `aria-label` qui le dirait une seconde fois : deux libellés à tenir
      // d'accord finissent par diverger, et c'est celui de l'écran qui fait foi.
      aria-labelledby={ID_TITRE}
      // ⚠ Les deux tests « fermée » de `shell.test.tsx` **rougissent** si cet
      // attribut part : jsdom ne charge aucune feuille Tailwind, donc la classe
      // `hidden` n'y masque rien et la colonne resterait accessible aux sondes
      // comme aux lecteurs d'écran. Vérifié en le retirant (#925).
      hidden={!ouverte}
      className={
        (ouverte ? "flex " : "hidden ") +
        // Largeur fixe et jamais un pourcentage : 320 px, entre la colonne de
        // droite de Zulip (285) et le panneau de détail (448). `min(…,100vw)`
        // la borne sur une fenêtre plus étroite qu'elle.
        "w-[min(20rem,100vw)] shrink-0 flex-col border-l border-bord bg-surface-creuse " +
        // Sous `lg` : recouvrement. `z-20` la place au-dessus de la barre
        // supérieure (`z-10`) sans jamais passer devant les surfaces modales —
        // l'assistant flottant (`z-30`), la visite guidée et le détail de tâche
        // (`z-40`/`z-50`) gardent la priorité qu'ils avaient.
        "max-lg:fixed max-lg:inset-y-0 max-lg:right-0 max-lg:z-20 " +
        // À partir de `lg` : dans le flux, sur toute la hauteur, exactement
        // comme la barre latérale de gauche.
        "lg:sticky lg:top-0 lg:h-dvh"
      }
    >
      {/* Un `<div>` et **non** un `<header>` : le HTML ne donne le rôle `banner`
          qu'à un `<header>` qui n'est pas dans une section, donc celui-ci n'en
          serait pas un — mais ni jsdom ni les outils de test ne tiennent ce
          cadrage, et `getByRole("banner")` en trouvait deux au lieu d'un
          (`shell.test.tsx`). La balise n'apportait rien que la colonne n'ait
          déjà : elle est un `complementary` nommé par son titre. */}
      <div className="flex h-14 shrink-0 items-center justify-between gap-2 border-b border-bord px-4">
        <h2
          id={ID_TITRE}
          className="truncate text-corps font-semibold tracking-tight text-texte"
        >
          Conversation
        </h2>
        {/* L'en-tête reste **au calibre de la colonne** (parti pris 4 de la
            veille) : un titre et deux gestes, rien de plus. Mesuré chez VS Code,
            dont l'en-tête de barre secondaire fait 32 px de haut et ne porte que
            quatre boutons de 22 px — ce qui ne tient pas ici descend dans le fil
            ou n'y est pas. La colonne n'est pas un écran. */}
        <div className="flex shrink-0 items-center gap-1">
          {/* Le pont vers le grand format (parti pris 2), d'après le couple
              « Agrandir » / « Restaurer » de VS Code — mesuré : la même
              conversation passe de 260 px à 1384, nav et centre à zéro. C'est un
              **lien** et non un bouton parce que `/chat` est une route : le
              milieu du clic, le clic droit et l'ouverture dans un onglet doivent
              marcher comme partout ailleurs. Le repli de la colonne à l'arrivée
              est tenu par le `Shell`, pas ici — il vaut aussi quand on atteint
              `/chat` par le menu. */}
          {HREF_CHAT !== undefined && (
            <Infobulle texte="Ouvrir en grand">
              <Link
                href={HREF_CHAT}
                aria-label="Ouvrir la conversation en grand"
                className="flex rounded-md p-1.5 text-texte-secondaire hover:bg-survol hover:text-texte"
              >
                <IconeAgrandir className="size-5" />
              </Link>
            </Infobulle>
          )}
          {/* La fermeture est offerte **ici aussi**, et ce n'est pas un doublon du
              bouton de la barre supérieure : sous `lg`, la colonne recouvre la
              droite de l'écran, donc elle recouvre le bouton qui l'a ouverte. Sans
              cette croix, une fenêtre étroite ouvrirait une colonne qu'on ne peut
              plus refermer. Les deux libellés sont **distincts** à dessein
              (« Fermer » ici, « Replier » là-haut) : deux boutons de même nom sur
              le même écran ne se désignent plus. */}
          <button
            type="button"
            onClick={fermer}
            aria-label="Fermer la conversation"
            className="-mr-1 rounded-md p-1.5 text-texte-secondaire hover:bg-survol hover:text-texte"
          >
            <IconeFermer className="size-5" />
          </button>
        </div>
      </div>

      {/* ⚠ Monté **seulement quand la colonne est ouverte** : voir le point 2 de
          l'en-tête — une WebSocket par instance de `useChat`. Ce qui est en
          cours de saisie ne s'y perd pas (`lib/brouillons`). */}
      {ouverte && <FilDeLaColonne />}
    </aside>
  );
}

/**
 * Le fil de l'orchestration, dans la colonne.
 *
 * Composant séparé pour que `useChat` **et son socket** naissent et meurent avec
 * l'ouverture de la colonne : monté dans le parent, le hook tournerait même
 * fermée, un `if` dans le JSX ne changeant rien à l'ordre des hooks.
 */
function FilDeLaColonne() {
  const { projet } = useEtatGlobal();
  // Le même appel que `/chat` (`app/chat/page.tsx`) — donc **la même**
  // conversation, servie par la même API et la même mémoire de conversation
  // ouverte (`lib/useChat` lit `useConversationOuverte`, jamais un état d'ici).
  // C'est ce qui tient le critère « `/chat` reste servi et reste la même
  // conversation » sans rien synchroniser : il n'y a rien à synchroniser.
  const fil = useChat(AGENT_ORCHESTRATION, projet.id);
  // …et les mêmes gestes (#1106). Le fil est celui de l'orchestration, donc il
  // porte les trois demandes : question d'un agent, question d'outillage,
  // « Je lance ? ». La composition vient de `chat/GestesDuFil`, appelée et
  // jamais recopiée — sans quoi la colonne et `/chat` finiraient par ne plus
  // désigner la même attente, et c'est le défaut que ce ticket corrige.
  const gestes = useGestesDuFil(fil, AGENT_ORCHESTRATION);

  return (
    // L'ascenseur de la colonne (point 3 de l'en-tête). `min-h-0` est ce qui lui
    // donne sa hauteur : sans lui, `min-height:auto` laisserait la boîte grandir
    // sous le fil et plus rien ne défilerait.
    //
    // ⚠ `after:h-24` — **la réserve du bouton flottant**, et elle est
    // obligatoire ici pour la raison exacte de #888. Le composeur est à quai en
    // `sticky bottom-16` (#726) : il se tient 64 px au-dessus du bord de **son**
    // ascenseur, lequel est désormais cette boîte-ci et non plus la page. Or le
    // bouton de l'assistant (#123) est calé sur la **fenêtre**
    // (`fixed right-4 bottom-4 z-30`), donc il flotte par-dessus le bas de cette
    // colonne — elle occupe les 320 px de droite, c'est-à-dire son coin. Sans
    // cette réserve, au bas du défilement le formulaire remonterait de 64 px
    // **sur le fil** (le défaut que #888 décrit mot pour mot) ; avec elle, les
    // 96 derniers pixels ne portent que du fond et le flottant n'y rencontre
    // rien. Pas de `-mt-*` en regard du `after:-mt-6` de `main` : cette boîte
    // n'a pas de `gap` à reprendre.
    <div
      data-defilement
      className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pt-4 after:block after:h-24 after:shrink-0"
    >
      <Conversation
        fil={fil}
        interlocuteur={INTERLOCUTEUR_ORCHESTRATION}
        libelle="Conversation"
        // Le titre reste au document — donc aux lecteurs d'écran et à la
        // hiérarchie des titres — mais quitte l'**écran** : l'en-tête de la
        // colonne dit déjà « Conversation » deux lignes plus haut, et deux
        // titres empilés dans 320 px sont une ligne payée deux fois (vu au banc
        // du 2026-09-13 : « Conversation » puis « CHAT GLOBAL »). C'est le
        // patron `libelleMasque` de `CadreChamp` (#832), et VS Code ne titre pas
        // deux fois sa barre secondaire non plus. Le badge « Reconnexion… »,
        // lui, reste visible : c'est la seule chose de cet en-tête qui apprenne
        // quelque chose (#691).
        titre="Chat global"
        niveauTitre={3}
        titreMasque
        accueil={ACCUEIL_ORCHESTRATION}
        amorces={AMORCES_ORCHESTRATION}
        /* Les gestes du fil, **tels quels** (#1106) — même carte, même place,
           même ordre que sur `/chat`. Aucun rendu propre à la colonne : le
           parti pris 1 de la veille de #926 vaut pour le pied comme pour le
           fil, et une carte qui tiendrait mal à 320 px est une mesure
           (`/banc-mise-en-page`), jamais une seconde mise en page. */
        pied={gestes}
      />
    </div>
  );
}
