/**
 * La répartition du coût par agent (ticket #87) : une barre horizontale par
 * agent, triée par l'API du plus dépensier au moindre. Catégories nominales,
 * série unique : toutes les barres portent la même teinte, la longueur (et
 * l'étiquette directe coût + part) fait le travail. Quand aucun coût n'a été
 * rapporté, la part retombe sur les tokens — le libellé l'annonce.
 *
 * ⚠ **L'orchestration n'est plus une barre de cette liste** (#1028). Elle y
 * figurait comme un agent — 21 % d'un parc que `/agents` n'a jamais listé
 * (constat C13 du retex du 2026-09-11) —, alors qu'elle n'en est pas membre :
 * elle est Maestro ([docs/37 §4.2](../../../docs/37-decision-equipe-sur-mesure.md)).
 * Elle se lit désormais **dans l'en-tête du bloc** (`EtiquetteOrchestration`,
 * posée en `aside` de l'`EnTeteSection` par `/couts`), et la liste ne contient
 * plus que des agents.
 *
 * **Deux composants pour deux places, et c'est la décision** — celle du regard
 * neuf de #1028, sur trois variantes rendues et jugées contre deux produits
 * capturés en direct (veille consignée sur le ticket). Ce qui distingue
 * l'orchestration est sa **place** et son **mot**, jamais un signe graphique :
 * une barre absente (variante A) ou une barre creuse (variante B) se lisent
 * comme un défaut de rendu, là où « dont orchestration … » en tête de carte est
 * la forme que GitHub Actions donne à « Total duration » — un résumé au-dessus
 * de la liste, jamais une ligne dedans. Une teinte propre avait déjà été écartée
 * par la veille : la palette est sémantique, et un état porté par la couleur
 * seule tombe sous le filet a11y (docs/30 §1.6).
 *
 * **Les parts se comptent sur le tout**, orchestration comprise (`repartir`) :
 * elles font donc toujours 100 %, et la liste des agents laisse visiblement la
 * place de ce qui n'en est pas un. Les recompter sur le parc seul ferait dire à
 * l'écran que les agents ont dépensé toute la fenêtre.
 */

import { Infobulle } from "@/components/Infobulle";
import { formatCout, formatDuree, formatTokens } from "@/lib/format";
import type { CoutAgentAgrege } from "@/lib/types";

/** Comment l'orchestration se nomme ici : ce qu'elle fait, pas qui elle est. */
const LIBELLE_ORCHESTRATION = "orchestration";

/** Le détail d'un poste : ce qu'il a consommé, sous l'infobulle de son nom. */
function infobulle(poste: CoutAgentAgrege): string {
  /* La durée est celle du **travail** (#989) : l'horloge contenait le temps
     passé à attendre un créneau d'instance, qui n'est pas du travail et se lit
     à part. */
  return `${formatTokens(poste.usage.tokens_total)} tokens · ${poste.usage.appels} appel(s) · ${formatDuree(poste.usage.duree_execution_ms ?? poste.usage.duree_ms)}`;
}

/**
 * La règle de répartition de l'écran, écrite **une** fois : sur quoi les parts
 * se comptent, et dans quelle unité.
 *
 * Les deux composants de ce module l'appellent avec les deux mêmes props — la
 * liste et l'étiquette ne peuvent donc pas annoncer deux pourcentages
 * différents de la même dépense, ce qui serait la première chose qu'un lecteur
 * verrait.
 */
function repartir(agents: CoutAgentAgrege[], orchestration: CoutAgentAgrege | null) {
  const postes = orchestration ? [...agents, orchestration] : agents;
  const coutTotal = postes.reduce((s, p) => s + (p.usage.cout_usd ?? 0), 0);
  // Part au coût quand il est rapporté, aux tokens sinon (coût inconnu ≠ nul).
  const surTokens = coutTotal <= 0;
  const reference = surTokens
    ? postes.reduce((s, p) => s + p.usage.tokens_total, 0)
    : coutTotal;
  const valeurDe = (poste: CoutAgentAgrege) =>
    surTokens ? poste.usage.tokens_total : (poste.usage.cout_usd ?? 0);
  return {
    surTokens,
    reference,
    valeurDe,
    partDe: (poste: CoutAgentAgrege) =>
      reference > 0 ? valeurDe(poste) / reference : 0,
    /** Le montant tel qu'il s'écrit — coût, ou tokens quand rien n'est chiffré. */
    montantDe: (poste: CoutAgentAgrege) =>
      surTokens
        ? `${formatTokens(poste.usage.tokens_total)} tokens`
        : formatCout(poste.usage.cout_usd),
  };
}

export function RepartitionAgents({
  agents,
  orchestration = null,
}: {
  agents: CoutAgentAgrege[];
  /**
   * Le poste de Maestro (#1028) — `null` quand il n'a rien coûté de mesuré.
   *
   * Il n'est pas **rendu** ici (c'est `EtiquetteOrchestration` qui s'en charge,
   * en tête du bloc) : il n'entre que dans le **dénominateur** des parts. Le
   * passer quand même est ce qui garde les deux chiffres d'accord.
   */
  orchestration?: CoutAgentAgrege | null;
}) {
  if (agents.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        {orchestration
          ? "Aucun agent n'a travaillé sur la période."
          : "Aucun usage attribué sur la période."}
      </p>
    );
  }

  const { surTokens, reference, valeurDe, partDe, montantDe } = repartir(
    agents,
    orchestration,
  );

  return (
    <div className="space-y-2">
      {surTokens && reference > 0 && (
        <p className="text-xs text-neutral-500 dark:text-neutral-400">
          Aucun coût rapporté sur la période : répartition en tokens.
        </p>
      )}
      {agents.map((agent) => {
        const part = partDe(agent);
        return (
          <div key={agent.agent}>
            <div className="flex items-baseline justify-between gap-x-4 text-sm">
              <p className="truncate">
                {/* L'infobulle se pose sur le **nom** de l'agent, pas sur la
                    ligne entière (#536) : le wrapper focusable est un `<span>`,
                    qui ne peut pas contenir les `<div>` de la barre. */}
                <Infobulle texte={infobulle(agent)}>
                  <span className="font-medium">{agent.agent}</span>
                </Infobulle>
                {agent.role && (
                  <span className="text-neutral-500 dark:text-neutral-400">
                    {" "}
                    · {agent.role}
                  </span>
                )}
              </p>
              <p className="shrink-0 text-xs tabular-nums text-neutral-600 dark:text-neutral-400">
                {montantDe(agent)}
                {reference > 0 && (
                  <span className="text-neutral-400 dark:text-neutral-500">
                    {" "}
                    · {Math.round(part * 100)}&nbsp;%
                  </span>
                )}
              </p>
            </div>
            <div className="mt-1 h-2.5 overflow-hidden rounded-full bg-neutral-100 dark:bg-neutral-800">
              <div
                className="h-full rounded-full bg-[#2a78d6] dark:bg-[#3987e5]"
                style={{
                  width: `${Math.max(part * 100, valeurDe(agent) > 0 ? 1 : 0)}%`,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Ce que l'orchestration a coûté, **en tête du bloc** et non dans sa liste
 * (#1028) : « dont orchestration 0,02 $US · 7 % ».
 *
 * Posée en `aside` de l'`EnTeteSection` de « Répartition par agent », c'est-à-
 * dire à l'endroit exact où GitHub Actions pose « Total duration » — le résumé
 * appartient au bandeau de la carte, la liste en dessous n'en porte jamais de
 * ligne. Le mot « dont » fait tout le travail : il dit qu'on sort de ce que le
 * titre annonce, sans que le titre ait à mentir.
 *
 * Rendue en second plan (`text-annexe`, `texte-secondaire`) parce que c'est un
 * **cadre** et non une part : ce qu'on vient chercher dans ce bloc reste la
 * comparaison des agents entre eux.
 *
 * `null` quand rien n'a été mesuré : un poste à « 0,00 $US » dirait que
 * l'orchestration n'a rien coûté, là où l'API dit qu'elle n'a rien rapporté.
 */
export function EtiquetteOrchestration({
  agents,
  orchestration,
}: {
  agents: CoutAgentAgrege[];
  /**
   * `null` quand la fenêtre n'a rien à en dire — et **absent** quand la réponse
   * vient d'une API d'avant #1028, qui ne porte pas le champ. Les deux se
   * traitent pareil : l'en-tête ne rend rien, et l'écran retombe exactement sur
   * celui d'avant plutôt que de s'effondrer sur un champ qu'il croyait dû.
   */
  orchestration?: CoutAgentAgrege | null;
}) {
  if (!orchestration) return null;
  const { reference, partDe, montantDe } = repartir(agents, orchestration);
  return (
    /* Les tokens du socle et non une paire `neutral` + `dark:` : une couleur se
       choisit une fois, les deux thèmes viennent avec elle (apps/web/README.md,
       « La palette sémantique »). Le résidu de paires écrites à la main ne peut
       que décroître — `tests/couleurs.test.ts` le compte, fichier par fichier. */
    <p className="text-annexe tabular-nums text-texte-secondaire">
      dont{" "}
      <Infobulle texte={infobulle(orchestration)}>
        <span>{LIBELLE_ORCHESTRATION}</span>
      </Infobulle>{" "}
      <span className="font-medium text-texte">
        {montantDe(orchestration)}
      </span>
      {reference > 0 && <> · {Math.round(partDe(orchestration) * 100)}&nbsp;%</>}
    </p>
  );
}
