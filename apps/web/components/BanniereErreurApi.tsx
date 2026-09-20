/**
 * Le bandeau d'erreur commun à toutes les pages (#117) : chacune garde son
 * propre chargement, donc sa propre erreur, mais le message et son habillage ne
 * se réécrivent plus page par page.
 *
 * **Il nomme la panne qui a eu lieu** (#996), et il y en a deux : une API
 * *injoignable* — rien n'a répondu — et une API qui *a répondu* en 4xx/5xx.
 * Les confondre, c'est ce que faisait le bandeau d'avant : il envoyait vérifier
 * que `maestro-api` tourne alors que le backend venait de répondre, et le
 * `detail` que l'API rendait n'arrivait jamais à l'écran. La classe vient de
 * `ErreurApi` (`lib/api`), qui la porte **depuis la lecture** : rien n'est
 * deviné en relisant le message.
 *
 * La forme est celle retenue par le regard neuf (#1009, commentaire
 * « ## Variante retenue » du ticket), d'après Primer (*Banner* critical :
 * icône + libellé en gras + détail) et Carbon (icône · titre · corps) :
 *
 *     ⚠ **<la panne>** — <le motif du serveur, ou le geste>
 *       GET <route> → <code> · <où aller voir>
 *
 * Deux choses s'y décident et ne se défont pas seules :
 *
 * - **Le motif rendu par le serveur porte la ligne principale**, et le code
 *   HTTP descend en annexe — c'est le conseil de NN/g (minimiser les codes
 *   techniques, les réserver au diagnostic) et la séparation que Stripe fait
 *   entre `message` et `statusCode`. L'inverse mettrait un nombre là où le
 *   lecteur attend une phrase.
 * - **L'annexe s'écrit en `alerte-texte`, jamais en `texte-secondaire`** : sur
 *   un fond creux, `texte-secondaire` ne tient pas 4,5:1 (4,23:1 mesuré sur le
 *   fond voisin, `contraste.test.ts`). Son retrait est porté par la **taille**
 *   et la **police**, pas par une teinte plus pâle.
 */

import { IconeAlerte } from "@/components/Icones";
import { ErreurApi, type PanneApi } from "@/lib/api";

/** Le service à relancer — celui que `start.sh` monte, et que la doc nomme. */
const SERVICE_API = "maestro-api";

type Contenu = {
  /** Ce qui a eu lieu, en tête et en gras. */
  panne: string;
  /** Ce qu'on en sait : le motif rendu par le serveur, ou le geste à faire. */
  message: string;
  /** La ligne de diagnostic, ou `null` quand la panne n'en porte pas. */
  diagnostic: string | null;
};

function contenuDe(erreur: PanneApi): Contenu {
  // Une panne qui n'est pas une lecture de l'API (un envoi refusé, une
  // exception d'écran) n'a ni route ni code : on montre son texte, sans rien
  // inventer autour.
  if (!(erreur instanceof ErreurApi)) {
    return { panne: "Lecture impossible", message: erreur, diagnostic: null };
  }
  if (erreur.statut === null) {
    return {
      panne: "API injoignable",
      message: `rien n'a répondu — vérifier que le backend tourne (${SERVICE_API}).`,
      diagnostic: `GET ${erreur.chemin} · aucune réponse`,
    };
  }
  return {
    panne: "L'API a répondu en erreur",
    message:
      erreur.motif === ""
        ? "le backend tourne, mais il a refusé cette lecture."
        : erreur.motif,
    diagnostic: `GET ${erreur.chemin} → ${erreur.statut} · voir le journal de ${SERVICE_API}`,
  };
}

export function BanniereErreurApi({ erreur }: { erreur: PanneApi | null }) {
  if (erreur === null) return null;
  const { panne, message, diagnostic } = contenuDe(erreur);
  return (
    <div
      role="alert"
      className="flex gap-2 rounded-md border border-alerte bg-alerte-creux px-3 py-2"
    >
      <IconeAlerte
        aria-hidden
        className="mt-0.5 size-4 shrink-0 text-alerte-texte"
      />
      <div className="min-w-0">
        <p className="text-corps text-alerte-texte">
          <span className="font-medium">{panne}</span> — {message}
        </p>
        {diagnostic !== null && (
          <p className="mt-1 font-mono text-annexe text-alerte-texte">
            {diagnostic}
          </p>
        )}
      </div>
    </div>
  );
}
