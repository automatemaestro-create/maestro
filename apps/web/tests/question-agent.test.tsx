/**
 * La **question d'un agent** dans le fil, et le geste qui y répond (#1025,
 * tests différés au lot final de #1019 → #1027 ; docs/05 §2.7.6, §6.17).
 *
 * Le canal existait des deux côtés depuis #1023 ; il n'arrivait nulle part. Ce
 * que ces tests gardent n'est donc pas « la carte s'affiche » mais les quatre
 * promesses sur lesquelles elle a été dessinée, et dont chacune se casserait
 * **sans rien faire échouer** :
 *
 * ① **les gestes sont ceux que l'agent a déclarés.** Pas de `choix`, pas de
 *    boutons : l'écran n'en fabrique aucun, et un choix se répond **en
 *    recopiant son libellé** — c'est le contrat de la route, un index ferait
 *    voyager un numéro dont la signification vivrait dans un autre événement ;
 *
 * ② **ce qui se passera sans réponse est écrit avec la question**, et la phrase
 *    vient de l'API (`attente`) — la recomposer ici en ferait deux rédactions du
 *    même fait, dont une seule connaîtrait la borne. Après la borne, c'est
 *    l'écran qui parle, parce que le canal ne dit rien de plus : la question
 *    reste ouverte et l'agent, lui, est reparti ;
 *
 * ③ **la question reste répondable après la borne.** Une réponse tardive sert
 *    encore (`MemoireArbitrage`, #584), et c'est pour ça que l'API ne ferme pas
 *    la question. Une carte qui se désarmerait à la borne perdrait la seule
 *    chose que ce dispositif a de particulier ;
 *
 * ④ **on répond là où l'on est.** Le fil de l'orchestration porte **toutes** les
 *    questions du projet — il est la porte d'entrée (docs/29) et c'est là que la
 *    cloche achemine —, un aparté `@agent` ne porte que les siennes. La règle
 *    vit dans `lib/questions` et n'est jamais recopiée : c'est le défaut que le
 *    retex du 2026-09-11 avait trouvé sur le cadrage (G10), un panneau qui
 *    disait « aucun » pendant que la question était posée.
 *
 * Et une garde qui n'appartient à aucun critère mais au dépôt : **répondre
 * n'approuve rien** (EF-08). La carte n'offre aucun geste d'approbation, et le
 * compte de la cloche ne mélange les deux familles que dans son **nombre** —
 * « combien de choses m'attendent », jamais « combien de validations ».
 *
 * Réseau débranché comme partout (`tests/setup.ts`) : l'état global est factice,
 * les composants sont les vrais.
 */

import { fireEvent, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { QuestionDansLeFil, QuestionsDuFil } from "@/components/chat/QuestionDansLeFil";
import { resumeArbitrages } from "@/lib/annonces";
import {
  PAGE_DES_QUESTIONS,
  questionEchue,
  questionsDuFil,
  questionsEnAttente,
} from "@/lib/questions";
import { AGENT_ORCHESTRATION } from "@/lib/orchestration";
import { QUESTION_REPONDUE, type Question } from "@/lib/types";

import { questionFactice, rendreAvecEtat } from "./aides";

/** L'instant de référence des tests qui jouent la borne. */
const MAINTENANT = Date.parse("2026-09-20T09:16:00Z");
const AVANT_LA_BORNE = "2026-09-20T09:20:00+00:00";
const APRES_LA_BORNE = "2026-09-20T09:14:00+00:00";

/** La carte d'une question, montée seule — `repondre` est l'observable. */
function carte(question: Question, repondre = vi.fn(async () => {})) {
  rendreAvecEtat(<QuestionDansLeFil question={question} repondre={repondre} />);
  return {
    repondre,
    region: within(
      screen.getByRole("region", { name: `Question de l'agent ${question.agent}` }),
    ),
  };
}

/* ==================================================================== *
 * ① Les gestes sont ceux que l'agent a déclarés
 * ==================================================================== */

describe("la carte d'une question", () => {
  it("rend la question, qui la pose et à propos de quoi", () => {
    const question = questionFactice();
    const { region } = carte(question);

    // Qui demande, en en-tête — c'est la grammaire de la demande de cadrage
    // (#943), et les deux moments sont jumeaux.
    expect(
      region.getByRole("heading", { name: /Question de bdd/ }),
    ).toBeInTheDocument();
    // Son rôle et sa tâche, en une ligne et en place fixe.
    expect(region.getByText("Base de données · Rédiger le schéma de données")).toBeInTheDocument();
    // Et la question elle-même, telle que l'agent l'a écrite.
    expect(region.getByText(question.question)).toBeInTheDocument();
  });

  it("ne fabrique aucun choix que l'agent n'a pas proposé", () => {
    // Le cas nominal du verbe : `choix` est facultatif par contrat, précisément
    // pour que l'agent ne fabrique pas d'options là où il n'en a pas. L'écran
    // n'en invente donc pas non plus — il ne reste que le champ libre.
    const { region } = carte(questionFactice({ choix: [] }));

    expect(region.queryByRole("list")).not.toBeInTheDocument();
    expect(region.getByLabelText("Votre réponse")).toBeInTheDocument();
    // Un seul bouton : celui qui envoie la réponse écrite.
    expect(region.getAllByRole("button")).toHaveLength(1);
  });

  it("rend les choix déclarés en liste, et le champ libre dans le même bloc", () => {
    // Les options, puis « ou une phrase » — jamais un mode à choisir entre
    // « répondre d'un geste » et « répondre en une phrase ». La liste est un
    // `<ul>` : ce sont des options, donc un ensemble, et un lecteur d'écran en
    // annonce le compte.
    const { region } = carte(questionFactice());

    const options = within(region.getByRole("list")).getAllByRole("button");
    expect(options.map((bouton) => bouton.textContent)).toEqual([
      "Postgres",
      "SQLite",
    ]);
    expect(region.getByLabelText("Ou répondez en une phrase")).toBeInTheDocument();
  });

  it("répond un choix en recopiant son libellé, jamais un index", () => {
    // Le contrat de `POST /api/questions/{id}/reponse` : du texte, rien d'autre.
    // Un numéro ferait voyager une signification qui vivrait dans un autre
    // événement — et l'agent, lui, a écrit des libellés.
    const question = questionFactice();
    const { region, repondre } = carte(question);

    return userEvent
      .click(within(region.getByRole("list")).getByRole("button", { name: "Postgres" }))
      .then(() => {
        expect(repondre).toHaveBeenCalledWith(question.question_id, "Postgres");
      });
  });

  it("n'envoie rien tant que la réponse écrite est vide", async () => {
    const { region, repondre } = carte(questionFactice({ choix: [] }));

    const envoyer = region.getByRole("button", { name: "Répondre" });
    expect(envoyer).toBeDisabled();

    await userEvent.type(region.getByLabelText("Votre réponse"), "   ");
    // Des blancs ne sont pas une réponse : elle n'apprendrait rien à l'agent,
    // qui reprendrait sur son hypothèse en croyant le contraire (422 côté API).
    expect(envoyer).toBeDisabled();

    await userEvent.type(region.getByLabelText("Votre réponse"), "Postgres");
    expect(envoyer).toBeEnabled();
    await userEvent.click(envoyer);
    expect(repondre).toHaveBeenCalledWith(
      "t2:9f1c0a4bd3",
      expect.stringContaining("Postgres"),
    );
  });

  it("montre le refus de l'API et rend la main pour réessayer", async () => {
    // Une réponse perdue en silence serait le pire des cas : l'agent attend
    // toujours, et la personne croit avoir répondu.
    const repondre = vi.fn(async () => {
      throw new Error("question déjà répondue");
    });
    const { region } = carte(questionFactice({ choix: [] }), repondre);

    await userEvent.type(region.getByLabelText("Votre réponse"), "Postgres");
    await userEvent.click(region.getByRole("button", { name: "Répondre" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "question déjà répondue",
    );
    expect(region.getByRole("button", { name: "Répondre" })).toBeEnabled();
  });

  it("n'offre aucun geste d'approbation — répondre n'autorise rien (EF-08)", () => {
    // La frontière avec `PanneauValidations`, à l'écran : là-bas on approuve ou
    // on refuse un **acte**, ici on écrit du **texte**. Un bouton « Approuver »
    // sur cette carte ferait croire qu'une réponse rend un acte licite, alors
    // qu'un outil classé `ask` reste refusé sans canal d'arbitrage.
    const { region } = carte(questionFactice({ choix: [] }));

    for (const bouton of region.getAllByRole("button")) {
      expect(bouton.textContent).not.toMatch(/approuv|refus/i);
    }
  });
});

/* ==================================================================== *
 * ② et ③ La borne : ce qui arrivera, puis ce qui est arrivé
 * ==================================================================== */

describe("ce que la carte dit de l'attente", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(MAINTENANT);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("rend telle quelle la phrase que l'API compose, borne comprise", () => {
    // Elle porte déjà le délai (docs/05 §6.17) : la recomposer ici ferait deux
    // rédactions du même fait, dont une seule suivrait le réglage du moteur.
    const question = questionFactice({ echeance: AVANT_LA_BORNE });
    const { region } = carte(question);

    // La première lettre passe en capitale, sans toucher au texte : la phrase de
    // l'API est écrite pour être lue au milieu d'une ligne de journal.
    expect(region.getByText(/Sans réponse d'ici 240 s/)).toBeInTheDocument();
    expect(region.queryByText(/reparti/i)).not.toBeInTheDocument();
  });

  it("dit l'hypothèse quand le canal n'a pas composé de phrase", () => {
    // Une question servie par un backend qui ne porte pas `attente` : l'écran ne
    // se tait pas pour autant — il dit ce qui se passera, sans le délai qu'il ne
    // connaît pas.
    const { region } = carte(questionFactice({ attente: "", echeance: "" }));

    expect(region.getByText(/Sans réponse, l'agent reprendra/)).toBeInTheDocument();
    expect(region.getByText(/je pars sur SQLite/)).toBeInTheDocument();
  });

  it("dit que l'agent est reparti une fois la borne passée, et reste répondable", () => {
    // Le troisième critère de #1025. L'état ne tient pas à la couleur seule
    // (docs/30 §1.6) : il est **écrit**, en badge et en toutes lettres.
    const question = questionFactice({ echeance: APRES_LA_BORNE });
    const { region, repondre } = carte(question);

    expect(region.getByText("Reparti sans réponse")).toBeInTheDocument();
    expect(region.getByText(/l'agent est reparti sur son hypothèse/)).toBeInTheDocument();
    expect(region.getByText(/Répondre sert encore/)).toBeInTheDocument();

    // Et le geste n'est pas désarmé : une réponse tardive est retrouvée par le
    // même appel rejoué (#584). Une carte qui se fermerait ici perdrait la seule
    // chose que ce dispositif a de particulier.
    //
    // `fireEvent` et non `userEvent` : les faux timers sont en place pour tenir
    // l'horloge de l'écran, et la frappe simulée de `userEvent` attend sur eux.
    fireEvent.click(
      within(region.getByRole("list")).getByRole("button", { name: "Postgres" }),
    );
    expect(repondre).toHaveBeenCalledWith(question.question_id, "Postgres");
  });
});

/* ==================================================================== *
 * ④ Qu'est-ce qui attend, et quel fil le concerne (lib/questions)
 * ==================================================================== */

describe("la règle partagée (lib/questions)", () => {
  it("ne retient que ce qui attend encore, la plus ancienne d'abord", () => {
    // L'ordre est la moitié du signal : celle qui dort depuis dix minutes est
    // celle dont l'agent est déjà reparti sans elle.
    const file = questionsEnAttente([
      questionFactice({ question_id: "b", horodatage: "2026-09-20T09:12:00Z" }),
      questionFactice({
        question_id: "repondue",
        statut: QUESTION_REPONDUE,
        reponse: "Postgres",
        horodatage: "2026-09-20T09:00:00Z",
      }),
      questionFactice({ question_id: "a", horodatage: "2026-09-20T09:05:00Z" }),
    ]);

    expect(file.map((question) => question.question_id)).toEqual(["a", "b"]);
  });

  it("range en dernier une question sans horodatage plutôt qu'en tête", () => {
    // Une chaîne vide remonterait en tête d'un tri de chaînes, et ferait passer
    // pour la plus ancienne une question dont on ne sait rien.
    const file = questionsEnAttente([
      questionFactice({ question_id: "sans", horodatage: "" }),
      questionFactice({ question_id: "datee", horodatage: "2026-09-20T09:05:00Z" }),
    ]);

    expect(file.map((question) => question.question_id)).toEqual(["datee", "sans"]);
  });

  it("porte toutes les questions dans le fil de l'orchestration, et seulement les siennes dans un aparté", () => {
    // Le fil global est la porte d'entrée et c'est là que la cloche achemine :
    // une question de `bdd` qui n'apparaîtrait que dans l'aparté avec `bdd`
    // n'atteindrait personne — il faudrait savoir qu'elle existe pour aller la
    // chercher, ce que le badge existe précisément pour éviter.
    const questions = [
      questionFactice({ question_id: "bdd-1", agent: "bdd" }),
      questionFactice({ question_id: "dev-1", agent: "developpeur" }),
    ];

    expect(
      questionsDuFil(questions, AGENT_ORCHESTRATION, AGENT_ORCHESTRATION).map(
        (question) => question.question_id,
      ),
    ).toEqual(["bdd-1", "dev-1"]);
    expect(
      questionsDuFil(questions, "bdd", AGENT_ORCHESTRATION).map(
        (question) => question.question_id,
      ),
    ).toEqual(["bdd-1"]);
  });

  it("ne dit l'agent reparti que sur une date, jamais sur la phrase", () => {
    // Le verdict se rend sur `echeance`, une **date** servie par le canal.
    // Reconnaître un nombre dans `attente` serait juger du texte par un motif,
    // ce que le dépôt refuse (#746) ; recopier la borne côté navigateur ferait
    // deux supports pour un réglage du moteur.
    const question = questionFactice({ echeance: APRES_LA_BORNE });
    expect(questionEchue(question, MAINTENANT)).toBe(true);
    expect(questionEchue({ ...question, echeance: AVANT_LA_BORNE }, MAINTENANT)).toBe(
      false,
    );

    // Trois replis, et tous rendent « pas encore échue » : annoncer que l'agent
    // est reparti alors qu'on ne sait pas l'heure serait affirmer un fait faux,
    // tandis que le taire une seconde ne coûte que la seconde.
    expect(questionEchue(question, null)).toBe(false);
    expect(questionEchue({ ...question, echeance: "" }, MAINTENANT)).toBe(false);
    expect(questionEchue({ ...question, echeance: "bientôt" }, MAINTENANT)).toBe(false);
  });

  it("nomme la page où l'on répond par son libellé de menu, jamais par un chemin", () => {
    // Le renvoi suit sa page si elle déménage, et ne s'allume pas vers une page
    // absente (#191). C'est ce que la cloche utilise pour acheminer.
    expect(PAGE_DES_QUESTIONS).toBe("Chat");
  });
});

/* ==================================================================== *
 * La pile du pied de fil, et le compte de la cloche
 * ==================================================================== */

describe("les questions au pied d'un fil", () => {
  it("empile une carte par question, sans en replier aucune", () => {
    // Deux questions d'une même tâche peuvent attendre ensemble (§6.17), et en
    // replier une derrière un « voir les autres » ferait exactement ce que le
    // canal existe pour éviter : une demande posée que personne ne voit.
    rendreAvecEtat(
      <QuestionsDuFil
        questions={[
          questionFactice({ question_id: "t2:aaaa", agent: "bdd" }),
          questionFactice({ question_id: "t2:bbbb", agent: "developpeur" }),
        ]}
        repondre={vi.fn(async () => {})}
      />,
    );

    expect(screen.getAllByRole("region", { name: /Question de l'agent/ })).toHaveLength(2);
  });

  it("ne rend rien du tout quand rien n'attend", () => {
    const { container } = rendreAvecEtat(
      <QuestionsDuFil questions={[]} repondre={vi.fn(async () => {})} />,
    );

    expect(container).toBeEmptyDOMElement();
  });

  it("entre dans le compte de ce qui m'attend, sans se confondre avec les validations", () => {
    // Le seul endroit où les deux files se rejoignent est le **nombre** : la
    // pastille répond à « combien de choses m'attendent » (#322). La phrase, elle,
    // les dit séparément — répondre à une question et approuver un acte sensible
    // ne demandent ni la même disponibilité ni la même personne.
    expect(resumeArbitrages(2, 1, 1)).toBe(
      "2 validations, 1 brief et 1 question en attente",
    );
    expect(resumeArbitrages(0, 0, 3)).toBe("3 questions en attente");
    // Sans question, la phrase est celle d'avant ce lot, à l'octet près.
    expect(resumeArbitrages(1, 1)).toBe("1 validation et 1 brief en attente");
    expect(resumeArbitrages(0, 0, 0)).toBeNull();
  });
});
