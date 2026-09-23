"""Le rapport d'un passage : `.maestro/scenarios/<horodatage>/` (#1148, docs/10 §8.5).

Deux fichiers, parce qu'il y a deux lecteurs et qu'ils ne demandent pas la même
chose :

- **`rapport.md`** — ce qu'un humain ouvre quand un scénario est rouge : le
  verdict, le motif, et le **déroulé** étape par étape. C'est ce qui évite de
  repayer un passage pour savoir où il s'est arrêté ;
- **`rapport.json`** — ce que `/milestone-bilan` relira (#1152, « un jalon produit
  ne se boucle pas GO avec un scénario rouge »). Une forme stable, parce qu'un
  bilan qui lirait le Markdown jugerait du texte par un motif, ce que le dépôt
  refuse (#746).

**Sous `.maestro/` et en chemin relatif** : la convention de #234 — ce qu'un
script invite à lire va là, jamais dans `${TMPDIR}`, où personne ne le retrouve.
Le dossier est horodaté, donc deux passages ne s'écrasent pas : comparer la même
suite avant et après un correctif est exactement ce qu'on veut pouvoir faire.
"""

from __future__ import annotations

import json
from pathlib import Path

from maestro.scenarios.modele import Rapport, Resultat

#: Où les rapports s'écrivent, **relatif au répertoire courant** (#234).
RACINE_RAPPORTS = Path(".maestro") / "scenarios"

#: Le rapport lisible, et le rapport relu par le bouclage d'un jalon.
FICHIER_MARKDOWN = "rapport.md"
FICHIER_JSON = "rapport.json"


def dossier_du_passage(horodatage: str, *, racine: Path | None = None) -> Path:
    """Le dossier d'un passage — créé s'il manque."""
    chemin = (racine or RACINE_RAPPORTS) / horodatage
    chemin.mkdir(parents=True, exist_ok=True)
    return chemin


def ecrire(rapport: Rapport, *, racine: Path | None = None) -> Path:
    """Écrit les deux formes du rapport et rend le dossier du passage."""
    dossier = dossier_du_passage(rapport.horodatage, racine=racine)
    (dossier / FICHIER_JSON).write_text(
        json.dumps(rapport.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (dossier / FICHIER_MARKDOWN).write_text(en_markdown(rapport), encoding="utf-8")
    return dossier


def en_markdown(rapport: Rapport) -> str:
    """Le rapport en Markdown : un tableau de tête, puis une section par scénario."""
    verdict = "✅ tous verts" if rapport.vert else "❌ au moins un rouge"
    lignes = [
        f"# Scénarios de référence — passage {rapport.horodatage}",
        "",
        f"**Verdict du passage : {verdict}**",
        "",
        f"- coût : {_cout(rapport.cout_usd)}",
        f"- durée : {_duree(rapport.duree_s)}",
        "",
        "| | Scénario | Verdict | Coût | Durée | Run |",
        "|---|---|---|---|---|---|",
    ]
    for resultat in rapport.resultats:
        lignes.append(
            f"| {resultat.identifiant} | {resultat.titre} | {_marque(resultat)} | "
            f"{_cout(resultat.cout_usd)} | {_duree(resultat.duree_s)} | "
            f"{resultat.run_id or '—'} |"
        )
    for resultat in rapport.resultats:
        lignes.extend(["", *_section(resultat)])
    return "\n".join(lignes) + "\n"


def _section(resultat: Resultat) -> list[str]:
    """La section d'un scénario : son verdict motivé, ses pièces, son déroulé."""
    lignes = [
        f"## {resultat.identifiant} — {resultat.titre}",
        "",
        f"**{_marque(resultat)}** — {resultat.motif}",
        "",
    ]
    if resultat.rejoue:
        lignes.extend(
            [
                "> Rejoué une fois : ce scénario n'est pas déterministe, et un premier "
                "rouge ne se croit pas sans être rejoué.",
                "",
            ]
        )
    if resultat.empechement:
        lignes.extend(
            [
                "> Empêchement : le banc n'a pas pu mener ce scénario jusqu'à son "
                "oracle. Rouge quand même — rien n'a été vérifié —, mais cela ne se "
                "répare pas au même endroit qu'un défaut du produit.",
                "",
            ]
        )
    lignes.extend(
        [
            f"- run : `{resultat.run_id or '—'}`",
            f"- projet : `{resultat.projet_id or '—'}`",
            f"- racine : `{resultat.racine or '—'}`",
            f"- coût : {_cout(resultat.cout_usd)} · durée : {_duree(resultat.duree_s)}",
            f"- {_arbitrages(resultat)}",
            "",
            "### Déroulé",
            "",
        ]
    )
    if not resultat.etapes:
        lignes.append("_aucune étape consignée._")
        return lignes
    lignes.extend(
        f"{rang}. **{etape.libelle}**{f' — {etape.detail}' if etape.detail else ''}"
        for rang, etape in enumerate(resultat.etapes, start=1)
    )
    return lignes


def _arbitrages(resultat: Resultat) -> str:
    """Ce que le banc a tranché à la place de la personne, **et combien de commandes**.

    Une ligne dans chaque section, y compris quand il n'y a rien : « aucun »
    est le fait qu'on vient vérifier depuis #1226, et une ligne absente se lirait
    comme une ligne qu'on a oublié d'écrire. Le compte des **commandes** est
    donné à part, parce que c'est celui-là qui dit si l'équipe travaille sans
    déranger personne — une validation de tâche, elle, est attendue.
    """
    total = len(resultat.arbitrages)
    if not total:
        return "arbitrages tranchés par le banc : aucun"
    commandes = resultat.validations_de_commande
    return (
        f"arbitrages tranchés par le banc : {total}, dont "
        f"{commandes} validation(s) de commande"
    )


def _marque(resultat: Resultat) -> str:
    """Le verdict d'un scénario, tel qu'on le lit dans un tableau."""
    return "✅ vert" if resultat.vert else "❌ rouge"


def _cout(montant: float | None) -> str:
    """Un montant en mots du produit, ou le fait qu'aucun n'a été rapporté.

    La virgule décimale et le symbole suivent `formatCout` (`apps/web/lib/format`)
    et `bornes._cout` : le même passage lu dans un rapport et dans l'UI ne doit pas
    afficher deux montants d'apparence différente (règle de #571).
    """
    if montant is None:
        return "non rapporté"
    return f"{montant:.4f}".replace(".", ",") + " $"


def _duree(secondes: float) -> str:
    """Une durée en mots : « 42 s » sous la minute, « 3 min 12 s » au-delà."""
    entier = int(round(secondes))
    if entier < 60:
        return f"{entier} s"
    return f"{entier // 60} min {entier % 60:02d} s"
