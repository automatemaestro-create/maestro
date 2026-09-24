/**
 * L'équipe d'un projet, côté écran : ce qu'on en valide, et quand le fil la demande.
 *
 * Deux surfaces recrutent depuis #1146 — l'étape d'équipe du parcours de création
 * (`EtapeEquipe`, #1040) et la carte que le fil de l'orchestration pose au pied
 * d'une demande de travail sur un projet sans agent (`EquipeDansLeFil`). Ce
 * module tient ce qu'elles doivent dire **pareil** : sans lui, « ce qui repart à
 * la création » finirait par ne plus être la même chose selon l'endroit d'où l'on
 * valide, et un cran `auto` lu ici pourrait ne pas être celui qui est écrit là.
 */

import type {
  AutorisationEquipe,
  CorrectionEquipe,
  EquipeRecrutee,
  MembreEquipe,
  MessageChat,
  PropositionEquipe,
  RoleEquipe,
  RoleValideEquipe,
} from "@/lib/types";

/**
 * La **demande de recrutement** que ce fil porte encore, `null` sinon (#1146).
 *
 * La règle des deux autres demandes du fil — le dernier message, et lui seul
 * (`propositionEnAttente`, `questionEnAttente`) —, énoncée une fois de ce côté-ci
 * comme elle l'est une fois côté moteur (`recrutement_en_attente`). Ce qui la
 * solde n'est pas le temps, c'est qu'on y ait répondu.
 */
export function recrutementEnAttente(
  messages: MessageChat[],
): MessageChat | null {
  const dernier = messages[messages.length - 1];
  if (dernier === undefined) return null;
  return dernier.recrutement ? dernier : null;
}

/**
 * Les propositions déjà demandées pour une demande de recrutement, par clé.
 *
 * La carte d'équipe du fil est montée **sur chaque écran** — la colonne de
 * conversation la porte partout (`GestesDuFil`, #1106) —, et une proposition coûte
 * une analyse du projet plus un appel modèle par rôle (#257). Sans cette mémoire,
 * chaque navigation la redemandait tant que la demande attendait : relevé à la
 * relecture de #1146. La clé est la **demande** (son projet et son message), donc
 * une nouvelle demande repart d'une proposition neuve.
 */
const propositions = new Map<string, Promise<PropositionEquipe>>();

/**
 * La proposition d'équipe de cette demande — demandée une fois, puis partagée.
 *
 * Un échec n'est pas retenu : la clé est oubliée, et le montage suivant réessaie.
 */
export function propositionPourDemande(
  cle: string,
  charger: () => Promise<PropositionEquipe>,
): Promise<PropositionEquipe> {
  const deja = propositions.get(cle);
  if (deja !== undefined) return deja;
  const promesse = charger();
  propositions.set(cle, promesse);
  promesse.catch(() => {
    if (propositions.get(cle) === promesse) propositions.delete(cle);
  });
  return promesse;
}

/** Oublie toutes les propositions retenues — l'état d'une session neuve (tests). */
export function oublierPropositions(): void {
  propositions.clear();
}

/** « 2 agents », « 1 agent » — le compte et son nom, accordés. */
export function compteAgents(nombre: number): string {
  return `${nombre} agent${nombre > 1 ? "s" : ""}`;
}

/**
 * Les rôles **gardés**, dans la forme où ils repartent à la création (#1040).
 *
 * Ce qui repart est ce qui a été **montré** : playbook et `politique` repris de
 * la proposition, jamais recomposés ici — c'est ainsi que le cran `auto` qu'on a
 * lu avec sa raison est le cran qui sera écrit (#716). Seules les instances sont
 * celles de l'écran.
 */
export function rolesValides(
  proposition: PropositionEquipe,
  retenus: ReadonlySet<string>,
  instances: Readonly<Record<string, number>>,
): RoleValideEquipe[] {
  return proposition.roles
    .filter((r) => retenus.has(r.nom))
    .map((r) => ({
      nom: r.nom,
      role: r.role,
      competences: r.competences,
      playbook: r.playbook,
      instances: instances[r.nom] ?? r.instances,
      gabarit: r.gabarit,
      skills: r.skills.map((s) => ({
        nom: s.nom,
        chemin: s.chemin,
        commandes: s.commandes,
      })),
      politique: r.politique,
    }));
}

/**
 * « Développeur ×2 · QA — 3 agents » — l'équipe qu'un geste a **créée** (#1262).
 *
 * Les formules de `composition` et `compteAgents`, appliquées au fait que la
 * réponse porte (`EquipeRecrutee`) et non à la proposition : ce qui se lit sous
 * la bulle est ce qui existe désormais dans le projet.
 */
export function equipeCreeeEnUneLigne(equipe: EquipeRecrutee): string {
  const roles = equipe.roles
    .map((r) => (r.instances > 1 ? `${r.role} ×${r.instances}` : r.role))
    .join(" · ");
  return `${roles} — ${compteAgents(equipe.instances_total)}`;
}

/** L'équipe **telle qu'une étape la montre** : ses lignes, ses cases, ses instances. */
export type EquipeMontree = {
  roles: RoleEquipe[];
  retenus: ReadonlySet<string>;
  instances: Readonly<Record<string, number>>;
};

/**
 * L'équipe montrée, dans la forme d'une demande de correction (#1159) — cases et
 * instances comprises, retraits aussi : « remets les tests » n'a de sens que si
 * le modèle sait qu'ils ont été retirés.
 */
export function membresMontres(equipe: EquipeMontree): MembreEquipe[] {
  return equipe.roles.map((r) => ({
    nom: r.nom,
    role: r.role,
    retenu: equipe.retenus.has(r.nom),
    instances: equipe.instances[r.nom] ?? r.instances,
  }));
}

/**
 * L'équipe montrée, la correction **appliquée** (#1159) — rien n'est créé.
 *
 * La correction atterrit sur l'équipe elle-même (veille consignée sur #1159,
 * d'après *Crew Studio*) : un rôle ajouté devient une ligne de plus, **retenue**,
 * en fin de liste — l'ordre de la proposition ne bouge pas (variante retenue A) ;
 * un retrait décoche, une remise recoche, une instance change le compteur. Un
 * ajout dont le nom est déjà montré est ignoré — l'API les rend libres, et une
 * seconde ligne du même nom perdrait sa case (même `id`). `ajoutes` nomme les
 * lignes nées de la demande, que l'écran signale comme telles ; `change` dit si
 * la demande a touché quoi que ce soit.
 */
export function appliquerCorrection(
  equipe: EquipeMontree,
  correction: CorrectionEquipe,
): EquipeMontree & { ajoutes: string[]; change: boolean } {
  const montres = new Set(equipe.roles.map((r) => r.nom));
  const ajouts = correction.ajouts.filter((r) => !montres.has(r.nom));
  const retenus = new Set(equipe.retenus);
  for (const nom of correction.retraits) retenus.delete(nom);
  for (const nom of [...correction.remis, ...ajouts.map((r) => r.nom)]) {
    retenus.add(nom);
  }
  return {
    roles: [...equipe.roles, ...ajouts],
    retenus,
    instances: {
      ...equipe.instances,
      ...Object.fromEntries(ajouts.map((r) => [r.nom, r.instances])),
      ...correction.instances,
    },
    ajoutes: ajouts.map((r) => r.nom),
    change:
      ajouts.length > 0 ||
      correction.retraits.length > 0 ||
      correction.remis.length > 0 ||
      Object.keys(correction.instances).length > 0,
  };
}

/** « Développeur ×2 · QA » — l'équipe gardée, en une ligne. */
export function composition(
  roles: RoleEquipe[],
  instances: Readonly<Record<string, number>>,
): string {
  return roles
    .map((r) => {
      const n = instances[r.nom] ?? r.instances;
      return n > 1 ? `${r.role} ×${n}` : r.role;
    })
    .join(" · ");
}

/** Une autorisation décidée d'avance, avec le rôle qui la reçoit. */
export type AutorisationAuto = {
  role: string;
  autorisation: AutorisationEquipe;
};

/**
 * Les autorisations que la validation **décide d'avance** — celles dont le
 * décideur est `auto` —, rôle par rôle, pour les rôles gardés seulement.
 *
 * C'est le critère de l'étape d'équipe (« chaque permission `auto` étant
 * montrée », #1040) : une surface qui replie le détail des rôles doit quand même
 * les nommer sur le chemin par défaut, sans quoi un cran `auto` que personne n'a
 * lu ne serait décidé par personne (#716).
 */
export function autorisationsDecideesDavance(
  roles: RoleEquipe[],
): AutorisationAuto[] {
  return roles.flatMap((r) =>
    r.autorisations
      .filter((a) => a.decideur === "auto")
      .map((autorisation) => ({ role: r.role, autorisation })),
  );
}
