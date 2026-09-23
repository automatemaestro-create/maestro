"use client";

/**
 * La vue Kanban des tâches par statut (docs/05 §2.2) et la **réassignation
 * manuelle** (EF-11/EF-20) : chaque carte porte un sélecteur d'agent qui
 * appelle `POST /api/taches/{id}/reassigner`. Les colonnes suivent la machine
 * à états du moteur (docs/03 §3) ; un statut inconnu du front tombe dans une
 * colonne « Autres » plutôt que de disparaître.
 *
 * Depuis #248, c'est **la section qui prend la place** : elle absorbe la hauteur
 * que la page lui laisse (le `<main>` du shell est une colonne flex, #117) et
 * chaque colonne défile chez elle. Sa largeur, elle, se règle par une **largeur
 * minimale de colonne** plutôt que par un nombre de colonnes : les colonnes
 * s'élargissent jusqu'à 2 560 px et se replient en lignes en dessous de la
 * largeur où elles tiennent toutes de front.
 *
 * Une carte qui porte un **détail** (description, étapes, liens — #246) l'ouvre
 * au clic dans un panneau (#251). La carte, elle, ne change pas : elle reste
 * l'objet dense qu'on lit en diagonale sur cinq colonnes, et une tâche sans
 * détail reste exactement la carte d'avant — pas de bouton, pas de curseur qui
 * promet une ouverture, pas de cadre vide.
 *
 * Une carte dont la tâche **travaille** porte son **signe de vie** (#837, lot 3
 * de #834) : le dernier geste de l'agent et son ancienneté, qui compte à la
 * seconde (`components/SigneDeVie`). La carte ne décide pas si elle en a un —
 * c'est la projection qui ne sert `activite` que sur une tâche `en_cours`
 * (#836), et le shell relit ses tâches en entier à chaque battement, jamais par
 * retouche locale : ce que la carte lit est toujours d'accord avec son statut.
 * Une tâche qui ne travaille pas rend donc la carte d'avant, au pixel près.
 *
 * ## Une carte qui dément sa colonne (#1111)
 *
 * Une tâche **arrêtée sur un humain** reste `en_cours` pour le moteur — il
 * n'émet pas encore `en_attente_validation`, et la table partagée
 * (`progression.py`) la compte en vol, à raison : elle est en vol. Elle tombe
 * donc dans la colonne « En cours », et c'est **ce qui ne bouge pas** : les
 * colonnes suivent la machine à états, les comptes restent ceux de #924.
 *
 * Ce qui bougeait, ce sont les **mots** : la carte disait « En cours »,
 * « Travaille depuis 1 min » et son dernier geste, pendant que le pipeline
 * disait « Attente humaine », l'en-tête « Validation en attente » et le tableau
 * de bord « aucun run ne travaille en ce moment ». Les comptes concordaient, le
 * vocabulaire non — et le Kanban était seul à dire qu'une tâche travaillait.
 *
 * D'où : la carte **dément sa colonne**. Elle prend la surface `attention` —
 * l'ambre que `Carte` accorde déjà au nœud de pipeline en attente humaine,
 * aucune couleur nouvelle —, porte le lien qui mène à l'écran des validations,
 * écrit « Attente humaine » dans la place où elle écrivait son statut, et tait
 * les deux marques de travail (signe de vie, chrono en vol). La question qu'elle
 * pose n'est pas la sienne : `lib/execution.tacheArreteeSurUnHumain` la pose une
 * fois, pour le Kanban comme pour le pipeline.
 *
 * Variante retenue sur pièces (regard neuf, commentaire « ## Variante retenue »
 * du ticket) contre deux autres : le mot seul, qui **taisait** sans démentir, et
 * la pastille d'état, qui repliait la ligne de statut sur deux lignes. La veille
 * qui l'a cadrée — Jira (la carte reste en colonne, son apparence dément),
 * Buildkite (glyphe pause et flèche vers le geste, jamais un indicateur de
 * course), GitHub Actions (« Waiting » est un statut, pas « In progress ») —
 * vit dans le commentaire « ## Veille de conception » du même ticket.
 */

import { useRef, useState, type MouseEvent } from "react";

import {
  GesteValidation,
  type ArbitrageSurPlace,
} from "@/components/CarteValidation";
import {
  IconeAgent,
  IconeChrono,
  IconeJetons,
  IconePuce,
  IconeStatutAFaire,
  IconeStatutAssignee,
  IconeStatutBloquee,
  IconeStatutEchec,
  IconeStatutEnCours,
  IconeStatutTerminee,
  IconeTache,
} from "@/components/Icones";
import { Infobulle } from "@/components/Infobulle";
import { LienTicketExterne } from "@/components/LienTicketExterne";
import { PanneauDetailTache } from "@/components/PanneauDetailTache";
import {
  SelecteurReassignation,
  type Reassigner,
} from "@/components/SelecteurReassignation";
import { ChronoEnVol, LigneSigneDeVie } from "@/components/SigneDeVie";
import { detailDe } from "@/lib/detailTache";
import {
  BadgeEtat,
  Carte,
  EnTeteSection,
  type Icone,
  type TonBadge,
} from "@/components/Primitives";
import { tacheArreteeSurUnHumain } from "@/lib/execution";
import {
  formatCout,
  formatDuree,
  formatHeure,
  formatTokens,
  libelleStatut,
} from "@/lib/format";
import {
  STATUT_EN_ATTENTE_VALIDATION,
  type EtatAgent,
  type Projet,
  type Tache,
} from "@/lib/types";

type Props = {
  taches: Tache[];
  agents: EtatAgent[];
  reassigner: Reassigner;
  /**
   * Le projet dont ces tâches sont les tâches (#281). Sert à **nommer le vide** :
   * un tableau sans carte doit dire de quel périmètre il est vide, sans quoi il
   * se lit « rien ne tourne » alors qu'il dit « rien ici ».
   */
  projet: Projet;
  /**
   * Ce que dit le tableau **vide**, quand le périmètre n'est pas le projet (#475).
   *
   * La phrase par défaut nomme le projet, ce qui est juste au tableau de bord et
   * faux dans la vue d'un run : « rien encore sur Dépensio » y désignerait le
   * mauvais vide — le projet peut être plein pendant que *ce* run n'a créé aucune
   * tâche, ce qui est l'état normal d'un run arrêté sur son brief. Un appelant qui
   * cadre autrement nomme donc son vide ; les autres n'ont rien à changer.
   */
  messageVide?: string;
  /**
   * Les tâches dont une demande de validation **dort** (#1111), telles que
   * `lib/execution.tachesEnAttenteDeValidation` les rend — la même valeur que
   * la vue pipeline reçoit sous le même nom, et pour la même raison : une
   * demande de validation porte sa tâche, jamais son run, et le statut servi ne
   * la dit pas.
   *
   * **Optionnelle**, et le défaut est « aucune » : le Kanban se monte aussi
   * hors de la vue d'un run, où les validations du projet ne sont pas à portée.
   * Un appelant qui ne la passe pas rend exactement la carte d'avant ce ticket
   * — jamais une carte qui *devine* une attente qu'on ne lui a pas dite.
   */
  enAttenteHumaine?: ReadonlySet<string>;
  /**
   * De quoi **trancher sans quitter le tableau** (#1228) : la demande qui dort
   * sur chaque tâche (`lib/validations.arbitragesEnAttente`) et le décideur.
   *
   * **Optionnelle**, comme sa voisine et pour la même raison : le Kanban se
   * monte aussi là où les validations ne sont pas à portée. Absente, la carte
   * dit qu'une tâche attend quelqu'un sans proposer de geste — c'est ce qu'elle
   * faisait avant ce ticket, moins le lien qui emmenait ailleurs.
   */
  arbitrer?: ArbitrageSurPlace;
};

/** Le défaut de `enAttenteHumaine` — hissé pour ne pas en recréer un par rendu. */
const AUCUNE_ATTENTE: ReadonlySet<string> = new Set<string>();

/**
 * Ce dont un clic **n'ouvre pas** le détail de la tâche. Les contrôles gardent
 * leur geste — un clic sur le sélecteur de réassignation ou sur un lien ne doit
 * pas ouvrir un panneau par-dessus l'action qu'on vient de lancer — et, depuis
 * #1228, **le dialogue que la carte a elle-même ouvert** : trancher une
 * validation sur place se fait dans un panneau monté dans la carte, et un clic
 * dedans y remonterait sans cette exception.
 */
const SANS_OUVERTURE = "a, select, option, button, [role='dialog']";

/**
 * Les colonnes du Kanban, dans l'ordre du flux de travail. Chaque statut porte
 * son **icône** en plus de son ton (#245) : la pastille de couleur seule ne
 * distinguait pas « bloquée » de « échec » pour qui ne sépare pas le violet du
 * rouge, et disparaissait à l'impression.
 */
const COLONNES: {
  /**
   * Les statuts que la colonne rassemble. Une **liste** et non un statut depuis
   * #924 : « À faire » en réunit deux (`backlog` et `prete`), que la machine à
   * états distingue par une question — les dépendances sont-elles levées ? — à
   * laquelle un tableau n'a pas à répondre. C'est déjà le rangement de
   * `progression.py` côté serveur, où les deux tombent dans `a_faire` ; les
   * séparer ici donnerait deux colonnes dont l'une serait toujours vide, le
   * moteur n'émettant ni l'un ni l'autre pour l'instant.
   */
  statuts: string[];
  titre: string;
  ton: TonBadge;
  icone: Icone;
}[] = [
  {
    // Les tâches que le **plan** a déclarées et que personne ne porte encore
    // (#924). Elles n'avaient aucune colonne, donc aucune place sur ce tableau :
    // un run de quatre tâches y montrait **une** carte pendant que son pipeline
    // en annonçait quatre (retex du 2026-09-11, G3). Elles arrivent en tête
    // parce que c'est le début du flux, et la colonne n'existe pas quand le
    // moteur n'a rien déclaré — comme les autres, elle se rend vide.
    statuts: ["backlog", "prete"],
    titre: "À faire",
    ton: "neutre",
    icone: IconeStatutAFaire,
  },
  {
    statuts: ["assignee"],
    titre: "Assignées",
    ton: "info",
    icone: IconeStatutAssignee,
  },
  {
    statuts: ["en_cours"],
    titre: "En cours",
    ton: "attention",
    icone: IconeStatutEnCours,
  },
  {
    statuts: ["bloquee"],
    titre: "Bloquées",
    // Le violet du badge, tel qu'il était rendu avant #912 — un **état** qui
    // emprunte le ton de la provenance. Le renommage n'a pas le droit de
    // changer le rendu ; lui donner un ton d'état est un autre ticket.
    ton: "provenance",
    icone: IconeStatutBloquee,
  },
  {
    statuts: ["terminee"],
    titre: "Terminées",
    ton: "positif",
    icone: IconeStatutTerminee,
  },
  { statuts: ["echec"], titre: "Échecs", ton: "alerte", icone: IconeStatutEchec },
];

/** Ouvre le panneau de détail sur une tâche, en retenant d'où on est parti. */
type Ouvrir = (tache: Tache, declencheur: HTMLElement | null) => void;

export function Kanban({
  taches,
  agents,
  reassigner,
  projet,
  messageVide,
  enAttenteHumaine = AUCUNE_ATTENTE,
  arbitrer,
}: Props) {
  // Le panneau est tenu **ici**, pas dans la carte : il est modal (une tâche à
  // la fois), et une carte est un `<article>` cliquable au fond d'une colonne
  // qui déroule — y imbriquer le dialogue le ferait hériter du clic de la carte
  // et de l'espacement de la colonne.
  const [ouverte, setOuverte] = useState<Tache | null>(null);
  const declencheur = useRef<HTMLElement | null>(null);

  const ouvrir: Ouvrir = (tache, depuis) => {
    declencheur.current = depuis;
    setOuverte(tache);
  };
  // Le focus revient à la carte d'où le panneau a été ouvert : sans quoi il
  // retomberait sur le document, en haut de page.
  const fermer = () => {
    setOuverte(null);
    declencheur.current?.focus();
  };

  // La tâche affichée suit le flux : le panneau reste ouvert sur la même carte
  // pendant qu'un run avance, avec des étapes qui se cochent sous les yeux.
  const affichee =
    ouverte === null ? null : (taches.find((t) => t.id === ouverte.id) ?? ouverte);

  const connus = new Set(COLONNES.flatMap((c) => c.statuts));
  const autres = taches.filter((t) => !connus.has(t.statut));
  const colonnes = [
    ...COLONNES.map((colonne) => ({
      ...colonne,
      taches: taches.filter((t) => colonne.statuts.includes(t.statut)),
    })),
    ...(autres.length > 0
      ? [
          {
            statuts: [],
            titre: "Autres",
            ton: "neutre" as TonBadge,
            icone: IconePuce,
            taches: autres,
          },
        ]
      : []),
  ];

  // #248 — les tâches sont l'objet du tableau de bord : elles en prennent la
  // place. La borne `max-h-96` de #191 protégeait la densité d'un écran qui
  // portait encore cinq panneaux de plein format ; ceux-ci sont partis avec le
  // même lot, et la borne est restée — sur un grand écran les tâches tenaient
  // dans le tiers supérieur pendant que le reste était vide.
  //
  // L'étirement est une **chaîne** : chaque maillon doit pouvoir rétrécir sous
  // son contenu (`min-h-0`, faute de quoi le `min-height:auto` d'un élément
  // flex l'en empêche) et prendre le reste (`flex-1`). Elle se pose donc en
  // entier ou pas du tout — un maillon manquant et le débordement remonte à la
  // zone de contenu au lieu de rester dans la colonne. Elle commence plus haut
  // que ce fichier : c'est le `<body>` (layout) qui pose la hauteur définie,
  // sans laquelle il n'y aurait rien à prendre.
  //
  // Pas de tâche, pas d'étirement : cinq colonnes « Aucune tâche. » n'ont pas
  // besoin de tout l'écran, et sans hauteur à partager la chaîne n'aurait rien
  // à distribuer.
  const etire = taches.length > 0;
  const maillon = etire ? "min-h-0 flex-1" : "";

  return (
    <section
      data-guide="kanban"
      aria-label="Tâches (Kanban)"
      // Plancher à `min-h-96` : sur une fenêtre courte, le tableau garde les
      // 24 rem qu'il avait avant ce lot et c'est la zone de contenu qui
      // défile, plutôt qu'un tableau écrasé à quelques pixels.
      className={`flex flex-col ${etire ? "min-h-96 flex-1" : ""}`}
    >
      <EnTeteSection titre="Tâches" icone={IconeTache} className="mb-2" />
      {/* Une largeur MINIMALE par colonne, pas un nombre de colonnes (#248) :
          au-delà, les colonnes s'élargissent jusqu'aux 2 560 px d'un grand
          écran ; en dessous, elles se replient en lignes au lieu d'être
          comprimées — l'ancien `md:grid-flow-col` les tassait toutes de front
          dès 768 px, où une carte n'avait plus que ~120 px.
          `auto-fit` et non `auto-fill` : les pistes que personne n'occupe se
          referment, sinon les colonnes réelles rétréciraient au profit de
          pistes vides. Aucune colonne rendue ne disparaît pour autant — elles
          portent toutes un élément, même sans tâche.
          `min(…,100%)` : sous 11,5 rem de large (mobile étroit), la piste suit
          le conteneur au lieu de le faire déborder.
          `minmax(11rem,1fr)` en hauteur de ligne : quand ça se replie, les
          lignes se partagent la hauteur — mais jamais en dessous de 11 rem,
          sans quoi elles se l'arrachent. Mesuré sur un mobile de 390 px, où
          les cinq colonnes s'empilent : 20 px de zone utile par colonne avec
          un simple `1fr`, 132 px avec ce plancher (et c'est la zone de contenu
          qui défile, ce qui est le bon compromis). */}
      <div
        className={`grid auto-rows-[minmax(11rem,1fr)] grid-cols-[repeat(auto-fit,minmax(min(11.5rem,100%),1fr))] gap-3 ${maillon}`}
      >
        {colonnes.map((colonne) => (
          <Carte
            balise="div"
            ton="creuse"
            densite="compacte"
            key={colonne.titre}
            /* Le fond, le bord, l'arrondi et la densité viennent de `Carte`
               (#245) ; ne reste ici que le maillon de la chaîne d'étirement
               (#248), qui est propre à cet écran. */
            className="flex min-h-0 min-w-0 flex-col"
          >
            <h3 className="mb-2 flex items-center gap-2 px-1 text-corps font-medium">
              <colonne.icone className="size-4 shrink-0 text-neutral-500 dark:text-neutral-400" />
              {colonne.titre}
              <BadgeEtat ton={colonne.ton} className="chiffre ml-auto">
                {colonne.taches.length}
              </BadgeEtat>
            </h3>
            {/* Chaque colonne défile chez elle (#191) plutôt que d'étirer la
                page — mais dans la hauteur que la fenêtre lui donne (#248) et
                non plus dans les 24 rem d'une borne fixe. */}
            <div className={`space-y-2 overflow-y-auto ${maillon}`}>
              {colonne.taches.map((tache) => (
                <CarteTache
                  key={tache.id}
                  tache={tache}
                  agents={agents}
                  reassigner={reassigner}
                  ouvrir={ouvrir}
                  attendUnHumain={tacheArreteeSurUnHumain(
                    tache,
                    enAttenteHumaine,
                  )}
                  arbitrer={arbitrer}
                />
              ))}
              {colonne.taches.length === 0 && (
                <p className="px-1 pb-1 text-annexe text-neutral-400 dark:text-neutral-600">
                  Aucune tâche.
                </p>
              )}
            </div>
          </Carte>
        ))}
      </div>
      {taches.length === 0 && (
        <p className="mt-2 text-corps text-neutral-500">
          {messageVide ??
            `Rien encore sur ${projet.nom} — les tâches apparaîtront dès qu'un run de ce projet publiera ses événements.`}
        </p>
      )}
      {affichee !== null && (
        <PanneauDetailTache
          tache={affichee}
          agents={agents}
          reassigner={reassigner}
          fermer={fermer}
        />
      )}
    </section>
  );
}

function CarteTache({
  tache,
  agents,
  reassigner,
  ouvrir,
  attendUnHumain,
  arbitrer,
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
  ouvrir: Ouvrir;
  /**
   * Cette tâche est-elle arrêtée sur quelqu'un (#1111) ? **Résolu par le
   * tableau**, pas par la carte : la question a une réponse
   * (`lib/execution.tacheArreteeSurUnHumain`) et une seule, et la laisser à la
   * carte la ferait reposer une fois par tâche rendue.
   */
  attendUnHumain: boolean;
  /** De quoi trancher cette attente sur place (#1228). */
  arbitrer?: ArbitrageSurPlace;
}) {
  const declencheur = useRef<HTMLButtonElement>(null);

  // Y a-t-il seulement quelque chose à ouvrir ? C'est le cas courant que non :
  // le lot modèle (#246) ne sert pas encore ces champs. La carte reste alors
  // strictement inerte — rien n'annonce un panneau qui serait vide.
  const ouvrable = !detailDe(tache).vide;
  const nom = tache.titre || tache.id;

  // Depuis combien de temps cette tâche travaille (#894). Le second temps
  // voyage dans le signe de vie, qui n'est servi que pour une tâche en cours :
  // la carte n'a donc aucune règle à rejouer — elle le montre quand elle l'a,
  // exactement comme la ligne du signe juste au-dessus.
  //
  // Une réserve de plus depuis #1111, et c'est **la même** que celle du nœud de
  // pipeline : une tâche arrêtée sur un humain ne « travaille » pas, donc les
  // deux temps se taisent ensemble. Le `sr-only` de `ChronoEnVol` dit
  // « Travaille », et c'était le mot de trop — « Travaille depuis 1 min » sur
  // une tâche que le reste de l'écran disait en attente.
  const travailleDepuis =
    (!attendUnHumain && tache.activite?.travaille_depuis) || null;

  // La ligne chrono porte **deux** durées selon le moment : celle d'une tâche
  // soldée (un fait figé) et, tant qu'elle travaille, le temps qu'elle y passe —
  // dans la place existante, qui affichait « — » en vol faute d'une durée que le
  // relevé en cours (#835) ne mesure pas. Les deux ne coexistent jamais : la
  // durée d'une tâche soldée n'arrive qu'à l'issue.
  //
  // Depuis #989, la durée soldée est le **travail** (`duree_execution_ms`) et
  // non l'horloge : une tâche restée douze minutes à attendre l'atelier de son
  // projet se lisait ici comme une tâche de treize minutes (retex du
  // 2026-09-11, G4). Les attentes se lisent **à part**, nommées, dans le
  // panneau de détail — la carte garde un seul chiffre, et c'est la variante
  // retenue de ce ticket : aucune des références (GitHub Actions, Buildkite,
  // GitLab CI) ne charge d'un second temps l'élément compact d'une liste.
  // Le repli sur `duree_ms` couvre un backend d'avant ce ticket.
  const chrono = (
    <span className="inline-flex items-center gap-1">
      <IconeChrono className="size-3.5 shrink-0" />
      {travailleDepuis !== null ? (
        <ChronoEnVol depuis={travailleDepuis} />
      ) : (
        <>
          {/* Le chiffre porte son mot : depuis #894 cette place rend déjà deux
              mesures de sens différent, et « 8 min 05 » de travail ne se lit
              plus comme « 8 min 05 » d'horloge. `ChronoEnVol` pose le sien. */}
          <span className="sr-only">Travail </span>
          {formatDuree(
            tache.usage?.duree_execution_ms ?? tache.usage?.duree_ms ?? null,
          )}
        </>
      )}
    </span>
  );

  // Le sélecteur de réassignation et le lien du ticket externe gardent leur
  // geste : un clic dessus ne doit pas ouvrir le panneau par-dessus l'action
  // qu'on vient de lancer. Le titre, lui, est un vrai bouton — c'est par lui que
  // passent le clavier et les lecteurs d'écran.
  const surClicCarte = (evenement: MouseEvent<HTMLElement>) => {
    if (!ouvrable) return;
    if ((evenement.target as HTMLElement).closest(SANS_OUVERTURE)) {
      return;
    }
    ouvrir(tache, declencheur.current);
  };

  // La surface vient de `Carte` (#245, balise `article` par défaut) ; ne reste
  // ici que ce qui est propre à l'ouverture du panneau (#251) — le curseur et
  // le survol, posés **seulement** si la carte a un détail.
  //
  // `attention` sur une tâche arrêtée sur un humain (#1111) : l'ambre que
  // `Carte` accorde déjà au nœud de pipeline en attente humaine, aucune couleur
  // nouvelle. C'est ce qui rend le démenti lisible **à distance** — la carte
  // reste dans « En cours », mais elle ne ressemble plus à ce qui y travaille.
  return (
    <Carte
      densite="compacte"
      ton={attendUnHumain ? "attention" : "pleine"}
      onClick={surClicCarte}
      className={
        "text-corps" +
        (ouvrable
          ? " cursor-pointer transition motion-reduce:transition-none hover:border-neutral-300 hover:shadow dark:hover:border-neutral-700"
          : "")
      }
    >
      {ouvrable ? (
        <button
          ref={declencheur}
          type="button"
          onClick={() => ouvrir(tache, declencheur.current)}
          aria-haspopup="dialog"
          aria-label={`Ouvrir le détail de la tâche ${nom}`}
          title={tache.id}
          className="w-full rounded text-left font-medium hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-sky-600 dark:focus-visible:outline-sky-400"
        >
          {nom}
        </button>
      ) : (
        <p className="font-medium" title={tache.id}>
          {nom}
        </p>
      )}
      {/* ⚠ **Renversement de #1228**, le même qu'au nœud de pipeline et pour
          les mêmes raisons : le geste était ailleurs et la carte y menait, sans
          chemin de retour. Ce qui reste vrai est qu'un arbitrage ne se **lit**
          pas dans 11 rem ; ce qui était faux est qu'il fallait pour autant
          quitter l'écran. Le panneau porte la lecture, la carte porte le geste.

          Il double le « Trancher » de la tête du run, à quelques centimètres
          au-dessus, et c'est assumé (réserve du regard neuf) : la tête dit
          **qu'**une tâche attend, la carte dit **laquelle** — et c'est ce qui
          manquait à l'écran, où l'attente s'annonçait en haut pendant que le
          tableau du bas montrait une tâche au travail. */}
      {attendUnHumain && (
        <div className="mt-1.5">
          <GesteValidation tacheId={tache.id} arbitrer={arbitrer} />
        </div>
      )}
      {/* Le ticket qui a motivé la tâche (#192) — absent : la carte est
          exactement celle d'avant, la marge partant avec le composant. */}
      <LienTicketExterne
        reference={tache.ticket}
        tache={tache.titre || tache.id}
        className="mt-1"
      />
      {/* « Agent » en toutes lettres : l'émoji 🤖 portait seul l'information,
          et une tâche non assignée ne disait pas de quoi elle manquait. */}
      <p className="mt-1 flex items-center gap-1 text-annexe text-neutral-500 dark:text-neutral-400">
        <IconeAgent className="size-3.5 shrink-0" />
        Agent {tache.agent || "non assigné"}
        {tache.role ? ` · ${tache.role}` : ""}
      </p>
      {/* Le signe de vie (#837) : servi seulement sur une tâche qui travaille
          — la carte le montre quand elle l'a, et n'en déduit rien sinon. Sous
          la réserve de #1111, qui est celle du nœud de pipeline : l'attente
          humaine l'emporte sur le signe comme elle l'emporte sur l'état, une
          tâche arrêtée sur quelqu'un ne « bougeant » pas quel qu'ait été son
          dernier geste. */}
      {!attendUnHumain && tache.activite && (
        <LigneSigneDeVie signe={tache.activite} className="mt-1" />
      )}
      <p className="chiffre mt-0.5 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400">
        {/* La place de l'état, et **un seul** mot dedans (#1111) : le statut
            servi dit « en cours » d'une tâche arrêtée sur un humain, parce que
            le moteur n'émet pas encore `en_attente_validation` et que la table
            partagée (`progression.py`) la compte en vol, à raison. La colonne
            garde donc ce rangement — c'est ce qui ne bouge pas —, et c'est le
            mot de la carte qui le dément. */}
        <span>
          {libelleStatut(
            attendUnHumain ? STATUT_EN_ATTENTE_VALIDATION : tache.statut,
          )}
        </span>
        <span>
          {formatCout(tache.cout_usd)}
          {tache.horodatage ? ` · ${formatHeure(tache.horodatage)}` : ""}
        </span>
      </p>
      {tache.usage ? (
        // La ventilation entrée/sortie ne se lit nulle part ailleurs : elle
        // passe donc du `title` à l'infobulle (#536).
        <Infobulle
          texte={`${formatTokens(tache.usage.tokens_entree)} tokens en entrée / ${formatTokens(tache.usage.tokens_sortie)} en sortie`}
          className="chiffre mt-0.5 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400"
        >
          <span className="inline-flex items-center gap-1">
            <IconeJetons className="size-3.5 shrink-0" />
            {formatTokens(tache.usage.tokens_total)} tokens
          </span>
          {chrono}
        </Infobulle>
      ) : (
        // Une tâche qui travaille **sans mesure d'usage** : le second temps a
        // quand même sa place. Le cas ne se produit pas côté serveur (le
        // `:debut` d'une tâche pose un usage vide), mais le contrat client
        // autorise `usage: null` — et faire dépendre « depuis combien de temps
        // elle travaille » de la présence d'un champ voisin serait un couplage
        // sans raison. Pas d'infobulle ici : il n'y a pas de ventilation à
        // décrire.
        travailleDepuis !== null && (
          <p className="chiffre mt-0.5 flex justify-end text-annexe text-neutral-500 dark:text-neutral-400">
            {chrono}
          </p>
        )
      )}
      <SelecteurReassignation
        tache={tache}
        agents={agents}
        reassigner={reassigner}
        className="mt-2"
      />
    </Carte>
  );
}
