"use client";

/**
 * La troisième zone du shell (#925, lot 4 de #921) : la colonne de droite,
 * **posée vide**. Le fil s'y installe au lot suivant (#926) ; ce lot-ci ne
 * livre que la zone, son ouverture et son comportement à l'étroit.
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
 * Les deux régimes s'écrivent en `max-lg:` / `lg:` et **ne s'annulent pas l'un
 * l'autre** : chacun pose ses propres propriétés, si bien qu'aucune ne dépend
 * de l'ordre dans lequel Tailwind les émet.
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

import { IconeFermer } from "@/components/Icones";

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

      {/* La place tenue, patron repris d'`EmplacementReserve` (barre supérieure,
          #119) : une zone qui s'ouvre sur rien apprend à ne plus l'ouvrir.
          Mesuré à la veille sur Zulip, dont la colonne de droite en vue publique
          n'affiche qu'une action orpheline — 50 px de contenu pour 285 de large.
          Ce bloc disparaît au lot #926, quand le fil prend la place. */}
      <div className="flex flex-1 flex-col items-center justify-center gap-1 px-6 text-center">
        <p className="text-corps font-medium text-texte">
          La conversation s&apos;installera ici
        </p>
        <p className="text-annexe text-texte-secondaire">
          La zone est posée d&apos;abord, vide : le fil la rejoint au ticket
          #926.
        </p>
      </div>
    </aside>
  );
}
