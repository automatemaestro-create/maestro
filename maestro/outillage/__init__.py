"""L'outillage universel d'un projet : l'analyser, et recommander ce qu'il lui faut (#1020).

Le chantier de [docs/38](../../docs/38-decision-outillage-universel-du-projet.md) :
créer ou importer un projet **commence par son outillage** — `AGENTS.md`, des
Agent Skills dans `.agents/skills/`, et des scripts —, recommandé par l'analyse
sur un projet existant, choisi par l'utilisateur sur un projet neuf.

Il porte deux lots, qui regardent le même outillage par ses deux bouts — le
**lot 2** (#1030) l'analyse et le recommande, le **lot 4** (#1032) le relit pour
le transmettre aux agents qui travaillent dans le projet :

    from maestro.outillage import analyser, outillage_du_projet

    analyse = analyser("D:/projets/depensio", projet_id="prj-7f3a")
    analyse.resume                     # "Python, TypeScript ; uv, npm ; tests : pytest ; …"
    analyse.constats.commande_de("tester")
    analyse.recommandation.entrees     # AGENTS.md, les deux ponts, les skills justifiés
    analyse.source_manifeste()         # le fragment `source` du manifeste (docs/38 §4.1)

    outillage = outillage_du_projet(projet)
    outillage.instructions             # le texte d'`AGENTS.md`, dans la portée déclarée
    outillage.skills                   # l'**index** : nom, description, chemin
    outillage.consigne()               # ce qui part dans le message de la tâche

Quatre modules, et la frontière entre eux est celle du disque :

- `maestro.outillage.modele` — les formes, **inertes** : elles décrivent et
  sérialisent, elles ne touchent à rien ;
- `maestro.outillage.detection` — les **tables** (extensions, gestionnaires, CI,
  forges, conventions) et les lecteurs de manifestes. Tout y est lu, **rien n'y
  est exécuté** ;
- `maestro.outillage.analyse` — le parcours borné de la racine, en lecture
  seule, et la mise en constats ;
- `maestro.outillage.recommandation` — des constats à l'outillage proposé,
  chaque entrée avec sa raison et l'endroit du projet qui la justifie ;
- `maestro.outillage.contexte` — ce qu'un agent en reçoit, **dérivé du manifeste
  et borné à ce qu'il déclare** (docs/38 §5). C'est la moitié « transmis
  explicitement » de la frontière ; l'autre moitié, la porte qu'on ferme sur la
  configuration ambiante, vit dans `maestro.providers.claude`.

**Les promesses du paquet, et où elles tiennent.** *Lecture seule* : aucun
module de ce paquet n'ouvre un fichier en écriture ni ne crée de dossier.
*Aucune exécution du code du projet* : aucun n'importe `subprocess`, y compris
là où lancer la commande serait plus court — le VCS lui-même est lu dans
`.git/HEAD` et `.git/config` (`maestro.projets.racine.detecter_vcs`, #221)
plutôt qu'obtenu d'un `git remote`. *Aucun lien symbolique suivi*, et le
**périmètre déclaré du projet s'applique** des deux côtés : ni `.env` ni
`**/secrets/**` ne sont ouverts, qu'on analyse ou qu'on transmette.

Ce que l'analyse rend est **servi par l'API** —
`GET /api/projets/{id}/outillage/analyse`, via
`maestro.controltower.outillage` — et servira de `source` au manifeste que
#1033 écrira. L'écriture dans le dossier de l'utilisateur, elle, n'est pas ici :
ce paquet ne fait que regarder, proposer et relire.
"""

from __future__ import annotations

from maestro.outillage.analyse import analyser, resume
from maestro.outillage.contexte import (
    BALISE_DEBUT,
    BALISE_FIN,
    CHAMP_OUTILS,
    ROLES_TRANSMIS,
    VERSION_MANIFESTE,
    NonTransmis,
    OutillageDuProjet,
    SkillDuProjet,
    outillage_du_projet,
)
from maestro.outillage.detection import CHEMIN_MANIFESTE
from maestro.outillage.modele import (
    ETATS_ENTREE,
    ORIGINES_COMMANDE,
    PREFIXE_ID,
    USAGES,
    VERSION_ANALYSE,
    Analyse,
    Bornes,
    Commande,
    Constats,
    DossierScripts,
    Ecarte,
    Entree,
    Forge,
    Gestionnaire,
    Langage,
    Parcours,
    Piece,
    Recommandation,
    nouvel_id,
)
from maestro.outillage.recommandation import DOSSIER_SKILLS, SKILL_PAR_USAGE, recommander

#: ⚠ `Bornes` exporté ici est celui de l'**analyse** (`maestro.outillage.modele`).
#: Le contexte d'un agent a les siennes, qui ne bornent pas les mêmes choses :
#: elles s'importent de `maestro.outillage.contexte`, nommément. Deux `Bornes`
#: dans le même espace de noms se seraient servies l'une pour l'autre.
__all__ = [
    "BALISE_DEBUT",
    "BALISE_FIN",
    "CHAMP_OUTILS",
    "CHEMIN_MANIFESTE",
    "DOSSIER_SKILLS",
    "ETATS_ENTREE",
    "ORIGINES_COMMANDE",
    "PREFIXE_ID",
    "ROLES_TRANSMIS",
    "SKILL_PAR_USAGE",
    "USAGES",
    "VERSION_ANALYSE",
    "VERSION_MANIFESTE",
    "Analyse",
    "Bornes",
    "Commande",
    "Constats",
    "DossierScripts",
    "Ecarte",
    "Entree",
    "Forge",
    "Gestionnaire",
    "Langage",
    "NonTransmis",
    "OutillageDuProjet",
    "Parcours",
    "Piece",
    "Recommandation",
    "SkillDuProjet",
    "analyser",
    "nouvel_id",
    "outillage_du_projet",
    "recommander",
    "resume",
]
