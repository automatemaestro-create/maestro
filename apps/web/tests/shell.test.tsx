/**
 * Le shell assemblé (#117 et les lots qui s'y sont greffés) — le seul test qui
 * monte la composition entière plutôt qu'un composant isolé.
 *
 * Il vérifie ce qu'aucun test unitaire ne peut voir : que les lots 2 à 7 sont
 * bien **branchés** dans le cadre commun. Chacun d'eux a livré un composant
 * autonome ; rien, ni le lint ni le build, ne remarquerait qu'un `slot` de la
 * barre supérieure a cessé d'être rempli — la cloche ou la bascule de thème
 * disparaîtraient simplement de toutes les pages.
 *
 * La visite guidée est marquée « déjà vue » : sans cela elle s'ouvrirait d'elle-
 * même par-dessus le shell, ce qui est son comportement normal (couvert par
 * `guide.test.tsx`) mais masquerait ce qu'on observe ici.
 *
 * Un projet actif est posé de même : depuis #279 le cadre commun vit **sous** la
 * garde du projet, et sans elle on n'observerait ici que la porte d'entrée (ce
 * que couvre `projet-actif.test.tsx`). C'est aussi pourquoi le montage est
 * devenu asynchrone — la garde ne tranche qu'après la lecture des projets.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { Shell } from "@/components/Shell";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";
import { ecrireRepliSidebar, lireRepliSidebar } from "@/lib/preferences";

import {
  coutExecutionFactice,
  poserChemin,
  poserEtatGlobal,
  poserProjetActif,
  usageFactice,
  validationFactice,
} from "./aides";

const monterShell = async () => {
  const rendu = render(
    <Shell>
      <p>contenu de la page</p>
    </Shell>,
  );
  await screen.findByText("contenu de la page");
  return rendu;
};

describe("le shell applicatif (Shell)", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
  });

  it("encadre le contenu de la page sans le remplacer", async () => {
    await monterShell();
    expect(screen.getByText("contenu de la page")).toBeInTheDocument();
    expect(
      screen.getByRole("navigation", { name: "Navigation principale" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("banner")).toBeInTheDocument();
  });

  it("branche les sept lots dans le cadre commun", async () => {
    poserChemin("/couts");
    poserEtatGlobal({ validations: [validationFactice()] });
    await monterShell();

    // #117 : la navigation et le titre de page.
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Coûts & analytics",
    );
    // #119 : la cloche, ici avec son compte de validations en attente.
    expect(
      screen.getByRole("button", { name: "Notifications — 1 validation en attente" }),
    ).toBeInTheDocument();
    // #118 : la bascule de thème — et non l'emplacement réservé.
    expect(
      screen.getByRole("button", { name: "Thème de l'interface" }),
    ).toBeInTheDocument();
    // #122 : le menu d'aide.
    expect(screen.getByRole("button", { name: "Aide" })).toBeInTheDocument();
    // #123 : l'assistant flottant.
    expect(
      screen.getByRole("button", { name: "Ouvrir l'assistant" }),
    ).toBeInTheDocument();
    // #120 : le monogramme du lien de marque, à la place de l'emoji 🎼.
    // Par le nom accessible : le `title` du lien de marque est devenu un
    // `aria-label` avec #536 — repliée, la sidebar n'a que lui pour le nommer.
    const marque = screen.getByRole("link", { name: "Maestro — Control Tower" });
    expect(marque.querySelector("svg")).not.toBeNull();
  });

  it("n'ouvre le flux temps réel qu'une fois pour tout le shell", async () => {
    // La barre supérieure lit le statut de connexion et le coût cumulé dans le
    // contexte, pas dans son propre hook : c'est ce qui évite d'ouvrir une
    // WebSocket par composant.
    //
    // ⚠ Le témoin a changé avec #691 : la pastille ne dit plus rien quand tout
    // va bien, donc « Temps réel connecté » ne prouve plus rien. C'est la
    // **coupure** qui sert de preuve — elle n'apparaît que si la barre a bien lu
    // le `connecte` du contexte —, et le coût cumulé confirme la seconde moitié.
    poserEtatGlobal({
      connecte: false,
      couts: [coutExecutionFactice({ total: usageFactice({ cout_usd: 1.25 }) })],
    });
    await monterShell();
    expect(screen.getByText("Reconnexion…")).toBeInTheDocument();
    expect(
      screen.getByText(/Coût cumulé/, {
        selector: "[data-guide='cout-cumule']",
      }),
    ).toHaveTextContent("1,25");
  });

  it("restitue la sidebar repliée d'une session à l'autre", async () => {
    ecrireRepliSidebar(true);
    await monterShell();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Déplier la navigation" }),
      ).toBeInTheDocument(),
    );
  });

  it("fait passer le repli par le stockage, pas par un état local", async () => {
    // Un seul chemin de bascule : c'est ce qui permet à la section Apparence
    // des Paramètres (#121) de commander la même sidebar sans connaître le shell.
    const utilisateur = userEvent.setup();
    await monterShell();

    await utilisateur.click(
      screen.getByRole("button", { name: "Replier la navigation" }),
    );
    expect(lireRepliSidebar()).toBe(true);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Déplier la navigation" }),
      ).toBeInTheDocument(),
    );
  });

  it("suit un repli commandé depuis ailleurs dans la page", async () => {
    await monterShell();
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Replier la navigation" }),
      ).toBeInTheDocument(),
    );

    ecrireRepliSidebar(true);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Déplier la navigation" }),
      ).toBeInTheDocument(),
    );
  });

  it("garde toutes les sections joignables depuis n'importe quelle page", async () => {
    poserChemin("/parametres");
    await monterShell();
    const navigation = screen.getByRole("navigation", {
      name: "Navigation principale",
    });
    expect(navigation.querySelectorAll("a")).toHaveLength(MENU.length);
  });

  it("réserve la bande du bouton flottant sous le contenu", async () => {
    // Sans cette réserve (`pb-24`), une action de la page — décider une
    // validation — pourrait finir masquée par l'assistant (#123).
    const { container } = await monterShell();
    expect(container.querySelector("main")).toHaveClass("pb-24");
  });

  it("pose les ancres que la visite guidée éclaire", async () => {
    const { container } = await monterShell();
    for (const ancre of ["marque", "navigation", "notifications", "aide", "contenu"]) {
      expect(
        container.querySelector(`[data-guide="${ancre}"]`),
        `ancre « ${ancre} » absente du shell`,
      ).not.toBeNull();
    }
  });
});
