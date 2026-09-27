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
   Ce qui le distingue est sa **provenance**, portée par un champ — et une demande
   d'agent ne portant **aucun cran** (elle ne vient pas d'une politique), elle
   retombe sur `humain`, le défaut.

   ⚠ Ce chemin portait un test de plus, du temps où un cran `orchestrateur`
   existait : on y câblait une machine qui approuve tout pour vérifier qu'elle
   n'était **pas consultée**. #715 a retiré ce cran et son canal, donc il n'y a
   plus rien à ne pas consulter — la garantie tient faute de sujet, et ce qui la
   portait vraiment reste éprouvé deux fois ici : le défaut d'une demande d'agent
   (`test_le_cran_par_defaut_d_une_demande_est_humain`) et le fail-safe sans
   validateur juste au-dessus.

④ **l'accord que l'objectif a déjà donné** (#1198, `Task.acte_accorde`). C'est la
   moitié que #1149 n'avait pas portée : le plan avait cessé d'ajouter une tâche
   « faire valider », mais l'exécution redemandait une personne **à chaque
   commande** — S1 du banc, le 2026-09-22, cinq demandes écartées à 240 s, dossier
   intact. Le bloc éprouve l'accord *et ses bords*, parce que c'est un garde-fou
   qu'on ouvre : il ne vaut que pour l'outil d'exécution, que pour la tâche qui le
   déclare, jamais contre une liste `deny`, et jamais en silence.

⑤ **la portée franchie, sur toute la chaîne** (#1278). Le hook sortait bien de
   la portée un appel qui quittait le projet et désignait une personne ; l'arbitre
   du moteur **redemandait** le cran à la politique, qui répondait `auto`, et le
   garde-fou accordait d'office. Chaque maillon, éprouvé seul, avait raison. Ce
   bloc joue donc le **vrai** hook, le vrai arbitre et le vrai garde-fou dans un
   même run — c'est la seule expérience qui voie ce qui se perdait entre eux.

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
from maestro.engine.executor import (
    STATUT_ARBITRAGE_OUTIL,
    STATUT_REFUS_OUTIL,
    SUFFIXE_ETAPE_REFUS,
)
from maestro.engine.guardrails import (
    DETAIL_AUTO,
    MOTS_SENSIBLES,
    ORIGINE_AGENT,
    ORIGINE_POLITIQUE,
    DemandeValidation,
    Guardrails,
)
from maestro.orchestrator import Orchestrator
from maestro.providers import claude as claude_mod
from maestro.providers.arbitrage import (
    NOM_OUTIL,
    NOM_SERVEUR,
    OUTIL_ARBITRAGE,
    RAISON_MANQUANTE,
    motif_approbation,
    motif_refus,
    reponse,
)
from maestro.providers.base import ModelProvider
from maestro.sandbox.en_place import portee_de
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
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
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
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
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
                "Bash", dict(self.ARGUMENTS), decision.motif, decision.decideur
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
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
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


def _tache(id_: str, titre: str, description: str, acte_accorde: str = "") -> dict:
    """Une tâche de plan, routée vers le développeur (« backend »).

    `acte_accorde` (#1198) reste **absent** quand il est vide : c'est le cas
    courant du schéma, et une clé posée à la chaîne vide dirait autre chose
    qu'une clé omise.
    """
    tache = {
        "id": id_,
        "titre": titre,
        "description": description,
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": [],
    }
    if acte_accorde:
        tache["acte_accorde"] = acte_accorde
    return tache


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


# --- ④ L'accord que l'objectif a déjà donné (#1198) -------------------------------------


#: L'acte, écrit comme l'objectif le nomme — c'est ce texte-là qui doit se
#: retrouver au journal, sans reformulation par le moteur.
ACTE_ACCORDE = "supprimer tout le contenu du dossier du projet"

#: Les commandes de l'acte, dans l'ordre où l'agent les joue — les lectures, puis
#: le geste. Trois et non une : le défaut mesuré le 2026-09-22 n'est pas « une
#: demande de trop », c'est **une par commande** — le lire sur un seul appel
#: laisserait croire qu'un accord unique en début de tâche aurait suffi.
#:
#: Trois commandes **distinctes**, et c'est nécessaire : la mémoire de
#: délibération (#584) sert la décision déjà rendue à un acte identique, si bien
#: qu'un `ls -la` rejoué ne composerait pas de seconde demande. Le témoin
#: compterait alors deux demandes pour trois commandes et dirait à moitié ce
#: qu'il prétend dire.
COMMANDES = (
    {"command": "ls -la"},
    {"command": "cat lisez-moi.txt"},
    {"command": "rm -rf notes lisez-moi.txt rapport.csv && ls -la"},
)

#: Le plan de S1 : **une** tâche qui agit (#1149), et une seconde qui construit,
#: pour que le même run porte les deux régimes. Le compte des demandes ne veut
#: rien dire sans elle — zéro demande pourrait aussi bien signifier « le canal
#: est débranché ».
TITRE_ACTE = "Vider le dossier du projet"
TITRE_CONSTRUIT = "Écrire le guide de reprise"


def _plan_s1(*, accorde: bool) -> str:
    return json.dumps(
        [
            _tache(
                "vider-le-dossier",
                TITRE_ACTE,
                "Supprimer tout le contenu de la racine, hors périmètre exclu.",
                acte_accorde=ACTE_ACCORDE if accorde else "",
            ),
            _tache(
                "guide",
                TITRE_CONSTRUIT,
                "Écrire un guide de reprise du projet.",
            ),
        ],
        ensure_ascii=False,
    )


OBJECTIF_S1 = "Vide le dossier de ce projet : supprime tout son contenu."


class JoueSesCommandes(_Executant):
    """Exécutant qui joue une liste d'actes, **comme le hook les jouerait**.

    Le double de `maestro.providers.claude._hook_permissions` vu du moteur : il
    demande son verdict à la politique, part sur `on_arbitrage_acte` quand elle
    dit `ARBITRAGE`, et trace **les deux issues** par `on_refus` — l'approbation
    avec `motif_approbation`, le refus avec `motif_refus`. Reproduire la trace
    compte ici autant que le verdict : ce qui est en jeu est un garde-fou qu'on
    ouvre, et un accord qui passerait sans ligne au journal serait un trou.

    Les actes se choisissent sur le **titre** de la tâche, que le tableau noir
    porte en première ligne (`_build_task_description`) : c'est ce qui permet au
    même run de faire agir une tâche et construire l'autre.
    """

    name = "joue-ses-commandes"

    def __init__(self, actes: dict[str, tuple[tuple[str, dict[str, str]], ...]]) -> None:
        super().__init__()
        self.actes = actes
        #: Ce que l'agent a réellement obtenu, acte par acte : (titre, outil,
        #: approuvé, détail). C'est la moitié que le compte des demandes ne dit
        #: pas — un acte peut passer sans demande, c'est tout l'objet du lot.
        self.issues: list[tuple[str, str, bool, str]] = []

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
        plafond_tours=None, projet=None,
    ):
        for titre, actes in self.actes.items():
            if titre not in prompt:
                continue
            for outil, arguments in actes:
                decision = None if politique is None else politique.decide(outil)
                if decision is None or decision.verdict is Verdict.PASSE:
                    continue
                if decision.verdict is Verdict.REFUS:
                    if on_refus is not None:
                        on_refus(outil, decision.motif)
                    continue
                approuve, detail = await on_arbitrage_acte(
                    outil, dict(arguments), decision.motif, decision.decideur
                )
                self.issues.append((titre, outil, approuve, detail))
                if on_refus is not None:
                    on_refus(
                        outil,
                        motif_approbation(outil, detail)
                        if approuve
                        else motif_refus(outil, detail),
                    )
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique,
            on_refus=on_refus, plafond_tours=plafond_tours,
        )


def _bash(titre: str) -> dict[str, tuple[tuple[str, dict[str, str]], ...]]:
    """Les trois commandes de l'acte, jouées par la tâche `titre`."""
    return {titre: tuple(("Bash", dict(arguments)) for arguments in COMMANDES)}


def test_l_acte_que_l_objectif_a_nomme_ne_redemande_personne(store):
    """Le critère du ticket : *l'action approuvée dans le fil s'exécute sans
    redemander un humain à chaque commande* (#1198).

    Le run est celui de S1 : `Bash` en `ask`/`humain` — le cran qu'une équipe
    proposée pose sur un projet qui ne déclare aucune commande
    (`maestro.equipe.proposition`) —, et une tâche qui agit sur un acte que
    l'objectif nomme. Les trois commandes passent, et **aucune** n'a composé de
    demande : c'est le compte, pas le verdict, qui porte le remède. Sous le
    régime d'avant, ce même run rendait trois demandes ; la personne n'en
    tranchait qu'une et les deux autres expiraient à la borne d'arbitrage.
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": {"Bash": "humain"}})
    validateur = ValidateurEnregistreur(decision=True)
    provider = JoueSesCommandes(_bash(TITRE_ACTE))
    journal = RunJournal(run_id="run-1198-accorde")

    report = asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), _plan_s1(accorde=True)).run(
            OBJECTIF_S1, journal=journal
        )
    )

    # La prémisse : les trois commandes ont bien été soumises au canal.
    assert len(provider.issues) == len(COMMANDES)
    # Et les trois sont passées, sans que personne ne soit dérangé une seule fois.
    assert all(approuve for _, _, approuve, _ in provider.issues)
    assert validateur.demandes == []

    # Le détail **nomme l'acte accordé**, mot pour mot : c'est ce qu'on relira.
    assert all(ACTE_ACCORDE in detail for _, _, _, detail in provider.issues)

    # Et chaque appel a laissé sa ligne au journal : un accord n'est pas un
    # silence. Statut d'arbitrage, jamais de refus.
    traces = [
        r for r in journal.records if r.etape == f"vider-le-dossier{SUFFIXE_ETAPE_REFUS}"
    ]
    assert len(traces) == len(COMMANDES)
    assert all(t.statut == STATUT_ARBITRAGE_OUTIL for t in traces)
    assert all(ACTE_ACCORDE in t.sortie for t in traces)

    assert all(r.ok for r in report.resultats)


def test_sans_l_accord_la_meme_tache_redemande_a_chaque_commande(store):
    """Le témoin, et la mesure du 2026-09-22 rejouée : **une demande par commande**.

    Même plan, même politique, même exécutant — seule la clé `acte_accorde`
    disparaît. Sans ce témoin, « aucune demande » ci-dessus pourrait vouloir dire
    que le canal n'est pas câblé, et le lot n'aurait rien prouvé du défaut qu'il
    répare.
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": {"Bash": "humain"}})
    validateur = ValidateurEnregistreur(decision=True)
    provider = JoueSesCommandes(_bash(TITRE_ACTE))

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), _plan_s1(accorde=False)).run(
            OBJECTIF_S1, journal=RunJournal(run_id="run-1198-temoin")
        )
    )

    assert len(validateur.demandes) == len(COMMANDES)
    assert {d.task_id for d in validateur.demandes} == {"vider-le-dossier"}
    assert [d.arguments for d in validateur.demandes] == [dict(c) for c in COMMANDES]


def test_l_accord_ne_vaut_que_pour_la_tache_qui_le_declare(store):
    """L'accord est posé sur **une** tâche, pas sur le run.

    Le même run porte une tâche qui agit sous accord et une tâche qui construit.
    La seconde n'a rien reçu : son `Bash` compose une demande, comme avant. C'est
    ce qui sépare « l'acte que l'objectif nomme » de « tout ce que ce run fera ».
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": {"Bash": "humain"}})
    validateur = ValidateurEnregistreur(decision=True)
    actes = _bash(TITRE_ACTE)
    actes[TITRE_CONSTRUIT] = (("Bash", {"command": "npm run build"}),)
    provider = JoueSesCommandes(actes)

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), _plan_s1(accorde=True)).run(
            OBJECTIF_S1, journal=RunJournal(run_id="run-1198-portee")
        )
    )

    (demande,) = validateur.demandes
    assert demande.task_id == "guide"
    assert demande.arguments == {"command": "npm run build"}


def test_l_accord_ne_couvre_pas_un_autre_outil_soumis_a_arbitrage(store):
    """« Vide le dossier » n'a jamais accordé un message dans Slack.

    L'accord porte sur l'**outil d'exécution**, celui par lequel l'acte se fait —
    c'est aussi le seul sur lequel une équipe proposée pose un cran. Un autre
    outil classé `ask` garde son humain sur la tâche qui agit, et
    `core/permissions/devops.json` en livre un.
    """
    _ecrire_politique(
        store.racine,
        "developpeur",
        {"ask": {"Bash": "humain", "mcp__slack": "humain"}},
    )
    validateur = ValidateurEnregistreur(decision=True)
    actes = _bash(TITRE_ACTE)
    actes[TITRE_ACTE] = actes[TITRE_ACTE] + (
        ("mcp__slack__send_message", {"text": "dossier vidé"}),
    )
    provider = JoueSesCommandes(actes)

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), _plan_s1(accorde=True)).run(
            OBJECTIF_S1, journal=RunJournal(run_id="run-1198-autre-outil")
        )
    )

    (demande,) = validateur.demandes
    assert demande.outil == "mcp__slack__send_message"
    assert demande.task_id == "vider-le-dossier"


def test_l_accord_ne_leve_pas_la_liste_deny(store):
    """Un accord n'est pas un laissez-passer : `deny` l'emporte, comme toujours.

    C'est la priorité de `PolitiqueOutils` (#580), et elle se juge **avant** que
    quoi que ce soit n'atteigne ce canal. Le vérifier ici plutôt que de s'en
    remettre au test de priorité : ce qui est en cause n'est pas l'ordre des
    listes, c'est qu'un champ du plan ne puisse pas s'y substituer.
    """
    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"], "ask": {"Bash": "humain"}})
    validateur = ValidateurEnregistreur(decision=True)
    provider = JoueSesCommandes(_bash(TITRE_ACTE))
    journal = RunJournal(run_id="run-1198-deny")

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), _plan_s1(accorde=True)).run(
            OBJECTIF_S1, journal=journal
        )
    )

    # Aucun acte n'a atteint le canal, et aucun n'est passé.
    assert provider.issues == []
    assert validateur.demandes == []
    traces = [
        r for r in journal.records if r.etape == f"vider-le-dossier{SUFFIXE_ETAPE_REFUS}"
    ]
    assert len(traces) == len(COMMANDES)
    assert all(t.statut == STATUT_REFUS_OUTIL for t in traces)


def test_l_acte_accorde_voyage_avec_la_tache(store):
    """Le champ est de la **donnée durable** : il survit à l'aller-retour JSON.

    Le plan est sérialisé et relu à chaque frontière — file de tâches, workflow
    durable, API. Un champ qui ne ferait pas l'aller-retour laisserait l'accord
    sur le quai, et la tâche redemanderait une personne de l'autre côté.
    """
    from maestro.orchestrator.schema import Task, validate_plan

    (tache, _) = validate_plan(json.loads(_plan_s1(accorde=True)))
    assert tache.acte_accorde == ACTE_ACCORDE
    assert Task.from_dict(tache.to_dict()).acte_accorde == ACTE_ACCORDE

    # Et son absence reste une absence : pas de clé posée à la chaîne vide.
    (sans, _) = validate_plan(json.loads(_plan_s1(accorde=False)))
    assert sans.acte_accorde == ""
    assert "acte_accorde" not in sans.to_dict()


# --- ⑤ La portée franchie, sur toute la chaîne (#1278) ---------------------------------


class PasseParLeVraiHook(_Executant):
    """Exécutant qui soumet ses commandes au **vrai** hook du fournisseur Claude.

    Là où `JoueSesCommandes` rejoue la logique du hook, celui-ci l'emprunte :
    `_hook_permissions`, armé comme `run_agent` l'arme — la portée de l'espace de
    travail (`portee_de`) et les deux canaux que le moteur lui passe. C'est ce qui
    fait de ce double un banc de la **chaîne** et non d'un maillon.
    """

    name = "passe-par-le-vrai-hook"

    def __init__(self, commandes: tuple[str, ...]) -> None:
        super().__init__()
        self.commandes = commandes
        #: Ce que le hook a rendu, commande par commande : `{}` laisse passer.
        self.sorties: list[tuple[str, dict]] = []

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None, on_decision=None,
        credit_arbitrage=None,
        on_courrier=None, on_question=None,
        plafond_tours=None, projet=None,
    ):
        hook = claude_mod._hook_permissions(
            politique, on_refus, on_arbitrage_acte, portee=portee_de(workspace, projet)
        )
        for commande in self.commandes:
            sortie = await hook(
                {"tool_name": "Bash", "tool_input": {"command": commande}}, "tu-1", None
            )
            self.sorties.append((commande, sortie))
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique,
            on_refus=on_refus, plafond_tours=plafond_tours,
        )


#: Le plan d'une tâche qui monte une maquette : un seul agent, un seul acte à juger
#: par commande.
PLAN_MAQUETTE = json.dumps(
    [_tache("maquette", "Monter la maquette", "Capturer la page d'accueil du projet.")],
    ensure_ascii=False,
)

#: La politique qu'une équipe proposée pose sur son `dev` depuis #1226, et celle
#: de l'agent `interface` du run `3fe501fc0878` : `Bash` en `ask` + `auto`, borné
#: au projet.
POLITIQUE_CADREE = {"ask": {"Bash": "auto"}, "portees": {"Bash": "projet"}}


def test_hors_de_la_portee_une_personne_tranche_sur_toute_la_chaine(store):
    """Le deuxième défaut du ticket, et son critère : *un appel hors portée sous
    cran `auto` crée une demande humaine sur toute la chaîne*.

    Le geste est celui du run : écrire dans `/tmp`, hors du projet. Il remonte à
    une personne, avec le motif de hors-portée — c'est lui qu'elle lira dans la
    Control Tower. Le geste d'à côté, dans le projet, ne dérange personne : sans
    ce témoin, « une demande » pourrait venir de n'importe quel appel.
    """
    _ecrire_politique(store.racine, "developpeur", POLITIQUE_CADREE)
    validateur = ValidateurEnregistreur(decision=True)
    provider = PasseParLeVraiHook(
        ("mkdir -p .maestro/maquette", "cd /tmp && mkdir -p edge-shot")
    )
    journal = RunJournal(run_id="run-1278")

    asyncio.run(
        _moteur(provider, store, Guardrails(validateur=validateur), PLAN_MAQUETTE).run(
            "Monter la maquette du projet", journal=journal
        )
    )

    (demande,) = validateur.demandes
    assert demande.arguments == {"command": "cd /tmp && mkdir -p edge-shot"}
    assert demande.decideur == Decideur.HUMAIN
    assert "hors de la portée" in demande.raison and "sort du" in demande.raison
    # La personne a approuvé : l'appel passe, et les deux commandes aussi.
    assert [sortie for _, sortie in provider.sorties] == [{}, {}]


def test_la_trace_d_une_approbation_nomme_qui_l_a_rendue(store):
    """Le troisième défaut : la trace se contredisait — « approuvé à
    l'arbitrage humain — accordée d'office, personne n'a été sollicité ».

    Sur la même chaîne, l'appel hors du projet est approuvé **par une
    personne** et la trace le dit ; l'appel dans le projet passe par `auto`, et
    sa trace ne prétend aucun arbitrage humain.
    """
    _ecrire_politique(store.racine, "developpeur", POLITIQUE_CADREE)
    provider = PasseParLeVraiHook(
        ("mkdir -p .maestro/maquette", "cd /tmp && mkdir -p edge-shot")
    )
    journal = RunJournal(run_id="run-1278-trace")

    asyncio.run(
        _moteur(
            provider, store, Guardrails(validateur=ValidateurEnregistreur(True)), PLAN_MAQUETTE
        ).run("Monter la maquette du projet", journal=journal)
    )

    dans, dehors = [r for r in journal.records if r.etape == f"maquette{SUFFIXE_ETAPE_REFUS}"]
    assert "personne n'a été sollicité" in dans.sortie
    assert "humain" not in dans.sortie
    assert "validateur humain" in dehors.sortie
    assert "personne n'a été sollicité" not in dehors.sortie
    # Et le nom de l'étape, que le banc lit pour savoir qui a tranché (#586), dit
    # la même chose que le motif — pas le cran que la politique donne à l'outil.
    assert dans.nom.startswith("Outil arbitré (auto)")
    assert dehors.nom.startswith("Outil arbitré (humain)")


def test_une_approbation_d_office_ne_se_dit_pas_humaine():
    """La même règle au niveau du texte, pour tout producteur : l'issue qu'aucune
    personne n'a prononcée ne se trace jamais comme un arbitrage humain."""
    motif = motif_approbation("Bash", DETAIL_AUTO)

    assert "humain" not in motif
    assert DETAIL_AUTO in motif
    assert "validateur humain" in motif_approbation("Bash", "approuvée par le validateur humain")
