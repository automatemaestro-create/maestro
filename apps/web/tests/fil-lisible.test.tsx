/**
 * Ce que le fil rend d'un message, et ce qu'il en refuse (#697, lot 7 de #690).
 *
 * ⚠ **Les tests de ce lot sont différés au lot 8 (#698)**, et ce fichier n'y
 * déroge que sur une chose : la règle du dépôt réserve une exception aux lots
 * intermédiaires dont la **logique est critique** (docs/10 §5.1), et un
 * analyseur écrit à la main qui traite du **texte produit par un modèle** en est
 * la définition. Le reste — la mise en page des bulles, le bouton de copie, le
 * séparateur à l'écran, la géométrie — attend le lot 8 ; on garde ici les deux
 * propriétés qui ne se rattrapent pas après coup :
 *
 * 1. **rien de ce qu'un modèle écrit ne devient du balisage.** C'est
 *    l'avertissement du ticket, et il est tenu par construction (`lib/markdown`
 *    rend des données, jamais du HTML) — ce qui ne dispense pas de le prouver :
 *    une propriété structurelle se perd le jour où quelqu'un ajoute un
 *    `dangerouslySetInnerHTML` « juste pour les tableaux » ;
 * 2. **les écarts délibérés à CommonMark sont des décisions, pas des trous.**
 *    `_` qui n'emphase pas est ce qui garde `run_id` lisible ; sans test, le
 *    prochain lecteur du fichier le prendra pour un oubli et le « corrigera ».
 *
 * Chaque sonde est prouvée sur ce qu'elle doit **trouver** avant qu'on conclue
 * de ce qu'elle ne trouve pas (méthode de `contraste.test.ts`, #534) : un
 * analyseur qui rendrait un tableau vide sur tout passerait sans cela chaque
 * contrôle de sûreté avec les mots de « rien de dangereux n'est rendu ».
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { TexteMarkdown } from "@/components/chat/TexteMarkdown";
import { jourDe, libelleDuJour } from "@/lib/journees";
import { analyserMarkdown, type Bloc, type Inline } from "@/lib/markdown";

/** Le premier bloc, quand le cas ne porte que sur lui. */
function seul(texte: string): Bloc {
  const blocs = analyserMarkdown(texte);
  expect(blocs).toHaveLength(1);
  return blocs[0];
}

/** Le bloc unique, dont on affirme qu'il est un paragraphe — et ses marques. */
function marquesDe(texte: string): Inline[] {
  const bloc = seul(texte);
  if (bloc.type !== "paragraphe") {
    throw new Error(`attendu un paragraphe, reçu « ${bloc.type} »`);
  }
  return bloc.enfants;
}

function inlinesEnTexte(noeuds: Inline[]): string {
  return noeuds
    .map((noeud) => {
      switch (noeud.type) {
        case "texte":
        case "code":
          return noeud.texte;
        case "saut":
          return "\n";
        default:
          return inlinesEnTexte(noeud.enfants);
      }
    })
    .join("");
}

/** Tout le texte d'un bloc, marques aplaties — de quoi juger sans le rendu. */
function texteDe(bloc: Bloc): string {
  switch (bloc.type) {
    case "code":
      return bloc.texte;
    case "filet":
      return "";
    case "citation":
      return bloc.blocs.map(texteDe).join("\n");
    case "liste":
      return bloc.entrees.map(inlinesEnTexte).join("\n");
    default:
      return inlinesEnTexte(bloc.enfants);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// L'analyseur — ce qu'il reconnaît
// ─────────────────────────────────────────────────────────────────────────────

describe("l'analyseur Markdown (lib/markdown)", () => {
  it("ne rend rien d'un texte vide, et un paragraphe d'un texte nu", () => {
    // La moitié qui rend les contrôles de sûreté opposables : un analyseur muet
    // ne rendrait jamais de balisage non plus.
    expect(analyserMarkdown("   \n  ")).toEqual([]);
    expect(seul("Bonjour").type).toBe("paragraphe");
    expect(texteDe(seul("Bonjour"))).toBe("Bonjour");
  });

  it("garde les retours à la ligne d'un paragraphe au lieu de les recoller", () => {
    // Un agent aligne ses phrases ; recoller changerait ce qu'il a écrit.
    expect(marquesDe("une ligne\nune autre").map((n) => n.type)).toEqual([
      "texte",
      "saut",
      "texte",
    ]);
  });

  it("rend un bloc de code avec son langage", () => {
    const bloc = seul("```bash\nls -la\n```");
    expect(bloc).toEqual({
      type: "code",
      langage: "bash",
      texte: "ls -la",
      ferme: true,
    });
  });

  it("tient un bloc de code encore ouvert pour du code qui arrive", () => {
    // La propriété qui fait qu'un flux ne clignote pas (#695) : la clôture
    // manquante ne renvoie pas le contenu au régime « paragraphe » pour l'en
    // sortir trois caractères plus tard.
    const bloc = seul("```py\nx = 1");
    expect(bloc).toMatchObject({ type: "code", texte: "x = 1", ferme: false });
  });

  it("ne laisse aucun autre motif jouer à l'intérieur d'un bloc de code", () => {
    const bloc = seul("```\n# pas un titre\n- pas une liste\n```");
    expect(bloc.type).toBe("code");
    expect(texteDe(bloc)).toBe("# pas un titre\n- pas une liste");
  });

  it("reconnaît titres, listes, citation et filet", () => {
    const blocs = analyserMarkdown(
      "## Étapes\n\n- un\n- deux\n\n1. premier\n\n> cité\n\n---",
    );
    expect(blocs.map((b) => b.type)).toEqual([
      "titre",
      "liste",
      "liste",
      "citation",
      "filet",
    ]);
    expect(blocs[0]).toMatchObject({ type: "titre", niveau: 2 });
    expect(blocs[1]).toMatchObject({ ordonnee: false });
    expect(blocs[2]).toMatchObject({ ordonnee: true, depart: 1 });
    // La citation est **ré-analysée**, donc elle porte des blocs et non du texte.
    expect(blocs[3]).toMatchObject({ type: "citation" });
    expect(texteDe(blocs[3])).toBe("cité");
  });

  it("numérote une liste ordonnée à partir de son premier numéro", () => {
    expect(seul("3. trois\n4. quatre")).toMatchObject({ depart: 3 });
  });

  it("reconnaît code en ligne, gras, italique et lien", () => {
    const types = marquesDe(
      "vois `run_id`, c'est **important**, un peu *long*, [ici](https://exemple.test/a)",
    ).map((n) => n.type);
    expect(types).toContain("code");
    expect(types).toContain("fort");
    expect(types).toContain("accent");
    expect(types).toContain("lien");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// L'analyseur — les écarts délibérés
// ─────────────────────────────────────────────────────────────────────────────

describe("les écarts assumés à CommonMark (lib/markdown)", () => {
  it("laisse `_` littéral : un identifiant n'est pas une emphase", () => {
    // La raison d'être de l'écart : `run_id` et `tache_id` traversent chaque
    // réponse de l'orchestration, et `snake_case_ici` en emphaserait le milieu.
    const marques = marquesDe("le champ run_id et tache_id de l'événement");
    expect(marques.every((n) => n.type === "texte")).toBe(true);
    expect(inlinesEnTexte(marques)).toBe(
      "le champ run_id et tache_id de l'événement",
    );
  });

  it("ne fait pas basculer une emphase qui n'est pas refermée", () => {
    // Le cas du direct : `**Att` arrive avant `**Attention**`. Tant que la
    // seconde marque manque, c'est du texte — et rien ne saute quand elle vient.
    expect(texteDe(seul("**Att"))).toBe("**Att");
    expect(marquesDe("**Attention**")[0].type).toBe("fort");
  });

  it("ne laisse pas une emphase franchir la fin d'une ligne", () => {
    // Ce qui borne l'analyse et garde le moteur linéaire sur une entrée non
    // fiable : sans cette borne, `*` ouvert en tête d'un pavé chercherait sa
    // paire jusqu'au bout.
    const marques = marquesDe("un * seul\net un autre * seul");
    expect(marques.some((n) => n.type === "accent")).toBe(false);
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// La sûreté — ce qu'un modèle ne peut pas faire faire au fil
// ─────────────────────────────────────────────────────────────────────────────

describe("ce que le fil refuse au texte d'un modèle", () => {
  it("rend le HTML de la source en toutes lettres, jamais en éléments", () => {
    // L'avertissement du ticket, prouvé du côté du rendu : la propriété est
    // structurelle (aucune chaîne de HTML n'existe nulle part), et c'est
    // justement pour cela qu'elle doit être gardée — elle se perdrait sans bruit.
    const { container } = render(
      <TexteMarkdown texte={'<img src=x onerror="alert(1)"> et <b>gras</b>'} />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<img src=x");
    expect(container.textContent).toContain("<b>gras</b>");
  });

  it("refuse un lien qui n'est pas suivable, et le laisse lisible", () => {
    // `lienExterneSur` (#192) est le point de passage unique du produit. Un
    // `href` mort vaudrait mieux que dangereux ; le texte d'origine vaut mieux
    // que les deux — on ne fait pas disparaître ce que l'agent a écrit.
    //
    // ⚠ Les trois adresses ci-dessous **matchent le motif de lien** et sont
    // écartées par l'assainisseur : c'est ce qu'on veut prouver. Une charge
    // parenthésée (`javascript:alert(1)`) serait un mauvais cas d'espèce — le
    // motif ne la reconnaît déjà pas comme une adresse, donc le test passerait
    // sans que `lienExterneSur` soit jamais consulté.
    const { container } = render(
      <TexteMarkdown texte="[a](javascript:alert1) [b](data:text/html;x) [c](/interne)" />,
    );
    expect(container.querySelector("a")).toBeNull();
    expect(container.textContent).toContain("[a](javascript:alert1)");
    expect(container.textContent).toContain("[b](data:text/html;x)");
    expect(container.textContent).toContain("[c](/interne)");
  });

  it("ouvre un lien légitime sans donner prise à la page ouverte", () => {
    // Le pendant du contrôle ci-dessus : une sonde qui refuserait tout lien
    // passerait le précédent sans rien garder.
    render(<TexteMarkdown texte="[le ticket](https://exemple.test/697)" />);
    const lien = screen.getByRole("link", { name: "le ticket" });
    expect(lien).toHaveAttribute("href", "https://exemple.test/697");
    expect(lien).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("ne laisse pas un titre de message entrer dans le plan de la page", () => {
    // Un `###` d'agent deviendrait un `<h3>` du document : la règle
    // `heading-order` d'axe rougirait (#537) et le sommaire annoncé au lecteur
    // d'écran décrirait la réponse au lieu de l'écran.
    const { container } = render(<TexteMarkdown texte="# Résumé" />);
    expect(container.querySelector("h1, h2, h3, h4, h5, h6")).toBeNull();
    expect(container.textContent).toBe("Résumé");
  });
});

// ─────────────────────────────────────────────────────────────────────────────
// Les journées
// ─────────────────────────────────────────────────────────────────────────────

describe("le découpage en journées (lib/journees)", () => {
  /** Un instant local, construit par composantes : le fuseau du poste fait foi. */
  const le = (a: number, m: number, j: number, h = 12) =>
    new Date(a, m - 1, j, h).getTime();

  it("range deux instants du même jour local sous la même journée", () => {
    const matin = new Date(le(2026, 8, 28, 8)).toISOString();
    const soir = new Date(le(2026, 8, 28, 23)).toISOString();
    expect(jourDe(matin)).toBe(jourDe(soir));
    expect(jourDe(new Date(le(2026, 8, 29, 8)).toISOString())).not.toBe(
      jourDe(matin),
    );
  });

  it("ne devine pas la journée d'un horodatage absent ou illisible", () => {
    // Reprise de `EtatDesRuns.soldeAujourdHui` : on ne sait pas quel jour
    // c'était, donc le message n'ouvre ni ne ferme aucune journée.
    expect(jourDe("")).toBeNull();
    expect(jourDe(undefined)).toBeNull();
    expect(jourDe("pas une date")).toBeNull();
  });

  it("dit « Aujourd'hui » et « Hier » quand l'horloge a démarré", () => {
    const maintenant = le(2026, 8, 28, 15);
    expect(libelleDuJour("2026-08-28", maintenant)).toBe("Aujourd'hui");
    expect(libelleDuJour("2026-08-27", maintenant)).toBe("Hier");
  });

  it("rend une date absolue tant qu'il n'y a pas d'horloge", () => {
    // La règle d'hydratation (#250) : `Date.now()` ne vaut pas la même chose
    // des deux côtés, donc « Aujourd'hui » posé trop tôt ferait diverger l'HTML.
    // La date absolue, elle, est identique partout.
    const sansHorloge = libelleDuJour("2026-08-28", null);
    expect(sansHorloge).not.toBe("Aujourd'hui");
    expect(sansHorloge).toContain("2026");
  });

  it("garde l'année quand la journée n'est pas de l'année courante", () => {
    const maintenant = le(2026, 8, 28, 15);
    expect(libelleDuJour("2026-03-02", maintenant)).not.toContain("2026");
    expect(libelleDuJour("2025-03-02", maintenant)).toContain("2025");
  });
});
