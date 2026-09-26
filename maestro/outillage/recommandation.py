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
   les commandes — que docs/38 §3.5 écarte par décision —, chaque usage
   qu'aucun constat ne justifie, et depuis #1295 chaque **pont** qu'aucun client
   ne demande. Sans cette liste, « pas de skill de tests » se lirait comme une
   défaillance de Maestro plutôt que comme un fait du projet, et « pas de
   `CLAUDE.md` » comme un oubli.

Et une quatrième depuis #1295 : **un pont suit un client, jamais une liste**. Un
`CLAUDE.md` ou un `GEMINI.md` ne se propose que pour un client que la personne
utilise et qui ne lit pas `AGENTS.md` à sa version (`_ponts`).

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

from collections.abc import Sequence

from maestro.outillage.clients import CLIENTS_CONNUS, Client, ClientConnu, par_cle
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
    "aucun format de commande n'est commun aux clients — un point d'entrée qu'on "
    "déclenche par son nom est un skill, et un skill s'appelle déjà par son nom "
    "dans deux clients sur quatre"
)


def recommander(constats: Constats, clients: Sequence[Client] = ()) -> Recommandation:
    """L'outillage que `constats` justifie, et ce qui a été écarté.

    L'ordre des entrées est celui de docs/38 §3.6 — les instructions, les ponts
    qu'il faut, puis les skills : c'est l'ordre de lecture d'un projet outillé,
    et c'est celui dans lequel #1033 les écrira.

    `clients` (#1295) sont les clients d'agents que la personne utilise — trouvés
    sur le poste avec leur version (`maestro.clients_du_poste`), ou nommés dans la
    conversation (`maestro.outillage.clients.clients_depuis_texte`). Ils décident
    des **ponts**, et d'eux seuls : aucun client, aucun pont — un client
    introuvable n'est pas supposé présent. La règle vit **ici**, une fois, pour
    le projet importé comme pour le projet neuf (docs/43 §2.3).
    """
    presents = {piece.chemin: piece for piece in constats.outillage_present}
    utilises = par_cle(clients)
    ponts, ponts_ecartes = _ponts(presents, utilises)
    entrees: list[Entree] = [_instructions(constats, presents, utilises)]
    entrees.extend(ponts)
    entrees.extend(_skills(constats))
    ecartes = _ecartes(constats)
    return Recommandation(
        entrees=tuple(entrees), ecartes=(*ecartes[:1], *ponts_ecartes, *ecartes[1:])
    )


def _instructions(
    constats: Constats, presents: dict[str, Piece], utilises: dict[str, Client]
) -> Entree:
    """`AGENTS.md` — toujours recommandé, jamais écrasé, et le **seul** fichier d'instructions.

    C'est la seule entrée qui ne dépend d'aucun constat : un projet sans
    gestionnaire, sans CI et sans test a quand même besoin qu'on dise ce qu'il
    est. Sa **justification** est en revanche bien constatée — le `README` s'il
    y en a un, le dossier de scripts sinon —, parce que c'est de là que son
    contenu sortira. Sa raison nomme les clients de la personne qui le lisent de
    **eux-mêmes** (#1295) : c'est la moitié de « pourquoi un seul fichier ».
    """
    deja = "AGENTS.md" in presents
    readme = next((piece for piece in constats.conventions if piece.nom == "README.md"), None)
    lecteurs = [
        client.nomme()
        for client in utilises.values()
        if (connu := client.connu) is not None
        and connu.lit_agents_md(
            client.version, pont_present=connu.pont is not None and connu.pont in presents
        )
    ]
    lu_par = f" — lu tel quel par {', '.join(lecteurs)}" if lecteurs else ""
    return Entree(
        type="instructions",
        nom="AGENTS.md",
        chemin="AGENTS.md",
        etat="a-completer" if deja else "a-generer",
        raison=(
            "le projet porte déjà un AGENTS.md : Maestro n'y écrirait qu'un bloc délimité, "
            f"sans toucher au reste{lu_par}"
            if deja
            else "le seul fichier d'instructions du projet, au format ouvert que lisent la "
            f"plupart des clients d'agents, et celui qui désigne où sont les skills{lu_par}"
        ),
        justification=readme
        or Piece(
            nom=constats.dossier_scripts.chemin,
            chemin=constats.dossier_scripts.chemin,
            role="dossier de scripts",
        ),
    )


def _ponts(
    presents: dict[str, Piece], utilises: dict[str, Client]
) -> tuple[list[Entree], list[Ecarte]]:
    """Les ponts qu'il faut — et, pour chacun qu'il ne faut pas, pourquoi (#1295, docs/43 §2.3).

    Un pont est un fichier d'**une ligne** qui importe `AGENTS.md` (`@AGENTS.md`),
    jamais une copie : recopier le texte donnerait deux sources pour une
    instruction (docs/38 §3.2). Il ne s'écrit que pour un client que la personne
    **utilise** et qui ne lit pas `AGENTS.md` de lui-même à sa version. Trois cas
    pour chaque client à qui un pont peut manquer (`ClientConnu.pont`) :

    - **il le faut** — le client est utilisé et ne lit pas `AGENTS.md` (Gemini
      CLI ; Claude Code avant la v2.1.277, ou de version inconnue ; ou un pont
      déjà présent qui masque sa lecture native) : une entrée, `a-completer` si
      le fichier existe (Maestro n'y écrit qu'un bloc délimité, docs/38 §4.2) ;
    - **le projet le porte déjà, sans qu'il le faille** — le projet importé garde
      ses ponts : une entrée `deja-present`, jamais retirée ni réécrite ;
    - **il ne le faut pas** — un écarté, avec sa raison : c'est l'autre moitié de
      « pourquoi un seul fichier ».

    Un client que la table ne connaît pas est nommé en écarté : Maestro ne sait
    pas ce qu'il lit, et n'écrit pas un pont au hasard.
    """
    entrees: list[Entree] = []
    ecartes: list[Ecarte] = []
    for connu in CLIENTS_CONNUS:
        if connu.pont is None:
            continue
        client = utilises.get(connu.cle)
        present = connu.pont in presents
        lit = (
            connu.lit_agents_md(client.version, pont_present=present)
            if client is not None
            else None
        )
        if client is not None and lit is not True:
            entrees.append(_pont(connu, _raison_du_pont(connu, client, present, lit), present))
        elif present:
            entrees.append(
                _pont(
                    connu,
                    f"le projet porte déjà {connu.pont} : il est gardé tel quel — "
                    f"{_absent(connu)}, et Maestro ne retire jamais ce qu'il n'a pas écrit",
                    present,
                    etat="deja-present",
                )
            )
        else:
            ecartes.append(Ecarte(type="pont", nom=connu.pont, raison=_pas_de_pont(connu, client)))
    for client in utilises.values():
        if client.connu is None:
            ecartes.append(
                Ecarte(
                    type="pont",
                    nom=client.libelle,
                    raison=(
                        f"{client.nomme()} : Maestro ne sait pas s'il lit AGENTS.md, ni quel "
                        "fichier il lirait à la place — aucun pont n'est écrit sans le savoir"
                    ),
                )
            )
    return entrees, ecartes


def _pont(connu: ClientConnu, raison: str, present: bool, *, etat: str = "") -> Entree:
    """L'entrée d'un pont — son état suit le disque, sauf à le dire."""
    chemin = connu.pont or ""
    return Entree(
        type="pont",
        nom=chemin,
        chemin=chemin,
        etat=etat or ("a-completer" if present else "a-generer"),
        raison=raison,
        justification=Piece(nom="AGENTS.md", chemin="AGENTS.md", role="instructions"),
        commandes=(),
    )


def _raison_du_pont(connu: ClientConnu, client: Client, present: bool, lit: bool | None) -> str:
    """Pourquoi ce pont — le client, sa version, et ce qu'il ne lit pas (#1295)."""
    qui = client.nomme()
    pont = connu.pont
    if present:
        return (
            f"le projet porte déjà {pont}, que {connu.libelle} lit à la place d'AGENTS.md "
            f"({qui}) : Maestro n'y écrirait qu'un bloc délimité d'une ligne (@AGENTS.md), "
            "sans toucher au reste"
        )
    if connu.natif_depuis is None:
        return (
            f"{qui} lit {pont} par défaut, pas AGENTS.md : {pont} l'importe en une ligne "
            "(@AGENTS.md), sans dupliquer son texte"
        )
    if lit is None:
        return (
            f"{qui}, version inconnue : {connu.libelle} ne lit AGENTS.md de lui-même qu'à partir "
            f"de la v{connu.natif_depuis} — {pont} l'importe en une ligne (@AGENTS.md), ce qui "
            "vaut pour toutes ses versions sans jamais le faire lire deux fois"
        )
    return (
        f"{qui} ne lit AGENTS.md de lui-même qu'à partir de la v{connu.natif_depuis} : "
        f"{pont} l'importe en une ligne (@AGENTS.md), sans dupliquer son texte"
    )


def _absent(connu: ClientConnu) -> str:
    """« Gemini CLI n'est ni trouvé sur ce poste ni nommé dans la conversation »."""
    return f"{connu.libelle} n'est ni trouvé sur ce poste ni nommé dans la conversation"


def _pas_de_pont(connu: ClientConnu, client: Client | None) -> str:
    """Pourquoi ce pont ne s'écrit pas : personne n'utilise ce client, ou il lit `AGENTS.md`."""
    if client is None:
        return (
            f"{_absent(connu)} : un pont pour un client que personne n'utilise serait une "
            "supposition"
        )
    return (
        f"{client.nomme()} lit AGENTS.md de lui-même depuis la v{connu.natif_depuis}, et un "
        f"{connu.pont} masquerait cette lecture : aucun pont"
    )


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
