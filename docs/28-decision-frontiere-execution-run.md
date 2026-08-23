# 28 — Frontière d'exécution d'un run : note de décision

> Ticket #350 (lot 3/4 du parent #347). Décision datée du **2026-08-23**, sur `origin/main` à
> `c284e6b`.
>
> **Verdict : l'hôte de run détaché.** L'exécution d'un run sort du process de `maestro-api` pour
> un process qu'elle possède, sur la même machine. Elle y gagne de survivre à tout ce qui arrête
> l'API — et garde, sans les repayer, l'annulation immédiate, le brief `humain` et la validation
> humaine. **Temporal n'est pas écarté** : il est mis derrière une porte nommée (§8), et rien de ce
> qui est construit ici n'est jeté le jour où on la franchit.

---

## 1. La question — et ce qu'elle n'est pas

Un run lancé depuis la Control Tower s'exécute en **tâche de fond du process de l'API**. Il ne
survit pas à son hôte. Le parent #347 a rendu cette perte **visible** (#348, battement de cœur) et
**rattrapable** (#349, relance sur le brief déjà validé) ; il ne l'a pas empêchée. Ce lot décide
si on l'empêche, et à quel prix.

**Ce n'est pas la question de la persistance de l'état.** Elle est réglée : le journal durable
(#97) conserve la trace, et c'est précisément parce qu'il la conserve qu'un run mort restait
affiché `en_cours` pour toujours. Ce qui ne survit pas, c'est l'**exécution**.

**Et ce n'est pas la frontière que le dépôt appelle déjà par ce nom.** `TaskExecutor` se présente
comme la « frontière d'exécution d'une tâche assignable » (`maestro/engine/executor.py:167`) : elle
décide *où s'exécute une tâche* — en process (`LocalExecutor`) ou dans un worker Celery
(`CeleryExecutor`). La question d'ici est d'un cran au-dessus : **qui possède le process qui tient
la boucle d'orchestration**, celui qui porte le plan, les dépendances, le brief et l'attente d'un
humain. Les deux seams sont orthogonaux, et les confondre est l'erreur qui rend l'option « la
file » plausible alors qu'elle ne répond pas à la question (§4.2).

Appelons la seconde, faute de nom dans le dépôt, la **frontière d'hôte de run**.

## 2. Les deux pannes ne sont pas la même — et tout en découle

Le parent chiffre deux runs perdus le 2026-08-14. Les relire séparément est ce qui décide la note :
un mécanisme qui traite l'un ne traite pas forcément l'autre.

| Run | Cause | La machine ? | Ce qu'il faut pour survivre |
| --- | --- | --- | --- |
| `3ff0bcb065f9` | **Fenêtre du navigateur fermée** — le chien de garde (#149) arrête l'API avec elle | **vivante** | que le run ne soit pas *dans* l'API |
| `4b33ea332e60` | **Machine endormie** — Docker, Redis et l'API emportés | **morte** | que le run ne soit pas *sur cette machine*, ou qu'il reprenne au réveil |

La première panne est celle d'un **process**. La seconde est celle d'un **hôte**. Aucun dispositif
tournant sur la machine qui s'endort ne survit à la seconde : ni un process détaché, ni un worker
Temporal, ni Redis. Ce que Temporal offre pour ce cas n'est pas la survie, c'est la **reprise
automatique au réveil** — ce qui est réel, et se compare à ce que #349 offre déjà : une reprise en
un geste, sur un brief intégralement conservé.

Et les deux pannes n'ont pas la même fréquence. Fermer la fenêtre, relancer l'API après une
modification, jouer `start.sh --stop` : ce sont des gestes de **chaque heure de développement**.
Une machine s'endort une fois par nuit, et rarement au milieu d'un run. **La panne dominante est
celle du process, et c'est la moins chère à traiter.**

## 3. Où le run est attaché aujourd'hui

Cinq faits, tous vérifiés dans le code, qui bornent les options.

**Le run est une tâche asyncio de l'API.** `ServiceExecutions.lancer` construit le moteur puis
`asyncio.get_running_loop().create_task(...)` (`executions.py:509`) ; la tâche est rangée dans
`self._runs`, seul registre en mémoire du service. L'annulation la reprend et l'interrompt
(`tache.cancel()` puis attente bornée à `DELAI_ANNULATION_S = 5 s`). L'en-tête du module assume le
choix et nomme sa contrepartie : la tâche de fond « garde l'annulation *à portée*
(`asyncio.Task.cancel`, là où interrompre un run déjà distribué demanderait un protocole de
révocation côté workers) et n'ajoute aucune dépendance d'infrastructure à `maestro-api` ».

**Le seam est déjà nommé.** Le même en-tête ajoute : « basculer sur la file plus tard ne change que
la fabrique du moteur (`fabrique_moteur`), pas les routes. » `fabrique_moteur` est injectable
depuis `create_app` (`app.py:683`) et déjà substitué par trois suites de tests. Le point d'entrée
d'une autre frontière existe donc, et il est éprouvé.

**Il y a trois attentes humaines, et toutes tiennent un process.** Le patron est le même aux trois
endroits : s'abonner au bus, publier la demande, `await` sur le premier événement de réponse — et,
si le flux se tarit, **lever** plutôt que d'approuver par défaut.

| Attente | Arbitre | Statut | Réponse |
| --- | --- | --- | --- |
| Décision sur le brief (#320) | `ArbitreBriefControlTower` | `en_attente_brief` | `POST …/brief/decision` |
| Réponses de clarification (#321) | `ArbitreClarificationControlTower` | `en_attente_reponses` | `POST …/brief/reponses` |
| Validation d'action sensible (#9/#48) | `ValidateurControlTower` | — | `POST /api/validations/{id}/decision` |

Aucune n'a de time-out : l'attente est « indéfinie […], jamais un time-out silencieux »
(`validation.py:88-91`). Le statut de la projection décrit cet état ; il ne le remplace pas. **Une
tâche asyncio vivante est bloquée là**, potentiellement des dizaines de minutes — et c'est la
fenêtre exacte où un run coûte le plus cher à perdre, puisqu'il porte un cadrage déjà payé.

Mais — et c'est ce qui rend l'option retenue bon marché — **cette attente est déjà inter-process par
construction**. Le bus a deux implémentations, `InMemoryEventBus` en mono-process et
`RedisEventBus` en production, et les fabriques `arbitre_brief_redis` / `validateur_redis` existent
précisément pour que la demande et la décision traversent des process distincts. Le mécanisme
d'attente n'est pas à réinventer pour détacher l'hôte : **il est à câbler**.

#348 a d'ailleurs tiré la conséquence de cette forme : les deux attentes de brief ne sont pas des
statuts terminaux et reçoivent un verdict de vitalité comme les autres, parce qu'« un run suspendu
sur un humain est porté par un hôte, qui bat — c'est même le seul moyen de distinguer "personne n'a
encore répondu" de "le process qui posait la question est mort", les deux cas de #347 »
(`battement.py:113-119`). **Une attente humaine n'est sûre que si quelqu'un la porte et le fait
savoir.** Retenir ce fait dispense de le redécouvrir en §4.4.

**La durabilité existe, et elle est verrouillée en tenaille.** `maestro.durable` est complet pour la
boucle de tâches : un run = un workflow, une tâche = une activité, reprise sur panne par rejeu de
l'historique sans repayer l'amont (`workflow.py:1-46`). Mais quatre garde-fous de
`maestro/engine/cli.py` (l. 312-350) l'interdisent au chemin de la Control Tower, et deux d'entre
eux se referment l'un sur l'autre :

- `--durable` refuse `--messagerie` / `--validation-ui` / `--notifier` (« #95, lot 2/5 ») ;
- `--durable` refuse tout `--brief` autre que `sans`, avec le motif écrit noir sur blanc : « l'étape
  de cadrage (#318) n'est pas encore une étape du workflow Temporal — **elle serait rejouée à chaque
  reprise, et payée à chaque fois** » ;
- et `--brief humain` **exige** `--validation-ui`, lui-même refusé par `--durable`.

Le défaut diffère d'ailleurs selon la porte d'entrée : `sans` en CLI (`cli.py:176`), **`humain` en
Control Tower** (`app.py:352`, décision D5 / #320). La voie de lancement qui a par construction
quelqu'un devant est exactement celle que le mode durable ne sait pas servir. Le fait est même figé
par le typage : la branche durable appelle `DurableEngine.run(objective, journal=…)` **sans**
`mode_brief`, « pour que ce fait soit vérifié par le typage plutôt que tenu par la seule validation
d'arguments » (`cli.py:422-429`).

Dernier fait, hors durabilité : **`maestro-run` ignore la notion de projet** — la chaîne `projet`
n'apparaît pas une fois dans `maestro/engine/cli.py`. Le `projet_id` (#222) ne descend que par le
chemin Control Tower (`executions.py:468`, puis `moteur.run(projet_id=…)`), d'où il est hérité par
chaque tâche puis résolu en racine de travail par `espace_de_travail` (`sandbox/projet.py:116-141`
— worktree git sur une racine versionnée, copie de périmètre sinon).

La conséquence n'est pas abstraite : sans `projet_id`, `espace_de_travail(None)` retombe sur un
`mkdtemp()`. Le run travaille alors dans **un répertoire temporaire vide**, et son livrable n'atteint
jamais le projet. `DurableEngine.run(objective, *, journal=None)` n'accepte d'ailleurs ni
`projet_id`, ni `ticket`, ni `mode_brief` — l'amputation est délibérée et vérifiée par le typage.

Tout hôte d'exécution qui n'est pas l'API perd donc la racine où le travail est appliqué, **quelle
que soit l'option retenue**. Ce n'est pas un départageur : c'est un **prérequis commun** (§9, lot 2),
et le fait qu'il serve aussi O4 est une raison de le payer tôt.

## 4. Les options, instruites

### 4.1 O1 — Statu quo : la tâche de fond de l'API

La référence. Zéro infra, annulation immédiate et locale, brief et validation qui marchent, projet
rattaché. Elle ne survit ni à l'API ni à la machine. #348 et #349 en ont retiré le pire — la mort
silencieuse et la perte du cadrage — et c'est ce qui permet d'instruire la suite sans urgence.

Ce n'est pas une option « ne rien faire par paresse » : c'est la ligne de base contre laquelle les
autres doivent payer leur prix. Elle est **écartée** parce que le geste le plus banal de la journée
— fermer une fenêtre — tue un run en vol, et qu'aucune des trois autres options ne coûte cher pour
supprimer ça.

### 4.2 O2 — Passer par la file (`maestro.queue`) : l'option n'existe pas

Le ticket la nomme comme alternative à Temporal. **Elle ne répond pas à la question, et c'est un
fait de code, pas un jugement.**

`CeleryExecutor` est un `TaskExecutor` (`queue/dispatch.py:55`) : il « pousse chaque tâche dans la
file Celery + Redis […] puis **attend le résultat** renvoyé par un worker », attente déportée dans
un thread « pour que la boucle asyncio continue de dispatcher les autres tâches ». Autrement dit :
avec `--queue`, **la boucle d'orchestration reste dans le process appelant**. Ce qui part dans la
file, ce sont les tâches ; le run, lui, meurt exactement comme aujourd'hui avec l'API qui le porte.

Deux faits achèvent de le montrer. La file **n'expose aucune annulation** — il n'y a pas de `cancel`
côté `CeleryExecutor`, ce qui est précisément le « protocole de révocation côté workers » que
l'en-tête de `executions.py` refusait de payer. Et `--queue` refuse déjà `--validation-ui` : la
validation humaine n'y survit pas plus qu'en durable. Enfin, `maestro.queue` **n'a aucun
consommateur** en dehors de `maestro-run --queue` et de ses tests ; la Control Tower ne l'importe
jamais.

Pour que la file devienne une frontière d'*hôte de run*, il faudrait mettre **le run entier** en
file comme un seul message, consommé par un worker qui tiendrait la boucle. Cela n'existe pas, et
ce serait un mauvais Temporal : une file donne la **livraison**, pas l'exécution durable. Un worker
qui meurt en cours de run rend son message à la file, qui le **redistribue depuis le début** — donc
qui **repaie la clarification**, exactement ce que #349 a été écrit pour éviter. Y ajouter des
points de reprise reviendrait à réimplémenter l'historique de workflow que Temporal fournit déjà,
et que le dépôt a déjà intégré.

**Écartée**, et pas pour son coût : parce que sous le nom d'une option existante elle cache soit un
non-changement (la file d'aujourd'hui), soit une réécriture de Temporal en moins bon.

### 4.3 O3 — L'hôte de run détaché

Le run s'exécute dans un process que l'API **lance et surveille**, mais dont elle ne possède pas le
cycle de vie : arrêter l'API n'arrête pas le run. Le process publie sur le même Redis, journalise
au même `RunJournal`, et bat le même cœur (#348).

Ce n'est pas une figure de style dans ce dépôt : c'est **exactement le patron de
`scripts/orchestrate/run.sh --detach`** (#173), retenu là pour la même raison — « le pilote est un
script shell, jamais une session Claude Code », parce qu'un pilote qui vit dans ce qu'il pilote
meurt avec. Le précédent est en production depuis des mois.

- **Infra** : aucune dépendance nouvelle. Même interpréteur, même `.venv`, même machine, même Redis
  — qui est déjà requis par la Control Tower pour le bus.
- **Annulation** : elle traverse la frontière par le bus, sur le patron déjà utilisé deux fois
  (`brief.decision`, décision de validation). L'hôte s'abonne, observe l'événement, annule sa propre
  tâche `asyncio` — donc `Task.cancel` reste le mécanisme réel, à un aller Redis près. Ce n'est pas
  le « protocole de révocation côté workers » que l'en-tête de `executions.py` redoutait pour la
  file : il n'y a qu'un destinataire, et il est nommé.
- **Brief `humain` et validation** : **inchangés**. `arbitre_brief_redis` et `validateur_redis`
  existent déjà pour ce cas. Le lot est du câblage et un fail-safe, pas une refonte du modèle
  d'interaction. Et l'attente reste **sûre au sens de #348** : l'hôte détaché bat pendant qu'il
  attend, donc « personne n'a encore répondu » continue de se distinguer de « le process qui posait
  la question est mort » — là où O4 doit d'abord se défendre de relancer la tâche sous le nez de
  l'opérateur.
- **Projet** : passé au lancement du process, comme aujourd'hui à la fabrique du moteur.
- **Survie** : au process de l'API **oui** ; à la machine **non** — reprise par #349.

### 4.4 O4 — Le workflow durable (Temporal) pour le chemin Control Tower

Rendre le brief `humain` et la validation compatibles d'un workflow : une attente devient un
**signal** du workflow, pas un blocage de process. C'est le patron canonique de Temporal pour
l'humain dans la boucle, et le moteur durable existe déjà pour la boucle de tâches.

- **Infra** : Temporal devient une dépendance **dure du chemin par défaut**. Il est déclaré dans
  `infra/docker-compose.yml` mais « pas monté par défaut » (docs/07 §2.1) et pèse plusieurs Go
  d'images. Il faudrait qu'il tourne pour que la Control Tower lance un run — sur la machine dont
  l'endormissement est la panne n° 2. L'état survit (SQLite dans un volume), la reprise est
  automatique au réveil ; mais on adosse la durabilité à Docker, c'est-à-dire à ce qui est tombé.
- **Annulation** : **régression réelle**. La cancellation d'un workflow est coopérative : une
  activité longue — un agent qui travaille 42 minutes, comme le run `4b33ea332e60` — ne l'observe
  que si elle bat et interroge son contexte. Là où `Task.cancel` interrompt tout de suite, il
  faudrait instrumenter les activités. C'est la propriété que le POC a explicitement protégée en
  choisissant la tâche de fond.
- **Brief et validation** : le modèle d'interaction, lui, est proche — publier une demande puis
  attendre une réponse est déjà ce que fait le bus. Mais le brief doit devenir une **étape du
  workflow**, sans quoi il est rejoué et repayé à chaque reprise ; le garde-fou de `cli.py` le dit
  déjà. Et la rédaction du brief est un appel modèle : elle doit vivre dans une **activité**, pas
  dans le code du workflow, contrainte de déterminisme. Ce n'est pas hors de portée — c'est le
  chantier laissé ouvert par #95, lot 2/5, plus l'étape de cadrage #318 qui lui est postérieure.

  ⚠ **Et il y a un piège que le dépôt a déjà rencontré et écrit.** Une attente humaine ne peut pas
  vivre *dans* une activité : `guardrails.py:208-214` note qu'un validateur qui bloque la boucle est
  « anodin en local, mais **fatal en mode durable (#96), où la boucle doit continuer à battre le
  cœur des activités : un worker qui ne bat plus est réputé mort et sa tâche relancée sous 30 s, en
  pleine question à l'opérateur** ». Une délibération humaine dure des minutes, un battement
  d'activité se compte en dizaines de secondes : l'attente doit donc remonter dans le **code du
  workflow**, en `workflow.wait_condition` sur un `@workflow.signal`. C'est faisable et c'est le
  patron canonique — mais cela veut dire que les trois arbitres du §3 ne sont pas *portés* vers le
  durable, ils y sont **réécrits**, sur un mécanisme d'attente qui n'a rien de commun avec
  l'abonnement au bus. C'est le vrai volume du chantier O4, et il est invisible tant qu'on ne
  regarde que les garde-fous de `cli.py`.
- **Projet** : argument de workflow ; même prérequis que partout.
- **Survie** : au process **oui**, à la machine **oui, avec reprise automatique au réveil**. C'est
  la seule option qui l'offre, et c'est son vrai argument.

### 4.5 La comparaison, sur les quatre axes du ticket

| | **O1** statu quo | **O2** la file | **O3** hôte détaché | **O4** Temporal |
| --- | --- | --- | --- | --- |
| **Dépendance d'infra** | aucune | Redis (déjà là) | **aucune** | **Temporal requis** sur le chemin par défaut |
| **Annulation** | immédiate, `Task.cancel` | **aucune** — rien d'exposé côté Celery | **`Task.cancel`, via un événement du bus déjà en place** | **coopérative** — activités à instrumenter |
| **Brief `humain` + validation** | marchent | `--validation-ui` **déjà refusé** avec `--queue` | **inchangés** (`arbitre_brief_redis` / `validateur_redis` existent) | **réécrits** en `wait_condition` + `signal` — une attente dans une activité fait relancer la tâche sous 30 s |
| **Rattachement au projet** | acquis (#222) | perdu (chemin CLI) | à passer au lancement | absent de la signature `DurableEngine.run` |
| **Survit à l'arrêt de l'API** | non | **non** | **oui** | oui |
| **Survit au sommeil machine** | non | non | non → reprise #349 | **oui, reprise automatique** |
| **Coût** | — | nul ou énorme (§4.2) | **faible, incrémental** | **élevé** (#95 lot 2/5 + #318 en activité + instrumentation) |

## 5. Décision

**L'hôte de run détaché (O3) est retenu.**

Trois raisons, dans l'ordre où elles pèsent.

**Elle supprime la panne dominante pour un coût presque nul.** Fermer la fenêtre, relancer l'API,
`--stop` : la panne du process est celle de chaque heure, et O3 la fait disparaître sans une
dépendance nouvelle, sans toucher au modèle d'interaction humaine, et en gardant l'annulation
immédiate. Aucune autre option n'a ce rapport.

**Elle ne paie pas la propriété que le POC a protégée exprès.** L'annulation « à portée » est écrite
dans l'en-tête de `executions.py` comme la raison du choix initial. O4 la dégrade en attente
coopérative sur des activités de quarante minutes. Payer une régression sur ce qu'on utilise tous
les jours pour gagner une reprise automatique sur la panne la plus rare est un mauvais change **à
cette échelle** — une machine, un utilisateur devant.

**Et surtout : ce n'est pas O3 *au lieu de* O4, c'est O3 *avant* O4.** Quel que soit l'hôte final —
process détaché ou worker Temporal —, la Control Tower doit cesser de supposer que le run vit dans
son process : lancer **à travers** une frontière, annuler **à travers** une frontière, recevoir le
brief et la validation **à travers** une frontière, et transporter le `projet_id`. Ce sont
exactement les lots 1 à 4 du §9, et **aucun n'est jeté** le jour où l'hôte devient un worker
Temporal : seule l'implémentation derrière le contrat change. La voie chère reste ouverte, et on y
arrive par un chemin qui livre de la valeur à chaque étape.

Corollaire assumé, à écrire là où le corollaire d'aujourd'hui est écrit (docs/05, `executions.py`,
`battement.py`) : **un run survivra à son API, pas à sa machine.** Le sommeil de la machine reste
traité par #348 (on le voit) et #349 (on le rattrape sur le brief), pas par la frontière.

## 6. Pourquoi les autres sont écartées — récapitulatif

- **O1, statu quo** : le geste le plus banal de la journée tue un run en vol, et le supprimer ne
  coûte pas cher. Ne rien faire ne se défend que si la correction est chère ; elle ne l'est pas.
- **O2, la file** : **elle n'existe pas** comme frontière d'hôte de run. `CeleryExecutor` déplace
  des *tâches* et laisse la boucle dans l'appelant : le run meurt exactement comme aujourd'hui. La
  bâtir vraiment donnerait une redistribution **depuis le début**, donc le re-paiement du cadrage
  que #349 vient d'éliminer. Écartée sur un fait, pas sur une préférence.
- **O4, Temporal** : écartée **maintenant**, pas sur le fond. Elle achète la reprise automatique au
  réveil — la panne la plus rare — au prix d'une dépendance d'infra dure sur le chemin par défaut,
  d'une régression de l'annulation, de la reprise d'un chantier laissé ouvert (#95 lot 2/5), du
  portage de l'étape de cadrage #318 en activité, et de la **réécriture des trois attentes humaines**
  en signaux de workflow — ce dernier poste étant le plus gros et le moins visible. Le rapport ne se
  justifie pas à une machine et un utilisateur. Il se justifierait aux conditions du §8.

## 7. Ce que la veille AionUi ajoute — et ce qu'elle ne nous coûte pas

AionUi a tranché la même question en avril 2026 dans le sens d'O3 : le cœur agent est un binaire
séparé que l'application résout, lance et surveille, joint en HTTP + WebSocket sur `127.0.0.1`
(#352). Deux enseignements, et une soustraction.

**À reprendre : le contrat ne connaît pas le transport.** Leur crate de types d'API a interdiction
de dépendre du framework HTTP. C'est ce qui leur permet d'avoir le même client en fenêtre, en
navigateur et en ligne de commande. Transposé ici : le contrat d'hôte de run (§9, lot 1) doit être
une **abstraction de lancement/annulation/observation**, pas « une fonction qui lance un
sous-process ». C'est aussi la condition pour qu'O4 s'y branche plus tard sans réécrire l'appelant.

**À noter : le bénéfice principal n'a pas été la survie, mais la multiplicité des clients.** La
frontière a payé au-delà de la robustesse. Chez nous, elle rendrait au passage joignable un run
depuis autre chose que l'API qui l'a lancé — ce n'est pas l'objectif du ticket, c'est un gain à ne
pas oublier au moment de dessiner le contrat.

**Ce que nous ne payons pas.** Leur prix — résolution du binaire par plateforme × architecture,
diagnostics de démarrage, détection d'incompatibilité de runtime, réparation de base corrompue,
trois fichiers de `process/startup/` — est très majoritairement le prix de **distribuer un binaire
compilé à des utilisateurs finaux**, pas celui de **détacher un process**. Notre hôte détaché est le
même interpréteur, dans le même `.venv`, sur la même machine, lancé par un process qui connaît déjà
son chemin. Ce qu'il faut en garder quand même, et qui est réel : **on peut rater un démarrage**.
Un hôte qui ne démarre pas doit le dire tout de suite et solder le run, jamais le laisser
`en_attente` — c'est un critère du lot 2.

## 8. Ce qui rouvrirait la question

O4 est derrière une porte. Les conditions qui la franchissent, nommées d'avance pour qu'on n'ait pas
à re-débattre :

1. **Un second run perdu par sommeil machine** *après* la livraison de l'hôte détaché. La reprise
   #349 aura alors montré qu'elle ne suffit pas, sur pièces.
2. **L'exécution quitte la machine** — un hôte distant, un serveur partagé, plusieurs postes. Le
   sommeil du poste cesse alors d'être une fatalité et la reprise automatique devient le sujet.
3. **Des runs plus longs qu'une journée de travail.** Aujourd'hui un run se compte en dizaines de
   minutes ; s'il se compte en jours, il traversera forcément un arrêt, et #349 (qui repart du
   brief) redeviendrait un re-paiement lourd.
4. **Le besoin d'une reprise à l'endroit exact de l'interruption** — pas au brief. C'est la seule
   chose que #349 ne sait pas faire, et que Temporal fait nativement ; le commit de #349 le dit
   déjà : « Ce n'est pas une reprise à l'endroit exact de l'interruption, qui suppose une frontière
   d'exécution durable (cadrage #350). »

Aucune de ces quatre conditions n'est remplie au 2026-08-23.

## 9. Le chantier — découpage en lots

Le chantier est ouvert : **parent de suivi #441**, six lots d'~1 session, chacun mergeable seul sur
`main` sans casser l'existant. Le défaut reste **l'exécution en process** jusqu'au lot 5 : chaque lot
intermédiaire livre du code inerte tant que la bascule n'a pas eu lieu, ce qui est la condition pour
qu'ils soient mergeables séparément. Tests différés au lot 6, sauf mention contraire.

| # | Lot | Ce qu'il livre | Dépend de |
| --- | --- | --- | --- |
| 1 | **#442** — Le contrat d'hôte de run | L'abstraction (lancer / annuler / observer) à côté de `fabrique_moteur`, avec l'hôte **en process** comme unique implémentation. Aucun changement de comportement. | — |
| 2 | **#443** — L'hôte détaché : lancer | Un run part dans un process indépendant (objectif, garde-fous, `projet_id`, `mode_brief`), publie sur le même Redis, bat son cœur (#348). Opt-in. Un démarrage raté solde le run au lieu de le laisser en attente. | 1 |
| 3 | **#444** — L'annulation traverse la frontière *(parallèle)* | Événement d'annulation sur le bus, observé par l'hôte détaché qui annule sa tâche ; `annuler` publie au lieu d'appeler `.cancel()` quand le run est détaché. | 2 |
| 4 | **#445** — Brief `humain` et validation depuis un hôte détaché *(parallèle)* | Câblage `arbitre_brief_redis` / `validateur_redis` côté hôte, et le fail-safe : bus refermé sans décision ⇒ le run échoue, il n'approuve pas par défaut. | 2 |
| 5 | **#446** — Bascule du défaut, et la fin de vie d'un hôte | Le détaché devient le défaut des lancements Control Tower ; l'hôte **publie son issue** en partant — ce que `--publier` ne fait pas aujourd'hui, d'où un run terminé normalement qui finit `orphelin` (`battement.py`, corollaire assumé) ; ramassage des hôtes morts. | 3, 4 |
| 6 | **#447** — Tests + doc | Suites du chantier, mise à jour de docs/05 (frontière et corollaire), docs/07 §6.8 (limites du mode durable), et de cette note en §« la suite ». | 5 |

Le lot 2 porte au passage le **prérequis commun** identifié au §3 : faire descendre le `projet_id`
jusqu'à un hôte qui n'est pas l'API. C'est là et pas ailleurs parce que c'est le premier lot où un
run s'exécute hors du process qui connaît le projet — et parce que ce travail sert **aussi** O4 le
jour où la porte du §8 s'ouvre.

Deux lots sont marqués **(parallèle)** : l'annulation et le canal humain sont indépendants l'un de
l'autre une fois le lancement détaché en place. Le lot 6, comme toujours, n'est jamais marqué et
reste derrière l'ensemble.
