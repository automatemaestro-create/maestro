<!-- documentation: développement -->
# 55 — Le code s'écrit en anglais

**Date :** 2026-11-12. **Instruite par :** `/idee` (#1013), sur un plan **présenté avant toute
écriture** (#1415) et accepté par la personne tel que recommandé (« Oui, crée-les »).
**Consignée par :** #1659, dont le corps porte l'analyse entière, mesures comprises.
**Jalon :** *Le code parle anglais — un nom par concept, paquet par paquet, à comportement constant*
(rail outillage, échéance 2027-09-22).
**Tickets :** le chantier #1649 ; ses lots #1659 (ce document), #1650 (le pilote), puis #1651 à
#1658 ; notes sur #1468, #1469 et #1308.

**Renverse :**
- **le « plus tard » du 2026-10-15**, qui n'avait jamais été écrit dans `docs/`. Ce jour-là, le passage
  à l'anglais avait été validé, mais pour le jour où des contributeurs ou des clients non francophones
  arriveraient ;
- **la convention implicite « les noms du métier en français »**, écrite nulle part et suivie
  partout ;
- [docs/53](./53-decision-le-code-dit-le-present.md) §0.4 et §7, **en partie** : la prose d'un paquet
  se réécrit d'un coup quand son lot de traduction passe.

---

## 0. Ce que ce document décide

1. **Le code de Maestro s'écrit en anglais** : identifiants, docstrings et commentaires, dans
   `maestro/`, `tests/` et `apps/web/`.
2. **Un concept du métier a un seul nom anglais, celui du glossaire**. C'est le chapitre « Les noms
   dans le code » de [docs/08](./08-glossaire.md). Un nom ne se traduit jamais à la volée : un terme
   absent du glossaire y entre avant d'être employé.
3. **Le passage se fait paquet par paquet, à comportement constant**, dans des lots en série. Il ne se
   fait ni d'un coup, ni au fil de l'eau.
4. **Ce que la personne lit reste en français**, et **ce qui voyage ou s'enregistre garde sa valeur**.
5. **Le code neuf s'écrit en anglais dès aujourd'hui**, dans un fichier neuf. Un fichier existant
   garde sa langue jusqu'au lot qui traduit son paquet.

## 1. D'où vient la demande

> *« je pense qu'il faudrait qu'on internationalise le code et les commentaires en anglais, je pense
> que c'est un frein au développement de maestro. peux-tu planifier cela? »*

Le 2026-10-15, la personne avait validé le principe pour plus tard. La méthode était déjà fixée :
`/idee` et une note de décision, un glossaire, un module après l'autre, jamais tout d'un coup, jamais
un ticket orphelin que `/orchestrate` prendrait dans sa file. Le « plus tard » est levé à sa demande.

## 2. Ce que dit la mesure

Relevé du 2026-11-12 :

| Zone | Fichiers | Lignes |
|---|---|---|
| `maestro/` (Python) | 273 | 140 911, dont environ 42 % de prose (docs/53 §2) |
| `tests/` (Python) | 218 | 181 136 |
| `apps/web/` (TS/TSX) | 326 | 115 663 |
| `scripts/` et autres `*.sh` | 38 | 37 178 |

**Le mélange existe déjà.** Les noms du métier sont en français (`EchecTache`, `FrontiereEcriture`,
`JugeLivraison`) et ceux de la technique en anglais (`to_dict`, `subscribe`, `ModelProvider`). Le
même concept porte donc deux noms selon le module qui l'écrit :
- `controltower/events.py` et `controltower/routes/evenements.py` ;
- `telemetry/costs.py` et `controltower/routes/couts.py` ;
- `agents/catalog.py` et `controltower/routes/catalogue.py` ;
- « exécution » et « run », dans la route `/api/executions/{run_id}`.

**Une bonne part de ce qui est nommé en français est un contrat, pas du code** :
- **l'API** : 92 routes (`/api/projets/{id_projet}`, `/api/taches`…) et les clés JSON partagées avec
  `apps/web` (`statut`, `equipe`, `portee`…) ;
- **Redis et les files** : `maestro.taches`, `maestro.evenements`, `maestro.runs:battements`,
  `maestro.boite.*`, et la tâche Celery `maestro.executer_tache` ;
- **Temporal** : `@activity.defn` sans `name=`, où le nom de la fonction (`planifier`…) *est* le type
  d'activité enregistré dans l'historique d'un workflow ;
- **les fichiers d'état du poste** (`etat.json`, `projets.json`, `reprise.json`…) et leurs clés. Elles
  viennent souvent du **nom d'un attribut sérialisé** : renommer le champ renomme la clé, en silence ;
- **ce que le modèle reçoit** : les prompts, et les noms de champ qu'ils demandent et que les parseurs
  relisent.

## 3. Pourquoi

- **Un concept, un nom.** Le mélange actuel donne deux noms au même concept selon le module. C'est
  le défaut que le dépôt refuse ailleurs : deux supports d'un même fait finissent par se
  contredire (#365). Un glossaire unique le règle, quelle que soit la langue, et le passage à
  l'anglais est l'occasion de l'écrire.
- **L'écosystème du code est anglais.** Les bibliothèques, les erreurs, les exemples et les modèles
  qui écrivent le code le sont. Un nom du métier en français oblige le lecteur, humain ou modèle, à
  changer de langue au milieu d'une expression (`self._store.lire_etat()`).
- **La personne le juge comme un frein**, et c'est elle qui porte le développement du produit.

**Le prix est connu, et accepté.** Environ 440 000 lignes de code et de tests sont à relire et à
réécrire, sans capacité nouvelle pour l'utilisateur du produit. C'est un chantier du rail outillage.
Le pilote (#1650) mesure ce que coûte un lot, et le rythme se décide sur cette mesure. Ce document
tient lieu de l'**arbitrage écrit** que #1524 demandera, une fois livré, au-delà de 15 % de tickets
d'outillage.

## 4. Les cinq règles d'un lot

1. **Le glossaire d'abord.** Chaque nom du métier vient du chapitre « Les noms dans le code » de
   docs/08. Un terme nouveau y entre, dans la PR du lot, **avant** d'être employé. Les mots courants
   (lire, chemin, texte) se traduisent sans entrée.
2. **Un fichier a une seule langue.** Un lot traduit un paquet ou un sous-paquet **entier**, avec les
   tests qui le nomment. Un fichier neuf s'écrit en anglais. Un fichier existant garde sa langue
   jusqu'au lot de son paquet, même quand un autre ticket y ajoute du code.
3. **Ce qui voyage ou s'enregistre garde sa valeur.** Cela couvre les routes et les clés JSON de
   l'API, les clés et canaux Redis, les noms de tâches Celery et d'activités Temporal, les fichiers
   d'état et leurs clés, les valeurs énumérées enregistrées (`developpeur`, `humain`…) et ce que le
   modèle reçoit.
   - **La constante change, pas la chaîne** : `TASKS_QUEUE = "maestro.taches"`,
     `@activity.defn(name="planifier")`, ou un alias de sérialisation explicite.
   - **Le lot relève ces noms sur son ticket avant de renommer.**
   - **L'API change dans son propre lot**, Python et TypeScript ensemble.
   - **Un nom enregistré ne change qu'avec une migration**, et aucune n'est prévue.
   - **Un nom neuf qui voyage ou s'enregistre naît en anglais**, ce qui évite la migration de demain
     (#1308).
4. **À comportement constant, prouvé.**
   - Les suites existantes passent sans assertion modifiée, hors les noms.
   - Le schéma OpenAPI est identique, sauf dans le lot du contrat.
   - L'état du banc (`start.sh --etat-banc`) se rouvre avec le nouveau code quand le paquet lit ou
     écrit un état.
   - mypy strict, ruff et la couverture restent verts.
   - La prose traduite **dit le présent** (docs/53 §4). Puisque le lot réécrit toute la prose du
     paquet, c'est le moment où docs/53 s'y applique.
5. **Les noms cités suivent dans la même PR**, et `git blame` saute le lot.
   - Les noms cités se trouvent dans `docs/`, `CLAUDE.md`, `pyproject.toml` (points d'entrée) et
     `.claude/` (ce dernier en interactif, docs/10 §11.7).
   - Le commit de traduction mergé entre dans `.git-blame-ignore-revs`, que GitHub lit nativement. Le
     premier lot qui en a besoin crée ce fichier.

**Pièges connus.** mypy ne voit pas une chaîne d'import, un point d'entrée, un `getattr`, ni le nom
d'un attribut qui devient une clé. Les tests sont le filet, et la règle 3 est la garde.

## 5. Le périmètre

**Dedans** : le code de `maestro/`, `tests/` et `apps/web/`, c'est-à-dire les modules, classes,
fonctions, variables, types TypeScript, noms de tests, docstrings et commentaires.

**Restent en français :**
- **ce que la personne lit** : les textes des écrans, les messages rendus par l'API, les prompts et
  les playbooks des agents, la documentation du produit. Les rendre en anglais serait l'**i18n du
  produit**, un autre chantier (§8) ;
- **l'outillage de la forge** : `scripts/`, `.claude/`, les verbes de `lib.sh`, les variables
  `MAESTRO_*`, les labels (docs/10 §3.2) et le Status. C'est l'interface des commandes de la
  personne, et on ne réécrit pas le bash (docs/49 §4) ;
- **`docs/`, les tickets, les commits, les PR et la prose de `CLAUDE.md`** : c'est la langue de
  travail de la personne.

## 6. Le chantier

Le parent est #1649. Ses lots sont tous en série, sans `lot::parallele` : chacun renomme des noms que
d'autres paquets importent. Un lot se joue quand aucune branche ouverte ne touche son paquet.

| Rang | Lot | Lignes `maestro/` | Condition |
|---|---|---|---|
| 1 | #1659 — ce document, le glossaire et la règle | — | — |
| 2 | #1650 — **pilote** : `messaging`, `queue`, `router`, `durable` | 3 037 | |
| 3 | #1651 — `telemetry`, `sources` | 5 035 | |
| 4 | #1652 — `projets`, `equipe`, `orchestrator` | 8 979 | |
| 5 | #1653 — `scenarios` | 8 338 | |
| 6 | #1654 — `outillage` | 10 294 | |
| 7 | #1655 — `providers` | 6 864 | aucun lot de #1307 en vol |
| 8 | #1656 — `sandbox` | 7 605 | après #1515 |
| 9 | #1657 — `agents` | 12 382 | |
| 10 | #1658 — la racine de `maestro/` et `lanceur` | 11 669 | après #1647 |

Quatre zones **restent à découper après le pilote**, sur la taille de lot qu'il recommande :
- `engine` (15 463 lignes), après le découpage #1469 ;
- `controltower` (51 245 lignes), après #1468, par domaine ;
- **le contrat de l'API**, c'est-à-dire les routes et les clés JSON, Python et TypeScript ensemble ;
- `apps/web/`.

Les découpages #1468 et #1469 écrivent en anglais les modules qu'ils créent, si cette règle est
entrée avant eux (notes sur les deux tickets).

## 7. Ce qui ne bouge pas

- **Le comportement**, prouvé à chaque lot (§4, règle 4).
- **Les écrans, les réponses des agents et l'état enregistré sur un poste.**
- **docs/53 sur le reste** : un pointeur `(#iid)` reste permis, le récit ne l'est pas, et hors d'un
  lot de traduction la prose se réécrit quand on touche le code.
- **Les labels de la forge en français** (docs/10 §3.2), et le bash qui ne se réécrit pas (docs/49 §4).
- **Le cliquet de `CLAUDE.md`** (#1523) : la règle y entre en une ligne, et une démonstration en sort.

## 8. Ce qui est écarté

- **Tout d'un coup, par un outil de renommage global.** Un diff de cette taille ne se relit pas, et
  il entre en conflit avec toute branche en vol. C'était l'objection de docs/53 §7 à « une passe
  d'ensemble sur les docstrings ». Le lot par paquet y répond.
- **Au fil de l'eau** (« on traduit ce qu'on touche »). Cela ne finit jamais, et produit des
  fichiers à deux langues.
- **Une garde automatique de la langue.** Il faudrait un lexique pour juger du texte (#746). La
  garde porte sur les contrats (règles 3 et 4), et la relecture du lot juge les mots.
- **L'i18n du produit** : des écrans en anglais, et des agents qui répondent dans la langue de la
  personne (« les réponses toujours en français », différé par docs/41). C'est un chantier du rail
  produit, sans ticket, qui se rouvre par `/idee` quand un utilisateur non francophone est en vue.
- **L'outillage de la forge, `docs/`, les tickets et `CLAUDE.md` en anglais.** Cela se rouvre quand
  un contributeur non francophone arrive.

## 9. Ce qui rouvrirait la décision

- **Le pilote coûte trop cher pour ce qu'il rend**, chiffres à l'appui (`log-time-mesure`, lignes,
  conflits). Le rythme se rejuge alors avec la personne. Le principe d'un nom par concept, lui, reste.
- **Un nom qui voyage change malgré la règle 3**, et un poste perd un état. C'est l'application qui
  se corrige, par un test qui garde ce nom, pas la règle.
- **Le glossaire se contredit** : deux lots emploient deux noms pour un même concept. On corrige le
  glossaire d'abord, puis le lot fautif.

## 10. Où cette décision est écrite ailleurs

- **[docs/08](./08-glossaire.md)**, chapitre « Les noms dans le code » : le glossaire.
- **[docs/06](./06-roadmap.md)**, section « Le code parle anglais » : le chantier et sa place dans
  la file.
- **Un renvoi ⚠** dans docs/53 §0.4 et §7.
- **[`CLAUDE.md`](../CLAUDE.md)** : la règle, en une ligne qui pointe ce document. La ligne « Le code
  dit le présent » y nomme l'exception du lot de traduction.

## 11. La recette mesurée

Mesurée par le pilote #1650 le 2026-11-13, sur `messaging`, `queue`, `router` et `durable` :
3 037 lignes de `maestro/` et 2 067 lignes de tests.

### 11.1 Ce que le lot a coûté

- **35 minutes de session** entre le démarrage du ticket et l'écriture de cette recette,
  clôture et pipeline non compris (`lib.sh log-time-mesure 1650`). Quatre sous-agents ont traduit
  en parallèle les quatre suites du paquet.
- **La limite est le contexte, pas l'horloge.** Le contexte de la session a été compacté deux fois
  pendant le lot. C'est elle qui avait réécrit les modules, et un module réécrit coûte autant de
  sortie que ses lignes.
- **57 fichiers, environ 2 700 lignes de chaque côté du diff** :

| Partie | Fichiers | Lignes (+ / −) |
|---|---|---|
| les quatre paquets, réécrits en entier | 16 | 1 628 / 1 649 |
| leurs suites, réécrites, et la garde des noms voyageurs | 5 | 912 / 778 |
| les appelants dans d'autres paquets | 15 | 56 / 56 |
| les autres suites, noms seulement | 12 | 68 / 68 |
| les documents qui citaient les noms, glossaire compris | 9 | 30 / 25 |

- **Le comportement est constant** :
  - les 24 suites qui nomment ces paquets passent, sans assertion modifiée hors des noms ;
  - mypy strict et ruff sont verts sur `maestro/` ;
  - le pipeline de la PR rend la couverture.

### 11.2 La recette

Aucun outil de renommage automatique : un tel outil renomme les symboles, mais ne traduit pas la
prose, qui fait environ 42 % des lignes. La recette tient en huit gestes :

1. **Relever les noms voyageurs sur le ticket, avant tout renommage** (règle 3) : canaux et clés
   Redis, tâches Celery, types Temporal, clés JSON, valeurs énumérées.
2. **Écrire la table ancien → nouveau dans l'atelier de la session**, et faire entrer au
   glossaire (docs/08) les termes neufs. C'est cette table que suivent les appelants, les tests et
   les documents.
3. **Réécrire chaque module en entier, prose comprise**, la table en main. Pour un lot plus gros
   que le pilote, la réécriture se confie à des sous-agents, un par module.
4. **Suivre les appelants par des remplacements exacts et comptés.** Un script de l'atelier porte
   chaque remplacement avec son nombre d'occurrences attendu, et refuse le fichier si un compte ne
   tombe pas juste. Un `sed` global sur un nom court (`raison`, `file`, `statut`) toucherait les
   attributs d'autres classes.
5. **Traduire les suites du paquet par des sous-agents en parallèle**, un par suite. Chacun reçoit
   la table et la consigne de ne changer aucune assertion hors des noms. Il rend en retour la table
   des noms de tests qu'il a changés.
6. **Garder les noms voyageurs par une suite qui écrit leurs valeurs en toutes lettres**
   ([`tests/test_wire_names.py`](../tests/test_wire_names.py)). Chaque lot la complète. Une garde
   se prouve en la sabotant : retirer un `name=` doit la faire rougir.
7. **Jouer toutes les suites qui nomment le paquet**, pas seulement les siennes. On les trouve en
   cherchant les imports **et** les classes du paquet. Puis mypy, ruff et `scripts/ci/local.sh`.
8. **Inscrire dans `.git-blame-ignore-revs` le sha du lot précédent** (§11.4).

### 11.3 Les pièges

- **Temporal identifie une activité par le nom de sa fonction**, tant que `@activity.defn` n'a pas
  de `name=`. Une requête, de même, sans `@workflow.query(name=…)`. Renommer la fonction sans
  `name=` rendrait introuvables les activités des workflows en vol.
- **Celery nomme ses tâches à part.** Le nom d'une tâche est explicite (`EXECUTE_TASK_NAME`), mais
  le module listé dans `include=` est un chemin : renommer ce module coupe le worker.
- **Une clé JSON peut naître d'un nom de champ.** `AgentMessage.to_dict` écrivait ses clés d'après
  ses champs. Les champs renommés, les clés s'écrivent désormais en toutes lettres, et un aller-retour
  les garde.
- **Un lot ne renomme que ce qu'il possède.** Les noms des paquets encore en français restent :
  `Task.titre`, `TaskResult.statut`, les arguments nommés du journal. Un paramètre de méthode
  surchargée garde le nom de la classe mère.
- **mypy ne voit pas les lectures d'attribut sur un double non typé.** Huit tests hors des quatre
  suites lisaient des attributs renommés : `decision.raison`, `message.objet`, `boite.file`,
  `mailbox._abonnes`. Seul le passage de toutes les suites qui nomment le paquet les a trouvés.
- **Les numéros de ligne cités dans `docs/` bougent avec la réécriture.** Ils se suivent comme les
  noms.
- **Le sha d'un lot n'existe qu'après son merge**, puisque le merge se fait en squash. Le pilote a
  donc créé `.git-blame-ignore-revs` vide de tout sha.
- **La mesure du temps a besoin de l'instant du démarrage.** Le suivi de #1650 ne portait pas
  `demarre`. `log-time-mesure` comptait alors une nuit entière dans un tour resté ouvert, soit
  10 h 32 au lieu de 35 minutes. Avant de logger son temps, un lot vérifie que son suivi porte cet
  instant ; s'il manque, `lib.sh set-dates <iid> <début> <échéance> <instant>` le repose.

### 11.4 Le fichier de `git blame`

Chaque lot inscrit dans `.git-blame-ignore-revs` le sha mergé du lot qui le précède : une ligne qui
nomme le ticket, puis le sha complet. Le sha du dernier lot du chantier s'inscrit à la clôture du
parent #1649.

### 11.5 La taille de lot recommandée

Un lot tient en une session jusqu'à **environ 6 000 lignes de `maestro/`**, tests en plus, soit
deux fois le pilote. Il faut pour cela que la réécriture des modules soit confiée à des sous-agents
(geste 3). Ce chiffre est un jugement, posé sur une seule mesure : le lot suivant le confirme ou le
corrige. Au-delà, le lot se découpe par sous-paquet au démarrage, comme `/ticket-start` le fait de
tout ticket trop gros.

Pour les zones qui restent à découper :

| Zone | Lignes | Découpage recommandé |
|---|---|---|
| `engine` | 15 463 | trois lots, sur les modules que crée #1469 |
| `controltower` | 51 245 | une dizaine de lots, un par domaine, sur les routeurs de #1468 |
| le contrat de l'API | 92 routes | un lot par domaine de routes, Python et TypeScript dans la même PR |
| `apps/web/` | 115 663 | une vingtaine de lots, par dossier de route |

Sur ce seuil, sept lots du tableau du §6 dépassent la taille d'une session : #1652 à #1658,
de 6 864 à 12 382 lignes. Chacun se rejuge à son démarrage.
