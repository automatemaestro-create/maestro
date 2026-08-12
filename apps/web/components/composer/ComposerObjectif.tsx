"use client";

/**
 * Composer un objectif (#319, docs/05 §2.7.3) — l'écran qui manquait entre
 * l'intention et le run.
 *
 * Jusqu'ici, lancer une orchestration passait par `curl` : `POST /api/executions`
 * ne prenait qu'un objectif **texte** et `PosteVide` renvoyait l'utilisateur à la
 * ligne de commande. Un cahier des charges de quinze pages n'avait qu'un chemin,
 * le copier-coller. Cet écran est l'autre chemin : un objectif se **compose** —
 * du texte, des fichiers déposés, un dossier de références, des adresses — et ce
 * qu'il embarque se **voit avant de coûter**.
 *
 * Quatre partis pris tiennent l'écran, et aucun n'est cosmétique :
 *
 * - **le dossier vient de l'explorateur, jamais d'une saisie** (#223/#278) : un
 *   navigateur ne livre pas de chemin absolu, c'est le backend — qui tourne sur
 *   le poste — qui énumère. Le seul chemin absolu de cet écran est donc un
 *   chemin que l'API a rendu ;
 * - **l'aperçu est gratuit et il est un geste** (critère 2) : rien n'est lu tant
 *   qu'on ne le demande pas, et le demander ne lance rien. Toute modification
 *   d'une source **périme** le rapport plutôt que de le laisser décrire un état
 *   qui n'existe plus — un aperçu qui traîne est pire que pas d'aperçu ;
 * - **un refus s'affiche à l'endroit du geste refusé** (critère 3) : sur la
 *   source fautive quand l'API en donne l'`index`, sous le bouton sinon. La
 *   saisie est **conservée** dans tous les cas — un objectif de quinze lignes
 *   effacé par un refus de plafond est un objectif qu'on ne réécrit pas ;
 * - **le run est rattaché au projet actif** (#281), sans le demander : le projet
 *   est le cadre de tous les écrans, pas un champ de formulaire de plus.
 *
 * Deux temps pour les fichiers, et c'est le contrat de docs/05 §6.8 : l'aperçu
 * porte les **octets** (il ne dépose rien), le lancement porte les
 * **identifiants** rendus par `POST /api/sources`. Le même fichier voyage donc
 * deux fois par des chemins différents, parce que ce sont deux questions
 * différentes — « qu'est-ce que ça donnerait ? » et « garde ça pour le run ».
 */

import { useId, useMemo, useRef, useState } from "react";

import { ExplorateurDossiers } from "@/components/projets/ExplorateurDossiers";
import { BadgeEtat, Carte, EtatVide } from "@/components/Primitives";
import { IconeDossier, IconeFermer, IconeLienExterne } from "@/components/Icones";
import { RapportExtraction } from "@/components/composer/RapportExtraction";
import { RefusSource } from "@/components/composer/RefusSource";
import { ErreurSource, apercuSources, lancerExecution, televerserSources } from "@/lib/api";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { nomDepuisChemin } from "@/lib/projets";
import {
  declarationDe,
  formaterOctets,
  libelleType,
  type SourceComposee,
} from "@/lib/sources";
import {
  SOURCE_DOSSIER,
  SOURCE_FICHIER,
  SOURCE_URL,
  type RapportLecture,
  type ResumeExecution,
} from "@/lib/types";

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-corps text-neutral-900 placeholder:text-neutral-400 focus:border-emerald-500 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:placeholder:text-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-annexe font-medium text-neutral-600 dark:text-neutral-400";

const CLASSE_BOUTON_PRIMAIRE =
  "rounded-md bg-emerald-600 px-3 py-1.5 text-annexe font-medium text-white hover:bg-emerald-700 disabled:opacity-50";

const CLASSE_BOUTON_SECONDAIRE =
  "rounded-md border border-neutral-300 px-3 py-1.5 text-annexe font-medium hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800";

/** Le refus d'une exception quelconque — une panne réseau en est un aussi. */
function refusDepuis(erreur: unknown): ErreurSource {
  if (erreur instanceof ErreurSource) return erreur;
  return new ErreurSource(
    "api-injoignable",
    erreur instanceof Error ? erreur.message : "backend injoignable",
  );
}

/** Une clé locale stable : deux fichiers peuvent porter le même nom. */
let compteurCles = 0;
function cleSuivante(): string {
  compteurCles += 1;
  return `source-${compteurCles}`;
}

export function ComposerObjectif() {
  const { projet } = useEtatGlobal();
  const idObjectif = useId();
  const idUrl = useId();
  const entreeFichiers = useRef<HTMLInputElement>(null);

  const [objectif, setObjectif] = useState("");
  const [sources, setSources] = useState<SourceComposee[]>([]);
  const [url, setUrl] = useState("");
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);

  const [rapport, setRapport] = useState<RapportLecture | null>(null);
  const [apercuEnCours, setApercuEnCours] = useState(false);
  const [refusApercu, setRefusApercu] = useState<ErreurSource | null>(null);

  const [lancementEnCours, setLancementEnCours] = useState(false);
  const [refusLancement, setRefusLancement] = useState<ErreurSource | null>(null);
  const [lance, setLance] = useState<ResumeExecution | null>(null);

  const fichiers = useMemo(
    () =>
      sources
        .filter((source) => source.type === SOURCE_FICHIER && source.fichier !== null)
        .map((source) => source.fichier as File),
    [sources],
  );

  /**
   * Toute modification des sources **périme** l'aperçu et les refus qui le
   * visaient : un rapport qui survivrait au retrait d'un document décrirait un
   * état que plus rien ne produit, et un refus indexé désignerait la mauvaise
   * ligne. L'objectif, lui, ne périme rien — il n'entre pas dans l'extraction.
   */
  function changerSources(suite: (avant: SourceComposee[]) => SourceComposee[]) {
    setSources(suite);
    setRapport(null);
    setRefusApercu(null);
    setRefusLancement(null);
  }

  function deposer(choisis: FileList | null) {
    if (choisis === null || choisis.length === 0) return;
    const ajoutees = Array.from(choisis).map((fichier) => ({
      cle: cleSuivante(),
      type: SOURCE_FICHIER,
      nom: fichier.name,
      valeur: "",
      taille: fichier.size,
      id: null,
      fichier,
    }));
    changerSources((avant) => [...avant, ...ajoutees]);
  }

  function ajouterDossier(chemin: string) {
    changerSources((avant) => [
      ...avant,
      {
        cle: cleSuivante(),
        type: SOURCE_DOSSIER,
        nom: nomDepuisChemin(chemin),
        valeur: chemin,
        id: null,
        fichier: null,
      },
    ]);
    setExplorateurOuvert(false);
  }

  function ajouterUrl() {
    const propre = url.trim();
    if (propre === "") return;
    changerSources((avant) => [
      ...avant,
      {
        cle: cleSuivante(),
        type: SOURCE_URL,
        nom: propre,
        valeur: propre,
        id: null,
        fichier: null,
      },
    ]);
    setUrl("");
  }

  function retirer(cle: string) {
    changerSources((avant) => avant.filter((source) => source.cle !== cle));
  }

  async function voirApercu() {
    setApercuEnCours(true);
    setRefusApercu(null);
    try {
      setRapport(await apercuSources(sources.map(declarationDe), fichiers));
    } catch (erreur) {
      setRefusApercu(refusDepuis(erreur));
      setRapport(null);
    } finally {
      setApercuEnCours(false);
    }
  }

  async function lancer() {
    setLancementEnCours(true);
    setRefusLancement(null);
    try {
      // Les octets d'abord (#317) : le lancement ne porte que des identifiants,
      // et c'est ce qui garantit qu'un fichier n'atterrit jamais dans le dossier
      // de l'utilisateur. Aucun fichier déposé, aucun appel — un lancement sans
      // matière reste exactement celui d'avant la Phase 8.
      const televerses =
        fichiers.length === 0 ? [] : (await televerserSources(fichiers)).sources;
      let rang = 0;
      const declarees = sources.map((source) =>
        source.type === SOURCE_FICHIER && source.fichier !== null
          ? declarationDe({ ...source, id: televerses[rang++]?.id ?? null })
          : declarationDe(source),
      );
      const resume = await lancerExecution({
        objectif,
        plafond_cout_usd: null,
        plafond_tokens: null,
        timeout_tache_s: null,
        parallelisme: null,
        ticket: null,
        projet_id: projet.id,
        ...(declarees.length > 0 && { sources: declarees }),
      });
      setLance(resume);
      // Le succès, lui, remet l'écran à zéro : le run est parti, la saisie qui
      // l'a produit n'a plus de sens ici — c'est le seul cas où l'on efface.
      setObjectif("");
      setSources([]);
      setRapport(null);
    } catch (erreur) {
      setRefusLancement(refusDepuis(erreur));
    } finally {
      setLancementEnCours(false);
    }
  }

  const objectifPret = objectif.trim() !== "";
  const occupe = apercuEnCours || lancementEnCours;

  return (
    <section aria-label="Composer un objectif" className="space-y-4">
      {lance !== null && <RunLance resume={lance} onFermer={() => setLance(null)} />}

      <Carte balise="section" densite="aeree" className="space-y-4">
        <div>
          <label className={CLASSE_LIBELLE} htmlFor={idObjectif}>
            Objectif
            <textarea
              id={idObjectif}
              rows={5}
              value={objectif}
              onChange={(evenement) => setObjectif(evenement.target.value)}
              disabled={occupe}
              placeholder="Ce que Maestro doit obtenir, en langage naturel. Le Chef de projet le décomposera en tâches."
              className={CLASSE_CHAMP}
            />
          </label>
          <p className="mt-1 text-annexe text-neutral-500 dark:text-neutral-400">
            Le run sera rattaché au projet{" "}
            <strong className="font-medium">{projet.nom}</strong> — le projet
            actif est le cadre de l&apos;écran, pas un champ à remplir.
          </p>
        </div>

        <div className="space-y-2">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="text-corps font-medium">Sources</h2>
            <p className="text-annexe text-neutral-500 dark:text-neutral-400">
              La matière que l&apos;objectif embarque — déclarée et bornée, jamais
              un projet entier.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              disabled={occupe}
              onClick={() => entreeFichiers.current?.click()}
              className={CLASSE_BOUTON_SECONDAIRE}
            >
              Déposer des fichiers…
            </button>
            <input
              ref={entreeFichiers}
              type="file"
              multiple
              aria-label="Fichiers à joindre"
              onChange={(evenement) => {
                deposer(evenement.target.files);
                // L'entrée est remise à zéro : redéposer deux fois le même
                // fichier doit rester possible, or `change` ne part pas si la
                // valeur n'a pas changé.
                evenement.target.value = "";
              }}
              className="sr-only"
            />
            <button
              type="button"
              disabled={occupe}
              aria-expanded={explorateurOuvert}
              onClick={() => setExplorateurOuvert(!explorateurOuvert)}
              className={CLASSE_BOUTON_SECONDAIRE}
            >
              {explorateurOuvert ? "Fermer l'explorateur" : "Choisir un dossier de références…"}
            </button>
          </div>

          {explorateurOuvert && (
            <ExplorateurDossiers
              cheminInitial={null}
              onChoisir={ajouterDossier}
              onFermer={() => setExplorateurOuvert(false)}
            />
          )}

          <div className="flex flex-wrap items-end gap-2">
            <label className={`${CLASSE_LIBELLE} min-w-64 flex-1`} htmlFor={idUrl}>
              Adresse à lire
              <input
                id={idUrl}
                type="url"
                value={url}
                onChange={(evenement) => setUrl(evenement.target.value)}
                onKeyDown={(evenement) => {
                  if (evenement.key === "Enter") {
                    evenement.preventDefault();
                    ajouterUrl();
                  }
                }}
                disabled={occupe}
                placeholder="https://…"
                className={CLASSE_CHAMP}
              />
            </label>
            <button
              type="button"
              disabled={occupe || url.trim() === ""}
              onClick={ajouterUrl}
              className={CLASSE_BOUTON_SECONDAIRE}
            >
              Ajouter l&apos;adresse
            </button>
          </div>

          <ListeSources
            sources={sources}
            refus={refusApercu ?? refusLancement}
            occupe={occupe}
            onRetirer={retirer}
          />
        </div>
      </Carte>

      <Carte balise="section" densite="aeree" className="space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            disabled={occupe || sources.length === 0}
            onClick={() => void voirApercu()}
            className={CLASSE_BOUTON_SECONDAIRE}
          >
            {apercuEnCours ? "Lecture des sources…" : "Voir ce qui sera lu"}
          </button>
          <p className="text-annexe text-neutral-500 dark:text-neutral-400">
            Gratuit et sans effet : rien n&apos;est lancé, rien n&apos;est
            conservé.
          </p>
        </div>

        {/* Le refus de l'aperçu qui ne vise **aucune** source en particulier
            reste ici, au geste qui l'a produit ; celui qui en vise une est
            rendu sur sa ligne par `ListeSources`. */}
        {refusApercu !== null && refusApercu.index === null && (
          <RefusSource refus={refusApercu} titre="Aperçu refusé" />
        )}

        {rapport !== null && <RapportExtraction rapport={rapport} />}

        <div className="flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
          <button
            type="button"
            disabled={occupe || !objectifPret}
            onClick={() => void lancer()}
            className={CLASSE_BOUTON_PRIMAIRE}
          >
            {lancementEnCours ? "Lancement…" : "Lancer l'orchestration"}
          </button>
          <p className="text-annexe text-neutral-500 dark:text-neutral-400">
            {objectifPret
              ? "Le moteur décompose l'objectif en tâches et publie chaque étape sur le tableau de bord."
              : "Un objectif est nécessaire : c'est lui que le moteur décompose."}
          </p>
        </div>

        {refusLancement !== null && refusLancement.index === null && (
          <RefusSource refus={refusLancement} titre="Lancement refusé" />
        )}
      </Carte>
    </section>
  );
}

/** Les sources déclarées, chacune retirable — et chacune capable de porter son refus. */
function ListeSources({
  sources,
  refus,
  occupe,
  onRetirer,
}: {
  sources: SourceComposee[];
  refus: ErreurSource | null;
  occupe: boolean;
  onRetirer: (cle: string) => void;
}) {
  if (sources.length === 0) {
    return (
      <EtatVide
        icone={IconeDossier}
        message="Aucune source déclarée — le run partira sur l'objectif seul, ce qui reste un usage normal."
        releve="Un fichier déposé, un dossier de références ou une adresse s'ajoutent ci-dessus."
      />
    );
  }
  return (
    <ul aria-label="Sources déclarées" className="space-y-1.5">
      {sources.map((source, rang) => (
        <li
          key={source.cle}
          className="rounded-md border border-neutral-200 px-2.5 py-1.5 dark:border-neutral-800"
        >
          <div className="flex flex-wrap items-center gap-2">
            <BadgeEtat ton="neutre" contour>
              {libelleType(source.type)}
            </BadgeEtat>
            {source.type === SOURCE_DOSSIER && (
              <IconeDossier className="size-4 shrink-0 text-neutral-400" />
            )}
            {source.type === SOURCE_URL && (
              <IconeLienExterne className="size-4 shrink-0 text-neutral-400" />
            )}
            <span className="min-w-0 truncate text-corps">{source.nom}</span>
            {source.valeur !== "" && source.valeur !== source.nom && (
              <span className="min-w-0 truncate text-annexe text-neutral-500 dark:text-neutral-400">
                {source.valeur}
              </span>
            )}
            {source.taille !== undefined && (
              <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
                {formaterOctets(source.taille)}
              </span>
            )}
            <button
              type="button"
              disabled={occupe}
              onClick={() => onRetirer(source.cle)}
              aria-label={`Retirer ${source.nom}`}
              className="ml-auto rounded border border-neutral-300 p-1 text-neutral-500 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              <IconeFermer className="size-3.5" />
            </button>
          </div>
          {/* Le refus **sur la source qu'il vise** : « une source est trop
              grosse » sans dire laquelle obligerait à tout relire pour savoir
              quoi retirer. */}
          {refus !== null && refus.index === rang && (
            <div className="mt-1.5">
              <RefusSource refus={refus} titre="Source refusée" />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}

/** Ce qu'un lancement réussi laisse à l'écran : le run parti, et où le suivre. */
function RunLance({
  resume,
  onFermer,
}: {
  resume: ResumeExecution;
  onFermer: () => void;
}) {
  return (
    <Carte balise="section" ton="attentionClaire" densite="compacte">
      <div className="flex flex-wrap items-center gap-2">
        <BadgeEtat ton="positif" pastille>
          Run lancé
        </BadgeEtat>
        <span className="min-w-0 truncate text-corps">{resume.objectif}</span>
        <code className="font-mono text-annexe text-neutral-600 dark:text-neutral-300">
          {resume.run_id}
        </code>
        <button
          type="button"
          onClick={onFermer}
          aria-label="Masquer le run lancé"
          className="ml-auto rounded border border-neutral-300 p-1 text-neutral-500 hover:bg-neutral-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
        >
          <IconeFermer className="size-3.5" />
        </button>
      </div>
      <p className="mt-1 text-annexe text-neutral-600 dark:text-neutral-300">
        Les étapes arrivent d&apos;elles-mêmes sur le tableau de bord — rien à
        rafraîchir.
      </p>
      {/* Le rapport rendu par le **lancement** (#317) : il dit ce qui a
          réellement été lu, là où l'aperçu disait ce qui le serait. */}
      {resume.rapport !== undefined && (
        <div className="mt-2">
          <RapportExtraction rapport={resume.rapport} />
        </div>
      )}
    </Carte>
  );
}
