"""Des constats à l'outillage recommandé — avec, pour chacun, la raison et l'endroit (#1030).

La seconde moitié de l'analyse : `maestro.outillage.analyse` a lu le projet,
ce module en déduit ce que Maestro propose d'écrire. Il ne touche pas au disque
— il ne reçoit que des `Constats` —, et c'est ce qui le rend éprouvable sur des
constats fabriqués, sans projet réel.

## Les trois règles qui décident

1. **Rien sans son endroit.** Chaque entrée porte une `justification` : le
   fichier du projet qui la fait exister (`package.json` → `scripts.test`,
   `Cargo.toml` → la convention de cargo). Une recommandation sans son endroit
   serait invérifiable ; avec lui, elle se conteste en ouvrant le fichier.
2. **Ce que le projet porte déjà est reconnu, pas dupliqué.** Un skill de même
   nom présent dans l'un des quatre dossiers que les clients lisent (docs/38
   §2.1) sort en `deja-present`, avec **son** chemin ; un `AGENTS.md` déjà écrit
   sort en `a-completer`, parce que docs/38 §4.2 n'y écrit alors qu'un bloc
   délimité. Rien ne disparaît de la réponse : « déjà là » est une information,
   et une entrée qui s'efface se lirait comme un oubli.
3. **Ce qui n'est pas recommandé est nommé, avec sa raison.** `ecartes` porte
   les commandes — que docs/38 §3.5 écarte par décision — et chaque usage
   qu'aucun constat ne justifie. Sans cette liste, « pas de skill de tests » se
   lirait comme une défaillance de Maestro plutôt que comme un fait du projet.

## Ce que ce module ne décide pas

**L'emplacement.** Il vient de docs/38 §3.3 (`.agents/skills/<nom>/`) et le
module s'y tient : le rouvrir ici, dans un lot marqué parallèle, reviendrait à
le trancher une seconde fois — c'est exactement ce que la note de #1029 existe
pour éviter.

**L'écriture.** C'est #1033, et il aura besoin de deux choses qui sortent d'ici :
`Entree.etat` (les quatre cas de docs/38 §4.2) et
`Analyse.source_manifeste()`.
"""

from __future__ import annotations

from maestro.outillage.detection import DOSSIERS_SKILLS
from maestro.outillage.modele import (
    Commande,
    Constats,
    Ecarte,
    Entree,
    Piece,
    Recommandation,
)

#: Le dossier des skills d'un projet (docs/38 §3.3) : le seul chemin lu par
#: plus d'un client, et le seul qui ne porte le nom d'aucun éditeur. Décidé par
#: #1029, **pas ici**.
DOSSIER_SKILLS = DOSSIERS_SKILLS[0]

#: Usage → (nom du skill, raison). Le nom est en français et en verbe d'action,
#: comme les commandes du dépôt : c'est celui qu'un agent lira dans l'index
#: d'`AGENTS.md` pour décider s'il ouvre le `SKILL.md`, et « tests » n'aurait
#: pas dit s'il s'agit de les lancer ou d'en écrire.
#:
#: `formater` et `types` rejoignent `lint` dans **un seul** skill de
#: vérification : ce sont trois commandes de la même question — *est-ce que ça
#: passe ?* —, et trois skills se seraient disputé le même moment.
SKILL_PAR_USAGE: dict[str, tuple[str, str]] = {
    "installer": (
        "mettre-en-route",
        "un agent qui arrive doit pouvoir installer les dépendances sans deviner le gestionnaire",
    ),
    "construire": (
        "construire-le-projet",
        "la construction a sa commande propre, que rien d'autre ne nomme",
    ),
    "tester": (
        "lancer-les-tests",
        "c'est la vérification que tout agent joue avant de rendre son travail",
    ),
    "lint": (
        "verifier-le-style",
        "le projet a ses vérifications de style : les jouer évite de les redécouvrir en CI",
    ),
    "demarrer": (
        "lancer-en-local",
        "voir tourner le projet est le seul moyen de vérifier un changement d'interface",
    ),
}

#: Les usages que `verifier-le-style` absorbe, en plus du sien.
USAGES_VERIFICATION: tuple[str, ...] = ("lint", "formater", "types")

#: Pourquoi aucun format de commande n'est généré (docs/38 §3.5). Recopié ici
#: parce que c'est la phrase que l'appelant affiche ; la décision, elle, vit
#: dans la note et pas dans ce module.
RAISON_AUCUNE_COMMANDE = (
    "aucun format de commande n'est commun aux clients (docs/38 §3.5) — "
    "un point d'entrée qu'on déclenche par son nom est un skill, et un skill "
    "s'appelle déjà par son nom dans deux clients sur quatre"
)


def recommander(constats: Constats) -> Recommandation:
    """L'outillage que `constats` justifie, et ce qui a été écarté.

    L'ordre des entrées est celui de docs/38 §3.6 — les instructions, les deux
    ponts, puis les skills : c'est l'ordre de lecture d'un projet outillé, et
    c'est celui dans lequel #1033 les écrira.
    """
    presents = {piece.chemin: piece for piece in constats.outillage_present}
    entrees: list[Entree] = [_instructions(constats, presents)]
    entrees.extend(_ponts(presents))
    entrees.extend(_skills(constats))
    return Recommandation(entrees=tuple(entrees), ecartes=tuple(_ecartes(constats)))


def _instructions(constats: Constats, presents: dict[str, Piece]) -> Entree:
    """`AGENTS.md` — toujours recommandé, jamais écrasé.

    C'est la seule entrée qui ne dépend d'aucun constat : un projet sans
    gestionnaire, sans CI et sans test a quand même besoin qu'on dise ce qu'il
    est. Sa **justification** est en revanche bien constatée — le `README` s'il
    y en a un, le dossier de scripts sinon —, parce que c'est de là que son
    contenu sortira.
    """
    deja = "AGENTS.md" in presents
    readme = next((piece for piece in constats.conventions if piece.nom == "README.md"), None)
    return Entree(
        type="instructions",
        nom="AGENTS.md",
        chemin="AGENTS.md",
        etat="a-completer" if deja else "a-generer",
        raison=(
            "le projet porte déjà un AGENTS.md : Maestro n'y écrirait qu'un bloc délimité, "
            "sans toucher au reste (docs/38 §4.2)"
            if deja
            else "le fichier d'instructions que tous les clients lisent, et celui qui désigne "
            "où sont les skills du projet (docs/38 §3.1, §3.3)"
        ),
        justification=readme
        or Piece(
            nom=constats.dossier_scripts.chemin,
            chemin=constats.dossier_scripts.chemin,
            role="dossier de scripts",
        ),
    )


def _ponts(presents: dict[str, Piece]) -> list[Entree]:
    """`CLAUDE.md` et `GEMINI.md` — une ligne chacun, jamais une copie (docs/38 §3.2).

    Les deux clients qui ne lisent pas `AGENTS.md` par défaut. Un pont, pas une
    copie : recopier le texte donnerait trois sources pour une instruction, et
    la première correction faite à l'une des trois créerait l'écart.
    """
    return [
        Entree(
            type="pont",
            nom=nom,
            chemin=nom,
            etat="a-completer" if nom in presents else "a-generer",
            raison=(
                f"{client} ne lit pas AGENTS.md par défaut : {nom} l'importe en une ligne "
                "(@AGENTS.md), sans dupliquer son texte (docs/38 §2.2, §3.2)"
            ),
            justification=Piece(nom="AGENTS.md", chemin="AGENTS.md", role="instructions"),
            commandes=(),
        )
        for nom, client in (("CLAUDE.md", "Claude Code"), ("GEMINI.md", "Gemini CLI"))
    ]


def _skills(constats: Constats) -> list[Entree]:
    """Un skill par usage que le projet justifie — et rien pour ceux qu'il ne justifie pas."""
    presents = _skills_presents(constats)
    entrees: list[Entree] = []
    for usage, (nom, raison) in SKILL_PAR_USAGE.items():
        commandes = _commandes_du_skill(constats, usage)
        scripts = _scripts_pour(constats, usage)
        if not commandes and not scripts:
            continue
        chemin_present = presents.get(nom)
        entrees.append(
            Entree(
                type="skill",
                nom=nom,
                chemin=chemin_present or f"{DOSSIER_SKILLS}/{nom}/SKILL.md",
                etat="deja-present" if chemin_present else "a-generer",
                raison=_raison_du_skill(raison, chemin_present, scripts),
                justification=(scripts[0] if scripts else _justification(commandes[0])),
                commandes=(
                    tuple(f"bash {script.chemin}" for script in scripts)
                    + tuple(commande.commande for commande in commandes)
                ),
            )
        )
    entrees.extend(_scripts_reconnus(constats))
    return entrees


def _raison_du_skill(raison: str, chemin_present: str | None, scripts: list[Piece]) -> str:
    """Pourquoi ce skill — et, le cas échéant, ce qu'il reprend du projet.

    Deux reconnaissances distinctes, et il faut les deux : le **skill** existe
    déjà (on le reprend tel quel), ou le **script** existe déjà (le skill
    l'appelle au lieu de réécrire sa commande). Les confondre ferait disparaître
    l'un des deux cas, et c'est celui du script qui disparaîtrait — il est le
    plus fréquent sur un projet qui a déjà ses habitudes.
    """
    if chemin_present:
        return (
            f"le projet porte déjà ce skill ({chemin_present}) : "
            "il est repris tel quel, pas dupliqué"
        )
    if scripts:
        noms = ", ".join(script.chemin for script in scripts)
        return f"{raison} — et le projet a déjà son script pour ça ({noms}), que le skill appelle"
    return raison


def _commandes_du_skill(constats: Constats, usage: str) -> list[Commande]:
    """Les commandes qu'un skill enveloppe — plusieurs pour la vérification, une sinon."""
    usages = _usages_du_skill(usage)
    trouvees = [
        commande for cible in usages if (commande := constats.commande_de(cible)) is not None
    ]
    return trouvees


def _scripts_pour(constats: Constats, usage: str) -> list[Piece]:
    """Les scripts que le projet porte **déjà** pour cet usage (docs/38 §3.4).

    C'est le cœur de « reconnu plutôt que dupliqué » : un `scripts/test.sh`
    existant est ce que le skill de tests doit appeler, et lui préférer la
    commande brute reviendrait à ignorer la façon dont le projet se joue.
    """
    usages = _usages_du_skill(usage)
    return [piece for piece in constats.dossier_scripts.scripts if piece.role in usages]


def _usages_du_skill(usage: str) -> tuple[str, ...]:
    """Les usages qu'un skill couvre — les trois vérifications pour `lint`, le sien sinon."""
    return USAGES_VERIFICATION if usage == "lint" else (usage,)


def _justification(commande: Commande) -> Piece:
    """L'endroit du projet qui justifie un skill : le fichier lu, et ce qu'on y a vu."""
    return Piece(nom=commande.chemin, chemin=commande.chemin, role=commande.extrait)


def _skills_presents(constats: Constats) -> dict[str, str]:
    """`nom de skill → chemin`, pour les skills que le projet porte déjà.

    Les quatre dossiers de `DOSSIERS_SKILLS` comptent, pas seulement celui que
    docs/38 retient : un skill posé dans `.claude/skills/` par quelqu'un est un
    skill du projet, et en écrire un second dans `.agents/skills/` donnerait
    deux fois la même chose à maintenir — ce que §3.3 écarte explicitement.
    """
    return {
        piece.nom: piece.chemin for piece in constats.outillage_present if piece.role == "skill"
    }


def _scripts_reconnus(constats: Constats) -> list[Entree]:
    """Les scripts du projet qu'un skill appellera — reconnus, jamais réécrits (docs/38 §3.4).

    Le cas que le ticket vise en toutes lettres : un `scripts/test.sh` existant
    est ce que le skill de tests doit appeler. Il sort donc en `deja-present`,
    avec son chemin **depuis la racine du projet** — la seule forme qui ne
    dépende pas du répertoire courant de l'agent, que rien ne garantit.

    Aucun script n'est **recommandé à écrire** : Maestro n'en génère un que
    lorsqu'il en faut un, et ce qu'une analyse constate ici tient toujours en
    une commande. Un script neuf est une décision de génération (#1033), pas un
    constat.
    """
    dossier = constats.dossier_scripts
    return [
        Entree(
            type="script",
            nom=piece.nom,
            chemin=piece.chemin,
            etat="deja-present",
            raison=(
                f"le projet a déjà son script « {piece.nom} » : le skill l'appelle "
                f"depuis la racine (bash {piece.chemin}) au lieu de réécrire sa commande"
            ),
            justification=Piece(
                nom=dossier.chemin,
                chemin=dossier.chemin,
                role="dossier de scripts constaté",
            ),
            commandes=(f"bash {piece.chemin}",),
        )
        for piece in dossier.scripts
        if piece.role
    ]


def _ecartes(constats: Constats) -> list[Ecarte]:
    """Ce qui n'est pas recommandé, et pourquoi — la décision d'abord, les faits ensuite."""
    ecartes = [Ecarte(type="commande", nom="toutes", raison=RAISON_AUCUNE_COMMANDE)]
    for usage, (nom, _) in SKILL_PAR_USAGE.items():
        if _commandes_du_skill(constats, usage) or _scripts_pour(constats, usage):
            continue
        ecartes.append(
            Ecarte(
                type="skill",
                nom=nom,
                raison=(
                    f"aucune commande de « {usage} » constatée dans les bornes de l'analyse : "
                    "un skill sans commande ne dirait rien de plus que son titre"
                ),
            )
        )
    return ecartes
