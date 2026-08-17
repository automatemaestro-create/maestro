---
description: Clôt un ticket sans le réaliser — pose l'état « Abandonné » (won't-do) ou « Doublon » et ferme le ticket
argument-hint: "<iid> [doublon [<iid-original>]]  — sans « doublon », c'est un abandon (won't-do)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*)
---

Tu vas **clôturer un ticket sans qu'il soit réalisé** (cette commande est autosuffisante ; réf.
complète `docs/10-workflow-git.md` §3, non chargée automatiquement, à n'ouvrir qu'en cas de doute).
Deux variantes, portées par les **labels `workflow::*`** qui tiennent le cycle de vie (docs/10 §3) :
- **Abandonné** (won't-do) — décision de ne pas faire ce ticket.
- **Doublon** — ce ticket fait double emploi avec un autre.

⚠ Ces deux états ne ferment **rien** tout seuls : poser un label ne ferme pas un ticket. La
fermeture est donc **toujours** un geste explicite ici (étape 7), et l'ordre compte — l'état
d'abord, la fermeture ensuite.

C'est une action **volontaire et consignée** : ne clôture jamais silencieusement, demande toujours
une raison et confirmation.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require` ; arrête-toi si non authentifié.

2. Détermine l'**IID** : utilise `$ARGUMENTS` s'il en contient un, sinon extrais-le du nom de la
   branche courante (`<type>/<iid>-<slug>`). Si rien ne le donne, demande-le.

3. Détermine la **variante** depuis `$ARGUMENTS` : si le mot `doublon` (ou `duplicate`) est
   présent → état « **Doublon** » ; sinon → « **Abandonné** ». Pour un doublon, note aussi
   l'`iid` du ticket **original** s'il est fourni (sinon demande-le, c'est utile dans la trace).

4. Affiche le ticket concerné (`bash scripts/gitlab/lib.sh issue-brief <iid>`) et **demande
   confirmation** à l'utilisateur, avec une **raison** (pourquoi on l'abandonne, ou de quel ticket
   c'est le doublon). N'enchaîne pas sans cette confirmation explicite.

5. Consigne la raison en commentaire sur le ticket avant de le fermer. Le texte passe **par un
   fichier** (écris-le avec `Write`), et le commentaire par le helper — il vaut des deux côtés de la
   bascule et garde l'UTF-8 intact :
   ```
   bash scripts/gitlab/lib.sh issue-note <iid> <fichier>
   ```
   Contenu du fichier : `Clôturé (<Abandonné|Doublon>) : <raison>[ — doublon de #<iid-original>]`.

6. Pose l'**état** correspondant via le helper (il dérive les GID des labels par nom, pas de GID en
   dur) :
   ```
   bash scripts/gitlab/lib.sh set-workflow <iid> "Abandonné"   # ou "Doublon"
   ```
   Le helper ajoute la cible et **retire les cinq autres `workflow::*` dans le même appel** —
   l'exclusion mutuelle des labels scopés étant Premium, rien ne l'assurerait à notre place
   (docs/10 §3). Le ticket reste **ouvert** : le poser ne le ferme pas.

7. **Ferme le ticket** — c'est l'étape qui le clôt, plus aucun état ne le fait à sa place :
   ```
   gh issue close <iid>
   ```
   (`glab issue close <iid>` tant que la forge active est GitLab — `bash scripts/gitlab/lib.sh
   forge-cli` tranche.) La fermeture est sous règle **`ask`** des deux côtés : la confirmation
   demandée est voulue, ne cherche pas à la contourner.

   Puis vérifie l'état final : `bash scripts/gitlab/lib.sh issue-raw <iid>` doit rendre
   `state:` `closed` et le label `workflow::abandonne` (ou `workflow::doublon`). Une fermeture ne
   rebascule aucun label : si le `workflow::*` attendu n'y est pas, c'est que l'étape 6 a échoué —
   repose-le et signale-le.

8. Si une **branche** locale existe pour ce ticket et qu'aucun travail n'y est à sauvegarder, tu
   peux proposer de la supprimer — mais **uniquement en local** et seulement après accord de
   l'utilisateur (`git branch -D <branche>`). Ne touche pas à une éventuelle branche distante ni à
   une PR ici : si une PR ouverte existe, signale-la et laisse l'utilisateur décider de la fermer.

9. Termine par un résumé : IID, état posé (Abandonné/Doublon), raison consignée, ticket fermé, et
   le sort de la branche locale le cas échéant.
