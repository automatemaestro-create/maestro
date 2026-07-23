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
      <body className="flex min-h-full flex-col">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
