"""La question d'un agent vue du moteur — ce qui part, et ce qui l'identifie (#1023).

Pendant exact de `maestro.engine.brief` sur l'autre canal de texte libre : ce
module porte la **demande** qu'un agent adresse à l'utilisateur pendant sa tâche,
et le contrat de l'arbitre qui la lui porte. Il ne décide de rien — ni de la
borne (`maestro.engine.executor`), ni du transport
(`maestro.controltower.question`), ni du texte servi à l'agent
(`maestro.providers.question`).

Il vit sous `maestro.engine` et non sous `maestro.providers` pour la raison qui a
déjà rangé `DemandeBrief` ici : ce qui voyage porte la **tâche** et son **run**,
que la couche de transport n'a pas à connaître — l'agent, lui, n'écrit que sa
question. Un agent qui fournirait son nom, sa tâche ou son run pourrait signer
d'un autre nom ou rattacher sa question à celle d'un tiers ; les trois sont
fermés par l'exécuteur, comme dans `_arbitre` et `_courrier` (#582, #720).

## L'identifiant, et pourquoi il ne suffit pas d'indexer par tâche

La file de validation s'indexe par `tache_id` (#48) et l'assume : une nouvelle
demande sur la même tâche remplace la précédente. Ce serait faux ici. Une tâche
pose **plusieurs** questions au fil de son travail, et surtout une question
restée sans réponse **reste en vol** pendant que l'agent reprend sur son
hypothèse (`MemoireArbitrage`, #584) : deux questions d'une même tâche peuvent
donc attendre en même temps. Indexées par `tache_id`, l'une écraserait l'autre à
l'écran, et la réponse écrite pour la seconde serait rendue à la première.

D'où `identifiant_question`, dérivé de la **clé d'acte** de la question
(`maestro.deliberation.cle_acte`) plutôt que tiré au sort. Le choix a une
conséquence utile : il est **déterministe**, donc la même question reposée porte
le même identifiant — ce qui est exactement ce que la mémoire des réponses
tardives promet, et ce qui rend adressable après coup une réponse arrivée trop
tard.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from hashlib import sha256

from maestro.providers.question import NOM_OUTIL

#: Le verbe sous lequel une question entre dans la mémoire des délibérations
#: (`maestro.deliberation.cle_acte`). C'est le nom **court** de l'outil, celui que
#: le hook `PreToolUse` ne voit jamais — lui ne manipule que la forme préfixée
#: (`mcp__maestro__…`, `OUTIL_QUESTION`). Les deux espaces de clés ne peuvent donc
#: pas se croiser, et une question ne retrouvera jamais la décision rendue sur un
#: acte, ni l'inverse. C'est ce qui permet de partager **une seule**
#: `MemoireArbitrage` par tâche sans que les deux canaux se confondent.
VERBE_QUESTION = NOM_OUTIL


@dataclass(frozen=True)
class DemandeQuestion:
    """Ce qu'un agent demande à l'utilisateur pendant sa tâche, et à qui l'imputer.

    Les trois premiers champs viennent de l'agent — sa question, ce qu'il fera
    sans réponse, et ses choix éventuels (déjà nettoyés et bornés par
    `maestro.providers.question.choix_nettoyes`) ; tout le reste est **fermé par
    l'exécuteur**.

    `hypothese` est un champ à part : c'est le seul qui serve **deux fois** — il
    est montré à qui répond (savoir ce qui se passera sans réponse change
    l'urgence de la question) et il est servi à l'agent à la borne, mot pour mot.
    """

    question_id: str
    question: str
    hypothese: str
    choix: tuple[str, ...] = ()
    tache_id: str = ""
    titre: str = ""
    agent: str = ""
    role: str = ""
    run_id: str = ""
    projet_id: str | None = None
    #: La borne annoncée, en secondes — ce que l'exécuteur laisse à qui répond.
    #: Elle voyage avec la demande parce qu'elle est une **information pour la
    #: personne** autant qu'un réglage : « il reste quatre minutes » et « il reste
    #: une heure » n'appellent pas le même geste.
    attente_s: float = 0.0

    def resume(self) -> str:
        """La question, ses choix et son hypothèse en une ligne — l'entrée du journal.

        Les choix y entrent quand il y en a : une trace qui ne garderait que
        l'énoncé d'une question à options serait illisible après coup, et c'est
        pourtant elle qu'on relit pour juger de la réponse.
        """
        morceaux = [self.question]
        if self.choix:
            morceaux.append("choix : " + " · ".join(self.choix))
        morceaux.append(f"sans réponse : {self.hypothese}")
        return " — ".join(morceaux)


def identifiant_question(tache_id: str, cle: str) -> str:
    """L'identifiant d'une question : sa tâche, puis l'empreinte de son contenu.

    Lisible d'un bout (on voit de quelle tâche il s'agit sans rien ouvrir) et
    stable de l'autre (`cle` est la clé d'acte de la question, donc la même
    question reposée rend le même identifiant). L'empreinte est tronquée à dix
    signes : elle n'a pas à résister à une attaque — elle sépare les questions
    d'**une** tâche, qui se comptent sur les doigts d'une main.
    """
    empreinte = sha256(cle.encode("utf-8")).hexdigest()[:10]
    return f"{tache_id}:{empreinte}"


#: À qui l'exécuteur porte la question, et ce qu'il en attend : la **réponse
#: humaine**, sans borne à lui.
#:
#: Attendre indéfiniment est le contrat, et c'est le même que celui du
#: `Validateur` (#9) et de l'`ArbitreBrief` (#320) : *où* la question est posée
#: est un câblage de déploiement, *combien de temps on l'attend* est une décision
#: du moteur. La borne vit donc chez l'appelant (`LocalExecutor`), seul à pouvoir
#: consigner **les deux** issues — la réponse reçue, et la reprise sur hypothèse
#: (troisième critère de #1023).
#:
#: Un arbitre qui **lève** (bus refermé, transport en panne) n'échoue pas la
#: tâche : l'exécuteur le traduit pour l'agent en « je n'ai pu demander à
#: personne, reprends sur ton hypothèse ». Une question sans réponse n'a jamais
#: été un motif de tuer un travail en cours.
ArbitreQuestion = Callable[[DemandeQuestion], Awaitable[str]]
