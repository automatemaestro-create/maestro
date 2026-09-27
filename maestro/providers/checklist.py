"""La checklist d'une tâche, verbe de Maestro (#489, puis #1291).

L'arbitrage de #489 (cf. `maestro.detail_tache`) confie à l'agent la moitié
« complétée et cochée » de la checklist d'une tâche. Restait à savoir **par où**
il la dit, et #489 avait répondu : il la dit déjà, dans l'outil de liste de
travail du CLI Claude (`TodoWrite`), qu'il suffisait de lire au passage.

C'était une dépendance à un **outil interne d'un CLI**, et elle a cassé comme
casse ce genre de dépendance : sans prévenir. Le CLI embarqué par le SDK 0.2.159
ne monte plus `TodoWrite` par défaut, il le remplace par `TaskCreate` et
`TaskUpdate`, et depuis le 2026-09-22 toutes les tâches se soldaient à « 0/N ·
relevé incomplet » (#1291). Lire le nouvel outil aurait remplacé une dépendance
par une autre, que la version suivante aurait cassée pareil. Forcer l'ancien
outil par une variable du CLI, ou épingler le SDK pour le garder, aurait figé la
même dépendance.

La règle est désormais celle de
[docs/44](../../docs/44-decision-maestro-possede-ses-contrats.md) : *ce dont
Maestro a besoin, il le possède*. La checklist est un **verbe de Maestro**,
`tenir_checklist(etapes)`, servi par le serveur MCP in-process `maestro` à côté
de `consigner_decision`, `signaler_blocage`, `demander_arbitrage`,
`ecrire_a_un_pair` et `poser_une_question`. Ce module en porte **le contrat
entier** : le nom, le schéma, ce que l'agent lit, et `servir`, qui fait de
l'appel un relevé. Il n'importe rien d'un fournisseur. Le fournisseur Claude
l'enveloppe dans un `@tool` (`maestro.providers.claude._outil_checklist`), et un
autre fournisseur outillé servirait le même contrat sans en réécrire une ligne.

Trois choses à connaître avant d'y toucher :

- **le canal ne bouge pas**. `servir` appelle `on_etapes` avec l'état complet de
  la liste, et c'est `SuiviChecklist` qui décide de ce qui progresse, exactement
  comme au temps de `TodoWrite`. Seule la **source** a changé ;
- **une entrée invalide est dite, jamais relevée à moitié**. Une liste fautive
  ne coche rien, pas même ses lignes lisibles, et l'agent lit ce qui cloche,
  rang compris. Il rappelle alors l'outil avec la liste complète, ce que le
  contrat lui demande de toute façon ;
- **rien ne tue la tâche**. Ni une entrée invalide ni un canal en panne ne
  rendent une erreur d'outil ou ne lèvent : une erreur inviterait l'agent à
  rejouer le même appel, et une exception tuerait la tâche à l'instant où il
  rend compte de son avancement.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from maestro.detail_tache import ETAPE_A_FAIRE, ETAPE_EN_COURS, ETAPE_FAITE, EtapeTache
from maestro.providers.arbitrage import NOM_SERVEUR

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur. Un
#: verbe à l'infinitif, comme ses voisins : c'est ce que l'agent fait.
NOM_OUTIL = "tenir_checklist"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`),
#: donc la forme sous laquelle une politique de permissions (#110) le désigne. Le
#: serveur est importé plutôt que réécrit : deux littéraux « maestro » feraient
#: deux serveurs le jour où l'un des deux change.
OUTIL_CHECKLIST = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Les clés de l'entrée : la liste, puis, pour chaque étape, son énoncé et son
#: avancement. Ce sont celles d'`EtapeTache.to_dict` : l'agent écrit la forme que
#: le journal transporte, et il n'y a rien à traduire entre les deux.
CLE_ETAPES = "etapes"
CLE_LIBELLE = "libelle"
CLE_ETAT = "etat"

#: Les avancements qu'une étape peut avoir, ceux que l'écran sait rendre (#246).
#: Le contrat est **le nôtre** : il n'y a plus de vocabulaire d'outil tiers à
#: traduire, donc un état hors de ces trois-là est une faute que l'agent corrige,
#: et non une valeur qu'on laisse passer en espérant que le front la ramène.
ETATS: tuple[str, ...] = (ETAPE_A_FAIRE, ETAPE_EN_COURS, ETAPE_FAITE)

_ETATS_EN_MOTS = ", ".join(f"« {etat} »" for etat in ETATS)

#: Ce que l'agent lit pour savoir **quand** appeler l'outil, et comment.
#:
#: Le débit se règle ici, et à l'inverse des recours (`demander_arbitrage`,
#: `signaler_blocage`) : ce verbe est la seule fenêtre sur l'avancement d'une
#: tâche, et le sous-employer laisse la carte à 0/N. D'où « dès que tu as compris
#: la tâche » et « chaque fois qu'une étape change d'état ».
#:
#: Deux phrases servent le décompte final, et elles comptent autant que le
#: reste : la liste **complète** à chaque appel (le canal reçoit un état, pas un
#: delta), et les **mêmes libellés** d'un appel à l'autre — une étape renommée ou
#: omise reste affichée avec son dernier état (`SuiviChecklist` : rien ne
#: recule), donc elle ferait un écart que l'agent n'a pas voulu.
DESCRIPTION_OUTIL = (
    "Tiens ici la checklist de ta tâche : ses étapes, et où tu en es. Pose-la dès que tu "
    "as compris la tâche, puis rappelle cet outil chaque fois qu'une étape change d'état : "
    "« en_cours » quand tu l'attaques, « faite » quand elle l'est. Donne à chaque appel la "
    "liste complète, dans l'ordre : « etapes » est une liste d'objets {libelle, etat}, où "
    f"« etat » vaut {_ETATS_EN_MOTS}. Garde les mêmes libellés d'un appel à l'autre : une "
    "étape renommée ou omise reste affichée avec son dernier état. Cette checklist est ce "
    "qui dit à l'extérieur où en est ta tâche, pendant que tu travailles ; solde-la avant "
    "de conclure. L'appel n'attend aucune réponse et ne te suspend pas : poursuis aussitôt."
)

#: Le schéma d'entrée de l'outil : une liste d'étapes, et rien d'autre. La tâche,
#: l'agent et le run ne sont pas demandés : l'exécuteur les ferme (règle de
#: `consigner_decision`), et un agent ne coche pas la checklist d'un autre.
#:
#: ⚠ Un schéma **descriptif** : types et descriptions, ni `required` ni `enum`.
#: Un adaptateur peut valider l'entrée contre lui avant de nous la passer (le SDK
#: Claude le fait), et il rendrait alors la faute dans ses propres mots. Ce que
#: l'agent doit lire d'une entrée incomplète ou d'un état inconnu se décide ici
#: (`lire_etapes`), pas chez l'adaptateur.
SCHEMA_ENTREE: dict[str, Any] = {
    "type": "object",
    "properties": {
        CLE_ETAPES: {
            "type": "array",
            "description": "La checklist complète de ta tâche, dans l'ordre où elle se lit.",
            "items": {
                "type": "object",
                "properties": {
                    CLE_LIBELLE: {
                        "type": "string",
                        "description": "L'étape, en une ligne.",
                    },
                    CLE_ETAT: {
                        "type": "string",
                        "description": f"Où en est l'étape : {_ETATS_EN_MOTS}.",
                    },
                },
            },
        },
    },
}

#: Ce que lit l'agent dont la checklist **est** relevée. Deux choses, dans l'ordre
#: où elles comptent : elle se lit dehors, et **personne ne répondra** (même
#: raison qu'en #719 : sans cette phrase, un agent peut attendre un tour de plus
#: une réponse qui ne viendra jamais).
CHECKLIST_RELEVEE = (
    "Checklist relevée : {faites} étape(s) faite(s) sur {total}. Elle se lit à l'extérieur "
    "pendant que tu travailles. Personne ne va te répondre ici : poursuis ta tâche, et "
    "rappelle cet outil avec la liste complète dès qu'une étape change d'état — soldée avant "
    "de conclure."
)

#: Ce que lit l'agent dont l'entrée est invalide. Rien n'a été relevé et le texte
#: le dit d'abord ; puis ce qui cloche (`{fautes}`), puis ce qu'il faut écrire à
#: la place. Ce n'est pas un refus : il n'y a que la liste à récrire.
ENTREE_INVALIDE = (
    "Checklist NON relevée — {fautes}. Rien n'a changé à l'extérieur. Rappelle cet outil "
    "avec « etapes » : la liste complète, chaque étape un objet {{libelle, etat}}, où "
    f"« etat » vaut {_ETATS_EN_MOTS}."
)

#: Ce que rend le verbe quand le canal lui-même casse (callback en erreur). On le
#: **dit** à l'agent plutôt que d'avaler l'échec : il croirait sa liste à jour
#: dehors. Et on lui dit où reporter son avancement, puisque la checklist ne le
#: portera pas.
CANAL_EN_ERREUR = (
    "Checklist NON relevée — le canal est en erreur ({cause}). Poursuis ta tâche, et dis "
    "dans ton compte-rendu final où tu en es, étape par étape : c'est le seul endroit où "
    "ce sera lu."
)

#: Le contrat du canal : l'état complet de la liste, rien en retour. C'est la
#: signature de `on_etapes` (`ModelProvider.run_agent`), que ce verbe alimente.
#: Elle peut lever : le verbe sert alors `CANAL_EN_ERREUR`.
Releveur = Callable[[Sequence[EtapeTache]], None]


def lire_etapes(entree: object) -> tuple[list[EtapeTache], list[str]]:
    """Les étapes de l'entrée du verbe, et ses fautes — les étapes ne valent que sans faute.

    Tolérante sur la **forme** : blancs et casse d'un état ne sont pas des fautes
    (« Faite » est « faite »), le libellé est normalisé comme partout
    (`EtapeTache.valide`), et une étape sans état est « à faire », l'état qu'elle
    a quand on la pose. Exigeante sur le **fond** : une liste absente ou vide,
    une étape sans libellé, un état hors du contrat sont des fautes, chacune
    nommée avec son rang. Ne lève jamais : l'entrée vient du modèle, et la lire ne
    doit pas casser la tâche qui l'envoie.
    """
    if not isinstance(entree, Mapping):
        return [], ["l'entrée n'est pas un objet"]
    if CLE_ETAPES not in entree:
        return [], [f"« {CLE_ETAPES} » manque"]
    lignes = entree[CLE_ETAPES]
    if isinstance(lignes, str) or not isinstance(lignes, Sequence):
        return [], [f"« {CLE_ETAPES} » n'est pas une liste"]
    if not lignes:
        return [], [f"« {CLE_ETAPES} » est vide : une checklist a au moins une étape"]
    etapes: list[EtapeTache] = []
    fautes: list[str] = []
    for rang, ligne in enumerate(lignes, start=1):
        etape, faute = _etape(rang, ligne)
        if faute:
            fautes.append(faute)
        elif etape is not None:
            etapes.append(etape)
    return etapes, fautes


def _etape(rang: int, ligne: object) -> tuple[EtapeTache | None, str]:
    """Une ligne de l'entrée en étape — ou la faute qui l'en empêche."""
    if not isinstance(ligne, Mapping):
        return None, f"l'étape {rang} n'est pas un objet {{{CLE_LIBELLE}, {CLE_ETAT}}}"
    libelle = ligne.get(CLE_LIBELLE)
    if not isinstance(libelle, str) or not libelle.strip():
        return None, f"l'étape {rang} n'a pas de libellé"
    brut = ligne.get(CLE_ETAT)
    etape = EtapeTache(
        libelle=libelle,
        etat=brut if isinstance(brut, str) else "",
    ).valide()
    if (brut is not None and not isinstance(brut, str)) or etape.etat not in ETATS:
        return None, (
            f"l'étape {rang} (« {etape.libelle} ») a l'état « {brut} », "
            f"hors de {_ETATS_EN_MOTS}"
        )
    return etape, ""


def servir(entree: object, on_etapes: Releveur) -> str:
    """Fait d'un appel au verbe un relevé, et rend le texte servi à l'agent.

    Le verbe entier, indépendant de tout fournisseur : un adaptateur n'a qu'à
    passer l'entrée de l'appel et rendre ce texte à l'agent, sans jamais le
    marquer en erreur d'outil. Trois issues — relevée, entrée invalide, canal en
    erreur — et aucune ne lève.
    """
    etapes, fautes = lire_etapes(entree)
    if fautes:
        return ENTREE_INVALIDE.format(fautes=" ; ".join(fautes))
    try:
        on_etapes(etapes)
    except Exception as exc:  # noqa: BLE001 — dit à l'agent, jamais une tâche tuée
        return CANAL_EN_ERREUR.format(cause=exc)
    faites = sum(1 for etape in etapes if etape.etat == ETAPE_FAITE)
    return CHECKLIST_RELEVEE.format(faites=faites, total=len(etapes))
