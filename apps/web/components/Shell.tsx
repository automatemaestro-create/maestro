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

import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { AssistantFlottant } from "@/components/AssistantFlottant";
import { BandeauMagasin } from "@/components/BanniereErreurApi";
import { BarreLaterale } from "@/components/BarreLaterale";
import { BarreSuperieure } from "@/components/BarreSuperieure";
import { BasculeTheme } from "@/components/BasculeTheme";
import { CentreNotifications } from "@/components/CentreNotifications";
import { ColonneConversation } from "@/components/ColonneConversation";
import { GuidePriseEnMain } from "@/components/GuidePriseEnMain";
import { MenuAide } from "@/components/MenuAide";
import { RegionArbitrage } from "@/components/RegionLive";
import { ChoixProjet, EcranOuverture } from "@/components/projets/ChoixProjet";
import { SelecteurProjet } from "@/components/projets/SelecteurProjet";
import { ASCENSEUR_PAGE, ecouterDefilement } from "@/lib/ascenseur";
import { FournisseurEtatGlobal, useEtatGlobal } from "@/lib/etatGlobal";
import { entreeParLibelle } from "@/lib/navigation";
import { FournisseurProjetActif, useProjetActif } from "@/lib/etatProjetActif";
import { FournisseurMagasin } from "@/lib/magasin";
import {
  ecouterConversationOuverte,
  ecouterRepliSidebar,
  ecrireConversationOuverte,
  ecrireRepliSidebar,
  lireConversationOuverte,
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

/**
 * Le grand format de la conversation (#926) — résolu par le menu, jamais écrit
 * en dur, et `undefined` si « Chat » quittait le menu (contrat de `hrefRun`).
 * La colonne y renvoie, le shell s'y replie : la même route, lue au même
 * endroit.
 */
const HREF_CHAT = entreeParLibelle("Chat")?.href;

export function Shell({ children }: { children: React.ReactNode }) {
  // L'ascenseur discret du socle (#725) se montre pendant le défilement, et CSS
  // n'a aucun état pour le dire : `lib/ascenseur` marque l'élément qui défile.
  // Posé ici, **au-dessus** de la garde du projet — la porte d'entrée défile
  // aussi (`ChoixProjet`), et il n'y a qu'un document à écouter.
  useEffect(() => ecouterDefilement(document), []);
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
  const pathname = usePathname();
  const [repliee, setRepliee] = useState(false);
  // La troisième zone (#925) : fermée au premier rendu, puis résolue comme le
  // repli ci-dessous.
  //
  // ⚠ `false` ici est l'état du **rendu serveur**, pas le défaut du produit :
  // depuis #1107 celui-ci est « ouverte au large » et se résout dans l'effet,
  // contre le `localStorage` et la largeur de la fenêtre — deux choses que le
  // serveur ne connaît pas. La colonne s'ouvre donc juste après l'hydratation,
  // exactement comme elle était restituée jusqu'ici pour qui l'avait laissée
  // ouverte : c'est le même chemin, et il n'y en a pas d'autre sans rendre la
  // largeur au serveur.
  const [conversationOuverte, setConversationOuverte] = useState(false);

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

  // Même mécanique, même raison (#925) : un second effet plutôt qu'un ajout au
  // premier, pour que chaque zone garde son abonnement et son nettoyage — deux
  // préférences indépendantes, dont l'une peut changer sans l'autre.
  useEffect(() => {
    const tick = setTimeout(
      () => setConversationOuverte(lireConversationOuverte()),
      0,
    );
    const detacher = ecouterConversationOuverte(setConversationOuverte);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, []);

  // Le stockage tranche : on écrit, l'abonnement ci-dessus met l'état à jour —
  // ici comme depuis les Paramètres, un seul chemin de bascule.
  const basculerRepli = () => ecrireRepliSidebar(!repliee);
  const basculerConversation = () =>
    ecrireConversationOuverte(!conversationOuverte);

  /**
   * **Une seule conversation à l'écran** (#926, parti pris 3 de sa veille) : sur
   * `/chat`, le fil occupe déjà le centre, donc la colonne se replie et son
   * bouton de bascule quitte la barre supérieure — il n'y a rien à déplier
   * quand on y est.
   *
   * Mesuré dehors : VS Code, en agrandissant sa conversation, met la navigation
   * et le centre à **zéro** — jamais la même conversation deux fois. Et ici ce
   * n'est pas qu'une affaire de doublon visuel : `useChat` ouvre une **WebSocket
   * par instance**, donc deux fils montés sur `orchestrateur` en ouvriraient
   * deux.
   *
   * ⚠ La **préférence n'est pas écrite** au passage, et c'est le point : on
   * masque, on ne ferme pas. L'écrire à `false` ferait qu'en quittant `/chat` la
   * colonne resterait repliée — la page aurait éteint un réglage qui ne lui
   * appartient pas. En sortant, elle revient exactement comme on l'avait
   * laissée. Ce que l'on écrivait, lui, survit dans `lib/brouillons`.
   */
  const surLeChat = HREF_CHAT !== undefined && pathname === HREF_CHAT;
  const colonneOuverte = conversationOuverte && !surLeChat;

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
      {/* La sonde du magasin (#1206) : une pour toute l'application, lue par le
          bandeau système ci-dessous et par le bandeau d'écran, qui se tait
          quand le shell dit déjà la même panne. Au retour du magasin, l'état
          se relit (#1217) — sans quoi un écran dirait encore la panne. */}
      <MagasinDuShell>
        {/* Le lien d'évitement (#537, WCAG 2.2 §2.4.1). Le produit n'en avait
          aucun (docs/30 §3.4) : au clavier, chaque écran commençait par
          **toutes les entrées** du menu (dix à l'époque, onze depuis #270),
          puis la barre supérieure, avant d'atteindre quoi que ce soit de la
          page — et le menu est identique partout, donc c'était autant de
          tabulations à repayer à chaque navigation.
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
        {/* La seule région `aria-live="assertive"` de l'application (#538) : les
          demandes d'arbitrage humain interrompent, et elles doivent s'entendre
          quel que soit l'écran ouvert. Elle est **sous** la clé du projet, comme
          l'état qu'elle annonce : changer de projet remonte la région, donc son
          premier relevé est celui du nouveau projet et la file du précédent ne
          s'annonce pas une dernière fois en partant.
          **Après** le lien d'évitement (#537) et pas avant : celui-ci doit
          rester le premier arrêt de la touche Tab, ce que seule sa position dans
          le DOM lui donne. La région, elle, n'est jamais focusable. */}
        <RegionArbitrage />
        {/* `min-h-0` tout le long (#248) : la hauteur définie posée par le
          `<body>` ne descend jusqu'aux pages que si chaque élément flex
          accepte de rétrécir sous son contenu — le `min-height:auto` par
          défaut le lui interdit, et un seul maillon manquant suffit à rendre
          la chaîne indéfinie. */}
        <div className="flex min-h-0 flex-1">
          <BarreLaterale repliee={repliee} />
          {/* `data-ascenseur="page"` (#882, parti pris 1 de la veille #859) : cet
            ascenseur-ci est celui de la **page**, et le socle le peint sans
            condition là où toutes les autres surfaces s'effacent au repos
            (`app/globals.css`, « L'ascenseur discret »). Un marqueur est
            nécessaire faute d'un sélecteur qui distingue la page d'une colonne
            bornée — et c'est un **attribut de données**, pas une classe
            utilitaire : le CSS lit un contrat, que nul refactor de Tailwind ne
            retire (même raison que `data-defilement`). La valeur vient de
            `lib/ascenseur`, qui la nomme pour le JSX comme pour la sonde qui
            lit les octets de la feuille : renommée d'un seul côté, la barre de
            page redeviendrait tributaire du pointeur sans que rien ne casse. */}
          <div
            data-ascenseur={ASCENSEUR_PAGE}
            className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto"
          >
            <BarreSuperieure
              repliee={repliee}
              basculerRepli={basculerRepli}
              selecteurProjet={<SelecteurProjet />}
              notifications={<CentreNotifications />}
              theme={<BasculeTheme />}
              aide={<MenuAide />}
              conversationOuverte={colonneOuverte}
              // Sur `/chat` le bouton **disparaît** — `BarreSuperieure` ne le rend
              // que si la bascule lui est donnée. C'est plus juste que de le
              // laisser inerte : on est déjà dans la conversation, il n'y a rien à
              // déplier, et un bouton qui ne fait rien s'apprend comme un bouton
              // cassé.
              basculerConversation={
                surLeChat ? undefined : basculerConversation
              }
            />
            {/* Le bandeau **système** de la perte du magasin (#1206, variante C
              retenue par le regard neuf) : directement sous la barre, comme
              Carbon y place un message qui vaut pour tout le produit. Une zone
              du **shell**, hors de `#contenu-principal` — inventoriée par
              `frontiere-shell-ecran.test.tsx` —, rendue seulement tant que
              la panne est vraie. Il défile avec le contenu et le pousse sans
              le recouvrir : il déclare périmé ce qui est dessous, le cacher
              serait taire ce qu'il dit. */}
            <BandeauMagasin />
            {/* `@container` : la sidebar prend de la largeur au contenu, donc les
              grilles des pages se calent sur la largeur **réelle** de cette
              zone (`@md:`, `@3xl:`…) et non sur celle de la fenêtre.
              `data-guide` : ancre de repli de la visite guidée (#122) quand la
              page ne rend pas encore le panneau qu'une étape vise.
              `after:h-24` — la **réserve du bouton flottant** (#888) : la bande
              que le bouton de l'assistant (#123) occupe en bas à droite est
              réservée par un pseudo-élément de 96 px, **dernier élément du
              flux** de `main` — aucun contenu ne se termine sous lui, donc
              aucune action de la page (décider une validation…) ne finit
              masquée. Ce fut un `pb-24` sur `main` de #123 à #888, juste tant
              que la page tenait dans la fenêtre : depuis #248 `main` est une
              boîte à hauteur fixée (`min-h-0 flex-1`) que le contenu dépasse
              dès qu'une page est plus haute qu'elle, et un padding enfermé
              dans une boîte déjà dépassée n'est plus nulle part — au bas du
              défilement la fin de page affleurait le bord (mesuré au banc :
              36 px du dernier message sous le composeur à quai, les dernières
              lignes du journal sous le flottant). ⚠ Le porter sur
              l'**ascenseur** (`pb-24` sur le `div … overflow-y-auto` ci-dessus,
              la piste du ticket) a été **mesuré faux** : Chrome n'ajoute le
              padding de fin d'un conteneur défilant qu'à ses boîtes en flux
              **directes** (`main`, fixée), jamais au débordement de leurs
              descendants — marge sous le contenu toujours 0 au bas du
              défilement —, et il y calcule le `sticky` contre la boîte de
              **contenu** de l'ascenseur, ce qui remontait le composeur à quai
              de 96 px de plus (bas du formulaire à 640 pour 800 de haut, quand
              `bottom-16` en promet 736). Un élément du flux, lui, suit le
              contenu où qu'il aille : quand la page déborde, il est après elle
              dans le débordement défilable, et au bas du défilement les 96
              derniers pixels ne portent que du fond (mesuré : le formulaire du
              fil finit à 704 pour 800 de haut, le dernier message 28 px
              au-dessus de lui). `after:-mt-6` reprend le `gap-6` pour que la
              réserve fasse 96 px et non 120, et `main` n'a **aucun padding
              bas** : la géométrie d'une page qui tient ne bouge pas — le
              Kanban (#248) finit exactement où il finissait. Deux choses en
              dépendent : le composeur à quai (`sticky bottom-16`,
              `Conversation.tsx`) compte sur ces 96 px pour retrouver sa place
              naturelle au bas du fil, et les colonnes de propriétés collantes
              (`/chat`, `/couts`) les retranchent de leur plafond.
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
              className="@container mx-auto flex min-h-0 w-full max-w-screen-2xl flex-1 flex-col gap-6 px-4 pt-4 outline-none after:-mt-6 after:block after:h-24 after:shrink-0 focus-visible:outline-2 focus-visible:outline-sky-600 sm:px-6 sm:pt-6"
            >
              {children}
            </main>
          </div>
          {/* La troisième zone (#925, docs/35 §3) : sœur de la colonne centrale,
            donc **hors** de `<main>` — c'est ce qui la tient hors du comptage de
            `sobriete.test.tsx`, qui ne recense que les blocs de
            `#contenu-principal`. Une zone du shell n'est pas un bloc de plus
            dans l'écran, et une `<aside>` posée *dedans* aurait été la « sortie
            de secours » que docs/35 §3.4 nomme : la seule place sans plafond, où
            un écran plein rangerait son quatrième bloc. La frontière est portée
            par le code depuis le lot 11 (#929, `frontiere-shell-ecran.test.tsx`)
            et non par ce commentaire : ce qu'un écran rend hors de `<main>` est
            comparé à ce que le shell rend seul. Et `BLOCS_MAX` ne se relève
            jamais — il est confronté au texte de docs/30 §4.1.
            Après la colonne centrale dans le DOM, donc dernier dans l'ordre de
            tabulation : la conversation se consulte en marge du travail, elle ne
            se met pas devant. */}
          <ColonneConversation
            ouverte={colonneOuverte}
            fermer={() => ecrireConversationOuverte(false)}
          />
        </div>
        {/* Hors flux (position fixe) : la visite se superpose au shell entier, et
          l'assistant (#123) flotte sur toutes les pages — la visite passant
          par-dessus lui (`z-40`/`z-50` contre `z-30`). */}
        <AssistantFlottant />
        <GuidePriseEnMain />
      </MagasinDuShell>
    </FournisseurEtatGlobal>
  );
}

/**
 * La sonde du magasin, branchée sur l'état du shell (#1217) : au retour d'un
 * magasin perdu, l'état se relit, et le pouls avec lui. Un composant à part
 * parce que la relecture vit **sous** `FournisseurEtatGlobal`, que ce fichier
 * monte juste au-dessus.
 */
function MagasinDuShell({ children }: { children: React.ReactNode }) {
  const { relire } = useEtatGlobal();
  return <FournisseurMagasin auRetour={relire}>{children}</FournisseurMagasin>;
}
