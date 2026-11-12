"""Les routes de l'outillage et de l'équipe d'un projet.

L'outillage : son analyse, son report, sa génération, le questionnaire et sa recommandation.
L'équipe : sa proposition, sa correction et sa création."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from maestro.controltower.equipe import EquipeRefusee
from maestro.controltower.generation_agent import GenerationIndisponible
from maestro.controltower.projets import ProjetInconnu
from maestro.controltower.routes.communs import (
    _refus_projet,
    au_compte_du_projet,
    creer_equipe_du_projet,
)
from maestro.controltower.routes.contexte import Ctx
from maestro.controltower.routes.requetes import (
    CorrectionEquipeRequete,
    EquipeValideeRequete,
    GenerationOutillageRequete,
    PropositionEquipeRequete,
    QuestionnaireOutillageRequete,
)
from maestro.outillage.questionnaire import ComprehensionIllisible
from maestro.projets import ApplicationRefusee
from maestro.sandbox import EspaceProjetIndisponible

router = APIRouter()


@router.get("/api/projets/{id_projet}/outillage/analyse")
async def analyser_outillage(ctx: Ctx, id_projet: str) -> dict[str, Any]:
    """L'analyse d'un projet existant et l'outillage qu'elle recommande (#1030, docs/38).

    Le premier geste du chantier « outillage universel » (#1020) : avant de
    proposer quoi que ce soit à écrire dans le dossier de quelqu'un, on
    regarde ce qu'il y a. La racine est lue **en lecture seule** et le code
    du projet n'est **jamais exécuté** — le VCS lui-même est lu dans
    `.git/config` plutôt qu'obtenu d'un `git remote`.

    La réponse porte ses propres **bornes** (nombre de fichiers, profondeur,
    octets par fichier, dossiers ignorés) et ce que le parcours a vu : une
    analyse tronquée le dit, là où un plafond resté dans le code ferait lire
    un projet plus petit qu'il n'est. Le périmètre déclaré du projet
    s'applique en plus, donc ni `.env` ni `**/secrets/**` ne sont ouverts.

    `constats` porte les langages, les gestionnaires (avec leur verrou), les
    commandes de construction/test/lint — chacune avec le fichier et
    l'extrait qui la justifient, et son `origine` : `declaree` par le projet
    ou `convention` de l'outil —, la CI, la forge, les conventions déjà
    écrites et le **dossier de scripts constaté**. `recommandation` porte
    chaque skill, script ou fichier d'instructions avec sa raison et son
    `etat` : ce que le projet porte déjà sort en `deja-present`, avec son
    chemin, jamais dupliqué. `ecartes` nomme ce qui n'est pas recommandé et
    pourquoi — les commandes le sont par décision (docs/38 §3.5).

    **Les tables ne sont que des indices** (#1158) : le modèle lit ensuite le
    projet — deux verbes, lister et lire, servis dans le périmètre, sans lien
    suivi ni rien d'exécuté — et ce qu'il en tire complète les constats, chacun
    avec le fichier qu'il a lu. `lecture` dit ce qu'il a ouvert, ce qui lui a
    été refusé, ce qui a été coupé et ce qui a été écarté ; un modèle qui ne
    répond pas laisse l'analyse aux tables, `lecture.etat` le dit.

    **Rien n'est écrit** : la génération est #1033. 404 si le projet est
    inconnu, 422 motivé si sa fiche est illisible ou si sa racine n'est plus
    un dossier lisible — jamais un 500.

    Le parcours est joué **hors de la boucle d'événements** : parcourir un
    projet réel prend des secondes, et une route qui bloquerait la boucle
    figerait les flux SSE des autres écrans.
    """
    outillage = ctx.outillage
    try:
        async with au_compte_du_projet(ctx, id_projet, "analyse de l'outillage"):
            return await outillage.analyser(id_projet)
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/outillage/report")
async def reporter_outillage_du_projet(ctx: Ctx, id_projet: str) -> dict[str, Any]:
    """Enregistre le « plus tard » de l'étape d'outillage (#1034, docs/37 §4.6).

    L'étape d'outillage est **première et proposée d'office, mais
    reportable** : importer un projet pour seulement le regarder ne doit pas
    imposer une génération. Cette route est l'autre issue de l'étape, celle
    qui ne produit rien — la seule chose qu'elle écrit est la date de la
    décision, dans la fiche du projet.

    **Sans corps**, comme `versionner`, et pour la même raison : il n'y a
    rien à déclarer, seulement un verbe à appeler. Idempotente — la première
    date gagne, un second appel rend la fiche telle quelle.

    La fiche rendue porte `outillage.a_faire` : reporté **et** pas encore
    généré. C'est ce que la carte du projet affiche, et c'est pourquoi
    générer suffit à faire taire le rappel sans qu'aucun code de génération
    (#1033) connaisse ce champ.

    404 si le projet est inconnu, 422 motivé si sa fiche est illisible.
    """
    projets = ctx.projets
    try:
        return projets.reporter_outillage(id_projet)
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/outillage/generation")
async def generer_outillage_du_projet(
    ctx: Ctx,
    id_projet: str, requete: GenerationOutillageRequete | None = None
) -> dict[str, Any]:
    """Écrit dans le projet l'outillage que son analyse recommande (#1033, docs/38).

    Le second geste du chantier, et celui qui touche au dossier de
    quelqu'un : `AGENTS.md`, un pont d'une ligne par client du poste qui ne
    le lit pas (#1295), les skills dans
    `.agents/skills/` et le manifeste `.maestro/outillage/manifeste.json`,
    qui dit ce qui vient de Maestro.

    **Rien n'est jamais écrasé en silence** (docs/38 §4.2). Un fichier que le
    projet portait et que Maestro n'a pas écrit n'est pas touché ; un fichier
    qu'il avait écrit et que quelqu'un a modifié depuis n'est pas réécrit — la
    version neuve est déposée dans `.maestro/outillage/refuses/`, et le
    rapport la nomme. Un `AGENTS.md` déjà présent reçoit un **bloc délimité**
    plutôt qu'un remplacement, et c'est ce bloc-là, et lui seul, que Maestro
    possède ensuite. Régénérer ne duplique rien : ce que le manifeste déclare
    déjà à jour n'est pas réécrit.

    **Le régime d'écriture est celui du projet** (docs/24 §2.4), et la
    réponse le dit (`regime`) : un projet **non versionné** est servi *en
    place* et c'est fait à la réponse ; un projet **versionné** reçoit son
    outillage sur une branche `maestro/outillage-…` que la **validation
    humaine** fusionne (`application`), diff sous les yeux. ⚠ Dans ce second
    cas la requête **attend la décision**, sans time-out : c'est le contrat du
    validateur (#9), et l'attente se voit sur l'écran des validations. Un
    refus laisse la branche intacte — le travail reste consultable et se
    récupère d'un `git merge`.

    **Le corps est facultatif** (#1034). `retenus` porte les chemins que
    l'étape d'outillage a gardés cochés. Absent — un appel qui ne vient pas
    d'un écran —, tout ce qui est recommandé est écrit, ce qui est le
    comportement d'origine. Ce qui n'y est pas n'est pas écrit, et **quitte
    le manifeste sans quitter le disque** : c'est déjà la règle de docs/38
    §4.2 pour un fichier que l'analyse ne recommande plus. Un chemin qui ne
    désigne aucune entrée revient dans `retenus_inconnus` (#1100) : jamais
    perdu en silence.

    `choix` porte les réponses d'un projet **neuf** (#1100) : l'outillage se
    dérive alors d'elles, et non de l'analyse d'une racine encore vide — la
    même dérivation que `…/outillage/recommandation`, donc ce que l'écran a
    montré est ce qui s'écrit. `source` dit d'où il sort (`analyse` ou
    `choix`), et le manifeste le garde.

    404 si le projet est inconnu, 422 motivé si sa fiche est illisible, si sa
    racine n'est plus un dossier lisible, si le worktree ne se monte pas ou si
    la fusion est refusée (racine occupée, conflit) — jamais un 500.
    """
    outillage = ctx.outillage
    try:
        async with au_compte_du_projet(ctx, id_projet, "génération de l'outillage"):
            return await outillage.generer(
                id_projet,
                retenus=None if requete is None else requete.retenus,
                choix=() if requete is None else requete.choix_acquis(),
            )
    except (
        ValueError,
        ProjetInconnu,
        ApplicationRefusee,
        EspaceProjetIndisponible,
    ) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/outillage/questionnaire")
async def question_outillage(
    ctx: Ctx,
    id_projet: str, requete: QuestionnaireOutillageRequete
) -> dict[str, Any]:
    """La prochaine question qui décide de l'outillage d'un projet neuf (#1031).

    La voie **sans état** du questionnaire, à côté de celle du fil : le client dit
    ce qu'il a, l'API dit ce qui en découle. C'est ce qui permet au fil — qui
    tient ses réponses dans ses messages — et au parcours de création (#1034) —
    qui les tiendra à l'écran — de servir du même questionnaire sans partager de
    session.

    Rangée sous le projet, comme l'analyse : les deux décident du même outillage,
    et un projet déclaré est la seule chose qu'elles prennent du dehors. `404` sur
    un projet inconnu, `422` sur un identifiant mal formé — les mêmes refus que
    `GET …/outillage/analyse`, traduits par la même fonction.

    Depuis #1147 la suite se **comprend** (un appel au modèle dès qu'une réponse a
    été donnée) : un fournisseur injoignable ou une compréhension illisible est un
    `502`, jamais une question inventée à sa place.
    """
    outillage = ctx.outillage
    try:
        async with au_compte_du_projet(ctx, id_projet, "question d'outillage"):
            return await outillage.question(id_projet, requete.choix_acquis())
    except ComprehensionIllisible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc
    except Exception as exc:  # noqa: BLE001 — le fournisseur, pas la requête
        raise HTTPException(
            status_code=502,
            detail=f"le projet n'a pas pu être compris : {exc}",
        ) from exc



@router.post("/api/projets/{id_projet}/outillage/recommandation")
async def recommandation_outillage(
    ctx: Ctx,
    id_projet: str, requete: QuestionnaireOutillageRequete
) -> dict[str, Any]:
    """L'outillage que ces réponses recommandent — **la forme de l'analyse** (#1031).

    Le second critère du ticket : les réponses produisent la *même* recommandation
    structurée que l'analyse d'un projet existant (#1030), servie par l'API. Elles
    la produisent par la **même fonction** — les réponses deviennent des
    `Constats`, et `recommander` fait le reste —, si bien qu'il n'y a pas deux
    formes à tenir d'accord mais une seule et deux façons de la remplir.

    Rendue à **tout moment**, questionnaire fini ou non. Rien n'est écrit : c'est
    une proposition, et la génération est le lot 5 (#1033).
    """
    outillage = ctx.outillage
    try:
        async with au_compte_du_projet(ctx, id_projet, "recommandation d'outillage"):
            return await outillage.recommandation(id_projet, requete.choix_acquis())
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/equipe/proposition")
async def proposition_equipe(
    ctx: Ctx,
    id_projet: str, requete: PropositionEquipeRequete | None = None
) -> dict[str, Any]:
    """L'équipe que ce projet appelle — **proposée**, jamais créée (#1039, docs/37).

    Le troisième geste du chantier « équipe sur mesure » (#1021) : un projet
    naît sans agent, et c'est son analyse qui lui propose son équipe. Chaque
    rôle sort avec **sa raison** et l'endroit du projet qui la prouve, son
    **nombre d'instances** et pourquoi ce nombre, son **playbook**, les
    **skills** de l'outillage qu'il branche et ses **autorisations
    proposées** — chacune avec sa raison, le cran `auto` compris (#716,
    docs/37 §4.3). `politique` porte les mêmes autorisations sous la forme
    que #1040 persistera, pour que ce qu'on valide soit exactement ce qu'on
    a lu.

    **Une route, deux provenances.** Corps vide (ou `choix` vide) : le projet
    est **analysé** (#1030) — c'est le cas d'un projet existant. Avec des
    réponses au questionnaire d'outillage (#1031) : l'équipe se dérive de
    ces **réponses**, sans qu'aucun fichier soit ouvert — c'est le cas d'un
    projet neuf. Les deux passent par la même dérivation, et `source` dit
    laquelle a servi. Un corps vide sur un projet **encore vide** (aucun
    fichier que son manifeste ne déclare) reprend les réponses que ce
    manifeste garde (#1350, `choix_d_un_projet_decrit`) : c'est ainsi que le
    fil, qui n'en passe pas, compose l'équipe sur ce que le projet est.

    **Rien n'est créé**, et la réponse le dit (`cree`, `validation`) : aucun
    agent, aucun playbook, aucune politique, aucune capacité n'est écrit. La
    validation et la création sont le lot 4 (#1040) ; d'ici là l'équipe se
    relit, se modifie et se redemande sans conséquence.

    `ecartes` nomme les rôles **non** proposés avec leur raison —
    l'orchestrateur en fait partie *par décision* : c'est Maestro, et c'est
    lui qui recrute (docs/37 §4.2).

    Les playbooks sont écrits par la mécanique de #257. Un rôle dont la
    rédaction échoue garde le playbook de son **gabarit** et le dit
    (`playbook_origine`) : une équipe entière perdue parce qu'un quota est
    épuisé serait une bien pire réponse.

    **Une troisième demande depuis #1227** : `renfort` fait proposer **un seul
    rôle**, celui qu'un plan appelle et que l'équipe n'a pas, justifié par le
    plan et non par un constat. C'est ce que la carte du fil demande quand un
    run l'attend — sans lui, le rôle manquant serait `ecarte` par l'analyse,
    aucun constat d'un projet en Python ne justifiant un designer. Le gabarit
    est revalidé contre le catalogue (422 sinon).

    404 si le projet est inconnu, 422 motivé si sa fiche est illisible ou si
    sa racine n'est plus un dossier lisible — jamais un 500.
    """
    equipe = ctx.equipe
    choix = requete.choix_acquis() if requete is not None else []
    renfort = requete.renfort if requete is not None else None
    try:
        async with au_compte_du_projet(ctx, id_projet, "équipe proposée"):
            return await equipe.proposer(
                id_projet,
                choix,
                renfort=renfort.role_manquant() if renfort is not None else None,
                raison=renfort.raison if renfort is not None else "",
            )
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/equipe/correction")
async def correction_equipe(
    ctx: Ctx,
    id_projet: str, requete: CorrectionEquipeRequete
) -> dict[str, Any]:
    """Ce que la personne demande de changer à l'équipe proposée, **compris** (#1159).

    « Ajoute quelqu'un pour la sécurité », « retire le designer », « deux
    développeurs » : la demande, en langage naturel, et l'équipe **telle que
    l'étape d'équipe la montre** (retraits et instances compris). Le modèle rend
    les rôles à **ajouter** — vérifiés comme une composition, playbooks écrits
    pour ce projet —, ceux à **retirer** ou à **remettre**, les **instances** à
    changer, et une phrase qui répond à la personne (`reponse`). Une demande qui
    fixe une convention du projet rend aussi les **playbooks** des rôles montrés
    qu'elle contredisait, réécrits pour la suivre (#1521) : le modèle a lu ceux que
    la carte montre, et la convention gagne tous les rôles, pas le seul nommé.

    **Rien n'est créé** (`cree: False`) : l'écran applique la correction à ce
    qu'il montre, et la création reste la validation (#1040). Retraits, remises
    et instances ne nomment que des rôles de l'équipe montrée.

    422 si la demande est vide ou plus longue qu'une phrase — refusée avant
    tout appel —, 404/422 motivés sur le projet, **502** si le modèle ne répond
    pas ou répond hors contrat : une correction n'a pas de repli, aucune règle
    ne comprend une phrase. L'équipe montrée reste intacte, et l'appel se
    rejoue sans conséquence.
    """
    equipe = ctx.equipe
    try:
        async with au_compte_du_projet(ctx, id_projet, "équipe corrigée"):
            return await equipe.corriger(
                id_projet, requete.demande, requete.membres(), requete.choix_acquis()
            )
    except GenerationIndisponible as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except (ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc



@router.post("/api/projets/{id_projet}/equipe", status_code=201)
async def creer_equipe(
    ctx: Ctx,
    id_projet: str, requete: EquipeValideeRequete
) -> dict[str, Any]:
    """Crée dans le projet l'équipe que l'utilisateur a **validée** (#1040, docs/37).

    Le quatrième geste du chantier (#1021), et le premier qui écrit quelque
    chose : `…/equipe/proposition` ne crée rien, celui-ci crée tout. Pour
    chaque rôle gardé, trois écritures **dans le projet** (#1038) — sa fiche
    et son playbook, sa politique d'autorisations, sa capacité (le nombre
    d'instances validé). L'équipe se revoit et se modifie ensuite depuis les
    écrans d'agents du projet (`/api/catalogue?projet=…`).

    **Ce qui est créé est ce qui a été montré.** Le corps rapporte la
    proposition telle que l'API l'a servie — playbook compris, `politique`
    comprise —, plutôt qu'un identifiant à re-dériver : rejouer la
    proposition appellerait à nouveau un modèle et rendrait un *autre*
    playbook, c'est-à-dire un agent que personne n'a validé. Et la
    traduction « autorisations proposées → politique » reste unique
    (`RolePropose.politique()`), si bien que le cran `auto` qu'on a lu avec
    sa raison est le cran qui est écrit : *c'est l'utilisateur qui le décide,
    à froid* (#716, docs/37 §4.3).

    **Tout ou rien.** L'équipe entière est vérifiée avant que le premier
    fichier ne soit écrit — nom déjà pris dans ce projet ou réservé,
    doublon dans la liste, instances hors bornes, fiche ou politique que les
    dépôts refuseraient. Un seul blocage rend un 422 `equipe-refusee` qui
    les nomme **tous**, et rien n'a été créé : sans transaction de système de
    fichiers, une demi-équipe serait pire qu'un refus.

    404 si le projet est inconnu, 422 motivé s'il est illisible ou si
    l'équipe est refusée — jamais un 500.
    """
    if not requete.roles:
        raise HTTPException(
            status_code=422,
            detail={
                "motif": "equipe-vide",
                "message": (
                    "aucun rôle validé : ne pas recruter se dit en ne validant "
                    "pas l'équipe, pas en validant une équipe vide."
                ),
            },
        )
    try:
        # Les agents créés entrent aussi dans la **projection**, comme ceux
        # du `POST /api/catalogue` : même geste, même conséquence — ils y
        # deviennent cibles de réassignation manuelle
        # (`POST /api/taches/{id}/reassigner`, qui n'a pas de projet à
        # connaître) et peuplent le parc transverse. C'est
        # `creer_equipe_du_projet` qui tient les deux, parce que le fil de
        # l'orchestration crée une équipe par la même voie (#1146).
        # ⚠ La vue de l'équipe, elle, n'en dépend plus (#1101) :
        # `GET /api/agents?projet=` la dérive du catalogue du projet et de ses
        # capacités. C'est ce qui la rend juste au redémarrage, quand la
        # projection repart vide.
        return await creer_equipe_du_projet(ctx, 
            id_projet, requete.roles_valides(), requete.proposition_id
        )
    except (EquipeRefusee, ValueError, ProjetInconnu) as exc:
        raise _refus_projet(exc) from exc
