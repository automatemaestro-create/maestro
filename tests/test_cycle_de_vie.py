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

Un huitième lot s'y est greffé après coup, et il n'est pas du chantier #358 : **#377** donne au
cycle de vie un **déclencheur** — un workflow GitHub Actions sur `issues: closed` — là où les sept
lots ci-dessus lui donnaient un support. Ses tests sont en fin de module, sur le même harnais : ce
qui s'y joue est la **décision** (`scripts/github/ticket-ferme.sh`), le YAML n'étant qu'un
déclencheur qu'on garde par `grep`.

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
    reponse_owner,
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


def libelle_pose(ligne: str) -> str | None:
    """Le LIBELLÉ qu'une mutation a réellement posé, relu depuis son id d'option.

    C'EST L'ALLER-RETOUR, dans le seul sens qu'un double puisse attester : le verbe reçoit un
    libellé, résout l'option PAR NOM, et c'est cet identifiant-là — inventé par le harnais, écrit
    nulle part dans le dépôt — que porte la mutation. Le relire par la table inverse referme la
    boucle : libellé → id d'option → libellé.
    """
    ids = {f"{ID_PROJET}_opt{i}": libelle for i, libelle in enumerate(LIBELLES_WORKFLOW)}
    trouve = re.search(r'singleSelectOptionId: \\?"([^"\\]+)', ligne)
    if not trouve:
        return None
    return ids.get(trouve.group(1), f"id inconnu : {trouve.group(1)}")


def option_posee(depot: Depot) -> str | None:
    """Le libellé posé par la DERNIÈRE mutation du journal, quel qu'en soit le ticket."""
    for ligne in reversed(depot.appels()):
        if "updateProjectV2ItemFieldValue" not in ligne:
            continue
        libelle = libelle_pose(ligne)
        if libelle is not None:
            return libelle
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
            # ⚠ `__pycache__` est écarté par le RÉPERTOIRE et non par le suffixe (#345). Python
            # écrit son bytecode sous `<nom>.pyc.<pid>` avant de le renommer, et un worker xdist
            # tué en laisse derrière lui : `suffix` y vaut alors « .2594 », pas « .pyc ». Le
            # balayage lisait donc le bytecode de CE module — qui contient forcément les littéraux
            # `MAESTRO_CYCLE` du motif — et rendait un rouge dont la cause n'a rien à voir avec le
            # dépôt. C'est le seul des trois `grep` de ce fichier qui inclut `tests/`, donc le seul
            # concerné. Un cache n'est pas une source : l'exclure ne réduit pas la portée.
            if "__pycache__" in fichier.parts:
                continue
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


# =================================================================================================
# #377 — La pose à l'ÉVÉNEMENT : `on: issues: [closed]`
# =================================================================================================
# Les sept lots ci-dessus donnent au cycle de vie un support ; celui-ci lui donne un DÉCLENCHEUR
# qui ne dépend d'aucune machine. Jusque-là « Terminé » n'était posé que par `worktree.sh gc`
# (#275) — donc au prochain `/ticket-start`, `/branch-cleanup` ou démarrage de run, et sur la seule
# machine qui les lance : le board était faux AU REPOS (§9.2). Trois tickets mergés le 2026-08-18
# affichaient encore « En revue » le lendemain, faute qu'un `/ticket-start` soit passé.
#
# CE QUI SE TESTE ICI EST LA DÉCISION, PAS LE DÉCLENCHEUR. Le workflow ne fait qu'appeler
# `scripts/github/ticket-ferme.sh` ; c'est ce script qui filtre, délègue et s'abstient — et lui
# seul se rejoue sur le dépôt jetable. Les deux derniers tests gardent cette répartition : un
# `if:` qui recopierait la condition dans le YAML la rendrait invérifiable ici.

#: Ce que GitHub met dans `state_reason` quand la fermeture ne vaut PAS livraison. « duplicate » a
#: été ajouté à l'énumération après coup, et c'est la raison d'être de la liste blanche : une liste
#: noire aurait laissé passer chaque valeur suivante.
RAISONS_SANS_LIVRAISON = ("not_planned", "duplicate", "")


def test_un_ticket_ferme_comme_realise_passe_a_termine(depot: Depot) -> None:
    """Le cas nominal : la PR est mergée, `Closes #<iid>` a fermé le ticket, l'état suit.

    C'est ce que personne ne faisait au repos — et la pose n'est pas réécrite par le script : elle
    est déléguée à `reconcile-workflow`, donc c'est bien l'option « Terminé » résolue par son nom
    qui part dans la mutation.
    """
    depot.pose_etat(graphql=[regle_owner("En revue", ["bea"]), regle_pose_status()])

    acheve = depot.ticket_ferme("377", "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert option_posee(depot) == "Terminé"


@pytest.mark.parametrize("raison", RAISONS_SANS_LIVRAISON)
def test_une_fermeture_qui_ne_vaut_pas_livraison_ne_pose_aucun_etat(
    depot: Depot, raison: str
) -> None:
    """Barrière n°1 — la liste blanche, qui n'ouvre que sur « completed ».

    ⚠ CE TEST A PERDU UNE ASSERTION EN GAGNANT SON VRAI PÉRIMÈTRE (#515). Il exigeait une
    abstention TOTALE — « pas même une LECTURE » —, ce qui n'était juste que tant que ce script
    n'avait qu'une seule question à poser. Il en a deux depuis, et la seconde (« ce lot était-il le
    dernier de son parent ? ») vaut pour TOUTE raison de fermeture : un lot abandonné solde son
    parent comme un lot livré. La lecture qui l'établit est donc légitime, et l'interdire ferait
    exactement ce que #515 corrige.

    Ce que la barrière n°1 garde, elle, n'a pas bougé d'un caractère : sur une raison qui ne vaut
    pas livraison, AUCUN ÉTAT n'est posé — ni « Terminé », ni quoi que ce soit d'autre. C'est cette
    assertion-là qui porte la défense en profondeur, et c'est la seule que ce test ait jamais
    vraiment protégée ; `not depot.appels()` en était une conséquence de l'époque, pas la règle.

    La lecture supplémentaire n'est d'ailleurs plus gratuite à décrire : le ticket #377 n'étant
    décrit dans aucun `etat["issues"]`, elle échoue et la question 2 s'abstient — ce que le test de
    l'abstention sur un ticket illisible garde de son côté.
    """
    depot.pose_etat(graphql=[regle_owner("En revue", []), regle_pose_status()])

    acheve = depot.ticket_ferme("377", raison)
    assert acheve.returncode == 0, acheve.stderr
    assert not mutations(depot), "une fermeture sans livraison ne pose aucun état"
    assert "rien à poser" in acheve.stdout


@pytest.mark.parametrize("final", ["Abandonné", "Doublon"])
def test_un_ticket_abandonne_garde_son_etat_meme_ferme_comme_realise(
    depot: Depot, final: str
) -> None:
    """Barrière n°2 — l'ÉTAT COURANT, le filet du geste MANUEL fermé « as completed ».

    ⚠ CE TEST A CHANGÉ DE RÔLE SANS CHANGER D'UNE LIGNE, ET C'EST TOUT SON INTÉRÊT. #377 décrivait
    ce cas comme « un ticket fermé en `not_planned` (donc par `/ticket-abandon`) » — or la commande
    fermait alors par un `gh issue close <iid>` NU, et GitHub mettait `state_reason: completed`,
    comme sur n'importe quel merge. La barrière n°1 laissait donc entrer TOUT abandon, et seul le
    filtre d'état de `reconcile-workflow` (#275) l'arrêtait — une « défense en profondeur » à une
    seule couche. Ce qui la rendait possible était l'ORDRE de la commande : l'état est posé
    (étape 6) AVANT la fermeture (étape 7), donc il est déjà là quand ce script lit.

    **#388 a fermé cet écart** — l'étape 7 passe désormais `--reason "not planned"` dans les deux
    variantes, donc la n°1 écarte l'abandon avant même de lire l'état. Ce test est resté vert
    INCHANGÉ, et c'était l'un de ses critères : il ne jouait pas la commande mais la raison
    `completed`, qui reste celle d'un abandon fait à la main depuis l'interface web (ou d'une
    commande qui oublierait le `--reason`). C'est exactement ce cas-ci, et il n'a jamais cessé
    d'exister — seul l'a quitté ce que `/ticket-abandon` y déversait.
    """
    depot.pose_etat(graphql=[regle_owner(final, []), regle_pose_status()])

    acheve = depot.ticket_ferme("377", "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not mutations(depot), f"« {final} » a été écrasé par « Terminé »"
    assert "déjà à un état final" in acheve.stdout


def test_rejouer_sur_un_ticket_deja_termine_n_ecrit_rien(depot: Depot) -> None:
    """L'IDEMPOTENCE dans le seul sens qui compte : un second événement ne réécrit pas.

    Un ticket peut être fermé, rouvert, refermé ; le workflow rejoue alors sur un ticket qui porte
    déjà « Terminé ». Sauter l'écriture est le cas nominal en régime établi.
    """
    depot.pose_etat(graphql=[regle_owner("Terminé", []), regle_pose_status()])

    acheve = depot.ticket_ferme("377", "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not mutations(depot)


def test_sans_le_secret_rien_n_est_ecrit_et_le_journal_nomme_le_geste(depot: Depot) -> None:
    """Le secret est LE geste manuel du dispositif : son absence s'annonce, elle n'échoue pas.

    Tant que personne ne l'a posé, chaque fermeture de ticket passerait ici : un run rouge par
    ticket fermé ne dirait rien de plus que le premier, et ce que le journal doit porter est le
    geste qui manque — pas une pile d'échecs. Le filet de rattrapage (`worktree.sh gc`) est nommé
    au même endroit, parce que c'est ce qui tient en attendant.
    """
    depot.pose_etat(graphql=[regle_owner("En revue", []), regle_pose_status()])

    acheve = depot.ticket_ferme("377", "completed", reglages={"GH_TOKEN": ""})
    assert acheve.returncode == 0, acheve.stderr
    assert not depot.appels(), "sans jeton, on ne tente même pas la lecture"
    assert "MAESTRO_PROJECT_TOKEN" in acheve.stdout
    assert "worktree.sh gc" in acheve.stdout


def test_une_pose_en_echec_laisse_le_run_rouge_et_nomme_le_rattrapage(depot: Depot) -> None:
    """« Best-effort » ne veut pas dire « vert quoi qu'il arrive ».

    Un projet injoignable, un jeton périmé : la pose échoue et le script PROPAGE l'échec, ce qui
    laisse le run rouge dans l'onglet Actions — la seule visibilité qu'on puisse lui donner. Rien
    n'en dépend : ce workflow ne se déclenche que sur `issues: closed`, il ne conditionne aucun
    merge et n'entre dans aucune protection de branche.
    """
    depot.pose_etat(graphql=[regle_owner("En revue", [])])  # aucune règle pour la mutation

    acheve = depot.ticket_ferme("377", "completed")
    assert acheve.returncode == 1
    assert "reconcile-workflow 377" in acheve.stderr, "le rattrapage manuel est nommé"


@pytest.mark.parametrize("mauvais", ["", "377; rm -rf .", "trois-cent-soixante-dix-sept"])
def test_un_iid_qui_n_en_est_pas_un_est_refuse_avant_toute_lecture(
    depot: Depot, mauvais: str
) -> None:
    """L'iid vient de l'événement : il est validé plutôt que cru sur parole.

    Il ne traverse qu'un `bash "$lib" reconcile-workflow "$iid"`, jamais un `eval` — le contrôle
    est donc une ceinture. Mais c'est la donnée d'un événement, et la borner coûte une ligne.
    """
    depot.pose_etat(graphql=[regle_owner("En revue", []), regle_pose_status()])

    acheve = depot.ticket_ferme(mauvais, "completed")
    assert acheve.returncode == 2
    assert not depot.appels()


def test_le_workflow_declenche_sur_la_fermeture_et_delegue_toute_sa_decision() -> None:
    """Le YAML déclenche et transmet ; il ne décide pas — sinon rien de tout ce qui précède ne vaut.

    Un `if:` sur le `state_reason` recopierait la barrière n°1 dans un fichier qu'aucun test ne
    joue et qu'aucune commande locale ne rejoue : deux formulations du même filtre, dont une seule
    serait corrigée le jour où GitHub ajoutera une troisième raison de fermeture (il en a déjà
    ajouté une).
    """
    workflow = (RACINE / ".github" / "workflows" / "cycle-de-vie.yml").read_text(encoding="utf-8")

    assert re.search(r"^on:\s*$", workflow, re.MULTILINE)
    assert re.search(r"^\s+issues:\s*$", workflow, re.MULTILINE)
    assert re.search(r"types:\s*\[closed\]", workflow)
    assert "bash scripts/github/ticket-ferme.sh" in workflow

    # Le motif est prouvé sur la forme fautive AVANT d'être cru sur parole — et sur celle qu'il
    # doit laisser passer, la ligne d'environnement qui TRANSMET la raison sans en juger.
    decision = re.compile(r"^\s*if:.*state_reason", re.MULTILINE)
    assert decision.search("    if: github.event.issue.state_reason == 'completed'")
    assert not decision.search("          RAISON: ${{ github.event.issue.state_reason }}")
    assert not decision.search(workflow), "la décision est dans le script, pas dans le YAML"


def test_le_nom_du_secret_est_le_meme_partout_ou_il_est_nomme() -> None:
    """Trois endroits nomment le secret ; un quatrième nom serait une panne muette.

    Le workflow le LIT, le script dit ce qui manque quand il est vide, la doc dit comment le poser.
    Un renommage qui n'irait pas au bout laisserait un workflow silencieux (secret vide, abstention
    annoncée) que personne ne saurait relier au geste qui le réparerait.
    """
    secret = "MAESTRO_PROJECT_TOKEN"
    for relatif in (
        ".github/workflows/cycle-de-vie.yml",
        "scripts/github/ticket-ferme.sh",
        "docs/10-workflow-git.md",
    ):
        assert secret in (RACINE / relatif).read_text(encoding="utf-8"), relatif


def test_aucune_expression_du_workflow_n_atterrit_dans_un_run() -> None:
    """Une expression `${{ }}` est substituée AVANT que bash ne voie la ligne.

    C'est l'injection classique des Actions : un titre de ticket interpolé dans un `run:` s'exécute.
    Les deux données de la décision (numéro, raison) sont ici un entier et une énumération, mais la
    règle ne se relâche pas au cas par cas — toutes les expressions passent par le bloc `env:`.
    """
    workflow = (RACINE / ".github" / "workflows" / "cycle-de-vie.yml").read_text(encoding="utf-8")

    affectation = re.compile(r"^\s+[A-Z_]+: \$\{\{ [^{}]+ \}\}$")
    assert affectation.match("          GH_TOKEN: ${{ secrets.MAESTRO_PROJECT_TOKEN }}")
    assert not affectation.match("        run: bash ticket-ferme.sh ${{ github.event.issue.id }}")

    # Les COMMENTAIRES sont écartés : ce fichier explique la règle, donc il écrit la forme qu'elle
    # proscrit — et une ligne que YAML ne lit pas n'est exécutée par personne. Le contrôle ne vaut
    # que sur ce qui part au runner.
    commentaire = re.compile(r"^\s*#")
    assert commentaire.match("          # une expression `${{ }}` y est substituée AVANT bash")
    assert not commentaire.match("          TICKET: ${{ github.event.issue.number }}")

    fautives = [
        ligne
        for ligne in workflow.splitlines()
        if "${{" in ligne and not commentaire.match(ligne) and not affectation.match(ligne)
    ]
    assert not fautives, "expression hors du bloc env: :\n" + "\n".join(fautives)


# =================================================================================================
# #515 — La SECONDE question du même événement : « était-ce le dernier lot de son parent ? »
# =================================================================================================
# Un parent de suivi ne porte ni branche ni code : aucune PR ne le ferme par un `Closes #`, et sa
# fermeture était le seul geste du cycle d'un chantier resté MANUEL (§5.1 : « une décision
# humaine/orchestrateur »). Depuis #418/#419 un run se solde tout mergé — les lots se ferment tous
# dans la foulée, et il ne reste personne pour fermer le parent.
#
# CE QUI SE TESTE ICI EST LA MESURE, et elle tient en une phrase : un lot est soldé quand il est
# FERMÉ. Ni son cycle de vie (posé après coup, par l'événement d'où l'on vient), ni sa coche dans la
# checklist (tenue au fil de l'eau, donc best-effort) n'entrent dans la décision — et les deux tests
# qui le prouvent sont les seuls de la section à décrire un chantier incohérent exprès.

#: Le parent de suivi des chantiers de cette section, et la base de ses lots.
PARENT_SUIVI = "600"
PREMIER_LOT = 601


def chantier(*etats: str, coche: str = " ") -> dict[str, str]:
    """Un parent et ses lots, décrits par l'ÉTAT de chacun (« open »/« closed »), dans l'ordre.

    La coche de la checklist est un paramètre séparé, et par défaut FAUSSE (aucune case cochée,
    lots fermés compris) : c'est l'état d'un parent dont personne n'a synchronisé la description,
    et il ne doit rien changer au verdict. Un test le prouve explicitement ; tous les autres
    travaillent dessus sans y penser, ce qui est la meilleure garantie qu'elle ne sert à rien ici.

    LE CHANTIER PORTE LES DEUX SUPPORTS (#393) : checklist et prose dans les corps, en-têtes
    `parent:` / `lot:` du régime `natif`, qui est le défaut depuis ce lot. La coche NATIVE, elle,
    est dérivée de l'état (#390) et donc juste par construction — ce qui ne change rien au verdict,
    et le montre : les deux régimes concluent pareil sur des coches qui se contredisent.
    """
    lots = [str(PREMIER_LOT + i) for i in range(len(etats))]
    checklist = "\n".join(
        f"- [{coche}] #{iid} — Lot {rang}" for rang, iid in enumerate(lots, start=1)
    )
    issues = {
        PARENT_SUIVI: corps_ticket(
            "Chantier de suivi",
            "type::infra",
            f"## Sous-tickets\n\n{checklist}\n",
            lots=tuple(
                (iid, "x" if etat == "closed" else "-", "-", f"Lot {rang}")
                for rang, (iid, etat) in enumerate(zip(lots, etats, strict=True), start=1)
            ),
        )
    }
    for rang, (iid, etat) in enumerate(zip(lots, etats, strict=True), start=1):
        issues[iid] = corps_ticket(
            f"Lot {rang}",
            "type::infra",
            f"Sous-ticket de #{PARENT_SUIVI} — lot {rang}/{len(etats)}.\n\nCorps.",
            etat,
            parent=PARENT_SUIVI,
        )
    return issues


def fermeture_du_parent(depot: Depot) -> list[str]:
    """Les appels qui FERMENT le parent — le PATCH REST, isolé des autres écritures.

    Le commentaire passe par le même verbe REST (`gh api -X POST …/comments`) : filtrer sur « -X »
    seul les confondrait, et un test qui vérifie qu'on ne ferme PAS passerait alors au vert sur un
    script qui ferme sans commenter.
    """
    return [
        ligne
        for ligne in ecritures(depot)
        if "PATCH" in ligne and f"issues/{PARENT_SUIVI}" in ligne
    ]


def commentaires(depot: Depot, iid: str) -> list[str]:
    """Les commentaires postés sur un ticket (`gh api -X POST …/issues/<iid>/comments`)."""
    return [ligne for ligne in ecritures(depot) if f"issues/{iid}/comments" in ligne]


def test_la_fermeture_du_dernier_lot_ferme_le_parent(depot: Depot) -> None:
    """Le cas nominal, et la raison d'être du ticket : le dernier lot tombe, le parent suit.

    La fermeture est posée en `state_reason=completed` PLUTÔT QU'AU DÉFAUT DE L'API, et ce n'est pas
    un détail de forme : c'est ce champ que relit la liste blanche de `ticket-ferme.sh` au tour
    suivant. Un parent fermé « sans raison » en ressortirait « rien à poser » — fermé côté forge et
    resté « En cours » au board, c'est-à-dire la dérive exacte que #377 avait supprimée.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "closed", "closed"),
    )

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 2), "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    ferme = fermeture_du_parent(depot)
    assert len(ferme) == 1, f"le parent devait être fermé une fois — {ecritures(depot)}"
    assert "state=closed" in ferme[0]
    assert "state_reason=completed" in ferme[0]
    assert f"#{PARENT_SUIVI}" in acheve.stdout


def test_le_parent_ferme_recoit_un_commentaire_qui_dit_sur_quoi_il_s_appuie(depot: Depot) -> None:
    """Une fermeture automatique sans explication est une fermeture qu'on rouvre pour comprendre.

    Le commentaire nomme le lot déclencheur et le verbe : c'est ce qui distingue, pour qui relit le
    chantier six mois plus tard, une fermeture décidée d'une fermeture subie.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "closed"),
    )

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    postes = commentaires(depot, PARENT_SUIVI)
    assert len(postes) == 1, "un commentaire, et un seul"
    assert f"#{PREMIER_LOT + 1}" in postes[0], "le lot déclencheur est nommé"
    assert "ferme-parent" in postes[0], "le verbe qui a fermé est nommé"


def test_un_lot_encore_ouvert_laisse_le_parent_intact(depot: Depot) -> None:
    """La garde qui compte : tant qu'un lot est ouvert, le parent ne bouge pas.

    C'est aussi ce qui rend #515 et #394 compatibles sans qu'ils aient à se parler — la garde de
    #394 rouvre un parent fermé trop tôt, celle-ci ne ferme jamais trop tôt. Deux mécanismes sur le
    même événement, incapables de se déclencher l'un contre l'autre.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "open", "closed"),
    )

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 2), "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not fermeture_du_parent(depot), "un lot ouvert, donc un parent resté ouvert"
    assert not commentaires(depot, PARENT_SUIVI)
    # Le lot qui bloque est NOMMÉ : « il en reste » sans dire lequel oblige à rouvrir la checklist.
    assert f"#{PREMIER_LOT + 1}" in acheve.stdout


def test_un_ticket_qui_n_est_pas_un_lot_ne_coute_qu_une_lecture(depot: Depot) -> None:
    """L'abstention est le cas NOMINAL : la plupart des tickets fermés ne sont pas des lots.

    Ce verbe passe à chaque fermeture du dépôt. S'il lisait une checklist à chaque fois, il paierait
    sur tous les tickets le prix d'une question qui n'en concerne qu'une minorité — d'où un contrôle
    sur le NOMBRE DE LECTURES, et pas seulement sur l'absence d'écriture.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues={"700": corps_ticket("Ticket ordinaire", "type::infra", "Aucun parent ici.")},
    )

    acheve = depot.ticket_ferme("700", "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not ecritures(depot), "rien n'est fermé ni commenté"
    assert "n'est pas un lot" in acheve.stdout

    vues = [ligne for ligne in depot.appels() if "issue(number:700)" in ligne and "body }" in ligne]
    assert len(vues) == 1, "une seule lecture du ticket pour répondre « pas un lot »"


@pytest.mark.parametrize("raison", RAISONS_SANS_LIVRAISON)
def test_un_lot_abandonne_ferme_son_parent_comme_un_lot_livre(depot: Depot, raison: str) -> None:
    """Le critère qui a fait sortir la question 2 de dessous la liste blanche de la question 1.

    Un chantier peut parfaitement se terminer sur un lot qu'on RENONCE à faire : « Abandonné » et
    « Doublon » ferment le ticket au même titre que « Terminé », donc ils le soldent. Ranger la
    fermeture du parent derrière le filtre « completed » — ce qu'aurait fait le plus court des deux
    chemins — laisserait ce parent-là ouvert pour toujours, et personne ne saurait dire pourquoi.

    ⚠ Aucun état n'est posé pour autant : le parent est fermé, la barrière n°1 tient sur SA moitié.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "closed"),
    )

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), raison)
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert "rien à poser" in acheve.stdout, "la question 1 s'abstient toujours"
    assert not mutations(depot), "un abandon ne pose aucun cycle de vie"
    assert len(fermeture_du_parent(depot)) == 1, "la question 2, elle, a bien été posée"


def test_la_coche_de_la_checklist_ne_decide_de_rien(depot: Depot) -> None:
    """Deux chantiers identiques aux cases près : elles ne changent pas le verdict.

    La coche est tenue au fil de l'eau par `/ticket-ship`, donc best-effort — un lot mergé depuis
    l'interface web n'en coche aucune. Faire dépendre une fermeture d'une synchronisation qui peut
    manquer reconstruirait à l'identique le geste manuel qu'on retire.
    """
    for coche in (" ", "x"):
        depot.journal.unlink(missing_ok=True)
        depot.pose_etat(
            graphql=[regle_owner("En revue", []), regle_pose_status()],
            issues=chantier("closed", "closed", coche=coche),
        )
        acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), "completed")
        assert acheve.returncode == 0, acheve.stdout + acheve.stderr
        assert len(fermeture_du_parent(depot)) == 1, f"coche « {coche} »"


def test_un_lot_dont_l_etat_ne_revient_pas_est_compte_ouvert(depot: Depot) -> None:
    """La seule erreur d'ici qui ne se rattrape pas est de fermer sur une donnée manquante.

    Un lot supprimé, une réponse partielle : la requête d'états ne rend rien pour cet alias. Le
    compter « fermé » viderait la garde de son sens exactement quand la donnée manque — alors que
    le compter « ouvert » ne coûte qu'une fermeture différée, que le lot suivant redéclenchera.
    """
    issues = chantier("closed", "closed")
    del issues[str(PREMIER_LOT)]  # le premier lot n'existe plus
    depot.pose_etat(graphql=[regle_owner("En revue", []), regle_pose_status()], issues=issues)

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not fermeture_du_parent(depot), "un état manquant ne vaut pas « soldé »"


def test_la_fermeture_du_parent_ne_boucle_pas(depot: Depot) -> None:
    """La récursion est bornée par la DONNÉE et non par un compteur — c'est ce qui la rend sûre.

    Fermer le parent redéclenche `issues: closed`, donc rejoue ce script SUR LE PARENT. Ce second
    passage fait deux choses puis s'arrête : il pose « Terminé » (question 1) et ne trouve aucun
    parent au parent (question 2). Il n'y a pas de troisième passage à empêcher — un ticket sans
    marqueur `Sous-ticket de #` est un point fixe.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "closed"),
    )

    # Le tour n°2 : l'événement que la fermeture du tour n°1 vient de produire.
    acheve = depot.ticket_ferme(PARENT_SUIVI, "completed")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert option_posee(depot) == "Terminé", "le parent reçoit son état comme n'importe quel ticket"
    assert not ecritures(depot), "et ne ferme rien de plus : il n'a pas de parent"
    assert "n'est pas un lot" in acheve.stdout


def test_lots_ouverts_repond_par_son_code_de_retour(depot: Depot) -> None:
    """Les trois codes du verbe de mesure, éprouvés un par un.

    Le 3 existe pour que « il reste des lots » — une RÉPONSE — ne se confonde pas avec le 1 d'une
    lecture impossible. `ferme-parent` en fait deux choses différentes : le premier s'abstient sans
    bruit, le second laisse le run rouge.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues={
            **chantier("closed", "open"),
            "700": corps_ticket("Ticket ordinaire", "type::infra", "Pas de checklist."),
        },
    )

    reste = depot.lib("lots-ouverts", PARENT_SUIVI)
    assert reste.returncode == 3
    assert f"{PREMIER_LOT + 1}\t" in reste.stdout

    pas_un_parent = depot.lib("lots-ouverts", "700")
    assert pas_un_parent.returncode == 1
    assert "pas un ticket parent" in pas_un_parent.stderr

    depot.pose_etat(issues=chantier("closed", "closed"))
    solde = depot.lib("lots-ouverts", PARENT_SUIVI)
    assert solde.returncode == 0
    assert solde.stdout.strip() == "", "un parent soldé ne liste rien"


def test_un_echec_de_fermeture_laisse_le_run_rouge_et_nomme_le_rattrapage(depot: Depot) -> None:
    """« Best-effort » ne veut pas dire « vert quoi qu'il arrive » — même règle que pour la pose.

    Un jeton périmé, un ticket verrouillé : la fermeture échoue et le script propage, ce qui laisse
    le run rouge dans l'onglet Actions. Le parent reste ouvert, ce qui est le bon état de repli — et
    le rattrapage manuel est nommé, un run rouge sans geste à faire n'apprenant rien à personne.
    """
    depot.pose_etat(
        graphql=[regle_owner("En revue", []), regle_pose_status()],
        issues=chantier("closed", "closed"),
        ecriture_en_echec=True,
    )

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), "completed")
    assert acheve.returncode == 1
    assert "ferme-parent" in acheve.stderr, "le rattrapage manuel est nommé"


def test_les_deux_questions_sont_independantes(depot: Depot) -> None:
    """Une pose en échec n'empêche pas la fermeture du parent d'être tentée, et réciproquement.

    Elles ne partagent que le déclencheur. Les enchaîner ferait qu'un projet injoignable — la panne
    la plus banale des deux — emporterait la fermeture du parent avec lui, sans que rien ne dise que
    la seconde question n'a pas même été posée.
    """
    # Aucune règle pour la mutation : la pose échouera, comme dans le test de #377 qui la garde.
    depot.pose_etat(graphql=[regle_owner("En revue", [])], issues=chantier("closed", "closed"))

    acheve = depot.ticket_ferme(str(PREMIER_LOT + 1), "completed")
    assert acheve.returncode == 1, "la pose a échoué, le run est rouge"
    assert "reconcile-workflow" in acheve.stderr
    assert len(fermeture_du_parent(depot)) == 1, "et le parent a quand même été fermé"


# =================================================================================================
# #517 — L'AUTRE BOUT DU MÊME CYCLE : le parent entre en travail avec son premier lot
# =================================================================================================
# La section ci-dessus solde un parent quand son dernier lot tombe ; celle-ci le fait ENTRER en
# travail quand son premier lot démarre. Les deux se relisent ensemble à dessein — ce sont les deux
# seuls moments où l'état d'un parent de suivi est écrit par quelqu'un. Avant #517, personne :
# `/ticket-create` posait « À faire », `/ticket-start` REFUSAIT de démarrer un parent (il ne porte
# ni branche ni code) et `/ticket-ship` ne touchait que le lot, si bien qu'un parent affichait
# « À faire » pendant que ses lots partaient un par un.
#
# CE QUI SE TESTE ICI EST LE FILTRE, et il est une LISTE BLANCHE : on écrit sur « À faire » ou sur
# rien, jamais sur les cinq autres états. Une liste noire des états à protéger laisserait passer
# tout ce qu'elle n'a pas prévu — une option renommée dans l'UI, un état ajouté demain — et
# écraserait un parent « En revue » ou « Abandonné », ce dont on ne revient pas. C'est la leçon de
# la liste blanche `completed` de la section précédente, appliquée à l'autre bout.
#
# ET LE POINT DE GREFFE EST L'AUTRE MOITIÉ DU SUJET : la pose vit dans `lib.sh begin`, la mutation
# groupée de `/ticket-start`, donc dans le point de passage OBLIGÉ de ses DEUX appelants — session
# interactive et session de run. C'est ce que garde le dernier test de la section.

#: Le motif d'un APPEL au verbe (et non d'une mention) — cf. `test_la_pose_vit_dans_le_verbe…`.
_APPEL_DEMARRE_PARENT = re.compile(r"lib\.sh\s+demarre-parent")


def regle_contexte(iid: str, statut: str, dans_projet: bool = True) -> dict:
    """Le contexte d'UN ticket, CIBLÉ par son numéro — `regle_owner` en sachant les distinguer.

    `regle_owner` répond à n'importe quel ticket (son fragment s'arrête à `issue(number:`), ce qui
    suffit partout ailleurs. Ici il en faut deux dans le même test — l'état du lot et celui de son
    parent, différents par construction —, donc le numéro entre dans le fragment.
    """
    return {
        "contient": [f"issue(number:{iid})", "projectItems(first:"],
        "brut": reponse_owner(statut, [], iid, dans_projet),
    }


def regles_demarrage(etat_parent: str, dans_projet: bool = True) -> list[dict]:
    """Les règles d'un `begin` sur le premier lot : son contexte, celui du parent, la mutation.

    Les deux contextes sont disjoints par leur NUMÉRO, pas par leur ordre : c'est ce qui permet de
    décrire un lot « À faire » et son parent dans un tout autre état sans que l'un réponde pour
    l'autre — et un test qui les confondrait serait vert quel que soit le filtre.
    """
    return [
        regle_contexte(str(PREMIER_LOT), "À faire"),
        regle_contexte(PARENT_SUIVI, etat_parent, dans_projet),
        regle_pose_status(),
    ]


def pose_sur(depot: Depot, iid: str) -> list[str]:
    """Les mutations du champ Status qui visent l'ITEM de CE ticket-là (`PVTI_<iid>`, cf. harnais).

    `begin` en écrit DEUX quand le lot a un parent — la sienne, puis celle du parent. Un test qui
    compterait les mutations sans les distinguer passerait au vert dans les deux sens.
    """
    return [
        ligne
        for ligne in depot.appels()
        if "updateProjectV2ItemFieldValue" in ligne and f"PVTI_{iid}" in ligne
    ]


def test_demarrer_un_lot_fait_entrer_son_parent_en_travail(depot: Depot) -> None:
    """Le cas nominal, et la raison d'être du ticket : le premier lot part, le parent suit.

    Deux mutations, deux items, une seule commande — et c'est bien l'item du PARENT qui reçoit
    « En cours », relu par son id d'option comme partout ailleurs dans ce module.
    """
    depot.pose_etat(graphql=regles_demarrage("À faire"), issues=chantier("open", "open"))

    acheve = depot.lib("begin", str(PREMIER_LOT))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr

    posees = pose_sur(depot, PARENT_SUIVI)
    assert len(posees) == 1, f"le parent devait être posé une fois — {mutations(depot)}"
    assert libelle_pose(posees[0]) == "En cours"
    assert len(pose_sur(depot, str(PREMIER_LOT))) == 1, "et le lot garde la sienne"
    assert f"#{PARENT_SUIVI}" in acheve.stdout, "le démarrage dit ce qu'il a posé de plus"


@pytest.mark.parametrize("etat", ["En cours", "En revue", "Terminé", "Abandonné", "Doublon"])
def test_un_parent_deja_engage_n_est_jamais_ecrase(depot: Depot, etat: str) -> None:
    """Les cinq états que la liste blanche laisse dehors, éprouvés UN PAR UN.

    Les vérifier ensemble ne dirait pas lequel garde. Deux d'entre eux ne se rattrapent pas —
    « Abandonné » et « Doublon » n'ont pas de retour — et « En cours » est le cas NOMINAL dès le
    deuxième lot d'un chantier : c'est lui qui rend le coût de la greffe nul sur la durée.
    """
    depot.pose_etat(graphql=regles_demarrage(etat), issues=chantier("open", "open"))

    acheve = depot.lib("begin", str(PREMIER_LOT))
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert not pose_sur(depot, PARENT_SUIVI), f"« {etat} » ne se réécrit pas"
    assert len(pose_sur(depot, str(PREMIER_LOT))) == 1, "le lot, lui, démarre normalement"


def test_un_ticket_sans_parent_ne_coute_qu_une_lecture(depot: Depot) -> None:
    """L'abstention est le cas NOMINAL : la plupart des tickets ne sont pas des lots.

    Ce verbe passe à CHAQUE démarrage. D'où un contrôle sur le NOMBRE DE LECTURES et pas seulement
    sur l'absence d'écriture : lire l'état d'un parent qui n'existe pas serait un aller-retour de
    plus sur tous les tickets du dépôt, pour une question qui n'en concerne qu'une minorité.

    La lecture se compte sur `jalon: title`, l'alias que seule la vue canonique porte : `begin` lit
    aussi le bloc de suivi du ticket, dont la requête frôle la même sélection (« … body } »).
    """
    depot.pose_etat(
        graphql=[regle_contexte("700", "À faire"), regle_pose_status()],
        issues={"700": corps_ticket("Ticket ordinaire", "type::infra", "Aucun parent ici.")},
    )

    acheve = depot.lib("begin", "700")
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(pose_sur(depot, "700")) == 1, "son propre état, et pas un de plus"
    assert "n'est pas un lot" not in acheve.stdout, "l'abstention nominale est MUETTE au démarrage"

    vues = [
        ligne
        for ligne in depot.appels()
        if "issue(number:700)" in ligne and "jalon: title" in ligne
    ]
    assert len(vues) == 1, "une seule lecture du ticket pour répondre « pas de parent »"


def test_une_pose_en_echec_sur_le_parent_ne_fait_pas_echouer_le_demarrage(depot: Depot) -> None:
    """« Best-effort » au même titre que les dates de `st_begin` et que `sync-main` (docs/10 §9.3).

    Le cas décrit est celui qui arrive vraiment : un parent créé depuis l'interface web, donc ABSENT
    du projet — la lecture le rend « sans état », l'écriture le refuse. Le lot, lui, est démarré,
    assigné et daté : refuser un démarrage pour un board en retard d'un état serait le mauvais
    arbitrage. Et le rattrapage est NOMMÉ, un avertissement sans geste à faire n'apprenant rien.
    """
    depot.pose_etat(
        graphql=regles_demarrage("", dans_projet=False), issues=chantier("open", "open")
    )

    acheve = depot.lib("begin", str(PREMIER_LOT))
    assert acheve.returncode == 0, "le lot est démarré : c'est ce qui compte"
    assert len(pose_sur(depot, str(PREMIER_LOT))) == 1
    assert not pose_sur(depot, PARENT_SUIVI)
    assert any("assignees" in ligne for ligne in ecritures(depot)), "le lot est bien assigné"
    assert "demarre-parent" in acheve.stderr, "le rattrapage manuel est nommé"


def test_demarre_parent_repond_par_son_code_de_retour(depot: Depot) -> None:
    """Les codes du verbe appelé seul, et son `--check` qui n'écrit rien.

    Le 3 existe pour la raison qui l'a fait naître dans `lots-ouverts` : « il n'y a rien à faire »
    est une RÉPONSE, pas une panne — et `begin` traite les deux différemment, le premier en silence,
    le second par une ligne de rattrapage sur stderr.
    """
    depot.pose_etat(
        graphql=regles_demarrage("À faire"),
        issues={
            **chantier("open", "open"),
            "700": corps_ticket("Ticket ordinaire", "type::infra", "Pas de parent."),
        },
    )

    simule = depot.lib("demarre-parent", "--check", str(PREMIER_LOT))
    assert simule.returncode == 0, simule.stderr
    assert f"#{PARENT_SUIVI}" in simule.stdout
    assert not mutations(depot), "`--check` ne pose rien"

    pose = depot.lib("demarre-parent", str(PREMIER_LOT))
    assert pose.returncode == 0, pose.stderr
    assert libelle_pose(pose_sur(depot, PARENT_SUIVI)[0]) == "En cours"

    pas_un_lot = depot.lib("demarre-parent", "700")
    assert pas_un_lot.returncode == 3
    assert "n'est pas un lot" in pas_un_lot.stdout

    depot.pose_etat(graphql=regles_demarrage("En revue"))
    deja = depot.lib("demarre-parent", str(PREMIER_LOT))
    assert deja.returncode == 3
    assert "En revue" in deja.stdout, "l'abstention NOMME l'état qui l'a décidée"


def test_le_commutateur_eteint_la_pose_sans_toucher_au_demarrage(depot: Depot) -> None:
    """`MAESTRO_PARENT_EN_COURS=0` — même statut que `MAESTRO_WORKFLOW_POSE` : une décision.

    Ce qu'il éteint est la GREFFE, jamais le démarrage : le lot passe « En cours », est assigné et
    daté comme avant #517.
    """
    depot.pose_etat(graphql=regles_demarrage("À faire"), issues=chantier("open", "open"))

    acheve = depot.lib(
        "begin", str(PREMIER_LOT), reglages={"MAESTRO_PARENT_EN_COURS": "0"}
    )
    assert acheve.returncode == 0, acheve.stdout + acheve.stderr
    assert len(pose_sur(depot, str(PREMIER_LOT))) == 1, "le lot démarre comme avant"
    assert not pose_sur(depot, PARENT_SUIVI)


def test_la_pose_vit_dans_le_verbe_partage_et_dans_aucun_prompt() -> None:
    """Les deux appelants en héritent parce qu'elle est dans `begin`, et pas dans leur prompt.

    `/ticket-start` est joué à l'identique par une session interactive et par une session de run
    (`scripts/orchestrate/run.sh`) : la seule chose que les deux ont en commun est le VERBE. Une
    pose recopiée dans un prompt en ferait deux à tenir d'accord — et un prompt est ce qu'une
    session lit en dernier, donc c'est lui qui l'emporterait.

    Le motif cherche un APPEL (`lib.sh demarre-parent`), jamais une mention : la doc de
    `/ticket-start` a le droit de dire que le parent suit, elle n'a pas à le faire elle-même. Il
    prouve d'abord qu'il trouve un appel, faute de quoi le balayage rendrait un ✓ sur une question
    jamais posée.
    """
    assert _APPEL_DEMARRE_PARENT.search("bash scripts/gitlab/lib.sh demarre-parent 517"), (
        "le motif doit reconnaître un appel avant qu'on balaie avec"
    )

    lib = (RACINE / "scripts/gitlab/lib.sh").read_text(encoding="utf-8")
    corps_begin = lib.split("\ngl_begin() {", 1)[1].split("\n}\n", 1)[0]
    assert "gl_demarre_parent" in corps_begin, "la greffe est dans la mutation groupée"

    fautifs = [
        chemin.relative_to(RACINE).as_posix()
        for chemin in (RACINE / ".claude").rglob("*.md")
        if _APPEL_DEMARRE_PARENT.search(chemin.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not fautifs, f"aucun prompt ne repose l'état du parent lui-même : {fautifs}"
