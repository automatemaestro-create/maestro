"""Les questions qui décident de l'outillage d'un projet neuf (#1031).

Lot 3/7 de #1020, et le **pendant exact** de `maestro.outillage.analyse` : là-bas on
lit un projet existant pour savoir ce qu'il lui faut, ici on le demande à qui va le
créer. Les deux aboutissent au même endroit, et c'est le sujet du module.

## Les réponses rendent la recommandation de l'analyse — parce que c'est la sienne

« Les réponses produisent la **même recommandation structurée** que l'analyse d'un
projet existant (lot 2), servie par l'API » : le critère se tient de la seule façon qui
ne puisse pas diverger — les réponses sont muées en `Constats`, et c'est
`maestro.outillage.recommandation.recommander` — la fonction du lot 2, inchangée — qui
en tire la `Recommandation`. Il n'y a donc **pas deux chemins** de « ce qu'il faut à ce
projet » à tenir d'accord : il y en a un, et deux façons d'en remplir l'entrée.

C'est ce qui rend `Entree.justification` lisible des deux côtés sans que personne ait à
savoir d'où elle sort : pour une analyse, le **fichier lu** ; ici, le manifeste que le
choix implique, dans le rôle de la **réponse** qui l'a décidé.

⚠ **Sur un projet neuf, une commande est toujours de la `convention`, jamais
`declaree`** — et la distinction est déjà celle d'`ORIGINES_COMMANDE` : le projet
n'écrit rien nulle part puisqu'il n'existe pas encore. Son `chemin` est donc l'endroit
où la commande **vivra** (`pyproject.toml`, `package.json`), pas un fichier qu'on a
ouvert ; son `extrait` nomme la réponse. Confondre les deux ferait passer un choix pour
une lecture, ce que le lot 2 s'interdit dans l'autre sens.

## Les questions — bornées, recommandées, et celles qu'on ne pose pas

Six thèmes décident de l'outillage d'un projet neuf : sa nature, son langage, ses
tests, sa forge, sa CI, ses conventions. Ils sont **bornés** (`QUESTIONS_MAX`), comme
les tours de clarification du brief (#321), et pour la même raison : un questionnaire
sans fin est un questionnaire qu'on abandonne.

**Une question dont la réponse se déduit d'une autre ne se pose pas** (note technique du
ticket), et la règle est mécanique plutôt que d'humeur : `options_admissibles` réduit
les options à celles que les réponses déjà données laissent tenables ; s'il n'en reste
**qu'une**, la question est *déduite* et `question_suivante` passe à la suivante. Deux
conséquences qu'on ne veut pas perdre :

- une déduction est un **fait**, pas un défaut. Elle se relit (`deductions`), porte sa
  cause (`Deduction.parce_que`) et entre dans la recommandation au même titre qu'une
  réponse donnée — c'est ce que Vercel fait de sa commande de build, qu'il déduit du
  framework et ne demande jamais ;
- le nombre de questions **réellement posées** est donc ≤ `QUESTIONS_MAX`, et il dépend
  des réponses. Un projet sans forge n'est pas interrogé sur sa CI.

`QuestionOutillage.recommande` est l'option que Maestro propose, `pourquoi` est ce qui
l'a désignée — **jamais une statistique inventée** : quand rien ne la décide, la phrase
le dit. C'est la règle du dépôt (« ce qui n'est pas vérifié n'est pas cité ») appliquée
à ce qu'on écrit dans une carte.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
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

#: Le `source.type` du manifeste (docs/38 §4.1) quand l'outillage vient des **choix**
#: de l'utilisateur. Son pendant, `"analyse"`, est écrit par `Analyse.source_manifeste`
#: et pas ici : chacun des deux lots nomme sa propre provenance, et aucun ne décrit
#: celle de l'autre.
SOURCE_CHOIX = "choix"

#: Le plafond de questions, comme les tours de clarification du brief ont le leur
#: (#321). Ce n'est pas le nombre posé — les questions déduites ne le sont pas — mais
#: ce qu'il ne dépassera jamais, et ce que l'écran peut donc annoncer en tête
#: (« question 3 sur N ») sans mentir.
QUESTIONS_MAX = 6


@dataclass(frozen=True)
class Option:
    """Un choix offert par une question : sa valeur, son nom, et ce qu'il implique.

    `raison` est **ce que ce choix-là entraîne**, en une ligne — pas un argument de
    vente. C'est elle que l'écran pose sous le libellé de l'option, d'après le sondage
    de Slack Block Kit, où chaque option porte sa propre explication plutôt qu'une
    légende commune (veille de #1031).
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

    `recommande` est la valeur que Maestro propose ; `pourquoi` est **ce qui l'a
    désignée**. Les deux voyagent ensemble parce que séparés ils mentent : une
    recommandation sans sa cause se lit comme un défaut arbitraire, et une cause sans
    sa recommandation ne se rattache à rien.

    `rang` et `total` disent où l'on en est. `total` est le **plafond**
    (`QUESTIONS_MAX`), jamais le nombre restant : il ne bouge pas d'une question à
    l'autre, là où le nombre restant dépendrait des déductions à venir et ferait
    reculer la barre sous les yeux de l'utilisateur.
    """

    cle: str
    intitule: str
    options: tuple[Option, ...]
    recommande: str
    pourquoi: str
    rang: int
    total: int = QUESTIONS_MAX

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST et du message de chat (`MessageChat.question`)."""
        return {
            "cle": self.cle,
            "intitule": self.intitule,
            "options": [o.to_dict() for o in self.options],
            "recommande": self.recommande,
            "pourquoi": self.pourquoi,
            "rang": self.rang,
            "total": self.total,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> QuestionOutillage:
        """Relit une question depuis sa forme stockée — **ne rejuge rien**.

        Même règle que `MessageChat.from_dict` (#315) : c'est la relecture d'une
        question déjà posée, et le catalogue a pu changer depuis. Reconstruire depuis
        `CATALOGUE` ferait lire au fil une question que personne n'a vue.
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
            total=int(data.get("total") or QUESTIONS_MAX),
        )

    def libelle_de(self, valeur: str) -> str:
        """Le nom d'affichage d'une valeur — la valeur elle-même si elle est inconnue.

        Le repli est volontaire : une valeur hors catalogue est refusée à l'entrée
        (`valeur_admissible`), mais un fil relu peut en porter une d'un catalogue
        antérieur, et l'écrire telle quelle vaut mieux que de rendre une ligne vide.
        """
        for option in self.options:
            if option.valeur == valeur:
                return option.libelle
        return valeur


@dataclass(frozen=True)
class Choix:
    """Une réponse donnée à une question — ce qu'un geste écrit dans le fil.

    `deduit` distingue les deux façons dont une réponse est acquise : quelqu'un l'a
    choisie, ou elle **découlait** d'une réponse antérieure. La distinction ne change
    pas ce qu'on génère ; elle change ce qu'on peut relire, et c'est tout son intérêt
    — un outillage qu'on rouvre six mois plus tard se juge sur ce qui a été décidé
    *et* sur ce qui a été conclu à notre place.

    `parce_que` porte la cause d'une déduction, vide sur un choix donné.
    """

    cle: str
    valeur: str
    deduit: bool = False
    parce_que: str = ""

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST et du message de chat (`MessageChat.choix`)."""
        return {
            "cle": self.cle,
            "valeur": self.valeur,
            "deduit": self.deduit,
            "parce_que": self.parce_que,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Choix:
        """Relit un choix depuis sa forme stockée — sans rejuger, comme la question."""
        return cls(
            cle=str(data.get("cle") or ""),
            valeur=str(data.get("valeur") or ""),
            deduit=bool(data.get("deduit")),
            parce_que=str(data.get("parce_que") or ""),
        )


# --------------------------------------------------------------------------- #
# Le catalogue des questions                                                    #
# --------------------------------------------------------------------------- #

#: Les tests que Maestro sait câbler, par langage. **Une seule entrée = une déduction**
#: (`options_admissibles`), et c'est la mécanique de la note technique du ticket : la
#: question ne se pose pas quand le langage y répond seul. Le tableau est court à
#: dessein — il dit ce que le dépôt sait générer, pas l'état de l'art.
_TESTS_PAR_LANGAGE: dict[str, tuple[str, ...]] = {
    "python": ("pytest",),
    "typescript": ("vitest", "jest"),
    "go": ("go-test",),
    "rust": ("cargo-test",),
    "autre": ("aucun",),
}

#: La CI que chaque forge peut porter. `aucune` y figure partout : ne pas vouloir de CI
#: reste un choix, même sur une forge qui en offre une. Une forge absente ne laisse
#: qu'elle, donc la question de la CI **ne se pose pas**.
_CI_PAR_FORGE: dict[str, tuple[str, ...]] = {
    "github": ("github-actions", "aucune"),
    "gitlab": ("gitlab-ci", "aucune"),
    "aucune": ("aucune",),
}

#: Le langage proposé selon la nature du projet, et **ce qui le désigne**. La raison
#: renvoie toujours à ce que Maestro sait outiller — un fait du dépôt, vérifiable —
#: plutôt qu'à une fréquence d'usage qu'on ne mesure nulle part.
_LANGAGE_PAR_NATURE: dict[str, tuple[str, str]] = {
    "application-web": (
        "typescript",
        "une interface web : c'est le langage dont Maestro sait décrire le montage, "
        "les tests et le lint.",
    ),
    "service-api": (
        "python",
        "un service : c'est le langage du moteur de Maestro, donc celui dont il sait "
        "écrire l'outillage de bout en bout.",
    ),
    "bibliotheque": (
        "python",
        "une bibliothèque : c'est le langage dont Maestro sait écrire les tests et le "
        "lint sans configuration à part.",
    ),
    "outil-cli": (
        "python",
        "un outil en ligne de commande : c'est le langage dont Maestro sait écrire le "
        "script de lancement et ses tests.",
    ),
}

_QUESTION_NATURE = QuestionOutillage(
    cle="nature",
    intitule="Quelle sorte de projet est-ce ?",
    options=(
        Option(
            "application-web",
            "Une application web",
            "Une interface servie par un navigateur — montage, build, tests d'écran.",
        ),
        Option(
            "service-api",
            "Un service ou une API",
            "Un programme qui répond à des appels — lancement, tests, journalisation.",
        ),
        Option(
            "bibliotheque",
            "Une bibliothèque",
            "Du code que d'autres projets importent — tests, lint, publication.",
        ),
        Option(
            "outil-cli",
            "Un outil en ligne de commande",
            "Un exécutable qu'on lance à la main — un point d'entrée et ses tests.",
        ),
    ),
    recommande="application-web",
    pourquoi=(
        "Première question : rien ne la décide encore. Celle-ci est la plus large — "
        "les suivantes s'y ajustent."
    ),
    rang=1,
)

_QUESTION_LANGAGES = QuestionOutillage(
    cle="langages",
    intitule="Dans quel langage sera-t-il principalement écrit ?",
    options=(
        Option("python", "Python", "pytest et ruff, que Maestro sait câbler."),
        Option("typescript", "TypeScript", "npm, un lanceur de tests et eslint."),
        Option("go", "Go", "`go test` et `go vet`, livrés avec la chaîne d'outils."),
        Option("rust", "Rust", "`cargo test` et `cargo clippy`, livrés avec cargo."),
        Option(
            "autre",
            "Un autre langage",
            "Maestro écrira les instructions, pas les scripts : il ne devinera pas "
            "des commandes qu'il ne connaît pas.",
        ),
    ),
    # Recalculée pour chaque projet par `_question_langages` : la recommandation
    # dépend de la nature, et c'est précisément ce que ce lot ajoute.
    recommande="typescript",
    pourquoi="",
    rang=2,
)

_QUESTION_TESTS = QuestionOutillage(
    cle="tests",
    intitule="Comment ce projet joue-t-il ses tests ?",
    options=(
        Option("pytest", "pytest", "Le lanceur de tests de référence en Python."),
        Option(
            "vitest",
            "Vitest",
            "Démarrage court, API compatible Jest — celui dont `apps/web` se sert.",
        ),
        Option(
            "jest",
            "Jest",
            "Le plus répandu ; demande une configuration à part pour TypeScript.",
        ),
        Option("go-test", "go test", "Livré avec Go, aucune dépendance à ajouter."),
        Option("cargo-test", "cargo test", "Livré avec cargo, aucune dépendance."),
        Option(
            "aucun",
            "Aucun pour l'instant",
            "Aucun skill de test ne sera généré — la question se reposera à la "
            "prochaine génération.",
        ),
    ),
    recommande="vitest",
    pourquoi="",
    rang=3,
)

_QUESTION_FORGE = QuestionOutillage(
    cle="forge",
    intitule="Où le code vivra-t-il ?",
    options=(
        Option("github", "GitHub", "Issues, Pull Requests et GitHub Actions."),
        Option("gitlab", "GitLab", "Issues, Merge Requests et GitLab CI."),
        Option(
            "aucune",
            "Nulle part pour l'instant",
            "Le projet reste local — ni CI, ni conventions de contribution.",
        ),
    ),
    recommande="github",
    pourquoi=(
        "C'est la forge dont Maestro sait décrire le cycle — c'est celle qu'il "
        "utilise lui-même."
    ),
    rang=4,
)

_QUESTION_CI = QuestionOutillage(
    cle="ci",
    intitule="Qu'est-ce qui vérifie le code à chaque changement ?",
    options=(
        Option(
            "github-actions",
            "GitHub Actions",
            "Un workflow qui joue les tests et le lint sur chaque Pull Request.",
        ),
        Option(
            "gitlab-ci",
            "GitLab CI",
            "Un pipeline qui joue les tests et le lint sur chaque Merge Request.",
        ),
        Option(
            "aucune",
            "Rien pour l'instant",
            "Les tests se jouent à la main — le skill le dira en toutes lettres.",
        ),
    ),
    recommande="github-actions",
    pourquoi="",
    rang=5,
)

_QUESTION_CONVENTIONS = QuestionOutillage(
    cle="conventions",
    intitule="Les messages de commit suivent-ils une convention ?",
    options=(
        Option(
            "conventional-commits",
            "Conventional Commits",
            "`feat:`, `fix:`, `docs:` — une convention que Maestro sait vérifier.",
        ),
        Option(
            "aucune",
            "Aucune",
            "Aucune règle de message ne sera écrite dans `AGENTS.md`.",
        ),
    ),
    recommande="conventional-commits",
    pourquoi=(
        "C'est la seule convention de message que Maestro sait décrire et vérifier "
        "— son propre dépôt la pose par un hook `commit-msg`."
    ),
    rang=6,
)

#: Les six questions, **dans l'ordre où elles se posent**, et l'ordre est une décision :
#: chacune ne dépend que de celles qui la précèdent (`forge` avant `ci`, `langages`
#: avant `tests`). L'inverser ferait des déductions qui regardent en arrière, donc des
#: questions posées puis rendues caduques.
CATALOGUE: tuple[QuestionOutillage, ...] = (
    _QUESTION_NATURE,
    _QUESTION_LANGAGES,
    _QUESTION_TESTS,
    _QUESTION_FORGE,
    _QUESTION_CI,
    _QUESTION_CONVENTIONS,
)


def _repondues(choix: Sequence[Choix]) -> dict[str, str]:
    """Les réponses acquises, par clé — la **dernière** l'emporte.

    Répondre deux fois à la même question arrive : on revient en arrière, on se
    corrige. Garder la première ferait ignorer la correction en silence.
    """
    return {c.cle: c.valeur for c in choix}


def options_admissibles(
    question: QuestionOutillage, choix: Sequence[Choix]
) -> tuple[Option, ...]:
    """Les options de `question` que les réponses déjà données laissent tenables.

    C'est **ici** que vit la règle « une question dont la réponse se déduit d'une autre
    ne se pose pas » : il n'y a pas de liste de questions à sauter, il y a une
    réduction, et une question dont il ne reste qu'une option est déduite. La
    différence compte — une liste se périme dès qu'on ajoute une option, la réduction
    non.

    Sans contrainte connue, toutes les options sont admissibles : c'est le cas des
    questions qui ne dépendent de rien (`nature`, `forge`, `conventions`).
    """
    acquis = _repondues(choix)
    if question.cle == "tests":
        tenables = _TESTS_PAR_LANGAGE.get(acquis.get("langages", ""), ())
    elif question.cle == "ci":
        tenables = _CI_PAR_FORGE.get(acquis.get("forge", ""), ())
    else:
        return question.options
    if not tenables:
        return question.options
    return tuple(o for o in question.options if o.valeur in tenables)


def _question_langages(choix: Sequence[Choix]) -> QuestionOutillage:
    """La question du langage, dont la recommandation **dépend de la nature**.

    C'est le seul endroit où une recommandation se recalcule, et c'est le sujet du
    ticket : « chaque question propose une recommandation ». Sans nature connue —
    impossible en pratique, `nature` étant la première —, on retombe sur le défaut du
    catalogue et la phrase le dit.
    """
    nature = _repondues(choix).get("nature", "")
    valeur, cause = _LANGAGE_PAR_NATURE.get(nature, ("", ""))
    if not valeur:
        return replace(
            _QUESTION_LANGAGES,
            pourquoi=(
                "Rien ne le décide encore : c'est celui qui s'outille le plus "
                "complètement."
            ),
        )
    intitule_nature = _QUESTION_NATURE.libelle_de(nature).lower()
    return replace(
        _QUESTION_LANGAGES,
        recommande=valeur,
        pourquoi=f"Déduit de « {intitule_nature} » — {cause}",
    )


def _recommandation_reduite(
    question: QuestionOutillage, admissibles: tuple[Option, ...]
) -> QuestionOutillage:
    """La question restreinte à `admissibles`, recommandation ramenée dedans.

    Une recommandation qui n'est plus admissible est un défaut qu'on ne peut plus
    choisir : la première option restante prend sa place, et `pourquoi` dit d'où vient
    la restriction. La taire donnerait une carte dont le bouton est armé sur une valeur
    que la liste n'offre pas.
    """
    valeurs = {o.valeur for o in admissibles}
    recommande = (
        question.recommande
        if question.recommande in valeurs
        else (admissibles[0].valeur if admissibles else question.recommande)
    )
    return replace(question, options=admissibles, recommande=recommande)


def _question_tests(
    choix: Sequence[Choix], admissibles: tuple[Option, ...]
) -> QuestionOutillage:
    """La question des tests, dont la cause de recommandation **nomme le langage**."""
    langage = _repondues(choix).get("langages", "")
    libelle = _QUESTION_LANGAGES.libelle_de(langage)
    reduite = _recommandation_reduite(_QUESTION_TESTS, admissibles)
    # « Aucun » n'est pas une recommandation, c'est un **aveu** : le dire comme une
    # préférence ferait passer une ignorance pour un conseil. C'est la règle du dépôt
    # — l'inconnu est nommé — au niveau d'une phrase de carte.
    if reduite.recommande == "aucun":
        return replace(
            reduite,
            pourquoi=(
                f"Déduit de « {libelle} » — Maestro ne connaît pas de lanceur de tests "
                "pour ce langage : il écrira les instructions, pas le script."
            ),
        )
    return replace(
        reduite,
        pourquoi=(
            f"Déduit de « {libelle} » — c'est le lanceur que Maestro sait câbler pour "
            "ce langage."
        ),
    )


def _question_ci(
    choix: Sequence[Choix], admissibles: tuple[Option, ...]
) -> QuestionOutillage:
    """La question de la CI, dont la cause de recommandation **nomme la forge**."""
    forge = _repondues(choix).get("forge", "")
    libelle = _QUESTION_FORGE.libelle_de(forge)
    reduite = _recommandation_reduite(_QUESTION_CI, admissibles)
    # Même règle qu'aux tests : sans forge il n'y a pas « une CI recommandée », il n'y
    # a **rien pour en jouer une**, et la phrase doit dire laquelle des deux.
    if forge == "aucune":
        return replace(
            reduite,
            pourquoi=(
                "Déduit de « aucune forge » — rien n'hébergerait un pipeline. Les "
                "tests se jouent à la main, et le skill le dira."
            ),
        )
    return replace(
        reduite,
        pourquoi=(
            f"Déduit de « {libelle} » — c'est la CI que cette forge porte nativement."
        ),
    )


def _preparee(
    question: QuestionOutillage, choix: Sequence[Choix], admissibles: tuple[Option, ...]
) -> QuestionOutillage:
    """La question telle qu'elle se pose **ici** : options réduites, cause nommée."""
    if question.cle == "langages":
        return _question_langages(choix)
    if question.cle == "tests":
        return _question_tests(choix, admissibles)
    if question.cle == "ci":
        return _question_ci(choix, admissibles)
    return _recommandation_reduite(question, admissibles)


def _rang(choix: Sequence[Choix]) -> int:
    """Le rang d'affichage de la question à poser : combien en a-t-on déjà posé + 1.

    Compté sur les réponses **données au geste** (`deduit=False`), pas sur la place de
    la question dans le catalogue. C'est la différence entre « question 3 sur 6 » et
    « question 4 sur 6 » juste après la 2 : les questions déduites ne sont pas posées,
    les compter creuserait des trous que personne ne peut expliquer. Le compteur ne
    recule jamais et ne saute jamais un cran ; `total` reste le **plafond**, donc le
    questionnaire peut finir avant.
    """
    return sum(1 for c in choix if not c.deduit) + 1


def deductions(choix: Sequence[Choix]) -> tuple[Choix, ...]:
    """Les réponses que les choix déjà donnés **entraînent**, dans l'ordre.

    Rendues comme des `Choix` à part entière, marqués `deduit=True` et portant leur
    cause : une déduction n'est pas un trou dans le questionnaire, c'est une réponse
    dont personne n'a eu à s'occuper. Elles sont calculées **en cascade** — une
    déduction peut en entraîner une autre —, ce qui est le cas de « aucune forge » →
    « aucune CI ».

    Vide quand rien ne se déduit ; idempotent — une clé déjà répondue n'est jamais
    re-déduite.
    """
    acquis = list(choix)
    trouvees: list[Choix] = []
    for question in CATALOGUE:
        if question.cle in _repondues(acquis):
            continue
        admissibles = options_admissibles(question, acquis)
        if len(admissibles) != 1:
            continue
        deduit = Choix(
            cle=question.cle,
            valeur=admissibles[0].valeur,
            deduit=True,
            parce_que=_preparee(question, acquis, admissibles).pourquoi,
        )
        acquis.append(deduit)
        trouvees.append(deduit)
    return tuple(trouvees)


def question_suivante(choix: Sequence[Choix]) -> QuestionOutillage | None:
    """La prochaine question à **poser**, `None` quand il n'y en a plus.

    Les questions déduites sont sautées sans être posées — appelle `deductions` pour
    les acquérir avant d'appeler ceci, sans quoi elles seraient reposées au tour
    suivant. Les deux verbes sont séparés parce que la déduction **écrit** (elle entre
    au fil) là où celui-ci ne fait que lire.
    """
    acquis = list(choix)
    for question in CATALOGUE:
        if question.cle in _repondues(acquis):
            continue
        admissibles = options_admissibles(question, acquis)
        if len(admissibles) == 1:
            continue
        return replace(_preparee(question, acquis, admissibles), rang=_rang(acquis))
    return None


def valeur_admissible(cle: str, valeur: str, choix: Sequence[Choix]) -> bool:
    """`valeur` est-elle une réponse tenable à `cle`, vu les réponses déjà données ?

    La garde de la frontière : une valeur hors catalogue, ou rendue intenable par une
    réponse antérieure, est refusée **avant** d'entrer au fil. Le fil est la seule
    mémoire du canal ; une valeur fantaisiste y resterait et se relirait à chaque tour.
    """
    question = next((q for q in CATALOGUE if q.cle == cle), None)
    if question is None:
        return False
    return any(o.valeur == valeur for o in options_admissibles(question, choix))


def cles_connues() -> tuple[str, ...]:
    """Les clés du catalogue, dans l'ordre — de quoi nommer ce qu'on refuse."""
    return tuple(q.cle for q in CATALOGUE)


# --------------------------------------------------------------------------- #
# Des choix aux constats — puis à la recommandation du lot 2                    #
# --------------------------------------------------------------------------- #

#: Ce qu'un langage implique, quand on le **choisit** plutôt que de le constater : le
#: manifeste où ses commandes vivront, son gestionnaire, et les commandes elles-mêmes
#: par usage (`USAGES` du modèle).
#:
#: ⚠ Ce tableau dit **ce que Maestro sait outiller**, pas l'état de l'art — la même
#: retenue que `maestro.outillage.detection`, qui ne connaît que ce qu'il sait lire.
#: Un langage absent (`autre`) n'a ni manifeste ni commande : l'outillage s'y réduit
#: aux instructions et aux ponts, et le dire est plus utile que de deviner.
_OUTILS_PAR_LANGAGE: dict[str, tuple[str, str, dict[str, str]]] = {
    "python": (
        "pyproject.toml",
        "uv",
        {"installer": "uv sync", "lint": "ruff check .", "formater": "ruff format ."},
    ),
    "typescript": (
        "package.json",
        "npm",
        {"installer": "npm ci", "lint": "npm run lint", "types": "npx tsc --noEmit"},
    ),
    "go": ("go.mod", "go", {"installer": "go mod download", "lint": "go vet ./..."}),
    "rust": ("Cargo.toml", "cargo", {"installer": "cargo fetch", "lint": "cargo clippy"}),
}

#: La commande de test de chaque réponse possible à la question « tests ».
_COMMANDE_DE_TEST: dict[str, str] = {
    "pytest": "pytest",
    "vitest": "npx vitest run",
    "jest": "npx jest",
    "go-test": "go test ./...",
    "cargo-test": "cargo test",
}

#: La commande de démarrage, quand la **nature** du projet en justifie une. Seules
#: l'application web et le service en ont une : une bibliothèque ne se lance pas, et un
#: outil en ligne de commande se lance par la commande qu'on est en train d'écrire.
_COMMANDE_DE_DEMARRAGE: dict[tuple[str, str], str] = {
    ("application-web", "typescript"): "npm run dev",
    ("service-api", "python"): "uv run python -m app",
    ("service-api", "typescript"): "npm run start",
    ("service-api", "go"): "go run ./...",
    ("service-api", "rust"): "cargo run",
}

#: Le fichier de CI que chaque réponse implique — l'endroit où le workflow vivra.
_FICHIER_DE_CI: dict[str, tuple[str, str]] = {
    "github-actions": (".github/workflows/ci.yml", "workflow GitHub Actions"),
    "gitlab-ci": (".gitlab-ci.yml", "pipeline GitLab CI"),
}


def _piece_du_choix(cle: str, valeur: str, chemin: str, role: str) -> Piece:
    """La pièce qui justifie un constat **choisi** : où il vivra, et quelle réponse l'a dit.

    `role` porte la réponse en toutes lettres — c'est lui que `recommander` recopie
    dans la justification de l'entrée (`_justification`), donc c'est là qu'il faut
    qu'elle se lise. Sur une analyse ce rôle dit ce qu'on a **vu** ; ici il dit ce qui
    a été **répondu**, et les deux se distinguent à la lecture sans qu'aucun lecteur
    n'ait à savoir quel lot a rempli la forme.
    """
    return Piece(nom=chemin, chemin=chemin, role=f"{role} — réponse « {cle} » = {valeur}")


def constats_depuis_choix(choix: Sequence[Choix]) -> Constats:
    """Les réponses muées en `Constats` — la matière que `recommander` attend.

    C'est **toute** la jonction entre ce lot et le lot 2, et elle tient dans une
    fonction : ce qui suit (quels skills, quelles entrées, quels écartés, dans quel
    ordre) est la recommandation du lot 2, appelée telle quelle. Rien de ce que docs/38
    tranche n'est re-décidé ici — pas même l'emplacement des skills, que ce module ne
    nomme nulle part.

    Trois propriétés à ne pas défaire :

    - **toutes les commandes sont de la `convention`.** Un projet qui n'existe pas
      encore ne déclare rien ; les marquer `declaree` ferait passer une réponse pour
      une lecture, l'exact travers qu'`ORIGINES_COMMANDE` existe pour empêcher.
    - **rien n'est constaté sur une question sans réponse.** Une question qu'on n'a pas
      encore posée ne justifie aucun fichier, et `recommander` écartera alors le skill
      correspondant **avec sa raison** — ce qui est la bonne réponse, pas un trou.
    - **`outillage_present` reste vide et `dossier_scripts.constate` faux.** Il n'y a
      rien à reconnaître dans un projet neuf : c'est ce qui fait que toutes les entrées
      sortent en `a-generer`, là où une analyse en rend `deja-present`.

    ⚠ **Deux réponses ne produisent aucun skill, et ce n'est pas une perte** : la forge
    et les conventions remplissent `Constats.forge` et `Constats.conventions`, que
    `recommander` ne mue pas en entrée — parce qu'il n'y a pas de skill à en tirer.
    Elles vont dans les sections `## Conventions` et `## Le projet` d'`AGENTS.md`
    (docs/38 §3.1), que #1033 écrira depuis ces mêmes constats. Leur ajouter un usage
    dans `SKILL_PAR_USAGE` reviendrait à re-trancher ici la table que le lot 2 tient,
    et c'est exactement ce que la note de #1029 existe pour empêcher.
    """
    acquis = _repondues(choix)
    nature = acquis.get("nature", "")
    langage = acquis.get("langages", "")
    manifeste, gestionnaire, commandes = _OUTILS_PAR_LANGAGE.get(langage, ("", "", {}))

    langages: list[Langage] = []
    gestionnaires: list[Gestionnaire] = []
    lues: list[Commande] = []
    if langage and manifeste:
        libelle = _QUESTION_LANGAGES.libelle_de(langage)
        langages.append(Langage(nom=libelle, fichiers=0, part=1.0, exemple=manifeste))
        gestionnaires.append(
            Gestionnaire(
                nom=gestionnaire,
                chemin=manifeste,
                installer=commandes.get("installer"),
            )
        )

    def _commande(usage: str, texte: str, cle: str) -> Commande:
        return Commande(
            usage=usage,
            commande=texte,
            chemin=manifeste,
            extrait=f"réponse « {cle} » = {acquis.get(cle, '')}",
            origine="convention",
        )

    # L'ordre suit `USAGES` : c'est celui dans lequel on arrive sur un projet, et
    # `recommander` ne trie pas — il lit la première commande de chaque usage.
    for usage in USAGES:
        if usage == "tester":
            tests = acquis.get("tests", "")
            if tests and tests != "aucun" and tests in _COMMANDE_DE_TEST:
                lues.append(_commande(usage, _COMMANDE_DE_TEST[tests], "tests"))
            continue
        if usage == "demarrer":
            demarrage = _COMMANDE_DE_DEMARRAGE.get((nature, langage))
            if demarrage:
                lues.append(_commande(usage, demarrage, "nature"))
            continue
        if usage in commandes:
            lues.append(_commande(usage, commandes[usage], "langages"))

    ci: list[Piece] = []
    reponse_ci = acquis.get("ci", "")
    if reponse_ci in _FICHIER_DE_CI:
        chemin, role = _FICHIER_DE_CI[reponse_ci]
        ci.append(_piece_du_choix("ci", reponse_ci, chemin, role))

    forge: Forge | None = None
    reponse_forge = acquis.get("forge", "")
    if reponse_forge and reponse_forge != "aucune":
        forge = Forge(nom=reponse_forge, chemin="")

    conventions: list[Piece] = []
    if acquis.get("conventions") == "conventional-commits":
        conventions.append(
            _piece_du_choix(
                "conventions",
                "conventional-commits",
                "CONTRIBUTING.md",
                "convention de message de commit",
            )
        )

    return Constats(
        langages=tuple(langages),
        gestionnaires=tuple(gestionnaires),
        commandes=tuple(lues),
        ci=tuple(ci),
        forge=forge,
        conventions=tuple(conventions),
        # `constate=False` : un projet neuf n'a pas d'habitude à respecter, donc
        # `scripts/` est le défaut de docs/38 §3.4 et il est dit comme tel.
        dossier_scripts=DossierScripts(),
    )


def resume_des_choix(choix: Sequence[Choix]) -> str:
    """La phrase que le manifeste garde (`source.resume`, docs/38 §4.1).

    Le pendant de `maestro.outillage.analyse.resume`, écrit sur les mêmes principes :
    une ligne, et **seulement ce qui a été répondu** — ce qu'on n'a pas demandé n'y
    figure pas en « inconnu ». C'est elle qu'on relira six mois plus tard, à côté d'un
    outillage dont on se demande d'où il sort.
    """
    acquis = _repondues(choix)
    morceaux: list[str] = []
    for cle, question in (("nature", _QUESTION_NATURE), ("langages", _QUESTION_LANGAGES)):
        if cle in acquis:
            morceaux.append(question.libelle_de(acquis[cle]))
    tests = acquis.get("tests", "")
    if tests and tests != "aucun":
        morceaux.append(f"tests : {_QUESTION_TESTS.libelle_de(tests)}")
    ci = acquis.get("ci", "")
    if ci:
        morceaux.append(
            _QUESTION_CI.libelle_de(ci) if ci != "aucune" else "aucune CI"
        )
    return " ; ".join(morceaux) if morceaux else "aucun choix encore donné"


def source_manifeste_des_choix(projet_id: str, choix: Sequence[Choix]) -> dict[str, Any]:
    """Le fragment `source` du manifeste quand l'outillage vient des choix (docs/38 §4.1).

    Le jumeau d'`Analyse.source_manifeste()`, et il est écrit **ici** pour la raison qui
    l'a fait écrire là-bas : c'est #1033 qui le posera dans le manifeste, et une
    seconde formulation du même fragment finirait par diverger — on lirait alors un
    outillage sans savoir d'où il sort.

    `reference` est la **suite des réponses**, dans l'ordre : c'est ce qui tient lieu
    d'identifiant d'analyse, et c'est relisible sans rien ouvrir d'autre.
    """
    return {
        "type": SOURCE_CHOIX,
        "projet_id": projet_id,
        "reference": " ; ".join(f"{c.cle}={c.valeur}" for c in choix),
        "resume": resume_des_choix(choix),
    }


def recommandation_depuis_choix(choix: Sequence[Choix]) -> Recommandation:
    """L'outillage que ces réponses recommandent — **la recommandation du lot 2**.

    Une ligne de corps, et c'est le critère du ticket : `recommander` est la fonction
    de `maestro.outillage.recommandation`, appelée sans copie ni variante. Ce que cette
    fonction ajoute est **uniquement** la mue des réponses en constats
    (`constats_depuis_choix`) ; tout le reste — quelles entrées, dans quel ordre, avec
    quelle raison et quels écartés — est celui d'un projet analysé.
    """
    return recommander(constats_depuis_choix(choix))


