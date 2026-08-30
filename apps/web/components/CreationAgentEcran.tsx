"use client";

/**
 * L'écran de création d'un agent (#254, lot 2 de #243) : le **cadre** autour du
 * formulaire — le retour à la liste, la touche Échap, et la garde du brouillon.
 *
 * Créer un agent était un dépliant sous la liste : le formulaire cohabitait avec
 * ce qu'il remplaçait, et son bouton d'accès descendait avec le catalogue.
 * C'est désormais une **route** (`/agents/nouveau`, `CHEMIN_CREATION_AGENT`), ce
 * qui donne trois choses qu'un dépliant ne donne pas — une adresse qui se
 * partage, un retour arrière du navigateur qui a un sens, et tout l'espace de la
 * zone de contenu. Le reste du cadre ne bouge pas : la barre latérale, la barre
 * supérieure (qui titre toujours « Agents », l'entrée de menu couvrant ses
 * sous-chemins) et le lien d'évitement sont ceux du shell.
 *
 * La séparation avec `CreationAgent` (`components/EditeurAgent`) est celle du
 * **cadre** et du **contenu**, et elle vaut d'être tenue : le formulaire est
 * repris de fond en comble par le lot 3 du chantier (rôle, fournisseur, modèle
 * et effort en listes liées), pendant que les sorties, elles, ne changeront pas.
 * Le formulaire garde donc son état ; il n'en publie qu'un fait, « il y a une
 * saisie en cours » (`onBrouillon`), qui est la seule chose dont les sorties ont
 * besoin.
 *
 * ⚠ Trois sorties, une seule question. « Un brouillon non enregistré se signale
 * avant d'être perdu » vaut pour le lien de retour, pour Échap **et** pour la
 * fermeture de l'onglet — la troisième ne peut être servie que par le
 * navigateur (`beforeunload`), les deux premières le sont par la confirmation
 * ci-dessous. Aucune n'est un dialogue modal : le produit n'en pose que sur ce
 * qui doit couper l'écran (détail de tâche, visite guidée), et une question qui
 * s'inscrit dans la page se lit aussi bien sans piéger le focus.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { CreationAgent } from "@/components/EditeurAgent";
import { IconeFlecheGauche, IconePlus } from "@/components/Icones";
import {
  Bouton,
  CIBLE_MINIMALE,
  EnTeteSection,
  classesCarte,
} from "@/components/Primitives";
import { cheminOnglet } from "@/lib/agents";

/** Le chemin de la liste — celui du retour, et celui d'une sortie confirmée. */
const LISTE = "/agents";

export function CreationAgentEcran() {
  const router = useRouter();
  const racine = useRef<HTMLDivElement>(null);
  const question = useRef<HTMLDivElement>(null);
  const [brouillon, setBrouillon] = useState(false);
  const [sortieArmee, setSortieArmee] = useState(false);

  const quitter = useCallback(() => router.push(LISTE), [router]);

  /** Sortir — ou demander d'abord, s'il y a une saisie à perdre. */
  const demanderSortie = useCallback(() => {
    if (brouillon) setSortieArmee(true);
    else quitter();
  }, [brouillon, quitter]);

  // Échap quitte la création. Deux précautions, et chacune vise un vrai voisin :
  // un événement déjà consommé (`defaultPrevented`) appartient à la visite
  // guidée, qui pilote l'écran par-dessus ; une frappe *dans* une surface
  // superposée (assistant, infobulle) appartient à cette surface, qui se ferme
  // sur la même touche — quitter la page au même moment ferait deux gestes pour
  // une frappe. On agit donc sur ce qui vient de l'écran lui-même, ou du
  // document quand rien n'a le focus (le cas nominal à l'arrivée sur la page).
  useEffect(() => {
    const surTouche = (evenement: KeyboardEvent) => {
      if (evenement.key !== "Escape" || evenement.defaultPrevented) return;
      const cible = evenement.target;
      const nu = cible === document.body || cible === document.documentElement;
      const dedans =
        cible instanceof Node && (racine.current?.contains(cible) ?? false);
      if (!nu && !dedans) return;
      // La question posée, Échap la retire au lieu de la trancher : rien ne se
      // perd sur une frappe répétée, et abandonner un brouillon reste un geste
      // explicite.
      if (sortieArmee) setSortieArmee(false);
      else demanderSortie();
    };
    document.addEventListener("keydown", surTouche);
    return () => document.removeEventListener("keydown", surTouche);
  }, [demanderSortie, sortieArmee]);

  // La sortie que l'application ne voit pas : fermer l'onglet, recharger, suivre
  // un lien hors du produit. Seul le navigateur peut encore interrompre, et
  // seulement s'il y a quelque chose à perdre — un `beforeunload` posé en
  // permanence ferait payer une boîte de dialogue à un formulaire vierge.
  useEffect(() => {
    if (!brouillon) return;
    const surDepart = (evenement: BeforeUnloadEvent) => {
      evenement.preventDefault();
    };
    window.addEventListener("beforeunload", surDepart);
    return () => window.removeEventListener("beforeunload", surDepart);
  }, [brouillon]);

  // La question prend le focus : elle est annoncée (`role="alert"`), et la
  // tabulation suivante tombe sur ses deux boutons plutôt que de repartir du
  // haut de la page. Sur le conteneur et non sur l'un des boutons — poser le
  // focus sur « Quitter » armerait la perte d'une frappe d'Entrée.
  useEffect(() => {
    if (sortieArmee) question.current?.focus();
  }, [sortieArmee]);

  return (
    <div ref={racine} className="flex min-w-0 flex-1 flex-col gap-4">
      {/* Même en-tête qu'une fiche agent (`OngletsAgent`) : le retour d'abord,
          le titre de ce qu'on fait ensuite. Un `Link` et non un bouton — il
          s'ouvre dans un onglet, il se copie —, dont le clic ordinaire est
          seulement retenu tant qu'il y a un brouillon. */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Link
          href={LISTE}
          onClick={(evenement) => {
            if (!brouillon) return;
            evenement.preventDefault();
            setSortieArmee(true);
          }}
          className={`inline-flex items-center gap-1 ${CIBLE_MINIMALE} text-annexe font-medium text-neutral-500 hover:text-neutral-900 dark:text-neutral-400 dark:hover:text-neutral-100`}
        >
          <IconeFlecheGauche className="size-3.5 shrink-0" />
          Tous les agents
        </Link>
        <EnTeteSection
          titre="Nouvel agent"
          icone={IconePlus}
          aside={
            <span className="text-annexe text-neutral-500 dark:text-neutral-400">
              Échap ou « Tous les agents » pour revenir à la liste.
            </span>
          }
          className="justify-start"
        />
      </div>

      {sortieArmee && (
        <div
          ref={question}
          tabIndex={-1}
          role="alert"
          className={classesCarte({
            ton: "attention",
            className:
              "flex flex-wrap items-center gap-3 outline-none focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
          })}
        >
          <span className="text-corps font-medium text-amber-900 dark:text-amber-200">
            Brouillon non enregistré : quitter maintenant le perd.
          </span>
          <Bouton onClick={() => setSortieArmee(false)}>
            Reprendre la saisie
          </Bouton>
          <Bouton variante="contour" ton="alerte" onClick={quitter}>
            Quitter sans enregistrer
          </Bouton>
        </div>
      )}

      {/* Le retour après création ne bouge pas (#254, notes du ticket) : on
          arrive sur la fiche de l'agent qui vient de naître, pas sur la liste. */}
      <CreationAgent
        onCreation={(nom) => router.push(cheminOnglet(nom))}
        onBrouillon={setBrouillon}
      />
    </div>
  );
}
