"""Ce qu'une tâche a consommé se lit en contexte, cache et coût — pas en somme de tours (#1398).

Le défaut vu sur p5 le 2026-10-01 : la tâche `socle-nextjs` affichait
**1 845 438 tokens** d'entrée pour un contexte qui n'a jamais dépassé ≈ 66 k.
Le chiffre était la **somme des contextes de ses 65 tours**, relectures de cache
comprises — ce que le bloc `usage` du résultat du SDK additionne —, et son coût,
1,87 $, celui d'un travail surtout servi par le cache. Trois effets, un par
section de ce module :

① l'usage ne distinguait rien : ni le **contexte atteint** (le plus grand prompt
  d'un appel d'API), ni le **cache relu**, ni le cache écrit — `StepUsage` n'avait
  aucun champ pour eux ;
② les ≈ 20 k tokens de la **sonde de démarrage** (#1304) tombaient dans la
  première tâche qui l'armait : la sonde courait dans une tâche asyncio créée
  depuis la session de l'agent, donc sous **son** collecteur ;
③ le **plafond en tokens** d'un run portait sur ce même total : il coupait une
  tâche sur ce que le cache relit, pas sur ce qu'elle consomme.

Ni réseau ni modèle : le flux du SDK est joué par des doubles, comme dans
`tests/test_usage_en_vol.py`, et la sonde par le double du CLI partagé
(`tests/double_cli_claude.py`).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from claude_agent_sdk import AssistantMessage, TextBlock
from double_cli_claude import DoubleCli

from maestro.agents.permissions import PermissionStore, PolitiqueOutils
from maestro.controltower import ControlTowerState
from maestro.controltower.bridge import evenements_depuis_step
from maestro.engine import OrchestrationEngine
from maestro.orchestrator import Orchestrator
from maestro.providers import ClaudeProvider, Credentials, ModelProvider
from maestro.providers import claude as claude_mod
from maestro.providers.openai_compat import _usage_depuis
from maestro.telemetry import (
    ETAPE_SONDE,
    PlafondDepense,
    PlafondDepenseDepasse,
    RunCost,
    RunJournal,
    StepUsage,
    collect_usage,
    depenses_du_run,
    resume_controle_depense,
)


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Un `Path.home()` sous `tmp_path` : sous Windows, `AppData` est une racine refusée."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


# --------------------------------------------- ① Le contexte, le cache relu, le cache écrit


class _Texte:
    def __init__(self, text: str) -> None:
        self.text = text


class _Assistant:
    """Le `AssistantMessage` du SDK : ses blocs, et le `usage` de son appel d'API."""

    def __init__(self, content, *, usage=None, message_id=None) -> None:
        self.content = content
        self.usage = usage
        self.message_id = message_id


class _Resultat:
    def __init__(self, *, usage=None, total_cost_usd=None, duration_api_ms=0, num_turns=1):
        self.usage = usage
        self.total_cost_usd = total_cost_usd
        self.duration_api_ms = duration_api_ms
        self.num_turns = num_turns


@pytest.fixture()
def sdk(monkeypatch):
    """Substitue les types du SDK par des doubles — même harnais que test_usage_en_vol."""
    monkeypatch.setattr(claude_mod, "AssistantMessage", _Assistant)
    monkeypatch.setattr(claude_mod, "TextBlock", _Texte)
    monkeypatch.setattr(claude_mod, "ResultMessage", _Resultat)


def _tour(*, frais: int, ecrit: int, relu: int, sortie: int) -> dict[str, int]:
    """Le bloc `usage` **public** d'un appel d'API : prompt frais, cache écrit, cache relu."""
    return {
        "input_tokens": frais,
        "cache_creation_input_tokens": ecrit,
        "cache_read_input_tokens": relu,
        "output_tokens": sortie,
    }


#: Trois tours d'une tâche longue servie par le cache — la forme de `socle-nextjs`
#: sur p5 : chaque tour relit le contexte du précédent et en écrit un peu plus.
_TOURS = (
    _tour(frais=3, ecrit=60_000, relu=0, sortie=800),
    _tour(frais=2, ecrit=4_000, relu=60_000, sortie=1_200),
    _tour(frais=5, ecrit=2_000, relu=64_000, sortie=900),
)


def _somme(cle: str) -> int:
    return sum(tour[cle] for tour in _TOURS)


def _resultat_cumule() -> dict[str, int]:
    """Ce que le `ResultMessage` du SDK rend : la **somme** des blocs de ses tours."""
    return {cle: _somme(cle) for cle in _TOURS[0]}


def _joue_session(absorbe) -> None:
    for rang, usage in enumerate(_TOURS, start=1):
        absorbe(_Assistant([_Texte("…")], usage=usage, message_id=f"m{rang}"))
    absorbe(_Resultat(usage=_resultat_cumule(), total_cost_usd=1.87, num_turns=len(_TOURS)))


def _absorbeur():
    compteur = claude_mod._CompteurTours()
    return lambda message: claude_mod._absorbe(message, [], [], None, compteur=compteur)


def test_une_tache_servie_par_le_cache_dit_son_contexte_atteint_et_son_cache_relu(sdk):
    """Le défaut de p5, à l'échelle : le contexte atteint n'est pas la somme des tours."""
    with collect_usage() as recolte:
        _joue_session(_absorbeur())

    usage = recolte.total
    # Le plus grand prompt d'un appel — 2 + 4 000 + 60 000 ne l'est pas, 5 + 2 000 +
    # 64 000 l'est —, et non les 190 010 tokens que les trois tours additionnent.
    assert usage.contexte_max == 66_005
    assert usage.tokens_cache_lus == 124_000
    assert usage.tokens_cache_ecrits == 66_000
    # Ce qui ne bouge pas : l'entrée reste la somme de ce que les tours ont envoyé,
    # cache compris (#8), et le coût celui que le fournisseur a rapporté.
    assert usage.tokens_entree == 190_010
    assert usage.tokens_sortie == 2_900
    assert usage.cout_usd == pytest.approx(1.87)
    # Le cache relu est majoritaire : c'est ce qui rend le chiffre total trompeur.
    assert usage.tokens_cache_lus > usage.tokens_total / 2
    assert usage.tokens_hors_cache_lu == usage.tokens_total - 124_000


def test_le_contexte_se_lit_des_le_premier_tour_pendant_que_la_tache_tourne(sdk):
    vus: list[StepUsage] = []
    absorbe = _absorbeur()
    with collect_usage(on_mesure=vus.append):
        absorbe(_Assistant([_Texte("…")], usage=_TOURS[0], message_id="m1"))
        # Le CLI répète l'usage d'un même appel sur chacun de ses blocs.
        absorbe(_Assistant([_Texte("…")], usage=_TOURS[0], message_id="m1"))
        absorbe(_Assistant([_Texte("…")], usage=_TOURS[1], message_id="m2"))

    assert [v.contexte_max for v in vus] == [60_003, 64_002]
    assert [v.tokens_cache_lus for v in vus] == [0, 60_000]
    assert [v.tokens_cache_ecrits for v in vus] == [60_000, 64_000]


def test_le_contexte_se_fusionne_par_maximum_et_le_cache_par_somme():
    a = StepUsage(tokens_entree=100, tokens_cache_lus=60, tokens_cache_ecrits=30, contexte_max=70)
    b = StepUsage(tokens_entree=50, tokens_cache_lus=10, tokens_cache_ecrits=5, contexte_max=40)

    fondu = a.fusion(b)

    assert fondu.contexte_max == 70
    assert (fondu.tokens_cache_lus, fondu.tokens_cache_ecrits) == (70, 35)
    # Inconnu n'est pas zéro : une mesure sans contexte garde celui de l'autre.
    assert StepUsage().fusion(b).contexte_max == 40
    assert StepUsage().fusion(StepUsage()).contexte_max is None


def test_l_usage_voyage_avec_son_contexte_et_son_cache():
    usage = StepUsage(
        appels=1,
        tokens_entree=190_010,
        tokens_sortie=2_900,
        cout_usd=1.87,
        tokens_cache_lus=124_000,
        tokens_cache_ecrits=66_000,
        contexte_max=66_005,
    )

    brut = usage.to_dict()

    assert brut["contexte_max"] == 66_005
    assert brut["tokens_cache_lus"] == 124_000
    assert brut["tokens_cache_ecrits"] == 66_000
    # Dérivée comme `tokens_total` : calculée ici, ignorée au retour.
    assert brut["tokens_hors_cache_lu"] == 192_910 - 124_000
    assert StepUsage.from_dict(json.loads(json.dumps(brut))) == usage


def test_une_ligne_d_avant_ce_ticket_se_relit_sans_contexte_connu():
    ancienne = {"appels": 1, "tokens_entree": 1_845_438, "tokens_sortie": 30_000, "cout_usd": 1.87}

    usage = StepUsage.from_dict(ancienne)

    # Personne n'a mesuré : le contexte est inconnu, pas nul — et rien ne se
    # fabrique à partir d'une somme de tours.
    assert usage.contexte_max is None
    assert (usage.tokens_cache_lus, usage.tokens_cache_ecrits) == (0, 0)


def test_le_resume_d_une_etape_dit_le_contexte_et_le_cache_relu():
    usage = StepUsage(
        appels=1,
        tokens_entree=190_010,
        tokens_sortie=2_900,
        cout_usd=1.87,
        tokens_cache_lus=124_000,
        contexte_max=66_005,
    )

    resume = usage.resume_court()

    assert "contexte max 66005" in resume
    assert "124000 relus en cache" in resume


def test_le_bilan_ne_dit_plus_le_total_seul():
    """Le bilan d'un run se lit à la personne et au modèle qui juge : le total seul trompe."""
    from maestro.controltower.bilan import _usage_en_mots, _usage_en_phrase

    usage = StepUsage(
        appels=1,
        tokens_entree=1_845_438,
        tokens_sortie=30_000,
        cout_usd=1.87,
        tours=65,
        tokens_cache_lus=1_700_000,
        contexte_max=66_000,
    )

    # Le format de l'écran : espaces fines insécables entre milliers, insécable avant l'unité.
    mots = _usage_en_mots(usage).replace(" ", " ").replace("\xa0", " ")
    phrase = _usage_en_phrase(usage).replace(" ", " ").replace("\xa0", " ")
    assert mots.startswith(
        "1 875 438 tokens, dont 1 700 000 relus en cache, contexte max 66 000, coût 1,87 $US"
    )
    assert phrase.startswith(
        "1 875 438 tokens (dont 1 700 000 relus en cache, contexte max 66 000) pour 1,87 $US"
    )
    # Sans cache rapporté ni contexte mesuré, la phrase est celle d'avant.
    assert _usage_en_mots(StepUsage(tokens_entree=10, tours=1, cout_usd=0.0)).startswith(
        "10 tokens, coût "
    )


def test_le_releve_en_vol_dit_le_contexte_avant_le_total():
    from maestro.engine.executor import _resume_releve

    usage = StepUsage(tokens_entree=190_010, tours=3, tokens_cache_lus=124_000, contexte_max=66_005)

    assert _resume_releve(usage).startswith(
        "contexte 66005 tokens · 190010 tokens traités dont 124000 relus en cache · 3 tour(s)"
    )
    # Un fournisseur qui ne dit ni l'un ni l'autre se lit comme avant.
    assert _resume_releve(StepUsage(tokens_entree=10, tours=1)).startswith("10 tokens · 1 tour(s)")


def test_un_endpoint_openai_dit_son_cache_relu_et_son_contexte():
    # `prompt_tokens_details.cached_tokens` est le champ public du dialecte : une
    # part de `prompt_tokens`, comme `cache_read_input_tokens` en est une chez Claude.
    payload = {
        "usage": {
            "prompt_tokens": 1_200,
            "completion_tokens": 40,
            "prompt_tokens_details": {"cached_tokens": 1_024},
        }
    }

    usage = _usage_depuis(payload, 12)

    assert usage.tokens_entree == 1_200
    assert usage.tokens_cache_lus == 1_024
    # Un appel est un tour : son prompt est son contexte.
    assert usage.contexte_max == 1_200
    # Sans le détail (Ollama, vLLM…), rien de relu n'est inventé.
    assert _usage_depuis({"usage": {"prompt_tokens": 9}}, 1).tokens_cache_lus == 0


# ------------------------------------------------- ② La sonde se compte au run, pas à une tâche


#: Ce que coûte la sonde sur le vrai CLI : ≈ 20 k tokens, surtout du prompt système.
_USAGE_SONDE = {
    "input_tokens": 3,
    "cache_creation_input_tokens": 19_800,
    "cache_read_input_tokens": 0,
    "output_tokens": 60,
}


async def _livre(*, prompt: object, options: object):
    """La session d'un agent qui livre, sans rien consommer de mesuré."""
    yield AssistantMessage(content=[TextBlock(text="Livré.")], model="claude-double")


def _lance(provider: ClaudeProvider, workspace: Path) -> str:
    return asyncio.run(
        provider.run_agent(
            "Fais",
            model="claude-double",
            workspace=workspace,
            tools=("Read",),
            politique=PolitiqueOutils(deny=("Bash",)),
        )
    )


def test_la_sonde_ne_tombe_pas_dans_la_tache_qui_l_arme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = DoubleCli(monkeypatch, session=_livre, usage_sonde=_USAGE_SONDE, cout_sonde=0.075)
    du_run: list[tuple[str, str, StepUsage]] = []

    with depenses_du_run(lambda etape, nom, usage: du_run.append((etape, nom, usage))):
        with collect_usage() as tache:
            assert _lance(ClaudeProvider(Credentials()), tmp_path) == "Livré."

    assert len(cli.sondes) == 1
    # La tâche n'a rien consommé de mesuré : les 19 863 tokens de la sonde ne sont pas à elle.
    assert tache.total.tokens_total == 0
    ((etape, nom, usage),) = du_run
    assert etape == ETAPE_SONDE and nom
    assert usage.tokens_total == 19_863
    assert usage.cout_usd == pytest.approx(0.075)


def test_sans_consignataire_la_sonde_reste_comptee_plutot_que_perdue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hors d'un run (un appelant qui ne consigne rien), ce que la sonde a coûté
    # retombe là où l'on mesure : un chiffre mal rangé vaut mieux qu'un chiffre perdu.
    DoubleCli(monkeypatch, session=_livre, usage_sonde=_USAGE_SONDE, cout_sonde=0.075)

    with collect_usage() as recolte:
        _lance(ClaudeProvider(Credentials()), tmp_path)

    assert recolte.total.tokens_total == 19_863


class _Planificateur(ModelProvider):
    """Planificateur factice : une tâche unique, routée vers le développeur."""

    name = "planificateur-1398"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None, effort=None):
        return json.dumps(
            [
                {
                    "id": "socle-nextjs",
                    "titre": "Socle Next.js",
                    "description": "Monter le socle.",
                    "competences_requises": ["backend"],
                    "format_sortie": "Texte",
                    "dependances": [],
                }
            ]
        )


def test_dans_un_run_la_sonde_a_sa_ligne_et_la_premiere_tache_la_perd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le critère vu du moteur : une ligne `sonde` au run, la tâche sans ses ≈ 20 k."""
    DoubleCli(monkeypatch, session=_livre, usage_sonde=_USAGE_SONDE, cout_sonde=0.075)
    permissions = PermissionStore(tmp_path / "permissions")
    permissions.racine.mkdir(parents=True)
    (permissions.racine / "developpeur.json").write_text(
        json.dumps({"deny": ["WebFetch"]}), encoding="utf-8"
    )
    moteur = OrchestrationEngine(
        ClaudeProvider(Credentials()),
        Orchestrator(_Planificateur(), model="claude-double"),
        permissions=permissions,
    )
    journal = RunJournal()

    asyncio.run(moteur.run("Objectif", journal=journal))

    (ligne,) = [r for r in journal.records if r.etape == ETAPE_SONDE]
    assert ligne.usage.tokens_total == 19_863
    cout = RunCost.depuis_journal(journal)
    assert cout.sonde.tokens_total == 19_863
    (tache,) = cout.taches
    assert tache.tache_id == "socle-nextjs"
    assert tache.usage.tokens_total == 0
    # Le run, lui, la compte toujours : rien ne se perd en changeant de ligne.
    assert cout.total.tokens_total >= 19_863
    assert cout.to_dict()["sonde"]["tokens_total"] == 19_863


def test_la_ligne_de_la_sonde_est_une_etape_du_run_cote_control_tower():
    journal = RunJournal(run_id="run-1398")
    journal.consigne(
        etape=ETAPE_SONDE,
        nom="Sonde du point de contrôle",
        agent="orchestrateur",
        role="Orchestrateur",
        statut="terminee",
        entree="",
        sortie="",
        usage=StepUsage(appels=1, tokens_entree=19_803, tokens_sortie=60, cout_usd=0.075),
    )
    (record,) = journal.records

    evenements = evenements_depuis_step(record.to_dict())

    # Une étape du run : aucune carte de tâche fantôme nommée « sonde » au Kanban.
    assert all(e.tache_id == "" for e in evenements)
    etat = ControlTowerState()
    for evenement in evenements:
        etat.appliquer(evenement)
    assert etat.tache(ETAPE_SONDE) is None
    run = etat.execution("run-1398")
    assert run is not None
    cout = run.cout
    assert cout.sonde.tokens_total == 19_863
    assert cout.planification.tokens_total == 0
    assert cout.taches == ()


# ---------------------------------------------------- ③ Le plafond en tokens, hors cache relu


def _journal_d_une_tache_servie_par_le_cache() -> RunJournal:
    journal = RunJournal(run_id="run-plafond")
    journal.consigne(
        etape="socle-nextjs",
        nom="Socle Next.js",
        agent="developpeur",
        role="Développeur",
        statut="terminee",
        entree="",
        sortie="",
        # p5 : 1 845 438 tokens d'entrée, dont l'essentiel relu en cache.
        usage=StepUsage(
            appels=1,
            tokens_entree=1_845_438,
            tokens_sortie=30_000,
            cout_usd=1.87,
            tokens_cache_lus=1_700_000,
            tokens_cache_ecrits=140_000,
            contexte_max=66_000,
        ),
    )
    return journal


def test_le_plafond_en_tokens_ne_compte_pas_le_cache_relu():
    journal = _journal_d_une_tache_servie_par_le_cache()
    # 1 875 438 tokens au total, dont 175 438 hors cache relu.
    plafond = PlafondDepense(journal, plafond_tokens=500_000)

    plafond.verifie(StepUsage())  # ne lève pas : la tâche n'a pas consommé 500 k

    with pytest.raises(PlafondDepenseDepasse, match="hors cache relu"):
        PlafondDepense(journal, plafond_tokens=150_000).verifie(StepUsage())


def test_le_controle_de_depense_dit_ce_que_le_plafond_en_tokens_compte():
    usage = RunCost.depuis_journal(_journal_d_une_tache_servie_par_le_cache()).total

    resume = resume_controle_depense(None, 500_000, usage)

    assert "175438/500000" in resume
    assert "hors cache relu" in resume
