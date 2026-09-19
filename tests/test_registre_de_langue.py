"""Un seul registre de langue dans toute l'interface, agents compris (#945).

Constat **C5** du [retex du 2026-09-11](../docs/retex/2026-09-11-premiere-session-utilisateur.md) :
l'assistant et l'orchestration **tutoyaient** l'utilisateur (« Je te propose », « Ce que je peux
te dire ») quand tout le reste du produit **vouvoie** — y compris le message d'accueil du chat,
deux lignes au-dessus. La cause n'était pas une phrase mal écrite : **aucun prompt ne disait le
registre**, et un modèle rend alors celui dans lequel on s'adresse à lui.

Ce que cette suite garde tient en une phrase : **le registre existe une fois, et tout ce que le
produit sert le porte**. Les deux moitiés comptent autant —

- *une fois* : `_registre.md` est la source, et le socle l'appelle, si bien qu'un rôle neuf ne
  peut pas l'oublier (il porte déjà `{{socle}}`). Trois phrases écrites à trois endroits
  finiraient par en faire trois registres, ce qui est exactement le défaut d'origine ;
- *tout* : les deux chemins d'exécution des rôles (texte du catalogue, playbook outillé) **et**
  les trois prompts conversationnels de la Control Tower, qui ne passent par aucun playbook.

⚠ Ce qui est vérifié ici est que la **consigne** est servie, jamais ce qu'un modèle en fait : le
registre d'une réponse est du jugement, pas de la plomberie (même partage que l'aveu d'ignorance
de #748). On peut le lui demander sans ambiguïté, et c'est tout ce qui se teste.
"""

from __future__ import annotations

import pytest

from maestro.agents import playbook_du_code as pdc
from maestro.agents.catalog import DEFAULT_AGENTS
from maestro.agents.playbook_du_code import playbook_du_code, registre, roles_du_code, socle
from maestro.controltower.assistance import _PROMPT_ASSISTANCE
from maestro.controltower.chat import _CADRE_CONVERSATION
from maestro.controltower.generation_agent import _CADRE_GENERATION
from maestro.controltower.orchestration import _PROMPT_ORCHESTRATION

#: Les prompts **conversationnels** de la Control Tower : ceux qui parlent à un humain sans
#: passer par un playbook de rôle, donc sans socle pour leur porter le registre.
CONVERSATIONNELS = {
    "assistant (#123)": _PROMPT_ASSISTANCE,
    "orchestration (#685)": _PROMPT_ORCHESTRATION,
    "cadre de conversation d'un agent (#85)": _CADRE_CONVERSATION,
}


# --- La consigne elle-même ------------------------------------------------------------


def test_le_registre_prescrit_le_vouvoiement():
    texte = registre()
    assert "vouvoies" in texte
    assert "vous" in texte


def test_le_registre_distingue_les_deux_adresses():
    """Le piège du correctif, et la raison pour laquelle il ne se fait pas au chercher-remplacer.

    Un prompt système **tutoie l'agent** — c'est la convention du dépôt, et
    `generation_agent` l'écrit en toutes lettres. Ce tutoiement-là n'est pas le défaut ;
    le défaut est qu'il se **recopiait** dans les réponses, faute que rien ne dise à
    l'agent que l'adresse change quand il écrit à l'utilisateur. Retirer le premier aurait
    été se tromper de cible ; c'est le second que la consigne nomme.
    """
    texte = registre()
    # Les deux mots qui portent la distinction, et non la phrase qui les relie : celle-ci
    # peut se réécrire, la distinction non — sans elle la consigne redevient ambiguë.
    assert "tutoient" in texte, "la consigne ne dit plus que c'est l'agent qu'on tutoie"
    assert "vouvoie" in texte, "la consigne ne dit plus que c'est l'utilisateur qu'on vouvoie"


# --- Une source unique, que le socle diffuse ------------------------------------------


def test_le_socle_porte_le_registre():
    # C'est ce qui fait qu'un rôle neuf ne peut pas l'oublier : il porte déjà `{{socle}}`.
    assert registre() in socle()


def test_le_socle_ne_part_pas_avec_une_accolade():
    # `socle()` sert l'exécution texte du catalogue **directement** : un marqueur non
    # substitué y partirait tel quel en prompt système.
    assert "{{" not in socle()


@pytest.mark.parametrize("role", roles_du_code())
def test_chaque_playbook_du_code_porte_le_registre(role):
    contenu = playbook_du_code(role)
    assert registre() in contenu
    assert "{{" not in contenu


@pytest.mark.parametrize("agent", DEFAULT_AGENTS, ids=lambda a: a.nom)
def test_chaque_agent_du_catalogue_porte_le_registre(agent):
    # L'autre chemin d'exécution — le texte du catalogue, qui prend le socle sans le
    # cadre outillé. Les deux doivent recevoir la même consigne, sans quoi le même rôle
    # ne parlerait pas pareil selon qu'il a des outils.
    assert registre() in agent.prompt_systeme


@pytest.mark.parametrize("nom", sorted(CONVERSATIONNELS))
def test_chaque_prompt_conversationnel_porte_le_registre(nom):
    assert registre() in CONVERSATIONNELS[nom]


def test_le_generateur_prescrit_le_registre_au_playbook_qu_il_ecrit():
    """Un agent **personnalisé** (#72) n'a aucun socle : son playbook est écrit pour lui.

    Sans cette consigne, chaque agent généré repartirait avec le défaut de départ — et
    c'est en conversation directe qu'on le verrait.
    """
    assert "vouvoie l'utilisateur" in _CADRE_GENERATION


# --- Ce que la composition ne doit pas casser -----------------------------------------


def test_le_contrat_json_de_l_orchestration_reste_intact():
    """La raison d'être du `+` dans `_PROMPT_ORCHESTRATION`, écrite comme un fait.

    Ce prompt porte un gabarit JSON, donc des accolades littérales : passé en f-string
    pour y interpoler le registre, il les lirait comme des champs — soit une erreur à
    l'import, soit un contrat de réponse amputé. Le jour où quelqu'un « harmonise » les
    trois prompts en f-strings, c'est ce test qui le dit.
    """
    assert '{"verdict": "proposition|accord|echange", "objectif": "...", "reponse": "..."}' in (
        _PROMPT_ORCHESTRATION
    )


def test_un_cycle_de_fragments_leve_au_lieu_de_boucler(racine_jetable, monkeypatch):
    """La garde de la substitution récursive (#945).

    Un fragment peut désormais en appeler un autre — c'est ce qui permet au socle
    d'appeler `{{registre}}`. Le prix est qu'un cycle boucle jusqu'à la pile ; on le paie
    par une erreur franche, comme le marqueur inconnu juste à côté.
    """
    monkeypatch.setitem(pdc._MARQUEURS, "aller", "_aller")
    monkeypatch.setitem(pdc._MARQUEURS, "retour", "_retour")
    (racine_jetable / "_aller.md").write_text("{{retour}}", encoding="utf-8")
    (racine_jetable / "_retour.md").write_text("{{aller}}", encoding="utf-8")
    (racine_jetable / "essai.md").write_text("# Playbook\n\n{{aller}}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="fragment de playbook récursif"):
        playbook_du_code("essai")


def test_un_fragment_imbrique_est_bien_developpe(racine_jetable, monkeypatch):
    """Le pendant positif : sans cette descente, `{{registre}}` partirait **littéral**.

    C'est la panne que la garde « accolade mal formée » ne verrait pas — le marqueur est
    bien formé, simplement jamais substitué.
    """
    monkeypatch.setitem(pdc._MARQUEURS, "feuille", "_feuille")
    (racine_jetable / "_feuille.md").write_text("la feuille", encoding="utf-8")
    (racine_jetable / "_socle.md").write_text("le socle, puis {{feuille}}", encoding="utf-8")
    (racine_jetable / "essai.md").write_text("# Playbook\n\n{{socle}}\n", encoding="utf-8")

    assert playbook_du_code("essai") == "# Playbook\n\nle socle, puis la feuille"
    assert socle() == "le socle, puis la feuille"


@pytest.fixture
def racine_jetable(tmp_path, monkeypatch):
    """Déporte la lecture des documents sur un dossier vide, caches vidés de part et d'autre.

    Jumelle de celle de `tests/test_playbooks_defaut.py`, et pour la même raison : les
    trois lectures du module sont mémoïsées, donc un document bricolé pour un test
    d'erreur empoisonnerait les suivants s'il restait en cache.
    """
    _vide_les_caches()
    monkeypatch.setattr(pdc, "RACINE", tmp_path)
    yield tmp_path
    _vide_les_caches()


def _vide_les_caches() -> None:
    pdc.playbook_du_code.cache_clear()
    pdc.fragment.cache_clear()
    pdc.roles_du_code.cache_clear()
