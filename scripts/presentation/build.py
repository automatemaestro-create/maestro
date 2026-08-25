"""Génère la présentation HTML d'un milestone (#142, enrichie par #546).

Entrée : un fichier JSON décrivant le milestone, ses tickets, les captures, les
**écrans touchés** et les **démonstrations filmées** (voir `SCHEMA` plus bas et la
commande `/milestone-presentation`). Sortie : un fichier HTML **autonome** — CSS
en ligne, images et vidéos en `data:`, aucune ressource externe — lisible en
thème clair comme sombre.

    .venv/Scripts/python.exe scripts/presentation/build.py <donnees.json> [--sortie <fichier.html>]

Le gabarit vit ici, pas dans le prompt de la commande : c'est ce qui rend le
rendu stable d'une génération à l'autre. La commande fournit la matière (quels
tickets, quel résumé, quelle capture illustre quoi, quels écrans un ticket a
touchés, quels clips ont été tournés) ; ce script décide de la forme.
"""

from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

SCHEMA = """\
{
  "milestone": {"titre": str, "etat": "active"|"closed", "debut": str|null,
                "echeance": str|null, "resume": str|null},
  "projet":    {"url": str},                       # base des liens vers les tickets
  "tickets":   [{"iid": int, "titre": str, "statut": str, "type": str|null,
                 "agent": str|null, "prio": str|null, "resume": str|null,
                 "capture": str|null,              # `capture` = clé d'une entrée de `captures`
                 "ecrans": [str]|null}],           # clés d'entrées de `ecrans` (ecrans-touches.sh)
  "captures":  [{"cle": str, "libelle": str, "fichier": str}],
  "ecrans":    [{"cle": str, "libelle": str, "route": str|null}],
  "videos":    [{"cle": str, "libelle": str, "fichier": str,
                 "affiche": str|null}],            # image de repli si le clip est écarté
  "notes":     [str]                               # avertissements affichés en pied
}
"""

# --- Vocabulaire ---------------------------------------------------------------------------------

# Les états du cycle de vie (champ Status de Projects v2, docs/10-workflow-git.md §3.1), regroupés
# pour la lecture : un lecteur de présentation veut savoir ce qui est acquis, ce qui bouge et ce qui
# reste — pas le détail du cycle de vie. « En revue » garde sa case à lui : le travail est fait, le
# merge ne l'est pas.
#
# Ce sont les LIBELLÉS qui sont attendus ici, pas les slugs : `lib.sh milestone-issues` ne rend
# jamais autre chose (contrat de surface en tête de lib.sh), et un slug qui arriverait jusqu'ici
# tomberait dans « À venir » via libelle_groupe.
GROUPES: list[tuple[str, tuple[str, ...], str]] = [
    ("Livré", ("Terminé",), "Fusionné sur main."),
    ("En revue", ("En revue",), "Terminé, en attente de merge."),
    ("En cours", ("En cours",), "Travail engagé."),
    ("À venir", ("À faire",), "Planifié sur le milestone."),
    ("Écarté", ("Abandonné", "Doublon"), "Fermé sans être réalisé."),
]

#: Rampe ORDINALE bleue (une seule teinte, luminance monotone) : plus un lot est avancé, plus il
#: pèse visuellement. Validée par scripts/validate_palette.js du skill dataviz — mode `light`
#: sur surface #fcfcfb et mode `dark` sur #1a1a19, contrainte ordinale (l'extrémité proche de la
#: surface reste au-dessus de 2:1). Le sens s'inverse en sombre : sur fond noir, c'est le pas
#: CLAIR qui a le plus de présence, donc qui porte « livré ».
RAMPE_CLAIR = {
    "À venir": "#86b6ef", "En cours": "#3987e5", "En revue": "#256abf", "Livré": "#104281",
}
RAMPE_SOMBRE = {
    "À venir": "#184f95", "En cours": "#256abf", "En revue": "#3987e5", "Livré": "#86b6ef",
}
#: « Écarté » sort de la rampe : ce n'est pas une étape d'avancement, c'est un retrait.
GRIS_ECARTE = "#898781"

TYPES = {
    "feature": "Fonctionnalité",
    "bug": "Correction",
    "infra": "Infra",
    "doc": "Documentation",
}

AGENTS = {
    "dev": "Dev",
    "bdd": "BDD",
    "devops": "DevOps",
    "design": "Design",
    "qa": "QA",
    "orchestrateur": "Orchestrateur",
}

PRIOS = {"haute": "Priorité haute", "moyenne": "Priorité moyenne", "basse": "Priorité basse"}

# Ordre d'apparition des types dans une section : le lecteur cherche d'abord les fonctionnalités.
ORDRE_TYPES = ["feature", "bug", "infra", "doc", "-"]

# --- Écrans touchés & démonstrations (#546) -------------------------------------------------------

#: La clé que `ecrans-touches.sh` rend pour une surface visible SANS ROUTE : un composant partagé
#: (`apps/web/components/**`), ou la coquille de tous les écrans (`layout.tsx`, `globals.css`). Ce
#: n'est PAS un écran, et c'est tout l'intérêt de la garder : #543 demande que la limite soit
#: **nommée** plutôt que devinée ou tue — la rattacher à une route serait la rattacher au hasard.
CLE_INDETERMINEE = "-"
LIBELLE_INDETERMINE = "Composants partagés"
AIDE_INDETERMINEE = (
    "Écran indéterminé : la modification porte sur une surface commune à plusieurs écrans."
)

#: Un mébioctet, l'unité des deux plafonds ci-dessous.
MIO = 1024 * 1024

#: Plafond **par clip**, réglable (`MAESTRO_PRESENTATION_VIDEO_MAX`, en Mio ; `0` = aucun).
#: Généreux au regard du mesuré (334 à 1215 Kio pour les cinq parcours de #545) : il n'est pas là
#: pour rogner les clips normaux, mais pour qu'un clip parti en vrille ne coule pas le fichier.
VIDEO_MAX_MIO_DEFAUT = 6.0

#: Plafond **du fichier produit**, réglable (`MAESTRO_PRESENTATION_MAX`, en Mio ; `0` = aucun).
#: Une présentation est faite pour être ENVOYÉE : au-delà de ce que prend une pièce jointe, elle a
#: perdu sa raison d'être. 25 Mio est la limite de la plupart des messageries.
FICHIER_MAX_MIO_DEFAUT = 25.0

#: Marge retranchée du budget vidéo : la page est pesée AVANT d'y insérer les clips, or leur
#: balisage (~1 Kio par clip) et les notes de repli s'y ajouteront. Quelques dizaines de Kio face à
#: un plafond en Mio — mais les compter en trop plutôt qu'en moins est ce qui rend le plafond vrai.
MARGE_OCTETS = 64 * 1024


# --- Utilitaires ---------------------------------------------------------------------------------


def slugifier(texte: str) -> str:
    """« Phase 3 — V2 » → « phase-3-v2 » : le nom de fichier de la présentation."""
    sans_accents = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", sans_accents.lower())).strip("-")


def image_en_data_uri(chemin: Path) -> str | None:
    """Encode un PNG en `data:` pour que la présentation reste un fichier unique."""
    try:
        octets = chemin.read_bytes()
    except OSError as erreur:
        print(f"[build] ⚠ capture illisible ({chemin}) : {erreur}", file=sys.stderr)
        return None
    return f"data:image/png;base64,{base64.b64encode(octets).decode('ascii')}"


def video_en_data_uri(chemin: Path) -> str | None:
    """Encode un clip webm en `data:` — même motif que l'image, même promesse d'autonomie."""
    try:
        octets = chemin.read_bytes()
    except OSError as erreur:
        print(f"[build] ⚠ clip illisible ({chemin}) : {erreur}", file=sys.stderr)
        return None
    return f"data:video/webm;base64,{base64.b64encode(octets).decode('ascii')}"


def plafond_octets(variable: str, defaut_mio: float) -> float:
    """
    Un plafond de taille lu dans l'environnement, en Mio, rendu en octets.

    `0` (ou une valeur vide) vaut **aucun plafond** — le même repli qu'ailleurs dans le dépôt
    (`MAESTRO_ORCHESTRATE_BUDGET`, `--timeout 0`), et la seule façon d'annuler une variable déjà
    posée. Une valeur illisible retombe sur le défaut **en le disant** : un plafond silencieusement
    ignoré est pire qu'un plafond absent.
    """
    brut = os.environ.get(variable, "").strip()
    if not brut:
        return defaut_mio * MIO
    try:
        valeur = float(brut.replace(",", "."))
    except ValueError:
        print(
            f"[build] ⚠ {variable} = « {brut} » n'est pas un nombre de Mio"
            f" — plafond par défaut ({defaut_mio:g} Mio)",
            file=sys.stderr,
        )
        return defaut_mio * MIO
    if valeur < 0:
        print(
            f"[build] ⚠ {variable} = « {brut} » est négatif"
            f" — plafond par défaut ({defaut_mio:g} Mio)",
            file=sys.stderr,
        )
        return defaut_mio * MIO
    return math.inf if valeur == 0 else valeur * MIO


def poids_texte(octets: float) -> str:
    """« 512 Kio », « 1,6 Mio », « sans plafond » — à la virgule française."""
    if octets == math.inf:
        return "sans plafond"
    if octets < MIO:
        return f"{max(round(octets / 1024), 0)} Kio"
    return f"{octets / MIO:.1f}".replace(".", ",") + " Mio"


def libelle_groupe(statut: str) -> str:
    """Le groupe d'affichage d'un état du cycle de vie ; tout état inconnu tombe dans « À venir ».

    Le repli couvre notamment le « - » que rendent les helpers pour un ticket SANS ÉTAT — hors du
    projet Projects v2, ou Status vide (cas d'un ticket ouvert à la main dans l'interface web, qui
    n'est ajouté au projet par personne). C'est la dérive que doctor.sh signale.
    """
    for nom, statuts, _ in GROUPES:
        if statut in statuts:
            return nom
    return "À venir"


def pourcent(part: int, total: int) -> float:
    return 0.0 if total == 0 else round(100 * part / total, 1)


def pourcent_texte(part: int, total: int) -> str:
    """« 100 », « 42,9 » — sans décimale inutile, et à la virgule française."""
    return f"{pourcent(part, total):g}".replace(".", ",")


def ancre_ecran(cle: str) -> str:
    """L'ancre d'un écran dans la page. `-` ne se slugifie pas : il lui faut un nom à lui."""
    return slugifier(cle) or "indetermine"


def libelle_ecran_defaut(cle: str) -> str:
    """Le nom d'un écran quand le référentiel n'en donne pas : la clé, sauf pour l'indéterminé."""
    return LIBELLE_INDETERMINE if cle == CLE_INDETERMINEE else cle


def ecrans_du_ticket(ticket: dict[str, Any]) -> list[str]:
    """Les clés d'écrans d'un ticket, dédoublonnées dans l'ordre donné.

    Un ticket **sans surface visible** rend une liste vide, et n'affichera donc rien : c'est la
    règle déjà écrite pour les captures (« une vignette qui n'illustre rien dessert la
    présentation »), et l'absence de ligne est précisément ce que rend `ecrans-touches.sh`.
    """
    return list(dict.fromkeys(c for c in (ticket.get("ecrans") or []) if c))


def preparer_ecrans(
    declares: list[dict[str, Any]], tickets: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Le référentiel des écrans : ce que le JSON déclare, complété par ce que les tickets citent.

    Un écran cité par un ticket mais absent du référentiel est **rendu quand même**, sous sa clé.
    Le cas n'est pas théorique : `ecrans-touches.sh` rend la clé d'une route SERVIE MAIS HORS MENU
    (`/projets`, #280), pour laquelle aucune capture n'existe. Le taire retirerait de la vue un
    écran que les commits désignent — l'inverse de ce que ce chantier cherche.
    """
    ecrans: dict[str, dict[str, Any]] = {}
    for entree in declares:
        cle = entree.get("cle")
        if not cle:
            continue
        ecrans[cle] = {
            "cle": cle,
            "libelle": entree.get("libelle") or libelle_ecran_defaut(cle),
            "route": entree.get("route"),
        }
    for ticket in tickets:
        for cle in ecrans_du_ticket(ticket):
            ecrans.setdefault(
                cle, {"cle": cle, "libelle": libelle_ecran_defaut(cle), "route": None}
            )
    return ecrans


def compter_ecrans(tickets: list[dict[str, Any]]) -> Counter[str]:
    """Combien de tickets ont touché chaque écran — un ticket compte **une fois** par écran."""
    comptes: Counter[str] = Counter()
    for ticket in tickets:
        comptes.update(ecrans_du_ticket(ticket))
    return comptes


# --- Rendu ----------------------------------------------------------------------------------------


def rendre_badges(ticket: dict[str, Any]) -> str:
    """Les étiquettes d'un ticket — texte seul, jamais la couleur seule."""
    morceaux = []
    type_ = ticket.get("type")
    if type_ and type_ != "-":
        morceaux.append(f'<span class="badge badge-type">{escape(TYPES.get(type_, type_))}</span>')
    agent = ticket.get("agent")
    if agent and agent != "-":
        morceaux.append(f'<span class="badge">{escape(AGENTS.get(agent, agent))}</span>')
    prio = ticket.get("prio")
    if prio and prio != "-":
        morceaux.append(f'<span class="badge">{escape(PRIOS.get(prio, prio))}</span>')
    return "".join(morceaux)


def rendre_ecrans_touches(ticket: dict[str, Any], ecrans: dict[str, dict[str, Any]]) -> str:
    """Les écrans qu'un ticket a touchés, sur sa carte — vides, ils n'affichent rien du tout."""
    cles = ecrans_du_ticket(ticket)
    if not cles:
        return ""
    puces = "".join(
        f'<a class="ecran-puce" href="#ecran-{escape(ancre_ecran(cle))}">'
        f"{escape(ecrans.get(cle, {}).get('libelle') or libelle_ecran_defaut(cle))}</a>"
        for cle in cles
    )
    return (
        '<p class="carte-ecrans"><span class="carte-ecrans-titre">Écrans touchés</span>'
        f"{puces}</p>"
    )


def rendre_carte(
    ticket: dict[str, Any],
    base_url: str,
    captures: dict[str, dict[str, str]],
    ecrans: dict[str, dict[str, Any]],
) -> str:
    iid = ticket["iid"]
    lien = f"{base_url.rstrip('/')}/-/work_items/{iid}"
    resume = ticket.get("resume") or ""
    bloc_resume = f'<p class="carte-resume">{escape(resume)}</p>' if resume else ""

    vignette = ""
    cle = ticket.get("capture")
    if cle and cle in captures:
        capture = captures[cle]
        vignette = (
            f'<a class="vignette" href="#capture-{escape(cle)}" '
            f'title="Voir « {escape(capture["libelle"])} » en grand">'
            f'<img src="{capture["uri"]}" alt="Control Tower — {escape(capture["libelle"])}" '
            f'loading="lazy"></a>'
        )

    return f"""
        <article class="carte">
          <div class="carte-corps">
            <div class="carte-entete">
              <a class="iid" href="{escape(lien)}">#{iid}</a>
              {rendre_badges(ticket)}
            </div>
            <h4 class="carte-titre">{escape(ticket["titre"])}</h4>
            {bloc_resume}
            {rendre_ecrans_touches(ticket, ecrans)}
          </div>
          {vignette}
        </article>"""


def rendre_section(
    nom: str,
    aide: str,
    tickets: list[dict[str, Any]],
    base_url: str,
    captures: dict[str, dict[str, str]],
    ecrans: dict[str, dict[str, Any]],
) -> str:
    """Une section de statut, sous-groupée par type de travail."""
    par_type: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        par_type.setdefault(ticket.get("type") or "-", []).append(ticket)

    blocs = []
    for type_ in ORDRE_TYPES:
        lot = par_type.get(type_)
        if not lot:
            continue
        lot.sort(key=lambda t: t["iid"])
        cartes = "".join(rendre_carte(t, base_url, captures, ecrans) for t in lot)
        titre_type = TYPES.get(type_, "Divers")
        blocs.append(
            f'<h3 class="sous-titre">{escape(titre_type)}'
            f'<span class="compte">{len(lot)}</span></h3>'
            f'<div class="grille-cartes">{cartes}</div>'
        )

    ancre = slugifier(nom)
    return f"""
      <section class="section" id="section-{ancre}">
        <header class="section-entete">
          <h2>{escape(nom)}<span class="compte compte-fort">{len(tickets)}</span></h2>
          <p class="aide">{escape(aide)}</p>
        </header>
        {"".join(blocs)}
      </section>"""


def rendre_jauge(comptes: Counter[str], total: int) -> str:
    """
    La barre d'avancement : un empilement ordinal, un segment par groupe.

    Chaque segment porte une infobulle au survol ; la légende sous la barre étiquette les
    segments en clair (nombre + part), et la vue tableau reprend les mêmes chiffres — l'identité
    ne repose donc jamais sur la seule couleur.
    """
    presents = [(nom, comptes[nom]) for nom, _, _ in GROUPES if comptes[nom] > 0]

    segments = []
    for nom, compte in presents:
        segments.append(
            f'<div class="segment" style="width:{pourcent(compte, total)}%" '
            f'data-groupe="{escape(slugifier(nom))}" '
            f'title="{escape(nom)} — {compte} ticket(s), {pourcent_texte(compte, total)} %">'
            f"</div>"
        )

    legende = []
    for nom, compte in presents:
        legende.append(
            f'<li><span class="pastille" data-groupe="{escape(slugifier(nom))}"></span>'
            f'<span class="legende-nom">{escape(nom)}</span>'
            f'<span class="legende-valeur">{compte} · {pourcent_texte(compte, total)} %</span></li>'
        )

    lignes = "".join(
        f'<tr><th scope="row">{escape(nom)}</th><td>{compte}</td>'
        f"<td>{pourcent_texte(compte, total)} %</td></tr>"
        for nom, compte in presents
    )

    return f"""
      <div class="jauge" role="img"
           aria-label="Répartition des {total} tickets du milestone par état d'avancement">
        {"".join(segments)}
      </div>
      <ul class="legende">{"".join(legende)}</ul>
      <details class="tableau">
        <summary>Voir les chiffres en tableau</summary>
        <table>
          <thead><tr><th scope="col">État</th><th scope="col">Tickets</th>
          <th scope="col">Part</th></tr></thead>
          <tbody>{lignes}</tbody>
        </table>
      </details>"""


def rendre_tuiles(comptes: Counter[str], total: int, par_type: Counter[str]) -> str:
    """Le bandeau de chiffres clés — des valeurs, pas des graphiques."""
    livres = comptes["Livré"] + comptes["En revue"]
    tuiles = [
        ("Tickets sur le milestone", total, None),
        ("Livrés", livres, f"{pourcent_texte(livres, total)} % du milestone"),
        ("En cours", comptes["En cours"], None),
        ("À venir", comptes["À venir"], None),
    ]
    rendu = []
    for libelle, valeur, note in tuiles:
        bloc_note = f'<p class="tuile-note">{escape(note)}</p>' if note else ""
        rendu.append(
            f'<div class="tuile"><p class="tuile-libelle">{escape(libelle)}</p>'
            f'<p class="tuile-valeur">{valeur}</p>{bloc_note}</div>'
        )

    detail = " · ".join(
        f"{TYPES.get(t, t)} {n}" for t, n in par_type.most_common() if t and t != "-"
    )
    ligne_detail = f'<p class="tuiles-detail">{escape(detail)}</p>' if detail else ""
    return f'<div class="tuiles">{"".join(rendu)}</div>{ligne_detail}'


def rendre_ecrans(
    comptes: Counter[str],
    ecrans: dict[str, dict[str, Any]],
    captures: dict[str, dict[str, str]],
) -> str:
    """
    La vue « écrans touchés par la phase » : chaque écran, et combien de tickets l'ont modifié.

    L'ordre est le poids décroissant — ce que la phase a le plus remué se lit en premier —, à
    égalité le nom. L'**indéterminé** sort de ce classement et passe en dernier : le ranger parmi
    les écrans le ferait lire comme un écran, alors qu'il dit exactement l'inverse.
    """
    if not comptes:
        return ""

    ordonnes = sorted(
        comptes.items(),
        key=lambda kv: (
            kv[0] == CLE_INDETERMINEE,
            -kv[1],
            slugifier(ecrans.get(kv[0], {}).get("libelle") or kv[0]),
        ),
    )

    cartes = []
    for cle, compte in ordonnes:
        ecran = ecrans.get(cle, {})
        libelle = ecran.get("libelle") or libelle_ecran_defaut(cle)
        capture = captures.get(cle)
        # Pas de `loading="lazy"` ici ni sur l'affiche d'un clip : la source est une data URI, donc
        # il n'y a AUCUNE requête à différer — les octets sont déjà dans le document. Le report
        # n'économise rien et peut coûter l'image : mesuré sur cette page, une image `lazy` dont la
        # hauteur calculée vaut zéro tant qu'elle n'est pas chargée reste à 0×0 et ne s'affiche
        # jamais. Un visuel qui doit être vu ne se charge pas paresseusement.
        vue = (
            f'<a class="ecran-vue" href="#capture-{escape(cle)}" '
            f'title="Voir « {escape(capture["libelle"])} » en grand">'
            f'<img src="{capture["uri"]}" alt="Control Tower — {escape(capture["libelle"])}"></a>'
            if capture
            else ""
        )
        route = ecran.get("route")
        ligne_route = f'<p class="ecran-route">{escape(route)}</p>' if route else ""
        aide = (
            f'<p class="ecran-aide">{escape(AIDE_INDETERMINEE)}</p>'
            if cle == CLE_INDETERMINEE
            else ""
        )
        cartes.append(
            f'<article class="ecran" id="ecran-{escape(ancre_ecran(cle))}">{vue}'
            f'<div class="ecran-corps"><h4 class="ecran-titre">{escape(libelle)}</h4>'
            f'{ligne_route}<p class="ecran-compte">{compte} ticket(s)</p>{aide}</div></article>'
        )

    return f"""
      <section class="section" id="section-ecrans">
        <header class="section-entete">
          <h2>Écrans touchés par la phase
              <span class="compte compte-fort">{len(ordonnes)}</span></h2>
          <p class="aide">Dérivés des commits de chaque ticket, pas devinés.</p>
        </header>
        <div class="grille-ecrans">{"".join(cartes)}</div>
      </section>"""


def rendre_demonstrations(clips: list[dict[str, Any]]) -> str:
    """
    Les démonstrations filmées, jouables **dans le fichier** — source `data:`, zéro requête réseau.

    Pas de démarrage automatique : plusieurs clips qui partent ensemble à l'ouverture rendent la
    page illisible. Muet, en boucle, avec les contrôles — la main reste au lecteur.

    Un clip **écarté** garde sa place et dit pourquoi : son affiche s'il en a une, un cartouche
    sinon, et jamais un trou silencieux.
    """
    if not clips:
        return ""

    figures = []
    for clip in clips:
        libelle = clip["libelle"]
        if clip["ecarte"] is None:
            affiche = f' poster="{clip["affiche"]}"' if clip["affiche"] else ""
            visuel = (
                f'<video class="clip-video" controls muted loop playsinline'
                f' preload="metadata"{affiche} src="{clip["uri"]}">'
                f"Ce navigateur ne sait pas lire les vidéos webm — le clip"
                f" « {escape(libelle)} » ne peut pas être joué ici.</video>"
            )
            note = ""
        else:
            # L'affiche est le repli EXIGÉ par le critère : elle se charge donc tout de suite
            # (voir `rendre_ecrans` — une data URI n'a rien à différer, et `lazy` l'a laissée
            # invisible à la mesure).
            visuel = (
                f'<span class="clip-affiche"><img src="{clip["affiche"]}" '
                f'alt="Control Tower — {escape(libelle)}"></span>'
                if clip["affiche"]
                else f'<p class="clip-absente">Clip non intégré — {escape(libelle)}</p>'
            )
            note = f'<p class="clip-note">Clip écarté : {escape(clip["ecarte"])}.</p>'
        figures.append(
            f'<figure class="clip" id="clip-{escape(slugifier(clip["cle"]) or "clip")}">{visuel}'
            f'<figcaption class="clip-legende">{escape(libelle)}</figcaption>{note}</figure>'
        )

    retenus = sum(1 for c in clips if c["ecarte"] is None)
    aide = (
        "Tournées sur la stack de démonstration, jouables ici même."
        if retenus == len(clips)
        else f"Tournées sur la stack de démonstration. {len(clips) - retenus} clip(s) sur "
        f"{len(clips)} écartés pour tenir le plafond de taille."
    )
    return f"""
      <section class="section" id="section-demonstrations">
        <header class="section-entete">
          <h2>Démonstrations<span class="compte compte-fort">{retenus}</span></h2>
          <p class="aide">{escape(aide)}</p>
        </header>
        <div class="grille-clips">{"".join(figures)}</div>
      </section>"""


def rendre_galerie(captures: dict[str, dict[str, str]]) -> str:
    if not captures:
        return ""
    figures = "".join(
        f'<figure class="figure" id="capture-{escape(cle)}">'
        f'<img src="{c["uri"]}" alt="Control Tower — {escape(c["libelle"])}" loading="lazy">'
        f"<figcaption>{escape(c['libelle'])}</figcaption></figure>"
        for cle, c in captures.items()
    )
    return f"""
      <section class="section" id="section-captures">
        <header class="section-entete">
          <h2>La Control Tower<span class="compte compte-fort">{len(captures)}</span></h2>
          <p class="aide">Captures prises sur l'application au moment de la génération.</p>
        </header>
        <div class="galerie">{figures}</div>
      </section>"""


# --- Feuille de style -----------------------------------------------------------------------------


def feuille_de_style() -> str:
    """
    Le CSS complet, en ligne (la présentation doit rester un fichier unique).

    Les couleurs sont posées en variables : les valeurs sombres sont déclarées sous les deux
    portées recommandées — la media query pour la préférence système, `[data-theme]` pour la
    bascule de la page, qui doit l'emporter dans les deux sens.
    """
    rampe_claire = "\n".join(
        f'    .jauge .segment[data-groupe="{slugifier(nom)}"],'
        f' .pastille[data-groupe="{slugifier(nom)}"] {{ background: {hex_}; }}'
        for nom, hex_ in RAMPE_CLAIR.items()
    )
    rampe_sombre = "\n".join(
        f'    .jauge .segment[data-groupe="{slugifier(nom)}"],'
        f' .pastille[data-groupe="{slugifier(nom)}"] {{ background: {hex_}; }}'
        for nom, hex_ in RAMPE_SOMBRE.items()
    )
    ecarte = (
        f'    .jauge .segment[data-groupe="{slugifier("Écarté")}"],'
        f' .pastille[data-groupe="{slugifier("Écarté")}"] {{ background: {GRIS_ECARTE}; }}'
    )

    couleurs_sombres = """
      color-scheme: dark;
      --plan: #0d0d0d;
      --surface: #1a1a19;
      --encre: #ffffff;
      --encre-2: #c3c2b7;
      --encre-3: #898781;
      --trait: rgba(255,255,255,0.10);
      --filet: #2c2c2a;
      --accent: #3987e5;"""

    return f"""
  :root {{
    color-scheme: light;
    --plan: #f9f9f7;
    --surface: #fcfcfb;
    --encre: #0b0b0b;
    --encre-2: #52514e;
    --encre-3: #898781;
    --trait: rgba(11,11,11,0.10);
    --filet: #e1e0d9;
    --accent: #2a78d6;
    --sans: system-ui, -apple-system, "Segoe UI", sans-serif;
  }}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="clair"])) {{{couleurs_sombres}
    }}
  }}
  :root[data-theme="sombre"] {{{couleurs_sombres}
  }}

  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 0 1.5rem 5rem;
    font-family: var(--sans);
    background: var(--plan); color: var(--encre);
    -webkit-font-smoothing: antialiased;
  }}
  .page {{ max-width: 1100px; margin: 0 auto; }}
  a {{ color: var(--accent); }}

  /* --- Bascule de thème --- */
  .barre {{ display: flex; justify-content: flex-end; padding-top: 1.25rem; }}
  .bascule {{
    font: inherit; font-size: .8125rem; color: var(--encre-2);
    background: var(--surface); border: 1px solid var(--trait);
    border-radius: 999px; padding: .4rem .85rem; cursor: pointer;
  }}
  .bascule:hover {{ color: var(--encre); }}

  /* --- Couverture --- */
  .couverture {{ padding: 2.5rem 0 3rem; border-bottom: 1px solid var(--filet); }}
  .surtitre {{
    margin: 0 0 .75rem; font-size: .75rem; font-weight: 600;
    letter-spacing: .08em; text-transform: uppercase; color: var(--encre-3);
  }}
  .couverture h1 {{
    margin: 0; font-size: clamp(2rem, 5vw, 3rem); line-height: 1.1;
    letter-spacing: -0.02em; font-weight: 650;
  }}
  .meta {{ margin: 1rem 0 0; color: var(--encre-2); font-size: .9375rem; }}
  .meta .sep {{ color: var(--encre-3); padding: 0 .5rem; }}
  .resume {{
    margin: 1.75rem 0 0; max-width: 62ch; font-size: 1.0625rem;
    line-height: 1.65; color: var(--encre-2);
  }}

  /* --- Chiffre de tête + jauge --- */
  /* `flex-start` et non `flex-end` : la vue tableau se déplie sous la barre, et un alignement
     par le bas ferait descendre le chiffre de tête à chaque ouverture. */
  .avancement {{ display: flex; flex-wrap: wrap; gap: 2.5rem; align-items: flex-start;
                 margin: 2.75rem 0 0; }}
  .heros {{ flex: 0 0 auto; }}
  .heros-valeur {{ margin: 0; font-size: 4rem; line-height: 1; font-weight: 650;
                   letter-spacing: -0.03em; }}
  .heros-libelle {{ margin: .5rem 0 0; color: var(--encre-2); font-size: .9375rem; }}
  .avancement-barre {{ flex: 1 1 22rem; min-width: 18rem; margin-top: 1.4rem; }}

  .jauge {{ display: flex; gap: 2px; height: 14px; margin: 0 0 .9rem; }}
  .jauge .segment {{ border-radius: 2px; }}
  .jauge .segment:first-child {{ border-top-left-radius: 4px; border-bottom-left-radius: 4px; }}
  .jauge .segment:last-child {{ border-top-right-radius: 4px; border-bottom-right-radius: 4px; }}

  .legende {{ display: flex; flex-wrap: wrap; gap: .35rem 1.25rem;
              margin: 0; padding: 0; list-style: none; font-size: .8125rem; }}
  .legende li {{ display: flex; align-items: center; gap: .45rem; }}
  .pastille {{ width: 9px; height: 9px; border-radius: 2px; flex: none; }}
  .legende-nom {{ color: var(--encre-2); }}
  .legende-valeur {{ color: var(--encre-3); }}

  .tableau {{ margin: 1.25rem 0 0; font-size: .8125rem; }}
  .tableau summary {{ color: var(--encre-2); cursor: pointer; }}
  .tableau table {{ margin: .75rem 0 0; border-collapse: collapse;
                    font-variant-numeric: tabular-nums; }}
  .tableau th, .tableau td {{ padding: .3rem .9rem .3rem 0; text-align: left;
                              border-bottom: 1px solid var(--filet); font-weight: 400;
                              color: var(--encre-2); }}
  .tableau thead th {{ color: var(--encre-3); font-size: .75rem; }}

  /* --- Tuiles --- */
  .tuiles {{ display: grid; gap: 1px; margin: 3rem 0 0;
             grid-template-columns: repeat(auto-fit, minmax(11rem, 1fr));
             background: var(--filet); border: 1px solid var(--filet); border-radius: 10px;
             overflow: hidden; }}
  .tuile {{ background: var(--surface); padding: 1.25rem 1.25rem 1.4rem; }}
  .tuile-libelle {{ margin: 0; font-size: .8125rem; color: var(--encre-2); }}
  .tuile-valeur {{ margin: .5rem 0 0; font-size: 2rem; font-weight: 600; line-height: 1; }}
  .tuile-note {{ margin: .45rem 0 0; font-size: .75rem; color: var(--encre-3); }}
  .tuiles-detail {{ margin: .85rem 0 0; font-size: .8125rem; color: var(--encre-3); }}

  /* --- Sections --- */
  .section {{ margin: 3.5rem 0 0; }}
  .section-entete {{ border-bottom: 1px solid var(--filet); padding-bottom: .85rem;
                     margin-bottom: 1.75rem; }}
  .section-entete h2 {{ margin: 0; font-size: 1.375rem; font-weight: 620;
                        letter-spacing: -0.01em; display: flex; align-items: center; gap: .7rem; }}
  .aide {{ margin: .4rem 0 0; font-size: .875rem; color: var(--encre-3); }}
  .compte {{ font-size: .75rem; font-weight: 600; color: var(--encre-2);
             background: var(--filet); border-radius: 999px; padding: .15rem .55rem;
             font-variant-numeric: tabular-nums; }}
  .sous-titre {{ margin: 2rem 0 .9rem; font-size: .8125rem; font-weight: 600;
                 letter-spacing: .06em; text-transform: uppercase; color: var(--encre-3);
                 display: flex; align-items: center; gap: .6rem; }}
  .sous-titre .compte {{ text-transform: none; letter-spacing: 0; }}

  /* --- Cartes --- */
  .grille-cartes {{ display: grid; gap: 1rem;
                    grid-template-columns: repeat(auto-fill, minmax(min(20rem, 100%), 1fr)); }}
  .carte {{ background: var(--surface); border: 1px solid var(--trait); border-radius: 10px;
            padding: 1.1rem 1.2rem 1.2rem; display: flex; flex-direction: column; gap: .9rem; }}
  .carte-entete {{ display: flex; flex-wrap: wrap; align-items: center; gap: .4rem; }}
  .iid {{ font-size: .8125rem; font-weight: 600; text-decoration: none;
          font-variant-numeric: tabular-nums; }}
  .iid:hover {{ text-decoration: underline; }}
  .badge {{ font-size: .6875rem; color: var(--encre-2); border: 1px solid var(--trait);
            border-radius: 999px; padding: .1rem .5rem; }}
  .badge-type {{ color: var(--encre); border-color: var(--encre-3); }}
  .carte-titre {{ margin: 0; font-size: .9375rem; font-weight: 580; line-height: 1.45; }}
  .carte-resume {{ margin: 0; font-size: .875rem; line-height: 1.6; color: var(--encre-2); }}
  .vignette {{ display: block; border-radius: 6px; overflow: hidden;
               border: 1px solid var(--trait); line-height: 0; }}
  .vignette img {{ width: 100%; height: auto; display: block; }}

  /* --- Écrans touchés, sur la carte d'un ticket --- */
  .carte-ecrans {{ margin: 0; display: flex; flex-wrap: wrap; align-items: center;
                   gap: .35rem; font-size: .6875rem; }}
  .carte-ecrans-titre {{ color: var(--encre-3); text-transform: uppercase;
                         letter-spacing: .06em; font-weight: 600; margin-right: .15rem; }}
  .ecran-puce {{ color: var(--encre-2); text-decoration: none; background: var(--plan);
                 border: 1px solid var(--trait); border-radius: 999px; padding: .1rem .5rem; }}
  .ecran-puce:hover {{ color: var(--encre); border-color: var(--encre-3); }}

  /* --- Écrans touchés par la phase --- */
  .grille-ecrans {{ display: grid; gap: 1rem;
                    grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); }}
  .ecran {{ background: var(--surface); border: 1px solid var(--trait); border-radius: 10px;
            overflow: hidden; display: flex; flex-direction: column; }}
  .ecran-vue {{ display: block; line-height: 0; border-bottom: 1px solid var(--trait); }}
  .ecran-vue img {{ width: 100%; height: auto; display: block; }}
  .ecran-corps {{ padding: .9rem 1rem 1rem; display: flex; flex-direction: column; gap: .3rem; }}
  .ecran-titre {{ margin: 0; font-size: .9375rem; font-weight: 580; line-height: 1.4; }}
  .ecran-route {{ margin: 0; font-size: .75rem; color: var(--encre-3);
                  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
  .ecran-compte {{ margin: .15rem 0 0; font-size: .8125rem; color: var(--encre-2);
                   font-variant-numeric: tabular-nums; }}
  .ecran-aide {{ margin: .15rem 0 0; font-size: .75rem; color: var(--encre-3); line-height: 1.5; }}

  /* --- Démonstrations --- */
  /* `min(26rem, 100%)` et non `26rem` : une piste de grille ne descend JAMAIS sous son minimum,
     donc `minmax(26rem, …)` déborde de toute fenêtre plus étroite que 416 px — mesuré, 65 px de
     débordement horizontal à 390 px de large. Le `min()` laisse la piste se rabattre sur la
     largeur disponible. Même correction sur .galerie et .grille-cartes, qui portaient le défaut. */
  .grille-clips {{ display: grid; gap: 1.75rem;
                   grid-template-columns: repeat(auto-fit, minmax(min(26rem, 100%), 1fr)); }}
  .clip {{ margin: 0; }}
  .clip-video {{ width: 100%; height: auto; display: block; border-radius: 10px;
                 border: 1px solid var(--trait); background: #000; }}
  .clip-affiche {{ display: block; line-height: 0; }}
  .clip-affiche img {{ width: 100%; height: auto; display: block; border-radius: 10px;
                       border: 1px solid var(--trait); }}
  .clip-absente {{ margin: 0; aspect-ratio: 8 / 5; display: flex; align-items: center;
                   justify-content: center; text-align: center; padding: 1rem;
                   background: var(--surface); border: 1px dashed var(--encre-3);
                   border-radius: 10px; color: var(--encre-3); font-size: .8125rem; }}
  .clip-legende {{ margin: .6rem 0 0; font-size: .8125rem; color: var(--encre-2); }}
  .clip-note {{ margin: .35rem 0 0; font-size: .75rem; color: var(--encre-3); line-height: 1.5; }}

  /* --- Galerie --- */
  .galerie {{ display: grid; gap: 1.75rem;
              grid-template-columns: repeat(auto-fit, minmax(min(26rem, 100%), 1fr)); }}
  .figure {{ margin: 0; }}
  .figure img {{ width: 100%; height: auto; display: block; border-radius: 10px;
                 border: 1px solid var(--trait); }}
  .figure figcaption {{ margin: .6rem 0 0; font-size: .8125rem; color: var(--encre-2); }}

  /* --- Pied --- */
  .pied {{ margin: 4rem 0 0; padding-top: 1.5rem; border-top: 1px solid var(--filet);
           font-size: .8125rem; color: var(--encre-3); }}
  .pied ul {{ margin: .6rem 0 0; padding-left: 1.1rem; }}

{rampe_claire}
{ecarte}
  @media (prefers-color-scheme: dark) {{
    :root:where(:not([data-theme="clair"])) {{
{rampe_sombre}
    }}
  }}
  :root[data-theme="sombre"] {{
{rampe_sombre}
  }}

  @media print {{
    .barre {{ display: none; }}
    body {{ background: #fff; }}
    .section {{ break-inside: avoid; }}
  }}
"""


SCRIPT_THEME = """
(function () {
  var CLE = "maestro.presentation.theme";
  var racine = document.documentElement;
  var bouton = document.getElementById("bascule-theme");

  // Le thème RÉELLEMENT affiché : le choix explicite s'il y en a un, sinon la préférence
  // système. Le libellé du bouton doit annoncer ce qu'un clic va faire — sans ça, une page
  // ouverte sur un système en sombre proposerait « Thème sombre ».
  function effectif() {
    var pose = racine.getAttribute("data-theme");
    if (pose) return pose;
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "sombre" : "clair";
  }
  function etiqueter() {
    bouton.textContent = effectif() === "sombre" ? "Thème clair" : "Thème sombre";
  }
  function appliquer(choix) {
    if (choix) racine.setAttribute("data-theme", choix);
    else racine.removeAttribute("data-theme");
    etiqueter();
  }

  var memorise = null;
  try { memorise = localStorage.getItem(CLE); } catch (e) {}
  appliquer(memorise);

  // Tant qu'aucun choix n'est posé, la page suit l'OS : le libellé doit suivre aussi.
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", etiqueter);

  bouton.addEventListener("click", function () {
    var suivant = effectif() === "sombre" ? "clair" : "sombre";
    appliquer(suivant);
    try { localStorage.setItem(CLE, suivant); } catch (e) {}
  });
})();
"""


# --- Clips : encodage, plafonds, repli ------------------------------------------------------------


def resoudre(chemin_brut: str, racine: Path) -> Path:
    """Un chemin du JSON, relatif au fichier de données quand il n'est pas absolu."""
    chemin = Path(chemin_brut)
    return chemin if chemin.is_absolute() else racine / chemin


def preparer_clips(
    declares: list[dict[str, Any]], racine: Path, captures: dict[str, dict[str, str]]
) -> list[dict[str, Any]]:
    """
    Encode les clips et leur affiche de repli. Aucun plafond n'est appliqué ici : peser vient
    d'abord, trier vient après (`selectionner_clips`).

    Le poids retenu est celui de la **data URI**, pas celui du webm sur le disque : base64 coûte
    +33 %, et ce que les plafonds protègent est le fichier *produit*. Mesurer le webm ferait
    promettre 25 Mio pour en écrire 33.

    L'affiche se cherche dans cet ordre : celle que le JSON déclare, puis la capture de **même
    clé** — gratuite quand les clés coïncident (`couts`), absente quand elles divergent (les
    parcours de #545 ne portent pas les clés du menu). Sans aucune des deux, le repli reste
    **visible** : un cartouche qui nomme le clip, jamais un trou.
    """
    clips = []
    for entree in declares:
        cle = entree.get("cle") or ""
        libelle = entree.get("libelle") or cle or "Démonstration"

        affiche = None
        if entree.get("affiche"):
            affiche = image_en_data_uri(resoudre(entree["affiche"], racine))
        if affiche is None and cle in captures:
            affiche = captures[cle]["uri"]

        fichier = entree.get("fichier")
        if not fichier:
            # Un parcours en échec laisse sa ligne au manifeste avec `fichier: null` (#545) : il
            # n'y a rien à jouer, et rien à dire d'une démonstration qui n'a pas eu lieu.
            print(f"[build] ⚠ clip sans fichier ({libelle}) — ignoré", file=sys.stderr)
            continue
        uri = video_en_data_uri(resoudre(fichier, racine))
        if uri is None:
            continue

        clips.append(
            {
                "cle": cle or slugifier(libelle),
                "libelle": libelle,
                "uri": uri,
                "poids": len(uri),
                "affiche": affiche,
                "ecarte": None,
            }
        )
    return clips


def selectionner_clips(
    clips: list[dict[str, Any]], plafond_clip: float, budget: float
) -> list[str]:
    """
    Applique les deux plafonds et rend les motifs d'écart, pour le pied de page.

    Les clips sont examinés dans **l'ordre déclaré** : un clip lourd ne double pas la file parce
    qu'il est lourd, et deux générations sur les mêmes données écartent les mêmes clips. Un clip
    au-delà du plafond individuel ne consomme rien du budget — il n'entre pas.
    """
    motifs = []
    reste = budget
    for clip in clips:
        if clip["poids"] > plafond_clip:
            clip["ecarte"] = (
                f"{poids_texte(clip['poids'])} une fois encodé, au-delà du plafond de "
                f"{poids_texte(plafond_clip)} par clip"
            )
        elif clip["poids"] > reste:
            clip["ecarte"] = (
                f"le budget vidéo du fichier est épuisé — il restait "
                f"{poids_texte(max(reste, 0))} pour {poids_texte(clip['poids'])} demandés"
            )
        else:
            reste -= clip["poids"]
            continue
        motifs.append(f"« {clip['libelle']} » : {clip['ecarte']}")
    return motifs


# --- Assemblage -----------------------------------------------------------------------------------


def construire(donnees: dict[str, Any], racine_captures: Path) -> str:
    milestone = donnees["milestone"]
    base_url = donnees.get("projet", {}).get("url", "").rstrip("/")
    tickets: list[dict[str, Any]] = donnees.get("tickets", [])

    captures: dict[str, dict[str, str]] = {}
    for capture in donnees.get("captures", []):
        uri = image_en_data_uri(resoudre(capture["fichier"], racine_captures))
        if uri:
            captures[capture["cle"]] = {"libelle": capture["libelle"], "uri": uri}

    ecrans = preparer_ecrans(donnees.get("ecrans") or [], tickets)
    comptes_ecrans = compter_ecrans(tickets)
    clips = preparer_clips(donnees.get("videos") or [], racine_captures, captures)

    for ticket in tickets:
        ticket["_groupe"] = libelle_groupe(ticket.get("statut", ""))

    comptes = Counter(t["_groupe"] for t in tickets)
    par_type = Counter(t.get("type") or "-" for t in tickets)
    total = len(tickets)
    livres = comptes["Livré"] + comptes["En revue"]

    sections = []
    for nom, _, aide in GROUPES:
        lot = [t for t in tickets if t["_groupe"] == nom]
        if lot:
            sections.append(rendre_section(nom, aide, lot, base_url, captures, ecrans))

    dates = [d for d in (milestone.get("debut"), milestone.get("echeance")) if d]
    meta = [" → ".join(dates)] if dates else []
    meta.append("Milestone clos" if milestone.get("etat") == "closed" else "Milestone actif")
    ligne_meta = '<span class="sep">·</span>'.join(escape(m) for m in meta)

    resume = milestone.get("resume")
    bloc_resume = f'<p class="resume">{escape(resume)}</p>' if resume else ""

    titre = milestone["titre"]
    lien_projet = (
        f'<a href="{escape(base_url)}">{escape(base_url)}</a>' if base_url else "GitLab"
    )
    notes_donnees = list(donnees.get("notes") or [])

    def page(section_clips: str, notes: list[str]) -> str:
        bloc_notes = ""
        if notes:
            items = "".join(f"<li>{escape(n)}</li>" for n in notes)
            bloc_notes = f"<ul>{items}</ul>"
        return f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maestro — {escape(titre)}</title>
<style>{feuille_de_style()}</style>
</head>
<body>
<div class="page">

  <div class="barre">
    <button type="button" id="bascule-theme" class="bascule">Thème sombre</button>
  </div>

  <header class="couverture">
    <p class="surtitre">Maestro · Revue de milestone</p>
    <h1>{escape(titre)}</h1>
    <p class="meta">{ligne_meta}</p>
    {bloc_resume}

    <div class="avancement">
      <div class="heros">
        <p class="heros-valeur">{pourcent_texte(livres, total)}&#8239;%</p>
        <p class="heros-libelle">livré — {livres} ticket(s) sur {total}</p>
      </div>
      <div class="avancement-barre">
        {rendre_jauge(comptes, total)}
      </div>
    </div>

    {rendre_tuiles(comptes, total, par_type)}
  </header>

  {"".join(sections)}

  {rendre_ecrans(comptes_ecrans, ecrans, captures)}

  {section_clips}

  {rendre_galerie(captures)}

  <footer class="pied">
    <p>Généré le {date.today().isoformat()} depuis {lien_projet} — milestone
       « {escape(titre)} ».</p>
    {bloc_notes}
  </footer>

</div>
<script>{SCRIPT_THEME}</script>
</body>
</html>
"""

    if not clips:
        return page("", notes_donnees)

    # Première passe : la page SANS un seul clip. C'est elle qui dit ce qui reste sous le plafond
    # du fichier — mesuré, pas estimé. Sans ça, « plafond pour le fichier » ne serait qu'un plafond
    # sur les vidéos déguisé, faux dès que les captures pèsent.
    plafond_clip = plafond_octets("MAESTRO_PRESENTATION_VIDEO_MAX", VIDEO_MAX_MIO_DEFAUT)
    plafond_fichier = plafond_octets("MAESTRO_PRESENTATION_MAX", FICHIER_MAX_MIO_DEFAUT)
    sans_clips = len(page("", notes_donnees).encode("utf-8"))
    budget = (
        math.inf
        if plafond_fichier == math.inf
        else max(plafond_fichier - sans_clips - MARGE_OCTETS, 0)
    )

    motifs = selectionner_clips(clips, plafond_clip, budget)
    retenus = [c for c in clips if c["ecarte"] is None]
    print(
        f"[build] clips : {len(retenus)}/{len(clips)} intégré(s)"
        f" — par clip : {poids_texte(plafond_clip)}"
        f" · fichier : {poids_texte(plafond_fichier)}"
        f" (budget vidéo {poids_texte(budget)})",
        file=sys.stderr,
    )

    notes = list(notes_donnees)
    if motifs:
        # Le pied de page reprend ce que la section dit déjà en place : c'est là qu'un lecteur
        # cherche les réserves, et un clip absent doit se lire sans avoir à repérer son cartouche.
        notes.append(
            f"{len(motifs)} clip(s) sur {len(clips)} écartés pour tenir le plafond de taille "
            f"({poids_texte(plafond_clip)} par clip, {poids_texte(plafond_fichier)} pour le "
            f"fichier) — " + " ; ".join(motifs) + "."
        )
        for motif in motifs:
            print(f"[build] ⚠ clip écarté — {motif}", file=sys.stderr)

    return page(rendre_demonstrations(clips), notes)


def principal(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Génère la présentation HTML autonome d'un milestone.",
        epilog=f"Schéma attendu du JSON d'entrée :\n{SCHEMA}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    analyseur.add_argument("donnees", type=Path, help="fichier JSON décrivant le milestone")
    analyseur.add_argument(
        "--sortie",
        type=Path,
        default=None,
        help="fichier HTML à écrire (défaut : docs/presentations/<slug-du-milestone>.html)",
    )
    args = analyseur.parse_args(argv)

    try:
        donnees = json.loads(args.donnees.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as erreur:
        print(f"[build] données illisibles ({args.donnees}) : {erreur}", file=sys.stderr)
        return 1

    if "milestone" not in donnees or "titre" not in donnees.get("milestone", {}):
        print(
            f"[build] JSON incomplet : il faut au moins milestone.titre\n{SCHEMA}",
            file=sys.stderr,
        )
        return 2

    sortie = args.sortie
    if sortie is None:
        racine = Path(__file__).resolve().parents[2]
        slug = slugifier(donnees["milestone"]["titre"])
        sortie = racine / "docs" / "presentations" / f"{slug}.html"

    html = construire(donnees, racine_captures=args.donnees.resolve().parent)
    sortie.parent.mkdir(parents=True, exist_ok=True)
    sortie.write_text(html, encoding="utf-8")

    taille_ko = round(len(html.encode("utf-8")) / 1024)
    print(f"[build] présentation écrite : {sortie} ({taille_ko} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
