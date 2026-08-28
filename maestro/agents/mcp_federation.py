"""Fédération de la bibliothèque MCP : le câblage des sources (#677, #678, parent #673).

`maestro.agents.mcp_registry` sait **composer** ses sources ; il ne sait ni lire
un disque ni parler à un registre, et c'est voulu — c'est une structure de
données. Ce module est le câblage qui manque entre les trois pièces du chantier :

    miroir (lot 1, `mcp_amont`)
        → traduction (lot 2, `mcp_traduction`)
            → bibliothèque à plusieurs sources (lot 3, `mcp_registry`)
                → porte d'admission (lot 4, `mcp_admission`)

## Ce que la fédération garantit

- **Elle ne lève jamais.** Un miroir absent, illisible, vide, une entrée
  intraduisible, un journal d'admissions corrompu : tout retombe sur la
  bibliothèque curée, qui est le comportement d'avant le chantier. La Control
  Tower ne doit pas perdre sa bibliothèque parce qu'un fichier de cache est
  corrompu — et le registre amont est en **préversion**, « no uptime or data
  durability guarantees ».
  ⚠ Une exception à cette règle, et elle est nommée : un journal d'admissions
  illisible retire de l'allowlist tout ce qu'il autorisait, donc la cause est
  **rendue** (`Federation.cause_admissions`) au lieu d'être seulement
  journalisée. Perdre une découverte est un affichage en moins ; perdre une
  admission est un serveur qui ne monte plus, et personne ne doit avoir à
  deviner pourquoi.
- **Elle ne moissonne pas.** Elle *lit* le miroir déjà sur le disque. Déclencher
  un rafraîchissement ici mettrait dix minutes de réseau sur le chemin d'une
  requête d'écran ; `MiroirAmont.rafraichir_si_perime()` est le geste d'une
  boucle de fond, pas d'une lecture.
- **Elle est mémoïsée sur l'empreinte du miroir ET du journal d'admissions**
  (mtime + taille de chacun), comme `MiroirAmont.entrees()` l'est déjà pour le
  premier. Traduire 25 000 entrées coûte trop cher pour être refait par requête,
  et une empreinte — plutôt qu'une durée — fait tomber la mémoire à l'instant
  même où elle mentirait. Le journal en fait partie parce qu'une admission doit
  être visible **tout de suite** : sur la seule empreinte du miroir, elle
  n'apparaîtrait qu'au prochain rafraîchissement, une heure plus tard.

## Le seul refus qui n'est pas nommé par la traduction

Une entrée traduite arrive sans `version`, sans `depot` et sans `statut` : ces
trois-là vivent dans l'enveloppe du miroir (`EntreeAmont`) et non dans le
`server.json` que la traduction lit. C'est ici qu'on les recolle — c'est le seul
endroit où les deux moitiés sont tenues ensemble, et le critère 2 du ticket les
demande sur toute entrée découverte.

## Une couture temporaire, et elle est nommée

`maestro.agents.mcp_traduction` est livré par le **lot 2 (#676)**, dont la PR
était encore ouverte quand ce lot a été écrit. L'import est donc **paresseux et
tolérant** (`_traducteur`) : sans le module, la fédération sert la bibliothèque
curée en le disant, au lieu de faire échouer l'import de toute la Control Tower.
Ce n'est pas une option de configuration — c'est une couture entre deux lots
d'un même chantier, qui devient du code mort le jour où #676 est mergé et se
retire alors en trois lignes.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from maestro.agents.mcp_admission import (
    EtatAmontEntree,
    MagasinAdmissions,
    veiller,
)
from maestro.agents.mcp_amont import EntreeAmont, MiroirAmont
from maestro.agents.mcp_registry import (
    PROVENANCE,
    SEED,
    Admission,
    EntreeRegistre,
    Provenance,
    ProvenanceDecouverte,
    RegistreMcp,
    SignalAmont,
)

_LOG = logging.getLogger(__name__)

#: La cause servie quand le lot 2 n'est pas encore là (voir l'en-tête du module).
CAUSE_SANS_TRADUCTION = (
    "traduction des entrées d'amont indisponible (maestro.agents.mcp_traduction, "
    "lot 2 du parent #673) : seules les entrées curées sont servies."
)

#: Le type du verbe attendu du lot 2 — `EntreeAmont` → objet portant `.entree`
#: (une `EntreeRegistre` ou None) et `.refus` (un objet portant `.motif`).
Traducteur = Callable[[EntreeAmont], Any]


@dataclass(frozen=True)
class Federation:
    """Le résultat d'une fédération : la bibliothèque, et ce qu'il a fallu écarter.

    `registre` est utilisable seul — c'est ce que la Control Tower monte. Le
    reste est le **compte rendu**, que la route de provenance et l'écran (lot 5)
    lisent pour dire l'écart entre ce que le miroir porte et ce que la
    bibliothèque sert. Un écart tu se lit comme une perte silencieuse.
    """

    registre: RegistreMcp
    #: Le nombre d'entrées d'amont lues dans le miroir.
    lues: int = 0
    #: Le nombre d'entrées traduites avec succès.
    traduites: int = 0
    #: Le nombre d'entrées refusées par la traduction.
    refusees: int = 0
    #: Le compte des refus **par motif** (`mcp_traduction.MOTIFS`) — ce sur quoi
    #: une UI groupe, et ce qu'un test compte. Jamais les phrases, qui bougent.
    motifs: dict[str, int] = field(default_factory=dict)
    #: La cause d'une fédération qui n'a rien pu ajouter, vide sinon.
    cause: str = ""
    #: Le nombre d'admissions **actives** au moment de la composition (#678).
    admises: int = 0
    #: Le nombre d'admissions **révoquées** encore au journal.
    revoquees: int = 0
    #: La cause d'un journal d'admissions illisible, vide sinon. Rendue à part de
    #: `cause` parce qu'elle ne coûte pas la même chose : l'une prive d'un
    #: affichage, l'autre prive d'un montage (voir l'en-tête du module).
    cause_admissions: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Réémet le compte rendu en dict JSON-sérialisable (forme publique API/UI)."""
        return {
            "lues": self.lues,
            "traduites": self.traduites,
            "refusees": self.refusees,
            "motifs": dict(self.motifs),
            "ecartees": list(self.registre.decouvertes_ecartees),
            "cause": self.cause,
            "admises": self.admises,
            "revoquees": self.revoquees,
            "signaux": [s.to_dict() for s in self.registre.signaux],
            "cause_admissions": self.cause_admissions,
        }


def _traducteur() -> Traducteur | None:
    """Le verbe de traduction du lot 2, ou None tant qu'il n'est pas livré.

    Import **différé et rattrapé** : voir « Une couture temporaire » en tête de
    module. Le jour où #676 est mergé, cette fonction devient un import normal.
    """
    try:
        from maestro.agents.mcp_traduction import traduire_entree
    except ImportError:  # pragma: no cover - couture entre deux lots (#676)
        return None
    # Passage par une variable annotée plutôt qu'un `cast` : tant que le module
    # est absent, mypy voit `Any` (dérogation `ignore_missing_imports` du
    # pyproject) ; une fois #676 mergé il voit le vrai type, qui satisfait
    # `Traducteur` — les deux régimes passent, et rien n'est à défaire ici.
    verbe: Traducteur = traduire_entree
    return verbe


def _depot(document: Mapping[str, Any]) -> str:
    """L'URL du dépôt déclarée par le `server.json`, ou "" — jamais devinée.

    Cherchée sous l'enveloppe de listing comme sur le document nu, parce que le
    miroir garde le `server.json` **verbatim** et qu'un appelant peut tenir l'un
    ou l'autre.
    """
    interne = document.get("server")
    brut = interne if isinstance(interne, Mapping) else document
    depot = brut.get("repository")
    if not isinstance(depot, Mapping):
        return ""
    url = depot.get("url")
    return url.strip() if isinstance(url, str) else ""


def traduire_miroir(
    entrees: Iterable[EntreeAmont], traducteur: Traducteur | None = None
) -> tuple[tuple[EntreeRegistre, ...], int, int, dict[str, int]]:
    """Traduit les entrées du miroir en entrées de bibliothèque **découvertes**.

    Rend `(entrées, traduites, refusées, motifs)`. Ne lève jamais : une entrée
    dont la traduction se casse — ce que son contrat interdit, mais on ne parie
    pas la bibliothèque dessus — est comptée comme refusée sous le motif
    `exception` et la boucle continue. Sur 25 000 entrées d'amont, une seule
    ligne fautive ne doit pas coûter les 24 999 autres.

    Le marquage `curee=False` n'est **pas** fait ici : c'est `RegistreMcp` qui
    l'impose à tout ce qui entre par `decouvertes`, pour qu'un seul endroit
    décide de la source (voir sa docstring).
    """
    traducteur = traducteur or _traducteur()
    if traducteur is None:
        return (), 0, 0, {}
    retenues: list[EntreeRegistre] = []
    traduites = 0
    refusees = 0
    motifs: dict[str, int] = {}
    for amont in entrees:
        try:
            resultat = traducteur(amont)
        except Exception as exc:  # noqa: BLE001 - une entrée d'amont ne casse pas la bibliothèque
            refusees += 1
            motifs["exception"] = motifs.get("exception", 0) + 1
            _LOG.warning("traduction MCP en échec pour %r : %s", amont.nom, exc)
            continue
        entree = getattr(resultat, "entree", None)
        if entree is None:
            refusees += 1
            refus = getattr(resultat, "refus", None)
            motif = getattr(refus, "motif", "") or "inconnu"
            motifs[motif] = motifs.get(motif, 0) + 1
            continue
        traduites += 1
        # Les trois signaux que le `server.json` ne porte pas : ils vivent dans
        # l'enveloppe du miroir, et c'est ici qu'on les recolle (critère 2).
        retenues.append(
            replace(
                entree,
                version=amont.version,
                statut=amont.statut,
                depot=_depot(amont.document),
            )
        )
    return tuple(retenues), traduites, refusees, motifs


def lire_admissions(magasin: MagasinAdmissions | None = None) -> tuple[tuple[Admission, ...], str]:
    """Le journal des admissions et sa cause d'échec — **sans jamais lever** (#678).

    Rend `(admissions, cause)`. Un journal absent est l'état normal d'un projet
    qui n'a rien admis, et rend `((), "")` ; un journal illisible rend `((),
    "<cause>")`, ce qui **retire de l'allowlist** tout ce qu'il autorisait.

    C'est le repli le plus prudent des deux et non le plus commode : servir une
    allowlist qu'on ne sait pas relire entièrement, ce serait monter des serveurs
    sur la foi d'un fichier corrompu. La cause remonte jusqu'à l'écran plutôt que
    de rester dans un journal d'application — voir l'en-tête du module.
    """
    try:
        magasin = magasin if magasin is not None else MagasinAdmissions.default()
        return magasin.lister(), ""
    except Exception as exc:  # noqa: BLE001 - un journal fautif ne coûte pas la bibliothèque
        _LOG.warning("journal des admissions MCP illisible, allowlist réduite au seed : %s", exc)
        return (), str(exc)


def veille_du_miroir(
    admissions: Iterable[Admission], entrees: Iterable[EntreeAmont]
) -> tuple[SignalAmont, ...]:
    """Ce que l'amont dit **aujourd'hui** des entrées admises **hier** (#678, critère 3).

    Le seul endroit du chantier où les deux moitiés sont tenues ensemble : la
    règle vit dans `mcp_admission.veiller`, la **table** (nom amont → version,
    statut) se construit ici, parce que `mcp_admission` ne connaît pas le miroir
    et n'a pas à le connaître pour dire ce qu'est un écart.
    """
    amont = {
        entree.nom: EtatAmontEntree(version=entree.version, statut=entree.statut)
        for entree in entrees
    }
    return veiller(admissions, amont)


def federer(
    miroir: MiroirAmont | None = None,
    *,
    entrees_curees: Iterable[EntreeRegistre] = SEED,
    provenance: Provenance = PROVENANCE,
    traducteur: Traducteur | None = None,
    magasin: MagasinAdmissions | None = None,
) -> Federation:
    """La bibliothèque à trois sources, montée depuis les fichiers déjà sur le disque.

    `miroir` par défaut est celui que la configuration désigne
    (`MiroirAmont.default()` → `MAESTRO_MCP_AMONT_*`, sinon `core/mcp-amont/`),
    `magasin` le journal des admissions (`MAESTRO_MCP_DIR`, sinon `core/mcp/`).
    **Ne moissonne pas** et **ne lève jamais** : sans miroir lisible, sans
    entrées, ou sans le module de traduction, elle rend la bibliothèque curée
    avec la cause en clair.

    ⚠ Les admissions sont lues **même quand le miroir est absent**, et c'est
    voulu : elles sont une allowlist locale, pas une vue du miroir. Un poste qui
    n'a jamais moissonné doit continuer à monter ce qu'il a admis — ce que
    l'entrée figée rend possible sans rien redemander à l'amont (#678, règle 1).
    La **veille**, elle, ne joue que s'il y a un miroir : sans lui, il n'y a rien
    à confronter, et « disparue de l'amont » serait faux pour tout le monde.

    Le résultat est mémoïsé par `federer_memo`, qui est ce que la Control Tower
    appelle ; cette fonction-ci refait le travail à chaque appel et reste la voie
    des tests, qui veulent un résultat déterminé et non un cache.
    """
    admissions, cause_admissions = lire_admissions(magasin)
    actives = tuple(a for a in admissions if a.active)
    revoquees = tuple(a for a in admissions if not a.active)

    try:
        miroir = miroir if miroir is not None else MiroirAmont.default()
        entrees = miroir.entrees()
        etat = miroir.etat
    except Exception as exc:  # noqa: BLE001 - un miroir illisible ne coûte pas la bibliothèque
        _LOG.warning("miroir MCP illisible, bibliothèque curée seule : %s", exc)
        return Federation(
            registre=RegistreMcp(entrees_curees, provenance, admissions=admissions),
            cause=f"miroir illisible : {exc}",
            admises=len(actives),
            revoquees=len(revoquees),
            cause_admissions=cause_admissions,
        )

    traducteur = traducteur or _traducteur()
    cause = "" if traducteur is not None else CAUSE_SANS_TRADUCTION
    decouvertes, traduites, refusees, motifs = traduire_miroir(entrees, traducteur)
    registre = RegistreMcp(
        entrees_curees,
        provenance,
        decouvertes=decouvertes,
        admissions=admissions,
        signaux=veille_du_miroir(actives, entrees),
        provenance_decouverte=ProvenanceDecouverte(
            amont=etat.amont or miroir.amont,
            rafraichi_le=etat.rafraichi_le,
            moissonne_le=etat.moissonne_le,
            nombre=etat.nombre or len(entrees),
            cause=etat.cause,
            echoue_le=etat.echoue_le,
        ),
    )
    return Federation(
        registre=registre,
        lues=len(entrees),
        traduites=traduites,
        refusees=refusees,
        motifs=motifs,
        cause=cause,
        admises=len(actives),
        revoquees=len(revoquees),
        cause_admissions=cause_admissions,
    )


#: Le type d'une empreinte de fichier : chemin, mtime en nanosecondes, taille —
#: `(-1, -1)` quand il n'existe pas, ce qui est un état comme un autre.
Empreinte = tuple[str, int, int]

#: La mémoire de `federer_memo` : `(empreintes des deux fichiers) → Federation`.
#: Portée du processus, et **une seule entrée** — la Control Tower n'a qu'un
#: miroir et qu'un journal.
_MEMO: tuple[tuple[Empreinte, Empreinte], Federation] | None = None


def _empreinte(chemin: Path) -> Empreinte:
    """L'empreinte d'un fichier : chemin, mtime, taille — `(-1, -1)` s'il manque."""
    try:
        marque = chemin.stat()
    except OSError:
        return (str(chemin), -1, -1)
    return (str(chemin), marque.st_mtime_ns, marque.st_size)


def federer_memo(
    miroir: MiroirAmont | None = None, magasin: MagasinAdmissions | None = None
) -> Federation:
    """`federer`, mémoïsé sur les **empreintes** de ses deux fichiers sur disque.

    Ce que la Control Tower appelle. L'empreinte est celle du fichier (chemin,
    mtime, taille), la même que `MiroirAmont.entrees()` utilise : une écriture
    change les deux, donc la fédération se refait **à l'instant** où l'une des
    sources bouge, et jamais entre deux. Une durée d'expiration aurait servi une
    bibliothèque périmée pendant sa fenêtre, et une fédération refaite par
    requête coûterait la traduction de 25 000 entrées à chaque frappe de l'écran.

    ⚠ **Deux fichiers et non un seul** depuis #678 : le journal des admissions
    change à un rythme humain — un clic — quand le miroir change à l'heure. Sur
    la seule empreinte du miroir, une entrée admise n'apparaîtrait qu'au
    prochain rafraîchissement, c'est-à-dire jusqu'à une heure après le geste qui
    l'a admise. La route qui écrit appelle en outre `oublier_memo()` : la
    granularité d'un mtime dépend du système de fichiers, et une porte
    d'admission ne se paie pas le luxe d'une fenêtre où elle mentirait.

    Sans fichier lisible, l'empreinte vaut `(chemin, -1, -1)` : la mémoire tient
    donc aussi pour un miroir absent, qui est l'état normal d'un poste neuf.
    """
    global _MEMO
    miroir = miroir if miroir is not None else MiroirAmont.default()
    magasin = magasin if magasin is not None else MagasinAdmissions.default()
    empreintes = (
        _empreinte(miroir.racine / MiroirAmont.FICHIER_ENTREES),
        _empreinte(magasin.chemin),
    )
    if _MEMO is not None and _MEMO[0] == empreintes:
        return _MEMO[1]
    federation = federer(miroir, magasin=magasin)
    _MEMO = (empreintes, federation)
    return federation


def oublier_memo() -> None:
    """Vide la mémoire de `federer_memo`.

    Deux appelants, et le second n'est pas un test : les tests, qui montent deux
    miroirs successifs au même chemin et peuvent les écrire dans la même
    nanoseconde ; et les **routes d'admission** (#678), qui viennent d'écrire le
    journal et doivent servir la bibliothèque neuve à la requête suivante, sans
    dépendre de la granularité du mtime du système de fichiers.

    Ailleurs en production, la mémoire tombe d'elle-même sur les empreintes.
    """
    global _MEMO
    _MEMO = None
