"use client";

/**
 * Création et configuration d'un agent du catalogue (ticket #73, EF-03) :
 * le formulaire de définition — rôle, compétences, fournisseur/modèle/effort —
 * branché sur l'API du lot 1 (#72, `/api/catalogue`).
 *
 * Trois entrées, une par facette de la fiche agent (#190) : `CreationAgent`
 * (nouvel agent personnalisé, `POST` — sur **son propre écran** depuis #254,
 * `components/CreationAgentEcran`), `EditeurAgent` (onglet Profil — fiche
 * existante) et `McpEtPermissionsAgent` (onglet MCP & permissions). Un agent
 * créé ou modifié vaut pour les moteurs construits ensuite.
 *
 * Depuis #257 la création a **deux entrées** et non plus une : les champs qu'on
 * remplit, et une intention en une phrase que l'assistant transforme en
 * définition proposée (`AssistantDefinition`). La seconde n'ajoute aucun chemin
 * d'écriture — elle remplit les champs de la première, qui reste seule à créer.
 *
 * **Ce que #259 a déplacé, et pourquoi.** Le Profil portait un champ Playbook
 * alors que l'onglet Playbook existe depuis #190 : deux chemins d'écriture pour
 * la même valeur, dont un aveugle au versionnement et à l'historique. Le champ
 * n'existe donc plus qu'**à la création** — là où l'agent n'a pas encore
 * d'onglet où aller —, et partout ailleurs un `RenvoiPlaybook` prend sa place.
 *
 * Et le Profil d'un agent **du code** n'est plus en lecture seule : ses trois
 * réglages de modèle se **surchargent** (`FicheDefaut`), le reste de sa
 * définition continuant de venir du code. Le seul contournement d'avant était
 * de le dupliquer en agent personnalisé — c'est-à-dire d'en recopier le
 * playbook pour changer un modèle, après quoi la copie cesse de suivre le code.
 */

import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useId, useState } from "react";

import { ChampJetons } from "@/components/ChampJetons";
import {
  IconeAlerte,
  IconeAssistant,
  IconeMcp,
  IconePermissions,
  IconePlaybooks,
} from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { Bouton, EnTeteSection, classesCarte } from "@/components/Primitives";
import {
  CHEMIN_CREATION_AGENT,
  cheminOnglet,
  estNomAgentReserve,
} from "@/lib/agents";
import {
  annulerSurchargeAgent,
  chargerAgentCatalogue,
  chargerCatalogue,
  chargerFournisseurs,
  creerAgent,
  definirActivationsMcp,
  definirPermissions,
  genererDefinitionAgent,
  modifierAgent,
  supprimerAgent,
  surchargerAgent,
} from "@/lib/api";
import {
  competenceProche,
  inedites,
  normaliserCompetence,
  vocabulaireDuCatalogue,
} from "@/lib/competences";
import { formatDateHeure } from "@/lib/format";
import { entreesHorsPortee } from "@/lib/permissions";
import {
  AGENT_SOURCE_SURCHARGE,
  type AgentCatalogue,
  type AgentCatalogueDetail,
  type CatalogueFournisseurs,
  type DefinitionAgent,
  type DefinitionAgentProposee,
  estAgentDuCode,
  type FournisseurCatalogue,
  type IntegrationPoolMcp,
  type PolitiquePermissions,
  type ReglagesModele,
  type ServeurMcp,
} from "@/lib/types";

import { Interrupteur } from "./parametres/SectionParametres";

/** Miroir du slug accepté par le backend (`_NOM_AGENT`, maestro/agents/store.py). */
const SLUG_NOM = /^[a-z0-9][a-z0-9_-]*$/;

/**
 * Les champs du formulaire.
 *
 * `fournisseur`, `modele` et `effort` sont **liés** depuis #255 : le premier
 * borne ce que le deuxième offre, et le deuxième décide si le troisième existe.
 * La chaîne vide y vaut « rien de choisi », que `definitionDepuis` rend en
 * `null` — le **défaut légitime** d'un agent qui suit le fournisseur et le
 * modèle de l'exécution (`MAESTRO_PROVIDER`\`MAESTRO_MODEL`), et non un trou de
 * configuration : les listes l'offrent explicitement.
 *
 * Les `competences` sont une **liste** depuis #256, et plus la chaîne virgulée
 * d'avant : c'est la saisie qui change de forme, pas le contrat d'API
 * (`DefinitionAgent.competences` a toujours été une liste de chaînes — la
 * chaîne n'existait qu'ici, entre la frappe et l'envoi).
 */
type Champs = {
  role: string;
  competences: string[];
  playbook: string;
  modele: string;
  fournisseur: string;
  effort: string;
};

const CHAMPS_VIERGES: Champs = {
  role: "",
  competences: [],
  playbook: "",
  modele: "",
  fournisseur: "",
  effort: "",
};

function champsDepuis(fiche: AgentCatalogueDetail): Champs {
  return {
    role: fiche.role,
    competences: fiche.competences,
    playbook: fiche.playbook,
    modele: fiche.modele ?? "",
    fournisseur: fiche.fournisseur ?? "",
    effort: fiche.effort ?? "",
  };
}

/** Les champs tels que la génération assistée les propose (#257). */
function champsDepuisProposition(proposition: DefinitionAgentProposee): Champs {
  return {
    role: proposition.role,
    // La proposition rend déjà une liste, et le champ en est une depuis #256 :
    // elle se pose telle quelle, comme celle d'une fiche existante. La joindre en
    // chaîne virgulée n'aurait plus de lecteur — les jetons se corrigent un par un.
    competences: proposition.competences,
    playbook: proposition.playbook,
    // `null` — le modèle n'a rien proposé que le registre reconnaisse — devient
    // la chaîne vide, que les listes liées de #255 lisent déjà « défaut de
    // l'exécution ». C'est la même absence, pas une seconde.
    modele: proposition.modele ?? "",
    fournisseur: proposition.fournisseur ?? "",
    // L'effort n'est **pas** proposé (#257) : il ne se règle que sur les modèles
    // qui l'admettent, et le sélecteur de #255 n'apparaît qu'alors, sur son
    // défaut. Le faire suggérer par le modèle demanderait de lui passer la gamme
    // d'efforts par modèle pour qu'il rende, au mieux, ce défaut-là.
    effort: "",
  };
}

/** Le corps envoyé à l'API : champs épurés, optionnels vides rendus null. */
function definitionDepuis(champs: Champs): DefinitionAgent {
  return {
    role: champs.role.trim(),
    competences: champs.competences
      .map(normaliserCompetence)
      .filter((c) => c !== ""),
    playbook: champs.playbook,
    modele: champs.modele.trim() || null,
    fournisseur: champs.fournisseur.trim() || null,
    effort: champs.effort.trim() || null,
  };
}

/**
 * Les trois réglages de modèle seuls, épurés (#259) — ce qu'une surcharge porte.
 *
 * La chaîne vide y devient `null`, qui veut dire **hérité** et non « vide » :
 * c'est le même mot que `definitionDepuis` emploie pour « suit l'exécution »,
 * et côté agent du code il veut dire « suit le code ». Les trois à `null`, le
 * dépôt retire la surcharge — poser une surcharge qui ne surcharge rien et
 * l'annuler sont le même geste.
 */
function reglagesDepuis(champs: Champs): ReglagesModele {
  return {
    fournisseur: champs.fournisseur.trim() || null,
    modele: champs.modele.trim() || null,
    effort: champs.effort.trim() || null,
  };
}

/** Vrai si la définition passerait la validation du dépôt (miroir de `_valide`). */
function champsComplets(champs: Champs): boolean {
  const definition = definitionDepuis(champs);
  return (
    definition.role !== "" &&
    definition.playbook.trim() !== "" &&
    definition.competences.length > 0
  );
}

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm font-normal " +
  "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 " +
  "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-xs font-medium text-neutral-600 dark:text-neutral-400";

const CLASSE_ANNEXE = "text-xs text-neutral-500 dark:text-neutral-400";

/** Le libellé d'une option de fournisseur — les deux colonnes du catalogue (#487). */
const SUPPORTE_ICI = "supporté par Maestro · présent ici";
const SUPPORTE_AILLEURS = "supporté par Maestro · absent d'ici";

/**
 * Ce que le poste propose au formulaire (`GET /api/fournisseurs`, #487).
 *
 * **Best-effort par construction** : un échec ne remonte nulle part et ne
 * bloque rien. Les deux champs restent en saisie libre — c'est un choix, pas un
 * repli : `OpenAICompatProvider.supports` accepte tout nom non vide, et un
 * endpoint peut servir un modèle que personne n'a listé. La sonde **suggère**,
 * elle ne restreint pas. Un `<select>` ferait l'inverse et rendrait
 * insaisissable ce que le catalogue ignore.
 */
function useCataloguePoste(): CatalogueFournisseurs | null {
  const [catalogue, setCatalogue] = useState<CatalogueFournisseurs | null>(null);
  useEffect(() => {
    let vivant = true;
    void chargerFournisseurs()
      .then((recu) => {
        if (vivant) setCatalogue(recu);
      })
      .catch(() => {
        // Sans catalogue, le formulaire est celui d'avant #487 : deux champs
        // libres. Rien à signaler — l'utilisateur n'a rien demandé.
      });
    return () => {
      vivant = false;
    };
  }, []);
  return catalogue;
}

/**
 * Le catalogue d'agents, lu **une fois** pour ce formulaire (#255, #256).
 *
 * Deux champs en vivent, et c'est la raison de ce hook unique plutôt que d'un
 * par usage : le **rôle** y prend les rôles déjà portés (#255), les
 * **compétences** le vocabulaire déjà en usage (#256). Deux hooks, ce serait
 * deux `GET /api/catalogue` par montage pour une réponse identique.
 *
 * Best-effort, comme `useCataloguePoste` : sans catalogue, les deux champs
 * restent ce qu'ils étaient, en saisie libre. Ce sont des listes **alimentées**
 * et non fermées — un rôle comme une compétence inédits doivent rester
 * saisissables —, d'où des suggestions et jamais un `<select>`.
 *
 * ⚠ Il rend `null` et non `[]` tant que la lecture n'a pas abouti, et la nuance
 * porte tout le signalement de #256 : une liste vide dirait « le catalogue ne
 * connaît aucune compétence », donc *toutes* celles saisies seraient inédites,
 * donc l'écran alerterait sur chacune. `null` dit « on ne sait pas », et on se
 * tait (`inedites`).
 */
function useCatalogueAgents(): AgentCatalogue[] | null {
  const [fiches, setFiches] = useState<AgentCatalogue[] | null>(null);
  useEffect(() => {
    let vivant = true;
    void chargerCatalogue()
      .then((recu) => {
        if (vivant) setFiches(recu);
      })
      .catch(() => {
        // Sans catalogue, les deux champs sont ceux d'avant : saisie libre,
        // aucune suggestion, aucun signalement. Personne n'a rien demandé.
      });
    return () => {
      vivant = false;
    };
  }, []);
  return fiches;
}

/**
 * Les **rôles connus** — ceux que portent les agents du catalogue (#255).
 *
 * La source est `GET /api/catalogue` et rien d'autre : les rôles ne sont
 * déclarés nulle part ailleurs, et une liste écrite ici serait une seconde
 * définition qui dériverait au premier agent créé.
 */
function rolesConnus(fiches: AgentCatalogue[] | null): string[] {
  const vus: string[] = [];
  for (const fiche of fiches ?? []) {
    const role = fiche.role.trim();
    if (role !== "" && !vus.includes(role)) vus.push(role);
  }
  return vus;
}

/** Un modèle proposé par le champ, et d'où il vient (gamme annoncée ou poste). */
type OptionModele = { nom: string; libelle: string; ici: boolean };

/** La fiche du fournisseur choisi, ou `null` (aucun choix, ou hors registre). */
function fournisseurDe(
  catalogue: CatalogueFournisseurs | null,
  nom: string,
): FournisseurCatalogue | null {
  if (!catalogue || nom === "") return null;
  return catalogue.fournisseurs.find((f) => f.nom === nom) ?? null;
}

/**
 * Les modèles que le champ **offre**, pour le fournisseur choisi — critère 2 :
 * « le modèle n'offre que les siens ».
 *
 * Deux moitiés qui ne se confondent pas, comme partout dans ce catalogue : la
 * **gamme annoncée** par le registre (`modeles`, avec son libellé lisible) puis
 * ce que la **sonde a vu ici** pour ce fournisseur (`modeles_ici`) et que la
 * gamme ne nommait pas — un serveur local sert des modèles que personne n'a
 * listés.
 *
 * Sans fournisseur choisi, l'offre reste celle d'avant #255 : ce que le poste
 * sert, tous fournisseurs confondus. Ce n'est pas une exception à la règle mais
 * sa lecture exacte — « les siens » suppose un « il », et l'agent qui n'en
 * nomme aucun suit celui de l'exécution.
 */
function modelesOfferts(
  catalogue: CatalogueFournisseurs | null,
  nomFournisseur: string,
): OptionModele[] {
  if (!catalogue) return [];
  if (nomFournisseur === "") {
    const offerts: OptionModele[] = [];
    for (const fournisseur of catalogue.fournisseurs) {
      for (const modele of fournisseur.modeles_ici) {
        if (!offerts.some((o) => o.nom === modele)) {
          offerts.push({ nom: modele, libelle: modele, ici: true });
        }
      }
    }
    return offerts;
  }
  const fournisseur = fournisseurDe(catalogue, nomFournisseur);
  if (!fournisseur) return [];
  const offerts: OptionModele[] = fournisseur.modeles.map((modele) => ({
    nom: modele.nom,
    libelle: modele.libelle || modele.nom,
    ici: fournisseur.modeles_ici.includes(modele.nom),
  }));
  for (const modele of fournisseur.modeles_ici) {
    if (!offerts.some((o) => o.nom === modele)) {
      offerts.push({ nom: modele, libelle: modele, ici: true });
    }
  }
  return offerts;
}

/**
 * Les efforts admis **sur ce modèle** — miroir exact de
 * `ModelProvider.efforts_admis` : vide pour un modèle **hors gamme**, parce
 * qu'on ne sait rien de ce qu'il accepte et que supposer serait le seul moyen
 * d'envoyer un réglage qu'un endpoint refuserait.
 *
 * Vide aussi tant qu'aucun fournisseur n'est choisi : l'effort se règle sur le
 * modèle d'un fournisseur nommé, et c'est l'exécution qui tranchera pour un
 * agent qui suit ses défauts.
 */
function effortsDe(
  catalogue: CatalogueFournisseurs | null,
  nomFournisseur: string,
  nomModele: string,
): string[] {
  const fournisseur = fournisseurDe(catalogue, nomFournisseur);
  if (!fournisseur || nomModele === "") return [];
  return fournisseur.modeles.find((m) => m.nom === nomModele)?.efforts ?? [];
}

/** L'effort gardé s'il reste admis, sinon vide — jamais un réglage impossible. */
function effortRetenu(
  catalogue: CatalogueFournisseurs | null,
  nomFournisseur: string,
  nomModele: string,
  effort: string,
): string {
  // Catalogue pas encore arrivé : on ne juge pas, on garde. Vider ici
  // effacerait le réglage d'une fiche au premier rendu, avant toute question.
  if (effort === "" || !catalogue) return effort;
  return effortsDe(catalogue, nomFournisseur, nomModele).includes(effort)
    ? effort
    : "";
}

/**
 * Ce que la sonde a trouvé, en une ligne — et ce qu'elle ne peut pas savoir.
 *
 * Les trois états du catalogue sont **nommés séparément** parce qu'ils appellent
 * trois gestes différents : ce qui est armé ici se choisit tel quel, ce qui est
 * là sans être supporté ne se choisit pas du tout, et « rien trouvé » n'est pas
 * « rien installé » — d'où les incertitudes rendues telles quelles, jamais
 * résumées en « détection partielle ».
 */
function ResumeDuPoste({
  catalogue,
  id,
}: {
  catalogue: CatalogueFournisseurs | null;
  id: string;
}) {
  if (!catalogue) return null;
  const armes = catalogue.fournisseurs.filter((f) => f.utilisable_ici);
  const horsRegistre = catalogue.hors_registre;
  return (
    <div id={id} className={"flex flex-col gap-1 " + CLASSE_ANNEXE}>
      <p>
        <span className="font-medium">Sur ce poste : </span>
        {armes.length > 0
          ? armes
              .map(
                (f) =>
                  `${f.nom} (${f.constats.map((c) => c.libelle).join(", ")})`,
              )
              .join(" · ")
          : "aucun fournisseur armé n’a été détecté."}
      </p>
      {horsRegistre.length > 0 && (
        <p>
          <span className="font-medium">Présent, non supporté : </span>
          {horsRegistre.map((c) => `${c.libelle} — ${c.detail}`).join(" · ")}
        </p>
      )}
      {catalogue.incertitudes.map((incertitude) => (
        <p key={incertitude} className="italic">
          {incertitude}
        </p>
      ))}
    </div>
  );
}

/** Au-delà, le signalement se compte au lieu de se dérouler ligne à ligne. */
const INEDITES_NOMMEES = 5;

/**
 * Ce que le formulaire dit d'une compétence que le catalogue ne connaît pas
 * (#256) — et ce qu'il n'en dit pas.
 *
 * Il la **signale**, il ne la refuse pas : la valeur part telle quelle, le
 * bouton d'enregistrement ne bouge pas. Deux raisons, et la seconde est celle
 * que les notes du ticket demandaient d'aller vérifier dans le routeur — les
 * deux signaux n'ont pas la même tolérance (`lib/competences.ts`) : la règle de
 * recouvrement apparie au mot près, le classifieur lit la même compétence en
 * texte et peut la rapprocher. Une compétence inédite n'est donc pas perdue,
 * elle est seulement privée du signal déterministe. Et un vocabulaire ne
 * s'enrichit que si quelqu'un a le droit d'y ajouter un mot.
 *
 * Le voisin le plus proche est **nommé** quand il y en a un, parce que c'est là
 * qu'est le vrai coût : « React » et « react » sont le même mot pour qui le lit
 * et deux compétences étrangères pour une intersection d'ensembles.
 */
function SignalementInedites({
  inconnues,
  vocabulaire,
  remplacer,
  desactive,
}: {
  inconnues: string[];
  vocabulaire: string[];
  remplacer: (ancienne: string, nouvelle: string) => void;
  desactive: boolean;
}) {
  const nommees = inconnues.slice(0, INEDITES_NOMMEES);
  const enPlus = inconnues.length - nommees.length;
  return (
    <>
      {nommees.map((jeton) => {
        const proche = competenceProche(jeton, vocabulaire);
        return (
          <p key={jeton} className="flex flex-wrap items-center gap-x-1 gap-y-1">
            <IconeAlerte aria-hidden="true" className="size-3.5 shrink-0" />
            <span>
              « {jeton} » est inédite : aucun agent du catalogue ne la déclare.
              {proche !== null
                ? ` Le catalogue connaît « ${proche} » — au mot près, ce ne sont pas la même.`
                : " Elle n'appariera que les tâches qui l'écrivent à l'identique."}
            </span>
            {proche !== null && (
              <Bouton
                variante="contour"
                ton="attention"
                taille="petite"
                disabled={desactive}
                onClick={() => remplacer(jeton, proche)}
              >
                Reprendre « {proche} »
              </Bouton>
            )}
          </p>
        );
      })}
      {enPlus > 0 && (
        <p>
          … et {enPlus} autre{enPlus > 1 ? "s" : ""} que le catalogue ne connaît
          pas encore.
        </p>
      )}
    </>
  );
}

/**
 * Les trois réglages de modèle — **fournisseur → modèle → effort** —, liés
 * depuis #255 : chaque maillon borne le suivant, si bien qu'on ne peut plus
 * composer une configuration qui n'existe pas.
 *
 * Extrait de `FormulaireDefinition` par #259, qui en a un **second** appelant :
 * la fiche d'un agent du code, dont ces trois réglages se surchargent alors que
 * son rôle, ses compétences et son playbook restent au code. Les recopier
 * aurait donné deux chaînes à tenir d'accord, et #255 venait précisément de
 * n'en faire qu'une.
 *
 * ⚠ Le fournisseur est passé du champ libre de #487 à un `<select>`, et c'est
 * un renversement assumé plutôt qu'un oubli : l'argument de #487 — « la sonde
 * suggère, elle ne restreint pas » — portait sur le **modèle**
 * (`OpenAICompatProvider.supports` accepte tout nom non vide, un endpoint sert
 * ce qu'il veut), jamais sur le fournisseur, dont le **registre est
 * exhaustif** : un nom qui n'y est pas ne s'exécute pas, donc le laisser saisir
 * n'offrait que la faute de frappe. Le modèle, lui, garde sa saisie libre
 * **quand le fournisseur l'admet** (`modeles_libres`) — les deux champs du
 * contrat se lisent ensemble, et c'est ce qui décide de la forme du champ.
 *
 * Les **compétences**, elles, sont passées de la chaîne virgulée au champ à
 * jetons (#256) : elles ne bornent rien et ne sont bornées par rien — c'est un
 * vocabulaire ouvert, pas une chaîne de dépendances —, d'où des suggestions et
 * un signalement plutôt qu'une liste fermée.
 */
function ChampsDuModele({
  champs,
  setChamps,
  desactive,
  marqueur,
  enTete,
}: {
  champs: Champs;
  setChamps: (champs: Champs) => void;
  desactive: boolean;
  /**
   * Ce qui s'affiche sous chaque réglage — d'où il vient (#259). Rend `null`
   * là où il n'y a rien à dire : sur un agent personnalisé, aucun des trois ne
   * tient de qui que ce soit.
   */
  marqueur?: (reglage: "fournisseur" | "modele" | "effort") => ReactNode;
  /** Ce qui précède les trois champs dans la même grille (rôle, compétences…). */
  enTete?: ReactNode;
}) {
  const catalogue = useCataloguePoste();
  /**
   * Ce qu'un changement de fournisseur a **retiré**, en toutes lettres. Vider un
   * champ sans le dire serait la moitié muette du critère 2 : c'est le
   * « visiblement » qui distingue l'invalidation du sabotage silencieux.
   *
   * On retient **ce qui a été vidé** et pas seulement la phrase, pour que
   * l'annonce cesse d'elle-même dès que ce n'est plus vrai — « Annuler les
   * modifications » restaure les champs sans passer par ce composant, et un
   * message qui survivrait à la restauration dirait le contraire de l'écran.
   */
  const [invalidation, setInvalidation] = useState<{
    texte: string;
    modele: string;
    effort: string;
  } | null>(null);
  const invalidationVisible =
    invalidation !== null &&
    (invalidation.modele === "" || champs.modele === "") &&
    (invalidation.effort === "" || champs.effort === "");
  // Deux formulaires peuvent cohabiter sur la page (création + fiche) : des
  // identifiants dérivés, jamais écrits en dur — deux `<datalist>` de même id
  // rendraient les suggestions de l'un dans l'autre.
  const prefixe = useId();
  const idModeles = `${prefixe}-modeles`;
  const idPoste = `${prefixe}-poste`;

  const fournisseur = fournisseurDe(catalogue, champs.fournisseur);
  const modeles = modelesOfferts(catalogue, champs.fournisseur);
  const efforts = effortsDe(catalogue, champs.fournisseur, champs.modele);
  // Gamme **fermée** : le champ devient un `<select>`, rien d'autre n'étant
  // recevable. Gamme libre (le cas des deux fournisseurs d'aujourd'hui) : une
  // liste qui propose sans interdire.
  const modeleFerme = fournisseur !== null && !fournisseur.modeles_libres;
  // Une valeur stockée que le registre ne connaît plus reste **représentable** :
  // sans cette option, ouvrir la fiche d'un agent réécrirait sa définition en
  // silence au premier enregistrement — une perte de données déguisée en menu.
  const fournisseurInconnu =
    catalogue !== null && champs.fournisseur !== "" && fournisseur === null;

  /**
   * Changer de fournisseur, et **invalider ce qui devient impossible** (critère 2).
   *
   * Un modèle que le nouveau fournisseur n'offre pas est retiré, jamais laissé
   * en place : `claude-opus-5` conservé après un passage à `openai` est
   * exactement « une configuration qui n'existe pas ». L'effort suit son
   * modèle, faute de quoi il resterait un réglage orphelin que l'exécution
   * écarterait sans rien dire (`ModelProvider.effort_admis`).
   */
  const changerFournisseur = (nom: string) => {
    const suivant: Champs = { ...champs, fournisseur: nom };
    const retires: string[] = [];
    let modeleRetire = "";
    let effortRetire = "";
    // Tant que le catalogue n'est pas là, on ne juge rien : on ne sait pas
    // encore ce que ce fournisseur offre. Et « aucun fournisseur » n'invalide
    // aucun modèle — c'est l'exécution qui tranchera.
    if (catalogue !== null && nom !== "" && champs.modele !== "") {
      if (!modelesOfferts(catalogue, nom).some((m) => m.nom === champs.modele)) {
        modeleRetire = champs.modele;
        retires.push(`le modèle « ${champs.modele} »`);
        suivant.modele = "";
      }
    }
    suivant.effort = effortRetenu(catalogue, nom, suivant.modele, champs.effort);
    if (suivant.effort !== champs.effort) {
      effortRetire = champs.effort;
      retires.push(`l’effort « ${champs.effort} »`);
    }
    setInvalidation(
      retires.length > 0
        ? {
            texte:
              `${retires.join(" et ")} — ${
                nom === "" ? "sans objet ici" : `impossible chez « ${nom} »`
              }. ` +
              (fournisseurDe(catalogue, nom)?.modeles_libres
                ? "Un autre nom reste saisissable."
                : "Choisissez-en un dans la liste."),
            modele: modeleRetire,
            effort: effortRetire,
          }
        : null,
    );
    setChamps(suivant);
  };

  /** Changer de modèle : l'effort ne survit que si le nouveau modèle l'admet. */
  const changerModele = (nom: string) => {
    setInvalidation(null);
    setChamps({
      ...champs,
      modele: nom,
      effort: effortRetenu(catalogue, champs.fournisseur, nom, champs.effort),
    });
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="grid gap-3 sm:grid-cols-2">
        {enTete}
        <label className={CLASSE_LIBELLE}>
          Fournisseur
          <select
            value={champs.fournisseur}
            onChange={(e) => changerFournisseur(e.target.value)}
            disabled={desactive}
            aria-describedby={catalogue ? idPoste : undefined}
            className={CLASSE_CHAMP + " font-mono"}
          >
            {/* Le défaut légitime, offert explicitement (note du ticket) : un
                agent sans fournisseur propre suit celui de l'exécution. */}
            <option value="">— défaut de l’exécution</option>
            {/* Seuls les fournisseurs du **registre** sont proposés : un outil
                trouvé ici que Maestro ne sait pas piloter est montré par
                `ResumeDuPoste`, jamais suggéré — le proposer serait le seul vrai
                mensonge possible de cet écran. */}
            {(catalogue?.fournisseurs ?? []).map((f) => (
              <option key={f.nom} value={f.nom}>
                {f.nom} — {f.present_ici ? SUPPORTE_ICI : SUPPORTE_AILLEURS}
              </option>
            ))}
            {fournisseurInconnu && (
              <option value={champs.fournisseur}>
                {champs.fournisseur} — inconnu du registre
              </option>
            )}
          </select>
          {marqueur?.("fournisseur")}
        </label>
        <label className={CLASSE_LIBELLE}>
          Modèle
          {modeleFerme ? (
            <select
              value={champs.modele}
              onChange={(e) => changerModele(e.target.value)}
              disabled={desactive}
              aria-describedby={catalogue ? idPoste : undefined}
              className={CLASSE_CHAMP + " font-mono"}
            >
              <option value="">— défaut de l’exécution</option>
              {modeles.map((modele) => (
                <option key={modele.nom} value={modele.nom}>
                  {modele.libelle}
                  {modele.ici ? " — servi ici" : ""}
                </option>
              ))}
              {champs.modele !== "" &&
                !modeles.some((m) => m.nom === champs.modele) && (
                  <option value={champs.modele}>
                    {champs.modele} — hors gamme
                  </option>
                )}
            </select>
          ) : (
            <>
              <input
                type="text"
                value={champs.modele}
                onChange={(e) => changerModele(e.target.value)}
                disabled={desactive}
                placeholder="claude-sonnet-5"
                list={modeles.length > 0 ? idModeles : undefined}
                aria-describedby={catalogue ? idPoste : undefined}
                className={CLASSE_CHAMP + " font-mono"}
              />
              {modeles.length > 0 && (
                <datalist id={idModeles}>
                  {modeles.map((modele) => (
                    <option
                      key={modele.nom}
                      value={modele.nom}
                      label={modele.ici ? "servi ici" : modele.libelle}
                    />
                  ))}
                </datalist>
              )}
            </>
          )}
          {marqueur?.("modele")}
        </label>
        {/* Le sélecteur d'effort n'existe que si le modèle en admet (critère 3) :
            un modèle hors gamme n'annonce rien, donc le champ disparaît plutôt
            que d'offrir un réglage que l'exécution écarterait. */}
        {efforts.length > 0 && (
          <label className={CLASSE_LIBELLE}>
            Effort
            <select
              value={champs.effort}
              onChange={(e) =>
                setChamps({ ...champs, effort: e.target.value })
              }
              disabled={desactive}
              className={CLASSE_CHAMP + " font-mono"}
            >
              {/* La valeur par défaut du sélecteur : aucun réglage, donc le
                  régime par défaut du fournisseur — ce que `effort: null` veut
                  dire de bout en bout. */}
              <option value="">— défaut du fournisseur</option>
              {efforts.map((effort) => (
                <option key={effort} value={effort}>
                  {effort}
                </option>
              ))}
            </select>
            {marqueur?.("effort")}
          </label>
        )}
      </div>
      {invalidationVisible && invalidation && (
        <p
          role="status"
          className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950 dark:text-amber-300"
        >
          {invalidation.texte}
        </p>
      )}
      <ResumeDuPoste catalogue={catalogue} id={idPoste} />
    </div>
  );
}

/**
 * Les champs communs de la définition d'un agent **personnalisé** : son
 * identité (rôle, compétences), ses trois réglages de modèle, et son playbook.
 *
 * ⚠ Le playbook n'est éditable qu'**à la création** depuis #259, et c'est le
 * critère 1 : partout ailleurs il s'écrit sur l'onglet Playbook, qui le
 * versionne et en garde l'historique (#190) — deux chemins d'écriture pour la
 * même valeur, dont un aveugle aux versions, étaient une occasion permanente
 * d'écraser sans le savoir. `agent` porte ce partage : absent, l'agent n'existe
 * pas encore, il n'a donc pas d'onglet où aller et le champ est le seul endroit
 * possible ; présent, le champ cède la place au **renvoi** vers cet onglet.
 */
function FormulaireDefinition({
  champs,
  setChamps,
  desactive,
  agent,
}: {
  champs: Champs;
  setChamps: (champs: Champs) => void;
  desactive: boolean;
  /** Le nom de l'agent s'il existe déjà — alors son playbook s'édite ailleurs. */
  agent?: string;
}) {
  // Une seule lecture du catalogue d'agents, deux usages : les rôles (#255) et
  // le vocabulaire des compétences (#256).
  const fiches = useCatalogueAgents();
  const roles = rolesConnus(fiches);
  const vocabulaire = fiches === null ? null : vocabulaireDuCatalogue(fiches);
  const prefixe = useId();
  const idRoles = `${prefixe}-roles`;
  const idCompetences = `${prefixe}-competences`;
  const inconnues = inedites(champs.competences, vocabulaire);

  return (
    <div className="flex flex-col gap-3">
      <ChampsDuModele
        champs={champs}
        setChamps={setChamps}
        desactive={desactive}
        enTete={
          <label className={CLASSE_LIBELLE}>
            Rôle
            <input
              type="text"
              value={champs.role}
              onChange={(e) => setChamps({ ...champs, role: e.target.value })}
              disabled={desactive}
              placeholder="Développeur front"
              list={roles.length > 0 ? idRoles : undefined}
              className={CLASSE_CHAMP}
            />
            {roles.length > 0 && (
              // Une liste **alimentée**, jamais fermée : un rôle inédit se
              // saisit tel quel, et c'est ce que le critère 1 de #255 demande.
              <datalist id={idRoles}>
                {roles.map((role) => (
                  <option key={role} value={role} />
                ))}
              </datalist>
            )}
          </label>
        }
      />
      {/* Hors de la grille, et après la chaîne fournisseur → modèle → effort :
          un champ à jetons porte ses jetons, ses suggestions et son
          signalement, donc une hauteur qui n'a rien à voir avec celle d'une
          saisie sur une ligne. Il ne borne rien et n'est borné par rien — sa
          place n'est pas dans la chaîne, elle est à côté. */}
      <ChampJetons
        id={idCompetences}
        libelle="Compétences"
        valeurs={champs.competences}
        onChange={(competences) => setChamps({ ...champs, competences })}
        suggestions={vocabulaire ?? []}
        signales={new Set(inconnues)}
        motSignal="inédite"
        nomElement="la compétence"
        vide="Aucune compétence pour l'instant : saisir un mot, puis Entrée."
        desactive={desactive}
        placeholder="frontend"
        aide={
          <>
            À quoi elles servent : le routeur <strong>auto-assigne</strong> une
            tâche en confrontant, <strong>au mot près</strong>, les compétences
            qu&apos;elle demande à celles de chaque agent. Un mot qui ne tombe
            pas juste ne compte pour rien.
          </>
        }
        avertissement={
          inconnues.length > 0 ? (
            <SignalementInedites
              inconnues={inconnues}
              vocabulaire={vocabulaire ?? []}
              remplacer={(ancienne, nouvelle) =>
                setChamps({
                  ...champs,
                  competences: champs.competences.map((c) =>
                    c === ancienne ? nouvelle : c,
                  ),
                })
              }
              desactive={desactive}
            />
          ) : null
        }
      />
      {agent === undefined ? (
        <label className={CLASSE_LIBELLE}>
          Playbook (les instructions du rôle — son prompt système)
          <textarea
            value={champs.playbook}
            onChange={(e) => setChamps({ ...champs, playbook: e.target.value })}
            disabled={desactive}
            spellCheck={false}
            placeholder={"Tu es développeur front de l'équipe.\n- Livre du code testé…"}
            className={
              "h-64 w-full resize-y rounded-md border border-neutral-200 bg-white p-3 " +
              "font-mono text-xs leading-relaxed text-neutral-900 shadow-sm " +
              "focus:border-neutral-400 focus:outline-none disabled:opacity-50 " +
              "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600"
            }
          />
        </label>
      ) : (
        <RenvoiPlaybook agent={agent} />
      )}
    </div>
  );
}

/**
 * La génération assistée (#257) : une intention en une phrase, une définition
 * proposée dans le formulaire ci-dessous.
 *
 * Trois choses que ce bloc ne fait pas, et qui sont le ticket :
 *
 * 1. **il n'enregistre rien** — la proposition remplit les champs, et rien
 *    d'autre ne se passe tant que « Créer l'agent » n'a pas été cliqué. C'est le
 *    principe des propositions de playbook (#111/#140) : une suggestion n'est pas
 *    une version ;
 * 2. **il ne se substitue pas au formulaire** — les champs restent ceux qu'on
 *    remplit à la main, et la proposition y arrive comme une saisie ordinaire,
 *    donc modifiable mot à mot ;
 * 3. **il ne touche à rien quand il échoue** — quota, réseau, fournisseur muet :
 *    le message est rendu ici, le formulaire garde exactement ce qu'il portait.
 *
 * Il est **en tête** et non en pied : c'est une porte d'entrée, pas une action
 * de fin de saisie. Sa surface est `creuse` — un contenant en retrait du fond —
 * pour qu'il se lise comme une aide posée devant le formulaire et non comme une
 * seconde section de plein rang.
 */
function AssistantDefinition({
  intention,
  setIntention,
  enCours,
  propose,
  erreur,
  generer,
  abandonner,
  desactive,
}: {
  intention: string;
  setIntention: (intention: string) => void;
  /** Une génération est en vol : le champ et les deux boutons attendent. */
  enCours: boolean;
  /** Une proposition est en place dans le formulaire (régénérable, abandonnable). */
  propose: boolean;
  erreur: string | null;
  generer: () => void;
  abandonner: () => void;
  /** La création est en cours : tout le formulaire est figé, celui-ci compris. */
  desactive: boolean;
}) {
  const pret = intention.trim() !== "" && !enCours && !desactive;
  return (
    <div
      className={classesCarte({
        ton: "creuse",
        className: "flex flex-col gap-2",
      })}
    >
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <label className={CLASSE_LIBELLE + " min-w-0 flex-1"}>
          Décrire l&apos;agent en une phrase
          <input
            type="text"
            value={intention}
            onChange={(e) => setIntention(e.target.value)}
            // Entrée génère : c'est le geste attendu dans un champ à une ligne
            // suivi d'un seul bouton. `preventDefault` parce que le champ vit
            // dans une page qui porte d'autres actions — valider ici ne doit rien
            // déclencher d'autre.
            onKeyDown={(e) => {
              if (e.key !== "Enter") return;
              e.preventDefault();
              if (pret) generer();
            }}
            disabled={enCours || desactive}
            placeholder="Un agent qui relit mes migrations SQL avant de les appliquer"
            className={CLASSE_CHAMP}
          />
        </label>
        <Bouton
          icone={IconeAssistant}
          disabled={!pret}
          occupe={enCours}
          onClick={generer}
        >
          {enCours ? "Génération…" : propose ? "Régénérer" : "Générer"}
        </Bouton>
      </div>
      {propose ? (
        <div
          role="status"
          className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-neutral-600 dark:text-neutral-400"
        >
          <span>
            Proposition en brouillon : relisez et corrigez ci-dessous. Rien
            n&apos;est enregistré tant que vous n&apos;avez pas créé
            l&apos;agent.
          </span>
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            disabled={enCours || desactive}
            onClick={abandonner}
          >
            Abandonner la proposition
          </Bouton>
        </div>
      ) : (
        <p className={CLASSE_ANNEXE}>
          L&apos;assistant remplit le formulaire ci-dessous — rôle, compétences,
          playbook et réglages suggérés — sans rien enregistrer.
        </p>
      )}
      {erreur && (
        <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur} — le formulaire est intact, vous pouvez réessayer ou remplir
          les champs à la main.
        </p>
      )}
    </div>
  );
}

/**
 * Le renvoi vers l'onglet Playbook — ce qui remplace le champ retiré (#259).
 *
 * Retirer un champ sans dire où sa valeur s'écrit désormais, ce serait la
 * moitié muette du critère 1 : on aurait supprimé le doublon **et** le chemin.
 * Le patron est celui que `FicheDefaut` tenait déjà pour les agents du code —
 * il devient celui de tout le monde.
 */
function RenvoiPlaybook({ agent }: { agent: string }) {
  return (
    <p className="rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400">
      Les instructions de cet agent — son playbook — s&apos;éditent (et se
      versionnent) depuis l&apos;onglet{" "}
      <Link
        href={cheminOnglet(agent, "playbook")}
        className="inline-flex items-center gap-1 font-medium text-neutral-900 underline dark:text-neutral-200"
      >
        <IconePlaybooks className="size-3.5 shrink-0" />
        Playbook
      </Link>
      , seul endroit où elles s&apos;écrivent.
    </p>
  );
}

/** Le formulaire « nouvel agent » : la définition complète, nom compris (`POST`). */
export function CreationAgent({
  onCreation,
  onBrouillon,
}: {
  /** Prévenir la page qu'un agent est né : elle recharge et sélectionne sa fiche. */
  onCreation: (nom: string) => void | Promise<void>;
  /**
   * Prévenir le cadre qu'une saisie est **commencée et non enregistrée** (#254).
   *
   * C'est lui, et non le formulaire, qui porte les sorties — le retour à la
   * liste, la touche Échap, la fermeture de l'onglet — donc lui qui doit savoir
   * s'il y a quelque chose à perdre. Le formulaire garde son état ; il n'en
   * publie que ce fait-là.
   */
  onBrouillon?: (brouillon: boolean) => void;
}) {
  const [nom, setNom] = useState("");
  const [champs, setChamps] = useState<Champs>(CHAMPS_VIERGES);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [intention, setIntention] = useState("");
  const [generation, setGeneration] = useState(false);
  const [erreurGeneration, setErreurGeneration] = useState<string | null>(null);
  // Ce que le formulaire portait **avant** la première proposition — ce que
  // « Abandonner » restitue. Posé une seule fois : régénérer remplace la
  // proposition, il ne réécrit pas le point de retour, sans quoi abandonner après
  // trois essais rendrait le troisième au lieu de la saisie d'origine.
  const [avant, setAvant] = useState<{ nom: string; champs: Champs } | null>(
    null,
  );

  const format = SLUG_NOM.test(nom);
  const reserve = estNomAgentReserve(nom);
  const nomValide = format && !reserve;
  const pret = nomValide && champsComplets(champs);

  // Un brouillon, c'est une saisie qui a commencé : le nom, n'importe quel champ,
  // ou l'intention (#257) — elle aussi est une saisie qu'on perdrait en quittant,
  // et la seule qui puisse exister avant que les champs soient remplis. Les
  // espaces seuls n'en font pas un — il n'y aurait rien à perdre. Les compétences
  // sont une liste depuis #256 : un jeton posé compte, une liste vide non — même
  // règle que pour une chaîne d'espaces.
  const brouillon =
    nom.trim() !== "" ||
    intention.trim() !== "" ||
    Object.values(champs).some((valeur) =>
      Array.isArray(valeur) ? valeur.length > 0 : valeur.trim() !== "",
    );
  useEffect(() => {
    onBrouillon?.(brouillon);
  }, [brouillon, onBrouillon]);

  /**
   * Demande une proposition et la pose dans les champs (#257).
   *
   * L'écriture n'a lieu qu'**après** la réponse : un échec — quota, réseau,
   * fournisseur muet ou hors contrat — ne fait que poser un message, et le
   * formulaire garde ce qu'il portait, à la virgule près.
   */
  const generer = async () => {
    setGeneration(true);
    setErreurGeneration(null);
    try {
      const proposition = await genererDefinitionAgent(intention);
      setAvant((precedent) => precedent ?? { nom, champs });
      setNom(proposition.nom);
      setChamps(champsDepuisProposition(proposition));
    } catch (e) {
      setErreurGeneration(e instanceof Error ? e.message : String(e));
    } finally {
      setGeneration(false);
    }
  };

  /** Rend au formulaire ce qu'il portait avant la proposition — l'intention reste. */
  const abandonner = () => {
    setNom(avant?.nom ?? "");
    setChamps(avant?.champs ?? CHAMPS_VIERGES);
    setAvant(null);
    setErreurGeneration(null);
  };

  const creer = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await creerAgent(nom, definitionDepuis(champs));
      await onCreation(nom);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
    // Succès : la page remplace ce formulaire par la fiche créée — ne plus
    // toucher à l'état d'un composant démonté.
  };

  return (
    <section
      aria-label="Nouvel agent"
      className="flex min-w-0 flex-1 flex-col gap-4"
    >
      {/* Pas d'en-tête ici depuis #254 : la création a son écran, et c'est lui
          qui la titre — le redire ferait deux titres pour une seule page. */}
      <AssistantDefinition
        intention={intention}
        setIntention={setIntention}
        enCours={generation}
        propose={avant !== null}
        erreur={erreurGeneration}
        generer={() => void generer()}
        abandonner={abandonner}
        desactive={enCours}
      />
      <label className={CLASSE_LIBELLE + " sm:max-w-xs"}>
        Nom (identifiant unique : minuscules, chiffres, - ou _)
        <input
          type="text"
          value={nom}
          onChange={(e) => setNom(e.target.value)}
          // Figé aussi pendant une génération (#257) : la réponse va écrire dans
          // ce champ, et une saisie faite entre-temps serait perdue sans que
          // personne l'ait décidé.
          disabled={enCours || generation}
          placeholder="dev-front"
          className={CLASSE_CHAMP + " font-mono"}
        />
      </label>
      {nom !== "" && !format && (
        <p className="-mt-2 text-xs text-amber-700 dark:text-amber-400">
          Nom hors format : commencer par une lettre ou un chiffre, en
          minuscules sans espace ni accent (ex. « dev-front »).
        </p>
      )}
      {reserve && (
        <p className="-mt-2 text-xs text-amber-700 dark:text-amber-400">
          « {nom} » est l&apos;adresse de cette page ({CHEMIN_CREATION_AGENT}) :
          un agent qui le porterait n&apos;y serait plus atteignable. Choisir un
          autre nom.
        </p>
      )}
      <FormulaireDefinition
        champs={champs}
        setChamps={setChamps}
        desactive={enCours || generation}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Bouton
          disabled={!pret || generation}
          occupe={enCours}
          onClick={() => void creer()}
        >
          {enCours ? "Création…" : "Créer l'agent"}
        </Bouton>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          L&apos;agent créé est routable et exécutable par les moteurs
          construits ensuite.
        </span>
      </div>
      {erreur && (
        <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
    </section>
  );
}

/**
 * L'onglet Profil d'un agent : édition et suppression s'il est personnalisé,
 * lecture seule s'il vient du code (agent par défaut). Ses serveurs MCP et sa
 * politique de permissions ont leur propre onglet (#190).
 */
export function EditeurAgent({
  nom,
  onSuppression,
}: {
  nom: string;
  /**
   * Prévenir la fiche que l'agent n'existe plus : elle n'a plus rien à montrer
   * et revient à la liste. Une modification, elle, se resynchronise sur place.
   */
  onSuppression: () => void;
}) {
  const [fiche, setFiche] = useState<AgentCatalogueDetail | null>(null);
  const [champs, setChamps] = useState<Champs>(CHAMPS_VIERGES);
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [suppressionArmee, setSuppressionArmee] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const recharger = useCallback(async () => {
    const nouvelle = await chargerAgentCatalogue(nom);
    setFiche(nouvelle);
    return nouvelle;
  }, [nom]);

  // Chargement différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    let abandonne = false;
    const tick = setTimeout(() => {
      setChargement(true);
      setErreur(null);
      recharger()
        .then((nouvelle) => {
          if (!abandonne) setChamps(champsDepuis(nouvelle));
        })
        .catch((e) => {
          if (!abandonne) setErreur(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!abandonne) setChargement(false);
        });
    }, 0);
    return () => {
      abandonne = true;
      clearTimeout(tick);
    };
  }, [recharger]);

  const enregistrer = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await modifierAgent(nom, definitionDepuis(champs));
      // Resynchronisation sur la définition normalisée par le dépôt
      // (rôle épuré, compétences dédoublonnées, date de modification).
      setChamps(champsDepuis(await recharger()));
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  const supprimer = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await supprimerAgent(nom);
      onSuppression();
      // Succès : la fiche n'a plus d'objet — la navigation démonte ce composant.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
      setSuppressionArmee(false);
    }
  };

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement de la fiche…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-sm text-rose-600 dark:text-rose-400" role="alert">
        Fiche illisible : {erreur}
      </p>
    );
  }

  // Deux sources sur trois mènent ici depuis #259 : un agent du code, surchargé
  // ou non, se règle sur `FicheDefaut` — c'est elle qui sait ce qui est hérité.
  if (estAgentDuCode(fiche.source)) {
    // `key` sur le nom : passer d'un agent du code à un autre **remonte** la
    // fiche, donc repart de ses réglages. Sans lui, l'état de saisie du
    // précédent survivrait à la navigation — on éditerait les réglages d'un
    // agent en croyant régler l'autre.
    return <FicheDefaut key={fiche.nom} fiche={fiche} recharger={recharger} />;
  }

  const modifie =
    JSON.stringify(definitionDepuis(champs)) !==
    JSON.stringify(definitionDepuis(champsDepuis(fiche)));

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Configuration de ${nom}`}>
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190) : la
            section ne redit que ce qui lui est propre. */}
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            Définition
          </h3>
          <span className="rounded-full bg-sky-100 px-2 text-xs text-sky-800 dark:bg-sky-950 dark:text-sky-300">
            personnalisé
          </span>
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {fiche.modifie_le
              ? `modifié le ${formatDateHeure(fiche.modifie_le)}`
              : ""}
          </span>
        </div>
        <FormulaireDefinition
          champs={champs}
          setChamps={setChamps}
          desactive={enCours}
          agent={nom}
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Bouton
            disabled={!modifie || !champsComplets(champs)}
            occupe={enCours}
            onClick={() => void enregistrer()}
          >
            {enCours ? "Envoi…" : "Enregistrer les modifications"}
          </Bouton>
          {modifie && !enCours && (
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => setChamps(champsDepuis(fiche))}
            >
              Annuler les modifications
            </Bouton>
          )}
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {modifie
              ? "Modifications non enregistrées."
              : "La définition modifiée vaut pour les moteurs construits ensuite."}
          </span>
        </div>
        {erreur && (
          <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
            {erreur}
          </p>
        )}
      </section>

      <section
        aria-label={`Suppression de ${nom}`}
        className="flex flex-wrap items-center gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800"
      >
        {suppressionArmee ? (
          <>
            <span className="text-xs font-medium text-rose-700 dark:text-rose-400">
              Supprimer définitivement « {nom} » ? Les moteurs construits
              ensuite ne le chargeront plus.
            </span>
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
              disabled={enCours}
              onClick={() => setSuppressionArmee(false)}
            >
              Garder l&apos;agent
            </Bouton>
          </>
        ) : (
          <Bouton
            variante="contour"
            ton="alerte"
            disabled={enCours}
            onClick={() => setSuppressionArmee(true)}
          >
            Supprimer l&apos;agent…
          </Bouton>
        )}
      </section>
    </div>
  );
}

/**
 * L'onglet MCP & permissions d'un agent (#190) : ce que l'agent peut appeler —
 * les serveurs MCP qu'on lui active, et la politique d'outils que le moteur
 * applique à l'exécution.
 *
 * Ces deux sections étaient reléguées en bas de la fiche du catalogue, après le
 * formulaire de définition et son bouton de suppression : elles n'avaient de
 * page nulle part. Le contenu est inchangé — seul l'endroit l'est.
 */
export function McpEtPermissionsAgent({ nom }: { nom: string }) {
  const [fiche, setFiche] = useState<AgentCatalogueDetail | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  // Chargement différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    let abandonne = false;
    const tick = setTimeout(() => {
      chargerAgentCatalogue(nom)
        .then((nouvelle) => {
          if (!abandonne) setFiche(nouvelle);
        })
        .catch((e) => {
          if (!abandonne) setErreur(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!abandonne) setChargement(false);
        });
    }, 0);
    return () => {
      abandonne = true;
      clearTimeout(tick);
    };
  }, [nom]);

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement de la fiche…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-sm text-rose-600 dark:text-rose-400" role="alert">
        Fiche illisible : {erreur}
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <SectionServeursMcp fiche={fiche} />
      <SectionPermissions fiche={fiche} />
    </div>
  );
}

/**
 * La politique de permissions d'un agent (#110), **en écriture** depuis #262 :
 * ce que le moteur applique à l'exécution se règle ici, au lieu d'éditer
 * `core/permissions/<agent>.json` à la main puis de relancer.
 *
 * Deux listes s'éditent — `allow` et `deny` —, la troisième s'affiche : une
 * entrée `ask` porte **qui la tranche** (#586), un cran qui se pose à froid et
 * dont le choix n'est pas de ce lot. La montrer quand même est la règle de
 * #580 : n'en rendre que deux ferait passer un outil arbitré pour un outil sans
 * contrainte, puisqu'il n'apparaîtrait dans aucune.
 *
 * Chaque geste écrit — pas de bouton « Enregistrer » —, comme les interrupteurs
 * MCP juste au-dessus : l'état local ne bouge qu'**après** l'accord de l'API, si
 * bien qu'une entrée refusée s'efface d'elle-même en laissant son motif à
 * l'écran. C'est ce motif-là qui est utile (il nomme la liste et l'entrée en
 * faute), pas un « politique refusée » de notre cru.
 *
 * Une politique **invalide** reste diagnostiquée comme avant — et se corrige
 * d'ici : le moteur la refuse en bloc, donc rien n'est appliqué, et le seul
 * geste qui débloque est de la remplacer. L'écriture ne relisant pas ce qu'elle
 * écrase, elle aboutit sur un fichier que la lecture refuse.
 */
function SectionPermissions({ fiche }: { fiche: AgentCatalogueDetail }) {
  const prefixe = useId();
  const [politique, setPolitique] = useState<PolitiquePermissions>(
    fiche.permissions ?? { allow: [], ask: {}, deny: [] },
  );
  // La cause du refus de lecture, effacée dès qu'une écriture a repris la main :
  // la fiche, elle, garde celle du chargement — c'est un instantané, pas l'état.
  const [invalide, setInvalide] = useState(fiche.permissions_erreur);
  const [dediee, setDediee] = useState(fiche.permissions !== null);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const outils = fiche.permissions_outils;
  const suggestions = outils.map((outil) => outil.nom);
  const askEntrees = Object.entries(politique.ask);

  /** Écrit la politique voulue ; l'état local ne suit qu'en cas d'accord. */
  const enregistrer = async (voulue: PolitiquePermissions) => {
    setEnCours(true);
    setErreur(null);
    try {
      await definirPermissions(fiche.nom, voulue);
      setPolitique(voulue);
      setInvalide(null);
      setDediee(true);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  return (
    <section aria-label={`Permissions de ${fiche.nom}`}>
      <EnTeteSection
        niveau={3}
        titre="Permissions"
        icone={IconePermissions}
        className="mb-2"
      />
      {invalide !== null ? (
        <div className="flex flex-col gap-2">
          <p
            role="alert"
            className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
          >
            Politique invalide : {invalide}
          </p>
          <p className="text-xs text-neutral-500 dark:text-neutral-400">
            Tant qu&apos;elle est illisible, elle n&apos;est appliquée à rien —
            le moteur refuse la tâche plutôt que d&apos;exécuter sous une
            politique douteuse. Repartir d&apos;une politique vide la remplace
            ici même ; les entrées voulues se réajoutent ensuite.
          </p>
          <div>
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              disabled={enCours}
              onClick={() =>
                void enregistrer({ allow: [], ask: {}, deny: [] })
              }
            >
              Repartir d&apos;une politique vide
            </Bouton>
          </div>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {!dediee && (
            <p className="text-xs text-neutral-500 dark:text-neutral-400">
              Aucune politique dédiée : l&apos;agent dispose de tous les outils
              que son profil expose. La première entrée ajoutée ci-dessous en
              crée une.
            </p>
          )}
          <ChampJetons
            id={`${prefixe}-allow`}
            libelle="allow — liste fermée"
            valeurs={politique.allow}
            onChange={(allow) => void enregistrer({ ...politique, allow })}
            suggestions={suggestions}
            signales={entreesHorsPortee(politique.allow, outils)}
            motSignal="hors des outils exposés"
            nomElement="l'entrée allow"
            vide="Vide — tout ce que le profil expose est permis (hors deny et ask)."
            desactive={enCours}
            placeholder="Read"
            aide="Non vide, elle ferme la liste : tout ce qui n'y figure pas est refusé, sauf ce qu'ask cite — qui est arbitré, pas refusé."
          />
          <ChampJetons
            id={`${prefixe}-deny`}
            libelle="deny — l'emporte sur tout"
            valeurs={politique.deny}
            onChange={(deny) => void enregistrer({ ...politique, deny })}
            suggestions={suggestions}
            signales={entreesHorsPortee(politique.deny, outils)}
            motSignal="hors des outils exposés"
            nomElement="l'entrée deny"
            vide="Vide — aucun outil interdit."
            desactive={enCours}
            placeholder="mcp__slack__chat_delete"
            aide="Un outil intégré refusé est retiré de la session ; un serveur MCP refusé en entier n'est jamais monté, ses secrets ne sont même pas résolus."
          />
          <ListeArbitrages entrees={askEntrees} agent={fiche.nom} />
        </div>
      )}
      {erreur && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
      <p className="mt-3 text-xs text-neutral-500 dark:text-neutral-400">
        Politique effective, appliquée à l&apos;exécution :{" "}
        <strong>deny l&apos;emporte sur ask, qui l&apos;emporte sur allow</strong>{" "}
        (un appel refusé est tracé au fil d&apos;activité sans condamner le run).
        Une entrée vaut pour l&apos;outil exact ou, aux frontières{" "}
        <code className="font-mono">__</code>, pour tout ce qu&apos;elle
        préfixe : <code className="font-mono">mcp__slack</code> couvre tous les
        outils du serveur.
      </p>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
        Écrite dans{" "}
        <code className="font-mono">core/permissions/{fiche.nom}.json</code>,{" "}
        <strong>versionné avec le dépôt</strong> : chaque enregistrement modifie
        un fichier suivi par git — celui du dossier de travail où tourne cette
        Control Tower, à commiter comme le reste. Elle vaut pour la tâche
        suivante, sans redémarrage.
      </p>
    </section>
  );
}

/**
 * Les entrées `ask` de la politique, avec **qui les tranche** (#586) — affichées
 * et non éditées.
 *
 * Le cran (`auto`/`humain`) est une décision prise à froid, et l'écran ne sait
 * pas encore la poser ; l'ajouter à moitié — une entrée qu'on pourrait créer
 * sans choisir son décideur — la ferait retomber sur le défaut sans le dire,
 * alors que le défaut *est* le cran le plus fermé. Le fichier reste donc la
 * porte d'entrée de cette liste-là, et la section le nomme.
 */
function ListeArbitrages({
  entrees,
  agent,
}: {
  entrees: [string, string][];
  agent: string;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-annexe font-medium text-texte-secondaire">
        ask — soumis à arbitrage
      </span>
      {entrees.length === 0 ? (
        <p className="text-annexe text-texte-secondaire">
          Vide — aucun outil soumis à arbitrage.
        </p>
      ) : (
        <ul className="flex flex-wrap gap-1">
          {entrees.map(([outil, decideur]) => (
            <li key={outil}>
              <span className="inline-flex items-center gap-1 rounded-full bg-attention-creux px-2 py-0.5 text-annexe text-attention-texte">
                {outil}
                <span className="font-medium">— {decideur}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-annexe text-texte-secondaire">
        Un outil arbitré n&apos;est pas interdit : il reste monté, son appel est
        suspendu le temps qu&apos;on tranche. Le cran se pose dans{" "}
        <code className="font-mono">core/permissions/{agent}.json</code>{" "}
        — il n&apos;est pas réglable ici.
      </p>
    </div>
  );
}

/**
 * Les serveurs MCP d'un agent (#133) : la section est passée **en écriture**.
 * Chaque intégration du **pool projet** (configurée une fois depuis les
 * Paramètres) porte un interrupteur qui l'active ou la désactive **pour cet
 * agent** — ce qui remplace l'ancien affichage lecture seule. Les déclarations
 * **héritées** (`core/mcp/<agent>.json`) restent affichées en lecture seule
 * pendant la migration. Une source invalide affiche sa cause exacte.
 */
function SectionServeursMcp({ fiche }: { fiche: AgentCatalogueDetail }) {
  const [activations, setActivations] = useState<string[]>(
    fiche.mcp_activations,
  );
  const [enCours, setEnCours] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);

  const basculer = async (id: string) => {
    const cible = activations.includes(id)
      ? activations.filter((a) => a !== id)
      : [...activations, id];
    setEnCours(id);
    setErreur(null);
    try {
      await definirActivationsMcp(fiche.nom, cible);
      setActivations(cible);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(null);
    }
  };

  // Les serveurs hérités (fichier `<agent>.json`) : ceux montés qui ne viennent
  // pas d'une intégration du pool activée — encore en lecture seule (migration).
  const nomsPoolActives = new Set(
    fiche.mcp_pool
      .filter((i) => activations.includes(i.id))
      .map((i) => i.serveur.nom),
  );
  const herites = fiche.mcp_serveurs.filter((s) => !nomsPoolActives.has(s.nom));

  return (
    <section aria-label={`Serveurs MCP de ${fiche.nom}`}>
      <EnTeteSection
        niveau={3}
        titre="Serveurs MCP"
        icone={IconeMcp}
        className="mb-2"
      />
      {fiche.mcp_pool_erreur !== null && (
        <p
          role="alert"
          className="mb-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Pool invalide : {fiche.mcp_pool_erreur}
        </p>
      )}
      {fiche.mcp_pool.length === 0 ? (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Aucune intégration au pool projet. Ajoutez-en depuis l&apos;écran{" "}
          <Link
            href="/integrations"
            className="font-medium text-emerald-700 underline dark:text-emerald-400"
          >
            Intégrations
          </Link>
          , puis activez-les ici pour cet agent.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {fiche.mcp_pool.map((integration) => (
            <LigneActivation
              key={integration.id}
              integration={integration}
              actif={activations.includes(integration.id)}
              enCours={enCours === integration.id}
              basculer={() => void basculer(integration.id)}
            />
          ))}
        </ul>
      )}
      {erreur && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
      {fiche.mcp_erreur !== null && (
        <p
          role="alert"
          className="mt-2 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Déclaration invalide : {fiche.mcp_erreur}
        </p>
      )}
      {herites.length > 0 && (
        <div className="mt-3">
          <p className="mb-1 text-xs font-medium text-neutral-500 dark:text-neutral-400">
            Hérités de{" "}
            <code className="font-mono">core/mcp/{fiche.nom}.json</code> —
            lecture seule (à migrer vers le pool)
          </p>
          <ul className="flex flex-col gap-2">
            {herites.map((serveur) => (
              <li
                key={serveur.nom}
                className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-xs dark:border-neutral-800 dark:bg-neutral-900"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{serveur.nom}</span>
                  <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                    {serveur.type}
                  </span>
                  {serveur.optionnel ? (
                    <Infobulle
                      texte="Serveur omis du montage (sans échec) tant que son secret n'est pas fourni"
                      className="inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
                    >
                      optionnel
                    </Infobulle>
                  ) : null}
                  <code className="truncate font-mono text-neutral-600 dark:text-neutral-400">
                    {serveur.type === "stdio"
                      ? [serveur.commande, ...serveur.args].join(" ")
                      : serveur.url}
                  </code>
                </div>
                <CouplesMasques
                  libelle={serveur.type === "stdio" ? "env" : "headers"}
                  valeurs={
                    serveur.type === "stdio" ? serveur.env : serveur.headers
                  }
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

/** Un interrupteur d'activation d'une intégration du pool pour l'agent (#133). */
function LigneActivation({
  integration,
  actif,
  enCours,
  basculer,
}: {
  integration: IntegrationPoolMcp;
  actif: boolean;
  enCours: boolean;
  basculer: () => void;
}) {
  // Un secret manquant ou expiré : l'intégration s'active, mais on prévient
  // qu'elle ne montera pas tant que son secret n'est pas (re)configuré.
  const secretManquant = integration.secrets.find((s) => !s.present || !s.valide);
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-neutral-200 bg-white px-3 py-2 text-xs dark:border-neutral-800 dark:bg-neutral-900">
      <Interrupteur
        libelle={`Activer ${integration.serveur.nom} pour cet agent`}
        actif={actif}
        desactive={enCours}
        basculer={basculer}
      />
      <span className="font-medium">{integration.serveur.nom}</span>
      <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
        {integration.serveur.type}
      </span>
      {actif && secretManquant && (
        <Infobulle
          texte="Configurer le secret sur l'écran Intégrations"
          className="inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
        >
          secret à configurer
        </Infobulle>
      )}
    </li>
  );
}

/** Les paires clé → valeur (masquée) d'un serveur : env (stdio) ou headers (distant). */
function CouplesMasques({
  libelle,
  valeurs,
}: {
  libelle: string;
  valeurs: ServeurMcp["env"];
}) {
  const couples = Object.entries(valeurs);
  if (couples.length === 0) {
    return null;
  }
  return (
    <dl className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-neutral-500 dark:text-neutral-400">
      {couples.map(([cle, valeur]) => (
        <div key={cle} className="flex gap-1">
          <dt className="font-mono">
            {libelle}.{cle}
          </dt>
          <dd className="font-mono">= {valeur}</dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * La fiche d'un agent **du code** : son identité en lecture seule, ses trois
 * réglages de modèle **surchargeables** (#259).
 *
 * Ce qui a changé, et pourquoi. Cette fiche était entièrement en lecture seule,
 * si bien que changer de modèle sur un agent par défaut — un besoin courant —
 * n'avait qu'un contournement : le **dupliquer** en agent personnalisé, c'est-à-
 * dire recopier son playbook pour ne toucher qu'un réglage, après quoi les deux
 * exemplaires divergent en silence et la copie cesse de suivre le code.
 *
 * Le partage tient en une ligne : **l'identité reste au code, les réglages se
 * posent.** Rôle, compétences et playbook viennent de
 * `maestro.agents.catalog` et continuent d'en suivre les évolutions ;
 * fournisseur, modèle et effort se surchargent, et ce qui n'est **pas**
 * surchargé est marqué « hérité du code » plutôt que d'être présenté comme un
 * choix qu'on aurait fait.
 *
 * ⚠ Trois boutons, trois gestes distincts, et le troisième est le sujet du
 * critère 3 : « Annuler les modifications » revient à l'état affiché (local),
 * « Revenir aux réglages du code » **annule la surcharge** côté serveur, et
 * **aucun** ne supprime — la suppression reste réservée aux agents
 * personnalisés, et le serveur la refuse ici en 403.
 */
function FicheDefaut({
  fiche,
  recharger,
}: {
  fiche: AgentCatalogueDetail;
  /** Relire la fiche après écriture : la surcharge normalisée par le dépôt. */
  recharger: () => Promise<AgentCatalogueDetail>;
}) {
  // L'état de saisie part de la fiche et n'est resynchronisé qu'après une
  // écriture (`ecrire`). Passer d'un agent du code à un autre est traité par le
  // `key` de l'appelant, qui remonte le composant : un `useEffect` qui
  // recopierait la fiche dans l'état à chaque changement serait un rendu en
  // cascade, et surtout un second endroit d'où l'état peut bouger.
  const [champs, setChamps] = useState<Champs>(() => champsDepuis(fiche));
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const reglages = reglagesDepuis(champs);
  const modifie =
    JSON.stringify(reglages) !== JSON.stringify(reglagesDepuis(champsDepuis(fiche)));
  const surcharge = fiche.source === AGENT_SOURCE_SURCHARGE;

  const ecrire = async (action: () => Promise<void>) => {
    setEnCours(true);
    setErreur(null);
    try {
      await action();
      setChamps(champsDepuis(await recharger()));
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  /**
   * D'où vient ce réglage — la moitié « marqué comme tel » du critère 2.
   *
   * Le serveur tranche (`herite`), l'écran ne le redéduit pas : une valeur
   * affichée peut venir du code **ou** avoir été surchargée à l'identique, et
   * seule la première est « héritée » — la comparer au code ici rendrait les
   * deux indiscernables, et l'écran mentirait sur ce qui suit le code.
   */
  const marqueur = (reglage: "fournisseur" | "modele" | "effort") => {
    const duCode = fiche.reglages_du_code?.[reglage] ?? null;
    if (fiche.herite.includes(reglage)) {
      return (
        <span className={CLASSE_ANNEXE}>
          hérité du code{duCode !== null ? ` — ${duCode}` : ""}
        </span>
      );
    }
    return (
      <span className={CLASSE_ANNEXE}>
        surchargé — le code dit{" "}
        <span className="font-mono">
          {duCode ?? "aucun réglage"}
        </span>
      </span>
    );
  };

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Fiche de ${fiche.nom}`}>
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            Définition
          </h3>
          <span className="rounded-full bg-neutral-200 px-2 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            {surcharge ? "agent du code, surchargé" : "agent du code"}
          </span>
          <span className={CLASSE_ANNEXE}>
            {surcharge && fiche.modifie_le
              ? `surchargé le ${formatDateHeure(fiche.modifie_le)}`
              : ""}
          </span>
        </div>
        <dl className="mb-3 grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-xs font-medium text-neutral-500 dark:text-neutral-400">
              Rôle
            </dt>
            <dd className="mt-1">{fiche.role}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-neutral-500 dark:text-neutral-400">
              Compétences
            </dt>
            <dd className="mt-1 flex flex-wrap gap-1">
              {fiche.competences.map((competence) => (
                <span
                  key={competence}
                  className="rounded-full bg-neutral-100 px-2 py-0.5 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300"
                >
                  {competence}
                </span>
              ))}
            </dd>
          </div>
        </dl>
        {/* Le modèle n'est plus une ligne de `<dl>` : c'est un réglage, et les
            trois se règlent avec la même chaîne liée que partout ailleurs. */}
        <ChampsDuModele
          champs={champs}
          setChamps={setChamps}
          desactive={enCours}
          marqueur={marqueur}
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Bouton
            disabled={!modifie}
            occupe={enCours}
            onClick={() =>
              void ecrire(() => surchargerAgent(fiche.nom, reglages))
            }
          >
            {enCours ? "Envoi…" : "Enregistrer les réglages"}
          </Bouton>
          {modifie && !enCours && (
            <Bouton
              variante="contour"
              ton="neutre"
              onClick={() => setChamps(champsDepuis(fiche))}
            >
              Annuler les modifications
            </Bouton>
          )}
          {surcharge && !modifie && (
            <Bouton
              variante="contour"
              ton="neutre"
              occupe={enCours}
              onClick={() =>
                void ecrire(() => annulerSurchargeAgent(fiche.nom))
              }
            >
              Revenir aux réglages du code
            </Bouton>
          )}
          <span className={CLASSE_ANNEXE}>
            {modifie
              ? "Réglages non enregistrés."
              : "Le rôle, les compétences et le playbook restent définis par le code."}
          </span>
        </div>
        {erreur && (
          <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
            {erreur}
          </p>
        )}
      </section>
      {/* Plus de recopie du playbook ici (#259) : il se lit et s'écrit sur son
          onglet, qui le versionne. Un `<pre>` de plus n'était pas un second
          chemin d'écriture, mais c'était bien le même contenu à deux endroits —
          et celui-ci ne savait pas dire quelle version le moteur charge. */}
      <RenvoiPlaybook agent={fiche.nom} />
    </div>
  );
}
