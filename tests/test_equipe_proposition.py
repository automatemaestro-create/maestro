"""L'analyse du projet **propose** son équipe — et ne crée rien (#1039).

Lot final du chantier #1021 (#1043) — le lot 3 avait livré sans tests, par la
convention de découpage ([docs/10 §5.1](../docs/10-workflow-git.md)).

Quatre règles décident dans `maestro.equipe`, et ce sont elles qu'on tient ici
(cf. l'en-tête de `maestro.equipe.proposition`) :

① **rien sans son endroit** — chaque rôle porte la `Piece` du projet qui le fait
   exister, le fichier lu et pas une phrase : une équipe se conteste en ouvrant
   les fichiers qui l'ont désignée ;
② **ce qui n'est pas proposé est nommé, avec sa raison** — sinon « pas de rôle
   base de données » se lirait comme une défaillance de Maestro plutôt que comme
   un fait du projet. C'est aussi là que vit l'orchestrateur, qui n'est jamais un
   membre de l'équipe : c'est Maestro, et c'est lui qui recrute ;
③ **chaque autorisation porte sa raison, et le cran se dérive du projet** —
   le critère du ticket (#716) : Maestro *rédige* la politique, l'utilisateur la
   *décide*. Un cran `auto` proposé sans sa raison serait une décision prise sans
   personne ;
④ **rien n'est créé** — `cree: False`, `validation: "requise"`, et aucun dépôt
   ouvert en écriture.

S'y ajoute, depuis #1102, une cinquième règle que le module ne portait pas et
qu'un run réel a désignée :

⑤ **le playbook et l'autorisation d'un rôle sont d'accord** — l'`intention` d'où
   #257 écrira le playbook porte le **cran proposé pour l'outil d'exécution**.
   Sans lui, le playbook ordonnait en premier geste une commande qu'une personne
   devait approuver, et une équipe validée telle quelle attendait un humain dès
   sa première tâche.

Les constats sont **fabriqués** : c'est ce qui rend la dérivation éprouvable sans
projet réel, sans disque et sans fournisseur de modèle. La recommandation
d'outillage, elle, est dérivée de ces constats par `recommander` — la vraie, pour
qu'un skill branché ici soit un skill que l'outillage recommanderait là-bas.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from maestro import equipe
from maestro.agents.store import NOMS_RESERVES
from maestro.controltower.generation_agent import _CADRE_GENERATION, INTENTION_MAX
from maestro.decideur import Decideur
from maestro.equipe import (
    ECARTE_ORCHESTRATEUR,
    GABARITS,
    INSTANCES_MAX_PROPOSEES,
    ORIGINE_PLAYBOOK_GABARIT,
    ORIGINE_PLAYBOOK_GENERE,
    OUTIL_EXECUTION,
    PART_SUBSTANTIELLE,
    PREFIXE_ID,
    VERSION_PROPOSITION,
    AutorisationProposee,
    avec_playbook,
    proposer_equipe,
)
from maestro.outillage.modele import Commande, Constats, Langage, Piece
from maestro.outillage.questionnaire import Choix
from maestro.outillage.recommandation import recommander
from maestro.portee import PORTEE_PROJET

PROJET = "prj-depensio"

#: Les verbes par lesquels un module du paquet toucherait un dépôt — écriture de
#: fichier, création de dossier, ouverture en mode écriture, ou l'`ecrire` d'un
#: dépôt d'agents. Cherché comme **usage** (l'appel), jamais comme mention : une
#: docstring qui parle d'écriture n'écrit pas.
_ECRITURE = re.compile(r"\.(write_text|write_bytes|mkdir|ecrire)\(|\bopen\([^)]*[\"']w")


def _langage(nom: str, part: float, fichiers: int = 10, exemple: str = "") -> Langage:
    return Langage(nom=nom, fichiers=fichiers, part=part, exemple=exemple or f"src/x.{nom}")


def _constats(**extra) -> Constats:
    """Un projet Python ordinaire : un langage dominant et rien d'autre."""
    base = {"langages": (_langage("Python", 1.0, exemple="src/app.py"),)}
    return Constats(**{**base, **extra})


def _propose(constats: Constats, **extra):
    """La proposition de ce projet, sur la recommandation d'outillage réelle."""
    return proposer_equipe(constats, recommander(constats), projet_id=PROJET, **extra)


def _role(proposition, nom: str):
    """Le rôle proposé nommé `nom` — lève si l'équipe ne le porte pas."""
    return next(role for role in proposition.roles if role.nom == nom)


# --- ④ Rien n'est créé --------------------------------------------------------


def test_la_proposition_dit_en_toutes_lettres_qu_elle_n_a_rien_cree() -> None:
    """`cree` et `validation` ne sont pas des réglages : ce sont les promesses du
    ticket rendues lisibles par l'appelant, et aucun appel ne peut les changer."""
    servi = _propose(_constats()).to_dict()

    assert servi["cree"] is False
    assert servi["validation"] == "requise"
    assert servi["proposition"] == VERSION_PROPOSITION


def test_aucun_module_du_paquet_equipe_n_ouvre_un_fichier_en_ecriture() -> None:
    """La promesse prise à la source. « Rien n'est créé » se dit dans la forme
    servie, mais se **tient** dans le code : aucun module de `maestro.equipe`
    n'écrit, et l'écriture est le seul verbe de `maestro.controltower.equipe`.

    Le motif est prouvé sur un échantillon fautif avant de balayer : une garde
    qui ne reconnaîtrait plus rien passerait au vert sur un paquet qui écrit.
    """
    fautif = "    chemin.write_text(json.dumps(role), encoding='utf-8')\n"
    assert _ECRITURE.search(fautif) is not None, "le motif ne reconnaît plus une écriture"

    paquet = Path(equipe.__file__).parent
    for module in sorted(paquet.glob("*.py")):
        faute = _ECRITURE.search(module.read_text(encoding="utf-8"))
        assert faute is None, f"{module.name} écrit ({faute.group(0)!r})"


def test_l_identifiant_de_proposition_est_une_reference_pas_un_compteur() -> None:
    premiere = _propose(_constats())
    seconde = _propose(_constats())

    assert premiere.id.startswith(PREFIXE_ID)
    assert premiere.id != seconde.id
    assert premiere.projet_id == PROJET


def test_la_provenance_du_manifeste_est_reprise_telle_quelle() -> None:
    """Elle dira, six mois plus tard, si cette équipe vient de l'analyse d'un
    projet existant ou des réponses données sur un projet neuf."""
    source = {"origine": "analyse", "analyse_id": "ana-1234"}

    servi = _propose(_constats(), source=source).to_dict()

    assert servi["source"] == source


# --- ① Rien sans son endroit --------------------------------------------------


def test_le_role_toujours_propose_porte_le_langage_dominant_et_son_fichier() -> None:
    dev = _role(_propose(_constats()), "dev")

    assert "Python" in dev.raison
    assert dev.justification == Piece(
        nom="Python", chemin="src/app.py", role="10 fichier(s) Python"
    )


def test_un_projet_sans_aucun_langage_garde_un_developpeur_sans_inventer_de_piece() -> None:
    """« Un projet existe pour qu'on y écrive » : c'est le seul rôle qu'un projet
    appelle sans rien avoir à prouver — et inventer une pièce serait pire que de
    n'en pas avoir."""
    dev = _role(_propose(Constats()), "dev")

    assert dev.justification is None
    assert "aucun langage de code n'a été constaté" in dev.raison


def test_chaque_role_propose_porte_sa_raison_et_son_playbook_de_repli() -> None:
    proposition = _propose(
        _constats(commandes=(Commande(usage="tester", commande="pytest", chemin="Makefile"),))
    )

    for role in proposition.roles:
        assert role.raison.strip()
        assert role.playbook.strip()
        assert role.playbook_origine == ORIGINE_PLAYBOOK_GABARIT
        assert role.playbook_raison.strip()
        assert role.intention.strip()


def test_un_role_propose_ne_porte_jamais_le_nom_de_son_gabarit() -> None:
    """La filiation se lit (`gabarit`), mais le nom diffère — sinon le document du
    paquet masquerait le playbook écrit pour ce projet (`playbook_outille`), en
    silence. C'est aussi ce que la réserve de noms du dépôt refuserait."""
    proposition = _propose(_constats())

    for role in proposition.roles:
        assert role.nom != role.gabarit
        assert role.nom not in NOMS_RESERVES


# --- ② Ce qui n'est pas proposé est nommé ------------------------------------


def test_l_orchestrateur_est_ecarte_nommement_de_toute_equipe() -> None:
    """Il n'a pas de gabarit et il n'en aura pas : c'est Maestro, et c'est lui qui
    recrute. L'écarter en silence laisserait croire qu'aucun projet n'en a."""
    proposition = _propose(_constats())

    assert ECARTE_ORCHESTRATEUR in proposition.ecartes
    assert all(role.nom != "orchestrateur" for role in proposition.roles)
    assert "c'est lui qui recrute" in ECARTE_ORCHESTRATEUR.raison


def test_un_projet_nu_ne_propose_que_le_developpeur_et_nomme_les_quatre_autres() -> None:
    proposition = _propose(_constats())

    assert [role.nom for role in proposition.roles] == ["dev"]
    ecartes = {ecarte.nom for ecarte in proposition.ecartes}
    assert ecartes == {"orchestrateur", "donnees", "infra", "interface", "tests"}


def test_un_role_ecarte_nomme_ce_qui_manque_dans_le_projet() -> None:
    """La raison nomme ce qui manque, pas ce que Maestro n'a pas fait."""
    ecarte = next(e for e in _propose(_constats()).ecartes if e.nom == "donnees")

    assert "aucun fichier SQL" in ecarte.raison
    assert "bornes de l'analyse" in ecarte.raison


def test_du_sql_constate_fait_proposer_le_role_base_de_donnees() -> None:
    constats = _constats(
        langages=(
            _langage("Python", 0.8, exemple="src/app.py"),
            _langage("SQL", 0.2, fichiers=4, exemple="db/schema.sql"),
        )
    )

    proposition = _propose(constats)

    donnees = _role(proposition, "donnees")
    assert donnees.gabarit == "bdd"
    assert donnees.justification is not None
    assert donnees.justification.chemin == "db/schema.sql"


def test_une_nature_declaree_suffit_a_proposer_le_role_d_interface() -> None:
    """Sur un projet **neuf**, aucun fichier ne prouve encore les écrans : c'est
    la réponse au questionnaire qui le dit."""
    proposition = _propose(_constats(), choix=(Choix(cle="nature", valeur="application-web"),))

    interface = _role(proposition, "interface")
    assert interface.gabarit == "designer"
    assert "application web" in interface.raison


# --- ③ Chaque autorisation porte sa raison ------------------------------------


def test_chaque_autorisation_proposee_est_nommee_avec_sa_raison() -> None:
    proposition = _propose(
        _constats(
            commandes=(
                Commande(
                    usage="tester",
                    commande="pytest",
                    chemin="Makefile",
                    extrait="test:",
                    origine="declaree",
                ),
            )
        )
    )

    for role in proposition.roles:
        for autorisation in role.autorisations:
            assert autorisation.raison.strip()
            assert autorisation.outil == OUTIL_EXECUTION


def test_le_cran_auto_nomme_les_fichiers_du_projet_ou_les_commandes_ont_ete_lues() -> None:
    """Le critère du ticket (#716) : `auto` n'est pas « la machine approuve »,
    c'est une décision prise d'avance — et elle nomme ce sur quoi elle est prise."""
    constats = _constats(
        commandes=(
            Commande(
                usage="tester",
                commande="pytest -q",
                chemin="Makefile",
                extrait="test:",
                origine="declaree",
            ),
        )
    )

    tests = _role(_propose(constats), "tests")

    execution = next(a for a in tests.autorisations if a.outil == OUTIL_EXECUTION)
    assert execution.cran == "ask"
    assert execution.decideur_effectif is Decideur.AUTO
    assert execution.portee == PORTEE_PROJET
    assert "Makefile" in execution.raison
    assert "ne le borne pas à celles-là" in execution.raison


def test_une_commande_de_convention_n_est_pas_une_commande_lue() -> None:
    """La retenir ferait passer une supposition pour une lecture — l'exact travers
    qu'`ORIGINES_COMMANDE` existe pour empêcher.

    ⚠ Depuis #1226, ce que ce relevé sépare n'est plus le **cran** — une équipe
    validée exécute dans son projet quoi qu'il arrive — mais la **raison servie** :
    une autorisation qui nomme la pièce qui l'a désignée se conteste en ouvrant
    cette pièce. Une commande de convention n'en est pas une, donc elle n'est pas
    citée."""
    constats = _constats(
        commandes=(
            Commande(
                usage="tester", commande="pytest", chemin="pyproject.toml", origine="convention"
            ),
        )
    )

    tests = _role(_propose(constats), "tests")

    execution = next(a for a in tests.autorisations if a.outil == OUTIL_EXECUTION)
    assert execution.decideur_effectif is Decideur.AUTO
    assert execution.portee == PORTEE_PROJET
    assert "pyproject.toml" not in execution.raison
    assert "aucune n'a pu être lue" in execution.raison


def test_la_politique_proposee_laisse_allow_ouvert_et_porte_le_decideur() -> None:
    """Une liste `allow` non vide est *fermée* : la remplir avec les outils du rôle
    refuserait les canaux in-process de Maestro — poser une question, consigner une
    décision — et rendrait l'agent muet."""
    dev = _role(_propose(_constats()), "dev")

    politique = dev.politique()

    assert politique.allow == ()
    assert politique.deny == ()
    assert [entree for entree in politique.ask] == [OUTIL_EXECUTION]
    assert politique.decideur(OUTIL_EXECUTION) is Decideur.AUTO
    # La portée voyage avec le cran (#1226) : un `auto` servi sans elle serait une
    # autorisation d'exécuter n'importe où, et ce n'est pas ce qui est proposé.
    assert politique.decide(OUTIL_EXECUTION).portee == PORTEE_PROJET
    assert politique.to_dict()["portees"] == {OUTIL_EXECUTION: PORTEE_PROJET}


def test_la_politique_servie_ne_peut_pas_contredire_les_autorisations_detaillees() -> None:
    """Les deux voyagent côte à côte — l'écran montre les raisons une par une, la
    création n'a besoin que de la politique —, et elles sortent de la même source."""
    dev = _role(_propose(_constats()), "dev").to_dict()

    crans = {a["outil"]: a["cran"] for a in dev["autorisations"]}
    assert set(dev["politique"]["ask"]) == {o for o, c in crans.items() if c == "ask"}


def test_un_cran_inconnu_est_refuse_a_la_construction() -> None:
    """Un cran inconnu produirait une politique que `PermissionStore` refuserait de
    relire — c'est-à-dire une proposition invalidable seulement à la création."""
    with pytest.raises(ValueError, match="cran d'autorisation inconnu"):
        AutorisationProposee(outil="Bash", cran="parfois", raison="…")


def test_un_decideur_ne_se_pose_que_sur_le_cran_ask() -> None:
    """Un `allow` passe sans que personne tranche, un `deny` refuse sans que
    personne tranche."""
    with pytest.raises(ValueError, match="décideur ne se pose que sur le cran"):
        AutorisationProposee(
            outil="Bash", cran="allow", raison="…", decideur=Decideur.AUTO
        )


def test_un_ask_sans_decideur_escalade_plutot_que_de_s_auto_approuver() -> None:
    servi = AutorisationProposee(outil="Bash", cran="ask", raison="…").to_dict()

    assert servi["decideur"] == str(Decideur.HUMAIN)


# --- ⑤ Le playbook et l'autorisation sont d'accord (#1102) --------------------


def test_l_intention_dit_sous_quel_regime_le_role_execute() -> None:
    """#1102 : le playbook était écrit dans l'ignorance du cran de son agent, si
    bien qu'il lui ordonnait en premier geste une commande qu'une personne devait
    approuver. L'intention porte désormais le fait ; la règle qu'on en tire vit
    dans le cadre de #257, et nulle part ailleurs.

    ⚠ Depuis #1226 le fait a changé de valeur — l'agent exécute dans son projet
    sans attendre personne — et la **portée** le borne. L'intention doit porter
    les deux : le régime seul ferait écrire un playbook qui croit tout permis."""
    dev = _role(_propose(_constats()), "dev")

    execution = next(a for a in dev.autorisations if a.outil == OUTIL_EXECUTION)
    assert execution.decideur_effectif is Decideur.AUTO
    assert equipe.REGIME_EXECUTION[Decideur.AUTO] in dev.intention
    assert equipe.REGIME_PORTEE[PORTEE_PROJET] in dev.intention


def test_l_intention_suit_le_cran_quand_le_projet_declare_ses_commandes() -> None:
    """Le régime annoncé est **celui de l'autorisation proposée**, jamais une
    phrase écrite à côté d'elle : un projet qui déclare ses commandes fait passer
    les deux à `auto` du même coup."""
    constats = _constats(
        commandes=(
            Commande(
                usage="tester",
                commande="pytest -q",
                chemin="Makefile",
                extrait="test:",
                origine="declaree",
            ),
        )
    )

    tests = _role(_propose(constats), "tests")

    execution = next(a for a in tests.autorisations if a.outil == OUTIL_EXECUTION)
    assert execution.decideur_effectif is Decideur.AUTO
    assert equipe.REGIME_EXECUTION[Decideur.AUTO] in tests.intention
    assert equipe.REGIME_EXECUTION[Decideur.HUMAIN] not in tests.intention


@pytest.mark.parametrize("decideur", list(Decideur))
def test_chaque_decideur_a_sa_phrase_de_regime(decideur: Decideur) -> None:
    """Un décideur sans phrase ferait une intention muette sur le régime — le
    défaut que #1102 corrige, revenu par la porte d'un cran neuf."""
    assert equipe.REGIME_EXECUTION[decideur].strip()


def test_l_intention_tient_sous_la_borne_du_generateur() -> None:
    """`ServiceEquipe._playbook` coupe l'intention à `INTENTION_MAX` : le régime
    étant en fin de phrase, il serait le premier perdu."""
    constats = _constats(
        langages=(
            _langage("Python", 0.5, exemple="src/app.py"),
            _langage("TypeScript", 0.3, exemple="web/app.ts"),
            _langage("CSS", 0.2, exemple="web/app.css"),
        ),
        commandes=tuple(
            Commande(usage=usage, commande=f"make {usage}", chemin="Makefile", origine="declaree")
            for usage in ("installer", "construire", "demarrer", "tester", "lint")
        ),
    )

    for role in _propose(constats).roles:
        # La coupe de `_playbook` doit être un non-événement : sinon le régime,
        # qui ferme la phrase, partirait le premier.
        assert role.intention[:INTENTION_MAX] == role.intention
        assert any(phrase in role.intention for phrase in equipe.REGIME_EXECUTION.values())


def test_le_cadre_de_generation_interdit_de_faire_d_une_commande_le_premier_geste() -> None:
    """L'autre moitié de ⑤, et la seule qui porte une **consigne** : l'intention
    dit le fait, le cadre de #257 en tire la règle. La règle y est écrite
    conditionnellement parce que ce cadre sert aussi la saisie libre du
    formulaire, où aucune intention ne parle de cran."""
    # Le cadre est enveloppé à ~80 colonnes : une phrase attendue y traverse une
    # fin de ligne. On compare donc sur le texte remis à plat, pas sur la mise en page.
    cadre = " ".join(_CADRE_GENERATION.split())

    assert "jamais d'une commande à exécuter le premier geste obligatoire" in cadre
    assert "Si l'intention dit que ses commandes attendent l'accord d'une personne" in cadre
    # Et la conduite qu'il prescrit à la place, jusqu'au refus.
    assert "avec son outil de lecture, jamais par le shell" in cadre
    assert "il poursuit et le signale, il ne réessaie pas" in cadre


def test_sur_un_projet_neuf_le_dev_recoit_le_droit_d_executer_dans_le_projet() -> None:
    """Le premier critère de #1226. Un projet neuf ne déclare aucune commande :
    jusqu'ici le `dev` y recevait `humain`, et le run du 2026-09-22 a demandé
    14 validations `Bash` pour 14 approbations — `mkdir`, `python`, `pytest`, et le
    ménage des caches que ses propres exécutions venaient de produire.

    L'autorisation proposée dit désormais les deux moitiés, et la raison est ce
    qui la rend décidable à la validation (#1040) : ce qu'il fait sans déranger
    personne, et ce qui revient quand même à la personne."""
    dev = _role(_propose(_constats()), "dev")

    execution = next(a for a in dev.autorisations if a.outil == OUTIL_EXECUTION)
    assert execution.cran == "ask"
    assert execution.decideur_effectif is Decideur.AUTO
    assert execution.portee == PORTEE_PROJET
    assert execution.to_dict()["portee"] == PORTEE_PROJET
    # Ce qu'il fait seul…
    assert "sans vous demander de trancher chaque commande" in execution.raison
    # …et ce qui vous revient quand même — les deux familles, nommées.
    assert "sort du dossier du projet" in execution.raison
    assert "effacerait ce que vous aviez posé là" in execution.raison


# --- Les instances : combien, et pourquoi -----------------------------------


def test_un_seul_langage_substantiel_donne_une_instance_avec_sa_raison() -> None:
    dev = _role(_propose(_constats()), "dev")

    assert dev.instances == 1
    assert "on augmente les instances pour absorber la charge" in dev.raison_instances


def test_deux_langages_substantiels_sont_deux_surfaces_donc_deux_instances() -> None:
    constats = _constats(
        langages=(
            _langage("Python", 0.55, exemple="src/app.py"),
            _langage("TypeScript", 0.45, exemple="web/app.ts"),
        )
    )

    dev = _role(_propose(constats), "dev")

    assert dev.instances == 2
    assert "TypeScript" in dev.raison_instances
    assert "décision, pas une mesure" in dev.raison_instances


def test_un_langage_confie_a_un_autre_role_ne_recrute_pas_un_developpeur_de_plus() -> None:
    """La même matière serait comptée deux fois : une fois pour recruter le
    designer, une fois pour lui retirer son travail."""
    constats = _constats(
        langages=(
            _langage("Python", 0.6, exemple="src/app.py"),
            _langage("CSS", 0.4, exemple="web/style.css"),
        )
    )

    proposition = _propose(constats)

    assert _role(proposition, "dev").instances == 1
    assert _role(proposition, "interface").gabarit == "designer"


def test_un_residu_sous_le_seuil_ne_justifie_pas_une_instance_de_plus() -> None:
    constats = _constats(
        langages=(
            _langage("Python", 0.95, exemple="src/app.py"),
            _langage("Shell", PART_SUBSTANTIELLE / 2, exemple="build.sh"),
        )
    )

    assert _role(_propose(constats), "dev").instances == 1


def test_les_instances_proposees_sont_plafonnees() -> None:
    constats = _constats(
        langages=tuple(
            _langage(nom, 0.2, exemple=f"src/x.{nom}")
            for nom in ("Python", "TypeScript", "Go", "Rust", "Java")
        )
    )

    dev = _role(_propose(constats), "dev")

    assert dev.instances == INSTANCES_MAX_PROPOSEES


# --- Les skills branchés, et le nom de la fiche ------------------------------


def test_un_role_branche_les_skills_du_projet_par_usage() -> None:
    """Par usage, jamais par nom : `SKILL_PAR_USAGE` tient les noms, et un usage
    que la recommandation a écarté ne produit rien."""
    constats = _constats(
        commandes=(
            Commande(usage="tester", commande="pytest", chemin="Makefile", origine="declaree"),
            Commande(
                usage="installer", commande="pip install -e .", chemin="Makefile",
                origine="declaree",
            ),
        )
    )

    proposition = _propose(constats)

    assert [s.nom for s in _role(proposition, "dev").skills] == ["mettre-en-route"]
    assert [s.nom for s in _role(proposition, "tests").skills] == ["lancer-les-tests"]


def test_un_skill_branche_reprend_l_entree_qui_l_a_recommande() -> None:
    constats = _constats(
        commandes=(
            Commande(usage="tester", commande="pytest", chemin="Makefile", origine="declaree"),
        )
    )
    recommandation = recommander(constats)

    tests = _role(
        proposer_equipe(constats, recommandation, projet_id=PROJET), "tests"
    )

    entree = next(e for e in recommandation.entrees if e.nom == "lancer-les-tests")
    branche = tests.skills[0]
    assert (branche.chemin, branche.etat, branche.commandes) == (
        entree.chemin,
        entree.etat,
        entree.commandes,
    )
    assert branche.raison != entree.raison  # pourquoi **ce rôle-là** le branche


def test_un_nom_deja_pris_dans_le_projet_est_suffixe_avant_d_etre_propose() -> None:
    """La collision n'est pas une validation — le dépôt reste seul juge à la
    création —, mais proposer « dev » à un projet qui en a déjà un est le cas
    fréquent et évitable."""
    proposition = _propose(_constats(), noms_pris=("dev",))

    assert _role(proposition, "dev-2").gabarit == "developpeur"


# --- Le résumé et le playbook rapporté ---------------------------------------


def test_le_resume_tient_en_une_ligne_et_compte_les_ecartes() -> None:
    constats = _constats(
        langages=(
            _langage("Python", 0.55, exemple="src/app.py"),
            _langage("TypeScript", 0.45, exemple="web/app.ts"),
        )
    )

    proposition = _propose(constats)

    assert proposition.resume.startswith("Développeur ×2")
    assert f"{len(proposition.ecartes)} rôle(s) écarté(s)" in proposition.resume
    assert proposition.instances_total == sum(r.instances for r in proposition.roles)


def test_un_playbook_ecrit_pour_le_projet_remplace_celui_du_gabarit() -> None:
    """Le seul chemin de ce remplacement : la rédaction vit dans la Control Tower,
    seule couche qui connaisse un fournisseur de modèle."""
    dev = _role(_propose(_constats()), "dev")

    rapporte = avec_playbook(
        dev, "Tu travailles sur Dépensio.", origine=ORIGINE_PLAYBOOK_GENERE, raison="écrit"
    )

    assert rapporte.playbook == "Tu travailles sur Dépensio."
    assert rapporte.playbook_origine == ORIGINE_PLAYBOOK_GENERE
    assert dev.playbook != rapporte.playbook  # la proposition d'origine est inerte


def test_une_origine_de_playbook_inconnue_est_refusee() -> None:
    """Sans cette garde, on servirait une proposition dont on ne saurait pas dire
    d'où vient le playbook — l'information que ce champ existe pour porter."""
    dev = _role(_propose(_constats()), "dev")

    with pytest.raises(ValueError, match="origine de playbook inconnue"):
        avec_playbook(dev, "texte", origine="ailleurs", raison="…")


def test_un_playbook_vide_est_refuse() -> None:
    dev = _role(_propose(_constats()), "dev")

    with pytest.raises(ValueError, match="playbook vide"):
        avec_playbook(dev, "   ", origine=ORIGINE_PLAYBOOK_GENERE, raison="…")


def test_les_gabarits_sont_consultes_et_jamais_instancies() -> None:
    """Ce que docs/37 §2.1 appelle « les agents figés deviennent des gabarits » :
    la matière est là, les rôles en descendent, et rien ne les instancie."""
    assert {gabarit.gabarit for gabarit in GABARITS} <= NOMS_RESERVES
    assert all(gabarit.nom not in NOMS_RESERVES for gabarit in GABARITS)
    assert all(gabarit.playbook_de_repli().strip() for gabarit in GABARITS)
