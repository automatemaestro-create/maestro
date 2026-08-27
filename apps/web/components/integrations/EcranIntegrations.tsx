"use client";

/**
 * L'écran « Intégrations » (#270, lot 3/6 de #244) : le pool projet et la
 * bibliothèque curée, en pleine page.
 *
 * **Pourquoi un écran et non une section des Paramètres.** Les deux blocs
 * vivaient au fond de `Paramètres → Intégrations MCP` (#133), empilés dans une
 * colonne de réglages. Or une intégration n'est pas un réglage du poste : c'est
 * ce qui détermine **ce qu'un agent sait faire**, au même titre que son
 * playbook — et le poste, lui, ne la voit jamais. C'est l'argument qui avait
 * déjà sorti « Projets » des Paramètres (docs/05 §2.7.1) : déclarer *où* et
 * *avec quoi* Maestro travaille n'est pas un réglage d'installation.
 *
 * **Les trois places** (docs/30 §4) : un bandeau de tête de **3 chiffres**, un
 * corps de **2 blocs** — le pool, la bibliothèque. Rien en colonne de
 * propriétés : l'écran n'a pas de métadonnée à ranger, et se donner un `<aside>`
 * pour la forme reviendrait à ouvrir la place sans plafond avant d'en avoir
 * besoin.
 *
 * **Deux sources, et une seule est vitale.** Le pool (`GET /api/mcp/pool`) est
 * l'écran ; le catalogue (`GET /api/catalogue`) ne sert qu'à dire **qui utilise
 * quoi**. Un catalogue muet ne doit donc rien blanchir : il retire une
 * information, il n'empêche pas de configurer une intégration. Les deux erreurs
 * sont tenues séparément pour cette raison — voir `usage.ts`, où « je ne sais
 * pas » ne s'écrit jamais « personne ».
 *
 * **Les blocs sont montés d'emblée**, chargement compris, au lieu d'être
 * remplacés par un « Chargement… » nu : la structure de l'écran ne dépend pas
 * de l'état du réseau, ce qui évite le saut de mise en page et donne aux sondes
 * de #537/#539 un écran à auditer plutôt qu'un écran vide.
 */

import { useCallback, useEffect, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeAgent, IconeAlerte, IconeMcp } from "@/components/Icones";
import { TuileChiffre } from "@/components/Primitives";
import { chargerCatalogue, chargerPoolMcp } from "@/lib/api";
import { entreeParLibelle } from "@/lib/navigation";
import type { IntegrationPoolMcp } from "@/lib/types";

import { BibliothequeMcp } from "./BibliothequeMcp";
import { PoolProjet } from "./PoolProjet";
import { USAGE_INCONNU, usageDuPool, type UsageDuPool } from "./usage";

/** Une intégration dont un secret manque ou n'est plus valide appelle un geste. */
function secretsARevoir(pool: IntegrationPoolMcp[]): number {
  return pool.filter((integration) =>
    integration.secrets.some((secret) => !secret.present || !secret.valide),
  ).length;
}

export function EcranIntegrations() {
  const [pool, setPool] = useState<IntegrationPoolMcp[]>([]);
  const [poolErreur, setPoolErreur] = useState<string | null>(null);
  const [usage, setUsage] = useState<UsageDuPool>(USAGE_INCONNU);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);

  const rechargerPool = useCallback(async () => {
    const rendu = await chargerPoolMcp();
    setPool(rendu.integrations);
    setPoolErreur(rendu.erreur);
  }, []);

  // Le catalogue est secondaire : son échec n'est pas celui de l'écran. On
  // retombe sur « usage inconnu », que le pool sait rendre sans mentir.
  const rechargerUsage = useCallback(async () => {
    try {
      setUsage(usageDuPool(await chargerCatalogue()));
    } catch {
      setUsage(USAGE_INCONNU);
    }
  }, []);

  const recharger = useCallback(async () => {
    await Promise.all([rechargerPool(), rechargerUsage()]);
  }, [rechargerPool, rechargerUsage]);

  // Chargement différé d'un tick (même mécanique que les autres écrans) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => {
      void (async () => {
        try {
          await recharger();
          setErreur(null);
        } catch (e) {
          setErreur(e instanceof Error ? e.message : String(e));
        } finally {
          setChargement(false);
        }
      })();
    }, 0);
    return () => clearTimeout(tick);
  }, [recharger]);

  const agents = entreeParLibelle("Agents");
  const aRevoir = secretsARevoir(pool);

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      {/*
        Le bandeau de tête (docs/30 §4.1) : trois chiffres, et rien d'autre dans
        la `<section>` — c'est à cela que `tests/sobriete.test.tsx` le reconnaît,
        et une seule balise étrangère le ferait compter comme un bloc de corps.
        Les points de repli sont ceux du tableau de bord et de `/couts`, au pas
        près : deux bandeaux qui ne se replient pas au même endroit se liraient
        comme deux écrans différents.
      */}
      <section
        aria-label="Vue d'ensemble des intégrations"
        className="grid grid-cols-1 gap-3 @sm:grid-cols-2 @3xl:grid-cols-3"
      >
        <TuileChiffre
          libelle="Au pool projet"
          icone={IconeMcp}
          valeur={chargement ? "—" : pool.length}
          detail="intégrations configurées pour ce projet"
        />
        <TuileChiffre
          libelle="Agents équipés"
          icone={IconeAgent}
          // « — » et non « 0 » quand le catalogue n'a pas répondu : un zéro
          // affirmerait qu'aucun agent n'utilise rien, ce qu'on ne sait pas.
          valeur={usage.connu ? `${usage.agentsEquipes} / ${usage.agents}` : "—"}
          detail="agents ayant activé au moins une intégration"
          renvoi={
            agents ? { href: agents.href, libelle: "Voir les agents" } : undefined
          }
        />
        <TuileChiffre
          libelle="Secrets à revoir"
          icone={IconeAlerte}
          valeur={chargement ? "—" : aRevoir}
          detail={
            aRevoir === 0
              ? "tous les secrets du pool sont valides"
              : "intégrations dont un secret manque ou a expiré"
          }
        />
      </section>
      <PoolProjet
        pool={pool}
        erreur={poolErreur}
        chargement={chargement}
        usage={usage}
        onChangement={() => void recharger()}
      />
      <BibliothequeMcp
        idsPool={new Set(pool.map((integration) => integration.id))}
        onAjout={() => void recharger()}
      />
    </>
  );
}
