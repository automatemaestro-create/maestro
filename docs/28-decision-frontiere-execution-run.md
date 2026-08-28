# 28 — Frontière d'exécution d'un run : note de décision

> Ticket #350 (lot 3/4 du parent #347). Décision datée du **2026-08-23**, sur `origin/main` à
> `c284e6b`.
>
> **Verdict : l'hôte de run détaché.** L'exécution d'un run sort du process de `maestro-api` pour
> un process qu'elle possède, sur la même machine. Elle y gagne de survivre à tout ce qui arrête
> l'API — et garde, sans les repayer, l'annulation immédiate, le brief `humain` et la validation
> humaine. **Temporal n'est pas écarté** : il est mis derrière une porte nommée (§8), et rien de ce
> qui est construit ici n'est jeté le jour où on la franchit.
>
> **Deux révisions datées ont suivi, et aucune ne défait ce verdict.** Le **§11** (2026-08-24,
> #470/#486) sépare l'accident de la décision : un run survit à l'accident, pas à l'extinction. Le
> **§12** (2026-08-28, #701) franchit la porte n° 4 du §8 — la reprise à l'endroit exact — et
> tranche l'**état acquis durable hors process, sans Temporal** : sur l'axe même de cette porte, O4
> ne reprend pas plus finement. Il reste derrière les trois autres.

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
  vivre *dans* une activité — conclusion **confirmée le 2026-08-28** (§12.5), mais pour une autre
  raison que celle donnée ici, et le pointeur ci-dessous a bougé depuis (la note est en
  `maestro/engine/guardrails.py:427-433`) : `guardrails.py:208-214` note qu'un validateur qui bloque la boucle est
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

> ⚠ **Ce corollaire était trop large d'un cas, corrigé le 2026-08-24** (revue #470,
> [docs/29 §5](./29-decision-run-objet-de-premier-plan.md)) **et livré par #486**. Il énumérait trois
> gestes — « fermer la fenêtre, relancer l'API, `--stop` » — comme s'ils étaient de même nature. Les
> deux premiers sont des **accidents**, et les protéger est ce que ce chantier a acheté ;
> `start.sh --stop` est une **décision**, et un run qui lui survit tourne sans écran pour le suivre
> ni bouton pour l'arrêter.
>
> **Le corollaire à jour se lit : un run survit à l'accident, pas à l'extinction — ni à sa
> machine.** Rien du verdict ci-dessus n'est défait ; c'est sa formulation qui l'était. Voir le
> **§11**, plus bas, pour ce que #486 a livré.

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

> ⚠ **La n° 4 a été franchie le 2026-08-28 — et elle n'a pas mené à O4** (#701, **§12**). La
> condition est bien remplie : la reprise exacte est demandée, #699 et #700 en font une urgence.
> Mais sa formulation portait une prémisse fausse — « que Temporal fait nativement » était vrai au
> sens où il était le **seul** mécanisme du dépôt à la faire, jamais au sens où il était le seul
> possible. Sur l'axe même de cette porte, **O4 ne reprend pas plus finement** que l'option retenue :
> les deux reprennent à la tâche terminée, une tâche en vol étant repayée des deux côtés
> (`durable/workflow.py:117-118`). Le verdict du §12 est donc l'**état acquis durable hors process,
> sans Temporal** ; O4 reste derrière les trois autres portes, intactes.
>
> Les conditions qui rouvriraient *cette* décision-là sont nommées au **§12.8**, et l'une d'elles ne
> mène pas ici non plus : reprendre **au milieu** d'une tâche est une porte vers le découpage des
> tâches, pas vers Temporal.

> ⚠ **Il y en a une cinquième depuis le 2026-08-24, et elle va dans l'autre sens** (revue #470,
> [docs/29 §5](./29-decision-run-objet-de-premier-plan.md)). Les quatre ci-dessus rouvrent toutes la
> question vers **plus** de durabilité — elles mènent à O4. La cinquième demande **moins** de
> survie, sur un geste précis : **l'arrêt volontaire doit solder les runs**. Elle n'était pas
> prévue ici parce que la section ne regardait qu'une direction. Elle ne franchit aucune porte
> d'O4 et ne rapproche d'aucune ; elle est traitée au **§11** et par #486.

## 9. Le chantier — découpage en lots

> ⚠ **Ce paragraphe est le plan, pas l'état des lieux.** Le chantier est livré : ce qui a
> réellement été construit, ce que le plan a dû corriger en route et ce que ça a coûté se lisent
> au **§10**, plus bas.

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

---

## 10. La suite — le chantier livré (2026-08-24)

> Écrit au lot final **#447**, le lendemain de la décision. Cette section ne révise ni §4 ni §5 —
> les options tiennent, le verdict aussi — mais rend leur **contrepartie constatée** : l'hôte
> détaché a été construit, basculé en défaut, et voici ce qu'il a tenu, ce qu'il a coûté et les
> trois endroits où le plan a dû être corrigé en route. Les chiffres sont relevés sur le dépôt,
> jamais recopiés du plan.

### 10.1 Ce qui a été livré

Les **cinq** premiers lots ont été mergés le **2026-08-24**, de `3102576` (la note) à `4a10fd3` (la
bascule) : **15 fichiers, +2 654 / −185**. Le sixième est la PR qui porte cette section.

| Lot | Livré | Taille |
| --- | --- | --- |
| **#442** — le contrat | `maestro/controltower/hote.py` (`HoteRun`, `OrdreRun`, `DemarrageHoteRate`) et `hote_en_process.py` : la connaissance « un run est une tâche de ce process », qui vivait dans cinq méthodes du service, tient désormais dans une classe. Aucun changement de comportement. | +456 / −80 |
| **#443** — l'hôte détaché | `hote_detache.py` : les deux côtés de la frontière dans un fichier — le lanceur sérialise l'ordre, `main` le relit. Détachement par plateforme, témoin de démarrage, journal, `MAESTRO_HOTE_RUN` **opt-in**. | +877 / −19 |
| **#444** — l'annulation | Le process **écoute** : il guette l'issue `annulee` de son run sur le bus et annule sa propre tâche. Repli franc du lanceur derrière. | +338 / −67 |
| **#445** — le canal humain | Les trois arbitres branchés sur le bus de *ce* process, un seul bus pour quatre abonnements. Le refus temporaire du brief `humain` au lancement disparaît. | +482 / −63 |
| **#446** — la bascule et la fin de vie | `detache` devient le défaut ; l'hôte **publie son issue** en partant et retire son battement (`bridge.solder_le_run`), `maestro-run --publier` compris ; `HoteRun.ramasser` + `ServiceExecutions._ramasser` pour ce qui meurt sans un mot. | +760 / −215 |
| **#447** — tests et doc | [`tests/test_hote_detache.py`](../tests/test_hote_detache.py) (le process : transport, démarrage, survie, annulation, canal humain, issue, ramassage) et [`tests/test_hote_run.py`](../tests/test_hote_run.py) (la frontière vue de l'appelant : les deux hôtes, le service, le déploiement). Plus cette section, docs/05 et docs/07 §6.8. | cette PR |

### 10.2 Ce que la note avait bien vu

Quatre prévisions du §4.3, tenues sans mauvaise surprise :

- **aucune dépendance d'infra nouvelle.** `sys.executable -m <module>` : même interpréteur, même
  `.venv`, même machine, même Redis. Le chantier de résolution de binaire d'AionUi (§7) n'a pas eu
  lieu, et rien n'a été ajouté à `infra/` ;
- **`Task.cancel` reste le mécanisme réel**, à un aller Redis près. La propriété que le POC avait
  protégée exprès (#185) n'a pas été repayée ;
- **les trois attentes humaines sont du câblage.** Le lot 4 n'invente aucun mécanisme d'attente et
  aucun fail-safe : les deux existants — lever sur le brief, refuser l'action sensible — retombent
  d'eux-mêmes sur le bon comportement, y compris dans le cas neuf du lot (un bus qu'on n'a pas pu
  *construire*, où rien n'est câblé du tout) ;
- **« on peut rater un démarrage »** est bien la seule chose que la veille AionUi coûte, et c'est
  le seul défaut que `lancer` remonte (`DemarrageHoteRate`) — code de sortie, dernières lignes du
  journal de l'hôte et chemin de ce journal, dans le `detail` du run.

Une cinquième, moins visible : le **prérequis commun** du §3 (faire descendre `projet_id`) a été
payé au lot 2 comme prévu, et il servira O4 tel quel le jour où la porte du §8 s'ouvre.

### 10.3 Les trois corrections en route

**① Le contrat a un quatrième verbe.** Le §7 le décrivait comme une abstraction de « lancement /
annulation / observation » ; il en a une de plus, `ramasser` (#446). Ce n'est pas un quatrième
*pouvoir* et c'est ce qui le rend sûr : `runs_en_vol` dit ce qui vit, donc rien de ce qui vient de
cesser. L'hôte rapporte un **fait** — ce process est mort, voici son code et sa trace — et
l'appelant seul, qui lit la projection, décide de ce qu'il signifie. Lui faire dire « ce run a
échoué » lui demanderait de connaître le statut du run, c'est-à-dire précisément ce que le contrat
existe pour lui épargner.

**② Le brief `humain` a été refusé pendant deux lots.** La note le donnait « inchangé » (§4.3) et il
l'est *au bout* — mais entre le lot 2 et le lot 4, la décision n'avait aucun canal jusqu'au process,
et un run parti dans ces conditions serait resté suspendu pour toujours. Or chaque lot doit être
mergeable seul sans casser l'existant : `HoteRunDetache.lancer` a donc **refusé** le mode `humain`
le temps que le canal existe. Le refus temporaire est le prix de la découpe, pas un revirement — et
il valait mieux que l'alternative, qui était de fusionner les lots 2 et 4.

**③ La fin de vie d'un hôte a coûté plus cher que sa naissance.** Le lot 5 est le plus gros du
chantier en lignes **touchées** (+760 / −215, sur 14 fichiers, contre 7 pour le lot qui a écrit
l'hôte lui-même), et ce n'est pas la bascule qui pèse : elle tient dans une valeur de repli, exactement
comme les quatre lots précédents l'avaient préparée. Ce qui pèse est le **corollaire de #348**, que
le plan mentionnait en une ligne. Un run publié hors de l'API n'émettait aucun statut de fin, donc
son dernier battement vieillissait et le faisait apparaître `orphelin` alors qu'il avait très bien
terminé. Acceptable tant que le détaché était opt-in ; plus du tout une fois qu'il est le chemin de
tous les lancements Control Tower. Quatre gestes ont dû naître ensemble — publier l'issue
(`bridge.solder_le_run`), retirer le battement (`battement.oublieur_redis`), constater les morts
(`HoteRun.ramasser`) et les trancher (`ServiceExecutions._ramasser`) — et le **même défaut vivait à
côté**, dans `maestro-run --publier`, qu'il a fallu réparer dans le même lot sous peine de le voir
survivre à sa propre correction.

### 10.4 Le corollaire, à jour

Il ne disparaît pas, il **change de portée** : un run survit à son API, **pas à sa machine**. Ce qui
change vraiment est le sens du verdict `orphelin` :

| | avant le chantier | depuis #446 |
| --- | --- | --- |
| Fermer la fenêtre du navigateur, relancer l'API, `start.sh --stop` | le run meurt | **le run continue** |
| Un run terminé normalement hors de l'API | finit `orphelin`, faute de statut de fin | publie son issue et se solde |
| Un hôte mort **sous les yeux de l'API** | reste `en_cours` jusqu'au seuil d'orphelinat, puis pour toujours | ramassé et soldé `echec` **avec sa cause** |
| Machine endormie, process tué net, Redis muet au dernier instant | `orphelin` | `orphelin` — et c'est exactement ce que le verdict doit signaler |

> ⚠ **La première ligne a vieilli deux fois, et elle n'en garde plus qu'un cas.** Le 2026-08-24 en a
> détaché `start.sh --stop`, qui **solde ses runs depuis #486** (§11) ; le 2026-08-28 en a détaché la
> **fenêtre du navigateur**, qui les solde depuis **#700** (§11.2). « Le run continue » n'est donc
> plus vrai que de l'**API relancée** — et du crash, et du `SIGTERM`. Le tableau est laissé tel qu'il
> a été mesuré au 2026-08-24 ; ce sont les gestes qui ont changé de camp, pas la mesure.

Deux mesures relevées au passage, sur le poste de référence (Windows, 2026-08-24) : un hôte détaché
s'arme en **1,3 s** quand Redis répond et **5,5 s** quand il ne répond pas (le premier battement est
synchrone et une connexion refusée coûte ses quatre secondes de tentatives), d'où un plafond
d'attente de démarrage à trente secondes — le triple de marge sur le pire cas observé. Et le
ramassage accorde un **délai de grâce** de cinq secondes avant de conclure : un process publie son
issue *puis* sort, et regarder entre les deux ferait solder en `echec` un run qui vient d'annoncer
sa réussite.

### 10.5 Les quatre portes du §8, au 2026-08-24

Aucune n'est franchie, et le chantier n'en a rapproché aucune :

1. **un second run perdu par sommeil machine** *après* cette livraison — il n'y en a pas eu ;
2. **l'exécution quitte la machine** — non : le process fils est sur la même machine, par
   construction ;
3. **des runs plus longs qu'une journée** — non : un run se compte toujours en dizaines de minutes ;
4. **une reprise à l'endroit exact de l'interruption** — toujours pas offerte, et toujours pas
   demandée ; #349 repart du brief.

> ⚠ **Le point 4 a vieilli de quatre jours** : la reprise exacte a été **demandée le 2026-08-28**
> (#699, #700, #701), et c'est la seule des quatre portes à avoir bougé. Le relevé ci-dessus est
> laissé tel qu'il a été fait au 2026-08-24 ; la suite est au **§12**, qui la franchit **sans**
> ouvrir O4.

Ce que la livraison change pour O4 est ailleurs, et c'est le §5 qui l'annonçait : la Control Tower a
**cessé de supposer que le run vit dans son process**. Lancer, annuler, recevoir le brief et la
validation, transporter le `projet_id`, publier son issue en partant — tout cela passe désormais par
un contrat que ni les routes, ni les événements, ni la projection ne connaissent autrement que par
son nom. Un hôte Temporal s'y brancherait sans réécrire l'appelant ; c'est la seule chose qu'il
fallait acheter d'avance, et elle est acquise.

---

## 11. La cinquième porte — l'arrêt volontaire (2026-08-24)

> Écrit au ticket **#470**, le jour même de la livraison du chantier. Cette section ne révise ni le
> verdict du §5, ni aucune des quatre options : l'hôte détaché reste retenu et rien de ce qui a été
> construit n'est repris. Elle corrige une **formulation** — celle du corollaire — qui couvrait un
> cas de trop, et ajoute au §8 une condition qu'il ne pouvait pas voir parce qu'il ne regardait
> qu'une direction. L'arbitrage complet est en
> [docs/29 §5](./29-decision-run-objet-de-premier-plan.md).

> ⚠ **Une ligne de cette section a été renversée le 2026-08-28 (#700), et c'est la première.** La
> fenêtre du navigateur fermée n'est plus un accident : elle **solde**, comme `--stop`. Le
> raisonnement de #470 tient tout entier — c'est sa **frontière** qui s'est déplacée, sous une mesure
> qu'il n'avait pas (#699). Le §11.2 porte le renversement, ses raisons et son prix ; la table
> ci-dessous **dit la règle en vigueur**, chaque ligne datée de ce qui l'a posée. Le texte qui
> l'entoure est laissé tel qu'il a été écrit : ce qui a vieilli est nommé, pas réécrit.

**Les trois gestes du §5 ne sont pas de même nature.** « Fermer la fenêtre, relancer l'API,
`--stop` » : les deux premiers sont des **accidents** — personne n'a demandé d'arrêter le run, et
les rendre inoffensifs est exactement ce que ce chantier a acheté. Le troisième est une
**décision**. Les traiter ensemble était juste tant que la question posée était « qui tue un run
qu'on voulait garder ? » ; elle ne l'est plus dès qu'on pose l'autre : « que devient un run qu'on
voulait arrêter ? »

> ⚠ Ce paragraphe **range la fenêtre du bon côté pour la mauvaise raison**, et #700 ne le corrige
> qu'à moitié : la fenêtre a bien changé de camp, mais parce qu'elle est une décision — pas parce
> que la distinction accident / décision aurait été fausse. Elle reste ce qui départage, à ceci près
> qu'elle passe désormais entre **arrêter** et **redémarrer** (§11.2).

**Ce qu'un run survivant à l'extinction coûte réellement.** Control Tower éteinte, il continue de
consommer du quota et d'écrire dans le projet de l'utilisateur — sans écran pour le suivre, sans
bouton pour l'arrêter, et sans rien qui signale son existence. Ce n'est pas la robustesse que le §5
défendait, c'est son ombre portée : la survie devient une fuite dès qu'elle dépasse l'intention.

**La table en vigueur** (les deux premières lignes revues au 2026-08-28, #700) :

| Geste | Nature | Ce que devient le run |
| --- | --- | --- |
| Fenêtre du navigateur fermée (chien de garde #149) | **décision** — on quitte la Control Tower | **soldé**, et reprenable au redémarrage (#700) |
| Démarrage (`arreter_session` rejouée par `start.sh`), crash, `SIGTERM` | accident | **continue** — c'est la propriété qu'on ne défait pas, et il n'en reste qu'elle |
| `start.sh --stop`, quitter l'enveloppe le jour où elle existe | **décision** | **soldé**, et reprenable au redémarrage (#439, #486) |
| Machine endormie | ni l'un ni l'autre | `orphelin` — inchangé, traité par #348 et #349 |

**La distinction vit du côté qui sait.** `start.sh --stop` sait qu'il arrête exprès ; un `SIGTERM`
reçu par l'API ne le sait pas — il peut venir d'un arrêt propre comme d'un gestionnaire de tâches.
La déduire d'un signal ferait exactement la confusion que cette section défait, à l'étage en
dessous. Elle descend donc depuis l'appelant, comme une cause.

**Ce que ça ne coûte pas.** Aucun des six lots n'est repris. L'extinction passe par `_eteindre`
(`hote_detache.py`), qui vise **déjà** le groupe de process et non l'hôte seul — la leçon de #291,
*tuer un parent avant ses enfants fabrique l'orphelin qu'on veut éviter*, et un hôte tenait cinq
process au premier essai du lot 3. Ce qui manque est la cause d'arrêt et la reprise au redémarrage.
Le travail est **ajouté**, pas repayé : c'est #486.

**Et cette porte ne mène pas à O4.** Les quatre du §8 sont des conditions de *plus* de durabilité ;
celle-ci demande moins de survie sur un geste précis, et n'en rapproche aucune. Le jour où
quelqu'un voudra délibérément partir en laissant tourner, la réponse sera une **option** sur un
geste qui solde par défaut — jamais un défaut qui laisse tourner en silence.

### 11.1 Ce que #486 a livré (2026-08-25)

**Une porte, et elle est explicite** : `POST /api/extinction`. C'est le seul endroit par lequel un
arrêt volontaire se déclare — `scripts/controltower/start.sh --stop` l'appelle **avant** de libérer
les ports, et la fermeture de l'enveloppe l'appellera le jour où elle existera. Le service y solde
chaque run que **cette API porte** (`HoteRun.runs_en_vol`), par le geste qui existait déjà
(`ServiceExecutions._solder`) : issue publiée — donc entendue par le process détaché, qui annule sa
propre tâche (#444) —, hôte éteint **avec sa descendance** au bout du délai de grâce
(`hote_detache._eteindre`), tâches soldées et battement retiré.

**La distinction ne se déduit toujours d'aucun signal**, et c'est ce que cette forme achète : le
`lifespan` de l'API — donc un `SIGTERM`, un plantage, l'API qu'on tue pour la relancer — passe par
`ServiceExecutions.fermer`, qui **ne touche à rien**. Un drapeau sur cette méthode-là aurait demandé
à celui qui ne sait pas de deviner. Corollaire de forme : l'appel vit dans les branches de `start.sh`
qui **savent** qu'on arrête, et **surtout pas** dans `arreter_session`, que le démarrage rejoue pour
remplacer la session précédente — l'y mettre aurait soldé les runs à chaque relance, c'est-à-dire
fabriqué l'accident qu'on protège.

> ⚠ Cette phrase citait la **fenêtre refermée par le chien de garde #149** parmi les arrêts subis, et
> « la seule branche `--stop` » comme l'unique appelant : les deux ont été renversés le 2026-08-28
> (§11.2). Ce qui n'a pas bougé est l'essentiel — l'API ne devine rien, `fermer` ne solde toujours
> rien, et `arreter_session` reste hors du chemin. Le chien de garde pousse la porte **lui-même**,
> avant de tuer l'API, exactement comme `--stop` : le savoir descend toujours de celui qui l'a.

**Ce qui rend le run reprenable est une cause, pas un statut.** Le run est consigné `annulee` comme
n'importe quelle interruption ; ce qui le distingue est `CAUSE_EXTINCTION`
(`maestro/controltower/causes.py`), le sixième code de #479. C'est lui, et lui seul, que
`ServiceExecutions.relancer` accepte parmi les runs soldés, et que l'UI lit pour proposer le bouton
**« Reprendre » déjà existant** (#349, `PanneauRunsPerdus`) — le brief approuvé restant requis comme
partout ailleurs. Un run **délibérément annulé** n'entre pas par là : personne ne veut se voir
reproposer un run qu'il vient d'arrêter. Le laissez-passer est **consommé** à la reprise (la relance
re-solde le run avec la cause `annulation`), ce qui garde le garde-fou de #349 contre le double clic.

**L'option annoncée plus haut existe déjà** : `MAESTRO_EXTINCTION=0` laisse délibérément tourner —
sur un geste qui solde par défaut, et **en le disant**, jamais en silence.

### 11.2 La fenêtre fermée change de camp (2026-08-28)

> Écrit au ticket **#700**. Cette section **renverse** la première ligne de la table du §11 : fermer
> la fenêtre du navigateur solde désormais les runs en vol, comme `--stop`. Elle ne reprend ni le
> §5, ni les quatre options, ni une ligne de l'hôte détaché — et elle ne défait pas le raisonnement
> de #470, dont elle déplace la **frontière** sous une mesure qu'il n'avait pas.

**Ce que #470 ne pouvait pas savoir.** Sa table repose sur une prémisse : la survie du run
*préserve* le travail. #699 l'a mesurée fausse le 2026-08-28 — le bus Redis est du **Pub/Sub
éphémère** et le journal durable n'est alimenté que par la **pompe de l'API**. Un run qui survit à
la fermeture de la fenêtre continue donc de consommer du quota **et perd définitivement son
historique** : au retour, sa tâche finie est encore « en cours », la suivante n'a aucun statut, le
compte de tâches est faux. La survie ne préserve plus le run, elle le rend **invisible et
incorrigible** — c'est-à-dire exactement l'« ombre portée » que le §11 décrivait pour `--stop`, et
qu'on découvre valable ici. L'accident n'est pas inoffensif ; il ne l'est plus, donc la ligne bouge.

**Et la fenêtre n'était pas un accident.** C'est la seconde moitié, et elle se lisait déjà dans le
script : le chien de garde #149 coupe **l'API et l'UI** dès que la fenêtre se ferme. Personne
n'appelle « accident » un geste qui arrête délibérément les deux services ; le run était la seule
chose qui survivait à un arrêt que `start.sh` tient pour volontaire **partout ailleurs**. Fermer la
fenêtre de la Control Tower, c'est quitter la Control Tower. La distinction accident / décision de
#470 reste donc entière — c'est son tracé qui était faux d'un cas.

**La ligne de partage passe entre arrêter et redémarrer.** Ce qui reste un accident est le
**démarrage** (`arreter_session`, rejouée par `start.sh` pour remplacer la session précédente), le
crash et le `SIGTERM`. Trois raisons, et la première suffirait :

1. **Un démarrage n'est pas un arrêt.** Solder dans `arreter_session` tuerait le run par le geste
   même qui vient le reprendre en main. C'est le corollaire de forme du §11.1, et il ne bouge pas.
2. **Le prix n'est pas symétrique.** La reprise (#349) ne repart **pas** de l'interruption : elle
   rejoue **toutes** les tâches depuis la synthèse du brief approuvé — un run arrêté à 4 tâches sur 5
   en coûte 5 —, et elle **refuse net** un run sans brief approuvé (`MOTIF_RELANCE_SANS_CADRAGE`),
   c'est-à-dire tout run en `mode_brief: auto`. Solder à chaque relance ferait payer la reprise
   intégrale au geste le plus fréquent du développement, et **perdrait sans retour** le travail des
   runs sans cadrage.
3. **La fenêtre d'invisibilité du redémarrage est bornée par lui-même.** L'API repart dans la
   foulée : elle rejoue le journal, reprend la pompe, et le run **réapparaît à l'écran** — puis reste
   interruptible, l'annulation voyageant par le **bus** que le process détaché écoute (#444) et non
   par le registre d'hôtes que la nouvelle API n'a pas. Ce qu'elle perd est ce que le bus a diffusé
   pendant la coupure, quelques secondes ; une fenêtre fermée, elle, ne fait repartir personne.

**Ce qui a été livré est le déplacement d'un appel.** Rien n'est construit : `POST /api/extinction`
(#486) et `solder_les_runs` (`scripts/controltower/start.sh`) sont inchangés. Le chien de garde les
appelle **avant** de libérer les ports — après, l'API qui tient les hôtes détachés n'existe plus —,
et il le fait **après** avoir retiré le jeton de session, pour qu'un `--stop` concurrent ne se
dispute pas la fenêtre avec lui. Il y a donc **deux appelants et deux seulement**, les deux gestes
d'arrêt ; `arreter_session` reste hors du chemin, et
[`tests/test_extinction.py`](../tests/test_extinction.py) le garde par un invariant de forme prouvé
sur échantillon fautif, faute de pouvoir jouer un chemin qui demande Redis, l'UI et une fenêtre.

**Ce que l'arrêt fera se dit au démarrage**, et pas seulement au moment où il l'a fait : c'est là
qu'on part travailler en sachant ce que fermer la fenêtre emportera, et là que l'option se présente.
`MAESTRO_EXTINCTION=0` reste la sortie explicite pour laisser tourner — annoncée **des deux côtés**,
jamais en silence. Une réserve de forme, dite plutôt que masquée : fermée par le chien de garde, la
Control Tower nomme les runs qu'elle solde dans `navigateur.log` et non sur le terminal, qui a rendu
la main depuis longtemps ; le démarrage imprime donc le chemin de ce journal avec l'annonce.

**Ce que ça ne ferme pas.** La fenêtre de #699 est **réduite, pas fermée** : un crash de l'API, un
`maestro-run --publier` hors Control Tower, une machine endormie continuent de publier dans le vide.
Un cas neuf s'y ajoute, petit et à nommer : un **démarrage qui échoue après** `arreter_session` (API
qui ne répond pas, UI qui ne compile pas) laisse le run de la session précédente sans écran, comme
un crash — l'accident toléré est alors payé sans que la relance ait abouti. La durabilité du journal
reste le sujet de #699, et la reprise **à l'endroit exact** de l'interruption reste la porte 4 du
§10.5, toujours pas franchie.

> ⚠ **Les deux phrases ci-dessus ont été dépassées dans la journée**, et par les deux tickets
> qu'elles nomment. #699 a été livré quatre minutes après cette section (`b8d885a`) : la
> consignation a suivi la **publication**, donc la durabilité du journal ne dépend plus d'un
> consommateur vivant. Et la porte 4 est **franchie** par #701, au **§12** — sans mener à O4. Ce
> qui reste vrai ici : le cas neuf du démarrage qui échoue, et le fait que la reprise dont dispose
> #700 est encore celle de #349. Le §12.7 reprend ce dernier point, qui est le prix assumé de cette
> section jusqu'au chantier de reprise.

---

## 12. La porte n° 4 est franchie — la reprise exacte (2026-08-28)

> Ticket **#701**. Décision datée du **2026-08-28**, sur `origin/main` à `b8d885a` — donc **après**
> #700 (§11.2) et #699, livrés le même jour et pris en compte tels qu'ils sont dans `main`, pas tels
> que leurs tickets les annonçaient (§12.7). Aucun code n'est livré par ce cadrage.
>
> **Verdict : l'état acquis devient durable hors du process, sans Temporal.** Un run interrompu
> repart de ses tâches déjà abouties au lieu de rejouer depuis le brief. **O4 reste derrière sa
> porte** — mais la porte a changé de serrure : le §8 la formulait comme « le besoin d'une reprise à
> l'endroit exact », en supposant que ce besoin *appelait* Temporal. Il ne l'appelle pas, et le
> §12.5 dit pourquoi : sur l'axe même de cette porte, **O4 ne reprend pas plus finement** que
> l'option retenue.

### 12.1 Ce qui a changé depuis le 2026-08-23

La porte n° 4 du §8 — « une reprise à l'endroit exact de l'interruption » — était classée « toujours
pas offerte, et **toujours pas demandée** » au §10.5. Elle a été **demandée le 2026-08-28**, et deux
tickets du même jour l'ont rendue urgente plutôt que théorique. Tous deux ont été **livrés dans la
journée**, avant que ce cadrage ne soit rendu — ce qui change ce qu'il reste à dire d'eux, et le
§12.7 en tire les conséquences :

- **#699** (livré, `b8d885a`) : le bus Redis est du Pub/Sub éphémère, et le journal durable (#97)
  n'était alimenté que par la pompe de l'API — donc par un **consommateur**. Un run détaché qui
  publiait pendant que l'API était arrêtée publiait **dans le vide** : rien n'était consigné, et le
  rejeu au démarrage rebâtissait fidèlement une projection trouée. La consignation a suivi la
  **publication** (`persistence.BusDurable`, `bridge.publieur_redis`), et la pompe ne consigne plus
  rien (`app.py:808-814`) ;
- **#700** (livré, `aa3e3c7`, §11.2) : fermer la fenêtre du navigateur **solde** désormais les runs
  en vol, comme `--stop`. Or « reprenable » ne vaut que ce que vaut `ServiceExecutions.relancer`
  (#349), qui crée un **nouveau** run reparti de la synthèse du brief et **rejoue toutes les
  tâches**.

Les trois tickets butent sur la même question, et elle est de frontière : sur un run arrêté à quatre
tâches sur cinq, la Control Tower en repaie cinq. Le §11.2 le dit de son côté, et se conclut en
renvoyant ici : « la reprise **à l'endroit exact** de l'interruption reste la porte 4 du §10.5,
toujours pas franchie ». C'est cette phrase que la présente section périme.

**Et le statu quo ne dégrade pas, il refuse.** `relancer` exige un brief approuvé
(`MOTIF_RELANCE_SANS_CADRAGE`, `executions.py:261`) — mesuré sur `811d738020d5` le 2026-08-28 :
`brief = None`, donc **non reprenable du tout**. Ce n'est pas un cas de bord : `ouvrir_un_run`, le
lanceur du fil de chat (#268), part en `MODE_BRIEF_AUTO` et non en `humain`
(`app.py:1099-1113`), pour une raison qui reste bonne — « le cadrage d'une demande **est** la
conversation qu'on est en train d'avoir ». Et [docs/29 §4](./29-decision-run-objet-de-premier-plan.md)
fait du chat **la seule porte d'entrée**. La reprise de #349 n'a donc aucune prise sur la porte qui
devient le défaut. Le déménagement du brief dans le fil refermera ce trou pour les runs qu'on valide ;
il ne le refermera pas pour ceux qui partent sans validation, et le refus de `relancer` est **binaire**.

**Un troisième fait, qui ne se lit pas comme un coût.** Une tâche peut **écrire dans le projet de
l'utilisateur** — `maestro.projets.application.appliquer` (`application.py:643`), après accord
humain. Rejouer les cinq tâches d'un run arrêté à quatre ne coûte donc pas seulement le travail :
cela **repose des questions déjà tranchées** et **réapplique** dans un projet déjà modifié. Ce
n'est plus un argument de dépense, c'en est un de correction — et il n'était écrit nulle part.

### 12.2 Ce que « reprendre » demande vraiment — l'état acquis, mesuré

Le ticket #701 pose que le journal durable « porte des statuts et des étapes, pas des **sorties** ».
C'est exact et vérifié : pour l'issue d'une tâche, `bridge.evenements_depuis_step` met l'`erreur`
dans `detail` et **jamais** la `sortie` (`bridge.py:215-218`), et `Event` n'a aucun champ pour la
porter (`events.py:263` et suivantes). ⚠ **#699 n'y a rien changé, et c'est à vérifier avant de le
lire de travers** : il a déplacé **où** l'on consigne — de la consommation vers la publication —, pas
**ce que** l'événement porte. Or `dep.sortie` est **littéralement** ce qui entre dans le prompt de la
tâche suivante — le « tableau noir » de `_build_task_description` (`executor.py:1547-1551`). La
Control Tower sait qu'une tâche a réussi ; elle ignore ce qu'elle a produit.

**Mais le trou est plus large d'une moitié, et cette moitié manquait au ticket.** Depuis #490 le
plan **est** persisté, en `run.plan` — sauf que `NoeudPlan` ne porte que `{id, titre, dependances,
etapes}` (`plan_run.py:97-100`), quand `Task` porte en plus `description`, `competences_requises` et
`format_sortie` (`schema.py:123-131`). Ces trois-là sont exactement ce dont l'exécution a besoin : la
description **est** le prompt (`executor.py:376`, `1538`), les compétences font le routage
(`executor.py:925`), le format cadre la sortie (`executor.py:1371`). Le graphe persisté suffit à
**dessiner** un plan ; il ne suffit pas à en **reprendre** l'exécution.

Reprendre demande donc **deux** choses hors process, pas une : le plan **exécutable** et les
**sorties**.

**Trois bonnes nouvelles, également vérifiées.**

- **Le format existe déjà, et il voyage déjà entre process.** `TaskResult.to_dict`/`from_dict`
  (`executor.py:200`, `230`) est ce que le mode durable sérialise pour passer les dépendances d'une
  activité à l'autre (`activities.py:277`), et ce que `resultats_acquis` rend à une reprise
  (`workflow.py:126-135`). Rien n'est à inventer : l'état acquis d'un run est **déjà** une valeur
  JSON, dans ce dépôt, aujourd'hui.
- **Aucun système de fichiers n'est à faire survivre.** `espace_de_travail` ouvre un espace **par
  tâche**, dérivé du projet (worktree Git ou copie de périmètre), sous un `mkdtemp` démonté en
  sortie (`sandbox/projet.py:93-138`). Une tâche ne reprend pas un répertoire : elle en dérive un
  neuf. L'état à reconstruire est donc **purement des valeurs**.
- **La granularité utile est la tâche terminée**, et c'est aussi celle de Temporal : « une tâche en
  vol au moment de l'interruption n'y figure pas — elle n'a rien produit et sera reprise »
  (`workflow.py:117-118`).

**Une quatrième, qui aurait été mauvaise la veille.** Persister l'état acquis **par le bus** aurait
hérité du défaut de #699 : la sortie d'une tâche qui se termine pendant une coupure de l'API aurait
été perdue — c'est-à-dire trouée exactement au moment où l'on reprend. C'était, à l'heure où ce
cadrage a commencé, un **prérequis** qui ordonnait les chantiers ; **#699 l'a levé le jour même**
(`b8d885a`), en portant la consignation là où l'événement naît. Le chantier de reprise part donc
d'un journal qui ne dépend plus d'un consommateur vivant, et c'est acquis sans qu'il ait à le payer.

⚠ La leçon, elle, reste et vaut pour ce qu'on ajoutera : **tout ce qu'un run doit pouvoir relire
après coup s'écrit du côté du producteur.** Un magasin d'état acquis branché sur la pompe
recréerait le trou que #699 vient de fermer, à un endroit où il coûterait plus cher — la projection
trouée se voit à l'écran, un état acquis troué se lit comme un run à moitié fait.

### 12.3 Les trois options

#### 12.3.1 (a) O4 — porter le chemin Control Tower sur `DurableEngine`/Temporal

L'option instruite au §4.4, à réévaluer et non à recopier. Elle offre la reprise **et** la survie au
sommeil de la machine, seule option à l'offrir. Son prix est au §12.5 : cinq portages et non trois,
dont deux sont des incompatibilités de nature.

Un fait de signature qui ne s'est pas amélioré : `DurableEngine.run(objective, *, journal)`
(`engine.py:116`) n'accepte toujours ni `ticket`, ni `projet_id`, ni `mode_brief`, ni `porte` — les
**quatre** que `OrchestrationEngine.run` prend en plus du journal (`loop.py:527-536`), et que l'hôte
détaché passe tous (`hote_detache.py:1052-1078`).

#### 12.3.2 (b) L'état acquis durable, sans Temporal

Persister hors du process ce qu'un run a **payé** — le plan exécutable et les `TaskResult` aboutis —
et reprendre en sautant ce qui est acquis. L'hôte du run écrit ; l'API relit et décide.

- **Infra** : aucune dépendance nouvelle. Redis est déjà requis par la Control Tower pour le bus
  (#46), le journal durable (#97), les boîtes (#44) et la file (#41).
- **Annulation, brief `humain`, validation, projet, pause** : **intacts**. L'option ne touche pas au
  moteur — elle ajoute un magasin et un verbe de reprise. C'est sa propriété principale : elle ne
  paie **aucun** des cinq portages du §12.5.
- **Survie** : au process de l'API **oui** (acquis depuis #446), à la machine **non** — mais au
  réveil, la reprise repart de l'acquis au lieu du brief, ce qui est l'essentiel du gain que la
  porte n° 4 demandait.
- **Reprise exacte** : **oui**, à la granularité de la tâche terminée — la même que Temporal.
- **Ce qu'elle n'achète pas** : la reprise **automatique**. Elle reprend sur un geste, celui que
  `PanneauRunsPerdus` propose déjà (#349).

#### 12.3.3 (c) Statu quo — relancer depuis le brief

La ligne de base : `relancer` (#349) conserve le cadrage — sur `3ff0bcb065f9`, deux tours de
clarification et une approbation, **2,52 $** et une vingtaine de minutes d'attention
([docs/05](./05-interface-control-tower.md)) —, ce qui reste le poste le plus cher d'un run de démo
(« ~1-5 $ », [docs/34 §3.3](./34-decision-agent-cli-tiers-acp.md)).

Ce qu'il faudrait **ouvrir** pour tenir sur ce seul socle, et c'est ce qui l'écarte : étendre
`relancer` aux runs sans brief approuvé — donc repartir de l'objectif brut, c'est-à-dire **sauter
une validation que le run attendait encore**, sans que personne l'ait demandé. Le §12.1 le dit : le
refus est binaire, et il porte sur la porte d'entrée qui devient le défaut. On paierait un
contournement du point de contrôle **D5** pour éviter de construire ce que (b) construit une fois.

⚠ **Ce socle-là n'est pas une hypothèse : c'est ce sur quoi #700 tourne aujourd'hui**, et il a été
livré en le sachant — sa raison n° 2 (§11.2) nomme ce même refus pour garder le **démarrage** hors
du chemin d'extinction. Le statu quo n'est donc pas écarté comme une option qu'on refuse, mais
décrit comme l'état **en vigueur**, dont le §12.7 dit le prix et l'échéance.

### 12.4 La comparaison, sur les axes du §4.5 — plus celui qu'il n'avait pas à juger

| | **(c)** statu quo | **(b)** état acquis durable | **(a)** O4 Temporal |
| --- | --- | --- | --- |
| **Reprise exacte** | **non** — rejoue tout depuis le brief, et **refuse** sans brief approuvé | **oui**, à la tâche terminée | **oui**, à la tâche terminée — *pas plus fin* |
| **Dépendance d'infra** | aucune | **aucune** (Redis déjà requis) | **Temporal requis** sur le chemin par défaut |
| **Annulation** | `Task.cancel` via le bus (#444) | **inchangée** | **coopérative** — activités à instrumenter |
| **Brief `humain` + validation** | marchent | **inchangés** | **réécrits** en `wait_condition` + `signal` (§12.5) |
| **Rattachement au projet** | acquis (#222, #443) | **inchangé** | absent de la signature `DurableEngine.run` |
| **Pause / reprendre (#477)** | acquise | **inchangée** | **absente** du durable — `porte` n'est pas un paramètre |
| **Survit à l'arrêt de l'API** | oui (#446) | oui | oui |
| **Survit au sommeil machine** | non | non — mais reprise **sur l'acquis** au réveil | **oui, reprise automatique** |
| **Coût** | nul, et un contournement de D5 à payer | **faible, incrémental** | **élevé** — cinq portages (§12.5) |

### 12.5 Les trois obstacles d'O4, réexaminés — et les deux qui manquaient

**① Temporal en dépendance dure du chemin par défaut — tient, et pèse un peu plus.** Rien n'a changé
côté Temporal ; ce qui a changé est autour. La panne n° 2 du §2 — la machine qui s'endort — est
toujours là, et l'on adosserait la durabilité à Docker, c'est-à-dire à ce qui tombe avec elle. À une
machine et un utilisateur devant, le change reste mauvais.

**② L'annulation coopérative — tient, mais l'écart s'est réduit, et il faut le dire.** Le §4.5
opposait `Task.cancel` **immédiat** à une cancellation coopérative. Depuis #444 l'annulation de la
Control Tower **traverse déjà une frontière** : elle publie sur le bus, l'hôte détaché l'observe et
annule sa propre tâche, sous `DELAI_ANNULATION_S = 5 s` (`executions.py:248`, `1014`). Le mécanisme
réel reste `Task.cancel`, mais l'immédiateté de 2026-08-23 n'est déjà plus la référence. La
régression subsiste — une activité de quarante minutes doit observer son contexte pour la voir —,
elle est simplement moins large qu'écrit.

**③ L'attente humaine dans une activité — tient, mais *pas pour la raison écrite*, et sa vraie forme
est plus dure.** Le §4.4 cite `guardrails.py` : un validateur qui bloque la boucle est « fatal en
mode durable, où la boucle doit continuer à battre le cœur des activités ». Deux corrections.

D'abord le pointeur : la note vit en **`maestro/engine/guardrails.py:427-433`**. Le §4.4 la donnait à
`guardrails.py:208-214`, où se trouve aujourd'hui tout autre chose (la docstring de
`DemandeValidation`), et #701 a recopié cette référence — c'est le §4.4 qu'elle corrige, pas le
ticket. Ensuite et surtout, **elle décrit le remède, pas le défaut** : un canal synchrone est
exécuté `await asyncio.to_thread(canal, demande)`
(`guardrails.py:433`) précisément pour ne pas bloquer la boucle. Le compagnon `_bat_le_coeur`
(`activities.py:199-203`) continue donc de battre pendant la délibération, et `_avec_battement`
enveloppe tout le travail de l'activité (`activities.py:279`). **Le battement tient.**

Ce qui ne tient pas est ailleurs, et c'est plus qu'un réglage. Une activité est bornée par son
`start_to_close_timeout` — `TIMEOUT_TACHE = 1 h` (`workflow.py:72`) — et une expiration est **rejouée**
par `RELANCE_PERTE_WORKER` jusqu'à trois fois (`workflow.py:86-91`). Une délibération plus longue
qu'une heure ferait donc **reposer la question à l'opérateur et repayer le travail de la tâche**. Or
le §3 le rappelle : aucune des trois attentes n'a de time-out, l'attente est « indéfinie […], jamais
un time-out silencieux » (`validation.py:88-91`) — **par décision**. Une attente indéfinie ne peut
pas vivre sous un `start_to_close_timeout`, quelle qu'en soit la valeur : ce n'est pas un plafond à
monter, c'est une incompatibilité de nature. La conclusion du §4.4 est donc **confirmée et
renforcée** — les trois arbitres ne sont pas portés vers le durable, ils y sont **réécrits** en
`workflow.wait_condition` sur un `@workflow.signal`.

**④ Le parallélisme — obstacle non listé en 2026-08-23.** `--parallele` est refusé en mode durable :
« le plafond global de concurrence n'est pas encore porté par le workflow » (`cli.py:333-339`). Or la
Control Tower passe `max_parallele=ordre.parallelisme` à chaque run (`hote_detache.py:1054`). Le
portage est à faire, et il n'était compté nulle part.

**⑤ La pause — obstacle qui n'existait pas en 2026-08-23.** `PorteExecution` (#477,
`engine/pause.py:45`) est câblée à la Control Tower et passée au moteur (`hote_detache.py:1077`).
Elle n'a **aucun équivalent** côté durable : suspendre un run reviendrait à écrire une seconde fois,
en `wait_condition`, ce que la boucle en process obtient d'un `asyncio.Event`. C'est le même poste
que l'obstacle ③, sur un autre objet.

**Bilan : cinq portages, pas trois** — le cadrage #318 en étape de workflow, les trois attentes
humaines réécrites, le parallélisme, la signature (`projet_id`, `ticket`, `mode_brief`), la pause —
plus une dépendance d'infra dure et une annulation dégradée.

### 12.6 Décision

**L'état acquis durable hors process (b) est retenu.**

**① Ce qui est demandé est la reprise, pas Temporal — et sur cet axe, O4 ne fait pas mieux.** C'est
le constat qui tranche, et il est vérifiable en une ligne de code : `resultats_acquis` exclut la
tâche en vol, qui « n'a rien produit et sera reprise » (`workflow.py:117-118`). Temporal reprend à la
tâche terminée ; (b) aussi. La porte n° 4 a été écrite en supposant que la reprise exacte *appelait*
le workflow durable — ce qui était vrai au sens où Temporal était le **seul** mécanisme du dépôt qui
la faisait, jamais au sens où il était le seul possible. On n'achète pas cinq portages et une
dépendance d'infra pour une granularité qu'on obtient sans eux.

**② Le format existe, le magasin manque.** `TaskResult` est déjà sérialisé et déjà transporté entre
process (`activities.py:277`) ; le plan est déjà écrit, à trois champs près (§12.2). Ce qui manque
n'est pas un modèle d'exécution, c'est un endroit où écrire et un verbe pour relire. Une option qui
comble un trou nommé coûte moins qu'une qui change de frontière.

**③ Elle ne paie aucune des propriétés qu'on utilise tous les jours.** Annulation, brief `humain`,
validation, projet, pause : (b) n'y touche pas. C'est le même raisonnement qu'au §5 — ne pas payer
une régression sur le quotidien pour un gain sur le rare —, appliqué à une liste devenue plus
longue de deux entrées depuis (le parallélisme et la pause).

**④ Et ce n'est pas (b) *au lieu de* O4, c'est (b) *avant* O4 — avec une honnêteté que le §5 n'avait
pas à avoir.** Au §5, aucun lot n'était jeté le jour d'O4. Ici, une pièce le serait : le **magasin**,
puisque Temporal fournirait l'état acquis par `resultats_acquis`. Ce n'est ni le format
(`TaskResult`, que Temporal transporte déjà tel quel), ni le **verbe de reprise** côté Control Tower
— « reprendre sur l'acquis ou relancer sur le brief » est une décision de produit, dont seule la
source d'état changerait. Ce qui serait jeté est un `RPUSH`/`LRANGE` ; ce qui serait gardé est le
contrat. Le change reste très favorable, et il vaut mieux l'écrire que le taire.

**Corollaire, à écrire là où les précédents le sont** (docs/05, `executions.py`, `battement.py`) :
**un run reprend là où il s'est arrêté, à la tâche près — pas au milieu d'une tâche.** Une tâche
interrompue en vol est repayée en entier, et aucune des trois options ne fait mieux.

### 12.7 Ce que ça change pour #699 et #700 — tous deux livrés avant ce cadrage

**Les trois tickets sont nés le même jour ; les deux autres sont arrivés les premiers.** #700
(`aa3e3c7`, 22:50) puis #699 (`b8d885a`, 22:54) étaient sur `main` quand cette section a été écrite.
Elle ne les instruit donc pas, elle **constate** ce qu'ils ont tranché et dit ce qui reste.

**#699 : la piste que ce cadrage aurait exigée est celle qui a été prise.** Il a porté la
consignation du côté de la **publication** — `BusDurable` pour les producteurs asynchrones,
`publieur_redis` pour le pont télémétrie, `RPUSH` et `PUBLISH` dans un seul `MULTI`/`EXEC` — et
retiré celle de la pompe, « deux écrivains sans dédoublonnage auraient doublé chaque ligne au lieu
d'en perdre ». C'est exactement la propriété dont le chantier de reprise a besoin, et elle est
**acquise sans qu'il ait à la payer** (§12.2). L'alternative Redis Streams n'a pas eu à être
tranchée.

**#700 : sa question ouverte est refermée, et dans le sens de ce cadrage.** Sa note technique posait
trois issues — ouvrir la relance aux runs sans brief, accepter de perdre leur travail, ou faire de
la reprise à l'endroit exact un chantier à part. Il a retenu la **troisième** sans attendre ce
cadrage, et sa §11.2 le dit en propres termes : « la reprise **à l'endroit exact** de l'interruption
reste la porte 4 du §10.5, toujours pas franchie ». Ce §12 la franchit ; le renvoi est désormais
dans les deux sens. La première issue est **écartée pour de bon** — repartir de l'objectif brut
sauterait la validation que le run attendait (§12.3.3), et la reprise exacte la rend sans objet.

**Mieux : #700 s'est appuyé sur le défaut que ce cadrage instruisait, pour tracer sa propre
frontière.** Sa raison n° 2 de garder `arreter_session` hors du chemin est mot pour mot le constat
du §12.1 — la relance « rejoue toutes les tâches » et « refuse net un run sans brief approuvé » —,
et il en tire la conclusion juste : solder à chaque relance « perdrait sans retour le travail des
runs sans cadrage ». Le même fait a servi deux décisions différentes le même jour, sans qu'aucune
n'ait à attendre l'autre.

**L'ordre que ce cadrage aurait prescrit était trop fort d'un cran, et les faits l'ont montré.**
Écrire « #699 → #700 → le chantier » supposait une dépendance qui n'existe pas : #700 n'avait pas
besoin de #699 pour être juste, il avait besoin de sa **mesure**, qu'il avait déjà. Ce qui est
réellement forcé est plus étroit — **#699 avant le chantier de reprise**, et rien d'autre. Il est
satisfait.

⚠ **Un prix subsiste, et il n'appartient à personne des deux.** Depuis #700, fermer la fenêtre solde
les runs en vol ; un run lancé depuis le chat (`MODE_BRIEF_AUTO`, `app.py:1099-1113`) ainsi soldé
n'est **pas reprenable du tout** (`MOTIF_RELANCE_SANS_CADRAGE`). #700 a traité ce risque là où il
pouvait — en gardant le **démarrage** hors du chemin, sa raison n° 2 —, mais il ne pouvait pas le
traiter pour le geste qu'il ajoute, sauf à ne pas l'ajouter. C'est donc une conséquence assumée qui
**vit jusqu'au chantier de reprise**, et qui doit être **dite au moment du geste** plutôt que
découverte au retour : un run non reprenable qu'on solde en fermant la fenêtre doit être nommé comme
tel. C'est le seul point que ce cadrage laisse à la charge de l'existant.

**Ce que ce cadrage ne ferme pas** : la reprise **automatique** au réveil de la machine (elle reste
derrière O4), la reprise **au milieu d'une tâche** (§12.8), et le découpage du chantier lui-même, qui
est fait séparément — comme le §9 l'a été pour #441.

### 12.8 Ce qui rouvrirait cette question-ci

Le §8 garde ses portes vers O4 ; deux d'entre elles sont **inchangées et intactes** — l'exécution qui
quitte la machine (n° 2), des runs plus longs qu'une journée (n° 3). La n° 1 (un second run perdu par
sommeil machine) reste ouverte, avec une nuance : la reprise sur l'acquis en réduit le coût, donc il
en faudra davantage pour que la balance penche. La n° 4 est **franchie**, et la réponse n'a pas été
Temporal.

Ce qui rouvrirait la décision de ce §12, nommé d'avance :

1. **Un état acquis qui ment.** Une reprise repartie d'une sortie fausse, tronquée ou trouée. C'est
   le risque **propre** à (b) : on reconstruit à la main un état que Temporal tiendrait par
   construction. Un seul cas avéré, non imputable à un bug réparable, et la balance change.
2. **Le besoin de reprendre au milieu d'une tâche.** Une tâche de quarante minutes interrompue à la
   trente-huitième est repayée en entier. ⚠ Cette porte **ne mène pas à O4** — Temporal rejoue
   l'activité entière lui aussi — mais vers le **découpage** des tâches ou un point de reprise
   intra-tâche. La confondre avec la porte n° 4 ferait acheter Temporal pour ce qu'il ne donne pas.
3. **Le jour où le magasin coûte plus cher qu'un worker.** Si maintenir l'état acquis revient à
   réécrire à la main la moitié de ce que Temporal fait — reprise, requêtes d'état, exactement-une-
   fois —, la comparaison du §12.4 s'inverse d'elle-même. C'est le critère de bascule honnête, et
   c'est celui qu'il faut surveiller.
4. **Une seconde machine.** Deux postes, ou un hôte partagé, et le magasin devient un état distribué
   à tenir cohérent — ce pour quoi Temporal existe. C'est la porte n° 2 du §8, vue depuis ce §12 :
   elle rouvre les deux décisions à la fois.
