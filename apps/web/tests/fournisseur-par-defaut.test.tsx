/**
 * Le fournisseur par défaut, choisi et éprouvé dans les Paramètres (#1609).
 *
 * La carte répond à une question — *quel fournisseur et quel modèle Maestro
 * utilise-t-il sur ce poste, et l'accès marche-t-il ?* — et ce qu'on garde ici
 * est ce qui la rend vraie :
 *
 * ① **ce qui est utilisé se lit à part de ce qui est coché** — l'effectif vient
 *    de l'API, la coche n'est que la saisie ;
 * ② **présent n'est pas éprouvé** — la sonde dit ce qu'elle a vu, seule
 *    l'épreuve rend un verdict, et seul un verdict réussi s'enregistre ;
 * ③ **le verdict dit son motif et son geste**, par accès ;
 * ④ **une clé ne se réaffiche jamais** ;
 * ⑤ **l'environnement qui impose se dit**, avec le geste qui rend la main ;
 * ⑥ **une API coupée se dit** au lieu d'un écran vide.
 *
 * Le va-et-vient réel avec l'API et le modèle est exercé sur la vraie stack à la
 * clôture du ticket ; ici, les doubles de `setup.ts` (`poserFournisseurDefaut`,
 * `poserVerdictEpreuve`) tiennent lieu de serveur.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { FournisseurParDefaut } from "@/components/parametres/FournisseurParDefaut";
import { phraseDuVerdict } from "@/lib/fournisseurParDefaut";

import {
  ACCES_CLAUDE,
  ACCES_OPENAI,
  CATALOGUE_POSTE_NU,
  constatPosteFactice,
  demandesEnregistrees,
  demandesEprouvees,
  poserFournisseurDefaut,
  poserFournisseurs,
  poserPanneFournisseurDefaut,
  poserVerdictEpreuve,
} from "./aides";

const ABONNEMENT = "Abonnement Claude, par Claude Code sur ce poste";
const CLE_ANTHROPIC = "Clé d'API Anthropic";
const LOCAL = "Serveur de modèles sur ce poste, comme Ollama";

/** Monte la carte et attend que l'état du poste soit arrivé. */
async function monter() {
  const utilisateur = userEvent.setup();
  render(<FournisseurParDefaut />);
  await screen.findByRole("group", { name: "Accès au fournisseur par défaut" });
  return utilisateur;
}

/** La carte d'un accès : le bloc qui porte son bouton radio. */
function carte(libelle: string): HTMLElement {
  const radio = screen.getByRole("radio", { name: libelle });
  const bloc = radio.closest("div.rounded-carte");
  if (!(bloc instanceof HTMLElement)) throw new Error(`aucune carte pour ${libelle}`);
  return bloc;
}

/**
 * Ce que la sonde écrit dans `detail` pour la fiche d'un agent — le vocabulaire
 * du code, que la carte ne doit jamais rendre (les deux de `maestro/poste.py`).
 */
const DETAIL_CLI = "runtime des agents Claude — le SDK le lance en sous-processus";
const DETAIL_OLLAMA =
  "servi par le fournisseur `openai` — MAESTRO_PROVIDER=openai OPENAI_BASE_URL=http://localhost:11434/v1";

/** Un poste où la sonde a vu Claude Code et un Ollama qui sert `qwen2.5`. */
function posteOutille() {
  const [claude, openai] = CATALOGUE_POSTE_NU.fournisseurs;
  poserFournisseurs({
    ...CATALOGUE_POSTE_NU,
    fournisseurs: [
      {
        ...claude,
        present_ici: true,
        utilisable_ici: true,
        constats: [constatPosteFactice({ detail: DETAIL_CLI })],
      },
      {
        ...openai,
        present_ici: true,
        utilisable_ici: true,
        modeles_ici: ["qwen2.5"],
        constats: [
          constatPosteFactice({
            genre: "serveur_local",
            cle: "serveur:ollama",
            libelle: "Ollama",
            fournisseur: "openai",
            detail: DETAIL_OLLAMA,
            modeles: ["qwen2.5"],
          }),
        ],
      },
    ],
  });
}

describe("① ce qui est utilisé se lit à part de ce qui est coché", () => {
  it("dit l'effectif d'un poste où rien n'est choisi, et coche sa carte", async () => {
    await monter();

    expect(screen.getByText(/Utilisé maintenant :/).closest("p")).toHaveTextContent(
      `Utilisé maintenant : ${ABONNEMENT} · modèle claude-opus-5, retenu d'office`,
    );
    expect(
      screen.getByText("Rien n'est choisi ici : c'est le réglage d'origine de Maestro."),
    ).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: ABONNEMENT })).toBeChecked();
    expect(within(carte(ABONNEMENT)).getByText("Utilisé maintenant")).toBeInTheDocument();
  });

  it("coche une autre carte sans déplacer « Utilisé maintenant »", async () => {
    const utilisateur = await monter();

    await utilisateur.click(screen.getByRole("radio", { name: CLE_ANTHROPIC }));

    expect(screen.getByRole("radio", { name: CLE_ANTHROPIC })).toBeChecked();
    expect(within(carte(ABONNEMENT)).getByText("Utilisé maintenant")).toBeInTheDocument();
    expect(within(carte(CLE_ANTHROPIC)).queryByText("Utilisé maintenant")).toBeNull();
  });

  it("distingue le choix enregistré de ce que l'exécution utilise", async () => {
    // L'environnement impose `openai` : le choix du poste reste enregistré,
    // mais ce n'est pas lui qui sert — l'écran le dit des deux côtés.
    poserFournisseurDefaut({
      choix: {
        fournisseur: "claude",
        acces: "cle",
        modele: null,
        adresse: null,
        eprouve_le: "2026-11-09T10:00:00+00:00",
        pose_le: "2026-11-09T10:00:00+00:00",
      },
      effectif: {
        fournisseur: "openai",
        acces: "local",
        modele: "qwen2.5",
        modele_par_defaut: "qwen2.5",
      },
      environnement: [{ variable: "MAESTRO_PROVIDER", valeur: "openai" }],
    });

    await monter();

    expect(within(carte(CLE_ANTHROPIC)).getByText("Enregistré, pas utilisé")).toBeInTheDocument();
    expect(within(carte(LOCAL)).getByText("Utilisé maintenant")).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: CLE_ANTHROPIC })).toBeChecked();
  });
});

describe("② présent n'est pas éprouvé", () => {
  it("dit ce que la sonde a vu sans rendre de verdict ni ouvrir l'enregistrement", async () => {
    posteOutille();

    await monter();

    const abonnement = within(carte(ABONNEMENT));
    // Un programme trouvé ne dit pas qu'il est connecté : la phrase le dit.
    expect(
      abonnement.getByText(
        "Claude Code est installé sur ce poste ; sa connexion à un compte se vérifie en l'éprouvant.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText("Accès éprouvé")).toBeNull();
    expect(screen.getByText(/Ce réglage n'est pas encore éprouvé/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("dit en tête si l'accès en service est éprouvé, et le geste qui l'éprouve", async () => {
    const utilisateur = await monter();

    // Sa carte est cochée : le geste est dedans.
    expect(
      screen.getByText("Accès pas encore éprouvé : « Éprouver », dans sa carte ci-dessous."),
    ).toBeInTheDocument();

    // Une autre carte cochée et éprouvée ne parle pas de ce qui est en service.
    await utilisateur.click(screen.getByRole("radio", { name: CLE_ANTHROPIC }));
    expect(
      screen.getByText("Accès pas encore éprouvé : cochez sa carte ci-dessous, puis « Éprouver »."),
    ).toBeInTheDocument();
    await utilisateur.type(screen.getByLabelText("Clé"), "sk-ant-essai");
    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));
    await screen.findByText("Accès éprouvé");
    expect(screen.queryByText("Accès éprouvé à l'instant.")).toBeNull();

    // L'accès en service, éprouvé sur son modèle retenu d'office : la tête le dit.
    await utilisateur.click(screen.getByRole("radio", { name: ABONNEMENT }));
    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));
    expect(await screen.findByText("Accès éprouvé à l'instant.")).toBeInTheDocument();

    // Un autre modèle n'est plus ce qui est en service.
    await utilisateur.selectOptions(screen.getByRole("combobox", { name: "Modèle" }), "claude-sonnet-5");
    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));
    await screen.findByText("Accès éprouvé");
    expect(screen.queryByText("Accès éprouvé à l'instant.")).toBeNull();
  });

  it("dit l'échec de l'accès en service en tête, par une forme", async () => {
    poserVerdictEpreuve({ ok: false, motif: "authentification", precision: "authentication_failed" });
    const utilisateur = await monter();

    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));

    expect(
      await screen.findByText(
        "L'épreuve de cet accès vient d'échouer : son motif est dans sa carte, ci-dessous.",
      ),
    ).toBeInTheDocument();
  });

  it("propose le modèle retenu d'office sans répéter « par défaut »", async () => {
    await monter();

    const liste = screen.getByRole("combobox", { name: "Modèle" });
    expect(within(liste).getByRole("option", { name: "Sans préférence" })).toBeInTheDocument();
    expect(screen.getByText(/^Sans préférence, Maestro interroge/)).toHaveTextContent(
      "Sans préférence, Maestro interroge claude-opus-5.",
    );
  });

  it("dit ce que la sonde a vu dans les mots de la personne, jamais dans ceux du code", async () => {
    posteOutille();

    await monter();

    const texte = screen.getByRole("group", { name: "Accès au fournisseur par défaut" }).textContent;
    expect(texte).not.toContain(DETAIL_CLI);
    expect(texte).not.toContain(DETAIL_OLLAMA);
    expect(texte).not.toMatch(/MAESTRO_PROVIDER|OPENAI_BASE_URL|SDK|`/);
    expect(
      within(carte(LOCAL)).getByText("Ollama répond sur ce poste, avec 1 modèle : qwen2.5."),
    ).toBeInTheDocument();
  });

  it("atténue ce que la sonde n'a pas vu, sans le cacher", async () => {
    await monter();

    expect(within(carte(LOCAL)).getByText("Rien de détecté sur ce poste.")).toBeInTheDocument();
    expect(within(carte(CLE_ANTHROPIC)).getByText("Aucune clé enregistrée sur ce poste.")).
      toBeInTheDocument();
  });

  it("enregistre un choix éprouvé avec succès, et le dit pris sans redémarrage", async () => {
    const utilisateur = await monter();

    await utilisateur.selectOptions(screen.getByRole("combobox", { name: "Modèle" }), "claude-sonnet-5");
    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));

    expect(await screen.findByText("Accès éprouvé")).toBeInTheDocument();
    expect(screen.getByText("L'accès répond : un tour de modèle a abouti.")).toBeInTheDocument();
    expect(demandesEprouvees).toEqual([
      { fournisseur: "claude", acces: "abonnement", modele: "claude-sonnet-5", adresse: null, cle: null },
    ]);

    await utilisateur.click(screen.getByRole("button", { name: "Enregistrer" }));

    expect(await screen.findByText(/sans redémarrer Maestro/)).toBeInTheDocument();
    expect(demandesEnregistrees).toHaveLength(1);
    expect(screen.getByText(/Utilisé maintenant :/).closest("p")).toHaveTextContent(
      "modèle claude-sonnet-5",
    );
    expect(screen.getByText(/^Accès éprouvé le /)).toBeInTheDocument();
    expect(screen.getByText(/^Choisi ici le /)).toBeInTheDocument();
  });

  it("efface le verdict dès que la saisie change", async () => {
    const utilisateur = await monter();

    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));
    await screen.findByText("Accès éprouvé");
    await utilisateur.selectOptions(screen.getByRole("combobox", { name: "Modèle" }), "claude-opus-5");

    expect(screen.queryByText("Accès éprouvé")).toBeNull();
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("refuse l'enregistrement quand l'épreuve échoue entre-temps, et montre pourquoi", async () => {
    const utilisateur = await monter();
    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));
    await screen.findByText("Accès éprouvé");

    poserVerdictEpreuve({ ok: false, motif: "limite", precision: "HTTP 429" });
    await utilisateur.click(screen.getByRole("button", { name: "Enregistrer" }));

    expect(await screen.findByText("Échec de l'épreuve")).toBeInTheDocument();
    expect(screen.getByText(/La limite d'usage du compte est atteinte/)).toBeInTheDocument();
    expect(screen.queryByText(/sans redémarrer Maestro/)).toBeNull();
  });
});

describe("③ le verdict dit son motif et son geste", () => {
  it("un abonnement non connecté nomme la commande qui le connecte", async () => {
    poserVerdictEpreuve({ ok: false, motif: "authentification", precision: "authentication_failed" });
    const utilisateur = await monter();

    await utilisateur.click(screen.getByRole("button", { name: "Éprouver" }));

    expect(await screen.findByText("Échec de l'épreuve")).toBeInTheDocument();
    expect(screen.getByText(/« claude auth login »/)).toBeInTheDocument();
    expect(screen.getByText("authentication_failed").closest("p")).toHaveTextContent(
      "Relevé de l'épreuve : authentication_failed",
    );
    expect(screen.getByRole("button", { name: "Enregistrer" })).toBeDisabled();
  });

  it("un Ollama détecté propose ses modèles, et un serveur muet se dit injoignable", async () => {
    posteOutille();
    poserVerdictEpreuve({ ok: false, motif: "injoignable", precision: "http://localhost:11434/v1" });
    const utilisateur = await monter();

    await utilisateur.click(screen.getByRole("radio", { name: LOCAL }));
    const local = within(carte(LOCAL));
    expect(local.getByText(/Ollama répond sur ce poste/)).toBeInTheDocument();
    expect(local.getByText("Conversation seulement")).toBeInTheDocument();
    expect(local.getByText("Vus sur ce poste : qwen2.5.")).toBeInTheDocument();
    // Sans modèle, rien à éprouver : ce fournisseur n'a pas de défaut.
    expect(local.getByRole("button", { name: "Éprouver" })).toBeDisabled();

    await utilisateur.type(local.getByRole("combobox", { name: "Modèle" }), "qwen2.5");
    await utilisateur.click(local.getByRole("button", { name: "Éprouver" }));

    expect(await local.findByText(/Rien ne répond à cette adresse/)).toBeInTheDocument();
    expect(demandesEprouvees.at(-1)).toEqual({
      fournisseur: "openai",
      acces: "local",
      modele: "qwen2.5",
      adresse: null,
      cle: null,
    });
  });

  it("rend une phrase pour chaque motif, sans relire de texte d'erreur", () => {
    const [abonnement, cle] = ACCES_CLAUDE;
    const [local] = ACCES_OPENAI;
    const echec = (motif: Parameters<typeof phraseDuVerdict>[0]["motif"]) => ({
      ok: false,
      motif,
      precision: "",
    });

    expect(phraseDuVerdict(echec("authentification"), cle)).toBe(
      "La clé est refusée. Vérifiez-la, ou collez-en une autre.",
    );
    expect(phraseDuVerdict(echec("authentification"), local)).toMatch(/demande sans doute une clé/);
    expect(phraseDuVerdict(echec("outil-absent"), abonnement)).toMatch(/« claude » est introuvable/);
    expect(phraseDuVerdict(echec("requete"), local)).toMatch(/pas servi à cette adresse/);
    expect(phraseDuVerdict(echec("facturation"), cle)).toMatch(/plus de crédit/);
  });
});

describe("④ une clé ne se réaffiche jamais", () => {
  it("demande la clé, l'éprouve, l'enregistre, puis vide le champ", async () => {
    const utilisateur = await monter();
    await utilisateur.click(screen.getByRole("radio", { name: CLE_ANTHROPIC }));
    const cle = within(carte(CLE_ANTHROPIC));

    // Sans clé ni au coffre ni dans l'environnement, rien à éprouver.
    expect(cle.getByRole("button", { name: "Éprouver" })).toBeDisabled();

    const champ = cle.getByLabelText("Clé");
    expect(champ).toHaveAttribute("type", "password");
    await utilisateur.type(champ, "sk-ant-essai-123");
    await utilisateur.click(cle.getByRole("button", { name: "Éprouver" }));
    await cle.findByText("Accès éprouvé");
    await utilisateur.click(cle.getByRole("button", { name: "Enregistrer" }));

    await screen.findByText(/sans redémarrer Maestro/);
    expect(demandesEnregistrees.at(-1)?.cle).toBe("sk-ant-essai-123");
    expect(cle.getByLabelText("Clé")).toHaveValue("");
    expect(screen.queryByDisplayValue("sk-ant-essai-123")).toBeNull();
    expect(document.body.textContent).not.toContain("sk-ant-essai-123");
    expect(cle.getByText(/Une clé est enregistrée sur ce poste/)).toBeInTheDocument();
  });
});

describe("⑤ l'environnement qui impose se dit", () => {
  it("nomme les variables, tait la valeur d'une clé, et dit comment rendre la main", async () => {
    poserFournisseurDefaut({
      environnement: [
        { variable: "MAESTRO_PROVIDER", valeur: "openai" },
        { variable: "ANTHROPIC_API_KEY", valeur: null },
      ],
    });

    await monter();

    const note = screen.getByRole("note");
    expect(note).toHaveTextContent("MAESTRO_PROVIDER=openai");
    expect(note).toHaveTextContent("ANTHROPIC_API_KEY (une clé)");
    expect(note).toHaveTextContent(/retirez-les du fichier \.env de Maestro, puis relancez-le/);
  });

  it("ne montre aucune note quand rien ne s'impose", async () => {
    await monter();

    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("⑥ une API coupée se dit", () => {
  it("rend la panne dans la forme de tout écran en panne, au lieu d'une carte vide", async () => {
    poserPanneFournisseurDefaut("/api/fournisseurs/defaut n'a pas répondu");

    render(<FournisseurParDefaut />);

    // La forme de #1217, la même que le catalogue des agents juste dessous ; le
    // bandeau du shell est le seul à dire la panne elle-même.
    expect(
      await screen.findByText(/^Impossible de lire le fournisseur par défaut du poste\./),
    ).toBeInTheDocument();
    expect(screen.queryByRole("radio")).toBeNull();
  });
});
