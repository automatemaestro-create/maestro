"use client";

/**
 * Création et configuration d'un agent du catalogue (ticket #73, EF-03) :
 * le formulaire de définition — rôle, compétences, fournisseur/modèle,
 * playbook — branché sur l'API du lot 1 (#72, `/api/catalogue`).
 *
 * Deux entrées : `CreationAgent` (nouvel agent personnalisé, `POST`) et
 * `EditeurAgent` (fiche existante — modification `PUT` et suppression `DELETE`
 * d'un agent personnalisé ; les agents par défaut, définis par le code, sont
 * montrés en lecture seule, leur playbook s'éditant sur la page Playbooks).
 * Un agent créé ou modifié vaut pour les moteurs construits ensuite.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import {
  chargerAgentCatalogue,
  creerAgent,
  modifierAgent,
  supprimerAgent,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  AGENT_SOURCE_DEFAUT,
  type AgentCatalogueDetail,
  type DefinitionAgent,
  type ServeurMcp,
} from "@/lib/types";

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
}: {
  /** Prévenir la page qu'un agent est né : elle recharge et sélectionne sa fiche. */
  onCreation: (nom: string) => void | Promise<void>;
}) {
  const [nom, setNom] = useState("");
  const [champs, setChamps] = useState<Champs>(CHAMPS_VIERGES);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const nomValide = SLUG_NOM.test(nom);
  const pret = nomValide && champsComplets(champs);

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
      <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        ➕ Nouvel agent personnalisé
      </h2>
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
      {nom !== "" && !nomValide && (
        <p className="-mt-2 text-xs text-amber-700 dark:text-amber-400">
          Nom hors format : commencer par une lettre ou un chiffre, en
          minuscules sans espace ni accent (ex. « dev-front »).
        </p>
      )}
      <FormulaireDefinition
        champs={champs}
        setChamps={setChamps}
        desactive={enCours}
      />
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          disabled={enCours || !pret}
          onClick={() => void creer()}
          className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {enCours ? "Création…" : "Créer l'agent"}
        </button>
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
 * La fiche d'un agent du catalogue : édition et suppression s'il est
 * personnalisé, lecture seule s'il vient du code (agent par défaut).
 */
export function EditeurAgent({
  nom,
  onChangement,
}: {
  nom: string;
  /** Prévenir la page qu'une fiche a changé ou disparu : elle recharge la liste. */
  onChangement: () => void | Promise<void>;
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
      await onChangement();
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
      await onChangement();
      // Succès : la page retire cette fiche — le composant est démonté.
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
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            🤖 {nom}
          </h2>
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
          <button
            type="button"
            disabled={enCours || !modifie || !champsComplets(champs)}
            onClick={() => void enregistrer()}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700 disabled:opacity-50"
          >
            {enCours ? "Envoi…" : "Enregistrer les modifications"}
          </button>
          {modifie && !enCours && (
            <button
              type="button"
              onClick={() => setChamps(champsDepuis(fiche))}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
            >
              Annuler les modifications
            </button>
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

      <SectionServeursMcp fiche={fiche} />

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
            <button
              type="button"
              disabled={enCours}
              onClick={() => void supprimer()}
              className="rounded-md bg-rose-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-rose-700 disabled:opacity-50"
            >
              {enCours ? "Suppression…" : "Confirmer la suppression"}
            </button>
            <button
              type="button"
              disabled={enCours}
              onClick={() => setSuppressionArmee(false)}
              className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-900"
            >
              Garder l&apos;agent
            </button>
          </>
        ) : (
          <button
            type="button"
            disabled={enCours}
            onClick={() => setSuppressionArmee(true)}
            className="rounded-md border border-rose-300 px-3 py-1.5 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900 dark:text-rose-400 dark:hover:bg-rose-950"
          >
            Supprimer l&apos;agent…
          </button>
        )}
      </section>
    </div>
  );
}

/**
 * Les serveurs MCP déclarés d'un agent (#104), en lecture seule à ce lot : la
 * déclaration vit dans `core/mcp/<agent>.json` (versionnée avec le dépôt) et
 * le moteur la monte sur les exécutions outillées de l'agent. Les valeurs de
 * secrets sont masquées par le backend — seules les références `${VAR}`
 * restent lisibles. Une déclaration invalide affiche sa cause exacte.
 */
function SectionServeursMcp({ fiche }: { fiche: AgentCatalogueDetail }) {
  if (fiche.mcp_erreur === null && fiche.mcp_serveurs.length === 0) {
    return null;
  }
  return (
    <section aria-label={`Serveurs MCP de ${fiche.nom}`}>
      <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
        🔌 Serveurs MCP
      </h3>
      {fiche.mcp_erreur !== null ? (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Déclaration invalide : {fiche.mcp_erreur}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {fiche.mcp_serveurs.map((serveur) => (
            <li
              key={serveur.nom}
              className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-xs dark:border-neutral-800 dark:bg-neutral-900"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{serveur.nom}</span>
                <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
                  {serveur.type}
                </span>
                <code className="truncate font-mono text-neutral-600 dark:text-neutral-400">
                  {serveur.type === "stdio"
                    ? [serveur.commande, ...serveur.args].join(" ")
                    : serveur.url}
                </code>
              </div>
              <CouplesMasques
                libelle={serveur.type === "stdio" ? "env" : "headers"}
                valeurs={serveur.type === "stdio" ? serveur.env : serveur.headers}
              />
            </li>
          ))}
        </ul>
      )}
      <p className="mt-2 text-xs text-neutral-500 dark:text-neutral-400">
        Déclarés dans{" "}
        <code className="font-mono">core/mcp/{fiche.nom}.json</code> et montés
        sur les exécutions outillées de l&apos;agent — lecture seule à ce lot.
      </p>
    </section>
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
 * modifiable ni supprimable ici — seul son playbook s'édite, page Playbooks.
 */
function FicheDefaut({ fiche }: { fiche: AgentCatalogueDetail }) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Fiche de ${fiche.nom}`}>
        <div className="mb-3 flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
            🤖 {fiche.nom}
            {fiche.role ? ` (${fiche.role})` : ""}
          </h2>
          <span className="rounded-full bg-neutral-200 px-2 text-xs text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
            agent du code
          </span>
        </div>
        <dl className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
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
          ici. Ses instructions s&apos;éditent (et se versionnent) depuis la
          page{" "}
          <Link
            href="/playbooks"
            className="font-medium text-neutral-900 underline dark:text-neutral-200"
          >
            📖 Playbooks
          </Link>
          .
        </p>
      </section>
      <SectionServeursMcp fiche={fiche} />
      <section aria-label={`Playbook du code de ${fiche.nom}`}>
        <h3 className="mb-2 text-sm font-semibold uppercase tracking-wide text-neutral-500">
          📖 Playbook du code
        </h3>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-3 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {fiche.playbook}
        </pre>
      </section>
    </div>
  );
}
