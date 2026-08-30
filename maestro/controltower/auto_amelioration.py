"""Propositions de playbook — les deux voies qui mènent à une version révisée (#139, #261).

Ce module porte l'axe « auto-amélioration des playbooks » (#111), au-dessus du stockage des
propositions en brouillon (#138). Une **proposition** y est toujours la même chose : un
playbook révisé **intégral** accompagné de sa justification, produit par la couche
d'abstraction fournisseur (`ModelProvider.generate`, #32/#69), que personne n'a encore
endossé. Seule la **matière** de départ change, et c'est ce qui distingue les deux voies :

- `AnalyseurEchecs` (#139) part des **échecs consignés d'un run**. Sa proposition est
  enregistrée en brouillon (`PlaybookStore.proposer`, provenance « proposition ») : elle
  survit à la page, attend une décision, et n'est jamais chargée par le moteur tant qu'une
  action humaine ne l'a pas appliquée (lot UI #140). Point d'entrée :
  `POST /api/playbooks/{agent}/propositions`.
- `RedacteurPlaybook` (#261) part du **brouillon en cours de frappe** dans l'éditeur, et
  d'une consigne libre facultative (« resserre les garde-fous », « ajoute une section
  Méthode »). Sa proposition n'est **rien enregistrer du tout** : elle est rendue à
  l'éditeur, qui l'affiche en différentiel et laisse l'utilisateur l'appliquer à son
  brouillon — ou la jeter. Point d'entrée : `POST /api/playbooks/{agent}/redaction`.

⚠ **Pourquoi la seconde n'écrit pas**, alors que la première écrit : une proposition d'après
run naît sans que personne regarde l'écran, il faut donc qu'elle attende quelque part ; une
réécriture demandée au clavier a son destinataire devant elle, et la stocker numéroterait
dans un dépôt append-only des brouillons dont la moitié seront jetés dans la seconde. Surtout,
appliquer une proposition stockée **publie une version** (`appliquer_proposition`) : c'est
exactement ce que le critère « rien n'est publié sans geste explicite » de #261 interdit à
l'assistance de faire. Ce qui est mutualisé est donc le **cadre** — le format de réponse
(justification, marqueur, document intégral), son découpage, la classe d'échec et la
résolution paresseuse du fournisseur — et non le geste d'écriture.

Comme le répondeur du chat (#84), les deux résolvent leur fournisseur paresseusement : les
construire ne coûte rien, et les tests (#137) leur injectent un fournisseur factice.
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


#: Cadre système de la **rédaction assistée** (#261) : même contrat de réponse que l'analyse
#: — une justification, le marqueur, le document intégral — parce que c'est le même objet
#: qu'on produit. Ce qui change est la matière : un brouillon en cours d'écriture plutôt que
#: les échecs d'un run, et une consigne libre que l'utilisateur a tapée juste avant.
_CADRE_REDACTION = """\
Tu es un expert en conception de playbooks d'agents autonomes. On te confie le brouillon de
playbook qu'une personne est en train d'écrire, et éventuellement ce qu'elle te demande d'en
faire. Ta tâche : rendre une version COMPLÈTE et améliorée de ce brouillon.

Respecte ce qui est déjà écrit : garde le rôle, le ton, les garde-fous et les sections que
l'auteur a posés ; complète, reformule et structure, ne repars pas de zéro. Si le brouillon
est très court, développe-le sans inventer de mission qu'il ne porte pas.

Réponds EXACTEMENT dans ce format, sans rien d'autre :
1. Une justification de 2 à 4 phrases décrivant ce que tu as changé et pourquoi.
2. Sur une ligne seule, le marqueur : ===PLAYBOOK===
3. Le playbook réécrit intégral en Markdown (le document complet, pas un diff), et rien après."""

#: Bornes de la demande de rédaction. Le brouillon est plafonné loin au-dessus d'un playbook
#: réel (les documents livrés pèsent ~5 ko) : la borne écarte un envoi aberrant, elle ne
#: rationne pas l'écriture. La consigne est alignée sur l'intention de #257.
BROUILLON_MAX = 40_000
CONSIGNE_MAX = 500


class RevisionIndisponible(RuntimeError):
    """La proposition n'a pas pu être produite (fournisseur en échec, réponse inexploitable).

    L'analyse est à la demande et sans effet de bord tant qu'elle échoue : rien n'est
    stocké. L'API la traduit en 502 — l'utilisateur peut relancer sans conséquence.
    """


class _AppelModele:
    """La part commune aux deux voies : un fournisseur résolu au **premier usage**.

    Construire un analyseur ou un rédacteur ne doit rien coûter — l'app en instancie à
    chaque démarrage, y compris là où aucun fournisseur n'est configuré (démo, tests). La
    résolution passe donc par un import local, au moment où l'appel a réellement lieu.
    """

    def __init__(self, *, provider: ModelProvider | None = None) -> None:
        self._provider = provider

    async def _generer(self, prompt: str, *, modele: str, cadre: str) -> str:
        if self._provider is None:
            from maestro.providers.factory import provider_from_settings

            self._provider = provider_from_settings()
        return await self._provider.generate(prompt, model=modele, system_prompt=cadre)


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


class AnalyseurEchecs(_AppelModele):
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
        super().__init__(provider=provider)
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
            texte = await self._generer(prompt, modele=agent.modele, cadre=_CADRE_ANALYSE)
        except Exception as exc:
            raise RevisionIndisponible(
                f"l'analyse des échecs de {agent.nom} a échoué : {exc}"
            ) from exc
        rationale, contenu = _decouper(texte)
        justification = _justification(run_id, echecs, rationale)
        return self._playbooks.proposer(agent.nom, contenu, justification)


@dataclass(frozen=True)
class RedactionProposee:
    """Une réécriture proposée à l'éditeur : le document entier, et pourquoi il a changé.

    Volontairement **sans numéro ni date** — ce n'est pas une version, ni même une
    proposition stockée, mais un candidat en vol : tant que personne ne l'a appliqué à son
    brouillon, il n'existe nulle part ailleurs que dans la réponse HTTP.
    """

    contenu: str
    justification: str

    def to_dict(self) -> dict[str, str]:
        return {"contenu": self.contenu, "justification": self.justification}


class RedacteurPlaybook(_AppelModele):
    """Réécrit le **brouillon en cours** d'un playbook, sans rien enregistrer (#261).

    Le pendant « au clavier » de `AnalyseurEchecs` : même cadre de réponse, même classe
    d'échec, même fournisseur — mais la matière est le texte que l'utilisateur a sous les
    yeux, et le résultat lui revient pour qu'il en décide. Aucun dépôt n'est touché, ni
    celui des versions ni celui des propositions.
    """

    async def proposer_redaction(
        self, agent: Agent, brouillon: str, consigne: str | None = None
    ) -> RedactionProposee:
        """Propose une réécriture de `brouillon` pour `agent`, guidée par `consigne`.

        Lève `ValueError` si le brouillon est vide ou hors bornes (rien à réécrire, ou
        envoi aberrant) et `RevisionIndisponible` si le fournisseur échoue ou rend une
        réponse inexploitable — dans les deux cas le brouillon de l'utilisateur est
        intact, il n'a jamais quitté son écran.
        """
        texte = brouillon.strip()
        if not texte:
            raise ValueError("brouillon vide : rien à réécrire.")
        if len(texte) > BROUILLON_MAX:
            raise ValueError(
                f"brouillon trop long ({len(texte)} caractères, maximum {BROUILLON_MAX})."
            )
        demande = (consigne or "").strip()
        if len(demande) > CONSIGNE_MAX:
            raise ValueError(
                f"consigne trop longue ({len(demande)} caractères, maximum {CONSIGNE_MAX})."
            )
        prompt = _prompt_redaction(agent, texte, demande)
        try:
            reponse = await self._generer(
                prompt, modele=agent.modele, cadre=_CADRE_REDACTION
            )
        except Exception as exc:
            raise RevisionIndisponible(
                f"la rédaction assistée du playbook de {agent.nom} a échoué : {exc}"
            ) from exc
        rationale, contenu = _decouper(reponse)
        return RedactionProposee(
            contenu=contenu,
            justification=rationale or "Le modèle n'a pas motivé sa réécriture.",
        )


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


def _prompt_redaction(agent: Agent, brouillon: str, consigne: str) -> str:
    """Le prompt de rédaction : le rôle, le brouillon tel quel, la consigne si elle existe.

    Le brouillon est passé **sans retouche** — c'est le texte que l'utilisateur a sous les
    yeux, et la réécriture doit pouvoir s'y comparer ligne à ligne dans le différentiel de
    l'éditeur. Sans consigne, la demande reste ouverte : compléter et structurer.
    """
    demande = (
        f"Ce que l'auteur demande :\n\n{consigne}"
        if consigne
        else "L'auteur n'a pas précisé sa demande : complète et structure ce brouillon."
    )
    return (
        f"Brouillon de playbook pour l'agent {agent.role} ({agent.nom}) :\n\n"
        f"{brouillon}\n\n"
        f"{demande}\n\n"
        "Rends la version réécrite selon le format demandé."
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
