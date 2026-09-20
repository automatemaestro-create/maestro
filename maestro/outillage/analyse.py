"""Analyser un projet existant — **en lecture seule**, et sans jamais l'exécuter (#1030).

Le premier geste du chantier « outillage universel » (#1020,
[docs/38](../../docs/38-decision-outillage-universel-du-projet.md)) : avant de
proposer quoi que ce soit à écrire dans le dossier de quelqu'un, on regarde ce
qu'il y a. Jusqu'ici Maestro ne lisait **jamais** la racine d'un projet — les
sources d'un run sont des références attachées, pas une analyse.

## Les trois promesses, et comment elles tiennent

1. **Lecture seule.** Aucun `open` en écriture, aucune création de dossier :
   le module ne fait que `scandir`, `stat` et lire des fichiers nommés.
2. **Aucune exécution.** Ni ici ni dans `maestro.outillage.detection` il n'y a
   d'import de `subprocess`. Le VCS lui-même est **lu** — `detecter_vcs`
   (#221) ouvre `HEAD` et `config` plutôt que d'appeler `git`, et c'est ce qui
   permet de s'appuyer dessus sans rouvrir la promesse.
3. **Des bornes explicites.** `Bornes` voyage **dans la réponse** (nombre de
   fichiers, profondeur, octets par fichier, dossiers ignorés), et `Parcours`
   dit ce qui a été vu et quelle borne a été atteinte. Une analyse tronquée qui
   se tairait se lirait comme un projet plus petit qu'il n'est.

## Ce que le périmètre du projet y fait

Les motifs `exclus` du projet déclaré (`Perimetre`, #221) s'appliquent **en
plus** des dossiers ignorés, avec le moteur de motifs existant
(`maestro.projets.perimetre.motifs_compiles`) — jamais un second. Ils retirent
d'office `.env` et `**/secrets/**` (docs/24 §2.5) : l'analyse ne peut donc pas
lire les deux gisements de secrets d'un dépôt, et ce n'est pas une précaution
prise ici mais une propriété du périmètre qu'on hérite.

## Ce qui n'est pas fait, et pourquoi

- **`.gitignore` n'est pas interprété.** C'est l'ignorance du projet, pas la
  nôtre, et elle ne serait pas lisible dans la réponse — or les bornes doivent
  l'être. Les dossiers coûteux qu'il couvre sont dans `IGNORES_DEFAUT`, qui, lui,
  voyage avec l'analyse.
- **Le contenu des fichiers de code n'est pas lu.** Le parcours compte des
  extensions et retient des chemins ; seuls les fichiers **nommés** (manifestes,
  CI, conventions) sont ouverts, et plafonnés. Lire un projet entier coûterait
  des minutes pour un gain nul : ce qu'on cherche est écrit dans une poignée de
  fichiers dont on connaît le nom.
- **Les manifestes d'un monorepo ne sont lus qu'une fois par gestionnaire.** Le
  plus proche de la racine gagne (`PROFONDEUR_MARQUEURS`), et son chemin voyage
  dans le constat. Lire quarante `package.json` rendrait quarante fois la même
  commande, et l'analyse ne saurait pas laquelle recommander.
"""

from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from maestro.outillage.detection import (
    CI_PAR_MARQUEUR,
    DOSSIERS_SCRIPTS,
    DOSSIERS_SKILLS,
    EXTENSIONS_SCRIPT,
    GESTIONNAIRE_PAR_MARQUEUR,
    IGNORES_DEFAUT,
    INSTALLATION_PYTHON,
    LANGAGE_PAR_EXTENSION,
    MARQUEURS_COMMANDE,
    NODE_COMMANDES,
    VERROU_NODE,
    VERROU_PYTHON,
    commandes_conventionnelles,
    commandes_make,
    commandes_node,
    commandes_pyproject,
    forge_depuis_distant,
    gestionnaire_python,
    piece_de_convention,
    usage_de_script,
)
from maestro.outillage.modele import (
    USAGES,
    Analyse,
    Bornes,
    Commande,
    Constats,
    DossierScripts,
    Forge,
    Gestionnaire,
    Langage,
    Parcours,
    Piece,
    nouvel_id,
)
from maestro.outillage.recommandation import recommander
from maestro.projets.modele import Perimetre, Vcs
from maestro.projets.perimetre import motifs_compiles
from maestro.projets.racine import RacineRefusee, detecter_vcs

#: Jusqu'où un manifeste de gestionnaire est cherché sous la racine. Deux
#: niveaux : de quoi attraper `apps/web/package.json` et `services/api/pyproject.toml`
#: d'un monorepo ordinaire, sans descendre dans les recoins où un manifeste est
#: presque toujours un exemple ou un gabarit.
PROFONDEUR_MARQUEURS = 2

#: Nombre de langages rendus. Au-delà, ce sont des queues de distribution — un
#: fichier `.lua` dans un projet Python — et elles noieraient les deux ou trois
#: langages qui décident de l'outillage.
LANGAGES_MAX = 8

#: Nombre de scripts existants rendus par dossier de scripts. Le dépôt de
#: Maestro lui-même en porte des dizaines ; l'analyse en montre assez pour que
#: la recommandation reconnaisse les usages, pas l'inventaire complet.
SCRIPTS_MAX = 40

#: Nom du dossier de tests, quand c'est lui qui justifie `pytest`.
DOSSIERS_TESTS: tuple[str, ...] = ("tests", "test")

_TRONCATURE_FICHIERS = "fichiers-max"
_TRONCATURE_PROFONDEUR = "profondeur-max"


def analyser(
    racine: Path | str,
    *,
    projet_id: str = "",
    perimetre: Perimetre | None = None,
    bornes: Bornes | None = None,
) -> Analyse:
    """Analyse le projet posé sur `racine` et rend ce qu'il porte et ce qu'on lui recommande.

    `perimetre` est celui du projet déclaré (`Projet.perimetre`) ; son défaut
    retire déjà `.git`, `node_modules`, `.env` et `**/secrets/**`. `bornes`
    permet de resserrer l'analyse (les tests s'en servent) ; son défaut est
    celui de `Bornes`, complété par `IGNORES_DEFAUT` quand aucun dossier ignoré
    n'est fourni — un appelant qui passe une liste vide la veut vide.

    Lève `RacineRefusee` **motivée** si la racine n'est pas un dossier lisible
    (`dossier-absent`, `pas-un-dossier`) : c'est le même type de refus que le
    reste du paquet `projets`, donc le même chemin de traduction en code HTTP.
    """
    base = Path(racine).expanduser()
    if not base.exists():
        raise RacineRefusee("dossier-absent", f"Dossier introuvable : {base}.", base)
    if not base.is_dir():
        raise RacineRefusee(
            "pas-un-dossier", f"Chemin invalide : {base} n'est pas un dossier.", base
        )

    bornes = _bornes(bornes)
    perimetre = perimetre if perimetre is not None else Perimetre()
    releve = _parcourir(base, perimetre, bornes)
    constats = _constater(base, bornes, releve)
    return Analyse(
        id=nouvel_id(),
        projet_id=projet_id,
        racine=base.as_posix(),
        faite_le=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        resume=resume(constats),
        bornes=bornes,
        parcours=releve.parcours(),
        constats=constats,
        recommandation=recommander(constats),
    )


def resume(constats: Constats) -> str:
    """La phrase que le manifeste garde (`source.resume`, docs/38 §4.1).

    Une ligne, faite de ce qui a été constaté et de rien d'autre : elle est
    relue des mois plus tard, à côté d'un outillage dont on se demande d'où il
    sort. Un projet où rien n'a été trouvé le **dit** — c'est un constat, et le
    taire ferait lire un résumé vide comme une analyse qui n'a pas tourné.
    """
    morceaux: list[str] = []
    if constats.langages:
        morceaux.append(", ".join(langage.nom for langage in constats.langages[:3]))
    if constats.gestionnaires:
        morceaux.append(", ".join(g.nom for g in constats.gestionnaires))
    for usage, etiquette in (("tester", "tests"), ("lint", "lint")):
        commande = constats.commande_de(usage)
        if commande is not None:
            morceaux.append(f"{etiquette} : {commande.commande}")
    if constats.ci:
        morceaux.append(", ".join(piece.nom for piece in constats.ci))
    if constats.forge is not None:
        morceaux.append(f"forge {constats.forge.nom}")
    if not morceaux:
        return "aucun langage, gestionnaire ni outil constaté dans les bornes de l'analyse"
    return " ; ".join(morceaux)


class _Releve:
    """Ce que le parcours accumule — mutable, et confiné à ce module.

    Une classe plutôt qu'un empilement de variables locales : le parcours et la
    mise en constats sont deux fonctions, et se passer huit accumulateurs entre
    elles serait huit occasions d'en oublier un (le même argument que
    `maestro.controltower.bornes`).
    """

    def __init__(self) -> None:
        self.extensions: Counter[str] = Counter()
        self.exemples: dict[str, str] = {}
        self.fichiers = 0
        self.dossiers = 0
        self.profondeur = 0
        self.troncatures: list[str] = []
        self.ignores: set[str] = set()
        #: `nom de marqueur → (profondeur, caché, chemin relatif)` — le meilleur
        #: candidat retenu, au sens de `_rang_marqueur`.
        self.marqueurs: dict[str, tuple[int, int, str]] = {}

    def noter_troncature(self, motif: str) -> None:
        """Enregistre une borne atteinte, une seule fois."""
        if motif not in self.troncatures:
            self.troncatures.append(motif)

    def noter_marqueur(self, nom: str, relatif: str, profondeur: int) -> None:
        """Retient le marqueur `nom` s'il est meilleur que celui déjà vu."""
        rang = _rang_marqueur(relatif, profondeur)
        connu = self.marqueurs.get(nom)
        if connu is None or rang < connu:
            self.marqueurs[nom] = rang

    def parcours(self) -> Parcours:
        """Le parcours tel qu'il sera servi."""
        return Parcours(
            fichiers_vus=self.fichiers,
            dossiers_vus=self.dossiers,
            profondeur_atteinte=self.profondeur,
            troncatures=tuple(self.troncatures),
            ignores_rencontres=tuple(sorted(self.ignores)),
        )


def _rang_marqueur(relatif: str, profondeur: int) -> tuple[int, int, str]:
    """Le rang d'un manifeste candidat : proche de la racine, puis **hors dossier caché**.

    Le second critère est ce qui manquait à la première version, et la mesure
    l'a dit tout de suite : sur ce dépôt, `.tools/mcp/package.json` battait
    `apps/web/package.json` au seul tri alphabétique. Un dossier qui commence
    par un point porte de la configuration ou de l'outillage provisionné, pas
    le projet — et l'analyse en aurait tiré les commandes de quelqu'un d'autre.
    """
    cache = 1 if any(segment.startswith(".") for segment in relatif.split("/")[:-1]) else 0
    return (profondeur, cache, relatif)


def _bornes(bornes: Bornes | None) -> Bornes:
    """Les bornes effectives : celles fournies, ou le défaut avec `IGNORES_DEFAUT`.

    Un appelant qui passe des bornes **avec** une liste d'ignorés vide la veut
    vide (c'est ce qu'un test fait pour voir un `node_modules`) ; c'est
    seulement l'absence de bornes qui retombe sur la liste du dépôt.
    """
    if bornes is not None:
        return bornes
    return Bornes(ignores=IGNORES_DEFAUT)


def _parcourir(base: Path, perimetre: Perimetre, bornes: Bornes) -> _Releve:
    """Le parcours borné de la racine — itératif, déterministe, sans lire un contenu.

    Itératif parce que la pile de Python n'est pas le bon endroit où compter sur
    la profondeur d'un projet réel (le même parti pris que
    `maestro.projets.perimetre.exclusions`), déterministe parce que deux
    analyses du même projet doivent rendre le même relevé : entrées triées,
    borne atteinte notée, aucun lien symbolique suivi.
    """
    releve = _Releve()
    motifs = motifs_compiles(perimetre.exclus)
    ignores = frozenset(bornes.ignores)
    pile: list[tuple[str, int]] = [("", 0)]
    while pile:
        relatif_dossier, profondeur = pile.pop()
        courant = base / relatif_dossier if relatif_dossier else base
        try:
            with os.scandir(courant) as entrees:
                triees = sorted(entrees, key=lambda entree: entree.name)
        except OSError:
            # Dossier illisible ou disparu : sauté, jamais fatal. Analyser le
            # projet de quelqu'un, c'est accepter qu'une partie soit fermée.
            continue
        for entree in triees:
            relatif = f"{relatif_dossier}/{entree.name}" if relatif_dossier else entree.name
            if entree.is_symlink():
                # Jamais suivi, à aucune profondeur : c'est le vecteur d'évasion
                # de docs/24 §2.5, et un lien vers `~/.ssh` n'a pas à devenir un
                # chemin que l'analyse ouvre.
                continue
            if any(motif.match(relatif) for motif in motifs):
                continue
            if _est_dossier(entree):
                if entree.name in ignores:
                    releve.ignores.add(entree.name)
                    continue
                releve.dossiers += 1
                releve.profondeur = max(releve.profondeur, profondeur + 1)
                if profondeur + 1 >= bornes.profondeur_max:
                    releve.noter_troncature(_TRONCATURE_PROFONDEUR)
                    continue
                pile.append((relatif, profondeur + 1))
                continue
            if releve.fichiers >= bornes.fichiers_max:
                releve.noter_troncature(_TRONCATURE_FICHIERS)
                return releve
            releve.fichiers += 1
            releve.profondeur = max(releve.profondeur, profondeur + 1)
            _noter_fichier(releve, entree.name, relatif, profondeur)
    return releve


def _noter_fichier(releve: _Releve, nom: str, relatif: str, profondeur: int) -> None:
    """Range un fichier vu : son langage, et le marqueur qu'il est peut-être."""
    langage = LANGAGE_PAR_EXTENSION.get(Path(nom).suffix.lower())
    if langage is not None:
        releve.extensions[langage] += 1
        releve.exemples.setdefault(langage, relatif)
    if nom in GESTIONNAIRE_PAR_MARQUEUR and profondeur <= PROFONDEUR_MARQUEURS:
        releve.noter_marqueur(nom, relatif, profondeur)


def _est_dossier(entree: os.DirEntry[str]) -> bool:
    """`entree` est-elle un dossier ? Faux si l'OS refuse de le dire (lien mort, droits)."""
    try:
        return entree.is_dir()
    except OSError:
        return False


def _constater(base: Path, bornes: Bornes, releve: _Releve) -> Constats:
    """Met le relevé en constats : langages, gestionnaires, commandes, CI, forge, conventions."""
    vcs = detecter_vcs(base)
    gestionnaires, commandes = _gestionnaires_et_commandes(base, bornes, releve)
    commandes += _commandes_de_marqueurs(base)
    commandes += _commandes_par_defaut(base, releve, commandes)
    ci = _ci(base)
    return Constats(
        langages=_langages(releve),
        gestionnaires=gestionnaires,
        commandes=_ordonner(commandes),
        ci=ci,
        forge=_forge(vcs, ci),
        vcs=vcs,
        conventions=_conventions(base),
        dossier_scripts=_dossier_scripts(base),
        outillage_present=_outillage_present(base),
    )


def _langages(releve: _Releve) -> tuple[Langage, ...]:
    """Les langages constatés, du plus présent au moins présent.

    La part est calculée sur les fichiers **de code** vus, pas sur tous les
    fichiers : un dépôt de documentation avec trois scripts ne doit pas rendre
    « Shell : 2 % », qui se lirait comme « ce projet est à 98 % autre chose ».
    """
    total = sum(releve.extensions.values())
    if not total:
        return ()
    classes = sorted(releve.extensions.items(), key=lambda paire: (-paire[1], paire[0]))
    return tuple(
        Langage(
            nom=nom,
            fichiers=compte,
            part=round(compte / total, 3),
            exemple=releve.exemples.get(nom, ""),
        )
        for nom, compte in classes[:LANGAGES_MAX]
    )


def _gestionnaires_et_commandes(
    base: Path,
    bornes: Bornes,
    releve: _Releve,
) -> tuple[tuple[Gestionnaire, ...], tuple[Commande, ...]]:
    """Les gestionnaires constatés et les commandes qu'ils **déclarent**.

    Un seul manifeste par gestionnaire est lu — le plus proche de la racine —,
    et les verrous sont cherchés **à côté de lui** : c'est le verrou qui décide
    entre `npm ci` et `npm install`, et le chercher ailleurs qu'auprès de son
    manifeste donnerait la réponse d'un autre paquet du monorepo.
    """
    gestionnaires: list[Gestionnaire] = []
    commandes: list[Commande] = []
    marqueurs = sorted((rang, nom) for nom, rang in releve.marqueurs.items())
    vus: set[str] = set()
    for (_, _, relatif), nom in marqueurs:
        famille = GESTIONNAIRE_PAR_MARQUEUR[nom]
        chemin = base / relatif
        dossier = chemin.parent
        if famille == "npm":
            reel, _ = _gestionnaire_node(dossier)
        elif famille in ("pip", "pipenv"):
            reel = _gestionnaire_python(dossier, chemin, relatif, nom, bornes, famille)
        else:
            reel = famille
        if reel in vus:
            continue
        vus.add(reel)
        if famille == "npm":
            gestionnaire, trouvees = _node(base, chemin, relatif, reel, bornes)
        elif famille in ("pip", "pipenv"):
            gestionnaire, trouvees = _python(chemin, relatif, nom, reel, bornes)
        else:
            gestionnaire, trouvees = _autre(chemin, relatif, reel, bornes)
        gestionnaires.append(gestionnaire)
        commandes.extend(trouvees)
    return tuple(gestionnaires), tuple(commandes)


def _node(
    base: Path,
    manifeste: Path,
    relatif: str,
    gestionnaire: str,
    bornes: Bornes,
) -> tuple[Gestionnaire, tuple[Commande, ...]]:
    """Le gestionnaire Node retenu, son installation et les scripts qu'il déclare."""
    dossier = manifeste.parent
    _, verrou = _gestionnaire_node(dossier)
    installer = _installation_node(gestionnaire, verrou)
    installation = Commande(
        usage="installer",
        commande=installer,
        chemin=_relatif(dossier, base, verrou) if verrou else relatif,
        extrait=f"verrou {verrou}" if verrou else "manifeste sans verrou",
        origine="convention",
    )
    declarees = commandes_node(manifeste, relatif, gestionnaire, bornes.octets_par_fichier_max)
    return (
        Gestionnaire(nom=gestionnaire, chemin=relatif, verrou=verrou, installer=installer),
        (installation, *declarees),
    )


def _python(
    manifeste: Path,
    relatif: str,
    nom: str,
    gestionnaire: str,
    bornes: Bornes,
) -> tuple[Gestionnaire, tuple[Commande, ...]]:
    """Le gestionnaire Python retenu et les outils que son manifeste configure.

    L'installation dépend du **couple** gestionnaire/marqueur : un
    `pip install -r requirements.txt` sur un projet qui n'a qu'un
    `pyproject.toml` échouerait, et un outillage généré qui ne marche pas est
    pire que pas d'outillage du tout.
    """
    verrou = _premier_present(manifeste.parent, tuple(VERROU_PYTHON))
    installer = INSTALLATION_PYTHON.get((gestionnaire, nom))
    commandes: list[Commande] = []
    if installer is not None:
        commandes.append(
            Commande(
                usage="installer",
                commande=installer,
                chemin=relatif,
                extrait=f"convention {gestionnaire}",
                origine="convention",
            )
        )
    if nom == "pyproject.toml":
        commandes.extend(commandes_pyproject(manifeste, relatif, bornes.octets_par_fichier_max))
    return (
        Gestionnaire(nom=gestionnaire, chemin=relatif, verrou=verrou, installer=installer),
        tuple(commandes),
    )


def _autre(
    manifeste: Path,
    relatif: str,
    gestionnaire: str,
    bornes: Bornes,
) -> tuple[Gestionnaire, tuple[Commande, ...]]:
    """Les autres gestionnaires : les cibles d'un `Makefile`, ou les conventions de l'outil."""
    if gestionnaire == "make":
        commandes = commandes_make(manifeste, relatif, bornes.octets_par_fichier_max)
    else:
        commandes = commandes_conventionnelles(gestionnaire, relatif)
    return Gestionnaire(nom=gestionnaire, chemin=relatif), commandes


def _gestionnaire_node(dossier: Path) -> tuple[str, str | None]:
    """Le gestionnaire Node réel et son verrou — `npm` par défaut, sans verrou."""
    for verrou, nom in sorted(VERROU_NODE.items()):
        if (dossier / verrou).is_file():
            return nom, verrou
    return "npm", None


def _installation_node(gestionnaire: str, verrou: str | None) -> str:
    """La commande d'installation Node juste : reproductible **si** un verrou est là.

    Sans verrou, `npm ci` échouerait : proposer la commande reproductible
    d'office donnerait un skill qui ne marche pas, et c'est exactement ce qu'un
    outillage généré ne doit pas être.
    """
    nu, _ = NODE_COMMANDES.get(gestionnaire, NODE_COMMANDES["npm"])
    if verrou is None:
        return nu
    if gestionnaire == "npm":
        return "npm ci"
    return f"{nu} --frozen-lockfile"


def _gestionnaire_python(
    dossier: Path,
    manifeste: Path,
    relatif: str,
    nom: str,
    bornes: Bornes,
    famille: str,
) -> str:
    """Le gestionnaire Python réel : le verrou d'abord, la déclaration ensuite, `famille` sinon.

    `Pipfile` porte son gestionnaire dans son nom (`pipenv`) ; les deux autres
    marqueurs ne disent pas qui installe, et c'est le verrou posé à côté —
    `uv.lock`, `poetry.lock` — qui tranche.
    """
    if famille == "pipenv":
        return "pipenv"
    for verrou, gestionnaire in sorted(VERROU_PYTHON.items()):
        if (dossier / verrou).is_file():
            return gestionnaire
    if nom == "pyproject.toml":
        declare = gestionnaire_python(manifeste, relatif, bornes.octets_par_fichier_max)
        if declare is not None:
            return declare
    return "pip"


def _commandes_de_marqueurs(base: Path) -> tuple[Commande, ...]:
    """Les commandes que déclarent les fichiers de configuration de la racine.

    Leur seule **présence** suffit : poser un `.pre-commit-config.yaml`, c'est
    dire comment le projet se vérifie. Rien n'est ouvert ici — ce qui borne
    d'autant la lecture.
    """
    return tuple(
        Commande(
            usage=usage,
            commande=commande,
            chemin=nom,
            extrait=f"présence de {nom}",
            origine="declaree",
        )
        for nom, (usage, commande) in sorted(MARQUEURS_COMMANDE.items())
        if (base / nom).is_file()
    )


def _commandes_par_defaut(
    base: Path,
    releve: _Releve,
    deja: tuple[Commande, ...],
) -> tuple[Commande, ...]:
    """Le filet : `pytest` sur un projet Python qui a un dossier de tests et rien d'autre.

    Une convention, donc annoncée comme telle. Elle ne se déclenche que si
    **aucune** commande de test n'a été constatée : un projet qui déclare la
    sienne n'a pas besoin qu'on en devine une seconde.
    """
    if any(commande.usage == "tester" for commande in deja):
        return ()
    if "Python" not in releve.extensions:
        return ()
    dossier = next((nom for nom in DOSSIERS_TESTS if (base / nom).is_dir()), None)
    if dossier is None:
        return ()
    return (
        Commande(
            usage="tester",
            commande="pytest",
            chemin=dossier,
            extrait="dossier de tests d'un projet Python",
            origine="convention",
        ),
    )


def _ordonner(commandes: tuple[Commande, ...]) -> tuple[Commande, ...]:
    """Les commandes rangées par usage, **déclarées avant conventions**.

    C'est cet ordre qui fait que `Constats.commande_de` rend la commande du
    projet plutôt que celle de l'outil : la règle vit ici, une fois, plutôt que
    dans chaque appelant qui aurait à retrier.
    """
    rang = {usage: index for index, usage in enumerate(USAGES)}
    return tuple(
        sorted(
            commandes,
            key=lambda c: (rang.get(c.usage, len(rang)), c.origine != "declaree"),
        )
    )


def _ci(base: Path) -> tuple[Piece, ...]:
    """Les intégrations continues constatées à la racine."""
    return tuple(
        Piece(nom=nom, chemin=marqueur, role="intégration continue")
        for marqueur, nom in sorted(CI_PAR_MARQUEUR.items())
        if (base / marqueur).exists()
    )


def _forge(vcs: Vcs | None, ci: tuple[Piece, ...]) -> Forge | None:
    """La forge du projet : son distant Git d'abord, sa CI à défaut.

    Le distant est la preuve ; la CI n'est qu'un indice — `.github/workflows`
    sur un dépôt sans remote dit « GitHub » avec raison, mais on ne peut pas en
    rendre l'URL. Les deux sortent avec le chemin qui les a fait dire.
    """
    if vcs is not None and vcs.distant:
        nom = forge_depuis_distant(vcs.distant)
        if nom is not None:
            return Forge(nom=nom, distant=vcs.distant, chemin=".git/config")
    for piece in ci:
        nom = _FORGE_PAR_CI.get(piece.nom)
        if nom is not None:
            return Forge(nom=nom, distant=None, chemin=piece.chemin)
    return None


#: La forge que trahit une CI, quand le dépôt n'a pas de distant. Les autres CI
#: ne disent rien de la forge (Jenkins, CircleCI et Drone servent tout le monde).
_FORGE_PAR_CI: dict[str, str] = {
    "GitHub Actions": "GitHub",
    "GitLab CI": "GitLab",
    "Bitbucket Pipelines": "Bitbucket",
}


def _conventions(base: Path) -> tuple[Piece, ...]:
    """Les conventions **déjà écrites** à la racine, plus le dossier de documentation.

    À la racine seulement : c'est là que les fichiers de convention se posent,
    et les chercher en profondeur rendrait le `README.md` d'un exemple au même
    rang que celui du projet.
    """
    pieces: list[Piece] = []
    try:
        with os.scandir(base) as entrees:
            noms = sorted(entree.name for entree in entrees if not entree.is_dir())
    except OSError:
        noms = []
    for nom in noms:
        piece = piece_de_convention(nom, nom)
        if piece is not None:
            pieces.append(piece)
    copilote = ".github/copilot-instructions.md"
    if (base / copilote).is_file():
        pieces.append(
            Piece(nom="copilot-instructions.md", chemin=copilote, role="instructions d'agent")
        )
    if (base / "docs").is_dir():
        pieces.append(Piece(nom="docs", chemin="docs", role="documentation"))
    return tuple(pieces)


def _dossier_scripts(base: Path) -> DossierScripts:
    """Le dossier de scripts du projet — **constaté**, jamais imposé (docs/38 §3.4).

    Le premier de `DOSSIERS_SCRIPTS` qui existe gagne ; aucun ne répond, et le
    défaut (`scripts`) est rendu avec `constate=False` — c'est ce que
    `AGENTS.md` nommerait, en disant qu'il reste à créer.
    """
    for nom in DOSSIERS_SCRIPTS:
        dossier = base / nom
        if not dossier.is_dir():
            continue
        return DossierScripts(chemin=nom, constate=True, scripts=_scripts_de(dossier, nom))
    return DossierScripts(chemin=DOSSIERS_SCRIPTS[0], constate=False)


def _scripts_de(dossier: Path, prefixe: str) -> tuple[Piece, ...]:
    """Les scripts d'un dossier, à **un seul niveau**, avec l'usage que leur nom suggère.

    Un niveau, parce qu'un script rangé dans un sous-dossier est presque
    toujours une pièce appelée par un autre et non un point d'entrée. Le `role`
    est l'usage deviné d'après le **nom** : on ne lit pas le contenu d'un
    script, ce serait interpréter du code du projet pour un gain nul.
    """
    try:
        with os.scandir(dossier) as entrees:
            noms = sorted(
                entree.name
                for entree in entrees
                if not entree.is_dir()
                and not entree.is_symlink()
                and Path(entree.name).suffix.lower() in EXTENSIONS_SCRIPT
            )
    except OSError:
        return ()
    return tuple(
        Piece(nom=nom, chemin=f"{prefixe}/{nom}", role=usage_de_script(nom))
        for nom in noms[:SCRIPTS_MAX]
    )


def _outillage_present(base: Path) -> tuple[Piece, ...]:
    """Ce que le projet porte **déjà** de l'outillage de docs/38 §3.6.

    C'est la liste qui permet de reconnaître au lieu de dupliquer : un
    `AGENTS.md` écrit avant Maestro, un `SKILL.md` posé dans n'importe lequel
    des quatre dossiers que les clients lisent, un manifeste d'une génération
    précédente. Tout y vient avec son chemin — c'est ce qu'il faudra ouvrir pour
    décider quoi en faire.
    """
    pieces: list[Piece] = []
    for nom, role in (
        ("AGENTS.md", "instructions"),
        ("CLAUDE.md", "pont"),
        ("GEMINI.md", "pont"),
    ):
        if (base / nom).is_file():
            pieces.append(Piece(nom=nom, chemin=nom, role=role))
    for dossier in DOSSIERS_SKILLS:
        pieces.extend(_skills_de(base, dossier))
    manifeste = ".maestro/outillage/manifeste.json"
    if (base / manifeste).is_file():
        pieces.append(Piece(nom="manifeste.json", chemin=manifeste, role="manifeste"))
    return tuple(pieces)


def _skills_de(base: Path, dossier: str) -> tuple[Piece, ...]:
    """Les skills d'un dossier de skills : un sous-dossier qui porte un `SKILL.md`.

    C'est la définition de la spécification Agent Skills, et rien de plus : un
    dossier sans `SKILL.md` n'est pas un skill, et le compter en ferait
    disparaître un vrai derrière un dossier de ressources.
    """
    racine = base / dossier
    if not racine.is_dir():
        return ()
    try:
        with os.scandir(racine) as entrees:
            noms = sorted(entree.name for entree in entrees if _est_dossier(entree))
    except OSError:
        return ()
    return tuple(
        Piece(nom=nom, chemin=f"{dossier}/{nom}/SKILL.md", role="skill")
        for nom in noms
        if (racine / nom / "SKILL.md").is_file()
    )


def _premier_present(dossier: Path, noms: tuple[str, ...]) -> str | None:
    """Le premier de `noms` présent dans `dossier`, `None` sinon."""
    return next((nom for nom in sorted(noms) if (dossier / nom).is_file()), None)


def _relatif(dossier: Path, base: Path, nom: str) -> str:
    """Le chemin POSIX de `dossier/nom`, relatif à `base` — jamais un chemin du poste."""
    try:
        return (dossier / nom).relative_to(base).as_posix()
    except ValueError:  # pragma: no cover - `dossier` vient toujours de `base`
        return nom
