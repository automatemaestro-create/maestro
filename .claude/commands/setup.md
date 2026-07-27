---
description: Met en route un clone du dépôt — venv, .env, hooks git, dépendances web, serveurs MCP — et prend en charge les authentifications interactives
allowed-tools: Bash(bash:*), Bash(git:*), Bash(glab:*), Bash(npm:*), Bash(node:*), Bash(python:*), Bash(claude:*), Read, Edit
---

Amène ce clone à l'état « il ne reste qu'à renseigner le `.env` et lancer ». Le travail est fait
par le script `scripts/setup.sh`, **source unique** du parcours : ne
réimplémente jamais ses étapes à la main (créer un venv, copier le `.env`, poser un hook…),
appelle-le. Ton rôle est ce qu'un script ne peut pas faire seul : les authentifications
interactives, le diagnostic d'un échec, et l'accompagnement du remplissage du `.env`.

Le script est **idempotent** et **non destructif** (il n'écrase ni le `.env` ni le
`.claude/settings.local.json` existants) : le relancer est toujours sans risque.

1. **Diagnostic d'abord** : `bash scripts/setup.sh --check`. Cette forme n'écrit **rien** — elle
   dit seulement ce qui manque. Lis le rapport et annonce en une phrase ce qui va être monté.

2. **Prérequis manquants ?** Le script les **installe lui-même** (winget / brew / apt) — tu n'as
   rien à lancer à la main, et surtout **n'installe rien toi-même en parallèle** : tu doublerais le
   travail du script et tu masquerais son diagnostic. Préviens seulement l'utilisateur de ce qui va
   être installé, et qu'une invite d'**élévation** (UAC sous Windows) peut apparaître : elle vient
   du système, le script ne peut pas la supprimer.

3. **Applique** : `bash scripts/setup.sh`. Compte quelques minutes au premier passage
   (installation des outils manquants, puis `pip install -e ".[dev]"` et `npm ci` dans `apps/web`).
   Le script déroule toutes les étapes même si l'une échoue, puis rend un rapport ; un code de
   sortie non nul signale au moins une étape dure en échec.

   Trois situations que le script **signale sans pouvoir les résoudre** — relaie-les telles
   quelles, ne prétends pas que la machine est prête :
   - *« installé, mais pas encore dans le PATH »* — l'installation a réussi, mais le terminal
     courant garde son ancien environnement. Il faut **rouvrir le terminal** et relancer.
   - *« version X trouvée, minimum requis Y »* — le gestionnaire de paquets de la plateforme ne
     propose pas mieux (Debian stable plafonne Node à 18, par exemple). Le remède passe par une
     autre source, indiquée dans le message.
   - *« pas de gestionnaire de paquets utilisable ici »* — pas de winget/brew/apt, ou élévation
     refusée. Donne la commande d'installation, c'est le seul recours.

4. **Une étape en échec ?** Le script imprime le chemin de son log
   (`${TMPDIR:-/tmp}/maestro-setup/<étape>.log`). Ouvre-le, diagnostique, corrige si c'est
   corrigeable en local (dépendance système absente, lockfile désynchronisé, droits) puis relance
   uniquement l'étape concernée : `bash scripts/setup.sh --only <étape>`. Si ce n'est pas
   corrigeable sans décision humaine (installer un logiciel, changer de version de Python),
   explique-le et arrête-toi là plutôt que de contourner.

5. **Prends en charge le « Reste à faire »** du rapport — c'est le cœur de cette commande, ce que
   le script ne peut pas faire seul :

   - **`.env`** — c'est le seul fichier que l'utilisateur doit vraiment renseigner. Demande-lui
     quel **mode d'authentification Claude** il veut (`CLAUDE_AUTH_MODE`) :
     - `subscription` (défaut du POC, **aucune clé**) — vérifie qu'il est connecté (`claude`, ou
       `/login` dans une session interactive) ; en CI, c'est `claude setup-token` qui produit
       `CLAUDE_CODE_OAUTH_TOKEN` ;
     - `api_key` — il doit coller sa clé Anthropic dans `ANTHROPIC_API_KEY`.

     Tu peux éditer le `.env` pour poser `CLAUDE_AUTH_MODE`, mais **ne demande jamais une clé pour
     l'écrire toi-même** et ne recopie aucune valeur de secret dans un message, un commit ou un
     ticket. Si des clés manquent par rapport au gabarit (dérive signalée par le script), montre
     leur **nom** et le commentaire correspondant de `.env.example`, jamais leur valeur.

     Signale que le `.env` est la **source** de deux valeurs recopiées dans le bloc `env` de
     `.claude/settings.local.json` (`MAESTRO_CHROME_PROFILE`, `CLAUDE_CODE_OAUTH_TOKEN`) : les y
     modifier à la main ne sert à rien, la prochaine exécution du script les réalignera sur le
     `.env`. C'est voulu — c'est ce qui fait qu'une rotation de token se propage.

   - **`glab`** — plus rien à faire si `GITLAB_TOKEN` est renseigné dans le `.env` : le script
     s'authentifie tout seul (`glab auth login --stdin`, token jamais passé en ligne de commande).
     Ce n'est que **sans** token que le rapport renvoie vers `glab auth login` interactif — dans ce
     cas, dis à l'utilisateur de le lancer lui-même, ou de poser un PAT (scope `api`) dans le
     `.env`, ce qui est plus durable.

   - **Serveurs MCP** — rien à approuver : `enabledMcpjsonServers`, que le script écrit dans
     `.claude/settings.local.json`, **est** le registre d'approbation de Claude Code. Ne réclame
     pas une approbation manuelle, elle est déjà faite.

   - **Figma** — serveur HTTP en OAuth : **un clic** via `/mcp` dans une session Claude Code
     interactive, mis en cache ensuite, une fois par personne. C'est volontairement resté
     interactif : le `FIGMA_OAUTH_TOKEN` du `.env` sert la couche **produit**
     (`core/mcp/designer.json`), où aucun humain n'est là pour cliquer ; l'imposer aussi à Claude
     Code alourdirait la mise en route (ce token s'obtient via un client OAuth approuvé par Figma)
     et casserait le serveur pour qui n'en a pas. Rien à committer.

6. **Vérifie** : relance `bash scripts/setup.sh --check`. Tout doit ressortir en `OK` ou
   `DÉJÀ FAIT`, sauf ce qui dépend encore d'un geste humain non fait. Si l'utilisateur a renseigné
   son `.env`, confirme avec `maestro-check-env` (via le venv du dépôt :
   `.venv/Scripts/python.exe -m maestro.check_env` sous Windows, `.venv/bin/python …` sinon).

7. **Termine par un résumé court** : ce qui a été monté, ce qui reste à la charge de l'utilisateur
   (avec la commande exacte pour chaque point), et la suite — `bash scripts/controltower/start.sh`
   pour voir la Control Tower, `maestro-run "<objectif>"` pour dérouler une orchestration.

**Pas encore couvert** (lot 2, ticket #146) : Docker et le **runner CI de projet**. Tant que ce lot
n'est pas livré, ces deux points restent manuels — renvoie à `docs/10-workflow-git.md` §8, et
signale que sans runner en ligne les pipelines de MR restent `pending`.

**Garde-fous.** Cette commande ne touche ni à Git (pas de commit, pas de branche, pas de push) ni à
GitLab (ni statut, ni MR) : elle prépare une machine, rien d'autre. Elle n'écrit aucun secret dans
un fichier versionné. Elle **installe en revanche des logiciels** sur la machine (c'est son objet) :
si l'utilisateur veut s'en tenir au diagnostic, c'est `bash scripts/setup.sh --no-install`.
