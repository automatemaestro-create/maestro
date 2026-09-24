"use client";

/**
 * L'étape d'équipe : ce qui suit immédiatement l'outillage (#1040, docs/37 §4.6).
 *
 * « Après l'outillage, le parcours du projet présente l'équipe proposée. » Un
 * projet naît sans agent ; son analyse lui propose des rôles (#1039), et c'est
 * ici qu'on les relit, qu'on en retire, qu'on ajuste leurs instances — puis
 * qu'on valide. **La validation est le premier geste du chantier qui écrit
 * quelque chose** : elle crée les agents dans le projet, avec leur playbook,
 * leurs skills branchés, leurs autorisations et leur capacité.
 *
 * La question à laquelle un coup d'œil doit répondre : **quelle équipe va
 * travailler sur mon projet, pourquoi chacun, et ce que chacun aura le droit de
 * faire — avant que rien ne soit créé ?**
 *
 * ## La forme vient d'une veille et d'un choix consignés sur le ticket
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1040 : trois
 * références **vérifiées en direct** — *AWS IAM* (l'étape « Review and create »
 * d'une création de rôle), *Renovate* (la PR d'onboarding) et *GitHub*
 * (l'installation d'une App tierce) — et trois directions pesées contre elles.
 * La retenue est **A — l'inventaire à plat, autorisations dépliées d'office**.
 * Ce qu'elle tranche, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **une ligne par rôle, qui porte sa raison ET l'endroit qui la prouve** —
 *   d'après *Renovate* (« Detected Package Files » : un fichier par ligne avec
 *   ce qui l'a trahi). Une carte par rôle a été écartée : elle ferait un bloc de
 *   plein format par membre de l'équipe ;
 * - **les autorisations sont dépliées d'office**, en une ligne *cran + outil +
 *   décideur*, la raison dessous — d'après *GitHub*, qui dit avant toute
 *   installation ce que l'App a demandé, lu comme niveau + objet. La variante
 *   qui les repliait derrière un `<details>` a été écartée **par le critère du
 *   ticket lui-même** (« chaque permission `auto` étant montrée ») : repliée,
 *   la carte ne porte aucune autorisation sur le chemin par défaut, et un cran
 *   `auto` que personne n'a lu n'est décidé par personne (#716) ;
 * - **tout arrive retenu, corriger c'est décocher** — comme l'étape d'outillage
 *   (#1034, d'après *GitHub* « select or deselect ») ;
 * - **une ligne retirée perd son aplat ET son libellé est barré** : l'état ne
 *   tient jamais à la seule couleur (docs/30 §1, filet a11y) ;
 * - **valider et renoncer sont nommés ensemble, et le bouton dit l'acte** —
 *   d'après *Renovate* (activer et désactiver dans le même paragraphe) et *AWS
 *   IAM* (un seul « Create role », après le récapitulatif).
 *
 * Restent repliés : le **playbook** (long par nature) et les **rôles écartés** —
 * ni l'un ni l'autre ne porte le critère du ticket.
 *
 * ## Retirer, ajouter, ajuster — ce que chacun veut dire ici
 *
 * *Retirer*, c'est décocher ; *ajuster*, c'est le nombre d'instances — le partage
 * que l'étape d'outillage a posé (#1034 : « tout arrive retenu, corriger c'est
 * décocher »). *Ajouter* ou *changer* un rôle se **dit** depuis #1159 : l'équipe
 * est composée par le modèle pour le besoin du projet, et la personne la corrige
 * avec ses mots au pied de la liste (« ajoute quelqu'un pour la sécurité »). Le
 * rôle demandé est composé pour ce projet, playbook compris, et atterrit dans la
 * liste comme une ligne de plus, retenue et signalée — rien n'est créé avant la
 * validation. Un rôle **écarté** n'a toujours pas de case : il n'a ni playbook ni
 * autorisations tant qu'on ne l'a pas demandé, et la liste des écartés renvoie au
 * champ, qui est le geste réel (critère de #1159 : aucun texte ne promet un geste
 * que l'écran n'offre pas).
 *
 * ## Ce que l'écran ne décide pas
 *
 * Il ne **rédige** rien : les playbooks viennent de la proposition, écrits pour
 * ce projet par la mécanique de #257 (ou repris du gabarit, et la ligne le dit).
 * Il ne **traduit** aucune autorisation non plus : `politique` est reprise telle
 * que l'API l'a servie et repart telle quelle, pour que ce qui est écrit soit
 * exactement ce qui a été montré. Et il ne crée qu'à la validation — d'où un
 * bouton qui reste occupé, sans délai annoncé.
 */

import { type ReactNode, useEffect, useState } from "react";

import {
  BadgeEtat,
  Bouton,
  Carte,
  Champ,
  CLASSE_CONTROLE,
  EnTeteSection,
} from "@/components/Primitives";
import { corrigerEquipe, creerEquipe, proposerEquipe } from "@/lib/api";
import {
  appliquerCorrection,
  compteAgents,
  membresMontres,
  rolesValides,
} from "@/lib/equipe";
import type {
  AutorisationEquipe,
  ChoixOutillage,
  Projet,
  PropositionEquipe,
  RapportCreationEquipe,
  RefusProjet,
  RoleEquipe,
} from "@/lib/types";

import { refusDepuis, RefusMotive } from "./ExplorateurDossiers";

/** Le ton d'un cran d'autorisation — il appuie le sens, il ne le porte jamais seul. */
const TON_CRAN = {
  allow: "positif",
  ask: "attention",
  deny: "alerte",
} as const;

/**
 * Ce qu'un cran veut dire, en trois mots. Les crans sont écrits en anglais dans
 * la politique (`allow` / `ask` / `deny`) parce que c'est ce que le moteur lit
 * et ce que l'éditeur de permissions d'un agent affiche déjà (#262) : les
 * traduire ici donnerait deux vocabulaires pour la même chose. On les
 * **glose**, on ne les renomme pas.
 */
const SENS_CRAN: Record<string, string> = {
  allow: "passe sans rien demander",
  ask: "arbitré à chaque appel",
  deny: "refusé, toujours",
};

/**
 * Qui tranche un `ask` (#586, `maestro.decideur`). `auto` est le cas que le
 * ticket vise : *ce n'est pas la machine qui approuve*, c'est une décision prise
 * ici, à froid, et révocable — d'où une glose qui le dit plutôt qu'un mot seul.
 */
const SENS_DECIDEUR: Record<string, string> = {
  auto: "décidé d'avance par vous — l'appel passe, et il est tracé",
  humain: "une personne tranche, appel par appel",
};

/**
 * Une raison servie, ses `**…**` rendus en **gras** au lieu d'être recopiés.
 *
 * Les raisons d'autorisation (`maestro.equipe.proposition`) soulignent ce que la
 * personne garde avec la convention Markdown, et l'écran affichait les
 * astérisques tels quels (relevé par le regard neuf de #1159). Un nombre impair
 * de marqueurs laisse le texte intact : mieux vaut deux astérisques visibles
 * qu'une moitié de phrase mise en gras par erreur.
 */
export function avecGras(texte: string): ReactNode[] {
  const morceaux = texte.split("**");
  if (morceaux.length % 2 === 0) return [texte];
  return morceaux.map((morceau, index) =>
    index % 2 === 1 ? (
      <strong key={index} className="font-medium text-texte">
        {morceau}
      </strong>
    ) : (
      morceau
    ),
  );
}

/**
 * Une autorisation proposée, **dépliée** : le cran, l'outil, qui tranche, et la
 * raison. C'est la moitié « et pourquoi ? » du critère du ticket, et c'est
 * pourquoi elle n'est jamais derrière un pli.
 */
function LigneAutorisation({ autorisation }: { autorisation: AutorisationEquipe }) {
  const ton = TON_CRAN[autorisation.cran as keyof typeof TON_CRAN] ?? "neutre";
  const decideur = autorisation.decideur;
  return (
    <li className="flex flex-col gap-0.5">
      <span className="flex flex-wrap items-center gap-2">
        <BadgeEtat ton={ton} contour>
          {autorisation.cran}
        </BadgeEtat>
        <code className="font-mono text-annexe break-all text-texte">
          {autorisation.outil}
        </code>
        <span className="text-micro text-texte-secondaire">
          {SENS_CRAN[autorisation.cran] ?? autorisation.cran}
          {decideur !== null && (
            <>
              {" · "}
              <strong className="font-medium">{decideur}</strong>
              {SENS_DECIDEUR[decideur] !== undefined &&
                ` — ${SENS_DECIDEUR[decideur]}`}
            </>
          )}
        </span>
      </span>
      <span className="min-w-0 break-words text-annexe text-texte-secondaire">
        {avecGras(autorisation.raison)}
      </span>
    </li>
  );
}

/**
 * Un rôle proposé : ce qu'il est, **pourquoi**, d'où ça sort, ce qu'il branche
 * et ce qu'il aura le droit de faire.
 *
 * La grille est celle de `LigneEntree` (étape d'outillage, #1034) : le nom est
 * un enfant direct du `<label>` — c'est ce que le lint a11y cherche
 * (`label-has-associated-control`), et trois `<span>` empilés l'y cacheraient.
 *
 * Exportée depuis #1146 : la carte d'équipe du fil rend **la même ligne** dans son
 * détail, pour qu'un rôle se lise pareil aux deux endroits où l'on recrute.
 * `prefixe` fait les identifiants — les deux surfaces peuvent être à l'écran
 * ensemble (l'écran Projets et la colonne du fil), et deux cases de même `id`
 * feraient perdre son libellé à la seconde.
 */
export function LigneRole({
  role,
  retenu,
  basculer,
  instances,
  changerInstances,
  fige,
  prefixe = "equipe",
  ajoute = false,
}: {
  role: RoleEquipe;
  retenu: boolean;
  basculer: () => void;
  instances: number;
  changerInstances: (valeur: number) => void;
  fige: boolean;
  prefixe?: string;
  /**
   * Le rôle vient d'une demande de la personne, pas de la proposition (#1159) —
   * dit par un **mot** (« ajouté à votre demande »), jamais par la couleur seule.
   */
  ajoute?: boolean;
}) {
  const idCase = `${prefixe}-${role.nom}`;
  const idInstances = `${prefixe}-${role.nom}-instances`;
  return (
    <li
      className={[
        "flex flex-col gap-2 rounded-carte border border-bord p-3",
        // Les **deux** signaux du filet a11y : l'aplat ici, le libellé barré
        // plus bas. Un seul des deux serait la couleur seule.
        retenu ? "bg-surface" : "bg-surface-creuse",
      ].join(" ")}
    >
      <label
        htmlFor={idCase}
        className="grid cursor-pointer grid-cols-[auto_auto_1fr] items-start gap-x-2 gap-y-0.5"
      >
        <input
          id={idCase}
          type="checkbox"
          checked={retenu}
          disabled={fige}
          onChange={basculer}
          className="col-start-1 row-start-1 mt-1 size-4 shrink-0 rounded-controle border-bord-fort"
        />
        <span
          className={[
            "col-start-2 row-start-1 text-corps font-medium",
            retenu ? "text-texte" : "text-texte-secondaire line-through",
          ].join(" ")}
        >
          {role.role}
        </span>
        <span className="col-start-3 row-start-1 flex flex-wrap items-center gap-2">
          <BadgeEtat contour>{role.nom}</BadgeEtat>
          {ajoute && (
            <BadgeEtat ton="info" contour>
              ajouté à votre demande
            </BadgeEtat>
          )}
          {/* D'où ce rôle descend (docs/37 §2.1) : les agents figés sont
              devenus des gabarits, et la filiation se lit. Un rôle composé pour
              le besoin du projet hors des gabarits (#1159) n'en porte pas. */}
          {role.gabarit !== "" && (
            <BadgeEtat contour>gabarit {role.gabarit}</BadgeEtat>
          )}
          {/* Un playbook qui n'a pas été écrit pour ce projet se dit : celui
              d'un gabarit, ou l'esquisse d'un rôle qui n'en a pas (#1159). */}
          {role.playbook_origine !== "genere" && (
            <BadgeEtat ton="attention" contour>
              {role.playbook_origine === "esquisse"
                ? "playbook esquissé"
                : "playbook générique"}
            </BadgeEtat>
          )}
        </span>
        <span className="col-start-2 col-end-4 row-start-2 min-w-0 break-words text-annexe text-texte-secondaire">
          {role.raison}
        </span>
        {role.justification && (
          <span className="col-start-2 col-end-4 row-start-3 text-micro text-texte-secondaire">
            d&apos;après{" "}
            <code className="font-mono break-all">
              {role.justification.chemin}
            </code>
          </span>
        )}
      </label>

      {/* Ce qui suit n'est plus dans le `<label>` : un champ, une liste et un
          pli n'ont rien à faire dans l'étiquette d'une case à cocher — et un
          clic dessus basculerait la case. */}
      <div className="flex flex-col gap-2 pl-6">
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={idInstances} className="text-annexe text-texte">
            Instances
          </label>
          {/* `min` seulement, jamais de `max` : le plafond est une **décision**
              et elle vit en un seul endroit (`INSTANCES_MAX_CREEES`), qui la
              nomme dans son refus. Le recopier ici en ferait une seconde règle
              à tenir d'accord — et c'est toujours celle de l'écran qui dérive.
              « Moins d'une instance », lui, n'est pas une décision : c'est
              l'absence d'agent, qui se dit en décochant. */}
          {/* La largeur tient à l'**enveloppe** : `CLASSE_CONTROLE` porte
              `w-full`, qui l'emportait sur un `w-20` posé à côté — le champ
              prenait toute la ligne et renvoyait sa raison dessous (relevé à
              la relecture de #1146). */}
          <span className="w-20 shrink-0">
            <input
              id={idInstances}
              type="number"
              min={1}
              value={instances}
              disabled={fige || !retenu}
              onChange={(e) => changerInstances(Number(e.target.value))}
              className={CLASSE_CONTROLE}
            />
          </span>
          <span className="min-w-0 flex-1 break-words text-micro text-texte-secondaire">
            {role.raison_instances}
          </span>
        </div>

        {role.skills.length > 0 && (
          <p className="text-annexe text-texte-secondaire">
            <span className="font-medium text-texte">Skills branchés :</span>{" "}
            {role.skills.map((skill, index) => (
              <span key={skill.nom}>
                {index > 0 && " · "}
                <code className="font-mono break-all">{skill.nom}</code>
                {skill.etat === "a-generer" && " (à générer)"}
              </span>
            ))}
          </p>
        )}

        {/* Dépliées d'office : c'est le critère du ticket. Un rôle sans
            autorisation le **dit** — « aucune » et « on n'a pas regardé » ne
            doivent pas se ressembler. */}
        <div className="flex flex-col gap-1">
          <p className="text-annexe font-medium text-texte">Autorisations</p>
          {role.autorisations.length === 0 ? (
            <p className="text-annexe text-texte-secondaire">
              Aucune : ce rôle n&apos;exécute aucune commande, et rien
              d&apos;autre ne se décide d&apos;avance ici.
            </p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {role.autorisations.map((autorisation) => (
                <LigneAutorisation
                  key={`${autorisation.cran}-${autorisation.outil}`}
                  autorisation={autorisation}
                />
              ))}
            </ul>
          )}
        </div>

        <details className="text-annexe text-texte-secondaire">
          <summary className="min-h-6 cursor-pointer">
            Voir le playbook ({role.playbook_origine === "genere"
              ? "écrit pour ce projet"
              : role.playbook_origine === "esquisse"
                ? "une esquisse"
                : "celui du gabarit"}
            )
          </summary>
          <p className="mt-1 text-micro">{role.playbook_raison}</p>
          {/* `p-2.5` et non `p-2` : c'est le pas « compacte » du barème
              (#983), et un pas se choisit une fois. */}
          <pre className="mt-2 max-h-64 overflow-auto rounded-carte border border-bord bg-surface-creuse p-2.5 text-micro whitespace-pre-wrap">
            {role.playbook}
          </pre>
        </details>
      </div>
    </li>
  );
}

/**
 * Ce que la liste dit **avant** qu'on la lise : combien d'agents, lesquels, et
 * la promesse que rien n'est encore créé.
 *
 * Repris de l'`EnTeteListe` de l'étape d'outillage, pour la raison que le regard
 * neuf y avait relevée : sans compte en tête, le seul chiffre de l'écran est
 * celui du bouton, qu'on n'atteint qu'après avoir fait défiler toute la liste.
 */
function EnTeteListe({
  roles,
  retenus,
  instances,
  toutBasculer,
  fige,
}: {
  roles: RoleEquipe[];
  retenus: Set<string>;
  instances: Record<string, number>;
  toutBasculer: (retenir: boolean) => void;
  fige: boolean;
}) {
  const gardes = roles.filter((r) => retenus.has(r.nom));
  const total = gardes.reduce((somme, r) => somme + (instances[r.nom] ?? r.instances), 0);
  const retires = roles.length - gardes.length;
  return (
    <div className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-corps text-texte">
          {gardes.length === 0 ? (
            <span className="text-attention-texte">
              Aucun agent ne sera créé — tout a été retiré.
            </span>
          ) : (
            <>
              <strong>{compteAgents(total)}</strong>{" "}
              {total > 1 ? "seront créés" : "sera créé"} dans votre projet :{" "}
              {gardes
                .map((r) => {
                  const n = instances[r.nom] ?? r.instances;
                  return n > 1 ? `${r.role} ×${n}` : r.role;
                })
                .join(" · ")}
              .{" "}
              {retires > 0 && (
                <span className="text-attention-texte">
                  {retires} rôle{retires > 1 ? "s" : ""} retiré
                  {retires > 1 ? "s" : ""} par vous.
                </span>
              )}
            </>
          )}
        </p>
        <button
          type="button"
          disabled={fige}
          onClick={() => toutBasculer(gardes.length === 0)}
          className="min-h-6 text-annexe text-texte-secondaire underline underline-offset-2 hover:text-texte"
        >
          {gardes.length === 0 ? "tout remettre" : "tout retirer"}
        </button>
      </div>
      {/* La promesse, **avant** le geste : c'est ce qu'on veut savoir au moment
          de laisser un outil recruter pour soi. */}
      <p className="text-annexe text-texte-secondaire">
        Rien n&apos;est créé tant que vous n&apos;avez pas validé. Chaque
        autorisation ci-dessous est une décision que vous prenez maintenant, à
        froid — et qui se révoque depuis les écrans d&apos;agents du projet.
      </p>
    </div>
  );
}

/**
 * **Ce qui manque, dit avec ses mots** (#1159) — un champ, un bouton, et la phrase
 * que le modèle rend.
 *
 * ## La forme vient d'une veille et d'un choix consignés sur #1159
 *
 * Commentaires « Veille de conception » et « Variante retenue » : *CrewAI Crew
 * Studio* (un composeur contre l'équipe, dont ce qu'on dit atterrit sur l'équipe)
 * et *Cursor Plan Mode* (la proposition relue, puis corrigée, puis un seul geste
 * en pied). La retenue est **A — au pied de la liste**, choisie par le regard neuf
 * contre deux autres directions rendues sur la vraie stack. Ce qu'elle tranche :
 *
 * - **l'ordre de la question** : les rôles et leurs raisons, le repli des écartés,
 *   *puis* ce qui manque, *puis* « Créer l'équipe ». La variante qui mettait le
 *   champ en tête a été écartée — on y demandait ce qui manque avant d'avoir
 *   montré ce qui est là — comme celle qui en faisait une ligne de la liste, lue
 *   comme « ajouter une ligne » et placée avant les écartés ;
 * - **un seul champ, dans la carte** : ni colonne, ni fil à part — la
 *   conversation appartient au shell (#929), et la règle des trois places compte
 *   l'étape comme un bloc ;
 * - **la réponse tient en une ligne, détachée des boutons** (réserve du regard
 *   neuf) : elle se lit comme ce que Maestro a fait de la demande, y compris
 *   quand il n'a pas compris — le champ garde alors le texte, pour qu'on
 *   reformule ;
 * - **le bouton est secondaire** (`contour`) et dit l'acte : le seul geste qui
 *   crée reste « Créer l'équipe », en pied.
 */
function DemandeSurLEquipe({
  demande,
  changer,
  envoyer,
  enCours,
  fige,
  reponse,
  refus,
}: {
  demande: string;
  changer: (valeur: string) => void;
  envoyer: () => void;
  enCours: boolean;
  fige: boolean;
  reponse: string | null;
  refus: RefusProjet | null;
}) {
  return (
    <form
      aria-label="Corriger l'équipe avec vos mots"
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        envoyer();
      }}
    >
      <div className="flex flex-wrap items-end gap-2">
        <Champ
          id="equipe-demande"
          libelle={
            <>
              {/* La question pèse plus que le repli des écartés juste au-dessus
                  (réserve du regard neuf) : tokens du socle, pas de style à part. */}
              <span className="text-corps text-texte">
                Il manque quelqu&apos;un, ou un rôle ne convient pas ?
              </span>{" "}
              Dites-le avec vos mots.
            </>
          }
          placeholder="Par exemple : ajoute quelqu'un pour la sécurité"
          value={demande}
          onChange={(e) => changer(e.target.value)}
          disabled={fige}
          maxLength={500}
          className="min-w-0 flex-1 basis-72"
        />
        <Bouton
          type="submit"
          variante="contour"
          ton="neutre"
          occupe={enCours}
          disabled={fige || demande.trim() === ""}
        >
          {enCours ? "Composition…" : "Ajouter ou corriger"}
        </Bouton>
      </div>
      {/* Une surface en retrait, et non du texte courant : la phrase se lit
          comme la réponse à la demande, jamais comme une légende de la rangée
          de boutons qui suit (réserve du regard neuf). */}
      {reponse !== null && (
        <Carte
          balise="p"
          densite="compacte"
          ton="creuse"
          role="status"
          className="text-annexe text-texte"
        >
          <span className="font-medium">Maestro :</span> {reponse}
        </Carte>
      )}
      {refus && <RefusMotive refus={refus} titre="Demande non traitée" />}
    </form>
  );
}

/** Ce que la validation a créé — la liste, jamais un « ok » (docs/38 §4.2). */
function RapportCreation({ rapport }: { rapport: RapportCreationEquipe }) {
  return (
    <div className="flex flex-col gap-2">
      <p className="text-corps text-texte">
        <strong>{compteAgents(rapport.instances_total)}</strong>{" "}
        {rapport.instances_total > 1 ? "créés" : "créé"} dans votre projet
        {rapport.agents.length !== rapport.instances_total &&
          ` (${rapport.agents.length} rôle${rapport.agents.length > 1 ? "s" : ""})`}
        .
      </p>
      <ul className="flex flex-col gap-1 text-annexe text-texte-secondaire">
        {rapport.agents.map((agent) => (
          <li key={agent.nom}>
            <span className="font-medium text-texte">{agent.role}</span>{" "}
            (<code className="font-mono">{agent.nom}</code>) — {agent.instances}{" "}
            instance{agent.instances > 1 ? "s" : ""}
            {agent.skills.length > 0 && `, skills : ${agent.skills.join(", ")}`}
            {agent.politique !== null &&
              Object.keys(agent.politique.ask).length > 0 &&
              `, arbitrages : ${Object.entries(agent.politique.ask)
                .map(([outil, decideur]) => `${outil} (${decideur})`)
                .join(", ")}`}
          </li>
        ))}
      </ul>
      <p className="text-annexe text-texte-secondaire">
        L&apos;équipe se revoit et se modifie depuis les écrans d&apos;agents du
        projet — playbook, autorisations et capacité y sont éditables.
      </p>
    </div>
  );
}

export function EtapeEquipe({
  projet,
  choix = [],
  onTermine,
}: {
  projet: Projet;
  /**
   * Les réponses du questionnaire d'outillage, pour un projet **neuf** (#1031).
   *
   * Sans elles, l'équipe se dérive de l'**analyse** de la racine — ce qui est le
   * cas d'un projet existant. Les deux passent par la même dérivation ; c'est
   * l'origine qui change, pas la forme.
   */
  choix?: ChoixOutillage[];
  /** L'étape est finie — équipe créée, ou recrutement remis à plus tard. */
  onTermine: () => void;
}) {
  const [proposition, setProposition] = useState<PropositionEquipe | null>(null);
  const [retenus, setRetenus] = useState<Set<string>>(new Set());
  const [instances, setInstances] = useState<Record<string, number>>({});
  const [rapport, setRapport] = useState<RapportCreationEquipe | null>(null);
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  // La correction en langage naturel (#1159) : la demande en cours de saisie, la
  // phrase rendue par le modèle, et les lignes nées d'une demande.
  const [demande, setDemande] = useState("");
  const [reponse, setReponse] = useState<string | null>(null);
  const [ajoutes, setAjoutes] = useState<Set<string>>(new Set());
  const [enCorrection, setEnCorrection] = useState(false);
  const [refusCorrection, setRefusCorrection] = useState<RefusProjet | null>(null);

  useEffect(() => {
    // `vivant` plutôt qu'un `AbortController`, comme l'étape d'outillage : ce
    // qu'on protège n'est pas la requête mais l'écriture d'état sur un composant
    // démonté — le projet peut avoir changé sous nos pieds.
    let vivant = true;
    const partir = async () => {
      try {
        const rendue = await proposerEquipe(projet.id, choix);
        if (!vivant) return;
        setProposition(rendue);
        setRetenus(new Set(rendue.roles.map((r) => r.nom)));
        setInstances(
          Object.fromEntries(rendue.roles.map((r) => [r.nom, r.instances])),
        );
      } catch (erreur) {
        if (vivant) setRefus(refusDepuis(erreur));
      } finally {
        if (vivant) setChargement(false);
      }
    };
    void partir();
    return () => {
      vivant = false;
    };
    // `choix` est un tableau reconstruit à chaque rendu du parent : le mettre en
    // dépendance relancerait la proposition en boucle. Ce qui identifie la
    // demande est le projet.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projet.id]);

  const basculer = (nom: string) =>
    setRetenus((avant) => {
      const apres = new Set(avant);
      if (apres.has(nom)) apres.delete(nom);
      else apres.add(nom);
      return apres;
    });

  const toutBasculer = (retenir: boolean) =>
    setRetenus(
      retenir ? new Set((proposition?.roles ?? []).map((r) => r.nom)) : new Set(),
    );

  // La correction s'applique à **ce que l'écran montre** (cases et instances
  // comprises) et y atterrit : lignes ajoutées, décochées, recochées. Rien n'est
  // créé — la validation reste le seul geste qui écrit (#1040).
  const corriger = async () => {
    if (proposition === null || demande.trim() === "") return;
    setEnCorrection(true);
    setRefusCorrection(null);
    try {
      const montree = { roles: proposition.roles, retenus, instances };
      const correction = await corrigerEquipe(
        projet.id,
        demande,
        membresMontres(montree),
        choix,
      );
      const apres = appliquerCorrection(montree, correction);
      setProposition({ ...proposition, roles: apres.roles });
      setRetenus(new Set(apres.retenus));
      setInstances({ ...apres.instances });
      setAjoutes((avant) => new Set([...avant, ...apres.ajoutes]));
      setReponse(correction.reponse);
      // Le champ ne se vide que si quelque chose a changé : une demande que le
      // modèle n'a pas comprise reste là, pour qu'on la reformule.
      if (apres.change) setDemande("");
    } catch (erreur) {
      setRefusCorrection(refusDepuis(erreur));
    } finally {
      setEnCorrection(false);
    }
  };

  const valider = async () => {
    if (proposition === null) return;
    setEnCours(true);
    setRefus(null);
    try {
      // Ce qui repart est ce qui a été **montré** : playbook et `politique`
      // repris de la proposition, jamais recomposés ici (cf. l'en-tête). La
      // règle est partagée avec la carte d'équipe du fil (`lib/equipe`, #1146).
      const valides = rolesValides(proposition, retenus, instances);
      setRapport(await creerEquipe(projet.id, valides, proposition.id));
    } catch (erreur) {
      setRefus(refusDepuis(erreur));
    } finally {
      setEnCours(false);
    }
  };

  const roles = proposition?.roles ?? [];
  const pret = proposition !== null && !chargement;
  const total = roles
    .filter((r) => retenus.has(r.nom))
    .reduce((somme, r) => somme + (instances[r.nom] ?? r.instances), 0);

  return (
    <Carte
      balise="section"
      densite="aeree"
      aria-label={`Équipe de ${projet.nom}`}
      className="flex flex-col gap-4"
    >
      <EnTeteSection
        niveau={3}
        titre={`Équipe de « ${projet.nom} »`}
        aside={
          <span className="text-annexe text-texte-secondaire">
            {rapport === null ? "étape 3 sur 3" : "terminé"}
          </span>
        }
      />

      {rapport === null && (
        <p className="max-w-2xl text-annexe text-texte-secondaire">
          Votre projet n&apos;a encore <strong>aucun agent</strong>. Voici
          l&apos;équipe que son analyse appelle : chaque rôle avec ce qui le
          justifie, ce qu&apos;il branche de votre outillage et ce qu&apos;il
          aura le droit de faire. Retirez, ajustez, dites ce qui manque, puis
          validez — <strong>ou remettez à plus tard</strong> : le projet reste
          utilisable, et l&apos;équipe se crée depuis les écrans d&apos;agents.
        </p>
      )}

      {/* La phrase qu'on relit six mois plus tard à côté d'une équipe dont on se
          demande d'où elle sort. Elle compte ce que l'**analyse** a proposé :
          après une demande, elle le dit, pour ne pas contredire le compte de la
          liste (réserve du regard neuf, #1159). Composée par les règles des
          gabarits — le repli quand le modèle n'a pas abouti —, elle le dit
          aussi, avec sa cause : ce n'est pas une équipe jugée pour ce projet. */}
      {proposition !== null && proposition.resume !== "" && rapport === null && (
        <p className="text-annexe text-texte">
          <span className="font-medium">Ce que l&apos;analyse en déduit :</span>{" "}
          {proposition.resume}
          {reponse !== null && (
            <span className="text-texte-secondaire"> — avant vos demandes</span>
          )}
          {proposition.composition?.origine === "regles" &&
            proposition.composition.raison !== "" && (
              <span className="block text-attention-texte">
                {proposition.composition.raison}.
              </span>
            )}
        </p>
      )}

      {chargement && (
        <p className="text-corps text-texte-secondaire">
          Composition de l&apos;équipe — la racine est lue, puis un playbook est
          rédigé par rôle…
        </p>
      )}

      {proposition !== null && rapport === null && (
        <>
          <EnTeteListe
            roles={roles}
            retenus={retenus}
            instances={instances}
            toutBasculer={toutBasculer}
            fige={enCours}
          />
          <ul className="flex flex-col gap-2">
            {roles.map((role) => (
              <LigneRole
                key={role.nom}
                role={role}
                retenu={retenus.has(role.nom)}
                basculer={() => basculer(role.nom)}
                instances={instances[role.nom] ?? role.instances}
                changerInstances={(valeur) =>
                  setInstances((avant) => ({ ...avant, [role.nom]: valeur }))
                }
                fige={enCours || enCorrection}
                ajoute={ajoutes.has(role.nom)}
              />
            ))}
          </ul>
          {/* Ce que l'analyse a choisi de **ne pas** recruter : replié, mais
              présent. L'orchestrateur en fait toujours partie — c'est Maestro,
              et c'est lui qui recrute (docs/37 §4.2). */}
          {proposition.ecartes.length > 0 && (
            <details className="text-annexe text-texte-secondaire">
              <summary className="min-h-6 cursor-pointer">
                {proposition.ecartes.length} rôle
                {proposition.ecartes.length > 1 ? "s" : ""} écarté
                {proposition.ecartes.length > 1 ? "s" : ""}, et pourquoi
              </summary>
              <ul className="mt-2 flex flex-col gap-1">
                {proposition.ecartes.map((ecarte) => (
                  <li key={ecarte.nom}>
                    <span className="font-medium text-texte">{ecarte.role}</span>{" "}
                    — {ecarte.raison}
                  </li>
                ))}
              </ul>
              {/* Le geste, nommé à côté du contrôle qui le porte (#1159) : la
                  raison servie d'un écarté ne dit que le fait, parce qu'elle
                  sert aussi la carte du fil, qui n'a pas ce champ. Un rôle
                  écarté n'a ni playbook ni autorisations — c'est la demande qui
                  les fait composer, pour ce projet. */}
              <p className="mt-2">
                Pour en ajouter un quand même, dites-le ci-dessous : il sera
                composé pour votre projet, playbook compris.
              </p>
            </details>
          )}
          <DemandeSurLEquipe
            demande={demande}
            changer={setDemande}
            envoyer={() => void corriger()}
            enCours={enCorrection}
            fige={enCours || enCorrection}
            reponse={reponse}
            refus={refusCorrection}
          />
        </>
      )}

      {rapport !== null && <RapportCreation rapport={rapport} />}

      {refus && <RefusMotive refus={refus} titre="Équipe refusée" />}

      <div className="flex flex-wrap items-center gap-3">
        {rapport === null ? (
          <>
            <Bouton
              disabled={!pret || retenus.size === 0 || enCorrection}
              occupe={enCours}
              onClick={() => void valider()}
            >
              {enCours
                ? "Création…"
                : `Créer l'équipe${pret ? ` (${total})` : ""}`}
            </Bouton>
            <Bouton
              variante="contour"
              ton="neutre"
              disabled={enCours}
              onClick={onTermine}
            >
              Recruter plus tard
            </Bouton>
          </>
        ) : (
          <Bouton onClick={onTermine}>Terminer</Bouton>
        )}
      </div>
    </Carte>
  );
}
