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

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { Shell } from "@/components/Shell";
import { MENTION_COUT_PARTIEL, MENTION_COUT_PARTIEL_COURTE } from "@/lib/format";
import { marquerGuideVu } from "@/lib/guide";
import { MENU } from "@/lib/navigation";
import {
  CLE_CONVERSATION_OUVERTE,
  REQUETE_COLONNE_AU_LARGE,
  ecrireConversationOuverte,
  ecrireRepliSidebar,
  lireConversationOuverte,
  lireRepliSidebar,
} from "@/lib/preferences";

import {
  coutExecutionFactice,
  poserChemin,
  poserEtatGlobal,
  poserLargeurFenetre,
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

  it("dit un coût cumulé partiel quand un grand livre porte des tokens sans prix (#1280)", async () => {
    // Sans quoi « 0,21 $US » restait nu au-dessus d'un run qui se disait
    // partiel — la contradiction relevée par le regard neuf sur la vraie stack.
    poserEtatGlobal({
      couts: [
        coutExecutionFactice({
          total: usageFactice({ cout_usd: 0.2051, tokens_total: 153021, tokens_non_tarifes: 130079 }),
        }),
      ],
    });
    await monterShell();
    const cumul = screen.getByText(/Coût cumulé/, {
      selector: "[data-guide='cout-cumule']",
    });
    // Forme courte — le libellé dit déjà « Coût » — et hors du gras du montant :
    // la relecture visuelle l'a vu peser autant que le chiffre, et tronquer le
    // titre de la page à côté de la pastille « Reconnexion… ».
    expect(cumul).toHaveTextContent(`0,21 $US · ${MENTION_COUT_PARTIEL_COURTE}`);
    expect(cumul).not.toHaveTextContent(MENTION_COUT_PARTIEL);
    expect(within(cumul).getByText(/0,21/)).not.toHaveTextContent(MENTION_COUT_PARTIEL_COURTE);
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

  it("réserve la bande du bouton flottant en fin de flux, après le contenu", async () => {
    // Sans cette réserve, une action de la page — décider une validation —
    // pourrait finir masquée par l'assistant (#123). Elle est le **dernier
    // élément du flux** de `main` (#888, `after:h-24`) et non un padding :
    // `main` est une boîte à hauteur fixée (#248) que le contenu dépasse dès
    // qu'une page est plus haute que la fenêtre, et un `pb-24` enfermé dedans
    // n'était plus nulle part au bas du défilement — la fin de page affleurait
    // le bord. Le porter sur l'ascenseur (la piste du ticket) a été mesuré
    // faux aussi : Chrome n'ajoute le padding de fin d'un conteneur défilant
    // qu'à ses boîtes en flux directes, jamais au débordement de leurs
    // descendants. Un élément du flux, lui, suit le contenu où qu'il aille.
    // jsdom ne mesure rien (#308) et le pixel appartient au banc ; ce qui est
    // gardé ici est la **forme** de la réserve — un item de flux de 96 px qui
    // ne rétrécit pas — et l'absence de tout padding bas, sur `main` comme
    // sur l'ascenseur, qui ferait croire à une réserve là où il n'y en a pas.
    const { container } = await monterShell();
    const main = container.querySelector("main")!;
    expect(Array.from(main.classList)).toEqual(
      expect.arrayContaining(["after:block", "after:h-24", "after:shrink-0"]),
    );
    const ascenseur = main.closest(".overflow-y-auto");
    expect(ascenseur, "main n'a aucun ascenseur au-dessus de lui").not.toBeNull();
    const paddingsBas = (element: Element) =>
      Array.from(element.classList).filter((c) => /^(sm:)?pb-/.test(c));
    expect(paddingsBas(main)).toEqual([]);
    expect(paddingsBas(ascenseur!)).toEqual([]);
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

  // --- La troisième zone (#925, lot 4 de #921) ------------------------------
  //
  // Ce qui se vérifie ici est ce que jsdom peut voir : la **présence**, l'état
  // par défaut, le chemin de la bascule et la place de la zone dans l'arbre. Ce
  // qu'il ne peut pas — la colonne qui recouvre au lieu de pousser sous `lg`,
  // les 420 px sans débordement — n'est pas oublié : il ne se mesure pas ici
  // (#308), c'est le banc de mise en page qui le tranche.

  // --- Le défaut arbitré (#1107) -------------------------------------------
  //
  // Ce que ces quatre sondes tiennent est une **décision**, pas une commodité :
  // la colonne est ouverte au premier passage parce qu'un projet est déjà
  // choisi, et seulement au large parce qu'en dessous de `lg` elle recouvre le
  // travail. Les quatre états que le ticket nomme y sont : premier passage,
  // largeur sous `lg`, repli mémorisé, stockage bloqué. Le cinquième — `/chat`,
  // où la colonne se retire — est plus bas, inchangé depuis #926.

  it("ouvre la colonne au premier passage, sur une fenêtre large", async () => {
    // Sur un poste neuf, la conversation est **là** : c'est le critère C2 du
    // jalon, et ce que le défaut de #925 ne tenait plus depuis que #926 a rempli
    // la colonne.
    await monterShell();
    const colonne = await screen.findByRole("complementary", {
      name: "Conversation",
    });
    expect(colonne).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Replier la conversation" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("la laisse fermée au premier passage quand la fenêtre est étroite", async () => {
    // L'autre moitié de la décision, et elle est indissociable : sous `lg` la
    // colonne **recouvre** (#925), donc l'ouvrir d'office poserait 320 px de
    // conversation sur ce qu'on était venu regarder. Mesuré à 420 px sur les
    // variantes de #1107.
    poserLargeurFenetre(420);
    await monterShell();
    const bascule = await screen.findByRole("button", {
      name: "Déplier la conversation",
    });
    expect(bascule).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("complementary", { name: "Conversation" }),
    ).not.toBeInTheDocument();
  });

  it("respecte un repli choisi, même sur une fenêtre large", async () => {
    // Un défaut n'est pas un choix : `"0"` est ce qu'écrit quelqu'un qui vient
    // de replier la colonne, et le confondre avec une clé absente la rouvrirait
    // à chaque visite. C'est la distinction que #1107 introduit dans
    // `preferenceBooleenne`.
    ecrireConversationOuverte(false);
    await monterShell();
    const bascule = await screen.findByRole("button", {
      name: "Déplier la conversation",
    });
    expect(bascule).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByRole("complementary", { name: "Conversation" }),
    ).not.toBeInTheDocument();
  });

  it("applique le défaut quand le stockage refuse de répondre", async () => {
    // Navigation privée, cookies bloqués : l'état est celui d'un premier
    // passage, il ne sera simplement pas mémorisé. Traiter le refus comme un
    // « non » rendrait un autre produit dans la fenêtre privée que dans celle
    // d'à côté.
    // Seule **cette** clé se voit refuser : le reste du shell (le projet actif,
    // le thème, la visite guidée) continue de lire le sien, sans quoi on
    // n'observerait ici que la porte d'entrée.
    const vrai = Storage.prototype.getItem;
    const bloque = vi
      .spyOn(Storage.prototype, "getItem")
      .mockImplementation(function (this: Storage, cle: string) {
        if (cle === CLE_CONVERSATION_OUVERTE) {
          throw new DOMException("stockage refusé", "SecurityError");
        }
        return vrai.call(this, cle);
      });
    try {
      await monterShell();
      expect(
        await screen.findByRole("complementary", { name: "Conversation" }),
      ).toBeInTheDocument();
    } finally {
      bloque.mockRestore();
    }
  });

  it("nomme la colonne dans la barre tant qu'elle est repliée", async () => {
    // Le parti pris pris à Grafana : un défaut fermé — la fenêtre étroite, ou
    // qui vient de replier — ne se rattrape pas par un onboarding mais par un
    // bouton nommé, à la même place partout. Ouverte, la colonne se titre
    // elle-même : le libellé ne dirait qu'une seconde fois ce que l'écran
    // montre.
    ecrireConversationOuverte(false);
    await monterShell();
    const replie = await screen.findByRole("button", {
      name: "Déplier la conversation",
    });
    expect(replie).toHaveTextContent("Conversation");

    ecrireConversationOuverte(true);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Replier la conversation" }),
      ).toHaveTextContent(""),
    );
  });

  it("fait passer l'ouverture de la conversation par le stockage", async () => {
    // Même contrat que le repli de la sidebar : le stockage tranche, l'abonnement
    // met l'état à jour. C'est ce qui permettra à un autre contrôle — un onglet
    // voisin, une préférence — de commander la même colonne sans connaître le
    // shell. Joué depuis l'état replié, pour que le clic observé soit bien une
    // **ouverture** : au large, le défaut de #1107 ouvre la colonne de lui-même.
    const utilisateur = userEvent.setup();
    ecrireConversationOuverte(false);
    await monterShell();

    await utilisateur.click(
      await screen.findByRole("button", { name: "Déplier la conversation" }),
    );
    expect(lireConversationOuverte()).toBe(true);
    await waitFor(() =>
      expect(
        screen.getByRole("complementary", { name: "Conversation" }),
      ).toBeInTheDocument(),
    );
    expect(
      screen.getByRole("button", { name: "Replier la conversation" }),
    ).toHaveAttribute("aria-expanded", "true");
  });

  it("résout le défaut sur le seuil même du recouvrement", async () => {
    // La frontière est dite deux fois — en CSS (`max-lg:` / `lg:` de la
    // colonne) et en JavaScript (`REQUETE_COLONNE_AU_LARGE`) — parce qu'aucun
    // des deux ne sait lire l'autre. Désaccordées, le défaut ouvrirait une
    // colonne qui recouvre, ou fermerait une colonne qui avait la place.
    expect(REQUETE_COLONNE_AU_LARGE).toBe("(min-width: 64rem)");
    await monterShell();
    const colonne = await screen.findByRole("complementary", {
      name: "Conversation",
    });
    expect(colonne.className).toContain("max-lg:fixed");
    expect(colonne.className).toContain("lg:sticky");
  });

  it("restitue la colonne ouverte d'un rechargement à l'autre, dans la session", async () => {
    // Plus « d'une session à l'autre » depuis #1293 : le choix de la colonne vit
    // le temps de la session, et chaque démarrage retrouve le défaut
    // (`demarrage.test.tsx`).
    ecrireConversationOuverte(true);
    await monterShell();
    await waitFor(() =>
      expect(
        screen.getByRole("complementary", { name: "Conversation" }),
      ).toBeInTheDocument(),
    );
  });

  it("referme la colonne depuis la colonne elle-même", async () => {
    // La croix n'est pas un doublon du bouton de la barre : sous `lg` la colonne
    // recouvre la droite de l'écran, donc le bouton qui l'a ouverte. Sans elle,
    // une fenêtre étroite ouvrirait une colonne qu'on ne pourrait plus refermer.
    const utilisateur = userEvent.setup();
    ecrireConversationOuverte(true);
    await monterShell();
    await waitFor(() =>
      expect(
        screen.getByRole("complementary", { name: "Conversation" }),
      ).toBeInTheDocument(),
    );

    await utilisateur.click(
      screen.getByRole("button", { name: "Fermer la conversation" }),
    );
    expect(lireConversationOuverte()).toBe(false);
    await waitFor(() =>
      expect(
        screen.queryByRole("complementary", { name: "Conversation" }),
      ).not.toBeInTheDocument(),
    );
  });

  it("tient la colonne hors du contenu de l'écran, et la désigne sans se tromper", async () => {
    // Le point de vigilance du chantier (docs/35 §3.4) : une zone du **shell**
    // n'est pas un bloc de plus dans l'**écran**. Rendue dans `<main>`, elle
    // deviendrait la « sortie de secours » de la règle des trois places — la
    // seule place sans plafond, où un écran plein rangerait son quatrième bloc.
    // La frontière elle-même est comptée par `frontiere-shell-ecran.test.tsx`
    // (#929), écran par écran ; ce qui est gardé ici est le fait de base dont
    // ce comptage dépend : la colonne est hors de `<main>`.
    // Second contrôle : le bouton dit commander la colonne (`aria-controls`), et
    // l'identifiant doit désigner un élément **qui existe** — la colonne reste
    // donc dans le DOM une fois fermée, simplement masquée.
    ecrireConversationOuverte(true);
    const { container } = await monterShell();
    const colonne = await screen.findByRole("complementary", {
      name: "Conversation",
    });
    const main = container.querySelector("main")!;
    expect(main.contains(colonne)).toBe(false);

    const bascule = screen.getByRole("button", {
      name: "Replier la conversation",
    });
    const cible = bascule.getAttribute("aria-controls");
    expect(cible).not.toBeNull();
    expect(document.getElementById(cible!)).toBe(colonne);
  });
});
