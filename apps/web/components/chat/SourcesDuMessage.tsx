"use client";

/**
 * Ce qu'un message du fil embarque, pendant qu'on le compose (#482, lot 1 de #481)
 * — et, depuis #727 (lot 4 de #722), **depuis le composeur** : le geste
 * « joindre » est la tête du rail du cadre de saisie (`BoutonJoindre`), les trois
 * gestes existants vivent derrière lui, et ce qui est joint se lit attaché au
 * message en préparation, juste sous le cadre.
 *
 * Le pendant, dans la conversation, du bloc « Sources » de `ComposerObjectif`
 * (#319) — et volontairement **le même vocabulaire** : les mêmes types
 * (`SourceComposee`), les mêmes libellés (`libelleType`, `formaterOctets`), le
 * même bandeau de refus (`RefusSource`), le même explorateur de dossiers
 * (#223/#278). Un second vocabulaire pour la même matière aurait divergé, et
 * c'est la moitié la moins relue qui aurait gagné.
 *
 * Trois partis pris tiennent ce composant depuis #482 :
 *
 * - **le glisser-déposer est la voie principale, pas un supplément.** C'est le
 *   geste que le critère nomme, et c'est celui qu'on fait dans une conversation :
 *   la zone de dépôt entoure la saisie plutôt que de vivre à côté. Le bouton
 *   reste, parce qu'un dépôt à la souris n'est pas atteignable au clavier ;
 * - **rien n'est promis sur ce qui sera lu.** Une image se dépose comme un
 *   `.md` — la chaîne d'ingestion est unique, le critère l'exige — mais elle
 *   ressort « Ignoré / format-non-gere » du rapport, parce que l'extraction ne
 *   lit aujourd'hui que le texte, le Markdown, le `.docx` et le `.pdf`. Cet
 *   écran **ne redit pas cette liste** : elle vit côté backend
 *   (`EXTENSIONS_TEXTE`/`EXTENSIONS_CONVERTIES`), et la recopier ici en ferait
 *   une seconde table à tenir d'accord — exactement ce que `lib/sources.ts`
 *   refuse déjà pour les motifs de refus. Ce qui a réellement été lu se lit là
 *   où c'est vrai : dans le rapport, sous le message ;
 * - **un refus s'affiche sur la source qu'il vise.** L'API rend un `index` quand
 *   le refus en désigne une (#315) ; le taire obligerait à tout relire pour
 *   savoir quoi retirer.
 *
 * Et deux de plus depuis #727, venus de la veille #724 (parti pris 2, docs/30
 * §5.3, décision complète en commentaire de #722) :
 *
 * - **un seul point d'entrée, qui ouvre les gestes** — d'après ChatGPT (un `+`
 *   en tête du champ, qui ouvre un menu) et Perplexity (un `+` en tête du
 *   rail). Jusque-là « Joindre des sources… » était un **troisième bloc** posé
 *   sous le formulaire, alors que joindre est un geste de la saisie, pas une
 *   section de l'écran. `BoutonJoindre` est désormais l'icône de tête du rail du
 *   cadre (`components/Conversation`), et le panneau des trois gestes
 *   (fichiers, dossier, adresse) ne se déplie que derrière lui — jamais N
 *   boutons permanents ;
 * - **la liste des sources jointes reste sous le cadre : ce sont des objets,
 *   pas des contrôles.** Elle n'a pas sa place sur le rail, et elle n'apparaît
 *   que quand il y a quelque chose à montrer — sans volet à ouvrir pour la voir,
 *   donc aucune pièce jointe ne part sans avoir été vue. Chaque ligne garde son
 *   retrait et le refus qui la vise (#482) : rien n'a bougé de ce qui s'y joue,
 *   seulement l'endroit d'où on y arrive.
 *
 * Ce que la veille a laissé au lot : le bouton réduit à son icône porte son
 * **nom accessible** (le libellé est là, `sr-only`) et le plancher de 24 px du
 * socle (`BOUTON_SOCLE`, WCAG 2.2 §2.5.8) ; l'état ouvert/fermé est dit par
 * `aria-expanded`, et le panneau qu'il commande par `aria-controls`.
 */

import { useId, useRef, useState } from "react";

import { ExplorateurDossiers } from "@/components/projets/ExplorateurDossiers";
import {
  BadgeEtat,
  Bouton,
  Champ,
  CIBLE_MINIMALE,
} from "@/components/Primitives";
import {
  IconeDossier,
  IconeFermer,
  IconeLienExterne,
  IconePlus,
} from "@/components/Icones";
import { RefusSource } from "@/components/composer/RefusSource";
import type { ErreurSource } from "@/lib/api";
import { formaterOctets, libelleType, type SourceComposee } from "@/lib/sources";
import { SOURCE_DOSSIER, SOURCE_URL } from "@/lib/types";
import type { SourcesComposees } from "@/lib/useSourcesComposees";

/** Le nom du geste — celui que le bouton porte et que le panneau reprend. */
const LIBELLE_JOINDRE = "Joindre des sources…";

/**
 * La tête du rail du composeur (#727) : le seul point d'entrée des gestes de
 * dépôt. Une icône, parce que le rail n'a pas la place d'un libellé à côté de
 * l'envoi et que le geste se reconnaît (le `+` des messageries mesurées par la
 * veille) — mais **jamais une icône seule** : le libellé est là pour le lecteur
 * d'écran, et `title` le rend au survol.
 *
 * `aria-controls` n'est posé que **panneau ouvert** : fermé, le panneau n'est
 * pas dans le document, et désigner un identifiant absent est une faute pour
 * axe. `aria-expanded` suffit à dire l'état dans les deux cas.
 */
export function BoutonJoindre({
  ouvert,
  occupe,
  idPanneau,
  onBasculer,
}: {
  ouvert: boolean;
  occupe: boolean;
  /** L'identifiant du panneau que le bouton déplie (`SourcesDuMessage`). */
  idPanneau: string;
  onBasculer: () => void;
}) {
  return (
    <Bouton
      variante="contour"
      ton="neutre"
      taille="petite"
      icone={IconePlus}
      disabled={occupe}
      aria-expanded={ouvert}
      aria-controls={ouvert ? idPanneau : undefined}
      title={LIBELLE_JOINDRE}
      onClick={onBasculer}
    >
      <span className="sr-only">{LIBELLE_JOINDRE}</span>
    </Bouton>
  );
}

/**
 * Sous le cadre de saisie : le panneau des gestes de dépôt quand il est ouvert
 * (fichiers, dossier, adresse — derrière `BoutonJoindre`), puis ce qui est déjà
 * joint au message en préparation, tant qu'il y a quelque chose. Rend `null`
 * quand ni l'un ni l'autre n'a lieu d'être : au repos, le composeur est le seul
 * cadre à l'écran.
 */
export function SourcesDuMessage({
  composition,
  refus,
  occupe,
  ouvert,
  idPanneau,
}: {
  composition: SourcesComposees;
  /** Le refus du dernier envoi, s'il visait la composition en cours. */
  refus: ErreurSource | null;
  occupe: boolean;
  /** Le panneau des gestes est-il déplié ? (état tenu par le composeur, avec son bouton) */
  ouvert: boolean;
  /** L'identifiant du panneau — celui que `BoutonJoindre` désigne. */
  idPanneau: string;
}) {
  const idUrl = useId();
  const entreeFichiers = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const { sources, deposer, ajouterDossier, ajouterUrl, retirer } = composition;

  const soumettreUrl = () => {
    if (ajouterUrl(url)) setUrl("");
  };

  if (!ouvert && sources.length === 0) return null;

  return (
    <div className="flex flex-col gap-2">
      {ouvert && (
        <div
          id={idPanneau}
          role="group"
          aria-label="Joindre des sources"
          className="flex flex-col gap-2 rounded-md border border-bord bg-surface p-2.5"
        >
          <div className="flex flex-wrap gap-2">
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              disabled={occupe}
              onClick={() => entreeFichiers.current?.click()}
            >
              Déposer des fichiers…
            </Bouton>
            <input
              ref={entreeFichiers}
              type="file"
              multiple
              aria-label="Fichiers à joindre au message"
              onChange={(evenement) => {
                deposer(evenement.target.files);
                // Redéposer deux fois le même fichier doit rester possible, or
                // `change` ne part pas si la valeur n'a pas changé.
                evenement.target.value = "";
              }}
              className="sr-only"
            />
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              disabled={occupe}
              aria-expanded={explorateurOuvert}
              onClick={() => setExplorateurOuvert(!explorateurOuvert)}
            >
              {explorateurOuvert ? "Fermer l'explorateur" : "Choisir un dossier…"}
            </Bouton>
          </div>

          {/* Le dossier vient de l'explorateur servi par le backend, jamais d'une
              saisie : un navigateur ne livre pas de chemin absolu (#223/#278). */}
          {explorateurOuvert && (
            <ExplorateurDossiers
              cheminInitial={null}
              onChoisir={(chemin) => {
                ajouterDossier(chemin);
                setExplorateurOuvert(false);
              }}
              onFermer={() => setExplorateurOuvert(false)}
            />
          )}

          <div className="flex flex-wrap items-end gap-2">
            {/* Un `Champ` du socle (#832) et non une recopie de ses classes :
                c'est de là que viennent le bord de focus et le contour clavier.
                Le libellé est **masqué** (#727) : dans un panneau déplié depuis
                le rail, la question tient dans le `placeholder`, et un libellé
                au-dessus ferait du panneau un formulaire de plus. */}
            <Champ
              id={idUrl}
              libelle="Adresse à lire"
              libelleMasque
              className="min-w-56 flex-1"
              type="url"
              value={url}
              onChange={(evenement) => setUrl(evenement.target.value)}
              onKeyDown={(evenement) => {
                if (evenement.key === "Enter") {
                  // Sans quoi Entrée soumettrait le formulaire du fil et
                  // enverrait le message au lieu d'ajouter l'adresse.
                  evenement.preventDefault();
                  soumettreUrl();
                }
              }}
              disabled={occupe}
              placeholder="Adresse à lire — https://…"
            />
            <Bouton
              variante="contour"
              ton="neutre"
              taille="petite"
              disabled={occupe || url.trim() === ""}
              onClick={soumettreUrl}
            >
              Ajouter l&apos;adresse
            </Bouton>
          </div>

          {/* Ce que l'état vide disait avant #727, en une ligne : la voie
              principale n'est pas dans ce panneau, elle est sur la conversation
              entière (glisser) et dans le champ (coller une capture). */}
          <p className="text-micro text-texte-secondaire">
            Un fichier se glisse aussi directement sur la conversation, et une
            image collée dans le message s&apos;y joint de même.
          </p>
        </div>
      )}

      {sources.length > 0 && (
        <ListeSourcesJointes
          sources={sources}
          refus={refus}
          occupe={occupe}
          onRetirer={retirer}
        />
      )}
    </div>
  );
}

/**
 * Les sources jointes au message en cours, chacune retirable et capable de
 * porter son refus. Des lignes sur le fond creux du socle et non des cadres
 * bordés (#727) : sous un cadre de saisie qui porte déjà le sien, une pile de
 * rectangles refaisait le défaut que le lot corrige. Le bouton de retrait suit
 * `ChampJetons` — réduit à son icône, il garde son nom et `CIBLE_MINIMALE`.
 */
function ListeSourcesJointes({
  sources,
  refus,
  occupe,
  onRetirer,
}: {
  sources: SourceComposee[];
  refus: ErreurSource | null;
  occupe: boolean;
  onRetirer: (cle: string) => void;
}) {
  return (
    <ul aria-label="Sources jointes au message" className="flex flex-col gap-1">
      {sources.map((source, rang) => (
        <li key={source.cle} className="rounded-md bg-surface-creuse px-2.5 py-1">
          <div className="flex flex-wrap items-center gap-2">
            <BadgeEtat ton="neutre" contour>
              {libelleType(source.type)}
            </BadgeEtat>
            {source.type === SOURCE_DOSSIER && (
              <IconeDossier
                aria-hidden="true"
                className="size-4 shrink-0 text-texte-secondaire"
              />
            )}
            {source.type === SOURCE_URL && (
              <IconeLienExterne
                aria-hidden="true"
                className="size-4 shrink-0 text-texte-secondaire"
              />
            )}
            <span className="min-w-0 truncate text-annexe text-texte">
              {source.nom}
            </span>
            {source.taille !== undefined && (
              <span className="chiffre text-annexe text-texte-secondaire">
                {formaterOctets(source.taille)}
              </span>
            )}
            <button
              type="button"
              disabled={occupe}
              onClick={() => onRetirer(source.cle)}
              aria-label={`Retirer ${source.nom}`}
              className={
                `ml-auto inline-flex ${CIBLE_MINIMALE} min-w-6 items-center justify-center ` +
                "rounded text-annexe text-texte-secondaire hover:bg-survol hover:text-texte " +
                "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent " +
                "disabled:cursor-not-allowed disabled:opacity-50"
              }
            >
              <IconeFermer aria-hidden="true" className="size-3.5 shrink-0" />
            </button>
          </div>
          {/* Le refus **sur la source qu'il vise** : « une source est trop
              grosse » sans dire laquelle obligerait à tout relire. */}
          {refus !== null && refus.index === rang && (
            <div className="mt-1.5">
              <RefusSource refus={refus} titre="Source refusée" />
            </div>
          )}
        </li>
      ))}
    </ul>
  );
}
