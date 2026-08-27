"use client";

/**
 * Section « Intégrations MCP » des Paramètres (#133, lot 4/5 de #129) : la
 * Control Tower en **écriture** pour la configuration MCP. Deux blocs :
 *
 * 1. le **pool projet** — les intégrations déjà configurées (secret saisi une
 *    seule fois, chiffré côté serveur), retirables ; l'état de chaque secret
 *    (présent / valide / expiré) y est visible sans jamais réémettre de valeur ;
 * 2. la **bibliothèque** — le registre curé recherchable (`GET /api/mcp/registre`,
 *    #131) : on cherche une intégration (« figma », « gitlab »…), on la configure
 *    **selon son mode d'auth** (token statique / appairage / token OAuth importé,
 *    docs/21) et on l'**ajoute au pool** (`POST /api/mcp/pool`).
 *
 * L'activation d'une intégration **par agent** (l'autre moitié du ticket) vit sur
 * la fiche de l'agent, onglet « MCP & permissions » depuis #190
 * (`McpEtPermissionsAgent`), là où elle remplace l'affichage lecture seule des
 * serveurs MCP.
 */

import { useCallback, useEffect, useState } from "react";

import { Bouton } from "@/components/Primitives";
import {
  ajouterIntegrationPoolMcp,
  chargerPoolMcp,
  chargerProvenanceRegistreMcp,
  chargerRegistreMcp,
  supprimerIntegrationPoolMcp,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  MCP_MODE_APPAIRAGE,
  MCP_MODE_OAUTH,
  MCP_MODE_SANS_SECRET,
  type EntreeRegistreMcp,
  type EtatSecretPool,
  type IntegrationPoolMcp,
  type ProvenanceRegistreMcp,
  type VariableSecret,
} from "@/lib/types";

import { EtatVide } from "./SectionParametres";

/** Libellé lisible d'un mode d'auth (docs/21 §2) — l'inconnu retombe sur sa clé. */
const LIBELLE_MODE: Record<string, string> = {
  token_statique: "Token statique",
  appairage: "Appairage (sans token)",
  oauth_importe: "Token OAuth importé",
  sans_secret: "Sans secret",
};

/** Combien de pistes proposer quand une recherche ne rend rien (#271). */
const PISTES_MAX = 10;

/**
 * Une date **pure** (`AAAA-MM-JJ`) rendue en français, sans passer par `Date` :
 * `new Date("2026-08-28")` est lu en UTC, donc rendrait la veille sur tout
 * fuseau négatif. La date de revue n'a pas d'heure — la traiter comme un
 * instant est ce qui la ferait reculer d'un jour.
 */
function formatDateSeule(iso: string): string {
  const jour = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso);
  return jour ? `${jour[3]}/${jour[2]}/${jour[1]}` : iso;
}

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-sm " +
  "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 " +
  "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-xs font-medium text-neutral-600 dark:text-neutral-400";

export function ParametresMcp() {
  const [pool, setPool] = useState<IntegrationPoolMcp[]>([]);
  const [poolErreur, setPoolErreur] = useState<string | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargerPool = useCallback(async () => {
    const rendu = await chargerPoolMcp();
    setPool(rendu.integrations);
    setPoolErreur(rendu.erreur);
  }, []);

  // Chargement différé d'un tick (même mécanique que les autres sections) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          await rechargerPool();
          setErreur(null);
        } catch (e) {
          setErreur(e instanceof Error ? e.message : String(e));
        } finally {
          setChargement(false);
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, [rechargerPool]);

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement des intégrations MCP…</p>;
  }
  if (erreur !== null) {
    return (
      <EtatVide
        message={`Intégrations MCP illisibles : ${erreur}`}
        releve="Vérifier que le backend répond (section Général) — la configuration MCP passe par son API."
      />
    );
  }

  const idsPool = new Set(pool.map((i) => i.id));

  return (
    <div className="flex flex-col gap-6">
      <PoolProjet
        pool={pool}
        erreur={poolErreur}
        onChangement={() => void rechargerPool()}
      />
      <Bibliotheque idsPool={idsPool} onAjout={() => void rechargerPool()} />
    </div>
  );
}

/** Le pool projet : les intégrations configurées, avec l'état de leurs secrets. */
function PoolProjet({
  pool,
  erreur,
  onChangement,
}: {
  pool: IntegrationPoolMcp[];
  erreur: string | null;
  onChangement: () => void;
}) {
  return (
    <section aria-label="Pool projet des intégrations MCP" className="flex flex-col gap-2">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Pool projet
      </h3>
      {erreur !== null ? (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Pool invalide : {erreur}
        </p>
      ) : pool.length === 0 ? (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Aucune intégration configurée. Cherchez-en une dans la bibliothèque
          ci-dessous et ajoutez-la au pool — son secret n&apos;est saisi
          qu&apos;une fois, puis partagé par les agents qui l&apos;activent.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {pool.map((integration) => (
            <LignePool
              key={integration.id}
              integration={integration}
              onRetrait={onChangement}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Une intégration du pool : identité, état des secrets, retrait. */
function LignePool({
  integration,
  onRetrait,
}: {
  integration: IntegrationPoolMcp;
  onRetrait: () => void;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const retirer = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await supprimerIntegrationPoolMcp(integration.id);
      onRetrait();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <li className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{integration.serveur.nom}</span>
        <span className="rounded-full bg-neutral-100 px-2 py-0.5 font-mono text-xs text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300">
          {integration.id}
        </span>
        {integration.mode_auth && (
          <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-800 dark:bg-sky-950 dark:text-sky-300">
            {LIBELLE_MODE[integration.mode_auth] ?? integration.mode_auth}
          </span>
        )}
        <button
          type="button"
          disabled={enCours}
          onClick={() => void retirer()}
          className="ml-auto rounded-md border border-rose-300 px-2 py-1 text-xs font-medium text-rose-700 hover:bg-rose-50 disabled:opacity-50 dark:border-rose-900 dark:text-rose-400 dark:hover:bg-rose-950"
        >
          {enCours ? "Retrait…" : "Retirer"}
        </button>
      </div>
      {integration.secrets.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
          {integration.secrets.map((secret) => (
            <li key={secret.cle}>
              <EtatSecretPuce secret={secret} />
            </li>
          ))}
        </ul>
      )}
      {erreur && (
        <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
    </li>
  );
}

/** L'état d'un secret d'une intégration : configuré, à configurer, ou expiré. */
function EtatSecretPuce({ secret }: { secret: EtatSecretPool }) {
  const [classe, texte] = !secret.present
    ? [
        "bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
        "à configurer",
      ]
    : !secret.valide
      ? [
          "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
          secret.expire_le
            ? `expiré le ${formatDateHeure(secret.expire_le)}`
            : "expiré",
        ]
      : secret.ephemere
        ? [
            "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-300",
            "appairage (jetable)",
          ]
        : [
            "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
            secret.expire_le
              ? `valide jusqu'au ${formatDateHeure(secret.expire_le)}`
              : "configuré",
          ];
  return (
    <span className="inline-flex items-baseline gap-1 text-xs">
      <code className="font-mono text-neutral-500 dark:text-neutral-400">
        {secret.cle}
      </code>
      <span className={`rounded-full px-2 py-0.5 ${classe}`}>{texte}</span>
    </span>
  );
}

/** La bibliothèque : recherche du registre curé + configuration d'une entrée. */
function Bibliotheque({
  idsPool,
  onAjout,
}: {
  idsPool: Set<string>;
  onAjout: () => void;
}) {
  const [q, setQ] = useState("");
  const [entrees, setEntrees] = useState<EntreeRegistreMcp[]>([]);
  const [erreur, setErreur] = useState<string | null>(null);
  const [ouverte, setOuverte] = useState<string | null>(null);
  // Le catalogue complet, mémorisé au premier chargement (la recherche part
  // toujours d'une requête vide) : c'est de lui que sortent les **pistes** d'une
  // recherche infructueuse (#271), sans un appel de plus.
  const [catalogue, setCatalogue] = useState<EntreeRegistreMcp[]>([]);
  const [provenance, setProvenance] = useState<ProvenanceRegistreMcp | null>(
    null,
  );
  // Tant que la première recherche n'a pas répondu, « aucun résultat » serait
  // faux : c'est l'état d'avant la question, pas sa réponse. Invisible tant que
  // la liste tenait en quatre entrées et que le vide n'était qu'une phrase ;
  // avec des pistes et un bouton dessous (#271), le clignotement se voit.
  const [charge, setCharge] = useState(false);

  // La provenance ne dépend pas de la recherche : un seul chargement, hors du
  // débounce. Son échec n'est pas une panne de la bibliothèque — la liste reste
  // lisible sans elle, seul le pied de section manque.
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          setProvenance(await chargerProvenanceRegistreMcp());
        } catch {
          setProvenance(null);
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, []);

  // Recherche différée : un court délai après la frappe évite un appel par
  // caractère (l'effet lui-même ne déclenche aucun setState synchrone).
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          const rendu = await chargerRegistreMcp(q);
          setEntrees(rendu);
          if (q.trim() === "") setCatalogue(rendu);
          // Un panneau de configuration ne survit pas à la disparition de son
          // entrée des résultats : on l'**oublie** au lieu de le rouvrir quand
          // l'entrée revient (#231). Sans cet oubli, effacer la recherche
          // remontait le panneau, donc son champ mot de passe, donc le
          // remplissage automatique du gestionnaire de mots de passe qui avait
          // sali la recherche — un champ qu'on ne pouvait plus vider.
          setOuverte((o) =>
            o !== null && rendu.some((entree) => entree.id === o) ? o : null,
          );
          setErreur(null);
        } catch (e) {
          setErreur(e instanceof Error ? e.message : String(e));
        } finally {
          setCharge(true);
        }
      })();
    }, 200);
    return () => clearTimeout(tick);
  }, [q]);

  // Les pistes : les tags des intégrations les plus courantes d'abord (le
  // registre est déjà trié par palier d'usage), à défaut ceux que la provenance
  // rend. Proposer *tous* les tags serait un second cul-de-sac, plus long.
  const pistes = Array.from(
    new Set([
      ...catalogue.flatMap((entree) => entree.tags),
      ...(provenance?.tags ?? []),
    ]),
  ).slice(0, PISTES_MAX);

  return (
    <section aria-label="Bibliothèque de serveurs MCP" className="flex flex-col gap-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-neutral-500 dark:text-neutral-400">
        Bibliothèque
      </h3>
      <label className={CLASSE_LIBELLE}>
        Rechercher une intégration (nom, éditeur, tag…)
        {/*
          `name` + `autoComplete="off"` : un champ anonyme est exactement ce
          qu'un gestionnaire de mots de passe prend pour un champ identifiant
          (#231). Le nom le désigne pour ce qu'il est ; la vraie barrière reste
          le `<form>` qui enferme les champs secrets plus bas, hors de portée
          d'ici.
        */}
        <input
          type="search"
          name="recherche-integration-mcp"
          autoComplete="off"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="figma, gitlab, slack…"
          className={CLASSE_CHAMP}
        />
      </label>
      {erreur !== null ? (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-xs text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Registre illisible : {erreur}
        </p>
      ) : !charge ? (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Chargement de la bibliothèque…
        </p>
      ) : entrees.length === 0 ? (
        // Un cul-de-sac se sort en montrant *par quoi* chercher (#271) : répéter
        // qu'il n'y a rien laisse l'utilisateur sans geste suivant.
        <div className="flex flex-col gap-2">
          <p className="text-sm text-neutral-500 dark:text-neutral-400">
            Aucune intégration ne correspond à « {q} ».
          </p>
          {pistes.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-neutral-500 dark:text-neutral-400">
                Essayer plutôt :
              </span>
              {pistes.map((tag) => (
                <button
                  key={tag}
                  type="button"
                  onClick={() => setQ(tag)}
                  className="rounded-full border border-neutral-300 px-2 py-0.5 text-xs text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
                >
                  {tag}
                </button>
              ))}
            </div>
          )}
          <button
            type="button"
            onClick={() => setQ("")}
            className="self-start text-xs font-medium text-sky-700 underline hover:no-underline dark:text-sky-400"
          >
            Voir toute la bibliothèque
            {provenance ? ` (${provenance.total} intégrations)` : ""}
          </button>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {entrees.map((entree) => (
            <EntreeBibliotheque
              key={entree.id}
              entree={entree}
              dejaAuPool={idsPool.has(entree.id)}
              ouverte={ouverte === entree.id}
              basculer={() =>
                setOuverte((o) => (o === entree.id ? null : entree.id))
              }
              onAjout={() => {
                setOuverte(null);
                onAjout();
              }}
            />
          ))}
        </ul>
      )}
      <p className="text-xs text-neutral-500 dark:text-neutral-400">
        Seules les intégrations curées de cette bibliothèque sont installables
        (découverte ≠ installation, docs/19). Les secrets sont chiffrés côté
        serveur — jamais dans le dépôt Git.
      </p>
      {provenance && <Provenance provenance={provenance} />}
    </section>
  );
}

/**
 * D'où vient la liste, et quand elle a été revue (#271, critère 1). Un registre
 * curé qui ne dit pas sa provenance demande une confiance qu'il ne justifie
 * pas : « les plus utilisés » selon qui, et à quelle date ?
 */
function Provenance({ provenance }: { provenance: ProvenanceRegistreMcp }) {
  return (
    <p className="text-xs text-neutral-500 dark:text-neutral-400">
      {provenance.resume} Revue le{" "}
      <time dateTime={provenance.revue_le}>
        {formatDateSeule(provenance.revue_le)}
      </time>{" "}
      — {provenance.total} intégrations. Sources :{" "}
      {provenance.sources.map((source, index) => (
        <span key={source.url}>
          {index > 0 ? ", " : ""}
          <a
            href={source.url}
            target="_blank"
            rel="noreferrer"
            className="underline hover:no-underline"
          >
            {source.libelle}
          </a>
        </span>
      ))}
      .
    </p>
  );
}

/** Une entrée du registre : sa fiche, dépliable en formulaire de configuration. */
function EntreeBibliotheque({
  entree,
  dejaAuPool,
  ouverte,
  basculer,
  onAjout,
}: {
  entree: EntreeRegistreMcp;
  dejaAuPool: boolean;
  ouverte: boolean;
  basculer: () => void;
  onAjout: () => void;
}) {
  return (
    <li className="rounded-md border border-neutral-200 bg-white px-3 py-2 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{entree.nom}</span>
        <span className="rounded-full bg-sky-100 px-2 py-0.5 text-xs text-sky-800 dark:bg-sky-950 dark:text-sky-300">
          {LIBELLE_MODE[entree.mode_auth] ?? entree.mode_auth}
        </span>
        {dejaAuPool && (
          <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
            au pool
          </span>
        )}
        <button
          type="button"
          onClick={basculer}
          aria-expanded={ouverte}
          className="ml-auto rounded-md border border-neutral-300 px-2 py-1 text-xs font-medium text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
        >
          {ouverte ? "Fermer" : dejaAuPool ? "Reconfigurer" : "Configurer"}
        </button>
      </div>
      <p className="mt-1 text-xs text-neutral-500 dark:text-neutral-400">
        {entree.editeur && (
          <span className="text-neutral-600 dark:text-neutral-300">
            {entree.editeur} —{" "}
          </span>
        )}
        {entree.description}
      </p>
      {entree.tags.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1" aria-label="Tags">
          {entree.tags.map((tag) => (
            <li
              key={tag}
              className="rounded-full bg-neutral-100 px-2 py-0.5 text-[11px] text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400"
            >
              {tag}
            </li>
          ))}
        </ul>
      )}
      {ouverte && (
        <FormulaireConfiguration
          entree={entree}
          dejaAuPool={dejaAuPool}
          onAjout={onAjout}
        />
      )}
    </li>
  );
}

/**
 * Le formulaire de configuration d'une entrée, adapté à son mode d'auth : un
 * champ par variable requise (secret masqué, appairage en clair), une échéance
 * pour un token OAuth importé, et un lien vers la procédure d'émission côté
 * outil. « Ajouter au pool » pose l'intégration et ses secrets, une seule fois.
 */
function FormulaireConfiguration({
  entree,
  dejaAuPool,
  onAjout,
}: {
  entree: EntreeRegistreMcp;
  dejaAuPool: boolean;
  onAjout: () => void;
}) {
  const [valeurs, setValeurs] = useState<Record<string, string>>({});
  const [echeance, setEcheance] = useState("");
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const oauth = entree.mode_auth === MCP_MODE_OAUTH;
  // Un secret vrai (token/OAuth) ne peut être vide ; un appairage l'est aussi
  // (le canal est requis), mais on ne bloque pas une reconfiguration partielle.
  const requisRempli = entree.secrets.every(
    (s) => (valeurs[s.cle] ?? "").trim() !== "",
  );
  const pret = requisRempli && (!oauth || echeance.trim() !== "");

  const ajouter = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      const secrets = entree.secrets
        .map((s) => ({
          cle: s.cle,
          valeur: (valeurs[s.cle] ?? "").trim(),
          ...(oauth && echeance.trim() !== ""
            ? { expire_le: new Date(echeance).toISOString() }
            : {}),
        }))
        .filter((s) => s.valeur !== "");
      await ajouterIntegrationPoolMcp({ registre_id: entree.id, secrets });
      onAjout();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    // Un vrai `<form>`, et pas une `<div>` : c'est lui qui **borne** la
    // détection du gestionnaire de mots de passe (#231). Sans propriétaire de
    // formulaire, un `<input type="password">` est apparié aux champs texte du
    // document — ici la recherche de la bibliothèque, qui se remplissait alors
    // d'un identifiant enregistré. La soumission est neutralisée : rien ne part
    // au serveur par le navigateur, `ajouter` fait l'appel API.
    <form
      autoComplete="off"
      onSubmit={(e) => {
        e.preventDefault();
        if (!enCours && pret) void ajouter();
      }}
      className="mt-3 flex flex-col gap-3 border-t border-neutral-100 pt-3 dark:border-neutral-800"
    >
      {entree.secrets.length === 0 ? (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Cette intégration ne demande aucun secret — l&apos;ajouter au pool
          suffit.
        </p>
      ) : (
        entree.secrets.map((variable) => (
          <ChampSecret
            key={variable.cle}
            variable={variable}
            mode={entree.mode_auth}
            valeur={valeurs[variable.cle] ?? ""}
            setValeur={(v) =>
              setValeurs((etat) => ({ ...etat, [variable.cle]: v }))
            }
            desactive={enCours}
          />
        ))
      )}
      {oauth && (
        <label className={CLASSE_LIBELLE}>
          Échéance du token (il sera refusé au montage une fois expiré)
          <input
            type="datetime-local"
            value={echeance}
            onChange={(e) => setEcheance(e.target.value)}
            disabled={enCours}
            className={CLASSE_CHAMP}
          />
        </label>
      )}
      {entree.procedure_url && (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Procédure d&apos;obtention côté outil :{" "}
          {/*
            Cliquable si c'est une URL, en clair si c'est un chemin du dépôt : la
            bibliothèque élargie (#271) renvoie surtout vers la documentation de
            l'éditeur, qu'on ne recopie pas à la main dans une barre d'adresse.
          */}
          {entree.procedure_url.startsWith("https://") ? (
            <a
              href={entree.procedure_url}
              target="_blank"
              rel="noreferrer"
              className="font-mono underline hover:no-underline"
            >
              {entree.procedure_url}
            </a>
          ) : (
            <code className="font-mono">{entree.procedure_url}</code>
          )}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Bouton type="submit" disabled={!pret} occupe={enCours}>
          {enCours
            ? "Ajout…"
            : dejaAuPool
              ? "Enregistrer la configuration"
              : "Ajouter au pool"}
        </Bouton>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {entree.mode_auth === MCP_MODE_APPAIRAGE
            ? "Valeur d'appairage jetable, à renouveler à chaque session."
            : entree.mode_auth === MCP_MODE_SANS_SECRET
              ? "Aucun secret : rien n'est stocké pour cette intégration."
              : "Secret saisi une seule fois, chiffré côté serveur."}
        </span>
      </div>
      {erreur && (
        <p className="text-xs text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
    </form>
  );
}

/** Un champ de saisie d'une variable : masqué pour un vrai secret, en clair sinon. */
function ChampSecret({
  variable,
  mode,
  valeur,
  setValeur,
  desactive,
}: {
  variable: VariableSecret;
  mode: string;
  valeur: string;
  setValeur: (v: string) => void;
  desactive: boolean;
}) {
  return (
    <label className={CLASSE_LIBELLE}>
      <span>
        <code className="font-mono text-neutral-700 dark:text-neutral-300">
          {variable.cle}
        </code>
        {variable.description ? ` — ${variable.description}` : ""}
      </span>
      {/*
        `new-password` et non `off` sur un champ masqué : Chrome **ignore
        délibérément** `off` sur un champ de mot de passe (il n'honore que
        `new-password`), ce qui laissait le gestionnaire proposer un
        identifiant enregistré et remplir au passage le champ voisin qu'il
        prenait pour son pendant (#231). La valeur dit ce qui est vrai — ce
        token n'est pas un mot de passe connu du navigateur.
      */}
      <input
        type={variable.secret ? "password" : "text"}
        name={variable.cle}
        value={valeur}
        onChange={(e) => setValeur(e.target.value)}
        disabled={desactive}
        autoComplete={variable.secret ? "new-password" : "off"}
        placeholder={
          mode === MCP_MODE_APPAIRAGE ? "canal d'appairage" : "valeur à saisir"
        }
        className={CLASSE_CHAMP + " font-mono"}
      />
    </label>
  );
}
