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
 *
 * ## Le troisième, ajouté par #938 : **on peut aussi déposer un dossier**
 *
 * « La racine ne se tape pas » ne dit pas « la racine ne se dépose pas » — et
 * jusqu'ici elle ne se déposait nulle part, parce qu'aucun navigateur ne donne
 * le chemin de ce qu'on lui dépose (docs/24 §4.7). La **fenêtre**, elle, le
 * donne : le dépôt est donc un troisième geste, à côté de l'explorateur de
 * l'API et du dialogue du poste — jamais à leur place.
 *
 * Trois choses le tiennent, et la première est celle qu'on oublierait :
 *
 * - **la zone n'est dessinée que là où le geste est possible**
 *   (`peutLireCheminDepose`). Une cible visible dans un onglet promettrait ce
 *   qu'aucun navigateur ne peut tenir — c'est la réserve du regard neuf sur la
 *   variante retenue, et le troisième critère du ticket : *hors fenêtre, rien
 *   ne change* ;
 * - **le chemin déposé passe par la même porte que les autres**
 *   (`POST /api/projets/racine`, EF-38) : la coque lit un chemin, elle
 *   n'autorise rien ;
 * - **les trois issues du dialogue sont rendues à l'identique** — rien
 *   d'exploitable (on ne touche à rien, on le dit) · déclarable (on le choisit)
 *   · lisible mais non déclarable (on **ouvre l'explorateur dessus**, avec le
 *   motif). C'est ce qui évite le cul-de-sac, et un dépôt n'a pas de raison d'en
 *   rendre d'autres.
 */

import { useState, type DragEvent } from "react";

import { IconeDossier, IconePlus } from "@/components/Icones";
import { Bouton, Carte, Champ } from "@/components/Primitives";
import { verdictRacine } from "@/lib/api";
import { cheminDuDossierDepose, peutLireCheminDepose } from "@/lib/poste";
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
  // Le dossier sur lequel l'explorateur s'ouvre quand ce n'est pas celui du
  // formulaire : un dossier déposé mais non déclarable, qu'on montre pour en
  // descendre d'un cran (#938). `null` le rend au dossier choisi.
  const [explorateurDepart, setExplorateurDepart] = useState<string | null>(null);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [survolDepot, setSurvolDepot] = useState(false);
  const [depotEnCours, setDepotEnCours] = useState(false);

  // Lu au rendu et non dans un effet : la capacité est posée par la coque avant
  // le premier script de la page et ne change jamais en cours de vie (#928).
  const posteDepose = peutLireCheminDepose();

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
    setExplorateurDepart(null);
    setRefus(null);
    // Le nom du dossier fait un premier jet de nom de projet — modifiable,
    // mais un champ pré-rempli vaut mieux qu'un champ à recopier.
    if (!nouveauDossier && nom.trim() === "") setNom(nomDepuisChemin(chemin));
  };

  /**
   * Un dossier déposé sur la zone (#938) — et ses trois issues, les mêmes que
   * celles du dialogue natif.
   *
   * Le chemin vient de la coque (`cheminDuDossierDepose`) et le **verdict** de
   * l'API (`verdictRacine`, EF-38) : c'est la seule porte, la même que
   * l'explorateur et que le dialogue. Rien n'est jugé ici.
   */
  const deposer = async (evenement: DragEvent<HTMLDivElement>) => {
    evenement.preventDefault();
    setSurvolDepot(false);
    // Pendant l'enregistrement, tout le formulaire est `disabled` : la zone,
    // elle, n'est pas un contrôle et ne l'est donc pas d'elle-même.
    if (enCours || depotEnCours) return;
    const fichier = evenement.dataTransfer.files[0];
    const chemin = fichier === undefined ? null : cheminDuDossierDepose(fichier);
    if (chemin === null) {
      // Un objet qui ne vient pas du disque (glissé depuis une page, une
      // sélection de texte) : on ne touche à rien, et on le **dit** — un dépôt
      // sans effet ni message ne se distingue pas d'une page figée.
      setRefus({
        motif: "depot-sans-chemin",
        message:
          "Ce dépôt n'a pas de chemin sur le disque — déposez un dossier depuis l'explorateur de votre poste.",
      });
      return;
    }
    setDepotEnCours(true);
    setRefus(null);
    try {
      const verdict = await verdictRacine(chemin);
      if (verdict.racine_valide && verdict.chemin !== null) {
        choisir(verdict.chemin);
        return;
      }
      setRefus(verdict.refus);
      // Lisible mais non déclarable (une racine de disque, le dossier
      // utilisateur nu) : on ouvre l'explorateur **dessus**, de quoi descendre
      // d'un cran plutôt que de recommencer. Une seule exception, et elle est
      // propre au dépôt : ce qui n'est pas un dossier n'a rien à explorer, et
      // le motif le dit déjà.
      if (verdict.chemin !== null && verdict.refus?.motif !== "pas-un-dossier") {
        setExplorateurDepart(verdict.chemin);
        setExplorateurOuvert(true);
      }
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
    } finally {
      setDepotEnCours(false);
    }
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
    <Carte
      balise="form"
      densite="aeree"
      aria-label={creation ? "Nouveau projet" : `Modifier ${projet.nom}`}
      onSubmit={(e) => {
        e.preventDefault();
        void soumettre();
      }}
      className="flex flex-col gap-4"
    >
      {/* Le titre porte son texte **en enfant direct** (#946, C12) : enveloppé
          dans un `<span>` aux côtés de l'icône, il ressortait sans nom dans
          l'arbre d'accessibilité — la carte n'avait alors de nom que par le
          `aria-label` du `<form>`, et son titre, lui, n'annonçait rien. La
          disposition ne bouge pas : le `flex` passe du span au titre. */}
      <h3 className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        {creation && <IconePlus className="size-4 shrink-0" />}
        {creation ? "Nouveau projet" : `Modifier « ${projet.nom} »`}
      </h3>

      <Champ
        id="projet-nom"
        libelle="Nom du projet"
        className="sm:max-w-sm"
        type="text"
        value={nom}
        onChange={(e) => setNom(e.target.value)}
        disabled={enCours}
        placeholder="Dépensio"
      />

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
          <Bouton
            variante="contour"
            ton="neutre"
            onClick={() => {
              // Le bouton rouvre toujours sur le dossier du formulaire : le
              // départ posé par un dépôt refusé ne vaut que pour ce dépôt-là.
              setExplorateurDepart(null);
              setExplorateurOuvert(!explorateurOuvert);
            }}
            disabled={enCours}
            aria-expanded={explorateurOuvert}
          >
            {dossier === null ? "Choisir un dossier…" : "Changer de dossier…"}
          </Bouton>
        </div>
        {/* La zone de dépôt (#938, variante C retenue par le regard neuf).
            Dessinée **seulement** là où le poste sait rendre un chemin réel :
            dans un onglet, rien n'apparaît et l'écran est celui d'avant, au
            pixel près (troisième critère du ticket).

            L'état « je peux lâcher » change la **forme** autant que la teinte —
            tireté → plein, plus l'aplat et le libellé : le filet a11y refuse un
            état porté par la seule couleur, et le banc de #471 en a fait un
            parti pris (docs/30 §1). */}
        {posteDepose && (
          <div
            onDragOver={(evenement) => {
              // Sans ce `preventDefault`, `drop` ne part jamais : c'est le
              // navigateur qui décide, et son défaut est de refuser.
              evenement.preventDefault();
              evenement.dataTransfer.dropEffect = "copy";
              setSurvolDepot(true);
            }}
            onDragLeave={() => setSurvolDepot(false)}
            onDrop={(evenement) => void deposer(evenement)}
            className={[
              "flex flex-col items-center gap-1 rounded-carte border border-dashed p-4 text-center transition-colors motion-reduce:transition-none",
              // `accent-creux` va avec `accent-texte`, jamais avec `texte` : la
              // palette ne promet AA que sur les paires qu'elle déclare, et
              // c'est celle-là que le socle montre (`app/socle/page.tsx`).
              survolDepot
                ? "border-solid border-accent bg-accent-creux text-accent-texte"
                : "border-bord-fort bg-surface-creuse",
            ].join(" ")}
          >
            <p
              className={[
                "flex items-center gap-2 text-annexe font-medium",
                survolDepot ? "" : "text-texte",
              ].join(" ")}
            >
              <IconeDossier
                className={[
                  "size-5 shrink-0",
                  survolDepot ? "" : "text-texte-secondaire",
                ].join(" ")}
                aria-hidden="true"
              />
              {depotEnCours
                ? "Lecture du dossier déposé…"
                : survolDepot
                  ? "Lâchez pour désigner ce dossier"
                  : "Déposer un dossier ici"}
            </p>
            {/* La contrainte se dit **dans** la zone, avant le refus — et sur
                sa propre ligne : filée derrière le titre elle passait pour une
                nuance de celui-ci (relevé par le regard neuf). Le survol la
                laisse hériter d'`accent-texte`, la graisse du titre suffisant à
                tenir la hiérarchie le temps d'un glisser. */}
            <p
              className={[
                "text-micro",
                survolDepot ? "" : "text-texte-secondaire",
              ].join(" ")}
            >
              Depuis l&apos;explorateur de votre poste. Un dossier, pas un
              fichier.
            </p>
          </div>
        )}
        {explorateurOuvert && (
          <ExplorateurDossiers
            cheminInitial={explorateurDepart ?? dossier}
            onChoisir={choisir}
            onFermer={() => {
              setExplorateurOuvert(false);
              setExplorateurDepart(null);
            }}
          />
        )}
        {nouveauDossier && (
          // L'erreur passe **dans** le champ : `aria-invalid` et
          // `aria-describedby` la lient à la saisie, là où le paragraphe qui la
          // suivait n'était rattaché à rien pour un lecteur d'écran.
          <Champ
            id="projet-nom-dossier"
            libelle="Nom du dossier à créer"
            className="sm:max-w-sm"
            monospace
            erreur={
              nomDossier !== "" && !nomDossierValide(nomDossier)
                ? "Un nom de dossier, pas un chemin : ni « / » ni « \\ ». Le dossier parent se choisit dans l'explorateur."
                : undefined
            }
            type="text"
            value={nomDossier}
            onChange={(e) => setNomDossier(e.target.value)}
            disabled={enCours}
            placeholder="depensio"
          />
        )}
        {racine !== null && (
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            Racine déclarée :{" "}
            <code className="font-mono break-all">{racine}</code>
          </p>
        )}
      </div>

      <div className="grid gap-3 @md:grid-cols-2">
        <Champ
          id="projet-perimetre-inclus"
          libelle="Périmètre — inclus (motifs séparés par des virgules)"
          monospace
          type="text"
          value={inclusTexte}
          onChange={(e) => setInclusTexte(e.target.value)}
          disabled={enCours}
          placeholder={texteDepuisMotifs(PERIMETRE_PAR_DEFAUT.inclus)}
        />
        <Champ
          id="projet-perimetre-exclus"
          libelle="Périmètre — exclus (l'emporte sur les inclus)"
          monospace
          type="text"
          value={exclusTexte}
          onChange={(e) => setExclusTexte(e.target.value)}
          disabled={enCours}
          placeholder={texteDepuisMotifs(PERIMETRE_PAR_DEFAUT.exclus)}
        />
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
        <Bouton type="submit" disabled={!pret} occupe={enCours}>
          {enCours
            ? "Enregistrement…"
            : creation
              ? "Déclarer le projet"
              : "Enregistrer les modifications"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="neutre"
          onClick={onAnnuler}
          disabled={enCours}
        >
          Annuler
        </Bouton>
      </div>
    </Carte>
  );
}
