"""Génère la présentation HTML d'un milestone (#142).

Entrée : un fichier JSON décrivant le milestone, ses tickets et les captures
disponibles (voir `SCHEMA` plus bas et la commande `/milestone-presentation`).
Sortie : un fichier HTML **autonome** — CSS en ligne, images en `data:`, aucune
ressource externe — lisible en thème clair comme sombre.

    .venv/Scripts/python.exe scripts/presentation/build.py <donnees.json> [--sortie <fichier.html>]

Le gabarit vit ici, pas dans le prompt de la commande : c'est ce qui rend le
rendu stable d'une génération à l'autre. La commande fournit la matière (quels
tickets, quel résumé, quelle capture illustre quoi) ; ce script décide de la
forme.
"""

from __future__ import annotations

import argparse
import base64
import json
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
                 "capture": str|null}],            # `capture` = clé d'une entrée de `captures`
  "captures":  [{"cle": str, "libelle": str, "fichier": str}],
  "notes":     [str]                               # avertissements affichés en pied
}
"""

# --- Vocabulaire ---------------------------------------------------------------------------------

# Les états du cycle de vie (labels `workflow::*`, docs/10-workflow-git.md §3.1), regroupés pour la
# lecture : un lecteur de présentation veut savoir ce qui est acquis, ce qui bouge et ce qui reste —
# pas le détail du cycle de vie. « En revue » garde sa case à lui : le travail est fait, le merge ne
# l'est pas.
#
# Ce sont les LIBELLÉS qui sont attendus ici, pas les slugs des labels : `lib.sh milestone-issues`
# ne rend jamais autre chose (contrat de surface en tête de lib.sh), et un slug qui arriverait
# jusqu'ici tomberait dans « À venir » via libelle_groupe.
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


def libelle_groupe(statut: str) -> str:
    """Le groupe d'affichage d'un état du cycle de vie ; tout état inconnu tombe dans « À venir ».

    Le repli couvre notamment le « - » que rendent les helpers pour un ticket ne portant AUCUN
    label `workflow::` — cas devenu possible depuis #207 (un ticket ouvert à la main dans
    l'interface web n'en reçoit pas), là où le champ Status natif en avait toujours un.
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


def rendre_carte(ticket: dict[str, Any], base_url: str, captures: dict[str, dict[str, str]]) -> str:
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
          </div>
          {vignette}
        </article>"""


def rendre_section(
    nom: str,
    aide: str,
    tickets: list[dict[str, Any]],
    base_url: str,
    captures: dict[str, dict[str, str]],
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
        cartes = "".join(rendre_carte(t, base_url, captures) for t in lot)
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
                    grid-template-columns: repeat(auto-fill, minmax(20rem, 1fr)); }}
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

  /* --- Galerie --- */
  .galerie {{ display: grid; gap: 1.75rem;
              grid-template-columns: repeat(auto-fit, minmax(26rem, 1fr)); }}
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


# --- Assemblage -----------------------------------------------------------------------------------


def construire(donnees: dict[str, Any], racine_captures: Path) -> str:
    milestone = donnees["milestone"]
    base_url = donnees.get("projet", {}).get("url", "").rstrip("/")
    tickets: list[dict[str, Any]] = donnees.get("tickets", [])

    captures: dict[str, dict[str, str]] = {}
    for capture in donnees.get("captures", []):
        chemin = Path(capture["fichier"])
        if not chemin.is_absolute():
            chemin = racine_captures / chemin
        uri = image_en_data_uri(chemin)
        if uri:
            captures[capture["cle"]] = {"libelle": capture["libelle"], "uri": uri}

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
            sections.append(rendre_section(nom, aide, lot, base_url, captures))

    dates = [d for d in (milestone.get("debut"), milestone.get("echeance")) if d]
    meta = [" → ".join(dates)] if dates else []
    meta.append("Milestone clos" if milestone.get("etat") == "closed" else "Milestone actif")
    ligne_meta = '<span class="sep">·</span>'.join(escape(m) for m in meta)

    resume = milestone.get("resume")
    bloc_resume = f'<p class="resume">{escape(resume)}</p>' if resume else ""

    notes = donnees.get("notes") or []
    bloc_notes = ""
    if notes:
        items = "".join(f"<li>{escape(n)}</li>" for n in notes)
        bloc_notes = f"<ul>{items}</ul>"

    titre = milestone["titre"]
    lien_projet = (
        f'<a href="{escape(base_url)}">{escape(base_url)}</a>' if base_url else "GitLab"
    )

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
