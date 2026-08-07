import type { Metadata } from "next";

import { Shell } from "@/components/Shell";
import { SCRIPT_INIT_THEME } from "@/lib/theme";

import "./globals.css";

export const metadata: Metadata = {
  title: "Maestro — Control Tower",
  description:
    "Poste de pilotage de l'orchestration : agents, tâches et coûts en temps réel.",
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
          shell qui s'en charge. */}
      <body className="flex h-full flex-col overflow-hidden">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
