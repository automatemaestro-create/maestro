"""L'outillage se **corrige en langage naturel** (#1161).

« Nos tests tournent avec `dotnet test` », « on utilise pnpm, pas npm » : la personne dit
ce qui est faux avec ses mots, dans la conversation où l'outillage se construit. Le modèle
comprend la phrase (son appel vit côté Control Tower, `maestro.controltower.pieces`, là
où le registre de langue s'applique) ; ce module tient les deux moitiés qui ne demandent
ni réseau ni modèle, donc se testent sur du texte :

- **lire ce que le modèle a compris** (`lire_correction`) — sans rien croire sans le
  vérifier : un sujet hors du schéma de l'outillage (`questionnaire.SUJETS`) est écarté,
  une valeur tient sur une ligne et elle est bornée ;
- **appliquer la correction aux constats** (`corriger`) — la matière que `recommander`
  lit, celle d'une analyse (#1158) comme celle d'un questionnaire (#1147). Ce qui en sort
  passe ensuite par le même chemin que tout outillage : recommandé, rédigé, **joué avant
  d'être écrit** (#1160), montré avec son diff, écrit sur accord.

## La phrase est la justification, et c'est le code qui l'écrit

Une commande corrigée a pour provenance `ORIGINE_DITE`, et pour extrait **la phrase de la
personne**, entre guillemets, telle qu'elle l'a tapée. Ce n'est pas le modèle qui la
recopie : il rend la clé et la valeur qu'il a comprises, le code pose la phrase. Une
justification réécrite par le modèle serait une paraphrase — et c'est précisément ce que
`AGENTS.md` ne doit jamais présenter comme les mots de quelqu'un.

## Trois issues, et aucune n'écrit

- **comprise, avec des corrections** — les constats changent, les pièces qu'elles
  touchent sont reproposées ;
- **comprise, sans correction** — la personne a parlé de l'outillage sans rien y changer
  (« continue », « outille ce projet ») : on reprend où l'on en était ;
- **incomprise** — le modèle ne sait pas traduire la phrase en sujets de l'outillage : il
  le dit, et rien ne change. Ce qu'on ne comprend pas ne s'écrit jamais dans le projet.

## Ce qui se corrige, et ce qui ne se corrige pas ici

`corriger` porte sur ce qu'un outillage **écrit** à partir des constats : la commande de
chaque usage, le gestionnaire, la forge, l'intégration continue (`CLES_CORRIGEABLES`). La
sorte de projet, le langage ou le manifeste d'un projet **lu** ne se corrigent pas ici :
la part d'un langage est une mesure, et la remplacer par ce qu'on nous dit la
falsifierait. Sur un projet neuf, ces sujets-là passent par le questionnaire, dont ils
sont des réponses comme les autres. Une correction qui ne touche aucun sujet corrigeable
se dit « comprise, sans effet sur l'outillage » — elle n'est ni perdue ni appliquée en
silence.

## Une correction prise reste acquise au projet (#1334)

Une correction ne vit pas que dans la conversation où elle a été dite : dès qu'une pièce
s'écrit, le manifeste la garde (`CorrectionPrise`, docs/38 §4.1) — le sujet, la valeur et
la phrase —, et l'outillage rouvert dans une autre conversation la rejoue par `corriger`
avant tout. Sans elle, il se redérivait de l'analyse et proposait de **remplacer** la
commande dite par celle que le projet déclare. Entre une correction du manifeste et une
du fil, **la plus récente l'emporte** (`retenir`) : une conversation plus ancienne,
reprise, ne défait pas ce qu'une plus récente a écrit.

## Ce que Maestro propose n'est pas ce qu'on lui a dit (#1381)

Quand une commande de l'outillage échoue à la revue d'après un run, Maestro lit le projet
construit et propose la commande de même usage qu'il y montre, jouée avant d'être
montrée (`maestro.controltower.pieces`). Écrite, elle est retenue comme une correction —
même manifeste, même `retenir` —, mais elle n'est **pas dite** : son origine est
`ORIGINE_PROPOSEE`, sa justification le fichier où Maestro l'a lue, et c'est `adopter`,
non `corriger`, qui la pose dans les constats.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from maestro.outillage.modele import (
    ORIGINE_DITE,
    ORIGINE_PROPOSEE,
    USAGES,
    Commande,
    Constats,
    Forge,
    Gestionnaire,
    Piece,
)
from maestro.outillage.questionnaire import (
    POUR_MAX,
    SUJETS,
    VALEUR_MAX,
    Choix,
    _cle,
    _est_aucun,
    _objet_json,
    _une_ligne,
)

#: Les sujets qu'une correction change **dans les constats** d'un projet, lu ou décrit.
#: Ce sont ceux que la rédaction écrit : une commande par usage, le gestionnaire, la
#: forge, l'intégration continue (voir le module pour ce qui n'y est pas).
CLES_CORRIGEABLES: frozenset[str] = frozenset({*USAGES, "gestionnaire", "forge", "ci"})

#: La longueur au-delà de laquelle la phrase d'une correction est tronquée **dans la
#: justification** : elle finit écrite à côté d'une commande, dans `AGENTS.md` et dans
#: un `SKILL.md`. La phrase entière, elle, reste dans le fil.
PHRASE_MAX = VALEUR_MAX


@dataclass(frozen=True)
class CorrectionLue:
    """Ce que le modèle a compris d'une correction, **lu et vérifié**.

    `comprise` dit si la phrase a pu être traduite en sujets de l'outillage ;
    `corrections` porte ce qu'elle change, chaque sujet en `Choix` **déduit** dont la
    cause (`parce_que`) est la phrase de la personne ; `message` est ce que le modèle a à
    lui dire — ce qu'il n'a pas compris, ou ce qu'il retient.
    """

    comprise: bool
    corrections: tuple[Choix, ...] = ()
    message: str = ""


@dataclass(frozen=True)
class CorrectionPrise:
    """Une correction **prise**, telle que le projet la garde d'une conversation à l'autre (#1334).

    `cle` et `valeur` sont ce que `corriger` applique, `phrase` la phrase de la personne
    — la justification qui s'écrit à côté de la commande, et que la carte redit. Le
    manifeste la porte dès qu'une pièce s'écrit (docs/38 §4.1). `prise_le` dit quand
    Maestro l'a prise (ISO 8601 UTC, la précision du fil) : c'est lui qui départage une
    correction du manifeste et une du fil (`retenir`).

    `origine` (#1381) dit **qui** l'a apportée. `ORIGINE_DITE`, le défaut : la personne,
    avec ses mots. `ORIGINE_PROPOSEE` : Maestro, à la revue d'après un run — la commande
    écrite y avait échoué, il a lu celle que le projet construit montre et l'a jouée
    avant de la proposer. `chemin` est alors le fichier où il l'a lue, et `phrase` ce
    qu'il y a lu : une commande proposée n'a pas de phrase de la personne, et la carte ne
    lui en prête pas. Une fois écrite, elle reste acquise au projet exactement comme une
    correction dite, et se corrige encore avec des mots.
    """

    cle: str
    valeur: str
    phrase: str = ""
    prise_le: str = ""
    origine: str = ORIGINE_DITE
    chemin: str = ""
    pour: str = ""

    @property
    def proposee(self) -> bool:
        """Maestro l'a-t-il proposée, lue dans le projet construit, plutôt que la personne dite ?"""
        return self.origine == ORIGINE_PROPOSEE

    @classmethod
    def de(cls, choisi: Choix, prise_le: str) -> CorrectionPrise:
        """La correction `choisi`, prise à `prise_le` — sa cause est la phrase dite."""
        return cls(cle=choisi.cle, valeur=choisi.valeur, phrase=choisi.parce_que, prise_le=prise_le)

    @classmethod
    def lue(cls, commande: Commande, prise_le: str) -> CorrectionPrise:
        """`commande`, lue dans le projet construit et **proposée** par Maestro (#1381)."""
        return cls(
            cle=commande.usage,
            valeur=commande.commande,
            phrase=commande.extrait,
            prise_le=prise_le,
            origine=ORIGINE_PROPOSEE,
            chemin=commande.chemin,
            pour=commande.pour,
        )

    def en_choix(self) -> Choix:
        """La forme que `corriger` lit — un `Choix` déduit dont la cause est la phrase."""
        return Choix(cle=self.cle, valeur=self.valeur, deduit=True, parce_que=self.phrase)

    def to_dict(self) -> dict[str, str]:
        """La correction en JSON — la forme du manifeste et de la pièce qui la porte.

        `origine` et `chemin` n'y sont que pour une commande **proposée** : une correction
        dite s'écrit comme avant #1381, et un manifeste d'avant se relit à l'identique.
        """
        forme = {
            "cle": self.cle,
            "valeur": self.valeur,
            "phrase": self.phrase,
            "prise_le": self.prise_le,
        }
        if self.proposee:
            forme.update(origine=self.origine, chemin=self.chemin)
        if self.pour:
            forme["pour"] = self.pour
        return forme

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> CorrectionPrise:
        """Relit une correction persistée, sans la rejuger (la règle de `Choix.from_dict`).

        Une origine inconnue se relit « dite » : c'est ce qu'était toute correction avant
        #1381, et la seule qui ne prétend rien de plus qu'une phrase gardée.
        """
        origine = str(data.get("origine") or "")
        return cls(
            cle=str(data.get("cle") or ""),
            valeur=str(data.get("valeur") or ""),
            phrase=str(data.get("phrase") or ""),
            prise_le=str(data.get("prise_le") or ""),
            origine=ORIGINE_PROPOSEE if origine == ORIGINE_PROPOSEE else ORIGINE_DITE,
            chemin=str(data.get("chemin") or ""),
            pour=str(data.get("pour") or ""),
        )


def corrections_lues(brutes: Any) -> tuple[CorrectionPrise, ...]:
    """Les corrections d'un manifeste, **lues sans rien croire** — `()` sur tout le reste.

    Le manifeste vit dans le projet, et n'importe qui peut l'avoir touché : une entrée
    hors des sujets corrigeables, ou sans valeur, est écartée ; une valeur et une phrase
    tiennent sur une ligne, bornées comme celles que le modèle rend (`lire_correction`).
    Ce qui passe n'est qu'une correction comme une autre : sa commande est **jouée**
    avant qu'une pièce ne se montre, comme toute commande corrigée. Une commande
    **proposée** (#1381) ne vaut que pour un usage : Maestro ne propose que des commandes.
    """
    lues: list[CorrectionPrise] = []
    for entree in brutes if isinstance(brutes, list) else ():
        if not isinstance(entree, Mapping):
            continue
        prise = CorrectionPrise.from_dict(entree)
        valeur = _une_ligne(prise.valeur, VALEUR_MAX)
        if prise.cle not in CLES_CORRIGEABLES or not valeur:
            continue
        if prise.proposee and prise.cle not in USAGES:
            continue
        lues.append(
            replace(
                prise,
                valeur=valeur,
                phrase=_une_ligne(prise.phrase, PHRASE_MAX),
                chemin=_une_ligne(prise.chemin, VALEUR_MAX),
                pour=_une_ligne(prise.pour, POUR_MAX),
            )
        )
    return tuple(lues)


def retenir(*groupes: Sequence[CorrectionPrise]) -> tuple[CorrectionPrise, ...]:
    """La correction **la plus récente** de chaque sujet, de la plus ancienne à la plus récente.

    `prise_le` ordonne. À date égale — la même seconde —, l'ordre des groupes départage :
    on les donne du plus ancien au plus récent (le manifeste, puis le fil, puis la
    correction qu'on vient de comprendre). Une correction sans date est plus ancienne que
    toute autre. C'est ce qui fait qu'une conversation reprise ne défait pas ce qu'une
    plus récente a écrit, et qu'une correction plus récente du fil l'emporte sur le
    manifeste.
    """
    ordonnees = sorted((c for groupe in groupes for c in groupe), key=lambda c: c.prise_le)
    dernieres: dict[str, CorrectionPrise] = {}
    for prise in ordonnees:
        # Retirée puis remise : l'ordre rendu est celui de la dernière prise de chaque sujet.
        dernieres.pop(prise.cle, None)
        dernieres[prise.cle] = prise
    return tuple(dernieres.values())


def lire_correction(texte: str, phrase: str) -> CorrectionLue:
    """Ce que le modèle a compris de `phrase`, lu dans `texte` — la frontière avec le fil.

    Le contrat du modèle : un objet JSON `{"comprise": bool, "corrections": [{"cle",
    "valeur"}], "message": str}`. Une correction sur un sujet **hors du schéma** est
    écartée ; la dernière d'un même sujet l'emporte. `comprise: false` rend une lecture
    **sans** correction, quoi que le modèle ait mis à côté : ce qu'il dit ne pas avoir
    compris, il ne l'a pas compris à moitié.

    La **phrase** est posée ici, par le code, comme cause de chaque correction : elle
    est la justification que le ticket demande, jamais une paraphrase du modèle.

    Lève `ComprehensionIllisible` (de `questionnaire`) sur un texte sans objet JSON.
    """
    objet = _objet_json(texte)
    message = _une_ligne(objet.get("message") or "", 2 * VALEUR_MAX)
    if objet.get("comprise") is False:
        return CorrectionLue(comprise=False, message=message)
    cause = _une_ligne(phrase, PHRASE_MAX)
    lues: dict[str, Choix] = {}
    brutes = objet.get("corrections")
    for entree in brutes if isinstance(brutes, list) else ():
        if not isinstance(entree, dict):
            continue
        cle = _cle(entree.get("cle"))
        valeur = _une_ligne(entree.get("valeur") or "", VALEUR_MAX)
        if cle not in SUJETS or not valeur:
            continue
        lues[cle] = Choix(cle=cle, valeur=valeur, deduit=True, parce_que=cause)
    return CorrectionLue(comprise=True, corrections=tuple(lues.values()), message=message)


def corrections_en_texte(corrections: Sequence[CorrectionPrise]) -> str:
    """Les corrections déjà prises, telles que le modèle les relit — une par ligne.

    Chacune dit qui l'a apportée : une commande que Maestro a **proposée** (#1381) n'a pas
    été dite, et le modèle qui comprend la phrase suivante ne doit pas la prêter à la
    personne.
    """
    lignes = [
        f"- {SUJETS.get(c.cle, c.cle)} ({c.cle}) : « {c.valeur} » — "
        + (
            f"proposée par Maestro, lue dans {c.chemin or 'le projet construit'}"
            if c.proposee
            else f"dit : « {c.phrase} »"
        )
        for c in corrections
    ]
    return "\n".join(lignes) if lignes else "(aucune correction pour l'instant)"


def constats_en_texte(constats: Constats) -> str:
    """Ce que les constats disent des sujets corrigeables — ce que le modèle corrige.

    Seuls les sujets qu'une correction peut changer y figurent, avec leur valeur et d'où
    elle vient : le modèle ne peut corriger que ce qu'il voit, et lui montrer une part de
    langage l'inviterait à la « corriger ».
    """
    lignes: list[str] = []
    for usage in USAGES:
        commande = constats.commande_de(usage)
        if commande is not None:
            lignes.append(f'- clé "{usage}" ({SUJETS[usage]}) : {commande.commande}')
    if constats.gestionnaires:
        noms = ", ".join(g.nom for g in constats.gestionnaires)
        lignes.append(f'- clé "gestionnaire" ({SUJETS["gestionnaire"]}) : {noms}')
    if constats.forge is not None:
        lignes.append(f'- clé "forge" ({SUJETS["forge"]}) : {constats.forge.nom}')
    if constats.ci:
        chemins = ", ".join(piece.chemin for piece in constats.ci)
        lignes.append(f'- clé "ci" ({SUJETS["ci"]}) : {chemins}')
    return "\n".join(lignes) if lignes else "(rien n'est encore établi)"


def corriger(constats: Constats, corrections: Sequence[Choix]) -> Constats:
    """Les `constats`, corrigés par ce que la personne a dit — la dernière correction l'emporte.

    Une commande corrigée **remplace** celle de son usage, quelle qu'en soit la
    provenance — déclarée par le projet, conventionnelle, ou dite plus tôt : c'est la
    personne qui sait comment son projet se teste. Elle garde l'endroit de la commande
    qu'elle remplace (le fichier où vivent les commandes du projet), et prend pour
    extrait la phrase, entre guillemets (`ORIGINE_DITE`). « aucun » retire la commande
    de l'usage : « pas de tests pour l'instant » est une correction comme une autre, et
    `recommander` écartera le skill avec sa raison.

    Les sujets hors de `CLES_CORRIGEABLES` sont ignorés ici (voir le module).
    """
    derniers: dict[str, Choix] = {}
    for choisi in corrections:
        if choisi.cle in CLES_CORRIGEABLES and choisi.valeur:
            derniers[choisi.cle] = choisi
    if not derniers:
        return constats
    commandes = _commandes_corrigees(constats, derniers)
    return replace(
        constats,
        commandes=commandes,
        gestionnaires=_gestionnaires_corriges(constats, derniers, commandes),
        forge=_forge_corrigee(constats, derniers),
        ci=_ci_corrigee(constats, derniers),
    )


def _extrait(choisi: Choix) -> str:
    """La phrase de la personne, entre guillemets — la justification écrite."""
    return f"« {choisi.parce_que} »" if choisi.parce_que else ""


def _commandes_corrigees(constats: Constats, derniers: dict[str, Choix]) -> tuple[Commande, ...]:
    """Chaque usage corrigé ne garde qu'une commande : celle qui a été dite."""
    commandes: list[Commande] = []
    poses: set[str] = set()
    for commande in constats.commandes:
        choisi = derniers.get(commande.usage)
        if choisi is None:
            commandes.append(commande)
            continue
        if commande.usage in poses:
            continue
        poses.add(commande.usage)
        if not _est_aucun(choisi.valeur):
            commandes.append(_dite(commande.usage, choisi, commande.chemin, commande.pour))
    for usage in USAGES:
        choisi = derniers.get(usage)
        if choisi is not None and usage not in poses and not _est_aucun(choisi.valeur):
            commandes.append(_dite(usage, choisi, _chemin_des_commandes(constats)))
    return tuple(commandes)


def _dite(usage: str, choisi: Choix, chemin: str, pour: str = "") -> Commande:
    """La commande dite pour `usage`, justifiée par la phrase.

    Elle garde ce que la commande qu'elle remplace faisait **pour le projet** (`pour`,
    #1350) : « nos tests tournent avec pytest » change la commande, pas ce que les tests
    vérifient — et la perdre rendrait au skill une description qui vaut pour tout projet.
    """
    return Commande(
        usage=usage,
        commande=_une_ligne(choisi.valeur, VALEUR_MAX),
        chemin=chemin,
        extrait=_extrait(choisi),
        origine=ORIGINE_DITE,
        pour=pour,
    )


def adopter(constats: Constats, proposees: Sequence[CorrectionPrise]) -> Constats:
    """Les `constats`, chaque commande **proposée par Maestro** à la place de la sienne (#1381).

    La jumelle de `corriger` pour ce que personne n'a dit : la commande que la revue
    d'après un run a lue dans le projet construit, jouée, puis montrée. Elle prend
    `ORIGINE_PROPOSEE`, le fichier où Maestro l'a lue pour chemin et ce qu'il y a lu pour
    extrait — c'est ce que la rédaction écrit à côté d'elle, jamais « dite par la
    personne ». Ce qu'elle fait **pour le projet** (`pour`, #1350) est ce que le modèle
    en a dit en la lisant, et à défaut ce que faisait la commande qu'elle remplace (voir
    `_proposee`). Les corrections qui ne sont pas proposées sont ignorées ici : c'est
    `corriger` qui les applique.
    """
    par_usage = {p.cle: p for p in proposees if p.proposee and p.cle in USAGES and p.valeur}
    if not par_usage:
        return constats
    commandes: list[Commande] = []
    poses: set[str] = set()
    for commande in constats.commandes:
        prise = par_usage.get(commande.usage)
        if prise is None:
            commandes.append(commande)
        elif commande.usage not in poses:
            poses.add(commande.usage)
            commandes.append(_proposee(prise, commande.pour))
    commandes.extend(
        _proposee(par_usage[usage]) for usage in USAGES if usage in par_usage and usage not in poses
    )
    return replace(constats, commandes=tuple(commandes))


def _proposee(prise: CorrectionPrise, pour_remplacee: str = "") -> Commande:
    """La commande que Maestro propose pour `prise.cle`, justifiée par le fichier où il l'a lue.

    Ce qu'elle fait pour le projet est ce que le modèle en a dit en la lisant (`pour`) :
    la commande remplacée pouvait nommer un script qui n'existe pas — vu sur S9, « …
    assembler.py compile… » à côté de `assembler_carnet.py`. Sans phrase du modèle, elle
    garde celle de la commande qu'elle remplace, comme une commande dite.
    """
    return Commande(
        usage=prise.cle,
        commande=_une_ligne(prise.valeur, VALEUR_MAX),
        chemin=prise.chemin,
        extrait=prise.phrase,
        origine=ORIGINE_PROPOSEE,
        pour=prise.pour or pour_remplacee,
    )


def _chemin_des_commandes(constats: Constats) -> str:
    """Où vivent les commandes du projet — le fichier de son gestionnaire, s'il en a un."""
    if constats.gestionnaires:
        return constats.gestionnaires[0].chemin
    return constats.commandes[0].chemin if constats.commandes else ""


def _gestionnaires_corriges(
    constats: Constats, derniers: dict[str, Choix], commandes: Sequence[Commande]
) -> tuple[Gestionnaire, ...]:
    """Le gestionnaire dit remplace ceux constatés ; son installation suit la commande dite."""
    choisi = derniers.get("gestionnaire")
    if choisi is None:
        return constats.gestionnaires
    if _est_aucun(choisi.valeur):
        return ()
    ancien = constats.gestionnaires[0] if constats.gestionnaires else None
    installer = next((c.commande for c in commandes if c.usage == "installer"), None)
    return (
        Gestionnaire(
            nom=_une_ligne(choisi.valeur, VALEUR_MAX),
            chemin=ancien.chemin if ancien is not None else _chemin_des_commandes(constats),
            # Le verrou d'un autre gestionnaire ne dit rien de celui-ci : on ne le garde pas.
            verrou=None,
            installer=installer,
        ),
    )


def _forge_corrigee(constats: Constats, derniers: dict[str, Choix]) -> Forge | None:
    """La forge dite, ou aucune."""
    choisi = derniers.get("forge")
    if choisi is None:
        return constats.forge
    if _est_aucun(choisi.valeur):
        return None
    return Forge(nom=_une_ligne(choisi.valeur, VALEUR_MAX))


def _ci_corrigee(constats: Constats, derniers: dict[str, Choix]) -> tuple[Piece, ...]:
    """L'intégration continue dite — un chemin de fichier —, ou aucune."""
    choisi = derniers.get("ci")
    if choisi is None:
        return constats.ci
    if _est_aucun(choisi.valeur):
        return ()
    chemin = _une_ligne(choisi.valeur, VALEUR_MAX)
    return (
        Piece(
            nom=chemin.rsplit("/", 1)[-1],
            chemin=chemin,
            role=f"intégration continue — dite par la personne ({_extrait(choisi)})",
        ),
    )


def a_effet(corrections: Sequence[Choix]) -> bool:
    """Au moins une correction porte-t-elle sur un sujet que `corriger` change ?"""
    return any(c.cle in CLES_CORRIGEABLES for c in corrections)


def cles_de(corrections: Sequence[Choix]) -> tuple[str, ...]:
    """Les sujets corrigés, dans l'ordre — pour dire ce qui a changé."""
    return tuple(dict.fromkeys(c.cle for c in corrections))
