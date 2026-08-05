"use client";

/**
 * Le formulaire de déclaration d'un projet (#225) : nom, origine, racine et
 * périmètre. Il sert la création comme la modification — `PUT /api/projets/{id}`
 * étant un **remplacement intégral** (docs/05 §6.7), les deux gestes portent
 * exactement les mêmes champs, et un formulaire unique est ce qui garantit
 * qu'ils ne divergent pas.
 *
 * Deux partis pris à connaître :
 *
 * 1. **La racine ne se tape pas.** Elle vient toujours de l'explorateur servi
 *    par l'API (critère #225). L'origine « nouveau dossier » — où le dossier
 *    n'existe pas encore, donc où l'explorateur ne peut pas le montrer — se
 *    résout en deux temps : le **parent** est choisi dans l'explorateur, et
 *    l'utilisateur ne saisit qu'un **nom de dossier**, jamais un chemin (le
 *    champ refuse d'ailleurs les séparateurs).
 * 2. **L'origine ne se modifie pas après coup.** Elle raconte comment le projet
 *    est né ; la réécrire ne changerait rien sur le disque et mentirait sur
 *    l'historique. En modification, elle s'affiche, elle ne s'édite pas — et
 *    c'est bien celle du projet qui repart dans le `PUT`.
 */

import { useState } from "react";

import {
  cheminEnfant,
  motifsDepuisTexte,
  nomDepuisChemin,
  nomDossierValide,
  PERIMETRE_PAR_DEFAUT,
  texteDepuisMotifs,
  libelleOrigine,
} from "@/lib/projets";
import type { DeclarationProjet, Projet, RefusProjet } from "@/lib/types";

import { ExplorateurDossiers, refusDepuis, RefusMotive } from "./ExplorateurDossiers";

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm font-normal " +
  "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 " +
  "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-xs font-medium text-neutral-600 dark:text-neutral-400";

export function FormulaireProjet({
  projet,
  enregistrer,
  onAnnuler,
}: {
  /** Le projet modifié, ou `undefined` pour une déclaration neuve. */
  projet?: Projet;
  /** Écrit la déclaration ; lève un refus motivé que ce formulaire affiche. */
  enregistrer: (declaration: DeclarationProjet) => Promise<void>;
  onAnnuler: () => void;
}) {
  const creation = projet === undefined;
  const [nom, setNom] = useState(projet?.nom ?? "");
  const [origine, setOrigine] = useState(projet?.origine ?? "existant");
  // En création avec « nouveau dossier », c'est le **parent** ; partout
  // ailleurs, la racine elle-même.
  const [dossier, setDossier] = useState<string | null>(projet?.racine ?? null);
  const [nomDossier, setNomDossier] = useState("");
  const [inclusTexte, setInclusTexte] = useState(
    projet ? texteDepuisMotifs(projet.perimetre.inclus) : "",
  );
  const [exclusTexte, setExclusTexte] = useState(
    projet ? texteDepuisMotifs(projet.perimetre.exclus) : "",
  );
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);

  const nouveauDossier = creation && origine === "nouveau";
  const racine = nouveauDossier
    ? dossier === null
      ? null
      : cheminEnfant(dossier, nomDossier)
    : dossier;
  const pret =
    nom.trim() !== "" &&
    racine !== null &&
    (!nouveauDossier || nomDossierValide(nomDossier));

  const choisir = (chemin: string) => {
    setDossier(chemin);
    setExplorateurOuvert(false);
    // Le nom du dossier fait un premier jet de nom de projet — modifiable,
    // mais un champ pré-rempli vaut mieux qu'un champ à recopier.
    if (!nouveauDossier && nom.trim() === "") setNom(nomDepuisChemin(chemin));
  };

  const soumettre = async () => {
    if (racine === null) return;
    setEnCours(true);
    setRefus(null);
    try {
      await enregistrer({
        nom: nom.trim(),
        racine,
        origine,
        inclus: motifsDepuisTexte(inclusTexte),
        exclus: motifsDepuisTexte(exclusTexte),
      });
    } catch (erreur) {
      // Le refus s'affiche **dans** le formulaire, la saisie est conservée :
      // une racine refusée se corrige, elle ne fait pas tout recommencer.
      setRefus(refusDepuis(erreur));
      setEnCours(false);
    }
    // Succès : la liste remplace ce formulaire — ne plus toucher à l'état d'un
    // composant démonté.
  };

  return (
    <form
      aria-label={creation ? "Nouveau projet" : `Modifier ${projet.nom}`}
      onSubmit={(e) => {
        e.preventDefault();
        void soumettre();
      }}
      className="flex flex-col gap-4 rounded-lg border border-neutral-200 bg-white p-4 shadow-sm dark:border-neutral-800 dark:bg-neutral-900"
    >
      <h3 className="text-sm font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        {creation ? "➕ Nouveau projet" : `Modifier « ${projet.nom} »`}
      </h3>

      <label className={CLASSE_LIBELLE + " sm:max-w-sm"}>
        Nom du projet
        <input
          type="text"
          value={nom}
          onChange={(e) => setNom(e.target.value)}
          disabled={enCours}
          placeholder="Dépensio"
          className={CLASSE_CHAMP}
        />
      </label>

      {creation ? (
        <fieldset className="flex flex-col gap-1" disabled={enCours}>
          <legend className="text-xs font-medium text-neutral-600 dark:text-neutral-400">
            Origine
          </legend>
          <div className="flex flex-wrap gap-4">
            {(["existant", "nouveau"] as const).map((valeur) => (
              <label
                key={valeur}
                className="flex items-center gap-2 text-sm font-normal"
              >
                <input
                  type="radio"
                  name="origine"
                  value={valeur}
                  checked={origine === valeur}
                  onChange={() => setOrigine(valeur)}
                />
                {libelleOrigine(valeur)}
              </label>
            ))}
          </div>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            {origine === "nouveau"
              ? "Le dossier est créé à la déclaration, sous le dossier parent choisi."
              : "Le dossier doit déjà être là ; s'il est versionné, son dépôt Git est constaté."}
          </p>
        </fieldset>
      ) : (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Origine : <strong>{libelleOrigine(origine)}</strong> — elle raconte
          comment le projet est né et ne se réécrit pas.
        </p>
      )}

      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-neutral-600 dark:text-neutral-400">
          {nouveauDossier ? "Dossier parent" : "Racine du projet"}
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <code className="min-w-0 flex-1 font-mono text-xs break-all text-neutral-800 dark:text-neutral-200">
            {dossier ?? "aucun dossier choisi"}
          </code>
          <button
            type="button"
            onClick={() => setExplorateurOuvert(!explorateurOuvert)}
            disabled={enCours}
            aria-expanded={explorateurOuvert}
            className="shrink-0 rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            {dossier === null ? "Choisir un dossier…" : "Changer de dossier…"}
          </button>
        </div>
        {explorateurOuvert && (
          <ExplorateurDossiers
            cheminInitial={dossier}
            onChoisir={choisir}
            onFermer={() => setExplorateurOuvert(false)}
          />
        )}
        {nouveauDossier && (
          <>
            <label className={CLASSE_LIBELLE + " sm:max-w-sm"}>
              Nom du dossier à créer
              <input
                type="text"
                value={nomDossier}
                onChange={(e) => setNomDossier(e.target.value)}
                disabled={enCours}
                placeholder="depensio"
                className={CLASSE_CHAMP + " font-mono"}
              />
            </label>
            {nomDossier !== "" && !nomDossierValide(nomDossier) && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                Un nom de dossier, pas un chemin : ni « / » ni « \ ». Le dossier
                parent se choisit dans l&apos;explorateur.
              </p>
            )}
          </>
        )}
        {racine !== null && (
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            Racine déclarée :{" "}
            <code className="font-mono break-all">{racine}</code>
          </p>
        )}
      </div>

      <div className="grid gap-3 @md:grid-cols-2">
        <label className={CLASSE_LIBELLE}>
          Périmètre — inclus (motifs séparés par des virgules)
          <input
            type="text"
            value={inclusTexte}
            onChange={(e) => setInclusTexte(e.target.value)}
            disabled={enCours}
            placeholder={texteDepuisMotifs(PERIMETRE_PAR_DEFAUT.inclus)}
            className={CLASSE_CHAMP + " font-mono"}
          />
        </label>
        <label className={CLASSE_LIBELLE}>
          Périmètre — exclus (l&apos;emporte sur les inclus)
          <input
            type="text"
            value={exclusTexte}
            onChange={(e) => setExclusTexte(e.target.value)}
            disabled={enCours}
            placeholder={texteDepuisMotifs(PERIMETRE_PAR_DEFAUT.exclus)}
            className={CLASSE_CHAMP + " font-mono"}
          />
        </label>
      </div>
      <p className="-mt-2 text-xs text-neutral-500 dark:text-neutral-400">
        Laissés vides, les deux champs retombent sur les défauts du modèle.
      </p>

      {refus && (
        <RefusMotive
          refus={refus}
          titre={creation ? "Déclaration refusée" : "Modification refusée"}
        />
      )}

      <div className="flex flex-wrap items-center gap-3">
        <button
          type="submit"
          disabled={enCours || !pret}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enCours
            ? "Enregistrement…"
            : creation
              ? "Déclarer le projet"
              : "Enregistrer les modifications"}
        </button>
        <button
          type="button"
          onClick={onAnnuler}
          disabled={enCours}
          className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
        >
          Annuler
        </button>
      </div>
    </form>
  );
}
