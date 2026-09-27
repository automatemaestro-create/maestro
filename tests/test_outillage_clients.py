"""Un projet n'a qu'un `AGENTS.md` : un pont ne s'écrit que pour un client qui en a besoin (#1295).

Le retex du 2026-09-24 : *« AGENT.md, CLAUDE.md, GEMINI.md — pourquoi ? »*. Deux ponts d'une
ligne s'écrivaient d'office (docs/38 §3.2), pour deux clients que la personne n'utilisait peut-être
pas, et le `CLAUDE.md` masquait même la lecture native de Claude Code ≥ 2.1.277. docs/43 §2.3
renverse : un pont ne s'écrit que pour un client **que la personne utilise** — trouvé sur le poste
avec sa version, ou nommé dans la conversation — et qui **ne lit pas** `AGENTS.md` à cette version.

① **la règle**, dans `recommander` — commune au projet importé et au projet neuf : chaque état du
   rendu attendu du ticket, avec la raison que la liste de l'outillage affiche ;
② **la conversation** : « j'utilise aussi Gemini CLI », tapé pendant le questionnaire, ajoute le
   pont Gemini sous sa forme vérifiée ;
③ **la détection**, doublée à ses deux portes (le `PATH`, `--version`) — et la garde de la suite
   qui la ferme ;
④ **le contrat** : la lecture native de Claude Code, rejouée sur le vrai CLI quand il est installé
   (`cli_reel`, sauté sauf `MAESTRO_TESTS_CLI_REEL=1`).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from maestro import clients_du_poste
from maestro.controltower.chat import UTILISATEUR, MessageChat
from maestro.controltower.outillage import (
    _PROMPT_COMPREHENSION,
    ComprehensionModele,
    ConducteurOutillage,
)
from maestro.outillage import Choix, Constats, Piece, rediger
from maestro.outillage.clients import (
    CLIENTS_CONNUS,
    SOURCE_CONVERSATION,
    SOURCE_POSTE,
    Client,
    cle_de_version,
    client_connu,
    clients_depuis_texte,
    reunir,
)
from maestro.outillage.questionnaire import (
    SUJETS,
    comprehension_depuis_texte,
    recommandation_depuis_choix,
)
from maestro.outillage.recommandation import recommander
from maestro.providers.base import ModelProvider

# --- Fabriques ---------------------------------------------------------------------------


def _claude(version: str | None, source: str = SOURCE_POSTE) -> Client:
    return Client(cle="claude", libelle="Claude Code", version=version, source=source)


def _gemini(version: str | None = "0.9.0", source: str = SOURCE_POSTE) -> Client:
    return Client(cle="gemini", libelle="Gemini CLI", version=version, source=source)


def _present(*noms: str) -> Constats:
    """Un projet importé qui porte déjà ces fichiers à sa racine."""
    return Constats(outillage_present=tuple(Piece(nom=n, chemin=n, role="pont") for n in noms))


def _ponts(recommandation) -> dict[str, object]:
    return {e.chemin: e for e in recommandation.entrees if e.type == "pont"}


def _ecartes_ponts(recommandation) -> dict[str, str]:
    return {e.nom: e.raison for e in recommandation.ecartes if e.type == "pont"}


# --- ① La règle, dans `recommander` --------------------------------------------------------


def test_sur_un_poste_ou_claude_code_lit_agents_md_seul_agents_md_est_propose() -> None:
    """Critère 1 : Claude Code ≥ 2.1.277 seul client — `AGENTS.md` et ses skills, rien d'autre."""
    reco = recommander(Constats(), (_claude("2.1.281"),))

    assert [e.chemin for e in reco.entrees if e.type in {"instructions", "pont"}] == ["AGENTS.md"]
    ecartes = _ecartes_ponts(reco)
    # Maestro dit pourquoi il n'y a qu'un fichier : le nom, la version, le seuil.
    assert "Claude Code 2.1.281 (trouvé sur ce poste)" in ecartes["CLAUDE.md"]
    assert "2.1.277" in ecartes["CLAUDE.md"] and "masquerait" in ecartes["CLAUDE.md"]
    assert "Gemini CLI" in ecartes["GEMINI.md"]
    assert "Claude Code 2.1.281" in reco.entrees[0].raison


def test_sans_aucun_client_trouve_aucun_pont_n_est_suppose() -> None:
    """Un client introuvable n'est pas supposé présent — et l'absence de pont se dit."""
    reco = recommander(Constats())

    assert _ponts(reco) == {}
    ecartes = _ecartes_ponts(reco)
    assert set(ecartes) == {"CLAUDE.md", "GEMINI.md"}
    assert all("ni trouvé sur ce poste ni nommé" in raison for raison in ecartes.values())


def test_claude_code_anterieur_a_la_lecture_native_recoit_son_pont_avec_sa_raison() -> None:
    """État « Claude Code < 2.1.277 » : le pont `CLAUDE.md`, nom et version dans la raison."""
    reco = recommander(Constats(), (_claude("2.1.200"),))

    pont = _ponts(reco)["CLAUDE.md"]
    assert pont.etat == "a-generer"
    assert "Claude Code 2.1.200 (trouvé sur ce poste)" in pont.raison
    assert "2.1.277" in pont.raison and "@AGENTS.md" in pont.raison
    assert "CLAUDE.md" not in _ecartes_ponts(reco)


def test_une_version_de_claude_code_inconnue_recoit_le_pont_et_le_dit() -> None:
    """Une version qu'on n'a pas pu lire n'est pas supposée récente : le pont vaut pour toutes."""
    reco = recommander(Constats(), (_claude(None),))

    pont = _ponts(reco)["CLAUDE.md"]
    assert "version inconnue" in pont.raison
    assert "toutes ses versions" in pont.raison


def test_gemini_cli_present_recoit_son_pont_avec_sa_raison() -> None:
    """État « Gemini CLI présent » : `GEMINI.md`, qui l'importe en une ligne."""
    reco = recommander(Constats(), (_claude("2.1.281"), _gemini("0.9.0")))

    assert list(_ponts(reco)) == ["GEMINI.md"]
    raison = _ponts(reco)["GEMINI.md"].raison
    assert "Gemini CLI 0.9.0 (trouvé sur ce poste)" in raison
    assert "GEMINI.md par défaut" in raison and "@AGENTS.md" in raison


def test_le_pont_gemini_est_la_ligne_d_import_verifiee_jamais_une_copie() -> None:
    """La forme vérifiée (docs/38 §3.2) : `GEMINI.md` d'une ligne — ni copie, ni réglage."""
    constats = Constats()
    fichiers = rediger(constats, recommander(constats, (_gemini(),)))

    (pont,) = [f for f in fichiers if f.role == "pont"]
    assert (pont.chemin, pont.contenu) == ("GEMINI.md", "@AGENTS.md")
    assert not any(f.chemin.startswith(".gemini/") for f in fichiers)


def test_un_client_qui_lit_agents_md_de_lui_meme_ne_recoit_aucun_pont() -> None:
    codex = Client(cle="codex", libelle="Codex CLI", source=SOURCE_POSTE)

    reco = recommander(Constats(), (codex,))

    assert _ponts(reco) == {}
    assert "Codex CLI (trouvé sur ce poste)" in reco.entrees[0].raison


def test_un_client_que_maestro_ne_connait_pas_est_nomme_sans_pont_invente() -> None:
    zed = Client(cle="zed", libelle="Zed", source=SOURCE_CONVERSATION)

    reco = recommander(Constats(), (zed,))

    assert _ponts(reco) == {}
    assert "ne sait pas" in _ecartes_ponts(reco)["Zed"]


def test_un_projet_importe_garde_ses_ponts_sans_qu_aucun_client_ne_les_demande() -> None:
    """État « projet importé qui a déjà ses ponts » : gardés, jamais retirés ni réécrits."""
    constats = _present("CLAUDE.md", "GEMINI.md")

    reco = recommander(constats)

    ponts = _ponts(reco)
    assert {chemin: e.etat for chemin, e in ponts.items()} == {
        "CLAUDE.md": "deja-present",
        "GEMINI.md": "deja-present",
    }
    assert all("gardé tel quel" in e.raison for e in ponts.values())
    # `deja-present` ne rend aucun fichier : ce que le projet porte n'est pas réécrit.
    assert not [f for f in rediger(constats, reco) if f.role == "pont"]


def test_un_claude_md_deja_present_masque_la_lecture_native_donc_il_recoit_le_bloc() -> None:
    """Claude Code lit un `CLAUDE.md` **à la place** d'`AGENTS.md` : le pont y devient dû."""
    constats = _present("CLAUDE.md")

    reco = recommander(constats, (_claude("2.1.281"),))

    pont = _ponts(reco)["CLAUDE.md"]
    assert pont.etat == "a-completer"
    assert "à la place d'AGENTS.md" in pont.raison and "bloc" in pont.raison


def test_la_version_se_compare_en_nombres_jamais_en_texte() -> None:
    claude = client_connu("claude")
    assert claude is not None

    assert claude.lit_agents_md("2.1.1000") is True  # « 2.1.1000 » < « 2.1.277 » en texte
    assert claude.lit_agents_md("2.1.276") is False
    assert claude.lit_agents_md("2.1.277 (Claude Code)") is True
    assert claude.lit_agents_md(None) is None
    assert claude.lit_agents_md("2.1.281", pont_present=True) is False
    assert cle_de_version("0.9.0-preview.1") == (0, 9, 0)


# --- ② La conversation --------------------------------------------------------------------


def test_les_clients_nommes_se_lisent_avec_leur_version() -> None:
    lus = clients_depuis_texte("Gemini CLI, Claude Code 2.1.200 ; Zed v0.150.4")

    assert [(c.cle, c.libelle, c.version) for c in lus] == [
        ("gemini", "Gemini CLI", None),
        ("claude", "Claude Code", "2.1.200"),
        ("zed", "Zed", "0.150.4"),
    ]
    assert all(c.source == SOURCE_CONVERSATION for c in lus)


def test_la_version_mesuree_sur_le_poste_l_emporte_sur_celle_qui_est_dite() -> None:
    poste = (_claude("2.1.281"), Client(cle="codex", libelle="Codex CLI"))
    dits = (_claude("2.1.200", SOURCE_CONVERSATION), _gemini(None, SOURCE_CONVERSATION))

    reunis = {c.cle: c for c in reunir(poste, dits)}

    assert reunis["claude"].version == "2.1.281"
    assert reunis["claude"].source == SOURCE_POSTE
    assert set(reunis) == {"claude", "codex", "gemini"}


def test_j_utilise_aussi_gemini_cli_ajoute_le_pont_gemini() -> None:
    """Critère 2, par les constats du questionnaire : le sujet `clients` nourrit la règle."""
    compris = [
        Choix("nature", "Une API en Python", deduit=True),
        Choix("clients", "Gemini CLI", deduit=True, parce_que="vous utilisez aussi Gemini"),
    ]

    reco = recommandation_depuis_choix(compris, poste=(_claude("2.1.281"),))

    assert list(_ponts(reco)) == ["GEMINI.md"]
    assert "Gemini CLI (nommé dans la conversation)" in _ponts(reco)["GEMINI.md"].raison
    assert "CLAUDE.md" in _ecartes_ponts(reco)


class _FauxModele(ModelProvider):
    """Rend, dans l'ordre, les compréhensions qu'on lui a données."""

    name = "faux-modele"

    def __init__(self, *reponses: dict[str, object]) -> None:
        self._reponses = [json.dumps(r) for r in reponses]
        self.prompts: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self,
        prompt: str,
        *,
        model: str,
        system_prompt: str | None = None,
        effort: str | None = None,
    ) -> str:
        self.prompts.append(prompt)
        return self._reponses[min(len(self.prompts), len(self._reponses)) - 1]


_API_PYTHON = [
    {"cle": "nature", "valeur": "Une API en Python", "parce_que": "vous l'avez décrite ainsi"},
    {"cle": "langages", "valeur": "Python", "parce_que": "vous l'avez dit"},
    {"cle": "tester", "valeur": "pytest", "parce_que": "le lanceur de tests de Python"},
]


def _message(auteur: str, contenu: str, **champs: object) -> MessageChat:
    return MessageChat(agent="orchestrateur", auteur=auteur, contenu=contenu, **champs)


def test_la_correction_tapee_dans_le_questionnaire_ajoute_le_pont_gemini() -> None:
    """Critère 2, de bout en bout dans le fil : la phrase tapée, comprise, puis la conclusion.

    Le modèle est un double, le reste est réel : la question en attente, la frappe qui y
    répond, la lecture vérifiée de ce qu'il a compris, la conclusion et sa recommandation.
    """
    question_ci = {
        "cle": "ci",
        "intitule": "Une intégration continue ?",
        "options": [{"valeur": "aucun", "libelle": "Pas encore", "raison": "rien à rejouer"}],
    }
    faux = _FauxModele(
        {"constats": _API_PYTHON, "questions": [question_ci]},
        {
            "constats": [
                *_API_PYTHON,
                {"cle": "ci", "valeur": "aucun", "parce_que": "pas de CI pour l'instant"},
                {"cle": "clients", "valeur": "Gemini CLI", "parce_que": "vous utilisez Gemini"},
            ],
            "questions": [],
        },
    )
    conducteur = ConducteurOutillage(ComprehensionModele(faux), clients=lambda: ())
    fil = [_message(UTILISATEUR, "Une API en Python")]
    premiere = asyncio.run(conducteur.ouvrir(fil))
    assert premiere.question is not None
    fil += [
        _message("orchestrateur", premiere.contenu, question=premiere.question),
        _message(
            UTILISATEUR,
            "Pas de CI. J'utilise aussi Gemini CLI.",
            choix=Choix("ci", "Pas de CI. J'utilise aussi Gemini CLI.", libre=True),
        ),
    ]

    conclusion = asyncio.run(conducteur.ouvrir(fil))

    assert conclusion.question is None
    assert "J'utilise aussi Gemini CLI" in faux.prompts[-1]
    reco = recommandation_depuis_choix(conclusion.comprehension)
    assert list(_ponts(reco)) == ["GEMINI.md"]
    # La phrase de conclusion compte ce que l'écran montrera, pont compris.
    assert f"{len(reco.entrees)} entrée(s)" in conclusion.contenu


def test_la_conclusion_compte_avec_les_clients_du_poste() -> None:
    """Le fil et l'écran comptent la même liste : le conducteur lit les mêmes clients."""
    faux = _FauxModele({"constats": _API_PYTHON, "questions": []})
    avec_gemini = ConducteurOutillage(ComprehensionModele(faux), clients=lambda: (_gemini(),))

    conclusion = asyncio.run(avec_gemini.ouvrir([_message(UTILISATEUR, "Une API en Python")]))

    reco = recommandation_depuis_choix(conclusion.comprehension, poste=(_gemini(),))
    assert list(_ponts(reco)) == ["GEMINI.md"]
    assert f"{len(reco.entrees)} entrée(s)" in conclusion.contenu


def test_le_sujet_clients_est_au_schema_et_le_prompt_nomme_les_clients_connus() -> None:
    assert "clients" in SUJETS
    for connu in CLIENTS_CONNUS:
        assert connu.libelle in _PROMPT_COMPREHENSION
    # Le modèle rend le sujet par son nom d'écran : il revient à sa clé, et nourrit la règle.
    lue = comprehension_depuis_texte(
        json.dumps({"constats": [*_API_PYTHON, {"cle": SUJETS["clients"], "valeur": "gemini"}]}),
        [],
    )
    assert "clients" in {c.cle for c in lue.constats}


# --- ③ La détection, doublée ----------------------------------------------------------------


def test_un_client_introuvable_sur_le_poste_n_est_pas_suppose_present() -> None:
    lus: list[str] = []

    trouves = clients_du_poste.detecter(
        resolveur=lambda commande: None, versionneur=lambda chemin: lus.append(chemin) or "1.0.0"
    )

    assert trouves == ()
    assert lus == []


def test_la_version_n_est_lue_que_pour_un_client_a_qui_un_pont_peut_manquer() -> None:
    poste = {"claude": "/bin/claude", "gemini": "/bin/gemini", "codex": "/bin/codex"}
    lus: list[str] = []

    def versionneur(chemin: str) -> str:
        lus.append(chemin)
        return {"/bin/claude": "2.1.281", "/bin/gemini": "0.9.0"}[chemin]

    trouves = clients_du_poste.detecter(resolveur=poste.get, versionneur=versionneur)

    assert [(c.cle, c.version, c.origine) for c in trouves] == [
        ("claude", "2.1.281", "/bin/claude"),
        ("gemini", "0.9.0", "/bin/gemini"),
        ("codex", None, "/bin/codex"),
    ]
    assert lus == ["/bin/claude", "/bin/gemini"]
    assert all(c.source == SOURCE_POSTE for c in trouves)


def test_lire_version_lance_le_binaire_avec_version_seule_et_lit_sa_sortie() -> None:
    """Sur un vrai binaire, sans réseau : l'interpréteur de cette suite."""
    version = clients_du_poste.lire_version(sys.executable)

    assert version == ".".join(str(n) for n in sys.version_info[:3])


def test_une_version_qui_ne_vient_pas_est_inconnue_jamais_devinee(tmp_path: Path) -> None:
    assert clients_du_poste.lire_version(str(tmp_path / "absent")) is None


def test_la_suite_ne_lit_pas_les_clients_du_poste() -> None:
    """La garde de `tests/conftest.py` : un poste nu, celui de la CI, quel que soit le poste."""
    assert clients_du_poste.resoudre("python") is None
    assert clients_du_poste.detecter() == ()


# --- ③bis Ce que la note de décision en garde --------------------------------------------------

_DOCS_38 = Path(__file__).resolve().parents[1] / "docs/38-decision-outillage-universel-du-projet.md"


def _section(texte: str, debut: str, fin: str) -> str:
    """Le texte d'une section de la note, de son titre au titre suivant."""
    return texte.split(debut, 1)[1].split(fin, 1)[0]


def test_docs_38_renvoie_a_la_decision_et_dit_ce_qui_l_a_fait_bouger() -> None:
    """Critère 3 : §3.2 porte le renvoi ⚠ vers la note de décision, §7 la condition qui a joué.

    Une note qui décrirait encore deux ponts d'office, sans renvoi, ferait relire la règle
    d'avant à qui conteste celle-ci ; et §7 perdrait la seule condition dont on sait qu'elle a
    déjà rouvert la décision — la lecture native d'`AGENTS.md` par Claude Code.
    """
    texte = _DOCS_38.read_text(encoding="utf-8")
    ponts = _section(texte, "### 3.2 ", "### 3.3 ")
    rouvrir = _section(texte, "## 7. ", "## 8. ")

    # Le renvoi ouvre la section : c'est la première chose que lit qui y arrive.
    assert ponts.split("\n", 1)[1].lstrip().startswith("> ⚠")
    assert "43-decision-un-projet-nait-dans-la-conversation.md" in ponts
    assert "#1295" in ponts and "GEMINI.md" in ponts
    assert "Claude Code lit `AGENTS.md` de lui-même" in rouvrir
    assert "2.1.277" in rouvrir and "test_outillage_clients.py" in rouvrir


# --- ④ Le contrat, sur le vrai CLI ------------------------------------------------------------
#
# Ce que la règle affirme de Claude Code — il lit `AGENTS.md` de lui-même depuis la v2.1.277,
# et un `CLAUDE.md` le masque — est un fait **extérieur** : il se rejoue sur le CLI installé,
# jamais ne se suppose (docs/38 §7). Un mot-témoin au hasard dans `AGENTS.md`, une question
# qui ne se répond qu'en l'ayant lu, et le modèle le plus économe de la gamme.


def _modele_econome() -> str:
    from maestro.familles_claude import derniere_version

    return derniere_version("haiku")


def _demande_au_vrai_claude(racine: Path, chemin: str) -> str:
    sortie = subprocess.run(  # noqa: S603 — le CLI du poste, sur un dossier jetable
        [
            chemin,
            "-p",
            "Quel est le mot de passe de ce projet, d'après ses instructions ? "
            "Réponds par le mot seul, ou par INCONNU si tes instructions ne le donnent pas.",
            "--model",
            _modele_econome(),
        ],
        cwd=racine,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=240,
        stdin=subprocess.DEVNULL,
        check=False,
    )
    assert sortie.returncode == 0, sortie.stderr
    return sortie.stdout


def _claude_du_poste() -> Client:
    (claude,) = [c for c in clients_du_poste.detecter() if c.cle == "claude"] or [None]
    if claude is None:
        pytest.skip("Claude Code n'est pas installé sur ce poste")
    return claude


def _projet_temoin(tmp_path: Path) -> tuple[Path, str]:
    temoin = f"PONT-{uuid.uuid4().hex[:8].upper()}"
    racine = tmp_path / "projet-temoin"
    racine.mkdir()
    (racine / "AGENTS.md").write_text(
        f"# AGENTS.md\n\nLe mot de passe de ce projet est {temoin}.\n", encoding="utf-8"
    )
    return racine, temoin


@pytest.mark.cli_reel
@pytest.mark.clients_du_poste
def test_contrat_claude_code_lit_agents_md_seul_a_sa_version(tmp_path: Path) -> None:
    """Ce que la règle attend de la version installée, rejoué : lu, ou pas lu, sans pont."""
    claude = _claude_du_poste()
    connu = claude.connu
    assert connu is not None
    racine, temoin = _projet_temoin(tmp_path)

    reponse = _demande_au_vrai_claude(racine, claude.origine)

    attendu = connu.lit_agents_md(claude.version)
    assert attendu is not None, f"version de Claude Code illisible : {claude}"
    assert (temoin in reponse) is attendu, (claude.version, reponse)


@pytest.mark.cli_reel
@pytest.mark.clients_du_poste
def test_contrat_un_claude_md_sans_import_masque_agents_md(tmp_path: Path) -> None:
    """L'échantillon fautif du contrat : un `CLAUDE.md` lu à la place, le témoin ne sort plus.

    C'est aussi le fait qui fait écrire un bloc `@AGENTS.md` dans le `CLAUDE.md` d'un projet
    importé : sans lui, l'outillage de Maestro y resterait invisible pour Claude Code.
    """
    claude = _claude_du_poste()
    racine, temoin = _projet_temoin(tmp_path)
    (racine / "CLAUDE.md").write_text("# Ce projet\n\nRien de plus.\n", encoding="utf-8")

    reponse = _demande_au_vrai_claude(racine, claude.origine)

    assert temoin not in reponse, reponse
