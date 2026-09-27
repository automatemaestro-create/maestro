/**
 * Le bilan d'un run vu du front (#1285, lot 3 de #1281) : ce qui se dit de lui
 * sans rien dessiner — dans quel ordre ses rubriques se lisent, comment une nature
 * d'échec se nomme, ce que la tête de la vue en résume, ce qu'une pièce ouvre.
 *
 * Le bilan lui-même est rendu par le backend (#1284, `maestro/controltower/
 * bilan.py`) : le modèle juge sur les pièces du journal, l'exécution vérifie que
 * chaque pièce citée existe. **Rien n'est rejugé ici** — ni la nature d'un échec,
 * ni la rubrique d'un constat, ni ce qu'une pièce prouve. Ce module range et
 * nomme ; il ne lit jamais le texte d'un constat pour en tirer quoi que ce soit.
 *
 * Il vit ici plutôt que dans le composant pour la raison de `lib/vuesRun` : la
 * tête de la vue et l'onglet du bilan disent la même chose du même bilan, et deux
 * formulations finiraient par ne plus compter pareil.
 */

import {
  ETAT_BILAN_ABSENT,
  ETAT_BILAN_RENDU,
  FAMILLE_PIECE_USAGE,
  NATURE_ALEA,
  NATURE_DETERMINISTE,
  NATURE_INDETERMINEE,
  RAISON_BILAN_MODELE_MUET,
  RAISON_BILAN_REPONSE_ILLISIBLE,
  RUBRIQUE_ACTE,
  RUBRIQUE_CONSOMMATION,
  RUBRIQUE_ECHEC,
  RUBRIQUE_LIVRE,
  RUBRIQUE_RECOMMANDATION,
  type BilanRun,
  type ConstatBilan,
  type PieceBilan,
  type ReponseBilan,
} from "./types";

/**
 * Les rubriques, **dans l'ordre où on les lit** : le travers d'abord (veille de
 * #1285, d'après GitHub Actions et Buildkite, qui rangent les erreurs avant les
 * notices). La question de l'écran est « qu'est-ce que ce run a fait de travers,
 * pourquoi, et que faire ? » — ce qui a failli y répond, ce qu'il faut changer
 * ensuite, les actes sortis ou accordés sans personne après, puis ce qui a coûté
 * sans rien rendre, et ce qui a été livré en dernier.
 *
 * L'ordre est celui de la **lecture**, pas celui du backend (`RUBRIQUES`, qui
 * ouvre sur le livré, sert le récit de fin) : un constat garde sa rubrique, seule
 * sa place à l'écran est décidée ici.
 */
export const RUBRIQUES_BILAN: { cle: string; intitule: string }[] = [
  { cle: RUBRIQUE_ECHEC, intitule: "Ce qui a failli, et pourquoi" },
  { cle: RUBRIQUE_RECOMMANDATION, intitule: "Ce qu'il faut changer" },
  { cle: RUBRIQUE_ACTE, intitule: "Actes sortis du projet ou accordés sans personne" },
  { cle: RUBRIQUE_CONSOMMATION, intitule: "Consommation sans résultat" },
  { cle: RUBRIQUE_LIVRE, intitule: "Ce qui a été livré" },
];

export type RubriqueLue = {
  cle: string;
  intitule: string;
  constats: ConstatBilan[];
};

/**
 * Les rubriques du bilan qui ont quelque chose à dire, dans l'ordre de lecture,
 * chacune avec ses constats dans l'ordre où le modèle les a rendus. Une rubrique
 * vide ne s'affiche pas : « rien sur ce qui a failli » ne se dit pas par un titre
 * sans rien dessous. Un constat d'une rubrique inconnue n'existe pas — la
 * vérification du backend l'aurait écarté.
 */
export function rubriquesDuBilan(bilan: BilanRun): RubriqueLue[] {
  return RUBRIQUES_BILAN.map(({ cle, intitule }) => ({
    cle,
    intitule,
    constats: bilan.constats.filter((constat) => constat.rubrique === cle),
  })).filter((rubrique) => rubrique.constats.length > 0);
}

/**
 * Ce qu'une nature d'échec dit **en mots** — jamais le code. La forme la porte
 * aussi (le glyphe du badge, choisi par le composant) : « se reproduira » et
 * « aléa » ne doivent pas se distinguer par leur seule teinte (veille de #1285,
 * d'après Buildkite — « TIMEOUT 3/3 » contre « FLAKY 2/3 » — et Datadog Bits).
 */
export const NATURES_BILAN: Record<
  string,
  {
    libelle: string;
    ton: "alerte" | "attention" | "neutre";
    /** Ce que la tête dit de ce qui a failli quand toute la rubrique a cette nature. */
    verdict: string;
    /** Son nom court, quand la rubrique mêle plusieurs natures. */
    court: string;
  }
> = {
  [NATURE_DETERMINISTE]: {
    libelle: "Se reproduira",
    ton: "alerte",
    verdict: "ce qui a failli se reproduira",
    court: "se reproduira",
  },
  [NATURE_ALEA]: {
    libelle: "Aléa",
    ton: "attention",
    verdict: "ce qui a failli relève d'un aléa",
    court: "aléa",
  },
  [NATURE_INDETERMINEE]: {
    libelle: "Nature indéterminée",
    ton: "neutre",
    verdict: "ce qui a failli est de nature indéterminée",
    court: "nature indéterminée",
  },
};

/** « 1 chose », « 2 choses » — le pluriel français des comptes de la tête. */
function compte(nombre: number, singulier: string, pluriel: string): string {
  return `${nombre} ${nombre > 1 ? pluriel : singulier}`;
}

/**
 * Ce que la **tête** de la vue dit du bilan, en une ligne (veille de #1285,
 * d'après le résumé de Buildkite : « 3 annotations — View all → ») : la nature de
 * ce qui a failli, ce qu'il faut changer, les actes sortis ou accordés sans
 * personne. Les deux dernières rubriques ne s'y comptent pas — elles ne changent
 * pas ce qu'on fait dans la minute —, et l'onglet les porte.
 *
 * La nature est dans la ligne parce que c'est **la** réponse que la tête doit
 * donner (relevé par le regard neuf sur le brouillon) : « se reproduira » dit
 * qu'une relance à l'identique échouera, et c'est le contresens du run `p3`.
 *
 * Elle ne **compte pas** les échecs : le bilan compte des *constats*, et deux
 * constats peuvent dire la même panne. « 2 échecs qui se reproduiront » sous une
 * tête qui dit « 1 échec » se contredisaient (relevé par le regard neuf, sur un
 * run réel) : la ligne dit donc la nature, et le nombre de constats vit dans
 * l'onglet.
 */
export function resumeDuBilan(bilan: BilanRun): string {
  const morceaux: string[] = [];
  const echecs = bilan.constats.filter((c) => c.rubrique === RUBRIQUE_ECHEC);
  const parNature = [NATURE_DETERMINISTE, NATURE_ALEA, NATURE_INDETERMINEE]
    .map((nature) => ({
      nature,
      nombre: echecs.filter(
        (c) => (NATURES_BILAN[c.nature] ? c.nature : NATURE_INDETERMINEE) === nature,
      ).length,
    }))
    .filter(({ nombre }) => nombre > 0);
  if (parNature.length === 1) {
    morceaux.push(NATURES_BILAN[parNature[0].nature].verdict);
  } else if (parNature.length > 1) {
    morceaux.push(
      `ce qui a failli : ${parNature
        .map(({ nature, nombre }) => `${NATURES_BILAN[nature].court} (${nombre})`)
        .join(", ")}`,
    );
  }
  const aChanger = bilan.constats.filter(
    (c) => c.rubrique === RUBRIQUE_RECOMMANDATION,
  ).length;
  if (aChanger > 0) morceaux.push(`${compte(aChanger, "chose", "choses")} à changer`);
  const actes = bilan.constats.filter((c) => c.rubrique === RUBRIQUE_ACTE).length;
  if (actes > 0) {
    morceaux.push(`${compte(actes, "acte", "actes")} sorti${actes > 1 ? "s" : ""} ou accordé${actes > 1 ? "s" : ""} sans personne`);
  }
  if (morceaux.length > 0) return morceaux.join(" · ");
  return bilan.constats.length === 0
    ? "aucun constat n'a tenu contre ses pièces"
    : `${compte(bilan.constats.length, "constat", "constats")} sur pièces, aucun sur ce qui a failli`;
}

/**
 * L'état du bilan tel que la vue le lit. Un backend antérieur à #1285 ne dit pas
 * d'état : un bilan présent est rendu, un bilan nul est absent, sans raison — ce
 * que ce backend-là savait en dire.
 */
export function etatDuBilan(reponse: ReponseBilan): { etat: string; raison: string } {
  if (reponse.etat) return { etat: reponse.etat, raison: reponse.raison ?? "" };
  return {
    etat: reponse.bilan !== null ? ETAT_BILAN_RENDU : ETAT_BILAN_ABSENT,
    raison: "",
  };
}

/**
 * Pourquoi ce run n'a pas de bilan, en une phrase — celle de l'onglet. Trois cas,
 * tranchés : chaque appel qui n'a rien rendu laisse sa ligne au journal, donc sans
 * raison, c'est qu'aucune rédaction n'a été tentée (un run soldé avant que Maestro
 * ne rende des bilans) — l'écran le dit, au lieu d'hésiter entre deux causes.
 */
export function pourquoiPasDeBilan(raison: string): string {
  if (raison === RAISON_BILAN_MODELE_MUET) {
    return "Ce run n'a pas de bilan : le modèle n'a pas répondu quand il s'est soldé.";
  }
  if (raison === RAISON_BILAN_REPONSE_ILLISIBLE) {
    return "Ce run n'a pas de bilan : la réponse du modèle ne se lisait pas. L'appel a coûté, et ce coût est compté au run.";
  }
  return "Ce run n'a pas de bilan : aucun n'a été demandé à sa fin, et son journal ne garde aucune tentative de rédaction.";
}

/**
 * Les pièces qu'un constat cite, **dans l'ordre où il les cite**. Une pièce que
 * le bilan ne porte pas n'est pas inventée : la vérification du backend a déjà
 * écarté tout constat qui en citait une absente, donc ce cas ne se produit que sur
 * un bilan relu d'un backend qui aurait changé — on la tait plutôt que de la
 * montrer vide.
 */
export function piecesDuConstat(
  bilan: BilanRun,
  constat: { pieces: string[] },
): PieceBilan[] {
  const parId = new Map(bilan.pieces.map((piece) => [piece.id, piece]));
  return constat.pieces
    .map((id) => parId.get(id))
    .filter((piece): piece is PieceBilan => piece !== undefined);
}

/**
 * La pièce **est-elle** une ligne du journal ? Alors la vue rend cette ligne,
 * telle que le journal la dit ; sinon (une synthèse), son texte. Un backend
 * antérieur à #1285 ne dit pas `synthese` : l'usage n'est jamais une entrée, et
 * une pièce d'entrée n'en cite qu'une — la règle que le backend applique aussi
 * en relisant un bilan de cette époque (`Piece.depuis`).
 */
export function estUneLigneDuJournal(piece: PieceBilan): boolean {
  if (piece.synthese !== undefined) return !piece.synthese;
  return piece.famille !== FAMILLE_PIECE_USAGE && piece.entrees.length === 1;
}

/** Les entrées du journal à lire pour rendre en clair les pièces qui en sont une ligne. */
export function entreesALire(bilan: BilanRun): string[] {
  return [
    ...new Set(
      bilan.pieces.filter(estUneLigneDuJournal).flatMap((piece) => piece.entrees),
    ),
  ];
}

/** Toutes les entrées qu'un ensemble de pièces cite, sans doublon, dans l'ordre. */
export function entreesDesPieces(pieces: PieceBilan[]): string[] {
  return [...new Set(pieces.flatMap((piece) => piece.entrees))];
}

/**
 * Ce qu'on ouvre depuis le bilan (#1285) : les entrées qu'une pièce — ou toutes
 * celles d'un constat — citent, dans le journal ou dans la frise du run, et ce
 * qu'on en dit à l'arrivée (« P9 », « les 4 pièces de ce constat »).
 */
export type Citation = {
  entrees: string[];
  libelle: string;
};
