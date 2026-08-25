"use client";

/**
 * Le shell applicatif de la Control Tower (#117, lot 1 de #116) : le cadre
 * commun à toutes les pages — sidebar de navigation, barre supérieure et zone
 * de contenu. Chaque page ne rend plus que son contenu ; l'en-tête, le retour
 * au tableau de bord et les indicateurs globaux vivent ici, une seule fois.
 *
 * Le repli de la sidebar est tenu ici : la sidebar en dépend pour sa largeur,
 * la barre supérieure porte le bouton qui le bascule.
 *
 * Depuis #279 le shell porte aussi la **garde du projet actif** : tant qu'aucun
 * projet n'est choisi, c'est la porte d'entrée qui occupe l'écran et le cadre
 * ci-dessous n'est pas monté du tout. Depuis #281 il en est en plus la **source
 * de portée** : le projet passé au fournisseur d'état cadre toutes les lectures
 * et le flux temps réel, écran par écran. Depuis #280, enfin, il le rend
 * **visible et changeable** en permanence — le sélecteur de la barre supérieure,
 * qui remplace l'entrée « Projets » de la barre latérale.
 */

import { useEffect, useState } from "react";

import { AssistantFlottant } from "@/components/AssistantFlottant";
import { BarreLaterale } from "@/components/BarreLaterale";
import { BarreSuperieure } from "@/components/BarreSuperieure";
import { BasculeTheme } from "@/components/BasculeTheme";
import { CentreNotifications } from "@/components/CentreNotifications";
import { GuidePriseEnMain } from "@/components/GuidePriseEnMain";
import { MenuAide } from "@/components/MenuAide";
import { ChoixProjet, EcranOuverture } from "@/components/projets/ChoixProjet";
import { SelecteurProjet } from "@/components/projets/SelecteurProjet";
import { FournisseurEtatGlobal } from "@/lib/etatGlobal";
import { FournisseurProjetActif, useProjetActif } from "@/lib/etatProjetActif";
import {
  ecouterRepliSidebar,
  ecrireRepliSidebar,
  lireRepliSidebar,
} from "@/lib/preferences";
import type { Projet } from "@/lib/types";

/**
 * L'ancre du contenu principal (#537), visée par le lien d'évitement.
 *
 * Exportée plutôt qu'écrite deux fois : le lien et sa cible sont à deux endroits
 * du même fichier, et une faute de frappe entre eux ne se voit ni au lint, ni au
 * build, ni dans un rendu — le lien mènerait simplement nulle part.
 */
export const ID_CONTENU_PRINCIPAL = "contenu-principal";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <FournisseurProjetActif>
      <PorteProjet>{children}</PorteProjet>
    </FournisseurProjetActif>
  );
}

/**
 * La garde de #279 : pas de projet actif, pas de Control Tower.
 *
 * C'est une garde de **shell** et non une redirection, et c'est ce qui rend le
 * troisième critère gratuit : l'URL demandée ne bouge pas, si bien qu'un lien
 * profond ou un rechargement retrouve sa page dès le choix fait — rien à
 * mémoriser, rien vers quoi renvoyer, aucun aller-retour à défaire dans
 * l'historique du navigateur.
 *
 * Le cadre entier est **sous** la garde, `FournisseurEtatGlobal` compris : la
 * porte d'entrée n'ouvre donc ni WebSocket ni lecture d'API globale, alors que
 * la portée projet de ces lectures (#277) n'est pas encore connue.
 */
function PorteProjet({ children }: { children: React.ReactNode }) {
  const { projet, pret } = useProjetActif();
  if (!pret) return <EcranOuverture />;
  if (projet === null) return <ChoixProjet />;
  return <CadreControlTower projet={projet}>{children}</CadreControlTower>;
}

function CadreControlTower({
  projet,
  children,
}: {
  projet: Projet;
  children: React.ReactNode;
}) {
  const [repliee, setRepliee] = useState(false);

  // Lu après l'hydratation : le rendu serveur ne connaît pas le localStorage,
  // le lire pendant le rendu ferait diverger les deux arbres. Restitution
  // différée d'un tick (même mécanique que useControlTower) : l'effet lui-même
  // ne déclenche aucun setState synchrone. L'abonnement, lui, suit ensuite les
  // changements venus d'ailleurs — section Apparence des Paramètres (#121) ou
  // autre onglet.
  useEffect(() => {
    const tick = setTimeout(() => setRepliee(lireRepliSidebar()), 0);
    const detacher = ecouterRepliSidebar(setRepliee);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, []);

  // Le stockage tranche : on écrit, l'abonnement ci-dessus met l'état à jour —
  // ici comme depuis les Paramètres, un seul chemin de bascule.
  const basculerRepli = () => ecrireRepliSidebar(!repliee);

  return (
    // `key` : changer de projet **remonte** tout ce qui est dessous (#281).
    // C'est ce qui tient le critère « aucune donnée de l'ancien projet ne
    // subsiste » dans son entier — un rechargement des lectures suffirait pour
    // l'état temps réel, mais pas pour ce que les pages tiennent elles-mêmes :
    // les filtres du Journal (dont les listes sont dérivées des événements du
    // projet quitté), la période des Coûts, un panneau déplié. Le repli de la
    // sidebar, lui, est **au-dessus** de la clé : c'est une préférence
    // d'affichage, elle ne change pas avec le projet.
    <FournisseurEtatGlobal key={projet.id} projet={projet}>
      {/* Le lien d'évitement (#537, WCAG 2.2 §2.4.1). Le produit n'en avait
          aucun (docs/30 §3.4) : au clavier, chaque écran commençait par les
          **dix entrées** du menu, puis la barre supérieure, avant d'atteindre
          quoi que ce soit de la page — et le menu est identique partout, donc
          c'était dix tabulations à repayer à chaque navigation.
          Il est **premier dans l'ordre du DOM** et non seulement à l'écran :
          c'est sa position ici, avant la barre latérale, qui en fait le premier
          arrêt de la touche Tab.
          `sr-only` + `focus:not-sr-only` : invisible tant qu'il n'a pas le
          focus, visible dès qu'il l'a. Le masquer par `display:none` ou
          `visibility:hidden` le sortirait de l'ordre de tabulation, c'est-à-dire
          le supprimerait ; `sr-only` le garde atteignable. */}
      <a
        href={`#${ID_CONTENU_PRINCIPAL}`}
        className="sr-only focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50 focus:rounded-md focus:border focus:border-bord-fort focus:bg-surface focus:px-4 focus:py-2 focus:text-corps focus:font-medium focus:text-texte focus:shadow-lg"
      >
        Aller au contenu principal
      </a>
      {/* `min-h-0` tout le long (#248) : la hauteur définie posée par le
          `<body>` ne descend jusqu'aux pages que si chaque élément flex
          accepte de rétrécir sous son contenu — le `min-height:auto` par
          défaut le lui interdit, et un seul maillon manquant suffit à rendre
          la chaîne indéfinie. */}
      <div className="flex min-h-0 flex-1">
        <BarreLaterale repliee={repliee} />
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          <BarreSuperieure
            repliee={repliee}
            basculerRepli={basculerRepli}
            selecteurProjet={<SelecteurProjet />}
            notifications={<CentreNotifications />}
            theme={<BasculeTheme />}
            aide={<MenuAide />}
          />
          {/* `@container` : la sidebar prend de la largeur au contenu, donc les
              grilles des pages se calent sur la largeur **réelle** de cette
              zone (`@md:`, `@3xl:`…) et non sur celle de la fenêtre.
              `data-guide` : ancre de repli de la visite guidée (#122) quand la
              page ne rend pas encore le panneau qu'une étape vise.
              `pb-24` : la bande que le bouton de l'assistant (#123) occupe en bas
              à droite est réservée — aucun contenu ne se termine sous lui, donc
              aucune action de la page (décider une validation…) ne finit
              masquée.
              `min-h-0 flex-1` : la zone occupe la hauteur du cadre, ce qui
              donne enfin une hauteur à prendre à une page qui le demande (le
              Kanban, #248). Ce qui déborde fait défiler la colonne parente —
              la barre supérieure y reste collée (`sticky`) comme quand c'était
              la fenêtre qui défilait, et l'ascenseur reste au bord de l'écran
              plutôt qu'au bord de la colonne centrée. */}
          {/* `tabIndex={-1}` (#537) : sans lui, suivre le lien d'évitement
              déplace l'ancre du document mais **pas le focus** — Chrome et
              Firefox refusent de le poser sur un élément non focalisable, si
              bien que la tabulation suivante repartait de la barre latérale,
              c'est-à-dire exactement ce que le lien devait éviter.
              `focus-visible:` et non `focus:` : Chrome pose le focus sur un
              `tabindex="-1"` au simple **clic**, si bien qu'un `focus:` dessinerait
              un cadre pleine largeur à chaque clic dans le contenu. On garde donc
              la confirmation d'arrivée pour qui vient au clavier, et rien pour
              qui vient à la souris — plutôt qu'un `outline-none` sec, qui
              retirerait le seul signe que le saut a eu lieu. */}
          <main
            id={ID_CONTENU_PRINCIPAL}
            tabIndex={-1}
            data-guide="contenu"
            className="@container mx-auto flex min-h-0 w-full max-w-screen-2xl flex-1 flex-col gap-6 p-4 pb-24 outline-none focus-visible:outline-2 focus-visible:outline-sky-600 sm:p-6 sm:pb-24"
          >
            {children}
          </main>
        </div>
      </div>
      {/* Hors flux (position fixe) : la visite se superpose au shell entier, et
          l'assistant (#123) flotte sur toutes les pages — la visite passant
          par-dessus lui (`z-40`/`z-50` contre `z-30`). */}
      <AssistantFlottant />
      <GuidePriseEnMain />
    </FournisseurEtatGlobal>
  );
}
