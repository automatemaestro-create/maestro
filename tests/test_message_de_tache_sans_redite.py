"""Ce que l'agent d'un projet neuf reçoit se dit une fois (#1404).

Le défaut, relevé sur p5 le 2026-10-01 : l'agent `socle-nextjs` recevait un message de
tâche de 13 646 caractères et un prompt système de 10 641, qui se répétaient et le
dispersaient :

- `AGENTS.md` disait **sept fois** la même raison « À vérifier », chaque fois suivie
  d'une promesse de Maestro qui ne servait pas l'agent ;
- il annonçait `.github/workflows/ci.yml` et `CONTRIBUTING.md` « à créer », hors de la
  tâche ;
- la liste des skills paraissait deux fois dans le message (l'index d'`AGENTS.md`, puis
  « Skills du projet ») et deux fois dans le prompt système (le modèle qui rédige le
  playbook recopiait les noms que l'intention lui donnait, puis Maestro ajoutait sa
  section) ; le titre « L'outillage de ce projet » deux fois ;
- le prompt système tutoyait l'agent, puis le vouvoyait.

La preuve est celle que le ticket demande : le **chemin réel**, des réponses d'un projet
neuf jusqu'au fournisseur — outillage écrit et vérifié sur son dossier vide, équipe
proposée, playbook rédigé depuis l'intention, rôle créé avec ses skills, tâche exécutée
par `runtime_outille` —, et un faux fournisseur qui retient le message et le prompt.
Chaque élément y est **compté**. Seul le modèle est doublé : celui qui rédige le
playbook reformule ce qu'on lui donne, et c'est par là que les skills entraient deux
fois.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from maestro.agents.fiche_outillee import runtime_outille
from maestro.controltower.auto_amelioration import MARQUEUR_PLAYBOOK
from maestro.controltower.generation_agent import INTENTION_MAX, GenerateurDefinitionAgent
from maestro.equipe import (
    TITRE_SKILLS,
    RoleValide,
    SkillRetenu,
    definition,
    proposer_equipe,
    skills_constates,
)
from maestro.equipe.modele import RolePropose
from maestro.outillage import (
    Choix,
    constats_depuis_choix,
    generer_outillage,
    outillage_du_projet,
    recommandation_depuis_choix,
    source_manifeste_des_choix,
)
from maestro.outillage.verification import A_VERIFIER, Verificateur
from maestro.projets.modele import Projet
from maestro.providers.base import ModelProvider
from maestro.sandbox import verification as execution

QUAND = "2026-10-01T09:00:00+00:00"

#: Un interpréteur factice : le joueur doublé ne le lance jamais.
FAUX_BASH = ("bash", "-c")

#: Ce que la personne a dit de p5 — une application web Next.js —, sept commandes
#: comprises de ses réponses, la forge, la CI et la convention cliquées.
COMPRIS_P5 = (
    ("nature", "Une application web"),
    ("langages", "TypeScript"),
    ("manifeste", "package.json"),
    ("gestionnaire", "npm"),
    ("installer", "npm install"),
    ("construire", "npm run build"),
    ("demarrer", "npm run dev"),
    ("tester", "npx vitest run"),
    ("lint", "npm run lint"),
    ("formater", "npx prettier --check ."),
    ("types", "npx tsc --noEmit"),
)

#: Les marques du vouvoiement — un mot entier, jamais une partie de mot.
VOUVOIEMENT = re.compile(r"\b(vous|votre|vos|vôtres?|servez)\b", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _maison_isolee(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """`Path.home()` sous `tmp_path` : `valider_racine` refuse `AppData` sous Windows (#221)."""
    maison = tmp_path / "maison"
    maison.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: maison))
    return maison


class _Joueur:
    """Le poste doublé : chaque outil répond présent, rien du projet n'est joué."""

    def __call__(
        self, commande: str, cwd: Path, *, interprete: Any, delai_s: float
    ) -> execution.Execution:
        return execution.Execution(code=0, sortie="ok", duree_s=0.1)


class _RedacteurQuiReformule(ModelProvider):
    """Le modèle qui rédige un playbook (#257) : il reformule l'intention qu'on lui donne.

    C'est ce qu'il a fait sur p5 — sa section « Ton périmètre » reprenait la phrase
    d'où il écrivait, skills compris. Le doublon ne lui doit rien : il recopie ce qu'il
    reçoit, et ce qu'on ne lui donne pas, il ne le recopie pas.
    """

    name = "redacteur"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        intention = prompt.split("Intention de l'utilisateur :", 1)[1].strip().splitlines()[0]
        return (
            "NOM: socle-nextjs\n"
            "ROLE: Développeur Next.js\n"
            "COMPETENCES: nextjs, react, typescript\n"
            "FOURNISSEUR:\n"
            "MODELE:\n"
            f"{MARQUEUR_PLAYBOOK}\n"
            "# Développeur Next.js du livre de recettes\n\n"
            "Tu poses et fais grandir l'application du livre de recettes.\n\n"
            f"## Ton périmètre\n\n{intention}\n\n"
            "## Comment tu procèdes\n\n"
            "Tu lis ce dont tu as besoin, tu écris le code, puis tu rends compte."
        )


class _Executant(ModelProvider):
    """Le fournisseur de la tâche : retient le message et le prompt système, n'écrit rien."""

    name = "executant"

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.systemes: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("une tâche outillée passe par run_agent")

    async def run_agent(self, prompt, *, model, system_prompt=None, workspace, tools, **_):
        self.messages.append(prompt)
        self.systemes.append(system_prompt or "")
        return "Fait."


@dataclass(frozen=True)
class _Capture:
    """Ce que l'agent de p5 a reçu, et ce qui a servi à le composer."""

    message: str
    systeme: str
    raisons_a_verifier: tuple[str, ...]
    skills_du_message: tuple[str, ...]
    skills_du_role: tuple[str, ...]
    role: RolePropose


def _choix_de_p5() -> list[Choix]:
    return [
        *(Choix(cle=cle, valeur=valeur, deduit=True) for cle, valeur in COMPRIS_P5),
        Choix("forge", "github"),
        Choix("ci", ".github/workflows/ci.yml"),
        Choix("conventions", "conventional-commits"),
    ]


def _role_le_plus_outille(roles: tuple[RolePropose, ...]) -> RolePropose:
    """Le rôle qui branche le plus de skills — celui du socle, sur p5."""
    return max(roles, key=lambda role: len(role.skills))


def _tache_de_p5(tmp_path: Path) -> _Capture:
    """Le chemin réel, des réponses de p5 jusqu'au fournisseur de sa première tâche."""
    racine = Path.home() / "Maestro" / "livre-de-recettes"
    racine.mkdir(parents=True)
    projet = Projet(id="prj-0000p500", nom="Livre de recettes", racine=racine.as_posix())
    choix = _choix_de_p5()
    constats = constats_depuis_choix(choix)
    recommandation = recommandation_depuis_choix(choix)
    source = source_manifeste_des_choix(projet.id, choix)

    # ① L'outillage, écrit et vérifié sur le dossier encore vide du projet.
    preparation = generer_outillage(
        projet,
        constats,
        recommandation,
        source=source,
        horodatage=QUAND,
        verificateur=Verificateur(joueur=_Joueur(), interprete=FAUX_BASH),
    )
    # ② L'équipe proposée, le playbook rédigé depuis l'intention, le rôle créé.
    proposition = proposer_equipe(
        constats, recommandation, projet_id=projet.id, choix=choix, source=source
    )
    propose = _role_le_plus_outille(proposition.roles)
    redige = asyncio.run(
        GenerateurDefinitionAgent(
            provider=_RedacteurQuiReformule(), fournisseurs=lambda: ()
        ).proposer(propose.intention[:INTENTION_MAX])
    )
    valide = skills_constates(
        RoleValide(
            nom=propose.nom,
            role=propose.role,
            competences=propose.competences,
            playbook=redige.playbook,
            skills=tuple(
                SkillRetenu(nom=s.nom, chemin=s.chemin, commandes=s.commandes)
                for s in propose.skills
            ),
            politique=propose.politique(),
        ),
        lambda chemin: (racine / chemin).is_file(),
    )
    # ③ La première tâche, jusqu'au fournisseur.
    executant = _Executant()
    asyncio.run(
        runtime_outille(executant, definition(valide).to_agent()).execute(
            "Poser le socle Next.js du livre de recettes",
            projet=projet,
            tache_id="t-socle",
            politique=valide.politique,
        )
    )
    (message,) = executant.messages
    (systeme,) = executant.systemes
    return _Capture(
        message=message,
        systeme=systeme,
        raisons_a_verifier=tuple(
            v.raison for v in preparation.rapport.verifications if v.etat == A_VERIFIER
        ),
        skills_du_message=tuple(s.nom for s in outillage_du_projet(projet).skills),
        skills_du_role=tuple(s.nom for s in valide.skills),
        role=propose,
    )


def _nommes(texte: str, nom: str) -> int:
    """Combien de fois `texte` nomme le skill `nom` — hors des chemins qui le contiennent."""
    return len(re.findall(rf"(?<![\w/.-]){re.escape(nom)}(?![\w/-])", texte))


def test_la_raison_commune_des_commandes_a_verifier_se_dit_une_fois(tmp_path: Path) -> None:
    """Sept commandes ne se jouent pas, pour une même raison : elle se lit une fois."""
    capture = _tache_de_p5(tmp_path)

    (raison,) = set(capture.raisons_a_verifier)
    assert len(capture.raisons_a_verifier) == 7
    assert capture.message.count(raison) == 1
    # Chaque commande garde son verdict, à côté d'elle ; la raison suit la dernière ligne
    # de la première section qui en porte une.
    assert capture.message.count("**À vérifier**.") == 7
    assert (
        "Les commandes marquées **À vérifier** n'ont pas encore été jouées, pour une même "
        f"raison : {raison}." in capture.message
    )
    # La suite que Maestro donnera n'est pas l'affaire de l'agent.
    assert "prochaine écriture de l'outillage" not in capture.message


def test_ce_qui_est_prevu_ne_se_cree_que_si_la_tache_le_demande(tmp_path: Path) -> None:
    """La CI et `CONTRIBUTING.md` sont prévus, pas commandés : la règle se dit une fois."""
    capture = _tache_de_p5(tmp_path)

    assert "à créer" not in capture.message.lower()
    assert "`.github/workflows/ci.yml`, prévu" in capture.message
    assert "`CONTRIBUTING.md`, prévu" in capture.message
    assert capture.message.count("ne se crée que si ta tâche le demande") == 1


def test_les_skills_se_listent_une_fois_dans_le_message(tmp_path: Path) -> None:
    """L'index d'`AGENTS.md` suffit : la consigne garde la règle de lecture, pas la liste."""
    capture = _tache_de_p5(tmp_path)

    assert len(capture.skills_du_message) >= 4
    for nom in capture.skills_du_message:
        assert _nommes(capture.message, nom) == 1, nom
    assert capture.message.count("## L'outillage de ce projet") == 1
    # La règle de lecture reste.
    assert "**outil de lecture de fichier**" in capture.message


def test_les_skills_se_listent_une_fois_dans_le_prompt_systeme(tmp_path: Path) -> None:
    """Le modèle qui rédige le playbook ne reçoit plus les noms des skills : seule la
    section de Maestro les nomme."""
    capture = _tache_de_p5(tmp_path)

    assert capture.skills_du_role
    for nom in capture.skills_du_role:
        assert nom not in capture.role.intention
        assert _nommes(capture.systeme, nom) == 1, nom
    assert capture.systeme.count(TITRE_SKILLS) == 1


def test_le_prompt_systeme_tutoie_l_agent_de_bout_en_bout(tmp_path: Path) -> None:
    capture = _tache_de_p5(tmp_path)

    assert VOUVOIEMENT.findall(capture.systeme) == []


def test_la_sonde_du_vouvoiement_le_reconnait_et_ne_voit_pas_un_mot_qui_le_contient() -> None:
    """L'échantillon fautif, avant le verdict : sans lui, le balayage serait vert par accident."""
    assert VOUVOIEMENT.findall("Servez-vous-en, c'est votre rôle.") == ["Servez", "vous", "votre"]
    assert VOUVOIEMENT.findall("Les skills qui sont les vôtres.") == ["vôtres"]
    assert VOUVOIEMENT.findall("Tu trouves les voûtes et les vosges.") == []


def test_la_sonde_des_noms_ne_compte_pas_un_chemin() -> None:
    """Un chemin contient le nom de son skill : il désigne le fichier, il ne liste pas."""
    texte = "- **mettre-en-route** → `.agents/skills/mettre-en-route/SKILL.md`, mettre-en-route."

    assert _nommes(texte, "mettre-en-route") == 2
    assert _nommes("`.agents/skills/mettre-en-route/SKILL.md`", "mettre-en-route") == 0
