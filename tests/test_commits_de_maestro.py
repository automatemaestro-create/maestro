"""Les commits que Maestro écrit lui-même suivent la convention du projet (#1507).

Sur p5 (bilan du 2026-10-14, critère C2), la personne avait choisi Conventional
Commits ; les 33 commits du livrable étaient « Maestro : travail de la tâche (…) » et
« Maestro : application de … ». Ces tests gardent la moitié du remède qui revient à
Maestro : ses propres messages (`maestro.projets.commits`), rédigés par le modèle dans
la convention dite, vérifiés, et jamais une cause d'échec.

Aucun réseau ni modèle : un double de fournisseur, qui retient ce qu'on lui a dit.
"""

from __future__ import annotations

import asyncio

import pytest

from maestro.projets.commits import (
    COMMITS_MONTRES,
    SUJET_MAX,
    SYSTEME,
    RedacteurDeCommits,
    prompt_du_message,
    sujet_lisible,
)
from maestro.providers.base import ModelProvider


class _Modele(ModelProvider):
    """Un modèle doublé : rend sa réponse dictée, ou lève — et retient chaque appel."""

    name = "modele-double"

    def __init__(self, reponse: str = "", *, panne: Exception | None = None) -> None:
        self.reponse = reponse
        self.panne = panne
        self.appels: list[tuple[str, str, str | None]] = []

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt: str, *, model: str, system_prompt: str | None = None) -> str:
        self.appels.append((prompt, model, system_prompt))
        if self.panne is not None:
            raise self.panne
        return self.reponse


def _message(modele: _Modele, **kwargs: object) -> str:
    redacteur = RedacteurDeCommits(modele, modele="modele-du-test")
    arguments = {
        "convention": "Conventional Commits",
        "changement": "fusionne la page des favoris",
        **kwargs,
    }
    return asyncio.run(redacteur.message(**arguments))  # type: ignore[arg-type]


def test_le_sujet_est_redige_dans_la_convention_par_le_modele() -> None:
    modele = _Modele("feat(favoris): intègre la page des favoris")

    assert _message(modele) == "feat(favoris): intègre la page des favoris"
    ((prompt, model, systeme),) = modele.appels
    assert systeme == SYSTEME
    assert model == "modele-du-test"
    assert "<convention>Conventional Commits</convention>" in prompt
    assert "<commit>fusionne la page des favoris</commit>" in prompt


def test_les_commits_de_la_branche_disent_la_forme_et_la_langue() -> None:
    """Le modèle voit ce que l'agent a écrit : même langue, même forme."""
    modele = _Modele("feat: merge the favorites page")
    sujets = [f"feat: commit {rang}" for rang in range(COMMITS_MONTRES + 5)]

    _message(modele, commits=sujets)

    ((prompt, _, _),) = modele.appels
    assert "feat: commit 0" in prompt
    assert f"feat: commit {COMMITS_MONTRES - 1}" in prompt
    assert f"feat: commit {COMMITS_MONTRES}" not in prompt


def test_sans_convention_le_modele_n_est_pas_appele() -> None:
    """Rien ne change sans convention : le message de Maestro, au caractère près."""
    modele = _Modele("feat: x")

    assert _message(modele, convention="  ") == ""
    assert modele.appels == []


def test_un_modele_en_panne_rend_vide_sans_lever() -> None:
    """Un commit n'échoue jamais faute de message : l'appelant garde le sien."""
    assert _message(_Modele(panne=RuntimeError("fournisseur injoignable"))) == ""


@pytest.mark.parametrize(
    ("reponse", "sujet"),
    [
        ("feat: ajoute la page", "feat: ajoute la page"),
        ("```\nfeat: ajoute la page\n```", "feat: ajoute la page"),
        ("« feat: ajoute la page »", "feat: ajoute la page"),
        ("`feat: ajoute la page`", "feat: ajoute la page"),
        ("feat: ajoute la page\n\nCe commit ajoute la page.", "feat: ajoute la page"),
        ("", ""),
        ("   \n  ", ""),
        ("x" * (SUJET_MAX + 1), ""),
    ],
)
def test_seul_un_sujet_d_une_ligne_et_borne_est_retenu(reponse: str, sujet: str) -> None:
    assert sujet_lisible(reponse) == sujet


def test_le_prompt_ne_montre_pas_de_branche_vide() -> None:
    prompt = prompt_du_message("gitmoji", "ajoute AGENTS.md", ["", "  "])
    assert "<commits>" not in prompt
