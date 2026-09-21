# 41 — Maestro juge, il ne bride pas ; le produit se vérifie sur le réel

**Date :** 2026-09-21. **Instruite par :** `/idee` (#1013). **Consignée par :** #1169.
**Jalons :**
- *Rien de figé — Maestro comprend le projet, propose, vérifie* (neuf, échéance 2028-02-02) ;
- *Les scénarios de référence — le produit fait ce qu'on lui demande* (critère C5 ajouté).

**Tickets :**
- **#1155**, Maestro comprend n'importe quel projet. Lots, dans l'ordre : #1158, #1147, #1159, #1160, #1161, #1162.
- **#1163**, les sources d'un brief.
- **#1156**, le mode démo quitte le dépôt. Lots : #1164 à #1168.

---

## 0. Ce que ce document décide

Trois choses. La personne les a dites en toutes lettres ; les deux points qu'elle n'avait pas tranchés lui ont été demandés (§5).

1. **Maestro juge, il ne bride pas.** Aucun catalogue fermé, aucun défaut arbitraire, aucune supposition ne sert de **mécanisme principal** là où la situation réelle de l'utilisateur peut en sortir. Le modèle **comprend** : ce que le projet contient, ce que la personne en dit. Il **propose**, en justifiant chaque élément. Il **se laisse corriger en langage naturel**. Et l'**exécution vérifie** ce qu'il écrit (§3).
2. **Le mode démo quitte le dépôt.** Toute vérification **du produit** se joue sur la vraie stack : relecture visuelle, `/verify`, banc de mise en page, captures et films de présentation, bilans de jalon (§4).
3. **La barre est celle des produits IA professionnels**, jamais celle d'un POC. Maestro sera vendu. Il se compare aux agents de code et aux orchestrateurs du moment, pas à son propre historique.

## 1. D'où vient la demande

Le 2026-09-21, la personne crée un projet « p2 ». À l'étape d'outillage, la question 1 sur 6, *« Quelle sorte de projet est-ce ? »*, n'offre que quatre natures, et son projet n'est aucune d'elles (#1147). La réponse proposée d'abord, une option « Autre chose », est celle qu'elle refuse :

> « Maestro n'est pas un outil qui impose et bride. Ce n'est pas le but du projet. Rien ne doit être figé, supposé, fixe, statique… L'outil doit être intelligent. À partir de maintenant, supprime le mode démo. Tous les tests seront faits sur la version réelle de Maestro. Ne rabaisse pas tes critères en pensant que ce projet est un POC : il sera vendu, c'est un projet professionnel. Aligne-toi sur l'objectif de Maestro : un outil qui suit l'évolution des IA et de leurs usages. »

Elle juge l'existant *« très amateur »*, et ses fonctionnalités *« très basiques, voire pathétiques »*.

Cette demande prolonge celle du matin ([docs/40](./40-decision-rythme-et-scenarios-de-reference.md), #1153) : le produit se juge sur ce qu'on lui demande, joué avec le vrai modèle. L'analyse complète, avec l'inventaire, les deux cartes et chaque arbitrage, est le corps de #1169.

## 2. Où le produit bridait

Un inventaire du code produit (`maestro/`, `apps/web/`, `packages/shared/`, `core/`) aboutit à un constat central : **les premières minutes d'un projet — son outillage, puis son équipe — passaient par des fonctions pures appliquées à des tables fermées.** Le modèle en était écarté à dessein (`maestro/controltower/outillage.py`, en-tête), alors qu'il tient déjà le fil de conversation où ces questions se posent. Le fil lui-même suivait pourtant le bon patron : *« le modèle juge, l'utilisateur tranche »* (`orchestration.py`).

| Mécanisme | Où | Ce que vit l'utilisateur hors de la liste | Ticket |
| --- | --- | --- | --- |
| Six questions à options fermées : 4 natures, 4 langages plus « autre », tests déduits du langage, 3 forges, un plafond de 6 questions | `maestro/outillage/questionnaire.py` | **Bloqué.** Aucune réponse libre. Une correction tapée dans le fil n'est jamais enregistrée. « Autre » fait perdre le langage | #1147 |
| Commandes par langage (uv, ruff, npm, `python -m app`) écrites comme « convention » | `questionnaire.py`, rédaction | **Faux en silence.** Rien n'est exécuté, sur un projet sous poetry, pnpm ou bun | #1160 |
| Détection par extensions et noms de fichiers | `maestro/outillage/detection.py` | **Dégradé.** Pas de commande pour .NET, Swift, Deno, Bazel, `justfile`… ; skills écartés, puis ni QA ni DevOps. Et à l'écran, *« un élément se garde ou se retire, il ne se ressaisit pas »* | #1158, #1161 |
| Cinq gabarits de rôle retenus par des règles fixes | `maestro/equipe/gabarits.py` | Mobile, ML, sécurité, documentation : **jamais proposés**. Le rôle données jamais sur un projet neuf. Le prompt du fil décrit une équipe figée | #1159 |
| Plancher de 3 tâches, plan jamais réparé | `maestro/orchestrator/` | Un objectif simple gonflé ; un titre de plus de 120 caractères qui fait échouer le run | #1149 |
| Six formats de source seulement | `maestro/sources/extraction.py` | `.json`, `.yaml`, `.csv`, code et images écartés du brief | #1163 |

**Différé**, parce que ces points brident moins et que chaque ticket qui touchera ces modules appliquera la règle :
- git, seul gestionnaire de versions reconnu. Un dépôt Mercurial ou SVN est écrit en place, sans diff à valider : c'est une **perte de sûreté silencieuse**, à ouvrir dès qu'un vrai dépôt la confirme ;
- deux tours de clarification figés ;
- les seuils de l'équipe ;
- les bornes de l'analyse ;
- les ponts vers deux clients seulement ;
- les réponses toujours en français ;
- l'image du bac à sable limitée à Node et Python.

## 3. Le principe, et comment il se tient

**Comprendre, proposer, se laisser corriger, vérifier.** C'est le patron des produits professionnels actuels, vérifié à la source :
- **Claude Code**, `/init` : *« Claude analyzes your codebase and creates a file with build commands, test instructions, and project conventions it discovers »*. Le parcours récent explore le projet par un sous-agent, *« fills in gaps via follow-up questions, and presents a reviewable proposal before writing any files »* ([source](https://code.claude.com/docs/en/memory)).
- **GitHub Copilot**, agent cloud : la découverte par le modèle *« can be slow and unreliable, given the non-deterministic nature of large language models »*. D'où des étapes d'installation qui s'exécutent et se vérifient ([source](https://docs.github.com/en/copilot/how-tos/copilot-on-github/customize-copilot/customize-cloud-agent/customize-the-agent-environment)).

La leçon tient en une ligne : **le modèle propose, l'exécution tranche, et ce qui est vérifié s'écrit.** C'est ce qui concilie la règle du dépôt, *« ce qui n'est pas vérifié n'est pas cité »*, avec l'intelligence demandée. Avant, on ne citait que ce qu'une table connaissait. Désormais, on cite ce qu'on a lu ou exécuté, et on nomme ce qui ne l'a pas été.

**Un garde-fou n'est pas une bride.** Le principe ne touche à rien de ce qui protège :
- les racines interdites et l'exclusion des secrets (`.env`, `**/secrets/**`) ;
- la frontière d'écriture ;
- la branche et le diff à valider sur un projet versionné ;
- l'arbitrage des actes irréversibles ;
- la liste d'autorisation MCP avec admission humaine ;
- le décideur humain par défaut ;
- les contrats machine.

Une bride ferme la porte à une situation légitime. Un garde-fou la ferme à un dommage.

**Les questions à se poser en concevant**, sur toute capacité. Ce ne sont pas des cases à cocher dans un gabarit :
- que vit un utilisateur **hors de nos hypothèses** ?
- le modèle est-il **là où il faut comprendre** ?
- ce que le produit écrit ou affirme est-il **vérifié en l'exécutant**, ou nommé comme non vérifié ?
- la personne peut-elle **corriger avec ses mots** ?
- à quel **produit professionnel** se compare-t-on ?

## 4. Le mode démo quitte le dépôt

**Ce qu'il était.**
- `maestro/controltower/demo.py` (1 718 lignes) et `fixtures.py` (263 lignes).
- La commande `maestro-controltower-demo` et `start.sh --demo`/`--scenario`.
- Un `create_app` monté sur un bus en mémoire et un répondeur scénarisé, avec cinq scénarios : `nominal`, `vide`, `erreur`, `charge`, `decomposition`.
- Deux routes qui n'existaient que pour lui.

**Aucun code du chemin réel ne l'importe.** Ses consommateurs sont l'outillage : relecture visuelle, captures et films de présentation, quatre skills, environ sept commandes, les gabarits de ticket et la coque Electron.

**Pourquoi il part.** Un scénario factice montre ce qu'on a scénarisé, pas ce que le produit fait. Il peuplait parfaitement les écrans, et une régression comme #1146 passait dessous.

**Remplacer, puis supprimer.** Supprimer d'abord casserait la relecture visuelle que `/ticket-finish` joue sur tout ticket d'écran. D'où l'ordre de #1156 :
1. une stack réelle isolée par copie de travail, et l'état d'un vrai passage qui se rouvre (#1164) ;
2. en parallèle : la relecture visuelle (#1165), les captures et les films (#1166), les skills et les textes (#1167) ;
3. la suppression (#1168).

D'ici là, aucune session ne s'appuie plus sur la démo pour juger.

**D'où viennent les états**, une fois la démo partie :

| État | Source réelle |
| --- | --- |
| vide | une stack réelle neuve |
| erreur | une vraie panne : magasin coupé (l'API rend 500), API coupée (« injoignable », un autre état à l'écran, #996) |
| peuplé, charge | **l'état réel laissé par le dernier passage du banc des scénarios** (#1148), servi par l'API réelle, rejoué à la demande quand il date |
| ce que le réel ne sait pas produire | nommé **non couvert** dans la note de relecture, jamais fabriqué |

**`maestro-demo` part aussi.** C'est un vrai run, mais sur un objectif figé (le mini-CRM de la Phase 0), avec le verdict d'un POC. Le banc #1148 en est le successeur professionnel. Les démos de fin de phase ([docs/11](./11-demo-poc.md), 12, 13, 23) restent : ce sont des archives.

## 5. Ce qui a été demandé, et répondu

| Question | Réponse de la personne |
| --- | --- |
| « Tous les tests sur la version réelle » vaut-il pour les tests unitaires ? | **Non : les vérifications du produit seulement.** Les tests unitaires (pytest, Vitest) gardent leurs doubles, parce qu'ils vérifient le code. |
| D'où vient l'écran peuplé de la relecture et des captures ? | **L'état du dernier vrai passage du banc**, rejoué à la demande. Rejouer un scénario à chaque vérification a été écarté : payé en modèle et en minutes à chaque clôture. |

## 6. Ce qui est renversé

La personne a dit vouloir chacun de ces renversements.

| Décision | Où elle vivait | Ce qui la remplace | Ticket |
| --- | --- | --- | --- |
| Le questionnaire d'un projet neuf, **borné, recommandé, sans modèle** : *« le geste ne repasse pas par le juge »* | [docs/38 §8](./38-decision-outillage-universel-du-projet.md) (#1031), `questionnaire.py` | Le projet se décrit avec les mots de la personne. Le modèle en tire les constats, ne pose que les questions utiles, génère ses options et accepte toujours une réponse libre. Seul le `recommander` commun aux deux chemins ne bouge pas | #1147 |
| L'analyse par **tables de détection** | [docs/38 §2 et §8](./38-decision-outillage-universel-du-projet.md) (#1030), `detection.py` | Le modèle lit le projet. Les tables deviennent des indices, jamais un plafond | #1158 |
| Des **commandes par langage** écrites sans exécution | `questionnaire.py`, rédaction (#1031, #1033) | Chaque commande est jouée avant d'être écrite, et son verdict est gardé au manifeste | #1160 |
| *« Un élément d'outillage se garde ou se retire, il ne se ressaisit pas »* | `EtapeOutillage.tsx` (#1034) | L'outillage se corrige en langage naturel, puis se revérifie | #1161 |
| L'équipe **dérivée par des règles** sur cinq gabarits | [docs/37 §2.1](./37-decision-equipe-sur-mesure.md) (#1039), `gabarits.py` | Le modèle propose les rôles dont le projet a besoin. Les gabarits restent une matière, comme docs/37 §7 l'avait prévu | #1159 |
| La démo **gardée en option** quand le réel est devenu le défaut | [docs/07](./07-guide-de-demarrage.md) (#186), `start.sh` | Retirée | #1168 |
| Les **états limites ouverts dans la démo**, et la relecture qui les regarde | [docs/30 §5.8](./30-cible-visuelle-control-tower.md) (#978), gabarits de ticket | Stack neuve, vraie panne, état laissé par le banc (§4) | #1165, #1167 |
| Les **films de présentation** sur la stack de démo | [docs/36](./36-outillage-du-design.md), `scripts/presentation/` (#545) | La vraie stack | #1166 |
| Le scénario de décomposition, les **fixtures servies par la démo** | `demo.py` (#1109, #1140), [docs/05 §6](./05-interface-control-tower.md) (#183) | Retirés, avec les deux routes qui rendaient 501 en réel | #1168 |
| `maestro-demo`, la démo du POC | [docs/11](./11-demo-poc.md) (#10) | Le banc des scénarios de référence (#1148) | #1168 |

## 7. Ce qui ne bouge pas

- **Les garde-fous** listés au §3.
- **« Ce qui n'est pas vérifié n'est pas cité »**, qui gagne un moyen de vérifier : l'exécution.
- **Les doubles des tests unitaires** (§5).
- **Un seul chemin de recommandation** pour un projet importé et un projet neuf. C'est ce que #1031 avait de juste, et il le garde.
- **L'analyse n'exécute rien** : on lit pendant l'analyse ; l'exécution vient au moment d'écrire, sous validation (#1160).
- [docs/40](./40-decision-rythme-et-scenarios-de-reference.md) : un ticket porte ses tests (#1150) ; la cérémonie de conception est réservée aux tickets qui décident d'un écran (#1151) ; le gel du rail outillage. Les lots de #1156 qui écrivent sous `.claude/` **retirent** une dépendance, comme #1150 à #1152 : c'est la même exception au gel.
- **Les archives** : docs/11, 12, 13 et 23, `docs/bilans/`, `docs/retex/`, les présentations commitées.

## 8. Ce qui rouvrirait la décision

- **Une étape pilotée par le modèle qui se révèle instable sur le banc.** La réponse n'est pas de revenir à une table : c'est de mieux vérifier. Une table peut revenir comme **indice** pour accélérer ; elle ne redevient jamais un plafond.
- **Le coût.** Le modèle entre là où il n'était pas : analyse, questions, équipe, vérification. Les rapports du banc donnent le chiffre. Un coût jugé trop haut se règle par la manière d'appeler le modèle, pas en rendant le produit moins intelligent.
- **Un état d'écran que le réel ne sait pas produire, et qui compte.** On le rend productible par un vrai scénario du banc. On ne rouvre pas un mode factice.
