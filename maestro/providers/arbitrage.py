"""Les bornes de l'arbitrage au vol, et l'invariant qui les tient (#583, parent #573).

Le chantier #573 déplace le déclencheur de l'arbitrage humain du **texte de la
tâche** vers l'**acte** : c'est le hook `PreToolUse` du fournisseur — seul point
de contrôle consulté sous `bypassPermissions` — qui suspend un appel classé
`ask` (#580) le temps qu'une personne tranche.

Or un hook n'a pas tout son temps. `HookMatcher.timeout` **borne la durée d'un
hook**, à 60 s par défaut, et le SDK transmet cette borne au CLI. Un arbitrage
humain dure plus longtemps que ça. La question n'est donc pas « comment attendre
assez longtemps » mais **qui rend le verdict quand l'attente s'éternise** : nous,
ou le CLI par échéance ?

Ce module répond « nous », et il le garantit par un invariant plutôt que par une
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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from maestro.config import ConfigError, Settings

#: Ce qu'on laisse à la personne qui tranche, en secondes. Quatre minutes : assez
#: pour lire l'acte et décider, assez court pour qu'un agent ne reste pas suspendu
#: une demi-heure sur un appel que personne ne regarde. Ce n'est pas le temps
#: qu'un humain *devrait* avoir — c'est le temps qu'un **hook** peut tenir ; le
#: découplage de l'attente et du délai de la tâche est le sujet de #584.
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


#: Le canal de l'arbitrage : reçoit l'outil, ses arguments (`maestro.acte`) et le
#: motif de la mise en arbitrage, rend `(approuvé ?, détail traçable)` — le même
#: couple que `Guardrails.demande_validation`, dont il n'est que le relais. Le
#: fournisseur ne sait rien de la tâche ni du run : c'est l'appelant (l'exécuteur)
#: qui compose la `DemandeValidation` et la soumet au validateur configuré, donc
#: lui qui hérite du fail-safe « pas de validateur ⇒ refus » (EF-08, ENF-04).
Arbitre = Callable[[str, dict[str, str], str], Awaitable[tuple[bool, str]]]


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

    C'est le motif qui donne son sens au ticket : à l'expiration de *notre*
    attente, **nous** répondons. Le texte dit l'état exact des choses — la
    demande est toujours en attente, l'appel est écarté *pour cette fois* — plutôt
    que d'annoncer un refus qui n'a été prononcé par personne.
    """
    return (
        f"appel de l'outil {outil!r} écarté : arbitrage en cours, aucune décision "
        f"humaine après {attente_s:g} s. La demande reste en attente. "
        "Poursuis la tâche sans cet outil."
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
    depuis #9 : un canal de délibération en panne ne laisse rien passer. La cause
    voyage dans le motif — c'est elle qu'on cherchera, et elle ne se reconstitue
    pas depuis un refus muet.
    """
    return (
        f"appel de l'outil {outil!r} refusé : l'arbitrage humain n'a pas pu être "
        f"soumis ({cause}). Poursuis la tâche sans cet outil."
    )
