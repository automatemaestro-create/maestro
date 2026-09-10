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
 * ## Deux temps, et lequel dit quoi (#894)
 *
 * La veille du 2026-09-10 (#868, décision en commentaire de #837) a confirmé
 * deux des trois points tenus à l'écran et fait bouger le troisième : GitHub
 * Actions, capturé sur un run réellement en cours, montre **deux** mesures sur
 * un job qui travaille — la durée comptée en direct sur le nœud, et « Started
 * 3m 47s ago » sur son en-tête. Nous n'avions que la seconde.
 *
 * D'où `ChronoEnVol` : « depuis 6 min », l'ancienneté de la **tâche**, à côté
 * de « il y a 12 s », l'âge du dernier **geste**. Le module de formats porte
 * déjà les deux mots avec leur raison (`formatAnciennete` situe un fait passé,
 * `formatAttente` mesure une attente qui dure), et c'est cette distinction
 * qu'il ne faut pas fondre : seule la seconde mesure distingue une tâche
 * vivante d'une tâche **vivante mais partie trop loin** — celle qui enchaîne
 * des gestes depuis vingt minutes sur un sujet qui en demandait deux.
 *
 * Deux feuilles et non une, parce que les deux temps ne se posent pas au même
 * endroit. Sur le nœud du Pipeline et la carte du Kanban, le chrono va dans la
 * **ligne chrono** — celle qui porte déjà `formatDuree(duree_ms)` une fois la
 * tâche soldée, et qui, en vol, ne disait rien ou « — » : la place existe, et
 * la règle des trois places (docs/30 §4) plafonne ces boîtes. L'en-tête de
 * couloir de la frise, lui, n'a pas de ligne chrono : son unique place est
 * cette ligne-ci, d'où `avecChrono`. Une information, deux places existantes,
 * **un** composant — plutôt qu'une quatrième ligne quelque part.
 *
 * Et rien n'est ajouté au mouvement : la veille confirme que rien ne doit
 * pulser (parti pris 2), et une durée qui avance est le mouvement qu'on a
 * déjà — c'en est un de plus, pas deux.
 *
 * ## L'horloge est la sienne
 *
 * Il s'abonne lui-même à l'horloge fine (`useHorlogeFine`, un battement par
 * seconde) et non son appelant : un nœud de pipeline mesure ses boîtes à chaque
 * rendu, une vue entière qui re-rendrait chaque seconde pour un compteur serait
 * le prix d'une feuille payé par tout l'écran. Seule cette ligne re-rend — et
 * `ChronoEnVol` est une feuille pour la même raison, montée dans une ligne que
 * ni le nœud ni la carte ne re-rendent.
 */

import { IconeActivite } from "@/components/Icones";
import { formatAnciennete, formatAttente, formatDateHeure } from "@/lib/format";
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
  avecChrono = false,
  className = "",
}: {
  signe: SigneDeVie;
  taille?: TailleSigne;
  /**
   * Poser aussi le second temps (« depuis 6 min ») **sur cette ligne** — pour
   * la seule surface qui n'a pas de ligne chrono où le mettre : l'en-tête de
   * couloir de la frise. Le nœud et la carte le montrent dans leur place à eux
   * et laissent ce drapeau à `false` ; l'y ajouter deux fois dirait deux fois
   * la même chose sur la même boîte.
   */
  avecChrono?: boolean;
  className?: string;
}) {
  const maintenant = useHorlogeFine();
  const anciennete = formatAnciennete(signe.horodatage, maintenant);
  const depuis = signe.travaille_depuis;

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
      {/* L'horodatage absolu au survol (#894) : « il y a 4 min » ne dit pas
          **de quand**, et c'est la première question devant un run qui traîne.
          Le texte visible ne bouge pas — la date se prend en plus, pas à la
          place, et sans ouvrir le Journal.

          `title` et non `Infobulle` (#536), à dessein : la primitive existe
          pour le cas **inverse** — une information portée nulle part ailleurs,
          rendue atteignable au clavier par un `tabIndex` sur son wrapper. Ici
          l'information est un **complément** de ce que la ligne dit déjà, dont
          le Journal porte la version exacte ; et un arrêt de tabulation par
          signe de vie coûterait, sur un Pipeline aux nœuds en cours, autant
          d'arrêts que de boîtes qui travaillent. C'est aussi ce que fait la
          référence de la veille (GitHub Actions, `title` sur chaque durée
          relative). */}
      <time
        dateTime={signe.horodatage}
        title={formatDateHeure(signe.horodatage)}
        className="chiffre shrink-0 whitespace-nowrap text-neutral-500 dark:text-neutral-400"
      >
        {anciennete}
      </time>
      {avecChrono && depuis && (
        <>
          <span aria-hidden="true">·</span>
          <ChronoEnVol depuis={depuis} />
        </>
      )}
    </p>
  );
}

/**
 * **Depuis combien de temps la tâche travaille** — « depuis 6 min », compté en
 * direct (#894).
 *
 * Une feuille, et c'est tout son intérêt : elle s'abonne seule à l'horloge fine
 * (`useHorlogeFine`), si bien qu'un nœud de pipeline ou une carte de Kanban
 * peuvent la monter dans leur ligne chrono sans re-rendre chaque seconde. Même
 * raison qu'en tête de fichier, et même dispositif que `LigneSigneDeVie` — les
 * deux partagent un unique battement, quel que soit le nombre de tâches en vol
 * à l'écran.
 *
 * Elle rend `formatAttente` et non `formatDuree` : le second mesure un
 * intervalle **figé** (une tâche soldée, `duree_ms`), celui-ci une attente qui
 * **dure**, et c'est bien ce qu'on cherche à lire d'une tâche qui tourne
 * encore. Le `<time>` porte le départ en valeur machine et sa date au survol,
 * pour la raison qui vaut au-dessus : « depuis 6 min » ne dit pas de quand.
 *
 * Le sr-only nomme ce que la valeur est. Sans lui, la ligne chrono d'une boîte
 * en cours se lirait « 0,00 $US depuis 6 min », deux mesures sans étiquette
 * dont la seconde n'a plus de rapport avec la première.
 */
export function ChronoEnVol({
  depuis,
  className = "",
}: {
  depuis: string;
  className?: string;
}) {
  const maintenant = useHorlogeFine();

  return (
    <>
      <span className="sr-only">Travaille </span>
      <time
        data-chrono-en-vol
        dateTime={depuis}
        title={formatDateHeure(depuis)}
        className={`shrink-0 whitespace-nowrap ${className}`}
      >
        {formatAttente(depuis, maintenant)}
      </time>
    </>
  );
}
