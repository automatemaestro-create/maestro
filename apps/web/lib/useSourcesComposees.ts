"use client";

/**
 * Les sources qu'un message est en train de composer (#482, lot 1 de #481).
 *
 * Le fil accepte désormais ce que le formulaire acceptait : des fichiers (glissés
 * ou choisis), un dossier du poste, une adresse. Ce hook porte **l'état de la
 * composition** et les gestes qui le changent — pas le rendu, et pas l'envoi.
 *
 * Un hook plutôt qu'un état local dans `FilChat`, pour la raison qui a mis
 * `lib/sources.ts` hors des composants (#319) : c'est de la **donnée et des
 * transitions**, testables sans monter de DOM, et les deux surfaces de fil de
 * l'application (l'onglet Chat d'un agent, le panneau d'assistance) n'auront pas
 * à s'accorder sur une copie chacune.
 *
 * Le **téléversement est ici et l'envoi ailleurs**, et c'est la frontière qui
 * compte : `declarer()` transforme des octets en identifiants
 * (`POST /api/sources`, #317) et rend les sources déclarées ; ce qu'on en fait —
 * un message du fil aujourd'hui, un lancement demain — ne le regarde pas. C'est
 * la même séquence que `ComposerObjectif.lancer()`, à ceci près qu'elle est
 * nommée : un fichier voyage par son **identifiant**, jamais par ses octets, et
 * c'est ce qui garantit qu'il n'atterrit pas dans le dossier de l'utilisateur.
 */

import { useCallback, useMemo, useState } from "react";

import { televerserSources } from "./api";
import { nomDepuisChemin } from "./projets";
import { declarationDe, type SourceComposee } from "./sources";
import {
  SOURCE_DOSSIER,
  SOURCE_FICHIER,
  SOURCE_URL,
  type SourceDeclaree,
} from "./types";

/** Une clé locale stable : deux fichiers peuvent porter le même nom. */
let compteurCles = 0;
function cleSuivante(): string {
  compteurCles += 1;
  return `source-fil-${compteurCles}`;
}

export type SourcesComposees = {
  /** Les sources déclarées, dans l'ordre où elles ont été composées. */
  sources: SourceComposee[];
  /** Ajoute des fichiers (bouton, glisser-déposer, collage) — ignore une liste vide. */
  deposer: (choisis: FileList | File[] | null) => void;
  /** Ajoute un dossier de références, désigné par l'explorateur du backend. */
  ajouterDossier: (chemin: string) => void;
  /** Ajoute une adresse ; rend `false` si la saisie est vide (rien n'est ajouté). */
  ajouterUrl: (brut: string) => boolean;
  /** Retire une source par sa clé locale. */
  retirer: (cle: string) => void;
  /** Vide la composition — ce que fait un envoi réussi. */
  vider: () => void;
  /**
   * Téléverse les octets en attente et rend les sources **déclarées**, prêtes à
   * partir. Rejette avec `ErreurSource` si le dépôt est refusé (plafond, nom) :
   * le refus arrive alors avant tout envoi, et rien n'est perdu.
   */
  declarer: () => Promise<SourceDeclaree[]>;
};

export function useSourcesComposees(): SourcesComposees {
  const [sources, setSources] = useState<SourceComposee[]>([]);

  const fichiers = useMemo(
    () =>
      sources
        .filter((source) => source.type === SOURCE_FICHIER && source.fichier !== null)
        .map((source) => source.fichier as File),
    [sources],
  );

  const deposer = useCallback((choisis: FileList | File[] | null) => {
    const liste = choisis === null ? [] : Array.from(choisis);
    if (liste.length === 0) return;
    setSources((avant) => [
      ...avant,
      ...liste.map((fichier) => ({
        cle: cleSuivante(),
        type: SOURCE_FICHIER,
        nom: fichier.name,
        valeur: "",
        taille: fichier.size,
        id: null,
        fichier,
      })),
    ]);
  }, []);

  const ajouterDossier = useCallback((chemin: string) => {
    setSources((avant) => [
      ...avant,
      {
        cle: cleSuivante(),
        type: SOURCE_DOSSIER,
        nom: nomDepuisChemin(chemin),
        valeur: chemin,
        id: null,
        fichier: null,
      },
    ]);
  }, []);

  const ajouterUrl = useCallback((brut: string) => {
    const propre = brut.trim();
    if (propre === "") return false;
    setSources((avant) => [
      ...avant,
      {
        cle: cleSuivante(),
        type: SOURCE_URL,
        nom: propre,
        valeur: propre,
        id: null,
        fichier: null,
      },
    ]);
    return true;
  }, []);

  const retirer = useCallback((cle: string) => {
    setSources((avant) => avant.filter((source) => source.cle !== cle));
  }, []);

  const vider = useCallback(() => setSources([]), []);

  const declarer = useCallback(async () => {
    // Aucun fichier déposé, aucun appel : un message qui ne joint qu'un dossier
    // ou une adresse ne touche pas au dépôt de téléversement.
    const televerses =
      fichiers.length === 0 ? [] : (await televerserSources(fichiers)).sources;
    let rang = 0;
    return sources.map((source) =>
      source.type === SOURCE_FICHIER && source.fichier !== null
        ? declarationDe({ ...source, id: televerses[rang++]?.id ?? null })
        : declarationDe(source),
    );
  }, [fichiers, sources]);

  return { sources, deposer, ajouterDossier, ajouterUrl, retirer, vider, declarer };
}
