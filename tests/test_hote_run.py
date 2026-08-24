"""La frontière d'exécution vue de l'appelant (#447, lot 6/6 du chantier #441).

Le pendant de [`tests/test_hote_detache.py`](test_hote_detache.py), qui éprouve le
process détaché lui-même. Ici on ne regarde jamais *où* un run vit : on regarde ce
que la Control Tower en sait, ce qui ne fait que trois choses et c'est tout
l'intérêt du contrat (`maestro.controltower.hote`) —

① **deux hôtes, une seule différence qui compte** : `fermer`. Celui en process
   annule ses runs, faute de pouvoir leur survivre ; le détaché ne fait rien. Tout
   le reste du contrat, les deux le disent pareil, `False` sur un run qu'ils ne
   portent pas compris ;
② **le service ne sait que deux choses de la fin de vie d'un hôte**, et les deux
   sont ici : un hôte peut **rater son départ** (#443) — le run est alors soldé
   avec sa cause, parce qu'à cet instant plus rien ne viendra de lui —, et un hôte
   peut **mourir sans dire son issue** (#446) — le ramassage confronte alors ce
   que l'hôte a vu mourir à la projection, et solde ce qui restait `en_cours` ;
③ **le déploiement choisit** (`MAESTRO_HOTE_RUN`), à un seul endroit, et une
   valeur inconnue y est une erreur franche : une frontière d'exécution mal
   orthographiée ne doit jamais ressembler à un choix.

**Ni Redis, ni réseau, ni process, ni appel modèle.** Les hôtes sont des doubles —
c'est justement ce que le contrat autorise, et un test qui aurait besoin d'un vrai
process pour vérifier ce que l'appelant en fait prouverait que la frontière
n'existe pas.

⚠ Deux méthodes privées sont appelées directement, et c'est délibéré. `_ramasser`
et `_derouler` vivent derrière le **cœur** du service, dont la période est un
réglage de déploiement (trente secondes) : passer par lui obligerait à dormir, ou
à régler la période assez bas pour que le test devienne une course. Ce qu'on veut
lire est une décision, pas un ordonnancement.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.config import ConfigError, Settings
from maestro.controltower.app import _hote_configure, create_app
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    InMemoryEventBus,
)
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.hote import (
    HOTE_RUN_DETACHE,
    HOTE_RUN_EN_PROCESS,
    DemarrageHoteRate,
    HoteMort,
    HoteRun,
    OrdreRun,
)
from maestro.controltower.hote_detache import HoteRunDetache
from maestro.controltower.hote_en_process import HoteRunEnProcess
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    ControlTowerState,
)

RUN = "run-frontiere"
CAUSE = "l'hôte détaché du run run-frontiere s'est arrêté sans publier d'issue (code 9) : boum"


def ordre(run_id: str = RUN, **surcharges: Any) -> OrdreRun:
    """Un ordre de run minimal — le contrat ne demande rien de plus."""
    return OrdreRun(run_id=run_id, objectif="Objectif", **surcharges)


def settings(hote_run: str | None) -> Settings:
    """Un `Settings` réel dont seul l'hôte des runs est choisi.

    Construit par recopie de celui du poste plutôt que de toutes pièces : ce qu'on
    éprouve est la **résolution d'un nom**, et lister ici les champs de `Settings`
    ferait échouer ce fichier au prochain réglage ajouté ailleurs.
    """
    return dataclasses.replace(
        Settings(
            anthropic_api_key=None,
            anthropic_model="claude-opus-4-8",
            claude_auth_mode=None,
            claude_oauth_token=None,
            database_url=None,
            redis_url=None,
        ),
        hote_run=hote_run,
    )


# --------------------------------------------------------------------- doubles


class HoteMuet(HoteRun):
    """Un hôte qui accepte tout et ne porte rien — le strict contrat, sans transport.

    Il sert les tests du **service** : ce qu'on y regarde est ce que l'appelant
    fait de ce qu'un hôte lui rend, pas la façon dont l'hôte l'a appris.
    """

    def __init__(self, *, depart_rate: str = "", morts: tuple[HoteMort, ...] = ()) -> None:
        self._depart_rate = depart_rate
        self._morts = morts
        self.lances: list[OrdreRun] = []
        self.annules: list[str] = []
        self.ferme = False
        self.ramassages = 0

    async def lancer(self, ordre_du_run: OrdreRun) -> None:
        if self._depart_rate:
            raise DemarrageHoteRate(self._depart_rate)
        self.lances.append(ordre_du_run)

    async def annuler(self, run_id: str, *, delai_s: float) -> bool:
        self.annules.append(run_id)
        return False

    def en_vol(self, run_id: str) -> bool:
        return any(o.run_id == run_id for o in self.lances)

    def runs_en_vol(self) -> tuple[str, ...]:
        return tuple(o.run_id for o in self.lances)

    def ramasser(self) -> tuple[HoteMort, ...]:
        # Une seule fois, comme le contrat l'exige : redire une mort ferait
        # réécrire l'issue d'un run à chaque tour d'horloge.
        self.ramassages += 1
        morts, self._morts = self._morts, ()
        return morts

    async def fermer(self, *, delai_s: float) -> None:
        self.ferme = True


class HoteQuiRefuseDeRamasser(HoteMuet):
    """Un hôte dont l'observation elle-même tombe en panne."""

    def ramasser(self) -> tuple[HoteMort, ...]:
        raise RuntimeError("registre des dépouilles illisible")


class ProcessQuiVit:
    """Le strict nécessaire d'un `Popen` vivant, pour un hôte détaché qu'on ferme."""

    pid = 4321
    tue = False

    def poll(self) -> int | None:
        return None


def service(hote: HoteRun, **reglages: Any) -> tuple[ServiceExecutions, ControlTowerState]:
    """Un service de pilotage sur un bus mémoire et une projection neuve."""
    projection = ControlTowerState()
    return ServiceExecutions(InMemoryEventBus(), projection, hote=hote, **reglages), projection


def pose_un_run(
    pilote: ServiceExecutions, statut: str = EXECUTION_EN_COURS, run_id: str = RUN
) -> None:
    """Inscrit un run dans la projection, comme le ferait son lancement."""
    pilote._consigne(run_id, statut, "Objectif", "lancée depuis la Control Tower")


# --- ① Deux hôtes, une seule différence qui compte -----------------------------


def test_l_hote_en_process_annule_ses_runs_quand_l_api_se_retire() -> None:
    """La contrepartie assumée de la tâche de fond, énoncée à un seul endroit.

    Ce qu'elle emporte n'est pas silencieux pour autant : le dernier battement de
    chaque run reste au registre et vieillit (#348), ce qui le fera ressortir
    `orphelin` au lieu de rester `en_cours` pour toujours — et `relancer` (#349)
    sait le rejouer sur son brief approuvé.
    """
    annulations: list[str] = []

    async def travail(ordre_du_run: OrdreRun) -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            annulations.append(ordre_du_run.run_id)
            raise

    async def scenario() -> tuple[tuple[str, ...], tuple[str, ...]]:
        hote = HoteRunEnProcess(travail)
        await hote.lancer(ordre())
        await asyncio.sleep(0)  # laisse la tâche démarrer
        avant = hote.runs_en_vol()
        await hote.fermer(delai_s=1.0)
        return avant, hote.runs_en_vol()

    avant, apres = asyncio.run(scenario())

    assert avant == (RUN,)
    assert apres == ()
    assert annulations == [RUN]


def test_l_hote_detache_ne_touche_a_rien_quand_l_api_se_retire(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Le **rien** qui est la livraison du chantier #441, lu depuis l'appelant.

    Les deux méthodes s'appellent pareil, se passent le même `delai_s` et disent le
    contraire l'une de l'autre : c'est là, et nulle part ailleurs, que la frontière
    est visible à l'œil nu. Le comportement du process, lui, est éprouvé sur un
    vrai process (`tests/test_hote_detache.py`) ; ce qui compte ici est que le
    contrat laisse dire les deux.
    """
    monkeypatch.setattr(
        HoteRunDetache, "_ouvrir_process", lambda self, atelier, journal: ProcessQuiVit()
    )
    hote = HoteRunDetache(atelier=tmp_path, delai_demarrage_s=0.0)
    asyncio.run(hote.lancer(ordre()))

    asyncio.run(hote.fermer(delai_s=1.0))

    assert hote.runs_en_vol() == (RUN,)


@pytest.mark.parametrize(
    "fabrique",
    [
        lambda tmp: HoteRunEnProcess(lambda _ordre: asyncio.sleep(0)),
        lambda tmp: HoteRunDetache(atelier=tmp),
    ],
    ids=["en-process", "detache"],
)
def test_annuler_un_run_qu_on_ne_porte_pas_rend_false_sans_lever(fabrique, tmp_path) -> None:
    """`False` n'est pas un échec : c'est le cas normal d'un run orphelin.

    Son hôte est justement tombé — rien à interrompre, rien à signaler —, et
    l'appelant a déjà consigné son issue. Les deux implémentations doivent le dire
    de la même façon : c'est ce que `ServiceExecutions.annuler` lit sans savoir à
    qui il parle.
    """
    hote = fabrique(tmp_path)

    assert asyncio.run(hote.annuler("run-inconnu", delai_s=0.1)) is False
    assert hote.en_vol("run-inconnu") is False


def test_un_hote_qui_n_a_rien_a_ramasser_le_dit_par_un_tuple_vide() -> None:
    """Le no-op hérité est ici la réponse juste, et non un trou.

    Le dérouleur de l'hôte en process *est* le run : il consigne lui-même son issue
    avant de rendre la main, donc il n'existe aucun cas où une tâche s'éteint en
    laissant un run `en_cours` — sauf l'annulation de `fermer`, que l'appelant a
    déjà soldée. `ramasser` est concret pour cette raison, là où `fermer` reste
    abstraite : ici le silence ne cache aucune décision.
    """
    assert HoteRunEnProcess(lambda _ordre: asyncio.sleep(0)).ramasser() == ()


# --- ② Un hôte peut rater son départ ------------------------------------------


def test_un_demarrage_rate_solde_le_run_avec_sa_cause() -> None:
    """Rien ne viendra plus de cet hôte : ni étape, ni battement, ni statut de fin.

    Le run est donc soldé **ici**, à l'instant où l'appelant est encore là pour
    l'écrire, au lieu d'être laissé `en_cours` jusqu'à ce que le seuil d'orphelinat
    l'éteigne une demi-heure plus tard. La cause voyage jusqu'au `detail` : c'est
    la seule information dont dispose quelqu'un qui découvre le run soldé plus
    tard, et elle porte le code de sortie et les dernières lignes du journal.
    """
    hote = HoteMuet(depart_rate="l'hôte détaché du run … s'est arrêté aussitôt (code 2) : boum")
    pilote, projection = service(hote)

    resume = asyncio.run(pilote.lancer("Prototyper un mini-CRM"))

    assert resume["statut"] == EXECUTION_ECHEC
    assert resume["fin"]
    etat = projection.execution(resume["run_id"])
    assert etat is not None
    issue = [e for e in etat.evenements if e.type == EVENEMENT_EXECUTION_STATUT][-1]
    assert "code 2" in issue.detail


def test_un_demarrage_rate_repond_202_et_un_run_deja_solde() -> None:
    """Vu de la route, un départ raté n'est pas une erreur HTTP — c'est un run soldé.

    `POST /api/executions` répond ce qu'il répond toujours : « il est parti, voici
    son résumé ». Ce que le résumé dit, en revanche, n'est plus « en cours » — et
    c'est la bonne lecture : la requête a bien été traitée, c'est le run qui n'a
    pas eu lieu. Rendre 500 ferait chercher une panne d'API là où l'API a
    parfaitement fait son travail.
    """
    app = create_app(hote_run=HoteMuet(depart_rate="rien n'est parti (code 127)"))

    with TestClient(app) as client:
        reponse = client.post("/api/executions", json={"objectif": "Prototyper un mini-CRM"})

        assert reponse.status_code == 202
        resume = reponse.json()
        assert resume["statut"] == EXECUTION_ECHEC
        # Soldé, donc la question de sa vitalité ne se pose plus (#348).
        assert resume["vitalite"] is None


def test_l_ordre_confie_a_l_hote_porte_ce_que_la_route_a_recu() -> None:
    """Des **données**, et rien que des données — c'est le point qui décide du reste.

    Un contrat qui prendrait « la coroutine à dérouler » serait honoré par le seul
    hôte capable de la partager, celui du process courant : on ne sérialise pas une
    fermeture. L'ordre porte donc ce qu'un lancement *dit* — l'objectif, ses
    plafonds, son ticket, son projet, son régime de brief —, c'est-à-dire
    exactement ce que la route a reçu et validé.
    """
    hote = HoteMuet()
    app = create_app(hote_run=hote)

    with TestClient(app) as client:
        client.post(
            "/api/executions",
            json={
                "objectif": "Prototyper un mini-CRM",
                "plafond_cout_usd": 2.5,
                "plafond_tokens": 90_000,
                "timeout_tache_s": 60.0,
                "parallelisme": 2,
                "ticket": {"id": "#42", "url": "https://exemple.test/issues/42"},
            },
        )

    (confie,) = hote.lances
    assert confie.objectif == "Prototyper un mini-CRM"
    assert (confie.plafond_cout_usd, confie.plafond_tokens) == (2.5, 90_000)
    assert (confie.timeout_tache_s, confie.parallelisme) == (60.0, 2)
    assert confie.ticket is not None and confie.ticket.id == "#42"
    # Le régime du brief est celui de la Control Tower (#320, décision D5), et il
    # descend jusqu'à l'hôte : c'est *lui* qui posera la question, où qu'il vive.
    assert confie.mode_brief == "humain"


# --- ② (suite) Un hôte peut mourir sans dire son issue -------------------------


def test_un_hote_mort_sans_issue_solde_son_run_en_echec_avec_sa_cause() -> None:
    """`echec` et non `annulee` : personne n'a dit stop, l'hôte est tombé.

    C'est la seule différence de vocabulaire avec tout ce que solde `_solder`, et
    elle porte tout le sens : un run qu'on interrompt n'a rien raté, un run dont
    l'hôte meurt si. Sans ce geste, il resterait `en_cours` jusqu'au seuil
    d'orphelinat — puis le resterait indéfiniment, `orphelin` n'étant pas un statut
    mais un verdict sur son hôte.
    """
    hote = HoteMuet(morts=(HoteMort(run_id=RUN, cause=CAUSE),))
    pilote, projection = service(hote)
    pose_un_run(pilote)

    asyncio.run(pilote._ramasser())

    resume = pilote.resume(RUN)
    assert resume is not None
    assert resume["statut"] == EXECUTION_ECHEC
    assert resume["fin"]
    etat = projection.execution(RUN)
    assert etat is not None
    assert [e for e in etat.evenements if e.type == EVENEMENT_EXECUTION_STATUT][-1].detail == CAUSE


@pytest.mark.parametrize(
    "statut", [EXECUTION_TERMINEE, EXECUTION_ANNULEE, EXECUTION_ECHEC], ids=lambda s: str(s)
)
def test_un_hote_qui_avait_publie_son_issue_n_a_rien_laisse_a_solder(statut) -> None:
    """L'hôte rend un **constat**, jamais un verdict — c'est ici qu'on tranche.

    « Ce process est mort » ne dit pas « ce run a échoué » : un hôte qui vient de
    publier son issue meurt aussi (#446), et son run n'a rien à ramasser. Seul
    l'appelant, qui lit la projection, peut faire la différence — et la faire dans
    l'hôte lui demanderait de connaître le statut du run, c'est-à-dire exactement
    ce que le contrat existe pour lui épargner.
    """
    hote = HoteMuet(morts=(HoteMort(run_id=RUN, cause=CAUSE),))
    pilote, projection = service(hote)
    pose_un_run(pilote, statut)

    asyncio.run(pilote._ramasser())

    resume = pilote.resume(RUN)
    assert resume is not None
    assert resume["statut"] == statut
    etat = projection.execution(RUN)
    assert etat is not None
    assert all(CAUSE not in e.detail for e in etat.evenements)


def test_le_ramassage_ne_touche_pas_aux_runs_qu_il_n_a_pas_vus_mourir() -> None:
    """Il ne **redéduit pas l'orphelinat**, et il ne solde donc pas les orphelins.

    Deux bornes qui n'en font qu'une : `vitalite` est la seule formule du verdict
    (#348), et un second calcul serait un second endroit à tenir d'accord avec le
    premier. Surtout, un run dont l'hôte est tombé pendant que l'API était arrêtée
    est exactement celui que la relance sur brief (#349) sait rattraper — le solder
    le rendrait au contraire irrattrapable (`run-solde`).
    """
    hote = HoteMuet()
    pilote, _ = service(hote)
    pose_un_run(pilote, run_id="run-muet-depuis-des-heures")

    asyncio.run(pilote._ramasser())

    resume = pilote.resume("run-muet-depuis-des-heures")
    assert resume is not None
    assert resume["statut"] == EXECUTION_EN_COURS
    assert hote.ramassages == 1


def test_une_depouille_dont_le_run_est_inconnu_ne_cree_rien() -> None:
    """Un run absent de la projection n'a pas à y entrer par sa mort.

    Le ramassage solde ce qui existe ; il ne fabrique pas de run. L'inverse ferait
    apparaître à l'écran, en `echec`, un identifiant que personne n'a jamais lancé.
    """
    pilote, projection = service(HoteMuet(morts=(HoteMort(run_id="run-fantome", cause=CAUSE),)))

    asyncio.run(pilote._ramasser())

    assert projection.execution("run-fantome") is None


def test_un_ramassage_en_panne_n_arrete_pas_le_coeur() -> None:
    """Le ramassage vit dans le cœur : une panne ici arrêterait **tous** les battements.

    C'est-à-dire ferait exactement le mal qu'il existe pour réparer — un run muet
    n'a plus qu'à vieillir. Il ne lève donc jamais, au même titre que le battement
    lui-même, et la cause part au journal.
    """
    pilote, _ = service(HoteQuiRefuseDeRamasser())
    pose_un_run(pilote)

    asyncio.run(pilote._ramasser())  # ne lève pas

    resume = pilote.resume(RUN)
    assert resume is not None
    assert resume["statut"] == EXECUTION_EN_COURS


# --- ③ Le déploiement choisit, à un seul endroit -------------------------------


@pytest.mark.parametrize("valeur", [None, "", HOTE_RUN_DETACHE, " DETACHE "])
def test_le_defaut_du_deploiement_est_l_hote_detache(valeur) -> None:
    """La bascule du chantier tient dans la **valeur de repli** d'une ligne.

    Les quatre lots précédents ont livré du code inerte pour que celui-ci n'ait
    rien d'autre à changer. Ce que ce défaut promet est écrit dans `hote_detache` —
    un run survit à son API, pas à sa machine ; ce qu'il exige est un Redis
    joignable. La casse et les espaces sont absorbés : un `.env` recopié à la main
    ne doit pas décider d'une frontière d'exécution.
    """
    assert isinstance(_hote_configure(settings(valeur)), HoteRunDetache)


@pytest.mark.parametrize("brut", ["", "   ", "\tDETACHE\n"])
def test_un_env_bavard_est_normalise_avant_meme_d_arriver_ici(
    monkeypatch: pytest.MonkeyPatch, brut
) -> None:
    """La lecture de l'environnement absorbe déjà la casse et les blancs.

    Le contrôle vaut d'être écrit parce qu'il ferme le seul angle mort de la ligne
    ci-dessus : `(valeur or DÉFAUT)` retomberait sur le défaut pour une chaîne
    **vide**, mais pas pour une chaîne d'espaces, qui est *truthy*. Elle n'atteint
    jamais `_hote_configure` — `load_settings` la réduit à `None` — et c'est ce
    fait-là, non l'ordre des opérations plus bas, qui rend la valeur de repli
    suffisante.
    """
    from maestro.config import load_settings

    monkeypatch.setenv("MAESTRO_HOTE_RUN", brut)

    lu = load_settings().hote_run

    assert lu in (None, HOTE_RUN_DETACHE)
    assert isinstance(_hote_configure(settings(lu)), HoteRunDetache)


@pytest.mark.parametrize("valeur", [HOTE_RUN_EN_PROCESS, "PROCESS"])
def test_la_tache_de_fond_reste_disponible_et_doit_desormais_se_nommer(valeur) -> None:
    """`process` reste là, mais le silence ne le désigne plus — c'est le sens de la bascule.

    Un déploiement qui veut des runs mourant avec l'API le dit, là où c'était
    jusqu'ici ce qu'on obtenait sans rien dire. `None` est rendu pour lui, et non un
    `HoteRunEnProcess()` : le service en fabrique un autour de **son propre**
    dérouleur, qu'on n'a pas ici — c'est le seul hôte à qui l'on puisse passer une
    coroutine, donc le seul défaut de *construction* possible.
    """
    assert _hote_configure(settings(valeur)) is None


def test_un_hote_inconnu_est_une_erreur_franche_qui_nomme_les_deux_valeurs() -> None:
    """Une frontière d'exécution mal orthographiée ne doit jamais ressembler à un choix.

    `MAESTRO_HOTE_RUN=procesus` laisserait croire que les runs meurent avec l'API
    alors qu'ils lui survivent, et la panne ne se verrait qu'au premier arrêt —
    c'est-à-dire trop tard. Même parti pris que `MAESTRO_ISOLATION` (#108). Le
    message nomme les deux hôtes **et** le défaut : c'est ce qui distingue une
    erreur qu'on répare d'une erreur qu'on relit.
    """
    with pytest.raises(ConfigError) as refus:
        _hote_configure(settings("procesus"))

    message = str(refus.value)
    assert "procesus" in message
    assert HOTE_RUN_EN_PROCESS in message
    assert HOTE_RUN_DETACHE in message


def test_sans_hote_injecte_le_service_se_donne_celui_en_process() -> None:
    """Le défaut de **construction** n'est pas celui du déploiement, et c'est voulu.

    Faute d'hôte injecté, `ServiceExecutions` prend `HoteRunEnProcess` : le seul à
    qui l'on puisse passer une coroutine, donc le seul qu'une app puisse se donner
    sans avoir de process à fabriquer — c'est ce que veulent les tests et une démo
    mono-process. Le défaut de production, lui, se résout là où se résolvent le bus,
    le journal et le registre (`create_default_app`, ci-dessus).
    """
    pilote, _ = service(None)  # type: ignore[arg-type] - c'est le cas « pas d'hôte »

    assert isinstance(pilote._hote, HoteRunEnProcess)
