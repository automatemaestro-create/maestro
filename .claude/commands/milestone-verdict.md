---
description: Enregistre le verdict de bouclage arbitré d'un milestone et ouvre ses réserves en tickets — ne ferme jamais le milestone
argument-hint: "[milestone]  (titre ou fragment, ex. « Phase 3 » — sans argument, les rapports de bouclage en attente d'arbitrage te sont proposés)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), Read, Write, Skill
---

<!-- Cette commande n'a ni navigateur, ni stack, ni suite de tests, et c'est voulu : elle ne rejoue
     aucune pièce. Exercer le livrable est le travail de `/milestone-bilan` ; celle-ci enregistre ce
     qu'une personne a conclu de ses pièces. Lui donner de quoi vérifier l'inviterait à re-juger un
     verdict qu'elle a pour seule mission de faire tenir.

     Elle porte le nom du verbe `lib.sh milestone-verdict` (#757) et ce n'est pas la même chose :
     le verbe lit ou écrit une section, la commande est le geste qui décide de l'écrire. C'est la
     forme qui les distingue partout ci-dessous — un `bash scripts/gitlab/lib.sh …` est le verbe. -->


Tu vas **enregistrer un verdict de bouclage** qu'une personne a arbitré, et faire de ses réserves
des **tickets**.

C'est la seconde moitié du bouclage. `/milestone-bilan` exerce le livrable sur pièces et **propose**
un verdict ; elle s'arrête là, sur une question. Celle-ci prend la réponse et la fait **tenir** :
dans le jalon, où la convocation la lira, et dans des tickets, où les réserves seront suivies.

Le partage est celui que le dépôt tient partout — un verbe **lit** et propose, un autre
**enregistre** : `arbitrage` / `arbitre` sur les lots parallélisables (#562), `touche-surface` /
`veille-arbitre` sur la veille de conception (#714). Ce sont deux commandes et non deux étapes de
la même, pour deux raisons :

- **Un bilan est long** — stack de démo, captures, clips, suites jouées — et l'arbitrage n'arrive
  pas toujours dans la foulée. Enchaîner l'enregistrement à la fin du bilan ferait perdre tout
  verdict que personne n'a arbitré dans la même session : c'est exactement la panne qu'on corrige
  (#608), pas celle qu'on recrée. Le rapport est un **document** ; il attend.
- **La clause finale de `/milestone-bilan` reste vraie au mot près.** Elle n'écrit rien côté forge
  parce que rien n'y est arbitré. Ici quelque chose l'est, et c'est la seule différence.

Trois règles gouvernent tout le reste :

- **Rien ne s'enregistre sans un « oui » explicite** — ni le verdict, ni un seul ticket de réserve.
  Une réserve peut être **acceptée telle quelle** : c'est une décision, pas un oubli.
- **Aucun verdict ne se fabrique ici.** Sans rapport de bouclage, tu ne rédiges pas un verdict à la
  place de quelqu'un — symétrie exacte du refus de `/milestone-bilan` d'écrire les critères qui lui
  manquent : ce serait l'examen rédigé après l'épreuve, et il rendrait toujours un `GO`.
- **Aucune commande ne ferme un jalon** (`docs/10 §3.4`), celle-ci pas davantage. Un `GO avec
  réserves` le laisse **fermable** ; le fermer reste un geste humain, et tu ne le proposes sous
  aucune forme.

---

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. **Résous le jalon.** Liste-les : `bash scripts/gitlab/lib.sh milestones` (TSV : `titre`, `etat`,
   `debut`, `echeance`, `fermes`, `total`, `rail` — la ligne d'en-tête `#` s'ignore). Garde la
   **ligne entière** : son `rail` décide du milestone des tickets de réserve à l'étape 6, et ses
   compteurs sont cités dans la section consignée.
   - `$ARGUMENTS` renseigné → cherche le jalon dont le titre **contient** ce fragment, sans tenir
     compte de la casse. **Zéro ou plusieurs correspondances : arrête-toi**, affiche les candidats
     et demande lequel — ne devine pas.
   - `$ARGUMENTS` vide → liste les **rapports de bouclage présents** (`docs/bilans/*.md`) dont le
     jalon n'a **pas encore** de verdict consigné (étape 4), et demande lequel arbitrer.

     ⚠ **Cette liste est locale, et ça se dit.** Le rapport n'est pas commité (décision de
     `/milestone-bilan`, comme pour `/milestone-presentation`) : un bilan produit sur une autre
     machine, ou dans un worktree depuis ramassé, n'apparaît pas ici. Une liste vide ne veut donc
     pas dire « aucun bouclage n'attend » — nomme le jalon à la main dans ce cas.

   Le titre retenu doit être **exact** : c'est la clé de l'API pour tout ce qui suit.

3. **Retrouve le rapport** — `docs/bilans/<slug>.md`, le `<slug>` du titre comme pour les
   présentations (« Phase 3 — V2 » → `phase-3-v2`).
   - **Présent** → lis-le **en entier**. Il porte les critères numérotés (`C1`, `C2`, …), le tableau
     critère par critère, les réserves rattachées à leur critère et le verdict proposé. C'est ton
     matériau : ne le reconstitue pas, ne le résume pas de mémoire, cite-le.
   - **Absent → ne rédige pas de verdict.** Dis-le en clair, nomme le geste qui débloque —
     `/milestone-bilan "<titre-exact>"` — et rends la main.

     ⚠ Une seule exception, étroite : la personne **apporte elle-même** son verdict et ses réserves
     (bouclage mené ailleurs, rapport perdu avec son worktree). Tu enregistres alors **ce qu'elle
     dicte**, mot pour mot, et la section consignée porte « rapport : absent » plutôt qu'un chemin
     qui ne mène à rien. Tu n'en reconstruis aucune ligne.

4. **Lis ce qui est déjà consigné** : `bash scripts/gitlab/lib.sh milestone-verdict "<titre-exact>"`.
   - **Code `3`** → aucun verdict, le cas nominal. Rien à dire.
   - **Code `0`** → un verdict est déjà là. **Relaie-le**, et annonce que tu vas le **remplacer** :
     le verbe met sa section à jour, et reboucler après correction d'une réserve bloquante est
     précisément ce qu'un `NO-GO` appelle. Surtout, **repères-y les réserves déjà ouvertes en
     tickets** — leurs numéros y sont, et c'est **le seul chemin** pour ne pas les rouvrir en
     double. N'improvise aucune recherche dans le texte des tickets : la relation vit à un seul
     endroit, cette section.

5. **Présente, puis demande l'arbitrage.** Rappelle en quelques lignes : le verdict **proposé** par
   le rapport, le compte de critères tenus / en défaut / non couverts, et chaque réserve avec son
   critère et son caractère bloquant. Puis demande lequel est **retenu**.

   C'est une **vraie pause** : n'écris rien avant la réponse.

   - Le verdict retenu **peut différer** de celui que le rapport proposait — c'est le sens du mot
     arbitrage : une réserve jugée bloquante par la machine ne l'est peut-être pas, et l'inverse.
     Enregistre ce que la personne retient, jamais ce que le rapport proposait.
   - « Pas maintenant » est une réponse : n'écris rien, dis que le rapport reste là et que la
     convocation continue de signaler le jalon. C'est un état normal, pas un échec.

6. **Propose les réserves en tickets — une par une.** Écarte d'abord celles qui ont déjà leur
   ticket (étape 4). Pour chacune des autres, montre ce qui sera créé : le titre, le type, l'agent,
   la priorité, le critère dont elle vient et **ce qui a été observé**. Puis demande.

   - **Un « oui » par réserve.** Une réserve **acceptée telle quelle** est une décision assumée, et
     un ticket ouvert d'office sur une réserve assumée est un ticket que personne ne fermera.
   - Un « oui » global (« ouvre-les toutes ») vaut pour toutes — mais **il se demande**, il ne se
     suppose pas.
   - La création passe par le skill **`/ticket-create`**, jamais par un `gh issue create` recopié
     ici : le corps de template, les labels, le milestone, l'état « À faire » et l'item de projet en
     dépendent, et cette commande en est la source unique.

   Deux choses à ne pas laisser au hasard dans ce qui est créé :

   - **Le milestone est le COURANT, jamais le jalon qu'on vient de boucler.** Une réserve se traite
     maintenant, dans la phase en cours. L'inscrire au jalon bouclé le **dé-solderait** — il
     repasserait à `open_issues > 0`, donc cesserait d'être fermable, et le bouclage qu'on
     enregistre se retournerait contre lui-même.

     Le geste sûr est de **ne rien forcer** : `/ticket-create` pose le courant tout seul
     (`current-milestone <rail>`), et c'est son défaut qui est la bonne réponse. **Ne lui passe donc
     pas `--milestone`** — le seul cas où l'on nommerait un jalon à la main est précisément celui
     qu'il faut éviter. Ce que tu lui indiques est le **rail**, celui du jalon bouclé (colonne
     `rail` de l'étape 2) : une réserve d'un jalon d'outillage est de l'outillage, comme un lot
     hérite du rail de son parent (#617).
   - **La première ligne du corps est le renvoi**, de forme fixe :

     ```
     Réserve du bouclage de « <titre exact du jalon> » — critère C<n>, verdict du <AAAA-MM-JJ>.
     ```

     Elle est là pour la **personne** qui tombera sur ce ticket sans son contexte, exactement comme
     le `Sous-ticket de #<parent>` d'un lot. Le lien que les **machines** suivent est l'autre : les
     numéros que la section `## Verdict` va porter à l'étape 7. Un fait, un support — ne fais
     jamais dépendre l'idempotence d'une recherche plein texte sur cette phrase.

7. **Compose la section, puis consigne-la.** Écris le corps dans un fichier — `Write`, dans le
   **dossier de scratchpad** de la session, jamais dans le dépôt — puis :
   ```
   bash scripts/gitlab/lib.sh milestone-verdict "<titre-exact>" <fichier>
   ```

   **La section doit se suffire à elle-même**, et c'est ce qui décide de son contenu : le rapport
   n'est pas commité, donc son chemin est un renvoi **local** que personne d'autre n'ouvrira. Ce qui
   survit est ce qui est écrit là. Elle porte donc, dans cet ordre :

   1. le **verdict retenu** — `GO`, `GO avec réserves` ou `NO-GO` — et sa **date** ;
   2. l'état du jalon au bouclage (`N/N` soldé, ou le nombre de tickets encore ouverts) ;
   3. le **compte** de critères tenus / en défaut / non couverts ;
   4. **chaque réserve sur sa ligne**, avec son critère et **son sort** : `→ #<iid>` quand un ticket
      a été ouvert, `acceptée telle quelle` quand la personne l'a assumée. **Jamais une réserve sans
      son sort** — une réserve muette est indiscernable d'un oubli, et c'est précisément ce qu'on
      cherche à ne plus perdre ;
   5. le **renvoi au rapport** : `docs/bilans/<slug>.md`, en disant sur place qu'il n'est pas
      commité (ou « rapport : absent », cas de l'exception de l'étape 3).

   Codes du verbe : `0` écrit — ou déjà à jour, il le dit — · `2` fichier absent ou vide · `1` jalon
   inconnu ou forge muette. Il **ne sait pas retirer** une section (#757) : il n'existe pas de geste
   inverse, et c'est voulu — on n'efface pas un verdict rendu.

8. **Constate que la convocation a cessé** : `bash scripts/gitlab/lib.sh milestones-a-boucler`. Le
   jalon ne doit plus y figurer (code `3` et sortie muette s'il était le seul).

   C'est la **moitié observable** du geste — tout le reste est du texte, celle-ci se vérifie. S'il y
   figure encore, la consignation n'a pas pris : dis-le franchement et ne conclus pas au bouclage.

9. **Ne ferme pas le jalon, et ne propose pas de le fermer.** Dis simplement où il en est :
   - un `GO` ou un `GO avec réserves` le laisse **fermable** — ses réserves sont nommées et suivies,
     elles ne le retiennent pas ;
   - un `NO-GO` dit qu'il ne doit pas l'être encore.

   Dans les deux cas la fermeture est une **décision humaine**, prise sur la page du jalon.

10. **Résumé court** : le jalon et son état, le verdict **enregistré** avec sa date, le compte de
    critères, chaque réserve avec son sort (ticket ouvert et son iid, ou acceptée), le constat de
    l'étape 8, et où en est la fermeture. Signale ce qui a échoué plutôt que de le taire.

## Ce que la commande ne fait pas

- **Elle ne rejoue aucune pièce** — ni stack, ni capture, ni suite, ni navigateur. Un verdict à
  revérifier se revérifie en rejouant `/milestone-bilan`, pas ici.
- **Elle ne juge pas le verdict qu'on lui donne**, ni la pertinence des critères qui l'ont produit.
  Elle l'enregistre.
- **Elle ne commite pas le rapport.** Comme pour `/milestone-presentation`, c'est une décision
  humaine — et c'est la raison pour laquelle la section consignée doit se suffire.
- **Elle ne ferme aucun jalon**, ne touche à aucun cycle de vie de ticket au-delà de ce que
  `/ticket-create` pose, n'ouvre aucune PR et ne merge rien.
