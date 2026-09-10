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
 * ⑥ **l'ascenseur discret** (#725, puis #882) — vérifié sur les **octets** de
 *    `globals.css` (technique de `contraste.test.ts`), et la moitié JS de la
 *    frontière (`lib/ascenseur`, câblé dans le `Shell`). Depuis #882 la page
 *    en est l'**exception** : sa barre est peinte sans condition, dans les
 *    deux moteurs, et le `Shell` la désigne par `data-ascenseur="page"` — une
 *    sonde prouvée sur la feuille d'**avant**, où la page dépendait du
 *    pointeur comme tout le reste ;
 * ⑦ **la colonne de propriétés** de `/chat` est collante **et** bornée, comme
 *    celle de `/couts` que `sobriete.test.tsx` garde déjà ;
 * ⑧ **l'envoi et l'arrêt sont deux icônes nommées, de même taille** (#884,
 *    partis pris 1 et 2 de la veille #866) — la construction de
 *    `BoutonJoindre` en tête du rail, l'arrêt à la place de l'envoi, et le
 *    champ qui part d'**une** ligne. La sonde est prouvée sur le rail d'avant
 *    #884, où l'envoi était un texte et l'arrêt ~43 px plus large que lui ;
 * ⑨ **le cadre se replie sous `sm`** (#891, parti pris 2 de la veille #873) —
 *    une rangée qui passe à la ligne, le champ qui prend la ligne entière dès
 *    la deuxième ligne de brouillon, et l'invariant qui empêche le cadre
 *    d'osciller : seule une mesure prise **en rangée unique** pose ou lève le
 *    débordement. Plus l'**ordre d'émission de Tailwind**, sur le CSS
 *    compilé — la moitié de la frontière qu'aucun test de rendu ne voit ;
 * ⑩ **les amorces se bornent à deux sous `sm`** (#891, parti pris 3) — un
 *    marqueur de mise en page, aucune amorce retirée du DOM.
 */

import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { compile } from "tailwindcss";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import PageChat from "@/app/chat/page";
import { ContenuOngletAgent } from "@/components/ContenuOngletAgent";
import { AMORCE_HORS_SM, AMORCES_SOUS_SM } from "@/components/Conversation";
import { ID_CONTENU_PRINCIPAL } from "@/components/Shell";
import {
  ASCENSEUR_PAGE,
  ATTRIBUT_ASCENSEUR,
  ATTRIBUT_DEFILEMENT,
  REPOS_DEFILEMENT_MS,
  ecouterDefilement,
} from "@/lib/ascenseur";
import { marquerGuideVu } from "@/lib/guide";
import {
  AGENT_ORCHESTRATION,
  AMORCES_ORCHESTRATION,
  INTERLOCUTEUR_ORCHESTRATION,
  ROLE_ORCHESTRATION,
} from "@/lib/orchestration";

import {
  agentFactice,
  poserFilAssistance,
  poserProjetActif,
  rendreAvecEtat,
  reserveDuFlottantRem,
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
 * Ce que le fil porte de l'assistant flottant (#123) : son bouton, reconnu à
 * l'ancre `data-guide="assistant"` qu'il porte pour la visite guidée — le seul
 * repère qui ne dépende ni d'un libellé ni d'une classe. Depuis #885 la réponse
 * attendue est « rien » : la veille #866 proposait de le poser au-dessus du
 * composeur, dans le fil, et c'est refusé sur mesure — le flottant vit dans le
 * shell, en coin, sur les dix écrans, et la bande sous le composeur en est le
 * prix.
 */
function assistantDans(fil: HTMLElement): HTMLElement[] {
  return Array.from(
    fil.querySelectorAll<HTMLElement>('[data-guide="assistant"]'),
  );
}

/**
 * Un fil qui **porterait** l'assistant : le bouton du flottant posé au-dessus
 * du composeur, dans la section du fil — la place que la veille #866 proposait
 * (celle du « aller en bas » de ChatGPT et de Zulip) et que #885 a refusée.
 * Personne ne l'a jamais écrit ; c'est l'échantillon fautif qui prouve la sonde.
 */
function filAvecAssistant(): { fil: HTMLElement } {
  const fil = document.createElement("section");
  fixtures.push(fil);
  fil.innerHTML =
    '<ol aria-label="Fil"></ol>' +
    '<button type="button" data-guide="assistant" aria-expanded="false" aria-label="Ouvrir l\'assistant"></button>' +
    '<form class="sticky bottom-16"><textarea aria-label="Message à dev"></textarea></form>';
  document.body.appendChild(fil);
  return { fil };
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
 * Le bout du rail d'**avant** #884, tel que #726 l'avait écrit et que la veille
 * #866 l'a mesuré : « Envoyer » en **texte** (`plein petite`, 63×25) et,
 * pendant une réponse, « Interrompre » en `contour` avec une icône **et** son
 * texte (~106 px) — deux boîtes que leur libellé dimensionne, d'où un bout de
 * rail qui sautait de ~43 px à chaque envoi. Les deux sont posés ensemble ici ;
 * à l'écran ils alternaient.
 */
function railDAvant884(): {
  envoyer: HTMLButtonElement;
  interrompre: HTMLButtonElement;
} {
  const rail = document.createElement("div");
  fixtures.push(rail);
  rail.innerHTML =
    '<button type="submit" class="inline-flex min-h-6 gap-1 rounded-md bg-accent px-2.5 py-1 text-annexe text-sur-ton">Envoyer</button>' +
    '<button type="button" class="inline-flex min-h-6 gap-1 rounded-md border border-bord-fort px-2.5 py-1 text-annexe text-texte-secondaire">' +
    '<svg aria-hidden="true" class="size-3.5"></svg>Interrompre</button>';
  document.body.appendChild(rail);
  const [envoyer, interrompre] = Array.from(rail.querySelectorAll("button"));
  return { envoyer, interrompre };
}

/**
 * La **forme** d'un contrôle du rail (#884, parti pris 1 de la veille #866) :
 * porte-t-il une icône du jeu, son libellé est-il visible ou réservé aux
 * lecteurs d'écran (`sr-only`), et quelle boîte s'est-il donnée — le pas de sa
 * taille (`px-*`/`py-*`), son plancher (`min-h-*`) et sa largeur (`w-*`), si
 * une est posée. Deux contrôles « de même taille » au sens du parti pris sont
 * deux icônes nommées dont la boîte ne dépend d'aucun texte : même pas, même
 * largeur posée, rien de visible qui puisse l'élargir. Le pixel, lui, est au
 * banc (#308).
 */
function formeDuControle(bouton: HTMLElement): {
  icone: boolean;
  libelle: "visible" | "sr-only" | "aucun";
  boite: string[];
} {
  const icone = bouton.querySelector('svg[aria-hidden="true"]') !== null;
  const copie = bouton.cloneNode(true) as HTMLElement;
  for (const masque of copie.querySelectorAll(".sr-only")) masque.remove();
  const visible = (copie.textContent ?? "").trim() !== "";
  const masque = Array.from(bouton.querySelectorAll(".sr-only")).some(
    (n) => (n.textContent ?? "").trim() !== "",
  );
  return {
    icone,
    libelle: visible ? "visible" : masque ? "sr-only" : "aucun",
    boite: Array.from(bouton.classList)
      .filter((c) => /^(px|py|w|min-h|size)-/.test(c))
      .sort(),
  };
}

/**
 * La **mise en page du cadre** (#891) : une rangée qui passe à la ligne, avec
 * ses deux écarts. Les deux étages de #726 n'en sont pas une autre boîte —
 * c'est le même cadre, dans l'état où le champ prend la ligne entière (voir
 * `dispositionDuChamp`) et pousse le rail sur la suivante. Avant #891, le
 * cadre était une **colonne** : le repli y était impossible, la tête du rail
 * n'étant pas même une sœur du champ.
 */
function dispositionDuCadre(cadre: HTMLElement): string[] {
  return Array.from(cadre.classList)
    .filter((c) => /^(flex|flex-wrap|flex-col|items-|gap-)/.test(c))
    .sort();
}

/**
 * Ce que le `className` d'une pièce du cadre dit de la rangée où elle vit
 * (#891) : les utilitaires de mise en page **nus** — ceux qui valent à toute
 * largeur — et ceux que le `sm:` remet au-dessus du point de rupture, préfixe
 * retiré.
 *
 * - rangée unique : le champ en `flex-1 min-w-0` entre les deux bouts du rail,
 *   la tête du rail passée devant lui à l'affichage (`order-first`), et le
 *   `sm:` qui restitue les deux étages à l'un comme à l'autre ;
 * - deux étages : le champ en `w-full`, qui pousse le rail sur la ligne
 *   suivante, et une tête sans classe de place — rien à restituer, c'est déjà
 *   l'état de #726.
 *
 * C'est le contrat, pas la géométrie : le pixel est au banc (#308).
 */
function dispositionDe(piece: HTMLElement): {
  rangee: string[];
  auDela: string[];
} {
  const MISE_EN_PAGE = /^(order-|w-full$|flex-1$|flex-none$|min-w-0$|basis-)/;
  const classes = Array.from(piece.classList);
  return {
    rangee: classes.filter((c) => MISE_EN_PAGE.test(c)).sort(),
    auDela: classes
      .filter((c) => c.startsWith("sm:"))
      .map((c) => c.slice("sm:".length))
      .sort(),
  };
}

/**
 * Les **pièces du cadre**, dans l'ordre du **flux** (#891) — celui que la
 * tabulation suit, et qui ne bouge pas : le champ, puis la tête du rail, puis
 * son bout. Le repli ne déplace la tête qu'à l'**affichage**, ce qui garde
 * intacte la tabulation de #726 (③) et l'ordre de lecture des deux étages.
 */
function piecesDuCadre(cadre: HTMLElement): string[] {
  return Array.from(cadre.children).map((enfant) => {
    if (enfant.tagName === "TEXTAREA") return "champ";
    if (enfant.tagName === "BUTTON") return nomDe(enfant);
    return "bout-du-rail";
  });
}

/**
 * Les amorces **retirées sous `sm`** (#891, parti pris 3), telles que leur
 * marqueur les désigne — et lui seul : rien n'est retiré du DOM, donc compter
 * les boutons ne dirait rien.
 */
function amorcesHorsSm(groupe: HTMLElement): string[] {
  return Array.from(groupe.querySelectorAll<HTMLElement>("button"))
    .filter((bouton) => bouton.classList.contains(AMORCE_HORS_SM))
    .map((bouton) => (bouton.textContent ?? "").trim());
}

/**
 * Le cadre d'**avant** #891 : une **colonne** (`flex flex-col gap-1`), le
 * champ toujours pleine largeur, et un rail dans son propre `<div>` — donc la
 * tête du rail n'est pas une sœur du champ et aucune rangée `+` · champ ·
 * envoi n'est possible, à aucune largeur. C'est l'échantillon fautif des trois
 * sondes ci-dessus.
 */
function cadreDAvant891(): { cadre: HTMLElement; champ: HTMLTextAreaElement } {
  const cadre = document.createElement("div");
  fixtures.push(cadre);
  cadre.className =
    "flex flex-col gap-1 w-full rounded-md border border-bord bg-surface px-3 py-1.5";
  cadre.innerHTML =
    '<textarea rows="1" aria-label="Message à dev" ' +
    'class="max-h-48 w-full resize-none overflow-y-auto outline-none"></textarea>' +
    '<div class="flex items-center gap-2">' +
    '<button type="button" title="Joindre des sources…">' +
    '<span class="sr-only">Joindre des sources…</span></button>' +
    '<div class="ms-auto flex items-center gap-2">' +
    '<button type="submit"><span class="sr-only">Envoyer</span></button>' +
    "</div></div>";
  document.body.appendChild(cadre);
  return { cadre, champ: cadre.querySelector("textarea")! };
}

/**
 * Les amorces d'**avant** #891 : les quatre rendues sans marqueur, qui
 * s'empilaient sur quatre lignes à 375 × 667 sous un composeur à quai (banc du
 * 2026-09-04, capture `.maestro/banc/chat-375x667.png`). L'échantillon fautif
 * de `amorcesHorsSm` — sans lui, « deux amorces bornées » serait vrai d'une
 * sonde qui regarde ailleurs.
 */
function amorcesDAvant891(): { groupe: HTMLElement } {
  const groupe = document.createElement("div");
  fixtures.push(groupe);
  groupe.setAttribute("role", "group");
  groupe.setAttribute("aria-label", "Suggestions pour commencer");
  groupe.className = "flex flex-wrap gap-1.5";
  groupe.innerHTML = ["une", "deux", "trois", "quatre"]
    .map(
      (amorce) =>
        '<button type="button" class="inline-flex min-h-6 border border-bord-fort ' +
        `px-2.5 py-1">${amorce}</button>`,
    )
    .join("");
  document.body.appendChild(groupe);
  return { groupe };
}

/**
 * Le CSS que Tailwind émet **réellement** pour une liste de classes.
 *
 * C'est l'autre moitié de la frontière de #891 : une chaîne de classes d'un
 * côté, une cascade de l'autre, et rien entre les deux qu'un test de rendu
 * puisse voir — jsdom n'applique aucune feuille (#308), donc un marqueur
 * inerte y serait indiscernable d'un marqueur qui agit (leçon de #830, où le
 * signal « page prête » vivait des deux côtés d'une frontière que rien ne
 * gardait). Le paquet est résolu par son export `tailwindcss/index.css` plutôt
 * qu'en chemin recopié : un `node_modules` remonté d'un cran ne casse rien.
 */
const INDEX_TAILWIND = createRequire(import.meta.url).resolve(
  "tailwindcss/index.css",
);

async function cssCompile(classes: string[]): Promise<string> {
  const compilateur = await compile('@import "tailwindcss";', {
    base: path.dirname(INDEX_TAILWIND),
    loadStylesheet: async (id, base) => {
      const fichier =
        id === "tailwindcss" ? INDEX_TAILWIND : path.resolve(base, id);
      return {
        path: fichier,
        base: path.dirname(fichier),
        content: readFileSync(fichier, "utf8"),
      };
    },
  });
  return compilateur.build(classes);
}

/**
 * Où la règle d'un utilitaire est émise. Le sélecteur est cherché **avec son
 * point et son accolade** : sans eux, `.hidden` matcherait d'abord
 * `.max-sm\:hidden`, et la comparaison dirait l'inverse de la vérité.
 */
function rangDeLaRegle(css: string, selecteur: string): number {
  const rang = css.indexOf(`${selecteur} {`);
  expect(rang, `règle absente du CSS compilé : ${selecteur}`).toBeGreaterThan(-1);
  return rang;
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

describe("les sondes du composeur, prouvées sur le composeur d'avant (#726, puis #884)", () => {
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

  it("reconnaissent un assistant posé dans le fil, au-dessus du composeur", () => {
    const { fil } = filAvecAssistant();
    const [bouton] = assistantDans(fil);
    expect(bouton).toBeDefined();
    expect(bouton.getAttribute("aria-label")).toBe("Ouvrir l'assistant");
  });

  it("trouvent le raccourci dans le placeholder, et nulle part ailleurs", () => {
    const { champ } = composeurDAvant();
    expect(ouVitLeRaccourci(champ)).toEqual({
      placeholder: true,
      description: null,
    });
  });

  it("voient, sur le cadre d'avant #891, une colonne dont le champ ne se replie pas", () => {
    const { cadre, champ } = cadreDAvant891();
    // Une colonne, jamais une rangée qui passe à la ligne : rien à replier.
    expect(dispositionDuCadre(cadre)).toEqual(["flex", "flex-col", "gap-1"]);
    // Et la tête du rail n'est même pas une sœur du champ : elle vit dans le
    // `<div>` du rail, donc `+` · champ · envoi est hors d'atteinte à toute
    // largeur.
    expect(piecesDuCadre(cadre)).toEqual(["champ", "bout-du-rail"]);
    // ⚠ Le champ, lui, porte **exactement** ce qu'il porte à deux étages
    // aujourd'hui (`w-full`, rien à restituer) — et c'est normal : ce composeur
    // n'a que cet état-là. Ce que l'échantillon prouve n'est donc pas que
    // `dispositionDe` distingue les deux formes du champ, mais qu'aucun autre
    // état n'existait — ce que les deux sondes ci-dessus établissent.
    expect(dispositionDe(champ)).toEqual({ rangee: ["w-full"], auDela: [] });
  });

  it("ne trouvent aucune amorce bornée dans les quatre d'avant #891", () => {
    const { groupe } = amorcesDAvant891();
    expect(within(groupe).getAllByRole("button")).toHaveLength(4);
    expect(amorcesHorsSm(groupe)).toEqual([]);
  });

  it("voient, sur le rail d'avant #884, un envoi en texte et un arrêt dont la boîte suit le texte", () => {
    const { envoyer, interrompre } = railDAvant884();
    // « Envoyer » n'est qu'un texte ; « Interrompre » a bien une icône, mais
    // son libellé reste visible, et ni l'un ni l'autre ne pose de largeur :
    // leurs boîtes sont celles de leurs mots — 63 et ~106 px, mesurés.
    expect(formeDuControle(envoyer)).toEqual({
      icone: false,
      libelle: "visible",
      boite: ["min-h-6", "px-2.5", "py-1"],
    });
    expect(formeDuControle(interrompre)).toEqual({
      icone: true,
      libelle: "visible",
      boite: ["min-h-6", "px-2.5", "py-1"],
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
    it("part d'une ligne, sans hauteur posée", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      // `rows` est la hauteur de départ — une ligne depuis #884 (parti pris 2
      // de la veille #866 : aucune référence ne part de deux), le plancher
      // laissé au navigateur sans pixel recopié — et rien n'est posé tant que
      // rien ne déborde.
      expect(champ.rows).toBe(1);
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

    it("ne porte pas l'assistant : le flottant reste en coin, hors du fil (#885)", () => {
      monter();
      // La veille #866 proposait le bouton de l'assistant au-dessus du
      // composeur, dans le fil — la place du « aller en bas » de ChatGPT et de
      // Zulip. Refusé sur mesure (#885) : ces références y posent un flottant
      // de fil, transitoire ; le nôtre est un flottant d'outil, permanent, et
      // il y couvrirait le dernier message aux six fenêtres du banc. Le fil ne
      // le porte donc pas — il vit dans le shell, et la bande ci-dessus en est
      // le prix.
      const fil = composeurDe(zoneDeSaisie(interlocuteur)).closest("section");
      expect(fil).not.toBeNull();
      expect(assistantDans(fil!)).toEqual([]);
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

  // ── ⑧ l'envoi et l'arrêt sont deux icônes nommées, de même taille ──────
  describe("⑧ l'envoi et l'arrêt sont deux icônes nommées, de même taille (#884)", () => {
    const BOITE_DU_RAIL = ["min-h-6", "px-2.5", "py-1", "w-9"];

    it("envoie par une icône nommée « Envoyer », de la construction du joindre", () => {
      monter();
      const cadre = cadreDe(zoneDeSaisie(interlocuteur));
      const envoyer = within(cadre).getByRole("button", { name: "Envoyer" });
      const joindre = within(cadre).getByRole("button", {
        name: "Joindre des sources…",
      });
      // Une icône du jeu, un libellé que seuls les lecteurs d'écran lisent, et
      // aucun `title=` (#536) : le nom accessible vient du libellé, jamais
      // d'une infobulle.
      expect(formeDuControle(envoyer)).toEqual({
        icone: true,
        libelle: "sr-only",
        boite: BOITE_DU_RAIL,
      });
      expect(envoyer.getAttribute("title")).toBeNull();
      // Le pas de la tête du rail, `w-9` en plus : `plein` n'a pas le filet
      // de `contour`, et 36 px est la largeur du `+`. Le pixel est au banc.
      expect(formeDuControle(joindre)).toMatchObject({
        icone: true,
        libelle: "sr-only",
      });
      expect(BOITE_DU_RAIL).toEqual(
        expect.arrayContaining(formeDuControle(joindre).boite),
      );
      // Au bout du rail, et dernier de son groupe : c'est cette place que
      // l'arrêt reprendra.
      expect(envoyer.parentElement?.classList.contains("ms-auto")).toBe(true);
      expect(envoyer.nextElementSibling).toBeNull();
    });

    it("cède sa place, à sa taille, à un arrêt nommé « Interrompre » pendant une réponse", () => {
      const interrompre = vi.fn();
      poserFilAssistance({ envoi: true, interrompre });
      monter();
      const cadre = cadreDe(zoneDeSaisie(interlocuteur));
      // L'envoi a cédé la place : un seul bouton au bout du rail, de la même
      // forme et de la même boîte — rien ne bouge d'un état à l'autre.
      expect(within(cadre).queryByRole("button", { name: "Envoyer" })).toBeNull();
      const arret = within(cadre).getByRole("button", { name: "Interrompre" });
      expect(formeDuControle(arret)).toEqual({
        icone: true,
        libelle: "sr-only",
        boite: BOITE_DU_RAIL,
      });
      expect(arret.getAttribute("title")).toBeNull();
      expect(arret.parentElement?.classList.contains("ms-auto")).toBe(true);
      expect(arret.nextElementSibling).toBeNull();
      // Et l'arrêt arrête pour de bon (#695) : c'est `interrompre` du fil
      // qu'il joue, pas un simple « je cesse de regarder ».
      fireEvent.click(arret);
      expect(interrompre).toHaveBeenCalledTimes(1);
    });
  });

  // ── ⑨ le cadre se replie sous sm ────────────────────────────────────────
  describe("⑨ le cadre se replie sous `sm` (#891)", () => {
    /** La rangée unique : `+` · champ · envoi, et le `sm:` qui la défait. */
    const RANGEE_UNIQUE = {
      champ: { rangee: ["flex-1", "min-w-0"], auDela: ["flex-none", "w-full"] },
      tete: { rangee: ["order-first"], auDela: ["order-none"] },
    };
    /** Les deux étages de #726 : le champ pleine largeur, le rail dessous. */
    const DEUX_ETAGES = {
      champ: { rangee: ["w-full"], auDela: [] },
      tete: { rangee: [], auDela: [] },
    };

    /** Ce que le cadre monté rend des deux pièces que le repli déplace. */
    function etatDuCadre(champ: HTMLElement) {
      const cadre = cadreDe(champ);
      const tete = within(cadre).getByRole("button", {
        name: "Joindre des sources…",
      });
      return { champ: dispositionDe(champ), tete: dispositionDe(tete) };
    }

    it("est une rangée qui passe à la ligne, et non deux boîtes", () => {
      monter();
      const cadre = cadreDe(zoneDeSaisie(interlocuteur));
      // Les deux étages de #726 ne sont plus une colonne mais l'état de cette
      // rangée où le champ prend la ligne entière : mêmes écarts qu'avant
      // (`gap-x-2` entre les bouts du rail, `gap-y-1` entre les deux étages).
      expect(dispositionDuCadre(cadre)).toEqual([
        "flex",
        "flex-wrap",
        "gap-x-2",
        "gap-y-1",
        "items-center",
      ]);
      // Et le rail n'a plus de `<div>` à lui : ses deux bouts sont des sœurs
      // du champ, ce qui est la seule façon de lui faire partager sa rangée.
      // L'ordre du **flux**, lui, est celui de #726 — c'est ce qui garde la
      // tabulation de ③ intacte, le repli ne jouant qu'à l'affichage.
      expect(piecesDuCadre(cadre)).toEqual([
        "champ",
        "Joindre des sources…",
        "bout-du-rail",
      ]);
    });

    it("partage la rangée au repos, et la rend au rail dès la deuxième ligne", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      // Au repos, le brouillon est vide : il tient sur une ligne, donc `+` ·
      // champ · envoi partagent la rangée sous `sm`, et le `sm:` restitue les
      // deux étages au-dessus.
      expect(etatDuCadre(champ)).toEqual(RANGEE_UNIQUE);

      // Le champ déborde de sa hauteur de départ : c'est la « deuxième ligne »,
      // et le rail reprend la sienne — à toute largeur, donc plus rien à
      // restituer au-dessus du point de rupture.
      const mesure = { contenu: 120, boite: 52 };
      simulerLaMesure(champ, mesure);
      fireEvent.change(champ, { target: { value: "deux\nlignes" } });
      expect(etatDuCadre(champ)).toEqual(DEUX_ETAGES);
      expect(champ.style.height).toBe("120px");

      // Le brouillon repasse sous le point de débordement : le cadre se
      // replie, et le champ rend sa hauteur.
      mesure.contenu = 40;
      fireEvent.change(champ, { target: { value: "deux" } });
      expect(etatDuCadre(champ)).toEqual(RANGEE_UNIQUE);
      expect(champ.style.height).toBe("");
    });

    it("ne se replie pas sur une mesure prise à deux étages — il oscillerait", () => {
      monter();
      const champ = zoneDeSaisie(interlocuteur);
      const mesure = { contenu: 120, boite: 52 };
      simulerLaMesure(champ, mesure);

      // Le cadre se déplie sur une mesure prise en rangée unique.
      fireEvent.change(champ, { target: { value: "deux lignes" } });
      expect(etatDuCadre(champ)).toEqual(DEUX_ETAGES);

      // Déplié, le champ est plus large de ~88 px (le `+`, l'envoi et leurs
      // deux écarts) : le même texte y rentre sur une ligne. Se replier
      // là-dessus le ferait aussitôt déborder à nouveau — replié il déborde,
      // déplié il rentre, et le cadre changerait de forme à chaque frappe, le
      // champ passant la moitié du temps trop court pour ce qu'il montre. La
      // mesure ne vaut que pour la mise en page où elle a été prise : celle-ci
      // ne lève pas le débordement.
      mesure.contenu = 40;
      fireEvent.change(champ, { target: { value: "deux lignes et plus" } });
      expect(etatDuCadre(champ)).toEqual(DEUX_ETAGES);
      // Ce qu'elle fait, en revanche, c'est rendre au champ la hauteur de sa
      // nouvelle largeur : sans elle, le cadre déplié garderait la hauteur de
      // deux lignes pour un texte qui n'en occupe qu'une.
      expect(champ.style.height).toBe("");
    });
  });
});

// ---------------------------------------------------------------------------
// ⑨ bis — l'ordre d'émission de Tailwind, sur le CSS compilé (#891)
// ---------------------------------------------------------------------------

describe("⑨ l'ordre d'émission de Tailwind (#891)", () => {
  it("écarte le `hidden sm:inline-flex` d'instinct, et retient `max-sm:hidden`", async () => {
    const css = await cssCompile([
      "hidden",
      "inline-flex",
      "sm:inline-flex",
      AMORCE_HORS_SM,
    ]);
    // L'échantillon fautif est ici la **règle** : `.hidden` est émis AVANT
    // `.inline-flex`, donc sur un `Bouton` — dont la classe de socle porte
    // `inline-flex` (`BOUTON_SOCLE`) — un `hidden` nu ne cacherait rien, à
    // aucune largeur, et sans un mot. C'est la forme que la note technique du
    // ticket proposait ; elle ne tient que sur un élément sans display à lui.
    expect(rangDeLaRegle(css, ".hidden")).toBeLessThan(
      rangDeLaRegle(css, ".inline-flex"),
    );
    // Le marqueur retenu est une **variante** : émise après les utilitaires
    // nus, elle l'emporte — et seulement sous le point de rupture.
    expect(rangDeLaRegle(css, `.${AMORCE_HORS_SM.replace(":", "\\:")}`)).toBeGreaterThan(
      rangDeLaRegle(css, ".inline-flex"),
    );
    expect(css).toContain("width < 40rem");
  });

  it("laisse le `sm:` défaire la rangée unique, sur ses deux pièces", async () => {
    // L'autre moitié du repli : au-dessus de `sm`, `sm:flex-none` doit
    // l'emporter sur le `flex-1` nu du champ — sans quoi sa base `0%`
    // gagnerait contre `sm:w-full` et le champ ne reprendrait jamais la ligne
    // entière — et `sm:order-none` sur l'`order-first` de la tête du rail,
    // sans quoi elle resterait devant le champ à deux étages.
    const css = await cssCompile([
      "flex-1",
      "order-first",
      "sm:flex-none",
      "sm:w-full",
      "sm:order-none",
    ]);
    expect(rangDeLaRegle(css, ".sm\\:flex-none")).toBeGreaterThan(
      rangDeLaRegle(css, ".flex-1"),
    );
    expect(rangDeLaRegle(css, ".sm\\:w-full")).toBeGreaterThan(
      rangDeLaRegle(css, ".flex-1"),
    );
    expect(rangDeLaRegle(css, ".sm\\:order-none")).toBeGreaterThan(
      rangDeLaRegle(css, ".order-first"),
    );
  });
});

// ---------------------------------------------------------------------------
// ⑩ Les amorces se bornent à deux sous sm (#891)
// ---------------------------------------------------------------------------

describe("⑩ les amorces se bornent à deux sous `sm` (#891)", () => {
  /**
   * ⚠ Joué sur `/chat` **seulement**, et ce n'est pas la moitié d'un test :
   * `app/chat/page.tsx` est le seul appelant qui passe des amorces, l'onglet
   * Chat d'une fiche agent n'en ayant aucune à borner. Le second cas est
   * vérifié pour ce qu'il est — une absence — plutôt que supposé.
   */
  it("borne à deux, sans retirer une seule amorce du DOM", () => {
    rendreAvecEtat(<PageChat />, {
      agents: [
        agentFactice({ nom: "dev" }),
        agentFactice({ nom: AGENT_ORCHESTRATION, role: ROLE_ORCHESTRATION }),
      ],
    });
    const groupe = screen.getByRole("group", {
      name: "Suggestions pour commencer",
    });
    // Les quatre sont là, à toute largeur : le bornage est un marqueur de mise
    // en page, jamais un `slice` — rien n'est perdu au-dessus du point de
    // rupture, et c'est ce que la note technique du ticket exige.
    expect(within(groupe).getAllByRole("button")).toHaveLength(
      AMORCES_ORCHESTRATION.length,
    );
    // Et ce sont bien les deux **premières** qui restent : les suivantes
    // portent le marqueur, dans l'ordre où elles sont proposées.
    expect(amorcesHorsSm(groupe)).toEqual(
      AMORCES_ORCHESTRATION.slice(AMORCES_SOUS_SM),
    );
  });

  it("n'a rien à borner sur l'onglet Chat d'une fiche agent", () => {
    rendreAvecEtat(<ContenuOngletAgent nom="dev" onglet="chat" />);
    expect(
      screen.queryByRole("group", { name: "Suggestions pour commencer" }),
    ).toBeNull();
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

/** Le marqueur que le `Shell` porte et que la feuille lit (#882). */
const MARQUE_PAGE = `[${ATTRIBUT_ASCENSEUR}="${ASCENSEUR_PAGE}"]`;

/**
 * Le verdict rendu sur une feuille : la liste de ce qui manque à l'ascenseur
 * discret pour tenir ses promesses (`globals.css`, #725, puis #882). Vide, la
 * feuille les tient toutes. C'est **lui** que la feuille réelle subit, et lui
 * qu'on prouve d'abord sur des échantillons fautifs.
 *
 * `attribut` est celui que `lib/ascenseur` pose, `marquePage` celui que le
 * `Shell` porte : la règle vit des deux côtés d'une frontière — le JS qui
 * marque, le JSX qui désigne, le CSS qui lit —, et rien d'autre ne les tient
 * d'accord (même leçon que #830 pour le signal « page prête »).
 */
function verdictAscenseur(
  source: string,
  attribut: string,
  marquePage: string,
): string[] {
  const feuille = source.replace(/\/\*[\s\S]*?\*\//g, "");
  const fautes: string[] = [];
  const marque = `[${attribut}]`;

  /**
   * Un pouce emprunte-t-il un token de la palette, déclaré dans les deux
   * thèmes ? Rend la faute, ou `null`. Une teinte à lui aurait dû entrer dans
   * la palette et y déclarer sa paire (`contraste.test.ts`) ; un `var()` vers
   * un token absent est la même faute, en plus discret — le navigateur rend
   * alors la valeur initiale, sans un mot.
   */
  const fauteDuPouce = (couleur: string, quoi: string): string | null => {
    const token = /^var\(--([\w-]+)\)\s+transparent$/.exec(couleur)?.[1];
    if (token === undefined) {
      return `${quoi} n'emprunte pas un token de la palette (scrollbar-color: ${couleur || "absent"})`;
    }
    if (!declareDansLesDeuxThemes(feuille, token)) {
      return `--${token} n'est pas déclaré dans les deux thèmes de la palette`;
    }
    return null;
  };

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
    const faute = fauteDuPouce(
      declarationsDe(eveil.corps).get("scrollbar-color") ?? "",
      "le pouce éveillé",
    );
    if (faute !== null) fautes.push(faute);
  }

  // L'ascenseur de PAGE est un repère permanent (#882, parti pris 1 de la
  // veille #859) : le sélecteur cherché est le marqueur **nu**, sans pseudo-
  // classe — c'est là tout le contrat. Une règle qui ne le peindrait que sous
  // `:hover` ou `:focus-within` laisserait la page où #725 l'avait laissée :
  // `*:hover` s'applique à tout ancêtre du pointeur, donc la barre de page
  // apparaissait sur le contenu et disparaissait sur la navigation, et
  // n'existait pas au clavier. Un sélecteur nu, lui, s'applique toujours — le
  // trouver dans un groupe (`*:hover, [data-ascenseur="page"]`) suffit donc,
  // chaque sélecteur d'un groupe valant pour lui-même.
  const permanente = regles.find(
    (r) =>
      selecteursDe(r.prelude).includes(marquePage) &&
      declarationsDe(r.corps).has("scrollbar-color"),
  );
  if (permanente === undefined) {
    fautes.push(
      `aucune règle ne peint ${marquePage} sans condition — la barre de page dépend du pointeur`,
    );
  } else {
    // Même contrôle de token que l'éveil, par la même fonction : c'est le pouce
    // de la page, il n'a pas droit à une teinte que l'autre n'aurait pas.
    const faute = fauteDuPouce(
      declarationsDe(permanente.corps).get("scrollbar-color") ?? "",
      "le pouce de la page",
    );
    if (faute !== null) fautes.push(faute);
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

  // La même exception dans l'autre moteur : « les deux moteurs, `@supports`
  // compris ». Un `@supports` qui n'aurait la permanence que d'un côté rendrait
  // la page tributaire du pointeur sur Safari et les anciens Chromium, sans que
  // rien ne le montre depuis un poste sous Chrome.
  const poucePage = reglesWebkit.find((r) =>
    selecteursDe(r.prelude).includes(`${marquePage}::-webkit-scrollbar-thumb`),
  );
  if (poucePage === undefined) {
    fautes.push(
      `WebKit : aucune règle ne peint ${marquePage} sans condition — la barre de page dépend du pointeur`,
    );
  } else if (
    !/^var\(--[\w-]+\)$/.test(declarationsDe(poucePage.corps).get("background-color") ?? "")
  ) {
    fautes.push("WebKit : le pouce de la page n'emprunte pas un token de la palette");
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
  page = MARQUE_PAGE,
}: {
  largeur?: string;
  attribut?: string;
  couleur?: string;
  fondu?: string;
  enPlus?: string;
  couche?: boolean;
  /**
   * Le **sélecteur** de la règle permanente de l'ascenseur de page (#882).
   * `null` retire la règle des deux moteurs : c'est la feuille d'**avant**
   * #882, où la page dépendait du pointeur comme tout le reste.
   */
  page?: string | null;
} = {}): string {
  const palette =
    ':root, [data-theme="clair"] { --bord-fort: #888888; }\n' +
    '[data-theme="sombre"] { --bord-fort: #737373; }\n';
  const reglePage =
    page === null ? "" : `  ${page} { scrollbar-color: var(--bord-fort) transparent; }\n`;
  // Le pseudo-élément se suffixe à **chaque** sélecteur du groupe, sinon un
  // échantillon groupé serait fautif d'un côté pour une raison qui n'est pas
  // celle qu'il illustre.
  const poucePage =
    page === null
      ? ""
      : `  ${page
          .split(",")
          .map((s) => `${s.trim()}::-webkit-scrollbar-thumb`)
          .join(", ")} { background-color: var(--bord-fort); }\n`;
  const standard =
    "@supports (scrollbar-color: auto) {\n" +
    `  * { scrollbar-width: ${largeur}; scrollbar-color: transparent transparent; }\n` +
    `  *:hover, *:focus-within, [${attribut}] { scrollbar-color: ${couleur} transparent; }\n` +
    reglePage +
    `  ${fondu}\n  ${enPlus}\n}\n`;
  const webkit =
    "@supports not (scrollbar-color: auto) {\n" +
    "  ::-webkit-scrollbar { width: 0.5rem; height: 0.5rem; }\n" +
    "  ::-webkit-scrollbar-track, ::-webkit-scrollbar-corner { background: transparent; }\n" +
    "  ::-webkit-scrollbar-thumb { border-radius: 9999px; background-color: transparent; }\n" +
    `  *:hover::-webkit-scrollbar-thumb, *:focus-within::-webkit-scrollbar-thumb, [${attribut}]::-webkit-scrollbar-thumb { background-color: var(--bord-fort); }\n` +
    poucePage +
    "}\n";
  const bloc = standard + webkit;
  return palette + (couche ? `@layer base {\n${bloc}}\n` : bloc);
}

describe("⑥ la sonde de l'ascenseur discret, prouvée avant de servir", () => {
  it("rend une feuille saine sans faute", () => {
    // Le témoin doit être sain AVANT d'être sali, sans quoi les fautes
    // ci-dessous pourraient venir d'un défaut de la sonde et non de la retouche.
    expect(verdictAscenseur(feuille(), ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toEqual([]);
  });

  it("refuse une barre absente (scrollbar-width: none)", () => {
    expect(verdictAscenseur(feuille({ largeur: "none" }), ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("absente"),
    );
  });

  it("refuse un fondu posé hors de prefers-reduced-motion", () => {
    const sansGarde = feuille({ fondu: "* { transition: scrollbar-color 150ms ease-out; }" });
    expect(verdictAscenseur(sansGarde, ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("hors de prefers-reduced-motion"),
    );
  });

  it("refuse les deux moteurs superposés", () => {
    const cumul = feuille({ enPlus: "::-webkit-scrollbar { width: 0.5rem; }" });
    expect(verdictAscenseur(cumul, ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("se cumulent"),
    );
  });

  it("refuse une feuille qui lit un autre attribut que celui que le JS pose", () => {
    // La frontière : `lib/ascenseur` pose `data-defilement`, la feuille le lit.
    // Renommer d'un seul côté ne casse rien à la compilation, et la barre ne
    // se montrerait plus jamais au défilement.
    const desaccord = feuille({ attribut: "data-scroll" });
    expect(verdictAscenseur(desaccord, ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining(`[${ATTRIBUT_DEFILEMENT}]`),
    );
  });

  it("refuse une règle hors de @layer base", () => {
    expect(verdictAscenseur(feuille({ couche: false }), ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("@layer base"),
    );
  });

  it("refuse la feuille d'AVANT #882, où la barre de page dépend du pointeur", () => {
    // L'échantillon fautif est la feuille telle que #725 l'a laissée : aucune
    // règle pour la page, donc `*:hover` seul décide — la barre apparaît quand
    // le pointeur est sur le contenu, disparaît sur la navigation ou hors de la
    // fenêtre, et n'existe pas au clavier. Sans cette moitié, le ✓ sur la
    // feuille réelle serait vrai pour deux raisons : la bonne, et une sonde qui
    // regarde ailleurs.
    const avant = verdictAscenseur(feuille({ page: null }), ATTRIBUT_DEFILEMENT, MARQUE_PAGE);
    expect(avant).toContainEqual(expect.stringContaining("dépend du pointeur"));
    // Les deux moteurs, `@supports` compris : la page n'est pas permanente que
    // sous Chrome. Une seule des deux fautes laisserait Safari en arrière.
    expect(avant.filter((f) => f.includes("dépend du pointeur"))).toHaveLength(2);
  });

  it("refuse une règle de page conditionnée au pointeur", () => {
    // Le piège d'à côté : le marqueur est là, mais sous `:hover` — la page
    // dépend du pointeur exactement comme avant, et le marqueur donne à croire
    // le contraire. Le contrat est le sélecteur **nu**.
    const conditionnee = feuille({ page: `${MARQUE_PAGE}:hover` });
    expect(verdictAscenseur(conditionnee, ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("dépend du pointeur"),
    );
  });

  it("accepte le marqueur groupé avec l'éveil : un sélecteur d'un groupe vaut pour lui-même", () => {
    // Le pendant du test précédent, sans quoi la sonde exigerait une **forme**
    // (une règle à elle) là où le contrat porte sur l'**effet**. Groupé ou non,
    // `[data-ascenseur="page"]` nu s'applique toujours.
    // ⚠ Ce n'est pas une tolérance de principe : c'est ce que la compilation
    // **fait**. Mesuré le 2026-09-10 sur le CSS servi par Next — Lightning CSS
    // réunit les deux règles, qui portent les mêmes déclarations, et rend
    // `:hover, :focus-within, [data-defilement], [data-ascenseur="page"]`. Une
    // sonde qui exigerait une règle séparée rougirait sur la feuille **source**
    // du jour où quelqu'un lirait le CSS compilé.
    const groupee = feuille({ page: `*:focus-within, ${MARQUE_PAGE}` });
    expect(verdictAscenseur(groupee, ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toEqual([]);
  });

  it("refuse une teinte nouvelle, ou un token que la palette ne porte pas", () => {
    // Une teinte à elle aurait dû entrer dans la palette et y déclarer sa paire
    // (`contraste.test.ts`) ; emprunter `--bord-fort` garde le filet sans rien
    // y ajouter. Un `var()` vers un token absent est la même faute, plus
    // discrète : le navigateur rend alors la valeur initiale, sans un mot.
    expect(verdictAscenseur(feuille({ couleur: "#888888" }), ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
      expect.stringContaining("n'emprunte pas un token"),
    );
    expect(verdictAscenseur(feuille({ couleur: "var(--pouce)" }), ATTRIBUT_DEFILEMENT, MARQUE_PAGE)).toContainEqual(
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
    const fautes = verdictAscenseur(lireSource("app/globals.css"), ATTRIBUT_DEFILEMENT, MARQUE_PAGE);
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

  it("s'efface au bout de REPOS_DEFILEMENT_MS quand on ne lui dit rien", () => {
    // Les autres sondes passent un repos court pour ne pas attendre — c'est le
    // **défaut** qui vaut ici, et lui seul est en usage dans le `Shell`. Sans
    // cette sonde, la constante pourrait valoir n'importe quoi sans que rien ne
    // change : c'est le câblage qu'on garde, pas le chiffre. Il vaut 500 ms
    // depuis #882, mesuré sur les deux références de la veille #859.
    const detacher = ecouterDefilement(document);
    const surface = document.createElement("div");
    document.body.appendChild(surface);

    surface.dispatchEvent(new Event("scroll"));
    vi.advanceTimersByTime(REPOS_DEFILEMENT_MS - 1);
    expect(surface).toHaveAttribute(ATTRIBUT_DEFILEMENT);
    vi.advanceTimersByTime(1);
    expect(surface).not.toHaveAttribute(ATTRIBUT_DEFILEMENT);

    detacher();
    surface.remove();
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

describe("⑥ le Shell installe l'écoute, et désigne l'ascenseur de la page", () => {
  beforeEach(() => {
    marquerGuideVu();
    poserProjetActif();
    peuplerEtat();
  });

  it("marque son conteneur défilant comme l'ascenseur de la page (#882)", async () => {
    // L'autre moitié de la frontière du parti pris 1 : la feuille peint
    // `[data-ascenseur="page"]` sans condition (sonde ci-dessus, sur les
    // octets), encore faut-il que quelque chose le porte. Poser la règle sans
    // le marqueur ne casse rien à la compilation et ne peint rien du tout.
    await monterEcran(ECRANS.find((ecran) => ecran.href === "/chat")!);
    const marques = document.querySelectorAll(
      `[${ATTRIBUT_ASCENSEUR}="${ASCENSEUR_PAGE}"]`,
    );

    // Un seul, et c'est le sens du mot « page » : deux marqueurs rendraient
    // permanentes des barres imbriquées que la règle discrète doit effacer.
    expect(marques).toHaveLength(1);
    const page = marques[0];
    // C'est bien le conteneur **défilant**, celui qui porte le contenu — pas
    // un cadre voisin : un marqueur sur une boîte qui ne défile pas ne peint
    // aucune barre, et le ✓ ci-dessus resterait vert.
    expect(page.className).toContain("overflow-y-auto");
    expect(page.contains(document.getElementById(ID_CONTENU_PRINCIPAL))).toBe(true);
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
   *
   * Et le plafond **retranche la réserve du bouton flottant** (#888) : à
   * `calc(100dvh-6rem)` la colonne descendait de 80 px dans la bande que le
   * shell réserve en fin de page, et comme c'est elle qui donne sa
   * hauteur à la rangée, le fil s'y étirait avec elle — composeur à quai
   * remonté de 36 px sur le dernier message **au repos**, à 1280×800 comme à
   * 1536×900. Le bon plafond est `top-20` (5 rem) plus la réserve, lue dans
   * `Shell.tsx` et non recopiée : le jour où la réserve change, ce test dit
   * que le plafond doit suivre.
   */
  it("borne sa hauteur partout où elle est collante, au-dessus de la réserve du flottant", () => {
    const source = lireSource("app/chat/page.tsx");
    expect(source).toContain("@4xl:sticky");
    expect(source).toContain("@4xl:top-20");
    expect(source).toContain(
      `@4xl:max-h-[calc(100dvh-${5 + reserveDuFlottantRem()}rem)]`,
    );
    expect(source).toContain("@4xl:overflow-y-auto");
  });
});
