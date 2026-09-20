"""L'outillage universel d'un projet : l'analyser, et recommander ce qu'il lui faut (#1020).

Le chantier de [docs/38](../../docs/38-decision-outillage-universel-du-projet.md) :
créer ou importer un projet **commence par son outillage** — `AGENTS.md`, des
Agent Skills dans `.agents/skills/`, et des scripts —, recommandé par l'analyse
sur un projet existant, choisi par l'utilisateur sur un projet neuf.

Il porte trois lots, qui regardent le même outillage par ses trois bouts — le
**lot 2** (#1030) l'analyse sur un projet existant, le **lot 3** (#1031) le
demande sur un projet neuf, le **lot 4** (#1032) le relit pour le transmettre
aux agents qui travaillent dans le projet :

    from maestro.outillage import analyser, outillage_du_projet

    analyse = analyser("D:/projets/depensio", projet_id="prj-7f3a")
    analyse.resume                     # "Python, TypeScript ; uv, npm ; tests : pytest ; …"
    analyse.constats.commande_de("tester")
    analyse.recommandation.entrees     # AGENTS.md, les deux ponts, les skills justifiés
    analyse.source_manifeste()         # le fragment `source` du manifeste (docs/38 §4.1)

    from maestro.outillage import Choix, question_suivante, recommandation_depuis_choix

    question_suivante([])              # « Quelle sorte de projet est-ce ? », recommandée
    reco = recommandation_depuis_choix([Choix("nature", "service-api"), …])
    reco.entrees                       # la **même** forme que `analyse.recommandation`

    outillage = outillage_du_projet(projet)
    outillage.instructions             # le texte d'`AGENTS.md`, dans la portée déclarée
    outillage.skills                   # l'**index** : nom, description, chemin
    outillage.consigne()               # ce qui part dans le message de la tâche

⚠ **Les deux premiers bouts se rejoignent sur `recommander`**, et c'est le
critère de #1031 : les réponses de l'utilisateur deviennent des `Constats`
(`constats_depuis_choix`), et la suite est celle d'un projet analysé. Il n'y a
donc **pas deux chemins** de « ce qu'il faut à ce projet » à tenir d'accord — ce
que #1033 générera vient de la même fonction, quelle que soit sa provenance.

Six modules, et la frontière entre eux est celle du disque :

- `maestro.outillage.modele` — les formes, **inertes** : elles décrivent et
  sérialisent, elles ne touchent à rien ;
- `maestro.outillage.questionnaire` — le **lot 3** (#1031) : les questions qui
  décident de l'outillage d'un projet **neuf**, et la mue de leurs réponses en
  `Constats`. C'est le pendant d'`analyse` — là-bas on lit un projet existant,
  ici on le demande —, et les deux aboutissent au même `recommander` ;
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
from maestro.outillage.questionnaire import (
    CATALOGUE,
    QUESTIONS_MAX,
    SOURCE_CHOIX,
    Choix,
    Option,
    QuestionOutillage,
    cles_connues,
    constats_depuis_choix,
    deductions,
    options_admissibles,
    question_suivante,
    recommandation_depuis_choix,
    resume_des_choix,
    source_manifeste_des_choix,
    valeur_admissible,
)
from maestro.outillage.recommandation import DOSSIER_SKILLS, SKILL_PAR_USAGE, recommander

#: ⚠ `Bornes` exporté ici est celui de l'**analyse** (`maestro.outillage.modele`).
#: Le contexte d'un agent a les siennes, qui ne bornent pas les mêmes choses :
#: elles s'importent de `maestro.outillage.contexte`, nommément. Deux `Bornes`
#: dans le même espace de noms se seraient servies l'une pour l'autre.
__all__ = [
    "BALISE_DEBUT",
    "BALISE_FIN",
    "CATALOGUE",
    "CHAMP_OUTILS",
    "CHEMIN_MANIFESTE",
    "DOSSIER_SKILLS",
    "ETATS_ENTREE",
    "ORIGINES_COMMANDE",
    "PREFIXE_ID",
    "QUESTIONS_MAX",
    "ROLES_TRANSMIS",
    "SKILL_PAR_USAGE",
    "SOURCE_CHOIX",
    "USAGES",
    "VERSION_ANALYSE",
    "VERSION_MANIFESTE",
    "Analyse",
    "Bornes",
    "Choix",
    "Commande",
    "Constats",
    "DossierScripts",
    "Ecarte",
    "Entree",
    "Forge",
    "Gestionnaire",
    "Langage",
    "NonTransmis",
    "Option",
    "OutillageDuProjet",
    "Parcours",
    "Piece",
    "QuestionOutillage",
    "Recommandation",
    "SkillDuProjet",
    "analyser",
    "cles_connues",
    "constats_depuis_choix",
    "deductions",
    "nouvel_id",
    "options_admissibles",
    "outillage_du_projet",
    "question_suivante",
    "recommandation_depuis_choix",
    "recommander",
    "resume",
    "resume_des_choix",
    "source_manifeste_des_choix",
    "valeur_admissible",
]
