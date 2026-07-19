# Gestion fine des secrets par agent et intégration (ticket #109)

**Version :** 0.1
Deuxième lot du renforcement sécurité (#102) : les tokens des intégrations MCP
sortent de l'environnement global du process au profit d'un **coffre local par
agent** — chaque agent ne résout que **ses** secrets — et toute valeur
réellement servie est **masquée automatiquement** si elle réapparaît en sortie
(journal, traces Langfuse, rapports, tickets). Cette page consigne le contrat,
l'architecture et les limites. Le coffre est **opt-in par provisionnement** :
sans lui, rien ne change.

> **Pourquoi** : jusqu'ici tout secret était visible de tout le process
> (`os.environ`), et chaque intégration MCP (#101 : Slack #105, GitLab #106…)
> en ajoute. En cas de compromission d'un agent ou de fuite de journal, tout
> partait d'un coup. Tests : `tests/test_secrets.py` (#107) ; modèle de
> menace : [docs/19](./19-securite-modele-de-menace.md).

---

## 1. Contrat

Deux garanties, alignées sur les critères du ticket :

1. **Scoping par agent** : dès que le coffre est provisionné (premier fichier
   `<agent>.json` écrit — le README versionné ne compte pas), les références
   `${VARIABLE}` des déclarations MCP d'un agent (#104) se résolvent dans
   **son coffre seulement** (`core/secrets/<agent>.json`). Le token GitLab du
   QA n'est pas résolvable par le DevOps, et réciproquement. Un secret absent
   du coffre rend le serveur **indisponible** (`McpServerUnavailable`, échec
   propre avant tout appel modèle), même si la variable existe dans le shell —
   c'est voulu : le scoping strict prime sur la commodité.
2. **Masquage automatique en sortie** : toute valeur servie (lue dans un
   coffre, ou résolue depuis une référence `${VAR}` quel que soit
   l'environnement de résolution) est enregistrée au **registre de rédaction**
   (`maestro.telemetry.redact.enregistre_secret`). `redact_secrets` — déjà sur
   le chemin de toute consignation (`RunJournal.consigne`) — la remplace par
   `[secret masqué]` où qu'elle réapparaisse. Le journal alimentant l'export
   Langfuse (#81), le pont Control Tower (#98) et les livrables consignés, le
   masquage suit partout, y compris dans un compte-rendu d'agent qui citerait
   son token.

## 2. Architecture : une indirection, trois couches

```
core/mcp/<agent>.json           core/secrets/<agent>.json      (jamais versionné)
  "env": {"TOKEN": "${VAR}"}      {"secrets": {"VAR": "valeur"}}
        │ déclaration (versionnée)        │ coffre (local, par agent)
        ▼                                 ▼
LocalExecutor._produce ── environ = SecretStore.environ(agent) ──▶ AgentRuntime.execute
                                                                        │
                                              resolus(serveurs, environ)│ + enregistre_secret
                                                                        ▼
                                                    fournisseur (montage SDK, en mémoire)
```

- **`maestro/agents/secrets.py`** (`SecretStore`) : le coffre, un fichier JSON
  par agent, même pattern que les dépôts voisins (`core/mcp/`, `core/capacite/`) ;
  relu **à chaud** à chaque tâche — tourner un secret vaut pour la tâche
  suivante. `environ(agent)` est la bascule : coffre provisionné → secrets de
  l'agent seuls ; coffre absent → `os.environ` (comportement historique #104).
- **`maestro/agents/mcp.py`** (`resolus`) : la résolution existante, inchangée
  dans sa forme — elle reçoit simplement l'environnement scopé, et enregistre
  chaque valeur résolue au registre de rédaction.
- **`maestro/telemetry/redact.py`** : le registre des **secrets servis** — la
  liste de rédaction n'est plus figée au code (`_ENV_SENSIBLES`, motifs
  `sk-…`/`glpat-…`/`xox…`), elle suit ce qui a réellement été confié aux
  agents. En mémoire process seulement, jamais persistée ni réémise.

Câblage : `LocalExecutor(secrets=…)` — branché par défaut dans
`OrchestrationEngine.default()` et les workers Celery (`maestro.queue.worker`),
ainsi que sur le notificateur de supervision (#105, `maestro.supervision`).
En mode distribué, moteur et workers doivent voir le **même stockage**
(`MAESTRO_SECRETS_DIR`), comme pour les autres dépôts.

## 3. Activation

```bash
# Écrire le premier coffre = activer le scoping (pour TOUS les agents) :
cat > core/secrets/qa.json <<'EOF'
{"secrets": {"GITLAB_TOKEN": "glpat-…"}}
EOF
```

- La bascule est le **premier coffre écrit** : migrez d'un coup tous les
  agents à intégrations (au POC : `qa` → `GITLAB_TOKEN`, `devops` →
  `SLACK_BOT_TOKEN`/`SLACK_TEAM_ID`), sinon les serveurs des agents non
  migrés deviennent indisponibles (échec propre, cause nommée) ;
- Racine remplaçable par `MAESTRO_SECRETS_DIR` (cf. `.env.example`) ;
- `.gitignore` couvre `core/secrets/*` (seul le README y est versionné) ;
- un coffre **invalide** (JSON illisible, nom de variable malformé) est un
  échec de tâche propre, cause exacte consignée — jamais un montage partiel.

## 4. Ce que le lot ne couvre pas (et pourquoi)

- **Clés fournisseur** (`ANTHROPIC_API_KEY`, token OAuth) : elles restent
  portées par la config du process (`maestro.config` → `Credentials`) — le
  fournisseur est une dépendance du moteur, pas d'un agent ; elles ne passent
  jamais dans l'environnement de résolution scopé, sont neutralisées dans le
  sous-processus CLI (`_auth_env`) et figurent dans la liste fixe de rédaction.
  Leur sortie de l'environnement global relève d'un gestionnaire de secrets
  (V1) — l'indirection `SecretStore` est le point d'accroche prévu.
- **Chiffrement au repos** : le coffre POC est un fichier local en clair,
  hors dépôt Git. Passer sur Vault/SOPS ne changera pas le contrat (`environ`).
- **Mode isolé (#108)** : rien à changer — les secrets MCP résolus voyagent
  déjà en mémoire dans la config MCP du CLI, jamais dans l'environnement du
  conteneur (`ENV_TRANSMISES` reste limité à l'auth fournisseur).

## 5. Vérification du masquage

Le critère « masquage vérifié » est instrumenté par les tests du lot final
(#107, `tests/test_secrets.py`) : valeur de coffre réapparaissant dans une
sortie d'agent → `[secret masqué]` au journal (qui alimente l'export Langfuse
et les événements Control Tower) ; scoping → serveur indisponible pour l'agent
non détenteur, même si la variable existe dans l'environnement du process. En
complément, la vérification manuelle tient en un run avec coffre provisionné et
`maestro-run --trace` : aucun `glpat-…`/`xoxb-…` ne doit apparaître en clair.
