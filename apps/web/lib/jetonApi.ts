/**
 * Le **jeton de l'API locale** (#638) — le seul endroit qui nomme sa variable.
 *
 * L'API de la Control Tower sert durcie : chaque requête porte
 * `Authorization: Bearer <jeton>`, et sans lui elle répond `401`. Personne ne le
 * manipule — `scripts/controltower/start.sh` lit celui que l'API a engendré
 * (`~/.maestro/jeton-api`) et le passe au front par l'environnement, exactement
 * comme l'URL du backend. Vide quand le poste sert l'API en régime `ouvert` : on
 * n'envoie alors aucun en-tête, et l'appel redevient celui d'avant ce lot.
 *
 * ⚠ **Une fonction, pas une constante d'import**, et c'est ce qui rend la moitié
 * cliente du contrat vérifiable. Next.js inline `NEXT_PUBLIC_*` au build : le
 * résultat est le même en production, mais figé à l'import il serait hors
 * d'atteinte d'un test — `@/lib/api` est déjà évalué quand une suite démarre
 * (`tests/setup.ts` le mocke en gardant l'original), si bien qu'aucun réglage
 * posé ensuite ne l'atteindrait. Or c'est précisément « toutes les requêtes le
 * portent, le flux le porte par l'URL » qui se casse en silence quand on ajoute
 * un appel : `tests/jeton-api.test.ts` joue les deux régimes grâce à cette
 * lecture-là. Le pendant exact de `maestro.controltower.acces` côté Python :
 * une seule façon de résoudre le jeton, de chaque côté.
 */
export function jetonApi(): string {
  return (process.env.NEXT_PUBLIC_MAESTRO_API_JETON ?? "").trim();
}
