"""Le banc : il déroule les scénarios de référence et rend un verdict par scénario (#1148).

    .venv/Scripts/python.exe -m maestro.scenarios [--scenario S1[,S3]] [--delai <s>]
                                                  [--nettoyer | --sauver-etat] [--liste]

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
que les quatre scénarios soient verts : c'est son rôle de montrer les rouges. S1
passe au vert avec #1149, S4 avec #1157. Un code de sortie non nul n'est donc pas
un défaut d'outillage — c'est la mesure.

**Un rouge non déterministe se rejoue une fois, et le rapport le dit.** S2 demande
au modèle d'écrire du code qui s'exécute, S4 de reconnaître une cause dans une
phrase : les deux échouent parfois sans que le produit ait changé (docs/40 §5). Le
second passage fait foi, et `rejoue` reste écrit au rapport — un rejeu tu ferait
lire deux runs comme un seul.

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

## Codes de sortie

`0` les scénarios joués sont **tous** verts · `1` au moins un rouge · `2` usage ·
`3` l'API ne répond pas (rien n'a été joué) · `4` le passage est joué mais son état
n'a pas pu être sauvé (`--sauver-etat` ; le verdict est au rapport). Le `3` est un
refus et non un rouge : distinguer « le produit s'est trompé » de « le produit
n'était pas allumé » est la première chose qu'un bouclage a besoin de savoir.
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
    Journal,
    Rapport,
    Resultat,
    empeche,
)
from maestro.scenarios.modele import (
    horodatage as horodatage_courant,
)
from maestro.scenarios.projets import Atelier
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
    "[--nettoyer | --sauver-etat] [--liste]"
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
        resultat = _une_tentative(scenario, fabrique, horloge=horloge)
        if not resultat.vert and scenario.rejouable:
            trace(f"  rouge non déterministe : {scenario.identifiant} rejoué une fois")
            resultat = _une_tentative(scenario, fabrique, horloge=horloge, rejoue=True)
        trace(f"  {'vert' if resultat.vert else 'rouge'} — {resultat.motif}")
        resultats.append(resultat)
    return Rapport(horodatage=horodatage, resultats=tuple(resultats))


def _une_tentative(
    scenario: Scenario,
    fabrique: Callable[[], Contexte],
    *,
    horloge: Callable[[], float],
    rejoue: bool = False,
) -> Resultat:
    """Joue un scénario une fois, et rend son résultat — même si l'API a refusé."""
    ctx = fabrique()
    debut = horloge()
    try:
        issue = scenario.jouer(ctx)
    except ErreurAPI as echec:
        issue = empeche(f"l'API a refusé : {echec}")
    except OSError as echec:  # disque, projet jetable devenu illisible
        issue = empeche(f"le banc n'a pas pu jouer : {echec}")
    duree = horloge() - debut
    return Resultat(
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

    horodatage = horodatage_courant()
    atelier = atelier or Atelier.pour(horodatage)
    juge = juge or JugeModele()
    client_resolu, juge_resolu, atelier_resolu = client, juge, atelier

    contextes: list[Contexte] = []

    def fabrique() -> Contexte:
        ctx = Contexte(
            client=client_resolu,
            atelier=atelier_resolu,
            juge=juge_resolu,
            journal=Journal(),
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
        atelier_resolu.retirer()
        print(
            f"Nettoyé : déclarations retirées et atelier {atelier_resolu.racine} effacé.",
            file=sortie,
        )
    else:
        print(
            f"Projets jetables conservés sous {atelier_resolu.racine} — ce sont les "
            "pièces d'un rouge (`--nettoyer` les retire).",
            file=sortie,
        )
    return CODE_VERT if rapport.vert else CODE_ROUGE


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
