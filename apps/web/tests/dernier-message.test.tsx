/**
 * **Le fil montre qu'on a décroché** (#877 — parti pris 4 de la veille #820).
 *
 * La règle de suivi de #695 ne change pas : le fil recolle en bas à chaque
 * incrément **sauf** si le lecteur est remonté lire. Ce qui manquait est qu'elle
 * ne se voyait pas — remonté, rien ne disait qu'une réponse continuait en bas, et
 * aucun geste n'y ramenait autrement qu'en défilant. Ce fichier garde le geste
 * qui la rend visible, sur les **deux** surfaces qui montent `Conversation`
 * (`/chat` et l'onglet Chat d'une fiche agent) : une garde qui ne couvrirait
 * que la première n'en garderait que la moitié (règle de `composeur.test.tsx`).
 *
 * Couvre :
 *
 * ① **le geste n'existe que décroché** — absent tant que la vue suit, rendu dès
 *    que `suit` passe à faux, reparti dès qu'elle est revenue en bas ;
 * ② **le cliquer redescend et réarme le suivi** — `collerEnBas` sur l'ascenseur
 *    du cadre, et le geste disparaît puisque le fil suit à nouveau ;
 * ③ **sa forme** — `Bouton contour/neutre`, icône du jeu + libellé, le plancher
 *    de 24 px du socle (`CIBLE_MINIMALE`), l'apparition gardée par
 *    `motion-reduce:`, et sa place : à quai juste au-dessus du formulaire,
 *    aligné à droite de la colonne ;
 * ④ **l'état est posé sur le CHANGEMENT de suivi** — aucun rendu du fil par
 *    cran de molette, exactement un par bascule.
 *
 * ⚠ **Aucune géométrie ici** (#308). jsdom ne calcule ni hauteur ni défilement :
 * `estEnBas` est donc piloté par une **doublure** (le ticket le demande en
 * toutes lettres), et la mesure de l'ascenseur est posée à la main comme
 * `composeur.test.tsx` pose celle du champ. Ce que ③ observe est le **contrat de
 * mise en page tel qu'il est écrit** — les utilitaires présents dans le DOM —,
 * jamais son effet : l'effet est le rôle de `/banc-mise-en-page`, et son verdict
 * se consigne dans la PR.
 *
 * Chaque sonde est **prouvée sur un échantillon fautif avant de balayer**
 * (méthode de #534/#537/#539) : le verdict de forme sur un geste écrit sans le
 * socle, et le compteur de rendus sur un fil qui rendrait à chaque `scroll` —
 * celui-là précisément parce qu'un compteur muet rendrait « aucun rendu par
 * cran » avec les mots de « le garde-fou tient ».
 */

import {
  Profiler,
  useEffect,
  useState,
  type ProfilerOnRenderCallback,
} from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { Conversation } from "@/components/Conversation";
import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { CIBLE_MINIMALE } from "@/components/Primitives";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";

import {
  agentFactice,
  filAssistanceCourant,
  messageFactice,
  poserFilAssistance,
  rendreAvecEtat,
} from "./aides";

/**
 * Où en est le lecteur, du point de vue de la doublure. `vi.hoisted` parce que
 * la fabrique d'un `vi.mock` est hissée au-dessus du corps du fichier : une
 * constante ordinaire y serait dans sa zone morte.
 */
const lecture = vi.hoisted(() => ({ enBas: true }));

// `estEnBas` seul est doublé — `ascenseurDe` reste le **vrai**, et c'est lui
// qui désigne à quoi le fil s'abonne. Le doubler aussi aurait donné un test qui
// dit vrai d'un câblage imaginaire.
vi.mock("@/lib/defilement", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/defilement")>();
  return { ...reel, estEnBas: () => lecture.enBas };
});

/**
 * L'ascenseur que `ascenseurDe` va désigner : `document.body`, rendu défilant
 * pour l'occasion (voir `beforeEach`).
 *
 * ⚠ Ce n'est **pas** un détail de confort. `ascenseurDe` remonte les ancêtres
 * jusqu'à un `overflow-y` calculé, puis retombe sur `document.scrollingElement`
 * — que **jsdom n'implémente pas** (mesuré : `undefined`, donc `null` rendu).
 * Sans un ancêtre réellement défilant, le fil ne s'abonne donc à rien du tout
 * sous jsdom, et un test qui dispatcherait un `scroll` sur l'élément racine
 * n'exercerait rien en rendant un vert. Un `overflowY` posé sur `body` remet
 * les choses dans l'ordre du produit, où l'ascenseur est le conteneur du
 * `Shell` et jamais la fenêtre.
 */
const ascenseur = () => document.body;

/** Un cran de molette — le seul événement que le fil écoute. */
function cransDeMolette(nombre = 1): void {
  for (let cran = 0; cran < nombre; cran++) fireEvent.scroll(ascenseur());
}

/**
 * Fait dire à l'ascenseur ce que le navigateur mesurerait, et retient où
 * `collerEnBas` a posé la vue. jsdom ne fait aucune mise en page : `scrollTop`
 * y est un puits sans fond et `scrollHeight` vaut zéro, donc poser la mesure est
 * la seule façon d'observer le geste (même technique que `simulerLaMesure` dans
 * `composeur.test.tsx`).
 */
function surveillerLAscenseur(hauteur = 4242): { posee: number | null } {
  const vue = { posee: null as number | null };
  Object.defineProperty(ascenseur(), "scrollHeight", {
    configurable: true,
    get: () => hauteur,
  });
  Object.defineProperty(ascenseur(), "scrollTop", {
    configurable: true,
    get: () => 0,
    set: (valeur: number) => {
      vue.posee = valeur;
    },
  });
  mesuresPosees.push("scrollHeight", "scrollTop");
  return vue;
}

/** Les propriétés posées à la main sur l'élément racine, à rendre après coup. */
const mesuresPosees: string[] = [];

/** Les fragments posés à la main dans le document (échantillons fautifs). */
const fixtures: HTMLElement[] = [];

beforeEach(() => {
  // Posé **avant** le montage : `ascenseurDe` est résolu une fois, dans l'effet
  // de montage du fil (« il ne change pas sous les pieds du fil »).
  ascenseur().style.overflowY = "auto";
});

afterEach(() => {
  lecture.enBas = true;
  ascenseur().style.overflowY = "";
  for (const nom of mesuresPosees.splice(0)) {
    // Une propriété **propre** retirée rend la main à l'accesseur du prototype :
    // le nœud survit aux tests, sa mesure ne doit pas leur survivre.
    delete (ascenseur() as unknown as Record<string, unknown>)[nom];
  }
  for (const fixture of fixtures.splice(0)) fixture.remove();
});

// ---------------------------------------------------------------------------
// Les deux surfaces qui montent le fil
// ---------------------------------------------------------------------------

type Surface = {
  nom: string;
  monter: () => void;
  interlocuteur: string;
};

const SURFACES: Surface[] = [
  {
    nom: "le chat global (/chat)",
    monter: () => {
      rendreAvecEtat(<PageChat />, {
        agents: [
          agentFactice({ nom: "dev" }),
          agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION }),
        ],
      });
    },
    interlocuteur: INTERLOCUTEUR_ORCHESTRATION,
  },
  {
    nom: "l'onglet Chat d'une fiche agent",
    monter: () => {
      rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="chat" />);
    },
    interlocuteur: "dev",
  },
];

/** Le geste, tel qu'un lecteur le nomme — ou `null` s'il n'est pas rendu. */
const gesteDeRetour = () =>
  screen.queryByRole("button", { name: "Dernier message" });

// ---------------------------------------------------------------------------
// ③ La sonde de forme, et l'échantillon fautif qui la prouve
// ---------------------------------------------------------------------------

/**
 * Ce qui manque au geste pour tenir ses promesses — vide, il les tient toutes.
 *
 * Un verdict en liste plutôt qu'une grappe d'assertions : une faute est rendue
 * **avec son motif**, et le même verdict passe sur l'échantillon fautif avant de
 * passer sur le produit.
 */
function verdictDuGeste(bouton: HTMLElement | null): string[] {
  if (bouton === null) return ["aucun bouton « Dernier message »"];
  const fautes: string[] = [];
  const classes = Array.from(bouton.classList);

  // Le socle, et rien d'écrit à la main : `contour`/`neutre` sont des choix
  // nommés du `Bouton` (#245), le plancher de 24 px vient de `BOUTON_SOCLE`.
  if (!classes.includes("border") || !classes.includes("border-bord-fort")) {
    fautes.push("pas la variante `contour` (border + border-bord-fort)");
  }
  if (!classes.includes("text-texte-secondaire")) {
    fautes.push("pas le ton `neutre` (text-texte-secondaire)");
  }
  // Résolu par la constante et jamais recopié : c'est la règle de #832, et elle
  // fait que le jour où le plancher change, ce test suit au lieu de mentir.
  if (!classes.includes(CIBLE_MINIMALE)) {
    fautes.push(`cible sans plancher de 24 px (${CIBLE_MINIMALE} absent)`);
  }
  if (bouton.querySelector('svg[aria-hidden="true"]') === null) {
    fautes.push("aucune icône du jeu, ou une icône laissée dans l'arbre a11y");
  }
  if ((bouton.textContent ?? "").trim() !== "Dernier message") {
    fautes.push("le libellé n'est pas « Dernier message »");
  }

  // Le mouvement se garde (#537, WCAG 2.2 §2.3.3) : une apparition animée, et
  // sa neutralisation sous `prefers-reduced-motion`, dans la même chaîne.
  const bouge = classes.filter(
    (c) =>
      /^(transition(-\[[^\]]*\]|-[a-z]+)?|animate-[a-z-]+)$/.test(c) &&
      c !== "transition-none" &&
      c !== "animate-none",
  );
  if (bouge.length === 0) fautes.push("aucune apparition animée");
  else if (!classes.includes("motion-reduce:transition-none")) {
    fautes.push(`apparition non gardée : ${bouge.join(", ")}`);
  }

  // La place. L'enveloppe accroche le geste au bord **haut** du formulaire
  // (`bottom-full`) quelle que soit la hauteur de celui-ci, qui grandit avec le
  // brouillon (#726) — et `end-0` l'aligne à droite de la colonne, en logique
  // et non en `right-0` comme le `ms-auto` du rail.
  const enveloppe = bouton.parentElement;
  const utilites = enveloppe === null ? [] : Array.from(enveloppe.classList);
  for (const utilite of ["absolute", "bottom-full", "end-0"]) {
    if (!utilites.includes(utilite)) {
      fautes.push(`l'enveloppe ne porte pas \`${utilite}\``);
    }
  }
  // Et c'est le formulaire à quai qui la porte : un `sticky` frère se
  // pinnerait sur la même ligne que lui et le recouvrirait.
  const quai = enveloppe?.parentElement ?? null;
  if (quai === null || quai.tagName !== "FORM") {
    fautes.push("le geste n'est pas enfant du formulaire");
  } else if (
    !quai.classList.contains("sticky") ||
    !quai.classList.contains("bottom-16")
  ) {
    fautes.push("le formulaire qui le porte n'est plus à quai");
  }
  return fautes;
}

/**
 * Le geste tel qu'on l'écrirait **sans le socle** : un bouton nu posé dans le
 * flux au-dessus du composeur, avec une transition que rien ne garde. Personne
 * ne l'a jamais écrit — c'est l'échantillon fautif, et sans lui « aucune faute »
 * serait vrai pour deux raisons, la bonne et une sonde qui regarde ailleurs.
 */
function gesteDAvant877(): HTMLElement {
  const form = document.createElement("form");
  fixtures.push(form);
  form.className = "flex flex-col gap-2";
  form.innerHTML =
    '<div class="flex justify-end">' +
    '<button type="button" class="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-annexe transition">' +
    "Dernier message</button></div>";
  document.body.appendChild(form);
  return form.querySelector("button")!;
}

describe("③ la sonde de forme, prouvée avant de servir", () => {
  it("nomme tout ce qui manque au geste écrit sans le socle", () => {
    const fautes = verdictDuGeste(gesteDAvant877());
    expect(fautes).toEqual(
      expect.arrayContaining([
        expect.stringContaining("variante `contour`"),
        expect.stringContaining("ton `neutre`"),
        expect.stringContaining(CIBLE_MINIMALE),
        expect.stringContaining("icône"),
        expect.stringContaining("apparition non gardée"),
        expect.stringContaining("`absolute`"),
        expect.stringContaining("`bottom-full`"),
        expect.stringContaining("`end-0`"),
        expect.stringContaining("plus à quai"),
      ]),
    );
  });

  it("ne se tait pas non plus sur un geste absent", () => {
    expect(verdictDuGeste(null)).toEqual(["aucun bouton « Dernier message »"]);
  });
});

// ---------------------------------------------------------------------------
// ① → ③ Le geste, sur ses deux surfaces
// ---------------------------------------------------------------------------

describe.each(SURFACES)(
  "le geste « Dernier message » sur $nom",
  ({ monter, interlocuteur }) => {
    /** Un fil qui a de quoi être quitté des yeux. */
    const poserUnFilLu = () =>
      poserFilAssistance({
        messages: [
          messageFactice({ contenu: "Bonjour" }),
          messageFactice({ auteur: interlocuteur, contenu: "Je regarde." }),
        ],
      });

    it("① n'existe que décroché : absent en bas, rendu remonté, reparti revenu", () => {
      poserUnFilLu();
      monter();
      // Le fil suit : rien à montrer. C'est la moitié qui compte le plus — un
      // geste permanent occuperait la bande au-dessus du composeur pour ne rien
      // apprendre, ce que docs/30 §4 refuse (« une place se gagne »).
      expect(gesteDeRetour()).toBeNull();

      lecture.enBas = false;
      cransDeMolette();
      expect(gesteDeRetour()).toBeInTheDocument();

      lecture.enBas = true;
      cransDeMolette();
      expect(gesteDeRetour()).toBeNull();
    });

    it("② ramène au bas du fil et réarme le suivi", () => {
      const vue = surveillerLAscenseur(4242);
      poserUnFilLu();
      monter();
      lecture.enBas = false;
      cransDeMolette();

      fireEvent.click(gesteDeRetour()!);
      // Redescendu : `collerEnBas` pose la vue au bout de l'ascenseur du cadre.
      expect(vue.posee).toBe(4242);
      // Et réarmé : le geste s'en va **sans attendre** le `scroll` que le
      // recollement provoquera — celui-là est asynchrone dans un vrai
      // navigateur, et un défilement doux le rendrait à contretemps.
      expect(gesteDeRetour()).toBeNull();
    });

    it("② l'envoi d'un message le retire lui aussi", async () => {
      // Écrire, c'est reprendre le fil (#695) : le suivi se rétablit, donc le
      // geste n'a plus lieu d'être. C'est le second appelant de `reglerLeSuivi`,
      // et celui qu'un état posé à part aurait laissé diverger.
      const envoyer = vi.fn().mockResolvedValue(undefined);
      poserFilAssistance({ envoyer });
      monter();
      lecture.enBas = false;
      cransDeMolette();
      expect(gesteDeRetour()).toBeInTheDocument();

      const champ = screen.getByLabelText(`Message à ${interlocuteur}`);
      fireEvent.change(champ, { target: { value: "Où en es-tu ?" } });
      fireEvent.submit(champ.closest("form")!);
      await waitFor(() => expect(envoyer).toHaveBeenCalled());
      expect(gesteDeRetour()).toBeNull();
    });

    it("③ tient sa forme et sa place", () => {
      poserUnFilLu();
      monter();
      lecture.enBas = false;
      cransDeMolette();

      const fautes = verdictDuGeste(gesteDeRetour());
      expect(fautes, `\n${fautes.join("\n")}\n`).toEqual([]);
    });
  },
);

// ---------------------------------------------------------------------------
// ④ L'état est posé sur le CHANGEMENT de suivi
// ---------------------------------------------------------------------------

/** Un compteur de rendus : ce que React rapporte des commits du sous-arbre. */
function sondeDeRendus() {
  const commits: string[] = [];
  const onRender: ProfilerOnRenderCallback = (_id, phase) => {
    commits.push(phase);
  };
  return { commits, onRender };
}

/**
 * Un fil qui **rendrait à chaque `scroll`** — l'état de suivi relu par cran de
 * molette au lieu d'être posé sur son changement. C'est la version que #695
 * avait évitée en faisant de `suit` une `ref`, et celle que #877 aurait pu
 * réintroduire en ajoutant un état à côté.
 */
function FilQuiRendAChaqueCran() {
  const [, setCrans] = useState(0);
  useEffect(() => {
    const surDefilement = () => setCrans((n) => n + 1);
    const cadre = ascenseur();
    cadre.addEventListener("scroll", surDefilement);
    return () => cadre.removeEventListener("scroll", surDefilement);
  }, []);
  return <p>un fil</p>;
}

describe("④ l'affichage est posé sur le changement de suivi, jamais par cran", () => {
  it("la sonde compte bien un rendu par cran sur un fil qui rend à chaque scroll", () => {
    // La sonde avant ce qu'elle mesure : un compteur muet — Profiler mal câblé,
    // événement qui n'arrive pas jusqu'au fil — rendrait « aucun rendu par
    // cran » avec les mots de « le garde-fou tient ».
    const { commits, onRender } = sondeDeRendus();
    render(
      <Profiler id="fautif" onRender={onRender}>
        <FilQuiRendAChaqueCran />
      </Profiler>,
    );
    commits.length = 0;
    cransDeMolette(5);
    expect(commits).toHaveLength(5);
  });

  it("le fil ne rend rien par cran, et exactement un rendu par bascule", async () => {
    const { commits, onRender } = sondeDeRendus();
    poserFilAssistance({ messages: [messageFactice({ contenu: "Bonjour" })] });
    // Le composant seul, et non un écran : ce que le critère borne est le rendu
    // **du fil**, et profiler la page entière compterait aussi ce qu'elle fait
    // d'autre.
    rendreAvecEtat(
      <Profiler id="fil" onRender={onRender}>
        <Conversation
          fil={filAssistanceCourant()}
          interlocuteur="dev"
          libelle="Chat avec dev"
          titre="Chat avec dev"
        />
      </Profiler>,
    );
    await screen.findByLabelText("Message à dev");
    commits.length = 0;

    // Cinq crans qui ne font pas changer d'avis : rien n'est posé, rien n'est
    // rendu. C'est le cœur du critère — une réponse s'écrit pendant qu'on
    // défile, et un rendu du fil par cran serait payé au pire moment.
    cransDeMolette(5);
    expect(commits).toEqual([]);
    expect(gesteDeRetour()).toBeNull();

    // Le lecteur décroche : **un** rendu, et le geste paraît.
    lecture.enBas = false;
    cransDeMolette();
    expect(commits).toHaveLength(1);
    expect(gesteDeRetour()).toBeInTheDocument();

    // Cinq crans de plus, toujours décroché : toujours rien.
    commits.length = 0;
    cransDeMolette(5);
    expect(commits).toEqual([]);

    // Il revient en bas : **un** rendu, et le geste s'en va.
    lecture.enBas = true;
    cransDeMolette();
    expect(commits).toHaveLength(1);
    expect(gesteDeRetour()).toBeNull();
  });
});
