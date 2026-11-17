"""Un livrable rejoué sur un **clone propre**, selon la grille du bilan de p5 (#1512).

    .venv/Scripts/python.exe -m maestro.scenarios.comparatif --rejouer seul=<dépôt>

Le bilan de « Le run livre un vrai projet » (2026-10-14, § 3.1) a comparé le
livrable de Maestro à celui d'une session seule en rejouant chacun, script par
script, sur un clone neuf. C'est ce rejeu qui a vu ce qu'aucun statut ne disait :
cinquante fichiers hors du formatage choisi, un typage qui ne passe que sur
l'arbre de l'agent, une intégration continue retenue à l'outillage et jamais
écrite, des commits hors de la convention. Ce module en fait une commande, la
même pour les deux côtés du banc comparatif (`maestro.scenarios.comparatif`).

## La grille

Dix lignes, celles du bilan, dans son ordre :

- **installation, lint, formatage, types, tests, build** : la commande que **le
  livrable déclare** pour elle, jouée sur le clone, rend `0` ;
- **pages servies** : le livrable servi par sa commande, chaque page qu'il déclare
  répond `200`, et une page qui ne doit pas exister répond `404` ;
- **CI écrite, convention de commit, outillage choisi suivi** : un modèle juge sur
  pièces, jamais un lexique (#746).

**Le clone, et lui seul.** Ce qu'une personne récupère d'un projet est ce qu'il a
commité (`maestro.scenarios.projets.cloner`) : ni ce que le run a laissé traîner
dans la racine, ni les dépendances qu'un agent a installées. Un livrable qui ne
se clone pas manque toutes ses lignes, et le dit.

**Les commandes viennent du livrable.** Le banc ne porte aucune table de piles :
un modèle lit ce que le dépôt déclare — ses scripts, son README, ses
instructions, son intégration continue — et nomme, ligne par ligne, la commande
que **le projet** donne pour elle ; l'exécution tranche (docs/41). Un projet qui
ne déclare rien pour une ligne la manque : « le projet ne dit pas comment
vérifier son typage » est exactement le défaut que le bilan a relevé. C'est la
règle de S12, où l'oracle ne devine pas le script des tests (`TRAVAIL_S12`) : ici
le livrable le dit, et c'est lui qu'on lit. Les deux côtés passent par la même
lecture, donc par le même biais s'il y en a un.

**Ce qui se constate se constate.** L'exécution d'abord, dans l'ordre d'une
personne qui découvre le dépôt : installer, puis vérifier, puis construire, puis
servir. Une installation qui échoue laisse les lignes suivantes **non jouées**
(elles n'avaient pas de quoi passer), un build qui échoue laisse les pages non
jouées, et une non jouée compte comme manquée. Les trois lignes jugées le sont
**après**, sur ce que le dépôt porte : un juge injoignable s'abstient (« non
jugé »), et une abstention ne compte ni pour ni contre.

**La durée d'une commande n'est jamais un verdict** (#1681). Une commande se joue
dans le délai que **le livrable** lui laisse : la limite de son intégration
continue, ou ce que ses instructions annoncent, que le lecteur de commandes nomme
avec son origine. Un livrable qui ne dit rien reçoit celui du banc
(`DELAI_DU_BANC_S`). Une commande sans retour dans son délai rend « non tranché » :
le banc s'abstient, et le dit. Une installation qui n'a pas fini ne bloque rien,
mais un échec derrière elle ne tranche pas non plus, puisque rien ne dit qu'il
vient du livrable. Un succès, lui, est tenu. Le dépôt de Maestro se met en route par
`bash scripts/setup.sh`, et sa suite compte des milliers de tests : ni l'une ni
l'autre ne tient dans un délai fixe, et le temps qu'elles prennent ne dit rien de
leur justesse.

**Une vérification se joue là où le livrable en rend le verdict** (#1695). Quand le
projet dit rendre un verdict dans un autre environnement que le poste (un autre
système, un conteneur) et déclare une commande qui l'y joue d'ici, le lecteur retient
celle-là. Sinon, il retient la variante du poste et nomme cet environnement
(`ELSEWHERE:`) : un échec sur le poste ne tranche pas, puisque rien ne dit qu'il vient
du livrable, et un succès est tenu. La suite de Maestro rend son verdict sous Linux
(`scripts/ci/pytest.sh`, #372). Jouée en natif sous Windows, des tests qui dépendent du
poste la faisaient manquer. Le système où tourne une intégration continue n'est pas une
telle déclaration : sans elle, un échec sur le poste reste un manque. C'est ce que le
banc existe pour voir, comme le typage du bilan de p5, qui ne passait que sur l'arbre de
l'agent.

**Une ligne que la personne a écartée est sans objet** (#1681). « Aucun formateur
appliqué au dépôt » écarte le formatage : un modèle lit les choix de la personne et
nomme les lignes qu'un choix exclut en toutes lettres (`lignes_ecartees`). Ces
lignes ne se jouent ni ne se jugent, et ne comptent ni pour ni contre. L'outillage
choisi, lui, ne s'écarte pas : ce sont les choix eux-mêmes. Les lignes écartées
dépendent des choix et non du livrable : le banc comparatif les lit une fois pour
les deux côtés.

## Ce que le modèle lit du dépôt

Les pièces sont bornées : la liste des fichiers suivis, le début de ceux de la racine
et des dossiers de configuration, les messages des commits. Un message se lit
**entier**, sujet, corps et pied : une convention qui exige une mention
(`Refs #<iid>`) la porte souvent dans le corps, qu'un sujet seul ne montre pas
(#1681). Un message trop long garde son début et sa fin. Sur un gros dépôt, la liste
est tronquée et les quotas s'épuisent avant un fichier qui compte (`ci.yml` au rejeu
de #1505, docs/51 § 3.2). Alors **chaque ligne nomme ce qu'elle doit lire** : avant
de lire les commandes et de juger, le modèle reçoit les pièces — et, la liste
tronquée, tous les dossiers suivis — et nomme, question par question, les fichiers
ou les motifs à lire. Le banc les résout contre la liste **entière** des fichiers
suivis, et seul un fichier suivi se lit. Aucune table de chemins : le modèle sait où
une intégration continue s'écrit, le dépôt dit s'il en porte une.

Le lecteur de commandes sait aussi **sur quel système** le clone se rejoue
(`PosteDuBanc.systeme`) : un projet qui déclare une commande pour Windows et une
pour Unix est rejoué par celle du poste.

## Le livrable d'un ticket, sur un dépôt existant (#1544)

Un livrable rejoué **contre une base** (`base`) est ce que `HEAD` ajoute à ce
commit : ses commits se jugent depuis elle (`base..HEAD`), et ce qu'il change depuis
elle est montré et lu d'abord. Sans base, le livrable est le dépôt entier — un projet
né du passage (p5, #1512). Une base que le livrable ne porte pas rend la grille
**injouable** : le banc a été mal appelé, le livrable n'y est pour rien.
"""

from __future__ import annotations

import fnmatch
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Protocol

from maestro.machine import systeme as systeme_du_poste
from maestro.outillage.verification import Delais
from maestro.sandbox import verification as execution
from maestro.scenarios.gestes import (
    PAGE_SERVIE,
    SANS_TERMINAL,
    Joueur,
    jouer_commande,
    servir,
    sonder_page,
)
from maestro.scenarios.juge import FORME, Avis
from maestro.scenarios.projets import (
    changements_depuis,
    cloner,
    commit_de,
    fichiers_suivis,
    instantaner,
    messages_des_commits,
)

AXE_INSTALLATION = "installation"
AXE_LINT = "lint"
AXE_FORMATAGE = "formatage"
AXE_TYPES = "types"
AXE_TESTS = "tests"
AXE_BUILD = "build"
AXE_PAGES = "pages"
AXE_CI = "ci"
AXE_COMMITS = "commits"
AXE_OUTILLAGE = "outillage"

#: Les lignes qui se tiennent par une commande du livrable, dans l'ordre où elles se
#: jouent : installer d'abord, construire en dernier — les pages ont besoin du build.
AXES_JOUES: tuple[str, ...] = (
    AXE_INSTALLATION,
    AXE_LINT,
    AXE_FORMATAGE,
    AXE_TYPES,
    AXE_TESTS,
    AXE_BUILD,
)

#: Les lignes qu'un modèle juge sur pièces.
AXES_JUGES: tuple[str, ...] = (AXE_CI, AXE_COMMITS, AXE_OUTILLAGE)

#: La grille entière, dans l'ordre du bilan (§ 3.1).
AXES: tuple[str, ...] = (*AXES_JOUES, AXE_PAGES, *AXES_JUGES)

#: Le nom de chaque ligne, tel que le rapport l'écrit.
LIBELLES: Mapping[str, str] = {
    AXE_INSTALLATION: "Installation",
    AXE_LINT: "Lint",
    AXE_FORMATAGE: "Formatage",
    AXE_TYPES: "Types",
    AXE_TESTS: "Tests",
    AXE_BUILD: "Build",
    AXE_PAGES: "Pages servies",
    AXE_CI: "CI écrite",
    AXE_COMMITS: "Convention de commit",
    AXE_OUTILLAGE: "Outillage choisi suivi",
}

#: Les quatre états d'une ligne. `NON_JOUE` compte comme manqué — le livrable n'a pas
#: permis de la jouer —, `NON_JUGE` ne compte pas : c'est le juge qui s'est abstenu.
TENU = "tenu"
MANQUE = "manque"
NON_JOUE = "non joué"
NON_JUGE = "non jugé"
#: Le banc n'a pas pu rejouer la ligne pour une raison du poste (un clone refusé) :
#: une abstention, comme `NON_JUGE`, jamais un manque du livrable.
INJOUABLE = "injouable"
#: La ligne s'est jouée sans trancher : sa commande n'a pas rendu la main dans son
#: délai, ou elle a échoué derrière une installation qui n'avait pas fini. Une durée
#: n'est pas un verdict (#1681) : c'est une abstention.
NON_TRANCHE = "non tranché"
#: La personne a écarté la ligne par un de ses choix (« aucun formateur ») : elle ne
#: se joue ni ne se juge, et ne compte ni pour ni contre (#1681).
SANS_OBJET = "sans objet"
#: Les états qui ne sont pas un verdict : ils sortent du dénominateur du score.
ABSTENTIONS: tuple[str, ...] = (NON_JUGE, INJOUABLE, NON_TRANCHE, SANS_OBJET)

#: Le délai d'une commande dont le livrable ne dit rien : ce que le banc lui laisse,
#: celui de la vérification du produit. Une commande qui le dépasse ne tranche pas.
DELAI_DU_BANC_S = Delais().commande_s

#: La page que le banc demande sans que le livrable l'ait déclarée : une application
#: web répond `404` à un chemin qu'elle ne connaît pas. Le bilan l'a vu manquer — une
#: fiche inconnue servie en `200` (« faux 404 »).
PAGE_ABSENTE_DU_BANC = "/page-absente-du-banc-maestro"
PAGE_ABSENTE = 404

#: Combien de pages déclarées le banc sonde au plus, et de pages absentes.
PAGES_MAX = 8
ABSENTES_MAX = 3

#: Ce que le juge et le lecteur voient du dépôt : la liste des fichiers suivis, les
#: fichiers de la racine et ceux des dossiers de configuration (`.github/…`), chacun
#: borné, et les sujets des derniers commits. De quoi reconnaître une pile et des
#: gestes, pas un dépôt recopié.
FICHIERS_LISTES = 200
CARACTERES_PAR_FICHIER = 3000
#: Deux quotas et non un : ceux de la racine, et ceux des dossiers de configuration.
#: Mesuré au premier passage réel (2026-10-15) : un quota commun, épuisé par vingt
#: fichiers de racine, laissait `.github/workflows/ci.yml` hors de la lecture, et le
#: juge a conclu à une CI absente faute de l'avoir lue.
FICHIERS_LUS = 12
FICHIERS_DE_CONFIGURATION_LUS = 8
OCTETS_LISIBLES = 64_000
COMMITS_LUS = 60
#: Un message de commit se lit entier jusqu'à cette longueur. Au-delà, son début et sa
#: fin : le sujet ouvre le message, et la mention qu'une convention exige le ferme
#: souvent (`Refs #<iid>`).
CARACTERES_PAR_MESSAGE = 1200
DEBUT_DE_MESSAGE = 700
#: La liste tronquée, les dossiers suivis sont tous nommés, avec leur compte de fichiers :
#: de quoi nommer un fichier que la liste ne montre pas.
DOSSIERS_LISTES = 400
#: Ce que le livrable change depuis sa base : la liste, et les premiers fichiers lus avant
#: les autres — c'est eux qu'on juge.
CHANGEMENTS_LISTES = 200
FICHIERS_CHANGES_LUS = 8
#: Les fichiers qu'une question nomme, lus en plus des autres pour elle seule.
FICHIERS_NOMMES_LUS = 6

#: Les en-têtes de la réponse du lecteur de commandes, une ligne chacun. Lire un champ
#: que le modèle a été chargé de remplir est une **forme**, comme `VERDICT:` dans le
#: juge (`maestro.scenarios.juge`) — jamais une recherche dans le texte du livrable.
CHAMPS_COMMANDES: Mapping[str, str] = {
    AXE_INSTALLATION: "INSTALLATION",
    AXE_LINT: "LINT",
    AXE_FORMATAGE: "FORMATAGE",
    AXE_TYPES: "TYPES",
    AXE_TESTS: "TESTS",
    AXE_BUILD: "BUILD",
}
CHAMP_SERVIR = "SERVIR"
CHAMP_PAGES = "PAGES"
CHAMP_ABSENTES = "ABSENTES"
CHAMP_DELAIS = "DELAIS"
#: Les lignes dont le projet dit rendre le verdict ailleurs que sur le poste, sans commande
#: qui l'y joue d'ici (#1695). Un nom neuf qui voyage naît en anglais (docs/55 § 3).
CHAMP_AILLEURS = "ELSEWHERE"
AUCUNE = "aucune"

#: Une ligne `CHAMP: valeur`. Des blancs **de ligne** seulement (`[ \t]`) : `\s`
#: traverse le saut de ligne, et un champ laissé vide avalerait alors la ligne suivante.
_LIGNE_CHAMP = re.compile(
    r"^[ \t]*(?P<champ>[A-Z]+)[ \t]*:[ \t]*(?P<valeur>.*?)[ \t]*$", re.MULTILINE
)

#: Un délai du champ `DELAIS:` : `CHAMP=secondes (d'où il vient)`. L'origine est entre
#: parenthèses et peut porter des virgules : on lit les délais un à un, sans découper
#: le champ sur ses virgules.
_DELAI = re.compile(
    r"(?P<champ>[A-Z]+)[ \t]*=[ \t]*(?P<secondes>\d+(?:[.,]\d+)?)[ \t]*s?\b"
    r"(?:[ \t]*\((?P<origine>[^()]*)\))?"
)

#: Un verdict du champ `ELSEWHERE:` : `CHAMP=où il se rend (d'où le lecteur le tient)`. L'endroit
#: ne porte ni virgule ni parenthèse ; l'origine, entre parenthèses, peut porter des virgules.
_AILLEURS = re.compile(
    r"(?P<champ>[A-Z]+)[ \t]*=[ \t]*(?P<systeme>[^=(),]*[^=(),\s])"
    r"(?:[ \t]*\((?P<origine>[^()]*)\))?"
)

SYSTEME_COMMANDES = (
    "Tu lis un dépôt de logiciel et tu nommes, pour chaque vérification, la commande que "
    "le projet lui-même déclare pour la jouer : dans ses scripts, son README, ses "
    "instructions ou son intégration continue. Tu n'inventes rien : une vérification que "
    f"le projet ne déclare pas reçoit « {AUCUNE} ».\n"
    "Une commande vérifie sans modifier les fichiers : si le projet ne déclare que la forme "
    "qui réécrit (un formateur en écriture, un linter qui corrige), donne la forme qui "
    "vérifie du même outil, tel que le projet l'installe. N'ajoute aucune option qui "
    "assouplirait la vérification.\n"
    "Les commandes se jouent par bash, depuis la racine d'un clone neuf du dépôt, l'une "
    "après l'autre dans l'ordre ci-dessous, sur le système qu'on te donne. Chacune se joue "
    "telle que tu la donnes, sans rien y ajouter : donne-la entière, avec les arguments que "
    "son usage exige pour jouer la vérification sur tout le projet. Quand le projet "
    "déclare une même commande en plusieurs variantes selon le système, retiens celle de "
    "ce système. Le serveur reçoit son port dans la variable d'environnement PORT.\n"
    "Le délai d'une commande est celui que le projet laisse à la vérification qu'elle joue. "
    "Si son intégration continue joue cette vérification, même par une autre commande que "
    "celle que tu retiens (les tests avec d'autres options, une installation par un autre "
    "outil), c'est la limite de temps du job ou de l'étape qui la joue. Quand le projet n'en "
    "pose aucune, c'est la limite par défaut de cet hébergement. Sinon, c'est la durée que "
    "ses instructions annoncent pour elle. Une vérification que ni son intégration continue "
    "ni ses instructions ne bornent n'a pas de délai : ne lui en invente pas.\n"
    "Une vérification se joue là où le projet en rend le verdict. Quand le projet dit qu'il "
    "rend le verdict d'une vérification dans un autre environnement que ce système (un autre "
    "système d'exploitation, un conteneur) et qu'il déclare une commande qui l'y joue depuis "
    "ce système (un script qui lance son conteneur, par exemple), retiens cette commande-là "
    "plutôt que la variante qui se joue directement ici. S'il n'en déclare aucune, retiens "
    f"celle qui se joue ici et nomme cet environnement sous {CHAMP_AILLEURS}. Le système où "
    "tourne son intégration continue ne suffit pas : il faut que le projet dise que ce "
    "verdict se rend là-bas et pas ici.\n"
    "Réponds par exactement ces lignes, et rien d'autre :\n"
    f"{CHAMPS_COMMANDES[AXE_INSTALLATION]}: la commande qui installe les dépendances, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_LINT]}: la commande du linter, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_FORMATAGE]}: la commande qui vérifie le formatage, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_TYPES]}: la commande qui vérifie les types, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_TESTS]}: la commande des tests, ou {AUCUNE}\n"
    f"{CHAMPS_COMMANDES[AXE_BUILD]}: la commande qui construit le livrable, ou {AUCUNE}\n"
    f"{CHAMP_SERVIR}: la commande qui sert l'application une fois construite, ou {AUCUNE}\n"
    f"{CHAMP_PAGES}: les chemins que l'application sert, séparés par des virgules "
    f"(au plus {PAGES_MAX}, un exemple réel par route paramétrée), ou {AUCUNE}\n"
    f"{CHAMP_ABSENTES}: des chemins qui doivent répondre 404, par exemple un contenu "
    f"inconnu sous une route paramétrée (au plus {ABSENTES_MAX}), ou {AUCUNE}\n"
    f"{CHAMP_DELAIS}: pour chaque commande ci-dessus que le projet borne, `CHAMP=secondes "
    f"(d'où vient ce délai)`, séparés par des virgules, ou {AUCUNE}\n"
    f"{CHAMP_AILLEURS}: pour chaque commande ci-dessus dont le projet dit rendre le verdict "
    "ailleurs que sur ce système sans en déclarer une qui l'y joue d'ici, `CHAMP=où ce "
    f"verdict se rend (d'où tu le tiens)`, séparés par des virgules, ou {AUCUNE}"
)

#: Les lignes qu'un choix de la personne peut écarter, sous le champ que le modèle nomme,
#: et ce que chacune vérifie. L'outillage suivi ne s'écarte pas : il juge les choix
#: eux-mêmes.
CHAMPS_ECARTABLES: Mapping[str, str] = {
    **CHAMPS_COMMANDES,
    AXE_PAGES: CHAMP_PAGES,
    AXE_CI: "CI",
    AXE_COMMITS: "COMMITS",
}
VERIFICATIONS_ECARTABLES: Mapping[str, str] = {
    AXE_INSTALLATION: "installer les dépendances du projet",
    AXE_LINT: "passer le linter",
    AXE_FORMATAGE: "vérifier le formatage du code",
    AXE_TYPES: "vérifier les types",
    AXE_TESTS: "jouer les tests",
    AXE_BUILD: "construire le livrable",
    AXE_PAGES: "servir l'application et en sonder les pages",
    AXE_CI: "porter une intégration continue écrite",
    AXE_COMMITS: "écrire ses messages de commit dans une convention",
}
CHAMP_ECARTEES = "ECARTEES"
CHAMP_POURQUOI = "POURQUOI"

SYSTEME_ECARTEES = (
    "Tu lis les choix d'outillage d'une personne et tu nommes les vérifications qu'elle a "
    "écartées : celles qu'un de ses choix exclut en toutes lettres. Par exemple, « aucun "
    "formateur » écarte la vérification du formatage, et « pas d'intégration continue » "
    "écarte celle de l'intégration continue. Un choix qui ne dit rien d'une vérification ne "
    "l'écarte pas, et un outil choisi ne l'écarte jamais.\n"
    "Réponds par exactement ces deux lignes, et rien d'autre :\n"
    f"{CHAMP_ECARTEES}: les champs des vérifications écartées, séparés par des virgules, "
    f"ou {AUCUNE}\n"
    f"{CHAMP_POURQUOI}: une phrase qui cite, pour chacune, le choix qui l'écarte"
)

#: Les questions pour lesquelles le modèle nomme ce qu'il faut lire, et le champ de sa
#: réponse : les commandes du projet, et les deux lignes jugées sur le dépôt. Les commits
#: ne se lisent pas dans un fichier.
LECTURE_COMMANDES = "commandes"
QUESTIONS_DE_LECTURE: Mapping[str, tuple[str, str]] = {
    LECTURE_COMMANDES: (
        "COMMANDES",
        "quelles commandes le projet déclare-t-il pour installer ses dépendances, se "
        "vérifier (lint, formatage, types, tests), se construire et se servir ?",
    ),
    AXE_CI: (
        "CI",
        "le dépôt porte-t-il une intégration continue écrite, qui joue ses vérifications ?",
    ),
    AXE_OUTILLAGE: (
        "OUTILLAGE",
        "le livrable suit-il chacun des choix d'outillage de la personne ?",
    ),
}
AUCUN = "aucun"

SYSTEME_LECTURES = (
    "Tu prépares la relecture d'un dépôt de logiciel. On te montre la liste de ses fichiers "
    "suivis — tronquée sur un gros dépôt, ses dossiers étant alors tous nommés — et le "
    "début de quelques-uns. Pour chaque question qu'on te pose, nomme les fichiers suivis "
    "qu'il faut lire pour y répondre et dont le contenu n'est pas déjà montré : des chemins "
    "exacts, ou des motifs (`dossier/*.yml`, `dossier/`). Tu ne réponds pas aux questions.\n"
    "Réponds par une ligne par question, sous le champ qu'elle porte : "
    f"`CHAMP: chemins séparés par des virgules`, ou `CHAMP: {AUCUN}`, et rien d'autre."
)

#: Les trois jugements disent ce qui rend **non** en toutes lettres (« c'est non si… ») :
#: une formule comme « ne compte pas : des messages écrits par un outil » se lit dans
#: les deux sens. Mesuré au premier passage réel (2026-10-15), le juge des commits en a
#: conclu qu'il fallait **écarter** les commits « Maestro : … », et non les compter.
SYSTEME_CI = (
    "Tu juges une seule chose : ce dépôt porte-t-il une intégration continue écrite, qui "
    "joue les vérifications du projet (au moins ses tests ou sa construction) sur "
    "l'hébergement que la personne a choisi ?\n"
    "C'est oui si un fichier d'intégration continue est commité dans le dépôt et lance "
    "réellement des vérifications. C'est non si l'intégration n'est qu'une intention "
    "écrite dans un README ou des instructions, si elle est prévue mais absente, ou si son "
    "fichier est vide ou ne joue rien.\n" + FORME
)

SYSTEME_COMMITS = (
    "Tu juges une seule chose : les messages de commit qu'on te montre suivent-ils la "
    "convention que la personne a choisie ?\n"
    "Chaque message est montré entier, avec son sujet, son corps et son pied, et la "
    "convention se juge sur le message entier. Une mention qu'elle exige (une référence, "
    "une signature) compte où que le message la porte, et elle manque si le message ne la "
    "porte nulle part.\n"
    "**Chaque commit compte**, quel qu'en soit l'auteur : une personne, un agent ou un "
    "outil qui écrit ses propres messages. Aucun ne s'écarte du compte. C'est oui si la "
    "quasi-totalité des messages suit la convention. C'est non si des commits dans une autre "
    "forme sont plus que de rares exceptions — y compris ceux qu'un outil a écrits —, si la "
    "convention n'est suivie que par une partie des commits, ou s'il n'y a aucun "
    "commit.\n" + FORME
)

SYSTEME_OUTILLAGE = (
    "Tu juges une seule chose : ce livrable suit-il chacun des choix d'outillage que la "
    "personne a faits (langage, cadre, gestionnaire de paquets, tests, vérification du "
    "code, formatage, hébergement, intégration continue) ?\n"
    "C'est oui si chaque outil choisi se retrouve dans le dépôt, configuré et utilisable. "
    "C'est non si un seul outil choisi est absent ou remplacé par un autre : dis lequel. "
    "Une **convention** choisie (messages de commit, nommage) n'est pas un outil : elle "
    "se juge à part, sur les commits eux-mêmes, et aucun outil qui la force n'est exigé "
    "ici. Tu ne vérifies pas que les commandes passent, c'est constaté à part en les "
    "exécutant.\n" + FORME
)


class Lecteur(Protocol):
    """Ce que la grille demande à un modèle : lire des commandes, juger une ligne."""

    def rediger(self, systeme: str, prompt: str) -> str: ...

    def juger(self, systeme: str, prompt: str) -> Avis: ...


def systeme_du_rejeu() -> str:
    """Le système de ce poste et le bash qui y joue les commandes — `Windows 11 (…), par …`."""
    bash = "bash (Git Bash)" if sys.platform == "win32" else "bash"
    return f"{systeme_du_poste()}, commandes jouées par {bash}"


@dataclass
class PosteDuBanc:
    """Le poste où le livrable se rejoue : jouer une commande, sonder une page, attendre.

    Les vrais gestes par défaut — le bash des agents (`jouer_commande`), une vraie
    requête (`sonder_page`) ; les tests les doublent. Il satisfait le `Poste` de
    `maestro.scenarios.gestes.servir`, sans parler à aucune API. `systeme` est ce que
    le lecteur de commandes sait du poste, pour retenir la variante qui s'y joue.
    """

    joueur: Joueur = jouer_commande
    sonde: Callable[[str], int | None] = sonder_page
    horloge: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    systeme: str = field(default_factory=systeme_du_rejeu)

    def jouer(self, commande: str, dossier: Path, delai_s: float) -> execution.Execution:
        """Joue `commande` dans `dossier`."""
        return self.joueur(commande, dossier, delai_s)

    def sonder_la_page(self, url: str) -> int | None:
        """Le statut que rend `url`, `None` si personne ne répond."""
        return self.sonde(url)


@dataclass(frozen=True)
class Ligne:
    """Une ligne de la grille : son état, ce qui le dit, la commande jouée s'il y en a une."""

    axe: str
    etat: str
    detail: str
    commande: str = ""
    duree_s: float | None = None

    @property
    def tenue(self) -> bool:
        """La ligne est-elle tenue ?"""
        return self.etat == TENU

    @property
    def jugee(self) -> bool:
        """A-t-elle un verdict ? Une abstention — du juge, du poste ou du délai — n'en est pas
        un, et une ligne écartée par la personne non plus."""
        return self.etat not in ABSTENTIONS

    def to_dict(self) -> dict[str, Any]:
        """La ligne en JSON — la forme du rapport."""
        return {
            "axe": self.axe,
            "libelle": LIBELLES.get(self.axe, self.axe),
            "etat": self.etat,
            "detail": self.detail,
            "commande": self.commande,
            "duree_s": None if self.duree_s is None else round(self.duree_s, 3),
        }


@dataclass(frozen=True)
class Delai:
    """Le délai que le livrable laisse à une commande, et d'où le lecteur le tient."""

    secondes: float
    origine: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"secondes": self.secondes, "origine": self.origine}


@dataclass(frozen=True)
class Ailleurs:
    """Où le livrable dit rendre le verdict d'une ligne, quand ce n'est pas le poste et que
    rien de ce qu'il déclare ne l'y joue d'ici — et d'où le lecteur le tient (#1695)."""

    systeme: str
    origine: str = ""

    def en_mots(self) -> str:
        """Pourquoi un échec de cette ligne sur le poste ne tranche pas."""
        origine = f" ({self.origine})" if self.origine else ""
        return (
            f"le livrable rend ce verdict sous {self.systeme}{origine}, pas sur ce poste : "
            "rien ne dit que l'échec vient de lui"
        )

    def to_dict(self) -> dict[str, Any]:
        return {"system": self.systeme, "origin": self.origine}


@dataclass(frozen=True)
class Commandes:
    """Ce que le lecteur a lu dans le livrable : une commande par ligne jouée, ses pages, le
    délai que le livrable laisse à celles qu'il borne, et les lignes dont il rend le verdict
    ailleurs que sur le poste."""

    par_axe: Mapping[str, str] = field(default_factory=dict)
    servir: str = ""
    pages: tuple[str, ...] = ()
    absentes: tuple[str, ...] = ()
    delais: Mapping[str, Delai] = field(default_factory=dict)
    ailleurs: Mapping[str, Ailleurs] = field(default_factory=dict)
    brut: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Les commandes lues en JSON — la réponse brute du lecteur comprise."""
        return {
            "par_axe": dict(self.par_axe),
            "servir": self.servir,
            "pages": list(self.pages),
            "absentes": list(self.absentes),
            "delais": {axe: delai.to_dict() for axe, delai in self.delais.items()},
            "elsewhere": {axe: verdict.to_dict() for axe, verdict in self.ailleurs.items()},
            "brut": self.brut,
        }


@dataclass(frozen=True)
class Ecartees:
    """Les lignes que la personne a écartées par ses choix, et ce qui le dit."""

    axes: frozenset[str] = frozenset()
    pourquoi: str = ""

    def en_mots(self) -> str:
        """Ce qu'une ligne écartée dit dans la grille."""
        return f"écartée par la personne : {self.pourquoi or 'sans raison donnée'}"


@dataclass(frozen=True)
class Grille:
    """Le rejeu d'un livrable : où il a été cloné, et l'état de chaque ligne.

    `base` est le commit complet d'où le livrable part, `""` s'il est le dépôt entier.
    """

    depot: str
    copie: str
    lignes: tuple[Ligne, ...]
    commandes: Commandes | None = None
    base: str = ""

    @property
    def tenues(self) -> int:
        """Combien de lignes sont tenues."""
        return sum(1 for ligne in self.lignes if ligne.tenue)

    @property
    def jugees(self) -> int:
        """Combien de lignes ont un verdict — le dénominateur du score."""
        return sum(1 for ligne in self.lignes if ligne.jugee)

    def ligne(self, axe: str) -> Ligne | None:
        """La ligne d'un axe, si la grille la porte."""
        return next((ligne for ligne in self.lignes if ligne.axe == axe), None)

    def to_dict(self) -> dict[str, Any]:
        """La grille en JSON — la forme du rapport."""
        return {
            "depot": self.depot,
            "copie": self.copie,
            "base": self.base,
            "tenues": self.tenues,
            "jugees": self.jugees,
            "lignes": [ligne.to_dict() for ligne in self.lignes],
            "commandes": None if self.commandes is None else self.commandes.to_dict(),
        }


def rejouer(
    depot: Path,
    copie: Path,
    *,
    choix: Sequence[str],
    lecteur: Lecteur,
    poste: PosteDuBanc | None = None,
    axes: Sequence[str] = AXES,
    trace: Callable[[str, str], None] = lambda _libelle, _detail: None,
    versionne: bool = True,
    base: str = "",
    ecartees: Ecartees | None = None,
) -> Grille:
    """Clone `depot` dans `copie` et y joue la grille — l'état de chaque ligne de `axes`.

    `choix` sont les choix d'outillage de la personne, en mots : ce que le juge
    confronte au livrable. Sans choix, les lignes qui en dépendent (convention de
    commit, outillage suivi) ne se jugent pas. `trace` reçoit chaque étape, pour le
    déroulé du rapport et l'écran du passage.

    `ecartees` sont les lignes que ces choix écartent (`lignes_ecartees`). Sans elles,
    la grille les lit ici. Le banc comparatif les lit une fois et les passe aux deux
    côtés, puisqu'elles ne dépendent que des choix.

    `versionne` dit si la personne a demandé un dépôt Git. Demandé, un livrable qui
    n'en est pas un manque toutes ses lignes ; non demandé (#1513), il se rejoue comme
    son dossier, moins ce qu'il déclare ignorer (`instantaner`) : la personne qui ne
    code pas récupère un dossier, et n'a jamais dit qu'il fallait le versionner.

    `base` est le commit d'où part le livrable d'un ticket sur un dépôt existant, tel
    que `depot` le nomme (un sha, `de9564ce~1`) : le livrable est ce que `HEAD` lui
    ajoute (#1544).
    """
    poste = poste or PosteDuBanc()
    sous_git = (depot / ".git").exists()
    if not sous_git and versionne:
        return _toutes(depot, copie, axes, MANQUE, "le livrable n'est pas un dépôt Git")
    try:
        commit_base = _base(depot, base, sous_git)
        if sous_git:
            cloner(depot, copie)
        else:
            instantaner(depot, copie.with_name(f"{copie.name}-instantane"), copie)
        if commit_base and commit_de(copie, commit_base) != commit_base:
            raise _BaseInconnue(f"la base `{base}` n'est pas dans l'histoire clonée du livrable")
    except _BaseInconnue as exc:
        return _toutes(depot, copie, axes, INJOUABLE, str(exc))
    except OSError as exc:
        # Un dépôt qui ne se clone pas dit une panne du poste, pas un défaut du livrable :
        # mesuré le 2026-10-15, le poste a refusé de créer `git` (0xC0000142).
        return _toutes(depot, copie, axes, INJOUABLE, f"le clone a échoué : {exc}")
    trace(
        "livrable cloné" if sous_git else "dossier livré cloné par instantané",
        f"{depot} → {copie}" + (f", depuis la base {commit_base[:12]}" if commit_base else ""),
    )
    ecartees = _ecartees_du_rejeu(lecteur, choix, axes, ecartees, trace)
    pieces = _pieces(copie, commit_base)

    joues = [axe for axe in AXES_JOUES if axe in axes]
    lit_les_commandes = any(
        axe in axes and axe not in ecartees.axes for axe in (*AXES_JOUES, AXE_PAGES)
    )
    questions = _questions(axes, choix, ecartees, lit_les_commandes)
    nommes, detail = _lectures(copie, lecteur, choix, pieces, questions)
    if questions:
        trace("fichiers nommés pour chaque ligne", detail)

    commandes: Commandes | None = None
    illisible = ""
    if lit_les_commandes:
        commandes, illisible = _lire_commandes(
            lecteur, choix, pieces, poste.systeme, nommes.get(LECTURE_COMMANDES, ())
        )
        trace("commandes lues dans le livrable", illisible or _commandes_en_mots(commandes))
    # Sans commande à lire, chaque ligne jouée est écartée : elle se range sans se jouer.
    lignes = _jouer_les_commandes(
        copie, commandes or Commandes(), illisible, joues, axes, poste, trace, ecartees
    )

    for axe in (a for a in AXES_JUGES if a in axes):
        lignes[axe] = _juger(axe, lecteur, choix, pieces, nommes.get(axe, ()), ecartees)
        trace(f"ligne jugée : {LIBELLES[axe]}", f"{lignes[axe].etat} — {lignes[axe].detail}")

    return Grille(
        depot=str(depot),
        copie=str(copie),
        lignes=tuple(lignes[axe] for axe in AXES if axe in lignes),
        commandes=commandes,
        base=commit_base,
    )


def _ecartees_du_rejeu(
    lecteur: Lecteur,
    choix: Sequence[str],
    axes: Sequence[str],
    ecartees: Ecartees | None,
    trace: Callable[[str, str], None],
) -> Ecartees:
    """Les lignes écartées que l'appelant a lues, ou lues ici — et dites au déroulé."""
    if ecartees is None:
        ecartees = lignes_ecartees(lecteur, choix, axes)
    if choix:
        noms = ", ".join(LIBELLES[axe] for axe in AXES if axe in ecartees.axes)
        trace("lignes écartées par la personne", f"{noms or AUCUNE} — {ecartees.pourquoi}")
    return ecartees


def _questions(
    axes: Sequence[str], choix: Sequence[str], ecartees: Ecartees, lit_les_commandes: bool
) -> list[str]:
    """Les questions pour lesquelles le modèle nomme ce qu'il faut lire — aucune pour une
    ligne que la personne a écartée."""
    return [
        question
        for question, posee in (
            (LECTURE_COMMANDES, lit_les_commandes),
            (AXE_CI, AXE_CI in axes and AXE_CI not in ecartees.axes),
            (AXE_OUTILLAGE, AXE_OUTILLAGE in axes and bool(choix)),
        )
        if posee
    ]


class _BaseInconnue(Exception):
    """La base demandée ne désigne aucun commit du livrable : le banc a été mal appelé."""


def _base(depot: Path, base: str, sous_git: bool) -> str:
    """Le commit complet que `base` nomme dans `depot`, `""` sans base — lève `_BaseInconnue`."""
    if not base:
        return ""
    if not sous_git:
        raise _BaseInconnue(
            f"la base `{base}` se lit dans l'histoire d'un dépôt Git, "
            "et le livrable n'en est pas un"
        )
    commit = commit_de(depot, base)
    if commit is None:
        raise _BaseInconnue(f"la base `{base}` ne désigne aucun commit du livrable")
    return commit


def _toutes(depot: Path, copie: Path, axes: Sequence[str], etat: str, detail: str) -> Grille:
    """Une grille dont chaque ligne a le même état, pour la même raison."""
    return Grille(
        depot=str(depot),
        copie=str(copie),
        lignes=tuple(Ligne(axe, etat, detail) for axe in AXES if axe in axes),
    )


# --- Ce que le dépôt porte --------------------------------------------------


@dataclass(frozen=True)
class Pieces:
    """Ce que le lecteur et le juge voient du clone, déjà borné.

    `fichiers` est la liste **entière** des fichiers suivis : seul le texte la tronque, et
    c'est contre elle que se résout ce qu'une ligne nomme. `commits` sont les messages
    entiers des commits, du plus récent au plus ancien. `base` et `changements` disent ce
    que le livrable change depuis sa base — vides sans base.
    """

    fichiers: tuple[str, ...]
    contenus: tuple[tuple[str, str], ...]
    commits: tuple[str, ...]
    base: str = ""
    changements: tuple[tuple[str, str], ...] = ()

    def en_texte(self, nommes: Sequence[tuple[str, str]] = ()) -> str:
        """Les pièces en un bloc : la liste des fichiers, ce que le livrable change, ce qui est lu.

        `nommes` sont les fichiers que la question a nommés, lus pour elle seule.
        """
        liste = "\n".join(self.fichiers[:FICHIERS_LISTES]) or "(aucun fichier suivi)"
        reste = len(self.fichiers) - FICHIERS_LISTES
        blocs = []
        if reste > 0:
            liste += f"\n… et {reste} autre(s)"
            blocs.append(f"Dossiers suivis, et leur nombre de fichiers :\n{self._dossiers()}")
        if self.base:
            changes = "\n".join(
                f"{etat} {chemin}" for etat, chemin in self.changements[:CHANGEMENTS_LISTES]
            )
            if len(self.changements) > CHANGEMENTS_LISTES:
                changes += f"\n… et {len(self.changements) - CHANGEMENTS_LISTES} autre(s)"
            blocs.append(
                f"Ce que le livrable change depuis sa base `{self.base[:12]}` :\n"
                f"{changes or '(aucun fichier changé)'}"
            )
        lus = "\n".join(
            f'<fichier chemin="{chemin}">\n{contenu}\n</fichier>'
            for chemin, contenu in (*self.contenus, *nommes)
        )
        return "\n\n".join(
            [f"Fichiers suivis :\n{liste}", *blocs, f"Fichiers lus :\n{lus or '(aucun)'}"]
        )

    def _dossiers(self) -> str:
        """Chaque dossier qui porte un fichier suivi, avec leur nombre."""
        comptes = Counter(chemin.rsplit("/", 1)[0] for chemin in self.fichiers if "/" in chemin)
        noms = sorted(comptes)
        texte = "\n".join(f"{nom}/ ({comptes[nom]})" for nom in noms[:DOSSIERS_LISTES])
        if len(noms) > DOSSIERS_LISTES:
            texte += f"\n… et {len(noms) - DOSSIERS_LISTES} autre(s)"
        return texte


def _pieces(copie: Path, base: str = "") -> Pieces:
    """Les fichiers suivis du clone, ceux qu'on lit, et les messages des commits.

    Lus : ce que le livrable change depuis sa base, s'il en a une — c'est lui qu'on
    juge —, puis les fichiers de la racine, puis ceux des dossiers de configuration qui
    commencent par un point (`.github/…`) — là où un dépôt dit comment il se
    construit et se vérifie —, les moins profonds d'abord, chacun borné et sans les
    gros fichiers (un verrou de dépendances ne dit rien qu'un manifeste ne dise).
    """
    try:
        suivis = fichiers_suivis(copie)
    except OSError:
        suivis = ()
    try:
        commits = messages_des_commits(copie, nombre=COMMITS_LUS, depuis=base)
    except OSError:
        commits = ()
    try:
        changements = changements_depuis(copie, base) if base else ()
    except OSError:
        changements = ()
    fichiers = tuple(sorted(suivis, key=lambda chemin: (chemin.count("/"), chemin)))
    presents = set(fichiers)
    changes = [chemin for _etat, chemin in changements if chemin in presents]
    racine = [chemin for chemin in fichiers if "/" not in chemin]
    configuration = [
        chemin
        for chemin in fichiers
        if "/" in chemin and chemin.split("/", 1)[0].startswith(".")
    ]
    contenus: list[tuple[str, str]] = []
    for chemins, maximum in (
        (changes, FICHIERS_CHANGES_LUS),
        (racine, FICHIERS_LUS),
        (configuration, FICHIERS_DE_CONFIGURATION_LUS),
    ):
        deja = {chemin for chemin, _ in contenus}
        contenus += _lus(copie, [chemin for chemin in chemins if chemin not in deja], maximum)
    return Pieces(
        fichiers=fichiers,
        contenus=tuple(contenus),
        commits=commits,
        base=base,
        changements=changements,
    )


def _lus(copie: Path, chemins: Sequence[str], maximum: int) -> list[tuple[str, str]]:
    """Les `maximum` premiers fichiers lisibles de `chemins`, et leur début."""
    lus: list[tuple[str, str]] = []
    for chemin in chemins:
        if len(lus) >= maximum:
            break
        texte = _lire(copie / chemin)
        if texte is not None:
            lus.append((chemin, texte))
    return lus


def _lire(chemin: Path) -> str | None:
    """Le début d'un fichier texte — `None` s'il est trop gros, binaire ou illisible."""
    try:
        if chemin.stat().st_size > OCTETS_LISIBLES:
            return None
        texte = chemin.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(texte) > CARACTERES_PAR_FICHIER:
        return texte[:CARACTERES_PAR_FICHIER] + "\n[…]"
    return texte


def _lectures(
    copie: Path,
    lecteur: Lecteur,
    choix: Sequence[str],
    pieces: Pieces,
    questions: Sequence[str],
) -> tuple[dict[str, tuple[tuple[str, str], ...]], str]:
    """Les fichiers que chaque question nomme, lus — et ce qui l'a été, en mots.

    Une nomination qui n'a pas eu lieu (fournisseur injoignable) ne lit rien de plus :
    chaque ligne se juge alors sur les seules pièces communes.
    """
    if not questions:
        return {}, ""
    demandes = "\n".join(
        f"{QUESTIONS_DE_LECTURE[question][0]}: {QUESTIONS_DE_LECTURE[question][1]}"
        for question in questions
    )
    prompt = (
        f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
        f"Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte()}</depot>\n"
        f"Les questions, chacune sous son champ :\n<questions>\n{demandes}\n</questions>\n"
        "Quels fichiers faut-il lire pour répondre à chacune ?"
    )
    try:
        brut = lecteur.rediger(SYSTEME_LECTURES, prompt)
    except Exception as exc:  # fournisseur injoignable, quota : rien de plus n'est lu
        return {}, f"aucun fichier nommé : {exc}"
    champs = _champs(brut)
    deja = {chemin for chemin, _ in pieces.contenus}
    nommes: dict[str, tuple[tuple[str, str], ...]] = {}
    for question in questions:
        valeur = champs.get(QUESTIONS_DE_LECTURE[question][0], "")
        motifs = [
            motif
            for motif in (morceau.strip().strip("`") for morceau in valeur.split(","))
            if motif and motif.strip(" .").lower() != AUCUN
        ]
        designes = _designes(motifs, pieces.fichiers, deja)
        nommes[question] = tuple(_lus(copie, designes, FICHIERS_NOMMES_LUS))
    detail = " ; ".join(
        f"{QUESTIONS_DE_LECTURE[question][0]} : "
        + (", ".join(chemin for chemin, _ in nommes[question]) or AUCUN)
        for question in questions
    )
    return nommes, detail


def _designes(motifs: Sequence[str], fichiers: Sequence[str], deja: Collection[str]) -> list[str]:
    """Les fichiers suivis que `motifs` désignent, dans leur ordre, sans ceux déjà lus.

    Un motif est un chemin exact, un dossier (`dossier/`) ou un motif de shell
    (`dossier/*.yml`). Seul un fichier suivi se désigne : un chemin hors du dépôt, ou
    qu'il ne suit pas, n'en désigne aucun.
    """
    retenus: list[str] = []
    for brut in motifs:
        motif = brut.removeprefix("./").lstrip("/")
        if not motif:
            continue
        dossier = motif.rstrip("/") + "/"
        for chemin in fichiers:
            if chemin in deja or chemin in retenus:
                continue
            if chemin == motif or chemin.startswith(dossier) or fnmatch.fnmatchcase(chemin, motif):
                retenus.append(chemin)
    return retenus


# --- Les commandes ----------------------------------------------------------


def _lire_commandes(
    lecteur: Lecteur,
    choix: Sequence[str],
    pieces: Pieces,
    systeme: str,
    nommes: Sequence[tuple[str, str]] = (),
) -> tuple[Commandes, str]:
    """Ce que le livrable déclare, ligne par ligne — et pourquoi rien, si la lecture a échoué."""
    prompt = (
        f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
        f"Le système où le clone se rejoue :\n<systeme>{systeme}</systeme>\n"
        f"Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte(nommes)}</depot>\n"
        "Quelles commandes ce projet déclare-t-il ?"
    )
    try:
        brut = lecteur.rediger(SYSTEME_COMMANDES, prompt)
    except Exception as exc:  # fournisseur injoignable, quota : la lecture n'a pas eu lieu
        return Commandes(), f"les commandes du livrable n'ont pas pu être lues : {exc}"
    return commandes_depuis(brut), ""


def _champs(brut: str) -> dict[str, str]:
    """Les champs `CHAMP: valeur` d'une réponse du lecteur — le premier de chaque nom fait foi."""
    champs: dict[str, str] = {}
    for trouve in _LIGNE_CHAMP.finditer(brut or ""):
        champs.setdefault(trouve.group("champ"), trouve.group("valeur").strip().strip("`"))
    return champs


def commandes_depuis(brut: str) -> Commandes:
    """Les commandes lues dans la réponse du lecteur — un champ absent vaut « aucune »."""
    champs = _champs(brut)

    def valeur(champ: str) -> str:
        texte = champs.get(champ, "").strip()
        return "" if texte.strip(" .").lower() == AUCUNE else texte

    def chemins(champ: str, maximum: int) -> tuple[str, ...]:
        vus: list[str] = []
        for morceau in valeur(champ).split(","):
            chemin = morceau.strip().strip("`")
            if chemin.startswith("/") and chemin not in vus:
                vus.append(chemin)
        return tuple(vus[:maximum])

    return Commandes(
        par_axe={axe: valeur(champ) for axe, champ in CHAMPS_COMMANDES.items()},
        servir=valeur(CHAMP_SERVIR),
        pages=chemins(CHAMP_PAGES, PAGES_MAX),
        absentes=chemins(CHAMP_ABSENTES, ABSENTES_MAX),
        delais=_delais(valeur(CHAMP_DELAIS)),
        ailleurs=_ailleurs(valeur(CHAMP_AILLEURS)),
        brut=brut or "",
    )


def _delais(texte: str) -> dict[str, Delai]:
    """Les délais du champ `DELAIS:`, par ligne jouée — un délai nul ou hors des lignes jouées
    ne s'applique à rien, et le premier de chaque ligne fait foi."""
    axes = {champ: axe for axe, champ in CHAMPS_COMMANDES.items()}
    delais: dict[str, Delai] = {}
    for trouve in _DELAI.finditer(texte):
        axe = axes.get(trouve.group("champ"))
        secondes = float(trouve.group("secondes").replace(",", "."))
        if axe is None or secondes <= 0 or axe in delais:
            continue
        delais[axe] = Delai(secondes, (trouve.group("origine") or "").strip())
    return delais


def _ailleurs(texte: str) -> dict[str, Ailleurs]:
    """Les verdicts du champ `ELSEWHERE:`, par ligne jouée — un champ hors des lignes jouées
    ne s'applique à rien, et le premier de chaque ligne fait foi."""
    axes = {champ: axe for axe, champ in CHAMPS_COMMANDES.items()}
    ailleurs: dict[str, Ailleurs] = {}
    for trouve in _AILLEURS.finditer(texte):
        axe = axes.get(trouve.group("champ"))
        if axe is None or axe in ailleurs:
            continue
        ailleurs[axe] = Ailleurs(
            trouve.group("systeme").strip(), (trouve.group("origine") or "").strip()
        )
    return ailleurs


def lignes_ecartees(lecteur: Lecteur, choix: Sequence[str], axes: Sequence[str] = AXES) -> Ecartees:
    """Les lignes de `axes` que les choix de la personne écartent en toutes lettres (#1681).

    Un modèle lit les choix, jamais une liste de mots (#746) : « aucun formateur appliqué
    au dépôt » écarte le formatage. Une lecture qui n'a pas eu lieu, ou hors contrat,
    n'écarte rien : chaque ligne se joue alors comme si la personne n'avait rien écarté.
    """
    candidates = [axe for axe in axes if axe in CHAMPS_ECARTABLES]
    if not choix or not candidates:
        return Ecartees()
    verifications = "\n".join(
        f"{CHAMPS_ECARTABLES[axe]}: {VERIFICATIONS_ECARTABLES[axe]}" for axe in candidates
    )
    prompt = (
        f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
        f"Les vérifications, chacune sous son champ :\n<verifications>\n{verifications}\n"
        "</verifications>\nLaquelle de ces vérifications ses choix écartent-ils ?"
    )
    try:
        brut = lecteur.rediger(SYSTEME_ECARTEES, prompt)
    except Exception as exc:  # fournisseur injoignable, quota : rien n'est écarté
        return Ecartees(pourquoi=f"les lignes écartées n'ont pas pu être lues : {exc}")
    champs = _champs(brut)
    if CHAMP_ECARTEES not in champs:
        return Ecartees(pourquoi=f"réponse hors contrat : {' '.join((brut or '').split())[:200]}")
    par_champ = {CHAMPS_ECARTABLES[axe]: axe for axe in candidates}
    nommes = (morceau.strip().strip("`").upper() for morceau in champs[CHAMP_ECARTEES].split(","))
    nommees = frozenset(par_champ[champ] for champ in nommes if champ in par_champ)
    return Ecartees(nommees, champs.get(CHAMP_POURQUOI, "") or "sans raison donnée")


def _jouer_les_commandes(
    copie: Path,
    commandes: Commandes,
    illisible: str,
    joues: Sequence[str],
    axes: Sequence[str],
    poste: PosteDuBanc,
    trace: Callable[[str, str], None],
    ecartees: Ecartees,
) -> dict[str, Ligne]:
    """Les lignes jouées, dans l'ordre — une installation ratée laisse les suivantes non jouées.

    Une commande sans retour dans son délai ne tranche pas (#1681), ni l'échec d'une
    ligne dont le livrable rend le verdict ailleurs que sur le poste (#1695). Une
    installation qui n'a pas abouti pour l'une de ces raisons laisse les suivantes se
    jouer : un succès est tenu, un échec ne tranche pas. Une ligne écartée par la
    personne ne se joue pas.
    """
    lignes: dict[str, Ligne] = {}
    bloquee = illisible
    inachevee = ""
    for axe in joues:
        commande = commandes.par_axe.get(axe, "")
        if axe in ecartees.axes:
            lignes[axe] = Ligne(axe, SANS_OBJET, ecartees.en_mots(), commande=commande)
            continue
        if bloquee:
            lignes[axe] = Ligne(axe, NON_JOUE, bloquee, commande=commande)
            continue
        if not commande:
            lignes[axe] = Ligne(
                axe, MANQUE, f"le livrable ne déclare aucune commande pour : {LIBELLES[axe]}"
            )
            if axe == AXE_INSTALLATION:
                bloquee = "l'installation n'a pas de commande : rien n'a de quoi passer"
            continue
        lignes[axe], expiree = _jouer_la_ligne(
            axe,
            commande,
            commandes.delais.get(axe),
            commandes.ailleurs.get(axe),
            copie,
            poste,
            trace,
            inachevee,
        )
        if axe == AXE_INSTALLATION and lignes[axe].etat == MANQUE:
            bloquee = "l'installation a échoué : rien n'a de quoi passer"
        if axe == AXE_INSTALLATION and expiree:
            inachevee = (
                "derrière une installation qui n'a pas fini dans son délai, un échec ne dit "
                "rien du livrable"
            )
        elif axe == AXE_INSTALLATION and lignes[axe].etat == NON_TRANCHE:
            inachevee = (
                "derrière une installation que le livrable ne fait pas sur ce poste, un échec "
                "ne dit rien de lui"
            )
    if AXE_PAGES in axes:
        lignes[AXE_PAGES] = _pages(copie, commandes, illisible, lignes, poste, trace, ecartees)
    return lignes


def _jouer_la_ligne(
    axe: str,
    commande: str,
    delai: Delai | None,
    ailleurs: Ailleurs | None,
    copie: Path,
    poste: PosteDuBanc,
    trace: Callable[[str, str], None],
    inachevee: str,
) -> tuple[Ligne, bool]:
    """La commande d'une ligne jouée dans son délai — la ligne, et si la commande a expiré.

    Une commande sans retour ne tranche pas. Un échec ne tranche pas non plus quand le
    livrable rend ce verdict ailleurs que sur le poste (`ailleurs`), ni derrière une
    installation inachevée (`inachevee` dit pourquoi). Un succès est tenu.
    """
    delai_s = DELAI_DU_BANC_S if delai is None else delai.secondes
    fait = poste.jouer(f"{SANS_TERMINAL} {commande}", copie, delai_s)
    mots = _execution_en_mots(fait, delai_s, delai)
    trace(f"ligne jouée : {LIBELLES[axe]}", f"`{commande}` — {mots}")
    if fait.expiree:
        etat = NON_TRANCHE
    elif fait.code == 0:
        etat = TENU
    elif ailleurs is not None:
        etat, mots = NON_TRANCHE, f"{mots} — {ailleurs.en_mots()}"
    elif inachevee:
        etat, mots = NON_TRANCHE, f"{mots} — {inachevee}"
    else:
        etat = MANQUE
    return Ligne(axe, etat, mots, commande=commande, duree_s=fait.duree_s), fait.expiree


def _pages(
    copie: Path,
    commandes: Commandes,
    illisible: str,
    lignes: Mapping[str, Ligne],
    poste: PosteDuBanc,
    trace: Callable[[str, str], None],
    ecartees: Ecartees,
) -> Ligne:
    """Le livrable servi : ses pages en `200`, les pages absentes en `404`.

    Derrière une installation ou un build qui n'a pas tranché, le livrable se sert quand
    même, et un écart ne tranche pas non plus : rien ne dit qu'il vient du livrable.
    """
    if AXE_PAGES in ecartees.axes:
        return Ligne(AXE_PAGES, SANS_OBJET, ecartees.en_mots(), commande=commandes.servir)
    if illisible:
        return Ligne(AXE_PAGES, NON_JOUE, illisible, commande=commandes.servir)
    inachevee = _avant_de_servir(lignes)
    if isinstance(inachevee, Ligne):
        return replace(inachevee, commande=commandes.servir)
    if not commandes.servir:
        return Ligne(AXE_PAGES, MANQUE, "le livrable ne déclare aucune commande pour se servir")
    attendus: dict[str, int] = dict.fromkeys(commandes.pages or ("/",), PAGE_SERVIE)
    for absente in (*commandes.absentes, PAGE_ABSENTE_DU_BANC):
        attendus.setdefault(absente, PAGE_ABSENTE)
    vues, sortie = servir(poste, copie, commande=commandes.servir, attendus=attendus)
    rendu = ", ".join(f"{page} → {statut or 'sans réponse'}" for page, statut in vues.items())
    trace("pages servies", rendu)
    ecarts = [
        f"{page} ({statut or 'sans réponse'} au lieu de {attendus[page]})"
        for page, statut in vues.items()
        if statut != attendus[page]
    ]
    if ecarts:
        fin = f" — {sortie}" if sortie else ""
        if inachevee:
            fin += f" — {inachevee}"
        return Ligne(
            AXE_PAGES,
            NON_TRANCHE if inachevee else MANQUE,
            f"{', '.join(ecarts)}{fin}",
            commande=commandes.servir,
        )
    return Ligne(AXE_PAGES, TENU, rendu, commande=commandes.servir)


def _avant_de_servir(lignes: Mapping[str, Ligne]) -> Ligne | str:
    """Ce que l'installation et le build laissent aux pages : une ligne non jouée s'ils
    l'empêchent, sinon pourquoi un écart ne tranchera pas (`""` s'il tranchera)."""
    inachevee = ""
    for prealable, echec in (
        (AXE_INSTALLATION, "l'installation a échoué : rien à servir"),
        (AXE_BUILD, "le build a échoué : rien à servir"),
    ):
        ligne = lignes.get(prealable)
        if ligne is None:
            continue
        if ligne.etat == NON_JOUE:
            return Ligne(AXE_PAGES, NON_JOUE, ligne.detail)
        # Un build que le livrable ne déclare pas n'empêche pas de servir : certaines
        # piles servent sans construire. Un build déclaré qui échoue, lui, l'empêche.
        if ligne.etat == MANQUE and ligne.commande:
            return Ligne(AXE_PAGES, NON_JOUE, echec)
        if ligne.etat == NON_TRANCHE and not inachevee:
            inachevee = (
                f"derrière une ligne qui n'a pas tranché ({LIBELLES[prealable]}), un écart ne "
                "dit rien du livrable"
            )
    return inachevee


def _execution_en_mots(fait: execution.Execution, delai_s: float, delai: Delai | None) -> str:
    """Ce qu'une commande a rendu, en une ligne — et, sans retour, quel délai elle avait."""
    if fait.expiree:
        if delai is None:
            return (
                f"aucun retour en {delai_s:g} s, et le livrable ne dit pas combien de temps "
                "lui laisser : une durée n'est pas un verdict"
            )
        origine = delai.origine or "sans origine dite"
        return (
            f"aucun retour dans les {delai_s:g} s que le livrable lui laisse ({origine}) : "
            "une durée n'est pas un verdict"
        )
    fin = " ".join(fait.sortie.split())[-300:] or "aucune sortie"
    return f"code {fait.code} en {fait.duree_s:.1f} s — {fin}"


def _commandes_en_mots(commandes: Commandes) -> str:
    """Les commandes lues, en une ligne — la pièce du déroulé."""
    morceaux = [
        f"{LIBELLES[axe]} : `{commande}`" if commande else f"{LIBELLES[axe]} : aucune"
        for axe, commande in commandes.par_axe.items()
    ]
    morceaux.append(f"servir : `{commandes.servir}`" if commandes.servir else "servir : aucune")
    morceaux.append(f"pages : {', '.join(commandes.pages) or 'aucune'}")
    delais = ", ".join(
        f"{LIBELLES[axe]} {delai.secondes:g} s" + (f" ({delai.origine})" if delai.origine else "")
        for axe, delai in commandes.delais.items()
    )
    morceaux.append(f"délais du livrable : {delais or f'aucun, {DELAI_DU_BANC_S:g} s du banc'}")
    ailleurs = ", ".join(
        f"{LIBELLES[axe]} sous {verdict.systeme}"
        + (f" ({verdict.origine})" if verdict.origine else "")
        for axe, verdict in commandes.ailleurs.items()
    )
    morceaux.append(f"verdicts rendus ailleurs : {ailleurs or 'aucun'}")
    return " ; ".join(morceaux)


# --- Les lignes jugées ------------------------------------------------------


def _juger(
    axe: str,
    lecteur: Lecteur,
    choix: Sequence[str],
    pieces: Pieces,
    nommes: Sequence[tuple[str, str]] = (),
    ecartees: Ecartees | None = None,
) -> Ligne:
    """Une ligne jugée sur pièces : tenue, manquée, ou non jugée si le juge s'abstient.

    `nommes` sont les fichiers que la ligne a nommés, lus pour elle (`_lectures`). Une
    ligne que la personne a écartée ne se juge pas : elle est sans objet.
    """
    if ecartees is not None and axe in ecartees.axes:
        return Ligne(axe, SANS_OBJET, ecartees.en_mots())
    if axe in (AXE_COMMITS, AXE_OUTILLAGE) and not choix:
        return Ligne(axe, NON_JUGE, "aucun choix d'outillage n'est déclaré pour ce livrable")
    choisi = f"Les choix d'outillage de la personne :\n<choix>{_choix_en_texte(choix)}</choix>\n"
    depot = f"Le dépôt, tel qu'il est commité :\n<depot>{pieces.en_texte(nommes)}</depot>\n"
    if axe == AXE_COMMITS:
        messages = "\n".join(
            f"<commit>\n{_message_borne(message)}\n</commit>" for message in pieces.commits
        )
        lesquels = (
            f"Les messages entiers des commits du livrable depuis sa base `{pieces.base[:12]}`"
            if pieces.base
            else "Les messages entiers des derniers commits"
        )
        systeme, prompt = (
            SYSTEME_COMMITS,
            f"{choisi}{lesquels}, du plus récent au plus ancien :\n"
            f"<commits>\n{messages or '(aucun commit)'}\n</commits>\n"
            "Ces commits suivent-ils la convention choisie ?",
        )
    elif axe == AXE_CI:
        systeme, prompt = (
            SYSTEME_CI,
            f"{choisi}{depot}Ce dépôt porte-t-il une intégration continue écrite ?",
        )
    else:
        systeme, prompt = (
            SYSTEME_OUTILLAGE,
            f"{choisi}{depot}Ce livrable suit-il chacun de ces choix ?",
        )
    avis = lecteur.juger(systeme, prompt)
    if not avis.lisible:
        return Ligne(axe, NON_JUGE, avis.pourquoi)
    return Ligne(axe, TENU if avis.nomme else MANQUE, avis.pourquoi)


def _message_borne(message: str) -> str:
    """Un message de commit entier, ou son début et sa fin s'il dépasse la longueur lue."""
    if len(message) <= CARACTERES_PAR_MESSAGE:
        return message
    fin = CARACTERES_PAR_MESSAGE - DEBUT_DE_MESSAGE
    return f"{message[:DEBUT_DE_MESSAGE]}\n[…]\n{message[-fin:]}"


def _choix_en_texte(choix: Sequence[str]) -> str:
    """Les choix d'outillage, un par ligne — « aucun » sans choix."""
    return "\n".join(f"- {un}" for un in choix) or "(aucun choix déclaré)"
