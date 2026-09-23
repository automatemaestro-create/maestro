/**
 * **Ce que l'orchestrateur a lu** se voit dans le fil (#1223, lot 2 de #1221).
 *
 * Il répondait sur un contexte figé ; il ouvre désormais les fichiers du projet,
 * cherche dedans et relit le détail complet d'un run. Sans ces lignes à l'écran,
 * une réponse juste se lirait comme une réponse sûre d'elle — c'est la question
 * que le ticket pose : *vois-je ce qu'il a consulté pour me répondre ?*
 *
 * La forme a été choisie sur pièces (#1009) : trois variantes rendues sur la
 * vraie stack, deux références capturées en direct, le choix rendu par le
 * sous-agent `regard-neuf` et consigné sur le ticket. Ce fichier garde ce qui en
 * a été retenu, et **rien de ce qui relève du goût** :
 *
 * ① **une seule ligne, repliée** — c'est le compte qu'on lit sans rien ouvrir,
 *    et les lectures n'apparaissent qu'au dépli. La variante « à plat, toujours
 *    visible » a été écartée parce qu'elle occupait plus de hauteur que les
 *    réponses ;
 * ② **au-dessus de la réponse** — l'ordre réel des choses : il lit, puis il
 *    rédige. La variante « en pied de réponse » a été écartée pour cela ;
 * ③ **le dépli montre les lectures, pas leur contenu** — chaque extrait a son
 *    propre repli, fermé ;
 * ④ **rien à montrer ⇒ rien à rendre** — un message ordinaire ne laisse aucune
 *    ligne au-dessus de lui, comme `Suite` et `SourcesDuFil` ;
 * ⑤ **pendant qu'il répond** — la bulle en cours les affiche **dépliées** tant
 *    qu'aucun mot n'est écrit : à cet instant, c'est tout ce qu'il y a à voir.
 *
 * La couture flux → état (la trame `etape` qui alimente `reponseEnCours`) se
 * juge sur le **vrai** hook et vit donc dans `chat-direct.test.tsx`.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { EtapesDuFil } from "@/components/chat/EtapesDuFil";
import type { EtapeFil } from "@/lib/types";

const LECTURES: EtapeFil[] = [
  { libelle: "A listé « . »", detail: "README.md\npackage.json\nsrc/" },
  {
    libelle: "A lu « README.md »",
    detail: "# Animation du logo\n\n## Lancer\n\n    npm run dev\n",
  },
  { libelle: "A cherché « npm run »", detail: "README.md:5:    npm run dev" },
];

/** Le repli de tête — celui qui porte le compte. */
function repliDeTete(): HTMLElement {
  const bloc = document.querySelector("[data-etapes-du-fil]");
  if (bloc === null) throw new Error("aucun bloc d'étapes rendu");
  return bloc as HTMLElement;
}

describe("les étapes de lecture dans le fil (#1223)", () => {
  it("ne rend rien quand rien n'a été lu", () => {
    const { container } = render(<EtapesDuFil etapes={[]} />);

    // Pas « un bloc vide », pas « une ligne à zéro » : rien. Un message
    // ordinaire doit être exactement celui d'avant ce lot.
    expect(container).toBeEmptyDOMElement();
  });

  it("dit le compte sans rien déplier, et tait les lectures", () => {
    render(<EtapesDuFil etapes={LECTURES} />);

    expect(screen.getByText("A consulté 3 éléments")).toBeInTheDocument();
    // Le repli est fermé : c'est la propriété qui a écarté la variante à plat.
    expect(repliDeTete()).not.toHaveAttribute("open");
  });

  it("accorde le compte au singulier", () => {
    render(<EtapesDuFil etapes={[LECTURES[0]]} />);

    expect(screen.getByText("A consulté 1 élément")).toBeInTheDocument();
  });

  it("nomme chaque lecture au dépli, sans ouvrir leurs extraits", async () => {
    const utilisateur = userEvent.setup();
    render(<EtapesDuFil etapes={LECTURES} />);

    await utilisateur.click(screen.getByText("A consulté 3 éléments"));

    const lectures = within(repliDeTete()).getAllByRole("listitem");
    expect(lectures.map((ligne) => ligne.textContent)).toEqual([
      expect.stringContaining("A listé « . »"),
      expect.stringContaining("A lu « README.md »"),
      expect.stringContaining("A cherché « npm run »"),
    ]);
    // Chaque extrait garde son propre repli, fermé : ouvrir les trois d'un coup
    // enfouirait la réponse sous son propre appareil de preuve.
    for (const ligne of lectures) {
      expect(ligne.querySelector("details")).not.toHaveAttribute("open");
    }
  });

  it("montre l'extrait de la lecture qu'on ouvre, et lui seul", async () => {
    const utilisateur = userEvent.setup();
    render(<EtapesDuFil etapes={LECTURES} />);

    await utilisateur.click(screen.getByText("A consulté 3 éléments"));
    await utilisateur.click(screen.getByText("A lu « README.md »"));

    // Un seul extrait ouvert, et c'est celui qu'on a demandé. (`jsdom` laisse le
    // contenu d'un `<details>` fermé dans l'arbre — c'est le navigateur qui le
    // cache —, donc c'est l'attribut `open` qui se mesure ici, jamais l'absence
    // du texte : voir `banc-mise-en-page` pour ce qui se juge dans un vrai
    // navigateur.)
    const ouverts = [...document.querySelectorAll("li details[open]")];
    expect(ouverts).toHaveLength(1);
    expect(ouverts[0]).toHaveTextContent("Animation du logo");
  });

  it("ne promet pas un dépli à une lecture qui n'a rien rendu", async () => {
    const utilisateur = userEvent.setup();
    render(<EtapesDuFil etapes={[{ libelle: "A lu « vide.md »", detail: "" }]} />);

    await utilisateur.click(screen.getByText("A consulté 1 élément"));

    const ligne = within(repliDeTete()).getByRole("listitem");
    expect(ligne).toHaveTextContent("A lu « vide.md »");
    // Un chevron qui n'ouvre rien promet ce qu'il n'a pas.
    expect(ligne.querySelector("details")).toBeNull();
  });

  it("s'ouvre d'office et parle au présent pendant qu'il répond", () => {
    render(<EtapesDuFil etapes={LECTURES} enCours />);

    expect(screen.getByText("Consulte 3 éléments…")).toBeInTheDocument();
    // Tant qu'aucun mot n'est écrit, ce qu'il lit **est** ce qu'il y a à voir.
    expect(repliDeTete()).toHaveAttribute("open");
  });
});
