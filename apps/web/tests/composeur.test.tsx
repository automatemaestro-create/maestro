/**
 * **Le composeur et le pourtour du fil** (#728, lot 5 de #722) — les tests que
 * les lots 2 à 4 ont différés ici (docs/10 §5.1).
 *
 * Trois de ces lots portent sur de la **géométrie** — la hauteur d'un champ qui
 * grandit, un ascenseur qui s'efface, un bloc collant —, c'est-à-dire ce que
 * jsdom ne calcule pas (#308). Ce fichier ne prétend donc mesurer aucun pixel :
 * il garde le **contrat tel qu'il est écrit** (les utilitaires posés, la
 * hauteur que le code pose, les octets de la feuille) et laisse l'effet au
 * banc (`/banc-mise-en-page`), dont le verdict se consigne dans la PR.
 *
 * Chaque sonde est **prouvée sur un échantillon fautif avant de balayer**
 * (méthode de #534/#537/#539) : l'échantillon est le composeur d'**avant** le
 * chantier — trois rectangles voisins, le raccourci dans le placeholder, la
 * poignée `resize-y` — tel que #722 et la veille #724 l'ont relevé. Sans cette
 * moitié, une absence serait vraie pour deux raisons, la bonne et le fait que
 * la sonde regarde ailleurs.
 *
 * Le composeur étant monté par **deux** surfaces — le chat global (`/chat`) et
 * l'onglet Chat d'une fiche agent —, ce qui le concerne est joué sur les deux :
 * un test qui ne le couvrirait que sur `/chat` ne garderait que la moitié.
 *
 * Couvre :
 *
 * ① **le champ grandit puis plafonne** (#726) — la hauteur suit le contenu
 *    quand il déborde, revient au plancher quand il rentre, et le plafond est
 *    au CSS ; la poignée a disparu ;
 * ② **le composeur est un bloc** (#726) — le cadre est le contrôle, l'envoi se
 *    tient dedans, et la réserve du bouton flottant est passée de côté
 *    (verticale, jamais un vide à droite) ;
 * ③ **l'envoi et le joindre restent atteignables** (#726/#727) — dans le
 *    formulaire, et au clavier depuis le champ ;
 * ④ **le raccourci reste lisible pendant la saisie** (#726) — il décrit le
 *    champ au lieu de vivre dans un placeholder qui s'efface ;
 * ⑤ **aucune fonctionnalité de #482 n'est perdue** — dépôt, collage d'une
 *    image, panneau des gestes, envoi par identifiant, sur les deux surfaces ;
 * ⑥ **l'ascenseur discret** (#725) — vérifié sur les **octets** de
 *    `globals.css` (technique de `contraste.test.ts`), et la moitié JS de la
 *    frontière (`lib/ascenseur`, câblé dans le `Shell`) ;
 * ⑦ **la colonne de propriétés** de `/chat` est collante **et** bornée, comme
 *    celle de `/couts` que `sobriete.test.tsx` garde déjà.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { ATTRIBUT_DEFILEMENT, ecouterDefilement } from "@/lib/ascenseur";
import { marquerGuideVu } from "@/lib/guide";
import {
  AGENT_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";

import {
  agentFactice,
  poserFilAssistance,
  poserProjetActif,
  rendreAvecEtat,
} from "./aides";
import { ECRANS, monterEcran, peuplerEtat } from "./ecrans";

// Le réseau, pour de bon : le téléversement d'une source est le seul appel que
// le composeur fait de lui-même (`lib/useSourcesComposees`), et l'écran monté
// sous le `Shell` (⑥) rencontre les lectures que `ecrans-reseau` bouchonne.
// `importOriginal` garde `ErreurSource` **la** classe du module (même piège que
// `fil-sources.test.tsx`).
vi.mock("@/lib/api", async (importOriginal) => {
  const reel = await importOriginal<typeof import("@/lib/api")>();
  const { mocksApi } = await import("./ecrans-reseau");
  return { ...reel, ...mocksApi(), televerserSources: vi.fn() };
});

const { televerserSources } = await import("@/lib/api");
const televerse = vi.mocked(televerserSources);

const ICI = path.dirname(fileURLToPath(import.meta.url));
const lireSource = (relatif: string) =>
  readFileSync(path.join(ICI, "..", relatif), "utf8");

// ---------------------------------------------------------------------------
// Les deux surfaces qui montent le composeur
// ---------------------------------------------------------------------------

type Surface = {
  nom: string;
  /** Monte la surface avec le fil que le test a posé. */
  monter: () => void;
  /** Celui à qui l'on parle — tous les libellés du composeur en dérivent. */
  interlocuteur: string;
  /** Le nom de la section de conversation : la cible du glisser-déposer. */
  section: string;
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
    section: "Chat global",
  },
  {
    nom: "l'onglet Chat d'une fiche agent",
    monter: () => {
      rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="chat" />);
    },
    interlocuteur: "dev",
    section: "Chat avec dev",
  },
];

/** La zone de saisie du composeur, nommée d'après l'interlocuteur. */
function zoneDeSaisie(interlocuteur: string): HTMLTextAreaElement {
  return screen.getByLabelText(`Message à ${interlocuteur}`);
}

/** Le formulaire du composeur — celui qui porte la zone de saisie. */
function composeurDe(champ: HTMLElement): HTMLFormElement {
  const form = champ.closest("form");
  if (form === null) throw new Error("la zone de saisie n'est dans aucun <form>");
  return form;
}

/** Le nom accessible d'un contrôle, tel que le test le lit sans dépendre de sa forme. */
function nomDe(element: Element): string {
  return (
    element.getAttribute("aria-label") ?? element.textContent?.trim() ?? ""
  );
}

// ---------------------------------------------------------------------------
// Les sondes — et l'échantillon fautif qui les prouve
// ---------------------------------------------------------------------------

/** Les fragments posés à la main dans le document, à retirer après chaque test. */
const fixtures: HTMLElement[] = [];

/**
 * Le composeur d'**avant** #726, tel que le ticket parent et la veille #724
 * l'ont relevé dans `Conversation.tsx` : le champ bordé lui-même, « Envoyer » à
 * côté (`flex items-end gap-2`), la réserve `pe-14` du bouton flottant, le
 * raccourci dans le placeholder, la poignée `resize-y`, et « Joindre des
 * sources… » en troisième bloc sous le formulaire.
 */
function composeurDAvant(): { form: HTMLFormElement; champ: HTMLTextAreaElement } {
  const form = document.createElement("form");
  // Posé à la main, donc hors du ménage de Testing Library : retiré par
  // l'`afterEach` du bloc qui s'en sert, sans quoi ses boutons doubleraient
  // ceux du composeur réel dans tous les tests suivants.
  fixtures.push(form);
  form.innerHTML =
    '<div class="flex items-end gap-2 pe-14">' +
    '<textarea rows="2" aria-label="Message à dev" ' +
    'placeholder="Écrire à dev… (Entrée envoie, Maj+Entrée saute une ligne)" ' +
    'class="w-full resize-y rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm"></textarea>' +
    '<button type="submit">Envoyer</button>' +
    "</div>" +
    '<div class="space-y-2"><button type="button" aria-expanded="false">Joindre des sources…</button></div>';
  document.body.appendChild(form);
  return { form, champ: form.querySelector("textarea")! };
}

/**
 * Ce que le champ porte de son contrat de croissance (#726, parti pris 3) :
 * le **plafond** (`max-h-*`), le **défilement interne** au-delà, et la
 * **poignée** de redimensionnement (`resize-*`). Le plafond est au CSS et non
 * dans le code : c'est lui qui l'emporte sur la hauteur que `ajusterLaHauteur`
 * pose, et laisse alors `overflow-y-auto` défiler.
 */
function contratDeCroissance(champ: HTMLElement) {
  const classes = Array.from(champ.classList);
  return {
    plafond: classes.find((c) => /^max-h-/.test(c)) ?? null,
    defilement: classes.includes("overflow-y-auto"),
    poignee: classes.find((c) => /^resize-/.test(c)) ?? null,
  };
}

/**
 * Le **cadre** d'un champ : le premier élément, de lui-même vers la racine, qui
 * porte l'utilitaire `border`. Avant #726 c'était le champ lui-même — son
 * rectangle à lui, d'où les trois rectangles ; depuis, c'est le cadre à deux
 * étages qui porte `CLASSE_CONTROLE`, et le champ n'a plus de bord propre.
 */
function cadreDe(champ: HTMLElement): HTMLElement {
  let courant: HTMLElement | null = champ;
  while (courant !== null) {
    if (courant.classList.contains("border")) return courant;
    courant = courant.parentElement;
  }
  throw new Error("aucun cadre : ni le champ ni un ancêtre ne porte `border`");
}

/**
 * La réserve **latérale** du bouton flottant (#123) : un `pe-*` posé dans le
 * composeur, qui laissait 56 px de vide à droite de l'envoi. Depuis #726 la
 * réserve est **verticale** (`bottom-16` sur le formulaire, et la bande
 * couverte qui le suit) — aucun `pe-*` n'a plus d'objet ici.
 */
function reserveLaterale(form: HTMLElement): string[] {
  return Array.from(form.querySelectorAll<HTMLElement>("*"))
    .concat(form)
    .flatMap((element) =>
      Array.from(element.classList).filter((c) => /^pe-\d+$/.test(c)),
    );
}

/**
 * Où vit le raccourci clavier (#726, parti pris 4) : dans le **placeholder**,
 * qui s'efface au premier caractère, ou dans la **description** du champ
 * (`aria-describedby`), qui reste. Les deux sont rendus pour qu'un test dise
 * lequel — une absence seule ne prouverait rien.
 */
function ouVitLeRaccourci(champ: HTMLElement): {
  placeholder: boolean;
  description: string | null;
} {
  const RACCOURCI = /Entrée envoie/;
  const ids = (champ.getAttribute("aria-describedby") ?? "").split(/\s+/);
  const description = ids
    .filter((id) => id !== "")
    .map((id) => document.getElementById(id)?.textContent ?? "")
    .find((texte) => RACCOURCI.test(texte));
  return {
    placeholder: RACCOURCI.test(champ.getAttribute("placeholder") ?? ""),
    description: description ?? null,
  };
}

/**
 * Fait dire au champ ce que le navigateur mesurerait : `scrollHeight` (la
 * hauteur du contenu) et `clientHeight` (la boîte). jsdom rend zéro aux deux
 * (#308), et c'est précisément pourquoi `ajusterLaHauteur` n'y pose rien —
 * poser la mesure est la seule façon d'exercer sa décision.
 */
function simulerLaMesure(
  champ: HTMLElement,
  mesure: { contenu: number; boite: number },
): void {
  Object.defineProperty(champ, "scrollHeight", {
    configurable: true,
    get: () => mesure.contenu,
  });
  Object.defineProperty(champ, "clientHeight", {
    configurable: true,
    get: () => mesure.boite,
  });
}

/** Un fichier déposable, tel qu'un navigateur le livrerait. */
function fichierFactice(nom: string, type = "text/markdown"): File {
  return new File(["# Cahier\n"], nom, { type });
}

/** Le dépôt de fichiers sur une cible, glisser-déposer compris. */
function glisserSur(cible: HTMLElement, fichiers: File[]): void {
  const transfert = { files: fichiers, items: [], types: ["Files"] };
  fireEvent.dragOver(cible, { dataTransfer: transfert });
  fireEvent.drop(cible, { dataTransfer: transfert });
}

describe("les sondes du composeur, prouvées sur le composeur d'avant #726", () => {
  afterEach(() => {
    for (const fixture of fixtures.splice(0)) fixture.remove();
  });

  it("reconnaissent le champ à hauteur fixe et sa poignée", () => {
    const { champ } = composeurDAvant();
    expect(contratDeCroissance(champ)).toEqual({
      plafond: null,
      defilement: false,
      poignee: "resize-y",
    });
  });

  it("voient que le champ est son propre rectangle et que l'envoi est à côté", () => {
    const { form, champ } = composeurDAvant();
    const envoyer = within(form).getByRole("button", { name: "Envoyer" });
    // Le cadre est le champ lui-même : « Envoyer » n'est pas dedans.
    expect(cadreDe(champ)).toBe(champ);
    expect(cadreDe(champ).contains(envoyer)).toBe(false);
  });

  it("trouvent la réserve latérale du bouton flottant", () => {
    const { form } = composeurDAvant();
    expect(reserveLaterale(form)).toEqual(["pe-14"]);
  });

  it("trouvent le raccourci dans le placeholder, et nulle part ailleurs", () => {
    const { champ } = composeurDAvant();
    expect(ouVitLeRaccourci(champ)).toEqual({
      placeholder: true,
      description: null,
    });
  });
});

// ---------------------------------------------------------------------------
// ① → ⑤ Le composeur, sur ses deux surfaces
// ---------------------------------------------------------------------------

beforeEach(() => {
  televerse.mockReset();
  televerse.mockResolvedValue({
    sources: [{ id: "tv-1", type: "fichier", nom: "capture.png", taille: 9 }],
    total_octets: 9,
  });
});

describe.each(SURFACES)("le composeur sur $nom", ({ monter, interlocuteur, section }) => {
  // ── ① le champ grandit puis plafonne ────────────────────────────────────
  describe("① le champ grandit puis plafonne (#726)", () => {
    it("part de deux lignes, sans hauteur posée", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      // `rows` est la hauteur de départ — le plancher est laissé au navigateur,
      // sans pixel recopié — et rien n'est posé tant que rien ne déborde.
      expect(champ.rows).toBe(2);
      expect(champ.style.height).toBe("");
    });

    it("prend la hauteur de son contenu quand il déborde, et la rend quand il rentre", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      const mesure = { contenu: 120, boite: 52 };
      simulerLaMesure(champ, mesure);

      fireEvent.change(champ, { target: { value: "une\nligne\nde\nplus" } });
      // Joué avant la peinture (`useLayoutEffect`) : la hauteur suit le contenu
      // dès la frappe, sans image du champ trop court au contenu déjà défilé.
      expect(champ.style.height).toBe("120px");

      // Le contenu rentre à nouveau : le champ repart de sa hauteur de départ,
      // il ne garde pas la plus haute qu'il ait atteinte.
      mesure.contenu = 40;
      fireEvent.change(champ, { target: { value: "x" } });
      expect(champ.style.height).toBe("");
    });

    it("plafonne au CSS, défile en interne au-delà, et n'a plus de poignée", () => {
      monter();
      // Le plafond n'est pas dans le code : `max-h-*` l'emporte sur la hauteur
      // posée, et c'est `overflow-y-auto` qui prend le relais — l'ascenseur
      // rendu est alors le discret du socle (⑥), sans une ligne à lui.
      expect(contratDeCroissance(zoneDeSaisie(interlocuteur))).toEqual({
        plafond: "max-h-48",
        defilement: true,
        poignee: "resize-none",
      });
    });
  });

  // ── ② le composeur est un bloc ──────────────────────────────────────────
  describe("② le composeur est un bloc (#726)", () => {
    it("fait du cadre le contrôle, et y tient l'envoi", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      const cadre = cadreDe(champ);
      // Le champ n'a plus de rectangle à lui : c'est le cadre à deux étages
      // qui porte le bord, le fond et l'anneau de focus (`CLASSE_CONTROLE`).
      expect(cadre).not.toBe(champ);
      expect(Array.from(cadre.classList)).toEqual(
        expect.arrayContaining(["border-bord", "focus-within:border-bord-fort"]),
      );
      const envoyer = within(composeurDe(champ)).getByRole("button", {
        name: "Envoyer",
      });
      expect(cadre.contains(envoyer)).toBe(true);
    });

    it("ne réserve plus de vide à droite : la réserve du flottant est verticale", () => {
      monter();
      const form = composeurDe(zoneDeSaisie(interlocuteur));
      expect(reserveLaterale(form)).toEqual([]);
      // À quai, le formulaire s'arrête au-dessus de la bande du bouton flottant
      // (#123), et l'élément qui le suit couvre cette bande : rien ne se termine
      // sous le flottant, et aucune bulle ne défile dans la bande. La géométrie
      // (64 px, 8 px d'air) appartient au banc ; le contrat, lui, est ici.
      expect(Array.from(form.classList)).toEqual(
        expect.arrayContaining(["sticky", "bottom-16"]),
      );
      const bande = form.nextElementSibling;
      expect(bande).not.toBeNull();
      expect(bande!.getAttribute("aria-hidden")).toBe("true");
      expect(Array.from(bande!.classList)).toEqual(
        expect.arrayContaining(["sticky", "bottom-0", "h-16"]),
      );
    });
  });

  // ── ③ l'envoi et le joindre restent atteignables ────────────────────────
  describe("③ l'envoi et le joindre restent atteignables (#726/#727)", () => {
    it("les tient tous deux dans le formulaire du composeur", () => {
      monter();
      const form = composeurDe(zoneDeSaisie(interlocuteur));
      expect(
        within(form).getByRole("button", { name: "Joindre des sources…" }),
      ).not.toBeDisabled();
      // L'envoi n'est barré que faute de contenu — pas hors de portée.
      expect(within(form).getByRole("button", { name: "Envoyer" })).toBeDisabled();
    });

    it("les atteint au clavier depuis le champ, sans quitter le composeur", async () => {
      const utilisateur = userEvent.setup();
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      const form = composeurDe(champ);
      // Un contenu d'abord : un bouton d'envoi désactivé est sauté par Tab, et
      // ce qu'on garde ici est qu'un message prêt à partir s'envoie au clavier.
      await utilisateur.type(champ, "Bonjour");
      champ.focus();

      const atteints: string[] = [];
      for (let pas = 0; pas < 4; pas++) {
        await utilisateur.tab();
        const actif = document.activeElement;
        if (!(actif instanceof HTMLElement) || !form.contains(actif)) break;
        atteints.push(nomDe(actif));
      }
      expect(atteints).toEqual(
        expect.arrayContaining(["Envoyer", "Joindre des sources…"]),
      );
    });
  });

  // ── ④ le raccourci reste lisible pendant la saisie ──────────────────────
  describe("④ le raccourci reste lisible pendant la saisie (#726)", () => {
    it("décrit le champ au lieu de vivre dans le placeholder", async () => {
      const utilisateur = userEvent.setup();
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      await utilisateur.type(champ, "Bonjour");

      // Pendant la saisie — c'est-à-dire à l'instant où il servait, et où le
      // placeholder s'était effacé —, le raccourci est toujours là, dans le
      // cadre, et un lecteur d'écran l'entend avec le champ.
      const { placeholder, description } = ouVitLeRaccourci(champ);
      expect(placeholder).toBe(false);
      expect(description).toBe("Entrée envoie · Maj+Entrée saute une ligne");
      const raccourci = within(cadreDe(champ)).getByText(description!);
      expect(raccourci).toBeInTheDocument();
      // Et le placeholder ne dit plus que l'interlocuteur.
      expect(champ.getAttribute("placeholder")).toBe(`Écrire à ${interlocuteur}…`);
    });
  });

  // ── ⑤ aucune fonctionnalité de #482 n'est perdue ────────────────────────
  describe("⑤ rien de #482 n'est perdu", () => {
    it("joint un fichier glissé sur la conversation, et l'envoie par son identifiant", async () => {
      const envoyer = vi.fn().mockResolvedValue(undefined);
      poserFilAssistance({ envoyer });
      monter();

      glisserSur(screen.getByLabelText(section), [fichierFactice("cahier.md")]);
      const jointes = await screen.findByRole("list", {
        name: "Sources jointes au message",
      });
      expect(within(jointes).getByText("cahier.md")).toBeInTheDocument();

      // Une source seule est un message légitime (#482) : l'envoi s'ouvre sans
      // texte, et ce qui part est l'identifiant rendu par le téléversement,
      // jamais les octets ni le nom.
      const envoi = screen.getByRole("button", { name: "Envoyer" });
      expect(envoi).not.toBeDisabled();
      fireEvent.click(envoi);
      await waitFor(() => expect(envoyer).toHaveBeenCalled());
      expect(televerse).toHaveBeenCalledTimes(1);
      expect(envoyer).toHaveBeenCalledWith("", [{ type: "fichier", id: "tv-1" }]);
    });

    it("joint une image collée dans le champ, sans toucher au texte collé", async () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);

      // Le geste jumeau du glisser-déposer, et le seul par lequel une capture
      // arrive sans passer par un fichier du disque (#482).
      fireEvent.paste(champ, {
        clipboardData: {
          files: [fichierFactice("capture.png", "image/png")],
          types: ["Files"],
        },
      });
      const jointes = await screen.findByRole("list", {
        name: "Sources jointes au message",
      });
      expect(within(jointes).getByText("capture.png")).toBeInTheDocument();

      // Un collage de **texte** n'est pas touché : `files` est alors vide, et
      // le brouillon reste ce qu'il est.
      fireEvent.change(champ, { target: { value: "du texte" } });
      fireEvent.paste(champ, { clipboardData: { files: [], types: ["text/plain"] } });
      expect(champ).toHaveValue("du texte");
      expect(within(jointes).getAllByRole("listitem")).toHaveLength(1);
    });

    it("ouvre les trois gestes de dépôt derrière « Joindre des sources… »", async () => {
      const utilisateur = userEvent.setup();
      monter();
      await utilisateur.click(
        screen.getByRole("button", { name: "Joindre des sources…" }),
      );
      // Les gestes de #482 sont tous là — fichiers, dossier, adresse —, quelle
      // que soit la place d'où on les ouvre.
      expect(
        screen.getByRole("button", { name: "Choisir un dossier…" }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Adresse à lire")).toBeInTheDocument();
      expect(
        screen.getByRole("button", { name: "Ajouter l'adresse" }),
      ).toBeInTheDocument();
    });
  });
});

// ---------------------------------------------------------------------------
// ⑥ L'ascenseur discret, sur les octets de globals.css (#725)
// ---------------------------------------------------------------------------

/**
 * Le corps du premier bloc dont l'en-tête matche `entete` **et** ouvre une
 * accolade juste après — sans quoi `[data-theme="sombre"]` matcherait d'abord la
 * ligne `@custom-variant` de la feuille, et le bloc rendu serait celui du
 * voisin. Accolades équilibrées : la règle vit trois niveaux sous `@layer`.
 */
function corpsDuBloc(source: string, entete: RegExp): string | null {
  const global = new RegExp(entete.source, "g");
  let debut: RegExpExecArray | null;
  while ((debut = global.exec(source)) !== null) {
    const apres = source.slice(debut.index + debut[0].length);
    const ouverture = /^\s*\{/.exec(apres);
    if (ouverture === null) continue;
    const depart = debut.index + debut[0].length + ouverture[0].length;
    let profondeur = 1;
    for (let i = depart; i < source.length; i++) {
      if (source[i] === "{") profondeur++;
      else if (source[i] === "}" && --profondeur === 0) {
        return source.slice(depart, i);
      }
    }
    return null;
  }
  return null;
}

/** Les règles de premier niveau d'un corps de bloc : leur prélude et leur corps. */
function reglesDe(corps: string): { prelude: string; corps: string }[] {
  const regles: { prelude: string; corps: string }[] = [];
  let i = 0;
  while (i < corps.length) {
    const ouverture = corps.indexOf("{", i);
    if (ouverture === -1) break;
    const prelude = corps.slice(i, ouverture).trim();
    let profondeur = 0;
    let fin = -1;
    for (let j = ouverture; j < corps.length; j++) {
      if (corps[j] === "{") profondeur++;
      else if (corps[j] === "}" && --profondeur === 0) {
        fin = j;
        break;
      }
    }
    if (fin === -1) break;
    regles.push({ prelude, corps: corps.slice(ouverture + 1, fin) });
    i = fin + 1;
  }
  return regles;
}

const selecteursDe = (prelude: string) => prelude.split(",").map((s) => s.trim());

/** Les déclarations d'une règle feuille, nom → valeur. */
function declarationsDe(corps: string): ReadonlyMap<string, string> {
  const table = new Map<string, string>();
  for (const [, nom, valeur] of corps.matchAll(/([\w-]+)\s*:\s*([^;{}]+);/g)) {
    table.set(nom, valeur.trim());
  }
  return table;
}

const compteFondus = (s: string) =>
  (s.match(/transition\s*:[^;{}]*scrollbar-color/g) ?? []).length;
const compteWebkit = (s: string) => (s.match(/::-webkit-scrollbar/g) ?? []).length;

/** Le token est-il porté par les deux blocs de palette (technique de `contraste.test.ts`) ? */
function declareDansLesDeuxThemes(feuille: string, token: string): boolean {
  const motif = new RegExp(`--${token}\\s*:`);
  const clair = corpsDuBloc(feuille, /:root\s*,\s*\[data-theme="clair"\]/) ?? "";
  const sombre = corpsDuBloc(feuille, /\[data-theme="sombre"\]/) ?? "";
  return motif.test(clair) && motif.test(sombre);
}

/**
 * Le verdict rendu sur une feuille : la liste de ce qui manque à l'ascenseur
 * discret pour tenir ses promesses (`globals.css`, #725). Vide, la feuille les
 * tient toutes. C'est **lui** que la feuille réelle subit, et lui qu'on prouve
 * d'abord sur des échantillons fautifs.
 *
 * `attribut` est celui que `lib/ascenseur` pose : la règle vit des deux côtés
 * d'une frontière — le JS qui marque, le CSS qui lit —, et rien d'autre ne les
 * tient d'accord (même leçon que #830 pour le signal « page prête »).
 */
function verdictAscenseur(source: string, attribut: string): string[] {
  const feuille = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const fautes: string[] = [];
  const marque = `[${attribut}]`;

  // Discrète, jamais absente : `none` retirerait l'information qu'une surface
  // bornée continue sous le pli (#306).
  if (/scrollbar-width\s*:\s*none/.test(feuille)) {
    fautes.push("scrollbar-width: none — la barre est absente au lieu d'être discrète");
  }

  // `@layer base`, et ce n'est pas un rangement : hors couche, une règle sur `*`
  // l'emporterait sur tout utilitaire Tailwind — le `transition` ci-dessous
  // aurait éteint le `transition-[width]` de la barre latérale.
  const couche = corpsDuBloc(feuille, /@layer\s+base/);
  if (couche === null) {
    fautes.push("aucun bloc @layer base — la règle sur `*` l'emporterait sur les utilitaires");
    return fautes;
  }
  const standard = corpsDuBloc(couche, /@supports\s*\(scrollbar-color\s*:\s*auto\)/);
  const webkit = corpsDuBloc(couche, /@supports\s+not\s*\(scrollbar-color\s*:\s*auto\)/);
  if (standard === null) {
    fautes.push("la règle standard ne vit pas sous @layer base > @supports (scrollbar-color: auto)");
  }
  if (webkit === null) {
    fautes.push("le repli WebKit ne vit pas sous @layer base > @supports not (scrollbar-color: auto)");
  }
  if (standard === null || webkit === null) return fautes;

  // Le moteur standard : au repos rien, à l'éveil le token qui identifie un contrôle.
  const regles = reglesDe(standard);
  const repos = regles.find((r) => selecteursDe(r.prelude).join(",") === "*");
  const declarationsRepos = repos ? declarationsDe(repos.corps) : new Map<string, string>();
  if (declarationsRepos.get("scrollbar-width") !== "thin") {
    fautes.push("au repos, la barre ne garde pas sa place (scrollbar-width: thin)");
  }
  if (declarationsRepos.get("scrollbar-color") !== "transparent transparent") {
    fautes.push("au repos, la barre n'est pas transparente (scrollbar-color)");
  }
  const eveil = regles.find((r) => {
    const s = selecteursDe(r.prelude);
    return s.includes("*:hover") && s.includes("*:focus-within") && s.includes(marque);
  });
  if (eveil === undefined) {
    fautes.push(`aucune règle n'éveille la barre sur *:hover, *:focus-within et ${marque} à la fois`);
  } else {
    const couleur = declarationsDe(eveil.corps).get("scrollbar-color") ?? "";
    const token = /^var\(--([\w-]+)\)\s+transparent$/.exec(couleur)?.[1];
    if (token === undefined) {
      fautes.push(`le pouce éveillé n'emprunte pas un token de la palette (scrollbar-color: ${couleur || "absent"})`);
    } else if (!declareDansLesDeuxThemes(feuille, token)) {
      fautes.push(`--${token} n'est pas déclaré dans les deux thèmes de la palette`);
    }
  }

  // Le fondu respecte `prefers-reduced-motion` : posé sous `no-preference`
  // seulement, jamais annulé après coup (#537).
  const garde = corpsDuBloc(standard, /@media\s*\(prefers-reduced-motion\s*:\s*no-preference\)/) ?? "";
  if (compteFondus(standard) !== compteFondus(garde)) {
    fautes.push("un fondu de scrollbar-color joue hors de prefers-reduced-motion: no-preference");
  }

  // Deux moteurs, un seul actif : dès que `scrollbar-color` est posé, Chromium
  // ignore les pseudo-éléments — les superposer ne choisirait pas.
  if (compteWebkit(feuille) !== compteWebkit(webkit)) {
    fautes.push("::-webkit-scrollbar apparaît hors du bloc @supports not — les deux moteurs se cumulent");
  }
  if (/display\s*:\s*none/.test(webkit)) {
    fautes.push("display: none sur un pseudo-élément WebKit — la barre est absente");
  }
  const reglesWebkit = reglesDe(webkit);
  const pouceRepos = reglesWebkit.find(
    (r) => selecteursDe(r.prelude).join(",") === "::-webkit-scrollbar-thumb",
  );
  if (
    pouceRepos === undefined ||
    declarationsDe(pouceRepos.corps).get("background-color") !== "transparent"
  ) {
    fautes.push("WebKit : le pouce n'est pas transparent au repos");
  }
  const pouceEveil = reglesWebkit.find((r) => {
    const s = selecteursDe(r.prelude);
    return (
      s.includes("*:hover::-webkit-scrollbar-thumb") &&
      s.includes("*:focus-within::-webkit-scrollbar-thumb") &&
      s.includes(`${marque}::-webkit-scrollbar-thumb`)
    );
  });
  if (pouceEveil === undefined) {
    fautes.push(`WebKit : aucune règle n'éveille le pouce sur *:hover, *:focus-within et ${marque}`);
  } else if (
    !/^var\(--[\w-]+\)$/.test(declarationsDe(pouceEveil.corps).get("background-color") ?? "")
  ) {
    fautes.push("WebKit : le pouce éveillé n'emprunte pas un token de la palette");
  }
  return fautes;
}

/**
 * Une feuille **saine**, réduite à ce que le verdict lit, et dont chaque
 * échantillon fautif dérive par une seule retouche — de quoi glisser une faute
 * sans toucher au reste, comme `avec()` dans `contraste.test.ts`.
 */
function feuille({
  largeur = "thin",
  attribut = ATTRIBUT_DEFILEMENT,
  couleur = "var(--bord-fort)",
  fondu = "@media (prefers-reduced-motion: no-preference) { * { transition: scrollbar-color 150ms ease-out; } }",
  enPlus = "",
  couche = true,
}: {
  largeur?: string;
  attribut?: string;
  couleur?: string;
  fondu?: string;
  enPlus?: string;
  couche?: boolean;
} = {}): string {
  const palette =
    ':root, [data-theme="clair"] { --bord-fort: #888888; }\n' +
    '[data-theme="sombre"] { --bord-fort: #737373; }\n';
  const standard =
    "@supports (scrollbar-color: auto) {\n" +
    `  * { scrollbar-width: ${largeur}; scrollbar-color: transparent transparent; }\n` +
    `  *:hover, *:focus-within, [${attribut}] { scrollbar-color: ${couleur} transparent; }\n` +
    `  ${fondu}\n  ${enPlus}\n}\n`;
  const webkit =
    "@supports not (scrollbar-color: auto) {\n" +
    "  ::-webkit-scrollbar { width: 0.5rem; height: 0.5rem; }\n" +
    "  ::-webkit-scrollbar-track, ::-webkit-scrollbar-corner { background: transparent; }\n" +
    "  ::-webkit-scrollbar-thumb { border-radius: 9999px; background-color: transparent; }\n" +
    `  *:hover::-webkit-scrollbar-thumb, *:focus-within::-webkit-scrollbar-thumb, [${attribut}]::-webkit-scrollbar-thumb { background-color: var(--bord-fort); }\n` +
    "}\n";
  const bloc = standard + webkit;
  return palette + (couche ? `@layer base {\n${bloc}}\n` : bloc);
}

describe("⑥ la sonde de l'ascenseur discret, prouvée avant de servir", () => {
  it("rend une feuille saine sans faute", () => {
    // Le témoin doit être sain AVANT d'être sali, sans quoi les fautes
    // ci-dessous pourraient venir d'un défaut de la sonde et non de la retouche.
    expect(verdictAscenseur(feuille(), ATTRIBUT_DEFILEMENT)).toEqual([]);
  });

  it("refuse une barre absente (scrollbar-width: none)", () => {
    expect(verdictAscenseur(feuille({ largeur: "none" }), ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("absente"),
    );
  });

  it("refuse un fondu posé hors de prefers-reduced-motion", () => {
    const sansGarde = feuille({ fondu: "* { transition: scrollbar-color 150ms ease-out; }" });
    expect(verdictAscenseur(sansGarde, ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("hors de prefers-reduced-motion"),
    );
  });

  it("refuse les deux moteurs superposés", () => {
    const cumul = feuille({ enPlus: "::-webkit-scrollbar { width: 0.5rem; }" });
    expect(verdictAscenseur(cumul, ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("se cumulent"),
    );
  });

  it("refuse une feuille qui lit un autre attribut que celui que le JS pose", () => {
    // La frontière : `lib/ascenseur` pose `data-defilement`, la feuille le lit.
    // Renommer d'un seul côté ne casse rien à la compilation, et la barre ne
    // se montrerait plus jamais au défilement.
    const desaccord = feuille({ attribut: "data-scroll" });
    expect(verdictAscenseur(desaccord, ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining(`[${ATTRIBUT_DEFILEMENT}]`),
    );
  });

  it("refuse une règle hors de @layer base", () => {
    expect(verdictAscenseur(feuille({ couche: false }), ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("@layer base"),
    );
  });

  it("refuse une teinte nouvelle, ou un token que la palette ne porte pas", () => {
    // Une teinte à elle aurait dû entrer dans la palette et y déclarer sa paire
    // (`contraste.test.ts`) ; emprunter `--bord-fort` garde le filet sans rien
    // y ajouter. Un `var()` vers un token absent est la même faute, plus
    // discrète : le navigateur rend alors la valeur initiale, sans un mot.
    expect(verdictAscenseur(feuille({ couleur: "#888888" }), ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("n'emprunte pas un token"),
    );
    expect(verdictAscenseur(feuille({ couleur: "var(--pouce)" }), ATTRIBUT_DEFILEMENT)).toContainEqual(
      expect.stringContaining("--pouce n'est pas déclaré"),
    );
  });
});

describe("⑥ l'ascenseur discret de app/globals.css (#725)", () => {
  it("tient toutes ses promesses, sur les octets de la feuille", () => {
    // Le même verdict que ci-dessus, sur la feuille réelle : au repos rien,
    // éveillée au survol, au focus et pendant le défilement ; `thin` et jamais
    // `none` ; le pouce sur `--bord-fort`, déclaré dans les deux thèmes ; le
    // fondu sous `no-preference` ; un seul moteur à la fois ; le tout sous
    // `@layer base`. Une faute est rendue avec son motif.
    const fautes = verdictAscenseur(lireSource("app/globals.css"), ATTRIBUT_DEFILEMENT);
    expect(fautes, `\n${fautes.join("\n")}\n`).toEqual([]);
  });

  it("lit bien un bloc, et pas un vide", () => {
    // Si la feuille déplaçait la règle ou changeait sa forme, `corpsDuBloc`
    // rendrait `null` et le verdict le dirait — mais un parseur qui rendrait un
    // corps VIDE pour un bloc qu'il croit avoir trouvé rendrait des fautes
    // muettes. Le compte épingle ce que le verdict a réellement lu.
    const source = lireSource("app/globals.css").replace(/\/\*[\s\S]*?\*\//g, "");
    const couche = corpsDuBloc(source, /@layer\s+base/);
    expect(couche).not.toBeNull();
    const standard = corpsDuBloc(couche!, /@supports\s*\(scrollbar-color\s*:\s*auto\)/);
    expect(reglesDe(standard!).length).toBeGreaterThanOrEqual(3);
    expect(compteWebkit(source)).toBeGreaterThanOrEqual(4);
  });
});

// ---------------------------------------------------------------------------
// ⑥ bis — la moitié JS de la frontière : lib/ascenseur, et son câblage
// ---------------------------------------------------------------------------

describe("⑥ lib/ascenseur marque l'élément qui défile", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("pose la marque au défilement et la retire après le repos", () => {
    const detacher = ecouterDefilement(document, 50);
    const surface = document.createElement("div");
    document.body.appendChild(surface);

    // `scroll` ne remonte pas : c'est l'écoute en capture qui l'entend.
    surface.dispatchEvent(new Event("scroll"));
    expect(surface).toHaveAttribute(ATTRIBUT_DEFILEMENT);

    // Un défilement par à-coups reste UNE apparition : la marque suit le
    // dernier `scroll`, elle ne s'efface pas entre deux crans.
    vi.advanceTimersByTime(30);
    surface.dispatchEvent(new Event("scroll"));
    vi.advanceTimersByTime(30);
    expect(surface).toHaveAttribute(ATTRIBUT_DEFILEMENT);
    vi.advanceTimersByTime(20);
    expect(surface).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);

    detacher();
    surface.remove();
  });

  it("marque l'élément racine quand c'est la fenêtre qui défile", () => {
    // Le défilement de la fenêtre arrive avec `document` pour cible, or c'est
    // l'élément racine qui porte alors l'ascenseur — et lui seul que le CSS
    // peut habiller.
    const detacher = ecouterDefilement(document, 50);
    document.dispatchEvent(new Event("scroll"));
    expect(document.documentElement).toHaveAttribute(ATTRIBUT_DEFILEMENT);
    vi.advanceTimersByTime(50);
    expect(document.documentElement).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);
    detacher();
  });

  it("ne laisse aucune marque derrière lui au démontage", () => {
    const detacher = ecouterDefilement(document, 50);
    const surface = document.createElement("div");
    document.body.appendChild(surface);
    surface.dispatchEvent(new Event("scroll"));
    expect(surface).toHaveAttribute(ATTRIBUT_DEFILEMENT);

    detacher();
    // Retirée tout de suite — pas au terme du repos —, et l'écoute est partie :
    // un défilement suivant ne marque plus rien.
    expect(surface).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);
    surface.dispatchEvent(new Event("scroll"));
    expect(surface).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);
    surface.remove();
  });
});

describe("⑥ le Shell installe l'écoute — la colonne de /chat se marque quand elle défile", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("marque la colonne de propriétés au défilement, sous le vrai Shell", async () => {
    // Monté sous le `Shell` réel, comme `a11y` et `sobriete` le font : c'est
    // lui qui installe `ecouterDefilement` (#725), au-dessus de la garde du
    // projet. Sans ce câblage, le CSS ne verrait jamais `[data-defilement]`,
    // et la barre ne se montrerait plus au tactile ni au défilement suivi.
    await monterEcran(ECRANS.find((ecran) => ecran.href === "/chat")!);
    const colonne = screen.getByRole("complementary", { name: "Propriétés du fil" });

    expect(colonne).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);
    fireEvent.scroll(colonne);
    expect(colonne).toHaveAttribute(ATTRIBUT_DEFILEMENT);
    await waitFor(() => expect(colonne).not.toHaveAttribute(ATTRIBUT_DEFILEMENT), {
      timeout: 3_000,
    });
  });
});

// ---------------------------------------------------------------------------
// ⑦ La colonne de propriétés de /chat — collante ET bornée
// ---------------------------------------------------------------------------

describe("⑦ la colonne de propriétés de /chat", () => {
  /**
   * ⚠ Une **déclaration**, pas une mesure — le pendant exact du contrôle que
   * `sobriete.test.tsx` fait sur `/couts`, et qu'il ne faisait pas sur `/chat`
   * alors que la page s'y réfère en toutes lettres. C'est cette colonne, avec
   * sa jumelle, qui portait la seconde barre système que #725 a rendue
   * discrète : elle reste collante et bornée (le bon choix, classe de bug de
   * #306 — une surface collante sans plafond voit son bas rester sous le pli),
   * et son ascenseur est désormais celui du socle.
   */
  it("borne sa hauteur partout où elle est collante", () => {
    const source = lireSource("app/chat/page.tsx");
    expect(source).toContain("@4xl:sticky");
    expect(source).toContain("@4xl:max-h-[calc(100dvh-6rem)]");
    expect(source).toContain("@4xl:overflow-y-auto");
  });
});
