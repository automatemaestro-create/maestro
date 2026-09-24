"""L'orchestrateur **lit** pour répondre, et ce qu'il lit se voit (ticket #1223).

Le fil répondait sur un contexte figé — compteurs, trois runs, détails coupés à
300 caractères. Le 2026-09-22, à « comment je fais pour tester l'animation ? »,
après un run qui venait d'écrire sa doc de lancement : *« le détail que j'ai ici
est tronqué […] un README y a probablement été créé »*. Ce lot lui donne quatre
lectures en lecture seule et les montre dans le fil.

Ce que cette suite tient, et rien d'autre :

① **la frontière** (`maestro.controltower.consultation`) — aucune lecture ne sort
   de la racine du projet (chemin relatif remontant, chemin absolu, lien
   symbolique), les secrets du périmètre restent fermés, les bornes se **disent**
   quand elles coupent, et **rien n'est jamais écrit** : le dossier du projet est
   identique, octet pour octet, après une salve de lectures ;
② **le protocole** — les demandes se lisent en tête de ligne, une ligne qui
   *cite* le marqueur n'en est pas une, et une ligne mal formée est sautée sans
   rien casser ;
③ **le tour de lecture** — un faux fournisseur **à outils** (il répond au prompt
   de consultation par des demandes, puis au prompt du juge par sa phrase) sur un
   projet posé dans un dossier temporaire : la réponse finale porte la commande
   et le fichier **lus**, et le prompt du juge porte ce qui a été lu ;
④ **les étapes** — publiées au fil de l'eau **avant** le premier mot de la
   réponse, puis persistées sur le message rendu ;
⑤ **ce qui ne peut jamais coûter la réponse** — sans consultation, sans
   fournisseur, sur un texte hors contrat ou sur une lecture qui lève, le canal
   répond comme avant ce lot ;
⑥ **l'équipe et les attentes** (critère 3) — rôles, agents, contenu des
   validations et des questions en attente, dans le prompt du juge ;
⑦ **ce que les tâches ont rendu** (#1263) — le texte qu'une tâche soldée rend
   au moteur traverse le journal, le pont et la projection jusqu'à la lecture
   du run, et un lecteur qui suit la piste va lire le livrable au lieu de
   renvoyer la personne l'ouvrir.

Aucun réseau, aucun modèle, aucun moteur : le fournisseur est un double, le
projet un dossier temporaire, la projection une `ControlTowerState` nue.
"""

from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

import pytest

from maestro.controltower.bridge import evenements_depuis_step
from maestro.controltower.chat import UTILISATEUR, EtapeFil, MessageChat
from maestro.controltower.consultation import (
    LECTURES_PAR_TOUR,
    MARQUEUR_LECTURE,
    OUTIL_CHERCHER,
    OUTIL_DETAIL,
    OUTIL_LIRE,
    OUTIL_LISTER,
    Consultations,
    Demande,
    Lecture,
    catalogue,
    demandes_de,
)
from maestro.controltower.events import (
    EVENEMENT_EXECUTION_STATUT,
    EVENEMENT_QUESTION_DEMANDE,
    EVENEMENT_TACHE_STATUT,
    EVENEMENT_VALIDATION_DEMANDE,
    Event,
)
from maestro.controltower.orchestration import (
    AGENT_ORCHESTRATION,
    NOM_ORCHESTRATION,
    RepondeurOrchestration,
    attentes_de,
    detail_du_run,
    faits_des_runs,
)
from maestro.controltower.state import EXECUTION_EN_COURS, EXECUTION_TERMINEE, ControlTowerState
from maestro.projets.modele import Perimetre, Projet
from maestro.providers.base import ModelProvider
from maestro.telemetry import RunJournal, StepUsage

# --------------------------------------------------------------- le harnais


def _projet(racine: Path) -> Projet:
    """Un projet déclaré sur `racine`, avec le périmètre **par défaut**.

    Le périmètre par défaut est ce qui ferme `.env` et `**/secrets/**`
    (`EXCLUS_DEFAUT`, docs/24 §2.5) : les tests de frontière ci-dessous
    n'ajoutent aucune exclusion pour leur compte — ils éprouvent celle que tout
    projet de l'utilisateur porte déjà.
    """
    return Projet(
        id="prj-essai",
        nom="Essai",
        racine=racine.resolve().as_posix(),
        perimetre=Perimetre(),
    )


def _consultations(racine: Path, *, detail=None) -> Consultations:
    return Consultations(projet=lambda _id: _projet(racine), detail=detail)


def _peupler(racine: Path) -> None:
    """Un petit projet réel : un README qui porte la commande, un manifeste, un secret."""
    (racine / "README.md").write_text(
        "# Animation du logo\n\n## Lancer\n\n    npm install\n    npm run dev\n",
        encoding="utf-8",
    )
    (racine / "package.json").write_text(
        '{\n  "scripts": { "dev": "vite" }\n}\n', encoding="utf-8"
    )
    (racine / ".env").write_text("SECRET=ne-doit-jamais-sortir\n", encoding="utf-8")
    (racine / "secrets").mkdir()
    (racine / "secrets" / "prod.key").write_text("aussi-secret\n", encoding="utf-8")
    (racine / "src").mkdir()
    (racine / "src" / "index.js").write_text("console.log('salut');\n", encoding="utf-8")


def _empreinte(racine: Path) -> list[tuple[str, int, bytes]]:
    """Tout ce que le dossier contient — chemin, taille, contenu. La mesure de « rien écrit »."""
    trouves: list[tuple[str, int, bytes]] = []
    for chemin in sorted(racine.rglob("*")):
        relatif = chemin.relative_to(racine).as_posix()
        if chemin.is_dir():
            trouves.append((f"{relatif}/", -1, b""))
        else:
            trouves.append((relatif, chemin.stat().st_size, chemin.read_bytes()))
    return trouves


class FournisseurAOutils(ModelProvider):
    """Faux fournisseur qui **se sert des outils** : il demande à lire, puis répond.

    Les deux appels d'un tour se distinguent par leur **prompt système** — celui
    de la consultation, celui de l'orchestration —, exactement comme le vrai
    canal les distingue. `lectures` est la suite des réponses au prompt de
    consultation (une par tour) ; `reponse` est ce que le juge rend ensuite.

    Il note les prompts : c'est par eux qu'on vérifie que **ce qui a été lu
    atteint le juge**, ce qu'aucune assertion sur le texte final ne montrerait.
    """

    name = "fournisseur-a-outils"

    def __init__(self, lectures: list[str], reponse: str) -> None:
        self.lectures = list(lectures)
        self.reponse = reponse
        self.prompts_consultation: list[str] = []
        self.prompts_juge: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        if system_prompt is not None and "tu décides ce qu'il faut LIRE" in system_prompt:
            self.prompts_consultation.append(prompt)
            return self.lectures.pop(0) if self.lectures else "RIEN"
        self.prompts_juge.append(prompt)
        return self.reponse


def _demande(outil: str, **arguments: str) -> str:
    """Une ligne de demande, telle que le modèle l'écrit."""
    champs = "".join(f', "{cle}": "{valeur}"' for cle, valeur in arguments.items())
    return f'{MARQUEUR_LECTURE} {{"outil": "{outil}"{champs}}}'


def _fil(*contenus: str) -> list[MessageChat]:
    return [
        MessageChat(
            agent=NOM_ORCHESTRATION,
            auteur=UTILISATEUR if rang % 2 == 0 else NOM_ORCHESTRATION,
            contenu=contenu,
        )
        for rang, contenu in enumerate(contenus)
    ]


class EtapesRecues:
    """Un `Etapeur` qui note **quand** chaque étape est arrivée, par rapport au texte."""

    def __init__(self) -> None:
        self.journal: list[tuple[str, str]] = []

    async def etape(self, etape: EtapeFil) -> None:
        self.journal.append(("etape", etape.libelle))

    async def increment(self, morceau: str) -> None:
        self.journal.append(("texte", morceau))


# ------------------------------------------------ ① la frontière des lectures


def test_une_lecture_ne_sort_jamais_de_la_racine(tmp_path: Path) -> None:
    """Remonter, viser un absolu, suivre un lien : trois refus, une seule phrase.

    La phrase est **la même** pour les trois, et c'est voulu : distinguer « hors
    racine » de « exclu » apprendrait au lecteur ce que la frontière est là pour
    taire.
    """
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    (tmp_path / "dehors.txt").write_text("interdit\n", encoding="utf-8")
    consultations = _consultations(racine)

    # Les deux conventions d'absolu sont éprouvées **sur les deux OS** : sous
    # Linux `Path("C:/Windows")` est relatif, sous Windows `/etc/passwd` l'est
    # presque, et le verdict ne doit pas dépendre de l'OS qui sert la Control
    # Tower — un `/tmp/secret` relu comme `tmp/secret` sous la racine serait
    # refusé par accident, avec une phrase qui parle d'autre chose.
    for chemin in (
        "../dehors.txt",
        "../",
        str(tmp_path / "dehors.txt"),
        (tmp_path / "dehors.txt").as_posix(),
        "/etc/passwd",
        "C:/Windows/System32/config/SAM",
        "\\\\serveur\\partage\\secret.txt",
    ):
        lecture = consultations.executer(
            Demande(outil=OUTIL_LIRE, arguments={"chemin": chemin}), "prj-essai"
        )
        assert "n'est pas lisible" in lecture.contenu, chemin
        assert "interdit" not in lecture.contenu, chemin


@pytest.mark.skipif(os.name == "nt", reason="un lien symbolique demande un privilège sous Windows")
def test_un_lien_symbolique_vers_l_exterieur_est_refuse(tmp_path: Path) -> None:
    """Le vecteur d'évasion de docs/24 §2.5 : la résolution réelle le ferme."""
    racine = tmp_path / "projet"
    racine.mkdir()
    (tmp_path / "dehors.txt").write_text("interdit\n", encoding="utf-8")
    (racine / "raccourci.txt").symlink_to(tmp_path / "dehors.txt")

    lecture = _consultations(racine).executer(
        Demande(outil=OUTIL_LIRE, arguments={"chemin": "raccourci.txt"}), "prj-essai"
    )

    assert "n'est pas lisible" in lecture.contenu
    assert "interdit" not in lecture.contenu


def test_les_secrets_du_perimetre_restent_fermes(tmp_path: Path) -> None:
    """`.env` et `secrets/` ne se lisent pas, et ne se listent pas non plus.

    Ce n'est pas une précaution prise ici : c'est le périmètre du projet
    (`EXCLUS_DEFAUT`) qu'on hérite, comme l'analyse d'un projet (#1158).
    """
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    consultations = _consultations(racine)

    for chemin in (".env", "secrets/prod.key", "secrets"):
        lecture = consultations.executer(
            Demande(outil=OUTIL_LIRE, arguments={"chemin": chemin}), "prj-essai"
        )
        assert "ne-doit-jamais-sortir" not in lecture.contenu, chemin
        assert "aussi-secret" not in lecture.contenu, chemin

    listing = consultations.executer(
        Demande(outil=OUTIL_LISTER, arguments={"chemin": "."}), "prj-essai"
    )
    assert ".env" not in listing.contenu
    assert "secrets/" not in listing.contenu
    assert "README.md" in listing.contenu

    # Et la recherche ne les balaye pas davantage : `grep -r` sans le nommer
    # était l'autre moitié du trou (`maestro.lecture`, #1197).
    trouve = consultations.executer(
        Demande(outil=OUTIL_CHERCHER, arguments={"motif": "ne-doit-jamais-sortir"}),
        "prj-essai",
    )
    assert "ne-doit-jamais-sortir" not in trouve.contenu.replace(
        "Aucune ligne ne contient « ne-doit-jamais-sortir ».", ""
    )


def test_aucune_lecture_n_ecrit_quoi_que_ce_soit(tmp_path: Path) -> None:
    """Le dossier du projet est identique **octet pour octet** après une salve."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    avant = _empreinte(racine)
    consultations = _consultations(racine)

    for demande in (
        Demande(outil=OUTIL_LISTER, arguments={"chemin": "."}),
        Demande(outil=OUTIL_LISTER, arguments={"chemin": "src"}),
        Demande(outil=OUTIL_LIRE, arguments={"chemin": "README.md"}),
        Demande(outil=OUTIL_LIRE, arguments={"chemin": "absent.md"}),
        Demande(outil=OUTIL_CHERCHER, arguments={"motif": "npm run"}),
        Demande(outil=OUTIL_CHERCHER, arguments={"motif": "npm", "chemin": "src"}),
        Demande(outil=OUTIL_DETAIL, arguments={"run_id": "inconnu"}),
    ):
        consultations.executer(demande, "prj-essai")

    assert _empreinte(racine) == avant


def test_une_borne_atteinte_se_dit_au_lieu_de_couper_en_silence(tmp_path: Path) -> None:
    """La règle de #1157 : un lecteur doit **savoir** qu'il lui manque quelque chose."""
    racine = tmp_path / "projet"
    racine.mkdir()
    (racine / "gros.md").write_text("x" * 20_000, encoding="utf-8")
    for rang in range(120):
        (racine / f"f{rang:03d}.txt").write_text("a\n", encoding="utf-8")
    consultations = _consultations(racine)

    lu = consultations.executer(
        Demande(outil=OUTIL_LIRE, arguments={"chemin": "gros.md"}), "prj-essai"
    )
    assert "fichier coupé" in lu.contenu

    listing = consultations.executer(
        Demande(outil=OUTIL_LISTER, arguments={"chemin": "."}), "prj-essai"
    )
    assert "non montrées ici" in listing.contenu


def test_sans_projet_la_lecture_le_dit_au_lieu_de_rendre_un_vide(tmp_path: Path) -> None:
    """« Rien trouvé » et « je ne peux pas chercher » ne se confondent pas."""
    lecture = Consultations(projet=lambda _id: None).executer(
        Demande(outil=OUTIL_LIRE, arguments={"chemin": "README.md"}), "prj-essai"
    )
    assert "Aucun projet ouvert" in lecture.contenu
    assert lecture.libelle


def test_le_detail_d_un_run_n_est_pas_tronque_a_trois_cents_caracteres() -> None:
    """Le verbe `detail` rend ce que la borne de #1157 coupait — c'est le constat du ticket."""
    state = ControlTowerState()
    trace = "Traceback: " + "x" * 2_000
    state.appliquer(
        Event(
            type=EVENEMENT_TACHE_STATUT,
            run_id="run-1",
            tache_id="t1",
            titre="Rédiger le README",
            statut="echec",
            detail=trace,
            agent="dev",
            role="Développeur",
        )
    )

    lecture = Consultations(detail=detail_du_run(state)).executer(
        Demande(outil=OUTIL_DETAIL, arguments={"run_id": "run-1"}), None
    )

    assert trace[:1_500] in lecture.contenu
    assert "run-1" in lecture.libelle


# --------------------------------------------------------- ② le protocole


def test_les_demandes_se_lisent_en_tete_de_ligne_et_rien_d_autre() -> None:
    """Une ligne qui **cite** le protocole n'en est pas une — sinon le fil se lirait lui-même."""
    texte = "\n".join(
        [
            "Je vais regarder.",
            _demande(OUTIL_LIRE, chemin="README.md"),
            f"Pour demander, écris `{MARQUEUR_LECTURE} " + '{"outil": "lire"}` en fin de phrase.',
            _demande(OUTIL_CHERCHER, motif="npm run"),
        ]
    )

    demandes = demandes_de(texte)

    assert [d.outil for d in demandes] == [OUTIL_LIRE, OUTIL_CHERCHER]
    assert demandes[0].arguments["chemin"] == "README.md"


def test_une_ligne_mal_formee_est_sautee_sans_rien_casser() -> None:
    """JSON illisible, objet qui n'en est pas un, verbe inconnu : rien d'exécutable."""
    texte = "\n".join(
        [
            f"{MARQUEUR_LECTURE} ceci n'est pas du JSON",
            f"{MARQUEUR_LECTURE} [1, 2, 3]",
            _demande("effacer", chemin="README.md"),
            _demande(OUTIL_LISTER, chemin="."),
        ]
    )

    assert [d.outil for d in demandes_de(texte)] == [OUTIL_LISTER]


def test_le_catalogue_nomme_les_quatre_verbes_et_la_forme_d_une_demande() -> None:
    """Le prompt décrit ce que le module exécute — recopié ailleurs, il divergerait."""
    texte = catalogue()
    for outil in (OUTIL_LISTER, OUTIL_CHERCHER, OUTIL_LIRE, OUTIL_DETAIL):
        assert outil in texte
    assert MARQUEUR_LECTURE in texte
    assert str(LECTURES_PAR_TOUR) in texte


# ------------------------------------------------------ ③ le tour de lecture


def test_la_reponse_donne_la_commande_et_le_fichier_lus_dans_le_projet(tmp_path: Path) -> None:
    """Critère 1, joué de bout en bout — la question du 2026-09-22, sur un vrai dossier.

    Le fournisseur demande à lire (deux tours : lister, puis lire le README qu'il
    y a vu), puis répond. Ce qu'on tient ici est que ce qu'il a **lu** entre dans
    le prompt du juge — c'est-à-dire qu'il peut répondre avec, au lieu d'envoyer
    vers la documentation.
    """
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    fournisseur = FournisseurAOutils(
        lectures=[
            _demande(OUTIL_LISTER, chemin="."),
            _demande(OUTIL_LIRE, chemin="README.md"),
        ],
        reponse="Le README du projet dit : `npm install` puis `npm run dev`.",
    )
    repondeur = RepondeurOrchestration(
        provider=fournisseur,
        consultation=_appel(_consultations(racine)),
    )

    reponse = asyncio.run(
        repondeur.produire(
            AGENT_ORCHESTRATION,
            _fil("comment je teste ce que le run a livré ?"),
            projet_id="prj-essai",
        )
    )

    prompt_du_juge = fournisseur.prompts_juge[-1]
    assert "Ce que tu viens de lire dans le projet" in prompt_du_juge
    assert "npm run dev" in prompt_du_juge
    assert "README.md" in prompt_du_juge
    assert reponse.contenu.startswith("Le README du projet dit")
    # Et les deux lectures sont rattachées au message, dans l'ordre où elles ont eu lieu.
    assert [etape.libelle for etape in reponse.etapes] == [
        # La racine se **nomme** : « A listé « . » » est le chemin que le modèle a
        # écrit, pas ce qu'il a consulté (relevé par le regard neuf de #1223).
        "A listé la racine du projet",
        "A lu « README.md »",
    ]


def test_le_tour_de_lecture_s_arrete_quand_il_n_y_a_plus_rien_a_lire(tmp_path: Path) -> None:
    """« RIEN » clôt le tour : le cas courant coûte un appel court, pas deux."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    fournisseur = FournisseurAOutils(lectures=["RIEN"], reponse="Bonjour !")

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(AGENT_ORCHESTRATION, _fil("bonjour"), projet_id="prj-essai")
    )

    assert len(fournisseur.prompts_consultation) == 1
    assert reponse.etapes == ()
    assert reponse.contenu == "Bonjour !"


def test_au_plus_cinq_lectures_par_tour(tmp_path: Path) -> None:
    """Le surplus est écarté : cinq fichiers répondent, trente sont une invitation à tout lire."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    fournisseur = FournisseurAOutils(
        lectures=["\n".join(_demande(OUTIL_LIRE, chemin="README.md") for _ in range(9)), "RIEN"],
        reponse="Voilà.",
    )

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(AGENT_ORCHESTRATION, _fil("et alors ?"), projet_id="prj-essai")
    )

    assert len(reponse.etapes) == LECTURES_PAR_TOUR


def test_une_meme_lecture_ne_se_fait_qu_une_fois_par_message(tmp_path: Path) -> None:
    """Redemander au second tour ce qu'on a lu au premier ne coûte ni lecture, ni ligne.

    C'est le cas le plus courant quand la première lecture n'a pas répondu : le
    modèle repose la même demande. Sans cette garde, le fil afficherait deux fois
    « A lu « README.md » » pour un seul fichier ouvert.
    """
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    meme = _demande(OUTIL_LIRE, chemin="README.md")
    fournisseur = FournisseurAOutils(lectures=[meme, meme], reponse="Toujours rien.")

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(AGENT_ORCHESTRATION, _fil("et encore ?"), projet_id="prj-essai")
    )

    assert [etape.libelle for etape in reponse.etapes] == ["A lu « README.md »"]


# -------------------------------------------------------------- ④ les étapes


def test_les_etapes_sont_publiees_avant_le_premier_mot_de_la_reponse(tmp_path: Path) -> None:
    """Critère 2 : on voit ce qu'il consulte **pendant** qu'il répond, donc avant qu'il parle."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    recues = EtapesRecues()
    fournisseur = FournisseurAOutils(
        lectures=[_demande(OUTIL_LIRE, chemin="README.md"), "RIEN"],
        reponse="Le README donne la commande.",
    )

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(
            AGENT_ORCHESTRATION,
            _fil("comment je lance ?"),
            incrementer=recues.increment,
            etapeur=recues.etape,
            projet_id="prj-essai",
        )
    )

    genres = [genre for genre, _ in recues.journal]
    assert genres[0] == "etape"
    assert "texte" in genres
    assert genres.index("etape") < genres.index("texte")
    # Publiées **et** persistées : le fil rouvert demain les retrouve.
    assert [etape.libelle for etape in reponse.etapes] == ["A lu « README.md »"]
    assert reponse.etapes[0].detail.startswith("# Animation du logo")


def test_sans_etapeur_la_reponse_est_exactement_celle_d_avant(tmp_path: Path) -> None:
    """Un appelant qui n'affiche rien au fil de l'eau ne change rien au reste."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)
    fournisseur = FournisseurAOutils(
        lectures=[_demande(OUTIL_LIRE, chemin="README.md"), "RIEN"], reponse="Voilà."
    )

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(AGENT_ORCHESTRATION, _fil("et donc ?"), projet_id="prj-essai")
    )

    assert reponse.contenu == "Voilà."
    assert len(reponse.etapes) == 1


# ------------------------------------- ⑤ rien de tout cela ne coûte la réponse


def test_sans_consultation_le_canal_repond_comme_avant_ce_lot() -> None:
    """Aucun tour de lecture, aucune étape, un seul appel modèle."""
    fournisseur = FournisseurAOutils(lectures=[], reponse="Réponse simple.")

    reponse = asyncio.run(
        RepondeurOrchestration(provider=fournisseur).produire(
            AGENT_ORCHESTRATION, _fil("bonjour"), projet_id="prj-essai"
        )
    )

    assert fournisseur.prompts_consultation == []
    assert reponse.etapes == ()
    assert reponse.contenu == "Réponse simple."


def test_une_lecture_qui_leve_ne_coute_pas_la_reponse(tmp_path: Path) -> None:
    """Un empêchement se **raconte** — l'invariant du fil depuis #686, un cran plus bas."""

    async def consultation_en_panne(demande: Demande, projet_id: str | None) -> Lecture:
        raise RuntimeError("disque débranché")

    fournisseur = FournisseurAOutils(
        lectures=[_demande(OUTIL_LIRE, chemin="README.md"), "RIEN"], reponse="Je n'ai pas pu lire."
    )

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=consultation_en_panne
        ).produire(AGENT_ORCHESTRATION, _fil("et le README ?"), projet_id="prj-essai")
    )

    assert reponse.contenu == "Je n'ai pas pu lire."
    assert "disque débranché" in reponse.etapes[0].detail
    assert "pas pu exécuter" in reponse.etapes[0].libelle


def test_un_fournisseur_muet_sur_la_consultation_laisse_le_juge_repondre(tmp_path: Path) -> None:
    """Lire est facultatif, répondre ne l'est pas."""
    racine = tmp_path / "projet"
    racine.mkdir()
    _peupler(racine)

    class ConsultationEnPanne(FournisseurAOutils):
        async def generate(self, prompt, *, model, system_prompt=None):
            if system_prompt is not None and "tu décides ce qu'il faut LIRE" in system_prompt:
                raise RuntimeError("le fournisseur ne répond pas")
            return await super().generate(prompt, model=model, system_prompt=system_prompt)

    fournisseur = ConsultationEnPanne(lectures=[], reponse="Je réponds quand même.")

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur, consultation=_appel(_consultations(racine))
        ).produire(AGENT_ORCHESTRATION, _fil("alors ?"), projet_id="prj-essai")
    )

    assert reponse.contenu == "Je réponds quand même."
    assert reponse.etapes == ()


# ----------------------------------------- ⑥ l'équipe et ce qui attend (critère 3)


def test_le_prompt_porte_l_equipe_reelle_et_le_contenu_des_attentes() -> None:
    """Critère 3 : le fil ne **comptait** que les validations, il en dit maintenant le contenu."""
    state = ControlTowerState()
    state.appliquer(
        Event(
            type=EVENEMENT_VALIDATION_DEMANDE,
            run_id="run-1",
            tache_id="t1",
            titre="Vider le dossier",
            description="supprimer 14 fichiers du dossier du projet",
            detail="acte irréversible",
            agent="dev",
            role="Développeur",
            projet_id="prj-essai",
        )
    )
    state.appliquer(
        Event(
            type=EVENEMENT_QUESTION_DEMANDE,
            run_id="run-1",
            tache_id="t1",
            question_id="q1",
            description="Faut-il garder le dossier `assets` ?",
            hypothese="je le garde",
            agent="dev",
            role="Développeur",
            projet_id="prj-essai",
        )
    )
    fournisseur = FournisseurAOutils(lectures=[], reponse="Voici où en est le projet.")

    asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur,
            attentes=attentes_de(state),
            roles=lambda _projet: "Équipe du projet : Développeur « dev » — modèle m",
        ).produire(AGENT_ORCHESTRATION, _fil("qui travaille dessus ?"), projet_id="prj-essai")
    )

    prompt = fournisseur.prompts_juge[-1]
    assert "Développeur « dev »" in prompt
    assert "Vider le dossier" in prompt
    assert "supprimer 14 fichiers" in prompt
    assert "Faut-il garder le dossier `assets` ?" in prompt
    assert "je le garde" in prompt


def test_sans_attente_le_bloc_disparait_du_prompt() -> None:
    """Un vide ne s'annonce pas : l'aperçu le dit déjà (règle de #1157)."""
    assert attentes_de(ControlTowerState())("prj-essai") == ""


# ------------------------------------------ ⑦ ce que les tâches ont rendu (#1263)

RUN_LIVRE = "run-s5"

#: Ce qu'un agent rend en soldant sa tâche, dans la forme réelle d'une `sortie` :
#: ce qu'il a fait et les fichiers qu'il a écrits. Il **nomme** le README sans en
#: donner la commande de test — seule la lecture du fichier l'apprend, et c'est
#: ce qui fait de la question « comment je teste ? » une question de lecture.
RENDU_APPLICATION = (
    "J'ai créé `app.py` à la racine : lancé par `python app.py`, il affiche une "
    "ligne puis se termine sans erreur."
)
RENDU_README = "J'ai rédigé `README.md`, qui dit comment lancer et tester l'application."

#: Ce que le README livré porte, et que rien d'autre ne dit.
COMMANDE_DU_README = "python -m unittest test_app"


def _livrer(racine: Path) -> None:
    """Le livrable du run sur le disque : le point d'entrée, et le README qui dit la commande."""
    (racine / "app.py").write_text('print("Bonjour depuis le livrable")\n', encoding="utf-8")
    (racine / "README.md").write_text(
        "# Petite application\n\n## Lancer\n\n    python app.py\n\n"
        f"## Tester\n\n    {COMMANDE_DU_README}\n",
        encoding="utf-8",
    )


def _projection_du_run_livre(
    rendus: list[tuple[str, str, str]], projet_id: str | None = "prj-essai"
) -> ControlTowerState:
    """La projection d'un run qui a livré, rejouée **par son journal** et par le pont.

    Le chemin est celui de la vraie Control Tower : le moteur consigne le début
    puis l'issue de chaque tâche dans un `RunJournal`, le pont
    (`evenements_depuis_step`) en fait des événements, la projection les
    applique. Poser les champs à la main laisserait passer un pont qui ne fait
    pas voyager ce que la tâche a rendu — le défaut exact du bouclage du
    2026-09-24.
    """
    state = ControlTowerState()
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN_LIVRE,
            statut=EXECUTION_EN_COURS,
            description="Créer une petite application Python exécutable, avec son mode d'emploi",
            projet_id=projet_id,
        )
    )
    journal = RunJournal(run_id=RUN_LIVRE)
    for tache_id, titre, rendu in rendus:
        journal.consigne(
            etape=f"{tache_id}:debut", nom=titre, agent="dev", role="Développeur",
            statut="en_cours", entree="", sortie="démarrage de la tâche",
            usage=StepUsage(), projet_id=projet_id,
        )
        journal.consigne(
            etape=tache_id, nom=titre, agent="dev", role="Développeur",
            statut="terminee", entree="la consigne de la tâche", sortie=rendu,
            usage=StepUsage(cout_usd=0.01), projet_id=projet_id,
        )
    for record in journal.records:
        for event in evenements_depuis_step(record.to_dict()):
            state.appliquer(event)
    state.appliquer(
        Event(
            type=EVENEMENT_EXECUTION_STATUT,
            run_id=RUN_LIVRE,
            statut=EXECUTION_TERMINEE,
            detail=f"{len(rendus)}/{len(rendus)} tâche(s) réussie(s)",
            projet_id=projet_id,
        )
    )
    return state


def _lecture_du_run(state: ControlTowerState) -> Lecture:
    return Consultations(detail=detail_du_run(state)).executer(
        Demande(outil=OUTIL_DETAIL, arguments={"run_id": RUN_LIVRE}), None
    )


def test_le_pont_fait_voyager_ce_que_la_tache_a_rendu_et_seulement_sur_son_issue() -> None:
    """Le moteur consigne la `sortie` d'une tâche soldée ; le pont la jetait.

    Elle voyage dans un champ à elle (`resultat`) et non dans `detail`, qui reste
    ce qu'il était — l'erreur d'une issue, la phrase d'un début — et que la frise
    affiche tel quel. Le début d'une tâche n'a rien rendu : il n'en porte pas. Et
    le champ survit au journal durable, qui relit les événements en JSON.
    """
    (debut,) = evenements_depuis_step(
        {"run_id": "r", "etape": "t1:debut", "nom": "Écrire l'application",
         "statut": "en_cours", "sortie": "démarrage de la tâche"}
    )
    (issue,) = evenements_depuis_step(
        {"run_id": "r", "etape": "t1", "nom": "Écrire l'application",
         "statut": "terminee", "sortie": RENDU_APPLICATION, "erreur": None}
    )

    assert debut.resultat == ""
    assert issue.type == EVENEMENT_TACHE_STATUT
    assert issue.resultat == RENDU_APPLICATION
    assert issue.detail == ""
    assert Event.from_json(issue.to_json()).resultat == RENDU_APPLICATION


def test_la_lecture_d_un_run_rend_le_resultat_de_chaque_tache_soldee() -> None:
    """Critère 1 : le verbe `detail` rend ce que chaque tâche soldée a produit.

    Au bouclage, pour chaque tâche soldée, la lecture ne rendait que « détail :
    démarrage de la tâche » — le dernier détail vu, celui du début. L'orchestrateur
    a lu une matière pauvre et s'est arrêté là.
    """
    state = _projection_du_run_livre(
        [
            ("t1", "Écrire l'application", RENDU_APPLICATION),
            ("t2", "Rédiger le README", RENDU_README),
        ]
    )

    lecture = _lecture_du_run(state)

    assert f"résultat : {RENDU_APPLICATION}" in lecture.contenu
    assert f"résultat : {RENDU_README}" in lecture.contenu
    # Une tâche soldée ne se raconte plus par son démarrage.
    assert "démarrage de la tâche" not in lecture.contenu


def test_un_resultat_sur_plusieurs_lignes_reste_sous_sa_tache() -> None:
    """Un rendu d'agent est souvent un petit compte rendu : il garde ses lignes, en retrait.

    Sans retrait, sa deuxième ligne se lirait comme une tâche de plus — ou comme
    un champ du run.
    """
    rendu = "J'ai livré deux fichiers :\n- app.py\n- README.md"
    state = _projection_du_run_livre([("t1", "Écrire l'application", rendu)])

    contenu = _lecture_du_run(state).contenu

    assert "résultat : J'ai livré deux fichiers :" in contenu
    assert "\n    - app.py\n    - README.md" in contenu


def test_chaque_tache_garde_sa_place_quand_les_resultats_sont_longs() -> None:
    """« De chaque tâche » : un premier rendu bavard ne pousse pas les suivants hors de la lecture.

    La lecture entière est bornée (`consultation`), et une borne qui coupe à la
    fin ferait disparaître les dernières tâches derrière la première. Chaque
    résultat reçoit donc sa part, et une part atteinte se **dit**.
    """
    rendus = [
        (f"t{rang}", f"Tâche {rang}", f"Rendu de la tâche {rang}. " + "détail " * 700)
        for rang in range(1, 7)
    ]
    state = _projection_du_run_livre(rendus)

    contenu = _lecture_du_run(state).contenu

    for rang in range(1, 7):
        assert f"résultat : Rendu de la tâche {rang}." in contenu
    assert "résultat coupé" in contenu
    assert "(détail coupé)" not in contenu


def test_les_faits_d_un_run_disent_ce_qu_une_tache_a_rendu_et_non_qu_elle_a_demarre() -> None:
    """Le même défaut, un cran plus haut : le bloc de faits que chaque prompt reçoit.

    C'est lui que le tour de lecture voit **avant** de rien demander : s'il nomme
    les fichiers rendus, le premier tour peut aller les lire sans attendre le
    second.
    """
    state = _projection_du_run_livre([("t1", "Écrire l'application", RENDU_APPLICATION)])

    faits = faits_des_runs(state)("prj-essai", ())

    assert f"résultat : {RENDU_APPLICATION}" in faits
    assert "démarrage de la tâche" not in faits


class FournisseurQuiSuitLaPiste(ModelProvider):
    """Faux fournisseur à outils **sans script** : il lit ce qu'on lui montre, puis demande.

    À chaque tour de lecture, il demande les fichiers que le prompt **nomme**
    (faits des runs, lectures déjà faites) et qu'il n'a pas encore lus ; à
    défaut, le détail d'un run dont il voit la fiche et qu'il n'a pas relu ;
    sinon, `RIEN`. C'est la conduite d'un modèle qui suit la piste, et c'est ce
    qui rend le test honnête : il ne lit le README que si le canal lui en a
    montré le chemin.

    Le juge répond avec ce qui est dans son prompt : la commande du README s'il
    l'a sous les yeux, sinon l'aveu relevé au bouclage du 2026-09-24 — il renvoie
    la personne ouvrir les fichiers.
    """

    name = "fournisseur-qui-suit-la-piste"
    _FICHIER = re.compile(r"[\w./-]+\.(?:md|py|toml|json|txt)\b")
    _RUN = re.compile(r"^- Run (\S+)", re.MULTILINE)
    _LU = re.compile(r"^A lu « (.+?) » :", re.MULTILINE)

    def __init__(self, commande: str) -> None:
        self.commande = commande
        self.prompts_juge: list[str] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(
        self, prompt: str, *, model: str, system_prompt: str | None = None
    ) -> str:
        if system_prompt is not None and "tu décides ce qu'il faut LIRE" in system_prompt:
            return self._demandes(prompt)
        self.prompts_juge.append(prompt)
        if self.commande in prompt:
            return f"Le README livré le dit : `{self.commande}`."
        return (
            "Le run a livré app.py et un README ; je n'ai pas lu leur contenu, il "
            "faudra ouvrir ces fichiers dans votre dossier de projet."
        )

    def _demandes(self, prompt: str) -> str:
        lus = set(self._LU.findall(prompt))
        a_lire = [chemin for chemin in dict.fromkeys(self._FICHIER.findall(prompt))
                  if chemin not in lus]
        if a_lire:
            return "\n".join(_demande(OUTIL_LIRE, chemin=chemin) for chemin in a_lire)
        runs = [run for run in self._RUN.findall(prompt)
                if f"A relu le run « {run} »" not in prompt]
        if runs:
            return _demande(OUTIL_DETAIL, run_id=runs[0])
        return "RIEN"


def test_a_comment_tester_la_reponse_s_appuie_sur_le_readme_lu_et_ne_renvoie_pas_aux_fichiers(
    tmp_path: Path,
) -> None:
    """Critère 2, joué de bout en bout — la question du bouclage, dans une conversation neuve.

    Un projet dont le README donne la commande de test, le run qui l'a livré
    rejoué par son journal, et un lecteur qui ne lit que ce qu'on lui montre. La
    réponse doit porter la commande **lue**, et non renvoyer ouvrir les fichiers :
    c'est ce que le fil a répondu au bouclage, faute d'avoir vu nommé ce que le
    run avait produit.
    """
    racine = tmp_path / "projet"
    racine.mkdir()
    _livrer(racine)
    state = _projection_du_run_livre(
        [
            ("t1", "Écrire l'application", RENDU_APPLICATION),
            ("t2", "Rédiger le README", RENDU_README),
        ]
    )
    fournisseur = FournisseurQuiSuitLaPiste(COMMANDE_DU_README)
    consultations = Consultations(
        projet=lambda _id: _projet(racine), detail=detail_du_run(state)
    )

    reponse = asyncio.run(
        RepondeurOrchestration(
            provider=fournisseur,
            consultation=_appel(consultations),
            faits=faits_des_runs(state),
        ).produire(
            AGENT_ORCHESTRATION,
            _fil("Comment je fais pour tester ce projet ?"),
            projet_id="prj-essai",
        )
    )

    assert COMMANDE_DU_README in reponse.contenu
    assert "ouvrir ces fichiers" not in reponse.contenu
    # Elle s'appuie sur ce qui a été lu : le README est dans les étapes du message,
    # et son contenu dans le prompt du juge.
    assert "A lu « README.md »" in [etape.libelle for etape in reponse.etapes]
    assert COMMANDE_DU_README in fournisseur.prompts_juge[-1]


# ------------------------------------------------------------------ outillage


def _appel(consultations: Consultations):
    """L'adaptateur attendable du répondeur — ce que `app.py` branche sur `to_thread`."""

    async def consulter(demande: Demande, projet_id: str | None) -> Lecture:
        return consultations.executer(demande, projet_id)

    return consulter
