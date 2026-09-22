# 40 — Le produit se juge sur ce qu'on lui demande, et le processus s'allège

**Date :** 2026-09-21. **Instruite par :** `/idee` (#1013). **Consignée par :** #1153.
**Jalon :** *Les scénarios de référence — le produit fait ce qu'on lui demande* (échéance 2028-01-30).
**Tickets :** #1148 (le banc des scénarios), #1149 (une action simple s'exécute), #1146 (déjà
ouvert, dans « Avant l'installeur »). Côté outillage : #1150 (un ticket porte ses tests), #1151 (la
cérémonie de conception réservée), #1152 (le bouclage joue les scénarios).

---

## 0. Ce que ce document décide

Quatre choses. La personne les a demandées : elle a répondu « go » à une recommandation qui les
nommait, avec pour objectif *un résultat de qualité et un rythme accéléré*. Rien n'a été tranché à sa
place.

1. **Le rail outillage est gelé jusqu'au 2026-10-12.** Il ne reçoit que #1150, #1151, #1152 et les
   pannes qui bloquent réellement le travail.
2. **Des scénarios de référence, joués avec le vrai modèle, conditionnent le bouclage d'un jalon
   produit.**
3. **Un ticket porte une capacité visible pour l'utilisateur, et ses propres tests.** Il n'y a plus de
   lot final « tests + doc » par défaut.
4. **La cérémonie de conception** (veille, variantes, regard neuf) **est réservée aux tickets qui
   décident d'un écran.**

Et une décision produit, née du même essai : **une action simple sur le projet s'exécute**. Elle ne
se transforme pas en outil à développer (§4).

## 1. D'où vient la demande

Elle est venue le 2026-09-21, pendant un essai réel sur un projet de l'utilisateur. « Vide le dossier
du projet » a coûté 0,36 $ de cadrage et de planification, puis a échoué au routage : le projet,
antérieur à #1042, n'avait aucun agent (#1146). Le fil de l'orchestrateur n'a pas su dire pourquoi.
Puis, dans les mots de la personne :

> « Je trouve qu'on n'avance pas du tout. On fait du sur-place. Peux-tu identifier où est le blocage
> dans notre mode de travail ? »

Le constat, mesuré sur les **158 tickets fermés en septembre**. Le tri se fait par titre et par rail :
c'est un ordre de grandeur, pas une comptabilité.

| Nature | Tickets | Part |
| --- | --- | --- |
| Outillage de la forge (workflow, prompts, CI, relecture visuelle) | 37 | 23 % |
| Finitions d'interface (composeur, ascenseur, tokens, barèmes) | 35 | 22 % |
| « Veille de conception à jouer » | 11 | 7 % |
| Lots « tests + doc », roadmap, cadrage | 16 | 10 % |
| Capacités et bugs du produit | 59 | 37 % |

S'y ajoutent trois faits :
- **Le produit n'a été essayé en vrai qu'une fois en six semaines** : le retex du 2026-09-11 (#854),
  qui a réussi. Dix jours plus tard, le même geste échoue sur un projet plus ancien, et le bilan du
  jalon qui a introduit la cause (« L'équipe sur mesure ») ne l'a pas vu. Il a exercé des projets
  neufs et un projet existant. Il n'a pas lancé de run sur un projet sans équipe.
- **Le processus pèse plus lourd que ce qu'il encadre** : `CLAUDE.md` fait 51 Ko et il est relu à
  chaque session ; `docs/10-workflow-git.md` fait 620 Ko ; les 19 commandes font 305 Ko.
- **Une fonctionnalité d'interface devient couramment 4 à 9 tickets** : parent, lots, veille à jouer,
  variantes, lot final.

L'analyse complète, avec l'existant, les renversements et chaque arbitrage, est le corps de #1153.

## 2. Pourquoi on faisait du sur-place

Trois causes, qui se renforcent :

1. **On mesure des tickets fermés, pas ce que l'utilisateur peut faire.** Tous les filets vérifient
   qu'un ticket fait ce qu'il annonce : CI verte, critères confrontés au diff (#968), relecture
   visuelle (#935), bilan sur pièces (#759). Aucun ne rejoue un parcours. Une régression entre deux
   tickets passe donc sous tous les filets à la fois.
2. **Le processus se nourrit de lui-même.** Chaque incident produit une règle, un test de garde et
   un paragraphe de doc. Près d'un ticket sur quatre modifie la forge. Le règlement grossit plus vite
   que le produit qu'il encadre.
3. **Le poli passe avant la fonction.** Une cinquantaine de tickets de finition et de veille sont
   fermés dans le mois, alors que la promesse de base n'est pas tenue : demander, voir fait, savoir
   pourquoi.

## 3. Ce qui est renversé

| Décision | Où elle vivait | Ce qui la remplace | Ticket |
| --- | --- | --- | --- |
| Les tests différés au **lot final « tests + doc »**, jamais parallèle | [docs/10 §5.1](./10-workflow-git.md), `CLAUDE.md` (Découpage), `/ticket-create` étape 4 — depuis #53 | Chaque ticket, et chaque lot quand un découpage reste nécessaire, livre **ses** tests. Un parent n'existe que si **une capacité** dépasse une session | #1150 |
| La veille proposée ou jouée sur **toute** surface visible non arbitrée | [docs/30 §5.2](./30-cible-visuelle-control-tower.md) (#714), [§5.4](./30-cible-visuelle-control-tower.md) (#934) | Seul un ticket qui **décide** d'un écran (§7.2 de `/design-veille`) a une veille | #1151 |
| La veille **différée en ticket satellite** | [docs/30 §5.3](./30-cible-visuelle-control-tower.md) (#795) | Plus de ticket « Veille de conception à jouer » pour un ticket qui ne décide pas de l'écran | #1151 |
| Le **regard neuf** saisi à la relecture de tout diff d'écran | [docs/30 §5.8](./30-cible-visuelle-control-tower.md) (#980) | Le regard neuf juge les tickets qui décident. Pour les autres, la relecture reste jouée, mais la session la juge elle-même | #1151 |
| Le **plancher de 3 tâches** et « des artefacts, pas des activités » | `maestro/orchestrator/prompt.py` (`MIN_TASKS`, ticket #6), `maestro/orchestrator/playbook.md`, [docs/06 §Phase 0](./06-roadmap.md) | Une action simple sur le projet est **une** tâche qui agit (§4) | #1149 |

## 4. Une action simple s'exécute

Le run `8a15f78f45d3` a décomposé « Vider le dossier du projet en supprimant l'ensemble de son
contenu » en trois tâches :
1. implémenter un utilitaire de vidage ;
2. le tester ;
3. faire valider et exécuter le vidage par un humain.

Le planificateur a suivi ses consignes à la lettre :
- lister ce qui doit **exister**, « des artefacts, pas des activités » ;
- un plancher de 3 tâches ;
- un acte irréversible devient une tâche confiée à un humain.

Les playbooks des agents disent d'ailleurs que « ce que tu laisses dans ce répertoire est le
livrable ».

Ce qui est décidé :
- **Une demande d'action sur le projet** (vider, supprimer, renommer, déplacer, lancer une commande)
  se planifie comme **une** tâche qui agit dans la racine du projet.
- **L'accord donné au cadrage sur un objectif qui nomme l'acte irréversible vaut décision humaine.**
  Aucune tâche « faire valider et exécuter » ne s'y ajoute.

Ce qui ne bouge pas :
- **l'arbitrage sur l'acte** (#582, #583, hook `PreToolUse`) — *renversé en partie par #1198, voir
  ci-dessous* ;
- **le périmètre du projet** : `.git`, `.env`, les secrets et les exclusions déclarées ne sont
  jamais touchés ;
- **le découpage d'un objectif de développement**, qui reste celui d'un lead technique.

### 4bis. L'accord ne s'arrêtait pas au plan — #1198

Le 2026-09-22, le banc des scénarios a joué S1 contre la vraie stack (passage `20260922-100639`,
run `3d4d032fd154`). Le plan était celui que #1149 promettait : **une** tâche qui agit, aucune
tâche « faire valider ». Et pourtant l'acte n'a pas eu lieu. L'équipe proposée posait `Bash` en
`ask`/`humain` — le cran normal d'un projet qui ne déclare aucune commande —, si bien que **chaque
commande** de la tâche émettait une demande de validation : les lectures d'abord, puis le
`rm -rf`. Cinq demandes, une seule tranchée, les autres écartées à 240 s ; à l'échéance de 900 s le
run était « en attente d'arbitrage » et le dossier intact. **S1 rouge.**

Ce que la mesure dit : *« il lève la main au moment de l'acte »* ne voulait pas dire « une fois »,
mais « une fois par appel d'outil ». La validation retirée du plan revenait dans l'exécution,
multipliée.

Ce qui est décidé en plus :
- **l'accord donné au cadrage voyage jusqu'à l'exécution**, par une clé du plan
  (`acte_accorde`, `packages/shared/schemas/task.schema.json`) qui porte l'acte tel que l'objectif
  le nomme. Sur la tâche qui le déclare, l'**outil d'exécution** passe sans redemander personne ;
- **l'arbitrage reste armé pour ce que personne n'a décidé** : toute autre tâche, tout autre outil
  classé `ask`, la liste `deny`, la frontière d'écriture (#839) et le périmètre exclu ;
- **rien ne passe en silence** : chaque appel laisse sa ligne au journal (`:refus-outil`, statut
  `arbitrage_outil`) et son détail **nomme l'acte accordé**.

Ce n'est pas la portée étendue d'une approbation, qui reste un choix de la personne (#1185) : ici
la personne a déjà approuvé, et c'est son accord qu'on cesse de lui redemander.

## 5. Les scénarios de référence

Le retex du 2026-09-11 est la seule vérification qui ait trouvé de vrais défauts. Il était manuel et
n'a été joué qu'une fois. Il devient une commande (#1148), jouée contre la vraie stack et le vrai
modèle, par la porte d'entrée réelle (le fil de l'orchestrateur) :

| | Scénario | Ce qui le rend vert |
| --- | --- | --- |
| S1 | Vider un dossier | Le dossier est vide hors périmètre exclu, sans outil écrit |
| S2 | Créer une petite application | Elle s'exécute |
| S3 | Reprendre un projet existant sans équipe | Le fil propose l'équipe **avant** de dépenser ; validée d'un geste, elle est créée et le run demandé aboutit |
| S4 | « Pourquoi le run a échoué ? » | La réponse nomme la cause réelle, jugée par un modèle, jamais par un lexique (#746) |

**S3 a son comportement depuis #1146.** Sur un projet sans agent, le fil ne propose plus de run : il
dit pourquoi (personne pour prendre les tâches) et propose l'équipe que l'analyse du projet appelle
(#1039). On la valide dans la conversation, rôle retiré ou instances ajustées compris. Elle est créée
par la voie de l'étape d'équipe (#1040), puis le fil repropose la demande d'origine, et le run part
sur un clic. Rien n'est recruté sans cette validation, ni pendant un run (docs/31 §3.5). Le parcours
est joué de bout en bout, juge et moteur mis à part, par `tests/test_projet_outille_http.py` (section
⑧). Le banc de #1148 le rejoue avec le vrai modèle.

**Un jalon produit ne se boucle pas GO avec un scénario rouge** (#1152). Les scénarios ne sont pas
en CI : un passage coûte du vrai modèle (le run du retex a coûté ~10 $). S2 et S4 ne sont pas
déterministes, donc un rouge se rejoue une fois avant d'être cru.

## 6. Le gel du rail outillage

Il court jusqu'au **2026-10-12**. Pendant le gel, le rail outillage ne reçoit que :
- les trois allègements #1150, #1151 et #1152, qui **retirent** de la cérémonie au lieu d'en
  ajouter ;
- les pannes qui bloquent réellement le travail.

Un incident de forge qui ne bloque pas se note, il ne devient pas un chantier.

Deux chantiers sont **différés, pas abandonnés**. Ils passent en `prio::basse` :
- **#1052**, le plan d'un run qui traverse les jalons ;
- **#1129**, la bibliothèque de références et le regard de la personne par jalon. Il **ajoute** de la
  cérémonie : il se rejuge à la levée du gel.

Deux veilles satellites dont les tickets sources étaient fermés, #1141 et #1117, sont abandonnées,
sur décision de la personne.

## 7. Ce qui ne bouge pas

- **#1009** : un ticket qui **décide** d'un écran garde sa veille, ses variantes rendues, le choix du
  regard neuf et sa consignation avant le code. La qualité se décide là, et c'est là qu'elle reste
  outillée.
- **La relecture visuelle** (#935) reste jouée sur tout diff d'écran, avant le filet CI. Seul change
  qui la juge quand le ticket ne décide pas de l'écran.
- **Le merge vérifié** (#417, `merge-mr`), le filet CI, la protection de `main`, les sub-issues et
  leurs marqueurs (`lot::parallele`, `lot::arbitre`).
- **« Le niveau visuel »** (docs/39) : sa décision tient. Son jalon passe simplement derrière celui-ci
  (docs/06).
- **Les lots « tests + doc » déjà découpés** (#1128, #1133, #645) : la règle vaut pour les tickets à
  naître.
