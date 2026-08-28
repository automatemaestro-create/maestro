"""La **porte d'admission** de la bibliothèque MCP (#678, lot 4/6 du parent #673).

Le lot 3 a donné deux sources à la bibliothèque : les entrées curées, écrites à
la main et instanciables, et les entrées découvertes dans le miroir du registre
officiel — visibles, cherchables, **jamais montables**. Ce module est ce qui
manquait entre les deux : le **geste humain tracé** qui promeut une découverte en
entrée de l'allowlist.

C'est le lot qui tient la promesse du parent — *fédérer la découverte sans
fédérer l'installation*. Le garde-fou de
[docs/19](../../docs/19-securite-modele-de-menace.md) n'est pas levé, il devient
**exact** : jusqu'ici l'allowlist portait deux rôles (« ce qu'on connaît » et
« ce qu'on autorise »), elle n'en garde qu'un.

## Ce que le registre officiel prouve, et ce qu'il ne prouve pas

Il vérifie la **propriété du namespace** de l'éditeur (`io.github.<user>` par
OAuth GitHub, `com.exemple` par preuve DNS/HTTP) et rien de plus : aucun scan de
sécurité, aucune caution. Il dit « ce serveur existe », **jamais** « ce serveur
est sûr ». La seconde question reste la nôtre, et c'est cette porte qui l'écrit.

Ce qu'il change, en revanche, c'est la **qualité de la matière**. La règle de
curation de #271 interdit d'écrire un `npx -y <paquet>` **de mémoire**, parce que
c'est une invitation au typosquatting dans une allowlist. Un identifiant lu dans
un enregistrement d'éditeur au namespace vérifié, à version épinglée, n'est pas
de la mémoire — le motif de la règle **cesse de s'appliquer** sans que la règle
s'affaiblisse. L'admission fait entrer cette matière-là, et elle en garde la
source (`Admission.nom_amont`/`version`/`editeur`/`depot`/`amont`/`miroir_le`)
pour qu'on puisse toujours revenir la vérifier.

## Quatre décisions, et chacune répond à un critère du ticket

1. **L'admission fige l'entrée traduite** (`Admission.entree`). Ce que la
   bibliothèque sert d'une admise ne vient pas du miroir d'aujourd'hui mais de
   l'enregistrement d'hier. Sans ce figement, une nouvelle version amont
   changerait ce qu'on monte sans que personne l'ait admis — l'admission
   autoriserait une version et en monterait une autre, c'est-à-dire exactement
   le trou qu'elle est censée fermer. Promouvoir une nouvelle version demande un
   **nouveau geste** (`admettre` à nouveau), et le journal en garde la trace.

2. **Rien ne disparaît en silence** (`veiller`). Une admise dont l'amont passe
   `deprecated`, `deleted`, ou qui sort du miroir, reste servie **avec son
   signal**. Retirer d'office casserait un serveur monté sans le dire ; et la
   décision — garder, révoquer, mettre à jour — appartient à qui a admis, pas à
   une boucle de fond. C'est la même asymétrie que partout dans ce dépôt : ce
   qui est automatique est la **détection**, jamais le verdict.

3. **Une révocation ne s'oublie pas.** L'admission révoquée reste dans le
   journal et le registre la garde de côté, pour que le refus d'instanciation
   puisse **nommer** ce qui s'est passé. Et elle **ne démonte rien** : un
   serveur déjà dans le pool projet y reste, avec son alerte — casser un run en
   cours pour appliquer une décision d'allowlist serait un remède pire que le
   mal. Ce qui est promis est « jamais sans le dire », pas « jamais sans casser ».

4. **La politique d'entreprise se branche ici, et nulle part ailleurs**
   (`PolitiqueAdmission`). L'admission est le point où une organisation veut
   glisser sa revue, son scan, son refus par éditeur. Le contrat est un
   **callable** d'une ligne (`Candidature` → `VerdictPolitique`), injectable au
   service ou désigné par `MAESTRO_MCP_ADMISSION_POLITIQUE` (`module:attribut`).
   Le défaut accepte tout : le geste humain **est** la politique par défaut, et
   en inventer une plus stricte ici reviendrait à décider à la place de gens
   qu'on ne connaît pas.

## Ce que ce module ne fait pas

Il ne moissonne pas (c'est `mcp_amont`), ne traduit pas (`mcp_traduction`), et
ne compose pas la bibliothèque (`mcp_federation`, qui l'appelle). Il tient un
**journal** — `admissions.json`, à côté du pool et des activations, sous la même
racine (`MAESTRO_MCP_DIR`, sinon `core/mcp/`) : c'est une **donnée
d'installation**, comme le pool, pas du code relu en revue comme `SEED`.

Tests différés → lot 6 du parent (#680).
"""

from __future__ import annotations

import importlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from maestro.agents.mcp import McpStore
from maestro.agents.mcp_registry import (
    MODES_AUTH,
    SIGNAL_DEPRECIEE,
    SIGNAL_DISPARUE,
    SIGNAL_SUPPRIMEE,
    SIGNAL_VERSION,
    Admission,
    EntreeRegistre,
    SignalAmont,
)
from maestro.config import Settings, load_settings

_LOG = logging.getLogger(__name__)

#: Le fichier du journal, sous la racine des données d'installation MCP — à côté
#: de `pool.json` et `activations.json`, parce que c'est la même famille : ce que
#: *ce projet-ci* a décidé d'installer, par opposition au seed qui décrit ce que
#: Maestro connaît.
FICHIER_ADMISSIONS = "admissions.json"

#: Le statut amont qui interdit l'admission — l'entrée a été retirée par la
#: modération du registre (spam, malware, illégal).
STATUT_SUPPRIME = "deleted"

#: Les motifs de refus d'une admission, dans l'ordre où ils sont éprouvés. Codes
#: stables : c'est sur eux qu'une UI groupe et qu'un test compte, jamais sur les
#: phrases, qui bougent (même règle que les `MOTIFS` de la traduction, #676).
MOTIF_INCONNUE = "inconnue"
MOTIF_DEJA_CUREE = "deja_curee"
MOTIF_SUPPRIMEE = "amont_supprimee"
MOTIF_NON_TRADUISIBLE = "non_traduisible"
MOTIF_POLITIQUE = "politique"
MOTIF_NON_ADMISE = "non_admise"
MOTIF_DEJA_REVOQUEE = "deja_revoquee"
MOTIFS_REFUS: tuple[str, ...] = (
    MOTIF_INCONNUE,
    MOTIF_DEJA_CUREE,
    MOTIF_SUPPRIMEE,
    MOTIF_NON_TRADUISIBLE,
    MOTIF_POLITIQUE,
    MOTIF_NON_ADMISE,
    MOTIF_DEJA_REVOQUEE,
)

#: Qui a admis, quand personne ne l'a dit. L'admission est un geste **tracé** :
#: une trace anonyme reste une trace, mais elle se distingue d'un nom au premier
#: coup d'œil plutôt que de se lire comme un champ oublié.
ADMIS_PAR_DEFAUT = "inconnu"


class RefusAdmission(ValueError):
    """Une admission (ou une révocation) refusée — avec son **motif** et sa cause.

    Une `ValueError` pour que les appelants qui attrapent déjà les erreurs de
    validation du registre n'aient rien à changer ; un `motif` en plus, parce
    qu'une route doit choisir son code HTTP (404 « inconnue » n'est pas 409
    « déjà admise ») sans lire la phrase française.
    """

    def __init__(self, motif: str, cause: str) -> None:
        super().__init__(cause)
        self.motif = motif
        self.cause = cause


@dataclass(frozen=True)
class Candidature:
    """Ce qu'on soumet à la politique d'admission avant d'écrire quoi que ce soit.

    Tout ce dont une politique d'entreprise peut avoir besoin pour trancher, et
    rien de plus : l'entrée traduite (donc le gabarit exact qui serait monté), sa
    source amont, et le geste (qui, avec quelle note). Elle ne porte **aucun
    secret** — une entrée de bibliothèque est un gabarit `${VAR}`.
    """

    entree: EntreeRegistre
    nom_amont: str = ""
    version: str = ""
    editeur: str = ""
    depot: str = ""
    statut: str = ""
    amont: str = ""
    par: str = ""
    note: str = ""


@dataclass(frozen=True)
class VerdictPolitique:
    """Le verdict d'une politique : admise, ou refusée **avec sa cause**.

    Une cause obligatoire sur un refus, et c'est le seul contrat qu'on impose à
    une politique tierce : un refus muet renverrait l'utilisateur devant un mur
    sans lui dire à qui parler.
    """

    admise: bool = True
    cause: str = ""


#: Le contrat du point d'extension : une fonction, pas une classe à hériter — ce
#: qu'on demande à une organisation est de savoir dire oui ou non, pas d'épouser
#: notre hiérarchie de types.
PolitiqueAdmission = Callable[[Candidature], VerdictPolitique]


def politique_ouverte(candidature: Candidature) -> VerdictPolitique:
    """La politique par défaut : **le geste humain suffit**.

    Elle accepte tout ce que le service lui présente — ce qui n'est pas « aucune
    règle » : le service a déjà écarté ce qui n'est pas admissible (entrée
    inconnue, déjà curée, `deleted` chez l'amont, gabarit qui ne se monte pas).
    Ce qui reste est une décision de **confiance**, et personne ici ne sait mieux
    que l'organisation qui l'installe qui elle veut laisser entrer.
    """
    del candidature  # le défaut ne juge rien : c'est l'humain qui a jugé
    return VerdictPolitique(admise=True)


def charger_politique(reference: str) -> PolitiqueAdmission:
    """Résout `module:attribut` en politique appelable — refus **nommé** si impossible.

    Le point d'extension d'entreprise (`MAESTRO_MCP_ADMISSION_POLITIQUE`). Charger
    du code désigné par l'environnement n'élargit rien : qui pose cette variable a
    déjà la main sur le processus. Ce qui compte est qu'un réglage illisible
    **échoue franchement** au lieu de retomber en silence sur la politique
    ouverte — une politique d'entreprise qu'on croit active et qui ne l'est pas
    est pire que pas de politique du tout.
    """
    module_nom, _, attribut = reference.partition(":")
    if not module_nom or not attribut:
        raise ValueError(
            f"politique d'admission MCP invalide : {reference!r} — forme attendue "
            "« module:attribut » (ex. « mon_org.mcp:politique »)."
        )
    try:
        module = importlib.import_module(module_nom)
    except ImportError as exc:
        raise ValueError(
            f"politique d'admission MCP introuvable : module {module_nom!r} non "
            f"importable ({exc})."
        ) from exc
    politique = getattr(module, attribut, None)
    if not callable(politique):
        raise ValueError(
            f"politique d'admission MCP invalide : {reference!r} — "
            f"{attribut!r} n'est pas appelable dans {module_nom!r}."
        )
    verbe: PolitiqueAdmission = politique
    return verbe


@dataclass(frozen=True)
class EtatAmontEntree:
    """Ce que le miroir dit **aujourd'hui** d'une entrée, indexé par son nom amont.

    Le strict nécessaire à la veille : deux champs, pour que ce module n'ait pas
    à connaître `maestro.agents.mcp_amont`. C'est `mcp_federation` — qui tient
    déjà les deux moitiés — qui construit la table.
    """

    version: str = ""
    statut: str = ""


def veiller(
    admissions: Iterable[Admission], amont: Mapping[str, EtatAmontEntree]
) -> tuple[SignalAmont, ...]:
    """Confronte les admissions **actives** au miroir courant (#678, critère 3).

    Rend un `SignalAmont` par écart constaté — jamais une décision. Quatre
    écarts, et aucun ne retire quoi que ce soit : dépréciation, suppression,
    disparition du miroir, version plus récente en amont.

    ⚠ **Un miroir vide ne produit aucun signal**, et c'est le garde-fou de cette
    fonction. Sans lui, un poste qui n'a jamais moissonné — l'état normal d'un
    clone neuf — déclarerait « disparue de l'amont » **toutes** ses admissions à
    la fois : une alerte massive qui ne dirait rien de vrai, et le meilleur moyen
    d'apprendre à ne plus lire les alertes.

    La clé est le **nom amont** (`io.github.alice/serveur`) et non notre id : le
    nom est ce que l'amont garantit stable, notre slug n'en est qu'une dérivée.
    Une admission sans nom amont — journal écrit à la main, ou entrée d'un
    millésime antérieur — est **sautée** plutôt que déclarée disparue.
    """
    if not amont:
        return ()
    signaux: list[SignalAmont] = []
    for admission in admissions:
        if not admission.active or not admission.nom_amont:
            continue
        etat = amont.get(admission.nom_amont)
        if etat is None:
            signaux.append(
                SignalAmont(
                    id=admission.id,
                    genre=SIGNAL_DISPARUE,
                    message=(
                        f"« {admission.nom_amont} » n'est plus dans le miroir du "
                        "registre officiel : retirée par la modération, ou plus "
                        "servie par l'amont. L'entrée admise reste montable telle "
                        "qu'elle a été admise — à vous de décider de la garder ou "
                        "de la révoquer."
                    ),
                )
            )
            continue
        if etat.statut == STATUT_SUPPRIME:
            signaux.append(
                SignalAmont(
                    id=admission.id,
                    genre=SIGNAL_SUPPRIMEE,
                    message=(
                        f"« {admission.nom_amont} » a été **supprimée** chez l'amont "
                        "(modération du registre : spam, malware ou contenu "
                        "illégal). Elle reste montable tant qu'elle est admise — "
                        "c'est le signal le plus grave de cette liste."
                    ),
                    version_amont=etat.version,
                    statut_amont=etat.statut,
                )
            )
        elif etat.statut and etat.statut != "active":
            signaux.append(
                SignalAmont(
                    id=admission.id,
                    genre=SIGNAL_DEPRECIEE,
                    message=(
                        f"« {admission.nom_amont} » est passée « {etat.statut} » chez "
                        "l'amont depuis son admission. Rien n'a été retiré : "
                        "l'entrée reste montable, le signal est là pour qu'on en "
                        "décide."
                    ),
                    version_amont=etat.version,
                    statut_amont=etat.statut,
                )
            )
        if etat.version and admission.version and etat.version != admission.version:
            signaux.append(
                SignalAmont(
                    id=admission.id,
                    genre=SIGNAL_VERSION,
                    message=(
                        f"l'amont publie « {etat.version} », la version admise est "
                        f"« {admission.version} ». La bibliothèque continue de servir "
                        "la version admise : promouvoir la nouvelle est un nouveau "
                        "geste d'admission."
                    ),
                    version_amont=etat.version,
                    statut_amont=etat.statut,
                )
            )
    return tuple(signaux)


def nom_amont_de(entree: EntreeRegistre) -> str:
    """Recompose le nom amont d'une entrée découverte (`<editeur>/<nom>`).

    La traduction découpe le nom amont sur son dernier `/` pour en tirer `nom` et
    `editeur`, « au caractère près, donc rien n'est perdu et rien n'est
    embelli » (`mcp_traduction._entree`). Cette fonction est l'opération inverse,
    et elle vit ici plutôt que d'être recopiée dans le service : c'est la seule
    règle du dépôt qui remonte d'une entrée vers son identité d'amont.

    Le repli est le nom court seul — une entrée sans éditeur n'a pas de
    namespace, ce qui est le cas des serveurs de premier niveau du registre.
    """
    return f"{entree.editeur}/{entree.nom}" if entree.editeur else entree.nom


class MagasinAdmissions:
    """Le **journal** des admissions, sur fichier (`<racine>/admissions.json`).

    Un fichier JSON à côté du pool et des activations, pour la même raison
    qu'eux : c'est une donnée d'installation, écrite par la Control Tower, pas du
    code relu en revue. Écriture **atomique** (tampon puis renommage) et forme
    indentée — un journal d'autorisations se relit à l'œil nu et se met sous
    contrôle de version si l'équipe le veut.

    `lister` **lève** (`ValueError`, cause exacte) sur un fichier illisible ou
    une ligne inexploitable, comme `McpStore.pool` : on ne compose jamais une
    allowlist douteuse. C'est la fédération qui décide d'en faire une cause à
    l'écran plutôt qu'une exception — elle attrape déjà tout, et sa règle est de
    ne jamais coûter la bibliothèque.
    """

    def __init__(self, racine: Path) -> None:
        self._racine = racine

    @classmethod
    def default(cls, settings: Settings | None = None) -> MagasinAdmissions:
        """Le magasin configuré — la **racine du dépôt MCP**, jamais une seconde à tenir.

        `McpStore.default().racine` plutôt qu'une résolution recopiée : « où
        vivent les données d'installation MCP » est déjà une question tranchée
        (`MAESTRO_MCP_DIR`, sinon `core/mcp/`), et deux réponses finiraient par
        diverger le jour où quelqu'un déplace l'une des deux.
        """
        return cls(McpStore.default(settings).racine)

    @property
    def racine(self) -> Path:
        """La racine du journal (le dossier, pas le fichier)."""
        return self._racine

    @property
    def chemin(self) -> Path:
        """Le fichier du journal — ce que `federer_memo` empreinte pour sa mémoire."""
        return self._racine / FICHIER_ADMISSIONS

    def lister(self) -> tuple[Admission, ...]:
        """Toutes les admissions du journal, actives **et** révoquées, dans l'ordre du geste.

        () si le fichier n'existe pas — l'état normal d'un projet qui n'a rien
        admis. Lève `ValueError` (cause exacte) si le fichier est illisible, sa
        forme inattendue, une ligne inexploitable ou deux admissions de même id :
        ce journal décide de ce qui est montable, il ne se lit pas « au mieux ».
        """
        chemin = self.chemin
        if not chemin.is_file():
            return ()
        try:
            brut: object = json.loads(chemin.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"journal des admissions MCP illisible ({chemin}) : {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"journal des admissions MCP illisible ({FICHIER_ADMISSIONS}) : {exc}"
            ) from exc
        if not isinstance(brut, Mapping) or not isinstance(brut.get("admissions"), list):
            raise ValueError(
                f"journal des admissions MCP invalide ({FICHIER_ADMISSIONS}) : objet "
                '{"admissions": [...]} attendu.'
            )
        admissions: list[Admission] = []
        vus: set[str] = set()
        for ligne in brut["admissions"]:
            if not isinstance(ligne, Mapping):
                raise ValueError(
                    f"journal des admissions MCP invalide ({FICHIER_ADMISSIONS}) : "
                    f"une entrée n'est pas un objet ({type(ligne).__name__})."
                )
            admission = Admission.from_dict(ligne)
            if admission.id in vus:
                raise ValueError(
                    f"journal des admissions MCP invalide ({FICHIER_ADMISSIONS}) : "
                    f"deux admissions pour {admission.id!r}."
                )
            vus.add(admission.id)
            admissions.append(admission)
        return tuple(admissions)

    def ecrire(self, admissions: Sequence[Admission]) -> tuple[Admission, ...]:
        """Réécrit le journal (remplacement intégral), atomiquement. Le renvoie.

        Une admission par id — le journal porte l'**état** d'une autorisation, pas
        son historique : ré-admettre remplace, révoquer marque sur place. Un
        historique complet est une autre question (audit), qui se poserait sur un
        autre support et n'a pas à alourdir ce qui décide du montage.
        """
        propres = tuple(admissions)
        doublons = sorted({a.id for a in propres if [x.id for x in propres].count(a.id) > 1})
        if doublons:
            raise ValueError(
                f"journal des admissions MCP invalide : deux admissions pour "
                f"{', '.join(doublons)}."
            )
        self._racine.mkdir(parents=True, exist_ok=True)
        chemin = self.chemin
        temporaire = chemin.parent / f"{chemin.name}.tmp"
        temporaire.write_text(
            json.dumps(
                {"admissions": [a.to_dict() for a in propres]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporaire, chemin)
        return propres


class ServiceAdmission:
    """Les deux verbes de la porte : **admettre** et **révoquer**.

    Le service tient les règles que ni le magasin (qui ne fait que lire et
    écrire) ni la politique (qui juge la confiance) n'ont à porter : ce qui est
    *admissible* — une entrée qui existe, qui n'est pas déjà curée, qui n'est pas
    supprimée chez l'amont, et dont le gabarit se monterait vraiment.

    L'ordre des contrôles est le contenu de la décision : la politique passe
    **en dernier**, sur une candidature dont tout le reste est déjà vrai. Une
    politique d'entreprise doit répondre à « fait-on confiance à ce serveur ? »
    et jamais à « ce serveur existe-t-il ? » — lui poser les deux questions à la
    fois obligerait chaque organisation à réimplémenter les nôtres.
    """

    def __init__(
        self,
        magasin: MagasinAdmissions,
        *,
        politique: PolitiqueAdmission | None = None,
    ) -> None:
        self._magasin = magasin
        self._politique = politique or politique_ouverte

    @classmethod
    def default(cls, settings: Settings | None = None) -> ServiceAdmission:
        """Le service configuré : le journal par défaut, la politique de l'environnement.

        Une politique illisible **lève** ici, au montage de l'application, plutôt
        que de retomber sur la politique ouverte : voir `charger_politique`.
        """
        settings = settings or load_settings()
        reference = (settings.mcp_admission_politique or "").strip()
        politique = charger_politique(reference) if reference else None
        return cls(MagasinAdmissions.default(settings), politique=politique)

    @property
    def magasin(self) -> MagasinAdmissions:
        """Le journal sous-jacent — ce que la fédération lit à chaque composition."""
        return self._magasin

    @property
    def politique(self) -> PolitiqueAdmission:
        """La politique en vigueur — nommée à l'écran, pour qu'on sache qui juge."""
        return self._politique

    def lister(self) -> tuple[Admission, ...]:
        """Le journal entier, actives et révoquées (délégué au magasin)."""
        return self._magasin.lister()

    def admettre(
        self,
        entree: EntreeRegistre,
        *,
        par: str = "",
        note: str = "",
        amont: str = "",
        miroir_le: str = "",
        nom_amont: str = "",
        maintenant: datetime | None = None,
    ) -> Admission:
        """Fait entrer `entree` dans l'allowlist — le geste, tracé et daté (#678).

        `entree` est l'entrée **découverte** telle que la bibliothèque la sert :
        elle porte déjà ses signaux d'amont (`version`, `depot`, `editeur`,
        `statut`), recollés par la fédération. Le reste est le contexte du
        miroir, que seul l'appelant connaît.

        Lève `RefusAdmission` (avec son motif) et **n'écrit rien** si l'entrée est
        déjà curée, supprimée chez l'amont, non montable, ou refusée par la
        politique. Ré-admettre une entrée **déjà admise à la même version** est
        idempotent : l'admission en place est rendue telle quelle, sans écriture
        et sans réécrire qui l'a admise. À une **autre** version, c'est le
        nouveau geste que le critère 3 demande — l'admission est remplacée, et
        c'est la nouvelle version qui devient celle qu'on monte.
        """
        if entree.curee and entree.admission is None:
            raise RefusAdmission(
                MOTIF_DEJA_CUREE,
                f"serveur MCP {entree.id!r} : déjà curé dans le seed, il est montable "
                "sans admission — il n'y a rien à admettre.",
            )
        if entree.statut == STATUT_SUPPRIME:
            raise RefusAdmission(
                MOTIF_SUPPRIMEE,
                f"serveur MCP {entree.id!r} : **supprimé** chez l'amont (modération du "
                "registre : spam, malware ou contenu illégal). Une entrée retirée par "
                "la modération n'entre pas dans une allowlist.",
            )
        if entree.mode_auth not in MODES_AUTH:
            raise RefusAdmission(
                MOTIF_NON_TRADUISIBLE,
                f"serveur MCP {entree.id!r} : mode d'auth {entree.mode_auth!r} inconnu "
                f"(attendu : {', '.join(MODES_AUTH)}) — non admissible.",
            )
        try:
            entree.vers_serveur()
        except ValueError as exc:
            raise RefusAdmission(
                MOTIF_NON_TRADUISIBLE,
                f"serveur MCP {entree.id!r} : son gabarit ne se monterait pas ({exc}). "
                "La porte ne fabrique pas ce que le gabarit ne sait pas exprimer.",
            ) from exc

        journal = {a.id: a for a in self._magasin.lister()}
        en_place = journal.get(entree.id)
        if en_place is not None and en_place.active and en_place.version == entree.version:
            return en_place

        candidature = Candidature(
            entree=entree,
            nom_amont=nom_amont or nom_amont_de(entree),
            version=entree.version,
            editeur=entree.editeur,
            depot=entree.depot,
            statut=entree.statut,
            amont=amont,
            par=par or ADMIS_PAR_DEFAUT,
            note=note,
        )
        verdict = self._politique(candidature)
        if not verdict.admise:
            raise RefusAdmission(
                MOTIF_POLITIQUE,
                f"serveur MCP {entree.id!r} refusé par la politique d'admission : "
                f"{verdict.cause or 'aucune cause donnée'}.",
            )

        admission = Admission(
            id=entree.id,
            # L'entrée est figée **sans** son admission ni ses signaux : ce sont
            # des vues, posées par le registre à chaque composition (#678). Les
            # graver ici ferait vieillir dans le journal un état qui bouge.
            entree=replace(entree, curee=False, admission=None, signaux=()),
            nom_amont=candidature.nom_amont,
            version=entree.version,
            editeur=entree.editeur,
            depot=entree.depot,
            amont=amont,
            miroir_le=miroir_le,
            par=candidature.par,
            le=_iso(maintenant or datetime.now(UTC)),
            note=note,
        )
        self._magasin.ecrire([*(a for a in journal.values() if a.id != admission.id), admission])
        _LOG.info(
            "admission MCP : %s (%s %s) admis par %s",
            admission.id,
            admission.nom_amont or "?",
            admission.version or "sans version",
            admission.par,
        )
        return admission

    def revoquer(
        self,
        id: str,
        *,
        par: str = "",
        motif: str = "",
        maintenant: datetime | None = None,
    ) -> Admission:
        """Retire `id` de l'allowlist — **sans rien démonter** (#678, critère 2).

        L'admission n'est pas effacée : elle est marquée révoquée, avec qui, quand
        et pourquoi, et reste dans le journal. C'est ce qui permet au refus
        d'instanciation de nommer la révocation plutôt que de rendre le « hors
        allowlist » d'un id inconnu.

        Un serveur déjà monté dans le pool projet **y reste** : c'est l'appelant
        (la route `DELETE /api/mcp/admissions/{id}`) qui dit ce qui reste monté
        et comment le retirer. Démonter d'office casserait un run en cours pour
        appliquer une décision d'allowlist — la promesse est « jamais sans le
        dire », pas « jamais sans casser ».

        Lève `RefusAdmission` si l'id n'a jamais été admis (`non_admise`) ou l'est
        déjà (`deja_revoquee`).
        """
        journal = {a.id: a for a in self._magasin.lister()}
        admission = journal.get(id)
        if admission is None:
            raise RefusAdmission(
                MOTIF_NON_ADMISE,
                f"serveur MCP {id!r} : aucune admission à révoquer (une entrée curée "
                "du seed se retire en revue de code, pas ici).",
            )
        if not admission.active:
            raise RefusAdmission(
                MOTIF_DEJA_REVOQUEE,
                f"serveur MCP {id!r} : admission déjà révoquée le "
                f"{admission.revoquee_le} par {admission.revoquee_par or '?'}.",
            )
        revoquee = replace(
            admission,
            revoquee_par=par or ADMIS_PAR_DEFAUT,
            revoquee_le=_iso(maintenant or datetime.now(UTC)),
            motif=motif,
        )
        journal[id] = revoquee
        self._magasin.ecrire(list(journal.values()))
        _LOG.info(
            "révocation MCP : %s révoqué par %s (%s)",
            id,
            revoquee.revoquee_par,
            motif or "-",
        )
        return revoquee


def _iso(instant: datetime) -> str:
    """L'instant en RFC 3339 UTC suffixé `Z` — la forme du miroir et de l'amont."""
    return instant.astimezone(UTC).isoformat().replace("+00:00", "Z")


def etat_politique(politique: PolitiqueAdmission) -> dict[str, Any]:
    """De quoi **nommer** la politique en vigueur à l'écran (`GET /api/mcp/admissions`).

    Une porte dont on ne sait pas qui la garde n'est pas une porte : si une
    organisation a branché sa revue, l'écran doit le dire, et si personne n'a
    rien branché il doit dire ça aussi. `defaut` distingue les deux — un nom de
    fonction seul laisserait croire à une politique maison là où il n'y a que le
    défaut du dépôt.
    """
    return {
        "nom": getattr(politique, "__name__", politique.__class__.__name__),
        "module": getattr(politique, "__module__", ""),
        "defaut": politique is politique_ouverte,
    }
