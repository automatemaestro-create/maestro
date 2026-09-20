/**
 * **Un run qui se termine l'annonce, et remet son livrable** (#928, lot 7 de
 * #921) — le filet que ce lot a différé au lot final (#929).
 *
 * Le constat qui l'ouvre est le plus net du retex du 2026-09-11 (G1) : un run de
 * 53 minutes et 12,51 $ s'est terminé sans prévenir personne, et son livrable
 * n'a été trouvé qu'en allant regarder le disque. Ce qui est gardé ici est donc
 * ce qui, en se défaisant, ramènerait ce silence :
 *
 * ① **l'annonce est dérivée du PERSISTÉ** — `lib/issueRun` croise les
 *    `executions` rechargées par le REST avec les `run_id` persistés des
 *    messages (#268), jamais le flux d'événements, qui part vide à chaque
 *    chargement. C'est le troisième critère du ticket — *un run terminé pendant
 *    qu'on regardait ailleurs se retrouve* — et il se perdrait en une ligne ;
 * ② **« aucun livrable » s'écrit** — un run sans racine ne rend pas `null` en
 *    silence : il rend sa raison, et les deux codes ne se confondent pas ;
 * ③ **deux gestes, jamais un seul** — « Ouvrir le dossier » quand le poste le
 *    permet, « Copier le chemin » **toujours**. Le second n'est pas le lot de
 *    consolation du premier (parti pris 4 de la veille) ;
 * ④ **la capacité, jamais la plateforme** — `lib/poste` teste si la fonction
 *    est là, jamais où il tourne. C'est ENF-12 (docs/35 §2.5), et c'est ce qui
 *    fait que la même page sert dans un onglet et dans la fenêtre ;
 * ⑤ **la cloche marque d'un POINT, pas d'un second chiffre** — la pastille
 *    répond « combien de choses m'attendent » (#322), une fin de run n'attend
 *    rien, et deux compteurs côte à côte obligeraient à en faire la somme.
 *
 * ⚠ Ce qui n'est **pas** mesuré ici : des pixels (#308). La place des deux
 * gestes sur la ligne du chemin, la respiration du `pe-2`, le repli à 320 px —
 * tout cela est du ressort du banc et de la relecture visuelle. Ce qui est
 * compté ici est ce que le DOM dit : les faits rendus, les gestes présents, et
 * ce qui se passe quand on les actionne.
 */

import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { CentreNotifications } from "@/components/CentreNotifications";
import { AnnonceIssueRun } from "@/components/runs/AnnonceIssueRun";
import {
  aDesIssuesNonLues,
  CLE_ISSUES_VUES,
  issueDuRun,
  issuesDuFil,
  issuesRecentes,
  libelleIssue,
  raisonSansLivrable,
  SANS_LIVRABLE_HORS_CADRE,
  SANS_LIVRABLE_HORS_PROJET,
  type IssueRun,
} from "@/lib/issueRun";
import {
  EXECUTION_ANNULEE,
  EXECUTION_ECHEC,
  EXECUTION_EN_COURS,
  EXECUTION_TERMINEE,
} from "@/lib/types";

import {
  messageFactice,
  poserFilAssistance,
  projetFactice,
  rendreAvecEtat,
  runFactice,
  validationFactice,
} from "./aides";

const PROJET = projetFactice({ id: "prj-1", nom: "Dépensio", racine: "D:/w/depensio" });

/** Un run soldé de ce projet — le cas nominal, avec son heure de fin. */
function runSolde(partiel: Partial<ReturnType<typeof runFactice>> = {}) {
  return runFactice({
    run_id: "run-1",
    objectif: "Ajouter la pagination",
    statut: EXECUTION_TERMINEE,
    nb_taches: 4,
    cout_usd: 12.51,
    projet_id: PROJET.id,
    fin: "2026-09-11T14:53:00Z",
    ...partiel,
  });
}

/** L'issue telle que les deux surfaces la reçoivent. */
function issueFactice(partiel: Partial<ReturnType<typeof runFactice>> = {}): IssueRun {
  const issue = issueDuRun(runSolde(partiel), PROJET);
  if (issue === null) throw new Error("ce run n'est pas soldé");
  return issue;
}

// ===========================================================================
// 1. Ce qu'un run rend en finissant (lib/issueRun)
// ===========================================================================

describe("l'issue d'un run (lib/issueRun)", () => {
  it("ne rend rien tant que le run n'a pas fini", () => {
    // ① Une annonce qui paraîtrait sur un run en cours dirait « c'est fini »
    // d'un travail qui continue — le seul mensonge pire que le silence de G1.
    expect(
      issueDuRun(runSolde({ statut: EXECUTION_EN_COURS, fin: null }), PROJET),
    ).toBeNull();
  });

  it("rend le verdict, le chemin et le projet d'un run abouti", () => {
    const issue = issueFactice();
    expect(issue.abouti).toBe(true);
    // ② Le livrable, c'est la **racine du projet** du run : c'est là que le
    // travail atterrit dans les deux régimes du moteur (fusion, écriture en
    // place). Le résumé d'un run ne porte qu'un `projet_id`.
    expect(issue.racine).toBe(PROJET.racine);
    expect(issue.projet).toBe(PROJET.nom);
    expect(issue.sansLivrable).toBeNull();
  });

  it("distingue « fini » de « abouti »", () => {
    // Un run soldé n'a pas forcément réussi, et l'annonce ne distingue que ces
    // deux-là : le détail se lit sur la vue du run.
    expect(issueFactice({ statut: EXECUTION_ECHEC }).abouti).toBe(false);
    expect(issueFactice({ statut: EXECUTION_ANNULEE }).abouti).toBe(false);
    expect(libelleIssue(issueFactice())).toBe("Run terminé");
    expect(libelleIssue(issueFactice({ statut: EXECUTION_ECHEC }))).toBe(
      "Run en échec",
    );
    expect(libelleIssue(issueFactice({ statut: EXECUTION_ANNULEE }))).toBe(
      "Run interrompu",
    );
  });

  it("dit POURQUOI il n'y a pas de chemin, et ne confond pas les deux raisons", () => {
    // ② « Aucun livrable » s'écrit (parti pris 2). Et les deux codes n'appellent
    // pas le même geste : un run hors projet n'a jamais eu de racine, un run
    // d'un autre projet en a une qu'il n'est pas à nous d'afficher ici.
    const horsProjet = issueFactice({ projet_id: null });
    expect(horsProjet.racine).toBeNull();
    expect(horsProjet.sansLivrable).toBe(SANS_LIVRABLE_HORS_PROJET);

    const horsCadre = issueFactice({ projet_id: "prj-autre" });
    expect(horsCadre.racine).toBeNull();
    expect(horsCadre.sansLivrable).toBe(SANS_LIVRABLE_HORS_CADRE);

    // Deux phrases distinctes, et jamais `null` : une issue sans chemin a
    // toujours quelque chose à afficher à la place.
    expect(raisonSansLivrable(horsProjet)).not.toBe(
      raisonSansLivrable(horsCadre),
    );
    expect(raisonSansLivrable(horsProjet)).not.toBeNull();
    expect(raisonSansLivrable(horsCadre)).not.toBeNull();
    expect(raisonSansLivrable(issueFactice())).toBeNull();
  });
});

describe("les runs qu'un fil a ouverts (issuesDuFil)", () => {
  const messages = [
    messageFactice({ contenu: "Ajoute la pagination" }),
    messageFactice({ auteur: "agent", contenu: "C'est parti", run_id: "run-1" }),
    messageFactice({ auteur: "agent", contenu: "toujours run-1", run_id: "run-1" }),
  ];

  it("annonce une fin par run, et jamais une par message", () => {
    const issues = issuesDuFil(messages, [runSolde()], PROJET);
    expect(issues.map((issue) => issue.execution.run_id)).toEqual(["run-1"]);
  });

  it("ne dit rien d'un run que ce fil n'a pas demandé", () => {
    // Le rattachement vient de `message.run_id` (#268) : un run lancé depuis la
    // page Runs n'a pas été demandé ici, et la cloche est là pour lui.
    const issues = issuesDuFil(messages, [runSolde({ run_id: "run-9" })], PROJET);
    expect(issues).toEqual([]);
  });

  it("ne dit rien d'un run qui n'a pas fini", () => {
    const issues = issuesDuFil(
      messages,
      [runSolde({ statut: EXECUTION_EN_COURS, fin: null })],
      PROJET,
    );
    expect(issues).toEqual([]);
  });
});

describe("les fins les plus récentes (issuesRecentes)", () => {
  it("met devant ce qui vient d'arriver, et garde ce qui n'a pas d'heure", () => {
    const issues = issuesRecentes(
      [
        runSolde({ run_id: "vieux", fin: "2026-09-01T08:00:00Z" }),
        runSolde({ run_id: "sans-heure", fin: null }),
        runSolde({ run_id: "neuf", fin: "2026-09-19T08:00:00Z" }),
        runFactice({ run_id: "en-cours", statut: EXECUTION_EN_COURS }),
      ],
      PROJET,
      5,
    );
    // Un run soldé sans horodatage passe derrière, il ne disparaît pas : le
    // perdre serait taire une fin, ce que ce lot existe pour empêcher.
    expect(issues.map((issue) => issue.execution.run_id)).toEqual([
      "neuf",
      "vieux",
      "sans-heure",
    ]);
  });

  it("s'arrête à la limite qu'on lui donne", () => {
    const issues = issuesRecentes(
      ["a", "b", "c"].map((id) => runSolde({ run_id: id })),
      PROJET,
      2,
    );
    expect(issues).toHaveLength(2);
  });
});

describe("le repère de lecture de la cloche", () => {
  it("n'allume la marque que pour une fin postérieure au repère", () => {
    const issues = [issueFactice()];
    expect(aDesIssuesNonLues(issues, "")).toBe(true);
    expect(aDesIssuesNonLues(issues, "2026-09-11T14:52:00Z")).toBe(true);
    expect(aDesIssuesNonLues(issues, "2026-09-11T14:53:00Z")).toBe(false);
  });

  it("ne l'allume jamais pour une fin sans horodatage", () => {
    // Le seul endroit où l'on préfère le silence, et il est borné à ce cas : on
    // ne saurait pas comparer cette fin au repère, donc on ne saurait pas
    // éteindre la marque après l'avoir allumée.
    expect(aDesIssuesNonLues([issueFactice({ fin: null })], "")).toBe(false);
  });
});

// ===========================================================================
// 2. L'annonce (components/runs/AnnonceIssueRun)
// ===========================================================================

describe("l'annonce de fin", () => {
  afterEach(() => {
    delete window.maestro;
  });

  it("dit l'issue en toutes lettres, pas seulement par une couleur", () => {
    // docs/30 §3.2 : l'état ne tient jamais à la couleur seule. Le libellé le
    // dit, et l'icône qui l'accompagne est décorative.
    render(<AnnonceIssueRun issue={issueFactice({ statut: EXECUTION_ECHEC })} />);
    expect(screen.getByText("Run en échec")).toBeInTheDocument();
  });

  it("porte l'heure de la FIN, et non celle du lancement", () => {
    // C'est le défaut qui a écarté la variante B du choix de #928 : rangée sous
    // la bulle qui avait lancé le run, l'annonce portait l'heure du départ — un
    // run de 53 minutes annonçait sa fin à l'heure où il avait commencé.
    const { container } = render(<AnnonceIssueRun issue={issueFactice()} />);
    const heure = container.querySelector("time");
    expect(heure).toHaveAttribute("dateTime", "2026-09-11T14:53:00Z");
    expect(heure?.textContent).not.toBe("");
  });

  it("rend le chemin du livrable, et les deux gestes qui vont avec", async () => {
    // ③ Dans un onglet, « Ouvrir le dossier » n'existe pas et « Copier le
    // chemin » reste : le repli demandé par les notes techniques du ticket.
    const ecrireTexte = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText: ecrireTexte } });
    render(<AnnonceIssueRun issue={issueFactice()} />);

    expect(screen.getByText(PROJET.racine)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Ouvrir le dossier/ }),
    ).not.toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: /Copier le chemin/ }),
    );
    expect(ecrireTexte).toHaveBeenCalledWith(PROJET.racine);
    // Chacun **dit ce qu'il a fait** : un geste sans retour ne se distingue pas
    // d'une page figée.
    expect(await screen.findByRole("status")).toHaveTextContent("Chemin copié");
  });

  it("offre d'ouvrir le dossier dès que le poste sait le faire", async () => {
    // ④ La capacité, jamais la plateforme : rien ici ne lit `userAgent` ni un
    // drapeau de coque — le pont est là, donc le geste l'est (`lib/poste`).
    const ouvrir = vi.fn().mockResolvedValue(true);
    window.maestro = { ouvrirDossier: ouvrir };
    render(<AnnonceIssueRun issue={issueFactice()} />);

    await userEvent.click(
      screen.getByRole("button", { name: /Ouvrir le dossier/ }),
    );
    expect(ouvrir).toHaveBeenCalledWith(PROJET.racine);
  });

  it("dit quand l'ouverture a échoué plutôt que de ne rien faire", async () => {
    window.maestro = { ouvrirDossier: vi.fn().mockResolvedValue(false) };
    render(<AnnonceIssueRun issue={issueFactice()} />);
    await userEvent.click(
      screen.getByRole("button", { name: /Ouvrir le dossier/ }),
    );
    expect(await screen.findByRole("status")).toHaveTextContent(
      "Dossier introuvable",
    );
  });

  it("écrit « aucun livrable » à la place qu'il occuperait", () => {
    // ② Parti pris 2, d'après la colonne `Artifacts` d'un run GitHub Actions,
    // qui affiche `–` plutôt que rien. La ligne est **toujours** rendue.
    render(<AnnonceIssueRun issue={issueFactice({ projet_id: null })} />);
    expect(screen.getByText(/Livrable/)).toBeInTheDocument();
    expect(screen.getByText(/aucun projet/)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /Copier le chemin/ }),
    ).not.toBeInTheDocument();
  });

  it("ne fait pas du chemin un lien", () => {
    // Un `file://` serait refusé par la coque (qui ne navigue que sur l'origine
    // locale) comme par le navigateur (qui bloque `file:` depuis une page
    // http) — refus consigné dans la veille de #928. Le chemin reste du texte
    // sélectionnable, et ce sont les deux gestes qui l'emportent ailleurs.
    render(<AnnonceIssueRun issue={issueFactice()} />);
    expect(screen.getByText(PROJET.racine).closest("a")).toBeNull();
  });
});

// ===========================================================================
// 3. Les deux surfaces qui annoncent
// ===========================================================================

describe("le fil qui a demandé le travail", () => {
  it("annonce la fin du run qu'il a ouvert, avec son livrable", async () => {
    poserFilAssistance({
      messages: [
        messageFactice({ contenu: "Ajoute la pagination" }),
        messageFactice({
          auteur: "agent",
          contenu: "C'est parti",
          run_id: "run-1",
        }),
      ],
    });
    rendreAvecEtat(<PageChat />, { executions: [runSolde()] }, PROJET);

    expect(await screen.findByText("Run terminé")).toBeInTheDocument();
    expect(screen.getByText(PROJET.racine)).toBeInTheDocument();
  });

  it("ne l'annonce pas tant que le run tourne", async () => {
    poserFilAssistance({
      messages: [
        messageFactice({ auteur: "agent", contenu: "C'est parti", run_id: "run-1" }),
      ],
    });
    rendreAvecEtat(
      <PageChat />,
      { executions: [runSolde({ statut: EXECUTION_EN_COURS, fin: null })] },
      PROJET,
    );

    await screen.findByText("C'est parti");
    expect(screen.queryByText("Run terminé")).not.toBeInTheDocument();
  });

  it("la retrouve alors que rien n'est passé en temps réel", async () => {
    // ① Le troisième critère du ticket, et le seul qui ne se voie pas à
    // l'écran : `evenements` est **vide** — c'est l'état d'un chargement de
    // page —, et l'annonce est là quand même, parce qu'elle sort du persisté.
    poserFilAssistance({
      messages: [
        messageFactice({ auteur: "agent", contenu: "C'est parti", run_id: "run-1" }),
      ],
    });
    rendreAvecEtat(
      <PageChat />,
      { evenements: [], executions: [runSolde()] },
      PROJET,
    );

    expect(await screen.findByText("Run terminé")).toBeInTheDocument();
  });
});

describe("la cloche", () => {
  afterEach(() => {
    window.localStorage.removeItem(CLE_ISSUES_VUES);
  });

  it("rappelle les fins de run, et le dit dans son nom accessible", () => {
    rendreAvecEtat(<CentreNotifications />, { executions: [runSolde()] }, PROJET);
    expect(
      screen.getByRole("button", { name: /du travail terminé/ }),
    ).toBeInTheDocument();
  });

  it("range les fins derrière la file d'arbitrage, sans les y ajouter", () => {
    // ⑤ « 2 à valider, et du travail terminé » se lit dans l'ordre où l'on agit,
    // et la somme n'est jamais demandée au lecteur.
    rendreAvecEtat(
      <CentreNotifications />,
      { executions: [runSolde()], validations: [validationFactice()] },
      PROJET,
    );
    const nom = screen
      .getByRole("button", { name: /Notifications/ })
      .getAttribute("aria-label");
    expect(nom).toMatch(/validation en attente.*du travail terminé/);
  });

  it("montre l'annonce entière une fois le panneau ouvert", async () => {
    rendreAvecEtat(<CentreNotifications />, { executions: [runSolde()] }, PROJET);
    await userEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    const panneau = await screen.findByRole("dialog");
    expect(within(panneau).getByText("Run terminé")).toBeInTheDocument();
    expect(within(panneau).getByText(PROJET.racine)).toBeInTheDocument();
  });

  it("acquitte sur la fin la plus récente, et non sur l'horloge", async () => {
    // Une fin qui arriverait pendant que le panneau est ouvert reste neuve —
    // ce qu'un « maintenant » aurait avalé.
    rendreAvecEtat(<CentreNotifications />, { executions: [runSolde()] }, PROJET);
    await userEvent.click(screen.getByRole("button", { name: /Notifications/ }));
    await waitFor(() =>
      expect(window.localStorage.getItem(CLE_ISSUES_VUES)).toBe(
        "2026-09-11T14:53:00Z",
      ),
    );
    expect(
      screen.queryByRole("button", { name: /du travail terminé/ }),
    ).not.toBeInTheDocument();
  });

  it("se tait quand tout est déjà lu", () => {
    window.localStorage.setItem(CLE_ISSUES_VUES, "2026-09-11T14:53:00Z");
    rendreAvecEtat(<CentreNotifications />, { executions: [runSolde()] }, PROJET);
    expect(
      screen.queryByRole("button", { name: /du travail terminé/ }),
    ).not.toBeInTheDocument();
  });
});
