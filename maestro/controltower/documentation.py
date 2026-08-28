"""La carte du corpus de documentation, et l'extraction d'une section (#763, lot 1 de #748).

Le chantier #748 remplace la table de mots-clés de l'assistance (#123, #684) par un
modèle **documenté** : plutôt que de reconnaître la forme d'une phrase, l'assistant
lit la documentation du produit et répond à partir d'elle, en citant ce sur quoi il
s'appuie. Ce module est le premier maillon — il **indexe** et il **extrait**. Il ne
répond à personne : le répondeur est le lot 2, le retrait de la table le lot 3. À la
fin de ce lot-ci, l'assistance répond exactement comme avant.

## Ce que la mesure du 2026-08-28 a décidé

Trois chiffres, repris et re-mesurés ici avec l'estimateur du dépôt :

- **le corpus interdit le bourrage** — 36 fichiers, 1,58 Mio, ~559 000 tokens
  estimés. Le passer au modèle à chaque question est exclu, et `docs/10-workflow-git.md`
  pèse à lui seul un tiers de l'ensemble sans rien dire d'un écran ;
- **sa carte tient largement** — 639 sections H1-H3, soit une carte de 34,0 Kio et
  **11 869 tokens** estimés, quarante-sept fois moins que le corpus. C'est ce qui rend
  le dispositif possible : le modèle choisit ses sections sur la carte, puis répond à
  partir d'elles seules ;
- **la plomberie existe** — `maestro.sources.extraction.estimer_tokens` chiffre le
  coût, et il est **jamais optimiste** (part ASCII à 3 caractères par token, chaque
  caractère accentué pour un token entier). Un corpus français y est compté large,
  ce qui est exactement le sens qu'on veut à un budget.

## Ce que ce module n'est pas

⚠ **Aucun score lexical.** La réponse évidente à « quel passage répond à cette
question ? » serait un BM25 ou un TF-IDF sur la question de l'utilisateur ; ce serait
réintroduire, un cran plus bas et sous une forme dérivée du corpus, exactement ce que
le chantier supprime. Ici on **indexe** et on **extrait** : rien n'est classé, rien
n'est comparé à une question. Choisir les sections est le travail du modèle (lot 2).

La seule comparaison de chaînes de ce module est la **résolution d'un identifiant**
dans un index — un test d'appartenance à une clé, au même titre qu'un parseur de
format connu. Elle ne juge aucune intention humaine (docs/10, règle du 2026-08-28).

## Quatre décisions, et leurs raisons

**1. Les sections ne s'emboîtent pas.** Une section court de son titre jusqu'au
**titre suivant** de niveau 1 à 3, exclu — ses sous-sections en font donc partie
seulement si elles sont plus profondes que la carte (`####` et au-delà, qui sont du
corps). Les sections partitionnent le fichier : rien n'est rendu deux fois. Emboîter
aurait fait payer trois fois les mêmes octets à qui demande un `##` et son `###`,
c'est-à-dire dépenser le budget qu'on vient de mesurer ; et comme la carte liste
**tous** les titres H1-H3, qui veut le détail le demande par son nom.

**2. Un `#` dans un bloc de code n'est pas un titre.** Sans cette règle, la carte du
corpus d'aujourd'hui porterait **42 sections qui n'existent pas** — dont le gabarit de
playbook de `docs/04-specifications-agents.md`, qui offrirait à la carte un « ## Mission »
et un « ## Garde-fous » dont l'extraction rendrait… le gabarit. Les barrières
` ``` ` et `~~~` sont donc suivies, ouverture et fermeture (règle de CommonMark : la
clôture est du même caractère, au moins aussi longue, et seule sur sa ligne).

**3. L'identifiant est court, le chemin de titres est complet.** Deux besoins qui ne
se servent pas au même endroit : le **prompt** nomme une section et paie chaque
caractère (`<fichier>#<titre>`, ce que la carte affiche), l'**utilisateur** doit
retrouver le passage et veut le chemin entier (`SectionDoc.chemin`, avec ses
ancêtres). Répéter le chemin d'ancêtres sur les 639 lignes de la carte la ferait
passer de 11 869 à **35 421** tokens — trois fois le prix, au-delà du budget, et pour
zéro information nouvelle puisque l'indentation le porte déjà. Un titre en double
**dans un même fichier** — aucun aujourd'hui sur 639 sections — prend un rang (` ~2`),
affiché tel quel dans la carte pour que ce qu'on y lit soit toujours l'identifiant
exact. Un titre en double **dans deux fichiers différents** (8 cas réels, dont
« 1. Prérequis et mise en place » dans trois fichiers) est distingué par le nom du
fichier, qui ouvre l'identifiant.

**4. La carte porte le texte de ses sections.** Elle pèse donc le corpus (~1,6 Mio),
et c'est un prix payé pour deux propriétés : l'extraction est une **recherche dans un
index**, jamais un accès au disque — un identifiant venu du modèle ne peut donc
désigner aucun fichier hors du corpus, la traversée de chemin est sans objet plutôt
que gardée — et le texte rendu est **exactement** celui que la carte décrit, sans
fenêtre entre les deux où le fichier aurait changé.

## Le budget est une erreur franche

`BUDGET_CARTE_TOKENS` est **annoncé et testé**. Le dépasser lève `CarteTropGrande` :
il n'y a pas de troncature, parce qu'une carte amputée ferait choisir le modèle parmi
des sections qu'elle ne montre plus — il chercherait alors ce qu'il ne peut pas voir,
et l'aveu d'ignorance du lot 3 porterait sur une absence qu'on aurait fabriquée. Une
carte qui déborde est une décision à prendre (relever le budget en connaissant le coût
par question, ou resserrer le corpus), pas un réglage à faire au passage.

## Le cache

`carte_documentation` ne recalcule pas la carte à chaque appel — analyser 1,58 Mio pour
répondre à un message le ferait payer à chaque question. Elle la **reconstruit quand un
fichier change** : l'empreinte du corpus (chemin, date de modification, taille de
chaque fichier) est relue à chaque appel — 36 `stat`, quelques microsecondes — et
c'est sa comparaison qui décide. Un fichier ajouté, retiré ou réécrit change
l'empreinte, donc la carte.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from maestro.sources.extraction import estimer_tokens

#: Ce qui **fait** le corpus, relatif à la racine du dépôt, dans l'ordre où la carte
#: le présente. `docs/*.md` ne descend pas : c'est ce qui laisse dehors
#: `docs/presentations/` (les présentations de milestone, du HTML autonome et daté)
#: et `docs/assets/`, sans avoir à les nommer.
MOTIFS_CORPUS: tuple[str, ...] = ("docs/*.md", "apps/web/README.md")

#: Le niveau de titre le plus profond que la carte porte. Au-delà (`####`), un titre
#: est du **corps** : il ne se cite pas et ne coupe pas la section qui le contient.
NIVEAU_MAX = 3

#: Le budget de la carte, **en tokens estimés**. Mesurée à 11 869 le 2026-08-28 sur
#: 639 sections : la marge tient ~230 sections de plus, de quoi absorber la croissance
#: ordinaire du corpus sans laisser passer un changement de nature (une carte qui
#: doublerait n'est plus « assez petite pour tenir dans un prompt »). Le dépassement
#: lève `CarteTropGrande` — voir le docstring du module.
BUDGET_CARTE_TOKENS = 16_000

#: Ce qui sépare les titres dans le chemin lisible d'une section (`SectionDoc.chemin`).
SEPARATEUR_CHEMIN = " › "

#: Un titre ATX de niveau 1 à 3, en colonne 0 (la forme de tout le corpus : aucun
#: titre indenté n'y existe, et l'exiger évite de prendre pour un titre le `#` d'un
#: exemple mis en retrait). La suite finale de `#` est la clôture optionnelle de
#: CommonMark, pas du texte.
_TITRE = re.compile(r"^(#{1,3})[ \t]+(.+?)[ \t]*#*[ \t]*$")

#: Une barrière de bloc de code — trois accents graves ou trois tildes au moins,
#: jusqu'à trois espaces d'indentation, suivis de la chaîne d'information.
_BARRIERE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


class CarteTropGrande(RuntimeError):
    """La carte dépasse son budget de tokens — le corpus a changé de nature.

    Franche à dessein : on ne tronque pas. Une carte amputée ferait choisir le modèle
    parmi des sections qu'elle ne montre plus, et l'ignorance qu'il avouerait alors
    serait celle qu'on lui aurait fabriquée. Voir le docstring du module.
    """


@dataclass(frozen=True)
class SectionDoc:
    """Une section du corpus : de quoi la **désigner** et de quoi la **citer**.

    Elle ne porte pas son texte — c'est la carte qui le tient (`CarteDocumentation.texte`),
    pour qu'une citation reste un petit objet qu'on peut rendre à l'interface.

    Deux formes, et ce n'est pas une redondance (cf. décision 3 du module) :

    - `identifiant` — la clé courte, `<fichier>#<titre>`, celle que la carte affiche
      et que le modèle recopie. C'est elle qui paie des tokens ;
    - `chemin` — le chemin de titres complet, celui qu'on montre à un humain pour
      qu'il retrouve le passage.

    `ligne` est la ligne du **titre** dans le fichier, 1-indexée : elle ne sert pas à
    l'extraction (l'index porte déjà le texte) mais rend la citation vérifiable d'un
    coup d'œil.
    """

    fichier: str
    titre: str
    niveau: int
    ancetres: tuple[str, ...] = ()
    ligne: int = 0
    rang: int = 1

    @property
    def cle_titre(self) -> str:
        """La part « titre » de l'identifiant — telle que la carte l'affiche.

        Le rang n'apparaît qu'à partir de la deuxième section homonyme **du même
        fichier**, cas absent du corpus d'aujourd'hui. Il est affiché dans la carte
        et pas seulement calculé ici : ce qu'on lit sur une ligne de carte doit être
        l'identifiant exact, sinon le modèle recopie une clé qui n'existe pas.
        """
        return self.titre if self.rang <= 1 else f"{self.titre} ~{self.rang}"

    @property
    def identifiant(self) -> str:
        """La clé courte de la section — `<fichier>#<titre>`.

        Le nom de fichier ouvre l'identifiant : c'est lui qui distingue deux sections
        de même titre dans deux fichiers différents (8 cas dans le corpus). Un chemin
        de fichier ne contient jamais `#`, donc la coupure au premier `#` est sûre,
        même pour un titre qui en porte.
        """
        return f"{self.fichier}#{self.cle_titre}"

    @property
    def chemin(self) -> str:
        """Le chemin de titres complet, pour un lecteur humain."""
        return SEPARATEUR_CHEMIN.join((self.fichier, *self.ancetres, self.titre))

    def to_dict(self) -> dict[str, Any]:
        """La section en dict JSON-sérialisable — la forme d'une citation."""
        return {
            "identifiant": self.identifiant,
            "fichier": self.fichier,
            "titre": self.titre,
            "niveau": self.niveau,
            "ancetres": list(self.ancetres),
            "ligne": self.ligne,
            "chemin": self.chemin,
        }


@dataclass(frozen=True)
class CarteDocumentation:
    """Le corpus indexé : sa carte pour un prompt, ses sections, et leur texte.

    `markdown` est **l'artefact mesuré** : c'est lui qui entre dans un prompt et lui
    dont `tokens` donne le coût, borné par `BUDGET_CARTE_TOKENS`. `fichiers` vient du
    corpus et non des sections — un fichier sans titre reste dans la carte, et c'est
    ce qui permet de dire que la carte **couvre** le corpus plutôt que ce qu'elle a su
    y lire.
    """

    markdown: str
    tokens: int
    fichiers: tuple[str, ...]
    sections: tuple[SectionDoc, ...]
    textes: Mapping[str, str]
    empreinte: tuple[tuple[str, int, int], ...]
    _index: Mapping[str, SectionDoc] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Les clés exactes d'abord, **toutes**, avant la moindre clé normalisée : une
        # section ne doit jamais être masquée par la forme relâchée d'une autre.
        index: dict[str, SectionDoc] = {section.identifiant: section for section in self.sections}
        for section in self.sections:
            index.setdefault(_cle(section.identifiant), section)
        object.__setattr__(self, "_index", MappingProxyType(index))

    def section(self, identifiant: str) -> SectionDoc | None:
        """La section désignée par `identifiant`, ou `None` s'il n'en désigne aucune.

        `None` est une réponse **nominale** et non une panne : l'identifiant vient du
        modèle (lot 2), qui peut nommer une section qui n'existe pas — c'est alors un
        aveu d'ignorance à rendre, pas une erreur à lever. Le budget, lui, lève : il
        constate un défaut du corpus, que personne d'autre ne verra.

        La résolution est exacte, avec un repli sur une clé normalisée (casse et
        espaces) pour absorber un identifiant recopié à un espace près. C'est une
        recherche dans un index — rien n'est classé, rien n'est approché.
        """
        brut = (identifiant or "").strip()
        if not brut:
            return None
        trouvee = self._index.get(brut)
        return trouvee if trouvee is not None else self._index.get(_cle(brut))

    def texte(self, identifiant: str) -> str | None:
        """Le texte exact de la section désignée — son titre, son corps, rien de plus.

        Ne touche pas au disque : le texte a été lu quand la carte a été construite,
        et c'est ce qui rend l'extraction insensible à ce qu'un identifiant pourrait
        contenir (cf. décision 4 du module).
        """
        section = self.section(identifiant)
        return None if section is None else self.textes.get(section.identifiant)

    def sections_du_fichier(self, fichier: str) -> tuple[SectionDoc, ...]:
        """Les sections d'un fichier du corpus, dans l'ordre du document."""
        return tuple(section for section in self.sections if section.fichier == fichier)


def fichiers_corpus(racine: Path | str | None = None) -> tuple[tuple[str, Path], ...]:
    """Les fichiers du corpus : `(chemin relatif POSIX, chemin absolu)`, dans l'ordre.

    L'ordre est celui de `MOTIFS_CORPUS`, trié à l'intérieur de chaque motif : il est
    donc déterministe, ce dont dépendent la carte (qu'on relit d'une version à l'autre)
    et l'empreinte (qu'on compare telle quelle).
    """
    base = _racine(racine)
    vus: dict[str, Path] = {}
    for motif in MOTIFS_CORPUS:
        for chemin in sorted(base.glob(motif)):
            if chemin.is_file():
                vus.setdefault(chemin.relative_to(base).as_posix(), chemin)
    return tuple(vus.items())


def empreinte_corpus(racine: Path | str | None = None) -> tuple[tuple[str, int, int], ...]:
    """L'empreinte du corpus : `(chemin, date de modification en ns, taille)` par fichier.

    C'est ce qui décide qu'une carte est **périmée**. Trois signaux dans un seul objet :
    la liste elle-même (un fichier ajouté ou retiré change l'empreinte), la date et la
    taille. Aucun des trois ne se lit sans l'autre — une réécriture de même taille dans
    la même seconde changerait quand même `st_mtime_ns`, qui est en nanosecondes.

    Un fichier disparu entre le parcours et la mesure est simplement absent de
    l'empreinte, donc l'empreinte change, donc la carte se refait : le cas se répare
    tout seul plutôt que de lever.
    """
    empreinte: list[tuple[str, int, int]] = []
    for relatif, chemin in fichiers_corpus(racine):
        try:
            infos = chemin.stat()
        except OSError:  # pragma: no cover - le fichier vient de disparaître
            continue
        empreinte.append((relatif, infos.st_mtime_ns, infos.st_size))
    return tuple(empreinte)


def construire_carte(
    racine: Path | str | None = None,
    *,
    budget_tokens: int | None = BUDGET_CARTE_TOKENS,
) -> CarteDocumentation:
    """Construit la carte du corpus — **sans cache**, à chaque appel.

    Lève `CarteTropGrande` si la carte rendue dépasse `budget_tokens` ; `None` lève la
    borne, ce qui sert à **mesurer** une carte trop grande pour comprendre pourquoi
    elle l'est, jamais à s'en passer en production.
    """
    base = _racine(racine)
    # L'empreinte est prise **avant** la lecture, et l'ordre est le contenu de la
    # décision : un fichier réécrit pendant la construction laisse alors une empreinte
    # plus ancienne que le texte, donc l'appel suivant la voit périmée et refait la
    # carte. Prise après, elle certifierait un contenu qu'on n'a pas lu.
    empreinte = empreinte_corpus(base)
    fichiers: list[str] = []
    sections: list[SectionDoc] = []
    textes: dict[str, str] = {}
    par_fichier: dict[str, list[SectionDoc]] = {}
    for relatif, chemin in fichiers_corpus(base):
        fichiers.append(relatif)
        # `replace` plutôt qu'une exception : le corpus est celui du dépôt, donc un
        # octet invalide est un fichier abîmé — qui ne doit pas emporter l'assistance
        # tout entière, et se verra au caractère de remplacement dans la citation.
        texte = chemin.read_text(encoding="utf-8", errors="replace")
        trouvees = _sections_du_fichier(relatif, texte)
        par_fichier[relatif] = [section for section, _corps in trouvees]
        for section, corps in trouvees:
            sections.append(section)
            textes[section.identifiant] = corps
    markdown = _rendre_carte(fichiers, par_fichier, len(sections))
    tokens = estimer_tokens(markdown)
    if budget_tokens is not None and tokens > budget_tokens:
        raise CarteTropGrande(
            f"La carte du corpus de documentation pèse {tokens} tokens estimés, au-delà "
            f"du budget annoncé de {budget_tokens} ({len(fichiers)} fichiers, "
            f"{len(sections)} sections). Elle n'est pas tronquée : une carte amputée "
            "ferait choisir le modèle parmi des sections qu'elle ne montre plus. "
            "Relever BUDGET_CARTE_TOKENS se décide en connaissant le coût par question, "
            "ou bien c'est le corpus qu'il faut resserrer."
        )
    return CarteDocumentation(
        markdown=markdown,
        tokens=tokens,
        fichiers=tuple(fichiers),
        sections=tuple(sections),
        textes=MappingProxyType(textes),
        empreinte=empreinte,
    )


def carte_documentation(
    racine: Path | str | None = None,
    *,
    budget_tokens: int | None = BUDGET_CARTE_TOKENS,
) -> CarteDocumentation:
    """La carte du corpus, **construite une fois** et refaite quand un fichier change.

    L'empreinte est relue à chaque appel (36 `stat`) et comparée à celle de la carte en
    cache : c'est bien moins que de réanalyser 1,58 Mio, et c'est ce qui fait que le
    dispositif suit le dépôt sans qu'on ait à le prévenir.
    """
    base = _racine(racine)
    empreinte = empreinte_corpus(base)
    connue = _CACHE.get(base)
    if connue is not None and connue.empreinte == empreinte:
        return connue
    with _VERROU:
        # Relu sous le verrou : deux appels simultanés sur un corpus qui vient de
        # changer ne doivent analyser le corpus qu'une fois. La carte de celui qui a
        # gagné la course ressort par le même chemin que celle qu'on vient de
        # construire — il n'y a qu'une sortie, donc qu'un comportement à tenir.
        connue = _CACHE.get(base)
        if connue is None or connue.empreinte != empreinte:
            connue = construire_carte(base, budget_tokens=budget_tokens)
            _CACHE[base] = connue
        return connue


def oublier_carte(racine: Path | str | None = None) -> None:
    """Vide le cache — celui d'une racine, ou tout entier si `racine` est `None`.

    Le cache s'invalide seul par l'empreinte ; ce verbe est là pour les tests et pour
    reprendre la main après un changement que la date de modification ne montrerait
    pas (une restauration qui remet un fichier tel qu'il était, à la taille près).
    """
    if racine is None:
        _CACHE.clear()
        return
    _CACHE.pop(_racine(racine), None)


def extraire_section(
    identifiant: str,
    *,
    carte: CarteDocumentation | None = None,
) -> str | None:
    """Le texte exact de la section nommée, ou `None` si aucune ne porte ce nom.

    Le raccourci de `CarteDocumentation.texte` sur la carte en cache — la forme dont
    le répondeur du lot 2 se servira, une section à la fois.
    """
    reference = carte if carte is not None else carte_documentation()
    return reference.texte(identifiant)


def _sections_du_fichier(relatif: str, texte: str) -> tuple[tuple[SectionDoc, str], ...]:
    """Les sections d'un fichier et leur texte, dans l'ordre du document.

    Les sections **partitionnent** le fichier : chacune court de son titre jusqu'au
    titre suivant de niveau 1 à 3, exclu (décision 1 du module). Ce qui précède le
    premier titre — aucun fichier du corpus n'en porte — n'est dans aucune section :
    une section se cite par son titre, et un préambule n'en a pas.
    """
    lignes = texte.splitlines()
    titres = [
        (index, niveau, titre)
        for index, niveau, titre in _titres(lignes)
        if niveau <= NIVEAU_MAX
    ]
    trouvees: list[tuple[SectionDoc, str]] = []
    pile: list[tuple[int, str]] = []
    rangs: dict[str, int] = {}
    for position, (index, niveau, titre) in enumerate(titres):
        while pile and pile[-1][0] >= niveau:
            pile.pop()
        fin = titres[position + 1][0] if position + 1 < len(titres) else len(lignes)
        rangs[titre] = rangs.get(titre, 0) + 1
        section = SectionDoc(
            fichier=relatif,
            titre=titre,
            niveau=niveau,
            ancetres=tuple(ancetre for _niveau, ancetre in pile),
            ligne=index + 1,
            rang=rangs[titre],
        )
        trouvees.append((section, "\n".join(lignes[index:fin]).rstrip()))
        pile.append((niveau, titre))
    return tuple(trouvees)


def _titres(lignes: Sequence[str]) -> Iterator[tuple[int, int, str]]:
    """Les titres ATX de `lignes` — `(index 0-indexé, niveau, titre)`, hors blocs de code.

    Les barrières de code sont suivies parce qu'un `#` en tête de ligne y est du texte
    et non un titre : sans cette passe, le corpus d'aujourd'hui rendrait 42 sections
    fantômes (décision 2 du module). La clôture est celle de CommonMark — même
    caractère, au moins aussi longue, seule sur sa ligne —, et l'ouverture par accents
    graves refuse une chaîne d'information qui en contient un.
    """
    barriere = ""
    for index, ligne in enumerate(lignes):
        barre = _BARRIERE.match(ligne)
        if barriere:
            if (
                barre is not None
                and barre.group(1)[0] == barriere[0]
                and len(barre.group(1)) >= len(barriere)
                and not barre.group(2).strip()
            ):
                barriere = ""
            continue
        if barre is not None and (barre.group(1)[0] == "~" or "`" not in barre.group(2)):
            barriere = barre.group(1)
            continue
        titre = _TITRE.match(ligne)
        if titre is not None:
            yield index, len(titre.group(1)), titre.group(2).strip()


def _rendre_carte(
    fichiers: Sequence[str],
    par_fichier: Mapping[str, Sequence[SectionDoc]],
    nb_sections: int,
) -> str:
    """La carte en Markdown : un bloc par fichier, une ligne indentée par section.

    L'indentation **est** le chemin de titres : la porter en toutes lettres sur chaque
    ligne ferait plus que doubler le coût de la carte sans rien apprendre (décision 3
    du module). L'exemple de la légende est pris dans le corpus lui-même plutôt
    qu'écrit ici — un exemple recopié survit à la section qu'il cite, et enseigne alors
    une clé qui n'existe plus.
    """
    premiere = next(
        (sections[0] for sections in par_fichier.values() if sections),
        None,
    )
    lignes = [
        "# Carte de la documentation de Maestro",
        "",
        f"{len(fichiers)} fichiers, {nb_sections} sections. Une ligne par section ; "
        "l'indentation donne la hiérarchie des titres.",
    ]
    if premiere is not None:
        lignes.append(
            "Une section se désigne par `<fichier>#<titre>`, recopié tel quel — par "
            f"exemple `{premiere.identifiant}`."
        )
    for relatif in fichiers:
        lignes.extend(["", f"## {relatif}"])
        for section in par_fichier.get(relatif, ()):
            lignes.append(f"{'  ' * (section.niveau - 1)}- {section.cle_titre}")
    return "\n".join(lignes) + "\n"


def _cle(identifiant: str) -> str:
    """La forme normalisée d'un identifiant — casse et espaces.

    Le repli de `CarteDocumentation.section`, et rien de plus : deux identifiants qui
    ne diffèrent que par la casse ou l'espacement désignent la même section. Ce n'est
    pas une approximation — une clé normalisée est présente ou absente, jamais proche.
    """
    return " ".join(identifiant.split()).casefold()


def _racine(racine: Path | str | None) -> Path:
    """La racine du corpus — celle qu'on passe, ou celle du dépôt."""
    if racine is not None:
        return Path(racine).resolve()
    return Path(__file__).resolve().parents[2]


#: Les cartes déjà construites, par racine résolue. Le verrou n'évite pas une erreur
#: (deux constructions concurrentes rendraient la même carte) mais le double travail :
#: analyser 1,56 Mo deux fois parce que deux questions sont arrivées ensemble.
_CACHE: dict[Path, CarteDocumentation] = {}
_VERROU = threading.Lock()
