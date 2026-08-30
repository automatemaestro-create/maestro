"use client";

/**
 * La **bibliothèque** de l'écran « Intégrations » (#270, lot 3/6 de #244) : le
 * registre recherchable (`GET /api/mcp/registre`, #131). On cherche une
 * intégration (« figma », « gitlab »…), on la configure **selon son mode
 * d'auth** (token statique / appairage / token OAuth importé, docs/21) et on
 * l'**ajoute au pool** (`POST /api/mcp/pool`).
 *
 * Le bloc vient de `parametres/ParametresMcp.tsx` (#133), dont il était la
 * seconde moitié, et il en garde **la structure au détail près**. Ce n'est pas
 * de la paresse : tout ce que #231 a corrigé face au gestionnaire de mots de
 * passe du navigateur tient dans cette structure — le `<form>` qui borne la
 * détection, la recherche laissée **dehors**, `autoComplete="new-password"` sur
 * le champ masqué (Chrome ignore délibérément `off` sur un champ de mot de
 * passe), le panneau **oublié** quand son entrée quitte les résultats. Un
 * déménagement qui « en profiterait pour ranger » rejouerait le bug.
 *
 * ## Trois sources, et l'écran ne les confond pas (#679)
 *
 * La bibliothèque n'a plus une seule sorte d'entrée. Elle en a trois (#677,
 * #678), et la fédération fait de la **découverte** la plus nombreuse de loin —
 * un écran qui ne dirait pas laquelle est laquelle serait pire que la liste
 * figée d'avant :
 *
 * - **curée** — écrite à la main, relue en revue de code, versionnée. Montable.
 *   C'est le cas nominal de cet écran depuis #131, et **le seul qui ne porte
 *   aucun badge** : marquer 29 entrées sur 29 n'apprend rien, et le pied de
 *   section dit déjà d'où vient la liste ;
 * - **découverte** — le miroir du registre officiel, personne ne l'a approuvée.
 *   **Non montable**, donc pas de formulaire de configuration : le panneau y
 *   rend ses *signaux de confiance* et propose l'**admission** ;
 * - **admise** — une découverte qu'un geste humain a fait entrer dans
 *   l'allowlist. Montable comme une curée, et elle garde ses signaux d'amont.
 *
 * ⚠ Ce qui décide du formulaire est **`curee`** et jamais `source` : le booléen
 * répond à « montable ? », la source à « d'où ça vient ? ». Les lire à l'envers
 * ferait proposer une configuration à une entrée que `POST /api/mcp/pool`
 * refuse — et l'écran promettrait ce que le garde-fou interdit (docs/19).
 *
 * ⚠ La distinction est portée par **le badge et le libellé du bouton**, jamais
 * par la seule couleur du cadre : deux teintes de bord se ressemblent en
 * impression noir et blanc comme pour qui ne les sépare pas. Le trait en
 * pointillés est un appui, pas le signal.
 *
 * ⚠ **Aucun champ de saisie** dans le panneau d'une découverte, bien que l'API
 * d'admission accepte un auteur et une note (`POST /api/mcp/admissions`, #678,
 * tous deux facultatifs). Ajouter un `<input>` de texte hors du `<form>` de
 * configuration rouvrirait la porte de #231 — c'est *exactement* la conjonction
 * qui salissait la recherche. Le geste tracé, ici, est le clic.
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

import { IconeAlerte, IconeMcp } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  EnTeteSection,
} from "@/components/Primitives";
import {
  admettreEntreeMcp,
  ajouterIntegrationPoolMcp,
  chargerProvenanceRegistreMcp,
  chargerRegistreMcp,
} from "@/lib/api";
import {
  MCP_MODE_APPAIRAGE,
  MCP_MODE_OAUTH,
  MCP_MODE_SANS_SECRET,
  MCP_STATUT_DEPRECIE,
  type EntreeRegistreMcp,
  type IntegrationPoolMcp,
  type ProvenanceAdmise,
  type ProvenanceDecouverte,
  type ProvenanceRegistreMcp,
  type VariableSecret,
} from "@/lib/types";

import { libelleMode } from "./modes";

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

/**
 * Le **jour** d'un horodatage d'amont (`2026-08-28T09:12:00Z`) ou d'une date
 * déjà pure (#679).
 *
 * On ne garde que la date, et c'est délibéré : l'heure à laquelle un éditeur a
 * publié une version n'apprend rien à qui juge un serveur, tandis que la rendre
 * dans le fuseau du lecteur ferait reculer d'un jour ce que l'amont a daté —
 * le piège exact que `formatDateSeule` documente pour la date de revue. Un
 * horodatage qu'on ne sait pas lire est rendu **tel quel** plutôt que masqué :
 * un signal de confiance illisible reste un signal, une absence n'en est pas un.
 */
function formatJour(iso: string): string {
  const jour = iso.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(jour) ? formatDateSeule(jour) : iso;
}

/**
 * Comment le registre officiel a vérifié qu'un éditeur possède son namespace.
 *
 * Deux formes seulement, et ce sont celles que la documentation du registre
 * décrit (parent #673) : `io.github.<compte>` est prouvé par OAuth GitHub, un
 * namespace en nom de domaine inversé par une preuve DNS ou HTTP. On ne devine
 * rien au-delà — un namespace dont on ignore le mode de preuve se dit
 * « vérifié à la publication », qui est vrai de tous.
 */
function preuveNamespace(editeur: string): string {
  if (editeur.startsWith("io.github."))
    return "propriété prouvée par OAuth GitHub";
  if (/^[a-z0-9-]+\.[a-z0-9.-]+$/.test(editeur))
    return "propriété prouvée par DNS ou HTTP sur le domaine";
  return "propriété vérifiée par le registre à la publication";
}

const CLASSE_CHAMP =
  "w-full rounded-md border border-neutral-200 bg-white px-3 py-1.5 text-corps " +
  "text-neutral-900 shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 " +
  "dark:border-neutral-800 dark:bg-neutral-900 dark:text-neutral-100 dark:focus:border-neutral-600";

const CLASSE_LIBELLE =
  "flex flex-col gap-1 text-annexe font-medium text-neutral-600 dark:text-neutral-400";

/**
 * Les deux surfaces d'une ligne de bibliothèque (#679). La découverte emprunte
 * la grammaire de l'état vide — trait en pointillés, fond en retrait : la place
 * est **occupée sans être approuvée**. La curée et l'admise sont du contenu
 * plein, parce que toutes deux sont montables.
 */
const CLASSE_LIGNE = "rounded-lg border px-3 py-2.5";
const CLASSE_LIGNE_ALLOWLIST =
  "border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900";
const CLASSE_LIGNE_DECOUVERTE =
  "border-dashed border-neutral-300 bg-neutral-50 dark:border-neutral-700 dark:bg-neutral-950";

/** La bibliothèque : recherche des trois sources + configuration ou admission. */
export function BibliothequeMcp({
  idsPool,
  onAjout,
}: {
  idsPool: Set<string>;
  /**
   * L'ajout au pool a abouti — avec **l'intégration créée** (#263). L'écran
   * Intégrations n'en fait rien (il recharge le pool entier), la fiche d'un
   * agent en a besoin : « ajouter puis activer dans la foulée » suppose de
   * savoir *quel id* activer, et le redemander au pool serait une seconde
   * vérité à rapprocher de la première.
   */
  onAjout: (integration: IntegrationPoolMcp) => void;
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
  // Une admission change **deux** choses que l'API sert séparément : la source
  // de l'entrée (donc son panneau) et les totaux du pied. Ce compteur les
  // relit tous les deux, plutôt que de rapiécer l'entrée en mémoire — la
  // bibliothèque est recomposée côté serveur, et deviner ici ce qu'elle va
  // rendre reviendrait à tenir une seconde vérité (#678).
  const [revision, setRevision] = useState(0);
  const apresAdmission = useCallback(() => setRevision((n) => n + 1), []);

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
  }, [revision]);

  // Recherche différée : un court délai après la frappe évite un appel par
  // caractère (l'effet lui-même ne déclenche aucun setState synchrone).
  //
  // ⚠ Le délai ne vaut que pour une **recherche**. Une recherche vide n'est pas
  // une frappe — c'est l'arrivée sur l'écran, ou le champ qu'on vient de vider —
  // et elle ne peut pas partir en rafale, puisqu'on n'entre qu'une fois dans cet
  // état. Le seul effet du délai y était de retarder de 200 ms l'affichage de la
  // bibliothèque au chargement de la page, ce qui n'a jamais été le but.
  const delai = q.trim() === "" ? 0 : 200;

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
          //
          // ⚠ Une entrée **admise à l'instant** reste dans les résultats sous le
          // même id : son panneau reste donc ouvert et devient le formulaire de
          // configuration, ce qui est l'enchaînement voulu — on vient
          // d'autoriser le serveur, l'étape suivante est de le configurer.
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
    }, delai);
    return () => clearTimeout(tick);
  }, [q, delai, revision]);

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
    <section
      aria-label="Bibliothèque de serveurs MCP"
      className="flex flex-col gap-3"
    >
      <EnTeteSection titre="Bibliothèque" icone={IconeMcp} />
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
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-annexe text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Registre illisible : {erreur}
        </p>
      ) : !charge ? (
        <p className="text-corps text-neutral-500 dark:text-neutral-400">
          Chargement de la bibliothèque…
        </p>
      ) : entrees.length === 0 ? (
        // Un cul-de-sac se sort en montrant *par quoi* chercher (#271) : répéter
        // qu'il n'y a rien laisse l'utilisateur sans geste suivant. Cette sortie
        // est inchangée par la fédération (#679, critère 3) — elle vaut pour la
        // recherche, qui porte sur les trois sources d'un seul geste.
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
              onAjout={(integration) => {
                setOuverte(null);
                onAjout(integration);
              }}
              onAdmission={apresAdmission}
            />
          ))}
        </ul>
      )}
      <p className="text-annexe text-neutral-500 dark:text-neutral-400">
        Seules les intégrations de l&apos;allowlist sont installables — les
        curées, et les découvertes qu&apos;un geste humain y a admises
        (découverte ≠ installation, docs/19). Les secrets sont chiffrés côté
        serveur — jamais dans le dépôt Git.
      </p>
      {provenance && <Provenance provenance={provenance} />}
    </section>
  );
}

/* ------------------------------------------------------------------ *
 * Provenance
 * ------------------------------------------------------------------ */

/**
 * D'où vient la bibliothèque, et de quand (#271, #677, #678, #679).
 *
 * Une ligne par source, **toujours les trois**, et jamais un résumé unique : la
 * curée se date par sa revue humaine, la découverte par le rafraîchissement de
 * son miroir, l'admise par le geste qui l'a faite. Fondre les trois dans une
 * phrase obligerait chacune à porter le vocabulaire des autres — et c'est ce
 * qui faisait dire « jamais moissonnée » au pied d'un écran dont la moitié des
 * entrées venaient d'un moissonnage (#679, critère 3).
 *
 * Les trois lignes sont là même quand l'une est vide (aucun miroir, aucune
 * admission) : un pied dont la forme change avec l'état se relit à chaque fois.
 */
function Provenance({ provenance }: { provenance: ProvenanceRegistreMcp }) {
  const [curee, admise, decouverte] = provenance.provenances;
  return (
    <div className="flex flex-col gap-1 text-xs text-neutral-500 dark:text-neutral-400">
      <p>
        <span className="font-medium text-neutral-600 dark:text-neutral-300">
          Curée
        </span>{" "}
        — {curee.resume} Revue le{" "}
        <time dateTime={curee.revue_le}>{formatDateSeule(curee.revue_le)}</time>{" "}
        — {curee.total} intégrations. Sources :{" "}
        {curee.sources.map((source, index) => (
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
      <p>
        <span className="font-medium text-neutral-600 dark:text-neutral-300">
          Admise
        </span>{" "}
        — {phraseAdmise(admise)}
      </p>
      <p>
        <span className="font-medium text-neutral-600 dark:text-neutral-300">
          Découverte
        </span>{" "}
        — {phraseDecouverte(decouverte)}
      </p>
    </div>
  );
}

/** Ce qu'un humain a fait entrer dans l'allowlist, et ce qu'il en a retiré (#678). */
function phraseAdmise(provenance: ProvenanceAdmise): string {
  if (provenance.total === 0 && provenance.revoquees === 0) {
    return (
      "aucune entrée découverte n'a encore été admise sur ce projet — " +
      "la porte existe, personne ne l'a franchie."
    );
  }
  const morceaux = [
    `${provenance.total} entrée${provenance.total > 1 ? "s" : ""} admise${
      provenance.total > 1 ? "s" : ""
    } par un geste humain tracé`,
  ];
  if (provenance.derniere_le)
    morceaux.push(`dernière le ${formatJour(provenance.derniere_le)}`);
  if (provenance.revoquees > 0)
    morceaux.push(
      `${provenance.revoquees} révoquée${provenance.revoquees > 1 ? "s" : ""} (gardée${provenance.revoquees > 1 ? "s" : ""} au journal)`,
    );
  if (provenance.signaux > 0)
    morceaux.push(
      `${provenance.signaux} signal${provenance.signaux > 1 ? "s" : ""} d'amont depuis`,
    );
  return `${morceaux.join(", ")}.`;
}

/**
 * Ce que le miroir du registre officiel porte, et de quand il date (#677).
 *
 * Les trois états ne se confondent pas : **moissonné** (ce qu'il porte, ce
 * qu'on en sert), **en échec** (la cause et sa date, jamais tue) et **pas
 * encore moissonné** — qui est l'état normal d'un poste neuf, pas une panne.
 */
function phraseDecouverte(provenance: ProvenanceDecouverte): string {
  if (!provenance.amont) {
    return "aucun registre amont n'est branché sur ce poste.";
  }
  if (provenance.moissonnee) {
    const quand = provenance.rafraichi_le
      ? ` le ${formatJour(provenance.rafraichi_le)}`
      : "";
    return (
      `registre MCP officiel (${provenance.amont}), miroir rafraîchi${quand} : ` +
      `${provenance.nombre} entrées moissonnées, ${provenance.total} servies ici ` +
      "(les autres n'étaient pas traduisibles en gabarit Maestro)."
    );
  }
  if (provenance.cause) {
    const quand = provenance.echoue_le
      ? ` le ${formatJour(provenance.echoue_le)}`
      : "";
    return `registre MCP officiel (${provenance.amont}) : dernier rafraîchissement en échec${quand} — ${provenance.cause}`;
  }
  return (
    `registre MCP officiel (${provenance.amont}) : pas encore moissonné sur ce ` +
    "poste — la bibliothèque ne sert pour l'instant que ses entrées curées et admises."
  );
}

/* ------------------------------------------------------------------ *
 * Une entrée
 * ------------------------------------------------------------------ */

/**
 * Une entrée de la bibliothèque : sa fiche, dépliable en **formulaire de
 * configuration** si elle est dans l'allowlist, en **panneau de confiance** si
 * elle n'y est pas encore (#679).
 */
function EntreeBibliotheque({
  entree,
  dejaAuPool,
  ouverte,
  basculer,
  onAjout,
  onAdmission,
}: {
  entree: EntreeRegistreMcp;
  dejaAuPool: boolean;
  ouverte: boolean;
  basculer: () => void;
  onAjout: (integration: IntegrationPoolMcp) => void;
  onAdmission: () => void;
}) {
  // ⚠ `curee` et non `source` : c'est le champ du garde-fou (voir l'en-tête).
  const montable = entree.curee;
  const admise = entree.source === "admise";
  const depreciee = entree.statut === MCP_STATUT_DEPRECIE;
  return (
    <li
      className={`${CLASSE_LIGNE} ${
        montable ? CLASSE_LIGNE_ALLOWLIST : CLASSE_LIGNE_DECOUVERTE
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-corps font-medium">{entree.nom}</span>
        <BadgeEtat ton="info">{libelleMode(entree.mode_auth)}</BadgeEtat>
        {/*
          Le badge de source ne se pose que sur ce qui n'est pas le cas nominal.
          Une curée n'en porte pas : le pied de section dit déjà d'où vient la
          liste, et un badge sur toutes les lignes de l'allowlist ne
          distinguerait plus rien.
        */}
        {!montable && (
          <BadgeEtat ton="accent" contour>
            Découverte
          </BadgeEtat>
        )}
        {admise && (
          <BadgeEtat ton="positif" contour>
            Admise
          </BadgeEtat>
        )}
        {depreciee && (
          <BadgeEtat ton="attention" icone={IconeAlerte}>
            Dépréciée
          </BadgeEtat>
        )}
        {dejaAuPool && <BadgeEtat ton="positif">au pool</BadgeEtat>}
        <Bouton
          variante="contour"
          ton="neutre"
          taille="petite"
          onClick={basculer}
          aria-expanded={ouverte}
          className={`ml-auto ${CIBLE_MINIMALE}`}
        >
          {ouverte
            ? "Fermer"
            : !montable
              ? "Examiner"
              : dejaAuPool
                ? "Reconfigurer"
                : "Configurer"}
        </Bouton>
      </div>
      <p className="mt-1 text-annexe text-neutral-500 dark:text-neutral-400">
        {entree.editeur && (
          <span className="text-neutral-600 dark:text-neutral-300">
            {entree.editeur} —{" "}
          </span>
        )}
        {entree.description}
      </p>
      {/*
        Les écarts que l'amont a signalés depuis l'admission (#678, critère 3 du
        parent) sont rendus **sans qu'on déplie** : « rien ne disparaît en
        silence » ne tient pas si le signal attend un clic. Ils ne retirent rien
        — l'entrée reste montable telle qu'elle a été admise —, ils appellent une
        décision humaine.
      */}
      {entree.signaux.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1" aria-label="Signaux d'amont">
          {entree.signaux.map((signal) => (
            <li
              key={signal.genre}
              className="flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-annexe text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200"
            >
              <IconeAlerte aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
              <span>{signal.message}</span>
            </li>
          ))}
        </ul>
      )}
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
      {ouverte &&
        (montable ? (
          <FormulaireConfiguration
            entree={entree}
            dejaAuPool={dejaAuPool}
            onAjout={onAjout}
          />
        ) : (
          <PanneauDecouverte entree={entree} onAdmission={onAdmission} />
        ))}
    </li>
  );
}

/* ------------------------------------------------------------------ *
 * Le panneau d'une découverte
 * ------------------------------------------------------------------ */

/**
 * Ce qu'on sait d'une entrée que **personne n'a approuvée**, et le geste qui
 * l'approuverait (#679, critères 1 et 2).
 *
 * Il n'y a pas de formulaire de configuration ici, et ce n'est pas un manque :
 * `POST /api/mcp/pool` refuserait l'entrée (garde-fou supply-chain, docs/19).
 * Un écran qui proposerait la saisie d'un token pour une entrée non montable
 * ferait remplir un formulaire dont il connaît d'avance le refus.
 *
 * Ce qui le remplace est ce que le registre officiel apporte de plus qu'une
 * liste de noms : les **signaux de confiance**. Ils ne disent pas que le
 * serveur est sûr — c'est la phrase qui suit qui le nomme —, ils disent ce
 * qu'on peut vérifier avant de décider.
 */
function PanneauDecouverte({
  entree,
  onAdmission,
}: {
  entree: EntreeRegistreMcp;
  onAdmission: () => void;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const depreciee = entree.statut === MCP_STATUT_DEPRECIE;

  const admettre = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      await admettreEntreeMcp({ registre_id: entree.id });
      onAdmission();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
      <SignauxConfiance entree={entree} />
      {/*
        La phrase du critère 2, et elle tient en deux moitiés qui ne se
        remplacent pas : ce que le registre **garantit** (la propriété du
        namespace, vérifiée par la forge de l'éditeur) et ce qu'il ne garantit
        **pas** (que le serveur soit sûr). C'est toute la raison d'être de la
        porte d'admission : le registre répond à « ce serveur existe », nous
        seuls répondons à « nous l'autorisons ».
      */}
      <p className="text-annexe text-neutral-600 dark:text-neutral-300">
        Le registre officiel garantit que l&apos;éditeur possède le namespace{" "}
        <code className="font-mono">{entree.editeur || "déclaré"}</code> —{" "}
        {preuveNamespace(entree.editeur)}. Il ne garantit <strong>pas</strong>{" "}
        que ce serveur est sûr : personne en amont ne l&apos;a audité. L&apos;
        admettre, c&apos;est ce projet qui l&apos;autorise.
      </p>
      {depreciee && (
        <p className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-annexe text-amber-900 dark:border-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
          L&apos;éditeur a marqué cette entrée <strong>dépréciée</strong> chez
          l&apos;amont : elle reste publiée et admissible, mais elle n&apos;est
          plus maintenue. Chercher son remplaçant avant de l&apos;admettre.
        </p>
      )}
      <div className="flex flex-wrap items-center gap-3">
        {/*
          `occupe` et non `disabled` : le bouton est inerte le temps de l'appel
          et le dit (`aria-busy`), là où un simple `disabled` annoncerait qu'il
          n'y a rien à faire (voir la primitive).
        */}
        <Bouton ton="accent" occupe={enCours} onClick={() => void admettre()}>
          {enCours ? "Admission…" : "Admettre dans l'allowlist"}
        </Bouton>
        <span className="text-annexe text-neutral-500 dark:text-neutral-400">
          Geste tracé et révocable : la version ci-dessus est <em>figée</em> —
          une version amont plus récente ne la remplacera pas toute seule.
        </span>
      </div>
      {erreur && (
        <p className="text-annexe text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
    </div>
  );
}

/**
 * Les signaux de confiance que **seul l'amont** fournit (#679, critère 2) :
 * l'éditeur et son namespace, le nom complet au registre, le dépôt, la version
 * épinglée, la date de publication et le statut.
 *
 * ⚠ Un signal absent est **nommé**, jamais omis : une ligne qui disparaît se
 * lit comme une ligne qu'on n'avait pas à montrer, alors qu'un dépôt non
 * déclaré est précisément ce qu'il faut savoir avant d'admettre. C'est la règle
 * « l'absence est muette, l'inconnu est nommé » appliquée dans le sens où elle
 * porte : ici, l'inconnu est le sujet.
 */
function SignauxConfiance({ entree }: { entree: EntreeRegistreMcp }) {
  const nomAmont = entree.editeur
    ? `${entree.editeur}/${entree.nom}`
    : entree.nom;
  return (
    <dl className="grid grid-cols-1 gap-x-4 gap-y-1 text-annexe @sm:grid-cols-[10rem_1fr]">
      <Signal libelle="Nom au registre">
        <code className="font-mono">{nomAmont}</code>
      </Signal>
      <Signal libelle="Éditeur (namespace)">
        {entree.editeur ? (
          <>
            <code className="font-mono">{entree.editeur}</code> —{" "}
            {preuveNamespace(entree.editeur)}
          </>
        ) : (
          <Absent>aucun namespace déclaré</Absent>
        )}
      </Signal>
      <Signal libelle="Dépôt">
        {entree.depot ? (
          <a
            href={entree.depot}
            target="_blank"
            rel="noreferrer"
            className="font-mono underline hover:no-underline"
          >
            {entree.depot}
          </a>
        ) : (
          <Absent>aucun dépôt déclaré — le code n&apos;est pas lisible</Absent>
        )}
      </Signal>
      <Signal libelle="Version épinglée">
        {entree.version ? (
          <code className="font-mono">{entree.version}</code>
        ) : (
          <Absent>aucune version déclarée</Absent>
        )}
      </Signal>
      <Signal libelle="Publiée le">
        {entree.publie_le ? (
          <time dateTime={entree.publie_le}>{formatJour(entree.publie_le)}</time>
        ) : (
          <Absent>date de publication inconnue</Absent>
        )}
      </Signal>
      <Signal libelle="Statut amont">
        {entree.statut === MCP_STATUT_DEPRECIE
          ? "dépréciée par son éditeur"
          : entree.statut
            ? entree.statut
            : "active"}
      </Signal>
    </dl>
  );
}

/** Une paire libellé/valeur de la fiche de confiance. */
function Signal({
  libelle,
  children,
}: {
  libelle: string;
  children: ReactNode;
}) {
  return (
    <>
      <dt className="font-medium text-neutral-600 dark:text-neutral-400">
        {libelle}
      </dt>
      <dd className="text-neutral-700 dark:text-neutral-300">{children}</dd>
    </>
  );
}

/** Un signal que l'amont n'a pas déclaré — dit, jamais tu. */
function Absent({ children }: { children: React.ReactNode }) {
  return (
    <span className="italic text-neutral-500 dark:text-neutral-400">
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Le formulaire d'une entrée de l'allowlist
 * ------------------------------------------------------------------ */

/**
 * Le formulaire de configuration d'une entrée, adapté à son mode d'auth : un
 * champ par variable requise (secret masqué, appairage en clair), une échéance
 * pour un token OAuth importé, et un lien vers la procédure d'émission côté
 * outil. « Ajouter au pool » pose l'intégration et ses secrets, une seule fois.
 *
 * Il ne se monte que pour une entrée de l'**allowlist** — curée ou admise
 * (#679) : c'est la condition que `POST /api/mcp/pool` vérifie de son côté, et
 * l'écran ne propose pas ce que le garde-fou refuse.
 */
function FormulaireConfiguration({
  entree,
  dejaAuPool,
  onAjout,
}: {
  entree: EntreeRegistreMcp;
  dejaAuPool: boolean;
  onAjout: (integration: IntegrationPoolMcp) => void;
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
      onAjout(await ajouterIntegrationPoolMcp({ registre_id: entree.id, secrets }));
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
      {/*
        Une admise vient de l'amont : ses signaux de confiance restent lisibles
        après l'admission, au-dessus du formulaire. Les retirer une fois la
        porte franchie reviendrait à ne les montrer qu'au moment où l'on n'a pas
        encore décidé, et jamais quand on reconfigure.
      */}
      {entree.source === "admise" && (
        <div className="flex flex-col gap-2 rounded-md bg-neutral-50 px-3 py-2 dark:bg-neutral-950">
          <SignauxConfiance entree={entree} />
          {entree.admission && (
            <p className="text-annexe text-neutral-500 dark:text-neutral-400">
              Admise le {formatJour(entree.admission.le)}
              {entree.admission.par ? ` par ${entree.admission.par}` : ""}
              {entree.admission.note ? ` — ${entree.admission.note}` : ""}.
            </p>
          )}
        </div>
      )}
      {entree.secrets.length === 0 ? (
        <p className="text-annexe text-neutral-500 dark:text-neutral-400">
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
        <p className="text-annexe text-neutral-500 dark:text-neutral-400">
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
        <span className="text-annexe text-neutral-500 dark:text-neutral-400">
          {entree.mode_auth === MCP_MODE_APPAIRAGE
            ? "Valeur d'appairage jetable, à renouveler à chaque session."
            : entree.mode_auth === MCP_MODE_SANS_SECRET
              ? "Aucun secret : rien n'est stocké pour cette intégration."
              : "Secret saisi une seule fois, chiffré côté serveur."}
        </span>
      </div>
      {erreur && (
        <p className="text-annexe text-rose-600 dark:text-rose-400" role="alert">
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
