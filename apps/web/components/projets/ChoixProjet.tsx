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
 * 2. **la création est proposée sur place**, avec le formulaire de l'écran
 *    Projets (#225) — ce lot ne réécrit pas la déclaration d'un projet, il la
 *    place au bon endroit du parcours. Aucun projet déclaré : le formulaire est
 *    déjà ouvert, parce qu'un écran qui n'a rien à lister n'a qu'une chose à
 *    proposer, et l'ouvrir d'office évite un clic qui n'a pas d'alternative ;
 * 3. **on n'invente jamais l'absence** : une API muette dit sa panne et offre de
 *    réessayer, elle ne se lit pas « aucun projet » (même règle que la liste de
 *    #225, pour la même raison — inviter à re-déclarer des projets déjà là) ;
 * 4. **la page demandée est nommée, pas oubliée** : la garde ne redirige pas,
 *    donc l'URL ne bouge pas. Le dire à l'écran fait de cette propriété quelque
 *    chose qui se voit — on sait où l'on retombe avant de choisir.
 */

import { usePathname } from "next/navigation";
import { useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { LogoMaestro } from "@/components/Logo";
import { creerProjet } from "@/lib/api";
import { useProjetActif } from "@/lib/etatProjetActif";
import { entreeCourante } from "@/lib/navigation";
import { libelleOrigine } from "@/lib/projets";
import type { DeclarationProjet, Projet } from "@/lib/types";

import { RefusMotive } from "./ExplorateurDossiers";
import { FormulaireProjet } from "./FormulaireProjet";

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
        className="flex w-full flex-col gap-1 rounded-lg border border-neutral-200 bg-white p-4 text-left shadow-sm hover:border-neutral-400 dark:border-neutral-800 dark:bg-neutral-900 dark:hover:border-neutral-600"
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
  // `null` : personne n'a encore tranché, c'est la liste qui décide. Une fois
  // le formulaire ouvert ou refermé à la main, le choix de l'utilisateur tient —
  // sans quoi « Annuler » serait sans effet sur une liste vide.
  const [creationDemandee, setCreationDemandee] = useState<boolean | null>(null);
  const chemin = usePathname();

  // Rien à lister et rien qui l'explique : le formulaire s'ouvre de lui-même.
  const listeVide = !chargement && erreur === null && projets.length === 0;
  const creation = creationDemandee ?? listeVide;

  const declarer = async (declaration: DeclarationProjet) => {
    // `creerProjet` rend le projet **relu** par le backend (racine
    // canonicalisée, VCS constaté) : c'est celui-là qu'on ouvre, pas ce qui a
    // été envoyé. Un refus remonte au formulaire, qui l'affiche avec son motif.
    choisir(await creerProjet(declaration));
  };

  // La garde ne redirige pas : la page demandée est toujours celle de l'URL, on
  // se contente de la nommer. `undefined` sur une page hors menu — on ne promet
  // alors rien qu'on ne sache dire.
  const destination = entreeCourante(chemin);

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
          <button
            type="button"
            onClick={() => void recharger()}
            disabled={chargement}
            className="rounded-md border border-neutral-300 px-3 py-1.5 text-xs font-medium text-neutral-600 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            {chargement ? "Relecture…" : "Réessayer"}
          </button>
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

      {listeVide && (
        <p className="text-sm text-neutral-600 dark:text-neutral-300">
          Aucun projet déclaré pour l&apos;instant — en déclarer un, c&apos;est
          donner une adresse aux exécutions.
        </p>
      )}

      {creation ? (
        <FormulaireProjet
          enregistrer={declarer}
          onAnnuler={() => setCreationDemandee(false)}
        />
      ) : (
        <div>
          <button
            type="button"
            onClick={() => setCreationDemandee(true)}
            className="rounded-md bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
          >
            ➕ Nouveau projet
          </button>
        </div>
      )}
    </CadrePorte>
  );
}
