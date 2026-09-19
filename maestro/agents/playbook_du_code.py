"""Lecture des playbooks « du code » — les documents Markdown livrés avec le paquet (#295).

Le playbook d'un rôle (docs/04 §1) est un **document Markdown structuré** : mission, entrées
attendues, méthode, critères de « terminé », garde-fous, format de sortie. Jusqu'ici les
prompts systèmes effectifs en étaient une version dégradée de trois paragraphes, écrite en dur
dans le module de chaque rôle (`maestro.agents.developer` et consorts) : le modèle du produit
et le code n'étaient pas au même endroit, et une chaîne Python ne se relit ni ne se diffe
comme un document. Ce module lit ces documents là où ils vivent désormais —
`maestro/agents/playbooks_defaut/<agent>.md`, livrés avec le paquet.

Trois fragments sont **partagés par tous les rôles**, pour n'exister qu'une fois :

- `_socle.md` — le **régime sénior** (#293) : ce que l'agent décide seul, ce qu'il remonte au
  lieu de le décider, et ce qu'il rend (décisions & arbitrages, recommandations). Il vaut pour
  les **deux** chemins d'exécution — runtime outillé et exécution texte du catalogue.
- `_registre.md` — le **registre de langue** du produit (#945) : l'agent vouvoie l'utilisateur.
  C'est le fragment le plus partagé des trois, parce qu'il ne s'arrête pas aux rôles : les
  prompts conversationnels de la Control Tower (assistant, orchestration, cadre de chat) le
  prennent par `registre()`. Un registre écrit en deux endroits finit par en faire deux, et
  c'est le défaut que #945 corrige — l'assistant tutoyait deux lignes sous un accueil qui
  vouvoie.
- `_cadre_outille.md` — le cadre propre à l'exécution **outillée** : répertoire de travail,
  livrable matérialisé en fichiers, ce qui a servi à le produire rangé à part (#944), rien qui
  survive à la tâche. L'exécution texte du catalogue n'a pas d'outils : elle ne le charge pas.
  ⚠ Il ne promet **pas** un répertoire « isolé » : depuis #839 ce répertoire peut être la racine
  du projet de quelqu'un, et c'est cette fausse prémisse qui faisait laisser les dossiers de
  travail des agents à côté du livrable. *Où* ranger le reste est dit par l'espace lui-même, dans
  le message de la tâche (`maestro.sandbox.en_place.EspaceEnPlace.consigne_espace`).

Les documents de rôle les appellent par les marqueurs `{{socle}}`, `{{registre}}` et
`{{cadre}}`, substitués à la lecture. Un marqueur inconnu (ou une accolade double laissée dans
le texte) est une erreur franche : mieux vaut un import qui échoue qu'un prompt système servi
avec un trou dedans.

⚠ **Un fragment peut en appeler un autre** — `_socle.md` appelle `{{registre}}`, et c'est ce qui
fait que tout rôle hérite du registre par le seul `{{socle}}` qu'il porte déjà : un rôle neuf ne
peut pas l'oublier. La substitution redescend donc dans les fragments insérés, et un cycle
(`fragment_a` → `fragment_b` → `fragment_a`) lève plutôt que de boucler.

Ce module **n'importe rien du paquet** — ni le catalogue, ni les profils de rôle, ni le
stockage versionné. C'est ce qui lui permet d'être appelé par les uns comme par les autres
sans boucle d'import : `maestro.agents.playbooks` construit `PLAYBOOK_DEFAUTS` dessus, chaque
module de rôle y prend son `prompt_systeme`, et `maestro.agents.catalog` y prend le socle.

⚠ À ne pas confondre avec `core/playbooks/` (voir `maestro.agents.playbooks`), le dépôt
**versionné** des éditions humaines : ce qui est ici est le **repli** — ce que le moteur charge
tant que personne n'a rien publié.
"""

from __future__ import annotations

import re
from functools import cache
from pathlib import Path

#: Racine des documents livrés avec le paquet (un fichier par rôle, plus les fragments
#: partagés préfixés d'un souligné).
RACINE = Path(__file__).resolve().parent / "playbooks_defaut"

#: Le tronc commun du régime sénior, partagé par les deux chemins d'exécution.
FRAGMENT_SOCLE = "_socle"

#: Le cadre d'exécution outillée (espace de travail, livrable, atelier), réservé aux runtimes.
FRAGMENT_CADRE = "_cadre_outille"

#: Le registre de langue du produit (#945) : l'agent vouvoie l'utilisateur. Appelé par
#: `_socle.md`, donc porté par tous les rôles, et servi à part par `registre()` aux prompts
#: conversationnels de la Control Tower, qui ne passent pas par les playbooks.
FRAGMENT_REGISTRE = "_registre"

#: Les marqueurs de substitution admis dans un document de rôle, et le fragment qu'ils
#: appellent. Volontairement fermé : un marqueur hors de cette table lève.
_MARQUEURS = {
    "socle": FRAGMENT_SOCLE,
    "registre": FRAGMENT_REGISTRE,
    "cadre": FRAGMENT_CADRE,
}

#: Un marqueur dans le texte d'un rôle : `{{socle}}`, `{{registre}}`, `{{cadre}}`.
_MARQUEUR = re.compile(r"\{\{\s*([a-z_]+)\s*\}\}")

#: La clause de rendu de compte, commune à tous les rôles : le pendant, dans le **message
#: de tâche** (`RoleProfile.consigne_finale`), de la section « Ce que tu rends » du socle.
#: Elle y est répétée à dessein — le prompt système cadre le rôle, la consigne finale cadre
#: le livrable attendu, et c'est celle-là que l'agent relit juste avant de conclure.
CONSIGNE_RENDU_COMPTE = (
    "puis rends deux sections : « Décisions & arbitrages » (ce que tu as tranché, pourquoi, "
    "les options écartées) et « Recommandations » (suite conseillée, angles morts, ce qui "
    "t'a manqué, ce qui demande un arbitrage humain)."
)


@cache
def fragment(nom: str) -> str:
    """Le fragment partagé `nom` (`_socle`, `_cadre_outille`), texte brut sans substitution.

    Lève `FileNotFoundError` si le fichier manque — un playbook amputé de son régime ne
    doit pas partir en prompt système.
    """
    return _lire(nom)


def socle() -> str:
    """Le tronc commun du régime sénior (#293), marqueurs substitués.

    Servi à part pour l'exécution **texte** du catalogue (`maestro.agents.catalog`), qui
    n'a ni outils ni espace de travail : elle prend le régime sans le cadre outillé.

    Substitué, et non brut : le socle appelle `{{registre}}` (#945), et les deux chemins
    d'exécution doivent recevoir le **même** texte — celui-ci et celui qu'un `{{socle}}` de
    document de rôle insère.
    """
    return _developpe(fragment(FRAGMENT_SOCLE))


def registre() -> str:
    """Le registre de langue du produit (#945) : l'agent vouvoie l'utilisateur.

    Servi à part pour les prompts **conversationnels** de la Control Tower — assistant
    (`maestro.controltower.assistance`), orchestration, cadre de chat —, qui ne sont pas des
    playbooks et n'ont donc aucun marqueur à substituer. Les rôles, eux, le reçoivent par le
    `{{socle}}` qu'ils portent déjà.
    """
    return fragment(FRAGMENT_REGISTRE)


@cache
def playbook_du_code(agent: str) -> str:
    """Le playbook « du code » de `agent` : son document, marqueurs partagés substitués.

    C'est le repli du rôle : ce que `PLAYBOOK_DEFAUTS` expose et ce que le moteur charge
    tant qu'aucune version n'a été publiée dans `core/playbooks/` (#76). Lève
    `FileNotFoundError` si le document manque, `ValueError` sur un marqueur inconnu.
    """
    texte = _developpe(_lire(agent))
    if "{{" in texte:
        raise ValueError(f"marqueur mal formé dans le playbook du rôle {agent!r}.")
    return texte


@cache
def roles_du_code() -> tuple[str, ...]:
    """Les rôles qui ont un playbook livré avec le paquet, par ordre alphabétique.

    Les fragments partagés (préfixés d'un souligné) n'en sont pas : ils n'ont pas de rôle.
    """
    return tuple(sorted(c.stem for c in RACINE.glob("*.md") if not c.name.startswith("_")))


def _developpe(texte: str, vus: frozenset[str] = frozenset()) -> str:
    """Substitue les marqueurs de `texte`, fragments imbriqués compris (#945).

    `vus` porte les fragments déjà ouverts au-dessus de cet appel : c'est ce qui permet à un
    cycle de lever au lieu de boucler jusqu'à la pile.
    """
    return _MARQUEUR.sub(lambda m: _substitue(m, vus), texte)


def _substitue(m: re.Match[str], vus: frozenset[str]) -> str:
    """Remplace un marqueur `{{…}}` par son fragment partagé (lève s'il est inconnu).

    Le fragment inséré est lui-même développé : `_socle.md` appelle `{{registre}}` (#945),
    et le résultat ne doit pas repartir en prompt système avec une accolade dedans.
    """
    cle = m.group(1)
    if cle not in _MARQUEURS:
        raise ValueError(
            f"marqueur de playbook inconnu : {{{{{cle}}}}} (attendus : "
            f"{', '.join(sorted(_MARQUEURS))})."
        )
    nom = _MARQUEURS[cle]
    if nom in vus:
        raise ValueError(
            f"fragment de playbook récursif : {{{{{cle}}}}} s'appelle lui-même "
            f"(chaîne : {', '.join(sorted(vus))})."
        )
    return _developpe(fragment(nom).strip(), vus | {nom})


def _lire(nom: str) -> str:
    """Le texte d'un document du dossier, normalisé (UTF-8, sans blancs de fin)."""
    chemin = RACINE / f"{nom}.md"
    if not chemin.is_file():
        raise FileNotFoundError(f"playbook du code introuvable : {chemin}")
    return chemin.read_text(encoding="utf-8").strip()
