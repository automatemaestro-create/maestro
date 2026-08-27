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
 *
 * ⚠ **Trois familles depuis #539** (règle des trois places, docs/30 §4). L'écran
 * empilait **sept** sections de plein format — le plus gros dépassement du
 * produit, quatre au-dessus du plafond. Aucune n'a été retirée : elles se
 * rangent sous trois familles (`FAMILLES_PARAMETRES`), qui sont désormais les
 * blocs, et deviennent leurs sous-parties. Le sous-menu, lui, était déjà la
 * moitié de la réponse ; il gagne le niveau qui lui manquait. Ce qu'on aurait
 * perdu à passer par des **onglets** est ce qui avait fait choisir les ancres
 * (`NavigationParametres`) : une page imprimable, cherchable au Ctrl+F et
 * partageable au lien près.
 */

import { FamilleParametres } from "@/components/parametres/SectionParametres";
import { NavigationParametres } from "@/components/parametres/NavigationParametres";
import { ParametresAgents } from "@/components/parametres/ParametresAgents";
import { ParametresApparence } from "@/components/parametres/ParametresApparence";
import { ParametresCouts } from "@/components/parametres/ParametresCouts";
import { ParametresFournisseurs } from "@/components/parametres/ParametresFournisseurs";
import { ParametresGeneral } from "@/components/parametres/ParametresGeneral";
import { ParametresNotifications } from "@/components/parametres/ParametresNotifications";
import { RedirectionAncreMcp } from "@/components/parametres/RedirectionAncreMcp";
import {
  EspaceDefilement,
  SectionParametres,
} from "@/components/parametres/SectionParametres";
import { FAMILLES_PARAMETRES, type IdSection } from "@/lib/parametres";

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
      {/*
        Ne rend rien : rattrape les signets `/parametres#mcp`, dont la section
        est partie sur son propre écran en #270. Un fragment n'atteignant jamais
        le serveur, la redirection ne peut pas vivre dans `next.config.ts`.
      */}
      <RedirectionAncreMcp />
      <NavigationParametres />
      <div className="flex min-w-0 flex-1 flex-col gap-6">
        {FAMILLES_PARAMETRES.map((famille) => (
          <FamilleParametres key={famille.id} famille={famille}>
            {famille.sections.map((section) => {
              const Contenu = CONTENUS[section.id];
              return (
                <SectionParametres key={section.id} section={section}>
                  <Contenu />
                </SectionParametres>
              );
            })}
          </FamilleParametres>
        ))}
        <EspaceDefilement />
      </div>
    </div>
  );
}
