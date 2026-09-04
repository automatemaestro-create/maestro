# Retours d'expérience utilisateur — `docs/retex/`

Un fichier par retex, `<AAAA-MM-JJ>-<slug>.md`, produit par la commande **`/retex-utilisateur
[objectif]`** (#853, [docs/10 §3.4](../10-workflow-git.md)). Chaque rapport est ce qu'une personne
qui **découvre Maestro** aurait vu en se servant de la Control Tower **réelle** — jamais `--demo` —
par sa seule interface, du poste vide jusqu'au livrable exécuté.

## À quoi ça sert

Les deux revues d'usage qui ont fait naître la vague front (2026-08-05) et « Le run, objet de
premier plan » (2026-08-24, [docs/29](../29-decision-run-objet-de-premier-plan.md)) ont été faites à la
main, par l'humain, et **aucune n'est rejouable**. Un retex est ce geste, rejouable — à chaque fin
de milestone produit, ou avant d'empaqueter (Phase 9). Il ne remplace ni le bilan d'un jalon
(`/milestone-bilan`, qui exerce des **critères de sortie**), ni la vérification de câblage
(`/verify`), ni le banc de mise en page (`/banc-mise-en-page`) : ces trois-là savent ce qu'ils
cherchent, le retex regarde avec les yeux de quelqu'un qui **ne sait pas**.

Ce qui en sort est **proposé, jamais créé** : un rapport propose un milestone et des tickets, une
personne décide de les ouvrir (`/ticket-create`). La commande n'écrit rien côté forge.

## Ce qu'un rapport contient

1. **Contexte** — sha d'`origin/main`, date, stack (ports, mode réel), et le **constat de départ** :
   le poste était-il vide (`PosteVide`) ?
2. **Parcours, écran par écran** — les entrées de `PAGES` (`apps/web/lib/navigation.ts`), une par
   une, avec capture : à quoi sert l'écran, ce qu'on a essayé d'y faire, ce qui marche, ce qui
   bloque, ce qui surprend, le rendu ; puis les gestes transverses (projet actif, thème,
   notifications, visite guidée, assistant).
3. **Le run** — l'objectif et pourquoi celui-là, le brief, les questions et validations, la durée,
   le coût **lu à l'écran Coûts**, et le **livrable exécuté** : fonctionne ou non, avec ce qui a été vu.
4. **L'objectif de Maestro, confronté** à O1–O4 de [docs/00 §2.1](../00-cahier-des-charges.md).
5. **Constats classés** — bloquant / gênant / cosmétique —, chacun **confronté au backlog** (déjà
   couvert par #n, trou, ou renverse une décision écrite — la méthode de docs/29 §2).
6. **Proposition** de milestone et de tickets (titre, `type::`, `prio::`).
7. **Ce que ce retex n'est pas** — une phrase.

Le rapport **n'est pas commité par la commande** : le versionner est une décision humaine, comme
pour les bilans (`docs/bilans/`) et les présentations (`docs/presentations/`).

## Le prérequis : un poste vide

Un retex part d'un poste **vide**, comme un premier démarrage. Aucun geste ne le vidait jusqu'à
#853 : `start.sh --stop` solde les runs sans rien effacer, et aucune route ne supprime une
exécution. D'où le verbe de purge, joué par la commande **après un « oui » explicite** :

```
bash scripts/controltower/start.sh --stop
.venv/Scripts/python.exe -m maestro.controltower.purge --check      # ce qui partirait, sans rien écrire
.venv/Scripts/python.exe -m maestro.controltower.purge [--projets]  # le réel, mêmes comptes
bash scripts/controltower/start.sh --no-browser
```

Il vide l'**état d'exécution** — journal durable, battements, file de tâches, boîtes, conversations,
téléversements ; avec `--projets`, les déclarations de projets et **jamais** un dossier de projet
sur le disque — et **ne touche jamais à la configuration** (agents, playbooks, surcharges,
capacités, permissions, secrets, MCP). Il **refuse** (code `3`) tant que l'API répond ou qu'un
hôte détaché bat encore, en nommant le geste préalable. Ses clés et ses dossiers viennent des
constantes Python, jamais recopiés (leçon de #830). Gardé par
[`tests/test_retex_utilisateur.py`](../../tests/test_retex_utilisateur.py).
