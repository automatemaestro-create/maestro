"use client";

/**
 * L'onglet **MCP & permissions** d'une fiche agent : ce que l'agent peut
 * appeler — les intégrations MCP qu'on lui active, et la politique d'outils que
 * le moteur applique à l'exécution.
 *
 * Il vient d'`EditeurAgent.tsx` (#190), où il tenait en deux sections de
 * lecture-presque-seule. #263 en a fait une surface à part entière, et c'est
 * pourquoi il a son fichier : l'onglet n'est plus une vue de la fiche, c'est
 * **l'endroit où les intégrations d'un agent se règlent**, bibliothèque et
 * migration comprises. `EditeurAgent` redevient le formulaire de définition.
 *
 * ## Ce que #263 a changé, et pourquoi
 *
 * Trois manques, tous du même genre : l'écran montrait un état sans donner le
 * geste qui le change.
 *
 * 1. **Actives et disponibles étaient mêlées.** Le pool s'affichait en une liste
 *    plate d'interrupteurs, si bien que « de quoi cet agent dispose-t-il ? » —
 *    la question qu'on vient poser sur *sa* fiche — se lisait en dépliant les
 *    interrupteurs un par un. Les deux groupes sont désormais séparés, actives
 *    en tête ;
 * 2. **ajouter une intégration absente du pool obligeait à sortir** vers l'écran
 *    Intégrations, à y chercher, configurer, puis à revenir activer ici. La
 *    bibliothèque est montée sur place — la **même**, jamais une seconde
 *    (voir plus bas) — et ce qu'on y ajoute est **activé dans la foulée** : on
 *    est venu équiper cet agent, pas garnir un pool ;
 * 3. **les déclarations héritées étaient un cul-de-sac** : un bloc « lecture
 *    seule (à migrer vers le pool) » qu'aucun écran ne proposait de migrer.
 *    Elles se migrent maintenant en un geste (`POST /api/mcp/migration/{agent}`).
 *
 * ## Trois choix à ne pas défaire
 *
 * ⚠ **La bibliothèque est celle de l'écran Intégrations, importée telle quelle.**
 * En recopier une version « allégée » ici rejouerait #231 (le `<form>` qui borne
 * la détection du gestionnaire de mots de passe, le panneau oublié quand son
 * entrée quitte les résultats) et ferait deux vérités sur ce qui est montable.
 * Elle est repliée derrière un bouton parce qu'on n'arrive pas sur cet onglet
 * pour chercher une intégration — on y arrive pour voir ce que l'agent a.
 *
 * ⚠ **Retirer ≠ retirer du pool, et l'écran le dit en toutes lettres.**
 * L'interrupteur écrit `PUT /api/mcp/activations/{agent}` : il désactive pour
 * **cet agent seul**, l'intégration reste au pool avec son secret, prête pour un
 * autre. Le retrait du projet (`DELETE /api/mcp/pool/{id}`, qui désactive
 * partout et purge les secrets) reste sur l'écran Intégrations, et c'est
 * délibéré : un geste dont la portée dépasse la page où on le fait ne se pose
 * pas sur cette page.
 *
 * ⚠ **Aucune seconde porte sur les secrets.** Le secret d'une intégration se
 * saisit **une fois** (formulaire de la bibliothèque, chiffré côté serveur) et
 * n'est jamais réémis. Cet onglet en montre l'**état** — « secret à configurer »
 * — et renvoie vers l'écran qui le pose ; il n'en propose jamais la ressaisie.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { IconeMcp, IconePermissions } from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import { BibliothequeMcp } from "@/components/integrations/BibliothequeMcp";
import { Interrupteur } from "@/components/parametres/SectionParametres";
import {
  chargerAgentCatalogue,
  definirActivationsMcp,
  migrerDeclarationsMcp,
} from "@/lib/api";
import type {
  AgentCatalogueDetail,
  IntegrationPoolMcp,
  MigrationMcp,
  ServeurMcp,
} from "@/lib/types";

/** L'écran d'où une intégration s'ajoute au projet, se reconfigure et se retire. */
const CHEMIN_INTEGRATIONS = "/integrations";

/**
 * L'onglet : charge la fiche, la recharge après chaque écriture, et monte les
 * deux sections.
 *
 * Le rechargement passe par une **révision** qui sert aussi de `key` à la
 * section des serveurs. Ce n'est pas une astuce : la section tient l'ensemble
 * activé en état local (pour que l'interrupteur réponde sans attendre le
 * réseau), et une fiche relue sous le même montage laisserait cet état local
 * faire foi contre elle. Remonter la section est ce qui garantit qu'après une
 * écriture, c'est le serveur qui a le dernier mot.
 */
export function McpEtPermissionsAgent({ nom }: { nom: string }) {
  const [fiche, setFiche] = useState<AgentCatalogueDetail | null>(null);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [revision, setRevision] = useState(0);
  /**
   * Ce que la dernière migration a fait — tenu **ici**, au-dessus de la `key`.
   *
   * Il a d'abord vécu dans le bloc des héritées, qui est le seul endroit
   * logique… et le seul endroit où il ne pouvait pas être lu : une migration
   * réussie recharge la fiche, donc remonte la section, donc efface l'état local
   * du bloc — et comme la fiche rechargée n'a plus de déclaration héritée, le
   * bloc disparaît avec. On cliquait, tout s'évanouissait, et rien ne disait ce
   * qui venait de se passer. Le compte rendu d'un geste ne peut pas vivre dans
   * ce que le geste supprime.
   */
  const [migration, setMigration] = useState<MigrationMcp | null>(null);

  const recharger = useCallback(async () => {
    setFiche(await chargerAgentCatalogue(nom));
    setRevision((n) => n + 1);
  }, [nom]);

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
      <SectionIntegrations
        key={revision}
        fiche={fiche}
        recharger={() => recharger()}
        migration={migration}
        onMigration={setMigration}
      />
      <SectionPermissions fiche={fiche} />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * Les intégrations de l'agent
 * ------------------------------------------------------------------ */

/**
 * Les intégrations MCP de l'agent (#133, #263), en quatre temps qui suivent la
 * question qu'on vient poser : **ce dont il dispose** (actives), **ce qu'on peut
 * lui donner** (le pool non activé), **ce qui n'existe pas encore** (la
 * bibliothèque) et **ce qui reste à migrer** (les déclarations héritées).
 *
 * L'ordre est le contenu de la section : l'écran d'avant listait le pool entier
 * à plat, ce qui répondait d'abord à « qu'y a-t-il au projet ? » sur la fiche
 * d'un agent — la question de l'écran d'à côté.
 */
function SectionIntegrations({
  fiche,
  recharger,
  migration,
  onMigration,
}: {
  fiche: AgentCatalogueDetail;
  recharger: () => Promise<void>;
  /** Ce que la dernière migration a fait — porté par le parent (voir sa docstring). */
  migration: MigrationMcp | null;
  onMigration: (migration: MigrationMcp) => void;
}) {
  const [activations, setActivations] = useState<string[]>(
    fiche.mcp_activations,
  );
  const [enCours, setEnCours] = useState<string | null>(null);
  const [erreur, setErreur] = useState<string | null>(null);
  const [bibliothequeOuverte, setBibliothequeOuverte] = useState(false);

  /** Écrit l'ensemble activé de l'agent (remplacement intégral côté API). */
  const ecrire = async (cible: string[], marqueur: string) => {
    setEnCours(marqueur);
    setErreur(null);
    try {
      await definirActivationsMcp(fiche.nom, cible);
      setActivations(cible);
      return true;
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setEnCours(null);
    }
  };

  const basculer = (id: string) =>
    void ecrire(
      activations.includes(id)
        ? activations.filter((a) => a !== id)
        : [...activations, id],
      id,
    );

  /**
   * Ajout depuis la fiche : l'intégration entre au pool **puis est activée**,
   * sans second geste — c'est le critère 2 du ticket, et la raison d'être de la
   * bibliothèque ici. Les deux moitiés sont distinctes côté API (`POST
   * /api/mcp/pool` puis `PUT /api/mcp/activations`), donc l'échec de la seconde
   * doit se dire pour ce qu'il est : l'intégration existe, elle n'est pas
   * montée. Taire l'écart laisserait chercher au pool une intégration qu'on
   * croit activée.
   */
  const apresAjout = async (integration: IntegrationPoolMcp) => {
    setBibliothequeOuverte(false);
    const cible = activations.includes(integration.id)
      ? activations
      : [...activations, integration.id];
    const active = await ecrire(cible, integration.id);
    if (!active) {
      setErreur(
        `« ${integration.serveur.nom} » est bien au pool projet, mais son ` +
          `activation pour ${fiche.nom} a échoué — l'interrupteur ci-dessus la ` +
          "rejouera.",
      );
    }
    await recharger();
  };

  const parId = new Map(fiche.mcp_pool.map((i) => [i.id, i]));
  // L'ordre des actives suit celui des activations (celui dans lequel elles ont
  // été posées), pas celui du pool : c'est la liste de l'agent.
  const actives = activations
    .map((id) => parId.get(id))
    .filter((i): i is IntegrationPoolMcp => i !== undefined);
  const disponibles = fiche.mcp_pool.filter((i) => !activations.includes(i.id));

  return (
    <section
      aria-label={`Intégrations MCP de ${fiche.nom}`}
      className="flex flex-col gap-3"
    >
      <EnTeteSection
        niveau={3}
        titre="Intégrations MCP"
        icone={IconeMcp}
        aside={
          <span className="text-annexe text-neutral-500 dark:text-neutral-400">
            {actives.length} active{actives.length > 1 ? "s" : ""} sur{" "}
            {fiche.mcp_pool.length} au pool projet
          </span>
        }
      />
      {fiche.mcp_pool_erreur !== null && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-annexe text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Pool invalide : {fiche.mcp_pool_erreur}
        </p>
      )}
      {migration !== null && (
        // En tête de section, et non dans le bloc des héritées : celui-ci vient
        // de disparaître (son fichier est parti), et ce compte rendu parle des
        // listes ci-dessous, où les intégrations migrées viennent d'arriver.
        <p
          role="status"
          className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-annexe text-emerald-800 dark:border-emerald-900 dark:bg-emerald-950 dark:text-emerald-300"
        >
          {resumeMigration(migration)}
        </p>
      )}

      <BlocIntegrations titre="Actives sur cet agent" compte={actives.length}>
        {actives.length === 0 ? (
          <EtatVide
            icone={IconeMcp}
            message={`${fiche.nom} n'a aucune intégration MCP active.`}
            releve={
              fiche.mcp_pool.length > 0
                ? "Activez-en une ci-dessous : le secret est déjà posé au pool, il n'y a rien à ressaisir."
                : "Ajoutez-en une depuis la bibliothèque, plus bas — elle sera activée dans la foulée."
            }
          />
        ) : (
          <ul className="flex flex-col gap-2">
            {actives.map((integration) => (
              <LigneActivation
                key={integration.id}
                integration={integration}
                actif
                enCours={enCours === integration.id}
                basculer={() => basculer(integration.id)}
              />
            ))}
          </ul>
        )}
        {/*
          Le critère 2, dit là où le geste se fait — pas dans une aide générale.
          C'est la question qu'un interrupteur d'activation pose forcément :
          « si je l'éteins, est-ce que je perds la configuration ? »
        */}
        <p className="text-annexe text-neutral-500 dark:text-neutral-400">
          Éteindre un interrupteur <strong>désactive pour cet agent seul</strong>{" "}
          : l&apos;intégration reste au pool projet, secret compris, prête pour un
          autre agent. La retirer <em>du projet</em> — ce qui la désactive partout
          et purge ses secrets — se fait depuis l&apos;écran{" "}
          <Link
            href={CHEMIN_INTEGRATIONS}
            className="font-medium text-sky-700 underline hover:no-underline dark:text-sky-400"
          >
            Intégrations
          </Link>
          .
        </p>
      </BlocIntegrations>

      {disponibles.length > 0 && (
        <BlocIntegrations
          titre="Au pool projet, non activées"
          compte={disponibles.length}
        >
          <ul className="flex flex-col gap-2">
            {disponibles.map((integration) => (
              <LigneActivation
                key={integration.id}
                integration={integration}
                actif={false}
                enCours={enCours === integration.id}
                basculer={() => basculer(integration.id)}
              />
            ))}
          </ul>
        </BlocIntegrations>
      )}

      {erreur !== null && (
        <p className="text-annexe text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}

      <BlocIntegrations titre="Ajouter une intégration">
        <div className="flex flex-wrap items-center gap-3">
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            icone={IconeMcp}
            aria-expanded={bibliothequeOuverte}
            onClick={() => setBibliothequeOuverte((o) => !o)}
            className={CIBLE_MINIMALE}
          >
            {bibliothequeOuverte
              ? "Fermer la bibliothèque"
              : "Chercher dans la bibliothèque…"}
          </Bouton>
          <span className="text-annexe text-neutral-500 dark:text-neutral-400">
            Ce qu&apos;on y ajoute rejoint le pool projet{" "}
            <strong>et s&apos;active pour {fiche.nom}</strong> dans la foulée.
          </span>
        </div>
        {bibliothequeOuverte && (
          <BibliothequeMcp
            idsPool={new Set(fiche.mcp_pool.map((i) => i.id))}
            onAjout={(integration) => void apresAjout(integration)}
          />
        )}
      </BlocIntegrations>

      {fiche.mcp_erreur !== null && (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-annexe text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Déclaration invalide : {fiche.mcp_erreur}
        </p>
      )}
      {fiche.mcp_herites.length > 0 && (
        <BlocHerites
          fiche={fiche}
          recharger={recharger}
          onMigration={onMigration}
        />
      )}
    </section>
  );
}

/**
 * Une sous-partie de la section, avec son titre et son compte.
 *
 * Une `<div>` et un `<h4>`, jamais une `<section>` de plus : la règle des trois
 * places compte les blocs de **premier niveau** (docs/30 §4,
 * `tests/sobriete.test.tsx`), et découper une section en quatre sous-sections
 * nommées reviendrait à quadrupler ce qu'un écran occupe sans rien ajouter à ce
 * qu'il dit. Le titre porte le compte parce que c'est lui qu'on cherche des
 * yeux — combien cet agent en a, combien il pourrait en avoir.
 */
function BlocIntegrations({
  titre,
  compte,
  children,
}: {
  titre: string;
  compte?: number;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2">
      <h4 className="flex items-center gap-2 text-annexe font-semibold tracking-wide text-neutral-500 uppercase dark:text-neutral-400">
        {titre}
        {compte !== undefined && (
          <BadgeEtat ton="neutre">{compte}</BadgeEtat>
        )}
      </h4>
      {children}
    </div>
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
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-neutral-200 bg-white px-3 py-2 text-annexe dark:border-neutral-800 dark:bg-neutral-900">
      <Interrupteur
        libelle={`Activer ${integration.serveur.nom} pour cet agent`}
        actif={actif}
        desactive={enCours}
        basculer={basculer}
      />
      <span className="font-medium">{integration.serveur.nom}</span>
      <BadgeEtat ton="neutre" className="font-mono">
        {integration.serveur.type}
      </BadgeEtat>
      {/*
        L'id ne s'affiche que s'il **dit quelque chose de plus** que le nom du
        serveur — même règle que la ligne du pool (#270). Il le dit précisément
        pour une intégration migrée depuis un fichier hérité, dont le nom de
        montage (`forge`) n'est pas celui de son entrée de bibliothèque.
      */}
      {integration.id !== integration.serveur.nom && (
        <BadgeEtat ton="neutre" className="font-mono">
          {integration.id}
        </BadgeEtat>
      )}
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

/* ------------------------------------------------------------------ *
 * Les déclarations héritées
 * ------------------------------------------------------------------ */

/**
 * Ce que le fichier `core/mcp/<agent>.json` porte encore, et **l'issue** (#263).
 *
 * Le bloc existait depuis #133, sous le titre « lecture seule (à migrer vers le
 * pool) » — une consigne adressée à personne : aucun écran ne proposait la
 * migration, et le seul outil qui l'aurait faite (`McpStore.migrer`) n'avait
 * aucun appelant. Le critère 3 du ticket est là : *soit migrable en un geste,
 * soit expliqué* — jamais un état sans porte de sortie.
 *
 * Deux choses que le bloc doit dire avant qu'on clique, parce qu'elles sont
 * contre-intuitives :
 *
 * - **rien à ressaisir.** Une déclaration héritée porte déjà ses références
 *   `${VAR}`, qui se résolvent au montage exactement comme avant. Migrer déplace
 *   une déclaration, ça ne redemande pas un secret ;
 * - **le fichier part.** C'est le contenu du geste, pas un effet de bord : tant
 *   qu'il est là, l'héritée reste autoritaire à la lecture (`McpStore.lire`),
 *   donc le serveur monté resterait celui du fichier et ce bloc ne
 *   disparaîtrait pas. Une migration qui laisserait le fichier serait invisible.
 */
function BlocHerites({
  fiche,
  recharger,
  onMigration,
}: {
  fiche: AgentCatalogueDetail;
  recharger: () => Promise<void>;
  /**
   * Ce que la migration a fait remonte au parent, qui survit au rechargement —
   * le garder ici reviendrait à l'écrire dans le bloc que la migration efface.
   */
  onMigration: (migration: MigrationMcp) => void;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  const migrer = async () => {
    setEnCours(true);
    setErreur(null);
    try {
      const faite = await migrerDeclarationsMcp(fiche.nom);
      onMigration(faite);
      await recharger();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  return (
    <BlocIntegrations
      titre="Héritées d'un fichier"
      compte={fiche.mcp_herites.length}
    >
      <p className="text-annexe text-neutral-500 dark:text-neutral-400">
        Déclarées dans{" "}
        <code className="font-mono">core/mcp/{fiche.nom}.json</code>, la forme
        d&apos;avant le pool projet : montées pour {fiche.nom} et pour lui seul,
        non partageables, et non réglables ici. Les migrer les inscrit au{" "}
        <strong>pool</strong>, activées pour cet agent — elles rejoignent alors
        les listes ci-dessus, avec interrupteur. <strong>Aucun secret à
        ressaisir</strong> : la déclaration garde ses références{" "}
        <code className="font-mono">{"${VAR}"}</code>, résolues au montage comme
        aujourd&apos;hui. Le fichier est retiré au passage — tant qu&apos;il est
        là, c&apos;est lui qui fait foi.
      </p>
      <ul className="flex flex-col gap-2">
        {fiche.mcp_herites.map((serveur) => (
          <LigneHeritee key={serveur.nom} serveur={serveur} />
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <Bouton
          variante="contour"
          ton="neutre"
          taille="petite"
          occupe={enCours}
          onClick={() => void migrer()}
          className={CIBLE_MINIMALE}
        >
          {enCours ? "Migration…" : "Migrer vers le pool projet"}
        </Bouton>
      </div>
      {erreur !== null && (
        <p className="text-annexe text-rose-600 dark:text-rose-400" role="alert">
          Migration refusée : {erreur}
        </p>
      )}
    </BlocIntegrations>
  );
}

/**
 * Ce qu'une migration a réellement fait, en une phrase (#263) — et non « c'est
 * fait » : elle **crée** ou **retrouve** selon que le pool portait déjà la même
 * déclaration, et les confondre ferait annoncer une intégration de plus au
 * projet là où deux agents viennent d'en partager une.
 */
function resumeMigration(migration: MigrationMcp): string {
  const morceaux: string[] = [];
  if (migration.ajoutees.length > 0) {
    morceaux.push(
      `${migration.ajoutees.length} intégration${
        migration.ajoutees.length > 1 ? "s" : ""
      } ajoutée${migration.ajoutees.length > 1 ? "s" : ""} au pool projet`,
    );
  }
  if (migration.reprises.length > 0) {
    morceaux.push(
      `${migration.reprises.length} déjà au pool, partagée${
        migration.reprises.length > 1 ? "s" : ""
      } plutôt que dupliquée${migration.reprises.length > 1 ? "s" : ""}`,
    );
  }
  const fichier = migration.fichier_retire
    ? ` Le fichier core/mcp/${migration.agent}.json a été retiré.`
    : "";
  return `Migration faite : ${morceaux.join(", ")}, activée${
    migration.ajoutees.length + migration.reprises.length > 1 ? "s" : ""
  } pour ${migration.agent}.${fichier}`;
}

/** Une déclaration héritée, en lecture seule — ce que la migration ferait passer au pool. */
function LigneHeritee({ serveur }: { serveur: ServeurMcp }) {
  return (
    <li className="rounded-md border border-dashed border-neutral-300 bg-neutral-50 px-3 py-2 text-annexe dark:border-neutral-700 dark:bg-neutral-950">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{serveur.nom}</span>
        <BadgeEtat ton="neutre" className="font-mono">
          {serveur.type}
        </BadgeEtat>
        {serveur.optionnel && (
          <Infobulle
            texte="Serveur omis du montage (sans échec) tant que son secret n'est pas fourni"
            className="inline-flex rounded-full bg-amber-50 px-2 py-0.5 text-amber-700 dark:bg-amber-950 dark:text-amber-300"
          >
            optionnel
          </Infobulle>
        )}
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

/* ------------------------------------------------------------------ *
 * Les permissions
 * ------------------------------------------------------------------ */

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
 * L'absence de politique se dit explicitement : la section était muette quand
 * tout allait bien, ce qui se lisait comme un panneau de plus tout en bas de la
 * fiche du catalogue ; sur son propre onglet (#190), le silence passerait pour
 * un chargement raté.
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
