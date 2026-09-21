/**
 * Le guide de prise en main de la Control Tower (#122, lot 6 de #116) : la
 * définition de la visite guidée et l'état qui décide de son déclenchement.
 *
 * Le contenu vit ici, à part du composant qui le rend : ajouter une étape se
 * fait dans une seule liste, sans toucher au mécanisme de surbrillance.
 *
 * Même contrat que `lib/theme.ts` et `lib/preferences.ts`, et pour la même
 * raison : la visite se lance depuis deux endroits — automatiquement à la
 * première visite, et à la demande depuis le menu d'aide (`MenuAide`), qui ne
 * connaît pas le composant. Le stockage retient qu'elle a été vue, l'événement
 * en porte la relance.
 *
 * ## Ce que #940 a changé, et pourquoi
 *
 * La visite de #122 décrivait une version passée du produit : elle énumérait six
 * sections quand le menu en portait neuf — Runs, Intégrations et Journal
 * manquaient —, et **aucune** de ses sept étapes ne nommait le chat, seule porte
 * d'entrée depuis #470/#484. On la finissait sans savoir démarrer (constat G7 du
 * retex du 2026-09-11). Une visite qui décrit une version passée du produit est
 * pire qu'aucune : elle apprend à chercher au mauvais endroit.
 *
 * Trois propriétés à ne pas défaire, la troisième étant le livrable :
 *
 * - **la visite mène au premier run.** Elle dépense ses étapes sur le chemin
 *   *chat → objectif → run → ce qui attend un geste*, et se termine sur le
 *   composeur du chat, curseur posé dedans — pas devant le bouton d'aide ;
 * - **l'inventaire du menu tient en UNE étape** (`ETAPE_DU_MENU`), et son texte
 *   est **dérivé de `MENU`**. C'est le parti pris 1 de la veille (d'après
 *   Grafana Play, dont l'accueil n'énumère aucune de ses ~20 entrées, et
 *   Excalidraw, qui nomme la périphérie sans lui donner d'étape) ; le parti pris
 *   2 en donne le geste : le menu **se cite depuis sa source**. Une prose qui le
 *   recopierait serait fausse au premier ajout — c'est exactement la dérive
 *   qu'on répare ;
 * - **ce qu'on fait de chaque écran est déclaré** (`TRAITEMENT_DES_ECRANS`) :
 *   une étape à lui, ou seulement nommé. Le jour où une entrée de menu est
 *   ajoutée ou retirée, `ecartsDeVisite` le rend **visible** (rouge dans
 *   `tests/guide.test.tsx`) et quelqu'un tranche. Ce qui est automatique est la
 *   détection du manque, jamais le verdict — même partage que #562 et #714.
 *
 * ⚠ **Aucun chemin n'est écrit en dur ici** : une étape déclare l'**écran**
 * qu'elle ouvre, par son libellé de menu, et `entreeParLibelle` en donne le
 * chemin (règle de #191). Le jour où une page déménage, la visite suit ; le jour
 * où elle disparaît, l'étape n'ouvre plus rien et `ecartsDeVisite` le dit.
 */

import { MENU, entreeParLibelle } from "@/lib/navigation";

/** Clé localStorage — même espace de noms que le thème (#118) et le repli (#121). */
export const CLE_GUIDE_VU = "maestro.guide.vu";

const EVENEMENT_LANCEMENT = "maestro:guide-lancer";

export type EtapeGuide = {
  /** Identifiant stable — clé de rendu, et repère pour les tests (lot 8). */
  id: string;
  titre: string;
  texte: string;
  /**
   * Les ancres candidates, par ordre de préférence : la première **présente et
   * visible** porte la surbrillance. Plusieurs plutôt qu'une seule, parce que
   * la cible idéale peut manquer — le panneau des validations disparaît quand
   * rien n'attend d'arbitrage, le coût cumulé est masqué sous `sm` —, et
   * qu'une étape doit rester ancrée sur du réel plutôt que de sauter.
   */
  ancres: string[];
  /**
   * L'entrée de menu que l'étape présente, par son **libellé** (#940). C'est
   * elle qui donne `chemin` ci-dessous, et elle que `ecartsDeVisite` confronte
   * au menu.
   */
  ecran?: string;
  /** Page que l'étape présente ; la visite y navigue d'elle-même. Dérivé d'`ecran`. */
  chemin?: string;
  /**
   * La visite s'achève-t-elle en posant le curseur dans cette ancre ? (#940)
   *
   * C'est le parti pris 3 de la veille, d'après la procédure pas à pas de VS
   * Code pour le web, dont chaque étape porte un bouton qui **fait** le geste
   * plutôt que de le décrire. Ici le geste n'a pas besoin d'un contrôle de
   * plus : c'est « Terminer » qui le fait, en rendant la main dans le composeur
   * au lieu de la rendre à ce qui avait le focus avant la visite. Sans quoi
   * « mène à un premier objectif composé » ne serait qu'une phrase.
   *
   * ⚠ Seulement quand la visite est **menée à son terme**. Quittée en route
   * (Échap, « Quitter »), l'utilisateur a tranché : on lui rend son focus.
   */
  rendreLaMain?: boolean;
};

/**
 * Ce que la visite fait d'un écran du menu (#940) :
 *
 * - `"etape"` — il a une étape à lui, qui l'ouvre et l'explique ;
 * - `"cite"` — il est **nommé** par l'étape du menu, sans étape à lui.
 *
 * Les deux sont des réponses ; ce qui n'en est pas une, c'est l'absence. Un
 * écran qui n'a pas de ligne ici fait rougir `ecartsDeVisite`, et c'est tout
 * l'objet du troisième critère de #940 : le jour où une entrée de menu est
 * ajoutée ou retirée, ce qui ne suit pas se voit.
 */
export type TraitementDEcran = "etape" | "cite";

/**
 * Le traitement de chaque écran, **relu à chaque ajout ou retrait** au menu.
 *
 * Ce n'est pas une recopie de `MENU` : c'est la **décision** prise sur chacune
 * de ses entrées, et c'est elle qu'il faut reprendre quand le menu bouge. Le
 * choix d'aujourd'hui suit le parti pris 1 de la veille — les étapes vont au
 * chemin du premier run, le reste est nommé une fois.
 */
export const TRAITEMENT_DES_ECRANS: Record<string, TraitementDEcran> = {
  "Tableau de bord": "etape",
  Chat: "etape",
  Runs: "etape",
  Agents: "cite",
  Intégrations: "cite",
  "Coûts & analytics": "cite",
  Validations: "cite",
  Journal: "cite",
  Paramètres: "cite",
};

/**
 * L'identifiant de l'étape qui **énumère le menu en entier**. L'inventaire tient
 * là, et nulle part ailleurs : c'est ce qui permet à `ecartsDeVisite` de savoir
 * où vérifier qu'aucune entrée n'est omise.
 */
export const ETAPE_DU_MENU = "navigation";

/** « a, b et c » — une énumération, dérivée d'une liste et jamais recopiée. */
function enumerer(mots: string[]): string {
  if (mots.length <= 1) return mots.join("");
  return `${mots.slice(0, -1).join(", ")} et ${mots[mots.length - 1]}`;
}

/** Les libellés du menu, dans son ordre. */
const libellesDuMenu = (): string[] => MENU.map((entree) => entree.libelle);

/** Les écrans que la visite nomme sans leur donner d'étape — dérivés, jamais listés. */
const ecransCites = (): string[] =>
  libellesDuMenu().filter((libelle) => TRAITEMENT_DES_ECRANS[libelle] === "cite");

/** Une étape telle qu'on l'écrit : l'écran par son libellé, jamais par son chemin. */
type DefinitionEtape = Omit<EtapeGuide, "chemin">;

/** Le chemin d'une étape, résolu par le menu (#191) — `undefined` si la page n'existe plus. */
function monter(definition: DefinitionEtape): EtapeGuide {
  const entree =
    definition.ecran === undefined ? undefined : entreeParLibelle(definition.ecran);
  return { ...definition, chemin: entree?.href };
}

/**
 * La visite : le chemin du premier run, du chat à ce qu'il produit (#940, choix
 * consigné sur le ticket — variante A, retenue par le regard neuf contre « un
 * écran par étape » et « deux étapes et la main »). Chaque étape désigne un
 * élément réel du shell (#117) ou d'une page, marqué par un attribut
 * `data-guide`.
 */
export const ETAPES_GUIDE: EtapeGuide[] = [
  {
    id: "bienvenue",
    titre: "Bienvenue dans la Control Tower",
    texte:
      "Le poste de pilotage de Maestro : vos agents, leurs tâches et leurs coûts, en temps réel. La visite prend moins d'une minute et se termine sur votre premier objectif — vous pouvez la quitter à tout moment (Échap) et la relancer depuis l'aide.",
    ancres: ['[data-guide="marque"]'],
  },
  {
    // L'inventaire du menu tient ici, **dérivé de `MENU`** : recopier les
    // libellés en prose est ce qui a fait dériver la visite de #122 pendant
    // quatre milestones.
    id: ETAPE_DU_MENU,
    titre: "La navigation",
    texte: `Toutes les sections sont ici : ${enumerer(
      libellesDuMenu(),
    )}. Une entrée par intention — un agent se consulte d'un seul endroit, ses facettes tenant en onglets sur sa fiche. Le bouton en haut à gauche replie la barre en un simple rail d'icônes.`,
    ancres: ['[data-guide="navigation"]'],
  },
  {
    id: "chat",
    ecran: "Chat",
    titre: "Le chat : par où tout commence",
    texte:
      "C'est la porte d'entrée du produit. On ne remplit pas un formulaire : on écrit ce qu'on veut obtenir, en une phrase — « ajoute la pagination à la liste des projets ». L'orchestration répond par un brief que vous relisez, corrigez, puis approuvez ; rien n'est décomposé avant votre accord.",
    ancres: ['[data-guide="composeur"]', '[data-guide="contenu"]'],
  },
  {
    id: "runs",
    ecran: "Runs",
    titre: "Le run, et ce qu'il devient",
    texte:
      "Un brief approuvé ouvre un run : Maestro le découpe en tâches et les confie aux agents. Cette page les liste tous, en cours comme passés ; en ouvrir un donne ses tâches en colonnes, sa frise d'activité et ses décisions. Tout se met à jour en direct, sans recharger la page.",
    ancres: ['[data-guide="contenu"]'],
  },
  {
    id: "arbitrages",
    ecran: "Tableau de bord",
    titre: "Ce qui attend un geste de vous",
    texte:
      "Une action sensible met la tâche en pause et attend votre arbitrage, avec le contexte pour trancher. Le tableau de bord les porte en tête, la cloche vous les suit de page en page, et Validations en garde l'historique. Approuver fait reprendre, Refuser annule proprement.",
    // `validations` d'abord, la cloche en repli : le panneau disparaît du
    // tableau de bord quand rien n'attend d'arbitrage, la cloche reste.
    ancres: ['[data-guide="validations"]', '[data-guide="notifications"]'],
  },
  {
    id: "aide",
    titre: "Et si vous voulez revoir tout ça",
    // Le reste du menu est nommé ici — **dérivé de `TRAITEMENT_DES_ECRANS`**,
    // pour la raison qui vaut à l'étape du menu : une liste recopiée dérive.
    texte: `Ce bouton d'aide relance la visite quand vous le souhaitez. Le reste du menu — ${enumerer(
      ecransCites(),
    )} — s'ouvre au moment où vous en aurez besoin.`,
    ancres: ['[data-guide="aide"]'],
  },
  {
    // La dernière étape rend la main **là où on agit** : le composeur du chat,
    // curseur dedans. C'est ce qui fait de « mène à un premier objectif
    // composé » autre chose qu'une phrase (voir `rendreLaMain`).
    id: "premier-objectif",
    ecran: "Chat",
    titre: "À vous de jouer",
    texte:
      "Décrivez ici ce que vous voulez faire faire, puis envoyez. Vous n'avez rien à préparer : le brief se discute ensuite, et vous gardez la main jusqu'au lancement.",
    ancres: ['[data-guide="composeur"]', '[data-guide="contenu"]'],
    rendreLaMain: true,
  },
].map(monter);

/**
 * Les écarts entre la visite et le menu (#940) — les quatre façons dont la
 * première peut cesser de décrire le second.
 *
 * Toutes les listes sont vides quand tout va bien ; chacune nomme ce qui ne suit
 * plus, pour que le test n'ait pas à le déduire d'un compte.
 */
export type EcartsDeVisite = {
  /** Au menu, sans ligne dans `TRAITEMENT_DES_ECRANS` : personne n'a tranché. */
  sansTraitement: string[];
  /** Dans `TRAITEMENT_DES_ECRANS`, plus au menu : une décision sur un écran disparu. */
  traitementOrphelin: string[];
  /** Déclarés `"etape"`, qu'aucune étape n'ouvre : la promesse n'est pas tenue. */
  promisSansEtape: string[];
  /** Des étapes qui présentent un écran que le menu ne porte plus. */
  etapesHorsMenu: string[];
  /** Au menu, absents du texte de l'étape qui énumère : la visite les omet. */
  nonNommes: string[];
};

/**
 * La confrontation, **dans les deux sens** — c'est la méthode de la frontière
 * écrans ↔ `navigation.ts` de `tests/test_retex_utilisateur.py`, reprise ici sur
 * la visite : une liste recopiée dérive au premier écran ajouté, et un écran
 * retiré du menu laisse une étape qui ouvre une page qui se dérobe.
 *
 * Pure et entièrement paramétrée : c'est ce qui permet de la **prouver sur un
 * échantillon fautif** avant de la lâcher sur le vrai menu (règle de #534 et de
 * #830 — un motif mal branché rend un ✓ sur une question jamais posée).
 */
export function ecartsDeVisite(
  menu: readonly { libelle: string }[],
  traitement: Readonly<Record<string, TraitementDEcran>>,
  etapes: readonly EtapeGuide[],
  texteDuMenu: string,
): EcartsDeVisite {
  const libelles = menu.map((entree) => entree.libelle);
  const declares = Object.keys(traitement);
  const ouverts = etapes
    .map((etape) => etape.ecran)
    .filter((ecran): ecran is string => ecran !== undefined);

  return {
    sansTraitement: libelles.filter((libelle) => !declares.includes(libelle)),
    traitementOrphelin: declares.filter((libelle) => !libelles.includes(libelle)),
    promisSansEtape: libelles.filter(
      (libelle) => traitement[libelle] === "etape" && !ouverts.includes(libelle),
    ),
    etapesHorsMenu: [...new Set(ouverts)].filter(
      (ecran) => !libelles.includes(ecran),
    ),
    nonNommes: libelles.filter((libelle) => !texteDuMenu.includes(libelle)),
  };
}

/**
 * La visite a-t-elle déjà été vue ? Un stockage indisponible (navigation
 * privée, cookies bloqués) répond « oui » : sans persistance, répondre « non »
 * relancerait la visite à **chaque** chargement de page. Elle reste accessible
 * à la demande depuis le menu d'aide.
 */
export function lireGuideVu(): boolean {
  try {
    return window.localStorage.getItem(CLE_GUIDE_VU) === "1";
  } catch {
    return true;
  }
}

/** Retient que la visite a été menée à son terme — ou quittée en route. */
export function marquerGuideVu(): void {
  try {
    window.localStorage.setItem(CLE_GUIDE_VU, "1");
  } catch {
    // Voir `lireGuideVu` : l'absence de persistance ne casse pas la visite,
    // elle ne fait que retirer son déclenchement automatique.
  }
}

/** Relance la visite depuis le début, d'où qu'on la demande. */
export function lancerGuide(): void {
  window.dispatchEvent(new CustomEvent(EVENEMENT_LANCEMENT));
}

/** S'abonne aux demandes de relance. Rend la fonction de retrait. */
export function ecouterLancementGuide(rappel: () => void): () => void {
  const surLancement = () => rappel();
  window.addEventListener(EVENEMENT_LANCEMENT, surLancement);
  return () => window.removeEventListener(EVENEMENT_LANCEMENT, surLancement);
}
