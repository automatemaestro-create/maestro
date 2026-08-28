"""L'application du travail d'une tâche **dans** le projet de l'utilisateur (#227, EF-37).

Le dernier mètre de la Phase 7 (parent #219). Les lots précédents font travailler
un agent dans un espace **dérivé** du projet — worktree Git sur une branche
`maestro/<tâche>` si le projet est versionné, copie de son périmètre sinon
(#224, `maestro.sandbox.projet`) — mais rien ne ramenait ce travail dans le
projet : on avait remplacé un dossier de sortie à recopier à la main par un
worktree à fusionner à la main.

Ce module calcule **ce qui changerait** et l'écrit **si on l'y autorise**. Il ne
décide de rien : l'accord humain est le sujet de
`maestro.controltower.validation.appliquer_sous_validation`, qui branche cette
action sur le mécanisme de validation existant (EF-08) au lieu d'en inventer un.

Deux chemins, ceux de la décision **D2**
([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md)) :

- **projet versionné** → le diff est celui de la branche de tâche par rapport à
  la **branche de travail déclarée** (`Projet.vcs.branche_base`), et l'appliquer
  c'est **fusionner** cette branche. Le retour arrière reste natif : c'est un
  commit de fusion, `git revert` le défait ;
- **projet non versionné** → le diff est la comparaison de la **copie** avec la
  racine, et l'appliquer c'est y recopier les fichiers. Sans historique pour
  revenir en arrière, c'est le diff montré à l'humain qui fait office de filet.

Quatre partis pris à connaître avant d'y toucher :

1. **le nom de la branche de tâche n'est jamais recalculé ici** — il est *passé*
   par l'appelant, qui le tient de `maestro.sandbox.projet.branche_de_tache`
   (#224). Deux orthographes d'une même convention finiraient par diverger, et
   celle qui se tromperait fusionnerait la branche d'une autre tâche ;
2. **tout le diff est vérifié avant la première écriture** — aucun chemin ne
   sort de la racine (EF-38, `chemin_dans_racine`), et un refus laisse le projet
   exactement dans l'état où il était : « refusée avec son motif, pas appliquée
   partiellement » est le critère, et il ne se tient pas si on valide en
   écrivant ;
3. **le point d'appel appartient à qui tient l'espace de travail.** Pour un
   projet versionné, tout se lit dans la branche : l'application peut être
   demandée bien après la tâche. Pour un projet **non** versionné, la copie est
   le diff — refermée par `runtime.executer` (#224), elle emporte le travail avec
   elle, et l'application doit donc être demandée tant qu'elle est ouverte. C'est
   pour cela que ce module prend l'espace en argument au lieu d'aller le
   chercher : il ne décide pas de sa durée de vie ;
4. **aucune suppression n'est appliquée à un projet non versionné.** Un fichier
   absent de la copie est indistinguable d'un fichier que le périmètre n'y a
   jamais mis (`EXCLUS_DEFAUT` retire `.env`, `node_modules`, `**/secrets/**`) ;
   effacer sur cette ambiguïté, dans un projet sans historique, est exactement
   ce que ENF-13 existe pour empêcher. Sur un projet versionné, où Git sait de
   quoi il parle et où le retour arrière est natif, les suppressions font partie
   de la fusion comme le reste.
"""

from __future__ import annotations

import difflib
import os
import subprocess
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maestro.projets.modele import Projet
from maestro.projets.racine import RacineRefusee, chemin_dans_racine, valider_racine

#: Les trois natures d'une modification, telles que l'UI les affiche.
NATURE_AJOUT = "ajoute"
NATURE_MODIFICATION = "modifie"
NATURE_SUPPRESSION = "supprime"

#: Délai maximum d'une commande Git — même borne d'anomalie que l'espace de
#: travail dérivé (#224) : monter, diffuser ou fusionner sont des opérations
#: locales de quelques dizaines de millisecondes, une minute dit « quelque chose
#: ne va pas » (disque réseau, dépôt colossal) plutôt qu'« attends encore ».
_DELAI_GIT_S = 60

#: Taille au-delà de laquelle un fichier n'est plus compté ligne à ligne : on
#: sait qu'il change, pas de combien. Même seuil que la capture des livrables
#: (`maestro.sandbox.workspace`) — au-delà, c'est un artefact, pas du code.
_MAX_COMPTAGE_OCTETS = 1_000_000

#: Nombre de fichiers listés nommément dans le résumé textuel d'un diff. Le diff
#: complet reste dans `DiffProjet.modifications` (l'UI l'affiche en entier) ;
#: c'est la phrase du journal et de la notification qu'on borne.
_MAX_LIGNES_RESUME = 40

#: Identité portée par les DEUX commits que Maestro écrit dans le projet : le commit de rattrapage
#: (`commiter_en_attente`) et le commit de fusion (`_fusionner`, `--no-ff`). Le dépôt de
#: l'utilisateur n'a aucune raison d'avoir une identité Git valable pour un agent, et un
#: `user.email` absent ferait échouer la fusion au tout dernier geste. Posée par `-c`, donc jamais
#: écrite dans sa configuration.
_AUTEUR_NOM = "Maestro"
_AUTEUR_COURRIEL = "maestro@localhost"


class ApplicationRefusee(RuntimeError):
    """L'application n'a pas eu lieu — **avec son motif** (EF-37, EF-38).

    Même parti pris que `RacineRefusee` (#221) et `EspaceProjetIndisponible`
    (#224) : le refus porte un code court et stable que l'API et l'UI peuvent
    afficher ou traduire (`hors-racine`, `branche-introuvable`, `base-introuvable`,
    `racine-occupee`, `fusion-refusee`, `git-indisponible`, `espace-introuvable`),
    le message restant la phrase lisible.

    Un refus est **total** : quand il est levé, rien n'a été écrit dans le projet.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class Modification:
    """Un fichier du diff : ce qui lui arrive, et de combien de lignes.

    `chemin` est **relatif à la racine du projet**, en POSIX — la même forme que
    les motifs du périmètre et que les livrables (`ProducedFile.chemin`), donc
    comparable sans conversion sur les trois OS. `ajouts`/`suppressions` valent 0
    pour un fichier binaire ou trop volumineux, que `binaire` distingue d'un
    fichier réellement inchangé.
    """

    chemin: str
    nature: str = NATURE_MODIFICATION
    ajouts: int = 0
    suppressions: int = 0
    binaire: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Réémet la modification en dict JSON-sérialisable (la forme du REST)."""
        return {
            "chemin": self.chemin,
            "nature": self.nature,
            "ajouts": self.ajouts,
            "suppressions": self.suppressions,
            "binaire": self.binaire,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Modification:
        """Reconstruit la modification depuis sa forme `to_dict`."""
        return cls(
            chemin=str(data.get("chemin", "")),
            nature=str(data.get("nature", NATURE_MODIFICATION)),
            ajouts=int(data.get("ajouts") or 0),
            suppressions=int(data.get("suppressions") or 0),
            binaire=bool(data.get("binaire")),
        )


@dataclass(frozen=True)
class DiffProjet:
    """Ce que l'application changerait dans le projet — la pièce jointe de la demande.

    C'est **l'objet que l'humain approuve** : la fusion (ou la recopie) qui suit
    n'applique que ce qui est décrit ici. `branche` et `base` ne sont renseignées
    que pour un projet versionné — `branche` est la branche de tâche à fusionner
    (posée par #224, jamais recalculée ici), `base` la branche de travail
    déclarée du projet vers laquelle elle part.
    """

    modifications: tuple[Modification, ...] = ()
    branche: str = ""
    base: str = ""

    @property
    def versionne(self) -> bool:
        """Le diff porte-t-il sur une branche (projet versionné) ?"""
        return bool(self.branche)

    @property
    def vide(self) -> bool:
        """Rien à appliquer — la tâche n'a rien changé dans le projet."""
        return not self.modifications

    @property
    def fichiers(self) -> int:
        """Nombre de fichiers touchés."""
        return len(self.modifications)

    @property
    def ajouts(self) -> int:
        """Total des lignes ajoutées."""
        return sum(m.ajouts for m in self.modifications)

    @property
    def suppressions(self) -> int:
        """Total des lignes supprimées."""
        return sum(m.suppressions for m in self.modifications)

    def resume(self) -> str:
        """Le diff en texte, tel que le journal et la notification le portent.

        Une première ligne de totaux, puis un fichier par ligne — bornée à
        `_MAX_LIGNES_RESUME`, le reste étant compté. L'UI, elle, affiche
        `modifications` en entier : ce résumé sert là où il n'y a pas d'écran.
        """
        if self.vide:
            return "Aucune modification à appliquer."
        entete = (
            f"{self.fichiers} fichier(s), +{self.ajouts} / −{self.suppressions}"
        )
        if self.versionne:
            entete += f" — fusion de {self.branche} vers {self.base}"
        lignes = [entete]
        for modification in self.modifications[:_MAX_LIGNES_RESUME]:
            compte = "binaire" if modification.binaire else (
                f"+{modification.ajouts} −{modification.suppressions}"
            )
            lignes.append(f"  {_SIGNE[modification.nature]} {modification.chemin} ({compte})")
        reste = self.fichiers - _MAX_LIGNES_RESUME
        if reste > 0:
            lignes.append(f"  … et {reste} autre(s) fichier(s)")
        return "\n".join(lignes)

    def to_dict(self) -> dict[str, Any]:
        """Réémet le diff en dict JSON-sérialisable (la forme du WebSocket et du REST)."""
        return {
            "modifications": [m.to_dict() for m in self.modifications],
            "branche": self.branche,
            "base": self.base,
            "fichiers": self.fichiers,
            "ajouts": self.ajouts,
            "suppressions": self.suppressions,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> DiffProjet:
        """Reconstruit le diff depuis sa forme `to_dict` (aller-retour JSON).

        Les totaux (`fichiers`, `ajouts`, `suppressions`) ne sont pas relus : ils
        sont **dérivés** des modifications, et les relire laisserait passer un
        événement dont l'en-tête contredit la liste.
        """
        brutes = data.get("modifications") or ()
        return cls(
            modifications=tuple(
                Modification.from_dict(m) for m in brutes if isinstance(m, Mapping)
            ),
            branche=str(data.get("branche", "") or ""),
            base=str(data.get("base", "") or ""),
        )


#: Le signe d'une nature dans le résumé textuel — l'ordre de lecture d'un diff.
_SIGNE = {
    NATURE_AJOUT: "+",
    NATURE_MODIFICATION: "~",
    NATURE_SUPPRESSION: "−",
}


# --------------------------------------------------------------------------- #
# Calcul du diff
# --------------------------------------------------------------------------- #


def diff_du_travail(
    projet: Projet,
    *,
    branche: str = "",
    espace: Path | str | None = None,
) -> DiffProjet:
    """Ce que le travail de la tâche changerait dans `projet`, sans rien écrire.

    `branche` est la branche de tâche posée par l'espace de travail dérivé
    (#224, `branche_de_tache`) : **obligatoire** pour un projet versionné, où
    c'est elle qui porte le travail une fois le worktree retiré. `espace` est le
    répertoire de travail de la tâche : obligatoire pour un projet **non**
    versionné (c'est la copie qu'on compare à la racine), facultatif pour un
    projet versionné — s'il est encore monté, le travail **non commité** qu'il
    contient entre dans le diff, sinon seul ce qui est commité sur la branche
    compte.

    Lève `RacineRefusee` si la racine n'est plus admissible (EF-38 — elle est
    revalidée ici, le dépôt des projets étant un dossier de fichiers JSON qu'on
    peut éditer à la main) et `ApplicationRefusee` motivée si le diff n'est pas
    calculable.
    """
    racine = valider_racine(projet.racine)
    if not projet.versionne:
        return DiffProjet(modifications=_diff_copie(racine, _exige_espace(espace)))
    if not branche:
        raise ApplicationRefusee(
            "branche-inconnue",
            f"Projet versionné {projet.nom!r} : indiquez la branche de tâche à "
            "fusionner (celle posée par l'espace de travail dérivé).",
        )
    base = _base(projet)
    _exige_ref(racine, branche, "branche-introuvable")
    _exige_ref(racine, base, "base-introuvable")
    chemin = Path(espace) if espace is not None else None
    modifications = (
        _diff_worktree(chemin, base)
        if chemin is not None and chemin.is_dir()
        else _diff_branche(racine, base, branche)
    )
    return DiffProjet(modifications=modifications, branche=branche, base=base)


def _base(projet: Projet) -> str:
    """La branche de travail déclarée du projet, ou un refus motivé.

    Un dépôt déclaré en HEAD détaché n'a pas de branche de travail : il n'y a
    alors **rien vers quoi** fusionner, et le dire vaut mieux que de choisir une
    branche à la place de l'utilisateur.
    """
    base = projet.vcs.branche_base if projet.vcs is not None else ""
    if not base:
        raise ApplicationRefusee(
            "base-introuvable",
            f"Projet {projet.nom!r} : aucune branche de travail déclarée "
            "(dépôt en HEAD détaché à la déclaration) — re-déclarez le projet "
            "sur la branche vers laquelle fusionner.",
        )
    return base


def _exige_espace(espace: Path | str | None) -> Path:
    """L'espace de travail de la tâche, ou un refus motivé (projet non versionné).

    Sans historique Git, le diff **est** la comparaison de la copie avec la
    racine : privé de la copie, il n'existe pas. Le cas se produit quand l'espace
    a déjà été nettoyé — l'application doit alors être demandée avant que la
    tâche ne referme son espace de travail.
    """
    if espace is None:
        raise ApplicationRefusee(
            "espace-introuvable",
            "Projet non versionné : l'espace de travail de la tâche est requis "
            "pour calculer le diff (la copie a-t-elle déjà été nettoyée ?).",
        )
    chemin = Path(espace)
    if not chemin.is_dir():
        raise ApplicationRefusee(
            "espace-introuvable",
            f"Espace de travail introuvable : {chemin}.",
        )
    return chemin


def _diff_copie(racine: Path, espace: Path) -> tuple[Modification, ...]:
    """Compare la copie `espace` à la racine, fichier par fichier (D2, option C).

    Parcourt **la copie** et non la racine : la copie *est* le périmètre (#224
    l'y a appliqué), donc s'y tenir garantit qu'aucun fichier exclu ne ressort du
    diff — sans rejouer le moteur de motifs, qui vit chez celui qui l'applique.
    Corollaire assumé : ce parcours ne peut pas voir de suppression (cf. le
    docstring du module), il ne rend que des ajouts et des modifications.

    Aucun lien symbolique n'est suivi — même règle qu'à la copie (#224), pour la
    même raison : un lien vers `~/.ssh` recopié dans la racine serait la fuite
    qu'on prétend fermer.
    """
    modifications: list[Modification] = []
    for relatif in sorted(_fichiers(espace)):
        source = espace / relatif
        cible = racine / relatif
        if not cible.exists():
            modifications.append(
                Modification(
                    chemin=relatif,
                    nature=NATURE_AJOUT,
                    **_comptage(None, source),
                )
            )
            continue
        if _identiques(source, cible):
            continue
        modifications.append(
            Modification(
                chemin=relatif,
                nature=NATURE_MODIFICATION,
                **_comptage(cible, source),
            )
        )
    return tuple(modifications)


def _fichiers(espace: Path) -> Iterator[str]:
    """Les chemins relatifs POSIX des fichiers de `espace`, liens symboliques exclus."""
    pile = [""]
    while pile:
        courant = pile.pop()
        base = espace / courant if courant else espace
        try:
            with os.scandir(base) as entrees:
                lot = list(entrees)
        except OSError:  # dossier devenu illisible : sauté, jamais fatal
            continue
        for entree in lot:
            relatif = f"{courant}/{entree.name}" if courant else entree.name
            if entree.is_symlink():
                continue
            if entree.is_dir():
                pile.append(relatif)
            elif entree.is_file():
                yield relatif


def _identiques(source: Path, cible: Path) -> bool:
    """Les deux fichiers ont-ils exactement le même contenu ?

    Comparaison **par octets** et non par taille et date : on s'apprête à écrire
    dans le projet de quelqu'un, et une empreinte suffisait à recenser un
    livrable (#224) mais pas à décider d'un remplacement.
    """
    try:
        if source.stat().st_size != cible.stat().st_size:
            return False
        return source.read_bytes() == cible.read_bytes()
    except OSError:
        return False


def _comptage(avant: Path | None, apres: Path) -> dict[str, Any]:
    """Lignes ajoutées/supprimées entre `avant` (None = fichier neuf) et `apres`.

    Rend aussi `binaire`, vrai dès qu'un des deux côtés n'est pas du texte
    décodable ou dépasse `_MAX_COMPTAGE_OCTETS` : on sait alors *que* le fichier
    change, pas *de combien*, et prétendre le contraire vaudrait moins que le
    dire.
    """
    nouvelles = _lignes(apres)
    anciennes = _lignes(avant) if avant is not None else []
    if nouvelles is None or anciennes is None:
        return {"ajouts": 0, "suppressions": 0, "binaire": True}
    ajouts = 0
    suppressions = 0
    comparateur = difflib.SequenceMatcher(None, anciennes, nouvelles, autojunk=False)
    for tag, debut_a, fin_a, debut_b, fin_b in comparateur.get_opcodes():
        if tag in ("insert", "replace"):
            ajouts += fin_b - debut_b
        if tag in ("delete", "replace"):
            suppressions += fin_a - debut_a
    return {"ajouts": ajouts, "suppressions": suppressions, "binaire": False}


def _lignes(fichier: Path) -> list[str] | None:
    """Les lignes de `fichier`, ou None s'il est binaire, illisible ou trop gros."""
    try:
        if fichier.stat().st_size > _MAX_COMPTAGE_OCTETS:
            return None
        return fichier.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return None


# --------------------------------------------------------------------------- #
# Projet versionné : le diff se lit dans Git
# --------------------------------------------------------------------------- #


def _diff_branche(racine: Path, base: str, branche: str) -> tuple[Modification, ...]:
    """Le diff de `branche` depuis son point de départ sur `base` (worktree retiré).

    `base...branche` (trois points) et non `base branche` : ce qu'on s'apprête à
    fusionner, ce sont les changements **de la branche**, pas ceux que `base` a
    pris depuis. Avec deux points, le travail d'un collègue mergé entre-temps
    ressortirait comme une suppression proposée par l'agent.
    """
    return _modifications_git(racine, f"{base}...{branche}")


def _diff_worktree(espace: Path, base: str) -> tuple[Modification, ...]:
    """Le diff du worktree encore monté : commité **et** non commité, plus les nouveaux.

    Un agent outillé écrit des fichiers ; il ne fait pas forcément `git add`. Se
    contenter de ce qui est commité sur la branche présenterait un diff vide à
    l'humain, puis fusionnerait une branche sans le travail qu'elle était censée
    porter. Les fichiers non suivis sont donc ajoutés au diff — `--exclude-standard`
    en retire ce que le projet a lui-même déclaré ignorer, ce qui est le seul
    endroit où le `.gitignore` de l'utilisateur a son mot à dire.
    """
    suivis = _modifications_git(espace, base)
    connus = {m.chemin for m in suivis}
    nouveaux = [
        Modification(
            chemin=relatif,
            nature=NATURE_AJOUT,
            **_comptage(None, espace / relatif),
        )
        for relatif in _non_suivis(espace)
        if relatif not in connus
    ]
    return tuple(sorted([*suivis, *nouveaux], key=lambda m: m.chemin))


def _modifications_git(cwd: Path, revision: str) -> tuple[Modification, ...]:
    """Les modifications que Git rapporte pour `revision`, natures et lignes réunies.

    Deux appels parce que Git ne rend pas les deux d'un coup : `--numstat` donne
    les lignes (`-` pour un binaire) et `--name-status` la nature. `-z` évite
    l'échappement des noms de fichiers — sans lui, un chemin accentué ressort
    entre guillemets avec ses octets en `\\303\\251`, et le chemin appliqué ne
    serait pas celui que Git a vu. `--no-renames` garde un enregistrement par
    ligne : un renommage vaut une suppression et un ajout, ce qui est aussi la
    forme sous laquelle on l'applique.
    """
    chiffres = _lire_numstat(_git(cwd, "diff", "--numstat", "-z", "--no-renames", revision))
    natures = _lire_name_status(
        _git(cwd, "diff", "--name-status", "-z", "--no-renames", revision)
    )
    return tuple(
        Modification(
            chemin=chemin,
            nature=natures.get(chemin, NATURE_MODIFICATION),
            ajouts=ajouts,
            suppressions=suppressions,
            binaire=binaire,
        )
        for chemin, (ajouts, suppressions, binaire) in sorted(chiffres.items())
    )


def _lire_numstat(resultat: subprocess.CompletedProcess[str]) -> dict[str, tuple[int, int, bool]]:
    """`git diff --numstat -z` → `{chemin: (ajouts, suppressions, binaire)}`."""
    releve: dict[str, tuple[int, int, bool]] = {}
    for enregistrement in _enregistrements(resultat):
        morceaux = enregistrement.split("\t", 2)
        if len(morceaux) != 3:
            continue
        ajouts, suppressions, chemin = morceaux
        binaire = ajouts == "-" or suppressions == "-"
        releve[chemin] = (
            0 if binaire else int(ajouts),
            0 if binaire else int(suppressions),
            binaire,
        )
    return releve


def _lire_name_status(resultat: subprocess.CompletedProcess[str]) -> dict[str, str]:
    """`git diff --name-status -z` → `{chemin: nature}` (statut et chemin alternés)."""
    champs = _enregistrements(resultat)
    natures: dict[str, str] = {}
    for index in range(0, len(champs) - 1, 2):
        statut = champs[index][:1]
        natures[champs[index + 1]] = _NATURES_GIT.get(statut, NATURE_MODIFICATION)
    return natures


#: Le statut d'un fichier chez Git, ramené aux trois natures de `Modification`.
#: Tout le reste (copie, changement de type, non fusionné) est traité comme une
#: modification : c'en est une, et inventer une quatrième nature pour un cas que
#: l'UI afficherait pareil ne rendrait service à personne.
_NATURES_GIT = {"A": NATURE_AJOUT, "D": NATURE_SUPPRESSION, "M": NATURE_MODIFICATION}


def _enregistrements(resultat: subprocess.CompletedProcess[str]) -> list[str]:
    """Les champs d'une sortie Git en `-z` (séparés par NUL), les vides retirés."""
    if resultat.returncode != 0:
        raise ApplicationRefusee("git-refuse", f"Git a refusé : {_message_git(resultat)}")
    return [champ for champ in resultat.stdout.split("\0") if champ]


def _non_suivis(espace: Path) -> list[str]:
    """Les fichiers non suivis du worktree, hors de ce que le projet ignore."""
    resultat = _git(espace, "ls-files", "--others", "--exclude-standard", "-z")
    return _enregistrements(resultat)


def _exige_ref(racine: Path, branche: str, motif: str) -> None:
    """Refuse, motif à l'appui, si la branche locale `branche` n'existe pas."""
    if _git(racine, "rev-parse", "--verify", "--quiet", f"refs/heads/{branche}").returncode:
        raise ApplicationRefusee(
            motif, f"Branche {branche!r} introuvable dans le dépôt {racine}."
        )


def _git(cwd: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Lance `git <arguments>` dans `cwd` et rend le processus achevé.

    Ne lève **que** pour ce qui n'est pas un verdict de Git (binaire absent,
    délai dépassé) : un code de retour non nul est une réponse, que l'appelant
    traduit en refus motivé — c'est ce qui permet à `_exige_ref` de poser une
    question sans attraper d'exception.
    """
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DELAI_GIT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ApplicationRefusee(
            "git-indisponible", f"Git indisponible pour {cwd} : {exc}"
        ) from exc


def _message_git(resultat: subprocess.CompletedProcess[str]) -> str:
    """Le message d'erreur de Git, en une ligne (stderr, à défaut stdout)."""
    brut = (resultat.stderr or resultat.stdout or "").strip()
    return " ".join(brut.split()) or f"code de retour {resultat.returncode}"


# --------------------------------------------------------------------------- #
# Périmètre : le contrôle qui précède toute écriture
# --------------------------------------------------------------------------- #


def verifier_perimetre(projet: Projet, diff: DiffProjet) -> tuple[Path, ...]:
    """Les chemins absolus visés par `diff`, ou `ApplicationRefusee` (EF-38).

    Chaque chemin est résolu **sous la racine** par `chemin_dans_racine` — la
    brique que le socle (#221) a écrite pour ce lot : « aucune écriture ne se
    calcule autrement ». Un `..`, un chemin absolu venu d'ailleurs ou un lien
    pointant hors du projet sont refusés avec le motif du socle (`hors-racine`).

    Appelée **avant la première écriture**, et sur le diff entier : le critère
    est « refusée avec son motif, pas appliquée partiellement », et il ne tient
    pas si l'on vérifie au fil de l'écriture.

    Ce contrôle porte sur la **frontière** (EF-38), pas sur les motifs
    d'inclusion/exclusion du périmètre : ceux-là sont appliqués à la dérivation
    de l'espace de travail (#224), par le seul moteur de motifs du projet — en
    rejouer un second ici ferait deux vérités là où il en faut une.
    """
    chemins: list[Path] = []
    for modification in diff.modifications:
        try:
            chemins.append(chemin_dans_racine(projet.racine, modification.chemin))
        except RacineRefusee as refus:
            raise ApplicationRefusee(
                refus.motif,
                f"Application refusée — {modification.chemin} sort du périmètre "
                f"du projet {projet.nom!r} : {refus}",
            ) from refus
    return tuple(chemins)


# --------------------------------------------------------------------------- #
# Application
# --------------------------------------------------------------------------- #


def appliquer(
    projet: Projet, diff: DiffProjet, *, espace: Path | str | None = None
) -> tuple[str, ...]:
    """Écrit `diff` dans le projet et rend les chemins appliqués (EF-37, D2).

    N'est appelée qu'**après** un accord humain (cf.
    `maestro.controltower.validation.appliquer_sous_validation`) : ce module
    n'interroge personne. Projet versionné → la branche de tâche est fusionnée
    vers la branche de travail déclarée ; sinon → les fichiers du diff sont
    recopiés dans la racine.

    Le périmètre est vérifié en entier d'abord (`verifier_perimetre`) : lever
    ici, c'est laisser le projet intact.
    """
    racine = valider_racine(projet.racine)
    verifier_perimetre(projet, diff)
    if diff.vide:
        return ()
    if diff.versionne:
        return _fusionner(racine, diff, Path(espace) if espace is not None else None)
    return _recopier(racine, diff, _exige_espace(espace))


def _recopier(racine: Path, diff: DiffProjet, espace: Path) -> tuple[str, ...]:
    """Recopie les fichiers du diff de `espace` vers `racine` (projet non versionné).

    Écrit par `read_bytes`/`write_bytes` et non `shutil.copy2` : recopier les
    métadonnées d'un fichier produit dans un espace temporaire n'apporte rien, et
    l'écriture explicite ne suit aucun lien symbolique déjà en place à la cible.
    """
    appliques: list[str] = []
    for modification in diff.modifications:
        if modification.nature == NATURE_SUPPRESSION:
            continue  # cf. le docstring du module : jamais sur un projet sans historique
        source = espace / modification.chemin
        cible = chemin_dans_racine(racine, modification.chemin)
        try:
            cible.parent.mkdir(parents=True, exist_ok=True)
            cible.write_bytes(source.read_bytes())
        except OSError as exc:
            raise ApplicationRefusee(
                "ecriture-refusee",
                f"Écriture impossible dans le projet : {modification.chemin} — {exc}",
            ) from exc
        appliques.append(modification.chemin)
    return tuple(appliques)


def _fusionner(racine: Path, diff: DiffProjet, espace: Path | None) -> tuple[str, ...]:
    """Fusionne la branche de tâche vers la branche de travail déclarée (D2, option B).

    Trois gestes, dans cet ordre : le travail encore non commité du worktree est
    **commité sur sa branche** (sans quoi la fusion emporterait moins que le diff
    approuvé), l'état de la racine est vérifié, puis la fusion a lieu.

    La racine doit être **propre et posée sur la branche de travail** : fusionner
    sous les pieds de quelqu'un qui a des changements en cours, ou basculer sa
    copie de travail sur une autre branche pour les besoins d'un agent, sont
    précisément les destructions que EF-36/EF-37 existent pour empêcher. Le refus
    nomme ce qui bloque plutôt que de forcer.
    """
    if espace is not None and espace.is_dir():
        commiter_en_attente(espace, diff.branche)
    _exige_racine_disponible(racine, diff.base)
    # `--no-ff` écrit un COMMIT de fusion : il lui faut une identité, exactement comme au commit de
    # rattrapage ci-dessus. Elle manquait ici (#333) — la moitié non couverte de ce que le
    # commentaire de `_AUTEUR_NOM` annonce pourtant : « un user.email absent ferait échouer la
    # fusion au tout dernier geste ». C'est le geste en question, et il échouait pour de bon sur
    # tout dépôt sans identité (dépôt neuf, conteneur, runner CI) — en `fusion-refusee`, motif qui
    # désigne un conflit et envoyait donc chercher le problème là où il n'était pas.
    resultat = _git(
        racine,
        "-c",
        f"user.name={_AUTEUR_NOM}",
        "-c",
        f"user.email={_AUTEUR_COURRIEL}",
        "merge",
        "--no-ff",
        "--no-edit",
        "-m",
        _message_fusion(diff),
        diff.branche,
    )
    if resultat.returncode != 0:
        _git(racine, "merge", "--abort")
        raise ApplicationRefusee(
            "fusion-refusee",
            f"Fusion de {diff.branche} vers {diff.base} refusée par Git "
            f"({_message_git(resultat)}) — la branche conserve le travail.",
        )
    return tuple(m.chemin for m in diff.modifications)


def _message_fusion(diff: DiffProjet) -> str:
    """Le message du commit de fusion — le diff approuvé, résumé en une ligne."""
    return (
        f"Maestro : application de {diff.branche} "
        f"({diff.fichiers} fichier(s), +{diff.ajouts} / −{diff.suppressions})"
    )


def commiter_en_attente(espace: Path, branche: str) -> None:
    """Commite sur `branche` ce que le worktree porte encore, s'il porte quelque chose.

    Les hooks du dépôt ne sont **pas** contournés : ce sont ceux de
    l'utilisateur, et un `pre-commit` qui refuse le travail d'un agent a
    exactement raison de le faire — le refus remonte motivé plutôt que de passer
    en force. L'identité est posée par `-c` pour ne rien écrire dans la
    configuration du projet.

    **Publique depuis #705**, et c'est le seul changement : elle a désormais deux
    appelants, parce que la phrase « le worktree est retiré, jamais la branche —
    c'est elle qui porte le travail jusqu'à la fusion » (`maestro.sandbox.projet`)
    n'était vraie que si l'agent avait commité de lui-même. Le second appelant est
    donc la **fin de vie du worktree**, qui la joue avant de démonter : sans elle,
    `--force` emportait le travail non commité et la branche ne portait rien.

    Une seule orthographe pour un seul geste : deux implémentations de « commiter
    ce que le worktree porte encore » finiraient par diverger, et celle qui se
    tromperait laisserait le travail d'un agent hors de la branche qu'on fusionne.
    Chaque appelant garde en revanche sa **politique d'échec** — la fusion refuse
    motivé, la fin de vie du worktree s'abstient en silence (elle vit dans un
    `finally`, où lever masquerait la cause réelle de la tâche).
    """
    if _git(espace, "add", "-A").returncode != 0:
        raise ApplicationRefusee(
            "commit-refuse", f"Git n'a pas pu indexer le travail de {branche} dans {espace}."
        )
    if _git(espace, "diff", "--cached", "--quiet").returncode == 0:
        return  # rien en attente : tout est déjà commité sur la branche
    resultat = _git(
        espace,
        "-c",
        f"user.name={_AUTEUR_NOM}",
        "-c",
        f"user.email={_AUTEUR_COURRIEL}",
        "commit",
        "-m",
        f"Maestro : travail de la tâche ({branche})",
    )
    if resultat.returncode != 0:
        raise ApplicationRefusee(
            "commit-refuse",
            f"Commit du travail sur {branche} refusé : {_message_git(resultat)}",
        )


def _exige_racine_disponible(racine: Path, base: str) -> None:
    """Refuse si la racine n'est pas propre, ou pas posée sur la branche de travail."""
    tete = _git(racine, "rev-parse", "--abbrev-ref", "HEAD")
    courante = tete.stdout.strip() if tete.returncode == 0 else ""
    if courante != base:
        raise ApplicationRefusee(
            "racine-occupee",
            f"Le projet est sur {courante or 'HEAD détachée'}, pas sur sa branche "
            f"de travail {base!r} — placez-vous dessus avant d'appliquer.",
        )
    etat = _git(racine, "status", "--porcelain")
    if etat.returncode != 0 or etat.stdout.strip():
        raise ApplicationRefusee(
            "racine-occupee",
            f"Le projet a des changements en cours dans {racine} — committez-les "
            "ou mettez-les de côté avant d'appliquer.",
        )


#: Les trois issues d'une demande d'application (cf. `ResultatApplication`) :
#: le diff était vide (personne n'a été dérangé), l'humain a dit oui, l'humain a
#: dit non. Un refus **n'est pas une erreur** — c'est une réponse, et c'est
#: pourquoi il rend un résultat là où un chemin hors périmètre lève.
APPLICATION_SANS_OBJET = "sans_objet"
APPLICATION_APPROUVEE = "approuvee"
APPLICATION_REFUSEE = "refusee"


@dataclass(frozen=True)
class ResultatApplication:
    """L'issue d'une demande d'application : ce qui a été décidé, et ce qui a été écrit.

    `statut` vaut `SANS_OBJET` (diff vide — personne n'a été dérangé), `APPROUVEE`
    ou `REFUSEE`. `appliques` est vide dans les deux derniers cas sauf accord :
    sur refus, **rien n'est écrit** et le travail reste consultable (branche de
    tâche conservée, ou copie intacte).
    """

    statut: str
    diff: DiffProjet
    detail: str = ""
    appliques: tuple[str, ...] = field(default_factory=tuple)

    @property
    def approuvee(self) -> bool:
        """L'application a-t-elle été autorisée et faite ?"""
        return self.statut == APPLICATION_APPROUVEE

    def to_dict(self) -> dict[str, Any]:
        """Réémet le résultat en dict JSON-sérialisable."""
        return {
            "statut": self.statut,
            "diff": self.diff.to_dict(),
            "detail": self.detail,
            "appliques": list(self.appliques),
        }
