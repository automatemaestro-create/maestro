"use client";

/**
 * Section « Notifications » des Paramètres (#121) : ce que remonte la cloche de
 * la barre supérieure (#119), et ce qui n'est pas encore réglable.
 *
 * Rien à choisir aujourd'hui — le centre de notifications montre toutes les
 * validations en attente et l'activité récente notable, sans filtre persisté.
 * La section décrit donc le comportement en vigueur (compteur en direct à
 * l'appui) plutôt que d'afficher des interrupteurs sans effet.
 */

import { useEtatGlobal } from "@/lib/etatGlobal";
import { VALIDATION_EN_ATTENTE } from "@/lib/types";

import { EtatVide, LigneReglage } from "./SectionParametres";

export function ParametresNotifications() {
  const { validations } = useEtatGlobal();
  const enAttente = validations.filter(
    (validation) => validation.statut === VALIDATION_EN_ATTENTE,
  ).length;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col">
        <LigneReglage
          libelle="Validations en attente"
          aide="Le badge de la cloche suit ce compteur en temps réel. Chaque demande s'approuve ou se refuse depuis le panneau, sans quitter la page."
        >
          <span className="text-lg font-semibold tabular-nums">{enAttente}</span>
        </LigneReglage>
      </div>

      <EtatVide
        message="Le tri des notifications n'est pas encore paramétrable."
        releve="La cloche remonte toutes les validations en attente et l'activité récente notable ; aucun filtre n'est persisté pour l'instant."
        lien={{ href: "/validations", libelle: "Ouvrir les validations" }}
      />
    </div>
  );
}
