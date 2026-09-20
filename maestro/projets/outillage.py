"""L'outillage d'un projet : ce qu'on recommande, et les questions qui le décident (#1031).

Lot 3/7 de #1020. Le format est arrêté par
[docs/38](../../docs/38-decision-outillage-universel-du-projet.md) — `AGENTS.md` à la
racine, deux ponts d'une ligne, les skills sous `.agents/skills/<nom>/`, les scripts
dans le skill ou dans le dossier de scripts du projet, aucune commande, et un
manifeste. Ce module n'en **redécide rien** : il en dérive ce qu'un projet donné
reçoit.

Il porte deux choses, et elles se lisent dans cet ordre.

## 1. La recommandation structurée — le contrat que deux lots partagent

`RecommandationOutillage` est ce que rend **l'analyse d'un projet existant** (#1030)
*et* ce que produisent **les choix d'un projet neuf** (ce lot). Une seule forme, parce
que le lot 5 (#1033) génère depuis l'une ou l'autre sans savoir laquelle, et que le
chantier « équipe » (#1021) dérive l'équipe de la même structure. Deux formes auraient
donné deux générateurs.

Ce qui change entre les deux n'est pas la forme mais le **remplissage** de deux champs,
et c'est voulu :

- `source` dit d'où ça vient — `SOURCE_ANALYSE` ou `SOURCE_CHOIX` — et remplit le
  `source` du manifeste (docs/38 §4.1), qui est la mémoire de « pourquoi » ;
- `Piece.justifie_par` nomme **ce qui justifie cette pièce-là** : pour une analyse, un
  endroit du projet (`pyproject.toml`, `.github/workflows/ci.yml`) ; pour un projet
  neuf, la **réponse** qui l'a décidée. Le champ existe une fois, se lit une fois, et
  un lecteur n'a jamais à savoir lequel des deux lots l'a rempli.

⚠ **`Piece` est une pièce par fichier, jamais par skill** — c'est la première des trois
propriétés que docs/38 §4.2 demande de ne pas défaire : « régénérer sans écraser » se
décide fichier par fichier, quelqu'un corrigeant un `SKILL.md` en laissant ses scripts
tranquilles, et l'inverse.

## 2. Les questions — bornées, recommandées, et celles qu'on ne pose pas

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

#: D'où vient une recommandation — l'analyse d'un projet existant (#1030) ou les
#: choix de l'utilisateur sur un projet neuf (#1031). C'est le `source.type` du
#: manifeste (docs/38 §4.1), et les deux seules valeurs qu'il prenne.
SOURCE_ANALYSE = "analyse"
SOURCE_CHOIX = "choix"

#: Le rôle d'un fichier généré, tel que le manifeste le nomme (docs/38 §4.1). Quatre
#: rôles, pas cinq : il n'y a **aucune commande** générée (docs/38 §3.5), et le
#: manifeste lui-même n'est pas une pièce de l'outillage mais sa comptabilité.
ROLE_INSTRUCTIONS = "instructions"
ROLE_PONT = "pont"
ROLE_SKILL = "skill"
ROLE_SCRIPT = "script"

#: Le dossier des skills d'un projet — le seul chemin lu par plus d'un client, et le
#: seul qui ne porte le nom d'aucun éditeur (docs/38 §2.1, §3.3). Écrit ici en une
#: seule place : trois lots le composent, et trois recopies finiraient par diverger.
DOSSIER_SKILLS = ".agents/skills"

#: Le dossier de scripts d'un projet **neuf**. Sur un projet existant il se
#: **constate** (`scripts/`, `bin/`, `tools/` — docs/38 §3.4) ; un projet neuf n'a rien
#: à constater, et `scripts/` est ce que la note retient à défaut.
DOSSIER_SCRIPTS_DEFAUT = "scripts"

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


@dataclass(frozen=True)
class Piece:
    """Un fichier de l'outillage recommandé, avec sa raison et ce qui le justifie.

    Une pièce **par fichier** (docs/38 §4.2), jamais par skill : c'est ce qui permet à
    la génération de régénérer l'un sans toucher l'autre.

    `deja_present` dit que le projet le porte **déjà** : reconnu plutôt que dupliqué,
    ce qui est le second critère de l'analyse (#1030). Toujours faux sur un projet
    neuf, où il n'y a rien à reconnaître — le champ existe pour que la forme soit la
    même des deux côtés, pas pour que chacun s'en serve.
    """

    chemin: str
    role: str
    titre: str
    raison: str
    justifie_par: str
    deja_present: bool = False

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST."""
        return {
            "chemin": self.chemin,
            "role": self.role,
            "titre": self.titre,
            "raison": self.raison,
            "justifie_par": self.justifie_par,
            "deja_present": self.deja_present,
        }


@dataclass(frozen=True)
class RecommandationOutillage:
    """L'outillage recommandé pour un projet — la forme que l'API sert.

    Le contrat partagé par l'analyse d'un projet existant (#1030) et les choix d'un
    projet neuf (#1031). `resume` est la phrase qui dit de quoi il s'agit, telle
    qu'elle entrera dans `source.resume` du manifeste (docs/38 §4.1) ; `reference`
    identifie *ce* qui a produit la recommandation — l'analyse, ou les réponses.

    `dossier_scripts` est nommé ici et pas déduit à la génération : sur un projet
    existant il se **constate**, sur un projet neuf il vaut `scripts/`, et les
    `SKILL.md` l'écrivent en chemin depuis la racine (docs/38 §3.4). Le laisser
    implicite obligerait chaque lecteur à refaire le constat.
    """

    projet_id: str
    source: str
    reference: str
    resume: str
    dossier_scripts: str
    pieces: tuple[Piece, ...] = ()
    choix: tuple[Choix, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """La forme du REST — `GET`/`POST …/outillage/recommandation`."""
        return {
            "projet_id": self.projet_id,
            "source": self.source,
            "reference": self.reference,
            "resume": self.resume,
            "dossier_scripts": self.dossier_scripts,
            "pieces": [p.to_dict() for p in self.pieces],
            "choix": [c.to_dict() for c in self.choix],
        }


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
# Des choix à la recommandation                                                 #
# --------------------------------------------------------------------------- #

def _skill(
    nom: str, titre: str, raison: str, justifie_par: str, script: str | None = None
) -> tuple[Piece, ...]:
    """Un skill, et son script quand il en a un — **deux pièces**, jamais une.

    docs/38 §4.2 : une entrée par fichier. Le script d'un skill qui n'en sert qu'un
    vit **dans** le skill, en chemin relatif à sa racine (§3.4).
    """
    pieces = [
        Piece(
            chemin=f"{DOSSIER_SKILLS}/{nom}/SKILL.md",
            role=ROLE_SKILL,
            titre=titre,
            raison=raison,
            justifie_par=justifie_par,
        )
    ]
    if script is not None:
        pieces.append(
            Piece(
                chemin=f"{DOSSIER_SKILLS}/{nom}/scripts/{script}",
                role=ROLE_SCRIPT,
                titre=f"Le script de « {titre} »",
                raison=f"Ce que « {titre} » exécute, appelé en chemin relatif au skill.",
                justifie_par=justifie_par,
            )
        )
    return tuple(pieces)


def _socle(justifie_par: str) -> tuple[Piece, ...]:
    """Les trois fichiers que tout projet outillé reçoit (docs/38 §3.1, §3.2).

    `AGENTS.md` est la source ; les deux ponts sont des fichiers d'**une ligne**, jamais
    une copie — trois sources pour une instruction divergeraient à la première
    correction.
    """
    return (
        Piece(
            chemin="AGENTS.md",
            role=ROLE_INSTRUCTIONS,
            titre="Les instructions du projet",
            raison=(
                "Le fichier d'instructions dont l'emplacement est écrit, lu nativement "
                "par Codex et Copilot."
            ),
            justifie_par=justifie_par,
        ),
        Piece(
            chemin="CLAUDE.md",
            role=ROLE_PONT,
            titre="Le pont vers Claude Code",
            raison="Une ligne — `@AGENTS.md` : Claude Code ne lit `AGENTS.md` que sans lui.",
            justifie_par=justifie_par,
        ),
        Piece(
            chemin="GEMINI.md",
            role=ROLE_PONT,
            titre="Le pont vers Gemini CLI",
            raison="Une ligne — `@AGENTS.md` : Gemini CLI lit `GEMINI.md` par défaut.",
            justifie_par=justifie_par,
        ),
    )


def _resume(acquis: Mapping[str, str]) -> str:
    """La phrase qui dit de quoi cet outillage est fait — le `source.resume` du manifeste.

    Écrite à partir des seules réponses acquises : ce qui n'a pas été répondu n'y
    apparaît pas, plutôt que d'y figurer en « inconnu ». C'est ce que `resume` sert à
    faire relire six mois plus tard.
    """
    morceaux: list[str] = []
    for cle, question in (("nature", _QUESTION_NATURE), ("langages", _QUESTION_LANGAGES)):
        if cle in acquis:
            morceaux.append(question.libelle_de(acquis[cle]))
    tests = acquis.get("tests", "")
    if tests and tests != "aucun":
        morceaux.append(_QUESTION_TESTS.libelle_de(tests))
    ci = acquis.get("ci", "")
    morceaux.append(
        _QUESTION_CI.libelle_de(ci) if ci and ci != "aucune" else "aucune CI"
    )
    return ", ".join(morceaux) if morceaux else "aucun choix encore donné"


def recommandation_depuis_choix(
    projet_id: str, choix: Sequence[Choix], reference: str = ""
) -> RecommandationOutillage:
    """L'outillage que ces choix recommandent — la **même forme** que l'analyse (#1030).

    Chaque pièce porte sa raison et **la réponse qui la justifie** : c'est le pendant,
    pour un projet neuf, de « l'endroit du projet qui la justifie » que l'analyse
    remplit. Un lecteur n'a donc jamais à savoir lequel des deux lots a produit la
    recommandation qu'il lit.

    Rien n'est recommandé sur une réponse absente : une question qu'on n'a pas encore
    posée ne peut justifier aucun fichier, et en inventer un reviendrait à générer
    l'outillage d'un projet qu'on n'a pas fini de décrire. Le socle (`AGENTS.md` et ses
    deux ponts), lui, est là dès le premier appel — il ne dépend d'aucune réponse.
    """
    acquis = _repondues(choix)
    pieces: list[Piece] = list(_socle("les réponses données à Maestro"))

    tests = acquis.get("tests", "")
    if tests and tests != "aucun":
        libelle = _QUESTION_TESTS.libelle_de(tests)
        pieces.extend(
            _skill(
                "lancer-les-tests",
                "Lancer les tests",
                f"Le projet joue ses tests avec {libelle} : un agent doit savoir les "
                "lancer avant de proposer un changement.",
                f"réponse « tests » = {tests}",
                script="tests.sh",
            )
        )

    nature = acquis.get("nature", "")
    if nature:
        pieces.extend(
            _skill(
                "monter-et-lancer",
                "Monter et lancer le projet",
                "Installer les dépendances et démarrer — ce qu'un agent qui arrive ne "
                "sait pas.",
                f"réponse « nature » = {nature}",
                script="lancer.sh",
            )
        )

    ci = acquis.get("ci", "")
    if ci and ci != "aucune":
        pieces.extend(
            _skill(
                "verifier-avant-de-pousser",
                "Vérifier avant de pousser",
                f"Rejouer localement ce que {_QUESTION_CI.libelle_de(ci)} jouera : un "
                "pipeline rouge se découvre avant la forge.",
                f"réponse « ci » = {ci}",
                script="verifier.sh",
            )
        )

    if acquis.get("conventions") == "conventional-commits":
        pieces.extend(
            _skill(
                "ecrire-un-commit",
                "Écrire un message de commit",
                "Le projet suit Conventional Commits : la forme du message est une "
                "règle, pas un goût.",
                "réponse « conventions » = conventional-commits",
            )
        )

    return RecommandationOutillage(
        projet_id=projet_id,
        source=SOURCE_CHOIX,
        reference=reference or "les réponses données à Maestro",
        resume=_resume(acquis),
        dossier_scripts=DOSSIER_SCRIPTS_DEFAUT,
        pieces=tuple(pieces),
        choix=tuple(choix),
    )
