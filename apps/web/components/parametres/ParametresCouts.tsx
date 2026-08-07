"use client";

/**
 * Section « Coûts & plafonds » des Paramètres (#121) : la dépense cumulée du
 * **projet actif**, et l'état des garde-fous de budget.
 *
 * Depuis #281 le chiffre est celui du projet, comme partout ailleurs — même
 * source que la barre supérieure et que la tuile « Dépense » (`coutCumule`).
 * Une page de réglages n'échappe pas au cadre : c'est justement là qu'un total
 * « toutes activités confondues » se lirait comme la vérité de référence.
 *
 * Le plafond de dépense existe bien dans le moteur (#56 : budget de l'exécution
 * entière), mais il se fixe au **lancement du run** (`--plafond-cout`) et l'API
 * Control Tower ne l'expose pas — ni en lecture ni en écriture. La section le
 * dit et renvoie vers la page qui, elle, existe : Coûts & analytics.
 */

import { useEtatGlobal } from "@/lib/etatGlobal";
import { formatCout } from "@/lib/format";

import { EtatVide, LigneReglage } from "./SectionParametres";

export function ParametresCouts() {
  const { coutTotal, projet } = useEtatGlobal();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col">
        <LigneReglage
          libelle="Dépense cumulée"
          aide={`Somme des grands livres des exécutions de ${projet.nom}, planification comprise. « — » tant qu'aucun coût n'a été rapporté (inconnu n'est pas nul).`}
        >
          <span className="text-lg font-semibold tabular-nums">
            {formatCout(coutTotal)}
          </span>
        </LigneReglage>
      </div>

      <EtatVide
        message="Le plafond de dépense n'est pas encore réglable depuis l'interface."
        releve="Il se fixe au lancement d'une exécution (option --plafond-cout, #56) : le moteur interrompt le run quand le budget est atteint."
        lien={{ href: "/couts", libelle: "Voir les coûts & analytics" }}
      />
    </div>
  );
}
