# 29 — Le run, objet de premier plan : note de décision

> Ticket #470. Décision datée du **2026-08-24**, sur `origin/main` à `c69153f`.
>
> **Trois arbitrages, rendus sur une revue d'usage de seize demandes.** ① Le **Kanban quitte le
> tableau de bord** pour la vue d'un run : le run devient une portée d'écran **à côté** du projet,
> qui reste le cadre. ② Le **chat devient la seule porte d'entrée** : « composer » et « valider le
> brief » y déménagent — le brief validé ne disparaît pas, la décision **D5** tient. ③ L'**arrêt
> volontaire solde les runs**, l'accident ne les touche pas : une **cinquième porte** à
> [docs/28 §8](./28-decision-frontiere-execution-run.md), et la seule qui aille dans l'autre sens.
>
> Les trois renversent une décision écrite **et livrée**. Aucun ne l'annule en silence : le §6 dit
> ce que chacun coûte en travail déjà payé, mesuré sur le dépôt.

---

## 1. La revue, et pourquoi elle demande une note

La revue d'usage du **2026-08-24** a porté seize corrections sur le pilotage des runs et la Control
Tower. Confrontées au dépôt — docs, milestones, backlog —, elles se répartissent très inégalement,
et **c'est cette répartition qui appelait une note** plutôt qu'une salve de tickets.

**Sept sont des trous réels** : rien au backlog ne les couvre, elles se découpent le jour même.
**Six sont alignées** ou déjà portées par un ticket ouvert — il n'y avait rien à décider, seulement
à le dire. **Trois renversent une décision documentée et livrée**, et celles-là ne se découpent pas
tant qu'elles ne sont pas tranchées : ouvrir un ticket « retirer le Kanban du tableau de bord »
reviendrait à défaire par le code ce qui a été décidé par écrit, sans que la décision change de
camp. Un ticket exécute une décision ; il n'en rend pas.

D'où l'ordre suivi : **trancher les trois, puis découper** — ce que le §9 récapitule.

## 2. Les seize demandes, réparties

| # | Demande | Ce que le dépôt disait au 2026-08-24 | Sort |
| --- | --- | --- | --- |
| 1 | Un menu « Runs » | `apps/web/lib/navigation.ts` : neuf entrées, aucune | trou → #474 |
| 2 | Chaque run a sa liste de tâches | portée = **projet** (#277/#281), jamais run | trou → #473/#475 |
| 3 | Run **pausable** | ni UI, ni API, ni moteur | trou → #477 |
| 3 | Annuler / relancer par bouton | #467 et #466 ouverts ; #439 livré | aligné |
| 4 | Progression par statut de tâche | tuile **globale** au tableau de bord | trou → #475 |
| **5** | **Kanban par run** | **#248 : le Kanban *est* le tableau de bord** | **arbitrage ①** |
| 6 | Tableau de bord = l'état des runs | découle de 5 | trou → #476 |
| 7 | Lever les limites tours/budget | existent et sont **déjà réglables** (#239) | reporté (§8) |
| 8 | Ne pas perdre les logs au refresh | fil **éphémère** ; `GET /api/journal` figé, non servi | trou → #478 |
| 9 | Un run plus communicatif | #355 ouvert, écrit pour ce motif | aligné |
| 10 | Remonter les erreurs | partiel : `TurnLimitReached`, `BanniereErreurApi` | trou → #479 |
| **11** | **Supprimer l'écran « Composer »** | **Phase 8 entière (#314, neuf lots)** | **arbitrage ②** |
| 12/13 | Le chat, seule surface, avec pièces jointes | #268/#269 ouverts, **sans** pièces jointes | arbitrage ② |
| 14 | Détecter les outils installés sur le poste | #253 sert le **registre du code**, jamais le poste | trou → #487 |
| **15** | **Éteindre Maestro ⇒ aucun run** | **docs/28 §5 : « un run survivra à son API »** | **arbitrage ③** |
| 16 | Aucune fenêtre terminale | le fond **est** la décision ; les fenêtres sont #469 | aligné |

## 3. Arbitrage ① — Le Kanban passe dans le run, le tableau de bord montre les runs

**Ce qui est demandé.** Retirer le Kanban du tableau de bord et en donner un **par run** ; faire du
tableau de bord l'état des runs (demandes 5 et 6), avec pour chaque run sa liste de tâches et sa
progression (demandes 2 et 4).

**Ce que le dépôt disait.** #248 a fait du Kanban l'objet du tableau de bord, et docs/05 l'écrit
**quatre fois** — §1 (« il **est** le tableau de bord »), §2.1 item 5, §2.2 (« Le Kanban n'a pas
d'entrée de menu à lui »), §5 et sa maquette. La borne `max-h-96` de #191 est tombée pour qu'il
prenne toute la hauteur restante.

**Le vrai obstacle n'est pas #248, c'est un fait de modèle.** La portée de tous les écrans est le
**projet** (#277/#281) : le sélecteur du shell, les listes filtrées, les grands livres, la cloche.
Un Kanban par run n'est pas un filtre de plus sur cette portée — c'est une **seconde portée**, à
faire vivre à côté de la première. C'est le vrai prix de la demande, et il est structurel.

> **Verdict : le run s'ajoute au projet, il ne le remplace pas.**
>
> Le **projet** reste le cadre du shell — #277/#281 ne sont pas défaits. Le **run** devient une
> portée d'écran : une entrée de menu « Runs » qui liste les runs du projet actif, une vue par run
> qui porte **son** Kanban, **sa** progression et **son** journal. Le tableau de bord cesse d'être
> un Kanban et devient l'état des runs.

**Pourquoi ce sens.** Un projet est ce **dans** quoi on travaille ; un run est ce qu'on **regarde**
pendant qu'il travaille. Le tableau de bord répond à « où en est-on ? » ([docs/05
§2.1](./05-interface-control-tower.md)) — or ce qui est en cours, à l'instant où on le demande,
c'est un run, et le Kanban de tout le projet mélange ce qui court avec ce qui est fini depuis
trois jours. La demande ne conteste pas la place du Kanban, elle conteste sa **portée**.

**Et le dépôt en portait déjà l'aveu.** [docs/05 §2.7.4](./05-interface-control-tower.md) justifie
l'entrée de menu « Valider le brief » par ceci : « un run suspendu sur son brief ne crée aucune
tâche, donc **ni le Kanban, ni les grands livres, ni le fil d'activité ne le montrent** ». Un run
invisible tant qu'il n'a pas produit de tâche est exactement le symptôme d'un run qui n'est l'objet
d'aucun écran.

**Ce que ça coûte en travail livré.** Peu de code, et c'est le point : #248 fait **+120 / −20 sur
six fichiers** (`ce4e968`). Le composant `Kanban.tsx` n'est pas jeté, il **change de page** ; #191
(l'épure) et #251 (le détail sur place) ne sont pas touchés. Ce qui est réellement renversé est une
**affirmation de doc**, répétée à quatre endroits de docs/05 — et le §2 de ce document existe pour
qu'elle ne soit pas défaite en silence. Le vrai coût est ailleurs, et il est neuf : faire vivre une
seconde portée (#473, l'API qui rend un run porteur de ses tâches).

## 4. Arbitrage ② — Le chat devient la seule porte d'entrée, le brief y déménage

**Ce qui est demandé.** Supprimer l'écran « Composer un objectif » (demande 11) et faire du chat
l'unique surface d'interaction, capable de recevoir fichiers, dossiers, images et liens
(demandes 12/13).

**Ce que le dépôt disait.** La **Phase 8 entière** (#314, neuf lots #315→#323) mène de l'intention
au brief, et deux de ses lots sont des écrans avec leur entrée de menu : #319 « Composer un
objectif » et #322 « Valider le brief ». Leur place au menu est argumentée dans docs/05 §2.7.3 —
« une action qu'on ne trouve pas est une action qui n'existe pas ».

**La demande ne tranche pas la question qu'elle pose.** Supprimer l'écran de composition est clair ;
le **brief validé** l'est moins. La décision **D5** du cadrage #218 dit qu'on ne décompose pas avant
validation humaine, parce qu'une décomposition est payante et qu'un cadrage de travers se paie deux
fois — c'est « le point de contrôle le plus rentable du produit » (docs/05 §2.7.4). Le supprimer
rouvre un jalon go/no-go ; le déménager ne coûte qu'un déménagement.

> **Verdict : le brief migre dans le chat, il ne disparaît pas.**
>
> D5 tient. Ce chantier est un **déménagement** : ce que `/composer` et `/brief` savent faire se
> retrouve **dans le fil** — déposer des sources, lire un rapport d'ingestion, répondre aux
> questions de clarification, lire les sept sections et trancher. Les deux écrans partent quand ils
> n'ont plus rien d'unique, et **pas avant** ; leurs chemins restent servis et redirigés.

**Pourquoi ce sens.** Un point de contrôle ne vaut que s'il est **lu**, et une décision se lit mieux
là où on a la conversation qui l'a produite que dans un écran qu'il faut aller ouvrir. Rien dans D5
n'exige un écran : elle exige un **arrêt** avant décomposition, et un fil arrête aussi bien. En
revanche l'ordre compte — supprimer les entrées de menu **avant** que le fil sache tout faire
laisserait le produit sans porte d'entrée du tout, d'où un lot de retrait (#484) placé en dernier.

**Ce que ça coûte en travail livré.** Les deux écrans, pas la Phase 8. Mesuré : #319 fait
**+2 208 / −21** (`3051427`), dont l'écran lui-même — `app/composer/page.tsx` et
`components/composer/ComposerObjectif.tsx` — **527 lignes**, soit moins d'un quart ; tout le reste
(le rapport d'extraction, les refus de source, `lib/sources.ts`, les contrats, `maestro/sources/apercu.py`,
la route d'aperçu) est **rebranché tel quel** sur le fil. #322 fait **+2 098 / −27** (`943d27c`),
dont la page et la liste plein format **127 lignes** ; les quatre composants de brief — sections,
questions, coût, validation, **763 lignes** — déménagent au lieu d'être réécrits. Et rien du socle
n'est touché : #315 (modèle et résolution des sources), #316 (extraction et rapport), #317 (un
lancement porte ses sources), #318 (schéma de brief), #320/#321 (validation et clarifications) ne
bougent pas d'une ligne. **Deux entrées de menu sur neuf disparaissent ; les capacités restent.**

⚠ **Le chantier prolonge #268/#269, il ne les double pas.** Le chat global — le fil avec
l'orchestration, puis l'écran — est déjà au backlog du milestone « Control Tower v3 — conversation
& intégrations », et ni l'un ni l'autre ne prévoit de pièces jointes ni de sources. C'est
exactement ce que les lots de l'arbitrage ② ajoutent, et c'est pourquoi ils vivent **dans ce
milestone-là**.

## 5. Arbitrage ③ — L'arrêt volontaire solde les runs, l'accident ne les touche pas

**Ce qui est demandé.** Éteindre Maestro doit éteindre ses runs (demande 15) ; un run tourne en
fond, suivi depuis la Control Tower, sans fenêtre de console (demande 16).

**Ce que le dépôt disait.** [docs/28 §5](./28-decision-frontiere-execution-run.md) : « un run
survivra à son API, pas à sa machine ». C'est la **raison d'être** du chantier #441, livré le
2026-08-24 — le jour même de la revue. La note nomme les trois gestes que l'hôte détaché rend
inoffensifs : « fermer la fenêtre, relancer l'API, `--stop` ».

**Un de ces trois gestes n'est pas comme les deux autres, et c'est toute la réconciliation.**
Fermer le navigateur (le chien de garde #149 arrête l'API avec lui) et relancer l'API après une
modification sont des **accidents** : personne n'a demandé d'arrêter le run, et le protéger est
précisément ce que #441 a acheté. `start.sh --stop` — comme quitter l'application le jour où elle
existe — est une **décision**, et elle laisse aujourd'hui tourner des hôtes que plus rien ne
montre : Control Tower éteinte, le run continue de consommer du quota et d'écrire dans le projet,
sans écran pour le suivre ni bouton pour l'arrêter.

> **Verdict : l'arrêt volontaire solde les runs ; l'arrêt subi ne les touche pas.**
>
> Les deux tiennent ensemble et **rien de #441 n'est défait**. Le corollaire change de forme, pas de
> camp : « un run survit à **l'accident**, pas à l'extinction ». La distinction vit du côté qui
> **sait** — `start.sh --stop` sait qu'il arrête exprès, un `SIGTERM` reçu par l'API ne le sait pas :
> elle ne se déduit pas d'un signal.

⚠ **Le verdict tient, son tracé a bougé d'un cas le 2026-08-28** (#700, [docs/28
§11.2](./28-decision-frontiere-execution-run.md)) : **fermer la fenêtre du navigateur est une
décision**, et solde donc les runs. Deux choses l'ont fait changer de camp — #699 a mesuré que la
survie ne préserve plus le run mais lui fait *perdre son historique* (bus Pub/Sub éphémère, journal
alimenté par la seule pompe de l'API), et le chien de garde #149 coupait déjà l'API et l'UI avec la
fenêtre, ce qu'on n'appelle pas un accident. Ce qui reste subi : le **redémarrage**, le plantage, le
`SIGTERM`. La phrase ci-dessus, qui range la fenêtre parmi les accidents, est celle du 2026-08-24.

**Ce n'est aucune des quatre portes du §8 de docs/28, et c'est une cinquième.** Les quatre nommées
d'avance rouvrent toutes la question dans le sens de **plus** de durabilité (Temporal) : un second
run perdu par sommeil machine, l'exécution qui quitte la machine, des runs de plusieurs jours, une
reprise à l'endroit exact. Celle-ci va dans l'autre sens — elle demande **moins** de survie, sur un
geste précis — et elle n'y était pas prévue. Elle s'y ajoute avec son motif ; docs/28 la reçoit
en §11.

**Ce que ça coûte en travail livré : rien.** Aucun des six lots de #441 n'est repris. L'extinction
passe par `_eteindre` (`controltower/hote_detache.py`), qui vise **déjà** le groupe de process et
non l'hôte seul — la leçon de #291, *tuer un parent avant ses enfants fabrique l'orphelin qu'on veut
éviter*. Ce qui manque est une **cause d'arrêt** à faire descendre depuis `start.sh`, et la reprise
au redémarrage par le bouton existant (#439). Le travail est ajouté, pas repris : c'est le seul des
trois arbitrages qui ne renverse rien — il **complète** une décision dont la formulation était trop
large d'un cas.

> **Livré le 2026-08-25 par #486**, et au prix annoncé : aucune ligne des six lots de #441 reprise.
> La porte est `POST /api/extinction`, appelée par `start.sh --stop` avant qu'il ne libère les
> ports ; la cause d'arrêt est `extinction` (`maestro/controltower/causes.py`), sixième code de
> #479 ; et c'est elle, et rien d'autre, qui rend le run reprenable par le bouton existant. Détail
> en [docs/28 §11.1](./28-decision-frontiere-execution-run.md).

## 6. Ce que les trois arbitrages coûtent, récapitulé

Mesuré sur le dépôt, jamais estimé.

| | Ce qui est renversé | Code déjà payé qui est jeté | Code déjà payé qui déménage | Ce qui reste intact |
| --- | --- | --- | --- | --- |
| **①** Kanban | #248 et quatre passages de docs/05 | **rien** | `Kanban.tsx` change de page (#248 : +120 / −20) | #191, #251, #277/#281 (portée projet **doublée**, pas défaite) |
| **②** Chat | deux entrées de menu sur neuf ; docs/05 §2.7.3/§2.7.4 | l'enveloppe des deux écrans — **527 l.** (#319) et **127 l.** (#322) | les composants de brief — **763 l.** — et toute l'ingestion | **D5**, #315, #316, #317, #318, #320, #321, les contrats §6.8/§6.9/§6.10 |
| **③** Extinction | une formulation de docs/28 §5, trop large d'un cas | **rien** | **rien** | les six lots de #441, `_eteindre`, #439, le battement #348 |

**Aucun des trois ne jette de fonctionnalité.** ① déplace un composant, ② déplace des capacités
d'un écran vers un fil, ③ ajoute une distinction. Ce qui est réellement payé est **neuf** : une
seconde portée pour ① (#473), un fil qui sait recevoir des sources pour ② (#482/#483), une cause
d'arrêt pour ③ (#486).

## 7. Les sept trous et leurs tickets

Le quatrième critère de #470 demandait, pour chacun, un ticket **ou** une raison écrite de ne pas en
avoir. Les sept en ont un.

| # | Trou | Ticket | Milestone |
| --- | --- | --- | --- |
| 1 | Un menu « Runs » et la liste des runs du projet actif | **#474** | Le run, objet de premier plan |
| 2 | Chaque run porte ses tâches | **#473** (API) et **#475** (la vue) | Le run, objet de premier plan |
| 3 | Mettre un run en pause, et le reprendre | **#477** | Le run, objet de premier plan |
| 4 | La progression d'un run par statut de tâche | **#475** | Le run, objet de premier plan |
| 6 | Le tableau de bord montre l'état des runs | **#476** | Le run, objet de premier plan |
| 8 | Le journal persisté : les logs survivent au rechargement | **#478** | Le run, objet de premier plan |
| 14 | Détecter les outils et modèles installés sur le poste | **#487** | Control Tower v3 — agents |

Deux d'entre eux méritent un mot, parce qu'ils portent un prix nommé d'avance.

**#477, la pause, est le lot le plus cher du chantier** — et le seul qui touche la boucle
d'exécution. Elle n'existe à aucun étage : ni UI, ni API, ni moteur. `annuler` et `relancer` sont
seuls, et une pause n'est pas une annulation qu'on regretterait.

> **Livré**, et moins cher que prévu côté moteur : la boucle n'a coûté qu'un `await` — une tâche
> prête franchit une **porte** avant d'atteindre l'exécuteur, celle qui est déjà passée n'en a plus
> devant elle ([`maestro/engine/pause.py`](../maestro/engine/pause.py)). Le prix réel était ailleurs,
> dans la **modélisation** : la pause n'est **pas un statut** mais un drapeau à côté du statut
> (`en_pause`), faute de quoi une pause demandée pendant le cadrage aurait été effacée par la demande
> de brief qui suit — porte fermée, et plus rien à l'écran pour la rouvrir. Contrat complet en
> [docs/05 §6.1](./05-interface-control-tower.md). L'ordre emprunte le canal de l'annulation (#444),
> ce qui lui donne gratuitement la survie au redémarrage de l'API ; le run **continue de battre**
> (#348), sans quoi #349 aurait proposé de le relancer depuis son brief — c'est-à-dire de repayer un
> cadrage que la pause avait justement préservé.

**#487, la détection, est un chantier à lui seul, et c'est un choix assumé.** [docs/28
§7](./28-decision-frontiere-execution-run.md) note que le coût d'AionUi vient très majoritairement
de là — résolution de binaire par plateforme × architecture, diagnostics de démarrage, détection
d'incompatibilité de runtime — et conclut : « ce que nous ne payons pas ». Cette conclusion valait
pour **détacher un process**, et elle tient : #441 n'a rien payé de tout cela (docs/28 §10.2). La
demande 14 est autre chose — sonder le poste pour proposer ce qu'il a — et la payer est une
décision, prise ici : **oui, mais après #253** (le catalogue depuis le registre du code), sans quoi
la détection n'aurait aucun endroit où se rendre. D'où sa place au milestone « Control Tower v3 —
agents » plutôt qu'au chantier des runs, et une priorité `moyenne` là où les autres sont `haute`.

## 8. Ce qui n'est pas tranché ici

- **Les limites de tours et de budget (demande 7).** Elles existent et sont **déjà réglables** —
  `plafond_tours` par agent (#239, livré) descend dans le `max_turns` du SDK ; `plafond_cout_usd` et
  `plafond_tokens` se posent au `POST /api/executions`. L'utilisateur a dit y revenir plus tard : il
  n'y avait rien à décider, et inventer un ticket sur une demande explicitement reportée coûterait
  un cadrage à refaire le jour où elle revient pour de bon.
- **Qui pose la checklist d'une tâche** — l'orchestrateur ou l'agent qui la prend. La question est
  réelle (`consigne_detail` est défini, transporté, diffusé, affiché… et **appelé par personne** :
  ses seules occurrences dans `maestro/` sont des commentaires), mais elle appartient au chantier du
  suivi en pipeline (#488) et s'y tranche sur pièces, pas ici.
  ⚠ **Tranchée depuis, au lot #489** : *ossature au plan, complétée et cochée par l'agent*. Le
  motif, les deux options écartées et les règles de réconciliation sont écrits en tête de
  [`maestro/detail_tache.py`](../maestro/detail_tache.py) — c'est là qu'ils vivent, pas ici : la
  décision s'est prise sur pièces, comme prévu, et ce document n'en garde que le renvoi. Le lot
  final (#492) l'a en outre portée dans le **modèle de données**
  ([docs/03 § TASK](./03-modele-de-donnees.md)), là où un lecteur qui découvre `TASK.etapes` se pose
  la question — un motif qui ne vit que dans une docstring ne se trouve pas.
- **La cible visuelle de la Control Tower** (#471) : une recherche, pas une refonte, et rien de ce
  qui est décidé ici n'en dépend.
- **Le Kanban d'un run ou son pipeline** — les deux vues du même objet, dont ce document notait
  qu'elles « ne répondent pas à la même question » sans trancher laquelle occupe l'écran. Renvoyée
  au lot 3 de #488, comme prévu.
  ⚠ **Tranchée depuis, au lot #491** : *les deux coexistent sous une bascule, et le pipeline
  ouvre*. Le motif, les deux options écartées — deux routes, ou le retrait du Kanban — sont écrits
  dans [`apps/web/lib/vuesRun.ts`](../apps/web/lib/vuesRun.ts) et en
  [docs/05 §2.4.4](./05-interface-control-tower.md) ; ce document n'en garde que le renvoi.

## 9. Le chantier — milestones et lots

Trois chantiers, découpés le jour même de la décision, chacun sous le milestone qui lui revient. Les
lots sont mergeables un à un sur `main` ; les tests sont différés au lot final de chaque parent.

| Chantier | Parent | Lots | Milestone |
| --- | --- | --- | --- |
| Le run se liste, s'ouvre, se suit et se pilote | **#472** | #473, #474, #475, #476, #477 ∥, #478 ∥, #479 ∥, #480 (tests + doc) — **les huit livrés** | Le run, objet de premier plan |
| Suivre un run comme un pipeline | **#488** | #489, #490 ∥, #491, #492 (tests + doc) — **les quatre livrés** | Le run, objet de premier plan |
| Le chat, seule porte d'entrée | **#481** | #482, #483, #484, #485 (tests + doc) — **les quatre livrés** | Control Tower v3 — conversation & intégrations |
| L'extinction solde les runs | — (lot seul) | **#486** | Résilience des runs |
| Détecter ce que le poste a | — (lot seul) | **#487** | Control Tower v3 — agents |

**L'ordre entre les chantiers n'est pas libre.** #472 précède #488 : on ne suit pas un run comme un
pipeline avant d'avoir un écran de run où le dessiner. #481 dépend de #268/#269, qu'il prolonge.
#486 et #487 sont indépendants des trois autres et de tout le reste.

**Ce que les lots ∥ ont coûté, une fois, et ce que ça apprend** (constat de #480) : #476 fige la
table des groupes du tableau de bord sur les **quatre** régimes qui existent le jour où il est
écrit, et #477 en crée un **cinquième** — les deux lots sont corrects séparément, mergés dans cet
ordre ils font disparaître de l'écran tout run qu'on suspend. C'est exactement le défaut que #476
avait nommé pour un autre régime et évité à la main. Le lot final l'a rattrapé, ce qui est son
rôle ; la leçon n'est donc pas de renoncer au marqueur ∥ — il a fait gagner trois lots de temps de
mur — mais que **le rattrapage vaut par sa forme** : le garde-fou posé balaie l'énumération du
régime au lieu de nommer les cinq groupes, si bien qu'un sixième ne pourra plus passer en silence.
Une table exhaustive se garde par un test qui la parcourt, jamais par une relecture.

**Deux d'entre eux ne prennent pas de numéro de phase**, et pour la raison déjà écrite pour la vague
front dans [docs/06](./06-roadmap.md) : ce sont des chantiers qui **recouvrent** les phases qu'ils
accompagnent au lieu de s'y insérer. Le numéro 10 reste réservé à « Continuité & multi-projet ».

## 10. Ce qui rouvrirait ces décisions

Nommé d'avance, pour qu'on n'ait pas à re-débattre — même patron que [docs/28
§8](./28-decision-frontiere-execution-run.md).

1. **L'arbitrage ① se rouvre** si la portée « run » se révèle vide la plupart du temps — un projet
   qui n'a jamais qu'un run à la fois n'a pas besoin d'une seconde portée, il a besoin d'un filtre —
   ou si le tableau de bord des runs répond **moins bien** à « où en est-on ? » que le Kanban qu'il
   remplace. Les deux se constatent à l'usage, pas au cadrage.
2. **L'arbitrage ② se rouvre** si le fil se révèle incapable de porter une décision à sept sections
   — un brief qui ne se lit pas dans une conversation est un brief qu'on approuve sans lire, et D5
   serait alors respectée en apparence seulement. Le signal à guetter est le **temps passé** sur un
   brief avant approbation, pas le nombre de briefs approuvés.
3. **L'arbitrage ③ se rouvre** le jour où quelqu'un veut délibérément partir en laissant tourner.
   Ce n'est pas un retour en arrière : la réponse serait une **option d'arrêt** (`--laisser-tourner`)
   sur un geste qui solde par défaut, et non un défaut qui laisse tourner en silence.

Aucune de ces trois conditions n'est remplie au 2026-08-24.
