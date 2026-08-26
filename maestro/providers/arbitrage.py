"""L'arbitrage humain vu de la couche fournisseur — ses deux canaux (#582, #583).

Le chantier #573 déplace le déclencheur de l'arbitrage du **texte de la tâche**
vers l'**acte** : développer une fonction de suppression n'est pas exécuter une
suppression. Ce module porte le vocabulaire de ce déplacement côté fournisseur,
et il en porte **deux canaux**, qu'il ne faut pas confondre :

- **l'agent lève la main** (#582, `Arbitre`) : il s'apprête à quelque chose
  d'irréversible, au-delà de ce que la politique avait prévu, et il le dit —
  l'outil `demander_arbitrage(raison)`, monté sur la session par un serveur MCP
  in-process. Une raison en entrée, une décision en sortie ;
- **l'acte est suspendu** (#583, `ArbitreActe`) : la politique de permissions
  classe l'outil en `ask` (#580) et le hook `PreToolUse` — seul point de contrôle
  consulté sous `bypassPermissions` — suspend l'appel avant qu'il ne parte.
  L'outil et ses arguments en entrée, une décision en sortie.

Les deux vont **vers** l'agent là où `on_refus`, `on_activite` et `on_etapes` en
reviennent, et tous deux aboutissent au même `Guardrails.demande_validation`,
donc au même validateur et au même fail-safe. Ce qui les sépare est la valeur de
preuve, et c'est le cadrage de #573 : la classification tient quand l'agent se
trompe ou se fait manipuler, sa demande ne prouve que ce qu'il a bien voulu dire.
Le second n'est pas un doublon du premier — **le silence de l'agent ne dispense
de rien**.

Le reste de ce module est ce qui appartient en propre au **second** canal : les
bornes de l'attente, et l'invariant qui les tient.

Un hook n'a pas tout son temps. `HookMatcher.timeout` **borne la durée d'un
hook**, à 60 s par défaut, et le SDK transmet cette borne au CLI. Un arbitrage
humain dure plus longtemps que ça. La question n'est donc pas « comment attendre
assez longtemps » mais **qui rend le verdict quand l'attente s'éternise** : nous,
ou le CLI par échéance ?

La réponse est « nous », et elle est garantie par un invariant plutôt que par une
discipline : **notre attente reste strictement sous la borne que nous annonçons
au runtime**, `MARGE_MIN_S` en réserve. À l'expiration de *notre* attente, le
hook rend un `deny` motivé « arbitrage en cours » — une réponse, pas un silence.
Ce que le CLI fait d'un hook expiré (laisser passer ? refuser ?) n'a jamais à
porter le fail-safe : on ne l'atteint pas.

Deux réglages, et un seul invariant :

- `attente_s` est ce qu'on laisse à la personne qui tranche ;
- `borne_hook_s` est ce qu'on annonce au runtime (`HookMatcher(timeout=…)`) ;
- l'attente **effective** est `min(attente_s, borne_hook_s - MARGE_MIN_S)` : un
  réglage qui rapprocherait les deux ne peut pas nous faire dépasser l'échéance,
  il ne fait que raccourcir l'attente. C'est la seule façon que le fail-safe ne
  dépende pas de la cohérence de deux nombres réglés séparément.

Une borne qui ne laisse même pas la marge est en revanche une **erreur de
config** franche (`ConfigError`), jamais un repli silencieux : à ce régime plus
aucun arbitrage n'aboutirait, et le découvrir sur un run serait le découvrir
trop tard.

⚠ Ces bornes bornent une **attente**, jamais une décision — et depuis #584 elles
ne bornent plus rien d'autre. Le temps passé ici ne consomme pas le délai de la
tâche (`maestro.deliberation.CreditArbitrage`, mesuré par le hook lui-même
puisqu'il est le seul à savoir quand il cesse d'attendre), et une décision qui
arrive après l'expiration n'est plus perdue : elle est retenue
(`maestro.deliberation.MemoireArbitrage`) et le rappel du même acte la retrouve.
Ce qui se joue à l'expiration est donc « qui répond maintenant », pas « quel sort
est réservé à cet acte ».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from maestro.config import ConfigError, Settings

# --- ① L'agent lève la main : l'outil `demander_arbitrage` (#582) ----------------------

#: Nom du serveur MCP **in-process** qui porte l'outil. Le nom est **réservé** :
#: un serveur déclaré par un agent (`core/mcp/<agent>.json`) qui le porterait
#: serait supplanté par celui-ci. C'est le sens sûr des deux — le canal d'un
#: garde-fou ne se laisse pas masquer par une déclaration.
NOM_SERVEUR = "maestro"

#: Nom de l'outil tel que l'agent l'appelle, une fois préfixé par son serveur.
NOM_OUTIL = "demander_arbitrage"

#: Le nom complet de l'outil dans une session SDK (`mcp__<serveur>__<outil>`) —
#: donc la forme sous laquelle une politique de permissions (#110) le désigne.
OUTIL_ARBITRAGE = f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"

#: Ce que l'agent lit pour savoir **quand** appeler l'outil. Écrit pour être un
#: recours et non une étape : « au-delà de ce qui est prévu », jamais « avant
#: chaque action ». Un outil qu'on appellerait par acquit de conscience ferait
#: de chaque tâche une file d'attente humaine.
DESCRIPTION_OUTIL = (
    "Demande un arbitrage humain avant une action que tu juges irréversible ou "
    "hors de ce que ta tâche prévoyait (suppression, déploiement, écriture hors "
    "de ton espace de travail, dépense…). Décris dans « raison » l'action exacte "
    "et pourquoi tu hésites. L'appel attend la réponse : approuvée, réalise "
    "l'action ; refusée, ne la réalise pas et poursuis la tâche sans elle. "
    "N'appelle pas cet outil pour une action ordinaire de ta tâche."
)

#: Le schéma d'entrée de l'outil — un seul champ, celui que l'humain lira.
SCHEMA_ENTREE: dict[str, type] = {"raison": str}

#: Ce que lit l'agent qui a appelé l'outil sans rien dire. Un arbitrage sans
#: raison n'est pas arbitrable : personne ne peut trancher « il demande quelque
#: chose », et rien n'a été soumis à qui que ce soit.
#:
#: Ce n'est donc **pas** un refus, et c'est la nuance qui a coûté une relecture :
#: la réponse d'un refus dit « ne fais pas cette action, poursuis sans elle » —
#: exactement ce qu'il ne faut pas dire ici, où l'appel n'a rien décidé du tout.
#: Le seul geste utile est de rappeler l'outil, cette fois en décrivant l'action.
RAISON_MANQUANTE = (
    "Aucune raison fournie — la demande n'a été soumise à personne. Un arbitrage "
    "se tranche sur l'action décrite : rappelle cet outil en disant précisément "
    "ce que tu veux faire et pourquoi tu hésites."
)

#: Ce que rend la couche fournisseur quand le canal d'arbitrage lui-même casse
#: (callback en erreur). Refus, comme partout ailleurs sur ce chemin : une
#: panne d'observation n'a jamais autorisé une action sensible.
CANAL_EN_ERREUR = "canal d'arbitrage en erreur ({cause}) — refus par défaut"

#: Le contrat de la couche fournisseur : une raison, une décision.
#:
#: Reçoit la raison **telle que l'agent l'a écrite** (non vide, déjà nettoyée) et
#: rend `(approuvée ?, détail traçable)` — exactement le couple de
#: `Guardrails.demande_validation`, dont c'est le seul appelant en amont. Le
#: fournisseur n'en fabrique jamais la décision : il la demande, l'attend et la
#: transmet.
Arbitre = Callable[[str], Awaitable[tuple[bool, str]]]


def reponse(approuve: bool, detail: str) -> str:
    """Compose ce que l'agent lit en retour de sa demande — décision et suite à donner.

    Les deux branches disent **la décision, son motif, et ce qu'il faut en
    faire** : sans la troisième moitié, un agent approuvé peut hésiter et un
    agent refusé peut réessayer. Le motif vient du garde-fou (`detail`) et n'est
    jamais réécrit ici — « aucun validateur humain configuré » et « refusée par
    le validateur humain » ne se répondent pas de la même façon, et c'est à
    l'agent d'en tenir compte dans son compte-rendu.
    """
    if approuve:
        return (
            f"Arbitrage approuvé — {detail}. "
            "Réalise l'action que tu as décrite, puis poursuis ta tâche."
        )
    return (
        f"Arbitrage refusé — {detail}. "
        "Ne réalise pas cette action. Poursuis ta tâche sans elle, et dis dans "
        "ton compte-rendu final ce que tu n'as pas pu faire et pourquoi."
    )


# --- ② L'acte est suspendu : le hook PreToolUse et ses bornes (#583) -------------------

#: Ce qu'on laisse à la personne qui tranche, en secondes. Quatre minutes : assez
#: pour lire l'acte et décider, assez court pour qu'un agent ne reste pas suspendu
#: une demi-heure sur un appel que personne ne regarde. Ce n'est pas le temps
#: qu'un humain *devrait* avoir — c'est le temps qu'un **hook** peut tenir.
#:
#: Ce n'est plus non plus le temps qu'un humain *a* (#584) : ces quatre minutes
#: bornent une **attente**, pas une décision. Au-delà, l'appel est écarté pour
#: cette fois et la demande reste en vol ; la décision, quand elle vient, est
#: retenue (`maestro.deliberation.MemoireArbitrage`) et le rappel du même acte la
#: retrouve. Et pendant tout ce temps, le délai de la tâche ne court pas
#: (`CreditArbitrage`) : il n'y a donc plus d'arbitrage à faire entre « laisser à
#: quelqu'un le temps de lire » et « ne pas tuer la tâche ».
ATTENTE_S: float = 240.0

#: Ce qu'on annonce au runtime (`HookMatcher(timeout=…)`), en secondes. Posé
#: **explicitement** : sans lui la borne serait celle du SDK (60 s), c'est-à-dire
#: une valeur qu'on subit au lieu de la choisir.
BORNE_HOOK_S: float = 300.0

#: Ce qu'on garde entre la fin de notre attente et l'échéance annoncée. Il ne
#: couvre pas un traitement — composer un `deny` et l'écrire coûte des
#: microsecondes — mais le **décalage des deux horloges** : celle du CLI part
#: quand il émet la requête, la nôtre quand la coroutine démarre, et entre les
#: deux il y a un aller-retour de processus à processus.
MARGE_MIN_S: float = 5.0


#: Le contrat du **second** canal : l'acte, et une décision.
#:
#: Reçoit l'outil que l'agent s'apprête à appeler, ses arguments (`maestro.acte`
#: — du texte, clé par clé, chaque valeur bornée) et le motif rendu par la
#: politique, et rend `(approuvé ?, détail traçable)` — le même couple que
#: `Arbitre`, et pour la même raison : c'est celui de
#: `Guardrails.demande_validation`, dont les deux canaux ne sont que des relais.
#:
#: Il est **distinct d'`Arbitre`** et le restera : ce qu'un humain doit voir pour
#: trancher n'est pas le même dans les deux cas — la raison qu'un agent a
#: rédigée d'un côté, l'acte qu'on a intercepté de l'autre. Les réunir sous une
#: seule signature obligerait l'un des deux à mentir sur ce qu'il transporte.
ArbitreActe = Callable[[str, dict[str, str], str], Awaitable[tuple[bool, str]]]


@dataclass(frozen=True)
class BornesArbitrage:
    """Les deux bornes de l'attente d'arbitrage, et l'invariant qui les relie.

    `attente_effective` est la seule valeur que le hook doit lire : elle tient
    l'invariant quoi qu'on ait réglé, et c'est ce qui distingue ce dispositif
    d'une convention entre deux constantes.
    """

    attente_s: float = ATTENTE_S
    borne_hook_s: float = BORNE_HOOK_S

    def __post_init__(self) -> None:
        for nom, valeur in (
            ("attente_s", self.attente_s),
            ("borne_hook_s", self.borne_hook_s),
        ):
            if valeur <= 0:
                raise ConfigError(f"{nom} doit être > 0 (reçu : {valeur}).")
        if self.borne_hook_s <= MARGE_MIN_S:
            raise ConfigError(
                f"borne_hook_s doit dépasser la marge de réponse ({MARGE_MIN_S:g} s) "
                f"— reçu : {self.borne_hook_s:g} s. Sous ce seuil, aucun arbitrage "
                "ne peut aboutir et le hook rendrait un refus à chaque appel."
            )

    @property
    def attente_effective(self) -> float:
        """L'attente réellement accordée : jamais à moins de `MARGE_MIN_S` de l'échéance.

        Le `min` n'est pas une précaution de style : c'est lui qui rend le
        fail-safe indépendant du réglage. Une attente réglée au-delà de la borne
        est **raccourcie**, jamais honorée — sans quoi il suffirait d'un `.env`
        mal recopié pour que le verdict d'un appel sensible revienne au CLI.
        """
        return min(self.attente_s, self.borne_hook_s - MARGE_MIN_S)

    @classmethod
    def from_settings(cls, settings: Settings) -> BornesArbitrage:
        """Les bornes de la config (`MAESTRO_ARBITRAGE_ATTENTE`/`_BORNE_HOOK`).

        Absentes, les valeurs du module. Illisibles, une `ConfigError` nommant la
        variable : un réglage de garde-fou qu'on ne sait pas lire ne se remplace
        pas en silence par un défaut — c'est le seul endroit où l'écart entre ce
        qu'on croit avoir réglé et ce qui s'applique ne se verrait jamais.
        """
        return cls(
            attente_s=_secondes(
                settings.arbitrage_attente, "MAESTRO_ARBITRAGE_ATTENTE", ATTENTE_S
            ),
            borne_hook_s=_secondes(
                settings.arbitrage_borne_hook, "MAESTRO_ARBITRAGE_BORNE_HOOK", BORNE_HOOK_S
            ),
        )


def _secondes(brut: str | None, variable: str, defaut: float) -> float:
    """Lit une durée en secondes ; `ConfigError` nommant `variable` si elle est illisible."""
    if brut is None:
        return defaut
    try:
        return float(brut)
    except ValueError as exc:
        raise ConfigError(
            f"{variable} invalide : {brut!r} (durée en secondes attendue)."
        ) from exc


def motif_approbation(outil: str, detail: str) -> str:
    """Ce qu'on trace d'un appel **approuvé** à l'arbitrage — jamais servi à l'agent.

    L'agent n'a rien à lire ici : son appel passe, et lui dire qu'il a été
    approuvé n'apprend rien à qui n'a jamais su qu'il était suspendu. La trace,
    elle, compte : un acte sensible parti sur accord humain doit se retrouver
    dans le journal du run au même titre qu'un acte refusé.

    C'est la différence avec `reponse` du premier canal, qui *parle à l'agent* :
    lui a levé la main, donc il attend une réponse. Ici il ne sait même pas
    qu'on lui a demandé la permission.
    """
    return f"appel de l'outil {outil!r} approuvé à l'arbitrage humain — {detail}"


def motif_refus(outil: str, detail: str) -> str:
    """Ce qu'on sert à l'agent — et trace — d'un appel **refusé** à l'arbitrage.

    Même forme que les motifs de politique (`maestro.agents.permissions`) : il
    nomme l'outil, dit d'où vient le refus, et se termine par la consigne qui
    évite qu'un refus propre ne devienne un échec de tâche.
    """
    return (
        f"appel de l'outil {outil!r} refusé à l'arbitrage humain — {detail}. "
        "Poursuis la tâche sans cet outil."
    )


def motif_attente(outil: str, attente_s: float) -> str:
    """Ce qu'on sert à l'agent — et trace — quand l'arbitrage **n'a pas encore tranché**.

    C'est le motif qui donne son sens à #583 : à l'expiration de *notre* attente,
    **nous** répondons. Le texte dit l'état exact des choses — la demande est
    toujours en attente, l'appel est écarté *pour cette fois* — plutôt que
    d'annoncer un refus qui n'a été prononcé par personne.

    Il dit aussi, depuis #584, **ce qu'il y a à en faire** : la décision qui
    arrivera est retenue (`maestro.deliberation.MemoireArbitrage`) et le même
    appel, rejoué plus tard, la retrouvera sans rouvrir de demande ni attendre à
    nouveau. Sans cette phrase, le dispositif existerait sans que le seul acteur
    capable de le déclencher sache qu'il est là — l'agent lisait « poursuis sans
    cet outil » et n'avait aucune raison d'y revenir.

    « Plus tard » et non « tout de suite », à dessein : rappeler l'outil dans la
    seconde rouvrirait l'attente pour rien et brûlerait les tours de l'agent
    (#239) sur une personne qui n'a pas fini de lire. Ce qu'on lui demande est de
    continuer, puis de repasser — pas de faire le pied de grue.
    """
    return (
        f"appel de l'outil {outil!r} écarté : arbitrage en cours, aucune décision "
        f"humaine après {attente_s:g} s. La demande reste en attente. "
        "Poursuis la tâche sans cet outil ; si tu y reviens plus tard, la "
        "décision rendue entre-temps s'appliquera sans nouvelle attente."
    )


def motif_sans_arbitre(outil: str) -> str:
    """Le fail-safe : un acte à arbitrer, et personne à qui demander (EF-08, ENF-04).

    Le cas se présente quand la politique classe un outil en `ask` alors qu'aucun
    canal d'arbitrage n'est câblé — un appelant qui exécute hors de la Control
    Tower, par exemple. La règle du parent #573 est explicite : « sans validateur
    humain, un acte classé humain est refusé, et l'orchestrateur ne peut jamais
    l'approuver à la place d'une personne ». Laisser passer serait l'exact inverse
    du cran qu'on vient d'ajouter.
    """
    return (
        f"appel de l'outil {outil!r} refusé : il demande un arbitrage humain et "
        "aucun canal d'arbitrage n'est câblé sur cette exécution. "
        "Poursuis la tâche sans cet outil."
    )


def motif_panne(outil: str, cause: object) -> str:
    """Le fail-safe de l'autre côté : le canal d'arbitrage a levé (bus en panne…).

    Même parti pris que `Guardrails.demande_validation`, dont c'est la règle
    depuis #9, et que `CANAL_EN_ERREUR` sur le canal de l'agent : un canal de
    délibération en panne ne laisse rien passer. La cause voyage dans le motif —
    c'est elle qu'on cherchera, et elle ne se reconstitue pas depuis un refus muet.
    """
    return (
        f"appel de l'outil {outil!r} refusé : l'arbitrage humain n'a pas pu être "
        f"soumis ({cause}). Poursuis la tâche sans cet outil."
    )
