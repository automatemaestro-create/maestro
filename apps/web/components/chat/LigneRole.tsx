"use client";

/**
 * **Un rôle proposé**, tel qu'on le relit avant de recruter (#1040) : ce qu'il est,
 * **pourquoi**, d'où ça sort, ce qu'il branche et ce qu'il aura le droit de faire.
 *
 * Née à l'étape d'équipe du parcours de création (`EtapeEquipe`), que #1161 a
 * laissée sans écran et que #1331 a retirée : la carte d'équipe du fil
 * (`EquipeDansLeFil`, #1146) est désormais **la seule** surface qui recrute, et
 * c'est elle qui rend cette ligne dans son détail replié.
 *
 * ## La forme vient d'une veille et d'un choix consignés sur #1040
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1040 : trois
 * références **vérifiées en direct** — *AWS IAM* (l'étape « Review and create »
 * d'une création de rôle), *Renovate* (la PR d'onboarding) et *GitHub*
 * (l'installation d'une App tierce). La retenue est **A — l'inventaire à plat,
 * autorisations dépliées d'office**. Ce qu'elle tranche pour la ligne, et qu'on ne
 * défait pas sans rejouer le même geste :
 *
 * - **une ligne par rôle, qui porte sa raison ET l'endroit qui la prouve** —
 *   d'après *Renovate* (« Detected Package Files » : un fichier par ligne avec ce
 *   qui l'a trahi). Une carte par rôle a été écartée : elle ferait un bloc de plein
 *   format par membre de l'équipe ;
 * - **les autorisations sont dépliées d'office**, en une ligne *cran + outil +
 *   décideur*, la raison dessous — d'après *GitHub*, qui dit avant toute
 *   installation ce que l'App a demandé, lu comme niveau + objet. La variante qui
 *   les repliait derrière un `<details>` a été écartée **par le critère du ticket
 *   lui-même** (« chaque permission `auto` étant montrée ») : un cran `auto` que
 *   personne n'a lu n'est décidé par personne (#716). La carte du fil, qui replie
 *   le détail entier, nomme donc ses `auto` dans son récapitulatif ;
 * - **tout arrive retenu, corriger c'est décocher** — d'après *GitHub* « select or
 *   deselect » ; ajouter ou changer un rôle se **dit** (#1159, `DemandeSurLEquipe`),
 *   et un rôle né d'une demande le dit par un mot (`ajoute`) ;
 * - **une ligne retirée perd son aplat ET son libellé est barré** : l'état ne tient
 *   jamais à la seule couleur (docs/30 §1, filet a11y).
 *
 * Restent repliés : le **playbook** (long par nature).
 *
 * ## Ce que la ligne ne décide pas
 *
 * Elle ne **rédige** rien : le playbook vient de la proposition, écrit pour ce
 * projet par la mécanique de #257 (ou repris du gabarit, et la ligne le dit). Elle
 * ne **traduit** aucune autorisation non plus : `politique` repart telle que l'API
 * l'a servie (`rolesValides`), pour que ce qui est écrit soit exactement ce qui a
 * été montré.
 */

import type { ReactNode } from "react";

import { BadgeEtat, CLASSE_CONTROLE } from "@/components/Primitives";
import type { AutorisationEquipe, RoleEquipe } from "@/lib/types";

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
 * Qui tranche un `ask` (#586, `maestro.decideur`). `auto` est le cas que #1040
 * vise : *ce n'est pas la machine qui approuve*, c'est une décision prise ici, à
 * froid, et révocable — d'où une glose qui le dit plutôt qu'un mot seul.
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
 * raison. C'est la moitié « et pourquoi ? » du critère de #1040, et c'est
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
 * La ligne d'un rôle. La grille est celle de l'ancienne étape d'outillage
 * (#1034) : le nom est un enfant direct du `<label>` — c'est ce que le lint a11y
 * cherche (`label-has-associated-control`), et trois `<span>` empilés l'y
 * cacheraient.
 *
 * `prefixe` fait les identifiants : la carte du fil peut être à l'écran deux fois
 * (la page `/chat` et la colonne de conversation), et deux cases de même `id`
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
          {/* Un rôle ajouté puis retiré ne se dit plus « ajouté » à côté de son
              nom barré (✗ du regard neuf, relecture de #1331) : les deux gestes
              sont nommés, dans l'ordre. */}
          {ajoute && (
            <BadgeEtat ton={retenu ? "info" : "neutre"} contour>
              {retenu ? "ajouté à votre demande" : "ajouté, puis retiré"}
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
          {/* La raison servie justifie le nombre **proposé** : une fois ce nombre
              changé — au champ, ou par une demande en mots (« deux développeurs »,
              #1331) —, elle contredirait le compteur à côté d'elle (vu sur la
              vraie stack : « 3 » à côté de « une instance : un seul travail… »).
              « À votre demande » et non « par vous » : dit en mots, le nombre est
              celui que le modèle a compris, et sa réponse dit lequel (regard neuf). */}
          <span className="min-w-0 flex-1 break-words text-micro text-texte-secondaire">
            {instances === role.instances
              ? role.raison_instances
              : `Ajusté à votre demande — la proposition en prévoyait ${role.instances}.`}
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

        {/* Dépliées d'office : c'est le critère de #1040. Un rôle sans
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
