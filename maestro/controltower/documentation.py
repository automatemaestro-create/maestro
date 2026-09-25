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

⚠ **Et ce n'est pas non plus l'assemblage du prompt.** De `maestro.sources.extraction`
on reprend `estimer_tokens` — le budget, c'est-à-dire ce que la note du ticket
demandait — mais **pas `contexte_markdown`**, et c'est une décision et non un oubli :
son préambule déclare le contenu « fourni par l'utilisateur », « pas fiable », à
signaler plutôt qu'à suivre (ENF-13). C'est juste d'un document téléversé et **faux**
de la documentation du produit, qui est dans le dépôt et relue au merge — dire au
modèle de s'en méfier reviendrait à saper la source même dont le chantier veut qu'il
réponde. La protection qu'apporte l'encadrement est ici obtenue autrement, et plus
tôt : ce qui vient du modèle est un **identifiant**, jamais un chemin, et il ne
résout que dans l'index (décision 4). Encadrer les sections retenues reste possible
au lot 2, qui assemble le prompt — c'est là que la question se pose.

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

## Ce qu'une citation ne porte pas

Ce que l'assistant montre de ses lectures (`SectionDoc.citation`) ne renvoie jamais
à l'intérieur du dépôt : ni chemin ou lien vers un fichier, ni numéro de ticket.
**La règle porte sur les titres, pas seulement sur le nom du fichier** — #939 avait
retiré ce dernier, mais la citation enchaîne un chemin de **titres**, et ceux-ci
portaient les mêmes renvois (10 liens, 31 chemins, 127 numéros sur 801 sections au
2026-09-22). `titre_citable` en porte la démonstration et les trois temps.

⚠ La **carte**, elle, garde les titres bruts : ce n'est pas un oubli. Ce qu'elle
affiche est l'**identifiant** que le modèle recopie, et qui doit résoudre dans
l'index — l'assainir ferait nommer des sections qui n'existent pas.

## Le budget est une erreur franche

`BUDGET_CARTE_TOKENS` est **annoncé et testé**. Le dépasser lève `CarteTropGrande` :
il n'y a pas de troncature, parce qu'une carte amputée ferait choisir le modèle parmi
des sections qu'elle ne montre plus — il chercherait alors ce qu'il ne peut pas voir,
et l'aveu d'ignorance du lot 3 porterait sur une absence qu'on aurait fabriquée. Une
carte qui déborde est une décision à prendre (relever le budget en connaissant le coût
par question, ou resserrer le corpus), pas un réglage à faire au passage.

## Deux niveaux : ce que la mesure du 2026-09-25 a décidé (#1316)

La carte à plat — les 842 titres H1-H3 sur une seule page — a franchi son budget le
2026-09-24. On l'a ramenée à **15 986 / 16 000** en raccourcissant des titres, ce qui
ne répare rien : l'assistant allait cesser de répondre au prochain document. La
mesure, sur l'historique de `main` (641 → 842 sections du 2026-08-29 au 2026-09-26,
soit **≈ 200 sections par mois**) :

| forme de la carte                  | 2026-08-29 | 2026-09-26 | par mois  |
|------------------------------------|-----------:|-----------:|----------:|
| à plat, titres H1-H3               |     11 935 |     15 982 | ≈ + 4 340 |
| **sommaire**, titres H1-H2         |      5 121 |      6 779 | ≈ + 1 780 |

La carte a donc **deux niveaux**. Le premier prompt reçoit le **sommaire**
(`CarteDocumentation.markdown`, niveaux 1 et 2) ; un titre qui porte des
sous-sections y est marqué `+`, et ce qu'il porte se montre **à la demande** par
`CarteDocumentation.detail`, pour les seuls titres que le modèle a choisis. Le
sommaire absorbe un trimestre de croissance (≈ 12 000 tokens au bout de trois mois)
et en tient cinq au rythme mesuré — la sonde de `tests/test_documentation.py` rejoue
le trimestre —, et le détail pèse au plus **2 188** tokens, les six plus gros
chapitres du corpus réunis.

Deux pistes écartées, sur la même mesure :

- **resserrer le corpus seul** : la croissance vient des notes de décision, qui sont
  celles du **produit** (l'équipe sur mesure, le projet né dans la conversation…) —
  les retirer ôterait à l'assistant ce qu'il doit savoir, et la carte à plat de ce
  qui resterait (≈ 9 000 tokens) regagnerait sa limite avant un trimestre ;
- **une carte réduite aux documents** (titres H1 seuls, 1 580 tokens) : elle
  tiendrait des années, mais le modèle choisirait sur quarante-six titres, sans voir
  les chapitres qu'un document porte. Le sommaire est la table des matières qu'on
  lit, pas la liste des livres.

Le budget reste une **borne** : un sommaire qui doublerait ne serait plus « assez
petit pour tenir dans un prompt », et une année de croissance au même rythme le
ferait lever — la sonde le prouve aussi.

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

#: Le niveau de titre le plus profond que le **sommaire** montre (#1316). Au-delà, une
#: section reste dans l'index et se montre au **détail** du titre qui la porte, quand
#: le modèle a choisi ce titre — voir « Deux niveaux » dans le docstring du module.
NIVEAU_SOMMAIRE = 2

#: Le budget du sommaire, **en tokens estimés** — ce qui entre à chaque question dans
#: le premier prompt. Posé le 2026-08-28 sur la carte à plat (11 869 pour 639
#: sections), il borne depuis #1316 le sommaire : 6 779 le 2026-09-26 pour 842
#: sections, et ≈ + 1 780 par mois au rythme mesuré — de quoi tenir cinq mois, sans
#: laisser passer un changement de nature (un sommaire qui doublerait n'est plus
#: « assez petit pour tenir dans un prompt »). Le dépassement lève `CarteTropGrande`.
BUDGET_CARTE_TOKENS = 16_000

#: Ce qui sépare les titres dans le chemin lisible d'une section (`SectionDoc.chemin`).
SEPARATEUR_CHEMIN = " › "

#: Les extensions qui font d'un nom un **fichier**. Une liste de formats, pas un
#: lexique : elle reconnaît une forme (`run.sh`), jamais une intention. Un nom qui
#: n'en porte aucune et n'a pas de `/` — `maestro.queue`, `mcp__chrome` — n'est pas
#: un chemin et reste dans la citation : c'est du vocabulaire du produit.
EXTENSIONS_FICHIER: tuple[str, ...] = (
    "md", "py", "sh", "json", "jsonl", "ts", "tsx", "js", "jsx", "mjs", "cjs",
    "yml", "yaml", "toml", "cfg", "ini", "css", "html", "txt", "tsv", "csv",
    "lock", "example", "Dockerfile",
)

#: Ce qui marque, le temps de la recouture, la place d'une mention retirée. Un
#: caractère que le corpus ne porte pas : la carte est faite de Markdown, et un
#: octet nul n'y survivrait pas à la relecture.
_MARQUE = "\x00"

#: Ce qui remplace une mention retirée quand son segment a encore quelque chose à
#: dire. L'ellipse **avoue le trou** au lieu de recoudre une phrase que personne n'a
#: écrite : une citation charcutée en silence se lirait comme le titre exact.
ELLIPSE = "…"

#: Un lien Markdown. Sa **cible** est un fichier du dépôt par construction dans ce
#: corpus ; son texte, lui, repasse par les autres règles — il est souvent un chemin
#: à son tour (`[docs/24](./24-projets-locaux-et-poste-de-travail.md)`).
_LIEN = re.compile(r"\[([^\[\]]*)\]\([^()]*\)")

#: Un numéro de ticket interne. Il n'y a rien derrière pour qui n'a pas la forge.
_TICKET = re.compile(r"#\d+")

#: Un segment de chemin — ce qui tient entre deux `/`.
_SEGMENT = r"[A-Za-z0-9_@+.-]+"

#: Un chemin vers un fichier du dépôt : soit il porte un `/` entre deux noms, soit
#: il se termine par une extension de fichier. Le renvoi de section qui le suit
#: (`docs/24 §2.5`) part avec lui — seul, il ne désignerait plus rien.
#:
#: Deux formes sont **exclues** à dessein, et c'est ce qui rend le motif utilisable :
#: un rapport de nombres (« Phases 5/6 », « lot 11/15 ») n'est pas un chemin, d'où
#: l'exigence d'une lettre ; une commande (`/run-audit`) ni une route (`/chat`) n'en
#: sont un non plus, d'où le premier segment obligatoire avant le `/`.
#: Deux pièges, tous deux vus sur le corpus réel et tous deux tenus par la forme du
#: motif plutôt que par une exception :
#:
#: - le dernier segment s'écrit `(?:{_SEGMENT})?` et non `{_SEGMENT}?` — la seconde
#:   forme rend le `+` **paresseux** au lieu de rendre le segment optionnel, et le
#:   chemin est alors coupé après sa première lettre (`components/I`), la citation
#:   gardant `…cones.tsx` ;
#: - `(?!\w)` ferme le motif — sans lui, « activer/désactiver » est pris pour un
#:   chemin jusqu'au premier accent (`activer/d`), parce qu'un `é` n'entre pas dans
#:   un segment. Un chemin qui déborde sur un mot n'en est pas un.
_CHEMIN = re.compile(
    r"(?<![\w/])(?:"
    rf"(?=[A-Za-z0-9_@+./-]*[A-Za-z]){_SEGMENT}(?:/{_SEGMENT})*/(?:{_SEGMENT})?"
    rf"|{_SEGMENT}\.(?:{'|'.join(EXTENSIONS_FICHIER)})"
    r")(?!\w)(?:\s*§[\d.]+)?"
)

#: Le balisage Markdown d'un titre. Une citation est du **texte** : elle s'affiche
#: telle quelle, sans rendu. `_` n'en est pas — le corpus ne s'en sert pas pour
#: l'emphase, mais il en porte dans des identifiants (`mcp__chrome`).
_BALISAGE = re.compile(r"\*|~~|`")

#: Un groupe parenthésé, sans imbrication — le corpus n'en porte pas.
_GROUPE = re.compile(r"\s*\([^()]*\)")

#: Ce qui découpe un titre en segments de haut niveau. Dans ce corpus, le tiret
#: cadratin et le deux-points séparent presque toujours le **sujet** de ce qui le
#: qualifie : c'est la coupe qui permet de laisser tomber une qualification devenue
#: muette sans emporter le sujet.
_SEPARATEUR_SEGMENT = re.compile(r"(\s+[—–]\s+|\s*:\s+)")

#: Un caractère qui **dit** quelque chose — lettre ou chiffre. Un segment qui n'en
#: porte plus aucun ne nomme plus rien.
_SIGNIFIANT = re.compile(r"[^\W_]", re.UNICODE)

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


def titre_citable(titre: str) -> str:
    """Le titre tel qu'on peut le **montrer** — sans renvoi vers l'intérieur (#1199).

    #939 avait retiré le nom de fichier de la citation, mais la citation reprend le
    **chemin des titres**, et ces titres portent eux-mêmes ce qu'on venait d'ôter :
    sur le corpus du 2026-09-22 (801 sections), 10 titres portent un lien Markdown
    vers un `.md` du dépôt, 31 un chemin de fichier et 127 un numéro de ticket. Un
    lien mort et un numéro de ticket interne ne sont pas des sources pour qui a
    *installé* Maestro : ce sont deux façons de renvoyer l'utilisateur là où il ne
    peut pas aller.

    Trois temps, et le troisième est celui qui compte :

    1. **le lien perd sa cible** et garde son texte, qui repasse par la suite ;
    2. **la mention non citable** — numéro de ticket, chemin de fichier — est
       marquée, et le balisage Markdown tombe (une citation est du texte) ;
    3. **ce qui n'a plus rien à dire disparaît, le reste avoue son trou.** Un groupe
       parenthésé qui citait un ticket ou un fichier tombe **en entier** : dans un
       titre de documentation, une parenthèse est une annotation, et une annotation
       qui a besoin d'un numéro de ticket est de l'appareil de suivi, pas du produit
       (« Projets… — cadrage (ticket #215) » → « Projets… — cadrage »). Un segment
       qui ne porte plus ni lettre ni chiffre tombe de même. Partout ailleurs, la
       mention laisse `…` : la phrase dit alors qu'il lui manque un mot, au lieu de
       se refermer sur un sens qu'elle n'a plus.

    Rendre le titre brut en repli serait la seule façon de rater le ticket : quand
    il ne reste rien, la fonction rend `""` et c'est `SectionDoc.citation` qui
    décide — elle omet le maillon plutôt que de citer ce qu'elle doit cacher.
    """
    texte = _LIEN.sub(lambda lien: lien.group(1), titre or "")
    texte = _TICKET.sub(_MARQUE, texte)
    texte = _CHEMIN.sub(_MARQUE, texte)
    texte = _BALISAGE.sub("", texte)
    return _recoudre(texte)


def _recoudre(texte: str) -> str:
    """Le titre marqué, rendu lisible — groupes muets ôtés, trous avoués.

    L'ordre est la décision : le groupe parenthésé part **avant** le découpage en
    segments, sinon « (#223) — livré » laisserait une parenthèse vide au milieu
    d'un segment qui, lui, a encore quelque chose à dire.
    """
    texte = _GROUPE.sub(lambda groupe: "" if _MARQUE in groupe.group(0) else groupe.group(0), texte)
    morceaux = _SEPARATEUR_SEGMENT.split(texte)
    garde = ""
    for position in range(0, len(morceaux), 2):
        segment = morceaux[position]
        if not _SIGNIFIANT.search(segment.replace(_MARQUE, " ")):
            continue
        if garde:
            garde += morceaux[position - 1]
        garde += segment
    garde = garde.replace(_MARQUE, ELLIPSE)
    garde = re.sub(r"\(\s*\)", "", garde)
    garde = re.sub(r"\s+", " ", garde)
    # La virgule et la parenthèse fermante seulement : en français le deux-points
    # et le point-virgule gardent leur espace, et la recoudre serait réécrire des
    # titres que le ticket ne touche pas.
    garde = re.sub(r"\s+([,)])", r"\1", garde)
    garde = re.sub(r"^[\s—–:,;]+", "", garde)
    # Le deux-points final ne tombe que **détaché** : collé, il fait partie du mot
    # (« 3.2 Les labels de catégorisation — type::, agent::, prio:: »), et le
    # retirer réécrirait le titre au lieu de le nettoyer.
    garde = re.sub(r"[\s—–,;(]+$", "", garde)
    garde = re.sub(r"\s+:+$", "", garde)
    return "" if garde == ELLIPSE else garde.strip()


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
    #: Le nom **humain** du document, son titre de niveau 1 (#939). Dérivé et non
    #: recopié : c'est le document qui se nomme, pas une table à tenir à jour.
    #: Vide quand le fichier n'ouvre pas sur un `#` — `citation` retombe alors sur
    #: le chemin de titres seul plutôt que d'inventer un nom.
    document: str = ""

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
        """Le chemin de titres complet, **fichier en tête** — pour le prompt et les tests.

        Il nomme un fichier du dépôt : il ne sort donc jamais vers l'utilisateur
        (#939). Ce qu'on lui montre est `citation`.
        """
        return SEPARATEUR_CHEMIN.join((self.fichier, *self.ancetres, self.titre))

    @property
    def citation(self) -> str:
        """La section telle qu'on la **cite à l'utilisateur** — et rien de l'intérieur.

        Le document s'y nomme par son titre (« Guide de démarrage ») et non par son
        chemin (`docs/07-guide-de-demarrage.md`) : quelqu'un qui a *installé* Maestro
        n'a pas ce fichier, et une source qu'on ne peut pas ouvrir ne se vérifie pas
        (#939). Ce qui la rend vérifiable est qu'elle se **nomme** : ainsi désignée,
        elle peut être redemandée à l'assistant, qui en rendra le passage.

        Chaque maillon passe par `titre_citable` (#1199) : le nom de fichier avait
        disparu de la citation, mais les **titres** qu'elle enchaîne portaient à leur
        tour des liens vers des `.md` du dépôt et des numéros de ticket. Un maillon
        qui ne survit pas à l'assainissement est **omis** — rendre son titre brut
        pour ne pas perdre un maillon rendrait justement ce qu'on retire.
        """
        chaine = [
            citable
            for citable in (titre_citable(titre) for titre in (*self.ancetres, self.titre))
            if citable
        ]
        document = titre_citable(self.document)
        # La section de niveau 1 **est** le document : la nommer deux fois
        # (« Guide de démarrage › Guide de démarrage ») serait le seul cas où la
        # citation dirait moins en disant plus.
        if document and (not chaine or document != chaine[0]):
            chaine.insert(0, document)
        return SEPARATEUR_CHEMIN.join(chaine)

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
            "document": self.document,
            "citation": self.citation,
        }


@dataclass(frozen=True)
class CarteDocumentation:
    """Le corpus indexé : sa carte pour un prompt, ses sections, et leur texte.

    `markdown` est **l'artefact mesuré** : le **sommaire** (niveaux 1 et 2, #1316),
    c'est-à-dire ce qui entre à chaque question dans le premier prompt, et `tokens` en
    donne le coût, borné par `BUDGET_CARTE_TOKENS`. Le second niveau n'a pas de champ :
    il se rend à la demande (`detail`), pour les titres que le modèle a choisis.
    `fichiers` vient du corpus et non des sections — un fichier sans titre reste dans
    la carte, et c'est ce qui permet de dire que la carte **couvre** le corpus plutôt
    que ce qu'elle a su y lire.
    """

    markdown: str
    tokens: int
    fichiers: tuple[str, ...]
    sections: tuple[SectionDoc, ...]
    textes: Mapping[str, str]
    empreinte: tuple[tuple[str, int, int], ...]
    _index: Mapping[str, SectionDoc] = field(init=False, repr=False, compare=False)
    _ouvertures: Mapping[str, tuple[SectionDoc, ...]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        # Les clés exactes d'abord, **toutes**, avant la moindre clé normalisée : une
        # section ne doit jamais être masquée par la forme relâchée d'une autre.
        index: dict[str, SectionDoc] = {section.identifiant: section for section in self.sections}
        for section in self.sections:
            index.setdefault(_cle(section.identifiant), section)
        object.__setattr__(self, "_index", MappingProxyType(index))
        object.__setattr__(self, "_ouvertures", MappingProxyType(_ouvertures(self.sections)))

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

    def sous_sections(self, identifiant: str) -> tuple[SectionDoc, ...]:
        """Ce que le sommaire tait sous ce titre — vide pour un titre qui ne porte rien.

        C'est ce qui fait marquer le titre `+` au sommaire, et ce que son détail
        montre. Un identifiant qui ne résout rien ne porte rien, comme ailleurs :
        c'est une réponse nominale, pas une panne.
        """
        section = self.section(identifiant)
        return () if section is None else self._ouvertures.get(section.identifiant, ())

    def detail(self, identifiants: Sequence[str], *, maximum: int | None = None) -> str:
        """Le second niveau de la carte : les titres choisis, et ce qu'ils portent (#1316).

        Chaque titre choisi garde sa ligne — il reste un passage qu'on peut lire pour
        lui-même, le chapeau d'un chapitre répondant parfois mieux que ses parties —,
        et ses sous-sections suivent, indentées sous lui. L'ordre est celui du modèle :
        c'est lui qui a dit ce qui compte d'abord.

        Rend `""` quand **aucun** titre choisi n'a de sous-section : il n'y a rien à
        ouvrir, et c'est ce vide qui dit au répondeur de lire sans détour. Un
        identifiant qui ne résout rien est passé, comme à la sélection ; `maximum`
        borne le nombre de titres montrés, comptés parmi ceux qui résolvent.
        """
        choisies: list[SectionDoc] = []
        for identifiant in identifiants:
            section = self.section(identifiant)
            if section is None or section in choisies:
                continue
            if maximum is not None and len(choisies) >= maximum:
                break
            choisies.append(section)
        if not any(section.identifiant in self._ouvertures for section in choisies):
            return ""
        lignes = [
            "# Détail des titres choisis dans la carte de la documentation",
            "",
            "Chaque titre choisi, puis les sous-sections qu'il porte ; l'indentation "
            "donne la hiérarchie des titres. Une section se désigne toujours par "
            "`<fichier>#<titre>`, recopié tel quel.",
        ]
        fichier = ""
        for section in choisies:
            if section.fichier != fichier:
                fichier = section.fichier
                lignes.extend(["", f"## {fichier}"])
            lignes.append(f"- {section.cle_titre}")
            for sous in self._ouvertures.get(section.identifiant, ()):
                lignes.append(f"{'  ' * (sous.niveau - section.niveau)}- {sous.cle_titre}")
        return "\n".join(lignes) + "\n"


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
    markdown = _rendre_carte(fichiers, par_fichier, len(sections), _ouvertures(sections))
    tokens = estimer_tokens(markdown)
    if budget_tokens is not None and tokens > budget_tokens:
        raise CarteTropGrande(
            f"Le sommaire de la carte du corpus de documentation pèse {tokens} tokens "
            f"estimés, au-delà du budget annoncé de {budget_tokens} ({len(fichiers)} "
            f"fichiers, {len(sections)} sections). Il n'est pas tronqué : une carte amputée "
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
    # Le nom humain du document (#939) : son **premier** titre de niveau 1. Dérivé
    # et non listé, pour la raison qui vaut partout ici — une table de noms à tenir
    # à côté du corpus finirait par nommer un document qui a changé de titre. Un
    # fichier qui n'en porte pas garde un nom vide, et sa citation se réduit à son
    # chemin de titres : on ne lui en invente pas un depuis son nom de fichier,
    # qui est précisément ce que ce ticket retire de l'écran.
    document = next((titre for _index, niveau, titre in titres if niveau == 1), "")
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
            document=document,
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


def _ouvertures(sections: Sequence[SectionDoc]) -> dict[str, tuple[SectionDoc, ...]]:
    """Les sections que le sommaire tait, rangées sous le titre qui les porte (#1316).

    Une section plus profonde que `NIVEAU_SOMMAIRE` se range sous son plus proche
    ancêtre **visible** — son `##` le plus souvent, son `#` quand le document saute un
    niveau. Celle qui n'a aucun ancêtre visible (un fichier qui ouvre sur un `###`)
    reste au sommaire : sans quoi aucun des deux niveaux ne la montrerait, et le
    modèle ne pourrait jamais la choisir. C'est la propriété que les deux niveaux
    doivent à la carte à plat — **tout** ce que l'index porte se voit quelque part.

    La règle vit ici une fois : le sommaire s'en sert pour marquer ses titres, et
    `CarteDocumentation` pour rendre leur détail.
    """
    ouvertures: dict[str, list[SectionDoc]] = {}
    visibles: list[SectionDoc] = []
    fichier = ""
    for section in sections:
        if section.fichier != fichier:
            fichier, visibles = section.fichier, []
        while visibles and visibles[-1].niveau >= section.niveau:
            visibles.pop()
        if section.niveau > NIVEAU_SOMMAIRE and visibles:
            ouvertures.setdefault(visibles[-1].identifiant, []).append(section)
        else:
            visibles.append(section)
    return {identifiant: tuple(portees) for identifiant, portees in ouvertures.items()}


def _rendre_carte(
    fichiers: Sequence[str],
    par_fichier: Mapping[str, Sequence[SectionDoc]],
    nb_sections: int,
    ouvertures: Mapping[str, Sequence[SectionDoc]],
) -> str:
    """Le sommaire en Markdown : un bloc par fichier, une ligne indentée par titre visible.

    Une section rangée dans `ouvertures` n'y a pas de ligne — elle se montre au
    détail du titre qui la porte, et ce titre est marqué `+` pour que le modèle sache
    qu'en le choisissant il en verra davantage. Sans `ouvertures`, c'est la carte à
    plat d'avant #1316, que les tests rendent encore pour mesurer ce qu'elle coûterait.

    L'indentation **est** le chemin de titres : la porter en toutes lettres sur chaque
    ligne ferait plus que doubler le coût de la carte sans rien apprendre (décision 3
    du module). L'exemple de la légende est pris dans le corpus lui-même plutôt
    qu'écrit ici — un exemple recopié survit à la section qu'il cite, et enseigne alors
    une clé qui n'existe plus.
    """
    tues = {sous.identifiant for portees in ouvertures.values() for sous in portees}
    premiere = next(
        (sections[0] for sections in par_fichier.values() if sections),
        None,
    )
    lignes = [
        "# Carte de la documentation de Maestro",
        "",
        f"{len(fichiers)} fichiers, {nb_sections} sections. Une ligne par titre ; "
        "l'indentation donne la hiérarchie des titres.",
    ]
    if tues:
        lignes.append(
            "Ce sommaire montre les titres de niveaux 1 et 2. Un titre marqué `+` porte "
            "des sous-sections, montrées à part quand on le choisit ; un titre marqué "
            "`-` n'en porte pas."
        )
    if premiere is not None:
        lignes.append(
            "Une section se désigne par `<fichier>#<titre>`, recopié tel quel — par "
            f"exemple `{premiere.identifiant}`."
        )
    for relatif in fichiers:
        lignes.extend(["", f"## {relatif}"])
        for section in par_fichier.get(relatif, ()):
            if section.identifiant in tues:
                continue
            puce = "+" if section.identifiant in ouvertures else "-"
            lignes.append(f"{'  ' * (section.niveau - 1)}{puce} {section.cle_titre}")
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
