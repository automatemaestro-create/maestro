/**
 * **Un fichier du livrable s'ouvre d'un geste, depuis le fil** (#1224).
 *
 * La fin d'un run laisse désormais dans son fil un message rédigé : ce qui a
 * été produit, comment l'essayer, et les fichiers qui comptent en liens. Côté
 * écran, tout tient dans une chose que le Markdown du fil ne savait pas faire —
 * viser un **fichier du poste**. Ce fichier garde les décisions qui s'effacent
 * en silence :
 *
 * ① **l'analyseur** — une destination bornée (`[x](<…>)`) qui est un chemin
 *    absolu devient un nœud `fichier`, une adresse `http(s)` reste un lien, et
 *    la forme **nue** ne devient jamais un fichier : c'est ce qui laisse intacte
 *    la conduite de #697 pour une destination relative ;
 * ② **le geste** — c'est un `<button>` et jamais une ancre (un `href` vers le
 *    disque ne se suit ni dans un onglet ni dans la fenêtre), il **nomme sa
 *    destination** (parti pris 5 de #928), et il **dit ce qu'il a fait** ;
 * ③ **la capacité, jamais la plateforme** (ENF-12) — la fenêtre montre le
 *    fichier, l'onglet copie son chemin, et le second n'est pas un lot de
 *    consolation : c'est le second geste de #928.
 *
 * Chaque sonde est prouvée sur un échantillon fautif avant qu'on conclue de ce
 * qu'elle ne trouve pas (méthode de #534) : un analyseur qui rendrait du texte
 * brut sur tout passerait sans cela chaque contrôle de ce fichier.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TexteMarkdown } from "@/components/chat/TexteMarkdown";
import { cheminLocalSur, nomDuFichier } from "@/lib/liens";
import { analyserMarkdown, type Inline } from "@/lib/markdown";

const CHEMIN = "E:/Mes projets/demo/app.py";

/** Les marques du paragraphe unique — de quoi juger l'analyseur sans le rendu. */
function marquesDe(texte: string): Inline[] {
  const blocs = analyserMarkdown(texte);
  expect(blocs).toHaveLength(1);
  const bloc = blocs[0];
  if (bloc.type !== "paragraphe") {
    throw new Error(`attendu un paragraphe, reçu « ${bloc.type} »`);
  }
  return bloc.enfants;
}

/** Pose (ou retire) le pont de la coque — une **capacité**, jamais une plateforme. */
function poserLaCoque(pont: Record<string, unknown> | null): void {
  if (pont === null) {
    delete (window as { maestro?: unknown }).maestro;
    return;
  }
  (window as { maestro?: unknown }).maestro = pont;
}

afterEach(() => {
  poserLaCoque(null);
  vi.restoreAllMocks();
});

// ─────────────────────────────────────────────────────────────────────────────
// ① L'analyseur
// ─────────────────────────────────────────────────────────────────────────────

describe("une destination qui vise le disque (lib/markdown)", () => {
  it("reconnaît un chemin absolu dans la forme bornée, espaces compris", () => {
    // L'espace est le fond du sujet : la forme nue (`[^\s()]+`) couperait le
    // chemin à « Mes », donc offrirait un geste sur un fichier qui n'existe pas.
    const marques = marquesDe(`Le point d'entrée : [app.py](<${CHEMIN}>)`);
    const fichier = marques.find((noeud) => noeud.type === "fichier");

    expect(fichier).toEqual({
      type: "fichier",
      chemin: CHEMIN,
      enfants: [{ type: "texte", texte: "app.py" }],
    });
  });

  it("garde un lien http borné en lien, et non en fichier", () => {
    // L'ordre des deux questions est une décision : une adresse suivable est un
    // lien, quelle que soit la forme dans laquelle elle a été écrite.
    const marques = marquesDe("[le ticket](<https://exemple.test/1224>)");

    expect(marques.map((noeud) => noeud.type)).toEqual(["lien"]);
  });

  it("ne fait jamais un fichier d'une destination nue", () => {
    // ⚠ La moitié qui garde #697 : une destination relative reste du **texte
    // lisible**. Le chemin ci-dessous serait accepté par `cheminLocalSur` — la
    // ligne suivante le prouve —, et il ne devient pourtant pas un geste : c'est
    // la forme, et elle seule, qui décide.
    expect(cheminLocalSur("/livrable/app.py")).toBe("/livrable/app.py");

    const marques = marquesDe("[a](/livrable/app.py) [b](/interne)");

    expect(marques.every((noeud) => noeud.type === "texte")).toBe(true);
  });

  it("laisse lisible une destination bornée qui n'est ni adresse ni chemin", () => {
    // Ni suivable, ni absolue : rien n'est posé, et le texte d'origine reste —
    // la même conduite que pour un lien refusé (#192).
    const marques = marquesDe("[x](<javascript:alert1>) [y](<./relatif.md>)");

    expect(marques.every((noeud) => noeud.type === "texte")).toBe(true);
  });
});

describe("cheminLocalSur", () => {
  it("accepte les trois formes absolues, et rien d'autre", () => {
    expect(cheminLocalSur("C:\\Mes projets\\app.py")).toBe("C:\\Mes projets\\app.py");
    expect(cheminLocalSur("C:/projets/app.py")).toBe("C:/projets/app.py");
    expect(cheminLocalSur("\\\\serveur\\partage\\app.py")).toBe(
      "\\\\serveur\\partage\\app.py",
    );
    expect(cheminLocalSur("/home/moi/app.py")).toBe("/home/moi/app.py");
    expect(cheminLocalSur("app.py")).toBeNull();
    expect(cheminLocalSur("./app.py")).toBeNull();
    expect(cheminLocalSur("https://exemple.test/a")).toBeNull();
    expect(cheminLocalSur("")).toBeNull();
  });

  it("écarte un chemin porteur de caractères de contrôle", () => {
    // Insérés pour tromper la lecture de ce qu'on s'apprête à ouvrir — même
    // raison que la normalisation de `lienExterneSur`.
    expect(cheminLocalSur("/home/moi/\u0000app.py")).toBeNull();
    expect(cheminLocalSur("/home/moi/a\npp.py")).toBeNull();
  });

  it("nomme le fichier au bout d'un chemin, quel que soit le séparateur", () => {
    expect(nomDuFichier("C:\\Mes projets\\app.py")).toBe("app.py");
    expect(nomDuFichier("/home/moi/app.py")).toBe("app.py");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// ② et ③ — le geste, et la capacité
// ─────────────────────────────────────────────────────────────────────────────

describe("le geste posé sur un fichier du livrable", () => {
  it("est un bouton, jamais une ancre vers le disque", async () => {
    // Un `href` vers le disque ne se suit ni dans un onglet (`file:` est bloqué
    // depuis une page http) ni dans la fenêtre (qui ne navigue que sur l'origine
    // locale — refus consigné dans la veille de #928) : poser une ancre morte
    // promettrait ce que rien ne tient.
    poserLaCoque({ montrerFichier: vi.fn().mockResolvedValue(true) });

    const { container } = render(
      <TexteMarkdown texte={`Voir [app.py](<${CHEMIN}>)`} />,
    );

    expect(container.querySelector("a")).toBeNull();
    expect(screen.getByRole("button")).toHaveTextContent("app.py");
  });

  it("montre le fichier quand la fenêtre le sait, et nomme ce qu'il fera", async () => {
    const montrer = vi.fn().mockResolvedValue(true);
    poserLaCoque({ montrerFichier: montrer });

    render(<TexteMarkdown texte={`Voir [app.py](<${CHEMIN}>)`} />);
    const bouton = screen.getByRole("button", {
      name: "Montrer app.py dans l'explorateur",
    });
    await userEvent.click(bouton);

    expect(montrer).toHaveBeenCalledWith(CHEMIN);
    // Rien à dire quand c'est fait : l'explorateur s'est ouvert sous les yeux.
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("dit quand le fichier n'est plus là", async () => {
    // Un geste sans effet ni message ne se distingue pas d'une page figée
    // (#928). La coque refuse ce qui n'existe pas, et l'écran le relaie.
    poserLaCoque({ montrerFichier: vi.fn().mockResolvedValue(false) });

    render(<TexteMarkdown texte={`Voir [app.py](<${CHEMIN}>)`} />);
    await userEvent.click(screen.getByRole("button"));

    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Fichier introuvable"),
    );
  });

  it("copie le chemin dans un onglet — le second geste de #928, pas un pis-aller", async () => {
    // ENF-12 : la question est « puis-je ? », jamais « où suis-je ? ». Sans
    // pont, le geste reste offert et **change de nom** : c'est le nom
    // accessible qui dit ce qui va se passer.
    poserLaCoque(null);
    const ecrire = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", {
      configurable: true,
      value: { writeText: ecrire },
    });

    render(<TexteMarkdown texte={`Voir [app.py](<${CHEMIN}>)`} />);
    const bouton = screen.getByRole("button", {
      name: "Copier le chemin de app.py",
    });
    await userEvent.click(bouton);

    expect(ecrire).toHaveBeenCalledWith(CHEMIN);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("Chemin copié"),
    );
  });

  it("porte le chemin entier en infobulle, libellé court ou non", () => {
    // Le libellé est court par nature (le nom du fichier) ; savoir **où** il est
    // reste une question qu'on doit pouvoir poser sans copier quoi que ce soit.
    poserLaCoque({ montrerFichier: vi.fn() });

    render(<TexteMarkdown texte={`Voir [app.py](<${CHEMIN}>)`} />);

    expect(screen.getByRole("button")).toHaveAttribute("title", CHEMIN);
  });

  it("retombe sur le nom du fichier quand le libellé est vide", () => {
    // `[](<…>)` est du Markdown légal, et un bouton sans nom est un bouton que
    // personne ne peut annoncer (a11y).
    poserLaCoque({ montrerFichier: vi.fn() });

    render(<TexteMarkdown texte={`Voir [](<${CHEMIN}>)`} />);

    expect(screen.getByRole("button")).toHaveTextContent("app.py");
  });
});
