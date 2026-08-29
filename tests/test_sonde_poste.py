"""La sonde du poste et le catalogue qu'elle éclaire (#487).

Quatre promesses, une section chacune :

1. la sonde **rend ce qui est là** — CLI résolus, serveur local qui répond,
   clés présentes — avec ce qui rend chacun utilisable ou non ;
2. elle est **gratuite et sans effet de bord** : rien n'est exécuté, rien n'est
   écrit, aucun endpoint distant n'est joint, et un poste nu rend une liste vide
   sans erreur ;
3. ce qu'elle trouve **alimente le catalogue** des fournisseurs au lieu d'ouvrir
   une seconde source — donc la route `GET /api/fournisseurs`, où le registre du
   code et le poste sont **deux colonnes d'une même charge** ;
4. ce qu'elle **ne peut pas savoir est dit** : la validité d'une clé, la version
   d'un binaire, et le fait qu'un `PATH` de process n'est pas celui d'un shell.

Aucun test n'ouvre de socket ni ne lit un vrai `PATH` : les trois seams de la
sonde (`resolveur`, `environ`, `lecteur`) sont injectés, ce qu'exige
`tests/conftest.py` (#195). Le seul appel réseau **réel** du module est
`lire_locale`, exercé ici contre un port fermé — vérifier qu'une panne réseau
devient un résultat et non une exception ne se simule pas.
"""

from __future__ import annotations

import asyncio
import json
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from maestro.controltower.app import create_app
from maestro.controltower.fournisseurs import catalogue
from maestro.poste import (
    GENRE_CLE,
    GENRE_CLI,
    GENRE_SERVEUR,
    INCERTITUDE_PATH,
    OLLAMA_DEFAUT,
    RapportSonde,
    ReponseLocale,
    SondePoste,
    lire_locale,
)

# --- Harnais ---------------------------------------------------------------


def _resolveur(*presents: str):
    """Un `PATH` décrit : seuls les noms cités se résolvent."""
    connus = {nom: f"/opt/bin/{nom}" for nom in presents}

    def resoudre(commande: str) -> str | None:
        return connus.get(commande)

    return resoudre


def _lecteur(reponses: Mapping[str, ReponseLocale]):
    """Une boucle locale décrite : hors des URL citées, rien n'écoute.

    Note le nombre d'appels : c'est ce qui prouve qu'un endpoint distant n'est
    **pas** joint, et non la seule absence de son constat.
    """
    appels: list[str] = []

    async def lire(url: str, _delai: float) -> ReponseLocale:
        appels.append(url)
        return reponses.get(url, ReponseLocale(joignable=False, erreur="connexion refusée"))

    lire.appels = appels  # type: ignore[attr-defined]
    return lire


def _tags(*modeles: str) -> ReponseLocale:
    """La réponse d'un Ollama qui va bien."""
    corps = json.dumps({"models": [{"name": m} for m in modeles]})
    return ReponseLocale(joignable=True, statut=200, corps=corps)


def _sonde(
    *,
    presents: tuple[str, ...] = (),
    environ: Mapping[str, str] | None = None,
    reponses: Mapping[str, ReponseLocale] | None = None,
) -> SondePoste:
    return SondePoste(
        resolveur=_resolveur(*presents),
        environ=environ if environ is not None else {},
        lecteur=_lecteur(reponses or {}),
    )


def _rapport(sonde: SondePoste) -> RapportSonde:
    return asyncio.run(sonde.rapport())


def _par_cle(rapport: RapportSonde, cle: str) -> Any:
    return next(c for c in rapport.constats if c.cle == cle)


# --- ① Ce que la sonde rend ------------------------------------------------


def test_un_cli_resolu_sur_le_path_devient_un_constat_utilisable() -> None:
    rapport = _rapport(_sonde(presents=("claude",)))

    constat = _par_cle(rapport, "cli:claude")
    assert constat.genre == GENRE_CLI
    assert constat.fournisseur == "claude"
    assert constat.utilisable is True
    assert constat.origine == "/opt/bin/claude"


def test_un_serveur_local_qui_repond_rend_ses_modeles() -> None:
    sonde = _sonde(reponses={f"{OLLAMA_DEFAUT}/api/tags": _tags("qwen2.5:3b", "llama3:8b")})

    constat = _par_cle(_rapport(sonde), "serveur:ollama")
    assert constat.genre == GENRE_SERVEUR
    assert constat.fournisseur == "openai"
    assert constat.utilisable is True
    assert constat.modeles == ("qwen2.5:3b", "llama3:8b")


def test_une_cle_presente_dans_l_environnement_devient_un_constat() -> None:
    rapport = _rapport(_sonde(environ={"OPENAI_API_KEY": "sk-secrete"}))

    constat = _par_cle(rapport, f"{GENRE_CLE}:OPENAI_API_KEY")
    assert constat.fournisseur == "openai"
    assert constat.utilisable is True


def test_aucune_valeur_de_secret_ne_quitte_la_sonde() -> None:
    # Le seul vrai risque de ce module : rendre la clé avec sa présence. Le
    # constat porte le **nom** de la variable, jamais ce qu'elle contient.
    rapport = _rapport(
        _sonde(environ={"ANTHROPIC_API_KEY": "sk-ant-ne-doit-jamais-sortir"})
    )

    assert "sk-ant-ne-doit-jamais-sortir" not in json.dumps(catalogue(rapport))


def test_le_port_d_ollama_suit_OLLAMA_HOST() -> None:
    # #113 : le serveur local est un cas valide du catalogue, et son port n'est
    # pas toujours celui du défaut. `OLLAMA_HOST` est la convention d'Ollama
    # lui-même — pas une variable que Maestro invente.
    sonde = _sonde(
        environ={"OLLAMA_HOST": "127.0.0.1:9999"},
        reponses={"http://127.0.0.1:9999/api/tags": _tags("phi4")},
    )

    assert _par_cle(_rapport(sonde), "serveur:ollama").modeles == ("phi4",)


def test_un_serveur_qui_ecoute_sans_repondre_est_present_mais_pas_utilisable() -> None:
    # Le cas dégradé que le ticket nomme : la panne n'est pas « absent », elle
    # est « là, et muet ». Les confondre ferait chercher une installation
    # manquante là où il y a un service à redémarrer.
    sonde = _sonde(
        reponses={
            f"{OLLAMA_DEFAUT}/api/tags": ReponseLocale(
                joignable=True, erreur="délai dépassé sans réponse"
            )
        }
    )

    constat = _par_cle(_rapport(sonde), "serveur:ollama")
    assert constat.utilisable is False
    assert "ne répond pas" in constat.detail


def test_un_serveur_hors_dialecte_n_est_pas_un_serveur_sans_modele() -> None:
    sonde = _sonde(
        reponses={
            f"{OLLAMA_DEFAUT}/api/tags": ReponseLocale(
                joignable=True, statut=200, corps="<html>bienvenue</html>"
            )
        }
    )

    constat = _par_cle(_rapport(sonde), "serveur:ollama")
    assert constat.utilisable is False
    assert constat.modeles == ()


# --- ② Gratuite et sans effet de bord --------------------------------------


def test_un_poste_nu_rend_une_liste_vide_sans_erreur() -> None:
    rapport = _rapport(_sonde())

    assert rapport.constats == ()
    # Le rapport reste renseigné : « rien trouvé » n'est pas « rien installé ».
    assert rapport.incertitudes


def test_aucun_binaire_n_est_execute(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prouver le motif avant de conclure : on casse `subprocess` et `os.system`
    # sous les pieds de la sonde. Sans cette moitié, « la sonde n'exécute rien »
    # serait un ✓ sur une question jamais posée.
    import subprocess

    def interdit(*_a: object, **_k: object) -> None:
        raise AssertionError("la sonde a lancé un processus")

    monkeypatch.setattr(subprocess, "run", interdit)
    monkeypatch.setattr(subprocess, "Popen", interdit)
    monkeypatch.setattr(subprocess, "check_output", interdit)

    rapport = _rapport(_sonde(presents=("claude", "codex")))

    assert {c.cle for c in rapport.constats} == {"cli:claude", "cli:codex"}


def test_la_sonde_n_ecrit_rien(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    _rapport(_sonde(presents=("claude",), environ={"OPENAI_API_KEY": "sk"}))

    assert list(tmp_path.iterdir()) == []


def test_un_endpoint_distant_est_nomme_mais_jamais_joint() -> None:
    lecteur = _lecteur({})
    sonde = SondePoste(
        resolveur=_resolveur(),
        environ={"OPENAI_BASE_URL": "https://api.mistral.ai/v1"},
        lecteur=lecteur,
    )

    constat = _par_cle(_rapport(sonde), "serveur:openai-configure")
    assert constat.utilisable is False
    assert "distant" in constat.detail
    # Le fait, pas seulement l'apparence : aucune requête n'est partie vers lui.
    assert lecteur.appels == [f"{OLLAMA_DEFAUT}/api/tags"]  # type: ignore[attr-defined]


def test_un_ollama_declare_hors_du_poste_est_nomme_mais_jamais_joint() -> None:
    # « Seule la boucle locale est sondée » est une promesse du module : une
    # exception pour un fournisseur la rendrait fausse pour tous.
    lecteur = _lecteur({})
    sonde = SondePoste(
        resolveur=_resolveur(),
        environ={"OLLAMA_HOST": "http://ollama.interne:11434"},
        lecteur=lecteur,
    )

    constat = _par_cle(_rapport(sonde), "serveur:ollama")
    assert constat.utilisable is False
    assert "OLLAMA_HOST" in constat.detail
    assert lecteur.appels == []  # type: ignore[attr-defined]


def test_un_meme_serveur_ecrit_deux_fois_ne_compte_qu_une_fois() -> None:
    # `http://localhost:11434/v1` et le défaut `http://127.0.0.1:11434` sont le
    # même service : deux entrées pour un serveur donneraient à l'utilisateur
    # deux choses à configurer là où il n'y en a qu'une.
    sonde = _sonde(
        environ={"OPENAI_BASE_URL": "http://localhost:11434/v1"},
        reponses={f"{OLLAMA_DEFAUT}/api/tags": _tags("qwen2.5:3b")},
    )

    rapport = _rapport(sonde)
    assert [c.cle for c in rapport.constats] == ["serveur:ollama"]


def test_un_port_ferme_rend_injoignable_et_non_une_exception() -> None:
    # Le seul appel réseau non simulé de la suite, et il vise un port fermé de la
    # boucle locale : c'est la promesse « un poste sans rien rend une liste vide
    # sans erreur » là où elle peut casser pour de vrai.
    #
    # ⚠ Ce test a **trouvé un défaut** plutôt que de confirmer une évidence :
    # un port fermé se refuse franchement sous Linux mais **expire à la
    # connexion** sous Windows, et `TimeoutException` était rangé sous « écoute
    # sans répondre ». Un Ollama fantôme serait apparu sur toute machine qui n'en
    # a pas — d'où le partage `ConnectTimeout` / `ReadTimeout` de `lire_locale`.
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]

    reponse = asyncio.run(lire_locale(f"http://127.0.0.1:{port}/api/tags", 0.5))

    assert reponse.joignable is False
    assert reponse.erreur


def test_un_port_ferme_ne_fabrique_aucun_serveur_dans_le_rapport() -> None:
    # La conséquence de ce qui précède, vue du rapport : c'est elle qui compte.
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]

    sonde = SondePoste(
        resolveur=_resolveur(), environ={"OLLAMA_HOST": f"127.0.0.1:{port}"}, delai_s=0.5
    )

    assert _rapport(sonde).constats == ()


# --- ③ Une seule source : le catalogue -------------------------------------


def test_le_catalogue_part_du_registre_et_non_du_poste() -> None:
    # Un poste nu ne vide pas le catalogue : Maestro supporte ce qu'il supporte,
    # que la machine l'arme ou non. C'est la colonne que la sonde n'écrit pas.
    vue = catalogue(_rapport(_sonde()))

    assert [f["nom"] for f in vue["fournisseurs"]] == ["claude", "openai"]
    assert all(f["supporte"] and not f["present_ici"] for f in vue["fournisseurs"])


def test_le_catalogue_distingue_supporte_et_present_ici() -> None:
    sonde = _sonde(
        presents=("claude",),
        reponses={f"{OLLAMA_DEFAUT}/api/tags": _tags("qwen2.5:3b")},
    )

    vue = catalogue(_rapport(sonde))
    par_nom = {f["nom"]: f for f in vue["fournisseurs"]}
    assert par_nom["claude"]["present_ici"] is True
    assert par_nom["openai"]["modeles_ici"] == ["qwen2.5:3b"]


def test_un_outil_non_supporte_est_montre_hors_registre_jamais_propose() -> None:
    # docs/34 a décidé de ne pas brancher les agents CLI tiers. Les taire ferait
    # croire qu'ils ne sont pas là ; les ranger parmi les fournisseurs les
    # ferait proposer comme s'ils marchaient.
    vue = catalogue(_rapport(_sonde(presents=("gemini",))))

    assert [c["cle"] for c in vue["hors_registre"]] == ["cli:gemini"]
    assert all(f["present_ici"] is False for f in vue["fournisseurs"])


def test_la_route_sert_le_catalogue_de_la_sonde_injectee() -> None:
    sonde = _sonde(
        presents=("claude", "codex"),
        environ={"ANTHROPIC_API_KEY": "sk-ant"},
        reponses={f"{OLLAMA_DEFAUT}/api/tags": _tags("qwen2.5:3b")},
    )
    with TestClient(create_app(sonde_poste=sonde)) as client:
        reponse = client.get("/api/fournisseurs")

    assert reponse.status_code == 200
    charge = reponse.json()
    par_nom = {f["nom"]: f for f in charge["fournisseurs"]}
    assert par_nom["claude"]["utilisable_ici"] is True
    assert par_nom["openai"]["modeles_ici"] == ["qwen2.5:3b"]
    assert [c["cle"] for c in charge["hors_registre"]] == ["cli:codex"]


def test_la_route_repond_200_sur_un_poste_nu() -> None:
    with TestClient(create_app(sonde_poste=_sonde())) as client:
        reponse = client.get("/api/fournisseurs")

    assert reponse.status_code == 200
    assert reponse.json()["hors_registre"] == []
    assert all(not f["present_ici"] for f in reponse.json()["fournisseurs"])


def test_le_catalogue_est_la_seule_route_du_sujet() -> None:
    # Le critère 3 en une assertion : la détection *alimente* le catalogue. Une
    # seconde route « ce que le poste a » serait la double source qu'il interdit.
    with TestClient(create_app(sonde_poste=_sonde())) as client:
        chemins = {
            route.path  # type: ignore[attr-defined]
            for route in client.app.routes  # type: ignore[attr-defined]
            if getattr(route, "path", "").startswith("/api/")
        }

    assert "/api/fournisseurs" in chemins
    assert not [c for c in chemins if "poste" in c or "sonde" in c]


# --- ④ Ce qu'elle ne peut pas savoir ---------------------------------------


def test_une_cle_presente_ne_pretend_pas_etre_valide() -> None:
    rapport = _rapport(_sonde(environ={"ANTHROPIC_API_KEY": "sk-ant"}))

    incertitude = _par_cle(rapport, f"{GENRE_CLE}:ANTHROPIC_API_KEY").incertitude
    assert incertitude is not None
    assert "valide" in incertitude and "facturé" in incertitude


def test_la_version_d_un_binaire_est_dite_inconnue_plutot_que_devinee() -> None:
    incertitude = _par_cle(_rapport(_sonde(presents=("claude",))), "cli:claude").incertitude

    assert incertitude is not None
    assert "version inconnue" in incertitude


def test_le_rapport_dit_que_le_path_du_process_n_est_pas_celui_du_shell() -> None:
    # La panne déjà payée par `scripts/mcp/playwright-mcp.mjs` : « rien trouvé »
    # peut vouloir dire « pas sur CE `PATH` ». Sans cette phrase, une absence se
    # lirait comme un constat.
    assert INCERTITUDE_PATH in _rapport(_sonde()).incertitudes


def test_les_incertitudes_remontent_jusqu_a_la_charge_de_la_route() -> None:
    with TestClient(create_app(sonde_poste=_sonde())) as client:
        charge = client.get("/api/fournisseurs").json()

    assert INCERTITUDE_PATH in charge["incertitudes"]
