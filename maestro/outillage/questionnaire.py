"""Ce que Maestro comprend d'un projet neuf, et ce qu'il demande encore (#1031, #1147).

Le **pendant exact** de `maestro.outillage.analyse` : là-bas on lit un projet existant
pour savoir ce qu'il lui faut, ici on l'apprend de qui va le créer. Les deux aboutissent
au même endroit — `maestro.outillage.recommandation.recommander` —, et c'est la seule
chose de #1031 que #1147 n'a pas touchée.

## Plus de catalogue : un schéma de l'outillage, des valeurs libres (#1147)

#1031 posait six questions à options fermées — quatre natures, quatre langages plus
« autre », trois forges, deux conventions — et déduisait le reste de tables (Python ⇒
pytest). Un projet qui n'était d'aucune des quatre natures n'avait **aucune réponse
honnête** : la personne de « p2 » devait choisir une nature fausse, qui se propageait au
langage recommandé, aux commandes et à l'équipe. Le défaut n'était pas une option
manquante, c'était le catalogue lui-même (directive « rien de figé », docs/41).

Ce module ne décide donc plus **quoi** demander ni **quelles** réponses sont
admissibles. Il tient deux choses, et seulement elles :

- **le schéma de ce qui fait un outillage** (`SUJETS`) : la sorte de projet, son
  langage, le fichier où vivront ses commandes, son gestionnaire, la commande de chaque
  usage (`USAGES` du modèle), sa forge, sa CI, sa convention de commit. Ce n'est pas un
  catalogue de projets : c'est la forme des `Constats` que `recommander` lit, et les
  **valeurs** y sont libres — `flutter test`, `terraform validate`, `pubspec.yaml`.
  Un sujet hors de ce schéma est gardé (il se lit dans le fil) sans nourrir aucun
  constat ;
- **la lecture de ce que le modèle a compris** (`comprehension_depuis_texte`), qui ne
  croit rien sans le vérifier : une question sans intitulé est écartée, une
  recommandation absente des options est ramenée dedans, une question sur un sujet
  auquel la personne a **déjà** répondu n'est pas reposée.

Le modèle, lui, comprend : il lit ce qui a été dit, en tire les constats, et ne pose que
les questions qui comblent un vrai manque, avec des options écrites **pour ce projet**.
Son appel vit côté Control Tower (`maestro.controltower.outillage`), là où le registre de
langue s'applique à tout ce qui parle à la personne (#945, #1261) ; ce module reste sans
réseau et sans modèle, donc testable sur du texte.

## Deux sortes de réponses, et ce qu'elles deviennent

- une option **cliquée** (`Choix`, `libre=False`) porte une valeur que le modèle a
  lui-même écrite pour ce sujet — une commande, un fichier, un nom de forge. Elle **est**
  un constat, tel quel ;
- une réponse **tapée** (`libre=True`) est ce que la personne a dit avec ses mots. Elle
  s'enregistre au fil — c'est la moitié de #1147 : une correction tapée n'est plus
  perdue — mais ne devient jamais un constat par elle-même : « on verra, sans doute
  GitHub » n'est pas un nom de forge. C'est le tour suivant du modèle qui la comprend,
  et ce qu'il en tire revient en constats **déduits** (`deduit=True`, avec leur cause).

`acquis_de` dit ce qui fait foi quand les deux coexistent : la **dernière**
compréhension du modèle — elle a lu toutes les réponses, dans l'ordre, et une réponse
tapée plus tard corrige une réponse cliquée —, complétée des réponses cliquées sur les
sujets qu'elle aurait tus. Jamais l'inverse, sans quoi une correction tapée
(« finalement GitLab ») perdrait contre le clic qu'elle corrige.

## Les réponses rendent la recommandation de l'analyse — parce que c'est la sienne

Les constats acquis sont mués en `Constats` (`constats_depuis_choix`), et c'est
`recommander` — la fonction de #1030, inchangée — qui en tire la `Recommandation`. Il
n'y a donc pas deux chemins de « ce qu'il faut à ce projet » : il y en a un, et deux
façons d'en remplir l'entrée.

⚠ **Sur un projet neuf, une commande est toujours de la `convention`, jamais
`declaree`** (`ORIGINES_COMMANDE`) : le projet n'écrit rien nulle part puisqu'il
n'existe pas encore. Son `chemin` est l'endroit où la commande **vivra** (le
`manifeste` compris), son `extrait` dit d'où on la tient — la réponse cliquée, ou la
cause que le modèle a donnée.

## Pas de plafond : on s'arrête quand plus rien ne manque

#1031 bornait le questionnaire à six questions et l'écran l'annonçait (« question 3
sur 6 »). Le plafond disparaît avec le catalogue : la compréhension rend les questions
qui restent, et une compréhension sans question **est** la conclusion. `rang` dit
combien de réponses ont déjà été données, jamais combien il en reste — personne ne le
sait d'avance, et un total qui bouge sous les yeux mentirait deux fois.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from maestro.outillage.modele import (
    USAGES,
    Commande,
    Constats,
    DossierScripts,
    Forge,
    Gestionnaire,
    Langage,
    Piece,
    Recommandation,
)
from maestro.outillage.recommandation import recommander

#: Le `source.type` du manifeste (docs/38 §4.1) quand l'outillage vient des **réponses**
#: de l'utilisateur. Son pendant, `"analyse"`, est écrit par `Analyse.source_manifeste`
#: et pas ici : chacun des deux lots nomme sa propre provenance.
SOURCE_CHOIX = "choix"

#: La valeur qui dit **explicitement** qu'un sujet n'a rien : « pas de tests pour
#: l'instant », « pas de forge ». Ce n'est pas un manque — un manque se demande, ceci
#: se sait. Comparée sans casse, et « aucune » vaut « aucun » (le genre suit le sujet).
AUCUN = "aucun"

#: Le sujet de la première question, et de la seule qui se pose sans options : ce
#: qu'est le projet, dit avec les mots de la personne.
SUJET_NATURE = "nature"

#: Le **schéma** de ce qui fait l'outillage d'un projet — les sujets qu'une
#: compréhension renseigne, avec le nom qu'on leur donne à l'écran. Ce n'est pas un
#: catalogue de réponses : les valeurs sont libres. C'est la forme des `Constats` que
#: `recommander` lit, écrite une fois : le prompt du modèle la cite, `constats_depuis_choix`
#: la mue, et l'écran nomme un constat par elle (`Choix.to_dict` porte le `sujet`).
SUJETS: dict[str, str] = {
    SUJET_NATURE: "sorte de projet",
    "langages": "langage",
    "manifeste": "manifeste",
    "gestionnaire": "gestionnaire",
    "installer": "installer",
    "construire": "construire",
    "tester": "tests",
    "lint": "vérification",
    "formater": "formatage",
    "types": "types",
    "demarrer": "démarrer",
    "forge": "forge",
    "ci": "intégration continue",
    "conventions": "convention de commit",
}

#: La longueur au-delà de laquelle un **constat** est tronqué. Un constat est une
#: commande, un fichier ou un nom, et il finit entre accents graves dans un skill :
#: une valeur de trois paragraphes n'en est pas un, c'est une réponse mal rangée.
VALEUR_MAX = 200

#: La longueur au-delà de laquelle une réponse **tapée** est tronquée. Plus large :
#: c'est la personne qui décrit son projet, et c'est tout ce que le modèle en saura.
REPONSE_LIBRE_MAX = 2000


def _est_aucun(valeur: str) -> bool:
    """`valeur` dit-elle « il n'y en a pas » ?"""
    return valeur.strip().lower() in {AUCUN, "aucune"}


def _une_ligne(texte: str, borne: int) -> str:
    """Le texte sur une seule ligne, espaces compactés, borné — la forme d'un constat."""
    compacte = " ".join(str(texte).split())
    return compacte if len(compacte) <= borne else compacte[: borne - 1].rstrip() + "…"


def sujet_de(cle: str) -> str:
    """Le nom qu'on donne à un sujet — la clé elle-même s'il est hors du schéma."""
    return SUJETS.get(cle, cle)


@dataclass(frozen=True)
class Option:
    """Un choix offert par une question : sa valeur, son nom, et ce qu'il implique.

    `raison` est **ce que ce choix-là entraîne**, en une ligne — pas un argument de
    vente. C'est elle que l'écran pose sous le libellé de l'option (veille de #1031).
    Depuis #1147 les options sont **écrites pour le projet** par le modèle, et `valeur`
    est la valeur concrète du sujet (une commande, un fichier), pas un code de
    catalogue.
    """

    valeur: str
    libelle: str
    raison: str

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST."""
        return {"valeur": self.valeur, "libelle": self.libelle, "raison": self.raison}


@dataclass(frozen=True)
class QuestionOutillage:
    """Une question qui décide de l'outillage, avec la recommandation qu'elle porte.

    `recommande` est la valeur que Maestro propose ; `pourquoi` est **ce qui, dans ce
    projet, l'a désignée**. Les deux voyagent ensemble parce que séparés ils mentent.

    Une question **sans options** se répond avec ses mots seulement : c'est la première
    (« qu'est-ce que ce projet ? »), et toute question dont le modèle ne voit pas
    d'options plausibles. Une réponse libre reste possible sur **toutes** les autres
    (veille de #1147, d'après le contrat `AskUserQuestion` : « Claude's predefined
    options won't always cover what users want »).

    `rang` dit combien de réponses précèdent celle-ci, plus un. Il n'y a plus de
    `total` : le plafond de #1031 est retiré, et une forme stockée qui en porte un
    se relit sans lui.
    """

    cle: str
    intitule: str
    options: tuple[Option, ...]
    recommande: str
    pourquoi: str
    rang: int = 1

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST et du message de chat (`MessageChat.question`)."""
        return {
            "cle": self.cle,
            "intitule": self.intitule,
            "options": [o.to_dict() for o in self.options],
            "recommande": self.recommande,
            "pourquoi": self.pourquoi,
            "rang": self.rang,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuestionOutillage:
        """Relit une question depuis sa forme stockée — **ne rejuge rien**.

        Même règle que `MessageChat.from_dict` (#315) : c'est la relecture d'une
        question déjà posée. Une forme de #1031 (qui portait `total`) se relit telle
        qu'elle a été vue, son plafond en moins.
        """
        options = data.get("options") or ()
        return cls(
            cle=str(data.get("cle") or ""),
            intitule=str(data.get("intitule") or ""),
            options=tuple(
                Option(
                    valeur=str(o.get("valeur") or ""),
                    libelle=str(o.get("libelle") or ""),
                    raison=str(o.get("raison") or ""),
                )
                for o in options
                if isinstance(o, Mapping)
            ),
            recommande=str(data.get("recommande") or ""),
            pourquoi=str(data.get("pourquoi") or ""),
            rang=int(data.get("rang") or 0),
        )

    def libelle_de(self, valeur: str) -> str:
        """Le nom d'affichage d'une valeur — la valeur elle-même si ce n'est pas une option.

        Le repli est la règle, pas un cas de bord : une réponse tapée n'est aucune des
        options, et c'est ce qu'on a écrit qu'il faut relire.
        """
        for option in self.options:
            if option.valeur == valeur:
                return option.libelle
        return valeur


@dataclass(frozen=True)
class Choix:
    """Une réponse, ou un constat — ce qu'un sujet vaut, et comment on le sait.

    Trois provenances, et elles ne disent pas la même chose :

    - **cliquée** (`deduit=False`, `libre=False`) : une option que la personne a
      retenue. Sa valeur est concrète — c'est le modèle qui l'a écrite pour ce sujet —,
      donc elle est un constat telle quelle ;
    - **tapée** (`libre=True`) : ce que la personne a dit avec ses mots, sur le sujet de
      la question qui attendait. Enregistrée pour être relue et comprise, jamais
      recopiée comme constat (voir le module) ;
    - **déduite** (`deduit=True`) : ce que le modèle a compris de tout ce qui a été dit,
      avec sa cause (`parce_que`). C'est la forme que prend la compréhension quand elle
      voyage — dans le fil (`MessageChat.comprehension`) comme dans le corps des routes
      sans état.
    """

    cle: str
    valeur: str
    deduit: bool = False
    parce_que: str = ""
    libre: bool = False

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST et du message de chat — le `sujet` nommé pour l'écran."""
        return {
            "cle": self.cle,
            "valeur": self.valeur,
            "deduit": self.deduit,
            "parce_que": self.parce_que,
            "libre": self.libre,
            "sujet": sujet_de(self.cle),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Choix:
        """Relit un choix depuis sa forme stockée — sans rejuger, comme la question."""
        return cls(
            cle=str(data.get("cle") or ""),
            valeur=str(data.get("valeur") or ""),
            deduit=bool(data.get("deduit")),
            parce_que=str(data.get("parce_que") or ""),
            libre=bool(data.get("libre")),
        )


def donnees(choix: Sequence[Choix]) -> tuple[Choix, ...]:
    """Les réponses **données** par la personne — cliquées ou tapées, jamais déduites."""
    return tuple(c for c in choix if not c.deduit)


def question_ouverte(rang: int = 1) -> QuestionOutillage:
    """La question par laquelle tout commence : qu'est-ce que ce projet ?

    **Sans options**, et c'est la réponse au ticket : il n'y a pas de liste de sortes de
    projet où « p2 » aurait dû entrer. On la pose quand rien n'a encore été dit — ni
    réponse, ni conversation — ou quand ce qui a été dit ne dit pas ce qu'est le projet.
    D'après *Bolt* (veille de #1147) : on commence par décrire son projet avec ses mots.
    """
    return QuestionOutillage(
        cle=SUJET_NATURE,
        intitule="Qu'est-ce que ce projet ?",
        options=(),
        recommande="",
        pourquoi=(
            "Dites-le avec vos mots : ce qu'il fera, et avec quoi si c'est déjà décidé. "
            "Je n'en tirerai que ce qui en découle, et ne vous demanderai que ce qui manque."
        ),
        rang=rang,
    )


class ComprehensionIllisible(ValueError):
    """Ce que le modèle a rendu n'est pas une compréhension lisible.

    Un `ValueError` et non un défaut silencieux : une compréhension qu'on ne sait pas
    lire ne se remplace pas par une question inventée ici — ce serait remettre un
    catalogue par la porte de derrière. L'appelant la traduit en réponse indisponible,
    et la réponse de la personne, elle, reste acquise au fil.
    """


@dataclass(frozen=True)
class Comprehension:
    """Ce que Maestro a compris d'un projet neuf à ce tour, et ce qu'il demande encore.

    - `constats` : **tout** ce qu'on sait à ce tour, en `Choix` déduits — pas seulement
      ce qui est nouveau. C'est ce qui permet à la dernière compréhension de faire foi
      seule (`acquis_de`) ;
    - `questions` : ce qui manque encore, dans l'ordre où le demander. Vide : plus rien
      ne manque, et c'est la conclusion ;
    - `message` : ce que le modèle a à dire à la personne avant la question — une
      réponse à sa question en retour, un doute levé. Vide le plus souvent.
    """

    constats: tuple[Choix, ...] = ()
    questions: tuple[QuestionOutillage, ...] = ()
    message: str = ""

    @property
    def terminee(self) -> bool:
        """Plus rien ne manque."""
        return not self.questions

    def question_suivante(self, rang: int) -> QuestionOutillage | None:
        """La prochaine question à poser, à son rang — `None` quand plus rien ne manque."""
        if not self.questions:
            return None
        premiere = self.questions[0]
        return QuestionOutillage(
            cle=premiere.cle,
            intitule=premiere.intitule,
            options=premiere.options,
            recommande=premiere.recommande,
            pourquoi=premiere.pourquoi,
            rang=rang,
        )


def _objet_json(texte: str) -> Mapping[str, Any]:
    """L'objet JSON que `texte` porte — un bloc de code ou du texte autour ne gênent pas."""
    debut, fin = texte.find("{"), texte.rfind("}")
    if debut < 0 or fin <= debut:
        raise ComprehensionIllisible("la compréhension rendue ne porte aucun objet JSON.")
    try:
        objet = json.loads(texte[debut : fin + 1])
    except json.JSONDecodeError as exc:
        raise ComprehensionIllisible(
            f"la compréhension rendue n'est pas un JSON valide : {exc.msg}."
        ) from exc
    if not isinstance(objet, Mapping):
        raise ComprehensionIllisible("la compréhension rendue n'est pas un objet JSON.")
    return objet


def _cle(brute: Any) -> str:
    """Une clé de sujet : courte, en minuscules, sans espaces ni ponctuation superflue."""
    return re.sub(r"[^a-z0-9_-]+", "-", str(brute or "").strip().lower()).strip("-")[:40]


def _constats_lus(brut: Any) -> tuple[Choix, ...]:
    """Les constats d'une compréhension, validés — le dernier d'un même sujet l'emporte."""
    lus: dict[str, Choix] = {}
    for entree in brut if isinstance(brut, list) else ():
        if not isinstance(entree, Mapping):
            continue
        cle = _cle(entree.get("cle"))
        valeur = _une_ligne(entree.get("valeur") or "", VALEUR_MAX)
        if not cle or not valeur:
            continue
        lus[cle] = Choix(
            cle=cle,
            valeur=valeur,
            deduit=True,
            parce_que=_une_ligne(entree.get("parce_que") or "", VALEUR_MAX),
        )
    return tuple(lus.values())


def _options_lues(brut: Any) -> tuple[Option, ...]:
    """Les options d'une question, validées : une valeur, un nom, sans doublon."""
    vues: dict[str, Option] = {}
    for entree in brut if isinstance(brut, list) else ():
        if not isinstance(entree, Mapping):
            continue
        valeur = _une_ligne(entree.get("valeur") or "", VALEUR_MAX)
        if not valeur or valeur in vues:
            continue
        vues[valeur] = Option(
            valeur=valeur,
            libelle=_une_ligne(entree.get("libelle") or valeur, VALEUR_MAX),
            raison=_une_ligne(entree.get("raison") or "", VALEUR_MAX),
        )
    return tuple(vues.values())


def _questions_lues(brut: Any, repondus: set[str]) -> tuple[QuestionOutillage, ...]:
    """Les questions d'une compréhension, validées — celles déjà répondues écartées.

    Une question sur un sujet auquel la personne a **déjà** répondu n'est pas reposée,
    quelle que soit la façon dont elle a répondu : c'est ce qui fait converger le
    questionnaire sans plafond. Une réponse tapée peu claire se comprend au tour
    suivant ; si elle ne suffit pas, le modèle demande **autre chose**, pas la même
    chose une seconde fois.

    Une recommandation absente des options est ramenée sur la première : sinon le
    bouton serait armé sur une valeur que la liste n'offre pas.
    """
    lues: dict[str, QuestionOutillage] = {}
    for entree in brut if isinstance(brut, list) else ():
        if not isinstance(entree, Mapping):
            continue
        cle = _cle(entree.get("cle"))
        intitule = _une_ligne(entree.get("intitule") or "", VALEUR_MAX)
        if not cle or not intitule or cle in repondus or cle in lues:
            continue
        options = _options_lues(entree.get("options"))
        recommande = str(entree.get("recommande") or "").strip()
        valeurs = {o.valeur for o in options}
        if recommande not in valeurs:
            recommande = options[0].valeur if options else ""
        lues[cle] = QuestionOutillage(
            cle=cle,
            intitule=intitule,
            options=options,
            recommande=recommande,
            pourquoi=_une_ligne(entree.get("pourquoi") or "", 2 * VALEUR_MAX),
        )
    return tuple(lues.values())


def comprehension_depuis_texte(texte: str, reponses: Sequence[Choix]) -> Comprehension:
    """Ce que le modèle a compris, **lu et vérifié** — la frontière entre son texte et le fil.

    `reponses` sont les réponses données (`donnees`) : elles décident des questions
    qu'on ne repose pas. Tant que la sorte de projet n'est ni dite ni comprise, la
    seule question est la question ouverte — quoi que le modèle ait proposé d'autre :
    demander la CI d'un projet dont on ne sait pas ce qu'il est, c'est poser des
    questions au hasard.

    Lève `ComprehensionIllisible` sur un texte qui ne porte pas d'objet JSON.
    """
    objet = _objet_json(texte)
    repondus = {c.cle for c in donnees(reponses)}
    constats = _constats_lus(objet.get("constats"))
    questions = _questions_lues(objet.get("questions"), repondus)
    connus = repondus | {c.cle for c in constats}
    if SUJET_NATURE not in connus:
        questions = (question_ouverte(),)
    return Comprehension(
        constats=constats,
        questions=questions,
        message=_une_ligne(objet.get("message") or "", 2 * VALEUR_MAX),
    )


def reponses_en_texte(reponses: Sequence[Choix]) -> str:
    """Les réponses données, telles que le modèle les lit — une par ligne, dans l'ordre.

    Chacune dit **comment** elle a été donnée, parce que c'est ce qui décide de ce
    qu'elle vaut : une option cliquée est une valeur, une réponse tapée est une phrase à
    comprendre (voir le module).
    """
    lignes = [
        f"- {c.cle} ({'tapée' if c.libre else 'cliquée'}) : « {c.valeur} »"
        for c in donnees(reponses)
    ]
    return "\n".join(lignes) if lignes else "(aucune réponse pour l'instant)"


def acquis_de(choix: Sequence[Choix]) -> tuple[Choix, ...]:
    """Ce qui fait foi, un constat par sujet : la compréhension, complétée des clics.

    Les constats **déduits** d'abord — ils viennent de la dernière compréhension, qui a
    lu toutes les réponses dans l'ordre —, puis les réponses **cliquées** sur les sujets
    qu'elle aurait tus. Les réponses **tapées** n'y entrent pas : ce sont des phrases,
    comprises par le modèle et revenues en constats déduits (voir le module).

    Un sujet vide ou valant `AUCUN` est gardé tel quel : « aucun » est une réponse, et
    c'est `constats_depuis_choix` qui décide de ce qu'elle ne produit pas.
    """
    acquis: dict[str, Choix] = {}
    for choisi in choix:
        if choisi.deduit and choisi.cle and choisi.valeur:
            acquis[choisi.cle] = choisi
    for choisi in choix:
        if not choisi.deduit and not choisi.libre and choisi.cle and choisi.valeur:
            acquis.setdefault(choisi.cle, choisi)
    return tuple(acquis.values())


def compris_en_phrase(choix: Sequence[Choix]) -> str:
    """« langage : Dart · tests : flutter test · forge : github » — ce qui a été compris.

    Vide quand rien ne l'est. C'est la ligne que le fil écrit en toutes lettres, et que
    la carte rend de son côté depuis les mêmes constats (`sujet` de chacun).
    """
    return " · ".join(f"{sujet_de(c.cle)} : {c.valeur}" for c in acquis_de(choix))


# --------------------------------------------------------------------------- #
# Des constats acquis aux `Constats` — puis à la recommandation du lot 2        #
# --------------------------------------------------------------------------- #


def _extrait(choisi: Choix) -> str:
    """D'où l'on tient un constat, en quelques mots : la réponse, ou la cause comprise."""
    if choisi.deduit:
        return f"compris : {choisi.parce_que}" if choisi.parce_que else "compris de vos réponses"
    return f"réponse « {choisi.cle} » = {choisi.valeur}"


def constats_depuis_choix(choix: Sequence[Choix]) -> Constats:
    """Les constats acquis mués en `Constats` — la matière que `recommander` attend.

    C'est **toute** la jonction entre ce lot et le lot 2 : ce qui suit (quels skills,
    quelles entrées, quels écartés, dans quel ordre) est la recommandation du lot 2,
    appelée telle quelle. La mue ne connaît **aucun langage** : elle range des valeurs
    sous les sujets du schéma (`SUJETS`), et c'est tout.

    Trois propriétés à ne pas défaire :

    - **toutes les commandes sont de la `convention`.** Un projet qui n'existe pas
      encore ne déclare rien ; les marquer `declaree` ferait passer une réponse pour
      une lecture ;
    - **rien n'est constaté sur un sujet inconnu ou valant `AUCUN`.** `recommander`
      écartera alors le skill correspondant **avec sa raison** — ce qui est la bonne
      réponse, pas un trou ;
    - **`outillage_present` reste vide et `dossier_scripts.constate` faux.** Il n'y a
      rien à reconnaître dans un projet neuf : toutes les entrées sortent `a-generer`.

    ⚠ Une valeur arrive du modèle ou d'un clic, jamais d'une lecture du disque, et elle
    finit écrite dans le projet de la personne (un skill documente sa commande). C'est
    pourquoi un constat est tenu sur **une ligne** et borné (`VALEUR_MAX`), et pourquoi
    rien ici ne l'exécute : la recommandation se relit à l'écran avant toute écriture,
    et un projet versionné la reçoit sur une branche à valider (docs/24 §2.4).
    """
    acquis = {c.cle: c for c in acquis_de(choix)}

    def valeur(cle: str) -> str:
        choisi = acquis.get(cle)
        if choisi is None or _est_aucun(choisi.valeur):
            return ""
        return _une_ligne(choisi.valeur, VALEUR_MAX)

    manifeste = valeur("manifeste")
    langage = valeur("langages")
    langages = (
        (Langage(nom=langage, fichiers=0, part=1.0, exemple=manifeste),) if langage else ()
    )
    gestionnaire = valeur("gestionnaire")
    gestionnaires = (
        (
            Gestionnaire(
                nom=gestionnaire,
                chemin=manifeste,
                installer=valeur("installer") or None,
            ),
        )
        if gestionnaire
        else ()
    )
    # L'ordre suit `USAGES` : c'est celui dans lequel on arrive sur un projet, et
    # `recommander` ne trie pas — il lit la première commande de chaque usage.
    commandes = tuple(
        Commande(
            usage=usage,
            commande=valeur(usage),
            chemin=manifeste,
            extrait=_extrait(acquis[usage]),
            origine="convention",
        )
        for usage in USAGES
        if valeur(usage)
    )
    ci_chemin = valeur("ci")
    ci = (
        (
            Piece(
                nom=ci_chemin.rsplit("/", 1)[-1],
                chemin=ci_chemin,
                role=f"intégration continue — {_extrait(acquis['ci'])}",
            ),
        )
        if ci_chemin
        else ()
    )
    forge_nom = valeur("forge")
    convention = valeur("conventions")
    conventions = (
        (
            Piece(
                nom="CONTRIBUTING.md",
                chemin="CONTRIBUTING.md",
                role=(
                    f"convention de message de commit : {convention} — "
                    f"{_extrait(acquis['conventions'])}"
                ),
            ),
        )
        if convention
        else ()
    )
    return Constats(
        langages=langages,
        gestionnaires=gestionnaires,
        commandes=commandes,
        ci=ci,
        forge=Forge(nom=forge_nom) if forge_nom else None,
        conventions=conventions,
        # `constate=False` : un projet neuf n'a pas d'habitude à respecter, donc
        # `scripts/` est le défaut de docs/38 §3.4 et il est dit comme tel.
        dossier_scripts=DossierScripts(),
    )


def resume_des_choix(choix: Sequence[Choix]) -> str:
    """La phrase que le manifeste garde (`source.resume`, docs/38 §4.1).

    Le pendant de `maestro.outillage.analyse.resume` : une ligne, et **seulement ce qui
    est acquis** — ce qu'on ne sait pas n'y figure pas en « inconnu ». C'est elle qu'on
    relira six mois plus tard, à côté d'un outillage dont on se demande d'où il sort.
    """
    acquis = {c.cle: c.valeur for c in acquis_de(choix)}
    morceaux: list[str] = []
    for cle in (SUJET_NATURE, "langages"):
        if acquis.get(cle) and not _est_aucun(acquis[cle]):
            morceaux.append(acquis[cle])
    tests = acquis.get("tester", "")
    if tests and not _est_aucun(tests):
        morceaux.append(f"tests : {tests}")
    ci = acquis.get("ci", "")
    if ci:
        morceaux.append("aucune CI" if _est_aucun(ci) else f"CI : {ci}")
    return " ; ".join(morceaux) if morceaux else "aucun choix encore donné"


def source_manifeste_des_choix(projet_id: str, choix: Sequence[Choix]) -> dict[str, Any]:
    """Le fragment `source` du manifeste quand l'outillage vient des réponses (docs/38 §4.1).

    Le jumeau d'`Analyse.source_manifeste()`. `reference` est la suite des **constats
    acquis**, dans l'ordre : c'est ce qui tient lieu d'identifiant d'analyse, et c'est
    relisible sans rien ouvrir d'autre. Les phrases tapées n'y vont pas — elles sont
    dans le fil, et c'est ce qu'on en a compris qui a décidé de l'outillage.
    """
    acquis = acquis_de(choix)
    return {
        "type": SOURCE_CHOIX,
        "projet_id": projet_id,
        "reference": " ; ".join(f"{c.cle}={c.valeur}" for c in acquis),
        "resume": resume_des_choix(acquis),
    }


def recommandation_depuis_choix(choix: Sequence[Choix]) -> Recommandation:
    """L'outillage que ces constats recommandent — **la recommandation du lot 2**.

    Une ligne de corps : `recommander` est la fonction de
    `maestro.outillage.recommandation`, appelée sans copie ni variante. Ce que cette
    fonction ajoute est **uniquement** la mue des constats (`constats_depuis_choix`).
    """
    return recommander(constats_depuis_choix(choix))


def schema_en_texte() -> str:
    """Les sujets du schéma, un par ligne, tels que le prompt du modèle les cite."""
    return "\n".join(f'- "{cle}" : {nom}' for cle, nom in SUJETS.items())
