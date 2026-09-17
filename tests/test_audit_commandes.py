"""Ce qu'un prompt du dépôt affirme, gardé par une machine (#966, lot final de #960).

L'audit du 2026-09-14 (docs/25 §9) a trouvé quatre dérives qu'**aucun** outil du dépôt ne
regardait : `doctor.sh` juge la forge, `ecart-run.sh` juge les listes de permissions,
`test_cycle_de_vie.py` juge la pose du cycle de vie dans les prompts — personne ne jugeait si un
prompt **dit vrai**. Les lots 1 à 4 ont corrigé des fichiers ; rien n'empêchait la même dérive de
revenir au ticket suivant, et elle était déjà revenue une fois (M4 de #304, recommandé puis oublié
cinq semaines). Cette suite est la moitié qui survit.

**Quatre gardes, une par constat** :

- `TestBudgetClaudeMd` — `CLAUDE.md` est chargé par chaque session et avait quadruplé sans que
  personne le décide. #965 a fixé **25 000 tokens**, au compteur du dépôt (`estimer_tokens`), et le
  dépassement **lève** : un fichier trop gros est une décision à prendre, pas un réglage à faire en
  passant — même contrat que `BUDGET_CARTE_TOKENS` (`tests/test_documentation.py`).
- `TestForgeAuPresent` — `.claude/**` et `scripts/orchestrate/run.sh` ne nomment plus GitLab au
  présent, ni `.gitlab-ci.yml` du tout (#961).
- `TestAtelierDeSession` — les commandes qu'une session de run joue n'envoient plus ses fichiers de
  travail dans le scratchpad, chemin absolu que le prompt de `run.sh` refuse (#962).
- `TestChainagesDesDeclarations` — une commande jouée comme étape transmet son `allowed-tools:` à
  celle qui la joue (#964, docs/10 §7.1).

**Le temps d'une phrase ne se juge pas par des mots.** « Au passé » n'a pas de forme lexicale
fiable — « du temps de », « pendant la migration », « la version GitLab de cette boucle coûtait »
ne partagent rien —, et juger un texte humain par un lexique est proscrit dans ce dépôt : il
mesure la forme d'une phrase, pas ce qu'elle dit, et il se trompe en silence. Les deux gardes de
mentions tiennent donc un **inventaire** : chaque mention admise y est recopiée avec **sa raison**,
jugée une fois par quelqu'un ; la machine ne détecte que ce que personne n'a jugé. C'est le partage
de #562 — ce qui est automatique est la détection du manque, jamais le verdict. Une mention neuve,
au passé comme au présent, fait rougir la suite jusqu'à ce qu'on la corrige ou qu'on l'inscrive ;
une mention inscrite qui a disparu du fichier la fait rougir aussi, pour que l'inventaire ne
couvre jamais que ce qui existe.

**L'échantillon fautif d'abord, et c'est le vrai.** Chaque motif est éprouvé avant le balayage sur
les phrases **exactes** que #961 et #962 ont retirées (recopiées de `d742670^` et `85f42e5^`) : un
motif trop lâche répondrait « tout va bien » sur une question jamais posée, ce qui a laissé passer
#830 pendant un mois. Un échantillon inventé prouverait qu'on sait reconnaître la faute qu'on a en
tête ; celui-ci prouve que la garde aurait arrêté celle qui a eu lieu.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from maestro.sources.extraction import estimer_tokens

#: La racine du dépôt — les fichiers réels, ceux qu'une session lit.
RACINE = Path(__file__).resolve().parents[1]
CLAUDE_MD = RACINE / "CLAUDE.md"
COMMANDES = RACINE / ".claude" / "commands"
DOCS_10 = RACINE / "docs" / "10-workflow-git.md"
DOCS_25 = RACINE / "docs" / "25-audit-commandes-claude.md"
RUN_SH = "scripts/orchestrate/run.sh"

#: Ce que `CLAUDE.md` peut coûter à chaque session, arbitré par #965 le 2026-09-17 : l'ordre de
#: grandeur du jour de l'audit #304 (22 261) plus une marge — la place de ~150 interdits d'une
#: phrase. 16 000 aurait coupé des interdits utiles ; 40 000 ne rendait que la moitié du gain.
BUDGET_CLAUDE_MD_TOKENS = 25_000


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# Outillage commun : aplatir un fichier, et y trouver ce que personne n'a jugé
# ─────────────────────────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Admise:
    """Une mention jugée une fois : l'extrait qui la porte, et pourquoi elle reste."""

    extrait: str
    raison: str


#: En tête de ligne : l'indentation, la citation Markdown (`>`) et le commentaire shell (`#`). Les
#: retirer avant d'aplatir laisse un extrait tenir à cheval sur deux lignes d'une citation ou d'un
#: commentaire — sans quoi rewrapper un paragraphe ferait rougir l'inventaire pour rien.
_MARQUE_DE_LIGNE = re.compile(r"^\s*[>#]*\s*")


def aplatir(texte: str) -> str:
    """Le texte sur une seule ligne, espaces normalisés et marques de début de ligne retirées."""
    return " ".join(
        " ".join(_MARQUE_DE_LIGNE.sub("", ligne) for ligne in texte.splitlines()).split()
    )


def _spans(plat: str, admises: Iterable[Admise]) -> list[tuple[int, int]]:
    spans = []
    for admise in admises:
        extrait = aplatir(admise.extrait)
        debut = plat.find(extrait)
        while debut != -1:
            spans.append((debut, debut + len(extrait)))
            debut = plat.find(extrait, debut + 1)
    return spans


def non_jugees(texte: str, motif: re.Pattern[str], admises: Iterable[Admise] = ()) -> list[str]:
    """Les occurrences du motif qu'aucun extrait admis ne couvre, chacune dans son contexte."""
    plat = aplatir(texte)
    couvertes = _spans(plat, admises)
    return [
        f"…{plat[max(0, m.start() - 60) : m.end() + 60]}…"
        for m in motif.finditer(plat)
        if not any(debut <= m.start() and m.end() <= fin for debut, fin in couvertes)
    ]


def absentes(texte: str, admises: Iterable[Admise]) -> list[str]:
    """Les extraits inscrits qu'on ne retrouve plus dans le fichier."""
    plat = aplatir(texte)
    return [admise.extrait for admise in admises if aplatir(admise.extrait) not in plat]


def lire(relatif: str) -> str:
    return (RACINE / relatif).read_text(encoding="utf-8")


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 1. Le budget de CLAUDE.md (#965)
# ─────────────────────────────────────────────────────────────────────────────────────────────────


class BudgetDepasse(AssertionError):
    """`CLAUDE.md` dépasse son budget — une décision à prendre, jamais une troncature."""


def verifier_budget(texte: str, budget: int = BUDGET_CLAUDE_MD_TOKENS) -> int:
    """Le coût de `texte` s'il tient dans `budget` ; sinon lève, en disant de combien."""
    cout = estimer_tokens(texte)
    if cout > budget:
        raise BudgetDepasse(
            f"CLAUDE.md coûte {cout} tokens pour un budget de {budget} "
            f"(BUDGET_CLAUDE_MD_TOKENS, arbitrage de #965) : {cout - budget} de trop, payés par "
            "chaque session. Il ne garde que la règle et ce qu'il ne faut pas défaire : la "
            "démonstration (mesures, pistes écartées, historique, incidents) va dans docs/. "
            "Relever le budget est une décision qui se prend en connaissant ce coût."
        )
    return cout


#: Un budget annoncé en toutes lettres : « budget 25 000 tokens », « Budget : 25 000 tokens ».
_BUDGET_ANNONCE = re.compile(r"(?i)budget\W{0,4}(\d{1,3}(?:\s\d{3})+|\d+) tokens")


def budgets_annonces(texte: str) -> set[int]:
    return {int(re.sub(r"\D", "", nombre)) for nombre in _BUDGET_ANNONCE.findall(texte)}


class TestBudgetClaudeMd:
    """Le budget est un contrat : mesuré sur le fichier chargé, et son dépassement lève."""

    def test_claude_md_tient_sous_son_budget(self) -> None:
        """Le fichier réel passe dessous — mesuré à 16 040 tokens le 2026-09-17.

        Le plancher n'est pas décoratif : un `CLAUDE.md` vide ou mal lu passerait le plafond sans
        rien prouver.
        """
        cout = verifier_budget(CLAUDE_MD.read_text(encoding="utf-8"))
        assert cout > 5_000, "CLAUDE.md lu presque vide : ce n'est pas le fichier chargé"

    def test_a_un_token_pres_la_garde_leve(self) -> None:
        """La paire qui prouve que la garde n'est pas creuse, sur le fichier réel.

        Au coût exact elle rend le coût ; un token en dessous elle lève. Une comparaison mal
        orientée, ou un budget jamais lu, rendrait les deux appels verts.
        """
        texte = CLAUDE_MD.read_text(encoding="utf-8")
        cout = estimer_tokens(texte)
        assert verifier_budget(texte, budget=cout) == cout
        with pytest.raises(BudgetDepasse):
            verifier_budget(texte, budget=cout - 1)

    def test_le_message_nomme_le_cout_le_budget_et_l_arbitrage(self) -> None:
        """Une erreur franche dit de combien, et où la décision a été prise."""
        with pytest.raises(BudgetDepasse) as leve:
            verifier_budget("x" * 30, budget=3)
        message = str(leve.value)
        assert "10 tokens pour un budget de 3" in message
        assert "BUDGET_CLAUDE_MD_TOKENS" in message and "#965" in message

    def test_le_budget_annonce_est_celui_qui_est_garde(self) -> None:
        """`CLAUDE.md` et docs/25 annoncent le budget en chiffres : ce sont les chiffres gardés.

        Sans ce lien, relever la constante en silence laisserait deux documents dire 25 000
        tokens à une session qui en paie davantage.
        """
        assert budgets_annonces("(#965, budget 25 000 tokens)") == {25_000}
        assert budgets_annonces("**Budget : 25 000 tokens**, soit") == {25_000}
        assert budgets_annonces("BUDGET_CARTE_TOKENS = 16_000") == set()
        assert BUDGET_CLAUDE_MD_TOKENS in budgets_annonces(CLAUDE_MD.read_text(encoding="utf-8"))
        assert BUDGET_CLAUDE_MD_TOKENS in budgets_annonces(DOCS_25.read_text(encoding="utf-8"))


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 2. La forge au présent (#961)
# ─────────────────────────────────────────────────────────────────────────────────────────────────

#: Une mention de l'ancienne forge. `scripts/gitlab/` est un CHEMIN — le helper y vit toujours, et
#: le nommer est vrai au présent : le lire comme une mention noierait l'inventaire sous ~120 appels.
_FORGE = re.compile(r"(?i)(?<!scripts/)gitlab|\bglab\b")

#: Un fichier que le dépôt n'a plus : aucune phrase ne le nomme, à aucun temps.
_CI_GITLAB = ".gitlab-ci.yml"

_PASSE = "au passé : ce que la forge était, pas ce qu'elle est"
_VOCABULAIRE = "au présent et vrai : `lib.sh` garde le vocabulaire GitLab comme contrat de surface"
_DEUX_FORGES = "au présent et vrai : `lib.sh merge-settings` lit le réglage des deux forges"

#: Les mentions jugées, fichier par fichier. Une mention neuve se corrige (la forge est GitHub,
#: #335) ou s'inscrit ici avec sa raison — jamais l'une à la place de l'autre.
FORGE_ADMISES: Mapping[str, tuple[Admise, ...]] = {
    ".claude/commands/backlog.md": (
        Admise("gardent le vocabulaire GitLab, c'est le contrat de `lib.sh`", _VOCABULAIRE),
        Admise("(là où GitLab distinguait `#` et `!`)", _PASSE),
    ),
    ".claude/commands/branch-cleanup.md": (
        Admise("la version GitLab de cette boucle coûtait", _PASSE),
        Admise("pré-cochée sur GitLab, `delete_branch_on_merge` sur GitHub", _DEUX_FORGES),
    ),
    ".claude/commands/mr-fix.md": (
        Admise("mesure du 2026-08-07 côté GitLab", _PASSE),
        Admise("du temps de la CI GitLab", _PASSE),
        Admise("cas observé du temps de GitLab", _PASSE),
    ),
    ".claude/commands/mr-review.md": (
        Admise("normalisé vers le vocabulaire GitLab", _VOCABULAIRE),
        Admise("(Pendant la migration, `origin` pointait encore sur GitLab", _PASSE),
    ),
    ".claude/commands/orchestrate.md": (
        Admise(
            "(`--no-gitlab` reste accepté en alias historique)",
            "au présent et vrai : l'alias est toujours accepté",
        ),
    ),
    ".claude/commands/setup.md": (
        Admise("l'étape `runner` a disparu avec l'outillage GitLab", _PASSE),
    ),
    ".claude/commands/ticket-finish.md": (
        Admise("est parti avec la CI GitLab (#344", _PASSE),
        Admise("(#165 sur GitLab, `on: pull_request` sur GitHub", _PASSE),
        Admise("normalisé vers le vocabulaire GitLab pour que ses appelants", _VOCABULAIRE),
        Admise("sur un ticket importé de GitLab", _PASSE),
    ),
    RUN_SH: (
        Admise("troisième support après le champ natif de GitLab et les six labels", _PASSE),
        Admise("son verdict GitLab n'était jamais lu", _PASSE),
    ),
}

#: Les phrases que #961 a corrigées, telles qu'elles étaient (`git show d742670^:<fichier>`).
_FORGE_FAUTIVES: Mapping[str, str] = {
    ".claude/commands/ticket-finish.md": (
        "contrôles locaux (#214, `docs/10-workflow-git.md` §8.4), il lit les jobs dans "
        "`.gitlab-ci.yml`\n   et déduit du diff ce qui les concerne."
    ),
    ".claude/commands/ticket-ship.md": (
        "commit porte `Closes` (et non `Refs`) : GitLab fermera le ticket au merge."
    ),
    ".claude/commands/mr-review.md": (
        "lirait normalement du remote, or `origin` pointe sur GitLab jusqu'à la bascule (#343)."
    ),
    RUN_SH: "Tu traites intégralement le ticket GitLab #$1 de ce dépôt, seul et sans supervision.",
}

#: `.claude/settings.local.json` et `.claude/worktrees/` sont ignorés par git : ce qu'ils portent
#: est propre à un poste, et le second abrite des clones entiers du dépôt.
_HORS_DEPOT = {"settings.local.json", "worktrees"}


def fichiers_du_perimetre_forge() -> list[str]:
    """`.claude/**` tel que le dépôt le versionne, puis le prompt de run."""
    racine = RACINE / ".claude"
    fichiers: list[Path] = []
    for entree in sorted(racine.iterdir()):
        if entree.name in _HORS_DEPOT:
            continue
        fichiers += (
            sorted(p for p in entree.rglob("*") if p.is_file()) if entree.is_dir() else [entree]
        )
    return [p.relative_to(RACINE).as_posix() for p in fichiers] + [RUN_SH]


class TestForgeAuPresent:
    """`.claude/**` et le prompt de run nomment la forge d'aujourd'hui."""

    def test_le_motif_arrete_les_phrases_que_961_a_corrigees(self) -> None:
        """Chaque phrase retirée par #961 aurait fait rougir le balayage, inventaire compris."""
        for relatif, fautive in _FORGE_FAUTIVES.items():
            assert non_jugees(fautive, _FORGE, FORGE_ADMISES.get(relatif, ())), relatif

    def test_le_motif_laisse_passer_le_chemin_du_helper_et_ce_qui_est_inscrit(self) -> None:
        """L'autre moitié : un motif qui trouve tout ne prouve rien de plus qu'un motif muet."""
        assert not non_jugees("bash scripts/gitlab/lib.sh merge-mr <iid>", _FORGE)
        corrige = (
            "(Pendant la migration, `origin` pointait encore sur GitLab et `gh` ne pouvait pas\n"
            "   le déduire du remote, #343 ; la bascule est faite, `origin` est le dépôt GitHub.)"
        )
        admises = FORGE_ADMISES[".claude/commands/mr-review.md"]
        assert not non_jugees(corrige, _FORGE, admises)
        # …mais un extrait inscrit ne blanchit pas une seconde mention dans la même phrase.
        assert non_jugees(corrige + " GitLab ferme le ticket.", _FORGE, admises)

    def test_le_perimetre_couvre_les_commandes_les_skills_et_le_prompt_de_run(self) -> None:
        perimetre = fichiers_du_perimetre_forge()
        assert len([f for f in perimetre if f.startswith(".claude/commands/")]) >= 17
        assert len([f for f in perimetre if f.endswith("/SKILL.md")]) >= 4
        assert ".claude/settings.json" in perimetre and RUN_SH in perimetre
        assert not [f for f in perimetre if f.startswith(".claude/worktrees/")]

    def test_aucune_mention_de_forge_non_jugee(self) -> None:
        """Le balayage : toute mention de GitLab est inscrite, avec sa raison."""
        fautes = [
            f"{relatif} : {contexte}"
            for relatif in fichiers_du_perimetre_forge()
            for contexte in non_jugees(lire(relatif), _FORGE, FORGE_ADMISES.get(relatif, ()))
        ]
        assert not fautes, (
            "mention(s) de GitLab que personne n'a jugée(s). Au présent : corriger, la forge est "
            "GitHub (#335). Au passé ou vraie au présent : l'inscrire dans FORGE_ADMISES avec sa "
            "raison.\n" + "\n".join(fautes)
        )

    def test_gitlab_ci_yml_n_est_nomme_nulle_part(self) -> None:
        """Le fichier n'existe plus : ni une phrase ni l'inventaire ne peut le nommer."""
        assert non_jugees(_FORGE_FAUTIVES[".claude/commands/ticket-finish.md"], _FORGE)
        assert not [
            a for admises in FORGE_ADMISES.values() for a in admises if _CI_GITLAB in a.extrait
        ]
        nommant = [f for f in fichiers_du_perimetre_forge() if _CI_GITLAB in lire(f)]
        assert not nommant, f"{_CI_GITLAB} nommé dans : {nommant}"

    def test_l_inventaire_ne_couvre_que_ce_qui_existe(self) -> None:
        """Un extrait disparu se retire de l'inventaire — sinon il couvrirait une phrase future."""
        perimes = [
            f"{relatif} : {extrait}"
            for relatif, admises in FORGE_ADMISES.items()
            for extrait in absentes(lire(relatif), admises)
        ]
        assert not perimes, "extrait(s) inscrit(s) introuvable(s) :\n" + "\n".join(perimes)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 3. L'atelier de session contre le scratchpad (#962)
# ─────────────────────────────────────────────────────────────────────────────────────────────────

_SCRATCHPAD = re.compile(r"(?i)scratchpad")

_ECARTE = (
    Admise(
        "ni le scratchpad de session ni `/tmp`",
        "nomme ce qu'il écarte : le scratchpad n'y est cité que pour être refusé (#962)",
    ),
)

#: Les mentions jugées dans les commandes qu'une session de run joue.
SCRATCHPAD_ADMISES: Mapping[str, tuple[Admise, ...]] = {
    ".claude/commands/mr-fix.md": _ECARTE,
    ".claude/commands/ticket-finish.md": _ECARTE,
    ".claude/commands/ticket-ship.md": _ECARTE,
}

#: Les passages que #962 a corrigés, tels qu'ils étaient (`git show 85f42e5^:<fichier>`).
_SCRATCHPAD_FAUTIFS: Mapping[str, str] = {
    ".claude/commands/ticket-ship.md": (
        "Le message passe par un **fichier** : écris-le avec l'outil `Write` dans ton scratchpad "
        'de\n     session — jamais un heredoc, jamais `-m "$(…)"`'
    ),
    ".claude/commands/ticket-finish.md": (
        "**Prépare le fichier de description**, dans ton répertoire de scratchpad de session (ce "
        "n'est\n      pas un livrable, il n'a rien à faire dans le worktree)."
    ),
}

#: Les usages que #962 a laissés en place à dessein : des commandes jouées en interactif seulement,
#: dont le dossier de travail est une stack ou un navigateur jetables hors du dépôt.
_SCRATCHPAD_LEGITIMES = (
    ".claude/commands/milestone-bilan.md",
    ".claude/commands/milestone-presentation.md",
    ".claude/commands/milestone-verdict.md",
    ".claude/skills/verify/SKILL.md",
)

#: Un prompt de session de `run.sh` : le corps d'un heredoc `<<PROMPT`.
_PROMPT_DE_SESSION = re.compile(r"<<PROMPT\n(.*?)\nPROMPT\n", re.DOTALL)
#: Une commande nommée : `/nom` en début de mot — ni `scripts/orchestrate`, ni `.maestro/session`.
_COMMANDE_NOMMEE = re.compile(r"(?<![\w/.-])/([a-z][a-z-]*)")
#: Un jeton des chaînages de docs/10 §7.1 : une commande, `⊇`, « et », ou une fin de chaîne.
_JETON_CHAINAGE = re.compile(r"`/([a-z][a-z-]*)`|(⊇)|(\bet\b)|([,.;→])")


def chainages(texte: str) -> set[tuple[str, str]]:
    """Les paires (appelante, jouée) d'un texte écrit « `/a` ⊇ `/b` ⊇ `/c`, `/d` ⊇ `/e` et `/f` ».

    `⊇` ouvre une chaîne ; « et » ajoute une jouée à la même appelante ; une virgule, un point ou
    une flèche la referment — la flèche de « `/ticket-create` → `/ticket-start` » est justement ce
    qui ne transmet rien.
    """
    paires: set[tuple[str, str]] = set()
    appelante = derniere = None
    for commande, inclut, _et, fin in _JETON_CHAINAGE.findall(texte):
        if commande:
            if appelante:
                paires.add((appelante, commande))
            derniere = commande
        elif inclut:
            appelante = derniere
        elif fin:
            appelante = None
    return paires


def chainages_de_docs_10() -> set[tuple[str, str]]:
    """Les chaînages tels que docs/10 §7.1 les écrit — la source, pas une copie."""
    plat = aplatir(DOCS_10.read_text(encoding="utf-8"))
    debut = plat.index("**chaînages compris**")
    return chainages(plat[debut : plat.index("**un skill garde ses outils**", debut)])


def commandes_nommees_par_le_run() -> set[str]:
    """Les commandes du dépôt que nomment les prompts de session de `run.sh`."""
    return {
        nom
        for prompt in _PROMPT_DE_SESSION.findall(lire(RUN_SH))
        for nom in _COMMANDE_NOMMEE.findall(prompt)
        if (COMMANDES / f"{nom}.md").is_file()
    }


def commandes_jouees_en_run() -> set[str]:
    """Les commandes que nomment les prompts de session de `run.sh`, et celles qu'elles jouent.

    Dérivé, jamais listé : un prompt qui nommerait demain `/milestone-bilan` l'y ferait entrer.
    Une commande citée pour être interdite (`/ticket-abandon`) y entre aussi — sans dommage, la
    règle de l'atelier y vaut autant.
    """
    portee = commandes_nommees_par_le_run()
    paires = chainages_de_docs_10()
    while ajout := {jouee for appelante, jouee in paires if appelante in portee} - portee:
        portee |= ajout
    return portee


class TestAtelierDeSession:
    """Ce qu'une session de run joue écrit ses fichiers de travail dans `.maestro/session/`."""

    def test_le_motif_arrete_les_passages_que_962_a_corriges(self) -> None:
        for relatif, fautif in _SCRATCHPAD_FAUTIFS.items():
            assert non_jugees(fautif, _SCRATCHPAD, SCRATCHPAD_ADMISES.get(relatif, ())), relatif

    def test_le_perimetre_est_derive_des_prompts_de_run(self) -> None:
        """Les quatre prompts sont lus, et la clôture y entre par chaînage.

        `/ticket-finish` n'est nommé par aucun prompt : il y entre parce que `/ticket-ship` le
        joue — c'est ce qui fait de lui l'étape terminale de chaque ticket.
        """
        assert len(_PROMPT_DE_SESSION.findall(lire(RUN_SH))) >= 4
        assert _COMMANDE_NOMMEE.findall("scripts/orchestrate/run.sh, puis /ticket-ship") == [
            "ticket-ship"
        ]
        assert {"ticket-start", "ticket-ship", "mr-fix"} <= commandes_nommees_par_le_run()
        assert "ticket-finish" not in commandes_nommees_par_le_run()
        assert "ticket-finish" in commandes_jouees_en_run()

    def test_les_usages_legitimes_restent_hors_du_perimetre_et_sont_bien_vus(self) -> None:
        """Ce qui les épargne est le périmètre, pas un motif aveugle : le motif, lui, les voit."""
        portee = {f".claude/commands/{nom}.md" for nom in commandes_jouees_en_run()}
        for relatif in _SCRATCHPAD_LEGITIMES:
            assert relatif not in portee, relatif
            assert non_jugees(lire(relatif), _SCRATCHPAD), f"{relatif} n'est plus un témoin"

    def test_aucune_commande_jouee_en_run_ne_prescrit_le_scratchpad(self) -> None:
        perimetre = sorted(f".claude/commands/{nom}.md" for nom in commandes_jouees_en_run())
        fautes = [
            f"{relatif} : {contexte}"
            for relatif in perimetre
            for contexte in non_jugees(
                lire(relatif), _SCRATCHPAD, SCRATCHPAD_ADMISES.get(relatif, ())
            )
        ]
        assert not fautes, (
            "une commande jouée en run nomme le scratchpad : ses fichiers de travail vont dans "
            "`.maestro/session/`, en chemin relatif (#962, prompt de run.sh). S'il n'est cité que "
            "pour être écarté, l'inscrire dans SCRATCHPAD_ADMISES.\n" + "\n".join(fautes)
        )

    def test_l_inventaire_ne_couvre_que_ce_qui_existe(self) -> None:
        perimes = [
            f"{relatif} : {extrait}"
            for relatif, admises in SCRATCHPAD_ADMISES.items()
            for extrait in absentes(lire(relatif), admises)
        ]
        assert not perimes, "extrait(s) inscrit(s) introuvable(s) :\n" + "\n".join(perimes)


# ─────────────────────────────────────────────────────────────────────────────────────────────────
# 4. Les chaînages des `allowed-tools:` (#964)
# ─────────────────────────────────────────────────────────────────────────────────────────────────


def declaration(nom: str) -> set[str]:
    """L'`allowed-tools:` d'une commande, outil par outil (une virgule entre parenthèses reste)."""
    _, entete, _ = (COMMANDES / f"{nom}.md").read_text(encoding="utf-8").split("---", 2)
    for ligne in entete.splitlines():
        if ligne.startswith("allowed-tools:"):
            outils = re.split(r",(?![^(]*\))", ligne.partition(":")[2])
            return {outil.strip() for outil in outils if outil.strip()}
    return set()


def corps(nom: str) -> str:
    return (COMMANDES / f"{nom}.md").read_text(encoding="utf-8").split("---", 2)[2]


class TestChainagesDesDeclarations:
    """Une commande jouée comme étape transmet sa déclaration à celle qui la joue (docs/10 §7.1)."""

    def test_le_lecteur_suit_les_trois_formes_ecrites(self) -> None:
        """Chaîne, « et », et la flèche qui ne transmet rien — sur un texte fabriqué pour ça."""
        texte = (
            "`/a` ⊇ `/b` ⊇ `/c`, `/d` ⊇ `/e` et `/f` (découpage), `/g` ⊇ `/h`. Celle à qui "
            "elle passe la main (`/h` → `/i`) ne transmet rien."
        )
        assert chainages(texte) == {("a", "b"), ("b", "c"), ("d", "e"), ("d", "f"), ("g", "h")}

    def test_docs_10_ecrit_les_chainages_que_964_a_releves(self) -> None:
        assert {
            ("ticket-ship", "ticket-finish"),
            ("ticket-finish", "mr-fix"),
            ("ticket-start", "design-veille"),
            ("ticket-start", "ticket-create"),
            ("milestone-verdict", "ticket-create"),
        } <= chainages_de_docs_10()

    def test_le_manque_se_voit_sur_un_chainage_qui_n_existe_pas(self) -> None:
        """L'échantillon fautif de #964 : `/mr-review` ne contient pas `/ticket-finish`."""
        assert declaration("ticket-finish") - declaration("mr-review")
        assert "Bash(gh:*)" in declaration("mr-review"), "l'en-tête est bien lu, et non vide"

    def test_chaque_chainage_ecrit_existe_dans_l_appelante(self) -> None:
        """Un chaînage que l'appelante ne cite pas est une doc qui décrit un autre dépôt."""
        fantomes = [
            f"/{appelante} ⊇ /{jouee}"
            for appelante, jouee in sorted(chainages_de_docs_10())
            if f"/{jouee}" not in corps(appelante)
        ]
        assert not fantomes, f"chaînage(s) de docs/10 §7.1 sans appel dans la commande : {fantomes}"

    def test_une_commande_jouee_transmet_toute_sa_declaration(self) -> None:
        manques = {
            f"/{appelante} ⊇ /{jouee}": sorted(declaration(jouee) - declaration(appelante))
            for appelante, jouee in sorted(chainages_de_docs_10())
            if declaration(jouee) - declaration(appelante)
        }
        assert not manques, (
            "une commande jouée comme étape déclare ce que son appelante ne déclare pas — "
            f"compléter l'en-tête de l'appelante (docs/10 §7.1) : {manques}"
        )
