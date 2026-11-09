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
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";

import { ContenuIndisponible } from "@/components/BanniereErreurApi";
import { IconeAlerte, IconeCoche } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte, Champ, ChampListe } from "@/components/Primitives";
import {
  chargerFournisseurDefaut,
  chargerFournisseurs,
  effacerFournisseurDefaut,
  enregistrerFournisseurDefaut,
  eprouverFournisseurDefaut,
} from "@/lib/api";
import {
  type OptionAcces,
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
} from "@/lib/types";

/** Le verdict d'une épreuve, et la saisie qu'il a éprouvée. */
type Verdict = { empreinte: string; epreuve: Epreuve; modele: string };

/** L'action en vol — une à la fois, les boutons se taisent pendant qu'elle dure. */
type Action = "epreuve" | "enregistrement" | "retrait";

const MESSAGE_ENREGISTRE =
  "Enregistré. Le prochain appel de modèle s'en sert, sans redémarrer Maestro.";

export function FournisseurParDefaut() {
  const [catalogue, setCatalogue] = useState<CatalogueFournisseurs | null>(null);
  const [etat, setEtat] = useState<EtatFournisseurDefaut | null>(null);
  const [panne, setPanne] = useState<string | null>(null);
  const [selection, setSelection] = useState<string | null>(null);
  const [modele, setModele] = useState("");
  const [adresse, setAdresse] = useState("");
  const [cle, setCle] = useState("");
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [action, setAction] = useState<Action | null>(null);
  const [annonce, setAnnonce] = useState<string | null>(null);
  const [refus, setRefus] = useState<string | null>(null);

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
              setAdresse(poste.choix.adresse ?? "");
            }
          }
          setPanne(null);
        } catch (e) {
          setPanne(e instanceof Error ? e.message : String(e));
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, []);

  const options = useMemo(
    () => (catalogue === null ? [] : optionsAcces(catalogue)),
    [catalogue],
  );

  if (panne !== null) {
    // La forme de tout écran en panne (#1217), celle du catalogue juste dessous :
    // la place est réservée, et le bandeau du shell reste le seul message de la panne.
    return (
      <div className="mb-4">
        <ContenuIndisponible quoi="le fournisseur par défaut du poste" />
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

  const choisir = (option: OptionAcces) => {
    setSelection(option.cle);
    const memeQueLeChoix = enregistree === option.cle && etat.choix !== null;
    setModele(memeQueLeChoix ? (etat.choix?.modele ?? "") : "");
    setAdresse(memeQueLeChoix ? (etat.choix?.adresse ?? "") : "");
    setCle("");
    setVerdict(null);
    setAnnonce(null);
    setRefus(null);
  };

  const eprouver = async () => {
    if (demande === null || empreinte === null) return;
    setAction("epreuve");
    setAnnonce(null);
    setRefus(null);
    try {
      const resultat = await eprouverFournisseurDefaut(demande);
      setVerdict({ empreinte, epreuve: resultat.epreuve, modele: resultat.modele });
    } catch (e) {
      setRefus(e instanceof Error ? e.message : String(e));
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
        // La clé est au coffre : le champ se vide, et le verdict tient pour la
        // saisie sans clé — c'est celle du coffre qu'on éprouverait désormais.
        setCle("");
        setVerdict(null);
        setAnnonce(MESSAGE_ENREGISTRE);
      }
    } catch (e) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setAction(null);
    }
  };

  const oublier = async () => {
    setAction("retrait");
    setRefus(null);
    try {
      setEtat(await effacerFournisseurDefaut());
      setAnnonce("Le choix est oublié : Maestro suit de nouveau son réglage par défaut.");
    } catch (e) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setAction(null);
    }
  };

  return (
    <Carte balise="section" aria-labelledby="defaut-titre" className="mb-4">
      <h3 id="defaut-titre" className="text-titre font-semibold">
        Par défaut
      </h3>
      <p className="mt-1 text-corps text-texte-secondaire">
        Le fournisseur et le modèle de tout ce qui n&apos;en choisit pas un : les
        runs, le fil et l&apos;assistant.
      </p>
      <Effectif etat={etat} option={optionEffective} />
      <Imposition etat={etat} />

      {/* `min-w-0` : un fieldset a pour largeur minimale son contenu (`min-inline-size:
          min-content`) ; sans lui, à largeur de téléphone, une adresse insécable pousse
          les cartes hors de l'écran. */}
      <fieldset className="mt-3 flex min-w-0 flex-col gap-2">
        <legend className="sr-only">Accès au fournisseur par défaut</legend>
        {options.map((option) => (
          <CarteAcces
            key={option.cle}
            option={option}
            etat={etat}
            active={option.cle === selection}
            utilisee={option.cle === effective}
            enregistree={option.cle === enregistree}
            onChoisir={() => choisir(option)}
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
                action={action}
                annonce={annonce}
                refus={refus}
                onEprouver={() => void eprouver()}
                onEnregistrer={() => void enregistrer()}
              />
            )}
          </CarteAcces>
        ))}
      </fieldset>

      {etat.choix !== null && (
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
            Maestro reprend alors son réglage par défaut.
          </span>
        </div>
      )}
    </Carte>
  );
}

/** La ligne de tête : ce que l'exécution utilise maintenant, et d'où ça vient. */
function Effectif({
  etat,
  option,
}: {
  etat: EtatFournisseurDefaut;
  option: OptionAcces | undefined;
}) {
  const { effectif, choix } = etat;
  const identifiant = effectif.modele ?? effectif.modele_par_defaut;
  return (
    <p className="mt-3 text-corps">
      <span className="text-texte-secondaire">Utilisé maintenant : </span>
      <span className="font-medium">{option?.acces.libelle ?? effectif.fournisseur}</span>
      <span className="text-texte-secondaire"> · modèle </span>
      {identifiant === null ? (
        <span>aucun modèle réglé</span>
      ) : (
        <>
          {/* Un identifiant ne se coupe pas à ses tirets : « claude-opus- / 5-5 »
              ne se lit plus comme un nom. */}
          <span className="font-mono whitespace-nowrap">{identifiant}</span>
          {effectif.modele === null && (
            <span className="text-texte-secondaire"> (par défaut)</span>
          )}
        </>
      )}
      <span className="block text-annexe text-texte-secondaire">
        {choix === null
          ? "Rien n'est choisi ici : Maestro suit son réglage par défaut."
          : `Choisi ici${choix.eprouve_le ? `, éprouvé le ${dateLisible(choix.eprouve_le)}` : ""}.`}
      </span>
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
  onChoisir,
  children,
}: {
  option: OptionAcces;
  etat: EtatFournisseurDefaut;
  active: boolean;
  utilisee: boolean;
  enregistree: boolean;
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
          className="mt-1 size-4 shrink-0 accent-accent"
        />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <label
              htmlFor={id}
              className={`min-h-6 font-medium ${vu || active ? "" : "text-texte-secondaire"}`}
            >
              {option.acces.libelle}
            </label>
            {utilisee && (
              <BadgeEtat ton="info" pastille>
                Utilisé maintenant
              </BadgeEtat>
            )}
            {enregistree && !utilisee && (
              <BadgeEtat ton="neutre" contour>
                Enregistré, pas utilisé
              </BadgeEtat>
            )}
            {!option.fournisseur.outille && (
              <BadgeEtat ton="attention" contour>
                Conversation seulement
              </BadgeEtat>
            )}
          </div>
          <p className="mt-0.5 text-annexe wrap-anywhere text-texte-secondaire">
            {ceQueLaSondeAVu(option, constats, cleGardee)}
          </p>
          {!option.fournisseur.outille && active && (
            <p className="mt-0.5 text-annexe text-texte-secondaire">
              Ce fournisseur répond dans le fil et l&apos;assistant, mais ne fait pas
              travailler un agent sur un projet.
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
  action,
  annonce,
  refus,
  onEprouver,
  onEnregistrer,
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
  action: Action | null;
  annonce: string | null;
  refus: string | null;
  onEprouver: () => void;
  onEnregistrer: () => void;
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
              placeholder="Le nom du modèle servi à cette adresse"
              list={vusIci.length > 0 ? `${base}-modeles-vus` : undefined}
              monospace
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
          >
            <option value="">
              {defautDuFournisseur === null ? "Par défaut" : `Par défaut (${defautDuFournisseur})`}
            </option>
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
            placeholder={acces.adresse_defaut ?? ""}
            monospace
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
            placeholder={cleDisponible ? "Laisser vide pour garder la clé actuelle" : "Coller la clé"}
            aide="Gardée chiffrée sur ce poste, jamais réaffichée."
          />
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <Bouton
          variante="contour"
          onClick={onEprouver}
          occupe={action === "epreuve"}
          disabled={incomplet || (action !== null && action !== "epreuve")}
        >
          Éprouver
        </Bouton>
        <Bouton
          onClick={onEnregistrer}
          occupe={action === "enregistrement"}
          disabled={verdict?.epreuve.ok !== true || (action !== null && action !== "enregistrement")}
          className="@xl:ml-auto"
        >
          Enregistrer
        </Bouton>
      </div>
      <div aria-live="polite" className="mt-3 flex min-w-0 flex-col gap-1 wrap-anywhere">
        <VerdictEpreuve verdict={verdict} option={option} />
        {annonce !== null && <p className="text-corps">{annonce}</p>}
        {refus !== null && (
          <p role="alert" className="text-corps text-alerte-texte">
            {refus}
          </p>
        )}
      </div>
    </div>
  );
}

/**
 * Le verdict : l'état porté par la **forme** autant que par la couleur — une
 * coche ou une alerte, et une phrase qui dit le motif et le geste.
 */
function VerdictEpreuve({
  verdict,
  option,
}: {
  verdict: Verdict | null;
  option: OptionAcces;
}) {
  if (verdict === null) {
    return (
      <p className="text-annexe text-texte-secondaire">
        Pas encore éprouvé : l&apos;épreuve fait un tour de modèle avec ce réglage,
        avant tout enregistrement.
      </p>
    );
  }
  const { epreuve } = verdict;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-center gap-2">
        {epreuve.ok ? (
          <BadgeEtat ton="positif" icone={IconeCoche}>
            Accès éprouvé
          </BadgeEtat>
        ) : (
          <BadgeEtat ton="alerte" icone={IconeAlerte}>
            Échec de l&apos;épreuve
          </BadgeEtat>
        )}
        {verdict.modele !== "" && (
          <span className="font-mono text-annexe text-texte-secondaire">{verdict.modele}</span>
        )}
      </div>
      <p className="text-corps">{phraseDuVerdict(epreuve, option.acces)}</p>
      {!epreuve.ok && epreuve.precision !== "" && (
        <p className="text-annexe text-texte-secondaire">Détail : {epreuve.precision}</p>
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
