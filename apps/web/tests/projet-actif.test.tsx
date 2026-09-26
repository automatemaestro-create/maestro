/**
 * La porte d'entrée de la Control Tower (#279, lot 3 de #276).
 *
 * Ces tests montent le **shell entier**, et pas la porte seule : ce qui est en
 * jeu n'est pas le rendu d'un écran de plus, c'est qu'aucun autre ne s'atteigne
 * avant lui. Un `ChoixProjet` rendu isolément passerait tous ces tests sans rien
 * garantir — c'est la garde qui est le sujet.
 *
 * Ce qu'ils protègent en propre :
 *
 * 1. **la garde n'est pas une redirection** — l'URL demandée ne bouge pas, si
 *    bien que la page revient d'elle-même après le choix. Le jour où quelqu'un
 *    remplacerait la garde par un `router.push("/projets")`, le parcours aurait
 *    l'air identique et la page demandée serait pourtant perdue : les tests
 *    regardent donc aussi ce qui **n'a pas** été poussé ;
 * 2. **le choix retenu est confronté à l'état réel**, jamais recopié — un projet
 *    supprimé entre deux visites ramène au choix avec son motif ;
 * 3. **une API muette n'est pas une absence de projet** — même règle que la
 *    liste de #225 : on ne prétend pas que le backlog est vide quand on n'a rien
 *    pu lire, et on n'oublie pas le choix retenu pour autant ;
 * 4. **la porte défile** (#306) — être rendue *au-dessus* du cadre applicatif
 *    la prive du conteneur défilant du shell, et le `<body>` en `overflow-hidden`
 *    de #248 rognait alors le bas de ce qu'elle montre. jsdom ne calcule
 *    aucune mise en page : ce qui se teste est la **chaîne de classes** qui rend
 *    le défilement possible, comme pour le Kanban (`kanban.test.tsx`) ;
 * 5. **un projet naît dans la conversation** (#1294, docs/43 §2.2) — « Nouveau
 *    projet » ouvre le fil de l'orchestration **sans projet**, jamais un
 *    formulaire ; la proposition se tranche sur une carte ; et le projet que le
 *    fil fait naître est ouvert, la conversation continuant dans sa colonne.
 *    `useChat` est le double de `setup.ts` : le fil rendu est celui qu'on pose,
 *    et ce qu'on lui demande (canal, projet) s'observe.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { EcranOuverture } from "@/components/projets/ChoixProjet";
import { Shell } from "@/components/Shell";
import { ecrireConversationOuverte as ecrireConversationDuFil } from "@/lib/conversationOuverte";
import { marquerGuideVu } from "@/lib/guide";
import { demanderNaissance } from "@/lib/naissance";
import { lireConversationOuverte } from "@/lib/preferences";
import { ecrireProjetActifId, lireProjetActifId } from "@/lib/projetActif";
import type { DemandeProjet, Projet, ProjetCree } from "@/lib/types";

import {
  canalCourant,
  cheminCourant,
  messageFactice,
  navigations,
  poserChemin,
  poserFilAssistance,
  projetDuFilCourant,
  projetFactice,
} from "./aides";

const chargerProjets = vi.fn();
const creerProjet = vi.fn();

// `importOriginal`, et non un objet nu : `ErreurProjet` doit rester **la**
// classe du module (voir `projets.test.tsx`). Cette déclaration prend le pas sur
// celle de `setup.ts`, qui ne sert que les fichiers n'ayant rien à régler ici.
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...reel,
    chargerProjets: () => chargerProjets(),
    creerProjet: (declaration: unknown) => creerProjet(declaration),
  };
});

/** Ce que la page demandée rend — son absence dit que la garde tient. */
const CONTENU = "contenu de la page demandée";

const monter = () =>
  render(
    <Shell>
      <p>{CONTENU}</p>
    </Shell>,
  );

/** La porte d'entrée, une fois la première lecture des projets tranchée. */
const porte = () => screen.findByRole("main", { name: "Choix du projet" });

/** La porte en mode création (#1294) : la question, puis la conversation. */
const creation = () => screen.findByRole("main", { name: "Nouveau projet" });

/** La proposition qu'une réponse de l'orchestration porte, déjà vérifiée. */
function demandeProjet(partiel: Partial<DemandeProjet> = {}): DemandeProjet {
  return {
    nom: "kombucha-vitrine",
    racine: "C:/Users/moi/Maestro/kombucha-vitrine",
    origine: "nouveau",
    versionner: true,
    deja_versionne: false,
    raison_nom: "Ce qu'il est, en deux mots.",
    raison_dossier: "Un dossier neuf, dans votre répertoire des projets.",
    raison_versionnement: "Chaque tâche sur sa branche.",
    ajustements: [],
    ...partiel,
  };
}

/** Le projet qu'un accord a déclaré, tel que la réponse le porte. */
function projetCree(partiel: Partial<ProjetCree> = {}): ProjetCree {
  return {
    id: "prj-neuf",
    nom: "kombucha-vitrine",
    racine: "C:/Users/moi/Maestro/kombucha-vitrine",
    origine: "nouveau",
    versionne: true,
    versionnement_refuse: "",
    ...partiel,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // Sans cela la visite guidée s'ouvrirait par-dessus le shell une fois entré.
  marquerGuideVu();
  chargerProjets.mockResolvedValue([]);
  creerProjet.mockResolvedValue(projetFactice());
  poserFilAssistance();
  window.sessionStorage.clear();
});

describe("l'écran de choix du projet", () => {
  it("occupe l'écran tant qu'aucun projet n'est actif", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    monter();

    const ecran = await porte();
    expect(
      within(ecran).getByRole("button", { name: "Ouvrir Dépensio" }),
    ).toBeInTheDocument();
    // Ni la page, ni le cadre : on ne voit rien de la Control Tower avant de
    // savoir de quel projet elle parlerait.
    expect(screen.queryByText(CONTENU)).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Navigation principale" }),
    ).toBeNull();
  });
});

describe("un projet naît dans la conversation (#1294)", () => {
  it("ouvre la conversation, sans formulaire, quand il n'y a rien à choisir", async () => {
    monter();
    const ecran = await creation();

    // Un poste sans projet n'a qu'une chose à proposer : la question, et le fil
    // de l'orchestration dessous — plus aucun formulaire à étapes.
    expect(
      within(ecran).getByRole("heading", {
        level: 1,
        name: "Que voulez-vous construire ?",
      }),
    ).toBeInTheDocument();
    expect(within(ecran).getByRole("region", { name: "Nouveau projet" })).toBeInTheDocument();
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("button", { name: "Déclarer le projet" })).toBeNull();
    // Le fil demandé est celui de l'orchestration, **sans projet** : c'est celui
    // qu'il fera naître qui lui en donnera un.
    expect(canalCourant()).toBe("orchestrateur");
    expect(projetDuFilCourant()).toBeNull();
    // Rien à choisir : aucun retour vers une liste vide.
    expect(
      screen.queryByRole("button", { name: "Choisir un projet existant" }),
    ).toBeNull();
  });

  it("s'ouvre sur « Nouveau projet », et se referme sur la liste", async () => {
    const utilisateur = userEvent.setup();
    chargerProjets.mockResolvedValue([projetFactice()]);
    monter();
    await porte();

    await utilisateur.click(screen.getByRole("button", { name: "Nouveau projet" }));
    await creation();

    await utilisateur.click(
      screen.getByRole("button", { name: "Choisir un projet existant" }),
    );
    expect(await porte()).toBeInTheDocument();
  });

  it("garde le retour quand la liste est illisible, qui n'est pas vide", async () => {
    // Vu à la relecture, API coupée : la création ouverte depuis la panne perdait
    // son retour, et plus rien ne ramenait à la liste ni à son « Réessayer ».
    const utilisateur = userEvent.setup();
    chargerProjets.mockRejectedValue(new Error("fetch failed"));
    monter();
    await screen.findByRole("alert");

    await utilisateur.click(screen.getByRole("button", { name: "Nouveau projet" }));
    const ecran = await creation();
    // La panne se dit aussi en création, avec de quoi relire — pas seulement par
    // le fil illisible dessous (relecture de clôture).
    const panne = within(ecran).getAllByRole("alert")[0];
    expect(panne).toHaveTextContent("fetch failed");
    expect(within(ecran).getByRole("button", { name: "Réessayer" })).toBeInTheDocument();
    // Sous la question, qui reste la première chose lue.
    const question = within(ecran).getByRole("heading", {
      level: 1,
      name: "Que voulez-vous construire ?",
    });
    expect(
      question.compareDocumentPosition(panne) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();

    await utilisateur.click(
      screen.getByRole("button", { name: "Choisir un projet existant" }),
    );
    expect(await screen.findByRole("button", { name: "Réessayer" })).toBeInTheDocument();
  });

  it("s'ouvre d'office quand « Nouveau projet » a été demandé depuis un projet", async () => {
    // L'écran Projets quitte le projet ouvert pour venir créer ici : la porte
    // l'apprend par la mémoire de session, et une seule fois.
    chargerProjets.mockResolvedValue([projetFactice()]);
    demanderNaissance();
    monter();

    expect(await creation()).toBeInTheDocument();
    expect(window.sessionStorage.getItem("maestro.porte.naissance")).toBeNull();
  });

  it("pose la proposition sur une carte, et l'accord part d'un geste", async () => {
    const utilisateur = userEvent.setup();
    const declarerProjet = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({
      messages: [
        messageFactice({ agent: "orchestrateur", contenu: "je veux un site vitrine pour mon kombucha" }),
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Un site vitrine : je vous le propose ci-dessous.",
          projet_propose: demandeProjet({
            ajustements: ["Le nom « kombucha » est déjà celui d'un projet : proposé « kombucha-vitrine »."],
          }),
        }),
      ],
      declarerProjet,
    });
    monter();
    await creation();

    const carte = await screen.findByRole("region", { name: "Proposition de projet" });
    expect(within(carte).getByRole("heading", { name: "Créer ce projet ?" })).toBeInTheDocument();
    expect(carte).toHaveTextContent("kombucha-vitrine");
    expect(carte).toHaveTextContent("C:/Users/moi/Maestro/kombucha-vitrine");
    expect(carte).toHaveTextContent("Git, en local");
    // Chaque choix avec sa raison, et ce que la vérification a changé, dit.
    expect(carte).toHaveTextContent("Chaque tâche sur sa branche.");
    const changements = within(carte).getByRole("list", {
      name: "Ce que la vérification a changé",
    });
    expect(changements).toHaveTextContent("déjà celui d'un projet");
    // Dit **avant** les lignes, pour qu'on le lise avant le nom qu'il corrige.
    expect(
      changements.compareDocumentPosition(within(carte).getByText("Nom")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // Aucun champ : une correction se dit dans la conversation.
    expect(within(carte).queryByRole("textbox")).toBeNull();

    await utilisateur.click(within(carte).getByRole("button", { name: "Créer le projet" }));
    expect(declarerProjet).toHaveBeenCalledWith(true);
    expect(creerProjet).not.toHaveBeenCalled();
  });

  it("dit un import pour un dossier existant, et refuse d'un geste", async () => {
    const utilisateur = userEvent.setup();
    const declarerProjet = vi.fn().mockResolvedValue(undefined);
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Je l'importe ?",
          projet_propose: demandeProjet({
            nom: "racines",
            racine: "E:/sites/racines",
            origine: "existant",
            versionner: false,
            deja_versionne: true,
          }),
        }),
      ],
      declarerProjet,
    });
    monter();

    const carte = await screen.findByRole("region", { name: "Proposition de projet" });
    expect(within(carte).getByRole("heading", { name: "Importer ce projet ?" })).toBeInTheDocument();
    expect(carte).toHaveTextContent("Déjà sous Git");
    // Sa raison est un fait du code : la seule ligne qui n'en portait pas.
    expect(carte).toHaveTextContent("Son dépôt Git est constaté tel quel");
    expect(carte).toHaveTextContent("rien n'y sera écrit à l'import");

    await utilisateur.click(within(carte).getByRole("button", { name: "Pas maintenant" }));
    expect(declarerProjet).toHaveBeenCalledWith(false);
  });

  it("ne promet pas un import intouché quand la mise sous Git y écrira", async () => {
    // Relu sur la vraie stack : « rien n'y sera écrit » au-dessus d'une mise sous
    // Git, qui crée `.git` et un premier commit dans le dossier de la personne.
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Je l'importe ?",
          projet_propose: demandeProjet({
            nom: "atelier-savons",
            racine: "E:/sites/atelier-savons",
            origine: "existant",
            versionner: true,
            deja_versionne: false,
          }),
        }),
      ],
    });
    monter();

    const carte = await screen.findByRole("region", { name: "Proposition de projet" });
    expect(carte).toHaveTextContent("l'import n'y écrit que sa mise sous Git");
    expect(carte).not.toHaveTextContent("rien n'y sera écrit");
  });

  it("entre dans le projet né dans la conversation, qui continue dans sa colonne", async () => {
    const neuf = projetFactice({ id: "prj-neuf", nom: "kombucha-vitrine" });
    // La porte lit la liste une première fois (vide), puis la relit pour
    // ouvrir la fiche du projet né : racine canonicalisée, VCS constaté.
    chargerProjets.mockResolvedValueOnce([]).mockResolvedValue([neuf]);
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Le projet est déclaré.",
          projet_cree: projetCree(),
        }),
      ],
    });
    monter();

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe("prj-neuf");
    // Le relais : la conversation où il est né est ouverte dans le projet.
    expect(lireConversationOuverte()).toBe(true);
    // Rien n'a été déclaré par l'écran : c'est l'accord, dans le fil, qui l'a fait.
    expect(creerProjet).not.toHaveBeenCalled();
  });

  it("entre dans le projet né même si le fil se relit pendant qu'on l'ouvre", async () => {
    // Constaté sur la vraie stack : après le geste, `useChat` relit le fil, et
    // chaque relecture rend des messages **neufs** portant le même fait. La porte
    // annulait alors la lecture de la liste en vol et ne la relançait jamais —
    // le projet était déclaré, et on restait devant la porte.
    const neuf = projetFactice({ id: "prj-neuf", nom: "kombucha-vitrine" });
    let livrer: (projets: Projet[]) => void = () => {};
    chargerProjets
      .mockResolvedValueOnce([])
      .mockImplementationOnce(
        () =>
          new Promise<Projet[]>((resoudre) => {
            livrer = resoudre;
          }),
      )
      .mockResolvedValue([neuf]);
    const reponse = () =>
      messageFactice({
        agent: "orchestrateur",
        auteur: "orchestrateur",
        contenu: "Le projet est déclaré.",
        projet_cree: projetCree(),
      });
    poserFilAssistance({ messages: [reponse()] });
    const { rerender } = monter();
    await creation();
    await waitFor(() => expect(chargerProjets).toHaveBeenCalledTimes(2));

    // Le fil se relit : les mêmes faits, dans des objets neufs.
    poserFilAssistance({ messages: [reponse()] });
    rerender(
      <Shell>
        <p>{CONTENU}</p>
      </Shell>,
    );
    await act(async () => livrer([neuf]));

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe("prj-neuf");
  });

  it("n'entre pas dans le projet né d'une conversation précédente", async () => {
    // Vu sur la vraie stack à la relecture : « Nouveau projet » juste après une
    // naissance relisait un instant la conversation précédente, y trouvait son
    // projet né, et y entrait — la création s'ouvrait sur un autre projet.
    chargerProjets.mockResolvedValue([
      projetFactice(),
      projetFactice({ id: "prj-neuf", nom: "kombucha-vitrine" }),
    ]);
    poserFilAssistance({
      conversation: "conv-precedente",
      messages: [
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Le projet est déclaré.",
          projet_cree: projetCree(),
        }),
      ],
      nouvelleConversation: async () => {
        ecrireConversationDuFil("orchestrateur", "conv-neuve");
      },
    });
    demanderNaissance();
    monter();
    await creation();
    await act(async () => {
      await new Promise((resoudre) => setTimeout(resoudre, 50));
    });

    expect(lireProjetActifId()).toBeNull();
    expect(screen.queryByText(CONTENU)).toBeNull();
  });

  it("montre, sous la réponse, le projet que l'accord a déclaré", async () => {
    poserFilAssistance({
      messages: [
        messageFactice({
          agent: "orchestrateur",
          auteur: "orchestrateur",
          contenu: "Le projet est déclaré.",
          projet_cree: projetCree({ id: "prj-introuvable" }),
        }),
      ],
    });
    monter();

    // Le fait est sous la bulle, qu'on puisse ou non ouvrir le projet ensuite.
    expect(await screen.findByText("Projet créé :")).toBeInTheDocument();
    // Et une fiche introuvable se dit, au lieu d'une porte figée.
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "je ne le retrouve pas dans la liste",
    );
  });
});

describe("la mémoire du projet actif", () => {
  it("retrouve le projet retenu d'une visite à l'autre", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "Choix du projet" })).toBeNull();
  });

  it("ramène au choix, avec son motif, sur un projet devenu introuvable", async () => {
    chargerProjets.mockResolvedValue([
      projetFactice({ id: "prj-autre", nom: "Autre" }),
    ]);
    ecrireProjetActifId("prj-disparu");
    monter();

    const motif = await screen.findByRole("alert");
    expect(motif).toHaveTextContent("prj-disparu");
    // En mots, pas sous l'identifiant de l'API (#946, C7) : c'est cette
    // ligne-là que le retex du 2026-09-11 a lue sur la porte d'entrée.
    expect(motif).toHaveTextContent("motif : Projet inconnu");
    expect(motif).not.toHaveTextContent("projet-inconnu");
    // Le conseil du motif prolonge le message du backend (EF-38).
    expect(motif).toHaveTextContent(/recharger la liste/);

    // L'identifiant fantôme est oublié — sans quoi chaque visite rejouerait le
    // même refus —, et le choix reste possible sur ce qui existe encore.
    expect(lireProjetActifId()).toBeNull();
    expect(
      screen.getByRole("button", { name: "Ouvrir Autre" }),
    ).toBeInTheDocument();
  });

  it("ne prend pas une API muette pour une absence de projet", async () => {
    chargerProjets.mockRejectedValue(new Error("fetch failed"));
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();

    expect(await screen.findByRole("alert")).toHaveTextContent("fetch failed");
    // On n'a rien pu lire, ce qui n'est pas n'avoir rien à lire : ni « aucun
    // projet déclaré », ni l'oubli du choix retenu.
    expect(screen.queryByText(/Aucun projet déclaré/)).toBeNull();
    expect(lireProjetActifId()).toBe("prj-7f3a1c2b");
    expect(screen.queryByText(CONTENU)).toBeNull();
  });

  it("laisse réessayer, et le choix retenu reprend dès que l'API répond", async () => {
    const utilisateur = userEvent.setup();
    chargerProjets.mockRejectedValueOnce(new Error("fetch failed"));
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();
    await screen.findByRole("alert");

    await utilisateur.click(screen.getByRole("button", { name: "Réessayer" }));
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
  });

  it("repasse par la porte quand le projet actif est relâché depuis ailleurs", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    ecrireProjetActifId("prj-7f3a1c2b");
    monter();
    await screen.findByText(CONTENU);

    // Le stockage est la source de vérité et l'événement la notification : c'est
    // par là que passeront le sélecteur du lot 4 (#280) et un autre onglet.
    act(() => ecrireProjetActifId(null));
    expect(await porte()).toBeInTheDocument();
  });
});

describe("le défilement de la porte (#306)", () => {
  /**
   * Le conteneur qui **doit** défiler autour d'un écran de la porte. On le prend
   * par le parent du `<main>` et non par une recherche de classe : ce que le
   * test protège, c'est justement qu'il y en ait un *sur ce chemin-là*.
   */
  const conteneur = (ecran: HTMLElement) => {
    expect(ecran.parentElement).not.toBeNull();
    return ecran.parentElement as HTMLElement;
  };

  it("donne un ascenseur à la création, où la conversation déborde", async () => {
    // Depuis #1294 ce qui déborde n'est plus un formulaire mais le fil de la
    // création, ouvert d'office sur un poste sans projet : il s'allonge à chaque
    // tour, et son composeur doit rester atteignable au bas du défilement.
    monter();
    const ecran = await creation();

    const defilant = conteneur(ecran);
    expect(defilant.className).toContain("overflow-y-auto");
    // `min-h-0` + `flex-1` : sans eux le conteneur se dimensionne sur le
    // fil au lieu de rétrécir sous lui, et l'`overflow-y-auto` n'a jamais rien
    // à faire défiler (même chaîne qu'au #248).
    expect(defilant.className).toContain("min-h-0");
    expect(defilant.className).toContain("flex-1");
  });

  it("met l'ascenseur au bord de l'écran, pas au bord de la colonne", async () => {
    chargerProjets.mockResolvedValue([projetFactice()]);
    monter();
    const ecran = await porte();
    // La colonne reste centrée et bornée…
    expect(ecran.className).toContain("max-w-2xl");
    expect(ecran.className).toContain("mx-auto");
    // …donc c'est bien le conteneur pleine largeur qui défile, et pas elle :
    // un `overflow-y-auto` posé ici collerait l'ascenseur au texte.
    expect(ecran.className).not.toContain("overflow");
    expect(conteneur(ecran).className).not.toContain("max-w-");
  });

  it("couvre aussi l'écran d'ouverture, qui partage le même cadre", () => {
    // Les deux écrans de la porte se ressemblent parce qu'ils sont le même
    // cadre : les séparer ferait revenir le défaut sur celui qu'on oublierait.
    render(<EcranOuverture />);
    const ecran = screen.getByRole("main", {
      name: "Ouverture de la Control Tower",
    });
    expect(conteneur(ecran).className).toContain("overflow-y-auto");
  });
});

describe("une page atteinte directement, sans projet actif", () => {
  it("passe par le choix puis rend la page demandée, sans redirection", async () => {
    poserChemin("/couts");
    chargerProjets.mockResolvedValue([projetFactice()]);
    const utilisateur = userEvent.setup();
    monter();

    // La page demandée est nommée : on sait où l'on retombe avant de choisir.
    expect(await porte()).toHaveTextContent("Coûts & analytics");

    await utilisateur.click(
      screen.getByRole("button", { name: "Ouvrir Dépensio" }),
    );

    // La page demandée revient — et dans son cadre, titre compris.
    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(
      "Coûts & analytics",
    );
    // Garde de shell, pas redirection : rien n'a été poussé, donc rien n'a eu à
    // être mémorisé — et l'historique du navigateur n'a pas d'aller-retour à
    // défaire.
    expect(navigations).toEqual([]);
    expect(cheminCourant()).toBe("/couts");
  });
});
