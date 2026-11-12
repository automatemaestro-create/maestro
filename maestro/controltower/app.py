"""API Control Tower — REST + WebSocket temps réel (ticket #46).

Le backend du poste de pilotage (docs/05) : expose l'**état courant** de
l'orchestration en REST et **pousse les événements** en temps réel par
WebSocket. Stack retenue : FastAPI + WebSocket + Redis Pub/Sub (docs/02 §4).

Endpoints :

- `GET  /api/sante` — vitalité du service ;
- `GET  /api/installation` — ce que l'installation fait tourner (#1622) : la
  version du produit et le CLI `claude` qu'il lance, avec sa provenance ;
- `GET  /api/taches` — les tâches (statut, agent, coût détaillé — tokens,
  durée) : la source du Kanban ;
- `GET  /api/agents` — l'état des agents (libre/occupé, charge, compteurs,
  capacité : actif/instances) ;
- `POST /api/agents/{nom}/capacite` — le contrôle de capacité (#86, EF-21) :
  active/désactive l'agent et/ou ajuste son plafond d'instances — persisté
  (`maestro.agents.capacity`) et relu à chaud par moteur et workers ;
- `GET  /api/executions` — les runs connus (en cours et passés), récents d'abord :
  objectif, statut, volume, coût, bornes temporelles (#185) et, sur un run non
  soldé, sa **vitalité** (#348) — `vivant` / `orphelin` / `indetermine`, selon que
  l'hôte du run bat encore, ne bat plus depuis le seuil, ou n'a jamais battu ;
- `POST /api/executions` — **lance** une exécution (objectif + garde-fous + les
  **sources** de l'objectif, #317) chez l'**hôte de run** du déploiement — un
  process détaché depuis #446 — et rend son `run_id` immédiatement (#185), avec le
  **rapport de lecture** de sa matière ;
- `POST /api/sources` — **téléverse** un ou plusieurs fichiers (multipart) et
  rend leurs identifiants de source (#317, EF-39) : les octets attendent dans le
  dépôt de téléversement, hors de tout projet, jusqu'au lancement qui les
  rattachera à son run. Refus motivé au-delà des plafonds d'ingestion (ENF-07),
  jamais de troncature silencieuse ;
- `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `POST /api/executions/{run_id}/brief/decision` — tranche le **brief** d'un run
  suspendu (#320, décision D5) : approuver (avec un brief éventuellement corrigé,
  qui devient l'entrée de la décomposition) ou refuser (run « annulée », aucune
  tâche créée) ;
- `GET  /api/executions/{run_id}` — le détail d'une exécution (état, trace, coût)
  et, depuis #320, le **brief** soumis ou retenu ;
- `GET  /api/executions/{run_id}/cout` — le grand livre du run (#57) : coût
  par tâche (tokens entrée/sortie, coût estimé, durée) et agrégat ;
- `GET  /api/executions/{run_id}/graphe` — le **graphe** du run (#490) : un nœud
  par tâche du plan, une arête par dépendance, les nœuds rangés par niveaux —
  deux tâches indépendantes y tombent au même niveau, donc se lisent comme
  parallèles ;
- `GET  /api/analytics/couts` — la vue coûts & analytics (#87) : agrégats par
  tâche, par agent et par exécution, total et série temporelle du coût
  (`depuis` pour la période, `pas` pour la granularité des seaux) ;
- `POST /api/taches/{tache_id}/reassigner` — réassignation manuelle (Kanban) ;
- `GET  /api/validations` — les demandes de validation humaine (#48 : en
  attente d'abord le contexte, puis l'issue une fois tranchée). Une demande
  d'**application dans le projet** (#227, EF-37) y porte en plus son `diff` :
  les fichiers que l'accord écrirait et la branche qu'il fusionnerait ;
- `POST /api/validations/{tache_id}/decision` — la décision humaine
  (approuver/refuser) : le moteur, en attente sur le bus, reprend ou annule. Un
  refus peut porter un `motif` (#272), facultatif, qui rejoint le `detail` de
  l'événement — donc le journal et la `decision` de la demande — et, depuis
  #1185, revient à l'agent comme **consigne** : il replanifie son geste au lieu
  d'abandonner. Une approbation d'acte peut porter une `etendue` (`run`,
  `projet`) : l'outil n'est plus redemandé à cet agent pour la suite ;
- `GET  /api/questions` — les questions **libres** posées par les agents pendant
  leur tâche (#1023) : la question, ses choix facultatifs, l'hypothèse que
  l'agent suivra sans réponse, puis la réponse une fois écrite. Une question
  reste servie tant que personne n'y a répondu, même après que l'agent a repris ;
- `POST /api/questions/{question_id}/reponse` — la réponse humaine, du **texte**
  : l'agent, suspendu sur le bus, la reçoit et reprend. Répondre n'approuve
  aucun acte — un outil classé `ask` reste refusé sans arbitrage (EF-08). Ces
  deux gestes se règlent aussi depuis le fil de l'orchestrateur, par le même
  service (#1183, `ServiceAttentes`, `POST /api/chat/{agent}/reglement`) ;
- `GET  /api/playbooks` — les playbooks des agents (#76 : version courante et
  provenance — défaut du code ou stockage versionné) ;
- `GET  /api/playbooks/{agent}` — le playbook courant d'un agent (contenu) ;
- `PUT  /api/playbooks/{agent}` — publie une nouvelle version du playbook ;
- `GET  /api/playbooks/{agent}/versions` — l'historique des versions ;
- `GET  /api/playbooks/{agent}/versions/{version}` — une version passée ;
- `POST /api/playbooks/{agent}/restaurer` — retour arrière (EF-25) : republie
  une version passée comme nouvelle version courante ;
- `GET  /api/playbooks/{agent}/propositions` — les propositions d'auto-amélioration
  en brouillon (#111 : jamais courantes, jamais chargées tant que non appliquées) ;
- `POST /api/playbooks/{agent}/propositions` — analyse **à la demande** les échecs
  d'un run (`run_id`) et génère une proposition de révision du playbook (#139) ;
- `GET  /api/playbooks/{agent}/propositions/{numero}` — une proposition, contenu compris ;
- `POST /api/playbooks/{agent}/propositions/{numero}/appliquer` — l'action humaine (#140) :
  la proposition devient la version courante (chargée à chaud, #78) et quitte les brouillons ;
- `POST /api/playbooks/{agent}/propositions/{numero}/rejeter` — écarte la proposition sans
  toucher à la version courante ;
- `GET  /api/mcp/registre` — la bibliothèque de serveurs MCP (#131), **à trois
  sources** depuis #678 : templates recherchables (`q`, par nom/tag/éditeur)
  portant transport, gabarit `${VAR}`, mode d'auth (docs/21), variables à
  fournir et procédure côté outil. `source=toutes|curee|admise|decouverte` n'en
  sert qu'une ; `curee` (le booléen) dit ce qui est **montable**, `source` dit
  **d'où ça vient** — garde-fou supply-chain, docs/19 ;
- `GET  /api/mcp/registre/provenance` — les **trois** provenances : le seed relu
  à la main avec sa date de revue, les admissions avec leur dernier geste, le
  miroir avec son rafraîchissement et son compte (#271, #677, #678) ;
- `GET  /api/mcp/registre/{id}` — une entrée, quelle que soit sa source (404 sinon) ;
- `GET  /api/mcp/admissions` — le **journal des admissions** (#678) : ce qu'un
  humain a fait entrer dans l'allowlist (actives et révoquées), les signaux que
  l'amont a émis depuis, et la politique qui garde la porte ;
- `POST /api/mcp/admissions` — **admet** une entrée découverte : enregistre
  l'entrée traduite figée avec sa source (nom amont, version épinglée, éditeur,
  dépôt, horodatage du miroir) et le geste (qui, quand, pourquoi) ;
- `POST /api/mcp/admissions/{id}/revocation` — **révoque** une admission :
  l'entrée sort de l'allowlist, rien n'est effacé ni démonté, et la réponse dit
  ce qui reste monté (un `POST` parce qu'une révocation est un acte tracé, pas
  une suppression) ;
- `GET  /api/mcp/pool` — le **pool projet** des intégrations MCP configurées
  (#133) : chaque intégration avec son mode d'auth et l'état (présent/valide)
  de ses secrets côté coffre projet — jamais une valeur de secret —, plus depuis
  #678 son `admission`, ses `signaux` d'amont et son `alerte` si elle n'est plus
  dans l'allowlist ;
- `POST /api/mcp/pool` — ajoute (ou reconfigure) une intégration au pool depuis
  la bibliothèque : instancie l'entrée de l'allowlist (garde-fou supply-chain)
  et pose ses secrets **une seule fois** dans le coffre projet chiffré (#133).
  Un 404 y **nomme le geste manquant** — l'admission — depuis #678 ;
- `DELETE /api/mcp/pool/{id}` — retire une intégration du pool, désactive son id
  chez chaque agent et purge les secrets qu'elle était seule à référencer ;
- `PUT  /api/mcp/activations/{agent}` — fixe les intégrations du pool **activées**
  pour un agent (#133) : l'écriture derrière l'interrupteur par agent qui
  remplace l'affichage lecture seule des serveurs MCP ;
- `POST /api/mcp/migration/{agent}` — migre la déclaration **héritée**
  `core/mcp/{agent}.json` vers le pool projet (#263) : ses serveurs y entrent
  (sous l'id de leur entrée de bibliothèque quand elle les décrit exactement),
  l'agent les active et le fichier part. **Additif** — le reste du pool et les
  autres agents ne bougent pas — et sans aucun secret à ressaisir ;
- `PUT  /api/permissions/{agent}` — écrit la politique allow/ask/deny d'un agent
  (#262, source `core/permissions/<agent>.json`) : remplacement intégral, 422
  **motivé** sur une entrée mal formée, et aucune lecture préalable — c'est ce
  qui permet de réparer depuis l'écran une politique que le moteur refuse ;
- `DELETE /api/permissions/{agent}/accords/{id}` — retire un **accord étendu**
  (#1185) : l'outil qu'une personne avait approuvé « pour la suite du run » ou
  « du projet » repasse à l'arbitrage dès l'appel suivant ;
- `GET  /api/projets` — les projets déclarés de l'utilisateur (#223, EF-35) :
  racine canonicalisée sur le disque, origine, `vcs` détecté et périmètre ;
- `GET  /api/projets/explorateur` — l'**explorateur de dossiers servi par
  l'API** (docs/05 §2.7) : énumère les sous-dossiers d'un chemin (marqueur
  « dépôt Git », projet déjà déclaré), borné aux **racines explorables** et aux
  zones non sensibles — un dépassement est un **refus motivé**, jamais une
  liste vide. Sans `chemin`, il rend les **points d'entrée** (#278) : dossier
  utilisateur, dossiers récents, projets déclarés, volumes du poste, chacun
  avec son `origine` ;
- `GET  /api/projets/selecteur` — le **sélecteur de dossier natif** est-il
  ouvrable ici (#278) ? Toujours 200 : une indisponibilité (backend distant,
  réglage à `0`, aucun dialogue sur le poste) est une réponse motivée, que
  l'écran affiche au lieu d'un bouton mort ;
- `POST /api/projets/selecteur` — ouvre le dialogue de dossier de l'OS et rend
  le chemin choisi, confronté à EF-38 (`racine_valide`, `refus`). Annuler rend
  200 (`annule: true`) — fermer une fenêtre n'est pas une erreur ;
- `POST /api/projets/racine` — ce qu'EF-38 dit d'un chemin **qu'on lui apporte**
  (#938) : celui du dialogue ouvert par la **fenêtre** de bureau, celui d'un
  **dossier déposé** sur l'écran de déclaration. Même corps que la route
  ci-dessus, `annule` en moins. C'est la **porte unique** : aucun chemin réel
  n'entre sans passer par elle ;
- `GET  /api/projets/repertoire` — le **répertoire des projets** (#1022) : où
  naît un projet neuf. `par_defaut` dit qu'aucun réglage n'a été posé (c'est
  alors `~/Maestro`) ; la lecture **crée** le dossier s'il manque et le dit
  (`cree`) — « créé à la première utilisation » ;
- `PUT  /api/projets/repertoire` — pose ce répertoire ; `chemin: null` revient au
  défaut. Validé et créé avant d'être stocké, sinon rien n'est écrit ;
- `GET`/`POST`/`DELETE /api/projets/dossiers-autorises` — les **dossiers
  autorisés** depuis l'écran (#1610) : un dossier que l'explorateur refusait
  hors des dossiers explorables s'y ajoute, validé par `valider_racine`, sans
  redémarrage ; il se retire par `DELETE ?chemin=` ;
- `GET  /api/projets/{id}` — un projet déclaré ;
- `POST /api/projets` — déclare un projet : racine **validée** (EF-38, refus
  motivé en 422) et VCS **constaté** sur le disque ;
- `PUT  /api/projets/{id}` — remplace la déclaration (l'intégrale, pas un diff) ;
- `DELETE /api/projets/{id}` — oublie un projet, sans jamais toucher au dossier
  sur le disque ;
- `POST /api/projets/{id}/versionner` — met un projet **non versionné** sous
  Git (#855, verbe de #704) : `git init` puis un premier commit de toute la
  racine, `.gitignore` respecté, et rend la fiche relue — `vcs` constaté. Sans
  corps : le `vcs` ne se déclare pas (EF-38), il se déclenche. Un projet déjà
  versionné rend sa fiche telle quelle ; un refus (`depot-englobant` 409,
  `commit-refuse` 422, `git-indisponible` 503…) porte son motif, jamais un 500 ;
- `GET  /api/projets/{id}/outillage/analyse` — l'**analyse** d'un projet
  existant et l'outillage qu'elle recommande (#1030, docs/38) : la racine lue
  **en lecture seule**, dans des bornes qui voyagent dans la réponse, sans
  jamais exécuter le code du projet. Rend les langages, les gestionnaires, les
  commandes de construction/test/lint, la CI, la forge et les conventions déjà
  écrites — puis chaque skill, script ou fichier d'instructions recommandé avec
  sa **raison** et l'**endroit du projet** qui la justifie, ce que le projet
  porte déjà étant reconnu (`deja-present`) plutôt que dupliqué. Depuis #1158
  les tables n'en sont que les indices : le **modèle lit** le projet (lister,
  lire — dans le périmètre, sans lien suivi) et ce qu'il tire des fichiers lus
  complète les constats ; `lecture` en dit la provenance. N'écrit rien :
  la génération est #1033 ;
- `POST /api/projets/{id}/outillage/report` — le « **plus tard** » de l'étape
  d'outillage (#1034, docs/37 §4.6) : sans corps, idempotent, il n'écrit rien
  dans le dossier de l'utilisateur et rend la fiche relue. Celle-ci porte
  `outillage.a_faire` — reporté **et** manifeste absent —, ce qui fait qu'une
  génération suffit à faire taire le rappel ;
- `POST /api/projets/{id}/equipe/proposition` — l'**équipe** que ce projet
  appelle (#1039, docs/37) : chaque rôle avec sa **raison** et l'endroit du
  projet qui la prouve, son nombre d'**instances** et pourquoi ce nombre, son
  **playbook** (écrit par la mécanique de #257, à défaut celui de son gabarit —
  `playbook_origine` le dit), les **skills** de l'outillage qu'il branche et ses
  **autorisations proposées**, chacune avec sa raison, cran `auto` compris
  (#716). Corps vide : le projet est analysé — sauf un projet encore vide, qui
  reprend les réponses que son manifeste d'outillage garde (#1350) ; avec les
  réponses du questionnaire d'outillage : l'équipe se dérive d'elles. `ecartes` nomme ce qui
  n'est **pas** proposé — l'orchestrateur en fait partie par décision. Avec
  `renfort` (#1227) : **un seul rôle**, celui qu'un plan de run appelle et que
  l'équipe n'a pas, justifié par ce plan et non par un constat — sans quoi il
  serait `ecarte`, aucun constat d'un projet en Python ne désignant un designer.
  **Rien n'est créé** (`cree`, `validation`) : valider et créer est la route
  suivante. Depuis #1159 l'équipe est **composée par le modèle** pour le besoin
  réel du projet — les cinq gabarits n'en sont plus qu'une matière —, et
  `composition` dit qui l'a composée : le modèle, ou les règles des gabarits en
  repli, avec la cause ;
- `POST /api/projets/{id}/equipe/correction` — la personne corrige l'équipe
  proposée **avec ses mots** (#1159) : « ajoute quelqu'un pour la sécurité ». Le
  modèle rend les rôles à ajouter (playbooks écrits pour ce projet), à retirer, à
  remettre, les instances à changer, et une phrase qui lui répond. Rien n'est
  créé ; 502 si le modèle ne répond pas — une phrase n'a pas de règle de repli ;
- `POST /api/projets/{id}/equipe` — **crée** dans le projet l'équipe validée
  (#1040, docs/37) : par rôle gardé, sa fiche et son playbook (les skills
  branchés y sont nommés), sa politique d'autorisations et sa capacité. Le corps
  rapporte la proposition **telle qu'elle a été servie** — la rejouer rendrait un
  autre playbook, donc un agent que personne n'a validé. L'équipe entière est
  vérifiée avant la première écriture : un refus (`equipe-refusee`) les nomme
  tous et **rien** n'est créé. L'équipe se revoit ensuite depuis
  `GET /api/catalogue?projet=…` ;
- `GET  /api/fournisseurs` — ce qui existe côté modèles (#253) **et ce qui est
  déjà là** (#487) : les fournisseurs du **registre**, leurs modèles annoncés et,
  pour chacun, les niveaux d'effort admis (liste vide quand le fournisseur
  n'expose pas ce réglage) ; `modeles_libres` dit qu'un nom hors gamme reste
  recevable ; et, sur la même ligne, ce que la **sonde du poste** en a trouvé
  (`present_ici`, `utilisable_ici`, `modeles_ici`, `constats`), plus
  `hors_registre` — l'outillage présent que Maestro ne sait pas piloter — et les
  `incertitudes` de la sonde ;
- `GET  /api/catalogue` — le catalogue d'agents (#72, EF-03) : les agents par
  défaut du code et les personnalisés persistés, avec leur provenance
  (`source`, **trois** valeurs depuis #259 : `defaut`, `defaut_surcharge`,
  `personnalise`), leurs réglages de modèle **effectifs**
  (`fournisseur`/`modele`/`effort` — #253) et d'où chacun vient (`herite`,
  `reglages_du_code` — #259), leurs serveurs MCP effectifs (#104 —
  `mcp_serveurs`/`mcp_erreur`), ce que leur **seule déclaration héritée** porte
  encore (#263 — `mcp_herites`, ce que la migration ferait passer au pool) et
  leur politique de permissions effective (#110 —
  `permissions`/`permissions_erreur`, écrite par `PUT /api/permissions/{agent}`
  depuis #262, qui sert avec elle les outils **réellement exposés** à l'agent
  dans `permissions_outils`) ;
- `GET  /api/catalogue/{nom}` — la définition complète d'un agent (playbook
  compris) ;
- `POST /api/catalogue` — crée un agent personnalisé (persisté hors du code,
  routable et exécutable par les moteurs construits ensuite) ;
- `POST /api/catalogue/generation` — **propose** une définition d'agent à partir
  d'une intention en une phrase (#257) : rôle, compétences, playbook et réglages
  suggérés, confrontés au registre des fournisseurs. Rien n'est créé — la
  proposition est un brouillon que le formulaire reçoit, et la création reste le
  `POST /api/catalogue` ci-dessus ;
- `PUT  /api/catalogue/{nom}` — remplace la définition d'un agent personnalisé ;
- `DELETE /api/catalogue/{nom}` — supprime un agent personnalisé ;
- `PUT  /api/catalogue/{nom}/reglages` — surcharge les réglages de modèle d'un
  agent **du code** (#259) sans le dupliquer : le rôle, les compétences et le
  playbook restent au code et continuent d'en suivre les évolutions ; ce qui
  n'est pas envoyé reste hérité ;
- `DELETE /api/catalogue/{nom}/reglages` — **annule** cette surcharge (retour aux
  réglages du code). Annuler n'est pas supprimer : l'agent reste au catalogue, et
  la suppression demeure réservée aux personnalisés ;
- `GET  /api/chat/{agent}` — le fil de conversation utilisateur ↔ agent (#84),
  persisté et relu du `ChatStore` ; `?conversation=` en désigne une (#694),
  sinon la plus récente ;
- `GET  /api/chat/{agent}/conversations` — les conversations de l'agent, la plus
  récente d'abord (#694) : identifiant, titre dérivé, ouverture, dernière
  activité, nombre de messages ;
- `POST /api/chat/{agent}/conversations` — en ouvre une neuve et rend sa carte
  (idempotent tant que la plus récente est vierge) ;
- `POST /api/chat/{agent}/messages` — envoie un message à l'agent et rend la
  paire message/réponse (le fil est aussi diffusé en `chat.message` sur le
  WebSocket, réponse comprise) ; `assistance` (#123) y désigne le **canal
  d'aide** — mêmes endpoints, fiche hors catalogue et réponses sans modèle
  (`maestro.controltower.assistance`) — et `orchestrateur` (#268) le **fil
  global** : on y adresse une demande à l'orchestration, qui répond et **ouvre
  un run** quand c'en est une (`maestro.controltower.orchestration`). Le corps
  porte le `projet_id` de la fenêtre (#683) : le run ouvert **appartient** au
  projet actif, donc il figure dans sa liste de runs et s'ouvre en détail. Sans
  projet, le fil ne propose ni n'ouvre aucun run (#1180) ;
- `POST /api/chat/{agent}/cadrage` — **tranche** la demande de cadrage que le
  fil porte (#943) : accepter, refuser, ou accepter un objectif **amendé**, sans
  repasser par la zone de saisie ni par le juge. Le run s'ouvre dans le projet
  **de la proposition** (`projet_vise`, #1180), et non dans celui de la fenêtre
  du clic. Même paire rendue qu'un envoi ;
  `409` quand rien n'attend. Le corps porte aussi les quatre **bornes** du run
  (#990) — coût, tokens, délai par tâche, parallélisme —, aux mêmes noms que sur
  `POST /api/executions` : c'est par ce geste qu'un run lancé depuis l'interface
  se borne, la conversation en étant la seule porte depuis #666 ;
- `POST /api/chat/{agent}/recrutement` — **valide** ou décline l'équipe que le
  fil propose à un projet sans agent (#1146), au lieu d'un run qui n'aurait
  personne pour prendre ses tâches. Le corps porte l'équipe retenue dans la forme
  de `POST /api/projets/{id}/equipe` ; la création passe par la même voie
  (#1040), puis le fil repropose le run demandé. Même paire rendue qu'un envoi ;
  `409` quand rien n'attend. **Le même geste complète l'équipe d'un run
  suspendu** (#1227) : quand la demande du fil porte un `run_id`, la décision est
  publiée sur le bus (`renfort.decision`) et le run repart — accepté avec
  l'équipe complétée, décliné avec celle qu'il a. Rien n'est reproposé alors : le
  run tourne déjà ;
- `GET  /api/chat/{agent}/flux` — la même réponse rendue **au fur et à mesure**
  (SSE, trames `debut`/`fragment`/`fin`/`interrompu`/`erreur`, #268) : un canal,
  valable pour les trois fils ; `?projet_id=` y porte le même rattachement que le
  corps du POST — et n'est pas le `?projet=` des lectures, qui est une portée
  (#277) ;
- `POST /api/chat/{agent}/flux` — le **même flux** pour un message qui embarque
  des sources (#692) : corps de `POST …/messages`, réponse en `text/event-stream`.
  Une URL ne pouvant porter ni identifiants de sources ni corps, le GET reste la
  voie du cas sans source ; les deux verbes appellent le même `ServiceChat.diffuser`
  (arbitrage écrit en tête de `maestro.controltower.chat`) ;
- `POST /api/chat/{agent}/flux/{echange}/arret` — **arrête** la génération en vol
  (#695). Rien n'y est envoyé : c'est le seul verbe qui retire quelque chose au
  canal, et le seul geste qui annule une production — une déconnexion, elle, la
  laisse s'achever (#268). Ce qui a été reçu avant l'arrêt est **persisté** comme
  réponse et porté par la trame `interrompu` ; 404 quand il n'y a rien à arrêter ;
- `WS   /ws/evenements` — le flux d'événements (statuts de tâches, activité
  des agents, messages inter-agents, validations, chat), au format
  `Event.to_dict`.

Contrats d'API **v2** (#183 — formes figées des Phases 5/6, servies pour de vrai
à mesure que leur lot a été livré) :

- `GET  /api/executions` — la liste des runs (résumés : statut, coût, dates,
  ticket) ; `POST /api/executions` — lance un run (objectif + garde-fous) ;
  `POST /api/executions/{run_id}/annuler` — interrompt un run en cours (#185) ;
- `GET  /api/journal` — le journal requêtable (filtres agent / type / run /
  période, tri, pagination) — servi **pour de bon** depuis #478.

Les deux autres, `GET /api/configuration` et `GET /api/playbooks/propositions`,
ne répondaient qu'en fixtures du mode démo et `501` en réel : elles sont parties
avec lui (#1168).

Assemblage : une **pompe** unique s'abonne au bus (`EventBus`), projette chaque
événement sur l'état (`ControlTowerState`), l'ajoute au journal requêtable
(`ServiceJournal`, #478) puis le rediffuse aux WebSockets connectées — l'ordre
« état d'abord, diffusion ensuite » garantit qu'un client qui reçoit un
événement lit un REST déjà à jour. La **consignation** au journal durable
(`EventLog`, #97) n'est plus la sienne depuis #699 : elle a lieu à la
**publication** (`BusDurable`, dont `create_app` enveloppe le bus reçu), parce
qu'un run vit hors de l'API et publie pendant qu'elle est arrêtée — faire
dépendre la durabilité d'un consommateur vivant perdait alors tout, et
définitivement. Au démarrage, le lifespan **rejoue** le journal pour
reconstruire la projection (exécutions, grands livres, analytics, tâches,
agents, validations) **et l'historique requêtable** d'avant un redémarrage de
l'API : l'état survit à la vie du process (#97). `create_app` s'injecte bus, état
et journal (les tests d'API tournent sur `InMemoryEventBus`/`InMemoryEventLog`,
sans Redis) ; `create_default_app` câble les supports que le **déploiement**
désigne (`_supports_configures`, #639) : `RedisEventBus` + `RedisEventLog` en
serveur — le défaut, canal `maestro.evenements` alimenté par
`maestro.controltower.bridge` côté moteur, journal persistant sur la liste
`maestro.evenements:journal` —, `InMemoryEventBus` + `SqliteEventLog` en local,
où plus aucun service n'est à installer.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from functools import partial
from typing import Any

from fastapi import (
    FastAPI,
)
from fastapi.middleware.cors import CORSMiddleware

from maestro.agents.capacity import CapacityStore
from maestro.agents.configuration import ConfigurationAgents
from maestro.agents.mcp import McpStore
from maestro.agents.mcp_admission import (
    ServiceAdmission,
)
from maestro.agents.mcp_registry import (
    RegistreMcp,
)
from maestro.agents.permissions import PermissionStore
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.reprise import reprendre
from maestro.agents.secrets import SecretStore
from maestro.agents.store import (
    AgentStore,
    SurchargeStore,
    catalogue,
    catalogue_du_projet,
)
from maestro.config import ConfigError, Settings, load_settings
from maestro.controltower.acces import GardeAcces, PolitiqueAcces, politique_depuis
from maestro.controltower.accueil import DepotAccueil, ServiceAccueil
from maestro.controltower.acquis import magasin_acquis_configure
from maestro.controltower.assistance import (
    RepondeurAssistance,
)
from maestro.controltower.assistance_documentee import RepondeurAssistanceDocumentee
from maestro.controltower.attentes import ServiceAttentes
from maestro.controltower.auto_amelioration import (
    AnalyseurEchecs,
    RedacteurPlaybook,
)
from maestro.controltower.battement import (
    RegistreBattements,
    RegistreBattementsMemoire,
    RegistreBattementsRedis,
)
from maestro.controltower.bilan import JugeBilan, ServiceBilan
from maestro.controltower.bornes import AUCUNE_BORNE, BornesRun
from maestro.controltower.chat import (
    ChatStore,
    ProjetVise,
    RepondeurChat,
    RepondeurModele,
    ServiceChat,
)
from maestro.controltower.consultation import Consultations, Demande, Lecture
from maestro.controltower.depense_du_fil import au_compte_du_fil
from maestro.controltower.equipe import CompositeurEquipe, ServiceEquipe
from maestro.controltower.events import (
    ACTEUR_RUN,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_RENFORT_DEMANDE,
    Event,
    EventBus,
    InMemoryEventBus,
    RedisEventBus,
)
from maestro.controltower.executions import (
    FabriqueMoteur,
    LecteurSources,
    ServiceExecutions,
)
from maestro.controltower.fournisseur_defaut import (
    ServiceFournisseurParDefaut,
)
from maestro.controltower.generation_agent import (
    GenerateurDefinitionAgent,
)
from maestro.controltower.hote import (
    HOTE_RUN_DETACHE,
    HOTE_RUN_EN_PROCESS,
    HOTES_RUN,
    HoteRun,
)
from maestro.controltower.journal import (
    ServiceJournal,
)
from maestro.controltower.magasin import GardeMagasin, Magasin, MagasinRefuse
from maestro.controltower.naissance import ServiceNaissance
from maestro.controltower.orchestration import (
    AGENT_ORCHESTRATION,
    RepondeurOrchestration,
    apercu_de,
    attentes_de,
    detail_du_run,
    faits_des_runs,
    outillage_en_clair,
)
from maestro.controltower.outillage import (
    ComprehensionModele,
    ConducteurOutillage,
    ServiceOutillage,
)
from maestro.controltower.persistence import (
    SUPPORT_SQLITE,
    BusDurable,
    EventLog,
    InMemoryEventLog,
    journal_configure,
    support_persistance,
)
from maestro.controltower.pieces import CorrectionModele, ServicePieces
from maestro.controltower.projets import (
    ProjetIllisible,
    ProjetInconnu,
    ServiceProjets,
)
from maestro.controltower.recit import ConteurDeFin, RedacteurRecit
from maestro.controltower.regime import MembreDeLEquipe, regime_d_un_run
from maestro.controltower.renfort import RelaisRenfort
from maestro.controltower.routes import ROUTEURS
from maestro.controltower.routes.communs import Diffusion, creer_equipe_du_projet
from maestro.controltower.routes.contexte import Contexte
from maestro.controltower.state import (
    STATUTS_EXECUTION_TERMINAUX,
    ControlTowerState,
)
from maestro.controltower.validation import ValidateurControlTower
from maestro.engine.acquis import MagasinAcquis
from maestro.engine.brief import MODE_BRIEF_AUTO
from maestro.installation import LecteurInstallation
from maestro.messaging import InMemoryMailbox, Mailbox, RedisMailbox
from maestro.outillage.contexte import outillage_du_projet
from maestro.poste import SondePoste
from maestro.projets import (
    Projet,
)
from maestro.providers.base import ModelProvider
from maestro.sources import DepotTeleversements

_LOGGER = logging.getLogger("maestro.controltower")


def _reprise_agents_activee() -> bool:
    """La reprise des agents dans leur projet (#1038) est-elle jouée au démarrage ?

    `MAESTRO_REPRISE_AGENTS=0` s'en passe — même convention que les autres
    mécanismes best-effort du dépôt (`MAESTRO_WORKFLOW_POSE`,
    `MAESTRO_AUDIT_FIN_RUN`…) : on coupe avec un `0`, jamais avec une valeur
    inventée. Lue **à chaque démarrage** et non figée à l'import, pour qu'un test
    puisse la poser sans réimporter le module.
    """
    return (os.environ.get("MAESTRO_REPRISE_AGENTS") or "").strip() != "0"


#: Attente entre deux reprises de la pompe après une perte du bus (#1206), en
#: secondes : on repart vite, puis on espace jusqu'au plafond — un Redis éteint
#: pour la nuit ne doit pas remplir le journal technique.
REPRISE_POMPE_S = 1.0


REPRISE_POMPE_MAX_S = 10.0


async def _pompe(  # noqa: C901
    bus: EventBus,
    state: ControlTowerState,
    diffusion: Diffusion,
    journal: ServiceJournal,
    *,
    magasin: Magasin | None = None,
    rejouer: Callable[[], Awaitable[None]] | None = None,
    reprise_s: float = REPRISE_POMPE_S,
    reprise_max_s: float = REPRISE_POMPE_MAX_S,
    sur_fin_de_run: Callable[[str], None] | None = None,
    sur_demande_de_renfort: Callable[[Event], None] | None = None,
) -> None:
    """Le seul consommateur du bus : projette sur l'état, indexe, puis rediffuse.

    Cet ordre rend le flux cohérent pour les clients : à réception d'un
    événement WebSocket, l'état REST le reflète déjà — le journal requêtable
    (#478) compris, d'où sa consignation **avant** la diffusion : un client qui
    recharge sur un événement qu'il vient de recevoir en direct doit le
    retrouver dans son historique, jamais l'inverse.

    **Une panne du bus ne l'arrête plus** (#1206). Elle s'arrêtait pour de bon :
    un Redis coupé puis relancé laissait l'API servir une projection figée,
    sans flux, jusqu'au redémarrage — et sans le dire. Elle réessaie désormais,
    en espaçant ses essais (`reprise_s` → `reprise_max_s`), et rejoue d'abord le
    journal si le démarrage n'avait pas pu (`magasin.rejeu_fait`). La panne est
    tracée une fois par coupure, pas à chaque essai ; pendant ce temps le
    magasin la sert aux écrans (`GardeMagasin`). Un abonnement qui se **termine**
    sans erreur est un bus refermé : la pompe s'arrête avec lui.

    ⚠ **Elle ne consigne plus au journal durable** (#699). Elle en fut le seul
    écrivain de #97 à ce lot, et c'est ce qui faisait dépendre la durabilité
    d'un **consommateur vivant** : le pub/sub Redis ne bufferise rien, donc tout
    ce qu'un run détaché publiait pendant que l'API était arrêtée n'était
    consigné par personne, puis perdu. La consignation a suivi la publication
    (`persistence.BusDurable`, `bridge.publieur_redis`), et la retirer d'ici
    n'est pas une conséquence mais la **moitié** du remède : deux écrivains sans
    dédoublonnage — un `Event` n'a pas d'identifiant — auraient doublé chaque
    ligne du journal requêtable au lieu d'en perdre.

    `sur_fin_de_run` (#1224) est appelé **après** la projection, et c'est tout ce
    qui le justifie ici plutôt que dans un second abonné au bus : le récit de fin
    lit ce que le run a fait dans `state`, et un abonné parallèle le lirait
    parfois avant que l'événement y soit appliqué. Il reçoit un `run_id`, rend la
    main tout de suite (le travail part dans une tâche), et **ne peut pas
    arrêter la pompe** — une fin de run qui ne se raconte pas ne doit pas
    interrompre le flux temps réel de tout le monde.

    `sur_demande_de_renfort` (#1260) suit le même régime, pour la même raison :
    le relais qui pose dans le fil la demande de renfort d'un run
    (`maestro.controltower.renfort.RelaisRenfort`). Il reçoit l'événement
    entier — la demande y est composée —, rend la main tout de suite et ne peut
    pas arrêter la pompe. Le **rejeu** du journal ne le rappelle pas : une
    demande d'avant le redémarrage a déjà sa trace dans le fil, et le run qui
    l'attendait tient sa propre borne.
    """
    attente = reprise_s
    en_panne = False
    while True:
        try:
            if magasin is not None and rejouer is not None and not magasin.rejeu_fait:
                await rejouer()
            async for event in bus.subscribe():
                if en_panne:
                    _LOGGER.info("La pompe d'événements a repris : flux temps réel rétabli.")
                    en_panne = False
                attente = reprise_s
                state.appliquer(event)
                # Rattaché **après** la projection, qui vient d'apprendre le
                # projet du run si c'est cet événement qui le porte (#1290).
                event = state.au_projet_de_son_run(event)
                journal.consigner(event)
                diffusion.diffuser(event)
                if (
                    sur_fin_de_run is not None
                    and event.type == EVENEMENT_EXECUTION_STATUT
                    and event.statut in STATUTS_EXECUTION_TERMINAUX
                    and event.run_id
                ):
                    try:
                        sur_fin_de_run(event.run_id)
                    except Exception:  # noqa: BLE001 — le flux passe avant le récit
                        _LOGGER.exception(
                            "Fin du run %s non racontée dans son fil.", event.run_id
                        )
                if (
                    sur_demande_de_renfort is not None
                    and event.type == EVENEMENT_RENFORT_DEMANDE
                ):
                    try:
                        sur_demande_de_renfort(event)
                    except Exception:  # noqa: BLE001 — le flux passe avant le relais
                        _LOGGER.exception(
                            "Demande de renfort du run %s non relayée.", event.run_id
                        )
            return
        except asyncio.CancelledError:
            raise
        except Exception:
            if magasin is not None:
                magasin.oublier()
            if not en_panne:
                _LOGGER.exception(
                    "La pompe d'événements a perdu le magasin : flux temps réel "
                    "interrompu, l'API sert la panne et réessaie."
                )
                en_panne = True
        await asyncio.sleep(attente)
        attente = min(attente * 2, reprise_max_s)


def create_app(
    *,
    bus: EventBus | None = None,
    state: ControlTowerState | None = None,
    playbooks: PlaybookStore | None = None,
    agents_store: AgentStore | None = None,
    surcharges: SurchargeStore | None = None,
    mailbox: Mailbox | None = None,
    chat_store: ChatStore | None = None,
    chat_repondeur: RepondeurChat | None = None,
    assistance_repondeur: RepondeurChat | None = None,
    orchestration_repondeur: RepondeurChat | None = None,
    recit_redacteur: RedacteurRecit | None = None,
    bilan_juge: JugeBilan | None = None,
    analyseur: AnalyseurEchecs | None = None,
    redacteur_playbook: RedacteurPlaybook | None = None,
    generateur_agent: GenerateurDefinitionAgent | None = None,
    compositeur_equipe: CompositeurEquipe | None = None,
    lecteur_outillage: ModelProvider | None = None,
    capacites: CapacityStore | None = None,
    mcp: McpStore | None = None,
    registre_mcp: RegistreMcp | None = None,
    admissions: ServiceAdmission | None = None,
    secrets: SecretStore | None = None,
    permissions: PermissionStore | None = None,
    projets: ServiceProjets | None = None,
    event_log: EventLog | None = None,
    journal: ServiceJournal | None = None,
    battements: RegistreBattements | None = None,
    fabrique_moteur: FabriqueMoteur | None = None,
    televersements: DepotTeleversements | None = None,
    lecteur_sources: LecteurSources | None = None,
    hote_run: HoteRun | None = None,
    sonde_poste: SondePoste | None = None,
    fournisseur_defaut: ServiceFournisseurParDefaut | None = None,
    accueil: ServiceAccueil | None = None,
    installation: LecteurInstallation | None = None,
    acces: PolitiqueAcces | None = None,
    comprehension: ComprehensionModele | None = None,
    acquis: MagasinAcquis | None = None,
) -> FastAPI:
    """Construit l'app FastAPI de la Control Tower autour d'un bus et d'un état.

    Par défaut : bus mémoire et état neuf (agents du catalogue, statut libre) —
    la configuration des tests. La production passe
    par `create_default_app` (bus Redis). La pompe vit avec l'app (lifespan) :
    démarrée à l'ouverture, annulée à l'arrêt, bus refermé derrière elle.

    `playbooks` (#76) est le dépôt versionné servi par les endpoints
    `/api/playbooks` — par défaut celui de la config (`MAESTRO_PLAYBOOKS_DIR`,
    sinon `core/playbooks/` du dépôt) ; les tests en injectent un temporaire.

    `agents_store` (#72) est le dépôt des agents personnalisés servi par les
    endpoints `/api/catalogue` — par défaut celui de la config
    (`MAESTRO_AGENTS_DIR`, sinon `core/agents/` du dépôt). L'état par défaut se
    construit sur le catalogue **effectif** : les agents personnalisés déjà
    persistés sont présents dès le démarrage, comme ceux du code.

    `surcharges` (#259) est le dépôt des **surcharges** posées sur les agents du
    code — les trois réglages de modèle qu'on peut leur changer sans les
    dupliquer (`MAESTRO_SURCHARGES_DIR`, sinon `core/surcharges/`). Distinct
    d'`agents_store` : celui-ci stocke des agents, celui-là des réglages posés
    sur des agents qui existent déjà. Le catalogue effectif les applique, donc
    l'état de démarrage les porte aussi.

    `mailbox`, `chat_store` et `chat_repondeur` (#84) portent le chat
    utilisateur ↔ agent des endpoints `/api/chat` : la messagerie inter-agents
    où transitent les échanges (mémoire par défaut, Redis en production), le
    fil persisté (`MAESTRO_CHAT_DIR`, sinon `core/chat/` du dépôt) et la
    production de la réponse — par défaut le fournisseur configuré, cadré par
    le playbook courant de l'agent ; les tests injectent un répondeur scripté.

    `assistance_repondeur` (#123) porte le **canal d'aide** `/api/chat/assistance` :
    un second `ServiceChat` sur le même fil persisté, la même messagerie et le même
    bus que le chat, mais avec son propre répondeur — depuis #764
    `RepondeurAssistanceDocumentee`, qui choisit ses sections dans la documentation
    du produit (#763) et cite ce qu'il a lu, avec le déterministe
    `RepondeurAssistance` en **repli** quand aucun fournisseur n'est joignable.
    Les questions portent sur l'outil, pas sur le projet ; voir
    `maestro.controltower.assistance_documentee`.

    `orchestration_repondeur` (#268) porte le **fil global**
    `/api/chat/orchestrateur` : un troisième `ServiceChat` sur les mêmes rouages,
    dont le répondeur par défaut (`RepondeurOrchestration`) **agit** — une demande
    de travail y est proposée par le modèle (#685), et l'accord de l'utilisateur y
    ouvre un run par le service d'exécutions de cette app, dont la réponse porte
    l'identifiant. Il est construit **après** `executions`, dont il tient son
    lanceur ; c'est aussi le point d'injection qui permet de jouer le fil **sans
    fournisseur** — les tests y mettent un répondeur scripté, un répondeur à
    fournisseur factice, ou aucun lanceur.

    `bilan_juge` (#1284) rend le **bilan sur pièces** de chaque run à sa fin
    (`maestro.controltower.bilan`), servi par `GET /api/executions/{run_id}/bilan`
    et lu par le récit de fin. Par défaut le modèle du poste, résolu au premier
    bilan ; les tests en injectent un scripté, et la suite neutralise le défaut
    (`tests/conftest.py`).

    `analyseur` (#139) produit les propositions d'auto-amélioration servies par
    `POST /api/playbooks/{agent}/propositions` : à la demande, il analyse les
    échecs d'un run via la couche fournisseur et enregistre un brouillon (#138).
    Par défaut il partage le dépôt `playbooks` et résout son fournisseur par
    config ; les tests (#137) en injectent un à fournisseur factice.

    `redacteur_playbook` (#261) produit les réécritures servies par
    `POST /api/playbooks/{agent}/redaction` : à partir du brouillon en cours dans
    l'éditeur et d'une consigne libre, le modèle rend une version réécrite —
    **rien n'est enregistré**, ni version ni proposition, l'éditeur l'affiche en
    différentiel et l'utilisateur décide. Par défaut il résout son fournisseur
    par config ; les tests en injectent un factice.

    `generateur_agent` (#257) produit les définitions d'agent proposées par
    `POST /api/catalogue/generation` : à partir d'une intention en une phrase, le
    modèle propose rôle, compétences, playbook et réglages — **rien n'est
    enregistré**, la proposition est un brouillon que le formulaire reçoit et que
    l'utilisateur crée par le `POST /api/catalogue` ordinaire. Par défaut il
    résout son fournisseur par config ; les tests en injectent un factice.

    `compositeur_equipe` (#1159) compose l'équipe d'un projet pour son besoin
    réel (`POST …/equipe/proposition`) et la corrige en langage naturel
    (`POST …/equipe/correction`). Par défaut il résout son fournisseur par
    config ; les tests en injectent un factice.

    `lecteur_outillage` (#1158) est le fournisseur de modèle qui **lit** un projet
    existant pour `GET /api/projets/{id}/outillage/analyse` et la génération qui
    en découle : il demande à lister et lire, dans le périmètre du projet, et
    ses constats sont confrontés à ce qu'il a lu. Par défaut il est résolu par
    config au premier usage ; sans fournisseur, l'analyse reste celle des tables
    et le dit. Les tests en injectent un factice.

    `capacites` (#86) est le dépôt du contrôle de capacité servi par
    `POST /api/agents/{nom}/capacite` — par défaut celui de la config
    (`MAESTRO_CAPACITE_DIR`, sinon `core/capacite/` du dépôt) : le même que
    relisent moteur et workers, pour que le réglage ait un effet réel. L'état
    par défaut est amorcé des réglages déjà persistés (un état injecté reste,
    lui, tel quel — la configuration des tests).

    `mcp` (#104) est le dépôt des serveurs MCP déclarés par agent, affichés en
    **lecture seule** sur les fiches du catalogue (`mcp_serveurs`, valeurs de
    secrets masquées) — par défaut celui de la config (`MAESTRO_MCP_DIR`, sinon
    `core/mcp/` du dépôt) : le même que montent moteur et workers.

    `registre_mcp` (#131) est la **bibliothèque** de serveurs MCP servie par
    `/api/mcp/registre` : des templates recherchables (nom/tag/éditeur) portant
    transport, gabarit `${VAR}`, mode d'auth (docs/21) et procédure côté outil.
    Par défaut elle est **fédérée** (#677) : le seed curé en code — l'allowlist
    supply-chain du garde-fou docs/19, seule instanciable — plus les entrées
    découvertes dans le miroir du registre officiel, s'il y en a un sur le
    disque. Sans miroir, le résultat est exactement le seed d'avant. Un registre
    **injecté** court-circuite la fédération : c'est ainsi que les tests servent
    une allowlist restreinte, et la valeur injectée est alors la seule servie.

    `secrets` (#132/#133) est le **coffre chiffré** des secrets d'intégrations :
    l'UI d'écriture (`POST /api/mcp/pool`) y pose les secrets du pool projet
    **une seule fois** (coffre projet, `SecretStore.enregistrer_projet`), partagés
    par tout agent qui active l'intégration — par défaut celui de la config
    (`MAESTRO_SECRETS_DIR`, sinon `core/secrets/` du dépôt), le même que résolvent
    moteur et workers au montage. Les tests en injectent un coffre temporaire.

    `permissions` (#110) est le dépôt des politiques allow/ask/deny par agent,
    servies sur les fiches du catalogue (`permissions`, la politique effective
    appliquée à l'exécution) et **écrites** depuis leur onglet MCP & permissions
    (#262, `PUT /api/permissions/{agent}`) — par défaut celui de la config
    (`MAESTRO_PERMISSIONS_DIR`, sinon `core/permissions/` du dépôt) : le même que
    relisent moteur et workers, d'où l'application à la tâche suivante sans
    redémarrage. Les tests en injectent un dépôt temporaire, comme pour le
    coffre.

    `projets` (#223) porte le CRUD des projets de l'utilisateur et l'explorateur
    de dossiers servis par `/api/projets` — par défaut le service de la config
    (dépôt `MAESTRO_PROJETS_DIR`, sinon `core/projets/` du dépôt ; racines
    explorables `MAESTRO_EXPLORATEUR_RACINES`, sinon le dossier utilisateur).
    Les tests en injectent un service sur dépôt temporaire, seule façon de fixer
    les racines explorables sans dépendre du poste qui joue la suite.

    `event_log` (#97) est le **journal durable des événements** que le lifespan
    **rejoue au démarrage** pour reconstruire la projection (exécutions, grands
    livres, analytics, tâches, agents, validations) après un redémarrage de
    l'API — par défaut un journal mémoire (pas de durabilité inter-redémarrage :
    la configuration des tests). La production câble celui que le **réglage**
    désigne via `create_default_app` (`journal_configure`, #639) : `RedisEventLog`
    en serveur, `SqliteEventLog` en local. Un état injecté (`state`) reste tel
    quel puis reçoit le rejeu par-dessus (idempotent : les événements
    reconstruisent le même état).

    ⚠ Depuis #699 il est aussi ce que le **bus marie à la publication** : le bus
    reçu est enveloppé dans un `BusDurable` sur ce journal-là, si bien que tout
    ce que l'API publie est consigné à l'instant où elle le publie, et non plus
    quand sa pompe le reçoit. Le bus passé ici doit donc être un **transport
    nu** — l'envelopper une seconde fois en amont doublerait chaque ligne.
    Corollaire pour un appelant qui garde la main sur le bus qu'il injecte
    (quelques tests) : ce qu'il publie **par sa propre référence** est
    diffusé et projeté comme avant, mais n'est pas consigné — il court-circuite
    le producteur, donc le geste qui consigne.

    `journal` (#478) est le **journal requêtable** servi par `GET /api/journal` :
    l'index transverse, filtrable et paginé, des événements consignés. Il n'a
    aucune durabilité propre — il se remplit du rejeu d'`event_log` à l'ouverture
    puis de la pompe au fil de l'eau —, ce qui fait de lui une **vue** du journal
    durable et non un second stockage à tenir d'accord avec lui.

    `battements` (#348) est le registre où l'**hôte** d'un run pose son signal de
    vie, et où `GET /api/executions` lit la vitalité de chaque run non soldé
    (vivant / orphelin / indéterminé) — par défaut un registre mémoire, qui ne
    voit battre que les runs de ce process. La production câble
    `RegistreBattementsRedis` via `create_default_app` : c'est ce qui fait qu'un
    run lancé par `maestro-run --publier` reste reconnu vivant **à travers un
    redémarrage de l'API**, la lecture ne dépendant alors d'aucun process.

    `acquis` (#1391) est le **magasin d'état acquis** des runs : le plan exécutable
    et les issues réussies de chacun, que `POST /api/executions/{id}/reprendre`
    relit pour reprendre un run interrompu sur son plan. Par défaut un magasin
    mémoire, qui ne survit à rien ; la production câble
    `magasin_acquis_configure` via `create_default_app` — celui du journal durable,
    Redis ou SQLite, que le process détaché d'un run remplit de son côté.

    `fabrique_moteur` (#185) construit le moteur de chaque exécution lancée par
    `POST /api/executions` — par défaut `OrchestrationEngine.default`, résolu au
    **premier lancement** et non à la construction de l'app : une API qui ne
    lance aucun run ne résout aucun fournisseur. Les tests en injectent une
    fabrique factice pour exercer le pilotage sans appeler de modèle.

    `televersements` (#317) est le dépôt où `POST /api/sources` pose les octets
    reçus, et où le lancement retrouve par identifiant ce qu'il doit rattacher au
    run — par défaut celui de la config
    (`<MAESTRO_INGESTION_DIR|core/ingestion>/_televersements/`). Les tests en
    injectent un dépôt temporaire : c'est la seule façon de plafonner sans
    écrire dix mégaoctets sur le disque de qui joue la suite.

    `lecteur_sources` (#316) lit la matière d'un objectif et rend son rapport de
    lecture — par défaut `lecteur_par_defaut()`, qui montre en plus les images au
    modèle du poste (#1163). Injectable parce qu'une source `url` part sur le
    réseau et qu'une image part au modèle : `tests/conftest.py` (#195, #782) exige
    qu'aucun test n'ait besoin ni de l'un ni de l'autre.

    `sonde_poste` (#487) est ce qui répond à « qu'est-ce qui est déjà installé
    ici ? » pour `GET /api/fournisseurs` — par défaut `SondePoste()`, qui lit le
    `PATH`, l'environnement et la boucle locale. Injectable parce qu'elle sort du
    process : `tests/conftest.py` (#195) exige qu'aucun test n'ait besoin d'un
    backend, et la suite lui donne donc un poste décrit plutôt que le vrai. Elle
    n'est appelée qu'à la requête — une API qu'on ne consulte pas ne sonde rien.

    `fournisseur_defaut` (#1609) lit, éprouve et enregistre le fournisseur par
    défaut du poste (`/api/fournisseurs/defaut`, `/api/fournisseurs/epreuve`).
    Par défaut il éprouve avec le vrai fournisseur : un test lui injecte son
    éprouveur, comme il injecte une sonde.

    `accueil` (#642) dit si le premier lancement reste à faire sur ce poste
    (`/api/accueil`) : par défaut, le dépôt des réglages du poste et les projets
    déclarés par `projets`.

    `installation` (#1622) dit ce que cette installation fait tourner
    (`/api/installation`) : la version du produit et le CLI `claude` que le
    produit lance. Injectable parce qu'il sort du process — il lance le CLI
    avec `--version` : un test lui décrit un poste, comme il décrit une sonde.

    `hote_run` (#442) est **où** les exécutions se déroulent : le contrat d'hôte
    de run (`maestro.controltower.hote`). Même point d'injection que
    `fabrique_moteur`, et pour une raison voisine : la fabrique décide de *quoi*
    déroule le run, l'hôte de *où* il vit. C'est par lui que l'hôte survivant à
    l'API (#441) se câble, sans que les routes, les événements ni la projection en
    sachent quoi que ce soit. `None` laisse le service se donner
    `HoteRunEnProcess` — la tâche de fond, la configuration des tests ; la
    **production** câble l'hôte détaché, résolu depuis
    l'environnement par `create_default_app` (#446).

    `acces` (#638) est **ce que l'API accepte** : le jeton exigé et les origines
    autorisées (`maestro.controltower.acces`). `None` construit une app
    **ouverte** — aucun jeton, toutes origines : c'est la configuration des
    tests, qui montent l'app sans rien savoir d'un secret du poste, et elle est
    explicite plutôt que déduite. La **production** passe par
    `create_default_app`, qui résout la politique depuis l'environnement et sert
    donc durcie ; le portier vit en middleware ASGI, devant toutes les routes et
    devant le WebSocket.
    """
    ctx = Contexte()
    ctx.chat_repondeur = chat_repondeur
    acces = acces if acces is not None else PolitiqueAcces.ouverte()
    event_log = event_log if event_log is not None else InMemoryEventLog()
    ctx.event_log = event_log
    # Ce que l'API sait de son magasin (#1206) : la sonde est celle du journal
    # lui-même, donc un journal en process (les tests) ne tombe jamais.
    magasin = Magasin(event_log.sonder, lieu=event_log.lieu())
    ctx.magasin = magasin
    # Le mariage du transport et de la mémoire longue (#699), fait **ici** et une
    # seule fois : tout ce qui publie en aval — routes, services, hôte en process
    # — reçoit ce bus-là, donc consigne en publiant, sans que rien n'ait à s'en
    # souvenir. Le journal n'appartient pas au bus : le lifespan le relit au
    # démarrage et le referme en partant, d'où `possede_le_journal` laissé à
    # False.
    bus = BusDurable(bus if bus is not None else InMemoryEventBus(), event_log)
    ctx.bus = bus
    journal = journal if journal is not None else ServiceJournal()
    ctx.journal = journal
    battements = battements if battements is not None else RegistreBattementsMemoire()
    ctx.battements = battements
    agents_store = agents_store if agents_store is not None else AgentStore.default()
    ctx.agents_store = agents_store
    surcharges = surcharges if surcharges is not None else SurchargeStore.default()
    capacites = capacites if capacites is not None else CapacityStore.default()
    # Construire la sonde ne sonde rien (#487) : elle n'a ni état ni cache, et ne
    # regarde le poste qu'au moment où `GET /api/fournisseurs` le demande.
    sonde_poste = sonde_poste if sonde_poste is not None else SondePoste()
    ctx.sonde_poste = sonde_poste
    mcp = mcp if mcp is not None else McpStore.default()
    ctx.mcp = mcp
    # La bibliothèque MCP a **trois sources** depuis #678 : le seed curé (en
    # code), les entrées admises (le journal, sur le disque) et le miroir du
    # registre officiel (sur le disque aussi). Un registre **injecté**
    # l'emporte toujours — c'est ainsi que les tests servent une allowlist
    # restreinte, et rien de ce qui suit ne s'applique à eux. Sinon on fédère à
    # la lecture, mémoïsé sur l'empreinte du miroir : la bibliothèque suit donc
    # un rafraîchissement sans redémarrage, et ne repaie la traduction de
    # 25 000 entrées qu'au moment où le miroir a effectivement bougé.
    registre_mcp_injecte = registre_mcp
    ctx.registre_mcp_injecte = registre_mcp_injecte
    # La **porte d'admission** (#678) : le geste humain tracé qui fait entrer une
    # entrée découverte dans l'allowlist. Le service écrit le journal
    # (`admissions.json`, à côté du pool) ; la bibliothèque le relit à la
    # composition suivante. ⚠ Un `registre_mcp` **injecté** ne relit rien : c'est
    # une allowlist figée, donc les routes d'admission écrivent bien mais leur
    # effet ne s'y verra pas — le contrat d'un registre injecté est justement de
    # ne dépendre d'aucun disque.
    admissions = admissions if admissions is not None else ServiceAdmission.default()
    ctx.admissions = admissions
    secrets = secrets if secrets is not None else SecretStore.default()
    ctx.secrets = secrets
    fournisseur_defaut = (
        fournisseur_defaut
        if fournisseur_defaut is not None
        else ServiceFournisseurParDefaut(coffre=secrets)
    )
    ctx.fournisseur_defaut = fournisseur_defaut
    permissions = permissions if permissions is not None else PermissionStore.default()
    ctx.permissions = permissions
    projets = projets if projets is not None else ServiceProjets.default()
    ctx.projets = projets
    # Le premier lancement (#642) se juge sur les projets **de ce service** : un
    # poste qui en déclare un n'est plus à son premier lancement.
    installation = installation if installation is not None else LecteurInstallation()
    ctx.installation = installation
    accueil = (
        accueil
        if accueil is not None
        else ServiceAccueil(DepotAccueil.default(), lambda: len(projets.lister()))
    )
    ctx.accueil = accueil
    # L'analyse d'outillage (#1030) se greffe sur le **même** service de projets :
    # elle n'a pas de dépôt à elle, et un second lecteur de fiches finirait par ne
    # plus refuser les mêmes racines que le premier. La **génération** (#1033) y
    # ajoute un validateur, et c'est le seul qu'elle ait : écrire l'outillage d'un
    # projet versionné est une action sensible au sens exact de EF-37, et elle
    # passe donc par le canal de validation de toujours — la demande sort sur le
    # bus, l'écran la montre, `POST /api/validations/{tache}/decision` la tranche.
    # Le questionnaire d'un projet neuf **comprend** par le modèle (#1147) : un seul
    # `ComprehensionModele` pour ses deux voies — celle sans état rangée sous le
    # projet, celle du fil de l'orchestration. Injecté par les tests (un faux
    # fournisseur), résolu sur celui du poste au premier usage sinon.
    comprehension = comprehension if comprehension is not None else ComprehensionModele()
    outillage = ServiceOutillage(
        projets,
        validateur=ValidateurControlTower(bus),
        provider=lecteur_outillage,
        comprehension=comprehension,
    )
    ctx.outillage = outillage
    # La projection part du catalogue **hors projet** : les agents rangés à la
    # racine du dépôt. Vide sur un poste neuf depuis #1042 — les cinq rôles du
    # code n'y sont plus —, et c'est voulu : le parc d'agents se peuple projet par
    # projet, par l'équipe que chacun valide.
    # ⚠ Ce vide est donc l'état **normal** de la projection, et c'est pourquoi
    # `GET /api/agents?projet=` ne s'y adosse plus (#1101) : elle la filtrait sur
    # l'équipe demandée, si bien qu'une équipe validée disparaissait de l'écran au
    # redémarrage suivant. La vue d'un projet se dérive désormais de son catalogue
    # et de ses capacités (`ControlTowerState.equipe`), la projection n'y apportant
    # que l'activité — la seule chose qu'elle soit seule à savoir.
    state = (
        state
        if state is not None
        else ControlTowerState(catalogue(agents_store), capacites=capacites.lister())
    )
    ctx.state = state
    playbooks = playbooks if playbooks is not None else PlaybookStore.default()
    # Les six dépôts d'un bloc (#1038) : c'est cet objet-là que `_config` cadre sur
    # le projet demandé, une fois par requête. Les variables individuelles restent
    # pour ce qui n'a pas de projet à connaître — le démarrage, le chat, l'analyse
    # d'auto-amélioration —, et elles désignent le **même** niveau : les gabarits.
    gabarits = ConfigurationAgents(
        agents=agents_store,
        surcharges=surcharges,
        playbooks=playbooks,
        permissions=permissions,
        mcp=mcp,
        capacites=capacites,
    )
    ctx.gabarits = gabarits
    # L'outillage s'écrit **pièce par pièce dans la conversation** (#1161) : le même
    # service d'outillage lit le projet, les mêmes verdicts de commandes sont joués
    # avant chaque carte, et une correction dite avec des mots est comprise par le
    # modèle du poste — celui du lecteur d'outillage quand il est injecté. Chaque pièce
    # écrite rebranche l'équipe du projet et constate ce qu'elle reçoit (#1520) : les
    # **mêmes** dépôts d'agents que la création d'équipe, cadrés sur le projet.
    pieces = ServicePieces(
        outillage, projets, correction=CorrectionModele(lecteur_outillage), equipe=gabarits
    )
    # Un seul conducteur pour les deux moments où l'outillage se propose : pendant la
    # conversation (#1161), et à la fin d'un run, quand le projet construit fait changer
    # ce que ses commandes rendent (#1343, `ConteurDeFin`).
    conducteur = ConducteurOutillage(comprehension, pieces=pieces)
    analyseur = analyseur if analyseur is not None else AnalyseurEchecs(playbooks=playbooks)
    ctx.analyseur = analyseur
    redacteur_playbook = (
        redacteur_playbook if redacteur_playbook is not None else RedacteurPlaybook()
    )
    ctx.redacteur_playbook = redacteur_playbook
    generateur_agent = (
        generateur_agent if generateur_agent is not None else GenerateurDefinitionAgent()
    )
    ctx.generateur_agent = generateur_agent
    # La proposition d'équipe (#1039) se greffe sur les trois pièces qu'elle a
    # besoin de connaître et sur aucune autre : le service de projets (pour
    # résoudre et analyser le projet visé), la configuration d'agent au niveau
    # des **gabarits** (pour savoir quels noms sont déjà pris) et le générateur
    # de #257 (pour écrire les playbooks). Le **même** générateur que
    # `POST /api/catalogue/generation` : deux instances auraient deux
    # fournisseurs à tenir d'accord, et un test qui en injecte un n'en verrait
    # qu'un. Le **même** service porte aussi la création (#1040) : c'est le seul
    # endroit qui sait cadrer les six dépôts sur le projet visé, et le séparer en
    # deux services obligerait à tenir deux fois d'accord ce qui est proposé et
    # ce qui est écrit.
    equipe = ServiceEquipe(
        projets,
        gabarits,
        generateur=generateur_agent,
        compositeur=(
            compositeur_equipe if compositeur_equipe is not None else CompositeurEquipe()
        ),
    )
    ctx.equipe = equipe
    mailbox = mailbox if mailbox is not None else InMemoryMailbox()
    ctx.mailbox = mailbox
    chat_store = chat_store if chat_store is not None else ChatStore.default()
    ctx.chat_store = chat_store
    # Un seul dépôt de téléversement (#317) pour la route qui reçoit les octets et
    # pour tout service qui les rattache : deux instances pointeraient le même
    # disque, mais l'injection des tests n'en tiendrait qu'une. Résolu **avant**
    # les services parce que le fil en dépend désormais aussi (#482) — un message
    # peut porter des sources, et il les résout par la même chaîne qu'un
    # lancement.
    televersements = (
        televersements if televersements is not None else DepotTeleversements.default()
    )
    ctx.televersements = televersements
    chat = ServiceChat(
        store=chat_store,
        repondeur=(
            chat_repondeur if chat_repondeur is not None else RepondeurModele(playbooks=playbooks)
        ),
        mailbox=mailbox,
        bus=bus,
        televersements=televersements,
    )
    ctx.chat = chat
    # Le canal d'aide (#123) : mêmes rouages que le chat — persistance, messagerie,
    # bus — pour que le fil se relise et se diffuse à l'identique ; seul le
    # répondeur change, l'assistant ne parlant pas du projet mais de l'outil. Le
    # dépôt en fait partie : l'assistance sert les mêmes endpoints, donc le même
    # contrat, et lui refuser les sources ferait dépendre un 422 du nom du fil.
    #
    # Il est servi par le **répondeur documenté** depuis #764 : le modèle choisit ses
    # sections sur la carte de `docs/` (#763) puis répond à partir d'elles, en citant
    # ce qu'il a lu. Le déterministe de #123 lui est passé en **repli** plutôt que
    # d'être remplacé — sans fournisseur ni authentification, le canal répond
    # encore, et le dit. Construire ce répondeur ne résout aucun fournisseur
    # et ne lit aucun fichier : les deux sont paresseux, comme partout ailleurs ici.
    assistance = ServiceChat(
        store=chat_store,
        repondeur=(
            assistance_repondeur
            if assistance_repondeur is not None
            else RepondeurAssistanceDocumentee(repli=RepondeurAssistance())
        ),
        mailbox=mailbox,
        bus=bus,
        televersements=televersements,
    )
    ctx.assistance = assistance
    diffusion = Diffusion()
    ctx.diffusion = diffusion
    # Pilotage des exécutions (#185) : lance sur le bus et la projection de
    # cette app — le run est donc suivi par les mêmes rouages que n'importe
    # quelle orchestration observée.
    executions = ServiceExecutions(
        bus,
        state,
        fabrique_moteur=fabrique_moteur,
        televersements=televersements,
        lecteur_sources=lecteur_sources,
        battements=battements,
        hote=hote_run,
        acquis=acquis,
        # Le dépôt des projets de l'app (#1392) : de quoi porter sur sa branche le
        # travail d'une tâche en vol quand son run est soldé de l'extérieur — le
        # même dépôt que celui des écrans, jamais un second qui pourrait diverger.
        projets=projets.store,
    )
    ctx.executions = executions
    # Le régime de brief des runs ouverts depuis le fil — **une** valeur, lue par
    # le lanceur qui l'applique et par le régime qui le dit (#1323) : deux
    # écritures du même choix finiraient par faire annoncer au fil un autre
    # cadrage que celui du run.
    mode_brief_du_fil = MODE_BRIEF_AUTO
    ctx.mode_brief_du_fil = mode_brief_du_fil
    consultations = Consultations(projet=partial(projet_du_fil, ctx), detail=detail_du_run(state))
    ctx.consultations = consultations
    # Ce qui attend quelqu'un — questions d'agent et validations — se règle par un
    # seul service (#1183) : les deux routes de leurs écrans et le fil de
    # l'orchestrateur l'appellent, aucun ne recopie ses règles.
    # Une approbation étendue (#1185) s'écrit dans les permissions de l'agent, au
    # projet de la demande : le service reçoit de quoi trouver ce dépôt, jamais un
    # chemin.
    attentes = ServiceAttentes(
        state,
        bus,
        accords=lambda projet_id: gabarits.permissions.pour_projet(projet_id).accords(),
    )
    ctx.attentes = attentes
    # Le fil global (#268) : mêmes rouages que le chat — persistance, messagerie,
    # bus —, un répondeur qui peut ouvrir un run, et rien de plus côté REST. Il se
    # construit ici, et pas avec les deux autres, parce qu'il tient son lanceur du
    # service d'exécutions ci-dessus.
    orchestration = ServiceChat(
        store=chat_store,
        repondeur=(
            orchestration_repondeur
            if orchestration_repondeur is not None
            else RepondeurOrchestration(
                lanceur=partial(ouvrir_un_run, ctx),
                apercu=apercu_de(state),
                # Ce que les runs ont **fait** (#1157) : la même projection, lue
                # pour raconter au lieu de compter — sans quoi « pourquoi le run
                # a échoué ? » ne peut que renvoyer vers un écran.
                faits=faits_des_runs(state),
                # La même sonde que `GET /api/fournisseurs` (#1173) : quand aucun
                # fournisseur n'est réglé, le fil dit ce que le poste offre.
                sonde=sonde_poste.rapport,
                # Un projet sans équipe se la voit proposer au lieu d'un run
                # voué à l'échec (#1146) — comptée comme le routeur la comptera,
                # créée par la même voie que l'étape d'équipe du parcours.
                equipe=partial(equipe_du_projet, ctx),
                recruteur=partial(creer_equipe_du_projet, ctx),
                # Ce qui fait que l'orchestrateur **sait** (#1223) : les lectures
                # qu'il demande (fichiers du projet, détail complet d'un run),
                # l'équipe réelle et ce qui attend quelqu'un.
                consultation=partial(consulter, ctx),
                roles=partial(roles_du_projet, ctx),
                attentes=attentes_de(state),
                # Ce qu'un run fera (#1323) : la politique réelle de l'équipe, le
                # cadrage du lanceur, la règle des bornes — ce que le fil devinait.
                regime=partial(regime_du_projet, ctx),
                # Le projet de la conversation (#1180) : nommé par sa fiche, avec son
                # outillage — et sans lui, aucun run ne part du fil.
                projet=partial(projet_nomme, ctx),
                outillage=partial(outillage_du_fil, ctx),
                conducteur=conducteur,
                # Un projet naît dans la conversation (#1294) : déclaré par le
                # **même** service que `POST /api/projets`, et un dossier importé
                # lu par la lecture de l'outillage (#1158) — une fois accordé.
                naissance=ServiceNaissance(projets, lecteur=outillage.analyser),
                # Et son outillage s'y construit, pièce par pièce (#1161).
                pieces=pieces,
                # Il agit sur les runs existants — pause, reprise, annulation,
                # relance — par le **même** service que les boutons (#1179).
                pilote=executions,
                # Et il règle ce qui attend quelqu'un — répondre à un agent,
                # trancher une validation — par le service de leurs écrans (#1183).
                reglements=attentes,
            )
        ),
        mailbox=mailbox,
        bus=bus,
    )
    ctx.orchestration = orchestration
    # Le bilan sur pièces d'un run (#1284) : rendu à la fin de **tout** run, qu'un
    # fil l'ait demandé ou non, et publié sur le bus — donc gardé au journal durable.
    bilans = ServiceBilan(
        state=state,
        journal=journal,
        bus=bus,
        agent=AGENT_ORCHESTRATION,
        juge=bilan_juge,
    )
    ctx.bilans = bilans
    # Le récit de fin d'un run (#1224) : il écrit dans le fil ci-dessus, donc il
    # se construit après lui. Rien ne l'abonne au bus — c'est la pompe qui lui
    # passe la main, une fois la projection à jour (voir `_pompe`).
    conteur = ConteurDeFin(
        chat=orchestration,
        state=state,
        agent=AGENT_ORCHESTRATION,
        projet=partial(projet_du_run, ctx),
        redacteur=recit_redacteur,
        # La fin d'un run revoit aussi l'outillage de son projet (#1343) : ce qui ne
        # pouvait pas se jouer sur un dossier vide se joue sur le projet construit.
        outillage=conducteur,
        # Et le récit lit le bilan avant d'écrire (#1284) : celui que la fin a mis
        # en route, jamais un second.
        bilan=bilans,
    )
    ctx.conteur = conteur
    # Les récits en vol, tenus par l'app : `asyncio.create_task` ne garde qu'une
    # référence faible, et une tâche ramassée en cours de route perdrait le
    # message sans rien dire.
    recits: set[asyncio.Future[Any]] = set()
    ctx.recits = recits
    # Le relais du renfort (#1260) : il pose dans le fil ci-dessus la demande
    # qu'un run a publiée sur le bus — quel que soit l'hôte du run, en process
    # ou détaché. Même patron que le récit : rien ne l'abonne, la pompe lui passe
    # la main une fois la projection à jour.
    relais_renfort = RelaisRenfort(bus, orchestration, AGENT_ORCHESTRATION)
    ctx.relais_renfort = relais_renfort
    app = FastAPI(
        title="Maestro — Control Tower",
        description="État de l'orchestration (REST) et flux d'événements (WebSocket).",
        lifespan=partial(lifespan, ctx),
    )
    app.state.contexte = ctx
    # Ce que l'API accepte (#638), en deux couches montées dans cet ordre —
    # `add_middleware` empile par le haut, donc CORS, ajouté en second, est
    # **au-dessus** du portier. C'est ce qu'on veut : un préflight `OPTIONS` est
    # tranché par CORS sans jamais arriver au portier (un navigateur ne porte
    # pas d'`Authorization` sur un préflight), et un 401 du portier ressort avec
    # ses en-têtes CORS, donc la page peut en lire le motif.
    # La garde du magasin (#1206) est montée **sous** le portier : une requête
    # sans jeton est refusée pour ce motif-là d'abord, et un 503 ressort lui
    # aussi avec ses en-têtes CORS, donc lisible par la page.
    app.add_middleware(GardeMagasin, magasin=magasin)
    app.add_middleware(GardeAcces, politique=acces)
    # L'UI (apps/web, ticket #47) est servie sur une autre origine que l'API
    # (Next.js sur :3000, API sur :8000) : sans CORS le navigateur bloque les
    # appels REST. La liste n'est plus `["*"]` depuis #638 — c'est un réglage
    # (`MAESTRO_API_ORIGINES`), dont le défaut local est l'origine du front, et
    # le mode serveur se règle par la même variable (ENF-12).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(acces.origines),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
    #: Un service de chat par projet (#1175), construit au premier fil ouvert.
    chats_de_projet: dict[str, ServiceChat] = {}
    ctx.chats_de_projet = chats_de_projet
    for routeur in ROUTEURS:
        app.include_router(routeur)
    return app


async def ouvrir_un_run(
    ctx: Contexte,
    objectif: str,
    projet_id: str | None = None,
    bornes: BornesRun = AUCUNE_BORNE,
    contexte_sources: str = "",
) -> dict[str, Any]:
    """Le lanceur du fil global (#268) — un run sur l'objectif dicté au chat.

    `MODE_BRIEF_AUTO` et non le `humain` des lancements par l'écran : le
    cadrage d'une demande **est** la conversation qu'on est en train d'avoir,
    et renvoyer vers un écran de validation de brief pour la poursuivre
    couperait le fil en deux. Le run part donc, brief rédigé sans attendre ;
    l'écran des exécutions reste la voie de celui qui veut le valider avant.

    `projet_id` (#683) est le projet du run — celui de la proposition
    approuvée (#1180), à défaut celui de la fenêtre, **normalisé à la
    frontière** (`envoyer_chat`). Il est passé à `lancer` exactement comme le
    fait `POST /api/executions` — même paramètre, même validation de forme
    (`projet_id_valide`), donc une seule règle pour les deux portes d'entrée.
    Le fil ne l'appelle jamais sans projet (#1180, `_ouvrir_un_run`) ; l'écran
    des exécutions, lui, garde le rattachement pour une donnée (#222).

    `bornes` (#990) achève le rapprochement des deux portes : les quatre
    garde-fous que `POST /api/executions` transmettait depuis #185 passent
    désormais aussi par celle-ci, qui est **la seule qu'un écran offre**
    depuis #666. Elles arrivent du geste de cadrage, où l'écran les a
    posées ; `AUCUNE_BORNE` — le défaut — rend exactement l'appel d'avant ce
    ticket, et c'est `lancer` qui refuse une valeur hors bornes.

    `contexte_sources` (#1172) : les pièces jointes de la conversation, déjà
    lues par le fil. Elles passent en contexte et non en `sources` à relire
    (cf. `orchestration.contexte_du_fil`).
    """
    executions = ctx.executions
    mode_brief_du_fil = ctx.mode_brief_du_fil
    return await executions.lancer(
        objectif,
        plafond_cout_usd=bornes.plafond_cout_usd,
        plafond_tokens=bornes.plafond_tokens,
        timeout_tache_s=bornes.timeout_tache_s,
        parallelisme=bornes.parallelisme,
        projet_id=projet_id,
        mode_brief=mode_brief_du_fil,
        contexte_sources=contexte_sources,
    )


def equipe_du_projet(ctx: Contexte, projet_id: str) -> int | None:
    """Combien d'agents le routeur trouvera pour ce projet — `None` : je ne sais pas (#1146).

    La **règle unique** de l'exécuteur et de la boucle (`catalogue_du_projet`),
    sur le même dépôt : le fil compte exactement les agents vers lesquels les
    tâches seront routées. Un projet non déclaré n'est pas « sans équipe »,
    il est inconnu — le fil n'en tire rien, et c'est la portée qui le dira.
    """
    agents_store = ctx.agents_store
    projets = ctx.projets
    if not projets.existe(projet_id):
        return None
    agents = catalogue_du_projet(agents_store, projet_id)
    return None if agents is None else len(agents)


def roles_du_projet(ctx: Contexte, projet_id: str | None) -> str:
    """L'équipe du projet **en clair** pour le fil — rôles, agents, instances (#1223).

    La même règle que la sonde du recrutement (`catalogue_du_projet`), lue
    pour **dire** au lieu de compter : « qui travaille sur ce projet ? » ne
    trouvait aucune réponse dans le prompt, où l'équipe n'existait que par un
    `_sans_equipe` booléen. Rend `""` quand il n'y a rien à dire — aucun
    projet, projet inconnu, dépôt illisible —, et la phrase qui dit qu'il n'y
    a **personne** quand le catalogue est vide : c'est un fait, pas un vide.
    """
    agents_store = ctx.agents_store
    projets = ctx.projets
    if not projet_id or not projets.existe(projet_id):
        return ""
    agents = catalogue_du_projet(agents_store, projet_id)
    if agents is None:
        return ""
    if not agents:
        return (
            "Équipe du projet : aucun agent recruté — personne ne peut prendre "
            "une tâche de run aujourd'hui."
        )
    lignes = ["Équipe du projet (les agents vers qui les tâches sont routées) :"]
    for agent in agents:
        competences = ", ".join(sorted(agent.competences))
        ligne = f"- {agent.role} « {agent.nom} » — modèle {agent.modele}"
        if competences:
            ligne += f" — compétences : {competences}"
        lignes.append(ligne)
    return "\n".join(lignes)


def regime_du_projet(ctx: Contexte, projet_id: str | None) -> str:
    """Ce qu'un run de ce projet fera, pour le fil — lu, jamais deviné (#1323).

    L'équipe par la règle unique du routeur (`catalogue_du_projet`), comme
    `roles_du_projet`, et la politique de chaque agent par l'appel exact de
    l'exécution (`permissions.pour_projet(…).lire`, relu à chaque message
    comme l'exécuteur le relit à chaque tâche) : le fil dit ce que la tâche
    appliquera. Une politique que la lecture refuse n'est pas tue —
    l'exécution en ferait un échec de tâche, et c'est ce que le fil en dira.
    Sans projet, ou sur un projet inconnu, le bloc ne parle que de ce qui ne
    dépend d'aucune équipe : le cadrage et les bornes.

    Le **versionnement** du projet est relu ici aussi (#1299), par le seul
    lecteur de projets : c'est lui qui décide si les tâches indépendantes
    partiront de front, et un projet versionné depuis le dernier message doit
    se dire tel. Illisible, il se tait plutôt que deviner.
    """
    agents_store = ctx.agents_store
    mode_brief_du_fil = ctx.mode_brief_du_fil
    permissions = ctx.permissions
    projets = ctx.projets
    if not projet_id or not projets.existe(projet_id):
        return regime_d_un_run(None, mode_brief=mode_brief_du_fil)
    agents = catalogue_du_projet(agents_store, projet_id)
    if agents is None:
        return regime_d_un_run(None, mode_brief=mode_brief_du_fil)
    politiques = permissions.pour_projet(projet_id)
    membres: list[MembreDeLEquipe] = []
    for agent in agents:
        try:
            politique = politiques.lire(agent.nom)
        except ValueError as refus:
            membres.append(MembreDeLEquipe(agent.role, agent.nom, illisible=str(refus)))
        else:
            membres.append(MembreDeLEquipe(agent.role, agent.nom, politique))
    try:
        versionne: bool | None = projets.entite(projet_id).versionne
    except (ProjetInconnu, ProjetIllisible):
        versionne = None
    return regime_d_un_run(membres, mode_brief=mode_brief_du_fil, versionne=versionne)


def projet_du_fil(ctx: Contexte, projet_id: str) -> Projet | None:
    """Le projet de la fenêtre en **entité**, ou `None` — le seul lecteur de projets.

    `ServiceProjets.entite` et lui seul (#1223) : les lectures de
    l'orchestrateur sont bornées à la racine que ce service déclare, et son
    **périmètre** est ce qui ferme `.env` et `secrets/`. Relire la fiche à
    côté ouvrirait une seconde définition de « où a-t-on le droit de lire ».
    """
    projets = ctx.projets
    try:
        return projets.entite(projet_id)
    except (ValueError, ProjetInconnu, ProjetIllisible):
        return None


def projet_nomme(ctx: Contexte, projet_id: str) -> ProjetVise | None:
    """Le projet de la fenêtre tel que le fil le nomme — `None` s'il n'est pas déclaré (#1180).

    Le même lecteur que les lectures du fil (`projet_du_fil`, `ServiceProjets`) :
    un projet que l'API ne déclare pas n'est pas un projet où un run peut partir,
    et c'est la seule réponse qui ferme la porte. Ce qu'il rend est ce qu'une
    proposition écrit sur elle (`ProjetVise`) : l'identifiant que l'accord suit,
    et le nom et le dossier que la carte affiche.
    """
    projet = projet_du_fil(ctx, projet_id)
    if projet is None:
        return None
    return ProjetVise(id=projet.id, nom=projet.nom, racine=projet.racine)


def outillage_du_fil(ctx: Contexte, projet_id: str) -> str:
    """L'outillage du projet pour le fil, lu **comme un agent le reçoit** (#1180).

    `outillage_du_projet` est la lecture qui part dans le message de chaque
    tâche : le fil dit donc de l'outillage exactement ce que l'équipe en
    recevra, et rien qu'une seconde analyse du disque pourrait inventer.
    """
    projet = projet_du_fil(ctx, projet_id)
    return outillage_en_clair(outillage_du_projet(projet)) if projet is not None else ""


async def consulter(ctx: Contexte, demande: Demande, projet_id: str | None) -> Lecture:
    """Exécute une lecture du fil, **hors boucle** — elle touche le disque (#1223).

    `asyncio.to_thread` pour la même raison que la création d'équipe juste
    au-dessus : lire un fichier ou balayer un dossier est bloquant, et
    l'API sert d'autres requêtes pendant qu'un fil réfléchit.
    """
    consultations = ctx.consultations
    return await asyncio.to_thread(consultations.executer, demande, projet_id)


def projet_du_run(ctx: Contexte, projet_id: str | None) -> Projet | None:
    """Le projet d'un run — `None` s'il n'en relève d'aucun, ou s'il a disparu.

    Un `None` et non une exception : un run hors projet est un cas normal
    (#222), et un projet retiré après coup ne doit pas empêcher de raconter
    ce que le run a fait. C'est la même asymétrie que l'annonce de #928, qui
    écrit sa raison à la place du chemin plutôt que de se taire.
    """
    projets = ctx.projets
    if not projet_id:
        return None
    try:
        return projets.entite(projet_id)
    except Exception:  # noqa: BLE001 — projet retiré, illisible, ou jamais déclaré
        return None


async def _raconter_au_compte_du_fil(ctx: Contexte, run_id: str) -> None:
    """Le récit de fin d'un run, sa dépense au grand livre du fil (#1534).

    Le récit et la revue d'outillage qui le suit sont écrits **dans le fil**,
    pour le projet du run, et non par le run : ils n'entrent pas à son grand
    livre, ils entrent à celui du fil, comme tout ce que Maestro dit en
    conversation. Le bilan, qu'il attend, a son propre collecteur et sa ligne
    sur le run (#1284) : il n'est pas compté ici.
    """
    bus = ctx.bus
    conteur = ctx.conteur
    state = ctx.state
    execution = state.execution(run_id)
    projet_id = execution.projet_id if execution is not None else None
    async with au_compte_du_fil(
        bus, titre="récit de fin de run", agent=ACTEUR_RUN, projet=lambda: projet_id
    ):
        await conteur.raconter(run_id)


def raconter_la_fin(ctx: Contexte, run_id: str) -> None:
    """Lance le bilan et le récit de `run_id` — sans faire attendre la pompe (#1224, #1284).

    La rédaction est un appel modèle et la lecture du livrable touche le
    disque : les faire dans la pompe figerait le flux temps réel de tous les
    écrans pendant plusieurs secondes.

    Le bilan part **à côté** du récit, et non dedans : tout run en a un, y
    compris celui qu'aucun fil n'a demandé et qui n'aura donc pas de récit. Le
    récit, lui, attend le même bilan (`ServiceBilan.rendre` partage l'appel en
    vol) : il n'y a qu'un appel au modèle pour les deux.
    """
    bilans = ctx.bilans
    recits = ctx.recits
    # Le bilan est **lancé** ici, sans attendre (#1285) : l'appel est en vol dès
    # cette ligne, donc la vue du run qui relit juste après lit « en
    # rédaction », jamais « absent ».
    en_vol = bilans.lancer(run_id)
    if isinstance(en_vol, asyncio.Future):
        recits.add(en_vol)
        en_vol.add_done_callback(recits.discard)
    tache = asyncio.create_task(_raconter_au_compte_du_fil(ctx, run_id))
    recits.add(tache)
    tache.add_done_callback(recits.discard)


async def rejouer(ctx: Contexte) -> None:
    """Relit le journal durable dans la projection — une seule fois par process."""
    event_log = ctx.event_log
    journal = ctx.journal
    magasin = ctx.magasin
    state = ctx.state
    try:
        evenements = await event_log.relire()
    except Exception as erreur:
        magasin.rejeu_echoue(erreur)
        raise
    for event in evenements:
        state.appliquer(event)
        # Le même rattachement que la pompe (#1290) : le journal durable
        # garde l'issue d'un run telle qu'elle a été publiée, sans projet.
        journal.consigner(state.au_projet_de_son_run(event))
    magasin.rejeu_abouti()


@asynccontextmanager
async def lifespan(ctx: Contexte, app: FastAPI) -> AsyncIterator[None]:
    # Rejeu du journal durable (#97) **avant** d'ouvrir la pompe : la
    # projection retrouve l'historique (exécutions, grands livres, analytics)
    # d'avant le redémarrage, puis la pompe prend le relais du flux à venir.
    # Le journal requêtable (#478) se remplit du **même** parcours : c'est ce
    # qui fait de lui une vue de l'historique durable et non un second
    # stockage — et ce qui lui donne des rangs, donc des identifiants
    # d'entrée, stables d'un redémarrage à l'autre.
    # Un journal illisible (Redis absent au démarrage…) ne bloque pas l'API,
    # mais il ne se tait plus (#1206) : tant que l'historique n'est pas relu,
    # le magasin se dit indisponible, et la pompe retente le rejeu dès que
    # le magasin revient.
    # Un journal qui refuse son fichier (schéma plus récent que le code,
    # migration en échec, #1621) se nomme : l'API sert sa panne et son geste.
    battements = ctx.battements
    bus = ctx.bus
    diffusion = ctx.diffusion
    event_log = ctx.event_log
    executions = ctx.executions
    gabarits = ctx.gabarits
    journal = ctx.journal
    magasin = ctx.magasin
    mailbox = ctx.mailbox
    projets = ctx.projets
    recits = ctx.recits
    relais_renfort = ctx.relais_renfort
    state = ctx.state
    try:
        await rejouer(ctx)
    except MagasinRefuse as refus:
        _LOGGER.error("Journal des événements refusé au démarrage : %s", refus, exc_info=True)
    except Exception:
        _LOGGER.exception(
            "Rejeu du journal des événements impossible : l'API sert la panne "
            "du magasin et retentera dès qu'il répondra."
        )
    # Reprise des agents et réglages globaux dans le projet qui les utilise
    # (#1038, critère 2). Ici parce que c'est le seul moment où une
    # installation d'avant ce lot croise le nouveau code sans que personne ait
    # rien à taper — et **best-effort** : elle n'ajoute jamais, ne supprime
    # jamais, et une reprise impossible ne doit pas empêcher l'API de
    # démarrer. Idempotente, donc rejouée à chaque démarrage sans effet.
    # `MAESTRO_REPRISE_AGENTS=0` s'en passe ; la CLI
    # `python -m maestro.agents.reprise [--check]` la rejoue et la détaille.
    if _reprise_agents_activee():
        try:
            rapport = reprendre(projets=projets.store, configuration=gabarits)
            if rapport.nb_reprises:
                _LOGGER.info("%s", rapport)
        except Exception:
            _LOGGER.exception(
                "Reprise des agents dans leur projet impossible : les réglages "
                "globaux restent lisibles comme gabarits (rien n'est perdu). "
                "La rejouer : python -m maestro.agents.reprise --check"
            )
    pompe = asyncio.create_task(
        _pompe(
            bus,
            state,
            diffusion,
            journal,
            magasin=magasin,
            rejouer=partial(rejouer, ctx),
            sur_fin_de_run=partial(raconter_la_fin, ctx),
            sur_demande_de_renfort=relais_renfort,
        )
    )
    try:
        yield
    finally:
        # Les runs en vol s'arrêtent **avant** la pompe et le bus : leur
        # issue (annulation) a encore un canal pour être consignée.
        await executions.fermer()
        # Les récits encore en vol s'arrêtent avec le reste : un appel modèle
        # qui survivrait à l'arrêt écrirait dans un fil dont plus personne ne
        # pompe les événements.
        for recit in list(recits):
            recit.cancel()
        # Les relais de renfort aussi : un run détaché qui attend encore tient
        # sa propre borne, et repart avec l'équipe actuelle à l'échéance.
        relais_renfort.fermer()
        pompe.cancel()
        with suppress(asyncio.CancelledError):
            await pompe
        await mailbox.close()
        await bus.close()
        await event_log.close()
        await battements.close()


@dataclass(frozen=True)
class _SupportsDeploiement:
    """Les objets d'infrastructure que le **mode de distribution** choisit.

    Ils vont ensemble et c'est tout le sujet : un journal local derrière un bus
    Redis exigerait encore un service, et un bus mémoire devant un journal Redis
    ferait consigner dans une instance qu'aucun autre process ne lit. Les
    résoudre d'un seul geste est ce qui rend le mode **nommable** — « serveur »
    ou « local » — plutôt qu'assemblable de travers. Le magasin d'état acquis
    (#1391) en est, et pour la même raison : il suit le journal durable, dont il
    garde ce que le journal ne porte pas (`magasin_acquis_configure`).
    """

    bus: EventBus
    mailbox: Mailbox
    journal: EventLog
    battements: RegistreBattements
    acquis: MagasinAcquis


def _supports_configures(settings: Settings) -> _SupportsDeploiement:
    """Résout `MAESTRO_PERSISTANCE` en infrastructure — **Redis** par défaut (#639).

    `redis` (le défaut, et le silence) rend exactement les quatre objets d'avant
    ce lot : rien de ce qui marche aujourd'hui ne change, ni l'URL, ni les clés,
    ni le canal.

    `sqlite` est le **mode local** : le journal durable vit dans un fichier
    (`SqliteEventLog`), et le reste passe aux implémentations mono-process. Ce
    n'est pas un raccourci, c'est ce que chacune de ces classes documente
    d'elle-même — `InMemoryEventBus`, `InMemoryMailbox` et
    `RegistreBattementsMemoire` sont « le transport des tests **et d'un
    déploiement mono-process** », et c'est exactement ce qu'est un produit posé
    sur un poste : une API, et rien d'autre à installer.

    Le bus et le journal restent **deux questions** (cf. l'en-tête de
    `persistence`) : on ne fabrique pas un pub/sub avec une table qu'on
    interrogerait en boucle. Ce que le mode local perd est la diffusion
    **inter-process** — un producteur hors de l'API —, et c'est précisément ce
    que `_hote_configure` refuse d'y câbler plutôt que de le laisser publier dans
    le vide. Ce qu'il garde est l'essentiel : ce qui est publié est consigné
    (`BusDurable`, #699), donc rejoué au démarrage suivant.
    """
    if support_persistance(settings) == SUPPORT_SQLITE:
        return _SupportsDeploiement(
            bus=InMemoryEventBus(),
            mailbox=InMemoryMailbox(),
            journal=journal_configure(settings),
            battements=RegistreBattementsMemoire(),
            acquis=magasin_acquis_configure(settings),
        )
    return _SupportsDeploiement(
        bus=RedisEventBus(settings.redis_url),
        mailbox=RedisMailbox(settings.redis_url),
        journal=journal_configure(settings),
        battements=RegistreBattementsRedis(settings.redis_url),
        acquis=magasin_acquis_configure(settings),
    )


def create_default_app() -> FastAPI:
    """L'app de production : ses supports résolus depuis l'environnement.

    En **serveur** (le défaut) : consomme le canal `maestro.evenements` de
    l'instance `REDIS_URL` (celle du docker-compose par défaut — la même que la
    file de tâches #41), alimenté côté moteur par `maestro.controltower.bridge` ;
    le chat (#84) transite par la messagerie Redis de la même instance (boîtes
    `maestro.boite.<agent>`). Les événements y sont aussi **persistés** (#97) sur
    la liste Redis `maestro.evenements:journal` et rejoués au démarrage : l'état
    de la Control Tower survit au redémarrage de l'API.

    Les **battements** des runs (#348) vivent sur la même instance, dans le hash
    `maestro.runs:battements` — hors du process, seule façon de voir battre un run
    lancé par `maestro-run --publier` et de le retrouver vivant après un
    redémarrage de l'API.

    En **local** (`MAESTRO_PERSISTANCE=sqlite`, #639) : le journal durable est un
    fichier SQLite hors du dépôt, le reste vit dans le process, et **aucun
    service n'est à installer** — la promesse d'un produit qu'on pose sur un
    poste (docs/24 §4.7). La projection se reconstruit au démarrage du même
    rejeu, par le même code : le support est un **réglage**, jamais un
    embranchement (ENF-12), et `_supports_configures` est le seul endroit qui le
    lit.

    L'**hôte des runs** (#443) se choisit ici, et nulle part ailleurs. Depuis #446
    le défaut est l'hôte **détaché** : chaque run vit dans un process indépendant,
    qui survit à l'arrêt **subi** de `maestro-api` — relancer après une
    modification, planter, recevoir un `SIGTERM` n'emportent plus le run. Il ne
    survit pas à l'arrêt **volontaire** (#486, `POST /api/extinction`), qui est une
    décision et non un accident : `start.sh --stop`, et depuis #700 la fermeture de
    la fenêtre du navigateur.
    `MAESTRO_HOTE_RUN=process` ramène la tâche de fond de l'API, dont les runs
    meurent avec elle. C'est le seul endroit du dépôt qui *nomme* un hôte : le
    service ne connaît que le contrat, et le résoudre là où sont déjà résolus le
    bus, le journal et le registre met la frontière d'exécution parmi les autres
    choix de déploiement, ce qu'elle est.

    La **politique d'accès** (#638) se résout ici aussi, et pour la même raison :
    c'est un choix de déploiement. Le défaut est le mode **durci** — jeton du
    poste exigé sur chaque requête, origines limitées à celle du front —, et le
    régime `ouvert` se nomme (`MAESTRO_API_AUTH`, voir
    `maestro.controltower.acces`). L'app des tests, elle, passe par `create_app`
    sans politique : ouverte, explicitement.

    C'est la cible *factory* d'uvicorn :
    `uvicorn --factory maestro.controltower.app:create_default_app`
    (ou le script `maestro-api`).
    """
    settings = load_settings()
    supports = _supports_configures(settings)
    return create_app(
        bus=supports.bus,
        mailbox=supports.mailbox,
        event_log=supports.journal,
        battements=supports.battements,
        hote_run=_hote_configure(settings),
        acces=politique_depuis(settings),
        acquis=supports.acquis,
    )


def _hote_configure(settings: Settings) -> HoteRun | None:
    """Résout `MAESTRO_HOTE_RUN` en hôte — **détaché** par défaut depuis #446.

    La bascule du chantier #441 tient dans la valeur de repli de la première
    ligne, et c'est voulu : les quatre lots précédents ont livré du code inerte
    pour que celui-ci n'ait rien d'autre à changer. Ce que ce défaut promet est
    écrit dans `hote_detache` — un run survit à l'accident, pas à l'extinction ni
    à sa machine (#486) ; ce
    qu'il exige est un Redis joignable, sur lequel le process publie ses étapes,
    bat son cœur, reçoit les décisions humaines et consigne son issue.

    `process` reste disponible et **doit se nommer** : le silence ne le désigne
    plus. C'est le sens de la bascule — un déploiement qui veut des runs mourant
    avec l'API le dit, là où c'était jusqu'ici ce qu'on obtenait sans rien dire.
    `None` est rendu pour lui, et non `HoteRunEnProcess()` : le service en fabrique
    un autour de son propre dérouleur, qu'on n'a pas ici.

    Une valeur inconnue est une **erreur franche** et non un repli silencieux :
    `MAESTRO_HOTE_RUN=procesus` laisserait sinon croire que les runs meurent avec
    l'API alors qu'ils lui survivent — et depuis la bascule, l'erreur se lit dans
    l'autre sens qu'avant, ce qui ne change rien à sa nature : une frontière
    d'exécution mal orthographiée ne doit jamais ressembler à un choix. Même parti
    pris que `MAESTRO_ISOLATION` (#108).

    L'import de l'hôte détaché est **local** à cette branche : il tire
    `subprocess` et, par la ligne de commande du fils, tout le moteur — une app
    qui ne le demande pas n'a pas à le charger, exactement comme `moteur_par_defaut`
    ne résout aucun fournisseur tant qu'aucun run ne part.

    ⚠ **En mode local (`MAESTRO_PERSISTANCE=sqlite`, #639), le défaut est
    `process`** — et c'est la même règle vue de l'autre côté : le mode de
    distribution change les défauts, pas les fonctions (docs/24 §4.7). L'hôte
    détaché *exige* un Redis joignable, sur lequel il publie, bat et consigne ;
    un poste qui n'en a pas le verrait partir et ne recevrait jamais une ligne de
    son run. Demandé **explicitement** dans ce mode, il est donc une **erreur
    franche** plutôt qu'un run muet : le silence choisit l'hôte qui marche, la
    demande impossible se dit.
    """
    local = support_persistance(settings) == SUPPORT_SQLITE
    defaut = HOTE_RUN_EN_PROCESS if local else HOTE_RUN_DETACHE
    nom = (settings.hote_run or defaut).strip().lower()
    if nom == HOTE_RUN_EN_PROCESS:
        return None
    if nom == HOTE_RUN_DETACHE:
        if local:
            raise ConfigError(
                f"MAESTRO_HOTE_RUN={HOTE_RUN_DETACHE} exige un Redis joignable "
                f"(le run y publie, y bat et y consigne son issue), or "
                f"MAESTRO_PERSISTANCE={SUPPORT_SQLITE} est le mode local, sans service. "
                f"Laisser MAESTRO_HOTE_RUN vide y désigne « {HOTE_RUN_EN_PROCESS} »."
            )
        from maestro.controltower.hote_detache import HoteRunDetache

        return HoteRunDetache()
    raise ConfigError(
        f"MAESTRO_HOTE_RUN : hôte inconnu {nom!r} "
        f"(attendu : {' | '.join(HOTES_RUN)}, ou vide pour « {defaut} »)."
    )
