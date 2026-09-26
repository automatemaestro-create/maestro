"use client";

/**
 * La barre supérieure du backoffice (#117) : titre de la page courante à
 * gauche, indicateurs globaux à droite — statut du flux temps réel et coût
 * cumulé (docs/05 §2.1 — « lisibilité du coût »), qui suivent l'utilisateur de
 * page en page au lieu d'être refaits dans chaque en-tête.
 *
 * Les emplacements de droite sont des `slots` que leur lot de #116 remplit : le
 * thème (#118) et l'aide (#122) sont livrés, les notifications sont encore
 * tenues par un bouton inerte quand la barre est rendue sans elles, pour que sa
 * géométrie ne bouge pas.
 *
 * Depuis #925 elle porte **les deux** bascules de zone du shell, une à chaque
 * extrémité : la navigation à gauche (#117) et la conversation à droite. Le
 * second bouton est le jumeau du premier — même patron ARIA, mêmes chevrons au
 * miroir —, et c'est délibéré : deux zones qui se replient de la même façon
 * s'apprennent une fois. Ce qu'on n'a **pas** repris des produits regardés à la
 * veille de #925, c'est le raccourci clavier global (`[` chez Linear, `⌘B` chez
 * shadcn/ui) : le produit n'en a aucun aujourd'hui, celui-ci serait le premier
 * d'une famille que personne ne tient, et il entrerait en collision avec la
 * saisie du composeur qui rejoint la colonne au lot #926.
 *
 * ── Ce que #1108 y change : la barre se mesure ELLE-MÊME ────────────────────
 *
 * Depuis #925 la barre n'occupe plus la fenêtre : elle est dans la colonne
 * centrale, dont la colonne de conversation retranche 320 px dès qu'on l'ouvre.
 * Ses seuils étaient pourtant des `media queries` de **viewport** (`sm:`), qui
 * ne savent rien de cette colonne — si bien qu'entre ~1024 et ~1270 px la barre
 * croyait avoir 1116 px de large là où elle en avait 508, gardait tout ce
 * qu'elle affiche à 1116, et ses éléments se **recouvraient** (mesuré au banc,
 * projet au nom long : sélecteur 304→558 par-dessus le titre 398→434 et le coût
 * 446→591). Une largeur de fenêtre ne dit donc plus rien de la place disponible
 * ici : les seuils sont lus sur la **barre** (`@container`, la primitive que
 * `<main>` emploie déjà dans `Shell.tsx`), et ils redeviennent vrais quelle que
 * soit la zone qu'on ouvre ou qu'on replie.
 *
 * Variante retenue par le regard neuf le 2026-09-21 (#1009, consignée sur le
 * ticket sous « ## Variante retenue »), d'après la veille de #1108 — VS Code
 * for the Web, Grafana, GitHub Actions, Zulip : **ce qui rassure s'efface avant
 * ce qui situe**. L'ordre de cession, quand la barre se resserre :
 *
 *   1. le **coût cumulé** part le premier (`@3xl`) — il se relit dans `/couts`
 *      et sur le tableau de bord, donc rien ne se perd ;
 *   2. le **nom du projet** se raccourcit ensuite, en gardant un préfixe
 *      lisible (`SelecteurProjet`) ;
 *   3. le **titre de page** cède en dernier — c'est le seul de la barre qui ne
 *      se lise nulle part ailleurs.
 *
 * Et les **deux extrémités ne rétrécissent jamais** : le groupe de droite est
 * `shrink-0`, comme le centre de commande de VS Code est borné entre deux bouts
 * fixes. Écartés ici, avec leur raison : la seconde ligne de Grafana (notre
 * barre ne porte aucune action de page à reléguer) et le menu `…` de GitHub
 * (ce qu'elle porte n'est pas homogène — un chiffre, un thème, une aide et deux
 * bascules de zone ne se rangent pas dans le même tiroir).
 *
 * ⚠ Le seuil `@3xl` du coût n'est pas celui auquel il *tiendrait* : c'est celui
 * à partir duquel il tient **sans faire tronquer le titre**. Le descendre à
 * `@2xl` a été mesuré et refusé — à 1280 px de fenêtre, colonne ouverte et nom
 * de projet long, le coût revenait et le titre repassait à « Tableau de b… »,
 * c'est-à-dire l'ordre de cession à l'envers.
 *
 * ⚠ Le `lg:block` du bouton de navigation, lui, **reste** une media query de
 * viewport, et c'est volontaire : il ne parle pas de la place dans la barre mais
 * du rail de gauche, qui est imposé sous `lg` par la fenêtre. Gardé par
 * `tests/barre-superieure-largeur.test.tsx`, qui refuse toute autre variante de
 * viewport dans cette barre.
 */

import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import {
  IconeDeplier,
  IconeNotifications,
  IconeReplier,
} from "@/components/Icones";
import { ID_COLONNE_CONVERSATION } from "@/components/ColonneConversation";
import { Infobulle } from "@/components/Infobulle";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatCoutPartiel, RAISON_COUT_PARTIEL } from "@/lib/format";
import { entreeCourante } from "@/lib/navigation";

type Props = {
  /** Sidebar repliée (état tenu par le shell). */
  repliee: boolean;
  basculerRepli: () => void;
  /**
   * Sélecteur du projet actif (#280) — à **gauche**, avant le titre : il porte
   * le cadre dans lequel la page se lit, la page vient après. Optionnel comme
   * les autres emplacements, mais sans place réservée : contrairement à la
   * cloche de #119, il n'attend aucun lot suivant.
   *
   * Nommé `selecteurProjet` et non `projet` : ce dernier désigne déjà, dans ce
   * composant, le projet actif lu dans l'état global (#281) — deux `projet` de
   * natures différentes (un nœud, un objet de domaine) dans la même portée.
   */
  selecteurProjet?: ReactNode;
  /** Cloche de notifications — lot #119. */
  notifications?: ReactNode;
  /** Bascule clair / sombre / système (#118). */
  theme: ReactNode;
  /** Menu d'aide — visite guidée (#122), assistant à venir (#123). */
  aide: ReactNode;
  /**
   * Colonne de droite ouverte (#925) — état tenu par le shell, comme `repliee`.
   *
   * Optionnels tous les deux : la barre se rend sans eux (elle n'affiche alors
   * aucune bascule de conversation), ce qui garde montable hors du shell une
   * barre dont ce n'est pas le sujet. Contrairement à la cloche de #119, aucune
   * place n'est réservée — il n'y a rien à attendre, la zone existe déjà.
   */
  conversationOuverte?: boolean;
  basculerConversation?: () => void;
};

export function BarreSuperieure({
  repliee,
  basculerRepli,
  selecteurProjet,
  notifications,
  theme,
  aide,
  conversationOuverte = false,
  basculerConversation,
}: Props) {
  const chemin = usePathname();
  const { connecte, coutTotal, coutPartiel, projet } = useEtatGlobal();
  const titre = entreeCourante(chemin)?.libelle ?? "Control Tower";

  return (
    <header className="@container sticky top-0 z-10 flex h-14 shrink-0 items-center gap-x-3 gap-y-2 border-b border-neutral-200 bg-white/90 px-4 backdrop-blur @2xl:px-6 dark:border-neutral-800 dark:bg-neutral-950/90">
      <button
        type="button"
        onClick={basculerRepli}
        aria-expanded={!repliee}
        aria-controls="navigation-principale"
        aria-label={repliee ? "Déplier la navigation" : "Replier la navigation"}
        // Le rail est imposé sous `lg` : le bouton n'y aurait rien à basculer.
        className="-ml-1 hidden rounded-md p-1.5 text-neutral-500 hover:bg-neutral-100 hover:text-neutral-900 lg:block dark:text-neutral-400 dark:hover:bg-neutral-900 dark:hover:text-neutral-100"
      >
        {repliee ? (
          <IconeDeplier className="size-5" />
        ) : (
          <IconeReplier className="size-5" />
        )}
      </button>
      {selecteurProjet}
      {/* Le milieu de la barre — le seul endroit qui rétrécit. Le sélecteur
          cède d'abord (il garde un préfixe lisible, voir `SelecteurProjet`),
          le titre en dernier : `min-w-0 truncate` lui laisse prendre tout ce
          qui reste et tronquer plutôt que déborder. */}
      <h1
        // Pas de `title` (#536) : il ne répétait que le texte du titre, donc
        // n'apprenait rien à personne — ni à la souris, ni au lecteur d'écran,
        // qui lit le `<h1>` en entier même tronqué à l'écran.
        className="min-w-0 truncate text-base font-semibold tracking-tight"
      >
        {titre}
      </h1>

      {/* `shrink-0` (#1108) : l'extrémité droite ne rétrécit pas. Sans lui, le
          groupe se serait mis à disputer la place au titre dès que la barre se
          resserre — or ce qu'il porte ne se tronque pas (des icônes, un montant
          en `whitespace-nowrap`) : il aurait simplement débordé. Ce qui doit
          céder ici cède en **disparaissant** (le coût, `@3xl`), jamais en se
          comprimant. */}
      <div className="ml-auto flex shrink-0 items-center gap-2 @xl:gap-3">
        {/* ⚠ **Ce qui va bien ne s'affiche plus** (#691). Cette pastille a porté
            « Temps réel connecté » en permanence depuis #117, sur les onze
            écrans, à la place la plus visible de la fenêtre — pour dire, en
            régime nominal, que rien n'allait mal. La revue du 2026-08-28 l'a
            nommée deux fois sur `/chat`, où l'en-tête du fil la doublait ; le
            fil a rendu la sienne, celle-ci se tait.
            Ce qui reste est ce qui **apprend** quelque chose : la coupure. Elle
            explique un écran qui ne bouge plus, et c'est la seule chose que
            l'utilisateur ne peut pas déduire de ce qu'il voit. Même règle que le
            reste du produit (docs/30 §4) : une place se gagne, elle ne se garde
            pas parce qu'on l'avait.
            Le texte reste masqué quand la barre est étroite (`@xl` depuis
            #1108, `sm` avant lui) — la pastille seule y suffit, et le titre de
            page a la priorité sur une barre étroite. */}
        {!connecte && (
          <span className="inline-flex items-center gap-1.5 rounded-full bg-amber-100 px-2.5 py-0.5 text-xs font-medium text-amber-800 dark:bg-amber-950 dark:text-amber-300">
            <span className="size-1.5 animate-pulse rounded-full bg-amber-500 motion-reduce:animate-none" />
            <span className="hidden @xl:inline">Reconnexion…</span>
          </span>
        )}

        {/* Le **premier** à s'effacer quand la barre se resserre (#1108) : le
            titre de page a la priorité, et le coût reste lisible dans la page
            elle-même (`/couts`, tableau de bord). Seuil sur la largeur de la
            barre et non de la fenêtre — `@3xl`, celui à partir duquel il tient
            sans faire tronquer le titre, pas celui où il tiendrait tout juste.
            Depuis #281 c'est la dépense **du projet actif** — le titre le dit,
            un montant qui suit l'utilisateur de page en page ne pouvant pas
            rester le seul chiffre de l'écran à parler de tous les projets. */}
        {/* Ce que le montant recouvre ne se lit nulle part ailleurs — c'est
            donc une infobulle et non un `title` (#536), atteignable au clavier
            comme à la souris. */}
        {/* Un cumul qui n'est qu'un plancher le dit (#1280), de la même façon
            que le run dont il additionne le montant : sans quoi « 0,21 $US »
            s'affichait nu au-dessus d'un run qui se disait partiel. La raison
            rejoint l'infobulle déjà là, plutôt qu'une seconde. */}
        <Infobulle
          texte={
            coutPartiel
              ? `Coût cumulé sur ${projet.nom} — somme des grands livres, planification comprise. ${RAISON_COUT_PARTIEL}`
              : `Coût cumulé sur ${projet.nom} — somme des grands livres, planification comprise`
          }
          className="hidden whitespace-nowrap text-sm text-neutral-600 @3xl:inline-flex dark:text-neutral-400"
        >
          <span data-guide="cout-cumule">
            Coût cumulé :{" "}
            <span className="font-medium tabular-nums">
              {formatCoutPartiel(coutTotal, coutPartiel)}
            </span>
          </span>
        </Infobulle>

        <div className="flex items-center gap-1 border-l border-neutral-200 pl-2 @xl:pl-3 dark:border-neutral-800">
          {notifications ?? (
            <EmplacementReserve
              libelle="Notifications"
              lot={119}
              Icone={IconeNotifications}
            />
          )}
          {theme}
          {aide}
          {/* La bascule de la troisième zone (#925), à l'extrémité droite de la
              barre : le miroir exact du bouton de navigation, à l'autre bout.
              ⚠ Les deux chevrons sont **croisés** par rapport à leur nom, qui
              date de la sidebar de gauche : pour une colonne de *droite*,
              l'ouvrir la tire vers l'intérieur (chevron vers la gauche,
              `IconeReplier`) et la fermer la renvoie au bord (chevron vers la
              droite, `IconeDeplier`). Les noms disent le geste à gauche, les
              dessins disent la direction — ce sont ces derniers qui comptent
              ici, et c'est ce qui évite une paire d'icônes de plus.
              Pas de `lg:block` comme à gauche : sous ce seuil le rail est imposé
              et le bouton de navigation n'aurait rien à basculer, alors que la
              conversation, elle, s'ouvre à toutes les largeurs — en recouvrement
              quand la fenêtre est étroite. */}
          {basculerConversation !== undefined && (
            <button
              type="button"
              onClick={basculerConversation}
              aria-expanded={conversationOuverte}
              aria-controls={ID_COLONNE_CONVERSATION}
              aria-label={
                conversationOuverte
                  ? "Replier la conversation"
                  : "Déplier la conversation"
              }
              className="flex items-center gap-1.5 rounded-md p-2 text-texte-secondaire hover:bg-survol hover:text-texte"
            >
              {conversationOuverte ? (
                <IconeDeplier className="size-5" />
              ) : (
                <IconeReplier className="size-5" />
              )}
              {/* **Le repli se nomme** (#1107). Depuis #1107 la colonne est
                  ouverte au premier passage, mais seulement au large : sous
                  `lg`, et pour qui l'a repliée, il reste un chevron — et un
                  chevron à l'extrémité d'une barre ne dit pas ce qu'il ouvre.
                  C'est le parti pris que la veille a pris à Grafana, dont
                  l'assistant n'est jamais ouvert d'office mais dont le bouton
                  est **nommé et à la même place partout** : un défaut fermé ne
                  se rattrape pas par un onboarding, il se rattrape par un bouton
                  qui ne bouge pas. Le regard neuf de #1107 l'a explicitement
                  repris de la variante C.
                  Seulement quand la colonne est **repliée** : ouverte, elle est
                  sous les yeux et se titre elle-même — le libellé ne dirait
                  qu'une seconde fois ce que l'écran montre déjà. Et masqué
                  quand la barre est étroite, où le titre de page a la priorité,
                  comme le coût cumulé et « Reconnexion… » juste au-dessus. Le
                  nom accessible, lui, ne dépend pas de la largeur : il reste
                  porté par l'`aria-label`.
                  ⚠ Ce seuil est lu sur la **barre** (`@xl`) depuis #1108, et
                  non plus sur la fenêtre (`sm`) : « comme le coût cumulé et
                  Reconnexion… juste au-dessus » était l'intention de #1107, ce
                  n'était plus le cas depuis que ces deux-là se mesurent à la
                  barre. Un libellé de 110 px qui apparaît sur la foi d'une
                  largeur de fenêtre est précisément ce que ce ticket corrige :
                  la colonne repliée rend la barre large, mais c'est la barre
                  qui doit le dire. */}
              {!conversationOuverte && (
                <span className="hidden text-corps @xl:inline">Conversation</span>
              )}
            </button>
          )}
        </div>
      </div>
    </header>
  );
}

/**
 * La place tenue par un lot à venir : visible et désactivée plutôt qu'absente —
 * la barre garde sa géométrie définitive dès maintenant.
 */
function EmplacementReserve({
  libelle,
  lot,
  Icone,
}: {
  libelle: string;
  lot: number;
  Icone: (props: { className?: string }) => ReactNode;
}) {
  return (
    <button
      type="button"
      disabled
      // Le numéro de ticket a rejoint le nom accessible (#536) : sur un bouton
      // `disabled`, le `title` n'apparaît de toute façon pas dans plusieurs
      // navigateurs — l'information n'était donc lisible à peu près nulle part.
      aria-label={`${libelle} — bientôt disponible (ticket #${lot})`}
      className="rounded-md p-2 text-neutral-400 opacity-50 dark:text-neutral-600"
    >
      <Icone className="size-5" />
    </button>
  );
}
