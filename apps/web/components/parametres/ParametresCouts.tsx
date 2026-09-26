"use client";

/**
 * Section « Coûts & plafonds » des Paramètres (#121) : la dépense cumulée du
 * **projet actif**, et où se posent les garde-fous de budget.
 *
 * Depuis #281 le chiffre est celui du projet, comme partout ailleurs — même
 * source que la barre supérieure et que la tuile « Dépense » (`coutCumule`).
 * Une page de réglages n'échappe pas au cadre : c'est justement là qu'un total
 * « toutes activités confondues » se lirait comme la vérité de référence.
 *
 * ## Ce que #990 a changé, et pourquoi c'est un renvoi et non un champ
 *
 * Cette section disait « Le plafond de dépense n'est pas encore réglable depuis
 * l'interface » et renvoyait à une option de ligne de commande : un cul-de-sac,
 * et le deuxième critère du ticket. Les quatre bornes d'un run — coût, tokens,
 * délai par tâche, parallélisme — se posent désormais **au lancement**, dans la
 * carte « Lancer ce run ? » de la conversation
 * (`components/chat/DemandeDeCadrage`).
 *
 * Elles ne se règlent donc **pas ici**, et c'est un choix rendu sur pièces
 * (commentaire « Variante retenue » de #990) : une variante posait des
 * **défauts** dans cette section, et le regard neuf l'a écartée pour la raison
 * qui compte — pour borner *ce* run-ci, il aurait fallu quitter une proposition
 * en attente, aller régler un défaut ailleurs, puis revenir. Un second endroit
 * où poser la même valeur serait aussi un second support de la même vérité, et
 * le premier symptôme en est toujours le même : deux écrans qui affichent deux
 * plafonds.
 *
 * Ce qui reste donc ici est ce que des réglages doivent : **dire où**. Le
 * renvoi mène à la conversation, qui est la seule porte de lancement (#666).
 */

import { LienRenvoi } from "@/components/Primitives";
import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatCoutPartiel, RAISON_COUT_PARTIEL } from "@/lib/format";

import { LigneReglage } from "./SectionParametres";

export function ParametresCouts() {
  const { coutTotal, coutPartiel, projet } = useEtatGlobal();

  return (
    <div className="flex flex-col">
      {/* Le cumul partiel se dit comme partout (#1280) : « coût partiel »
          collé au montant, et l'aide de la ligne dit pourquoi — ici, l'aide
          est visible, l'infobulle n'aurait rien à ajouter. */}
      <LigneReglage
        libelle="Dépense cumulée"
        aide={
          coutPartiel
            ? `Somme des grands livres des exécutions de ${projet.nom}, planification comprise. ${RAISON_COUT_PARTIEL}`
            : `Somme des grands livres des exécutions de ${projet.nom}, planification comprise. « — » tant qu'aucun coût n'a été rapporté (inconnu n'est pas nul).`
        }
      >
        <span className="text-lg font-semibold tabular-nums">
          {formatCoutPartiel(coutTotal, coutPartiel)}
        </span>
      </LigneReglage>

      {/* L'aide tient en deux lignes courtes : la ligne de réglage donne sa
          largeur au texte et laisse le renvoi à droite (`LigneReglage`), si
          bien qu'une phrase longue vient buter contre lui — constat de la
          relecture visuelle sur la première rédaction. */}
      <LigneReglage
        libelle="Bornes d'un run"
        aide="Coût, tokens, délai par tâche, parallélisme : ils se posent au lancement, dans la carte « Lancer ce run ? ». Sans borne, le run va jusqu'au bout — et la carte le dit."
      >
        <LienRenvoi
          renvoi={{ href: "/chat", libelle: "Aller à la conversation" }}
        />
      </LigneReglage>

      <LigneReglage
        libelle="Ce que chaque run a coûté"
        aide="Le détail par run et par tâche, une fois le travail parti."
      >
        <LienRenvoi
          renvoi={{ href: "/couts", libelle: "Voir les coûts & analytics" }}
        />
      </LigneReglage>
    </div>
  );
}
