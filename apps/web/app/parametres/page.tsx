"use client";

/**
 * La page Paramètres de la Control Tower (#121, lot 5 de #116) : la
 * configuration regroupée en un endroit, organisée en sections nommées et
 * navigables par ancres (sous-menu à gauche, `lib/parametres.ts`).
 *
 * Le principe de la page : **ce qui est réglable l'est vraiment ici**, et ce qui
 * ne l'est pas encore le dit — d'où ça se règle aujourd'hui (variable
 * d'environnement, option de lancement) et vers quelle page de l'interface aller
 * quand il y en a une. Aucune section n'est un lien mort ni un interrupteur sans
 * effet.
 *
 * Les réglages branchés sur l'API existante sont ceux de la capacité des agents
 * (#86 : activer/désactiver, plafond d'instances) ; le thème (#118) et le repli
 * de la barre latérale (#117) sont des préférences du poste, portées par les
 * mêmes modules que les contrôles de la barre supérieure — ils s'appliquent
 * immédiatement, des deux côtés.
 */

import { NavigationParametres } from "@/components/parametres/NavigationParametres";
import { ParametresAgents } from "@/components/parametres/ParametresAgents";
import { ParametresApparence } from "@/components/parametres/ParametresApparence";
import { ParametresCouts } from "@/components/parametres/ParametresCouts";
import { ParametresFournisseurs } from "@/components/parametres/ParametresFournisseurs";
import { ParametresGeneral } from "@/components/parametres/ParametresGeneral";
import { ParametresNotifications } from "@/components/parametres/ParametresNotifications";
import {
  EspaceDefilement,
  SectionParametres,
} from "@/components/parametres/SectionParametres";
import { SECTIONS_PARAMETRES, type IdSection } from "@/lib/parametres";

/**
 * Le contenu de chaque section, par ancre — le sommaire, lui, vit dans
 * `lib/parametres`. Le `Record` sur l'union des ancres rend l'oubli impossible :
 * une section déclarée sans contenu ne compile pas.
 */
const CONTENUS: Record<IdSection, () => React.ReactNode> = {
  general: ParametresGeneral,
  apparence: ParametresApparence,
  agents: ParametresAgents,
  fournisseurs: ParametresFournisseurs,
  couts: ParametresCouts,
  notifications: ParametresNotifications,
};

export default function PageParametres() {
  return (
    <div className="flex flex-col gap-6 @3xl:flex-row @3xl:items-start @3xl:gap-8">
      <NavigationParametres />
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {SECTIONS_PARAMETRES.map((section) => {
          const Contenu = CONTENUS[section.id];
          return (
            <SectionParametres key={section.id} section={section}>
              <Contenu />
            </SectionParametres>
          );
        })}
        <EspaceDefilement />
      </div>
    </div>
  );
}
