---
description: Génère une présentation HTML autonome des travaux d'un milestone (fonctionnalités, corrections, écrans touchés, démonstrations filmées)
argument-hint: "[milestone]  (titre ou fragment, ex. « Phase 3 » — défaut : la phase courante)"
allowed-tools: Bash(git:*), Bash(gh:*), Bash(bash:*), Bash(node:*), Bash(npm:*), Bash(.venv/Scripts/python.exe:*), Bash(.venv/bin/python:*)
---

Tu vas produire une **présentation HTML** de ce qui a été construit pendant un milestone :
à montrer à un sponsor, à l'équipe, en fin de phase. Le fichier est **autonome** (CSS en ligne,
captures et clips en base64) — il s'ouvre et se partage tel quel.

Commande **de supervision côté forge** : tu **lis** le backlog et tu **écris un fichier** dans le
dépôt. Tu ne touches **jamais** au cycle de vie — ni statut, ni PR, ni merge, ni commit.

Quatre scripts font le travail ; ton rôle est de choisir le milestone, d'**écrire la matière
rédactionnelle** (le résumé du milestone, une phrase par ticket) et de **relayer** ce que les
scripts dérivent. Ne réécris pas le HTML à la main : le gabarit vit dans `build.py`, c'est ce qui
rend le rendu stable d'une génération à l'autre.

⚠ **Ce que tu ne devines plus** (#543) : le rattachement d'un ticket à un écran **se lit** dans
`ecrans-touches.sh`, qui le dérive des commits. Ne pose plus de clé de capture au jugé — la seule
chose qui te reste à juger est la **prose**.

1. Vérifie les pré-requis : `bash scripts/gitlab/lib.sh require`. Arrête-toi si non authentifié.

2. **Résous le milestone.** Liste-les : `bash scripts/gitlab/lib.sh milestones` (TSV :
   `titre`, `etat`, `debut`, `echeance`, `fermes`, `total`, `rail` — la ligne d'en-tête `#`
   s'ignore).
   - `$ARGUMENTS` vide → prends le milestone de la phase courante **du rail produit**
     (`bash scripts/gitlab/lib.sh current-milestone`, dont c'est le défaut). Une présentation de
     milestone présente **le produit** : elle joint des captures d'écran et des démonstrations
     filmées, que l'outillage de la forge n'a pas (#617). Un milestone de rail `outillage` se
     présente très bien si on le **nomme** explicitement — il rendra simplement des sections sans
     vignettes, ce que l'étape des écrans touchés sait déjà dire.
   - `$ARGUMENTS` renseigné → cherche le milestone dont le titre **contient** ce fragment, sans
     tenir compte de la casse (`Phase 3` → `Phase 3 — V2`). **Zéro ou plusieurs correspondances :
     arrête-toi**, affiche la liste des milestones et demande lequel — ne devine pas.
   Le titre retenu doit être **exact** pour l'étape suivante (c'est la clé de l'API).

3. **Collecte les tickets** : `bash scripts/gitlab/lib.sh milestone-issues "<titre-exact>"`.
   Sortie TSV : `iid`, `statut` (le libellé du cycle de vie, lu dans le champ Status du projet ;
   `-` si le ticket n'a pas d'état), `type`, `agent`, `prio`, `titre`. Garde **tous**
   les tickets : le rendu les regroupe lui-même par état (Livré / En revue / En cours / À venir /
   Écarté). N'invente aucun ticket et n'en écarte aucun de ton propre chef.

4. **Dérive les écrans touchés** — un seul appel, avec **tous** les iid de l'étape 3 :
   ```
   bash scripts/presentation/ecrans-touches.sh --check <iid> <iid> …
   ```
   Lecture seule, hors réseau. Sortie TSV (`iid`, `route`, `cle`, `fichiers`, en-tête `#` à
   ignorer) : **une ligne par (ticket, écran)**, dérivée des `Refs #<iid>` / `Closes #<iid>` que
   le hook `commit-msg` impose à tout commit. `--check` ajoute sur stderr la ref lue et les
   tickets dont **aucun commit** ne porte de référence sur cette ref (pas encore mergés) — à
   distinguer d'un ticket sans écran.

   Trois résultats, et il faut les lire différemment :
   - **une ou plusieurs lignes avec une `cle`** → ce sont les écrans du ticket. La `cle` est
     **exactement** celle du manifeste de captures : c'est par elle que le rendu retrouve l'image.
   - **une ligne dont `route` et `cle` valent `-`** → le ticket a une surface visible mais
     **indéterminée** : il n'a touché que des composants partagés (`apps/web/components/**`) ou la
     coquille commune (`app/layout.tsx`, `globals.css`). Garde-la : le rendu la nomme
     « Composants partagés ». La taire dirait « ce ticket n'a rien changé à l'écran », ce qui est
     faux.
   - **aucune ligne** → le ticket **n'a pas de surface visible** (moteur, CI, doc, outillage).
     C'est un résultat, pas un échec : il n'aura ni vignette ni écran, et c'est voulu.

   Son échec n'arrête pas la commande : continue sans écrans dérivés et note-le à l'étape 7.

5. **Prends les captures et les démonstrations filmées** — un seul appel, qui installe
   `playwright-core` dans un dossier temporaire, démarre la stack de démo, photographie les pages
   du menu principal, **filme les parcours de démonstration** et l'arrête :
   ```
   bash scripts/presentation/captures.sh --sortie <dossier-de-travail>/captures
   ```
   Utilise le **dossier de scratchpad** de la session comme dossier de travail, jamais le dépôt.
   Compte plusieurs minutes : build de production, série de captures, puis un clip par parcours.
   `--sans-videos` s'en passe (série plus courte, manifeste sans clip) — à réserver aux cas où
   seules les captures sont demandées.

   Le script écrit un manifeste `captures.json` à **deux listes** :
   - `pages` — `cle`, `href`, `libelle`, `fichier`, `complet`, `erreur`. `complet: false` signale
     une page photographiée avant d'être peuplée : regarde-la avant de la retenir.
   - `videos` — `cle`, `libelle`, `fichier`, `duree_ms`, `octets`, `gestes`, `gestes_joues`,
     `complet`, `erreur`. Les parcours sont déclarés dans `scripts/presentation/parcours.mjs`.
     `gestes_joues` (#830) dit combien de gestes déclarés ont réellement joué : c'est lui qui
     sépare un clip **écourté** (au moins un geste, conservé) d'un clip **muet** (aucun geste —
     écarté à la source, donc `fichier: null`).

   **Un parcours en échec laisse sa ligne** avec son erreur : c'est ainsi qu'on sait qu'il a été
   tenté. Lis-la, ne la recopie pas telle quelle — voir l'étape 6.

   **Son échec n'arrête pas la commande** : continue sans visuels et note-le à l'étape 7
   (`notes`) pour que la présentation le dise elle-même.

6. **Rédige la matière, et sélectionne les visuels.** C'est la seule partie qui demande ton
   jugement :
   - `milestone.resume` — 2 à 4 phrases : ce que cette phase a apporté, vu de l'utilisateur.
     Pas une paraphrase de la liste des tickets ; ce qui est vrai maintenant et ne l'était pas
     avant.
   - `tickets[].resume` — **une phrase** par ticket, en français, qui dit ce que le ticket
     apporte (pas ce qu'il touche). Le titre est déjà affiché : ne le répète pas. Si le titre
     suffit et que tu n'as rien à ajouter, laisse `null` plutôt que de délayer. Pour les tickets
     dont l'intitulé est opaque, va lire le ticket (`bash scripts/gitlab/lib.sh issue-brief <iid>`)
     — cible les quelques-uns qui le méritent, pas les trente.
   - `tickets[].ecrans` — **recopie** les `cle` que l'étape 4 rend pour cet iid, dans l'ordre. Pas
     de jugement ici : liste vide si le ticket n'a aucune ligne.
   - `tickets[].capture` — la clé de la vignette de la carte. **Prends la première `cle` de
     `tickets[].ecrans` qui existe dans `pages`**, et `null` sinon. Ce n'est plus un pari : un
     ticket sans écran dérivé n'a pas de vignette, un écran sans capture non plus (`/projets` est
     servi mais hors menu — il n'est photographié par personne).
   - `videos[]` — **quels parcours retenir**. Reprends du manifeste ceux qui ont un `fichier`, et
     **écarte** :
     - un parcours **sans `fichier`** (`erreur` renseignée) : la démonstration n'a pas eu lieu,
       il n'y a rien à jouer ;
     - un parcours dont le clip **ne démontre rien de la phase** — la règle d'abstention est
       celle des captures, en plus stricte : *pas de surface visible, pas de visuel*. Si aucun
       ticket du milestone n'a touché l'écran du parcours (étape 4), le clip illustre une
       fonctionnalité que cette phase n'a pas changée. Une vignette hors sujet dessert la
       présentation ; une vidéo hors sujet la dessert deux fois plus.
     Un parcours `complet: false` a été **écourté** (un geste en échec, ou le plafond du clip
     atteint) mais son clip existe et il a joué `gestes_joues` gestes sur `gestes` : regarde-le
     avant de le retenir — il montre ce qu'il a eu le temps de montrer. Une page qui n'était pas
     prête n'écourte plus rien depuis #830 : les gestes sont joués quand même, et un parcours qui
     n'en a joué **aucun** n'arrive pas jusqu'ici — son clip est écarté à la source, il est déjà
     couvert par la première puce.
     Renseigne `affiche` seulement si tu veux imposer une image de repli : sans elle, le rendu
     prend la capture de même clé, et à défaut un cartouche qui nomme le clip.

7. **Écris le JSON** dans le dossier de travail (jamais dans le dépôt), à ce schéma :
   ```json
   {
     "milestone": {"titre": "Phase 3 — V2", "etat": "active",
                   "debut": "2026-11-05", "echeance": "2026-12-16", "resume": "…"},
     "projet": {"url": "https://github.com/<compte>/<dépôt>"},
     "tickets": [{"iid": 96, "titre": "…", "statut": "Terminé", "type": "feature",
                  "agent": "dev", "prio": "haute", "resume": "…",
                  "capture": "couts", "ecrans": ["couts", "-"]}],
     "captures": [{"cle": "accueil", "libelle": "Tableau de bord",
                   "fichier": "<…>/captures/accueil.png"}],
     "ecrans":   [{"cle": "couts", "libelle": "Coûts & analytics", "route": "/couts"}],
     "videos":   [{"cle": "couts", "libelle": "Coûts & analytics : la dépense, période par période",
                   "fichier": "<…>/captures/couts.webm", "affiche": null}],
     "notes":    []
   }
   ```
   `projet.url` se déduit du dépôt : la base est `https://<hôte>/<dépôt>`, où l'hôte vient de
   `bash scripts/gitlab/lib.sh host` — il **suit la forge active** et non le remote, précisément
   pour rester juste tant qu'`origin` pointe encore ailleurs (#343). Les liens vers les tickets sont
   construits par le script. Ne reprends dans `captures` que les entrées **sans erreur** de
   `pages`. `ecrans` donne un **libellé** et une **route** aux clés dérivées à l'étape 4 : reprends
   le `libelle` de la page de même clé quand il y en a une, et la colonne `route` de l'étape 4 ;
   une clé citée par un ticket mais absente d'ici est rendue quand même, sous sa clé nue.

   ⚠ **`notes` ne porte que ce qui a MANQUÉ à cette génération-ci** (#563) — captures non prises,
   parcours non filmé, dérivation en erreur : des faits sur *ce fichier*, que son lecteur ne peut
   pas deviner en le regardant. Le script y ajoute lui-même les clips écartés par un plafond de
   taille ; le plus souvent, **`notes` reste vide, et c'est le cas nominal**. N'y mets **jamais**
   les limites méthodologiques de la commande — « un composant partagé ne se rattache à aucune
   route », « les captures montrent la stack d'aujourd'hui », « tel ticket n'a pas de commit sur
   `origin/main` ». Elles sont listées sous « Ce que la commande ne sait pas » et vont au **résumé
   de l'étape 10**, dans le terminal : ce sont des réserves de production, destinées à qui lance la
   commande. La page, elle, se partage à un sponsor — un pied de page qui explique comment
   l'outillage a été dérivé ne lui apprend rien et lui coûte la fin du document.

8. **Génère la présentation** avec le python du venv (jamais le python système) :
   ```
   .venv/Scripts/python.exe scripts/presentation/build.py <dossier-de-travail>/presentation.json --ouvrir
   ```
   Sans `--sortie`, le fichier va dans `docs/presentations/<slug-du-milestone>.html`. Le script
   imprime le chemin écrit, sa taille, et **le compte de clips intégrés sur clips demandés** avec
   les deux plafonds appliqués. Un clip trop lourd, ou qui ne tient plus dans le budget du
   fichier, est **écarté avec son motif** — il garde sa place dans la page, sous son affiche de
   repli. `MAESTRO_PRESENTATION_VIDEO_MAX` (Mio, par clip) et `MAESTRO_PRESENTATION_MAX` (Mio,
   fichier entier) déplacent les plafonds ; `0` vaut « aucun ».

   **`--ouvrir` ouvre la présentation** dans le navigateur par défaut du poste une fois le fichier
   écrit : la commande vient de passer plusieurs minutes à la produire, elle n'a pas à laisser son
   lecteur la retrouver dans un explorateur. L'ouverture est **best-effort** — aucun effet sur le
   code de retour, et un échec nomme ce qu'il a tenté d'ouvrir. N'écris **jamais** la commande
   d'ouverture toi-même (`Start-Process`, `open`, `xdg-open`…) : la logique de plateforme vit dans
   le script, seul à connaître le chemin réellement écrit.

9. **Regarde le résultat avant de le livrer** — l'étape 8 vient de l'ouvrir : vérifie qu'il tient
   debout (pas de section vide, pas de vignette hors sujet, pas de `null` affiché tel quel, les
   clips se lisent, **une image s'ouvre en grand au clic et se referme par `Échap`**). Si le skill
   `verify` est disponible, un coup d'œil au rendu via navigateur vaut mieux qu'une lecture du
   HTML — le MCP `chrome-maestro` refusant le protocole `file:`, sers le dossier
   (`python -m http.server`) et ouvre le fichier par `http://127.0.0.1:<port>/`.

10. Termine par un **résumé court** : le milestone présenté, le nombre de tickets par état, le
    nombre de captures intégrées, le nombre d'**écrans touchés** dérivés (et combien de tickets
    n'en ont aucun), les **clips retenus**, les **clips écartés et leur cause** (parcours en
    échec, hors sujet, plafond de taille), le **chemin du fichier** et sa taille. Signale ce
    qui a échoué plutôt que de le taire. Le fichier est écrit dans le dépôt mais **non commité**
    — dis-le, et laisse la décision de le versionner à l'utilisateur.

## Ce que la commande ne sait pas

À dire dans le résumé quand le cas se présente, plutôt que de le laisser deviner :

- **Un composant partagé ne se rattache à aucune route.** `apps/web/components/**` touche
  potentiellement plusieurs écrans sans qu'aucun ne le dise : la dérivation rend une ligne
  « indéterminée » (`-`) au lieu d'attribuer l'écran au hasard. Elle ne **compte pas**
  `apps/web/lib/**` ni `apps/web/hooks/**`, qui sont de la plomberie — presque tous les tickets de
  la Control Tower y touchent, et les compter n'apprendrait plus rien.
- **Le MCP `chrome-maestro` ne filme pas.** Il n'expose que `browser_take_screenshot`, aucun verbe
  d'enregistrement : les clips passent par le `recordVideo` de Playwright dans `captures.mjs`. Ce
  n'est pas un renoncement au MCP — c'est le même moteur, appelé là où le contexte du navigateur
  est déjà construit. Il n'y a donc **aucun** moyen de filmer un parcours depuis cette commande
  sans passer par `captures.sh`.
- **Les captures et les clips montrent la stack de démonstration d'aujourd'hui**, pas l'écran tel
  qu'il était pendant la phase. Ce que la dérivation rend, c'est *quels écrans la phase a touchés*
  — jamais *à quoi ils ressemblaient avant*.
- **Un ticket non mergé n'a pas de commit sur `origin/main`** : il ne rend donc aucun écran, comme
  un ticket sans surface visible. C'est `--check` qui les distingue, sur stderr.

Ne lance aucune commande d'écriture côté forge (`gh issue edit`, `gh pr create`, `set-workflow`,
`log-time`…) ni aucun `git commit`/`git push` : cette commande observe et produit un fichier.
