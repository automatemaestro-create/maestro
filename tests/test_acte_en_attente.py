"""Un acte qui attend une personne est écarté, pas refusé — et tous les textes le disent (#1407).

Le défaut : quand personne ne tranche un acte dans l'attente du hook (240 s,
`maestro.providers.arbitrage.ATTENTE_S`), l'appel est **écarté** pour cette fois, la demande
reste ouverte, et la décision rendue plus tard est retenue pour le rappel du même acte (#583,
#584). Mais l'agent lisait trois choses différentes :

- le motif de l'écart (`motif_attente`) : « si tu y reviens plus tard, la décision …
  s'appliquera » ;
- son cadre d'exécution (`_cadre_outille.md`) : « un appel d'outil refusé est une décision …
  ne le rejoue pas à l'identique » — et l'écart lui arrive comme un appel refusé ;
- son régime (`regime_de_l_agent`) : « sans réponse, la commande est refusée ».

Et l'orchestrateur disait à la personne (`CE_QUI_REVIENT`) que l'acte « est refusé ». Un agent
qui obéit au cadre abandonne pour de bon un acte qui n'était qu'en attente, et rend une tâche
incomplète sans nommer la demande restée ouverte.

Couvre :

① chaque texte qui parle d'une attente sans réponse la dit **écartée**, jamais refusée ;
② les textes que lit l'agent lui disent **la même conduite** : poursuivre, refaire l'appel avant
  de conclure, nommer au compte-rendu la demande encore en attente ;
③ le régime ne le dit qu'à l'agent qu'une personne peut faire attendre ;
④ le refus garde sa règle : il ne se rejoue pas.

Ce qui ne bouge pas — le délai, le décideur humain par défaut hors portée, la décision tardive
retenue — reste gardé là où il l'était : `tests/test_arbitrage_delai.py`,
`tests/test_permissions.py`. Seuls les textes changent.
"""

from __future__ import annotations

import pytest

from maestro.agents.permissions import EntreeArbitrage, PolitiqueOutils
from maestro.agents.playbook_du_code import cadre_outille
from maestro.agents.regime_d_execution import regime_de_l_agent
from maestro.controltower.regime import CE_QUI_REVIENT
from maestro.decideur import Decideur
from maestro.lecture import OUTIL_SHELL
from maestro.portee import PORTEE_PROJET
from maestro.providers.arbitrage import ATTENTE_S, motif_attente, motif_refus

#: Les politiques sous lesquelles une personne peut faire attendre l'agent : le shell au
#: cran `humain` ; le shell en `auto` borné au projet (#1226), qui revient au défaut
#: `humain` dès qu'un appel sort du projet — le cas de S8 et des équipes proposées ;
#: un autre outil au cran `humain`.
POLITIQUES_QUI_FONT_ATTENDRE = {
    "shell humain": PolitiqueOutils(ask=(OUTIL_SHELL,)),
    "shell auto borné au projet": PolitiqueOutils(
        ask=(EntreeArbitrage(OUTIL_SHELL, Decideur.AUTO, PORTEE_PROJET),)
    ),
    "autre outil humain": PolitiqueOutils(ask=(EntreeArbitrage("mcp__slack", Decideur.HUMAIN),)),
}

#: Celles sous lesquelles personne n'est jamais attendu : rien d'arbitré, un shell refusé,
#: un shell `auto` sans portée — il passe partout, en étant tracé.
POLITIQUES_SANS_ATTENTE = {
    "rien d'arbitré": PolitiqueOutils(),
    "shell refusé": PolitiqueOutils(deny=(OUTIL_SHELL,)),
    "shell auto sans portée": PolitiqueOutils(ask=(EntreeArbitrage(OUTIL_SHELL, Decideur.AUTO),)),
}


def _a_plat(texte: str) -> str:
    """Le texte sans mise en forme : les phrases attendues traversent des fins de ligne."""
    return " ".join(texte.replace("**", "").replace("`", "").split())


def _textes_de_l_agent() -> dict[str, str]:
    """Ce que l'agent lit d'un acte en attente : à l'instant de l'écart, dans son cadre,
    dans son régime."""
    return {
        "motif de l'écart": _a_plat(motif_attente(OUTIL_SHELL, ATTENTE_S)),
        "cadre d'exécution": _a_plat(cadre_outille()),
        **{
            f"régime ({nom})": _a_plat(regime_de_l_agent(politique))
            for nom, politique in POLITIQUES_QUI_FONT_ATTENDRE.items()
        },
    }


# --- ① Écarté, jamais refusé ----------------------------------------------------------


def test_le_motif_de_l_ecart_dit_que_rien_n_est_refuse() -> None:
    """L'instant où l'agent l'apprend : le `deny` du hook lui arrive comme un appel
    refusé, c'est donc le motif qui doit dire le contraire, d'entrée."""
    motif = _a_plat(motif_attente(OUTIL_SHELL, ATTENTE_S))

    assert motif.startswith(f"appel de l'outil {OUTIL_SHELL!r} écarté, pas refusé")
    assert "La demande reste en attente" in motif
    assert "refusé à l'arbitrage" not in motif


@pytest.mark.parametrize("nom", POLITIQUES_QUI_FONT_ATTENDRE)
def test_le_regime_ne_dit_plus_refusee_une_commande_sans_reponse(nom: str) -> None:
    bloc = _a_plat(regime_de_l_agent(POLITIQUES_QUI_FONT_ATTENDRE[nom]))

    assert "la commande est refusée" not in bloc
    assert "écarté, pas refusé" in bloc


def test_l_orchestrateur_ne_dit_plus_a_la_personne_que_l_acte_est_refuse() -> None:
    """Ce que le fil annonce à la personne : un acte qu'elle n'a pas encore tranché n'est
    pas perdu — sa décision, même tardive, s'appliquera quand l'agent y reviendra."""
    revient = _a_plat(CE_QUI_REVIENT)

    assert "l'acte est refusé" not in revient
    assert "écarté, pas refusé" in revient
    assert "la demande reste ouverte" in revient
    assert "avant de conclure" in revient
    assert "compte-rendu" in revient


# --- ② Une seule conduite, dans tous les textes de l'agent ---------------------------


@pytest.mark.parametrize("texte", _textes_de_l_agent())
def test_chaque_texte_de_l_agent_dit_la_meme_conduite(texte: str) -> None:
    """Le cœur du défaut : trois textes, trois consignes. Chacun dit désormais l'état
    (écarté, pas refusé), le geste (refaire l'appel avant de conclure : la décision rendue
    entre-temps s'appliquera) et ce qui reste à dire (la demande encore en attente, au
    compte-rendu)."""
    lu = _textes_de_l_agent()[texte]

    assert "écarté, pas refusé" in lu
    assert "refais-le à l'identique avant de conclure" in lu
    assert "la décision rendue entre-temps s'appliquera" in lu
    assert "nomme cette demande en attente dans ton compte-rendu" in lu


def test_le_motif_ne_fait_pas_attendre_l_agent_en_boucle() -> None:
    """Rappeler l'acte ne rouvre pas de demande — il rejoint celle qui est ouverte
    (`MemoireArbitrage`) — et encore sans décision, l'agent conclut : un seul retour,
    pas un pied de grue (#239)."""
    motif = _a_plat(motif_attente(OUTIL_SHELL, ATTENTE_S))

    assert "sans en ouvrir une autre" in motif
    assert "Encore sans décision, conclus sans lui" in motif


# --- ③ Seulement là où une personne peut faire attendre ------------------------------


@pytest.mark.parametrize("nom", POLITIQUES_SANS_ATTENTE)
def test_le_regime_ne_parle_d_attente_qu_a_l_agent_qu_on_peut_faire_attendre(nom: str) -> None:
    bloc = _a_plat(regime_de_l_agent(POLITIQUES_SANS_ATTENTE[nom]))

    assert "écarté" not in bloc


# --- ④ Le refus garde sa règle --------------------------------------------------------


def test_le_refus_garde_sa_regle_il_ne_se_rejoue_pas() -> None:
    """Distinguer l'écart du refus ne doit pas attendrir le refus : le cadre le dit
    toujours une décision, et un refus de la personne dit toujours de poursuivre sans."""
    cadre = _a_plat(cadre_outille())
    refus = _a_plat(motif_refus(OUTIL_SHELL, "refusée par le validateur humain"))

    assert "Un appel d'outil refusé est une décision, pas un incident." in cadre
    assert "Ne le rejoue pas à l'identique" in cadre
    assert "Poursuis la tâche sans cet outil." in refus
    assert "écarté" not in refus
