"""Tests de la boucle d'orchestration autonome — `scripts/orchestrate/` (tickets #172 et #175).

Tests différés des lots #168 à #171 (parent #167) puis #176-#177 (parent #174), réunis ici selon
la convention de découpage (`docs/10-workflow-git.md` §5.1).

**Ni réseau, ni quota, ni écriture côté forge.** Trois bouchons posés en tête de `PATH` ou par
variable d'environnement remplacent tout ce qui sortirait de la machine :

* `gh` — un script qui répond aux quelques requêtes GraphQL que `scripts/gitlab/lib.sh` émet
  (milestones, backlog, vue d'un ticket, PR d'une branche), à partir d'un **état écrit par le
  test** dans un dossier de fixtures. Aucune requête ne part.
* `claude` — via `MAESTRO_CLAUDE_BIN` : un script qui joue le scénario voulu (succès, limite
  d'usage, reprise) et **ne consomme aucun quota**.
* le montage de worktree — via `MAESTRO_ORCHESTRATE_WORKTREE` : une commande qui imprime un
  dossier déjà là, donc **aucune branche ni aucun worktree réels** ne sont créés.
* l'ouverture d'une console — via `MAESTRO_ORCHESTRATE_SPAWN` (#173) : une commande qui reçoit le
  lanceur au lieu qu'une vraie fenêtre s'ouvre, donc **aucune console** ne surgit pendant les tests.

**Un dépôt jetable.** Chaque test monte dans `tmp_path` un mini-clone qui ne porte que les
scripts visés, `scripts/gitlab/lib.sh` et un `.claude/settings.json` synthétique. Le vrai
dépôt n'est jamais touché : `HOME` et `TMPDIR` sont eux aussi redirigés.

`status.sh` (#177) lit en plus **le dépôt lui-même** (branche, worktree, commits) : les rares
tests qui portent là-dessus initialisent un vrai dépôt git dans `tmp_path` — toujours en local,
sans `origin` distant, avec une simple référence `refs/remotes/origin/main` posée à la main.
"""

from __future__ import annotations

import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from conftest import CLE_COULEUR_ORCHESTRATE  # le conftest du dossier, sur le sys.path de pytest

RACINE = Path(__file__).resolve().parent.parent
BASH = shutil.which("bash")
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(BASH is None, reason="bash introuvable")
besoin_git = pytest.mark.skipif(GIT is None, reason="git introuvable")

# Les scripts sous test, recopiés tels quels dans le dépôt jetable.
SCRIPTS = (
    "scripts/gitlab/lib.sh",
    "scripts/orchestrate/queue.sh",
    "scripts/orchestrate/guard.sh",
    "scripts/orchestrate/run.sh",
    "scripts/orchestrate/status.sh",
    "scripts/orchestrate/pilote.sh",
    "scripts/orchestrate/journal.sh",
    "scripts/orchestrate/settings.run.json",
)

# Le bouchon `gh`. Il ne cherche pas à imiter GitHub : il répond au strict nécessaire, en lisant
# des fichiers que le test a écrits. Le dispatch se fait sur des fragments de la requête GraphQL
# telle que lib.sh la compose — si lib.sh change de requête, ces tests le diront.
STUB_GH = r"""#!/usr/bin/env bash
FIX="$MAESTRO_FIXTURES"
# Tout appel est journalisé : c'est ce qui permet de vérifier qu'une option comme `--no-forge`
# n'interroge VRAIMENT rien, plutôt que de se contenter du message qu'elle imprime.
printf '%s\n' "$*" >> "$FIX/gh.log"
case "$1 $2" in
  "auth status") exit 0 ;;
  # La lecture LOCALE du jeton, par laquelle `gh_require` remplace depuis #602 le `gh auth status`
  # qui coûtait un aller réseau. Les deux formes sont servies : l'ancienne vit encore dans
  # setup.sh, env-pull.sh et les deux bootstrap.
  "auth token") echo "gho_jeton-de-test"; exit 0 ;;
esac
if [ "$1" = "api" ] && [ "$2" = "graphql" ]; then
  requete="$*"
  case "$requete" in
    # Les tickets d'un jalon, désigné par son NUMÉRO : la seconde lecture de `milestone-issues`,
    # qui résout d'abord le titre (GitHub ne filtre pas un jalon par son titre).
    *"milestone(number:"*)
      numero="${requete#*milestone(number: }"; numero="${numero%%)*}"
      if [ -f "$FIX/milestone-issues-$numero.json" ]; then
        cat "$FIX/milestone-issues-$numero.json"
      else
        cat "$FIX/milestone-issues.json" 2>/dev/null
      fi
      exit 0 ;;
    # DEUX lectures de jalons, deux formes de réponse — GraphQL rend les clés dans l'ordre où la
    # requête les demande, et les parsers de lib.sh découpent dessus. Celle qui résout un titre en
    # NUMÉRO (première moitié de `milestone-issues`) demande « number title » ; les autres
    # (`milestones`, `current-milestone`) demandent « title » d'abord.
    *"nodes { number title }"*) cat "$FIX/milestones-numeros.json" 2>/dev/null; exit 0 ;;
    *"milestones("*)            cat "$FIX/milestones.json" 2>/dev/null; exit 0 ;;
    # LA CARTE DES ÉTATS (#365) : deux lectures, le projet résolu par son TITRE puis sa page
    # d'items. Elles passent AVANT `issues(first:` — la requête du backlog ne les capterait pas,
    # mais l'ordre dit lequel des deux jeux de fixtures porte l'état.
    *"projectsV2(first:100){nodes{ id title }}"*) cat "$FIX/projets.json" 2>/dev/null; exit 0 ;;
    *"items(first:100"*)                          cat "$FIX/carte.json" 2>/dev/null; exit 0 ;;
    # La pose d'un état : le verbe ne lit que la présence de `projectV2Item` dans la réponse.
    *"updateProjectV2ItemFieldValue"*)
      printf '{"data":{"updateProjectV2ItemFieldValue":{"projectV2Item":{"id":"PVTI_pose"}}}}'
      exit 0 ;;
    *"issues(first:"*) cat "$FIX/backlog.json" 2>/dev/null; exit 0 ;;
    *"pullRequests("*)
      # La PR d'UNE branche : le nom du fichier de fixture aplatit ses « / » (comme côté Python).
      # Une branche sans fixture n'a pas de PR du tout — c'est ce qui distingue un ticket livré
      # d'un ticket dont la session n'a rien clos.
      case "$requete" in
        # Forme GROUPÉE (#602) : N branches sous les alias `b0:`, `b1:`… Chaque alias reprend les
        # nœuds de la fixture de SA branche — c'est le RANG qui fait le lien, comme en production,
        # un nom de branche portant des « / » qu'un alias GraphQL n'accepte pas. Testée AVANT la
        # forme unitaire, dont le motif est plus large et l'absorberait.
        *": pullRequests(headRefName:"*)
          sep=': pullRequests(headRefName: "'
          reste="$requete"; corps=""
          while :; do
            case "$reste" in *"$sep"*) ;; *) break ;; esac
            tete="${reste%%"$sep"*}"; alias="${tete##* }"
            reste="${reste#*"$sep"}"; branche="${reste%%\"*}"
            noeuds="[]"
            fixture="$FIX/mr-${branche//\//__}.json"
            if [ -f "$fixture" ]; then
              contenu="$(cat "$fixture")"
              # Découpage par expansion et non par sed : `closingIssuesReferences` porte un second
              # « nodes », qu'un motif gourmand prendrait pour celui de la PR.
              noeuds="${contenu#*'"pullRequests":{"nodes":'}"
              noeuds="${noeuds%'}}}}'}"
            fi
            corps="$corps${corps:+,}\"$alias\":{\"nodes\":$noeuds}"
          done
          printf '{"data":{"repository":{%s}}}' "$corps"
          exit 0 ;;
        *"headRefName:"*)
          branche="${requete#*headRefName: \"}"; branche="${branche%%\"*}"
          if [ -f "$FIX/mr-${branche//\//__}.json" ]; then
            cat "$FIX/mr-${branche//\//__}.json"
          else
            printf '{"data":{"repository":{"pullRequests":{"nodes":[]}}}}'
          fi
          exit 0 ;;
      esac
      cat "$FIX/mr-iid.json" 2>/dev/null; exit 0 ;;
    *"issue(number:"*)
      iid="${requete#*issue(number:}"; iid="${iid%%)*}"
      # La VUE CANONIQUE d'un ticket est décrite par le test dans son format de SORTIE (en-têtes
      # « clé:<TAB>valeur », « -- », corps) : un helper Python la retraduit en JSON, parce qu'un
      # bouchon shell n'a pas à porter un encodeur — les titres et les corps portent des accents.
      case "$requete" in
        *"body }"*)
          [ -f "$FIX/issue-$iid.txt" ] || exit 1
          exec "$MAESTRO_STUB_PYTHON" "$FIX/vue_en_json.py" "$FIX/issue-$iid.txt" ;;
      esac
      # Le contexte du ticket, DÉJÀ APLATI (cf. `_statut_json`). Un ticket sans fixture est
      # introuvable : `st_contexte` reconnaît cette ligne-là et refuse franchement, au lieu de
      # rendre zéro ligne — que l'appelant lirait « ticket sans état », c'est-à-dire un feu vert.
      if [ -f "$FIX/owner-$iid.json" ]; then cat "$FIX/owner-$iid.json"; else
        printf 'erreur\tticket\n'
      fi
      exit 0 ;;
  esac
  exit 1
fi
# --- REST : ce dont le MERGE a besoin (#414, chantier #413) -----------------------------------
# Deux endpoints, et pas un de plus : le dernier run Actions d'une branche (`gh_pipeline_latest`,
# #416) et le PUT qui merge (#415). Jusqu'ici le bouchon rendait 1 sur tout `api repos/…`, ce qui
# suffisait à observer un REFUS de merge — la PR de la fixture était en brouillon, donc `merge-mr`
# s'arrêtait à son premier prérequis. Un merge qui RÉUSSIT demande d'aller jusqu'au bout, d'où ces
# deux réponses ; c'est ce que #419 avait laissé au lot 7.
if [ "$1" = "api" ]; then
  requete="$*"
  case "$requete" in
    *"-X PUT"*"/merge"*)
      # La BARRIÈRE de sérialisation, quand un test en pose une : chaque écrivain dépose SON
      # relevé puis regarde s'il en trouve un autre. Un fichier par écrivain, jamais un compteur
      # partagé — c'est la leçon de #313, dont le compteur perdait l'incrémentation que la
      # barrière rend justement probable.
      if [ -n "$MAESTRO_STUB_BARRIERE" ]; then
        pr="${requete#*pulls/}"; pr="${pr%%/merge*}"
        mkdir -p "$MAESTRO_STUB_BARRIERE"
        : > "$MAESTRO_STUB_BARRIERE/$pr.en-vol"
        attendu=0
        while [ "$attendu" -lt "${MAESTRO_STUB_BARRIERE_DELAI:-3}" ]; do
          pairs=$(find "$MAESTRO_STUB_BARRIERE" -name '*.en-vol' | wc -l | tr -d ' ')
          if [ "$pairs" -gt 1 ]; then break; fi
          sleep 1
          attendu=$((attendu + 1))
        done
        # Le relevé de CET écrivain : combien d'écrivains il a vus en vol, lui compris. Le pic se
        # prend après coup, sur l'ensemble des relevés — aucun fichier n'a deux auteurs.
        printf '%s\n' "$pairs" > "$MAESTRO_STUB_BARRIERE/$pr.vus"
        rm -f "$MAESTRO_STUB_BARRIERE/$pr.en-vol"
      fi
      if [ -f "$FIX/merge-refuse" ]; then printf '{"message":"refus simule"}'; exit 0; fi
      # La PR passe MERGED dans sa fixture (#438). Sans ça, tout ce qui relit son état APRÈS le
      # merge — la purge des branches locales, qui exige que la forge confirme — verrait une PR
      # encore ouverte et se tairait : le harnais mentirait sur la seule chose que ces tests-là
      # observent, et un ramassage qui ne ramasse pas passerait pour un ramassage.
      pr="${requete#*pulls/}"; pr="${pr%%/merge*}"
      for fixture in "$FIX"/mr-*.json; do
        [ -e "$fixture" ] || continue
        grep -q "\"number\":$pr," "$fixture" || continue
        sed -i 's/"state":"OPEN"/"state":"MERGED"/' "$fixture"
      done
      printf '{"merged":true,"sha":"deadbeef"}'
      exit 0 ;;
    *"-X POST"*"/labels"*)
      # La pose d'un label (#562, `gh_add_label`). Le bouchon l'écrit dans la fixture du ticket,
      # sur sa ligne `labels:` — sans quoi `arbitre` puis `--non-arbitres` ne pourraient pas se
      # lire l'un l'autre, et le seul aller-retour qui compte (« l'arbitrage enregistré fait taire
      # le signalement ») ne serait pas observable.
      iid="${requete#*issues/}"; iid="${iid%%/labels*}"
      label="${requete#*labels[]=}"; label="${label%% *}"
      [ -f "$FIX/issue-$iid.txt" ] || exit 1
      grep -q "^labels:.*$label" "$FIX/issue-$iid.txt" ||
        sed -i "s/^\\(labels:.*\\)$/\\1, $label/" "$FIX/issue-$iid.txt"
      printf '[]'
      exit 0 ;;
    *"actions/runs?branch="*)
      branche="${requete#*actions/runs?branch=}"; branche="${branche%%&*}"
      if [ -f "$FIX/run-${branche//\//__}.json" ]; then
        cat "$FIX/run-${branche//\//__}.json"
      else
        printf '{"workflow_runs":[]}'
      fi
      exit 0 ;;
  esac
fi
exit 1
"""

# ⚠ IL REND AUSSI LE DÉCOUPAGE NATIF (#390, allumé par défaut depuis #393) : les lignes d'en-tête
# « parent: » et « lot: » d'une vue écrite par un test redeviennent `Issue.parent` et
# `Issue.subIssues`. Sans elles, `queue.sh` ne verrait plus aucun parent — donc plus aucun lot —,
# et la moitié des tests du plan seraient verts sur un backlog sans découpage.
#
# L'ORDRE DES CLÉS EST DU CONTRAT, pas de la mise en forme : `gh_lots_natifs` borne le bloc des lots
# par le champ SUIVANT (`]},"body":`) et le titre d'un lot par `","state":`. Même raison, mêmes
# règles et mêmes mots que `vue_texte_en_json` dans tests/harnais_forge.py — deux doubles à tenir
# d'accord, dont ni l'un ni l'autre ne peut être supprimé (ce dépôt-ci est jetable et sans mémoire,
# celui de l'autre suite tient une séquence).
VUE_EN_JSON = "\n".join([
    "# Rend, au format que lit `gh_issue_raw`, la vue canonique d'un ticket écrite par un test.",
    "import json",
    "import sys",
    "",
    'texte = open(sys.argv[1], encoding="utf-8").read()',
    'entete, _, corps = texte.partition("\\n--\\n")',
    "champs = {}",
    "lots = []",
    "for ligne in entete.splitlines():",
    '    cle, _, valeur = ligne.partition(":\\t")',
    '    if cle == "lot":',
    '        lots.append(valeur.split("\\t"))',
    "        continue",
    "    champs[cle] = valeur",
    "",
    "",
    "def nodes(cle, brut):",
    '    return {"nodes": [{cle: v.strip()} for v in brut.split(",") if v.strip()]}',
    "",
    "",
    "issue = {",
    '    "title": champs.get("title", ""),',
    '    "state": "CLOSED" if champs.get("state") == "closed" else "OPEN",',
    '    "author": {"login": champs.get("author", "MaestroAgents")},',
    '    "labels": nodes("name", champs.get("labels", "")),',
    '    "assignees": nodes("login", champs.get("assignees", "")),',
    '    "milestone": {"jalon": champs.get("milestone", "")},',
    "}",
    'if "parent" in champs:',
    '    issue["parent"] = {"pnum": int(champs["parent"])} if champs["parent"] else None',
    "if lots:",
    '    issue["lots"] = {"nodes": [',
    "        {",
    '            "number": int(iid),',
    '            "title": titre,',
    '            "state": "CLOSED" if coche == "x" else "OPEN",',
    '            "labels": {"nodes": [{"name": "lot::parallele"}] if par == "\\u2225" else []},',
    "        }",
    "        for iid, coche, par, titre in lots",
    "    ]}",
    'issue["body"] = corps.rstrip("\\n")',
    'sys.stdout.buffer.write(json.dumps({"data": {"repository": {"issue": issue}}},',
    '    separators=(",", ":"), ensure_ascii=False).encode("utf-8"))',
    "",
])

# Le bouchon de montage de worktree : il imprime un dossier qui existe déjà, sans rien créer.
STUB_WORKTREE = """#!/usr/bin/env bash
printf '%s\\n' "$MAESTRO_STUB_WORKTREE_DIR"
"""


# ================================================================================================
# LE CYCLE DE VIE VIT DANS LE CHAMP STATUS D'UN PROJET (#365, chantier #358)
# ================================================================================================
# Ces bouchons ont porté trois supports : le champ Status natif de GitLab, six labels `workflow::*`
# (#209), et depuis #365 le champ **Status** d'un projet GitHub Projects v2. Les tests, eux, n'ont
# jamais bougé : ils écrivent et attendent le LIBELLÉ (« En revue »), qui est le contrat de surface
# documenté en tête de scripts/gitlab/lib.sh.
#
# ⚠ LES LECTURES DU BACKEND STATUS PASSENT PAR `gh api graphql --jq`, où le programme jq fait tout
# l'aplatissement — et le bouchon `gh` ne l'exécute pas. Les fixtures portent donc le résultat DÉJÀ
# APLATI : des lignes `clé<TAB>…` copiées des en-têtes de `st_jq_contexte` et `st_jq_items`. Une
# fixture qui rendrait du JSON ici traverserait le filtre en silence et le verbe lirait zéro
# ligne — c'est-à-dire « ticket sans état », un feu vert sur une question jamais posée.
#
# Les identifiants sont inventés : lib.sh les résout PAR NOM à chaque appel et n'en code aucun en
# dur (contrat en tête du fichier).
_PROJET = "Maestro"
_ID_PROJET = "PVT_projet"
_ID_CHAMP = "PVTSSF_status"
_LIBELLES_WORKFLOW = ("À faire", "En cours", "En revue", "Terminé", "Abandonné", "Doublon")


def _statut_json(iid: str, statut: str, assigne: str = "") -> str:
    """Le contexte d'UN ticket, aplati — ce que `st_contexte` lit pour `gl_issue_owner`.

    Le nom est resté celui du temps où c'était du JSON : les ~30 appels qui l'écrivent dans une
    fixture n'ont pas eu à changer, ce qui est exactement ce que le contrat de surface promet.
    """
    lignes = [f"ticket\t{iid}"]
    if assigne:
        lignes.append(f"assigne\t{assigne}")
    lignes.append(f"item\t{_PROJET}\t{_ID_PROJET}\tPVTI_{iid}\t{statut}")
    lignes.append(f"projet\t{_PROJET}\t{_ID_PROJET}\t{_ID_CHAMP}")
    lignes += [
        f"option\t{_PROJET}\t{_ID_PROJET}_opt{i}\t{libelle}"
        for i, libelle in enumerate(_LIBELLES_WORKFLOW)
    ]
    return "\n".join(lignes) + "\n"


@dataclass
class Depot:
    """Un dépôt jetable, ses bouchons et de quoi lancer les scripts dessus."""

    racine: Path
    fixtures: Path
    env: dict[str, str]
    tickets: dict[str, dict] = field(default_factory=dict)
    numeros_jalons: dict[str, int] = field(default_factory=dict)

    # --- Mise en place de l'état GitLab simulé ---------------------------------------------------
    def milestone(self, titre: str) -> None:
        self.milestones([(titre, "active", 3, 10)])

    def milestones(self, jalons: list[tuple]) -> None:
        """La table des milestones du projet : (titre, état, fermés, total[, rail]) chacun.

        Les dates sont fixes : `gl_current_milestone` trie déjà côté API (`sort: DUE_DATE_ASC`) et
        le bouchon rend les nœuds dans l'ordre où on les écrit — c'est donc cet ordre-là qui fait
        foi dans les tests, pas les dates.

        Le 5e champ est le RAIL (#617) et il est FACULTATIF : sans lui, la description du jalon est
        vide, donc son rail est « produit » — le défaut du dépôt. Ce n'est pas une commodité
        d'écriture, c'est le contrat qu'on veut garder testé : un jalon non marqué reste du produit,
        et les dizaines d'appels existants du harnais le vérifient sans une ligne de plus.
        """
        def _noeud(t: str, etat: str, fermes: int, total: int, rail: str) -> str:
            marqueur = "rail: outillage" if rail == "outillage" else ""
            return (
                f'{{"title":"{t}","description":"{marqueur}",'
                f'"state":"{"OPEN" if etat == "active" else "CLOSED"}",'
                f'"dueOn":"2026-12-31T00:00:00Z",'
                f'"total":{{"totalCount":{total}}},"fermes":{{"totalCount":{fermes}}}}}'
            )

        noeuds = ",".join(
            _noeud(t, etat, fermes, total, rail[0] if rail else "produit")
            for t, etat, fermes, total, *rail in jalons
        )
        (self.fixtures / "milestones.json").write_text(
            f'{{"data":{{"repository":{{"milestones":{{"nodes":[{noeuds}]}}}}}}}}',
            encoding="utf-8",
        )
        # La SECONDE forme : « number title », celle que lit la résolution d'un titre en numéro.
        # GraphQL rend les clés dans l'ordre demandé et les parsers de lib.sh découpent dessus —
        # une seule fixture pour les deux requêtes ne pourrait donc pas convenir aux deux.
        numeros = ",".join(
            f'{{"number":{numero},"title":"{jalon[0]}"}}'
            for numero, jalon in enumerate(jalons, 1)
        )
        (self.fixtures / "milestones-numeros.json").write_text(
            f'{{"data":{{"repository":{{"milestones":{{"nodes":[{numeros}]}}}}}}}}',
            encoding="utf-8",
        )
        self.numeros_jalons = {jalon[0]: n for n, jalon in enumerate(jalons, 1)}

    def milestone_tickets(self, titre: str, iids: list[int]) -> None:
        """Les tickets d'UN milestone donné (les autres gardent la table de `publie`).

        Le bouchon `gh` retrouve ce fichier par le NUMÉRO du jalon, parce que c'est ce que porte la
        seconde lecture de `milestone-issues` : GitHub ne filtre pas un jalon par son titre, il faut
        d'abord le résoudre. La correspondance titre → numéro vient de `milestones()`.
        """
        numero = self.numeros_jalons[titre]
        noeuds = ",".join(self._noeud_jalon(str(iid)) for iid in iids)
        (self.fixtures / f"milestone-issues-{numero}.json").write_text(
            f'{{"data":{{"repository":{{"milestone":{{"issues":{{"nodes":[{noeuds}]}}}}}}}}}}',
            encoding="utf-8",
        )

    def ticket(
        self,
        iid: int,
        titre: str,
        *,
        statut: str = "À faire",
        prio: str = "moyenne",
        type_: str = "feature",
        assigne: str = "",
        parent: int | None = None,
        lots: list[tuple[int, str, bool]] | None = None,
        labels_sup: str = "",
    ) -> None:
        """Déclare un ticket : son statut, ses labels, et son rôle éventuel de lot ou de parent.

        `labels_sup` ajoute des labels à la liste de base — il n'existe que pour `lot::arbitre`
        (#562), qui est un fait porté par le PARENT et non par sa checklist : sans lui, le seul
        chemin testable serait celui du marqueur, c'est-à-dire la moitié de la règle.

        LE TICKET PORTE LES DEUX SUPPORTS DU DÉCOUPAGE (#393) : la prose et la checklist dans le
        corps, les lignes d'en-tête `parent:` / `lot:` que `gh_issue_raw` pose en régime `natif` —
        celui du défaut depuis ce lot. C'est la forme d'un vrai ticket depuis le backfill (#392),
        `/ticket-create` écrivant les deux et seule la LECTURE ayant basculé ; un double qui n'en
        porterait qu'un ferait dépendre le plan du régime, alors que tout l'enjeu est qu'il n'en
        dépende pas.
        """
        corps = f"Sous-ticket de #{parent} — lot 1/5.\n" if parent else ""
        entetes = f"parent:\t{parent}\n" if parent else ""
        if lots:
            corps += "\n## Sous-tickets\n\n" + "".join(
                f"- [ ] #{i} — {t}{' (parallèle)' if p else ''}\n" for i, t, p in lots
            )
            # La coche vaut « - » des deux côtés : le `state:` de ces doubles est toujours `open`,
            # et c'est de l'état que le natif la dérive (#390). Le `statut` d'un lot est son CYCLE
            # DE VIE, qui ne ferme rien.
            entetes += "".join(
                f"lot:\t{i}\t-\t{'∥' if p else '-'}\t{t}\n" for i, t, p in lots
            )
        labels = f"agent::dev, prio::{prio}, type::{type_}"
        if labels_sup:
            labels += f", {labels_sup}"
        (self.fixtures / f"issue-{iid}.txt").write_text(
            f"title:\t{titre}\nstate:\topen\nlabels:\t{labels}\n"
            f"assignees:\t{assigne}\n{entetes}--\n{corps}\n",
            encoding="utf-8",
        )
        (self.fixtures / f"owner-{iid}.json").write_text(
            _statut_json(str(iid), statut, assigne), encoding="utf-8"
        )
        self.tickets[str(iid)] = {
            "titre": titre, "statut": statut, "prio": prio, "type": type_, "assigne": assigne
        }

    def _labels(self, iid: str) -> str:
        """Les labels d'un ticket déclaré — CATÉGORISATION SEULE.

        L'état n'y est plus depuis #365 : il vit sur l'item de projet, et c'est la carte
        (`carte.json`) qui le porte. Les tables plates le recouvrent sur cette réponse-ci.
        """
        t = self.tickets[iid]
        return (
            f'{{"name":"type::{t["type"]}"}},{{"name":"prio::{t["prio"]}"}},'
            f'{{"name":"agent::dev"}}'
        )

    def _noeud(self, iid: str) -> str:
        """Un ticket déclaré, au format de nœud du BACKLOG (labels + assignés)."""
        t = self.tickets[iid]
        assignes = f'{{"login":"{t["assigne"]}"}}' if t["assigne"] else ""
        return (
            f'{{"number":{iid},"title":"{t["titre"]}",'
            f'"labels":{{"nodes":[{self._labels(iid)}]}},'
            f'"assignees":{{"nodes":[{assignes}]}}}}'
        )

    def _noeud_jalon(self, iid: str) -> str:
        """Le même ticket vu depuis un JALON : la requête n'y demande pas les assignés."""
        t = self.tickets[iid]
        return (
            f'{{"number":{iid},"title":"{t["titre"]}",'
            f'"labels":{{"nodes":[{self._labels(iid)}]}}}}'
        )

    def publie(self) -> None:
        """Compose ce que `queue.sh` lit : les deux tables, plus la CARTE qui porte les états.

        Trois fixtures et non deux depuis #365 : les issues disent QUI EXISTE, la carte du projet
        dit QUEL ÉTAT. Un ticket absent de la carte sort des tables avec un statut « - » — c'est le
        contrat, et c'est ce qui rend visible un ticket hors projet au lieu de le faire disparaître.
        """
        (self.fixtures / "projets.json").write_text(
            f"projets\nprojet\t{_PROJET}\t{_ID_PROJET}\n", encoding="utf-8"
        )
        # La ligne `page` est toujours émise, même sur un projet vide : sans elle, la garde
        # « réponse vide » de gh_graphql_read déclencherait trois tentatives puis une erreur.
        (self.fixtures / "carte.json").write_text(
            "page\tfalse\t\n"
            + "".join(f"item\t{iid}\t{t['statut']}\n" for iid, t in self.tickets.items()),
            encoding="utf-8",
        )
        (self.fixtures / "milestone-issues.json").write_text(
            '{{"data":{{"repository":{{"milestone":{{"issues":{{"nodes":[{}]}}}}}}}}}}'.format(
                ",".join(self._noeud_jalon(iid) for iid in self.tickets)
            ),
            encoding="utf-8",
        )
        (self.fixtures / "backlog.json").write_text(
            '{{"data":{{"repository":{{"issues":{{"nodes":[{}]}}}}}}}}'.format(
                ",".join(self._noeud(iid) for iid in self.tickets)
            ),
            encoding="utf-8",
        )

    def mr(
        self,
        branche: str,
        etat: str = "opened",
        iid: int = 99,
        brouillon: bool = True,
        ferme: tuple[int, ...] = (),
    ) -> None:
        """La PR d'UNE branche. Le nom du fichier aplatit les « / » — le bouchon fait de même.

        Une branche sans fixture n'a pas de PR : c'est ce qui fait la différence entre un ticket
        livré et un ticket dont la session n'a rien clos, donc entre un verdict « OK » et un
        « ECHEC ». Une fixture unique pour toutes les branches les confondrait.

        ⚠ `brouillon` vaut **vrai par défaut**, et ce n'est pas un détail de fixture : c'est ce que
        `/ticket-finish` produisait avant #418, donc l'état dans lequel une PR de run se trouvait
        quand tous ces tests ont été écrits. Un `merge-mr` (#415) s'y arrête à son premier
        prérequis et rend `6` — un verdict observable, mais jamais un merge. `brouillon=False` avec
        `ferme=(<iid>,)` décrit la PR que le chantier #413 rend MERGEABLE (#414).
        """
        etats = {"opened": "OPEN", "merged": "MERGED", "closed": "CLOSED"}
        fermetures = ",".join(f'{{"number":{n}}}' for n in ferme)
        charge = (
            f'{{"data":{{"repository":{{"pullRequests":{{"nodes":[{{"number":{iid},'
            f'"state":"{etats.get(etat, etat.upper())}","headRefOid":"deadbeef",'
            f'"isDraft":{"true" if brouillon else "false"},'
            f'"closingIssuesReferences":{{"nodes":[{fermetures}]}}}}]}}}}}}}}'
        )
        (self.fixtures / f"mr-{branche.replace('/', '__')}.json").write_text(
            charge, encoding="utf-8"
        )
        # Le repli des requêtes qui ne visent pas une branche (file de revue, branches des PR
        # ouvertes) : la dernière PR déclarée fait l'affaire, aucun test n'en distingue deux.
        (self.fixtures / "mr-iid.json").write_text(charge, encoding="utf-8")

    def run_actions(self, branche: str, conclusion: str = "success", statut: str = "completed",
                    sha: str = "deadbeef") -> None:
        """Le dernier run Actions d'une branche (#416) — le quatrième prérequis du merge.

        Le `sha` par défaut est celui que `mr()` donne à la tête de la PR : un run vert sur un
        AUTRE sha est un verdict périmé, que `merge-mr` rend en `3` et non en `0`.
        """
        (self.fixtures / f"run-{branche.replace('/', '__')}.json").write_text(
            f'{{"workflow_runs":[{{"id":9001,"status":"{statut}","conclusion":"{conclusion}",'
            f'"head_sha":"{sha}","html_url":"https://github.com/x/y/actions/runs/9001"}}]}}',
            encoding="utf-8",
        )

    # --- Lancement -------------------------------------------------------------------------------
    def lance(
        self,
        script: str,
        *args: str,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
    ) -> subprocess.CompletedProcess:
        # Le script est appelé par son chemin ABSOLU : les scripts se repèrent sur `BASH_SOURCE`,
        # donc ils doivent marcher depuis n'importe quel répertoire — `cwd` sert à le vérifier.
        return subprocess.run(
            [BASH, str(self.racine / "scripts/orchestrate" / script), *args],
            cwd=cwd or self.racine,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            # Filet contre un script bloqué, pas une mesure : il doit couvrir le pire cas légitime,
            # soit les quatre sessions d'un test de concurrence renonçant l'une après l'autre à leur
            # barrière de 45 s (#313). En deçà, une vraie régression de l'ordonnanceur sortirait en
            # `TimeoutExpired` — le seul échec de ce fichier qui ne dise pas ce qu'il a constaté.
            timeout=240,
        )

    def lib(self, *args: str) -> subprocess.CompletedProcess:
        """Comme `lance`, mais pour `scripts/gitlab/lib.sh` — dont queue.sh tient ses verdicts."""
        return subprocess.run(
            [BASH, str(self.racine / "scripts/gitlab/lib.sh"), *args],
            cwd=self.racine,
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=240,
        )


@pytest.fixture
def depot(tmp_path: Path) -> Depot:
    racine = tmp_path / "depot"
    fixtures = tmp_path / "fixtures"
    binaires = tmp_path / "bin"
    for d in (racine, fixtures, binaires, tmp_path / "home", tmp_path / "tmp"):
        d.mkdir(parents=True, exist_ok=True)

    for rel in SCRIPTS:
        cible = racine / rel
        cible.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(RACINE / rel, cible)

    # Un .claude/settings.json synthétique : `guard.sh --check` compare les `deny` du dépôt à ceux
    # de settings.run.json, il lui faut donc les deux fichiers.
    (racine / ".claude").mkdir(parents=True, exist_ok=True)
    reference = json.loads((RACINE / ".claude/settings.json").read_text(encoding="utf-8"))
    (racine / ".claude/settings.json").write_text(
        json.dumps({"permissions": reference["permissions"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    (binaires / "gh").write_text(STUB_GH, encoding="utf-8", newline="\n")
    (binaires / "gh").chmod(0o755)
    (fixtures / "vue_en_json.py").write_text(VUE_EN_JSON, encoding="utf-8", newline="\n")
    (binaires / "worktree-stub").write_text(STUB_WORKTREE, encoding="utf-8", newline="\n")
    (binaires / "worktree-stub").chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{binaires}{os.pathsep}{os.environ.get('PATH', '')}",
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path / "tmp"),
        "MAESTRO_FIXTURES": str(fixtures),
        # L'interpréteur qui a lancé pytest : le bouchon `gh` s'en sert pour la seule chose qu'un
        # script shell ne doit pas porter — l'encodage JSON d'un titre accentué.
        "MAESTRO_STUB_PYTHON": sys.executable,
        "MAESTRO_STUB_WORKTREE_DIR": str(racine),
        "MAESTRO_ORCHESTRATE_WORKTREE": str(binaires / "worktree-stub"),
        # La file de merge (#419) est ÉTEINTE par défaut dans ce harnais, et c'est un choix sur ce
        # que ces tests mesurent : ils portent sur la BOUCLE — plan, créneaux, verdicts, reprise —
        # et le bouchon `gh` ne sait pas jouer un merge (il faudrait un `origin/main`, un run
        # Actions et une PR non brouillon fermant son ticket). Laissée allumée, elle ferait passer
        # chaque ticket livré par un `merge-mr` qui refuse, et cent assertions parleraient d'un
        # refus de merge au lieu de ce qu'elles vérifient. Les tests qui la visent la rallument
        # explicitement (voir « La file de merge » plus bas) ; sa couverture complète — un merge qui
        # RÉUSSIT — est le lot 7 du chantier (#414), qui apporte le bouchon capable de le jouer.
        "MAESTRO_ORCHESTRATE_MERGE": "0",
        "GL_GQL_RETRIES": "1",
        "GL_GQL_RETRY_DELAY": "0",
    }
    return Depot(racine=racine, fixtures=fixtures, env=env)


def _claude_stub(depot: Depot, corps: str) -> str:
    """Écrit un bouchon de `claude` et renvoie son chemin."""
    chemin = depot.racine.parent / "bin" / "claude-stub"
    chemin.write_text(
        "#!/usr/bin/env bash\n" + textwrap.dedent(corps), encoding="utf-8", newline="\n"
    )
    chemin.chmod(0o755)
    return str(chemin)


def _spawn_stub(depot: Depot, corps: str = "") -> str:
    """Bouchon de `MAESTRO_ORCHESTRATE_SPAWN` : note le lanceur reçu, n'ouvre aucune console."""
    chemin = depot.racine.parent / "bin" / "spawn-stub"
    chemin.write_text(
        '#!/usr/bin/env bash\nprintf "%s\\n" "$1" > "$MAESTRO_FIXTURES/spawn.txt"\n'
        + textwrap.dedent(corps),
        encoding="utf-8",
        newline="\n",
    )
    chemin.chmod(0o755)
    return str(chemin)


def _groupe(parent: str, rang: int) -> str:
    """Le groupe de dépendance posé par queue.sh (#288) : « - » hors lot, « <parent>.<n> » sinon."""
    return "-" if parent == "-" else f"{parent}.{rang}"


def _plan(depot: Depot, lignes: list[tuple[int, int, str, str]]) -> str:
    """Écrit un plan figé (le TSV que queue.sh produit) et renvoie son chemin."""
    chemin = depot.racine / "plan.tsv"
    contenu = "# rang\tiid\tparent\tprio\tgroupe\ttitre\n" + "".join(
        f"{rang}\t{iid}\t{parent}\t{prio}\t{_groupe(parent, rang)}\tTicket {iid}\n"
        for rang, iid, parent, prio in lignes
    )
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return str(chemin)


def _lignes_du_plan(sortie: str) -> list[list[str]]:
    return [ligne.split("\t") for ligne in sortie.splitlines()
            if ligne and not ligne.startswith("#")]


# =====================================================================================
# queue.sh — l'ordre de traitement (#168)
# =====================================================================================

def _backlog_type(depot: Depot) -> None:
    """Un parent de suivi et ses cinq lots, plus un ticket isolé — le cas de référence."""
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=[(501, "Lot 1", False), (502, "Lot 2", False),
                                               (503, "Lot 3", False)])
    for i, titre in ((501, "Lot 1"), (502, "Lot 2"), (503, "Lot 3")):
        depot.ticket(i, titre, parent=500)
    depot.ticket(600, "Ticket isolé prioritaire", prio="haute")
    depot.publie()


def test_le_plan_ecarte_le_parent_et_garde_les_lots_dans_l_ordre(depot: Depot) -> None:
    _backlog_type(depot)
    r = depot.lance("queue.sh")
    assert r.returncode == 0, r.stderr
    iids = [ligne[1] for ligne in _lignes_du_plan(r.stdout)]
    assert "500" not in iids, "le parent de suivi ne porte ni branche ni code : il ne se traite pas"
    assert iids == ["600", "501", "502", "503"], (
        "le ticket prioritaire passe devant, puis les lots dans l'ordre de la checklist"
    )


def test_les_lots_d_un_meme_parent_restent_contigus(depot: Depot) -> None:
    """Un ticket isolé de priorité moyenne ne doit pas s'intercaler entre deux lots."""
    depot.milestone("Phase X")
    depot.ticket(500, "Parent", lots=[(501, "Lot 1", False), (502, "Lot 2", False)])
    depot.ticket(501, "Lot 1", parent=500)
    depot.ticket(502, "Lot 2", parent=500)
    depot.ticket(501 + 1000, "Isolé au milieu des iids", prio="moyenne")
    depot.publie()
    iids = [ligne[1] for ligne in _lignes_du_plan(depot.lance("queue.sh").stdout)]
    assert iids.index("502") - iids.index("501") == 1


def test_le_plan_ecarte_les_tickets_pris_et_les_statuts_autres(depot: Depot) -> None:
    depot.milestone("Phase X")
    depot.ticket(700, "Libre")
    depot.ticket(701, "Pris par quelqu'un", assigne="alice")
    depot.ticket(702, "Déjà en cours", statut="En cours")
    depot.ticket(703, "Déjà livré", statut="Terminé")
    depot.publie()
    r = depot.lance("queue.sh", "--check")
    iids = [ligne[1] for ligne in _lignes_du_plan(r.stdout)]
    assert iids == ["700"]
    assert "assigné à alice" in r.stderr
    assert "En cours" in r.stderr and "Terminé" in r.stderr


def test_le_plan_est_reproductible(depot: Depot) -> None:
    _backlog_type(depot)
    assert depot.lance("queue.sh").stdout == depot.lance("queue.sh").stdout


def test_plan_vide_sort_l_en_tete_sans_erreur(depot: Depot) -> None:
    depot.milestone("Phase X")
    depot.ticket(800, "Tout est fait", statut="Terminé")
    depot.publie()
    r = depot.lance("queue.sh")
    assert r.returncode == 0
    assert r.stdout.startswith("# rang")
    assert _lignes_du_plan(r.stdout) == []


# =====================================================================================
# guard.sh — le garde-fou en dur (#169)
# =====================================================================================

INTERDITS = [
    "git push --force origin main",
    "git push -f",
    "git push --force-with-lease origin x",
    "gh pr merge 143",
    "gh pr close 143",
    "gh run delete 1",
    "git reset --hard HEAD~1",
    "git commit --no-verify -m x",
    "npm test && git push --force",
]

AUTORISES = [
    "git push -u origin chore/1-x",
    "git commit -m 'feat: x'",
    "npm test",
    "gh pr create --draft",
    "gh run rerun 1",
    "git reset --soft HEAD~1",
    "cat -n fichier.txt",
]


@pytest.mark.parametrize("commande", INTERDITS)
def test_le_garde_fou_refuse_les_gestes_irreversibles(depot: Depot, commande: str) -> None:
    r = depot.lance("guard.sh", "--test", commande)
    assert r.returncode == 2, f"« {commande} » aurait dû être refusé : {r.stdout}"
    assert "REFUSÉ" in r.stdout


@pytest.mark.parametrize("commande", AUTORISES)
def test_le_garde_fou_laisse_passer_le_travail_ordinaire(depot: Depot, commande: str) -> None:
    r = depot.lance("guard.sh", "--test", commande)
    assert r.returncode == 0, f"« {commande} » aurait dû passer : {r.stdout}"
    assert "AUTORISÉ" in r.stdout


def test_le_garde_fou_ne_juge_que_les_appels_bash(depot: Depot) -> None:
    """Ce dépôt DOCUMENTE les commandes interdites : les écrire ne doit pas être refusé."""
    charge = json.dumps(
        {
            "tool_name": "Write",
            "tool_input": {
                "file_path": "docs/10.md",
                "content": "Ne jamais lancer gh pr merge ni git push --force.",
            },
        }
    )
    r = subprocess.run(
        [BASH, "scripts/orchestrate/guard.sh"],
        cwd=depot.racine, env=depot.env, input=charge,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 0, "un Write de documentation n'est pas un appel Bash"


def test_le_garde_fou_bloque_un_appel_bash_en_mode_hook(depot: Depot) -> None:
    charge = json.dumps(
        {"tool_name": "Bash", "tool_input": {"command": "git push --force origin main"}}
    )
    r = subprocess.run(
        [BASH, "scripts/orchestrate/guard.sh"],
        cwd=depot.racine, env=depot.env, input=charge,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )
    assert r.returncode == 2, "sortie 2 = appel bloqué (contrat PreToolUse)"
    assert "force-push" in r.stderr


def test_check_valide_la_copie_des_deny_du_depot(depot: Depot) -> None:
    r = depot.lance("guard.sh", "--check")
    assert r.returncode == 0, r.stderr


def test_check_detecte_une_regle_deny_oubliee(depot: Depot) -> None:
    """Un interdit ajouté au dépôt et oublié dans settings.run.json ne protégerait plus les runs."""
    chemin = depot.racine / ".claude/settings.json"
    reglages = json.loads(chemin.read_text(encoding="utf-8"))
    reglages["permissions"]["deny"].append("Bash(gh pr merge --admin:*)")
    chemin.write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding="utf-8")
    r = depot.lance("guard.sh", "--check")
    assert r.returncode == 1
    assert "Bash(gh pr merge --admin:*)" in r.stderr, "la règle manquante est nommée en entier"


# =====================================================================================
# settings.run.json — l'allowlist des sessions autonomes (#179)
# =====================================================================================
# Ces trois tests gardent des décisions qui ont coûté un run entier à apprendre (§11.7). Ils lisent
# le fichier VERSIONNÉ, pas la copie du dépôt jetable : c'est le régime réel des runs qui est en
# jeu.

def _allow() -> list:
    chemin = RACINE / "scripts/orchestrate/settings.run.json"
    return json.loads(chemin.read_text(encoding="utf-8"))["permissions"]["allow"]


def test_une_session_autonome_peut_invoquer_les_skills() -> None:
    """Sans `Skill`, la session de #130 a refait le cycle /ticket-start À LA MAIN (100 tours)."""
    allow = _allow()
    assert "Skill" in allow, "le tool Skill doit être autorisé, et nu"
    # Le tool Skill ne déclare pas de `ruleContentField` (là où Bash expose `command`) : une règle
    # `Skill(ticket-start)` ne matcherait jamais rien tout en donnant l'illusion d'autoriser.
    assert not [r for r in allow if r.startswith("Skill(")], \
        "une règle Skill avec spécificateur ne matche rien — elle donnerait une fausse sécurité"


def test_le_decor_de_pipeline_est_autorise() -> None:
    """Une chaîne vaut son maillon le plus faible : un `echo` de confort la faisait tomber."""
    allow = _allow()
    for binaire in ("cd", "echo", "printf", "grep", "sed"):
        assert f"Bash({binaire}:*)" in allow, \
            f"{binaire} manquant : il ferait tomber des chaînes dont tout le reste est autorisé"


def test_les_refus_merites_ne_sont_pas_leves() -> None:
    """#178 a fermé le mode d'échec « la session attend un résultat » — ne pas le rouvrir ici."""
    allow = _allow()
    assert not [r for r in allow if r.startswith("Bash(sleep")], \
        "les attentes actives ont coûté le run de #131 : un résultat s'obtient en avant-plan"
    assert "Bash(bash:*)" not in allow, \
        "« bash » tout court exécuterait n'importe quel script, hors du dépôt compris"


# --- Ce que onze runs de plus ont appris (#235, parent #232) -------------------------------------
# 83 refus sur 16 sessions, dont quinze ne tenaient à AUCUNE règle de matching : l'outil
# était
# simplement absent de la liste, alors qu'aucun n'écrit hors du worktree. Deux d'entre eux
# revenaient à CHAQUE run par construction — `env` sur la fausse alerte de la couleur (#236),
# `node` sur un `tsc` sans script npm — et se sont payés en tours à chaque fois.

@pytest.mark.parametrize(
    ("regle", "pourquoi"),
    [
        ("Bash(env:*)", "lire l'environnement est le premier geste devant un test rouge en local"),
        ("Bash(printenv:*)", "le pendant de `env`, que les sessions essaient tout autant"),
        ("Bash(awk:*)", "le seul dépouillement de JSON sans jq ni Python, partout ici"),
        ("Bash(command -v:*)", "« cet outil est-il là ? », que les scripts posent avant d'agir"),
        ("Bash(git ls-remote:*)", "une lecture, comme `git config --get`"),
        ("Bash(git config:*)", "n'écrit au pire que dans le dépôt du worktree"),
        ("Bash(node:*)", "`npm run:*` passait, un outil sans script npm dédié non"),
        ("Bash(npx:*)", "même raison que `node`"),
    ],
)
def test_les_outils_absents_qui_ont_coute_quinze_refus_sont_autorises(
    regle: str, pourquoi: str
) -> None:
    assert regle in _allow(), f"{regle} manquant — {pourquoi}"


def test_le_destructif_et_l_interpreteur_nu_restent_dehors() -> None:
    """Deux exclusions délibérées, que le `$comment` du fichier doit continuer d'expliquer.

    Elles ne se redécouvrent pas au refus suivant : leur raison est écrite là où on regarde quand
    on s'apprête à élargir la liste.
    """
    reglages = json.loads(
        (RACINE / "scripts/orchestrate/settings.run.json").read_text(encoding="utf-8")
    )
    allow = reglages["permissions"]["allow"]
    assert not [r for r in allow if r.startswith("Bash(rm")], \
        "aucune règle de préfixe ne borne la cible de `rm` : ce serait `rm -rf <n'importe quoi>`"
    assert "Bash(bash:*)" not in allow, \
        "`bash <chemin absolu>` ferait sauter la borne des règles `Bash(bash scripts/…)`"
    commentaire = " ".join(reglages["$comment"])
    for mot in ("rm", "chemin absolu"):
        assert mot in commentaire, f"l'exclusion de « {mot} » doit être expliquée dans le fichier"
    assert "journal.sh refus" in commentaire, \
        "la boucle de retour de §11.7 ne tient que si la commande qui l'outille est nommée ici"


def test_le_prompt_nomme_les_trois_formes_qu_aucune_regle_ne_matche(depot: Depot) -> None:
    """Elles ne se devinent PAS depuis un refus, qui ne dit jamais ce qui a manqué.

    Et la plus coûteuse tombe sur la dernière action du ticket : huit sessions sur seize ont buté
    sur une création de PR à description multi-ligne, puis sur le `$(cat …)` par lequel elles
    essayaient de s'en sortir. Le prompt doit donc les nommer, et dire le geste de remplacement.
    """
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-formes.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "formes",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt-formes.txt").read_text(encoding="utf-8")
    assert "SAUT DE LIGNE" in prompt
    assert "SUBSTITUTION" in prompt and "$(" in prompt
    assert "HEREDOC" in prompt
    # Nommer l'interdit ne suffit pas : sans le geste de remplacement, la session cherche, et
    # c'est cette recherche qui coûte des tours.
    assert "l'outil Write" in prompt, "le remplacement doit être nommé, pas seulement l'interdit"
    assert "CHEMIN de ce fichier" in prompt


def test_le_prefixe_de_variable_est_ecarte_avec_sa_raison_et_son_remplacement() -> None:
    """Le seul VRAI trou d'allowlist des onze runs suivant #232 — et il n'est PAS comblé (#307).

    Une règle est un préfixe de COMMANDE, or la commande commence par la variable : la seule règle
    qui matcherait devrait figer la valeur en dur, ne couvrirait que celle-là et se périmerait au
    premier port changé. Le geste est donc dans la forme — et il existe déjà.
    """
    reglages = json.loads(
        (RACINE / "scripts/orchestrate/settings.run.json").read_text(encoding="utf-8")
    )
    allow = reglages["permissions"]["allow"]
    assert not [r for r in allow if re.match(r"Bash\([A-Z_]+=", r)], (
        "une règle à préfixe de variable figerait la VALEUR : elle ne couvrirait que ce cas-là"
    )
    assert "Bash(env:*)" in allow, "`env VAR=… <commande>` est le remplacement — il doit passer"
    commentaire = " ".join(reglages["$comment"])
    assert "env VAR=" in commentaire, "l'écart n'est un choix que s'il dit par quoi remplacer"
    assert "#307" in commentaire


def test_le_prompt_designe_un_atelier_dans_le_worktree(depot: Depot) -> None:
    """Interdire `/tmp` sans DÉSIGNER un remplaçant ne fait que déplacer le refus (#307).

    Une session écrit forcément ses fichiers de travail quelque part, et les deux endroits qu'elle
    connaît spontanément sont hors du répertoire de travail. C'est la cause n°1 des refus : 9 sur
    12 du dernier run complet.
    """
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-atelier.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "atelier",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt-atelier.txt").read_text(encoding="utf-8")
    assert ".maestro/session/" in prompt, "l'endroit désigné doit être nommé, pas sous-entendu"
    assert "/tmp" in prompt, "et celui qu'on remplace aussi, sinon la session y retourne"
    assert "env VAR=" in prompt, "le remplacement du préfixe de variable (#307) se dit ici aussi"


# --- Trois maillons refusés run après run, et aucun ne reçoit de règle (#528) ---------------------
# Mesure du 2026-08-25 sur tout le journal : 88 refus sur 36 sessions, dont 45 TROUS d'allowlist —
# première famille depuis que #307 les classe. `for` (19 refus), `curl` (5) et `python -` (5) en
# portent 29 à eux seuls, et reviennent depuis #292 sans avoir jamais été instruits. Les trois
# sortent par la FORME, comme #307 avait DÉSIGNÉ `.maestro/session/` au lieu d'autoriser `/tmp`.

def _elargissements_ecartes(allow: list) -> list:
    """Les règles que #528 a écartées, repérées comme le ferait un élargissement distrait.

    Le motif porte sur le VERBE de la règle, pas sur son texte entier : la variante « bornée »
    `Bash(curl -s http://127.0.0.1:*)` doit tomber autant que `Bash(curl:*)`, puisqu'elle fige le
    drapeau et l'hôte tout en pariant sur ce que le CLI fait d'un préfixe qui ne s'arrête pas sur
    une espace.
    """
    return [r for r in allow if re.match(r"Bash\((for|curl|python3?)[ :)]", r)]


def test_le_motif_des_trois_maillons_attrape_un_elargissement() -> None:
    """Prouver le motif sur un échantillon fautif : un ✓ sur une question jamais posée ne garde
    rien. On le rejoue sur des `allow` fabriqués, jamais sur le vrai."""
    assert _elargissements_ecartes(["Bash(for:*)"]) == ["Bash(for:*)"]
    assert _elargissements_ecartes(["Bash(curl:*)"]) == ["Bash(curl:*)"]
    assert _elargissements_ecartes(["Bash(curl -s http://127.0.0.1:*)"]), \
        "la variante « bornée » parie sur le matching du CLI — elle doit tomber aussi"
    assert _elargissements_ecartes(["Bash(python:*)", "Bash(python3:*)"]) == \
        ["Bash(python:*)", "Bash(python3:*)"]
    # Et ce qu'il ne doit PAS confondre avec un élargissement : les formes de remplacement, dont
    # deux commencent par les mêmes lettres que ce qu'on écarte.
    assert _elargissements_ecartes([
        "Bash(.venv/Scripts/python.exe:*)", "Bash(node:*)", "Bash(sed:*)",
        "Bash(env:*)", "Bash(printf:*)", "Bash(printenv:*)",
    ]) == []


def test_les_trois_maillons_restent_dehors_avec_leur_raison_et_leur_remplacement() -> None:
    """Un refus mérité n'est un choix que s'il dit par quoi remplacer (#307), et que si sa raison
    est écrite là où on regarde quand on s'apprête à élargir la liste."""
    reglages = json.loads(
        (RACINE / "scripts/orchestrate/settings.run.json").read_text(encoding="utf-8")
    )
    allow = reglages["permissions"]["allow"]
    assert _elargissements_ecartes(allow) == [], (
        "une tête de boucle n'est pas une commande, la cible d'un `curl` est un ARGUMENT et "
        "`python -` est un heredoc : aucun des trois ne se borne par une règle de préfixe (#528)"
    )
    # La forme de remplacement, elle, doit passer : prescrire un geste que la liste refuse, c'est
    # un refus de plus par ticket — la leçon de #310, mesurée par #436.
    for regle, pourquoi in (
        ("Bash(sed:*)", "la boucle se remplace par une commande qui prend la liste"),
        ("Bash(node:*)", "la sonde HTTP locale passe par node, jamais par curl"),
        ("Bash(.venv/Scripts/python.exe:*)", "le snippet Python se joue au venv, dans un fichier"),
        ("Bash(env:*)", "`env PYTHONPATH=.` lui fait importer le maestro du worktree"),
    ):
        assert regle in allow, f"{regle} manquant — {pourquoi}"
    # Les lignes du `$comment` portent leur propre indentation : on compare le texte, pas la façon
    # dont il est replié, sinon le test casserait au premier reformatage du fichier.
    commentaire = re.sub(r"\s+", " ", " ".join(reglages["$comment"]))
    assert "#528" in commentaire
    for raison in (
        "tête de boucle n'est pas une commande",
        "pouvoir de `curl` est dans son ARGUMENT",
        "c'est un HEREDOC",
    ):
        assert raison in commentaire, f"la raison « {raison} » doit rester écrite dans le fichier"
    # Le résultat ATTENDU compte autant que la décision : traités par la forme, les trois restent
    # dans « maillons qu'aucune règle ne couvre » — un vieux run les montrera pour toujours.
    assert "RESTENT dans la liste" in commentaire, \
        "sans l'attendu écrit, la prochaine lecture de `journal.sh refus` passera pour un échec"


def test_le_prompt_donne_la_forme_couverte_des_trois_maillons(depot: Depot) -> None:
    """Interdire sans DÉSIGNER ne fait que déplacer le refus (#307) — la leçon vaut ici entière.

    Les trois maillons ne reçoivent aucune règle : ce qui les remplace vit donc TOUT ENTIER dans le
    prompt. Sans lui, une session réessaierait la même forme au run suivant, comme depuis #292.
    """
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-maillons.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "maillons",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt-maillons.txt").read_text(encoding="utf-8")
    for refuse, remplacement in (
        ("for … ; do … ; done", "sed -n -e 12p -e 40p"),
        ("python - ", ".venv/Scripts/python.exe .maestro/session/<nom>.py"),
        ("curl", "fetch('http://127.0.0.1:"),
    ):
        assert refuse in prompt, f"le geste refusé « {refuse} » doit être nommé"
        assert remplacement in prompt, \
            f"sans « {remplacement} », le prompt ne ferait qu'interdire (#307)"


# =====================================================================================
# run.sh — la boucle (#170)
# =====================================================================================

def test_dry_run_n_execute_rien_et_ne_laisse_aucun_run(depot: Depot) -> None:
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "essai")
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/essai").exists()


def test_un_ticket_reussi_est_celui_dont_gitlab_atteste_l_etat(depot: Depot) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    # Le bouchon bascule le statut du ticket comme /ticket-ship le ferait : le run doit le lire
    # « À faire » avant de le prendre, « En revue » après la session.
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":3.5}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "ok", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/ok/resume.tsv").read_text(encoding="utf-8")
    assert "130	OK	99" in resume
    assert "3.5" in resume, "le cout de la session est consigne"


def test_une_session_qui_se_dit_reussie_sans_mr_est_un_echec(depot: Depot) -> None:
    """Le verdict vient de GitLab, jamais de la prose de la session."""
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false,"result":"tout est fait !"}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "menteur",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 1
    chemin = depot.racine / ".maestro/orchestrate/menteur/resume.tsv"
    assert "ECHEC" in chemin.read_text(encoding="utf-8")


def test_un_echec_fait_sauter_les_lots_suivants_du_meme_parent(depot: Depot) -> None:
    for iid in (501, 502, 601):
        depot.ticket(iid, f"Ticket {iid}", parent=500 if iid < 600 else None)
    claude = _claude_stub(depot, 'printf \'{"is_error":true,"result":"boom"}\'\nexit 1\n')
    plan = _plan(
        depot,
        [(1, 501, "500", "haute"), (2, 502, "500", "haute"), (3, 601, "-", "haute")],
    )
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "casse",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    resume = (depot.racine / ".maestro/orchestrate/casse/resume.tsv").read_text(encoding="utf-8")
    assert "502\tSAUTE" in resume, "un lot dont le prédécesseur a échoué part d'une base incomplète"
    assert "lot précédent de #500" in resume
    assert "601\tECHEC" in resume, "les autres groupes du plan s'enchaînent malgré tout"
    assert r.returncode == 1


def test_un_ticket_pris_entre_temps_est_saute_pas_vole(depot: Depot) -> None:
    """Le plan est figé, le backlog non : quelqu'un a pu prendre le ticket depuis."""
    depot.ticket(130, "Déjà pris depuis", statut="En cours", assigne="alice")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "collision",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, "un ticket sauté n'est pas un échec"
    chemin = depot.racine / ".maestro/orchestrate/collision/resume.tsv"
    resume = chemin.read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume and "En cours" in resume


def test_max_borne_les_tickets_tentes_meme_en_cas_de_panne(depot: Depot) -> None:
    """Sans cela, une panne systématique épuiserait tout le plan malgré --max."""
    for iid in (130, 131, 132):
        depot.ticket(iid, f"Ticket {iid}")
    echec = depot.racine.parent / "bin" / "worktree-ko"
    echec.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8", newline="\n")
    echec.chmod(0o755)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "borne", "--max", "1",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_WORKTREE": str(echec)},
    )
    lignes = (depot.racine / ".maestro/orchestrate/borne/resume.tsv").read_text(encoding="utf-8")
    assert len([x for x in lignes.splitlines() if not x.startswith("#")]) == 1
    assert "Plafond --max 1" in r.stdout


def test_un_ticket_saute_avance_la_position_mais_pas_le_quota_de_max(depot: Depot) -> None:
    """Les deux compteurs disent deux choses (#230) : la position suit le plan, sautés compris ;
    `--max` ne compte que les tickets réellement TENTÉS, un saut ne coûtant rien."""
    depot.ticket(130, "Pris par quelqu un d autre", statut="En cours", assigne="alice")
    depot.ticket(131, "Ticket 131")
    depot.ticket(132, "Ticket 132")
    depot.mr("feat/131-ticket-131", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("131", "En revue")}' > "$MAESTRO_FIXTURES/owner-131.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "positions", "--max", "1",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[2/3] #131" in r.stdout, "#131 est le 2e du plan, même s'il est le 1er à être tenté"
    assert "Plafond --max 1" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/positions/resume.tsv").read_text(
        encoding="utf-8")
    iids = [x.split("\t")[0] for x in resume.splitlines() if not x.startswith("#")]
    assert iids == ["130", "131"], "le saut n'a rien consommé, le plafond a arrêté avant #132"


def test_le_fichier_stop_empeche_un_run_de_demarrer(depot: Depot) -> None:
    (depot.racine / ".maestro/orchestrate").mkdir(parents=True, exist_ok=True)
    (depot.racine / ".maestro/orchestrate/STOP").touch()
    # Bouchon qui échoue bruyamment : sans lui, le test emprunterait le `claude` de la machine —
    # vert sur un poste de dev, rouge en CI où le CLI n'existe pas. Il vaut mieux qu'il ait aussi
    # à dire que la session n'a pas démarré du tout.
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "stoppe",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0
    assert "Arrêt demandé" in r.stdout


def test_une_duree_de_timeout_invalide_est_refusee(depot: Depot) -> None:
    """Un timeout mal interprété tuerait des sessions valides : mieux vaut refuser tout de suite."""
    r = depot.lance("run.sh", "--timeout", "3j", "--dry-run")
    assert r.returncode == 2
    assert "durée invalide" in r.stderr


# =====================================================================================
# L'effort de raisonnement, épinglé par le dépôt (#217)
# =====================================================================================
#
# Ce que ces tests protègent n'est pas une valeur mais une PROVENANCE. Avant #217, `run.sh` ne
# passait aucun `--effort` et le niveau venait de `~/.claude/settings.json` du poste : un dépôt qui
# ne dit rien laisse la machine décider, et rien dans la sortie d'un run ne le montre. Le bouchon
# note donc les arguments reçus, et c'est sur eux qu'on juge — pas sur la prose du run.


def _claude_note_les_arguments(depot: Depot, journal: Path) -> str:
    """Bouchon qui consigne ses arguments, puis réussit comme /ticket-ship l'aurait fait."""
    return _claude_stub(depot, f"""
        printf '%s\\n' "$@" > "{journal}"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)


def test_l_effort_est_xhigh_sans_qu_on_le_demande(depot: Depot) -> None:
    """Le défaut du dépôt, celui qui vaut quand personne ne passe l'option."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / "args-defaut"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "eff", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--effort" in recus, "sans l'option, l'effort viendrait des settings du poste"
    assert recus[recus.index("--effort") + 1] == "xhigh"


@pytest.mark.parametrize(
    "args, env, attendu",
    [
        (["--effort", "max"], {}, "max"),
        ([], {"MAESTRO_ORCHESTRATE_EFFORT": "high"}, "high"),
        # L'option gagne sur la variable : c'est le geste le plus explicite des deux.
        (["--effort", "low"], {"MAESTRO_ORCHESTRATE_EFFORT": "medium"}, "low"),
    ],
)
def test_l_effort_se_surcharge_en_connaissance_de_cause(
    depot: Depot, args: list[str], env: dict[str, str], attendu: str
) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-{attendu}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"eff-{attendu}", *args,
        env={"MAESTRO_CLAUDE_BIN": claude, **env},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--effort") + 1] == attendu


def test_un_effort_inconnu_est_refuse_avant_le_premier_ticket(depot: Depot) -> None:
    """Le CLI refuserait la valeur à chaque session : le run brûlerait son plan en échecs
    jumeaux."""
    r = depot.lance("run.sh", "--effort", "extra-high", "--dry-run")
    assert r.returncode == 2
    assert "effort inconnu" in r.stderr
    assert "xhigh" in r.stderr, "le message nomme les niveaux acceptés"


def test_la_session_reprise_porte_aussi_l_effort(depot: Depot) -> None:
    """Deux invocations de `claude` dans la boucle — la reprise est la plus oubliable."""
    depot.ticket(130, "Ticket interrompu")
    journal = depot.racine.parent / "args-reprise"
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s\\n' "$@" > "{journal}"
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "eff-reprise", "--effort", "max",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--effort") + 1] == "max", "la session reprise garde le régime du run"


def test_l_effort_est_annonce_dans_le_plan(depot: Depot) -> None:
    """Journalisé à côté du modèle : relire un run doit dire sous quel régime il a tourné."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "eff-plan")
    assert "effort xhigh" in r.stdout
    assert "--effort xhigh" in r.stdout, "l'aperçu de la commande de session reste fidèle"


def test_l_effort_traverse_le_lancement_detache(depot: Depot) -> None:
    """Le run détaché est un autre processus : ce que l'appelant a choisi doit le suivre."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    claude = _claude_stub(depot, 'echo "aucune session côté pilote" >&2\nexit 1\n')
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "eff-detache", "--effort", "max",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    lanceur = depot.racine / ".maestro/orchestrate/eff-detache/lancer.sh"
    corps = lanceur.read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "--effort max" in commande


# =====================================================================================
# Le plafond de dépense, posé seulement s'il est demandé (#286)
# =====================================================================================
#
# Miroir exact de la section précédente, à l'inverse près : ce qu'on protège ici n'est pas la
# présence d'un réglage mais son ABSENCE. `run.sh` passait `--max-budget-usd 15` à chaque session ;
# une session qui touche le plafond meurt en plein travail, sans commit ni PR, et la boucle la
# compte en échec — ce qui saborde les lots suivants du même parent. Les deux runs du 2026-08-06 y
# ont laissé 2 tickets coupés au même montant (15.07 $) et 13 sautés en cascade. Le bouchon note ses
# arguments, et c'est sur eux qu'on juge.


def test_aucun_plafond_de_budget_sans_qu_on_le_demande(depot: Depot) -> None:
    """Le défaut du dépôt : une session va au bout de son ticket, pas d'un montant."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / "args-budget-defaut"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "bud", env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus, "un plafond non demandé coupe la session en plein vol"


@pytest.mark.parametrize(
    "args, env, attendu",
    [
        (["--budget", "20"], {}, "20"),
        ([], {"MAESTRO_ORCHESTRATE_BUDGET": "8"}, "8"),
        # L'option gagne sur la variable : c'est le geste le plus explicite des deux.
        (["--budget", "20"], {"MAESTRO_ORCHESTRATE_BUDGET": "8"}, "20"),
    ],
)
def test_le_plafond_se_pose_explicitement(
    depot: Depot, args: list[str], env: dict[str, str], attendu: str
) -> None:
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-budget-{attendu}-{len(env)}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"bud-{attendu}-{len(env)}", *args,
        env={"MAESTRO_CLAUDE_BIN": claude, **env},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert recus[recus.index("--max-budget-usd") + 1] == attendu


@pytest.mark.parametrize("zero", ["0", "0.00"])
def test_un_plafond_a_zero_vaut_pas_de_plafond(depot: Depot, zero: str) -> None:
    """Seule façon d'annuler une variable déjà posée dans l'environnement — et surtout, un
    « --max-budget-usd 0 » transmis tel quel tuerait chaque session avant son premier outil."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    journal = depot.racine.parent / f"args-budget-zero-{zero}"
    claude = _claude_note_les_arguments(depot, journal)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", f"bud-zero-{zero}", "--budget", zero,
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_BUDGET": "15"},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus


def test_un_budget_illisible_est_refuse_avant_le_premier_ticket(depot: Depot) -> None:
    """Même raison que pour l'effort : le CLI le refuserait à CHAQUE session."""
    r = depot.lance("run.sh", "--budget", "vingt", "--dry-run")
    assert r.returncode == 2
    assert "budget invalide" in r.stderr


def test_la_session_reprise_porte_le_meme_regime_de_budget(depot: Depot) -> None:
    """Deux invocations de `claude` dans la boucle — la reprise est la plus oubliable."""
    depot.ticket(130, "Ticket interrompu")
    journal = depot.racine.parent / "args-budget-reprise"
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s\\n' "$@" > "{journal}"
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "bud-reprise",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    recus = journal.read_text(encoding="utf-8").splitlines()
    assert "--max-budget-usd" not in recus, "la session reprise garde le régime du run"


def test_le_regime_de_budget_est_annonce_dans_les_deux_sens(depot: Depot) -> None:
    """« Illimité » est un choix, pas un oubli : relire un run doit dire lequel s'appliquait —
    un ticket coupé au plafond ne se distingue d'un échec de session que par cette ligne."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    sans = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "bud-plan")
    assert "budget illimité" in sans.stdout
    assert "--max-budget-usd" not in sans.stdout, "l'aperçu de la commande de session reste fidèle"
    avec = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "bud-plan-20",
                       "--budget", "20")
    assert "budget 20 $/ticket" in avec.stdout
    assert "--max-budget-usd 20" in avec.stdout


# =====================================================================================
# Le délai par ticket, posé seulement s'il est demandé (#326)
# =====================================================================================
#
# Même leçon que la section précédente, sur l'autre plafond de session — au point que le commentaire
# de `run.sh` citait `--timeout` parmi les bornages qui « bornent vraiment ». Il en était un tant
# qu'une session durait 20 min ; à `claude-opus-5` + effort `xhigh`, les 45 min par défaut sont
# devenues le premier tueur de sessions du run (2026-08-10 : #315 livré en 42min50, #316 coupé à
# 45min02 alors que son travail était commité — sept lots sautés en cascade derrière).
#
# Ce qui s'observe n'est pas un argument passé au CLI : `timeout` est un PRÉFIXE de commande, pas
# une option de `claude`, donc le bouchon qui note ses arguments ne le verrait pas. On juge sur le
# comportement — un bouchon qui traîne plus longtemps que le délai posé —, ce qui est de toute façon
# la vraie question : la session a-t-elle été tuée, oui ou non.

#: Un bouchon qui met deux secondes avant de rendre la main, puis réussit comme /ticket-ship.
#: Deux secondes suffisent : les délais testés en face valent 1 s.
_CLAUDE_LENT = """
    sleep 2
    printf '%s' '{statut}' > "$MAESTRO_FIXTURES/owner-130.json"
    printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
    exit 0
"""


def _claude_lent(depot: Depot) -> str:
    return _claude_stub(depot, _CLAUDE_LENT.format(statut=_statut_json("130", "En revue")))


def test_aucun_delai_sans_qu_on_le_demande(depot: Depot) -> None:
    """Le défaut du dépôt : une session va au bout de son ticket, pas d'un chronomètre."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "delai-defaut",
                    env={"MAESTRO_CLAUDE_BIN": _claude_lent(depot)})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/delai-defaut/resume.tsv").read_text(
        encoding="utf-8"
    )
    assert "130\tOK" in resume, "un délai non demandé coupe la session en plein vol"
    assert "timeout" not in r.stdout


def test_le_delai_se_pose_explicitement(depot: Depot) -> None:
    """Il reste disponible pour qui le veut — et il tue alors la session, c'est tout son objet."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "delai-1s", "--timeout", "1s",
                    env={"MAESTRO_CLAUDE_BIN": _claude_lent(depot)})
    assert "timeout" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/delai-1s/resume.tsv").read_text(encoding="utf-8")
    assert "130\tECHEC" in resume


def test_le_delai_se_pose_aussi_par_l_environnement(depot: Depot) -> None:
    """MAESTRO_ORCHESTRATE_TIMEOUT, pendant de la variable du modèle et de l'effort."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "delai-env",
                    env={"MAESTRO_CLAUDE_BIN": _claude_lent(depot),
                         "MAESTRO_ORCHESTRATE_TIMEOUT": "1s"})
    assert "timeout" in r.stdout


@pytest.mark.parametrize("zero", ["0", "0s"])
def test_un_delai_a_zero_vaut_pas_de_delai(depot: Depot, zero: str) -> None:
    """Seule façon d'annuler une variable déjà posée dans l'environnement — et surtout, un
    « timeout 0 » transmis tel quel tuerait chaque session à l'instant même."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", f"delai-zero-{zero}",
                    "--timeout", zero,
                    env={"MAESTRO_CLAUDE_BIN": _claude_lent(depot),
                         "MAESTRO_ORCHESTRATE_TIMEOUT": "1s"})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / f".maestro/orchestrate/delai-zero-{zero}/resume.tsv").read_text(
        encoding="utf-8"
    )
    assert "130\tOK" in resume


def test_le_regime_de_delai_est_annonce_dans_les_deux_sens(depot: Depot) -> None:
    """« Sans délai » est un choix, pas un oubli : relire un run doit dire lequel s'appliquait —
    un ticket coupé au chronomètre ne se distingue d'un échec de session que par cette ligne."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    sans = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "delai-plan")
    assert "sans délai" in sans.stdout
    avec = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "delai-plan-90",
                       "--timeout", "90m")
    assert "timeout 1h30/ticket" in avec.stdout


# =====================================================================================
# La reprise après limite d'usage (#171)
# =====================================================================================

def _fixture_limite(depot: Depot, nom: str, contenu: str) -> str:
    chemin = depot.racine / f"{nom}.json"
    chemin.write_text(contenu, encoding="utf-8", newline="\n")
    return str(chemin)


def test_un_reset_en_epoch_donne_l_attente_jusqu_au_reset(depot: Depot) -> None:
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "epoch", f'{{"is_error":true,"result":"Claude AI usage limit reached|{futur}"}}'
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0
    assert "LIMITE D'USAGE détectée" in r.stdout
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3600 < secondes <= 3600 + 130, "reset + la marge, à la seconde d'exécution près"


def test_un_reset_en_millisecondes_n_attend_pas_mille_fois_trop(depot: Depot) -> None:
    futur_ms = (int(time.time()) + 3600) * 1000
    f = _fixture_limite(
        depot,
        "ms",
        f'{{"is_error":true,"rate_limits":{{"five_hour":{{"resetsAt":"{futur_ms}"}}}},'
        f'"result":"usage limit reached"}}',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3500 < secondes <= 3600 + 130


def test_sans_heure_de_reset_on_retombe_sur_le_palier(depot: Depot) -> None:
    f = _fixture_limite(depot, "sans-reset",
                        '{"is_error":true,"api_error_status":429,"result":"rate limited"}')
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0
    assert "palier" in r.stdout


def test_un_reset_deja_passe_ne_relance_pas_aussitot(depot: Depot) -> None:
    """Horloge décalée ou en-tête périmé : sans garde-fou, la boucle retaperait la même limite."""
    passe = int(time.time()) - 7200
    f = _fixture_limite(
        depot, "passe", f'{{"is_error":true,"result":"usage limit reached|{passe}"}}'
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert secondes == 900, "on retombe sur le palier plutôt que d'attendre zéro"


@pytest.mark.parametrize("contenu", [
    '{"type":"result","subtype":"success","is_error":false,"result":"tout va bien"}',
    '{"is_error":true,"result":"ENOENT: no such file or directory"}',
])
def test_un_echec_ordinaire_ne_declenche_aucune_reprise(depot: Depot, contenu: str) -> None:
    f = _fixture_limite(depot, "ordinaire", contenu)
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 1
    assert "PAS UNE LIMITE" in r.stdout


# =====================================================================================
# La télémétrie du flux stream-json n'est pas un refus (#203)
# =====================================================================================
# Le CLI ouvre CHAQUE session par un événement qui rapporte la fenêtre de 5 h en cours — y compris
# une session qui ira au bout. Depuis que le flux brut est grepé (#176), il faisait dormir un run
# jusqu'au reset après un ticket pourtant LIVRÉ. Noter `overageStatus` : « rejected » dès que
# l'organisation interdit le dépassement, sur une ligne qui n'est pas un refus pour autant.
def _evenement_fenetre(statut: str, reset: int) -> str:
    """L'événement d'ouverture du flux, tel que le CLI l'écrit."""
    return (
        '{"type":"rate_limit_event","rate_limit_info":{"status":"' + statut + '",'
        '"resetsAt":' + str(reset) + ',"rateLimitType":"five_hour",'
        '"overageStatus":"rejected","isUsingOverage":false},"session_id":"06cacb83"}'
    )


def test_la_telemetrie_de_fenetre_n_est_pas_une_limite(depot: Depot) -> None:
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "telemetrie",
        _evenement_fenetre("allowed", futur) + "\n"
        + '{"type":"result","subtype":"success","is_error":false,"result":"livré"}\n',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 1, r.stdout
    assert "PAS UNE LIMITE" in r.stdout


def test_un_refus_dans_le_meme_evenement_reste_une_limite(depot: Depot) -> None:
    """Le filtre écarte l'information, pas le refus — sinon il masquerait ce qu'il doit détecter."""
    futur = int(time.time()) + 3600
    f = _fixture_limite(
        depot, "refus",
        _evenement_fenetre("rejected", futur) + "\n"
        + '{"type":"result","is_error":true,"result":"usage limit reached"}\n',
    )
    r = depot.lance("run.sh", "--test-reprise", f)
    assert r.returncode == 0, r.stdout
    assert "LIMITE D'USAGE détectée" in r.stdout
    secondes = int(r.stdout.split("(")[1].split(" s)")[0])
    assert 3500 < secondes <= 3600 + 130, "l'heure de reset du refus reste celle qu'on attend"


def test_une_session_reussie_ne_part_jamais_en_reprise(depot: Depot) -> None:
    """La ceinture des bretelles : sortie en 0 ⇒ verdict GitLab, sans passer par la détection.

    La session dit ici « usage limit reached » dans son message final — le marqueur SURVIT au
    filtre, et c'est voulu : une session qui travaille justement sur les limites d'usage en écrit
    les mots (celle-ci en est un cas réel). Seule la sortie en 0 doit alors la sauver.

    Le plafond est mis à 1 s pour qu'une régression échoue *vite* : toute limite détectée
    dépasserait alors le cumul autorisé et arrêterait le run au lieu de dormir jusqu'au reset.
    """
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    compteur = depot.racine.parent / "appels-succes"
    futur = int(time.time()) + 3600
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '%s\\n' '{_evenement_fenetre("allowed", futur)}'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2,'
        printf '"result":"corrigé le message usage limit reached"}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "succes",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PLAFOND": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert compteur.read_text().strip() == "1", "une seule session : aucune reprise"
    assert "limite d'usage" not in r.stdout.lower()
    resume = (depot.racine / ".maestro/orchestrate/succes/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume, "le verdict GitLab est lu — le ticket livré n'est pas dit en échec"


def test_apres_la_limite_la_session_reprend_au_lieu_de_recommencer(depot: Depot) -> None:
    depot.ticket(130, "Ticket interrompu")
    compteur = depot.racine.parent / "appels"
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{{"is_error":false,"subtype":"success","total_cost_usd":2}}'; exit 0
        fi
        printf '{{"is_error":true,"total_cost_usd":1,"result":"Claude AI usage limit reached"}}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "reprise",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert compteur.read_text().strip() == "2", "une session neuve, puis UNE reprise"
    assert "reprise 1/3" in r.stdout
    assert "limite d'usage atteinte" in r.stdout


def test_une_reprise_impossible_repart_a_froid(depot: Depot) -> None:
    """Session perdue : on redémarre, le travail déjà commité étant sur la branche."""
    depot.ticket(130, "Ticket interrompu")
    compteur = depot.racine.parent / "appels-froid"
    claude = _claude_stub(depot, f"""
        n=$(( $(cat "{compteur}" 2>/dev/null || echo 0) + 1 )); echo "$n" > "{compteur}"
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{{"is_error":true,"result":"No conversation found with session ID"}}'; exit 1
        fi
        if [ "$n" = 1 ]; then
          printf '{{"is_error":true,"result":"usage limit reached"}}'; exit 1
        fi
        printf '{{"is_error":false,"subtype":"success"}}'; exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "froid",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert compteur.read_text().strip() == "3", "neuve, reprise refusée, puis redémarrage à froid"
    assert "redémarrage à froid" in r.stdout


def test_les_reprises_sont_plafonnees(depot: Depot) -> None:
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, """
        printf '{"is_error":true,"result":"usage limit reached"}'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "plafond", "--max-reprises", "2",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert "après 2 reprise(s)" in r.stdout
    assert r.returncode == 1


def test_une_attente_trop_longue_est_lue_comme_la_limite_hebdomadaire(depot: Depot) -> None:
    """Au-delà de 5 h 30, on ne dort pas des jours : le run s'arrête et se relance plus tard."""
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, """
        LOIN=$(( $(date +%s) + 90000 ))
        printf '{"is_error":true,"result":"Claude AI usage limit reached|%s"}' "$LOIN"
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne"), (2, 131, "-", "moyenne")])
    debut = time.monotonic()
    # `--concurrence 1` explicite depuis #455 : deux tickets hors lot sont indépendants, donc la
    # dérivation les ferait partir ensemble et #131 se retrouverait au bilan — ce que la dernière
    # assertion vérifie justement ne PAS arriver. Ce test regarde le plafond d'attente, pas
    # l'ordonnanceur : il demande le régime dans lequel sa question a un sens.
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "hebdo", "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert time.monotonic() - debut < 60, "le run ne doit jamais attendre une limite hebdomadaire"
    assert "Limite hebdomadaire" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/hebdo/resume.tsv").read_text(encoding="utf-8")
    assert "131" not in resume, "le reste du plan est laissé intact pour un prochain run"


# =====================================================================================
# Le lancement détaché (#173)
# =====================================================================================

def test_detach_ecrit_un_lanceur_et_rend_la_main_sans_calculer_le_plan(depot: Depot) -> None:
    """`--detach` prépare et délègue : le plan est figé par le run détaché, pas ici."""
    claude = _claude_stub(depot, 'echo "aucune session ne démarre côté pilote" >&2\nexit 1\n')
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "detache", "--max", "1",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr

    lanceur = depot.racine / ".maestro/orchestrate/detache/lancer.sh"
    assert lanceur.exists(), "la console n'exécute qu'un lanceur écrit sur disque"
    corps = lanceur.read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "run.sh" in commande and "--max 1" in commande, "les options d'origine sont repassées"
    assert "--detach" not in commande, "sans quoi la console relancerait une console, à l'infini"
    assert commande.count("--run-id") == 1, "le run-id est imposé une fois, pas repris en double"
    assert "MAESTRO_ORCHESTRATE_COULEUR=1" in corps, "la fenêtre est un écran : couleurs gardées"

    dossier = depot.racine / ".maestro/orchestrate/detache"
    assert not (dossier / "plan.tsv").exists(), "deux calculs du plan risqueraient de diverger"
    # Comparaison sur la fin du chemin : bash le rend en style MSYS, Python en style Windows.
    recu = (depot.fixtures / "spawn.txt").read_text(encoding="utf-8").strip()
    assert recu.endswith("/detache/lancer.sh"), f"la console reçoit le lanceur, pas {recu}"
    assert "reprendre" in r.stdout, "le filet en cas de console tuée est annoncé au lancement"


def test_detach_avec_dry_run_reste_en_lecture_seule(depot: Depot) -> None:
    """Rien à détacher pour un plan qui s'affiche en une seconde — et aucune trace laissée."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--detach", "--dry-run", "--plan", plan, "--run-id", "sec",
        env={"MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout, "le plan s'affiche en direct"
    assert not (depot.fixtures / "spawn.txt").exists(), "aucune console n'est ouverte"
    assert not (depot.racine / ".maestro/orchestrate/sec").exists()


def test_un_lancement_detache_en_echec_ne_laisse_pas_de_run_fantome(depot: Depot) -> None:
    """Un journal annoncé mais jamais écrit vaudrait pire que pas de journal du tout."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot, "exit 1\n")
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "rate",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 1
    assert "n'a pas démarré" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/rate").exists()


def test_le_lanceur_detache_lance_vraiment_le_run(depot: Depot) -> None:
    """Le lanceur est le seul lien entre le pilote et le run : on l'exécute pour de bon."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1.25}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    spawn = _spawn_stub(depot)
    depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "vrai",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )

    lanceur = depot.racine / ".maestro/orchestrate/vrai/lancer.sh"
    r = subprocess.run(
        [BASH, str(lanceur)],
        cwd=depot.racine,
        env={**depot.env, "MAESTRO_CLAUDE_BIN": claude},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/vrai/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume
    journal = (depot.racine / ".maestro/orchestrate/vrai/run.log").read_text(encoding="utf-8")
    assert "#130" in journal, "la sortie survit à la fermeture de la fenêtre"

    # La fenêtre est un écran (couleurs), le journal se relit plus tard et souvent par un outil
    # (pas de codes ANSI) — `tee` les enverrait pourtant aux deux.
    assert "\x1b[" in r.stdout, "la console garde ses couleurs malgré le tee"
    assert "\x1b[" not in journal, "le journal est décoloré en fin de run"


def test_sans_le_marqueur_la_sortie_reste_sans_couleur(depot: Depot) -> None:
    """Le contre-test : hors console détachée, une sortie redirigée ne doit pas être colorée."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "terne")
    assert r.returncode == 0, r.stderr
    assert "#130" in r.stdout
    assert "\x1b[" not in r.stdout


def test_le_conftest_neutralise_la_couleur_heritee_du_poste() -> None:
    """Le contre-test ci-dessus ne tient que si le poste ne pose pas la variable (#236).

    `MAESTRO_ORCHESTRATE_COULEUR=1` dans le bloc `env` d'un `.claude/settings.local.json` fuit dans
    l'environnement de toute session de ce poste, donc dans les sous-processus lancés ici : la
    sortie ressort truffée de codes ANSI et le test précédent échoue **en local seulement**, la CI
    restant verte. Quatre sessions ont rouvert la même enquête sur cette fausse alerte — c'est le
    dépôt, pas chaque run, qui doit la tarir.

    Vide plutôt que supprimée, comme les clés Langfuse : `run.sh` lit
    `${MAESTRO_ORCHESTRATE_COULEUR:-0}`, pour qui vide et absente valent 0, et une valeur vide
    traverse sans surprise les `env={**os.environ, …}` de ces tests.
    """
    assert os.environ.get(CLE_COULEUR_ORCHESTRATE) == "", (
        "le conftest doit vider la variable à l'import, avant le premier module de test "
        "(tests/conftest.py, #236)"
    )


# =====================================================================================
# Le flux d'activité en direct (#176)
# =====================================================================================

def _flux(dossier: Path, iid: int = 130) -> str:
    """Le flux archivé d'un ticket, qu'il soit encore brut ou déjà compacté (#198).

    Ces tests-ci portent sur ce que le flux CONTIENT ; son format de stockage est le sujet de la
    section « journal.sh », qui vérifie explicitement la compaction.
    """
    brut = dossier / f"{iid}.jsonl"
    if brut.exists():
        return brut.read_text(encoding="utf-8")
    return gzip.decompress((dossier / f"{iid}.jsonl.gz").read_bytes()).decode("utf-8")


def _stub_flux(depot: Depot) -> str:
    """Un bouchon qui émet un vrai flux stream-json : plusieurs événements, `result` en dernier.

    Le premier événement porte un `total_cost_usd` LEURRE : c'est la régression que ce lot peut
    introduire (`champ_json` prend la première occurrence d'une clé), et elle serait silencieuse.
    """
    # Concaténation implicite : chaque ligne de source reste courte, le JSON produit tient sur une
    # seule ligne — c'est le format du flux, un objet par ligne.
    flux = "\n".join([
        '{"type":"system","subtype":"init","total_cost_usd":0.01}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read",'
        '"input":{"file_path":"docs/21-configuration-mcp.md"}}]}}',
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
        '"input":{"file_path":"core/models/mcp.py"}},{"type":"tool_use","name":"Bash",'
        '"input":{"command":"pytest -q"}}]}}',
        '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":4.2}',
    ])
    return _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        cat <<'FLUX'
{flux}
FLUX
        exit 0
    """)


def test_le_flux_donne_une_ligne_par_action_et_garde_le_resultat_final(depot: Depot) -> None:
    """Le flot d'une ligne par appel d'outil : depuis #240 il ne survit qu'en `--verbeux`.

    C'est le mode de diagnostic qui le porte désormais — le comportement par défaut est vérifié
    par la section suivante, qui exige justement son absence.
    """
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    # 1. La console dit ce que la session fabrique, au lieu de rester muette.
    assert "· Read docs/21-configuration-mcp.md" in r.stdout
    assert "· Edit core/models/mcp.py" in r.stdout
    assert "· Bash pytest -q" in r.stdout, "les tool_use multiples d'un événement sont tous vus"

    dossier = depot.racine / ".maestro/orchestrate/flux"
    # 2. Le flux brut est archivé en entier…
    lignes = [x for x in _flux(dossier).splitlines() if x]
    assert len(lignes) == 4

    # 3. …mais <iid>.json ne porte QUE le résultat final : sinon le coût lu serait le leurre.
    final = (dossier / "130.json").read_text(encoding="utf-8")
    assert '"type":"result"' in final
    assert "0.01" not in final
    resume = (dossier / "resume.tsv").read_text(encoding="utf-8")
    assert "4.2" in resume and "0.01" not in resume


# =====================================================================================
# Une valeur de `tool_use` est une chaîne JSON, donc ÉCHAPPÉE (#496)
# =====================================================================================
#
# L'extraction d'origine (`[^"]*` refermé par `cut -d'"' -f4`) s'arrêtait au premier guillemet
# ÉCHAPPÉ : toute commande dont un argument est entre guillemets était rendue amputée. Mesuré sur le
# run 20260824-192234 — 715 appels d'outil sur 2 979 (24 %) rendus tronqués, dont 278 sous la MÊME
# chaîne « cd \ » et 181 sous « grep -n \ ».
#
# Le second effet pèse plus lourd que l'affichage : `formate_flux` ne republie que sur CHANGEMENT
# d'action, donc dix appels différents rendus identiques ne produisaient AUCUNE republication — la
# ligne se figeait pendant que le chrono courait, et l'écran donnait à lire UNE commande de
# plusieurs minutes là où il y en avait dix de quelques secondes. C'est ce faux diagnostic de
# lenteur qui a motivé le chantier #495.

# L'échantillon fautif : deux appels DIFFÉRENTS dont le premier argument est entre guillemets.
_ECHANTILLON_GUILLEMETS = (
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
    '"input":{"command":"cd \\"apps/web\\" && npm test"}}]}}',
    '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash",'
    '"input":{"command":"cd \\"apps/web\\" && npx vitest run runs-liste.test.tsx"}}]}}',
)


def test_le_motif_d_origine_amputait_la_commande_au_guillemet_echappe() -> None:
    """Le motif prouvé sur l'échantillon fautif, AVANT de vérifier qu'il ne tombe plus.

    Sans cette moitié, le test suivant serait un ✓ sur une question jamais posée : rien ne dirait
    que son échantillon est bien celui qui faisait tomber `outils_de`, et un jour où quelqu'un
    l'aurait adouci en retirant les guillemets, il resterait vert sans plus rien garder. On rejoue
    donc ici l'extraction d'ORIGINE — la classe `[^"]*`, puis le quatrième champ découpé sur le
    guillemet — et on exige qu'elle rende le préfixe, identique pour les deux appels.
    """
    origine = re.compile(r'"(?:file_path|command|pattern|path|url|description)":"[^"]*"')
    rendus = []
    for evenement in _ECHANTILLON_GUILLEMETS:
        premier = origine.search(evenement)
        assert premier is not None, "l'échantillon doit au moins matcher l'ancien motif"
        rendus.append(premier.group(0).split('"')[3])  # l'exact `cut -d'"' -f4`
    assert rendus == ["cd \\", "cd \\"], (
        f"l'échantillon doit être celui qui tombe — obtenu {rendus!r}"
    )


def test_une_commande_a_guillemets_s_affiche_entiere_et_republie_a_chaque_appel(
    depot: Depot,
) -> None:
    """Les deux critères de #496 sur un même flux : la commande entière, deux actions distinctes.

    La publication se vérifie sur `<iid>.vue`, qui garde la DERNIÈRE action publiée : avant le
    correctif il y serait resté la première (`Bash cd \\`), la garde « on ne publie que sur
    changement » n'ayant jamais rien vu changer. Y trouver la SECONDE commande prouve donc les deux
    choses d'un coup — la valeur est rendue entière, et le changement a été vu.

    Le troisième appel est un chemin ABSOLU, comme l'est tout `file_path` par construction : il
    couvre l'autre moitié du correctif. Le laisser entier ferait reculer la troncature de `tronque`
    jusque dans le chemin du worktree (~84 colonnes pour 64 affichées), et TOUS les Read/Edit d'un
    run se ressembleraient jusqu'à la coupe — le même écran figé, par une autre cause.
    """
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    absolu = json.dumps(str(depot.racine / "docs" / "a.md"))  # échappé comme le CLI l'émet
    flux = "\n".join([
        *_ECHANTILLON_GUILLEMETS,
        '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit",'
        f'"input":{{"file_path":{absolu}}}}}]}}}}',
        '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}',
    ])
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        cat <<'FLUX'
{flux}
FLUX
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "echappe", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    # 1. La commande est rendue ENTIÈRE, guillemets compris — et non son préfixe.
    assert '· Bash cd "apps/web" && npm test' in r.stdout
    assert '· Bash cd "apps/web" && npx vitest run runs-liste.test.tsx' in r.stdout
    assert "· Bash cd \\" not in r.stdout, "le préfixe amputé ne doit plus apparaître"

    # 2. Le chemin absolu perd le worktree, et rien d'autre : c'est ce qui garde deux fichiers
    #    voisins distincts une fois la ligne coupée.
    edit = [x for x in r.stdout.splitlines() if "· Edit" in x]
    assert len(edit) == 1, f"un seul Edit attendu — obtenu {edit!r}"
    assert edit[0].strip().endswith("a.md"), edit[0]
    assert str(depot.racine) not in edit[0], "le chemin du worktree n'apprend rien à personne"

    # 3. Et l'action PUBLIÉE est la dernière, donc la garde n'a pas été court-circuitée.
    publie = (depot.racine / ".maestro/orchestrate/echappe/130.vue").read_text(encoding="utf-8")
    assert publie.startswith("."), publie
    assert publie.split("\t", 1)[1].strip().endswith("a.md"), (
        f"la dernière action doit avoir été publiée — obtenu {publie!r}"
    )


def test_une_limite_d_usage_annoncee_dans_le_flux_est_detectee(depot: Depot) -> None:
    """Le signal peut n'apparaître qu'au fil du flux, sans jamais atteindre l'objet `result`."""
    depot.ticket(130, "Ticket bloqué")
    claude = _claude_stub(depot, r"""
        LOIN=$(( $(date +%s) + 120 ))
        printf '{"type":"system","subtype":"init"}\n'
        printf '{"type":"assistant","message":{"content":[{"type":"text",'
        printf '"text":"Claude AI usage limit reached|%s"}]}}\n' "$LOIN"
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "limite-flux", "--max-reprises", "0",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    # Avec --max-reprises 0 la boucle renonce tout de suite : ce qui est vérifié ici, c'est qu'elle
    # a bien RECONNU une limite d'usage (et non un échec ordinaire, qui ne la mentionnerait pas).
    assert "limite d'usage" in r.stdout, "le flux est lu, pas seulement le résultat final"


def test_un_flux_sans_saut_de_ligne_final_ne_perd_pas_son_resultat(depot: Depot) -> None:
    """La dernière ligne d'un flux EST l'objet `result` : la perdre, c'est perdre le verdict."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"system","subtype":"init"}}\\n'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":7.75}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "tronque",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/tronque"
    assert '"type":"result"' in (dossier / "130.json").read_text(encoding="utf-8")
    assert "7.75" in (dossier / "resume.tsv").read_text(encoding="utf-8")


def test_sans_objet_result_le_dernier_evenement_en_tient_lieu(depot: Depot) -> None:
    """Repli pour un CLI plus ancien (ou un flux coupé) : mieux vaut la dernière ligne que rien."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"system","subtype":"init","total_cost_usd":0.01}}\\n'
        printf '{{"is_error":false,"subtype":"success","total_cost_usd":2.5}}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "repli",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/repli/resume.tsv").read_text(encoding="utf-8")
    assert "2.5" in resume and "0.01" not in resume, "le repli prend la dernière ligne, pas la 1re"


def test_la_session_reprise_passe_aussi_par_le_flux(depot: Depot) -> None:
    """Les DEUX invocations de `lance_session` sont concernées : sans quoi la console redeviendrait
    muette juste après une reprise — exactement le moment où l'on regarde."""
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"assistant","message":{{"content":[{{"type":"tool_use",'
          printf '"name":"Bash","input":{{"command":"pytest -q"}}}}]}}}}\\n'
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "flux-reprise", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Bash pytest -q" in r.stdout, "la reprise doit rester bavarde, elle aussi"
    dossier = depot.racine / ".maestro/orchestrate/flux-reprise"
    assert "6" in (dossier / "resume.tsv").read_text(encoding="utf-8")
    # Chaque tentative repart sur un flux propre, et c'est porteur : la détection de limite grepe
    # le `.jsonl` entier, donc un marqueur laissé par la tentative précédente ferait attendre puis
    # reprendre une session qui vient pourtant d'aboutir — indéfiniment.
    jsonl = _flux(dossier)
    assert "usage limit reached" not in jsonl, "le flux de la tentative précédente doit être effacé"


# =====================================================================================
# La console d'un run : une checklist vivante (#240)
# =====================================================================================
#
# Ces tests n'ont pas de pseudo-terminal, et n'en ont pas besoin : `run.sh` choisit le descripteur
# de ses frames, et `MAESTRO_ORCHESTRATE_CONSOLE` le fait pointer sur un FICHIER. Ce qu'une console
# aurait reçu se relit donc à l'octet près — et son absence dans `stdout` est vérifiable, ce qui est
# l'invariant central : `stdout` finit dans `run.log`, où une frame n'a rien à faire.

def _console(depot: Depot) -> Path:
    """Le fichier qui tient lieu de console pour les frames."""
    return depot.racine.parent / "console.txt"


def _stub_livre(depot: Depot) -> str:
    """Un bouchon qui livre le ticket qu'on lui confie, quel qu'il soit.

    L'iid se lit dans le prompt : un plan à plusieurs tickets réutilise le même bouchon, et poser
    « En revue » sur tous d'entrée ferait sauter les suivants avant qu'ils soient pris.
    """
    gabarit = _statut_json("%s", "En revue")
    return _claude_stub(depot, f"""
        iid=$(printf '%s\\n' "$@" | grep -o 'GitLab #[0-9]*' | head -1 | tr -dc '0-9')
        printf '{gabarit}' "$iid" > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '{{"type":"assistant","message":{{"content":[{{"type":"tool_use",'
        printf '"name":"Read","input":{{"file_path":"docs/21-configuration-mcp.md"}}}}]}}}}\\n'
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}\\n'
        exit 0
    """)


def test_par_defaut_le_flot_d_outils_ne_s_imprime_plus_mais_rien_n_est_perdu(depot: Depot) -> None:
    """Le critère central de #240 : l'écran cesse de défiler, le journal ne perd rien."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "muet",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" not in r.stdout
    assert "· Edit core/models/mcp.py" not in r.stdout

    dossier = depot.racine / ".maestro/orchestrate/muet"
    # Le flux brut est intégral : c'est lui qui porte le diagnostic, et il n'a pas changé.
    assert len([x for x in _flux(dossier).splitlines() if x]) == 4
    # Et `<iid>.json` ne porte toujours que le résultat final — le coût, le verdict et la détection
    # de limite d'usage le lisent (« à ne pas casser » du ticket).
    final = (dossier / "130.json").read_text(encoding="utf-8")
    assert '"type":"result"' in final and "0.01" not in final


def test_la_variable_d_environnement_vaut_l_option_verbeuse(depot: Depot) -> None:
    """`MAESTRO_ORCHESTRATE_VERBEUX=1` : de quoi rallumer le flot sans retoucher la ligne de
    commande d'un run déjà lancé par un lanceur."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "verbeux-env",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_VERBEUX": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" in r.stdout


def test_sans_console_la_vue_retombe_en_plein_texte(depot: Depot) -> None:
    """Détachement Unix, CI, tests : personne ne peut redessiner. La checklist s'imprime alors une
    fois par ticket, en clair — et SURTOUT sans une seule séquence de repositionnement, que le
    `sed` final du lanceur ne retire pas (il ne connaît que les codes de couleur)."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "texte",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot)})
    assert r.returncode == 0, r.stdout + r.stderr
    # Le marqueur « > » du ticket courant, et non le seul « 1. #130 » : le récapitulatif du plan
    # imprimé au démarrage porte déjà celui-là, et le test passerait sans qu'aucune vue soit rendue.
    assert ">  1. #130" in r.stdout, "la checklist du plan est rendue en plein texte"
    assert "\x1b[" not in r.stdout, "aucune séquence ANSI ne doit atterrir dans run.log"


def test_avec_une_console_les_frames_y_vont_et_jamais_dans_run_log(depot: Depot) -> None:
    """Les deux flux sont séparés : les frames vers la console, la trace permanente vers stdout."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "console",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert ". #130" in vue, "la checklist est dessinée sur la console"
    assert "\x1b[" in vue, "et elle y est redessinée — c'est tout l'objet du descripteur dédié"

    assert "\x1b[" not in r.stdout, "aucune frame dans le journal"
    assert ">  1. #130" not in r.stdout, "ni la vue plein texte en double : la console la porte"
    # Ce que `run.log` garde, lui : l'en-tête du ticket et son verdict — de quoi relire un run.
    assert "[1/1] #130" in r.stdout


def test_la_checklist_porte_les_verdicts_deja_rendus_et_le_cumul_du_run(depot: Depot) -> None:
    """Au deuxième ticket, le premier n'est plus « à venir » : il porte sa marque, sa PR et son
    coût, et le pied dit où en est le run — c'est l'information que le flot d'outils avait chassée
    de l'écran."""
    for iid in (130, 131):
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    # `--concurrence 1` explicite depuis #455 : « au deuxième ticket, le premier n'est plus à
    # venir » suppose que le premier soit soldé quand le second démarre. Dérivés à deux, ils
    # partent ensemble et la question ne se pose plus. La vue à N tickets a ses tests, plus bas.
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "checklist", "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "✓  1. #130" in vue, "le ticket livré porte sa marque dans la checklist"
    assert "PR #99" in vue, "avec sa PR"
    assert "2.00 $" in vue, "et son coût, arrondi comme dans resume.tsv"
    assert "reste " in vue and "✓ 1" in vue, "le pied donne le cumul du run"


def test_l_attente_et_la_reprise_sont_des_etats_de_la_vue(depot: Depot) -> None:
    """Une limite d'usage se compte en heures : l'écran ne doit ni paraître figé, ni laisser croire
    que la session rouverte est un ticket qui démarre. Et le chrono suit le TICKET — sans quoi il
    repartirait de zéro à chaque tentative, alors que c'est la durée du ticket qu'on consigne.

    Le palier est de TROIS secondes et non d'une (#292). Depuis #290 la session ne dessine plus,
    elle *publie* son état et le pilote l'échantillonne ; depuis #291 le délai annoncé est ce qui
    reste du rendez-vous, donc `fin - maintenant` — deux horloges lues à quelques forks d'écart,
    ce qui coûte
    sous MSYS de quoi franchir une seconde entière. À un palier d'une seconde, l'attente retombait à
    « 0s » : elle était publiée puis écrasée par la reprise dans le même souffle, et aucune frame ne
    pouvait tomber dessus. Ce n'était pas la vue qui manquait l'état, c'était le décor qui n'en
    créait plus. Trois secondes laissent une quinzaine de tours de pilote — l'attente redevient ce
    qu'elle est en production, un état qui dure.
    """
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    gabarit = _statut_json("%s", "En revue")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          iid=$(printf '%s\\n' "$@" | grep -o 'GitLab #[0-9]*' | head -1 | tr -dc '0-9')
          printf '{gabarit}' "${{iid:-130}}" > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "attente",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "3",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "en attente de la fin de la limite d'usage" in vue, "l'attente est un état, pas un gel"
    assert "=  1. #130" in vue, "et son marqueur est fixe : une session en pause ne tourne pas"
    assert "reprise 1/3" in vue, "et la reprise en est un autre"


def test_le_mode_verbeux_eteint_la_vue_vivante(depot: Depot) -> None:
    """Les deux se disputeraient l'écran — et c'est justement quand on lit chaque ligne qu'on ne
    veut rien qui bouge."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "verbeux-vue", "--verbeux",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "· Read docs/21-configuration-mcp.md" in r.stdout
    assert not console.exists() or console.read_text(encoding="utf-8") == "", (
        "aucune frame ne doit être dessinée en mode verbeux"
    )


# =====================================================================================
# Un bloc qui tient en place, et rien d'autre à l'écran (#284)
# =====================================================================================
#
# #240 avait donné à la console son tableau de bord ; il restait trois façons pour lui de salir
# l'écran, dont deux invisibles à la relecture de `run.log` — c'est justement pour ça qu'elles
# avaient tenu. Ces tests les fixent à l'octet près, sur le fichier qui tient lieu de console.

def test_la_frame_ne_se_termine_pas_par_un_saut_de_ligne(depot: Depot) -> None:
    """Le défaut coûteux : un « \\n » écrit sur la rangée du bas fait défiler le tampon. Le bloc vit
    précisément en bas de l'écran, et il se redessinait plusieurs fois par seconde — l'écran
    paraissait stable pendant que l'historique se remplissait d'une copie du bloc par frame."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sans-defilement",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    # Le pied est la dernière ligne du bloc : il se termine par « efface jusqu'au bout », et rien
    # d'autre. C'est ce qui laisse le curseur SUR la ligne, d'où le repositionnement en hauteur - 1.
    assert "reste 0\x1b[K" in vue, "le pied ferme la frame en effaçant la fin de ligne"
    assert "reste 0\x1b[K\n" not in vue, (
        "une frame finie par un saut de ligne pousse une ligne dans l'historique à chaque redessin"
    )
    # « ESC[F » nu vaut « remonte d'une ligne » : la hauteur est toujours dite explicitement.
    assert "\x1b[F" not in vue, "un repositionnement sans hauteur remonterait d'une ligne"


def test_le_curseur_est_cache_pendant_la_vue_et_rendu_en_sortant(depot: Depot) -> None:
    """Redessiner, c'est faire sauter le curseur d'un bout à l'autre du bloc — et c'est ce
    mouvement, plus que le texte, qui donnait à la console son air agité. Il est rendu à la sortie :
    une fenêtre gardée ouverte après le run ne doit pas rester sans curseur."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "curseur",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "\x1b[?25l" in vue, "le curseur est caché dès que la vue prend l'écran"
    assert vue.rindex("\x1b[?25h") > vue.rindex("\x1b[?25l"), (
        "et rendu APRÈS — le dernier geste de la vue, sinon la console reste amputée"
    )
    assert "\x1b[?25" not in r.stdout, "rien de tout cela n'a à finir dans run.log"


def test_le_battement_va_dans_le_journal_et_non_a_l_ecran(depot: Depot) -> None:
    """Le battement est fait pour `run.log`, où il est la seule trace d'une session qui dure. À
    l'écran il n'apprenait rien que le bloc ne dise déjà en plus frais, et il coûtait double : une
    ligne poussée sous le bloc chaque minute, plus un redessin « à neuf » qui laissait le bloc
    précédent derrière lui."""
    depot.ticket(130, "Ticket 130")
    depot.mr("feat/130-ticket-130", "opened")
    gabarit = _statut_json("130", "En revue")
    # Une session qui dure : de quoi laisser passer deux battements d'une seconde.
    claude = _claude_stub(depot, f"""
        printf '%s' '{gabarit}' > "$MAESTRO_FIXTURES/owner-130.json"
        sleep 2.5
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}\\n'
        exit 0
    """)
    console = _console(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "battement",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_BATTEMENT": "1",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = console.read_text(encoding="utf-8", errors="replace")
    # Sans descripteur dédié (le lanceur détaché en ouvre un), le battement retombe sur stdout —
    # c'est-à-dire sur le journal, exactement là où il sert.
    assert "  … " in r.stdout, "le journal garde la trace d'une session qui dure"
    assert "  … " not in vue, "l'écran, lui, n'en veut pas : le bloc dit déjà la même chose"


def test_le_lanceur_detache_ouvre_un_descripteur_sur_le_journal(depot: Depot) -> None:
    """Ce descripteur est ce qui permet d'écrire au journal SANS passer par `tee` — donc sans
    passer par l'écran — et d'écrire soi-même sur la console les lignes qui doivent y être : `tee`
    est un autre processus, et une ligne qui arrive après la frame suivante dédouble le bloc."""
    claude = _claude_stub(depot, "exit 1\n")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance(
        "run.sh", "--detach", "--plan", plan, "--run-id", "fd-journal",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_SPAWN": _spawn_stub(depot)},
    )
    assert r.returncode == 0, r.stdout + r.stderr

    corps = (depot.racine / ".maestro/orchestrate/fd-journal/lancer.sh").read_text(encoding="utf-8")
    assert "exec 4>&1" in corps and "MAESTRO_ORCHESTRATE_CONSOLE_FD=4" in corps
    assert "exec 5>>" in corps and "MAESTRO_ORCHESTRATE_TRACE_FD=5" in corps
    # Filet de dernier recours : `run.sh` rend le curseur par un trap, mais un trap ne s'exécute pas
    # sur un SIGKILL — et c'est ainsi qu'un run est arrêté par un autre (§11.9). La fenêtre survit à
    # son run : elle ne doit pas rester sans curseur.
    assert "\\033[?25h" in corps and ">&4" in corps, (
        "la fenêtre récupère son curseur quoi qu'il arrive"
    )
    # Les deux descripteurs sont ouverts AVANT le tube : le 4 doit désigner la fenêtre et non le
    # tube vers `tee`, et le 5 le fichier de journal lui-même.
    lignes = corps.splitlines()
    commande = next(i for i, ligne in enumerate(lignes) if ligne.startswith("bash "))
    assert next(i for i, ligne in enumerate(lignes) if ligne.startswith("exec 4>")) < commande
    assert next(i for i, ligne in enumerate(lignes) if ligne.startswith("exec 5>")) < commande


# =====================================================================================
# status.sh — savoir où en est un run, hors de sa console (#177)
# =====================================================================================

def _run_dir(
    depot: Depot,
    run_id: str,
    plan: list[tuple[int, int, str, str]],
    *,
    resume: list[tuple] | None = None,
    sessions: tuple[int, ...] = (),
    journal: str | None = None,
    age: int = 0,
) -> Path:
    """Monte à la main un répertoire de run, tel que `run.sh` le laisse derrière lui.

    Écrire ces fichiers plutôt que de lancer un vrai run est ce qui permet de poser les cas que
    `status.sh` doit distinguer — dont ceux qu'un run ne produit qu'en tombant en panne. `age`
    vieillit toutes les dates de modification : c'est le seul levier sur les états qui se
    déduisent du silence (« interrompu », « en cours ? »).
    """
    dossier = depot.racine / ".maestro/orchestrate" / run_id
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.tsv").write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        + "".join(f"{r}\t{i}\t{p}\t{prio}\t{_groupe(p, r)}\tTicket {i}\n"
                  for r, i, p, prio in plan),
        encoding="utf-8",
        newline="\n",
    )
    if resume is not None:
        (dossier / "resume.tsv").write_text(
            "# iid\tverdict\tmr\tduree_s\tcout_usd\traison\n"
            + "".join("\t".join(str(c) for c in ligne) + "\n" for ligne in resume),
            encoding="utf-8",
            newline="\n",
        )
    for iid in sessions:
        (dossier / f"{iid}.session").write_text(
            "11111111-2222-4333-a444-555555555555", encoding="utf-8", newline="\n"
        )
        (dossier / f"{iid}.log").write_text("", encoding="utf-8", newline="\n")
    if journal is not None:
        (dossier / "run.log").write_text(journal, encoding="utf-8", newline="\n")
    if age:
        quand = time.time() - age
        for chemin in (*sorted(dossier.rglob("*")), dossier):
            os.utime(chemin, (quand, quand))
    return dossier


def _init_git(depot: Depot, branche: str) -> None:
    """Fait du dépôt jetable un vrai dépôt git, posé sur `branche`, avec un `origin/main` local.

    Aucun distant : `refs/remotes/origin/main` est une simple référence locale — c'est tout ce que
    `status.sh` lit pour compter les commits d'avance, et ça évite un dépôt *bare* de plus.
    """
    assert GIT is not None

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True
        )

    git("init", "--quiet", "--initial-branch=main")
    git("config", "user.email", "test@maestro.invalid")
    git("config", "user.name", "Maestro Test")
    # Le journal du run vit sous .maestro/ : ignoré ici comme dans le vrai dépôt, sans quoi il
    # apparaîtrait dans les « fichiers modifiés » du worktree.
    (depot.racine / ".gitignore").write_text(".maestro/\n", encoding="utf-8", newline="\n")
    git("add", "-A")
    git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: depot jetable")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "--quiet", "-b", branche)


def test_aucun_run_est_un_cas_normal_pas_une_erreur(depot: Depot) -> None:
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "Aucun run d'orchestration" in r.stdout
    assert "run.sh --dry-run" in r.stdout, "on dit comment en lancer un"


def test_un_run_en_cours_montre_le_ticket_courant_le_reste_et_le_bilan(depot: Depot) -> None:
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(
        depot,
        "20260729-090000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne"), (3, 132, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        sessions=(131,),
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "— en cours" in r.stdout
    assert "En cours — #131" in r.stdout
    assert "Reste au plan (1)" in r.stdout and "#132" in r.stdout
    assert "Traités (1)" in r.stdout and "#130" in r.stdout
    assert "GitHub     ticket « En cours »" in r.stdout, "le statut du ticket courant est relu"
    assert "status.sh --watch" in r.stdout and "touch" in r.stdout, "suivre / arrêter sont donnés"


def test_un_run_termine_rend_son_bilan_et_ne_se_dit_plus_en_cours(depot: Depot) -> None:
    _run_dir(
        depot,
        "20260729-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[
            (130, "OK", "99", 620, "3.50", "-"),
            (131, "ECHEC", "-", 300, "1.20", "PR « aucune », cycle de vie « En cours »"),
        ],
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "— terminé" in r.stdout
    assert "Traités (2)" in r.stdout
    assert "10min20" in r.stdout, "la durée d'un ticket est rendue lisible"
    assert "review-queue" in r.stdout, "le travail d'un run terminé attend une revue humaine"
    assert "En cours — " not in r.stdout, "plus aucun ticket n'est en cours"


def test_un_run_detache_arrete_est_lu_dans_son_journal(depot: Depot) -> None:
    """Le code de sortie écrit par le lanceur tranche : sans lui, un run coupé en plein plan
    passerait pour « interrompu » alors qu'il s'est arrêté de lui-même (limite hebdomadaire…)."""
    _run_dir(
        depot,
        "20260729-110000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        journal="[1/2] #130\n\n--- run 20260729-110000 terminé (code 1) ---\n",
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "terminé (code 1)" in r.stdout
    assert "Reste au plan (1)" in r.stdout, "ce qui n'a pas été traité reste visible"


def test_un_run_sans_activite_recente_est_dit_interrompu(depot: Depot) -> None:
    """Aucun ticket pris en main et plus rien d'écrit : le run est mort sans le dire."""
    _run_dir(
        depot,
        "20260729-120000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
        age=7200,
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "interrompu" in r.stdout
    # Le filet, c'est le plan resté sur disque — mais on le désigne par son RUN-ID (#204), pas par
    # le chemin de son plan : un argument qui se retient est un argument qu'on retape.
    assert "reprendre" in r.stdout and "--resume 20260729-120000" in r.stdout


def test_un_silence_prolonge_fait_douter_l_en_tete_sans_trancher(depot: Depot) -> None:
    """Sans PID, une session qui réfléchit et une session morte se ressemblent : on le dit."""
    depot.ticket(131, "Ticket peut-être bloqué", statut="En cours")
    _run_dir(
        depot,
        "20260729-130000",
        [(1, 131, "-", "moyenne")],
        resume=[],
        sessions=(131,),
        age=7200,
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "en cours ?" in r.stdout, "le doute est dans l'en-tête, pas seulement plus bas"
    assert "rien d'écrit depuis 2h00" in r.stdout
    assert "peut-être bloquée ou morte" in r.stdout


def test_un_repertoire_de_run_sans_plan_le_dit(depot: Depot) -> None:
    (depot.racine / ".maestro/orchestrate/20260729-140000").mkdir(parents=True)
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "sans plan" in r.stdout


def test_le_fichier_stop_est_signale(depot: Depot) -> None:
    _run_dir(depot, "20260729-150000", [(1, 130, "-", "haute")], resume=[], sessions=(130,))
    (depot.racine / ".maestro/orchestrate/STOP").touch()
    r = depot.lance("status.sh")
    assert "arrêt demandé" in r.stdout
    assert "s'arrêtera entre deux tickets" in r.stdout


def test_le_run_par_defaut_est_le_plus_recent_et_run_id_cible_un_autre(depot: Depot) -> None:
    _run_dir(depot, "20260728-080000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", "99", 60, "1")])
    _run_dir(depot, "20260729-080000", [(1, 140, "-", "haute")],
             resume=[(140, "OK", "98", 60, "1")])

    defaut = depot.lance("status.sh")
    assert "Run 20260729-080000" in defaut.stdout and "#140" in defaut.stdout

    cible = depot.lance("status.sh", "--run-id", "20260728-080000")
    assert "Run 20260728-080000" in cible.stdout and "#130" in cible.stdout

    inconnu = depot.lance("status.sh", "--run-id", "jamais-vu")
    assert inconnu.returncode == 1
    assert "--list" in inconnu.stderr, "on oriente vers la liste plutôt que de laisser deviner"


def test_la_liste_enumere_les_runs_connus(depot: Depot) -> None:
    _run_dir(depot, "20260728-080000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", "99", 60, "1")])
    _run_dir(depot, "20260729-080000", [(1, 140, "-", "haute"), (2, 141, "-", "haute")], resume=[])
    r = depot.lance("status.sh", "--list")
    assert r.returncode == 0, r.stderr
    lignes = [x for x in r.stdout.splitlines() if x.strip().startswith("20260")]
    assert len(lignes) == 2
    assert lignes[0].strip().startswith("20260728"), "du plus ancien au plus récent"
    assert "2 ticket(s)" in lignes[1] and "0 traité(s)" in lignes[1]


def test_le_suivi_ne_boucle_pas_sur_un_run_qui_ne_tourne_plus(depot: Depot) -> None:
    """`--watch` sur un run terminé doit rendre la main : une boucle infinie n'apprend plus rien."""
    _run_dir(
        depot,
        "20260729-160000",
        [(1, 130, "-", "haute")],
        resume=[(130, "OK", "99", 600, "3.50", "-")],
    )
    debut = time.monotonic()
    r = depot.lance("status.sh", "--watch", "30")
    assert r.returncode == 0, r.stderr
    assert time.monotonic() - debut < 25, "un seul passage, pas d'attente"
    assert "rafraîchi toutes les 30 s" in r.stdout


@pytest.mark.parametrize("option", ["--no-forge", "--no-gitlab"])
def test_sans_forge_rien_n_est_interroge(depot: Depot, option: str) -> None:
    """La promesse « hors ligne » se vérifie sur les appels réellement émis, pas sur le message.

    Les DEUX orthographes sont jouées (#341). `--no-forge` est le nom depuis que la forge peut être
    GitHub ; `--no-gitlab` est l'alias historique, gardé parce que l'option a un an, qu'elle se tape
    à la main et qu'elle est documentée dans `docs/10` comme dans `/orchestrate`. Un alias que rien
    n'exerce est un alias qu'un remaniement supprime sans s'en apercevoir — et la panne serait
    silencieuse dans le pire sens : l'option inconnue fait sortir `status.sh` en 2, donc `--watch`
    d'un run en cours s'arrêterait net.
    """
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(depot, "20260729-170000", [(1, 131, "-", "moyenne")], resume=[], sessions=(131,))
    r = depot.lance("status.sh", option)
    assert r.returncode == 0, r.stderr
    assert "non interrogé (--no-forge)" in r.stdout
    assert "En cours — #131" in r.stdout, "tout le reste est lu en local"
    assert not (depot.fixtures / "gh.log").exists(), "pas même un « gh auth status »"


def test_status_n_ecrit_rien(depot: Depot) -> None:
    """Un run en cours doit pouvoir être observé sans risquer de le perturber."""
    dossier = _run_dir(
        depot, "20260729-180000", [(1, 131, "-", "moyenne")], resume=[], sessions=(131,)
    )
    depot.ticket(131, "Ticket en cours", statut="En cours")

    def empreinte() -> dict[str, tuple[int, int]]:
        return {
            str(c.relative_to(dossier)): (c.stat().st_size, c.stat().st_mtime_ns)
            for c in sorted(dossier.rglob("*"))
        }

    avant = empreinte()
    assert depot.lance("status.sh").returncode == 0
    assert empreinte() == avant


@besoin_git
def test_le_worktree_est_le_signal_de_progression(depot: Depot) -> None:
    """`<iid>.json` reste vide jusqu'à la fin : ce qui dit que ça avance, ce sont les commits."""
    branche = "feat/130-ticket-130"
    _init_git(depot, branche)
    depot.ticket(130, "Ticket en cours", statut="En cours")
    depot.mr(branche, "opened")
    _run_dir(depot, "20260729-190000", [(1, 130, "-", "haute")], resume=[], sessions=(130,))

    assert GIT is not None

    def git(*args: str) -> None:
        subprocess.run(  # noqa: S603
            [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True
        )

    (depot.racine / "livrable.txt").write_text("le travail\n", encoding="utf-8", newline="\n")
    git("add", "livrable.txt")
    git("-c", "core.hooksPath=", "commit", "--quiet", "-m", "feat: premiere moitie")
    (depot.racine / "livrable.txt").write_text("en cours\n", encoding="utf-8", newline="\n")

    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert f"[{branche}]" in r.stdout, "le worktree du ticket est nommé avec sa branche"
    assert "commits    1 en avance sur origin/main" in r.stdout
    assert "feat: premiere moitie" in r.stdout
    assert "fichiers   1 modifié(s) : livrable.txt" in r.stdout
    assert "PR #99 ouverte" in r.stdout, "l'état de la forge complète ce que le disque sait"


@besoin_git
def test_l_activite_suit_le_worktree_et_pas_seulement_le_journal(depot: Depot) -> None:
    """Une session qui édite sans rien écrire dans le répertoire du run travaille quand même."""
    branche = "feat/130-ticket-130"
    _init_git(depot, branche)
    depot.ticket(130, "Ticket en cours", statut="En cours")
    _run_dir(
        depot, "20260729-200000", [(1, 130, "-", "haute")], resume=[], sessions=(130,), age=7200
    )
    # L'index git est touché à chaque `git add`/`status` de la session : c'est lui qui vit.
    assert GIT is not None
    subprocess.run(  # noqa: S603
        [GIT, "status", "--porcelain"], cwd=str(depot.racine), check=True, capture_output=True
    )

    r = depot.lance("status.sh", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "en cours ?" not in r.stdout, "le worktree bouge : le run n'est pas muet"
    assert "peut-être bloquée" not in r.stdout

    # « Depuis n'importe quel terminal » est la raison d'être de la commande : lancée d'ailleurs,
    # elle doit lire le même worktree. `git rev-parse --git-path index` rend un chemin RELATIF sur
    # un répertoire de travail principal — non repris, il se résoudrait depuis le mauvais dossier
    # et l'activité du ticket retomberait sur les seuls fichiers du run, tous vieillis ici.
    ailleurs = depot.lance("status.sh", "--no-gitlab", cwd=depot.racine.parent)
    assert ailleurs.returncode == 0, ailleurs.stderr
    assert "en cours ?" not in ailleurs.stdout
    assert ailleurs.stdout.count("commits") == r.stdout.count("commits")


# =====================================================================================
# Une session qui rend la main sans verdict (#178)
# =====================================================================================
# Le mode d'échec le plus coûteux du premier run réel : la session croit faire une pause
# (« j'attends la fin du run de couverture »), or en `claude -p` la fin du tour est la fin du
# processus. Le CLI sort en `end_turn` / `success` / code 0 — indiscernable d'une session qui a
# fini — et le ticket reste « À faire », son travail non commité dans le worktree.
#
# Les tests reprennent `_init_git` : distinguer « a produit sans clore » de « n'a rien produit »
# se lit dans un vrai dépôt git, pas dans un dossier quelconque. Toujours sans quota ni réseau :
# le bouchon `claude` joue la sortie en code 0 sans PR, et écrit (ou non) dans le worktree.

def _stub_sans_cloture(depot: Depot, corps: str = "") -> str:
    """Un `claude` qui sort comme un succès sans avoir rien clos — le cas du run 20260729-132807."""
    return _claude_stub(depot, textwrap.dedent(corps) + """
        printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6.04,'
        printf '"result":"Je poursuivrai avec /ticket-ship des le verdict connu."}\\n'
        exit 0
    """)


@besoin_git
def test_une_session_qui_croit_faire_une_pause_dit_le_travail_laisse_dans_le_worktree(
    depot: Depot,
) -> None:
    depot.ticket(130, "Ticket a traiter")
    # Le plan d'abord : écrit dans le dépôt jetable, il doit être commité par `_init_git` pour ne
    # pas compter comme du travail de la session.
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot, """
        for f in un deux trois quatre cinq; do printf 'travail\\n' > "$f.txt"; done
    """)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "pause",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1, "sans PR ni « En revue », c'est un échec : le code 0 ne dit rien"
    resume = (depot.racine / ".maestro/orchestrate/pause/resume.tsv").read_text(encoding="utf-8")
    assert "130\tECHEC" in resume
    assert "session terminée sans clôture, 5 fichier(s) non commité(s)" in resume, (
        "la raison consignée doit être exploitable, pas juste « PR aucune, cycle de vie À faire »"
    )
    assert "PR « aucune »" in resume, "le verdict de la forge reste dit, il n'est pas remplacé"
    assert "le travail est conservé dans" in r.stdout, "la console dit où le retrouver"


@besoin_git
def test_une_session_qui_n_a_rien_laisse_est_dite_telle_quelle(depot: Depot) -> None:
    """L'autre moitié de la distinction : un worktree propre est à refaire, pas à reprendre."""
    depot.ticket(130, "Ticket a traiter")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "vide",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1
    resume = (depot.racine / ".maestro/orchestrate/vide/resume.tsv").read_text(encoding="utf-8")
    assert "session terminée sans rien produire (worktree propre)" in resume
    assert "non commité" not in resume
    assert "le travail est conservé dans" not in r.stdout, "il n'y a rien à conserver"


@besoin_git
def test_un_travail_commite_mais_non_clos_compte_aussi_comme_du_travail_en_attente(
    depot: Depot,
) -> None:
    """Une session peut avoir tout commité et s'être arrêtée juste avant `/ticket-ship`."""
    depot.ticket(130, "Ticket a traiter")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    _init_git(depot, "feat/130-ticket-a-traiter")
    claude = _stub_sans_cloture(depot, """
        printf 'le travail\\n' > livrable.txt
        git add livrable.txt >/dev/null 2>&1
        git -c core.hooksPath= commit --quiet -m 'feat: livrable' >/dev/null 2>&1
    """)
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "commite",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert r.returncode == 1
    resume = (depot.racine / ".maestro/orchestrate/commite/resume.tsv").read_text(encoding="utf-8")
    assert "session terminée sans clôture, 1 commit(s) sur la branche" in resume
    assert "le travail est conservé dans" in r.stdout


def test_le_prompt_interdit_d_attendre_un_resultat_et_couvre_le_travail_non_commite(
    depot: Depot,
) -> None:
    """Les deux causes du run perdu : le prompt ne parlait que de *validation*, et sa consigne de
    reprise ne couvrait que les *commits* — pas l'arbre sale qu'une session interrompue laisse."""
    depot.ticket(130, "Ticket a traiter")
    claude = _claude_stub(depot, """
        # `-p` est le premier argument : le prompt est le second.
        printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt.txt"
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "prompt",
                env={"MAESTRO_CLAUDE_BIN": claude})

    prompt = (depot.fixtures / "prompt.txt").read_text(encoding="utf-8")
    assert "N'attends AUCUN RÉSULTAT" in prompt
    assert "ORCHESTRATE: ECHEC" in prompt, "la sortie franche reste la troisième issue"
    assert "modifications non commitées" in prompt, (
        "un arbre sale sans commit est la trace d'une session perdue : elle doit la reprendre"
    )


def test_le_prompt_de_reprise_porte_la_meme_interdiction(depot: Depot) -> None:
    depot.ticket(130, "Ticket interrompu")
    claude = _claude_stub(depot, """
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' "$2" > "$MAESTRO_FIXTURES/prompt-reprise.txt"
          printf '{"is_error":false,"subtype":"success","total_cost_usd":2}\\n'; exit 0
        fi
        printf '{"is_error":true,"result":"Claude AI usage limit reached"}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "prompt-reprise",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})

    prompt = (depot.fixtures / "prompt-reprise.txt").read_text(encoding="utf-8")
    assert "aucun résultat différé" in prompt
    assert "ORCHESTRATE: ECHEC" in prompt


# =====================================================================================
# journal.sh — la rétention du journal d'orchestration (#198)
# =====================================================================================

def _vieux_run(depot: Depot, run_id: str, *, age: int, flux: str | None = None) -> Path:
    """Un répertoire de run figé dans le passé — `age` en secondes depuis sa dernière écriture.

    L'âge est le seul levier sur les décisions de `journal.sh` : un run qui a écrit récemment est
    présumé vivant, donc épargné quoi qu'il arrive. Sans vieillissement, tous les runs d'un test
    seraient protégés et la rétention n'aurait jamais rien à ramasser.
    """
    dossier = _run_dir(depot, run_id, [(1, 130, "-", "moyenne")], resume=[])
    if flux is not None:
        (dossier / "130.jsonl").write_text(flux, encoding="utf-8", newline="\n")
    quand = time.time() - age
    for chemin in (*sorted(dossier.rglob("*")), dossier):
        os.utime(chemin, (quand, quand))
    return dossier


def _runs_presents(depot: Depot) -> list[str]:
    dossier = depot.racine / ".maestro/orchestrate"
    return sorted(p.name for p in dossier.iterdir() if p.is_dir())


def test_la_retention_ne_garde_que_les_runs_les_plus_recents(depot: Depot) -> None:
    """Le cœur du ticket : sans elle, `.maestro/orchestrate/` ne fait que grossir."""
    for i in range(1, 7):
        _vieux_run(depot, f"run-{i:02d}", age=3600 + (7 - i) * 60)
    r = depot.lance("journal.sh", "gc", env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "3"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _runs_presents(depot) == ["run-04", "run-05", "run-06"]


def test_ni_le_run_courant_ni_un_run_qui_ecrit_encore_ne_sont_purges(depot: Depot) -> None:
    """Purger sous les pieds d'un run détaché lui ferait perdre son journal — et `status.sh` avec.

    Deux protections distinctes, éprouvées ensemble : le run que `run.sh` désigne (`--courant`) et
    celui dont la dernière écriture est récente, seul indice d'activité en l'absence de PID.
    """
    for i in range(1, 4):
        _vieux_run(depot, f"vieux-{i}", age=3600 + i * 60)
    _vieux_run(depot, "courant", age=3600)
    _run_dir(depot, "en-cours", [(1, 130, "-", "moyenne")])  # écrit à l'instant

    r = depot.lance("journal.sh", "gc", "--courant", "courant",
                    env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    restants = _runs_presents(depot)
    assert "courant" in restants, "le run désigné n'est jamais candidat"
    assert "en-cours" in restants, "un run qui écrit encore est présumé vivant"
    assert "vieux-1" in restants, "le plus récent des candidats tient dans la rétention"
    assert "vieux-2" not in restants and "vieux-3" not in restants


def test_check_dit_ce_qui_partirait_sans_rien_ecrire(depot: Depot) -> None:
    _vieux_run(depot, "garde", age=3600, flux='{"type":"result"}\n')
    _vieux_run(depot, "vieux", age=7200, flux='{"type":"result"}\n')
    r = depot.lance("journal.sh", "gc", "--check",
                    env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "vieux à retirer" in r.stdout
    assert "rien n'a été touché" in r.stdout
    assert _runs_presents(depot) == ["garde", "vieux"], "--check ne supprime rien"
    dossier = depot.racine / ".maestro/orchestrate/garde"
    assert (dossier / "130.jsonl").exists(), "--check ne compacte rien non plus"
    assert not (dossier / "130.jsonl.gz").exists()


def test_un_repertoire_de_run_vide_est_ramasse(depot: Depot) -> None:
    """Les sorties précoces de `run.sh` (plan vide, `queue.sh` en échec) laissent un `mkdir -p`
    derrière elles : aucun `rm -rf` du script ne couvre ces chemins-là."""
    vide = depot.racine / ".maestro/orchestrate/20260728-201836"
    vide.mkdir(parents=True)
    quand = time.time() - 3600
    os.utime(vide, (quand, quand))
    _vieux_run(depot, "plein", age=3600)

    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    # La rétention par défaut (10) garderait les deux : un répertoire vide n'y entre pas, il ne
    # porte rien à conserver.
    assert _runs_presents(depot) == ["plein"]


def test_un_repertoire_vide_tout_juste_cree_est_epargne(depot: Depot) -> None:
    """Un run qui vient de démarrer est vide pendant les secondes que dure le calcul du plan."""
    neuf = depot.racine / ".maestro/orchestrate/tout-neuf"
    neuf.mkdir(parents=True)
    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert neuf.exists(), "le vide n'autorise le retrait qu'une fois le silence installé"


def test_le_flux_d_un_run_conserve_est_compacte_sans_le_rajeunir(depot: Depot) -> None:
    """Compacter ne doit pas faire passer un vieux run pour un run actif : la date de la dernière
    écriture est ce dont `status.sh` — et la rétention elle-même — déduisent l'activité."""
    contenu = '{"type":"system"}\n{"type":"result","total_cost_usd":4.2}\n'
    dossier = _vieux_run(depot, "garde", age=3600, flux=contenu)
    avant = (dossier / "plan.tsv").stat().st_mtime

    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (dossier / "130.jsonl").exists()
    gz = dossier / "130.jsonl.gz"
    assert gzip.decompress(gz.read_bytes()).decode("utf-8") == contenu, "rien n'est perdu"
    assert abs(gz.stat().st_mtime - avant) < 5, "la date du flux survit à la compaction"


def test_le_flux_est_compacte_une_fois_le_verdict_rendu(depot: Depot) -> None:
    """Bout en bout : `run.sh` laisse un `.jsonl.gz`, pas un flux brut, dès le ticket terminé."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_flux(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "compacte",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/compacte"
    assert not (dossier / "130.jsonl").exists()
    assert '"type":"result"' in _flux(dossier), "le flux reste relisible, compacté"
    assert (dossier / "130.json").exists(), "le résultat final, lui, reste en clair"


def test_la_compaction_attend_le_verdict_et_ne_casse_pas_la_reprise(depot: Depot) -> None:
    """Compacter pendant le ticket ferait passer une pause pour un échec : `delai_avant_reprise`
    relit le `.jsonl` ENTIER à chaque tentative pour y trouver la limite d'usage."""
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "reprise-compacte",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reprise 1/3" in r.stdout, "la limite est toujours détectée, donc le flux toujours lu"
    dossier = depot.racine / ".maestro/orchestrate/reprise-compacte"
    assert (dossier / "130.jsonl.gz").exists(), "la compaction a bien eu lieu, mais à la fin"
    assert "usage limit reached" not in _flux(dossier), "la tentative perdue n'est pas réarchivée"


def test_un_run_fait_le_menage_du_journal_en_demarrant(depot: Depot) -> None:
    """La rétention n'est pas une commande à se rappeler : un run la déclenche en partant."""
    for i in range(1, 4):
        _vieux_run(depot, f"vieux-{i}", age=3600 + i * 60)
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "neuf",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1"})
    restants = _runs_presents(depot)
    assert "neuf" in restants, "le run qui fait le ménage ne se retire jamais lui-même"
    assert "vieux-1" in restants
    assert "vieux-2" not in restants and "vieux-3" not in restants


def test_le_menage_du_journal_se_desactive(depot: Depot) -> None:
    _vieux_run(depot, "vieux-1", age=3600)
    _vieux_run(depot, "vieux-2", age=7200)
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    depot.lance("run.sh", "--plan", plan, "--run-id", "sans-menage",
                env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "1",
                     "MAESTRO_ORCHESTRATE_JOURNAL_GC": "0"})
    assert "vieux-2" in _runs_presents(depot)


def test_un_seuil_de_retention_absurde_retombe_sur_le_defaut(depot: Depot) -> None:
    """Un `RUNS=0` mal posé viderait le journal entier : on préfère le défaut au pire."""
    for i in range(1, 4):
        _vieux_run(depot, f"run-{i}", age=3600 + i * 60)
    r = depot.lance("journal.sh", "gc", env={"MAESTRO_ORCHESTRATE_JOURNAL_RUNS": "0"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert len(_runs_presents(depot)) == 3


def test_un_journal_absent_est_un_cas_normal_pas_une_erreur(depot: Depot) -> None:
    r = depot.lance("journal.sh", "gc")
    assert r.returncode == 0, r.stderr
    assert "rien à ramasser" in r.stdout


def test_le_menage_automatique_se_tait_quand_il_n_a_rien_fait(depot: Depot) -> None:
    """`--auto` parle dans la console d'un run : le silence doit y être le cas normal."""
    _vieux_run(depot, "seul", age=3600)
    r = depot.lance("journal.sh", "gc", "--auto")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


# =====================================================================================
# Le résultat d'une session, lisible à l'œil nu (#180)
# =====================================================================================

def _objet_result(**champs) -> str:
    """Un objet `result` tel que le CLI l'écrit : minifié, sur une ligne, accents en clair.

    `json.dumps` reproduit exactement ce qui rend `<iid>.json` illisible — les retours à la ligne du
    message final y sont des « \\n » littéraux, et les antislashs d'une commande refusée y sont
    doublés. C'est cette matière-là que la vue doit désescaper.
    """
    base = {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "duration_ms": 2086510,
        "duration_api_ms": 1308490,
        "num_turns": 100,
        "result": "Le ticket est traité.\n\n## Résumé\n\n- un point « accentué »\n- un autre",
        "stop_reason": "end_turn",
        "session_id": "dba6a0ea-f843-441a-aed1-218fb3162221",
        "total_cost_usd": 10.686978499999995,
        "permission_denials": [
            {"tool_name": "Skill", "tool_use_id": "t1",
             "tool_input": {"skill": "ticket-start", "args": "130"}},
            {"tool_name": "Bash", "tool_use_id": "t2",
             "tool_input": {"command": 'cd "E:/Projets" && git status', "description": "état"}},
        ],
    }
    base.update(champs)
    return json.dumps(base, ensure_ascii=False, separators=(",", ":"))


def _stub_resultat(depot: Depot, corps_json: str, *, iid: int = 130, code: int = 0,
                   statut: str | None = "En revue") -> str:
    """Un bouchon `claude` qui recrache un flux écrit dans un fichier.

    Passer par un fichier plutôt que par des `printf` évite d'avoir à échapper deux fois le JSON
    (une fois pour Python, une fois pour le shell) — et c'est justement l'échappement qu'on teste.
    """
    (depot.fixtures / f"flux-{iid}.jsonl").write_text(
        '{"type":"system","subtype":"init"}\n' + corps_json + "\n",
        encoding="utf-8",
        newline="\n",
    )
    corps = ""
    if statut:
        corps += (
            f"printf '%s' '{_statut_json(str(iid), statut)}' "
            f'> "$MAESTRO_FIXTURES/owner-{iid}.json"\n'
        )
    corps += f'cat "$MAESTRO_FIXTURES/flux-{iid}.jsonl"\nexit {code}\n'
    return _claude_stub(depot, corps)


def test_le_resultat_d_une_session_se_lit_a_l_oeil_apres_le_run(depot: Depot) -> None:
    """Le cœur du ticket : après un run, plus besoin d'un script pour lire ce qui s'est passé."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_resultat(depot, _objet_result())
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "lisible",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    vue = (depot.racine / ".maestro/orchestrate/lisible/130.resultat.txt").read_text(
        encoding="utf-8"
    )
    # 1. De quoi on parle, et ce que GitLab en a dit — le verdict ne vient jamais de la prose.
    #    Le titre est celui du PLAN (« Ticket 130 » ici) : la vue est écrite par la boucle.
    assert "ticket #130" in vue and "Ticket 130" in vue
    assert "✓ OK" in vue and "PR #99" in vue
    # 2. Ce qu'on vient y chercher : coût, durée, refus.
    assert "10.69 $" in vue and "10.686978499999995" not in vue
    assert "34min46" in vue, "duration_ms se lit en heures et minutes, pas en millisecondes"
    assert "- Skill — ticket-start" in vue
    assert '- Bash — cd "E:/Projets" && git status' in vue
    # 3. Le message final DÉSESCAPÉ : c'est ce qui distingue une vue lisible du JSON brut.
    assert "\\n" not in vue, "les retours à la ligne sont de vrais retours à la ligne"
    assert "## Résumé" in vue and "« accentué »" in vue
    assert len(vue.splitlines()) > 10


def test_la_vue_lisible_ne_touche_pas_au_json_dont_depend_le_verdict(depot: Depot) -> None:
    """`champ_json` et `limite_atteinte` grepent `<iid>.json` : il reste brut, et sur une ligne."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    attendu = _objet_result()
    claude = _stub_resultat(depot, attendu)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "intact",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/intact"
    brut = (dossier / "130.json").read_text(encoding="utf-8")
    assert brut.strip() == attendu, "le fichier machine est recopié tel quel, octet pour octet"
    assert len(brut.strip().splitlines()) == 1
    assert "130\tOK\t99" in (dossier / "resume.tsv").read_text(encoding="utf-8")


def test_le_cout_est_arrondi_dans_le_bilan_et_dans_la_console(depot: Depot) -> None:
    """Quinze décimales n'apprennent rien de plus que deux, et débordent de toutes les colonnes."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _stub_resultat(depot, _objet_result())
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "arrondi",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/arrondi/resume.tsv").read_text(encoding="utf-8")
    assert "\t10.69\t" in resume, "le coût consigné tient en deux décimales"
    assert "10.686978499999995" not in resume
    assert "10.69 $" in r.stdout and "10.686978499999995" not in r.stdout
    # Le point décimal, pas la virgule : `status.sh` additionne cette colonne en awk.
    assert "10,69" not in resume


def test_une_session_morte_sans_resultat_le_dit_au_lieu_d_une_vue_vide(depot: Depot) -> None:
    """Un `<iid>.json` vide est le cas le plus opaque de tous — et le plus fréquent en échec."""
    depot.ticket(130, "Ticket à traiter")
    claude = _claude_stub(depot, "exit 1\n")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "muet",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 1
    vue = (depot.racine / ".maestro/orchestrate/muet/130.resultat.txt").read_text(encoding="utf-8")
    assert "✗ ECHEC" in vue
    assert "aucun résultat final" in vue
    # Sans résultat, la vue ne peut que dire où regarder : le flux et la sortie d'erreur.
    assert "130.jsonl.gz" in vue and "130.log" in vue
    assert "130.resultat.txt" in r.stdout, "la console pointe la vue lisible, pas le JSON minifié"


def test_la_vue_lisible_se_rejoue_sur_un_journal_deja_ecrit(depot: Depot) -> None:
    """Les runs d'avant ce lot n'ont pas de `.resultat.txt` : `--resultat` les rattrape."""
    vieux = depot.racine / "130.json"
    vieux.write_text(_objet_result(), encoding="utf-8", newline="\n")
    r = depot.lance("run.sh", "--resultat", str(vieux))
    assert r.returncode == 0, r.stderr
    assert "ticket #130" in r.stdout, "l'iid se déduit du nom du fichier"
    assert "## Résumé" in r.stdout and "- Skill — ticket-start" in r.stdout
    assert "10.69 $" in r.stdout
    # Diagnostic = lecture seule : ni run, ni journal, ni appel à GitLab.
    assert not (depot.racine / ".maestro").exists()
    assert not (depot.fixtures / "gh.log").exists()


def test_un_resultat_illisible_est_refuse_sans_rien_inventer(depot: Depot) -> None:
    r = depot.lance("run.sh", "--resultat", str(depot.racine / "jamais-ecrit.json"))
    assert r.returncode == 2
    assert "illisible" in r.stderr


def test_un_plan_vide_ne_laisse_pas_de_repertoire_de_run(depot: Depot) -> None:
    """Quatre vestiges de ce genre traînaient dans `.maestro/orchestrate/` — dont aucun n'était
    strictement vide, donc aucun ramassable par la rétention de #198."""
    # Bouchon qui échoue bruyamment : sans lui, le test emprunterait le `claude` de la machine —
    # vert sur un poste de dev, rouge en CI où le CLI n'existe pas (le préflight le réclame avant
    # même de lire le plan). Un plan vide ne doit de toute façon démarrer aucune session.
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sans-suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "le plan est vide" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/sans-suite").exists()


def test_un_journal_qui_a_servi_n_est_jamais_emporte_par_ce_renoncement(depot: Depot) -> None:
    """Le garde-fou du renoncement : il ne retire un run que s'il ne porte QUE son plan."""
    dossier = depot.racine / ".maestro/orchestrate/deja-la"
    dossier.mkdir(parents=True)
    (dossier / "resume.tsv").write_text("# iid\n130\tOK\t99\t60\t1.00\t-\n", encoding="utf-8")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    plan = _plan(depot, [])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "deja-la",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (dossier / "resume.tsv").exists(), "un bilan déjà écrit n'est pas un run sans suite"


# =====================================================================================
# Reprendre un run qui ne s'est pas terminé (#204)
# =====================================================================================

def _reprenables(depot: Depot) -> list[list[str]]:
    """Les lignes de `status.sh --reprenables`, découpées sur les tabulations."""
    r = depot.lance("status.sh", "--reprenables")
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines() if ligne]


def test_un_run_qui_a_tout_livre_n_est_pas_a_reprendre(depot: Depot) -> None:
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-"), (131, "OK", 98, 300, "1.20", "-")],
        age=4000,
    )
    assert _reprenables(depot) == [], "un plan entièrement soldé ne se rejoue pas"


def test_un_run_interrompu_est_reprenable_avec_ce_qu_il_lui_reste(depot: Depot) -> None:
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne"), (3, 132, "-", "basse")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    lignes = _reprenables(depot)
    assert len(lignes) == 1
    run_id, etat, restants, _debut, silence, courant = lignes[0]
    assert run_id == "20260730-100000"
    assert etat == "interrompu"
    assert restants == "2", "les tickets sans verdict, ticket en vol compris"
    assert int(silence) >= 4000
    assert courant == "", "ce run-là s'est arrêté entre deux tickets"
    # La colonne vide est la raison pour laquelle cette sortie ne se relit pas avec
    # « IFS=$'\t' read » : le tab est un blanc IFS, qui FUSIONNE les champs vides.
    assert len(lignes[0]) == 6, "six colonnes, y compris quand la dernière est vide"


def test_un_run_qui_ecrit_encore_n_est_pas_propose_a_la_reprise(depot: Depot) -> None:
    """Sans carte de pilote (journal d'avant #213), le silence reste le seul témoin — et il vaut.

    Le repli n'est pas décoratif : les journaux déjà sur disque n'ont pas de carte, et un run tué
    par SIGKILL laisse la sienne sans que personne la retire.
    """
    depot.ticket(131, "Ticket en cours", statut="En cours")
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        sessions=(131,),
    )
    assert _reprenables(depot) == [], "on ne propose pas de reprendre un run qui travaille"


def test_un_run_tue_en_plein_ticket_est_reprenable_malgre_son_ticket_en_cours(
    depot: Depot,
) -> None:
    """Machine éteinte au milieu : le témoin de session reste, personne n'écrit de code de sortie.

    Sans le critère de silence, ce run garderait le visage d'un run qui travaille pour toujours —
    et c'est précisément celui qu'on veut pouvoir reprendre.
    """
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        sessions=(131,),
        age=4000,
    )
    lignes = _reprenables(depot)
    assert len(lignes) == 1
    assert lignes[0][1] == "en-cours", "l'état déduit est dit tel quel, sans être maquillé"
    assert lignes[0][5] == "131", "le ticket en vol est nommé : c'est lui qu'une reprise reprend"


def test_les_runs_reprenables_ne_touchent_ni_a_gitlab_ni_au_disque(depot: Depot) -> None:
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    avant = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").rglob("*"))
    assert _reprenables(depot)[0][0] == "20260730-100000"
    assert not (depot.fixtures / "gh.log").exists(), "une liste qui doit marcher hors ligne"
    apres = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").rglob("*"))
    assert avant == apres


def test_la_liste_des_runs_signale_ceux_qu_on_peut_reprendre(depot: Depot) -> None:
    _run_dir(depot, "20260729-090000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    _run_dir(depot, "20260730-100000", [(1, 131, "-", "haute")], resume=[], age=4000)
    r = depot.lance("status.sh", "--list")
    assert r.returncode == 0, r.stderr
    lignes = r.stdout.splitlines()
    ligne_soldee = next(x for x in lignes if "20260729-090000" in x)
    ligne_reprenable = next(x for x in lignes if "20260730-100000" in x)
    assert "reprenable" not in ligne_soldee
    assert "reprenable" in ligne_reprenable
    assert "--resume" in r.stdout, "on dit quoi taper, pas seulement qu'il reste quelque chose"


def test_un_run_interrompu_propose_sa_propre_reprise(depot: Depot) -> None:
    """La vue détaillée nomme le run-id, jamais un chemin de journal à recopier."""
    _run_dir(depot, "20260730-100000",
             [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
             resume=[(130, "OK", 99, 600, "3.50", "-")], age=4000)
    r = depot.lance("status.sh", "--run-id", "20260730-100000", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "/orchestrate --resume 20260730-100000" in r.stdout
    assert "1 ticket(s) sans verdict" in r.stdout


def test_resume_rejoue_le_plan_du_run_vise_sans_le_recalculer(depot: Depot) -> None:
    """Le backlog a pu bouger : un ordre recalculé n'aurait plus rien du run qu'on croit reprendre.

    Aucun backlog n'est publié dans ce test — `queue.sh` échouerait s'il était appelé. C'est la
    preuve que le plan vient bien du run repris.
    """
    depot.ticket(130, "Deja livre", statut="En revue")
    depot.ticket(131, "Reste a faire")
    depot.mr("feat/131-reste-a-faire", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("131", "En revue")}' > "$MAESTRO_FIXTURES/owner-131.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    source = _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr

    nouveau = depot.racine / ".maestro/orchestrate/suite"
    assert (nouveau / "plan.tsv").read_text(encoding="utf-8") == \
        (source / "plan.tsv").read_text(encoding="utf-8")
    assert (nouveau / "reprise-de").read_text(encoding="utf-8").strip() == "20260730-100000"
    resume = (nouveau / "resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume, "un ticket livré depuis se saute de lui-même, par son statut"
    assert "131\tOK" in resume
    assert "reprise du run 20260730-100000" in r.stdout


def test_en_reprise_le_compteur_dit_la_position_dans_le_plan(depot: Depot) -> None:
    """Une reprise saute tout ce qui a été livré depuis, or le compteur suivait les tickets TENTÉS :
    le 3e du plan s'annonçait « [1/3] », et le run se terminait sur un compte qui n'y était pas."""
    depot.ticket(130, "Deja livre", statut="En revue")
    depot.ticket(131, "Livre aussi", statut="En revue")
    depot.ticket(132, "Reste a faire")
    depot.mr("feat/132-reste-a-faire", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("132", "En revue")}' > "$MAESTRO_FIXTURES/owner-132.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    _run_dir(
        depot, "20260730-100000",
        [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "moyenne")],
        resume=[(130, "OK", 99, 600, "3.50", "-")],
        age=4000,
    )
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "[3/3] #132" in r.stdout, "le seul ticket restant est le 3e du plan, pas le 1er"
    assert "[1/3]" not in r.stdout


def test_reprendre_n_ecrase_jamais_le_bilan_du_run_repris(depot: Depot) -> None:
    """`resume.tsv` s'écrit en tête de run : rejouer dans le même répertoire effacerait tout."""
    depot.ticket(130, "Deja livre", statut="En revue")
    source = _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
                      resume=[(130, "ECHEC", "-", 60, "1.00", "session coupée")], age=4000)
    avant = (source / "resume.tsv").read_text(encoding="utf-8")
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert (source / "resume.tsv").read_text(encoding="utf-8") == avant
    assert (depot.racine / ".maestro/orchestrate/suite/resume.tsv").exists()


def test_resume_sans_argument_prend_le_run_reprenable_le_plus_recent(depot: Depot) -> None:
    depot.ticket(131, "Reste a faire")
    _run_dir(depot, "20260101-000000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    _run_dir(depot, "20260202-000000", [(1, 131, "-", "haute")], resume=[], age=4000)
    r = depot.lance("run.sh", "--resume", "--dry-run",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "reprise du run 20260202-000000" in r.stdout
    assert "#131" in r.stdout


def test_resume_sans_rien_a_reprendre_le_dit_et_ne_cree_aucun_run(depot: Depot) -> None:
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
             resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    r = depot.lance("run.sh", "--resume", env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 1
    assert "aucun run à reprendre" in r.stderr
    assert "--detach" in r.stderr, "on oriente vers le run neuf plutôt que de laisser en plan"
    runs = sorted(p.name for p in (depot.racine / ".maestro/orchestrate").iterdir())
    assert runs == ["20260730-100000"], "rien de créé pour une reprise qui n'a pas eu lieu"


def test_resume_sur_un_run_inconnu_est_refuse_sans_rien_inventer(depot: Depot) -> None:
    r = depot.lance("run.sh", "--resume", "jamais-lance", env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 1
    assert "n'a pas de plan lisible" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/jamais-lance").exists()


def test_resume_et_plan_ensemble_sont_refuses(depot: Depot) -> None:
    """Deux façons de désigner le plan à jouer : en garder deux serait un piège silencieux."""
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--plan", plan,
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 2
    assert "n'en garder qu'un" in r.stderr


def test_le_ticket_en_vol_est_repris_au_lieu_d_etre_saute(depot: Depot) -> None:
    """La victime de la coupure : `/ticket-start` lui a posé « En cours », donc le filtre de statut
    l'écarterait comme s'il appartenait à quelqu'un d'autre — avec son worktree et son travail."""
    depot.ticket(130, "Ticket en vol", statut="En cours")
    depot.mr("feat/130-ticket-en-vol", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s\\n' "$@" > "$MAESTRO_FIXTURES/argv.txt"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}'
        exit 0
    """)
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
             resume=[], sessions=(130,), age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "repris en vol" in r.stdout
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume and "SAUTE" not in resume

    argv = (depot.fixtures / "argv.txt").read_text(encoding="utf-8")
    assert "--resume" in argv, "la session de la coupure est rouverte, pas recommencée à zéro"
    assert "11111111-2222-4333-a444-555555555555" in argv, "et c'est bien SON uuid"
    # L'uuid a été recopié dans le journal neuf : la reprise suivante le retrouvera là.
    session = depot.racine / ".maestro/orchestrate/suite/130.session"
    assert session.read_text(encoding="utf-8").strip() == "11111111-2222-4333-a444-555555555555"


def test_un_ticket_en_cours_que_le_run_n_avait_pas_en_main_reste_saute(depot: Depot) -> None:
    """L'exception est étroite : sans témoin de session dans le run repris, « En cours » veut dire
    qu'une autre session travaille dessus — et on ne lui prend pas son ticket."""
    depot.ticket(130, "Pris par quelqu'un d'autre", statut="En cours", assigne="alice")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, "un ticket sauté n'est pas un échec"
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume and "En cours" in resume


def test_un_ticket_deja_solde_par_le_run_repris_n_est_pas_repris_en_vol(depot: Depot) -> None:
    """Témoin de session ET ligne de bilan : le ticket a rendu son verdict, la coupure est venue
    après. Son « En cours » est alors celui d'un échec, pas d'un travail en cours de session."""
    depot.ticket(130, "Echoue puis laisse", statut="En cours")
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
             resume=[(130, "ECHEC", "-", 60, "1.00", "session terminée sans clôture")],
             sessions=(130,), age=4000)
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite", "--max", "1",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    resume = (depot.racine / ".maestro/orchestrate/suite/resume.tsv").read_text(encoding="utf-8")
    assert "130\tSAUTE" in resume
    assert "repris en vol" not in r.stdout


def test_resume_avec_detach_passe_le_run_resolu_au_lanceur(depot: Depot) -> None:
    """Le lanceur doit porter le run REPRIS, pas un « --resume » à re-résoudre : la liste aura
    changé d'ici là — le run qu'on vient de créer y figurerait, entre autres."""
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    spawn = _spawn_stub(depot)
    r = depot.lance(
        "run.sh", "--resume", "--detach", "--run-id", "detachee",
        env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_SPAWN": spawn},
    )
    assert r.returncode == 0, r.stdout + r.stderr
    corps = (depot.racine / ".maestro/orchestrate/detachee/lancer.sh").read_text(encoding="utf-8")
    commande = next(ligne for ligne in corps.splitlines() if ligne.startswith("bash "))
    assert "--resume 20260730-100000" in commande, "le run repris est nommé, la valeur est résolue"
    assert commande.count("--resume") == 1
    assert commande.count("--run-id") == 1
    assert "--detach" not in commande
    assert "reprise    du run 20260730-100000" in r.stdout


def test_le_journal_neuf_dit_de_quel_run_il_est_la_suite(depot: Depot) -> None:
    """Deux journaux partiels racontent la même liste de tickets : ils doivent se répondre."""
    depot.ticket(130, "Reste a faire")
    _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")], resume=[], age=4000)
    depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "suite",
                env={"MAESTRO_CLAUDE_BIN": "true"})
    r = depot.lance("status.sh", "--run-id", "suite", "--no-gitlab")
    assert r.returncode == 0, r.stderr
    assert "reprise    du run 20260730-100000" in r.stdout


def test_une_reprise_sans_suite_ne_laisse_pas_de_repertoire(depot: Depot) -> None:
    """Le renoncement (#180) doit aussi savoir jeter le marqueur de reprise qu'il vient de poser."""
    claude = _claude_stub(depot, 'echo "la session ne doit jamais démarrer" >&2\nexit 1\n')
    _run_dir(depot, "20260730-100000", [], resume=[], age=4000)
    # Un plan vide n'est pas reprenable : on vise donc le run explicitement.
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "sans-suite",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "le plan est vide" in r.stdout
    assert not (depot.racine / ".maestro/orchestrate/sans-suite").exists()


def test_reprendre_un_run_dans_son_propre_repertoire_est_refuse(depot: Depot) -> None:
    """Le cas tordu qui viderait le bilan qu'on prétend préserver : plan recopié sur lui-même,
    puis `resume.tsv` réécrit en tête de run."""
    source = _run_dir(depot, "20260730-100000", [(1, 130, "-", "haute")],
                      resume=[(130, "OK", 99, 60, "1.00", "-")], age=4000)
    avant = (source / "resume.tsv").read_text(encoding="utf-8")
    r = depot.lance("run.sh", "--resume", "20260730-100000", "--run-id", "20260730-100000",
                    env={"MAESTRO_CLAUDE_BIN": "true"})
    assert r.returncode == 2
    assert "son bilan serait écrasé" in r.stderr
    assert (source / "resume.tsv").read_text(encoding="utf-8") == avant


# =====================================================================================
# Un seul run à la fois — carte du pilote et arrêt des runs en vol (#213)
# =====================================================================================
#
# Ces tests-là lancent de VRAIS processus (un `sleep` qui pose sa carte comme un run le ferait) et
# les tuent pour de bon : c'est le seul moyen de vérifier qu'un arrêt arrête. Aucun n'appelle Claude
# ni GitLab — `--tuer-les-runs` ne touche ni au plan ni au réseau.


def _pilote_factice(depot: Depot, dossier: Path, duree: int = 120) -> subprocess.Popen:
    """Un processus bien vivant qui pose SA carte dans `dossier`, comme le ferait un run.

    La carte est écrite par `pilote.sh` lui-même, pas fabriquée à la main : un test qui inventerait
    le format ne vérifierait plus que sa propre invention (et la naissance, en ticks, ne se devine
    pas depuis Python).
    """
    script = depot.racine.parent / "bin" / "faux-pilote"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{depot.racine}/scripts/orchestrate/pilote.sh"\n'
        'pilote_ecrit "$1"\n'
        'sleep "$2"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(
        [BASH, str(script), str(dossier), str(duree)],
        env=depot.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # On attend une carte COMPLÈTE, pas seulement présente : le fichier apparaît dès l'ouverture de
    # la redirection, et un test qui le relirait dans cette fenêtre verrait ses retouches écrasées
    # par la fin de l'écriture. `hote` est le dernier champ posé.
    carte = dossier / "pid"
    for _ in range(200):
        if carte.exists() and "hote=" in carte.read_text(encoding="utf-8", errors="replace"):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a jamais posé sa carte")


def _carte(dossier: Path, **remplacements: str) -> None:
    """Réécrit la carte du pilote en changeant les champs demandés (PID recyclé, autre hôte…)."""
    champs = dict(
        ligne.split("=", 1)
        for ligne in (dossier / "pid").read_text(encoding="utf-8").splitlines()
        if "=" in ligne
    )
    champs.update(remplacements)
    (dossier / "pid").write_text(
        "".join(f"{c}={v}\n" for c, v in champs.items()), encoding="utf-8", newline="\n"
    )


def test_un_run_en_vol_est_arrete_avant_qu_un_autre_demarre(depot: Depot) -> None:
    """Le cœur de #213 : le processus est réellement tué, pas seulement signalé.

    On tue par `--tuer-les-runs`, qui est exactement le geste que tout démarrage fait d'office —
    sans avoir à dérouler un run entier pour l'observer.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert proc.wait(timeout=30) is not None, "le pilote tourne toujours après son arrêt"
        assert "20260803-171434" in r.stdout, "le run arrêté est nommé"
        assert "#131" in r.stdout, "le ticket en vol est nommé : c'est lui qu'on interrompt"
        assert "--resume" in r.stdout, "un run tué reste reprenable, et le rapport doit le dire"
        assert (dossier / "plan.tsv").exists() and (dossier / "resume.tsv").exists(), \
            "tuer un run ne touche pas à son journal"
    finally:
        proc.kill()


def test_un_run_tue_redevient_reprenable_immediatement(depot: Depot) -> None:
    """Sans la carte, le run qu'on vient de tuer resterait invisible un quart d'heure.

    C'est ce qui rend cohérent l'enchaînement « je tue, puis je reprends » : `--reprenables` écarte
    les runs qui écrivent encore, et celui-là vient tout juste d'écrire.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        assert _reprenables(depot) == [], "un run vivant ne se reprend pas : il travaille"
        depot.lance("run.sh", "--tuer-les-runs")
        proc.wait(timeout=30)
        lignes = _reprenables(depot)
        assert [ligne[0] for ligne in lignes] == ["20260803-171434"], \
            "pilote mort : reprenable tout de suite, sans attendre que le silence s'installe"
        assert lignes[0][1] == "interrompu"
    finally:
        proc.kill()


def test_un_pilote_vivant_n_est_jamais_propose_a_la_reprise(depot: Depot) -> None:
    """La carte l'emporte sur le silence : une session qui réfléchit longuement reste vivante."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[],
                       sessions=(130,), age=4000)
    proc = _pilote_factice(depot, dossier)
    try:
        assert _reprenables(depot) == [], \
            "4000 s sans une écriture, mais le pilote répond : ce run n'est pas à reprendre"
        r = depot.lance("status.sh", "--list")
        assert "en cours" in r.stdout, "un run vivant se voit dans la liste"
    finally:
        proc.kill()


def test_un_pid_recycle_n_est_jamais_tue(depot: Depot) -> None:
    """Le garde-fou qui protège les processus des autres : le numéro seul ne prouve rien.

    Un run tué par SIGKILL laisse sa carte derrière lui (aucun trap ne survit), et son numéro finit
    par désigner quelqu'un d'autre. Ce sont les TÉMOINS de la carte qui le démasquent.

    On les invalide TOUS, et c'est le sujet de #456 : un vrai recyclage change la naissance ET le
    winpid, alors qu'une carte dont seule la naissance ne colle plus est le cas courant d'un run
    long — le décalage d'échelle mesuré le 2026-08-24 (test voisin). N'en casser qu'un ici rendrait
    ce test vert sous Linux (où le winpid n'existe pas, donc où la naissance décide seule) et faux
    sous Windows : exactement l'écart inter-plateformes de #333.
    """
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[])
    proc = _pilote_factice(depot, dossier)
    try:
        _carte(dossier, naissance="999999999", winpid="999999999")
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert "Aucun run en cours" in r.stdout
        assert proc.poll() is None, "un processus dont l'identité ne colle pas doit être épargné"
    finally:
        proc.kill()


def _identite(depot: Depot, n_carte: str, n_lue: str, w_carte: str, w_lu: str) -> bool:
    """La règle d'identité de `pilote.sh`, éprouvée sur ses quatre témoins et rien d'autre.

    On appelle la fonction PURE plutôt que `pilote_vivant` : le winpid n'existe que sous MSYS, donc
    un test qui passerait par /proc ne poserait la question que sous Windows et rendrait, en CI
    Linux, un vert sur une question jamais posée (#333).
    """
    script = (
        f'. "{depot.racine}/scripts/orchestrate/pilote.sh"\n'
        'pilote_identite_concorde "$1" "$2" "$3" "$4"\n'
    )
    r = subprocess.run(
        [BASH, "-c", script, "bash", n_carte, n_lue, w_carte, w_lu],
        env=depot.env, capture_output=True, text=True,
    )
    assert r.returncode in (0, 1), f"verdict inattendu ({r.returncode}) : {r.stderr}"
    return r.returncode == 0


@pytest.mark.parametrize(
    ("cas", "temoins", "concorde"),
    [
        # Le cœur de #456 : la naissance a dérivé, le winpid tient — c'est bien lui.
        ("naissance dérivée, winpid OK", ("834570974", "834568417", "20968", "20968"), True),
        ("les deux témoins concordent", ("100", "100", "20968", "20968"), True),
        # Un vrai recyclage : plus aucun témoin ne reconnaît le processus.
        ("les deux témoins divergent", ("100", "999", "20968", "999"), False),
        # Un seul témoin disponible, selon la plateforme — la règle d'avant #456, intacte.
        ("Linux, naissance concordante", ("100", "100", "", ""), True),
        ("Linux, naissance divergente", ("100", "999", "", ""), False),
        # Témoin enregistré, devenu illisible, et aucun autre : on s'abstient plutôt que de tuer.
        ("naissance perdue, seul témoin", ("100", "", "", ""), False),
        ("winpid perdu, seul témoin", ("", "", "20968", ""), False),
        # Rien n'a jamais été enregistré (plateforme sans /proc) : `kill -0` fait foi.
        ("aucun témoin", ("", "", "", ""), True),
    ],
)
def test_un_seul_temoin_concordant_suffit_a_reconnaitre_le_pilote(
    depot: Depot, cas: str, temoins: tuple[str, str, str, str], concorde: bool
) -> None:
    """#456 : la naissance ne condamne plus seule, son échelle pouvant se décaler en cours de run.

    Mesuré le 2026-08-24 sur le run `20260824-094229` : carte à 834570974, relecture à 834568417
    pour le même processus jamais redémarré (2,557 s, CLK_TCK=1000), stable sur deux heures — et
    pendant que le pilote travaillait. Le verdict « mort » n'était pas qu'un affichage faux :
    `pilotes_vivants` s'en sert pour savoir QUI TUER, donc un pilote vivant déclaré mort n'est pas
    tué et deux runs cohabitent, ce que #213 existe pour empêcher.
    """
    assert _identite(depot, *temoins) is concorde, cas


def test_une_carte_orpheline_ne_fait_ni_erreur_ni_degat(depot: Depot) -> None:
    """Carte laissée par un run mort depuis longtemps : rien à tuer, et ce n'est pas une panne."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 130, "-", "haute")], resume=[])
    proc = _pilote_factice(depot, dossier)
    proc.kill()
    proc.wait(timeout=30)
    r = depot.lance("run.sh", "--tuer-les-runs")
    assert r.returncode == 0, r.stderr
    assert "Aucun run en cours" in r.stdout


def test_un_run_qui_demarre_pose_sa_carte_et_la_retire_en_partant(depot: Depot) -> None:
    """La carte vit le temps du run : posée avant le premier ticket, retirée à la sortie.

    Le bouchon `claude` relève ce qui est sur le disque PENDANT la session — seul moment où la
    carte du run courant existe.
    """
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        cat "$MAESTRO_STUB_WORKTREE_DIR"/.maestro/orchestrate/*/pid > "$MAESTRO_FIXTURES/carte.txt"
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "carte",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    pendant = (depot.fixtures / "carte.txt").read_text(encoding="utf-8")
    assert "pid=" in pendant and "naissance=" in pendant, \
        "un run en cours doit être identifiable, sans quoi personne ne peut l'arrêter"
    assert not (depot.racine / ".maestro/orchestrate/carte/pid").exists(), \
        "un run qui se termine proprement ne laisse pas sa carte derrière lui"


def test_un_run_ne_se_tue_pas_lui_meme(depot: Depot) -> None:
    """Le garde-fou de base : le run courant est exclu du tri, sans quoi il se suiciderait."""
    depot.ticket(130, "Ticket à traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "seul",
                    env={"MAESTRO_CLAUDE_BIN": claude})
    assert r.returncode == 0, r.stdout + r.stderr
    resume = (depot.racine / ".maestro/orchestrate/seul/resume.tsv").read_text(encoding="utf-8")
    assert "130\tOK" in resume, "le run est allé jusqu'au bout de son propre plan"


def test_sans_kill_laisse_cohabiter_les_runs(depot: Depot) -> None:
    """L'échappatoire explicite — et elle prévient, parce qu'elle rend le doublon possible."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        depot.ticket(130, "Ticket à traiter")
        depot.mr("feat/130-ticket-a-traiter", "opened")
        claude = _claude_stub(depot, f"""
            printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
            printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
            exit 0
        """)
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "cohabite", "--sans-kill",
                        env={"MAESTRO_CLAUDE_BIN": claude})
        assert r.returncode == 0, r.stdout + r.stderr
        assert proc.poll() is None, "--sans-kill ne tue rien"
        assert "sans-kill" in r.stdout, "le doublon assumé se dit, il ne se subit pas"
    finally:
        proc.kill()


def test_un_run_neuf_arrete_ce_qui_tourne_avant_de_partir(depot: Depot) -> None:
    """Le geste est bien câblé dans un démarrage ordinaire, pas seulement dans `--tuer-les-runs`."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        depot.ticket(130, "Ticket à traiter")
        depot.mr("feat/130-ticket-a-traiter", "opened")
        claude = _claude_stub(depot, f"""
            printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
            printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
            exit 0
        """)
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "neuf",
                        env={"MAESTRO_CLAUDE_BIN": claude})
        assert r.returncode == 0, r.stdout + r.stderr
        assert proc.wait(timeout=30) is not None
        assert "20260803-171434" in r.stdout, "le run arrêté est nommé avant que le nouveau parte"
    finally:
        proc.kill()


def test_un_dry_run_ne_tue_rien(depot: Depot) -> None:
    """`--dry-run` n'exécute rien : il n'a aucune place à faire, et ne doit pas en faire."""
    dossier = _run_dir(depot, "20260803-171434", [(1, 131, "-", "haute")], resume=[],
                       sessions=(131,))
    proc = _pilote_factice(depot, dossier)
    try:
        plan = _plan(depot, [(1, 130, "-", "moyenne")])
        r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "essai")
        assert r.returncode == 0, r.stderr
        assert proc.poll() is None, "regarder un plan n'arrête pas un run"
    finally:
        proc.kill()


# =====================================================================================
# L'arrêt quand N sessions sont en vol (#291)
# =====================================================================================
#
# Ces tests-là sont dans CE lot et non dans le lot « tests + doc » (#292), pour la raison de #213 :
# vérifier qu'un arrêt arrête ne se simule pas. Ils lancent donc de vrais processus — un pilote, ses
# N sous-shells et, sous chacun, un petit-fils — et les tuent pour de bon.
#
# Ce qu'on observe est un BATTEMENT et non un `kill -0` : un processus tué mais pas encore ramassé
# par son parent reste un zombie, et `kill -0` lui répond « vivant » (c'est tout l'objet de
# `pilote_zombie`). Un fichier qui cesse de grossir, lui, ne ment pas — plus rien ne tourne.
#
# Ce que ces tests NE couvrent pas, faute de pouvoir le faire ailleurs que sous Windows : le
# `taskkill //T //F` par WINPID, seul chemin jusqu'aux `claude.exe` natifs. Ailleurs, `pilote_tue`
# atteint toute la descendance par `kill`, et c'est cette récursion-là — jamais éprouvée au-delà du
# pilote lui-même avant ce lot — que les tests d'ici vérifient.


def _pilote_factice_a_n_sessions(
    depot: Depot, dossier: Path, n: int, duree: int = 120
) -> subprocess.Popen:
    """Un pilote vivant, ses `n` sous-shells, et sous chacun un petit-fils qui bat.

    La forme reproduit celle de `lance_ticket` : le pilote garde l'état, seule la session part dans
    un sous-shell, et c'est SOUS elle que vit le processus long (le `claude.exe` d'un vrai run).
    Trois étages, donc, pour que l'arrêt ait quelque chose de récursif à descendre.
    """
    battements = dossier.parent / "battements"
    battements.mkdir(exist_ok=True)
    script = depot.racine.parent / "bin" / "faux-pilote-n"
    script.write_text(
        "#!/usr/bin/env bash\n"
        f'. "{depot.racine}/scripts/orchestrate/pilote.sh"\n'
        'pilote_ecrit "$1"\n'
        'for ((i = 1; i <= $2; i++)); do\n'
        '  (\n'
        '    ( while :; do printf . >>"$4/session-$i"; sleep 0.2; done ) &\n'
        '    wait\n'
        '  ) &\n'
        'done\n'
        'sleep "$3"\n',
        encoding="utf-8",
        newline="\n",
    )
    script.chmod(0o755)
    proc = subprocess.Popen(  # noqa: S603
        [BASH, str(script), str(dossier), str(n), str(duree), str(battements)],
        env=depot.env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # On attend que les N battements aient VRAIMENT commencé : tuer avant qu'ils existent ferait
    # passer le test pour de mauvaises raisons.
    carte = dossier / "pid"
    for _ in range(300):
        if (
            carte.exists()
            and "hote=" in carte.read_text(encoding="utf-8", errors="replace")
            and all((battements / f"session-{i}").exists() for i in range(1, n + 1))
        ):
            return proc
        time.sleep(0.05)
    proc.kill()
    raise AssertionError("le pilote factice n'a pas démarré ses N sessions")


def _bat_encore(battements: Path, n: int) -> bool:
    """Vrai si l'un des N battements grossit encore — donc si quelque chose tourne toujours."""
    avant = [(battements / f"session-{i}").stat().st_size for i in range(1, n + 1)]
    time.sleep(1.5)
    apres = [(battements / f"session-{i}").stat().st_size for i in range(1, n + 1)]
    return avant != apres


def test_l_arret_atteint_les_n_sessions_en_vol_et_leurs_enfants(depot: Depot) -> None:
    """Le cœur de #291 côté arrêt : ce n'est pas le pilote qu'on tue, c'est tout son arbre.

    Avant ce lot, l'arrêt n'avait jamais été éprouvé sur plus d'un descendant — un run séquentiel
    n'en a qu'un. À N, une session laissée derrière soi continue de brûler du quota sans que rien ne
    la rattache plus à un run.
    """
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[], sessions=(130, 131, 132))
    battements = dossier.parent / "battements"
    proc = _pilote_factice_a_n_sessions(depot, dossier, 3)
    try:
        assert _bat_encore(battements, 3), "les trois sessions doivent battre AVANT l'arrêt"
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        assert proc.wait(timeout=30) is not None, "le pilote tourne toujours après son arrêt"
        assert not _bat_encore(battements, 3), \
            "une session survivante après l'arrêt, c'est du quota brûlé pour personne"
    finally:
        proc.kill()


def test_l_arret_nomme_tous_les_tickets_qu_il_interrompt(depot: Depot) -> None:
    """N'en nommer qu'un ferait croire qu'un seul worktree garde du travail non commité."""
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(130, 131, 132))
    proc = _pilote_factice(depot, dossier)
    try:
        r = depot.lance("run.sh", "--tuer-les-runs")
        assert r.returncode == 0, r.stderr
        proc.wait(timeout=30)
        assert "#131" in r.stdout and "#132" in r.stdout, \
            "les deux tickets encore en vol sont nommés, pas seulement le premier"
        assert "#130" not in r.stdout, \
            "un ticket déjà soldé n'est pas en vol : on ne l'interrompt pas"
    finally:
        proc.kill()


def test_un_run_a_n_sessions_reste_reprenable_apres_l_arret(depot: Depot) -> None:
    """L'arrêt est sans sommation mais BORNÉ : le journal reste entier, et tout est rejouable.

    C'est ce qui rend acceptable de tuer N sessions d'un coup — le travail non commité dort dans les
    worktrees, et les témoins de session (les uuid) sont ce qui permettra de les rouvrir.
    """
    dossier = _run_dir(depot, "20260803-171434",
                       [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
                       resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(130, 131, 132))
    proc = _pilote_factice_a_n_sessions(depot, dossier, 2)
    try:
        depot.lance("run.sh", "--tuer-les-runs")
        proc.wait(timeout=30)
        lignes = _reprenables(depot)
        assert [ligne[0] for ligne in lignes] == ["20260803-171434"]
        assert lignes[0][2] == "2", "les deux tickets en vol restent à faire"
        for iid in (131, 132):
            assert (dossier / f"{iid}.session").exists(), \
                f"l'uuid de #{iid} survit : c'est par lui que sa session se rouvrira"
        assert (dossier / "resume.tsv").exists(), "tuer un run ne touche pas à son bilan"
    finally:
        proc.kill()


def test_le_fichier_stop_arrete_un_run_concurrent_sans_couper_ce_qui_est_en_vol(
    depot: Depot,
) -> None:
    """STOP garde à N le sens qu'il avait à 1 : il arrête de LANCER, il ne tue personne.

    Les deux tickets partis ensemble vont donc au bout — c'est ce qui distingue le fichier STOP de
    `--tuer-les-runs`, et ce qui fait qu'il ne coûte aucun travail non commité. Le reste du plan est
    laissé intact pour un prochain run.
    """
    for iid in (130, 131, 132, 133):
        depot.ticket(iid, f"Ticket {iid}")
    run_dir = depot.racine / ".maestro/orchestrate/stop-n"
    stop = depot.racine / ".maestro/orchestrate/STOP"
    # La session n'attend pas une durée mais un ÉVÉNEMENT : le témoin de session du second ticket,
    # posé par le pilote juste avant de le lancer. Sans cela, la première session pourrait poser
    # STOP pendant que le pilote remplit encore son deuxième créneau, et le test dirait « un seul
    # en vol » là où c'est la course qui aurait tranché, pas le code.
    claude = _claude_stub(depot, f"""
        for _ in $(seq 1 200); do
          [ -e "{run_dir}/131.session" ] && break
          sleep 0.05
        done
        touch "{stop}"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute"),
                         (3, 132, "-", "haute"), (4, 133, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "stop-n", "--concurrence", "2",
        env={"MAESTRO_CLAUDE_BIN": claude},
    )
    # 1 : les deux sessions n'ont rien clos côté GitLab, donc deux ECHEC — ce n'est pas ce qu'on
    # regarde ici, seule compte la LISTE des tickets que le run a pris en main.
    assert r.returncode in (0, 1), r.stdout + r.stderr
    assert "Arrêt demandé" in r.stdout
    traites = [x.split("\t")[0]
               for x in (run_dir / "resume.tsv").read_text(encoding="utf-8").splitlines()
               if not x.startswith("#")]
    assert traites == ["130", "131"], (
        f"les deux en vol vont au bout, et rien de plus n'est lancé — obtenu {traites}"
    )
    assert not (run_dir / "132.session").exists(), "le reste du plan n'a pas été touché"


# =====================================================================================
# La limite d'usage quand N sessions sont en vol (#291)
# =====================================================================================
#
# Le reste de la couverture de ce chantier est au lot « tests + doc » (#292). Celui-ci est ici parce
# qu'il garde la mécanique la plus facile à casser sans que rien ne le montre : deux sessions qui
# attendent chacune dans leur coin ont exactement la même allure à l'écran qu'une attente partagée,
# et la différence ne se voit qu'au moment où la moins bien informée se réveille trop tôt.


def test_une_limite_d_usage_ne_declenche_qu_une_attente_pour_les_n_sessions(
    depot: Depot,
) -> None:
    """Une attente pour le run, chaque session rouverte PAR SON UUID : les deux moitiés du critère.

    Le palier est ramené à quelques secondes : ce qu'on vérifie n'est pas sa durée mais le fait que
    les deux sessions se rangent derrière LE MÊME rendez-vous, puis repartent chacune sur sa propre
    conversation. Sans le rendez-vous, la seconde ouvrirait la sienne et la sortie ne dirait rien de
    différent — d'où l'assertion sur le fichier, et pas seulement sur la prose.
    """
    for iid in (130, 131):
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    run_dir = depot.racine / ".maestro/orchestrate/limite-n"
    # Les deux sessions annoncent leur limite EN MÊME TEMPS — chacune attend que l'autre soit
    # arrivée. C'est le cas réel (la fenêtre se referme sur toutes à la fois) et c'est ce qui rend
    # le test insensible à la charge : laquelle des deux ouvre le rendez-vous est une course, mais
    # qu'il n'y en ait qu'UN ne l'est pas — et c'est cela, le critère.
    claude = _claude_stub(depot, f"""
        iid="$(printf '%s\\n' "$@" | grep -oE 'ticket (GitLab )?#[0-9]+' | head -1 |
               grep -oE '[0-9]+$')"
        printf '%s\\n' "$@" >> "$MAESTRO_FIXTURES/args-$iid.txt"
        n=$(( $(cat "$MAESTRO_FIXTURES/n-$iid" 2>/dev/null || echo 0) + 1 ))
        printf '%s' "$n" > "$MAESTRO_FIXTURES/n-$iid"
        if [ "$n" = 1 ]; then
          : > "$MAESTRO_FIXTURES/arrivee-$iid"
          for _ in $(seq 1 300); do
            [ -e "$MAESTRO_FIXTURES/arrivee-130" ] &&
              [ -e "$MAESTRO_FIXTURES/arrivee-131" ] && break
            sleep 0.05
          done
          printf '{{"type":"result","subtype":"error","is_error":true,"result":"rate limited"}}'
          exit 1
        fi
        printf '%s' '{_statut_json("$iid", "En revue")}' > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    r = depot.lance(
        "run.sh", "--plan", plan, "--run-id", "limite-n", "--concurrence", "2",
        env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "8"},
    )
    assert r.returncode in (0, 1), r.stdout + r.stderr

    rendez_vous = (run_dir / ".limite").read_text(encoding="utf-8").splitlines()
    assert len(rendez_vous) == 1, f"une seule attente pour le run, pas N — {rendez_vous}"
    ouvreur = rendez_vous[0].split("\t")[2]
    assert ouvreur in ("130", "131"), f"le rendez-vous nomme son ouvreur — {rendez_vous}"
    # Une qui ouvre, une qui rejoint : c'est ce couple, et non son ordre, qui dit que l'attente est
    # partagée. Deux annonces, ce serait deux attentes.
    assert r.stdout.count("avant reprise (fin vers") == 1, \
        f"une seule attente doit être annoncée — {r.stdout}"
    assert r.stdout.count(f"rejoint l'attente du run ouverte par #{ouvreur}") == 1, \
        f"l'autre session doit s'y ranger, pas ouvrir la sienne — {r.stdout}"

    # Chaque session est REPRISE, et sur SA conversation : même uuid au deuxième appel, et deux
    # uuid différents d'un ticket à l'autre.
    uuids = {}
    for iid in (130, 131):
        args = (depot.fixtures / f"args-{iid}.txt").read_text(encoding="utf-8").split("\n")
        assert (depot.fixtures / f"n-{iid}").read_text(encoding="utf-8") == "2", \
            f"#{iid} doit avoir été rejouée une fois après l'attente"
        neuf = args[args.index("--session-id") + 1]
        repris = args[args.index("--resume") + 1]
        assert neuf == repris, f"#{iid} doit rouvrir SA session, pas en ouvrir une neuve"
        uuids[iid] = neuf
    assert uuids[130] != uuids[131], "deux sessions, deux conversations"


# =====================================================================================
# Choisir le milestone d'un run neuf — queue.sh --milestones (#204)
# =====================================================================================

def _milestones(depot: Depot) -> list[list[str]]:
    """Les lignes de `queue.sh --milestones`, en-tête « # » ôtée."""
    r = depot.lance("queue.sh", "--milestones")
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines()
            if ligne and not ligne.startswith("#")]


def test_seuls_les_milestones_actifs_sont_proposes_avec_leur_reste(depot: Depot) -> None:
    depot.milestones([("Phase A", "active", 3, 10), ("Phase B", "active", 0, 4),
                      ("Phase Z", "closed", 8, 8)])
    depot.ticket(501, "A faire 1")
    depot.ticket(502, "A faire 2")
    depot.ticket(503, "Deja en revue", statut="En revue")
    depot.ticket(504, "B faire 1")
    depot.ticket(505, "B faire 2")
    depot.ticket(506, "B en cours", statut="En cours")
    depot.publie()
    depot.milestone_tickets("Phase A", [501, 502, 503])
    depot.milestone_tickets("Phase B", [504, 505, 506])

    lignes = _milestones(depot)
    assert [x[0] for x in lignes] == ["Phase A", "Phase B"], \
        "une phase soldée n'est pas un run à lancer"

    titre, courant, a_faire, ouverts, echeance, rail = lignes[0]
    assert (titre, courant) == ("Phase A", "1"), "la phase courante est marquée, pas devinée"
    assert a_faire == "2", "les « À faire » et libres, pas les ouverts"
    assert ouverts == "7" and echeance == "2026-12-31"
    assert rail == "produit", "un jalon non marqué reste du produit (#617)"

    assert lignes[1][1] == "0", "les autres phases actives sont proposables sans être le défaut"
    assert lignes[1][2] == "2"


def test_un_milestone_dont_les_tickets_sont_deja_pris_n_a_rien_a_traiter(depot: Depot) -> None:
    """Le compte suit le filtre de la boucle (« À faire » ET libre) : proposer un milestone dont
    tout est assigné mènerait à un plan vide, et le choix serait un piège."""
    depot.milestones([("Phase A", "active", 0, 2)])
    depot.ticket(501, "Pris par alice", assigne="alice")
    depot.ticket(502, "Pris par bob", assigne="bob")
    depot.publie()
    depot.milestone_tickets("Phase A", [501, 502])

    lignes = _milestones(depot)
    assert lignes[0][2] == "0", "aucun ticket que la boucle pourrait prendre"
    assert lignes[0][3] == "2", "... alors qu'il reste bien deux tickets ouverts"


# =====================================================================================
# Deux rails de milestone : outillage de la forge vs produit (#617)
# =====================================================================================
# Le rail est POSÉ dans la description du jalon (« rail: outillage »), jamais dérivé des labels du
# ticket — mesuré sur 113 tickets classés par les fichiers de leurs commits, le meilleur critère de
# labels plafonne à 91 %. Ce que ces tests gardent est le MÉCANISME : deux réponses de
# `current-milestone`, un défaut inchangé, et un rail qui voyage jusqu'au plan.

def test_sans_marqueur_le_rail_est_produit_et_le_courant_ne_bouge_pas(depot: Depot) -> None:
    """Le défaut est le comportement d'AVANT #617 : sur un dépôt dont aucun jalon n'est marqué,
    `current-milestone` sans argument rend exactement ce qu'il rendait."""
    depot.milestones([("Phase A", "active", 3, 10), ("Phase B", "active", 0, 4)])
    depot.publie()

    r = depot.lib("current-milestone")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Phase A", "le plus ancien non soldé, comme avant le rail"

    r = depot.lib("current-milestone", "produit")
    assert r.stdout.strip() == "Phase A", "« produit » explicite est le même que le défaut"


def test_le_rail_outillage_ecarte_les_jalons_produit(depot: Depot) -> None:
    """Le cœur du ticket : deux jalons actifs, deux réponses. Sans le rail, « Outillage » serait
    inatteignable — c'est ce qui faisait tomber tout ticket créé dans le jalon produit courant."""
    depot.milestones([("Phase A", "active", 3, 10),
                      ("Outillage", "active", 0, 4, "outillage")])
    depot.publie()

    # Le motif d'abord : sans rail, c'est bien « Phase A » qui gagne — donc le test qui suit
    # constate un CHANGEMENT et pas une coïncidence d'ordre.
    assert depot.lib("current-milestone").stdout.strip() == "Phase A"

    r = depot.lib("current-milestone", "outillage")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Outillage", "le rail écarte les jalons de l'autre rail"


def test_chaque_rail_a_son_courant_et_ils_ne_se_confondent_pas(depot: Depot) -> None:
    """Un jalon d'outillage plus ANCIEN ne devient pas le courant du produit, et réciproquement :
    la règle « le plus ancien non soldé » joue à l'intérieur d'un rail, jamais au travers."""
    depot.milestones([("Outillage 1", "active", 0, 4, "outillage"),
                      ("Phase A", "active", 3, 10),
                      ("Outillage 2", "active", 0, 4, "outillage"),
                      ("Phase B", "active", 0, 4)])
    depot.publie()

    assert depot.lib("current-milestone").stdout.strip() == "Phase A", \
        "le premier jalon PRODUIT, même précédé d'un jalon d'outillage"
    assert depot.lib("current-milestone", "outillage").stdout.strip() == "Outillage 1", \
        "le premier jalon OUTILLAGE, même précédé d'aucun"


def test_un_rail_inconnu_est_refuse_avant_toute_lecture(depot: Depot) -> None:
    """« produit »/« outillage » est un ensemble fermé de deux valeurs (même raison que les cinq
    efforts de #217) : une faute de frappe qui rendrait le jalon par défaut en silence recréerait
    le mélange qu'on corrige."""
    depot.milestones([("Phase A", "active", 3, 10)])
    depot.publie()

    r = depot.lib("current-milestone", "outilage")
    assert r.returncode == 2, "un rail inconnu n'est pas un rail par défaut"
    assert "outilage" in r.stderr and "produit" in r.stderr, \
        "le message nomme la faute ET les valeurs attendues"


def test_le_listing_des_milestones_porte_le_rail_de_chacun(depot: Depot) -> None:
    """`--milestones` sert à CHOISIR : sans la colonne, on choisirait un rail sans le savoir."""
    depot.milestones([("Phase A", "active", 0, 2),
                      ("Outillage", "active", 0, 2, "outillage")])
    depot.ticket(501, "produit a faire")
    depot.ticket(502, "outillage a faire")
    depot.publie()
    depot.milestone_tickets("Phase A", [501])
    depot.milestone_tickets("Outillage", [502])

    rails = {ligne[0]: ligne[5] for ligne in _milestones(depot)}
    assert rails == {"Phase A": "produit", "Outillage": "outillage"}

    # Et il y a DEUX courants, un par rail — c'est le changement de lecture que #617 introduit.
    courants = {ligne[0]: ligne[1] for ligne in _milestones(depot)}
    assert courants == {"Phase A": "1", "Outillage": "1"}, \
        "chaque rail a son courant ; n'en marquer qu'un cacherait l'autre"


def test_le_plan_dit_sur_quel_rail_il_porte(depot: Depot) -> None:
    """Le rail voyage jusqu'au plan en ligne de COMMENTAIRE, comme la réserve d'arbitrage de #562 :
    le pilote l'annonce sans redemander quoi que ce soit à la forge."""
    depot.milestones([("Outillage", "active", 0, 2, "outillage")])
    depot.ticket(501, "un ticket d'outillage")
    depot.publie()
    depot.milestone_tickets("Outillage", [501])

    r = depot.lance("queue.sh", "--milestone", "Outillage")
    assert r.returncode == 0, r.stderr
    entete = [ligne for ligne in r.stdout.splitlines() if ligne.startswith("# milestone\t")]
    assert entete == ["# milestone\tOutillage\toutillage"], \
        "le plan nomme son jalon et son rail"

    # C'est un commentaire : la lecture du plan par run.sh l'écarte déjà, donc il ne devient pas un
    # ticket fantôme en tête de run.
    tickets = [ligne for ligne in r.stdout.splitlines() if ligne and not ligne.startswith("#")]
    assert len(tickets) == 1 and "\t501\t" in tickets[0]


def test_le_listing_des_milestones_n_imprime_aucun_plan(depot: Depot) -> None:
    """C'est une sortie de données pour la question du milestone, pas un plan : rien d'autre ne doit
    s'y mêler — et surtout aucun run n'est préparé."""
    depot.milestones([("Phase A", "active", 0, 1)])
    depot.ticket(501, "A faire")
    depot.publie()
    depot.milestone_tickets("Phase A", [501])

    r = depot.lance("queue.sh", "--milestones")
    assert r.returncode == 0, r.stderr
    assert "501" not in r.stdout, "le plan n'est pas calculé ici"
    assert not (depot.racine / ".maestro").exists()


# =====================================================================================
# Soldé et vide : deux abstentions, jamais une seule (#619)
# =====================================================================================
# « Non soldé » recouvrait DEUX situations que rien ne séparait :
#
#   · SOLDÉ  — N fermés / N total, N > 0 : la phase est FINIE, seule sa fermeture reste (décision
#     humaine). Sauté depuis toujours.
#   · VIDE   — 0 / 0 : la phase n'est PAS DÉCOUPÉE, et parfois à dessein — docs/06-roadmap.md pose
#     que « la Phase 9 reste un contenant vide à dessein : on n'empaquette pas une cible mouvante ».
#
# Le second était RETENU (0 fermés < 0 total étant faux, la condition retombait sur total == 0), si
# bien que tout nouveau ticket produit tombait dans le contenant qu'on garde vide exprès et qu'un
# run au défaut y planifiait zéro ticket — mesuré le 2026-08-27, au lendemain de #617.
#
# UN TEST PAR CAUSE, et chacun PROUVE SON MOTIF sur son propre échantillon avant de conclure : il
# vérifie dans la table des jalons que celui qui va être sauté est bien CELUI QUE L'ORDRE AURAIT
# DÉSIGNÉ (premier, actif, du rail demandé) et qu'il porte bien le compte de sa cause. Sans cette
# moitié, « le suivant est rendu » serait tout aussi vrai d'un échantillon où le jalon fautif
# n'existe pas — un ✓ sur une question jamais posée.


def _table_jalons(depot: Depot) -> list[list[str]]:
    """`lib.sh milestones` en lignes : titre, etat, debut, echeance, fermes, total, rail."""
    r = depot.lib("milestones")
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines()
            if ligne and not ligne.startswith("#")]


def test_un_jalon_solde_est_saute_et_nomme_comme_tel(depot: Depot) -> None:
    """La cause historique : la phase est finie, il ne reste qu'à la fermer."""
    depot.milestones([("Phase finie", "active", 5, 5), ("Phase en cours", "active", 1, 4)])
    depot.publie()

    # Le motif d'abord : « Phase finie » est bien le PREMIER jalon actif du rail produit — c'est
    # donc l'ordre qui la désignerait — et elle porte bien la forme d'un soldé (5 fermés sur 5).
    premier = _table_jalons(depot)[0]
    assert premier[0] == "Phase finie" and premier[1] == "active" and premier[6] == "produit"
    assert (premier[4], premier[5]) == ("5", "5"), "l'échantillon porte bien un SOLDÉ"

    r = depot.lib("current-milestone")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Phase en cours", "un soldé est sauté au profit du suivant"
    assert "Phase finie" in r.stderr and "SOLDÉE" in r.stderr, "le saut est nommé avec sa cause"
    assert "5/5" in r.stderr, "... et avec le compte qui l'établit"
    assert "VIDE" not in r.stderr, \
        "un soldé n'est pas un vide : les deux causes ne se mélangent pas"


def test_un_jalon_vide_est_saute_au_meme_titre_qu_un_solde(depot: Depot) -> None:
    """Le cœur du ticket : un contenant vide n'est pas un contenant courant."""
    depot.milestones([("Phase 9 — Poste de travail : distribution", "active", 0, 0),
                      ("Control Tower v3 — conversation & intégrations", "active", 1, 4)])
    depot.publie()

    # Le motif d'abord : le jalon vide est le PREMIER actif du rail produit — c'est bien lui que la
    # règle rendait, et pas un artefact d'ordre — et il porte 0 ticket, ni fermé ni ouvert.
    premier = _table_jalons(depot)[0]
    assert premier[0].startswith("Phase 9") and premier[1] == "active"
    assert (premier[4], premier[5]) == ("0", "0"), "l'échantillon porte bien un VIDE (0 / 0)"

    r = depot.lib("current-milestone")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "Control Tower v3 — conversation & intégrations", \
        "un jalon sans aucun ticket est sauté comme un soldé, et le suivant du rail est rendu"
    assert "Phase 9" in r.stderr and "VIDE" in r.stderr, "le saut est nommé avec sa cause"
    assert "SOLDÉE" not in r.stderr, "un vide n'est pas une phase finie : rien à fermer ici"


def test_les_deux_causes_restent_comptees_a_part_quand_il_ne_reste_rien(depot: Depot) -> None:
    """L'abstention finale ne dit pas « aucun candidat » et s'arrête là : elle dit COMBIEN de
    phases sont à fermer et combien sont à découper — deux gestes différents. Et ne rien rendre
    reste le contrat, non bloquant : `/ticket-create` omet simplement l'option."""
    depot.milestones([("Phase finie", "active", 5, 5), ("Phase pas découpée", "active", 0, 0)])
    depot.publie()

    lignes = _table_jalons(depot)
    assert [(ligne[4], ligne[5]) for ligne in lignes] == [("5", "5"), ("0", "0")], \
        "l'échantillon porte UNE cause de chaque, et rien d'autre"

    r = depot.lib("current-milestone")
    assert r.returncode == 1, "aucun candidat : l'abstention est un code 1, pas une erreur franche"
    assert r.stdout.strip() == "", "rien sur stdout — c'est ce que /ticket-create lit"
    assert "1 soldé(s) à fermer" in r.stderr and "1 vide(s) à découper" in r.stderr, \
        "les deux causes sont comptées séparément : un seul mot ne dirait pas quoi faire"


def test_un_jalon_vide_de_l_autre_rail_ne_masque_rien(depot: Depot) -> None:
    """Le saut joue À L'INTÉRIEUR d'un rail, comme la règle qu'il complète (#617) : un contenant
    vide d'outillage n'a jamais barré le produit, et il ne le débloque pas non plus."""
    depot.milestones([("Outillage vide", "active", 0, 0, "outillage"),
                      ("Phase produit", "active", 1, 4),
                      ("Outillage plein", "active", 1, 4, "outillage")])
    depot.publie()

    assert depot.lib("current-milestone").stdout.strip() == "Phase produit", \
        "le rail produit ne voit ni le vide ni le plein de l'autre rail"
    r = depot.lib("current-milestone", "outillage")
    assert r.stdout.strip() == "Outillage plein", "le vide de CE rail est sauté, pas ignoré"
    assert "Outillage vide" in r.stderr and "VIDE" in r.stderr
    assert "Phase produit" not in r.stderr, "on ne rapporte que les sauts du rail demandé"


def test_un_courant_sans_rien_a_prendre_n_est_plus_le_defaut_d_un_run(depot: Depot) -> None:
    """`courant` désigne ce qu'un run prendrait SANS CONSIGNE. Un milestone dont la boucle ne peut
    rien tirer n'est pas un défaut, c'est un plan vide — et le critère est celui de la BOUCLE
    (« À faire » ET libre), strictement plus étroit que « au moins un ticket ouvert »."""
    depot.milestones([("Phase A", "active", 0, 2), ("Phase B", "active", 0, 1)])
    depot.ticket(501, "Pris par alice", assigne="alice")
    depot.ticket(502, "Pris par bob", assigne="bob")
    depot.ticket(503, "Libre, mais ailleurs")
    depot.publie()
    depot.milestone_tickets("Phase A", [501, 502])
    depot.milestone_tickets("Phase B", [503])

    # Le motif d'abord : « Phase A » EST le courant du rail — elle porte deux tickets ouverts, donc
    # le helper la rend. Sans cette moitié, un `courant = 0` pourrait n'être qu'un jalon écarté en
    # amont, et le test ne dirait rien de la garde qu'il prétend éprouver.
    assert depot.lib("current-milestone").stdout.strip() == "Phase A"

    lignes = {ligne[0]: ligne for ligne in _milestones(depot)}
    assert lignes["Phase A"][2] == "0", "rien que la boucle pourrait prendre (tout est assigné)"
    assert lignes["Phase A"][3] == "2", "... alors que la forge, elle, la voit ouverte"
    assert lignes["Phase A"][1] == "0", \
        "donc pas de `courant` : le proposer enverrait un run sur un plan vide"
    assert lignes["Phase B"][1] == "0", \
        "et le `courant` n'est pas DÉPLACÉ sur le suivant — ce serait une seconde règle, qui " \
        "finirait par diverger de current-milestone (leçon de gl_rail_de)"


def test_un_jalon_vide_ne_devient_plus_le_courant_du_listing(depot: Depot) -> None:
    """Bout en bout : le saut de lib.sh remonte jusqu'à la colonne que /orchestrate lit pour
    recommander un milestone."""
    depot.milestones([("Phase vide", "active", 0, 0), ("Phase pleine", "active", 0, 2)])
    depot.ticket(501, "A faire")
    depot.publie()
    depot.milestone_tickets("Phase vide", [])
    depot.milestone_tickets("Phase pleine", [501])

    premier = _table_jalons(depot)[0]
    assert premier[0] == "Phase vide" and (premier[4], premier[5]) == ("0", "0"), \
        "l'échantillon porte bien, en tête, le contenant vide"

    courants = {ligne[0]: ligne[1] for ligne in _milestones(depot)}
    assert courants == {"Phase vide": "0", "Phase pleine": "1"}, \
        "le défaut d'un run neuf tombe sur le jalon qui a du travail"


# =====================================================================================
# Proposer les orphelins sans les prendre — queue.sh --orphelins (#329, parent #327)
# =====================================================================================
# La règle 1 écarte les tickets « En cours » et assignés : c'est ce qui protège le travail des
# autres, et ce filtre ne bouge pas. Mais il écarte du même geste ceux qu'une session MORTE a
# laissés là — invisibles pour toujours, worktree plein (#316 : 2047 lignes jamais poussées). D'où
# une sortie SÉPARÉE : le plan reste ce qu'il était, ce qui pourrait le rejoindre se lit à côté.
#
# Le verdict n'est pas recalculé ici — il vient du verbe du lot 1, seul à savoir départager un
# vivant d'un orphelin. Ces tests posent donc ce verdict à la main (couture
# MAESTRO_ORPHELINS_SOURCE)
# pour éprouver ce que ce fichier-ci ajoute : le filtre du milestone, le run d'origine, le compte
# des reprises et le plafond.


def _source_orphelins(depot: Depot, lignes: str) -> str:
    """Couture `MAESTRO_ORPHELINS_SOURCE` : le TSV du lot 1, posé à la main.

    Le passer par un fichier plutôt que par un `printf` en ligne évite d'échapper des TAB dans un
    script dans un test — et laisse voir la fixture telle qu'elle sera lue.
    """
    fichier = depot.fixtures / "en-cours.tsv"
    fichier.write_text(
        "# iid\tverdict\tsource\tdetail\ttitre\n" + lignes, encoding="utf-8", newline="\n"
    )
    stub = depot.racine.parent / "bin" / "orphelins-stub"
    stub.write_text(
        f'#!/usr/bin/env bash\ncat "{fichier.as_posix()}"\n', encoding="utf-8", newline="\n"
    )
    stub.chmod(0o755)
    return str(stub)


def _orphelins(depot: Depot, source: str) -> list[list[str]]:
    r = depot.lance("queue.sh", "--orphelins", env={"MAESTRO_ORPHELINS_SOURCE": source})
    assert r.returncode == 0, r.stderr
    return [ligne.split("\t") for ligne in r.stdout.splitlines()
            if ligne and not ligne.startswith("#")]


def test_un_orphelin_est_liste_sans_jamais_entrer_dans_le_plan(depot: Depot) -> None:
    """LE critère du lot : proposé, jamais pris. Les deux sorties sont lues du même backlog."""
    depot.milestone("Phase X")
    depot.ticket(501, "A faire")
    depot.ticket(316, "Extraction des sources", statut="En cours", assigne="MaestroAgents")
    depot.publie()
    source = _source_orphelins(
        depot, "316\torphelin\tdéduction\tworktree silencieux depuis 19h02 — /wt/316\tExtraction\n"
    )

    plan = _lignes_du_plan(depot.lance("queue.sh").stdout)
    assert [ligne[1] for ligne in plan] == ["501"], "le filtre d'anti-collision reste le défaut"

    lignes = _orphelins(depot, source)
    assert [ligne[0] for ligne in lignes] == ["316"]
    assert "silencieux depuis 19h02" in lignes[0][5], "le détail voyage tel quel"


def test_seuls_les_orphelins_du_milestone_visé_sont_listes(depot: Depot) -> None:
    """Un run porte sur un milestone : un orphelin d'ailleurs ne rejoindrait pas CE plan.

    Le signalement global existe déjà (`reconcile-en-cours`, `doctor.sh`) — cette sortie-ci répond à
    « qu'est-ce qui manque au plan que je m'apprête à lancer ? ».
    """
    depot.milestones([("Phase X", "active", 0, 2), ("Phase Y", "active", 0, 1)])
    depot.ticket(316, "Du milestone courant", statut="En cours")
    depot.ticket(299, "D'une autre phase", statut="En cours")
    depot.publie()
    depot.milestone_tickets("Phase X", [316])
    depot.milestone_tickets("Phase Y", [299])
    source = _source_orphelins(
        depot,
        "316\torphelin\tdéduction\tmuet depuis 19h02 — /wt/316\tDu milestone courant\n"
        "299\torphelin\tdéduction\tmuet depuis 3j — /wt/299\tD'une autre phase\n",
    )

    assert [ligne[0] for ligne in _orphelins(depot, source)] == ["316"]


def test_un_ticket_vivant_n_est_jamais_propose(depot: Depot) -> None:
    """Le verdict du lot 1 fait foi, et « vivant » ferme la porte : c'est le ticket de quelqu'un."""
    depot.milestone("Phase X")
    depot.ticket(316, "Orphelin", statut="En cours")
    depot.ticket(317, "Bien vivant", statut="En cours")
    depot.publie()
    source = _source_orphelins(
        depot,
        "316\torphelin\tdéduction\tmuet depuis 19h02 — /wt/316\tOrphelin\n"
        "317\tvivant\tcarte du pilote\trun 20260810-141208, pilote pid 4242\tBien vivant\n",
    )

    assert [ligne[0] for ligne in _orphelins(depot, source)] == ["316"]


@besoin_git
def test_l_orphelin_porte_le_run_qui_l_a_laisse_la(depot: Depot) -> None:
    """« D'où sort ce ticket ? » — la seule information qu'un humain ne peut pas retrouver seul."""
    _init_git(depot, "chore/316-essai")
    depot.milestone("Phase X")
    depot.ticket(316, "Extraction des sources", statut="En cours")
    depot.publie()
    _run_dir(depot, "20260810-141208", [(1, 316, "-", "haute")],
             resume=[(316, "ECHEC", "-", 2702, 14.75, "timeout — session terminée sans clôture")])
    source = _source_orphelins(
        depot, "316\torphelin\tdéduction\tmuet depuis 19h02 — /wt/316\tExtraction\n"
    )

    ligne = _orphelins(depot, source)[0]
    assert ligne[3] == "20260810-141208" and ligne[4] == "ECHEC"


@besoin_git
def test_le_plafond_est_marque_pour_que_la_proposition_s_arrete(depot: Depot) -> None:
    """Un ticket déjà repris deux fois ne se propose plus : il est listé, marqué, et c'est tout.

    Lister sans proposer n'est pas une demi-mesure — c'est la forme que prend le bornage ici :
    /orchestrate saute les lignes `atteint`, et l'humain qui veut insister voit toujours de quoi
    il retourne (la trace, puis `--force`).
    """
    _init_git(depot, "chore/316-essai")
    depot.milestone("Phase X")
    depot.ticket(316, "Retombe à chaque run", statut="En cours")
    depot.ticket(317, "Jamais repris", statut="En cours")
    depot.publie()
    registre = depot.racine / ".maestro/orchestrate/reprises.tsv"
    registre.parent.mkdir(parents=True, exist_ok=True)
    registre.write_text(
        "# date\tiid\trun_origine\tverdict_origine\trang\tpar\n"
        "2026-08-10T09:00:00\t316\t20260809-100000\tECHEC\t1\tMaestroAgents\n"
        "2026-08-11T09:00:00\t316\t20260810-141208\tECHEC\t2\tMaestroAgents\n",
        encoding="utf-8", newline="\n",
    )
    source = _source_orphelins(
        depot,
        "316\torphelin\tdéduction\tmuet depuis 19h02 — /wt/316\tRetombe\n"
        "317\torphelin\tdéduction\tmuet depuis 8h — /wt/317\tJamais repris\n",
    )

    par_iid = {ligne[0]: ligne for ligne in _orphelins(depot, source)}
    assert par_iid["316"][1:3] == ["2", "atteint"], "deux reprises : on ne le propose plus"
    assert par_iid["317"][1:3] == ["0", "-"]


def test_lister_les_orphelins_n_imprime_aucun_plan_et_n_ecrit_rien(depot: Depot) -> None:
    """C'est une sortie de données pour une question, pas un run préparé — comme `--milestones`."""
    depot.milestone("Phase X")
    depot.ticket(501, "A faire")
    depot.ticket(316, "Orphelin", statut="En cours")
    depot.publie()
    source = _source_orphelins(depot, "316\torphelin\tdéduction\tmuet — /wt/316\tOrphelin\n")

    r = depot.lance("queue.sh", "--orphelins", env={"MAESTRO_ORPHELINS_SOURCE": source})
    assert r.returncode == 0, r.stderr
    assert "501" not in r.stdout, "le plan n'est pas calculé ici"
    assert not (depot.racine / ".maestro").exists(), "aucune trace laissée par une simple lecture"


# =====================================================================================
# journal.sh origine — quel run a laissé ce ticket là ? (#329)
# =====================================================================================


@besoin_git
def test_origine_rend_le_run_le_plus_recent_qui_a_juge_le_ticket(depot: Depot) -> None:
    _init_git(depot, "chore/316-essai")
    _run_dir(depot, "20260809-100000", [(1, 316, "-", "haute")],
             resume=[(316, "ECHEC", "-", 100, 1.0, "vieux verdict")])
    _run_dir(depot, "20260810-141208", [(1, 316, "-", "haute")],
             resume=[(316, "ECHEC", "-", 2702, 14.75, "timeout — session terminée sans clôture")])

    r = depot.lance("journal.sh", "origine", "316")
    assert r.returncode == 0, r.stderr
    run, verdict, raison = r.stdout.strip().split("\t")
    assert (run, verdict) == ("20260810-141208", "ECHEC")
    assert "timeout" in raison


@besoin_git
def test_un_ticket_saute_par_un_run_ne_lui_est_pas_attribue(depot: Depot) -> None:
    """`SAUTE` n'est pas une prise en main : le run a passé son tour sans ouvrir de session.

    Le compter masquerait le run PRÉCÉDENT — celui qui a réellement laissé le ticket là — au profit
    d'un run qui n'y a pas touché.
    """
    _init_git(depot, "chore/316-essai")
    _run_dir(depot, "20260809-100000", [(1, 316, "-", "haute")],
             resume=[(316, "ECHEC", "-", 2702, 14.75, "timeout")])
    _run_dir(depot, "20260811-080000", [(1, 316, "-", "haute")],
             resume=[(316, "SAUTE", "-", 0, 0, "cycle de vie « En cours » à son tour")])

    r = depot.lance("journal.sh", "origine", "316")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split("\t")[0] == "20260809-100000"


@besoin_git
def test_un_ticket_en_vol_a_la_coupure_a_bien_une_origine(depot: Depot) -> None:
    """Le mode de mort qui FABRIQUE les orphelins : pilote arrêté au `taskkill`, aucun trap, donc
    aucun verdict. S'arrêter au bilan laisserait sans origine ceux qu'on reprend le plus souvent.
    """
    _init_git(depot, "chore/316-essai")
    _run_dir(depot, "20260810-141208", [(1, 316, "-", "haute")], resume=[], sessions=(316,))

    r = depot.lance("journal.sh", "origine", "316")
    assert r.returncode == 0, r.stderr
    run, verdict, raison = r.stdout.strip().split("\t")
    assert run == "20260810-141208" and verdict == "sans verdict"
    assert "en vol" in raison


@besoin_git
def test_origine_ne_dit_rien_plutot_que_d_inventer(depot: Depot) -> None:
    """Une session interactive (#325) n'écrit aucun journal : ne rien trouver est une réponse."""
    _init_git(depot, "chore/316-essai")
    _run_dir(depot, "20260810-141208", [(1, 316, "-", "haute")],
             resume=[(316, "OK", "!259", 100, 1.0, "-")])

    r = depot.lance("journal.sh", "origine", "325")
    assert r.returncode == 1
    assert r.stdout.strip() == ""


# =====================================================================================
# journal.sh refus — l'agrégat des permission_denials (#235, parent #232)
# =====================================================================================
# §11.7 pose le principe : l'`allow` se complète À PARTIR DES REFUS OBSERVÉS. Il n'était outillé
# que par ticket (`<iid>.resultat.txt`, #180) ; la question qu'on se pose APRÈS un run est l'autre
# — « qu'est-ce qui a été refusé, en tout ? » —, et y répondre demandait de dépouiller 16 JSON à la
# main. Ce que l'agrégat voit et qu'une lecture ticket par ticket rate : le POIDS d'une forme, le
# MAILLON FAIBLE d'une chaîne, et les refus que rien dans le dépôt ne lèvera.


def _refus(*commandes: str, outil: str = "Bash") -> list[dict]:
    champ = {"Bash": "command", "Skill": "skill"}.get(outil, "file_path")
    return [
        {"tool_name": outil, "tool_use_id": f"t{i}", "tool_input": {champ: cmd}}
        for i, cmd in enumerate(commandes)
    ]


def _journal_refus(depot: Depot, run_id: str, sessions: dict[int, list[dict]]) -> Path:
    """Un run déjà terminé, dont chaque session a laissé son `<iid>.json` — la matière de #180."""
    plan = [(rang, iid, "-", "moyenne") for rang, iid in enumerate(sessions, 1)]
    dossier = _run_dir(depot, run_id, plan)
    for iid, refus in sessions.items():
        objet = {
            "type": "result", "subtype": "success", "is_error": False,
            "total_cost_usd": 1.5, "permission_denials": refus,
        }
        (dossier / f"{iid}.json").write_text(
            json.dumps(objet, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8", newline="\n",
        )
    return dossier


def test_refus_compte_chaque_maillon_d_une_chaine_pour_lui_meme(depot: Depot) -> None:
    """Le CLI découpe sur `&&`, `;` et `|` et exige CHAQUE morceau : l'agrégat doit faire pareil.

    Sans ça, `grep … | tail -8` serait rangé sous « grep » alors que c'est le seul mot qui a fait
    tomber la ligne — et on instruirait à côté, en ajoutant `tail`, déjà autorisé.
    """
    _journal_refus(depot, "chaines", {130: _refus(
        'cd "E:/ailleurs" && git status',
        'grep -nE "a|b" journal.log | tail -8',
    )})
    r = depot.lance("journal.sh", "refus", "chaines")

    assert r.returncode == 0, r.stderr
    for verbe in ("cd", "git status", "grep", "tail"):
        assert verbe in r.stdout, f"le maillon « {verbe} » doit être compté pour lui-même"
    assert "en commande composée" in r.stdout
    # Un `grep -E "a|b"` est UNE commande, pas deux : le `|` entre guillemets ne coupe rien.
    assert '  b"' not in r.stdout


def test_refus_pese_une_forme_qu_une_lecture_ticket_par_ticket_raterait(depot: Depot) -> None:
    """Six refus `env` sur cinq sessions ne se voient pas un par un : d'où le total."""
    _journal_refus(depot, "poids", {
        130: _refus("env | grep MAESTRO", "printf 'x'"),
        131: _refus("env | grep LANGFUSE"),
    })
    r = depot.lance("journal.sh", "refus", "poids")

    assert r.returncode == 0, r.stderr
    assert "2 session(s)" in r.stdout
    ligne = next(x for x in r.stdout.splitlines() if x.split()[1:2] == ["env"])
    assert ligne.split()[0] == "2", f"les deux sessions doivent s'additionner : {ligne}"
    assert "#130" in ligne and "#131" in ligne, "la provenance dit OÙ regarder"


def test_refus_releve_les_formes_qu_aucune_regle_ne_matchera(depot: Depot) -> None:
    """Ces trois-là ne s'instruisent pas en élargissant la liste : le geste est dans la FORME.

    Elles sont relevées AVANT que la commande soit aplatie pour le TSV interne — le saut de ligne
    n'y survivrait pas, et c'est justement la forme la plus coûteuse (huit sessions sur seize).
    """
    _journal_refus(depot, "formes", {130: _refus(
        'gh pr create --body "ligne un\nligne deux"',
        'gh pr create --body "$(cat brouillon.md)"',
        "cat > note.md <<'EOF'\ntexte\nEOF",
    )})
    r = depot.lance("journal.sh", "refus", "formes")

    assert r.returncode == 0, r.stderr
    assert "Formes immatchables" in r.stdout
    for forme in ("saut de ligne", "substitution", "heredoc"):
        assert forme in r.stdout, f"la forme « {forme} » doit être nommée"
    assert "l'outil Write" in r.stdout, "le geste de remplacement, pas seulement le constat"


def test_refus_signale_a_part_ce_qu_aucune_regle_ne_levera(depot: Depot) -> None:
    """Écrire sous `.claude/` vient du CLI, pas de la liste (#229) : aucune règle n'y peut rien."""
    _journal_refus(depot, "claude", {130: _refus(
        ".claude/skills/control-tower/SKILL.md", outil="Write",
    )})
    r = depot.lance("journal.sh", "refus", "claude")

    assert r.returncode == 0, r.stderr
    assert "Hors Bash" in r.stdout and "Write" in r.stdout
    assert ".claude/" in r.stdout
    assert "aucune règle ne les lèvera" in r.stdout


def test_refus_agrege_tout_le_journal_ou_un_seul_run(depot: Depot) -> None:
    """Deux portées, une même lecture : `--tous` pour la tendance, un run-id pour le run du jour."""
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    _journal_refus(depot, "20260804-100000", {131: _refus("npx tsc --noEmit")})

    cible = depot.lance("journal.sh", "refus", "20260801-100000")
    assert "1 session(s) · 1 refus" in cible.stdout
    assert "npx" not in cible.stdout

    tous = depot.lance("journal.sh", "refus", "--tous")
    assert tous.returncode == 0, tous.stderr
    assert "2 session(s) · 2 refus" in tous.stdout
    assert "awk" in tous.stdout and "npx" in tous.stdout


def test_refus_sans_argument_prend_le_dernier_run_qui_en_porte(depot: Depot) -> None:
    """Un run tout frais dont aucune session n'a rendu la main masquerait le seul run lisible.

    Les run-id sont horodatés : l'ordre alphabétique EST l'ordre chronologique, sans interroger le
    système de fichiers.
    """
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    _run_dir(depot, "20260805-090000", [(1, 999, "-", "haute")])  # parti, rien rendu

    r = depot.lance("journal.sh", "refus")
    assert r.returncode == 0, r.stderr
    assert "20260801-100000" in r.stdout
    assert "awk" in r.stdout


# --- Le classement en familles (#307) ------------------------------------------------------------
# L'agrégat disait COMBIEN et DE QUOI, jamais POURQUOI — d'où un sujet qui passait pour clos pendant
# que le compte, lui, ne baissait pas : le gisement des trous d'allowlist, #232 l'avait fini. Les
# sept commandes les plus refusées du journal sont TOUTES dans l'`allow` — c'est la CIBLE qui tombe.


def _familles(sortie: str) -> dict[str, int]:
    """Le classement, lu comme une TABLE et jamais par recherche de texte : le rappel de l'ordre de
    décision, juste en dessous, nomme les mêmes familles — un `in sortie` y matcherait toujours."""
    familles = {}
    for ligne in sortie.splitlines():
        trouve = re.match(r"\s+(\d+)\s+(\S.*?)\s+(\d+) %", ligne)
        if trouve:
            familles[trouve.group(2)] = int(trouve.group(1))
    return familles


def _maillons(sortie: str) -> list[str]:
    """La liste « ce qui s'instruit », vide quand aucun maillon n'est vraiment découvert."""
    if "s'instruit" not in sortie:
        return []
    bloc = sortie.split("s'instruit", 1)[1].split("── Par outil", 1)[0]
    return [
        ligne.split(maxsplit=1)[1]
        for ligne in bloc.splitlines()
        if ligne.strip() and ligne.split()[0].isdigit()
    ]


def test_refus_distingue_l_echappee_de_chemin_du_trou_d_allowlist(depot: Depot) -> None:
    """La distinction est tout l'objet du ticket : le geste n'est pas le même des deux côtés.

    `cat`/`head` sont autorisés — une chaîne qui n'échoue que par sa cible ne s'instruit pas dans
    `settings.run.json`, et l'y chercher est ce qui a fait passer le sujet pour clos.
    """
    _journal_refus(depot, "familles", {130: _refus(
        'cat "E:/ailleurs/notes.md" | head -20',      # tout est autorisé : c'est le chemin
        "bash scripts/orchestrate/queue.sh > /tmp/plan.txt",
        "for f in a b; do node $f; done",             # `for` n'est couvert par aucune règle
    )})
    r = depot.lance("journal.sh", "refus", "familles")

    assert r.returncode == 0, r.stderr
    familles = _familles(r.stdout)
    assert familles.get("échappée de chemin") == 2, f"les deux cibles hors worktree : {r.stdout}"
    assert familles.get("trou d'allowlist") == 1, f"le seul maillon découvert : {r.stdout}"
    # Et la liste qui s'instruit ne porte QUE ce maillon-là : y voir « cat » enverrait ajouter une
    # règle qui est déjà là.
    assert _maillons(r.stdout) == ["for"]


def test_refus_ne_prend_pas_un_chemin_absolu_pour_une_regle_manquante(depot: Depot) -> None:
    """Sa forme RELATIVE serait couverte, et aucune règle de préfixe ne bornera jamais un absolu :
    le compter comme un trou enverrait élargir la liste pour rien."""
    _journal_refus(depot, "absolu", {130: _refus(
        '"E:/depot/.venv/Scripts/python.exe" -m pytest',
    )})
    r = depot.lance("journal.sh", "refus", "absolu")

    assert r.returncode == 0, r.stderr
    assert _familles(r.stdout) == {"échappée de chemin": 1}
    assert _maillons(r.stdout) == [], "aucun maillon à instruire — le geste est la forme"


def test_refus_ne_confond_pas_une_url_ni_un_sed_avec_un_chemin(depot: Depot) -> None:
    """Trois faux positifs qui rangeraient des refus ordinaires en échappées : `https://`, `sed
    s/a/b/` et `2>/dev/null`. Le premier ferait basculer toute commande portant une URL."""
    _journal_refus(depot, "faux-positifs", {130: _refus(
        "sed -i 's/avant/apres/' fichier.md",
        "ls apps/web 2>/dev/null; cat .node-version",
    )})
    r = depot.lance("journal.sh", "refus", "faux-positifs")

    assert r.returncode == 0, r.stderr
    assert "échappée de chemin" not in _familles(r.stdout), (
        f"aucune de ces commandes ne sort du répertoire de travail : {r.stdout}"
    )


def test_refus_reconnait_un_refus_voulu_par_une_regle_ask_du_depot(depot: Depot) -> None:
    """`git commit --no-verify` est demandé en confirmation par le dépôt : en autonome, personne ne
    peut l'accorder. Le ranger ailleurs enverrait chercher une règle qui existe déjà et dit non.

    Le matching y est plus large qu'ailleurs, et à dessein : le CLI comprend les OPTIONS, un
    préfixe non — `git commit --no-edit --no-verify` doit tomber sous la même règle.
    """
    _journal_refus(depot, "voulu", {130: _refus(
        "git add x.py; git commit --no-edit --no-verify",
    )})
    r = depot.lance("journal.sh", "refus", "voulu")

    assert r.returncode == 0, r.stderr
    assert _familles(r.stdout) == {"refus voulu (ask/deny)": 1}


def test_refus_ne_prend_pas_une_tete_de_regle_pour_une_option(depot: Depot) -> None:
    """La contrepartie du matching large : seules les OPTIONS flottent, jamais la tête de la règle.

    Sans ça, `git commit -m "clean up"` tomberait sous `Bash(git clean:*)` et un refus ordinaire
    passerait pour voulu.
    """
    _journal_refus(depot, "tete", {130: _refus('git commit -m "clean up"')})
    r = depot.lance("journal.sh", "refus", "tete")

    assert r.returncode == 0, r.stderr
    assert "refus voulu (ask/deny)" not in _familles(r.stdout), r.stdout


def test_refus_lit_les_regles_la_ou_elles_vivent(depot: Depot) -> None:
    """Une copie figée du `allow` se périmerait en silence — le défaut même que #307 corrige.

    Le test le prouve par l'absurde : on retire une règle du fichier du dépôt jetable, et le
    classement doit changer d'avis sur la même commande.
    """
    _journal_refus(depot, "vivantes", {130: _refus("awk '{print}' fichier.txt")})
    avant = _familles(depot.lance("journal.sh", "refus", "vivantes").stdout)
    assert "trou d'allowlist" not in avant, "`awk` est autorisé — rien à instruire"

    chemin = depot.racine / "scripts/orchestrate/settings.run.json"
    reglages = json.loads(chemin.read_text(encoding="utf-8"))
    reglages["permissions"]["allow"].remove("Bash(awk:*)")
    chemin.write_text(json.dumps(reglages, ensure_ascii=False, indent=2), encoding="utf-8")

    r = depot.lance("journal.sh", "refus", "vivantes")
    assert _familles(r.stdout) == {"trou d'allowlist": 1}, (
        f"le classement doit suivre le fichier, pas une copie : {r.stdout}"
    )
    assert _maillons(r.stdout) == ["awk"]


def test_refus_range_chaque_refus_dans_une_seule_famille(depot: Depot) -> None:
    """La somme des familles EST le total : sans ça le classement serait un comptage de plus.

    Le premier cas porte les deux causes à la fois — un maillon découvert ET une cible hors du
    worktree —, et l'ordre de décision tranche en faveur du trou d'allowlist.
    """
    _journal_refus(depot, "partition", {130: _refus(
        "for f in a b; do cat /tmp/$f; done",
        'cd "E:/ailleurs" && git status',
        'gh pr create --body "un\ndeux"',
    ) + _refus(".claude/settings.json", outil="Write")})
    r = depot.lance("journal.sh", "refus", "partition")

    assert r.returncode == 0, r.stderr
    assert "1 session(s) · 4 refus" in r.stdout
    familles = _familles(r.stdout)
    assert sum(familles.values()) == 4, f"un refus, une famille — {r.stdout}"
    assert familles == {
        "trou d'allowlist": 1,
        "échappée de chemin": 1,
        "forme immatchable": 1,
        "blocage dur .claude/": 1,
    }, r.stdout


def test_refus_ne_touche_a_rien_et_dit_franchement_qu_il_n_a_rien_trouve(depot: Depot) -> None:
    """Lecture seule : `refus` sert à décider, il ne décide pas — et un run propre le dit."""
    dossier = _journal_refus(depot, "propre", {130: []})
    fichiers = [p for p in sorted(dossier.rglob("*")) if p.is_file()]
    empreinte = {p: p.stat().st_mtime_ns for p in fichiers}

    r = depot.lance("journal.sh", "refus", "propre")

    assert r.returncode == 0, r.stderr
    assert "Aucun refus de permission" in r.stdout
    assert {p: p.stat().st_mtime_ns for p in fichiers} == empreinte


def test_refus_nomme_les_runs_presents_quand_le_run_id_est_inconnu(depot: Depot) -> None:
    """Une faute de frappe ne doit pas rendre un vide, qu'on lirait « rien à faire »."""
    _journal_refus(depot, "20260801-100000", {130: _refus("awk '{print}' f")})
    r = depot.lance("journal.sh", "refus", "20260801-999999")
    assert r.returncode == 2
    assert "run inconnu" in r.stderr
    assert "20260801-100000" in r.stderr


def test_la_console_renvoie_vers_l_agregat_en_fin_de_run(depot: Depot) -> None:
    """Le seul moment où quelqu'un lit un run est celui-là : c'est là que l'invitation porte."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, """
        printf '{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "invite",
                    env={"MAESTRO_CLAUDE_BIN": claude})

    assert "journal.sh refus invite" in r.stdout, (
        "sans cette ligne, la boucle de retour de §11.7 ne part que si on y pense — "
        "et onze runs ont montré que non"
    )


# =====================================================================================
# journal.sh audit — où passe le temps d'un run (#497, puis #498 — parent #495)
# =====================================================================================
# Tests différés du lot 2 (#497), écrits ici avec la commande qui s'en sert (lot final, #498) —
# c'est la convention de découpage (docs/10 §5.1).
#
# Le journal est FABRIQUÉ et ses horodatages posés à la main : un audit qu'on jugerait sur un vrai
# run mesurerait la machine — sa charge, ses disques, le quota du jour — et non le code. Les durées
# attendues sont donc des soustractions exactes, jamais des ordres de grandeur.


def _t(secondes: float) -> str:
    """Un horodatage du flux, à `secondes` du début — la forme que le CLI écrit, à la ms près."""
    ms = round(secondes * 1000)
    h, reste = divmod(ms, 3_600_000)
    m, reste = divmod(reste, 60_000)
    s, ms = divmod(reste, 1000)
    return f"2026-08-23T{15 + h:02d}:{m:02d}:{s:02d}.{ms:03d}Z"


def _entree(outil: str, cible: str) -> dict:
    """La clé d'entrée que porte cet outil — parmi celles que `_cible` sait lire."""
    return {
        "Bash": {"command": cible},
        "Agent": {"description": cible},
        "Grep": {"pattern": cible},
    }.get(outil, {"file_path": cible})


def _jsonl(objet: dict) -> str:
    """Une ligne du flux, compacte comme le CLI l'écrit : l'extraction s'ancre sur les clés."""
    return json.dumps(objet, ensure_ascii=False, separators=(",", ":")) + "\n"


def _appel(instant: float, *blocs: tuple[str, str, str]) -> str:
    """Une ligne `assistant`. Plusieurs blocs = des appels PARALLÈLES, partis du même message."""
    return _jsonl({
        "type": "assistant",
        "timestamp": _t(instant),
        "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": ident, "name": outil, "input": _entree(outil, cible)}
            for ident, outil, cible in blocs
        ]},
    })


def _retour(instant: float, ident: str, *, type_d_abord: bool = False) -> str:
    """Une ligne `user`. `type_d_abord` inverse l'ordre des clés du bloc — il n'est pas stable.

    Mesuré sur le flux de #346 : un même run écrit tantôt `{"type":"tool_result","tool_use_id":…}`,
    tantôt l'inverse. Un appariement qui découperait sur le marqueur de type n'apparie alors que
    les appels dont l'ordre l'arrange — 4 sur 98 au premier essai de #497, tous les autres déclarés
    « morts sans retour ». Les deux ordres doivent donc voyager dans le même flux de test.
    """
    bloc = {"type": "tool_result", "tool_use_id": ident} if type_d_abord else \
        {"tool_use_id": ident, "type": "tool_result"}
    bloc["content"] = "ok"
    return _jsonl({"type": "user", "timestamp": _t(instant),
                   "message": {"role": "user", "content": [bloc]}})


def _journal_audit(depot: Depot, run_id: str, flux: dict[int, str], *,
                   compacte: tuple[int, ...] = ()) -> Path:
    """Un run dont chaque session a laissé son flux brut `<iid>.jsonl` — la matière de #176."""
    plan = [(rang, iid, "-", "moyenne") for rang, iid in enumerate(flux, 1)]
    dossier = _run_dir(depot, run_id, plan)
    for iid, lignes in flux.items():
        if iid in compacte:
            with gzip.open(dossier / f"{iid}.jsonl.gz", "wt", encoding="utf-8", newline="\n") as f:
                f.write(lignes)
        else:
            (dossier / f"{iid}.jsonl").write_text(lignes, encoding="utf-8", newline="\n")
    return dossier


def _section_audit(sortie: str, titre: str) -> list[str]:
    """Les lignes d'une section du rapport, son titre exclu."""
    lignes, dedans = [], False
    for ligne in sortie.splitlines():
        if ligne.startswith("── "):
            dedans = ligne.startswith(f"── {titre}")
            continue
        if dedans:
            lignes.append(ligne)
    return lignes


# « <durée cumulée>  <n>x  moy <moyenne>   <nom> » — la forme des deux tables agrégées du rapport.
_POSTE = re.compile(
    r"^\s+(?P<total>\S+(?: min)?)\s+(?P<n>\d+)x\s+moy\s+\S+(?: min)?\s+(?P<nom>.+?)\s*$"
)


def _postes(sortie: str, titre: str) -> dict[str, tuple[str, int]]:
    """Une table agrégée du rapport : nom -> (durée cumulée, nombre d'appels)."""
    trouves = {}
    for ligne in _section_audit(sortie, titre):
        m = _POSTE.match(ligne)
        if m:
            trouves[m["nom"]] = (m["total"], int(m["n"]))
    return trouves


# « <n>x <durée cumulée>  #<iid> <commande> » — la section des rejeux, qui porte SON ticket (#578).
_REJEU = re.compile(
    r"^\s+(?P<n>\d+)x\s+(?P<total>\S+(?: min)?)\s+#(?P<iid>\d+)\s+(?P<cmd>.+?)\s*$"
)


def _rejeux(sortie: str) -> dict[tuple[str, str], tuple[str, int]]:
    """La section des rejeux : (ticket, commande) -> (durée cumulée, nombre de passages).

    La clé est le COUPLE, et c'est tout le sujet de #578 : la même commande jouée dans deux
    tickets y fait deux entrées distinctes, dont aucune ne compte pour un rejeu.
    """
    trouves = {}
    for ligne in _section_audit(sortie, "Commandes rejouées"):
        m = _REJEU.match(ligne)
        if m:
            trouves[(f"#{m['iid']}", m["cmd"])] = (m["total"], int(m["n"]))
    return trouves


def test_audit_apparie_par_identifiant_meme_quand_des_appels_s_entrelacent(depot: Depot) -> None:
    """La durée d'un appel n'est sur aucune ligne : elle sépare son `tool_use` de SON retour.

    Trois appels sont ouverts avant que le premier ne se referme, et les retours arrivent dans un
    ordre qu'aucune heuristique de position ne devine : « le dernier `tool_use` vu », « le plus
    récent encore ouvert » (LIFO) et « le plus ancien encore ouvert » (FIFO) se trompent chacune au
    moins une fois sur ce flux. Seul l'identifiant tranche — et c'est le cas ordinaire dès que la
    session appelle de front, ce qui arrive à chaque message portant plusieurs `tool_use`.
    """
    flux = (
        _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh"))
        + _appel(2, ("toolu_b", "Read", "docs/10-workflow-git.md"))
        + _appel(4, ("toolu_c", "Grep", "merge-mr"))
        + _retour(10, "toolu_b")
        + _retour(20, "toolu_c", type_d_abord=True)
        + _retour(100, "toolu_a")
    )
    _journal_audit(depot, "entrelace", {473: flux})
    r = depot.lance("journal.sh", "audit", "entrelace")

    assert r.returncode == 0, r.stderr
    assert _postes(r.stdout, "Par outil") == {
        "Bash": ("1.7 min", 1),   # 100 s — et non 6 s, que « le dernier vu » aurait rendu
        "Grep": ("16.0s", 1),     # 20 - 4, et non 20 - 2 (LIFO) ni 20 - 0 (FIFO)
        "Read": ("8.0s", 1),      # 10 - 2, et non 10 - 4 (LIFO) ni 10 - 0 (FIFO)
    }, r.stdout
    assert "Appels restés sans retour" not in r.stdout, "les trois appels sont appariés"


def test_audit_rend_un_rapport_partiel_sur_un_flux_tronque(depot: Depot) -> None:
    """Une session tuée laisse un appel ouvert et une ligne coupée en deux : pas une erreur.

    Ce sont même les runs les plus intéressants à auditer : refuser de les lire, ou taire l'appel
    resté ouvert, ferait passer un run coupé en plein `local.sh` pour un run sans incident.
    """
    coupee = _jsonl({"type": "assistant", "timestamp": _t(50), "message": {"content": [
        {"type": "tool_use", "id": "toolu_coupe", "name": "Bash",
         "input": {"command": "git log"}}]}})
    flux = (
        _appel(0, ("toolu_ok", "Bash", "bash scripts/ci/local.sh"))
        + _retour(30, "toolu_ok")
        + _appel(40, ("toolu_vif", "Bash", "npm test"))
        + coupee[:-30]   # le pilote a été tué pendant l'écriture : la ligne s'arrête au milieu
    )
    _journal_audit(depot, "tronque", {480: flux})
    r = depot.lance("journal.sh", "audit", "tronque")

    assert r.returncode == 0, r.stderr
    assert r.stderr == "", f"un flux tronqué n'est pas une erreur : {r.stderr}"
    assert _postes(r.stdout, "Par outil") == {"Bash": ("30.0s", 1)}, r.stdout
    orphelins = "\n".join(_section_audit(r.stdout, "Appels restés sans retour"))
    assert "npm test" in orphelins, f"l'appel qui n'est jamais revenu doit être nommé : {r.stdout}"


def test_audit_lit_un_flux_compacte_comme_un_flux_en_clair(depot: Depot) -> None:
    """`gc` compacte le `.jsonl` dès le verdict rendu (#198) : lire un `.gz` est le cas NORMAL.

    Un audit qui ne saurait pas les ouvrir ne mesurerait que le run en cours — c'est-à-dire
    justement pas ceux qu'on compare.
    """
    flux = (
        _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh"))
        + _retour(45, "toolu_a")
        + _appel(50, ("toolu_b", "Edit", "scripts/orchestrate/run.sh"))
        + _retour(51, "toolu_b")
    )
    _journal_audit(depot, "clair", {490: flux})
    _journal_audit(depot, "compacte", {490: flux}, compacte=(490,))

    clair = depot.lance("journal.sh", "audit", "clair")
    compacte = depot.lance("journal.sh", "audit", "compacte")

    assert compacte.returncode == 0, compacte.stderr
    assert _postes(compacte.stdout, "Par outil") == {"Bash": ("45.0s", 1), "Edit": ("1.0s", 1)}
    assert _postes(compacte.stdout, "Par outil") == _postes(clair.stdout, "Par outil")


def test_audit_ne_compte_pas_deux_fois_un_ticket_sous_ses_deux_formes(depot: Depot) -> None:
    """Un run rejoué sous le même run-id peut laisser le clair ET le `.gz` du même ticket.

    Les additionner doublerait son temps sans que rien ne le montre — un rapport faux qui a
    exactement l'air d'un rapport juste.
    """
    flux = _appel(0, ("toolu_a", "Bash", "git status")) + _retour(3, "toolu_a")
    dossier = _journal_audit(depot, "double", {491: flux})
    with gzip.open(dossier / "491.jsonl.gz", "wt", encoding="utf-8", newline="\n") as f:
        f.write(flux)

    r = depot.lance("journal.sh", "audit", "double")

    assert r.returncode == 0, r.stderr
    assert _postes(r.stdout, "Par outil") == {"Bash": ("3.0s", 1)}, r.stdout


def test_audit_ecarte_le_cd_de_prefixe_en_regroupant_les_commandes(depot: Depot) -> None:
    """Le prompt d'une session autonome préfixe presque tous ses appels d'un `cd "<worktree>"`.

    C'est la règle #234 — la cible doit rester dans le worktree —, si bien que garder le préfixe
    rangerait tout le run sous une seule forme, « cd », qui ne dirait plus rien de personne. Même
    raison que le maillon d'une chaîne compté pour lui-même dans `refus`.

    Ce cas éprouve du même coup le DÉSESCAPAGE : la commande porte des guillemets échappés dans le
    JSON, et une extraction qui s'arrêterait au premier rendrait la commande amputée dès son
    deuxième caractère — la panne que #496 a corrigée dans la vue, et que l'audit ne peut pas se
    permettre de refaire : elle rangerait, elle aussi, tout le run sous une seule et même forme.
    """
    worktree = "E:/Projets/maestro-worktrees/498-la-commande"
    flux = (
        _appel(0, ("toolu_a", "Bash", f'cd "{worktree}" && bash scripts/ci/local.sh'))
        + _retour(60, "toolu_a")
        + _appel(61, ("toolu_b", "Bash", "bash scripts/ci/local.sh 2>&1 | tail -40"))
        + _retour(101, "toolu_b")
        + _appel(102, ("toolu_c", "Bash", f'cd "{worktree}" && git status'))
        + _retour(104, "toolu_c")
    )
    _journal_audit(depot, "formes", {492: flux})
    r = depot.lance("journal.sh", "audit", "formes")

    assert r.returncode == 0, r.stderr
    formes = _postes(r.stdout, "Bash, par forme")
    assert formes == {"bash scripts/ci/local.sh": ("1.7 min", 2), "git status": ("2.0s", 1)}, \
        r.stdout
    assert not [f for f in formes if f.startswith("cd")], \
        f"le `cd` de préfixe rangerait tout le run sous une seule forme : {formes}"


# Les rejeux se comptent DANS UN TICKET, jamais sur le run (#578)
# -------------------------------------------------------------------------------------
# Le journal de ces deux tests est un ÉCHANTILLON FAUTIF : il porte, côte à côte, les deux appels
# qui remontaient à tort — le filet CI joué une fois avant chaque push, et un verbe `lib.sh` sur le
# parent commun de deux lots — et un rejeu qui, lui, en est un. Prouver l'absence des premiers ne
# vaut que si le second est présent dans la même sortie : sans lui, une section vide, un titre
# renommé ou un parser mal branché rendraient un ✓ sur une question jamais posée.


def _run_deux_lots(depot: Depot, run_id: str) -> None:
    """Deux tickets d'un même parent — le décor où #578 se voyait, et le plus banal des runs.

    Chaque ticket joue le filet CI avant son push et interroge le parent commun : deux chaînes
    identiques d'un ticket à l'autre, dont aucune n'est rejouée. Le ticket #571 rejoue en plus une
    suite de tests qui vient d'échouer — le seul vrai rejeu du run, et le seul que la section doit
    retenir.
    """
    pytest_ = ".venv/Scripts/python.exe -m pytest tests/test_queue.py"
    _journal_audit(depot, run_id, {
        571: (
            _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh 2>&1 | tail -45"))
            + _retour(60, "toolu_a")
            + _appel(61, ("toolu_b", "Bash", "bash scripts/gitlab/lib.sh subtickets 569"))
            + _retour(71, "toolu_b")
            + _appel(100, ("toolu_c", "Bash", pytest_))      # rouge
            + _retour(120, "toolu_c")
            + _appel(121, ("toolu_d", "Bash", pytest_))      # la reprise, à l'identique
            + _retour(141, "toolu_d")
        ),
        572: (
            _appel(0, ("toolu_e", "Bash", "bash scripts/ci/local.sh 2>&1 | tail -45"))
            + _retour(90, "toolu_e")
            + _appel(91, ("toolu_f", "Bash", "bash scripts/gitlab/lib.sh subtickets 569"))
            + _retour(101, "toolu_f")
            + _appel(110, ("toolu_g", "Bash", "git status --short"))
            + _retour(112, "toolu_g")
        ),
    })


def test_un_appel_une_fois_par_ticket_n_est_pas_un_rejeu(depot: Depot) -> None:
    """La clé était la commande SEULE, agrégée sur tout le run : un appel joué une fois par ticket
    remontait donc « 2x … au-delà du premier passage » dès que le run portait deux tickets.

    Ce n'est pas un défaut de calcul — la ligne était exacte au sens littéral — mais de LECTURE, et
    il grandit avec le run : sur douze tickets la section serait dominée par les douze passages du
    filet CI, c'est-à-dire par le coût le plus attendu de tous, dans la section faite pour montrer
    l'inattendu. Mesuré sur le run `20260826-134119`, dont le premier poste était
    `bash scripts/ci/local.sh` à 7,3 min.
    """
    _run_deux_lots(depot, "deux-lots")
    r = depot.lance("journal.sh", "audit", "deux-lots")

    assert r.returncode == 0, r.stderr

    # D'abord : l'échantillon porte BIEN le piège. Les deux chaînes structurelles ont été jouées
    # deux fois chacune sur le run — c'est ce que comptait l'ancienne clé. Sans ce contrôle, un
    # échantillon adouci plus tard (deux chaînes qui cesseraient d'être identiques) laisserait les
    # deux absences ci-dessous passer pour un verdict.
    formes = _postes(r.stdout, "Bash, par forme")
    assert formes["bash scripts/ci/local.sh"] == ("2.5 min", 2), formes
    assert formes["bash scripts/gitlab/lib.sh"] == ("20.0s", 2), formes

    # Puis ce que la section garde : sans cette moitié, tout le reste passerait sur une sortie
    # vide. Le ticket est NOMMÉ — « rejoué » sans dire où ne se vérifie pas.
    assert _rejeux(r.stdout) == {
        ("#571", ".venv/Scripts/python.exe -m pytest tests/test_queue.py"): ("40.0s", 2),
    }, r.stdout

    # Et enfin ce qu'elle écarte, appel par appel.
    joues = "\n".join(_section_audit(r.stdout, "Commandes rejouées"))
    assert "scripts/ci/local.sh" not in joues, \
        f"le filet CI est joué une fois par ticket, jamais rejoué : {joues}"
    assert "subtickets 569" not in joues, \
        f"le parent commun est interrogé une fois depuis chaque lot : {joues}"


def test_le_total_des_rejeux_ne_compte_que_ce_que_la_section_retient(depot: Depot) -> None:
    """Le chiffre du titre est ce qu'on lit en premier, et c'est sur lui qu'on décide de chercher.

    Compté sur toutes les répétitions du run, il annoncerait un gisement d'économie que les lignes
    en dessous ne montreraient pas : 1.8 min ici, dont 1.5 min de filet CI irréductible. Le titre
    porte en outre la PORTÉE, faute de quoi « rejouées à l'identique » se relit « sur tout le run ».
    """
    _run_deux_lots(depot, "total-rejeux")
    r = depot.lance("journal.sh", "audit", "total-rejeux")

    assert r.returncode == 0, r.stderr
    titre = next(x for x in r.stdout.splitlines() if x.startswith("── Commandes rejouées"))

    # 40 s de pytest moins son premier passage (20 s) — et rien d'autre. Les 150 s de filet CI et
    # les 20 s de `subtickets` n'y entrent plus, ni en lignes ni au compteur.
    assert "20.0s" in titre, f"le total ne compte que le rejeu réel : {titre}"
    assert "min" not in titre, f"1.8 min serait le total d'avant #578 : {titre}"
    assert "dans un même ticket" in titre, f"l'intitulé doit dire sur quoi il porte : {titre}"


def test_audit_ne_passe_ni_par_jq_ni_par_python(depot: Depot) -> None:
    """Le pilote est un script shell et le reste (#180) : l'audit se lit en `awk`, comme `refus`.

    La preuve se fait à l'EXÉCUTION plutôt que par un `grep` du script, et c'est le seul moyen
    honnête : `journal.sh` nomme `jq` et Python dans ses commentaires, et `python` figure dans une
    expression régulière de `refus` — un motif textuel dirait donc l'inverse de la vérité. Trois
    bouchons en tête de `PATH` dénoncent l'appel s'il a lieu ; un rapport complet et un témoin
    absent prouvent qu'il n'a pas eu lieu.
    """
    for nom in ("jq", "python", "python3"):
        chemin = depot.racine.parent / "bin" / nom
        chemin.write_text(
            "#!/usr/bin/env bash\n"
            'echo "$0" >> "$MAESTRO_FIXTURES/interprete.log"\n'
            "exit 127\n",
            encoding="utf-8", newline="\n",
        )
        chemin.chmod(0o755)

    flux = _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh")) + _retour(42, "toolu_a")
    _journal_audit(depot, "shell-pur", {489: flux})
    r = depot.lance("journal.sh", "audit", "shell-pur")

    assert r.returncode == 0, r.stderr
    assert _postes(r.stdout, "Par outil") == {"Bash": ("42.0s", 1)}, r.stdout
    temoin = depot.fixtures / "interprete.log"
    assert not temoin.exists(), (
        f"l'audit a appelé un interpréteur : {temoin.read_text(encoding='utf-8')}"
    )


def test_audit_ne_touche_a_rien_et_mesure_un_run_encore_en_vol(depot: Depot) -> None:
    """Deux promesses en une, et la seconde est ce qui le distingue de `refus`.

    Lecture seule d'abord : l'audit sert à décider, il ne décide pas. Puis, sans argument, il part
    du dernier run qui porte un FLUX et non un RÉSULTAT — un run en cours n'a pas encore rendu la
    main, mais son `.jsonl` est déjà écrit et parfaitement mesurable. Exiger un résultat renverrait
    sur le run précédent celui qu'on regarde tourner, c'est-à-dire sur la mauvaise réponse à la
    question la plus fréquente.
    """
    _journal_audit(depot, "20260801-100000", {
        470: _appel(0, ("toolu_z", "Bash", "git log")) + _retour(1, "toolu_z"),
    })
    # Aucun `<iid>.json` : ce run n'a pas rendu son verdict, il est encore en vol.
    dossier = _journal_audit(depot, "20260804-100000", {
        473: _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh")) + _retour(75, "toolu_a"),
    })
    fichiers = [p for p in sorted(dossier.rglob("*")) if p.is_file()]
    empreinte = {p: p.stat().st_mtime_ns for p in fichiers}

    r = depot.lance("journal.sh", "audit")

    assert r.returncode == 0, r.stderr
    assert "run 20260804-100000" in r.stdout, "le dernier run qui porte un flux, en vol ou non"
    assert _postes(r.stdout, "Par outil") == {"Bash": ("75.0s", 1)}, r.stdout
    assert {p: p.stat().st_mtime_ns for p in fichiers} == empreinte, "l'audit n'écrit rien"


# =====================================================================================
# L'audit se JOUE en fin de run, au lieu de s'inviter (#530)
# =====================================================================================
#
# #498 imprimait une invitation dans le résumé — ce qui suppose quelqu'un devant la console au bon
# moment, or un run `--detach` se termine dans une fenêtre que personne ne regarde. Le partage se
# fait donc sur le COÛT : le pilote ÉCRIT le rapport (quelques secondes de CPU, aucun quota), une
# session PROPOSE le jugement (`/orchestrate --status`, hors de ce fichier).
#
# Ces quatre tests gardent le PILOTE et non l'audit, dont les huit sections sont éprouvées juste
# au-dessus sur un journal fabriqué. Ils tournent donc sur un vrai run du harnais : ce qu'on veut
# savoir est quand le pilote appelle, ce qu'il fait du résultat, et ce qu'il ne fait pas.


def _stub_flux_mesurable(depot: Depot, iid: int = 130) -> str:
    """Un bouchon de session dont le flux est MESURABLE : des appels horodatés et appariés.

    `_stub_flux` suffit à la vue, qui ne lit que les `tool_use`. L'audit, lui, apparie chaque appel
    à son retour et soustrait des horodatages : sur un flux qui n'en porte pas, il rend « aucun
    appel mesurable » — un fichier écrit, non vide, et qui ne prouverait rien de son contenu.
    """
    flux = (
        _appel(0, ("toolu_a", "Bash", "bash scripts/ci/local.sh"))
        + _retour(45, "toolu_a")
        + _appel(46, ("toolu_b", "Edit", "scripts/orchestrate/run.sh"))
        + _retour(47, "toolu_b")
    ).rstrip("\n")
    return _claude_stub(depot, f"""
        printf '%s' '{_statut_json(str(iid), "En revue")}' > "$MAESTRO_FIXTURES/owner-{iid}.json"
        cat <<'FLUX'
{flux}
{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}
FLUX
        exit 0
    """)


def _journal_espion(depot: Depot, *, code: int = 0) -> None:
    """Remplace `journal.sh` par un espion — le seul moyen d'observer un ORDRE et une ABSTENTION.

    Il note deux choses qu'aucun état final ne montre : ce que le pilote a demandé au journal, et ce
    que le répertoire du run contenait AU MOMENT où il l'a demandé. Il écrit toujours quelque chose
    avant de rendre son code, ce qui est justement ce qui rend `code != 0` intéressant : un
    `audit.txt` tronqué existe alors sur le disque, et le pilote doit le retirer.
    """
    chemin = depot.racine / "scripts/orchestrate/journal.sh"
    chemin.write_text(
        "#!/usr/bin/env bash\n"
        'racine="${0%/scripts/orchestrate/journal.sh}"\n'
        "printf '%s\\n' \"$*\" >> \"$MAESTRO_FIXTURES/journal.log\"\n"
        '[ "$1" = audit ] || exit 0\n'
        'ls "$racine/.maestro/orchestrate/$2" > "$MAESTRO_FIXTURES/instant.txt" 2>/dev/null\n'
        "printf 'rapport partiel'\n"
        f"exit {code}\n",
        encoding="utf-8",
        newline="\n",
    )
    chemin.chmod(0o755)


def _run_audite(depot: Depot, run_id: str, **env: str) -> subprocess.CompletedProcess:
    """Un run d'un ticket, dont la session laisse un flux mesurable."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    return depot.lance("run.sh", "--plan", plan, "--run-id", run_id,
                       env={"MAESTRO_CLAUDE_BIN": _stub_flux_mesurable(depot), **env})


def test_le_pilote_fige_l_audit_du_run_dans_son_journal(depot: Depot) -> None:
    """Le rapport est écrit, il est JUSTE, et le résumé le nomme au lieu d'inviter à le refaire.

    L'invitation de #498 est remplacée et non doublée : la nommer encore laisserait croire qu'il
    reste une commande à jouer. Ce qui reste proposé est le JUGEMENT — `/run-audit`, qui coûte du
    quota et ouvre une session, donc qui se demande.
    """
    r = _run_audite(depot, "audit-fige")
    assert r.returncode == 0, r.stdout + r.stderr

    rapport = (depot.racine / ".maestro/orchestrate/audit-fige/audit.txt").read_text(
        encoding="utf-8")
    assert _postes(rapport, "Par outil") == {"Bash": ("45.0s", 1), "Edit": ("1.0s", 1)}, rapport
    assert "audit.txt" in r.stdout, "le résumé nomme le rapport, il ne le fait pas deviner"
    assert "/run-audit audit-fige" in r.stdout, "le jugement, lui, reste une proposition"
    assert "journal.sh audit audit-fige" not in r.stdout, \
        "l'invitation de #498 est REMPLACÉE, pas doublée"


def test_l_audit_de_fin_de_run_est_ecrit_apres_la_compaction_des_flux(depot: Depot) -> None:
    """L'ordre, et pas seulement le résultat : `audit` sait relire un `.gz`, mais l'écrire avant la
    compaction ferait dépendre le rapport d'un ordre que rien n'oblige à tenir.

    Observé à l'instant de l'appel — un état final ne dirait rien, les deux gestes ayant tous deux
    eu lieu à la fin du run.
    """
    _journal_espion(depot)
    r = _run_audite(depot, "apres-compaction")
    assert r.returncode == 0, r.stdout + r.stderr

    instant = (depot.fixtures / "instant.txt").read_text(encoding="utf-8").split()
    assert "130.jsonl.gz" in instant, f"le flux devait être compacté avant l'audit : {instant}"
    assert "130.jsonl" not in instant, f"le flux clair survit à la compaction : {instant}"


def test_l_ecriture_de_l_audit_est_best_effort(depot: Depot) -> None:
    """Même statut que `gc` : son échec ne change ni le verdict du run ni son code de sortie.

    Et il ne laisse RIEN derrière : « `audit.txt` est là » doit vouloir dire « le rapport est
    complet », sans quoi la prochaine lecture jugerait un run sur un rapport tronqué. Le résumé
    retombe alors sur la commande — retirer l'invitation de #498 sans la remplacer laisserait un run
    sans rien à dire sur son temps.
    """
    _journal_espion(depot, code=1)
    r = _run_audite(depot, "audit-casse")
    assert r.returncode == 0, "un run réussi reste réussi sans son audit"

    assert "1 réussi(s)" in r.stdout, "le verdict du run est intact"
    assert not (depot.racine / ".maestro/orchestrate/audit-casse/audit.txt").exists(), \
        "un rapport tronqué est retiré, jamais gardé ni nommé"
    assert "journal.sh audit audit-casse" in r.stdout, \
        "sans rapport, le résumé rend l'invitation qu'il remplaçait"


def test_un_commutateur_eteint_l_ecriture_de_l_audit(depot: Depot) -> None:
    """`MAESTRO_AUDIT_FIN_RUN=0`, sur le modèle des autres greffes best-effort du pilote.

    L'appel lui-même n'a pas lieu — c'est ce que l'espion prouve, et un simple `audit.txt` absent ne
    dirait pas la différence entre « éteint » et « en échec », que le test précédent sépare.
    """
    _journal_espion(depot)
    # Un run-id sans le mot « audit » : le journal de l'espion porte le run-id de chaque appel, et
    # `gc --auto --courant <run-id>` suffirait sinon à faire matcher le verbe qu'on cherche à ne PAS
    # voir — un test vert pour la mauvaise raison, ou rouge pour rien.
    r = _run_audite(depot, "eteint", MAESTRO_AUDIT_FIN_RUN="0")
    assert r.returncode == 0, r.stdout + r.stderr

    demandes = (depot.fixtures / "journal.log").read_text(encoding="utf-8").splitlines()
    assert any(d.startswith("gc --auto") for d in demandes), \
        "le reste du journal continue de tourner"
    assert not [d for d in demandes if d.startswith("audit")], \
        f"le commutateur éteint l'APPEL, pas seulement le fichier : {demandes}"
    assert not (depot.racine / ".maestro/orchestrate/eteint/audit.txt").exists()
    assert "journal.sh audit eteint" in r.stdout


# =====================================================================================
# `main` remise à niveau au démarrage d'un run (#283)
# =====================================================================================
#
# Un run est ce qui fait vieillir le plus vite la ref LOCALE `refs/heads/main` du clone principal :
# il ouvre N PR destinées à être mergées, et plus personne ne repasse par `main` depuis #181. Elle
# n'avançait jusqu'ici qu'à l'intérieur d'une session (`worktree.sh ensure`, donc /ticket-start) —
# donc pas du tout quand le run part sur un plan vide, saute tous ses tickets ou échoue avant le
# premier. Le code produit, lui, n'a jamais été en cause : chaque worktree part d'`origin/main`.
#
# Ces tests portent donc sur la ref locale, et jamais sur du réseau : le dépôt jetable n'a aucun
# distant, `refs/remotes/origin/main` y est une simple référence posée à la main (comme pour
# status.sh), et le `git fetch` de `sync-main` y échoue en silence — exactement le cas « hors
# ligne » que le helper sait traiter.


def _git(depot: Depot, *args: str) -> str:
    assert GIT is not None
    return subprocess.run(  # noqa: S603
        [GIT, *args], cwd=str(depot.racine), check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_git_sur_main(depot: Depot) -> None:
    """Le dépôt jetable en CLONE PRINCIPAL : posé sur `main`, propre, avec un `origin/main` local.

    C'est la situation réelle d'un run depuis #181 (le clone principal ne change plus de branche),
    et celle qui met `sync-main` sur son chemin le plus délicat : `main` étant EMPRUNTÉE par un
    répertoire de travail, la ref ne se pose pas — elle s'avance par un `merge --ff-only` dans ce
    répertoire-là. D'où le `.gitignore` : le plan et le journal du run salissent l'arbre, et un
    arbre sale fait (à juste titre) renoncer le helper.
    """
    _git(depot, "init", "--quiet", "--initial-branch=main")
    _git(depot, "config", "user.email", "test@maestro.invalid")
    _git(depot, "config", "user.name", "Maestro Test")
    (depot.racine / ".gitignore").write_text(".maestro/\nplan.tsv\n", encoding="utf-8",
                                             newline="\n")
    _git(depot, "add", "-A")
    _git(depot, "-c", "core.hooksPath=", "commit", "--quiet", "-m", "chore: depot jetable")
    _git(depot, "update-ref", "refs/remotes/origin/main", "HEAD")


def _commit(depot: Depot, fichier: str, message: str) -> str:
    (depot.racine / fichier).write_text(message, encoding="utf-8", newline="\n")
    _git(depot, "add", fichier)
    _git(depot, "-c", "core.hooksPath=", "commit", "--quiet", "-m", message)
    return _git(depot, "rev-parse", "HEAD")


def _origin_main_avance(depot: Depot) -> str:
    """Un commit de plus sur `origin/main`, et rien sur `main` : le retard type d'après un merge."""
    _git(depot, "checkout", "--quiet", "-b", "amont")
    sha = _commit(depot, "livre.txt", "feat: un lot merge pendant la nuit")
    _git(depot, "update-ref", "refs/remotes/origin/main", sha)
    _git(depot, "checkout", "--quiet", "main")
    _git(depot, "branch", "--quiet", "-D", "amont")
    return sha


def _run_d_un_ticket(depot: Depot, run_id: str, **env: str) -> subprocess.CompletedProcess:
    """Un run d'un ticket, livré (PR ouverte + « En revue ») — le décor de ces tests."""
    depot.ticket(130, "Ticket a traiter")
    depot.mr("feat/130-ticket-a-traiter", "opened")
    claude = _claude_stub(depot, f"""
        printf '%s' '{_statut_json("130", "En revue")}' > "$MAESTRO_FIXTURES/owner-130.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])
    return depot.lance("run.sh", "--plan", plan, "--run-id", run_id,
                       env={"MAESTRO_CLAUDE_BIN": claude, **env})


@besoin_git
def test_un_run_remet_main_a_niveau_avant_son_premier_ticket(depot: Depot) -> None:
    _init_git_sur_main(depot)
    livre = _origin_main_avance(depot)

    r = _run_d_un_ticket(depot, "amont")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == livre
    # La ref ne suffit pas : `main` est empruntée par ce répertoire, donc son ARBRE doit avoir suivi
    # — sans quoi tout le delta apparaîtrait en « supprimé » au prochain git status.
    assert (depot.racine / "livre.txt").exists(), "le répertoire de travail a suivi la ref"
    assert "main mis à jour" in r.stdout, "le run le dit, il ne le fait pas en douce"


@besoin_git
def test_main_deja_a_jour_ne_dit_rien(depot: Depot) -> None:
    """Le cas de loin le plus fréquent : une ligne à chaque run n'apprendrait rien à personne."""
    _init_git_sur_main(depot)
    r = _run_d_un_ticket(depot, "ajour")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "sync-main" not in r.stdout and "main mis à jour" not in r.stdout


@besoin_git
def test_le_dry_run_ne_touche_pas_a_main_mais_annonce_l_etape(depot: Depot) -> None:
    _init_git_sur_main(depot)
    avant = _git(depot, "rev-parse", "refs/heads/main")
    _origin_main_avance(depot)
    plan = _plan(depot, [(1, 130, "-", "moyenne")])

    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "sec")

    assert r.returncode == 0, r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == avant, (
        "« rien n'a été lancé » vaut aussi pour main"
    )
    assert "sync-main" in r.stdout, "…mais le dry-run dit ce qu'un vrai run ferait"


@besoin_git
def test_une_main_divergente_est_signalee_sans_empecher_le_run(depot: Depot) -> None:
    """`sync-main` s'abstient plutôt que de forcer (#205) — et son abstention n'annule pas un run.

    Un `main` local divergent porte un commit que personne n'a poussé : l'écraser serait une perte
    de données. Mais refuser de traiter le backlog pour autant le serait tout autant, à l'échelle
    d'une nuit entière.
    """
    _init_git_sur_main(depot)
    _origin_main_avance(depot)
    local = _commit(depot, "local.txt", "chore: commit local jamais pousse")

    r = _run_d_un_ticket(depot, "diverge")

    assert r.returncode == 0, "le run a traité son ticket malgré l'abstention"
    assert _git(depot, "rev-parse", "refs/heads/main") == local, "rien n'a été écrasé"
    assert "divergé" in r.stderr, "l'abstention est relayée, pas avalée"


@besoin_git
def test_maestro_sync_main_a_zero_eteint_l_etape(depot: Depot) -> None:
    """Même interrupteur que /ticket-start : un poste peut vouloir garder la main sur sa `main`."""
    _init_git_sur_main(depot)
    avant = _git(depot, "rev-parse", "refs/heads/main")
    _origin_main_avance(depot)

    r = _run_d_un_ticket(depot, "eteint", MAESTRO_SYNC_MAIN="0")

    assert r.returncode == 0, r.stdout + r.stderr
    assert _git(depot, "rev-parse", "refs/heads/main") == avant
    assert "main mis à jour" not in r.stdout


# =====================================================================================
# L'orchestration concurrente — la couverture des lots 1 à 4 (#292, parent #287)
# =====================================================================================
#
# Lot final du chantier : il ne pouvait s'écrire qu'une fois #288 à #291 livrés. Deux morceaux de la
# couverture sont restés dans leur lot, pour la seule raison qui vaille — ils ne se simulent pas :
# l'arrêt de N sessions (#291, de vrais processus qu'on tue) et l'attente partagée d'une limite
# d'usage (#291, deux sessions qui doivent se ranger derrière le même rendez-vous). Tout le reste
# est ici.
#
# Le décor est celui de tout ce fichier — ni réseau, ni quota, ni écriture GitLab — avec une
# contrainte de plus, propre à la concurrence : **ce qui doit être simultané l'est par une BARRIÈRE,
# jamais par un `sleep`**. Chaque session bouchon signale son arrivée puis attend celle des autres.
# Sans cela, « deux tickets en vol » serait une course que la charge de la machine tranche, et le
# test dirait tantôt le code, tantôt l'ordonnancement du système. Et la **mesure** obéit à la même
# règle que ce qu'elle mesure (#313) : aucun état partagé, chaque session n'écrivant que ses propres
# fichiers — un compteur commun, lu puis réécrit, perdait une incrémentation dès que deux sessions
# arrivaient ensemble, soit précisément ce que la barrière provoque.


def _plan_groupes(depot: Depot, lignes: list[tuple[int, int, str, str, str]]) -> str:
    """Un plan figé dont on choisit le GROUPE de chaque ligne — ce que `_plan` ne permet pas.

    `_plan` dérive le groupe du rang (« <parent>.<rang> »), ce qui donne à chaque lot le sien et
    rend tout parent séquentiel : parfait pour les tests d'avant #288, inutilisable pour éprouver
    l'indépendance. Ici la colonne est posée à la main, exactement comme `queue.sh` la calcule.
    """
    chemin = depot.racine / "plan-groupes.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        + "".join(f"{rang}\t{iid}\t{parent}\t{prio}\t{groupe}\tTicket {iid}\n"
                  for rang, iid, parent, prio, groupe in lignes),
        encoding="utf-8",
        newline="\n",
    )
    return str(chemin)


def _stub_barriere(depot: Depot, iids: tuple[int, ...], *, apres: str = "") -> str:
    """Un bouchon `claude` qui livre son ticket, mais pas avant que TOUS soient arrivés.

    C'est ce qui rend « N en vol en même temps » observable sans dépendre d'un `sleep` : la première
    session ne peut pas se solder avant que la dernière soit partie, donc le pilote a forcément eu
    ses N créneaux occupés. Le bouchon note aussi son passage (`vus.txt`) et son relevé de
    simultanéité (`pic-<iid>`, agrégé par `_pic`), les deux mesures dont les tests d'ordonnancement
    se servent.
    """
    attente = " ".join(f'[ -e "$MAESTRO_FIXTURES/arrivee-{i}" ] &&' for i in iids)
    gabarit = _statut_json("$iid", "En revue")
    # « ticket GitLab #N » au premier tour, « le ticket #N » à la reprise : les deux formes
    # comptent, sans quoi une session rouverte n'écrirait son verdict sous aucun nom (et le test
    # dirait, à tort, que la reprise n'a rien livré).
    return _claude_stub(depot, f"""
        iid="$(printf '%s\\n' "$@" | grep -oE 'ticket (GitLab )?#[0-9]+' | head -1 |
               grep -oE '[0-9]+$')"
        printf '%s\\n' "$iid" >> "$MAESTRO_FIXTURES/vus.txt"
        # Le pic de simultanéité, mesuré par les sessions elles-mêmes — c'est la seule façon de
        # constater qu'on N'A JAMAIS eu deux tickets liés en vol : une lecture d'après coup ne
        # distingue pas « jamais ensemble » de « ensemble mais vite ». Chaque session pose SON
        # marqueur d'entrée, compte les marqueurs présents et écrit ce relevé dans SON fichier ;
        # aucun fichier n'a deux écrivains, et `_pic` prend le maximum après coup (#313). Le
        # compteur partagé d'avant — lu puis réécrit en deux commandes — perdait une incrémentation
        # dès que deux sessions arrivaient ensemble, c'est-à-dire exactement ce que la barrière rend
        # probable : le pic plafonnait sous le nombre réel de sessions en vol, et le test disait
        # l'ordonnancement de la machine plutôt que le code.
        : > "$MAESTRO_FIXTURES/en-vol-$iid"
        printf '%s' "$(set -- "$MAESTRO_FIXTURES"/en-vol-*; printf '%s' "$#")" \\
          > "$MAESTRO_FIXTURES/pic-$iid"
        # Relevé pris AVANT de signaler son arrivée : les sessions déjà là attendent encore la
        # nôtre, donc aucune n'a pu retirer son marqueur. La dernière arrivée voit ainsi tout le
        # monde — et le pic d'un ensemble d'intervalles est toujours atteint juste après une
        # arrivée, donc ces N relevés suffisent à le tenir.
        : > "$MAESTRO_FIXTURES/arrivee-$iid"
        # 45 s (900 × 0,05) : la barrière est un garde-fou contre un blocage réel, pas une fenêtre
        # de tir. Trop courte — 15 s avant #313 —, elle fait sortir la première session avant que la
        # dernière soit lancée sur une machine chargée, le montage des worktrees étant sérialisé :
        # pic légitimement bas, test rouge, produit correct. Elle reste bornée par le `timeout` de
        # `Depot.lance`, qui doit couvrir N sessions y renonçant l'une après l'autre — c'est ce qui
        # arrive si le run redevient séquentiel pour de bon, et le test doit alors le DIRE. Une
        # session qui renonce le signale (`abandon-<iid>`), sans quoi une mesure non concluante
        # passerait pour un verdict.
        attendu=1
        for _ in $(seq 1 900); do
          {attente} {{ attendu=0; break; }}
          sleep 0.05
        done
        [ "$attendu" = 0 ] || : > "$MAESTRO_FIXTURES/abandon-$iid"
        {apres}
        printf '%s' '{gabarit}' > "$MAESTRO_FIXTURES/owner-$iid.json"
        rm -f "$MAESTRO_FIXTURES/en-vol-$iid"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":2}}\\n'
        exit 0
    """)


def _pic(depot: Depot) -> int:
    """Le pic de simultanéité du run : le plus grand relevé qu'une session ait pris (#313).

    Le maximum est pris ICI, une fois toutes les sessions sorties, et non tenu en vol par un
    compteur partagé : côté bouchon il ne reste que des écritures à un seul auteur, seule forme
    qu'aucune course ne peut fausser. (Le passage dans `vus.txt`, lui, reste un `>>` partagé —
    une ligne courte ouverte en `O_APPEND`, indivisible, là où un lire-modifier-écrire réparti sur
    deux commandes ne peut jamais l'être.)

    Une barrière abandonnée invalide la mesure sans la rendre absurde : on le dit plutôt que de
    laisser un pic trop bas passer pour un verdict sur le code.
    """
    abandons = sorted(p.name.removeprefix("abandon-") for p in depot.fixtures.glob("abandon-*"))
    assert not abandons, (
        f"barrière abandonnée par {abandons} : les sessions n'ont jamais été lancées ensemble "
        "(machine surchargée ?) — la mesure du pic n'est pas concluante, et ce n'est pas un "
        "verdict sur le code"
    )
    return max((int(p.read_text(encoding="utf-8")) for p in depot.fixtures.glob("pic-*")),
               default=0)


def _livrables(depot: Depot, iids: tuple[int, ...]) -> None:
    """Déclare des tickets libres dont la PR est déjà ouverte — de quoi rendre un verdict « OK »."""
    for iid in iids:
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")


def _resume(run_dir: Path) -> list[list[str]]:
    return [ligne.split("\t")
            for ligne in (run_dir / "resume.tsv").read_text(encoding="utf-8").splitlines()
            if ligne and not ligne.startswith("#")]


# --- Lot 1 : le plan déclare ce qui est indépendant (#288) ---------------------------------------


def _parent_a_vagues(depot: Depot, marques: list[bool]) -> None:
    """Un parent dont les lots portent (ou non) le marqueur « (parallèle) », dans cet ordre."""
    lots = [(501 + i, f"Lot {i + 1}", p) for i, p in enumerate(marques)]
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=lots)
    for iid, titre, _ in lots:
        depot.ticket(iid, titre, parent=500)
    depot.publie()


def _groupes_du_plan(sortie: str) -> dict[str, str]:
    return {ligne[1]: ligne[4] for ligne in _lignes_du_plan(sortie)}


def test_une_suite_de_lots_marques_forme_une_seule_vague(depot: Depot) -> None:
    """Le cœur de #288 : le marqueur de la checklist cesse d'être jeté après le tri.

    Deux lots marqués qui se suivent tombent dans la MÊME vague, donc dans le même groupe — c'est
    exactement ce que le run pourra mener de front. Le lot non marqué qui les précède et celui qui
    les suit sont chacun leur propre barrière.
    """
    _parent_a_vagues(depot, [False, True, True, False])
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert groupes["502"] == groupes["503"], "deux lots marqués consécutifs partent ensemble"
    assert len({groupes["501"], groupes["502"], groupes["504"]}) == 3, (
        "un lot non marqué est une barrière : ni avec ce qui précède, ni avec ce qui suit"
    )


def test_un_seul_lot_marque_dans_une_chaine_reste_seul_dans_sa_vague(depot: Depot) -> None:
    """Le cas que la règle du parent, prise à la lettre, rendrait faux.

    « Deux lots du même parent tous deux marqués » suppose DEUX marqués. Un seul lot marqué au
    milieu de lots qui ne le sont pas n'est indépendant de personne : il forme sa propre vague, et
    le run reste séquentiel. Se tromper ici lancerait un lot par-dessus un prédécesseur non terminé.
    """
    _parent_a_vagues(depot, [False, True, False])
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert len(set(groupes.values())) == 3, f"trois vagues distinctes attendues — {groupes}"


def test_les_tickets_hors_lot_partagent_le_groupe_neutre(depot: Depot) -> None:
    """L'autre moitié de la règle : `parent` ne les départage pas (ils portent tous « - »), c'est
    leur groupe commun qui les rend indépendants entre eux."""
    depot.milestone("Phase X")
    for iid in (601, 602, 603):
        depot.ticket(iid, f"Isolé {iid}")
    depot.publie()
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert set(groupes.values()) == {"-"}, f"un seul groupe pour tout le hors-lot — {groupes}"


def test_la_vague_se_compte_sur_toute_la_checklist_lots_livres_compris(depot: Depot) -> None:
    """Un lot déjà livré ne disparaît pas de la chaîne : il continue de faire barrière.

    Sans cela le groupe d'un lot dépendrait de ce qui reste à faire au moment du calcul — deux runs
    successifs sur le même parent ne diraient pas la même chose, et le second pourrait paralléliser
    ce que le premier tenait pour séquentiel.
    """
    lots = [(501, "Lot 1", False), (502, "Lot 2", True), (503, "Lot 3", True)]
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=lots)
    depot.ticket(501, "Lot 1", parent=500, statut="Terminé")
    depot.ticket(502, "Lot 2", parent=500)
    depot.ticket(503, "Lot 3", parent=500)
    depot.publie()
    groupes = _groupes_du_plan(depot.lance("queue.sh").stdout)
    assert "501" not in groupes, "le lot livré n'est plus à traiter"
    assert groupes["502"] == groupes["503"] == "500.2", (
        f"la vague reste comptée depuis le premier lot de la checklist — {groupes}"
    )


def test_le_check_rend_les_groupes_lisibles(depot: Depot) -> None:
    """Une colonne de plus dans le plan ne dit pas d'elle-même ce qu'elle a conclu."""
    _parent_a_vagues(depot, [False, True, True])
    r = depot.lance("queue.sh", "--check")
    assert "groupes de dépendance" in r.stderr
    assert "(parallélisables)" in r.stderr, "un groupe à plusieurs membres est nommé comme tel"
    assert "#502, #503" in r.stderr, "et ses membres listés dans l'ordre du plan"


# --- L'arbitrage manquant : ce que la colonne « groupe » ne peut pas dire (#562) ------------------
#
# Le marqueur est FACULTATIF (#160). Un parent sans un seul marqueur rend donc autant de vagues que
# de lots — un plan parfaitement séquentiel, indiscernable d'un séquentiel VOULU. Ces tests portent
# sur la seule question qu'une machine tranche : « ce parent a-t-il été arbitré ? ».


def _non_arbitres(sortie: str) -> list[list[str]]:
    return [ligne.split("\t") for ligne in sortie.splitlines()
            if ligne and not ligne.startswith("#")]


def test_un_parent_sans_aucun_marqueur_est_dit_jamais_arbitre(depot: Depot) -> None:
    """Le cas nominal du signalement, avec ses comptes.

    `au-plan` et `lots` sont distincts à dessein : un parent dont un seul lot reste à faire est
    aussi peu arbitré qu'un autre, mais ce qu'un run en verra tient à ce qui est au plan.
    """
    _parent_a_vagues(depot, [False, False, False])
    lignes = _non_arbitres(depot.lance("queue.sh", "--non-arbitres").stdout)
    assert lignes == [["500", "3", "0", "3", "Parent de suivi"]], lignes


def test_un_seul_lot_marque_suffit_a_tenir_le_parent_pour_arbitre(depot: Depot) -> None:
    """La règle de FAIT, et ce qu'elle protège.

    Les 25 parents arbitrés à la main avant que `lot::arbitre` n'existe portent des marqueurs et
    rien d'autre. Sans cette moitié, le premier run les signalerait tous — et un signalement qui
    nomme 25 parents dont 24 vont bien n'est plus lu.
    """
    _parent_a_vagues(depot, [False, True, False])
    assert _non_arbitres(depot.lance("queue.sh", "--non-arbitres").stdout) == []


def test_le_label_arbitre_enregistre_le_verdict_tout_est_sequentiel(depot: Depot) -> None:
    """LE test du ticket : sans lui, la réponse « aucun lot n'est parallélisable » est inexprimable.

    Ce parent est arbitré et sa réponse est « rien n'est parallélisable » — exactement la forme
    qu'aucun marqueur ne peut porter, puisque le marqueur ne sait dire que l'inverse. S'il
    ressortait ici, /orchestrate reposerait la question à chaque run, pour toujours : le défaut
    symétrique de celui qu'on corrige.
    """
    lots = [(501, "Lot 1", False), (502, "Lot 2", False)]
    depot.milestone("Phase X")
    depot.ticket(500, "Parent de suivi", lots=lots, labels_sup="lot::arbitre")
    for iid, titre, _ in lots:
        depot.ticket(iid, titre, parent=500)
    depot.publie()
    assert _non_arbitres(depot.lance("queue.sh", "--non-arbitres").stdout) == []
    assert depot.lib("arbitrage", "500").stdout.split("\t")[:1] == ["arbitré"]


def test_arbitrer_un_parent_fait_taire_le_signalement(depot: Depot) -> None:
    """L'aller-retour complet, et la seule preuve que le dispositif CONVERGE.

    Un signalement qu'on ne peut pas éteindre est un signalement qu'on apprend à ignorer : ce test
    part d'un parent signalé, joue le geste que /orchestrate propose (`lib.sh arbitre`), et exige
    que le signalement disparaisse — sans qu'un seul lot ait été marqué, puisque le verdict était
    « tout est séquentiel ».
    """
    _parent_a_vagues(depot, [False, False])
    assert _non_arbitres(depot.lance("queue.sh", "--non-arbitres").stdout) != []
    r = depot.lib("arbitre", "500")
    assert r.returncode == 0, r.stderr
    assert "aucun lot parallélisable" in r.stdout, "le verbe dit ce qu'il vient d'enregistrer"
    assert _non_arbitres(depot.lance("queue.sh", "--non-arbitres").stdout) == []


def test_le_verbe_darbitrage_refuse_un_ticket_qui_nest_pas_un_parent(depot: Depot) -> None:
    """Le label dit quelque chose du DÉCOUPAGE. Sur un ticket ordinaire il deviendrait une
    décoration que la lecture prendrait pour un fait."""
    _parent_a_vagues(depot, [False, True])
    assert depot.lib("arbitre", "501").returncode == 1, "un lot n'est pas un parent"


def test_les_trois_codes_du_verbe_darbitrage(depot: Depot) -> None:
    """0 arbitré · 3 jamais · 1 pas un parent — éprouvés UN PAR UN.

    Le 3 plutôt qu'un 1 pour « jamais » suit `lots-ouverts` : c'est une RÉPONSE, pas une panne, et
    l'appelant n'a pas à trancher entre les deux sur la même valeur.
    """
    _parent_a_vagues(depot, [False, True])
    assert depot.lib("arbitrage", "500").returncode == 0
    depot.ticket(700, "Parent nu", lots=[(701, "Lot 1", False)])
    depot.ticket(701, "Lot 1", parent=700)
    depot.publie()
    assert depot.lib("arbitrage", "700").returncode == 3, "aucun marqueur, aucun label"
    assert depot.lib("arbitrage", "701").returncode == 1, "un lot n'est pas un parent"


def test_le_plan_porte_la_reserve_sans_que_ses_lignes_de_ticket_bougent(depot: Depot) -> None:
    """La réserve voyage DANS le plan, en commentaire — c'est ce qui évite à run.sh de replanifier.

    Les deux lectures du plan par run.sh écartent déjà les lignes « # » ; ce test garde qu'elles
    n'ont rien de nouveau à écarter du côté des tickets, et qu'un lecteur naïf du plan compte le
    même nombre de tickets qu'avant.
    """
    _parent_a_vagues(depot, [False, False])
    sortie = depot.lance("queue.sh").stdout
    assert "# non-arbitre\t500\t" in sortie, "la réserve est là"
    assert [ligne[1] for ligne in _lignes_du_plan(sortie)] == ["501", "502"], (
        "et elle n'ajoute aucun ticket au plan"
    )


def test_le_plan_reste_identique_a_deux_appels_avec_la_reserve(depot: Depot) -> None:
    """La règle 4 de queue.sh tient : la détection ne consulte que le backlog, jamais une horloge,
    un journal ou un jugement. Deux appels rendent le même plan, réserve comprise."""
    _parent_a_vagues(depot, [False, False])
    assert depot.lance("queue.sh").stdout == depot.lance("queue.sh").stdout


def test_le_check_nomme_les_parents_jamais_arbitres(depot: Depot) -> None:
    _parent_a_vagues(depot, [False, False])
    r = depot.lance("queue.sh", "--check")
    assert "parents jamais arbitrés" in r.stderr
    assert "#500" in r.stderr


def test_le_check_est_muet_quand_tout_est_arbitre(depot: Depot) -> None:
    """Règle de `gc --auto` : signaler l'abstention nominale apprend à ne plus lire les
    signalements. Un plan entièrement arbitré ne dit rien du tout."""
    _parent_a_vagues(depot, [False, True, True])
    r = depot.lance("queue.sh", "--check")
    assert "jamais arbitrés" not in r.stderr
    assert "non-arbitre" not in depot.lance("queue.sh").stdout


# --- Lot 2 : l'ordonnanceur (#289) ----------------------------------------------------------------


def test_deux_tickets_independants_partent_vraiment_ensemble(depot: Depot) -> None:
    """Le critère central de #289, mesuré par les sessions elles-mêmes.

    La barrière ne peut se lever que si les deux sessions sont vivantes en même temps : un run
    séquentiel s'y bloquerait jusqu'au timeout du bouchon. Le pic mesuré vaut donc 2, et un pic de 1
    voudrait dire que la concurrence n'a pas eu lieu — pas qu'elle est passée inaperçue.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "duo", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 2, "les deux sessions doivent avoir été en vol au même instant"


def test_jamais_deux_lots_du_meme_parent_hors_vague_en_vol(depot: Depot) -> None:
    """La garde de l'ordonnanceur : le plan a déclaré ces deux lots dépendants, `--concurrence 2` ne
    les rend pas indépendants pour autant.

    Le bouchon n'attend personne — il livre tout de suite —, sinon un run correct se bloquerait sur
    sa propre barrière. Ce qu'on lit est le pic de simultanéité : il doit rester à 1.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.2")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "barriere", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 1, (
        "deux vagues d'un même parent ne partent jamais ensemble, quelle que soit la concurrence"
    )
    assert [ligne[0] for ligne in _resume(depot.racine / ".maestro/orchestrate/barriere")] == \
        ["130", "131"], "et l'ordre du plan est respecté"


def test_un_creneau_libere_va_au_prochain_ELIGIBLE_et_non_au_suivant(depot: Depot) -> None:
    """Le balayage complet du plan, et non la ligne d'après.

    Le plan est : un lot (500.1), son successeur bloqué (500.2), puis un ticket isolé. Avec deux
    créneaux, le second doit aller à l'ISOLÉ — la ligne suivante, elle, est barrée. Un ordonnanceur
    qui se contenterait de « la prochaine ligne » laisserait un créneau vide tout le run.
    """
    _livrables(depot, (130, 131, 132))
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.2"),
                                 (3, 132, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "eligible", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 132))})
    assert r.returncode == 0, r.stdout + r.stderr
    vus = (depot.fixtures / "vus.txt").read_text(encoding="utf-8").split()
    assert vus[:2] == ["130", "132"] or vus[:2] == ["132", "130"], (
        f"le second créneau saute le lot barré pour prendre l'isolé — vu {vus}"
    )
    assert vus[2] == "131", "et le lot barré ne part qu'une fois son créneau libéré"


def test_le_bilan_n_a_aucune_ligne_tronquee_sous_n_verdicts(depot: Depot) -> None:
    """`resume.tsv` est écrit par le PILOTE seul — c'est ce qui règle la question par construction.

    Le test ne vérifie pas l'atomicité d'un `printf >>` (elle dépend de la plateforme, MSYS émulant
    O_APPEND) : il vérifie l'invariant qui la rend inutile — une ligne par ticket, six colonnes
    chacune, aucun iid en double, même quand quatre verdicts tombent en même temps.
    """
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "bilan", "--concurrence", "4",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, iids)})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _resume(depot.racine / ".maestro/orchestrate/bilan")
    assert [len(ligne) for ligne in lignes] == [6] * 4, f"six colonnes par ligne — {lignes}"
    assert sorted(ligne[0] for ligne in lignes) == [str(i) for i in iids]
    assert {ligne[1] for ligne in lignes} == {"OK"}


def test_max_borne_les_tickets_tentes_meme_a_plusieurs_creneaux(depot: Depot) -> None:
    """`--max` compte les tickets TENTÉS, et cela ne change pas parce qu'ils partent par deux.

    Le plafond est vérifié avant CHAQUE lancement, pas une fois par tour de boucle : sans quoi un
    run à quatre créneaux dépasserait son plafond de trois tickets.
    """
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "max-n", "--concurrence", "4",
                    "--max", "2", env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _resume(depot.racine / ".maestro/orchestrate/max-n")
    assert sorted(ligne[0] for ligne in lignes) == ["130", "131"], (
        f"deux tickets tentés, pas un de plus — {lignes}"
    )
    assert "Plafond --max 2 atteint" in r.stdout


def test_la_cascade_d_echec_saute_ce_qui_n_est_pas_parti_et_laisse_finir_ce_qui_l_est(
    depot: Depot,
) -> None:
    """La cascade se décide à la FIN d'un ticket, plus à son tour de boucle (#289).

    Deux lots de la même vague partent ensemble ; le premier échoue. Le second est déjà en vol : le
    plan l'avait déclaré indépendant, on ne le rappelle pas. Le troisième, d'une vague suivante,
    n'est pas parti : il est sauté au moment de le lancer.
    """
    _livrables(depot, (131, 132))
    depot.ticket(130, "Ticket 130")  # sans PR : la session ne clôt rien, verdict ECHEC
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.1"),
                                 (3, 132, "500", "haute", "500.2")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "cascade", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/cascade")}
    assert verdicts["130"] == "ECHEC"
    assert verdicts["131"] == "OK", "un lot déjà en vol n'est pas rappelé"
    assert verdicts["132"] == "SAUTE", "un lot pas encore parti l'est"
    assert "lot précédent de #500 a échoué" in r.stdout


def test_avec_concurrence_1_le_run_reste_sequentiel_au_bit_pres(depot: Depot) -> None:
    """⚠ Ce test gardait « SANS l'option, le run reste séquentiel » — l'invariant que #455 a
    délibérément renversé, et qu'il ne faut pas restaurer ici.

    Le défaut à 1 rendait le chantier #288-#292 mergeable seul, puis l'a éteint pour de bon : le
    mécanisme n'a jamais tourné jusqu'à ce que quelqu'un pense à passer l'option. Ce qui reste vrai,
    et qui est le deuxième critère d'acceptation de #455, est que la consigne explicite rend le run
    d'hier au bit près — pic de 1, et aucun régime particulier à annoncer.

    Le pendant, « sans l'option la dérivation fait partir deux tickets ensemble », est plus bas.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "seq", "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 1
    assert "tickets en vol" not in r.stdout, "aucun régime particulier à annoncer"


def test_une_concurrence_illisible_est_refusee_avant_le_premier_ticket(depot: Depot) -> None:
    """Même raison que l'effort et le budget : un réglage qu'on ne comprend pas ne doit pas se
    découvrir au premier ticket. Et `0` n'y vaut pas « pas de limite » — ce serait zéro créneau."""
    _livrables(depot, (130,))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-")])
    for valeur in ("0", "deux", "-1"):
        r = depot.lance("run.sh", "--plan", plan, "--run-id", "refus", "--concurrence", valeur,
                        env={"MAESTRO_CLAUDE_BIN": _claude_stub(depot, "exit 1\n")})
        assert r.returncode == 2, f"« {valeur} » aurait dû être refusé — {r.stdout}{r.stderr}"
        assert "concurrence invalide" in r.stderr
    assert not (depot.racine / ".maestro/orchestrate/refus").exists(), "rien n'a été entamé"


def test_un_plan_d_avant_la_colonne_groupe_retombe_en_sequentiel_en_le_disant(
    depot: Depot,
) -> None:
    """Un plan à cinq colonnes, rejoué par `--resume` : rien n'y dit ce qui est indépendant.

    Deviner serait pire que se taire — le run retombe à un créneau et l'annonce, plutôt que de
    paralléliser sur une colonne qu'il aurait lue de travers.
    """
    _livrables(depot, (130, 131))
    chemin = depot.racine / "plan-ancien.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\ttitre\n1\t130\t-\thaute\tTicket 130\n"
        "2\t131\t-\thaute\tTicket 131\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("run.sh", "--plan", str(chemin), "--run-id", "ancien", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "antérieur à la colonne « groupe »" in r.stdout
    assert _pic(depot) == 1


# --- La concurrence se dérive du plan au lieu de valoir 1 (#455) ----------------------------------
#
# Ce que ces tests gardent n'est PAS « le parallélisme marche » — #289 s'en charge depuis des mois,
# et c'est bien le problème : il marchait sans jamais tourner, parce que son plafond valait 1. Ce
# qui se garde ici est le DÉFAUT, et la chaîne de précédence autour de lui : une consigne (option,
# variable) et un run repris l'emportent tous trois sur la dérivation, jamais l'inverse.
#
# Le premier test est le seul qui mesure ; les autres lisent l'annonce. La distinction compte —
# un affichage juste sur un ordonnanceur mort passerait tous les seconds.


def test_sans_option_deux_tickets_independants_partent_vraiment_ensemble(depot: Depot) -> None:
    """Le critère central : la dérivation produit du parallélisme RÉEL, pas une ligne d'annonce.

    Même barrière qu'à #289 (`test_deux_tickets_independants_partent_vraiment_ensemble`), à ceci
    près qu'aucun `--concurrence` n'est passé : le pic vaut 2 parce que le plan le disait, et lui
    seul. Un pic de 1 signifierait que le défaut est resté à 1 — exactement la panne que ce ticket
    corrige.
    """
    _livrables(depot, (130, 131))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "derive-duo",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131))})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 2, "le plan les disait indépendants, rien n'a été imposé"


def test_la_derivation_annonce_sa_valeur_et_son_origine(depot: Depot) -> None:
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "derive-dit")
    assert "2 en vol (dérivé du plan)" in r.stdout


def test_un_plan_sans_ticket_simultanable_reste_sequentiel_et_le_dit(depot: Depot) -> None:
    """« séquentiel » doit être un VERDICT sur le plan, jamais un silence.

    Sans cette phrase, un run dérivé à 1 est indiscernable du défaut d'avant ce ticket : personne
    ne peut distinguer « ce plan ne s'y prête pas » de « la dérivation ne marche plus ».
    """
    plan = _plan_groupes(depot, [(1, 130, "500", "haute", "500.1"),
                                 (2, 131, "500", "haute", "500.2")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "derive-seq")
    assert "séquentiel — aucun ticket simultanable dans ce plan" in r.stdout


def test_un_plan_muet_sur_l_independance_ne_se_confond_pas_avec_un_plan_sans_independance(
    depot: Depot,
) -> None:
    """Deux causes, un même 1, deux phrases : « on ne peut pas savoir » ≠ « il n'y en a pas ».

    Les confondre enverrait chercher un défaut de dérivation là où il n'y a qu'un plan d'avant #288,
    et tairait qu'un run repris tourne en aveugle sur l'indépendance.
    """
    chemin = depot.racine / "plan-muet.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\ttitre\n1\t130\t-\thaute\tTicket 130\n"
        "2\t131\t-\thaute\tTicket 131\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("run.sh", "--dry-run", "--plan", str(chemin), "--run-id", "derive-muet")
    assert "antérieur à la colonne « groupe »" in r.stdout
    assert "aucun ticket simultanable" not in r.stdout


def test_la_derivation_est_bornee_et_l_annonce_dit_qu_elle_l_a_ete(depot: Depot) -> None:
    """Le plan dit ce qui est simultanable ; il ne dit rien de ce que la machine tient.

    Et l'écrêtage se DIT : sans ce chiffre, une borne basse se lirait comme un plan pauvre, et
    personne ne saurait qu'il y a du parallélisme laissé sur la table. C'est CE fait que le test
    garde — pas la valeur de la borne, qui est une décision et a bougé (2 → 3, #626).
    """
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-"),
                                 (3, 132, "-", "haute", "-"), (4, 133, "-", "haute", "-"),
                                 (5, 134, "-", "haute", "-")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", "derive-borne")
    assert "3 en vol (dérivé du plan : 5 simultanables, borné à 3)" in r.stdout


@pytest.mark.parametrize(
    ("borne", "attendu"),
    [
        ("2", "2 en vol (dérivé du plan : 4 simultanables, borné à 2)"),
        ("4", "4 en vol (dérivé du plan)"),
    ],
    ids=["sous-le-defaut", "au-dessus-du-defaut"],
)
def test_la_borne_se_deplace_par_l_environnement(depot: Depot, borne: str, attendu: str) -> None:
    """La variable doit déplacer la borne DANS LES DEUX SENS, et viser une valeur ≠ du défaut.

    Le piège que ce test a failli devenir (#626) : il posait
    `MAESTRO_ORCHESTRATE_CONCURRENCE_MAX=3` sur un plan qui offrait 3, ce qui prouvait la
    variable tant que le défaut valait 2 — et plus rien le jour où le défaut est passé à 3, le
    même verdict tombant sans elle. Un test qui ne peut plus échouer n'est pas un test qui passe,
    c'est un ✓ sur une question qui n'est plus posée. D'où un plan qui offre 4 et deux bornes
    encadrant le défaut : chaque cas rend une valeur INATTEIGNABLE sans la variable.
    """
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-"),
                                 (3, 132, "-", "haute", "-"), (4, 133, "-", "haute", "-")])
    r = depot.lance("run.sh", "--dry-run", "--plan", plan, "--run-id", f"derive-max-{borne}",
                    env={"MAESTRO_ORCHESTRATE_CONCURRENCE_MAX": borne})
    assert attendu in r.stdout


@pytest.mark.parametrize("consigne", ["option", "variable"])
def test_une_consigne_l_emporte_sur_la_derivation(depot: Depot, consigne: str) -> None:
    """`--concurrence 1` sur un plan qui offrait 4 : dériver PAR-DESSUS une consigne serait le
    défaut symétrique de celui qu'on corrige."""
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-"),
                                 (3, 132, "-", "haute", "-"), (4, 133, "-", "haute", "-")])
    args = ["run.sh", "--dry-run", "--plan", plan, "--run-id", f"impose-{consigne}"]
    env = {}
    if consigne == "option":
        args += ["--concurrence", "1"]
    else:
        env["MAESTRO_ORCHESTRATE_CONCURRENCE"] = "1"
    r = depot.lance(*args, env=env)
    assert "séquentiel (imposé)" in r.stdout
    assert "dérivé" not in r.stdout


def test_une_reprise_l_emporte_sur_la_derivation(depot: Depot) -> None:
    """La concurrence est un trait DU RUN (#291), et la dérivation ne doit pas le défaire.

    Le cas n'est pas théorique : la borne peut avoir changé entre les deux runs (autre poste,
    variable posée depuis), et le run repris rejouerait alors un régime que personne n'a choisi
    pour lui.
    """
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-"), (2, 131, "-", "haute", "-"),
                                 (3, 132, "-", "haute", "-"), (4, 133, "-", "haute", "-")])
    dossier = depot.racine / ".maestro" / "orchestrate" / "coupe-455"
    dossier.mkdir(parents=True, exist_ok=True)
    (dossier / "plan.tsv").write_text(Path(plan).read_text(encoding="utf-8"),
                                      encoding="utf-8", newline="\n")
    (dossier / "concurrence").write_text("1\n", encoding="utf-8", newline="\n")
    os.utime(dossier / "concurrence", (time.time() - 7200,) * 2)
    r = depot.lance("run.sh", "--resume", "coupe-455", "--dry-run", "--run-id", "reprise-455")
    assert "séquentiel (du run repris)" in r.stdout
    assert "dérivé" not in r.stdout


# --- Lot 3 : la vue rend N tickets en vol, et c'est le pilote qui dessine (#290) ------------------

REPOSITIONNEMENT = re.compile(r"\x1b\[(\d+)F")


def _frames(vue: str) -> list[str]:
    """Découpe la console aux repositionnements et rend les morceaux écrits entre eux.

    Un « ESC[<n>F » annonce de combien de rangées on remonte pour redessiner — donc la hauteur de la
    frame PRÉCÉDENTE, moins un (le curseur reste sur sa dernière ligne, #284). C'est cette relation
    entre l'annonce et ce qui a réellement été écrit que les tests d'ici vérifient : fausse, le bloc
    se dédouble ou se mange.
    """
    return REPOSITIONNEMENT.split(vue)[::2]


def _hauteur_de_frame(morceau: str) -> int:
    """Le nombre de rangées qu'une frame a écrites, compté sur « ESC[K ».

    Chaque ligne du bloc se termine par « efface jusqu'au bout » — et rien d'autre n'en porte : ni
    l'en-tête d'un ticket, ni un verdict, ni les lignes permanentes qu'un effacement a laissé passer
    dans le même morceau. Compter les sauts de ligne les compterait avec la frame, et le morceau qui
    ouvre la console (bannière du run comprise) paraîtrait trois rangées trop haut.
    """
    return morceau.count("\x1b[K")


def _hauteurs_annoncees_et_reelles(vue: str) -> list[tuple[int, int]]:
    """(hauteur annoncée par un repositionnement, rangées écrites par la frame d'avant)."""
    morceaux = REPOSITIONNEMENT.split(vue)
    return [(int(morceaux[i]), _hauteur_de_frame(morceaux[i - 1]))
            for i in range(1, len(morceaux) - 1, 2)]


def _ecritures_hors_bloc(vue: str) -> list[str]:
    """Ce qui a été écrit sur l'écran APRÈS un bloc sans l'avoir retiré — la faute qui dédouble.

    Tout ce que la vue écrit se termine par « ESC[J » : le pied d'une frame comme un effacement. Un
    morceau qui précède un repositionnement et finit autrement porte donc du texte tombé sous un
    bloc resté affiché — et le repositionnement qui suit, compté sur la hauteur du bloc seul,
    remontera trop peu. La hauteur annoncée, elle, reste juste : c'est pourquoi elle ne suffit pas à
    voir ce défaut-là.
    """
    morceaux = REPOSITIONNEMENT.split(vue)
    return [morceaux[i - 1] for i in range(1, len(morceaux) - 1, 2)
            if not morceaux[i - 1].endswith("\x1b[J")]


def _run_a_trois_en_vol(depot: Depot, run_id: str) -> tuple[subprocess.CompletedProcess, Path]:
    """Un run de quatre tickets dont trois sont en vol au même instant, console dans un fichier."""
    iids = (130, 131, 132, 133)
    _livrables(depot, iids)
    console = _console(depot)
    plan = _plan_groupes(depot, [(r, i, "-", "haute", "-") for r, i in enumerate(iids, 1)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", run_id, "--concurrence", "3",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, (130, 131, 132)),
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    return r, console


def test_le_bloc_ne_se_dedouble_pas_quand_sa_hauteur_varie(depot: Depot) -> None:
    """L'invariant qui tient tout le reste : ce qu'une frame annonce remonter est ce que la
    précédente a écrit.

    À un ticket la hauteur était constante et l'erreur impossible. À N elle VARIE d'une frame à
    l'autre — un ticket qui se solde rend sa ligne d'action —, et une annonce fausse d'une seule
    rangée laisse une copie du bloc dans l'historique à chaque redessin, cinq fois par seconde.
    """
    r, console = _run_a_trois_en_vol(depot, "hauteur")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    paires = _hauteurs_annoncees_et_reelles(vue)
    assert paires, "aucune frame n'a été redessinée — le test ne prouve rien"
    assert all(annonce == reel - 1 for annonce, reel in paires), (
        f"une frame remonte d'autant de rangées qu'elle en a écrit, moins une — {paires}"
    )
    assert len({reel for _, reel in paires}) > 1, (
        f"la hauteur doit VARIER pendant ce run, sinon l'invariant n'est pas mis à l'épreuve — "
        f"{paires}"
    )
    # L'autre moitié, que la hauteur seule ne voit pas : rien ne doit s'écrire SOUS un bloc resté
    # affiché — les verdicts des N sessions passent par la file du pilote, qui retire le bloc
    # d'abord.
    assert not _ecritures_hors_bloc(vue), "une ligne a été écrite par-dessus le bloc"


def test_une_frame_donne_une_ligne_d_action_a_chaque_ticket_en_vol(depot: Depot) -> None:
    """Le bloc à N : une ligne par entrée du plan, une de plus par ticket en vol, une pour le pied.

    N'en montrer qu'un serait pire que rien — les autres tiennent un worktree et une session sans
    que rien ne le dise.
    """
    r, console = _run_a_trois_en_vol(depot, "trois-lignes")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    corps = [m for m in _frames(vue) if "reste " in m]
    # 4 lignes de plan + 3 actions + 1 pied : la seule hauteur possible quand les trois sont en vol.
    assert any(_hauteur_de_frame(m) == 8 for m in corps), (
        "aucune frame ne rend les trois tickets en vol avec leur ligne d'action"
    )
    pleine = next(m for m in corps if _hauteur_de_frame(m) == 8)
    for iid in (130, 131, 132):
        assert f"#{iid}" in pleine, f"#{iid} manque au bloc alors qu'il est en vol"


def test_le_pied_compte_ce_qui_n_est_ni_solde_ni_en_vol(depot: Depot) -> None:
    """« reste » se compte sur le plan moins les soldés moins les en-vol.

    `nb_plan - POSITION` désignait la position du DERNIER ticket lancé : juste tant que les tickets
    partaient dans l'ordre, faux dès qu'ils ne se prennent plus un par un.
    """
    r, console = _run_a_trois_en_vol(depot, "pied")
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "3 en vol" in vue, "le pied dit combien de tickets sont en vol dès que N > 1"
    # Quatre au plan, trois en vol, aucun soldé : il en reste exactement un à venir.
    assert re.search(r"3 en vol.*reste 1", vue), (
        "le pied doit annoncer « reste 1 » quand trois des quatre sont en vol"
    )
    assert "reste 0" in vue, "et retomber à zéro quand tout est soldé"


def test_une_session_publie_son_etat_et_ne_dessine_jamais(depot: Depot) -> None:
    """Le choix de #290 : retirer l'écran à tous sauf un, plutôt que le partager entre N écrivains.

    Le contrat tient dans un fichier PAR TICKET (`<iid>.vue` — marqueur puis action) que le pilote
    relit à chaque frame. C'est ce qui permet à la hauteur du bloc de redevenir une simple
    variable : un seul processus la lit et l'écrit.
    """
    r, console = _run_a_trois_en_vol(depot, "publication")
    assert r.returncode == 0, r.stdout + r.stderr
    run_dir = depot.racine / ".maestro/orchestrate/publication"
    for iid in (130, 131, 132):
        publie = (run_dir / f"{iid}.vue").read_text(encoding="utf-8")
        assert publie.count("\t") == 1 and publie.startswith((".", "=")), (
            f"« <marqueur><TAB><action> », et un marqueur JAMAIS vide — obtenu {publie!r}"
        )
    assert "\x1b[" not in r.stdout, "aucune frame ne doit fuir dans run.log, même à N sessions"


def test_une_ligne_permanente_de_session_passe_par_la_file_et_ne_casse_pas_le_bloc(
    depot: Depot,
) -> None:
    """Le défaut que la réunion de #290 et #291 avait fabriqué, et que ce lot corrige.

    #291 annonçait l'attente d'une limite d'usage par `trace` — écrire sur l'écran — pour une raison
    juste à sa date : la frame suit immédiatement, et une ligne passée par `tee` pouvait arriver
    après elle. Depuis #290 c'est le contraire qu'il faut : la session n'écrit plus à l'écran, elle
    met en file. `trace` s'appuyait sur `VUE_HAUT` pour retirer le bloc d'abord, or c'est désormais
    une variable du PILOTE dont la session n'a qu'une copie figée au fork — rien n'était donc
    retiré, la ligne s'écrivait sous un bloc toujours affiché, et la frame suivante remontait d'une
    hauteur qui ne correspondait plus à rien. Ce que la hauteur annoncée, elle, ne montre pas :
    elle reste juste — c'est ce qui s'est glissé entre deux frames qui ne l'est pas.
    """
    depot.ticket(130, "Ticket interrompu")
    depot.mr("feat/130-ticket-interrompu", "opened")
    gabarit = _statut_json("%s", "En revue")
    claude = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q -- '--resume'; then
          printf '{gabarit}' 130 > "$MAESTRO_FIXTURES/owner-130.json"
          printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":6}}\\n'
          exit 0
        fi
        printf '{{"type":"result","is_error":true,"total_cost_usd":1,'
        printf '"result":"Claude AI usage limit reached"}}\\n'
        exit 1
    """)
    console = _console(depot)
    plan = _plan_groupes(depot, [(1, 130, "-", "moyenne", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "file-attente",
                    env={"MAESTRO_CLAUDE_BIN": claude, "MAESTRO_ORCHESTRATE_PALIER": "3",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "limite d'usage atteinte" in vue, "l'annonce arrive bien à l'écran, par la file"
    assert _hauteurs_annoncees_et_reelles(vue), "aucune frame redessinée — le test ne prouve rien"
    egarees = _ecritures_hors_bloc(vue)
    assert not egarees, (
        f"une ligne a été écrite sous un bloc resté affiché — {[m[-90:] for m in egarees]}"
    )


def test_chaque_ligne_permanente_porte_le_numero_de_son_ticket(depot: Depot) -> None:
    """Dans une trace entrelacée, rien d'autre ne dit à qui appartient un « ✓ PR #99 ouverte »."""
    r, _ = _run_a_trois_en_vol(depot, "prefixes")
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = [ligne for ligne in r.stdout.splitlines() if "PR #99 ouverte" in ligne]
    assert len(verdicts) == 4, f"un verdict par ticket — {verdicts}"
    assert all(re.search(r"#\d+", ligne) for ligne in verdicts), (
        f"chaque verdict doit nommer son ticket — {verdicts}"
    )


# --- Lot 4 : la reprise d'un run qui avait N tickets en main (#291) -------------------------------


def test_une_reprise_rejoue_tous_les_tickets_que_le_run_avait_en_vol(depot: Depot) -> None:
    """La question est posée PAR TICKET, jamais une fois pour le run.

    Un run concurrent coupé en avait N en main, chacun avec son témoin de session : les laisser
    derrière soi, c'est abandonner N worktrees porteurs de travail non commité. Leur cycle de vie
    est « En cours » — posé par leur propre `/ticket-start` —, donc c'est bien le filtre ordinaire
    qui les écarterait sans cette exception.
    """
    # #130 est allé au bout avant la coupure : son cycle de vie a suivi, et c'est par là qu'il se
    # saute tout seul. #131 et #132 étaient EN VOL — « En cours » posé par leur propre
    # `/ticket-start`, donc écartés par le filtre ordinaire sans l'exception de #204/#291.
    depot.ticket(130, "Ticket 130", statut="Terminé")
    for iid in (131, 132):
        depot.ticket(iid, f"Ticket {iid}", statut="En cours")
        depot.mr(f"feat/{iid}-ticket-{iid}", "opened")
    _run_dir(depot, "20260803-100000",
             [(1, 130, "-", "haute"), (2, 131, "-", "haute"), (3, 132, "-", "haute")],
             resume=[(130, "OK", 99, 600, "3.50", "-")], sessions=(131, 132), age=7200)
    r = depot.lance("run.sh", "--resume", "20260803-100000", "--run-id", "suite",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/suite")}
    assert verdicts == {"130": "SAUTE", "131": "OK", "132": "OK"}, (
        f"les deux tickets en vol à la coupure sont repris, le livré est sauté — {verdicts}"
    )
    assert r.stdout.count("repris en vol") == 2, (
        "la question est posée par ticket : deux tickets en main, deux reprises"
    )


def test_un_en_cours_que_le_run_repris_n_avait_pas_en_main_reste_saute(depot: Depot) -> None:
    """L'exception est étroite à dessein : sans témoin de session dans le journal repris, ce « En
    cours » est le ticket de quelqu'un d'autre — le reprendre lui retirerait son travail."""
    depot.ticket(130, "Ticket 130", statut="En cours")
    depot.ticket(131, "Ticket 131", statut="En cours")
    depot.mr("feat/130-ticket-130", "opened")
    _run_dir(depot, "20260803-110000", [(1, 130, "-", "haute"), (2, 131, "-", "haute")],
             resume=[], sessions=(130,), age=7200)
    r = depot.lance("run.sh", "--resume", "20260803-110000", "--run-id", "etroit",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    verdicts = {ligne[0]: ligne[1]
                for ligne in _resume(depot.racine / ".maestro/orchestrate/etroit")}
    assert verdicts["130"] == "OK", "celui dont le run repris avait le témoin est repris"
    assert verdicts["131"] == "SAUTE", "l'autre appartient à une session voisine"


def test_une_reprise_rejoue_la_concurrence_du_run_coupe(depot: Depot) -> None:
    """La concurrence est un trait DU RUN, pas de la ligne de commande qui le rejoue.

    `/orchestrate --resume` ne passe aucune option : sans le fichier `concurrence`, un run qui
    tournait à trois se reprendrait en séquentiel, et le gain en temps de mur disparaîtrait
    exactement au moment où on en a le plus besoin.
    """
    iids = (130, 131, 132)
    _livrables(depot, iids)
    dossier = _run_dir(depot, "20260803-120000",
                       [(r, i, "-", "haute") for r, i in enumerate(iids, 1)],
                       resume=[], age=7200)
    (dossier / "concurrence").write_text("3\n", encoding="utf-8", newline="\n")
    os.utime(dossier / "concurrence", (time.time() - 7200,) * 2)
    r = depot.lance("run.sh", "--resume", "20260803-120000", "--run-id", "meme-regime",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, iids)})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 3, (
        "la reprise doit repartir au régime du run coupé, pas au défaut de la ligne de commande"
    )


def test_une_concurrence_explicite_l_emporte_sur_celle_du_run_repris(depot: Depot) -> None:
    """`--resume --concurrence 1` reste la façon de dérouler en séquentiel un run qu'on veut suivre
    de près : ce qui est relu est un DÉFAUT, jamais un verrou."""
    iids = (130, 131)
    _livrables(depot, iids)
    dossier = _run_dir(depot, "20260803-130000",
                       [(r, i, "-", "haute") for r, i in enumerate(iids, 1)],
                       resume=[], age=7200)
    (dossier / "concurrence").write_text("2\n", encoding="utf-8", newline="\n")
    os.utime(dossier / "concurrence", (time.time() - 7200,) * 2)
    r = depot.lance("run.sh", "--resume", "20260803-130000", "--run-id", "explicite",
                    "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _pic(depot) == 1


def test_un_run_ecrit_sa_concurrence_pour_celui_qui_le_reprendra(depot: Depot) -> None:
    """Le fichier est posé au démarrage, avant le premier ticket : un run coupé à sa première
    minute doit être reprenable au même régime que celui qui serait allé au bout."""
    _livrables(depot, (130,))
    plan = _plan_groupes(depot, [(1, 130, "-", "haute", "-")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "trace-regime", "--concurrence", "2",
                    env={"MAESTRO_CLAUDE_BIN": _stub_barriere(depot, ())})
    assert r.returncode == 0, r.stdout + r.stderr
    trace = depot.racine / ".maestro/orchestrate/trace-regime/concurrence"
    assert trace.read_text(encoding="utf-8").strip() == "2"


# =====================================================================================
# Le bloc se ré-ancre au lieu de se recopier (#325)
# =====================================================================================
#
# Les tests de #290 mesurent le FLUX : « ce qu'une frame annonce remonter est ce que la précédente a
# écrit », en comptant les « ESC[K », c'est-à-dire des lignes LOGIQUES. C'est exactement ce que le
# défaut de #325 traverse sans les faire rougir : une ligne plus large que la console occupe DEUX
# rangées, le flux reste cohérent avec lui-même, et l'écran dérive quand même — d'une copie de la
# première ligne du bloc par redessin. On rejoue donc les frames dans un terminal simulé, où c'est
# la RANGÉE qui est l'unité, et on regarde l'écran qui en sort.

SEQUENCE = re.compile(r"\x1b\[([0-9;?]*)([A-Za-z])")


def _ecrans(flux: str, largeur: int, hauteur: int) -> list[list[str]]:
    """Rejoue un flux de frames dans un terminal de <largeur>×<hauteur>, écran par écran.

    Un instantané est pris à chaque « ESC[J » — la séquence qui ferme une frame comme un
    effacement, donc à chaque fois que l'écran est dans un état que quelqu'un aurait pu voir. C'est
    ce qui permet de mesurer le PIC de copies : le dernier écran d'un run ne montre rien, la boucle
    retirant son bloc avant d'imprimer le résumé.

    Le sous-ensemble suffit à ce que la vue émet : texte (avec repli en fin de rangée), « \\n »
    (traité en CR+LF, comme une console Windows), « \\r », « ESC[K », « ESC[J », « ESC[<n>F »,
    « ESC[<n>B ». Le repli est modélisé au plus TÔT — la rangée est consommée dès le caractère qui
    remplit la dernière colonne : c'est l'hypothèse pessimiste, celle de conhost, et un correctif
    qui tient sous elle tient aussi sous le repli différé des terminaux Unix.
    """
    ecran = [""] * hauteur
    vus: list[list[str]] = []
    ligne = col = 0

    def defile() -> None:
        nonlocal ligne
        if ligne + 1 < hauteur:
            ligne += 1
        else:
            ecran.pop(0)
            ecran.append("")

    def pose(c: str) -> None:
        nonlocal col
        if col >= largeur:
            defile()
            col = 0
        rang = ecran[ligne].ljust(col + 1)
        ecran[ligne] = rang[:col] + c + rang[col + 1:]
        col += 1

    i = 0
    while i < len(flux):
        m = SEQUENCE.match(flux, i)
        if m:
            arg, verbe = m.group(1), m.group(2)
            n = int(arg) if arg.isdigit() else (0 if verbe in "JK" else 1)
            if verbe == "F":
                ligne, col = max(ligne - n, 0), 0
            elif verbe == "A":
                ligne = max(ligne - n, 0)
            elif verbe == "B":
                # Le déplacement vers le bas est BORNÉ par la fenêtre et ne fait jamais défiler :
                # c'est cette propriété du terminal que `vue_ancre` emprunte comme repère.
                ligne = min(ligne + n, hauteur - 1)
            elif verbe == "K":
                ecran[ligne] = ecran[ligne][:col]
            elif verbe == "J":
                ecran[ligne] = ecran[ligne][:col]
                for r in range(ligne + 1, hauteur):
                    ecran[r] = ""
                vus.append(list(ecran))
            i = m.end()
            continue
        c = flux[i]
        if c == "\n":
            defile()
            col = 0
        elif c == "\r":
            col = 0
        elif c != "\x1b":
            pose(c)
        i += 1
    vus.append(list(ecran))
    return vus


def _plan_titres(depot: Depot, lignes: list[tuple[int, int, str]]) -> str:
    """Un plan figé dont on choisit le TITRE de chaque ligne — c'est lui qui fait la largeur."""
    chemin = depot.racine / "plan-titres.tsv"
    chemin.write_text(
        "# rang\tiid\tparent\tprio\tgroupe\ttitre\n"
        + "".join(f"{rang}\t{iid}\t-\thaute\t-\t{titre}\n" for rang, iid, titre in lignes),
        encoding="utf-8",
        newline="\n",
    )
    return str(chemin)


def _tput_stub(depot: Depot, cols: tuple[int, ...], lines: int) -> Path:
    """Un `tput` qui rend une largeur DIFFÉRENTE d'un appel à l'autre — une fenêtre redimensionnée.

    Le compteur vit dans un fichier : `tput` est un processus par appel, il ne peut pas se souvenir
    autrement. La dernière valeur de `cols` est celle qui reste une fois la liste épuisée.
    """
    compteur = depot.racine.parent / "tput-appels.txt"
    valeurs = " ".join(str(c) for c in cols)
    chemin = depot.racine.parent / "bin" / "tput"
    chemin.write_text(
        "#!/usr/bin/env bash\n"
        f'compteur="{compteur.as_posix()}"\n'
        f'[ "$1" = lines ] && {{ printf "{lines}\\n"; exit 0; }}\n'
        '[ "$1" = cols ] || exit 1\n'
        'n=$(cat "$compteur" 2>/dev/null || printf 0); n=$((n + 1))\n'
        'printf "%s\\n" "$n" > "$compteur"\n'
        f'set -- {valeurs}\n'
        '[ "$n" -gt "$#" ] && n=$#\n'
        'eval "printf \'%s\\n\' \\"\\${$n}\\""\n',
        encoding="utf-8",
        newline="\n",
    )
    chemin.chmod(0o755)
    return compteur


def _lignes_de_frame(vue: str) -> list[str]:
    """Toutes les lignes écrites par des frames, couleurs retirées — une par « ESC[K »."""
    COULEURS = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
    lignes = []
    for morceau in vue.split("\x1b[K"):
        # Ce qui précède un « ESC[K » est la ligne, moins ce que le repositionnement a laissé
        # devant elle.
        lignes.append(COULEURS.sub("", morceau.rsplit("\n", 1)[-1]))
    return lignes[:-1]


def _pic_de_copies(ecrans: list[list[str]], marque: str) -> tuple[int, list[str]]:
    """Le plus grand nombre de rangées portant <marque> vu à un même instant, et cet écran-là."""
    pire = max(ecrans, key=lambda e: sum(1 for rang in e if marque in rang))
    return sum(1 for rang in pire if marque in rang), pire


def _dessine(ecran: list[str]) -> str:
    return "\n".join(f"|{rang}" for rang in ecran)


TITRE_LONG = ("Extraction des sources : tout ramené au Markdown avec son rapport de lecture "
              "et la trace de ce qui a été écarté")


def test_une_largeur_perimee_ne_recopie_plus_le_bloc(depot: Depot) -> None:
    """Le défaut observé, de bout en bout : la console a rétréci, le bloc ne doit pas se recopier.

    `tput` annonce 200 colonnes au démarrage puis 100 — une fenêtre redimensionnée pendant un run
    qui dure des heures. Avant #325 la largeur était lue UNE FOIS : les lignes du bloc continuaient
    d'être calculées pour 200 colonnes, se repliaient sur 100, et chaque redessin abandonnait une
    copie de sa première ligne. Le run du 2026-08-10 en affichait trois.
    """
    _livrables(depot, (130, 131))
    appels = _tput_stub(depot, (200, 100), 20)
    console = _console(depot)
    plan = _plan_titres(depot, [(1, 130, TITRE_LONG), (2, 131, TITRE_LONG)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "perimee",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MESURE": "0",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr

    assert int(appels.read_text(encoding="utf-8")) > 1, (
        "la largeur doit être RELUE en cours de run, pas figée à l'ouverture"
    )
    ecrans = _ecrans(console.read_text(encoding="utf-8", errors="replace"), 100, 20)
    for marque in ("1. #130", "2. #131"):
        copies, pire = _pic_de_copies(ecrans, marque)
        assert copies == 1, (
            f"« {marque} » apparaît {copies} fois à l'écran, une seule est attendue —\n"
            + _dessine(pire)
        )


def test_le_bloc_se_repositionne_depuis_le_bas_des_qu_il_y_touche(depot: Depot) -> None:
    """Le repère de #325 : « ESC[999B » descend jusqu'à la dernière rangée, et le terminal borne le
    déplacement — la position se recalcule donc à neuf au lieu de se cumuler depuis le curseur.

    La fenêtre est basse EN RANGÉES à dessein : le régime ancré ne commence que lorsque le bloc
    touche vraiment le bas, et `VUE_ROW` est une borne inférieure qui n'y arrive qu'après quelques
    lignes permanentes. Deux tickets et dix rangées suffisent à l'atteindre ; à douze, le run se
    terminait avant, et le test ne prouvait rien de plus que le régime relatif. Dix est aussi le
    PLANCHER de `vue_mesure` — en dessous, une hauteur est tenue pour aberrante et remplacée par 40,
    ce qui rendait le test muet sans le faire échouer autrement que sur cette assertion-ci.
    """
    _livrables(depot, (130, 131))
    console = _console(depot)
    plan = _plan_titres(depot, [(1, 130, "Ticket 130"), (2, 131, "Ticket 131")])
    # `--concurrence 1` explicite depuis #455 : le régime ancré ne s'atteint qu'après quelques
    # lignes permanentes, et deux tickets menés ENSEMBLE en produisent moitié moins — le bloc ne
    # touche jamais le bas des dix rangées, et l'assertion échoue sur un run par ailleurs correct.
    # Ce test mesure le repère de la vue, pas l'ordonnanceur.
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "ancre", "--concurrence", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_LARGEUR": "100",
                         "MAESTRO_ORCHESTRATE_HAUTEUR": "10",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert "\x1b[999B" in vue, "le bloc doit s'ancrer sur le bas dès qu'il y touche"
    # Et le bloc reste bien collé au bas : son pied est sur la dernière rangée de l'écran.
    ecrans = _ecrans(vue, 100, 10)
    pieds = [e for e in ecrans if e[-1].startswith("  run ")]
    assert pieds, "le pied du bloc doit fermer l'écran —\n" + _dessine(ecrans[-1])


def test_aucune_ligne_du_bloc_n_atteint_la_largeur_de_la_console(depot: Depot) -> None:
    """L'invariant qui rend le compte de rangées juste : une ligne qui atteint la largeur se replie,
    et une rangée de plus est une rangée que le repositionnement ignore."""
    _livrables(depot, (130,))
    console = _console(depot)
    plan = _plan_titres(depot, [(1, 130, TITRE_LONG), (2, 131, TITRE_LONG)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "largeur", "--max", "1",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_LARGEUR": "80",
                         "MAESTRO_ORCHESTRATE_HAUTEUR": "20",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _lignes_de_frame(console.read_text(encoding="utf-8", errors="replace"))
    assert lignes, "aucune frame n'a été écrite — le test ne prouve rien"
    trop = [ligne for ligne in lignes if len(ligne) >= 80]
    assert not trop, f"une ligne du bloc atteint la largeur de la console : {trop}"


def test_la_largeur_du_bloc_ne_depend_pas_de_la_locale(depot: Depot) -> None:
    """« modèle » pèse 6 caractères et 7 octets : mesurer en `${#s}` donnait au bloc une largeur
    différente d'un poste à l'autre, et la machine qui comptait des octets repliait ses lignes.

    La console est dimensionnée pour que le gabarit autorise EXACTEMENT la largeur du titre : compté
    en octets il serait tronqué, compté en colonnes il passe entier. La largeur se DÉDUIT du titre
    (et non l'inverse) — un compte à la main s'était déjà décalé d'une colonne, et un titre d'un
    caractère de trop transforme l'invariant en tautologie sans que rien ne le dise.
    """
    titre = "Extraction des sources : tout ramené au Markdown, résumé, référencé et daté"
    assert len(titre.encode("utf-8")) > len(titre), "il faut des accents pour que le test prouve"
    _livrables(depot, (130,))
    console = _console(depot)
    plan = _plan_titres(depot, [(1, 130, titre)])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "locale",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "LC_ALL": "C", "LANG": "C",
                         # 46 = VUE_GABARIT (43) + les 3 colonnes que `vue_ligne` réserve au titre.
                         "MAESTRO_ORCHESTRATE_LARGEUR": str(len(titre) + 46),
                         "MAESTRO_ORCHESTRATE_HAUTEUR": "20",
                         "MAESTRO_ORCHESTRATE_CONSOLE": str(console)})
    assert r.returncode == 0, r.stdout + r.stderr
    vue = console.read_text(encoding="utf-8", errors="replace")
    assert titre in vue, "un titre qui tient en colonnes ne doit pas être tronqué"
    assert "daté…" not in vue and "dat…" not in vue


# =====================================================================================
# La file de merge du pilote (#419, parent #413)
# =====================================================================================
# Ces tests-ci RALLUMENT `MAESTRO_ORCHESTRATE_MERGE`, éteint dans le harnais (voir le bloc `env` du
# dépôt jetable). Ce qu'ils mesurent est ce que le PILOTE fait de son côté — l'entrée en file,
# l'écriture de `merge.tsv`, la conduite tirée du code de `merge-mr`, le bilan, la reprise — et
# jamais le merge lui-même : le bouchon `gh` rend une PR EN BROUILLON, donc `merge-mr` refuse en 6,
# ce qui est un verdict aussi observable qu'un autre. Le merge qui RÉUSSIT demande un bouchon
# capable de jouer un `origin/main`, un run Actions et une PR non brouillon fermant son ticket :
# c'est le lot 7 (#414) qui l'apporte, avec la couverture de bout en bout.


def _merge_tsv(run_dir: Path) -> list[list[str]]:
    return [ligne.split("\t")
            for ligne in (run_dir / "merge.tsv").read_text(encoding="utf-8").splitlines()
            if ligne and not ligne.startswith("#")]


def test_un_ticket_livre_entre_dans_la_file_de_merge(depot: Depot) -> None:
    """Le verdict « livré » (PR ouverte + « En revue ») inscrit la PR dans `merge.tsv`.

    C'est la seule porte d'entrée de la file : sans elle, le pilote n'aurait rien à drainer et le
    run laisserait ses PR ouvertes, ce que tout le chantier existe pour supprimer.
    """
    _livrables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "file",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    lignes = _merge_tsv(depot.racine / ".maestro/orchestrate/file")
    assert [ligne[0] for ligne in lignes] == ["130"], f"la file porte le ticket livré : {lignes}"
    assert lignes[0][1] == "99", "la PR de la fixture"
    assert lignes[0][2] == "feat/130-ticket-130", "la branche, seule chose que merge-mr sait juger"


def test_le_code_de_merge_mr_decide_de_la_conduite(depot: Depot) -> None:
    """Un `6` (geste humain — ici un brouillon) sort la PR de la file et NOMME sa cause.

    Un booléen ne suffirait pas : c'est sur ce code que le pilote distingue « repasser plus tard »
    de « bloquée », et une cause absente ferait chercher dans le journal ce que le résumé doit dire.
    """
    _livrables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "conduite",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    ligne = _merge_tsv(depot.racine / ".maestro/orchestrate/conduite")[0]
    assert ligne[3] == "bloquee", f"un 6 ne laisse pas la PR en attente : {ligne}"
    assert ligne[4] == "6", "le code est gardé — il distingue le réparable du geste humain"
    assert "brouillon" in ligne[6], f"la cause vient de merge-mr, pas d'un libellé : {ligne}"
    assert "bloquée" in r.stdout, "le résumé rend l'état de merge, pas seulement « PR ouverte »"
    assert "Merges :" in r.stdout, "le bilan par ticket"


def test_sans_merge_le_run_est_celui_d_avant(depot: Depot) -> None:
    """`--sans-merge` : aucune file, aucun bilan de merge, et le résumé dit lequel a tourné.

    Sans cette annonce, « aucune PR mergée » serait indiscernable de « aucune PR mergeable ».
    """
    _livrables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sansmerge", "--sans-merge",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (depot.racine / ".maestro/orchestrate/sansmerge/merge.tsv").exists()
    assert "Merges :" not in r.stdout
    assert "--sans-merge" in r.stdout, "le régime effectif est annoncé, pas deviné"


def test_une_reprise_ne_rejoue_pas_un_merge_deja_fait(depot: Depot) -> None:
    """La file du run repris est rechargée : ce qui était mergé le reste, le reste revient en file.

    Sans ce rechargement, les tickets livrés par le run coupé sortiraient du run par la porte de
    derrière : ils sont « En revue », donc sautés au moment de les prendre, donc jamais inscrits.
    """
    depot.ticket(130, "Ticket 130", statut="En revue")
    depot.ticket(131, "Ticket 131", statut="En revue")
    depot.mr("feat/131-ticket-131", "opened")
    dossier = _run_dir(depot, "coupe", [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    (dossier / "merge.tsv").write_text(
        "# iid\tpr\tbranche\tetat\tcode\tessais\tcause\n"
        "130\t77\tfeat/130-ticket-130\tmergee\t0\t1\t-\n"
        "131\t78\tfeat/131-ticket-131\tbloquee\t5\t1\tconflit avec origin/main\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("run.sh", "--resume", "coupe", "--run-id", "suite", "--sans-kill",
                    env={"MAESTRO_CLAUDE_BIN": "true",
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode in (0, 1), r.stdout + r.stderr
    lignes = {ligne[0]: ligne
              for ligne in _merge_tsv(depot.racine / ".maestro/orchestrate/suite")}
    assert lignes["130"][3] == "mergee", "un merge fait ne se rejoue pas"
    assert lignes["130"][5] == "0", "il n'a pas été retenté : aucun essai de plus"
    assert lignes["131"][5] == "1", "ce qui n'était pas mergé est rejugé, une fois"


def test_status_rend_la_file_de_merge(depot: Depot) -> None:
    """`status.sh` porte la file, sans quoi un run occupé à drainer ressemblerait à une panne :
    tout le plan traité, plus rien en vol, et pourtant le pilote tourne encore."""
    dossier = _run_dir(depot, "20260823-090000", [(1, 130, "-", "haute")],
                       resume=[(130, "OK", "99", 620, "3.50", "-")])
    (dossier / "merge.tsv").write_text(
        "# iid\tpr\tbranche\tetat\tcode\tessais\tcause\n"
        "130\t99\tfeat/130-ticket-130\tattente\t3\t2\tpipeline « in_progress »\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("status.sh")
    assert r.returncode == 0, r.stderr
    assert "Merges (1)" in r.stdout, "la file a sa section, avec sa propre horloge"
    assert "en attente" in r.stdout and "in_progress" in r.stdout, "l'état ET sa cause"


# =====================================================================================
# Le merge qui ABOUTIT, et sa sérialisation (#414, chantier #413)
# =====================================================================================
# Ce que #419 avait laissé au lot 7 : jusqu'ici la file était observée sur des REFUS (une PR en
# brouillon, `merge-mr` rend 6), ce qui suffit à juger la conduite du pilote mais jamais le merge
# lui-même. Ces tests-ci vont au bout — PR non brouillon fermant son ticket, pipeline vert sur la
# tête, `origin/main` réel — parce que les deux propriétés qui restaient à garder ne s'observent
# QUE sur un merge qui réussit : qu'il ait lieu, et qu'il ait lieu seul.


def _pr_mergeables(depot: Depot, iids: tuple[int, ...]) -> None:
    """Des tickets livrables dont la PR passe les quatre prérequis de `merge-mr`.

    Le dépôt jetable devient un vrai dépôt git : le troisième prérequis est un
    `git merge-tree --write-tree` contre `origin/main`, et il ne se bouchonne pas — c'est la seule
    source dont #303 a montré qu'elle pouvait porter la décision. Les branches partent donc de
    `main` sans diverger, ce qui est le cas nominal d'un run (toutes les branches d'un run
    naissent du même `origin/main`).
    """
    _init_git_sur_main(depot)
    for iid in iids:
        branche = f"feat/{iid}-ticket-{iid}"
        depot.ticket(iid, f"Ticket {iid}")
        depot.mr(branche, "opened", iid=800 + iid, brouillon=False, ferme=(iid,))
        depot.run_actions(branche)
        _git(depot, "branch", branche, "main")


def _pr_du_journal(depot: Depot, motif: str) -> list[str]:
    """Les appels `gh` du run qui portent `motif`, dans l'ordre où ils sont partis."""
    journal = depot.fixtures / "gh.log"
    if not journal.exists():
        return []
    return [ligne for ligne in journal.read_text(encoding="utf-8").splitlines() if motif in ligne]


@besoin_git
def test_une_pr_verte_est_mergee_pendant_le_run(depot: Depot) -> None:
    """Le critère du chantier : un run se termine TOUT MERGÉ, pas sur N PR à reprendre après coup.

    C'est aussi le cas nominal sans lequel les tests de refus ne prouveraient rien : ils
    vérifieraient qu'un merge impossible n'a pas lieu.
    """
    _pr_mergeables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "vert",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    ligne = _merge_tsv(depot.racine / ".maestro/orchestrate/vert")[0]
    assert ligne[3] == "mergee", f"la PR devait être mergée : {ligne}"
    assert ligne[4] == "0", "le code de merge-mr est gardé tel quel"
    puts = _pr_du_journal(depot, "pulls/930/merge")
    assert len(puts) == 1, f"un merge, et un seul : {puts}"
    assert "merge_method=squash" in puts[0]
    assert "mergée" in r.stdout, "le résumé rend le merge, pas seulement « PR ouverte »"


@besoin_git
def test_un_merge_reussi_ramasse_ce_qu_il_rend_inutile(depot: Depot) -> None:
    """Le quatrième déclencheur du ramassage (#438) : après CHAQUE merge, et non au prochain départ.

    Les trois autres — `ensure`, /branch-cleanup, démarrage d'un run — sont tous antérieurs au
    merge, ce qui était juste tant qu'un humain mergeait plus tard. Depuis #418/#419 un run de huit
    tickets se terminait en laissant huit worktrees et huit branches.

    Observé sur la BRANCHE LOCALE, seule des deux moitiés que ce harnais porte : le worktree d'un
    ticket y est un bouchon (`MAESTRO_ORCHESTRATE_WORKTREE`), donc il n'y a pas de répertoire à
    retirer. Le ramassage ciblé lui-même — ce qu'il retire, et surtout ce qu'il refuse de
    retirer — est gardé par `tests/test_worktree.py`.
    """
    _pr_mergeables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "ramasse",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    dossier = depot.racine / ".maestro/orchestrate/ramasse"
    assert _merge_tsv(dossier)[0][3] == "mergee", "sans merge, le reste ne prouverait rien"
    assert "feat/130-ticket-130" not in _git(depot, "branch", "--format=%(refname:short)"), \
        "la branche locale d'une PR mergée ne survit pas au run qui l'a mergée"
    assert "ramassage après merge" in (dossier / "merge.log").read_text(encoding="utf-8"), \
        "le ramassage laisse sa trace là où on relit un merge, pas seulement dans son effet"


@besoin_git
def test_un_merge_refuse_ne_ramasse_rien(depot: Depot) -> None:
    """Le contre-échantillon, sans lequel le test précédent ne dirait pas D'OÙ vient le ramassage.

    Une PR non mergée garde son travail : sa branche est peut-être la seule copie d'un correctif
    que `/mr-fix` reprendra. Ramasser sur autre chose que le verdict `0` de `merge-mr`, ce serait
    déduire d'un passage dans la file ce que seul le merge établit.
    """
    _pr_mergeables(depot, (130,))
    (depot.fixtures / "merge-refuse").write_text("", encoding="utf-8")
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "refuse",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    dossier = depot.racine / ".maestro/orchestrate/refuse"
    assert _merge_tsv(dossier)[0][3] != "mergee", "la fixture doit bien faire échouer le merge"
    assert "feat/130-ticket-130" in _git(depot, "branch", "--format=%(refname:short)")
    assert "ramassage après merge" not in (dossier / "merge.log").read_text(encoding="utf-8")


# --- L'attente de NAISSANCE, nommée sur la console (#595) ----------------------------------------
# Le 2026-08-26, la file de merge du run `20260826-183242` a passé 24 tentatives sur une seule PR
# sans que la console dise autre chose que « attente » — parce que rien n'était rouge, rien n'était
# en conflit, et que le pipeline n'existait tout simplement pas encore (docs/10 §8.9). Le mode de
# panne le plus coûteux est celui qui ne fait rougir personne.
#
# Le pilote avait déjà la bonne CONDUITE (un `3` reste en file, et c'est ce qui a fini par merger) :
# ce qui manquait était le mot. Ces deux tests gardent le mot, et l'absence du mot.

# Le run n'attend pas : ce qu'on observe est la ligne, jamais une durée. Le seuil à 0 la fait
# paraître dès la première passe, la naissance à 1 s empêche le drain final de patienter pour de
# bon — sans quoi le test mesurerait le défaut d'un chronomètre plutôt que le comportement.
_SANS_ATTENDRE = {
    "MAESTRO_ORCHESTRATE_MERGE": "1",
    "MAESTRO_ORCHESTRATE_NAISSANCE_SIGNAL": "0",
    "MAESTRO_PIPELINE_NAISSANCE": "1",
    "MAESTRO_PIPELINE_NAISSANCE_PR": "1",
    "MAESTRO_PIPELINE_SONDAGE": "1",
}


def _pr_sans_pipeline(depot: Depot, iid: int) -> str:
    """Une PR par ailleurs mergeable, dont le run n'est PAS ENCORE NÉ — le cas du 2026-08-26.

    C'est `_pr_mergeables` moins la seule chose qui compte ici : `run_actions`. Les trois autres
    prérequis de `merge-mr` passent, si bien que le verdict vient du quatrième et de lui seul.
    """
    branche = f"feat/{iid}-ticket-{iid}"
    _init_git_sur_main(depot)
    depot.ticket(iid, f"Ticket {iid}")
    depot.mr(branche, "opened", iid=800 + iid, brouillon=False, ferme=(iid,))
    _git(depot, "branch", branche, "main")
    return branche


@besoin_git
def test_une_attente_de_naissance_est_nommee_et_non_fondue_dans_attente(depot: Depot) -> None:
    """Le troisième critère de #595 : la console NOMME l'attente de naissance.

    Trois choses doivent y être, et chacune répond à une question que « attente » laissait ouverte :
    de quoi s'agit-il (le pipeline n'est pas né), faut-il agir (non — ni rouge ni conflit, donc rien
    que `/mr-fix` sache réparer), et que faire si l'attente devient intenable (le dispatch, avec la
    branche déjà substituée). Sans la deuxième, la ligne enverrait ouvrir une session de remédiation
    sur une PR qui n'a rien à remédier — c'est-à-dire exactement le geste que le ticket supprime.
    """
    branche = _pr_sans_pipeline(depot, 130)
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "naissance",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot), **_SANS_ATTENDRE})
    assert r.returncode == 0, r.stdout + r.stderr

    ligne = _merge_tsv(depot.racine / ".maestro/orchestrate/naissance")[0]
    assert ligne[3] == "attente", f"un `3` laisse la PR en file : {ligne}"
    assert ligne[4] == "3", "ni un rouge (4) ni un conflit (5) : le code sépare les conduites"
    assert "pas encore né" in ligne[6], f"la cause vient de merge-mr : {ligne}"

    assert "pas encore né" in r.stdout, (
        "la console fond encore l'attente de naissance dans « attente » — c'est le silence qui a "
        f"coûté 24 tentatives le 2026-08-26\n{r.stdout}"
    )
    assert "rien à débloquer" in r.stdout, (
        "la ligne doit dire qu'il n'y a NI rouge NI conflit : sans ça, elle envoie chercher une "
        "remédiation là où il n'y a qu'à attendre"
    )
    assert f"gh workflow run ci.yml --ref {branche}" in r.stdout, (
        "le remède est nommé au moment où il sert, branche substituée — il a dû être retrouvé à la "
        "main deux fois faute d'être écrit là"
    )
    # UNE fois, et c'est la règle de `merge_annonce` : un drain qui répète « pas encore » à chaque
    # passe est un drain qu'on cesse de lire. Le seuil à 0 rend la répétition possible s'il n'y a
    # pas de témoin, donc l'assertion mord.
    assert r.stdout.count("rien à débloquer") == 1, (
        f"l'attente est annoncée {r.stdout.count('rien à débloquer')} fois au lieu d'une"
    )


@besoin_git
def test_un_pipeline_vert_ne_declenche_aucune_annonce_de_naissance(depot: Depot) -> None:
    """Le contre-échantillon, sans lequel le test précédent ne dirait pas d'où vient la ligne.

    Le run naît après la PR : c'est la RÈGLE (#165, docs/10 §8), pas une anomalie. Annoncer chaque
    naissance apprendrait à ne plus lire l'annonce — ce qui se dit est celle qui DURE.
    """
    _pr_mergeables(depot, (130,))
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "nee",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot), **_SANS_ATTENDRE})
    assert r.returncode == 0, r.stdout + r.stderr
    assert _merge_tsv(depot.racine / ".maestro/orchestrate/nee")[0][3] == "mergee", \
        "sans merge, l'absence d'annonce ne prouverait rien"
    assert "pas encore né" not in r.stdout, "une naissance normale ne s'annonce pas"


@besoin_git
def test_une_pr_mergee_entre_temps_est_livree_et_non_bloquee(depot: Depot) -> None:
    """Le scénario exact de #593, rejoué : une REPRISE dont la PR a été mergée entre-temps.

    C'est ce qui rend le cas courant plutôt que marginal — entre la coupure d'un run et sa reprise,
    une PR peut être mergée par une session interactive, un `/mr-fix` ou un run jumeau. Le drain la
    retente, `merge-mr` rend `7`, et tout ce que ce test garde tient dans ce que le pilote en fait.

    Trois choses se jouaient ensemble et se sont toutes trompées de sens le 2026-08-26 (#582, PR
    #590) : le ticket comptait parmi les BLOQUÉS d'un run qui l'avait livré, la console annonçait
    « non mergée » à propos d'une PR mergée, et le ramassage — accroché au seul code `0` — laissait
    worktree et branche derrière lui.

    La quatrième assertion est celle qui distingue `7` de `0` : le run ne rejoue pas le merge, donc
    aucun PUT. La cinquième dit qu'il ne se l'attribue pas non plus.
    """
    _init_git_sur_main(depot)
    depot.ticket(130, "Ticket 130", statut="En revue")
    depot.mr("feat/130-ticket-130", "merged", iid=930, brouillon=False, ferme=(130,))
    depot.run_actions("feat/130-ticket-130")
    _git(depot, "branch", "feat/130-ticket-130", "main")
    dossier = _run_dir(depot, "coupe593", [(1, 130, "-", "haute")])
    (dossier / "merge.tsv").write_text(
        "# iid\tpr\tbranche\tetat\tcode\tessais\tcause\n"
        "130\t930\tfeat/130-ticket-130\tattente\t3\t1\tpipeline « in_progress »\n",
        encoding="utf-8",
        newline="\n",
    )
    r = depot.lance("run.sh", "--resume", "coupe593", "--run-id", "repris593", "--sans-kill",
                    env={"MAESTRO_CLAUDE_BIN": "true", "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode in (0, 1), r.stdout + r.stderr

    suite = depot.racine / ".maestro/orchestrate/repris593"
    ligne = _merge_tsv(suite)[0]
    assert ligne[3] == "mergee", f"un ticket livré ne se compte pas parmi les bloqués : {ligne}"
    assert ligne[4] == "7", "le code distingue « déjà mergée » des cinq gestes humains du 6"
    assert "déjà mergée" in ligne[6], f"la cause dit d'où vient le merge : {ligne}"
    assert _pr_du_journal(depot, "pulls/930/merge") == [], (
        "constater qu'une PR est mergée ne consiste pas à la merger une seconde fois"
    )
    assert "non mergée" not in r.stdout, (
        "le run annonçait l'inverse de la vérité — c'est le symptôme qui a ouvert #593"
    )
    assert "feat/130-ticket-130" not in _git(depot, "branch", "--format=%(refname:short)"), (
        "le ramassage suit le VERDICT et non l'auteur du merge : une branche mergée est inutile "
        "ici, que ce run l'ait mergée ou qu'il l'ait trouvée mergée"
    )
    assert "ramassage après merge" in (suite / "merge.log").read_text(encoding="utf-8")


@besoin_git
def test_les_merges_d_un_run_sont_serialises(depot: Depot) -> None:
    """Un seul merge à la fois — mesuré par une BARRIÈRE et des relevés par écrivain.

    ⚠ Pourquoi une barrière et pas un `sleep` : « deux merges en même temps » est une course, et un
    `sleep` la fait trancher par la charge de la machine — le test dirait tantôt le code, tantôt
    l'ordonnancement du système (#292). Chaque merge signale donc son arrivée puis ATTEND d'en voir
    un autre ; si le pilote en lançait deux de front, la barrière se lèverait et les deux relevés
    porteraient 2.

    ⚠ Et pourquoi un fichier par écrivain plutôt qu'un compteur : un compteur partagé se lit puis
    se réécrit en deux temps, donc perd une incrémentation dès que deux écrivains arrivent
    ensemble — c'est-à-dire exactement ce que la barrière rend probable (#313). Le pic se prend
    APRÈS COUP, sur des relevés dont aucun n'a deux auteurs.

    Ce que ce test garde vraiment : le merge appartient au PILOTE et à lui seul (#419). Le jour où
    il repartirait dans le sous-shell d'une session, deux PR se mergeraient de front et
    périmeraient mutuellement leur verdict de conflit.
    """
    _pr_mergeables(depot, (130, 131))
    barriere = depot.racine / ".maestro" / "barriere"
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "serie",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1",
                         "MAESTRO_STUB_BARRIERE": str(barriere),
                         "MAESTRO_STUB_BARRIERE_DELAI": "3"})
    assert r.returncode == 0, r.stdout + r.stderr

    dossier = depot.racine / ".maestro/orchestrate/serie"
    etats = {ligne[0]: ligne[3] for ligne in _merge_tsv(dossier)}
    assert etats == {"130": "mergee", "131": "mergee"}, f"les deux PR sont mergées : {etats}"

    releves = sorted(barriere.glob("*.vus"))
    assert len(releves) == 2, f"un relevé par merge, et deux merges ont eu lieu : {releves}"
    pic = max(int(f.read_text(encoding="utf-8").strip() or 0) for f in releves)
    assert pic == 1, (
        f"pic de simultanéité {pic} : deux merges se sont recouverts alors que le pilote doit les "
        "sérialiser — le second aurait jugé son conflit sur un origin/main déjà périmé"
    )
    assert not list(barriere.glob("*.en-vol")), "chaque écrivain retire son témoin en partant"


@besoin_git
def test_le_verdict_de_la_seconde_pr_est_pris_apres_le_premier_merge(depot: Depot) -> None:
    """Une passe du drain s'arrête au PREMIER merge réussi, et c'est le cœur du chantier.

    Un merge déplace `origin/main` et périme le verdict de conflit de toutes les autres PR : les
    juger dans la même passe, c'est les juger sur une mesure d'avant. C'est aussi ce qui rend les
    conflits d'un run RÉSOLUBLES — à la fin d'un run les PR ne sont pas en conflit avec `main` mais
    entre elles, et c'est le premier merge qui donne un côté au suivant.
    """
    _pr_mergeables(depot, (130, 131))
    plan = _plan(depot, [(1, 130, "-", "haute"), (2, 131, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "ordre",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    journal = (depot.fixtures / "gh.log").read_text(encoding="utf-8").splitlines()
    merge_130 = [i for i, ligne in enumerate(journal) if "pulls/930/merge" in ligne]
    lectures_131 = [
        i for i, ligne in enumerate(journal)
        if 'headRefName: "feat/131-ticket-131"' in ligne
    ]
    assert merge_130 and lectures_131, f"les deux PR ont bien été lues et mergées : {journal[-6:]}"
    assert lectures_131[-1] > merge_130[0], (
        "la seconde PR est rejugée APRÈS le merge de la première — sinon son verdict de conflit "
        "porterait sur un origin/main que le merge vient de déplacer"
    )


@besoin_git
def test_une_pr_bloquee_ne_reçoit_pas_plus_de_deux_sessions_de_deblocage(depot: Depot) -> None:
    """Le plafond de #420 : deux sessions `/mr-fix`, puis la PR est laissée à un humain.

    Sans plafond, une PR que rien ne peut réparer — un secret manquant, un test cassé ailleurs —
    consommerait des sessions jusqu'à la fin du run, sur le quota du travail restant.
    """
    _pr_mergeables(depot, (130,))
    depot.run_actions("feat/130-ticket-130", conclusion="failure")
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "plafond",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    dossier = depot.racine / ".maestro/orchestrate/plafond"
    ligne = _merge_tsv(dossier)[0]
    assert ligne[3] == "bloquee", f"une PR au rouge reste bloquée : {ligne}"
    assert ligne[4] == "4", "le code distingue le pipeline rouge du conflit et du geste humain"
    assert ligne[7] == "2", f"exactement deux sessions de déblocage, pas trois : {ligne}"
    assert (dossier / "130-mrfix.resultat.txt").exists()
    assert (dossier / "130-mrfix2.resultat.txt").exists(), (
        "la seconde session porte son rang : sans lui elle écraserait le journal de la première, "
        "c'est-à-dire ce qu'on ira lire pour comprendre pourquoi la première n'a pas suffi"
    )
    assert not (dossier / "130-mrfix3.resultat.txt").exists()
    assert not _pr_du_journal(depot, "/merge"), "aucun merge : la PR est restée au rouge"
    assert "plafond de 2 tentative(s) atteint" in r.stdout


@besoin_git
def test_une_session_de_deblocage_en_echec_laisse_la_pr_ouverte_et_intacte(depot: Depot) -> None:
    """Une session qui abandonne proprement est un RÉSULTAT, pas un verdict sur la PR.

    Elle a pu renoncer pour la bonne raison — une résolution qui n'est pas claire ne se pousse pas
    (§8.3). Le run retente donc le merge quand même, et c'est `merge-mr` qui tranche : la seule
    chose qu'une session sache dire est ce qu'elle a tenté, jamais si la PR est mergeable.
    """
    _pr_mergeables(depot, (130,))
    depot.run_actions("feat/130-ticket-130", conclusion="failure")
    gabarit = _statut_json("%s", "En revue")
    echoue = _claude_stub(depot, f"""
        if printf '%s\\n' "$@" | grep -q '/mr-fix'; then
          printf '{{"type":"result","subtype":"error","is_error":true,"total_cost_usd":0.5}}\\n'
          exit 1
        fi
        iid=$(printf '%s\\n' "$@" | grep -o 'GitLab #[0-9]*' | head -1 | tr -dc '0-9')
        printf '{gabarit}' "$iid" > "$MAESTRO_FIXTURES/owner-$iid.json"
        printf '{{"type":"result","subtype":"success","is_error":false,"total_cost_usd":1}}\\n'
        exit 0
    """)
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "echec",
                    env={"MAESTRO_CLAUDE_BIN": echoue, "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr

    dossier = depot.racine / ".maestro/orchestrate/echec"
    ligne = _merge_tsv(dossier)[0]
    assert ligne[3] == "bloquee", "la PR reste bloquée, et nommée comme telle au bilan"
    assert not _pr_du_journal(depot, "/merge"), "rien n'a été mergé sur un déblocage en échec"
    assert not _pr_du_journal(depot, "pr\tclose"), "et rien n'a été fermé : la PR est intacte"

    # Le cœur du test : la PR est REJUGÉE après chaque session, et le verdict vient de `merge-mr`.
    # Un essai initial, puis un par session de déblocage — la session qui a échoué coûte donc un
    # appel de plus, et c'est le plafond de deux qui borne ce que cette générosité peut coûter.
    assert ligne[5] == "3", f"1 essai initial + 1 par session de déblocage : {ligne}"
    assert ligne[7] == "2", "les deux sessions ont bien été jouées malgré la première en échec"
    assert "on retente le merge" in r.stdout

    # ⚠ Et ce que ce test établit en creux : le CODE DE SORTIE d'une session n'est pas lu comme un
    # verdict. Le bouchon sort en 1, et pourtant la session est comptée « finie » — même règle que
    # pour un ticket (#203) : hors limite d'usage, une sortie non nulle dit seulement que le tour
    # est terminé. La seule chose qu'une session sache dire est ce qu'elle a tenté, jamais si la PR
    # est mergeable ; l'inverse ferait renoncer à une PR qu'un correctif poussé a peut-être sauvée.
    resultat = (dossier / "130-mrfix.resultat.txt").read_text(encoding="utf-8")
    assert "merge-mr" in resultat, (
        "le résultat de la session renvoie au verbe qui tranche, au lieu de rendre un verdict"
    )


@besoin_git
def test_sans_mrfix_une_pr_bloquee_reste_bloquee_avec_sa_cause(depot: Depot) -> None:
    """`--sans-mrfix` : aucune session de déblocage, et le bilan dit pourquoi la PR reste là."""
    _pr_mergeables(depot, (130,))
    depot.run_actions("feat/130-ticket-130", conclusion="failure")
    plan = _plan(depot, [(1, 130, "-", "haute")])
    r = depot.lance("run.sh", "--plan", plan, "--run-id", "sansfix", "--sans-mrfix",
                    env={"MAESTRO_CLAUDE_BIN": _stub_livre(depot),
                         "MAESTRO_ORCHESTRATE_MERGE": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    dossier = depot.racine / ".maestro/orchestrate/sansfix"
    ligne = _merge_tsv(dossier)[0]
    assert ligne[3] == "bloquee"
    assert ligne[7] == "0", "aucune session de déblocage n'a été ouverte"
    assert not (dossier / "130-mrfix.resultat.txt").exists()
