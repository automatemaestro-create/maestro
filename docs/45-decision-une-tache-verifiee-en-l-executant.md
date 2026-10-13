<!-- documentation: produit -->
# 45 — Une tâche n'est « Terminée » qu'une fois vérifiée en l'exécutant

**Date :** 2026-09-27. **Ticket :** #1177. **Jalon :** *Le run tient parole — il vérifie, se rattrape, et le fil agit*.

**Renverse :** « au POC, pas de rétro-boucle automatique » — le verdict de la QA éclairait une décision humaine et ne changeait rien au run ([docs/04 §3.6](./04-specifications-agents.md)). Maestro n'est pas un POC ([docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)).

---

## 0. Ce que ce document décide

1. **Une tâche n'est « Terminée » qu'une fois ses critères vérifiés en l'exécutant.** Le planificateur écrit des *critères de réussite* dans chaque tâche ; ils sont désormais **confrontés au livrable**, et non plus seulement écrits.
2. **Un critère qui ne tient pas revient à l'agent de la tâche, preuve à l'appui**, dans la limite du budget du run et jamais d'un nombre de tentatives figé.
3. **Le verdict « non conforme » de la QA entre dans la même boucle** : le livrable jugé non conforme repart à son rôle producteur, puis la QA le rejuge.

## 1. D'où vient la demande

Une tâche était verte **dès que son agent rendait quelque chose** (`maestro/engine/executor.py` : `if sortie or fichiers:` suffisait). Rien ne confrontait les critères au résultat, et le « non conforme » de la QA ne changeait rien. La personne voyait donc des tâches vertes dont les tests échouaient, ou dont le critère n'était pas tenu, et ne pouvait pas se fier au vert. C'est l'inverse d'un produit professionnel. Le constat vient du balayage « rien de figé » du moteur, le 2026-09-21.

## 2. Le mécanisme

**Le modèle propose, l'exécution tranche.** C'est la leçon déjà appliquée à l'outillage d'un projet (#1160, [docs/38](./38-decision-outillage-universel-du-projet.md)). Elle s'applique ici au livrable de chaque tâche (`maestro/engine/verification.py`).

1. **Un vérificateur**, appel modèle distinct de l'agent qui a produit, traduit les critères de la tâche en **contrôles** :
   - un critère qui se constate en exécutant devient une **commande** bash, jouée dans l'espace de travail de l'agent. Son **code de retour** tranche, jamais le texte de sa sortie ([docs/44](./44-decision-maestro-possede-ses-contrats.md)) ;
   - un critère qui ne se constate qu'en lisant devient une **lecture**, que le vérificateur juge sur le livrable, preuve à l'appui.
2. Les contrôles sont établis **une fois**, à la première livraison, puis **rejoués à l'identique** : l'agent est jugé sur ce qu'on lui a dit.
3. Une livraison dont un critère ne tient pas **revient à l'agent**, dans le même espace de travail où ses fichiers l'attendent. Elle porte la commande, son code, la fin de sa sortie, ou ce que la lecture n'a pas trouvé.
4. **Le vérificateur se relit avant de renvoyer qui que ce soit.** Une commande que Maestro ne sait pas lire (une substitution `$(…)`, une redirection d'entrée) est **réécrite**. Une commande qui n'a pas tenu est **contre-expertisée**, à chaque livraison : le défaut est-il dans le livrable, ou dans le contrôle ? Seul un contrôle qui ne constate pas son critère se réécrit, jamais pour qu'il passe, et le compte-rendu de l'agent, qui peut contester, est lu sans faire foi. Mesuré au banc (S6, 2026-09-27) : « aucun fichier ajouté hors du logo » lu comme « le logo seul dans le dossier », sur un projet qui contenait déjà trois fichiers.
5. **On ne conforme jamais le projet à un contrôle.** Dans ce même passage, l'agent de QA a déplacé les fichiers du projet pour faire tenir le contrôle faux. Le retour à l'agent l'interdit en toutes lettres : ne rien supprimer, déplacer ni modifier de ce qui existait avant la tâche pour faire tenir un contrôle, et dire en quoi il se trompe.
6. **Un service se démarre, se sonde, s'arrête** (#1432). « Le serveur répond 200 » ne se constate pas d'une commande qui rend la main. Le contrôle porte alors la commande qui **démarre** le service, sans plus, et une **sonde** qui constate une fois qu'il répond. Maestro démarre, rejoue la sonde jusqu'à ce qu'elle rende 0, puis arrête le service avec sa descendance (`maestro.sandbox.verification.demarrer`). La sonde passe la même portée qu'une commande, et elle est jouée une fois **avant** le service : si elle tient déjà, ce qui lui répond n'est pas lui, et le contrôle n'est pas joué. Sans ce verbe, le juge écrivait lui-même l'attente et l'arrêt : sur le passage du banc `20261004-202743` (S12), un `node -e` qui lançait, interrogeait puis tuait le serveur, qu'un antivirus a pris pour un cheval de Troie (`Trojan:Win32/SuspExec.SE`) et que Windows a refusé de lancer (`[WinError 5] Accès refusé`).
   **Deux vérifications ne se disputent pas un port** (#1449). Un port est au poste, et les critères le nomment en toutes lettres (`PORT=4321 npm start`). Sur le run `3a7fa26ba927`, deux pages vérifiées à quelques secondes d'écart démarraient leur service sur le même port : la sonde de la seconde a trouvé le service de la première, son critère n'a pas été joué, et la tâche a fini en échec pour un port qu'elle ne tenait pas. Désormais, un service démarré par une vérification, sondé ou non, **prend son tour sur le poste**, de sa sonde préalable jusqu'à sa mort constatée (`creneau_de_service`). Le créneau vaut pour tous les process de Maestro, ne pose rien sur le disque, et un détenteur tué le rend. Une sonde qui trouve le port **déjà tenu** (le serveur qu'un agent voisin a lancé pour se vérifier) rend son tour et réessaie jusqu'à ce qu'il se libère, borné par un délai (`liberation_s`). Au-delà, le port est à quelqu'un d'autre, et le contrôle n'est pas joué, comme avant.
   **Le service d'une vérification ne joue pas sur le port des agents** (#1487). Le créneau ne sérialise les vérifications qu'entre elles, et les agents des tâches parallèles se vérifient sur le port que nomment leurs critères. Sur le run `c05bf6263236`, un agent en correction a « libéré son port » en tuant ce qui l'écoutait : c'était le service d'une vérification voisine. Elle a lu « le service a rendu la main (code 1) », et la tâche vérifiée a reçu un échec qu'elle n'avait pas causé, avec une fausse piste pour l'agent suivant. Un code de sortie ne dit pas qui a arrêté un service ([docs/44](./44-decision-maestro-possede-ses-contrats.md)) : ce cas se **prévient**. Le service d'un démarrage reçoit donc **son** port, que Maestro tire à son tour, entre 20000 et 32767, et constate libre adresse par adresse. Le port passe au service et à sa sonde par la variable `MAESTRO_PORT`, que le vérificateur écrit à la place du port d'un critère qui le laisse choisir (`PORT=$MAESTRO_PORT npm start`). Il garde le port écrit pour un service qui ne sait en prendre aucun autre. Ce que les agents trouvent sur le port de leur critère est donc toujours à l'un d'eux, jamais à une vérification, et la preuve dit sur quel port le critère a été joué. Le cadre de l'agent lui dit, de son côté, d'essayer son serveur sur un port à lui et de n'arrêter que ce qu'il a lancé, jamais ce qui écoute un port partagé.
7. Chaque vérification est **consignée** (étape `<tâche>:verification`). Le fil la dit, et le **détail de la tâche** montre la dernière, contrôle par contrôle ([docs/05](./05-interface-control-tower.md)).
8. **Le vérificateur lit le brief approuvé du run** (#1402), le même que l'agent a reçu dans son message, après la description de sa tâche : l'objectif, le hors-périmètre, les contraintes, les critères d'acceptation du run. Sans lui, le vérificateur « déduisait » les critères qu'une tâche n'écrivait pas de sa seule description, et l'agent d'une tâche de vérification « reconstituait » ceux du run, faute de brief dans le projet (p4, run `ad4d2ce8c3bf`). Le brief est le **cadre**, la tâche reste le **contrat** : les contrôles traduisent les critères de la tâche, et un critère du run qu'elle ne prend pas en charge n'en devient pas un, sans quoi elle échouerait pour le travail d'une autre. Une tâche qui demande de vérifier les critères du run les prend en charge : ce sont alors ceux du brief. Un run repris sur son plan ou relancé sur son brief, qui ne refait pas son cadrage, le garde : la Control Tower le confie avec l'ordre du run. Un run sans brief (mode `sans`) n'a que la description de ses tâches, comme avant.

**Où l'on joue :** dans l'environnement que l'agent a construit, donc le seul où « les tests passent » veuille dire quelque chose : le répertoire jetable ou le worktree de la branche `maestro/<tâche>` eux-mêmes, et, pour un projet non versionné, un **miroir** de sa racine (#1496, plus bas). Deux gardes, les mêmes que pour un agent : la **portée « projet »** (une commande qui sort du dossier n'est pas jouée, elle est dite) et un **délai par commande**, qui est un garde-fou et pas un verdict. Un livrable **texte**, qui n'a pas d'espace de travail, ne se vérifie que par des lectures.

**Ce que la vérification joue ne reste pas sur la branche** (#1448). Les commandes constatent sans rien modifier, et le prompt du vérificateur l'exige — ce n'est pas une garantie : sur le run `3a7fa26ba927`, un essai de Vitest a laissé son rapport JSON (`.vitest/json/output.json`) dans la copie de travail, que le démontage a commité sur la branche de la tâche, puis la fusion appliqué au projet. Dans le worktree d'un projet versionné, Maestro relève donc, avant chaque vérification, ce que la branche porterait, et le rend après : ce qui est apparu est retiré, ce qui a été réécrit revient tel que l'agent l'a laissé. La branche ne porte que le travail de l'agent, et l'agent dont la livraison ne tient pas repart de ses propres fichiers. Ce que le projet ignore — les dépendances qu'un contrôle installe — reste en place et ne part sur aucune branche. La racine d'un projet non versionné n'est pas défaite après coup : c'est le dossier vivant de la personne, où l'on effacerait ce qu'elle aurait écrit entre-temps.

**Et rien n'est écrit dans la racine d'un projet non versionné** (#1496). La vérification y jouait ses commandes, et ce qu'elles écrivaient y restait. Reproduit le 2026-10-13 avec `python -m pytest --junitxml=…` et `ruff check --fix .` : `.pytest_cache/`, `.ruff_cache/` et le rapport JUnit dans la racine, un fichier de la personne réécrit par le formateur, 13 fichiers comptés à l'agent au lieu de 2, et un agent qui, à sa correction, ne retrouvait pas son propre fichier. **Maestro n'efface ni ne réécrit jamais, dans la racine de la personne, ce qu'il n'y a pas lui-même écrit**, et cela vaut pour le remède : défaire après coup ne distingue pas ce que la vérification a écrit de ce que la personne, son éditeur ou un client de synchronisation ont écrit pendant ce temps. La vérification joue donc dans un **miroir** de la racine, sous le répertoire jetable du poste (`maestro.sandbox.verification.MiroirDeVerification`), et la contrainte tient par construction. Pour ce seul régime, cela renverse « là où l'agent l'a fait ».

- Le miroir porte **toute** la racine, pour que le verdict garde son sens : les dépendances en vraie copie (une écriture à travers un lien vers la racine y atterrirait), le `.env` que lit un service démarré, l'atelier des tâches. Un lien qui mène dans la racine mène au même endroit du miroir.
- Il naît à la première vérification **du run**, puis se **resynchronise** avant chaque suivante, de la même tâche ou d'une autre, dans un seul sens : ce que la racine a de neuf y entre, ce que la vérification précédente y a laissé ou réécrit en sort. Il vaut pour toutes les tâches du projet, qui passent déjà une à une dans sa racine, et part **à la fin du run** (#1497), que le run finisse vert, en échec ou annulé.
- Ce qui tient le miroir dépend de qui exécute. **En process** (la Control Tower, `maestro-run`), l'exécuteur le garde le temps du run, que la boucle borne autour de ses tâches (`TaskExecutor.tient_le_run`). **Un worker de file** (Celery) ne voit pas la fin des runs : chaque tâche y a son miroir, retiré avec son espace, plutôt qu'un miroir par run qui s'accumulerait le temps que vit le worker. **Un run durable** (Temporal) ne porte pas de projet ; une tâche qui en porterait un y aurait aussi le sien. Si le process meurt avant le retrait, le miroir, marqué de son pid sous le répertoire jetable, part au ramassage.
- La racine ne bougeant pas, les fichiers produits de la tâche, son récit et ce que lit le juge ne relèvent que le travail de l'agent, et l'agent qui corrige retrouve ses fichiers tels qu'il les a laissés.
- Les **preuves** disent les chemins de la racine, toutes graphies comprises (Windows, barres obliques, Git Bash) : l'agent cherche le fichier qu'elles nomment.
- Dans le miroir, la portée ne protège plus « ce que la personne avait posé » : ce qu'on y détruit ne se perd pas, comme dans un worktree.
- Au-delà de ses bornes (100 000 fichiers ou 2 Go, dépendances comprises ; S12 en compte 24 776 pour 505 Mo), ou faute de place sur le poste, le contrôle est **non joué** avec son motif, et la livraison gardée. Il n'est **jamais** rejoué dans la racine.
- **Limite dite** : un chemin absolu qui ramène à la racine. Une commande qui l'écrit sort du dossier et n'est pas jouée ; mais un lanceur de `.venv` ou une installation éditable font encore lire la racine, et Python peut y écrire ses `__pycache__`.

## 3. Ce qui arrête la boucle

Pas un nombre de tentatives : un **fait**.

| Fait | Issue |
| --- | --- |
| Tous les critères tiennent | **Terminée**, la seule sortie verte |
| La correction ne fait tenir **aucun critère de plus** que la meilleure livraison précédente | **Échec motivé** : l'agent avait la preuve et n'en a rien tiré |
| Le **budget du run** est atteint pendant une correction | **Échec motivé** : la cause d'arrêt, suivie des dernières preuves |
| Rien n'est constaté faux, mais un contrôle n'a **pas pu être joué** (portée, pas de bash, commande que le système refuse de lancer) ou le vérificateur est **illisible** | **Échec qui garde sa livraison** (#1432) : « livrée, non vérifiée », chaque preuve à l'appui — puis **demandé** dans le fil, jamais refait, comme une panne du vérificateur |
| Le **vérificateur est en panne** (son appel au fournisseur échoue) et le reste après ses relances | **Échec qui garde sa livraison** (#1388) : « livrée, vérification en panne » — puis **demandé** dans le fil, jamais refait (#1396) |

**Un contrôle que le juge n'a pas pu jouer ne jette pas la livraison** (#1432). Le juge le dit pour ce qu'il est (« le juge n'a pas pu la lancer — le système a répondu : … »), et quand rien d'autre n'a été constaté faux, la tâche finit comme une livraison que le vérificateur n'a pas pu juger : elle garde son compte-rendu et ses fichiers (`verification_en_panne`), et se demande. Avant, elle était vidée puis jugée par le Chef de projet comme un échec ordinaire, qui pouvait la refaire de zéro alors que trois critères sur quatre venaient de tenir.

**Une panne du vérificateur n'est pas un échec de l'agent** (#1388). Elle est typée par son
**origine** (l'appel du vérificateur a échoué), jamais reconnue à son texte. Elle est relancée
**seule**, selon la politique des aléas et dans l'espace encore ouvert : la session de l'agent ne
se rejoue pas. Restée en panne, elle laisse la tâche en échec, puisque rien n'est vérifié, mais la
tâche garde son compte-rendu et ses fichiers. C'est un **champ** du résultat
(`verification_en_panne`), pas un statut nouveau (§5). Chaque panne est consignée comme une
vérification impossible. Le prompt du vérificateur est **borné en entier**, noms de fichiers
compris. Ce qui a rendu ces trois règles nécessaires vient du run `da0a8ae6f1b2` (p5). La session
avait réussi, mais le vérificateur listait 22 891 fichiers de `node_modules/` et dépassait le
million de tokens. La tâche a été relancée session comprise, vidée, puis redécoupée de zéro.

Le nombre de critères est fini et chaque tour doit en gagner un, donc la boucle **finit toujours**, budget posé ou non. Un échec motivé dit combien de critères tiennent, après combien de livraisons, pourquoi la boucle s'est arrêtée, et chaque preuve. **Ce n'est jamais un vert.**

**Puis le rattrapage** (#1178, [docs/04 §3.1](./04-specifications-agents.md), `maestro/engine/rattrapage.py`) : un échec motivé est un échec comme un autre pour le Chef de projet. Il le juge, motif et preuves en main, et peut le retenter autrement (autre approche, autre agent, tâche redécoupée), ou le demander dans le fil. La tentative différente repasse par la même vérification. Le **budget dépensé** ne se rattrape pas : c'est une borne, pas un échec.

**Une livraison que le vérificateur n'a pas pu juger n'est pas un échec comme un autre** (#1396, qui renverse ici la phrase du paragraphe précédent pour ce seul cas). Ce n'est pas le travail qui a échoué, et le redécouper le refaisait de zéro sur des branches neuves : c'est ce qu'a fait le run `da0a8ae6f1b2`, parce que le Chef de projet ne lisait que le **texte** de l'erreur, et que le « Prompt is too long » du juge y disait « tâche trop grosse ». Le fait typé de #1388 voyage désormais dans la **tentative** (`Tentative.verification_en_panne`). Il n'appelle aucun diagnostic : le vérificateur a déjà été **revérifié** seul, l'espace encore ouvert, selon la politique des aléas. Reste à **demander**, d'emblée, dans le fil ([docs/05 §6.17](./05-interface-control-tower.md)) : la personne peut prendre la livraison telle quelle d'un geste (la tâche passe « Terminée » sur décision humaine, sa branche est fusionnée, l'aval part, et le résultat dit toujours qu'elle n'a pas été vérifiée), ou répondre en mots, que le Chef de projet lit en sachant que le travail est livré. Sans réponse, la tâche reste en échec **avec** sa livraison, et l'aval ne part pas sur un travail que personne n'a jugé.

**Et un redécoupage, quel qu'en soit le motif, ne refait pas ce qui est fait** (#1396). Sur un projet versionné, les tâches **de tête** d'un redécoupage (celles qui n'en attendent aucune autre) partent de la **branche de la tâche qu'elles remplacent**, et non de la base (`Task.reprend`, posé par le moteur seul) ; les autres partent de la base, où la fusion de celles qu'elles attendent a déjà porté ce travail.

## 4. La QA dans la même boucle

Le vérificateur d'une tâche qui dépend d'autres tâches dit aussi si son livrable **rend un verdict « non conforme »** sur l'une d'elles. C'est le modèle qui le lit, jamais un lexique (#746). La boucle du run (`maestro/engine/loop.py`) enchaîne alors trois gestes :

1. le renvoi est **consigné** sur la tâche productrice, avec les défauts de la QA en constat ;
2. la tâche productrice est **réexécutée par son rôle**, sa description suivie des défauts bloquants et de leurs preuves ;
3. la QA **rejuge**, sur le livrable refait.

La QA **évalue et ne réécrit toujours pas** le livrable d'un autre rôle : elle le **renvoie**. La boucle s'arrête quand la QA juge conforme, quand le budget refuse une exécution, quand un producteur échoue sa propre reprise, ou quand une reprise ne lève **aucun défaut bloquant de plus**. Dans ce dernier cas, la tâche productrice finit en échec motivé, la revue en motif.

**Limites dites :**
- une tâche qui dépendait du livrable renvoyé **sans passer par la QA** a pu partir sur sa première version. La boucle ne rejoue que la paire producteur → QA, et le cas courant, une QA en bout de chaîne, n'en a pas ;
- le **mode durable** (Temporal) vérifie chaque livraison, mais son workflow ordonnance ses activités lui-même : le renvoi par la QA ne vit que dans la boucle en process, file Celery comprise (`TaskResult.renvois` traverse la file).

## 5. Ce qui ne bouge pas

- **L'arbitrage des actes** : un contrôle hors de la portée n'est pas joué, et il n'est pas soumis à une personne non plus.
- **La frontière d'écriture** : les contrôles constatent et ne modifient rien, et c'est le prompt du vérificateur qui l'exige.
- **Les états d'une tâche** ([docs/03 §3](./03-modele-de-donnees.md)) : aucun statut nouveau. La vérification et sa preuve s'y **ajoutent**.
- **« La QA évalue, elle ne réécrit pas »** : elle renvoie.

## 6. Ce que cela coûte

Chaque tâche paie **un appel modèle de plus**, celui qui établit ses contrôles, puis l'exécution de ses commandes. Une livraison corrigée repaie une session d'agent, et les lectures seulement sont rejugées par le modèle. Sur un projet non versionné s'y ajoute le **miroir** de la racine (#1496), un par run (#1497) : mesuré le 2026-10-13 sur la racine de S12 (24 776 fichiers, 505 Mo), la première vérification du run le copie en 12,3 s, chaque suivante le resynchronise en 1,2 s, et la fin du run le retire en 7,7 s, hors de la boucle. Deux tâches successives paient ainsi 21,1 s, contre 40,3 s quand chacune avait son miroir, et chaque tâche de plus environ une seconde au lieu de vingt. Tout cela, le retrait excepté, tombe **dans la mesure de la tâche** qui le paie, et le plafond de dépense du run le borne comme le reste. Le moteur des vrais runs arme la vérification par défaut (`OrchestrationEngine.default`), comme la relance des aléas. `verification=False` l'éteint, et c'est un choix qu'on fait en le disant.
