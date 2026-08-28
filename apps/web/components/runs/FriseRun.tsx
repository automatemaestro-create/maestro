"use client";

/**
 * La **frise d'activité** d'un run (#355) : ce que les agents font et se disent,
 * pendant qu'ils le font.
 *
 * La quatrième lecture d'un run, et celle qui manquait. Le pipeline dit « quoi
 * après quoi », le Kanban « combien dans quel état », le journal « qu'a-t-il
 * fait » — aucune ne dit **qui, quand, et à qui**. Entre les deux compteurs et
 * le rapport final, une attente de décision humaine était indiscernable d'un
 * travail en cours : 53 minutes perdues sur un run le 14 août sans qu'aucun
 * écran ne le dise.
 *
 * ## Un tableau, et pourquoi c'en est un
 *
 * Une frise en couloirs est un croisement à deux entrées — le **temps** en
 * lignes, les **agents** en colonnes —, et c'est exactement ce qu'un `<table>`
 * décrit. Le gain n'est pas cosmétique : l'association `<th scope="col">` fait
 * que **chaque entrée porte son agent** sans qu'on ait à le réécrire sur chaque
 * carte (premier critère), pour tout le monde et sans effort — un lecteur
 * d'écran annonce la colonne en entrant dans la cellule. Une grille de `<div>`
 * aurait dessiné la même chose en perdant ce qui la rend lisible.
 *
 * Le tableau **défile horizontalement dans son propre conteneur** : un run à six
 * agents ne doit pas faire déborder la page (règle du dépôt sur le contenu
 * large).
 *
 * ## Le cinquième tableau d'apparence, et sa raison
 *
 * Le dépôt en compte quatre (colonne de Kanban, nœud de pipeline, segment de
 * barre, badge de run), chacun pour un motif écrit. Celui-ci en est un
 * cinquième, et son motif est que sa **population n'est pas la même** : la frise
 * range des *entrées* et non des tâches, donc elle rencontre deux choses
 * qu'aucun des quatre autres ne rencontre — un **message**, qui n'a pas de
 * statut de tâche du tout, et l'issue d'une **validation** (`approuve` /
 * `refuse`). Elle a en outre une contrainte à elle : les trois états du
 * troisième critère — bloquée, attente humaine, en cours — doivent se
 * distinguer **à l'œil**, ce qu'aucune des quatre autres tables n'a à garantir.
 *
 * Les tons des états partagés sont **repris à l'identique** du pipeline
 * (`VuePipeline`) : une tâche lue « bloquée » en violet sur un onglet et en
 * rouge sur l'autre serait une tâche dont on doute.
 *
 * ## Ce qui n'est pas recalculé ici
 *
 * Le tri (instant puis rang du journal), le couloir de chaque entrée et le
 * statut résolu viennent du backend (`maestro/controltower/frise.py`). Le front
 * range les entrées dans leur colonne et les habille ; il ne décide ni de
 * l'ordre, ni de qui appartient à qui — c'est la même doctrine que le graphe
 * (#490), et elle vaut ici pour une raison de plus : `en_attente_validation`
 * n'existe que parce que l'agrégat le résout depuis `validation.demande`, et le
 * redéduire du fil des validations côté client ferait deux règles à tenir
 * d'accord.
 */

import type { ReactNode } from "react";

import {
  IconeActivite,
  IconeArbitrage,
  IconeMessage,
  IconePuce,
  IconeStatutBloquee,
  IconeStatutEchec,
  IconeStatutEnCours,
  IconeStatutTerminee,
} from "@/components/Icones";
import {
  BadgeEtat,
  EnTeteSection,
  EtatVide,
  type Icone,
  type TonBadge,
} from "@/components/Primitives";
import { formatHeure, libelleStatut } from "@/lib/format";
import type { CouloirFrise, EntreeFrise, FriseRun as Frise } from "@/lib/types";
import { COULOIR_REPLI, STATUT_EN_ATTENTE_VALIDATION } from "@/lib/types";
import { useFriseRun } from "@/lib/useFriseRun";

/** Le nom affiché du couloir de repli — ce qu'aucun agent ne porte. */
const LIBELLE_REPLI = "Sans agent";

/**
 * Ce que la ligne d'un message vaut dans la table des apparences : un `statut`
 * vide, parce qu'un message n'est pas un état de tâche (`frise.py`). La clé est
 * nommée plutôt qu'écrite `""` sur place — une chaîne vide en indice de table se
 * relit mal.
 */
const STATUT_MESSAGE = "";

type ApparenceEntree = {
  ton: TonBadge;
  icone: Icone;
  /**
   * La carte est-elle **teintée** ? Une seule l'est — l'attente humaine —, comme
   * le pipeline n'accorde sa seule teinte qu'à `attente_humain` et `fondDe` au
   * seul régime suspendu d'un run. Teinter davantage reviendrait à ne rien
   * signaler.
   */
  teintee?: boolean;
  /** La pastille bat — et seulement pour ce qui travaille vraiment. */
  pulse?: boolean;
};

const APPARENCE: Record<string, ApparenceEntree> = {
  [STATUT_MESSAGE]: { ton: "neutre", icone: IconeMessage },
  en_cours: { ton: "info", icone: IconeStatutEnCours, pulse: true },
  [STATUT_EN_ATTENTE_VALIDATION]: {
    ton: "attention",
    icone: IconeArbitrage,
    teintee: true,
  },
  bloquee: { ton: "accent", icone: IconeStatutBloquee },
  terminee: { ton: "positif", icone: IconeStatutTerminee },
  echec: { ton: "alerte", icone: IconeStatutEchec },
  approuve: { ton: "positif", icone: IconeArbitrage },
  refuse: { ton: "alerte", icone: IconeArbitrage },
};

const APPARENCE_INCONNUE: ApparenceEntree = { ton: "neutre", icone: IconePuce };

function apparence(statut: string): ApparenceEntree {
  return APPARENCE[statut] ?? APPARENCE_INCONNUE;
}

/**
 * Les trois états que la légende nomme, et **seulement** eux : ce sont ceux du
 * troisième critère. Une légende qui listerait les huit apparences se lirait
 * comme un inventaire ; celle-ci dit ce qu'on est venu chercher.
 */
const LEGENDE: string[] = [
  "en_cours",
  STATUT_EN_ATTENTE_VALIDATION,
  "bloquee",
];

export function FriseRun({
  runId,
  revision,
  messageVide,
}: {
  runId: string;
  /** Le pouls du shell : une lecture de la frise par battement. */
  revision: number;
  /** Ce que dit la frise **vide**, nommé par l'appelant comme pour le Kanban. */
  messageVide: string;
}) {
  const { frise, chargement, erreur } = useFriseRun(runId, revision);

  if (erreur !== null && frise === null) {
    return (
      <SectionFrise>
        <EtatVide
          icone={IconeActivite}
          message={`La frise de ce run n'a pas pu être lue : ${erreur}`}
        />
      </SectionFrise>
    );
  }

  if (frise === null) {
    return (
      <SectionFrise>
        <p className="text-corps text-neutral-500">
          {chargement ? "Chargement de la frise…" : messageVide}
        </p>
      </SectionFrise>
    );
  }

  if (frise.entrees.length === 0) {
    return (
      <SectionFrise>
        <EtatVide icone={IconeActivite} message={messageVide} />
      </SectionFrise>
    );
  }

  const repli = frise.couloirs.find((couloir) => couloir.repli);

  return (
    <SectionFrise
      aside={
        <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
          {frise.entrees.length} entrée{frise.entrees.length > 1 ? "s" : ""}
          {frise.tronquee ? ` sur ${frise.total}` : ""} ·{" "}
          {frise.couloirs.length} couloir
          {frise.couloirs.length > 1 ? "s" : ""}
        </span>
      }
    >
      <Legende />

      {/* Le contenu large défile chez lui, jamais en poussant la page. */}
      <div className="overflow-x-auto">
        <TableFrise frise={frise} />
      </div>

      {/* La borne se dit toujours : une frise qui rendrait ses dernières lignes
          en silence ferait passer un run d'une heure pour un run court. Et elle
          renvoie là où le reste se lit, plutôt que de laisser chercher. */}
      {frise.tronquee && (
        <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
          Les {frise.entrees.length} entrées les plus récentes sur {frise.total} —
          l&apos;onglet Journal porte l&apos;historique complet de ce run.
        </p>
      )}

      {/* Un couloir « Sans agent » n'est pas un bug d'affichage : le moteur
          consigne un tiret sur une tâche jamais routée. Le dire ici évite de le
          faire chercher — c'est le couloir des tâches bloquées. */}
      {repli && (
        <p className="mt-2 text-annexe text-neutral-500 dark:text-neutral-400">
          Le couloir « {LIBELLE_REPLI} » recueille ce qu&apos;aucun agent ne
          porte — une tâche bloquée n&apos;a jamais été routée, donc jamais
          attribuée.
        </p>
      )}
    </SectionFrise>
  );
}

function SectionFrise({
  children,
  aside,
}: {
  children: ReactNode;
  aside?: ReactNode;
}) {
  return (
    <section data-guide="frise" aria-label="Frise d'activité du run">
      <EnTeteSection
        titre="Frise"
        icone={IconeActivite}
        className="mb-2"
        aside={aside}
      />
      {children}
    </section>
  );
}

/**
 * La légende des trois états du troisième critère.
 *
 * Elle n'est pas décorative : « bloquée » et « en attente d'un humain » se
 * ressemblent en ceci qu'aucune des deux n'avance, et c'est précisément la
 * confusion qui a coûté les 53 minutes. Les nommer côte à côte est la moitié du
 * remède ; les distinguer par la couleur et l'icône en est l'autre.
 */
function Legende() {
  return (
    <ul className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      {LEGENDE.map((statut) => (
        <li key={statut}>
          <Badge statut={statut} />
        </li>
      ))}
    </ul>
  );
}

function Badge({ statut }: { statut: string }) {
  const { ton, icone, pulse } = apparence(statut);
  return (
    <BadgeEtat ton={ton} icone={icone} pulse={pulse}>
      {libelle(statut)}
    </BadgeEtat>
  );
}

/**
 * Le libellé d'une entrée : celui du statut, ou « Message » pour un échange.
 *
 * `libelleStatut` n'est **pas** appelé ici avec une chaîne vide : il rendrait la
 * chaîne vide (son repli est le statut brut), donc un badge sans texte — et la
 * règle du dépôt veut qu'une pastille porte toujours du texte, la couleur ne
 * portant jamais le sens seule.
 */
function libelle(statut: string): string {
  return statut === STATUT_MESSAGE ? "Message" : libelleStatut(statut);
}

function TableFrise({ frise }: { frise: Frise }) {
  return (
    <table className="w-full min-w-max border-separate border-spacing-x-2 border-spacing-y-1 text-annexe">
      <caption className="sr-only">
        Frise d&apos;activité du run {frise.run_id} : {frise.entrees.length}{" "}
        entrée(s) dans l&apos;ordre du temps, une colonne par agent.
      </caption>
      <thead>
        <tr>
          <th
            scope="col"
            className="text-left font-medium text-neutral-500 dark:text-neutral-400"
          >
            Heure
          </th>
          {frise.couloirs.map((couloir) => (
            <th
              key={couloir.agent || COULOIR_REPLI}
              scope="col"
              className="min-w-48 text-left font-medium text-neutral-700 dark:text-neutral-300"
            >
              <EnTeteCouloir couloir={couloir} />
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {frise.entrees.map((entree) => (
          <LigneFrise
            key={entree.id}
            entree={entree}
            couloirs={frise.couloirs}
          />
        ))}
      </tbody>
    </table>
  );
}

function EnTeteCouloir({ couloir }: { couloir: CouloirFrise }) {
  return (
    <>
      <span className="block truncate">
        {couloir.repli ? LIBELLE_REPLI : couloir.agent}
      </span>
      {couloir.role && (
        <span className="block truncate text-micro font-normal text-neutral-500 dark:text-neutral-400">
          {couloir.role}
        </span>
      )}
      <span className="sr-only">
        {couloir.entrees.length} entrée(s) dans ce couloir
      </span>
    </>
  );
}

/**
 * Une ligne de la frise : un instant, et l'entrée posée dans **sa** colonne.
 *
 * Les autres cellules restent vides — c'est ce qui fait la lecture en couloirs :
 * l'œil suit une colonne pour savoir ce qu'un agent a fait, et une ligne pour
 * savoir ce qui s'est passé à cet instant.
 */
function LigneFrise({
  entree,
  couloirs,
}: {
  entree: EntreeFrise;
  couloirs: CouloirFrise[];
}) {
  return (
    <tr>
      <th
        scope="row"
        className="chiffre align-top text-left font-normal whitespace-nowrap text-neutral-500 dark:text-neutral-400"
      >
        <time dateTime={entree.horodatage}>
          {formatHeure(entree.horodatage)}
        </time>
      </th>
      {couloirs.map((couloir) => (
        <td key={couloir.agent || COULOIR_REPLI} className="align-top">
          {couloir.agent === entree.couloir && <CarteEntree entree={entree} />}
        </td>
      ))}
    </tr>
  );
}

/**
 * Ce qu'une entrée montre **sans qu'on l'ouvre** : son état, ce sur quoi elle
 * porte, ce qu'elle dit. C'est le troisième critère pris au mot — « à l'œil,
 * sans ouvrir de détail ».
 */
function CarteEntree({ entree }: { entree: EntreeFrise }) {
  const { teintee } = apparence(entree.statut);
  const fond = teintee
    ? "border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/40"
    : "border-neutral-200 bg-white dark:border-neutral-800 dark:bg-neutral-900";
  return (
    <div className={`rounded-md border p-1.5 ${fond}`}>
      <Badge statut={entree.statut} />
      {entree.titre && (
        <p className="mt-1 line-clamp-2 font-medium text-neutral-800 dark:text-neutral-200">
          {entree.titre}
        </p>
      )}
      {/* L'objet n'est répété que s'il apprend autre chose que le titre :
          l'issue réussie d'une tâche retombe sur son titre côté serveur, et
          l'afficher deux fois ne dirait rien de plus. */}
      {entree.objet && entree.objet !== entree.titre && (
        <p className="mt-0.5 line-clamp-3 text-neutral-600 dark:text-neutral-400">
          {entree.objet}
        </p>
      )}
    </div>
  );
}
