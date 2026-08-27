"use client";

/**
 * Le cadrage d'un run **dans la conversation** (#483, lot 2 de #481) : le brief
 * se lit, se corrige et se tranche là où on a parlé, et les questions de
 * clarification se répondent au même endroit.
 *
 * C'est un **déménagement, pas une réécriture** (arbitrage du 2026-08-24,
 * [docs/29 §4](../../../../docs/29-decision-run-objet-de-premier-plan.md)). La
 * décision **D5** de #218 tient — on ne décompose pas avant validation humaine —,
 * et rien du transport ne bouge : les deux routes sont celles de #320 et #321
 * (`POST /api/executions/{run_id}/brief/decision` et `/brief/reponses`, §6.10),
 * appelées par le `trancherBrief` / `repondreAuBrief` du contexte global. Aucun
 * second canal, c'est le critère 1 du ticket ; les sept sections, le formulaire
 * de réponses et le coût sont les composants de `/brief`, montés tels quels.
 *
 * **Ce qui change vraiment est l'ordre de lecture**, et c'est tout l'intérêt du
 * déménagement :
 *
 * - les allers-retours déjà joués sont **le fil lui-même**, déroulés, du plus
 *   ancien au plus récent. Sur `/brief` ils vivent dans un accordéon replié
 *   (`HistoriqueClarifications`) parce qu'ils y sont un à-côté du geste ; ici ils
 *   *sont* la conversation, et la replier reviendrait à cacher le fil dans le fil.
 *   La règle d'appariement, elle, ne bouge pas — `toursDeClarification`, partagée ;
 * - le **dernier message** est ce que le Chef de projet vient de dire : le brief
 *   complet, éditable sur place, ou le brief en cours de rédaction quand il attend
 *   des réponses ;
 * - ce qu'on **écrit** est en bas, à la place de la zone de saisie : le formulaire
 *   de réponses, ou le coût et les deux boutons.
 *
 * Deux propriétés du contrat portées ici comme sur l'écran, parce qu'elles ne
 * sont pas cosmétiques : un brief **touché** part corrigé et un brief **intact**
 * part en `null` (le moteur retient alors sa propre proposition sans la faire
 * retraverser la validation de schéma), et un **refus n'emporte jamais de brief**.
 *
 * ⚠ Ce composant ne monte **aucun fil de messages** : le canal `orchestrateur`
 * livré par #268 est un contrat d'API, son écran est #269, et ce lot prolonge les
 * deux sans les doubler. Il n'y a donc ici ni `useChat`, ni route `/api/chat` —
 * les deux fils se rejoindront sur cette page, ils ne se remplacent pas.
 */

import { Fragment, useCallback, useEffect, useState } from "react";

import { CoutBrief } from "@/components/brief/CoutBrief";
import { FormulaireReponses } from "@/components/brief/QuestionsBrief";
import { ChampsBrief, SectionsBrief } from "@/components/brief/SectionsBrief";
import { BulleFil } from "@/components/chat/BulleFil";
import { IconeBrief, IconeObjectif } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import { chargerExecution } from "@/lib/api";
import {
  AUTEUR_CADRAGE,
  briefDepuisEdition,
  editionDepuis,
  estCorrige,
  manquesDuBrief,
  toursDeClarification,
  type BriefEdite,
  type CleSectionListe,
} from "@/lib/brief";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  type DetailExecution,
  type ResumeExecution,
} from "@/lib/types";

export function CadrageDansLeFil({
  execution,
}: {
  execution: ResumeExecution;
}) {
  const { trancherBrief, repondreAuBrief } = useEtatGlobal();
  const maintenant = useHorloge();

  const [detail, setDetail] = useState<DetailExecution | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreurChargement, setErreurChargement] = useState<string | null>(null);
  const [edite, setEdite] = useState<BriefEdite | null>(null);
  const [reponses, setReponses] = useState<string[]>([]);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<string | null>(null);

  const runId = execution.run_id;
  // Même clé de péremption que l'écran (#322) : l'attente **et** depuis quand.
  // Un tour de clarification répondu ramène le run à la même attente avec un
  // brief régénéré — sans le second terme, le fil continuerait d'afficher les
  // questions auxquelles on vient de répondre. Le parent la met aussi dans sa
  // `key` ; la garder ici rend le composant juste monté sans elle.
  const attente = `${execution.statut}|${execution.attente_depuis ?? ""}`;

  useEffect(() => {
    let abandonne = false;
    chargerExecution(runId)
      .then((charge) => {
        if (abandonne) return;
        setDetail(charge);
        setEdite(charge.brief === null ? null : editionDepuis(charge.brief));
        setReponses((charge.brief?.questions ?? []).map(() => ""));
        setErreurChargement(null);
        setRefus(null);
      })
      .catch((e: unknown) => {
        if (abandonne) return;
        setErreurChargement(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!abandonne) setChargement(false);
      });
    return () => {
      abandonne = true;
    };
  }, [runId, attente]);

  const changerSection = useCallback(
    (cle: "objectif" | CleSectionListe, valeur: string) => {
      setEdite((avant) => (avant === null ? avant : { ...avant, [cle]: valeur }));
    },
    [],
  );

  const changerReponse = useCallback((rang: number, valeur: string) => {
    setReponses((avant) => avant.map((r, i) => (i === rang ? valeur : r)));
  }, []);

  const brief = detail?.brief ?? null;
  const attendDesReponses = execution.statut === EXECUTION_EN_ATTENTE_REPONSES;

  const surDecision = async (approuve: boolean) => {
    if (brief === null || edite === null) return;
    setEnCours(true);
    setRefus(null);
    try {
      const corrige =
        approuve && estCorrige(brief, edite) ? briefDepuisEdition(edite) : null;
      await trancherBrief(runId, { approuve, brief: corrige });
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  const surReponses = async () => {
    if (brief === null) return;
    setEnCours(true);
    setRefus(null);
    try {
      // Toujours **autant de réponses que de questions**, dans l'ordre, chaînes
      // vides comprises : l'appariement est positionnel et l'API refuse en 422
      // une liste qui ne fait pas le compte.
      await repondreAuBrief(
        runId,
        brief.questions.map((_, rang) => reponses[rang] ?? ""),
      );
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement du cadrage…</p>;
  }
  if (erreurChargement !== null) {
    return (
      <EtatVide
        message={`Le brief de ce run n'a pas pu être chargé : ${erreurChargement}`}
        icone={IconeObjectif}
      />
    );
  }
  if (detail === null || brief === null || edite === null) {
    // Un run suspendu sans brief consultable : la projection n'a pas (encore) vu
    // passer l'événement qui le porte. On le dit au lieu d'ouvrir une décision
    // vide — approuver ce qu'on ne voit pas n'est pas une validation.
    return (
      <EtatVide
        message="Ce run attend une décision mais son brief n'est pas encore consultable. Il arrivera avec le prochain événement."
        icone={IconeObjectif}
      />
    );
  }

  const tours = toursDeClarification(detail.evenements);
  const manques = manquesDuBrief(edite);
  const corrige = estCorrige(brief, edite);

  return (
    <div className="flex flex-col gap-3">
      <ol
        aria-label={`Cadrage de ${execution.objectif || runId}`}
        className="flex flex-col gap-2 rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950"
      >
        {/* L'objectif d'origine ouvre le fil, **côté utilisateur** : c'est la
            demande qu'on a faite, et c'est à elle que tout ce qui suit répond. */}
        <BulleFil auteur="vous" utilisateur horodatage={execution.debut}>
          <p className="whitespace-pre-wrap break-words">
            {execution.objectif || runId}
          </p>
        </BulleFil>

        {/* Les allers-retours déjà joués — déroulés, jamais repliés : ici, ils
            sont le fil. Le rang numérote les deux bulles d'un tour, et c'est lui
            qui porte l'appariement question ↔ réponse : le brief est régénéré en
            entier à chaque tour, donc une question n'a pas d'identité stable
            (#318). Une question sans réponse est **dite** telle — une hypothèse
            née d'un « je ne sais pas » assumé ne se conteste pas comme une
            hypothèse que personne n'a vue passer. */}
        {tours.map((tour) => (
          <Fragment key={`tour-${tour.tour}-${tour.horodatage}`}>
            <BulleFil auteur={AUTEUR_CADRAGE} horodatage={tour.horodatage}>
              <p className="text-annexe font-semibold text-neutral-500 dark:text-neutral-400">
                Tour {tour.tour}
              </p>
              <ol className="mt-1 list-decimal space-y-1 pl-4">
                {tour.questions.map((question, rang) => (
                  <li key={`q-${rang}`} className="whitespace-pre-wrap">
                    {question}
                  </li>
                ))}
              </ol>
            </BulleFil>
            <BulleFil auteur="vous" utilisateur>
              <ol className="list-decimal space-y-1 pl-4">
                {tour.questions.map((_, rang) => {
                  const reponse = (tour.reponses[rang] ?? "").trim();
                  return (
                    <li
                      key={`r-${rang}`}
                      // Le sans-réponse se dit en italique et **pas** en gris
                      // clair : la bulle est déjà sur fond plein, et l'y
                      // atténuer coûterait du contraste pour redire ce que la
                      // phrase énonce déjà en toutes lettres.
                      className={reponse ? "whitespace-pre-wrap" : "italic"}
                    >
                      {reponse || "Sans réponse — partie en hypothèse"}
                    </li>
                  );
                })}
              </ol>
            </BulleFil>
          </Fragment>
        ))}

        {/* Le dernier message : ce que le Chef de projet vient de dire. En pleine
            largeur — sept sections éditables ne tiennent pas dans 70 % de la
            colonne, et les y comprimer ferait de la correction une contorsion,
            c'est-à-dire la friction qui fait approuver sans lire. */}
        <BulleFil
          auteur={AUTEUR_CADRAGE}
          horodatage={execution.attente_depuis ?? undefined}
          pleineLargeur
        >
          <EnTeteSection
            niveau={3}
            icone={IconeBrief}
            titre={
              attendDesReponses ? "Brief en cours de rédaction" : "Brief proposé"
            }
            className="mb-2"
            aside={
              corrige && !attendDesReponses ? (
                <BadgeEtat ton="info">
                  Corrigé — c&apos;est cette version qui partira
                </BadgeEtat>
              ) : undefined
            }
          />
          {attendDesReponses ? (
            <>
              <p className="mb-3 text-annexe text-neutral-500 dark:text-neutral-400">
                Il sera <strong>réécrit en entier</strong> à partir de vos
                réponses — il se relit, il ne se corrige pas encore.
              </p>
              {/* En lecture seule : ce brief-là va être régénéré, le corriger
                  maintenant serait un travail jeté. */}
              <SectionsBrief brief={brief} />
            </>
          ) : (
            <>
              <p className="mb-3 text-annexe text-neutral-500 dark:text-neutral-400">
                Relisez, corrigez si besoin : <strong>c&apos;est ce texte</strong>{" "}
                qui sera décomposé en tâches.
              </p>
              <ChampsBrief
                edite={edite}
                changer={changerSection}
                desactive={enCours}
              />
            </>
          )}
        </BulleFil>
      </ol>

      {/* Ce qu'on écrit, à la place de la zone de saisie du fil. */}
      {attendDesReponses ? (
        <FormulaireReponses
          questions={brief.questions}
          reponses={reponses}
          changer={changerReponse}
          envoyer={() => void surReponses()}
          enCours={enCours}
          refus={refus}
          tour={execution.tour_clarification ?? 0}
          toursMax={execution.tours_clarification_max ?? 0}
        />
      ) : (
        <Carte
          balise="section"
          ton="attention"
          densite="aeree"
          aria-label="Décision sur le brief"
        >
          <EnTeteSection
            niveau={3}
            titre="Décision"
            ton="attention"
            className="mb-3"
            aside={
              execution.attente_depuis ? (
                <span className="text-annexe text-neutral-500 dark:text-neutral-400">
                  en attente depuis{" "}
                  {formatHeureRelative(execution.attente_depuis, maintenant)}
                </span>
              ) : undefined
            }
          />
          {/* Le coût est **en face de la décision** (#322, critère 3) : engagé
              d'un côté, estimé de l'autre. C'est ce qui rend un refus rationnel
              plutôt que timide. */}
          <CoutBrief cout={detail.cout} brief={briefDepuisEdition(edite)} />
          <div className="mt-4 flex flex-wrap gap-2">
            <Bouton
              disabled={manques.length > 0}
              occupe={enCours}
              onClick={() => void surDecision(true)}
            >
              {enCours
                ? "Envoi…"
                : corrige
                  ? "Approuver la version corrigée"
                  : "Approuver"}
            </Bouton>
            <Bouton
              variante="contour"
              ton="alerte"
              disabled={enCours}
              onClick={() => void surDecision(false)}
            >
              Refuser
            </Bouton>
          </div>
          {manques.length > 0 && (
            <p className="mt-2 text-annexe text-amber-700 dark:text-amber-400">
              Il manque {manques.join(", ")} — le brief serait refusé par
              l&apos;API.
            </p>
          )}
          {refus && (
            <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400">
              {refus}
            </p>
          )}
        </Carte>
      )}
    </div>
  );
}
