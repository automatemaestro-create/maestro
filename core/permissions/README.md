# Politiques de permissions par agent (ticket #110)

Un fichier par agent — `<agent>.json` — déclare sa politique **allow/ask/deny
par outil**, appliquée à l'exécution par le moteur et les workers (relue **à
chaud** à chaque tâche, comme les playbooks) :

```json
{
  "allow": [],
  "ask": {
    "mcp__slack__send_message": "humain",
    "Grep": "auto"
  },
  "deny": ["mcp__slack__chat_delete"]
}
```

- Les entrées des trois listes ont la même forme : un **outil intégré**
  (`Read`, `Write`, `Edit`, `Glob`, `Grep`, `Bash`), un **serveur MCP entier**
  (`mcp__<serveur>` — refusé en entier, il n'est alors jamais monté et ses
  secrets jamais résolus) ou un **outil MCP précis** (`mcp__<serveur>__<outil>`).
- **`deny` l'emporte sur `ask`, qui l'emporte sur `allow`** (#580) ; `allow`
  vide = tout ce que le profil du rôle expose est permis ; `allow` non vide =
  liste fermée, tout le reste est refusé — sauf ce que `ask` cite, qui est
  **arbitré et non refusé**, sans quoi fermer sa liste `allow` suffirait à
  rendre le cran du milieu lettre morte.
- Un outil cité en **`ask`** n'est pas interdit : il reste **monté** sur la
  session (un outil retiré avant l'ouverture n'atteindrait jamais le point de
  contrôle censé le suspendre), et son appel est soumis à un arbitrage.
- **Qui arbitre est dans la politique** (#586) : `ask` s'écrit alors en objet
  `{"<outil>": "<décideur>"}`, avec deux crans et un seul défaut —
  - `auto` : personne n'est sollicité, l'appel passe. Il est **tracé** quand
    même (journal + fil temps réel), et c'est toute la différence avec un
    `allow`, qui passe en silence : le cran de ce qu'on veut voir sans vouloir
    l'arrêter. N'ayant besoin d'aucun décideur, il ne dépend d'aucun canal.
    ⚠ Ce n'est pas « la machine approuve » : c'est une décision **humaine
    différée**, prise à froid et versionnée avec le dépôt ;
  - `humain` : une personne, et personne d'autre. C'est le **défaut** — un cran
    non précisé escalade, il ne s'auto-approuve pas (EF-08/ENF-04 : refuser est
    le défaut sûr, approuver ne l'est jamais).

  ⚠ **Un troisième cran, `orchestrateur`, a été retiré** ([docs/31](../../docs/31-decision-cran-orchestrateur.md),
  décision #647, retrait #715). Il n'a **jamais eu de canal** en production, si
  bien qu'une entrée qui le posait rendait « aucun orchestrateur configuré — refus
  par défaut » : la politique promettait une décision et rendait un refus, sans
  que rien n'avertisse au chargement. Depuis le retrait, une politique qui l'écrit
  **échoue franchement au chargement**, avec la liste des crans admis — le
  fichier qu'on charge peut encore être corrigé. ⚠ Les actes qui lui revenaient
  remontent au **défaut**, `humain` : `auto` n'hérite de rien.

  Fail-safe : pas de canal humain, ou canal en panne ⇒ **refus**. Un décideur
  inconnu dans le fichier est refusé avec sa cause — un garde-fou ne s'applique
  jamais à moitié.
- Liste absente = liste vide, `ask` comprise : un fichier écrit avant #580 se
  relit sous le régime d'hier. `ask` écrite en **liste** (`["Bash"]`, la forme
  d'avant #586) reste admise et vaut `humain` partout ; la relecture accepte les
  deux formes, l'écriture n'en produit qu'une — l'objet.
- Pas de fichier = pas de politique = comportement historique (les outils du
  profil, tous les serveurs MCP déclarés).

À l'exécution, un appel refusé produit un **refus propre** : l'agent reçoit le
motif et poursuit sa tâche (le run n'est jamais condamné), la violation est
tracée au journal (étape `<tâche>:refus-outil`) et au fil temps réel de la
Control Tower. La fiche agent du catalogue (UI) affiche la politique
effective, en lecture seule.

Un appel **arbitré** laisse la même trace, sous un statut à lui
(`arbitrage_outil`) : approuvé, refusé ou encore en attente, ce n'est pas un
refus. L'étape en nomme le **décideur** — « Outil arbitré (humain) » —, et le
tient de cette politique-ci et non du texte du motif : qui a tranché se lit, il
ne se déduit pas. ⚠ Le champ reste porté même si `humain` est désormais la seule
valeur qu'une **attente** puisse porter (un `auto` n'atteint jamais la file, le
hook le court-circuite) : le journal est **rejoué**, et des étapes déjà écrites
nomment le cran retiré. Ce qui a été supprimé est le routage, jamais la mémoire
de ce qui a été décidé.

## Ce que le dépôt classe, et pourquoi (#716)

Jusqu'à #716 ce dossier ne portait **aucune** entrée `ask` — pas une. La chaîne
livrée par #573 (politique #580, hook #583, demande avec l'acte #581, crédit de
délai #584, cran #586) était complète, testée, documentée et **dormante** : rien
ne disait quels actes méritaient d'être vus, donc aucun outil n'était suspendu,
aucune demande n'était composée et la file `/api/validations` ne recevait jamais
d'acte. Ce qui suit est le fichier qui manquait, et la règle qui l'a écrit.

### La règle : le cran suit ce que le NOM de l'outil permet de dire

Le hook juge un appel **avant** de le laisser partir. À cet instant, ce dont on
dispose est le **nom de l'outil** : les arguments sont du texte que l'agent vient
de composer, et une politique ne s'écrit pas sur du texte d'agent — c'est le
régime par mots-clés que #585 a désarmé. Trois questions, dans cet ordre :

1. Le nom suffit-il à dire que l'acte **sort du système et ne se défait pas** ?
   → `ask` / **`humain`**. Le prix est une attente (`BornesArbitrage.attente_effective`,
   `min(240, 300 − 5)` = **240 s** au défaut) et une carte devant quelqu'un ; on
   le paie parce que l'acte, lui, ne se reprend pas.
2. Le nom **ne dit-il rien** de ce que l'acte fera ?
   → `ask` / **`auto`**. On ne peut pas arrêter ce qu'on ne sait pas qualifier
   sans arrêter tout le reste avec : l'arrêter ferait de chaque tâche une file
   d'attente. On le **voit** au lieu de l'arrêter, et c'est toute la différence
   d'avec `allow`.
3. Sinon → **`allow`** (silence). Lire, chercher, écrire dans son espace de
   travail est le métier de l'agent, pas un acte à arbitrer.

**Granularité** : on classe au niveau le plus fin dont les noms d'outils sont
**vérifiables dans le dépôt**, à défaut au périmètre du **secret** qui arme le
serveur — un jeton, un rayon d'action. D'où `figma-officiel` classé outil par
outil (docs/20 §6 en a relevé le catalogue en direct) quand `slack` et `forge` le
sont en entier : le dépôt ne nomme aucun de leurs outils, et une entrée qui
nommerait un outil inexistant serait morte sans que rien ne le dise.

**`deny` reste vide, et ce n'est pas un oubli.** Un `deny` retire l'outil de la
session — l'agent ne le voit jamais. Aucun outil monté ici n'est « jamais, sous
aucune condition », et ce qui protège du pire n'est pas une liste noire mais le
fail-safe : sans personne pour trancher, un acte classé `humain` est **refusé**
(EF-08/ENF-04).

⚠ **`mcp__maestro__demander_arbitrage` n'est classé nulle part**, et c'est
délibéré : en `ask` on arbitrerait la demande d'arbitrage (circulaire), en `deny`
on couperait le canal par lequel un agent lève la main (#582). Il reste sous
`allow`, en silence.

### `developpeur`

| Acte | Cran | Pourquoi `ask` plutôt qu'`allow` ou `deny` — et pourquoi **ce** cran |
| --- | --- | --- |
| `Bash` | `auto` | Un shell ne dit pas ce qu'il fera : `Read` et `Grep` s'annoncent par leur nom, `Bash` non. `allow` le laisserait passer en silence ; `humain` mettrait `pytest -q` en file au même prix qu'un `rm -rf`, la politique ne classant que des noms d'outils. |

### `bdd`

| Acte | Cran | Pourquoi `ask` plutôt qu'`allow` ou `deny` — et pourquoi **ce** cran |
| --- | --- | --- |
| `Bash` | `auto` | Même raison, et elle pèse plus ici : la migration destructrice de cet agent part par un shell, indiscernable d'un `psql -c '\dt'` tant qu'on ne lit que le nom de l'outil. `auto` la donne à voir au journal ; `humain` arbitrerait aussi le `\dt`. |

### `devops`

| Acte | Cran | Pourquoi `ask` plutôt qu'`allow` ou `deny` — et pourquoi **ce** cran |
| --- | --- | --- |
| `mcp__slack` | `humain` | Le seul canal du dépôt qui sort du système et **atteint des personnes** : le jeton porte `chat:write` (registre MCP) et un message posté ne se retire pas. `allow` laisserait l'agent parler à l'équipe en silence, `deny` le couperait alors que publier est sa mission (#105). Serveur entier faute d'un nom d'outil vérifiable ici : une lecture de canal paie donc l'attente, prix assumé de n'avoir aucun trou. |
| `Bash` | `auto` | Même raison que chez le développeur, sur l'agent qui déploie : le cran ne peut pas départager `kubectl apply` d'un `docker ps`, il ne voit que « Bash ». On trace au lieu d'arrêter. |

### `qa`

| Acte | Cran | Pourquoi `ask` plutôt qu'`allow` ou `deny` — et pourquoi **ce** cran |
| --- | --- | --- |
| `mcp__forge` | `humain` | Le jeton `GITHUB_TOKEN` écrit dans le dépôt de l'équipe — ouvrir, commenter, fermer, merger : durable et visible de tous. `allow` laisserait l'agent de vérification agir sur la forge sans que personne le voie ; `deny` lui retirerait la lecture des tickets, qui est sa matière. Serveur entier, même raison que `slack`, et sans coût tant que le serveur reste `optionnel` et non monté. |
| `Bash` | `auto` | Un shell reste un shell même chez l'agent qui ne fait que vérifier : `auto` garde la trace des commandes de test sans mettre chaque `pytest` en file. |

### `designer`

| Acte | Cran | Pourquoi `ask` plutôt qu'`allow` ou `deny` — et pourquoi **ce** cran |
| --- | --- | --- |
| `mcp__figma-officiel__use_figma` | `humain` | L'édition **insécable** du canvas d'équipe — création *et* modification par un seul outil (docs/20 §6) : rien ne se dé-dessine dans un fichier partagé. C'est la troisième voie que docs/20 §6 laissait ouverte faute de cran du milieu : ni laisser passer (garde-fou réduit au prompt du rôle), ni barrer (serveur en lecture seule). |
| `mcp__figma-officiel__generate_figma_design` | `humain` | Importe une URL/HTML **dans** un fichier d'équipe : même sortie du système que `use_figma`, par une autre porte. |
| `mcp__figma-officiel__generate_diagram` | `humain` | Écrit du contenu FigJam dans l'espace de l'équipe : moins destructeur, aussi peu retirable. |
| `mcp__figma-officiel__create_new_file` | `humain` | Crée un fichier dans l'espace Figma de l'équipe — la seule écriture du serveur officiel **vérifiée en réel** (« 201, fichier créé », relevé docs/30 §3). |
| `mcp__figma-officiel__upload_assets` | `humain` | Téléverse des binaires dans l'espace de l'équipe : ce qui est monté y reste et porte notre nom. |
| `mcp__figma-officiel` | `auto` | Le reste du serveur — les outils de lecture, 21 des 26 relevés en docs/20 §6 — n'est ni irréversible ni sortant. C'est ce fourre-tout qui rend la liste au-dessus sûre d'être **courte** : un outil d'écriture ajouté demain au catalogue officiel serait au moins **vu**, jamais silencieux. |
| `Bash` | `auto` | Idem : rien dans le profil du designer n'appelle un shell pour autre chose que du service, mais le nom ne le garantit pas — on le voit, on ne l'arrête pas. |

⚠ **L'ordre des entrées du designer *est* la règle** : `decide` retient la
**première** entrée `ask` qui couvre l'outil (`maestro/agents/permissions.py` —
« un auteur de politique n'a qu'à mettre le cas particulier d'abord »). Les cinq
écritures passent donc avant le fourre-tout ; les intervertir les ferait toutes
retomber en `auto`, sans qu'aucun message ne le signale.

### Ce que ces politiques ne couvrent pas

- **`orchestrateur` et `assistance`** sont des noms réservés
  (`maestro/agents/store.py`) : ni l'un ni l'autre n'est routé comme agent
  exécutant, et `PermissionStore.lire` n'est appelé qu'avec `decision.agent.nom`.
  Un fichier à leur nom serait **mort** — et jamais signalé, « pas de fichier =
  pas de politique ». Ne pas en créer.
- **La supervision (#105) passe à côté** : `NotificateurRun` appelle
  `AgentRuntime.execute` **sans `politique`**, donc sans hook — poster une
  notification Slack n'emprunte pas l'entrée `mcp__slack` ci-dessus. C'est ce qui
  évite qu'une notification annonçant « validation en attente » attende
  elle-même une validation, mais c'est une frontière de fait et non une décision
  écrite : elle est constatée ici, hors de la portée de #716.
- **Le choix du cran ne juge pas les arguments** : une politique classe des noms
  d'outils. Tout ce qui se déciderait sur la *commande* (un `rm -rf` contre un
  `ls`) est hors de sa portée, et c'est la raison pour laquelle `Bash` ne peut
  être que `auto`.

### Exercer la chaîne

`scripts/arbitrage/banc-arbitrage.py` rejoue le parcours entier sur les
politiques **versionnées de ce dossier** : la politique classe, le vrai hook
`PreToolUse` suspend, la demande atteint `GET /api/validations`, la décision est
rendue par `POST /api/validations/{tache_id}/decision`, et le journal garde
l'étape `:refus-outil` sous le statut `arbitrage_outil`. Ni réseau, ni Redis, ni
quota — le seul élément absent est le modèle qui *choisit* d'appeler l'outil, et
le banc le dit. Gardé par `tests/test_arbitrage_banc.py`.

Ce dossier est **versionné** avec le dépôt (aucun secret n'y figure). Racine
remplaçable via `MAESTRO_PERMISSIONS_DIR` (cf. `.env.example`). Contrat et
sémantique : `maestro/agents/permissions.py` et `maestro/decideur.py`.
