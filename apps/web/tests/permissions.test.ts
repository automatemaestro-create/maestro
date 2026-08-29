/**
 * La règle de portée d'une entrée de politique (#262, `lib/permissions`).
 *
 * Tests différés au lot 15 pour tout le reste du ticket — sauf ceci, pour la
 * raison qui a valu son test à `lib/competences` (#256) : un appariement de
 * préfixe ne se voit ni au lint, ni au typage, ni à l'écran. Un `mcp__slack`
 * qui couvrirait `mcp__slackbot` ne se remarquerait qu'en production, sur un
 * agent qui perdrait un serveur sans que personne l'ait demandé.
 *
 * Le pendant Python (`_correspond`) décide du même appariement à l'exécution :
 * ce qui est gardé ici est que l'écran dise la même chose que le moteur.
 */

import { describe, expect, it } from "vitest";

import { couvre, entreeConnue, entreesHorsPortee } from "@/lib/permissions";
import type { OutilExpose } from "@/lib/types";

const outils: OutilExpose[] = [
  { nom: "Bash", origine: "integre", libelle: "outil intégré du profil" },
  { nom: "Read", origine: "integre", libelle: "outil intégré du profil" },
  {
    nom: "mcp__slack",
    origine: "mcp",
    libelle: "serveur MCP slack (tous ses outils)",
  },
];

describe("couvre", () => {
  it("reconnaît le nom exact", () => {
    expect(couvre("Bash", "Bash")).toBe(true);
  });

  it("couvre ce qu'elle préfixe à une frontière __", () => {
    expect(couvre("mcp__slack", "mcp__slack__send_message")).toBe(true);
  });

  it("ne couvre jamais un préfixe en plein mot", () => {
    // Le piège du motif : `mcp__slack` ne dit rien de `mcp__slackbot`, qui est
    // un autre serveur. Sans la frontière, refuser l'un refuserait l'autre.
    expect(couvre("mcp__slack", "mcp__slackbot__envoyer")).toBe(false);
  });
});

describe("entreeConnue", () => {
  it("reconnaît un outil exposé cité tel quel", () => {
    expect(entreeConnue("Bash", outils)).toBe(true);
  });

  it("reconnaît un outil précis d'un serveur exposé", () => {
    // Le sens que la fiche ne suggère pas : elle cite le serveur entier, on
    // écrit l'un de ses outils. C'est légitime, donc non signalé.
    expect(entreeConnue("mcp__slack__send_message", outils)).toBe(true);
  });

  it("reconnaît un serveur dont seul un outil est exposé", () => {
    const precis: OutilExpose[] = [
      {
        nom: "mcp__maestro__demander_arbitrage",
        origine: "maestro",
        libelle: "demander un arbitrage",
      },
    ];
    expect(entreeConnue("mcp__maestro", precis)).toBe(true);
  });

  it("ne reconnaît pas ce que rien d'exposé n'explique", () => {
    expect(entreeConnue("mcp__slackbot", outils)).toBe(false);
    expect(entreeConnue("WebFetch", outils)).toBe(false);
  });
});

describe("entreesHorsPortee", () => {
  it("ne retient que les entrées sans rattachement", () => {
    const hors = entreesHorsPortee(
      ["Bash", "mcp__slack__send_message", "WebFetch"],
      outils,
    );
    expect([...hors]).toEqual(["WebFetch"]);
  });

  it("ne signale rien quand la liste est vide", () => {
    expect(entreesHorsPortee([], outils).size).toBe(0);
  });
});
