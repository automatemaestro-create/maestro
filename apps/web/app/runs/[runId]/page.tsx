/**
 * La page d'un run (#475, docs/05 §2.4.2) : `/runs/<run_id>`.
 *
 * Une coquille comme les autres — le contenu vit dans `components/runs/`, parce
 * que c'est lui qui se teste. Elle ne porte ni titre ni en-tête : la barre
 * supérieure les dérive du menu (#117), et une entrée couvre ses sous-chemins
 * (`entreeCourante`), si bien que cette page est titrée « Runs » sans avoir
 * d'entrée à elle.
 *
 * `key={runId}` n'est pas un détail : Next garde le composant monté d'un run à
 * l'autre sur une même route dynamique, et la vue tient l'état des tâches du run
 * qu'elle affiche. Sans la clé, passer d'un run à un autre montrerait les tâches
 * du précédent le temps d'une lecture — et le panneau de détail ouvert sur une
 * tâche resterait ouvert sur une tâche qui n'est plus là.
 *
 * L'identifiant est **décodé** (`decodeURIComponent`) comme celui d'un agent
 * (#190) : `hrefRun` l'encode à l'écriture, la symétrie se fait ici.
 */

import { VueRun } from "@/components/runs/VueRun";

export default async function PageRun({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  const identifiant = decodeURIComponent(runId);
  return <VueRun key={identifiant} runId={identifiant} />;
}
