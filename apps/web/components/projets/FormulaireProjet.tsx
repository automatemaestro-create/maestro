"use client";

/**
 * Le formulaire de **modification** d'un projet déclaré (#225) : nom, racine et
 * périmètre. `PUT /api/projets/{id}` étant un **remplacement intégral** (docs/05
 * §6.7), il porte tous les champs de la déclaration, et l'origine repart telle
 * qu'elle est.
 *
 * ⚠ **Il ne crée plus** (#1294, docs/43 §2.2). Un projet naît dans la
 * conversation (`NaissanceProjet`) : l'orchestration comprend ce qu'on veut faire
 * et propose nom, dossier et versionnement sur une carte. Tout ce qui ne servait
 * qu'à la création est parti avec elle — le choix de l'origine, le dossier parent
 * prérempli par le répertoire des projets (#1022, qui vit désormais dans les faits
 * que la conversation reçoit), le nom du dossier à créer. Ce formulaire garde la
 * **gestion** de l'écran Projets.
 *
 * Deux partis pris à connaître :
 *
 * 1. **La racine ne se tape pas.** Elle vient toujours de l'explorateur servi
 *    par l'API (critère #225), du dialogue du poste (#278), ou d'un dossier
 *    déposé (#938).
 * 2. **L'origine ne se modifie pas après coup.** Elle raconte comment le projet
 *    est né ; la réécrire ne changerait rien sur le disque et mentirait sur
 *    l'historique. Elle s'affiche, elle ne s'édite pas — et c'est bien celle du
 *    projet qui repart dans le `PUT`.
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

import { IconeDossier } from "@/components/Icones";
import { Bouton, Carte, Champ } from "@/components/Primitives";
import { verdictRacine } from "@/lib/api";
import { cheminDuDossierDepose, peutLireCheminDepose } from "@/lib/poste";
import {
  motifsDepuisTexte,
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
  /** Le projet modifié. */
  projet: Projet;
  /** Écrit la déclaration ; lève un refus motivé que ce formulaire affiche. */
  enregistrer: (declaration: DeclarationProjet) => Promise<void>;
  onAnnuler: () => void;
}) {
  const [nom, setNom] = useState(projet.nom);
  const [racine, setRacine] = useState(projet.racine);
  const [inclusTexte, setInclusTexte] = useState(
    texteDepuisMotifs(projet.perimetre.inclus),
  );
  const [exclusTexte, setExclusTexte] = useState(
    texteDepuisMotifs(projet.perimetre.exclus),
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

  const pret = nom.trim() !== "" && racine !== "";

  const choisir = (chemin: string) => {
    setRacine(chemin);
    setExplorateurOuvert(false);
    setExplorateurDepart(null);
    setRefus(null);
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
    setEnCours(true);
    setRefus(null);
    try {
      await enregistrer({
        nom: nom.trim(),
        racine,
        origine: projet.origine,
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
      aria-label={`Modifier ${projet.nom}`}
      onSubmit={(e) => {
        e.preventDefault();
        void soumettre();
      }}
      className="flex flex-col gap-4"
    >
      {/* Le titre porte son texte **en enfant direct** (#946, C12) : enveloppé
          dans un `<span>` aux côtés de l'icône, il ressortait sans nom dans
          l'arbre d'accessibilité. */}
      <h3 className="flex items-center gap-1.5 text-sm font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        {`Modifier « ${projet.nom} »`}
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

      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Origine : <strong>{libelleOrigine(projet.origine)}</strong> — elle raconte
        comment le projet est né et ne se réécrit pas.
      </p>

      <div className="flex flex-col gap-2">
        <p className="text-xs font-medium text-neutral-600 dark:text-neutral-400">
          Racine du projet
        </p>
        <div className="flex flex-wrap items-center gap-3">
          <code className="min-w-0 flex-1 font-mono text-xs break-all text-neutral-800 dark:text-neutral-200">
            {racine}
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
            Changer de dossier…
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
                nuance de celui-ci (relevé par le regard neuf). */}
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
            cheminInitial={explorateurDepart ?? racine}
            onChoisir={choisir}
            onFermer={() => {
              setExplorateurOuvert(false);
              setExplorateurDepart(null);
            }}
          />
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

      {refus && <RefusMotive refus={refus} titre="Modification refusée" />}

      <div className="flex flex-wrap items-center gap-3">
        <Bouton type="submit" disabled={!pret} occupe={enCours}>
          {enCours ? "Enregistrement…" : "Enregistrer les modifications"}
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
