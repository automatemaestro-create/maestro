"""Le cycle de vie d'un ticket, porté par le champ Status de Projects v2 (ticket #366, parent #358).

Lot final : les lots #359 à #365 ont différé leurs tests ici (docs/10 §5.1). Ce module couvre ce
qu'ils ont fait du cycle de vie — son support, ses écritures, ses lectures et ses dérives — sur
[`scripts/gitlab/lib.sh`](../scripts/gitlab/lib.sh),
[`scripts/gitlab/doctor.sh`](../scripts/gitlab/doctor.sh) et
[`scripts/github/bootstrap-project.sh`](../scripts/github/bootstrap-project.sh).

CE QUI EST GARDÉ ICI, LOT PAR LOT :

| Lot | Ce que ce module garde |
|---|---|
| #359 | les six options du champ, **dans l'ordre du flux** ; idempotence et garde-fou du monteur |
| #360 | l'aller-retour libellé → option → libellé ; l'asymétrie écriture/lecture |
| #361 | `project-add` : rejouable, la valeur NOMMÉE et jamais devinée, appelé à la création |
| #362 | le recouvrement des tables par la carte, sa mémoire, et le tube qui ne masque pas d'échec |
| #363 | chaque dérive sur un cas fabriqué, et **aucune** sur un dépôt sain |
| #364 | le support est le champ Status, sans réglage à poser |
| #365 | plus aucun label `workflow::` ni commutateur — un seul support, prouvé par `grep` |

⚠ **LE VOCABULAIRE NE BOUGE PAS, ET C'EST LE SUJET.** Les six libellés — « À faire », « En cours »,
« En revue », « Terminé », « Abandonné », « Doublon » — sont le CONTRAT DE SURFACE documenté en tête
de `lib.sh`, et ils ont survécu à trois supports : le champ Status natif de GitLab, les six labels
scopés `workflow::*` (#207), puis ce champ-ci. Ce module les écrit en toutes lettres à dessein : un
test qui les dériverait du code testé ne pourrait plus dire qu'ils n'ont pas changé.

⚠ **DEUX LIGNES DU TABLEAU DE CADRAGE SONT CADUQUES, ET C'EST LE CHANTIER QUI LES A PÉRIMÉES.** #366
a été écrit avant #364 et #365 ; il demandait « le retour arrière `MAESTRO_CYCLE=labels` prouvé, pas
supposé » et « le backfill rejouable, reprenable ». Or #365 a retiré le commutateur AVEC les labels
— il n'y a plus de retour arrière à prouver, et l'en-tête de `lib.sh` en fait une décision explicite
— et a retiré `gl_project_backfill` avec eux, dont la seule source (le label courant) avait disparu.
Ce qui reste de ces deux lignes est gardé ici sous la forme qu'elles ont prise : le support est
UNIQUE (`test_aucun_commutateur_ne_choisit_plus_de_support`), et le peuplement se fait à l'unité
(`project-add`). Prouver un retour arrière qui n'existe plus reviendrait à le réintroduire.

⚠ **LE LOT #363 N'EST PAS MERGÉ, ET CE MODULE EST ÉCRIT POUR SURVIVRE À SON MERGE.** Il refait §4c
de `doctor.sh` sur une lecture **centrée ticket** — une requête qui sait dire d'un ticket
qu'il n'est dans AUCUN projet, là où `main` interroge la carte du projet — et sépare en deux
causes (« hors projet » / « Status vide ») ce que `main` fond en un message. Les assertions
portent donc sur ce que les deux versions garantissent **également** : le ticket est NOMMÉ,
le geste de réparation est NOMMÉ, un ticket qui a un état ne l'est pas. Épingler la
formulation ferait échouer le contrôle sur un merge qui ne change rien à ce qu'il garde.

Ce n'est pas une intersection *supposée* : le module a été joué **des deux côtés**, `main` tel quel
et `main` + la branche de #363 (`git restore --source=…`), **67/67 dans les deux cas**. C'est aussi
pourquoi `regles_doctor` sert les DEUX lectures — chacune prend la règle qui lui répond, l'autre
étant ignorée.

Restent à couvrir **avec #363**, dans ce module : les deux causes distinguées par leur message,
l'option en trop, les options dans le désordre, et ses deux gardes `st_erreur_graphql`.

Harnais commun : [`harnais_forge.py`](harnais_forge.py) — dépôt jetable, `gh` factice, ni réseau ni
compte de forge (il en sort, et sa docstring dit pourquoi).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from harnais_forge import (
    BASH,
    GIT,
    ID_CHAMP,
    ID_PROJET,
    LIBELLES_WORKFLOW,
    PROJET,
    RACINE,
    Depot,
    colonnes,
    corps_ticket,
    ecritures,
    lignes_projet,
    monte_depot,
    regle_owner,
    regle_pose_status,
    regles_backlog,
    regles_carte,
)

pytestmark = [
    pytest.mark.skipif(BASH is None, reason="bash introuvable"),
    pytest.mark.skipif(GIT is None, reason="git introuvable"),
]

#: Les six libellés du contrat de surface, ÉCRITS ICI et non dérivés du code testé (cf. docstring).
#: Ils sont dans l'ordre du flux, qui est aussi celui des colonnes du projet.
SIX_LIBELLES = ("À faire", "En cours", "En revue", "Terminé", "Abandonné", "Doublon")

#: Les six slugs acceptés en ENTRÉE. Ce fut le suffixe du label — c'est-à-dire un stockage ; ce
#: n'est plus qu'une forme d'entrée et la clé de la normalisation.
SIX_SLUGS = ("a-faire", "en-cours", "en-revue", "termine", "abandonne", "doublon")

#: Le nom d'un des six labels retirés par #365 — pas le mot « workflow », qui vit encore dans
#: `.github/workflows` et dans les commentaires d'histoire.
_LABEL_MORT = r"workflow::(?:" + "|".join(SIX_SLUGS) + r")"


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    return monte_depot(tmp_path)


# =================================================================================================
# Fabriques propres au cycle de vie
# =================================================================================================


def regle_projet_resolu() -> dict:
    """Réponse à `pj_resoudre` — le projet, son champ Status et ses six options, aplatis.

    Le fragment `options{id name}` (sans espace) est ce qui distingue cette requête de celle de
    `st_contexte`, qui demande la même chose autrement (`options { id name }`) et pour un ticket.
    """
    return {"contient": ["options{id name}"], "brut": "\n".join(lignes_projet()) + "\n"}


def regle_node_id(iid: str, node: str = "I_ticket") -> dict:
    """Réponse à `gh_workitem_gid` — le node id du ticket, que `project-add` passe au projet."""
    return {
        "contient": [f"issue(number:{iid}) {{ id }}"],
        "reponse": {"data": {"repository": {"issue": {"id": node}}}},
    }


def regle_ajout_item(item: str = "PVTI_ajoute") -> dict:
    """Réponse à `pj_ajouter_item`. Idempotent côté GitHub : un contenu déjà là rend son item."""
    return {
        "contient": ["addProjectV2ItemById"],
        "reponse": {"data": {"addProjectV2ItemById": {"item": {"id": item}}}},
    }


def option_posee(depot: Depot) -> str | None:
    """Le LIBELLÉ que la dernière mutation a réellement posé, relu depuis son id d'option.

    C'EST L'ALLER-RETOUR, dans le seul sens qu'un double puisse attester : le verbe reçoit un
    libellé, résout l'option PAR NOM, et c'est cet identifiant-là — inventé par le harnais, écrit
    nulle part dans le dépôt — que porte la mutation. Le relire par la table inverse referme la
    boucle : libellé → id d'option → libellé.
    """
    ids = {f"{ID_PROJET}_opt{i}": libelle for i, libelle in enumerate(LIBELLES_WORKFLOW)}
    for ligne in reversed(depot.appels()):
        if "updateProjectV2ItemFieldValue" not in ligne:
            continue
        trouve = re.search(r'singleSelectOptionId: \\?"([^"\\]+)', ligne)
        if trouve:
            return ids.get(trouve.group(1), f"id inconnu : {trouve.group(1)}")
    return None


def etats_du_monteur() -> list[tuple[str, str, str]]:
    """Les six états tels que `bootstrap-project.sh` les écrit : (libellé, couleur, description).

    Lus DANS LE SCRIPT plutôt que recopiés ici : c'est leur ORDRE et leur idempotence qu'on teste,
    et un second exemplaire du tableau ferait passer le test sur sa propre copie.
    """
    source = (RACINE / "scripts/github/bootstrap-project.sh").read_text(encoding="utf-8")
    bloc = source.split("\nETATS=(", 1)[1].split("\n)", 1)[0]
    etats = []
    for ligne in bloc.splitlines():
        ligne = ligne.strip().strip('"')
        if not ligne or ligne.startswith("#"):
            continue
        libelle, couleur, description = ligne.split("|", 2)
        etats.append((libelle, couleur, description))
    return etats


def options_en_chaine(etats: list[tuple[str, str, str]]) -> str:
    """Les options telles que le `--jq` du monteur les aplatit, jointes par « ¤ »."""
    return "¤".join(f"{nom}|{couleur}|{desc}" for nom, couleur, desc in etats)


def regles_monteur(
    options: str, nb_items: str = "0", projets: list[tuple[str, str]] | None = None
) -> list[dict]:
    """Les trois lectures de `bootstrap-project.sh`, déjà aplaties par leur `--jq`.

    `projets` est « (id, titre) », le numéro étant dérivé du rang : le script cherche PAR TITRE,
    c'est ce qui le rend rejouable sans rien créer en double.
    """
    projets = projets or [(ID_PROJET, PROJET)]
    liste = "".join(f"{pid}\t{n}\t{titre}\n" for n, (pid, titre) in enumerate(projets, start=7))
    return [
        {
            "contient": ["repositoryOwner(login: $proprietaire) { id }"],
            "brut": "O_owner\tR_depot\n",
        },
        {"contient": ["projectsV2(first: 100) { nodes { id number title } }"], "brut": liste},
        {
            "contient": ["items(first: 1) { totalCount }"],
            "brut": f"{ID_CHAMP}\t{nb_items}\t{options}\n",
        },
    ]


def mutations(depot: Depot) -> list[str]:
    """Les appels `gh` qui MUTENT côté Projects v2 — vides tant qu'un script s'abstient.

    `ecritures()` du harnais ne les voit pas : une mutation GraphQL passe par `gh api graphql`,
    sans `-X`, donc sans aucune des formes REST que cette liste-là reconnaît.
    """
    return [
        ligne
        for ligne in depot.appels()
        if "mutation" in ligne or any(v in ligne for v in ("createProjectV2", "updateProjectV2"))
    ]


# =================================================================================================
# Le contrat de surface : le vocabulaire, et lui seul, traverse les supports
# =================================================================================================
# « EN SORTIE, toujours le LIBELLÉ ; EN ENTRÉE, les DEUX sont acceptés » (en-tête de lib.sh). C'est
# ce contrat qui a permis de changer deux fois de support sans qu'aucune des commandes `.claude/`
# bouge d'une ligne — donc ce qu'il faut garder en premier.


@pytest.mark.parametrize(("slug", "libelle"), list(zip(SIX_SLUGS, SIX_LIBELLES, strict=True)))
def test_les_six_etats_font_l_aller_retour_slug_libelle(
    depot: Depot, slug: str, libelle: str
) -> None:
    """Le slug normalise, le libellé sort — et les deux formes d'entrée mènent au même slug."""
    assert depot.lib("workflow-label", slug).stdout.strip() == libelle
    assert depot.lib("workflow-slug", libelle).stdout.strip() == slug
    assert depot.lib("workflow-slug", slug).stdout.strip() == slug


def test_une_valeur_inconnue_est_refusee_en_nommant_les_six(depot: Depot) -> None:
    """Refuser sans dire quoi écrire à la place ferait chercher la liste dans le code."""
    acheve = depot.lib("workflow-slug", "En attente")
    assert acheve.returncode == 1
    assert "inconnue" in acheve.stderr
    for libelle in SIX_LIBELLES:
        assert libelle in acheve.stderr


def test_un_libelle_inconnu_ressort_tel_quel_sans_echouer(depot: Depot) -> None:
    """Une LECTURE ne doit pas échouer sur un état exotique — une option renommée dans l'UI.

    Le signaler est le rôle de `doctor.sh` ; le faire ici arrêterait des appelants dont ce n'est
    pas le sujet. C'est la contrepartie exacte du test précédent, qui porte sur une ÉCRITURE.
    """
    acheve = depot.lib("workflow-label", "En attente")
    assert acheve.returncode == 0
    assert acheve.stdout.strip() == "En attente"


# =================================================================================================
# #359 — Le socle : le projet, le champ Status et ses six options
# =================================================================================================


def test_le_monteur_ecrit_les_six_etats_du_contrat_dans_l_ordre_du_flux() -> None:
    """LE test qui tient les deux copies du vocabulaire d'accord.

    Le dépôt écrit les six libellés à DEUX endroits : `gl_workflow_label` (le contrat de surface) et
    le tableau `ETATS` du monteur (ce qui est posé dans le champ). Rien dans le code ne les relie —
    le monteur ne source pas `lib.sh` —, donc rien n'empêcherait un « Terminé » de devenir un
    « Fini » d'un seul côté : `set-workflow` chercherait alors une option qui n'existe pas, sur un
    champ que `bootstrap-project.sh --check` déclarerait conforme.

    L'ORDRE compte autant que la liste : c'est lui qui fait les colonnes du tableau, donc il se lit
    de gauche à droite comme le travail avance.
    """
    assert [libelle for libelle, _, _ in etats_du_monteur()] == list(SIX_LIBELLES)


def test_le_monteur_donne_une_couleur_distincte_aux_deux_etats_d_abandon() -> None:
    """« Doublon passant en rose pour ne pas être indiscernable d'Abandonné une fois en colonnes ».

    Les deux états FERMENT un ticket sans qu'il soit livré, et `reconcile-workflow` a pour règle de
    n'écraser ni l'un ni l'autre : les confondre à l'œil est ce qui ferait rouvrir la question.
    """
    couleurs = {libelle: couleur for libelle, couleur, _ in etats_du_monteur()}
    assert couleurs["Abandonné"] != couleurs["Doublon"]


def test_un_champ_deja_conforme_ne_declenche_aucune_ecriture(depot: Depot) -> None:
    """L'IDEMPOTENCE, dans le seul sens qui compte : rejouer ne réécrit rien.

    Le monteur est « à rejouer sur un dépôt neuf plutôt qu'à recliquer dans une interface » — donc
    il sera rejoué, et sur un projet PEUPLÉ. `updateProjectV2Field` REMPLACE la liste des options :
    une réécriture inutile effacerait l'état des items qui portaient une option retirée.
    """
    depot.pose_etat(graphql=regles_monteur(options_en_chaine(etats_du_monteur()), nb_items="366"))

    acheve = depot.bootstrap_project()
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "conforme" in acheve.stdout
    assert not mutations(depot), "un champ conforme n'a rien à faire écrire"


def test_les_memes_six_options_dans_le_desordre_ne_sont_pas_conformes(depot: Depot) -> None:
    """L'ordre EST le contrat, pas une préférence — le comparé est la chaîne entière.

    Six options justes dans le désordre donnent un tableau dont les colonnes ne suivent plus le
    flux : « Terminé » avant « En cours » se lit, mais ne raconte plus rien.
    """
    etats = etats_du_monteur()
    melange = [etats[3], *etats[:3], *etats[4:]]
    assert {e[0] for e in melange} == {e[0] for e in etats}, "mêmes six, autre ordre"
    depot.pose_etat(graphql=regles_monteur(options_en_chaine(melange)))

    acheve = depot.bootstrap_project("--check")
    assert acheve.returncode == 3, acheve.stdout
    assert "non conforme" in acheve.stdout


def test_check_diagnostique_une_option_manquante_sans_rien_ecrire(depot: Depot) -> None:
    """`--check` est un DIAGNOSTIC : il dit ce qui manque et laisse le geste à qui le lit."""
    etats = etats_du_monteur()
    depot.pose_etat(graphql=regles_monteur(options_en_chaine(etats[:-1])))

    acheve = depot.bootstrap_project("--check")
    assert acheve.returncode == 3, acheve.stdout
    assert "Rejouer sans --check" in acheve.stdout
    assert not mutations(depot)


def test_un_projet_peuple_refuse_la_reecriture_sans_force(depot: Depot) -> None:
    """LE garde-fou du lot : la seule opération destructrice du script demande un « oui » explicite.

    Réécrire les options d'un projet déjà peuplé efface l'état des items qui portaient une option
    retirée — c'est-à-dire le cycle de vie de tickets réels. La borne est grossière (`totalCount`)
    à dessein : « cette option est-elle utilisée ? » demanderait de paginer tous les items.
    """
    etats = etats_du_monteur()
    depot.pose_etat(graphql=regles_monteur(options_en_chaine(etats[:-1]), nb_items="366"))

    acheve = depot.bootstrap_project()
    assert acheve.returncode == 3, acheve.stdout
    assert "--force" in acheve.stderr
    assert not mutations(depot), "le refus doit précéder l'écriture, pas la suivre"


def test_le_monteur_retrouve_son_projet_par_titre_exact(depot: Depot) -> None:
    """Le titre est une CLÉ, comparée en ÉGALITÉ — c'est ce qui rend le script rejouable.

    `projectsV2(query:)` ne sait filtrer que par recherche FLOUE : sur un compte portant les deux,
    « Maestro » ramènerait « Maestro v2 ». Un projet retrouvé au jugé serait un second projet créé
    à chaque exécution, ou pire, le champ Status d'un autre projet réécrit.
    """
    depot.pose_etat(
        graphql=regles_monteur(
            options_en_chaine(etats_du_monteur()),
            projets=[("PVT_autre", f"{PROJET} v2"), (ID_PROJET, PROJET)],
        )
    )

    acheve = depot.bootstrap_project("--check")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "createProjectV2" not in "\n".join(depot.appels()), "le projet existe : rien à créer"


# =================================================================================================
# #360 — L'unité : écrire et relire l'état d'UN ticket
# =================================================================================================


@pytest.mark.parametrize("libelle", SIX_LIBELLES)
def test_poser_un_etat_vise_l_option_resolue_par_son_nom(depot: Depot, libelle: str) -> None:
    """L'aller-retour libellé → option → libellé, sur les six états.

    La mutation ne porte AUCUNE valeur lisible : quatre identifiants opaques, dont celui de
    l'option. Les faire correspondre au libellé demandé est exactement ce que le verbe promet, et
    la seule chose qu'un double puisse en attester.
    """
    depot.pose_etat(graphql=[regle_owner("À faire", []), regle_pose_status()])

    acheve = depot.lib("set-workflow", "360", libelle)
    assert acheve.returncode == 0, acheve.stderr
    assert f"« {libelle} »" in acheve.stdout
    assert option_posee(depot) == libelle


def test_le_slug_et_le_libelle_posent_la_meme_option(depot: Depot) -> None:
    """Slug et libellé posent la MÊME option — et la sortie, elle, est toujours le libellé."""
    depot.pose_etat(graphql=[regle_owner("À faire", []), regle_pose_status()])

    par_libelle = depot.lib("set-workflow", "360", "En revue")
    par_slug = depot.lib("set-workflow", "360", "en-revue")
    assert par_libelle.stdout == par_slug.stdout
    assert "« En revue »" in par_slug.stdout
    assert "en-revue" not in par_slug.stdout, "le slug ne sort jamais de lib.sh"


@pytest.mark.parametrize("libelle", SIX_LIBELLES)
def test_relire_un_etat_rend_le_libelle_du_contrat(depot: Depot, libelle: str) -> None:
    """La lecture rend le LIBELLÉ, pour les six — c'est ce que comparent queue.sh et run.sh."""
    depot.pose_etat(graphql=[regle_owner(libelle, ["bea"])])

    acheve = depot.lib("issue-owner", "360")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.split("\t")[0] == libelle


def test_ecrire_sur_un_ticket_hors_projet_refuse_en_nommant_la_reparation(depot: Depot) -> None:
    """L'ÉCRITURE refuse — il n'y a rien à mettre à jour, la mutation a besoin d'un item.

    Ajouter le ticket au passage serait faire le travail de #361 en silence, sur une écriture que
    personne n'a demandée : le verbe nomme donc `project-add` et s'arrête.
    """
    depot.pose_etat(graphql=[regle_owner("", [], dans_projet=False), regle_pose_status()])

    acheve = depot.lib("set-workflow", "375", "En cours")
    assert acheve.returncode == 1
    assert "n'est pas un item du projet" in acheve.stderr
    assert "project-add 375" in acheve.stderr
    assert not mutations(depot), "un refus ne doit rien avoir écrit"


def test_lire_un_ticket_hors_projet_rend_un_etat_vide_sans_echouer(depot: Depot) -> None:
    """LA LECTURE conclut l'inverse, et c'est délibéré.

    Écrire sur un ticket hors projet ne peut RIEN produire de juste ; le lire produit une
    information vraie — « ce ticket n'a pas d'état » —, qui est déjà le contrat de « non posé » et
    ce que `close-guard` et `start-brief` savent lire. Échouer ici arrêterait des appelants dont ce
    n'est pas le sujet, pour dire ce que le champ vide dit déjà.
    """
    depot.pose_etat(graphql=[regle_owner("", ["bea"], dans_projet=False)])

    acheve = depot.lib("issue-owner", "375")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout == "\tbea\n"


def test_une_valeur_inconnue_est_refusee_avant_toute_lecture_de_projet(depot: Depot) -> None:
    """La normalisation vient EN PREMIER : une faute de frappe ne doit coûter aucun appel."""
    depot.pose_etat(graphql=[regle_owner("À faire", []), regle_pose_status()])

    acheve = depot.lib("set-workflow", "360", "En attente")
    assert acheve.returncode == 1
    assert "inconnue" in acheve.stderr
    assert not depot.appels(), "rien ne doit partir vers la forge sur une valeur illisible"


def test_un_ticket_introuvable_est_une_erreur_franche_et_non_un_etat_vide(depot: Depot) -> None:
    """Sans ce garde-fou, un dépôt illisible rendrait zéro ligne — lues comme « sans état ».

    C'est-à-dire un FEU VERT sur une question jamais posée : `close-guard` conclurait « ticket
    libre » et laisserait clôturer le ticket de quelqu'un d'autre.
    """
    depot.pose_etat(
        graphql=[{"contient": ["projectItems(first:"], "brut": "erreur\tticket\n"}]
    )

    acheve = depot.lib("issue-owner", "999")
    assert acheve.returncode == 1
    assert "introuvable" in acheve.stderr


def test_begin_pose_l_etat_avant_d_assigner(depot: Depot) -> None:
    """L'ORDRE porte ici ce que l'atomicité portait du temps des labels.

    Côté labels, l'état et l'assignation voyageaient dans un seul `PATCH /issues/:n` — indivisible,
    ce qui compte parce que le filtre de `queue.sh` est une CONJONCTION (« À faire » ET libre).
    Côté Status, les deux écritures touchent deux objets et rien ne peut les grouper : l'état passe
    donc d'abord, parce qu'il est le seul des deux qui puisse REFUSER.
    """
    depot.pose_etat(graphql=[regle_owner("À faire", []), regle_pose_status()], rest=[])

    acheve = depot.lib("begin", "360")
    assert acheve.returncode == 0, acheve.stderr

    appels = depot.appels()
    pose = next(i for i, a in enumerate(appels) if "updateProjectV2ItemFieldValue" in a)
    assignation = next(i for i, a in enumerate(appels) if "api\t-X" in a and "assignees" in a)
    assert pose < assignation, "un refus après l'assignation laisserait le ticket pris par personne"


def test_begin_sur_un_ticket_hors_projet_n_assigne_personne(depot: Depot) -> None:
    """Le corollaire du précédent : refuser avant d'écrire laisse le ticket où on l'a trouvé."""
    depot.pose_etat(graphql=[regle_owner("", [], dans_projet=False), regle_pose_status()])

    acheve = depot.lib("begin", "375")
    assert acheve.returncode != 0
    assert not ecritures(depot), "aucun PATCH d'assignation ne doit partir"


def test_liberer_un_ticket_le_remet_a_faire_et_le_rend(depot: Depot) -> None:
    """Le geste inverse de `begin`, et une CONJONCTION elle aussi : « À faire » ET libre.

    Rendre l'état sans vider les assignés laisserait un ticket que `queue.sh` continue d'écarter.

    Appelé en SOURÇANT `lib.sh` : le verbe n'a pas d'entrée dans le dispatcher, `reprendre-en-cours`
    étant son seul appelant (#329). C'est une fonction interne à l'API publique, pas une commande.
    """
    depot.pose_etat(graphql=[regle_owner("En cours", ["bea"]), regle_pose_status()])

    acheve = depot.bash_inline(". scripts/gitlab/lib.sh\ngl_liberer_ticket 360\n")
    assert acheve.returncode == 0, acheve.stderr
    assert option_posee(depot) == "À faire"
    assert any("assignees" in a for a in ecritures(depot)), "la liste des assignés doit être vidée"


# --- Aucun ID en dur ------------------------------------------------------------------------------


def test_aucun_identifiant_de_projets_v2_n_est_ecrit_dans_le_depot() -> None:
    """« Un ID de projet figé dans un script est un clone qui ne démarre pas. »

    C'est la règle que le dépôt s'applique déjà aux labels (dérivés par nom) et qui vaut à
    l'identique pour l'ID du projet, celui du champ Status et ceux de ses six options. Le contrat en
    tête de `lib.sh` dit que « un `grep` du dépôt ne doit en trouver aucun » : c'est ce `grep`-là,
    et c'est pourquoi `st_set_workflow` s'interdit d'en recopier un, fût-ce en exemple dans un
    commentaire — cela ferait mentir le test qui suit.

    `tests/` est exclu : le harnais en INVENTE, et c'est même ce qui prouve qu'ils sont résolus par
    nom — s'ils étaient figés dans les scripts, aucun test ne passerait avec des identifiants
    inventés.
    """
    prefixes = ("PVT_", "PVTSSF_", "PVTI_", "PVTF_")
    # Le motif est prouvé sur la forme réelle d'un identifiant Projects v2, comme les deux autres
    # `grep` de ce module : un contrôle qu'on ne voit jamais échouer n'atteste de rien.
    assert any(p in 'projet_id="PVT_kwHOB1x2Ms4A9k3Q"' for p in prefixes)

    fautifs = []
    for chemin in (RACINE / "scripts", RACINE / ".claude", RACINE / "docs"):
        for fichier in chemin.rglob("*"):
            if not fichier.is_file() or fichier.suffix in {".png", ".jpg", ".gz"}:
                continue
            texte = fichier.read_text(encoding="utf-8", errors="replace")
            for ligne in texte.splitlines():
                if any(p in ligne for p in prefixes):
                    fautifs.append(f"{fichier.relative_to(RACINE)} : {ligne.strip()[:100]}")
    assert not fautifs, "identifiant(s) Projects v2 figé(s) dans le dépôt :\n" + "\n".join(fautifs)


def test_le_projet_se_designe_par_son_titre_et_non_par_un_prefixe(depot: Depot) -> None:
    """La comparaison est une ÉGALITÉ DE CHAMP, dans le shell — jamais un `grep`.

    Le titre d'un projet est une DONNÉE, pas un motif : « Maestro » ne doit pas ramener les lignes
    de « Maestro v2 ». Ici les deux existent, et « Maestro v2 » est rendu EN PREMIER — un `head -1`
    sur un filtre approximatif poserait l'état dans le mauvais projet, en silence.
    """
    contexte = ["ticket\t360"]
    contexte += [f"item\t{PROJET} v2\tPVT_autre\tPVTI_autre\tEn cours"]
    contexte += [f"projet\t{PROJET} v2\tPVT_autre\tPVTSSF_autre"]
    contexte += [f"option\t{PROJET} v2\tPVT_autre_opt0\tÀ faire"]
    contexte += [f"item\t{PROJET}\t{ID_PROJET}\tPVTI_360\tEn revue"]
    contexte += lignes_projet()
    depot.pose_etat(
        graphql=[{"contient": ["projectItems(first:"], "brut": "\n".join(contexte) + "\n"}]
    )

    acheve = depot.lib("issue-owner", "360")
    assert acheve.returncode == 0, acheve.stderr
    assert acheve.stdout.split("\t")[0] == "En revue", "l'état lu est celui du projet « Maestro »"


# =================================================================================================
# #361 — Le peuplement : un ticket hors projet n'a AUCUN état
# =================================================================================================


def test_project_add_fait_du_ticket_un_item_et_pose_a_faire_par_defaut(depot: Depot) -> None:
    """« À faire », c'est-à-dire l'état d'un ticket qui vient de naître.

    Rien côté forge ne pose d'état par défaut : sans ce verbe, un ticket créé est un ticket
    invisible à toute requête de cycle de vie — en plus silencieux qu'un ticket sans état, puisque
    rien à l'écran ne le distingue d'un ticket absent du filtre.
    """
    depot.pose_etat(
        graphql=[
            regle_projet_resolu(),
            regle_node_id("375"),
            regle_ajout_item(),
            regle_pose_status(),
        ]
    )

    acheve = depot.lib("project-add", "375")
    assert acheve.returncode == 0, acheve.stderr
    assert "item du projet" in acheve.stdout
    assert "« À faire »" in acheve.stdout
    assert option_posee(depot) == "À faire"


def test_project_add_pose_la_valeur_nommee_jamais_une_devinee(depot: Depot) -> None:
    """C'est ce qui en fait aussi la RÉPARATION d'un ticket signalé hors projet.

    L'appelant NOMME la valeur qu'il veut : un verbe qui la devinerait — « il est fermé, donc
    Terminé » — inventerait la donnée qu'on cherche justement à retrouver.
    """
    depot.pose_etat(
        graphql=[
            regle_projet_resolu(),
            regle_node_id("375"),
            regle_ajout_item(),
            regle_pose_status(),
        ]
    )

    acheve = depot.lib("project-add", "375", "En revue")
    assert acheve.returncode == 0, acheve.stderr
    assert option_posee(depot) == "En revue"


def test_project_add_rejoue_a_l_identique_ne_cree_pas_de_doublon(depot: Depot) -> None:
    """L'idempotence est celle de `addProjectV2ItemById`, qui rend l'item DÉJÀ LÀ.

    C'est ce qui dispense de vérifier avant d'ajouter, et ce qui rend le verbe rejouable sans
    lecture d'ensemble — un backfill de rattrapage n'a donc jamais besoin d'un état à lui.
    """
    depot.pose_etat(
        graphql=[
            regle_projet_resolu(),
            regle_node_id("375"),
            regle_ajout_item(),
            regle_pose_status(),
        ]
    )

    premier = depot.lib("project-add", "375", "En cours")
    second = depot.lib("project-add", "375", "En cours")
    assert premier.returncode == 0 and second.returncode == 0
    assert premier.stdout == second.stdout


def test_project_add_refuse_une_valeur_inconnue_avant_d_ajouter(depot: Depot) -> None:
    """Ajouter puis échouer sur la valeur laisserait un item sans état — la dérive même de #363."""
    depot.pose_etat(
        graphql=[
            regle_projet_resolu(),
            regle_node_id("375"),
            regle_ajout_item(),
            regle_pose_status(),
        ]
    )

    acheve = depot.lib("project-add", "375", "En attente")
    assert acheve.returncode == 1
    assert "inconnue" in acheve.stderr
    assert "addProjectV2ItemById" not in "\n".join(depot.appels())


def test_project_add_dit_quoi_rejouer_quand_seule_la_pose_echoue(depot: Depot) -> None:
    """Le ticket est un item, son Status n'est pas posé : l'état intermédiaire se NOMME.

    « Rejouer la commande : l'ajout ne sera pas dupliqué » est ce qui distingue un demi-succès
    rattrapable d'un échec dont on ne sait pas ce qu'il a laissé derrière lui.
    """
    depot.pose_etat(
        graphql=[
            regle_projet_resolu(),
            regle_node_id("375"),
            regle_ajout_item(),
            {"contient": ["updateProjectV2ItemFieldValue"], "reponse": {"errors": [{"m": "non"}]}},
        ]
    )

    acheve = depot.lib("project-add", "375")
    assert acheve.returncode == 1
    assert "est bien un item" in acheve.stderr
    assert "Rejouer la commande" in acheve.stderr


def test_la_creation_de_ticket_peuple_le_projet_dans_la_foulee() -> None:
    """« Pas plus tard, pas quand on y pensera » — sans quoi la dérive naît à chaque ticket.

    Le prompt est la source unique du geste : c'est lui que la session lit, et un verbe qui existe
    sans être appelé ne peuple rien. Le contrôle est textuel à dessein — vérifier l'appel
    demanderait de jouer une session Claude Code, ce qu'aucune suite ne fait.
    """
    prompt = (RACINE / ".claude/commands/ticket-create.md").read_text(encoding="utf-8")
    assert "project-add" in prompt, "/ticket-create doit ajouter le ticket au projet à la création"


def test_le_backfill_n_existe_plus_et_rien_ne_le_propose(depot: Depot) -> None:
    """Il est parti AVEC les labels (#365), et ce n'est pas un oubli.

    `gl_project_backfill` dérivait le Status du label `workflow::*` courant et de RIEN D'AUTRE :
    bonne source tant que le label faisait foi, photo périmée après la bascule, plus rien du tout
    après leur retrait. Le proposer encore enverrait chercher un verbe absent ; le réécrire « en
    masse » poserait un état par défaut sur des tickets anciens, c'est-à-dire inventerait la donnée
    qu'on vient de perdre. La détection est `doctor.sh`, la réparation `project-add`, à l'unité.
    """
    acheve = depot.lib("project-backfill")
    assert acheve.returncode != 0
    assert "project-backfill" not in depot.lib().stderr


# =================================================================================================
# #362 — Les lectures d'ensemble : le recouvrement par la carte
# =================================================================================================
# « LA MÉTHODE EST UN RECOUVREMENT, PAS UNE RÉÉCRITURE » : le JSON des tickets reste la source de
# QUI EXISTE, la carte des items celle de QUEL ÉTAT. Lister depuis les items ferait DISPARAÎTRE de
# /backlog tout ticket hors projet — c'est-à-dire exactement ceux qu'on veut voir signalés.


def test_la_table_du_backlog_porte_l_etat_lu_dans_la_carte(depot: Depot) -> None:
    """Le contrat des six appelants d'ensemble tient dans cette colonne-là."""
    depot.pose_etat(graphql=regles_backlog({"11": "En cours", "12": "En revue"}))

    acheve = depot.lib("backlog-table")
    assert acheve.returncode == 0, acheve.stderr
    assert {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)} == {
        "11": "En cours",
        "12": "En revue",
    }


def test_un_ticket_hors_projet_reste_dans_la_table_avec_un_etat_absent(depot: Depot) -> None:
    """Il SORT avec « - », il ne DISPARAÎT pas — et c'est tout l'intérêt du recouvrement.

    « - » était déjà, au caractère près, ce que rendait un ticket à 0 label du cycle de vie : les
    six appelants héritent du contrat sans le savoir.
    """
    depot.pose_etat(
        graphql=regles_carte({"11": "En cours"})
        + regles_backlog({"11": "En cours", "375": "En cours"})[-1:]
    )

    acheve = depot.lib("backlog-table")
    assert acheve.returncode == 0, acheve.stderr
    assert {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)} == {
        "11": "En cours",
        "375": "-",
    }


def test_un_item_au_status_vide_sort_comme_un_ticket_hors_projet(depot: Depot) -> None:
    """Même sortie, autre cause — et c'est #363 qui les distingue, pas la table.

    Le garder ici dit que la table N'A PAS à les distinguer : son contrat est « cet iid a-t-il un
    état ? ». Lui faire porter la cause obligerait les six appelants à la lire.
    """
    depot.pose_etat(graphql=regles_backlog({"11": "En cours", "12": ""}))

    acheve = depot.lib("backlog-table")
    assert {ligne[0]: ligne[1] for ligne in colonnes(acheve.stdout)} == {
        "11": "En cours",
        "12": "-",
    }


def test_les_derives_ne_rendent_que_les_tickets_sans_etat(depot: Depot) -> None:
    """La dérive a perdu une moitié, et c'est le GAIN du chantier.

    Un champ single-select ne peut pas porter deux valeurs : le « ≥ 2 » que traquait le backend
    labels est impossible par construction. Ne reste que le « 0 ».
    """
    depot.pose_etat(
        graphql=regles_carte({"11": "En cours", "12": ""})
        + regles_backlog({"11": "", "12": "", "375": ""})[-1:]
    )

    acheve = depot.lib("workflow-derives")
    assert acheve.returncode == 0, acheve.stderr
    assert {ligne[0] for ligne in colonnes(acheve.stdout)} == {"12", "375"}


def test_la_carte_n_est_demandee_qu_une_fois_pour_deux_tables(depot: Depot) -> None:
    """LA raison d'être de la mémoire : `queue.sh` demandait la carte DEUX fois, une par table.

    Le prix n'est pas dans le nombre d'appels mais dans le prix unitaire d'une page de Projects v2
    (~13 s sur 366 items) : le seul levier est de ne pas la demander deux fois. La mémoire ne vit
    que dans le PROCESSUS — d'où deux tables demandées par redirection dans un seul shell, ce que
    fait `queue.sh` et ce que reproduit ce test.
    """
    depot.pose_etat(graphql=regles_backlog({"11": "En cours"}))

    acheve = depot.bash_inline(
        ". scripts/gitlab/lib.sh\n"
        "gl_backlog_table opened >/dev/null\n"
        "gl_workflow_derives opened >/dev/null\n"
    )
    assert acheve.returncode == 0, acheve.stderr
    pages = [a for a in depot.appels() if "items(first:100" in a]
    assert len(pages) == 1, f"la carte a été relue {len(pages)} fois"


def test_une_ecriture_perime_la_memoire_de_la_carte(depot: Depot) -> None:
    """Oubliée au SEUL endroit qui écrit le champ — donc sans raisonner appelant par appelant.

    Un processus qui pose un état puis relit une table doit voir son écriture ; sans l'oubli, il
    lirait la carte d'avant, et rien à l'écran ne le dirait.
    """
    depot.pose_etat(
        graphql=[
            regle_owner("À faire", []),
            regle_pose_status(),
            *regles_backlog({"11": "À faire"}),
        ]
    )

    acheve = depot.bash_inline(
        ". scripts/gitlab/lib.sh\n"
        "gl_backlog_table opened >/dev/null\n"
        "gl_set_workflow 11 'En cours' >/dev/null\n"
        "gl_backlog_table opened >/dev/null\n"
    )
    assert acheve.returncode == 0, acheve.stderr
    pages = [a for a in depot.appels() if "items(first:100" in a]
    assert len(pages) == 2, "la table d'après l'écriture doit repartir d'une carte fraîche"


def test_la_memoire_de_la_carte_s_eteint(depot: Depot) -> None:
    """`MAESTRO_CYCLE_MEMO=0` — une mémoire qu'on ne peut éteindre est une mémoire qu'on subit."""
    depot.pose_etat(graphql=regles_backlog({"11": "En cours"}))

    acheve = depot.bash_inline(
        ". scripts/gitlab/lib.sh\n"
        "gl_backlog_table opened >/dev/null\n"
        "gl_backlog_table opened >/dev/null\n",
        reglages={"MAESTRO_CYCLE_MEMO": "0"},
    )
    assert acheve.returncode == 0, acheve.stderr
    assert len([a for a in depot.appels() if "items(first:100" in a]) == 2


def test_un_backlog_illisible_n_est_jamais_rendu_comme_une_table_vide(depot: Depot) -> None:
    """LE piège du tube, et ce qu'il coûterait.

    `gh_… | st_overlay_statut` rendrait le code du DERNIER maillon (bash, sans `pipefail`), donc 0
    même quand la lecture des tickets a échoué. L'appelant recevrait un en-tête seul AVEC un code
    de succès — et `queue.sh`, dont le `|| exit 1` ne verrait rien, partirait sur un backlog vide où
    chaque ticket paraît LIBRE, c'est-à-dire prenable à quelqu'un d'autre.
    """
    depot.pose_etat(graphql=regles_carte({"11": "En cours"}))  # la carte répond, le backlog non

    acheve = depot.lib("backlog-table")
    assert acheve.returncode != 0, "un backlog illisible doit se voir dans le code de retour"
    assert not colonnes(acheve.stdout), "et ne pas rendre de table"


def test_le_json_brut_du_backlog_ne_porte_aucun_etat(depot: Depot) -> None:
    """Ce qui n'est PAS recouvert : `backlog` rend la réponse de la forge, telle quelle.

    Y injecter un Status en ferait une projection déguisée, et le seul verbe qui montre la donnée
    non interprétée n'existerait plus. Conséquence à connaître : qui veut l'état lit la TABLE.
    """
    depot.pose_etat(graphql=regles_backlog({"11": "En cours"}))

    acheve = depot.lib("backlog")
    assert acheve.returncode == 0, acheve.stderr
    assert "En cours" not in acheve.stdout
    assert not [a for a in depot.appels() if "items(first:100" in a], "aucune carte à lire ici"


# =================================================================================================
# #363 — Les dérives, vues par doctor.sh
# =================================================================================================
# ⚠ CETTE SECTION EST ÉCRITE POUR SURVIVRE AU MERGE DE #363, non mergé à l'écriture : elle assert ce
# que `main` et sa branche garantissent également — le ticket nommé, le geste nommé —, jamais la
# formulation, que le lot change légitimement. Jouée des deux côtés, 67/67 (docstring du module).


def section(sortie: str, titre: str, suivante: str) -> str:
    """Isole une section du bilan, de son titre au titre suivant."""
    debut = sortie.index(titre)
    reste = sortie[debut:]
    fin = reste.find(suivante)
    return reste if fin < 0 else reste[:fin]


def regle_derives_par_ticket(statuts: dict[str, str]) -> dict:
    """La lecture des dérives **centrée ticket** — celle que #363 apporte, aplatie.

    ⚠ ELLE EXISTE ICI POUR QUE LE DOUBLE SERVE LES DEUX VERSIONS DE `doctor.sh` §4c, et c'est ce
    qui rend l'INTERSECTION mesurable au lieu d'être affirmée (cf. docstring du module) : celle de
    `main` interroge la CARTE du projet (`gl_workflow_derives`, une page d'items), celle de #363
    interroge les TICKETS et leurs items (une requête, qui sait dire d'un ticket qu'il n'est dans
    aucun projet — or c'est la moitié cherchée). Chacune prend la règle qui lui répond, la seconde
    étant simplement ignorée par l'autre.

    Un iid absent de `statuts` est hors projet — aucune ligne `item` ; un libellé vide est un item
    au Status non posé. Ce sont les deux causes que #363 sépare et que `main` fond sous un seul
    message.
    """
    lignes = [f"total\t{len(statuts)}"]
    lignes += [f"ticket\t{iid}" for iid in statuts]
    lignes += [
        f"item\t{iid}\t{PROJET}\t{statut}" for iid, statut in statuts.items() if statut is not None
    ]
    return {"contient": ["issues(states: OPEN, first: 100)"], "brut": "\n".join(lignes) + "\n"}


def regles_doctor(
    statuts: dict[str, str],
    options: list[str] | None = None,
    hors_projet: list[str] | None = None,
) -> list[dict]:
    """Un dépôt vu par `doctor.sh` : le projet, ses options, le backlog ouvert et ses dérives.

    `hors_projet` liste les tickets qui EXISTENT sans être items du projet — ils sont dans le
    backlog et dans la lecture des dérives, mais ni dans la carte ni dans les items par ticket.
    """
    if options is None:
        lignes = lignes_projet()
    else:
        lignes = [f"projet\t{PROJET}\t{ID_PROJET}\t{ID_CHAMP}"] + [
            f"option\t{PROJET}\t{ID_PROJET}_opt{i}\t{libelle}" for i, libelle in enumerate(options)
        ]
    absents = {iid: None for iid in (hors_projet or [])}
    return [
        {"contient": ["options{id name}"], "brut": "\n".join(lignes) + "\n"},
        regle_derives_par_ticket({**statuts, **absents}),
        *regles_carte(statuts),
        *regles_backlog({**statuts, **{iid: "" for iid in absents}})[-1:],
    ]


def test_doctor_valide_les_six_options_resolues_par_nom(depot: Depot) -> None:
    """Pendant exact du contrôle des six labels que #365 a retirés, sur l'objet qui les remplace."""
    depot.pose_etat(graphql=regles_doctor({"11": "En cours"}))

    bilan = section(depot.doctor().stdout, "3. Cycle de vie", "\n4. ")
    assert "6 options du champ Status" in bilan
    assert "aucun ID en dur" in bilan


def test_doctor_nomme_l_option_manquante_et_la_commande_qui_la_pose(depot: Depot) -> None:
    """Une valeur absente est un état que `set-workflow` ne pourra JAMAIS poser.

    Le nommer sans nommer le geste enverrait chercher dans l'interface web ce que le monteur pose
    en une commande — et c'est ce script qui est la source unique du réglage.
    """
    depot.pose_etat(graphql=regles_doctor({"11": "En cours"}, options=list(SIX_LIBELLES[:-1])))

    acheve = depot.doctor()
    bilan = section(acheve.stdout + acheve.stderr, "3. Cycle de vie", "\n4. ")
    assert "Doublon" in bilan
    assert "bootstrap-project.sh" in bilan


def test_doctor_dit_le_champ_illisible_au_lieu_de_le_declarer_conforme(depot: Depot) -> None:
    """Un projet qu'on n'a pas su lire n'est pas un projet conforme.

    C'est la panne de #341, dans le seul fichier du dépôt dont le métier est de détecter les
    dérives : un ✓ sur une question jamais posée.
    """
    depot.pose_etat(graphql=regles_backlog({"11": "En cours"}))  # aucune règle pour le projet

    acheve = depot.doctor()
    bilan = section(acheve.stdout + acheve.stderr, "3. Cycle de vie", "\n4. ")
    assert "illisible" in bilan
    assert "6 options" not in bilan


def test_doctor_nomme_le_ticket_ouvert_sans_etat_et_sa_reparation(depot: Depot) -> None:
    """La dérive propre au dispositif : l'état vit sur l'ITEM, pas sur l'issue.

    Elle est plus silencieuse que le « 0 label » qu'elle remplace : rien à l'écran ne distingue un
    ticket sans état d'un ticket absent du filtre, et il sort de TOUS les comptes — `queue.sh` ne le
    verra pas.
    """
    depot.pose_etat(graphql=regles_doctor({"11": "En cours"}, hors_projet=["375"]))

    bilan = section(depot.doctor().stdout, "4. Dérive cycle de vie", "\n5. ")
    # Le TICKET est nommé et le GESTE aussi ; la FORMULATION, elle, appartient à chaque version —
    # `main` fond les deux causes en un message, #363 les sépare. L'épingler ici ferait échouer le
    # contrôle sur un merge qui ne change rien à ce qu'il garde.
    assert "#375" in bilan
    assert "project-add 375" in bilan
    assert "#11" not in bilan, "un ticket qui a un état n'est pas une dérive"


def test_doctor_ne_signale_aucune_derive_sur_un_depot_sain(depot: Depot) -> None:
    """Un bilan qui alerte à vide n'est plus lu — le contre-test compte autant que le test.

    Aucun ticket « En revue » dans le décor : §4a en réclamerait une PR ouverte, et le dépôt ne
    serait plus sain — pour une dérive qui n'est pas celle qu'on regarde ici.
    """
    depot.pose_etat(graphql=regles_doctor({"11": "En cours", "12": "À faire"}))

    bilan = section(depot.doctor().stdout, "4. Dérive cycle de vie", "\n5. ")
    assert "tous les tickets ouverts" in bilan, "le ✓ doit dire ce qu'il a regardé"
    assert "#11" not in bilan and "#12" not in bilan, "aucun ticket ne doit être nommé"


def test_doctor_ne_repare_jamais_la_derive_qu_il_nomme(depot: Depot) -> None:
    """« Lecture seule » est sa promesse, et elle vaut aussi pour ce qu'il sait réparer d'un mot."""
    depot.pose_etat(graphql=regles_doctor({}, hors_projet=["375"]))

    depot.doctor()
    assert not ecritures(depot)
    assert not mutations(depot)


# =================================================================================================
# #364 et #365 — Un seul support, et plus rien pour en choisir un autre
# =================================================================================================


def test_aucun_label_de_cycle_de_vie_ne_subsiste_dans_le_depot() -> None:
    """Le critère du lot #365, gardé par un `grep` parce que c'est ce qu'il promet.

    Les six labels ont porté le cycle de vie de #207 à #364. Un seul survivant — une lecture dans un
    script, une pose dans un prompt — ferait deux supports, et « le premier symptôme de deux
    supports est un ticket qui porte deux états ».

    ⚠ `scripts/migration/` EST EXCLU, et ce n'est pas un trou dans le contrôle : ces deux scripts
    lisent l'ARCHIVE GitLab, où les six labels ont réellement porté le cycle de vie de #207 à la
    bascule de forge. Leur demander de ne plus les nommer reviendrait à leur demander de ne plus
    savoir lire ce qu'ils exportent. Ce que le lot #365 promet porte sur le dispositif VIVANT — ce
    qui pose et lit l'état d'aujourd'hui —, pas sur la lecture d'un passé figé.
    """
    # Même précaution que pour le commutateur : le motif est prouvé avant d'être cru sur parole.
    assert re.search(_LABEL_MORT, 'glab issue update 1 --label "workflow::en-cours"')
    assert not re.search(_LABEL_MORT, "  - uses: actions/checkout@v5  # .github/workflows")

    fautifs = []
    for dossier in (RACINE / "scripts", RACINE / ".claude"):
        for fichier in dossier.rglob("*"):
            if not fichier.is_file() or fichier.parent.name == "migration":
                continue
            texte = fichier.read_text(encoding="utf-8", errors="replace")
            for numero, ligne in enumerate(texte.splitlines(), start=1):
                # Le nom du label, pas le mot « workflow » : `.github/workflows`, `labelsWidget` et
                # les commentaires d'histoire ne sont pas des poses de label.
                if re.search(_LABEL_MORT, ligne):
                    fautifs.append(f"{fichier.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "label(s) workflow:: encore vivant(s) :\n" + "\n".join(fautifs)


def test_aucun_commutateur_ne_choisit_plus_de_support() -> None:
    """`MAESTRO_CYCLE` est parti avec les labels, et ne doit pas revenir « au cas où ».

    Tant que les labels étaient là, rebasculer coûtait une variable d'environnement ; ils sont
    partis, donc rebasculer coûterait une migration. Une variable épinglée qui ne commande plus rien
    est ce qui fait croire à un backend qu'on aurait encore le choix de servir — c'est le motif pour
    lequel `tests/conftest.py` a retiré son cinquième garde-fou.

    ⚠ CE QUI EST CHERCHÉ EST UN USAGE, PAS UNE MENTION — une LECTURE (`${MAESTRO_CYCLE:-…}`) ou une
    POSE (`MAESTRO_CYCLE=…`). Les commentaires qui RACONTENT son retrait sont ce que ce chantier
    demande d'écrire, ici comme en tête de `lib.sh` et dans `tests/conftest.py` : les compter pour
    des résurrections rendrait le test impossible à satisfaire autrement qu'en effaçant la mémoire
    du chantier, ce qui est exactement l'inverse du but. Ce module est exclu pour la même raison —
    il est celui qui l'explique, comme `scripts/migration/` l'est du contrôle des labels.

    ⚠ `MAESTRO_CYCLE_MEMO` n'est pas un commutateur de support : il éteint la MÉMOIRE de la carte
    (#362), qui est un cache, pas un backend. D'où la borne du motif.
    """
    usage = re.compile(
        r"""\$\{?MAESTRO_CYCLE(?!_MEMO)\b|^\s*MAESTRO_CYCLE(?!_MEMO)\b\s*=|["']MAESTRO_CYCLE["']"""
    )
    # Le motif est prouvé sur les trois formes qu'aurait une résurrection, et sur celle qu'il doit
    # laisser passer : un `grep` qui ne trouve rien parce qu'il ne cherche rien est le pire des ✓.
    assert usage.search('  cycle="${MAESTRO_CYCLE:-labels}"')
    assert usage.search("MAESTRO_CYCLE=labels")
    assert usage.search('    os.environ["MAESTRO_CYCLE"] = "labels"')
    assert not usage.search('if [ "${MAESTRO_CYCLE_MEMO:-1}" != 0 ]; then')
    fautifs = []
    for dossier in (RACINE / "scripts", RACINE / ".claude", RACINE / "tests"):
        for fichier in dossier.rglob("*"):
            if not fichier.is_file() or fichier.suffix == ".pyc" or fichier == Path(__file__):
                continue
            texte = fichier.read_text(encoding="utf-8", errors="replace")
            for numero, ligne in enumerate(texte.splitlines(), start=1):
                if usage.search(ligne):
                    fautifs.append(f"{fichier.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "commutateur de backend ressuscité :\n" + "\n".join(fautifs)


def test_poser_un_etat_ne_touche_a_aucun_label(depot: Depot) -> None:
    """Le pendant vivant du `grep` : une pose d'état n'écrit RIEN sur l'issue.

    Le `PATCH /issues/:n` ne porte plus que la liste des assignés. Lui faire porter des labels
    reviendrait à réécrire l'ensemble complet — l'endpoint REMPLACE, il n'ajoute pas —, donc à
    devoir d'abord les lire pour ne rien perdre, pour une écriture que personne ne demande.
    """
    depot.pose_etat(graphql=[regle_owner("À faire", []), regle_pose_status()])

    depot.lib("set-workflow", "364", "En cours")
    assert not ecritures(depot), "l'état ne vit plus sur l'issue : rien n'a à y être écrit"


def test_le_cycle_de_vie_est_annonce_comme_le_champ_status(depot: Depot) -> None:
    """L'usage de `lib.sh` est ce qu'on lit en premier — il doit nommer le support d'aujourd'hui."""
    usage = depot.lib().stderr
    assert "champ Status" in usage
    assert "labels workflow" not in usage


def test_les_huit_commandes_posent_l_etat_par_le_helper_et_jamais_a_la_main() -> None:
    """La couture unique : `set-workflow`, et rien d'autre.

    C'est elle qui a permis de changer deux fois de support sans qu'aucun prompt bouge — et c'est
    elle qu'un `gh issue edit --add-label` contournerait, en remettant l'état sur l'issue.
    """
    fautifs = []
    for prompt in (RACINE / ".claude/commands").glob("*.md"):
        texte = prompt.read_text(encoding="utf-8")
        for numero, ligne in enumerate(texte.splitlines(), start=1):
            if re.search(r"--(add|remove)-label", ligne):
                fautifs.append(f"{prompt.relative_to(RACINE)}:{numero} : {ligne.strip()[:90]}")
    assert not fautifs, "pose de cycle de vie hors de set-workflow :\n" + "\n".join(fautifs)


# =================================================================================================
# Le tour complet : ce que voit une session qui démarre un ticket
# =================================================================================================
# Les sept lots ne valent que s'ils se tiennent bout à bout — c'est le troisième critère du parent
# (« le workflow complet tourne de bout en bout sur le Status »). Ce test-ci en est la couture.


def test_start_brief_lit_l_etat_du_champ_pour_dire_si_le_ticket_est_libre(depot: Depot) -> None:
    """Le préflight d'un `/ticket-start`, dans les deux verdicts qui décident de démarrer ou non."""
    depot.pose_etat(
        graphql=[regle_owner("À faire", [])],
        issues={"366": corps_ticket("Tests + doc", "type::infra", "## Critères\n\n- [ ] vert")},
    )

    libre = depot.lib("start-brief", "366")
    assert libre.returncode == 0, libre.stderr
    assert "libre" in libre.stdout

    depot.pose_etat(graphql=[regle_owner("En cours", ["bea"])])
    pris = depot.lib("start-brief", "366")
    assert "déjà pris par bea" in pris.stdout


def test_close_guard_ne_prend_pas_un_ticket_sans_etat_pour_un_feu_vert(depot: Depot) -> None:
    """La lecture rend « pas d'état » ; le garde-fou ne doit pas y lire « ticket à moi ».

    C'est le seul endroit où l'asymétrie écriture/lecture de #360 pourrait coûter cher : un ticket
    hors projet est lu sans erreur, et il ne faut pas que ce silence autorise une clôture.
    """
    depot.git("checkout", "--quiet", "-b", "chore/366-tests-doc")
    depot.pose_etat(graphql=[regle_owner("", [], dans_projet=False)])

    acheve = depot.lib("close-guard", "366")
    assert acheve.returncode == 0, "un ticket sans état ne bloque pas — la branche fait foi"
    assert not ecritures(depot)


def test_le_plan_d_orchestration_ne_retient_que_les_tickets_a_faire_et_libres(depot: Depot) -> None:
    """Le filtre de `queue.sh` est une CONJONCTION, et il lit la colonne `statut` de la table.

    Basculer les deux producteurs a basculé les six appelants : c'est ce que ce test garde, sur le
    consommateur du chemin critique.
    """
    depot.pose_etat(
        graphql=regles_backlog({"11": "À faire", "12": "En cours", "13": "À faire"})
    )
    table = depot.lib("backlog-table").stdout
    a_faire = {ligne[0] for ligne in colonnes(table) if ligne[1] == "À faire"}
    assert a_faire == {"11", "13"}
