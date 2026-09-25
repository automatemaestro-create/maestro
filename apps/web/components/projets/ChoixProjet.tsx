"use client";

/**
 * La porte d'entrée de la Control Tower (#279, lot 3 de #276) : tant qu'aucun
 * projet n'est actif, c'est **cet** écran qu'on voit — pas le tableau de bord.
 *
 * Quatre partis pris, qui expliquent sa forme :
 *
 * 1. **la porte remplace la Control Tower, elle ne s'y ajoute pas** — ni
 *    sidebar, ni barre supérieure, ni flux temps réel derrière elle. Montrer le
 *    cadre d'un projet qu'on n'a pas encore choisi serait exactement ce que le
 *    chantier corrige : « personne ne voit la Control Tower sans savoir de quel
 *    projet elle parle » ;
 * 2. **la création est proposée sur place** — depuis #1294, **dans la
 *    conversation** (`NaissanceProjet`, docs/43 §2.2) et non plus par le
 *    formulaire de l'écran Projets : « Nouveau projet » fait passer la porte en
 *    mode création, une question en titre et le fil de l'orchestration dessous.
 *    Aucun projet déclaré : ce mode est déjà ouvert, parce qu'un écran qui n'a
 *    rien à lister n'a qu'une chose à proposer, et l'ouvrir d'office évite un
 *    clic qui n'a pas d'alternative ;
 * 3. **on n'invente jamais l'absence** : une API muette dit sa panne et offre de
 *    réessayer, elle ne se lit pas « aucun projet » (même règle que la liste de
 *    #225, pour la même raison — inviter à re-déclarer des projets déjà là) ;
 * 4. **la page demandée est nommée, pas oubliée** : la garde ne redirige pas,
 *    donc l'URL ne bouge pas. Le dire à l'écran fait de cette propriété quelque
 *    chose qui se voit — on sait où l'on retombe avant de choisir.
 *
 * ⚠ Le cinquième de #1034 — « déclarer ne fait plus entrer », l'étape
 * d'outillage en second écran de la porte — est **renversé** par #1294 (docs/43
 * §2.2, qui renverse docs/37 §4 point 6) : le projet né dans la conversation est
 * ouvert tout de suite, et son outillage se construit dans la même conversation
 * (#1161), pièce par pièce, au lieu d'une étape de formulaire.
 */

import { usePathname } from "next/navigation";
import { useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconePlus } from "@/components/Icones";
import { LogoMaestro } from "@/components/Logo";
import { BadgeEtat, Bouton, classesCarte } from "@/components/Primitives";
import { useProjetActif } from "@/lib/etatProjetActif";
import { naissanceDemandee } from "@/lib/naissance";
import { entreeCourante } from "@/lib/navigation";
import { libelleOrigine } from "@/lib/projets";
import type { Projet } from "@/lib/types";

import { RefusMotive } from "./ExplorateurDossiers";
import { NaissanceProjet } from "./NaissanceProjet";

const CLASSE_CADRE =
  "mx-auto flex w-full max-w-2xl flex-col gap-6 px-4 py-10 sm:py-16";

/**
 * Le cadre des deux écrans de la porte — et, depuis #306, **son ascenseur**.
 *
 * La porte est rendue par la garde du shell, donc **au-dessus** du cadre
 * applicatif : son `<main>` est fils direct d'un `<body>` que #248 a mis en
 * `h-full … overflow-hidden` — une hauteur *définie*, pour que le Kanban ait
 * quelque chose à prendre. Le défilement a alors été confié à la colonne de
 * contenu du shell, que la porte ne traverse jamais : ce qui dépassait était
 * simplement rogné, et le bas du formulaire de création — ses boutons compris —
 * devenait inatteignable sur une fenêtre courte ou l'explorateur déplié.
 *
 * ⚠ Le symptôme ne se reproduit **pas** en JavaScript : `overflow: hidden` sur
 * le `<body>` remonte au **viewport** (le `<html>` étant en `visible`, c'est le
 * body qui le lui donne), et un viewport en `hidden` reste défilable *par
 * programme*. Mesuré avant/après sur la même page : `window.scrollTo(0, 99999)`
 * amenait bien le bouton à l'écran, la touche `End` ne bougeait rien. Vérifier
 * ce genre de correction par un `scrollTo` conclurait donc à tort que tout va
 * bien — c'est la molette, l'ascenseur et le clavier qui n'avaient rien.
 *
 * Deux détails portent la correction :
 *
 * - le conteneur défilant **n'est pas le `<main>`** : celui-ci reste centré en
 *   `max-w-2xl`, si bien qu'un `overflow-y-auto` posé dessus mettrait
 *   l'ascenseur au bord de la colonne. Il est ici au bord de l'écran — même
 *   choix que la colonne de contenu du shell, pour la même raison ;
 * - `min-h-0` avec `flex-1` (#248) : sans lui, le `min-height:auto` par défaut
 *   interdit au conteneur de rétrécir sous son contenu, il se dimensionne sur
 *   le formulaire, et l'`overflow-y-auto` n'a jamais rien à faire défiler.
 */
function CadrePorte({
  etiquette,
  children,
}: {
  etiquette: string;
  children: React.ReactNode;
}) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <main aria-label={etiquette} className={CLASSE_CADRE}>
        {children}
      </main>
    </div>
  );
}

/** L'en-tête commun à la porte et à l'écran d'ouverture — la marque, seule. */
function EnTetePorte({ children }: { children?: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2 text-neutral-900 dark:text-neutral-100">
        <LogoMaestro className="h-7 w-7" />
        <span className="text-lg font-semibold tracking-tight">
          Maestro — Control Tower
        </span>
      </div>
      {children}
    </div>
  );
}

/**
 * Le temps que la première lecture tranche. Il ne dit pas « choisir un
 * projet » : on ne sait pas encore s'il y en a un d'actif, et l'annoncer à
 * quelqu'un qui a déjà le sien ferait clignoter la porte à chaque visite.
 */
export function EcranOuverture() {
  return (
    <CadrePorte etiquette="Ouverture de la Control Tower">
      <EnTetePorte>
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Lecture des projets déclarés…
        </p>
      </EnTetePorte>
    </CadrePorte>
  );
}

/** Une entrée de la liste : ce qu'il faut pour reconnaître un projet, et y entrer. */
function CarteChoix({
  projet,
  onOuvrir,
}: {
  projet: Projet;
  onOuvrir: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        onClick={onOuvrir}
        aria-label={`Ouvrir ${projet.nom}`}
        // La surface vient de la primitive ; ne reste ici que ce qu'un bouton
        // pleine largeur ajoute — sa mise en page et son survol.
        className={classesCarte({
          densite: "aeree",
          className:
            "flex w-full flex-col gap-1 text-left hover:border-neutral-400 dark:hover:border-neutral-600",
        })}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold">{projet.nom}</span>
          <span className="rounded-full border border-neutral-300 px-2 py-0.5 text-[11px] font-medium text-neutral-600 dark:border-neutral-700 dark:text-neutral-400">
            {libelleOrigine(projet.origine)}
          </span>
          {projet.vcs !== null && (
            <span className="rounded-full border border-sky-300 px-2 py-0.5 text-[11px] font-medium text-sky-700 dark:border-sky-800 dark:text-sky-400">
              {projet.vcs.type}
              {projet.vcs.branche_base !== "" && ` · ${projet.vcs.branche_base}`}
            </span>
          )}
          {/* « Un projet non outillé le dit » (#1034, docs/37 §4.6) — et il le
              dit **là où on le choisit** autant que sur l'écran Projets : c'est
              ici qu'on décide d'y entrer. Porté par `BadgeEtat` et non par une
              troisième pastille écrite à la main : la primitive existe, et la
              recopier est ce que docs/30 §2.2 a mesuré (18 cartes, 26 boutons). */}
          {projet.outillage?.a_faire && (
            <BadgeEtat ton="attention" contour>
              Outillage reporté
            </BadgeEtat>
          )}
        </span>
        <code className="font-mono text-xs break-all text-neutral-500 dark:text-neutral-400">
          {projet.racine}
        </code>
      </button>
    </li>
  );
}

export function ChoixProjet() {
  const { projets, chargement, erreur, perdu, choisir, recharger } =
    useProjetActif();
  // `null` : personne n'a encore tranché, c'est la liste qui décide. Une fois la
  // création ouverte ou refermée à la main, le choix de l'utilisateur tient —
  // sans quoi le retour à la liste serait sans effet sur une liste vide.
  //
  // « Nouveau projet » demandé **depuis un projet** (l'écran Projets, #1294) a
  // quitté le projet pour revenir ici : la demande voyage par la mémoire de
  // session (`naissanceDemandee`), lue une fois au montage.
  const [creationDemandee, setCreationDemandee] = useState<boolean | null>(() =>
    naissanceDemandee() ? true : null,
  );
  const chemin = usePathname();

  // Rien à lister et rien qui l'explique : la création s'ouvre d'elle-même.
  const listeVide = !chargement && erreur === null && projets.length === 0;
  const creation = creationDemandee ?? listeVide;

  // La garde ne redirige pas : la page demandée est toujours celle de l'URL, on
  // se contente de la nommer. `undefined` sur une page hors menu — on ne promet
  // alors rien qu'on ne sache dire.
  const destination = entreeCourante(chemin);

  // Le mode création (#1294) : la porte ne liste plus, elle pose la question —
  // « Que voulez-vous construire ? » — et la conversation dessous. Le projet qui
  // y naît est ouvert par `NaissanceProjet` lui-même, dès que le fil le porte.
  if (creation) {
    return (
      <CadrePorte etiquette="Nouveau projet">
        <EnTetePorte />
        <NaissanceProjet
          retour={
            projets.length > 0 ? () => setCreationDemandee(false) : undefined
          }
          parUnGeste={creationDemandee === true}
        />
      </CadrePorte>
    );
  }

  return (
    <CadrePorte etiquette="Choix du projet">
      <EnTetePorte>
        <h1 className="text-xl font-semibold tracking-tight">
          Choisir le projet
        </h1>
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Un projet est une <strong>racine sur le disque</strong> : tout ce que
          la Control Tower montre — tâches, agents, coûts, validations — lui
          appartient. {destination && destination.href !== "/" ? (
            <>
              Une fois choisi, retour à <strong>{destination.libelle}</strong>.
            </>
          ) : null}
        </p>
      </EnTetePorte>

      {/* Le motif d'abord : savoir *pourquoi* on est revenu ici précède le
          choix qu'on vient y refaire. */}
      {perdu && (
        <RefusMotive refus={perdu} titre="Retour au choix du projet" />
      )}

      <BanniereErreurApi erreur={erreur} />
      {erreur !== null && (
        <div>
          <Bouton
            variante="contour"
            ton="neutre"
            occupe={chargement}
            onClick={() => void recharger()}
          >
            {chargement ? "Relecture…" : "Réessayer"}
          </Bouton>
        </div>
      )}

      {chargement && (
        <p className="text-sm text-neutral-500 dark:text-neutral-400">
          Lecture des projets…
        </p>
      )}

      {projets.length > 0 && (
        <ul aria-label="Projets déclarés" className="flex flex-col gap-3">
          {projets.map((projet) => (
            <CarteChoix
              key={projet.id}
              projet={projet}
              onOuvrir={() => choisir(projet)}
            />
          ))}
        </ul>
      )}

      <div>
        {/* L'émoji part avec la migration : il apportait sa propre graisse et
            son propre rendu par plateforme, ce que le jeu d'icônes a retiré
            partout ailleurs (#245). */}
        <Bouton icone={IconePlus} onClick={() => setCreationDemandee(true)}>
          Nouveau projet
        </Bouton>
      </div>
    </CadrePorte>
  );
}
