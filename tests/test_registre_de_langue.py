"""Un seul registre de langue dans toute l'interface, agents compris (#945, #939).

Constat **C5** du [retex du 2026-09-11](../docs/retex/2026-09-11-premiere-session-utilisateur.md) :
l'assistant et l'orchestration **tutoyaient** l'utilisateur (« Je te propose », « Ce que je peux
te dire ») quand tout le reste du produit **vouvoie** — y compris le message d'accueil du chat,
deux lignes au-dessus. La cause n'était pas une phrase mal écrite : **aucun prompt ne disait le
registre**, et un modèle rend alors celui dans lequel on s'adresse à lui.

⚠ **Le constat G6 du même retex a la même forme, et vit donc au même endroit** (#939) : ce que le
produit dit à son utilisateur parlait du **dépôt** — une commande `bash scripts/…`, des numéros de
tickets, des `docs/*.md` cités en sources, un renvoi vers `/orchestrate --status`, « une commande
du workflow de développement qui n'existe pas pour un utilisateur ». La part *rendue par un agent*
n'était, là encore, prescrite nulle part. Elle l'est désormais dans le même fragment, pour la même
raison : deux endroits finiraient par dire deux choses. La part *écrite dans le front* est gardée
de l'autre côté, par `apps/web/tests/langue-du-produit.test.ts` — deux moitiés, deux filets, un
seul texte de règle de chaque côté.

Ce que cette suite garde tient en une phrase : **le registre existe une fois, et tout ce que le
produit sert le porte**. Les deux moitiés comptent autant —

- *une fois* : `_registre.md` est la source, et le socle l'appelle, si bien qu'un rôle neuf ne
  peut pas l'oublier (il porte déjà `{{socle}}`). Trois phrases écrites à trois endroits
  finiraient par en faire trois registres, ce qui est exactement le défaut d'origine ;
- *tout* : les deux chemins d'exécution des rôles (texte du catalogue, playbook outillé) **et**
  les prompts conversationnels de la Control Tower, qui ne passent par aucun playbook.

⚠ **« Tout » ne se tient pas par une liste qu'on complète** (#1261). Le récit de fin d'un run
(#1224) est arrivé après cette suite, a parlé à la personne, et l'a tutoyée : la liste des
prompts conversationnels ne le comptait pas, et rien ne l'obligeait à le compter. Ce qui
l'oblige désormais est un balayage des **appels au modèle** de la Control Tower : chacun y est
inscrit, soit avec le prompt conversationnel qu'il sert, soit avec la raison pour laquelle ce
qu'il fait écrire ne s'adresse pas à la personne. Un appel neuf non inscrit fait rougir — la
question se pose donc au moment où l'on écrit l'appel, pas au retex suivant.

⚠ Ce qui est vérifié ici est que la **consigne** est servie, jamais ce qu'un modèle en fait : le
registre d'une réponse est du jugement, pas de la plomberie (même partage que l'aveu d'ignorance
de #748). On peut le lui demander sans ambiguïté, et c'est tout ce qui se teste.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

import pytest

from maestro.agents import playbook_du_code as pdc
from maestro.agents.catalog import GABARITS_DU_CODE
from maestro.agents.playbook_du_code import playbook_du_code, registre, roles_du_code, socle
from maestro.controltower.assistance import _PROMPT_ASSISTANCE
from maestro.controltower.chat import _CADRE_CONVERSATION
from maestro.controltower.generation_agent import _CADRE_GENERATION
from maestro.controltower.orchestration import _PROMPT_ORCHESTRATION, _PROMPT_REDACTION
from maestro.controltower.outillage import _PROMPT_COMPREHENSION
from maestro.controltower.recit import SYSTEME as _SYSTEME_RECIT
from maestro.equipe.composition import CADRE_COMPOSITION
from maestro.providers.base import ModelProvider

#: Les prompts **conversationnels** de la Control Tower : ceux qui parlent à un humain sans
#: passer par un playbook de rôle, donc sans socle pour leur porter le registre.
CONVERSATIONNELS = {
    "assistant (#123)": _PROMPT_ASSISTANCE,
    "orchestration (#685)": _PROMPT_ORCHESTRATION,
    "cadre de conversation d'un agent (#85)": _CADRE_CONVERSATION,
    "récit de fin d'un run (#1224)": _SYSTEME_RECIT,
    "parole du fil sur un geste (#1262)": _PROMPT_REDACTION,
    # Les raisons des rôles et la réponse à une correction sont lues telles quelles
    # à l'étape d'équipe (#1159).
    "composition d'équipe (#1159)": CADRE_COMPOSITION,
    # Ses intitulés, ses options et leurs raisons s'affichent tels quels sur la carte
    # de la question d'outillage : ils parlent à la personne.
    "compréhension d'un projet neuf (#1147)": _PROMPT_COMPREHENSION,
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


def test_le_registre_interdit_de_parler_du_depot():
    """G6 : ce qu'un agent écrit ne renvoie pas vers ce que l'utilisateur n'a pas (#939).

    Les quatre familles relevées par le retex, et **pas** une phrase qui les
    résumerait : chacune a été observée à l'écran, et chacune se perd différemment.
    On vérifie que la consigne les nomme, jamais ce qu'un modèle en fait — même
    partage qu'au vouvoiement ci-dessus.
    """
    texte = registre()
    assert "scripts/" in texte, "la consigne ne nomme plus les commandes du dépôt"
    assert "docs/" in texte, "la consigne ne nomme plus les fichiers de documentation"
    assert "/orchestrate --status" in texte, "la consigne ne nomme plus le renvoi de G6"
    assert "#481" in texte, "la consigne ne nomme plus les numéros de tickets internes"


def test_le_registre_dit_par_quoi_remplacer():
    """Le second critère du ticket, prescrit et non espéré (#939).

    « Ce qui remplace dit **quoi faire**, pas seulement autre chose. » Une consigne
    qui se contenterait d'interdire ferait taire l'agent là où il doit orienter :
    elle doit donc nommer l'interface comme le lieu du geste, et autoriser l'aveu
    quand le geste n'y est pas — sans quoi le modèle comblerait, ce qui est
    exactement comment `/orchestrate --status` est arrivé à l'écran.
    """
    texte = registre()
    assert "nomme cet endroit" in texte
    assert "dis franchement qu'il n'y est pas" in texte


def test_le_registre_laisse_le_projet_de_l_utilisateur_au_developpeur():
    """La garde de la garde : l'interdit vise **Maestro**, jamais le projet traité.

    Un agent qui livre du code nomme des fichiers, donne des commandes et cite des
    chemins — c'est son travail. Sans cette distinction écrite, la consigne
    précédente lui ferait rendre des comptes-rendus sans aucun repère, et c'est le
    genre de dégât qu'un prompt fait en silence.
    """
    texte = registre()
    assert "projet de l'utilisateur" in texte


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


@pytest.mark.parametrize("agent", GABARITS_DU_CODE, ids=lambda a: a.nom)
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


# --- Aucun appel au modèle n'échappe à la question (#1261) ----------------------------
#
# `CONVERSATIONNELS` garde ce qu'il nomme, et ne peut rien pour ce qu'il ne nomme pas : c'est
# ainsi que le récit de fin (#1224) a tutoyé la personne dans un fil qui la vouvoie. Le filet
# part donc de l'autre bout — de chaque endroit où la Control Tower **fait écrire un modèle** —
# et exige que chacun ait été rangé d'un côté ou de l'autre.

#: Les appels au modèle qui font écrire **à la personne**, chacun avec le prompt de
#: `CONVERSATIONNELS` qu'il sert. Clé : `<module>::<fonction englobante>`.
APPELS_CONVERSATIONNELS = {
    "assistance_documentee.py::RepondeurAssistanceDocumentee._appeler": "assistant (#123)",
    "chat.py::RepondeurModele.repondre": "cadre de conversation d'un agent (#85)",
    "chat.py::RepondeurModele.produire": "cadre de conversation d'un agent (#85)",
    "orchestration.py::RepondeurOrchestration._juger": "orchestration (#685)",
    "orchestration.py::RepondeurOrchestration.rediger": "parole du fil sur un geste (#1262)",
    "outillage.py::ComprehensionModele.comprendre": "compréhension d'un projet neuf (#1147)",
    "recit.py::RedacteurModele.rediger": "récit de fin d'un run (#1224)",
    "equipe.py::CompositeurEquipe.ecrire": "composition d'équipe (#1159)",
}

#: Les appels au modèle dont ce qu'ils font écrire ne s'adresse **pas** à la personne, chacun
#: avec sa raison. Une entrée de plus se justifie, elle ne s'ajoute pas : si la réponse du
#: modèle finit dans un fil, sous les yeux de quelqu'un, l'appel est conversationnel.
APPELS_HORS_CONVERSATION = {
    "orchestration.py::RepondeurOrchestration._demandes": (
        "le tour de lecture (#1223) : des lignes de demande ou `RIEN`, que le code exécute "
        "et que personne ne lit"
    ),
    "generation_agent.py::GenerateurDefinitionAgent._generer": (
        "écrit le playbook d'un agent, qui prescrit lui-même le registre "
        "(`test_le_generateur_prescrit_le_registre_au_playbook_qu_il_ecrit`)"
    ),
    "auto_amelioration.py::_AppelModele._generer": (
        "réécrit un playbook — un document proposé en brouillon, pas une réponse dans un fil"
    ),
}


def _points_d_entree_du_modele() -> frozenset[str]:
    """Les méthodes du fournisseur qui font écrire un modèle sous un prompt système.

    **Dérivées** de `ModelProvider`, jamais listées : une méthode d'appel ajoutée à la
    frontière entre d'office dans le balayage, et c'est la frontière qui dit ce qu'est un
    appel au modèle — pas ce test.
    """
    return frozenset(
        nom
        for nom, membre in inspect.getmembers(ModelProvider, inspect.isfunction)
        if "system_prompt" in inspect.signature(membre).parameters
    )


def _appels_au_modele(source: str, points: frozenset[str]) -> set[str]:
    """Les fonctions d'un module qui appellent le modèle, en `Classe.methode` englobante.

    L'**AST**, pas les lignes : un `generate(` de docstring ou de commentaire n'appelle
    rien. La clé est la fonction englobante et non la ligne, qui bouge à chaque retouche du
    module ; deux appels dans la même fonction (le bloc et le flux d'un même répondeur) y
    servent le même prompt, et ne font qu'une entrée.
    """
    trouves: set[str] = set()

    def descendre(noeud: ast.AST, chemin: tuple[str, ...]) -> None:
        for enfant in ast.iter_child_nodes(noeud):
            if isinstance(enfant, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                descendre(enfant, (*chemin, enfant.name))
                continue
            if (
                isinstance(enfant, ast.Call)
                and isinstance(enfant.func, ast.Attribute)
                and enfant.func.attr in points
            ):
                trouves.add(".".join(chemin) or "<module>")
            descendre(enfant, chemin)

    descendre(ast.parse(source), ())
    return trouves


def _appels_de_la_control_tower() -> set[str]:
    points = _points_d_entree_du_modele()
    racine = _RACINE / "maestro" / "controltower"
    return {
        f"{chemin.relative_to(racine).as_posix()}::{fonction}"
        for chemin in sorted(racine.rglob("*.py"))
        for fonction in _appels_au_modele(chemin.read_text(encoding="utf-8"), points)
    }


def test_le_balayage_connait_les_points_d_entree_du_modele():
    # Si la dérivation ne trouvait plus rien, le balayage passerait à vide — et en silence.
    assert {"generate", "generate_stream", "run_agent"} <= _points_d_entree_du_modele()


def test_le_balayage_voit_un_appel_au_modele_et_ignore_sa_mention():
    """Le motif prouvé sur un échantillon fautif avant de balayer le dépôt.

    Le fautif est la forme exacte du défaut de #1261 : une méthode de rédacteur qui fait
    écrire le modèle. L'innocent en a les mots — dans une docstring, un commentaire, une
    chaîne — sans appeler quoi que ce soit.
    """
    fautif = (
        "class Redacteur:\n"
        "    async def rediger(self, provider):\n"
        "        return await provider.generate('x', model='m', system_prompt=SYSTEME)\n"
        "async def libre(p):\n"
        "    async for m in p.generate_stream('x', model='m'):\n"
        "        pass\n"
    )
    innocent = (
        "class Redacteur:\n"
        "    async def rediger(self, provider):\n"
        "        '''Appelle provider.generate(prompt, system_prompt=SYSTEME).'''\n"
        "        # provider.generate(...) plus tard\n"
        "        return 'generate'\n"
    )
    points = _points_d_entree_du_modele()
    assert _appels_au_modele(fautif, points) == {"Redacteur.rediger", "libre"}
    assert _appels_au_modele(innocent, points) == set()


def test_chaque_appel_au_modele_de_la_control_tower_est_range():
    """Tout endroit qui fait écrire un modèle dit s'il parle à la personne (#1261).

    Dans les deux sens : un appel **neuf** non rangé fait rougir, et une entrée qui ne
    correspond plus à aucun appel aussi — une inscription qui survit à son appel
    protège autre chose que ce qu'elle nomme.
    """
    ranges = set(APPELS_CONVERSATIONNELS) | set(APPELS_HORS_CONVERSATION)
    trouves = _appels_de_la_control_tower()
    neufs = sorted(trouves - ranges)
    assert not neufs, (
        "appel au modèle non rangé — s'il fait écrire à la personne, son prompt sert "
        "`registre()` et il entre dans APPELS_CONVERSATIONNELS ; sinon, sa raison entre "
        "dans APPELS_HORS_CONVERSATION :\n" + "\n".join(neufs)
    )
    assert not sorted(ranges - trouves), "entrée sans appel : " + ", ".join(
        sorted(ranges - trouves)
    )


def test_un_appel_ne_se_range_pas_des_deux_cotes():
    assert not set(APPELS_CONVERSATIONNELS) & set(APPELS_HORS_CONVERSATION)


@pytest.mark.parametrize("appel", sorted(APPELS_CONVERSATIONNELS))
def test_chaque_appel_conversationnel_sert_un_prompt_garde(appel):
    # Le lien qui fait de l'inventaire autre chose qu'une liste de noms : l'appel nomme le
    # prompt, et le prompt est éprouvé plus haut (`test_chaque_prompt_conversationnel_…`).
    assert APPELS_CONVERSATIONNELS[appel] in CONVERSATIONNELS


# --- Ce que la composition ne doit pas casser -----------------------------------------


def test_le_contrat_json_de_l_orchestration_reste_intact():
    """La raison d'être du `+` dans `_PROMPT_ORCHESTRATION`, écrite comme un fait.

    Ce prompt porte un gabarit JSON, donc des accolades littérales : passé en f-string
    pour y interpoler le registre, il les lirait comme des champs — soit une erreur à
    l'import, soit un contrat de réponse amputé. Le jour où quelqu'un « harmonise » les
    trois prompts en f-strings, c'est ce test qui le dit.
    """
    # Le quatrième verdict, `projet`, est venu avec #1294 : un projet qui naît dans la
    # conversation. Son objet (`"projet": {…}`) porte d'autres accolades littérales,
    # que le même `+` protège.
    assert (
        '%%MAESTRO%% {"verdict": "proposition|accord|echange|projet", "objectif": "..."}'
        in _PROMPT_ORCHESTRATION
    )
    assert '"raisons": {"nom": "...", "dossier": "...", "versionnement": "..."}' in (
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


# --- L'autre porteur : ce que le backend écrit en toutes lettres (#939) ----------------
#
# Le registre prescrit à un **modèle** ce qu'il ne doit pas dire ; il ne peut rien
# contre une chaîne que le code écrit lui-même et que l'API sert telle quelle. Or c'est
# par là que plusieurs fuites de G6 sont arrivées à l'écran : la description d'une
# entrée du catalogue MCP (« dans ce dépôt »), un message d'erreur, la raison d'un rôle
# écarté. Ce balayage est cette moitié-là — le pendant Python d'
# `apps/web/tests/langue-du-produit.test.ts`, qui tient la même règle sur le front.

#: Le renvoi interne dans une chaîne servie : un numéro de ticket entre parenthèses ou
#: suivi d'une ponctuation, un chemin de script, un document numéroté ou nommé du dépôt.
_RENVOI_INTERNE = re.compile(r"\(#\d{2,4}\b|#\d{2,4}[),.]|\bscripts/|\bdocs/\d|\bdocs/[\w.-]+\.md")

#: Ce que le retex du 2026-09-11 a lu, et ce qui lui ressemble sans en être. Le motif se
#: prouve avant de balayer : une sonde écrite sur un dépôt déjà propre ne prouve rien,
#: et c'est ainsi qu'un balayage finit par ne plus rien voir.
_FAUTIFS = (
    "C'est le serveur derrière `chrome-maestro` dans ce dépôt (docs/19).",
    "Erreur simulée par le scénario « erreur » de la démo (#978).",
    "playbook écrit pour ce projet à partir de l'intention ci-dessous (#257) ;",
    "réinstaller les dépendances (bash scripts/setup.sh --only python).",
    "une fourchette, sourcée de docs/09",
)
_INNOCENTS = (
    "Rien encore sur ce projet — lancez une exécution pour le remplir.",
    "La clé d'API du fournisseur : write-only, jamais renvoyée en clair.",
    "le canal #general de votre espace Slack",  # un salon, pas un ticket
    "POST /api/mcp/admissions",
)

#: Les modules dont les chaînes s'adressent à quelqu'un **dans un terminal**, pas à
#: l'interface : ils nomment donc légitimement une commande du dépôt. Chacun est ici
#: avec sa raison, et la liste est courte — un module de plus se justifie, il ne
#: s'ajoute pas.
_HORS_INTERFACE = {
    "maestro/controltower/cli.py": "la ligne de commande de la Control Tower",
    "maestro/controltower/purge.py": "la purge, jouée depuis un terminal",
    "maestro/engine/cli.py": "la ligne de commande du moteur (`maestro-run`)",
    # Même raison que la purge, et le même geste : le banc des scénarios (#1148) est
    # joué dans un terminal, et son refus nomme la commande qui allume la stack.
    # Taire `scripts/controltower/start.sh` laisserait « l'API ne répond pas » sans
    # suite, sur le seul écart que ce banc ne peut pas réparer lui-même.
    "maestro/scenarios/banc.py": "le banc des scénarios de référence, joué en terminal",
    # Le préflight et les gestes de l'état du banc (#1164) : joués par `start.sh`, dont
    # ils remontent la sortie au terminal. Un refus (« rien à rouvrir », « quelque chose
    # vit sur le banc ») n'a de suite que s'il nomme le geste qui débloque.
    "maestro/scenarios/etat.py": "l'état du banc, joué par le lanceur en terminal",
}

#: Le champ du registre MCP qui porte, pour une poignée d'entrées, un pointeur relatif
#: au dépôt à l'usage de qui contribue. Il n'atteint plus l'écran depuis #939 — la
#: bibliothèque ne rend que les procédures en `https://` — et un test de
#: `test_mcp_registry.py` exige que **chaque** entrée en porte une : le vider ici
#: reviendrait à faire échouer l'un pour satisfaire l'autre.
_CHAMP_POUR_LE_DEPOT = "procedure_url"

_RACINE = Path(__file__).resolve().parents[1]


def _chaines_servies(source: str) -> list[tuple[int, str]]:
    """Les littéraux de chaîne d'un module qui peuvent voyager vers l'écran.

    Lire l'**AST** plutôt que les lignes est ce qui rend le balayage juste : un `#123`
    de commentaire ou de docstring parle à qui relit le code, jamais à l'utilisateur, et
    un balayage textuel les confondrait — il crierait alors sur toute la prose du dépôt,
    et on cesserait de le lire.
    """
    arbre = ast.parse(source)
    hors: set[int] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            corps = noeud.body
            premier = corps[0] if corps else None
            if (
                isinstance(premier, ast.Expr)
                and isinstance(premier.value, ast.Constant)
                and isinstance(premier.value.value, str)
            ):
                hors.add(id(premier.value))
        if isinstance(noeud, ast.Call):
            for mot in noeud.keywords:
                if mot.arg == _CHAMP_POUR_LE_DEPOT and isinstance(mot.value, ast.Constant):
                    hors.add(id(mot.value))
    return [
        (noeud.lineno, noeud.value)
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Constant)
        and isinstance(noeud.value, str)
        and id(noeud) not in hors
    ]


@pytest.mark.parametrize("extrait", _FAUTIFS)
def test_le_motif_reconnait_ce_que_le_retex_a_lu(extrait):
    assert _RENVOI_INTERNE.search(extrait), extrait


@pytest.mark.parametrize("extrait", _INNOCENTS)
def test_le_motif_ne_crie_pas_sur_ce_qui_lui_ressemble(extrait):
    assert _RENVOI_INTERNE.search(extrait) is None, extrait


def test_aucune_chaine_servie_ne_renvoie_au_depot():
    """Ce que le backend écrit à l'utilisateur ne nomme ni ticket, ni script, ni doc.

    Le pendant du balayage front, sur l'autre moitié du produit. L'exemption se lit
    dans `_HORS_INTERFACE`, module par module et avec sa raison : une chaîne qui
    s'adresse à un terminal a le droit de nommer une commande, puisque celui qui la lit
    en a un sous les doigts.
    """
    fautifs: list[str] = []
    for chemin in sorted((_RACINE / "maestro").rglob("*.py")):
        relatif = chemin.relative_to(_RACINE).as_posix()
        if relatif in _HORS_INTERFACE:
            continue
        for ligne, texte in _chaines_servies(chemin.read_text(encoding="utf-8")):
            if _RENVOI_INTERNE.search(texte):
                fautifs.append(f"{relatif}:{ligne}: {' '.join(texte.split())[:120]}")
    assert not fautifs, "le produit parle son dépôt :\n" + "\n".join(fautifs)


@pytest.mark.parametrize("module", sorted(_HORS_INTERFACE))
def test_chaque_module_exempte_existe_encore(module):
    """Une exemption qui survit à son module protège autre chose que ce qu'elle nomme."""
    assert (_RACINE / module).is_file(), f"{module} — {_HORS_INTERFACE[module]}"
