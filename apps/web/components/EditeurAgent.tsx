"use client";

/**
 * Création et configuration d'un agent du catalogue (ticket #73, EF-03) :
 * le formulaire de définition — rôle, compétences, fournisseur/modèle,
 * playbook — branché sur l'API du lot 1 (#72, `/api/catalogue`).
 *
 * Trois entrées, une par facette de la fiche agent (#190) : `CreationAgent`
 * (nouvel agent personnalisé, `POST` — sur **son propre écran** depuis #254,
 * `components/CreationAgentEcran`), `EditeurAgent` (onglet Profil — fiche
 * existante, modification `PUT` et suppression `DELETE` d'un agent
 * personnalisé ; les agents par défaut, définis par le code, sont montrés en
 * lecture seule, leur playbook s'éditant sur l'onglet Playbook) et
 * `McpEtPermissionsAgent` (onglet MCP & permissions). Un agent créé ou modifié
 * vaut pour les moteurs construits ensuite.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  IconeMcp,
  IconePermissions,
  IconePlaybooks,
} from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { Bouton, EnTeteSection } from "@/components/Primitives";
import {
  CHEMIN_CREATION_AGENT,
  cheminOnglet,
  estNomAgentReserve,
} from "@/lib/agents";
import {
  chargerAgentCatalogue,
  creerAgent,
  definirActivationsMcp,
  modifierAgent,
  supprimerAgent,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  AGENT_SOURCE_DEFAUT,
  type AgentCatalogueDetail,
  type DefinitionAgent,
  type IntegrationPoolMcp,
  type ServeurMcp,
} from "@/lib/types";

import { Interrupteur } from "./parametres/SectionParametres";

/** Miroir du slug accepté par le backend (`_NOM_AGENT`, maestro/agents/store.py). */
const SLUG_NOM = /^[a-z0-9][a-z0-9_-]*$/;

/** Les champs du formulaire, tous en texte libre (les compétences virgulées). */
type Champs = {
  role: string;
  competences: string;
  playbook: string;
  modele: string;
  fournisseur: string;
};

const CHAMPS_VIERGES: Champs = {
  role: "",
  competences: "",
  playbook: "",
  modele: "",
  fournisseur: "",
};

function champsDepuis(fiche: AgentCatalogueDetail): Champs {
  return {
    role: fiche.role,
    competences: fiche.competences.join(", "),
    playbook: fiche.playbook,
    modele: fiche.modele ?? "",
    fournisseur: fiche.fournisseur ?? "",
  };
}

/** Le corps envoyé à l'API : champs épurés, optionnels vides rendus null. */
function definitionDepuis(champs: Champs): DefinitionAgent {
  return {
    role: champs.role.trim(),
    competences: champs.competences
      .split(",")
      .map((c) => c.trim())
      .filter(Boolean),
    playbook: champs.playbook,
    modele: champs.modele.trim() || null,
    fournisseur: champs.fournisseur.trim() || null,
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

/** Les champs communs de la définition (création comme modification). */
function FormulaireDefinition({
  champs,
  setChamps,
  desactive,
}: {
  champs: Champs;
  setChamps: (champs: Champs) => void;
  desactive: boolean;
}) {
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
            className={CLASSE_CHAMP}
          />
        </label>
        <label className={CLASSE_LIBELLE}>
          Compétences (séparées par des virgules)
          <input
            type="text"
            value={champs.competences}
            onChange={(e) =>
              setChamps({ ...champs, competences: e.target.value })
            }
            disabled={desactive}
            placeholder="frontend, react, css"
            className={CLASSE_CHAMP}
          />
        </label>
        <label className={CLASSE_LIBELLE}>
          Modèle (vide : modèle par défaut des exécutants)
          <input
            type="text"
            value={champs.modele}
            onChange={(e) => setChamps({ ...champs, modele: e.target.value })}
            disabled={desactive}
            placeholder="claude-sonnet-5"
            className={CLASSE_CHAMP + " font-mono"}
          />
        </label>
        <label className={CLASSE_LIBELLE}>
          Fournisseur (déclaratif au POC)
          <input
            type="text"
            value={champs.fournisseur}
            onChange={(e) =>
              setChamps({ ...champs, fournisseur: e.target.value })
            }
            disabled={desactive}
            placeholder="anthropic"
            className={CLASSE_CHAMP + " font-mono"}
          />
        </label>
      </div>
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

  const format = SLUG_NOM.test(nom);
  const reserve = estNomAgentReserve(nom);
  const nomValide = format && !reserve;
  const pret = nomValide && champsComplets(champs);

  // Un brouillon, c'est une saisie qui a commencé : le nom ou n'importe quel
  // champ. Les espaces seuls n'en font pas un — il n'y aurait rien à perdre.
  const brouillon =
    nom.trim() !== "" ||
    Object.values(champs).some((valeur) => valeur.trim() !== "");
  useEffect(() => {
    onBrouillon?.(brouillon);
  }, [brouillon, onBrouillon]);

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
      <label className={CLASSE_LIBELLE + " sm:max-w-xs"}>
        Nom (identifiant unique : minuscules, chiffres, - ou _)
        <input
          type="text"
          value={nom}
          onChange={(e) => setNom(e.target.value)}
          disabled={enCours}
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
        desactive={enCours}
      />
      <div className="flex flex-wrap items-center gap-3">
        <Bouton disabled={!pret} occupe={enCours} onClick={() => void creer()}>
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
 * La politique de permissions d'un agent (#110), en lecture seule : la
 * politique allow/ask/deny par outil que le moteur applique à l'exécution
 * (`core/permissions/<agent>.json`, versionnée avec le dépôt). Une politique
 * invalide affiche sa cause exacte.
 *
 * Les **trois** listes sont rendues depuis #580, `ask` comprise, alors même
 * qu'aucun appelant ne consulte encore le verdict d'arbitrage : n'en montrer
 * que deux ferait passer un outil arbitré pour un outil sans contrainte,
 * puisqu'il n'apparaîtrait dans aucune.
 *
 * L'absence de politique se dit désormais explicitement : la section était
 * muette quand tout allait bien, ce qui se lisait comme un panneau de plus tout
 * en bas de la fiche du catalogue ; sur son propre onglet (#190), le silence
 * passerait pour un chargement raté.
 */
function SectionPermissions({ fiche }: { fiche: AgentCatalogueDetail }) {
  const sansPolitique =
    fiche.permissions_erreur === null && fiche.permissions === null;
  return (
    <section aria-label={`Permissions de ${fiche.nom}`}>
      <EnTeteSection
        niveau={3}
        titre="Permissions"
        icone={IconePermissions}
        className="mb-2"
      />
      {fiche.permissions_erreur !== null ? (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Politique invalide : {fiche.permissions_erreur}
        </p>
      ) : sansPolitique ? (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Aucune politique dédiée : l&apos;agent dispose de tous les outils que
          son profil expose.
        </p>
      ) : (
        fiche.permissions !== null && (
          <div className="flex flex-col gap-2">
            <ListeEntreesPolitique
              libelle="allow"
              vide="vide — tout ce que le profil expose est permis (hors deny)"
              entrees={fiche.permissions.allow}
              classeEntree="bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300"
            />
            <ListeEntreesPolitique
              libelle="ask"
              vide="vide — aucun outil soumis à arbitrage"
              entrees={fiche.permissions.ask}
              classeEntree="bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300"
            />
            <ListeEntreesPolitique
              libelle="deny"
              vide="vide — aucun outil interdit"
              entrees={fiche.permissions.deny}
              classeEntree="bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300"
            />
          </div>
        )
      )}
      <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
        Politique effective, appliquée à l&apos;exécution (deny l&apos;emporte
        sur ask, qui l&apos;emporte sur allow ; un appel refusé est tracé au fil
        d&apos;activité sans condamner le run). Déclarée dans{" "}
        <code className="font-mono">core/permissions/{fiche.nom}.json</code> —
        lecture seule à ce lot.
      </p>
    </section>
  );
}

/** Une liste d'entrées (allow, ask ou deny) de la politique, ou son état « vide ». */
function ListeEntreesPolitique({
  libelle,
  vide,
  entrees,
  classeEntree,
}: {
  libelle: string;
  vide: string;
  entrees: string[];
  classeEntree: string;
}) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
      <span className="font-mono font-medium text-neutral-600 dark:text-neutral-400">
        {libelle}
      </span>
      {entrees.length === 0 ? (
        <span className="text-neutral-500 dark:text-neutral-400">{vide}</span>
      ) : (
        entrees.map((entree) => (
          <code
            key={entree}
            className={`rounded-full px-2 py-0.5 font-mono ${classeEntree}`}
          >
            {entree}
          </code>
        ))
      )}
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
