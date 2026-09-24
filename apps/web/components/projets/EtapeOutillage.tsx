"use client";

/**
 * L'étape d'outillage : ce qui suit immédiatement le choix de la racine (#1034,
 * docs/37 §4.6, docs/38).
 *
 * « Sur un projet, que ce soit un nouveau projet ou existant, la première étape
 * est la génération de l'outillage. » Déclarer un projet s'arrêtait au choix du
 * dossier ; il s'arrête maintenant sur cette carte, qui répond à une question et
 * une seule : **quel outillage mon projet va-t-il recevoir, et pourquoi ?**
 *
 * ## Deux origines, un seul rendu
 *
 * - un projet **existant** est analysé (`GET …/outillage/analyse`, #1030) : ce
 *   qu'on voit est ce que la lecture de la racine recommande ;
 * - un projet **neuf** n'a rien à analyser : Maestro pose les questions qui
 *   décident de son outillage (`POST …/outillage/questionnaire`, #1031), et les
 *   réponses produisent la **même** recommandation structurée
 *   (`POST …/outillage/recommandation`).
 *
 * Les deux chemins se rejoignent sur `RecommandationOutillage`, et c'est pour
 * cela qu'il n'y a qu'une liste ici. Les questions, elles, ne sont pas rendues
 * ici : `QuestionDOutillage` (#1031) est **reprise telle quelle** du fil de
 * conversation — sa forme a été tranchée sur pièces par la veille et le regard
 * neuf de son propre lot, et la redessiner pour l'écran de création donnerait
 * deux formes à la même question.
 *
 * ## La forme de la liste vient d'une veille et d'un choix rendu sur pièces
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1034 : trois
 * variantes rendues sur la vraie stack, jugées par un regard qui n'en était pas
 * l'auteur (#980). La retenue est **A — l'inventaire à plat**. Ce qu'elle
 * tranche, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **une liste à plat, pas de groupes et pas de repli** — d'après *Vercel*
 *   (Framework Settings : une ligne par réglage, la valeur déjà décidée par la
 *   détection) et *Renovate* (« Detected Package Files » : un fichier par ligne
 *   avec ce qui l'a trahi). La variante qui regroupait par nature a été écartée
 *   parce qu'**aucune** des trois références ne regroupe, et qu'elle payait un
 *   niveau de titres pour une hauteur perdue ;
 * - **chaque ligne porte sa raison et sa preuve**, jamais une légende commune.
 *   La variante qui repliait la liste derrière un résumé (la plus compacte, et
 *   celle que *GitHub* soutient) a été écartée sur pièces : repliée, l'écran ne
 *   portait plus **aucune** raison, et la moitié « et pourquoi ? » de la question
 *   du ticket restait sans réponse sur le chemin par défaut ;
 * - **tout arrive retenu, corriger c'est décocher** — d'après *GitHub*
 *   (« select or deselect that language » dans une configuration déjà écrite), et
 *   contre l'interrupteur « Override » de Vercel, refusé par la veille : un
 *   élément d'outillage se garde ou se retire, il ne se ressaisit pas ;
 * - **une ligne retirée perd son aplat ET son libellé est barré** : l'état ne
 *   tient jamais à la seule couleur (docs/30 §1, filet a11y) ;
 * - **le report est une issue nommée, à égalité avec la validation** — d'après
 *   *Renovate*, dont la PR d'onboarding écrit les deux issues dans le même
 *   paragraphe (« To activate […] merge. To disable […] close this PR unmerged »).
 *
 * Trois corrections demandées par le regard neuf sont tenues ici : le **compte
 * en tête** (« N fichiers seront écrits »), repris de la variante repliée pour
 * que *quel outillage* ne demande pas d'atteindre le bouton, natures accordées ;
 * un **geste d'ensemble** en tête de liste ; et la contradiction du pied levée —
 * la carte du projet n'annonce plus « Outillage reporté » pendant que l'étape est
 * ouverte sur lui (c'est `ListeProjets` qui le tient).
 *
 * ## Ce que l'écran ne décide pas
 *
 * Rien n'est écrit tant qu'on n'a pas validé, et ce qui est écrit l'est par la
 * génération (#1033) : l'écran lui passe les **chemins retenus**, pas des
 * fichiers — et, pour un projet neuf, les **réponses** d'où la liste a été
 * dérivée (#1100). Sans elles le serveur rederivait la liste de l'analyse d'une
 * racine vide, et les skills cochés n'étaient pas écrits. Un chemin retenu que
 * la génération ne reconnaît pas revient nommé dans le rapport, jamais tu.
 * Un projet versionné y gagne en plus l'accord humain sur la fusion
 * (docs/24 §2.4), et la requête attend cette décision — d'où un bouton qui reste
 * occupé, sans délai annoncé.
 *
 * Depuis #1160, la génération **joue** chaque commande avant de l'écrire, dans une
 * copie du projet : la requête dure donc le temps d'une installation et d'une suite
 * de tests, et la promesse le dit avant le geste. Le rapport rend ensuite le verdict
 * de chaque commande, dans la forme retenue sur pièces (`VerificationsOutillage`).
 */

import { useCallback, useEffect, useState } from "react";

import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
} from "@/components/Primitives";
import { QuestionDOutillage } from "@/components/chat/QuestionDOutillage";
import {
  analyserOutillage,
  genererOutillage,
  questionOutillage,
  recommandationOutillage,
  reporterOutillage,
} from "@/lib/api";
// Les libellés de natures et « tout arrive retenu » vivent dans `lib/outillage`
// depuis #1104 : la conclusion du questionnaire au pied du fil les rend aussi, et
// deux tables de pluriels finiraient par ne plus s'accorder.
import {
  comptesParNature,
  libelleNature,
  retenueParDefaut,
} from "@/lib/outillage";
import type {
  ChoixOutillage,
  EntreeOutillage,
  Projet,
  QuestionOutillage,
  RapportGenerationOutillage,
  RecommandationOutillage,
  RefusProjet,
} from "@/lib/types";

import { refusDepuis, RefusMotive } from "./ExplorateurDossiers";
import {
  CompteVerifications,
  ListeVerifications,
} from "./VerificationsOutillage";

/**
 * Une entrée recommandée : ce qu'on écrira, **pourquoi**, et d'où ça sort.
 *
 * Les trois lignes sont les trois champs que l'API sert déjà (`nom`, `raison`,
 * `justification.chemin`) : aucune n'est déduite ici. C'est ce qui rend une
 * recommandation contestable — on peut ouvrir le fichier cité et vérifier.
 */
function LigneEntree({
  entree,
  retenue,
  basculer,
  fige,
}: {
  entree: EntreeOutillage;
  retenue: boolean;
  basculer: () => void;
  fige: boolean;
}) {
  // L'association passe par `htmlFor`/`id` et non par le seul emboîtement : le
  // Le chemin fait un identifiant unique par construction — deux entrées ne
  // visent jamais le même fichier.
  const idCase = `outillage-${entree.chemin}`;
  return (
    <li>
      {/* Une **grille** plutôt que des colonnes emboîtées, et c'est le lint a11y
          qui l'a imposé : `label-has-associated-control` cherche le libellé de la
          case dans les deux premiers niveaux du `<label>`, et trois `<span>`
          empilés l'y cachaient. Ici le nom est un enfant direct ; la disposition,
          elle, ne bouge pas — chaque morceau est placé à sa ligne et à sa
          colonne. */}
      <label
        htmlFor={idCase}
        className={[
          "grid cursor-pointer grid-cols-[auto_auto_1fr] items-start gap-x-2 gap-y-0.5 rounded-carte border border-bord p-3",
          // L'état retenu/retiré se lit à **deux** signaux : l'aplat (ici) et le
          // libellé barré (plus bas). Un seul des deux serait la couleur seule,
          // que le filet a11y refuse.
          retenue ? "bg-surface" : "bg-surface-creuse",
        ].join(" ")}
      >
        <input
          id={idCase}
          type="checkbox"
          checked={retenue}
          disabled={fige}
          onChange={basculer}
          className="col-start-1 row-start-1 mt-1 size-4 shrink-0 rounded-controle border-bord-fort"
        />
        <span
          className={[
            "col-start-2 row-start-1 text-corps font-medium",
            retenue ? "text-texte" : "text-texte-secondaire line-through",
          ].join(" ")}
        >
          {entree.nom}
        </span>
        <span className="col-start-3 row-start-1 flex flex-wrap items-center gap-2">
          <BadgeEtat contour>{libelleNature(entree.type)}</BadgeEtat>
          {/* `deja-present` est une information, pas un silence : sans lui, un
              fichier déjà écrit et un fichier jamais envisagé se
              ressembleraient (#1030). */}
          {entree.etat === "deja-present" && (
            <BadgeEtat ton="info" contour>
              déjà présent
            </BadgeEtat>
          )}
          {entree.etat === "a-completer" && (
            <BadgeEtat ton="attention" contour>
              à compléter
            </BadgeEtat>
          )}
        </span>
        <span className="col-start-2 col-end-4 row-start-2 min-w-0 break-words text-annexe text-texte-secondaire">
          {entree.raison}
        </span>
        <span className="col-start-2 col-end-4 row-start-3 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-micro text-texte-secondaire">
          <code className="font-mono break-all">{entree.chemin}</code>
          {entree.justification && (
            <span>
              d&apos;après{" "}
              <code className="font-mono break-all">
                {entree.justification.chemin}
              </code>
            </span>
          )}
        </span>
      </label>
    </li>
  );
}

/**
 * Ce que la liste dit **avant** qu'on la lise : combien de fichiers, de quelles
 * natures, et la promesse de ne rien écraser.
 *
 * Correction demandée par le regard neuf : sans elle, le seul chiffre de l'écran
 * était celui du bouton, qu'on n'atteint qu'après avoir fait défiler toute la
 * liste — donc « quel outillage ? » n'avait pas de réponse d'un coup d'œil, là
 * où « pourquoi ? » en avait huit.
 */
function EnTeteListe({
  entrees,
  retenus,
  toutBasculer,
  fige,
}: {
  entrees: EntreeOutillage[];
  retenus: Set<string>;
  toutBasculer: (retenir: boolean) => void;
  fige: boolean;
}) {
  const gardes = entrees.filter((e) => retenus.has(e.chemin));
  const retires = entrees.length - gardes.length;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-corps text-texte">
          {gardes.length === 0 ? (
            <span className="text-attention-texte">
              Rien ne sera écrit — tout a été retiré.
            </span>
          ) : (
            <>
              <strong>
                {gardes.length} fichier{gardes.length > 1 ? "s" : ""}
              </strong>{" "}
              {gardes.length > 1 ? "seront écrits" : "sera écrit"} dans votre
              dossier : {comptesParNature(gardes)}.{" "}
              {retires > 0 && (
                <span className="text-attention-texte">
                  {retires} retiré{retires > 1 ? "s" : ""} par vous.
                </span>
              )}
            </>
          )}
        </p>
        <button
          type="button"
          disabled={fige}
          onClick={() => toutBasculer(gardes.length === 0)}
          className="min-h-6 text-annexe text-texte-secondaire underline underline-offset-2 hover:text-texte"
        >
          {gardes.length === 0 ? "tout remettre" : "tout retirer"}
        </button>
      </div>
      {/* La promesse de docs/38 §4.2, **avant** le geste et non dans le rapport
          qui le suit : c'est ce qu'on veut savoir au moment de laisser un outil
          écrire chez soi. Relevé manquant par le regard neuf — la correction
          demandait le compte *et* cette phrase, et seul le compte y était. */}
      <p className="text-annexe text-texte-secondaire">
        Rien n&apos;est écrasé : un fichier déjà là que Maestro n&apos;a pas
        écrit n&apos;est pas touché, et un fichier modifié depuis n&apos;est pas
        réécrit. Chaque commande est jouée dans une copie du projet avant
        d&apos;être écrite, et son verdict vous sera montré.
      </p>
    </div>
  );
}

/**
 * Ce que la génération a fait — quatre listes plutôt qu'un « ok » (docs/38 §4.2), et,
 * depuis #1160, le verdict de chaque commande écrite (`VerificationsOutillage`).
 */
function RapportGeneration({ rapport }: { rapport: RapportGenerationOutillage }) {
  const { ecrits, refuses, ignores } = rapport.rapport;
  const inconnus = rapport.retenus_inconnus ?? [];
  const verifications = rapport.rapport.verifications ?? [];
  return (
    <div className="flex flex-col gap-2">
      <p className="text-corps text-texte">
        <strong>
          {ecrits.length} fichier{ecrits.length > 1 ? "s" : ""} écrit
          {ecrits.length > 1 ? "s" : ""}
        </strong>{" "}
        dans <code className="font-mono break-all">{rapport.rapport.cible}</code>
        {rapport.regime === "branche" && " (après votre accord sur la fusion)"}.
      </p>
      {/* Ce qui n'a PAS été écrasé se nomme, toujours : c'est la liste qu'une
          personne doit relire, et la taire ferait passer une non-écriture pour
          une écriture. */}
      {refuses.length > 0 && (
        <p className="text-annexe text-attention-texte">
          {refuses.length} fichier{refuses.length > 1 ? "s" : ""} non écrasé
          {refuses.length > 1 ? "s" : ""} (modifié
          {refuses.length > 1 ? "s" : ""} depuis) —{" "}
          {refuses.map((chemin) => (
            <code key={chemin} className="font-mono break-all">
              {chemin}{" "}
            </code>
          ))}
          : la version neuve attend dans{" "}
          <code className="font-mono">.maestro/outillage/refuses/</code>.
        </p>
      )}
      {/* Ce qu'on avait gardé et que la génération n'a pas reconnu (#1100) : la
          liste lue et celle écrite ont divergé. Le taire ferait lire « rien de
          refusé » là où des éléments cochés manquent. */}
      {inconnus.length > 0 && (
        <p className="text-annexe text-attention-texte">
          {inconnus.length} élément{inconnus.length > 1 ? "s" : ""} retenu
          {inconnus.length > 1 ? "s" : ""} non écrit
          {inconnus.length > 1 ? "s" : ""} —{" "}
          {inconnus.map((chemin) => (
            <code key={chemin} className="font-mono break-all">
              {chemin}{" "}
            </code>
          ))}
          : la génération ne les recommande plus.
        </p>
      )}
      {ignores.length > 0 && (
        <p className="text-annexe text-texte-secondaire">
          {ignores.length} fichier{ignores.length > 1 ? "s" : ""} laissé
          {ignores.length > 1 ? "s" : ""} tel{ignores.length > 1 ? "s" : ""} quel
          {ignores.length > 1 ? "s" : ""} : le projet les portait déjà et Maestro
          ne les possède pas.
        </p>
      )}
      {rapport.rapport.refus !== "" && (
        <p className="text-annexe text-alerte-texte" role="alert">
          Rien n&apos;a été écrit : {rapport.rapport.refus}
        </p>
      )}
      {/* La variante A de #1160, retenue sur pièces : la liste à plat, dans l'ordre
          joué, l'échec déployé sur place — voir `VerificationsOutillage`. */}
      {verifications.length > 0 && (
        <div className="mt-2 flex flex-col gap-2">
          <CompteVerifications verifications={verifications} />
          <ListeVerifications verifications={verifications} />
        </div>
      )}
    </div>
  );
}

export function EtapeOutillage({
  projet,
  onTermine,
}: {
  projet: Projet;
  /**
   * L'étape est finie — générée ou reportée. L'appelant relit la liste des
   * projets : la fiche a changé dans les deux cas (le manifeste pour l'une, la
   * date de report pour l'autre), et c'est elle qui fait foi.
   *
   * `suite` dit **ce qui vient après** (#1040, docs/37 §4.6) : l'outillage
   * généré enchaîne sur l'**équipe**, qui branche justement les skills qu'on
   * vient d'écrire ; un « plus tard » referme le parcours. Enchaîner sur une
   * seconde étape derrière un report contredirait le geste qu'on vient de
   * faire — le report dit « laissez-moi », pas « posez-moi une autre
   * question ». Les réponses du questionnaire voyagent avec, pour qu'un projet
   * neuf n'ait pas à les redonner.
   */
  onTermine: (suite: "equipe" | "fin", choix?: ChoixOutillage[]) => void;
}) {
  // L'origine décide du chemin, et elle vient de la **fiche** : c'est la
  // déclaration qui dit si le dossier était là avant nous (#221).
  const neuf = projet.origine === "nouveau";
  const [recommandation, setRecommandation] =
    useState<RecommandationOutillage | null>(null);
  const [resume, setResume] = useState("");
  const [question, setQuestion] = useState<QuestionOutillage | null>(null);
  const [choix, setChoix] = useState<ChoixOutillage[]>([]);
  const [retenus, setRetenus] = useState<Set<string>>(new Set());
  const [rapport, setRapport] = useState<RapportGenerationOutillage | null>(
    null,
  );
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);

  const poser = useCallback((reco: RecommandationOutillage) => {
    setRecommandation(reco);
    setRetenus(
      new Set(reco.entrees.filter(retenueParDefaut).map((e) => e.chemin)),
    );
  }, []);

  useEffect(() => {
    // `vivant` plutôt qu'un `AbortController` : ce qu'on protège n'est pas la
    // requête (la laisser finir ne coûte rien) mais l'écriture d'état sur un
    // composant démonté — le projet peut avoir changé sous nos pieds.
    let vivant = true;
    const partir = async () => {
      try {
        if (neuf) {
          const etape = await questionOutillage(projet.id, []);
          if (!vivant) return;
          setQuestion(etape.question);
          setChoix(etape.deductions);
          if (etape.terminee) {
            const reco = await recommandationOutillage(
              projet.id,
              etape.deductions,
            );
            if (!vivant) return;
            poser(reco.recommandation);
          }
        } else {
          const analyse = await analyserOutillage(projet.id);
          if (!vivant) return;
          setResume(analyse.resume);
          poser(analyse.recommandation);
        }
      } catch (erreur) {
        if (vivant) setRefus(refusDepuis(erreur));
      } finally {
        if (vivant) setChargement(false);
      }
    };
    void partir();
    return () => {
      vivant = false;
    };
  }, [neuf, projet.id, poser]);

  /**
   * Une réponse part, la suite revient — et les réponses **acquises** repartent
   * entières à chaque appel : le questionnaire est sans état côté serveur
   * (#1031), c'est donc l'écran qui les tient.
   */
  const repondre = async (valeur: string) => {
    if (question === null) return;
    const acquis: ChoixOutillage[] = [
      ...choix,
      { cle: question.cle, valeur, deduit: false, parce_que: "" },
    ];
    setEnCours(true);
    try {
      const etape = await questionOutillage(projet.id, acquis);
      const tous = [...acquis, ...etape.deductions];
      setChoix(tous);
      setQuestion(etape.question);
      if (etape.terminee) {
        const reco = await recommandationOutillage(projet.id, tous);
        poser(reco.recommandation);
      }
    } finally {
      setEnCours(false);
    }
  };

  const basculer = (chemin: string) =>
    setRetenus((avant) => {
      const apres = new Set(avant);
      if (apres.has(chemin)) apres.delete(chemin);
      else apres.add(chemin);
      return apres;
    });

  const toutBasculer = (retenir: boolean) =>
    setRetenus(
      retenir
        ? new Set((recommandation?.entrees ?? []).map((e) => e.chemin))
        : new Set(),
    );

  const generer = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      // Le rapport reste à l'écran : c'est la seule trace de ce qui n'a PAS été
      // écrasé, et refermer l'étape dessus la ferait disparaître sans l'avoir lue.
      // Un projet neuf envoie ses **réponses** avec (#1100) : c'est d'elles que la
      // liste qu'on vient de trier a été dérivée, et c'est d'elles que le serveur
      // doit la rederiver — sa racine vide, analysée, ne recommande rien.
      setRapport(
        await genererOutillage(
          projet.id,
          [...retenus],
          neuf ? choix : undefined,
        ),
      );
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
    } finally {
      setEnCours(false);
    }
  };

  const reporter = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      await reporterOutillage(projet.id);
      onTermine("fin");
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
      setEnCours(false);
    }
  };

  const pret = recommandation !== null && !chargement;

  return (
    <Carte
      balise="section"
      densite="aeree"
      aria-label={`Outillage de ${projet.nom}`}
      className="flex flex-col gap-4"
    >
      <EnTeteSection
        niveau={3}
        titre={`Outillage de « ${projet.nom} »`}
        aside={
          <span className="text-annexe text-texte-secondaire">
            {/* Trois étapes depuis #1040 : la racine, l'outillage, l'équipe. Le
                rang se dit ici et dans `EtapeEquipe`, nulle part ailleurs. */}
            {rapport === null ? "étape 2 sur 3" : "outillage terminé"}
          </span>
        }
      />

      {rapport === null && (
        <p className="max-w-2xl text-annexe text-texte-secondaire">
          Maestro écrit dans votre dossier un outillage que <strong>tout</strong>{" "}
          agent sait lire — <code className="font-mono">AGENTS.md</code>, des
          skills et des scripts. Retirez ce dont vous ne voulez pas, puis générez.{" "}
          <strong>Ou repoussez :</strong> le projet est déclaré, il restera
          utilisable, et son outillage vous sera rappelé.
        </p>
      )}

      {/* La phrase que le manifeste gardera (docs/38 §4.1) : ce sur quoi la
          recommandation s'appuie, en une ligne. */}
      {resume !== "" && rapport === null && (
        <p className="text-annexe text-texte">
          <span className="font-medium">Ce que l&apos;analyse a lu :</span>{" "}
          {resume}
        </p>
      )}

      {chargement && (
        <p className="text-corps text-texte-secondaire">
          {neuf
            ? "Préparation des questions…"
            : "Analyse du projet — sa racine est lue, jamais exécutée…"}
        </p>
      )}

      {question !== null && rapport === null && (
        <QuestionDOutillage
          question={question}
          repondre={repondre}
          enCours={enCours}
        />
      )}

      {recommandation !== null && rapport === null && (
        <>
          <EnTeteListe
            entrees={recommandation.entrees}
            retenus={retenus}
            toutBasculer={toutBasculer}
            fige={enCours}
          />
          <ul className="flex flex-col gap-2">
            {recommandation.entrees.map((entree) => (
              <LigneEntree
                key={entree.chemin}
                entree={entree}
                retenue={retenus.has(entree.chemin)}
                basculer={() => basculer(entree.chemin)}
                fige={enCours}
              />
            ))}
          </ul>
          {/* Ce que l'analyse a choisi de **ne pas** recommander : replié, mais
              présent. Sans lui, « pas de skill de tests » se lirait comme un
              oubli de Maestro plutôt que comme un fait du projet (#1030). */}
          {recommandation.ecartes.length > 0 && (
            <details className="text-annexe text-texte-secondaire">
              <summary className="min-h-6 cursor-pointer">
                {recommandation.ecartes.length} élément
                {recommandation.ecartes.length > 1 ? "s" : ""} écarté
                {recommandation.ecartes.length > 1 ? "s" : ""}, et pourquoi
              </summary>
              <ul className="mt-2 flex flex-col gap-1">
                {recommandation.ecartes.map((ecarte) => (
                  <li key={`${ecarte.type}-${ecarte.nom}`}>
                    <span className="font-medium text-texte">{ecarte.nom}</span>{" "}
                    — {ecarte.raison}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}

      {rapport !== null && <RapportGeneration rapport={rapport} />}

      {refus && <RefusMotive refus={refus} titre="Outillage refusé" />}

      <div className="flex flex-wrap items-center gap-3">
        {rapport === null ? (
          <>
            <Bouton
              disabled={!pret || retenus.size === 0}
              occupe={enCours}
              onClick={() => void generer()}
            >
              {enCours
                ? "Génération…"
                : `Générer l'outillage${pret ? ` (${retenus.size})` : ""}`}
            </Bouton>
            <Bouton
              variante="contour"
              ton="neutre"
              disabled={enCours}
              onClick={() => void reporter()}
            >
              Outiller plus tard
            </Bouton>
          </>
        ) : (
          // L'outillage est écrit : la suite du parcours est l'équipe, qui
          // branche les skills qu'on vient de poser. Le libellé le dit plutôt
          // que de promettre une fin qui n'en est pas une.
          <Bouton onClick={() => onTermine("equipe", choix)}>
            Composer l&apos;équipe
          </Bouton>
        )}
      </div>
    </Carte>
  );
}
