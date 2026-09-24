# 44 — Maestro possède ses contrats

**Date :** 2026-09-24. **Consignée par :** #1315.
**Jalon :** *Indépendant du modèle — un agent travaille avec n'importe quel fournisseur, Maestro possède ses contrats* (neuf, échéance 2028-02-04).
**Tickets :**
- **#1291** : la checklist devient un verbe de Maestro ;
- **#1304** : le point de contrôle des outils ferme par défaut ;
- **#1305** : les pannes se lisent dans les champs typés ;
- **#1306** : le fournisseur d'un agent est honoré, ou le run le dit ;
- **#1307** : un agent travaille avec n'importe quel fournisseur, en quatre lots (#1308 à #1311) ;
- **#1312** : le contrat de l'adaptateur Claude se vérifie ;
- **#1313** : le serveur MCP navigateur a une version connue.

---

## 0. La décision

**Une règle, que la personne a posée et fait inscrire.** *Maestro est indépendant du modèle et des outils externes.* Ce dont Maestro a besoin pour conduire ses agents, il le **possède** : ses verbes, ses outils, sa politique, ses contrats. Un fournisseur de modèle (Claude, OpenAI, un modèle local…) ou un outil tiers (un CLI, un serveur MCP, un navigateur) n'en est qu'un **adaptateur remplaçable**, qui traduit.

Trois gestes en découlent, et [CLAUDE.md](../CLAUDE.md) les porte en une phrase :
- on ne lit jamais un **outil interne** d'un CLI pour savoir ce que fait un agent ;
- on ne reconnaît jamais une panne à son **texte** ;
- on ne verrouille jamais une **version** pour garder un comportement. Un comportement dont Maestro a besoin s'écrit dans ses propres contrats.

## 1. L'origine

Le 2026-09-24, la personne relève que la checklist de chaque tâche reste à « 0/N · relevé incomplet ». La cause : Maestro lisait la checklist dans les appels à `TodoWrite`, un outil interne du CLI Claude, et le CLI embarqué par le SDK 0.2.159 l'a remplacé par `TaskCreate`/`TaskUpdate`. La première correction proposée, lire le nouvel outil, remplaçait une dépendance par une autre. La personne :

> « Pour ce point, faire attention, nous ne devons pas être dépendant de la CLI Claude. Attention. »

> « Il faut que tu t'en souviennes stp. Maestro est indépendant du modèle et des outils externes. Analyse si on a d'autres dépendances. »

## 2. L'analyse

Trois balayages du code produit, en lecture seule, le même jour.

**Ce qui est déjà propre :**
- `claude_agent_sdk` n'est importé que dans `maestro/providers/claude.py` ;
- l'orchestration (fil, planificateur, classifieur, récit de fin, équipe, outillage) passe par l'abstraction des fournisseurs, et marche sur un fournisseur compatible OpenAI. Le protocole du fil est du texte, choisi pour cela ;
- git est appelé en sorties stables (`-z`, `--porcelain`), et son absence se dit ;
- Langfuse, Docker, le sélecteur de dossier et les forges sont optionnels et se dégradent proprement ;
- aucune table de prix : le coût vient du fournisseur.

**Ce qui dépend d'un fournisseur ou d'un outil**, rangé par le risque de casser **en silence** :

| Dépendance | Ce qui casse | Ticket |
|---|---|---|
| Le point de contrôle des outils est un hook du CLI Claude, qui laisse passer un appel sans nom lisible. `Grep`/`Glob` échappent aux exclusions du périmètre | Garde-fous et secrets ouverts sans un mot | #1304 |
| La checklist est lue dans `TodoWrite` | Toutes les checklists à 0/N depuis le 22/09 | #1291 |
| Les pannes sont reconnues par le texte du message | Le plafond de tours n'est plus reconnu depuis le SDK 0.2.159, et l'erreur est relancée comme un aléa | #1305 |
| Seul l'adaptateur Claude sert `run_agent` ; le `fournisseur` d'un agent est déclaratif ; le repli texte est muet | Un agent réglé sur un autre fournisseur « réussit » sans rien faire | #1306, #1307 |
| Le vocabulaire des outils (`Bash`, `Read`, `mcp__<serveur>__<outil>`) est inscrit dans les permissions, la portée, la frontière, les équipes et les playbooks | Une règle cesse de correspondre si le CLI renomme un outil ; aucun sens pour un autre fournisseur | #1308 |
| L'isolement de session, l'environnement et l'usage sont supposés ; versions non verrouillées ; CLI du conteneur sans version | Une session s'élargit, ou des jetons sont comptés deux fois, sans un mot | #1312 |
| Le serveur MCP navigateur est pris en `@latest` à l'exécution | Une version inconnue à chaque lancement | #1313 |

S'y ajoutent trois points notés sur leurs tickets :
- des processus survivent à leur tâche (#1279) ;
- la liste des modèles est recopiée et diverge déjà (#1270) ;
- la coque passe par `bash start.sh`, et Redis, donc Docker, est le magasin par défaut (#641).

## 3. Ce qui est renversé

- **[docs/04 §4](./04-specifications-agents.md)** : *« le champ `fournisseur` est déclaratif, le moteur exécute sur `MAESTRO_PROVIDER` »*. Le fournisseur d'un agent est désormais **honoré**, ou le run dit pourquoi il ne peut pas l'être (#1306).
- **[docs/37](./37-decision-equipe-sur-mesure.md) (« Différé, et pourquoi ») et [docs/06](./06-roadmap.md) « Au-delà »** : l'exécution outillée par un fournisseur non-Anthropic était différée, sans ticket (#1044). Elle a son jalon et son chantier (#1307).

**Pourquoi.** Un produit qui ne fait agir ses agents qu'à travers un seul outil d'un seul fournisseur n'est pas indépendant du modèle : il l'est dans son orchestration, pas dans son travail. Et une dépendance au comportement **interne** d'un outil tiers casse sans prévenir, comme `TodoWrite` l'a montré.

## 4. La voie retenue

**Retenue : Maestro possède sa boucle et ses outils** (#1307).
- Un vocabulaire de capacités propre à Maestro (#1308).
- Des outils de fichiers et de shell servis par Maestro, et une boucle agentique qui conduit tout fournisseur sachant appeler des outils, sous la même politique appliquée **dans** Maestro (#1309).
- Un client MCP de Maestro (#1310).
- Un fournisseur configurable et éprouvé depuis l'interface (#1311).

**Écartées :**
- **Lire le nouvel outil du CLI** (`TaskCreate`) à la place de l'ancien : c'est une dépendance remplacée par une autre.
- **Verrouiller le SDK pour garder `TodoWrite`** : c'est la même dépendance, figée, et un produit qui cesse de suivre ses modèles. Le verrou de #1312 sert la reproductibilité et la **détection** d'un contrat rompu, rien d'autre.
- **Brancher des agents CLI tiers par ACP comme voie principale** ([docs/34](./34-decision-agent-cli-tiers-acp.md)) : Maestro dépendrait de la boucle, des outils et du comportement de chaque CLI tiers. ACP reste une option d'**adaptateur**, aux conditions de docs/34.

## 5. Ce qui ne bouge pas

- **L'adaptateur Claude, par le SDK, reste de premier rang.** C'est lui qui sert l'abonnement de la personne. Il parle désormais les contrats de Maestro, et non l'inverse. Servir aussi à Claude les outils de fichiers et de shell de Maestro, à la place de ceux du CLI, se décide **sur le banc**, sur la qualité et le coût, pas par principe.
- **Les garde-fous** : permissions, frontière d'écriture, arbitrage des actes, diff à valider, secrets. Ils changent de lieu d'application (Maestro plutôt qu'un hook tiers), jamais de sévérité.
- **Les standards ouverts** ne sont pas des dépendances à défaire : MCP, git, `AGENTS.md` et Agent Skills. On en dépend comme d'un format, pas comme d'un fournisseur.
- **[docs/41](./41-decision-maestro-juge-il-ne-bride-pas.md)** : cette règle en est le prolongement côté fournisseurs. Aucun catalogue fermé de modèles, aucune liste recopiée.

## 6. Ce qui la rouvrirait

- Un fournisseur qui ne peut être conduit **que** par son propre outil, sans API d'appel d'outils. Il se branche alors par un adaptateur dédié (ACP, docs/34), sans que ses noms entrent dans le reste du produit.
- Une mesure sur le banc montrant que la boucle de Maestro rend un travail **nettement moins bon** que celle du CLI de Claude sur les mêmes tâches. La boucle se corrige : on ne revient pas à la dépendance.
