---
description: Génère une présentation HTML autonome des travaux d'un milestone (fonctionnalités, corrections, captures de la Control Tower)
argument-hint: "[milestone]  (titre ou fragment, ex. « Phase 3 » — défaut : la phase courante)"
allowed-tools: Bash(git:*), Bash(glab:*), Bash(bash:*), Bash(node:*), Bash(npm:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*)
---

Tu vas produire une **présentation HTML** de ce qui a été construit pendant un milestone :
à montrer à un sponsor, à l'équipe, en fin de phase. Le fichier est **autonome** (CSS en ligne,
captures en base64) — il s'ouvre et se partage tel quel.

Commande **de supervision côté GitLab** : tu **lis** le backlog et tu **écris un fichier** dans le
dépôt. Tu ne touches **jamais** au cycle de vie — ni statut, ni MR, ni merge, ni commit.

Trois scripts font le travail ; ton rôle est de choisir le milestone, d'**écrire la matière
rédactionnelle** (le résumé du milestone, une phrase par ticket) et de **rattacher les captures
aux tickets qu'elles illustrent**. Ne réécris pas le HTML à la main : le gabarit vit dans
`build.py`, c'est ce qui rend le rendu stable d'une génération à l'autre.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. **Résous le milestone.** Liste-les : `bash scripts/gitlab/lib.sh milestones` (TSV :
   `titre`, `etat`, `debut`, `echeance`, `fermes`, `total` — la ligne d'en-tête `#` s'ignore).
   - `$ARGUMENTS` vide → prends le milestone de la phase courante
     (`bash scripts/gitlab/lib.sh current-milestone`).
   - `$ARGUMENTS` renseigné → cherche le milestone dont le titre **contient** ce fragment, sans
     tenir compte de la casse (`Phase 3` → `Phase 3 — V2`). **Zéro ou plusieurs correspondances :
     arrête-toi**, affiche la liste des milestones et demande lequel — ne devine pas.
   Le titre retenu doit être **exact** pour l'étape suivante (c'est la clé de l'API).

3. **Collecte les tickets** : `bash scripts/gitlab/lib.sh milestone-issues "<titre-exact>"`.
   Sortie TSV : `iid`, `statut` (statut natif), `type`, `agent`, `prio`, `titre`. Garde **tous**
   les tickets : le rendu les regroupe lui-même par état (Livré / En revue / En cours / À venir /
   Écarté). N'invente aucun ticket et n'en écarte aucun de ton propre chef.

4. **Prends les captures de la Control Tower** — un seul appel, qui installe `playwright-core`
   dans un dossier temporaire, démarre la stack, photographie les pages du menu principal et
   l'arrête :
   ```
   bash scripts/presentation/captures.sh --sortie <dossier-de-travail>/captures
   ```
   Utilise le **dossier de scratchpad** de la session comme dossier de travail, jamais le dépôt.
   Le script écrit un manifeste `captures.json` (`cle`, `libelle`, `fichier`, `complet`,
   `erreur`). `complet: false` signale une page photographiée avant d'être peuplée : regarde-la
   avant de la retenir.
   **Son échec n'arrête pas la commande** : continue sans visuels et note-le à l'étape 6
   (`notes`) pour que la présentation le dise elle-même.

5. **Rédige la matière.** C'est la seule partie qui demande ton jugement :
   - `milestone.resume` — 2 à 4 phrases : ce que cette phase a apporté, vu de l'utilisateur.
     Pas une paraphrase de la liste des tickets ; ce qui est vrai maintenant et ne l'était pas
     avant.
   - `tickets[].resume` — **une phrase** par ticket, en français, qui dit ce que le ticket
     apporte (pas ce qu'il touche). Le titre est déjà affiché : ne le répète pas. Si le titre
     suffit et que tu n'as rien à ajouter, laisse `null` plutôt que de délayer. Pour les tickets
     dont l'intitulé est opaque, va lire le ticket (`bash scripts/gitlab/lib.sh issue-brief <iid>`)
     — cible les quelques-uns qui le méritent, pas les trente.
   - `tickets[].capture` — la **clé** d'une capture (colonne `cle` du manifeste) **quand la page
     illustre vraiment le ticket** : un ticket sur la page Coûts → `couts`, sur les validations →
     `validations`. Laisse `null` dès qu'il y a un doute, et pour tout ce qui n'a pas de surface
     visible (moteur, CI, doc). Une vignette qui n'illustre rien dessert la présentation.

6. **Écris le JSON** dans le dossier de travail (jamais dans le dépôt), à ce schéma :
   ```json
   {
     "milestone": {"titre": "Phase 3 — V2", "etat": "active",
                   "debut": "2026-11-05", "echeance": "2026-12-16", "resume": "…"},
     "projet": {"url": "https://gitlab.com/<groupe>/<projet>"},
     "tickets": [{"iid": 96, "titre": "…", "statut": "Terminé", "type": "feature",
                  "agent": "dev", "prio": "haute", "resume": "…", "capture": null}],
     "captures": [{"cle": "accueil", "libelle": "Tableau de bord",
                   "fichier": "<…>/captures/accueil.png"}],
     "notes": []
   }
   ```
   `projet.url` se déduit du dépôt (`glab repo view --web` n'est pas nécessaire : la base est
   `https://gitlab.com/<GL_PROJECT>`, et les liens vers les tickets sont construits par le
   script). Ne reprends dans `captures` que les entrées du manifeste **sans erreur**.

7. **Génère la présentation** avec le python du venv (jamais le python système) :
   ```
   .venv/Scripts/python.exe scripts/presentation/build.py <dossier-de-travail>/presentation.json
   ```
   Sans `--sortie`, le fichier va dans `docs/presentations/<slug-du-milestone>.html`. Le script
   imprime le chemin écrit et sa taille.

8. **Regarde le résultat avant de le livrer** : ouvre le fichier produit et vérifie qu'il tient
   debout (pas de section vide, pas de vignette hors sujet, pas de `null` affiché tel quel). Si
   le skill `verify` est disponible, un coup d'œil au rendu via navigateur vaut mieux qu'une
   lecture du HTML.

9. Termine par un **résumé court** : le milestone présenté, le nombre de tickets par état, le
   nombre de captures effectivement intégrées, le **chemin du fichier** et sa taille. Signale ce
   qui a échoué (captures manquantes, tickets sans résumé) plutôt que de le taire. Le fichier
   est écrit dans le dépôt mais **non commité** — dis-le, et laisse la décision de le versionner
   à l'utilisateur.

Ne lance aucune commande d'écriture GitLab (`glab issue update`, `mr create`, `set-status`,
`log-time`…) ni aucun `git commit`/`git push` : cette commande observe et produit un fichier.
