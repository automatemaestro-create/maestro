"use client";

/**
 * **Le premier lancement** (#642) : sur un poste neuf, la porte d'entrée ne s'ouvre
 * pas directement sur la création d'un projet. Elle mène d'abord, étape par étape,
 * à un produit qui marche : un modèle qui répond, un dossier où ranger les projets,
 * puis le premier projet. Rien n'est à éditer à la main.
 *
 * ## La forme retenue, sur pièces
 *
 * Variante B de #642, retenue par le regard neuf contre un assistant pas à pas (A)
 * et des préalables posés au-dessus de la conversation (C). Le commentaire
 * « Variante retenue » du ticket en donne les raisons. Elle s'appuie sur les
 * walkthroughs de VS Code, l'écran « LLM Preference » d'AnythingLLM et « Let's get
 * started! » de GitHub Desktop :
 *
 * - **les trois étapes se voient en entier**, chacune avec son état, porté par une
 *   forme et par une phrase, et l'étape courante ouverte sur son écran. Il n'y a
 *   **aucun « suivant »** : une étape se coche sur un fait lu à l'API
 *   (`lib/premierLancement`), et l'écran passe alors à celle qui reste ;
 * - **l'étape réutilise l'écran du produit**, elle ne le recopie pas : la carte du
 *   fournisseur par défaut (#1609) avec son épreuve, la section du répertoire des
 *   projets (#1022) avec ses dossiers autorisés (#1610), et la conversation où un
 *   projet naît (#1294). Leurs épreuves sont les seules ;
 * - **la fin est le premier projet**, pas un écran de félicitations.
 *
 * ## L'écran de l'étape ouverte, contre son étape
 *
 * L'écran se pose contre son étape, sans cadre, et de la même façon aux trois
 * étapes (#1617, variante B retenue par le regard neuf, d'après la colonne voisine
 * des walkthroughs de VS Code et l'`OnboardingView` de Stripe) :
 *
 * - **au large**, dans un volet à côté de la liste, bordé d'un filet sur lequel la
 *   ligne ouverte s'ouvre comme un onglet. Les trois étapes restent à l'écran
 *   quand celle du modèle est ouverte, et les cartes d'accès s'y posent sans
 *   cadre autour ;
 * - **plus étroit**, sous la ligne de son étape, le long du même filet.
 *
 * L'écran n'est jamais monté deux fois : sa place se décide en JavaScript, sur la
 * frontière `lg` du shell (`REQUETE_COLONNE_AU_LARGE`). L'état n'est dit qu'une
 * fois, par le résumé de l'étape, qui dit aussi à quoi sert le choix. Une forme
 * distingue chaque état, et celle de l'étape ouverte ne se lit pas comme un bouton
 * radio. Une étape repliée n'a aucun geste, et sa coche dit le même fait que sa
 * phrase.
 *
 * ## Quand il se montre
 *
 * À un poste sans projet dont le parcours n'a jamais été terminé
 * (`GET /api/accueil`), et à la demande depuis *Paramètres › Général*
 * (`demanderPremierLancement`). La naissance du premier projet le termine
 * (`POST /api/accueil/terminer`) : il ne se rejoue pas au démarrage suivant.
 */

import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import {
  IconeEtapeAFaire,
  IconeEtapeCourante,
  IconeEtapeEnAttente,
  IconeFlecheGauche,
  IconeStatutTerminee,
} from "@/components/Icones";
import { FournisseurParDefaut } from "@/components/parametres/FournisseurParDefaut";
import { ParametresProjets } from "@/components/parametres/ParametresProjets";
import { Bouton } from "@/components/Primitives";
import {
  chargerAccueil,
  chargerFournisseurDefaut,
  chargerFournisseurs,
  chargerRepertoireProjets,
  terminerAccueil,
} from "@/lib/api";
import { REQUETE_COLONNE_AU_LARGE } from "@/lib/preferences";
import {
  dossierFait,
  type EtatEtape,
  etapesDuParcours,
  ILLISIBLE,
  modeleFait,
  NUMEROS_ETAPES,
  type NumeroEtape,
  resumeDuDossier,
  resumeDuModele,
  resumeDuProjet,
} from "@/lib/premierLancement";
import type {
  CatalogueFournisseurs,
  EtatFournisseurDefaut,
  RepertoireProjets,
} from "@/lib/types";

import { NaissanceProjet } from "./NaissanceProjet";

/**
 * Ce que la porte sait du premier lancement : `inutile` quand elle n'a pas à le
 * demander (un poste qui a des projets n'y entre jamais), `lecture` le temps que
 * l'API réponde, puis `a-faire` ou `fait`.
 */
export type LecturePremierLancement = "inutile" | "lecture" | "a-faire" | "fait";

/**
 * Le premier lancement reste-t-il à faire ? La question ne se pose qu'à un poste
 * sans projet (`demander`). Une API qui ne répond pas laisse la porte telle
 * qu'elle était : on ne bloque jamais l'entrée sur cette lecture.
 */
export function usePremierLancement(demander: boolean): LecturePremierLancement {
  const [lu, setLu] = useState<"lecture" | "a-faire" | "fait">("lecture");
  useEffect(() => {
    if (!demander) return;
    let vivant = true;
    void chargerAccueil()
      .then((etat) => {
        if (vivant) setLu(etat.a_faire ? "a-faire" : "fait");
      })
      .catch(() => {
        if (vivant) setLu("fait");
      });
    return () => {
      vivant = false;
    };
  }, [demander]);
  return demander ? lu : "inutile";
}

/**
 * Un projet vient de naître dans le parcours : il est terminé. L'écriture peut
 * échouer sans dommage, car un poste qui a un projet n'est plus à son premier
 * lancement (`maestro.controltower.accueil`). Rien n'est donc dit à l'écran, qui
 * entre déjà dans le projet.
 */
function noterLaFinDuParcours(): void {
  void terminerAccueil().catch(() => {});
}

/**
 * La fenêtre est-elle assez large pour poser l'écran de l'étape à côté de la
 * liste ? La même frontière que les `lg:` du rendu, lue en JavaScript parce que
 * l'écran change de **place** dans l'arbre, et pas seulement d'apparence : le
 * monter aux deux endroits doublerait ses lectures et ses épreuves.
 */
function useAuLarge(): boolean {
  return useSyncExternalStore(
    abonnerAuLarge,
    () => window.matchMedia(REQUETE_COLONNE_AU_LARGE).matches,
    // Rien ne s'ouvre avant que les réglages soient lus, donc jamais au rendu
    // serveur : la valeur n'y place aucun écran.
    () => true,
  );
}

function abonnerAuLarge(rappel: () => void): () => void {
  const media = window.matchMedia(REQUETE_COLONNE_AU_LARGE);
  media.addEventListener("change", rappel);
  return () => media.removeEventListener("change", rappel);
}

/**
 * Le filet qui borde l'écran de l'étape ouverte, à côté de la liste ou sous sa
 * ligne. Au large, l'écran s'étire sur toute la hauteur des étapes : le filet
 * longe ainsi la ligne ouverte, même quand l'écran est court.
 */
const CLASSE_VOLET = "min-w-0 self-stretch border-l border-bord-fort pl-8";
/** Sous sa ligne, le filet part du symbole de l'étape. */
const CLASSE_SOUS_LIGNE = "ml-[1.3125rem] min-w-0 border-l border-bord-fort pt-3 pb-4 pl-4";

const ETAPES: Record<NumeroEtape, string> = {
  1: "Le modèle qui fait le travail",
  2: "Le dossier où naissent vos projets",
  3: "Votre premier projet",
};

/** L'état d'une ligne, dit aux lecteurs d'écran en tête de son résumé. */
const LIBELLE_ETAT: Record<EtatEtape | "courante", string> = {
  fait: "Fait",
  "a-faire": "À faire",
  courante: "En cours",
  attend: "En attente",
};

export function PremierLancement({
  projetsDeclares,
  onRevenir,
}: {
  /** Les projets déclarés sur ce poste : l'étape 3 est faite dès qu'il y en a un. */
  projetsDeclares: number;
  /**
   * Revenir à la liste des projets : absent au premier lancement, où il n'y a
   * rien vers quoi revenir, présent quand on revoit le parcours depuis les
   * Paramètres.
   */
  onRevenir?: () => void;
}) {
  const [etatModele, setEtatModele] = useState<
    EtatFournisseurDefaut | null | typeof ILLISIBLE
  >(null);
  const [catalogue, setCatalogue] = useState<CatalogueFournisseurs | null>(null);
  const [repertoire, setRepertoire] = useState<RepertoireProjets | null | typeof ILLISIBLE>(
    null,
  );
  // L'étape ouverte d'un clic, `null` : celle qui reste à faire.
  const [choisie, setChoisie] = useState<NumeroEtape | null>(null);

  // Une étape qui **vient** de se faire rend la main à la suivante (d'après VS
  // Code), même ouverte d'un clic. La transition seule compte : réenregistrer un
  // choix déjà fait laisse l'étape ouverte.
  const modeleEtaitFait = useRef<boolean | null>(null);
  const recevoirModele = useCallback((etat: EtatFournisseurDefaut) => {
    const fait = etat.choix !== null;
    if (modeleEtaitFait.current === false && fait) {
      setChoisie((avant) => (avant === 1 ? null : avant));
    }
    modeleEtaitFait.current = fait;
    setEtatModele(etat);
  }, []);
  const recevoirRepertoire = useCallback((lu: RepertoireProjets) => setRepertoire(lu), []);

  useEffect(() => {
    let vivant = true;
    void chargerFournisseurDefaut()
      .then((etat) => {
        if (vivant) recevoirModele(etat);
      })
      .catch(() => {
        if (vivant) setEtatModele(ILLISIBLE);
      });
    // Le catalogue ne sert qu'au libellé de l'accès retenu : sans lui, le résumé
    // nomme le fournisseur, il ne se dit pas en panne.
    void chargerFournisseurs()
      .then((lu) => {
        if (vivant) setCatalogue(lu);
      })
      .catch(() => {});
    void chargerRepertoireProjets()
      .then((lu) => {
        if (vivant) setRepertoire(lu);
      })
      .catch(() => {
        if (vivant) setRepertoire(ILLISIBLE);
      });
    return () => {
      vivant = false;
    };
  }, [recevoirModele]);

  const faitModele = modeleFait(etatModele);
  const faitDossier = dossierFait(repertoire);
  const { etats, courante: decidee } = etapesDuParcours(
    { modele: faitModele, dossier: faitDossier, projet: projetsDeclares > 0 },
    choisie,
  );
  // Aucune étape ne s'ouvre avant que le modèle et le dossier soient lus : les
  // deux lectures reviennent chacune à son heure, et décider sur la première
  // ouvrirait un écran le temps de l'autre, focus compris.
  const lu = faitModele !== null && faitDossier !== null;
  const courante = lu ? decidee : null;
  const resumes: Record<NumeroEtape, string> = {
    1: resumeDuModele(etatModele, catalogue),
    2: resumeDuDossier(repertoire),
    3: resumeDuProjet(projetsDeclares, faitModele === true),
  };

  const base = useId();
  const idLigne = (numero: NumeroEtape) => `${base}-etape-${numero}`;
  const idEcran = `${base}-ecran`;

  // Quand l'écran change sans clic (une étape vient de se cocher), le focus
  // quitte un bouton démonté. Il va à la ligne de l'étape qui s'ouvre, qui dit
  // où l'on est arrivé.
  // La première étape ouverte, une fois tout lu, ne prend pas le focus : rien n'a
  // changé sous les yeux de qui arrive. L'étape d'avant est comparée, plutôt
  // qu'un premier rendu compté : le double montage du mode strict ne doit pas
  // voler le focus à l'ouverture de la porte.
  const parUnClic = useRef(false);
  const precedente = useRef<NumeroEtape | null | undefined>(undefined);
  useEffect(() => {
    if (!lu) return;
    if (precedente.current === undefined) {
      precedente.current = courante;
      parUnClic.current = false;
      return;
    }
    if (precedente.current === courante) return;
    precedente.current = courante;
    if (parUnClic.current) {
      parUnClic.current = false;
      return;
    }
    if (courante !== null) document.getElementById(`${base}-etape-${courante}`)?.focus();
  }, [lu, courante, base]);

  const ouvrir = (numero: NumeroEtape) => {
    if (numero !== courante) parUnClic.current = true;
    setChoisie(numero);
  };

  const auLarge = useAuLarge();
  const ecran =
    courante === null ? null : (
      <section
        id={idEcran}
        // Nommé par le titre de l'étape seul : son état et son résumé sont dans
        // la ligne, et ne se relisent pas à l'entrée de l'écran.
        aria-labelledby={`${idLigne(courante)}-titre`}
        className={auLarge ? CLASSE_VOLET : CLASSE_SOUS_LIGNE}
      >
        {courante === 1 && <FournisseurParDefaut enEtape onEtat={recevoirModele} />}
        {courante === 2 && <ParametresProjets enEtape onRepertoire={recevoirRepertoire} />}
        {courante === 3 && (
          <NaissanceProjet enEtape onNe={noterLaFinDuParcours} parUnGeste={choisie === 3} />
        )}
      </section>
    );

  return (
    <div className="flex flex-col gap-6">
      {onRevenir !== undefined && courante !== null && (
        <div>
          <Bouton
            variante="contour"
            ton="neutre"
            icone={IconeFlecheGauche}
            onClick={onRevenir}
          >
            Revenir à mes projets
          </Bouton>
        </div>
      )}
      <div className="flex flex-col gap-2">
        <h1 className="text-page font-semibold tracking-tight text-texte">
          Mettre Maestro en route
        </h1>
        <p className="text-corps text-texte-secondaire">
          Chaque étape se coche quand ce qu&apos;elle règle a été vérifié. Tout se
          retrouve ensuite dans les Paramètres.
        </p>
      </div>

      <div className="grid gap-6 lg:grid-cols-[18rem_minmax(0,1fr)] lg:items-start lg:gap-0">
        <ol aria-label="Étapes du premier lancement" className="flex flex-col gap-2">
          {NUMEROS_ETAPES.map((numero) => (
            <LigneEtape
              key={numero}
              numero={numero}
              titre={ETAPES[numero]}
              resume={resumes[numero]}
              etat={etats[numero]}
              courante={courante === numero}
              id={idLigne(numero)}
              idEcran={idEcran}
              onOuvrir={() => ouvrir(numero)}
              ecran={!auLarge && courante === numero ? ecran : null}
            />
          ))}
        </ol>
        {auLarge && ecran}
        {lu && courante === null && (
          <div className="lg:pl-8">
            <ToutEstEnPlace onRevenir={onRevenir} />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * Une étape de la liste. Son titre est un bouton dans un titre, la forme d'un
 * accordéon (APG) : il ouvre l'écran de l'étape, à côté de la liste au large, et
 * sous la ligne (`ecran`) plus étroit. Une étape qui attend ne s'ouvre pas, et sa
 * phrase dit ce qu'elle attend.
 */
function LigneEtape({
  numero,
  titre,
  resume,
  etat,
  courante,
  id,
  idEcran,
  onOuvrir,
  ecran,
}: {
  numero: NumeroEtape;
  titre: string;
  resume: string;
  etat: EtatEtape;
  courante: boolean;
  id: string;
  idEcran: string;
  onOuvrir: () => void;
  /** L'écran de l'étape, quand il se pose sous sa ligne. */
  ecran: ReactNode;
}) {
  const [Icone, ton]: [typeof IconeEtapeAFaire, string] =
    etat === "fait"
      ? [IconeStatutTerminee, "text-positif-texte"]
      : courante
        ? [IconeEtapeCourante, "text-accent-texte"]
        : etat === "attend"
          ? [IconeEtapeEnAttente, "text-texte-secondaire"]
          : [IconeEtapeAFaire, "text-texte-secondaire"];
  const libelle = LIBELLE_ETAT[courante && etat !== "fait" ? "courante" : etat];
  return (
    <li aria-current={courante ? "step" : undefined}>
      <h2>
        <button
          type="button"
          id={id}
          onClick={onOuvrir}
          disabled={etat === "attend"}
          aria-expanded={courante}
          aria-controls={courante ? idEcran : undefined}
          // La ligne ouverte se distingue par son bord, en plus de sa forme, et prend
          // le fond de la page, celui de son écran. Au large, c'est un onglet : elle
          // déborde d'un pixel sur le filet du volet et y peint son bord droit à la
          // couleur de la page, si bien qu'elle s'ouvre sur son écran. Les autres
          // lignes s'arrêtent avant le filet. L'onglet n'arrondit que ses coins de
          // gauche, et son retrait de droite compense sa largeur : le texte de chaque
          // ligne garde la même largeur, et la hauteur des étapes ne bouge pas selon
          // celle qui est ouverte.
          className={`flex w-full items-start gap-3 border p-3 text-left ${
            courante
              ? "rounded-l-carte border-bord-fort bg-background max-lg:rounded-r-carte lg:relative lg:z-10 lg:w-[calc(100%+1px)] lg:border-r-background lg:pr-[calc(1.75rem+1px)]"
              : "rounded-carte border-bord bg-surface enabled:hover:bg-survol lg:w-[calc(100%-1rem)]"
          } disabled:cursor-default`}
        >
          <Icone className={`mt-0.5 size-5 shrink-0 ${ton}`} />
          <span className="flex min-w-0 flex-col gap-0.5">
            <span
              id={`${id}-titre`}
              className={`text-corps font-semibold ${
                etat === "attend" ? "text-texte-secondaire" : "text-texte"
              }`}
            >
              {numero}. {titre}
            </span>
            <span className="text-annexe font-normal wrap-anywhere text-texte-secondaire">
              <span className="sr-only">{libelle}. </span>
              {resume}
            </span>
          </span>
        </button>
      </h2>
      {ecran}
    </li>
  );
}

/**
 * Les trois étapes faites, ce qui n'arrive qu'en revoyant le parcours (au premier
 * lancement, le projet né ouvre le projet). Le geste ramène aux projets.
 */
function ToutEstEnPlace({ onRevenir }: { onRevenir?: () => void }) {
  return (
    <div className="flex flex-col items-start gap-3">
      <p className="text-corps text-texte">
        Tout est en place : un modèle éprouvé, un dossier pour vos projets, et des
        projets qui vous attendent.
      </p>
      {onRevenir !== undefined && <Bouton onClick={onRevenir}>Ouvrir mes projets</Bouton>}
    </div>
  );
}
