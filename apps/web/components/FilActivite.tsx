/**
 * Le fil d'activité (docs/05 §2.1 : « qui fait quoi ») : les événements du plus
 * récent au plus ancien.
 *
 * Il a longtemps été **éphémère** — il ne reflétait que le flux
 * `WS /ws/evenements` depuis l'ouverture de la page, donc un rechargement
 * effaçait tout. Ce n'est plus le cas depuis #478 : ses appelants partent du
 * **journal persisté** (`GET /api/journal`, `lib/useJournal`) et n'y superposent
 * le temps réel que par-dessus. Ce composant, lui, n'a pas changé de nature — il
 * rend la liste qu'on lui donne, et c'est précisément pour cela que l'historique
 * et le direct se lisent dans la même ligne, avec le même vocabulaire.
 *
 * Cinq réglages, tous optionnels : `limite` en fait un **aperçu** de quelques
 * lignes plutôt qu'un panneau de plein format (tableau de bord épuré, #191),
 * `renvoi` porte le lien vers la page qui héberge le fil complet,
 * `titre`/`messageVide` nomment le fil quand ce n'est pas celui du tableau de
 * bord — le journal **d'un run** (#478) parle du run, pas de « l'activité en
 * direct » de tout le projet — et `niveau` le range dans la hiérarchie du
 * document : un fil qui est **une sous-partie** d'un écran est un `h3`, ce dont
 * l'onglet Logs d'un agent (#266) a besoin, lui qui rend un fil par tâche sous
 * un titre commun. C'est le sens qui change, pas l'apparence (`EnTeteSection`).
 */

import { IconeActivite } from "@/components/Icones";
import { LigneActivite } from "@/components/LigneActivite";
import { EnTeteSection, LienRenvoi } from "@/components/Primitives";
import { grouperEvenements } from "@/lib/evenements";
import { type Evenement } from "@/lib/types";

export function FilActivite({
  evenements,
  limite,
  renvoi,
  titre = "Activité en direct",
  messageVide = "Aucun événement reçu pour l'instant.",
  niveau = 2,
}: {
  evenements: Evenement[];
  /** Nombre d'entrées affichées ; toutes par défaut. */
  limite?: number;
  /** Page où le fil se consulte en entier, si elle existe. */
  renvoi?: { href: string; libelle: string };
  /** Le nom du fil — celui du tableau de bord par défaut. */
  titre?: string;
  /** Ce que dit le fil vide : « pas encore » ne s'explique pas partout pareil. */
  messageVide?: string;
  /** Son rang dans le document : section de page (2) ou sous-partie (3). */
  niveau?: 2 | 3;
}) {
  // La limite borne des **lignes**, pas des événements : une rafale repliée
  // (#250) en occupe une seule. Le compte masqué, lui, reste en événements —
  // c'est ce qu'on va chercher en ouvrant le fil complet.
  const groupes = grouperEvenements(evenements);
  const affiches = limite === undefined ? groupes : groupes.slice(0, limite);
  const montres = affiches.reduce(
    (total, groupe) => total + groupe.evenements.length,
    0,
  );
  const masques = evenements.length - montres;

  return (
    <section data-guide="activite" aria-label={titre}>
      <EnTeteSection
        titre={titre}
        icone={IconeActivite}
        niveau={niveau}
        className="mb-2"
        aside={
          renvoi ? (
            <LienRenvoi renvoi={renvoi} />
          ) : (
            masques > 0 && (
              <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
                + {masques} événement(s) plus anciens
              </span>
            )
          )
        }
      />
      <ol className="space-y-1 text-corps">
        {affiches.map((groupe) => (
          <LigneActivite key={groupe.cle} groupe={groupe} />
        ))}
        {affiches.length === 0 && (
          <li className="text-corps text-neutral-500">{messageVide}</li>
        )}
      </ol>
    </section>
  );
}
