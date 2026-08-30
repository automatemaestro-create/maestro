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

  ⚠ **Un troisième cran, `orchestrateur`, a été retiré** ([docs/32](../../docs/32-decision-cran-orchestrateur.md),
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
Control Tower.

**Ce fichier s'édite depuis la Control Tower** (#262) — onglet « MCP &
permissions » de la fiche agent, `PUT /api/permissions/<agent>` : les listes
`allow` et `deny` s'y ajoutent et s'y retirent entrée par entrée, avec en
suggestion les outils **réellement exposés** à cet agent (ceux de son profil,
les verbes du serveur `maestro`, ses serveurs MCP montés). Quatre choses à
connaître :

- l'écran **suggère sans restreindre** : un outil MCP précis se désigne à la
  frappe, seule la **forme** de l'entrée est jugée, et ce que rien d'exposé
  n'explique est *signalé* — jamais refusé (un serveur désactivé depuis, un
  outil à venir et une faute de frappe se ressemblent ici) ;
- la **validation est la même dans les deux sens** (`politique_validee`) : ce
  que l'écran écrit, le moteur sait le relire, et une entrée mal formée est
  refusée **avec son motif** sans que le fichier bouge ;
- l'écriture **ne relit pas** ce qu'elle remplace — c'est ce qui permet de
  réparer depuis l'écran une politique devenue illisible, là où un aller-retour
  échouerait sur le fichier même qu'on vient corriger ;
- la liste **`ask` s'affiche mais ne s'édite pas** : son cran (`auto`/`humain`)
  est une décision prise à froid, et la poser à moitié — une entrée créée sans
  choisir son décideur — la ferait retomber en silence sur le défaut. Elle se
  règle ici, dans le fichier.

⚠ Ce dossier étant **versionné** (voir plus bas), chaque enregistrement modifie
un fichier suivi par git — celui du dossier de travail où tourne la Control
Tower, un worktree ayant le sien (docs/10 §9) : la modification est à commiter
comme le reste, et l'écran le dit.

Un appel **arbitré** laisse la même trace, sous un statut à lui
(`arbitrage_outil`) : approuvé, refusé ou encore en attente, ce n'est pas un
refus. L'étape en nomme le **décideur** — « Outil arbitré (humain) » —, et le
tient de cette politique-ci et non du texte du motif : qui a tranché se lit, il
ne se déduit pas. ⚠ Le champ reste porté même si `humain` est désormais la seule
valeur qu'une **attente** puisse porter (un `auto` n'atteint jamais la file, le
hook le court-circuite) : le journal est **rejoué**, et des étapes déjà écrites
nomment le cran retiré. Ce qui a été supprimé est le routage, jamais la mémoire
de ce qui a été décidé.

Ce dossier est **versionné** avec le dépôt (aucun secret n'y figure). Racine
remplaçable via `MAESTRO_PERMISSIONS_DIR` (cf. `.env.example`). Contrat et
sémantique : `maestro/agents/permissions.py` et `maestro/decideur.py`.

Tests : `tests/test_permissions.py` pour la **lecture** et l'application
(sémantique, arbitrage, montage runtime, moteur, refus au vol, journal), et
`tests/test_permissions_ecriture.py` pour ce que #262 a ouvert — le dépôt en
écriture, le point de passage unique de la validation, la route
`PUT /api/permissions/{agent}` et les suggestions servies avec la fiche. L'écran
qui s'en sert est gardé par `apps/web/tests/agent-permissions.test.tsx`.
