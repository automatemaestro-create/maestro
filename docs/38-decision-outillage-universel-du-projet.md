# 38 — L'outillage universel d'un projet : le format, sa place, et la frontière

**Date :** 2026-09-20. **Consignée par :** #1029, lot 1/7 de #1020.
**Jalon :** *L'équipe sur mesure — chaque projet s'outille et recrute ses agents*.
**Vérifications :** menées le 2026-09-20 sur les documentations en ligne des quatre clients, et sur
le dépôt (`maestro/providers/claude.py`, `claude_agent_sdk` 0.2.128). Les sources sont citées à
l'endroit où elles servent. **Ce qui n'est pas vérifié n'est pas cité.**

---

## 0. Ce que ce document arrête

[docs/37 §4.5](./37-decision-equipe-sur-mesure.md) avait retenu les deux standards — `AGENTS.md` et
Agent Skills — et laissé une question ouverte en toutes lettres : « **L'emplacement des skills n'est
fixé par aucune spécification.** Il se tranche dans #1029, une fois vérifié où chaque client cherche
les siens, et `AGENTS.md` le désigne. » C'est fait, et la réponse est plus courte que la
vérification :

| Ce que Maestro génère | Où | Pourquoi, en un mot |
| --- | --- | --- |
| Les instructions | `AGENTS.md` à la racine | Le seul fichier d'instructions dont l'emplacement soit écrit quelque part (§3.1) |
| Deux ponts d'une ligne | `CLAUDE.md`, `GEMINI.md` à la racine | `AGENTS.md` seul ne joint pas les quatre clients (§2.2, §3.2) |
| Les skills | `.agents/skills/<nom>/SKILL.md` | Le seul chemin projet lu par plus d'un client — trois sur quatre (§2.1, §3.3) |
| Les scripts | dans le skill, ou dans le dossier de scripts du projet | La spécification tranche le premier cas, le projet le second (§3.4) |
| Les commandes | **aucune** | Trois clients, trois formats, aucun commun — et un skill s'appelle déjà par son nom (§3.5) |
| Le manifeste | `.maestro/outillage/manifeste.json` | La mémoire de ce qui a été généré, d'où, et par quelle version (§4) |

Et une frontière, qui est l'autre moitié de la décision (§5) : **l'outillage voyage dans un seul
sens.** Maestro l'écrit dans le projet ; rien de ce qui est dans le projet ne s'impose de lui-même à
son runtime. Ce chantier est précisément celui qui rend cette règle difficile à tenir, parce que
Maestro écrit un `CLAUDE.md` et que le runtime de Maestro **est** Claude Code : §5.3 mesure la porte
restée ouverte, et la referme dans #1032.

## 1. Pourquoi une note, et pas un choix d'implémentation

Six lots suivent celui-ci. Trois **écrivent** cet outillage (#1030 le recommande, #1031 le choisit,
#1033 le génère), un le **lit** (#1032), un l'**ouvre** au parcours de création (#1034), un le
**teste** (#1035). Trois d'entre eux sont marqués parallèles : s'ils tranchaient chacun
l'emplacement, ils trancheraient trois fois, et le lot de tests découvrirait l'écart en dernier.

Le format est donc une décision **avant** le code, et elle tient à des faits extérieurs au dépôt —
ce que quatre clients lisent aujourd'hui. Ces faits bougent ; c'est pourquoi §2 les date et les
source, et §7 dit lesquels rouvriraient la décision.

## 2. Ce qui a été vérifié

### 2.1 Les skills : quatre clients, quatre listes, une seule intersection utile

La [spécification Agent Skills](https://agentskills.io/specification) décrit un skill
(`<nom>/SKILL.md`, plus `scripts/`, `references/`, `assets/` en option) et **ne dit rien de l'endroit
où il vit dans un projet**. Chaque client a le sien :

| Client | Chemins **projet** lus | Chemins personnels | Source |
| --- | --- | --- | --- |
| Claude Code | `.claude/skills/<nom>/SKILL.md` — depuis le répertoire de lancement et chaque parent jusqu'à la racine du dépôt ; un sous-dossier est chargé à la première lecture d'un fichier qui s'y trouve | `~/.claude/skills/` | [code.claude.com](https://code.claude.com/docs/en/skills) |
| Codex | `.agents/skills/<nom>/SKILL.md` — balayé en `$CWD`, `$CWD/..` et `$REPO_ROOT` | `~/.agents/skills/`, `/etc/codex/skills` | [learn.chatgpt.com](https://learn.chatgpt.com/docs/build-skills) |
| Gemini CLI | `.gemini/skills/` **ou** `.agents/skills/` — alias, et `.agents/` l'emporte | `~/.gemini/skills/`, `~/.agents/skills/` | [geminicli.com](https://geminicli.com/docs/cli/skills/) |
| GitHub Copilot | `.github/skills/`, `.claude/skills/`, `.agents/skills/` | `~/.copilot/skills/`, `~/.claude/skills/`, `~/.agents/skills/` | [docs.github.com](https://docs.github.com/en/copilot/concepts/agents/about-agent-skills), [code.visualstudio.com](https://code.visualstudio.com/docs/copilot/customization/agent-skills) |

Deux lectures de ce tableau, et la seconde est celle qui décide :

- **`.agents/skills/` est lu par trois clients sur quatre** — Codex, Gemini CLI, Copilot. C'est le
  seul chemin dans ce cas, et le seul qui ne porte le nom d'aucun éditeur.
- **Aucun chemin n'est lu par les quatre.** `.claude/skills/` en réunit deux (Claude Code, Copilot),
  `.github/skills/` et `.gemini/skills/` un seul chacun. Il n'y a donc pas d'emplacement à trouver :
  il y a un emplacement à **choisir**, et un client à joindre autrement.

`.codex/skills` n'existe pas : c'est une demande ouverte
([openai/codex#22590](https://github.com/openai/codex/issues/22590)), pas un chemin lu. Elle est
citée ici parce qu'elle dit la direction du vent, pas parce qu'on s'appuie dessus.

Le reste de la spécification, retenu tel quel : `name` (≤ 64 caractères, `[a-z0-9-]`, ni tiret en
tête ou en fin ni double tiret, **et il doit être égal au nom du dossier**) et `description`
(≤ 1024) sont requis ; `license`, `compatibility`, `metadata` et `allowed-tools` sont optionnels ;
les fichiers d'un skill se référencent en **chemins relatifs à la racine du skill**, sur un seul
niveau. La divulgation est progressive — les métadonnées au démarrage, le corps de `SKILL.md` à
l'activation, `scripts/` et `references/` à la demande.

### 2.2 Le fichier d'instructions : `AGENTS.md` seul ne joint pas les quatre

[agents.md](https://agents.md/) place le fichier sans ambiguïté — « Create an AGENTS.md file at the
root of the repository », un par paquet dans un monorepo, « the closest AGENTS.md to the edited file
wins ». C'est du Markdown nu, sans frontmatter ni section obligatoire. Mais *être lu* ne se déduit
pas de *être placé* :

| Client | Lit `AGENTS.md` ? | Ce que la documentation dit |
| --- | --- | --- |
| Codex | **Oui**, nativement | C'est le client d'origine du format |
| GitHub Copilot | **Oui**, nativement | Précédence : instructions personnelles > `.github/instructions/**` > `.github/copilot-instructions.md` > `AGENTS.md` > organisation |
| Claude Code | **Sous condition** | Depuis la v2.1.277, et **seulement s'il n'y a aucun `CLAUDE.md` dans le répertoire courant ou au-dessus**. Un `CLAUDE.md` contenant `@AGENTS.md` marche toujours, et ne le fait jamais lire deux fois |
| Gemini CLI | **Non, par défaut** | Le fichier de contexte par défaut est `GEMINI.md`. `AGENTS.md` demande soit `context.fileName` dans `.gemini/settings.json`, soit un import `@AGENTS.md` depuis `GEMINI.md` |

C'est ce tableau, et lui seul, qui justifie les deux fichiers d'une ligne de §3.2. Sans eux,
l'outillage d'un projet serait invisible pour deux des quatre clients — dont celui que Maestro
utilise lui-même.

### 2.3 Les commandes : aucun format commun

| Client | Fichier | Format |
| --- | --- | --- |
| Claude Code | `.claude/commands/<nom>.md` | Markdown — et **fusionné dans les skills** : `.claude/skills/<nom>/SKILL.md` produit le même `/<nom>`, l'ancien format restant lu |
| Gemini CLI | `.gemini/commands/<nom>.toml` | TOML, champ `prompt` requis |
| Copilot / VS Code | `.github/prompts/<nom>.prompt.md` | Markdown |
| Codex | — | Aucun fichier de commande de dépôt documenté ; un skill s'appelle par `$` ou `/skills` |

Markdown, TOML, Markdown à double extension, rien : il n'y a pas de format de commande à choisir.
En revanche, **un skill s'invoque déjà par son nom** dans deux des quatre clients — `/<nom>` dans
Claude Code, `$<nom>` dans Codex. C'est ce qui rend §3.5 possible sans rien perdre.

## 3. Le format arrêté

### 3.1 `AGENTS.md` à la racine, et ce qu'il porte

Un seul fichier d'instructions, à la racine du projet, en Markdown nu. La spécification n'impose
aucune section ; Maestro s'en donne six, parce qu'un gabarit stable est ce qui permet à #1033 de
régénérer et à #1032 de lire :

| Section | Ce qu'elle porte |
| --- | --- |
| `## Le projet` | Ce qu'il est, en un paragraphe. Un agent qui arrive ne le sait pas |
| `## Monter et lancer` | Installation, build, exécution — les commandes réelles, constatées sur le projet |
| `## Vérifier` | Tests et lint, et comment n'en jouer qu'une partie |
| `## Conventions` | Style, nommage, ce qu'on ne fait pas ici |
| `## L'outillage de ce projet` | **La désignation** (§3.3) : où sont les skills, où sont les scripts, et l'index des skills |
| `## Ce qu'un agent ne touche pas` | Secrets, fichiers générés, et `.maestro/`, qui n'est pas le livrable |

Le fichier se termine par une ligne visible qui nomme Maestro, sa version et le manifeste. Elle est
inerte pour un agent et utile pour la personne qui ouvre le fichier et se demande d'où il sort.

**L'index des skills est dérivé, jamais écrit à la main** : c'est le couple `name` / `description`
du frontmatter de chaque skill, recopié au moment de la génération. Deux orthographes d'une même
description finiraient par diverger, et c'est la description qui décide si un agent ouvre le skill.

### 3.2 Deux ponts d'une ligne, jamais une copie

À côté d'`AGENTS.md`, Maestro écrit deux fichiers d'**une ligne** :

```
CLAUDE.md   →  @AGENTS.md
GEMINI.md   →  @AGENTS.md
```

C'est la syntaxe d'import de chacun des deux clients, vérifiée (§2.2). Le dépôt en porte déjà le
précédent, écrit par `create-next-app` et pas par nous :
[`apps/web/CLAUDE.md`](../apps/web/CLAUDE.md) contient exactement `@AGENTS.md`.

**Un pont, jamais une copie.** Recopier le texte d'`AGENTS.md` dans deux autres fichiers donnerait
trois sources pour une instruction, et la première correction faite à l'une des trois créerait
l'écart. La documentation de Claude Code ferme même le dernier doute : garder l'import « ne fait
jamais lire `AGENTS.md` deux fois ».

Écarté : **écrire `.gemini/settings.json`** pour y poser `context.fileName`. C'est le fichier de
réglages du client, pas un fichier du projet ; le toucher reviendrait à configurer l'outil de
quelqu'un pour lui, là où `GEMINI.md` est un fichier de texte comme les deux autres. Si la personne
préfère le réglage, l'import ne la gêne pas.

Si le projet **possède déjà** l'un de ces fichiers, Maestro ne l'écrase pas — il n'en possède aucun
qu'il n'ait écrit (§4.2). Il y insère un bloc délimité, ou le signale ; c'est le même mécanisme, et
il est décrit avec le manifeste.

### 3.3 Les skills : `.agents/skills/`, et `AGENTS.md` le désigne

**Les skills d'un projet vivent dans `.agents/skills/<nom>/`, en un seul exemplaire.** Deux raisons,
et la seconde compte autant que la première :

1. C'est le seul chemin lu par plus d'un client — trois sur quatre (§2.1).
2. C'est le seul qui **ne porte le nom d'aucun éditeur**. Un outillage universel rangé dans
   `.claude/` dirait le contraire de ce qu'il est, et le premier projet qui change de client aurait
   à déménager son outillage.

Reste Claude Code, qui ne balaie pas `.agents/skills/`. Il n'est pas joint par une copie, il l'est
par la **désignation** — la section `## L'outillage de ce projet` d'`AGENTS.md`, qu'il lit par le
pont de §3.2, nomme le dossier, donne l'index des skills et dit quoi en faire :

> Les skills de ce projet sont dans `.agents/skills/`. Si ton agent ne les charge pas de lui-même,
> lis le `SKILL.md` de celui qui correspond à ta tâche avant de commencer.

**Ce que cette voie ne donne pas, et il faut le dire** : dans Claude Code, ces skills ne sont pas
découverts au démarrage, ne comptent pas dans la divulgation progressive et ne s'appellent pas par
`/<nom>`. Ils sont lus sur désignation, comme n'importe quel fichier. C'est une dégradation connue,
bornée à un client, et elle disparaîtra le jour où `.agents/skills/` entrera dans sa liste — fait à
revérifier (§7), jamais à supposer.

**Écarté : une seconde copie dans `.claude/skills/`.** Un skill n'est pas un fichier mais un arbre —
`SKILL.md`, `scripts/`, `references/`, `assets/` — dont le `SKILL.md` appelle ses scripts en chemins
relatifs. Le dupliquer, c'est dupliquer les scripts, donc corriger un bug dans l'un des deux. Le
dépôt a une règle pour ça et elle vaut ici : jamais deux doubles.

**Écarté : un lien symbolique `.claude/skills` → `.agents/skills`.** Codex documente qu'il suit les
liens ; pour Claude Code, ce n'est pas vérifié. Et sous Windows — le poste sur lequel Maestro tourne
aujourd'hui — créer un lien demande un privilège que l'utilisateur n'a pas toujours. Un mécanisme
qui marche sur certains postes seulement est pire qu'une désignation qui marche partout : il ne dit
pas sur lesquels il a échoué, et la personne découvre le manque en ne voyant rien. Il reste ouvert
à qui le veut, à la main.

### 3.4 Les scripts : deux places, et la ligne entre les deux est nette

| Le script est… | Il va… | Le `SKILL.md` l'appelle… |
| --- | --- | --- |
| utilisé par **un seul** skill | dans `.agents/skills/<nom>/scripts/` | en chemin relatif au skill — c'est la convention de la spécification |
| utilisé par **plusieurs** skills, ou lancé par une personne | dans le **dossier de scripts du projet** | par son chemin **depuis la racine du projet**, dit explicitement |

Ce second chemin est relatif à la racine, pas au skill, et le `SKILL.md` doit l'écrire ainsi
(« depuis la racine du projet, `bash scripts/tests.sh` »). C'est la seule forme qui ne dépende pas
du répertoire courant de l'agent, que rien ne garantit.

**Maestro n'invente pas le dossier de scripts du projet : il le constate.** `scripts/`, `bin/`,
`tools/` — celui que le projet a déjà, et `scripts/` seulement quand il n'en a aucun. Un projet
existant a ses habitudes, et une analyse qui les ignore produit un doublon de plus, pas un
outillage. `AGENTS.md` nomme le dossier retenu, quel qu'il soit.

### 3.5 Les commandes : Maestro n'en génère aucune

Un point d'entrée qu'on veut déclencher par son nom est un **skill**, pas une commande. Trois
arguments, tous vérifiés en §2.3 : il n'existe aucun format de commande commun ; Claude Code a
lui-même fusionné les commandes dans les skills ; et un skill s'appelle déjà par son nom dans deux
des quatre clients.

Écrire les trois fichiers quand même — un `.md`, un `.toml`, un `.prompt.md` — donnerait trois
sources à maintenir et à régénérer, pour une ergonomie que le skill rend déjà dans la moitié des
cas. C'est une option de génération que le manifeste rend ajoutable plus tard sans rien défaire ;
ce n'est pas le défaut.

### 3.6 L'arbre, en entier

```
<racine du projet>/
├── AGENTS.md                          # les instructions — la source
├── CLAUDE.md                          # @AGENTS.md
├── GEMINI.md                          # @AGENTS.md
├── .agents/
│   └── skills/
│       └── lancer-les-tests/
│           ├── SKILL.md               # name: lancer-les-tests (= le dossier)
│           ├── scripts/               # propres à ce skill
│           └── references/
├── scripts/                           # constaté sur le projet, pas imposé
└── .maestro/
    └── outillage/
        ├── manifeste.json             # §4
        └── refuses/                   # ce qu'une régénération n'a pas écrasé
```

## 4. Le manifeste

### 4.1 Ce qu'il porte

`.maestro/outillage/manifeste.json` — ce qui a été généré, depuis quelle analyse ou quels choix, et
par quelle version :

```jsonc
{
  "manifeste": 1,                              // version de ce format
  "genere_par": "maestro <version>",           // en toutes lettres, jamais dérivée après coup
  "genere_le": "2026-09-20T09:12:00+00:00",
  "source": {
    "type": "analyse",                         // analyse (#1030) | choix (#1031)
    "projet_id": "prj-7f3a",
    "reference": "ana-3c9",                    // l'analyse, ou les réponses données
    "resume": "Python + FastAPI, pytest, ruff ; aucune CI détectée"
  },
  "entrees": [
    { "chemin": "AGENTS.md", "role": "instructions", "portee": "fichier",
      "empreinte": "sha256:…", "genere_le": "2026-09-20T09:12:00+00:00" },
    { "chemin": "CLAUDE.md", "role": "pont", "portee": "fichier", "empreinte": "sha256:…", "genere_le": "…" },
    { "chemin": ".agents/skills/lancer-les-tests/SKILL.md", "role": "skill", "portee": "fichier",
      "empreinte": "sha256:…", "genere_le": "…" },
    { "chemin": ".agents/skills/lancer-les-tests/scripts/tests.sh", "role": "script", "portee": "fichier",
      "empreinte": "sha256:…", "genere_le": "…" }
  ]
}
```

Trois propriétés à ne pas défaire :

- **Une entrée par fichier, jamais par skill.** « Régénérer sans écraser » se décide fichier par
  fichier : une personne corrige un `SKILL.md` et laisse ses scripts tranquilles, et l'inverse.
- **`empreinte` est l'empreinte de ce que Maestro a écrit**, pas celle du fichier aujourd'hui. C'est
  la différence entre les deux qui porte toute l'information.
- **`source` dit pourquoi**, pas seulement quoi. Un outillage qu'on relit six mois plus tard se juge
  contre l'analyse qui l'a recommandé ; sans elle, chaque skill est un fait sans raison.

### 4.2 Régénérer sans écraser : quatre cas, et un seul demande un geste

| État | Ce que Maestro fait |
| --- | --- |
| Le fichier existe, **absent du manifeste** | **Rien.** Maestro ne possède que ce qu'il a déclaré avoir écrit |
| Dans le manifeste, empreinte **identique** | Réécrit sans question : personne n'y a touché depuis |
| Dans le manifeste, empreinte **différente** | **Jamais écrasé.** La version neuve va dans `.maestro/outillage/refuses/`, et elle est nommée dans le rapport |
| Dans le manifeste, **absent du disque** | Réécrit. Une suppression n'est pas une modification à préserver, et c'est une régénération qui est demandée |

Deux corollaires :

- **Retirer l'entrée du manifeste est la façon de dire « ne régénère plus ça ».** C'est le seul
  geste que le dernier cas laisse à la personne, et il faut donc qu'il soit écrit quelque part :
  c'est ici.
- **Un fichier que l'analyse ne recommande plus n'est pas supprimé.** Son entrée quitte le
  manifeste, le fichier reste sur le disque, et le rapport le nomme. Maestro n'efface rien dans le
  projet de quelqu'un.

**Le cas du fichier que le projet possède déjà** — un `AGENTS.md` écrit avant Maestro — se traite
par la même règle, avec une portée plus fine : `"portee": "bloc"`. Maestro n'écrit alors que
l'intérieur d'un bloc délimité, et `empreinte` est celle du **bloc**, pas du fichier :

```markdown
<!-- BEGIN:maestro-outillage -->
…
<!-- END:maestro-outillage -->
```

Hors du bloc, rien n'est touché ; si le bloc a été modifié, il n'est pas réécrit. Le dépôt en porte
là encore le précédent, écrit par un tiers : [`apps/web/AGENTS.md`](../apps/web/AGENTS.md) est fait
d'un bloc `<!-- BEGIN:nextjs-agent-rules -->`.

### 4.3 Pourquoi `.maestro/outillage/`, et pas ailleurs

`.maestro/` est déjà le nom que Maestro se donne dans la racine d'un projet — l'atelier d'une tâche
y vit, en `.maestro/<tâche>/` ([docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md), #944) —
et le dépôt applique à lui-même la même règle : ce qu'on invite à relire va sous `.maestro/<domaine>/`.
Le manifeste est du même ordre : la comptabilité de Maestro, pas l'outillage du projet.

Deux conséquences qui tombent bien : le recensement d'une tâche **ne descend jamais dans
`.maestro/`**, donc le manifeste ne ressortira jamais comme un livrable de run ; et le mettre dans
`.agents/` l'aurait posé dans un dossier que trois clients balaient, où il aurait fini lu comme une
consigne.

⚠ **`outillage` devient un nom d'atelier réservé** : `.maestro/<tâche>/` est dérivé de
l'identifiant de la tâche (`maestro.sandbox.projet._slug`), et rien n'empêche aujourd'hui un
identifiant de se réduire à `outillage`. C'est à #1033 de l'écarter.

## 5. La frontière avec la configuration ambiante

### 5.1 La règle : un seul sens de lecture

[docs/34 §4.6](./34-decision-agent-cli-tiers-acp.md) a nommé la configuration ambiante comme « la
perte qu'on n'attendait pas », et [docs/37 §3](./37-decision-equipe-sur-mesure.md) l'a rangée parmi
ce qui **ne bouge pas**. Ce chantier ne la rouvre pas ; il la rend plus difficile à tenir, et c'est
pour ça qu'elle s'écrit ici en toutes lettres :

- **Maestro → le projet : il écrit.** L'outillage généré est un livrable comme un autre, sous le
  régime d'écriture de [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md).
- **Le projet → le runtime de Maestro : jamais de lui-même.** Ce qu'un agent de Maestro reçoit de
  l'outillage du projet lui est **transmis explicitement** (#1032), dérivé du manifeste, et borné à
  ce que le manifeste déclare.

Trois conséquences concrètes :

1. Un `AGENTS.md`, `CLAUDE.md` ou `GEMINI.md` trouvé dans un projet **n'est pas** le prompt d'un
   agent de Maestro. Son contenu peut lui être transmis ; il n'y entre pas tout seul.
2. Un `.mcp.json` ou un `.claude/settings.json` du projet **ne monte aucun serveur** dans le
   runtime : c'est ce que `strict_mcp_config=True` ferme déjà
   ([`maestro/providers/claude.py`](../maestro/providers/claude.py)).
3. **`allowed-tools:` dans un `SKILL.md` du projet est inerte.** La spécification le décrit comme
   des outils « pré-approuvés » ; chez nous une permission est déclarée par une personne, outil par
   outil ([docs/32](./32-decision-cran-orchestrateur.md)), et aucun fichier du projet ne peut en
   poser une. C'est le cas le plus tentant de la liste, parce que le champ existe, qu'il est facile
   à honorer, et que **Maestro aura peut-être écrit le fichier lui-même** — ce qui ne change rien :
   ce qui autorise un outil n'est pas l'origine du fichier, c'est le geste d'une personne.

### 5.2 Le piège est propre à ce chantier

Jusqu'ici, la configuration ambiante était un risque de branchement d'un agent tiers (docs/34).
Ici, elle devient un risque de **boucle** : Maestro écrit `CLAUDE.md` et `.agents/skills/` dans un
projet, et le runtime de Maestro **est** Claude Code, via l'Agent SDK. Le répertoire courant d'une
tâche est le projet — `cwd=workspace` — et sur un projet non versionné le workspace **est** la
racine (#839). Autrement dit : ce que Maestro génère se trouve exactement là où son propre CLI va
chercher sa configuration. La boucle n'est pas théorique, elle est le cas nominal.

### 5.3 Ce que la mesure dit aujourd'hui — et ce que #1032 doit fermer

Mesuré le 2026-09-20, sur `claude_agent_sdk` 0.2.128 :

- [`maestro/providers/claude.py`](../maestro/providers/claude.py) monte `ClaudeAgentOptions` **sans**
  `setting_sources` ;
- le SDK documente ce défaut : *« When `None`, all sources are loaded (matches CLI defaults). Pass
  `[]` to disable filesystem settings (SDK isolation mode). Must include `"project"` to load
  CLAUDE.md files. »* ;
- `setting_sources` n'apparaît **nulle part** dans le dépôt — zéro occurrence dans `maestro/`,
  `scripts/`, `core/`, `tests/` et `docs/`.

Donc la porte que `strict_mcp_config` ferme sur les serveurs MCP est **restée ouverte** sur les
réglages de fichiers et les fichiers d'instructions : un `CLAUDE.md` posé dans un projet entre
aujourd'hui dans le contexte d'un agent de Maestro qui y travaille. Il en va de même des skills, que
`skills=None` laisse aux défauts du CLI — « **not** "skills off" », dit la même docstring.

Le trou n'est pas né ici — un projet importé peut très bien porter son propre `CLAUDE.md` depuis
toujours. Ce que ce chantier change, c'est qu'il rend le cas **systématique** et que **Maestro en
devient l'auteur** : à partir de #1033, tout projet outillé porte un `CLAUDE.md` et des skills, et
c'est Maestro qui les y a mis. Un mécanisme qui se relit lui-même sans le savoir n'est plus un cas
limite, c'est le cas nominal — d'où une décision de cette note plutôt qu'un défaut à signaler
ailleurs.

**Ce qui incombe donc à #1032**, et qui est une condition de son critère et non un réglage de
confort : passer `setting_sources=[]`, et transmettre l'outillage du projet par le message de la
tâche, dérivé du manifeste. Sans cela, « Maestro injecte » et « le projet s'impose » ne se
distinguent plus sur le disque — les deux donnent le même contexte au même agent, et seule
l'intention les sépare. Et la vérification se fait **sur un run réel** : la mesure ci-dessus dit ce
que le contrat du SDK promet, pas ce qu'un CLI fait.

### 5.4 Ce que #1032 a fermé, et ce qui a été mesuré

Fait le 2026-09-20 (`claude_agent_sdk` 0.2.128, `claude-haiku-4-5`), sur des runs réels et sur un
projet **outillé à la main** — le manifeste, `AGENTS.md`, un pont et un skill écrits pour la mesure.
Chaque sonde donne à l'agent un contexte dont **un seul** fragment doit ressortir, et lui retire les
outils qui lui permettraient d'aller lire ce qu'on ne lui a pas donné.

| Ce qu'on mesure | Sans le lot | Avec `setting_sources=[]` et `skills=[]` |
| --- | --- | --- |
| Mot-témoin d'un `CLAUDE.md` posé dans le `cwd` | **ressort** dans la réponse | absent |
| Mot-témoin d'un `AGENTS.md` **déclaré au manifeste** | — | **ressort** : il est transmis |
| Mot-témoin du **corps** d'un `SKILL.md` déclaré | — | absent : seul l'index part |

La porte était donc bien ouverte, et elle est fermée. Trois conséquences que la mesure fixe, et qui
ne se supposent plus :

- **`AGENTS.md` du projet n'entre pas par le CLI**, même sans `CLAUDE.md` à côté : ce qui entre,
  c'est ce que Maestro transmet (`maestro.outillage.contexte`), dérivé du manifeste ;
- **la portée déclarée est la portée transmise** — `"portee": "bloc"` transmet le bloc, pas le
  fichier qui l'entoure. Même règle qu'à l'écriture (§4.2), appliquée dans l'autre sens : ce qui
  entoure le bloc n'a été écrit ni déclaré par personne. Qui veut le fichier entier le déclare en
  `"portee": "fichier"` ;
- **`allowed-tools:` est inerte, et c'est mesuré aussi.** Sonde : un skill du projet déclare
  `allowed-tools: Bash, Read, Write` et porte un script qui écrit un fichier-témoin ; l'agent, dont
  la politique soumet `Bash` à un arbitrage sans canal, essaie deux fois, est refusé deux fois, et
  le fichier-témoin n'existe pas à la sortie. Le champ est **signalé comme ignoré** plutôt que
  silencieusement sauté : l'inertie se voit au lieu de se supposer.

Reste vrai ce que §5.1 disait déjà, et que le lot n'a pas eu à changer : un `.mcp.json` du projet ne
monte rien (`strict_mcp_config`). Les deux verrous ne se remplacent pas — l'un ferme les serveurs
MCP, l'autre les réglages, les fichiers d'instructions et les skills.

## 6. Ce qui est écarté, et pourquoi

| Écarté | Pourquoi |
| --- | --- |
| Les skills dans `.claude/skills/` | Deux clients sur quatre, et un dossier au nom d'un éditeur pour un outillage qui se veut universel |
| Une **seconde copie** des skills pour Claude Code | Un skill est un arbre avec ses scripts : deux copies, c'est deux scripts, donc un correctif sur un seul (§3.3) |
| Un **lien symbolique** `.claude/skills` | Non vérifié côté Claude Code, et privilégié sous Windows — il échouerait sans le dire (§3.3) |
| Recopier `AGENTS.md` dans `CLAUDE.md` et `GEMINI.md` | Trois sources pour une instruction. Le pont d'une ligne fait le même travail sans dériver (§3.2) |
| Écrire `.gemini/settings.json` | C'est le réglage du client, pas un fichier du projet (§3.2) |
| Générer les trois formats de commande | Aucun format commun, et le skill rend déjà le service dans deux clients sur quatre (§3.5) |
| Le manifeste dans `.agents/` | Il finirait lu comme une consigne par les trois clients qui balaient ce dossier (§4.3) |
| Honorer `allowed-tools` d'un skill du projet | Une permission se déclare par une personne, jamais par un fichier — fût-il écrit par Maestro (§5.1) |

## 7. Ce qui rouvrirait la décision

Cette note tient à des faits extérieurs, datés du 2026-09-20. Trois les feraient bouger, et chacun
se **revérifie**, jamais ne se suppose :

- **Claude Code ajoute `.agents/skills/` à ses chemins de découverte.** La désignation de §3.3
  devient alors un filet et non plus la voie principale, et sa dégradation disparaît. C'est le
  changement le plus probable : trois clients sur quatre y sont déjà, et Codex a une demande ouverte
  dans l'autre sens (`.codex/skills`).
- **La spécification Agent Skills fixe un emplacement.** Elle ne le fait pas aujourd'hui ; si elle
  le faisait, elle l'emporterait sur ce choix, qui n'existe que pour combler son silence.
- **Un format de commande commun apparaît.** §3.5 redeviendrait un arbitrage plutôt qu'un constat.

Et une qui tient au dépôt : **la frontière de §5.3 se mesure, elle ne se décrète pas**. La mesure a
été faite (§5.4) et elle a confirmé §5.3 — un `CLAUDE.md` de projet entrait bien dans le contexte.
Elle reste à **rejouer** quand le SDK ou le CLI bougent : c'est le contrat d'une version qu'elle
constate, pas une propriété acquise.

## 8. Ce que les lots suivants en tiennent — et où ils l'appliquent

Les six lots sont **livrés** (2026-09-21). Le tableau dit ce que chacun a pris ici et par quel
code il l'applique : c'est le chemin que prend quelqu'un qui conteste une décision de cette note —
de la règle à la ligne qui l'exécute, et à la suite qui la garde.

| Lot | Ce qu'il prend ici | Où il l'applique |
| --- | --- | --- |
| #1030 — analyse d'un projet existant | Le dossier de scripts se **constate** (§3.4) ; ce qu'elle recommande remplit `source` du manifeste (§4.1) | [`maestro/outillage/analyse.py`](../maestro/outillage/analyse.py), `detection.py`, `modele.py`, `recommandation.py` ; `GET /api/projets/{id}/outillage/analyse` ([docs/05 §6.19](./05-interface-control-tower.md)) |
| #1031 — choix d'un projet neuf | Les choix remplissent `source` de la même façon ; l'arbre de §3.6 est la cible | [`maestro/outillage/questionnaire.py`](../maestro/outillage/questionnaire.py) — les réponses deviennent des `Constats`, et c'est le `recommander` de #1030 qui tranche : **pas deux chemins** |
| #1032 — les agents lisent l'outillage | §5 en entier, et `setting_sources=[]` comme condition (§5.3). **Fait**, mesuré en §5.4 | [`maestro/outillage/contexte.py`](../maestro/outillage/contexte.py) (ce qui est transmis), [`maestro/providers/claude.py`](../maestro/providers/claude.py) (ce qui n'entre pas), `maestro/agents/runtime.py` (le message de la tâche) |
| #1033 — génération | §3.6 pour l'arbre, §4.2 pour les quatre cas, et le nom d'atelier réservé (§4.3) | [`maestro/outillage/redaction.py`](../maestro/outillage/redaction.py) (le texte), `generation.py` (les quatre cas), `ecriture.py` (le régime de [docs/24 §2.4](./24-projets-locaux-et-poste-de-travail.md)) ; `POST …/outillage/generation` |
| #1034 — parcours de création | L'étape d'outillage écrit ce que §3.6 décrit, et reste reportable ([docs/37 §4.6](./37-decision-equipe-sur-mesure.md)) | `apps/web/components/projets/EtapeOutillage.tsx` ; `POST …/outillage/report`, et `outillage.a_faire` sur la fiche du projet |
| #1035 — tests + doc | Les faits de §2 se revérifient ; §7 dit lesquels | [`tests/test_outillage_analyse.py`](../tests/test_outillage_analyse.py), [`test_outillage_questionnaire.py`](../tests/test_outillage_questionnaire.py), [`test_outillage_generation.py`](../tests/test_outillage_generation.py), [`test_outillage_contexte.py`](../tests/test_outillage_contexte.py), [`test_outillage_skills_ref.py`](../tests/test_outillage_skills_ref.py) ; [docs/24 §2.6](./24-projets-locaux-et-poste-de-travail.md) et [docs/05 §6.19](./05-interface-control-tower.md) |

**Ce que les tests gardent de cette note, et pas ailleurs** (#1035) : les trois promesses de
l'analyse — lecture seule, bornes dites dans la réponse, aucune exécution — sont mesurées **sur les
appels**, jamais sur le résultat (un module qui écrirait puis effacerait passerait une comparaison
avant/après) ; la frontière de §5 est gardée des deux côtés, `contexte.py` pour ce qui est transmis
et un balayage de **chaque** `ClaudeAgentOptions` de `claude.py` pour ce qui n'entre pas ; et les
skills écrits sur le disque sont validés contre la spécification Agent Skills.

Cette dernière validation est un **équivalent** de `skills-ref validate` (§2.1) et non l'outil
lui-même : l'appeler demanderait une dépendance installée depuis le réseau dans un job qui n'en a
pas besoin, là où les règles tiennent en une douzaine de lignes citées de la spécification. Le
validateur est écrit **indépendamment** du lecteur de frontmatter du code de production — le
réutiliser reviendrait à valider un texte avec l'outil qui l'a écrit — et chacune de ses règles est
prouvée sur un échantillon fautif avant de servir de verdict. Si `skills-ref` devient installable
sans réseau dans le job, le remplacer est une amélioration, pas une correction.
