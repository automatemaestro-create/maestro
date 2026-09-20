# core/reglages — Réglages du poste

Dépôt des **réglages de cette installation-ci** — un seul aujourd'hui, le
**répertoire des projets** (ticket #1022) : le dossier où **naît** un projet
neuf.

⚠ **À ne pas confondre avec `core/projets/`**, et la confusion est facile parce
que les deux parlent de « racines » : `core/projets/` porte les **déclarations**
(un `<id>.json` par projet — ce que Maestro sait des projets déjà déclarés) ;
ici vit un seul `projets.json`, qui dit **où le prochain sera créé**. D'où deux
dossiers plutôt qu'un fichier de réglages glissé parmi les déclarations, que
`ProjetStore.lister()` relirait comme un projet illisible de plus.

## Fonctionnement

- **Un fichier unique** : `projets.json` (`repertoire`, horodaté). C'est un
  objet de réglages, pas une collection.
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

Les réglages écrits ici sont des **données d'exécution** : ils ne sont pas
commités (voir `.gitignore`).

Tests (#1022) : `tests/test_reglages_projets.py` (dépôt, API, explorateur),
`apps/web/tests/parametres-projets.test.tsx` et le bloc « le répertoire des
projets » de `apps/web/tests/projets.test.tsx`.
