"""La planche de la relecture visuelle d'un ticket (#980, chantier #972).

Un fichier HTML **autonome** — CSS en ligne, captures en `data:`, aucune ressource externe — qui
montre, état par état et écran par écran, l'**avant** (`origin/main`) et l'**après** (la branche)
côte à côte dans chaque thème, avec le jugement de la relecture en tête.

    bash scripts/design/relecture-visuelle.sh --planche <iid>     # l'appelant normal

Pourquoi une planche : `gh` ne sait pas joindre une image à un commentaire. Le jugement consigné sur
le ticket (`lib.sh relecture-note`) reste donc du texte, et c'est ici qu'une personne VOIT ce que ce
texte juge. La planche n'est envoyée à aucune forge : elle vit dans `.maestro/relecture/<iid>/`, et
le skill `relecture-visuelle` la nomme dans le résumé de la session.

CE SCRIPT NE CHOISIT PAS LES CAPTURES. Il lit les paires que `relecture-visuelle.sh` dresse
(`paires.tsv`), seul endroit où le nom d'une capture se construit : la saisine du regard neuf et la
planche montrent ainsi les mêmes fichiers, par construction.

CE SCRIPT NE RECOPIE PAS LA PRÉSENTATION DE MILESTONE. Il en reprend la mécanique —
`scripts/presentation/build.py` : feuille de style et ses deux thèmes, encodage `data:`, plafond de
taille lu dans l'environnement, bascule de thème, visionneuse — par import, au lieu d'en écrire une
seconde qui dériverait.
"""

from __future__ import annotations

import argparse
import math
import re
import sys
from dataclasses import dataclass
from datetime import date
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "presentation"))

from build import (  # noqa: E402 — le voisin n'est pas un paquet : son dossier entre d'abord au chemin
    MARGE_OCTETS,
    SCRIPT_THEME,
    SCRIPT_VISIONNEUSE,
    VISIONNEUSE,
    attributs_agrandir,
    feuille_de_style,
    image_en_data_uri,
    plafond_octets,
    poids_texte,
)

#: Plafond du fichier produit, réglable (`MAESTRO_RELECTURE_PLANCHE_MAX`, en Mio ; `0` = aucun). Le
#: même que celui d'une présentation, et pour la même raison : une planche est faite pour être
#: ouverte, et au-delà de ce que prend une pièce jointe elle ne s'envoie plus à personne. Ordre de
#: grandeur : 3 écrans × 4 états × 2 thèmes × avant et après = 48 captures de ~300 Kio, ~19 Mio.
PLANCHE_MAX_MIO_DEFAUT = 25.0

#: Les jugements que la planche sait mettre en tête, dans l'ordre où elle les cherche : le jugement
#: complet de la session (grille recopiée comprise), sinon le seul regard neuf.
JUGEMENTS = ("jugement.md", "regard.md")

#: Les deux marqueurs de « pas de fichier » d'une paire — le contrat de `paires_de`.
ABSENT = "-"
NOUVEAU = "nouveau"


@dataclass
class Paire:
    """Une ligne de `paires.tsv` : même écran, même thème, même état — avant et après."""

    etat: str
    route: str
    cle: str
    theme: str
    apres: str
    avant: str

    def fichiers(self) -> list[str]:
        return [f for f in (self.avant, self.apres) if f not in (ABSENT, NOUVEAU)]


def lire_paires(chemin: Path) -> list[Paire]:
    """Les paires dressées par `relecture-visuelle.sh`, une par ligne, six colonnes."""
    paires: list[Paire] = []
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        if not ligne.strip() or ligne.startswith("#"):
            continue
        colonnes = ligne.split("\t")
        if len(colonnes) != 6:
            print(
                f"[planche] ⚠ ligne de paires ignorée (6 colonnes attendues) : {ligne}",
                file=sys.stderr,
            )
            continue
        paires.append(Paire(*colonnes))
    return paires


# --- Le jugement : un Markdown réduit à ce qu'une relecture écrit ---------------------------------
#
# Titres, paragraphes, listes, citations et TABLEAUX — la grille en est un. Pas de dépendance : le
# jugement est écrit par le skill, dans une forme connue, et un moteur Markdown complet ne rendrait
# rien de plus qu'on y trouve.


def en_ligne(texte: str) -> str:
    """Le balisage d'une ligne : `code`, **gras**, *italique* — sur un texte déjà échappé."""
    morceaux = re.split(r"(`[^`]+`)", texte)
    rendu = []
    for morceau in morceaux:
        if len(morceau) > 1 and morceau.startswith("`") and morceau.endswith("`"):
            rendu.append(f"<code>{escape(morceau[1:-1])}</code>")
            continue
        m = escape(morceau)
        m = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", m)
        m = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", m)
        rendu.append(m)
    return "".join(rendu)


def cellules(ligne: str) -> list[str]:
    brut = ligne.strip()
    if brut.startswith("|"):
        brut = brut[1:]
    if brut.endswith("|"):
        brut = brut[:-1]
    return [c.strip() for c in brut.split("|")]


def rendre_markdown(texte: str) -> str:
    lignes = texte.splitlines()
    blocs: list[str] = []
    i = 0
    while i < len(lignes):
        ligne = lignes[i].rstrip()
        if not ligne.strip():
            i += 1
            continue
        titre = re.match(r"^(#{1,6})\s+(.*)$", ligne)
        if titre:
            niveau = 3 if len(titre.group(1)) <= 3 else 4
            blocs.append(f"<h{niveau}>{en_ligne(titre.group(2))}</h{niveau}>")
            i += 1
            continue
        if ligne.lstrip().startswith("|"):
            rangees = []
            while i < len(lignes) and lignes[i].lstrip().startswith("|"):
                rangees.append(cellules(lignes[i]))
                i += 1
            entete, corps = rangees[0], rangees[1:]
            if corps and all(re.fullmatch(r":?-{2,}:?", c or "--") for c in corps[0]):
                corps = corps[1:]
            tete = "".join(f"<th>{en_ligne(c)}</th>" for c in entete)
            rangs = "".join(
                "<tr>"
                + "".join(
                    f'<td class="ko">{en_ligne(c)}</td>'
                    if c.startswith("✗")
                    else f"<td>{en_ligne(c)}</td>"
                    for c in rangee
                )
                + "</tr>"
                for rangee in corps
            )
            blocs.append(
                f'<div class="defile"><table><thead><tr>{tete}</tr></thead>'
                f"<tbody>{rangs}</tbody></table></div>"
            )
            continue
        if re.match(r"^\s*([-*]|\d+\.)\s+", ligne):
            ordonnee = bool(re.match(r"^\s*\d+\.", ligne))
            items = []
            while i < len(lignes) and re.match(r"^\s*([-*]|\d+\.)\s+", lignes[i]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lignes[i].rstrip()))
                i += 1
                # Une ligne suivante indentée prolonge l'item, comme en Markdown.
                while i < len(lignes) and lignes[i].startswith("  ") and lignes[i].strip():
                    items[-1] += " " + lignes[i].strip()
                    i += 1
            balise = "ol" if ordonnee else "ul"
            blocs.append(
                f"<{balise}>" + "".join(f"<li>{en_ligne(t)}</li>" for t in items) + f"</{balise}>"
            )
            continue
        if ligne.lstrip().startswith(">"):
            cite = []
            while i < len(lignes) and lignes[i].lstrip().startswith(">"):
                cite.append(re.sub(r"^\s*>\s?", "", lignes[i].rstrip()))
                i += 1
            blocs.append(f"<blockquote>{rendre_markdown(chr(10).join(cite))}</blockquote>")
            continue
        paragraphe = []
        while (
            i < len(lignes)
            and lignes[i].strip()
            and not re.match(r"^(#{1,6})\s|^\s*(\||>|[-*]\s|\d+\.\s)", lignes[i])
        ):
            paragraphe.append(lignes[i].strip())
            i += 1
        blocs.append(f"<p>{en_ligne(' '.join(paragraphe))}</p>")
    return "\n".join(blocs)


# --- Les paires -----------------------------------------------------------------------------------


def libelle_etat(etat: str) -> str:
    return f"État « {etat} »"


def rendre_cote(paire: Paire, cote: str, uris: dict[str, str], ecartees: dict[str, int]) -> str:
    """Un côté d'une paire : la capture, ou ce qui en tient lieu — nommé, jamais laissé en blanc."""
    fichier = paire.avant if cote == "avant" else paire.apres
    titre = "Avant — origin/main" if cote == "avant" else "Après — la branche"
    legende = f"{titre} · {paire.route} · {paire.theme} · {paire.etat}"
    if fichier == NOUVEAU:
        corps = (
            "<div class=\"absente\">Écran nouveau : absent d'origin/main, il n'a pas d'avant.</div>"
        )
    elif fichier == ABSENT:
        corps = '<div class="absente">Non capturé.</div>'
    elif fichier in ecartees:
        corps = (
            '<div class="absente">Capture écartée pour tenir le plafond de taille '
            f"({escape(poids_texte(ecartees[fichier]))}) : <code>{escape(fichier)}</code></div>"
        )
    elif fichier not in uris:
        corps = f'<div class="absente">Capture illisible : <code>{escape(fichier)}</code></div>'
    else:
        corps = (
            f'<button type="button" class="figure-vue"{attributs_agrandir(legende)}>'
            f'<img src="{uris[fichier]}" alt="{escape(legende, quote=True)}" loading="lazy">'
            "</button>"
        )
    return f'<figure class="figure">{corps}<figcaption>{escape(titre)}</figcaption></figure>'


def rendre_paires(
    paires: list[Paire], uris: dict[str, str], ecartees: dict[str, int]
) -> tuple[str, list[str]]:
    """Les sections par état. Rend aussi les états SANS aucune capture, pour le pied de page."""
    etats = list(dict.fromkeys(p.etat for p in paires))
    sections: list[str] = []
    vides: list[str] = []
    for etat in etats:
        du_etat = [p for p in paires if p.etat == etat]
        nb = sum(len(p.fichiers()) for p in du_etat)
        if nb == 0:
            vides.append(etat)
            continue
        ecrans = list(dict.fromkeys(p.route for p in du_etat))
        blocs = []
        for route in ecrans:
            themes = "".join(
                f'<p class="theme-titre">{escape(p.theme)}</p>'
                f'<div class="paire">{rendre_cote(p, "avant", uris, ecartees)}'
                f"{rendre_cote(p, 'apres', uris, ecartees)}</div>"
                for p in du_etat
                if p.route == route
            )
            blocs.append(f'<h3 class="ecran-titre"><code>{escape(route)}</code></h3>{themes}')
        sections.append(
            f"""
      <section class="section">
        <header class="section-entete">
          <h2>{escape(libelle_etat(etat))}<span class="compte compte-fort">{nb}</span></h2>
          <p class="aide">Avant à gauche, après à droite : même écran, même thème, même état.</p>
        </header>
        {"".join(blocs)}
      </section>"""
        )
    return "".join(sections), vides


STYLE_PLANCHE = """
  .jugement { margin: 2rem 0 0; line-height: 1.6; }
  .jugement p, .jugement ul, .jugement ol, .jugement blockquote { max-width: 72ch; }
  .jugement h3 { margin: 1.75rem 0 .5rem; font-size: 1.0625rem; font-weight: 620; }
  .jugement h4 { margin: 1.25rem 0 .4rem; font-size: .9375rem; font-weight: 620; }
  .jugement .defile { overflow-x: auto; }
  .jugement table { border-collapse: collapse; margin: .5rem 0 1rem; font-size: .875rem; }
  .jugement th, .jugement td { border-bottom: 1px solid var(--filet); padding: .4rem 1rem .4rem 0;
                               text-align: left; vertical-align: top; }
  .jugement th { color: var(--encre-3); font-weight: 500; font-size: .8125rem; }
  .jugement td.ko { font-weight: 650; }
  .jugement code { font-size: .875em; }
  .jugement blockquote { margin: .5rem 0; padding-left: .9rem; border-left: 2px solid var(--filet);
                         color: var(--encre-2); }
  .ecran-titre { margin: 2rem 0 0; font-size: 1rem; font-weight: 620; }
  .theme-titre { margin: 1rem 0 .5rem; font-size: .75rem; font-weight: 600; letter-spacing: .06em;
                 text-transform: uppercase; color: var(--encre-3); }
  .paire { display: grid; gap: 1rem;
           grid-template-columns: repeat(auto-fit, minmax(min(24rem, 100%), 1fr)); }
  .absente { display: flex; align-items: center; justify-content: center; min-height: 8rem;
             padding: 1rem; border: 1px dashed var(--filet); border-radius: 10px;
             color: var(--encre-3); font-size: .875rem; text-align: center; }
"""


def construire(
    iid: str, paires: list[Paire], jugement: tuple[str, str] | None
) -> tuple[str, list[str]]:
    """La page, et les notes de ce qu'elle n'a pas pu montrer (déjà écrites dans son pied)."""
    fichiers = list(dict.fromkeys(f for p in paires for f in p.fichiers()))
    # Lu UNE fois : une valeur illisible le dit sur stderr, et le dirait à chaque lecture.
    plafond = plafond_octets("MAESTRO_RELECTURE_PLANCHE_MAX", PLANCHE_MAX_MIO_DEFAUT)

    def page(uris: dict[str, str], ecartees: dict[str, int]) -> tuple[str, list[str]]:
        sections, vides = rendre_paires(paires, uris, ecartees)
        notes: list[str] = []
        if vides:
            notes.append(
                "Aucune capture pour : "
                + ", ".join(libelle_etat(e) for e in vides)
                + " — ce qui en dépend a été jugé « non vu »."
            )
        if ecartees:
            notes.append(
                f"{len(ecartees)} capture(s) écartée(s) pour tenir le plafond de taille "
                f"({poids_texte(plafond)}) : " + ", ".join(ecartees) + "."
            )
        if jugement:
            nom, texte = jugement
            bloc_jugement = (
                f'<section class="section"><header class="section-entete"><h2>Jugement</h2>'
                f'<p class="aide">Recopié de <code>{escape(nom)}</code> — le texte consigné sur '
                "le ticket.</p>"
                f'</header><div class="jugement">{rendre_markdown(texte)}</div></section>'
            )
        else:
            bloc_jugement = (
                '<section class="section"><header class="section-entete"><h2>Jugement</h2>'
                '<p class="aide">Aucun jugement écrit encore : la planche ne montre que les '
                "captures.</p>"
                "</header></section>"
            )
        nb = sum(len(p.fichiers()) for p in paires)
        pied = "".join(f"<li>{escape(n)}</li>" for n in notes)
        html = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Relecture visuelle #{escape(iid)}</title>
<style>{feuille_de_style()}{STYLE_PLANCHE}</style>
</head>
<body>
<div class="page">

  <div class="barre">
    <button type="button" id="bascule-theme" class="bascule">Thème sombre</button>
  </div>

  <header class="couverture">
    <p class="surtitre">Maestro · Relecture visuelle</p>
    <h1>Ticket #{escape(iid)}</h1>
    <p class="meta">Planche du {date.today().isoformat()}<span class="sep">·</span>
      {nb} capture(s)</p>
    <p class="resume">L'avant (origin/main) et l'après (la branche), côte à côte, avec le jugement
      rendu par le regard neuf. Planche de travail : elle n'est envoyée à aucune forge.</p>
  </header>

  {bloc_jugement}

  {sections}

  <footer class="pied">
    <p>Générée par <code>relecture-visuelle.sh --planche</code>.</p>
    {f"<ul>{pied}</ul>" if pied else ""}
  </footer>

</div>
{VISIONNEUSE}
<script>{SCRIPT_THEME}</script>
<script>{SCRIPT_VISIONNEUSE}</script>
</body>
</html>
"""
        return html, notes

    # Première passe SANS une image : c'est elle qui dit ce qui reste sous le plafond — mesuré, pas
    # estimé (même geste que les clips de la présentation). Puis les captures entrent dans l'ordre
    # des paires, l'état nominal d'abord ; celle qui ne tient plus est écartée et NOMMÉE, pas tue.
    sans_images, _ = page({}, {})
    budget = (
        math.inf
        if plafond == math.inf
        else max(plafond - len(sans_images.encode("utf-8")) - MARGE_OCTETS, 0)
    )
    uris: dict[str, str] = {}
    ecartees: dict[str, int] = {}
    for fichier in fichiers:
        uri = image_en_data_uri(Path(fichier))
        if uri is None:
            continue
        if len(uri) > budget:
            ecartees[fichier] = len(uri)
            continue
        uris[fichier] = uri
        budget -= len(uri)
    return page(uris, ecartees)


def principal(argv: list[str] | None = None) -> int:
    analyseur = argparse.ArgumentParser(
        description="Écrit la planche HTML de la relecture visuelle d'un ticket."
    )
    analyseur.add_argument("--iid", required=True, help="le ticket relu")
    analyseur.add_argument(
        "--paires", required=True, type=Path, help="paires.tsv dressé par relecture-visuelle.sh"
    )
    analyseur.add_argument(
        "--dossier", required=True, type=Path, help="le dossier de la relecture du ticket"
    )
    analyseur.add_argument("--sortie", required=True, type=Path, help="le fichier HTML à écrire")
    args = analyseur.parse_args(argv)

    try:
        paires = lire_paires(args.paires)
    except OSError as erreur:
        print(f"[planche] paires illisibles ({args.paires}) : {erreur}", file=sys.stderr)
        return 1
    if not any(p.fichiers() for p in paires):
        print("[planche] aucune capture : rien à mettre sur une planche.", file=sys.stderr)
        return 4

    jugement = None
    for nom in JUGEMENTS:
        chemin = args.dossier / nom
        if chemin.is_file() and chemin.stat().st_size > 0:
            jugement = (str(chemin).replace("\\", "/"), chemin.read_text(encoding="utf-8"))
            break

    html, notes = construire(args.iid, paires, jugement)
    args.sortie.parent.mkdir(parents=True, exist_ok=True)
    args.sortie.write_text(html, encoding="utf-8")
    for note in notes:
        print(f"[planche] ⚠ {note}", file=sys.stderr)
    poids = round(len(html.encode("utf-8")) / 1024)
    print(f"[planche] écrite : {args.sortie.as_posix()} ({poids} Ko)")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
