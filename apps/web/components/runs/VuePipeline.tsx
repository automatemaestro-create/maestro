"use client";

/**
 * La **vue pipeline** d'un run (#491, lot 3 de #488, docs/05 §6.12) : le flux
 * plutôt que l'inventaire — l'action en cours, ce qu'elle enchaîne, ce qui part
 * en parallèle, et sa checklist qui se coche sous les yeux.
 *
 * C'est la vue qui a motivé le chantier : on suit un run comme un pipeline
 * GitHub Actions ou un flux n8n. Le Kanban (#475) dit « combien dans quel
 * état », la barre de progression « où en est-on », celle-ci **« quoi après
 * quoi »** — et l'arbitrage entre les deux écrans est écrit dans `lib/vuesRun`,
 * pas ici.
 *
 * Quatre décisions la tiennent :
 *
 * - **Rien n'est recalculé.** `niveaux`, `niveau`/`rang`, `compartiment`,
 *   `plat`/`profondeur`/`largeur` sont **servis** par #490 ; un niveau est une
 *   colonne, un nœud une boîte, et le tri topologique n'est pas réécrit ici.
 *   Ce que ce fichier ajoute — l'attente humaine, la disponibilité, la branche
 *   courante — vit dans `lib/graphe`, hors du JSX.
 * - **Les branches parallèles sont les colonnes.** Deux tâches sans dépendance
 *   entre elles tombent au même niveau (le niveau est le *plus long chemin* qui
 *   mène au nœud, pas un rang de tri) : elles s'empilent donc dans la même
 *   colonne et se lisent comme simultanées. Une file les aurait mises l'une
 *   derrière l'autre, ce que le deuxième critère interdit.
 * - **Les arêtes sont dessinées, pas listées.** Un `<svg>` en fond, une courbe
 *   par dépendance, mesurée sur les boîtes réelles : c'est le seul moyen qu'un
 *   lien soit *orienté* et qu'on voie lequel s'allume. Aucune dépendance de
 *   rendu de graphe n'a été ajoutée — `apps/web` tient en trois paquets
 *   (`next`, `react`, `react-dom`) et le précédent local du SVG à la main est
 *   `components/GraphiqueEvolutionCout`.
 * - **Ce qui attend un humain est teinté et immobile ; ce qui travaille bat.**
 *   C'est la règle du badge d'un run (`components/runs/EtatRun`), reprise telle
 *   quelle : la pastille ne pulse que pour ce qui avance. Le troisième critère
 *   du ticket est là, et c'est le défaut d'origine du chantier — le 2026-08-14,
 *   une attente de décision est restée 53 minutes indiscernable d'un travail en
 *   cours (#355).
 *
 * ⚠ Un graphe **ne se lit pas s'il déborde** (note technique du ticket). Deux
 * réponses, et aucune ne consiste à tout montrer plus petit : le dessin vit dans
 * un cadre **borné qui défile chez lui** — jamais la page, dont le corps ne
 * défile pas horizontalement —, et une bascule cadre sur la **branche courante**,
 * c'est-à-dire ce qui tourne, ce qui y mène et ce qui en découle.
 */

import Link from "next/link";
import {
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
} from "react";

import { AvancementEtapes } from "@/components/EtapesTache";
import {
  IconeAgent,
  IconeArbitrage,
  IconeChrono,
  IconeFlecheDroite,
  IconeGraphe,
  IconePuce,
  IconeStatutAssignee,
  IconeStatutBloquee,
  IconeStatutEchec,
  IconeStatutEnCours,
  IconeStatutTerminee,
} from "@/components/Icones";
import { PanneauDetailTache } from "@/components/PanneauDetailTache";
import {
  BadgeEtat,
  Carte,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
  type Icone,
  type TonBadge,
  type TonCarte,
} from "@/components/Primitives";
import { ATTENTES } from "@/components/runs/EtatRun";
import type { Reassigner } from "@/components/SelecteurReassignation";
import { detailDe, normaliserEtapes } from "@/lib/detailTache";
import { ATTENTE_VALIDATION } from "@/lib/execution";
import { formatCout, formatDuree } from "@/lib/format";
import {
  amorcesDeBranche,
  brancheCourante,
  comptesEtapes,
  etapeCourante,
  etatsDesNoeuds,
  niveauxRetenus,
  NOEUD_A_FAIRE,
  NOEUD_ATTENTE_HUMAIN,
  NOEUD_AUTRE,
  NOEUD_BLOQUE,
  NOEUD_ECHEC,
  NOEUD_EN_COURS,
  NOEUD_PRET,
  NOEUD_TERMINE,
  type EtatNoeud,
} from "@/lib/graphe";
import { entreeParLibelle } from "@/lib/navigation";
import {
  ARETE_ATTENDUE,
  ARETE_FRANCHIE,
  ARETE_ROMPUE,
  type AreteGraphe,
  type EtatAgent,
  type GrapheRun,
  type NoeudGraphe,
  type Tache,
} from "@/lib/types";
import { useGrapheRun } from "@/lib/useGrapheRun";

/* ------------------------------------------------------------------ *
 * Les tables d'apparence — un état, une lecture
 * ------------------------------------------------------------------ */

type ApparenceNoeud = {
  libelle: string;
  ton: TonBadge;
  /**
   * La surface de la boîte, et il n'y en a que **trois** : `attention` pour ce
   * qui réclame un geste — la seule teinte du dessin, comme `fondDe` n'en
   * accorde qu'une au régime suspendu d'un run —, `creuse` pour ce qui n'a pas
   * commencé (en retrait, pas absent), `pleine` pour tout ce qui a vécu. Un
   * quatrième ton se discuterait dans `Primitives`, pas ici.
   */
  surface: TonCarte;
  icone: Icone;
  /** La pastille bat — et seulement pour ce qui travaille vraiment. */
  pulse: boolean;
};

const APPARENCE: Record<EtatNoeud, ApparenceNoeud> = {
  [NOEUD_ATTENTE_HUMAIN]: {
    libelle: "Attente humaine",
    ton: "attention",
    surface: "attention",
    icone: IconeArbitrage,
    pulse: false,
  },
  [NOEUD_EN_COURS]: {
    libelle: "En cours",
    ton: "info",
    surface: "pleine",
    icone: IconeStatutEnCours,
    pulse: true,
  },
  [NOEUD_PRET]: {
    // « Prête à partir » et non « À faire » : c'est le deuxième critère du
    // ticket. Sur un plan déclaré d'avance, la suite ne peut pas *apparaître* —
    // la boîte était là depuis le début —, elle **sort du retrait** : surface
    // pleine au lieu de creuse, et un badge qui la nomme.
    libelle: "Prête à partir",
    ton: "info",
    surface: "pleine",
    icone: IconeStatutAssignee,
    pulse: false,
  },
  [NOEUD_A_FAIRE]: {
    libelle: "À faire",
    ton: "neutre",
    surface: "creuse",
    icone: IconePuce,
    pulse: false,
  },
  [NOEUD_BLOQUE]: {
    libelle: "Bloquée",
    ton: "accent",
    surface: "pleine",
    icone: IconeStatutBloquee,
    pulse: false,
  },
  [NOEUD_TERMINE]: {
    libelle: "Terminée",
    ton: "positif",
    surface: "pleine",
    icone: IconeStatutTerminee,
    pulse: false,
  },
  [NOEUD_ECHEC]: {
    libelle: "Échec",
    ton: "alerte",
    surface: "pleine",
    icone: IconeStatutEchec,
    pulse: false,
  },
  [NOEUD_AUTRE]: {
    libelle: "Autre",
    ton: "neutre",
    surface: "creuse",
    icone: IconePuce,
    pulse: false,
  },
};

/** L'ordre de la légende : du vivant à l'acquis, comme la barre de progression. */
const ORDRE_LEGENDE: EtatNoeud[] = [
  NOEUD_ATTENTE_HUMAIN,
  NOEUD_EN_COURS,
  NOEUD_PRET,
  NOEUD_BLOQUE,
  NOEUD_TERMINE,
  NOEUD_ECHEC,
  NOEUD_A_FAIRE,
  NOEUD_AUTRE,
];

type ApparenceArete = {
  /** Le trait — `stroke-*`, et son variant sombre. */
  trait: string;
  /** La pointe, qui suit le trait (`fill-*`). */
  pointe: string;
  /** Pointillés tant que rien n'est passé ; plein une fois la main passée. */
  tirets?: string;
  /** Ce qu'un lecteur d'écran en dit, dans la liste textuelle des arêtes. */
  phrase: string;
};

const APPARENCE_ARETE: Record<string, ApparenceArete> = {
  [ARETE_FRANCHIE]: {
    trait: "stroke-emerald-500",
    pointe: "fill-emerald-500",
    phrase: "franchie",
  },
  [ARETE_ATTENDUE]: {
    trait: "stroke-neutral-300 dark:stroke-neutral-700",
    pointe: "fill-neutral-300 dark:fill-neutral-700",
    tirets: "5 5",
    phrase: "en attente",
  },
  [ARETE_ROMPUE]: {
    trait: "stroke-rose-400 dark:stroke-rose-500",
    pointe: "fill-rose-400 dark:fill-rose-500",
    tirets: "2 4",
    phrase: "rompue",
  },
};

const APPARENCE_ARETE_INCONNUE: ApparenceArete = {
  trait: "stroke-neutral-300 dark:stroke-neutral-700",
  pointe: "fill-neutral-300 dark:fill-neutral-700",
  tirets: "5 5",
  phrase: "état inconnu",
};

function apparenceArete(etat: string): ApparenceArete {
  return APPARENCE_ARETE[etat] ?? APPARENCE_ARETE_INCONNUE;
}

/**
 * Le préfixe des identifiants de marqueur SVG. Constant et non `useId()` : la
 * valeur de `useId` porte des délimiteurs (`:R1:`, `«r0»`) qui ne franchissent
 * pas proprement un `url(#…)` selon les moteurs, et il n'y a qu'un pipeline par
 * écran — la vue d'un run en monte un, jamais deux.
 */
const MARQUEUR = "pipeline-arete";

/* ------------------------------------------------------------------ *
 * Le cadrage : tout, ou la branche courante
 * ------------------------------------------------------------------ */

const CADRAGE_TOUT = "tout";
const CADRAGE_BRANCHE = "branche";
type Cadrage = typeof CADRAGE_TOUT | typeof CADRAGE_BRANCHE;

/* ------------------------------------------------------------------ *
 * La vue
 * ------------------------------------------------------------------ */

type Boite = { x: number; y: number; largeur: number; hauteur: number };

export function VuePipeline({
  runId,
  taches,
  agents,
  reassigner,
  enAttenteHumaine,
  revision,
  messageVide,
}: {
  runId: string;
  /**
   * Les tâches du run (`lib/useTachesRun`), pour **ouvrir un nœud**. Un
   * `NoeudGraphe` porte de quoi le dessiner mais pas de quoi le détailler : ni
   * description, ni liens, ni `usage`, ni ticket. Croiser par identifiant permet
   * de rouvrir le panneau qui existe déjà (#251) au lieu d'en écrire un second —
   * et un nœud dont la tâche n'a jamais démarré reste inerte, exactement comme
   * une carte de Kanban sans détail.
   */
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
  /** Les tâches dont une validation dort (`lib/execution`) — le troisième critère. */
  enAttenteHumaine: ReadonlySet<string>;
  /** Le pouls du shell : une lecture du graphe par battement. */
  revision: number;
  /** Ce que dit le graphe **vide**, nommé par l'appelant comme pour le Kanban. */
  messageVide: string;
}) {
  const { graphe, chargement, erreur } = useGrapheRun(runId, revision);
  const [cadrage, setCadrage] = useState<Cadrage>(CADRAGE_TOUT);

  if (erreur !== null && graphe === null) {
    return (
      <SectionPipeline>
        <EtatVide
          icone={IconeGraphe}
          message={`Le graphe de ce run n'a pas pu être lu : ${erreur}`}
        />
      </SectionPipeline>
    );
  }

  if (graphe === null) {
    return (
      <SectionPipeline>
        <p className="text-corps text-neutral-500">
          {chargement ? "Chargement du graphe…" : messageVide}
        </p>
      </SectionPipeline>
    );
  }

  if (graphe.nb_noeuds === 0) {
    return (
      <SectionPipeline>
        <EtatVide icone={IconeGraphe} message={messageVide} />
      </SectionPipeline>
    );
  }

  return (
    <GraphePipeline
      graphe={graphe}
      taches={taches}
      agents={agents}
      reassigner={reassigner}
      enAttenteHumaine={enAttenteHumaine}
      cadrage={cadrage}
      cadrer={setCadrage}
    />
  );
}

function SectionPipeline({
  children,
  aside,
}: {
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section data-guide="pipeline" aria-label="Pipeline du run">
      <EnTeteSection
        titre="Pipeline"
        icone={IconeGraphe}
        className="mb-2"
        aside={aside}
      />
      {children}
    </section>
  );
}

function GraphePipeline({
  graphe,
  taches,
  agents,
  reassigner,
  enAttenteHumaine,
  cadrage,
  cadrer,
}: {
  graphe: GrapheRun;
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
  enAttenteHumaine: ReadonlySet<string>;
  cadrage: Cadrage;
  cadrer: (cadrage: Cadrage) => void;
}) {
  const etats = etatsDesNoeuds(graphe, enAttenteHumaine);
  const amorces = amorcesDeBranche(graphe, etats);
  // Pas d'amorce, pas de cadrage : un run entièrement soldé n'a pas de « branche
  // courante », et en désigner une au hasard vaudrait moins que de tout montrer.
  const cadrable = amorces.length > 0;
  const retenus =
    cadrable && cadrage === CADRAGE_BRANCHE
      ? brancheCourante(graphe, amorces)
      : null;
  const niveaux = niveauxRetenus(graphe, retenus);
  const visibles = new Set(niveaux.flat());
  const aretes = graphe.aretes.filter(
    (arete) => visibles.has(arete.de) && visibles.has(arete.vers),
  );
  const parId = new Map(graphe.noeuds.map((noeud) => [noeud.id, noeud]));
  const tacheParId = new Map(taches.map((tache) => [tache.id, tache]));

  // Le panneau est tenu ici et non dans le nœud, même raison que le Kanban : il
  // est modal (une tâche à la fois) et un nœud est une carte au fond d'une
  // colonne qui défile.
  const [ouverte, setOuverte] = useState<Tache | null>(null);
  const declencheur = useRef<HTMLElement | null>(null);
  const [survole, setSurvole] = useState<string | null>(null);

  const ouvrir = (tache: Tache, depuis: HTMLElement | null) => {
    declencheur.current = depuis;
    setOuverte(tache);
  };
  const fermer = () => {
    setOuverte(null);
    declencheur.current?.focus();
  };
  // La tâche affichée suit le flux : le panneau reste ouvert sur le même nœud
  // pendant que le run avance, avec des étapes qui se cochent sous les yeux.
  const affichee =
    ouverte === null
      ? null
      : (taches.find((tache) => tache.id === ouverte.id) ?? ouverte);

  const { toile, refPour, boites } = useMesureDesBoites();

  // La légende suit le **cadrage**, pas le graphe entier : cadrer sur la branche
  // courante retire des nœuds, et laisser leur état dans la légende ferait
  // chercher des boîtes qu'on vient soi-même de masquer.
  const presents = ORDRE_LEGENDE.filter((etat) =>
    [...visibles].some((id) => etats.get(id) === etat),
  );

  return (
    <SectionPipeline
      aside={
        <BasculeCadrage
          cadrage={cadrage}
          cadrer={cadrer}
          actif={cadrable}
          nbBranche={retenus?.size ?? 0}
        />
      }
    >
      <ChiffresDuGraphe graphe={graphe} />
      <NoteDeLecture graphe={graphe} />

      {/* Le cadre borne le dessin et **défile chez lui** : c'est ce qui empêche
          le corps de la page de défiler horizontalement (#306/#308 — le banc de
          mise en page est le seul à voir cette classe de défaut). */}
      <div className="mt-3 max-h-[34rem] overflow-auto rounded-lg border border-neutral-200 bg-neutral-50 p-4 dark:border-neutral-800 dark:bg-neutral-950">
        <div ref={toile} className="relative flex w-max items-start gap-16">
          <svg
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 size-full"
          >
            <defs>
              {Object.entries(APPARENCE_ARETE).map(([etat, apparence]) => (
                <marker
                  key={etat}
                  id={`${MARQUEUR}-${etat}`}
                  viewBox="0 0 10 10"
                  refX="9"
                  refY="5"
                  markerWidth="5"
                  markerHeight="5"
                  orient="auto-start-reverse"
                >
                  <path d="M0 0 L10 5 L0 10 Z" className={apparence.pointe} />
                </marker>
              ))}
            </defs>
            {aretes.map((arete) => (
              <Arete
                key={`${arete.de}→${arete.vers}`}
                arete={arete}
                depart={boites.get(arete.de)}
                arrivee={boites.get(arete.vers)}
                survole={survole}
              />
            ))}
          </svg>

          {niveaux.map((niveau, rang) => (
            <ol
              // `relative` : les colonnes se posent **au-dessus** du calque des
              // arêtes, qui est absolu. La mesure, elle, passe par
              // `getBoundingClientRect` et ne dépend pas de ce positionnement.
              key={niveau.join("|") || rang}
              aria-label={`Niveau ${rang + 1}`}
              className="relative flex w-64 shrink-0 flex-col gap-3"
            >
              {niveau.map((id) => {
                const noeud = parId.get(id);
                if (noeud === undefined) return null;
                return (
                  <li key={id} ref={refPour(id)}>
                    <NoeudCarte
                      noeud={noeud}
                      etat={etats.get(id) ?? NOEUD_AUTRE}
                      tache={tacheParId.get(id)}
                      ouvrir={ouvrir}
                      survoler={setSurvole}
                    />
                  </li>
                );
              })}
            </ol>
          ))}
        </div>
      </div>

      {/* Le graphe **en toutes lettres**, pour qui ne voit pas les courbes : le
          `<svg>` est `aria-hidden`, un tracé n'ayant rien à annoncer. Replié par
          défaut — c'est un doublon du dessin, pas une seconde information. */}
      {aretes.length > 0 && (
        <details className="mt-2">
          <summary className="cursor-pointer text-annexe text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100">
            {`Les ${aretes.length} enchaînement${aretes.length > 1 ? "s" : ""} en toutes lettres`}
          </summary>
          <ul className="mt-1.5 space-y-1 text-annexe text-neutral-600 dark:text-neutral-400">
            {aretes.map((arete) => (
              <li key={`texte-${arete.de}→${arete.vers}`}>
                {`${parId.get(arete.de)?.titre ?? arete.de} → ${
                  parId.get(arete.vers)?.titre ?? arete.vers
                } — ${apparenceArete(arete.etat).phrase}`}
              </li>
            ))}
          </ul>
        </details>
      )}

      <Legende etats={presents} />

      {affichee !== null && (
        <PanneauDetailTache
          tache={affichee}
          agents={agents}
          reassigner={reassigner}
          fermer={fermer}
        />
      )}
    </SectionPipeline>
  );
}

/* ------------------------------------------------------------------ *
 * La mesure des boîtes — ce qui permet de tracer entre elles
 * ------------------------------------------------------------------ */

/**
 * Où se trouve chaque nœud **dans la toile**, en pixels.
 *
 * `getBoundingClientRect` et non `offsetLeft` : les colonnes sont positionnées
 * (pour passer devant le calque des arêtes), ce qui change l'`offsetParent` des
 * cartes et rendrait les décalages faux sans qu'aucun test ne le voie. La
 * différence de deux rectangles, elle, est vraie quel que soit le positionnement
 * — et invariante au défilement du cadre, les deux se déplaçant ensemble.
 *
 * L'effet tourne **à chaque rendu** et le résultat est gardé **par égalité** :
 * une mesure identique ne remplace pas l'état, donc ne déclenche pas de rendu.
 * C'est ce qui rend l'observateur de taille sûr — sans cette garde, redessiner
 * en réponse à sa propre mesure serait une boucle.
 */
function useMesureDesBoites() {
  const toile = useRef<HTMLDivElement>(null);
  const elements = useRef(new Map<string, HTMLElement>());
  const [boites, setBoites] = useState<Map<string, Boite>>(new Map());

  /**
   * L'élément qui porte ce nœud. Le rappel est **recréé à chaque rendu**, donc
   * React le rejoue (`null` puis l'élément) : c'est voulu, et c'est ce qui rend
   * l'inscription juste sans mémoïser une table de rappels — l'effet de mesure
   * passe après, et lit une table à jour.
   */
  const refPour = (id: string) => (element: HTMLElement | null) => {
    if (element === null) elements.current.delete(id);
    else elements.current.set(id, element);
  };

  const mesurer = () => {
    const conteneur = toile.current;
    if (conteneur === null) return;
    const base = conteneur.getBoundingClientRect();
    const prochaines = new Map<string, Boite>();
    for (const [id, element] of elements.current) {
      if (!conteneur.contains(element)) continue;
      const cadre = element.getBoundingClientRect();
      prochaines.set(id, {
        x: cadre.left - base.left,
        y: cadre.top - base.top,
        largeur: cadre.width,
        hauteur: cadre.height,
      });
    }
    setBoites((anciennes) =>
      memesBoites(anciennes, prochaines) ? anciennes : prochaines,
    );
  };

  // La mesure la plus fraîche, gardée par une référence : l'observateur ne
  // s'abonne qu'une fois, il ne doit pas se réabonner à chaque rendu — et il
  // couvre ce qu'aucun rendu n'annonce, la fenêtre qui change de taille.
  const derniereMesure = useRef(mesurer);

  // Sans tableau de dépendances : le dessin doit suivre **tout** ce qui déplace
  // une boîte — un nœud qui arrive, une checklist qui s'allonge, un cadrage qui
  // referme une colonne. La garde par égalité ci-dessus est ce qui rend ce choix
  // tenable.
  useLayoutEffect(() => {
    derniereMesure.current = mesurer;
    mesurer();
  });

  useLayoutEffect(() => {
    const conteneur = toile.current;
    if (conteneur === null || typeof ResizeObserver === "undefined") return;
    const observateur = new ResizeObserver(() => derniereMesure.current());
    observateur.observe(conteneur);
    return () => observateur.disconnect();
  }, []);

  return { toile, refPour, boites };
}

function memesBoites(
  anciennes: Map<string, Boite>,
  prochaines: Map<string, Boite>,
): boolean {
  if (anciennes.size !== prochaines.size) return false;
  for (const [id, boite] of prochaines) {
    const avant = anciennes.get(id);
    if (avant === undefined) return false;
    if (
      avant.x !== boite.x ||
      avant.y !== boite.y ||
      avant.largeur !== boite.largeur ||
      avant.hauteur !== boite.hauteur
    ) {
      return false;
    }
  }
  return true;
}

/* ------------------------------------------------------------------ *
 * Une arête
 * ------------------------------------------------------------------ */

/**
 * Une dépendance, tracée du **bord droit de l'amont** au **bord gauche de
 * l'aval** : le sens du flux, jamais celui de la déclaration.
 *
 * Une courbe de Bézier à tangentes horizontales plutôt qu'un segment : c'est ce
 * qui rend lisible un faisceau de liens qui partent du même nœud vers plusieurs
 * niveaux, là où des droites se superposeraient. Les points de contrôle sont à
 * mi-distance, avec un plancher — sinon deux colonnes voisines rendraient une
 * courbe si plate qu'elle passerait sous les boîtes.
 *
 * Ne rend rien tant que les deux boîtes ne sont pas mesurées : un tracé vers
 * `(0,0)` traverserait le dessin le temps d'une image.
 */
function Arete({
  arete,
  depart,
  arrivee,
  survole,
}: {
  arete: AreteGraphe;
  depart: Boite | undefined;
  arrivee: Boite | undefined;
  survole: string | null;
}) {
  if (depart === undefined || arrivee === undefined) return null;
  if (depart.largeur === 0 || arrivee.largeur === 0) return null;

  const apparence = apparenceArete(arete.etat);
  const x1 = depart.x + depart.largeur;
  const y1 = depart.y + depart.hauteur / 2;
  const x2 = arrivee.x;
  const y2 = arrivee.y + arrivee.hauteur / 2;
  const courbure = Math.max(24, (x2 - x1) / 2);
  const trace = `M ${x1} ${y1} C ${x1 + courbure} ${y1}, ${x2 - courbure} ${y2}, ${x2} ${y2}`;

  // Survoler un nœud met **ses** arêtes en avant et estompe les autres : sur un
  // faisceau dense, c'est la seule façon de suivre un lien du regard.
  const concerne =
    survole === null || survole === arete.de || survole === arete.vers;

  return (
    <path
      d={trace}
      fill="none"
      strokeWidth={concerne && survole !== null ? 2.5 : 1.5}
      strokeDasharray={apparence.tirets}
      markerEnd={`url(#${MARQUEUR}-${arete.etat})`}
      className={`${apparence.trait} transition-opacity motion-reduce:transition-none ${
        concerne ? "opacity-100" : "opacity-20"
      }`}
    />
  );
}

/* ------------------------------------------------------------------ *
 * Un nœud
 * ------------------------------------------------------------------ */

function NoeudCarte({
  noeud,
  etat,
  tache,
  ouvrir,
  survoler,
}: {
  noeud: NoeudGraphe;
  etat: EtatNoeud;
  tache: Tache | undefined;
  ouvrir: (tache: Tache, declencheur: HTMLElement | null) => void;
  survoler: (id: string | null) => void;
}) {
  const declencheur = useRef<HTMLButtonElement>(null);
  const apparence = APPARENCE[etat];
  const nom = noeud.titre || noeud.id;
  const etapes = normaliserEtapes(noeud.etapes);
  const { faites, total } = comptesEtapes(etapes);
  const courante = etapeCourante(etapes);

  // Y a-t-il seulement quelque chose à ouvrir ? Même règle que la carte du
  // Kanban : un nœud dont la tâche n'a pas démarré, ou qui n'a ni description ni
  // lien, reste strictement inerte — rien n'annonce un panneau qui serait vide.
  const ouvrable = tache !== undefined && !detailDe(tache).vide;

  const surClic = (evenement: MouseEvent<HTMLElement>) => {
    if (!ouvrable || tache === undefined) return;
    if ((evenement.target as HTMLElement).closest("a, select, option, button")) {
      return;
    }
    ouvrir(tache, declencheur.current);
  };

  const validations = entreeParLibelle(ATTENTES[ATTENTE_VALIDATION].page);

  return (
    <Carte
      densite="compacte"
      ton={apparence.surface}
      onClick={surClic}
      onMouseEnter={() => survoler(noeud.id)}
      onMouseLeave={() => survoler(null)}
      onFocusCapture={() => survoler(noeud.id)}
      onBlurCapture={() => survoler(null)}
      className={
        "text-corps" +
        (ouvrable
          ? " cursor-pointer transition motion-reduce:transition-none hover:border-neutral-300 hover:shadow dark:hover:border-neutral-700"
          : "")
      }
    >
      <div className="flex items-start justify-between gap-2">
        {ouvrable && tache !== undefined ? (
          <button
            ref={declencheur}
            type="button"
            onClick={() => ouvrir(tache, declencheur.current)}
            aria-haspopup="dialog"
            aria-label={`Ouvrir le détail de la tâche ${nom}`}
            title={noeud.id}
            className="min-w-0 flex-1 rounded text-left font-medium hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
          >
            {nom}
          </button>
        ) : (
          <p className="min-w-0 flex-1 font-medium" title={noeud.id}>
            {nom}
          </p>
        )}
        <apparence.icone className="mt-0.5 size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
      </div>

      <BadgeEtat
        ton={apparence.ton}
        pastille
        pulse={apparence.pulse}
        className="mt-1.5"
      >
        {apparence.libelle}
      </BadgeEtat>

      {/* Le geste qui lève l'attente est **ailleurs**, et le nœud y mène : même
          règle que la table `ATTENTES` (`components/runs/EtatRun`) — un
          arbitrage se tranche sur l'écran qui montre de quoi trancher, pas dans
          une boîte de 16 rem. */}
      {etat === NOEUD_ATTENTE_HUMAIN && validations && (
        <p className="mt-1.5">
          <Link
            href={validations.href}
            className={`inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-annexe font-medium text-amber-800 hover:underline dark:text-amber-300`}
          >
            {ATTENTES[ATTENTE_VALIDATION].action}
            <IconeFlecheDroite className="size-3.5 shrink-0" />
          </Link>
        </p>
      )}

      <p className="mt-1.5 flex items-center gap-1 truncate text-annexe text-neutral-500 dark:text-neutral-400">
        <IconeAgent className="size-3.5 shrink-0" />
        {noeud.agent || "Agent non assigné"}
        {noeud.role ? ` · ${noeud.role}` : ""}
      </p>

      {/* La checklist qui se coche en direct — premier critère du ticket. La
          rangée dit *combien*, la ligne dessous dit *quoi* ; la liste entière
          s'ouvre dans le panneau, un nœud n'ayant pas la place de la porter. */}
      {total > 0 && (
        <div className="mt-2">
          <AvancementEtapes
            etapes={etapes}
            faites={faites}
            taille="compacte"
          />
          <p className="chiffre mt-1 flex items-baseline gap-1.5 text-annexe text-neutral-500 dark:text-neutral-400">
            <span>{`${faites}/${total}`}</span>
            {courante !== null && (
              <span className="min-w-0 truncate">{courante.libelle}</span>
            )}
          </p>
        </div>
      )}

      {(noeud.cout_usd !== null || noeud.duree_ms !== null) && (
        <p className="chiffre mt-1 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400">
          <span>{noeud.cout_usd === null ? "" : formatCout(noeud.cout_usd)}</span>
          {noeud.duree_ms !== null && (
            <span className="inline-flex items-center gap-1">
              <IconeChrono className="size-3.5 shrink-0" />
              {formatDuree(noeud.duree_ms)}
            </span>
          )}
        </p>
      )}
    </Carte>
  );
}

/* ------------------------------------------------------------------ *
 * Ce qui entoure le dessin
 * ------------------------------------------------------------------ */

/**
 * La forme du graphe **avant de l'avoir parcouru** : combien de nœuds, combien
 * d'enchaînements, en combien de niveaux, et combien de front.
 *
 * `largeur` dit ce que le plan **autorise**, jamais ce que le run fera (le
 * parallélisme du moteur peut être plus étroit, et un run en pause ne démarre
 * rien) — d'où « jusqu'à N de front » et non « N en parallèle ».
 */
function ChiffresDuGraphe({ graphe }: { graphe: GrapheRun }) {
  const morceaux = [
    `${graphe.nb_noeuds} tâche${graphe.nb_noeuds > 1 ? "s" : ""}`,
    graphe.nb_aretes === 0
      ? "aucun enchaînement"
      : `${graphe.nb_aretes} enchaînement${graphe.nb_aretes > 1 ? "s" : ""}`,
    `${graphe.profondeur} niveau${graphe.profondeur > 1 ? "x" : ""}`,
  ];
  if (graphe.largeur > 1) morceaux.push(`jusqu'à ${graphe.largeur} de front`);
  return (
    <p className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
      {morceaux.join(" · ")}
    </p>
  );
}

/**
 * Ce qu'on a le droit de conclure de ce dessin — et il y a **deux** cas à ne pas
 * confondre (docs/05 §6.11).
 *
 * `plan_connu: false` d'abord, parce qu'il recouvre l'autre : un run qui n'a
 * jamais publié son plan rend forcément un graphe plat, et dire « aucune
 * dépendance déclarée » là où la vraie phrase est « on ne les connaît pas »
 * serait exactement l'erreur que deux booléens existent pour éviter.
 */
function NoteDeLecture({ graphe }: { graphe: GrapheRun }) {
  if (!graphe.plan_connu) {
    return (
      <p className="mt-2 text-annexe text-amber-800 dark:text-amber-300">
        Ce run n&apos;a pas publié son plan : les nœuds sont reconstruits de ses
        seules tâches vues, et aucun enchaînement n&apos;est connu. Le dessin est
        le même, ce qu&apos;on en conclut ne l&apos;est pas.
      </p>
    );
  }
  if (graphe.plat) {
    return (
      <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
        Aucune dépendance déclarée : ces tâches peuvent toutes partir en même
        temps. C&apos;est un graphe plat, pas un graphe vide.
      </p>
    );
  }
  return null;
}

/** La bascule de cadrage — tout le graphe, ou la seule branche courante. */
function BasculeCadrage({
  cadrage,
  cadrer,
  actif,
  nbBranche,
}: {
  cadrage: Cadrage;
  cadrer: (cadrage: Cadrage) => void;
  actif: boolean;
  nbBranche: number;
}) {
  const commun =
    "rounded-md px-2.5 py-1 text-annexe font-medium transition-colors motion-reduce:transition-none disabled:cursor-not-allowed disabled:opacity-50";
  const choisi =
    "bg-white text-neutral-900 shadow-sm dark:bg-neutral-800 dark:text-neutral-100";
  const autre =
    "text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100";

  return (
    <div
      role="group"
      aria-label="Cadrage du graphe"
      className="inline-flex gap-0.5 rounded-lg bg-neutral-100 p-0.5 dark:bg-neutral-900"
    >
      <button
        type="button"
        aria-pressed={cadrage === CADRAGE_TOUT}
        onClick={() => cadrer(CADRAGE_TOUT)}
        className={`${commun} ${cadrage === CADRAGE_TOUT ? choisi : autre}`}
      >
        Tout le graphe
      </button>
      <button
        type="button"
        aria-pressed={cadrage === CADRAGE_BRANCHE}
        disabled={!actif}
        onClick={() => cadrer(CADRAGE_BRANCHE)}
        // Ce que le cadrage montre — et, désactivé, pourquoi il ne montre rien
        // — a rejoint le nom accessible (#536) : le `title` d'un bouton
        // `disabled` n'apparaît pas dans plusieurs navigateurs.
        aria-label={
          actif
            ? "Branche courante — ce qui tourne, ce qui y mène et ce qui en découle"
            : "Branche courante — indisponible : aucune tâche en cours ni prête, il n'y a pas de branche à suivre"
        }
        className={`${commun} ${cadrage === CADRAGE_BRANCHE ? choisi : autre}`}
      >
        Branche courante
        {cadrage === CADRAGE_BRANCHE && nbBranche > 0 && (
          <span className="chiffre ml-1 font-normal">{nbBranche}</span>
        )}
      </button>
    </div>
  );
}

/**
 * La légende, **bornée à ce que ce graphe contient** : lister huit états dont
 * six absents ferait chercher des boîtes qui n'existent pas.
 */
function Legende({ etats }: { etats: EtatNoeud[] }) {
  if (etats.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
      {etats.map((etat) => (
        <li key={etat}>
          <BadgeEtat
            ton={APPARENCE[etat].ton}
            contour
            pastille
            pulse={APPARENCE[etat].pulse}
          >
            {APPARENCE[etat].libelle}
          </BadgeEtat>
        </li>
      ))}
    </ul>
  );
}
