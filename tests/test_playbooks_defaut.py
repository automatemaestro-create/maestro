"""Tests du **contenu** des playbooks « du code » (ticket #294, lot 5/5 du parent #293).

Là où [`tests/test_playbooks.py`](./test_playbooks.py) couvre le **stockage** versionné
(publication, historique, retour arrière, application à chaud), cette suite couvre la
**matière** : les documents Markdown livrés avec le paquet
(`maestro/agents/playbooks_defaut/<rôle>.md`, #295) et leur mise en service par les deux
chemins d'exécution.

Ce qu'elle tient, rôle par rôle :

① **une seule source** — le document du rôle est celui que sert `PLAYBOOK_DEFAUTS` *et*
   celui que porte le profil outillé (`RoleProfile.prompt_systeme`). L'invariant tient par
   construction depuis #295 ; il se teste parce que c'est précisément ce qui avait dérivé
   avant lui — une chaîne Python d'un côté, un document structuré dans docs/04 de l'autre ;
② **la structure** — les sections que docs/04 §1 décrit sont réellement là, et le titre
   porte le libellé de rôle du catalogue. Présence, jamais ordre : les lots #296/#297
   intercalent leurs propres sections (« Ce que tu tranches », « Exigences de qualité »…)
   et déplacent « Garde-fous » — c'est voulu, et ça ne doit pas casser ici ;
③ **le tronc commun sénior** (#293) — le socle est substitué dans chaque document, le cadre
   outillé aussi, et aucun marqueur `{{…}}` ne survit à la lecture ;
④ **les garde-fous propres au rôle** — QA n'écrit pas le livrable d'un autre, Designer
   propose la charte, BDD ne joue pas d'opération destructive, DevOps ne déploie pas,
   Développeur ne fusionne rien. C'est la frontière que le régime sénior (#293) ne devait
   **pas** élargir : on la vérifie dans la section « Garde-fous » du document, pas ailleurs
   dans la page, pour qu'une mention de passage ne suffise pas à faire passer le test ;
⑤ **les invariants de chargement**, rôle par rôle — un dépôt vide reproduit le comportement
   du code (#76), une version publiée prime toujours (#78), une proposition n'est jamais
   chargée (#111).

Les phrases attendues sont volontairement **courtes et sans apostrophe** : ce sont les
frontières du rôle, pas sa rédaction. Reformuler un garde-fou reste possible ; le supprimer
doit se voir.

Aucun appel réseau ni modèle : on lit des fichiers du paquet et un dépôt temporaire.
"""

import re

import pytest

from maestro.agents import TOOLED_PROFILES
from maestro.agents import playbook_du_code as pdc
from maestro.agents.catalog import DEFAULT_AGENTS
from maestro.agents.playbook_du_code import (
    CONSIGNE_RENDU_COMPTE,
    playbook_du_code,
    roles_du_code,
    socle,
)
from maestro.agents.playbooks import PLAYBOOK_DEFAUTS, PlaybookStore
from maestro.providers.base import PLAFOND_TOURS_DEFAUT

#: Les rôles à playbook, pris à la source plutôt que recopiés : ajouter un rôle au
#: catalogue le fait entrer dans toute cette suite sans y toucher.
ROLES = sorted(PLAYBOOK_DEFAUTS)

#: Les profils outillés indexés par nom de rôle (l'autre chemin d'exécution).
PROFILS = {profil.nom: profil for profil in TOOLED_PROFILES}

#: Les agents du catalogue indexés par nom (le chemin d'exécution texte).
AGENTS = {agent.nom: agent for agent in DEFAULT_AGENTS}

#: Les sections que docs/04 §1 pose comme structure d'un playbook. Un lot peut en
#: **ajouter** (les lots #296/#297 en ajoutent quatre) ; aucun ne doit en retirer.
SECTIONS_ATTENDUES = frozenset(
    {
        "Mission",
        "Entrées attendues",
        "Méthode",
        "Critères de « terminé »",
        "Garde-fous",
        "Format de sortie",
    }
)

#: Les garde-fous propres à chaque rôle, tels qu'ils doivent se lire dans la section
#: « Garde-fous » de son document. Ce sont les frontières que l'autonomie de #293
#: n'élargit pas : le réversible se tranche, ceci ne se tranche pas.
GARDE_FOUS: dict[str, tuple[str, ...]] = {
    "developpeur": (
        "Tu ne fusionnes rien",
        "aucune action destructrice hors de ton espace de travail",
    ),
    "bdd": (
        "Tu ne te connectes jamais à une base réelle ou de production",
        "destructive ou irréversible",
        "validation humaine",
    ),
    "devops": (
        "Tu ne déploies jamais vers un environnement réel",
        "ne modifies aucune infrastructure existante",
        "plafonds de ressources",
    ),
    "designer": (
        "tu proposes une évolution, tu ne la remplaces ni ne la réécris sans accord",
    ),
    "qa": (
        "tu ne les réécris pas",
        "Ne corrige jamais toi-même ce que tu évalues",
    ),
}

#: Un titre de section de niveau 2 dans un document de playbook.
_SECTION = re.compile(r"^## (.+)$", re.MULTILINE)


def _normalise(texte: str) -> str:
    """Le texte débarrassé de sa mise en forme Markdown et de ses retours à la ligne.

    Les documents sont enveloppés à ~95 colonnes et emphatisent au fil du texte : une
    phrase attendue y traverse une fin de ligne (`tu ne la\\n  remplaces`) et porte des
    `**` au milieu. Chercher la phrase brute échouerait sur la mise en page, pas sur le
    fond — on compare donc sur le texte normalisé.
    """
    sans_emphase = texte.replace("**", "").replace("`", "")
    return " ".join(sans_emphase.split())


def _sections(document: str) -> dict[str, str]:
    """Les sections `## …` du document, titre → corps (le corps peut être vide)."""
    titres = list(_SECTION.finditer(document))
    fins = [m.start() for m in titres[1:]] + [len(document)]
    return {
        m.group(1).strip(): document[m.end() : fin]
        for m, fin in zip(titres, fins, strict=True)
    }


@pytest.fixture()
def racine_jetable(tmp_path, monkeypatch):
    """Déporte la lecture des documents sur un dossier vide, caches vidés de part et d'autre.

    `playbook_du_code`, `fragment` et `roles_du_code` sont mémoïsés (`@cache`) : sans ce
    nettoyage au **retour**, un document bricolé pour un test d'erreur resterait en cache
    et empoisonnerait les suivants.
    """
    _vide_les_caches()
    monkeypatch.setattr(pdc, "RACINE", tmp_path)
    yield tmp_path
    _vide_les_caches()


def _vide_les_caches() -> None:
    """Vide les trois caches de lecture du module (l'ordre est indifférent)."""
    pdc.playbook_du_code.cache_clear()
    pdc.fragment.cache_clear()
    pdc.roles_du_code.cache_clear()


# --- ① Une seule source : le document, servi aux deux chemins d'exécution --------------


def test_les_roles_a_document_sont_ceux_du_catalogue():
    # Les cinq exécutants de docs/04 §2 — et la liste des agents éditables par l'API.
    assert set(ROLES) == {"developpeur", "bdd", "devops", "designer", "qa"}
    assert set(roles_du_code()) == set(ROLES) == set(AGENTS) == set(PROFILS)


@pytest.mark.parametrize("role", ROLES)
def test_playbook_defauts_sert_le_document_du_role(role):
    defaut = PLAYBOOK_DEFAUTS[role]

    assert defaut.agent == role
    assert defaut.role == AGENTS[role].role
    # Le repli exposé par le dépôt EST le document lu sur disque, sans retouche.
    assert defaut.contenu == playbook_du_code(role)
    # Et il s'annonce comme le playbook du rôle, sous le libellé du catalogue.
    assert defaut.contenu.startswith(f"# Playbook — {defaut.role}\n")


@pytest.mark.parametrize("role", ROLES)
def test_le_runtime_outille_porte_le_meme_document(role):
    # L'invariant que #295 a rendu structurel : plus de version dégradée d'un côté et
    # structurée de l'autre — le profil outillé et PLAYBOOK_DEFAUTS lisent le même fichier.
    assert PROFILS[role].prompt_systeme == PLAYBOOK_DEFAUTS[role].contenu


# --- ② La structure annoncée par docs/04 §1 -------------------------------------------


@pytest.mark.parametrize("role", ROLES)
def test_le_document_porte_les_sections_structurantes(role):
    presentes = set(_sections(PLAYBOOK_DEFAUTS[role].contenu))

    # Sous-ensemble, jamais égalité ni ordre : un lot peut enrichir un playbook de ses
    # propres sections (« Ce que tu tranches »…) et déplacer « Garde-fous ».
    manquantes = SECTIONS_ATTENDUES - presentes
    assert not manquantes, f"sections absentes du playbook {role} : {sorted(manquantes)}"


@pytest.mark.parametrize("role", ROLES)
def test_chaque_section_du_document_a_un_corps(role):
    sections = _sections(PLAYBOOK_DEFAUTS[role].contenu)
    vides = [titre for titre, corps in sections.items() if not corps.strip()]

    assert not vides, f"sections sans contenu dans le playbook {role} : {vides}"


# --- ③ Le tronc commun sénior (#293), substitué et complet ----------------------------


@pytest.mark.parametrize("role", ROLES)
def test_le_document_inclut_le_tronc_commun_senior(role):
    contenu = PLAYBOOK_DEFAUTS[role].contenu

    # Le socle entier, pas une paraphrase : c'est ce qui garantit le même régime partout.
    assert socle() in contenu
    # Le cadre d'exécution outillée aussi — ce chemin-là a des outils et un répertoire.
    assert pdc.fragment(pdc.FRAGMENT_CADRE) in contenu
    # Et rien ne reste à substituer : un prompt système servi avec un trou est une panne.
    assert "{{" not in contenu and "}}" not in contenu


def test_le_socle_porte_le_regime_senior_en_entier():
    texte = _normalise(socle())

    # Les trois volets du régime (#293) : ce qui se tranche, ce qui remonte, ce qui se rend.
    assert "Ce que tu décides seul" in texte
    assert "Ce que tu remontes au lieu de le décider" in texte
    assert "Ce que tu rends" in texte
    # La latitude est explicite, et elle porte sur le réversible.
    assert "sans demander d accord" in texte.replace("'", " ").replace("’", " ")
    assert "irréversible ou destructive" in texte
    # Le compte-rendu attendu, nommé — c'est lui que la consigne finale répète.
    assert "Décisions & arbitrages" in texte
    assert "Recommandations" in texte


@pytest.mark.parametrize("role", ROLES)
def test_la_consigne_finale_du_profil_repete_le_rendu_de_compte(role):
    # Le prompt système cadre le rôle ; la consigne finale cadre le livrable, et c'est
    # elle que l'agent relit juste avant de conclure. La clause y est répétée à dessein.
    assert CONSIGNE_RENDU_COMPTE in PROFILS[role].consigne_finale


# --- ④ Les garde-fous propres au rôle, dans la section qui les porte ------------------


@pytest.mark.parametrize("role", ROLES)
def test_les_garde_fous_du_role_sont_dans_la_section_garde_fous(role):
    section = _normalise(_sections(PLAYBOOK_DEFAUTS[role].contenu)["Garde-fous"])

    absents = [phrase for phrase in GARDE_FOUS[role] if phrase not in section]
    assert not absents, f"garde-fous perdus dans le playbook {role} : {absents}"


@pytest.mark.parametrize("role", ROLES)
def test_le_prompt_texte_du_catalogue_porte_le_regime_et_des_garde_fous(role):
    prompt = AGENTS[role].prompt_systeme

    # L'exécution texte prend le régime sénior **sans** le cadre outillé : elle n'a ni
    # outils ni répertoire de travail, sa réponse *est* le livrable.
    assert socle() in prompt
    assert pdc.fragment(pdc.FRAGMENT_CADRE) not in prompt
    # Elle porte ses propres garde-fous, condensés — non vides, et suivis du rendu de compte.
    garde_fous = prompt.split("Garde-fous :", 1)[1]
    assert garde_fous.strip()
    assert "Décisions & arbitrages" in garde_fous and "Recommandations" in garde_fous


def test_les_plafonds_de_tours_restent_bornes():
    # #239 : chaque rôle porte sa borne anti-emballement, jamais illimitée. Le Designer
    # est le seul à sortir du défaut (sa boucle rendre/regarder/reprendre coûte ~7× le tour).
    plafonds = {nom: profil.plafond_tours for nom, profil in PROFILS.items()}

    assert all(plafond > 0 for plafond in plafonds.values())
    assert plafonds["designer"] > PLAFOND_TOURS_DEFAUT
    assert {nom for nom, p in plafonds.items() if p == PLAFOND_TOURS_DEFAUT} == set(ROLES) - {
        "designer"
    }


# --- ⑤ Invariants de chargement, rôle par rôle (#76, #78, #111) -----------------------


@pytest.mark.parametrize("role", ROLES)
def test_un_depot_vide_reproduit_le_comportement_du_code(role, tmp_path):
    depot = PlaybookStore(tmp_path / "vide")

    assert depot.lire(role) is None
    # Chaque chemin garde exactement son prompt du code : le document pour le runtime
    # outillé, le prompt condensé du catalogue pour l'exécution texte.
    document = PLAYBOOK_DEFAUTS[role].contenu
    assert depot.prompt_systeme(role, document) == document
    assert depot.prompt_systeme(role, AGENTS[role].prompt_systeme) == AGENTS[role].prompt_systeme


@pytest.mark.parametrize("role", ROLES)
def test_une_version_publiee_prime_sur_le_document_du_code(role, tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    depot.ecrire(role, "Playbook publié.")

    # Le repli ne sert plus, quel que soit le chemin qui le propose.
    assert depot.prompt_systeme(role, PLAYBOOK_DEFAUTS[role].contenu) == "Playbook publié."
    assert depot.prompt_systeme(role, AGENTS[role].prompt_systeme) == "Playbook publié."


@pytest.mark.parametrize("role", ROLES)
def test_une_proposition_ne_masque_jamais_le_document_du_code(role, tmp_path):
    depot = PlaybookStore(tmp_path / "pb")
    depot.proposer(role, "Brouillon proposé.", justification="2 échecs")

    # #111 : un brouillon vit hors de la numérotation des versions — le moteur, qui ne
    # passe que par lire()/numeros(), reste sur le playbook du code.
    assert depot.numeros(role) == () and depot.lire(role) is None
    assert depot.numeros_propositions(role) == (1,)
    assert (
        depot.prompt_systeme(role, PLAYBOOK_DEFAUTS[role].contenu)
        == PLAYBOOK_DEFAUTS[role].contenu
    )


# --- Les pannes de lecture sont franches (contrat de `playbook_du_code`) --------------


def test_un_document_absent_leve_a_la_lecture(racine_jetable):
    # Mieux vaut un import qui échoue qu'un rôle servi sans playbook.
    with pytest.raises(FileNotFoundError, match="playbook du code introuvable"):
        playbook_du_code("fantome")


def test_un_marqueur_inconnu_leve(racine_jetable):
    (racine_jetable / "essai.md").write_text("# Playbook\n\n{{inexistant}}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="marqueur de playbook inconnu"):
        playbook_du_code("essai")


def test_une_accolade_double_laissee_dans_le_texte_leve(racine_jetable):
    # Un marqueur mal fermé ne matche pas la substitution : il ne doit pas partir tel quel.
    (racine_jetable / "essai.md").write_text("# Playbook\n\n{{ socle\n", encoding="utf-8")

    with pytest.raises(ValueError, match="marqueur mal formé"):
        playbook_du_code("essai")


def test_les_fragments_partages_ne_sont_pas_des_roles(racine_jetable):
    for nom in ("_socle.md", "_cadre_outille.md", "developpeur.md"):
        (racine_jetable / nom).write_text("# Playbook\n\ncorps\n", encoding="utf-8")

    # Les fragments préfixés d'un souligné n'ont pas de rôle : ils ne s'exposent pas.
    assert roles_du_code() == ("developpeur",)
