---
description: Où en est le projet — forge, jalons, roadmap, runs — et le plan de travail que cet état appelle
argument-hint: "[produit|outillage]  (défaut : les deux rails)"
allowed-tools: Bash(bash:*), Bash(gh:*), Bash(git:*), Read, Grep, Glob
---

<!-- Commande de SUPERVISION, en lecture seule, et interactive seulement : elle se termine par une
     recommandation, et un run n'a personne à qui la faire. Elle n'écrit rien — ni forge, ni
     fichier de travail — et ne crée aucun ticket : le plan NOMME la commande qui l'exécute, il ne
     la joue pas. Aucune écriture de forge n'est recopiée ici : c'est ce qui a permis de changer
     deux fois de support d'état sans qu'aucun prompt bouge. -->

Tu réponds à la question qu'on pose en rouvrant le dépôt après quelques jours : **où en est
Maestro, et que faut-il faire ensuite ?** En deux temps — un **état des lieux** sur pièces, puis le
**plan de travail** que cet état appelle.

**Ce que cette commande répond** : qu'est-ce qui bloque aujourd'hui, qu'est-ce qui avance, qu'est-ce
qui dort ; où en sont les jalons actifs de chaque rail ; ce que la roadmap annonce contre ce qui est
réellement fermé ; ce qui attend une décision humaine ; et, de tout ça, **quoi prendre en premier**.

**Ce qu'elle ne répond pas** : *le détail du backlog* (`/backlog`, qui rend chaque ticket de chaque
état) ; *ce livrable tient-il ?* (`/milestone-bilan`, seul à exercer un livrable et à rendre un
verdict de bouclage) ; *où est passé le temps d'un run ?* (`/run-audit`) ; *cette PR est-elle
bonne ?* (`/mr-review`). Elle **situe** et **oriente** — elle ne démontre rien et ne tranche rien.

Quatre règles gouvernent tout le reste :

- **Lecture seule, sans exception.** Aucun `set-workflow`, `gh issue create`/`edit`, `git push`,
  `merge-mr`, `milestone-*` en écriture, `journal.sh gc`. Tu ne crées **aucun** ticket, même sur un
  « go » : le plan nomme la commande qui le fera (`/idee`, `/ticket-create`, `/mr-fix`,
  `/milestone-bilan`), et s'arrête là. C'est le partage de #562 — ce qui est automatique est la
  **détection du manque**, jamais le verdict.
- **Chaque chiffre vient d'un verbe, jamais d'une analyse recopiée** (#310). Les verbes ci-dessous
  portent déjà les seuils, les jointures et les cas limites ; une analyse réécrite dans un prompt
  fige l'outil au jour où elle a été écrite. Tu lis leur sortie, tu ne la refais pas.
- **N'invente aucun chiffre, et nomme ce que tu n'as pas pu lire.** Un verbe muet, un code non nul,
  une section sautée : dis-le à sa place dans le compte rendu. Un état des lieux qui tait ses trous
  vaut moins que pas d'état des lieux — c'est sur lui qu'on décide de la semaine.
- **Aucun lexique pour juger** (#746). « Bloqué », « urgent », « abandonné » se jugent en lisant un
  ticket, une PR, une échéance — jamais en comptant des mots-clés dans un titre.

⚠ **Joue-la depuis le clone principal.** `status.sh` lit `.maestro/orchestrate` **du répertoire d'où
on l'appelle** : depuis un worktree il ne verra aucun run et le dira, ce qui n'est pas la même chose
qu'« aucun run n'a tourné ». Si tu es relocalisé dans un worktree, saute l'étape 4 en le disant.

---

1. **Pré-requis, et le cadre de la lecture.** `bash scripts/gitlab/lib.sh require` — arrête-toi et
   relaie son message si la forge n'est pas authentifiée : sans elle, tout ce qui suit est muet.

   `$ARGUMENTS` vaut `produit`, `outillage`, ou rien. Il ne restreint **pas la lecture** — l'état
   des lieux est toujours celui du projet entier, les deux rails se tenant l'un l'autre — mais il
   **pondère le plan** de l'étape 6 : ce qui relève de l'autre rail y descend d'un cran au lieu d'en
   disparaître. Dis en une ligne quel rail tu as privilégié, et qu'il n'a rien caché.

2. **La forge — un seul passage, cinq lectures.** Joue-les dans cet ordre, une par une (une session
   relocalisée refuse une boucle, #520) :

   ```
   bash scripts/gitlab/lib.sh backlog-table
   bash scripts/gitlab/lib.sh review-queue
   bash scripts/gitlab/lib.sh milestones
   bash scripts/gitlab/lib.sh milestones-a-boucler
   bash scripts/gitlab/lib.sh reconcile-en-cours
   ```

   Ce que chacune rend, et ce qu'il faut en retenir :
   - **`backlog-table`** — TSV, en-tête préfixé `#` : `iid`, `statut` (le libellé du cycle de vie,
     `-` si le ticket n'a **aucun** état), `prio`, `agent`, `assigne` (`-` = libre), `titre`. Les
     tickets ouverts par défaut ; `backlog-table all` ajoute les fermés, à ne demander que si une
     question précise l'exige — c'est la lecture la plus lourde du lot. Un `statut` à `-` n'est pas
     un détail : le ticket est hors du projet ou son Status est vide, donc invisible de `queue.sh`
     et de tous les comptes (docs/10 §3.7).
   - **`review-queue`** — TSV, la plus ancienne PR d'abord : `mr`, `age_j`, `etat`, `pipeline`,
     `auteur`, `relecteur`, `branche`, `titre`. Depuis #418 une PR qui traîne ici est une PR que
     `merge-mr` a **refusé de merger**, pas une PR en attente de relecteur — « aucun relecteur » est
     le cas normal (#196). Rattache-la à son ticket par l'`iid` du nom de branche.
   - **`milestones`** — TSV : `titre`, `etat`, `debut`, `echeance`, `fermes`, `total`, `rail`.
     Les **actifs** sont le sujet ; l'**échéance est l'ordre** de la file (docs/10 §3.4), pas une
     date de livraison à commenter.
   - **`milestones-a-boucler`** — TSV : `titre`, `rail`, `criteres`, `fermes`, `total`. **Muet**
     (code 3) quand il n'y a rien : alors n'en parle pas du tout.
   - **`reconcile-en-cours`** — recensement lisible des tickets « En cours » : **vivant** ·
     **orphelin** · **hors de portée**, puis le compte des trois. Un « hors de portée » n'est pas
     une anomalie (le ticket est travaillé sur une autre machine) ; un **orphelin** en est une.

   Puis le bilan de santé, qui juge la forge elle-même — labels, dérives cycle de vie ↔ PR, ménage
   des branches, garde-fous de merge du dépôt, jalons :

   ```
   bash scripts/gitlab/doctor.sh
   ```

   Il est en lecture seule et rend `1` sur un contrôle **dur** (auth, labels). Garde ses `✗` et ses
   `⚠` tels qu'il les écrit ; ses `·` (« sans objet ») ne sont pas des dérives et n'entrent pas dans
   le compte. Ne recopie pas ses sept sections — le compte rendu ne garde que ce qui appelle un
   geste.

3. **Les jalons actifs, et la roadmap contre le réel.** Pour **chaque rail** :

   ```
   bash scripts/gitlab/lib.sh current-milestone produit
   bash scripts/gitlab/lib.sh current-milestone outillage
   ```

   Le verbe rend le jalon actif le plus tôt échu **qui porte encore un ticket ouvert**, et **nomme
   sur stderr** chaque jalon sauté avec sa cause — **soldé** (fermeture en attente d'une décision
   humaine) ou **vide** (parfois à dessein, #619). Relaie ces sauts : ce sont eux qui expliquent
   pourquoi le jalon retenu n'est pas celui qu'on attendait. Ne rien rendre (code 1) n'est pas une
   panne : ce rail n'a plus rien à prendre, et c'est un fait du plan.

   Pour chaque jalon courant, ce qu'il lui reste :

   ```
   bash scripts/gitlab/lib.sh milestone-issues "<titre>"
   ```

   TSV : `iid`, `statut`, `type`, `agent`, `prio`, `titre`. C'est là que se lit **ce qui est
   prenable tout de suite** — « À faire » et sans assigné (colonne `assigne` de `backlog-table`) :
   un « À faire » assigné est une **protection**, jamais un oubli (#621).

   **Puis la roadmap** : lis dans [`docs/06-roadmap.md`](../../docs/06-roadmap.md) la section qui
   décrit chaque jalon courant, et compare-la à ce que la forge vient de rendre. Trois écarts
   méritent d'être nommés, et aucun ne se conclut d'un mot-clé :
   - un chantier que la roadmap annonce et qu'**aucun ticket ouvert ne porte** ;
   - un jalon **actif** que la roadmap ne décrit pas — `/idee` est la commande qui l'y consigne ;
   - une décision écrite (`docs/*-decision-*.md`) que le travail en cours **renverse** sans qu'une
     note le dise. Cherche par mots-clés pour **trouver** ; conclus en lisant.

4. **Les runs — ce que la machine a fait sans qu'on regarde.** Depuis le clone principal :

   ```
   bash scripts/orchestrate/status.sh --list
   bash scripts/orchestrate/journal.sh refus --claude --tous
   ```

   - **`status.sh --list`** rend les runs connus, du plus ancien au plus récent. Un run **en vol**
     change tout le plan : ses tickets sont pris, ses PR vont arriver, et `reconcile-en-cours`
     l'aura déjà nommé (« carte du pilote »). Ne **lance** rien et ne **reprends** rien ici.
   - **`refus --claude --tous`** rend, en TSV et par ticket (`iid`, nombre de refus, exemple de
     chemin), les **butées `.claude/`** : des correctifs qu'une session de run n'a pas pu écrire, et
     que rien ne rattrape (#229, #608). Une ligne = du travail qui existe et que personne ne verra
     si on ne le nomme pas. Code 3 : rien, et c'est muet.
   - Un run récent dont le **temps** interroge n'est pas ton sujet : propose `/run-audit`, ne
     l'analyse pas ici.

5. **Rends l'état des lieux** — Markdown, dans cet ordre, du plus actionnable au plus lent :

   1. **🚧 Ce qui bloque** — PR que `merge-mr` a refusées (numéro, ticket, ancienneté, cause lue
      dans `pipeline`/`etat`), dérives dures de `doctor.sh`, tickets « En cours » orphelins,
      tickets sans état. Une ligne par fait, chacune avec son chiffre.
   2. **🏃 Ce qui avance** — les jalons actifs de chaque rail avec leur avancement `fermes/total`,
      les tickets « En cours » vivants et par qui (run ou personne), les PR en route.
   3. **💤 Ce qui dort** — les tickets libres du jalon courant de chaque rail, par `prio::` ; les
      jalons soldés qui attendent leur bilan ; les butées `.claude/` sans ticket de reprise ; les
      écarts roadmap ↔ forge de l'étape 3.
   4. **🧭 Ce qui attend un humain** — fermeture d'un jalon, verdict de bouclage, abandon d'un
      ticket, choix produit à deux issues. Nomme-les **sans les trancher** : aucune de ces décisions
      n'est dérivable, et c'est pour ça qu'elles sont là.

   Puis une **synthèse chiffrée** en quatre ou cinq lignes : tickets ouverts par état, combien sont
   libres, PR ouvertes et âge de la plus vieille, avancement de chaque jalon actif. Ne compte que ce
   que les verbes ont rendu.

6. **Le plan de travail — c'est ce que tu ajoutes aux verbes.** Trois sections, dans l'ordre où on
   les joue, et chacune **finie** : pas plus de cinq lignes par section, parce qu'un plan de vingt
   lignes n'est pas un plan mais le backlog une seconde fois.

   - **Maintenant** — ce qui débloque le reste, et rien d'autre : une PR bloquée qui retient un lot
     suivant, un jalon soldé qui empêche le rail d'avancer, un orphelin à reprendre.
   - **Ensuite** — le travail à prendre, dans l'ordre : le rail de `$ARGUMENTS` d'abord, jalon
     courant, `prio::` décroissante, tickets **libres** seulement.
   - **À décider** — ce qui ne se fait pas sans la personne, chaque ligne disant ce qui **dépend**
     de la décision : c'est ce qui en fait une urgence ou pas.

   Chaque ligne porte trois choses et pas une de plus : **ce qu'il y a à faire**, **le chiffre qui
   le motive** (relevé aux étapes 2 à 4, jamais estimé), et **la commande exacte** qui le fait —
   `/mr-fix <pr>`, `/ticket-start <iid>`, `/milestone-bilan "<titre>"`, `/idee`,
   `/ticket-abandon <iid>`, `/retex-utilisateur`. Une ligne sans commande est une intention, pas un
   plan.

   **Recommande ce que tu ferais**, pas un inventaire d'options : une recommandation se contredit,
   un catalogue se relit. Et ce qui n'entre pas dans le plan y entre quand même — une dernière ligne
   « écarté cette fois : … » avec sa raison, pour que la personne puisse te contredire sur ce que tu
   as laissé de côté autant que sur ce que tu as retenu.

7. **Termine en une ligne sur ce que tu n'as pas fait** : tu as lu, tu n'as rien écrit, aucun ticket
   n'a été créé, aucun jalon fermé, aucun verdict rendu. Le plan **propose** des commandes ; c'est
   la personne qui les joue.
