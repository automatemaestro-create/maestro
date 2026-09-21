"""Le runtime outillé se dérive de la **fiche** de l'agent (#1037).

Lot final du chantier #1021 (#1043) — le lot 1 avait livré sans tests, par la
convention de découpage ([docs/10 §5.1](../docs/10-workflow-git.md)).

Ce que ces tests tiennent, et pourquoi c'est ce point-là qui compte : avant
#1037, l'outillage était le privilège de cinq noms écrits dans le code, et un
agent **défini par sa fiche** ne travaillait qu'en texte — ni fichiers, ni
commandes. Une équipe dérivée d'un projet ([docs/37](../docs/37-decision-equipe-sur-mesure.md))
ne pouvait pas exister sous ce verrou. La dérivation a donc changé de sens, et
c'est *ce* sens qu'on garde ici :

① **le métier vient de la fiche** — nom, rôle, modèle, effort —, si bien qu'une
   surcharge de réglages (#259) atteint désormais l'exécution outillée ;
② **le cadre vient du code quand il en déclare un**, du cadre générique sinon —
   et le générique outille autant : mêmes outils, même espace de travail ;
③ **un playbook de fiche reçoit le cadre d'exécution en plus**. C'est la garde
   du module : sans lui, un agent outillé pour la première fois répondrait en
   texte avec des outils dans les mains, et rendrait un livrable **vide** ;
④ **il n'y a plus deux chemins** : les rôles du code passent par le même, et
   `default_runtimes` n'est qu'un instantané de ces rôles-là.

Aucun appel modèle : `profil_outille` et `playbook_outille` sont purs, et les
runtimes ne sont ici que construits.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT, Agent
from maestro.agents.fiche_outillee import (
    CADRE_GENERIQUE,
    CADRES_DU_CODE,
    default_runtimes,
    playbook_outille,
    profil_outille,
    runtime_outille,
)
from maestro.agents.playbook_du_code import cadre_outille, playbook_du_code, roles_du_code
from maestro.agents.playbooks import PlaybookStore
from maestro.agents.runtime import DEFAULT_TOOLS
from maestro.agents.store import AgentDefinition
from maestro.providers.base import ModelProvider


class _Muet(ModelProvider):
    """Fournisseur qui n'exécute rien — on ne construit que des runtimes ici."""

    name = "muet"

    def supports(self, model: str) -> bool:
        return True

    async def generate(self, prompt, *, model, system_prompt=None):  # pragma: no cover
        raise AssertionError("aucune exécution ne doit partir de ces tests")


def _fiche(nom: str = "analyste", **extra) -> Agent:
    """Une fiche d'agent **hors du code** : ce que #1037 a rendu outillable."""
    base = {
        "nom": nom,
        "role": "Analyste",
        "competences": frozenset({"analyse"}),
        "modele": "un-modele",
        "prompt_systeme": "Tu es analyste. Tu ne sais rien d'un répertoire de travail.",
    }
    return Agent(**{**base, **extra})


# --- ① Le métier vient de la fiche -------------------------------------------


def test_le_profil_outille_prend_son_identite_sur_la_fiche() -> None:
    profil = profil_outille(_fiche(modele="modele-de-la-fiche", effort="high"))

    assert profil.nom == "analyste"
    assert profil.role == "Analyste"
    assert profil.modele == "modele-de-la-fiche"
    assert profil.effort == "high"


def test_un_reglage_de_modele_pose_sur_la_fiche_atteint_l_execution_outillee() -> None:
    """L'effet de bord voulu de #1037 : une surcharge (#259) recouvrait le seul
    chemin texte, elle recouvre désormais aussi le chemin outillé — parce que le
    modèle est pris **sur la fiche**, et qu'il n'y a plus qu'un chemin."""
    nu = profil_outille(_fiche())
    surcharge = profil_outille(_fiche(modele="modele-surcharge", effort="low"))

    assert nu.modele == "un-modele"
    assert (surcharge.modele, surcharge.effort) == ("modele-surcharge", "low")


def test_une_definition_persistee_donne_un_profil_outille() -> None:
    """Le chemin complet du ticket : une fiche **stockée** (#72) devient un profil
    outillé, sans rien demander au code."""
    definition = AgentDefinition(
        nom="analyste",
        role="Analyste",
        competences=("analyse",),
        playbook="Tu analyses.",
        modele="modele-stocke",
    )

    profil = profil_outille(definition.to_agent())

    assert profil.nom == "analyste"
    assert profil.modele == "modele-stocke"
    assert profil.outils == DEFAULT_TOOLS


# --- ② Le cadre : du code s'il en déclare un, générique sinon -----------------


def test_une_fiche_hors_du_code_prend_le_cadre_generique_et_ses_outils() -> None:
    profil = profil_outille(_fiche())

    assert profil.outils == DEFAULT_TOOLS
    assert profil.intro_tache == CADRE_GENERIQUE.intro_tache
    assert profil.consignes == CADRE_GENERIQUE.consignes
    assert profil.consigne_finale == CADRE_GENERIQUE.consigne_finale


def test_le_cadre_generique_ne_dit_rien_du_metier_mais_tout_de_l_execution() -> None:
    """Ce que le générique doit porter : le répertoire courant et le livrable en
    fichiers. Le métier, lui, est dans le playbook de la fiche — l'y redire ferait
    deux sources pour la même consigne."""
    assert "répertoire courant" in CADRE_GENERIQUE.consignes
    assert "fichiers du livrable" in CADRE_GENERIQUE.consignes
    assert CADRE_GENERIQUE.role == ""
    assert CADRE_GENERIQUE.prompt_systeme == ""


def test_chaque_agent_hors_du_code_a_son_prefixe_d_espace_de_travail() -> None:
    """Un préfixe par agent, pas un préfixe commun : c'est lui qui rend un espace
    de travail attribuable à l'œil nu."""
    premier = profil_outille(_fiche("analyste"))
    second = profil_outille(_fiche("redacteur"))

    assert premier.workspace_prefix == "maestro-analyste-"
    assert second.workspace_prefix == "maestro-redacteur-"
    assert premier.workspace_prefix != CADRE_GENERIQUE.workspace_prefix


@pytest.mark.parametrize("nom", sorted(CADRES_DU_CODE))
def test_un_role_du_code_garde_le_cadre_que_le_paquet_declare(nom: str) -> None:
    """Les cinq rôles du code passent par le même chemin, et leur comportement ne
    bouge pas : c'est le chemin qui a changé, pas ce qu'ils disent."""
    declare = CADRES_DU_CODE[nom]
    profil = profil_outille(_fiche(nom, role=declare.role))

    assert profil.intro_tache == declare.intro_tache
    assert profil.consignes == declare.consignes
    assert profil.workspace_prefix == declare.workspace_prefix


# --- ③ Le playbook d'une fiche reçoit le cadre d'exécution -------------------


def test_le_playbook_d_une_fiche_recoit_le_cadre_d_execution_en_plus() -> None:
    """La garde du module. Sans ce cadre, l'agent garderait un playbook qui ne lui
    parle ni de son répertoire ni de son livrable en fichiers : il répondrait en
    texte avec des outils dans les mains, et rendrait **moins** que le chemin
    texte qu'on vient de lui retirer."""
    fiche = _fiche()

    prompt = playbook_outille(fiche)

    assert prompt.startswith(fiche.prompt_systeme)
    assert cadre_outille() in prompt


@pytest.mark.parametrize("nom", sorted(roles_du_code()))
def test_un_role_du_code_sert_le_document_du_paquet(nom: str) -> None:
    """Pour eux, le document du paquet **remplace** le champ de la fiche : il porte
    déjà son cadre par son `{{cadre}}`, et le concaténer le poserait deux fois."""
    prompt = playbook_outille(_fiche(nom, prompt_systeme="texte de la fiche, à ignorer"))

    assert prompt == playbook_du_code(nom)
    assert "texte de la fiche" not in prompt


def test_le_document_du_paquet_masque_le_playbook_d_une_fiche_homonyme() -> None:
    """Le fait qui vaut réservation de nom : une fiche de projet nommée comme un
    rôle du code partirait en exécution avec le playbook du code, **en silence**.
    C'est pourquoi les rôles proposés par l'analyse d'équipe portent d'autres noms
    (`dev` descend de `developpeur`, `maestro.equipe.gabarits`)."""
    ecrit_pour_ce_projet = "Tu travailles sur le projet Dépensio, et rien d'autre."

    prompt = playbook_outille(_fiche("developpeur", prompt_systeme=ecrit_pour_ce_projet))

    assert ecrit_pour_ce_projet not in prompt


# --- ④ Un seul chemin de construction ----------------------------------------


def test_le_runtime_outille_porte_le_profil_derive_de_la_fiche() -> None:
    runtime = runtime_outille(_Muet(), _fiche(modele="modele-de-la-fiche"))

    assert runtime.profile.nom == "analyste"
    assert runtime.profile.modele == "modele-de-la-fiche"
    assert runtime.profile.outils == DEFAULT_TOOLS


def test_un_playbook_edite_fige_le_prompt_au_moment_du_cablage(tmp_path: Path) -> None:
    """`playbooks=` fige le prompt système sur la version courante du dépôt — un
    instantané pour les usages une-fois, là où l'application à chaud (#78) passe
    par l'exécuteur."""
    depot = PlaybookStore(tmp_path)
    depot.ecrire("analyste", "Version éditée depuis la Control Tower.")

    fige = depot.prompt_systeme("analyste", playbook_outille(_fiche()))

    assert fige == "Version éditée depuis la Control Tower."
    assert depot.prompt_systeme("inconnu", "défaut") == "défaut"


def test_les_runtimes_par_defaut_couvrent_les_roles_du_code() -> None:
    """`default_runtimes` n'est plus le câblage de production mais un **instantané**
    des rôles du code : l'exécuteur, lui, résout le runtime depuis la fiche de
    l'agent routé (agents de projet compris)."""
    runtimes = default_runtimes(_Muet())

    assert set(runtimes) == set(roles_du_code())
    assert all(nom == runtime.profile.nom for nom, runtime in runtimes.items())


def test_les_runtimes_par_defaut_basculent_tous_sur_un_modele_unique() -> None:
    """La moitié « exécutants » de la bascule par configuration (#69) : le modèle
    vient de la fiche, donc la bascule des fiches suffit."""
    impose = default_runtimes(_Muet(), model="modele-impose")
    sans = default_runtimes(_Muet())

    assert {r.profile.modele for r in impose.values()} == {"modele-impose"}
    assert {r.profile.modele for r in sans.values()} == {MODELE_EXECUTANT_DEFAUT}
