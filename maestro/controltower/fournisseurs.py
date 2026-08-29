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

⚠ **Cette route est l'endroit où se rend #253, et il s'y est rendu.** Le
catalogue demandé par #253 — les **modèles** d'un fournisseur et les **niveaux
d'effort** d'un modèle, lus du registre — est *ici*, en colonnes de la même
charge : `modeles` et `modeles_libres` sont ce que **Maestro** annonce
(`FournisseurDisponible.to_dict`, réémis tel quel plutôt que recopié),
`modeles_ici` ce que la **sonde** a vu sur ce poste. Ouvrir une seconde route
pour l'autre moitié recréerait exactement la double source que ce module existe
pour éviter — c'est pourquoi les deux tickets se sont rejoints sur celle-ci au
lieu d'en tenir chacun une.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from maestro.poste import Constat, RapportSonde

if TYPE_CHECKING:  # typage seul — l'import réel reste paresseux (voir `_registre`)
    from maestro.providers import FournisseurDisponible


def catalogue(rapport: RapportSonde) -> dict[str, Any]:
    """La charge de `GET /api/fournisseurs` : le registre, éclairé par le poste."""
    return {
        "fournisseurs": [_fournisseur(fiche, rapport) for fiche in _registre()],
        "hors_registre": [_en_clair(c) for c in rapport.hors_registre],
        "incertitudes": list(rapport.incertitudes),
    }


def _fournisseur(fiche: FournisseurDisponible, rapport: RapportSonde) -> dict[str, Any]:
    """Un fournisseur du registre, avec sa gamme annoncée et ce que le poste en dit.

    Les deux moitiés arrivent par deux chemins qu'on ne mélange pas : la fiche du
    registre est **réémise telle qu'elle se sérialise elle-même**
    (`FournisseurDisponible.to_dict`, #253) — la recopier champ à champ ferait
    ici une seconde définition de la gamme —, et les colonnes du poste sont
    calculées sur les constats de la sonde (#487).
    """
    constats = rapport.par_fournisseur(fiche.nom)
    modeles_ici: list[str] = []
    for constat in constats:
        for modele in constat.modeles:
            if modele not in modeles_ici:
                modeles_ici.append(modele)
    return {
        **fiche.to_dict(),
        # Toujours vrai ici : la liste part du registre. Le champ est explicite
        # pour que le front n'ait pas à le déduire de l'endroit où il a lu la
        # ligne — c'est la moitié « supporté par Maestro » du critère 3.
        "supporte": True,
        "present_ici": bool(constats),
        "utilisable_ici": any(c.utilisable for c in constats),
        "modeles_ici": modeles_ici,
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


def _registre() -> tuple[FournisseurDisponible, ...]:
    """Les fiches du registre — le code fait foi, jamais une liste recopiée ici.

    C'est `catalogue_fournisseurs()` (#253) et rien d'autre : nom, gamme annoncée
    et efforts admis viennent de la **classe** du fournisseur, donc se lisent sans
    credentials, sans réseau et sans rien construire. Un fournisseur ajouté au
    registre apparaît ici sans qu'on touche à ce fichier ni au front.

    Import **paresseux**, comme partout dans le dépôt : charger
    `maestro.providers` tire le SDK Claude, prix qu'on paie au premier appel et
    non au chargement de l'API.
    """
    from maestro.providers import catalogue_fournisseurs

    return tuple(catalogue_fournisseurs())


__all__ = ["catalogue"]
