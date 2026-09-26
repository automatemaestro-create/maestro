/**
 * **Les lignes d'un différentiel**, telles qu'on les lit — une seule fois écrites (#1161).
 *
 * Deux écrans montrent ce qu'un texte deviendra : l'éditeur de playbook, qui compare
 * une réécriture à son brouillon (#261), et la carte d'une pièce d'outillage, qui
 * montre ce qui sera écrit dans le projet avant qu'on l'accepte (#1161). La veille de
 * #1161 l'a posé en toutes lettres : le rendu ligne à ligne **sort** de l'éditeur
 * plutôt que d'être recopié une troisième fois (la carte de validation en porte un
 * autre, par fichier) — et ses tons partent avec lui.
 *
 * Ce composant ne décide que des **lignes** : le signe en gouttière (`+`, `−`, rien),
 * la teinte d'une ligne qui change, le repli « ⋯ N lignes inchangées ». Le cadre —
 * une région qui défile dans l'éditeur, une carte bornée sans zone défilante dans le
 * fil — reste à l'appelant, qui sait où il vit.
 *
 * ⚠ **L'état ne tient jamais à la couleur seule** (filet a11y, docs/30 §1) : le signe
 * est toujours là, la teinte ne fait que le doubler. C'est ce qui permet à la carte
 * d'un fichier **neuf** de retirer l'aplat (`aplatDesAjouts={false}`) sans rien
 * perdre : un fichier qu'on crée n'a que des ajouts, et une page entière d'aplat vert
 * se lisait mal (regard neuf de #1161) — le « + » en gouttière suffit à le dire.
 *
 * ⚠ **Les tons d'un ajout et d'un retrait n'ont pas de token.** Un ajout n'est pas un
 * « positif », un retrait n'est pas une « alerte » : `tests/couleurs.test.ts` les
 * inscrit comme résidu, ici et nulle part ailleurs. Le manque est nommé par la veille
 * de #1161 (« La palette porte les tons d'un diff »), pas comblé au passage.
 *
 * Deux reprises de la relecture de #1161, sur la vraie stack :
 *
 * - **une ligne qui se replie reste sous son texte**, jamais sous le signe : le signe
 *   a sa colonne. Sans elle, à 320 px, une ligne logique repliée cinq fois mêlait ses
 *   suites à la gouttière et les signes se perdaient dans le texte ;
 * - **en sombre, l'aplat est estompé** (`/40`) : plein, le cramoisi d'un retrait
 *   dominait l'écran dans une carte déjà teintée, là où le clair reste pâle.
 */

import type { EntreeDiff } from "@/lib/diff";

/** La teinte d'une ligne selon ce qu'elle devient. */
const TON_AJOUT =
  "bg-emerald-50 text-emerald-900 dark:bg-emerald-950/40 dark:text-emerald-200";
const TON_RETRAIT = "bg-rose-50 text-rose-900 dark:bg-rose-950/40 dark:text-rose-200";

export function LignesDiff({
  entrees,
  aplatDesAjouts = true,
}: {
  /** Les lignes et les plages repliées, dans l'ordre (`differencier` puis `condenser`). */
  entrees: EntreeDiff[];
  /**
   * Teinter les lignes ajoutées. `false` pour un fichier **neuf** : tout y est ajout,
   * et la teinte n'apprend rien que le signe ne dise déjà.
   */
  aplatDesAjouts?: boolean;
}) {
  return (
    <div className="font-mono text-annexe">
      {entrees.map((entree, i) =>
        entree.type === "repli" ? (
          <p
            key={`r${i}`}
            className="bg-surface-creuse px-2 py-0.5 text-center text-texte-secondaire"
          >
            ⋯ {entree.lignes} lignes inchangées
          </p>
        ) : (
          <p
            key={`l${i}`}
            className={
              "flex px-2 py-0.5 " +
              (entree.type === "ajout"
                ? aplatDesAjouts
                  ? TON_AJOUT
                  : "text-texte"
                : entree.type === "retrait"
                  ? TON_RETRAIT
                  : "text-texte-secondaire")
            }
          >
            <span aria-hidden className="w-4 shrink-0 select-none whitespace-pre opacity-60">
              {entree.type === "ajout" ? "+" : entree.type === "retrait" ? "−" : " "}
            </span>
            <span className="min-w-0 flex-1 whitespace-pre-wrap break-words">
              {entree.texte || " "}
            </span>
          </p>
        ),
      )}
    </div>
  );
}
