"use client";

/**
 * Le **bilan** d'un run (#1285, lot 3 de #1281, docs/05 §6.23) : la sixième
 * lecture de la vue d'un run.
 *
 * Le run réel `3fe501fc0878` (projet `p3`, 2026-09-24) a échoué trois fois à
 * l'identique pendant que le moteur présumait un aléa, et rien à l'écran ne disait
 * ce qui avait failli ni pourquoi. Le lot 2 (#1284) fait rendre, à la fin de tout
 * run, un bilan **sur pièces** : un modèle juge les pièces du journal, l'exécution
 * vérifie que chaque pièce citée existe. Cet écran le montre. La question à
 * laquelle un coup d'œil doit répondre : **« qu'est-ce que ce run a fait de
 * travers, pourquoi, et que faire ? »**
 *
 * ## La forme, et d'où elle vient
 *
 * Trois places ont été rendues sur la vraie stack et jugées sur pièces par le
 * sous-agent `regard-neuf` (#980, #1009) contre les références capturées par la
 * veille — le bloc « Annotations » d'un run GitHub Actions, les « Annotations »
 * d'une build Buildkite, une enquête terminée de Datadog Bits. Le choix, les deux
 * écartées et les partis pris sont consignés sur le ticket (`## Veille de
 * conception`, `## Variante retenue`) ; voici ce qu'ils engagent ici :
 *
 * - **le travers d'abord** : ce qui a failli, ce qu'il faut changer, les actes
 *   sortis ou accordés sans personne, la consommation sans résultat, puis ce qui a
 *   été livré (`lib/bilan`, `RUBRIQUES_BILAN`) — une rubrique vide ne s'affiche
 *   pas ;
 * - **la nature d'un échec se lit à sa forme et à son mot**, jamais à sa seule
 *   teinte : un badge à glyphe — « Se reproduira », « Aléa », « Nature
 *   indéterminée » —, parce que c'est là que `p3` s'est trompé ;
 * - **chaque constat ouvre ses pièces, et une pièce mène à sa ligne dans la
 *   trace** : sous le constat, ses pièces **en clair** — la ligne du journal telle
 *   que le journal la dit (`resumeEvenement`), jamais le texte écrit pour le
 *   modèle —, et un geste par pièce qui ouvre le journal, ou la frise quand elle y
 *   figure, **sur ces entrées-là** ;
 * - **ce qui a été écarté reste visible, en retrait** : les constats que la
 *   vérification a refusés, repliés au pied, chacun avec sa raison — la
 *   vérification se vérifie ;
 * - **aucun bloc de plus** : cette lecture vit dans la bascule, qui est une
 *   `<nav>` et n'occupe aucune des trois places (docs/30 §4.2). Les rubriques sont
 *   des sous-parties (`h3`), pas des sections.
 *
 * ## Ce que l'écran ne fait pas
 *
 * Il ne **rejuge** rien : ni la nature d'un échec, ni la rubrique d'un constat, ni
 * ce qu'une pièce prouve — tout vient du bilan servi. Il ne **tranche** rien non
 * plus ([docs/32](../../../../docs/32-decision-cran-orchestrateur.md) §b) : une
 * recommandation se lit, une personne la décide. Celle qui vise le playbook d'un
 * agent renvoie à sa fiche, où l'analyse d'échecs existante (#139) propose.
 */

import { useId, useState, type ReactNode } from "react";

import { useEcranEnPanne } from "@/components/BanniereErreurApi";
import {
  IconeActivite,
  IconeChevronBas,
  IconeHypothese,
  IconeJournal,
  IconeMonnaie,
  IconeObjectif,
  IconePermissions,
  IconeFichier,
  IconeReprise,
  IconeStatutEchec,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
  LienRenvoi,
  type Icone,
} from "@/components/Primitives";
import { cheminOnglet } from "@/lib/agents";
import type { PorteeProjet } from "@/lib/api";
import {
  NATURES_BILAN,
  entreesALire,
  entreesDesPieces,
  estUneLigneDuJournal,
  etatDuBilan,
  piecesDuConstat,
  pourquoiPasDeBilan,
  rubriquesDuBilan,
  type Citation,
} from "@/lib/bilan";
import { resumeEvenement } from "@/lib/evenements";
import { formatDateHeure, formatHeure } from "@/lib/format";
import { evenementDepuisEntree } from "@/lib/journal";
import {
  ETAT_BILAN_ATTENDU,
  ETAT_BILAN_EN_REDACTION,
  ETAT_BILAN_RENDU,
  NATURE_ALEA,
  NATURE_DETERMINISTE,
  RUBRIQUE_ACTE,
  RUBRIQUE_CONSOMMATION,
  RUBRIQUE_ECHEC,
  RUBRIQUE_LIVRE,
  RUBRIQUE_RECOMMANDATION,
  type BilanRun as Bilan,
  type ConstatBilan,
  type EntreeJournal,
  type PieceBilan,
} from "@/lib/types";
import type { BilanDuRun } from "@/lib/useBilanRun";
import { useEntreesCitees } from "@/lib/useBilanRun";
import { useFriseRun } from "@/lib/useFriseRun";

/**
 * L'icône du sujet de chaque rubrique — décorative, l'intitulé porte le sens.
 *
 * « Ce qui a été livré » porte un fichier, et non le ✓ cerclé de « Terminée » :
 * sur un run en échec, la rubrique dit souvent que **rien** n'a été livré, et un
 * glyphe de réussite y contredirait le texte (relevé par le regard neuf).
 */
const ICONES_RUBRIQUE: Record<string, Icone> = {
  [RUBRIQUE_ECHEC]: IconeStatutEchec,
  [RUBRIQUE_RECOMMANDATION]: IconeObjectif,
  [RUBRIQUE_ACTE]: IconePermissions,
  [RUBRIQUE_CONSOMMATION]: IconeMonnaie,
  [RUBRIQUE_LIVRE]: IconeFichier,
};

/**
 * Le glyphe de chaque nature : la **forme** qui double le mot et la teinte — un
 * échec qui se reproduira est barré comme un échec, un aléa tourne comme une
 * relance, une nature indéterminée est une question.
 */
const GLYPHES_NATURE: Record<string, Icone> = {
  [NATURE_DETERMINISTE]: IconeStatutEchec,
  [NATURE_ALEA]: IconeReprise,
};

/** Où ouvrir une pièce : la lecture, et les entrées à y montrer. */
export type OuvrirPieces = (lecture: "journal" | "frise", citation: Citation) => void;

export function BilanRun({
  portee,
  runId,
  bilan: lecture,
  solde,
  titresTaches,
  revision,
  ouvrir,
}: {
  portee: PorteeProjet;
  runId: string;
  /** La lecture du bilan, faite **une fois** par la vue pour sa tête et cet onglet. */
  bilan: BilanDuRun;
  /** Le run a-t-il rendu son verdict (`estSolde`) ? Un run en vol attend son bilan. */
  solde: boolean;
  /**
   * Le titre de chaque tâche du run d'après sa liste des tâches — le repli, quand
   * la réponse du bilan ne sert pas les siens (`taches`).
   */
  titresTaches: ReadonlyMap<string, string>;
  revision: number;
  ouvrir: OuvrirPieces;
}) {
  const { reponse, chargement, erreur } = lecture;
  // Le magasin perdu en route compte aussi (#1217) : cette lecture-ci n'a
  // peut-être pas encore échoué, mais son « pas de bilan » ne dirait rien.
  const enPanne = useEcranEnPanne(erreur);
  const { etat, raison } = reponse
    ? etatDuBilan(reponse)
    : { etat: solde ? "" : ETAT_BILAN_ATTENDU, raison: "" };
  const bilan = reponse?.bilan ?? null;

  let corps: ReactNode;
  if (!solde || etat === ETAT_BILAN_ATTENDU) {
    corps = (
      <EtatVide
        icone={IconeObjectif}
        message="Le bilan se rend à la fin du run, sur les pièces de son journal : ce qui a tenu, ce qui a failli et pourquoi, ce qu'il faut changer."
      />
    );
  } else if (bilan !== null && etat === ETAT_BILAN_RENDU) {
    corps = (
      <ContenuBilan
        portee={portee}
        runId={runId}
        bilan={bilan}
        // Les titres servis avec le bilan l'emportent : ils ne dépendent ni du
        // moment où la liste des tâches arrive, ni d'une panne qui l'aurait vidée
        // (relevé par le regard neuf, sur la vraie stack).
        titresTaches={
          new Map([...titresTaches, ...Object.entries(reponse?.taches ?? {})])
        }
        revision={revision}
        ouvrir={ouvrir}
      />
    );
  } else if (enPanne && reponse === null) {
    corps = (
      <EtatVide
        icone={IconeObjectif}
        message="Bilan indisponible — la lecture a échoué."
      />
    );
  } else if (chargement || reponse === null) {
    corps = (
      <EtatVide icone={IconeObjectif} message="Lecture du bilan de ce run…" />
    );
  } else if (etat === ETAT_BILAN_EN_REDACTION) {
    corps = (
      <EtatVide
        icone={IconeObjectif}
        message="Bilan en cours de rédaction — Maestro relit les pièces du journal de ce run. Il s'affichera ici dès qu'il sera rendu."
      />
    );
  } else {
    corps = (
      <EtatVide
        icone={IconeObjectif}
        message={pourquoiPasDeBilan(raison)}
        releve="Le journal et la frise de ce run restent ses pièces."
      >
        <Bouton
          variante="contour"
          ton="neutre"
          taille="petite"
          icone={IconeJournal}
          onClick={() => ouvrir("journal", { entrees: [], libelle: "" })}
        >
          Ouvrir le journal du run
        </Bouton>
      </EtatVide>
    );
  }

  return (
    <section aria-label="Bilan du run">
      <EnTeteSection
        titre="Bilan"
        icone={IconeObjectif}
        className="mb-2"
        aside={bilan !== null && etat === ETAT_BILAN_RENDU && <Compte bilan={bilan} />}
      />
      {corps}
    </section>
  );
}

/**
 * Le compte, à droite du titre : combien de constats ont tenu, combien ont été
 * écartés. Rien quand il n'y a rien à compter — l'état vide le dit déjà.
 */
function Compte({ bilan }: { bilan: Bilan }) {
  const tenus = bilan.constats.length;
  const ecartes = bilan.ecartes.length;
  if (tenus === 0 && ecartes === 0) return null;
  return (
    <p className="chiffre text-annexe text-texte-secondaire">
      {tenus} constat{tenus > 1 ? "s" : ""} sur pièces
      {ecartes > 0 && ` · ${ecartes} écarté${ecartes > 1 ? "s" : ""}`}
    </p>
  );
}

function ContenuBilan({
  portee,
  runId,
  bilan,
  titresTaches,
  revision,
  ouvrir,
}: {
  portee: PorteeProjet;
  runId: string;
  bilan: Bilan;
  titresTaches: ReadonlyMap<string, string>;
  revision: number;
  ouvrir: OuvrirPieces;
}) {
  // Les lignes du journal que les pièces **sont** — lues par leur identifiant,
  // où qu'elles soient dans le journal (#1285).
  const { parId } = useEntreesCitees(portee, runId, entreesALire(bilan), revision);
  // Ce que la frise montre de ce run : une pièce dont une entrée y figure s'y
  // ouvre aussi. Lue ici plutôt que devinée — la frise ne retient que les statuts
  // de tâche et les messages, et proposer de l'y ouvrir sur une entrée qu'elle ne
  // porte pas mènerait à un écran sans rien d'éclairé.
  const { frise } = useFriseRun(runId, revision);
  const dansLaFrise = new Set(frise?.entrees.map((entree) => entree.id) ?? []);
  const rubriques = rubriquesDuBilan(bilan);

  return (
    <div className="space-y-5">
      {rubriques.length === 0 ? (
        <EtatVide
          icone={IconeObjectif}
          message="Aucun constat n'a tenu contre ses pièces : le bilan n'en garde aucun qui en cite une existante."
        />
      ) : (
        rubriques.map((rubrique) => (
          <div key={rubrique.cle}>
            <IntituleRubrique
              intitule={rubrique.intitule}
              icone={ICONES_RUBRIQUE[rubrique.cle]}
            />
            <Carte balise="div" densite="aucune">
              <ol className="divide-y divide-bord">
                {rubrique.constats.map((constat, rang) => (
                  <LigneConstat
                    key={`${rubrique.cle}-${rang}`}
                    bilan={bilan}
                    constat={constat}
                    titresTaches={titresTaches}
                    entrees={parId}
                    dansLaFrise={dansLaFrise}
                    ouvrir={ouvrir}
                  />
                ))}
              </ol>
            </Carte>
          </div>
        ))
      )}

      <Ecartes bilan={bilan} />

      {/* Ce que le modèle a lu, dit en clair : le journal entier, et ce que le
          budget a laissé dehors s'il a mordu (#1284). Une borne muette ferait
          passer un run bavard pour un run sobre. */}
      {/* « 10 pièces tirées des 8 entrées » se lisait comme une erreur de compte
          (relevé deux fois par le regard neuf) : les pièces sont les lignes du
          journal **et** des synthèses — le coût, l'usage d'une tâche, sa
          checklist. La phrase le dit. */}
      <p className="text-annexe text-texte-secondaire">
        Rendu sur {bilan.pieces_offertes} pièce{bilan.pieces_offertes > 1 ? "s" : ""} :
        les lignes du journal de ce run, lu en entier ({bilan.entrees_lues} entrée
        {bilan.entrees_lues > 1 ? "s" : ""}), et des synthèses sur son coût et ses tâches
        {bilan.pieces_laissees > 0
          ? ` — ${bilan.pieces_laissees} laissée${bilan.pieces_laissees > 1 ? "s" : ""} de côté par le budget de lecture`
          : ""}
        {bilan.fin ? `, à sa fin du ${formatDateHeure(bilan.fin)}` : ""}.
      </p>
    </div>
  );
}

/**
 * L'intitulé d'une rubrique — un `h3` **en casse normale**, et non l'en-tête de
 * section en petites capitales grises : sous le titre « Bilan », qui en porte
 * déjà, cinq intitulés du même style se liraient comme cinq sections sœurs, et la
 * hiérarchie tiendrait à la seule indentation (relevé sur la capture de la vraie
 * stack). Deux pas du barème, déjà employés : `text-corps` en gras, l'icône du
 * sujet en second plan.
 */
function IntituleRubrique({ intitule, icone: Icone }: { intitule: string; icone: Icone }) {
  return (
    <h3 className="mb-2 flex items-center gap-2 text-corps font-semibold text-texte">
      <Icone aria-hidden="true" className="size-4 shrink-0 text-texte-secondaire" />
      {intitule}
    </h3>
  );
}

/**
 * Un constat : sa nature quand c'est un échec, ce qu'il dit, la tâche qu'il nomme,
 * et ses pièces.
 *
 * Les pièces sont **repliées** sous le constat, et le geste « les ouvrir toutes
 * dans le journal » reste à côté, toujours visible : la question « sur quoi
 * s'appuie-t-il ? » se pose à la demande, celle de « où le vérifier ? » se règle
 * d'un clic sans déplier. Le dépli est un bouton à `aria-expanded`, comme la
 * ligne d'activité du journal (`LigneActivite`) : un `<details>` ne pouvait pas
 * poser sa liste sous le geste voisin, et les deux colonnes de gestes ne
 * s'alignaient pas.
 */
function LigneConstat({
  bilan,
  constat,
  titresTaches,
  entrees,
  dansLaFrise,
  ouvrir,
}: {
  bilan: Bilan;
  constat: ConstatBilan;
  titresTaches: ReadonlyMap<string, string>;
  entrees: ReadonlyMap<string, EntreeJournal>;
  dansLaFrise: ReadonlySet<string>;
  ouvrir: OuvrirPieces;
}) {
  const pieces = piecesDuConstat(bilan, constat);
  const nature = constat.rubrique === RUBRIQUE_ECHEC ? NATURES_BILAN[constat.nature] : undefined;
  // Un run d'une seule tâche ne la renomme pas sous chaque constat : la même ligne
  // neuf fois n'apprend rien (relevé par le regard neuf, d'après ce que la veille
  // laissait à GitHub Actions). Elle ne dit quelque chose que s'il y a à choisir.
  const seule = titresTaches.size === 1 && titresTaches.has(constat.tache);
  const tache =
    constat.tache && !seule ? (titresTaches.get(constat.tache) ?? constat.tache) : "";
  const toutes = entreesDesPieces(pieces);
  const nombre = pieces.length;
  const [ouvert, setOuvert] = useState(false);
  const panneau = useId();

  return (
    <li className="px-3 py-2.5">
      <p className="text-corps break-words">
        {nature && (
          <BadgeEtat
            ton={nature.ton}
            icone={GLYPHES_NATURE[constat.nature] ?? IconeHypothese}
            className="mr-1.5 align-middle"
          >
            {nature.libelle}
          </BadgeEtat>
        )}
        {constat.texte}
      </p>
      {tache && (
        <p className="mt-0.5 text-annexe text-texte-secondaire">Tâche : {tache}</p>
      )}
      {constat.revision_playbook && constat.agent && (
        <LienRenvoi
          className="mt-1"
          renvoi={{
            href: cheminOnglet(constat.agent, "playbook"),
            libelle: `Playbook de ${constat.agent} — l'analyse d'échecs peut y proposer une révision`,
          }}
        />
      )}
      {nombre > 0 && (
        <>
          {/* Le compte et le geste sur **une** ligne, la liste **dessous**, sur
              toute la largeur : les gestes de chaque pièce tombent ainsi au même
              bord droit que « Voir dans le journal » (relevé par le regard neuf,
              quand la liste vivait dans un `<details>` à côté du geste). */}
          <div className="mt-1 flex flex-wrap items-center justify-between gap-x-3">
            <button
              type="button"
              aria-expanded={ouvert}
              aria-controls={panneau}
              onClick={() => setOuvert((avant) => !avant)}
              className={`inline-flex items-center gap-1 ${CIBLE_MINIMALE} rounded-controle text-annexe text-texte-secondaire hover:text-texte focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info`}
            >
              {/* Le chevron du dépli, couché quand c'est replié — sans transition,
                  donc sans `motion-reduce:` à poser. */}
              <IconeChevronBas
                aria-hidden="true"
                className={`size-3.5 shrink-0 ${ouvert ? "" : "-rotate-90"}`}
              />
              {nombre} pièce{nombre > 1 ? "s" : ""}
            </button>
            {toutes.length > 0 && (
              <Bouton
                variante="discret"
                ton="info"
                taille="petite"
                icone={IconeJournal}
                onClick={() =>
                  ouvrir("journal", {
                    entrees: toutes,
                    libelle:
                      nombre > 1
                        ? `Les ${nombre} pièces d'un constat`
                        : `La pièce ${pieces[0].id}`,
                  })
                }
              >
                Voir dans le journal
              </Bouton>
            )}
          </div>
          <ul
            id={panneau}
            hidden={!ouvert}
            className="mt-1.5 space-y-1.5 border-l border-bord pl-3 text-annexe"
          >
            {pieces.map((piece) => (
              <LignePiece
                key={piece.id}
                piece={piece}
                entrees={entrees}
                dansLaFrise={piece.entrees.some((id) => dansLaFrise.has(id))}
                ouvrir={ouvrir}
              />
            ))}
          </ul>
        </>
      )}
    </li>
  );
}

/**
 * Une pièce, **en clair** : la ligne du journal qu'elle est, telle que le journal
 * la dit — l'heure, la phrase, le détail que le moteur ou l'agent a consigné —,
 * ou, pour une synthèse, la phrase que le bilan a composée. Puis les gestes qui
 * l'ouvrent, en bout de ligne comme le « log » de Buildkite.
 *
 * Tant que la ligne n'est pas lue (ou si elle ne l'est pas), la pièce rend son
 * texte : il est technique, mais c'est le fait — une pièce ne disparaît pas parce
 * qu'une lecture a échoué.
 */
function LignePiece({
  piece,
  entrees,
  dansLaFrise,
  ouvrir,
}: {
  piece: PieceBilan;
  entrees: ReadonlyMap<string, EntreeJournal>;
  dansLaFrise: boolean;
  ouvrir: OuvrirPieces;
}) {
  const entree = estUneLigneDuJournal(piece) ? entrees.get(piece.entrees[0]) : undefined;
  const evenement = entree ? evenementDepuisEntree(entree) : null;
  // Une synthèse se dit par son libellé (des phrases, la tâche par son titre) ;
  // son texte — celui que le modèle a lu — n'est qu'un repli, pour un bilan rendu
  // avant que le libellé n'existe.
  const phrase = evenement ? resumeEvenement(evenement) : piece.libelle || piece.texte;
  // Le détail n'est redit que s'il apprend autre chose que la phrase : pour une
  // activité ou un arbitrage, la phrase **est** le détail (`lib/evenements`).
  const detail =
    evenement && evenement.detail && !phrase.includes(evenement.detail)
      ? evenement.detail
      : "";
  const citation = { entrees: piece.entrees, libelle: `La pièce ${piece.id}` };

  return (
    <li className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
      {/* L'identifiant et l'heure en **colonnes de largeur fixe**, l'heure laissée
          vide pour une synthèse (elle n'est pas une ligne du journal) : d'une pièce à
          l'autre, la phrase part du même bord — relevé par le regard neuf, quand une
          pièce sans heure commençait sa phrase plus à gauche que ses voisines. */}
      <div className="flex min-w-60 flex-1 items-baseline gap-x-2">
        {/* L'identifiant que le constat cite (« P9 ») : la poignée par laquelle le
            journal, une fois ouvert, dit de quelle pièce il montre les entrées. */}
        <span className="chiffre w-8 shrink-0 font-mono text-micro text-texte-secondaire">
          {piece.id}
        </span>
        <span className="w-18 shrink-0">
          {entree && (
            <time
              dateTime={entree.horodatage}
              title={formatDateHeure(entree.horodatage)}
              className="chiffre font-mono text-texte-secondaire"
            >
              {formatHeure(entree.horodatage)}
            </time>
          )}
        </span>
        <div className="min-w-0 flex-1">
          <p className="break-words text-texte">{phrase}</p>
          {detail && (
            <p className="mt-0.5 line-clamp-3 break-words text-texte-secondaire">{detail}</p>
          )}
        </div>
      </div>
      {/* Une colonne de largeur fixe, calée à droite, le journal toujours en
          dernier : d'une pièce à l'autre, « Journal » tombe au même endroit, que la
          frise soit proposée ou non — l'œil descend une colonne de gestes au lieu
          de les chercher ligne à ligne. */}
      {piece.entrees.length > 0 && (
        <span className="flex w-44 shrink-0 justify-end gap-1">
          {dansLaFrise && (
            <Bouton
              variante="discret"
              ton="info"
              taille="petite"
              icone={IconeActivite}
              aria-label={`Voir la pièce ${piece.id} dans la frise`}
              onClick={() => ouvrir("frise", citation)}
            >
              Frise
            </Bouton>
          )}
          <Bouton
            variante="discret"
            ton="info"
            taille="petite"
            icone={IconeJournal}
            aria-label={`Voir la pièce ${piece.id} dans le journal`}
            onClick={() => ouvrir("journal", citation)}
          >
            Journal
          </Bouton>
        </span>
      )}
    </li>
  );
}

/**
 * Ce que la vérification a refusé, **en retrait** (veille de #1285, d'après
 * Datadog Bits, qui garde ses hypothèses écartées grisées dans l'arbre) : replié
 * au pied, chaque constat avec sa raison. Le montrer comme un constat serait le
 * défaut que la vérification corrige ; le taire rendrait la vérification
 * invérifiable.
 */
function Ecartes({ bilan }: { bilan: Bilan }) {
  if (bilan.ecartes.length === 0) return null;
  const nombre = bilan.ecartes.length;
  return (
    <details className="text-annexe text-texte-secondaire">
      <summary className="w-fit cursor-pointer rounded-controle select-none hover:text-texte focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-info">
        {nombre} constat{nombre > 1 ? "s" : ""} écarté{nombre > 1 ? "s" : ""} faute de pièce
      </summary>
      <ul className="mt-1.5 space-y-1 border-l border-bord pl-3">
        {bilan.ecartes.map((ecarte, rang) => (
          <li key={rang} className="break-words">
            {ecarte.texte || "(sans texte)"}
            <span className="block">Écarté : {ecarte.raison}.</span>
          </li>
        ))}
      </ul>
    </details>
  );
}
