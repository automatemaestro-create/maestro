"""L'arbitrage se déclenche sur l'**acte**, et non sur le texte de la tâche (#579, parent #573).

Lot final « tests + doc » du chantier : les lots intermédiaires n'ont embarqué que
les tests de leur logique critique — la priorité des trois crans (#580), l'attente
et sa borne (#583), le crédit d'arbitrage (#584), l'asymétrie du fail-safe (#586) —
et ont différé le reste ici. Ce fichier porte ce reste, en trois blocs :

① **le scénario de #568, joué entier et en une fois** (critère du ticket). Les deux
   moitiés existaient, mais dans deux fichiers, sur deux harnais, avec deux plans :
   `tests/test_guardrails.py` prouvait qu'un objectif disant « supprimer » ne classe
   plus rien, `tests/test_permissions.py` qu'un outil `ask` produit bien une demande.
   Aucune des deux ne pouvait dire ce que le ticket demande — que dans **un même
   run**, le mot ne déclenche rien *pendant que* l'acte déclenche, et que l'unique
   demande porte le nom de l'**outil** là où l'ancien régime portait le titre du
   livrable. C'est la régression de #568 et son remède dans la même expérience ;

② **la forme de l'acte** (`maestro.acte`, différé de #581) : `arguments_depuis` est
   une **relecture** — elle ne fait jamais échouer une demande d'arbitrage parce
   qu'une valeur n'avait pas la forme attendue — et chaque valeur est **bornée**,
   sans quoi un `content` de `Write` partirait entier sur le bus et jusqu'à l'écran ;

③ **l'agent qui lève la main** (`maestro.providers.arbitrage`, différé de #582), et
   le fail-safe **sur ce chemin-là** (second critère du ticket). C'est le canal le
   plus récent et le seul qui reparte *vers* l'agent : il n'invente aucun garde-fou,
   il atteint le même `Guardrails.demande_validation`, donc le même refus par défaut.
   Ce qui le distingue est sa **provenance**, portée par un champ — et le ticket
   demande que l'orchestrateur ne puisse jamais approuver à la place d'une personne :
   une demande d'agent ne portant aucun cran, elle retombe sur `humain`, donc le
   canal de l'orchestrateur n'est pas sur son chemin.

Aucun appel réseau : plans constants, fournisseurs factices, dépôts sur répertoire
temporaire. Le harnais est celui de `tests/test_permissions.py` — mêmes doubles,
mêmes aides — plutôt qu'un second à tenir d'accord.
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.acte import ARGUMENT_MAX, arguments_depuis
from maestro.agents.permissions import PermissionStore, Verdict
from maestro.decideur import Decideur
from maestro.engine import OrchestrationEngine
from maestro.engine.guardrails import (
    MOTS_SENSIBLES,
    ORIGINE_AGENT,
    ORIGINE_POLITIQUE,
    DemandeValidation,
    Guardrails,
)
from maestro.orchestrator import Orchestrator
from maestro.providers.arbitrage import (
    NOM_OUTIL,
    NOM_SERVEUR,
    OUTIL_ARBITRAGE,
    RAISON_MANQUANTE,
    motif_refus,
    reponse,
)
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

# --- Harnais ----------------------------------------------------------------------------


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class _Executant(ModelProvider):
    """Exécutant outillé factice : rend son livrable, sans toucher à aucun canal."""

    name = "executant"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls) + 1}"


class AppelleUnOutilAsk(_Executant):
    """Exécutant qui appelle un outil classé `ask` : l'acte est suspendu au vol.

    Le double du hook `PreToolUse` vu du moteur (#583) : l'appel part sur
    `on_arbitrage_acte` avec l'outil, ses arguments et le motif de la politique.
    Il s'arrête là — borner l'attente est le travail du vrai hook, éprouvé dans
    `tests/test_permissions.py`.
    """

    name = "appelle-un-outil-ask"

    #: L'acte : un `rm -rf` réel, qui ne partage **aucun mot** avec le livrable
    #: demandé. C'est ce qui rend le test capable de dire lequel des deux a
    #: déclenché la demande.
    ARGUMENTS = {"command": "rm -rf /srv/donnees"}

    #: La tâche qui **ne commet aucun acte**, reconnue à son prompt. Rédiger un
    #: README ne lance pas de `rm -rf`, et c'est tout l'intérêt de la garder dans
    #: le plan : elle dit « supprimer » d'un bout à l'autre sans rien supprimer.
    SANS_ACTE = "README"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        if self.SANS_ACTE in prompt:
            return await super().run_agent(
                prompt, model=model, system_prompt=system_prompt, workspace=workspace,
                tools=tools, mcp_serveurs=mcp_serveurs, politique=politique,
                on_refus=on_refus, plafond_tours=plafond_tours,
            )
        decision = None if politique is None else politique.decide("Bash")
        if decision is not None and decision.verdict is Verdict.ARBITRAGE:
            approuve, detail = await on_arbitrage_acte(
                "Bash", dict(self.ARGUMENTS), decision.motif
            )
            self.run_calls.append({"arbitrage": (approuve, detail)})
            if not approuve and on_refus is not None:
                on_refus("Bash", motif_refus("Bash", detail))
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique,
            on_refus=on_refus, plafond_tours=plafond_tours,
        )


class LeveLaMain(_Executant):
    """Exécutant qui appelle `demander_arbitrage` : l'agent demande lui-même (#582).

    L'autre canal, et le seul qui reparte *vers* l'agent : une raison en entrée,
    une décision en sortie, que le double conserve pour que le test lise ce que
    l'agent a réellement lu.
    """

    name = "leve-la-main"

    RAISON = "Je m'apprête à vider /srv/donnees, ce que ma tâche ne prévoyait pas."

    def __init__(self, raison: str | None = None) -> None:
        super().__init__()
        self.raison = self.RAISON if raison is None else raison
        self.lu: list[tuple[bool, str]] = []

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        if on_arbitrage is not None:
            self.lu.append(await on_arbitrage(self.raison))
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique,
            on_refus=on_refus, plafond_tours=plafond_tours,
        )


class ValidateurEnregistreur:
    """Le canal humain : répond toujours pareil, et garde ce qu'on lui a soumis."""

    def __init__(self, decision: bool = True) -> None:
        self.decision = decision
        self.demandes: list[DemandeValidation] = []

    def __call__(self, demande: DemandeValidation) -> bool:
        self.demandes.append(demande)
        return self.decision


class _Mouchard:
    """Un canal de décision qui approuverait tout, et qui compte ses appels."""

    def __init__(self) -> None:
        self.vues: list[DemandeValidation] = []

    def __call__(self, demande: DemandeValidation) -> bool:
        self.vues.append(demande)
        return True


def _ecrire_politique(racine: Path, agent: str, politique: dict) -> None:
    """Écrit la politique JSON de `agent` dans le dépôt `racine`."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / f"{agent}.json").write_text(
        json.dumps(politique, ensure_ascii=False), encoding="utf-8"
    )


@pytest.fixture()
def store(tmp_path):
    """Dépôt de politiques vierge, sur répertoire temporaire."""
    return PermissionStore(tmp_path / "permissions")


def _tache(id_: str, titre: str, description: str) -> dict:
    """Une tâche de plan, routée vers le développeur (« backend »)."""
    return {
        "id": id_,
        "titre": titre,
        "description": description,
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": [],
    }


def _moteur(provider, store, guardrails, plan):
    """Boucle branchée sur le dépôt de permissions et les garde-fous donnés."""
    orchestrator = Orchestrator(ConstantProvider(plan), model="claude-opus-4-8")
    return OrchestrationEngine(
        provider, orchestrator, permissions=store, guardrails=guardrails
    )


#: Le plan de #568 : un objectif qui demande une **fonction de suppression**, dont
#: le mot se propage à toutes les descriptions que la décomposition en tire. Sous
#: l'ancien régime, 3 tâches sur 3 en sortaient sensibles — « Rédiger le README »
#: comprise.
PLAN_568 = json.dumps(
    [
        _tache(
            "cli-supprimer",
            "Ajouter la sous-commande supprimer",
            "Implémenter `notes supprimer <id>`, avec suppression définitive en base.",
        ),
        _tache(
            "readme",
            "Rédiger le README",
            "Documenter la sous-commande supprimer une note.",
        ),
    ],
    ensure_ascii=False,
)

OBJECTIF_568 = "Ajouter une sous-commande supprimer une note"


# --- ① Le scénario de #568, joué entier -------------------------------------------------


def test_le_mot_ne_declenche_rien_pendant_que_l_acte_declenche(store):
    """Le critère du ticket, en une seule expérience (#568 → #573).

    Tout dans ce run porte le mot qui suspendait un run entier : l'objectif, les
    deux titres, les deux descriptions. Et un seul des deux agents commet un
    **acte** classé `ask`. À l'arrivée il doit y avoir **exactement une** demande,
    et elle doit venir de l'acte.

    Les deux moitiés comptent l'une pour l'autre, et c'est pourquoi elles sont
    ici plutôt que dans deux fichiers : prouver que le mot ne déclenche plus rien
    ne dit pas que quelque chose déclenche encore, et prouver qu'un outil `ask`
    produit une demande sur un plan anodin ne dit pas qu'un plan saturé de mots
    n'en produit pas trois de plus. Le compte exact — **une**, pas zéro et pas
    trois — est le seul énoncé qui porte le remède *et* la régression.
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": ["Bash"]})
    validateur = ValidateurEnregistreur(decision=True)
    provider = AppelleUnOutilAsk()

    report = asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_568).run(
            OBJECTIF_568, journal=RunJournal(run_id="run-568")
        )
    )

    # La prémisse de l'expérience : **un** acte commis sur les deux tâches. Sans
    # elle, « une demande » pourrait venir d'un double qui n'a agi qu'une fois par
    # hasard — ou de la tâche qui n'agit pas.
    assert len(provider.run_calls) == 1

    # Une seule demande sur les deux tâches, alors que les deux disent « supprimer ».
    assert len(validateur.demandes) == 1
    (demande,) = validateur.demandes

    # Et c'est la tâche qui **agit** qui l'a produite, pas celle qui documente.
    assert demande.task_id == "cli-supprimer"

    # Elle porte l'**acte** : l'outil et ce qu'on lui passe.
    assert demande.outil == "Bash"
    assert demande.arguments == AppelleUnOutilAsk.ARGUMENTS

    # Et sa raison nomme l'outil, jamais le livrable — c'est l'inversion de #568,
    # où « Rédiger le README » se retrouvait au-dessus d'une demande d'arbitrage.
    assert "Bash" in demande.raison
    assert "supprimer" not in demande.raison.lower()

    # Le titre de la tâche voyage toujours, mais comme **contexte** : il dit d'où
    # vient l'acte, il ne prétend plus dire ce qu'on approuve.
    assert demande.titre == "Ajouter la sous-commande supprimer"

    # C'est une règle à nous, pas un aveu de l'agent.
    assert demande.origine == ORIGINE_POLITIQUE

    # Et rien n'a échoué au passage : les deux tâches rendent leur livrable.
    assert len(report.resultats) == 2
    assert all(r.ok for r in report.resultats)


def test_sans_le_moindre_acte_le_meme_plan_ne_demande_rien(store):
    """Le témoin du test précédent : retirer l'acte doit vider le compte.

    Même objectif, même plan, même validateur — seul l'exécutant change, et il ne
    commet aucun acte classé `ask`. Sans ce témoin, « une demande » pourrait aussi
    bien vouloir dire « le mot en a produit une » : c'est lui qui attribue la
    demande à l'acte et à rien d'autre.

    Le validateur **refuse**, à dessein : s'il était consulté, les tâches
    échoueraient. Leur succès est donc ce qui prouve qu'il ne l'a pas été.
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": ["Bash"]})
    validateur = ValidateurEnregistreur(decision=False)

    report = asyncio.run(
        _moteur(_Executant(), store, Guardrails(validateur=validateur), PLAN_568).run(
            OBJECTIF_568, journal=RunJournal(run_id="run-568-temoin")
        )
    )

    assert validateur.demandes == []
    assert all(r.ok for r in report.resultats)


def test_le_regime_d_avant_rendait_bien_trois_taches_sensibles(store):
    """La régression de #568, rejouée sous son propre régime — le piège est réel.

    Un test qui ne montre que l'après laisse ouverte la question « le défaut
    existait-il ? ». On rearme donc la liste de radicaux d'origine et on retrouve
    le compte mesuré : **les deux tâches** classées sensibles, « Rédiger le
    README » comprise, sur un plan dont aucune ne supprime quoi que ce soit.

    C'est aussi ce qui prouve que #585 n'a rien **retiré** : le mécanisme répond
    encore, il n'est simplement plus armé par défaut.
    """
    validateur = ValidateurEnregistreur(decision=True)

    asyncio.run(
        _moteur(
            _Executant(),
            store,
            Guardrails(validateur=validateur, mots_sensibles=MOTS_SENSIBLES),
            PLAN_568,
        ).run(OBJECTIF_568, journal=RunJournal(run_id="run-568-avant"))
    )

    assert len(validateur.demandes) == 2
    assert {d.titre for d in validateur.demandes} == {
        "Ajouter la sous-commande supprimer",
        "Rédiger le README",
    }
    # Et la raison désignait le **mot**, pas un acte : aucune de ces demandes ne
    # portait d'outil, faute d'acte à montrer.
    assert all("mot sensible" in d.raison for d in validateur.demandes)
    assert all(d.outil == "" for d in validateur.demandes)


# --- ② La forme de l'acte (`maestro.acte`, différé de #581) -----------------------------


def test_les_arguments_voyagent_en_texte_cle_par_cle():
    assert arguments_depuis({"command": "rm -rf /srv", "cwd": "/app"}) == {
        "command": "rm -rf /srv",
        "cwd": "/app",
    }


@pytest.mark.parametrize("brut", [None, "rm -rf /srv", 42, ["command"], object()])
def test_ce_qui_n_est_pas_un_objet_rend_un_dict_vide(brut):
    """Régime de **relecture** : ce qui arrive du SDK, du bus ou d'un journal
    rejoué n'a pas à faire échouer une demande d'arbitrage."""
    assert arguments_depuis(brut) == {}


def test_une_valeur_qui_n_est_pas_du_texte_est_rendue_en_texte():
    # `timeout: 120` et `recursive: true` font partie de ce qu'on arbitre : les
    # jeter reviendrait à faire approuver autre chose que ce qui sera exécuté.
    arguments = arguments_depuis({"timeout": 120, "recursive": True, "cible": None})
    assert arguments == {"timeout": "120", "recursive": "True", "cible": "None"}


def test_une_cle_inutilisable_est_ecartee_sans_faire_perdre_les_autres():
    arguments = arguments_depuis({"command": "ls", "": "vide", 7: "entier"})
    assert arguments == {"command": "ls"}


def test_une_valeur_trop_longue_est_bornee_et_le_dit():
    """Sans borne, un `content` de `Write` partirait entier sur le bus, dans la
    projection et jusqu'au WebSocket."""
    arguments = arguments_depuis({"content": "a" * (ARGUMENT_MAX + 500)})
    valeur = arguments["content"]

    assert len(valeur) == ARGUMENT_MAX + 1  # la troncature, plus le signe qui la dit
    assert valeur.endswith("…")
    # Une valeur pile à la borne n'est pas touchée : on ne coupe que ce qui dépasse.
    assert arguments_depuis({"c": "a" * ARGUMENT_MAX})["c"] == "a" * ARGUMENT_MAX


def test_les_sauts_de_ligne_sont_gardes():
    # Contrairement à une ligne d'activité, qui les écrase : un script passé à
    # `Bash` se lit sur plusieurs lignes, et l'aplatir rendrait illisible ce
    # qu'on demande d'approuver.
    script = "cd /srv\nrm -rf donnees\necho fini"
    assert arguments_depuis({"command": script})["command"] == script


def test_le_nombre_de_cles_n_est_pas_borne():
    # Il est celui du schéma de l'outil, et rien n'en produit mille.
    beaucoup = {f"cle{n}": str(n) for n in range(200)}
    assert len(arguments_depuis(beaucoup)) == 200


# --- ③ L'agent qui lève la main (différé de #582), et le fail-safe sur ce chemin --------


def test_l_outil_porte_le_nom_reserve_de_son_serveur():
    # C'est sous cette forme qu'une politique de permissions (#110) le désigne.
    assert OUTIL_ARBITRAGE == f"mcp__{NOM_SERVEUR}__{NOM_OUTIL}"
    assert OUTIL_ARBITRAGE == "mcp__maestro__demander_arbitrage"


@pytest.mark.parametrize("approuve", [True, False])
def test_la_reponse_dit_la_decision_son_motif_et_la_suite_a_donner(approuve):
    """Sans la troisième moitié, un agent approuvé peut hésiter et un agent
    refusé peut réessayer."""
    texte = reponse(approuve, "refusée par le validateur humain")

    assert ("approuvé" in texte) is approuve
    assert "refusée par le validateur humain" in texte  # le motif n'est jamais réécrit
    assert "poursuis" in texte.lower()


def test_une_raison_vide_n_est_pas_un_refus():
    """La nuance qui a coûté une relecture à #582 : rien n'a été soumis à
    personne, donc lui servir la réponse d'un refus l'enverrait renoncer à une
    action sur laquelle nul n'a été consulté. Le seul geste utile est de
    rappeler l'outil."""
    assert "rappelle cet outil" in RAISON_MANQUANTE
    # Et surtout pas la consigne d'un refus, qui dit de poursuivre sans l'action.
    assert "ne réalise pas" not in RAISON_MANQUANTE.lower()
    assert RAISON_MANQUANTE != reponse(False, "refusée par le validateur humain")


def test_la_demande_de_l_agent_porte_sa_provenance(store):
    """Ce qui distingue les deux canaux est un **champ**, pas une tournure.

    Les deux aboutissent au même validateur ; sans le champ, le journal les
    rendrait indiscernables et une déclaration d'agent finirait par se lire
    comme une classification — or elles n'ont pas la même valeur de preuve.
    """
    validateur = ValidateurEnregistreur(decision=True)
    provider = LeveLaMain()

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_568).run(
            OBJECTIF_568, journal=RunJournal(run_id="run-agent")
        )
    )

    assert [d.origine for d in validateur.demandes] == [ORIGINE_AGENT] * 2
    # La raison **est** l'action que l'agent décrit, et elle est préfixée pour que
    # la provenance survive là où seul le texte voyage.
    assert all(LeveLaMain.RAISON in d.raison for d in validateur.demandes)
    assert all("agent" in d.raison for d in validateur.demandes)
    # L'agent a bien reçu la décision, pas seulement le validateur la demande.
    assert all(approuve for approuve, _ in provider.lu)


def test_sans_validateur_la_demande_de_l_agent_est_refusee(store):
    """Le fail-safe est **littéralement** le même : c'est le même code qui répond.

    Rien du mécanisme de #48 n'a bougé pour accueillir ce canal — la demande part
    au `Guardrails` de l'exécuteur, donc au refus par défaut (EF-08, ENF-04).
    """
    provider = LeveLaMain()

    report = asyncio.run(
        _moteur(provider, store, Guardrails(), PLAN_568).run(
            OBJECTIF_568, journal=RunJournal(run_id="run-agent-sans-validateur")
        )
    )

    assert all(not approuve for approuve, _ in provider.lu)
    assert all("aucun validateur humain configuré" in detail for _, detail in provider.lu)
    # Et un refus ne condamne pas la tâche : ce serait punir la prudence de
    # l'agent qui a levé la main.
    assert all(r.ok for r in report.resultats)


def test_l_orchestrateur_ne_repond_pas_a_la_place_d_un_humain_sur_ce_canal(store):
    """Le second critère du ticket, sur le canal le plus récent.

    Une demande d'agent ne porte **aucun cran** — elle n'en a pas à porter, elle
    ne vient pas d'une politique — donc elle retombe sur `humain`, le défaut. La
    garantie n'est alors pas une vérification qu'on aurait pu oublier d'écrire :
    le canal de l'orchestrateur n'est pas sur le chemin, et il n'est pas consulté
    du tout. On câble un orchestrateur qui approuverait n'importe quoi, et on
    vérifie qu'il n'a rien vu.
    """
    orchestrateur = _Mouchard()
    provider = LeveLaMain()

    asyncio.run(
        _moteur(provider, store, Guardrails(orchestrateur=orchestrateur), PLAN_568).run(
            OBJECTIF_568, journal=RunJournal(run_id="run-agent-orchestrateur")
        )
    )

    assert all(not approuve for approuve, _ in provider.lu)
    assert orchestrateur.vues == []


def test_le_cran_par_defaut_d_une_demande_est_humain():
    """L'énoncé sous lequel tiennent les deux tests précédents : *un cran non
    précisé escalade, il ne s'auto-approuve pas*. Un défaut à `auto` ferait d'un
    oubli un laissez-passer — le défaut symétrique de celui que #573 répare."""
    demande = DemandeValidation(
        task_id="t1",
        titre="Rédiger le README",
        description="RAS.",
        agent="dev",
        role="Développeur",
        raison="arbitrage demandé par l'agent dev : je vide /srv.",
        origine=ORIGINE_AGENT,
    )

    assert demande.decideur == Decideur.HUMAIN


def test_l_issue_de_l_arbitrage_de_l_agent_est_consignee_au_journal(store):
    """Une demande d'agent laisse sa trace, et sous un nom qui la nomme : c'est
    l'autre chemin par lequel la provenance atteint quelqu'un qui lit."""
    journal = RunJournal(run_id="run-agent-journal")

    asyncio.run(
        _moteur(
            LeveLaMain(), store, Guardrails(validateur=ValidateurEnregistreur()), PLAN_568
        ).run(OBJECTIF_568, journal=journal)
    )

    traces = [r for r in journal.records if "Arbitrage demandé par l'agent" in r.nom]
    assert len(traces) == 2
