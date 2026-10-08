"""Le régime d'exécution d'un agent, écrit par Maestro depuis sa politique (#1405).

Le défaut : sur p5, le playbook de `dev-nextjs` — rédigé par un modèle (#257) — disait
que « joindre un service extérieur » attendait l'accord d'une personne. L'agent a fait
attendre quelqu'un 4 min 19 s avant un `npm install` que sa politique laissait passer.
Aucun test ne portait sur le texte que l'agent reçoit : c'est par là que le défaut est
passé. Couvre :

① le prompt système d'une tâche **se termine** par le régime composé depuis la
  politique qu'elle applique, recomposé à chaque exécution, et ce bloc prime sur ce
  que le playbook dit d'autre — le playbook de p5 tel qu'il était compris ;
② le texte et la règle disent la même chose : ce que `maestro.portee` laisse dans le
  projet (dont `npm install`), le bloc le dit passer sans arbitrage, et il ne nomme
  comme revenant à une personne que les deux familles qui en sortent ;
③ chaque cran a sa phrase pour ses deux lecteurs — l'agent, l'orchestrateur —, sous
  les mêmes clés ;
④ les playbooks déjà écrits se réécrivent sans leur régime : la section des skills
  ne passe pas par le modèle, l'ancien texte est gardé, un échec laisse la fiche
  telle quelle.
"""

from __future__ import annotations

import asyncio
import io
from pathlib import Path

import pytest

from maestro.agents.fiche_outillee import playbook_outille, runtime_outille
from maestro.agents.permissions import EntreeArbitrage, PolitiqueOutils
from maestro.agents.regime_d_execution import (
    INTRO_REGIME,
    PORTEE_DE_L_AGENT,
    REGIME_DE_L_AGENT,
    REGIME_EXECUTION,
    REGIME_PORTEE,
    SHELL_LIBRE,
    SHELL_REFUSE,
    TITRE_REGIME,
    avec_regime,
    regime_de_l_agent,
)
from maestro.agents.store import AgentDefinition, AgentStore
from maestro.controltower.auto_amelioration import MARQUEUR_PLAYBOOK
from maestro.controltower.generation_agent import (
    _CADRE_SANS_REGIME,
    GenerateurDefinitionAgent,
    GenerationIndisponible,
)
from maestro.controltower.regeneration_playbooks import (
    CODE_ECHEC,
    CODE_FAIT,
    main,
    projets_avec_equipe,
)
from maestro.decideur import Decideur
from maestro.equipe.creation import (
    TITRE_SKILLS,
    RoleValide,
    SkillRetenu,
    playbook_branche,
    scinder_section_skills,
)
from maestro.lecture import OUTIL_SHELL
from maestro.portee import PORTEE_PROJET, PORTEES, PorteeProjet
from maestro.providers.base import ModelProvider

#: Le régime que p5 avait écrit dans le playbook de `dev-nextjs`, au mot près pour le
#: passage fautif : la règle inventée est la fin du premier cas.
REGIME_INVENTE_DE_P5 = (
    "## Tes commandes : libres dans le dossier, tracées\n\n"
    "Dans le dossier du projet, tes commandes shell **passent sans attendre "
    "personne**.\n\n"
    "Deux cas seulement attendent l'accord d'une personne :\n\n"
    "1. **Ce qui sort du dossier du projet** : écrire, lire par le shell ou modifier "
    "quelque chose en dehors de lui, ou joindre un service extérieur.\n"
    "2. **Ce qui efface ce qui s'y trouvait avant toi**."
)

#: Le corps du playbook de p5 : son métier, et ce régime au milieu.
METIER_DE_P5 = (
    "# Développeur Next.js du livre de recettes\n\n"
    "Tu es le développeur du **livre de recettes**, une application Next.js.\n\n"
    f"{REGIME_INVENTE_DE_P5}\n\n"
    "## Comment tu procèdes\n\n"
    "1. **Prépare l'environnement si besoin** : appelle **mettre-en-route**."
)

#: Les skills de p5, tels que la création les branche.
SKILLS_DE_P5 = (
    SkillRetenu(
        "mettre-en-route", ".agents/skills/mettre-en-route/SKILL.md", ("npm install",)
    ),
    SkillRetenu(
        "lancer-les-tests", ".agents/skills/lancer-les-tests/SKILL.md", ("npx vitest run",)
    ),
)

#: La politique de p5 (`core/permissions/_projets/prj-be97b8aa/dev-nextjs.json`) : celle
#: qu'une équipe proposée reçoit depuis #1226.
POLITIQUE_DE_P5 = PolitiqueOutils(
    ask=(EntreeArbitrage(OUTIL_SHELL, Decideur.AUTO, PORTEE_PROJET),)
)


def _fiche_de_p5() -> AgentDefinition:
    """La fiche de `dev-nextjs` telle que la création l'a écrite : métier puis skills."""
    role = RoleValide(
        nom="dev-nextjs",
        role="Développeur Next.js du livre de recettes",
        competences=("nextjs", "react", "typescript"),
        playbook=METIER_DE_P5,
        skills=SKILLS_DE_P5,
    )
    return AgentDefinition(
        nom=role.nom,
        role=role.role,
        competences=role.competences,
        playbook=playbook_branche(role),
    )


def _a_plat(texte: str) -> str:
    """Le texte remis à plat — les phrases attendues traversent des fins de ligne."""
    return " ".join(texte.split())


class _Executant(ModelProvider):
    """Fournisseur outillé factice : retient le prompt système de chaque exécution."""

    name = "executant"

    def __init__(self) -> None:
        self.systemes: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("une tâche outillée passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **_):
        self.systemes.append(system_prompt or "")
        return "Fait."


class _Redacteur(ModelProvider):
    """Fournisseur texte factice : rend `reponse`, ou lève `panne`, et retient l'appel."""

    name = "redacteur"

    def __init__(self, reponse: str = "", panne: Exception | None = None) -> None:
        self._reponse = reponse
        self._panne = panne
        self.appels: list[tuple[str, str | None]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):
        self.appels.append((prompt, system_prompt))
        if self._panne is not None:
            raise self._panne
        return self._reponse


# --- ① Le prompt d'une tâche se termine par le régime de sa politique ----------------


def test_le_prompt_d_une_tache_se_termine_par_le_regime_de_sa_politique() -> None:
    """Le cas de p5, rejoué sur le chemin réel d'une fiche (`runtime_outille`) : son
    playbook dit encore la règle inventée, et c'est le bloc de Maestro — composé depuis
    `Bash: auto`, portée `projet` — qui ferme le prompt et dit qu'il prime."""
    executant = _Executant()
    agent = _fiche_de_p5().to_agent()

    asyncio.run(
        runtime_outille(executant, agent).execute(
            "Installe les dépendances", politique=POLITIQUE_DE_P5
        )
    )

    (systeme,) = executant.systemes
    assert systeme.startswith(playbook_outille(agent).rstrip())
    assert systeme.rstrip().endswith(regime_de_l_agent(POLITIQUE_DE_P5))
    # Il vient **après** ce que le playbook disait, et dit qu'il l'emporte.
    assert systeme.index(TITRE_REGIME) > systeme.index("joindre un service extérieur")
    assert INTRO_REGIME in systeme


def test_sans_politique_le_prompt_reste_celui_d_avant_au_caractere_pres() -> None:
    """Aucune politique, aucun régime à écrire : rien n'est ajouté."""
    executant = _Executant()
    agent = _fiche_de_p5().to_agent()

    asyncio.run(runtime_outille(executant, agent).execute("Tâche"))

    assert executant.systemes == [playbook_outille(agent)]
    assert avec_regime("Consignes.", None) == "Consignes."


def test_le_regime_est_recompose_a_chaque_execution_depuis_sa_politique() -> None:
    """Une politique se règle depuis l'écran d'un agent et vaut à la tâche suivante
    (#110) : le texte suit, sans reconstruire le runtime — il ne peut pas annoncer le
    régime d'hier."""
    executant = _Executant()
    runtime = runtime_outille(executant, _fiche_de_p5().to_agent())

    asyncio.run(runtime.execute("Tâche", politique=POLITIQUE_DE_P5))
    asyncio.run(runtime.execute("Tâche", politique=PolitiqueOutils(ask=(OUTIL_SHELL,))))

    auto, humain = executant.systemes
    assert REGIME_DE_L_AGENT[Decideur.AUTO] in auto
    assert REGIME_DE_L_AGENT[Decideur.HUMAIN] not in auto
    assert REGIME_DE_L_AGENT[Decideur.HUMAIN] in humain
    assert PORTEE_DE_L_AGENT[PORTEE_PROJET] not in humain


# --- ② Le texte et la règle disent la même chose ---------------------------------


@pytest.mark.parametrize("commande", ["npm install", "npm ci", "npm run build", "pytest -q"])
def test_ce_que_la_portee_garde_dans_le_projet_le_bloc_le_dit_passer_sans_arbitrage(
    tmp_path: Path, commande: str
) -> None:
    """La règle (`maestro.portee`, que `tests/test_portee.py` fige par
    `test_remplir_le_dossier_du_projet_n_est_pas_en_sortir`) garde ces commandes dans
    le projet ; le bloc que l'agent lit les range dans ce qui passe — installer les
    dépendances et joindre le réseau compris — et lui dit de n'en faire arbitrer
    aucune. C'est la phrase qui manquait à p5."""
    assert PorteeProjet(tmp_path).commande_hors_portee(commande) == ""

    bloc = _a_plat(regime_de_l_agent(POLITIQUE_DE_P5))
    assert "Tes commandes shell passent sans attendre personne" in bloc
    assert "installer les dépendances du projet dans le projet" in bloc
    assert "joindre le réseau pour cela" in bloc
    assert "Ne demande d'arbitrage pour aucune de ces commandes" in bloc


def test_le_bloc_ne_range_plus_le_menage_dans_le_travail_ordinaire() -> None:
    """#1401 : la portée énumérait « nettoyer ce que tes exécutions ont produit »
    parmi ce qui passe sans arbitrage — une invitation à effacer `node_modules/` et
    `.next/` en fin de tâche, là où le cadre dit de les laisser, ignorés. Le geste
    reste permis (la règle de `maestro.portee` n'a pas bougé), mais le bloc nomme à sa
    place ce qui finit une tâche proprement : arrêter ce que l'agent a lancé."""
    bloc = _a_plat(regime_de_l_agent(POLITIQUE_DE_P5))

    assert "nettoy" not in bloc.lower()
    assert "arrêter ce que tu as lancé" in bloc


@pytest.mark.parametrize(
    ("commande", "famille"),
    [
        ("npm install -g typescript", "une installation globale"),
        ("sudo apt-get install jq", "l'élévation de privilèges"),
        ("ssh serveur uptime", "une machine distante"),
    ],
)
def test_ce_que_la_portee_fait_remonter_le_bloc_le_nomme_et_rien_d_autre(
    tmp_path: Path, commande: str, famille: str
) -> None:
    """L'autre sens : ce que la règle fait sortir du projet, le bloc le nomme parmi ce
    qui attend une personne — et il annonce **deux** familles, « elles seules », celles
    de `maestro.portee`. Aucune n'est un service extérieur."""
    assert PorteeProjet(tmp_path).commande_hors_portee(commande) != ""

    bloc = _a_plat(regime_de_l_agent(POLITIQUE_DE_P5))
    assert "Deux familles d'actes, et elles seules, attendent l'accord d'une personne" in bloc
    assert famille in bloc
    assert "service extérieur" not in bloc


def test_sous_le_cran_humain_le_bloc_porte_la_regle_du_premier_geste() -> None:
    """La règle de #1102 vit désormais là où le cran est connu : un agent dont chaque
    commande attend une personne apprend de Maestro à ne pas en faire le préalable."""
    bloc = _a_plat(regime_de_l_agent(PolitiqueOutils(ask=(OUTIL_SHELL,))))

    assert "ne fais donc pas d'une commande le préalable de tout le reste" in bloc
    assert "sauf celles qui ne font que lire" in bloc
    # La portée ne borne que `auto` : sous `humain`, une personne tranche partout.
    assert _a_plat(PORTEE_DE_L_AGENT[PORTEE_PROJET]) not in bloc


def test_un_shell_libre_ou_refuse_se_dit_sans_aucun_arbitrage() -> None:
    libre = regime_de_l_agent(PolitiqueOutils())
    refuse = regime_de_l_agent(PolitiqueOutils(deny=(OUTIL_SHELL,)))

    assert f"- {SHELL_LIBRE}" in libre
    assert f"- {SHELL_REFUSE}" in refuse
    # Le shell refusé est dit une fois, par sa phrase — pas une seconde dans la liste.
    assert f": {OUTIL_SHELL}." not in refuse
    for bloc in (libre, refuse):
        assert not any(phrase in bloc for phrase in REGIME_DE_L_AGENT.values())


def test_les_autres_outils_de_la_politique_se_disent_par_sort() -> None:
    politique = PolitiqueOutils(
        ask=(
            EntreeArbitrage(OUTIL_SHELL, Decideur.AUTO, PORTEE_PROJET),
            EntreeArbitrage("mcp__slack", Decideur.HUMAIN),
            EntreeArbitrage("WebFetch", Decideur.AUTO),
        ),
        deny=("mcp__github",),
    )

    bloc = regime_de_l_agent(politique)

    assert "- Attendent l'accord d'une personne : mcp__slack." in bloc
    assert "- Passent sans attendre personne, en étant tracés : WebFetch." in bloc
    assert "- Te sont refusés : mcp__github." in bloc


# --- ③ Deux lecteurs, mêmes clés ---------------------------------------------


def test_chaque_cran_et_chaque_portee_ont_leur_phrase_pour_leurs_deux_lecteurs() -> None:
    """Un cran neuf qui n'aurait sa phrase que pour l'orchestrateur laisserait l'agent
    sans régime — ou l'inverse : les deux formes se tiennent par leurs clés."""
    assert set(REGIME_DE_L_AGENT) == set(REGIME_EXECUTION) == set(Decideur)
    assert set(PORTEE_DE_L_AGENT) == set(REGIME_PORTEE) == set(PORTEES)
    for phrase in (*REGIME_DE_L_AGENT.values(), *PORTEE_DE_L_AGENT.values()):
        assert phrase.strip()


def test_l_orchestrateur_dit_la_portee_avec_la_meme_precision_que_l_agent() -> None:
    """« Ce qui en sort » seul a laissé le modèle y ranger un service extérieur : la
    phrase de l'orchestrateur dit aussi que dépendances et réseau restent dedans."""
    portee = _a_plat(REGIME_PORTEE[PORTEE_PROJET])

    assert "l'installation de ses dépendances et le réseau compris" in portee


# --- ④ Les playbooks déjà écrits, réécrits sans leur régime ----------------------


def test_la_section_des_skills_se_met_de_cote_et_se_remet_telle_quelle() -> None:
    fiche = _fiche_de_p5()

    corps, section = scinder_section_skills(fiche.playbook)

    assert corps == METIER_DE_P5
    assert section.startswith(TITRE_SKILLS)
    assert "`npm install`" in section
    assert scinder_section_skills(METIER_DE_P5) == (METIER_DE_P5, "")


def test_la_reecriture_rend_le_playbook_apres_le_marqueur_et_rien_d_autre() -> None:
    sans_regime = METIER_DE_P5.replace(f"{REGIME_INVENTE_DE_P5}\n\n", "")
    redacteur = _Redacteur(f"J'ai retiré une section.\n{MARQUEUR_PLAYBOOK}\n{sans_regime}\n")

    reecrit = asyncio.run(
        GenerateurDefinitionAgent(provider=redacteur).sans_regime(METIER_DE_P5)
    )

    assert reecrit == sans_regime
    ((prompt, systeme),) = redacteur.appels
    assert systeme == _CADRE_SANS_REGIME
    assert METIER_DE_P5 in prompt


def test_une_reecriture_hors_contrat_ne_rend_rien() -> None:
    for reponse in ("Voici le playbook sans régime.", f"{MARQUEUR_PLAYBOOK}\n  \n"):
        generateur = GenerateurDefinitionAgent(provider=_Redacteur(reponse))
        with pytest.raises(GenerationIndisponible):
            asyncio.run(generateur.sans_regime(METIER_DE_P5))


def test_le_cadre_de_reecriture_demande_de_retirer_le_regime_et_de_garder_le_reste() -> None:
    cadre = _a_plat(_CADRE_SANS_REGIME)

    assert "Rends le playbook sans ce régime, et sans rien changer d'autre" in cadre
    assert "Maestro l'écrit désormais lui-même, depuis la politique" in cadre
    assert MARQUEUR_PLAYBOOK in cadre


def _depot_de_p5(racine: Path) -> AgentStore:
    store = AgentStore(racine / "agents")
    store.pour_projet("prj-p5").ecrire(_fiche_de_p5())
    return store


def test_la_regeneration_reecrit_le_metier_et_garde_les_skills_et_l_ancien_texte(
    tmp_path: Path,
) -> None:
    store = _depot_de_p5(tmp_path)
    avant = store.pour_projet("prj-p5").lire("dev-nextjs")
    assert avant is not None
    sans_regime = METIER_DE_P5.replace(f"{REGIME_INVENTE_DE_P5}\n\n", "")
    redacteur = _Redacteur(f"{MARQUEUR_PLAYBOOK}\n{sans_regime}")
    sortie = io.StringIO()

    code = main(
        [],
        store=store,
        generateur=GenerateurDefinitionAgent(provider=redacteur),
        archive=tmp_path / "archive",
        sortie=sortie,
    )

    assert code == CODE_FAIT
    apres = store.pour_projet("prj-p5").lire("dev-nextjs")
    assert apres is not None
    assert "service extérieur" not in apres.playbook
    _, section = scinder_section_skills(avant.playbook)
    assert apres.playbook == f"{sans_regime}\n\n{section}\n"
    # Le modèle n'a reçu que le métier : l'inventaire des skills ne se rédige pas.
    ((prompt, _),) = redacteur.appels
    assert TITRE_SKILLS not in prompt
    # Rien n'est perdu : l'ancien playbook est gardé, entier.
    garde = tmp_path / "archive" / "prj-p5" / "dev-nextjs.md"
    assert garde.read_text(encoding="utf-8") == avant.playbook
    assert "prj-p5 · dev-nextjs — réécrit" in sortie.getvalue()


def test_la_verification_n_appelle_ni_n_ecrit_rien(tmp_path: Path) -> None:
    store = _depot_de_p5(tmp_path)
    avant = store.pour_projet("prj-p5").lire("dev-nextjs")
    redacteur = _Redacteur(f"{MARQUEUR_PLAYBOOK}\nAutre chose.")
    sortie = io.StringIO()

    code = main(
        ["--check"],
        store=store,
        generateur=GenerateurDefinitionAgent(provider=redacteur),
        archive=tmp_path / "archive",
        sortie=sortie,
    )

    assert code == CODE_FAIT
    assert redacteur.appels == []
    assert store.pour_projet("prj-p5").lire("dev-nextjs") == avant
    assert not (tmp_path / "archive").exists()
    assert "prj-p5 · dev-nextjs — à réécrire" in sortie.getvalue()


def test_un_echec_laisse_la_fiche_telle_quelle_et_se_dit(tmp_path: Path) -> None:
    store = _depot_de_p5(tmp_path)
    avant = store.pour_projet("prj-p5").lire("dev-nextjs")
    redacteur = _Redacteur(panne=RuntimeError("quota épuisé"))
    sortie = io.StringIO()

    code = main(
        ["--projet", "prj-p5"],
        store=store,
        generateur=GenerateurDefinitionAgent(provider=redacteur),
        archive=tmp_path / "archive",
        sortie=sortie,
    )

    assert code == CODE_ECHEC
    assert store.pour_projet("prj-p5").lire("dev-nextjs") == avant
    assert "en échec" in sortie.getvalue()
    assert "quota épuisé" in sortie.getvalue()


def test_sans_projet_nomme_tous_les_projets_qui_ont_une_equipe(tmp_path: Path) -> None:
    store = _depot_de_p5(tmp_path)
    store.pour_projet("prj-autre").ecrire(_fiche_de_p5())
    (store.racine / "_projets" / "prj-vide").mkdir()
    store.ecrire(_fiche_de_p5())  # un gabarit, hors de tout projet : pas concerné

    assert projets_avec_equipe(store) == ("prj-autre", "prj-p5")


def test_l_archive_va_ou_on_la_nomme_un_dossier_par_passage(tmp_path: Path) -> None:
    """Le poste garde ses anciens playbooks là où il les relira — pas dans un
    worktree qu'on retirera."""
    store = _depot_de_p5(tmp_path)
    sans_regime = METIER_DE_P5.replace(f"{REGIME_INVENTE_DE_P5}\n\n", "")

    code = main(
        ["--projet", "prj-p5", "--archive", str(tmp_path / "gardes")],
        store=store,
        generateur=GenerateurDefinitionAgent(
            provider=_Redacteur(f"{MARQUEUR_PLAYBOOK}\n{sans_regime}")
        ),
        sortie=io.StringIO(),
    )

    assert code == CODE_FAIT
    (passage,) = (tmp_path / "gardes").iterdir()
    assert (passage / "prj-p5" / "dev-nextjs.md").is_file()


def test_un_projet_mal_nomme_est_refuse_avant_tout(tmp_path: Path) -> None:
    erreur = io.StringIO()

    code = main(
        ["--projet", "../ailleurs"], store=AgentStore(tmp_path / "agents"), erreur=erreur
    )

    assert code == 2
    assert "identifiant de projet invalide" in erreur.getvalue()
