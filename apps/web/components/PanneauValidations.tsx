"use client";

/**
 * Les validations humaines (docs/05 §2.6, #48) — les **deux surfaces de file**,
 * l'aperçu du tableau de bord et le plein format de la page (#272, lot 5 de
 * #244).
 *
 * C'est l'écran le plus coûteux à mal rendre : une demande de validation est
 * bloquante, un moteur est en pause et un run attend derrière. Trois décisions
 * le tiennent, et une seule fois chacune :
 *
 * 1. **La plus ancienne d'abord.** La file est triée par horodatage croissant et
 *    la tête est rendue en plein — c'est elle qui retient un moteur depuis le
 *    plus longtemps, et rien d'autre dans la demande ne dit l'urgence. Le tri
 *    n'existait pas : l'ordre était celui du backend, donc celui de personne. Il
 *    vit dans `lib/validations` depuis #1228, avec les deux autres questions que
 *    la file pose.
 * 2. **Le temps d'attente est au premier plan**, là où la carte ne montrait que
 *    l'heure de la demande — un chiffre dont il fallait faire la soustraction
 *    soi-même pour savoir s'il y avait urgence.
 * 3. **Une carte, toutes les surfaces.** `PanneauValidations` est l'**aperçu**
 *    du tableau de bord (la plus ancienne, décidable sur place, et une ligne de
 *    renvoi pour le reste — la règle des trois places, docs/30 §4) ;
 *    `FileValidations` est le **plein format** de la page. Les deux montent
 *    `CarteValidation` (`components/CarteValidation`), que la cloche, l'en-tête
 *    d'un run et ses lectures denses montent aussi depuis #1228 : ce qui change
 *    est ce qu'on voit autour, jamais ce qu'on lit pour trancher.
 *
 * Le **temps réel** est tenu par la clé de React et par elle seule : chaque carte
 * est keyée sur `tache_id`, donc une demande tranchée ailleurs démonte *sa*
 * carte et emporte son état local (motif en cours de frappe, erreur, envoi en
 * vol). Sans cette clé, la file se décalant d'un cran, un motif écrit pour une
 * demande se retrouverait attaché à la suivante — un refus motivé à côté de la
 * plaque, sans que rien ne le signale.
 */

import {
  CarteValidation,
  type Decider,
} from "@/components/CarteValidation";
import { IconeAlerte } from "@/components/Icones";
import {
  BadgeEtat,
  Carte,
  EnTeteSection,
  LienRenvoi,
} from "@/components/Primitives";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import { fileDAttente } from "@/lib/validations";
import { type Validation } from "@/lib/types";

/** Le libellé du menu qui mène à la page — le chemin n'est jamais écrit ici. */
export const PAGE_VALIDATIONS = "Validations";

/** L'en-tête commun aux deux surfaces : ce qui attend, et combien. */
function EnTeteFile({ nombre, renvoi }: { nombre: number; renvoi?: boolean }) {
  const page = renvoi ? entreeParLibelle(PAGE_VALIDATIONS) : undefined;
  return (
    <EnTeteSection
      titre={
        <>
          Validations en attente
          <BadgeEtat ton="attention" className="chiffre">
            {nombre}
          </BadgeEtat>
        </>
      }
      icone={IconeAlerte}
      ton="attention"
      className="mb-2"
      aside={
        page && (
          <LienRenvoi
            renvoi={{ href: page.href, libelle: "Ouvrir les validations" }}
          />
        )
      }
    />
  );
}

/**
 * L'**aperçu** du tableau de bord : la demande la plus ancienne, entière et
 * décidable sur place, puis une ligne pour le reste.
 *
 * Montrer toute la file ici serait la refaire — et le tableau de bord n'est pas
 * l'écran des validations. Ce que la règle des trois places prescrit dans ce cas
 * est exactement ce qu'on fait : ce qui ne tient pas devient une ligne avec un
 * renvoi (docs/30 §4). Ce qui compte est qu'on puisse trancher **la plus
 * urgente** sans quitter l'écran, et c'est le critère du ticket.
 */
export function PanneauValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const maintenant = useHorloge();
  const file = fileDAttente(validations);
  if (file.length === 0) return null;
  const [premiere, ...suivantes] = file;

  return (
    <Carte
      balise="section"
      ton="attention"
      data-guide="validations"
      aria-label="Validations en attente"
    >
      <EnTeteFile nombre={file.length} renvoi />
      <CarteValidation
        key={premiere.tache_id}
        validation={premiere}
        decider={decider}
        maintenant={maintenant}
        densite="aeree"
      />
      {suivantes.length > 0 && (
        <p className="mt-3 text-annexe text-texte-secondaire">
          {suivantes.length === 1
            ? "1 autre demande attend son tour."
            : `${suivantes.length} autres demandes attendent leur tour.`}
        </p>
      )}
    </Carte>
  );
}

/**
 * Le **plein format** de la page Validations : la plus ancienne en tête, aérée,
 * puis les suivantes sous leur propre titre.
 *
 * Les suivantes ne sont pas résumées — même carte, mêmes champs, seule la
 * densité change. C'est ce que demande le critère 3 : ce qu'on lit pour trancher
 * ne doit pas dépendre de la place qu'une demande occupe dans la file, sans quoi
 * la deuxième se déciderait sur moins d'information que la première.
 */
export function FileValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const maintenant = useHorloge();
  const file = fileDAttente(validations);
  if (file.length === 0) return null;
  const [premiere, ...suivantes] = file;

  return (
    <Carte balise="section" ton="attention" aria-label="Validations en attente">
      <EnTeteFile nombre={file.length} />
      <CarteValidation
        key={premiere.tache_id}
        validation={premiere}
        decider={decider}
        maintenant={maintenant}
        densite="aeree"
      />
      {suivantes.length > 0 && (
        <>
          <EnTeteSection
            niveau={3}
            titre={suivantes.length === 1 ? "La suivante" : "Les suivantes"}
            ton="attention"
            className="mt-4 mb-2"
          />
          <ul className="grid grid-cols-1 gap-3 @3xl:grid-cols-2">
            {suivantes.map((validation) => (
              <li key={validation.tache_id} className="flex">
                <CarteValidation
                  validation={validation}
                  decider={decider}
                  maintenant={maintenant}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Carte>
  );
}
