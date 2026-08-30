"use client";

/**
 * Création et configuration d'un agent du catalogue (ticket #73, EF-03) :
 * le formulaire de définition — rôle, compétences, fournisseur/modèle,
 * playbook — branché sur l'API du lot 1 (#72, `/api/catalogue`).
 *
 * Deux entrées, une par facette de la **définition** (#190) : `CreationAgent`
 * (nouvel agent personnalisé, `POST` — sur **son propre écran** depuis #254,
 * `components/CreationAgentEcran`) et `EditeurAgent` (onglet Profil — fiche
 * existante, modification `PUT` et suppression `DELETE` d'un agent
 * personnalisé ; les agents par défaut, définis par le code, sont montrés en
 * lecture seule, leur playbook s'éditant sur l'onglet Playbook). Un agent créé
 * ou modifié vaut pour les moteurs construits ensuite.
 *
 * L'onglet **MCP & permissions** vivait ici jusqu'à #263 et a son fichier
 * (`components/OngletMcpAgent`) : il n'est plus une vue de la fiche mais
 * l'endroit où les intégrations d'un agent se règlent — bibliothèque, ajout,
 * migration des déclarations héritées. Ce fichier-ci reste le formulaire de
 * définition.
 *
 * Depuis #257 la création a **deux entrées** et non plus une : les champs qu'on
 * remplit, et une intention en une phrase que l'assistant transforme en
 * définition proposée (`AssistantDefinition`). La seconde n'ajoute aucun chemin
 * d'écriture — elle remplit les champs de la première, qui reste seule à créer.
 */

import Link from "next/link";
import { useCallback, useEffect, useId, useState } from "react";

import { ChampJetons } from "@/components/ChampJetons";
import {
  IconeAlerte,
  IconeAssistant,
  IconePlaybooks,
} from "@/components/Icones";
import { Bouton, EnTeteSection, classesCarte } from "@/components/Primitives";
import {
  CHEMIN_CREATION_AGENT,
  cheminOnglet,
  estNomAgentReserve,
} from "@/lib/agents";
import {
  chargerAgentCatalogue,
  chargerCatalogue,
  chargerFournisseurs,
  creerAgent,
  genererDefinitionAgent,
  modifierAgent,
  supprimerAgent,
} from "@/lib/api";
import {
  competenceProche,
  inedites,
  normaliserCompetence,
  vocabulaireDuCatalogue,
} from "@/lib/competences";
import { formatDateHeure } from "@/lib/format";
import {
  AGENT_SOURCE_DEFAUT,
  type AgentCatalogue,
  type AgentCatalogueDetail,
  type CatalogueFournisseurs,
  type DefinitionAgent,
  type DefinitionAgentProposee,
  type FournisseurCatalogue,
} from "@/lib/types";

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
 * Les champs communs de la définition (création comme modification).
 *
 * Depuis #255 quatre d'entre eux sont **liés**, et l'ordre à l'écran est celui
 * de la dépendance : rôle, puis **fournisseur → modèle → effort**. Chaque
 * maillon borne le suivant, si bien qu'on ne peut plus composer une
 * configuration qui n'existe pas.
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
function FormulaireDefinition({
  champs,
  setChamps,
  desactive,
}: {
  champs: Champs;
  setChamps: (champs: Champs) => void;
  desactive: boolean;
}) {
  const catalogue = useCataloguePoste();
  // Une seule lecture du catalogue d'agents, deux usages : les rôles (#255) et
  // le vocabulaire des compétences (#256).
  const fiches = useCatalogueAgents();
  const roles = rolesConnus(fiches);
  const vocabulaire = fiches === null ? null : vocabulaireDuCatalogue(fiches);
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
  const idRoles = `${prefixe}-roles`;
  const idPoste = `${prefixe}-poste`;
  const idCompetences = `${prefixe}-competences`;
  const inconnues = inedites(champs.competences, vocabulaire);

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
            // Une liste **alimentée**, jamais fermée : un rôle inédit se saisit
            // tel quel, et c'est ce que le critère 1 demande explicitement.
            <datalist id={idRoles}>
              {roles.map((role) => (
                <option key={role} value={role} />
              ))}
            </datalist>
          )}
        </label>
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

  if (fiche.source === AGENT_SOURCE_DEFAUT) {
    return <FicheDefaut fiche={fiche} />;
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
 * La fiche en lecture seule d'un agent par défaut : défini par le code, ni
 * modifiable ni supprimable ici — seul son playbook s'édite, onglet Playbook.
 */
function FicheDefaut({ fiche }: { fiche: AgentCatalogueDetail }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Fiche de ${fiche.nom}`}>
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h3 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            Définition
          </h3>
          <span className="rounded-full bg-neutral-200 px-2 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            agent du code
          </span>
        </div>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
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
          <div>
            <dt className="text-xs font-medium text-neutral-500 dark:text-neutral-400">
              Modèle
            </dt>
            <dd className="mt-1 font-mono text-xs">
              {fiche.modele ?? "modèle par défaut des exécutants"}
            </dd>
          </div>
        </dl>
        <p className="mt-3 rounded-md border border-neutral-200 bg-neutral-50 px-3 py-2 text-xs text-neutral-600 dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-400">
          Agent par défaut, défini par le code : ni modifiable ni supprimable
          ici. Ses instructions s&apos;éditent (et se versionnent) depuis
          l&apos;onglet{" "}
          <Link
            href={cheminOnglet(fiche.nom, "playbook")}
            className="inline-flex items-center gap-1 font-medium text-neutral-900 underline dark:text-neutral-200"
          >
            <IconePlaybooks className="size-3.5 shrink-0" />
            Playbook
          </Link>
          .
        </p>
      </section>
      <section aria-label={`Playbook du code de ${fiche.nom}`}>
        <EnTeteSection
          niveau={3}
          titre="Playbook du code"
          icone={IconePlaybooks}
          className="mb-2"
        />
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-3 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {fiche.playbook}
        </pre>
      </section>
    </div>
  );
}
