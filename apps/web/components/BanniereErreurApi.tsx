"use client";

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
 *     ⚠ **<la panne>** — <le motif du serveur> · <ce qu'il y a à faire>
 *       GET <route> → <code>
 *
 * Trois choses s'y décident et ne se défont pas seules :
 *
 * - **Le geste reste sur la ligne de la panne.** Les deux questions du bandeau —
 *   *quelle panne ?* et *que faire maintenant ?* — se répondent d'un seul coup
 *   d'œil, et c'est ce que la variante retenue fait gagner sur les autres.
 *   Descendu dans l'annexe (ce qu'une première passe avait fait), le geste se
 *   lisait comme un jeton de log, après la route et le code : le regard neuf
 *   l'a vu, l'annexe ne porte donc plus que le diagnostic.
 * - **Le motif rendu par le serveur porte la ligne principale**, et le code
 *   HTTP descend en annexe — c'est le conseil de NN/g (minimiser les codes
 *   techniques, les réserver au diagnostic) et la séparation que Stripe fait
 *   entre `message` et `statusCode`. L'inverse mettrait un nombre là où le
 *   lecteur attend une phrase.
 * - **L'annexe s'écrit en `alerte-texte`, jamais en `texte-secondaire`** : sur
 *   un fond creux, `texte-secondaire` ne tient pas 4,5:1 (4,23:1 mesuré sur le
 *   fond voisin, `contraste.test.ts`). Son retrait est porté par la **taille**
 *   et la **police**, pas par une teinte plus pâle.
 *
 * ── La troisième panne : l'API a perdu son magasin (#1206) ─────────────────
 *
 * L'API tourne, mais sans son magasin d'événements (Redis) elle n'a rien d'à
 * jour à servir : elle refuse ses lectures en `503` et **nomme** la panne dans
 * le corps (`ErreurApi.magasin`). Ni « API injoignable » (elle a répondu), ni
 * « l'API a répondu en erreur » et son « voir le journal » (le remède est
 * ailleurs) : le bandeau dit le nom que le serveur lui donne, ce qu'elle coûte,
 * et le geste qui la lève — la commande en annexe, comme un diagnostic.
 *
 * Elle se dit à **deux** endroits, d'une **seule** mise en forme (`Bandeau`) :
 * à la porte d'entrée, par le bandeau d'écran, et dans l'application, par le
 * bandeau système que le shell rend sous la barre supérieure (`BandeauMagasin`,
 * variante C retenue par le regard neuf, consignée sur #1206 sous « ## Variante
 * retenue »). Quand le shell la dit, le bandeau d'écran de la même panne se tait
 * — un seul message à la fois (Carbon).
 */

import { IconeAlerte } from "@/components/Icones";
import { ErreurApi, type PanneApi } from "@/lib/api";
import { useMagasinSignale } from "@/lib/magasin";
import type { EtatMagasin } from "@/lib/types";

/** Le service à relancer — celui que `start.sh` monte, et que la doc nomme. */
const SERVICE_API = "maestro-api";

type Contenu = {
  /** Ce qui a eu lieu, en tête et en gras. */
  panne: string;
  /** Ce qu'on en sait : le motif rendu par le serveur, ou ce qui le remplace. */
  message: string;
  /** Ce qu'il y a à faire — sur la **même** ligne que la panne, jamais en annexe. */
  geste: string | null;
  /** La ligne de diagnostic, ou `null` quand la panne n'en porte pas. */
  diagnostic: string | null;
};

function contenuDe(erreur: PanneApi): Contenu {
  // Une panne qui n'est pas une lecture de l'API (un envoi refusé, une
  // exception d'écran) n'a ni route ni code : on montre son texte, sans rien
  // inventer autour.
  if (!(erreur instanceof ErreurApi)) {
    return {
      panne: "Lecture impossible",
      message: erreur,
      geste: null,
      diagnostic: null,
    };
  }
  if (erreur.magasin !== null) return contenuMagasin(erreur.magasin);
  if (erreur.statut === null) {
    return {
      panne: "API injoignable",
      message: "rien n'a répondu",
      geste: `vérifier que le backend tourne (${SERVICE_API}).`,
      diagnostic: `GET ${erreur.chemin} → aucune réponse`,
    };
  }
  return {
    panne: "L'API a répondu en erreur",
    message:
      erreur.motif === ""
        ? "le backend tourne, mais il a refusé cette lecture"
        : erreur.motif,
    geste: `voir le journal de ${SERVICE_API}.`,
    diagnostic: `GET ${erreur.chemin} → ${erreur.statut}`,
  };
}

/**
 * La perte du magasin, telle que le serveur la nomme (#1206) — champ par champ,
 * jamais relue dans son `detail`. La même pour la porte et pour le shell : c'est
 * ce qui leur donne un seul vocabulaire et un seul ordre.
 */
function contenuMagasin(magasin: EtatMagasin): Contenu {
  const annexe = [magasin.commande, magasin.lieu].filter(
    (partie): partie is string => partie !== null,
  );
  return {
    panne: magasin.titre ?? "Magasin des événements indisponible",
    message: magasin.motif ?? "rien de ce que l'écran montre n'est à jour",
    geste: magasin.geste === null ? null : `${magasin.geste}.`,
    diagnostic: annexe.length === 0 ? null : annexe.join(" · "),
  };
}

/**
 * Ce qui sépare le message du geste. Le motif vient du **serveur**, et il arrive
 * ponctué (« … de la démo (#978). ») aussi bien que nu : un point médian collé
 * derrière un point donnait « (#978). · voir le journal » — une scorie relevée à
 * la relecture. On regarde donc la dernière lettre, et rien d'autre : c'est de la
 * typographie, pas une lecture du sens.
 */
function separateur(message: string): string {
  return /[.!?…]$/.test(message.trimEnd()) ? " " : " · ";
}

export function BanniereErreurApi({ erreur }: { erreur: PanneApi | null }) {
  const signalee = useMagasinSignale();
  if (erreur === null) return null;
  // Un seul message à la fois (#1206) : quand le shell dit déjà la perte du
  // magasin, l'écran ne la répète pas sous lui.
  if (
    erreur instanceof ErreurApi &&
    erreur.magasin !== null &&
    signalee !== null
  ) {
    return null;
  }
  return (
    <Bandeau
      contenu={contenuDe(erreur)}
      className="flex gap-2 rounded-md border border-alerte bg-alerte-creux px-3 py-2"
    />
  );
}

/**
 * Le bandeau **système** de la perte du magasin (#1206), que le shell rend sous
 * la barre supérieure pour tous les écrans : celui qui dit la panne quand elle
 * survient en route, sur un écran déjà chargé qui ne relit rien de lui-même.
 * Pleine largeur de la colonne centrale, sans arrondi ni croix : une panne
 * encore vraie ne se congédie pas, elle part quand le magasin revient.
 */
export function BandeauMagasin() {
  const perdu = useMagasinSignale();
  if (perdu === null) return null;
  return (
    <Bandeau
      contenu={contenuMagasin(perdu)}
      className="flex gap-2 border-b border-alerte bg-alerte-creux p-3"
    />
  );
}

/**
 * L'habillage commun — icône, panne en gras, message et geste, annexe.
 *
 * Chaque appelant passe ses classes **en toutes lettres** : le balayage des
 * paddings (`tests/espacements.test.ts`) lit les littéraux, et une classe
 * composée à la volée lui échapperait — un résidu qui disparaît du compte sans
 * avoir été replié. Le bandeau système prend `p-3`, un pas du barème ; celui de
 * l'écran garde le sien, inscrit au résidu.
 */
function Bandeau({
  contenu,
  className,
}: {
  contenu: Contenu;
  className: string;
}) {
  const { panne, message, geste, diagnostic } = contenu;
  return (
    <div role="alert" className={className}>
      <IconeAlerte
        aria-hidden
        className="mt-0.5 size-4 shrink-0 text-alerte-texte"
      />
      <div className="min-w-0">
        <p className="text-corps text-alerte-texte">
          <span className="font-medium">{panne}</span> — {message}
          {geste !== null && (
            <>
              {separateur(message)}
              {geste}
            </>
          )}
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
