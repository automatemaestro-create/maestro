"""De l'outillage recommandé au **texte** qui sera écrit dans le projet (#1033).

Le pendant inerte de `maestro.outillage.generation` : ce module **rédige** —
il rend le contenu de chaque fichier de l'arbre de
[docs/38 §3.6](../../docs/38-decision-outillage-universel-du-projet.md) — et il
ne touche pas au disque. C'est la même frontière qu'entre `modele` et `analyse`,
et elle rend les gabarits éprouvables sur des constats fabriqués, sans projet
réel.

    from maestro.outillage import analyser, rediger

    analyse = analyser("D:/projets/depensio", projet_id="prj-7f3a")
    fichiers = rediger(analyse.constats, analyse.recommandation)
    fichiers[0].chemin      # "AGENTS.md"
    fichiers[0].portee      # "bloc" si le projet en avait déjà un, "fichier" sinon

## Trois propriétés à ne pas défaire

1. **Le rendu est déterministe.** Aucun horodatage, aucun identifiant, aucun
   ordre de dictionnaire ne rentre dans le texte : deux rédactions des mêmes
   constats rendent le **même octet**. C'est ce qui donne son sens au cas
   « empreinte identique » de docs/38 §4.2 — sans lui, régénérer réécrirait tout
   à chaque fois et la question « quelqu'un y a-t-il touché ? » n'aurait plus de
   réponse. La date de génération vit dans le manifeste, où elle ne se compare
   à rien.
2. **Rien n'est inventé.** Chaque commande écrite dans un `SKILL.md` ou dans
   `AGENTS.md` vient d'une `Entree` de la recommandation ou d'un `Constat` — donc
   d'un fichier du projet, lu. Un projet sans commande de test ne reçoit pas un
   skill de tests avec une commande plausible : il n'en reçoit pas (la
   recommandation l'a déjà écarté, avec sa raison).
3. **La portée suit l'état de l'entrée.** `a-completer` — le projet avait déjà
   son `AGENTS.md` — rend un fichier de **portée bloc** : ce qui sera écrit est
   le seul intérieur de `<!-- BEGIN:maestro-outillage -->`, jamais le fichier.
   `deja-present` ne rend **rien** : ce que le projet porte est reconnu, pas
   dupliqué (docs/38 §3.3).

## Ce que ce module ne décide pas

**L'emplacement** (docs/38 §3.3, porté par `recommandation.DOSSIER_SKILLS`) et
**les quatre cas d'écrasement** (docs/38 §4.2, portés par
`maestro.outillage.generation`). Ici on ne répond qu'à *quel texte*.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from maestro import __version__
from maestro.outillage.contexte import BALISE_DEBUT, BALISE_FIN
from maestro.outillage.detection import CHEMIN_MANIFESTE
from maestro.outillage.modele import Commande, Constats, Entree, Recommandation
from maestro.outillage.questionnaire import SOURCE_CHOIX
from maestro.outillage.recommandation import DOSSIER_SKILLS, SKILL_PAR_USAGE, USAGES_VERIFICATION

#: Les portées d'un fichier généré (docs/38 §4.1, champ `portee`) : le fichier
#: entier quand Maestro l'écrit, le seul bloc délimité quand il s'invite dans un
#: fichier que le projet possédait déjà.
PORTEE_FICHIER = "fichier"
PORTEE_BLOC = "bloc"

#: Ce que `genere_par` porte dans le manifeste et dans le pied d'`AGENTS.md` —
#: « en toutes lettres, jamais dérivée après coup » (docs/38 §4.1). Une constante
#: plutôt qu'un f-string recopié : les deux endroits doivent dire la même chose,
#: et c'est ce nom qu'on relit six mois plus tard pour savoir quelle version a
#: écrit ce fichier.
GENERE_PAR = f"maestro {__version__}"

#: Le texte d'un pont (docs/38 §3.2) : la syntaxe d'import de Claude Code et de
#: Gemini CLI, vérifiée par #1029. **Une ligne, jamais une copie** — recopier
#: `AGENTS.md` donnerait trois sources pour une instruction, et la première
#: correction faite à l'une des trois créerait l'écart.
TEXTE_PONT = "@AGENTS.md"

#: La désignation de docs/38 §3.3, recopiée **au mot** depuis la note : c'est par
#: elle que Claude Code — le seul des quatre clients qui ne balaie pas
#: `.agents/skills/` — atteint les skills du projet. La reformuler serait rouvrir
#: une décision prise ailleurs.
DESIGNATION_SKILLS = (
    f"Les skills de ce projet sont dans `{DOSSIER_SKILLS}/`. Si ton agent ne les "
    "charge pas de lui-même, lis le `SKILL.md` de celui qui correspond à ta tâche "
    "avant de commencer."
)

#: Nom du skill → sa `description` de frontmatter. Écrite ici et pas dérivée de
#: `SKILL_PAR_USAGE` : la *raison* d'un skill dit pourquoi Maestro le recommande
#: (elle s'affiche dans l'analyse), sa *description* dit à un agent **quand
#: l'ouvrir** — ce sont deux phrases pour deux lecteurs, et c'est la seconde qui
#: décide de la divulgation progressive (docs/38 §2.1).
DESCRIPTION_PAR_SKILL: dict[str, str] = {
    "mettre-en-route": (
        "Installer les dépendances du projet avec son gestionnaire, avant toute "
        "autre chose sur un poste ou un espace de travail neuf."
    ),
    "construire-le-projet": (
        "Construire le projet — compilation, bundle, artefacts — avec la commande "
        "que le projet déclare."
    ),
    "lancer-les-tests": (
        "Jouer la suite de tests du projet et lire son verdict. À utiliser avant "
        "de rendre un travail, et après tout changement de code."
    ),
    "verifier-le-style": (
        "Jouer les vérifications du projet — style, formatage, types — avant de "
        "rendre un travail, pour ne pas les redécouvrir en intégration continue."
    ),
    "lancer-en-local": (
        "Démarrer le projet en local pour le voir tourner : le seul moyen de "
        "vérifier un changement d'interface autrement qu'en le lisant."
    ),
}

#: Usage → nom du skill qui le sert, dérivé de `SKILL_PAR_USAGE` plutôt que
#: recopié — et l'inverse aussi, parce qu'une `Entree` de skill porte son nom et
#: non son usage. Deux tables écrites à la main auraient fini par ne plus se
#: répondre.
SKILL_PAR_NOM: dict[str, str] = {nom: usage for usage, (nom, _) in SKILL_PAR_USAGE.items()}

#: Les six sections d'`AGENTS.md` (docs/38 §3.1), dans l'ordre. La spécification
#: n'en impose aucune ; ce gabarit est stable parce que c'est lui qui permet de
#: **régénérer** (#1033) et de **relire** (#1032) sans deviner où commence quoi.
SECTIONS: tuple[str, ...] = (
    "Le projet",
    "Monter et lancer",
    "Vérifier",
    "Conventions",
    "L'outillage de ce projet",
    "Ce qu'un agent ne touche pas",
)

#: Ce qu'un agent ne touche pas, en toutes lettres. Les deux premières lignes
#: sont les gisements de secrets de docs/24 §2.5 — les mêmes que le périmètre
#: d'un projet retire d'office —, la dernière est l'atelier de Maestro
#: (docs/38 §4.3) : il porte la comptabilité de la génération, pas le livrable.
INTOUCHABLES: tuple[str, ...] = (
    "`.env` et tout fichier d'environnement : ils portent des secrets. Lis "
    "`.env.example` s'il existe.",
    "Tout ce qui vit sous un dossier `secrets/`.",
    "Ce que le projet génère — dépendances installées, artefacts de build, "
    "caches : cela se refabrique, cela ne s'édite pas.",
    f"`.maestro/` : l'atelier de Maestro (dont `{CHEMIN_MANIFESTE}`, qui dit ce "
    "qui vient de lui). Ce n'est pas le livrable du projet.",
)


@dataclass(frozen=True)
class Fichier:
    """Un fichier à écrire dans le projet : son chemin, son rôle, sa portée, son texte.

    `chemin` est **relatif à la racine**, en POSIX — la forme du manifeste et la
    seule qui ne dépende pas du répertoire courant (docs/38 §3.4). `role` est
    celui de docs/38 §4.1 (`instructions`, `pont`, `skill`, `script`) : c'est lui
    que `maestro.outillage.contexte` relit pour décider ce qui part dans le
    contexte d'un agent. `portee` dit ce que Maestro possède — le fichier, ou le
    seul bloc délimité qu'il s'autorise dans un fichier écrit avant lui.

    `contenu` est le texte **entier** dans les deux cas : en portée `bloc`, c'est
    ce qui ira entre les balises, et l'insertion est l'affaire de la génération.
    """

    chemin: str
    role: str
    portee: str
    contenu: str
    executable: bool = False


def rediger(
    constats: Constats,
    recommandation: Recommandation,
    *,
    portees: Mapping[str, str] | None = None,
    source: Mapping[str, Any] | None = None,
) -> tuple[Fichier, ...]:
    """Les fichiers à écrire pour l'outillage que `recommandation` retient.

    L'ordre est celui de la recommandation, donc celui de docs/38 §3.6 : les
    instructions, les deux ponts, puis les skills. C'est l'ordre de lecture d'un
    projet outillé, et c'est celui dans lequel le manifeste les déclarera.

    `portees` est **la mémoire de ce que Maestro possède**, relue du manifeste
    (`maestro.outillage.generation.portees_declarees`) : `chemin → portée`. Elle
    l'emporte sur l'état de l'entrée, et c'est ce qui rend une **régénération**
    possible — sans elle, l'analyse rejouée après une première génération voit
    son propre travail comme celui du projet : elle passe `AGENTS.md` en
    `a-completer` (Maestro s'ajouterait alors un bloc dans un fichier qu'il a
    écrit entier) et les skills en `deja-present` (ils quitteraient le plan, donc
    le manifeste). Mesuré sur le banc du 2026-09-20, avant que ce paramètre
    existe : la seconde génération dupliquait `AGENTS.md` dans lui-même et
    retirait les quatre skills de la comptabilité.

    Les entrées `deja-present` que le manifeste **ne** déclare pas ne rendent
    aucun fichier : ce que le projet porte de son côté est reconnu, jamais
    dupliqué. Une entrée d'un type inconnu est sautée plutôt que devinée — un
    type que ce module ne sait pas rédiger n'a rien à faire dans l'arbre du
    projet.

    `source` est le fragment de provenance que le manifeste gardera (docs/38
    §4.1). `AGENTS.md` n'en retient qu'une chose : un outillage qui vient des
    **réponses** d'un projet neuf le dit (#1100, `_ligne_origine`).
    """
    declarees = portees or {}
    index = _index_des_skills(recommandation)
    fichiers: list[Fichier] = []
    for entree in recommandation.entrees:
        declaree = declarees.get(entree.chemin)
        if entree.etat == "deja-present" and declaree is None:
            continue
        fichier = _fichier(entree, constats, index, declaree, source)
        if fichier is not None:
            fichiers.append(fichier)
    return tuple(fichiers)


def _fichier(
    entree: Entree,
    constats: Constats,
    index: tuple[tuple[str, str], ...],
    declaree: str | None,
    source: Mapping[str, Any] | None = None,
) -> Fichier | None:
    """Le fichier que rend une entrée recommandée, ou `None` si son type n'en rend pas.

    `declaree` — la portée que le manifeste garde pour ce chemin — l'emporte sur
    celle que l'état de l'entrée suggérerait : c'est **ce que Maestro possède**
    qui décide comment il le réécrit, et le disque ne sait pas le dire (docs/38
    §4.2).
    """
    portee = declaree or (PORTEE_BLOC if entree.etat == "a-completer" else PORTEE_FICHIER)
    if entree.type == "instructions":
        return Fichier(
            chemin=entree.chemin,
            role="instructions",
            portee=portee,
            contenu=texte_instructions(constats, index, source),
        )
    if entree.type == "pont":
        return Fichier(chemin=entree.chemin, role="pont", portee=portee, contenu=TEXTE_PONT)
    if entree.type == "skill":
        return Fichier(
            chemin=entree.chemin,
            role="skill",
            portee=portee,
            contenu=texte_skill(entree),
        )
    if entree.type == "script":
        return Fichier(
            chemin=entree.chemin,
            role="script",
            portee=portee,
            contenu=texte_script(entree),
            executable=True,
        )
    return None


def _index_des_skills(recommandation: Recommandation) -> tuple[tuple[str, str], ...]:
    """L'index `(nom, description)` des skills, **dérivé du frontmatter** (docs/38 §3.1).

    « L'index des skills est dérivé, jamais écrit à la main » : c'est le couple
    `name`/`description` de chaque `SKILL.md`, recopié au moment de la
    génération. Deux orthographes d'une même description finiraient par diverger,
    et c'est la description qui décide si un agent ouvre le skill.

    Les skills `deja-present` **y figurent** — ce sont des skills du projet, que
    l'agent doit pouvoir trouver, et l'index est une désignation, pas un
    inventaire de ce que Maestro a écrit.
    """
    return tuple(
        (entree.nom, DESCRIPTION_PAR_SKILL.get(entree.nom, _raison_stable(entree)))
        for entree in recommandation.entrees
        if entree.type == "skill"
    )


def _raison_stable(entree: Entree) -> str:
    """Pourquoi ce skill existe — **la raison du skill**, pas celle de la recommandation.

    `Entree.raison` est écrite pour qui lit l'analyse, et elle change avec l'état
    du projet : un skill que Maestro a écrit lui-même se relit ensuite en « le
    projet porte déjà ce skill, il est repris tel quel ». La recopier dans le
    `SKILL.md` ferait donc que **régénérer réécrirait le fichier à chaque fois**,
    pour une différence qui ne dit rien de plus — et le cas « empreinte
    identique » de docs/38 §4.2 ne serait jamais atteint (mesuré sur le banc du
    2026-09-20). La raison rendue ici est celle de `SKILL_PAR_USAGE`, qui ne
    dépend que de l'usage servi ; ce que le projet apporte reste dit par les
    commandes et par la ligne « constaté dans … ».
    """
    usage = SKILL_PAR_NOM.get(entree.nom, "")
    connu = SKILL_PAR_USAGE.get(usage)
    return connu[1] if connu is not None else entree.raison


def texte_instructions(
    constats: Constats,
    index: tuple[tuple[str, str], ...],
    source: Mapping[str, Any] | None = None,
) -> str:
    """Le texte d'`AGENTS.md` — les six sections de docs/38 §3.1, dans l'ordre.

    Une section sans matière n'est pas supprimée : elle porte une phrase qui dit
    que l'analyse n'a rien constaté. Un gabarit à sections variables serait
    illisible à la régénération (le diff porterait sur la structure), et surtout
    « le projet ne déclare pas comment on le teste » est une information que
    l'agent doit avoir — l'absence de section la lui cacherait.
    """
    blocs = [
        "# AGENTS.md",
        "",
        "Les instructions de ce projet, pour tout agent qui y travaille.",
        "",
        *_section("Le projet", _corps_projet(constats, source)),
        *_section("Monter et lancer", _corps_monter(constats)),
        *_section("Vérifier", _corps_verifier(constats)),
        *_section("Conventions", _corps_conventions(constats)),
        *_section("L'outillage de ce projet", _corps_outillage(constats, index)),
        *_section("Ce qu'un agent ne touche pas", [f"- {ligne}" for ligne in INTOUCHABLES]),
        "---",
        "",
        f"_Écrit par {GENERE_PAR}. Ce qui vient de Maestro — et lui seul — est "
        f"déclaré dans `{CHEMIN_MANIFESTE}` ; le reste de ce fichier vous "
        "appartient._",
    ]
    return "\n".join(blocs).rstrip() + "\n"


def _section(titre: str, corps: list[str]) -> list[str]:
    """Une section de niveau 2, suivie d'une ligne vide."""
    return [f"## {titre}", "", *corps, ""]


def _corps_projet(constats: Constats, source: Mapping[str, Any] | None = None) -> list[str]:
    """Ce que le projet **est** — langages, gestionnaires, forge, intégration continue.

    En liste et non en paragraphe rédigé : chaque ligne est un constat avec son
    chiffre ou son fichier, et un paragraphe les aurait liés par des phrases que
    rien n'a constatées.
    """
    lignes: list[str] = []
    if constats.langages:
        parts = ", ".join(
            f"{langage.nom} ({round(langage.part * 100)} %)" for langage in constats.langages
        )
        lignes.append(f"- **Langages** : {parts}.")
    if constats.gestionnaires:
        outils = ", ".join(
            f"{g.nom} (`{g.chemin}`{'' if not g.verrou else f', verrou `{g.verrou}`'})"
            for g in constats.gestionnaires
        )
        lignes.append(f"- **Gestionnaires** : {outils}.")
    if constats.ci:
        lignes.append(
            "- **Intégration continue** : "
            + ", ".join(f"{piece.role or piece.nom} (`{piece.chemin}`)" for piece in constats.ci)
            + "."
        )
    if constats.forge is not None:
        distant = f" — `{constats.forge.distant}`" if constats.forge.distant else ""
        lignes.append(f"- **Forge** : {constats.forge.nom}{distant}.")
    if constats.vcs is not None:
        lignes.append(
            f"- **Versions** : {constats.vcs.type}, branche de base "
            f"`{constats.vcs.branche_base or '(HEAD détaché)'}`."
        )
    if not lignes:
        lignes.append(
            "L'analyse n'a constaté ni langage dominant, ni gestionnaire de paquets : "
            "demande à la personne qui te confie la tâche ce qu'est ce projet."
        )
    return [_ligne_origine(source), *lignes] if _vient_des_choix(source) else lignes


def _vient_des_choix(source: Mapping[str, Any] | None) -> bool:
    """L'outillage vient-il des réponses d'un projet neuf ? Le manifeste le dit, pas le texte."""
    return source is not None and source.get("type") == SOURCE_CHOIX


def _ligne_origine(source: Mapping[str, Any]) -> str:
    """La ligne qui dit qu'un outillage vient des **réponses**, et pas d'une lecture (#1100).

    Sans elle, les lignes qui suivent se liraient comme des constats : « npm
    (`package.json`) », « convention de l'outil constaté dans `package.json` »
    sur un dossier où `package.json` n'existe pas encore. Les constats d'un
    projet neuf sont ceux que ses réponses **impliquent** (`constats_depuis_choix`),
    et un agent qui les prendrait pour une lecture chercherait des fichiers
    absents. Elle ne sort que pour une source `choix` : une analyse dit déjà sa
    provenance ligne par ligne (« déclarée dans … »), et y ajouter son
    identifiant réécrirait `AGENTS.md` à chaque lecture du projet.

    Le résumé est celui du manifeste (`resume_des_choix`) : stable pour des
    réponses inchangées, donc une régénération ne réécrit rien.
    """
    resume = str(source.get("resume") or "").strip()
    reponses = f" ({resume})" if resume else ""
    return (
        f"- **Origine** : cet outillage vient des réponses données à Maestro{reponses}, "
        "pas d'une lecture du dossier. Les fichiers nommés ici sont ceux que ces "
        "réponses impliquent : certains n'existent peut-être pas encore."
    )


def _corps_monter(constats: Constats) -> list[str]:
    """Installer, construire, démarrer — les commandes **constatées**, avec leur source."""
    lignes = [
        _ligne_commande(constats, "installer", "Installer les dépendances"),
        _ligne_commande(constats, "construire", "Construire"),
        _ligne_commande(constats, "demarrer", "Démarrer en local"),
    ]
    presentes = [ligne for ligne in lignes if ligne]
    if not presentes:
        return [
            "Le projet ne déclare aucune commande d'installation ni de démarrage. "
            "N'en invente pas : demande-la, ou lis son README."
        ]
    return ["Depuis la **racine du projet** :", "", *presentes]


def _corps_verifier(constats: Constats) -> list[str]:
    """Tests, style, formatage, types — et ce qu'il faut savoir d'une suite partielle."""
    lignes = [
        _ligne_commande(constats, "tester", "Tests"),
        *(
            _ligne_commande(constats, usage, libelle)
            for usage, libelle in (
                ("lint", "Style"),
                ("formater", "Formatage"),
                ("types", "Types"),
            )
        ),
    ]
    presentes = [ligne for ligne in lignes if ligne]
    if not presentes:
        return [
            "Le projet ne déclare aucune commande de test ni de vérification. "
            "Ne conclus pas qu'il n'en a pas : demande avant de rendre ton travail."
        ]
    return [
        "Depuis la **racine du projet** :",
        "",
        *presentes,
        "",
        "Joue ce qui concerne ce que tu as changé plutôt que tout : une suite "
        "entière sur un changement d'une ligne coûte du temps à tout le monde. "
        "Mais ne rends jamais un travail sans avoir joué **au moins** les tests.",
    ]


def _corps_conventions(constats: Constats) -> list[str]:
    """Les conventions **déjà écrites** du projet — à lire avant d'en inventer d'autres."""
    lisibles = [piece for piece in constats.conventions if piece.role != "instructions d'agent"]
    if not lisibles:
        return [
            "Le projet n'écrit aucune convention (ni README, ni CONTRIBUTING, ni "
            "fichier de style). Tiens-toi au style du code que tu modifies : c'est "
            "la seule convention constatable ici."
        ]
    return [
        "Ce projet écrit déjà ses conventions. Lis-les avant d'en inventer :",
        "",
        *[f"- `{piece.chemin}` — {piece.role or 'convention'}." for piece in lisibles],
        "",
        "Et, en toute circonstance, écris du code qui ressemble à celui qui "
        "l'entoure — nommage, densité de commentaires, idiomes.",
    ]


def _corps_outillage(constats: Constats, index: tuple[tuple[str, str], ...]) -> list[str]:
    """La **désignation** de docs/38 §3.3 : où sont les skills, où sont les scripts, et l'index.

    C'est la section qui fait tout tenir : elle est la seule voie par laquelle
    Claude Code — qui ne balaie pas `.agents/skills/` — atteint les skills du
    projet, et elle nomme le dossier de scripts **constaté**, jamais celui qu'on
    aurait imposé.
    """
    dossier = constats.dossier_scripts
    lignes = [DESIGNATION_SKILLS, ""]
    if index:
        lignes.append("Les skills de ce projet :")
        lignes.append("")
        lignes += [
            f"- **{nom}** — {description} → `{DOSSIER_SKILLS}/{nom}/SKILL.md`"
            for nom, description in index
        ]
        lignes.append("")
    else:
        lignes += ["Ce projet n'a pas encore de skill.", ""]
    if dossier.constate:
        lignes.append(
            f"Les scripts partagés du projet sont dans `{dossier.chemin}/` — appelle-les "
            f"**depuis la racine du projet** (`bash {dossier.chemin}/<script>`), jamais "
            "par un chemin relatif à ton répertoire courant, que rien ne garantit."
        )
    else:
        lignes.append(
            f"Le projet n'a pas de dossier de scripts. S'il en faut un, `{dossier.chemin}/` "
            "est l'endroit — et un script appelé par plusieurs skills y va, plutôt que "
            "d'être recopié dans chacun."
        )
    return lignes


def _ligne_commande(constats: Constats, usage: str, libelle: str) -> str:
    """Une ligne « **Libellé** : `commande` — lu dans `fichier` », ou "" sans constat.

    La provenance est dite et ne s'omet pas : une commande `declaree` est écrite
    par le projet, une commande de `convention` est celle de l'outil détecté, que
    le projet n'écrit nulle part. Elle peut donc être fausse sur un projet qui
    fait autrement, et la recopier sans le dire ferait passer une supposition pour
    une lecture.
    """
    commande = constats.commande_de(usage)
    if commande is None:
        return ""
    return f"- **{libelle}** : `{commande.commande}` — {_provenance(commande)}."


def _provenance(commande: Commande) -> str:
    """D'où sort une commande : le fichier qui la déclare, ou l'outil qui la conventionne."""
    if commande.origine == "convention":
        return f"convention de l'outil constaté dans `{commande.chemin}` (non déclarée)"
    extrait = f" ({commande.extrait})" if commande.extrait else ""
    return f"déclarée dans `{commande.chemin}`{extrait}"


def texte_skill(entree: Entree) -> str:
    """Le `SKILL.md` d'un skill recommandé — frontmatter minimal, puis quoi faire.

    Le frontmatter porte les deux champs **requis** par la spécification Agent
    Skills (`name`, ≤ 64 et égal au nom du dossier ; `description`, ≤ 1024) et
    rien de plus. Pas d'`allowed-tools` : le champ serait inerte partout où
    Maestro lit (docs/38 §5.1, §6), et l'écrire laisserait croire qu'un fichier
    du projet accorde des permissions.

    Le corps appelle les commandes **depuis la racine du projet** : c'est la seule
    forme qui ne dépende pas du répertoire courant de l'agent (docs/38 §3.4).
    """
    nom = entree.nom
    raison = _raison_stable(entree)
    description = DESCRIPTION_PAR_SKILL.get(nom, raison)
    lignes = [
        "---",
        f"name: {nom}",
        f"description: {description}",
        "---",
        "",
        f"# {_titre(nom)}",
        "",
        raison.strip().capitalize() + ("" if raison.rstrip().endswith(".") else "."),
        "",
        "## Comment faire",
        "",
        "Depuis la **racine du projet** :",
        "",
        "```bash",
        *(entree.commandes or ("# aucune commande constatée pour ce skill",)),
        "```",
    ]
    if entree.justification is not None:
        lignes += [
            "",
            f"Constaté dans `{entree.justification.chemin}`"
            + (f" ({entree.justification.role})" if entree.justification.role else "")
            + ". Si le projet a changé depuis, c'est ce fichier qui fait foi, pas celui-ci.",
        ]
    if _usages_couverts(nom):
        lignes += ["", _rappel_usages(nom)]
    return "\n".join(lignes).rstrip() + "\n"


def _usages_couverts(nom: str) -> tuple[str, ...]:
    """Les usages qu'un skill couvre en plus du sien — le cas de `verifier-le-style`."""
    usage = SKILL_PAR_NOM.get(nom, "")
    return USAGES_VERIFICATION if usage == "lint" else ()


def _rappel_usages(nom: str) -> str:
    """La phrase qui dit qu'un skill enveloppe plusieurs commandes de la même question."""
    usages = ", ".join(_usages_couverts(nom))
    return (
        f"Ce skill couvre {usages} : ce sont trois commandes de la même question — "
        "*est-ce que ça passe ?* — et les séparer ferait trois skills qui se "
        "disputent le même moment."
    )


def texte_script(entree: Entree) -> str:
    """Un script d'enchaînement pour une entrée `script` que la génération doit écrire.

    Aucune analyse n'en produit aujourd'hui : `maestro.outillage.recommandation`
    **reconnaît** les scripts du projet (`deja-present`) et n'en recommande
    jamais un neuf. Le gabarit existe pour le choix d'un projet neuf (#1031), qui
    pourra en demander un — et pour que « les scripts retenus » du critère
    d'acceptation ait une réponse quand il y en a, plutôt qu'un trou.

    `set -euo pipefail` et rien d'autre : un script généré doit s'arrêter à la
    première commande en échec, sans quoi son code de retour mentirait.
    """
    lignes = [
        "#!/usr/bin/env bash",
        f"# {entree.raison}",
        f"# Écrit par {GENERE_PAR} — déclaré dans {CHEMIN_MANIFESTE}.",
        "set -euo pipefail",
        "",
        *(entree.commandes or ('echo "aucune commande constatée" >&2; exit 1',)),
    ]
    return "\n".join(lignes).rstrip() + "\n"


def _titre(nom: str) -> str:
    """Le nom d'un skill en titre lisible : `lancer-les-tests` → `Lancer les tests`."""
    return nom.replace("-", " ").capitalize()


def bloc(contenu: str) -> str:
    """Le `contenu` entouré des balises que Maestro possède dans un fichier d'autrui.

    Les balises viennent de `maestro.outillage.contexte` — là où elles sont
    **lues** —, jamais réécrites ici : une seconde orthographe ferait que le bloc
    écrit ne serait jamais celui qu'on relit (docs/38 §4.2).
    """
    return f"{BALISE_DEBUT}\n{contenu.strip()}\n{BALISE_FIN}"
