"""La **dernière version de chaque famille** de modèles Claude, lue dans une source unique (#1270).

    from maestro.familles_claude import derniere_version, resoudre_famille

    derniere_version("opus")          # 'claude-opus-5-5'
    resoudre_famille("Opus")          # 'claude-opus-5-5'
    resoudre_famille("claude-opus-5") # 'claude-opus-5' — un identifiant complet passe tel quel

Le produit écrivait ses modèles en dur, à quatre endroits qui ne vieillissaient pas
ensemble : le défaut du Chef de projet (`ANTHROPIC_MODEL`, `claude-opus-5`), celui
des agents exécutants (`claude-sonnet-5`), celui du classifieur, et la gamme que
le formulaire d'agent propose (`ClaudeProvider.MODELES`). Opus 5.5 est sorti, et
rien n'a bougé ni ne l'a dit — c'est la liste fermée qui vieillit en silence que
« Maestro juge, il ne bride pas » refuse (docs/41).

Tous lisent désormais `maestro/providers/familles-claude.tsv`, le fichier que les
runs `/orchestrate` lisent déjà (#1269, `scripts/orchestrate/run.sh`) : une
nouvelle version sort, on remplace l'identifiant de sa famille **là et nulle part
ailleurs**, et le Chef de projet, les agents, le classifieur et le formulaire
passent ensemble à la suivante.

Module **feuille** : il n'importe rien de Maestro. `maestro.config` et
`maestro.agents.catalog` le lisent, et le paquet `maestro.providers` importe
lui-même `maestro.config` — un lecteur rangé dans ce paquet fermerait la boucle.
Les données restent à côté du fournisseur qu'elles décrivent ; seul leur lecteur
vit ici.

La lecture est la même que celle de `run.sh` : lignes `#` et lignes à moins de deux
colonnes écartées, fin de ligne Windows tolérée, famille comparée sans la casse.
Un fichier absent ou sans famille lève `FamillesIllisibles` au premier usage :
un modèle deviné serait pire qu'un démarrage refusé qui dit pourquoi.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

#: La source unique (#1269). Livrée avec le paquet (`package-data` de
#: `pyproject.toml`) : sans elle, une roue installée ne saurait nommer aucun modèle.
FICHIER_FAMILLES = Path(__file__).parent / "providers" / "familles-claude.tsv"


class FamillesIllisibles(RuntimeError):
    """`familles-claude.tsv` est absent, ou ne nomme aucune famille."""


@dataclass(frozen=True)
class FamilleClaude:
    """Une ligne du fichier : la famille, sa dernière version, son nom lisible."""

    famille: str
    identifiant: str
    libelle: str


@cache
def familles_claude() -> tuple[FamilleClaude, ...]:
    """Les familles du fichier, dans son ordre — lues une fois par processus."""
    try:
        texte = FICHIER_FAMILLES.read_text(encoding="utf-8")
    except OSError as exc:
        raise FamillesIllisibles(
            f"{FICHIER_FAMILLES} introuvable — c'est lui qui dit la dernière version "
            "de chaque famille Claude, et donc le modèle par défaut des agents."
        ) from exc
    familles: list[FamilleClaude] = []
    for ligne in texte.splitlines():
        ligne = ligne.rstrip("\r")
        if ligne.startswith("#"):
            continue
        colonnes = [colonne.strip() for colonne in ligne.split("\t")]
        if len(colonnes) < 2 or not colonnes[0] or not colonnes[1]:
            continue
        libelle = colonnes[2] if len(colonnes) > 2 and colonnes[2] else colonnes[1]
        familles.append(FamilleClaude(colonnes[0].lower(), colonnes[1], libelle))
    if not familles:
        raise FamillesIllisibles(f"{FICHIER_FAMILLES} ne nomme aucune famille Claude.")
    return tuple(familles)


def derniere_version(famille: str) -> str:
    """L'identifiant complet de la dernière version de `famille` (`opus`, `sonnet`…).

    Lève `KeyError` pour une famille que le fichier ne connaît pas : l'appelant
    nomme une famille écrite dans le code, et un nom inconnu est une faute de
    frappe à faire éclater, pas une valeur à deviner.
    """
    cherchee = famille.strip().lower()
    for ligne in familles_claude():
        if ligne.famille == cherchee:
            return ligne.identifiant
    connues = ", ".join(ligne.famille for ligne in familles_claude())
    raise KeyError(f"famille Claude inconnue : {famille!r} (connues : {connues}).")


def resoudre_famille(modele: str) -> str:
    """`modele` en identifiant complet s'il nomme une famille, sinon tel quel.

    C'est ce qui laisse écrire `ANTHROPIC_MODEL=opus` comme on écrit
    `--modele opus` à `run.sh` : la famille suit le fichier, un identifiant
    complet (`claude-opus-5`) reste un choix épinglé en connaissance de cause.
    """
    cherchee = modele.strip().lower()
    for ligne in familles_claude():
        if ligne.famille == cherchee:
            return ligne.identifiant
    return modele
