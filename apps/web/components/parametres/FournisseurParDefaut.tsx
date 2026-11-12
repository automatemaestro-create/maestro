"use client";

/**
 * Le fournisseur par défaut du poste (#1609) : le choisir, l'éprouver, l'enregistrer.
 *
 * La question à laquelle la carte répond d'un coup d'œil : *quel fournisseur et
 * quel modèle Maestro utilise-t-il sur ce poste, et l'accès marche-t-il ?* D'où
 * trois lectures qui ne se confondent jamais (variante B, consignée sur #1609) :
 *
 * - **ce qui est utilisé maintenant** — l'effectif, lu sur l'API, qui diffère du
 *   choix quand une variable de l'environnement l'impose ; la carte qui le porte
 *   le dit en toutes lettres, la carte cochée n'est que la saisie en cours ;
 * - **ce que la sonde a vu** — présent sur ce poste, ou rien de détecté : un
 *   constat, jamais un vert, parce que présent n'est pas éprouvé ;
 * - **ce que l'épreuve a établi** — le seul verdict, rendu dans la carte choisie,
 *   avec son motif et le geste qui le répare.
 *
 * Les accès viennent du registre (`GET /api/fournisseurs`, `acces` de chaque
 * fournisseur) : aucune liste n'est écrite ici. Une clé tapée part à l'épreuve
 * puis au coffre du poste ; elle n'est jamais réaffichée, et le champ se vide
 * dès qu'elle est enregistrée.
 *
 * La même carte est l'**étape du modèle** du premier lancement (#642, `enEtape`) :
 * l'étape réutilise l'écran du produit, elle ne le recopie pas (d'après AnythingLLM).
 * Montée dans une étape, elle perd son cadre, son titre et sa tête : l'étape porte
 * déjà le titre, et son résumé dit l'état, une fois. Rien ne le redit dans les
 * cartes d'accès non plus (#1617) : ni le badge « Utilisé maintenant », ni la phrase
 * qui précède toute épreuve. `onEtat` lui dit chaque état lu ou enregistré, pour
 * qu'elle se coche sur le fait.
 *
 * À largeur de téléphone, l'étape ne montre que la carte de l'accès choisi
 * (`autresReplies`, #1629) : les autres attendent derrière un repli qui dit leur
 * compte, et l'accès enregistré quand il en fait partie. En cocher un en fait la
 * carte ouverte, et le focus le suit. Les Paramètres montrent toujours toutes les
 * cartes.
 *
 * Un geste que l'API n'a pas reçu se dit à l'endroit du geste, en mots (#1636) :
 * une épreuve qui n'a pas pu partir rend un échec d'épreuve sous « Éprouver », et
 * un enregistrement ou un retrait sans réponse le dit sous son bouton. La panne se
 * reconnaît à sa classe, `ErreurApi.injoignable`, jamais au texte du navigateur.
 */

import { type ReactNode, useEffect, useMemo, useRef, useState } from "react";

import { IconeAlerte, IconeCoche, IconeStatutAFaire } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte, Champ, ChampListe } from "@/components/Primitives";
import { RefusMotive, refusDepuis } from "@/components/projets/ExplorateurDossiers";
import {
  chargerFournisseurDefaut,
  chargerFournisseurs,
  effacerFournisseurDefaut,
  enregistrerFournisseurDefaut,
  eprouverFournisseurDefaut,
  refusEnMots,
  sansReponse,
} from "@/lib/api";
import {
  type OptionAcces,
  adresseSaisie,
  cleEffective,
  cleEnregistree,
  constatsDe,
  empreinteDemande,
  optionsAcces,
  phraseDuVerdict,
} from "@/lib/fournisseurParDefaut";
import type {
  CatalogueFournisseurs,
  ConstatPoste,
  DemandeFournisseurDefaut,
  Epreuve,
  EtatFournisseurDefaut,
  RefusProjet,
} from "@/lib/types";

/** Le verdict d'une épreuve, et la saisie qu'il a éprouvée. */
type Verdict = { empreinte: string; epreuve: Epreuve; modele: string };

/** L'action en vol — une à la fois, les boutons se taisent pendant qu'elle dure. */
type Action = "epreuve" | "enregistrement" | "retrait";

const MESSAGE_ENREGISTRE =
  "Enregistré. Le prochain appel de modèle s'en sert, sans redémarrer Maestro.";

// Ce que chaque geste dit quand l'API de Maestro n'a pas répondu : ce qu'il en
// coûte, puis le geste qui y répond. Ce n'est pas « Rien ne répond à cette
// adresse », qui parle du fournisseur.
const EPREUVE_SANS_API =
  "L'API n'a pas répondu : l'épreuve n'a pas pu se faire. Vérifiez que maestro-api tourne, puis éprouvez de nouveau.";
const ENREGISTREMENT_SANS_API =
  "L'API n'a pas répondu : rien n'a été enregistré. Vérifiez que maestro-api tourne, puis réessayez.";
const RETRAIT_SANS_API =
  "L'API n'a pas répondu : le choix enregistré reste en place. Vérifiez que maestro-api tourne, puis réessayez.";

export function FournisseurParDefaut({
  enEtape = false,
  autresReplies = false,
  onEtat,
}: {
  /** Montée dans l'étape du premier lancement : sans cadre, sans titre ni tête. */
  enEtape?: boolean;
  /**
   * Seule la carte de l'accès choisi se montre, les autres derrière un repli qui
   * en dit le compte : l'étape du modèle à largeur de téléphone (#1629).
   */
  autresReplies?: boolean;
  /** Chaque état lu ou enregistré — ce sur quoi l'étape du modèle se coche. */
  onEtat?: (etat: EtatFournisseurDefaut) => void;
} = {}) {
  const [autresOuverts, setAutresOuverts] = useState(false);
  // L'accès coché dans le repli : sa carte est remontée en tête, et le focus,
  // perdu avec la radio démontée, va à la sienne.
  const choisiDansLeRepli = useRef<string | null>(null);
  const [catalogue, setCatalogue] = useState<CatalogueFournisseurs | null>(null);
  const [etat, setEtat] = useState<EtatFournisseurDefaut | null>(null);
  const [panne, setPanne] = useState<RefusProjet | null>(null);
  const [selection, setSelection] = useState<string | null>(null);
  const [modele, setModele] = useState("");
  const [adresse, setAdresse] = useState("");
  const [cle, setCle] = useState("");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [annonce, setAnnonce] = useState<string | null>(null);
  const [refus, setRefus] = useState<string | null>(null);
  // L'empreinte de la saisie dont l'épreuve n'a pas pu partir : comme un verdict,
  // elle cesse de valoir dès que la saisie change.
  const [epreuveSansApi, setEpreuveSansApi] = useState<string | null>(null);
  const [refusRetrait, setRefusRetrait] = useState<string | null>(null);

  // Chargement différé d'un tick (même mécanique que la section) : l'effet
  // lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          const [lu, poste] = await Promise.all([
            chargerFournisseurs(),
            chargerFournisseurDefaut(),
          ]);
          setCatalogue(lu);
          setEtat(poste);
          const depart = cleEnregistree(poste) ?? cleEffective(poste);
          const option = optionsAcces(lu).find((o) => o.cle === depart);
          if (option !== undefined) {
            setSelection(option.cle);
            if (poste.choix !== null && cleEnregistree(poste) === option.cle) {
              setModele(poste.choix.modele ?? "");
              setAdresse(adresseSaisie(poste.choix.adresse, option.acces));
            }
          }
          setPanne(null);
        } catch (e) {
          setPanne(refusDepuis(e));
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, []);

  useEffect(() => {
    if (etat !== null) onEtat?.(etat);
  }, [etat, onEtat]);

  useEffect(() => {
    if (choisiDansLeRepli.current === null || choisiDansLeRepli.current !== selection) return;
    choisiDansLeRepli.current = null;
    document.querySelector<HTMLInputElement>(`input[name="defaut-acces"][value="${selection}"]`)?.focus();
  }, [selection]);

  const options = useMemo(
    () => (catalogue === null ? [] : optionsAcces(catalogue)),
    [catalogue],
  );

  if (panne !== null) {
    // Paramètres n'a pas de bandeau qui dirait la panne plus haut : la carte la
    // dit elle-même, dans la forme de « Répertoire illisible » de la même page.
    return (
      <div className="mb-4">
        <RefusMotive refus={panne} titre="Fournisseur par défaut illisible" />
      </div>
    );
  }
  if (catalogue === null || etat === null) {
    return (
      <p className="mb-4 text-corps text-texte-secondaire">
        Lecture du fournisseur par défaut…
      </p>
    );
  }

  const effective = cleEffective(etat);
  const enregistree = cleEnregistree(etat);
  const optionEffective = options.find((o) => o.cle === effective);
  const choisie = options.find((o) => o.cle === selection) ?? null;

  const demande: DemandeFournisseurDefaut | null =
    choisie === null
      ? null
      : {
          fournisseur: choisie.fournisseur.nom,
          acces: choisie.acces.id,
          modele: modele.trim() || null,
          adresse: choisie.acces.adresse === null ? null : adresse.trim() || null,
          cle: choisie.acces.secret === null ? null : cle || null,
        };
  const empreinte = demande === null ? null : empreinteDemande(demande);
  const verdictCourant =
    verdict !== null && verdict.empreinte === empreinte ? verdict : null;
  const sansApiCourante = epreuveSansApi !== null && epreuveSansApi === empreinte;
  // Le verdict de la saisie parle de ce qui est en service quand la saisie est
  // celle-là : même accès, même modèle interrogé, même adresse, aucune clé neuve.
  const modeleEnService = etat.effectif.modele ?? etat.effectif.modele_par_defaut ?? "";
  const adresseEnService =
    enregistree === effective && optionEffective !== undefined
      ? adresseSaisie(etat.choix?.adresse, optionEffective.acces)
      : "";
  const epreuveEnService =
    verdictCourant !== null &&
    selection === effective &&
    choisie !== null &&
    cle === "" &&
    adresseSaisie(adresse, choisie.acces) === adresseEnService &&
    verdictCourant.modele === modeleEnService
      ? verdictCourant.epreuve
      : null;
  // La saisie est-elle le réglage enregistré ? Il a été éprouvé pour l'être, et
  // la carte ne le redit pas « pas encore éprouvé ».
  const saisieEnregistree =
    choisie !== null &&
    etat.choix !== null &&
    choisie.cle === enregistree &&
    modele.trim() === (etat.choix.modele ?? "") &&
    adresseSaisie(adresse, choisie.acces) === adresseSaisie(etat.choix.adresse, choisie.acces) &&
    cle === "";

  const choisir = (option: OptionAcces) => {
    setSelection(option.cle);
    const memeQueLeChoix = enregistree === option.cle && etat.choix !== null;
    setModele(memeQueLeChoix ? (etat.choix?.modele ?? "") : "");
    setAdresse(memeQueLeChoix ? adresseSaisie(etat.choix?.adresse, option.acces) : "");
    setCle("");
    setVerdict(null);
    setEpreuveSansApi(null);
    setAnnonce(null);
    setRefus(null);
  };

  const eprouver = async () => {
    if (demande === null || empreinte === null) return;
    setAction("epreuve");
    setAnnonce(null);
    setRefus(null);
    setEpreuveSansApi(null);
    try {
      const resultat = await eprouverFournisseurDefaut(demande);
      setVerdict({ empreinte, epreuve: resultat.epreuve, modele: resultat.modele });
    } catch (e) {
      if (sansReponse(e)) {
        // L'épreuve n'a pas eu lieu : elle se dit à la place du verdict, et un
        // verdict d'avant ne laisse pas « Enregistrer » ouvert sur une API muette.
        setVerdict(null);
        setEpreuveSansApi(empreinte);
      } else {
        setRefus(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setAction(null);
    }
  };

  const enregistrer = async () => {
    if (demande === null || empreinte === null) return;
    setAction("enregistrement");
    setRefus(null);
    try {
      const resultat = await enregistrerFournisseurDefaut(demande);
      if ("refus" in resultat) {
        setVerdict({
          empreinte,
          epreuve: resultat.refus,
          modele: verdictCourant?.modele ?? demande.modele ?? "",
        });
      } else {
        setEtat(resultat.etat);
        // La saisie reprend ce qui a été enregistré, comme à la réouverture de
        // l'écran ; une adresse laissée vide le reste.
        setModele(resultat.etat.choix?.modele ?? "");
        setAdresse(
          choisie === null ? "" : adresseSaisie(resultat.etat.choix?.adresse, choisie.acces),
        );
        // La clé est au coffre : le champ se vide, et le verdict tient pour la
        // saisie sans clé — c'est celle du coffre qu'on éprouverait désormais.
        setCle("");
        setVerdict(null);
        setAnnonce(MESSAGE_ENREGISTRE);
      }
    } catch (e) {
      setRefus(refusEnMots(e, ENREGISTREMENT_SANS_API));
    } finally {
      setAction(null);
    }
  };

  const oublier = async () => {
    setAction("retrait");
    setRefus(null);
    setRefusRetrait(null);
    try {
      setEtat(await effacerFournisseurDefaut());
      setAnnonce("Le choix est oublié : Maestro reprend son réglage d'origine.");
    } catch (e) {
      setRefusRetrait(refusEnMots(e, RETRAIT_SANS_API));
    } finally {
      setAction(null);
    }
  };

  // Un accès au moins à replier, et une carte choisie à montrer à sa place : sans
  // elle, tout replier ne laisserait rien à choisir à l'écran.
  const replie = autresReplies && choisie !== null && options.length > 1;
  const repliees = replie ? options.filter((o) => o.cle !== choisie.cle) : [];

  const carte = (option: OptionAcces, dansLeRepli = false) => (
    <CarteAcces
      key={option.cle}
      option={option}
      etat={etat}
      active={option.cle === selection}
      utilisee={option.cle === effective}
      enregistree={option.cle === enregistree}
      enEtape={enEtape}
      onChoisir={() => {
        if (dansLeRepli) {
          choisiDansLeRepli.current = option.cle;
          setAutresOuverts(false);
        }
        choisir(option);
      }}
    >
      {option.cle === selection && (
        <Saisie
          option={option}
          etat={etat}
          modele={modele}
          adresse={adresse}
          cle={cle}
          onModele={setModele}
          onAdresse={setAdresse}
          onCle={setCle}
          verdict={verdictCourant}
          sansApi={sansApiCourante}
          saisieEnregistree={saisieEnregistree}
          enEtape={enEtape}
          action={action}
          annonce={annonce}
          refus={refus}
          onEprouver={() => void eprouver()}
          onEnregistrer={() => void enregistrer()}
        />
      )}
    </CarteAcces>
  );

  const contenu = (
    <>
      {!enEtape && (
        <>
          <h3 id="defaut-titre" className="text-titre font-semibold">
            Par défaut
          </h3>
          <p className="mt-1 text-corps text-texte-secondaire">
            Le fournisseur et le modèle de tout ce qui n&apos;en choisit pas un : les
            runs, le fil et l&apos;assistant.
          </p>
          <Effectif
            etat={etat}
            option={optionEffective}
            epreuve={epreuveEnService}
            carteCochee={selection === effective}
          />
        </>
      )}
      <Imposition etat={etat} />

      {/* `min-w-0` : un fieldset a pour largeur minimale son contenu (`min-inline-size:
          min-content`) ; sans lui, à largeur de téléphone, une adresse insécable pousse
          les cartes hors de l'écran. */}
      <fieldset className="mt-3 flex min-w-0 flex-col gap-2">
        <legend className="sr-only">Accès au fournisseur par défaut</legend>
        {replie ? (
          <>
            {carte(choisie)}
            {/* Le repli natif du fil (`chat/EtapesDuFil`) : un second niveau qui dit
                son compte, jamais un retrait. Ouvert, il rend les cartes telles
                quelles, constat et badges compris. */}
            <details
              open={autresOuverts}
              onToggle={(e) => setAutresOuverts(e.currentTarget.open)}
              className="min-w-0"
            >
              <summary className="min-h-6 cursor-pointer text-corps font-medium text-texte">
                Autres accès · {repliees.length}
                {repliees.some((o) => o.cle === enregistree) && (
                  <span className="font-normal text-texte-secondaire">
                    , dont l&apos;accès enregistré
                  </span>
                )}
              </summary>
              <div className="mt-2 flex min-w-0 flex-col gap-2">
                {repliees.map((option) => carte(option, true))}
              </div>
            </details>
          </>
        ) : (
          options.map((option) => carte(option))
        )}
      </fieldset>

      {etat.choix !== null && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Bouton
              variante="discret"
              onClick={() => void oublier()}
              occupe={action === "retrait"}
              disabled={action !== null && action !== "retrait"}
            >
              Oublier le choix enregistré
            </Bouton>
            <span className="text-annexe text-texte-secondaire">
              Maestro reprend alors son réglage d&apos;origine.
            </span>
          </div>
          {/* Le refus d'un retrait se dit sous son bouton, pas dans la carte choisie. */}
          <div aria-live="polite" className="min-w-0 wrap-anywhere">
            {refusRetrait !== null && <Refus texte={refusRetrait} />}
          </div>
        </>
      )}
    </>
  );

  // Dans l'étape, le premier bloc ne garde pas la marge qui le séparait de la tête.
  return enEtape ? (
    <div className="[&>:first-child]:mt-0">{contenu}</div>
  ) : (
    <Carte balise="section" aria-labelledby="defaut-titre" className="mb-4">
      {contenu}
    </Carte>
  );
}

/**
 * La tête de la carte, qui répond à sa question : ce que l'exécution utilise
 * maintenant, si cet accès a été éprouvé, et d'où vient ce réglage.
 */
function Effectif({
  etat,
  option,
  epreuve,
  carteCochee,
}: {
  etat: EtatFournisseurDefaut;
  option: OptionAcces | undefined;
  epreuve: Epreuve | null;
  carteCochee: boolean;
}) {
  const { effectif, choix } = etat;
  const identifiant = effectif.modele ?? effectif.modele_par_defaut;
  const choisiIci = choix !== null && cleEnregistree(etat) === cleEffective(etat);
  return (
    <div className="mt-3 flex flex-col gap-1">
      <p className="text-corps">
        <span className="text-texte-secondaire">Utilisé maintenant : </span>
        <span className="font-medium">{option?.acces.libelle ?? effectif.fournisseur}</span>
        <span className="text-texte-secondaire"> · </span>
        {identifiant === null ? (
          <span>aucun modèle réglé</span>
        ) : (
          <>
            {/* « modèle » et son identifiant passent à la ligne ensemble, et un
                identifiant ne se coupe pas à ses tirets. */}
            <span className="whitespace-nowrap">
              <span className="text-texte-secondaire">modèle </span>
              <span className="font-mono">{identifiant}</span>
            </span>
            {effectif.modele === null && (
              <span className="text-texte-secondaire">, retenu d&apos;office</span>
            )}
          </>
        )}
      </p>
      <EtatEnService
        epreuve={epreuve}
        eprouveLe={choisiIci ? choix.eprouve_le : null}
        geste={
          option === undefined
            ? null
            : // Espaces insécables dans les guillemets : un « ne finit pas une ligne seul.
              carteCochee
              ? "« Éprouver », dans sa carte ci-dessous"
              : "cochez sa carte ci-dessous, puis « Éprouver »"
        }
      />
      <p className="text-annexe text-texte-secondaire">
        {choix === null
          ? "Rien n'est choisi ici : c'est le réglage d'origine de Maestro."
          : choisiIci
            ? `Choisi ici le ${dateLisible(choix.pose_le)}.`
            : "Imposé par l'environnement du poste, qui l'emporte sur le choix enregistré ici."}
      </p>
    </div>
  );
}

/**
 * L'accès en service a-t-il été éprouvé ? Une épreuve de cette session l'emporte
 * sur celle de l'enregistrement, qui est datée : un accès qui répondait peut avoir
 * cessé, et la date le laisse lire. L'état porte une forme, pas seulement une couleur.
 */
function EtatEnService({
  epreuve,
  eprouveLe,
  geste,
}: {
  epreuve: Epreuve | null;
  eprouveLe: string | null;
  geste: string | null;
}) {
  const [Icone, ton, phrase]: [typeof IconeCoche, string, string] =
    epreuve !== null
      ? epreuve.ok
        ? [IconeCoche, "text-positif-texte", "Accès éprouvé à l'instant."]
        : [
            IconeAlerte,
            "text-alerte-texte",
            "L'épreuve de cet accès vient d'échouer : son motif est dans sa carte, ci-dessous.",
          ]
      : eprouveLe !== null
        ? [IconeCoche, "text-positif-texte", `Accès éprouvé le ${dateLisible(eprouveLe)}.`]
        : [
            IconeStatutAFaire,
            "text-texte-secondaire",
            geste === null ? "Accès pas encore éprouvé." : `Accès pas encore éprouvé : ${geste}.`,
          ];
  return (
    <p className="flex gap-1.5 text-corps">
      <Icone aria-hidden="true" className={`mt-0.5 size-4 shrink-0 ${ton}`} />
      <span className="min-w-0">{phrase}</span>
    </p>
  );
}

/**
 * Ce que l'environnement impose, et le geste qui rend la main à l'écran.
 * Une variable posée dans le `.env` du poste l'emporte sur le choix : le taire
 * ferait croire qu'un enregistrement a été ignoré.
 */
function Imposition({ etat }: { etat: EtatFournisseurDefaut }) {
  if (etat.environnement.length === 0) return null;
  return (
    <Carte balise="div" ton="attention" densite="normale" role="note" className="mt-3">
      <p className="flex gap-2 text-corps text-attention-texte">
        <IconeAlerte aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
        <span className="min-w-0 wrap-anywhere">
          L&apos;environnement du poste impose{" "}
          {etat.environnement.map((variable, rang) => (
            <span key={variable.variable}>
              {rang > 0 && ", "}
              <code className="font-mono">
                {variable.valeur === null
                  ? `${variable.variable} (une clé)`
                  : `${variable.variable}=${variable.valeur}`}
              </code>
            </span>
          ))}{" "}
          : {etat.environnement.length > 1 ? "elles l'emportent" : "elle l'emporte"} sur
          le choix fait ici. Pour rendre la main à cet écran, retirez-
          {etat.environnement.length > 1 ? "les" : "la"} du fichier{" "}
          <code className="font-mono">.env</code> de Maestro, puis relancez-le.
        </span>
      </p>
    </Carte>
  );
}

/** Une carte d'accès : le choisir, et ce qu'on en sait avant de l'éprouver. */
function CarteAcces({
  option,
  etat,
  active,
  utilisee,
  enregistree,
  enEtape,
  onChoisir,
  children,
}: {
  option: OptionAcces;
  etat: EtatFournisseurDefaut;
  active: boolean;
  utilisee: boolean;
  enregistree: boolean;
  /** Dans l'étape du modèle, le résumé de l'étape dit déjà ce qui est utilisé. */
  enEtape: boolean;
  onChoisir: () => void;
  children: ReactNode;
}) {
  const constats = constatsDe(option);
  const cleGardee = option.acces.secret !== null && etat.cles.includes(option.acces.secret);
  const vu = constats.length > 0 || cleGardee;
  const id = `defaut-${option.fournisseur.nom}-${option.acces.id}`;
  return (
    // La carte choisie se distingue sans le vert : le vert dirait « ça marche »,
    // et seule l'épreuve le dit.
    <div
      className={`rounded-carte border p-3 ${
        active ? "border-bord-fort bg-surface-creuse" : "border-bord"
      }`}
    >
      <div className="flex items-start gap-3">
        <input
          id={id}
          type="radio"
          name="defaut-acces"
          value={option.cle}
          checked={active}
          onChange={onChoisir}
          className="mt-1 size-4 shrink-0 accent-texte"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {/* Au pas du corps : le titre de la carte, « Par défaut », reste au-dessus
                de ses options. Un accès que le poste n'offre pas perd la graisse en plus
                de la couleur — en clair, la couleur seule s'y lisait à peine. */}
            <label
              htmlFor={id}
              className={`min-h-6 text-corps ${
                vu || active ? "font-medium" : "font-normal text-texte-secondaire"
              }`}
            >
              {option.acces.libelle}
            </label>
            {utilisee && !enEtape && (
              <BadgeEtat ton="info" pastille>
                Utilisé maintenant
              </BadgeEtat>
            )}
            {enregistree && !utilisee && (
              <BadgeEtat ton="neutre" contour>
                Enregistré, pas utilisé
              </BadgeEtat>
            )}
            {/* La limite se dit toujours ; sur un accès que le poste n'offre pas, elle
                s'atténue comme son titre, sans quoi elle ferait ressortir la carte. */}
            {!option.fournisseur.outille && (
              <BadgeEtat
                ton={vu || active || utilisee ? "attention" : "neutre"}
                contour
                className={vu || active || utilisee ? "" : "font-normal text-texte-secondaire"}
              >
                Conversation seulement
              </BadgeEtat>
            )}
          </div>
          <p className="mt-0.5 text-annexe wrap-anywhere text-texte-secondaire">
            {ceQueLaSondeAVu(option, constats, cleGardee)}
          </p>
          {/* Le sous-titre de la carte dit que le défaut sert aussi les runs : ce qu'un
              run fait d'un fournisseur sans outils se dit ici, sans quoi l'écran se
              contredirait. */}
          {!option.fournisseur.outille && (active || utilisee) && (
            <p className="mt-0.5 text-annexe text-texte-secondaire">
              Il répond dans le fil et l&apos;assistant. Dans un run, ses agents
              rendent du texte : sans outils, ils ne lisent ni n&apos;écrivent les
              fichiers du projet.
            </p>
          )}
        </div>
      </div>
      {children}
    </div>
  );
}

/**
 * La ligne de constat d'une carte : ce que la sonde a vu, dans les mots de la
 * personne, sans jamais conclure. Elle se compose des champs typés du constat
 * (genre, libellé, modèles vus) : son `detail` sert la fiche d'un agent et parle
 * le vocabulaire du code — variables, fournisseur, SDK.
 */
function ceQueLaSondeAVu(
  option: OptionAcces,
  constats: ConstatPoste[],
  cleGardee: boolean,
): string {
  const parties = constats.map((constat) => constatEnMots(constat, option));
  if (cleGardee) parties.push("Une clé est enregistrée sur ce poste");
  if (parties.length === 0) {
    return option.acces.secret !== null
      ? "Aucune clé enregistrée sur ce poste."
      : "Rien de détecté sur ce poste.";
  }
  return `${parties.join(". ")}.`;
}

/**
 * Un constat en une proposition. Un programme trouvé ne dit pas qu'il est
 * connecté — la sonde ne lance aucun binaire —, et la phrase le dit plutôt que
 * de le laisser supposer.
 */
function constatEnMots(constat: ConstatPoste, option: OptionAcces): string {
  const { libelle, modeles, utilisable } = constat;
  switch (constat.genre) {
    case "cli":
      return utilisable
        ? `${libelle} est installé sur ce poste ; sa connexion à un compte se vérifie en l'éprouvant`
        : `${libelle} est installé sur ce poste, mais Maestro ne s'en sert pas`;
    case "serveur_local":
      if (!utilisable) return `${libelle} : la sonde ne l'a pas joint`;
      if (modeles.length === 0) return `${libelle} répond sur ce poste, sans nommer de modèle`;
      return `${libelle} répond sur ce poste, avec ${modeles.length} ${
        modeles.length > 1 ? "modèles" : "modèle"
      } : ${modeles.join(", ")}`;
    case "cle":
      return option.acces.secret === null
        ? "L'environnement du poste fournit un jeton de connexion"
        : "L'environnement du poste fournit une clé";
    default:
      return libelle;
  }
}

/** Ce qu'on règle dans la carte choisie, l'épreuve, et son verdict. */
function Saisie({
  option,
  etat,
  modele,
  adresse,
  cle,
  onModele,
  onAdresse,
  onCle,
  verdict,
  sansApi,
  saisieEnregistree,
  action,
  annonce,
  refus,
  onEprouver,
  onEnregistrer,
  enEtape,
}: {
  option: OptionAcces;
  etat: EtatFournisseurDefaut;
  modele: string;
  adresse: string;
  cle: string;
  onModele: (valeur: string) => void;
  onAdresse: (valeur: string) => void;
  onCle: (valeur: string) => void;
  verdict: Verdict | null;
  /** L'épreuve de cette saisie n'a pas pu partir : l'API n'a pas répondu. */
  sansApi: boolean;
  saisieEnregistree: boolean;
  action: Action | null;
  annonce: string | null;
  refus: string | null;
  onEprouver: () => void;
  onEnregistrer: () => void;
  enEtape: boolean;
}) {
  const { fournisseur, acces } = option;
  const base = `defaut-${fournisseur.nom}-${acces.id}`;
  const gamme = fournisseur.modeles;
  const vusIci = [...new Set([...fournisseur.modeles_ici, ...constatsDe(option).flatMap((c) => c.modeles)])];
  const modeleRequis = gamme.length === 0;
  const cleDisponible =
    acces.secret !== null &&
    (etat.cles.includes(acces.secret) ||
      etat.environnement.some((v) => v.variable === acces.secret));
  const cleManquante = acces.cle_requise && cle === "" && !cleDisponible;
  const incomplet = (modeleRequis && modele.trim() === "") || cleManquante;
  const defautDuFournisseur =
    etat.effectif.fournisseur === fournisseur.nom ? etat.effectif.modele_par_defaut : null;

  return (
    <div className="mt-3 border-t border-bord pt-3 pl-7">
      <div className="grid gap-3 @xl:grid-cols-2">
        {modeleRequis ? (
          <>
            <Champ
              id={`${base}-modele`}
              libelle="Modèle"
              value={modele}
              onChange={(e) => onModele(e.target.value)}
              // Court : à largeur de téléphone, le champ n'en montre pas davantage.
              placeholder="Nom du modèle"
              list={vusIci.length > 0 ? `${base}-modeles-vus` : undefined}
              aide={
                vusIci.length > 0
                  ? `Vus sur ce poste : ${vusIci.join(", ")}.`
                  : "Ce fournisseur n'a pas de modèle par défaut : nommez celui qu'il sert."
              }
            />
            {vusIci.length > 0 && (
              <datalist id={`${base}-modeles-vus`}>
                {vusIci.map((nom) => (
                  <option key={nom} value={nom} />
                ))}
              </datalist>
            )}
          </>
        ) : (
          <ChampListe
            id={`${base}-modele`}
            libelle="Modèle"
            value={modele}
            onChange={(e) => onModele(e.target.value)}
            // L'identifiant retenu d'office vit sous le champ : dans l'option, un champ
            // à largeur de téléphone le tronquait.
            aide={
              defautDuFournisseur === null ? undefined : (
                <>
                  Sans préférence, Maestro interroge{" "}
                  <span className="font-mono whitespace-nowrap">{defautDuFournisseur}</span>.
                </>
              )
            }
          >
            <option value="">Sans préférence</option>
            {gamme.map((m) => (
              <option key={m.nom} value={m.nom}>
                {m.libelle === m.nom ? m.nom : `${m.libelle} — ${m.nom}`}
              </option>
            ))}
          </ChampListe>
        )}
        {acces.adresse !== null && (
          <Champ
            id={`${base}-adresse`}
            libelle="Adresse"
            value={adresse}
            onChange={(e) => onAdresse(e.target.value)}
            // L'adresse par défaut se lit en entier dans l'aide : à largeur de
            // téléphone, le champ n'en montrerait que le début, et une indication
            // « Par défaut » s'y lisait comme une adresse saisie.
            aide={
              acces.adresse_defaut === null
                ? undefined
                : `Laissée vide : ${acces.adresse_defaut}.`
            }
          />
        )}
        {acces.secret !== null && (
          <Champ
            id={`${base}-cle`}
            libelle="Clé"
            type="password"
            autoComplete="off"
            value={cle}
            onChange={(e) => onCle(e.target.value)}
            // Ce que garde un champ vide se dit dans l'aide, qui ne s'efface pas à la
            // saisie et tient à largeur de téléphone.
            placeholder={cleDisponible ? "Inchangée" : "Coller la clé"}
            aide={
              cleDisponible
                ? "Laissée vide, la clé enregistrée reste. Toute clé est gardée chiffrée sur ce poste, jamais réaffichée."
                : "Gardée chiffrée sur ce poste, jamais réaffichée."
            }
          />
        )}
      </div>

      {/* L'ordre de la variante retenue, à toute largeur : le geste, ce qu'il a rendu,
          puis l'enregistrement qu'il ouvre. */}
      <div className="mt-3 flex flex-col items-start gap-3">
        <Bouton
          variante="contour"
          onClick={onEprouver}
          occupe={action === "epreuve"}
          disabled={incomplet || (action !== null && action !== "epreuve")}
        >
          Éprouver
        </Bouton>
        {/* Vide, la région reste montée pour annoncer le verdict à venir : elle
            rend alors l'écart qu'elle prendrait entre les deux boutons. */}
        <div
          aria-live="polite"
          className="flex min-w-0 flex-col gap-1 self-stretch wrap-anywhere empty:-mt-3"
        >
          <VerdictEpreuve
            enEtape={enEtape}
            verdict={verdict}
            sansApi={sansApi}
            option={option}
            saisieEnregistree={saisieEnregistree}
            vientDEtreEnregistree={annonce !== null}
          />
        </div>
        <div className="min-w-0 self-stretch">
          <Bouton
            onClick={onEnregistrer}
            occupe={action === "enregistrement"}
            disabled={verdict?.epreuve.ok !== true || (action !== null && action !== "enregistrement")}
          >
            Enregistrer
          </Bouton>
          <div aria-live="polite" className="min-w-0 wrap-anywhere">
            {annonce !== null && <p className="mt-2 text-corps">{annonce}</p>}
            {refus !== null && <Refus texte={refus} />}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Un geste refusé : l'alerte porte une forme autant qu'une couleur. */
function Refus({ texte }: { texte: string }) {
  return (
    <p role="alert" className="mt-2 flex gap-1.5 text-corps text-alerte-texte">
      <IconeAlerte aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span className="min-w-0">{texte}</span>
    </p>
  );
}

/**
 * Le verdict : l'état porté par la **forme** autant que par la couleur — une
 * coche ou une alerte, et une phrase qui dit le motif et le geste. Une épreuve
 * que l'API n'a pas reçue se dit dans la même forme qu'un échec, y compris dans
 * l'étape du modèle, qui tait pourtant un verdict absent.
 */
function VerdictEpreuve({
  verdict,
  sansApi,
  option,
  saisieEnregistree,
  vientDEtreEnregistree,
  enEtape,
}: {
  verdict: Verdict | null;
  sansApi: boolean;
  option: OptionAcces;
  saisieEnregistree: boolean;
  /** L'annonce sous « Enregistrer » le dit déjà : la carte ne le redit pas. */
  vientDEtreEnregistree: boolean;
  /**
   * Dans l'étape du modèle, le résumé de l'étape dit déjà ce que l'épreuve
   * établit et ce qui est enregistré : seul un verdict rendu se dit ici.
   */
  enEtape: boolean;
}) {
  if (sansApi) {
    return <RenduDuVerdict ok={false} modele="" phrase={EPREUVE_SANS_API} precision="" />;
  }
  if (verdict === null && enEtape) return null;
  if (verdict === null && saisieEnregistree) {
    if (vientDEtreEnregistree) return null;
    return (
      <p className="text-annexe text-texte-secondaire">
        C&apos;est le réglage enregistré : «&nbsp;Éprouver&nbsp;» le vérifie de nouveau.
      </p>
    );
  }
  if (verdict === null) {
    return (
      <p className="text-annexe text-texte-secondaire">
        Ce réglage n&apos;est pas encore éprouvé : l&apos;épreuve fait un tour de modèle
        avec lui, avant tout enregistrement.
      </p>
    );
  }
  const { epreuve } = verdict;
  return (
    <RenduDuVerdict
      ok={epreuve.ok}
      modele={verdict.modele}
      phrase={phraseDuVerdict(epreuve, option.acces)}
      precision={epreuve.ok ? "" : epreuve.precision}
    />
  );
}

/** Le badge, le modèle interrogé, la phrase, et le relevé d'un échec quand il y en a un. */
function RenduDuVerdict({
  ok,
  modele,
  phrase,
  precision,
}: {
  ok: boolean;
  modele: string;
  phrase: string;
  precision: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        {ok ? (
          <BadgeEtat ton="positif" icone={IconeCoche}>
            Accès éprouvé
          </BadgeEtat>
        ) : (
          <BadgeEtat ton="alerte" icone={IconeAlerte}>
            Échec de l&apos;épreuve
          </BadgeEtat>
        )}
        {modele !== "" && (
          <span className="font-mono text-annexe text-texte-secondaire">{modele}</span>
        )}
      </div>
      <p className="text-corps">{phrase}</p>
      {precision !== "" && (
        // Un fait court tiré d'un champ typé (statut, code, adresse) : il se lit comme
        // un relevé, en chasse fixe, pas comme une phrase.
        <p className="text-annexe text-texte-secondaire">
          Relevé de l&apos;épreuve : <span className="font-mono">{precision}</span>
        </p>
      )}
    </div>
  );
}

/** Une date ISO, en clair et en français. */
function dateLisible(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" });
}
