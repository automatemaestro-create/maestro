"""Un arbitrage ne consomme pas le délai, et une décision tardive n'est pas perdue (#584).

Lot 5 du chantier #573, à la suite immédiate du lot 4 (#583). Aucun appel réseau,
aucun SDK : fournisseurs factices, dépôts sur répertoires temporaires. Trois
sections, une par critère d'acceptation, plus deux qui gardent la **mécanique**
sur laquelle les trois reposent.

Le déplacement qu'on répare tient en une phrase : tant que l'arbitrage portait
sur le *texte* de la tâche, l'attente d'une décision vivait **avant** l'armement
du délai (`_realise_gardee`) et rien n'avait à être mesuré ; le lot 4 l'a déplacé
dans l'**appel d'outil**, donc au cœur de la réalisation, donc dans l'échéance.

① **le délai ne court pas pendant qu'un humain délibère** — une tâche qui attend
   plus longtemps que son `timeout_s` aboutit, pendant qu'une tâche simplement
   lente meurt toujours (sans cette seconde moitié, « ne tue jamais » se
   confondrait avec « ne tue plus rien ») ;
② **une décision tardive n'est pas perdue** — l'appel rejoué la retrouve, deux
   appels simultanés sur le même acte partagent une seule demande, deux actes
   *différents* n'héritent pas l'un de l'autre, et une panne n'est pas retenue
   comme une décision ;
③ **le journal distingue les deux temps** — `duree_arbitrage_ms` est une part de
   `duree_ms` et jamais un temps de plus, et l'aller-retour JSON la garde ;
④ **le crédit mesure l'union**, jamais la somme, et compte l'attente en cours ;
⑤ **la fenêtre se referme là où l'appel cesse d'être bloqué** — c'est-à-dire à la
   borne du hook et non à l'arrivée de la décision. C'est le seul endroit où une
   erreur ne se verrait pas : elle rendrait à la tâche du délai qu'elle a passé à
   travailler, sans plafond.
"""

import asyncio
import json
from pathlib import Path

import pytest

from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.deliberation import (
    CreditArbitrage,
    Deliberation,
    MemoireArbitrage,
    cle_acte,
)
from maestro.engine import OrchestrationEngine
from maestro.engine.guardrails import Guardrails
from maestro.orchestrator import Orchestrator
from maestro.providers import claude as claude_mod
from maestro.providers.arbitrage import BornesArbitrage
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage

# Ce que dure une délibération dans cette suite, et ce que dure le délai qu'elle
# doit cesser de consommer. L'écart est large à dessein : ces tests tournent en
# parallèle du reste de la suite (`-n auto` en CI), et un écart serré ne
# mesurerait plus le code mais l'ordonnancement de la machine.
DELAI_S = 0.5
DELIBERATION_S = 0.9


# --- Fournisseurs et validateurs factices -----------------------------------------------


class ConstantProvider(ModelProvider):
    """Renvoie toujours la même réponse (sert de planificateur factice)."""

    name = "constant"

    def __init__(self, response: str) -> None:
        self._response = response

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        return self._response


class ProviderQuiArbitre(ModelProvider):
    """Exécutant outillé factice qui suspend son appel d'outil le temps d'un arbitrage.

    Reproduit **le contrat** du hook réel (`maestro.providers.claude`) et rien
    d'autre : il ouvre la fenêtre du crédit autour de l'attente, puis la referme
    en rendant la main. C'est le comportement que `run_agent` exige d'un
    fournisseur qui honore `credit_arbitrage` (#584), et c'est celui qu'on veut
    voir décompté par le moteur.

    `travail_s` est le temps passé **hors** arbitrage : c'est lui, et lui seul,
    que le délai de la tâche doit continuer de borner.
    """

    name = "arbitre-lent"

    def __init__(self, *, travail_s: float = 0.0, arbitrages: int = 1) -> None:
        self.travail_s = travail_s
        self.arbitrages = arbitrages
        self.demandes: list[tuple[str, dict[str, str]]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("un rôle outillé doit passer par run_agent")

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        credit_arbitrage=None,
        on_courrier=None,
        plafond_tours=None, projet=None,
    ):
        for _ in range(self.arbitrages):
            if on_arbitrage_acte is None:
                break
            arguments = {"command": "rm -rf /srv"}
            self.demandes.append(("Bash", dict(arguments)))
            with credit_arbitrage.attente():
                await on_arbitrage_acte("Bash", arguments, "motif de politique")
        await asyncio.sleep(self.travail_s)
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        return "OUTILLE"


class ProviderLent(ProviderQuiArbitre):
    """Le témoin : il travaille longtemps et n'arbitre rien. Le délai doit le tuer."""

    name = "lent"

    def __init__(self, *, travail_s: float) -> None:
        super().__init__(travail_s=travail_s, arbitrages=0)


class ValidateurLent:
    """Valideur humain factice : prend `delai_s` à trancher, puis rend `decision`."""

    def __init__(self, delai_s: float, decision: bool = True) -> None:
        self._delai_s = delai_s
        self._decision = decision
        self.demandes: list[object] = []

    async def __call__(self, demande) -> bool:
        self.demandes.append(demande)
        await asyncio.sleep(self._delai_s)
        return self._decision


# --- Aides ------------------------------------------------------------------------------


def _plan_json(titre="Tâche unique", description="Réaliser la tâche."):
    """Plan factice d'une tâche unique, routée vers le développeur."""
    return json.dumps(
        [
            {
                "id": "tache-unique",
                "titre": titre,
                "description": description,
                "competences_requises": ["backend"],
                "format_sortie": "Texte",
                "dependances": [],
            }
        ],
        ensure_ascii=False,
    )


@pytest.fixture()
def store(tmp_path):
    """Dépôt de politiques vierge, avec `Bash` classé `ask` pour le développeur."""
    depot = PermissionStore(tmp_path / "permissions")
    depot.racine.mkdir(parents=True, exist_ok=True)
    (depot.racine / "developpeur.json").write_text(
        json.dumps({"ask": ["Bash"]}, ensure_ascii=False), encoding="utf-8"
    )
    return depot


def _moteur(provider, store=None, *, guardrails=None, plan=None):
    """Boucle d'orchestration à planification factice, branchée sur `store`."""
    return OrchestrationEngine(
        provider,
        Orchestrator(ConstantProvider(plan or _plan_json()), model="claude-opus-4-8"),
        permissions=store,
        guardrails=guardrails,
    )


def _joue(moteur, journal=None):
    """Joue le moteur et rend (résultat de la tâche unique, journal)."""
    journal = journal or RunJournal(run_id="run-584")
    rapport = asyncio.run(moteur.run("Objectif", journal=journal))
    return rapport.resultats[0], journal


# --- ① Le délai ne court pas pendant qu'un humain délibère ------------------------------


def test_un_arbitrage_plus_long_que_le_delai_ne_tue_pas_la_tache(store):
    # Le cœur du ticket. La délibération dure presque deux fois le `timeout_s` :
    # avant ce lot, l'échéance tombait en pleine question à l'opérateur et la
    # tâche mourait sur une lenteur qui n'était pas la sienne.
    provider = ProviderQuiArbitre()
    validateur = ValidateurLent(DELIBERATION_S, decision=True)
    garde = Guardrails(timeout_s=DELAI_S, validateur=validateur, mots_sensibles=())

    resultat, _ = _joue(_moteur(provider, store, guardrails=garde))

    assert resultat.ok, resultat.erreur
    assert len(validateur.demandes) == 1  # la personne a bien été consultée
    # Et la preuve que le scénario a bien joué ce qu'il prétend jouer : le temps
    # d'arbitrage mesuré dépasse le délai qui aurait dû tuer la tâche.
    assert resultat.usage.duree_arbitrage_ms >= DELAI_S * 1000


def test_le_delai_tue_toujours_une_tache_simplement_lente(store):
    # La moitié sans laquelle la première ne prouverait rien : « ne tue jamais
    # une tâche qui attend une décision » ne doit pas devenir « ne tue plus rien ».
    resultat, _ = _joue(
        _moteur(
            ProviderLent(travail_s=30),
            store,
            guardrails=Guardrails(timeout_s=0.2, mots_sensibles=()),
        )
    )

    assert resultat.statut == "echec"
    assert "time-out" in (resultat.erreur or "")
    # Aucun arbitrage n'a eu lieu : le message est celui d'avant ce lot, au
    # caractère près — il n'annonce aucun temps rendu.
    assert "rendues à l'arbitrage" not in (resultat.erreur or "")


def test_une_echeance_atteinte_pendant_la_deliberation_ne_conclut_a_rien(store):
    # Le piège du recalcul : l'échéance tombe *pendant* que la question est
    # posée, à un instant où le crédit qui la couvre n'est pas encore acquis.
    # Conclure là serait tuer la tâche entre l'instant où elle demande et celui
    # où on lui rend son temps.
    provider = ProviderQuiArbitre()
    garde = Guardrails(
        timeout_s=0.05,  # bien plus court que la délibération qui va s'ouvrir
        validateur=ValidateurLent(DELIBERATION_S, decision=True),
        mots_sensibles=(),
    )

    resultat, _ = _joue(_moteur(provider, store, guardrails=garde))

    assert resultat.ok, resultat.erreur


def test_le_temps_paye_avant_l_armement_du_delai_n_est_pas_rendu():
    # La validation d'une tâche sensible (#9) précède l'armement : elle n'a
    # jamais couru sur le délai, donc il n'y a rien à lui rendre. Le crédit
    # qu'elle acquiert est mesuré (le journal le veut) mais **remis à zéro** au
    # moment d'armer — sans quoi une tâche gagnerait du délai pour une attente
    # qu'elle n'a pas payée, et le time-out deviendrait négociable.
    garde = Guardrails(
        timeout_s=0.2,
        validateur=ValidateurLent(DELIBERATION_S, decision=True),
        mots_sensibles=("deploi",),
    )
    plan = _plan_json(titre="Déploiement", description="Déploiement en production.")

    resultat, _ = _joue(
        _moteur(ProviderLent(travail_s=30), guardrails=garde, plan=plan)
    )

    assert resultat.statut == "echec"
    assert "time-out" in (resultat.erreur or "")
    # La validation a bien été payée, et elle se lit dans le journal…
    assert resultat.usage.duree_arbitrage_ms >= DELIBERATION_S * 1000
    # …sans avoir été rendue au délai : le message ne l'annonce pas.
    assert "rendues à l'arbitrage" not in (resultat.erreur or "")


def test_un_depassement_apres_arbitrage_nomme_le_temps_qui_a_ete_rendu(store):
    # « La tâche a dépassé 0,3 s » sur une tâche qui en a passé une seconde
    # suspendue à une question enverrait chercher une lenteur d'exécution là où
    # l'échéance a déjà été repoussée d'autant.
    provider = ProviderQuiArbitre(travail_s=30)
    garde = Guardrails(
        timeout_s=0.3,
        validateur=ValidateurLent(DELIBERATION_S, decision=True),
        mots_sensibles=(),
    )

    resultat, _ = _joue(_moteur(provider, store, guardrails=garde))

    assert resultat.statut == "echec"
    assert "time-out" in (resultat.erreur or "")
    assert "rendues à l'arbitrage humain" in (resultat.erreur or "")


# --- ② Une décision tardive n'est pas perdue --------------------------------------------


def test_un_appel_rejoue_retrouve_une_decision_arrivee_apres_l_attente():
    # Le second critère, au niveau où il se joue : le hook a cessé d'attendre,
    # la demande est restée en vol, la décision arrive — et le rappel du même
    # acte la retrouve sans rouvrir de demande.
    async def scenario():
        memoire = MemoireArbitrage()
        tranche = asyncio.Event()
        soumissions = 0

        async def soumettre():
            nonlocal soumissions
            soumissions += 1
            await tranche.wait()
            return True, "approuvée (tardivement)"

        cle = cle_acte("Bash", {"command": "rm -rf /srv"})
        # Premier appel : il renonce à sa borne, comme le hook.
        premier = asyncio.ensure_future(memoire.tranche(cle, soumettre))
        await asyncio.sleep(0)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(asyncio.shield(premier), 0.05)
        # La décision arrive quand plus personne ne l'attend.
        tranche.set()
        await asyncio.sleep(0.05)
        # L'appel rejoué : il retrouve, il ne redemande pas.
        rejoue = await memoire.tranche(cle, soumettre)
        premier.cancel()
        return rejoue, soumissions, memoire.decision(cle)

    rejoue, soumissions, retenue = asyncio.run(scenario())

    assert rejoue == (True, "approuvée (tardivement)")
    assert soumissions == 1  # aucune seconde demande devant l'opérateur
    assert retenue == (True, "approuvée (tardivement)")


def test_deux_appels_sur_le_meme_acte_partagent_une_seule_demande():
    # Sans ce partage, un agent qui réessaie empilerait des demandes identiques
    # devant la même personne — et la projection de la Control Tower, indexée
    # par tâche, ne garderait que la dernière.
    async def scenario():
        memoire = MemoireArbitrage()
        soumissions = 0

        async def soumettre():
            nonlocal soumissions
            soumissions += 1
            await asyncio.sleep(0.05)
            return True, "approuvée"

        cle = cle_acte("Bash", {"command": "rm -rf /srv"})
        issues = await asyncio.gather(
            memoire.tranche(cle, soumettre), memoire.tranche(cle, soumettre)
        )
        return issues, soumissions

    issues, soumissions = asyncio.run(scenario())

    assert issues == [(True, "approuvée"), (True, "approuvée")]
    assert soumissions == 1


def test_deux_actes_differents_n_heritent_pas_l_un_de_l_autre():
    # L'identité est celle de l'**acte** et pas de la tâche : approuver
    # `rm build/` n'approuve pas `rm /`. Indexer par tâche — ce que fait la
    # projection — ferait hériter le second de la décision rendue sur le premier.
    async def scenario():
        memoire = MemoireArbitrage()
        soumissions: list[str] = []

        def soumettre(marque):
            async def _soumettre():
                soumissions.append(marque)
                return marque == "sûr", marque

            return _soumettre

        sur = await memoire.tranche(
            cle_acte("Bash", {"command": "rm build/"}), soumettre("sûr")
        )
        dangereux = await memoire.tranche(
            cle_acte("Bash", {"command": "rm /"}), soumettre("dangereux")
        )
        return sur, dangereux, soumissions

    sur, dangereux, soumissions = asyncio.run(scenario())

    assert sur == (True, "sûr")
    assert dangereux == (False, "dangereux")
    assert soumissions == ["sûr", "dangereux"]  # deux actes, deux questions


def test_le_meme_acte_se_reconnait_quel_que_soit_l_ordre_des_arguments():
    # La clé est stable ou elle n'est pas : un dictionnaire construit dans un
    # autre ordre est le même acte, et rouvrir une demande dessus reviendrait à
    # ne rien avoir retenu.
    assert cle_acte("Bash", {"a": "1", "b": "2"}) == cle_acte("Bash", {"b": "2", "a": "1"})
    # Et elle ne confond pas ce qu'une concaténation confondrait.
    assert cle_acte("Bash", {"a": "b:c"}) != cle_acte("Bash", {"a:b": "c"})


def test_une_panne_du_canal_n_est_pas_retenue_comme_une_decision():
    # Un bus tombé n'est pas un verdict : le rejeu doit reposer la question, pas
    # rejouer la panne. (Le refus, lui, est rendu par le hook — ici on garde que
    # la mémoire ne fige rien.)
    async def scenario():
        memoire = MemoireArbitrage()
        tentatives = 0

        async def soumettre():
            nonlocal tentatives
            tentatives += 1
            if tentatives == 1:
                raise RuntimeError("bus injoignable")
            return True, "approuvée"

        cle = cle_acte("Bash", {"command": "rm -rf /srv"})
        with pytest.raises(RuntimeError):
            await memoire.tranche(cle, soumettre)
        apres_panne = memoire.decision(cle)
        rejoue = await memoire.tranche(cle, soumettre)
        return apres_panne, rejoue, tentatives

    apres_panne, rejoue, tentatives = asyncio.run(scenario())

    assert apres_panne is None
    assert rejoue == (True, "approuvée")
    assert tentatives == 2


def test_une_seconde_decision_n_ecrase_pas_la_premiere():
    # Même règle que le `409` de la Control Tower sur une demande déjà tranchée :
    # une décision humaine ne se réécrit pas parce qu'un second chemin l'a
    # redemandée.
    memoire = MemoireArbitrage()
    cle = cle_acte("Bash", {"command": "rm -rf /srv"})

    memoire.retient(cle, (True, "approuvée"))
    memoire.retient(cle, (False, "refusée"))

    assert memoire.decision(cle) == (True, "approuvée")


def test_la_tache_relancee_reprend_sur_la_decision_deja_obtenue(store):
    # « La tâche relancée reprend sur elle » : la mémoire naît dans `execute` et
    # traverse les relances (#91). La placer plus bas ferait redemander à
    # l'opérateur, à chaque tentative, ce qu'il vient d'accorder.
    provider = ProviderQuiArbitre(arbitrages=3)  # trois appels sur le même acte
    validateur = ValidateurLent(0.0, decision=True)

    resultat, _ = _joue(
        _moteur(
            provider,
            store,
            guardrails=Guardrails(validateur=validateur, mots_sensibles=()),
        )
    )

    assert resultat.ok, resultat.erreur
    assert len(provider.demandes) == 3  # l'agent a bien rappelé l'outil trois fois
    assert len(validateur.demandes) == 1  # on n'a dérangé personne deux fois


# --- ③ Le journal distingue le temps d'exécution du temps d'arbitrage -------------------


def test_le_journal_distingue_le_temps_d_execution_du_temps_d_arbitrage(store):
    # Le troisième critère : un run lent sur arbitrage ne doit pas se lire comme
    # un run lent sur travail (#495).
    garde = Guardrails(
        validateur=ValidateurLent(DELIBERATION_S, decision=True), mots_sensibles=()
    )

    resultat, journal = _joue(_moteur(ProviderQuiArbitre(), store, guardrails=garde))

    usage = resultat.usage
    assert usage.duree_arbitrage_ms >= DELIBERATION_S * 1000
    # Une **part** de la durée horloge, jamais un temps de plus.
    assert usage.duree_arbitrage_ms <= usage.duree_ms
    # Ce qui reste est le temps réellement passé à travailler — proche de zéro
    # ici, où le fournisseur factice ne fait rien d'autre qu'attendre.
    assert usage.duree_execution_ms < DELIBERATION_S * 1000
    # Et c'est bien l'étape finale de la tâche qui le porte, pas une étape annexe.
    (finale,) = [r for r in journal.records if r.etape == "tache-unique"]
    assert finale.usage.duree_arbitrage_ms == usage.duree_arbitrage_ms


def test_sans_arbitrage_la_part_est_nulle_et_la_duree_reste_celle_du_travail():
    # Mesuré et nul (`0`), pas inconnu (`None`) : le moteur a regardé, il n'y a
    # rien eu. C'est la distinction de `cout_usd`, et elle vaut ici pareil.
    resultat, _ = _joue(_moteur(ProviderLent(travail_s=0.0)))

    assert resultat.usage.duree_arbitrage_ms == 0
    assert resultat.usage.duree_execution_ms == resultat.usage.duree_ms


def test_la_part_d_arbitrage_survit_a_l_aller_retour_json():
    # Le journal est du JSON Lines : ce qui ne fait pas l'aller-retour n'existe
    # pas pour l'audit d'un run, qui le relit (#495).
    usage = StepUsage(appels=1).avec_duree(12_000, arbitrage_ms=8_000)

    relu = StepUsage.from_dict(json.loads(json.dumps(usage.to_dict())))

    assert relu.duree_arbitrage_ms == 8_000
    assert relu.duree_execution_ms == 4_000


def test_une_mesure_ecrite_avant_ce_lot_se_relit_sans_part_d_arbitrage():
    # Un journal d'hier n'a pas la clé : « inconnu » et non « zéro », et la durée
    # d'exécution retombe alors sur la durée horloge — ce qu'elle a toujours été.
    relu = StepUsage.from_dict({"appels": 1, "duree_ms": 5_000})

    assert relu.duree_arbitrage_ms is None
    assert relu.duree_execution_ms == 5_000


def test_le_resume_ne_nomme_l_arbitrage_que_s_il_a_eu_lieu():
    # Annoncer « dont 0,0 s d'arbitrage » sur chaque tâche d'un run apprendrait à
    # ne plus lire la mention — et c'est elle qui doit sauter aux yeux le jour où
    # une tâche a passé quatre minutes à attendre quelqu'un.
    sans = StepUsage(appels=1).avec_duree(12_000, arbitrage_ms=0).resume_court()
    avec = StepUsage(appels=1).avec_duree(12_000, arbitrage_ms=8_000).resume_court()

    assert "arbitrage" not in sans
    assert "durée 12.0 s" in sans
    assert "dont 8.0 s d'arbitrage" in avec


def test_la_fusion_agrege_les_deux_durees_sans_perdre_l_inconnu():
    # `None` reste `None` face à `None`, et se laisse absorber par une valeur :
    # la règle de tous les compteurs optionnels de `StepUsage`.
    inconnu = StepUsage(appels=1)
    mesure = StepUsage(appels=1).avec_duree(1_000, arbitrage_ms=400)

    assert inconnu.fusion(inconnu).duree_arbitrage_ms is None
    assert inconnu.fusion(mesure).duree_arbitrage_ms == 400
    assert mesure.fusion(mesure).duree_arbitrage_ms == 800


# --- ④ Le crédit : l'union, jamais la somme ---------------------------------------------


def test_deux_attentes_qui_se_recouvrent_ne_comptent_qu_une_fois():
    # Une tâche n'est bloquée qu'une fois : additionner des intervalles qui se
    # recouvrent rendrait une part de 110 % — la règle même de `journal.sh audit`
    # sur le temps passé sous outil (#497).
    async def scenario():
        credit = CreditArbitrage()

        async def attend():
            with credit.attente():
                await asyncio.sleep(0.2)

        await asyncio.gather(attend(), attend())
        return credit.ecoule()

    ecoule = asyncio.run(scenario())

    assert 0.15 <= ecoule < 0.4  # ~0,2 s (l'union), et non ~0,4 s (la somme)


def test_une_attente_en_cours_est_deja_comptee():
    # Sans cela, une échéance atteinte pendant une délibération conclurait au
    # dépassement d'une tâche qui n'a rien consommé.
    async def scenario():
        credit = CreditArbitrage()
        with credit.attente():
            await asyncio.sleep(0.1)
            return credit.ecoule(), credit.en_attente()

    pendant, en_attente = asyncio.run(scenario())

    assert pendant >= 0.05
    assert en_attente


def test_une_attente_qui_leve_referme_quand_meme_sa_fenetre():
    # Les trois issues d'un arbitrage sortent par des chemins différents ; une
    # fenêtre laissée ouverte sur l'une d'elles créditerait la tâche jusqu'à la
    # fin du run.
    async def scenario():
        credit = CreditArbitrage()
        with pytest.raises(RuntimeError):
            with credit.attente():
                await asyncio.sleep(0.05)
                raise RuntimeError("canal en panne")
        return credit.en_attente(), credit.ecoule()

    en_attente, ecoule = asyncio.run(scenario())

    assert not en_attente
    assert ecoule >= 0.02


def test_le_repos_rend_la_main_des_la_fin_de_la_deliberation():
    # C'est ce que le moteur attend au lieu de conclure au dépassement.
    async def scenario():
        credit = CreditArbitrage()
        attendu: list[str] = []

        async def delibere():
            with credit.attente():
                await asyncio.sleep(0.1)
            attendu.append("tranché")

        async def guette():
            await credit.repos()
            attendu.append("repos")

        # Le guetteur part **après** l'ouverture, sinon il verrait le repos initial.
        deliberation = asyncio.ensure_future(delibere())
        await asyncio.sleep(0.02)
        await asyncio.gather(deliberation, guette())
        return attendu

    assert asyncio.run(scenario()) == ["tranché", "repos"]


def test_sans_attente_le_repos_est_immediat():
    # Le compteur part au repos : une tâche qui n'arbitre rien ne doit pas se
    # suspendre à un événement que personne ne posera.
    async def scenario():
        credit = CreditArbitrage()
        await asyncio.wait_for(credit.repos(), 0.5)
        return credit.ecoule()

    assert asyncio.run(scenario()) == 0.0


# --- ⑤ La fenêtre se referme là où l'appel cesse d'être bloqué --------------------------


def test_le_hook_referme_sa_fenetre_a_sa_borne_et_pas_a_la_decision():
    # L'erreur qui ne se verrait pas. Le hook cesse d'attendre à sa borne et rend
    # la main à l'agent ; la demande, elle, reste en vol — parfois longtemps.
    # Une mesure prise là où la demande est composée continuerait de courir
    # pendant que l'agent a déjà repris son travail, et rendrait à la tâche du
    # délai qu'elle a passé à travailler, sans plafond.
    async def scenario():
        credit = CreditArbitrage()
        tranche = asyncio.Event()

        async def arbitrage(outil, arguments, motif):
            await tranche.wait()
            return True, "décision tardive"

        hook = claude_mod._hook_permissions(
            PolitiqueOutils(ask=("Bash",)),
            None,
            arbitrage,
            BornesArbitrage(attente_s=0.05, borne_hook_s=10.0),
            credit,
        )
        await hook({"tool_name": "Bash", "tool_input": {}}, "tu-1", None)
        a_la_borne = credit.ecoule()
        # La demande court toujours : le compteur, lui, ne doit plus bouger.
        await asyncio.sleep(0.2)
        avant_decision = credit.ecoule()
        tranche.set()
        await asyncio.sleep(0.05)
        return a_la_borne, avant_decision, credit.ecoule(), credit.en_attente()

    a_la_borne, avant_decision, apres_decision, en_attente = asyncio.run(scenario())

    assert not en_attente
    assert a_la_borne >= 0.04  # l'attente a bien eu lieu…
    assert a_la_borne < 0.2  # …et s'est arrêtée à la borne, pas à la décision
    # Égalité **exacte** : le compteur est clos, il ne dérive pas d'un flottant.
    assert avant_decision == a_la_borne
    assert apres_decision == a_la_borne


def test_le_hook_mesure_aussi_une_attente_qui_aboutit():
    # Le cas nominal : la décision arrive avant la borne, et tout ce temps-là est
    # du temps pendant lequel l'appel de l'agent est resté suspendu.
    async def scenario():
        credit = CreditArbitrage()

        async def arbitrage(outil, arguments, motif):
            await asyncio.sleep(0.1)
            return True, "approuvée"

        hook = claude_mod._hook_permissions(
            PolitiqueOutils(ask=("Bash",)), None, arbitrage, BornesArbitrage(), credit
        )
        sortie = await hook({"tool_name": "Bash", "tool_input": {}}, "tu-1", None)
        return sortie, credit.ecoule()

    sortie, ecoule = asyncio.run(scenario())

    assert sortie == {}  # approuvé : l'appel n'est plus suspendu
    assert ecoule >= 0.05


def test_un_appel_qui_passe_sans_arbitrage_ne_credite_rien():
    # Le hook est consulté avant **chaque** appel d'outil : s'il créditait au
    # passage, une tâche qui n'arbitre rien gagnerait du délai à chaque `Read`.
    async def scenario():
        credit = CreditArbitrage()
        hook = claude_mod._hook_permissions(
            PolitiqueOutils(ask=("Bash",)), None, None, BornesArbitrage(), credit
        )
        sortie = await hook({"tool_name": "Read", "tool_input": {}}, "tu-1", None)
        return sortie, credit.ecoule()

    sortie, ecoule = asyncio.run(scenario())

    assert sortie == {}
    assert ecoule == 0.0


def test_l_outil_d_arbitrage_de_l_agent_mesure_toute_son_attente():
    # L'autre canal (#582) : l'agent a levé la main et **attend sa réponse**,
    # sans borne. C'est celui où un délai par tâche faisait le plus de dégâts —
    # tuer une tâche dont l'agent s'est montré prudent lui apprend exactement le
    # contraire de ce qu'on veut lui apprendre.
    async def scenario():
        credit = CreditArbitrage()

        async def on_arbitrage(raison):
            await asyncio.sleep(0.1)
            return True, "approuvée"

        outil = claude_mod._outil_arbitrage(on_arbitrage, credit)
        sortie = await outil.handler({"raison": "supprimer /srv"})
        return sortie, credit.ecoule()

    sortie, ecoule = asyncio.run(scenario())

    assert "approuvé" in sortie["content"][0]["text"]
    assert ecoule >= 0.05


def test_sans_credit_le_fournisseur_arbitre_exactement_comme_avant():
    # Capacité optionnelle : un appelant qui n'a pas de délai à défendre n'a rien
    # à mesurer, et le canal doit fonctionner tel quel — c'est le comportement
    # de #583, que ce lot ne doit pas rendre conditionnel à un branchement.
    async def scenario():
        async def arbitrage(outil, arguments, motif):
            return False, "refusée par le validateur humain"

        hook = claude_mod._hook_permissions(
            PolitiqueOutils(ask=("Bash",)), None, arbitrage, BornesArbitrage(), None
        )
        return await hook({"tool_name": "Bash", "tool_input": {}}, "tu-1", None)

    decision = asyncio.run(scenario())["hookSpecificOutput"]

    assert decision["permissionDecision"] == "deny"
    assert "refusé à l'arbitrage humain" in decision["permissionDecisionReason"]


def test_le_motif_d_attente_dit_a_l_agent_que_la_decision_le_rattrapera():
    # Le dispositif ne sert à rien si le seul acteur capable de le déclencher
    # ignore qu'il existe : l'agent lisait « poursuis sans cet outil » et n'avait
    # aucune raison d'y revenir.
    from maestro.providers.arbitrage import motif_attente

    motif = motif_attente("Bash", 240.0)

    assert "reste en attente" in motif
    assert "si tu y reviens plus tard" in motif
    assert "sans nouvelle attente" in motif


def test_la_deliberation_par_defaut_est_neutre():
    # `Deliberation()` sans argument est ce que composent les appelants qui n'en
    # passent pas : un crédit vide et une mémoire vide, jamais un état partagé.
    une, autre = Deliberation(), Deliberation()

    assert une.credit.ecoule() == 0.0
    assert une.credit is not autre.credit
    assert une.memoire is not autre.memoire
