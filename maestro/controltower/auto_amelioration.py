"""Auto-amélioration des playbooks — analyse post-run des échecs → proposition (ticket #139).

Deuxième lot de l'axe « auto-amélioration des playbooks » (#111), au-dessus du stockage
des propositions en brouillon (#138). **À la demande** — jamais en fin de run, par prudence
sur le coût —, l'analyse relit les échecs consignés d'un run pour un agent donné et confie
à la couche d'abstraction fournisseur (`ModelProvider.generate`, #32/#69) la rédaction d'une
**version révisée** de son playbook. Le résultat est enregistré comme **proposition en
brouillon** (`PlaybookStore.proposer`, provenance « proposition ») : il ne devient jamais la
version courante et n'est jamais chargé par le moteur tant qu'une action humaine ne l'a pas
appliqué (lot UI #140).

Le point d'entrée est l'endpoint `POST /api/playbooks/{agent}/propositions` de l'app
(`maestro.controltower.app`) : il extrait les échecs du run (`echecs_du_run`) puis délègue à
`AnalyseurEchecs`. Comme le répondeur du chat (#84), l'analyseur résout son fournisseur
paresseusement — le construire ne coûte rien et les tests (#137) injectent un fournisseur
factice.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from maestro.agents.catalog import Agent
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore, PlaybookVersion
from maestro.controltower.events import EVENEMENT_TACHE_STATUT
from maestro.controltower.state import EtatExecution
from maestro.engine.executor import STATUT_ECHEC
from maestro.providers.base import ModelProvider

#: Marqueur qui sépare, dans la réponse du modèle, sa justification (avant) du playbook
#: révisé intégral (après). Choisi improbable dans un playbook Markdown, pour un découpage sûr.
MARQUEUR_PLAYBOOK = "===PLAYBOOK==="

#: Cadre système de l'analyse : ce qu'on attend du modèle et le format exact de sa réponse.
#: Le playbook demandé est **intégral** (pas un diff) : c'est ce que `proposer` stocke comme
#: contenu candidat, prêt à devenir une version courante en un clic (lot #140).
_CADRE_ANALYSE = """\
Tu es un expert en conception de playbooks d'agents autonomes. On te confie le playbook
courant d'un agent et la liste des échecs qu'il a rencontrés lors d'un run. Ta tâche :
proposer une version révisée COMPLÈTE de son playbook qui réduirait ces échecs, sans
dénaturer son rôle ni ses garde-fous.

Réponds EXACTEMENT dans ce format, sans rien d'autre :
1. Une justification de 2 à 4 phrases expliquant en quoi tes changements répondent aux
   échecs analysés (cite-les).
2. Sur une ligne seule, le marqueur : ===PLAYBOOK===
3. Le playbook révisé intégral en Markdown (le document complet, pas un diff), et rien après."""


class RevisionIndisponible(RuntimeError):
    """La proposition n'a pas pu être produite (fournisseur en échec, réponse inexploitable).

    L'analyse est à la demande et sans effet de bord tant qu'elle échoue : rien n'est
    stocké. L'API la traduit en 502 — l'utilisateur peut relancer sans conséquence.
    """


@dataclass(frozen=True)
class EchecTache:
    """Un échec consigné d'un run, réduit à ce dont l'analyse a besoin.

    `titre` est le libellé de la tâche échouée, `raison` le motif consigné
    (`Event.detail`, déjà expurgé des secrets par le journal amont, #8).
    """

    tache_id: str
    titre: str
    raison: str


def echecs_du_run(execution: EtatExecution, agent: str) -> tuple[EchecTache, ...]:
    """Les échecs consignés du run `execution` imputables à `agent`, dans l'ordre reçu.

    Un échec est un événement `tache.statut` au statut terminal « echec »
    (`maestro.engine.executor`) porté par cet agent ; `Event.detail` en donne le motif.
    Le tuple est vide si le run n'a pas d'échec pour cet agent — l'appelant en tire une
    réponse « rien à proposer » plutôt qu'un brouillon sans matière.
    """
    return tuple(
        EchecTache(tache_id=e.tache_id, titre=e.titre, raison=e.detail)
        for e in execution.evenements
        if e.type == EVENEMENT_TACHE_STATUT and e.statut == STATUT_ECHEC and e.agent == agent
    )


class AnalyseurEchecs:
    """Produit une proposition de révision de playbook à partir des échecs d'un run.

    Confie la rédaction au fournisseur configuré (#32/#69, résolu paresseusement comme le
    répondeur du chat #84) et enregistre le résultat en **brouillon** via le dépôt des
    playbooks (#138) — jamais la version courante. Un fournisseur explicite (tests #137)
    court-circuite la résolution par config.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        playbooks: PlaybookStore | None = None,
    ) -> None:
        self._provider = provider
        self._playbooks = playbooks if playbooks is not None else PlaybookStore.default()

    async def proposer_revision(
        self, agent: Agent, run_id: str, echecs: Sequence[EchecTache]
    ) -> PlaybookVersion:
        """Analyse `echecs` et enregistre une proposition de nouveau playbook pour `agent`.

        La base révisée est le playbook **courant** de l'agent (version éditée si elle
        existe, sinon son playbook du code, #76). Lève `ValueError` si `echecs` est vide
        (rien à analyser) et `RevisionIndisponible` si le fournisseur échoue ou rend une
        réponse inexploitable — aucun brouillon n'est alors écrit.
        """
        if not echecs:
            raise ValueError("aucun échec à analyser : rien à proposer.")
        base = self._playbooks.prompt_systeme(agent.nom, _playbook_du_code(agent))
        prompt = _prompt_analyse(agent, run_id, echecs, base)
        try:
            texte = await self._generer(prompt, modele=agent.modele)
        except Exception as exc:
            raise RevisionIndisponible(
                f"l'analyse des échecs de {agent.nom} a échoué : {exc}"
            ) from exc
        rationale, contenu = _decouper(texte)
        justification = _justification(run_id, echecs, rationale)
        return self._playbooks.proposer(agent.nom, contenu, justification)

    async def _generer(self, prompt: str, *, modele: str) -> str:
        """L'appel modèle, fournisseur résolu au premier usage (import local, comme #84)."""
        if self._provider is None:
            from maestro.providers.factory import provider_from_settings

            self._provider = provider_from_settings()
        return await self._provider.generate(prompt, model=modele, system_prompt=_CADRE_ANALYSE)


def _playbook_du_code(agent: Agent) -> str:
    """Le repli de `agent` quand rien n'a été publié : son **document** de playbook (#294).

    `PLAYBOOK_DEFAUTS` (le document Markdown structuré livré avec le paquet, #295) et non
    `agent.prompt_systeme` (la version condensée que l'exécution texte du catalogue
    compose) : c'est le premier que la fiche playbook affiche, que l'éditeur de l'UI
    ouvre, et que la version publiée remplacera. Réviser le second reviendrait à proposer
    la réécriture d'un texte que personne n'a sous les yeux, puis — à l'application — à
    remplacer le document structuré par une révision de sa condensation. Une proposition
    reste un document du **même format** que celui qu'elle remplace (#293).

    Repli sur le prompt du catalogue pour un **agent personnalisé** (#72), qui n'a pas de
    document livré avec le paquet : là, la condensation *est* son playbook.
    """
    defaut = PLAYBOOK_DEFAUTS.get(agent.nom)
    return defaut.contenu if defaut is not None else agent.prompt_systeme


def _prompt_analyse(
    agent: Agent, run_id: str, echecs: Sequence[EchecTache], base: str
) -> str:
    """Le prompt d'analyse : le playbook courant, les échecs du run, la consigne de révision."""
    lignes = "\n".join(f"- Tâche « {e.titre} » : {e.raison}" for e in echecs)
    return (
        f"Playbook courant de l'agent {agent.role} ({agent.nom}) :\n\n"
        f"{base}\n\n"
        f"Échecs consignés lors du run {run_id} :\n\n"
        f"{lignes}\n\n"
        "Propose la version révisée du playbook selon le format demandé."
    )


def _decouper(texte: str) -> tuple[str, str]:
    """Sépare la réponse du modèle en (justification, playbook) autour du marqueur.

    Lève `RevisionIndisponible` si le marqueur manque ou si le playbook est vide : une
    réponse inexploitable ne doit pas produire un brouillon bancal.
    """
    parties = texte.split(MARQUEUR_PLAYBOOK, 1)
    if len(parties) != 2:
        raise RevisionIndisponible(
            f"réponse du modèle sans marqueur {MARQUEUR_PLAYBOOK!r} : découpage impossible."
        )
    rationale, contenu = parties[0].strip(), parties[1].strip()
    if not contenu:
        raise RevisionIndisponible("le modèle n'a pas produit de playbook révisé (contenu vide).")
    return rationale, contenu


def _justification(run_id: str, echecs: Sequence[EchecTache], rationale: str) -> str:
    """La justification stockée : les échecs analysés (déterministe) + le motif du modèle.

    L'entête énumère les échecs référencés — la proposition reste traçable à sa source (le
    critère « justification référençant les échecs analysés ») même si le modèle a été
    laconique. `rationale` ajoute l'analyse du modèle quand elle existe.
    """
    lignes = "\n".join(f"- « {e.titre} » : {e.raison}" for e in echecs)
    entete = f"Proposition issue de l'analyse des échecs du run {run_id} :\n{lignes}"
    return f"{entete}\n\n{rationale}" if rationale else entete
