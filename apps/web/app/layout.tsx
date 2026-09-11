import type { Metadata, Viewport } from "next";

import { Shell } from "@/components/Shell";
import { SCRIPT_INIT_THEME } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "Maestro — Control Tower",
  description:
    "Poste de pilotage de l'orchestration : agents, tâches et coûts en temps réel.",
};

/**
 * Le clavier virtuel réduit le viewport de **mise en page**, pas seulement le
 * visuel (#892, parti pris 4 de la veille #873).
 *
 * Sans cette clé, c'est le défaut de la spécification qui s'applique —
 * `resizes-visual` : le viewport visuel rétrécit, celui de mise en page garde
 * sa hauteur pleine. Tout ce qui se cale « en bas de l'écran » vise alors un
 * bord que le clavier couvre — le `sticky bottom-16` du composeur (#726), la
 * bande `sticky bottom-0 h-16` qui le suit, la réserve `after:h-24` du `Shell`
 * (#888) —, un décalage `sticky` se mesurant contre le scrollport, que le
 * défaut laisse intact.
 *
 * **Mesuré** sur Chrome Android le 2026-09-11, clavier ouvert, hauteur
 * initiale 779 px : sans la clé, `clientHeight` reste à **779** pendant que
 * `visualViewport.height` tombe à **461** — 318 px ignorés ; avec elle,
 * `clientHeight` suit à **460**. Deux choses à en retenir. `innerHeight` suit
 * le viewport de **mise en page** des deux côtés (779 puis 460) : il ne sert
 * donc pas à détecter le clavier. Et le défaut ne se voit **pas** sur le geste
 * le plus simple — le navigateur fait glisser la page pour ramener l'élément
 * focalisé dans la vue, si bien que le composeur paraît bien posé dans les
 * deux cas ; il se voit en **défilant** clavier ouvert, et sur ce qui est
 * dimensionné en `dvh`.
 *
 * C'est d'ailleurs là que le gain est net : `100dvh` valait 779 clavier
 * ouvert, donc les `calc(100dvh - …)` des colonnes collantes de `/chat` et
 * `/couts` se dimensionnaient contre une hauteur dont 318 px étaient couverts.
 * MDN met en garde sur l'autre versant — le contenu se redimensionne quand le
 * clavier monte —, et c'est le prix assumé : la valeur devient juste, au prix
 * d'un remous.
 */
export const viewport: Viewport = {
  interactiveWidget: "resizes-content",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    // Le serveur ne connaît pas le choix de l'utilisateur : il rend le thème
    // clair, que le script ci-dessous corrige avant le premier rendu (#118).
    // `suppressHydrationWarning` couvre cet écart attendu sur `data-theme` —
    // React garde le DOM déjà corrigé au lieu de le rétablir. Sans JavaScript,
    // l'interface reste en clair, lisible.
    <html
      lang="fr"
      data-theme="clair"
      className="h-full antialiased"
      suppressHydrationWarning
    >
      <head>
        <script dangerouslySetInnerHTML={{ __html: SCRIPT_INIT_THEME }} />
      </head>
      {/* `h-full` et non `min-h-full` (#248) : une hauteur **définie**, et non
          un simple plancher. C'est ce qui permet à une section de « prendre la
          hauteur disponible » — sans elle, un descendant en `flex-1` se
          dimensionne sur son contenu, la page s'allonge, et un
          `overflow-y-auto` plus bas n'a jamais rien à faire défiler (mesuré :
          le Kanban montait à 5 198 px au lieu de tenir dans la fenêtre).
          Le cadre ne défile donc plus lui-même — c'est la zone de contenu du
          shell qui s'en charge.

          `suppressHydrationWarning` **ici aussi** (#730), et pour une tout
          autre raison que celui de `<html>` : les extensions de navigateur
          décorent `<body>` **avant** que React n'hydrate — Grammarly y pose
          `data-gr-ext-installed` et `data-new-gr-c-s-check-loaded`, LastPass et
          consorts font de même —, et React signalait la divergence à **chaque**
          chargement. Rien ne cassait : sur un écart d'**attribut** React garde
          ceux du client et poursuit (« This won't be patched up »), là où un
          écart de **texte** le ferait re-rendre depuis la frontière la plus
          proche. Ce qui coûtait, c'est qu'une console rouge en permanence
          apprend à ne plus lire les erreurs d'hydratation — alors qu'il en
          existe de vraies : #312 en était une, un `<form>` imbriqué dans le
          formulaire de projet.

          ⚠ Trois choses à ne pas défaire. L'attribut ne vaut qu'**un niveau** —
          les attributs de l'élément et ses enfants texte directs, jamais ses
          descendants —, et c'est précisément ce qui le rend acceptable ici : il
          tolère ce que le dehors pose sur `<body>` sans rien masquer de ce que
          `<Shell>` et l'arbre en dessous rendent, si bien qu'une divergence
          comme celle de #312 rougirait toujours. Il ne **remplace pas** celui
          de `<html>` : les deux écarts sont distincts et cumulatifs — l'un est
          le `data-theme` que `SCRIPT_INIT_THEME` corrige avant le premier rendu
          (#118), l'autre vient du dehors. Et il ne nomme **aucune** extension :
          c'est la classe entière qui est visée, un correctif par extension
          serait à refaire à chaque nouvelle. */}
      <body
        className="flex h-full flex-col overflow-hidden"
        suppressHydrationWarning
      >
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
