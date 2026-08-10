# core/ingestion — Matière téléversée avec un objectif

Emplacement d'**ingestion** des sources de type `fichier` (EF-39, ticket #315 —
socle de la [Phase 8](../../docs/24-projets-locaux-et-poste-de-travail.md#3-ingestion-de-documents)) :
un sous-dossier par run, `<run_id>/`.

Un objectif ne se réduit plus à son texte : il peut embarquer un cahier des
charges, un dossier de maquettes ou une page de spécification
([docs/24 §3.2](../../docs/24-projets-locaux-et-poste-de-travail.md)). Les trois
types de source ne posent pas le même problème — un **dossier** et une **URL**
restent où ils sont, seul le **fichier téléversé** a besoin d'atterrir quelque
part. C'est ici.

## Pourquoi ici, et pas dans le projet

**Une matière téléversée ne se mêle jamais aux fichiers de l'utilisateur.** Même
raison que l'interdiction faite aux agents d'écrire dans la racine d'un projet
(EF-36) : un document venu de l'extérieur est une **entrée non fiable**
([docs/19 §2](../../docs/19-securite-modele-de-menace.md)), et le déposer parmi
les fichiers du projet le rendrait indiscernable de ce que l'utilisateur a écrit
lui-même — pour lui comme pour un agent qui relit le dossier.

Le contrôle est **actif**, pas seulement un défaut bien choisi : un
`MAESTRO_INGESTION_DIR` posé à l'intérieur d'un projet est **refusé**
(`ingestion-dans-le-projet`), jamais obéi.

## Fonctionnement

- Un sous-dossier par run : `<run_id>/`, le `run_id` étant celui du lancement.
  Ce qui appartient à une exécution se retrouve, se trace et se ramasse sans
  toucher à la matière d'un run voisin.
- **Rien n'est créé tant que rien n'est téléversé** : la résolution d'une source
  (#315) ne fait que *calculer* la destination. La création revient au
  téléversement (#317), qui écrit les octets.
- Le `run_id` sert de **segment de chemin** : il est validé (`ID_RUN`) et la
  destination passe par `chemin_dans_racine()` — un nom de fichier venu du
  navigateur ne remonte pas d'un cran (`../`, `C:\`, flux NTFS `note.md:cache`).
- **Plafonds d'ingestion** (`GardeFousIngestion`, ENF-07) : taille par source
  (10 Mio), taille totale (50 Mio), nombre de sources (20). Ils sont **actifs
  par défaut** — un plafond d'ingestion absent laisse un document entrer
  intégralement dans le contexte, et alors « la barre de dépense ment »
  ([docs/24 §3.4](../../docs/24-projets-locaux-et-poste-de-travail.md)). Un
  dépassement est **refusé avec son motif** (`source-trop-volumineuse`,
  `ingestion-trop-volumineuse`, `trop-de-sources`), rendu en 422 par la route de
  lancement.
- Ces plafonds portent sur des **octets**, parce que c'est tout ce que ce niveau
  connaît : le rapport octets → tokens dépend du format. C'est une barrière
  grossière, celle qui arrête l'absurde ; le plafond fin, en tokens, revient à
  l'extraction (#316), seule à connaître le texte.
- Racine remplaçable par `MAESTRO_INGESTION_DIR` (cf. `.env.example`).

## Où c'est écrit dans le code

- `maestro.sources.modele` — la forme (`Source`, `SourceRefusee`), module feuille ;
- `maestro.sources.resolution` — la résolution par type et les refus motivés
  (`racine_ingestion`, `emplacement_ingestion`, `resoudre_sources`) ;
- `maestro.engine.guardrails` — `GardeFousIngestion`, avec les autres limites du
  run ;
- `maestro.controltower.executions` — le lancement, qui résout **avant** de
  partir : refuser après coup reviendrait à annuler un run déjà lancé.

Les sources rejoignent ensuite la projection par l'événement de lancement, comme
le ticket externe (#187) et le projet (#222) — elles survivent donc au rejeu du
journal durable (#97) et se lisent dans le résumé du run.

Tests (#315) : `tests/test_sources.py` — le modèle, et surtout les **refus**
(racine interdite, évasion, schéma d'URL, dépassement de plafond), qui sont des
garde-fous de sécurité et se testent avec leur code. Le reste de la couverture et
la doc de la phase reviennent au lot final #323.

⚠ Le piège de `tests/test_projets.py` vaut ici : sous Windows, le `tmp_path` de
pytest vit sous `C:/Users/<moi>/AppData/Local/Temp`, que la validation de racine
refuse **à raison** (`chemin-sensible`). La parade est la même — la fixture
`_maison_isolee`, qui déplace `Path.home()` dans le dossier temporaire.
