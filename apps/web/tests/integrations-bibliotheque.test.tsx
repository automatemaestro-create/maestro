/**
 * La bibliothèque MCP face au **gestionnaire de mots de passe du navigateur**
 * (#231).
 *
 * Elle a été une section des Paramètres de #133 à #270, qui l'a déplacée sur
 * l'écran « Intégrations » (`components/integrations/BibliothequeMcp`). Ce
 * fichier n'a fait que **suivre son sujet** : les scénarios de #231 sont les
 * siens, au mot près. C'est d'ailleurs tout l'intérêt de les avoir gardés
 * intacts — un déménagement qui aurait « rangé » la structure au passage
 * rejouerait le bug, et ce sont eux qui le disent. Ceux de la bibliothèque
 * élargie (#271) les ont rejoints ici sans les toucher.
 *
 * Le bug corrigé ici n'était pas une faute de rendu mais une conjonction : le
 * clic sur « Configurer » montait un `<input type="password">` sans
 * propriétaire de formulaire, que Chrome appariait au seul champ texte du
 * document — la recherche de la bibliothèque — qu'il remplissait d'un
 * identifiant enregistré. La liste ne rendait alors plus rien, et le panneau
 * resté « ouvert » en mémoire ressuscitait dès qu'on vidait la recherche,
 * remontant le champ mot de passe et déclenchant un nouveau remplissage : un
 * champ qu'on ne pouvait plus effacer.
 *
 * Deux choses se testent donc, et elles sont indépendantes :
 *
 * 1. **la boucle** — comportementale, et la seule qui rendait le champ
 *    ineffaçable : un panneau dont l'entrée quitte les résultats est *oublié*,
 *    pas mis de côté. jsdom n'a pas de gestionnaire de mots de passe, mais il
 *    n'en a pas besoin : ce qu'on rejoue, c'est la conséquence (une valeur
 *    atterrit dans la recherche), pas la cause ;
 * 2. **le cloisonnement** — structurel : les champs secrets vivent dans un
 *    `<form>` et la recherche est **dehors**. C'est cette frontière qui met la
 *    recherche hors de portée des heuristiques du navigateur, et rien dans un
 *    DOM simulé ne peut l'observer autrement que par sa structure.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BibliothequeMcp } from "@/components/integrations/BibliothequeMcp";
import {
  MCP_MODE_APPAIRAGE,
  MCP_MODE_TOKEN,
  type EntreeRegistreMcp,
  type ProvenanceRegistreMcp,
} from "@/lib/types";

/** Une entrée du registre réduite à ce dont l'UI se sert. */
function entree(
  id: string,
  nom: string,
  options: Partial<EntreeRegistreMcp> = {},
): EntreeRegistreMcp {
  return {
    id,
    nom,
    description: `Intégration ${nom}.`,
    mode_auth: MCP_MODE_TOKEN,
    transport: "stdio",
    commande: "npx",
    args: [],
    url: "",
    env: {},
    headers: {},
    tags: [nom.toLowerCase()],
    secrets: [
      { cle: `${id.toUpperCase()}_TOKEN`, description: "Jeton", secret: true },
    ],
    procedure_url: "",
    optionnel: false,
    editeur: `Éditeur ${nom}`,
    popularite: 50,
    curee: true,
    source: "curee",
    version: "",
    depot: "",
    statut: "",
    ...options,
  };
}

const REGISTRE = [
  entree("gitlab", "GitLab"),
  entree("slack", "Slack", {
    mode_auth: MCP_MODE_APPAIRAGE,
    secrets: [
      { cle: "SLACK_CANAL", description: "Canal", secret: false },
      { cle: "SLACK_TOKEN", description: "Jeton", secret: true },
    ],
  }),
];

const chargerRegistreMcp = vi.fn();
const chargerPoolMcp = vi.fn();
const ajouterIntegrationPoolMcp = vi.fn();
const chargerProvenanceRegistreMcp = vi.fn();

vi.mock("@/lib/api", () => ({
  chargerRegistreMcp: (q?: string) => chargerRegistreMcp(q),
  chargerPoolMcp: () => chargerPoolMcp(),
  chargerProvenanceRegistreMcp: () => chargerProvenanceRegistreMcp(),
  ajouterIntegrationPoolMcp: (charge: unknown) =>
    ajouterIntegrationPoolMcp(charge),
  supprimerIntegrationPoolMcp: vi.fn(() => Promise.resolve()),
}));

/** La provenance servie par l'API sœur (#271, #677) — les deux sources, et quand. */
const PROVENANCE: ProvenanceRegistreMcp = {
  resume: "Sélection curée à la main.",
  sources: [{ libelle: "Dépôt MCP", url: "https://example.invalid/mcp" }],
  revue_le: "2026-08-28",
  tags: ["forge", "design", "recherche"],
  total: REGISTRE.length,
  total_curees: REGISTRE.length,
  total_decouvertes: 0,
  provenances: [
    {
      source: "curee",
      resume: "Sélection curée à la main.",
      sources: [{ libelle: "Dépôt MCP", url: "https://example.invalid/mcp" }],
      revue_le: "2026-08-28",
      total: REGISTRE.length,
    },
    {
      source: "decouverte",
      amont: "https://registry.modelcontextprotocol.io",
      rafraichi_le: "",
      moissonne_le: "",
      nombre: 0,
      retenues: 0,
      moissonnee: false,
      cause: "",
      echoue_le: "",
      total: 0,
    },
  ],
};

/** Le registre curé, filtré comme le fait l'API (`?q=`). */
function registreFiltre(q?: string) {
  const terme = (q ?? "").trim().toLowerCase();
  if (terme === "") return Promise.resolve(REGISTRE);
  return Promise.resolve(
    REGISTRE.filter(
      (e) =>
        e.nom.toLowerCase().includes(terme) ||
        e.tags.some((tag) => tag.includes(terme)),
    ),
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  chargerRegistreMcp.mockImplementation(registreFiltre);
  chargerPoolMcp.mockResolvedValue({ integrations: [], erreur: null });
  chargerProvenanceRegistreMcp.mockResolvedValue(PROVENANCE);
  ajouterIntegrationPoolMcp.mockResolvedValue(undefined);
});

/**
 * La bibliothèque rendue, une fois le premier chargement passé. Montée **seule**
 * et non par son écran : ce qui se teste ici est son cloisonnement interne, et
 * le pool projet — l'autre bloc de `/integrations` — n'y prend aucune part.
 */
async function bibliotheque() {
  render(<BibliothequeMcp idsPool={new Set()} onAjout={() => {}} />);
  return await screen.findByRole("region", {
    name: "Bibliothèque de serveurs MCP",
  });
}

function champRecherche() {
  return screen.getByRole("searchbox", {
    name: /Rechercher une intégration/,
  });
}

/** Déplie le formulaire d'une entrée nommée (chaque ligne a son « Configurer »). */
async function ouvrirConfiguration(
  utilisateur: ReturnType<typeof userEvent.setup>,
  nom: string,
) {
  const ligne = (await screen.findByText(nom)).closest("li") as HTMLElement;
  await utilisateur.click(
    within(ligne).getByRole("button", { name: "Configurer" }),
  );
}

describe("la bibliothèque MCP quand une valeur atterrit dans la recherche (#231)", () => {
  it("oublie le panneau ouvert dont l'entrée quitte les résultats", async () => {
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");
    // Le panneau est bien là : son champ secret est monté.
    expect(await screen.findByLabelText(/GITLAB_TOKEN/)).toBeInTheDocument();

    // Ce que faisait le gestionnaire de mots de passe : une valeur qui ne
    // correspond à rien atterrit dans la recherche.
    await utilisateur.type(champRecherche(), "yvan@joya.fr");
    expect(
      await screen.findByText(/Aucune intégration ne correspond/),
    ).toBeInTheDocument();

    // Et le point du ticket : vider la recherche rend la liste **sans**
    // ressusciter le panneau. C'est cette résurrection qui remontait le champ
    // mot de passe et relançait le remplissage, en boucle.
    await utilisateur.clear(champRecherche());
    expect(await screen.findByText("GitLab")).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByLabelText(/GITLAB_TOKEN/)).not.toBeInTheDocument(),
    );
    expect(champRecherche()).toHaveValue("");
  });

  it("garde le panneau ouvert tant que son entrée reste dans les résultats", async () => {
    // L'oubli ne doit pas dégénérer en « toute frappe referme » : affiner une
    // recherche qui garde l'entrée à l'écran n'a aucune raison de fermer ce
    // qu'on est en train de configurer.
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");
    expect(await screen.findByLabelText(/GITLAB_TOKEN/)).toBeInTheDocument();

    await utilisateur.type(champRecherche(), "git");
    await waitFor(() =>
      expect(screen.queryByText("Slack")).not.toBeInTheDocument(),
    );
    expect(screen.getByLabelText(/GITLAB_TOKEN/)).toBeInTheDocument();
  });
});

describe("le cloisonnement des champs secrets (#231)", () => {
  it("enferme les champs de configuration dans un formulaire, la recherche dehors", async () => {
    const utilisateur = userEvent.setup();
    const section = await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");

    const champ = await screen.findByLabelText(/GITLAB_TOKEN/);
    const formulaire = champ.closest("form");
    // Sans propriétaire de formulaire, le navigateur apparie un champ mot de
    // passe aux champs texte du document — c'est toute l'origine du bug.
    expect(formulaire).not.toBeNull();
    expect(section).toContainElement(formulaire);
    // La frontière : la recherche ne doit surtout pas être dans ce formulaire.
    expect(formulaire).not.toContainElement(champRecherche());
  });

  it("marque le champ secret « new-password », seule valeur qu'un navigateur honore", async () => {
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");

    const champ = await screen.findByLabelText(/GITLAB_TOKEN/);
    expect(champ).toHaveAttribute("type", "password");
    // `off` est délibérément ignoré par Chrome sur un champ de mot de passe :
    // le poser reviendrait à croire le problème réglé.
    expect(champ).toHaveAttribute("autocomplete", "new-password");
  });

  it("nomme le champ de recherche et l'exclut du remplissage automatique", async () => {
    await bibliotheque();
    // Un champ anonyme est exactement ce qu'un gestionnaire prend pour un
    // identifiant.
    expect(champRecherche()).toHaveAttribute("name");
    expect(champRecherche()).toHaveAttribute("autocomplete", "off");
  });

  it("laisse un identifiant non sensible en clair, hors remplissage lui aussi", async () => {
    // Mode appairage : le canal n'est pas un secret (pas de masquage), mais il
    // n'a pas davantage à être rempli par le navigateur.
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "Slack");

    const canal = await screen.findByLabelText(/SLACK_CANAL/);
    expect(canal).toHaveAttribute("type", "text");
    expect(canal).toHaveAttribute("autocomplete", "off");
    expect(await screen.findByLabelText(/SLACK_TOKEN/)).toHaveAttribute(
      "type",
      "password",
    );
  });
});

describe("le formulaire de configuration comme formulaire", () => {
  it("ajoute au pool par la soumission, sans laisser partir la requête native", async () => {
    // Devenir un `<form>` a un prix : le bouton est passé en `type="submit"`,
    // et une soumission native rechargerait la page en perdant la saisie. Les
    // deux moitiés se tiennent — que l'ajout marche encore *et* que rien ne
    // parte au navigateur — d'où un seul test qui les vérifie ensemble.
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");

    const champ = await screen.findByLabelText(/GITLAB_TOKEN/);
    const formulaire = champ.closest("form") as HTMLFormElement;
    const soumissions: SubmitEvent[] = [];
    formulaire.addEventListener("submit", (e) =>
      soumissions.push(e as SubmitEvent),
    );

    await utilisateur.type(champ, "glpat-xxx");
    await utilisateur.click(
      within(formulaire).getByRole("button", { name: "Ajouter au pool" }),
    );

    // La soumission a bien eu lieu (le bouton n'est plus un simple `onClick`)…
    expect(soumissions).toHaveLength(1);
    // …elle a été annulée (pas de navigation)…
    expect(soumissions[0].defaultPrevented).toBe(true);
    // …et c'est l'appel API qui porte l'ajout.
    await waitFor(() =>
      expect(ajouterIntegrationPoolMcp).toHaveBeenCalledWith({
        registre_id: "gitlab",
        secrets: [{ cle: "GITLAB_TOKEN", valeur: "glpat-xxx" }],
      }),
    );
  });

  it("ne soumet rien tant que le secret requis n'est pas saisi", async () => {
    const utilisateur = userEvent.setup();
    await bibliotheque();
    await ouvrirConfiguration(utilisateur, "GitLab");

    const formulaire = (await screen.findByLabelText(/GITLAB_TOKEN/)).closest(
      "form",
    ) as HTMLFormElement;
    await utilisateur.click(
      within(formulaire).getByRole("button", { name: "Ajouter au pool" }),
    );
    expect(ajouterIntegrationPoolMcp).not.toHaveBeenCalled();
  });
});

describe("la bibliothèque élargie (#271)", () => {
  it("dit d'où vient la liste et quand elle a été revue", async () => {
    const region = await bibliotheque();

    // La date est une date **pure** : rendue telle quelle, sans passer par
    // `Date` — qui la lirait en UTC et la ferait reculer d'un jour à l'ouest.
    expect(await within(region).findByText(/Revue le/)).toHaveTextContent(
      "28/08/2026",
    );
    expect(
      within(region).getByRole("link", { name: "Dépôt MCP" }),
    ).toHaveAttribute("href", "https://example.invalid/mcp");
  });

  it("montre l'éditeur de chaque intégration", async () => {
    const region = await bibliotheque();
    expect(
      await within(region).findByText(/Éditeur GitLab/),
    ).toBeInTheDocument();
  });

  it("rend une piste plutôt qu'un cul-de-sac quand rien ne correspond", async () => {
    const utilisateur = userEvent.setup();
    const region = await bibliotheque();
    // On attend la première réponse : avant elle, « aucun résultat » serait
    // l'état d'avant la question.
    await within(region).findByText(/Éditeur GitLab/);
    await utilisateur.type(champRecherche(), "kubernetes");

    await within(region).findByText(/Aucune intégration ne correspond/);
    // La sortie du cul-de-sac : des pistes cliquables, et le retour à la liste
    // entière. Un clic sur une piste relance la recherche sur ce tag.
    await utilisateur.click(within(region).getByRole("button", { name: "forge" }));
    await waitFor(() => expect(chargerRegistreMcp).toHaveBeenCalledWith("forge"));
    expect(
      within(region).getByRole("button", { name: /Voir toute la bibliothèque/ }),
    ).toBeInTheDocument();
  });
});
