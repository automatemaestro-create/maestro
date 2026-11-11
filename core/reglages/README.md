# core/reglages — Réglages du poste

Dépôt des **réglages de cette installation-ci**, un fichier par réglage :

- `projets.json` — le **répertoire des projets** (ticket #1022) : le dossier où
  **naît** un projet neuf ; et les **dossiers autorisés** (ticket #1610) que
  l'explorateur parcourt en plus de ses dossiers explorables. Décrit ci-dessous.
- `fournisseur.json` — le **fournisseur par défaut** (ticket #1609) : le
  fournisseur, son accès et son modèle, choisis et éprouvés dans *Paramètres ›
  Fournisseurs*. Il porte les variables que le choix pose
  (`MAESTRO_PROVIDER`, `MAESTRO_MODEL`, `CLAUDE_AUTH_MODE`, `OPENAI_BASE_URL`)
  et le **nom** de la variable de clé, jamais sa valeur : la clé vit chiffrée
  dans le coffre du poste (`core/secrets/_poste.json`). `load_settings` le relit
  à chaque appel et ne remplit que les variables que l'environnement laisse
  vides. Code : `maestro.fournisseur_par_defaut` ; HTTP :
  `/api/fournisseurs/defaut` (docs/05 §6.4quater) ; tests :
  `tests/test_fournisseur_par_defaut.py`.

⚠ **À ne pas confondre avec `core/projets/`**, et la confusion est facile parce
que les deux parlent de « racines » : `core/projets/` porte les **déclarations**
(un `<id>.json` par projet — ce que Maestro sait des projets déjà déclarés) ;
ici vit un seul `projets.json`, qui dit **où le prochain sera créé**. D'où deux
dossiers plutôt qu'un fichier de réglages glissé parmi les déclarations, que
`ProjetStore.lister()` relirait comme un projet illisible de plus.

## Le répertoire des projets

- **Un fichier** : `projets.json` (`repertoire`, horodaté). C'est un objet de
  réglages, pas une collection.
- `repertoire` à `null` ne veut pas dire « aucun » mais **« le défaut »** :
  `Maestro` sous le dossier personnel — un sous-dossier **nommé**, jamais le
  dossier personnel nu, que `valider_racine` refuse (`dossier-utilisateur-nu`).
  Le stocker nul plutôt que résolu fait suivre un déménagement du dossier
  personnel sans réécrire le fichier.
- **Créé à la première utilisation** : le dossier n'existe pas forcément, et le
  créer au démarrage de l'API le poserait chez qui n'ouvrira jamais l'écran
  Projets. C'est `GET /api/projets/repertoire` qui le crée — la première fois
  qu'on demande *où naît un projet neuf* —, et la réponse **dit** qu'elle l'a
  fait (`cree`) plutôt que de le faire en silence.
- Toute validation passe par `valider_racine` (EF-38) : il n'y a pas de seconde
  porte, ici pas plus qu'ailleurs (docs/24 §2.5).
- Lecture/écriture par le code : `maestro.projets.reglages.ReglagesProjetsStore` ;
  par HTTP : `GET` et `PUT /api/projets/repertoire` (docs/05 §6.7) ; depuis
  l'UI : la section **« Projets »** des Paramètres, et le dossier parent
  **prérempli** du formulaire de déclaration d'un projet neuf.
- Racine remplaçable par `MAESTRO_REGLAGES_DIR` (cf. `.env.example`).

## Les dossiers autorisés

- **Le même fichier** : `projets.json` (`dossiers_autorises`, une liste de
  chemins POSIX dans l'ordre de leur autorisation). Régler le répertoire ne les
  touche pas, et inversement.
- **Autorisés depuis l'écran** : quand l'explorateur refuse un dossier parce
  qu'il est hors des dossiers explorables, le refus propose de l'autoriser,
  après une confirmation qui nomme le dossier. Aucune variable à renseigner,
  aucun redémarrage : la frontière se relit à chaque appel.
- Ils **s'ajoutent** à `MAESTRO_EXPLORATEUR_RACINES` (ou au défaut) sans la
  remplacer.
- L'autorisation passe par `valider_racine` : pas de seconde porte. Un dossier
  qui ne pourrait pas être la racine d'un projet n'est pas autorisable, et chaque
  lecture rejuge la liste — une entrée devenue inutilisable (disque débranché) ne
  sert plus de frontière, et l'écran le dit. Les zones interdites restent
  refusées sous un dossier autorisé.
- Lecture/écriture par le code : `ReglagesProjetsStore.autoriser` et `retirer` ;
  par HTTP : `GET`, `POST` et `DELETE /api/projets/dossiers-autorises`
  (docs/05 §6.7) ; depuis l'UI : le refus de l'explorateur, et la ligne
  **« Dossiers autorisés »** de *Paramètres › Projets*, où chacun se retire.

Les réglages écrits ici sont des **données d'exécution** : ils ne sont pas
commités (voir `.gitignore`).

Tests (#1022) : `tests/test_reglages_projets.py` (dépôt, API, explorateur),
`apps/web/tests/parametres-projets.test.tsx` et le bloc « le répertoire des
projets » de `apps/web/tests/projets.test.tsx`. Tests (#1610) :
`tests/test_dossiers_autorises.py`, les blocs « dossiers autorisés » de
`apps/web/tests/parametres-projets.test.tsx` et de
`apps/web/tests/projets.test.tsx`.
