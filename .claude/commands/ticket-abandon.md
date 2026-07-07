---
description: Clôt un ticket sans le réaliser — pose le statut « Abandonné » (won't-do) ou « Doublon » et ferme le ticket
argument-hint: "<iid> [doublon [<iid-original>]]  — sans « doublon », c'est un abandon (won't-do)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*)
---

Tu vas **clôturer un ticket sans qu'il soit réalisé** (cette commande est autosuffisante ; réf.
complète `docs/10-workflow-git.md` §3, non chargée automatiquement, à n'ouvrir qu'en cas de doute).
Deux variantes, portées par le champ **Status natif** (lifecycle « Maestro »), catégorie `canceled` :
- **Abandonné** (won't-do) — décision de ne pas faire ce ticket.
- **Doublon** — ce ticket fait double emploi avec un autre.

C'est une action **volontaire et consignée** : ne clôture jamais silencieusement, demande toujours
une raison et confirmation.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require` ; arrête-toi si non authentifié.

2. Détermine l'**IID** : utilise `$ARGUMENTS` s'il en contient un, sinon extrais-le du nom de la
   branche courante (`<type>/<iid>-<slug>`). Si rien ne le donne, demande-le.

3. Détermine la **variante** depuis `$ARGUMENTS` : si le mot `doublon` (ou `duplicate`) est
   présent → statut « **Doublon** » ; sinon → « **Abandonné** ». Pour un doublon, note aussi
   l'`iid` du ticket **original** s'il est fourni (sinon demande-le, c'est utile dans la trace).

4. Affiche le ticket concerné (`glab issue view <iid>`) et **demande confirmation** à
   l'utilisateur, avec une **raison** (pourquoi on l'abandonne, ou de quel ticket c'est le
   doublon). N'enchaîne pas sans cette confirmation explicite.

5. Consigne la raison en commentaire sur le ticket avant de le fermer :
   ```
   glab issue note <iid> --message "Clôturé (<Abandonné|Doublon>) : <raison>[ — doublon de #<iid-original>]"
   ```

6. Pose le statut « canceled » correspondant via le helper (il dérive le GID par nom, pas de GID en
   dur) :
   ```
   bash scripts/gitlab/lib.sh set-status <iid> "Abandonné"   # ou "Doublon"
   ```
   Poser un statut de catégorie `canceled` **ferme normalement le ticket automatiquement**.

7. Vérifie l'état final : `glab issue view <iid> --output json` et inspecte `state`.
   - Si `state` vaut déjà `closed` : parfait, rien de plus.
   - S'il est encore `opened` : ferme-le explicitement avec `glab issue close <iid>`, **puis
     re-vérifie** que le statut est resté « Abandonné »/« Doublon » (une fermeture peut basculer
     un lifecycle vers son statut « done » par défaut) ; si le statut a changé, repose-le avec
     `bash scripts/gitlab/lib.sh set-status <iid> "<Abandonné|Doublon>"`.

8. Si une **branche** locale existe pour ce ticket et qu'aucun travail n'y est à sauvegarder, tu
   peux proposer de la supprimer — mais **uniquement en local** et seulement après accord de
   l'utilisateur (`git branch -D <branche>`). Ne touche pas à une éventuelle branche distante ni à
   une MR ici : si une MR ouverte existe, signale-la et laisse l'utilisateur décider de la fermer.

9. Termine par un résumé : IID, statut posé (Abandonné/Doublon), raison consignée, état du ticket
   (fermé), et le sort de la branche locale le cas échéant.
