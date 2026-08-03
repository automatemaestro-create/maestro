/**
 * Le lien vers le ticket externe d'une tâche (#192).
 *
 * Les tests de ce lot sont **différés au lot 4/4 (#193)** — sauf ceux-ci. Le
 * filtrage d'URL de `lienExterneSur` est de la logique critique au sens de
 * docs/10 §5.1 : l'URL vient du flux, et un `href` non filtré exécute du code
 * (`javascript:`) ou embarque une charge utile (`data:`). Une régression y
 * serait silencieuse — le lien resterait cliquable et aurait l'air sain.
 * Le reste (mise en page des cartes, tables de coûts) attend bien #193.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Kanban } from "@/components/Kanban";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { lienExterneSur } from "@/lib/liens";

import { tacheFactice } from "./aides";

describe("lienExterneSur", () => {
  it("suit http et https, normalise", () => {
    expect(lienExterneSur("https://gitlab.test/x/-/issues/192")).toBe(
      "https://gitlab.test/x/-/issues/192",
    );
    expect(lienExterneSur("HTTP://gitlab.test/a")).toBe("http://gitlab.test/a");
  });

  it("refuse tout le reste", () => {
    expect(lienExterneSur("javascript:alert(1)")).toBeNull();
    expect(lienExterneSur("data:text/html,<script>")).toBeNull();
    expect(lienExterneSur("file:///etc/passwd")).toBeNull();
    expect(lienExterneSur("//evil.test/x")).toBeNull();
    expect(lienExterneSur("/relatif")).toBeNull();
    expect(lienExterneSur("")).toBeNull();
    expect(lienExterneSur(null)).toBeNull();
    expect(lienExterneSur("  java\nscript:alert(1) ")).toBeNull();
  });
});

describe("LienTicketExterne", () => {
  it("ne rend rien sans référence", () => {
    const { container } = render(<LienTicketExterne reference={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("ne rend rien sur une référence vide", () => {
    const { container } = render(
      <LienTicketExterne reference={{ identifiant: "  ", url: "" }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("pose le lien en nouvel onglet avec noopener noreferrer", () => {
    render(
      <LienTicketExterne
        reference={{ identifiant: "#192", url: "https://gitlab.test/i/192" }}
        tache="Écrire les tests"
      />,
    );
    const lien = screen.getByRole("link", { name: /Ouvrir le ticket externe #192/ });
    expect(lien).toHaveAttribute("href", "https://gitlab.test/i/192");
    expect(lien).toHaveAttribute("target", "_blank");
    expect(lien).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("URL non suivable : texte, jamais de lien mort", () => {
    render(
      <LienTicketExterne
        reference={{ identifiant: "MAE-42", url: "javascript:alert(1)" }}
      />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("MAE-42")).toBeInTheDocument();
  });
});

describe("carte du Kanban", () => {
  it("porte le lien quand la tâche a une référence", () => {
    render(
      <Kanban
        taches={[
          tacheFactice({
            reference_externe: { identifiant: "#192", url: "https://gitlab.test/i/192" },
          }),
        ]}
        agents={[]}
        reassigner={async () => {}}
      />,
    );
    expect(
      screen.getByRole("link", { name: /Ouvrir le ticket externe #192/ }),
    ).toHaveAttribute("href", "https://gitlab.test/i/192");
  });

  it("reste inchangée sans référence", () => {
    render(
      <Kanban taches={[tacheFactice()]} agents={[]} reassigner={async () => {}} />,
    );
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByText(/Ticket externe/)).toBeNull();
  });
});
