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
    publie_le: "",
    admission: null,
    signaux: [],
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
  // Une entrée **découverte** (#679) : elle vient du miroir du registre
  // officiel, personne ne l'a admise, elle n'est donc pas montable.
  entree("io-github-alice-veille", "veille", {
    curee: false,
    source: "decouverte",
    editeur: "io.github.alice",
    // Vide comme le rend la traduction : l'amont ne déclare pas de tags, et les
    // fabriquer serait fabriquer de la métadonnée (`mcp_traduction._entree`).
    tags: [],
    version: "1.4.0",
    depot: "https://github.com/alice/veille",
    statut: "deprecated",
    publie_le: "2026-07-14T08:30:00Z",
    secrets: [{ cle: "MCP_VEILLE_TOKEN", description: "Jeton", secret: true }],
  }),
];

const chargerRegistreMcp = vi.fn();
const chargerPoolMcp = vi.fn();
const ajouterIntegrationPoolMcp = vi.fn();
const chargerProvenanceRegistreMcp = vi.fn();
const admettreEntreeMcp = vi.fn();

vi.mock("@/lib/api", () => ({
  chargerRegistreMcp: (q?: string) => chargerRegistreMcp(q),
  chargerPoolMcp: () => chargerPoolMcp(),
  chargerProvenanceRegistreMcp: () => chargerProvenanceRegistreMcp(),
  ajouterIntegrationPoolMcp: (charge: unknown) =>
    ajouterIntegrationPoolMcp(charge),
  // Le mock de ce fichier est **total** (pas de `importOriginal`) : un export
  // que la bibliothèque importe et qui manquerait ici ferait échouer l'accès,
  // pas seulement le clic. La porte d'admission (#678) est arrivée avec #679.
  admettreEntreeMcp: (charge: unknown) => admettreEntreeMcp(charge),
  supprimerIntegrationPoolMcp: vi.fn(() => Promise.resolve()),
}));

/** La provenance servie par l'API sœur (#271, #677, #678) — les trois sources, et quand. */
const PROVENANCE: ProvenanceRegistreMcp = {
  resume: "Sélection curée à la main.",
  sources: [{ libelle: "Dépôt MCP", url: "https://example.invalid/mcp" }],
  revue_le: "2026-08-28",
  tags: ["forge", "design", "recherche"],
  total: REGISTRE.length,
  total_curees: 2,
  total_admises: 0,
  total_decouvertes: 1,
  provenances: [
    {
      source: "curee",
      resume: "Sélection curée à la main.",
      sources: [{ libelle: "Dépôt MCP", url: "https://example.invalid/mcp" }],
      revue_le: "2026-08-28",
      total: 2,
    },
    {
      source: "admise",
      resume: "Entrées admises par un geste humain tracé.",
      total: 0,
      revoquees: 0,
      derniere_le: "",
      signaux: 0,
    },
    {
      source: "decouverte",
      amont: "https://registry.modelcontextprotocol.io",
      rafraichi_le: "2026-08-28T06:00:00Z",
      moissonne_le: "2026-08-27T06:00:00Z",
      nombre: 3,
      retenues: 0,
      moissonnee: true,
      cause: "",
      echoue_le: "",
      total: 1,
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
  admettreEntreeMcp.mockResolvedValue(undefined);
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

describe("les deux sources, sans les confondre (#679)", () => {
  /**
   * ⚠ Les tests de ce lot sont **différés au lot 6 du parent #673** — sauf
   * celui-ci, et c'est la règle du dépôt qui le veut : un lot intermédiaire
   * porte ses tests quand sa logique est critique. Elle l'est ici, parce que ce
   * qui se vérifie est un **garde-fou** : `POST /api/mcp/pool` refuse une entrée
   * hors allowlist (docs/19), donc un écran qui lui proposerait le formulaire
   * ferait saisir un token pour un ajout dont il connaît d'avance le refus — et
   * ferait passer pour installable ce que la sécurité du produit interdit.
   *
   * Ce qui décide n'est pas la source mais le champ `curee` : une entrée
   * **admise** est `curee: true` tout en venant de l'amont. Les lire à l'envers
   * fermerait la porte à ce qu'un humain vient d'ouvrir.
   */
  async function ligneDe(nom: string) {
    return (await screen.findByText(nom)).closest("li") as HTMLElement;
  }

  it("n'offre pas la configuration d'une découverte, mais son examen", async () => {
    await bibliotheque();
    const ligne = await ligneDe("veille");

    expect(within(ligne).getByText("Découverte")).toBeInTheDocument();
    expect(
      within(ligne).getByRole("button", { name: "Examiner" }),
    ).toBeInTheDocument();
    expect(
      within(ligne).queryByRole("button", { name: "Configurer" }),
    ).not.toBeInTheDocument();
    // Le statut d'amont est signalé sur la ligne, sans déplier.
    expect(within(ligne).getByText("Dépréciée")).toBeInTheDocument();
  });

  it("rend les signaux de confiance et l'admission au lieu du formulaire", async () => {
    const utilisateur = userEvent.setup();
    await bibliotheque();
    const ligne = await ligneDe("veille");
    await utilisateur.click(within(ligne).getByRole("button", { name: "Examiner" }));

    // Les quatre signaux du critère 2 : éditeur/namespace, dépôt, version
    // épinglée, date de publication.
    expect(within(ligne).getByText("io.github.alice/veille")).toBeInTheDocument();
    expect(
      within(ligne).getByRole("link", { name: "https://github.com/alice/veille" }),
    ).toBeInTheDocument();
    expect(within(ligne).getByText("1.4.0")).toBeInTheDocument();
    expect(within(ligne).getByText("14/07/2026")).toBeInTheDocument();

    // Aucun champ de secret : l'ajout au pool serait refusé, le proposer serait
    // promettre ce que le garde-fou interdit.
    expect(
      within(ligne).queryByLabelText(/MCP_VEILLE_TOKEN/),
    ).not.toBeInTheDocument();
    expect(ligne.querySelector("form")).toBeNull();

    await utilisateur.click(
      within(ligne).getByRole("button", { name: /Admettre dans l'allowlist/ }),
    );
    await waitFor(() =>
      expect(admettreEntreeMcp).toHaveBeenCalledWith({
        registre_id: "io-github-alice-veille",
      }),
    );
    expect(ajouterIntegrationPoolMcp).not.toHaveBeenCalled();
  });

  it("dit les trois sources au pied, sans plus prétendre que rien n'est moissonné", async () => {
    const region = await bibliotheque();

    // La ligne curée n'a pas bougé de sens (#271) — mais elle ne parle plus que
    // d'elle-même, et le pied porte les deux autres sources à côté.
    expect(await within(region).findByText(/Revue le/)).toHaveTextContent(
      "28/08/2026",
    );
    expect(
      within(region).getByText(/3 entrées moissonnées, 1 servies ici/),
    ).toBeInTheDocument();
    expect(
      within(region).getByText(/aucune entrée découverte n'a encore été admise/),
    ).toBeInTheDocument();
    expect(within(region).queryByText(/jamais moissonn/)).not.toBeInTheDocument();
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

/* ------------------------------------------------------------------ *
 * Le lot 6 (#680) : ce que #679 avait laissé derrière lui
 * ------------------------------------------------------------------ */

/**
 * #679 a livré l'écran en gardant **un seul** test — celui du garde-fou, parce
 * qu'un écran qui proposerait le formulaire d'une découverte ferait saisir un
 * token pour un ajout dont il connaît d'avance le refus. Tout le reste était
 * différé ici, et « tout le reste » n'est pas de la décoration : c'est la
 * **troisième source** (les admises, montables tout en venant de l'amont), les
 * **signaux d'amont** qui disent ce qui a bougé depuis, et les branches de
 * provenance qu'un poste réel rencontre — miroir en panne, miroir jamais
 * moissonné, aucun amont branché.
 */

/** Monte la bibliothèque sur un registre et une provenance choisis pour le cas. */
async function bibliothequeAvec(
  entrees: EntreeRegistreMcp[],
  provenance: ProvenanceRegistreMcp = PROVENANCE,
) {
  chargerRegistreMcp.mockImplementation(() => Promise.resolve(entrees));
  chargerProvenanceRegistreMcp.mockResolvedValue(provenance);
  return await bibliotheque();
}

/** La provenance des trois sources, dont on ne change que ce que le cas éprouve. */
function provenanceAvec(
  admiseModif: Partial<ProvenanceRegistreMcp["provenances"][1]> = {},
  decouverteModif: Partial<ProvenanceRegistreMcp["provenances"][2]> = {},
): ProvenanceRegistreMcp {
  const [source, admiseBase, decouverteBase] = PROVENANCE.provenances;
  return {
    ...PROVENANCE,
    provenances: [
      source,
      { ...admiseBase, ...admiseModif },
      { ...decouverteBase, ...decouverteModif },
    ],
  };
}

/** Une entrée **admise** : montable (`curee`), et pourtant venue de l'amont. */
function admise(options: Partial<EntreeRegistreMcp> = {}): EntreeRegistreMcp {
  return entree("io-github-alice-veille", "veille", {
    curee: true,
    source: "admise",
    editeur: "io.github.alice",
    tags: [],
    version: "1.4.0",
    depot: "https://github.com/alice/veille",
    statut: "active",
    publie_le: "2026-07-14T08:30:00Z",
    admission: {
      id: "io-github-alice-veille",
      nom_amont: "io.github.alice/veille",
      version: "1.4.0",
      editeur: "io.github.alice",
      depot: "https://github.com/alice/veille",
      amont: "https://registry.modelcontextprotocol.io",
      miroir_le: "2026-08-28T06:00:00Z",
      par: "alice",
      le: "2026-08-28T10:15:00Z",
      note: "revue faite",
      active: true,
      revoquee_par: "",
      revoquee_le: "",
      motif: "",
    },
    secrets: [{ cle: "MCP_VEILLE_TOKEN", description: "Jeton", secret: true }],
    ...options,
  });
}

async function ligneDeNom(nom: string) {
  return (await screen.findByText(nom)).closest("li") as HTMLElement;
}

describe("la troisième source : ce qu'un geste humain a admis (#680)", () => {
  it("montre l'entrée admise comme montable, sans la confondre avec une curée", async () => {
    await bibliothequeAvec([entree("gitlab", "GitLab"), admise()]);
    const ligne = await ligneDeNom("veille");

    // ⚠ Le couple qui fait tout le sens du lot : `curee: true` répond à
    // « montable ? », `source: "admise"` à « d'où ça vient ? ». Le badge dit la
    // seconde, le formulaire prouve la première.
    expect(within(ligne).getByText("Admise")).toBeInTheDocument();
    expect(within(ligne).queryByText("Découverte")).not.toBeInTheDocument();
    expect(
      within(ligne).getByRole("button", { name: "Configurer" }),
    ).toBeInTheDocument();
    expect(
      within(ligne).queryByRole("button", { name: "Examiner" }),
    ).not.toBeInTheDocument();
  });

  it("déplie le formulaire de configuration, et non le panneau d'examen", async () => {
    const utilisateur = userEvent.setup();
    await bibliothequeAvec([admise()]);
    const ligne = await ligneDeNom("veille");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Configurer" }),
    );

    expect(ligne.querySelector("form")).not.toBeNull();
    expect(within(ligne).getByLabelText(/MCP_VEILLE_TOKEN/)).toBeInTheDocument();
    expect(
      within(ligne).queryByRole("button", { name: /Admettre dans l'allowlist/ }),
    ).not.toBeInTheDocument();
  });

  it("porte la trace du geste : qui, quand, et pourquoi", async () => {
    const utilisateur = userEvent.setup();
    await bibliothequeAvec([admise()]);
    const ligne = await ligneDeNom("veille");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Configurer" }),
    );

    // Une allowlist locale sans trace serait une allowlist dont personne ne
    // répond : c'est tout ce que la porte d'admission ajoute au garde-fou.
    const mention = within(ligne).getByText(/Admise le/);
    expect(mention).toHaveTextContent("28/08/2026");
    expect(mention).toHaveTextContent("alice");
    expect(mention).toHaveTextContent("revue faite");
  });

  it("compte les admises au pied, avec la date du dernier geste", async () => {
    const region = await bibliothequeAvec(
      [admise()],
      provenanceAvec({
        total: 2,
        revoquees: 1,
        derniere_le: "2026-08-28T10:15:00Z",
        signaux: 3,
      }),
    );

    // `findByText` et non `getByText` : la provenance est une **seconde**
    // lecture réseau, qui se résout après le premier rendu de la région. Un
    // `get` synchrone y serait vert ou rouge selon l'ordonnancement des
    // promesses, c'est-à-dire un test qui ne dit rien.
    const pied = await within(region).findByText(/2 entrées admises/);
    expect(pied).toHaveTextContent("dernière le 28/08/2026");
    expect(pied).toHaveTextContent("1 révoquée (gardée au journal)");
  });

  it("dit la porte jamais franchie plutôt qu'un zéro", async () => {
    const region = await bibliothequeAvec([entree("gitlab", "GitLab")]);
    expect(
      await within(region).findByText(
        /la porte existe, personne ne l'a franchie/,
      ),
    ).toBeInTheDocument();
  });
});

describe("les signaux d'amont : rien ne disparaît en silence (#680)", () => {
  const SIGNAUX = [
    ["amont_depreciee", "est passée « deprecated » chez l'amont"],
    ["amont_supprimee", "a été supprimée chez l'amont"],
    ["amont_disparue", "n'est plus dans le miroir du registre officiel"],
    ["version_nouvelle", "l'amont publie « 2.0.0 »"],
  ] as const;

  it.each(SIGNAUX)(
    "rend le signal %s sans qu'on ait à déplier",
    async (genre, message) => {
      await bibliothequeAvec([
        admise({
          signaux: [
            {
              id: "io-github-alice-veille",
              genre,
              message,
              version_amont: "2.0.0",
              statut_amont: "deprecated",
            },
          ],
        }),
      ]);
      const ligne = await ligneDeNom("veille");

      // Sans déplier : un écart qu'il faut chercher est un écart qu'on ne voit
      // pas. C'est la moitié « jamais retirée en silence » du lot #678.
      const signaux = within(ligne).getByRole("list", {
        name: "Signaux d'amont",
      });
      expect(within(signaux).getByText(message)).toBeInTheDocument();
    },
  );

  it("laisse l'entrée signalée parfaitement montable", async () => {
    await bibliothequeAvec([
      admise({
        signaux: [
          {
            id: "io-github-alice-veille",
            genre: "amont_supprimee",
            message: "a été supprimée chez l'amont",
            version_amont: "",
            statut_amont: "deleted",
          },
        ],
      }),
    ]);
    const ligne = await ligneDeNom("veille");

    // La détection est automatique, jamais le verdict : retirer d'office
    // casserait un serveur monté sans le dire.
    expect(
      within(ligne).getByRole("button", { name: "Configurer" }),
    ).toBeInTheDocument();
  });

  it("n'affiche aucune liste de signaux quand l'amont n'a rien à dire", async () => {
    await bibliothequeAvec([admise()]);
    const ligne = await ligneDeNom("veille");
    expect(
      within(ligne).queryByRole("list", { name: "Signaux d'amont" }),
    ).not.toBeInTheDocument();
  });
});

describe("l'admission vue de l'écran (#680)", () => {
  it("relit la bibliothèque et sa provenance après le geste", async () => {
    const utilisateur = userEvent.setup();
    // Avant : une découverte. Après le geste : la même entrée, admise.
    chargerRegistreMcp.mockImplementation(() => Promise.resolve([REGISTRE[2]]));
    chargerProvenanceRegistreMcp.mockResolvedValue(PROVENANCE);
    await bibliotheque();
    const appelsAvant = chargerRegistreMcp.mock.calls.length;

    admettreEntreeMcp.mockImplementation(() => {
      chargerRegistreMcp.mockImplementation(() => Promise.resolve([admise()]));
      chargerProvenanceRegistreMcp.mockResolvedValue(
        provenanceAvec(
          { total: 1, derniere_le: "2026-08-28T10:15:00Z" },
          { total: 0 },
        ),
      );
      return Promise.resolve(undefined);
    });

    const ligne = await ligneDeNom("veille");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Examiner" }),
    );
    await utilisateur.click(
      within(ligne).getByRole("button", { name: /Admettre dans l'allowlist/ }),
    );

    // Le rechargement porte sur les **deux** lectures : sans la provenance, le
    // pied continuerait de dire que personne n'a franchi la porte.
    await waitFor(() =>
      expect(chargerRegistreMcp.mock.calls.length).toBeGreaterThan(appelsAvant),
    );
    await waitFor(() =>
      expect(chargerProvenanceRegistreMcp).toHaveBeenCalledTimes(2),
    );
    // Le panneau **reste ouvert** et change de contenu sous le même id : ce qui
    // était à examiner devient configurable, sans que la ligne ait changé de
    // place ni de nom, et sans que l'utilisateur ait à la rouvrir. C'est cela
    // que la porte d'admission promet — et « Admise » est cherché **dans la
    // ligne**, le pied de la bibliothèque portant le même mot pour sa source.
    await waitFor(async () => {
      const apres = await ligneDeNom("veille");
      expect(within(apres).getByText("Admise")).toBeInTheDocument();
      expect(apres.querySelector("form")).not.toBeNull();
      expect(
        within(apres).queryByRole("button", {
          name: /Admettre dans l'allowlist/,
        }),
      ).not.toBeInTheDocument();
    });
  });

  it("dit l'échec d'une admission au lieu de le passer sous silence", async () => {
    const utilisateur = userEvent.setup();
    admettreEntreeMcp.mockRejectedValue(
      new Error("409 — déjà curé dans le seed, rien à admettre."),
    );
    await bibliothequeAvec([REGISTRE[2]]);
    const ligne = await ligneDeNom("veille");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Examiner" }),
    );
    await utilisateur.click(
      within(ligne).getByRole("button", { name: /Admettre dans l'allowlist/ }),
    );

    const alerte = await within(ligne).findByRole("alert");
    expect(alerte).toHaveTextContent("déjà curé dans le seed");
    // Le bouton redevient actionnable : un refus n'est pas une impasse.
    expect(
      within(ligne).getByRole("button", { name: /Admettre dans l'allowlist/ }),
    ).toBeInTheDocument();
  });
});

describe("les signaux de confiance quand l'amont ne déclare rien (#680)", () => {
  it("nomme chaque signal absent au lieu d'escamoter sa ligne", async () => {
    const utilisateur = userEvent.setup();
    await bibliothequeAvec([
      entree("nu", "nu", {
        curee: false,
        source: "decouverte",
        editeur: "",
        tags: [],
        version: "",
        depot: "",
        statut: "",
        publie_le: "",
      }),
    ]);
    const ligne = await ligneDeNom("nu");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Examiner" }),
    );

    // ⚠ « L'absence est muette, l'inconnu est nommé » — et ici l'inconnu **est**
    // le sujet : un dépôt non déclaré est précisément ce qu'il faut savoir avant
    // d'admettre. Une ligne qui disparaîtrait se lirait comme une ligne qu'on
    // n'avait pas à montrer.
    expect(
      within(ligne).getByText("aucun namespace déclaré"),
    ).toBeInTheDocument();
    expect(within(ligne).getByText(/aucun dépôt déclaré/)).toBeInTheDocument();
    expect(
      within(ligne).getByText("aucune version déclarée"),
    ).toBeInTheDocument();
    expect(
      within(ligne).getByText("date de publication inconnue"),
    ).toBeInTheDocument();
    // Un statut vide vaut « active » : l'amont ne le déclare que s'il a bougé.
    expect(within(ligne).getByText("active")).toBeInTheDocument();
  });

  it.each([
    ["io.github.alice", "propriété prouvée par OAuth GitHub"],
    ["exemple.fr", "propriété prouvée par DNS ou HTTP sur le domaine"],
    ["Editeur Maison", "propriété vérifiée par le registre à la publication"],
  ])("dit comment le namespace %s a été prouvé", async (editeur, preuve) => {
    const utilisateur = userEvent.setup();
    await bibliothequeAvec([
      entree("sonde", "sonde", {
        curee: false,
        source: "decouverte",
        editeur,
        tags: [],
      }),
    ]);
    const ligne = await ligneDeNom("sonde");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Examiner" }),
    );

    // On ne devine rien au-delà de ce que le registre documente : un namespace
    // dont on ignore le mode de preuve se dit « vérifié à la publication ».
    //
    // La preuve est cherchée sur **la ligne du signal** (le `<dd>` qui suit son
    // `<dt>`) et non n'importe où dans le panneau : la phrase du critère 2 la
    // répète en prose juste en dessous, et se contenter d'un `getByText` global
    // passerait au vert sur un tableau de signaux qui aurait perdu sa ligne.
    const libelle = within(ligne).getByText("Éditeur (namespace)");
    expect(libelle.nextElementSibling).toHaveTextContent(preuve);
  });

  it("rend telle quelle une date que personne ne sait lire", async () => {
    const utilisateur = userEvent.setup();
    await bibliothequeAvec([
      entree("sonde", "sonde", {
        curee: false,
        source: "decouverte",
        editeur: "io.github.alice",
        tags: [],
        publie_le: "bientôt",
      }),
    ]);
    const ligne = await ligneDeNom("sonde");
    await utilisateur.click(
      within(ligne).getByRole("button", { name: "Examiner" }),
    );

    // Un signal de confiance illisible reste un signal ; le masquer en ferait
    // une absence, qui n'est pas la même information.
    expect(within(ligne).getByText("bientôt")).toBeInTheDocument();
  });
});

describe("la provenance de la découverte, dans ses états réels (#680)", () => {
  it("dit qu'aucun registre amont n'est branché", async () => {
    const region = await bibliothequeAvec(
      [entree("gitlab", "GitLab")],
      provenanceAvec({}, { amont: "", moissonnee: false, nombre: 0, total: 0 }),
    );
    expect(
      await within(region).findByText(
        /aucun registre amont n'est branché sur ce poste/,
      ),
    ).toBeInTheDocument();
  });

  it("dit la cause d'un rafraîchissement en échec, et sa date", async () => {
    const region = await bibliothequeAvec(
      [entree("gitlab", "GitLab")],
      provenanceAvec(
        {},
        {
          moissonnee: false,
          nombre: 0,
          total: 0,
          cause: "amont injoignable — registry.modelcontextprotocol.io muet",
          echoue_le: "2026-08-28T09:00:00Z",
        },
      ),
    );

    // Un écran ouvert trois heures après la panne doit pouvoir la dire, plutôt
    // que d'afficher une fraîcheur qu'il ne peut pas justifier.
    const pied = await within(region).findByText(
      /dernier rafraîchissement en échec/,
    );
    expect(pied).toHaveTextContent("28/08/2026");
    expect(pied).toHaveTextContent("amont injoignable");
  });

  it("distingue « pas encore moissonné » d'une panne", async () => {
    const region = await bibliothequeAvec(
      [entree("gitlab", "GitLab")],
      provenanceAvec(
        {},
        { moissonnee: false, nombre: 0, total: 0, rafraichi_le: "" },
      ),
    );

    // L'état normal d'un poste neuf, et surtout **pas** une erreur : le dire
    // comme une panne apprendrait à ne plus lire les pannes.
    expect(
      await within(region).findByText(/pas encore moissonné sur ce poste/),
    ).toBeInTheDocument();
    expect(within(region).queryByText(/en échec/)).not.toBeInTheDocument();
  });
});

describe("la recherche porte sur les trois sources (#680)", () => {
  it("trouve une découverte que personne n'a curée", async () => {
    const utilisateur = userEvent.setup();
    const region = await bibliotheque();
    await utilisateur.type(champRecherche(), "veille");

    // C'est la décision du parent #673 : la recherche de l'amont est une
    // sous-chaîne sur le seul nom, la nôtre porte nom, éditeur, tags et
    // description — on moissonne chez lui, on cherche chez nous.
    await waitFor(() =>
      expect(chargerRegistreMcp).toHaveBeenCalledWith("veille"),
    );
    expect(await within(region).findByText("veille")).toBeInTheDocument();
    expect(within(region).queryByText("GitLab")).not.toBeInTheDocument();
  });
});
