"""Le banc : il déroule les scénarios de référence et rend un verdict par scénario (#1148).

    .venv/Scripts/python.exe -m maestro.scenarios [--scenario S1[,S3]] [--delai <s>]
                                                  [--nettoyer | --sauver-etat]
                                                  [--temoin <fichier>] [--liste]

Le retex du 2026-09-11 (#854) est la seule vérification qui ait trouvé de vrais
défauts du produit. Il était manuel, et il n'a été joué **qu'une fois** : dix jours
plus tard le même geste échouait sur un projet antérieur à #1042 (#1146) sans
qu'aucun bilan de jalon ne l'ait vu. Ce que les bilans vérifiaient, c'est ce que
chaque ticket **dit**, jamais ce qu'un utilisateur **fait**. Ce module est ce retex
rendu rejouable.

## Trois choses qu'il ne faut pas défaire

**La vraie stack, le vrai modèle, la vraie porte.** Le banc parle à l'API qui
tourne, en HTTP, par le fil de l'orchestrateur. Il n'importe pas l'application et
ne monte aucun double : c'est tout l'intérêt — une pile *ressemblante* est
exactement ce qui n'a rien vu pendant dix jours. Corollaire assumé : **il n'est pas
en CI**, il coûte du vrai modèle (~10 $ pour le run du retex), et il se joue au
bouclage d'un jalon (#1152) ou à la demande.

**Un rouge est un résultat, pas une panne du banc.** Ce banc peut être livré avant
que tous les scénarios soient verts : c'est son rôle de montrer les rouges. S1
passe au vert avec #1149, S4 avec #1157, S5 avec #1224, S6 avec #1260 ; S9 attend
#1343, et S12 naît rouge : il est la preuve de bouclage du chantier de #1395. Un
code de sortie non nul
n'est donc pas un défaut d'outillage — c'est la mesure.

**Un rouge non déterministe se rejoue une fois, et le rapport le dit.** S2 demande
au modèle d'écrire du code qui s'exécute, S4 de reconnaître une cause dans une
phrase, S5 de dire comment essayer un livrable, S6 de nommer dans son plan le
métier qui manque, S9 et S10 de comprendre un projet qu'aucune liste ne prévoyait,
S11 de dégager de son plan le travail indépendant : tous échouent parfois sans
que le produit ait changé (docs/40 §5). Le
second passage fait foi, et `rejoue` reste écrit au rapport — un rejeu tu ferait
lire deux runs comme un seul. La tentative rouge est **oubliée** avant le rejeu —
sa déclaration, jamais son dossier —, pour qu'un projet né dans la conversation
ne se retrouve pas lui-même au second essai.

## L'état qu'un passage laisse (#1164)

`--sauver-etat` range l'état de la stack à la fin du passage — son journal et ses
dépôts, sous `<atelier>/_etat/` —, pour que l'écran peuplé de la relecture et des
captures soit **ce passage-là**, rouvert sans rien rejouer
(`maestro.scenarios.etat`). Il ne vaut que sur **le banc de la copie**, que
`start.sh --etat-banc --rejouer` vide, sert, puis fait jouer : le banc le vérifie
auprès de l'API (`/api/sante`) **avant** de jouer, et refuse sinon — sauver les
données d'une autre stack mêlerait au passage ce que la copie ou le poste
contiennent. Sans l'option, rien ne change : un passage de bouclage (#1152) ne
sauve rien.

## Les passages qu'il garde (#1457)

Un passage qui réserve son atelier fait d'abord la **rétention** du poste : les
`PASSAGES_GARDES` derniers passages restent, et celui que `start.sh --etat-banc`
rouvre, quel que soit son rang ; les autres partent avec les worktrees que leurs
tâches ont laissés sous la racine jetable (`maestro.scenarios.projets.retenir`).
Ce qui est retiré se dit, avant (le premier retrait d'un poste peut être long) et
après. `MAESTRO_SCENARIOS_PASSAGES_GARDES` règle le nombre, `0` éteint la rétention.

## Codes de sortie

`0` les scénarios joués sont **tous** verts · `1` au moins un rouge · `2` usage ·
`3` l'API ne répond pas (rien n'a été joué) · `4` le passage est joué mais son état
n'a pas pu être sauvé (`--sauver-etat` ; le verdict est au rapport). Le `3` est un
refus et non un rouge : distinguer « le produit s'est trompé » de « le produit
n'était pas allumé » est la première chose qu'un bouclage a besoin de savoir.

⚠ **Un processus tué sort en `1`** sous Windows (`TerminateProcess`), le code d'un
rouge : le code seul ne distingue pas « au moins un rouge » de « tué en chemin ».
`--temoin <fichier>` y répond — le banc y écrit le dossier de son rapport en
**dernier** geste, une fois allé au bout (#1365). Pas de témoin : le passage a été
interrompu, et n'a laissé ni verdict ni état. C'est ce que lit
`start.sh --etat-banc --rejouer`.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

from maestro.controltower.donnees import Donnees, donnees_du_banc
from maestro.scenarios import rapport as rapport_module
from maestro.scenarios.api import (
    DELAI_RUN_S,
    ClientAPI,
    ErreurAPI,
    TransportHTTP,
    base_locale,
)
from maestro.scenarios.juge import Juge, JugeModele
from maestro.scenarios.modele import (
    Etape,
    Journal,
    Rapport,
    Resultat,
    empeche,
)
from maestro.scenarios.modele import (
    horodatage as horodatage_courant,
)
from maestro.scenarios.projets import Atelier, retenir
from maestro.scenarios.scenarios import (
    SCENARIOS,
    Contexte,
    Scenario,
    nettoyer,
    par_identifiant,
)

if TYPE_CHECKING:
    from maestro.scenarios.etat import ClientRedis

#: Le nom sous lequel ce banc s'invoque (`python -m …`) — **dérivé** du paquet et
#: jamais écrit, comme dans `maestro.controltower.purge` : un nom recopié survit à
#: un renommage de module et envoie l'utilisateur sur une commande qui n'existe plus.
MODULE = __package__ or "maestro.scenarios"

#: Le geste qui allume la stack, nommé dans le refus — jamais deviné par l'appelant.
GESTE_PREALABLE = "bash scripts/controltower/start.sh"

CODE_VERT = 0
CODE_ROUGE = 1
CODE_USAGE = 2
CODE_API_MUETTE = 3
CODE_ETAT_NON_SAUVE = 4

_USAGE = (
    f"Usage : python -m {MODULE} [--scenario S1[,S3]] [--delai <secondes>] "
    "[--nettoyer | --sauver-etat] [--temoin <fichier>] [--liste]"
)


def jouer(
    scenarios: Sequence[Scenario],
    fabrique: Callable[[], Contexte],
    *,
    horodatage: str,
    horloge: Callable[[], float] = time.monotonic,
    trace: Callable[[str], None] = lambda _ligne: None,
) -> Rapport:
    """Déroule les scénarios et rend le rapport du passage.

    Un **contexte neuf par tentative** (`fabrique`) : un scénario rejoué déclare un
    autre projet jetable et ouvre une autre conversation, sans quoi il repartirait
    du dossier que la tentative précédente a laissé et de la demande qu'elle a déjà
    tranchée — il ne mesurerait plus le scénario mais sa propre trace.

    Une exception d'API ne fait jamais tomber le passage : le scénario devient un
    **empêchement** et les suivants sont joués. Un passage qui s'arrête au premier
    incident ne dirait rien des trois autres, et c'est précisément ce qu'on veut
    savoir au moment de boucler un jalon.
    """
    resultats: list[Resultat] = []
    for scenario in scenarios:
        trace(f"— {scenario.identifiant} · {scenario.titre}")
        resultat, tentative = _une_tentative(scenario, fabrique, horloge=horloge)
        if not resultat.vert and scenario.rejouable:
            trace(f"  rouge non déterministe : {scenario.identifiant} rejoué une fois")
            # La tentative rouge est **oubliée** avant le rejeu — sa déclaration, pas
            # son dossier, qui reste une pièce. Sans cela, un projet né dans la
            # conversation se retrouvait lui-même : rejoué au passage
            # `20260927-043104`, S9 s'est entendu répondre « ce projet existe déjà
            # sur ce poste », et le rejeu mesurait sa propre trace.
            nettoyer(tentative)
            resultat, _ = _une_tentative(scenario, fabrique, horloge=horloge, rejoue=True)
        trace(f"  {'vert' if resultat.vert else 'rouge'} — {resultat.motif}")
        resultats.append(resultat)
    return Rapport(horodatage=horodatage, resultats=tuple(resultats))


def _une_tentative(
    scenario: Scenario,
    fabrique: Callable[[], Contexte],
    *,
    horloge: Callable[[], float],
    rejoue: bool = False,
) -> tuple[Resultat, Contexte]:
    """Joue un scénario une fois : son résultat — même si l'API a refusé —, et son contexte."""
    ctx = fabrique()
    debut = horloge()
    try:
        issue = scenario.jouer(ctx)
    except ErreurAPI as echec:
        issue = empeche(f"l'API a refusé : {echec}")
    except OSError as echec:  # disque, projet jetable devenu illisible
        issue = empeche(f"le banc n'a pas pu jouer : {echec}")
    duree = horloge() - debut
    resultat = Resultat(
        identifiant=scenario.identifiant,
        titre=scenario.titre,
        verdict=issue.verdict,
        motif=issue.motif,
        duree_s=duree,
        run_id=issue.run_id,
        projet_id=ctx.projet_id,
        racine=str(ctx.racine or ""),
        cout_usd=issue.cout_usd,
        rejoue=rejoue,
        empechement=issue.empechement,
        etapes=tuple(ctx.journal.etapes),
        arbitrages=tuple(ctx.arbitrages),
    )
    return resultat, ctx


def main(
    argv: Sequence[str] | None = None,
    *,
    client: ClientAPI | None = None,
    juge: Juge | None = None,
    atelier: Atelier | None = None,
    racine_rapports: Path | None = None,
    horloge: Callable[[], float] = time.monotonic,
    dormir: Callable[[float], None] = time.sleep,
    lancer_application: Callable[[Path, str], tuple[int, str]] | None = None,
    client_redis: ClientRedis | None = None,
    donnees_banc: Donnees | None = None,
    sortie: TextIO | None = None,
    erreur: TextIO | None = None,
) -> int:
    """Point d'entrée : voir l'en-tête du module pour les options et les codes.

    `client`, `juge`, `atelier`, `racine_rapports`, `horloge`, `dormir`,
    `lancer_application`, `client_redis` et `donnees_banc` sont injectables **pour
    les tests** — une fausse API, un
    faux fournisseur, des dossiers jetables, aucune attente réelle. C'est la même
    couture que `maestro.controltower.purge`, et elle a la même raison d'être : le
    déroulé du banc doit être éprouvable sans réseau ni modèle.
    """
    sortie = sortie or sys.stdout
    erreur = erreur or sys.stderr
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        options = _options(args)
    except ValueError as refus:
        print(f"{_USAGE}\n  {refus}", file=erreur)
        return CODE_USAGE

    if options.liste:
        for scenario in SCENARIOS:
            rejeu = " (rejoué une fois si rouge)" if scenario.rejouable else ""
            print(f"{scenario.identifiant}  {scenario.titre}{rejeu}", file=sortie)
        return CODE_VERT

    try:
        choisis = par_identifiant(options.scenarios) if options.scenarios else SCENARIOS
    except ValueError as refus:
        print(f"{_USAGE}\n  {refus}", file=erreur)
        return CODE_USAGE
    if options.nettoyer and options.sauver_etat:
        print(
            f"{_USAGE}\n  --nettoyer efface l'atelier, donc l'état que --sauver-etat y range : "
            "l'un ou l'autre.",
            file=erreur,
        )
        return CODE_USAGE

    if client is None:
        # `--delai` borne aussi les gestes que le modèle rédige (#1232) : une seule
        # attente accordée au modèle, qu'il fasse un run ou qu'il rédige un playbook.
        client = ClientAPI(TransportHTTP(base_locale()), delai_modele_s=options.delai_s)
    if not client.sante():
        print(
            f"Banc refusé : l'API de la Control Tower ne répond pas sur {base_locale()} — "
            "les scénarios passent par la porte d'entrée réelle, il faut donc qu'elle "
            f"soit allumée.\n  Démarrer d'abord : {GESTE_PREALABLE}",
            file=erreur,
        )
        return CODE_API_MUETTE

    # Import paresseux : `python -m maestro.scenarios.etat` charge ce paquet, donc ce
    # module ; un import au niveau du module chargerait `etat` avant qu'il ne s'exécute.
    from maestro.scenarios import etat

    banc = (donnees_banc or donnees_du_banc()) if options.sauver_etat else None
    if banc is not None and client.espace() != banc.espace.nom:
        print(
            f"Banc refusé : --sauver-etat sauve l'état du banc de cette copie (espace "
            f"« {banc.espace.nom} »), mais l'API de {base_locale()} sert l'espace "
            f"« {client.espace() or '?'} ».\n  La démarrer sur le banc : "
            f"{etat.GESTE_REJOUER}",
            file=erreur,
        )
        return CODE_USAGE

    # L'atelier se **réserve** : le dossier des ateliers est celui du poste, et deux
    # copies qui lancent le banc dans la même seconde auraient sinon le même (#1365).
    # Le nom réservé est l'identifiant du passage — rapport, atelier et état.
    if atelier is None:
        atelier = Atelier.reserver(horodatage_courant())
        horodatage = atelier.passage
        _retenir(sortie)
    else:
        horodatage = horodatage_courant()
    juge = juge or JugeModele()
    client_resolu, juge_resolu, atelier_resolu = client, juge, atelier

    contextes: list[Contexte] = []

    def fabrique() -> Contexte:
        ctx = Contexte(
            client=client_resolu,
            atelier=atelier_resolu,
            juge=juge_resolu,
            journal=Journal(echo=lambda etape: print(_en_direct(etape), file=sortie)),
            delai_run_s=options.delai_s,
            horloge=horloge,
            dormir=dormir,
            lancer_application=lancer_application,
        )
        contextes.append(ctx)
        return ctx

    print(
        f"Scénarios de référence — passage {horodatage} · "
        f"{', '.join(s.identifiant for s in choisis)} · "
        f"délai par run {options.delai_s:.0f} s, et par réponse que le modèle rédige · "
        f"atelier {atelier_resolu.racine}",
        file=sortie,
    )
    rapport = jouer(
        choisis,
        fabrique,
        horodatage=horodatage,
        horloge=horloge,
        trace=lambda ligne: print(ligne, file=sortie),
    )

    dossier = rapport_module.ecrire(rapport, racine=racine_rapports)
    print(_synthese(rapport, dossier), file=sortie)

    if banc is not None:
        try:
            instantane = etat.sauver(
                rapport, atelier_resolu.racine, banc, client_redis or etat.client_redis()
            )
        except Exception as exc:  # Redis ou disque : le verdict, lui, est au rapport
            print(f"État du passage NON sauvé : {exc}", file=erreur)
            _temoigner(options.temoin, dossier, erreur)
            return CODE_ETAT_NON_SAUVE
        print(
            f"État du passage sauvé : {instantane.dossier} "
            f"({instantane.evenements} événement(s), {instantane.projets} projet(s)) — "
            f"rouvrable sans rien rejouer : {etat.GESTE_ROUVRIR}",
            file=sortie,
        )

    if options.nettoyer:
        for ctx in contextes:
            nettoyer(ctx)
        retrait = atelier_resolu.retirer()
        worktrees = (
            f", avec {retrait.worktrees} worktree(s) de tâches" if retrait.worktrees else ""
        )
        print(
            f"Nettoyé : déclarations retirées et atelier {atelier_resolu.racine} "
            f"effacé{worktrees}.",
            file=sortie,
        )
    else:
        print(
            f"Projets jetables conservés sous {atelier_resolu.racine} — ce sont les "
            "pièces d'un rouge (`--nettoyer` les retire).",
            file=sortie,
        )
    _temoigner(options.temoin, dossier, erreur)
    return CODE_VERT if rapport.vert else CODE_ROUGE


def _retenir(sortie: TextIO) -> None:
    """Ne garde que les derniers passages du poste, une fois l'atelier du passage réservé (#1457).

    **Après** la réservation : le passage qui commence compte parmi les derniers, et
    il s'est déjà nommé dans son atelier — une autre copie qui ferait sa rétention à
    cet instant le verrait occupé. Le passage que `start.sh --etat-banc` rouvre est
    lu ici (`etat.dernier`), avant tout retrait, et gardé quel que soit son rang.
    Seulement quand le banc réserve son atelier : un appelant qui en fournit un
    (les tests) ne fait pas le ménage du poste.
    """
    from maestro.scenarios import etat  # paresseux, pour la raison donnée dans `main`

    rouvert = etat.dernier()
    retention = retenir(
        rouvert=None if rouvert is None else rouvert.dossier.parent,
        avant=lambda nombre: print(
            f"Ateliers du banc : {nombre} passage(s) au-delà des derniers gardés — "
            "retrait en cours, worktrees de leurs tâches compris…",
            file=sortie,
        ),
    )
    for ligne in retention.lignes():
        print(ligne, file=sortie)


#: Ce qu'une étape occupe à l'écran pendant le passage : une ligne. Le rapport garde
#: le détail entier ; l'écran ne sert qu'à suivre, et à relire un passage tué.
ETAPE_EN_DIRECT_MAX = 240


def _en_direct(etape: Etape) -> str:
    """Une étape du déroulé telle que la sortie la montre pendant le passage (#1365)."""
    ligne = f"{etape.libelle} — {etape.detail}" if etape.detail else etape.libelle
    ligne = " ".join(ligne.split())
    if len(ligne) > ETAPE_EN_DIRECT_MAX:
        ligne = ligne[:ETAPE_EN_DIRECT_MAX].rstrip() + "…"
    return f"    · {ligne}"


def _temoigner(temoin: Path | None, dossier: Path, erreur: TextIO) -> None:
    """Écrit dans `temoin` le dossier du rapport — le dernier geste d'un passage (#1365).

    C'est ce que lit le lanceur (`start.sh --etat-banc --rejouer`) pour savoir
    que le passage est **allé au bout**. Le code de sortie ne le dit pas : un
    processus tué sous Windows sort en `1`, le code d'un rouge — mesuré le
    2026-09-27, deux passages tués à 12:41:25 que le lanceur a annoncés
    « état sauvé ». Écrit en dernier, après l'état et le ménage, pour qu'un
    passage tué en chemin n'en laisse aucun. Un témoin qui ne s'écrit pas se dit
    et ne change pas le verdict : le lanceur conclura au passage interrompu, le
    sens sûr de l'erreur.
    """
    if temoin is None:
        return
    try:
        temoin.parent.mkdir(parents=True, exist_ok=True)
        temoin.write_text(f"{dossier}\n", encoding="utf-8")
    except OSError as exc:
        print(f"Témoin du passage non écrit ({temoin}) : {exc}", file=erreur)


def _synthese(rapport: Rapport, dossier: Path) -> str:
    """Le verdict du passage en quelques lignes — le rapport dit le reste."""
    lignes = [
        "",
        f"Verdict : {'les scénarios joués sont verts' if rapport.vert else 'au moins un rouge'}",
    ]
    for resultat in rapport.resultats:
        marque = "vert " if resultat.vert else "rouge"
        rejeu = " (rejoué)" if resultat.rejoue else ""
        lignes.append(
            f"  {resultat.identifiant}  {marque}{rejeu}  {resultat.motif[:120]}"
        )
    lignes.append(f"Rapport : {dossier / rapport_module.FICHIER_MARKDOWN}")
    return "\n".join(lignes)


class _Options:
    """Les options de la ligne de commande, une fois lues."""

    def __init__(self) -> None:
        self.scenarios: list[str] = []
        self.delai_s: float = DELAI_RUN_S
        self.nettoyer = False
        self.sauver_etat = False
        self.liste = False
        self.temoin: Path | None = None


def _options(args: Sequence[str]) -> _Options:
    """Lit la ligne de commande — lève `ValueError` avec son motif sur tout écart."""
    options = _Options()
    reste = list(args)
    while reste:
        arg = reste.pop(0)
        if arg == "--liste":
            options.liste = True
        elif arg == "--nettoyer":
            options.nettoyer = True
        elif arg == "--sauver-etat":
            options.sauver_etat = True
        elif arg == "--scenario":
            options.scenarios.extend(_valeurs(_suivant(arg, reste)))
        elif arg.startswith("--scenario="):
            options.scenarios.extend(_valeurs(arg.partition("=")[2]))
        elif arg == "--delai":
            options.delai_s = _delai(_suivant(arg, reste))
        elif arg.startswith("--delai="):
            options.delai_s = _delai(arg.partition("=")[2])
        elif arg == "--temoin":
            options.temoin = Path(_suivant(arg, reste))
        elif arg.startswith("--temoin="):
            if not arg.partition("=")[2]:
                raise ValueError("--temoin attend une valeur")
            options.temoin = Path(arg.partition("=")[2])
        else:
            raise ValueError(f"argument inconnu : {arg}")
    return options


def _suivant(arg: str, reste: list[str]) -> str:
    """La valeur qui suit une option — lève si elle manque."""
    if not reste:
        raise ValueError(f"{arg} attend une valeur")
    return reste.pop(0)


def _valeurs(brut: str) -> list[str]:
    """« S1,S3 » → ['S1', 'S3'] — la virgule autant que l'option répétée."""
    valeurs = [morceau.strip() for morceau in brut.split(",") if morceau.strip()]
    if not valeurs:
        raise ValueError("--scenario attend au moins un identifiant")
    return valeurs


def _delai(brut: str) -> float:
    """Le délai par run, en secondes — strictement positif."""
    try:
        valeur = float(brut)
    except ValueError as exc:
        raise ValueError(f"--delai attend un nombre de secondes (reçu : {brut!r})") from exc
    if valeur <= 0:
        raise ValueError(f"--delai doit être > 0 (reçu : {brut!r})")
    return valeur
