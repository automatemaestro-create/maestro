"use client";

/**
 * Ce qu'un message du fil embarque, pendant qu'on le compose (#482, lot 1 de #481).
 *
 * Le pendant, dans la conversation, du bloc « Sources » de `ComposerObjectif`
 * (#319) — et volontairement **le même vocabulaire** : les mêmes types
 * (`SourceComposee`), les mêmes libellés (`libelleType`, `formaterOctets`), le
 * même bandeau de refus (`RefusSource`), le même explorateur de dossiers
 * (#223/#278). Un second vocabulaire pour la même matière aurait divergé, et
 * c'est la moitié la moins relue qui aurait gagné.
 *
 * Trois partis pris tiennent ce composant :
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
 */

import { useId, useRef, useState } from "react";

import { ExplorateurDossiers } from "@/components/projets/ExplorateurDossiers";
import { BadgeEtat, Bouton, Champ, EtatVide } from "@/components/Primitives";
import { IconeDossier, IconeFermer, IconeLienExterne } from "@/components/Icones";
import { RefusSource } from "@/components/composer/RefusSource";
import type { ErreurSource } from "@/lib/api";
import { formaterOctets, libelleType, type SourceComposee } from "@/lib/sources";
import { SOURCE_DOSSIER, SOURCE_URL } from "@/lib/types";
import type { SourcesComposees } from "@/lib/useSourcesComposees";

/**
 * Les gestes de dépôt : fichiers, dossier, adresse. Rendus **sous** la saisie,
 * repliés tant qu'on n'en a pas besoin — une conversation n'est pas un
 * formulaire, et trois champs permanents au-dessus du clavier feraient de
 * l'exception la règle.
 */
export function SourcesDuMessage({
  composition,
  refus,
  occupe,
  ouvert,
  onBasculer,
}: {
  composition: SourcesComposees;
  /** Le refus du dernier envoi, s'il visait la composition en cours. */
  refus: ErreurSource | null;
  occupe: boolean;
  ouvert: boolean;
  onBasculer: () => void;
}) {
  const idUrl = useId();
  const entreeFichiers = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState("");
  const [explorateurOuvert, setExplorateurOuvert] = useState(false);
  const { sources, deposer, ajouterDossier, ajouterUrl, retirer } = composition;

  const soumettreUrl = () => {
    if (ajouterUrl(url)) setUrl("");
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Bouton
          variante="contour"
          ton="neutre"
          taille="petite"
          disabled={occupe}
          aria-expanded={ouvert}
          onClick={onBasculer}
        >
          {ouvert ? "Masquer les sources" : "Joindre des sources…"}
        </Bouton>
        {/* Le compte reste visible replié : des pièces jointes oubliées sous un
            panneau fermé partiraient sans que rien ne l'ait dit. */}
        {!ouvert && sources.length > 0 && (
          <BadgeEtat ton="neutre" contour>
            {sources.length} source{sources.length > 1 ? "s" : ""} jointe
            {sources.length > 1 ? "s" : ""}
          </BadgeEtat>
        )}
      </div>

      {ouvert && (
        <div className="space-y-2 rounded-md border border-neutral-200 p-2.5 dark:border-neutral-800">
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
                c'est de là que viennent le bord de focus et le contour clavier. */}
            <Champ
              id={idUrl}
              libelle="Adresse à lire"
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
              placeholder="https://…"
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
        </div>
      )}

      {(ouvert || sources.length > 0) && (
        <ListeSourcesJointes
          sources={sources}
          refus={refus}
          occupe={occupe}
          onRetirer={retirer}
          montrerVide={ouvert}
        />
      )}
    </div>
  );
}

/** Les sources jointes au message en cours, chacune retirable et capable de porter son refus. */
function ListeSourcesJointes({
  sources,
  refus,
  occupe,
  onRetirer,
  montrerVide,
}: {
  sources: SourceComposee[];
  refus: ErreurSource | null;
  occupe: boolean;
  onRetirer: (cle: string) => void;
  montrerVide: boolean;
}) {
  if (sources.length === 0) {
    return montrerVide ? (
      <EtatVide
        icone={IconeDossier}
        message="Aucune source jointe — le message partira sur son texte seul."
        releve="Un fichier glissé sur la conversation, un dossier de références ou une adresse s'ajoutent ci-dessus."
      />
    ) : null;
  }
  return (
    <ul aria-label="Sources jointes au message" className="space-y-1.5">
      {sources.map((source, rang) => (
        <li
          key={source.cle}
          className="rounded-md border border-neutral-200 px-2.5 py-1.5 dark:border-neutral-800"
        >
          <div className="flex flex-wrap items-center gap-2">
            <BadgeEtat ton="neutre" contour>
              {libelleType(source.type)}
            </BadgeEtat>
            {source.type === SOURCE_DOSSIER && (
              <IconeDossier className="size-4 shrink-0 text-neutral-400" />
            )}
            {source.type === SOURCE_URL && (
              <IconeLienExterne className="size-4 shrink-0 text-neutral-400" />
            )}
            <span className="min-w-0 truncate text-annexe">{source.nom}</span>
            {source.taille !== undefined && (
              <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
                {formaterOctets(source.taille)}
              </span>
            )}
            <button
              type="button"
              disabled={occupe}
              onClick={() => onRetirer(source.cle)}
              aria-label={`Retirer ${source.nom}`}
              className="ml-auto rounded border border-neutral-300 p-1 text-neutral-500 hover:bg-neutral-50 disabled:opacity-50 dark:border-neutral-700 dark:hover:bg-neutral-800"
            >
              <IconeFermer className="size-3.5" />
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
