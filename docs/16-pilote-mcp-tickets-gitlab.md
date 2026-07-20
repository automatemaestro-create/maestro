# Pilote MCP gestion de tickets — GitLab (ticket #106)

**Version :** 0.1
Second pilote concret du socle MCP (#104, parent #101) : un agent **équipé d'un
serveur MCP GitLab** lit et crée des tickets du backlog au fil d'un run — les
runs Maestro se connectent à l'outil de gestion de tickets de l'équipe. Cette
page consigne le choix de l'outil, la configuration, les exigences sur les
secrets et la démonstration réelle.

> **Principe** : aucun connecteur GitLab dans Maestro. C'est un agent du
> catalogue (le QA ici), équipé par sa **déclaration MCP versionnée**
> ([core/mcp/qa.json](../core/mcp/qa.json)), qui lit et crée les tickets via
> ses outils `mcp__gitlab__…` — le moteur lui confie une mission, jamais un
> appel d'API. Passer à Linear ou Jira = changer la déclaration (serveur MCP
> Linear/Atlassian), pas le code.

---

## 1. Choix de l'outil : GitLab (plutôt que Linear/Jira)

La roadmap citait Linear/Jira ; le ticket laissait le choix ouvert. GitLab est
retenu pour ce pilote :

- **C'est l'outil du projet** : le backlog Maestro vit déjà sur GitLab — la
  démonstration « en conditions réelles » se fait sur de vrais tickets, sans
  compte ni espace de travail à créer ;
- **Le compte bot existe** (`MaestroAgents`, celui du CLI `glab`) : mêmes
  exigences sur les secrets que le pilote Slack, sans provisionnement neuf ;
- **La preuve est générique** : le contrat exercé (déclaration versionnée →
  montage à chaud → outils `mcp__<nom>__…`) est strictement le même pour
  Linear (`mcp.linear.app`) ou Jira (serveur MCP Atlassian) — seul le fichier
  de déclaration change.

## 2. Configuration

### 2.1 Serveur MCP retenu

[`@zereight/mcp-gitlab`](https://www.npmjs.com/package/@zereight/mcp-gitlab)
(v2.1.x, commande locale stdio via `npx`) — le serveur communautaire de
référence pour GitLab, déjà cité en exemple par [docs/04 §6.1](./04-specifications-agents.md).
Il expose ~19 toolsets (issues, MR, pipelines…) ; la déclaration le **restreint
au strict besoin du pilote** :

- `GITLAB_TOOLSETS=issues` — seuls les outils de tickets sont montés
  (`get_issue`, `list_issues`, `create_issue`, `update_issue`, notes…) : ni
  merge requests, ni pipelines, ni dépôt de code ;
- `GITLAB_PERMISSION_MODE=modify` — les outils de **suppression** sont retirés
  (`delete_issue`…). Combiné au toolset, l'agent ne peut **ni merger, ni
  fermer une MR, ni rien supprimer** — l'écho côté serveur MCP des garde-fous
  Maestro (docs/10 §2).

### 2.2 Déclaration MCP de l'agent (versionnée)

[core/mcp/qa.json](../core/mcp/qa.json) équipe l'agent `qa` — consigner un
échec de tâche est un geste de revue/QA :

```json
{
  "serveurs": [
    {
      "nom": "gitlab",
      "type": "stdio",
      "commande": "npx",
      "args": ["-y", "@zereight/mcp-gitlab"],
      "env": {
        "GITLAB_PERSONAL_ACCESS_TOKEN": "${GITLAB_TOKEN}",
        "GITLAB_TOOLSETS": "issues",
        "GITLAB_PERMISSION_MODE": "modify"
      }
    }
  ]
}
```

Conformément au contrat du socle (#104, [docs/04 §6](./04-specifications-agents.md)),
le serveur est monté **à chaud** sur chaque exécution outillée de l'agent, et
une variable absente rend le serveur indisponible : échec propre avant tout
appel modèle.

### 2.3 Côté Maestro (`.env`)

```bash
GITLAB_TOKEN=…   # jamais committé ni loggué (expurgé du journal)
```

Un **Personal Access Token** du compte bot (scope `api`) est la forme durable
recommandée. La démonstration a utilisé le token OAuth du CLI `glab` (même
compte bot) : il fonctionne à l'identique — l'API GitLab l'accepte en
`Authorization: Bearer` — mais **expire en ~2 h** (rafraîchi par `glab`, pas
par Maestro) ; pour un usage récurrent, préférer le PAT.

Dans la grille des modes d'auth MCP ([docs/21 §2](./21-configuration-mcp.md)),
GitLab est le cas le plus simple : **token statique saisissable** — un humain
le crée dans l'UI de l'outil, Maestro le consomme tel quel.

## 3. Le token ne transite jamais en clair (critère du ticket)

Mêmes exigences que le pilote Slack (#105) — défense en profondeur, trois
verrous :

1. **Déclaration versionnée sans secret** : `core/mcp/qa.json` ne porte que la
   référence `${GITLAB_TOKEN}`, résolue depuis l'environnement au moment du
   montage (#104) — la valeur effective n'existe qu'en mémoire, et l'API/UI de
   la fiche agent masque toute valeur littérale.
2. **Hors de portée de l'agent** : le token est injecté dans l'environnement du
   **sous-processus du serveur MCP**, pas dans le contexte du modèle — ni le
   prompt de mission, ni le playbook de l'agent ne le contiennent.
3. **Journaux expurgés** ([maestro/telemetry/redact.py](../maestro/telemetry/redact.py)) :
   la valeur de `GITLAB_TOKEN` (variable sensible) et le motif `glpat-…`
   (Personal Access Tokens GitLab) sont masqués (`[secret masqué]`) de toute
   étape consignée — journal du run, fil temps réel Control Tower, trace
   Langfuse.

## 4. Démonstration réelle (2026-07-17)

Run réel mené de bout en bout — même consignation sur pièces que
[docs/15](./15-pilote-mcp-slack.md).

### 4.1 Dispositif

| Élément | Valeur |
|---------|--------|
| Projet GitLab | `maestro-group4345327/maestro` (le backlog du projet lui-même) |
| Serveur MCP | `@zereight/mcp-gitlab` 2.1.40 (stdio via `npx`), déclaré dans [core/mcp/qa.json](../core/mcp/qa.json), toolset `issues` + mode `modify` |
| Agent | `qa` (runtime outillé, Claude Sonnet), équipé du serveur à chaud |
| Compte / token | Bot `MaestroAgents`, token OAuth `glab` via `${GITLAB_TOKEN}` |
| Garde-fous | `--plafond-cout 3`, `--timeout 600`, relances par défaut |
| `run_id` | `c269e3232c50` |

```bash
maestro-run --json --trace --plafond-cout 3 --timeout 600 \
  "Tâche unique de revue QA (compétences requises : qa, review) — une seule \
tâche suffit […] : (1) lire le ticket d'IID 106 du projet GitLab \
« maestro-group4345327/maestro » et relever son titre exact ; (2) créer dans ce \
même projet un nouveau ticket consignant un échec de tâche simulé […]. \
Livrable : l'IID et l'URL du ticket créé."
```

### 4.2 Les deux critères du ticket, exercés en réel (vérifiés)

Le plan produit **une** tâche (`revue-qa-mcp-tickets`, compétences `qa` +
`review`), routée sur l'agent `qa`. Son exécution (1 appel modèle, 6 tours,
53,8 s, ~0,28 $) enchaîne les outils `Bash`, `mcp__gitlab__get_issue`,
`mcp__gitlab__create_issue`, `Write` :

1. **Lecture d'un ticket existant** — `get_issue` sur l'IID 106 : titre exact
   relevé (« Pilote MCP gestion de tickets (Linear/Jira ou GitLab) »), aucune
   modification apportée.
2. **Création d'un ticket** (consigner un échec de tâche) — `create_issue` a
   créé le ticket réel [#114](https://gitlab.com/maestro-group4345327/maestro/-/issues/114)
   « [démo #106] Échec de tâche consigné par l'agent QA », labels `type::bug`
   + `prio::basse`, description en français citant le titre exact de #106, la
   tâche en échec (`verification-livrable`) et sa cause. L'agent a ensuite
   **relu** le ticket créé (`get_issue`) pour vérifier la persistance exacte
   des données — vérification recoupée côté humain par `glab issue view 114`.

Bilan du run : **1/1 tâche réussie**, 2 appels modèle, 114 639 tokens,
coût 0,3649 $, durée 64,7 s.

### 4.3 Lecture critique

- **Token vérifié absent** des artefacts du run : 0 occurrence de la valeur de
  `GITLAB_TOKEN` (et du motif `glpat`) dans la trace (`--trace`) comme dans le
  rapport `--json` — conjonction des trois verrous du §3.
- L'agent est **resté dans le périmètre** : lecture puis création, aucune
  modification du ticket lu ; le toolset restreint (§2.1) rendait de toute
  façon toute action de merge/suppression impossible côté serveur.
- Le routage par compétences (`qa`, `review`) a suffi : pas de flag dédié ni
  de code moteur nouveau — ce pilote est **purement configuratif** (une
  déclaration versionnée + un objectif de run), là où le pilote Slack (#105)
  avait dû ajouter le notificateur de supervision.
- Le montage à chaud du socle #104 a fonctionné tel quel sur `main` : le sas
  de connexion renforcé livré par #105 (en revue au moment de ce run) n'a pas
  été nécessaire ici — la mission multi-tours laisse le temps aux outils MCP
  de s'enregistrer.

## 5. Limites connues (POC)

- Le pilote exige un agent à **runtime outillé** (`developpeur`, `bdd`, `qa`,
  `devops`) : un fournisseur texte-seul ne peut pas monter de serveur MCP.
- Chaque mission est une exécution outillée complète (espace isolé + session
  SDK) : quelques dizaines de secondes et quelques centimes — le prix du
  « zéro connecteur » au POC.
- Le token OAuth `glab` expire en ~2 h (cf. §2.3) : un run lancé avec un token
  périmé échoue proprement (`401` remonté par les outils, jamais relancé) —
  préférer un PAT pour un montage durable.
- Les tests du parent #101 sont différés au lot final : **tests différés → #103**.
