"""Fédération, porte d'admission et garde-fou (#677, #678, #679 — testés au lot 6, #680).

Trois lots se rejoignent ici, et un seul fait tient l'ensemble : **la découverte
s'élargit, jamais l'installation**. Le registre officiel dit « ce serveur
existe » ; il ne dit pas « ce serveur est sûr », et c'est la porte d'admission
qui répond à la seconde question — par un geste humain tracé.

Le garde-fou est donc éprouvé **aux deux bouts**, comme le critère d'acceptation
le demande : une entrée découverte non admise est refusée par
`RegistreMcp.instancier` *et* par `POST /api/mcp/pool`. Les deux, jamais l'un à
la place de l'autre — ils ne partagent pas le même chemin de code (la route
refuse **avant** d'instancier), et vérifier le premier laisserait le second
s'ouvrir sans que rien ne rougisse.

⚠ **Rien ici ne moissonne**, et ce n'est pas qu'une affaire de réseau : la
fédération *lit* un miroir déjà sur le disque. `test_la_federation_ne_moissonne_jamais`
en fait une propriété vérifiée plutôt qu'une intention — un rafraîchissement
glissé sur le chemin d'une requête d'écran coûterait dix minutes de réseau par
affichage.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.agents.mcp import McpStore
from maestro.agents.mcp_admission import (
    MOTIF_DEJA_CUREE,
    MOTIF_DEJA_REVOQUEE,
    MOTIF_NON_ADMISE,
    MOTIF_NON_TRADUISIBLE,
    MOTIF_POLITIQUE,
    MOTIF_SUPPRIMEE,
    Candidature,
    EtatAmontEntree,
    MagasinAdmissions,
    RefusAdmission,
    ServiceAdmission,
    VerdictPolitique,
    charger_politique,
    etat_politique,
    nom_amont_de,
    politique_ouverte,
    veiller,
)
from maestro.agents.mcp_amont import EntreeAmont, MiroirAmont
from maestro.agents.mcp_federation import (
    CAUSE_SANS_TRADUCTION,
    federer,
    federer_memo,
    lire_admissions,
    oublier_memo,
    traduire_miroir,
    veille_du_miroir,
)
from maestro.agents.mcp_registry import (
    SIGNAL_DEPRECIEE,
    SIGNAL_DISPARUE,
    SIGNAL_SUPPRIMEE,
    SIGNAL_VERSION,
    SOURCE_ADMISE,
    SOURCE_CUREE,
    SOURCE_DECOUVERTE,
    Admission,
    EntreeRegistre,
    RegistreMcp,
    SignalAmont,
)
from maestro.controltower.app import create_app
from maestro.controltower.events import InMemoryEventBus

AMONT = "https://registre.test"
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


# --------------------------------------------------------------------------- #
# Harnais : un miroir et un journal sur disque, écrits dans leur forme publique
# --------------------------------------------------------------------------- #


def document(nom: str, *, url: str = "", depot: str = "") -> dict[str, Any]:
    """Un `server.json` d'amont minimal mais traduisible (endpoint HTTP)."""
    brut: dict[str, Any] = {
        "$schema": SCHEMA,
        "name": nom,
        "description": f"serveur {nom}",
        "remotes": [{"type": "streamable-http", "url": url or f"https://{nom.split('/')[-1]}.test/mcp"}],
    }
    if depot:
        brut["repository"] = {"url": depot}
    return brut


def amont(
    nom: str,
    *,
    version: str = "1.0.0",
    statut: str = "active",
    depot: str = "",
    publie_le: str = "2026-07-14T08:30:00Z",
) -> EntreeAmont:
    """Une entrée du miroir, telle que `mcp_amont` la garde."""
    return EntreeAmont(
        nom=nom,
        version=version,
        statut=statut,
        publie_le=publie_le,
        mis_a_jour_le=publie_le,
        document=document(nom, depot=depot),
    )


def poser_miroir(
    racine: Path,
    entrees: Sequence[EntreeAmont],
    *,
    rafraichi_le: str = "2026-08-28T06:00:00Z",
    nombre: int | None = None,
    cause: str = "",
) -> MiroirAmont:
    """Écrit un miroir sur disque **dans sa forme publique**, sans passer par le réseau."""
    racine.mkdir(parents=True, exist_ok=True)
    (racine / MiroirAmont.FICHIER_ENTREES).write_text(
        "".join(json.dumps(e.to_dict(), ensure_ascii=False) + "\n" for e in entrees),
        encoding="utf-8",
    )
    (racine / MiroirAmont.FICHIER_ETAT).write_text(
        json.dumps(
            {
                "amont": AMONT,
                "rafraichi_le": rafraichi_le,
                "moissonne_le": rafraichi_le,
                "borne_amont": rafraichi_le,
                "nombre": len(entrees) if nombre is None else nombre,
                "cause": cause,
                "echoue_le": "2026-08-28T09:00:00Z" if cause else "",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return MiroirAmont(racine, amont=AMONT)


def curee(id: str = "curee-test", **champs: Any) -> EntreeRegistre:
    """Une entrée curée minimale — le rôle du seed, sans en dépendre."""
    defauts: dict[str, Any] = {
        "id": id,
        "nom": id.title(),
        "description": f"entrée curée {id}",
        "mode_auth": "sans_secret",
        "transport": "http",
        "url": f"https://{id}.test/mcp",
    }
    return EntreeRegistre(**{**defauts, **champs})


class MagasinCompteur(MagasinAdmissions):
    """Un journal qui **compte ses écritures** — l'idempotence se prouve sur ce compteur.

    « Ré-admettre n'écrit rien » ne se lit ni sur le contenu du fichier (il serait
    identique) ni sur une durée : il se lit sur le nombre d'appels à `ecrire`.
    Même règle que le comptage d'allers d'amont (#577).
    """

    def __init__(self, racine: Path) -> None:
        super().__init__(racine)
        self.ecritures = 0

    def ecrire(self, admissions: Sequence[Admission]) -> tuple[Admission, ...]:
        self.ecritures += 1
        return super().ecrire(admissions)


@pytest.fixture()
def racines(tmp_path: Path) -> Iterator[tuple[Path, Path]]:
    """`(racine du miroir, racine des données d'installation MCP)` — jetables.

    La mémoire de `federer_memo` est vidée **des deux côtés** : elle est portée
    par le processus et n'a qu'une entrée, donc un test qui la laisserait pleine
    servirait sa fédération au suivant.
    """
    oublier_memo()
    yield tmp_path / "amont", tmp_path / "mcp"
    oublier_memo()


# --------------------------------------------------------------------------- #
# ① La fédération : deux sources, une bibliothèque
# --------------------------------------------------------------------------- #


def test_la_bibliotheque_rend_des_entrees_que_personne_n_a_ecrites(racines) -> None:
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    federation = federer(
        miroir,
        entrees_curees=[curee()],
        magasin=MagasinAdmissions(mcp_racine),
    )

    registre = federation.registre
    assert federation.lues == 1
    assert federation.traduites == 1
    assert {e.id for e in registre.lister()} == {"curee-test", "io-github-alice-veille"}
    decouverte = registre.trouver("io-github-alice-veille")
    assert decouverte is not None
    assert decouverte.curee is False
    assert decouverte.source == SOURCE_DECOUVERTE


def test_les_quatre_signaux_d_amont_sont_recolles_sur_l_entree_traduite(racines) -> None:
    """Version, statut, dépôt et date de publication vivent dans l'enveloppe du miroir.

    Le `server.json` ne les porte pas : la fédération est le seul endroit où les
    deux moitiés sont tenues ensemble, et un signal qu'on ne recolle pas est un
    signal que le panneau ne peut pas afficher.
    """
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(
        miroir_racine,
        [
            amont(
                "io.github.alice/veille",
                version="1.4.0",
                statut="deprecated",
                depot="https://github.com/alice/veille",
                publie_le="2026-07-14T08:30:00Z",
            )
        ],
    )
    federation = federer(miroir, entrees_curees=[], magasin=MagasinAdmissions(mcp_racine))
    entree = federation.registre.trouver("io-github-alice-veille")

    assert entree is not None
    assert entree.version == "1.4.0"
    assert entree.statut == "deprecated"
    assert entree.depot == "https://github.com/alice/veille"
    assert entree.publie_le == "2026-07-14T08:30:00Z"


def test_une_entree_curee_garde_la_main_sur_son_id(racines) -> None:
    """Le curé gagne — le masquer le rendrait injoignable, lui qui est instanciable."""
    miroir_racine, mcp_racine = racines
    # Un nom d'amont sans namespace, qui retombe donc sur le **même slug** qu'une
    # entrée du seed : c'est ainsi qu'une collision se produit pour de vrai.
    miroir = poser_miroir(miroir_racine, [amont("curee-test")])
    federation = federer(
        miroir, entrees_curees=[curee()], magasin=MagasinAdmissions(mcp_racine)
    )

    entree = federation.registre.trouver("curee-test")
    assert entree is not None and entree.curee is True
    assert entree.url == "https://curee-test.test/mcp", "le gabarit servi est celui du seed"
    assert any("le seed gagne" in cause for cause in federation.registre.decouvertes_ecartees)
    assert federation.registre.lister(SOURCE_DECOUVERTE) == ()


def test_la_source_est_decidee_par_l_argument_qui_porte_l_entree() -> None:
    """Jamais par le drapeau que l'entrée porte — sinon elle se déclarerait curée.

    Une entrée d'amont oubliée à `curee=True` serait présentée comme montable
    sans être dans l'allowlist : exactement le mensonge que le garde-fou ne doit
    jamais dire. C'est aussi pourquoi `entree_depuis_dict` ne relit pas ce champ.
    """
    menteuse = curee(id="io-github-alice-veille", curee=True)
    registre = RegistreMcp([curee()], decouvertes=[menteuse])

    servie = registre.trouver("io-github-alice-veille")
    assert servie is not None
    assert servie.curee is False
    assert servie.source == SOURCE_DECOUVERTE
    assert registre.get("io-github-alice-veille") is None


def test_la_provenance_de_la_decouverte_distingue_ce_qui_est_lu_de_ce_qui_est_servi(
    racines,
) -> None:
    """`retenues` n'est pas `nombre` : l'écart est une information, pas un trou à masquer."""
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(
        miroir_racine,
        [
            amont("io.github.alice/veille"),
            EntreeAmont(nom="io.github.bob/rien", document={"$schema": SCHEMA, "name": "x/rien"}),
        ],
    )
    federation = federer(miroir, entrees_curees=[], magasin=MagasinAdmissions(mcp_racine))

    provenance = federation.registre.provenance_decouverte
    assert provenance.amont == AMONT
    assert provenance.rafraichi_le == "2026-08-28T06:00:00Z"
    assert provenance.nombre == 2
    assert provenance.retenues == 1
    assert provenance.moissonnee is True
    assert federation.refusees == 1
    assert federation.motifs == {"sans_forme": 1}


def test_un_miroir_absent_rend_la_bibliotheque_curee_sans_lever(racines) -> None:
    miroir_racine, mcp_racine = racines
    miroir = MiroirAmont(miroir_racine / "jamais-moissonne", amont=AMONT)
    federation = federer(
        miroir, entrees_curees=[curee()], magasin=MagasinAdmissions(mcp_racine)
    )

    assert [e.id for e in federation.registre.lister()] == ["curee-test"]
    assert federation.lues == 0
    assert federation.registre.provenance_decouverte.moissonnee is False


def test_un_miroir_illisible_rend_la_cause_et_la_bibliotheque_curee(
    racines, monkeypatch: pytest.MonkeyPatch
) -> None:
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])

    def casse(self: MiroirAmont) -> tuple[EntreeAmont, ...]:
        raise OSError("disque fâché")

    monkeypatch.setattr(MiroirAmont, "entrees", casse)
    federation = federer(
        miroir, entrees_curees=[curee()], magasin=MagasinAdmissions(mcp_racine)
    )

    assert [e.id for e in federation.registre.lister()] == ["curee-test"]
    assert "miroir illisible" in federation.cause


def test_la_cause_du_miroir_en_panne_remonte_jusqu_a_la_provenance(racines) -> None:
    """Un écran ouvert trois heures après la panne doit pouvoir la dire."""
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(
        miroir_racine,
        [amont("io.github.alice/veille")],
        cause="amont injoignable — registre.test injoignable",
    )
    federation = federer(miroir, entrees_curees=[], magasin=MagasinAdmissions(mcp_racine))

    provenance = federation.registre.provenance_decouverte
    assert provenance.cause.startswith("amont injoignable")
    assert provenance.echoue_le == "2026-08-28T09:00:00Z"


def test_la_couture_du_lot_2_est_morte_et_le_traducteur_repond(racines) -> None:
    """`traducteur=None` retombe sur la résolution paresseuse — qui trouve #676, mergé.

    L'import différé de `mcp_federation._traducteur` était une **couture entre
    deux lots** d'un même chantier, que son propre en-tête annonce comme « du
    code mort le jour où #676 est mergé ». Ce jour est venu ; ce test l'établit,
    pour que le retrait de la couture soit un geste qu'on peut faire sans se
    demander ce qui en dépendait.
    """
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    federation = federer(
        miroir,
        entrees_curees=[curee()],
        traducteur=None,
        magasin=MagasinAdmissions(mcp_racine),
    )
    assert federation.cause == ""
    assert federation.traduites == 1


def test_sans_traducteur_la_bibliotheque_curee_est_servie_en_le_disant(
    racines, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Le repli reste éprouvé : la Control Tower ne perd pas sa bibliothèque."""
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    monkeypatch.setattr("maestro.agents.mcp_federation._traducteur", lambda: None)

    federation = federer(
        miroir, entrees_curees=[curee()], magasin=MagasinAdmissions(mcp_racine)
    )
    assert federation.cause == CAUSE_SANS_TRADUCTION
    assert federation.traduites == 0
    assert [e.id for e in federation.registre.lister()] == ["curee-test"]


def test_une_traduction_qui_explose_ne_coute_pas_les_autres_entrees() -> None:
    """Sur 25 000 entrées, une ligne fautive ne doit pas coûter les 24 999 autres."""

    def traducteur(entree: EntreeAmont) -> Any:
        if entree.nom.endswith("boum"):
            raise RuntimeError("contrat rompu")
        from maestro.agents.mcp_traduction import traduire_entree

        return traduire_entree(entree)

    entrees, traduites, refusees, motifs = traduire_miroir(
        [amont("io.github.alice/boum"), amont("io.github.bob/ok")], traducteur
    )
    assert [e.id for e in entrees] == ["io-github-bob-ok"]
    assert (traduites, refusees) == (1, 1)
    assert motifs == {"exception": 1}


def test_la_federation_ne_moissonne_jamais(racines, monkeypatch: pytest.MonkeyPatch) -> None:
    """Dix minutes de réseau sur le chemin d'une requête d'écran : jamais.

    Le contrat se prouve en rendant le moissonnage **impossible** — s'il était
    appelé, le test rougirait en nommant l'appel plutôt qu'en mesurant sa durée.
    """
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])

    def interdit(*_: object, **__: object) -> None:
        raise AssertionError("la fédération a moissonné")

    monkeypatch.setattr(MiroirAmont, "rafraichir", interdit)
    monkeypatch.setattr(MiroirAmont, "rafraichir_si_perime", interdit)
    federation = federer(miroir, entrees_curees=[], magasin=MagasinAdmissions(mcp_racine))
    assert federation.traduites == 1


def test_la_memoire_de_la_federation_tombe_quand_le_miroir_bouge(racines) -> None:
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    magasin = MagasinAdmissions(mcp_racine)

    premiere = federer_memo(miroir, magasin)
    assert federer_memo(miroir, magasin) is premiere

    poser_miroir(miroir_racine, [amont("io.github.alice/veille"), amont("io.github.bob/autre")])
    seconde = federer_memo(miroir, magasin)
    assert seconde is not premiere
    assert seconde.lues == 2


def test_la_memoire_de_la_federation_tombe_quand_le_journal_bouge(racines) -> None:
    """Une admission doit être visible **tout de suite**, pas au prochain moissonnage."""
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    magasin = MagasinAdmissions(mcp_racine)
    service = ServiceAdmission(magasin)

    premiere = federer_memo(miroir, magasin)
    decouverte = premiere.registre.trouver("io-github-alice-veille")
    assert decouverte is not None
    service.admettre(decouverte, par="alice")

    seconde = federer_memo(miroir, magasin)
    assert seconde is not premiere
    assert seconde.admises == 1


# --------------------------------------------------------------------------- #
# ② La porte d'admission : le geste humain tracé
# --------------------------------------------------------------------------- #


def registre_avec_decouverte(mcp_racine: Path, **champs: Any) -> tuple[RegistreMcp, EntreeRegistre]:
    """Une bibliothèque à deux sources, montée sans disque pour le miroir."""
    decouverte = curee(
        id="io-github-alice-veille",
        nom="veille",
        editeur="io.github.alice",
        version="1.4.0",
        depot="https://github.com/alice/veille",
        **champs,
    )
    registre = RegistreMcp([curee()], decouvertes=[decouverte])
    entree = registre.trouver("io-github-alice-veille")
    assert entree is not None
    return registre, entree


def test_une_decouverte_admise_entre_dans_l_allowlist(racines) -> None:
    _, mcp_racine = racines
    registre, decouverte = registre_avec_decouverte(mcp_racine)
    magasin = MagasinAdmissions(mcp_racine)
    admission = ServiceAdmission(magasin).admettre(
        decouverte, par="alice", note="revue faite", amont=AMONT, miroir_le="2026-08-28T06:00:00Z"
    )

    assert admission.par == "alice"
    assert admission.version == "1.4.0"
    assert admission.nom_amont == "io.github.alice/veille"
    assert admission.amont == AMONT
    assert admission.miroir_le == "2026-08-28T06:00:00Z"
    assert admission.active is True
    # L'entrée est figée **sans** son admission ni ses signaux : ce sont des vues
    # que le registre repose à chaque composition.
    assert admission.entree.admission is None
    assert admission.entree.signaux == ()
    assert admission.entree.curee is False

    apres = RegistreMcp([curee()], admissions=magasin.lister())
    montable = apres.get("io-github-alice-veille")
    assert montable is not None
    assert montable.curee is True
    assert montable.source == SOURCE_ADMISE
    montable.vers_serveur()


def test_readmettre_la_meme_version_n_ecrit_rien(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    magasin = MagasinCompteur(mcp_racine)
    service = ServiceAdmission(magasin)

    premiere = service.admettre(decouverte, par="alice")
    assert magasin.ecritures == 1
    seconde = service.admettre(decouverte, par="bob")
    # Idempotent, et **sans réécrire qui a admis** : la trace du premier geste
    # est ce qui a de la valeur.
    assert magasin.ecritures == 1
    assert seconde.par == "alice"
    # La **trace** est identique, et c'est elle le contrat. L'entrée figée, elle,
    # revient du journal sans son `curee` — `entree_depuis_dict` ne le relit pas
    # à dessein, le registre le reposant selon l'argument qui porte l'entrée.
    # Comparer les deux objets entiers épinglerait cette asymétrie voulue.
    assert seconde.trace() == premiere.trace()
    assert seconde.entree.id == premiere.entree.id
    assert seconde.entree.url == premiere.entree.url


def test_admettre_une_autre_version_est_un_nouveau_geste(racines) -> None:
    _, mcp_racine = racines
    _, ancienne = registre_avec_decouverte(mcp_racine)
    magasin = MagasinAdmissions(mcp_racine)
    service = ServiceAdmission(magasin)
    service.admettre(ancienne, par="alice")

    from dataclasses import replace

    neuve = replace(ancienne, version="2.0.0")
    promue = service.admettre(neuve, par="bob")

    assert promue.version == "2.0.0"
    assert promue.par == "bob"
    assert len(magasin.lister()) == 1


def test_une_entree_curee_n_a_rien_a_admettre(racines) -> None:
    _, mcp_racine = racines
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    with pytest.raises(RefusAdmission) as leve:
        service.admettre(curee())
    assert leve.value.motif == MOTIF_DEJA_CUREE


def test_une_entree_supprimee_chez_l_amont_n_entre_pas_dans_une_allowlist(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine, statut="deleted")
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    with pytest.raises(RefusAdmission) as leve:
        service.admettre(decouverte)
    assert leve.value.motif == MOTIF_SUPPRIMEE
    assert not MagasinAdmissions(mcp_racine).chemin.exists()


def test_un_gabarit_qui_ne_se_monterait_pas_est_refuse(racines) -> None:
    """La porte ne fabrique pas ce que le gabarit ne sait pas exprimer."""
    _, mcp_racine = racines
    boiteuse = EntreeRegistre(
        id="boiteuse",
        nom="Boiteuse",
        description="",
        mode_auth="sans_secret",
        transport="http",
        url="",
        curee=False,
    )
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    with pytest.raises(RefusAdmission) as leve:
        service.admettre(boiteuse)
    assert leve.value.motif == MOTIF_NON_TRADUISIBLE


def test_un_mode_d_auth_inconnu_est_refuse_avant_toute_ecriture(racines) -> None:
    _, mcp_racine = racines
    inconnue = EntreeRegistre(
        id="inconnue",
        nom="Inconnue",
        description="",
        mode_auth="carte-de-fidelite",
        transport="http",
        url="https://inconnue.test/mcp",
        curee=False,
    )
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    with pytest.raises(RefusAdmission) as leve:
        service.admettre(inconnue)
    assert leve.value.motif == MOTIF_NON_TRADUISIBLE


def test_la_politique_d_entreprise_passe_en_dernier_et_peut_refuser(racines) -> None:
    """Elle répond à « fait-on confiance ? », jamais à « ce serveur existe-t-il ? »."""
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    vues: list[Candidature] = []

    def severe(candidature: Candidature) -> VerdictPolitique:
        vues.append(candidature)
        return VerdictPolitique(admise=False, cause="éditeur hors liste maison")

    magasin = MagasinCompteur(mcp_racine)
    service = ServiceAdmission(magasin, politique=severe)
    with pytest.raises(RefusAdmission) as leve:
        service.admettre(decouverte, par="alice")

    assert leve.value.motif == MOTIF_POLITIQUE
    assert "éditeur hors liste maison" in leve.value.cause
    assert magasin.ecritures == 0
    # La candidature porte tout ce dont une politique a besoin, et **aucun
    # secret** : une entrée de bibliothèque est un gabarit `${VAR}`.
    (candidature,) = vues
    assert candidature.nom_amont == "io.github.alice/veille"
    assert candidature.version == "1.4.0"
    assert candidature.par == "alice"


def test_la_politique_par_defaut_est_nommee_a_l_ecran() -> None:
    etat = etat_politique(politique_ouverte)
    assert etat["defaut"] is True
    assert etat["nom"] == "politique_ouverte"

    def maison(candidature: Candidature) -> VerdictPolitique:
        return VerdictPolitique()

    assert etat_politique(maison)["defaut"] is False


@pytest.mark.parametrize(
    "reference",
    ["sans_deux_points", ":attribut", "module:", "maestro.agents.mcp_admission:inexistant"],
)
def test_une_politique_illisible_echoue_franchement(reference: str) -> None:
    """Une politique qu'on croit active et qui ne l'est pas est pire que pas de politique."""
    with pytest.raises(ValueError, match="politique d'admission MCP"):
        charger_politique(reference)


def test_une_politique_qui_existe_se_charge() -> None:
    politique = charger_politique("maestro.agents.mcp_admission:politique_ouverte")
    assert politique is politique_ouverte


# --------------------------------------------------------------------------- #
# ③ La révocation : elle retire sans démonter, et elle ne s'oublie pas
# --------------------------------------------------------------------------- #


def test_une_revocation_marque_sur_place_au_lieu_d_effacer(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    magasin = MagasinAdmissions(mcp_racine)
    service = ServiceAdmission(magasin)
    service.admettre(decouverte, par="alice")

    revoquee = service.revoquer("io-github-alice-veille", par="bob", motif="CVE ouverte")
    assert revoquee.active is False
    assert revoquee.revoquee_par == "bob"
    assert revoquee.motif == "CVE ouverte"
    # Toujours au journal : c'est ce qui permet de **nommer** ce qui s'est passé.
    assert [a.id for a in magasin.lister()] == ["io-github-alice-veille"]


def test_une_entree_revoquee_sort_de_l_allowlist_et_le_refus_la_nomme(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    magasin = MagasinAdmissions(mcp_racine)
    service = ServiceAdmission(magasin)
    service.admettre(decouverte, par="alice")
    service.revoquer("io-github-alice-veille", par="bob", motif="CVE ouverte")

    apres = RegistreMcp(
        [curee()], decouvertes=[decouverte], admissions=magasin.lister()
    )
    assert apres.get("io-github-alice-veille") is None
    cause = apres.cause_non_instanciable("io-github-alice-veille")
    # ⚠ L'ordre est le contenu de la décision : révoquée **avant** découverte.
    # « Personne ne l'a admise » serait exact et trompeur sur une entrée qu'on a
    # admise puis retirée.
    assert "révoquée" in cause
    assert "bob" in cause and "CVE ouverte" in cause
    assert "non admis" not in cause
    with pytest.raises(ValueError, match="révoquée"):
        apres.instancier("io-github-alice-veille")


def test_revoquer_ce_qui_n_a_jamais_ete_admis_est_refuse(racines) -> None:
    _, mcp_racine = racines
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    with pytest.raises(RefusAdmission) as leve:
        service.revoquer("curee-test")
    assert leve.value.motif == MOTIF_NON_ADMISE


def test_revoquer_deux_fois_est_refuse(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    service = ServiceAdmission(MagasinAdmissions(mcp_racine))
    service.admettre(decouverte, par="alice")
    service.revoquer("io-github-alice-veille", par="bob")
    with pytest.raises(RefusAdmission) as leve:
        service.revoquer("io-github-alice-veille", par="bob")
    assert leve.value.motif == MOTIF_DEJA_REVOQUEE


def test_un_journal_illisible_retire_de_l_allowlist_en_nommant_la_cause(racines) -> None:
    """Le repli le plus prudent, pas le plus commode — et il **se dit**."""
    _, mcp_racine = racines
    mcp_racine.mkdir(parents=True, exist_ok=True)
    (mcp_racine / "admissions.json").write_text("{ pas du JSON", encoding="utf-8")

    admissions, cause = lire_admissions(MagasinAdmissions(mcp_racine))
    assert admissions == ()
    assert "admissions MCP illisible" in cause

    federation = federer(
        MiroirAmont(mcp_racine / "vide"),
        entrees_curees=[curee()],
        magasin=MagasinAdmissions(mcp_racine),
    )
    assert federation.cause_admissions
    assert [e.id for e in federation.registre.lister()] == ["curee-test"]


def test_deux_admissions_de_meme_id_rendent_le_journal_invalide(racines) -> None:
    _, mcp_racine = racines
    _, decouverte = registre_avec_decouverte(mcp_racine)
    magasin = MagasinAdmissions(mcp_racine)
    doublon = Admission(id="io-github-alice-veille", entree=decouverte)
    with pytest.raises(ValueError, match="deux admissions"):
        magasin.ecrire([doublon, doublon])


# --------------------------------------------------------------------------- #
# ④ L'entrée admise est FIGÉE, et la veille dit ce qui a bougé
# --------------------------------------------------------------------------- #


def test_la_bibliotheque_sert_la_version_admise_et_non_celle_du_miroir(racines) -> None:
    """Sans ce figement, une nouvelle version amont changerait ce qu'on monte."""
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille", version="1.4.0")])
    magasin = MagasinAdmissions(mcp_racine)

    depart = federer(miroir, entrees_curees=[curee()], magasin=magasin)
    decouverte = depart.registre.trouver("io-github-alice-veille")
    assert decouverte is not None
    ServiceAdmission(magasin).admettre(decouverte, par="alice")

    # L'amont publie une version plus récente.
    poser_miroir(miroir_racine, [amont("io.github.alice/veille", version="2.0.0")])
    apres = federer(miroir, entrees_curees=[curee()], magasin=magasin)

    montable = apres.registre.get("io-github-alice-veille")
    assert montable is not None
    assert montable.version == "1.4.0", "la version admise fait foi"
    # La jumelle d'amont tombe sous la collision, avec **un autre mot** que « le
    # seed gagne » : la chercher au seed serait chercher ce qui n'y est pas.
    assert any("déjà admise" in cause for cause in apres.registre.decouvertes_ecartees)
    # Et l'écart est **dit** plutôt que tu.
    (signal,) = [s for s in apres.registre.signaux if s.genre == SIGNAL_VERSION]
    assert signal.version_amont == "2.0.0"
    assert "2.0.0" in signal.message and "1.4.0" in signal.message


@pytest.mark.parametrize(
    ("statut_amont", "genre"),
    [("deprecated", SIGNAL_DEPRECIEE), ("deleted", SIGNAL_SUPPRIMEE)],
)
def test_un_statut_amont_qui_bouge_est_signale_sans_rien_retirer(
    statut_amont: str, genre: str
) -> None:
    admission = Admission(
        id="io-github-alice-veille",
        entree=curee(id="io-github-alice-veille", curee=False),
        nom_amont="io.github.alice/veille",
        version="1.4.0",
        le="2026-08-01T00:00:00Z",
    )
    signaux = veiller(
        [admission],
        {"io.github.alice/veille": EtatAmontEntree(version="1.4.0", statut=statut_amont)},
    )
    (signal,) = signaux
    assert signal.genre == genre
    assert signal.statut_amont == statut_amont


def test_une_admise_disparue_du_miroir_est_signalee_et_reste_montable(racines) -> None:
    miroir_racine, mcp_racine = racines
    miroir = poser_miroir(miroir_racine, [amont("io.github.alice/veille")])
    magasin = MagasinAdmissions(mcp_racine)
    depart = federer(miroir, entrees_curees=[curee()], magasin=magasin)
    decouverte = depart.registre.trouver("io-github-alice-veille")
    assert decouverte is not None
    ServiceAdmission(magasin).admettre(decouverte, par="alice")

    poser_miroir(miroir_racine, [amont("io.github.bob/autre")])
    apres = federer(miroir, entrees_curees=[curee()], magasin=magasin)

    (signal,) = [s for s in apres.registre.signaux if s.genre == SIGNAL_DISPARUE]
    assert signal.id == "io-github-alice-veille"
    # Retirer d'office casserait un serveur monté sans le dire : la détection est
    # automatique, jamais le verdict.
    montable = apres.registre.get("io-github-alice-veille")
    assert montable is not None
    montable.vers_serveur()


def test_un_miroir_vide_ne_declare_aucune_admission_disparue() -> None:
    """Sinon un poste neuf alerterait sur **toutes** ses admissions à la fois."""
    admission = Admission(
        id="io-github-alice-veille",
        entree=curee(id="io-github-alice-veille", curee=False),
        nom_amont="io.github.alice/veille",
    )
    assert veiller([admission], {}) == ()
    assert veille_du_miroir([admission], []) == ()


def test_une_admission_revoquee_ne_produit_aucun_signal() -> None:
    revoquee = Admission(
        id="io-github-alice-veille",
        entree=curee(id="io-github-alice-veille", curee=False),
        nom_amont="io.github.alice/veille",
        revoquee_le="2026-08-20T00:00:00Z",
    )
    assert veiller([revoquee], {"autre": EtatAmontEntree()}) == ()


def test_une_admission_sans_nom_amont_est_sautee_plutot_que_declaree_disparue() -> None:
    """Journal écrit à la main, ou millésime antérieur : on ne crie pas au loup."""
    orpheline = Admission(id="io-github-alice-veille", entree=curee(id="io-github-alice-veille"))
    assert veiller([orpheline], {"io.github.bob/autre": EtatAmontEntree()}) == ()


def test_le_nom_amont_se_recompose_ou_se_replie_sur_le_nom_court() -> None:
    assert nom_amont_de(curee(id="x", nom="veille", editeur="io.github.alice")) == (
        "io.github.alice/veille"
    )
    assert nom_amont_de(curee(id="x", nom="veille", editeur="")) == "veille"


def test_les_signaux_sont_poses_sur_l_entree_admise_et_pas_ailleurs() -> None:
    decouverte = curee(id="io-github-alice-veille", curee=False)
    admission = Admission(id="io-github-alice-veille", entree=decouverte, nom_amont="a/b")
    signal = SignalAmont(id="io-github-alice-veille", genre=SIGNAL_DEPRECIEE, message="bougé")
    registre = RegistreMcp([curee()], admissions=[admission], signaux=[signal])

    admise = registre.get("io-github-alice-veille")
    assert admise is not None and admise.signaux == (signal,)
    assert registre.get("curee-test").signaux == ()
    assert registre.signaux_de("io-github-alice-veille") == (signal,)


# --------------------------------------------------------------------------- #
# ⑤ Le garde-fou, aux DEUX bouts (critère d'acceptation du ticket)
# --------------------------------------------------------------------------- #


def test_instancier_refuse_une_decouverte_et_nomme_le_geste_qui_manque(racines) -> None:
    _, mcp_racine = racines
    registre, _ = registre_avec_decouverte(mcp_racine)

    # Elle est **visible** — c'est tout l'intérêt de la fédération…
    assert registre.trouver("io-github-alice-veille") is not None
    assert registre.rechercher("veille")
    # …et elle n'est **pas montable**.
    assert registre.get("io-github-alice-veille") is None
    with pytest.raises(ValueError) as leve:
        registre.instancier("io-github-alice-veille")
    cause = str(leve.value)
    assert "non admis" in cause
    assert "POST /api/mcp/admissions" in cause
    assert "docs/19" in cause
    assert "1.4.0" in cause


def test_un_id_inconnu_et_une_decouverte_ne_se_refusent_pas_avec_les_memes_mots(
    racines,
) -> None:
    """Le « hors allowlist » unique d'avant les confondait, et faisait de la
    découverte un cul-de-sac : rien ne disait qu'il existait une porte."""
    _, mcp_racine = racines
    registre, _ = registre_avec_decouverte(mcp_racine)

    decouverte = registre.cause_non_instanciable("io-github-alice-veille")
    inconnu = registre.cause_non_instanciable("jamais-vu")
    assert decouverte != inconnu
    assert "hors allowlist" in inconnu
    assert "POST /api/mcp/admissions" not in inconnu


@pytest.fixture()
def client_federe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    """Une Control Tower dont les **trois disques** MCP sont jetables.

    Aucun registre injecté : c'est la fédération réelle qui compose, donc ce que
    la route refuse est ce que le produit refuse. Un registre injecté serait une
    allowlist figée — utile ailleurs, inutile ici, où l'on veut précisément
    éprouver l'effet d'une écriture du journal sur la requête suivante.
    """
    monkeypatch.setenv("MAESTRO_MCP_DIR", str(tmp_path / "mcp"))
    monkeypatch.setenv("MAESTRO_MCP_AMONT_DIR", str(tmp_path / "amont"))
    monkeypatch.setenv("MAESTRO_SECRETS_DIR", str(tmp_path / "secrets"))
    poser_miroir(
        tmp_path / "amont",
        [amont("io.github.alice/veille", version="1.4.0", depot="https://github.com/alice/veille")],
    )
    oublier_memo()
    with TestClient(create_app(bus=InMemoryEventBus())) as client:
        yield client
    oublier_memo()


def test_l_api_sert_la_decouverte_en_la_marquant_non_curee(client_federe: TestClient) -> None:
    reponse = client_federe.get("/api/mcp/registre", params={"source": "decouverte"})
    assert reponse.status_code == 200
    (entree,) = [e for e in reponse.json() if e["id"] == "io-github-alice-veille"]
    assert entree["curee"] is False
    assert entree["source"] == SOURCE_DECOUVERTE
    assert entree["version"] == "1.4.0"
    assert entree["depot"] == "https://github.com/alice/veille"
    assert entree["admission"] is None


def test_post_pool_refuse_une_decouverte_non_admise(client_federe: TestClient) -> None:
    """Le second bout du garde-fou — la route refuse **avant** d'instancier."""
    reponse = client_federe.post(
        "/api/mcp/pool", json={"registre_id": "io-github-alice-veille", "secrets": []}
    )
    assert reponse.status_code == 404
    detail = reponse.json()["detail"]
    assert "non admis" in detail
    assert "POST /api/mcp/admissions" in detail
    # Rien n'a été monté : le pool est resté vide.
    assert client_federe.get("/api/mcp/pool").json()["integrations"] == []


def test_le_parcours_entier_de_la_porte_est_traversable(
    client_federe: TestClient, tmp_path: Path
) -> None:
    """Découverte → refus → admission → montage → révocation → refus, en un seul fil.

    C'est la promesse du parent : *fédérer la découverte sans fédérer
    l'installation*. Chaque marche est vérifiée sur la **même** entrée, parce que
    ce qui compte n'est pas que chaque route réponde bien isolément mais que le
    passage de l'une à l'autre change réellement ce qui est montable.
    """
    refus = client_federe.post(
        "/api/mcp/pool", json={"registre_id": "io-github-alice-veille", "secrets": []}
    )
    assert refus.status_code == 404

    admise = client_federe.post(
        "/api/mcp/admissions",
        json={"registre_id": "io-github-alice-veille", "par": "alice", "note": "revue faite"},
    )
    assert admise.status_code == 201
    corps = admise.json()
    assert corps["curee"] is True
    assert corps["source"] == SOURCE_ADMISE
    assert corps["admission"]["par"] == "alice"
    assert corps["admission"]["version"] == "1.4.0"
    assert corps["admission"]["amont"] == AMONT
    assert corps["admission"]["miroir_le"] == "2026-08-28T06:00:00Z"

    monte = client_federe.post(
        "/api/mcp/pool", json={"registre_id": "io-github-alice-veille", "secrets": []}
    )
    assert monte.status_code == 201
    assert [i["id"] for i in client_federe.get("/api/mcp/pool").json()["integrations"]] == [
        "io-github-alice-veille"
    ]

    revocation = client_federe.post(
        "/api/mcp/admissions/io-github-alice-veille/revocation",
        json={"par": "bob", "motif": "CVE ouverte"},
    )
    assert revocation.status_code == 200
    # ⚠ Rien n'est démonté : « jamais sans le dire », pas « jamais sans casser ».
    assert revocation.json()["pool"]["montee"] is True
    assert [i["id"] for i in client_federe.get("/api/mcp/pool").json()["integrations"]] == [
        "io-github-alice-veille"
    ]

    apres = client_federe.post(
        "/api/mcp/pool", json={"registre_id": "io-github-alice-veille", "secrets": []}
    )
    assert apres.status_code == 404
    assert "révoquée" in apres.json()["detail"]
    assert "bob" in apres.json()["detail"]


def test_admettre_un_id_inconnu_de_la_bibliotheque_est_un_404(
    client_federe: TestClient,
) -> None:
    """Une entrée que la traduction a refusée n'y figure pas, donc n'est pas admissible."""
    reponse = client_federe.post(
        "/api/mcp/admissions", json={"registre_id": "jamais-traduite", "par": "alice"}
    )
    assert reponse.status_code == 404


def test_admettre_une_entree_curee_du_seed_est_un_409(client_federe: TestClient) -> None:
    reponse = client_federe.post(
        "/api/mcp/admissions", json={"registre_id": "github", "par": "alice"}
    )
    assert reponse.status_code == 409
    assert "déjà curé" in reponse.json()["detail"]


def test_le_journal_des_admissions_se_lit_avec_sa_politique(client_federe: TestClient) -> None:
    client_federe.post(
        "/api/mcp/admissions", json={"registre_id": "io-github-alice-veille", "par": "alice"}
    )
    corps = client_federe.get("/api/mcp/admissions").json()
    assert [a["id"] for a in corps["admissions"]] == ["io-github-alice-veille"]
    assert corps["revoquees"] == []
    assert corps["erreur"] is None
    # Une porte dont on ignore le gardien n'en est pas une.
    assert corps["politique"]["defaut"] is True


def test_la_provenance_rend_les_trois_sources_cote_a_cote(client_federe: TestClient) -> None:
    corps = client_federe.get("/api/mcp/registre/provenance").json()
    sources = {p["source"]: p for p in corps["provenances"]}
    assert set(sources) == {SOURCE_CUREE, SOURCE_ADMISE, SOURCE_DECOUVERTE}
    assert sources[SOURCE_DECOUVERTE]["moissonnee"] is True
    assert sources[SOURCE_DECOUVERTE]["amont"] == AMONT
    assert corps["total_decouvertes"] == 1
    assert corps["total_admises"] == 0
    # La phrase de la source curée ne prétend plus que rien n'est moissonné, et
    # ne se dit plus seule instanciable — la porte d'admission existe (#679).
    assert "jamais moissonn" not in corps["resume"]
    assert "elle seule" not in corps["resume"]


def test_une_admission_deplace_les_compteurs_de_provenance(client_federe: TestClient) -> None:
    avant = client_federe.get("/api/mcp/registre/provenance").json()
    client_federe.post(
        "/api/mcp/admissions", json={"registre_id": "io-github-alice-veille", "par": "alice"}
    )
    apres = client_federe.get("/api/mcp/registre/provenance").json()

    assert apres["total_admises"] == avant["total_admises"] + 1
    assert apres["total_decouvertes"] == avant["total_decouvertes"] - 1
    # `total_curees` compte le **seed seul** : une admise n'y entre pas.
    assert apres["total_curees"] == avant["total_curees"]


def test_le_pool_reste_intact_quand_le_registre_refuse(
    client_federe: TestClient, tmp_path: Path
) -> None:
    """Un refus d'allowlist n'écrit rien — ni pool, ni journal, ni secret."""
    store = McpStore(tmp_path / "mcp")
    client_federe.post(
        "/api/mcp/pool", json={"registre_id": "io-github-alice-veille", "secrets": []}
    )
    assert store.pool() == ()
    assert not (tmp_path / "mcp" / "admissions.json").exists()
