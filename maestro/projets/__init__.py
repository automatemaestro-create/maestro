"""Projets de l'utilisateur : l'entité, sa racine validée et sa persistance (#221).

Le socle de la **Phase 7** (parent #219,
[docs/24 §2](../../docs/24-projets-locaux-et-poste-de-travail.md)) :
jusqu'ici Maestro produisait des livrables sans jamais travailler *dans* un
projet — l'espace de travail d'une tâche était un `tempfile.mkdtemp()` créé vide
et détruit en fin d'exécution (`maestro.sandbox.workspace`), et le dernier mètre
(poser le travail chez l'utilisateur) était recopié à la main. Ce module pose la
première pierre : **un projet a une racine sur le disque**, déclarée et validée.

    from maestro.projets import ProjetStore

    store = ProjetStore.default()
    projet = store.creer("Dépensio", "D:/projets/depensio")
    projet.vcs          # Vcs(type='git', branche_base='main', distant='git@…') ou None
    projet.perimetre    # exclut d'office .git, node_modules, .env, **/secrets/**

Quatre responsabilités, une par module :

- `maestro.projets.modele` — l'entité `Projet` (`Vcs`, `Perimetre`) et sa
  sérialisation. Inerte : elle ne touche pas au disque (EF-35) ;
- `maestro.projets.racine` — la **frontière** : `valider_racine` canonicalise et
  refuse **avec un motif** (racine de disque, dossier utilisateur, `.ssh`,
  `AppData`, dépôt Maestro…), `verifier_zone_interdite` en isole la part qui
  vaut pour **tout** chemin (celle que réutilise l'explorateur de #223),
  `chemin_dans_racine` interdit d'écrire au-dessus de la racine déclarée, et
  `detecter_vcs` constate Git sans jamais l'imposer (EF-38) ;
- `maestro.projets.store` — la persistance, un fichier JSON par projet sous
  `core/projets/` (ou `MAESTRO_PROJETS_DIR`), au patron des autres dépôts du POC ;
- `maestro.projets.application` — le **dernier mètre** (#227, EF-37) : ce que le
  travail d'une tâche changerait (`diff_du_travail`), le contrôle qui précède
  toute écriture (`verifier_perimetre`) et l'écriture elle-même (`appliquer` —
  fusion de la branche de tâche si le projet est versionné, recopie sinon). Il
  n'interroge personne : l'accord humain est le sujet de
  `maestro.controltower.validation.appliquer_sous_validation`, qui branche cette
  action sur la validation existante (EF-08) au lieu d'en inventer une.

Deux modules s'y sont ajoutés avec le montage en mode isolé (#226), parce qu'ils
répondent à des questions du **projet** et non de la sandbox : `perimetre`
**applique** au disque les motifs que `modele` déclarait (quels chemins le
périmètre retire), et `secrets` fait couvrir par la rédaction (#109) les valeurs
lues dans le projet de l'utilisateur — pas seulement celles de Maestro.

Le reste de la phase vit ailleurs et s'appuie sur ce socle : le `projet_id` porté
par la tâche et le run (#222, `maestro.engine`), l'API et l'explorateur de
dossiers (#223, `maestro.controltower.projets`), l'espace de travail dérivé —
worktree Git ou copie (#224, `maestro.sandbox.projet`), l'écran Projets (#225,
`apps/web`) et le montage en mode isolé (#226, `maestro.sandbox.container`). Les
agents ne travaillent **jamais** directement dans la racine (EF-36) : rien ici
n'ouvre cette porte, on ne fait que déclarer où le projet se trouve.
"""

from __future__ import annotations

from maestro.projets.application import (
    APPLICATION_APPROUVEE,
    APPLICATION_REFUSEE,
    APPLICATION_SANS_OBJET,
    NATURE_AJOUT,
    NATURE_MODIFICATION,
    NATURE_SUPPRESSION,
    ApplicationRefusee,
    DiffProjet,
    Modification,
    ResultatApplication,
    appliquer,
    commiter_en_attente,
    diff_du_travail,
    verifier_perimetre,
)
from maestro.projets.modele import (
    EXCLUS_DEFAUT,
    ID_PROJET,
    INCLUS_DEFAUT,
    ORIGINES,
    PREFIXE_ID,
    Perimetre,
    Projet,
    Vcs,
    nouvel_id,
)
from maestro.projets.perimetre import Exclu, exclusions, fichiers_exclus
from maestro.projets.racine import (
    RacineRefusee,
    canonique,
    chemin_dans_racine,
    detecter_vcs,
    valider_racine,
    verifier_zone_interdite,
)
from maestro.projets.secrets import enregistre_secrets_du_projet
from maestro.projets.store import ProjetStore

__all__ = [
    "APPLICATION_APPROUVEE",
    "APPLICATION_REFUSEE",
    "APPLICATION_SANS_OBJET",
    "EXCLUS_DEFAUT",
    "ID_PROJET",
    "INCLUS_DEFAUT",
    "NATURE_AJOUT",
    "NATURE_MODIFICATION",
    "NATURE_SUPPRESSION",
    "ORIGINES",
    "PREFIXE_ID",
    "ApplicationRefusee",
    "DiffProjet",
    "Exclu",
    "Modification",
    "Perimetre",
    "Projet",
    "ProjetStore",
    "RacineRefusee",
    "ResultatApplication",
    "Vcs",
    "appliquer",
    "canonique",
    "chemin_dans_racine",
    "commiter_en_attente",
    "detecter_vcs",
    "diff_du_travail",
    "enregistre_secrets_du_projet",
    "exclusions",
    "fichiers_exclus",
    "nouvel_id",
    "valider_racine",
    "verifier_perimetre",
    "verifier_zone_interdite",
]
