"""L'outillage universel d'un projet : l'analyser, le recommander, l'écrire (#1020).

Le chantier de [docs/38](../../docs/38-decision-outillage-universel-du-projet.md) :
créer ou importer un projet **commence par son outillage** — `AGENTS.md`, des
Agent Skills dans `.agents/skills/`, et des scripts —, recommandé par l'analyse
sur un projet existant, choisi par l'utilisateur sur un projet neuf.

Il porte quatre lots, qui prennent le même outillage à quatre moments — le
**lot 2** (#1030) l'analyse sur un projet existant, le **lot 3** (#1031) le
demande sur un projet neuf, le **lot 5** (#1033) l'écrit dans le dossier du
projet, le **lot 4** (#1032) le relit pour le transmettre aux agents qui
travaillent dans le projet :

    from maestro.outillage import analyser, generer_outillage, outillage_du_projet, rediger

    analyse = analyser("D:/projets/depensio", projet_id="prj-7f3a")
    analyse.resume                     # "Python, TypeScript ; uv, npm ; tests : pytest ; …"
    analyse.constats.commande_de("tester")
    analyse.recommandation.entrees     # AGENTS.md, les skills justifiés — et un pont par
                                       # client qui en a besoin (`recommander(…, clients)`, #1295)
    analyse.source_manifeste()         # le fragment `source` du manifeste (docs/38 §4.1)

    preparation = generer_outillage(
        projet, analyse.constats, analyse.recommandation, source=analyse.source_manifeste()
    )
    preparation.regime                 # "en-place" (c'est fait) | "branche" (attend la fusion)
    preparation.rapport.refuses        # ce qui n'a **pas** été écrasé, et où la neuve attend

    from maestro.outillage import Choix, question_ouverte, recommandation_depuis_choix

    question_ouverte()                 # « Qu'est-ce que ce projet ? » — sans options (#1147)
    reco = recommandation_depuis_choix([Choix("tester", "flutter test", deduit=True), …])
    reco.entrees                       # la **même** forme que `analyse.recommandation`

    outillage = outillage_du_projet(projet)
    outillage.instructions             # le texte d'`AGENTS.md`, dans la portée déclarée
    outillage.skills                   # l'**index** : nom, description, chemin
    outillage.consigne()               # ce qui part dans le message de la tâche

⚠ **Les deux premiers bouts se rejoignent sur `recommander`**, et c'est le
critère de #1031 : les réponses de l'utilisateur deviennent des `Constats`
(`constats_depuis_choix`), et la suite est celle d'un projet analysé. Il n'y a
donc **pas deux chemins** de « ce qu'il faut à ce projet » à tenir d'accord — ce
que #1033 génère vient de la même fonction, quelle que soit sa provenance.

Douze modules, et la frontière entre eux est celle du disque :

- `maestro.outillage.modele` — les formes, **inertes** : elles décrivent et
  sérialisent, elles ne touchent à rien ;
- `maestro.outillage.questionnaire` — le **lot 3** (#1031), sans catalogue
  depuis #1147 : le schéma de ce qui fait un outillage, la lecture vérifiée de ce
  que le modèle a compris d'un projet **neuf**, et la mue des constats en
  `Constats`. C'est le pendant d'`analyse` — là-bas on lit un projet existant,
  ici on l'apprend de la personne —, et les deux aboutissent au même `recommander` ;
- `maestro.outillage.detection` — les **tables** (extensions, gestionnaires, CI,
  forges, conventions) et les lecteurs de manifestes. Tout y est lu, **rien n'y
  est exécuté** ;
- `maestro.outillage.analyse` — le parcours borné de la racine, en lecture
  seule, et la mise en constats — des **indices** depuis #1158, que `completer`
  enrichit sans jamais en retirer ;
- `maestro.outillage.exploration` — la **lecture du projet par le modèle**
  (#1158) : deux verbes, `LISTER` et `LIRE`, servis ici dans le périmètre et sans
  jamais suivre un lien, puis chaque constat du modèle confronté à ce qu'il a lu.
  Un modèle qui ne répond pas laisse l'analyse aux indices, et le dit ;
- `maestro.outillage.recommandation` — des constats à l'outillage proposé,
  chaque entrée avec sa raison et l'endroit du projet qui la justifie ;
- `maestro.outillage.clients` — ce que chaque client d'agents fait d'`AGENTS.md`
  (#1295), des faits datés et sourcés, et les clients que la conversation nomme :
  ils décident des **ponts**. Ceux du poste se trouvent hors du paquet
  (`maestro.clients_du_poste`), parce que lire une version lance un processus ;
- `maestro.outillage.contexte` — ce qu'un agent en reçoit, **dérivé du manifeste
  et borné à ce qu'il déclare** (docs/38 §5). C'est la moitié « transmis
  explicitement » de la frontière ; l'autre moitié, la porte qu'on ferme sur la
  configuration ambiante, vit dans `maestro.providers.claude` ;
- `maestro.outillage.redaction` — le **texte** de chaque fichier de l'arbre de
  docs/38 §3.6, rendu depuis les constats. Inerte, et **déterministe** : deux
  rédactions des mêmes constats rendent le même octet, sans quoi le cas
  « empreinte identique » de docs/38 §4.2 serait inatteignable ;
- `maestro.outillage.generation` — l'écriture dans un arbre et le **manifeste**,
  avec les quatre cas de docs/38 §4.2 : rien n'est jamais écrasé en silence ;
- `maestro.outillage.ecriture` — **où** cela s'écrit : le régime du projet
  (docs/24 §2.4), worktree à fusionner sous accord ou racine en place. Il
  prépare ; l'accord et la fusion restent à
  `maestro.controltower.validation` ;
- `maestro.outillage.verification` — **ce que valent les commandes qu'on écrit**
  (#1160) : chacune est jouée avant d'être écrite, dans une copie de l'arbre
  outillé, et son verdict — vérifiée, échouée avec sa sortie, ou à vérifier avec
  sa raison — va au texte, au manifeste et au rapport.

**Les promesses du paquet, et où elles tiennent.** *Lecture seule partout sauf
là où c'est le sujet* : seuls `generation` et `ecriture` ouvrent un fichier en
écriture, et uniquement sous la cible qu'on leur nomme, chaque chemin confronté
à la **frontière d'écriture** de `maestro.sandbox.en_place` — la même que celle
des agents (#839), jamais une seconde. *L'analyse n'exécute rien* : aucun module
n'importe `subprocess` — le VCS lui-même est lu dans `.git/HEAD` et
`.git/config` (`maestro.projets.racine.detecter_vcs`, #221) plutôt qu'obtenu
d'un `git remote`. Les processus que ce paquet fait lancer le sont par
`maestro.sandbox`, et à **l'écriture** seulement, après le geste qui la
déclenche : le Git qui monte un worktree (`maestro.sandbox.projet`), et depuis
#1160 les commandes du projet, jouées dans une **copie** de vérification
(`maestro.sandbox.verification`) — jamais dans la racine, jamais pendant une
analyse, et jamais une commande que la portée « projet » renvoie à une personne.
*Aucun lien symbolique suivi*, et le **périmètre déclaré du projet s'applique**
des quatre côtés : ni `.env` ni `**/secrets/**` ne sont ouverts, qu'on analyse,
qu'on écrive, qu'on transmette ou qu'on vérifie.

Ce que l'analyse rend est **servi par l'API** —
`GET /api/projets/{id}/outillage/analyse` — et sert de `source` au manifeste que
la génération écrit — `POST /api/projets/{id}/outillage/generation` —, toutes
deux via `maestro.controltower.outillage`.
"""

from __future__ import annotations

from maestro.outillage.analyse import analyser, completer, resume
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
from maestro.outillage.ecriture import (
    REGIME_BRANCHE,
    REGIME_EN_PLACE,
    Preparation,
    generer_outillage,
    nouvel_id_de_generation,
)
from maestro.outillage.exploration import (
    CADRE_LECTURE,
    Explorateur,
    lire_le_projet,
    sans_lecture,
)
from maestro.outillage.generation import (
    DOSSIER_REFUSES,
    ETATS_ECRITURE,
    Ecriture,
    Rapport,
    generer,
    portees_declarees,
)
from maestro.outillage.modele import (
    ETATS_ENTREE,
    ETATS_LECTURE,
    ORIGINES_COMMANDE,
    PREFIXE_ID,
    USAGES,
    VERSION_ANALYSE,
    Analyse,
    Bornes,
    Commande,
    ConstatEcarte,
    Constats,
    DossierScripts,
    Ecarte,
    Entree,
    Forge,
    Gestionnaire,
    Langage,
    Lecture,
    Parcours,
    Piece,
    Recommandation,
    Refus,
    nouvel_id,
)
from maestro.outillage.questionnaire import (
    AUCUN,
    SOURCE_CHOIX,
    SUJETS,
    Choix,
    Comprehension,
    ComprehensionIllisible,
    Option,
    QuestionOutillage,
    acquis_de,
    comprehension_depuis_texte,
    constats_depuis_choix,
    question_ouverte,
    recommandation_depuis_choix,
    resume_des_choix,
    source_manifeste_des_choix,
)
from maestro.outillage.recommandation import DOSSIER_SKILLS, SKILL_PAR_USAGE, recommander
from maestro.outillage.redaction import (
    GENERE_PAR,
    PORTEE_BLOC,
    PORTEE_FICHIER,
    Fichier,
    rediger,
)

#: ⚠ `Bornes` exporté ici est celui de l'**analyse** (`maestro.outillage.modele`).
#: Le contexte d'un agent a les siennes, qui ne bornent pas les mêmes choses :
#: elles s'importent de `maestro.outillage.contexte`, nommément. Deux `Bornes`
#: dans le même espace de noms se seraient servies l'une pour l'autre.
__all__ = [
    "AUCUN",
    "BALISE_DEBUT",
    "BALISE_FIN",
    "CADRE_LECTURE",
    "CHAMP_OUTILS",
    "CHEMIN_MANIFESTE",
    "DOSSIER_REFUSES",
    "DOSSIER_SKILLS",
    "ETATS_ECRITURE",
    "ETATS_ENTREE",
    "ETATS_LECTURE",
    "GENERE_PAR",
    "ORIGINES_COMMANDE",
    "PORTEE_BLOC",
    "PORTEE_FICHIER",
    "PREFIXE_ID",
    "REGIME_BRANCHE",
    "REGIME_EN_PLACE",
    "ROLES_TRANSMIS",
    "SKILL_PAR_USAGE",
    "SOURCE_CHOIX",
    "SUJETS",
    "USAGES",
    "VERSION_ANALYSE",
    "VERSION_MANIFESTE",
    "Analyse",
    "Bornes",
    "Choix",
    "Commande",
    "Comprehension",
    "ComprehensionIllisible",
    "ConstatEcarte",
    "Constats",
    "DossierScripts",
    "Ecarte",
    "Ecriture",
    "Entree",
    "Explorateur",
    "Fichier",
    "Forge",
    "Gestionnaire",
    "Langage",
    "Lecture",
    "NonTransmis",
    "Option",
    "OutillageDuProjet",
    "Parcours",
    "Piece",
    "Preparation",
    "QuestionOutillage",
    "Rapport",
    "Recommandation",
    "Refus",
    "SkillDuProjet",
    "acquis_de",
    "analyser",
    "completer",
    "comprehension_depuis_texte",
    "constats_depuis_choix",
    "generer",
    "generer_outillage",
    "lire_le_projet",
    "nouvel_id",
    "nouvel_id_de_generation",
    "outillage_du_projet",
    "portees_declarees",
    "question_ouverte",
    "recommandation_depuis_choix",
    "recommander",
    "rediger",
    "resume",
    "resume_des_choix",
    "sans_lecture",
    "source_manifeste_des_choix",
]
