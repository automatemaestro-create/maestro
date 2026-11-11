"use client";

/**
 * L'explorateur de dossiers de l'écran Projets (#225), servi par l'API (#223).
 *
 * Un navigateur ne livre jamais de chemin absolu — ni un `<input type="file">`,
 * ni un glisser-déposer : c'est donc le backend, qui tourne sur le poste, qui
 * énumère (docs/05 §2.7). Ce composant ne fabrique aucun chemin : il n'affiche
 * et ne rend que des chemins **énumérés par l'API**, ce qui est exactement ce
 * que le critère « aucun chemin absolu saisi à la main » demande.
 *
 * Le parti pris qui structure le rendu : **un refus n'est pas une liste vide**.
 * « ce dossier n'a pas de sous-dossier » et « je refuse de regarder là » sont
 * deux réponses différentes, et les confondre rend un explorateur inutilisable.
 * Un refus garde donc la page précédente à l'écran, s'affiche avec son motif et
 * laisse toujours une porte de sortie (remonter, revenir aux racines) — l'erreur
 * ne casse pas la navigation (critère #225).
 *
 * Un dossier refusé **hors des dossiers explorables** s'autorise d'ici (#1610),
 * quand l'API dit que `valider_racine` l'accepterait : le geste naît sous le
 * refus, et sa confirmation reste dans l'explorateur, sans boîte modale au
 * milieu d'une navigation (variante retenue sur le ticket). Sinon, une ligne dit
 * pourquoi le dossier ne peut pas l'être : jamais un bouton qui échouerait.
 *
 * Un composant **partagé**, enfin, et rendu aussi bien seul (écran Projets,
 * #225) qu'à l'intérieur du formulaire de projet (porte d'entrée, #279) : il ne
 * contient donc **aucun `<form>`** (#312). HTML interdit de les imbriquer —
 * l'analyseur jette le formulaire interne du HTML rendu côté serveur là où
 * React le crée côté client, d'où une erreur d'hydratation et une soumission
 * qui n'appartient à personne.
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import { IconeDossier, IconeFlecheHaut } from "@/components/Icones";
import { BadgeEtat, Bouton, classesCarte } from "@/components/Primitives";
import {
  autoriserDossier,
  chargerDisponibiliteSelecteur,
  chargerExplorateur,
  ErreurApi,
  ErreurProjet,
  ouvrirSelecteurNatif,
  verdictRacine,
} from "@/lib/api";
import { choisirDossierDuPoste, peutChoisirDossier } from "@/lib/poste";
import { conseilMotif, libelleMotif } from "@/lib/projets";
import type {
  ChoixSelecteur,
  DisponibiliteSelecteur,
  OrigineDossier,
  PageExplorateur,
  RefusProjet,
} from "@/lib/types";

/**
 * Le refus porté par une exception — une panne réseau en est un aussi.
 *
 * Une API qui n'a **pas répondu** (typée à la source, `ErreurApi.injoignable`) se
 * dit avec le geste qui y répond, jamais avec le chemin de la route ni l'erreur
 * brute du navigateur (même règle que `PieceDOutillage`, #1161 ; vu sous la
 * correction d'une équipe à la relecture de #1331).
 */
export function refusDepuis(erreur: unknown): RefusProjet {
  if (erreur instanceof ErreurProjet) {
    return {
      motif: erreur.motif,
      message: erreur.message,
      ...(erreur.autorisation !== null && { autorisation: erreur.autorisation }),
    };
  }
  if (erreur instanceof ErreurApi && erreur.statut === null) {
    return {
      motif: "api-injoignable",
      message:
        "L'API n'a pas répondu. Vérifiez que maestro-api tourne, puis réessayez.",
    };
  }
  return {
    motif: "api-injoignable",
    message: erreur instanceof Error ? erreur.message : String(erreur),
  };
}


/**
 * Le dialogue de dossier **de la fenêtre**, rendu sous la forme exacte de la
 * route qui l'ouvre depuis le backend (#938).
 *
 * C'est délibéré, et c'est ce qui fait tenir le reste : `parcourirNatif` traite
 * les **trois mêmes issues** (annulé · déclarable · lisible mais non déclarable)
 * sans savoir laquelle des deux voies a répondu. La ligne de partage est une
 * **capacité**, pas une plateforme — ENF-12 (`lib/poste`).
 *
 * Et le verdict vient de `POST /api/projets/racine`, jamais d'ici : les
 * frontières d'EF-38 vivent en un seul endroit, et la coque ne fait que lire un
 * chemin (`apps/desktop/preload.js`).
 */
async function choisirDepuisLaFenetre(
  depart: string | null,
): Promise<ChoixSelecteur> {
  const chemin = await choisirDossierDuPoste(depart);
  // Annuler n'est pas une erreur : même contrat qu'`ouvrirSelecteurNatif`.
  if (chemin === null) {
    return { annule: true, chemin: null, racine_valide: false, refus: null };
  }
  return { annule: false, ...(await verdictRacine(chemin)) };
}

/**
 * Ce que dit la pastille d'un point d'entrée (#278). Le libellé répond à
 * « pourquoi ce dossier m'est-il proposé ? » — sans lui, le dossier utilisateur,
 * un disque et le parent d'un projet se ressemblent au point de brouiller la
 * lecture de la page d'entrée.
 */
const LIBELLE_ORIGINE: Record<OrigineDossier, string> = {
  repertoire: "répertoire des projets",
  utilisateur: "dossier utilisateur",
  recent: "récent",
  projet: "projet déclaré",
  volume: "disque",
  configuree: "racine configurée",
  autorise: "dossier autorisé",
};

/** Le bandeau d'un refus : sa phrase, le geste qui en sort, et son code. */
export function RefusMotive({
  refus,
  titre,
  conseil: conseilImpose,
  ton = "attention",
}: {
  refus: RefusProjet;
  titre: string;
  /** Remplace le conseil du motif quand l'appelant en sait plus (#1610). */
  conseil?: string;
  /**
   * `attention` pour un refus motivé ; `alerte` pour une panne (API
   * injoignable), au ton de la bannière que les autres écrans lui donnent.
   */
  ton?: "attention" | "alerte";
}) {
  const conseil = conseilImpose ?? conseilMotif(refus.motif);
  // `wrap-anywhere` : un message porte des chemins sans espace, qui doivent se
  // couper au bord du bandeau plutôt que d'en sortir à la largeur d'un téléphone.
  // Seules les couleurs changent d'un ton à l'autre : la forme est celle du
  // bandeau de refus, écrite une fois.
  const couleurs =
    ton === "alerte"
      ? "border-alerte bg-alerte-creux text-alerte-texte"
      : "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200";
  return (
    <div
      role="alert"
      className={`rounded-md border px-3 py-2 text-xs wrap-anywhere ${couleurs}`}
    >
      <p className="font-medium">
        {titre} — {refus.message}
      </p>
      {conseil && <p className="mt-1">{conseil}</p>}
      {/* Le motif **en mots**, pas son identifiant (#946, C7) : la ligne
          affichait « motif : projet-inconnu », soit le code de l'API rendu tel
          quel à quelqu'un qui ne l'a jamais lu. Un motif que la table ne connaît
          pas se rend encore brut — un code affiché sans fard vaut mieux qu'une
          phrase inventée —, et le `font-mono` part avec la traduction : il
          annonçait justement du code. */}
      <p
        className={
          ton === "alerte"
            ? "mt-1 text-alerte-texte"
            : "mt-1 text-amber-700 dark:text-amber-400"
        }
      >
        motif : {libelleMotif(refus.motif)}
      </p>
    </div>
  );
}

/**
 * Pourquoi un dossier hors de la frontière ne s'autorise pas, en mots (#1610).
 *
 * Le libellé du motif et son conseil, jamais le message de `valider_racine` :
 * il redit le motif et écrit le chemin à la façon du système (`E:\`), quand
 * tout l'explorateur l'écrit en POSIX. Un motif que la table ne connaît pas
 * garde son message, faute de mieux.
 */
/** Le dernier segment d'un chemin POSIX : ce qu'on lit d'un dossier dans un bouton. */
function nomDuDossier(chemin: string): string {
  return chemin.replace(/\/+$/, "").split("/").pop() || chemin;
}

function phraseIndeclarable(refus: { motif: string; message: string }): string {
  const libelle = libelleMotif(refus.motif);
  const raison = libelle === refus.motif ? refus.message : libelle;
  const suite = conseilMotif(refus.motif);
  return `Ce dossier ne peut pas être autorisé — ${raison}.${suite ? ` ${suite}` : ""}`;
}

export function ExplorateurDossiers({
  cheminInitial,
  onChoisir,
  onFermer,
  onAutorise,
  actualisation,
}: {
  /** Le dossier ouvert à l'arrivée — `null` : les racines explorables. */
  cheminInitial: string | null;
  onChoisir: (chemin: string) => void;
  onFermer: () => void;
  /** Un dossier vient d'être autorisé (#1610) : qui en tient la liste la relit. */
  onAutorise?: (chemin: string) => void;
  /**
   * Change quand une autorisation a été retirée hors de l'explorateur (#1610) :
   * la page ouverte se relit, pour que ses pastilles « dossier autorisé » ne
   * contredisent pas la liste qui vient de perdre le dossier.
   */
  actualisation?: number;
}) {
  const [page, setPage] = useState<PageExplorateur | null>(null);
  const [chargement, setChargement] = useState(true);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [selecteur, setSelecteur] = useState<DisponibiliteSelecteur | null>(null);
  const [ouvertureNative, setOuvertureNative] = useState(false);
  const [saisie, setSaisie] = useState("");
  // La confirmation d'une autorisation (#1610) : ouverte, en vol, refusée.
  const [confirmation, setConfirmation] = useState(false);
  const [autorisationEnVol, setAutorisationEnVol] = useState(false);
  const [refusAutorisation, setRefusAutorisation] = useState<RefusProjet | null>(
    null,
  );
  const zoneConfirmation = useRef<HTMLDivElement>(null);
  const rangeeRefus = useRef<HTMLDivElement>(null);
  const rendreLeFocus = useRef(false);
  const titreConfirmation = useId();

  // Lu au rendu et non dans un effet : la capacité est posée par la coque avant
  // le premier script de la page et ne change jamais en cours de vie (même
  // motif qu'`AnnonceIssueRun`, #928). Dans la fenêtre, le dialogue est
  // **toujours** ouvrable — c'est le premier critère de #938 : `selecteur-hors-
  // poste` (backend joint depuis le réseau) et `selecteur-sans-outil` (ni
  // PowerShell, ni osascript, ni zenity/kdialog) n'ont plus d'objet quand ce
  // n'est plus le backend qui ouvre la fenêtre.
  const posteChoisit = peutChoisirDossier();

  /**
   * Ouvre `chemin`, et décide de ce qu'il advient du bandeau de refus.
   *
   * `refusConserve` est ce qui reste affiché **quand l'ouverture réussit** :
   * `null` dans le cas courant (une navigation efface le refus précédent, qui
   * ne parle plus du dossier affiché), le refus du sélecteur natif quand on
   * ouvre l'explorateur *sur* un dossier lisible mais non déclarable. Sans ce
   * paramètre, le motif posé juste avant l'appel était **effacé par le succès
   * de l'ouverture** : le seul cas où le refus et la page qu'on regarde parlent
   * du même dossier était aussi le seul où le refus disparaissait.
   *
   * Une ouverture qui **échoue** garde son propre refus : il explique pourquoi
   * on ne peut même pas regarder, ce qui prime sur « ce dossier n'est pas
   * déclarable ».
   */
  const ouvrir = useCallback(
    async (chemin: string | null, refusConserve: RefusProjet | null = null) => {
      setChargement(true);
      // Une navigation referme une confirmation en cours : elle parlait du
      // dossier refusé, que la page suivante ne montre plus.
      setConfirmation(false);
      setRefusAutorisation(null);
      try {
        setPage(await chargerExplorateur(chemin));
        setRefus(refusConserve);
      } catch (erreur) {
        // La page précédente reste affichée : un refus ne doit pas laisser
        // l'utilisateur devant un panneau vide dont il ne peut plus sortir.
        setRefus(refusDepuis(erreur));
      } finally {
        setChargement(false);
      }
    },
    [],
  );

  // Le dossier que le refus propose d'autoriser — le chemin vient de l'API, en
  // POSIX, jamais de la saisie : c'est celui que `valider_racine` a jugé.
  const autorisation = refus?.autorisation ?? null;
  const autorisable = autorisation !== null && autorisation.refus === null;

  /** Autorise le dossier refusé, puis l'ouvre dans le même geste (#1610). */
  const autoriser = useCallback(async () => {
    if (autorisation === null || autorisation.refus !== null) return;
    setAutorisationEnVol(true);
    setRefusAutorisation(null);
    try {
      const { chemin } = await autoriserDossier(autorisation.chemin);
      onAutorise?.(chemin);
      await ouvrir(chemin);
    } catch (erreur) {
      // La confirmation reste ouverte avec ce qui a empêché l'écriture : rien
      // n'est autorisé, et on peut réessayer ou renoncer.
      setRefusAutorisation(refusDepuis(erreur));
    } finally {
      setAutorisationEnVol(false);
    }
  }, [autorisation, onAutorise, ouvrir]);

  const renoncer = useCallback(() => {
    rendreLeFocus.current = true;
    setConfirmation(false);
    setRefusAutorisation(null);
  }, []);

  // Le focus suit le geste : il entre dans la confirmation qui s'ouvre, et
  // revient au bouton qui l'a ouverte quand on y renonce — le bouton disparaît
  // pendant la confirmation, le focus ne doit pas tomber sur le document.
  useEffect(() => {
    if (confirmation) {
      zoneConfirmation.current?.focus();
    } else if (rendreLeFocus.current) {
      rendreLeFocus.current = false;
      rangeeRefus.current?.querySelector("button")?.focus();
    }
  }, [confirmation]);

  useEffect(() => {
    // Chargement différé d'un tick, comme partout dans le shell : l'effet
    // lui-même ne déclenche aucun setState synchrone.
    const tick = setTimeout(() => void ouvrir(cheminInitial), 0);
    return () => clearTimeout(tick);
  }, [ouvrir, cheminInitial]);

  // Ce que la relecture d'après un retrait rouvre : la page affichée, et le
  // refus qu'elle porte.
  const vue = useRef<{ chemin: string | null; refus: RefusProjet | null }>({
    chemin: cheminInitial,
    refus: null,
  });
  useEffect(() => {
    vue.current = { chemin: page === null ? cheminInitial : page.chemin, refus };
  }, [page, refus, cheminInitial]);

  /**
   * Relit la page affichée après un retrait fait ailleurs (#1610).
   *
   * Le retrait a pu la sortir des dossiers explorables — c'était le dossier
   * retiré, ou l'un de ses sous-dossiers. La garder à l'écran sous son refus
   * montrerait le contenu d'un dossier qu'on dit ne plus explorer : on revient
   * aux dossiers explorables, le refus en tête, qui nomme le dossier et propose
   * de l'autoriser de nouveau. Tout autre échec garde la page, comme une
   * navigation qui échoue.
   */
  const relire = useCallback(async () => {
    const { chemin, refus: refusAffiche } = vue.current;
    try {
      const relue = await chargerExplorateur(chemin);
      setPage(relue);
      setRefus(refusAffiche);
    } catch (erreur) {
      const refusRelu = refusDepuis(erreur);
      if (chemin !== null && refusRelu.motif === "hors-racines-explorables") {
        await ouvrir(null, refusRelu);
      } else {
        setRefus(refusRelu);
      }
    }
  }, [ouvrir]);

  // La valeur reçue au montage n'est pas un retrait : l'ouverture initiale
  // s'en charge déjà.
  const actualisationVue = useRef(actualisation);
  useEffect(() => {
    if (actualisation === actualisationVue.current) return;
    actualisationVue.current = actualisation;
    const tick = setTimeout(() => void relire(), 0);
    return () => clearTimeout(tick);
  }, [actualisation, relire]);

  useEffect(() => {
    // L'état du sélecteur natif est demandé une fois, à l'arrivée : il dépend
    // du poste et du backend, pas du dossier ouvert. Son échec n'est pas une
    // panne de l'explorateur — on retombe simplement sur « pas de bouton ».
    //
    // Sauf quand le poste ouvre le dialogue lui-même (#938) : la question ne se
    // pose alors plus, et la poser rendrait un motif qu'on n'afficherait pas.
    if (posteChoisit) return;
    let vivant = true;
    const tick = setTimeout(() => {
      void chargerDisponibiliteSelecteur()
        .then((etat) => vivant && setSelecteur(etat))
        .catch(() => vivant && setSelecteur(null));
    }, 0);
    return () => {
      vivant = false;
      clearTimeout(tick);
    };
  }, [posteChoisit]);

  const courant = page?.chemin ?? null;
  const dossiers = page?.dossiers ?? [];
  // Une page lue, et c'est l'entrée : pas une page qui n'a jamais chargé, d'où
  // revenir aux dossiers explorables reste la sortie.
  const surLesExplorables = page !== null && page.chemin === null;

  /**
   * Le dialogue natif, et ce qu'on fait de son verdict. Trois issues, toutes
   * sans cul-de-sac : annulé, on ne touche à rien ; déclarable, on le choisit ;
   * lisible mais non déclarable (une racine de disque, le dossier utilisateur
   * nu), on **ouvre l'explorateur dessus** avec le motif affiché — de quoi
   * descendre d'un cran plutôt que de recommencer.
   */
  const parcourirNatif = useCallback(async () => {
    setOuvertureNative(true);
    try {
      const choix = posteChoisit
        ? await choisirDepuisLaFenetre(courant)
        : await ouvrirSelecteurNatif(courant);
      if (choix.annule || choix.chemin === null) return;
      if (choix.racine_valide) {
        onChoisir(choix.chemin);
        return;
      }
      await ouvrir(choix.chemin, choix.refus);
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
    } finally {
      setOuvertureNative(false);
    }
  }, [courant, onChoisir, ouvrir, posteChoisit]);

  /** Le chemin saisi, ouvert — par le bouton « Aller » comme par `Entrée`. */
  const allerAuChemin = useCallback(() => {
    const chemin = saisie.trim();
    if (chemin) void ouvrir(chemin);
  }, [saisie, ouvrir]);

  return (
    <section
      aria-label="Explorateur de dossiers"
      className="rounded-md border border-neutral-200 bg-neutral-50 p-3 dark:border-neutral-800 dark:bg-neutral-950"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="min-w-0 text-xs text-neutral-500 dark:text-neutral-400">
          Dossier courant :{" "}
          <code className="font-mono break-all text-neutral-800 dark:text-neutral-200">
            {courant ?? "dossiers explorables"}
          </code>
        </p>
        {/* Sans `shrink-0` : à la largeur d'un téléphone, les boutons passent
            à la ligne au lieu de pousser « Fermer » hors de la carte. */}
        <div className="flex flex-wrap gap-2">
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            icone={IconeFlecheHaut}
            onClick={() => void ouvrir(page?.parent ?? null)}
            disabled={chargement || courant === null}
            // Même raison qu'au bouton « Choisir » plus bas (#536) : sur un
            // bouton désactivé, le `title` n'atteignait personne.
            aria-label={
              page?.parent === null && courant !== null
                ? "Dossiers explorables — remonter sortirait des dossiers explorables"
                : undefined
            }
          >
            {page?.parent === null && courant !== null
              ? "Dossiers explorables"
              : "Remonter"}
          </Bouton>
          {/* Un seul bouton plein à la fois, sur le dossier qu'on traite : tant
              qu'un refus offre d'autoriser le dossier saisi, c'est ce geste qui
              est plein, et celui-ci, qui vise le dossier courant, passe en
              contour. */}
          <Bouton
            variante={autorisable ? "contour" : "plein"}
            ton={autorisable ? "neutre" : "accent"}
            taille="petite"
            onClick={() => courant !== null && onChoisir(courant)}
            disabled={chargement || courant === null}
          >
            Choisir ce dossier
          </Bouton>
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            onClick={onFermer}
          >
            Fermer
          </Bouton>
        </div>
      </div>

      {/* Les deux raccourcis vers un dossier lointain (#278). Le dialogue natif
          est un confort — il n'apparaît que là où il peut s'ouvrir —, la saisie
          d'un chemin est le repli qui marche partout, y compris en mode
          serveur : c'est l'API qui la vérifie, jamais le navigateur.

          ⚠ « Là où il peut s'ouvrir » a deux réponses depuis #938, et une seule
          d'entre elles demande l'avis du backend : dans la fenêtre, le dialogue
          est celui du poste et il est **toujours** ouvrable. */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {(posteChoisit || selecteur?.disponible) && (
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            occupe={ouvertureNative}
            disabled={chargement}
            onClick={() => void parcourirNatif()}
          >
            {ouvertureNative
              ? "Fenêtre ouverte sur votre poste…"
              : "Parcourir sur mon poste…"}
          </Bouton>
        )}
        {/* Un `<div>`, et la soumission tenue à la main : voir l'en-tête du
            fichier — un `<form>` ici serait imbriqué dans celui du formulaire
            de projet (#312). Sa largeur minimale le fait passer sous « Parcourir
            sur mon poste… » quand la ligne est étroite, au lieu d'écraser le
            champ jusqu'à ne plus lire le chemin tapé. */}
        <div className="flex min-w-48 flex-1 items-center gap-2">
          <label className="sr-only" htmlFor="explorateur-chemin">
            Aller à un chemin absolu
          </label>
          <input
            id="explorateur-chemin"
            type="text"
            value={saisie}
            onChange={(evenement) => setSaisie(evenement.target.value)}
            onKeyDown={(evenement) => {
              if (evenement.key !== "Enter") return;
              // Coupé **même sur une saisie vide** : sans ce `preventDefault`,
              // la soumission implicite du navigateur remonterait au
              // formulaire porteur et déclarerait le projet.
              evenement.preventDefault();
              allerAuChemin();
            }}
            placeholder="Aller à un chemin absolu (ex. D:/depots)"
            className="min-w-0 flex-1 rounded-md border border-neutral-300 bg-white px-2 py-1 font-mono text-xs text-neutral-800 dark:border-neutral-700 dark:bg-neutral-900 dark:text-neutral-200"
          />
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            onClick={allerAuChemin}
            disabled={chargement || saisie.trim() === ""}
          >
            Aller
          </Bouton>
        </div>
      </div>

      {/* Le mode serveur (et tout autre empêchement) se **dit**, à la place du
          bouton : un bouton mort ferait croire à une panne, un silence ferait
          croire que la fonction n'existe pas. Dans la fenêtre il n'y a rien à
          dire, et `selecteur` y reste `null` : la disponibilité n'est même pas
          demandée (voir l'effet plus haut). */}
      {selecteur && !selecteur.disponible && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          {selecteur.message}
        </p>
      )}

      {refus && (
        <div className="mt-3">
          {/* Hors frontière, mais pas déclarable (partage réseau, racine de
              disque, dossier absent) : aucun geste, et la raison dite dans le
              bandeau, à la place de la promesse « s'autorise d'ici » — un
              bouton qui échouerait ferait croire à une panne, une ligne sous le
              bandeau se lirait après l'offre. */}
          <RefusMotive
            refus={refus}
            titre="Dossier non exploré"
            conseil={
              autorisation !== null && autorisation.refus !== null
                ? phraseIndeclarable(autorisation.refus)
                : undefined
            }
          />
          {/* La confirmation, en place et sur la surface du contenu : le refus
              garde seul la teinte d'alerte, la question ne se lit pas comme
              une seconde alerte. Un `div` aux classes de `Carte` et non une
              `Carte` : il porte le `ref` qui reçoit le focus. */}
          {autorisable && confirmation && (
            <div
              ref={zoneConfirmation}
              role="group"
              aria-labelledby={titreConfirmation}
              tabIndex={-1}
              className={classesCarte({
                densite: "compacte",
                className:
                  "mt-2 flex flex-col gap-2 text-annexe text-texte outline-none",
              })}
            >
              <p
                id={titreConfirmation}
                className="font-medium text-texte"
              >
                Autoriser ce dossier sur ce poste ?
              </p>
              <code className="block font-mono wrap-anywhere text-texte">
                {autorisation.chemin}
              </code>
              <p>
                Maestro pourra parcourir ce dossier et ses sous-dossiers. Les
                zones protégées — clés, dossiers système, dépôt de Maestro —
                restent refusées. L&apos;autorisation se retire dans Paramètres ›
                Projets.
              </p>
              {/* L'échec de l'écriture, pas un jugement sur le dossier : une
                  API injoignable prend le ton de panne des autres écrans. */}
              {refusAutorisation && (
                <RefusMotive
                  refus={refusAutorisation}
                  titre="L'autorisation n'a pas abouti"
                  ton={
                    refusAutorisation.motif === "api-injoignable"
                      ? "alerte"
                      : "attention"
                  }
                />
              )}
              <div className="flex flex-wrap gap-2">
                <Bouton
                  taille="petite"
                  occupe={autorisationEnVol}
                  onClick={() => void autoriser()}
                >
                  Autoriser ce dossier
                </Bouton>
                <Bouton
                  variante="contour"
                  ton="neutre"
                  taille="petite"
                  disabled={autorisationEnVol}
                  onClick={renoncer}
                >
                  Ne pas autoriser
                </Bouton>
              </div>
            </div>
          )}
          <div ref={rangeeRefus} className="mt-2 flex flex-wrap gap-2">
            {/* Plein et nommé : c'est l'action de l'explorateur tant que le
                refus est là (« Choisir ce dossier » passe en contour), et il
                dit quel dossier il vise — ce n'est pas le dossier courant.
                Le nom vient du disque, donc de n'importe quelle longueur :
                à 390 px, un nom de 50 caractères sortirait du cadre.
                Borné à la colonne, le libellé passe à la ligne ; le chemin
                entier est en infobulle, et la confirmation le redit. */}
            {autorisable && !confirmation && (
              <Bouton
                taille="petite"
                title={autorisation.chemin}
                className="max-w-full text-left wrap-anywhere"
                onClick={() => setConfirmation(true)}
              >
                Autoriser {nomDuDossier(autorisation.chemin)}…
              </Bouton>
            )}
            {/* Sur la page des dossiers explorables, il n'y a nulle part où
                revenir : le refus y arrive d'une saisie, ou d'un retrait qui a
                sorti la page qu'on regardait. */}
            {!surLesExplorables && (
              <Bouton
                variante="contour"
                ton="neutre"
                taille="petite"
                disabled={autorisationEnVol}
                onClick={() => void ouvrir(null)}
              >
                Revenir aux dossiers explorables
              </Bouton>
            )}
          </div>
        </div>
      )}

      <ul className="mt-3 flex max-h-72 flex-col gap-1 overflow-y-auto">
        {dossiers.map((dossier) => (
          <li
            key={dossier.chemin}
            className="flex items-center justify-between gap-2 rounded-md px-1 hover:bg-white dark:hover:bg-neutral-900"
          >
            {/* Deux gestes distincts sur la même ligne — entrer dans le
                dossier, ou le prendre pour racine —, donc deux noms
                accessibles explicites : sans eux, les pastilles feraient du
                nom du bouton d'ouverture un « depensio dépôt Git » que rien ne
                distingue du « Choisir depensio » voisin. */}
            <button
              type="button"
              onClick={() => void ouvrir(dossier.chemin)}
              disabled={chargement}
              aria-label={`Ouvrir ${dossier.nom}`}
              // `flex-wrap` : à la largeur d'un téléphone, les pastilles
              // passent sous le nom au lieu de déborder sur « Choisir ».
              className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 py-1.5 text-left text-corps disabled:opacity-50"
            >
              <IconeDossier className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
              <span className="truncate">{dossier.nom}</span>
              {dossier.origine !== null && (
                <span className="shrink-0 rounded-full border border-neutral-300 px-1.5 text-[10px] font-medium text-neutral-500 dark:border-neutral-700 dark:text-neutral-400">
                  {LIBELLE_ORIGINE[dossier.origine]}
                </span>
              )}
              {dossier.depot_git && (
                <BadgeEtat ton="info" contour className="shrink-0 text-micro">
                  dépôt Git
                </BadgeEtat>
              )}
              {dossier.projet_id !== null && (
                <BadgeEtat contour className="shrink-0 text-micro">
                  déjà déclaré
                </BadgeEtat>
              )}
            </button>
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              onClick={() => onChoisir(dossier.chemin)}
              disabled={chargement || dossier.projet_id !== null}
              // Le motif du blocage est passé du `title` au nom accessible
              // (#536) : sur un bouton `disabled`, plusieurs navigateurs
              // n'affichent aucun `title` — il n'était donc lisible ni à la
              // souris, ni au clavier.
              aria-label={
                dossier.projet_id !== null
                  ? `Choisir ${dossier.nom} — indisponible : ce dossier est déjà la racine d'un projet`
                  : `Choisir ${dossier.nom}`
              }
              className="shrink-0"
            >
              Choisir
            </Bouton>
          </li>
        ))}
      </ul>

      {/* Les trois états de la liste sont distincts, à dessein : rien ici, pas
          encore lu, ou refus (au-dessus). Les afficher pareil ferait passer une
          frontière pour un dossier vide. */}
      {!chargement && refus === null && dossiers.length === 0 && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Aucun sous-dossier ici — « Choisir ce dossier » reste possible.
        </p>
      )}
      {chargement && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Lecture du dossier…
        </p>
      )}
      {page?.tronque && (
        <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
          Liste coupée : ce dossier contient plus de 500 sous-dossiers.
        </p>
      )}
    </section>
  );
}
