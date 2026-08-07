/**
 * Le fil d'activité en direct (docs/05 §2.1 : « qui fait quoi ») : les
 * derniers événements reçus sur `WS /ws/evenements`, du plus récent au plus
 * ancien. Le fil est éphémère — il reflète le flux depuis l'ouverture de la
 * page, l'état de référence restant le REST.
 *
 * Deux réglages posés par le tableau de bord épuré (#191) : `limite` en fait un
 * **aperçu** de quelques lignes plutôt qu'un panneau de plein format, et
 * `renvoi` porte le lien vers la page qui héberge le fil complet. Ce renvoi est
 * optionnel à dessein : la page Journal (chantier « Visibilité ») n'existe pas
 * encore, et l'aperçu se suffit à lui-même en attendant — le jour où elle entre
 * au menu, le lien s'allume sans toucher à ce composant.
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
}: {
  evenements: Evenement[];
  /** Nombre d'entrées affichées ; toutes par défaut. */
  limite?: number;
  /** Page où le fil se consulte en entier, si elle existe. */
  renvoi?: { href: string; libelle: string };
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
    <section data-guide="activite" aria-label="Activité en direct">
      <EnTeteSection
        titre="Activité en direct"
        icone={IconeActivite}
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
          <li className="text-corps text-neutral-500">
            Aucun événement reçu pour l&apos;instant.
          </li>
        )}
      </ol>
    </section>
  );
}
