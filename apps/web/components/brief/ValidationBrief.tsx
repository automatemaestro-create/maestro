"use client";

/**
 * **Valider le brief** (#322, docs/05 §2.7.4) — le dernier des quatre écrans de
 * la Phase 8, et le point de contrôle le plus rentable du produit : corriger un
 * brief coûte un message, corriger douze tâches coûte douze exécutions
 * (décision D5, #218).
 *
 * Le run est arrêté ici, en vol mais immobile, et rien ne repartira sans un
 * geste. L'écran en sert deux, selon ce que le run attend :
 *
 * - `en_attente_reponses` (#321) — le Chef de projet a **posé des questions** :
 *   on y répond, le brief est régénéré. Aucun bouton « approuver » n'est
 *   proposé, parce qu'on ne demande pas à quelqu'un d'approuver ce sur quoi on
 *   vient de l'interroger ;
 * - `en_attente_brief` (#320) — le brief est **complet** : on le relit, on le
 *   corrige si besoin, puis on approuve ou on refuse.
 *
 * Trois partis pris tiennent l'écran :
 *
 * - **la correction précède l'accord, elle ne le suit pas.** Ce qui repart en
 *   décomposition est ce qu'on a sous les yeux : approuver un brief modifié
 *   envoie le brief modifié (`brief`), approuver un brief intact envoie `null` —
 *   le moteur retient alors sa propre proposition sans la faire retraverser une
 *   validation de schéma. La distinction est portée par `estCorrige`, pas par un
 *   drapeau qu'un rendu pourrait désynchroniser ;
 * - **le coût est en face de la décision**, pas en haut de page : engagé d'un
 *   côté, estimé de l'autre, dans le même bloc que les deux boutons. C'est ce qui
 *   rend un refus rationnel plutôt que timide ;
 * - **un refus du backend s'affiche tel quel.** 409 (le run n'attend plus — il a
 *   été tranché ailleurs ou annulé entre-temps), 422 (le brief corrigé ne
 *   respecte pas le schéma partagé) : le message de l'API est le seul qui sache
 *   ce qu'il a refusé, et la saisie est **conservée** dans tous les cas.
 */

import { useCallback, useEffect, useState } from "react";

import { CoutBrief } from "@/components/brief/CoutBrief";
import {
  FormulaireReponses,
  HistoriqueClarifications,
} from "@/components/brief/QuestionsBrief";
import { ChampsBrief, SectionsBrief } from "@/components/brief/SectionsBrief";
import { IconeObjectif } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import {
  briefDepuisEdition,
  editionDepuis,
  estCorrige,
  toursDeClarification,
  type BriefEdite,
  type CleSectionListe,
} from "@/lib/brief";
import { chargerExecution } from "@/lib/api";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatHeureRelative } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import {
  EXECUTION_EN_ATTENTE_REPONSES,
  type DetailExecution,
  type ResumeExecution,
} from "@/lib/types";

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
 */
function manquesDe(edite: BriefEdite): string[] {
  const propose = briefDepuisEdition(edite);
  const manques: string[] = [];
  if (propose.objectif.length === 0) manques.push("un objectif");
  if (propose.perimetre.length === 0) manques.push("au moins une entrée de périmètre");
  if (propose.criteres_acceptation.length === 0) {
    manques.push("au moins un critère d'acceptation");
  }
  return manques;
}

export function ValidationBrief({ execution }: { execution: ResumeExecution }) {
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
  // Ce qui rend un chargement **périmé** : l'attente dans laquelle le run est, et
  // depuis quand elle dure. Le second terme n'est pas de trop — un tour de
  // clarification répondu ramène le run à la même attente avec un brief régénéré,
  // et sans lui l'écran continuerait d'afficher les questions du tour précédent,
  // auxquelles on vient justement de répondre. C'est aussi ce que le parent met
  // dans sa `key` (`ValidationBriefs`) ; le garder ici rend le composant juste
  // même monté sans elle.
  const attente = `${execution.statut}|${execution.attente_depuis ?? ""}`;

  // Aucun `setChargement(true)` ici : l'état de départ l'est déjà, et c'est la
  // `key` du parent — le run **et** son attente — qui remonte le composant quand
  // il y a autre chose à charger. Remettre le drapeau depuis le corps de l'effet
  // ferait un rendu en cascade pour obtenir ce que le remontage donne
  // gratuitement, en plus de tout le reste : corrections en cours et réponses
  // saisies, qui ne doivent survivre ni à un changement de run ni à un nouveau
  // tour de clarification.
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
      // Approuvé **corrigé** → le brief modifié devient l'entrée de la
      // décomposition ; approuvé **tel quel** → `null`, et le moteur retient sa
      // propre proposition. Un refus n'emporte jamais de brief : la route
      // l'ignorerait, et l'envoyer laisserait croire qu'il a été retenu.
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
    return (
      <p className="text-sm text-neutral-500">Chargement du brief…</p>
    );
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
    // passer l'événement qui le porte. On le dit au lieu d'afficher un écran de
    // décision vide — approuver ce qu'on ne voit pas n'est pas une validation.
    return (
      <EtatVide
        message="Ce run attend une décision mais son brief n'est pas encore consultable. Il arrivera avec le prochain événement."
        icone={IconeObjectif}
      />
    );
  }

  const tours = toursDeClarification(detail.evenements);
  const manques = manquesDe(edite);
  const corrige = estCorrige(brief, edite);

  return (
    <section aria-label="Valider le brief" className="space-y-4">
      <Carte balise="section" densite="aeree">
        <EnTeteSection
          titre="Objectif d'origine"
          icone={IconeObjectif}
          aside={
            <span className="flex flex-wrap items-center gap-2">
              {/* L'ancienneté de l'attente (#321) : c'est elle qui distingue un
                  run suspendu d'un run planté. */}
              <BadgeEtat ton="attention" pastille>
                {attendDesReponses ? "Attend vos réponses" : "Attend votre décision"}
              </BadgeEtat>
              {execution.attente_depuis && (
                <span className="text-annexe text-neutral-500 dark:text-neutral-400">
                  depuis{" "}
                  {formatHeureRelative(execution.attente_depuis, maintenant)}
                </span>
              )}
            </span>
          }
          className="mb-2"
        />
        <p className="whitespace-pre-wrap text-corps text-neutral-700 dark:text-neutral-300">
          {execution.objectif}
        </p>
        <p className="mt-2 font-mono text-annexe text-neutral-500 dark:text-neutral-400">
          {runId}
        </p>
      </Carte>

      <HistoriqueClarifications tours={tours} />

      {attendDesReponses ? (
        <>
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
          {/* En lecture seule : ce brief-là va être régénéré à partir des
              réponses, le corriger maintenant serait un travail jeté. */}
          <Carte balise="section" densite="aeree" aria-label="Brief en cours de rédaction">
            <EnTeteSection titre="Brief en cours de rédaction" className="mb-2" />
            <p className="mb-3 text-annexe text-neutral-500 dark:text-neutral-400">
              Il sera <strong>réécrit en entier</strong> à partir de vos réponses —
              il se relit, il ne se corrige pas encore.
            </p>
            <SectionsBrief brief={brief} />
          </Carte>
        </>
      ) : (
        <>
          <Carte balise="section" densite="aeree" aria-label="Brief à valider">
            <EnTeteSection
              titre="Brief proposé"
              className="mb-2"
              aside={
                corrige ? (
                  <BadgeEtat ton="info">Corrigé — c&apos;est cette version qui partira</BadgeEtat>
                ) : undefined
              }
            />
            <p className="mb-3 text-annexe text-neutral-500 dark:text-neutral-400">
              Relisez, corrigez si besoin : <strong>c&apos;est ce texte</strong>{" "}
              qui sera décomposé en tâches.
            </p>
            <ChampsBrief edite={edite} changer={changerSection} desactive={enCours} />
          </Carte>

          <Carte
            balise="section"
            ton="attention"
            densite="aeree"
            aria-label="Décision sur le brief"
          >
            <EnTeteSection titre="Décision" ton="attention" className="mb-3" />
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
        </>
      )}
    </section>
  );
}
