"use client";

/**
 * Section « Coûts & plafonds » des Paramètres (#121) : la dépense cumulée
 * rapportée par les agents, et l'état des garde-fous de budget.
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
  const { coutTotal } = useEtatGlobal();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col">
        <LigneReglage
          libelle="Dépense cumulée"
          aide="Somme des coûts rapportés par les agents depuis le démarrage du backend. « — » tant qu'aucun ne l'a rapportée (inconnu n'est pas nul)."
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
