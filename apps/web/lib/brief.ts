/**
 * Ce que l'écran de validation du brief (#322) sait du brief **hors du JSX** :
 * quels runs attendent quelqu'un, comment un brief se donne à corriger, comment
 * savoir s'il l'a été, et comment relire les allers-retours de clarification déjà
 * joués.
 *
 * Ces règles vivent ici et pas dans les composants pour la raison habituelle du
 * dépôt : elles sont **partagées** — le badge de la cloche, le panneau du tableau
 * de bord et l'écran lui-même posent tous la question « ce run attend-il un
 * geste ? », et trois formulations finiraient par diverger.
 *
 * Depuis #483 elles servent **deux** surfaces : l'écran `/brief` (#322) et le fil
 * du cadrage (`components/chat/CadrageDansLeFil`), où le même brief se lit, se
 * corrige et se tranche dans la conversation. C'est la raison d'être du module qui
 * se vérifie — le déménagement n'a rien eu à réécrire de ce qui est ici, et les
 * deux surfaces ne peuvent pas diverger sur ce qu'est un brief corrigé, un run en
 * attente ou un tour de clarification.
 */

import {
  EVENEMENT_BRIEF_QUESTIONS,
  EVENEMENT_BRIEF_REPONSES,
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
  type Brief,
  type Evenement,
  type ResumeExecution,
} from "./types";

/**
 * Les deux états où un run est arrêté **sur son brief** (#320, #321) : en vol,
 * mais rien ne bougera sans un geste. Rassemblés parce que la question se pose à
 * plusieurs endroits et qu'aucun n'a à connaître les deux noms.
 *
 * ⚠ Ce n'est **plus** le pendant exact de `STATUTS_EXECUTION_EN_ATTENTE` côté
 * backend, qui en compte trois depuis #571 : `en_attente_arbitrage` en est
 * délibérément absent ici. Ce module sert les écrans du **brief** — le panneau du
 * tableau de bord, la file de la cloche, l'écran de validation —, et un run arrêté
 * sur un arbitrage de tâche n'y a rien à faire : il n'a aucun brief à relire, et
 * l'y lister enverrait quelqu'un chercher une décision de cadrage là où c'est une
 * action sensible qui attend. La question générale — « ce run attend-il
 * quelqu'un ? », les trois attentes confondues — se pose à `causeDAttente`
 * (`lib/execution`), qui est l'endroit qui la traite.
 */
export const STATUTS_ATTENTE_HUMAINE: readonly string[] = [
  EXECUTION_EN_ATTENTE_BRIEF,
  EXECUTION_EN_ATTENTE_REPONSES,
];

/** Ce run est-il arrêté sur son brief (décision ou réponses) ? */
export function attendUnHumain(execution: ResumeExecution): boolean {
  return STATUTS_ATTENTE_HUMAINE.includes(execution.statut);
}

/**
 * Les runs suspendus, **le plus ancien d'abord**.
 *
 * L'ordre est la moitié du signal : deux briefs en attente ne se valent pas, et
 * celui qui dort depuis trois heures est celui qu'on a oublié. Même parti pris
 * que la file de revue des MR côté outillage — l'ancienneté d'abord, parce que
 * c'est elle qui distingue « à traiter » de « en train d'être traité ».
 *
 * `attente_depuis` (#321) est l'horodatage juste ; à défaut — run publié hors de
 * l'API, trace d'avant ce lot — on retombe sur `debut`, qui borne au moins le
 * run. Un run sans aucun des deux passe en dernier plutôt que de remonter en tête
 * par une chaîne vide.
 */
export function runsEnAttente(executions: ResumeExecution[]): ResumeExecution[] {
  return executions
    .filter(attendUnHumain)
    .slice()
    .sort((a, b) => {
      const [ga, gb] = [attenteDepuis(a), attenteDepuis(b)];
      return ga < gb ? -1 : ga > gb ? 1 : 0;
    });
}

/**
 * Depuis quand ce run attend, au mieux de ce que le backend en dit.
 *
 * Le repli est une date **au-delà de toute date ISO réelle** plutôt qu'une chaîne
 * vide : comparées comme des chaînes, les dates ISO se trient dans l'ordre du
 * temps, et `""` remonterait en tête — un run dont on ignore l'ancienneté
 * passerait devant celui qui attend depuis trois heures.
 */
export function attenteDepuis(execution: ResumeExecution): string {
  return execution.attente_depuis || execution.debut || "9999";
}

/**
 * Les six sections **listes** du brief, dans l'ordre où l'écran les rend — celui
 * du schéma partagé (`packages/shared/schemas/brief.schema.json`), pour qu'un
 * lecteur qui connaît l'un reconnaisse l'autre.
 *
 * `objectif` n'y est pas : c'est une chaîne, pas une liste, et il ouvre l'écran à
 * part. `questions` ferme la marche parce qu'elle ne décrit pas le travail — elle
 * dit ce que le Chef de projet n'a pas pu trancher seul.
 */
export const SECTIONS_LISTE = [
  { cle: "perimetre", libelle: "Périmètre" },
  { cle: "hors_perimetre", libelle: "Hors-périmètre" },
  { cle: "contraintes", libelle: "Contraintes" },
  { cle: "criteres_acceptation", libelle: "Critères d'acceptation" },
  { cle: "hypotheses", libelle: "Hypothèses" },
  { cle: "questions", libelle: "Questions" },
] as const satisfies readonly { cle: keyof Brief; libelle: string }[];

/** La clé d'une des six sections listes du brief. */
export type CleSectionListe = (typeof SECTIONS_LISTE)[number]["cle"];

/**
 * Le brief **en cours d'édition** : les mêmes sections, mais chaque liste tenue
 * comme un texte d'une entrée par ligne.
 *
 * Une ligne par entrée plutôt qu'un champ par puce, et c'est un choix de fond :
 * corriger un brief, c'est réécrire des phrases, en ajouter, en supprimer — pas
 * remplir un formulaire. Un tableau de champs numérotés transformerait « retire
 * ces deux critères » en quatre gestes de gestion de liste, et c'est précisément
 * la friction qui fait approuver sans lire.
 */
export type BriefEdite = { objectif: string } & Record<CleSectionListe, string>;

/** Une liste rendue éditable : une entrée par ligne, dans l'ordre. */
export function versTexte(entrees: string[]): string {
  return entrees.join("\n");
}

/**
 * Le texte d'une section relu en liste : lignes rognées, lignes vides écartées.
 *
 * Écarter une ligne vide n'est pas une perte silencieuse — une ligne vide n'est
 * pas une entrée, c'est une frappe. En revanche les **doublons sont conservés**
 * et partiront tels quels : le schéma partagé les refuse (`uniqueItems`), et
 * c'est l'API qui doit le dire en 422 plutôt qu'un nettoyage d'ici qui
 * modifierait en douce ce que quelqu'un a écrit.
 */
export function versListe(texte: string): string[] {
  return texte
    .split("\n")
    .map((ligne) => ligne.trim())
    .filter((ligne) => ligne.length > 0);
}

/** Le brief tel qu'il se donne à corriger. */
export function editionDepuis(brief: Brief): BriefEdite {
  return {
    objectif: brief.objectif,
    perimetre: versTexte(brief.perimetre),
    hors_perimetre: versTexte(brief.hors_perimetre),
    contraintes: versTexte(brief.contraintes),
    criteres_acceptation: versTexte(brief.criteres_acceptation),
    hypotheses: versTexte(brief.hypotheses),
    questions: versTexte(brief.questions),
  };
}

/** Le brief tel qu'il repartira — les sept sections, aucune omise (#318). */
export function briefDepuisEdition(edite: BriefEdite): Brief {
  return {
    objectif: edite.objectif.trim(),
    perimetre: versListe(edite.perimetre),
    hors_perimetre: versListe(edite.hors_perimetre),
    contraintes: versListe(edite.contraintes),
    criteres_acceptation: versListe(edite.criteres_acceptation),
    hypotheses: versListe(edite.hypotheses),
    questions: versListe(edite.questions),
  };
}

/**
 * Ce brief a-t-il été **touché** depuis qu'on l'a ouvert ?
 *
 * La comparaison porte sur le brief *normalisé* (celui qui repartirait), pas sur
 * le texte saisi : ajouter une ligne vide puis la retirer n'est pas une
 * correction. C'est ce qui décide de ce que l'approbation envoie — le brief
 * corrigé, ou `null` pour « tel quel » (#320) — et la distinction n'est pas
 * cosmétique : `null` fait retenir au moteur le brief qu'il a lui-même proposé,
 * sans le faire retraverser la validation de schéma.
 */
export function estCorrige(origine: Brief, edite: BriefEdite): boolean {
  const propose = briefDepuisEdition(edite);
  if (propose.objectif !== origine.objectif) return true;
  return SECTIONS_LISTE.some(
    ({ cle }) => versTexte(propose[cle]) !== versTexte(origine[cle]),
  );
}

/**
 * Ce que le schéma partagé exige et qu'un formulaire peut vider — le miroir
 * **minimal** de `packages/shared/schemas/brief.schema.json`.
 *
 * On ne revalide pas le schéma ici, et c'est délibéré : le backend le fait, il
 * le fait sur la version qui fait foi, et sa réponse 422 nomme ce qui cloche.
 * Ce contrôle-ci ne couvre que les trois manques qu'un humain produit en
 * effaçant un champ, et sert à **désactiver** le bouton en le disant — pas à
 * remplacer l'API. Les doublons (`uniqueItems`), eux, partent exprès : les
 * nettoyer en silence modifierait ce que quelqu'un a écrit.
 *
 * Il vit ici depuis #483, où le brief se corrige à **deux** endroits : l'écran et
 * le fil. Recopié, il aurait fini par désactiver le bouton d'un côté et pas de
 * l'autre — la version la plus laxiste des deux décidant alors de ce qui part
 * chercher un 422.
 */
export function manquesDuBrief(edite: BriefEdite): string[] {
  const propose = briefDepuisEdition(edite);
  const manques: string[] = [];
  if (propose.objectif.length === 0) manques.push("un objectif");
  if (propose.perimetre.length === 0) {
    manques.push("au moins une entrée de périmètre");
  }
  if (propose.criteres_acceptation.length === 0) {
    manques.push("au moins un critère d'acceptation");
  }
  return manques;
}

/**
 * Qui parle quand le cadrage s'affiche dans le fil (#483).
 *
 * Le brief est publié par l'**Orchestrateur** côté bus (`ROLE_BRIEF`,
 * `controltower/brief.py`), mais toute l'interface le nomme « Chef de projet »
 * depuis #321 — « Le Chef de projet attend vos réponses », au panneau comme à la
 * cloche. C'est ce nom-là qui va dans les bulles : une conversation où l'auteur
 * change de nom selon l'écran est une conversation avec deux interlocuteurs.
 */
export const AUTEUR_CADRAGE = "Chef de projet";

/**
 * Le **libellé de menu** de la page où un brief se tranche — « Chat » depuis
 * #483, « Valider le brief » avant lui.
 *
 * Trois surfaces acheminent vers cette page sans décider : le panneau du tableau
 * de bord, la cloche et la table `ATTENTES` de la vue d'un run (§2.1). Le nom vit
 * ici parce que ces trois-là doivent bouger **ensemble** : un renvoi resté sur
 * l'ancien écran mènerait vers un chemin que #484 redirige, et un renvoi vers une
 * entrée de menu retirée rend `undefined`, donc `null`, donc un run suspendu que
 * plus rien ne montre — le défaut même que le critère 3 du lot interdit.
 *
 * C'est un **libellé** et non un chemin, comme partout depuis #191 : le renvoi
 * suit sa page si elle déménage, et ne s'allume pas vers une page absente.
 */
export const PAGE_DU_CADRAGE = "Chat";

/**
 * Un aller-retour de clarification déjà joué (#321), reconstitué de la trace du
 * run : les questions posées, et les réponses qui leur ont été faites.
 *
 * `reponses` est vide tant que personne n'a répondu — c'est le tour en cours.
 */
export type TourClarification = {
  tour: number;
  questions: string[];
  reponses: string[];
  horodatage: string;
};

/**
 * Les allers-retours de clarification d'un run, du plus ancien au plus récent.
 *
 * Ils s'apparient **deux à deux** : `brief.questions` porte le brief dont on pose
 * les questions, `brief.reponses` les réponses qui suivent. Rien d'autre ne les
 * relie — le brief est régénéré en entier à chaque tour, donc une question n'a
 * pas d'identité stable (#318) —, et c'est cette règle d'appariement qui justifie
 * que la fonction existe plutôt qu'un `filter` dans un composant.
 *
 * La trace est servie **du plus récent au plus ancien** par le détail du run ;
 * on la remet dans l'ordre du temps ici, parce qu'une conversation se lit dans le
 * sens où elle a eu lieu. Un `brief.reponses` sans `brief.questions` qui le
 * précède est ignoré : il n'y aurait aucune question à mettre en face.
 */
export function toursDeClarification(
  evenements: Evenement[],
): TourClarification[] {
  const tours: TourClarification[] = [];
  for (const evenement of [...evenements].reverse()) {
    if (evenement.type === EVENEMENT_BRIEF_QUESTIONS) {
      tours.push({
        tour: evenement.tour ?? tours.length + 1,
        questions: evenement.brief?.questions ?? [],
        reponses: [],
        horodatage: evenement.horodatage,
      });
    } else if (evenement.type === EVENEMENT_BRIEF_REPONSES) {
      const dernier = tours[tours.length - 1];
      if (dernier !== undefined && dernier.reponses.length === 0) {
        dernier.reponses = evenement.reponses ?? [];
      }
    }
  }
  return tours.filter((tour) => tour.questions.length > 0);
}
