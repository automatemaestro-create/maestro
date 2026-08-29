"""Le catalogue des fournisseurs — ce que Maestro supporte, et ce qui est ici (#487).

**Une seule source, deux colonnes.** Le registre (`maestro.providers.registry`)
dit ce que Maestro **sait servir** ; la sonde du poste (`maestro.poste`) dit ce
qui est **présent ici**. Ce module les marie et
n'en fait qu'une vue — c'est le critère 3 du ticket : la détection *alimente* le
catalogue, elle n'ouvre pas une seconde source à côté de lui.

Les deux colonnes ne se confondent jamais, et c'est tout l'intérêt :

* **supporté ici** — le fournisseur est au registre *et* quelque chose sur ce
  poste le sert (un CLI, un serveur local, une clé) : proposable tel quel ;
* **supporté, absent d'ici** — le fournisseur est au registre, rien ne l'arme
  encore : proposable, mais il faudra une clé ou un endpoint ;
* **présent ici, non supporté** — un outil trouvé sur la machine que Maestro ne
  sait pas piloter (un agent CLI tiers, cf.
  [docs/34](../../docs/34-decision-agent-cli-tiers-acp.md)) : montré, jamais
  proposé. Le taire ferait croire qu'il n'est pas là ; le proposer serait le
  seul vrai mensonge possible.

Ce que le catalogue **ne fait pas** : deviner. Les incertitudes de la sonde
remontent telles quelles, jusqu'à l'écran (critère 4).

⚠ **Cette route est l'endroit où se rendre que #253 doit meubler.** Le catalogue
demandé par #253 — les **modèles** d'un fournisseur et les **niveaux d'effort**
d'un modèle, lus du registre — s'ajoute *ici*, en colonnes de la même charge :
`modeles_ici` est ce que la **sonde** a vu sur ce poste, jamais ce que Maestro
supporte. Ouvrir une seconde route pour l'autre moitié recréerait exactement la
double source que ce module existe pour éviter.
"""

from __future__ import annotations

from typing import Any

from maestro.poste import Constat, RapportSonde


def catalogue(rapport: RapportSonde) -> dict[str, Any]:
    """La charge de `GET /api/fournisseurs` : le registre, éclairé par le poste."""
    supportes = _noms_supportes()
    return {
        "fournisseurs": [_fournisseur(nom, rapport) for nom in supportes],
        "hors_registre": [_en_clair(c) for c in rapport.hors_registre],
        "incertitudes": list(rapport.incertitudes),
    }


def _fournisseur(nom: str, rapport: RapportSonde) -> dict[str, Any]:
    """Un fournisseur du registre, avec ce que le poste en dit."""
    constats = rapport.par_fournisseur(nom)
    modeles: list[str] = []
    for constat in constats:
        for modele in constat.modeles:
            if modele not in modeles:
                modeles.append(modele)
    return {
        "nom": nom,
        # Toujours vrai ici : la liste part du registre. Le champ est explicite
        # pour que le front n'ait pas à le déduire de l'endroit où il a lu la
        # ligne — c'est la moitié « supporté par Maestro » du critère 3.
        "supporte": True,
        "present_ici": bool(constats),
        "utilisable_ici": any(c.utilisable for c in constats),
        "modeles_ici": modeles,
        "constats": [_en_clair(c) for c in constats],
    }


def _en_clair(constat: Constat) -> dict[str, Any]:
    """Un constat en JSON — champs nommés un par un, jamais `asdict`.

    La sonde ne porte **aucune valeur de secret** (seulement le nom des
    variables) ; sérialiser explicitement est ce qui garantit qu'un champ ajouté
    demain à la dataclass ne parte pas sur le réseau sans qu'on l'ait décidé.
    """
    return {
        "genre": constat.genre,
        "cle": constat.cle,
        "libelle": constat.libelle,
        "fournisseur": constat.fournisseur,
        "utilisable": constat.utilisable,
        "detail": constat.detail,
        "origine": constat.origine,
        "modeles": list(constat.modeles),
        "incertitude": constat.incertitude,
    }


def _noms_supportes() -> tuple[str, ...]:
    """Les fournisseurs enregistrés — le registre du code fait foi, jamais une liste.

    Import **paresseux**, comme partout dans le dépôt : charger
    `maestro.providers` tire le SDK Claude, prix qu'on paie au premier appel et
    non au chargement de l'API. Un fournisseur ajouté au registre apparaît ici
    sans qu'on touche à ce fichier ni au front.
    """
    from maestro.providers import available_providers

    return tuple(available_providers())


__all__ = ["catalogue"]
