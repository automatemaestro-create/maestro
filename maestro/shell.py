"""« Lire une commande comme bash la lit » — le lexique de la dispense et de la portée (#1348).

Deux modules jugent le texte d'une commande shell avant qu'elle ne s'exécute :
[`maestro.lecture`](./lecture.py) (*ne fait-elle que lire ?*, #1197) et
[`maestro.portee`](./portee.py) (*reste-t-elle dans le projet ?*, #1226). Ils la
lisaient avec `shlex`, qui découpe des mots, pas une commande — et le passage
`20260927-070605` du banc l'a payé : **61 demandes de validation**, dont 42
« commande illisible », pour des gestes aussi ordinaires qu'un `T=$(mktemp -d)`,
un heredoc `<<'EOF'` ou une boucle `for`. Une personne réelle aurait eu à les
trancher ; absente, le garde-fou refusait, et l'agent ne vérifiait plus son
travail.

En préparant le test, un défaut plus grave est apparu : `shlex` lit le **saut de
ligne comme un blanc**. Un script de deux lignes devenait une seule commande, dont
la seconde ligne était un argument de la première — si bien qu'un `ls` suivi, à
la ligne, de n'importe quoi passait pour une lecture, et un `echo ok` suivi d'un
`rm` pour un geste dans la portée. Ce module lit ce que bash exécute, pas ce qui
y ressemble.

## Ce qu'il lit

La syntaxe qu'un agent écrit, et à laquelle `lis` rend une forme :

- les **séparateurs** — `;`, `&&`, `||`, `|`, `&` et le saut de ligne —, les
  guillemets simples, doubles et `$'…'`, l'échappement, la ligne continuée, le
  commentaire ;
- les **substitutions** `$(…)`, `` `…` `` et `<(…)` : ce qu'elles exécutent est
  dans le texte, donc lu comme le reste, et rendu à côté du mot qui les porte ;
- les **heredocs** : le corps est une donnée, lue jusqu'à son délimiteur ; non
  cité, ce qu'il substitue est rendu comme une substitution ;
- les **blocs** `{ …; }` et `( … )`, `if`, `for`, `while`/`until`, `case`,
  `[[ … ]]`, `(( … ))`, et les **affectations** qui précèdent une commande.

## Ce qu'il refuse

La définition de fonction, le tableau, `select`, `coproc`, une arithmétique
qui exécute, des guillemets ou un bloc jamais fermés : `lis` lève
`Illisible`, avec sa raison. C'est la liste courte de ce qu'un agent n'écrit
presque jamais, et ce que ses lecteurs font d'un texte illisible est **leur**
décision — la dispense n'affirme rien, la portée rend la commande à une personne.

## Ce qu'il ne fait pas

Il n'exécute rien et ne développe rien : une variable reste une variable, une
substitution un texte lu. Ce qu'une valeur **vaut** — le dossier que `mktemp`
créera, celui que désigne `$HOME` — est l'affaire de celui qui juge
(`maestro.portee`), parce que la réponse dépend de ce qu'il cherche. Il est
**feuille** — il n'importe rien de `maestro` —, pour la raison qui vaut déjà pour
ses deux lecteurs : le hook du fournisseur le consulte, et il doit pouvoir
s'éprouver sans rien monter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Les genres d'une partie de mot. Un mot est une suite de morceaux : du texte, et
#: ce que le shell développera à sa place.
LITTERAL = "litteral"
VARIABLE = "variable"  # `$nom`, `${nom}`, `${nom:-…}`
SPECIAL = "special"  # `$?`, `$$`, `$1`…, et l'arithmétique `$((…))`
SUBSTITUTION = "substitution"  # `$(…)`, `` `…` ``, `<(…)`, `>(…)`
TILDE = "tilde"  # `~`, `~nom`, en tête de mot

#: Les opérateurs, du plus long au plus court : c'est l'ordre dans lequel on les
#: reconnaît, pour que `&&` ne se lise pas `&` puis `&`.
OPERATEURS: tuple[str, ...] = (
    "&>>", "<<<", "<<-", ";;&",
    "&&", "||", "|&", ";;", ";&", "<<", ">>", "<&", ">&", "<>", ">|", "&>",
    ";", "&", "|", "(", ")", "<", ">",
)  # fmt: skip

#: Ce qui clôt une branche de `case`.
FINS_DE_BRANCHE = frozenset({";;", ";&", ";;&"})

#: Ce qui sépare deux commandes d'une liste — leur ordre et leur logique ne
#: comptent pas pour qui juge chaque maillon.
SEPARATEURS = frozenset({";", "&", "&&", "||", "|", "|&"})

#: Les redirections. `<<` et `<<-` ouvrent un heredoc, qui n'est pas une cible de
#: fichier : il voyage à part (`Document`).
REDIRECTIONS = frozenset({"<", ">", ">>", "<&", ">&", "<>", ">|", "&>", "&>>", "<<<"})
HEREDOCS = frozenset({"<<", "<<-"})

#: Les mots réservés, reconnus **seulement en tête de commande** et nus : `echo
#: done` écrit « done », `"if"` est un programme qui s'appellerait ainsi.
RESERVES = frozenset(
    {"if", "then", "elif", "else", "fi", "for", "while", "until", "do", "done",
     "case", "esac", "select", "function", "coproc", "{", "}", "!", "[[", "time"}
)  # fmt: skip

#: Les mots réservés que ce lecteur refuse, et pourquoi on peut se le permettre :
#: un agent ne les écrit presque jamais, et chacun demanderait sa propre grammaire.
#: `case` n'y est plus : le passage `20260927-104414` en a montré un, écrit par
#: l'agent de S3 pour vérifier les chemins que ses notes citent.
NON_LUS = frozenset({"select", "function", "coproc"})

_METACARACTERES = frozenset(" \t\n;&|()<>")
_NOM = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_AFFECTATION = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\+?=")
_SPECIAUX = frozenset("?$#!@*-0123456789")


class Illisible(ValueError):
    """Le texte échappe au lecteur ; le message dit pourquoi."""


@dataclass(frozen=True)
class Partie:
    """Un morceau de mot. `texte` est le texte d'un littéral, le nom d'une variable,
    le paramètre d'un spécial ; `source` est ce qui était écrit.

    `scripts` porte ce que la partie **exécute** : le corps d'une substitution, et
    celles qu'une variable cache dans son opérateur (`${x:-$(commande)}`).
    `modifiee` dit qu'une variable passe par un opérateur : sa valeur n'est plus
    celle de la variable.
    """

    genre: str
    texte: str
    source: str = ""
    scripts: tuple[Script, ...] = ()
    modifiee: bool = False


@dataclass(frozen=True)
class Mot:
    """Un mot de la commande, guillemets retirés, et ce qu'il développera.

    `glob` dit qu'il porte, **hors guillemets**, un motif que le shell étendra
    (`*`, `?`, `[…]`, `{a,b}`) : le mot désignera alors d'autres chemins que le
    sien.
    """

    parties: tuple[Partie, ...]
    source: str
    glob: bool = False

    def litteral(self) -> str | None:
        """Le texte du mot s'il ne développe rien, `None` sinon."""
        if all(partie.genre == LITTERAL for partie in self.parties):
            return "".join(partie.texte for partie in self.parties)
        return None

    def rendu(self) -> str:
        """Le texte des littéraux, la source des développements — la forme d'un jeton `shlex`."""
        return "".join(
            partie.texte if partie.genre == LITTERAL else partie.source for partie in self.parties
        )

    def scripts(self) -> tuple[Script, ...]:
        """Ce que ce mot exécute en se développant."""
        return tuple(script for partie in self.parties for script in partie.scripts)


@dataclass(frozen=True)
class Redirection:
    """Une redirection : son opérateur, sa cible, et le descripteur qui la précède (`2`)."""

    operateur: str
    cible: Mot
    descripteur: str = ""


@dataclass(eq=False)
class Document:
    """Un heredoc. Son corps n'est connu qu'à la fin de la ligne qui l'ouvre : il est
    rempli à ce moment-là, d'où un objet qu'on complète plutôt qu'une valeur.

    `cite` : le délimiteur portait des guillemets, le corps est pris à la lettre.
    Sinon ce qu'il substitue s'exécute, et `scripts` le porte.
    """

    delimiteur: str
    cite: bool
    retrait: bool = False
    corps: str = ""
    scripts: tuple[Script, ...] = ()


@dataclass(frozen=True)
class Simple:
    """Une commande simple : ses affectations, ses mots, ses redirections, ses heredocs."""

    mots: tuple[Mot, ...]
    affectations: tuple[tuple[str, Mot], ...] = ()
    redirections: tuple[Redirection, ...] = ()
    documents: tuple[Document, ...] = ()


@dataclass(frozen=True)
class Groupe:
    """`{ … }`, ou `( … )` quand `sous_shell` : un sous-shell garde ses `cd` pour lui."""

    corps: Script
    sous_shell: bool
    redirections: tuple[Redirection, ...] = ()
    documents: tuple[Document, ...] = ()


@dataclass(frozen=True)
class Boucle:
    """`for variable in valeurs`, ou `while`/`until condition` — `variable` vide alors.

    `valeurs` est `None` pour un `for` sans liste (les paramètres de position) et
    pour un `for ((…))`.
    """

    variable: str
    valeurs: tuple[Mot, ...] | None
    condition: Script | None
    corps: Script
    redirections: tuple[Redirection, ...] = ()
    documents: tuple[Document, ...] = ()


@dataclass(frozen=True)
class Si:
    """`if … then … elif … else … fi` : chaque branche, sa condition et son corps."""

    branches: tuple[tuple[Script, Script], ...]
    sinon: Script | None
    redirections: tuple[Redirection, ...] = ()
    documents: tuple[Document, ...] = ()


@dataclass(frozen=True)
class Selon:
    """`case sujet in motif) … ;; esac` : le sujet, et le corps de chaque branche.

    Les motifs ne sont pas gardés : ils choisissent une branche, ils n'agissent
    sur rien — et qui juge le texte juge toutes les branches.
    """

    sujet: Mot
    branches: tuple[Script, ...]
    redirections: tuple[Redirection, ...] = ()
    documents: tuple[Document, ...] = ()


Commande = Simple | Groupe | Boucle | Si | Selon


@dataclass(frozen=True)
class Script:
    """Une liste de commandes, dans l'ordre du texte, et les séparateurs qu'elle emploie."""

    commandes: tuple[Commande, ...]
    operateurs: frozenset[str] = field(default_factory=frozenset)


def lis(commande: str) -> Script:
    """La forme de `commande`, telle que bash la lit — `Illisible` si elle lui échappe."""
    return _Lecteur(commande).script()


def affectation(mot: Mot) -> tuple[str, Mot] | None:
    """`nom=valeur` : le nom et la valeur, si `mot` est une affectation — `None` sinon.

    Le `=` doit être **hors guillemets** (`"A=1"` est un programme), d'où la lecture
    sur la source ; la valeur garde ses parties, qui peuvent développer quoi que ce
    soit.
    """
    trouve = _AFFECTATION.match(mot.source)
    if trouve is None:
        return None
    prefixe = len(trouve.group(0))
    restant, parties = prefixe, []
    for partie in mot.parties:
        if restant and partie.genre == LITTERAL:
            coupe = min(restant, len(partie.texte))
            restant -= coupe
            if partie.texte[coupe:]:
                parties.append(Partie(LITTERAL, partie.texte[coupe:]))
            continue
        parties.append(partie)
    valeur = mot.source[prefixe:]
    if valeur.startswith("~") and parties and parties[0].genre == LITTERAL:
        # `T=~/x` développe le tilde comme en tête de mot.
        tete, _, reste = parties[0].texte.partition("/")
        parties[0:1] = [Partie(TILDE, tete, tete)] + ([Partie(LITTERAL, "/" + reste)] if _ else [])
    return trouve.group(1), Mot(tuple(parties), valeur, mot.glob)


# --- Le lecteur ----------------------------------------------------------------

_MOT, _OP, _FD, _NL, _ARITH, _FIN = "mot", "op", "fd", "nl", "arith", "fin"


@dataclass(frozen=True)
class _Jeton:
    genre: str
    texte: str = ""
    mot: Mot | None = None


class _Lecteur:
    """Lexique et grammaire, sur une seule position : une substitution `$(…)` se lit
    en reprenant la grammaire là où le mot en est, et un heredoc à la fin de la
    ligne qui l'ouvre."""

    def __init__(self, texte: str) -> None:
        self.t = texte
        self.i = 0
        self.attente: list[Document] = []
        self._suivant: _Jeton | None = None

    # --- La grammaire -----------------------------------------------------

    def script(self) -> Script:
        script = self._liste(frozenset(), parenthese=False)
        jeton = self._regarde()
        if jeton.genre != _FIN:
            raise Illisible(f"« {jeton.texte or jeton.genre} » inattendu")
        for document in self.attente:
            # Un heredoc que la fin du texte coupe : bash le lit jusqu'au bout.
            document.corps = ""
        return script

    def _liste(
        self, arrets: frozenset[str], *, parenthese: bool, branche: bool = False
    ) -> Script:
        """Des commandes jusqu'à l'un des `arrets` — ou jusqu'à `;;` dans une branche de `case`."""
        commandes: list[Commande] = []
        operateurs: set[str] = set()
        while True:
            jeton = self._regarde()
            if jeton.genre == _NL or (jeton.genre == _OP and jeton.texte == ";"):
                self._prends()
                continue
            if jeton.genre == _FIN or self._reserve(jeton) in arrets:
                break
            if branche and jeton.genre == _OP and jeton.texte in FINS_DE_BRANCHE:
                break
            if jeton.genre == _OP and jeton.texte == ")":
                if parenthese:
                    break
                raise Illisible("parenthèse fermante sans ouvrante")
            commandes.append(self._commande())
            jeton = self._regarde()
            if jeton.genre == _OP and jeton.texte in SEPARATEURS:
                operateurs.add(jeton.texte)
                self._prends()
                continue
            if jeton.genre in (_NL, _FIN) or (jeton.genre == _OP and jeton.texte == ")"):
                continue
            if jeton.genre == _OP and jeton.texte in FINS_DE_BRANCHE:
                if branche:
                    continue
                raise Illisible(f"« {jeton.texte} » hors d'un case")
            raise Illisible(f"« {jeton.texte} » inattendu après une commande")
        return Script(tuple(commandes), frozenset(operateurs))

    def _selon(self) -> Selon:
        """`case sujet in [(]motif[|motif…]) liste ;; … esac`."""
        self._prends()
        sujet = self._prends()
        if sujet.genre != _MOT or sujet.mot is None:
            raise Illisible("`case` sans sujet")
        while self._regarde().genre == _NL:
            self._prends()
        self._attend("in")
        branches: list[Script] = []
        while True:
            while self._regarde().genre == _NL:
                self._prends()
            jeton = self._regarde()
            if self._reserve(jeton) == "esac":
                self._prends()
                break
            if jeton.genre == _OP and jeton.texte == "(":
                self._prends()
            while True:
                if self._prends().genre != _MOT:
                    raise Illisible("motif de `case` attendu")
                separateur = self._prends()
                if separateur.genre == _OP and separateur.texte == "|":
                    continue
                if separateur.genre == _OP and separateur.texte == ")":
                    break
                raise Illisible("motif de `case` mal fermé")
            branches.append(self._liste(frozenset({"esac"}), parenthese=False, branche=True))
            jeton = self._regarde()
            if jeton.genre == _OP and jeton.texte in FINS_DE_BRANCHE:
                self._prends()
                continue
            if self._reserve(jeton) == "esac":
                self._prends()
                break
            raise Illisible("`case` jamais fermé")
        return Selon(sujet.mot, tuple(branches), *self._redirections())

    def _commande(self) -> Commande:
        jeton = self._regarde()
        reserve = self._reserve(jeton)
        if reserve in ("!", "time"):
            self._prends()
            if reserve == "time" and self._reserve(self._regarde()) is None:
                suivant = self._regarde()
                if suivant.genre == _MOT and suivant.mot is not None and suivant.mot.source == "-p":
                    self._prends()
            return self._commande()
        if reserve == "{":
            self._prends()
            corps = self._liste(frozenset({"}"}), parenthese=False)
            self._attend("}")
            return Groupe(corps, False, *self._redirections())
        if jeton.genre == _OP and jeton.texte == "(":
            self._prends()
            corps = self._liste(frozenset(), parenthese=True)
            self._attend_op(")")
            return Groupe(corps, True, *self._redirections())
        if jeton.genre == _ARITH:
            self._prends()
            return Simple((_mot_litteral("(("),), (), *self._redirections())
        if reserve == "if":
            return self._si()
        if reserve == "for":
            return self._pour()
        if reserve in ("while", "until"):
            return self._tant_que()
        if reserve == "[[":
            return self._double_crochet()
        if reserve == "case":
            return self._selon()
        if reserve in NON_LUS:
            raise Illisible(f"« {reserve} » n'est pas lu")
        if reserve is not None:
            raise Illisible(f"« {reserve} » inattendu")
        return self._simple()

    def _simple(self) -> Simple:
        affectations: list[tuple[str, Mot]] = []
        mots: list[Mot] = []
        redirections: list[Redirection] = []
        documents: list[Document] = []
        while True:
            jeton = self._regarde()
            if jeton.genre == _MOT and jeton.mot is not None:
                self._prends()
                paire = affectation(jeton.mot) if not mots else None
                if paire is not None:
                    if not paire[1].source and self.t.startswith("(", self.i):
                        raise Illisible("tableau (`nom=(…)`) non lu")
                    affectations.append(paire)
                    continue
                mots.append(jeton.mot)
                suivant = self._regarde()
                if len(mots) == 1 and suivant.genre == _OP and suivant.texte == "(":
                    raise Illisible("définition de fonction non lue")
                continue
            if jeton.genre == _FD or (
                jeton.genre == _OP and jeton.texte in REDIRECTIONS | HEREDOCS
            ):
                self._redirection(redirections, documents)
                continue
            break
        if not (mots or affectations or redirections or documents):
            raise Illisible(f"commande vide avant « {self._regarde().texte or 'la fin'} »")
        return Simple(tuple(mots), tuple(affectations), tuple(redirections), tuple(documents))

    def _redirection(self, redirections: list[Redirection], documents: list[Document]) -> None:
        jeton = self._prends()
        descripteur = ""
        if jeton.genre == _FD:
            descripteur = jeton.texte
            jeton = self._prends()
            if jeton.genre != _OP or jeton.texte not in REDIRECTIONS | HEREDOCS:
                raise Illisible(f"descripteur {descripteur} sans redirection")
        cible = self._prends()
        if cible.genre != _MOT or cible.mot is None:
            raise Illisible(f"« {jeton.texte} » sans cible")
        if jeton.texte in HEREDOCS:
            document = Document(
                delimiteur=cible.mot.rendu(),
                cite=any(signe in cible.mot.source for signe in "'\"\\"),
                retrait=jeton.texte == "<<-",
            )
            self.attente.append(document)
            documents.append(document)
            return
        redirections.append(Redirection(jeton.texte, cible.mot, descripteur))

    def _redirections(self) -> tuple[tuple[Redirection, ...], tuple[Document, ...]]:
        """Les redirections qui suivent un bloc (`} > f`, `done < liste`)."""
        redirections: list[Redirection] = []
        documents: list[Document] = []
        while True:
            jeton = self._regarde()
            if jeton.genre == _FD or (
                jeton.genre == _OP and jeton.texte in REDIRECTIONS | HEREDOCS
            ):
                self._redirection(redirections, documents)
                continue
            return tuple(redirections), tuple(documents)

    def _si(self) -> Si:
        self._prends()
        branches: list[tuple[Script, Script]] = []
        sinon: Script | None = None
        while True:
            condition = self._liste(frozenset({"then"}), parenthese=False)
            self._attend("then")
            corps = self._liste(frozenset({"elif", "else", "fi"}), parenthese=False)
            branches.append((condition, corps))
            reserve = self._reserve(self._prends())
            if reserve == "elif":
                continue
            if reserve == "else":
                sinon = self._liste(frozenset({"fi"}), parenthese=False)
                self._attend("fi")
            elif reserve != "fi":
                raise Illisible("`if` jamais fermé")
            return Si(tuple(branches), sinon, *self._redirections())

    def _pour(self) -> Boucle:
        self._prends()
        jeton = self._prends()
        variable, valeurs = "", None
        if jeton.genre == _ARITH:
            pass  # `for ((…))` : ni variable nommée, ni liste
        elif jeton.genre == _MOT and jeton.mot is not None and _NOM.fullmatch(jeton.mot.source):
            variable = jeton.mot.source
            while self._regarde().genre == _NL:
                self._prends()
            if self._reserve(self._regarde()) == "in":
                self._prends()
                liste: list[Mot] = []
                while (suivant := self._regarde()).genre == _MOT and suivant.mot is not None:
                    self._prends()
                    liste.append(suivant.mot)
                valeurs = tuple(liste)
        else:
            raise Illisible("`for` sans variable")
        while (jeton := self._regarde()).genre == _NL or (
            jeton.genre == _OP and jeton.texte == ";"
        ):
            self._prends()
        self._attend("do")
        corps = self._liste(frozenset({"done"}), parenthese=False)
        self._attend("done")
        return Boucle(variable, valeurs, None, corps, *self._redirections())

    def _tant_que(self) -> Boucle:
        self._prends()
        condition = self._liste(frozenset({"do"}), parenthese=False)
        self._attend("do")
        corps = self._liste(frozenset({"done"}), parenthese=False)
        self._attend("done")
        return Boucle("", None, condition, corps, *self._redirections())

    def _double_crochet(self) -> Simple:
        """`[[ … ]]` : ses `<`, `&&` et parenthèses sont des opérandes, pas des opérateurs."""
        self._prends()
        mots = [_mot_litteral("[[")]
        while True:
            jeton = self._prends()
            if jeton.genre == _FIN:
                raise Illisible("`[[` jamais fermé")
            if jeton.genre == _MOT and jeton.mot is not None:
                mots.append(jeton.mot)
                if jeton.mot.source == "]]":
                    break
            elif jeton.genre in (_OP, _FD):
                mots.append(_mot_litteral(jeton.texte))
        redirections, documents = self._redirections()
        return Simple(tuple(mots), (), redirections, documents)

    def _attend(self, reserve: str) -> None:
        if self._reserve(self._regarde()) != reserve:
            raise Illisible(f"« {reserve} » attendu")
        self._prends()

    def _attend_op(self, operateur: str) -> None:
        jeton = self._regarde()
        if jeton.genre != _OP or jeton.texte != operateur:
            raise Illisible(f"« {operateur} » attendu")
        self._prends()

    @staticmethod
    def _reserve(jeton: _Jeton) -> str | None:
        """Le mot réservé que porte `jeton`, s'il est nu — `None` sinon."""
        if jeton.genre != _MOT or jeton.mot is None:
            return None
        source = jeton.mot.source
        if source in RESERVES | {"in", "]]"} and jeton.mot.litteral() == source:
            return source
        return None

    # --- Le lexique -------------------------------------------------------

    def _regarde(self) -> _Jeton:
        if self._suivant is None:
            self._suivant = self._jeton()
        return self._suivant

    def _prends(self) -> _Jeton:
        jeton = self._regarde()
        self._suivant = None
        return jeton

    def _jeton(self) -> _Jeton:
        self._blancs()
        if self.i >= len(self.t):
            return _Jeton(_FIN)
        caractere = self.t[self.i]
        if caractere == "\n":
            self.i += 1
            self._lis_documents()
            return _Jeton(_NL, "\n")
        if self.t.startswith("((", self.i):
            contenu = self._arithmetique(self.i + 2)
            return _Jeton(_ARITH, contenu)
        if caractere in "<>" and self.t.startswith("(", self.i + 1):
            return self._mot()  # substitution de processus : un mot
        for operateur in OPERATEURS:
            if self.t.startswith(operateur, self.i):
                self.i += len(operateur)
                return _Jeton(_OP, operateur)
        return self._mot()

    def _blancs(self) -> None:
        while self.i < len(self.t):
            if self.t[self.i] in " \t\r":
                self.i += 1
            elif self.t.startswith("\\\n", self.i):
                self.i += 2
            elif self.t[self.i] == "#":
                fin = self.t.find("\n", self.i)
                self.i = len(self.t) if fin < 0 else fin
            else:
                return

    def _lis_documents(self) -> None:
        """Les corps des heredocs ouverts sur la ligne qui vient de finir."""
        while self.attente:
            document = self.attente.pop(0)
            lignes: list[str] = []
            while self.i < len(self.t):
                fin = self.t.find("\n", self.i)
                ligne = self.t[self.i : len(self.t) if fin < 0 else fin]
                self.i = len(self.t) if fin < 0 else fin + 1
                if (ligne.lstrip("\t") if document.retrait else ligne) == document.delimiteur:
                    break
                lignes.append(ligne)
            document.corps = "\n".join(lignes)
            if not document.cite:
                lecteur = _Lecteur(document.corps)
                parties: list[Partie] = []
                lecteur._guillemets(parties, [], fin=None)
                document.scripts = tuple(s for partie in parties for s in partie.scripts)

    def _mot(self) -> _Jeton:
        debut = self.i
        parties: list[Partie] = []
        tampon: list[str] = []
        nus: list[str] = []  # ce qui est hors guillemets : là seulement vivent les motifs
        glob = False
        while self.i < len(self.t):
            caractere = self.t[self.i]
            if caractere in "<>" and self.i == debut and self.t.startswith("(", self.i + 1):
                self._solde(parties, tampon)
                self.i += 2
                parties.append(self._sous_liste(debut))
                continue
            if caractere in _METACARACTERES:
                break
            if caractere == "\\":
                if self.t.startswith("\\\n", self.i):
                    self.i += 2
                    continue
                tampon.append(self.t[self.i + 1 : self.i + 2] or "\\")
                self.i += 2
                continue
            if caractere == "'":
                fin = self.t.find("'", self.i + 1)
                if fin < 0:
                    raise Illisible("guillemet simple jamais fermé")
                tampon.append(self.t[self.i + 1 : fin])
                self.i = fin + 1
                continue
            if caractere == '"':
                self.i += 1
                self._guillemets(parties, tampon, fin='"')
                continue
            if caractere == "$":
                self._dollar(parties, tampon, dans_guillemets=False)
                continue
            if caractere == "`":
                self._accent(parties, tampon)
                continue
            if caractere == "~" and self.i == debut:
                nom = re.match(r"~[A-Za-z0-9._-]*", self.t[self.i :]).group(0)  # type: ignore[union-attr]
                apres = self.t[self.i + len(nom) : self.i + len(nom) + 1]
                if apres in ("", "/") or apres in _METACARACTERES:
                    parties.append(Partie(TILDE, nom, nom))
                    self.i += len(nom)
                    continue
            if caractere in "*?" or (caractere == "[" and "]" in self._reste_du_mot()):
                glob = True
            tampon.append(caractere)
            nus.append(caractere)
            self.i += 1
        self._solde(parties, tampon)
        source = self.t[debut : self.i]
        if source.isdigit() and self.t[self.i : self.i + 1] in ("<", ">"):
            return _Jeton(_FD, source)
        glob = glob or _accolades("".join(nus))
        return _Jeton(_MOT, source, Mot(tuple(parties), source, glob))

    def _reste_du_mot(self) -> str:
        fin = self.i
        while fin < len(self.t) and self.t[fin] not in _METACARACTERES:
            fin += 1
        return self.t[self.i : fin]

    @staticmethod
    def _solde(parties: list[Partie], tampon: list[str]) -> None:
        if tampon:
            parties.append(Partie(LITTERAL, "".join(tampon)))
            tampon.clear()

    def _guillemets(self, parties: list[Partie], tampon: list[str], fin: str | None) -> None:
        """Entre guillemets doubles — ou le corps d'un heredoc non cité, quand `fin` est `None`."""
        while self.i < len(self.t):
            caractere = self.t[self.i]
            if fin is not None and caractere == fin:
                self.i += 1
                return
            if caractere == "\\":
                suivant = self.t[self.i + 1 : self.i + 2]
                if suivant == "\n":
                    self.i += 2
                    continue
                if suivant in ("$", "`", '"', "\\"):
                    tampon.append(suivant)
                    self.i += 2
                    continue
                tampon.append("\\")
                self.i += 1
                continue
            if caractere == "$":
                self._dollar(parties, tampon, dans_guillemets=True)
                continue
            if caractere == "`":
                self._accent(parties, tampon)
                continue
            tampon.append(caractere)
            self.i += 1
        if fin is not None:
            raise Illisible("guillemet double jamais fermé")
        self._solde(parties, tampon)

    def _dollar(self, parties: list[Partie], tampon: list[str], *, dans_guillemets: bool) -> None:
        debut = self.i
        suivant = self.t[self.i + 1 : self.i + 2]
        if self.t.startswith("$((", self.i):
            self._arithmetique(self.i + 3)
            self._solde(parties, tampon)
            parties.append(Partie(SPECIAL, "((", self.t[debut : self.i]))
            return
        if suivant == "(":
            self._solde(parties, tampon)
            self.i += 2
            parties.append(self._sous_liste(debut))
            return
        if suivant == "{":
            self._solde(parties, tampon)
            parties.append(self._accolade())
            return
        if suivant == "'" and not dans_guillemets:
            fin = self._fin_ansi(self.i + 2)
            tampon.append(_ansi(self.t[self.i + 2 : fin]))
            self.i = fin + 1
            return
        if suivant == '"' and not dans_guillemets:
            self.i += 2
            self._guillemets(parties, tampon, fin='"')
            return
        nom = _NOM.match(self.t, self.i + 1)
        if nom is not None:
            self._solde(parties, tampon)
            self.i = nom.end()
            parties.append(Partie(VARIABLE, nom.group(0), self.t[debut : self.i]))
            return
        if suivant and suivant in _SPECIAUX:
            self._solde(parties, tampon)
            self.i += 2
            parties.append(Partie(SPECIAL, suivant, self.t[debut : self.i]))
            return
        tampon.append("$")
        self.i += 1

    def _sous_liste(self, debut: int) -> Partie:
        """Le corps d'un `$(…)` ou d'un `<(…)`, lu par la même grammaire."""
        sauve = self._suivant
        self._suivant = None
        script = self._liste(frozenset(), parenthese=True)
        self._attend_op(")")
        self._suivant = sauve
        return Partie(SUBSTITUTION, self.t[debut : self.i], self.t[debut : self.i], (script,))

    def _accent(self, parties: list[Partie], tampon: list[str]) -> None:
        """Une substitution à l'ancienne : son corps, déséchappé, se relit à part."""
        debut = self.i
        self.i += 1
        corps: list[str] = []
        while self.i < len(self.t) and self.t[self.i] != "`":
            if self.t[self.i] == "\\" and self.t[self.i + 1 : self.i + 2] in ("`", "\\", "$"):
                corps.append(self.t[self.i + 1])
                self.i += 2
                continue
            corps.append(self.t[self.i])
            self.i += 1
        if self.i >= len(self.t):
            raise Illisible("substitution `…` jamais fermée")
        self.i += 1
        self._solde(parties, tampon)
        script = _Lecteur("".join(corps)).script()
        source = self.t[debut : self.i]
        parties.append(Partie(SUBSTITUTION, source, source, (script,)))

    def _accolade(self) -> Partie:
        """`${…}` : le nom, et ce que son opérateur exécuterait (`${x:-$(commande)}`)."""
        debut = self.i
        self.i += 2
        profondeur, contenu_debut = 1, self.i
        while self.i < len(self.t):
            caractere = self.t[self.i]
            if caractere == "\\":
                self.i += 2
                continue
            if caractere == "'":
                fin = self.t.find("'", self.i + 1)
                self.i = len(self.t) if fin < 0 else fin + 1
                continue
            if self.t.startswith("${", self.i):
                profondeur += 1
                self.i += 2
                continue
            if caractere == "}":
                profondeur -= 1
                if profondeur == 0:
                    break
            self.i += 1
        if self.i >= len(self.t):
            raise Illisible("`${` jamais fermé")
        contenu = self.t[contenu_debut : self.i]
        self.i += 1
        source = self.t[debut : self.i]
        nom = re.match(r"[#!]?([A-Za-z_][A-Za-z0-9_]*|[0-9]+|[?$#!@*-])", contenu)
        if nom is None:
            raise Illisible(f"développement {source} non lu")
        operateur = contenu[nom.end() :]
        scripts: tuple[Script, ...] = ()
        if "$" in operateur or "`" in operateur:
            lecteur = _Lecteur(operateur)
            morceaux: list[Partie] = []
            lecteur._guillemets(morceaux, [], fin=None)
            scripts = tuple(s for morceau in morceaux for s in morceau.scripts)
        genre = SPECIAL if nom.group(1) in _SPECIAUX or nom.group(1).isdigit() else VARIABLE
        modifiee = bool(operateur) or nom.group(0) != nom.group(1)
        return Partie(genre, nom.group(1), source, scripts, modifiee)

    def _arithmetique(self, debut: int) -> str:
        """Le contenu d'un `((…))`/`$((…))` — qui ne doit rien exécuter."""
        profondeur, position = 0, debut
        while position < len(self.t):
            caractere = self.t[position]
            if caractere == "(":
                profondeur += 1
            elif caractere == ")":
                if profondeur == 0:
                    if not self.t.startswith("))", position):
                        raise Illisible("arithmétique mal fermée")
                    contenu = self.t[debut:position]
                    if "$(" in contenu or "`" in contenu:
                        raise Illisible("arithmétique qui exécute une commande")
                    self.i = position + 2
                    return contenu
                profondeur -= 1
            position += 1
        raise Illisible("arithmétique jamais fermée")

    def _fin_ansi(self, position: int) -> int:
        while position < len(self.t):
            if self.t[position] == "\\":
                position += 2
                continue
            if self.t[position] == "'":
                return position
            position += 1
        raise Illisible("chaîne $'…' jamais fermée")


def _mot_litteral(texte: str) -> Mot:
    return Mot((Partie(LITTERAL, texte),), texte)


def _accolades(nus: str) -> bool:
    """Le texte hors guillemets d'un mot porte-t-il des accolades à étendre (`{a,b}`, `{1..3}`) ?"""
    return bool(re.search(r"\{[^{}]*(,|\.\.)[^{}]*\}", nus))


_ECHAPPEMENTS_ANSI = {
    "n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v",
    "e": "\x1b", "E": "\x1b", "\\": "\\", "'": "'", '"': '"', "?": "?",
}  # fmt: skip


def _ansi(texte: str) -> str:
    """Le texte d'une chaîne `$'…'`, ses échappements courants développés."""
    sortie: list[str] = []
    position = 0
    while position < len(texte):
        if texte[position] != "\\" or position + 1 >= len(texte):
            sortie.append(texte[position])
            position += 1
            continue
        code = texte[position + 1]
        if code in _ECHAPPEMENTS_ANSI:
            sortie.append(_ECHAPPEMENTS_ANSI[code])
            position += 2
        elif code == "x" and re.match(r"[0-9A-Fa-f]{1,2}", texte[position + 2 :]):
            chiffres = re.match(r"[0-9A-Fa-f]{1,2}", texte[position + 2 :]).group(0)  # type: ignore[union-attr]
            sortie.append(chr(int(chiffres, 16)))
            position += 2 + len(chiffres)
        else:
            sortie.append("\\" + code)
            position += 2
    return "".join(sortie)
