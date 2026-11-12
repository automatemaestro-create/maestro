/**
 * Le premier lancement (#642) : la porte d'un poste neuf guide le modèle, le
 * dossier des projets et le premier projet, au lieu d'ouvrir la création sur un
 * fournisseur jamais choisi.
 *
 * Ce qui est gardé ici est ce que les critères promettent à l'écran :
 *
 * - C1 — un poste neuf voit le parcours ; chaque étape s'ouvre sur l'écran déjà
 *   livré (la carte du fournisseur, le répertoire des projets, la conversation),
 *   et le parcours finit sur le premier projet né, qui le termine ;
 * - C2 — ce qui est recueilli est vérifié par les épreuves déjà livrées : un accès
 *   qui échoue le dit avec son motif et ne coche rien, un répertoire refusé le dit ;
 * - C3 — le parcours ne s'ouvre ni à un poste qui l'a terminé ni à un poste qui a
 *   des projets, et se revoit à la demande depuis les Paramètres.
 *
 * Et la place de l'écran de l'étape ouverte (#1617) : à côté de la liste au large,
 * sous la ligne de son étape plus étroit, jamais monté deux fois ; l'état dit une
 * fois, par la ligne ; l'étape du dossier dans les mots de qui découvre Maestro.
 *
 * Le moteur derrière (`/api/accueil`) est gardé par `tests/test_accueil.py`.
 */

import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { ParametresGeneral } from "@/components/parametres/ParametresGeneral";
import { Shell } from "@/components/Shell";
import { phraseDuVerdict } from "@/lib/fournisseurParDefaut";
import { etapesDuParcours, premierLancementDemande } from "@/lib/premierLancement";
import { lireProjetActifId } from "@/lib/projetActif";
import type { ProjetCree } from "@/lib/types";

import {
  ACCES_CLAUDE,
  basculerLargeurFenetre,
  demandesEnregistrees,
  ETAT_FOURNISSEUR_DEFAUT_NU,
  finsDuPremierLancement,
  messageFactice,
  poserAccueil,
  poserFilAssistance,
  poserFournisseurDefaut,
  poserLargeurFenetre,
  poserPanneAccueil,
  poserProjetActif,
  poserProjets,
  poserRepertoire,
  poserVerdictEpreuve,
  projetFactice,
  REPERTOIRE_PROPOSE,
  retenirRepertoire,
} from "./aides";

const CONTENU = "contenu de la page demandée";

const monter = (contenu: React.ReactNode = <p>{CONTENU}</p>) =>
  render(<Shell>{contenu}</Shell>);

/** Le parcours, une fois la porte tranchée. */
const parcours = () => screen.findByRole("main", { name: "Premier lancement" });

/** La ligne d'une étape : le bouton de son titre. */
const etape = (numero: 1 | 2 | 3) =>
  screen.getByRole("button", { name: new RegExp(`^${numero}\\. `) });

const TITRES = {
  1: "1. Le modèle qui fait le travail",
  2: "2. Le dossier où naissent vos projets",
  3: "3. Votre premier projet",
} as const;

/** L'écran de l'étape ouverte : la région que nomme le titre de sa ligne. */
const ecran = (numero: 1 | 2 | 3) => screen.getByRole("region", { name: TITRES[numero] });

/** Un choix de fournisseur enregistré — il ne l'est qu'éprouvé. */
const CHOIX_ENREGISTRE = {
  choix: {
    fournisseur: "claude",
    acces: "abonnement",
    modele: null,
    adresse: null,
    eprouve_le: "2026-11-09T10:00:00+00:00",
    pose_le: "2026-11-09T10:00:00+00:00",
  },
};

function projetCree(partiel: Partial<ProjetCree> = {}): ProjetCree {
  return {
    id: "prj-neuf",
    nom: "kombucha-vitrine",
    racine: "C:/Users/essai/Maestro/kombucha-vitrine",
    origine: "nouveau",
    versionne: true,
    versionnement_refuse: "",
    ...partiel,
  };
}

beforeEach(() => {
  poserAccueil({ a_faire: true, termine_le: null });
});

describe("le premier lancement d'un poste neuf (C1)", () => {
  it("ouvre le parcours au lieu de la création, sur l'étape du modèle", async () => {
    monter();

    await parcours();
    expect(
      screen.getByRole("heading", { level: 1, name: "Mettre Maestro en route" }),
    ).toBeInTheDocument();
    const etapes = within(screen.getByRole("list", { name: "Étapes du premier lancement" }));
    expect(etapes.getAllByRole("listitem")).toHaveLength(3);
    // Le modèle d'abord, une fois les réglages lus : l'étape dit à quoi sert le
    // choix, dans les mots du public.
    await waitFor(() => expect(etape(1)).toHaveAttribute("aria-expanded", "true"));
    await waitFor(() =>
      expect(etape(1)).toHaveTextContent(
        "Maestro confie chaque conversation et chaque tâche à un fournisseur de modèle",
      ),
    );
    // L'écran de l'étape est la carte livrée par #1609, sans son titre ni sa tête.
    expect(
      await within(ecran(1)).findByRole("group", { name: "Accès au fournisseur par défaut" }),
    ).toBeInTheDocument();
    expect(within(ecran(1)).queryByRole("heading", { name: "Par défaut" })).toBeNull();
    expect(within(ecran(1)).queryByText(/Utilisé maintenant :/)).toBeNull();
    // La création n'est pas ouverte : la conversation attend un modèle.
    expect(screen.queryByRole("heading", { name: "Que voulez-vous construire ?" })).toBeNull();
    expect(etape(3)).toBeDisabled();
    expect(etape(3)).toHaveTextContent("Après le modèle : c'est lui qui vous répondra.");
  });

  it("dit le dossier des projets résolu, sans rien taper ni l'ouvrir", async () => {
    poserRepertoire({ cree: true });
    monter();

    await parcours();
    await waitFor(() =>
      expect(etape(2)).toHaveTextContent(
        "Vos projets naissent dans C:/Users/essai/Maestro, que Maestro vient de créer.",
      ),
    );
    // Faite, l'étape est repliée et n'offre aucun geste : sa ligne seule l'ouvre.
    expect(etape(2)).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("button", { name: "Changer de dossier…" })).toBeNull();
  });

  it("coche le modèle sur l'épreuve réussie, puis passe au premier projet", async () => {
    const utilisateur = userEvent.setup();
    monter();
    await parcours();
    const premier = await screen.findByRole("region", { name: TITRES[1] });
    await utilisateur.click(await within(premier).findByRole("button", { name: "Éprouver" }));
    await utilisateur.click(within(premier).getByRole("button", { name: "Enregistrer" }));

    // Faite sur le fait — l'enregistrement d'un choix éprouvé —, jamais sur un clic.
    expect(demandesEnregistrees).toHaveLength(1);
    await waitFor(() =>
      expect(etape(1)).toHaveTextContent("éprouvé le 9 novembre 2026."),
    );
    // Le dossier est déjà fait : l'écran passe à la conversation, la fin du parcours.
    expect(
      await screen.findByRole("heading", { level: 3, name: "Que voulez-vous construire ?" }),
    ).toBeInTheDocument();
    expect(etape(3)).toHaveAttribute("aria-expanded", "true");
    expect(etape(3)).toHaveFocus();
    expect(screen.queryByRole("group", { name: "Accès au fournisseur par défaut" })).toBeNull();
  });

  it("termine le parcours quand le premier projet naît, et y entre", async () => {
    poserFournisseurDefaut(CHOIX_ENREGISTRE);
    const { rerender } = monter();
    await screen.findByRole("heading", { level: 3, name: "Que voulez-vous construire ?" });
    expect(finsDuPremierLancement.nombre).toBe(0);

    // L'accord donné dans la conversation : la réponse porte le projet né.
    poserProjets([projetFactice({ id: "prj-neuf", nom: "kombucha-vitrine" })]);
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
    rerender(
      <Shell>
        <p>{CONTENU}</p>
      </Shell>,
    );

    expect(await screen.findByText(CONTENU)).toBeInTheDocument();
    expect(lireProjetActifId()).toBe("prj-neuf");
    expect(finsDuPremierLancement.nombre).toBe(1);
  });
});

describe("ce qui est recueilli est vérifié (C2)", () => {
  it("dit l'échec d'une épreuve avec son motif, et ne coche rien", async () => {
    const verdict = { ok: false, motif: "authentification", precision: "" } as const;
    poserVerdictEpreuve(verdict);
    const utilisateur = userEvent.setup();
    monter();
    await parcours();
    const premier = await screen.findByRole("region", { name: TITRES[1] });
    await utilisateur.click(await within(premier).findByRole("button", { name: "Éprouver" }));

    expect(await within(premier).findByText("Échec de l'épreuve")).toBeInTheDocument();
    expect(
      within(premier).getByText(phraseDuVerdict(verdict, ACCES_CLAUDE[0])),
    ).toBeInTheDocument();
    // Rien ne s'enregistre à l'aveugle, et le parcours n'avance pas.
    expect(within(premier).getByRole("button", { name: "Enregistrer" })).toBeDisabled();
    expect(etape(1)).toHaveAttribute("aria-expanded", "true");
    expect(etape(3)).toBeDisabled();
  });

  it("dit un répertoire refusé avec son motif, et l'ouvre une fois le modèle fait", async () => {
    poserFournisseurDefaut(CHOIX_ENREGISTRE);
    poserRepertoire({
      chemin: "E:/disque-debranche/Maestro",
      par_defaut: false,
      existe: false,
      refus: { motif: "dossier-absent", message: "Le dossier n'existe pas sur ce poste." },
    });
    monter();
    await parcours();

    await waitFor(() =>
      expect(etape(2)).toHaveTextContent("E:/disque-debranche/Maestro ne peut pas les recevoir"),
    );
    // L'étape qui reste à faire est ouverte, avec le refus motivé de #1022.
    expect(etape(2)).toHaveAttribute("aria-expanded", "true");
    expect(
      await within(ecran(2)).findByText(
        "Répertoire indisponible — Le dossier n'existe pas sur ce poste.",
      ),
    ).toBeInTheDocument();
    // Les dossiers autorisés (#1610) se montrent là où le répertoire se règle.
    expect(within(ecran(2)).getByText("Dossiers autorisés")).toBeInTheDocument();
  });
});

describe("le parcours ne se rejoue pas, et se revoit à la demande (C3)", () => {
  it("n'ouvre pas le parcours d'un poste qui l'a terminé : la création, comme avant", async () => {
    poserAccueil();
    monter();

    expect(await screen.findByRole("main", { name: "Nouveau projet" })).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "Premier lancement" })).toBeNull();
  });

  it("n'ouvre jamais le parcours à un poste qui a des projets", async () => {
    // Même si l'API disait le contraire : la porte ne le demande qu'à une liste vide.
    poserProjets([projetFactice()]);
    monter();

    expect(await screen.findByRole("main", { name: "Choix du projet" })).toBeInTheDocument();
    expect(screen.queryByRole("main", { name: "Premier lancement" })).toBeNull();
  });

  it("ne bloque pas la porte quand l'API du premier lancement est en panne", async () => {
    poserPanneAccueil("API injoignable");
    monter();

    expect(await screen.findByRole("main", { name: "Nouveau projet" })).toBeInTheDocument();
  });

  it("se revoit depuis Paramètres › Général, puis rend la main à la liste", async () => {
    poserAccueil();
    poserProjetActif(projetFactice({ id: "prj-a", nom: "depensio" }));
    const utilisateur = userEvent.setup();
    monter(<ParametresGeneral />);

    await utilisateur.click(
      await screen.findByRole("button", { name: "Revoir le premier lancement" }),
    );

    await parcours();
    // Revoir ne change rien : chaque étape dit son réglage tel qu'il est — ici,
    // aucun fournisseur choisi, et un projet déjà déclaré.
    await waitFor(() => expect(etape(2)).toHaveTextContent("Vos projets naissent dans"));
    expect(etape(1)).toHaveAttribute("aria-expanded", "true");
    expect(etape(3)).toHaveTextContent("1 projet déclaré sur ce poste.");
    await utilisateur.click(screen.getByRole("button", { name: "Revenir à mes projets" }));
    expect(await screen.findByRole("main", { name: "Choix du projet" })).toBeInTheDocument();
    // La demande a été lue une fois : un démarrage suivant ne le rejoue pas.
    expect(premierLancementDemande()).toBe(false);
  });

  it("revu avec tout en place, il le dit et ramène aux projets", async () => {
    poserAccueil();
    poserFournisseurDefaut(CHOIX_ENREGISTRE);
    poserProjetActif(projetFactice({ id: "prj-a", nom: "depensio" }));
    const utilisateur = userEvent.setup();
    monter(<ParametresGeneral />);
    await utilisateur.click(
      await screen.findByRole("button", { name: "Revoir le premier lancement" }),
    );
    await parcours();

    expect(await screen.findByText(/Tout est en place/)).toBeInTheDocument();
    await act(async () => {
      await utilisateur.click(screen.getByRole("button", { name: "Ouvrir mes projets" }));
    });
    expect(await screen.findByRole("main", { name: "Choix du projet" })).toBeInTheDocument();
  });

  it("n'ouvre aucune étape ni ne déplace le focus tant que les réglages se lisent", async () => {
    // Vu sur la vraie stack : le modèle lu avant le dossier ouvrait l'étape du
    // dossier le temps d'une lecture, et lui laissait le focus.
    poserAccueil();
    poserFournisseurDefaut(CHOIX_ENREGISTRE);
    poserProjetActif(projetFactice({ id: "prj-a", nom: "depensio" }));
    const utilisateur = userEvent.setup();
    monter(<ParametresGeneral />);
    const revoir = await screen.findByRole("button", { name: "Revoir le premier lancement" });
    const relacher = retenirRepertoire();
    await utilisateur.click(revoir);
    await parcours();

    await waitFor(() => expect(etape(1)).toHaveAccessibleName(/éprouvé le/));
    expect(etape(2)).toHaveAccessibleName(/Lecture du réglage/);
    expect(screen.queryByRole("region", { name: TITRES[1] })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: TITRES[2] })).not.toBeInTheDocument();
    expect(screen.queryByText(/Tout est en place/)).not.toBeInTheDocument();

    await act(async () => relacher());
    expect(await screen.findByText(/Tout est en place/)).toBeInTheDocument();
    expect(etape(2)).not.toHaveFocus();
  });
});

describe("l'écran de l'étape ouverte, contre son étape (#1617)", () => {
  /** Les lignes de la liste des étapes, dans l'ordre. */
  const lignes = () =>
    within(screen.getByRole("list", { name: "Étapes du premier lancement" })).getAllByRole(
      "listitem",
    );

  it("au large, pose l'écran à côté de la liste, une seule fois", async () => {
    monter();
    await parcours();

    await within(await screen.findByRole("region", { name: TITRES[1] })).findByRole("group", {
      name: "Accès au fournisseur par défaut",
    });
    expect(screen.getAllByRole("region", { name: TITRES[1] })).toHaveLength(1);
    // Le volet est hors de la liste : la ligne ouverte en est l'onglet.
    expect(
      within(screen.getByRole("list", { name: "Étapes du premier lancement" })).queryByRole(
        "region",
      ),
    ).toBeNull();
    expect(etape(1)).toHaveAttribute("aria-controls", ecran(1).id);
  });

  it("plus étroit, pose l'écran sous la ligne de son étape", async () => {
    poserLargeurFenetre(420);
    monter();
    await parcours();

    const premiere = lignes()[0];
    expect(
      await within(premiere).findByRole("group", { name: "Accès au fournisseur par défaut" }),
    ).toBeInTheDocument();
    expect(within(premiere).getByRole("region", { name: TITRES[1] })).toBe(ecran(1));
    expect(within(lignes()[1]).queryByRole("region")).toBeNull();
  });

  it("suit la fenêtre redimensionnée sans monter l'écran deux fois", async () => {
    monter();
    await parcours();
    await within(await screen.findByRole("region", { name: TITRES[1] })).findByRole("group", {
      name: "Accès au fournisseur par défaut",
    });

    await act(async () => basculerLargeurFenetre(420));
    expect(within(lignes()[0]).getByRole("region", { name: TITRES[1] })).toBeInTheDocument();
    expect(screen.getAllByRole("region", { name: TITRES[1] })).toHaveLength(1);

    await act(async () => basculerLargeurFenetre(1440));
    expect(within(lignes()[0]).queryByRole("region")).toBeNull();
    expect(screen.getAllByRole("region", { name: TITRES[1] })).toHaveLength(1);
  });

  it("ne redit pas dans les cartes d'accès l'état que la ligne de l'étape porte", async () => {
    // Rien de choisi : l'exécution suit l'abonnement Claude. Dans les Paramètres, sa
    // carte le dit « Utilisé maintenant », et la saisie « pas encore éprouvé ».
    monter();
    await parcours();
    const premier = await screen.findByRole("region", { name: TITRES[1] });
    await within(premier).findByRole("button", { name: "Éprouver" });

    expect(within(premier).queryByText("Utilisé maintenant")).toBeNull();
    expect(within(premier).queryByText(/pas encore éprouvé/)).toBeNull();
  });

  it("dit le dossier des projets dans les mots de qui découvre Maestro", async () => {
    poserFournisseurDefaut(CHOIX_ENREGISTRE);
    const utilisateur = userEvent.setup();
    monter();
    await parcours();
    await waitFor(() => expect(etape(2)).toHaveTextContent("Vos projets naissent dans"));
    await utilisateur.click(etape(2));

    const deuxieme = ecran(2);
    expect(
      await within(deuxieme).findByText(/Chaque projet que Maestro crée pour vous y reçoit son dossier/),
    ).toBeInTheDocument();
    // Ni le libellé du réglage ni le chemin : le titre et le résumé de l'étape les disent.
    expect(within(deuxieme).queryByText("Répertoire des projets")).toBeNull();
    expect(within(deuxieme).queryByText(REPERTOIRE_PROPOSE.chemin)).toBeNull();
    expect(within(deuxieme).getByRole("button", { name: "Changer de dossier…" })).toBeEnabled();
    expect(
      await within(deuxieme).findByText(/Si l'explorateur refuse un dossier, vous pouvez l'y autoriser/),
    ).toBeInTheDocument();
    expect(within(deuxieme).getByText("Aucun pour l'instant.")).toBeInTheDocument();
  });
});

describe("l'état des étapes (lib/premierLancement)", () => {
  it("ouvre la première étape qui reste à faire, et garde une étape ouverte d'un clic", () => {
    const neuf = { modele: false, dossier: true, projet: false };
    expect(etapesDuParcours(neuf, null)).toEqual({
      etats: { 1: "a-faire", 2: "fait", 3: "attend" },
      courante: 1,
    });
    // Une étape faite se rouvre pour la changer.
    expect(etapesDuParcours(neuf, 2).courante).toBe(2);
    // Une étape qui attend ne s'ouvre pas.
    expect(etapesDuParcours(neuf, 3).courante).toBe(1);
    // Rien de lu encore : l'étape du modèle s'ouvre, sans rien se dire fait.
    expect(etapesDuParcours({ modele: null, dossier: null, projet: false }, null)).toEqual({
      etats: { 1: "a-faire", 2: "a-faire", 3: "attend" },
      courante: 1,
    });
    expect(
      etapesDuParcours({ modele: true, dossier: true, projet: true }, null).courante,
    ).toBeNull();
  });

  it("ne dit pas le choix éprouvé ici quand l'environnement en impose un autre", async () => {
    poserFournisseurDefaut({
      ...CHOIX_ENREGISTRE,
      effectif: { ...ETAT_FOURNISSEUR_DEFAUT_NU.effectif, fournisseur: "openai", acces: "local" },
      environnement: [{ variable: "MAESTRO_PROVIDER", valeur: "openai" }],
    });
    monter();
    await parcours();

    await waitFor(() =>
      expect(etape(1)).toHaveTextContent("imposé par l'environnement du poste."),
    );
  });
});
