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
 */

import { useRef, useState, type MouseEvent } from "react";

import {
  IconeAgent,
  IconeChrono,
  IconeJetons,
  IconePuce,
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
import { detailDe } from "@/lib/detailTache";
import {
  BadgeEtat,
  Carte,
  EnTeteSection,
  type Icone,
  type TonBadge,
} from "@/components/Primitives";
import {
  formatCout,
  formatDuree,
  formatHeure,
  formatTokens,
  libelleStatut,
} from "@/lib/format";
import type { EtatAgent, Projet, Tache } from "@/lib/types";

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
};

/**
 * Les colonnes du Kanban, dans l'ordre du flux de travail. Chaque statut porte
 * son **icône** en plus de son ton (#245) : la pastille de couleur seule ne
 * distinguait pas « bloquée » de « échec » pour qui ne sépare pas le violet du
 * rouge, et disparaissait à l'impression.
 */
const COLONNES: {
  statut: string;
  titre: string;
  ton: TonBadge;
  icone: Icone;
}[] = [
  {
    statut: "assignee",
    titre: "Assignées",
    ton: "info",
    icone: IconeStatutAssignee,
  },
  {
    statut: "en_cours",
    titre: "En cours",
    ton: "attention",
    icone: IconeStatutEnCours,
  },
  {
    statut: "bloquee",
    titre: "Bloquées",
    ton: "accent",
    icone: IconeStatutBloquee,
  },
  {
    statut: "terminee",
    titre: "Terminées",
    ton: "positif",
    icone: IconeStatutTerminee,
  },
  { statut: "echec", titre: "Échecs", ton: "alerte", icone: IconeStatutEchec },
];

/** Ouvre le panneau de détail sur une tâche, en retenant d'où on est parti. */
type Ouvrir = (tache: Tache, declencheur: HTMLElement | null) => void;

export function Kanban({
  taches,
  agents,
  reassigner,
  projet,
  messageVide,
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

  const connus = new Set(COLONNES.map((c) => c.statut));
  const autres = taches.filter((t) => !connus.has(t.statut));
  const colonnes = [
    ...COLONNES.map((colonne) => ({
      ...colonne,
      taches: taches.filter((t) => t.statut === colonne.statut),
    })),
    ...(autres.length > 0
      ? [
          {
            statut: "",
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
}: {
  tache: Tache;
  agents: EtatAgent[];
  reassigner: Reassigner;
  ouvrir: Ouvrir;
}) {
  const declencheur = useRef<HTMLButtonElement>(null);

  // Y a-t-il seulement quelque chose à ouvrir ? C'est le cas courant que non :
  // le lot modèle (#246) ne sert pas encore ces champs. La carte reste alors
  // strictement inerte — rien n'annonce un panneau qui serait vide.
  const ouvrable = !detailDe(tache).vide;
  const nom = tache.titre || tache.id;

  // Le sélecteur de réassignation et le lien du ticket externe gardent leur
  // geste : un clic dessus ne doit pas ouvrir le panneau par-dessus l'action
  // qu'on vient de lancer. Le titre, lui, est un vrai bouton — c'est par lui que
  // passent le clavier et les lecteurs d'écran.
  const surClicCarte = (evenement: MouseEvent<HTMLElement>) => {
    if (!ouvrable) return;
    if ((evenement.target as HTMLElement).closest("a, select, option, button")) {
      return;
    }
    ouvrir(tache, declencheur.current);
  };

  // La surface vient de `Carte` (#245, balise `article` par défaut) ; ne reste
  // ici que ce qui est propre à l'ouverture du panneau (#251) — le curseur et
  // le survol, posés **seulement** si la carte a un détail.
  return (
    <Carte
      densite="compacte"
      onClick={surClicCarte}
      className={
        "text-corps" +
        (ouvrable
          ? " cursor-pointer transition hover:border-neutral-300 hover:shadow dark:hover:border-neutral-700"
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
      <p className="chiffre mt-0.5 flex justify-between gap-2 text-annexe text-neutral-500 dark:text-neutral-400">
        <span>{libelleStatut(tache.statut)}</span>
        <span>
          {formatCout(tache.cout_usd)}
          {tache.horodatage ? ` · ${formatHeure(tache.horodatage)}` : ""}
        </span>
      </p>
      {tache.usage && (
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
          <span className="inline-flex items-center gap-1">
            <IconeChrono className="size-3.5 shrink-0" />
            {formatDuree(tache.usage.duree_ms)}
          </span>
        </Infobulle>
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
