"""Le verdict d'attente, éprouvé pour ce qui le **distingue** (#739, lot 3/3 de #736).

Décision **#651**, [docs/33 §9](../docs/33-decision-surveillance-run.md). Les lots
#737 (le verdict) et #738 (l'écran qui le trie) ont différé leurs tests ici ; ce
fichier porte la moitié backend, `apps/web/tests/runs-immobiles.test.tsx` la moitié
écran.

**Ce n'est pas une suite de régression ordinaire, et la raison est de nature.** Un
verdict de surveillance qui s'allume correctement mais juge de travers rend
exactement l'écran d'avant — c'est ce que `tests/test_battement.py` disait déjà de
`vitalite`, et c'est plus vrai encore ici : le dispositif que ce chantier corrige
n'était pas absent, il était **muet**. `attente_depuis` était posé, sérialisé et
affiché en huit endroits d'`apps/web/`, tous via `formatHeureRelative` et aucun ne le
comparant à quoi que ce soit. Un test qui se contenterait de vérifier qu'un run
suspendu depuis longtemps ressort `en_souffrance: true` passerait **aussi bien** sur
un dispositif qui répondrait `true` à tout run non soldé, et c'est précisément la
version fausse qui coûterait les 31 % de temps de mur de #568 une seconde fois.

D'où les cinq sections, dont trois **sont** les vérifications que
[docs/33 §9](../docs/33-decision-surveillance-run.md) réclame en propre au lot final :

① **La règle** (`en_souffrance`) — la fonction pure, éprouvée comme l'est `vitalite` :
   sans horloge et sans processus, l'instant étant un **argument**. Le seuil et sa
   stricte inégalité, l'horodatage illisible qui signale au lieu de s'abstenir —
   l'**inverse** de `vitalite`, et le test le prouve en montrant les deux côte à côte
   —, l'attente dans le futur, l'horodatage sans fuseau, le run soldé. La table des
   trois attentes est **confrontée** à `STATUTS_EXECUTION_EN_ATTENTE`, si bien qu'une
   quatrième attente ajoutée plus tard hérite du filet ou fait rougir la confrontation.

② **Il survit à un redémarrage de l'API, sans rien de nouveau** (docs/33 §9, tiret 1)
   — la preuve qu'il est **dérivé** et non stocké. Le journal durable est rejoué sur
   une projection neuve et le verdict se reconstruit seul ; rien ne s'écrit dans
   l'intervalle, ni champ de projection ni événement, et les deux moitiés sont
   vérifiées : le verdict revient, **et** rien n'a été persisté pour qu'il revienne.

③ **Il juge l'attente, jamais la durée** (docs/33 §9, tiret 2) — un run au travail ne
   le porte pas, si long soit-il. Le motif est prouvé sur un **échantillon fautif** :
   le run du test travaille depuis six heures, donc la règle qu'il serait facile de
   refabriquer — comparer `debut` au lieu d'`attente_depuis` — s'y déclencherait, et
   c'est ce que le test montre avant de vérifier que la vraie règle se tait.

④ **Il se lève dès que l'attente est tranchée, refus compris** (docs/33 §9, tiret 3)
   — la moitié qu'on oublie. La règle de #571 est qu'un refus rend la main au moteur
   aussi sûrement qu'un accord ; un test qui ne jouerait que l'accord laisserait
   passer un run refusé resté « en souffrance » pour toujours. Et son pendant : une
   **autre demande encore en vol** laisse le run suspendu, donc en souffrance.

⑤ **Le signalement au journal** (`_veiller`) — il dit **une fois**, il oublie quand
   l'attente est tranchée, et il redit sur l'attente suivante. C'est la seule part du
   dispositif qui écrive quelque chose, et ce qu'elle garde n'est pas le verdict (qui
   se recalcule à chaque tour) mais le fait de l'avoir **annoncé**.

**Ni réseau, ni Redis, ni appel de modèle, ni process.** Les sections ① et ④ ne
touchent que la projection et la fonction ; ② monte l'app réelle sur bus mémoire, à
la manière de `tests/test_battement.py` ; ⑤ appelle `_veiller` directement, comme
`tests/test_hote_run.py` appelle `_ramasser` — ce qu'on veut lire est une décision,
pas un ordonnancement, et passer par le cœur du service obligerait à dormir ou à
transformer le test en course.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from maestro.controltower import (
    VITALITE_INDETERMINE,
    ControlTowerState,
    InMemoryEventBus,
    InMemoryEventLog,
    create_app,
)
from maestro.controltower.battement import SEUIL_ORPHELIN_S, vitalite
from maestro.controltower.events import (
    EVENEMENT_BRIEF_DECISION,
    EVENEMENT_BRIEF_DEMANDE,
    EVENEMENT_BRIEF_QUESTIONS,
    EVENEMENT_BRIEF_REPONSES,
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_VALIDATION_DECISION,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
)
from maestro.controltower.executions import ServiceExecutions
from maestro.controltower.souffrance import SEUIL_SOUFFRANCE_S, en_souffrance
from maestro.controltower.state import (
    EXECUTION_ANNULEE,
    EXECUTION_ECHEC,
    EXECUTION_EN_ATTENTE_ARBITRAGE,
    EXECUTION_EN_ATTENTE_BRIEF,
    EXECUTION_EN_ATTENTE_REPONSES,
    EXECUTION_EN_COURS,
    EXECUTION_TERMINEE,
    STATUTS_EXECUTION_EN_ATTENTE,
    STATUTS_EXECUTION_TERMINAUX,
    VALIDATION_APPROUVEE,
    VALIDATION_EN_ATTENTE,
    VALIDATION_REFUSEE,
)

#: Le run, sa tâche sensible et son projet — les trois identités de #568, reprises
#: telles quelles de `tests/test_arbitrage_visible.py` : c'est le même incident qui
#: a produit ce chantier-ci, un cran plus loin.
RUN = "5f531654e03b"
TACHE = "deploiement"
PROJET = "prj-7f3a"

#: L'instant de référence des tests **sans horloge** (section ①). Fixe, donc les
#: verdicts de cette section ne dépendent ni du jour où on les joue, ni de la charge
#: de la machine : c'est exactement le régime que `MAINTENANT` donne à
#: `tests/test_battement.py`, et c'est ce que « éprouvée comme l'est `vitalite` »
#: veut dire dans le critère du ticket.
MAINTENANT = datetime(2026, 8, 28, 12, 0, 0, tzinfo=UTC)

#: Les trois statuts terminaux, dans l'ordre où `state.py` les nomme.
SOLDES = (EXECUTION_TERMINEE, EXECUTION_ANNULEE, EXECUTION_ECHEC)


def _il_y_a(secondes: float, *, depuis: datetime = MAINTENANT) -> str:
    """L'horodatage ISO-8601 d'un instant `secondes` avant `depuis`."""
    return (depuis - timedelta(seconds=secondes)).isoformat(timespec="seconds")


def _vraiment_il_y_a(secondes: float) -> str:
    """Le même, mais depuis l'horloge **réelle** — pour les sections ② à ⑤.

    Elles montent l'app ou le service, qui appellent `en_souffrance` sans
    `maintenant` : l'ancienneté doit donc être vraie *maintenant*. Les écarts
    utilisés sont d'un quart d'heure au moins, là où ces tests durent quelques
    millisecondes — la marge n'est pas une course, c'est un ordre de grandeur.
    """
    return _il_y_a(secondes, depuis=datetime.now(UTC))


# --------------------------------------------------------------------------- #
# ① La règle — sans horloge, sans processus
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Attente:
    """Une attente humaine : son statut, et l'événement qui y suspend un run.

    ⚠ **Cette table n'est pas celle de `tests/test_arbitrage_visible.py`**, et le
    doublon est délibéré : là-bas les horodatages de suspension sont **fixes**,
    parce que le sujet est la pose et la levée de l'ancienneté ; ici l'âge de
    l'attente **est** la variable qu'on fait varier autour du seuil. Ce qui rend la
    seconde table sûre n'est pas la discipline mais la **confrontation** au backend
    (`test_aucune_attente_n_echappe_a_la_table` ci-dessous) — le mécanisme que #572 a
    précisément conçu pour ça : une quatrième attente fait rougir les deux tables, ou
    aucune.
    """

    nom: str
    statut: str
    #: La fabrique de l'événement qui suspend le run, à partir de son horodatage —
    #: c'est lui qui devient `attente_depuis`.
    suspend: Callable[[str], Event]


def _demande_brief(horodatage: str) -> Event:
    return Event(type=EVENEMENT_BRIEF_DEMANDE, run_id=RUN, horodatage=horodatage)


def _questions_brief(horodatage: str) -> Event:
    return Event(
        type=EVENEMENT_BRIEF_QUESTIONS,
        run_id=RUN,
        tour=1,
        tours_max=2,
        horodatage=horodatage,
    )


def _demande_arbitrage(horodatage: str, *, tache_id: str = TACHE) -> Event:
    return Event(
        type=EVENEMENT_VALIDATION_DEMANDE,
        run_id=RUN,
        tache_id=tache_id,
        projet_id=PROJET,
        statut=VALIDATION_EN_ATTENTE,
        horodatage=horodatage,
    )


ATTENTES: tuple[Attente, ...] = (
    Attente("brief", EXECUTION_EN_ATTENTE_BRIEF, _demande_brief),
    Attente("réponses", EXECUTION_EN_ATTENTE_REPONSES, _questions_brief),
    Attente("arbitrage", EXECUTION_EN_ATTENTE_ARBITRAGE, _demande_arbitrage),
)


def test_aucune_attente_n_echappe_a_la_table():
    """Le filet dont hérite une **quatrième** attente ajoutée plus tard.

    Sans cette confrontation, un `en_attente_<quelque chose>` de plus s'ajouterait à
    `STATUTS_EXECUTION_EN_ATTENTE` — donc au domaine d'`en_souffrance`, qui lit cet
    ensemble et rien d'autre — sans qu'aucun test ci-dessous ne le regarde. C'est le
    mécanisme de `tests/test_arbitrage_visible.py`, repris ici parce que la table
    l'est aussi.
    """
    assert {attente.statut for attente in ATTENTES} == set(STATUTS_EXECUTION_EN_ATTENTE)


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_une_attente_fraiche_ne_souffre_pas(attente: Attente):
    """Le cas nominal : le run attend, mais depuis une minute — rien à signaler."""
    assert (
        en_souffrance(attente.statut, _il_y_a(60), maintenant=MAINTENANT) is False
    )


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_une_attente_au_dela_du_seuil_souffre(attente: Attente):
    """Le verdict que #568 n'avait pas, sur les trois attentes.

    Un seul seuil pour les trois, et c'est un point du contrat : le dépôt a déjà
    tranché que « depuis quand attend-il ? » n'a qu'une réponse (un seul
    `attente_depuis`), donc en écrire trois rouvrirait cette décision par la bande
    ([docs/33 §9](../docs/33-decision-surveillance-run.md)).
    """
    assert (
        en_souffrance(
            attente.statut, _il_y_a(SEUIL_SOUFFRANCE_S + 1), maintenant=MAINTENANT
        )
        is True
    )


def test_le_seuil_est_strict_et_vaut_la_moitie_de_celui_de_l_orphelinat():
    """Pile au seuil on ne souffre pas encore — et la **valeur** est vérifiée.

    Les deux moitiés comptent. La stricte inégalité d'abord : elle est la seule
    chose qui distingue « au seuil » de « au-delà », et un `>=` posé par
    inadvertance déplacerait la frontière d'un tour de boucle sans que rien ne le
    dise. La valeur ensuite, comme `tests/test_battement.py` vérifie que le seuil
    d'orphelinat reste généreux : ici le rapport à `SEUIL_ORPHELIN_S` **est**
    l'argument écrit dans `souffrance.py` — la moitié, parce que l'asymétrie des
    erreurs est inversée (là-bas se tromper détruit, ici un faux positif coûte une
    ligne qu'on regarde et qu'on oublie).

    Ce n'est pas figer un chiffre : docs/33 §5.4 dit d'avance qu'il bougera. C'est
    garder qu'il reste **un seuil motivé** et non une valeur ramassée au passage —
    le déplacer se fait alors en touchant les deux endroits qui le disent.
    """
    assert (
        en_souffrance(
            EXECUTION_EN_ATTENTE_BRIEF,
            _il_y_a(SEUIL_SOUFFRANCE_S),
            maintenant=MAINTENANT,
        )
        is False
    )
    assert SEUIL_SOUFFRANCE_S == SEUIL_ORPHELIN_S / 2
    assert SEUIL_SOUFFRANCE_S == 15 * 60


def test_le_seuil_est_injectable_sans_toucher_a_la_constante():
    """Le seuil est un **argument**, ce qui est ce qui rend cette suite sans horloge.

    C'est aussi ce dont `ServiceExecutions` se sert (`seuil_souffrance_s`) : la
    section ⑤ en dépend, et un défaut ici s'y lirait comme un défaut de la mémoire
    d'annonce.
    """
    quart_dheure = _il_y_a(16 * 60)

    assert en_souffrance(EXECUTION_EN_ATTENTE_BRIEF, quart_dheure, maintenant=MAINTENANT)
    assert not en_souffrance(
        EXECUTION_EN_ATTENTE_BRIEF, quart_dheure, maintenant=MAINTENANT, seuil_s=3600.0
    )


def test_un_horodatage_illisible_signale_au_lieu_de_s_abstenir():
    """L'écart le plus contre-intuitif du dispositif, et il est **l'inverse** de `vitalite`.

    Les deux verdicts reçoivent une donnée qu'ils ne savent pas lire et concluent
    dans deux sens opposés — c'est voulu, et ça se prouve en les montrant côte à
    côte plutôt qu'en l'affirmant dans un commentaire. Là-bas, affirmer la mort sur
    une donnée illisible déclenche une reprise depuis le cadrage : on s'abstient
    (`indetermine`). Ici « ce run est suspendu et on ne sait même pas depuis quand »
    est **pire** que « suspendu depuis vingt minutes », et le signaler ne casse rien.
    """
    illisible = "hier après-midi"

    assert en_souffrance(EXECUTION_EN_ATTENTE_BRIEF, illisible, maintenant=MAINTENANT)
    assert (
        vitalite(EXECUTION_EN_ATTENTE_BRIEF, illisible, maintenant=MAINTENANT)
        == VITALITE_INDETERMINE
    )


@pytest.mark.parametrize("manquant", [None, ""], ids=["absent", "vide"])
def test_une_attente_sans_anciennete_signale(manquant):
    """Même règle, sur l'autre forme du même trou.

    Le statut affirme l'attente, l'ancienneté ne dit rien : la contradiction est
    elle-même l'information, et un run dont on ignore depuis quand il dort est le
    dernier qu'il faudrait taire.
    """
    assert en_souffrance(EXECUTION_EN_ATTENTE_ARBITRAGE, manquant, maintenant=MAINTENANT)


def test_une_attente_dans_le_futur_est_fraiche():
    """Horloges désaccordées entre l'hôte et l'API : l'écart n'invente pas une souffrance.

    La valeur est **lisible** — la règle du test précédent ne s'y applique donc pas,
    et c'est l'arithmétique qui parle. Même conduite que `vitalite`, pour une fois.
    """
    assert not en_souffrance(
        EXECUTION_EN_ATTENTE_REPONSES, _il_y_a(-300), maintenant=MAINTENANT
    )


def test_un_horodatage_sans_fuseau_est_lu_en_utc():
    """La seule forme que les événements écrivent (#46), et la comparaison ne lève pas.

    Naïvement comparée à un instant *aware*, une valeur sans fuseau ferait lever
    `datetime` — donc rendrait 500 la liste des runs, sur le champ même qu'on
    regarde quand quelque chose ne va pas. Le verdict doit être **le même** que sur
    la forme datée : c'est ce que « lu en UTC » veut dire.
    """
    nu = _il_y_a(SEUIL_SOUFFRANCE_S + 1).removesuffix("+00:00")
    assert "+" not in nu  # le décor est bien celui qu'on croit

    assert en_souffrance(EXECUTION_EN_ATTENTE_BRIEF, nu, maintenant=MAINTENANT)
    assert not en_souffrance(
        EXECUTION_EN_ATTENTE_BRIEF, _il_y_a(60).removesuffix("+00:00"), maintenant=MAINTENANT
    )


@pytest.mark.parametrize("statut", SOLDES, ids=lambda s: str(s))
def test_un_run_solde_ne_souffre_pas(statut: str):
    """Un run qui a rendu son issue n'attend plus personne — et il rend `False`, pas `None`.

    Le verdict est **binaire** là où `vitalite` est ternaire, et c'est un choix :
    ici « sans objet » et « faux » se disent du même mot, parce que le troisième
    état — « il attend, mais pas depuis trop longtemps » — est déjà porté par le
    statut. Le reporter dans le verdict serait un second support pour un même fait,
    c'est-à-dire la panne que #365 a supprimée sur le cycle de vie.

    Et c'est vrai **même si l'ancienneté traîne encore** : c'est le statut qui
    tranche, jamais la donnée résiduelle.
    """
    assert statut in STATUTS_EXECUTION_TERMINAUX
    assert en_souffrance(statut, _il_y_a(10_000), maintenant=MAINTENANT) is False
    assert vitalite(statut, _il_y_a(10_000), maintenant=MAINTENANT) is None


def test_sans_instant_donne_le_verdict_est_rendu_sur_l_horloge():
    """Le chemin par défaut, qui est celui qu'empruntent l'API et la veille.

    Tous les tests ci-dessus passent `maintenant` — c'est ce qui les rend
    reproductibles —, si bien qu'aucun n'exerce le `datetime.now(UTC)` par défaut.
    Une attente de 2020 est au-delà du seuil quel que soit le jour où on joue ce
    test : la vérification reste déterministe sans cesser d'être réelle.
    """
    assert en_souffrance(EXECUTION_EN_ATTENTE_BRIEF, "2020-01-01T00:00:00+00:00")
    assert not en_souffrance(EXECUTION_EN_COURS, "2020-01-01T00:00:00+00:00")


# --------------------------------------------------------------------------- #
# ② Le verdict survit à un redémarrage — la preuve qu'il est dérivé
# --------------------------------------------------------------------------- #


def _app(journal: InMemoryEventLog):
    """L'app réelle sur bus mémoire, autour du journal durable donné.

    Aucun run n'est lancé dans cette section : le décor est **relu** du journal,
    exactement comme `maestro-api` le relit au démarrage. Il n'y a donc ni moteur à
    doubler, ni hôte à monter.
    """
    return create_app(
        bus=InMemoryEventBus(),
        state=ControlTowerState(),
        event_log=journal,
    )


def _journal(*evenements: Event) -> InMemoryEventLog:
    """Un journal durable portant ces événements, dans cet ordre.

    Rempli par `consigner` plutôt que par son attribut interne : c'est le contrat du
    journal, et c'est ce que le rejeu relira.
    """
    journal = InMemoryEventLog()
    for event in evenements:
        asyncio.run(journal.consigner(event))
    return journal


def _lancement(horodatage: str) -> Event:
    """L'événement de lancement d'un run — il le crée et le met au travail."""
    return Event(
        type=EVENEMENT_EXECUTION_STATUT,
        run_id=RUN,
        titre="Mettre l'API en production",
        statut=EXECUTION_EN_COURS,
        projet_id=PROJET,
        horodatage=horodatage,
    )


def test_le_verdict_survit_a_un_redemarrage_de_l_api_sans_rien_de_nouveau():
    """La vérification n°1 de [docs/33 §9](../docs/33-decision-surveillance-run.md).

    Un run suspendu depuis vingt minutes est signalé ; l'API redémarre — projection
    neuve, même journal durable —, et il l'est encore. Ce n'est pas une redite du
    test précédent : il n'existe **aucun champ** où le verdict aurait pu être rangé,
    donc s'il revient c'est qu'il s'est recalculé, et c'est là toute la propriété.

    Les deux moitiés sont vérifiées, et la seconde est celle qui garde le contrat :
    le verdict revient, **et** rien n'a été persisté pour qu'il revienne. Le jour où
    quelqu'un le stockera, ce test passerait encore avec la première moitié seule —
    on aurait alors deux vérités, et la seconde se périmerait (docs/33 §8).
    """
    journal = _journal(
        _lancement(_vraiment_il_y_a(3600)),
        _demande_brief(_vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)),
    )
    avant = asyncio.run(journal.relire())

    with TestClient(_app(journal)) as premiere:
        (resume,) = premiere.get("/api/executions?projet=tous").json()
        assert resume["statut"] == EXECUTION_EN_ATTENTE_BRIEF
        assert resume["en_souffrance"] is True

    # L'API redémarre : nouvelle app, nouvelle projection, même journal — ce que
    # retrouve `maestro-api` au redémarrage.
    with TestClient(_app(journal)) as redemarree:
        (apres,) = redemarree.get("/api/executions?projet=tous").json()
        assert apres["en_souffrance"] is True

    # Rien de nouveau : ni événement écrit, ni type inventé. Le verdict n'a laissé
    # aucune trace derrière lui, et c'est ce qui lui permet de se recalculer juste.
    assert [e.type for e in asyncio.run(journal.relire())] == [e.type for e in avant]


def test_le_verdict_n_est_pas_un_champ_de_la_projection():
    """L'autre bout de « dérivé, jamais stocké » — celui qui se lit sur la donnée.

    `EtatExecution.resume()` est ce que la projection sait d'un run ; le verdict n'y
    est pas, il est **ajouté à la lecture** par `_avec_vitalite`, au même titre que
    `vitalite`. Un champ qui apparaîtrait ici serait la seconde vérité que docs/33
    §8 refuse — et il apparaîtrait en silence, la vue publique étant identique.
    """
    etat = ControlTowerState()
    etat.appliquer(_lancement(_vraiment_il_y_a(3600)))
    etat.appliquer(_demande_brief(_vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)))

    resume = etat.execution(RUN).resume()

    assert "attente_depuis" in resume  # la donnée, elle, est bien projetée
    assert "en_souffrance" not in resume
    assert "vitalite" not in resume  # le patron dont il est repris, à l'identique


def test_le_detail_d_un_run_porte_le_meme_verdict_que_la_liste():
    """Une liste qui saurait qu'un run souffre pendant que son écran l'ignore serait
    une couture, pas une économie — la raison même pour laquelle les deux verdicts
    de veille sont posés par le **même** chemin (`_avec_vitalite`)."""
    journal = _journal(
        _lancement(_vraiment_il_y_a(3600)),
        _demande_arbitrage(_vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)),
    )

    with TestClient(_app(journal)) as client:
        (resume,) = client.get("/api/executions?projet=tous").json()
        detail = client.get(f"/api/executions/{RUN}").json()

    assert resume["en_souffrance"] is True
    assert detail["en_souffrance"] is True


# --------------------------------------------------------------------------- #
# ③ Il juge l'attente, jamais la durée
# --------------------------------------------------------------------------- #


def test_un_run_au_travail_ne_souffre_jamais_si_long_soit_il():
    """La vérification n°2 de [docs/33 §9](../docs/33-decision-surveillance-run.md).

    Le run travaille depuis **six heures**, soit vingt-quatre fois le seuil, et il
    ne porte pas le verdict : il n'attend personne. C'est la confusion exacte que
    `vitalite` a évitée (`battement.py:137-141`) et qu'il serait facile de
    refabriquer ici en comparant `debut` au lieu d'`attente_depuis`.

    **Le motif est prouvé sur l'échantillon avant d'être vérifié** : le test montre
    d'abord que la règle fautive s'y déclencherait — six heures dépassent largement
    le seuil —, sans quoi « le verdict est `False` » serait un ✓ sur une question
    jamais posée, vrai de n'importe quel run.
    """
    debut = _vraiment_il_y_a(6 * 3600)
    journal = _journal(_lancement(debut))

    with TestClient(_app(journal)) as client:
        (resume,) = client.get("/api/executions?projet=tous").json()

    # L'échantillon est bien fautif pour la règle qu'on refuse : ce run travaille
    # depuis vingt-quatre fois le seuil, donc un verdict fondé sur la **durée**
    # s'allumerait ici.
    ecoule = (datetime.now(UTC) - datetime.fromisoformat(resume["debut"])).total_seconds()
    assert ecoule > SEUIL_SOUFFRANCE_S

    assert resume["statut"] == EXECUTION_EN_COURS
    assert resume["attente_depuis"] is None
    assert resume["en_souffrance"] is False


def test_un_run_qui_a_attendu_puis_reparti_ne_souffre_plus_de_son_attente_passee():
    """Le même principe, sur le cas qui l'use : l'attente a eu lieu, elle est finie.

    Sans cette vérification, une implémentation qui garderait la plus ancienne
    attente vue — plutôt que celle en cours — passerait les deux tests précédents et
    signalerait pour toujours tout run ayant patienté une fois. L'ancienneté est
    **effacée** à la sortie d'attente (`state.py`), et le verdict n'a rien d'autre à
    lire : c'est ce couplage-là qu'on garde.
    """
    journal = _journal(
        _lancement(_vraiment_il_y_a(3 * 3600)),
        _demande_brief(_vraiment_il_y_a(2 * 3600)),
        Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=VALIDATION_APPROUVEE),
    )

    with TestClient(_app(journal)) as client:
        (resume,) = client.get("/api/executions?projet=tous").json()

    assert resume["statut"] == EXECUTION_EN_COURS
    assert resume["en_souffrance"] is False


# --------------------------------------------------------------------------- #
# ④ Il se lève dès que l'attente est tranchée — refus compris
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Issue:
    """Une façon de sortir d'une attente, et l'état où elle laisse le run."""

    nom: str
    evenement: Event
    statut_apres: str


#: `(attente, issue)` aplati — chaque cas porte un identifiant lisible.
#:
#: L'issue **défavorable** n'a pas la même nature partout : on refuse un brief, on
#: refuse un arbitrage, mais on ne « refuse » pas des questions — le geste symétrique
#: y est l'annulation du run, éprouvée pour les trois par le test qui suit la table.
ISSUES: tuple[tuple[Attente, Issue], ...] = (
    (
        ATTENTES[0],
        Issue(
            "accord",
            Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=VALIDATION_APPROUVEE),
            EXECUTION_EN_COURS,
        ),
    ),
    (
        ATTENTES[0],
        Issue(
            "refus",
            Event(type=EVENEMENT_BRIEF_DECISION, run_id=RUN, statut=VALIDATION_REFUSEE),
            EXECUTION_ANNULEE,
        ),
    ),
    (
        ATTENTES[1],
        Issue(
            "réponses",
            Event(type=EVENEMENT_BRIEF_REPONSES, run_id=RUN),
            EXECUTION_EN_COURS,
        ),
    ),
    (
        ATTENTES[2],
        Issue(
            "accord",
            Event(
                type=EVENEMENT_VALIDATION_DECISION,
                tache_id=TACHE,
                statut=VALIDATION_APPROUVEE,
            ),
            EXECUTION_EN_COURS,
        ),
    ),
    (
        ATTENTES[2],
        Issue(
            "refus",
            Event(
                type=EVENEMENT_VALIDATION_DECISION,
                tache_id=TACHE,
                statut=VALIDATION_REFUSEE,
            ),
            EXECUTION_EN_COURS,
        ),
    ),
)


def _run_en_souffrance(attente: Attente) -> ControlTowerState:
    """Une projection portant un run suspendu **au-delà du seuil** sur cette attente."""
    etat = ControlTowerState()
    etat.appliquer(_lancement(_vraiment_il_y_a(2 * 3600)))
    etat.appliquer(attente.suspend(_vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)))
    return etat


def _verdict(etat: ControlTowerState) -> bool:
    """Le verdict tel que `_avec_vitalite` le sert, sur la projection donnée."""
    resume = etat.execution(RUN).resume()
    return en_souffrance(resume["statut"], resume["attente_depuis"])


@pytest.mark.parametrize(("attente", "issue"), ISSUES, ids=lambda x: x.nom)
def test_toute_issue_leve_le_verdict_refus_compris(attente: Attente, issue: Issue):
    """La vérification n°3 de [docs/33 §9](../docs/33-decision-surveillance-run.md).

    **C'est celle qu'on oublie.** La règle de #571 est qu'un refus lève l'attente
    autant qu'un accord — il rend la main au moteur aussi sûrement —, et un test qui
    ne jouerait que l'accord laisserait passer un run refusé resté « en souffrance »
    pour toujours, avec un compteur d'ancienneté qui court : la promesse fausse que
    ce chantier supprime, retournée.

    Le verdict est vérifié **posé** avant l'issue, faute de quoi « il ne souffre
    plus » serait vrai d'un décor où il n'aurait jamais souffert.
    """
    etat = _run_en_souffrance(attente)
    assert _verdict(etat) is True

    etat.appliquer(issue.evenement)

    resume = etat.execution(RUN).resume()
    assert resume["statut"] == issue.statut_apres
    assert resume["attente_depuis"] is None
    assert _verdict(etat) is False


@pytest.mark.parametrize("attente", ATTENTES, ids=lambda a: a.nom)
def test_une_annulation_en_pleine_attente_leve_le_verdict(attente: Attente):
    """Le geste symétrique commun aux trois : arrêter le run au lieu de lui répondre.

    C'est l'issue *défavorable* de l'attente de réponses, qui n'en a pas d'autre, et
    un chemin que les deux autres partagent. Sans lui, un run qu'on annule pendant
    qu'il dort resterait signalé « personne n'a répondu » sur un écran qui propose
    d'aller le voir — le pire des faux positifs, puisqu'il survit à sa correction.
    """
    etat = _run_en_souffrance(attente)
    assert _verdict(etat) is True

    etat.appliquer(
        Event(type=EVENEMENT_EXECUTION_STATUT, run_id=RUN, statut=EXECUTION_ANNULEE)
    )

    assert _verdict(etat) is False


def test_une_autre_demande_en_vol_garde_le_run_en_souffrance():
    """La moitié qu'une lecture littérale de « toute issue lève » ferait perdre.

    Un run peut porter plusieurs demandes (#568 : trois tâches sensibles sur trois).
    Trancher la première ne lui rend pas un « en cours » qu'il n'a pas — donc le
    verdict **tient**, et il le doit : c'est exactement l'état où le run reste
    bloqué, celui qu'on ne veut surtout pas voir disparaître de l'écran au premier
    arbitrage rendu.

    L'ancienneté ne bouge pas non plus : elle est celle de la **première** demande,
    et la repousser rajeunirait indéfiniment une attente qui dure.
    """
    etat = ControlTowerState()
    etat.appliquer(_lancement(_vraiment_il_y_a(2 * 3600)))
    premiere = _vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)
    etat.appliquer(_demande_arbitrage(premiere, tache_id="t1"))
    etat.appliquer(_demande_arbitrage(_vraiment_il_y_a(60), tache_id="t2"))

    etat.appliquer(
        Event(type=EVENEMENT_VALIDATION_DECISION, tache_id="t1", statut=VALIDATION_APPROUVEE)
    )

    resume = etat.execution(RUN).resume()
    assert resume["statut"] == EXECUTION_EN_ATTENTE_ARBITRAGE
    assert resume["attente_depuis"] == premiere
    assert _verdict(etat) is True


# --------------------------------------------------------------------------- #
# ⑤ Le signalement au journal — dit une fois, oublié quand l'attente est tranchée
# --------------------------------------------------------------------------- #


def _service() -> ServiceExecutions:
    """Un service nu — la veille ne lit que le résumé qu'on lui tend.

    Aucun moteur, aucun hôte, aucun cœur démarré : `_veiller` est appelée
    directement, comme `tests/test_hote_run.py` appelle `_ramasser`. Passer par le
    réveil obligerait à dormir trente secondes, ou à régler la période assez bas
    pour que le test devienne une course — et ce qu'on veut lire est une décision,
    pas un ordonnancement.
    """
    return ServiceExecutions(InMemoryEventBus(), ControlTowerState())


def _resume(statut: str, attente_depuis: str | None) -> dict[str, object]:
    """Le strict nécessaire de ce que `_veiller` lit sur un résumé."""
    return {"run_id": RUN, "statut": statut, "attente_depuis": attente_depuis}


def test_la_veille_dit_la_souffrance_une_seule_fois(caplog):
    """Le réveil repasse toutes les 30 s : une attente d'une heure vaudrait 120 lignes.

    Une règle qui crie trop se règle en déplaçant son seuil, jamais en ajoutant un
    juge qui trierait ses propres cris (docs/33 §4.3). Ce qui est mémorisé n'est
    donc **pas le verdict** — il se recalcule à chaque tour et à chaque lecture,
    c'est la propriété qu'on ne défait pas — mais le fait de l'avoir **annoncé**.
    """
    service = _service()
    resume = _resume(EXECUTION_EN_ATTENTE_ARBITRAGE, _vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300))

    with caplog.at_level(logging.WARNING, logger="maestro.controltower"):
        service._veiller(resume)
        service._veiller(resume)
        service._veiller(resume)

    lignes = [r for r in caplog.records if RUN in r.getMessage()]
    assert len(lignes) == 1
    # Le signalement dit ce qu'il ne fait pas : rien n'est annulé ni relancé.
    assert "signalement" in lignes[0].getMessage()


def test_la_veille_se_tait_sur_un_run_qui_travaille(caplog):
    """Rien à dire d'un run qui avance — et l'abstention est muette, pas silencieuse
    par accident : c'est le même verdict que celui servi sur le résumé, donc un run
    au travail ne peut pas y entrer."""
    service = _service()

    with caplog.at_level(logging.WARNING, logger="maestro.controltower"):
        service._veiller(_resume(EXECUTION_EN_COURS, None))

    assert [r for r in caplog.records if RUN in r.getMessage()] == []


def test_une_attente_tranchee_puis_une_autre_est_annoncee_de_nouveau(caplog):
    """La mémoire d'annonce **s'oublie** dès que l'attente est levée, refus compris.

    C'est la contrepartie du test précédent, et elle est indispensable : sans
    l'oubli, un run qui repart puis se ressuspend serait signalé **une seule fois
    dans sa vie**, et sa seconde attente — qui est une autre attente — passerait en
    silence. Le run de #568 en portait trois d'affilée.
    """
    service = _service()
    vieille = _vraiment_il_y_a(SEUIL_SOUFFRANCE_S + 300)

    with caplog.at_level(logging.WARNING, logger="maestro.controltower"):
        service._veiller(_resume(EXECUTION_EN_ATTENTE_ARBITRAGE, vieille))
        # L'arbitrage est rendu — accord **ou** refus, le run repart (#571).
        service._veiller(_resume(EXECUTION_EN_COURS, None))
        # Puis il se ressuspend, et cette attente-là est une autre attente.
        service._veiller(_resume(EXECUTION_EN_ATTENTE_BRIEF, vieille))

    assert len([r for r in caplog.records if RUN in r.getMessage()]) == 2
