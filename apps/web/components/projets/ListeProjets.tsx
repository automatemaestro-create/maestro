"use client";

/**
 * L'écran Projets (#225, docs/05 §2.7) : déclarer où Maestro travaille, et
 * gérer ces déclarations. Branché sur les six routes de #223.
 *
 * Ce que la page tient en propre — et qui explique sa forme :
 *
 * - **la liste est l'état réel du disque**, pas un cache optimiste : chaque
 *   écriture est suivie d'un rechargement, parce que le backend canonicalise la
 *   racine et **constate** le VCS. Afficher ce qu'on a envoyé plutôt que ce qui
 *   a été enregistré ferait diverger l'écran du dossier ;
 * - **un refus est une réponse, pas une panne** (EF-38) : il s'affiche avec son
 *   motif à l'endroit du geste refusé — dans le formulaire, ou sur la carte du
 *   projet — et le reste de l'écran continue de fonctionner ;
 * - **la suppression s'arme en deux temps**, comme celle d'un agent (#72) :
 *   pas de boîte de dialogue, un second bouton qui dit ce qu'il fait. Elle
 *   n'efface que la déclaration, jamais le dossier ;
 * - **la mise sous Git s'arme de la même façon** (#855) — sur un projet « Non
 *   versionné » seulement, et derrière une confirmation qui **dit ce qui va
 *   être fait** : `git init` puis un premier commit de toute la racine, le
 *   `.gitignore` du projet respecté. C'est le seul geste de l'écran qui écrive
 *   dans le dossier de l'utilisateur, et c'est ce qui fait passer le projet du
 *   régime « écriture en place » au régime « worktree + fusion sous accord »
 *   (docs/24 §2.4) — d'où une confirmation, là où déclarer ou modifier n'en
 *   demandent pas. Le `vcs` n'est pas envoyé : il est **constaté** au retour
 *   (EF-38), et la liste se relit comme après toute écriture.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { QuestionDOutillage } from "@/components/chat/QuestionDOutillage";
import { IconeDossier, IconePlus } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import {
  analyserOutillage,
  chargerProjets,
  creerProjet,
  genererOutillage,
  modifierProjet,
  panneDe,
  questionOutillage,
  recommandationOutillage,
  reporterOutillage,
  supprimerProjet,
  versionnerProjet,
  type PanneApi,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import { libelleOrigine } from "@/lib/projets";
import type {
  ChoixOutillage,
  DeclarationProjet,
  EntreeOutillage,
  Projet,
  QuestionOutillage,
  RecommandationOutillage,
  RefusProjet,
} from "@/lib/types";

import { refusDepuis, RefusMotive } from "./ExplorateurDossiers";
import { FormulaireProjet } from "./FormulaireProjet";

/** Ce qu'un projet expose aux agents, en une ligne lisible. */
function Perimetre({ projet }: { projet: Projet }) {
  return (
    <p className="text-xs text-neutral-500 dark:text-neutral-400">
      Périmètre — inclus :{" "}
      <code className="font-mono">{projet.perimetre.inclus.join(", ")}</code>
      {projet.perimetre.exclus.length > 0 && (
        <>
          {" · exclus : "}
          <code className="font-mono">{projet.perimetre.exclus.join(", ")}</code>
        </>
      )}
    </p>
  );
}

/* ===================== BROUILLON #1034 — variante A ======================
   L'étape d'outillage, rendue ici le temps de choisir sa forme (étape 7 de
   /ticket-start). Rien de définitif : ce bloc part dans un fichier à lui une
   fois la variante retenue. */

/** Ce qu'une entrée recommandée vaut par défaut : à écrire, ou déjà là. */
function retenueParDefaut(entree: EntreeOutillage): boolean {
  return entree.etat !== "deja-present";
}

const LIBELLE_ETAT: Record<string, string> = {
  "a-generer": "à écrire",
  "a-completer": "à compléter",
  "deja-present": "déjà présent",
};

const LIBELLE_TYPE: Record<string, string> = {
  instructions: "Instructions",
  pont: "Pont",
  skill: "Skill",
  script: "Script",
};

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
  return (
    <li>
      <label
        className={[
          "flex cursor-pointer items-start gap-3 rounded-carte border p-3",
          retenue
            ? "border-bord bg-surface"
            : "border-bord bg-surface-creuse opacity-70",
        ].join(" ")}
      >
        <input
          type="checkbox"
          checked={retenue}
          disabled={fige}
          onChange={basculer}
          className="mt-0.5 size-4 shrink-0 rounded border-bord-fort"
        />
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="flex flex-wrap items-center gap-2">
            <span
              className={[
                "text-corps font-medium text-texte",
                retenue ? "" : "line-through",
              ].join(" ")}
            >
              {entree.nom}
            </span>
            <BadgeEtat contour>
              {LIBELLE_TYPE[entree.type] ?? entree.type}
            </BadgeEtat>
            {entree.etat === "deja-present" && (
              <BadgeEtat ton="info" contour>
                {LIBELLE_ETAT[entree.etat]}
              </BadgeEtat>
            )}
          </span>
          <span className="min-w-0 break-words text-annexe text-texte-secondaire">
            {entree.raison}
          </span>
          <span className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-micro text-texte-secondaire">
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
        </span>
      </label>
    </li>
  );
}

function VueRecommandation({
  recommandation,
  retenus,
  basculer,
  fige,
}: {
  recommandation: RecommandationOutillage;
  retenus: Set<string>;
  basculer: (chemin: string) => void;
  fige: boolean;
}) {
  return (
    <>
      <ul className="flex flex-col gap-2">
        {recommandation.entrees.map((entree) => (
          <LigneEntree
            key={entree.chemin}
            entree={entree}
            retenue={retenus.has(entree.chemin)}
            basculer={() => basculer(entree.chemin)}
            fige={fige}
          />
        ))}
      </ul>
      {recommandation.ecartes.length > 0 && (
        <details className="mt-3 text-annexe text-texte-secondaire">
          <summary className="cursor-pointer">
            {recommandation.ecartes.length} élément
            {recommandation.ecartes.length > 1 ? "s" : ""} écarté
            {recommandation.ecartes.length > 1 ? "s" : ""}, et pourquoi
          </summary>
          <ul className="mt-2 flex flex-col gap-1">
            {recommandation.ecartes.map((ecarte) => (
              <li key={`${ecarte.type}-${ecarte.nom}`}>
                <span className="font-medium text-texte">{ecarte.nom}</span> —{" "}
                {ecarte.raison}
              </li>
            ))}
          </ul>
        </details>
      )}
    </>
  );
}

function EtapeOutillage({
  projet,
  onTermine,
  onReporte,
}: {
  projet: Projet;
  onTermine: () => void;
  onReporte: () => void;
}) {
  const neuf = projet.origine === "nouveau";
  const [recommandation, setRecommandation] =
    useState<RecommandationOutillage | null>(null);
  const [resume, setResume] = useState("");
  const [question, setQuestion] = useState<QuestionOutillage | null>(null);
  const [choix, setChoix] = useState<ChoixOutillage[]>([]);
  const [retenus, setRetenus] = useState<Set<string>>(new Set());
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

  const repondre = async (valeur: string) => {
    if (question === null) return;
    const acquis = [
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

  const generer = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      await genererOutillage(projet.id, [...retenus]);
      onTermine();
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
      setEnCours(false);
    }
  };

  const reporter = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      await reporterOutillage(projet.id);
      onReporte();
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
      setEnCours(false);
    }
  };

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
            étape 2 sur 2
          </span>
        }
      />
      <p className="max-w-2xl text-annexe text-texte-secondaire">
        Maestro écrit dans votre dossier un outillage que <strong>tout</strong>{" "}
        agent sait lire — <code className="font-mono">AGENTS.md</code>, des
        skills et des scripts. Retirez ce dont vous ne voulez pas, puis générez.{" "}
        <strong>Ou repoussez :</strong> le projet est déclaré, il restera
        utilisable, et son outillage vous sera rappelé.
      </p>
      {resume !== "" && (
        <p className="text-annexe text-texte">
          <span className="font-medium">Ce que l&apos;analyse a lu :</span>{" "}
          {resume}
        </p>
      )}

      {chargement && (
        <p className="text-corps text-texte-secondaire">
          {neuf ? "Préparation des questions…" : "Analyse du projet…"}
        </p>
      )}

      {question !== null && (
        <QuestionDOutillage
          question={question}
          repondre={repondre}
          enCours={enCours}
        />
      )}

      {recommandation !== null && (
        <VueRecommandation
          recommandation={recommandation}
          retenus={retenus}
          basculer={basculer}
          fige={enCours}
        />
      )}

      {refus && <RefusMotive refus={refus} titre="Outillage refusé" />}

      <div className="flex flex-wrap items-center gap-3">
        <Bouton
          disabled={recommandation === null || retenus.size === 0}
          occupe={enCours}
          onClick={() => void generer()}
        >
          {enCours ? "Génération…" : `Générer l'outillage (${retenus.size})`}
        </Bouton>
        <Bouton
          variante="contour"
          ton="neutre"
          disabled={enCours}
          onClick={() => void reporter()}
        >
          Outiller plus tard
        </Bouton>
      </div>
    </Carte>
  );
}

/* =================== FIN DU BROUILLON #1034 ============================= */

/**
 * Les deux gestes de la carte qui s'arment en deux temps. Un seul à la fois :
 * armer l'un désarme l'autre, sans quoi la carte porterait deux confirmations
 * qui ne parlent pas de la même chose.
 */
type GesteArme = "supprimer" | "versionner";

/** Un refus affiché sur la carte, sous le titre du geste qu'il a refusé. */
type RefusCarte = { titre: string; detail: RefusProjet };

function CarteProjet({
  projet,
  onModifier,
  onSupprime,
  onVersionne,
  onOutiller,
}: {
  projet: Projet;
  onModifier: () => void;
  onSupprime: () => Promise<void>;
  onVersionne: () => Promise<void>;
  /** Rouvre l'étape d'outillage sur ce projet — la sortie d'un « plus tard ». */
  onOutiller: () => void;
}) {
  const [geste, setGeste] = useState<GesteArme | null>(null);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusCarte | null>(null);

  /** Joue un geste armé : la carte se fige, un refus revient à son titre. */
  const jouer = async (titreRefus: string, action: () => Promise<void>) => {
    setEnCours(true);
    setRefus(null);
    try {
      await action();
    } catch (erreur) {
      setRefus({ titre: titreRefus, detail: refusDepuis(erreur) });
      setEnCours(false);
      setGeste(null);
    }
  };

  const supprimer = () =>
    jouer("Suppression refusée", async () => {
      await supprimerProjet(projet.id);
      await onSupprime();
    });

  // Le `vcs` rendu par la route n'est pas gardé : la liste se relit, comme
  // après toute écriture — c'est elle qui montre « git · <branche> ».
  const versionner = () =>
    jouer("Mise sous Git refusée", async () => {
      await versionnerProjet(projet.id);
      await onVersionne();
    });

  return (
    <Carte
      balise="li"
      densite="aeree"
      aria-label={`Projet ${projet.nom}`}
      className="flex flex-col gap-2"
    >
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-corps font-semibold">{projet.nom}</h3>
        <BadgeEtat contour>{libelleOrigine(projet.origine)}</BadgeEtat>
        {projet.vcs === null ? (
          <BadgeEtat contour>Non versionné</BadgeEtat>
        ) : (
          <BadgeEtat ton="info" contour>
            {projet.vcs.type}
            {projet.vcs.branche_base !== "" && ` · ${projet.vcs.branche_base}`}
          </BadgeEtat>
        )}
        {/* « Un projet non outillé le dit » (#1034, docs/37 §4.6) — et il le dit
            **tant qu'il ne l'est pas** : `a_faire` croise la décision (reporté)
            et le disque (manifeste absent), si bien que générer suffit à faire
            taire le rappel. */}
        {projet.outillage?.a_faire && (
          <BadgeEtat ton="attention" contour>
            Outillage reporté
          </BadgeEtat>
        )}
      </div>
      <code className="font-mono text-annexe break-all text-neutral-800 dark:text-neutral-200">
        {projet.racine}
      </code>
      <Perimetre projet={projet} />
      <p className="text-annexe text-neutral-400 dark:text-neutral-500">
        Déclaré le {formatDateHeure(projet.cree_le)} · modifié le{" "}
        {formatDateHeure(projet.modifie_le)}
      </p>
      {refus && <RefusMotive refus={refus.detail} titre={refus.titre} />}
      {geste === "versionner" ? (
        <Carte
          balise="div"
          ton="attention"
          densite="compacte"
          role="group"
          aria-label="Confirmer la mise sous Git"
          className="mt-1 flex flex-col gap-2 text-xs text-neutral-800 dark:text-neutral-200"
        >
          <p>
            <strong>Ce qui va être fait :</strong>{" "}
            <code className="font-mono">git init</code> dans{" "}
            <code className="font-mono break-all">{projet.racine}</code>, puis
            un premier commit « Maestro : état initial du projet » qui enregistre{" "}
            <strong>toute la racine</strong> telle qu&apos;elle est — le{" "}
            <code className="font-mono">.gitignore</code> du projet est
            respecté, rien d&apos;autre n&apos;est écrit. Dès la tâche suivante,
            les agents travailleront dans un espace dérivé de ce dépôt et la
            fusion de leur travail demandera votre accord.
          </p>
          <div className="flex flex-wrap gap-2">
            <Bouton ton="info" occupe={enCours} onClick={() => void versionner()}>
              {enCours ? "Mise sous Git…" : "Confirmer la mise sous Git"}
            </Bouton>
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => setGeste(null)}
              disabled={enCours}
            >
              Garder non versionné
            </Bouton>
          </div>
        </Carte>
      ) : (
        <div className="mt-1 flex flex-wrap gap-2">
          <Bouton
            variante="contour"
            ton="neutre"
            onClick={onModifier}
            disabled={enCours}
          >
            Modifier
          </Bouton>
          {projet.vcs === null && geste === null && (
            <Bouton
              variante="contour"
              ton="info"
              onClick={() => setGeste("versionner")}
              disabled={enCours}
            >
              Mettre sous Git
            </Bouton>
          )}
          {/* Un report n'est pas un cul-de-sac : la question revient d'un clic,
              là où elle a été posée. */}
          {projet.outillage?.a_faire && geste === null && (
            <Bouton
              variante="contour"
              ton="attention"
              onClick={onOutiller}
              disabled={enCours}
            >
              Outiller maintenant
            </Bouton>
          )}
          {geste === "supprimer" ? (
            <>
              <Bouton
                ton="alerte"
                occupe={enCours}
                onClick={() => void supprimer()}
              >
                {enCours ? "Suppression…" : "Confirmer la suppression"}
              </Bouton>
              <Bouton
                variante="contour"
                ton="neutre"
                onClick={() => setGeste(null)}
                disabled={enCours}
              >
                Garder le projet
              </Bouton>
              <span className="self-center text-xs text-neutral-500 dark:text-neutral-400">
                Seule la déclaration part : le dossier reste sur le disque.
              </span>
            </>
          ) : (
            <Bouton
              variante="contour"
              ton="alerte"
              onClick={() => setGeste("supprimer")}
              disabled={enCours}
            >
              Supprimer
            </Bouton>
          )}
        </div>
      )}
    </Carte>
  );
}

type Props = {
  /**
   * Appelé après **chaque écriture** (déclaration, modification, suppression,
   * mise sous Git) — l'écran ne connaît toujours pas le projet actif, il
   * signale seulement que la liste réelle a bougé.
   *
   * C'est ce qui rend l'écran atteignable depuis le sélecteur (#280) sans
   * régression : supprimer la racine sur laquelle la Control Tower est ouverte
   * doit ramener à la porte d'entrée avec son motif (#279), là où sans ce
   * signal le shell resterait le cadre d'un projet qui n'existe plus. Le rappel
   * est **optionnel** pour que l'écran continue de se tester seul, hors de tout
   * fournisseur.
   */
  apresEcriture?: () => void;
};

export function ListeProjets({ apresEcriture }: Props = {}) {
  const [projets, setProjets] = useState<Projet[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<PanneApi | null>(null);
  const [creationOuverte, setCreationOuverte] = useState(false);
  const [editionId, setEditionId] = useState<string | null>(null);
  // Le projet qui vient d'être déclaré et dont l'outillage se décide : l'étape
  // suivante du parcours, pas un écran à part (#1034).
  const [aOutiller, setAOutiller] = useState<Projet | null>(null);

  const recharger = useCallback(async () => {
    try {
      setProjets(await chargerProjets());
      setErreur(null);
    } catch (e) {
      setErreur(panneDe(e));
    } finally {
      setChargement(false);
    }
  }, []);

  useEffect(() => {
    // Chargement différé d'un tick (même mécanique que le shell) : l'effet
    // lui-même ne déclenche aucun setState synchrone.
    const tick = setTimeout(() => void recharger(), 0);
    return () => clearTimeout(tick);
  }, [recharger]);

  // La relecture d'après écriture, distincte de celle du montage : seule
  // celle-ci porte un changement, et donc seule elle a quelqu'un à prévenir.
  const rechargerApresEcriture = useCallback(async () => {
    await recharger();
    apresEcriture?.();
  }, [recharger, apresEcriture]);

  const declarer = async (declaration: DeclarationProjet) => {
    const projet = await creerProjet(declaration);
    setCreationOuverte(false);
    // Le choix de la racine n'est plus la fin du parcours : l'outillage est
    // l'étape suivante, proposée d'office (#1034, docs/37 §4.6).
    setAOutiller(projet);
    await rechargerApresEcriture();
  };

  const finirOutillage = () => {
    setAOutiller(null);
    void rechargerApresEcriture();
  };

  const modifier = async (id: string, declaration: DeclarationProjet) => {
    await modifierProjet(id, declaration);
    setEditionId(null);
    await rechargerApresEcriture();
  };

  return (
    <>
      {/* Le bandeau d'abord : une API injoignable se lit avant la liste vide
          qu'elle explique. */}
      <BanniereErreurApi erreur={erreur} />

      <section aria-label="Projets déclarés" className="flex flex-col gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          {/* L'espace qui suit le gras est **explicite** (#946, C1 du retex du
              2026-09-11) : écrit comme une espace de texte, il se perdait au
              rendu et la phrase donnait « racine sur le disqueet ce qu'elle
              expose ». Un jsdom ne le reproduit pas — d'où l'espace porté par
              une expression, qui ne dépend d'aucun nettoyage de JSX.

              Et la phrase ne dit plus « jamais en tapant un chemin » (C2) :
              l'explorateur offre justement d'y sauter par un chemin absolu, et
              elle contredisait l'écran qu'elle décrit. Ce qui reste vrai — et
              qui était ce qu'elle voulait dire — c'est qu'on n'a jamais **à**
              en taper un : la racine retenue vient toujours d'un dossier
              énuméré par l'API. */}
          <p className="max-w-2xl text-sm text-neutral-500 dark:text-neutral-400">
            Un projet, c&apos;est une <strong>racine sur le disque</strong>{" "}
            et ce qu&apos;elle expose aux agents. Le dossier se choisit dans
            l&apos;explorateur servi par le backend — sans jamais avoir à taper
            un chemin.
          </p>
          {!creationOuverte && (
            <Bouton
              icone={IconePlus}
              onClick={() => {
                setCreationOuverte(true);
                setEditionId(null);
              }}
            >
              Nouveau projet
            </Bouton>
          )}
        </div>

        {aOutiller !== null && (
          <EtapeOutillage
            projet={aOutiller}
            onTermine={finirOutillage}
            onReporte={finirOutillage}
          />
        )}

        {creationOuverte && (
          <FormulaireProjet
            enregistrer={declarer}
            onAnnuler={() => setCreationOuverte(false)}
          />
        )}

        {chargement && (
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            Chargement des projets…
          </p>
        )}
        {/* « Aucun projet » n'est dit que si la liste a vraiment été lue :
            l'afficher sur une API injoignable ferait passer une panne pour un
            backlog vide, et inviterait à re-déclarer des projets déjà là. */}
        {!chargement && erreur === null && projets.length === 0 && (
          <EtatVide
            icone={IconeDossier}
            message="Aucun projet déclaré. Les exécutions travaillent alors dans un espace jetable et leurs livrables restent dans le dossier de sortie — déclarer un projet, c'est leur donner une adresse."
          />
        )}
        {!chargement && projets.length > 0 && (
          <ul className="flex flex-col gap-3">
            {projets.map((projet) =>
              editionId === projet.id ? (
                <li key={projet.id}>
                  <FormulaireProjet
                    projet={projet}
                    enregistrer={(declaration) =>
                      modifier(projet.id, declaration)
                    }
                    onAnnuler={() => setEditionId(null)}
                  />
                </li>
              ) : (
                <CarteProjet
                  key={projet.id}
                  projet={projet}
                  onModifier={() => {
                    setEditionId(projet.id);
                    setCreationOuverte(false);
                  }}
                  onSupprime={rechargerApresEcriture}
                  onVersionne={rechargerApresEcriture}
                  onOutiller={() => {
                    setAOutiller(projet);
                    setCreationOuverte(false);
                    setEditionId(null);
                  }}
                />
              ),
            )}
          </ul>
        )}
      </section>
    </>
  );
}
