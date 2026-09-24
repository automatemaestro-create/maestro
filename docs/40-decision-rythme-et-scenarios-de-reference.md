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

1. **Le rail outillage est gelé jusqu'au 2026-10-12.** Il ne reçoit que #1150, #1151, #1152, les
   pannes qui bloquent réellement le travail et, depuis le 2026-09-23, les six tickets du flux d'un
   ticket, #1240 à #1245 (§6).
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
| S2 | Créer une petite application | Elle s'exécute, et **aucune commande n'a été soumise à la personne** (#1226 — le rapport du banc compte ce qu'il a tranché à sa place) |
| S3 | Reprendre un projet existant sans équipe | Le fil propose l'équipe **avant** de dépenser ; validée d'un geste, elle est créée et le run demandé aboutit |
| S4 | « Pourquoi le run a échoué ? » | La réponse nomme la cause réelle, jugée par un modèle, jamais par un lexique (#746) |
| S5 | « Comment j'essaie ce que le run a livré ? » | La fin du run **se raconte dans le fil**, met en lien un fichier du livrable qui **existe sur le disque**, et dit comment l'essayer — jugé par un modèle (#1224) |
| S6 | Le plan appelle un métier que l'équipe n'a pas | Sur un projet d'un seul `dev`, le rôle manquant **se propose dans le fil** qui a lancé le run, avant la première tâche ; accepté, il prend ses tâches et le run aboutit (#1260) |

**S3 a son comportement depuis #1146.** Sur un projet sans agent, le fil ne propose plus de run : il
dit pourquoi (personne pour prendre les tâches) et propose l'équipe que l'analyse du projet appelle
(#1039). On la valide dans la conversation, rôle retiré ou instances ajustées compris. Elle est créée
par la voie de l'étape d'équipe (#1040), puis le fil repropose la demande d'origine, et le run part
sur un clic. Rien n'est recruté sans cette validation, ni pendant un run (docs/31 §3.5). Le parcours
est joué de bout en bout, juge et moteur mis à part, par `tests/test_projet_outille_http.py` (section
⑧). Le banc de #1148 le rejoue avec le vrai modèle.

**L'état qu'un passage laisse se rouvre, sans rien rejouer** (#1164). L'écran peuplé que
regardent la relecture visuelle et les captures n'est plus un scénario factice : c'est l'état réel
qu'un passage du banc a laissé, servi par l'API réelle. `bash scripts/controltower/start.sh
--etat-banc` le rouvre sur la stack de la copie, dans un jeu de données à part (`<espace>.banc`,
`.maestro/banc/`) qui ne touche ni aux données du worktree ni à celles du poste, et **dit son âge**.
`--etat-banc --rejouer[=S2,S4]` le refait à la demande : banc remis à neuf, stack servie, passage
joué au premier plan avec le vrai modèle, état sauvé dans l'atelier du passage. Un passage joué
ailleurs (celui du bouclage, par exemple) ne sauve rien : son état serait mêlé à celui de la stack
qui l'a servi. Le détail est dans `maestro/scenarios/etat.py`.

**S5 porte le dernier mètre** (#1224). Le retex du 2026-09-22 : la personne avait le lien du
dossier — l'annonce de #928 le donne — et écrivait *« on ne me dit pas comment tester, pourtant on
a généré une documentation »*. Le scénario demande un livrable exécutable, puis constate trois
choses dans cet ordre : la fin du run **a écrit** dans le fil (un fait structurel, jamais un
vocabulaire — un message de l'orchestrateur portant le `run_id`, après celui qui a ouvert le run),
le récit met en lien un fichier qui **existe sur le disque** (un chemin cité qui ne mène à rien est
un geste mort), et enfin la question « comment j'essaie ce que tu viens de livrer ? » reçoit une
réponse dont un modèle juge qu'elle dit comment s'y prendre. Le détail est dans docs/05 §2.9.

**S6 porte ce que les tests ne voyaient pas** (#1260). #1227 avait livré la confrontation de
l'équipe au plan, tests verts. Le bouclage du 2026-09-24 l'a rejouée sur la vraie stack : rien dans
le fil, run échoué 0/3. Les tests exerçaient l'hôte en process, et la vraie stack confie ses runs à
l'**hôte détaché** (#446), qui n'avait pas d'arbitre de renfort. Depuis, les deux hôtes prennent le
même chemin : la demande est publiée sur le bus, l'API la relaie dans le fil (docs/28 §3). Le
scénario le rejoue par le fil, sur l'équipe d'un seul développeur. Il constate trois choses dans cet
ordre : la demande paraît, rattachée au run ; l'accepter recrute ce seul rôle ; au moins une tâche
va au rôle recruté. Son motif distingue les deux causes d'un rouge, qui ne se corrigent pas pareil :
un manque **constaté** que personne n'a proposé, ou un plan qui n'a **nommé** aucun métier absent.
La demande dit le besoin de design en toutes lettres. La phrase du bouclage laissait au plan le soin
de juger si un développeur suffit, et il en a jugé ainsi trois fois sur six le 2026-09-24. Ce
jugement est l'autre moitié de #1227, et il se mesure au bouclage : S6 mesure la chaîne qui suit.

**Un jalon produit ne se boucle pas GO avec un scénario rouge** (#1152). Les scénarios ne sont pas
en CI : un passage coûte du vrai modèle (le run du retex a coûté ~10 $). S2, S4, S5 et S6 ne sont
pas déterministes, donc un rouge se rejoue une fois avant d'être cru.

**Le banc simule un utilisateur qui regarde son run, donc il tranche par acte** (#1197). Il
approuve les arbitrages d'action sensible de *son* run et de lui seul (#570) ; il n'en approuvait
qu'**un par tâche**, si bien qu'une tâche demandant deux gestes voyait le second expirer — mesuré
sur le run `5508ebb01cb8`, où `python --version` consommait l'unique approbation et le
`mkdir -p src/depensio` qui suivait restait en attente. C'était un rouge qui ne disait rien du
produit, seulement de l'utilisateur simulé. Il répond désormais à chaque demande, et jamais deux
fois au **même acte** (`maestro.deliberation.cle_acte`) : une commande rejouée à l'identique ne
fabrique donc pas une boucle d'approbations. Ce qui a **libéré les lectures**, lui, est ailleurs et
n'a rien à voir avec le banc : [docs/38 §5.6](./38-decision-outillage-universel-du-projet.md).

## 6. Le gel du rail outillage

Il court jusqu'au **2026-10-12**. Pendant le gel, le rail outillage ne reçoit que :
- les trois allègements #1150, #1151 et #1152, qui **retirent** de la cérémonie au lieu d'en
  ajouter ;
- les pannes qui bloquent réellement le travail ;
- depuis le 2026-09-23, les six tickets du flux d'un ticket (ci-dessous).

Un incident de forge qui ne bloque pas se note, il ne devient pas un chantier.

### L'exception du 2026-09-23 : le flux d'un ticket (#1239)

La personne a demandé si le flux qui traite un ticket était réglé pour la qualité et pour ne pas
perdre de temps. L'analyse de #1239 a répondu par des mesures prises sur 8 runs, 50 tickets, 80 PR
et 104 pipelines. Le flux est réglé pour la sûreté du merge, mais pas pour la qualité du
fonctionnel :
- **le fonctionnel ne s'exerce qu'au jalon.** #1197, #1198, #1205 et #1212 ont été trouvés par le
  banc des scénarios, un à deux jours après le merge de ce qu'ils corrigent ;
- **le temps part dans des vérifications qui trouvent peu.** Le filet local prend 19 % du temps d'un
  ticket et se rejoue souvent après un vert. La relecture visuelle a coûté 6,3 h pour moins de
  0,1 h de corrections. La clôture pèse 36 % du coût.

La personne a retenu les six recommandations, en `prio::haute`, et les a **exclues du gel** :

| Ordre | Ticket | Ce qu'il change |
|---|---|---|
| 1 | #1240 | chaque critère se clôt sur une preuve exercée (test nommé, ou vraie stack et banc), plus sur un fichier du diff |
| 2 | #1241 | une méthode courte pour l'implémentation |
| 3 | #1242 | le filet local ne rejoue pas un vert sur un arbre inchangé, et son périmètre se réduit vraiment |
| 4 | #1243 | la relecture visuelle d'un ticket qui applique regarde l'après et les états nommés, sans seconde stack |
| 5 | #1244 | le temps loggé est mesuré, et la PR ne porte plus de checklist qui redit `merge-mr` |
| 6 | #1245 | les commandes de clôture ne portent que leur règle, et la démonstration part dans la doc |

L'ordre suit les dépendances : #1241 reprend la preuve exercée de #1240, et #1245 comprime en
dernier le texte que les cinq autres ont modifié. Les six écrivent sous `.claude/`, donc ils sont
nés assignés et se traitent en interactif (docs/10 §11.7).

La relecture de code reste écartée ; #1239 ne la rouvre pas. #969 l'a jugée sur mesure (5 bugs
sur 42 visibles dans un diff), et les quatre bugs ci-dessus confirment ce verdict : aucun ne se
lisait dans un diff, tous ont été trouvés en se servant du produit.

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
