"""La surface d'écriture des agents pendant un run (#721, parent #717).

Lot final « tests + doc » du chantier ouvert par la note de décision
[docs/31](../docs/31-decision-surface-ecriture-agents.md). Les trois lots
précédents n'ont embarqué que les tests de leur logique critique — le
porte-outils (#718), `signaler_blocage` (#719), `ecrire_a_un_pair` (#720) — et
ont différé le reste ici. Ce fichier porte ce reste, en trois blocs, un par
critère du ticket, et chacun éprouve **la moitié qui ne se voit pas** :

① **les deux verbes par leur promesse la moins évidente.** Ce qui se teste mal
   n'est pas le chemin nominal — un mot part, un blocage se consigne — mais les
   deux cas où le comportement juste ressemble à une panne : un mot adressé à un
   pair **absent** est consigné *malgré tout* (docs/31 §3.2 : le journal est la
   livraison, le pub/sub n'est que la notification), et une déclaration de
   blocage **sans raison** n'est *pas* consignée (docs/31 §3.1 : « il est
   bloqué » est ce que la frise montrait déjà, la raison est tout ce que le
   verbe apporte). Les deux sont pris **des deux côtés** — l'outil servi à
   l'agent et le canal de l'exécuteur —, parce que ces chemins ont chacun deux
   entrées et que les garder une seule fois laisserait l'autre libre de dériver ;

② **le graphe du plan est inchangé** après un run où les deux verbes ont été
   appelés. C'est l'invariant central de la note (§5) et **le seul qui ne se voit
   pas à l'œil** : un verbe qui ajouterait une tâche, poserait un statut ou
   réassignerait quelqu'un ne ferait rien échouer — il rendrait simplement le
   brief approuvé caduc, en silence. Il se prouve par **comparaison de deux
   runs** sur le même plan, l'un qui appelle les deux verbes et l'autre non ;

③ **la synthèse Markdown porte le `task_id`** (§6) — la dette que ce lot paie.
   Le refus de l'identité d'instance tenait à ce que `tache_id` la porte déjà
   partout où l'on mesure ; une surface y échappait, et c'est celle qu'un humain
   lit. Le test **prouve d'abord son échantillon** : il rejoue l'ancienne formule
   sur le plan à deux tâches homonymes et vérifie qu'elle rendait bien deux
   sections identiques, *avant* de conclure de leur distinction. Sans cette
   moitié, il rendrait un ✓ sur une question jamais posée.

Aucun appel réseau, aucun quota : plans constants, fournisseurs factices,
boîtes aux lettres en mémoire. Le harnais est celui de
`tests/test_arbitrage_acte.py` — le lot final du chantier voisin, sur la même
surface — plutôt qu'un second à tenir d'accord.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from maestro.engine import OrchestrationEngine
from maestro.engine.executor import STATUT_BLOCAGE_SIGNALE, SUFFIXE_ETAPE_BLOCAGE
from maestro.messaging.handoff import AGENT_RELAIS
from maestro.messaging.mailbox import (
    MESSAGE_NOTIFICATION,
    SUFFIXE_ETAPE_MESSAGE,
    AgentMessage,
    InMemoryMailbox,
)
from maestro.orchestrator import Orchestrator
from maestro.providers import blocage, courrier
from maestro.providers.arbitrage import NOM_SERVEUR
from maestro.providers.base import ModelProvider
from maestro.providers.claude import _outil_blocage, _outil_courrier
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
    """Exécutant outillé factice : rend son livrable, sans toucher à aucun canal.

    Le double **de base** déclare le protocole en toutes lettres (`on_blocage`,
    `on_courrier`…) plutôt que par `**kwargs` — les spécialisations ci-dessous ne
    font que le relayer. C'est ce qui fait rougir les doubles le jour où le
    protocole s'élargit, au lieu de les laisser ignorer en silence un canal qu'on
    vient d'ouvrir : la suite entière passe par ici.
    """

    name = "executant"

    def __init__(self) -> None:
        self.appels: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        return "TEXTE"

    async def run_agent(
        self, prompt, *, model, system_prompt=None, workspace, tools,
        mcp_serveurs=(), politique=None, on_refus=None, on_arbitrage_acte=None,
        on_activite=None, on_etapes=None, on_arbitrage=None, on_blocage=None,
        credit_arbitrage=None, on_courrier=None, plafond_tours=None, projet=None,
    ):
        (Path(workspace) / "livrable.txt").write_text("contenu", encoding="utf-8")
        self.appels.append(prompt)
        return f"OUTILLE #{len(self.appels)}"


class EcritAuPair(_Executant):
    """Exécutant qui appelle `ecrire_a_un_pair` sur sa **première** tâche (#720).

    Une seule fois, et sur la première tâche seulement : le compte exact est ce
    qui permet de dire, plus loin, qu'une trace de plus vient bien de ce verbe et
    pas d'un double qui aurait écrit à chaque tour.
    """

    name = "ecrit-au-pair"

    DESTINATAIRE = "recette"
    MESSAGE = "Le dépôt de recette refuse mes identifiants — prévois un jeton."

    def __init__(self, destinataire: str | None = None) -> None:
        super().__init__()
        self.destinataire = self.DESTINATAIRE if destinataire is None else destinataire
        self.ecrits = 0

    async def run_agent(self, prompt, **kw):
        if kw.get("on_courrier") is not None and self.ecrits == 0:
            self.ecrits += 1
            await kw["on_courrier"](self.destinataire, self.MESSAGE)
        return await super().run_agent(prompt, **kw)


class SignaleUnBlocage(_Executant):
    """Exécutant qui appelle `signaler_blocage` sur sa **première** tâche (#719)."""

    name = "signale-un-blocage"

    RAISON = "Le service de recette répond 503 depuis vingt minutes."

    def __init__(self, raison: str | None = None) -> None:
        super().__init__()
        self.raison = self.RAISON if raison is None else raison
        self.signales = 0

    async def run_agent(self, prompt, **kw):
        if kw.get("on_blocage") is not None and self.signales == 0:
            self.signales += 1
            kw["on_blocage"](self.raison)
        return await super().run_agent(prompt, **kw)


class LesDeuxVerbes(_Executant):
    """Exécutant qui appelle **les deux** verbes sur sa première tâche (#719 + #720).

    Le double du bloc ② : c'est sur ce run-là que le graphe du plan doit être
    exactement celui d'un run qui n'a rien écrit.
    """

    name = "les-deux-verbes"

    def __init__(self) -> None:
        super().__init__()
        self.ecrits = 0
        self.signales = 0

    async def run_agent(self, prompt, **kw):
        if kw.get("on_blocage") is not None and self.signales == 0:
            self.signales += 1
            kw["on_blocage"](SignaleUnBlocage.RAISON)
        if kw.get("on_courrier") is not None and self.ecrits == 0:
            self.ecrits += 1
            await kw["on_courrier"](EcritAuPair.DESTINATAIRE, EcritAuPair.MESSAGE)
        return await super().run_agent(prompt, **kw)


class MailboxEnregistreuse(InMemoryMailbox):
    """Transport mémoire qui garde ce qu'on lui a publié — pour le compter."""

    def __init__(self) -> None:
        super().__init__()
        self.publies: list[AgentMessage] = []

    async def publish(self, message: AgentMessage) -> None:
        self.publies.append(message)
        await super().publish(message)


class MailboxEnPanne(InMemoryMailbox):
    """Transport qui lève à la publication : la notification est hors service."""

    PANNE = "transport injoignable"

    async def publish(self, message: AgentMessage) -> None:
        raise RuntimeError(self.PANNE)


def _tache(id_: str, titre: str, dependances=()) -> dict:
    """Une tâche de plan, routée vers le développeur (« backend »)."""
    return {
        "id": id_,
        "titre": titre,
        "description": f"Travail de la tâche « {titre} ».",
        "competences_requises": ["backend"],
        "format_sortie": "Texte",
        "dependances": list(dependances),
    }


#: Deux tâches en chaîne, deux titres distincts : le plan des blocs ① et ②.
PLAN = json.dumps(
    [
        _tache("collecte", "Collecter les identifiants"),
        _tache("publication", "Publier le paquet", dependances=["collecte"]),
    ],
    ensure_ascii=False,
)

#: Deux tâches **de même titre**, routées vers la même compétence donc vers le
#: même rôle et le même agent : le plan du bloc ③, et le seul échantillon sur
#: lequel la question de #354 se pose vraiment.
PLAN_HOMONYME = json.dumps(
    [
        _tache("lot-1", "Migrer un lot"),
        _tache("lot-2", "Migrer un lot"),
    ],
    ensure_ascii=False,
)

OBJECTIF = "Publier le paquet de recette"


def _moteur(provider, plan=PLAN, *, mailbox=None):
    """Boucle branchée sur le plan constant et, au besoin, sur un transport."""
    orchestrator = Orchestrator(ConstantProvider(plan), model="claude-opus-4-8")
    return OrchestrationEngine(provider, orchestrator, mailbox=mailbox)


def _run(provider, plan=PLAN, *, mailbox=None, run_id="run-721"):
    """Joue un run complet et rend `(rapport, journal)`."""
    journal = RunJournal(run_id=run_id)
    rapport = asyncio.run(_moteur(provider, plan, mailbox=mailbox).run(OBJECTIF, journal=journal))
    return rapport, journal


def _etapes(journal: RunJournal, suffixe: str):
    """Les étapes du journal dont le nom d'étape porte `suffixe`."""
    return [r for r in journal.records if r.etape.endswith(suffixe)]


def _mots_adresses(journal: RunJournal):
    """Les étapes `:message` écrites par **le verbe**, pas par la machinerie.

    Le journal range sous le même suffixe `<tache>:message` deux écritures qui
    n'ont pas le même auteur : le **handoff** (#44), qui annonce en diffusion la
    fin d'une tâche à son aval, et le **mot adressé** qu'un agent écrit (#720).
    Les deux passent par `consigne_message` — c'est même le partage que docs/31
    §3.2 reconduit exprès —, et ce qui les sépare est le **type** du message :
    `handoff` pour la machinerie, `notification` pour l'agent.

    Compter les étapes `:message` sans ce tri dirait donc « deux mots » là où
    l'agent n'en a écrit qu'un, et le dirait seulement dans les runs pourvus
    d'un transport : un test qui l'ignore passe sans messagerie et rougit avec.
    """
    prefixe = f"{MESSAGE_NOTIFICATION} "
    return [r for r in _etapes(journal, SUFFIXE_ETAPE_MESSAGE) if r.sortie.startswith(prefixe)]


def _appelle_outil(outil, **arguments) -> str:
    """Appelle un outil MCP comme le ferait le SDK, et rend le texte servi à l'agent."""
    reponse = asyncio.run(outil.handler(dict(arguments)))
    return reponse["content"][0]["text"]


# --- ① Les deux verbes, par leur promesse la moins évidente ------------------------------


def test_un_mot_a_un_pair_absent_est_consigne_malgre_tout():
    """Le premier critère du ticket, et la promesse que docs/31 §3.2 tient à la place
    de celle qu'il ne tient pas.

    Le transport est un pub/sub **éphémère** — pas de rejeu, abonné requis avant
    la publication — et un agent n'existe que pendant sa tâche : un mot adressé à
    un pair dont la tâche n'a pas démarré, ou est déjà finie, part dans un canal
    que personne n'écoute. Ce qui est livré n'est donc pas une livraison mais une
    **trace adressée**, et c'est le journal qui la porte.

    Trois choses sont vérifiées ensemble, parce qu'aucune ne suffit seule :
    l'absence du pair est **prouvée** (sans quoi le test ne dirait rien de ce
    qu'il annonce), la notification a bien été **tentée** (l'absence est du côté
    du transport, pas du nôtre), et la trace **existe** avec son destinataire
    dedans — un mot consigné sans le nom de celui qu'il visait ne serait plus
    adressé.
    """
    mailbox = MailboxEnregistreuse()
    provider = EcritAuPair()

    rapport, journal = _run(provider, mailbox=mailbox)

    # La prémisse : le destinataire n'est **personne** dans ce run. Ni un rôle, ni
    # un agent du plan — c'est la définition de « pair absent », et sans elle le
    # test parlerait d'un pair présent qui n'écoutait pas.
    presents = {r.role for r in rapport.resultats} | {r.agent for r in rapport.resultats}
    assert EcritAuPair.DESTINATAIRE not in presents

    # La notification a été tentée, et n'a atteint personne : le transport n'a
    # aucun abonné à ce nom. C'est ce qui range l'échec du côté du canal.
    adresses = [m for m in mailbox.publies if m.type == MESSAGE_NOTIFICATION]
    assert len(adresses) == 1
    assert adresses[0].a_agent == EcritAuPair.DESTINATAIRE
    assert not [a for a in mailbox._abonnes if a.agent == EcritAuPair.DESTINATAIRE]

    # Et pourtant la trace est là — c'est tout le ticket.
    (etape,) = _mots_adresses(journal)
    assert etape.etape == f"collecte{SUFFIXE_ETAPE_MESSAGE}"
    assert EcritAuPair.MESSAGE in etape.sortie
    assert EcritAuPair.DESTINATAIRE in etape.sortie

    # Elle est rattachée à la tâche de l'expéditeur et au run, et ne coûte rien :
    # une observation n'entre pas au grand livre (docs/31 §3.2).
    assert etape.run_id == "run-721"
    assert etape.usage.cout_usd in (None, 0.0)

    # Le run, lui, s'est déroulé normalement — écrire à un pair n'échoue rien.
    assert [r.ok for r in rapport.resultats] == [True, True]


def test_sans_transport_du_tout_le_mot_est_consigne_aussi():
    """Le cas **courant**, et il ne doit pas être le cas dégradé.

    Un run se lance sans messagerie la plupart du temps (`mailbox=None`). Si la
    trace dépendait du transport, le verbe ne tiendrait alors *aucune* de ses
    promesses — et il le ferait en silence, ce qui est la pire des façons.
    """
    provider = EcritAuPair()

    _, journal = _run(provider, mailbox=None)

    assert provider.ecrits == 1
    (etape,) = _mots_adresses(journal)
    assert EcritAuPair.MESSAGE in etape.sortie


def test_un_transport_en_panne_ne_retire_pas_la_trace():
    """L'ordre des deux gestes, éprouvé par le seul moyen qui le distingue.

    `HandoffRelais.annonce` publie *puis* consigne, et abandonne tout — trace
    comprise — si la publication échoue. Le courrier fait l'**inverse** : il
    consigne d'abord, précisément pour que « consigné malgré tout » reste vrai
    quand le canal casse. Les deux ordres sont indiscernables tant que le
    transport répond ; c'est un transport **en panne** qui les sépare, et lui
    seul. Sans ce test, l'ordre pourrait être inversé sans qu'aucune suite ne
    rougisse.

    L'exception ne remonte pas non plus : la tâche aboutit. Elle tuerait le run
    à l'instant où l'agent essayait d'être utile.
    """
    provider = EcritAuPair()

    rapport, journal = _run(provider, mailbox=MailboxEnPanne())

    (etape,) = _mots_adresses(journal)
    assert EcritAuPair.MESSAGE in etape.sortie
    assert [r.ok for r in rapport.resultats] == [True, True]


def test_une_declaration_de_blocage_sans_raison_n_est_pas_consignee():
    """Le second critère du ticket, et son **témoin** dans la même expérience.

    Un blocage sans motif n'apprend rien à personne : « il est bloqué » est
    exactement ce que la frise montrait déjà avant ce verbe, et la seule chose
    qu'il apporte est **la raison** — celle qu'aucune règle de détection ne saura
    produire. Rien n'est donc écrit, plutôt qu'une ligne vide qui se lirait comme
    une panne d'affichage.

    Le témoin compte autant que le cas : prouver qu'une raison vide ne consigne
    rien ne dit pas que le verbe consigne quoi que ce soit. Les deux moitiés sont
    ici, sur le même plan et le même double, à un argument près.

    ⚠ C'est l'entrée **exécuteur** qui est éprouvée ici, et c'est voulu : le
    double appelle `on_blocage` en direct, donc `_consigne_blocage_signale` —
    sans passer par l'outil MCP, qui aurait écarté la raison vide avant elle. Ce
    chemin a **deux entrées** et sa garde est écrite deux fois exprès ; les
    éprouver ensemble laisserait la seconde libre de dériver. L'autre entrée est
    `test_l_outil_de_blocage_ne_consigne_rien_sans_raison`.
    """
    # ① La raison vide : rien.
    provider = SignaleUnBlocage(raison="   ")
    _, journal = _run(provider)

    assert provider.signales == 1  # le verbe a bien été appelé…
    assert _etapes(journal, SUFFIXE_ETAPE_BLOCAGE) == []  # … et n'a rien écrit.

    # ② Le témoin : la même expérience avec une vraie raison écrit, elle, une
    # étape — sinon le ✓ ci-dessus dirait seulement que le canal est mort.
    provider = SignaleUnBlocage()
    _, journal = _run(provider)

    (etape,) = _etapes(journal, SUFFIXE_ETAPE_BLOCAGE)
    assert etape.sortie == SignaleUnBlocage.RAISON
    assert etape.statut == STATUT_BLOCAGE_SIGNALE


def test_le_blocage_declare_ne_change_pas_le_statut_de_la_tache():
    """`blocage_signale` n'est pas `bloquee`, et c'est un refus de docs/31 §3.4.

    `loop.py` porte déjà un blocage **hérité** (#43) : une tâche que rien n'a
    jamais exécutée parce qu'une dépendance a échoué. Le nôtre en est le
    contraire — l'agent travaille et parle. Poser `bloquee` ici afficherait
    « cette tâche est morte » au moment précis où quelqu'un demande de l'aide, et
    condamnerait tout son aval par la cascade de #43.

    C'est la moitié invisible du verbe : rien n'échouerait si elle cédait, la
    tâche changerait seulement de colonne — et son aval avec elle.
    """
    provider = SignaleUnBlocage()

    rapport, _ = _run(provider)

    # L'aval dépend de l'amont qui a déclaré : si le statut avait bougé, la
    # cascade l'aurait emporté.
    assert [r.task_id for r in rapport.resultats] == ["collecte", "publication"]
    assert [r.ok for r in rapport.resultats] == [True, True]
    assert all(r.statut != STATUT_BLOCAGE_SIGNALE for r in rapport.resultats)


# --- ① bis. Les deux verbes vus de l'outil servi à l'agent -------------------------------


def test_les_deux_verbes_portent_le_nom_sous_lequel_une_politique_les_designe():
    """Le nom complet est un **contrat**, et il n'avait aucun lecteur (#721).

    docs/31 §7 fait tenir le droit de ces verbes par la couche permissions
    (#110) : « un outil MCP s'appelle `mcp__maestro__<nom>`, donc une politique
    le cite, l'autorise ou le refuse ». Cette phrase n'est vraie que tant que le
    nom ne bouge pas — or `OUTIL_BLOCAGE` et `OUTIL_COURRIER` sont définis pour
    ce seul usage et **référencés nulle part** dans le dépôt, contrairement à
    `OUTIL_ARBITRAGE`. Un renommage de `NOM_OUTIL` les suivrait donc en silence,
    et une politique écrite sur l'ancien nom cesserait de désigner quoi que ce
    soit — c'est-à-dire n'interdirait plus rien, sans que rien ne rougisse.

    Les deux formes sont écrites : la dérivée (qui garde l'accord entre les
    morceaux) et la littérale (qui garde le nom lui-même — c'est elle qu'un
    fichier de politique contient).
    """
    assert courrier.OUTIL_COURRIER == f"mcp__{NOM_SERVEUR}__{courrier.NOM_OUTIL}"
    assert courrier.OUTIL_COURRIER == "mcp__maestro__ecrire_a_un_pair"

    assert blocage.OUTIL_BLOCAGE == f"mcp__{NOM_SERVEUR}__{blocage.NOM_OUTIL}"
    assert blocage.OUTIL_BLOCAGE == "mcp__maestro__signaler_blocage"

    # Les deux verbes vivent sur le **même** serveur, dont le nom est réservé :
    # deux littéraux « maestro » seraient deux serveurs le jour où l'un change.
    assert NOM_SERVEUR == "maestro"


def test_l_outil_de_blocage_ne_consigne_rien_sans_raison():
    """L'autre entrée du même refus : ce que l'agent lit, et ce qu'il ne déclenche pas.

    Le canal n'est **pas appelé** — c'est ce qui compte : refuser après coup
    laisserait la trace vide qu'on veut éviter. Et la réponse n'est pas une
    erreur d'outil : il n'y a rien à réessayer contre, seulement un appel à
    refaire avec le motif.
    """
    recus: list[str] = []
    outil = _outil_blocage(recus.append)

    texte = _appelle_outil(outil, raison="   ")

    assert recus == []
    assert texte == blocage.RAISON_MANQUANTE

    # Le témoin : avec une raison, le canal passe et l'agent lit autre chose.
    texte = _appelle_outil(outil, raison=SignaleUnBlocage.RAISON)
    assert recus == [SignaleUnBlocage.RAISON]
    assert texte == blocage.BLOCAGE_CONSIGNE


def test_l_outil_de_courrier_refuse_ce_qui_n_est_pas_un_pair():
    """Le destinataire doit être **un pair**, et les deux refus ferment la même porte.

    Vide, c'est la **diffusion** côté transport (`mailbox.DIFFUSION`) : le mot
    partirait dans toutes les boîtes, dont celle du relais de handoff, qui écoute
    justement la diffusion. Le nom du relais la ferme par l'autre bout — un mot
    posté là porterait le `tache_id` de l'expéditeur et résoudrait l'attente de
    handoff de sa **propre** tâche.

    Dans les deux cas le canal n'est pas appelé : rien n'est écrit, rien n'est
    adressé.
    """
    recus: list[tuple[str, str]] = []

    async def canal(destinataire: str, message: str) -> None:
        recus.append((destinataire, message))

    outil = _outil_courrier(canal)

    assert _appelle_outil(outil, destinataire="", message="x") == courrier.DESTINATAIRE_MANQUANT
    assert recus == []

    # L'identité de la boucle, et la même en majuscules : la garde ne se
    # contourne pas par une capitale (elle reçoit ce qu'un modèle a écrit).
    for nom in (AGENT_RELAIS, AGENT_RELAIS.upper()):
        texte = _appelle_outil(outil, destinataire=nom, message="x")
        assert texte == courrier.DESTINATAIRE_RESERVE.format(relais=nom)
    assert recus == []

    # Un message vide n'écrit rien non plus — mais le texte nomme le
    # destinataire, pour que l'agent sache quoi rappeler.
    texte = _appelle_outil(outil, destinataire="recette", message="   ")
    assert texte == courrier.MESSAGE_MANQUANT.format(destinataire="recette")
    assert recus == []

    # Le témoin : un vrai pair et un vrai message passent.
    texte = _appelle_outil(outil, destinataire="recette", message=EcritAuPair.MESSAGE)
    assert recus == [("recette", EcritAuPair.MESSAGE)]
    assert texte == courrier.MOT_CONSIGNE.format(destinataire="recette")


def test_un_canal_en_erreur_est_dit_a_l_agent_sans_tuer_la_tache():
    """Les deux verbes avalent l'exception et la **disent** — jamais l'inverse.

    Laisser remonter tuerait la tâche à l'instant précis où l'agent se montre
    coopératif ; l'avaler en silence lui laisserait croire que sa raison ou son
    mot est parti. Les deux verbes servent donc un texte qui nomme la cause, et
    aucun ne lève.
    """

    def signaleur_casse(_: str) -> None:
        raise RuntimeError("journal indisponible")

    async def courrier_casse(_: str, __: str) -> None:
        raise RuntimeError("journal indisponible")

    texte = _appelle_outil(_outil_blocage(signaleur_casse), raison="peu importe")
    assert texte == blocage.CANAL_EN_ERREUR.format(cause="journal indisponible")

    texte = _appelle_outil(
        _outil_courrier(courrier_casse), destinataire="recette", message="peu importe"
    )
    assert texte == courrier.CANAL_EN_ERREUR.format(cause="journal indisponible")


# --- ② Le graphe du plan est inchangé ----------------------------------------------------


def _graphe(journal: RunJournal):
    """Le graphe figé du run : les nœuds du plan snapshotés sur `planification`.

    C'est l'instant où le plan existe et où il est figé (`loop.py`, #490) — donc
    le seul relevé qui puisse servir de référence, et le même objet que celui qui
    part sur le bus vers la Control Tower.
    """
    (planif,) = [r for r in journal.records if r.etape == "planification"]
    return [(n.id, n.titre, tuple(n.dependances)) for n in planif.plan]


def test_le_graphe_du_plan_est_inchange_apres_un_run_qui_ecrit():
    """L'invariant central de docs/31 §5, et le seul qui ne se voit pas à l'œil.

    « Le graphe du plan ne se modifie pas en cours de run. Ni un agent, ni un
    superviseur, ni l'orchestrateur ne lui ajoutent, n'en retirent ou n'en
    réaffectent un nœud à chaud. Ce qui s'accumule pendant un run est **à côté**
    du graphe — des observations, en ajout seul. »

    Il se prouve par **comparaison**, pas par relecture : le même plan est joué
    deux fois, une fois par un agent qui appelle les deux verbes et une fois par
    un agent qui n'en appelle aucun, et les deux graphes doivent coïncider — même
    nœuds, même ordre, mêmes arêtes. Un test qui se contenterait de relire le
    graphe du run bavard ne dirait pas à quoi le comparer.

    Le **témoin de bavardage** est ce qui donne son sens au reste : sans lui,
    deux graphes identiques prouveraient seulement que rien ne s'est passé.
    """
    bavard = LesDeuxVerbes()
    _, journal_bavard = _run(bavard, mailbox=MailboxEnregistreuse(), run_id="run-bavard")

    muet = _Executant()
    _, journal_muet = _run(muet, run_id="run-muet")

    # Le témoin : le premier run a **réellement** appelé les deux verbes, et le
    # second aucun. C'est l'expérience, pas un décor.
    assert (bavard.signales, bavard.ecrits) == (1, 1)
    assert len(_etapes(journal_bavard, SUFFIXE_ETAPE_BLOCAGE)) == 1
    assert len(_mots_adresses(journal_bavard)) == 1
    assert _etapes(journal_muet, SUFFIXE_ETAPE_BLOCAGE) == []
    assert _mots_adresses(journal_muet) == []

    # Et les deux graphes sont le même : rien n'a été ajouté, retiré ni réordonné.
    assert _graphe(journal_bavard) == _graphe(journal_muet)

    # Dit une seconde fois sur le plan de départ, pour que l'égalité ci-dessus ne
    # puisse pas être celle de deux graphes tous deux faux.
    assert _graphe(journal_bavard) == [
        ("collecte", "Collecter les identifiants", ()),
        ("publication", "Publier le paquet", ("collecte",)),
    ]


def test_les_deux_verbes_n_ajoutent_ni_tache_ni_reassignation():
    """Le versant **exécuté** du même invariant : le graphe figé n'est pas tout.

    Un nœud pourrait rester en place pendant que la boucle exécute autre chose —
    une tâche de plus, un propriétaire changé. Les trois refus de docs/31 §3.3 à
    §3.5 portent précisément là-dessus, et aucun ne se voit sur le snapshot.

    On compare donc aussi ce qui a **tourné** : les mêmes tâches, dans le même
    ordre, chez les mêmes agents, avec les mêmes issues.
    """
    bavard = LesDeuxVerbes()
    rapport_bavard, _ = _run(bavard, mailbox=MailboxEnregistreuse(), run_id="run-bavard")

    rapport_muet, _ = _run(_Executant(), run_id="run-muet")

    def execute(rapport):
        return [(r.task_id, r.titre, r.role, r.agent, r.ok) for r in rapport.resultats]

    assert execute(rapport_bavard) == execute(rapport_muet)

    # Et le compte est celui du plan : deux tâches planifiées, deux exécutées.
    assert len(rapport_bavard.resultats) == 2


# --- ③ La synthèse Markdown porte le `task_id` -------------------------------------------


def _sections(synthese: str) -> list[str]:
    """Découpe la synthèse en sections de tâche (`## …`), en-tête exclu."""
    return [bloc for bloc in synthese.split("\n## ")[1:]]


def test_deux_taches_homonymes_produisaient_deux_sections_identiques():
    """L'échantillon fautif, prouvé **avant** de conclure de sa correction (#721).

    C'est la moitié que la note technique du ticket exige, et sans elle le test
    suivant rendrait un ✓ sur une question jamais posée : il faut d'abord établir
    que le plan à deux tâches homonymes est réellement ambigu — que l'ancienne
    formule y rendait bien deux sections rigoureusement identiques — pour que
    leur distinction, ensuite, veuille dire quelque chose.

    L'ancienne formule est rejouée ici telle qu'elle était
    (`## {etat} {titre}` puis `- Agent : {role} ({agent})`), sur les résultats
    réels du run : c'est le seul moyen d'éprouver un défaut qu'on vient de
    corriger.
    """
    rapport, _ = _run(_Executant(), PLAN_HOMONYME, run_id="run-homonyme")

    # La prémisse : deux tâches **distinctes** que tout le reste confond.
    un, deux = rapport.resultats
    assert un.task_id != deux.task_id
    assert (un.titre, un.role, un.agent) == (deux.titre, deux.role, deux.agent)

    def section_d_avant(r):
        etat = "[terminée]" if r.ok else "[échec]"
        competences = ", ".join(r.competences_requises)
        return (
            f"## {etat} {r.titre}\n"
            f"- Agent : {r.role} (`{r.agent}`) — compétences : {competences}"
        )

    # Le défaut, tel qu'il était : rien ne les distingue.
    assert section_d_avant(un) == section_d_avant(deux)


def test_la_synthese_distingue_deux_taches_de_meme_titre_et_meme_role():
    """Le troisième critère du ticket — la dette de docs/31 §6, payée.

    On écarte l'identité d'instance parce que `tache_id` la porte déjà partout où
    l'on mesure ; il faut donc rendre lisible la clé qui la remplace, là où elle
    manquait. Le test est joué **sur un plan qui contient exactement deux tâches
    homonymes**, comme le ticket le demande : c'est le seul endroit où la
    question se pose.
    """
    rapport, _ = _run(_Executant(), PLAN_HOMONYME, run_id="run-homonyme")
    synthese = rapport.synthese()

    un, deux = rapport.resultats
    sections = _sections(synthese)
    assert len(sections) == 2

    # Les deux sections diffèrent — ce qui était faux avant ce lot.
    assert sections[0] != sections[1]

    # Et elles diffèrent **par la clé**, pas par un hasard de livrable : chacune
    # porte son `task_id`, et seulement le sien.
    assert f"- Tâche : `{un.task_id}`" in sections[0]
    assert f"- Tâche : `{deux.task_id}`" in sections[1]
    assert deux.task_id not in sections[0]
    assert un.task_id not in sections[1]


def test_la_cle_imprimee_est_celle_du_journal_et_du_grand_livre():
    """La clé de la synthèse est **celle qu'on avait déjà**, pas une clé de plus.

    C'est tout l'argument du refus : `tache_id` est la clé d'agrégation du grand
    livre, le préfixe de chaque ligne de journal (`<tache_id>:<quoi>`) et
    l'`etape` de la télémétrie. Imprimer autre chose ici — un rang, un compteur —
    rendrait la synthèse lisible sans la rendre **rapprochable**, ce qui est le
    seul usage qu'on en attend.
    """
    rapport, journal = _run(_Executant(), PLAN_HOMONYME, run_id="run-homonyme")
    synthese = rapport.synthese()

    etapes = {r.etape.split(":")[0] for r in journal.records}
    for r in rapport.resultats:
        assert f"- Tâche : `{r.task_id}`" in synthese
        assert r.task_id in etapes


@pytest.mark.parametrize("plan", [PLAN, PLAN_HOMONYME], ids=["titres-distincts", "homonymes"])
def test_toute_section_porte_sa_cle(plan):
    """Aucune section n'y échappe, homonyme ou pas.

    Le défaut se corrige pour toutes les tâches ou pour aucune : une synthèse où
    la clé n'apparaîtrait que sur les doublons obligerait son lecteur à savoir
    d'avance s'il en a.
    """
    rapport, _ = _run(_Executant(), plan, run_id="run-cles")

    sections = _sections(rapport.synthese())
    assert len(sections) == len(rapport.resultats)
    assert all("- Tâche : `" in section for section in sections)
