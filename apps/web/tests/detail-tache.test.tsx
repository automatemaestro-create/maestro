/**
 * Le détail d'une tâche ouvert depuis la carte du Kanban (#251).
 *
 * Les tests de mise en page de la vague sont différés au lot 8 (#252) — pas
 * ceux-ci, pour deux raisons de docs/10 §5.1.
 *
 * La première est de la **logique critique** : les URL des liens utiles viennent
 * du flux, exactement comme celle du ticket externe (#192), et un `href` non
 * filtré exécute du code (`javascript:`) ou embarque une charge utile (`data:`).
 * Une régression y serait silencieuse — le lien resterait cliquable et aurait
 * l'air sain.
 *
 * La seconde est que le lot **modèle (#246) n'est pas livré** : le backend ne
 * sert encore ni description, ni étapes, ni liens. Ces tests sont donc le seul
 * endroit où le panneau est exercé avec des données, et le seul garde-fou du
 * critère « une tâche sans détail affiche exactement la carte d'aujourd'hui »,
 * qui est justement l'état de **toutes** les tâches réelles aujourd'hui.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { Kanban } from "@/components/Kanban";
import { detailDe, etapesDe, liensDe } from "@/lib/detailTache";

import { agentFactice, projetFactice, tacheFactice } from "./aides";

/** Une tâche qui porte tout le détail : description, étapes, liens. */
function tacheDetaillee() {
  return tacheFactice({
    titre: "Écrire les tests",
    description: "Couvrir le panneau de détail\net ses trois sections.",
    etapes: [
      { libelle: "Lire le ticket", etat: "faite" },
      { libelle: "Écrire le composant", etat: "en_cours" },
      { libelle: "Passer la revue", etat: "a_faire" },
    ],
    liens: [
      {
        libelle: "Écran Kanban",
        url: "https://figma.test/fichier/251",
        nature: "maquette",
      },
      { libelle: "#251", url: "https://gitlab.test/i/251", nature: "ticket" },
      { libelle: "maestro", url: "https://gitlab.test/maestro", nature: "depot" },
    ],
  });
}

function rendreKanban(taches = [tacheDetaillee()], reassigner = vi.fn()) {
  render(
    <Kanban
      taches={taches}
      agents={[agentFactice({ nom: "qa", role: "Testeur" })]}
      reassigner={reassigner}
      projet={projetFactice()}
    />,
  );
  return reassigner;
}

async function ouvrirLeDetail() {
  const utilisateur = userEvent.setup();
  await utilisateur.click(
    screen.getByRole("button", { name: /Ouvrir le détail de la tâche/ }),
  );
  return utilisateur;
}

// --- Normalisation ---------------------------------------------------------

describe("detailDe", () => {
  it("une tâche d'aujourd'hui n'a aucun détail", () => {
    // Le backend ne sert pas encore les trois champs : ils arrivent `undefined`,
    // et rien ne doit planter à leur lecture.
    const detail = detailDe(tacheFactice());
    expect(detail.vide).toBe(true);
    expect(detail.description).toBe("");
    expect(detail.etapes).toEqual([]);
    expect(detail.liens).toEqual([]);
  });

  it("une description blanche ne compte pas pour un détail", () => {
    expect(detailDe(tacheFactice({ description: "   \n " })).vide).toBe(true);
    expect(detailDe(tacheFactice({ description: null })).vide).toBe(true);
  });

  it("compte les étapes terminées", () => {
    expect(detailDe(tacheDetaillee()).faites).toBe(1);
  });
});

describe("etapesDe", () => {
  it("retire les étapes sans libellé, qui fausseraient l'avancement", () => {
    const etapes = etapesDe(
      tacheFactice({
        etapes: [
          { libelle: "  ", etat: "faite" },
          { libelle: "Vraie étape", etat: "faite" },
        ],
      }),
    );
    expect(etapes).toEqual([{ libelle: "Vraie étape", etat: "faite" }]);
  });

  it("un état inconnu retombe sur « à faire » sans perdre l'étape", () => {
    const etapes = etapesDe(
      tacheFactice({ etapes: [{ libelle: "Étape", etat: "zoinx" }] }),
    );
    expect(etapes).toEqual([{ libelle: "Étape", etat: "a_faire" }]);
  });

  it("tolère un champ qui n'est pas une liste", () => {
    // Le flux peut envoyer autre chose que ce que le contrat annonce : la carte
    // ne doit pas tomber pour autant.
    const tordue = tacheFactice();
    (tordue as { etapes?: unknown }).etapes = "pas une liste";
    expect(etapesDe(tordue)).toEqual([]);
  });
});

describe("liensDe", () => {
  it("ne pose que des URL suivables", () => {
    const liens = liensDe(
      tacheFactice({
        liens: [
          { libelle: "Sain", url: "https://gitlab.test/x", nature: "ticket" },
          { libelle: "Piégé", url: "javascript:alert(1)", nature: "ticket" },
          { libelle: "Charge", url: "data:text/html,<script>", nature: "depot" },
          { libelle: "Local", url: "file:///etc/passwd", nature: "depot" },
          { libelle: "Relatif", url: "/interne", nature: "maquette" },
        ],
      }),
    );
    expect(liens.map((l) => l.url)).toEqual([
      "https://gitlab.test/x",
      null,
      null,
      null,
      null,
    ]);
  });

  it("une nature inconnue reste un lien, elle ne disparaît pas", () => {
    const liens = liensDe(
      tacheFactice({
        liens: [{ libelle: "Ailleurs", url: "https://a.test", nature: "wiki" }],
      }),
    );
    expect(liens[0].nature).toBe("lien");
  });

  it("retombe sur le nom de la nature quand le libellé manque", () => {
    const liens = liensDe(
      tacheFactice({
        liens: [{ libelle: "", url: "https://figma.test/f", nature: "maquette" }],
      }),
    );
    expect(liens[0].libelle).toBe("Maquette");
  });

  it("écarte le lien qui n'a ni URL suivable ni libellé propre", () => {
    expect(
      liensDe(
        tacheFactice({
          liens: [{ libelle: "  ", url: "javascript:alert(1)", nature: "depot" }],
        }),
      ),
    ).toEqual([]);
  });
});

// --- Critère 1 : le clic ouvre le détail -----------------------------------

describe("ouverture du détail", () => {
  it("un clic sur la carte ouvre le panneau", async () => {
    rendreKanban();
    await ouvrirLeDetail();
    const panneau = screen.getByRole("dialog", {
      name: "Détail de la tâche Écrire les tests",
    });
    expect(panneau).toHaveAttribute("aria-modal", "true");
  });

  it("le panneau porte la description", async () => {
    rendreKanban();
    await ouvrirLeDetail();
    const panneau = screen.getByRole("dialog");
    expect(
      within(panneau).getByText(/Couvrir le panneau de détail/),
    ).toBeInTheDocument();
  });

  it("le panneau porte les étapes en checklist et leur avancement", async () => {
    rendreKanban();
    await ouvrirLeDetail();
    const etapes = screen.getByRole("region", { name: "Étapes" });
    expect(within(etapes).getByText("Lire le ticket")).toBeInTheDocument();
    expect(within(etapes).getByText("Écrire le composant")).toBeInTheDocument();
    expect(within(etapes).getByText("Passer la revue")).toBeInTheDocument();
    expect(within(etapes).getByText("1/3")).toBeInTheDocument();

    const avancement = within(etapes).getByRole("progressbar", {
      name: "Avancement des étapes",
    });
    expect(avancement).toHaveAttribute("aria-valuenow", "1");
    expect(avancement).toHaveAttribute("aria-valuemax", "3");
  });

  it("chaque étape dit son état à voix haute", async () => {
    rendreKanban();
    await ouvrirLeDetail();
    const etapes = screen.getByRole("region", { name: "Étapes" });
    expect(within(etapes).getByText("— terminée")).toBeInTheDocument();
    expect(within(etapes).getByText("— en cours")).toBeInTheDocument();
    expect(within(etapes).getByText("— à faire")).toBeInTheDocument();
  });

  it("les liens utiles nomment leur nature et partent en nouvel onglet", async () => {
    rendreKanban();
    await ouvrirLeDetail();
    const liens = screen.getByRole("region", { name: "Liens utiles" });

    const maquette = within(liens).getByRole("link", {
      name: "Ouvrir maquette Écran Kanban dans un nouvel onglet",
    });
    expect(maquette).toHaveAttribute("href", "https://figma.test/fichier/251");
    expect(maquette).toHaveAttribute("target", "_blank");
    expect(maquette).toHaveAttribute("rel", "noopener noreferrer");

    expect(
      within(liens).getByRole("link", { name: /Ouvrir ticket #251/ }),
    ).toHaveAttribute("href", "https://gitlab.test/i/251");
    expect(
      within(liens).getByRole("link", { name: /Ouvrir dépôt maestro/ }),
    ).toHaveAttribute("href", "https://gitlab.test/maestro");
  });

  it("un lien non suivable reste lisible sans devenir cliquable", async () => {
    rendreKanban([
      tacheFactice({
        liens: [
          { libelle: "Maquette v2", url: "javascript:alert(1)", nature: "maquette" },
        ],
      }),
    ]);
    await ouvrirLeDetail();
    const liens = screen.getByRole("region", { name: "Liens utiles" });
    expect(within(liens).queryByRole("link")).toBeNull();
    expect(within(liens).getByText("Maquette v2")).toBeInTheDocument();
  });

  it("ne rend que les sections qui ont quelque chose à dire", async () => {
    rendreKanban([tacheFactice({ description: "Juste un mot." })]);
    await ouvrirLeDetail();
    expect(screen.getByRole("region", { name: "Description" })).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Étapes" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Liens utiles" })).toBeNull();
  });

  it("Échap ferme et rend le focus à la carte", async () => {
    rendreKanban();
    const utilisateur = await ouvrirLeDetail();
    await utilisateur.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(
      screen.getByRole("button", { name: /Ouvrir le détail de la tâche/ }),
    ).toHaveFocus();
  });

  it("le bouton de fermeture referme aussi", async () => {
    rendreKanban();
    const utilisateur = await ouvrirLeDetail();
    await utilisateur.click(
      screen.getByRole("button", { name: "Fermer le détail de la tâche" }),
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

// --- Critère 2 : la carte reste dense --------------------------------------

describe("densité de la carte", () => {
  it("le détail n'est pas versé dans la carte", async () => {
    rendreKanban();
    const carte = screen.getByRole("article");
    expect(within(carte).queryByText(/Couvrir le panneau de détail/)).toBeNull();
    expect(within(carte).queryByText("Lire le ticket")).toBeNull();
    expect(within(carte).queryByRole("link")).toBeNull();

    // Ouvert, le détail est ailleurs : la carte n'a pas gonflé.
    await ouvrirLeDetail();
    expect(within(carte).queryByText("Lire le ticket")).toBeNull();
  });

  it("la carte garde titre, agent, statut et coût", () => {
    rendreKanban();
    const carte = screen.getByRole("article");
    expect(within(carte).getByText("Écrire les tests")).toBeInTheDocument();
    expect(within(carte).getByText(/dev/)).toBeInTheDocument();
    expect(within(carte).getByText("Assignée")).toBeInTheDocument();
  });

  it("le ticket externe reste sur la carte (#192)", () => {
    rendreKanban([
      tacheDetaillee(),
      tacheFactice({
        id: "T-2",
        ticket: { id: "#192", url: "https://gitlab.test/i/192" },
      }),
    ]);
    expect(
      screen.getByRole("link", { name: /Ouvrir le ticket externe #192/ }),
    ).toHaveAttribute("href", "https://gitlab.test/i/192");
  });
});

// --- Critère 3 : sans détail, la carte d'aujourd'hui ------------------------

describe("tâche sans détail", () => {
  it("aucun bouton d'ouverture, le titre reste du texte", () => {
    rendreKanban([tacheFactice()]);
    expect(
      screen.queryByRole("button", { name: /Ouvrir le détail/ }),
    ).toBeNull();
    expect(screen.getByText("Écrire les tests")).toBeInTheDocument();
  });

  it("cliquer la carte n'ouvre aucun panneau vide", async () => {
    rendreKanban([tacheFactice()]);
    const utilisateur = userEvent.setup();
    await utilisateur.click(screen.getByRole("article"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("aucune section de remplissage nulle part", () => {
    rendreKanban([tacheFactice()]);
    expect(screen.queryByRole("region", { name: "Description" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Étapes" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Liens utiles" })).toBeNull();
    expect(screen.queryByText("—")).toBeNull();
  });
});

// --- La réassignation manuelle survit (EF-11/EF-20) ------------------------

describe("réassignation", () => {
  it("continue de fonctionner depuis la carte", async () => {
    const reassigner = rendreKanban([tacheDetaillee()]);
    const utilisateur = userEvent.setup();
    const carte = screen.getByRole("article");
    await utilisateur.selectOptions(
      within(carte).getByLabelText(/Réassigner la tâche/),
      "qa",
    );
    expect(reassigner).toHaveBeenCalledWith("T-1", "qa");
  });

  it("un clic sur le sélecteur n'ouvre pas le panneau par-dessus", async () => {
    rendreKanban();
    const utilisateur = userEvent.setup();
    const carte = screen.getByRole("article");
    await utilisateur.click(within(carte).getByLabelText(/Réassigner la tâche/));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("est aussi offerte depuis le panneau", async () => {
    const reassigner = rendreKanban([tacheDetaillee()]);
    const utilisateur = await ouvrirLeDetail();
    const panneau = screen.getByRole("dialog");
    await utilisateur.selectOptions(
      within(panneau).getByLabelText(/Réassigner la tâche/),
      "qa",
    );
    expect(reassigner).toHaveBeenCalledWith("T-1", "qa");
  });
});
