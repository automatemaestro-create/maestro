"""Le retour d'expérience utilisateur : la commande, la purge et la frontière des écrans (#853).

Trois choses sont gardées ici, et aucune ne demande un Redis, un navigateur ni un compte de forge :

- **la frontière écrans ↔ `navigation.ts`** — la liste des écrans que `/retex-utilisateur` fait
  parcourir est confrontée à `PAGES` (le menu, puis les pages hors menu). Une liste recopiée dérive
  au premier écran ajouté : c'est la garde de `tests/test_design_veille.py` sur les routes,
  reprise ici sur le menu, et **dans les deux sens** — un écran retiré du menu laisserait sinon dans
  le prompt un parcours vers une page qui n'existe plus ;
- **la purge, sans Redis réel** — un client factice qui tient les clés qu'on lui donne, comme le
  `docker` neutralisé du filet CI local : `--check` n'écrit rien, le refus tombe quand l'API répond
  ou qu'un hôte bat encore, les clés et les dossiers sont ceux des constantes (aucun littéral
  `maestro.` en dur dans le module hors docstring), la configuration et les dossiers de projet
  restent intacts, `--projets` ne retire que les `.json` ;
- **l'absence d'écriture forge** — la commande vit sous `.claude/commands/`, où
  `tests/test_cycle_de_vie.py` et `tests/test_collaboration.py` balaient déjà ; ce module ajoute ce
  que ces balayages ne posent pas : aucun `gh` prescrit, aucune forme qui crée.

⚠ **Chaque contrôle qui conclut d'une absence porte son contre-exemple** — méthode de
`tests/contraste.test.ts` (#534) et de `tests/test_milestone_bilan.py` : un motif mal branché rend
un ✓ sur une question jamais posée. Les deux parseurs de la frontière et le détecteur de littéraux
sont donc **éprouvés sur un échantillon fautif** avant de balayer le dépôt.
"""

from __future__ import annotations

import ast
import io
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from fnmatch import fnmatch
from pathlib import Path

import pytest

from maestro.config import Settings
from maestro.controltower import purge
from maestro.controltower.battement import CLE_BATTEMENTS, horodatage_battement
from maestro.controltower.persistence import CLE_JOURNAL_EVENEMENTS
from maestro.messaging.mailbox import CANAL_BOITE_PREFIXE, CANAL_DIFFUSION
from maestro.queue.celery_app import FILE_TACHES

RACINE = Path(__file__).resolve().parents[1]
COMMANDES = RACINE / ".claude" / "commands"
COMMANDE = COMMANDES / "retex-utilisateur.md"
NAVIGATION = RACINE / "apps" / "web" / "lib" / "navigation.ts"
MODULE_PURGE = RACINE / "maestro" / "controltower" / "purge.py"
CLAUDE_MD = RACINE / "CLAUDE.md"
DOC_WORKFLOW = RACINE / "docs" / "10-workflow-git.md"
DOC_RETEX = RACINE / "docs" / "retex" / "README.md"


def prose(chemin: Path) -> str:
    """Le texte d'un fichier, blancs repliés — pour asserter sur une phrase, pas sur sa coupe."""
    return re.sub(r"\s+", " ", chemin.read_text(encoding="utf-8"))


def blocs_de_code(chemin: Path) -> list[str]:
    """Les blocs de code d'un prompt — c'est là que vit une commande QU'IL JOUE."""
    texte = chemin.read_text(encoding="utf-8")
    return re.findall(r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```", texte, re.M | re.S)


def en_tete(chemin: Path) -> str:
    """Le frontmatter d'un prompt — entre les deux premiers `---`."""
    return chemin.read_text(encoding="utf-8").split("---")[1]


# =================================================================================================
# La frontière : les écrans du prompt sont ceux de `PAGES`
# =================================================================================================

_ENTREE_TS = re.compile(r'\{\s*href:\s*"([^"]+)",\s*libelle:\s*"([^"]+)"')
_LIGNE_TABLE = re.compile(r"^\s*\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*$", re.M)


def ecrans_de_navigation(texte: str) -> dict[str, str]:
    """`href → libellé` des deux listes que `PAGES` concatène : `MENU` puis `HORS_MENU`."""
    ecrans: dict[str, str] = {}
    for nom in ("MENU", "HORS_MENU"):
        bloc = re.search(rf"export const {nom}: EntreeMenu\[\] = \[(.*?)\];", texte, re.S)
        assert bloc, f"liste {nom} introuvable dans navigation.ts"
        for href, libelle in _ENTREE_TS.findall(bloc.group(1)):
            ecrans[href] = libelle
    return ecrans


def ecrans_du_prompt(texte: str) -> dict[str, str]:
    """`href → libellé` de la table que le prompt encadre entre ses deux marqueurs."""
    bloc = re.search(r"<!-- ecrans:debut -->(.*?)<!-- ecrans:fin -->", texte, re.S)
    assert bloc, "marqueurs <!-- ecrans:debut/fin --> introuvables dans le prompt"
    # Le libellé peut porter une parenthèse explicative (« Projets (hors menu — …) ») :
    # seul le libellé du menu est confronté.
    return {
        href: libelle.split(" (")[0].strip()
        for href, libelle in _LIGNE_TABLE.findall(bloc.group(1))
    }


_NAVIGATION_FAUTIVE = """
export const MENU: EntreeMenu[] = [
  { href: "/", libelle: "Tableau de bord", Icone: IconeTableauDeBord },
  { href: "/nouveau", libelle: "Nouveau", Icone: IconeNouveau },
];
export const HORS_MENU: EntreeMenu[] = [
  { href: "/projets", libelle: "Projets", Icone: IconeProjets },
];
"""

_PROMPT_FAUTIF = """
   <!-- ecrans:debut -->
   | chemin | écran |
   | --- | --- |
   | `/` | Tableau de bord |
   | `/projets` | Projets (hors menu) |
   | `/disparu` | Disparu |
   <!-- ecrans:fin -->
"""


def test_les_deux_parseurs_voient_un_ecart_dans_les_deux_sens() -> None:
    """Le contre-exemple : un écran ajouté au menu et absent du prompt, un écran du prompt qui
    n'existe plus. Sans cette preuve, deux parseurs qui rendraient `{}` tomberaient d'accord."""
    navigation = ecrans_de_navigation(_NAVIGATION_FAUTIVE)
    prompt = ecrans_du_prompt(_PROMPT_FAUTIF)
    assert navigation == {"/": "Tableau de bord", "/nouveau": "Nouveau", "/projets": "Projets"}
    assert prompt == {"/": "Tableau de bord", "/projets": "Projets", "/disparu": "Disparu"}
    assert set(navigation) - set(prompt) == {"/nouveau"}, "un écran ajouté est vu"
    assert set(prompt) - set(navigation) == {"/disparu"}, "un écran disparu est vu"


def test_la_liste_des_ecrans_suit_pages() -> None:
    """Le parcours de la commande est celui de `PAGES` — au chemin et au libellé près.

    ⚠ Rougit dans les DEUX sens : un écran ajouté au menu sans entrer au parcours ne serait jamais
    regardé ; un écran retiré du menu laisserait un parcours vers une page qui se dérobe.
    """
    navigation = ecrans_de_navigation(NAVIGATION.read_text(encoding="utf-8"))
    prompt = ecrans_du_prompt(COMMANDE.read_text(encoding="utf-8"))
    assert len(navigation) >= 2, "le parseur ne voit plus le menu : le test ne garde plus rien"
    manquants = set(navigation) - set(prompt)
    disparus = set(prompt) - set(navigation)
    assert not manquants, f"écrans du menu absents du parcours de /retex-utilisateur : {manquants}"
    assert not disparus, f"écrans du parcours qui ne sont plus dans PAGES : {disparus}"
    libelles = {
        href: (navigation[href], prompt[href])
        for href in navigation
        if navigation[href] != prompt[href]
    }
    assert not libelles, f"libellés qui divergent (menu, prompt) : {libelles}"


# =================================================================================================
# Le prompt : son navigateur, son enchaînement, et ce qu'il ne fait jamais
# =================================================================================================


def test_la_commande_declare_son_navigateur_et_jamais_le_web() -> None:
    """C'est SON navigateur : à la différence de `/milestone-bilan`, elle n'a personne à qui
    déléguer le regard. Et le retex n'est pas une veille (#792) : ni `WebSearch` ni `WebFetch`."""
    entete = en_tete(COMMANDE)
    assert "allowed-tools:" in entete, "l'en-tête est bien lu, et non vide"
    assert "mcp__chrome-maestro" in entete
    assert "WebSearch" not in entete and "WebFetch" not in entete


def test_la_commande_enchaine_du_poste_vide_au_rapport() -> None:
    """Prérequis → parcours → run → livrable → rapport → `browser_close`, dans le texte."""
    texte = prose(COMMANDE)
    ordre = [
        "start.sh --stop",
        "maestro.controltower.purge --check",
        "start.sh --no-browser",
        "PosteVide",
        "<!-- ecrans:debut -->",
        "crée un projet",
        "compose dans le chat",
        "ouvre le livrable",
        "docs/retex/",
        "browser_close",
    ]
    positions = [texte.find(jalon) for jalon in ordre]
    assert all(position >= 0 for position in positions), dict(zip(ordre, positions, strict=True))
    assert positions == sorted(positions), "les étapes ne sont pas dans l'ordre annoncé"


def test_la_purge_se_confirme_et_ne_se_joue_jamais_doffice() -> None:
    texte = prose(COMMANDE)
    assert "--check" in texte
    assert "« oui » explicite" in texte
    assert "destructive" in texte


def test_la_commande_propose_et_ne_cree_rien() -> None:
    """Partage de `/run-audit` et `/milestone-bilan` : détecter le manque, jamais le verdict."""
    texte = prose(COMMANDE)
    assert "jamais créée" in texte
    assert "n'est pas commité" in texte
    assert "ne le joue pas" in texte, "elle NOMME /ticket-create à qui voudra créer"


def test_aucun_bloc_de_code_ne_retombe_sur_la_demo() -> None:
    """`--demo` n'apparaît qu'en prose, pour l'interdire — jamais dans une commande à jouer."""
    fautifs = [bloc for bloc in blocs_de_code(COMMANDE) if "--demo" in bloc]
    assert not fautifs, fautifs
    assert "--demo" in prose(COMMANDE), "l'interdit est écrit, et non oublié"


def test_la_commande_ne_prescrit_aucune_ecriture_de_forge() -> None:
    """Aucun `gh` prescrit, et le seul verbe de `lib.sh` joué est une lecture."""
    lectures = {"backlog-table"}
    fautives = []
    for numero, ligne in enumerate(COMMANDE.read_text(encoding="utf-8").splitlines(), 1):
        nue = ligne.strip()
        if nue.startswith("gh "):
            fautives.append(f"{numero}: {nue}")
        if nue.startswith("bash scripts/gitlab/lib.sh"):
            verbe = nue.split()[2] if len(nue.split()) > 2 else ""
            if verbe not in lectures:
                fautives.append(f"{numero}: {nue}")
    assert not fautives, "écriture de forge prescrite :\n" + "\n".join(fautives)
    for bloc in blocs_de_code(COMMANDE):
        assert "ticket-create" not in bloc, "les tickets sont proposés, jamais créés"
        assert "gh " not in bloc


def test_la_commande_est_sous_le_balayage_du_cycle_de_vie() -> None:
    """Elle vit sous `.claude/commands/` : `test_cycle_de_vie.py` y interdit déjà `--add-label`,
    `test_collaboration.py` les formes immatchables — elle y entre par construction."""
    assert COMMANDE in set(COMMANDES.glob("*.md"))
    assert "--add-label" not in COMMANDE.read_text(encoding="utf-8")


# =================================================================================================
# La purge — sans Redis réel
# =================================================================================================


class FauxRedis:
    """Un client Redis synchrone qui tient ce qu'on lui donne — listes, hashes, clés nues.

    Rend des octets comme le vrai client, et note chaque `delete` : c'est ce qu'un `--check` ne
    doit jamais appeler.
    """

    def __init__(
        self,
        listes: dict[str, list[str]] | None = None,
        hashes: dict[str, dict[str, str]] | None = None,
        cles: tuple[str, ...] = (),
        *,
        joignable: bool = True,
    ) -> None:
        self.listes = dict(listes or {})
        self.hashes = dict(hashes or {})
        self.autres = set(cles)
        self.joignable = joignable
        self.supprimees: list[str] = []

    def ping(self) -> bool:
        if not self.joignable:
            raise ConnectionError("connexion refusée")
        return True

    def llen(self, name: str) -> int:
        return len(self.listes.get(name, []))

    def hlen(self, name: str) -> int:
        return len(self.hashes.get(name, {}))

    def hgetall(self, name: str) -> dict[bytes, bytes]:
        return {k.encode(): v.encode() for k, v in self.hashes.get(name, {}).items()}

    def scan_iter(self, match: str | None = None, count: int | None = None) -> list[bytes]:
        return [cle.encode() for cle in sorted(self.cles()) if match is None or fnmatch(cle, match)]

    def delete(self, *names: str) -> int:
        retirees = 0
        for name in names:
            present = name in self.listes or name in self.hashes or name in self.autres
            self.listes.pop(name, None)
            self.hashes.pop(name, None)
            self.autres.discard(name)
            if present:
                retirees += 1
            self.supprimees.append(name)
        return retirees

    def cles(self) -> set[str]:
        return set(self.listes) | set(self.hashes) | self.autres


CONFIGURATION = ("agents", "playbooks", "surcharges", "capacite", "permissions", "secrets", "mcp")


class Poste:
    """Un poste jetable : les dossiers de données, ceux de la configuration, un projet réel."""

    def __init__(self, racine: Path) -> None:
        self.racine = racine
        self.chat = racine / "chat"
        self.ingestion = racine / "ingestion"
        self.projets = racine / "projets"
        self.projet_reel = racine / "mon-projet"
        self.configuration = {nom: racine / nom for nom in CONFIGURATION}

    def peupler(self) -> None:
        for dossier in (self.chat, self.ingestion, self.projets):
            dossier.mkdir()
            (dossier / "README.md").write_text("doc versionnée\n", encoding="utf-8")
            (dossier / ".gitignore").write_text("*\n!README.md\n", encoding="utf-8")
        (self.chat / "dev.jsonl").write_text("{}\n", encoding="utf-8")
        (self.chat / "dev").mkdir()
        (self.chat / "dev" / "abc123.jsonl").write_text("{}\n", encoding="utf-8")
        (self.ingestion / "run-1").mkdir()
        (self.ingestion / "run-1" / "note.md").write_text("# note\n", encoding="utf-8")
        (self.ingestion / "_televersements").mkdir()
        (self.ingestion / "_televersements" / "octets.bin").write_bytes(b"\x00\x01")
        (self.projets / "abcd1234.json").write_text('{"racine": "mon-projet"}', encoding="utf-8")
        (self.projets / "notes.txt").write_text("pas une déclaration\n", encoding="utf-8")
        self.projet_reel.mkdir()
        (self.projet_reel / "app.py").write_text("print('bonjour')\n", encoding="utf-8")
        for nom, dossier in self.configuration.items():
            dossier.mkdir()
            (dossier / f"{nom}.json").write_text("{}", encoding="utf-8")

    def empreinte(self, dossier: Path) -> set[str]:
        return {str(p.relative_to(dossier)) for p in dossier.rglob("*")}

    def empreinte_configuration(self) -> dict[str, set[str]]:
        return {nom: self.empreinte(d) for nom, d in self.configuration.items()}


@pytest.fixture
def poste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Poste:
    poste = Poste(tmp_path)
    poste.peupler()
    monkeypatch.setenv("MAESTRO_CHAT_DIR", str(poste.chat))
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(poste.ingestion))
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(poste.projets))
    for nom, dossier in poste.configuration.items():
        monkeypatch.setenv(f"MAESTRO_{nom.upper()}_DIR", str(dossier))
    return poste


@pytest.fixture
def settings(poste: Poste) -> Settings:
    return Settings.from_env()


def redis_peuple(*, battement: str | None = None) -> FauxRedis:
    hashes = {CLE_BATTEMENTS: {"run-orphelin": battement}} if battement else {}
    return FauxRedis(
        listes={CLE_JOURNAL_EVENEMENTS: ["e1", "e2", "e3"], FILE_TACHES: ["t1"]},
        hashes=hashes,
        cles=(f"{CANAL_BOITE_PREFIXE}dev", CANAL_DIFFUSION, "autre.cle"),
    )


def joue(
    *args: str,
    client: FauxRedis,
    settings: Settings,
    api: bool = False,
) -> tuple[int, str, str]:
    sortie, erreur = io.StringIO(), io.StringIO()
    code = purge.main(
        list(args),
        client=client,
        sonde_api=lambda: api,
        settings=settings,
        sortie=sortie,
        erreur=erreur,
    )
    return code, sortie.getvalue(), erreur.getvalue()


def perime() -> str:
    return horodatage_battement(datetime.now(UTC) - timedelta(hours=2))


# ── Les clés et les dossiers viennent des constantes ─────────────────────────────


def test_le_perimetre_est_celui_des_constantes(settings: Settings, poste: Poste) -> None:
    """Les clés SONT les constantes importées — pas des copies qui leur ressembleraient."""
    perimetre = purge.perimetre(settings)
    assert perimetre.journal is CLE_JOURNAL_EVENEMENTS
    assert perimetre.battements is CLE_BATTEMENTS
    assert perimetre.file_taches is FILE_TACHES
    assert perimetre.prefixe_boites is CANAL_BOITE_PREFIXE
    assert perimetre.diffusion is CANAL_DIFFUSION
    assert perimetre.conversations == poste.chat
    assert perimetre.ingestion == poste.ingestion
    assert perimetre.projets == poste.projets


def litteraux_hors_docstring(source: str) -> list[str]:
    """Les chaînes en dur d'un module, docstrings exclues — là où une clé recopiée se cacherait."""
    arbre = ast.parse(source)
    docstrings: set[int] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            corps = noeud.body
            if (
                corps
                and isinstance(corps[0], ast.Expr)
                and isinstance(corps[0].value, ast.Constant)
            ):
                docstrings.add(id(corps[0].value))
    return [
        noeud.value
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.Constant)
        and isinstance(noeud.value, str)
        and id(noeud) not in docstrings
        and "maestro." in noeud.value
    ]


def test_le_detecteur_de_litteraux_voit_une_cle_recopiee() -> None:
    """Contre-exemple : une constante recopiée est vue, une docstring qui la cite non."""
    fautif = 'CLE = "maestro.evenements:journal"\n'
    innocent = (
        '"""Vide la liste `maestro.evenements:journal`."""\n\n'
        'def f() -> None:\n    """Lit `maestro.taches`."""\n'
    )
    assert litteraux_hors_docstring(fautif) == ["maestro.evenements:journal"]
    assert litteraux_hors_docstring(innocent) == []


def test_aucune_cle_nest_recopiee_dans_le_module() -> None:
    """Une constante recopiée des deux côtés d'une frontière est ce que #830 a vu casser."""
    trouves = litteraux_hors_docstring(MODULE_PURGE.read_text(encoding="utf-8"))
    assert trouves == [], f"littéraux `maestro.` en dur dans purge.py : {trouves}"


# ── `--check` : rien n'est écrit ─────────────────────────────────────────────────


def test_check_compte_sans_rien_ecrire(settings: Settings, poste: Poste) -> None:
    client = redis_peuple(battement=perime())
    avant = poste.empreinte(poste.racine)
    code, sortie, erreur = joue("--check", "--projets", client=client, settings=settings)
    assert code == purge.CODE_FAIT, erreur
    assert client.supprimees == [], "un --check a appelé delete"
    assert poste.empreinte(poste.racine) == avant, "un --check a touché le disque"
    assert "rien n'est écrit" in sortie
    assert "3 événement(s)" in sortie
    assert "1 run(s)" in sortie
    assert "1 tâche(s)" in sortie
    assert "2 clé(s)" in sortie, "la boîte `dev` et la diffusion, jamais `autre.cle`"
    assert "2 fichier(s)" in sortie, "deux conversations, deux fichiers d'ingestion"
    assert "1 déclaration(s)" in sortie
    assert "la purge passerait" in sortie


def test_check_sans_projets_dit_que_les_declarations_restent(
    settings: Settings, poste: Poste
) -> None:
    code, sortie, _ = joue("--check", client=redis_peuple(), settings=settings)
    assert code == purge.CODE_FAIT
    assert "conservés" in sortie and "--projets" in sortie


# ── Les deux refus ───────────────────────────────────────────────────────────────


def test_refus_tant_que_lapi_repond(settings: Settings, poste: Poste) -> None:
    """Une API vivante rejouerait et servirait l'état qu'on vient de vider (#699)."""
    client = redis_peuple()
    avant = poste.empreinte(poste.racine)
    code, _, erreur = joue(client=client, settings=settings, api=True)
    assert code == purge.CODE_REFUS
    assert "start.sh --stop" in erreur, "le geste préalable est nommé"
    assert client.supprimees == []
    assert poste.empreinte(poste.racine) == avant


def test_check_rend_le_meme_refus_avec_les_comptes(settings: Settings, poste: Poste) -> None:
    """On sait AVANT de confirmer si le geste passerait — et ce qui partirait quand même."""
    code, sortie, erreur = joue("--check", client=redis_peuple(), settings=settings, api=True)
    assert code == purge.CODE_REFUS
    assert "3 événement(s)" in sortie
    assert "Purge refusée" in erreur and "start.sh --stop" in erreur


def test_refus_tant_quun_hote_detache_bat(settings: Settings, poste: Poste) -> None:
    """Ce qui bat bloque ; ce qui ne bat plus depuis le seuil est un orphelin, et passe."""
    vivant = redis_peuple(battement=horodatage_battement())
    code, _, erreur = joue(client=vivant, settings=settings)
    assert code == purge.CODE_REFUS
    assert "run-orphelin" in erreur and "start.sh --stop" in erreur
    assert vivant.supprimees == []

    orphelin = redis_peuple(battement=perime())
    code, sortie, _ = joue(client=orphelin, settings=settings)
    assert code == purge.CODE_FAIT, "un orphelin est ce que la purge est là pour ramasser"
    assert CLE_BATTEMENTS in orphelin.supprimees


def test_hotes_vivants_lit_le_registre_comme_lapi() -> None:
    maintenant = datetime.now(UTC)
    client = FauxRedis(
        hashes={
            CLE_BATTEMENTS: {
                "vif": horodatage_battement(maintenant),
                "mort": horodatage_battement(maintenant - timedelta(hours=3)),
                "illisible": "hier soir",
            }
        }
    )
    assert purge.hotes_vivants(client, CLE_BATTEMENTS, maintenant=maintenant) == ("vif",)


# ── La purge réelle ──────────────────────────────────────────────────────────────


def test_la_purge_retire_letat_et_rien_dautre(settings: Settings, poste: Poste) -> None:
    client = redis_peuple(battement=perime())
    configuration = poste.empreinte_configuration()
    projet_reel = poste.empreinte(poste.projet_reel)
    code, sortie, erreur = joue(client=client, settings=settings)
    assert code == purge.CODE_FAIT, erreur

    assert set(client.supprimees) == {
        CLE_JOURNAL_EVENEMENTS,
        CLE_BATTEMENTS,
        FILE_TACHES,
        f"{CANAL_BOITE_PREFIXE}dev",
        CANAL_DIFFUSION,
    }
    assert client.cles() == {"autre.cle"}, "une clé étrangère au périmètre reste"

    assert poste.empreinte(poste.chat) == {"README.md", ".gitignore"}, (
        "les conversations partent, la doc reste"
    )
    assert poste.empreinte(poste.ingestion) == {"README.md", ".gitignore"}
    assert (poste.projets / "abcd1234.json").is_file(), "sans --projets, les déclarations restent"
    assert poste.empreinte_configuration() == configuration, "la configuration est intacte"
    assert poste.empreinte(poste.projet_reel) == projet_reel, "le dossier du projet est intact"

    assert "retiré" in sortie
    assert "3 événement(s)" in sortie and "2 fichier(s)" in sortie


def test_projets_ne_retire_que_les_declarations(settings: Settings, poste: Poste) -> None:
    projet_reel = poste.empreinte(poste.projet_reel)
    code, sortie, erreur = joue("--projets", client=redis_peuple(), settings=settings)
    assert code == purge.CODE_FAIT, erreur
    assert not (poste.projets / "abcd1234.json").exists()
    assert poste.empreinte(poste.projets) == {"README.md", ".gitignore", "notes.txt"}, (
        "seuls les .json partent — la doc et un fichier étranger restent"
    )
    assert poste.empreinte(poste.projet_reel) == projet_reel, "jamais un dossier de projet"
    assert "1 déclaration(s)" in sortie and "les dossiers de projet restent" in sortie


def test_le_reel_rend_les_memes_comptes_que_check(settings: Settings, poste: Poste) -> None:
    _, verification, _ = joue("--check", "--projets", client=redis_peuple(), settings=settings)
    _, reel, _ = joue("--projets", client=redis_peuple(), settings=settings)
    comptes = re.compile(r"\d+ (?:événement|run|tâche|clé|fichier|déclaration)\(s\)")
    assert comptes.findall(verification) == comptes.findall(reel)
    assert comptes.findall(reel), "le motif de compte voit bien des comptes"


def test_un_poste_deja_vide_se_purge_sans_lever(settings: Settings, poste: Poste) -> None:
    """Rejouer sur un poste vide n'est pas une erreur : c'est le cas nominal du second retex."""
    joue(client=redis_peuple(), settings=settings)
    code, sortie, erreur = joue(client=FauxRedis(), settings=settings)
    assert code == purge.CODE_FAIT, erreur
    assert "0 événement(s)" in sortie


def test_un_dossier_absent_nest_pas_une_panne(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un déploiement où personne n'a jamais chatté ni téléversé n'a pas ces dossiers."""
    monkeypatch.setenv("MAESTRO_CHAT_DIR", str(tmp_path / "jamais"))
    monkeypatch.setenv("MAESTRO_INGESTION_DIR", str(tmp_path / "jamais-non-plus"))
    monkeypatch.setenv("MAESTRO_PROJETS_DIR", str(tmp_path / "ni-celui-la"))
    code, sortie, erreur = joue("--projets", client=FauxRedis(), settings=Settings.from_env())
    assert code == purge.CODE_FAIT, erreur
    assert "0 fichier(s)" in sortie and "0 déclaration(s)" in sortie


# ── Les sorties franches ─────────────────────────────────────────────────────────


def test_redis_injoignable_est_nomme(settings: Settings, poste: Poste) -> None:
    code, _, erreur = joue(client=FauxRedis(joignable=False), settings=settings)
    assert code == purge.CODE_REDIS_INJOIGNABLE
    assert "Redis injoignable" in erreur and "docker compose" in erreur


def test_un_argument_inconnu_est_refuse_avant_tout(settings: Settings, poste: Poste) -> None:
    client = redis_peuple()
    code, _, erreur = joue("--tout", client=client, settings=settings)
    assert code == purge.CODE_USAGE
    assert "--tout" in erreur and "Usage" in erreur
    assert client.supprimees == []


def test_le_port_sonde_est_celui_de_start_sh() -> None:
    """`MAESTRO_PORT_API` — le contrat du lanceur, surchargé par `worktree.sh ensure`."""
    assert purge.port_api({}) == 8000
    assert purge.port_api({"MAESTRO_PORT_API": "8053"}) == 8053
    assert purge.port_api({"MAESTRO_PORT_API": "pas-un-port"}) == 8000


def test_les_settings_injectes_font_foi(poste: Poste, settings: Settings) -> None:
    """Le périmètre suit les `Settings` qu'on lui passe, jamais l'environnement derrière eux."""
    ailleurs = replace(settings, chat_dir=str(poste.racine / "ailleurs"))
    assert purge.perimetre(ailleurs).conversations == poste.racine / "ailleurs"


# =================================================================================================
# La doc : trois entrées, comme le ticket le demande
# =================================================================================================


@pytest.mark.parametrize("document", [CLAUDE_MD, DOC_WORKFLOW, DOC_RETEX])
def test_la_commande_est_documentee(document: Path) -> None:
    texte = prose(document)
    assert "/retex-utilisateur" in texte, document.name
    assert "purge" in texte, (
        f"{document.name} ne dit pas que la commande écrit l'état de la Control Tower"
    )


def test_claude_md_dit_que_la_purge_ecrit_letat() -> None:
    """Rangée parmi les commandes de supervision, elle en est la seule qui écrive quelque chose."""
    texte = prose(CLAUDE_MD)
    debut = texte.find("`/retex-utilisateur")
    assert debut >= 0
    entree = texte[debut : debut + 2500]
    assert "écrit l'état de la Control Tower" in entree
    assert "maestro.controltower.purge" in entree
