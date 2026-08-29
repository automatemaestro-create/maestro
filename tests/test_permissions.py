"""Tests de la politique de permissions par agent et par outil (ticket #110, lot #107).

Aucun appel réseau : dépôts sur répertoires temporaires, fournisseurs factices,
SDK monkeypatché. Fait partie du lot final « tests + doc » du renforcement
sécurité (#102) — couvre le critère « politique de permissions appliquée et
violations tracées » :

① **sémantique de la politique** (`PolitiqueOutils`) : `deny` l'emporte
   toujours ; `allow` vide = tout permis, non vide = liste fermée ; une entrée
   couvre l'outil exact ou tout ce qu'elle préfixe aux frontières `__`
   (`mcp__slack` couvre `mcp__slack__send_message`, pas `mcp__slackbot__x`) ;
①bis **le troisième cran** (`ask`, #580) : la priorité `deny` > `ask` >
   `allow` éprouvée **cran par cran** — un outil des deux listes fermées est
   refusé et non arbitré, un outil `ask` absent d'une liste `allow` fermée est
   arbitré et non refusé (sans quoi fermer sa liste suffirait à rendre le cran
   du milieu lettre morte) ; un outil classé `ask` n'est **pas** interdit et
   reste monté (`autorise`/`filtre_outils`/`serveur_autorise`) ; un fichier
   écrit avant ce lot se relit sous le régime d'hier (champ absent = vide) ;
② **dépôt** (`PermissionStore`) : validation à la lecture (JSON illisible,
   forme inattendue, entrée malformée — une politique douteuse est refusée
   avec sa cause, jamais appliquée à moitié), nom d'agent verrouillé, racine
   configurable (`MAESTRO_PERMISSIONS_DIR`) ;
③ **application au montage** (runtime, #110) : les outils intégrés refusés
   sont retirés de la session avant son ouverture, un serveur MCP entièrement
   refusé n'est jamais monté (ses secrets ne sont même pas résolus), un refus
   individuel laisse le serveur monté (le refus au vol s'en charge) ;
④ **application par le moteur** : politique invalide = échec de tâche propre
   avant toute exécution ; **violation tracée** au journal (étape
   `<tâche>:refus-outil`, statut `refus_outil` — le fil temps réel Control
   Tower la voit) sans condamner le run ; dépôt relu à chaud à chaque tâche ;
⑤ **refus au vol** (fournisseur Claude) : le hook PreToolUse — seul point de
   contrôle sous `bypassPermissions` — refuse un appel interdit avec son
   motif, signale la violation via `on_refus`, et n'échoue jamais lui-même
   (un traçage en échec est avalé) ;
①ter **qui décide** (`ask` + décideur, #586) : chaque entrée porte son cran —
   `auto`, `orchestrateur` ou `humain` —, une chaîne nue et une liste `ask`
   d'avant ce lot valant `humain` (*un cran non précisé escalade, il ne
   s'auto-approuve pas*) ; le décideur n'est renseigné que sur un `ARBITRAGE` ;
   une entrée reste son nom d'outil (préfixage, égalité, JSON) ; un cran inconnu
   **dans un fichier** est refusé avec sa cause et les trois valeurs admises,
   quand un cran inconnu **relu du dehors** retombe sur `humain` ;
⑥ **arbitrage au vol** (#583) : le même hook suspend un appel classé `ask`,
   et c'est **l'attente et sa borne** qui sont éprouvées ici — le reste de la
   couverture du chantier #573 est différé au lot final (#579). Trois choses
   qui ne doivent pas se défaire : l'attente reste **sous** la borne annoncée
   au runtime quoi qu'on règle (donc à l'expiration c'est nous qui répondons,
   jamais le CLI par échéance) ; la demande expirée n'est **pas annulée** — la
   décision tardive arrive et est absorbée sans casser quoi que ce soit ; et
   les **deux fail-safe** tiennent, un outil `ask` sans canal d'arbitrage ou
   avec un canal en panne étant refusé et jamais approuvé par défaut. Depuis
   #586 s'y ajoute le cran `auto`, qui ne **désigne personne** : le hook le
   tranche lui-même, sans canal et sans attente, mais en laissant la trace qui
   le distingue d'un `allow` ;
⑦ **la décision au journal** (#586) : l'étape `:refus-outil` nomme le décideur
   dans son **nom** — le champ qui porte une provenance sans qu'on la déduise —,
   et le tient de la politique plutôt que du texte du motif. S'y joue aussi le
   pendant de bout en bout du garde-fou : un orchestrateur qui approuverait tout
   ne fait pas passer un acte classé `humain`.
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.agents import QA_PROFILE, AgentRuntime
from maestro.agents.mcp import ServeurMcp
from maestro.agents.permissions import (
    DecisionOutil,
    EntreeArbitrage,
    PermissionStore,
    PolitiqueOutils,
    Verdict,
)
from maestro.config import ConfigError, Settings
from maestro.decideur import Decideur, decideur_depuis
from maestro.engine import OrchestrationEngine
from maestro.engine.executor import (
    STATUT_ARBITRAGE_OUTIL,
    STATUT_REFUS_OUTIL,
    SUFFIXE_ETAPE_REFUS,
)
from maestro.engine.guardrails import Guardrails
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider, Credentials
from maestro.providers import claude as claude_mod
from maestro.providers.arbitrage import (
    BORNE_HOOK_S,
    MARGE_MIN_S,
    BornesArbitrage,
    motif_approbation,
    motif_refus,
)
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal

# --- Fournisseurs factices --------------------------------------------------------------


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class MontageEnregistreur(ModelProvider):
    """Exécutant outillé factice : enregistre outils, serveurs et politique reçus."""

    name = "montage-enregistreur"

    def __init__(self) -> None:
        self.run_calls: list[dict[str, object]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        credit_arbitrage=None,
        plafond_tours=None, projet=None,
    ):
        self.run_calls.append(
            {
                "tools": tuple(tools),
                "mcp_serveurs": tuple(mcp_serveurs),
                "politique": politique,
                "on_refus": on_refus,
            }
        )
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return f"OUTILLE #{len(self.run_calls)}"


class ViolateurProvider(MontageEnregistreur):
    """Exécutant factice qui tente un outil interdit : simule le refus au vol du SDK.

    C'est le comportement du fournisseur réel (hook PreToolUse) vu du moteur :
    l'appel interdit est refusé, la violation signalée via `on_refus`, et
    l'exécution **poursuit** — elle rend son livrable malgré le refus.
    """

    name = "violateur"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        credit_arbitrage=None,
        plafond_tours=None, projet=None,
    ):
        if politique is not None and not politique.autorise("Bash") and on_refus is not None:
            on_refus("Bash", politique.raison_refus("Bash"))
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique, on_refus=on_refus,
            plafond_tours=plafond_tours,
        )


class ArbitreProvider(MontageEnregistreur):
    """Exécutant factice qui appelle un outil classé `ask` : simule l'arbitrage au vol.

    C'est le comportement du fournisseur réel (hook PreToolUse) vu du moteur :
    l'appel est suspendu, la demande part sur `on_arbitrage_acte`, et l'issue est
    tracée par `on_refus` — le canal qui existe depuis #110. Le double s'arrête
    là : borner l'attente est le travail du vrai hook, éprouvé à part.

    ⚠ `on_arbitrage_acte` et non `on_arbitrage` (#582) : celui-ci relaie une
    demande que l'agent a formulée, celui-là un acte qu'on a intercepté.
    """

    name = "arbitre"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        credit_arbitrage=None,
        plafond_tours=None, projet=None,
    ):
        decision = None if politique is None else politique.decide("Bash")
        if decision is not None and decision.verdict is Verdict.ARBITRAGE:
            approuve, detail = await on_arbitrage_acte(
                "Bash", {"command": "rm -rf /srv"}, decision.motif
            )
            self.run_calls.append({"arbitrage": (approuve, detail)})
            if on_refus is not None:
                on_refus(
                    "Bash",
                    motif_approbation("Bash", detail)
                    if approuve
                    else motif_refus("Bash", detail),
                )
        return await super().run_agent(
            prompt, model=model, system_prompt=system_prompt, workspace=workspace,
            tools=tools, mcp_serveurs=mcp_serveurs, politique=politique, on_refus=on_refus,
            plafond_tours=plafond_tours,
        )


# --- Aides ------------------------------------------------------------------------------


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


def _plan_json(competences=("backend",)):
    """Plan factice d'une tâche unique, routée par ses compétences requises."""
    return json.dumps(
        [
            {
                "id": "tache-unique",
                "titre": "Tâche unique",
                "description": "Réaliser la tâche.",
                "competences_requises": list(competences),
                "format_sortie": "Texte",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )


def _moteur(provider, store):
    """Boucle d'orchestration branchée sur le dépôt de permissions (planification factice)."""
    planner = ConstantProvider(_plan_json())
    orchestrator = Orchestrator(planner, model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, permissions=store)


# --- ① Sémantique de la politique -------------------------------------------------------


def test_sans_liste_tout_est_permis():
    politique = PolitiqueOutils()
    assert politique.autorise("Bash")
    assert politique.autorise("mcp__slack__send_message")


def test_deny_l_emporte_toujours_sur_allow():
    politique = PolitiqueOutils(allow=("Bash",), deny=("Bash",))
    assert not politique.autorise("Bash")


def test_allow_non_vide_est_une_liste_fermee():
    politique = PolitiqueOutils(allow=("Read", "Grep"))
    assert politique.autorise("Read")
    assert not politique.autorise("Bash")
    assert not politique.autorise("mcp__slack__send_message")


def test_une_entree_couvre_par_prefixe_aux_frontieres_de_segments():
    politique = PolitiqueOutils(deny=("mcp__slack",))
    # Le serveur entier est couvert : chacun de ses outils est refusé…
    assert not politique.autorise("mcp__slack")
    assert not politique.autorise("mcp__slack__send_message")
    # …mais jamais en plein mot : un autre serveur au nom voisin reste permis.
    assert politique.autorise("mcp__slackbot__envoyer")


def test_filtre_outils_retire_les_refuses():
    politique = PolitiqueOutils(deny=("Bash", "Write"))
    assert politique.filtre_outils(("Read", "Write", "Bash", "Grep")) == ("Read", "Grep")


def test_serveur_entierement_refuse_n_est_pas_montable():
    assert not PolitiqueOutils(deny=("mcp__slack",)).serveur_autorise("slack")


def test_allow_fermee_sans_outil_du_serveur_ne_le_monte_pas():
    politique = PolitiqueOutils(allow=("Read", "mcp__tickets__creer"))
    assert politique.serveur_autorise("tickets")
    assert not politique.serveur_autorise("slack")


def test_refus_individuel_laisse_le_serveur_monte():
    # Un seul outil du serveur est refusé : le serveur reste monté, c'est le
    # refus au vol (hook PreToolUse) qui applique l'interdit.
    politique = PolitiqueOutils(deny=("mcp__slack__chat_delete",))
    assert politique.serveur_autorise("slack")
    assert not politique.autorise("mcp__slack__chat_delete")
    assert politique.autorise("mcp__slack__send_message")


def test_raison_refus_nomme_l_outil_et_la_liste_en_cause():
    politique = PolitiqueOutils(allow=("Read",), deny=("Bash",))
    assert "deny" in politique.raison_refus("Bash")
    assert "'Bash'" in politique.raison_refus("Bash")
    assert "allow" in politique.raison_refus("Write")
    # Le motif invite l'agent à poursuivre : le refus n'est jamais fatal au run.
    assert "Poursuis la tâche" in politique.raison_refus("Write")


def test_aller_retour_dict_preserve_la_politique():
    politique = PolitiqueOutils(allow=("Read",), ask=("Write",), deny=("mcp__slack",))
    assert PolitiqueOutils.from_dict(politique.to_dict()) == politique


# --- ①bis Le troisième cran : `ask` (#580) ----------------------------------------------


def test_le_verdict_a_trois_valeurs_et_porte_son_motif():
    politique = PolitiqueOutils(ask=("Bash",), deny=("Write",))

    assert politique.decide("Read") == DecisionOutil(Verdict.PASSE)
    assert politique.decide("Bash").verdict is Verdict.ARBITRAGE
    assert politique.decide("Write").verdict is Verdict.REFUS
    # Le motif nomme l'outil et la liste en cause, comme `raison_refus`…
    assert "'Bash'" in politique.decide("Bash").motif
    assert "ask" in politique.decide("Bash").motif
    assert "deny" in politique.decide("Write").motif
    # …et il est vide quand rien ne s'oppose à l'appel : il n'y a rien à dire.
    assert politique.decide("Read").motif == ""


def test_deny_l_emporte_sur_ask():
    # Le cran le plus fermé gagne : un outil des deux listes est refusé, pas arbitré.
    politique = PolitiqueOutils(ask=("Bash",), deny=("Bash",))

    assert politique.decide("Bash").verdict is Verdict.REFUS
    assert "deny" in politique.decide("Bash").motif
    assert not politique.autorise("Bash")


def test_ask_l_emporte_sur_une_liste_allow_fermee():
    # Sans cette priorité, fermer sa liste `allow` suffirait à rendre `ask`
    # lettre morte : l'outil serait refusé avant d'avoir pu être arbitré.
    politique = PolitiqueOutils(allow=("Read",), ask=("Bash",))

    assert politique.decide("Bash").verdict is Verdict.ARBITRAGE
    assert politique.decide("Read").verdict is Verdict.PASSE
    # Le reste de la liste fermée reste refusé : `ask` déborde `allow`, ne l'ouvre pas.
    assert politique.decide("Write").verdict is Verdict.REFUS


def test_un_outil_classe_ask_n_est_pas_interdit_et_reste_monte():
    # `autorise` répond « non interdit » : un outil retiré de la session
    # n'atteindrait jamais le point de contrôle censé le suspendre.
    politique = PolitiqueOutils(allow=("Read",), ask=("Bash", "mcp__slack__chat_delete"))

    assert politique.autorise("Bash")
    assert politique.filtre_outils(("Read", "Bash", "Write")) == ("Read", "Bash")
    assert politique.serveur_autorise("slack")


def test_ask_couvre_par_prefixe_comme_les_deux_autres_listes():
    politique = PolitiqueOutils(ask=("mcp__slack",))

    assert politique.decide("mcp__slack__send_message").verdict is Verdict.ARBITRAGE
    # Jamais en plein mot : un serveur au nom voisin passe sans arbitrage.
    assert politique.decide("mcp__slackbot__envoyer").verdict is Verdict.PASSE


def test_une_politique_sans_ask_se_relit_a_l_identique():
    # Champ absent = liste vide = régime d'avant #580, au bit près.
    ancienne = {"allow": ["Read"], "deny": ["Bash"]}

    politique = PolitiqueOutils.from_dict(ancienne)

    assert politique == PolitiqueOutils(allow=("Read",), deny=("Bash",))
    assert politique.ask == ()
    assert politique.decide("Read").verdict is Verdict.PASSE
    assert politique.decide("Bash").verdict is Verdict.REFUS


def test_to_dict_porte_toujours_les_trois_listes():
    # `ask` sort en **objet** depuis #586 : une seule forme en écriture, celle
    # qui porte l'entrée *et* son décideur, même quand toutes sont au défaut.
    assert PolitiqueOutils().to_dict() == {"allow": [], "ask": {}, "deny": []}


# --- ①ter Qui décide, par entrée `ask` (#586) -------------------------------------------


def test_une_entree_ask_porte_son_decideur_et_le_defaut_est_humain():
    politique = PolitiqueOutils(
        ask=(
            EntreeArbitrage("Bash", Decideur.ORCHESTRATEUR),
            EntreeArbitrage("mcp__slack__send_message", Decideur.AUTO),
            "Write",  # chaîne nue : le cran non précisé escalade, il ne s'auto-approuve pas
        )
    )

    assert politique.decide("Bash").decideur is Decideur.ORCHESTRATEUR
    assert politique.decide("mcp__slack__send_message").decideur is Decideur.AUTO
    assert politique.decide("Write").decideur is Decideur.HUMAIN
    # Le motif le dit aussi : c'est lui que le journal consigne et que l'écran rend.
    assert "orchestrateur" in politique.decide("Bash").motif


def test_le_decideur_n_est_renseigne_que_sur_un_arbitrage():
    # Pendant exact du motif vide sur `PASSE` : un appel qu'on laisse passer ou
    # qu'on refuse d'office n'est soumis à personne, il n'a pas de décideur.
    politique = PolitiqueOutils(ask=("Bash",), deny=("Write",))

    assert politique.decide("Read").decideur is None
    assert politique.decide("Write").decideur is None
    assert politique.decideur("Bash") is Decideur.HUMAIN


def test_une_entree_ask_reste_son_nom_d_outil():
    # `EntreeArbitrage` est une chaîne : le préfixage aux frontières `__`, la
    # comparaison de deux politiques et la sérialisation JSON continuent de
    # fonctionner sans rien savoir du cran.
    politique = PolitiqueOutils(ask=(EntreeArbitrage("mcp__slack", Decideur.AUTO),))

    assert politique.ask == ("mcp__slack",)
    assert politique.decide("mcp__slack__send_message").decideur is Decideur.AUTO
    assert politique.decide("mcp__slackbot__envoyer").verdict is Verdict.PASSE
    assert json.dumps(politique.to_dict()) == json.dumps(
        {"allow": [], "ask": {"mcp__slack": "auto"}, "deny": []}
    )


def test_la_premiere_entree_ask_qui_couvre_l_outil_donne_son_cran():
    # Ordre du fichier, comme pour `allow`/`deny` : l'auteur d'une politique met
    # son cas particulier d'abord, il n'a pas de règle de précision à deviner.
    politique = PolitiqueOutils(
        ask=(
            EntreeArbitrage("mcp__slack__chat_delete", Decideur.HUMAIN),
            EntreeArbitrage("mcp__slack", Decideur.AUTO),
        )
    )

    assert politique.decide("mcp__slack__chat_delete").decideur is Decideur.HUMAIN
    assert politique.decide("mcp__slack__send_message").decideur is Decideur.AUTO


def test_l_aller_retour_dict_preserve_les_decideurs():
    politique = PolitiqueOutils(
        allow=("Read",),
        ask=(EntreeArbitrage("Bash", Decideur.ORCHESTRATEUR), "Write"),
        deny=("mcp__slack",),
    )

    relue = PolitiqueOutils.from_dict(politique.to_dict())

    assert relue == politique
    assert relue.decide("Bash").decideur is Decideur.ORCHESTRATEUR
    assert relue.decide("Write").decideur is Decideur.HUMAIN


# --- ② Dépôt : validation à la lecture --------------------------------------------------


def test_lire_une_politique_valide(store):
    _ecrire_politique(
        store.racine, "qa", {"allow": ["Read", "mcp__tickets"], "deny": ["Bash"]}
    )

    politique = store.lire("qa")

    assert politique == PolitiqueOutils(allow=("Read", "mcp__tickets"), deny=("Bash",))


def test_lire_une_politique_qui_porte_ask(store):
    _ecrire_politique(
        store.racine, "qa", {"allow": ["Read"], "ask": ["Bash"], "deny": ["Write"]}
    )

    politique = store.lire("qa")

    assert politique == PolitiqueOutils(allow=("Read",), ask=("Bash",), deny=("Write",))


def test_un_fichier_ecrit_avant_ask_se_relit_sous_le_regime_d_hier(store):
    _ecrire_politique(store.racine, "qa", {"allow": ["Read"], "deny": ["Bash"]})

    politique = store.lire("qa")

    assert politique == PolitiqueOutils(allow=("Read",), deny=("Bash",))
    assert politique.ask == ()


@pytest.mark.parametrize("entree", ["", "outil interdit", "mcp__", 42])
def test_entree_ask_malformee_refusee_en_bloc(store, entree):
    # `ask` est validée comme ses deux voisines : jamais appliquée en partie.
    _ecrire_politique(store.racine, "qa", {"ask": ["Read", entree]})

    with pytest.raises(ValueError, match="entrée ask"):
        store.lire("qa")


def test_lire_une_politique_qui_nomme_ses_decideurs(store):
    _ecrire_politique(
        store.racine, "qa", {"ask": {"Bash": "orchestrateur", "Write": "auto"}}
    )

    politique = store.lire("qa")

    assert politique.decide("Bash").decideur is Decideur.ORCHESTRATEUR
    assert politique.decide("Write").decideur is Decideur.AUTO


def test_une_liste_ask_se_relit_sous_le_cran_par_defaut(store):
    # Le fichier d'avant #586, au bit près : la forme liste ne portait aucun
    # cran, et ce qui n'en porte pas attend une personne.
    _ecrire_politique(store.racine, "qa", {"ask": ["Bash"]})

    politique = store.lire("qa")

    assert politique == PolitiqueOutils(ask=("Bash",))
    assert politique.decide("Bash").decideur is Decideur.HUMAIN


@pytest.mark.parametrize("cran", ["humains", "AUTO", "", 42, None])
def test_un_decideur_inconnu_est_refuse_avec_sa_cause(store, cran):
    # Une politique de garde-fou qu'on ne sait pas lire ne s'applique jamais à
    # moitié — le repli tolérant existe, mais pour ce qui se relit après coup
    # (`decideur_depuis`) et ne peut plus être corrigé.
    _ecrire_politique(store.racine, "qa", {"ask": {"Bash": cran}})

    with pytest.raises(ValueError, match="décideur"):
        store.lire("qa")


def test_le_message_d_un_decideur_inconnu_nomme_les_trois_crans(store):
    # Sans les valeurs admises, la seule façon de corriger le fichier est
    # d'aller les chercher dans le code.
    _ecrire_politique(store.racine, "qa", {"ask": {"Bash": "chef"}})

    with pytest.raises(ValueError) as capture:
        store.lire("qa")

    for cran in ("auto", "orchestrateur", "humain"):
        assert cran in str(capture.value)


@pytest.mark.parametrize("brut", ["dieu", "", "AUTO", None, 42])
def test_un_cran_relu_du_dehors_retombe_sur_humain(brut):
    # Régime de **relecture** : ce qui vient d'un journal rejoué ou d'un
    # producteur plus récent n'a pas à faire échouer un arbitrage. Le défaut est
    # le seul qui soit sûr — une valeur inconnue escalade, elle n'ouvre rien.
    assert decideur_depuis(brut) is Decideur.HUMAIN


def test_agent_sans_politique_rend_none(store):
    # Pas de fichier = pas de politique = tout permis (comportement historique).
    assert store.lire("qa") is None


def test_les_listes_absentes_valent_vides(store):
    _ecrire_politique(store.racine, "qa", {})
    assert store.lire("qa") == PolitiqueOutils()


def test_les_entrees_sont_dedoublonnees(store):
    _ecrire_politique(store.racine, "qa", {"deny": ["Bash", "Bash", "Write"]})
    assert store.lire("qa").deny == ("Bash", "Write")


def test_json_illisible_refuse_avec_la_cause(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text("{pas du json", encoding="utf-8")

    with pytest.raises(ValueError, match="illisible.*'qa'"):
        store.lire("qa")


def test_forme_inattendue_refusee(store):
    store.racine.mkdir(parents=True)
    (store.racine / "qa.json").write_text('["pas", "un", "objet"]', encoding="utf-8")

    with pytest.raises(ValueError, match="allow.*deny"):
        store.lire("qa")


def test_liste_non_liste_refusee(store):
    _ecrire_politique(store.racine, "qa", {"allow": "Bash"})

    with pytest.raises(ValueError, match="allow doit être une liste"):
        store.lire("qa")


@pytest.mark.parametrize("entree", ["", "outil interdit", "mcp__", 42])
def test_entree_malformee_refusee_en_bloc(store, entree):
    # Une politique fautive est refusée avec sa cause — jamais appliquée en partie.
    _ecrire_politique(store.racine, "qa", {"deny": ["Read", entree]})

    with pytest.raises(ValueError, match="entrée deny"):
        store.lire("qa")


def test_nom_d_agent_verrouille_pas_de_traversee_de_chemin(store):
    with pytest.raises(ValueError, match="nom d'agent invalide"):
        store.lire("../evasion")


def test_agents_liste_les_politiques_stockees(store):
    _ecrire_politique(store.racine, "qa", {})
    _ecrire_politique(store.racine, "devops", {})
    (store.racine / "Pas-Un-Agent.json").write_text("{}", encoding="utf-8")

    assert store.agents() == ("devops", "qa")


def test_racine_configurable_via_permissions_dir(tmp_path):
    class _Settings:
        permissions_dir = str(tmp_path / "depot-permissions")

    assert PermissionStore.default(_Settings()).racine == tmp_path / "depot-permissions"


def test_les_politiques_versionnees_du_depot_sont_valides():
    # Garde du dépôt Git : les politiques commitées (core/permissions/) restent
    # lisibles et valides — une régression de format casserait les vrais runs.
    depot = PermissionStore(Path(__file__).resolve().parents[1] / "core" / "permissions")
    for agent in depot.agents():
        depot.lire(agent)  # validée à la lecture : lire() lèverait sinon


# --- ③ Application au montage (runtime) -------------------------------------------------


def test_les_outils_refuses_sont_retires_de_la_session():
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute("Vérifie le livrable", politique=PolitiqueOutils(deny=("Bash",)))
    )

    (appel,) = provider.run_calls
    # L'agent ne voit jamais l'outil refusé : retiré avant l'ouverture de session.
    assert "Bash" not in appel["tools"]
    assert "Read" in appel["tools"]


def test_allow_fermee_ne_monte_que_les_outils_cites():
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(runtime.execute("Vérifie", politique=PolitiqueOutils(allow=("Read", "Grep"))))

    (appel,) = provider.run_calls
    assert appel["tools"] == ("Read", "Grep")


def test_serveur_refuse_jamais_monte_et_secrets_jamais_resolus(monkeypatch):
    # Le serveur refusé porte une référence ${VAR} irrésoluble : si la politique
    # ne l'écartait pas *avant* la résolution, l'exécution échouerait en
    # McpServerUnavailable. Elle passe : ses secrets n'ont jamais été demandés.
    monkeypatch.delenv("MAESTRO_TEST_MCP_TOKEN", raising=False)
    interdit = ServeurMcp(
        nom="interdit", type="stdio", commande="python",
        env={"TOKEN": "${MAESTRO_TEST_MCP_TOKEN}"},
    )
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute(
            "Vérifie",
            mcp_serveurs=(interdit,),
            politique=PolitiqueOutils(deny=("mcp__interdit",)),
        )
    )

    (appel,) = provider.run_calls
    assert appel["mcp_serveurs"] == ()


def test_refus_individuel_monte_le_serveur_et_transmet_la_politique():
    serveur = ServeurMcp(nom="slack", type="stdio", commande="npx")
    politique = PolitiqueOutils(deny=("mcp__slack__chat_delete",))
    vu: list[tuple[str, str]] = []
    provider = MontageEnregistreur()
    runtime = AgentRuntime(provider, QA_PROFILE)

    asyncio.run(
        runtime.execute(
            "Vérifie",
            mcp_serveurs=(serveur,),
            politique=politique,
            on_refus=lambda outil, raison: vu.append((outil, raison)),
        )
    )

    (appel,) = provider.run_calls
    # Serveur monté malgré le refus individuel : le refus au vol s'en chargera —
    # politique et canal de traçage sont transmis au fournisseur pour ça.
    assert [s.nom for s in appel["mcp_serveurs"]] == ["slack"]
    assert appel["politique"] is politique
    assert appel["on_refus"] is not None


# --- ④ Application par le moteur : échec propre, traçage, application à chaud -----------


def test_politique_invalide_est_un_echec_de_tache_propre(store):
    store.racine.mkdir(parents=True)
    (store.racine / "developpeur.json").write_text("{pas du json", encoding="utf-8")
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    (resultat,) = rapport.resultats
    # Validation à la lecture : la cause exacte est consignée, l'agent n'a
    # jamais exécuté — on n'exécute pas sous une politique douteuse.
    assert not resultat.ok
    assert "politique de permissions illisible" in (resultat.erreur or "")
    assert provider.run_calls == []


def test_la_violation_est_tracee_au_journal_sans_condamner_le_run(store):
    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"]})
    provider = ViolateurProvider()
    journal = RunJournal(run_id="run-violation")

    rapport = asyncio.run(_moteur(provider, store).run("Objectif", journal=journal))

    # Le refus n'est jamais fatal : la tâche a rendu son livrable.
    (resultat,) = rapport.resultats
    assert resultat.ok
    # La violation est consignée : étape dédiée `<tâche>:refus-outil` (le pont
    # Control Tower la mue en activité d'agent, visible au fil temps réel).
    (refus,) = [
        r for r in journal.records if r.etape == f"tache-unique{SUFFIXE_ETAPE_REFUS}"
    ]
    assert refus.statut == STATUT_REFUS_OUTIL
    assert refus.entree == "Bash"
    assert "deny" in refus.sortie
    assert refus.agent == "developpeur"


def test_agent_sans_politique_execute_tout_permis(store):
    provider = MontageEnregistreur()

    rapport = asyncio.run(_moteur(provider, store).run("Objectif"))

    assert rapport.resultats[0].ok
    appel = provider.run_calls[-1]
    assert appel["politique"] is None
    assert "Bash" in appel["tools"]


def test_la_politique_s_applique_a_chaud_a_la_tache_suivante(store):
    # Même contrat que les playbooks (#78) et les déclarations MCP (#104) : le
    # dépôt est relu à chaque tâche, une politique ajoutée vaut pour la suivante.
    provider = MontageEnregistreur()
    moteur = _moteur(provider, store)

    asyncio.run(moteur.run("Objectif"))
    assert "Bash" in provider.run_calls[-1]["tools"]

    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"]})

    asyncio.run(moteur.run("Objectif"))
    assert "Bash" not in provider.run_calls[-1]["tools"]


# --- ⑤ Refus au vol : le hook PreToolUse du fournisseur Claude --------------------------


def _hook(politique, on_refus=None):
    return claude_mod._hook_permissions(politique, on_refus)


def test_le_hook_laisse_passer_un_appel_permis():
    hook = _hook(PolitiqueOutils(deny=("Bash",)))
    assert asyncio.run(hook({"tool_name": "Read"}, None, None)) == {}


def test_le_hook_refuse_un_appel_interdit_avec_son_motif():
    vu: list[tuple[str, str]] = []
    hook = _hook(
        PolitiqueOutils(deny=("mcp__slack__chat_delete",)),
        lambda outil, raison: vu.append((outil, raison)),
    )

    sortie = asyncio.run(hook({"tool_name": "mcp__slack__chat_delete"}, "tu-1", None))

    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "mcp__slack__chat_delete" in decision["permissionDecisionReason"]
    # La violation est signalée au canal de traçage de l'exécuteur.
    assert vu == [("mcp__slack__chat_delete", decision["permissionDecisionReason"])]


def test_un_tracage_en_echec_n_empeche_pas_le_refus():
    # L'observation ne casse jamais l'exécution observée : `on_refus` qui lève
    # est avalé, le refus est rendu quand même.
    def _tracage_casse(outil, raison):
        raise RuntimeError("journal indisponible")

    hook = _hook(PolitiqueOutils(deny=("Bash",)), _tracage_casse)

    sortie = asyncio.run(hook({"tool_name": "Bash"}, None, None))

    assert sortie["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_un_appel_sans_nom_d_outil_est_laisse_au_flux_normal():
    hook = _hook(PolitiqueOutils(deny=("Bash",)))
    assert asyncio.run(hook({}, None, None)) == {}


class _FakeTextBlock:
    def __init__(self, text):
        self.text = text


class _FakeAssistantMessage:
    def __init__(self, content):
        self.content = content


def _run_agent_capture_options(monkeypatch, *, politique, arbitrage=None):
    """Lance `run_agent` sur un `query` factice et capture les options SDK."""
    vu: dict[str, object] = {}

    async def fake_query(*, prompt, options):
        vu["hooks"] = options.hooks
        yield _FakeAssistantMessage([_FakeTextBlock("Livré.")])

    monkeypatch.setattr(claude_mod, "query", fake_query)
    monkeypatch.setattr(claude_mod, "AssistantMessage", _FakeAssistantMessage)
    monkeypatch.setattr(claude_mod, "TextBlock", _FakeTextBlock)
    provider = ClaudeProvider(Credentials(), arbitrage=arbitrage)
    asyncio.run(
        provider.run_agent(
            "Fais", model="claude-sonnet-5", workspace=Path("."), tools=("Read",),
            politique=politique,
        )
    )
    return vu


def test_run_agent_arme_le_hook_quand_une_politique_est_fournie(monkeypatch, tmp_path):
    vu = _run_agent_capture_options(monkeypatch, politique=PolitiqueOutils(deny=("Bash",)))
    assert vu["hooks"] is not None
    assert "PreToolUse" in vu["hooks"]


def test_run_agent_sans_politique_n_arme_aucun_hook(monkeypatch, tmp_path):
    vu = _run_agent_capture_options(monkeypatch, politique=None)
    assert vu["hooks"] is None


# --- ⑥ Arbitrage au vol : l'attente, sa borne, et qui rend le verdict (#583) ------------


def _hook_arbitre(on_arbitrage_acte, on_refus=None, bornes=None):
    """Le hook armé sur une politique qui met `Bash` en arbitrage."""
    return claude_mod._hook_permissions(
        PolitiqueOutils(ask=("Bash",)), on_refus, on_arbitrage_acte, bornes
    )


def _appelle(hook, entree=None):
    """Joue le hook sur un appel de `Bash` et rend sa sortie."""
    return asyncio.run(
        hook({"tool_name": "Bash", "tool_input": entree or {}}, "tu-1", None)
    )


def _motif(sortie):
    """Le motif du `deny` rendu par le hook (échoue si ce n'en est pas un)."""
    decision = sortie["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    return decision["permissionDecisionReason"]


# ⑥a — les bornes et leur invariant


def test_l_attente_par_defaut_reste_sous_la_borne_annoncee_au_runtime():
    # L'invariant du ticket : notre attente finit **avant** l'échéance du hook,
    # donc le verdict d'un appel sensible ne revient jamais au CLI.
    bornes = BornesArbitrage()
    assert bornes.attente_effective <= bornes.borne_hook_s - MARGE_MIN_S


def test_une_attente_reglee_au_dela_de_la_borne_est_raccourcie_pas_honoree():
    # Le fail-safe ne dépend pas de la cohérence de deux nombres réglés
    # séparément : c'est le `min` qui le tient, pas la discipline de qui règle.
    bornes = BornesArbitrage(attente_s=1000.0, borne_hook_s=100.0)
    assert bornes.attente_effective == 100.0 - MARGE_MIN_S


def test_une_attente_sous_la_borne_est_honoree_telle_quelle():
    bornes = BornesArbitrage(attente_s=30.0, borne_hook_s=100.0)
    assert bornes.attente_effective == 30.0


def test_une_borne_qui_ne_laisse_pas_la_marge_est_une_erreur_de_config():
    # Sous ce seuil, plus aucun arbitrage n'aboutirait : mieux vaut casser au
    # câblage que le découvrir sur l'appel qu'on voulait faire trancher.
    with pytest.raises(ConfigError, match="marge"):
        BornesArbitrage(borne_hook_s=MARGE_MIN_S)
    with pytest.raises(ConfigError):
        BornesArbitrage(attente_s=0)


def test_les_bornes_se_reglent_par_la_config(monkeypatch):
    monkeypatch.setenv("MAESTRO_ARBITRAGE_ATTENTE", "30")
    monkeypatch.setenv("MAESTRO_ARBITRAGE_BORNE_HOOK", "90")

    bornes = BornesArbitrage.from_settings(Settings.from_env())

    assert (bornes.attente_s, bornes.borne_hook_s) == (30.0, 90.0)


def test_sans_reglage_les_bornes_sont_celles_du_module(monkeypatch):
    monkeypatch.delenv("MAESTRO_ARBITRAGE_ATTENTE", raising=False)
    monkeypatch.delenv("MAESTRO_ARBITRAGE_BORNE_HOOK", raising=False)

    assert BornesArbitrage.from_settings(Settings.from_env()) == BornesArbitrage()


def test_une_borne_illisible_ne_retombe_pas_en_silence_sur_le_defaut(monkeypatch):
    # Un réglage de garde-fou qu'on ne sait pas lire ne se remplace pas par un
    # défaut : c'est le seul endroit où l'écart entre ce qu'on croit avoir réglé
    # et ce qui s'applique ne se verrait jamais.
    monkeypatch.setenv("MAESTRO_ARBITRAGE_ATTENTE", "cinq minutes")

    with pytest.raises(ConfigError, match="MAESTRO_ARBITRAGE_ATTENTE"):
        BornesArbitrage.from_settings(Settings.from_env())


def test_la_borne_du_hook_est_posee_explicitement_dans_le_matcher(monkeypatch, tmp_path):
    # Le fait mesuré du ticket : sans ce `timeout`, la borne serait celle du SDK
    # (60 s), c'est-à-dire une valeur qu'on subit au lieu de la choisir.
    vu = _run_agent_capture_options(monkeypatch, politique=PolitiqueOutils(ask=("Bash",)))
    (matcher,) = vu["hooks"]["PreToolUse"]
    assert matcher.timeout == BORNE_HOOK_S

    vu = _run_agent_capture_options(
        monkeypatch,
        politique=PolitiqueOutils(ask=("Bash",)),
        arbitrage=BornesArbitrage(attente_s=10.0, borne_hook_s=42.0),
    )
    (matcher,) = vu["hooks"]["PreToolUse"]
    assert matcher.timeout == 42.0


# ⑥b — les trois issues


def test_un_appel_arbitre_approuve_passe_et_laisse_sa_trace():
    vu: list[tuple[str, str]] = []

    async def arbitrage(outil, arguments, motif):
        return True, "approuvée par le validateur humain"

    sortie = _appelle(_hook_arbitre(arbitrage, lambda o, m: vu.append((o, m))))

    # Sortie vide : l'appel n'a pas besoin d'être *forcé*, seulement de ne plus
    # être suspendu — sous `bypassPermissions` il n'y a rien à lever.
    assert sortie == {}
    ((outil, motif),) = vu
    assert outil == "Bash"
    assert "approuvé" in motif


def test_un_appel_arbitre_refuse_rend_un_deny_motive_et_l_agent_poursuit():
    vu: list[tuple[str, str]] = []

    async def arbitrage(outil, arguments, motif):
        return False, "refusée par le validateur humain"

    motif = _motif(_appelle(_hook_arbitre(arbitrage, lambda o, m: vu.append((o, m)))))

    assert "'Bash'" in motif
    assert "refusé à l'arbitrage humain" in motif
    # Un refus propre n'est jamais un échec de tâche : le modèle lit la consigne.
    assert "Poursuis la tâche" in motif
    assert vu == [("Bash", motif)]


def test_a_l_expiration_c_est_nous_qui_repondons_et_la_demande_reste_en_vol():
    # Le cœur du ticket. La borne du hook (60 s par défaut côté SDK) ne doit
    # jamais trancher à notre place : à l'expiration de **notre** attente, le
    # hook rend un `deny` motivé — et la demande, elle, continue son chemin.
    async def scenario():
        tranche = asyncio.Event()
        etats: list[str] = []
        vu: list[tuple[str, str]] = []

        async def arbitrage(outil, arguments, motif):
            try:
                await tranche.wait()
            except asyncio.CancelledError:
                etats.append("annulée")
                raise
            etats.append("aboutie")
            return True, "décision tardive"

        hook = _hook_arbitre(
            arbitrage,
            lambda o, m: vu.append((o, m)),
            BornesArbitrage(attente_s=0.01, borne_hook_s=10.0),
        )
        sortie = await hook({"tool_name": "Bash", "tool_input": {}}, "tu-1", None)
        # La décision arrive *après* que le hook a répondu : elle doit pouvoir
        # aboutir (la demande n'a pas été annulée) sans rien casser.
        tranche.set()
        await asyncio.sleep(0.02)
        return sortie, list(vu), list(etats)

    sortie, vu, etats = asyncio.run(scenario())

    motif = _motif(sortie)
    assert "arbitrage en cours" in motif
    assert "reste en attente" in motif
    assert vu == [("Bash", motif)]
    # « encore en attente » et non « annulée » : c'est ce qui distingue la
    # troisième issue d'un refus, et le seuil que #584 reprendra.
    assert etats == ["aboutie"]


def test_les_trois_issues_passent_toutes_par_le_canal_on_refus():
    reponses = iter([(True, "oui"), (False, "non")])

    async def tranche(outil, arguments, motif):
        return next(reponses)

    async def jamais(outil, arguments, motif):
        await asyncio.Event().wait()
        raise AssertionError("inatteignable")  # pragma: no cover

    vu: list[tuple[str, str]] = []
    trace = lambda outil, motif: vu.append((outil, motif))  # noqa: E731
    _appelle(_hook_arbitre(tranche, trace))
    _appelle(_hook_arbitre(tranche, trace))
    _appelle(
        _hook_arbitre(jamais, trace, BornesArbitrage(attente_s=0.01, borne_hook_s=10.0))
    )

    assert [outil for outil, _ in vu] == ["Bash"] * 3
    assert "approuvé" in vu[0][1]
    assert "refusé à l'arbitrage" in vu[1][1]
    assert "arbitrage en cours" in vu[2][1]


def test_la_demande_porte_l_outil_et_ses_arguments():
    # C'est tout l'objet du parent #573 : ce qu'on fait trancher est l'acte, pas
    # le titre de la tâche. Les arguments arrivent sous la forme de `maestro.acte`.
    vu: list[tuple[str, dict[str, str], str]] = []

    async def arbitrage(outil, arguments, motif):
        vu.append((outil, arguments, motif))
        return True, "ok"

    _appelle(
        _hook_arbitre(arbitrage),
        {"command": "rm -rf /srv", "timeout": 120},
    )

    (outil, arguments, motif) = vu[0]
    assert outil == "Bash"
    assert arguments == {"command": "rm -rf /srv", "timeout": "120"}
    assert "ask" in motif


# ⑥c — les deux fail-safe


def test_un_outil_a_arbitrer_sans_canal_est_refuse_jamais_approuve():
    # « Sans validateur humain, un acte classé humain est refusé » (#573) :
    # laisser passer serait l'exact inverse du cran qu'on vient d'ajouter.
    motif = _motif(_appelle(_hook_arbitre(None)))
    assert "aucun canal d'arbitrage" in motif


def test_un_canal_d_arbitrage_en_panne_ne_laisse_rien_passer():
    async def casse(outil, arguments, motif):
        raise RuntimeError("bus injoignable")

    motif = _motif(_appelle(_hook_arbitre(casse)))
    assert "bus injoignable" in motif


def test_un_transport_qui_coupe_n_est_pas_pris_pour_une_attente_qui_expire():
    # Les deux causes lèvent le même type. Rendre le motif de l'une pour l'autre
    # enverrait chercher une décision humaine là où c'est le transport qui est
    # tombé — et la demande, elle, n'est pas « encore en attente ».
    async def coupe(outil, arguments, motif):
        raise TimeoutError("lecture Redis expirée")

    motif = _motif(_appelle(_hook_arbitre(coupe)))

    assert "lecture Redis expirée" in motif
    assert "arbitrage en cours" not in motif


def test_l_arbitrage_ne_change_rien_aux_deux_autres_crans():
    # Le hook rend toujours les verdicts de #110 : ce lot en ajoute un troisième,
    # il n'en remplace aucun.
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(ask=("Bash",), deny=("Write",)), None, None
    )
    assert asyncio.run(hook({"tool_name": "Read"}, None, None)) == {}
    refus = asyncio.run(hook({"tool_name": "Write"}, None, None))
    assert "deny" in refus["hookSpecificOutput"]["permissionDecision"]


# ⑥e — le cran `auto` ne dépend d'aucun canal (#586)


def test_le_cran_auto_passe_sans_canal_d_arbitrage_et_laisse_sa_trace():
    """Sans lui, `auto` serait refusé faute de trouver quelqu'un à déranger.

    La configuration est celle de tout appelant hors Control Tower : aucun
    `on_arbitrage_acte` câblé. Un outil classé `humain` y est refusé — c'est le
    fail-safe éprouvé plus haut —, mais `auto` ne **désigne personne** : le
    refuser reviendrait à refuser un acte dont la politique dit qu'il n'a aucun
    décideur à consulter.

    La trace est l'autre moitié, et la seule chose qui distingue ce cran d'un
    `allow` : l'appel passe, mais il se voit.
    """
    vues: list[tuple[str, str]] = []
    hook = claude_mod._hook_permissions(
        PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),)),
        lambda outil, motif: vues.append((outil, motif)),
        None,
    )

    assert _appelle(hook) == {}
    assert [outil for outil, _ in vues] == ["Bash"]
    assert "auto" in vues[0][1]


def test_le_cran_auto_n_attend_pas_le_canal_meme_quand_il_existe():
    # Le hook tranche `auto` lui-même : le canal est câblé et n'est pas appelé,
    # donc aucune attente n'est ouverte pour un acte que personne n'a à trancher.
    appels: list[str] = []

    async def arbitrage(outil, arguments, motif):  # pragma: no cover - ne doit pas courir
        appels.append(outil)
        return True, "approuvée par le validateur humain"

    hook = claude_mod._hook_permissions(
        PolitiqueOutils(ask=(EntreeArbitrage("Bash", Decideur.AUTO),)), None, arbitrage
    )

    assert _appelle(hook) == {}
    assert appels == []


# ⑥d — l'issue vue du moteur : une trace qui ne se fait pas passer pour un refus


def _moteur_arbitre(provider, store, *, accord):
    """Moteur dont le validateur humain rend toujours `accord`."""
    return OrchestrationEngine(
        provider,
        Orchestrator(ConstantProvider(_plan_json()), model="claude-opus-4-8"),
        permissions=store,
        guardrails=Guardrails(validateur=lambda demande: accord),
    )


@pytest.mark.parametrize("accord", [True, False])
def test_l_issue_d_un_arbitrage_est_consignee_sous_son_propre_statut(store, accord):
    _ecrire_politique(store.racine, "developpeur", {"ask": ["Bash"]})
    provider = ArbitreProvider()
    journal = RunJournal(run_id="run-arbitrage")

    rapport = asyncio.run(
        _moteur_arbitre(provider, store, accord=accord).run("Objectif", journal=journal)
    )

    # L'arbitrage n'est jamais fatal : la tâche a rendu son livrable.
    assert rapport.resultats[0].ok
    (trace,) = [r for r in journal.records if r.etape == f"tache-unique{SUFFIXE_ETAPE_REFUS}"]
    # Statut à part : le fil rend `refus_outil` par « s'est vu refuser un outil »,
    # phrase fausse pour un appel qu'une personne vient d'approuver.
    assert trace.statut == STATUT_ARBITRAGE_OUTIL
    assert trace.statut != STATUT_REFUS_OUTIL
    assert trace.nom.startswith("Outil arbitré")
    assert trace.entree == "Bash"
    assert ("approuvé" if accord else "refusé") in trace.sortie


def test_un_refus_de_politique_garde_son_statut_d_avant(store):
    # Le statut de #110 ne bouge pas : c'est bien deux natures d'issue, pas un
    # renommage de l'ancienne.
    _ecrire_politique(store.racine, "developpeur", {"deny": ["Bash"]})
    journal = RunJournal(run_id="run-refus")

    asyncio.run(_moteur(ViolateurProvider(), store).run("Objectif", journal=journal))

    (trace,) = [r for r in journal.records if r.etape == f"tache-unique{SUFFIXE_ETAPE_REFUS}"]
    assert trace.statut == STATUT_REFUS_OUTIL
    assert trace.nom.startswith("Outil refusé")


@pytest.mark.parametrize(
    ("cran", "attendu"), [("humain", "humain"), ("orchestrateur", "orchestrateur")]
)
def test_le_journal_nomme_qui_a_tranche(store, cran, attendu):
    """Le critère du lot : *qui a tranché se lit, il ne se déduit pas*.

    Le décideur est dans le **nom** de l'étape — le seul champ du journal qui
    porte une provenance sans qu'un consommateur ait à la deviner d'une tournure
    de phrase, comme il distingue déjà les deux producteurs d'un `:validation`
    (#582) — et il est redemandé à la politique au moment de consigner, jamais
    déduit du texte du motif : changer une phrase ne doit pas changer la nature
    d'une ligne.
    """
    _ecrire_politique(store.racine, "developpeur", {"ask": {"Bash": cran}})
    journal = RunJournal(run_id=f"run-{cran}")
    moteur = OrchestrationEngine(
        ArbitreProvider(),
        Orchestrator(ConstantProvider(_plan_json()), model="claude-opus-4-8"),
        permissions=store,
        guardrails=Guardrails(
            validateur=lambda demande: True, orchestrateur=lambda demande: True
        ),
    )

    asyncio.run(moteur.run("Objectif", journal=journal))

    (trace,) = [r for r in journal.records if r.etape == f"tache-unique{SUFFIXE_ETAPE_REFUS}"]
    assert trace.nom.startswith(f"Outil arbitré ({attendu})")
    assert trace.statut == STATUT_ARBITRAGE_OUTIL
    # Et la décision elle-même, dans la sortie : approuvée, et **par qui**.
    assert attendu in trace.sortie


def test_l_orchestrateur_ne_tranche_pas_un_acte_classe_humain_bout_en_bout(store):
    # Le pendant de bout en bout du test unitaire des garde-fous : la politique
    # classe l'outil `humain`, un orchestrateur qui approuverait tout est câblé,
    # et aucune personne ne l'est. L'acte doit être écarté.
    _ecrire_politique(store.racine, "developpeur", {"ask": {"Bash": "humain"}})
    provider = ArbitreProvider()
    moteur = OrchestrationEngine(
        provider,
        Orchestrator(ConstantProvider(_plan_json()), model="claude-opus-4-8"),
        permissions=store,
        guardrails=Guardrails(orchestrateur=lambda demande: True),
    )

    asyncio.run(moteur.run("Objectif"))

    (arbitrage,) = [
        appel["arbitrage"] for appel in provider.run_calls if "arbitrage" in appel
    ]
    approuve, detail = arbitrage
    assert not approuve
    assert "aucun validateur humain configuré" in detail


def test_le_fail_safe_du_moteur_tient_sans_validateur(store):
    # Le canal existe (le moteur le câble dès qu'il y a une politique), mais
    # personne ne tranche : `Guardrails` refuse par défaut, et l'orchestrateur ne
    # peut jamais approuver à la place d'une personne (EF-08, ENF-04).
    _ecrire_politique(store.racine, "developpeur", {"ask": ["Bash"]})
    provider = ArbitreProvider()

    asyncio.run(_moteur(provider, store).run("Objectif"))

    (arbitrage,) = [
        appel["arbitrage"] for appel in provider.run_calls if "arbitrage" in appel
    ]
    approuve, detail = arbitrage
    assert not approuve
    assert "aucun validateur" in detail
