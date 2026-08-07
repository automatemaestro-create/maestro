"use client";

/**
 * L'évolution du coût dans le temps (ticket #87) : un graphique en colonnes,
 * un seau par période (`pas` de la série servie par l'API analytics). Série
 * unique — pas de légende, le titre du panneau nomme la mesure ; chaque
 * colonne porte son infobulle (survol et focus clavier) et la table dépliable
 * en dessous rend chaque valeur lisible sans survol.
 */

import { useState } from "react";

import { formatCout, formatCoutAxe, formatTokens } from "@/lib/format";
import type { PasSerie, PointCout, Usage } from "@/lib/types";

/** Géométrie du dessin (unités du viewBox — le SVG s'étire en largeur). */
const LARGEUR = 720;
const HAUTEUR = 220;
const MARGE = { haut: 10, droite: 8, bas: 26, gauche: 56 };

/** Largeur maximale d'une colonne (le reste du créneau reste de l'air). */
const COLONNE_MAX = 24;

/** Durée d'un seau par granularité (ms) — pour combler les seaux vides. */
const PAS_MS: Record<PasSerie, number> = {
  minute: 60_000,
  heure: 3_600_000,
  jour: 86_400_000,
};

/** Au-delà, on renonce à combler : la série servie reste affichée telle quelle. */
const MAX_SEAUX_COMBLES = 400;

const USAGE_VIDE: Usage = {
  appels: 0,
  tokens_entree: 0,
  tokens_sortie: 0,
  tokens_total: 0,
  cout_usd: null,
  duree_ms: null,
  duree_api_ms: null,
  tours: 0,
  outils: [],
};

/**
 * Rend l'axe du temps honnête : les seaux sans usage entre le premier et le
 * dernier deviennent des colonnes à zéro, au lieu de coller deux périodes
 * distantes l'une à l'autre.
 */
function seauxContigus(serie: PointCout[], pas: PasSerie): PointCout[] {
  if (serie.length < 2) return serie;
  const pasMs = PAS_MS[pas];
  const debut = new Date(serie[0].periode).getTime();
  const fin = new Date(serie[serie.length - 1].periode).getTime();
  const nb = Math.round((fin - debut) / pasMs) + 1;
  if (!Number.isFinite(nb) || nb <= serie.length || nb > MAX_SEAUX_COMBLES) {
    return serie;
  }
  const parInstant = new Map(
    serie.map((p) => [new Date(p.periode).getTime(), p]),
  );
  return Array.from({ length: nb }, (_, i) => {
    const instant = debut + i * pasMs;
    return (
      parInstant.get(instant) ?? {
        periode: new Date(instant).toISOString(),
        usage: USAGE_VIDE,
      }
    );
  });
}

/** Borne haute et pas d'un axe propre (0 / 0,05 / 0,10…), 4 intervalles. */
function axeY(max: number): { max: number; pas: number } {
  if (max <= 0) return { max: 1, pas: 0.25 };
  const brut = max / 4;
  const puissance = 10 ** Math.floor(Math.log10(brut));
  const normalise = brut / puissance;
  const pas =
    (normalise <= 1 ? 1 : normalise <= 2 ? 2 : normalise <= 5 ? 5 : 10) *
    puissance;
  return { max: pas * Math.ceil(max / pas), pas };
}

/** Le libellé d'un seau : heure pour minute/heure, date courte pour jour. */
function libelleSeau(periode: string, pas: PasSerie): string {
  const date = new Date(periode);
  if (Number.isNaN(date.getTime())) return periode;
  if (pas === "jour") {
    return date.toLocaleDateString("fr-FR", { day: "2-digit", month: "2-digit" });
  }
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

/** Colonne à sommet arrondi (4px), carrée à la ligne de base. */
function cheminColonne(x: number, y: number, largeur: number, hauteur: number): string {
  const rayon = Math.min(4, hauteur, largeur / 2);
  const base = y + hauteur;
  return [
    `M ${x} ${base}`,
    `L ${x} ${y + rayon}`,
    `A ${rayon} ${rayon} 0 0 1 ${x + rayon} ${y}`,
    `L ${x + largeur - rayon} ${y}`,
    `A ${rayon} ${rayon} 0 0 1 ${x + largeur} ${y + rayon}`,
    `L ${x + largeur} ${base}`,
    "Z",
  ].join(" ");
}

export function GraphiqueEvolutionCout({
  serie,
  pas,
}: {
  serie: PointCout[];
  pas: PasSerie;
}) {
  const [survol, setSurvol] = useState<number | null>(null);

  if (serie.length === 0) {
    return (
      <p className="text-sm text-neutral-500 dark:text-neutral-400">
        Aucun usage daté sur la période — la série se remplira au fil des
        exécutions.
      </p>
    );
  }

  const seaux = seauxContigus(serie, pas);
  const couts = seaux.map((p) => p.usage.cout_usd ?? 0);
  const axe = axeY(Math.max(...couts));
  const largeurTrace = LARGEUR - MARGE.gauche - MARGE.droite;
  const hauteurTrace = HAUTEUR - MARGE.haut - MARGE.bas;
  const base = MARGE.haut + hauteurTrace;
  const creneau = largeurTrace / seaux.length;
  const colonne = Math.min(COLONNE_MAX, Math.max(2, creneau - 2));
  // Un libellé de temps sur ~6 créneaux : lisible sans collisions.
  const cadenceLibelles = Math.ceil(seaux.length / 6);

  const graduations: number[] = [];
  for (let v = 0; v <= axe.max + axe.pas / 2; v += axe.pas) graduations.push(v);

  const pointSurvole = survol !== null ? seaux[survol] : null;

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${LARGEUR} ${HAUTEUR}`}
        className="w-full"
        role="img"
        aria-label={`Évolution du coût par ${pas === "jour" ? "jour" : pas} sur la période`}
      >
        {graduations.map((valeur) => {
          const y = base - (valeur / axe.max) * hauteurTrace;
          return (
            <g key={valeur}>
              <line
                x1={MARGE.gauche}
                x2={LARGEUR - MARGE.droite}
                y1={y}
                y2={y}
                strokeWidth={1}
                className={
                  valeur === 0
                    ? "stroke-neutral-300 dark:stroke-neutral-700"
                    : "stroke-neutral-200 dark:stroke-neutral-800"
                }
              />
              <text
                x={MARGE.gauche - 6}
                y={y + 3}
                textAnchor="end"
                className="fill-neutral-500 text-[10px] tabular-nums dark:fill-neutral-400"
              >
                {formatCoutAxe(valeur)}
              </text>
            </g>
          );
        })}
        {seaux.map((point, i) => {
          const cout = point.usage.cout_usd ?? 0;
          const hauteurColonne = (cout / axe.max) * hauteurTrace;
          const x = MARGE.gauche + i * creneau + (creneau - colonne) / 2;
          return (
            <g key={point.periode}>
              {hauteurColonne > 0 && (
                <path
                  d={cheminColonne(x, base - hauteurColonne, colonne, hauteurColonne)}
                  className="fill-[#2a78d6] dark:fill-[#3987e5]"
                  style={survol === i ? { filter: "brightness(1.15)" } : undefined}
                />
              )}
              {i % cadenceLibelles === 0 && (
                <text
                  x={MARGE.gauche + i * creneau + creneau / 2}
                  y={HAUTEUR - 8}
                  textAnchor="middle"
                  className="fill-neutral-500 text-[10px] dark:fill-neutral-400"
                >
                  {libelleSeau(point.periode, pas)}
                </text>
              )}
              {/* La zone de visée : tout le créneau, bien plus large que la
                  colonne — l'infobulle se gagne au survol comme au clavier. */}
              <rect
                x={MARGE.gauche + i * creneau}
                y={MARGE.haut}
                width={creneau}
                height={hauteurTrace}
                fill="transparent"
                tabIndex={0}
                aria-label={`${libelleSeau(point.periode, pas)} : ${formatCout(point.usage.cout_usd)}, ${formatTokens(point.usage.tokens_total)} tokens`}
                onPointerEnter={() => setSurvol(i)}
                onPointerLeave={() => setSurvol(null)}
                onFocus={() => setSurvol(i)}
                onBlur={() => setSurvol(null)}
                className="outline-none focus-visible:stroke-neutral-400"
              />
            </g>
          );
        })}
      </svg>
      {pointSurvole && survol !== null && (
        <div
          role="status"
          className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-md border border-neutral-200 bg-white px-2.5 py-1.5 text-xs shadow-md dark:border-neutral-700 dark:bg-neutral-800"
          style={{
            left: `clamp(90px, ${((MARGE.gauche + (survol + 0.5) * ((LARGEUR - MARGE.gauche - MARGE.droite) / seaux.length)) / LARGEUR) * 100}%, calc(100% - 90px))`,
          }}
        >
          <p className="font-semibold tabular-nums">
            {formatCout(pointSurvole.usage.cout_usd)}
          </p>
          <p className="text-neutral-500 dark:text-neutral-400">
            {libelleSeau(pointSurvole.periode, pas)}
            {" · "}
            {formatTokens(pointSurvole.usage.tokens_total)} tokens
            {" · "}
            {pointSurvole.usage.appels} appel(s)
          </p>
        </div>
      )}
      <details className="mt-2 text-xs text-neutral-600 dark:text-neutral-400">
        <summary className="cursor-pointer select-none">
          Données de la série
        </summary>
        <div className="mt-1 overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b border-neutral-200 text-left text-neutral-500 dark:border-neutral-800 dark:text-neutral-400">
                <th className="py-1 pr-3 font-medium">Période</th>
                <th className="py-1 pr-3 text-right font-medium">Coût</th>
                <th className="py-1 pr-3 text-right font-medium">Tokens</th>
                <th className="py-1 text-right font-medium">Appels</th>
              </tr>
            </thead>
            <tbody>
              {serie.map((point) => (
                <tr
                  key={point.periode}
                  className="border-b border-neutral-100 dark:border-neutral-800/60"
                >
                  <td className="py-1 pr-3">{libelleSeau(point.periode, pas)}</td>
                  <td className="py-1 pr-3 text-right tabular-nums">
                    {formatCout(point.usage.cout_usd)}
                  </td>
                  <td className="py-1 pr-3 text-right tabular-nums">
                    {formatTokens(point.usage.tokens_total)}
                  </td>
                  <td className="py-1 text-right tabular-nums">
                    {point.usage.appels}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </div>
  );
}
