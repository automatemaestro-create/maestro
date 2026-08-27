"use client";

/**
 * Le **pool projet** de l'écran « Intégrations » (#270, lot 3/6 de #244) : les
 * intégrations configurées, l'état de leurs secrets, et — c'est le troisième
 * critère du ticket — **qui les utilise**.
 *
 * Le bloc vient de `parametres/ParametresMcp.tsx` (#133), dont il était la
 * première moitié. Deux choses ont changé en déménageant, et une seule est
 * cosmétique :
 *
 * 1. les actions passent par les **primitives du socle** (`Bouton`,
 *    `BadgeEtat`) au lieu des `bg-*` recopiés — ce que la section pouvait se
 *    permettre au fond des Paramètres, où le pool vide ne rendait jamais une
 *    seule ligne, et qu'un écran à elle rend visible (docs/30 §3.6) ;
 * 2. chaque intégration nomme **les agents qui l'ont activée**, chacun étant un
 *    lien vers son onglet « MCP & permissions ». Le sens inverse existait déjà
 *    (la fiche d'un agent liste le pool et l'active) ; celui-ci manquait, et
 *    avec lui la réponse à « puis-je retirer cette intégration ? ».
 */

import Link from "next/link";

import { IconeAgent, IconeMcp } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  EnTeteSection,
  EtatVide,
} from "@/components/Primitives";
import { cheminOnglet } from "@/lib/agents";
import { formatDateHeure } from "@/lib/format";
import { entreeParLibelle } from "@/lib/navigation";
import { supprimerIntegrationPoolMcp } from "@/lib/api";
import type {
  AgentCatalogue,
  EtatSecretPool,
  IntegrationPoolMcp,
} from "@/lib/types";
import { useState } from "react";

import { libelleMode } from "./modes";
import type { UsageDuPool } from "./usage";

export function PoolProjet({
  pool,
  erreur,
  chargement,
  usage,
  onChangement,
}: {
  pool: IntegrationPoolMcp[];
  erreur: string | null;
  chargement: boolean;
  usage: UsageDuPool;
  onChangement: () => void;
}) {
  return (
    <section
      aria-label="Pool projet des intégrations MCP"
      className="flex flex-col gap-3"
    >
      <EnTeteSection
        titre="Pool projet"
        icone={IconeMcp}
        aside={
          pool.length > 0 && (
            <span className="text-annexe text-neutral-500 dark:text-neutral-400">
              {pool.length} intégration{pool.length > 1 ? "s" : ""}
            </span>
          )
        }
      />
      {chargement ? (
        <p className="text-corps text-neutral-500 dark:text-neutral-400">
          Chargement des intégrations…
        </p>
      ) : erreur !== null ? (
        <p
          role="alert"
          className="rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-annexe text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-300"
        >
          Pool invalide : {erreur}
        </p>
      ) : pool.length === 0 ? (
        <EtatVide
          icone={IconeMcp}
          message="Aucune intégration configurée pour ce projet."
          releve={
            <>
              Cherchez-en une dans la bibliothèque ci-dessous et ajoutez-la au
              pool — son secret n&apos;est saisi qu&apos;une fois, puis partagé
              par les agents qui l&apos;activent.
            </>
          }
        />
      ) : (
        <ul className="flex flex-col gap-2">
          {pool.map((integration) => (
            <LignePool
              key={integration.id}
              integration={integration}
              agents={usage.parIntegration.get(integration.id) ?? []}
              usageConnu={usage.connu}
              onRetrait={onChangement}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

/** Une intégration du pool : identité, état des secrets, utilisateurs, retrait. */
function LignePool({
  integration,
  agents,
  usageConnu,
  onRetrait,
}: {
  integration: IntegrationPoolMcp;
  agents: AgentCatalogue[];
  usageConnu: boolean;
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
    <li className="rounded-lg border border-neutral-200 bg-white px-3 py-2.5 text-corps dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{integration.serveur.nom}</span>
        {/*
          L'id n'est montré que s'il **dit quelque chose de plus** que le nom du
          serveur. Le pool nomme le plus souvent le serveur d'après l'entrée du
          registre qui l'a instancié (`figma-officiel`, `gitlab`…), si bien que
          la ligne affichait deux fois la même chaîne, dont une en chasse fixe :
          du bruit dans le cas nominal, pour une information utile dans le seul
          cas où l'intégration a été renommée à l'ajout.
        */}
        {integration.id !== integration.serveur.nom && (
          <BadgeEtat ton="neutre" className="font-mono">
            {integration.id}
          </BadgeEtat>
        )}
        {integration.mode_auth && (
          <BadgeEtat ton="info">{libelleMode(integration.mode_auth)}</BadgeEtat>
        )}
        <Bouton
          variante="contour"
          ton="alerte"
          taille="petite"
          occupe={enCours}
          onClick={() => void retirer()}
          // Le plancher de 24 px n'est pas dans `taille="petite"` (px-2.5 py-1) :
          // il se pose ici, comme partout où une action de ligne s'écrit en
          // petit corps (WCAG 2.2 §2.5.8, `a11y.test.tsx`).
          className={`ml-auto ${CIBLE_MINIMALE}`}
        >
          {enCours ? "Retrait…" : "Retirer"}
        </Bouton>
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
      <UtiliseePar agents={agents} usageConnu={usageConnu} />
      {erreur && (
        <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400" role="alert">
          {erreur}
        </p>
      )}
    </li>
  );
}

/**
 * Les agents qui ont activé cette intégration, chacun ouvrant sa fiche à
 * l'onglet où l'activation se défait — pas la fiche nue : on y va pour agir.
 *
 * Trois états et non deux, parce que le catalogue peut ne pas répondre : « on
 * ne sait pas » ne s'écrit jamais « personne » (voir `usage.ts`).
 */
function UtiliseePar({
  agents,
  usageConnu,
}: {
  agents: AgentCatalogue[];
  usageConnu: boolean;
}) {
  if (!usageConnu) {
    return (
      <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
        Catalogue d&apos;agents illisible — impossible de dire qui utilise cette
        intégration.
      </p>
    );
  }
  if (agents.length === 0) {
    // Résolu par le menu et jamais écrit en dur : le jour où « Agents » déménage,
    // ce renvoi suit, et il ne s'allume pas si la page n'existe plus.
    const agentsHref = entreeParLibelle("Agents")?.href;
    return (
      <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
        Aucun agent ne l&apos;a activée — elle est configurée mais montée nulle
        part.{" "}
        {agentsHref && (
          <Link
            href={agentsHref}
            className={`${CIBLE_MINIMALE} inline-flex items-center font-medium text-sky-700 hover:underline dark:text-sky-400`}
          >
            Activer sur un agent
          </Link>
        )}
      </p>
    );
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
      <span className="text-annexe text-neutral-500 dark:text-neutral-400">
        Utilisée par
      </span>
      {agents.map((agent) => (
        <Link
          key={agent.nom}
          href={cheminOnglet(agent.nom, "mcp")}
          // Le nom de l'agent, et non « voir la fiche » : c'est lui qu'on
          // cherche des yeux, et un lecteur d'écran qui liste les liens de la
          // page doit pouvoir les distinguer.
          title={`${agent.role} — MCP & permissions`}
          className={`${CIBLE_MINIMALE} inline-flex items-center gap-1 rounded-full border border-neutral-300 px-2 text-annexe font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800`}
        >
          <IconeAgent className="size-3.5 shrink-0" />
          {agent.nom}
        </Link>
      ))}
    </div>
  );
}

/** L'état d'un secret d'une intégration : configuré, à configurer, ou expiré. */
function EtatSecretPuce({ secret }: { secret: EtatSecretPool }) {
  const [ton, texte] = !secret.present
    ? (["attention", "à configurer"] as const)
    : !secret.valide
      ? ([
          "alerte",
          secret.expire_le
            ? `expiré le ${formatDateHeure(secret.expire_le)}`
            : "expiré",
        ] as const)
      : secret.ephemere
        ? (["neutre", "appairage (jetable)"] as const)
        : ([
            "positif",
            secret.expire_le
              ? `valide jusqu'au ${formatDateHeure(secret.expire_le)}`
              : "configuré",
          ] as const);
  return (
    <span className="inline-flex items-baseline gap-1 text-annexe">
      <code className="font-mono text-neutral-500 dark:text-neutral-400">
        {secret.cle}
      </code>
      <BadgeEtat ton={ton}>{texte}</BadgeEtat>
    </span>
  );
}
