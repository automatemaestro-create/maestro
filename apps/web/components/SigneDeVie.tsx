"use client";

/**
 * Le **signe de vie** d'une tâche qui travaille (#837, lot 3 de #834) : le
 * dernier geste de son agent et son ancienneté, qui se rafraîchit à la seconde.
 *
 * C'est la moitié visible de #836. Ce lot-là a posé la donnée — `activite`, un
 * horodatage et un libellé court, servi sur la carte de tâche, sur le nœud
 * `en_cours` du graphe et sur le couloir de la frise, `null` sur tout ce qui ne
 * travaille pas — et mesuré ce qui manquait : sur une fenêtre de 88 s, page
 * ouverte sur le Pipeline, 4 rafales de lectures pour **2** mutations du DOM.
 * La page rechargeait bien, elle n'avait rien de nouveau à peindre.
 *
 * ## Un composant, trois surfaces
 *
 * La même ligne sur le nœud du Pipeline, la carte du Kanban et l'en-tête du
 * couloir de la frise — parce que le backend sert **la même** valeur aux trois
 * endroits (`maestro/controltower/signe_de_vie.py`), et qu'un signe rendu de
 * trois façons ferait chercher trois faits là où il n'y en a qu'un. La règle qui
 * décide s'il y a un signe (« seule une tâche `en_cours` en porte un ») vit chez
 * la projection ; ce composant ne la rejoue pas, il rend ce qu'on lui donne.
 * L'appelant n'a qu'une question à se poser — « ai-je un signe ? » —, et ne le
 * monte pas sinon : une tâche arrêtée ne « bouge » pas, donc n'en montre aucun.
 *
 * ## Ce qu'il montre, et pourquoi pas plus
 *
 * Le **libellé** du geste (« Écrit api/contacts.py, puis relit le résultat »),
 * tronqué à la ligne, et son **âge** — « il y a 12 s », qui compte tout seul.
 * C'est l'âge qui fait le signe : « il y a 12 s » dit « ça bouge », « il y a
 * 4 min » dit « ça s'est peut-être arrêté », et cette différence se lit sans
 * ouvrir le Journal. Rien ne pulse ici : le badge « En cours » bat déjà juste
 * au-dessus sur le nœud, et un compteur qui avance est un mouvement suffisant —
 * en ajouter un second ferait du bruit là où l'on cherche un pouls.
 *
 * Il tient dans la **place existante** (règle des trois places, docs/30 §4) :
 * une ligne de plus dans une carte qui en a déjà quatre, ni bloc de plein
 * format ni chiffre de bandeau. Une tâche qui ne travaille pas rend la carte
 * d'avant ce lot, au pixel près.
 *
 * ## L'horloge est la sienne
 *
 * Il s'abonne lui-même à l'horloge fine (`useHorlogeFine`, un battement par
 * seconde) et non son appelant : un nœud de pipeline mesure ses boîtes à chaque
 * rendu, une vue entière qui re-rendrait chaque seconde pour un compteur serait
 * le prix d'une feuille payé par tout l'écran. Seule cette ligne re-rend.
 */

import { IconeActivite } from "@/components/Icones";
import { formatAnciennete } from "@/lib/format";
import { useHorlogeFine } from "@/lib/horloge";
import type { SigneDeVie } from "@/lib/types";

/** Le corps de texte : celui des lignes de carte, ou celui d'un en-tête de couloir. */
type TailleSigne = "annexe" | "micro";

const TAILLE: Record<TailleSigne, string> = {
  annexe: "text-annexe",
  micro: "text-micro",
};

export function LigneSigneDeVie({
  signe,
  taille = "annexe",
  className = "",
}: {
  signe: SigneDeVie;
  taille?: TailleSigne;
  className?: string;
}) {
  const maintenant = useHorlogeFine();
  const anciennete = formatAnciennete(signe.horodatage, maintenant);

  return (
    <p
      data-signe-de-vie
      className={`flex items-center gap-1 ${TAILLE[taille]} text-neutral-600 dark:text-neutral-300 ${className}`}
    >
      {/* Le glyphe du flux en direct (`IconeActivite`), au ton « info » du
          badge « En cours » : décoratif, le texte porte tout — la phrase
          lue par un lecteur d'écran commence par ce que la ligne est. */}
      <IconeActivite className="size-3.5 shrink-0 text-sky-600 dark:text-sky-400" />
      <span className="sr-only">Dernier geste de l&apos;agent : </span>
      {signe.libelle && (
        <>
          <span className="min-w-0 truncate">{signe.libelle}</span>
          <span aria-hidden="true">·</span>
        </>
      )}
      <time
        dateTime={signe.horodatage}
        className="chiffre shrink-0 whitespace-nowrap text-neutral-500 dark:text-neutral-400"
      >
        {anciennete}
      </time>
    </p>
  );
}
