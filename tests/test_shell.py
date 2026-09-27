"""« Lire une commande comme bash la lit » : le lexique de la dispense et de la portée (#1348).

`maestro.lecture` et `maestro.portee` lisaient le texte d'une commande avec
`shlex`, qui découpe des mots et non une commande. Le passage `20260927-070605`
du banc l'a payé de 42 demandes « commande illisible », et la préparation du test
a montré pire : le saut de ligne se lisait comme un blanc. Cette suite tient le
lecteur qui les remplace, en trois parties :

① ce que bash sépare, ce lecteur le sépare — et ce que bash tient ensemble
  (guillemets, échappement, ligne continuée), il le tient ensemble ;
② ce que le texte exécute en plus de sa commande — substitutions, heredocs,
  blocs — est rendu, pour être jugé ;
③ ce qu'il refuse, avec sa raison.
"""

from __future__ import annotations

import pytest

from maestro.shell import (
    SUBSTITUTION,
    TILDE,
    VARIABLE,
    Boucle,
    Groupe,
    Illisible,
    Selon,
    Si,
    Simple,
    affectation,
    lis,
)


def _mots(commande: str) -> list[list[str]]:
    """Les commandes simples de premier niveau, en mots rendus."""
    return [
        [mot.rendu() for mot in simple.mots]
        for simple in lis(commande).commandes
        if isinstance(simple, Simple)
    ]


# --- ① Ce qui sépare, ce qui tient ensemble --------------------------------


@pytest.mark.parametrize(
    ("commande", "attendu"),
    [
        ("ls\nrm -rf /", [["ls"], ["rm", "-rf", "/"]]),
        ("a; b && c || d | e", [["a"], ["b"], ["c"], ["d"], ["e"]]),
        ("a & b", [["a"], ["b"]]),
        ("ls \\\n  -la", [["ls", "-la"]]),  # une ligne continuée
        ("echo 'a;b' \"c|d\" e\\&f", [["echo", "a;b", "c|d", "e&f"]]),
        ("find . \\( -name a -o -name b \\) -print",
         [["find", ".", "(", "-name", "a", "-o", "-name", "b", ")", "-print"]]),
        ("ls # un commentaire ; rm x", [["ls"]]),
        ("echo a#b", [["echo", "a#b"]]),  # `#` au milieu d'un mot n'ouvre rien
        ("printf $'a\\tb'", [["printf", "a\tb"]]),
    ],
)  # fmt: skip
def test_ce_que_bash_separe_et_ce_qu_il_tient_ensemble(commande: str, attendu: list) -> None:
    assert _mots(commande) == attendu


def test_les_separateurs_employes_sont_rendus() -> None:
    """La dispense de lecture refuse l'arrière-plan : il faut qu'elle le voie."""
    assert lis("ls & ls").operateurs == frozenset({"&"})
    assert lis("ls && ls | wc").operateurs == frozenset({"&&", "|"})


def test_une_redirection_n_est_pas_un_argument() -> None:
    (simple,) = lis("python app.py 2>/dev/null >> journal.txt 2>&1 < entree").commandes

    assert [mot.rendu() for mot in simple.mots] == ["python", "app.py"]
    assert [(r.descripteur, r.operateur, r.cible.rendu()) for r in simple.redirections] == [
        ("2", ">", "/dev/null"),
        ("", ">>", "journal.txt"),
        ("2", ">&", "1"),
        ("", "<", "entree"),
    ]


def test_un_chiffre_separe_de_sa_redirection_est_un_argument() -> None:
    """`sort f 2 >x` passe `2` à `sort` : le descripteur doit être collé."""
    (simple,) = lis("sort f 2 >x").commandes

    assert [mot.rendu() for mot in simple.mots] == ["sort", "f", "2"]


def test_les_affectations_se_lisent_a_part() -> None:
    (simple,) = lis('T=$(mktemp -d) P="$HOME/x" python app.py').commandes

    assert [nom for nom, _ in simple.affectations] == ["T", "P"]
    assert [mot.rendu() for mot in simple.mots] == ["python", "app.py"]
    assert simple.affectations[0][1].parties[0].genre == SUBSTITUTION


def test_une_affectation_citee_est_un_programme() -> None:
    """`"A=1"` n'affecte rien : le `=` est entre guillemets."""
    (simple,) = lis('"A=1" ls').commandes

    assert simple.affectations == ()
    assert affectation(simple.mots[0]) is None


def test_le_glob_se_voit_hors_des_guillemets_seulement() -> None:
    (simple,) = lis('rm build/* "etoile*" [ab].txt src/{a,b} x').commandes

    assert [mot.glob for mot in simple.mots[1:]] == [True, False, True, True, False]


def test_le_tilde_et_les_variables_restent_des_developpements() -> None:
    (simple,) = lis('cp ~/a "$T/out" ${x:-y} $?').commandes
    genres = [[partie.genre for partie in mot.parties] for mot in simple.mots[1:]]

    assert genres[0][0] == TILDE
    assert genres[1][0] == VARIABLE
    assert simple.mots[3].parties[0].modifiee
    assert simple.mots[4].rendu() == "$?"


# --- ② Ce que le texte exécute d'autre --------------------------------------


def test_une_substitution_est_lue_par_la_meme_grammaire() -> None:
    (simple,) = lis('echo "taille : $(wc -c <"$T/err")" `date`').commandes
    (script_1,) = simple.mots[1].scripts()
    (script_2,) = simple.mots[2].scripts()

    (wc,) = script_1.commandes
    assert [mot.rendu() for mot in wc.mots] == ["wc", "-c"]
    assert wc.redirections[0].operateur == "<"
    assert [mot.rendu() for mot in script_2.commandes[0].mots] == ["date"]


def test_une_substitution_cachee_dans_un_operateur_est_rendue() -> None:
    (simple,) = lis("echo ${x:-$(rm notes.md)}").commandes

    (script,) = simple.mots[1].scripts()
    assert [mot.rendu() for mot in script.commandes[0].mots] == ["rm", "notes.md"]


def test_un_heredoc_cite_est_une_donnee_lue_jusqu_a_son_delimiteur() -> None:
    script = lis("python - <<'EOF'\nimport os; os.remove('x')\nEOF\nls")

    python, ls = script.commandes
    (document,) = python.documents
    assert document.cite and document.corps == "import os; os.remove('x')"
    assert document.scripts == ()
    assert [mot.rendu() for mot in ls.mots] == ["ls"]


def test_un_heredoc_non_cite_rend_ce_qu_il_substitue() -> None:
    (cat,) = lis("cat > f <<EOF\nbonjour $(rm notes.md)\nEOF").commandes

    (document,) = cat.documents
    assert not document.cite
    (script,) = document.scripts
    assert [mot.rendu() for mot in script.commandes[0].mots] == ["rm", "notes.md"]


def test_un_heredoc_que_la_coupe_du_texte_interrompt_court_jusqu_au_bout() -> None:
    (cat,) = lis("cat > f <<'EOF'\nligne 1\nligne 2").commandes

    assert cat.documents[0].corps == "ligne 1\nligne 2"


def test_les_blocs_les_boucles_et_les_conditions_se_lisent() -> None:
    script = lis(
        "{ a; b; } | sort; (cd x && c); "
        'for f in *.md v; do d "$f"; done; '
        'while read p; do if [ -e "$p" ]; then e; elif f; then g; else h; fi; done'
    )
    groupe, sous_shell, pour, tant_que = (
        script.commandes[0],
        script.commandes[2],
        *script.commandes[3:],
    )

    assert isinstance(groupe, Groupe) and not groupe.sous_shell
    assert isinstance(sous_shell, Groupe) and sous_shell.sous_shell
    assert isinstance(pour, Boucle) and pour.variable == "f"
    assert [mot.rendu() for mot in pour.valeurs or ()] == ["*.md", "v"]
    assert isinstance(tant_que, Boucle) and tant_que.condition is not None
    (si,) = tant_que.corps.commandes
    assert isinstance(si, Si) and len(si.branches) == 2 and si.sinon is not None


def test_un_mot_reserve_ne_l_est_qu_en_tete_de_commande() -> None:
    assert _mots("echo done fi then") == [["echo", "done", "fi", "then"]]


def test_double_crochet_garde_ses_operateurs_comme_operandes() -> None:
    assert _mots("[[ $a < $b && -f x ]] && ls") == [
        ["[[", "$a", "<", "$b", "&&", "-f", "x", "]]"],
        ["ls"],
    ]


def test_un_case_rend_chacune_de_ses_branches() -> None:
    """La forme que l'agent de S3 a écrite au passage `20260927-104414` : des
    motifs groupés par `|`, une branche vide, un `case` dans une boucle dans un
    `case`."""
    (selon,) = lis(
        'case "$p" in ./|../) ;; *.py|*/) for w in a; do case "$w" in *) ls;; esac; done;; '
        "(*) rm x ;& esac"
    ).commandes

    assert isinstance(selon, Selon)
    assert selon.sujet.rendu() == "$p"
    assert [len(branche.commandes) for branche in selon.branches] == [0, 1, 1]
    (rm,) = selon.branches[2].commandes
    assert [mot.rendu() for mot in rm.mots] == ["rm", "x"]


# --- ③ Ce qu'il refuse ------------------------------------------------------


@pytest.mark.parametrize(
    "commande",
    [
        "echo 'jamais fermé",
        'echo "jamais fermé',
        "echo $(ls",
        "echo `ls",
        "{ ls",
        "if true; then ls",
        "for x in a; do ls",
        "case $x in a) ls ;;",
        "select x in a; do ls; done",
        "f() { ls; }",
        "a=(1 2)",
        "echo $(( $(rm x) ))",
        "ls )",
        "; ;; ls",
    ],
)
def test_ce_qui_echappe_au_lecteur_leve_avec_sa_raison(commande: str) -> None:
    with pytest.raises(Illisible):
        lis(commande)
