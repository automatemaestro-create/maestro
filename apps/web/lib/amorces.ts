/**
 * Le **calibre** des amorces d'un fil vide (#908, parti pris 5 de la veille
 * #899) — deux nombres, et la raison qu'ils partagent.
 *
 * ## Pourquoi ils vivent ici, et pas dans le composant
 *
 * Ils sont nés dans `components/Conversation`, qui rend les amorces et porte
 * les marqueurs de mise en page (`AMORCE_NOWRAP`, `AMORCE_HORS_SM`) dont ils
 * sont le pendant écrit. Ils en sont sortis avec #942, quand la liste
 * d'orchestration a cessé d'être une constante pour devenir une **dérivation**
 * (`lib/orchestration`, `amorcesDuProjet`) : un nom de projet est une donnée de
 * l'utilisateur, donc c'est la dérivation elle-même qui doit savoir ce qui
 * tient.
 *
 * ⚠ Et cette dérivation est dans `lib/` : lui faire importer
 * `components/Conversation` a été **mesuré faux**. L'arête `lib → components`
 * suffit à casser `tests/dernier-message.test.tsx` — sept cas, sur les deux
 * surfaces de fil, y compris l'onglet Chat d'une fiche agent qui ne propose
 * aucune amorce, donc pour une raison qui n'est pas le rendu mais l'ordre dans
 * lequel les modules s'évaluent sous le double de `@/lib/defilement`. Vérifié
 * dans les deux sens : vert avec les nombres recopiés dans `lib/orchestration`,
 * rouge avec l'import. Ce module est la troisième voie — un module de `lib/`
 * qui n'importe **rien**, que les deux côtés lisent, et où le nombre reste
 * écrit une seule fois.
 *
 * `components/Conversation` les **ré-exporte** : c'est de là que les tests et
 * la doc les nomment depuis #908, et un déménagement de fichier n'est pas une
 * raison de renommer une règle.
 */

/**
 * Combien de caractères un libellé d'amorce peut compter — le **calibre
 * mesuré** chez Duck.ai à 390 px, 10 à 28 caractères, 2 à 5 mots. Avec
 * `AMORCE_NOWRAP` (`components/Conversation`), la longueur d'une amorce est
 * toute sa largeur, et sous `sm` les deux premières doivent partager une rangée
 * à 375 px : c'est le banc qui tranche le pixel, ce plafond garde la rédaction.
 * Il vaut pour les deux listes (`lib/orchestration`, `lib/assistance`).
 */
export const CALIBRE_AMORCE = 28;

/**
 * Combien de caractères les **deux amorces visibles sous `sm`** peuvent compter
 * **à elles deux** (#908) — pour partager une rangée à 375 px, où le composeur
 * ne fait que **268,8 px** (rail de 64 px, marges de 16 px du `main`) : 262,8 px
 * pour deux boutons de `petite` taille, moins 44 px de marges intérieures et de
 * filets, à ~5,2 px le caractère en `text-annexe`. Mesuré au banc du
 * 2026-09-10 : 39 caractères tiennent (252,2 px), 42 ne tiennent plus
 * (269,7 px, 7 px de trop). C'est une **borne de rédaction**, un compte de
 * caractères n'étant qu'une approximation d'une largeur ; le pixel reste au
 * banc.
 */
export const CALIBRE_PAIRE_SOUS_SM = 40;

/**
 * La longueur d'un libellé **en points de code**, comme le calibre la compte :
 * un « É » ou un « ’ » vaut un caractère à l'écran, pas deux.
 */
export function calibreDe(libelle: string): number {
  return Array.from(libelle).length;
}
