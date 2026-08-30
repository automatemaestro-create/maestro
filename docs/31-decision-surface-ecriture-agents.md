# 31 — La surface d'écriture des agents pendant un run : note de décision

> Ticket #354. Décision datée du **2026-08-28**, sur `origin/main` à `1bef04a`.
>
> **Une règle, appliquée cinq fois : un agent écrit ce qu'il *observe*, jamais ce que le plan
> *décide*.** Deux verbes ouverts — **déclarer un blocage**, **écrire à un pair** ; un troisième
> déjà ouvert et à ne pas refaire — l'**avancement fin** (#489) ; trois refusés — **créer une
> tâche**, **changer le statut ou le propriétaire**, **recruter** —, les trois pour le même motif :
> ils amendent le plan validé, et le plan ne s'amende que par un point d'approbation humain (**D5**).
>
> Deux questions du ticket ne se posaient plus dans les termes où il les posait. Le **canal était
> déjà choisi** : un serveur MCP in-process est monté sur chaque session depuis #582. L'**identité
> d'instance est écartée** : `tache_id` la porte déjà, et mieux.

---

## 1. Ce que le ticket croyait absent, et qui ne l'est plus

Le ticket est né de la veille AionUi (#352), le 2026-08-17. Il pose son constat ainsi :

> Un agent de Maestro ne peut ni créer une tâche, ni écrire à un pair, ni dire qu'il est bloqué :
> il produit un livrable et se tait.

**Les trois verbes cités sont bien fermés — c'est la conclusion qui ne l'est pas.** « Il produit un
livrable et se tait » était vrai le jour où la veille a été écrite ; le chantier #573, livré
entre-temps, et deux mécanismes plus anciens le démentent. Le relever n'est pas un point de détail :
c'est ce qui fait passer la décision de « **faut-il ouvrir une surface d'écriture ?** » à « **la
surface existe — quels verbes y ajoute-t-on ?** », et ça change le prix de tout ce qui suit.

**Quatre écritures traversent un run aujourd'hui, en production. Trois sont à l'initiative de
l'agent**, et le ticket n'en cite aucune.

| Quoi | Depuis | Le geste | À l'initiative de |
| --- | --- | --- | --- |
| Un **ticket externe** posé sur sa tâche | #187 | `consigne_ticket` → étape `<tache>:ticket` | **l'agent**, via le MCP de son outil |
| Sa **checklist** d'avancement | #489 | `consigne_detail`, via `on_etapes` | **l'agent**, via ses `TodoWrite` |
| Une **demande d'arbitrage** sur un acte | #582 | outil MCP `demander_arbitrage(raison)` | **l'agent**, et l'appel attend la réponse |
| Un **message** inter-agents | #44 | `consigne_message` → étape `<tache>:message` | la **machinerie** — handoff ou chat |

Les **trois premières lignes de ce tableau donnent la forme qu'on reconduit**, et les deux
premières la donnent entière : **une étape de journal → le pont Control Tower → un événement → la
projection**. Elle a trois propriétés qu'on ne retrouvera pas par hasard :

- **elle est en ajout seul.** Un journal ne se réécrit pas. Aucune de ces écritures ne retire ni ne
  contredit quoi que ce soit ;
- **le moteur n'en sait rien.** [`maestro/references.py:141`](../maestro/references.py) le dit pour
  le ticket : « le moteur n'est donc **jamais** au courant de l'outil de ticketing : il ne voit
  qu'une étape de journal de plus ». Le graphe du plan n'est pas touché ;
- **elle est gratuite au grand livre.** L'étape porte un `StepUsage()` vide — « rien n'a été dépensé
  pour la poser — elle n'entre pas au grand livre » (même fichier, l. 143).

La **troisième ligne**, elle, apporte l'autre moitié : le **canal**. `demander_arbitrage` n'est pas
une étape de journal mais un **outil MCP servi à l'agent** par un serveur **in-process** monté sur
sa session ([`maestro/providers/arbitrage.py:10`](../maestro/providers/arbitrage.py)) — un
aller-retour, pas une trace. C'est le §4, et c'est ce qui rend la question du canal sans objet.

**Ce qui reste fermé est donc précisément ce que le ticket demande d'ouvrir, mais pour une raison
plus étroite qu'il ne le dit.** Un agent sait écrire ce qu'il **découvre** (un ticket), ce qu'il
**prévoit** (sa checklist) et ce qu'il **s'apprête à faire** (un arbitrage). Il ne sait pas écrire
ce qu'il **subit** — qu'il bute, ou un mot à un pair. Et pour le mot au pair, le constat du ticket
est exact au pied de la lettre : les deux seuls appelants de `mailbox.publish` sont le handoff
([`handoff.py:124`](../maestro/messaging/handoff.py)) et le chat
([`chat.py:783`](../maestro/controltower/chat.py)), c'est-à-dire la machinerie.

⚠ **Une seconde prémisse du ticket ne résiste pas à la lecture, et elle compte pour la suite.** Le
ticket range parmi nos briques existantes « le tableau noir léger de `maestro.engine.loop` ». **Il
n'y a pas de tableau noir.** Aucune structure partagée n'existe : ce que l'agent reçoit de l'amont
est une **chaîne reconstruite pour lui**, tâche par tâche
([`maestro/engine/executor.py:1518`](../maestro/engine/executor.py), `_build_task_description`), et
elle est bornée à ses **dépendances directes** — `loop.py:643` ne résout que `task.dependances`, et
`loop.py:22-27` l'écrit : la boucle « n'introduit aucun état partagé entre tâches ».

Ce n'est pas un détail de vocabulaire. Un tableau noir est une surface où l'on écrit **pour les
autres** ; ce que nous avons est un **contexte servi**, où chacun ne lit que ce dont il dépend. Il
n'existe donc aujourd'hui **aucun endroit** où un agent puisse déposer quelque chose à l'intention
d'un pair qui n'est pas son aval direct — et c'est la moitié la moins visible du trou que le §3.2
vient combler.

## 2. La règle

Les cinq verbes candidats ne se départagent pas un par un au jugé. Ils se départagent par une
question unique, qui rend le même verdict cinq fois et se vérifie sur pièces :

> **Le verbe écrit-il une OBSERVATION, ou une DÉCISION sur le plan ?**
>
> Une **observation** dit ce qui est : « je suis bloqué », « voici où j'en suis », « voici ce que
> j'ai trouvé ». Elle s'ajoute, ne retire rien, et le moteur peut l'ignorer sans que le run change
> de sens. → **ouverte**.
>
> Une **décision sur le plan** dit ce qui doit être : « il faut une tâche de plus », « celle-ci
> revient à quelqu'un d'autre », « celle-là est finie ». Elle change ce qu'un humain a approuvé.
> → **fermée**, et elle ne se rouvre que par un point d'approbation.

Cette règle n'est pas inventée pour l'occasion : **c'est celle que #489 a déjà rendue**, sur le seul
champ du plan qu'un agent écrit. [`maestro/detail_tache.py:46`](../maestro/detail_tache.py) :
« ossature au plan, complétée et cochée par l'agent — *le plan donne de quoi lire la tâche avant
qu'elle démarre ; l'agent donne la vérité pendant qu'elle tourne. Chacun répond de ce qu'il sait, et
aucun des deux ne répond de ce qu'il ignore.* »

Et #489 a payé, sur ce champ-là, la garantie qui rend la règle sûre — la **monotonie** : « ce qui
est acquis ne se perd pas. Un état ne redescend jamais, une étape connue ne disparaît jamais d'un
relevé qui l'oublie. Le dénominateur, lui, peut grandir » (l. 60-65). C'est le patron. On ne le
réinvente pas, on l'étend.

## 3. Les cinq verbes, instruits séparément

Chacun est jugé sur les trois coûts que le ticket demande — **le plan validé**, **le brief**, **la
mesure de coût** — plus un quatrième que l'instruction a fait apparaître et qui départage à lui seul
deux des cinq : **qui répond de la vérité écrite**.

| Verbe | Plan validé | Brief (#318/D5) | Coût | Verdict |
| --- | --- | --- | --- | --- |
| **Déclarer un blocage** | intact | intact | étape de journal, hors grand livre | ✅ **ouvert** |
| **Écrire à un pair** | intact | intact | étape de journal, hors grand livre | ✅ **ouvert** |
| *(Avancement fin)* | intact | intact | déjà payé | ✅ *déjà ouvert (#489)* |
| **Créer une tâche** | **amendé** | **contourné** | une exécution non budgétée | ❌ **refusé** |
| **Changer statut / propriétaire** | **amendé** | intact | nul, mais fausse la cascade #43 | ❌ **refusé** |
| **Recruter un agent** | **amendé** | **contourné** | une exécution non budgétée | ❌ **refusé** |

### 3.1 Déclarer un blocage — ouvert

**Ce que c'est.** Un agent qui ne peut pas avancer le dit, tout de suite, au lieu d'attendre la fin
de sa tâche pour rendre un livrable vide. C'est le verbe qui motive le ticket, et le moins cher des
cinq.

**Ce qu'il coûte.** Rien de mesurable. Une étape de journal (`StepUsage()` vide, hors grand livre),
un événement de plus sur un pont qui en porte déjà une douzaine, une entrée de plus dans la frise de
#355 — qui la reçoit sans travail, puisqu'elle agrège déjà les événements de ce pont.

**Ce qu'il ne fait pas, et c'est la frontière avec #647.** Il **n'attend pas de réponse**. C'est une
*déclaration*, pas une *demande* — et cette distinction est toute la ligne de partage entre les
deux cadrages voisins :

- `demander_arbitrage` (#582) **attend** : l'appel se bloque, une personne tranche, l'agent reçoit
  oui ou non. C'est un acte soumis ;
- un **canal « question »** dont la réponse serait du texte est explicitement le sujet de **#647**,
  qui doit le trancher ;
- `signaler_blocage` **ne bloque pas l'agent** : il consigne et rend la main. L'agent poursuit comme
  il peut, ou échoue comme aujourd'hui — mais désormais **en ayant dit pourquoi**.

Ne pas attendre est ce qui le rend gratuit *et* ce qui l'empêche d'empiéter sur #647. Un verbe qui
attendrait serait un troisième canal d'arbitrage, à tenir d'accord avec les deux autres.

**À qui ça sert immédiatement.** À **#651**, qui cadre la surveillance côté *push* et note que
« un agent tué, muet, ou bloqué sans avoir jamais démarré ne demandera jamais rien ». Ce verbe est
le versant *pull* de la même chaîne : il ne remplace pas la détection par règles, il lui donne le
seul signal qu'aucune règle ne peut produire — **la raison**. Une règle sait dire « bloquée depuis
40 minutes » ; elle ne saura jamais dire « le dépôt de recette refuse mes identifiants ».

### 3.2 Écrire à un pair — ouvert, avec une réserve nommée

**Ce que c'est.** `AgentMessage` existe, complet : expéditeur, destinataire, type, `tache_id`,
`run_id`, objet, charge utile ([`maestro/messaging/mailbox.py:89`](../maestro/messaging/mailbox.py)).
Il ne manque que l'appelant.

**La réserve, et il faut la dire parce qu'elle change ce qu'on livre.** Le transport est un pub/sub
**éphémère** : « pas de rejeu », et `subscribe` doit être posé *avant* que l'expéditeur publie
(l. 21-25). Or **un agent de Maestro n'existe que pendant sa tâche.** Un message adressé à un pair
dont la tâche n'a pas démarré — ou est déjà finie — part dans un canal que personne n'écoute, et
disparaît. Les statuts `lu` et `traite` sont définis mais « attendent la persistance (PostgreSQL)
pour un accusé durable » (l. 53-57) : **ils ne sont assignés nulle part dans le dépôt**, il n'y a
donc aucun accusé de réception aujourd'hui.

**Et ce n'est pas une crainte théorique : le cas se produit déjà.** Un seul consommateur appelle
`subscribe` dans tout `maestro/` — le relais de handoff, sous l'identité `"orchestrateur"`
([`handoff.py:91`](../maestro/messaging/handoff.py)). Les messages `requete`/`reponse` que le chat
publie vers les agents ([`chat.py:783`](../maestro/controltower/chat.py)) **ne sont relevés par
personne**. Le handoff, lui, ne s'y risque pas : il part **toujours en diffusion**
([`handoff.py:117`](../maestro/messaging/handoff.py)), et son motif est écrit — l'agent aval
*n'est pas encore routé*, donc on ne sait pas à qui adresser. Ouvrir « écrire à un pair » en
promettant une livraison serait donc ajouter un troisième producteur à un canal qui n'a qu'un
lecteur, et pas celui qu'on vise.

**Ce qu'on en tire, et c'est la moitié qui compte.** Le remède est dans le même fichier : le handoff
fait **les deux** — `publish` *puis* `consigne_message`
([`maestro/messaging/handoff.py:124-127`](../maestro/messaging/handoff.py)). C'est le bon partage,
et on le reconduit tel quel :

> **Le journal est la livraison ; le pub/sub n'est que la notification.**

Un message écrit par un agent est **consigné** — donc durable, donc dans la frise, donc lisible par
qui vient après — et **publié** en best-effort pour le pair qui écoute par chance. On ne promet donc
pas une livraison point à point qu'on ne sait pas tenir. C'est aussi ce qui empêche d'ouvrir ce
verbe en croyant livrer une messagerie fiable : **ce qui est livré est une trace adressée**, et le
critère d'acceptation du lot devra le dire dans ces termes.

**Ce que ça coûte.** La même étape de journal que les deux autres. Zéro au grand livre.

### 3.3 Créer une tâche — refusé

C'est le verbe qui sépare les deux moitiés du tableau, et il mérite d'être refusé pour la **bonne**
raison, parce que la mauvaise est très disponible.

**La mauvaise raison serait le cycle.** On la trouve d'autant plus facilement que le ticket la
suggère — la détection de cycle « doit rester vraie à chaud », et le tableau d'AionUi « accepte
`A blocked_by B` et `B blocked_by A` ». Or **cet argument ne tient pas contre nous**, et il faut
l'écarter explicitement pour qu'il ne serve pas de fausse justification :

> Une tâche ajoutée dont **toutes les dépendances désignent des tâches déjà présentes** ne peut pas
> créer de cycle. Un cycle passant par le nouveau nœud exigerait une arête *entrante* vers lui ;
> or aucune tâche existante ne le nomme — `Task` est `frozen`
> ([`maestro/orchestrator/schema.py:95`](../maestro/orchestrator/schema.py)) et son tuple
> `dependances` est fixé à la construction. Un nœud sans arête entrante n'est sur aucun cycle. ∎
>
> Le corollaire vaut pour l'ordre : ce nœud se place **en queue** de l'ordre topologique déjà
> calculé, qui n'est donc pas invalidé.

Autrement dit, l'acyclicité et l'ordre — ce que `_ensure_acyclic` (l. 409) et `topological_order`
(l. 215) garantissent — survivraient **gratuitement** à un ajout en append-only. Le garde-fou qui
manque chez eux ne nous manque pas, et il ne coûterait rien à préserver. **Ce n'est donc pas là que
le refus se joue.**

**La vraie raison est D5, et elle est écrite.** Le cadrage #218 a posé qu'on ne décompose pas avant
validation humaine, « parce qu'une décomposition est payante et qu'un cadrage de travers se paie
deux fois » — c'est « le point de contrôle le plus rentable du produit », et
[docs/29 §4](./29-decision-run-objet-de-premier-plan.md) l'a **reconfirmée** quatre mois plus tard
en refusant de la supprimer alors même qu'on déménageait son écran. Or **créer une tâche à
l'exécution, c'est décomposer sans validation.** Ce n'est pas une entorse à D5 : c'en est
exactement l'inverse, obtenu par un autre chemin. Une décision documentée et deux fois tenue ne se
défait pas par un outil MCP.

**Et le budget ne rattrape pas.** On pourrait espérer que le plafond de dépense borne les dégâts :
`plafond_cout_usd` stoppe bien « la tâche qui fait déborder le cumul du run »
([`maestro/engine/guardrails.py:6`](../maestro/engine/guardrails.py)). Deux choses l'en empêchent :

- son défaut est **`None`, donc inactif** (l. 328-329). Sur un run lancé sans plafond — le cas
  courant —, des tâches qui créent des tâches n'ont **aucune borne**, ni en argent ni en nombre ;
- et là même où il est armé, **il ne couvre pas la planification**. `_plan` et `etape_brief`
  appellent `collect_usage()` **sans** `plafond=` ([`loop.py:849`](../maestro/engine/loop.py) et
  `loop.py:915`) : les étapes de décomposition sont comptées au total, jamais plafonnées. Or une
  tâche créée à chaud est précisément une décomposition de plus. Le seul poste que ce verbe ferait
  croître est celui que le plafond ne regarde pas.

Le garde-fou qui manque n'est donc pas seulement « non posé par défaut » : sur le chemin exact que
ce verbe emprunterait, **il n'existe pas**.

**Refusé, donc.** Et la porte de sortie n'est pas ici : amender un plan en cours est de la
**replanification**, que #651 instruit (« relancer, réassigner, replanifier »). Le §5 dit ce que
cette note lui lègue pour qu'il n'ait pas à reposer la question.

### 3.4 Changer le statut ou le propriétaire — refusé, et la partie utile est déjà livrée

**Le statut est un fait dérivé, pas une déclaration.** Il est posé par la boucle à partir de ce que
la tâche a produit, et la cascade de blocage de #43 en dépend : un agent qui poserait « réussie »
sur sa propre tâche s'auto-décernerait son verdict, et un agent qui poserait un statut sur la tâche
**d'un autre** débloquerait ou condamnerait un sous-arbre entier. C'est le quatrième coût annoncé
au §3 — *qui répond de la vérité écrite* — et c'est le seul verbe où la réponse est franchement
« pas lui ».

**Le propriétaire est un fait routé.** L'affectation vient des `competences_requises` et du contrôle
de capacité (#86), « relu à chaud à chaque tâche »
([`maestro/engine/loop.py:461`](../maestro/engine/loop.py)). Un agent qui réassigne court-circuite
le routeur *et* le plafond d'instances — il peut mettre au travail un agent que la Control Tower
vient de désactiver.

**Mais la demande légitime derrière ce verbe est déjà servie, et c'est ce qu'il faut retenir.**
Ce qu'un agent veut réellement dire n'est presque jamais « ma tâche est finie » : c'est « j'en suis
là ». Cette moitié-là est **ouverte depuis #489** — la checklist, dont l'agent tient la vérité
pendant que la tâche tourne. Le verbe est donc refusé **sans rien retirer**, ce qui est le meilleur
cas : il n'y a pas de manque à combler, il y a un mécanisme à ne pas doubler.

### 3.5 Recruter un agent — refusé par conséquence

Recruter, c'est créer une tâche (§3.3) et lui affecter quelqu'un (§3.4). Les deux étant refusés, le
troisième l'est sans jugement propre.

Une chose mérite quand même d'être notée, parce que le ticket la pose comme une question ouverte et
qu'elle est déjà résolue. Chez AionUi, l'approbation humaine avant recrutement est **une consigne
rédigée dans la description de l'outil MCP** — « elle tient tant que le modèle obéit ». Chez nous,
le jour où un tel verbe existerait, **il n'y aurait rien à inventer** : l'outil s'appelle
`mcp__maestro__<nom>` ([`maestro/providers/arbitrage.py:85`](../maestro/providers/arbitrage.py)),
donc la politique de permissions (#110) le désigne comme n'importe quel outil, et les trois crans de
#586 (`auto` / `orchestrateur` / `humain`, **défaut `humain`**) le routent. Le garde-fou est du bon
côté par construction. Ce qui manque à ce verbe n'est pas sa protection : c'est sa raison d'être.

## 4. Le canal — la question ne se posait plus

Le ticket demande de choisir entre « un serveur MCP interne au run » et « une capacité du runtime
outillé », et donne un argument contre le premier : « le second évite de monter un serveur par run ».

**Cet argument est sans objet, et c'est mesurable.** Le serveur est déjà monté, sur chaque session
qui reçoit un canal d'arbitrage, et il ne coûte pas ce que le ticket suppose :

- il est **in-process, de type `sdk`** — « servi en process par le SDK lui-même, déclaré au CLI dès
  l'initialisation : **il n'a rien à connecter** »
  ([`maestro/providers/claude.py:379-385`](../maestro/providers/claude.py)) ;
- il est **délibérément hors de `attendus`**, précisément pour qu'aucune attente de connexion ne
  puisse le retarder — « l'y inscrire n'ajouterait qu'un risque de 60 s d'attente sur un canal dont
  l'absence ne doit jamais arrêter une tâche » ;
- son nom est **réservé** (`NOM_SERVEUR = "maestro"`) et il est monté **en dernier**, si bien qu'une
  déclaration homonyme d'un agent ne peut pas masquer le canal d'un garde-fou (l. 416-419).

> **Verdict : le serveur MCP in-process, sans hésitation — parce que le choisir, c'est ne rien
> ajouter.**

**Pourquoi l'autre est refusé.** Une « capacité du runtime » serait un **second support** pour la
même chose, à côté d'un premier qui fonctionne. C'est exactement la panne que #365 a supprimée sur
le cycle de vie, et sa leçon est écrite : deux supports pour un même objet, et le premier symptôme
est un objet qui porte deux états. Ici le symptôme serait pire qu'une incohérence d'affichage —
**une des deux surfaces échapperait à la politique de permissions**, qui ne sait désigner qu'un
outil. Un verbe qui engage doit être gouvernable par le même mécanisme que les autres ; c'est la
dernière question du ticket, et elle tranche aussi celle-ci.

**Ce que ça change au découpage.** Le serveur ne porte aujourd'hui **qu'un seul outil**. Le passer à
N est un travail de plomberie réel mais borné, sans changement de comportement — c'est le lot 1 du
§8, et c'est ce qui rend les deux verbes ensuite parallélisables.

## 5. Ce que ça fait au plan validé — la réponse commune à #354 et #651

#651 demande explicitement **une seule réponse pour les deux cadrages, pas deux**. La voici, et elle
tient en une phrase.

> **Le graphe du plan ne se modifie pas en cours de run.** Ni un agent, ni un superviseur, ni
> l'orchestrateur ne lui ajoutent, n'en retirent ou n'en réaffectent un nœud à chaud. Ce qui
> s'accumule pendant un run est **à côté** du graphe — des observations, en ajout seul — et le
> graphe reste celui qu'un humain a approuvé.

**Ce n'est pas une contrainte à ajouter : c'est l'état du dépôt, et il est déjà écrit.** Le plan est
snapshoté **une seule fois**, sur l'étape `planification`
([`maestro/engine/loop.py:877`](../maestro/engine/loop.py)), et le motif y dit exactement la même
chose que cette note : « c'est l'instant où le plan existe et où il est figé ». Aucun mécanisme
d'ajout, de retrait ou de modification de tâche n'existe après `validate_plan` — ce qui rend cette
décision moins un choix qu'une **ratification** : on écrit une propriété que le code tient déjà, pour
qu'elle cesse d'être un accident d'implémentation et devienne un invariant opposable.

Une nuance à connaître avant d'y toucher : il n'existe **aucune classe `Plan`**. `validate_plan` rend
une `list[Task]` nue, et la boucle la reconstruit deux fois par `dataclasses.replace` pour faire
hériter `ticket` et `projet_id` (`loop.py:608-621`). L'immuabilité est donc celle des **nœuds**
(`Task` est `frozen`), jamais celle du conteneur. C'est suffisant pour l'invariant ci-dessus — ce
qu'on interdit est d'ajouter un nœud, pas de rebâtir la liste — mais quiconque voudrait s'appuyer
sur « le plan est immuable » au sens fort trouverait une liste Python ordinaire.

Ce que ça donne, question par question, pour que #651 n'ait pas à les reposer :

| La question de #651 | La réponse |
| --- | --- |
| Une tâche créée à chaud est-elle validée contre `task.schema.json` ? | **La question ne se pose pas** : aucune tâche n'est créée à chaud. |
| Entre-t-elle dans l'ordre topologique ? | Idem. *(Et si le jour vient : en queue, sans invalider l'ordre calculé — la démonstration est au §3.3.)* |
| `_ensure_acyclic` reste-t-elle vraie à chaud ? | **Vraie par construction**, le graphe ne changeant pas. |
| Une tâche **relancée** ? | Hors périmètre de cette note, et déjà servie : `maestro/engine/retry.py` relance sans toucher au graphe. |
| Une tâche **réassignée** ou **recréée** ? | Refusées ici (§3.3, §3.4) — et si #651 veut les rouvrir, c'est **par un point d'approbation**, pas par un canal d'agent. |

**Pourquoi ce sens plutôt que l'inverse.** Un plan qu'on peut amender à chaud est un plan dont plus
personne ne peut dire ce qu'il contient : le brief approuvé cesse d'être un contrat et devient un
point de départ. Or c'est le contrat qui porte tout le reste — l'estimation, l'ordre déterministe du
rapport, la reprise de #349 qui repart du brief. On ne paie pas ce prix pour un confort d'exécution
qu'aucun besoin observé ne réclame aujourd'hui.

**Et le prix du refus est nommé, pas masqué.** Un agent qui découvre en travaillant qu'il faudrait
une tâche de plus ne peut toujours que… le dire. C'est précisément à quoi sert `signaler_blocage`
(§3.1) : la demande ne disparaît pas, **elle change de destinataire** — elle va à l'humain qui lit
la frise, au lieu de s'exécuter en silence. C'est plus lent, et c'est tout l'intérêt.

## 6. L'identité d'instance — écartée, parce que `tache_id` la porte déjà

Le ticket demande si un membre d'équipe doit être une *instance* identifiée, comme le `slot_id`
d'AionUi. La réponse est **non**, et pour une raison qui n'est pas un renoncement.

**Chez eux, `slot_id` est nécessaire.** Un membre d'équipe est une **conversation qui vit
indépendamment des tâches** : on recrute d'abord, on donne du travail ensuite, le même assistant
peut être recruté deux fois. Sans identifiant, deux instances du même assistant seraient
indiscernables — il n'existe rien d'autre pour les séparer.

**Chez nous, l'exécution est adossée à la tâche.** Il n'y a pas de membre recruté puis employé : il
y a une tâche routée vers un rôle, exécutée, terminée. Et **`tache_id` est déjà partout où
`slot_id` serait attendu** :

- dans chaque ligne de journal, qui est nommée `<tache_id>:<quoi>` — c'est la convention même que
  lisent `consigne_message` et `consigne_ticket` ;
- dans chaque message, qui porte `tache_id` **et** `run_id`
  ([`maestro/messaging/mailbox.py:89`](../maestro/messaging/mailbox.py)) ;
- dans la télémétrie, où l'étape porte en plus `agent` et `role`
  ([`maestro/references.py:153`](../maestro/references.py)) ;
- dans le **grand livre**, où `TaskCost.tache_id` est la clé d'agrégation
  ([`maestro/telemetry/costs.py:82`](../maestro/telemetry/costs.py)) ;
- dans **Langfuse**, où l'observation porte `metadata["etape"]`, soit `task.id`
  ([`maestro/telemetry/langfuse.py:113`](../maestro/telemetry/langfuse.py)).

Deux tâches routées vers le même rôle sont donc **distinguables partout où l'on mesure**, et par une
clé plus précise qu'un numéro d'instance : `tache_id` dit *laquelle*, là où `slot_id` ne dirait que
*l'autre*.

⚠ **Une exception, et il faut la nommer parce que le ticket avait raison sur ce point précis.** Le
ticket affirme que « rien ne les distingue dans le rapport, la télémétrie ou la Control Tower ».
C'est faux pour la télémétrie et le JSON — voir ci-dessus —, mais **exact pour la synthèse
Markdown** : `RunReport.synthese` rend `## {etat} {titre}` puis `- Agent : {role} ({agent})`
([`maestro/engine/loop.py:282`](../maestro/engine/loop.py)) et **n'imprime le `task_id` nulle part**.
Deux tâches de même titre et même rôle y produisent deux sections rigoureusement identiques.

Ce défaut est réel, et il ne plaide pourtant pas pour une identité d'instance — il plaide pour
**imprimer la clé qu'on a déjà**. Le remède tient en un champ dans un f-string ; nommer l'instance
coûterait un champ dans `Task`, dans le journal, dans les événements, dans les projections et dans
le grand livre. Quand deux remèdes traitent le même symptôme et que l'un coûte cent fois l'autre,
le symptôme ne justifie pas le second. C'est un critère du lot final (§8).

> ⚠ **Livré depuis** : `RunReport.synthese` imprime une ligne « Tâche » portant le `task_id` sous le
> titre de chaque section (#721). La prévision « le remède tient en un champ dans un f-string » s'est
> vérifiée au mot près — une ligne, aucun champ ajouté nulle part.

> **Verdict : pas d'identité d'instance. L'instance, c'est la tâche.**

**L'effet sur la frise d'activité (#355) — et il est favorable.** #355 est en cours et a fait le
pari que « le rattachement naturel est le **rôle** », en notant que la question était instruite ici
et ne le bloquait pas. **Ce pari est confirmé, et il peut continuer sans changement** :

- le **couloir reste le rôle**, et c'est le bon axe pour une raison qui n'avait pas été écrite :
  un couloir doit être **stable sur toute la durée du run** pour qu'on puisse suivre une ligne des
  yeux. Les rôles du plan le sont ; des instances qui naissent et meurent au fil des tâches ne le
  sont pas. Un axe dont le nombre de pistes change en cours de route est un axe illisible ;
- **la désambiguïsation à l'intérieur d'un couloir est déjà là** : chaque entrée porte son
  `tache_id`. Deux tâches simultanées du même rôle se lisent comme deux fils dans le même couloir,
  étiquetés — pas comme une bouillie.

**L'effet sur la télémétrie : aucun** — rien à ajouter, rien à migrer. Le seul geste que ce verdict
laisse derrière lui est celui de l'exception ci-dessus : **imprimer le `task_id` dans la synthèse**,
une ligne.

## 7. Le garde-fou — du côté des nôtres, et rien à construire

Dernière question du ticket : si on ouvre un verbe qui engage, le garde-fou doit être « du même côté
que les nôtres, pas du côté du prompt ».

**Les deux verbes ouverts n'engagent rien** — ils consignent. Le pire qu'un agent puisse en faire
est du bruit dans la frise, et le remède au bruit est celui de #582, qui l'avait déjà anticipé pour
`demander_arbitrage` : **la description de l'outil le cadre comme un recours, pas comme une étape**
(« N'appelle pas cet outil pour une action ordinaire de ta tâche »,
[`arbitrage.py:91`](../maestro/providers/arbitrage.py)). Un texte de description suffit **ici**, et
seulement ici, parce qu'il ne protège rien : il règle un débit, pas un droit.

**Et le droit, lui, est déjà tenu par la couche permissions.** Un outil MCP s'appelle
`mcp__maestro__<nom>`, donc une politique (#110) le cite, l'autorise ou le refuse — « une liste
`allow` fermée qui ne le cite pas, ou un `deny` dessus, retire à l'agent la possibilité » de
l'appeler ([`claude.py:392`](../maestro/providers/claude.py)), et le refus est tracé comme les
autres. C'est vrai des deux verbes ouverts sans une ligne de plus.

**La distinction à retenir** — et c'est elle qui répond au ticket : chez AionUi, le prompt **est**
le garde-fou. Chez nous, le prompt règle l'usage **normal** d'un verbe dont le droit est tenu
ailleurs. Les deux ressemblances sont superficielles ; l'écart est que chez nous, un modèle qui
n'obéit pas à la description **ne franchit rien**.

## 8. Le découpage

La décision engage du code — deux verbes —, donc le quatrième critère du ticket appelle un
découpage. **Parent de suivi #717**, quatre lots, milestone « Collaboration inter-agents ».
**Les quatre sont livrés** — ce qu'ils ont tenu et ce qu'ils ont corrigé est au **§10**.

| Lot | Ce qu'il fait | ∥ |
| --- | --- | --- |
| **#718** | Le serveur MCP `maestro` porte plusieurs outils au lieu d'un — plomberie, `demander_arbitrage` inchangé | |
| **#719** | `signaler_blocage(raison)` : l'agent dit qu'il bute, sans attendre de réponse | ∥ |
| **#720** | Écrire à un pair : le journal livre, le pub/sub notifie | ∥ |
| **#721** | Tests + doc | |

**Pourquoi le lot 1 existe séparément.** Le serveur est aujourd'hui écrit pour **un** outil ; les
deux verbes suivants y toucheraient tous les deux au même endroit. En sortir la plomberie d'abord
est ce qui les rend réellement parallèles — sans quoi le marqueur ∥ promettrait une simultanéité que
le premier conflit démentirait.

**Pourquoi les deux verbes sont ∥.** Une fois le porte-outils en place, ils n'ont rien en commun :
l'un consigne une raison, l'autre publie et consigne un message. Aucun ne lit l'autre.

**Ce que le lot final doit vérifier en propre**, au-delà des tests de chaque verbe :

- qu'un message adressé à un pair **absent** est bien consigné malgré tout (§3.2 — c'est la
  promesse qu'on tient à la place de celle qu'on ne tient pas) ;
- que le graphe du plan est **inchangé** après un run où les deux verbes ont été appelés (§5 —
  l'invariant central de cette note, et le seul qui ne se voit pas à l'œil) ;
- que la **synthèse Markdown porte le `task_id`** (§6) — c'est là que le lot final paie la dette
  d'une décision de refus : on écarte l'identité d'instance, on doit donc rendre lisible la clé qui
  la remplace. Deux tâches de même titre et même rôle doivent produire deux sections distinctes,
  et le test se prouve sur un plan qui en contient exactement deux.

## 9. Ce qui rouvrirait la décision

Nommé d'avance, pour qu'on n'ait pas à re-débattre — même patron que
[docs/28 §8](./28-decision-frontiere-execution-run.md) et
[docs/29 §10](./29-decision-run-objet-de-premier-plan.md).

1. **« Créer une tâche » se rouvre** le jour où un run porte un **plafond de dépense obligatoire**
   *et* où l'amendement du plan passe par un point d'approbation humain — c'est-à-dire le jour où
   les deux motifs du refus (§3.3) tombent ensemble. Aucun des deux ne suffit seul : un budget sans
   approbation laisse D5 contournée, une approbation sans budget laisse la dépense libre.
2. **L'identité d'instance se rouvre** si une exécution cesse d'être adossée à une tâche — un agent
   qui vivrait *entre* deux tâches, en gardant sa conversation. C'est ce que suppose un exécuteur
   CLI tiers persistant (**#356**, ACP), et c'est là que la question se reposera, pas avant.
3. **« Écrire à un pair » se re-jugera** si le transport gagne la persistance que ses statuts
   attendent déjà (`lu`, `traite` — [`mailbox.py:53`](../maestro/messaging/mailbox.py)). La réserve
   du §3.2 tomberait alors, et la promesse pourrait passer de « une trace adressée » à « une
   livraison ». Ce serait un gain, pas un renversement.
4. **Le canal se rouvre** si un exécuteur non-MCP devient un chemin de production — un agent branché
   par ACP (#356) ne voit pas nos outils MCP. La surface d'écriture devrait alors exister deux fois,
   et c'est exactement le second support que le §4 refuse aujourd'hui : la question serait à
   reprendre entière.

Aucune de ces quatre conditions n'est remplie au 2026-08-28.

---

## 10. La suite — le chantier livré (2026-08-29)

> Écrit au lot final **#721**, le lendemain de la décision. Cette section ne révise ni le verdict
> ni les cinq instructions du §3 — ils tiennent — mais rend leur **contrepartie constatée** : ce que
> la note avait bien vu, les **quatre endroits où elle s'est trompée en route**, et l'état de ses
> portes. Les chiffres sont relevés sur le dépôt, jamais recopiés du plan.

### 10.1 Ce qui a été livré

Les trois premiers lots ont été mergés le **2026-08-29**, de `2ea2ed6` (le porte-outils) à
`4950464` (le mot à un pair). Le quatrième est la PR qui porte cette section.

| Lot | Livré | Taille |
| --- | --- | --- |
| **#718** — le porte-outils | `_outils_maestro` / `_serveur_maestro` / `_serveurs_mcp` séparés dans `maestro/providers/claude.py` : ce que le serveur **porte**, et ce qui décide de le **monter**, cessent d'être la même fonction. Aucun changement de comportement. | +103 / −36, **1 fichier** |
| **#719** — déclarer un blocage | `maestro/providers/blocage.py` (le vocabulaire), `_outil_blocage` (l'outil), `LocalExecutor._consigne_blocage_signale` (l'étape `<tache>:blocage`), `tache.blocage` jusqu'à la frise. | +436 / −21 |
| **#720** — écrire à un pair | `maestro/providers/courrier.py`, `_outil_courrier`, `LocalExecutor._courrier` : consigne **puis** publie, `mailbox` descendue jusqu'à l'exécuteur. | +466 / −3 |
| **#721** — tests et doc | [`tests/test_surface_ecriture_agents.py`](../tests/test_surface_ecriture_agents.py) (les trois critères, chacun avec son témoin), le `task_id` de la synthèse, cette section, docs/03 et docs/04. | cette PR |

### 10.2 Ce que la note avait bien vu

Trois prévisions, tenues et **mesurables** :

- **« le passer à N est un travail de plomberie réel mais borné, sans changement de
  comportement »** (§4). Le lot 1 pèse **un seul fichier**, +103 / −36, et n'a touché ni
  `run_agent`, ni le montage, ni `demander_arbitrage`. Les deux verbes suivants s'y sont ajoutés
  « en un `if` et une ligne », comme annoncé ;
- **« le remède tient en un champ dans un f-string »** (§6). Une ligne dans `RunReport.synthese`,
  et aucun champ ajouté à `Task`, au journal, aux événements, aux projections ni au grand livre —
  c'est-à-dire exactement le coût que l'identité d'instance aurait fait payer ;
- **« gratuit au grand livre »** (§3.1, §3.2). Les deux étapes portent un `StepUsage()` vide et le
  pont écarte leur mesure, comme il le fait déjà pour `:ticket` et `:detail`.

### 10.3 Les quatre corrections en route

**① « La frise reçoit l'entrée sans travail de son côté » était faux.** Le §3.1 comptait le blocage
pour une entrée de plus dans la frise de #355, « qui la reçoit sans travail ». La frise **filtre par
type** (`TYPES_FRISE`) et écarte `agent.activite` à dessein — le bruit de fond d'un run. Un blocage
rangé là aurait été consigné puis **invisible**, sans que rien n'échoue : le verbe aurait *paru*
marcher. D'où un type à lui, `tache.blocage`, ajouté à `TYPES_FRISE` — la seule voie qui le montre
sans défaire le tri de #355, ouvrir `agent.activite` en bloc faisant entrer relances et refus
d'outil avec lui. C'est la correction la plus utile du chantier, et elle n'aurait produit **aucun
symptôme** : le prix d'une note qui suppose une projection au lieu de l'ouvrir.

**② Le statut a dû être inventé, et il ne pouvait pas s'appeler `bloquee`.** Le §3.4 refuse à un
agent le droit de changer son propre statut ; il n'en tirait pas que le verbe du §3.1 aurait besoin
d'un statut d'étape **à lui**. `loop.py` porte déjà un `_consigne_blocage` (#43) — le blocage
*hérité*, une tâche que rien n'a jamais exécutée parce qu'une dépendance a échoué. Le nôtre en est
le contraire : **l'agent travaille et parle**. Rendre `bloquee` aurait affiché « cette tâche est
morte » au moment précis où quelqu'un demande de l'aide, et condamné tout son aval par la cascade de
#43. D'où `blocage_signale`, et une projection qui ne fait que rafraîchir la dernière activité de
l'agent — il vient de parler, donc il est vivant.

**③ « On le reconduit tel quel » ne valait que pour le partage, pas pour l'ordre.** Le §3.2 tire son
remède du handoff, qui fait les deux gestes — `publish` *puis* `consigne_message` — et conclut « c'est
le bon partage, et on le reconduit tel quel ». Le **partage** est bien reconduit ; l'**ordre** a dû
être inversé. Le handoff abandonne tout, trace comprise, si la publication échoue
([`handoff.py:123-127`](../maestro/messaging/handoff.py)) : la trace y est *conditionnée* à la
notification. Or la phrase qui porte toute la décision est « **le journal est la livraison** » — donc
consigner d'abord, publier ensuite, et avaler l'échec de la publication. Sans cette inversion,
« un mot adressé à un pair absent est consigné malgré tout » aurait été **faux** dès que le
transport tombe. Les deux ordres sont indiscernables tant que le transport répond, ce qui est
exactement pourquoi le lot final l'éprouve sur un transport **en panne**.

**④ Le marqueur ∥ a coûté un renommage — et il valait quand même son prix.** #719 et #720 ne se
sont pas croisés dans le code, comme le §8 le prévoyait (« aucun ne lit l'autre »). Ils se sont
croisés dans le **vocabulaire** : chacun a nommé ses constantes à sa façon, l'un par des noms nus
(`DESCRIPTION_OUTIL`) et l'autre par des suffixes (`DESCRIPTION_COURRIER`). Les deux règlent la
collision aussi bien pour deux verbes ; celle par suffixes oblige le troisième à en inventer un de
plus. C'est le lot arrivé le premier sur `main` qui l'a emporté, et l'autre a été renommé dans la
foulée (`32d0a28`). La leçon n'est pas de renoncer au marqueur — les deux lots ont bien été écrits
en parallèle — mais que **l'indépendance du code n'emporte pas celle des conventions** : ce que deux
lots ∥ partagent toujours, c'est le fichier qui les monte.

### 10.4 Ce que le lot final a ajouté au-delà de ses trois critères

Deux constats faits en écrivant les tests, tous deux corrigés ici :

- **`OUTIL_BLOCAGE` et `OUTIL_COURRIER` n'avaient aucun lecteur.** Les deux constantes existent pour
  que la politique de permissions désigne les verbes (§7 : « un outil MCP s'appelle
  `mcp__maestro__<nom>`, donc une politique le cite, l'autorise ou le refuse »), et elles n'étaient
  référencées **nulle part** — ni code, ni test, ni politique d'exemple. Un renommage de `NOM_OUTIL`
  les aurait suivies en silence, et une politique écrite sur l'ancien nom aurait cessé de désigner
  quoi que ce soit, c'est-à-dire **n'aurait plus rien interdit**, sans que rien ne rougisse. Le nom
  littéral est désormais figé par un test ;
- **quatre références pointaient des constantes disparues** au renommage de ④
  (`COURRIER_EN_ERREUR`, `DESCRIPTION_COURRIER`), dans `executor.py`, `base.py`, `claude.py` et
  `courrier.py` lui-même. Rien ne casse — ce sont des commentaires —, mais un lecteur qui cherche le
  nom cité ne trouve rien, ce qui est la façon la plus sûre de faire douter du reste.

### 10.5 Les quatre portes du §9, au 2026-08-29

Aucune n'est franchie, et le chantier n'en a rapprochée aucune :

1. **« Créer une tâche »** — les deux motifs du refus tiennent : `plafond_cout_usd` est toujours
   `None` par défaut, et la planification n'est toujours pas plafonnée ;
2. **l'identité d'instance** — l'exécution reste adossée à la tâche. La dette du §6 étant payée, il
   ne reste **aucun** symptôme à invoquer pour la rouvrir ailleurs qu'en #356 (ACP) ;
3. **« écrire à un pair » se re-jugera** si le transport gagne la persistance — `lu` et `traite`
   restent définis et assignés nulle part, la réserve du §3.2 est donc entière ;
4. **le canal** — aucun exécuteur non-MCP n'est un chemin de production.
