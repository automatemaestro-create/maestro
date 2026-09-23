"""Canal de chat avec l'**orchestration** — le fil global (ticket #268, lot 1 de #244).

Le chat de la Control Tower avait deux canaux, et il leur manquait le principal.
`maestro.controltower.chat` (#84) porte le dialogue avec un **agent exécutant** :
on s'adresse au Développeur, au QA, à propos du travail qu'ils font. `assistance`
(#123) porte les questions sur **l'outil** : où est un réglage, comment trancher
une validation. Aucun des deux ne permettait de dire « ajoute la pagination à la
liste des projets » — c'est-à-dire de s'adresser à **l'orchestration** plutôt qu'à
un exécutant, ce que la revue résume par « communiquer avec les agents sans passer
par les onglets chat de chacun ».

C'est ce canal : un fil `orchestrateur`, non lié à un agent du catalogue, qui
réutilise **toute** l'infrastructure du chat — `ChatStore` pour la persistance,
`ServiceChat` pour l'acheminement et la diffusion `chat.message` (#46), les mêmes
endpoints `/api/chat/{agent}` — avec deux pièces qui lui sont propres :

- `AGENT_ORCHESTRATION` : la fiche de l'orchestration. Comme l'assistant, ce
  n'est **pas** un agent du catalogue : elle n'exécute aucune tâche, n'apparaît
  ni au routage ni au Kanban, et n'a de l'`Agent` que ce dont le chat a besoin.
  Son nom était déjà **réservé** (`maestro.agents.store.NOMS_RESERVES`) avant ce
  lot, et c'est le même mot que l'acteur du cycle de vie d'un run
  (`events.ACTEUR_RUN`) : le fil et le journal parlent du même orchestrateur ;
- `RepondeurOrchestration` : la production de la réponse — et, ce qui le
  distingue de tous les répondeurs d'avant, la possibilité d'**agir**.

## Ouvrir des tâches, c'est ouvrir un run

La Control Tower n'a pas de `POST /api/taches`, et ce n'est pas un manque : une
tâche naît de la **décomposition** d'un objectif par l'orchestrateur, jamais
d'une écriture directe dans la projection (`maestro.controltower.state`, où seul
un événement `tache.statut` crée une carte). Une demande de travail formulée dans
le fil se traite donc en **lançant un run** — `ServiceExecutions.lancer` —, après
quoi les tâches apparaissent d'elles-mêmes au Kanban, avec leur graphe et leur
coût, exactement comme un run lancé depuis l'écran des exécutions.

D'où le `LanceurRun` injecté plutôt qu'un `ServiceExecutions` : le répondeur n'a
besoin que d'« ouvre un run sur cet objectif et dis-moi lequel », ce qui le rend
testable sans moteur et empêche ce module de tirer la couche d'exécution.
**Sans lanceur**, le canal reste conversationnel et le dit — il ne fait jamais
semblant d'avoir lancé quelque chose.

## Ce qui est ouvert est rattaché au fil

La réponse porte le `run_id` du run ouvert (`ReponseChat.run_id`), que le service
recopie sur le `MessageChat` persisté **et** sur l'événement `chat.message`
diffusé. Le fil garde donc le lien vers ce qu'il a déclenché, et un client temps
réel l'apprend sans relire quoi que ce soit.

## …et appartient au projet de la fenêtre (#683)

Le fil est **transverse** (#281) : il parle de l'outil, pas d'un projet, et ni le
message ni sa socket ne portent de périmètre. Mais ce qu'il **ouvre** en a un —
un run appartient à un projet (#222), et toutes les vues de travail sont cadrées
sur le projet actif (#277). Tant que le lanceur ne recevait pas de projet, un run
dicté au fil naissait orphelin : absent de la liste des runs de tout projet,
refusé par la vue de détail, invisible au Kanban et au journal. Le défaut était un
cas de bord tant que « Composer un objectif » existait ; depuis #666, où le chat
est **la seule porte d'entrée**, il valait pour **tous** les runs.

D'où le `projet_id` qui accompagne la demande : il vient de la fenêtre, il n'est
pas deviné, et il ne rend le fil ni cadré ni filtré — il ne touche ni le fil
persisté, ni l'événement diffusé, ni la socket. Deux usages seulement, dans le
répondeur : **rattacher** le run ouvert, et **cadrer** l'aperçu, pour que la
phrase « où en est-on ? » compte ce que l'écran d'à côté peut montrer.

## Le modèle juge, l'utilisateur tranche (#685)

Ce canal a reconnu les demandes de travail par un **lexique** jusqu'à #685 : une
demande commençait, politesses retirées, par un verbe d'une liste. La liste était
l'arbitraire même — « Crée-moi une app » lançait un run, « Génère-moi une app »
n'en lançait aucun —, et #682 a mesuré **quatre** causes de silence dans une seule
phrase réellement envoyée. Le lexique est parti en entier : ni juge, ni voie
rapide, ni repli.

### L'arbitrage de #268, et pourquoi il a été renversé

Il est écrit ici plutôt que laissé au ticket, parce qu'un arbitrage dont on ne
garde que la conclusion se refait dans six mois. #268 avait tranché pour le
lexique sur **quatre** appuis. Trois sont tombés, le quatrième a changé de sens :

- **coût et latence** — un appel modèle par message paraissait cher. Il ne l'est
  plus relativement à rien : les deux autres canaux du **même écran** le paient
  déjà, et celui d'un agent exécutant passait par `RepondeurModele` pendant que
  l'orchestrateur passait par une expression régulière ;
- **reproductibilité** — elle est acquise autrement, par le point d'injection
  `orchestration_repondeur` de `create_app`, sur lequel toute la suite du canal
  s'appuie sans réseau ni authentification. Et l'argument se retournait : un
  lexique n'est reproductible que dans un sens inutile — il se trompe **de façon
  reproductible** ;
- **« le vrai raisonnement a lieu dans le run »** — la décomposition est déjà un
  appel modèle *à l'intérieur* du run. Le lexique était donc exactement ce qui
  empêchait une demande légitime d'**atteindre** la partie intelligente ;
- **l'asymétrie des erreurs** — ne pas reconnaître coûte une reformulation,
  reconnaître à tort lance un run. Celui-là était juste, et c'est le seul. Mais
  le lexique n'achetait pas de la *prudence*, il achetait de l'*arbitraire* :
  même intention, verdict opposé selon le verbe employé.

Ce qui a réglé le quatrième n'est donc pas une meilleure liste, c'est la seconde
décision du 2026-08-28 — **tout run passe par un accord explicite**. La
validation systématique **dissout** l'asymétrie : un faux positif ne coûte plus
un run mais un « non ». C'est elle, et rien d'autre, qui autorise le juge à être
*large* là où le lexique devait être timide ; les deux moitiés du chantier ne
sont pas séparables, et un juge libéral sans la validation serait le pire des
trois régimes.

### Le précédent du moteur : le déclencheur passe du texte à l'acte

Le moteur a fait ce chemin le premier, et c'est la même leçon. `engine.guardrails`
classait une tâche « sensible » par **radicaux** (`deploi`, `supprim`,
`destructi`) trouvés dans son titre et sa description ; **#585** l'a désarmé
(`mots_sensibles` vide par défaut) sur un motif **mesuré** en **#568** — le mot
venait du *brief* et se propageait à toutes les descriptions issues de la
décomposition, si bien qu'un objectif demandant « une sous-commande **supprimer**
une note » rendait **3 tâches sur 3** sensibles, « Rédiger le README » comprise.
La docstring qui en est restée vaut ici mot pour mot :

> Développer une fonction de suppression n'est pas exécuter une suppression.

Ce qui l'a remplacé n'est pas une liste mieux tenue mais **deux canaux de
jugement** (`maestro.providers.arbitrage`, chantier **#573**) : l'agent lève la
main — l'outil MCP `demander_arbitrage(raison)`, **#582** — et l'acte est
suspendu au moment où il a lieu — hook `PreToolUse`, **#583**. Le déclencheur a
été déplacé **du texte vers l'acte**.

Le chat global en est la **seconde application**, et le parallèle est exact : ici
aussi le texte cesse d'être ce qui déclenche. Ce qui ouvre le run est l'**accord**
de l'utilisateur — un acte —, et le texte n'est plus qu'une entrée soumise au
jugement. La différence tient à qui arbitre : là-bas c'est l'humain qui suspend
l'acte d'un agent, ici c'est l'humain qui autorise celui du canal.

### Ce que le canal fait à la place

Ce qui remplace le lexique tient en **un appel modèle par message**, qui rend d'un
coup le texte de la réponse **et** le verdict (`_Verdict`) :

- **proposition** — le dernier message est une demande de travail. Le canal
  reformule l'objectif qu'il enverrait et demande l'accord. **Rien n'est ouvert à
  cet instant** ;
- **accord** — le dernier message approuve une proposition faite juste avant dans
  ce fil. Le run part alors, sur l'objectif **tel qu'il a été montré** ;
- **échange** — tout le reste : question, demande d'état, salutation, refus,
  message obscur. Rien ne s'ouvre.

Deux propriétés portent tout le reste :

**Aucun run sans accord explicite.** Un refus n'ouvre rien, et le **silence n'est
pas un accord** : une proposition sans réponse n'ouvre rien, parce que le run
n'est ouvert que sur le verdict `accord` d'un **message qui arrive**. La
propriété est structurelle et non gardée par un `if` — il n'existe qu'un chemin
vers le lanceur, et il part d'un verdict, qu'aucun silence ne produit. Rien
n'est mis « en attente » entre deux tours, ce qui ferait du message suivant, quel
qu'il soit, un accord par ricochet.

**Le fil est la seule mémoire.** Le répondeur reçoit le fil complet, donc sa
propre proposition et la réponse de l'utilisateur : rien à stocker à côté, aucun
état de session, aucun second lexique pour lire « oui » / « vas-y » / « plutôt
pas » — juger l'accord est du même ordre que juger la demande, et c'est le même
appel qui le fait.

Et l'objectif lancé est **celui que le modèle a recopié de sa proposition**, pas
le dernier message : `_ouvrir_un_run` ne connaît que `_Verdict.objectif`, si bien
qu'un « oui » ne peut structurellement pas partir comme objectif de run. On a
écarté de le **vérifier** contre le fil (chercher la reformulation dans les
messages précédents) : ce serait un second juge, en expression régulière, juste
après en avoir retiré un.

### Agir sur le projet est une demande de travail (#1205)

Le cadre présentait l'équipe comme cinq métiers (« Développeur, QA, DevOps, BDD,
Design »), et tous ses exemples de proposition étaient des demandes de
développement. Le juge en concluait que Maestro « sert à ouvrir des runs de
développement » et **refusait** « Vide le dossier de ce projet » en renvoyant
l'utilisateur le faire lui-même : 1 proposition sur 12 tirages du même message le
2026-09-22, alors que la décomposition sait planifier une action depuis #1149
(section « Agir » du playbook) et l'exécution la laisser passer depuis #1198.
C'est une bride au sens de docs/41, pas un garde-fou.

Le cadre dit donc ce que l'équipe **sait faire** plutôt que ce qu'elle **est** :
des agents qui écrivent et qui agissent dans le dossier du projet. Il nomme les
actions parmi les demandes de travail, interdit le refus « étranger à Maestro »
pour ce qui se fait dans ce dossier, et fait d'une demande irréversible une
proposition qui le dit, jamais un refus : c'est l'accord de l'utilisateur, pas le
juge, qui décide si une demande mérite un run. Le « dans le doute » de l'échange
ne vaut plus que pour l'accord, où il protège quelque chose — ce qu'on ne comprend
pas n'ouvre rien ; sur une demande il faisait taire une proposition, qui n'ouvre
rien non plus. Mesuré sur le fournisseur du poste : 12 sur 12 après, et les
questions et demandes d'état restent des échanges.

## La proposition sort de la phrase, et se répond d'un geste (#943)

Jusqu'ici la proposition n'existait que dans le **texte** de la réponse : « Je
lance ? », sans rien qui la désigne. Un écran n'a donc rien pu en faire — pas de
bouton dans le fil, et le panneau « Cadrage en attente » affirmant « aucun » au
moment même où la question était posée (retex du 2026-09-11, constat G10). Deux
surfaces qui se contredisent lisent deux endroits ; celle qui avait raison
lisait le seul qui existait.

La réponse porte donc l'objectif proposé (`ReponseChat.proposition`), jusqu'au
message persisté et diffusé, comme `run_id` porte déjà ce qu'elle a ouvert. Et
la décision revient par `trancher_cadrage`, **sans repasser par le juge** :

- un accord au bouton n'est pas un texte à reconnaître, c'est un acte. Le lui
  faire retraverser paierait un appel modèle pour rejuger une décision déjà
  prise, et pourrait rendre autre chose qu'un accord sur une décision qui, elle,
  est certaine ;
- un objectif **amendé** ne survivrait pas au tour : le contrat ci-dessous
  demande au juge, sur `accord`, de recopier *mot pour mot* la proposition qu'il
  a faite. L'amendement serait silencieusement remplacé par l'original — c'est
  ce qui rend ce chemin nécessaire, et non simplement économique.

La propriété que #685 a payée ne bouge pas : **aucun run sans accord explicite**.
Un bouton est l'accord le plus explicite qu'on puisse recevoir ; ce qui a été
retiré est le lexique qui *devinait* un accord, jamais l'exigence d'en avoir un.

Un verdict **illisible vaut un échange** : le texte du modèle est rendu tel quel
et rien ne s'ouvre. Une réponse hors contrat coûte ainsi une reformulation, jamais
un run — et jamais non plus un 502 sur une conversation que le modèle a pourtant
tenue.

## Et quand le juge est injoignable, on le dit (#686)

Le lot précédent a fait du modèle le seul juge de ce canal ; il laissait ouverte
la question que cette décision pose : **que fait la porte d'entrée quand ce juge
ne répond pas ?** La réponse est celle de #268, étendue d'un cran — un empêchement
**ne lève pas, il se raconte dans le fil**. Une exception deviendrait ici une
`ReponseIndisponible`, donc un 502 sans trace, sur la seule porte d'entrée du
produit depuis #666 : l'auteur verrait sa demande partir et rien revenir.

Le canal annonce donc qu'il ne peut pas juger, **en n'ouvrant ni ne proposant
rien**. Trois choses tiennent ensemble.

**La cause est nommée, et sa famille avec elle.** Un fournisseur muet et un
fournisseur absent ne se réparent pas de la même façon, et l'utilisateur d'une
Control Tower locale est aussi celui qui répare : l'un se réessaie, l'autre se
configure, et les confondre fait renvoyer dix fois un message que rien n'attend.
La famille se lit à **l'endroit** de l'échec, jamais à son texte : ce qui casse en
*résolvant* le fournisseur (`provider_from_settings`) est un réglage — rien n'est
encore parti sur le réseau —, ce qui casse en *appelant* `generate` est une
indisponibilité. C'est la règle de `controltower.causes` (« la classification est
un `isinstance`, pas une lecture de texte ») tenue d'un cran plus haut : ici c'est
la **structure** qui classe, et aucune chaîne n'est examinée.

**Aucun lexique ne prend le relais.** Le lexique retiré au lot 1 ne revient pas
par la porte de service, et c'est pourquoi la phrase est **la même quel que soit
le dernier message** : reconnaître qu'un « oui » était un accord demanderait
précisément le juge qui manque. Un juge de secours moins bon que le titulaire,
activé quand personne ne regarde, est la pire des combinaisons — il proposerait
des runs sur les seules formulations qu'il sait reconnaître, et tairait les
autres.

**La demande, elle, est acquise.** `ServiceChat` persiste et diffuse le message
d'utilisateur **avant** d'appeler le répondeur : ce qui est indisponible est la
réponse, jamais la demande. Le fil garde donc le texte écrit *et* la phrase qui
dit pourquoi rien n'a suivi — y compris quand le fournisseur tombe *entre* la
proposition et l'accord, cas où le « oui » reste au fil sans rien ouvrir ni se
perdre en silence.

## Un projet sans équipe se voit proposer la sienne (#1146)

Un run ouvert sur un projet **sans agent** échoue toujours, et tard : cadrage et
plan payés (0,36 $ sur l'essai du 2026-09-21), puis chaque tâche en repli « à
assigner » — personne pour la prendre. Or Maestro **sait** proposer une équipe
(#1039) et la créer (#1040) ; la capacité n'était branchée que dans le parcours
de création d'un projet, qu'un projet antérieur ne repasse jamais.

Le canal regarde donc l'équipe du projet de la fenêtre **avant** de proposer un
run, et au moment d'en ouvrir un. Quand il n'y a personne, il ne propose pas le
run : il propose l'**équipe**, dit pourquoi, et attend qu'on la valide
(`ReponseChat.recrutement`). Trois propriétés :

- **la sonde lit ce que le routeur lira** — `EquipeDuProjet`, câblée sur
  `catalogue_du_projet`, la règle unique de l'exécuteur et de la boucle. Elle ne
  tranche que sur « il n'y a personne » : « je ne sais pas » (aucun projet,
  dépôt illisible, projet inconnu) laisse le canal tel qu'avant ce lot, parce
  qu'une sonde aveugle qui bloquerait les runs serait une bride ;
- **rien n'est recruté sans validation** — la demande n'est qu'une demande, et
  l'équipe n'est créée que par le geste (`recruter`), par la voie de #1040
  (`RecruteurEquipe`, câblé sur `ServiceEquipe.creer`). Et rien pendant un run
  (docs/31 §3.5) : le canal agit entre deux runs, jamais dans l'un ;
- **la demande d'origine n'est pas perdue** — elle voyage sur la demande de
  recrutement (`DemandeRecrutement.objectif`), et une fois l'équipe créée le
  canal la **repropose**, sans nouvel accord deviné : le run part sur un clic,
  comme tout run.

La garde est posée à **deux** endroits, et c'est la même : au verdict (une
proposition, ou un accord tapé) et au geste de cadrage. Le second couvre l'équipe
retirée entre la proposition et le clic — rare, et c'est justement le cas qu'une
seule garde laisserait passer.

## Le fil sait ce que ses runs ont fait (#1157)

`apercu_de` **compte** — « 1 run en cours, 3 tâches suivies » —, et c'était tout
ce que le juge recevait de la projection. À « pourquoi le run a échoué ? », il
n'avait donc rien : le prompt lui demandait d'envoyer vers la page Runs, et c'est
ce qu'il a fait (essai réel du 2026-09-21 sur `p1`, run `8a15f78f45d3` — « Je
n'ai pas cette information sous les yeux… »). La cause était pourtant écrite en
clair à deux pas, sur chacune des tâches du run : « aucun agent dans ce catalogue
— l'équipe reste à recruter ».

`faits_des_runs` est la **seconde lecture**, à côté de l'aperçu et jamais à sa
place : les runs que **ce fil** a ouverts (rattachés par `MessageChat.run_id`,
#268) puis ceux du projet de la fenêtre, du plus récent au plus ancien, chacun
avec son statut, sa cause d'arrêt, l'issue écrite par son dernier événement de
cycle de vie, et **chaque tâche avec son détail**. Les deux lectures ne répondent
pas à la même question — « qu'est-ce qui tourne ? » et « qu'est-ce qui s'est
passé ? » —, et fondre la seconde dans la phrase de la première rendrait une
phrase illisible pour tuer un compteur qui marche.

Trois choses tiennent ensemble.

**Le détail se lit sur les événements du run, pas sur la tâche.** `EtatTache` ne
porte aucune erreur : ce qu'une tâche échouée a à dire voyage dans le `detail` de
son `tache.statut`, où `bridge` recopie son `erreur`. C'est exactement ce qui
séparait le fil de la vérité le 2026-09-21 — l'issue du run disait « 0/3 tâche(s)
réussie(s) », c'est-à-dire un **décompte** ; les tâches, elles, disaient pourquoi.

**La lecture est bornée, et sa borne se dit.** Trois runs, douze tâches par run,
trois cents caractères par détail. Ce qui dépasse est **compté dans le texte**
(« 38 autres tâches non montrées ici », « … (tronqué) ») plutôt que coupé en
silence : un juge qui ne sait pas qu'il lui manque quelque chose conclut sur un
run qu'il croit connaître en entier, et cette certitude-là ne se rattrape plus.

**Le prompt ne renvoie plus vers un écran ce qu'il a sous les yeux**, et garde
l'aveu pour ce qui manque vraiment — au-delà de la borne, ou hors de ce que la
projection sait. L'honnêteté de #686 ne change pas de camp : elle se déplace de
« je n'ai pas cette information » vers « je ne vois que les trois derniers runs ».

## La réponse s'écrit pendant qu'elle est jugée (#1222)

Ce canal rendait un seul objet JSON `{verdict, objectif, reponse}`, et c'est ce
qui le tenait muet jusqu'au dernier mot : **la phrase à afficher vivait dans une
structure qu'il fallait avoir entière pour la lire**. « … répond… » couvrait donc
toute la génération, là où le chat d'un agent écrit en direct depuis #693 — même
transport, même écran, même `Redaction`. Le retex du 2026-09-22 l'a dit en une
phrase : *« je veux que la réponse s'affiche au fur et à mesure »*.

Ce qui a changé n'est **pas** le nombre d'appels, ni qui juge, ni quand : c'est
l'**ordre des deux moitiés**. Le modèle écrit d'abord sa réponse, en clair, puis
une dernière ligne `%%MAESTRO%% {"verdict": …, "objectif": …}` que le canal retire
avant d'afficher (`_MARQUEUR_VERDICT`, `_LectureDuFlux`). Un seul appel rend
toujours les deux — séparer « juger » de « répondre » en ferait deux, dont le
second devrait redire au premier ce qu'il vient de décider (`_Verdict`) —, mais le
verdict ne retient plus la phrase, parce qu'il la suit.

Deux voies ont été écartées, et pour la même raison :

- **un appel d'outil** pour porter l'intention. C'est la forme la plus propre, et
  le lot 2 (#1223) donnera des outils à ce même appel : mais `generate`/
  `generate_stream` sont *texte seul* (`tools=[]`), l'exécution outillée passe par
  `run_agent`, et `run_agent` est **refusée** par le fournisseur compatible OpenAI
  (`UnsupportedCapability`). Y faire passer le fil aurait rendu le critère du
  streaming inatteignable sur un Ollama local, c'est-à-dire sur le seul endpoint
  que quelqu'un fait tourner chez lui ;
- **un second appel** qui jugerait après coup ce que le premier a écrit. Deux fois
  le quota, deux occasions de se contredire, et un verdict rendu sur un texte au
  lieu d'une intention.

⚠ **Le repli tient tout le reste.** Une réponse dont le premier caractère non blanc
est `{` ou un bloc de code est lue comme l'**ancien** contrat : rien n'est publié
au fil de l'eau, et c'est la `reponse` de l'objet qui s'affiche — exactement le fil
d'avant ce lot. Un modèle qui n'a pas suivi la consigne dégrade donc le direct, il
ne casse jamais le fil, et n'affiche jamais de JSON à l'utilisateur. Préfacé d'une
phrase, l'objet est encore lu, mais pour son **verdict seul** : ce qui est déjà à
l'écran n'y est pas repris, et une demande approuvée continue d'ouvrir son run.

## Il va voir, et ce qu'il voit se voit (#1223)

Le lot précédent a fait parler ce canal en direct ; celui-ci lui donne de quoi
**savoir**. Jusqu'ici il répondait sur un contexte figé — des compteurs, trois
runs, des détails coupés à 300 caractères — et il le disait honnêtement :
*« le détail que j'ai ici est tronqué […] un README y a probablement été créé »*
(2026-09-22, projet `p1`). Le fichier était à deux pas ; personne n'était allé le
voir.

**Un tour de lecture précède le jugement.** `_consulter` demande au modèle, au
contrat étroit de `_PROMPT_CONSULTATION`, ce qu'il a besoin de lire ; ce qu'il
nomme est exécuté par `maestro.controltower.consultation` — quatre verbes en
lecture seule, bornés à la racine du projet, secrets exclus — puis rendu au juge
dans son prompt. Deux tours au plus (`_TOURS_DE_LECTURE`), parce que le second
sert la piste que le premier ouvre : lister, puis lire le README qu'on y a vu.

**Ce qu'il lit se voit pendant qu'il répond.** Chaque lecture devient une
`EtapeFil` publiée sur le canal d'étapes (#1223, `chat.Etapeur`) **avant** le
premier mot de la réponse, puis persistée sur le message (`ReponseChat.etapes`) :
l'ordre est celui des choses, et c'est lui qui distingue « il a regardé » d'« il
a l'air sûr de lui ».

**Trois blocs de contexte s'ajoutent**, et ils ne se demandent pas — ils sont
toujours là (`_Contexte`) : l'**équipe** réelle du projet (rôles, agents,
modèles), ce qui **attend** quelqu'un avec son contenu (validations, questions),
et les faits des runs de #1157. La frontière entre les deux régimes est une
question de taille et de certitude : ce qui est petit, borné et toujours utile
entre dans le prompt ; ce qui est vaste et dépend de la question se **demande**.

**Deux voies ont été écartées.**

- **Les appels d'outil natifs** — la forme la plus propre, et celle que #1222
  avait déjà écartée pour le verdict : `generate`/`generate_stream` sont texte
  seul (`tools=[]`), l'exécution outillée passe par `run_agent`, et `run_agent`
  est **refusée** par le fournisseur compatible OpenAI. Le risque était nommé au
  ticket le 2026-09-23 — *« sur ce fournisseur, les lectures de ce lot
  n'existent pas »* — et le protocole textuel (`%%LIRE%%`) le referme au lieu de
  l'arbitrer : il traverse **tous** les fournisseurs, parce qu'il ne demande rien
  de plus qu'une chaîne de caractères.
- **Tout mettre dans le contexte** — livrables, arborescence, détails entiers.
  Un prompt qui grossit à chaque message, payé à chaque « bonjour », pour une
  matière dont on ne sait pas si elle sert. Le tour de lecture paie un appel
  court quand il sert, et rien quand il n'y a rien à lire.

**Le prix est assumé et il est écrit** : un appel modèle de plus par message.
Court dans les deux sens — le prompt est celui du juge, la réponse tient en une
ligne —, et il ne peut jamais coûter la réponse : sans fournisseur, hors contrat,
lecture en échec, on rend ce qu'on a et le juge répond avec le contexte seul,
c'est-à-dire exactement le fil d'avant ce lot.

## Ce qui est gardé, et par quoi (#688)

`tests/test_chat_global.py` tient le tout, sans réseau, sans modèle et sans
moteur. Trois choses y méritent d'être connues avant d'y toucher :

- le **banc de #682 est joué cause par cause**, chaque formulation portant en
  identifiant de cas la raison exacte pour laquelle le lexique la faisait taire
  (`verbe-hors-liste`, `amorce-sans-s`, `subordonnee-que-tu`,
  `pronom-objet-intercale`, `subordonnee-et-conjugaison`). Les quatre dernières
  causes tenaient **ensemble** dans la phrase réellement envoyée : les séparer est
  ce qui empêche qu'un correctif n'en traite qu'une et que le banc passe quand
  même. Ses témoins négatifs (`comment ajouter une page ?`, `où en sont les
  runs ?`, `merci`) sont la moitié qui interdit de le rendre vert en proposant un
  run sur tout ;
- le **protocole d'accord est joué en deux tours** sur un seul répondeur, seule
  forme où la décision du 2026-08-28 est visible : proposition → rien, puis
  accord → run. Un test à verdict unique ne voit jamais l'intervalle entre les
  deux, qui est précisément l'endroit où une régression se logerait ;
- l'absence du lexique est gardée **structurellement**, sur l'arbre syntaxique et
  jamais par un `grep` — ce module *doit* citer `_AMORCES` et `_VERBES_TRAVAIL`
  pour raconter leur retrait, et une garde textuelle se déclencherait sur la
  docstring même qui les documente. Elle porte sur les identifiants **Python**,
  ce qui écarte du même geste les amorces de conversation d'`apps/web`
  (`amorcesDuProjet`, `lib/orchestration`), qui sont les propositions de message
  d'un fil vide et n'ont jamais été ce lexique.

La moitié que ces tests **n'atteignent pas** est nommée plutôt que masquée : la
qualité du jugement. Le juge y est un double, donc « cette phrase est-elle une
demande de travail ? » n'y est pas posée — ce qui est tenu est qu'elle *atteint*
le juge, que le verdict décide seul, et qu'aucun run ne part sans accord. Le
reste relève du prompt, et se mesure en usage.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.agents.playbook_du_code import registre
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.causes import (
    CAUSE_ANNULATION,
    CAUSE_EXTINCTION,
    CAUSE_HOTE,
    CAUSE_LIMITE_USAGE,
    CAUSE_PLAFOND_COUT,
    CAUSE_PLAFOND_TOURS,
    cause_lisible,
)
from maestro.controltower.chat import (
    UTILISATEUR,
    DemandeRecrutement,
    EtapeFil,
    Etapeur,
    Incrementeur,
    MessageChat,
    Redaction,
    RepondeurChat,
    ReponseChat,
    transcription,
)
from maestro.controltower.consultation import (
    LECTURES_PAR_TOUR,
    Demande,
    Lecture,
    catalogue,
    demandes_de,
)
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_TACHE_STATUT,
    ROLE_RUN,
)
from maestro.controltower.outillage import ConducteurOutillage
from maestro.controltower.portee import PorteeProjet, PorteeRun

# Les statuts de tâche viennent de leurs **deux** définitions, comme partout
# ailleurs dans la projection (`progression`, `state`) : quatre que le moteur
# émet, quatre que la machine à états nomme sans que le moteur les émette encore
# (docs/03 §3). Les prendre en bloc à `progression`, qui les réunit déjà, serait
# un ré-export implicite — ce que le typage du dépôt refuse.
from maestro.controltower.progression import (
    STATUT_ASSIGNEE,
    STATUT_BACKLOG,
    STATUT_EN_ATTENTE_VALIDATION,
    STATUT_PRETE,
)
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ControlTowerState,
    EtatExecution,
    EtatQuestion,
    EtatTache,
    EtatValidation,
)
from maestro.engine.executor import (
    STATUT_BLOQUEE,
    STATUT_ECHEC,
    STATUT_EN_COURS,
    STATUT_TERMINEE,
)
from maestro.equipe import RoleValide
from maestro.outillage.questionnaire import QuestionOutillage
from maestro.providers.base import ModelProvider

if TYPE_CHECKING:  # pragma: no cover - typage seul, cf. `SondeDuPoste`
    from maestro.poste import RapportSonde

#: Le nom du fil global — la clé de stockage (`core/chat/orchestrateur.jsonl`), le
#: segment d'URL des endpoints `/api/chat/{agent}` et le `agent` des événements
#: `chat.message` que l'UI filtre. Il vaut `events.ACTEUR_RUN` à dessein : c'est
#: déjà sous ce nom que le cycle de vie d'un run est consigné, et deux
#: orchestrateurs sur le même écran seraient un de trop.
NOM_ORCHESTRATION = ACTEUR_RUN

#: Son rôle affiché, le même que celui du journal (`events.ROLE_RUN`).
ROLE_ORCHESTRATION = ROLE_RUN

#: Les trois verdicts que le modèle rend **avec** sa réponse (voir le module).
#: `VERDICT_ECHANGE` garde le mot de l'`INTENTION_ECHANGE` d'avant #685 : c'est la
#: même situation — rien ne s'ouvre — et le vocabulaire du fil n'a pas de raison
#: de changer parce que le juge a changé.
VERDICT_PROPOSITION = "proposition"
VERDICT_ACCORD = "accord"
VERDICT_ECHANGE = "echange"

#: Les seuls verdicts admis. Tout autre mot — comme toute réponse hors contrat —
#: retombe sur `VERDICT_ECHANGE` : la liste est **blanche**, jamais noire, parce
#: qu'on ne maîtrise pas ce qu'un modèle peut écrire dans ce champ et qu'un mot
#: inattendu ne doit jamais pouvoir valoir un accord.
VERDICTS = frozenset({VERDICT_PROPOSITION, VERDICT_ACCORD, VERDICT_ECHANGE})

#: Le **marqueur de fin** : ce qui sépare la réponse affichée de la décision
#: machine (#1222). Tout ce qui le précède est du texte pour l'utilisateur, tout
#: ce qui le suit est l'objet JSON du verdict — et rien de tout cela ne s'affiche.
#:
#: Il existe parce que l'ancien contrat — un seul objet JSON `{verdict, objectif,
#: reponse}` — ne pouvait pas s'afficher au fur et à mesure : la phrase à montrer
#: vivait *dans* une structure qu'il fallait avoir entière pour la lire. Le
#: verdict passe donc **après** la réponse, où il ne retient plus rien.
#:
#: Sa forme est choisie pour être reconnaissable **en cours de flux**, sur un
#: préfixe et sans arbre : une suite ASCII qu'aucune phrase française ne produit,
#: assez courte pour que la rétention de queue qu'elle impose (au plus
#: `len(_MARQUEUR_VERDICT) - 1` caractères) ne se voie pas à l'écran.
_MARQUEUR_VERDICT = "%%MAESTRO%%"

#: Le cadre de l'orchestration : ce qu'elle est, et le contrat de sa réponse.
#: Il existait depuis #268 « si un jour elle passe par un modèle » et n'avait
#: jamais été branché ; #685 le branche et lui ajoute le verdict, puisque c'est le
#: **même** appel qui rend la réponse et la décision. #1222 **inverse leur ordre**
#: — la réponse d'abord, le verdict en dernière ligne — pour que la première
#: puisse s'écrire à l'écran sans attendre le second.
#:
#: ⚠ **Concaténé, et non interpolé** : le contrat de réponse ci-dessous est un objet
#: JSON, donc ce texte porte des accolades littérales qu'une f-string lirait comme des
#: champs. Le registre (#945) s'ajoute donc par `+`, ce qui laisse le gabarit JSON
#: intact — c'est le second des deux tutoiements que le retex du 2026-09-11 a relevés
#: (C5 : « Je te propose »).
_PROMPT_ORCHESTRATION = (
    """\
Tu es l'orchestrateur de Maestro : tu reçois les demandes de l'utilisateur, tu les
cadres et tu les confies à l'équipe d'agents du projet. Tu n'exécutes pas le
travail toi-même et tu ne parles pas à la place des agents — tu ouvres le travail,
tu en rends compte et tu dis où il en est.

Ce que l'équipe sait faire n'est pas une liste de métiers. Ses agents travaillent
dans le dossier du projet, avec un shell et des outils de fichiers : ils y
ÉCRIVENT (code, tests, documentation) et ils y AGISSENT (vider un dossier,
supprimer, renommer ou déplacer des fichiers, installer une dépendance, lancer une
commande). Ce qui se fait dans le dossier du projet est un travail qu'un run sait
prendre.

Ouvrir un run coûte du quota et écrit dans le projet de l'utilisateur : tu n'en
ouvres JAMAIS un de ta propre initiative. Tu proposes, et c'est l'utilisateur qui
accepte — c'est donc son accord, et non toi, qui décide si une demande mérite un
run.

Écris D'ABORD ta réponse à l'utilisateur, en clair et rien d'autre : c'est elle
qui s'affiche, et elle s'affiche AU FUR ET À MESURE que tu l'écris. Pas de JSON,
pas de préambule, pas de balise — la première phrase que tu écris est la première
qu'il lit.

Puis termine par une DERNIÈRE LIGNE, et une seule, de cette forme exacte :

%%MAESTRO%% {"verdict": "proposition|accord|echange", "objectif": "..."}

Cette ligne n'est jamais affichée : elle dit à l'interface quoi faire de ce que
tu viens d'écrire. Elle vient en dernier, après le dernier mot de ta réponse, et
rien ne la suit. Ne la mets jamais en tête, ni au milieu d'une phrase, ni dans un
bloc de code, et n'écris nulle part ailleurs la suite de caractères %%MAESTRO%%.

Le verdict :
- "proposition" — le dernier message de l'utilisateur est une demande de travail,
  sous n'importe quelle forme : impératif, question, souhait, subordonnée
  ("génère-moi une application d'agenda", "j'aimerai que tu ajoutes la
  pagination", "il faudrait que tu corriges le tri", "peux-tu me créer une
  application"). Une demande d'AGIR sur le projet en est une au même titre
  qu'une demande d'écrire ("vide le dossier de ce projet", "supprime les
  fichiers de log", "renomme src en app", "déplace les images dans assets").
  Sois large : un run proposé de trop coûte un "non", une demande légitime non
  reconnue coûte à l'utilisateur de se reformuler sans savoir pourquoi.
- "accord" — le dernier message approuve une proposition que TU viens de faire
  dans ce fil ("oui", "vas-y", "ok lance"). Sans proposition juste avant, ce
  n'est jamais un accord — et dans le doute non plus.
- "echange" — tout le reste : question sur l'outil ou sur le travail, demande
  d'état, salutation, refus ("non", "plutôt pas"), message que tu ne comprends
  pas.

Ce qui se fait dans le dossier du projet ne se refuse jamais comme étranger à
Maestro, et tu ne renvoies jamais l'utilisateur le faire lui-même ailleurs. Une
demande irréversible (vider, supprimer, écraser) se propose comme les autres : ta
réponse dit qu'elle est irréversible et ce qu'elle touchera, puis demande
l'accord.

L'objectif :
- sur "proposition", l'objectif que tu enverrais au run — une phrase complète et
  autonome, qui reformule la demande sans rien inventer ;
- sur "accord", recopie MOT POUR MOT l'objectif de la proposition que
  l'utilisateur vient d'approuver ;
- vide sur "echange".

La réponse : le texte affiché à l'utilisateur, en français, bref. Sur
"proposition", il énonce l'objectif et demande explicitement l'accord. Sur
"accord", il confirme que le run part — et c'est TOUT ce qui sera dit : rien
n'est ajouté derrière tes mots, ni identifiant, ni récapitulatif, ni « les tâches
apparaîtront ». L'identifiant du run et ce qu'il a ouvert s'affichent d'eux-mêmes
sous ta réponse ; ne les invente donc pas, tu ne les connais pas. Sur "echange",
il répond — en s'appuyant sur l'état de l'orchestration quand la question porte
dessus.

Avant la conversation, tu reçois DES FAITS : l'état de l'orchestration, puis les
runs de ce fil et de ce projet — statut, cause d'arrêt, issue, et chaque tâche
avec son détail. Réponds AVEC ces faits ; n'envoie jamais vers un écran chercher
ce que tu as déjà sous les yeux. À « pourquoi le run a échoué ? », nomme le run,
son statut et la cause telle qu'elle est écrite là — le détail d'une tâche la
porte souvent mieux que l'issue du run, qui n'est parfois qu'un décompte — puis
dis le geste qui y répond.

Cette lecture est BORNÉE : seulement les runs les plus récents, un nombre limité
de tâches, des détails tronqués, et elle le signale quand elle coupe. Ce qui n'y
est pas, tu ne l'as pas vu : dis-le, et renvoie alors vers l'endroit qui le
montre — la page Runs pour le détail complet d'un run et ses tâches (chaque run y
a sa page), le tableau de bord pour ce qui court sur le projet, Validations pour
ce qui attend un arbitrage, Coûts & analytics pour la dépense. Et ne promets pas
qu'un écran montre ce que tu n'as pas vu toi-même : si tu ignores où un livrable
a été écrit, dis-le franchement au lieu d'envoyer chercher.

Tu reçois aussi, quand il y en a, CE QUE TU VIENS DE LIRE dans le projet :
fichiers ouverts, recherches passées, détail complet d'un run. C'est du RÉEL, et
c'est ce qui doit porter ta réponse : donne la commande et le fichier tels qu'ils
y sont écrits, entre guillemets ou en bloc de code, plutôt que d'envoyer lire la
documentation. Ne renvoie vers un écran ou vers un fichier QUE si tu ne l'as pas
lu — et dis alors que tu ne l'as pas lu.

"""
    + registre()
)

#: Le cadre du **tour de lecture** (#1223) — l'appel qui décide ce qu'il faut
#: aller chercher, avant celui qui répond. Son contrat est aussi étroit que
#: possible : des lignes de demande, ou le mot `RIEN`. Il ne parle pas à
#: l'utilisateur et ne juge rien ; c'est l'appel suivant qui fait les deux.
#:
#: ⚠ **Concaténé comme `_PROMPT_ORCHESTRATION`**, et pour la même raison : le
#: catalogue porte des accolades littérales (les gabarits JSON des demandes)
#: qu'une f-string lirait comme des champs.
_PROMPT_CONSULTATION = (
    """\
Tu prépares la réponse de l'orchestrateur de Maestro. Tu ne réponds PAS à
l'utilisateur : tu décides ce qu'il faut LIRE pour pouvoir lui répondre avec des
faits plutôt qu'avec des généralités.

On te donne l'état de l'orchestration, l'équipe du projet, ce qui attend un
arbitrage, les runs récents et la conversation. Tout cela, tu l'as déjà : ne le
redemande pas.

"""
    + catalogue()
    + """

Demande une lecture dès que la réponse gagnerait à s'appuyer sur le projet réel —
« comment je teste ce qui a été livré ? », « où est le code de X ? », « qu'est-ce
que le run a produit ? », « pourquoi cette tâche a échoué ? » (le détail que tu
vois est tronqué, `detail` le rend entier).

Si rien n'a besoin d'être lu — une salutation, un accord, une demande de travail,
une question à laquelle ce que tu as déjà répond —, écris exactement :

RIEN

N'écris jamais rien d'autre : ni phrase, ni explication, ni réponse à
l'utilisateur. Des lignes de demande, ou RIEN.
"""
)

#: La fiche de l'orchestration, hors catalogue (voir le module) : le chat n'a
#: besoin que du nom, du rôle et du prompt système. Les compétences restent vides
#: — rien ne doit pouvoir lui router une tâche.
AGENT_ORCHESTRATION = Agent(
    nom=NOM_ORCHESTRATION,
    role=ROLE_ORCHESTRATION,
    competences=frozenset(),
    modele=MODELE_EXECUTANT_DEFAUT,
    prompt_systeme=_PROMPT_ORCHESTRATION,
)

#: Ouvrir un run sur un objectif, et rendre son résumé (dont `run_id`) — le seul
#: geste que le canal demande à la couche d'exécution. `ServiceExecutions.lancer`
#: le satisfait tel quel, une fois ses réglages liés par l'appelant. Le second
#: argument est le **projet de la fenêtre** d'où part la demande (#683), `None`
#: quand il n'y en a pas : le run part alors sans projet, comme avant ce lot.
#:
#: Le troisième porte les **bornes** que l'écran a posées au moment de lancer
#: (#990) : coût, tokens, délai par tâche, parallélisme. Elles voyagent d'un bloc
#: plutôt qu'en quatre paramètres — voir `controltower.bornes` —, et
#: `AUCUNE_BORNE` est le régime de tous les runs ouverts depuis le fil avant ce
#: ticket, donc ce que rend un appelant qui n'en pose pas.
#:
#: Le quatrième est le **contexte des sources** de la conversation (#1172,
#: `contexte_du_fil`) : ce que les messages ont joint, déjà lu et encadré comme
#: donnée. Avant lui, un run ouvert depuis le fil partait sans les pièces jointes
#: dont on venait de parler. `""` quand la conversation n'en porte aucune.
LanceurRun = Callable[[str, str | None, BornesRun, str], Awaitable[Mapping[str, Any]]]

#: Combien d'agents le projet `projet_id` compte — la sonde de #1146. `0` dit « il
#: n'y a personne », et c'est la seule réponse qui change la conduite du canal ;
#: `None` dit « je ne sais pas » (projet inconnu, dépôt illisible) et la laisse
#: telle qu'avant ce lot. Le câblage la branche sur `catalogue_du_projet`, pour
#: que le fil compte exactement les agents vers lesquels le routeur enverra les
#: tâches.
EquipeDuProjet = Callable[[str], int | None]

#: Crée dans le projet l'équipe validée et rend son rapport (`EquipeCreee.to_dict`)
#: — le seul geste de recrutement que le canal demande, par la voie de #1040.
#: Arguments : le projet, les rôles retenus, la proposition dont ils sortent (une
#: trace, jamais une condition). Une équipe refusée lève, et le canal le raconte.
RecruteurEquipe = Callable[[str, Sequence[RoleValide], str], Awaitable[Mapping[str, Any]]]

#: Exécute **une** lecture demandée par le modèle, dans le projet de la fenêtre
#: (#1223). Attendable, parce que lire touche le disque ; elle ne lève jamais —
#: un empêchement est une `Lecture` qui le dit (`consultation.Consultations`).
#: Sans elle, le canal n'ouvre aucun tour de lecture.
Consultation = Callable[[Demande, str | None], Awaitable[Lecture]]

#: Combien de tours de lecture au plus (#1223). **Deux**, et c'est une décision :
#: un tour trouve ce qu'on lui a nommé, le second suit la piste que le premier a
#: ouverte — lister le projet, puis lire le README qu'on y a vu, c'est le geste
#: exact du constat du 2026-09-22. Un troisième paierait un appel de plus pour
#: une profondeur qu'aucune question de fil n'a demandée.
_TOURS_DE_LECTURE = 2

#: Ce que le canal dit quand il propose l'équipe au lieu du run (#1146). Les trois
#: moments du critère, dans l'ordre : pourquoi pas de run (personne pour prendre
#: les tâches, et ce que ça coûterait), ce qui est proposé à la place, et ce qui
#: suit la validation — la demande reprise, sans rien retaper.
_PHRASE_RECRUTEMENT = (
    "Avant de lancer « {objectif} », il faut une équipe : ce projet n'a encore "
    "aucun agent, donc personne pour prendre les tâches d'un run — il échouerait "
    "après avoir payé son cadrage. Je vous propose l'équipe que son analyse "
    "appelle, juste en dessous : validez-la, en retirant un rôle ou en ajustant "
    "ses instances si besoin, et je vous proposerai aussitôt le run. Rien n'est "
    "créé sans votre validation."
)

#: Ce qu'on répond à un refus de renfort **pendant un run** (#1227). Il ne se dit
#: pas comme le refus d'une équipe entière : là-bas rien ne pouvait partir, ici le
#: run continue — et c'est précisément ce qu'il faut dire, sans quoi la personne
#: reste à se demander si elle vient d'annuler son travail. La nuance de qualité
#: est nommée telle quelle : décliner n'est pas une erreur, c'est un arbitrage.
_REFUS_PENDANT_UN_RUN = (
    "Entendu : je ne recrute personne. Le run continue avec l'équipe actuelle — "
    "les tâches qui demandaient « {role} » iront au rôle le plus proche, qui n'en "
    "a pas le métier. Vous pourrez toujours recruter depuis les écrans d'agents du "
    "projet et relancer ce travail."
)

#: Le même constat, quand le canal ne peut pas créer d'équipe lui-même (aucun
#: recruteur câblé) : il ne propose pas une demande à laquelle aucun geste ne
#: pourrait répondre, il dit où la créer.
_PHRASE_SANS_RECRUTEUR = (
    "Je ne lance pas « {objectif} » : ce projet n'a encore aucun agent, donc "
    "personne pour prendre les tâches d'un run. Créez son équipe depuis les "
    "écrans d'agents du projet, puis redites-moi votre demande."
)


def contexte_du_fil(fil: Sequence[MessageChat]) -> str:
    """Les sources que la conversation a jointes, déjà lues et encadrées (#1172).

    Chaque message d'utilisateur qui a porté des sources persiste leur contexte
    (`MessageChat.contexte`), sorti de `contexte_markdown` et de lui seul : c'est
    ce qui a été annoncé « lu » dans le fil. On les reprend **tels quels**, dans
    l'ordre de la conversation, sans relire aucune source. Relire enverrait au
    brief une matière qui n'est peut-être plus celle que la personne a vue (une
    page, #316), ou plus du tout (un téléversement ramassé). Un contexte identique
    joint deux fois ne compte qu'une fois.

    Les réponses de l'agent n'en portent jamais (`MessageChat` : les sources n'ont
    de valeur que sur un message d'utilisateur). Le filtre sur l'auteur est donc
    une ceinture, pas une règle de plus.
    """
    vus: list[str] = []
    for message in fil:
        contexte = message.contexte.strip()
        if message.auteur == UTILISATEUR and contexte and contexte not in vus:
            vus.append(contexte)
    return "\n\n".join(vus)

#: L'état de l'orchestration en une phrase, pour répondre « où en est-on ? » sans
#: donner à ce module la connaissance de la projection. Il prend le projet de la
#: fenêtre (#683) — un `str | None`, jamais un objet de portée : ce module ne
#: connaît ni la projection ni le contrat de lecture, il **transmet** ce que le
#: fil lui a donné, l'appelant en fait une portée.
ApercuOrchestration = Callable[[str | None], str]

#: Les statuts sous lesquels un run **n'est pas soldé** : il tourne, ou il attend
#: quelqu'un. Les quatre comptent pour « en cours » dans l'aperçu — de la place
#: où l'on pose la question, un run qui attend un arbitrage est un run en cours,
#: et l'écran des exécutions dira lequel attend quoi.
_STATUTS_ACTIFS = frozenset(
    {
        EXECUTION_EN_COURS,
        EXECUTION_EN_ATTENTE_BRIEF,
        EXECUTION_EN_ATTENTE_REPONSES,
        EXECUTION_EN_ATTENTE_ARBITRAGE,
    }
)

#: Ce que le fil dit d'un statut d'exécution (#946, C7 du retex du 2026-09-11) :
#: l'ouverture d'un run annonçait « statut « en_cours » », c'est-à-dire
#: l'identifiant de la machine à états rendu tel quel dans une conversation.
#:
#: Les libellés sont ceux de `libelleStatutExecution` (`apps/web/lib/format.ts`)
#: **au mot près** — c'est la règle de #571, et le même run lu dans le fil puis
#: sur son écran ne doit pas paraître dans deux états. Un statut absent de la
#: table se dit brut plutôt que traduit à l'aveugle.
_LIBELLES_STATUT_EXECUTION = {
    EXECUTION_EN_COURS: "En cours",
    EXECUTION_TERMINEE: "Terminée",
    EXECUTION_ANNULEE: "Annulée",
    EXECUTION_ECHEC: "Échec",
    EXECUTION_EN_ATTENTE_BRIEF: "Brief à valider",
    EXECUTION_EN_ATTENTE_REPONSES: "Questions en attente",
    EXECUTION_EN_ATTENTE_ARBITRAGE: "Validation en attente",
}


def libelle_statut_execution(statut: str) -> str:
    """Le statut d'un run en mots d'interface, ou brut si le flux s'est enrichi."""
    return _LIBELLES_STATUT_EXECUTION.get(statut, statut)


#: Ce que le fil dit d'un statut de **tâche** (#1157) — les libellés de
#: `LIBELLES_STATUT` (`apps/web/lib/format.ts`) au mot près, exactement la règle
#: de la table au-dessus : la même tâche lue dans le fil puis sur le Kanban ne
#: doit pas paraître dans deux états.
#:
#: Seuls les huit statuts que la **machine à états** donne à une tâche (docs/03
#: §3) y figurent. La table du front en range aussi qui n'en sont pas — issues de
#: fusion, arbitrages d'outil, blocage signalé : ce sont des faits *consignés sur*
#: une tâche, jamais la colonne où elle se trouve. Les recopier ici ferait
#: traduire, dans une phrase qui dit où en est une tâche, des mots qui n'y
#: arrivent jamais.
_LIBELLES_STATUT_TACHE = {
    STATUT_BACKLOG: "À faire",
    STATUT_PRETE: "Prête",
    STATUT_ASSIGNEE: "Assignée",
    STATUT_EN_COURS: "En cours",
    STATUT_EN_ATTENTE_VALIDATION: "Attente humaine",
    STATUT_BLOQUEE: "Bloquée",
    STATUT_TERMINEE: "Terminée",
    STATUT_ECHEC: "Échec",
}


def libelle_statut_tache(statut: str) -> str:
    """Le statut d'une tâche en mots d'interface, ou brut si le flux s'est enrichi."""
    return _LIBELLES_STATUT_TACHE.get(statut, statut)


#: Ce que **dit** chaque cause d'arrêt d'un run (#479) — les phrases de
#: `LIBELLES_CAUSE` (`apps/web/lib/format.ts`) au mot près, une troisième fois la
#: règle de #571. Elles restent **génériques** là aussi : le chiffre — quelle
#: borne, quel montant — vit dans le détail de l'issue, que le fil rapporte à
#: côté.
_LIBELLES_CAUSE = {
    CAUSE_PLAFOND_TOURS: "Plafond de tours atteint",
    CAUSE_PLAFOND_COUT: "Plafond de dépense atteint",
    CAUSE_LIMITE_USAGE: "Limite d'usage du fournisseur",
    CAUSE_HOTE: "L'hôte du run n'a pas démarré",
    CAUSE_ANNULATION: "Interrompu",
    CAUSE_EXTINCTION: "Maestro s'est éteint",
}


def libelle_cause(cause: str) -> str:
    """La cause d'arrêt d'un run en une phrase — **vide** quand il n'y a rien à dire.

    Deux cas rendent la chaîne vide, et le fil n'a aucune raison de les
    distinguer : le moteur n'a pas su classer l'échec (`cause_de` rend `""`
    plutôt qu'une cause fourre-tout), ou il a émis un code que cette table ne
    connaît pas encore. Rendre le code brut écrirait « hote_non_demarre » dans
    une conversation, ce que `libelleCause` refuse déjà côté écran — et le détail
    de l'issue, lui, reste rapporté juste à côté.
    """
    return _LIBELLES_CAUSE.get(cause, "") if cause else ""


#: Un bloc de code Markdown, que les modèles posent volontiers autour d'un JSON
#: qu'on leur a demandé nu.
_FENCE = re.compile(r"```(?:json)?\s*(?P<corps>.+?)```", re.DOTALL)

#: Ce que le canal répond quand il n'a pas pu juger (#686). L'ordre des trois
#: morceaux **est** le contenu du message : la cause, ce qui n'a pas eu lieu, le
#: geste qui répare. Le deuxième porte le critère — dire « je ne peux pas » sans
#: dire « je n'ai rien ouvert » laisse chercher au tableau de bord un run qui
#: n'existe pas. Et il parle du **message**, jamais de « votre demande » : savoir
#: que c'en était une est précisément ce qui manque.
_PHRASE_INJOIGNABLE = (
    "Je ne peux pas juger votre message pour l'instant : {cause}. "
    "Aucun run n'a été ouvert, et je ne vous en ai proposé aucun. {reparation}"
)

#: Le fournisseur est configuré mais n'a rien rendu — panne passagère, on
#: réessaie. La phrase dit **où est la demande**, parce que c'est la question qui
#: vient juste après « ça n'a pas marché » : le message est déjà au fil, il n'y a
#: rien à retaper.
_REPARATION_PASSAGERE = (
    "Votre message reste dans ce fil : renvoyez-le tel quel quand le fournisseur "
    "répondra à nouveau, vous n'avez rien à retaper."
)

#: Rien ne répondra tant que le réglage n'aura pas été posé — le dire évite dix
#: renvois inutiles, et c'est toute la raison de séparer les deux familles. Les
#: réglages sont **nommés** parce qu'ici celui qui lit est celui qui répare : une
#: Control Tower locale n'a pas d'exploitant à qui transmettre.
_REPARATION_CONFIGURATION = (
    "Ce n'est pas une panne passagère mais un réglage absent : renseignez le "
    "fournisseur de modèle (MAESTRO_PROVIDER et ses identifiants) dans la "
    "configuration, puis renvoyez votre message — il reste dans ce fil."
)

#: Ce que le poste offre côté modèles (#253, `maestro.poste.SondePoste.rapport`) —
#: injecté plutôt qu'importé : le fil n'a pas à savoir comment on sonde un poste.
SondeDuPoste = Callable[[], Awaitable["RapportSonde"]]

#: Le nombre de modèles nommés par fournisseur trouvé : de quoi choisir, pas un
#: inventaire — un serveur local peut en servir des dizaines.
_MODELES_NOMMES = 3


async def reparation_configuration(sonde: SondeDuPoste | None) -> str:
    """Le geste qui répare un réglage absent, **avec ce que le poste offre** (#1173).

    Un produit qui sait sonder le poste ne renvoie pas vers une variable à
    deviner : il dit ce qu'il a trouvé et quel réglage le branche — Claude Code
    sur le `PATH`, un Ollama qui écoute et les modèles qu'il sert, une clé posée.
    C'est la règle de la personne pour tout prérequis manquant que Maestro sait
    combler : le proposer là où il manque. La sonde ne lance rien (résolution sur
    le `PATH`, sondes HTTP locales bornées) ; si elle échoue ou ne trouve rien, le
    message d'avant reste, complété de ce constat.
    """
    if sonde is None:
        return _REPARATION_CONFIGURATION
    try:
        rapport = await sonde()
    except Exception:  # noqa: BLE001 — la sonde éclaire, elle ne décide de rien
        return _REPARATION_CONFIGURATION
    prets = [c for c in rapport.constats if c.fournisseur and c.utilisable]
    if not prets:
        return f"{_REPARATION_CONFIGURATION} Je n'ai trouvé aucun fournisseur prêt sur ce poste."
    offres = []
    for constat in prets:
        modeles = ", ".join(constat.modeles[:_MODELES_NOMMES])
        offres.append(
            f"{constat.libelle} (MAESTRO_PROVIDER={constat.fournisseur}"
            + (f", MAESTRO_MODEL parmi : {modeles}" if modeles else "")
            + ")"
        )
    return f"Sur ce poste, je trouve : {' ; '.join(offres)}. {_REPARATION_CONFIGURATION}"


class _JugeInjoignable(RuntimeError):
    """Le juge n'a rendu **aucun** verdict, et ce que le fil doit en dire (#686).

    Une exception plutôt qu'un quatrième `VERDICT_*` : les trois autres disent ce
    que le modèle a *jugé*, celle-ci dit qu'il n'a rien jugé du tout. Les ranger
    ensemble ferait passer une panne pour une décision, et il suffirait d'un `if`
    oubliant le quatrième cas pour qu'un run s'ouvre sans juge.

    Elle ne sort jamais du module : `produire` la rattrape et rend son texte au
    fil, là où la laisser remonter la ferait traduire en `ReponseIndisponible`,
    donc en 502 muet.
    """

    def __init__(self, cause: str, reparation: str) -> None:
        super().__init__(_PHRASE_INJOIGNABLE.format(cause=cause, reparation=reparation))


def _accord(nombre: int, singulier: str, pluriel: str) -> str:
    """« 1 run » / « 3 runs » — l'accord, écrit une fois."""
    return f"{nombre} {singulier if nombre <= 1 else pluriel}"


def _composition(rapport: Mapping[str, Any]) -> str:
    """L'équipe créée en une ligne — « Développeur ×2 · QA — 3 agents » (#1146).

    Lue dans le **rapport de création** (`EquipeCreee.to_dict`) et non dans ce qui
    a été demandé : le fil dit ce qui existe désormais dans le projet, comme
    l'étape d'équipe du parcours de création (« la liste, jamais un ok »).
    """
    agents = [a for a in rapport.get("agents") or () if isinstance(a, Mapping)]
    roles = " · ".join(
        f"{a.get('role') or a.get('nom')} ×{a['instances']}"
        if int(a.get("instances") or 1) > 1
        else str(a.get("role") or a.get("nom"))
        for a in agents
    )
    total = int(rapport.get("instances_total") or 0) or sum(
        int(a.get("instances") or 1) for a in agents
    )
    return f"{roles} — {_accord(total, 'agent', 'agents')}"


def apercu_de(state: ControlTowerState) -> ApercuOrchestration:
    """L'aperçu de l'orchestration, lu **à chaque question** dans `state`.

    Une fabrique et non une méthode du répondeur : celui-ci ne connaît qu'un
    `ApercuOrchestration` (« l'état, en une phrase »), ce qui le rend jouable
    sans projection, tandis que la formule vit ici, avec le canal qui la dit. La
    lecture est refaite à chaque appel — un aperçu figé à la construction de
    l'app annoncerait l'état d'hier.

    Elle est **cadrée sur le projet de la fenêtre** depuis #683, et c'est la
    seconde moitié du défaut que ce ticket corrige : la phrase comptait *tous*
    les runs du poste quand chaque écran ne montre que ceux du projet actif, si
    bien que le fil annonçait « 1 run en cours » à propos d'un run que la liste
    ne portait pas et que la vue de détail refusait d'ouvrir. Ce qu'elle compte
    est désormais ce que l'écran peut montrer.

    La portée est celle du contrat de lecture (#277) — `PorteeProjet.retient`,
    la règle écrite une fois — et non un filtre de plus : les trois compteurs de
    la phrase (runs actifs, tâches suivies, validations en attente) passent par
    la **même**, faute de quoi une seule phrase mélangerait deux périmètres.
    Sans projet — un client qui n'en envoie pas, un poste qui n'en a pas —, elle
    reste **transverse**, c'est-à-dire exactement la phrase d'avant ce lot.

    Depuis #685 elle n'est plus rendue telle quelle à l'utilisateur : elle entre
    dans le **prompt**, en tête du fil, et c'est le modèle qui la reprend quand la
    question porte dessus. Elle reste lue à chaque message, y compris sur une
    demande de travail — la construire coûte une lecture en mémoire, et savoir ce
    qui tourne déjà est ce qui permet de répondre « un run est déjà en cours
    là-dessus » plutôt que d'en proposer un second.
    """

    def apercu(projet_id: str | None = None) -> str:
        portee = PorteeProjet.projet(projet_id) if projet_id else PorteeProjet.tous()
        actifs = [run for run in state.executions(portee) if run.statut in _STATUTS_ACTIFS]
        attentes = sum(
            1 for validation in state.validations(portee) if validation.en_attente
        )
        if not actifs:
            phrase = "Aucun run en cours."
        else:
            phrase = (
                f"{_accord(len(actifs), 'run en cours', 'runs en cours')}, "
                f"{_accord(len(state.taches(portee)), 'tâche suivie', 'tâches suivies')}."
            )
        if attentes:
            phrase += f" {_accord(attentes, 'validation attend', 'validations attendent')} "
            phrase += "votre arbitrage."
        return phrase

    return apercu


#: Ce que les runs ont **fait** — statut, cause d'arrêt, issue, et chaque tâche
#: avec son détail (#1157). Deux arguments : le projet de la fenêtre, comme
#: l'aperçu, et les runs que **ce fil** a ouverts (`runs_du_fil`), qui entrent
#: quel que soit leur projet — un run dicté au fil avant #683 est orphelin, donc
#: dans la vue d'aucun projet, et c'est pourtant de lui que la conversation
#: parle. Rend `""` quand il n'y a aucun run à raconter : le bloc disparaît du
#: prompt plutôt que d'y annoncer un vide que l'aperçu dit déjà.
FaitsDesRuns = Callable[[str | None, Sequence[str]], str]

#: Combien de runs le fil raconte au juge. Une borne, parce qu'un projet qui a
#: tourné cent fois ferait un prompt que personne ne paie deux fois : ce dont une
#: conversation parle est ce qui vient de se passer, pas l'histoire du projet.
#: Trois couvre « le run que je viens d'ouvrir », « celui d'avant » et « celui
#: qu'on a relancé ».
_RUNS_RACONTES = 3

#: Combien de tâches par run. Un run de la Control Tower en décompose une
#: poignée ; la borne existe pour le jour où il en décomposera cinquante, et ce
#: qui dépasse est **compté** dans le texte, jamais tu.
_TACHES_RACONTEES = 12

#: La longueur d'un détail rapporté. Un détail de tâche est une erreur, donc
#: parfois une trace entière : sa première phrase porte la cause, la suite porte
#: la pile. La coupe est **dite**, pour la même raison que le compte ci-dessus.
_DETAIL_MAX = 300


def runs_du_fil(fil: Sequence[MessageChat]) -> tuple[str, ...]:
    """Les runs que **ce fil** a ouverts, du plus récent au plus ancien (#1157).

    Chacun est rattaché au message qui l'a ouvert (`MessageChat.run_id`, #268) :
    la conversation porte donc déjà la liste, rien n'est à stocker à côté, et un
    fil relu du disque la retrouve entière. C'est la même propriété que « le fil
    est la seule mémoire » (#685), appliquée à ce qu'il a déclenché.

    L'ordre est celui de la question qu'on pose — « pourquoi le run a échoué ? »
    parle du dernier —, et un même run rattaché deux fois ne compte qu'une fois.
    """
    vus: list[str] = []
    for message in reversed(fil):
        if message.run_id and message.run_id not in vus:
            vus.append(message.run_id)
    return tuple(vus)


def faits_des_runs(state: ControlTowerState) -> FaitsDesRuns:
    """Les faits des runs, lus **à chaque question** dans `state` (#1157).

    Une fabrique et non une méthode du répondeur, exactement comme `apercu_de` et
    pour les mêmes deux raisons : le répondeur ne connaît qu'un `FaitsDesRuns`,
    ce qui le rend jouable sans projection, et la lecture est refaite à chaque
    appel — figée à la construction de l'app, elle raconterait les runs d'hier.

    Les runs du fil viennent **en premier et sans condition de projet** : ce sont
    ceux dont la conversation parle, et `state.execution` les trouve par leur
    seul identifiant. Ceux du projet de la fenêtre suivent, du plus récent au
    plus ancien, par la portée du contrat de lecture (#277) — la même règle que
    l'aperçu, pour que les deux blocs d'un même prompt ne parlent pas de deux
    périmètres.
    """

    def faits(projet_id: str | None = None, runs: Sequence[str] = ()) -> str:
        retenus: list[EtatExecution] = []
        vus: set[str] = set()
        for run_id in runs:
            execution = state.execution(run_id)
            if execution is not None and run_id not in vus:
                vus.add(run_id)
                retenus.append(execution)
        portee = PorteeProjet.projet(projet_id) if projet_id else PorteeProjet.tous()
        # `executions` rend l'ordre de première apparition : le plus récent est le
        # dernier, et c'est par lui qu'une conversation commence.
        for execution in reversed(state.executions(portee)):
            if execution.run_id not in vus:
                vus.add(execution.run_id)
                retenus.append(execution)
        if not retenus:
            return ""
        montres = retenus[:_RUNS_RACONTES]
        lignes = [_entete_des_runs(len(montres), len(retenus))]
        for execution in montres:
            lignes.extend(_fiche_du_run(state, execution))
        return "\n".join(lignes)

    return faits


def _entete_des_runs(montres: int, total: int) -> str:
    """La ligne qui annonce le bloc — et **dit** qu'il est borné quand il l'est."""
    compte = (
        f"{montres} sur {total}, lecture bornée" if montres < total else str(total)
    )
    return f"Runs de ce fil et de ce projet, du plus récent au plus ancien ({compte}) :"


def _fiche_du_run(state: ControlTowerState, execution: EtatExecution) -> list[str]:
    """Un run en quelques lignes : ce qu'il visait, où il en est, ce qu'ont fait ses tâches.

    Ses tâches sont celles que le run a **portées** (`PorteeRun`, #473) et non
    celles dont le `run_id` le désigne : sur une relance, la seconde lecture
    volerait ses tâches au run qu'on interroge. Aucune portée de projet ne s'y
    ajoute — les tâches d'un run sont les siennes, et un filtre de projet les
    ferait disparaître d'un run dont la projection n'a pas appris le projet.
    """
    entete = f"- Run {execution.run_id} — {libelle_statut_execution(execution.statut)}"
    if execution.objectif:
        entete += f" — « {_borne(execution.objectif)} »"
    lignes = [entete]
    cause = libelle_cause(execution.cause)
    if cause:
        lignes.append(f"  cause : {cause}")
    issue = _borne(_issue_du_run(execution))
    if issue:
        lignes.append(f"  issue : {issue}")
    taches = state.taches(run=PorteeRun.run(execution.run_id))
    if not taches:
        lignes.append("  tâches : aucune tâche connue de ce run.")
        return lignes
    details = _details_des_taches(execution)
    lignes.append(f"  tâches ({len(taches)}) :")
    lignes.extend(
        f"    · {_ligne_de_tache(tache, details.get(tache.id, ''))}"
        for tache in taches[:_TACHES_RACONTEES]
    )
    reste = len(taches) - _TACHES_RACONTEES
    if reste > 0:
        lignes.append(
            "    · "
            + _accord(
                reste,
                "autre tâche de ce run n'est pas montrée ici",
                "autres tâches de ce run ne sont pas montrées ici",
            )
            + "."
        )
    return lignes


def _ligne_de_tache(tache: EtatTache, detail: str) -> str:
    """Une tâche en une ligne : ce qu'elle est, où elle en est, ce qu'elle a dit.

    Le porteur est son **rôle** avant son nom d'agent, parce que c'est le rôle
    qui porte le repli du routeur : une tâche que personne n'a pu prendre a pour
    agent un tiret (`ROLE_A_ASSIGNER`, #42) et pour rôle « à assigner », et c'est
    le second qui apprend quelque chose.

    Le détail est **nommé** (« détail : ») et non simplement ajouté à la suite :
    il porte lui-même des tirets cadratins — « aucun agent dans ce catalogue —
    l'équipe reste à recruter » —, et sans l'étiquette il se confondrait avec les
    champs qui le précèdent.
    """
    morceaux = [f"{tache.id} « {tache.titre} »" if tache.titre else tache.id]
    morceaux.append(libelle_statut_tache(tache.statut) if tache.statut else "statut inconnu")
    porteur = tache.role or tache.agent
    if porteur:
        morceaux.append(porteur)
    borne = _borne(detail)
    if borne:
        morceaux.append(f"détail : {borne}")
    return " — ".join(morceaux)


def _issue_du_run(execution: EtatExecution) -> str:
    """Ce que le dernier événement de cycle de vie a écrit sur l'issue du run.

    Souvent un décompte (« 0/3 tâche(s) réussie(s) »), parfois la cause entière
    (« PlafondDepenseDepasse : … ») : les deux méritent d'être rapportés tels
    quels, et c'est précisément parce que le premier n'explique rien que les
    détails des tâches viennent avec.
    """
    for event in reversed(execution.evenements):
        if event.type == EVENEMENT_EXECUTION_STATUT and event.detail:
            return event.detail
    return ""


def _details_des_taches(execution: EtatExecution) -> dict[str, str]:
    """Le dernier détail écrit par chaque tâche du run — son erreur, le plus souvent.

    Lu dans les **événements du run** et non sur `EtatTache`, qui n'en porte
    aucun : ce qu'une tâche échouée a à dire voyage dans le `detail` de son
    `tache.statut`, où `bridge` recopie son `erreur`. C'est là, et nulle part
    ailleurs, qu'était la cause réelle de l'essai du 2026-09-21 — « aucun agent
    dans ce catalogue — l'équipe reste à recruter » — pendant que l'issue du run
    n'annonçait qu'un décompte.

    Le dernier vu fait foi : une tâche qui repart puis retombe parle de sa
    dernière chute, jamais de l'avant-dernière.
    """
    details: dict[str, str] = {}
    for event in execution.evenements:
        if event.type == EVENEMENT_TACHE_STATUT and event.tache_id and event.detail:
            details[event.tache_id] = event.detail
    return details


def _borne(texte: str) -> str:
    """Un texte ramené à `_DETAIL_MAX`, sur une seule ligne, coupé **en le disant**.

    Les sauts de ligne partent d'abord : le bloc est lu ligne par ligne, et une
    trace multi-ligne en ferait éclater la structure — une pile Python se lirait
    comme autant de tâches.
    """
    propre = " ".join(texte.split())
    if len(propre) <= _DETAIL_MAX:
        return propre
    return f"{propre[:_DETAIL_MAX].rstrip()}… (tronqué)"


def detail_du_run(state: ControlTowerState) -> Callable[[str], str]:
    """Le détail **complet** d'un run — ce que la borne de `faits_des_runs` coupe (#1223).

    La même fiche que `_fiche_du_run`, sans aucune des trois bornes : toutes les
    tâches, chaque détail entier. C'est le verbe `detail` de `consultation`, et
    c'est la réponse exacte au constat du ticket — *« le détail que j'ai ici est
    tronqué »*. Il n'entre jamais dans le prompt de lui-même : il faut que le
    modèle l'ait **demandé**, ce qui est la différence entre un contexte qui
    grossit à chaque message et une lecture payée quand elle sert.

    Rend `""` sur un run inconnu, que `consultation` traduit en « aucun run … ».
    """

    def detail(run_id: str) -> str:
        execution = state.execution(run_id)
        if execution is None:
            return ""
        lignes = [
            f"Run {execution.run_id} — {libelle_statut_execution(execution.statut)}"
        ]
        if execution.objectif:
            lignes.append(f"objectif : {execution.objectif}")
        cause = libelle_cause(execution.cause)
        if cause:
            lignes.append(f"cause : {cause}")
        issue = _issue_du_run(execution)
        if issue:
            lignes.append(f"issue : {issue}")
        taches = state.taches(run=PorteeRun.run(execution.run_id))
        details = _details_des_taches(execution)
        if not taches:
            lignes.append("tâches : aucune tâche connue de ce run.")
            return "\n".join(lignes)
        lignes.append(f"tâches ({len(taches)}) :")
        for tache in taches:
            entete = f"- {tache.id} « {tache.titre} »" if tache.titre else f"- {tache.id}"
            statut = libelle_statut_tache(tache.statut) if tache.statut else "statut inconnu"
            entete += f" — {statut}"
            porteur = tache.role or tache.agent
            if porteur:
                entete += f" — {porteur}"
            lignes.append(entete)
            detail_tache = details.get(tache.id, "").strip()
            if detail_tache:
                lignes.append(f"  détail : {detail_tache}")
        return "\n".join(lignes)

    return detail


#: L'équipe du projet **en clair** — rôles, agents, instances (#1223, critère 3).
#: Distincte d'`EquipeDuProjet`, qui ne rend qu'un compte : celle-là décide de la
#: conduite du canal (proposer une équipe au lieu d'un run), celle-ci entre dans
#: le prompt pour que « qui travaille sur ce projet ? » trouve une réponse. Les
#: fondre ferait dépendre un garde-fou d'une chaîne de caractères.
EquipeDuFil = Callable[[str | None], str]

#: Ce qui **attend quelqu'un**, avec son contenu (#1223, critère 3) : validations
#: et questions en attente. L'aperçu les **compte** depuis #683 ; le fil ne
#: pouvait donc dire ni ce qu'on lui demande d'arbitrer, ni ce qu'un agent a
#: demandé. Rend `""` quand rien n'attend — le bloc disparaît plutôt que
#: d'annoncer un vide que l'aperçu dit déjà.
AttentesEnCours = Callable[[str | None], str]

#: Combien d'attentes le fil raconte, et sur quelle longueur. Mêmes raisons que
#: les bornes des runs (#1157), et elles se **disent** de la même façon.
_ATTENTES_RACONTEES = 5
_ATTENTE_MAX = 400


def attentes_de(state: ControlTowerState) -> AttentesEnCours:
    """Ce qui attend un arbitrage ou une réponse, **avec son contenu** (#1223).

    Une fabrique, comme `apercu_de` et `faits_des_runs`, et pour les deux mêmes
    raisons : le répondeur ne connaît qu'un `AttentesEnCours`, et la lecture est
    refaite à chaque message — figée, elle annoncerait les attentes d'hier.

    Deux files plutôt qu'une, parce qu'elles n'appellent pas le même geste : une
    **validation** attend qu'on approuve ou refuse un acte (l'écran Validations),
    une **question** attend une réponse écrite, et elle dit ce que l'agent fera
    sans elle (`hypothese`, `attente`). Les fondre ferait perdre précisément ce
    qui distingue « tranche » de « réponds ».

    La portée est celle du contrat de lecture (#277) — la **même** que l'aperçu
    et les faits des runs, pour qu'un seul prompt ne mélange pas deux périmètres.
    """

    def attentes(projet_id: str | None = None) -> str:
        portee = PorteeProjet.projet(projet_id) if projet_id else PorteeProjet.tous()
        validations = [v for v in state.validations(portee) if v.en_attente]
        questions = [q for q in state.questions(portee) if q.en_attente]
        if not validations and not questions:
            return ""
        lignes: list[str] = []
        if validations:
            lignes.append(
                _entete_attentes("validation", "validations", len(validations))
            )
            for validation in validations[:_ATTENTES_RACONTEES]:
                lignes.extend(_fiche_validation(validation))
        if questions:
            lignes.append(_entete_attentes("question", "questions", len(questions)))
            for question in questions[:_ATTENTES_RACONTEES]:
                lignes.extend(_fiche_question(question))
        return "\n".join(lignes)

    return attentes


def _entete_attentes(singulier: str, pluriel: str, total: int) -> str:
    """La ligne qui annonce une file — et **dit** qu'elle est bornée quand elle l'est."""
    entete = f"{_accord(total, singulier + ' en attente', pluriel + ' en attente')}"
    if total > _ATTENTES_RACONTEES:
        entete += f" ({_ATTENTES_RACONTEES} montrées, lecture bornée)"
    return f"{entete} :"


def _fiche_validation(validation: EtatValidation) -> list[str]:
    """Une validation : ce qu'elle retient, l'acte proposé, et pourquoi on demande."""
    entete = f"- {validation.titre or validation.tache_id}"
    porteur = validation.role or validation.agent
    if porteur:
        entete += f" — {porteur}"
    if validation.run_id:
        entete += f" — run {validation.run_id}"
    lignes = [entete]
    if validation.outil:
        acte = validation.outil
        arguments = " ".join(
            f"{cle}={valeur}" for cle, valeur in (validation.arguments or {}).items()
        )
        lignes.append(f"  acte : {_attente_bornee(f'{acte} {arguments}'.strip())}")
    if validation.description:
        lignes.append(f"  ce qu'elle ferait : {_attente_bornee(validation.description)}")
    if validation.raison:
        lignes.append(f"  pourquoi on demande : {_attente_bornee(validation.raison)}")
    return lignes


def _fiche_question(question: EtatQuestion) -> list[str]:
    """Une question d'agent : ce qu'elle demande, ses choix, et l'hypothèse de repli."""
    entete = f"- {_attente_bornee(question.question) or question.question_id}"
    porteur = question.role or question.agent
    if porteur:
        entete += f" — {porteur}"
    lignes = [entete]
    if question.choix:
        lignes.append(f"  choix : {' · '.join(question.choix)}")
    if question.hypothese:
        lignes.append(f"  sans réponse : {_attente_bornee(question.hypothese)}")
    if question.attente:
        lignes.append(f"  délai : {_attente_bornee(question.attente)}")
    return lignes


def _attente_bornee(texte: str) -> str:
    """Un texte d'attente ramené à `_ATTENTE_MAX`, sur une ligne, coupé **en le disant**."""
    propre = " ".join(texte.split())
    if len(propre) <= _ATTENTE_MAX:
        return propre
    return f"{propre[:_ATTENTE_MAX].rstrip()}… (tronqué)"


@dataclass(frozen=True)
class _Verdict:
    """Ce qu'un appel modèle rend : ce que le canal dit, et ce qu'il en conclut.

    `nom` est l'un des `VERDICT_*`, `reponse` le texte affiché, `objectif` la
    reformulation — celle qui est **proposée**, puis celle qui est **lancée**
    quand l'utilisateur l'a approuvée. Les trois viennent du même appel : séparer
    « juger » de « répondre » en ferait deux, dont le second devrait redire au
    modèle ce que le premier vient de décider.
    """

    nom: str
    reponse: str
    objectif: str = ""


def _objet_json(texte: str) -> Any:
    """Le premier objet JSON de `texte` — nu, en bloc de code, ou noyé dans la prose.

    La borne de la sous-chaîne est la **dernière** accolade fermante et non un
    comptage de profondeur : les deux champs de texte du contrat (`reponse`,
    `objectif`) portent de la prose écrite d'après un message humain, donc des
    accolades sont possibles *dans les chaînes*, où un compteur les prendrait pour
    de la structure et couperait l'objet en plein milieu. Le décodage tranche
    ensuite — un candidat mal formé lève et le suivant est essayé.
    """
    candidats = [texte.strip()]
    fence = _FENCE.search(texte)
    if fence is not None:
        candidats.append(fence.group("corps").strip())
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut != -1 and fin > debut:
        candidats.append(texte[debut : fin + 1])
    for candidat in candidats:
        try:
            return json.loads(candidat)
        except json.JSONDecodeError:
            continue
    return None


def _verdict_depuis(texte: str) -> _Verdict:
    """Le verdict lu dans la réponse du modèle — **un verdict illisible est un échange**.

    Rendre une erreur ferait d'une réponse hors contrat un 502, sur un canal qui
    est depuis #666 la seule porte d'entrée : le modèle a parlé, on affiche ce
    qu'il a dit, et on n'ouvre rien. C'est l'asymétrie du module appliquée à
    l'analyse plutôt qu'au jugement — ce qu'on ne comprend pas ne peut jamais
    valoir un accord.
    """
    charge = _objet_json(texte)
    if not isinstance(charge, Mapping):
        return _Verdict(nom=VERDICT_ECHANGE, reponse=texte.strip())
    nom = str(charge.get("verdict") or "").strip().lower()
    return _Verdict(
        nom=nom if nom in VERDICTS else VERDICT_ECHANGE,
        # Le texte brut en repli : un objet bien formé mais sans phrase à
        # afficher laisserait le fil muet, et `ServiceChat` refuse une réponse
        # vide (502). Mieux vaut montrer ce que le modèle a écrit.
        reponse=str(charge.get("reponse") or "").strip() or texte.strip(),
        objectif=str(charge.get("objectif") or "").strip(),
    )


#: Ce par quoi une réponse **entière en JSON** commence — l'ancien contrat (#685),
#: nu ou en bloc de code. Un premier caractère non blanc qui en fait partie fait
#: basculer la lecture en régime retenu : voir `_LectureDuFlux`.
_OUVERTURES_MACHINE = ("{", "`")


def _sans_echec(lecture: Callable[[], str]) -> str:
    """Le texte que `lecture` rend, `""` si elle lève — une sonde éclaire, elle ne décide pas.

    La règle de `_sans_equipe` (#1146), appliquée aux blocs du contexte : un
    projet illisible, une projection à moitié rejouée ou un dépôt d'agents
    disparu doivent coûter *un bloc de prompt*, jamais la réponse.
    """
    try:
        return lecture() or ""
    except Exception:  # noqa: BLE001 — un contexte manquant n'arrête jamais un fil
        return ""


def _bloc_des_lectures(lues: Sequence[Lecture]) -> str:
    """Ce que le tour de lecture a rapporté, en un bloc de prompt — `""` s'il n'a rien lu.

    Chaque lecture y porte **son libellé** (le même que l'étape affichée) puis
    son contenu : le modèle doit pouvoir citer *d'où* vient ce qu'il avance, et
    c'est la même ligne que la personne lit dans le fil. Deux formulations pour
    la même lecture les feraient se contredire au premier « où as-tu vu ça ? ».
    """
    if not lues:
        return ""
    blocs = [f"{lecture.libelle} :\n{lecture.contenu}" for lecture in lues]
    return "Ce que tu viens de lire dans le projet :\n\n" + "\n\n".join(blocs)


def _signature(demande: Demande) -> tuple[str, tuple[tuple[str, str], ...]]:
    """Ce qui fait qu'une lecture est **la même** qu'une autre : son verbe et ses arguments."""
    return (demande.outil, tuple(sorted(demande.arguments.items())))


def _avec_etapes(reponse: ReponseChat, etapes: tuple[EtapeFil, ...]) -> ReponseChat:
    """La même réponse, portant les étapes du tour — sans toucher au reste.

    Les deux voies de `produire` qui délèguent (`_proposer_recrutement`,
    `_ouvrir_un_run`) rendent une `ReponseChat` qu'elles composent entièrement :
    leur passer les étapes ferait traverser à chacune une donnée dont elle n'a
    rien à faire. On les pose donc **là où le tour est connu**, en un seul
    endroit, et `replace` garantit qu'aucun autre champ ne bouge.
    """
    return replace(reponse, etapes=etapes) if etapes else reponse


@dataclass(frozen=True)
class _Contexte:
    """Ce que le canal sait **avant** d'appeler le modèle, en quatre blocs (#1223).

    Les quatre sont lus à chaque message, dans la projection et le dépôt, et
    chacun disparaît quand il n'a rien à dire. Ils sont groupés ici parce que les
    **deux** appels d'un tour les reçoivent — celui qui décide des lectures et
    celui qui répond — et que les construire deux fois les ferait diverger d'un
    message à l'autre sur le même tour.
    """

    etat: str = ""
    equipe: str = ""
    attentes: str = ""
    faits: str = ""


class _LectureDuFlux:
    """Sépare, **au fil des incréments**, ce qui s'affiche de ce qui décide (#1222).

    Le modèle écrit sa réponse puis, en dernière ligne, `%%MAESTRO%%` suivi de
    l'objet JSON du verdict (`_MARQUEUR_VERDICT`, `_PROMPT_ORCHESTRATION`). Cette
    classe consomme les morceaux tels que le fournisseur les rend et décide, à
    chaque fois, ce qui peut partir à l'écran **maintenant** — sans jamais y
    laisser fuiter une accolade du bloc machine.

    Deux régimes, et le premier caractère non blanc tranche une fois pour toutes :

    - **prose** — le cas nominal. Les morceaux sont publiés au fur et à mesure,
      à ceci près qu'on retient toujours la queue qui pourrait être le **début**
      du marqueur (`%`, `%%M`, `%%MAES`…) : au plus `len(_MARQUEUR_VERDICT) - 1`
      caractères, rendus dès que la suite dément. Marqueur complet vu : tout ce
      qui suit est du JSON, plus rien n'est publié ;
    - **machine** — la réponse commence par `{` ou un bloc de code, c'est-à-dire
      par l'ancien contrat : un modèle qui répond en JSON malgré la consigne.
      **Rien n'est publié en flux** ; à la clôture, `_verdict_depuis` lit l'objet
      et sa `reponse` part en **un seul** incrément. C'est exactement le
      comportement d'avant ce lot, et c'est ce qui fait qu'un modèle désobéissant
      dégrade le direct sans jamais casser le fil.

    Elle ne rase rien et ne réordonne rien : `Redaction` tient l'invariant du
    contrat SSE (la concaténation des incréments *est* le texte final), et le lui
    reprendre ici en donnerait deux gardiens.
    """

    def __init__(self) -> None:
        self._brut: list[str] = []
        # Ce qui est publiable mais pas encore parti : la queue qui pourrait
        # amorcer le marqueur. Vide dès que la suite la dément.
        self._retenu = ""
        # `None` tant qu'aucun caractère non blanc n'est venu : le régime ne se
        # décide pas sur des espaces.
        self._machine: bool | None = None
        self._coupe = False

    def pousser(self, morceau: str) -> str:
        """Le morceau consommé ; rend ce qui peut s'afficher **maintenant** (souvent `""`)."""
        self._brut.append(morceau)
        if self._coupe:
            return ""
        if self._machine is None:
            candidat = (self._retenu + morceau).lstrip()
            if not candidat:
                self._retenu = ""
                return ""
            self._machine = candidat.startswith(_OUVERTURES_MACHINE)
            self._retenu = "" if self._machine else candidat
        elif self._machine:
            return ""
        else:
            self._retenu += morceau
        coupe = self._retenu.find(_MARQUEUR_VERDICT)
        if coupe != -1:
            # Le marqueur est là : ce qui le précède est la dernière prose, et
            # plus rien ne sortira — le reste du flux est l'objet du verdict.
            acquis, self._retenu, self._coupe = self._retenu[:coupe], "", True
            return acquis
        garde = _amorce_retenue(self._retenu)
        acquis = self._retenu[: len(self._retenu) - garde]
        self._retenu = self._retenu[len(self._retenu) - garde :]
        return acquis

    def conclure(self) -> tuple[str, _Verdict]:
        """Le dernier morceau à publier, et le verdict — le flux étant terminé.

        En régime prose, ce qui restait retenu n'était une amorce de marqueur que
        par hypothèse : le flux fini, l'hypothèse tombe et le texte part. Le
        verdict se lit alors dans ce qui suivait le marqueur.

        **Pas de marqueur du tout** : le texte est retenté comme l'ancien contrat,
        et seul le **verdict** en est repris — jamais sa `reponse`, qui est déjà
        à l'écran et qu'aucun flux ne reprend. C'est le cas du modèle qui préface
        son JSON d'une phrase : il a désobéi deux fois (ni la prose demandée, ni
        l'objet nu qu'on ne demande plus), et ce que le canal garde de lui est ce
        qui se rattrape — une demande approuvée continue d'ouvrir son run, ce que
        #685 tenait déjà. Le prix est visible et assumé : ce tour-là affiche ce
        que le modèle a écrit, JSON compris.

        Ni marqueur ni contrat lisible : **échange**. Un modèle qui oublie sa
        dernière ligne a quand même parlé, et ce qu'on ne comprend pas n'ouvre
        jamais rien — l'asymétrie du module (`_verdict_depuis`).
        """
        texte = "".join(self._brut)
        if self._machine:
            verdict = _verdict_depuis(texte)
            return verdict.reponse, verdict
        reste, self._retenu = self._retenu, ""
        avant, separe, apres = texte.partition(_MARQUEUR_VERDICT)
        lu = _verdict_depuis(apres if separe else texte)
        return reste, _Verdict(
            nom=lu.nom,
            # La réponse affichée est ce qui a été écrit **avant** le marqueur, et
            # non le champ `reponse` d'un objet JSON : c'est le sens du nouveau
            # contrat, et c'est aussi ce qui a déjà été publié.
            reponse=avant.strip(),
            objectif=lu.objectif,
        )


def _verdict_du_texte(texte: str) -> _Verdict:
    """Le contrat lu sur une réponse **entière** — le même lecteur que le flux.

    Un seul lecteur pour les deux voies (#1222) : `POST …/messages` n'attendrait
    pas un autre format que `POST …/flux`, et deux analyses du même contrat
    finiraient par diverger sur le cas qui compte — une prose qui traîne un
    `%%MAESTRO%%` que l'une retire et l'autre affiche.
    """
    lecture = _LectureDuFlux()
    lecture.pousser(texte)
    return lecture.conclure()[1]


def _amorce_retenue(texte: str) -> int:
    """Combien de caractères de queue pourraient être le **début** du marqueur.

    `0` quand la fin du texte ne ressemble à rien, sa longueur bornée par
    `len(_MARQUEUR_VERDICT) - 1` sinon : le plus long suffixe de `texte` qui soit
    un préfixe strict de `_MARQUEUR_VERDICT`. Sans cette retenue, un incrément
    coupé au milieu du marqueur — ce que fait n'importe quel fournisseur — en
    afficherait la première moitié avant que la seconde ne le dénonce.
    """
    for longueur in range(min(len(texte), len(_MARQUEUR_VERDICT) - 1), 0, -1):
        if _MARQUEUR_VERDICT.startswith(texte[-longueur:]):
            return longueur
    return 0


def _prompt(
    fil: Sequence[MessageChat], contexte: _Contexte, lectures: str = ""
) -> str:
    """Le fil rendu en prompt, précédé de ce que le canal sait de l'orchestration.

    La conversation passe par `chat.transcription` — la **même** mise en forme
    que le chat d'un agent, sources comprises — et ce qu'on sait vient en tête
    plutôt qu'en queue : la consigne de réponse ferme la transcription, et
    glisser un fait après elle le ferait lire comme une instruction de plus.

    Cinq blocs, dans l'ordre où l'on interroge : l'**état** (ce qui tourne, #683),
    l'**équipe** (qui peut le prendre, #1223), les **attentes** (ce qui est
    bloqué et pourquoi, #1223), les **faits des runs** (ce qu'ils ont fait,
    #1157), puis les **lectures** du tour (ce qu'on vient d'aller chercher,
    #1223). Les quatre premiers sont sus, le dernier est allé se chercher : il
    vient donc en dernier, au plus près de la conversation qu'il sert.
    """
    entete = [
        bloc
        for bloc in (
            f"État de l'orchestration : {contexte.etat}" if contexte.etat else "",
            contexte.equipe,
            contexte.attentes,
            contexte.faits,
            lectures,
        )
        if bloc
    ]
    conversation = transcription(fil)
    if not entete:
        return conversation
    return "\n\n".join([*entete, conversation])


class RepondeurOrchestration(RepondeurChat):
    """Le répondeur du fil global : il répond, et il peut ouvrir un run (#268, #685).

    `lanceur` ouvre le run approuvé (`LanceurRun`) ; sans lui, le canal reste
    conversationnel et le dit. `apercu` rend l'état de l'orchestration en une
    phrase, qui entre dans le prompt ; sans lui, le modèle juge sur le seul fil.
    `faits` (#1157) rend ce que les runs de ce fil et de ce projet ont **fait**,
    qui entre dans le même prompt juste après ; sans lui, le canal compte sans
    savoir raconter, c'est-à-dire exactement ce qu'il faisait avant ce lot.
    `provider` est le fournisseur du jugement — résolu **paresseusement** comme
    dans `RepondeurModele` : construire le répondeur ne coûte rien et ne lève
    aucune erreur de configuration, ce dont dépend `create_app`.

    Un lancement qui échoue **ne lève pas** : il se raconte dans le fil. Une
    exception se traduirait en `ReponseIndisponible`, donc en 502 sans trace — or
    la demande, elle, est déjà persistée, et son auteur a besoin de lire pourquoi
    rien ne s'est ouvert pour pouvoir reformuler. **Un juge injoignable non plus**
    (#686) : c'est le même invariant un cran plus tôt — l'empêchement porte alors
    sur le verdict lui-même, et le canal dit qu'il ne peut pas juger au lieu de
    laisser passer un 502.

    `equipe` et `recruteur` (#1146) sont la sonde et le geste du recrutement :
    le premier dit si le projet a quelqu'un pour prendre les tâches, le second
    crée l'équipe validée. Sans sonde, le canal propose des runs comme avant ce
    lot ; sans recruteur, il dit le manque sans proposer d'équipe.

    `consultation`, `roles` et `attentes` (#1223) sont ce qui fait que ce canal
    **sait** au lieu de supposer : le premier exécute les lectures que le modèle
    demande (fichiers du projet, détail complet d'un run), les deux autres
    mettent dans le prompt l'équipe réelle et ce qui attend quelqu'un. Sans
    `consultation`, aucun tour de lecture n'a lieu et le canal répond sur son
    seul contexte — c'est-à-dire exactement ce qu'il faisait avant ce lot ; sans
    `roles` ni `attentes`, les blocs correspondants disparaissent du prompt.
    """

    def __init__(
        self,
        *,
        lanceur: LanceurRun | None = None,
        apercu: ApercuOrchestration | None = None,
        faits: FaitsDesRuns | None = None,
        provider: ModelProvider | None = None,
        conducteur: ConducteurOutillage | None = None,
        sonde: SondeDuPoste | None = None,
        equipe: EquipeDuProjet | None = None,
        recruteur: RecruteurEquipe | None = None,
        consultation: Consultation | None = None,
        roles: EquipeDuFil | None = None,
        attentes: AttentesEnCours | None = None,
    ) -> None:
        self._lanceur = lanceur
        self._apercu = apercu
        self._faits = faits
        self._provider = provider
        self._equipe = equipe
        self._recruteur = recruteur
        self._consultation = consultation
        self._roles = roles
        self._attentes = attentes
        # Le modèle suit le fournisseur (#1173) : résolu avec lui depuis la
        # configuration, jamais épinglé. Un fournisseur **injecté** (les tests,
        # un câblage explicite) garde le modèle de la fiche.
        self._modele: str | None = None
        self._conducteur = conducteur or ConducteurOutillage()
        self._sonde = sonde

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """La réponse seule — `produire` est la voie complète (rattachement compris)."""
        return (await self.produire(agent, fil)).contenu

    async def produire(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        incrementer: Incrementeur | None = None,
        etapeur: Etapeur | None = None,
        projet_id: str | None = None,
    ) -> ReponseChat:
        """Répond au dernier message, et ouvre le run que l'utilisateur vient d'approuver.

        Un seul appel modèle (`_juger`), dont le verdict décide de la suite :
        seul `VERDICT_ACCORD` ouvre quelque chose. Une **proposition** n'ouvre
        rien — c'est tout le sujet de #685 —, et un message qui ne vient pas
        n'ouvre rien non plus, faute de verdict à rendre : le silence n'est pas un
        accord parce qu'il n'est pas un message.

        `projet_id` est le **projet de la fenêtre** d'où part la demande (#683).
        Il sert deux fois, et les deux le doivent pour la même raison : il
        **cadre** l'aperçu que le modèle reçoit, et il **rattache** le run que
        l'accord ouvre. Les dissocier ferait juger sur l'état d'un périmètre et
        travailler dans un autre.

        Sans verdict du tout — juge injoignable (#686) —, le canal dit la cause
        et s'arrête là. Le `LanceurRun` n'est alors pas atteint, et pas par une
        garde qu'il faudrait tenir : il n'existe qu'**un** chemin vers lui, et il
        part d'un verdict qui n'a pas été rendu.

        **Le tour de lecture précède le jugement** (#1223), et l'ordre est le
        sujet : l'orchestrateur va chercher ce qui lui manque, *puis* répond avec
        ce qu'il a lu. Ce qu'il a lu se **voit** au passage (`etapeur`), donc
        avant le premier mot de la réponse — c'est l'ordre réel des choses, et
        c'est ce qui distingue « il a regardé » d'« il a l'air sûr de lui ». Un
        tour de lecture qui échoue ne lève pas : il rend moins de lectures, et la
        réponse se fait avec ce qu'elle a — la seule chose qu'un empêchement de
        lecture ne doit jamais coûter est la réponse elle-même.

        **La réponse s'écrit pendant qu'elle vient** (#1222), sauf dans un cas,
        et ce cas se connaît **avant** l'appel : un projet sans agent verra sa
        réponse *remplacée* par la proposition d'équipe (#1146), et on ne
        remplace pas ce qui est déjà à l'écran. Le régime se décide donc sur
        `_sans_equipe`, qui ne dépend que du projet — jamais sur le verdict, qui
        arrive trop tard pour décider s'il fallait le montrer. Tout le reste
        s'**ajoute** derrière la réponse (l'avertissement d'un fil sans
        exécution, la cause d'un lancement en échec) et ne demande rien.
        """
        # Le texte du juge sera peut-être remplacé (#1146) : la question se pose
        # **avant** l'appel, et sa réponse décide aussi du régime de publication.
        retenue = self._sans_equipe(projet_id)
        redaction = Redaction(incrementer)
        contexte = self._contexte(fil, projet_id)
        lectures, etapes = await self._consulter(agent, fil, contexte, projet_id, etapeur)
        try:
            verdict = await self._juger(
                agent,
                fil,
                projet_id,
                contexte,
                lectures,
                None if retenue else redaction,
            )
        except _JugeInjoignable as injoignable:
            # Ce qui a pu être publié reste à l'écran — il a été dit, le retirer
            # n'est pas au pouvoir de ce canal — et la cause s'écrit à sa suite.
            await redaction.ecrire(f" {injoignable}" if redaction.texte else str(injoignable))
            return ReponseChat(contenu=redaction.texte, etapes=etapes)
        if (
            verdict.nom in (VERDICT_PROPOSITION, VERDICT_ACCORD)
            and verdict.objectif
            and self._lanceur is not None
            and retenue
        ):
            # Personne pour prendre les tâches (#1146) : ni la proposition ni
            # l'accord ne tiennent, et le texte du juge — « je lance ? », « c'est
            # parti » — non plus. Il est remplacé **avant** d'être écrit, par la
            # phrase qui dit pourquoi et propose l'équipe.
            return _avec_etapes(
                await self._proposer_recrutement(redaction, verdict.objectif, projet_id),
                etapes,
            )
        if not redaction.texte:
            # Le flux l'a déjà écrite quand il a servi ; sinon, elle part d'un
            # bloc — un juge retenu, ou un modèle qui a répondu en JSON.
            await redaction.ecrire(verdict.reponse)
        if verdict.nom == VERDICT_ACCORD:
            # Un accord **tapé** ne porte aucune borne : le juge rend un
            # objectif, pas un formulaire. Les bornes viennent du geste
            # (`trancher_cadrage`), seul chemin où un écran a pu les poser.
            return _avec_etapes(
                await self._ouvrir_un_run(
                    redaction, verdict.objectif, projet_id, AUCUNE_BORNE, contexte_du_fil(fil)
                ),
                etapes,
            )
        if verdict.nom == VERDICT_PROPOSITION and self._lanceur is None:
            # Prévenir **avant** le « oui » : proposer un run qu'on ne pourra pas
            # ouvrir ferait attendre l'utilisateur pour un refus au tour suivant.
            await redaction.ecrire(
                " ⚠ Aucune exécution n'est branchée sur ce fil pour l'instant : je "
                "peux en parler, pas encore l'ouvrir."
            )
        # La demande **sort de la phrase** (#943) : l'objectif proposé voyage sur
        # le message, donc l'écran peut en faire un geste au lieu d'attendre une
        # réponse tapée. Rien n'est posé quand il n'y a pas de quoi trancher —
        # sans objectif il n'y aurait rien à lancer, et sans lanceur le message
        # vient de dire que le run n'ouvrirait pas : offrir le bouton serait
        # promettre deux fois ce qu'on annonce impossible une ligne plus haut.
        propose = (
            verdict.objectif
            if verdict.nom == VERDICT_PROPOSITION and self._lanceur is not None
            else ""
        )
        return ReponseChat(contenu=redaction.texte, proposition=propose, etapes=etapes)

    async def trancher_cadrage(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        approuve: bool,
        objectif: str,
        projet_id: str | None = None,
        bornes: BornesRun = AUCUNE_BORNE,
    ) -> ReponseChat:
        """Exécute la décision prise **au geste** sur une proposition (#943).

        Aucun appel modèle ici, et c'est le sujet : la question que le juge
        tranche — « ce message est-il un accord ? » — n'a plus lieu d'être quand
        l'accord est un clic. Le chemin vers le lanceur reste **unique** dans son
        esprit : il part d'une décision explicite de l'utilisateur, jamais d'un
        silence ni d'un texte reconnu. Ce qui change est la façon dont la
        décision arrive, pas ce qui l'autorise.

        `objectif` est ce qui **part** : la proposition telle quelle, ou la
        version amendée à l'écran. Elle ne peut pas traverser un tour de
        jugement — le contrat demande au juge de recopier mot pour mot *sa*
        proposition —, donc la corriger exige ce chemin-ci ou ne serait pas
        possible du tout.

        `bornes` (#990) est ce que l'écran a posé **au moment de lancer** : les
        quatre garde-fous de `lancer`, et rien d'autre. Elles passent par ici
        pour la raison exacte qui y fait passer l'objectif amendé — un tour de
        jugement les perdrait, le juge ne rendant qu'un objectif. Non posées,
        c'est `AUCUNE_BORNE`, donc le run d'avant ce ticket.

        Un refus n'ouvre rien et ne solde rien : le fil garde la proposition,
        l'utilisateur reformule. C'est la symétrie du brief refusé (§6.10) à
        ceci près qu'il n'y a pas encore de run à annuler. Les bornes y sont
        ignorées, comme l'objectif : il n'y a rien à borner.
        """
        redaction = Redaction(None)
        if not approuve:
            await redaction.ecrire(
                "Entendu, je n'ouvre rien. Dites-moi ce qu'il faut changer et je "
                "vous proposerai autre chose."
            )
            return ReponseChat(contenu=redaction.texte)
        if self._sans_equipe(projet_id):
            # L'équipe a pu disparaître entre la proposition et le clic, ou la
            # proposition précéder ce lot (#1146) : la garde du verdict ne suffit
            # pas, et « c'est parti » n'a pas encore été écrit.
            return await self._proposer_recrutement(redaction, objectif.strip(), projet_id)
        # La seule phrase du code sur un lancement qui réussit, et elle n'est
        # **accolée à rien** (#1222) : ici aucun modèle n'a parlé — l'accord est un
        # clic —, il faut donc bien que quelque chose accuse réception, et le fil
        # ne se persiste pas vide (`ServiceChat._persister_reponse`). Ce qui a été
        # retiré est ce qui *suivait* : l'identifiant du run et le régime des
        # bornes, que le message et le geste portent déjà (`_ouvrir_un_run`).
        await redaction.ecrire("C'est parti.")
        return await self._ouvrir_un_run(
            redaction, objectif.strip(), projet_id, bornes, contexte_du_fil(fil)
        )

    async def recruter(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        demande: DemandeRecrutement,
        approuve: bool,
        roles: Sequence[RoleValide],
        proposition_id: str = "",
    ) -> ReponseChat:
        """Crée l'équipe validée, puis **reprend** la demande d'origine (#1146).

        Aucun appel modèle : la validation est un clic, et la suite se déduit.
        Trois issues, et aucune n'ouvre de run — un run part sur son propre
        accord, jamais par ricochet d'un recrutement :

        - **déclinée** — rien n'est créé, rien n'est ouvert, et la phrase dit
          pourquoi le run n'est pas proposé à la place : il n'aurait personne ;
        - **créée** — la réponse dit qui a été recruté, puis **repropose** le run
          sur l'objectif que la demande portait (`ReponseChat.proposition`) : la
          demande de cadrage de #943 prend le relais, avec ses bornes ;
        - **refusée** (`EquipeRefusee`, projet illisible…) — rien n'a été créé
          (la création vérifie tout avant d'écrire), la cause est dite, et la
          demande est **reposée** telle quelle pour qu'on puisse corriger et
          valider à nouveau sans retaper sa demande.

        `demande.projet_id` est le projet où l'équipe naît : celui dont la
        demande parlait, relu du fil par le service — jamais la fenêtre.

        ⚠ **Une demande née pendant un run ne se conclut pas pareil** (#1227,
        `DemandeRecrutement.pendant_un_run`), et c'est la seule différence : le run
        tourne déjà, donc il n'y a rien à reproposer — ni sur un accord (lui rendre
        son objectif ouvrirait un second run sur le même travail), ni sur un refus
        (le run continue, et c'est ce qu'on dit). Ce que le run attend, lui, est la
        **décision**, publiée sur le bus par la route qui a reçu le geste
        (`maestro.controltower.renfort`) : ce module n'en sait rien et n'a pas à en
        savoir plus — il écrit dans le fil, comme pour les deux autres issues.
        """
        redaction = Redaction(None)
        if not approuve:
            await redaction.ecrire(
                _REFUS_PENDANT_UN_RUN.format(role=demande.role or "ce rôle")
                if demande.pendant_un_run
                else "Entendu : je ne recrute personne, et je n'ouvre pas de run — "
                "sans équipe, personne n'en prendrait les tâches. L'équipe se crée "
                "aussi depuis les écrans d'agents du projet ; redites-moi votre "
                "demande quand elle sera là."
            )
            return ReponseChat(contenu=redaction.texte)
        if self._recruteur is None:
            await redaction.ecrire(
                "Je ne peux pas créer d'équipe depuis ce fil : aucun recrutement n'y "
                "est branché. Créez-la depuis les écrans d'agents du projet."
            )
            return ReponseChat(contenu=redaction.texte)
        try:
            rapport = await self._recruteur(demande.projet_id, roles, proposition_id)
        except Exception as echec:
            # Nommé dans le fil plutôt que levé, comme un lancement qui échoue :
            # le geste est déjà écrit, et c'est la cause qui permet de corriger.
            await redaction.ecrire(
                f"Je n'ai créé aucun agent : {echec}. Ajustez l'équipe ci-dessous "
                "puis validez-la à nouveau — ou remettez à plus tard."
            )
            return ReponseChat(contenu=redaction.texte, recrutement=demande)
        await redaction.ecrire(f"Équipe créée : {_composition(rapport)}. ")
        if demande.pendant_un_run:
            # Le run tourne déjà : il n'y a rien à proposer, seulement à dire que
            # l'attente est levée. Lui reproposer son propre objectif ouvrirait un
            # second run sur le même travail — et `trancher_cadrage` n'a aucun
            # moyen de savoir qu'il ferait double emploi.
            await redaction.ecrire(
                "Le run reprend avec l'équipe complétée : les tâches qui demandaient "
                "ces compétences iront au nouveau rôle."
            )
            return ReponseChat(contenu=redaction.texte)
        if self._lanceur is None:
            await redaction.ecrire(
                "Je ne peux pas encore ouvrir de run depuis ce fil : aucune exécution "
                "n'y est branchée."
            )
            return ReponseChat(contenu=redaction.texte)
        await redaction.ecrire(
            f"Je reprends votre demande : « {demande.objectif} ». Je lance ?"
        )
        return ReponseChat(contenu=redaction.texte, proposition=demande.objectif)

    async def ouvrir_questionnaire(
        self, agent: Agent, fil: Sequence[MessageChat]
    ) -> ReponseChat:
        """Ouvre — ou reprend — le questionnaire d'outillage d'un projet neuf (#1031).

        Ce fil-ci le porte, et pas un autre, parce que c'est la seule porte d'entrée
        du produit (#666) : un questionnaire posé dans un second fil demanderait de
        quitter la conversation où l'on vient de déclarer son projet, ce que « sans
        formulaire à part » refuse.

        **Aucun appel modèle**, comme pour le geste de cadrage et pour une raison de
        plus : ici la suite du questionnaire est une fonction pure de ce que le fil
        porte (`maestro.outillage.questionnaire`). Le juge de ce module n'est consulté que
        sur ce qu'il est seul à savoir faire — dire si un message est une demande de
        travail.
        """
        return await self._conducteur.ouvrir(fil)

    async def repondre_question(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        *,
        question: QuestionOutillage,
        valeur: str,
    ) -> ReponseChat:
        """Enchaîne sur le geste : ce qu'il déduit, puis la question suivante (#1031)."""
        return await self._conducteur.repondre(fil, question, valeur)

    async def _juger(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        projet_id: str | None,
        contexte: _Contexte | None = None,
        lectures: str = "",
        redaction: Redaction | None = None,
    ) -> _Verdict:
        """L'appel modèle — fournisseur résolu au premier usage (import local, comme #84).

        Le prompt système est celui de la fiche (`_PROMPT_ORCHESTRATION`), qui
        porte le contrat de la réponse ; le prompt d'utilisateur est le fil,
        précédé du contexte (#683, #1157, #1223) puis des **lectures** du tour
        (#1223) — ce que l'orchestrateur vient d'aller chercher dans le projet,
        et qui doit porter sa réponse plutôt qu'un renvoi vers un écran.
        Aucun `PlaybookStore` ici, contrairement à `RepondeurModele` :
        l'orchestration n'est pas au catalogue, donc n'a pas de playbook éditable
        — et le contrat de sortie n'est pas un texte que l'UI doit pouvoir
        réécrire.

        **Avec une `redaction`, la réponse s'écrit pendant qu'elle est jugée**
        (#1222) : l'appel passe par `generate_stream` et `_LectureDuFlux` publie
        la prose au fur et à mesure, gardant pour elle la dernière ligne qui porte
        le verdict. Le jugement n'est pas déplacé d'un cran — il reste rendu par
        ce même appel —, c'est l'**ordre** dans lequel le modèle rend ses deux
        moitiés qui a changé, et c'est tout ce qu'il fallait pour que la première
        n'attende plus la seconde. Sans `redaction`, l'appel reste celui d'avant
        (`generate`, texte entier) : c'est ce que `repondre` demande, et le seul
        chemin d'un appelant qui n'a rien à afficher au fil de l'eau.

        Les trois façons de n'avoir **aucun** verdict lèvent `_JugeInjoignable`
        plutôt que de remonter (#686), et la **famille** de la cause se lit à
        l'endroit de l'échec : résoudre le fournisseur ne touche à aucun réseau,
        donc ce qui casse là est un réglage ; la génération, elle, part dehors,
        donc ce qui casse là est une indisponibilité. Aucune chaîne n'est examinée
        pour trancher — c'est la règle de `controltower.causes`, tenue ici par la
        structure plutôt que par un `isinstance`. Un flux qui **casse en cours**
        est de cette seconde famille : ce qui a déjà été publié reste à l'écran et
        la cause s'écrit à sa suite, parce que ce canal ne lève pas (voir la
        classe) — et non parce que l'échec serait moins grave.

        Une **réponse vide** est rangée avec les indisponibilités et non avec les
        verdicts illisibles, et la frontière est nette : un texte hors contrat est
        un modèle qui a *parlé* (on l'affiche, on n'ouvre rien — `_verdict_depuis`),
        un texte vide est un modèle qui n'a rien dit, donc rien à afficher, donc
        le 502 « réponse vide » de `ServiceChat` que ce lot supprime.

        Un échec de résolution **ne se mémorise pas** : `self._provider` reste
        `None`, si bien que le message suivant retente. C'est ce qui rend la
        phrase de réparation vraie — corriger la configuration suffit, sans
        redémarrer la Control Tower.
        """
        if self._provider is None:
            from maestro.providers.factory import modele_du_canal, provider_from_settings

            try:
                fournisseur = provider_from_settings()
                # Le modèle avec le fournisseur (#1173) : `claude-sonnet-5` n'a de
                # sens que chez Claude. Un modèle manquant est un réglage absent,
                # donc de la même famille que le fournisseur manquant, et il se dit
                # comme tel.
                self._modele = modele_du_canal(agent.modele, fournisseur)
                self._provider = fournisseur
            except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
                raise _JugeInjoignable(
                    f"aucun fournisseur de modèle n'est utilisable "
                    f"({cause_lisible(echec)})",
                    await reparation_configuration(self._sonde),
                ) from echec
        # Le contexte est **celui du tour** quand `produire` l'a construit : les
        # deux appels d'un même message doivent voir le même état, faute de quoi
        # le second jugerait sur une projection que le premier ne connaissait
        # pas. `repondre`, qui n'en construit aucun, le fait relire ici.
        vu = contexte if contexte is not None else self._contexte(fil, projet_id)
        prompt = _prompt(fil, vu, lectures)
        modele = self._modele or agent.modele
        if redaction is None:
            try:
                texte = await self._provider.generate(
                    prompt, model=modele, system_prompt=agent.prompt_systeme
                )
            except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
                raise _JugeInjoignable(
                    f"le fournisseur de modèle n'a pas répondu ({cause_lisible(echec)})",
                    _REPARATION_PASSAGERE,
                ) from echec
            if not (texte or "").strip():
                raise _JugeInjoignable(
                    "le fournisseur de modèle a rendu une réponse vide",
                    _REPARATION_PASSAGERE,
                )
            return _verdict_du_texte(texte)
        lecture = _LectureDuFlux()
        try:
            async for morceau in self._provider.generate_stream(
                prompt, model=modele, system_prompt=agent.prompt_systeme
            ):
                await redaction.ecrire(lecture.pousser(morceau))
        except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
            raise _JugeInjoignable(
                f"le fournisseur de modèle n'a pas répondu ({cause_lisible(echec)})",
                _REPARATION_PASSAGERE,
            ) from echec
        dernier, verdict = lecture.conclure()
        await redaction.ecrire(dernier)
        if not verdict.reponse.strip():
            raise _JugeInjoignable(
                "le fournisseur de modèle a rendu une réponse vide",
                _REPARATION_PASSAGERE,
            )
        return verdict

    def _contexte(self, fil: Sequence[MessageChat], projet_id: str | None) -> _Contexte:
        """Ce que le canal sait de l'orchestration, lu **une fois par message** (#1223).

        Les quatre lectures passent par le **même** projet de fenêtre, donc par la
        même portée (#277) : un prompt qui mélangerait deux périmètres ferait
        compter ce qu'il ne raconte pas. Chacune est facultative — un répondeur
        construit sans elle rend simplement un bloc vide, et le prompt s'en passe.

        Aucune ne lève : une sonde qui casse **éclaire**, elle ne décide de rien
        (même règle que `_sans_equipe`). Un bloc manquant coûte une réponse moins
        informée ; une exception coûterait la réponse.
        """
        # Les quatre sondes sont liées à des variables locales avant d'être
        # appelées : c'est ce qui permet de les passer à `_sans_echec` sans
        # refaire le test d'existence à l'intérieur de chaque lambda.
        apercu, roles, attentes, faits = (
            self._apercu,
            self._roles,
            self._attentes,
            self._faits,
        )
        return _Contexte(
            etat=_sans_echec(lambda: apercu(projet_id)) if apercu else "",
            equipe=_sans_echec(lambda: roles(projet_id)) if roles else "",
            attentes=_sans_echec(lambda: attentes(projet_id)) if attentes else "",
            # Les faits des runs (#1157), lus sur le **même** périmètre que
            # l'aperçu et sur les runs que ce fil a ouverts : c'est ce qui permet
            # de répondre « pourquoi le run a échoué ? » sans envoyer vers un écran.
            faits=(
                _sans_echec(lambda: faits(projet_id, runs_du_fil(fil))) if faits else ""
            ),
        )

    async def _consulter(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        contexte: _Contexte,
        projet_id: str | None,
        etapeur: Etapeur | None,
    ) -> tuple[str, tuple[EtapeFil, ...]]:
        """Le **tour de lecture** : ce que l'orchestrateur va chercher avant de répondre.

        Un appel modèle au contrat étroit (`_PROMPT_CONSULTATION`) : des lignes
        `%%LIRE%% {…}`, ou le mot `RIEN`. Ce qu'il demande est exécuté par
        `consultation` — quatre verbes en lecture seule, bornés à la racine du
        projet, secrets exclus —, publié en **étape** dès que la lecture est
        faite, puis rendu au juge dans son prompt.

        **Deux tours au plus** (`_TOURS_DE_LECTURE`), et le second existe pour la
        piste que le premier ouvre : lister le projet, puis lire le README qu'on
        y a vu. Le tour s'arrête dès qu'un appel ne demande plus rien — c'est le
        cas courant, et il coûte quelques jetons.

        **Rien de tout cela ne peut coûter la réponse.** Sans `consultation`, le
        tour n'a pas lieu. Un fournisseur qui ne répond pas, un texte hors
        contrat, une lecture qui échoue : on rend ce qu'on a, et le juge répond
        avec le contexte seul — c'est-à-dire exactement le fil d'avant ce lot. Ce
        canal ne lève pas (#686), et une préparation ne peut pas être plus fatale
        que ce qu'elle prépare.

        Un **coût est payé** ici, et il s'assume : un appel modèle de plus par
        message, court dans les deux sens (le prompt est celui du juge, la
        réponse tient en une ligne). C'était le prix de la propriété que le
        ticket demande — *répondre à partir de ce qu'on lit* — et le mesurer sur
        le seul cas qui n'en profite pas (« oui ») reviendrait à ne jamais lire.
        """
        if self._consultation is None or not self._resolu(agent):
            return "", ()
        lues: list[Lecture] = []
        etapes: list[EtapeFil] = []
        # Une même lecture ne se fait qu'**une fois** par message. Un modèle qui
        # redemande au second tour ce qu'il a déjà lu au premier — le cas le plus
        # courant quand il n'a pas trouvé sa réponse — paierait sinon deux fois la
        # même lecture et afficherait deux fois la même ligne. Ce n'est pas un
        # garde-fou : c'est le doublon que le fil montrerait.
        faites: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
        for tour in range(_TOURS_DE_LECTURE):
            demandes = await self._demandes(agent, fil, contexte, lues)
            neuves = [d for d in demandes if _signature(d) not in faites]
            if not neuves:
                break
            for demande in neuves[:LECTURES_PAR_TOUR]:
                faites.add(_signature(demande))
                lecture = await self._lire(demande, projet_id)
                lues.append(lecture)
                etape = EtapeFil(libelle=lecture.libelle, detail=lecture.contenu)
                etapes.append(etape)
                if etapeur is not None:
                    await etapeur(etape)
            if tour + 1 >= _TOURS_DE_LECTURE:
                break
        return _bloc_des_lectures(lues), tuple(etapes)

    async def _demandes(
        self,
        agent: Agent,
        fil: Sequence[MessageChat],
        contexte: _Contexte,
        lues: Sequence[Lecture],
    ) -> tuple[Demande, ...]:
        """Ce que le modèle demande à lire — `()` quand il ne demande rien, ou qu'il rate.

        L'échec est **silencieux par construction** : un fournisseur muet ne doit
        pas empêcher de répondre, seulement de lire. C'est la seule place du
        module où une exception se ravale sans rien écrire au fil, et elle le
        peut parce que ce qu'elle protège est une préparation, jamais la réponse.
        """
        if self._provider is None:
            return ()
        try:
            texte = await self._provider.generate(
                _prompt(fil, contexte, _bloc_des_lectures(lues)),
                model=self._modele or agent.modele,
                system_prompt=_PROMPT_CONSULTATION,
            )
        except Exception:  # noqa: BLE001 — lire est facultatif, répondre ne l'est pas
            return ()
        return demandes_de(texte or "")

    async def _lire(self, demande: Demande, projet_id: str | None) -> Lecture:
        """Exécute une lecture — un empêchement est une `Lecture` qui le dit, jamais une levée."""
        if self._consultation is None:  # pragma: no cover - garde de type, cf. `_consulter`
            return Lecture(libelle="N'a rien pu lire", contenu="Aucune lecture branchée.")
        try:
            return await self._consultation(demande, projet_id)
        except Exception as echec:  # noqa: BLE001 — cf. `consultation.Consultations.executer`
            return Lecture(
                libelle=f"N'a pas pu exécuter « {demande.outil} »",
                contenu=f"La lecture a échoué : {echec}.",
            )

    def _resolu(self, agent: Agent) -> bool:
        """Résout le fournisseur **sans lever** — le tour de lecture s'en passe s'il manque.

        `_juger` le résout aussi, et c'est lui qui **dit** l'empêchement (#686) :
        ici on se tait, parce qu'un fournisseur absent n'a pas deux causes ni deux
        phrases, et que celle du juge arrive une ligne plus loin.
        """
        if self._provider is not None:
            return True
        from maestro.providers.factory import modele_du_canal, provider_from_settings

        try:
            fournisseur = provider_from_settings()
            self._modele = modele_du_canal(agent.modele, fournisseur)
            self._provider = fournisseur
        except Exception:  # noqa: BLE001 — l'empêchement se dit au juge, pas ici
            return False
        return True

    def _sans_equipe(self, projet_id: str | None) -> bool:
        """Le projet de la fenêtre n'a **personne** pour prendre les tâches (#1146).

        Vrai seulement sur un compte **nul** et certain : sans projet, sans sonde,
        ou quand la sonde ne sait pas (`None`) — et même quand elle lève —, le
        canal garde sa conduite d'avant ce lot. Une sonde qui se trompe dans le
        doute bloquerait des runs légitimes ; celle-ci ne parle que de ce qu'elle
        a compté.
        """
        if not projet_id or self._equipe is None:
            return False
        try:
            return self._equipe(projet_id) == 0
        except Exception:  # noqa: BLE001 — la sonde éclaire, elle ne décide de rien
            return False

    async def _proposer_recrutement(
        self, redaction: Redaction, objectif: str, projet_id: str | None
    ) -> ReponseChat:
        """Propose l'équipe au lieu du run — la demande et la phrase qui dit pourquoi.

        Ne s'appelle que derrière `_sans_equipe`, donc avec un projet. Sans
        recruteur, le constat est dit sans demande : poser une demande à laquelle
        aucun geste ne peut répondre serait promettre ce qu'on sait impossible.
        """
        if self._recruteur is None or not projet_id:
            await redaction.ecrire(_PHRASE_SANS_RECRUTEUR.format(objectif=objectif))
            return ReponseChat(contenu=redaction.texte)
        await redaction.ecrire(_PHRASE_RECRUTEMENT.format(objectif=objectif))
        return ReponseChat(
            contenu=redaction.texte,
            recrutement=DemandeRecrutement(objectif=objectif, projet_id=projet_id),
        )

    async def _ouvrir_un_run(
        self,
        redaction: Redaction,
        objectif: str,
        projet_id: str | None,
        bornes: BornesRun = AUCUNE_BORNE,
        contexte_sources: str = "",
    ) -> ReponseChat:
        """Ouvre le run de `objectif`, dans son projet, et le rattache à la réponse.

        `contexte_sources` (#1172) est ce que la conversation a joint, déjà lu
        (`contexte_du_fil`). Il part avec l'objectif, sans quoi le brief se rédige
        sans la spec dont on vient de parler.

        `objectif` est **la reformulation approuvée** et jamais le dernier message
        (#685) : la méthode ne reçoit pas le fil, donc un « oui » ne peut pas
        partir comme objectif de run même par accident. Un objectif vide est un
        verdict qui se contredit — accord sans rien à lancer : on le dit et on
        n'ouvre rien, plutôt que de retomber sur le message brut.

        Le run **hérite du projet de la fenêtre** (#683) : il apparaît donc dans
        la liste des runs de ce projet et s'ouvre en détail, là où un run sans
        projet n'entrait dans la vue d'aucun (`PorteeProjet.retient`) — c'est-à-dire
        nulle part, le chat étant depuis #666 la seule porte d'entrée. Rien n'est
        deviné : `projet_id` est ce que la fenêtre a envoyé, `None` quand elle n'a
        pas de projet, et le run part alors sans projet comme avant ce lot.

        **Un lancement qui réussit n'ajoute plus un mot** (#1222). Ce qui s'y
        écrivait — « Run X ouvert, statut « En cours » — aucune borne : le run ira
        jusqu'au bout. Les tâches apparaîtront au tableau de bord… » — venait se
        coller derrière la phrase du modèle, et la personne l'a trouvé robotique :
        deux voix dans une même bulle, dont la seconde récite. Les faits n'ont pas
        disparu, ils ont retrouvé leur place :

        - l'**identifiant du run** voyage sur `ReponseChat.run_id`, donc sur le
          message persisté, donc sous la bulle (`Suite`, docs/05 §2.9) — il y
          survit au rechargement, ce qu'une phrase ne fait pas mieux ;
        - les **bornes réellement posées** sont écrites par le geste qui les a
          posées (`chat._geste_de_cadrage`), à l'endroit où quelqu'un les a
          choisies. Celles qu'on n'a pas posées n'ont rien à dire ici : le régime
          s'annonce **au moment de lancer**, sur la carte de cadrage qui le
          récapitule dans les deux sens (#990) — le redire après coup n'était plus
          un choix affiché, c'était un gabarit.

        Restent les phrases d'**empêchement** ci-dessous, et elles ne sont pas du
        même ordre : rien ne s'est ouvert, le modèle ne peut pas le savoir, et
        personne d'autre que ce code ne peut le dire.
        """
        if not objectif:
            await redaction.ecrire(
                " Je n'ai pas retrouvé l'objectif que vous venez d'approuver : "
                "redites-moi le travail à faire et je vous le proposerai à nouveau."
            )
            return ReponseChat(contenu=redaction.texte)
        if self._lanceur is None:
            await redaction.ecrire(
                " Je ne peux pas ouvrir de run depuis ce fil : aucune exécution n'y "
                "est branchée. La demande est bien enregistrée ici."
            )
            return ReponseChat(contenu=redaction.texte)

        try:
            resume = await self._lanceur(objectif, projet_id, bornes, contexte_sources)
        except Exception as echec:
            # Nommé dans le fil plutôt que levé : voir la classe. Un objectif
            # refusé (vide, plafond hors bornes) et un moteur qui ne démarre pas
            # se lisent tous deux ici, avec leur cause.
            await redaction.ecrire(f" Le lancement a échoué : {echec}")
            return ReponseChat(contenu=redaction.texte)

        return ReponseChat(contenu=redaction.texte, run_id=str(resume.get("run_id", "")))
