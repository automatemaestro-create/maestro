import type { Metadata } from "next";

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
    <html lang="fr" className="h-full antialiased">
      <body className="flex min-h-full flex-col">{children}</body>
    </html>
  );
}
