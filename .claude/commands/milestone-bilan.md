---
description: Exerce le livrable d'un milestone sur pièces et propose un verdict de bouclage — GO, GO avec réserves, NO-GO — critère par critère
argument-hint: "[milestone]  (titre ou fragment, ex. « Phase 3 » — sans argument, les jalons actifs soldés te sont proposés)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), Bash(node:*), Bash(npm:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*), Read, Write, Skill
---

<!-- `mcp__chrome-maestro` est ABSENT de cette liste, et c'est voulu : cette commande n'a aucun
     navigateur en propre. Ce qu'il faut voir dans un vrai navigateur est vu par `captures.sh`, par
     le skill `verify` et par le skill `banc-mise-en-page` — les déclarer, elle, l'inviterait à
     faire elle-même ce que ces trois-là font déjà, et à en tenir une seconde version. -->


Tu vas **boucler un milestone** : exercer son livrable **sur pièces** et proposer un **verdict** —
`GO`, `GO avec réserves`, `NO-GO` — critère par critère.

C'est le geste que `/milestone-presentation` ne fait pas. Elle **montre** ce qui a été construit ;
elle n'**exerce** rien et ne conclut sur rien. Les deux se complètent et ne se remplacent pas : on
présente une phase à quelqu'un, on la **boucle** avant de la fermer.

Commande **de supervision** : tu lis la forge, tu montes la stack, tu pilotes un navigateur par
les outils qui savent le faire, et tu **écris un fichier**. Tu ne touches à **rien** du cycle de
vie — ni Status, ni PR, ni merge, **ni fermeture de milestone** (`docs/10 §3.4` : c'est une
décision humaine, et cette commande ne la prend pas davantage qu'une autre).

Deux règles gouvernent tout le reste, et rien de ce qui suit ne les défait :

- **Le verdict est PROPOSÉ, jamais rendu.** Tu rassembles les pièces et tu proposes une
  conclusion ; l'arbitrage appartient à une personne. Un `NO-GO` posé par une machine sur une
  phase entière serait le contraire de ce dispositif — c'est le partage de `/orchestrate` sur
  l'arbitrage des lots, de `queue.sh` sur `.claude/` et de `/ticket-start` sur la veille de
  conception : **ce qui est automatique est la détection du manque, jamais le verdict.**
- **Un critère qu'aucune pièce ne couvre est nommé comme tel, jamais coché.** Un ✓ sur une
  question jamais posée est pire qu'une case vide : c'est lui qui a laissé quatorze jalons se
  fermer sur « ça a été écrit ». Tu ne conclus **que** sur ce que tu as exercé.

---

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. **Résous le jalon.** Liste-les : `bash scripts/gitlab/lib.sh milestones` (TSV : `titre`,
   `etat`, `debut`, `echeance`, `fermes`, `total`, `rail` — la ligne d'en-tête `#` s'ignore).
   Garde la **ligne entière** du jalon retenu : tu auras besoin de son état, de ses compteurs et
   de son rail aux étapes suivantes.
   - `$ARGUMENTS` renseigné → cherche le jalon dont le titre **contient** ce fragment, sans tenir
     compte de la casse (`Phase 3` → `Phase 3 — V2`). **Zéro ou plusieurs correspondances :
     arrête-toi**, affiche les candidats et demande lequel — ne devine pas.
   - `$ARGUMENTS` vide → **ne choisis pas à sa place.** Affiche les jalons **actifs entièrement
     soldés** (`etat == active`, `fermes == total`, `total > 0`) : une phase finie mais pas encore
     fermée, c'est-à-dire exactement le moment où un bouclage est dû. **Il n'y en a le plus
     souvent aucun** — au 2026-08-28, aucun des cinq jalons actifs n'était soldé —, et ce n'est
     pas une panne : dis-le, et rappelle qu'un jalon **déjà fermé** se boucle très bien en le
     nommant (les quatorze fermés sans bouclage sont la raison d'être de cette commande). Puis
     demande lequel. *Quel* jalon devrait être bouclé n'est pas la question de cette commande :
     elle boucle celui qu'on lui donne.

   Le titre retenu doit être **exact** : c'est la clé de l'API pour tout ce qui suit.

   **Un jalon non soldé se boucle quand même**, et ça se dit. Un bouclage à mi-phase est
   légitime — c'est même la bonne façon de ne pas découvrir un défaut à la fin —, mais le verdict
   porte alors sur un livrable **encore en mouvement** : note-le en tête du rapport et dans ton
   résumé, avec le compte de tickets encore ouverts. Le taire ferait lire un `GO` sur une phase à
   moitié construite.

   **Vérifie enfin si un verdict a déjà été consigné** :
   `bash scripts/gitlab/lib.sh milestone-verdict "<titre-exact>"`. Code `3` = aucun, le cas
   nominal, **rien à dire**. Un verdict déjà là n'interdit rien — reboucler après correction est
   exactement ce qu'une réserve bloquante appelle — mais **dis-le** : tu reboucles, et le rapport
   doit citer ce qui avait été conclu.

3. **Lis les critères de sortie** — c'est le sujet de tout ce qui suit :
   ```
   bash scripts/gitlab/lib.sh milestone-criteres "<titre-exact>"
   ```
   - **Code `0`** → les critères sont sur stdout. **Numérote-les** (`C1`, `C2`, …) et garde-les
     mot pour mot : c'est à ces numéros que les réserves seront rattachées, et c'est le texte du
     jalon qui fait foi, jamais ta reformulation.
   - **Code `3` (aucun critère) → ARRÊTE-TOI.** Dis-le en clair : ce jalon n'a pas de critères de
     sortie, il n'y a donc rien à vérifier et **aucun verdict n'est possible**. Nomme le geste qui
     débloque — `bash scripts/gitlab/lib.sh milestone-criteres "<titre-exact>" <fichier>` — et
     rends la main.

     ⚠ **N'écris pas les critères toi-même pour pouvoir continuer**, et ne le propose pas. Des
     critères rédigés à l'heure du bouclage sont des critères taillés sur ce qui a été livré :
     c'est l'examen écrit après l'épreuve, et il rendra toujours un `GO`. Les critères se posent
     quand la phase se **cadre**, par une personne, et c'est précisément parce qu'ils manquaient
     que ce geste avait disparu. Un bouclage sans critère n'est pas un verdict, c'est une opinion.

4. **Dérive les surfaces livrées par la phase** — jamais devinées :
   ```
   bash scripts/gitlab/lib.sh milestone-issues "<titre-exact>"
   bash scripts/presentation/ecrans-touches.sh --check <iid> <iid> …
   ```
   Le premier rend les tickets (TSV : `iid`, `statut`, `type`, `agent`, `prio`, `titre`) ; le
   second, en **un seul appel** avec tous les iid, rend une ligne par (ticket, écran)
   (`iid`, `route`, `cle`, `fichiers`), dérivée des `Refs #<iid>` / `Closes #<iid>` que le hook
   `commit-msg` impose à tout commit. `--check` ajoute sur stderr la ref lue et les tickets dont
   **aucun commit** ne porte de référence — c'est-à-dire les non mergés, à distinguer d'un ticket
   sans écran.

   Trois résultats, à lire différemment :
   - **une ou plusieurs lignes avec une `cle`** → les écrans de ce ticket ; la `cle` est
     exactement celle du manifeste de captures ;
   - **une ligne dont `route` et `cle` valent `-`** → surface visible mais **indéterminée** (le
     ticket n'a touché que des composants partagés ou la coquille commune) ;
   - **aucune ligne** → le ticket **n'a pas de surface visible** (moteur, CI, doc, outillage).

   ⚠ **Aucune surface n'est un résultat, pas une panne.** Un jalon de rail `outillage` ne livre
   pas d'écran : il livre des scripts, des verbes, des commandes et de la CI. Ne conclus jamais
   « rien à vérifier » d'un « rien à photographier » — c'est l'étape 5b qui l'exerce.

   L'échec de la dérivation n'arrête pas la commande : continue sans surfaces dérivées, et
   note-le comme une pièce **manquante** à l'étape 6 (des critères deviendront « non couverts »).

5. **Exerce sur pièces.** C'est le cœur, et il obéit à une règle : **ce qu'on exerce est dicté par
   les critères**, jamais par ce que l'outillage sait faire. Un critère qui parle de temps réel
   s'exerce en coupant la WebSocket, pas en photographiant un écran.

   ⚠ **Tu n'écris aucun pilotage de navigateur, aucun lanceur, aucune capture.** Quatre exécutants
   existent, ce sont les seuls : `ecrans-touches.sh`, `captures.sh` / `parcours.mjs`, le skill
   **`verify`** et le skill **`banc-mise-en-page`**. Tu les **appelles** ; tu n'en réécris aucun.

   **5a. Les surfaces visibles** (les écrans dérivés à l'étape 4) :
   ```
   bash scripts/presentation/captures.sh --sortie <dossier-de-travail>/captures
   ```
   Utilise le **dossier de scratchpad** de la session, jamais le dépôt. Le script monte une stack
   de **production** sur ses propres ports, photographie les pages du menu, **filme les parcours**
   de `parcours.mjs` et arrête tout. Compte plusieurs minutes. `--sans-videos` s'en passe — à
   réserver aux cas où aucun critère ne porte sur un parcours.

   Le manifeste `captures.json` porte deux listes, `pages` et `videos`, et **ce qui a échoué y
   laisse sa ligne avec son erreur** : c'est une pièce à part entière. Une page `complet: false` a
   été photographiée avant d'être peuplée ; un parcours sans `fichier` a été **tenté et n'a pas
   abouti**. Regarde-les : très souvent, c'est là qu'est la réserve.

   Puis, **selon ce que les critères demandent** :
   - un critère qui porte sur le **câblage réel** — WebSocket, absence de rechargement, reprise
     après coupure, l'ensemble branché sur l'API → joue le skill **`verify`** ;
   - un critère qui porte sur la **géométrie** — hauteurs, défilement, `overflow`, éléments
     collants, points de rupture → joue le skill **`banc-mise-en-page`** sur la page concernée.
     Une suite verte ne prouve rien sur la mise en page.

   **5b. Ce qui n'a pas d'écran** (outillage, moteur, CI, documentation) :
   - la pièce est le **verbe joué**, en lecture seule, sur le dépôt réel — `lib.sh <verbe>`,
     `queue.sh --check`, `journal.sh audit`, `doctor.sh` : ce qu'il rend *est* la démonstration ;
   - ou la **suite qui le garde**, jouée sur sa cible :
     `bash scripts/ci/pytest.sh tests/test_<suite>.py` (conteneur, ×20 sur une suite d'outillage).
     Ne lance `bash scripts/ci/local.sh` que si un critère porte littéralement sur le pipeline :
     c'est un verdict d'avant-push, il coûte plusieurs minutes et il n'est pas la question ici.

   ⚠ **Lire le code n'est pas l'exercer.** Un diff dit ce qui a été écrit, jamais que ça
   fonctionne — et c'est exactement sur « ça a été écrit » que les quatorze jalons se sont fermés.
   Un critère que rien ne peut atteindre reste **non couvert** ; le déclarer tenu sur lecture est
   la seule erreur que cette commande ne doit jamais commettre.

6. **Rattache chaque critère à ses pièces.** Reprends `C1`, `C2`, … et donne à chacun **un** état,
   avec la pièce qui le porte :
   - **tenu** — une pièce le montre. **Nomme-la** (telle capture, tel clip, la sortie de tel
     verbe, telle suite jouée). Un critère « tenu » sans pièce nommée n'est pas tenu : il est
     *non couvert*, et tu le classes là.
   - **en défaut** — une pièce montre qu'il n'est pas satisfait → **réserve**, avec ce qui a été
     observé.
   - **non couvert** — aucune pièce ne s'y prononce. Nomme-le comme tel **et dis pourquoi** : pas
     de surface visible, stack qui n'a pas démarré, critère invérifiable de l'extérieur, ticket
     non mergé. Jamais coché par défaut, jamais fondu dans « tenu ».

   Puis, pour chaque réserve, tranche : **bloquante** ou **non bloquante**. C'est ce jugement-là,
   et lui seul, qui décide du verdict — dis sur quoi tu le fondes.

7. **Propose un verdict.**
   - **`GO`** — tous les critères tenus, aucune réserve.
   - **`GO avec réserves`** — aucune réserve bloquante. Les réserves et les critères non couverts
     sont nommés et suivis. Un critère **non couvert est une réserve à lui seul** (« non
     vérifié ») : il ne laisse jamais un `GO` nu.
   - **`NO-GO`** — au moins une réserve bloquante.
   - **Aucun verdict (abstention)** — si **aucun** critère n'a pu être exercé : ne propose rien et
     dis pourquoi. Une abstention n'est pas un `NO-GO` — un livrable qu'on n'a pas su éprouver
     n'est pas un livrable jugé mauvais, et les confondre ferait rejeter une phase pour une panne
     de stack.

   Un `GO avec réserves` **n'est pas un `NO-GO`** : le jalon reste fermable, ses réserves restent
   nommées.

8. **Écris le rapport** dans `docs/bilans/<slug>.md` — le `<slug>` du titre, comme les
   présentations (« Phase 3 — V2 » → `phase-3-v2`) ; crée le dossier s'il manque. Un verdict qui
   ne survit pas au terminal est un verdict perdu, et les quatre bilans qui ont tenu
   (`docs/11`, `12`, `13`, `23`) ont tenu parce que c'étaient des **documents**.

   Le plan, dans cet ordre :
   1. **En-tête** — le jalon, son état (soldé `N/N`, ou **encore en cours** avec le nombre de
      tickets ouverts), son rail, la date du jour, la commande qui a produit le rapport, et le
      verdict précédent s'il y en avait un.
   2. **Critères de sortie** — numérotés, **mot pour mot** ceux du jalon.
   3. **Ce qui a été exercé** — les surfaces dérivées, les pièces produites (captures, clips,
      `verify`, banc de mise en page, verbes joués, suites jouées) et **ce qui n'a pas pu
      l'être**, avec sa cause.
   4. **Critère par critère** — le tableau de l'étape 6 : état, pièce, observation.
   5. **Réserves** — numérotées, chacune **rattachée à son critère**, bloquante ou non.
   6. **Verdict proposé** — avec son raisonnement, et la phrase qui dit que l'arbitrage revient à
      une personne.

   ⚠ **Le rapport ne consigne rien côté forge** : la section `## Verdict` du jalon n'est pas
   écrite ici, et le fichier **n'est pas commité** — c'est une décision humaine, comme pour
   `/milestone-presentation`. Dis-le dans le résumé.

9. **Résumé court** : le jalon bouclé et son état, le compte de critères **tenus / en défaut / non
   couverts**, les pièces produites (et celles qui ont manqué), les réserves avec le nombre de
   bloquantes, le **verdict proposé**, le chemin du rapport. Signale ce qui a échoué plutôt que de
   le taire.

   Puis **pose la question** : le verdict proposé est-il celui qu'on retient ? C'est la seule
   chose que cette commande attend d'une personne, et elle ne va pas plus loin sans elle.

   **Nomme la suite dans la même phrase** : `/milestone-verdict "<titre-exact>"` (#760) prend la
   réponse, la consigne dans la section `## Verdict` du jalon — d'où la convocation de `/backlog`
   et de `doctor.sh` la lira, et cessera de signaler — et **propose** chaque réserve en ticket, une
   par une. Elle ne ferme pas davantage le jalon que celle-ci.

   Elle est une commande à part et non tes étapes 10 et suivantes pour une raison qui est le sujet
   même de ce dispositif : un bilan est long, l'arbitrage n'arrive pas toujours dans la foulée, et
   un verdict qui attendrait la fin de **ta** session serait perdu avec elle. Le rapport, lui,
   attend — c'est un document. Ne t'y substitue pas : ton travail s'arrête sur la question.

## Ce que la commande ne sait pas

À dire dans le résumé quand le cas se présente, plutôt que de le laisser deviner :

- **La stack de démo montre l'application d'aujourd'hui**, pas l'écran tel qu'il était pendant la
  phase. Un défaut qu'on y voit est un défaut **maintenant** — c'est bien ce qu'un bouclage
  cherche —, mais quelque chose de corrigé depuis n'est pas un défaut de la phase.
- **Un composant partagé ne se rattache à aucune route.** `apps/web/components/**` rend une ligne
  « indéterminée » (`-`) plutôt qu'un écran tiré au hasard ; `apps/web/lib/**` et
  `apps/web/hooks/**` ne sont pas comptés du tout.
- **Un ticket non mergé n'a aucun commit sur `origin/main`** : il ne rend aucun écran, comme un
  ticket sans surface visible. C'est `--check` qui les distingue, sur stderr.
- **Un jalon d'outillage n'a rien à photographier.** Son livrable s'exerce en **jouant** ses
  verbes et les suites qui les gardent ; l'absence de capture n'y est pas une lacune du bouclage.
- **Elle ne juge pas la pertinence des critères**, seulement s'ils sont tenus. Un critère mal
  posé rendra un verdict juste sur une mauvaise question — et ça, c'est à dire à voix haute.

Ne lance aucune commande d'écriture côté forge (`gh issue edit`, `gh pr create`, `set-workflow`,
`log-time`…), aucun `git commit` / `git push`, et ne ferme aucun milestone : cette commande
observe, exerce et produit un fichier.

Les deux verbes du jalon en font partie, et c'est leur **forme à deux arguments** qui est
proscrite : `milestone-criteres "<titre>"` et `milestone-verdict "<titre>"` **lisent** et sont le
matériau des étapes 2 et 3 ; `milestone-criteres "<titre>" <fichier>` et
`milestone-verdict "<titre>" <fichier>` **écrivent** dans le jalon — le premier fabriquerait
l'examen après l'épreuve (étape 3), le second consignerait un verdict que personne n'a arbitré.
Tu les **nommes** à qui doit les jouer ; tu ne les joues pas.

⚠ **« Personne n'a arbitré » est la raison de cet interdit, et elle a une fin.** L'écriture du
verdict n'est pas défendue en soi : elle est défendue **ici**, parce qu'à cet instant la seule
conclusion qui existe est la tienne. Une fois qu'une personne a tranché, le même verbe devient le
geste juste — et c'est `/milestone-verdict` qui le joue, jamais toi, même si la réponse arrive dans
la seconde qui suit ta question. Ce n'est pas une formalité : la commande qui enregistre propose
aussi les réserves en tickets et vérifie que la convocation a cessé, et refaire ici la moitié qu'on
a sous la main laisserait l'autre moitié à personne.
