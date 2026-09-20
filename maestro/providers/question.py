"""La question libre d'un agent vue de la couche fournisseur (#1023).

Un agent pouvait **soumettre un acte** (`demander_arbitrage`, #582) et
**déclarer** ce qu'il subit ou transmet (`signaler_blocage` #719,
`ecrire_a_un_pair` #720). Il ne pouvait pas **demander un renseignement** : le
seul canal qui attendait une réponse ne transportait qu'un booléen, et les trois
contrats de l'arbitrage (`Validateur`, `Arbitre`, `ArbitreActe`) sont typés pour
ça. Le socle des playbooks disait donc l'inverse de ce qu'on veut — *personne ne
répondra pendant la tâche, préfère une hypothèse à une question*.

Ce module porte le vocabulaire du quatrième verbe du serveur MCP `maestro`
(`poser_une_question`), à côté de celui des trois autres — le porte-outils de
#718 les monte sans qu'ils se croisent.

## Les deux frontières, écrites avant le code (docs/32 §5.3)

- avec **`signaler_blocage`** (#719) : *poser une question attend une réponse,
  déclarer un blocage n'attend rien*. Un seul verbe pour les deux rendrait le
  blocage suspensif — donc cher, sur le verbe le plus additif du lot — ou la
  question non suspensive, c'est-à-dire pas une question ;
- avec **`demander_arbitrage`** (#582) : celui-là soumet un **acte** et reçoit un
  **oui/non** ; celui-ci pose une **question** et reçoit du **texte**. Faire
  voyager du texte dans le canal booléen aurait demandé d'élargir ses trois
  contrats — exactement ce que #320 avait déjà tranché dans l'autre sens en
  donnant au brief un canal à lui.

⚠ **Une question ne contourne jamais une validation**, et c'est le troisième
critère du ticket (EF-08). Ce verbe ne rend aucun acte licite : il ne traverse
pas le hook `PreToolUse`, ne compose aucune `DemandeValidation`, et ce qu'un
humain écrit en réponse n'est jamais lu comme une approbation. Un acte classé
`ask` sans personne pour trancher reste **refusé**, qu'une question ait été posée
ou non.

## Ce qui est **réutilisé**, et ce qui ne l'est pas

Les trois pièces de **suspension** écrites pour l'arbitrage servent ici telles
quelles, parce qu'elles ne supposent nulle part que la réponse soit un booléen :

- `BornesArbitrage` (#583) donne la **borne** de l'attente — `attente_s`, « ce
  qu'on laisse à la personne qui tranche ». ⚠ Et **pas** `attente_effective` :
  cette valeur-là retranche la marge d'une échéance de **hook**, or aucun hook
  n'intercepte ce verbe (c'est un appel d'outil MCP, comme `demander_arbitrage`,
  qui attend sans borne de runtime). L'y adosser ferait raccourcir l'attente
  d'une question parce qu'on aurait resserré le time-out d'un point de contrôle
  qui ne la voit jamais passer ;
- `CreditArbitrage` (#584) mesure cette attente pour que le **délai de la tâche
  ne coure pas** pendant qu'une personne lit. La fenêtre s'ouvre ici, dans
  l'outil, parce que c'est là que l'appel est réellement suspendu — la règle de
  `maestro.deliberation` ;
- `MemoireArbitrage` (#584) retient une **réponse tardive** : l'agent reprend sur
  son hypothèse à la borne, la demande reste en vol, et le même appel rejoué plus
  tard retrouve la réponse sans rouvrir d'attente.

Ce qui n'est **pas** réutilisé est le canal : la question ne passe ni par
`Guardrails.demande_validation`, ni par la file `/api/validations`, dont la carte
est une carte oui/non sans geste pour une question ouverte (docs/32 §5.3).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from maestro.providers.arbitrage import NOM_SERVEUR

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
#: Un verbe à l'infinitif comme ses trois voisins : c'est ce que l'agent *fait*,
#: pas ce que le moteur en tire.
NOM_OUTIL = "poser_une_question"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
#: Le serveur est celui de `maestro.providers.arbitrage` (nom **réservé**),
#: importé plutôt que réécrit : deux littéraux « maestro » seraient deux serveurs
#: le jour où l'un des deux change.
OUTIL_QUESTION = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Combien de choix on retient au plus. Le plafond n'est pas une précaution de
#: style : ce qui arrive vient d'un modèle, et une liste de trente options n'est
#: plus une question — c'est un formulaire, que personne ne lira dans un fil. Le
#: surplus est **écarté en silence** plutôt que de faire échouer l'appel : la
#: question, elle, reste posable, et c'est ce qui compte.
CHOIX_MAX = 8

#: Longueur maximale d'un choix retenu. Même raison que le plafond ci-dessus, et
#: même borne que les valeurs d'un acte (`maestro.acte`) : un « choix » de mille
#: signes est une réponse déguisée en bouton.
CHOIX_LONGUEUR_MAX = 200

#: Ce que l'agent lit pour savoir **quand** appeler l'outil, et surtout ce qu'il
#: doit y mettre. Écrite comme celle de ses trois voisins — un **recours**, jamais
#: une étape : un verbe appelé par acquit de conscience ferait de chaque tâche une
#: file d'attente humaine, ce qui est l'exact inverse du régime sénior (docs/04).
#:
#: Les trois champs sont décrits par ce qu'ils **coûtent** à qui répond : la
#: question en clair, les choix quand il y en a de vrais, et surtout
#: l'**hypothèse** — ce que l'agent fera si personne ne répond. Cette dernière
#: n'est pas une politesse : c'est elle qui rend l'attente bornable sans rien
#: perdre, et un agent qui ne l'écrirait pas n'aurait rien à reprendre à la borne.
DESCRIPTION_OUTIL = (
    "Pose une question à l'utilisateur quand une décision t'appartient mal : un "
    "choix de produit, une préférence, une information que ta tâche ne te donne "
    "pas et que tu ne peux pas déduire. Écris dans « question » ce que tu "
    "demandes, en clair et en une fois ; dans « choix », les options entre "
    "lesquelles tu hésites, s'il y en a de vraies ; dans « hypothese », ce que tu "
    "feras SANS réponse — elle est obligatoire. "
    "L'appel attend la réponse et te suspend, mais pas indéfiniment : sans "
    "réponse à la borne, tu reprends sur ton hypothèse, qui est consignée. "
    "N'appelle pas cet outil pour une décision ordinaire de ta tâche (tranche, et "
    "dis-le dans ton compte-rendu), ni pour obtenir une autorisation — cela, "
    "c'est « demander_arbitrage » —, ni pour signaler que tu es bloqué — cela, "
    "c'est « signaler_blocage », qui n'attend rien."
)

#: Le schéma d'entrée de l'outil. Écrit en **JSON Schema complet** et non sous la
#: forme courte `{"champ": str}` de ses trois voisins, pour une seule raison :
#: cette forme-là rend *tous* les champs obligatoires (le SDK en fait la liste
#: `required`), or `choix` est facultatif par contrat — le rendre requis
#: obligerait l'agent à fabriquer des options là où il n'en a pas, ce qui est la
#: meilleure façon d'obtenir une fausse question à choix multiple.
#:
#: `question` et `hypothese` restent requis, et c'est le contrat du verbe : une
#: question sans hypothèse n'est pas bornable, et ce qui n'est pas bornable ne se
#: pose pas pendant une tâche.
SCHEMA_ENTREE: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "description": "La question, en clair, telle que l'utilisateur la lira.",
        },
        "hypothese": {
            "type": "string",
            "description": (
                "Ce que tu feras si personne ne répond avant la borne. Obligatoire."
            ),
        },
        "choix": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Les options entre lesquelles tu hésites, s'il y en a de vraies. "
                "Facultatif — n'en invente pas."
            ),
        },
    },
    "required": ["question", "hypothese"],
}

#: Ce que lit l'agent qui a appelé l'outil sans question. Rien n'a été soumis à
#: personne, donc rien n'est refusé : c'est un champ à remplir, et le texte le dit
#: comme tel. Même parti pris que `arbitrage.RAISON_MANQUANTE` et
#: `blocage.RAISON_MANQUANTE` — servir un refus enverrait l'agent renoncer à
#: quelque chose dont nul n'a été saisi.
QUESTION_MANQUANTE = (
    "Aucune question — rien n'a été posé à personne. Rappelle cet outil en "
    "écrivant ce que tu demandes, en clair, et ce que tu feras sans réponse."
)

#: Ce que lit l'agent qui pose une question sans dire ce qu'il fera sans réponse.
#: C'est le seul refus de forme propre à ce verbe, et il n'est pas formel :
#: l'attente est **bornée**, donc une question sans hypothèse promet à l'agent une
#: reprise qu'il n'a pas décrite — à la borne, il n'aurait rien à reprendre, et le
#: journal n'aurait rien à consigner. La question n'est donc pas posée du tout.
HYPOTHESE_MANQUANTE = (
    "Aucune hypothèse — la question n'a été posée à personne. L'attente est "
    "bornée : dis dans « hypothese » ce que tu feras sans réponse, puis rappelle "
    "cet outil. Sans elle, ta tâche s'arrêterait sur un silence."
)

#: Ce que lit l'agent dont la question **a reçu** une réponse. Il dit la réponse,
#: puis ce qu'il y a à en faire : sans cette seconde moitié, un agent peut très
#: bien lire une réponse et la traiter comme une remarque. La réponse est servie
#: telle qu'elle a été écrite — ni résumée, ni réinterprétée : c'est la seule
#: chose que ce verbe transporte.
REPONSE_RECUE = (
    "Réponse de l'utilisateur : {reponse}\n"
    "Tiens-en compte pour la suite de ta tâche, et dis dans ton compte-rendu "
    "final ce qu'elle a changé."
)

#: Ce que lit l'agent dont la question **est restée sans réponse** à la borne.
#: Trois choses, et les trois comptent : personne n'a répondu, il reprend sur
#: **son** hypothèse (recopiée pour qu'il n'ait pas à se souvenir de ce qu'il a
#: écrit trois tours plus tôt), et la réponse qui viendrait plus tard n'est pas
#: perdue — le même appel, rejoué, la retrouvera sans nouvelle attente
#: (`MemoireArbitrage`).
#:
#: « Plus tard » et non « tout de suite », comme en #584 : rappeler l'outil dans
#: la seconde rouvrirait l'attente pour rien et brûlerait les tours de l'agent
#: (#239) sur quelqu'un qui n'a pas fini de lire.
#:
#: ⚠ **La durée n'y est pas nommée**, et c'est délibéré : elle est réglée chez
#: l'appelant (cf. `Questionneur`), et la faire dire par ce texte-ci en ferait un
#: second support à tenir d'accord — pour un chiffre qui n'apprend rien à un agent
#: dont la seule suite possible est de reprendre son travail. Le journal du run,
#: lui, porte la borne : c'est là qu'on lit *combien de temps* on a attendu.
SANS_REPONSE = (
    "Aucune réponse — la question reste posée, personne ne l'a encore lue. "
    "Reprends sur l'hypothèse que tu as annoncée : {hypothese}\n"
    "Elle est consignée au journal du run. Poursuis ta tâche ; si tu reposes la "
    "même question plus tard, la réponse arrivée entre-temps te sera rendue sans "
    "nouvelle attente. Dis dans ton compte-rendu final que tu as tranché seul."
)

#: Ce que rend la couche fournisseur quand le canal lui-même casse (callback en
#: erreur). On le **dit** à l'agent plutôt que d'avaler l'échec — il vient de
#: demander quelque chose, et le laisser attendre une réponse qui ne viendra
#: jamais serait le pire des silences — et on lui rappelle son hypothèse, qui est
#: exactement ce qu'il lui reste.
#:
#: Ce n'est pas un refus, et l'exception ne remonte jamais : elle tuerait la tâche
#: au moment précis où l'agent cherche à bien faire.
CANAL_EN_ERREUR = (
    "La question n'a pu être posée à personne — le canal est en erreur "
    "({cause}). Reprends sur l'hypothèse que tu as annoncée : {hypothese}\n"
    "Poursuis ta tâche et dis dans ton compte-rendu final que tu as tranché seul, "
    "faute d'avoir pu demander."
)


def choix_nettoyes(brut: object) -> tuple[str, ...]:
    """Les choix exploitables d'un appel — vides, doublons et surplus écartés.

    **Relecture tolérante, jamais validation** : ce qui arrive vient d'un modèle,
    pas d'un appelant. Un `choix` absent, nul, ou rendu sous une autre forme que
    la liste attendue ne fait pas échouer la question — il n'y a simplement pas de
    choix, ce qui est le cas nominal du verbe. Faire échouer l'appel ici ferait
    perdre la question pour une liste mal formée, c'est-à-dire pour la partie
    facultative.

    Trois nettoyages, chacun pour sa raison : les entrées non textuelles et les
    blancs sont écartés (un bouton vide n'est pas une option), les doublons aussi
    (deux fois la même option est une question qu'on ne peut pas trancher), et le
    tout est borné par `CHOIX_MAX` / `CHOIX_LONGUEUR_MAX` — cf. leurs notes.

    L'ordre de l'agent est **conservé** : c'est le sien, il porte souvent une
    préférence, et le trier serait réécrire sa question.
    """
    if isinstance(brut, str) or not isinstance(brut, Sequence):
        return ()
    retenus: list[str] = []
    for entree in brut:
        if not isinstance(entree, str):
            continue
        propre = " ".join(entree.split())[:CHOIX_LONGUEUR_MAX]
        if not propre or propre in retenus:
            continue
        retenus.append(propre)
        if len(retenus) == CHOIX_MAX:
            break
    return tuple(retenus)


def reponse_recue(reponse: str) -> str:
    """Ce que l'agent lit quand quelqu'un a répondu — la réponse, puis la suite à donner."""
    return REPONSE_RECUE.format(reponse=reponse)


def sans_reponse(hypothese: str) -> str:
    """Ce que l'agent lit à la borne — son hypothèse, et ce qu'il advient de la question."""
    return SANS_REPONSE.format(hypothese=hypothese)


#: Le contrat de la couche fournisseur : une question, des choix, une hypothèse —
#: et du **texte** en retour, ou `None`.
#:
#: Reçoit les trois champs **tels que l'agent les a écrits** (question et
#: hypothèse non vides, déjà nettoyées ; choix déjà bornés) et rend la réponse
#: humaine, ou `None` quand personne n'a répondu avant la borne. C'est tout
#: l'écart avec `Arbitre`, et il est entier : celui-là transporte une **décision**
#: sur un acte, celui-ci un **renseignement** sur une question — et il a le droit
#: de ne rien rapporter, ce qu'un booléen ne sait pas dire.
#:
#: `None` n'est donc **pas** un refus, et ce module ne le rend jamais comme tel :
#: personne n'a dit non, personne n'a lu. C'est la nuance de
#: `arbitrage.motif_attente`, et elle décide du texte servi à l'agent.
#:
#: ⚠ **C'est l'implémentation qui borne**, pas cet outil : la borne vit là où vit
#: le journal (`maestro.engine.executor`), seul endroit qui doive consigner *les
#: deux* issues — la réponse reçue, et la reprise sur hypothèse. Un fournisseur
#: qui bornerait de son côté ferait reprendre l'agent sans que rien ne l'écrive
#: nulle part, ce que le troisième critère du ticket interdit.
Questionneur = Callable[[str, tuple[str, ...], str], Awaitable[str | None]]
