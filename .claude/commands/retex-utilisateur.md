---
description: Se servir de la Control Tower réelle comme un utilisateur qui découvre Maestro — du poste vide au livrable exécuté — et en rendre un retour d'expérience exploitable en tickets
argument-hint: "[objectif]  (l'objectif du run à composer dans le chat — sans argument, la session choisit un projet léger avec une interface, et dit pourquoi)"
allowed-tools: Bash(bash:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*), Bash(git:*), Read, Write, mcp__chrome-maestro
---

<!-- `mcp__chrome-maestro` est déclaré, et c'est voulu : c'est SON navigateur. À la différence de
     `/milestone-bilan`, cette commande n'a aucun exécutant à qui déléguer le regard — ce qu'elle
     produit est précisément ce qu'un script ne sait pas faire : regarder sans savoir ce qu'on
     cherche. `WebSearch` / `WebFetch` n'y ont rien à faire (règle de #792) : un retour
     d'expérience n'est pas une veille. -->

Tu vas **te servir de Maestro comme un utilisateur qui le découvre** — par sa seule interface, la
Control Tower **réelle** (jamais `--demo`), pilotée au navigateur — du poste vide jusqu'au
livrable exécuté, et tu en rendras un **retour d'expérience** écrit, classé et confronté au backlog,
exploitable en tickets.

Commande **de supervision** pour le cycle de vie : tu ne touches à **rien** côté forge — ni Status,
ni PR, ni merge, ni ticket, ni milestone. Elle a une particularité qui doit se dire : elle **écrit
l'état de la Control Tower**, une fois, par sa purge (étape 1) — c'est ce qui la distingue des
autres commandes de supervision, et c'est un geste **confirmé**, jamais joué d'office.

## Ce que cette commande est, et ce qu'elle n'est pas

Trois commandes regardent déjà la Control Tower, et chacune **sait ce qu'elle cherche** :
`/milestone-bilan` exerce les critères de sortie d'un jalon et propose un verdict de bouclage,
`/verify` vérifie le câblage de bout en bout, `/banc-mise-en-page` mesure la géométrie. Celle-ci
regarde avec les yeux de quelqu'un qui **ne sait pas** — c'est sa seule valeur, et c'est pourquoi
elle ne délègue à aucune des trois.

Les deux revues d'usage qui ont fait naître la vague front (2026-08-05) et « Le run, objet de
premier plan » (2026-08-24, [docs/29](../../docs/29-decision-run-objet-de-premier-plan.md)) ont été faites
à la main, par l'humain, et aucune n'est rejouable. Cette commande rend ce geste **rejouable** — à
chaque fin de milestone produit, ou avant d'empaqueter (Phase 9). Ce qu'elle rend est **proposé,
jamais créé** : le partage de `/run-audit` et de `/milestone-bilan` — ce qui est automatique est la
détection du manque, jamais le verdict.

---

1. **Prérequis — le poste vide.** Un utilisateur qui découvre Maestro ne voit aucun historique ;
   toi non plus. Trois gestes, dans cet ordre, **avant** d'ouvrir le navigateur :

   - **Arrête la stack**, ce qui solde les runs en vol et éteint les hôtes détachés avec leur
     descendance (#486, #700) :
     ```
     bash scripts/controltower/start.sh --stop
     ```
   - **Purge l'état d'exécution** — journal durable, battements, file, boîtes, conversations,
     téléversements. D'abord ce qui partirait, sans rien écrire :
     ```
     .venv/Scripts/python.exe -m maestro.controltower.purge --check
     ```
     (`.venv/bin/python` sous Unix ; `--projets` retire en plus les **déclarations** de projets —
     jamais un dossier de projet sur le disque — à ajouter si l'on veut aussi le sélecteur de
     projet vide.) Relaie les comptes, puis **demande un « oui » explicite** : la purge est
     destructive, elle se confirme comme le feu vert de `/orchestrate`. Sans « oui », arrête-toi
     là — le retex ne se joue pas sur un poste qui n'est pas vide. Sur « oui », joue le même
     appel sans `--check`. Un code `3` est un **refus** (l'API répond encore, ou un hôte détaché
     vit) : le message nomme le geste préalable, rejoue-le et recommence — ne contourne jamais.
   - **Relance la stack réelle**, sans fenêtre (tu pilotes le navigateur toi-même) :
     ```
     bash scripts/controltower/start.sh --no-browser
     ```
     Redis est requis. Si le script s'arrête faute de Redis, relaie le geste exact qu'il donne
     (`docker compose … up -d redis`) et **ne retombe jamais en douce sur `--demo`** (règle du
     skill `control-tower`) : des données factices prises pour la réalité rendraient un retex
     faux. Depuis un worktree, passe les ports que `worktree.sh ensure` a annoncés
     (`MAESTRO_PORT_API` / `MAESTRO_PORT_UI`) à `start.sh` **et** à la purge, qui sonde l'API sur
     ce même port.

   Ouvre alors l'UI dans le navigateur (`mcp__chrome-maestro`, `browser_navigate`). L'écran doit
   rendre le **poste vide** (`PosteVide`) : c'est le constat de départ, à noter dans le rapport
   avec le sha d'`origin/main` (`git rev-parse --short origin/main`), la date et la stack.

2. **Posture.** À partir d'ici et jusqu'à l'étape 5, **tu n'es plus une session de
   développement** : aucune commande du dépôt, aucun appel direct à l'API, aucune lecture du code
   pour comprendre un écran. Ce que l'interface n'explique pas est un **constat**, pas une
   question à aller résoudre dans `apps/web/`. Le navigateur est ton seul outil. Les deux seules
   exceptions sont le prérequis (avant) et l'écriture du rapport (après).

3. **Parcours des écrans.** Les entrées du menu, une par une — la liste ci-dessous est celle de
   `PAGES` dans `apps/web/lib/navigation.ts` (le menu, puis les pages servies hors menu), et
   `tests/test_retex_utilisateur.py` la confronte au fichier : un écran ajouté qui n'y figure pas
   fait rougir la suite.

   <!-- ecrans:debut -->
   | chemin | écran |
   | --- | --- |
   | `/` | Tableau de bord |
   | `/chat` | Chat |
   | `/runs` | Runs |
   | `/agents` | Agents |
   | `/integrations` | Intégrations |
   | `/couts` | Coûts & analytics |
   | `/validations` | Validations |
   | `/journal` | Journal |
   | `/parametres` | Paramètres |
   | `/projets` | Projets (hors menu — par le sélecteur de projet du shell) |
   <!-- ecrans:fin -->

   Pour chaque écran, dans l'ordre : **à quoi il sert** (est-ce évident sans l'avoir lu ailleurs ?),
   **ce que tu essaies d'y faire**, ce qui marche, ce qui bloque, ce qui surprend, et le **rendu**
   — langage visuel, sobriété, lisibilité, clavier — à une ou deux largeurs de fenêtre
   (`browser_resize`). Prends une capture par écran (`browser_take_screenshot`) : elle est la pièce
   d'un constat. Puis les **gestes transverses** : projet actif (sélecteur du shell), thème,
   notifications, visite guidée, assistant.

4. **Le run.** Le seul critère qui dise si l'objectif de Maestro est tenu — un objectif → une
   équipe d'agents → un livrable, l'utilisateur chef d'orchestre et non opérateur
   ([docs/00 §1.2](../../docs/00-cahier-des-charges.md)) — est un livrable qui **fonctionne**. Depuis l'UI et
   seulement depuis l'UI :

   - **crée un projet** — dossier neuf, non versionné : il se remplit dans sa racine, sans copie
     intermédiaire (#839) ;
   - **compose dans le chat** — la seule porte d'entrée depuis #470 — un objectif de **projet
     léger avec une interface** (web, bureau ou autre). `$ARGUMENTS` l'impose s'il est renseigné ;
     sinon tu le choisis et tu **dis pourquoi** dans le rapport ;
   - réponds aux **questions de clarification**, valide le **brief**, puis **suis le run** — Runs,
     Kanban, pipeline, journal, coûts — et **décide les validations** qu'il te demande. Va au bout.
     Un run qui attend quelqu'un est indiscernable d'un run qui travaille sur les compteurs
     habituels : regarde aussi les validations en attente ;
   - **ouvre le livrable et exécute-le** : l'interface produite démarre-t-elle, fait-elle ce que
     l'objectif demandait ? Réponds par oui ou non, avec ce que tu as vu. C'est ce qu'aucune revue
     d'usage n'avait fait, et c'est le constat qui compte le plus.

5. **Le rapport** — `docs/retex/<date>-<slug>.md` (`<date>` au format `AAAA-MM-JJ`, `<slug>` tiré
   de l'objectif ; crée le dossier s'il manque — [docs/retex/README.md](../../docs/retex/README.md)
   dit ce qu'un rapport contient et à quoi il sert). Le plan, dans cet ordre :

   1. **Contexte** — sha d'`origin/main`, date, stack (ports, mode réel), le constat de départ
      (poste vide ou non).
   2. **Parcours, écran par écran** — les dix écrans de l'étape 3 puis les gestes transverses,
      chacun avec sa capture et ses constats.
   3. **Le run** — objectif (et pourquoi celui-là), brief, questions posées, validations
      décidées, durée, coût **lu à l'écran Coûts** (jamais recalculé), livrable : fonctionne ou
      non, et ce que tu as vu en l'exécutant.
   4. **L'objectif de Maestro, confronté** — O1 à O4 de [docs/00 §2.1](../../docs/00-cahier-des-charges.md)
      (spécialisation, autonomie, assignation automatique, parallélisme) : ce que le run a montré
      pour chacun, ou n'a pas permis de voir.
   5. **Constats classés** — **bloquant** / **gênant** / **cosmétique** —, chacun **confronté au
      backlog** par la méthode de [docs/29 §2](../../docs/29-decision-run-objet-de-premier-plan.md) :
      *déjà couvert par #n* (nomme-le), *trou*, ou *renverse une décision écrite* (nomme-la). Lis le
      backlog pour ça (`bash scripts/gitlab/lib.sh backlog-table`), c'est une lecture ; ne crée
      rien.
   6. **Proposition** — un milestone et des tickets (titre, `type::`, `prio::`), par constat non
      couvert. **Proposée, jamais créée** : la décision est humaine, comme pour
      `/milestone-verdict`.
   7. **Ce que ce retex n'est pas** — un rappel d'une phrase : ni bilan de jalon, ni vérification
      de câblage, ni banc de mise en page.

   Le rapport **n'est pas commité** : c'est une décision humaine, comme pour `/milestone-bilan`.

6. **Termine.** Ferme le navigateur (`browser_close`) — un profil Chrome n'accepte qu'un
   consommateur à la fois, et une fenêtre laissée ouverte bloque le prochain outil. Laisse la stack
   tourner : l'utilisateur peut vouloir regarder ce que tu as vu (`start.sh --stop` pour l'arrêter).

   Puis un **résumé court** : le constat de départ, le run (objectif, coût lu, livrable :
   fonctionne ou non), le compte de constats par gravité, combien sont déjà couverts et combien sont
   des trous, le chemin du rapport — et la question : quels tickets de la proposition ouvrir ?
   Nomme `/ticket-create` pour qui voudra les créer ; **ne le joue pas**.

## Ce que la commande ne sait pas

À dire dans le résumé quand le cas se présente, plutôt que de le laisser deviner :

- **Elle juge le produit d'aujourd'hui**, pas celui d'une phase : un défaut vu est un défaut
  maintenant, et la date du rapport est ce qui le situe.
- **Un run coûte du quota et du temps réel** — plusieurs dizaines de minutes et quelques dollars,
  lus à l'écran Coûts. Ce n'est pas un défaut de la commande, c'est ce qu'un utilisateur paie ;
  le rapport le dit en chiffres.
- **Elle ne relance jamais un run**, ne merge rien, ne crée ni ticket ni milestone, et ne rejoue
  pas la veille de conception (`/design-veille` est un autre geste, sur une autre question).
- **Le premier rapport est un ticket à part** (#854) : cette commande le rend possible, elle ne le
  remplace pas.

Ne lance aucune commande d'écriture côté forge (`gh issue create`, `gh issue edit`,
`gh pr create`, `set-workflow`, `issue-note`, `log-time`…), aucun `git commit` / `git push`, et ne
ferme ni ne crée aucun milestone : cette commande observe, se sert du produit, et produit un fichier.
