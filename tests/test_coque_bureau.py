"""**La coque de bureau** : sa sûreté, et le cycle de vie des processus (#923,
lot 2 de #921 — docs/35 §2). Tests différés au lot final (#929).

Le ticket dit en toutes lettres ce qui se garde et ce qui ne se garde pas :

> Les tests d'une coque Electron ne demandent **pas** de lancer Electron en CI :
> ce qui se garde est la configuration (`nodeIntegration`, `contextIsolation`,
> l'origine autorisée) et le cycle de vie des processus. Le reste est du ressort
> du filet visuel, pas du pipeline.

D'où la forme de ce fichier : il **lit** `apps/desktop/main.js`,
`apps/desktop/preload.js` et `scripts/controltower/desktop.sh`. C'est une lecture
de source, assumée comme telle — et c'est la seule façon de rendre opposables des
réglages qui ne s'exercent qu'en ouvrant une fenêtre. Chaque sonde est donc
**prouvée sur un échantillon fautif avant de balayer** (méthode de #534, #537,
#539) : sans cette moitié, une extraction mal branchée rendrait « tout va bien »
sur une question jamais posée.

Quatre volets :

① **la sonde** — un extracteur d'objet JavaScript, prouvé sur une configuration
   volontairement dangereuse ;
② **la sûreté de la fenêtre** — les quatre réglages de `webPreferences`, la
   navigation bornée à l'origine locale, et un pont qui n'expose que des verbes
   nommés (jamais `ipcRenderer`, jamais un `invoke(canal, …)` générique) ;
③ **le cycle de vie des processus** — `start.sh` source unique, l'arrêt qui
   attend le démarrage en cours, l'instance unique **par stack** (#1275), aucun argument de la coque
   relayé au lanceur.
   C'est le premier critère d'acceptation : *se ferme sans laisser de processus
   derrière elle* ;
④ **ENF-12** (docs/35 §2.5) — aucun embranchement de code applicatif dans
   `apps/web/**`. Le front teste une **capacité**, jamais sa plateforme, et
   c'est ce qui fait que la même page sert dans un onglet et dans la fenêtre.

⚠ Ce qui n'est **pas** ici : le rendu (skill `relecture-visuelle`), la géométrie
(`/banc-mise-en-page`), et le fait qu'Electron démarre — le job `desktop` de la
CI joue `npm ci` et `node --check` sur ces mêmes sources (#948).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parent.parent
COQUE = RACINE / "apps" / "desktop"
MAIN_JS = COQUE / "main.js"
PRELOAD_JS = COQUE / "preload.js"
DESKTOP_SH = RACINE / "scripts" / "controltower" / "desktop.sh"
START_SH = RACINE / "scripts" / "controltower" / "start.sh"
POSTE_TS = RACINE / "apps" / "web" / "lib" / "poste.ts"


@pytest.fixture(scope="module")
def main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def preload_js() -> str:
    return PRELOAD_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def desktop_sh() -> str:
    return DESKTOP_SH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def start_sh() -> str:
    return START_SH.read_text(encoding="utf-8")


# ==================================================================================================
# ① La sonde, prouvée avant de servir
# ==================================================================================================


#: Le délimiteur fermant de chaque ouvrant — un objet, une liste, un appel.
_FERMANT = {"{": "}", "[": "]", "(": ")"}


def objet(source: str, ouverture: str) -> str:
    """Le corps du littéral qui suit `ouverture`, **délimiteurs appariés**.

    Une expression régulière ne suffirait pas : `webPreferences` contient un
    appel (`path.join(…)`) et des commentaires, et un motif non apparié
    s'arrêterait au premier délimiteur fermant venu — donc au milieu du bloc, en
    rendant « réglage absent » pour un réglage présent.

    Le délimiteur est **déduit** du dernier caractère de `ouverture` plutôt que
    fixé aux accolades : la liste des origines autorisées est un tableau, et
    l'écrire en dur aurait fait de cette sonde une sonde d'objets, pas une sonde
    de sources.
    """
    ouvrant = ouverture[-1]
    fermant = _FERMANT.get(ouvrant)
    if fermant is None:
        raise AssertionError(f"« {ouverture} » n'ouvre aucun littéral")
    debut = source.index(ouverture) + len(ouverture)
    profondeur = 1
    for position in range(debut, len(source)):
        if source[position] == ouvrant:
            profondeur += 1
        elif source[position] == fermant:
            profondeur -= 1
            if profondeur == 0:
                return source[debut:position]
    raise AssertionError(f"« {ouvrant} » jamais refermé après « {ouverture} »")


def reglage(corps: str, nom: str) -> str | None:
    """La valeur d'un réglage `nom: valeur,` — `None` s'il n'y en a pas.

    La valeur va jusqu'au bout de **sa ligne** et non jusqu'à la première
    virgule : `preload: path.join(__dirname, 'preload.js')` en porte une au
    milieu, et s'arrêter là rendrait un chemin tronqué qu'aucune assertion
    n'aurait su lire.

    Rendre `None` plutôt que lever est délibéré : « absent » est un verdict, et
    c'en est un qu'on veut pouvoir affirmer (un réglage de sûreté retiré est la
    panne, pas une erreur de lecture).
    """
    trouve = re.search(rf"^\s*{re.escape(nom)}:\s*(.+?),?\s*$", corps, re.MULTILINE)
    return trouve.group(1).strip() if trouve else None


#: Une fenêtre volontairement dangereuse : Node ouvert à la page, isolation
#: levée, et pas de bac à sable. Rien de tout cela n'est dans le dépôt — c'est
#: l'échantillon fautif, et c'est ce que la sonde doit savoir dire.
ECHANTILLON_FAUTIF = """
  fenetre = new BrowserWindow({
    width: 1440,
    webPreferences: {
      nodeIntegration: true,
      contextIsolation: false,
      preload: path.join(__dirname, 'preload.js'),
    },
  });
"""


def test_la_sonde_lit_ce_qui_est_ecrit_et_non_ce_qu_on_espere():
    corps = objet(ECHANTILLON_FAUTIF, "webPreferences: {")

    assert reglage(corps, "nodeIntegration") == "true"
    assert reglage(corps, "contextIsolation") == "false"
    # Et un réglage **absent** se dit absent : sans cette moitié, un `sandbox`
    # retiré passerait pour « je n'ai pas su lire ».
    assert reglage(corps, "sandbox") is None


def test_la_sonde_apparie_les_delimiteurs_et_ne_s_arrete_pas_au_premier():
    """L'autre moitié : un littéral qui en contient un autre se lit entier.

    Sans l'appariement, la lecture s'arrêterait à `}` de `interne` et `dehors`
    serait déclaré absent — un réglage de sûreté présent, rendu manquant.
    """
    imbrique = "const a = {\n  interne: { deux: 2 },\n  dehors: 3,\n};"

    assert reglage(objet(imbrique, "const a = {"), "dehors") == "3"
    # Et la sonde suit aussi les crochets : la liste des origines en est une.
    assert objet("const s = new Set(['a', 'b']);", "const s = new Set([") == "'a', 'b'"


# ==================================================================================================
# ② La sûreté de la fenêtre (docs/19 — le modèle de menace s'applique)
# ==================================================================================================


@pytest.fixture(scope="module")
def preferences(main_js: str) -> str:
    return objet(main_js, "webPreferences: {")


def test_la_page_n_a_ni_node_ni_contexte_partage_ni_bac_a_sable_leve(preferences):
    """Les trois réglages du troisième critère d'acceptation de #923, plus
    `sandbox` — qui n'y est pas nommé mais va dans le même sens, et qui reste
    **posé** depuis que la coque expose un pont (#928).

    Un preload sandboxé n'a pas accès à Node : il ne peut que `contextBridge`,
    `ipcRenderer` et `webUtils`, ce qui est exactement la surface voulue.
    """
    assert reglage(preferences, "nodeIntegration") == "false"
    assert reglage(preferences, "contextIsolation") == "true"
    assert reglage(preferences, "sandbox") == "true"


def test_la_page_ne_peut_pas_embarquer_une_autre_page(preferences):
    """`webviewTag` : une balise `<webview>` rouvrirait une surface entière que
    ni l'isolation ni le bac à sable ne couvrent."""
    assert reglage(preferences, "webviewTag") == "false"


def test_le_pont_est_celui_du_dossier_de_la_coque(preferences):
    """Le preload est le seul code qui tourne entre la page et le processus
    principal : il vient d'à côté, jamais d'un chemin construit ailleurs."""
    assert reglage(preferences, "preload") == "path.join(__dirname, 'preload.js')"


def test_il_n_y_a_qu_une_seule_fenetre_dans_toute_la_coque(main_js):
    """Une seconde `BrowserWindow` serait une porte : rien ne garantirait
    qu'elle porte les mêmes `webPreferences`, et la sonde ci-dessus ne lit que
    la première."""
    assert main_js.count("new BrowserWindow(") == 1


def test_la_fenetre_ne_quitte_jamais_l_origine_locale(main_js):
    """`will-navigate` : ce qui n'est pas de l'origine locale est **refusé**, et
    ne disparaît pas pour autant — un lien http(s) part au navigateur du
    système, où l'utilisateur l'attend. Tout autre schéma (file:, data:) est
    refusé net."""
    assert "contenu.on('will-navigate'" in main_js
    assert "evenement.preventDefault()" in main_js
    assert "ORIGINES_LOCALES.has(origine)" in main_js


def test_les_origines_locales_sont_les_deux_noms_de_l_hote_local_sur_le_port_de_l_ui(main_js):
    """L'API n'est **pas** une destination de navigation : elle est appelée par
    la page. Une origine de plus ici serait une surface de plus."""
    origines = objet(main_js, "const ORIGINES_LOCALES = new Set([")
    assert "`http://localhost:${PORT_UI}`" in origines
    assert "`http://127.0.0.1:${PORT_UI}`" in origines
    assert "PORT_API" not in origines


def test_aucune_seconde_fenetre_ne_s_ouvre_par_window_open(main_js):
    """`setWindowOpenHandler` rend `deny` : un `target=_blank` ne fabrique pas
    une fenêtre de coque sans `webPreferences`."""
    handler = objet(main_js, "contenu.setWindowOpenHandler(({ url }) => {")
    assert "action: 'deny'" in handler


def test_l_url_servie_est_celle_que_start_sh_ouvrirait(main_js):
    """« localhost » et non « 127.0.0.1 » : les deux désignent la même stack mais
    pas la même **origine** au sens du navigateur — s'en écarter ferait diverger
    le stockage local de la fenêtre de celui de l'onglet, sans que rien ne le
    dise."""
    assert "const URL_UI = `http://localhost:${PORT_UI}`;" in main_js


# --- Le pont : quatre verbes nommés, et rien derrière un nom neutre ------------------------------


def verbes_du_pont(preload: str) -> list[str]:
    """Les clés exposées par `contextBridge.exposeInMainWorld`."""
    corps = objet(preload, "contextBridge.exposeInMainWorld('maestro', {")
    return re.findall(r"^  (\w+):", corps, re.MULTILINE)


def test_le_pont_expose_des_verbes_nommes_et_pas_le_canal_lui_meme(preload_js):
    """⚠ Ne jamais exposer `ipcRenderer` ni un verbe générique du type
    `invoke(canal, …)` : ce serait rouvrir tout le pont derrière un nom neutre,
    et la page est servie par un serveur local qu'un autre programme du poste
    peut atteindre."""
    verbes = verbes_du_pont(preload_js)

    assert verbes == [
        "ouvrirDossier",
        "montrerFichier",
        "choisirDossier",
        "cheminDuFichier",
    ]
    assert "invoke:" not in preload_js
    assert "ipcRenderer," not in objet(
        preload_js, "contextBridge.exposeInMainWorld('maestro', {"
    )


def test_le_front_et_le_pont_connaissent_les_memes_verbes(preload_js):
    """**La frontière.** `apps/web/lib/poste.ts` déclare `window.maestro` de son
    côté, et cette déclaration est ce que le front teste avant d'appeler. Les
    deux listes sont écrites dans deux dépôts d'idées différents (la coque, le
    front) : divergentes, le front testerait une capacité que personne n'expose,
    ou n'appellerait jamais celle qui est là. Ni le lint ni le build ne le
    verraient.
    """
    declaration = objet(POSTE_TS.read_text(encoding="utf-8"), "maestro?: {")
    declares = re.findall(r"^      (\w+)\?:", declaration, re.MULTILINE)

    assert sorted(declares) == sorted(verbes_du_pont(preload_js))


def test_chaque_canal_du_pont_est_traite_par_le_processus_principal(preload_js, main_js):
    """L'autre moitié de la même frontière, un cran plus bas : un canal invoqué
    sans `ipcMain.handle` en face laisserait la page sur une promesse rejetée,
    et le repli silencieux du preload (`.catch`) la rendrait indiscernable d'un
    refus légitime."""
    invoques = set(re.findall(r"ipcRenderer\s*\.?\s*invoke\(\s*'([^']+)'", preload_js))
    traites = set(re.findall(r"ipcMain\.handle\('([^']+)'", main_js))

    assert invoques != set(), "le preload n'invoque plus aucun canal"
    assert invoques == traites


def test_le_pont_n_ouvre_que_des_repertoires_existants(main_js):
    """`shell.openPath` ouvre un fichier avec son application par défaut : sur un
    `.exe`, un `.bat` ou un `.lnk`, cela revient à l'**exécuter**. Les trois
    gardes viennent donc **avant** l'ouverture, et l'ordre est le fond du
    contrôle — les écrire après ne garderait rien."""
    corps = objet(main_js, "async function ouvrirDossier(chemin) {")

    for garde in ("path.isAbsolute(cible)", "fs.statSync(cible)", "etat.isDirectory()"):
        assert garde in corps, garde
        assert corps.index(garde) < corps.index("shell.openPath(cible)"), garde


def test_le_verbe_qui_montre_un_fichier_ne_l_execute_jamais(main_js):
    """La borne d'`ouvrirDossier` est **levée sans lever le risque** (#1224).

    Ce verbe accepte un fichier, là où l'autre le refuse — et il le peut parce
    qu'il ne fait pas la même chose : `shell.showItemInFolder` *sélectionne* la
    cible dans l'explorateur, quand `shell.openPath` la *lance*. Le contrôle
    tient en deux moitiés indissociables : l'ouvreur employé, et les gardes qui
    le précèdent.

    ⚠ Le motif est prouvé sur un échantillon fautif : le corps d'`ouvrirDossier`,
    lui, **ne** contient **pas** `showItemInFolder` — sans quoi ce test serait
    vert sur n'importe quel fichier du dépôt qui nomme le bon symbole.
    """
    corps = objet(main_js, "function montrerFichier(chemin) {")

    assert "shell.showItemInFolder(cible)" in corps
    assert "shell.openPath" not in corps
    for garde in ("path.isAbsolute(cible)", "fs.statSync(cible)", "etat.isFile()"):
        assert garde in corps, garde
        assert corps.index(garde) < corps.index("shell.showItemInFolder(cible)"), garde
    assert "showItemInFolder" not in objet(main_js, "async function ouvrirDossier(chemin) {")


def test_la_coque_ne_juge_jamais_si_un_chemin_est_declarable(main_js):
    """Le chemin rendu par le dialogue est celui de l'OS, **non canonicalisé** :
    c'est `POST /api/projets/racine` qui porte les frontières d'EF-38 (#221).
    Une seconde validation ici ferait deux formules à tenir d'accord, et c'est
    la garde qui perdrait."""
    corps = objet(main_js, "async function choisirDossier(depart) {")

    assert "realpath" not in corps
    assert "EF-38" not in corps


# ==================================================================================================
# ③ Le cycle de vie des processus (premier critère d'acceptation de #923)
# ==================================================================================================


def test_la_coque_ne_lance_que_le_lanceur_du_depot(main_js):
    """`start.sh` est la **source unique** du démarrage et de l'arrêt : préflight
    Redis, remplacement de la session en place, ports dérivés du worktree, et
    surtout le **soldage des runs en vol** (#700). Rejouer ces gestes ici en
    ferait deux à tenir d'accord, et le second oublierait ce point-là en
    premier."""
    assert "const LANCEUR = path.join(RACINE, 'scripts', 'controltower', 'start.sh');" in main_js
    # Un seul `spawn`, et il vise le lanceur : aucun autre processus n'est
    # ouvert par la coque.
    assert main_js.count("spawn(") == 1
    assert "spawn(BASH, [LANCEUR, ...args]" in main_js


def test_les_deux_seuls_jeux_d_arguments_sont_le_demarrage_et_l_arret(main_js):
    """Ni un troisième verbe, ni une option ajoutée : ouvrir la fenêtre démarre,
    la fermer arrête."""
    appels = set(re.findall(r"jouerLanceur\((\[[^\]]*\])\)", main_js))

    assert appels == {"['--no-browser']", "['--stop']"}


def test_aucun_argument_de_la_coque_ne_passe_au_lanceur(main_js):
    """Ce processus reçoit **aussi** les arguments d'Electron : en laisser passer un
    enverrait `start.sh` sortir en « Option inconnue ». La seule option qu'on relayait,
    `--demo`, est partie avec le mode démo (#1168) : il n'y a plus rien à relayer, donc
    plus de liste blanche — la coque ne lit pas son `argv`."""
    assert "process.argv" not in main_js
    assert "--demo" not in main_js


def test_le_lanceur_de_la_coque_refuse_le_mode_demo_en_le_disant(desktop_sh):
    """La coque ne relaie plus rien : sans ce refus, `desktop.sh --demo` ouvrirait en
    silence la stack réelle, et on croirait regarder le scénario factice (#1168)."""
    assert "--demo | --demonstration)" in desktop_sh
    assert "le mode démo a quitté le produit" in desktop_sh


def test_fermer_la_fenetre_arrete_la_stack_avant_que_le_processus_ne_meure(main_js):
    """`before-quit` est le seul moment où l'on peut encore retenir la sortie.
    Sans `preventDefault`, le processus mourrait pendant que `--stop` court, et
    l'API comme l'UI resteraient derrière — le défaut exact que le premier
    critère interdit."""
    corps = objet(main_js, "app.on('before-quit', (evenement) => {")

    assert "evenement.preventDefault();" in corps
    assert "jouerLanceur(['--stop'])" in corps


def test_l_arret_attend_le_demarrage_en_cours_plutot_que_de_le_doubler(main_js):
    """La fenêtre s'ouvre **avant** que la stack ne réponde (c'est la raison
    d'être de l'écran d'attente) : la fermer pendant que Next compile est un
    geste ordinaire. Sans cette attente, `--stop` libérerait des ports que
    `--no-browser` est en train de peupler, et l'ordre du résultat dépendrait de
    qui a fini le premier."""
    corps = objet(main_js, "app.on('before-quit', (evenement) => {")

    assert "demarrageStack" in corps
    assert corps.index("demarrageStack") < corps.index("jouerLanceur(['--stop'])")


def test_l_arret_ne_se_rejoue_jamais(main_js):
    """Un garde (`arretEnCours`) et une sortie par `app.exit` : repasser par
    `app.quit` rejouerait `before-quit`, donc `--stop`, donc une stack arrêtée
    deux fois pendant que la première passe est encore en vol."""
    corps = objet(main_js, "app.on('before-quit', (evenement) => {")

    assert "if (arretEnCours) return;" in corps
    assert "app.exit(" in corps
    assert "app.quit()" not in corps


def test_la_derniere_fenetre_fermee_fait_sortir_l_application(main_js):
    """Sans cela, fermer la fenêtre sous macOS laisserait l'application — donc
    la stack — vivante sans plus rien à l'écran."""
    assert "app.on('window-all-closed', () => app.quit());" in main_js


def test_une_seconde_instance_sort_sans_couper_la_stack_de_la_premiere(main_js):
    """Une seconde fenêtre partagerait la stack de la première et l'arrêterait
    en se fermant. Elle sort donc tout de suite — **avant** que `before-quit` ne
    soit armé, l'arrêt couperait sinon la stack que la première sert."""
    assert "if (!app.requestSingleInstanceLock()) {" in main_js
    sortie = main_js.index("if (!app.requestSingleInstanceLock()) {")
    assert main_js.index("app.exit(0);", sortie) < main_js.index("armerArret();", sortie)


def test_la_seconde_instance_ramene_la_fenetre_deja_ouverte(main_js):
    """Le pendant : sortir en silence laisserait croire que le lancement a
    échoué. L'instance en place est restaurée et reçoit le focus."""
    corps = objet(main_js, "app.on('second-instance', () => {")

    assert "fenetre.restore()" in corps
    assert "fenetre.focus()" in corps


def test_le_verrou_d_instance_unique_se_prend_sur_le_profil_de_la_stack(main_js):
    """#1275 — Electron tient `requestSingleInstanceLock` sur le dossier
    `userData`, qui vaut `%APPDATA%/desktop` pour **toutes** les copies du dépôt
    tant que rien ne le fixe : une fenêtre du clone principal (:3000) refusait
    celle d'un worktree (:3073). Le profil se fixe donc par la stack, et
    **avant** le verrou — fixé après, le verrou serait déjà pris sur le dossier
    commun."""
    assert "const STACK = `${PORT_API}-${PORT_UI}`;" in main_js
    fixe = main_js.index("app.setPath('userData', ")
    assert fixe < main_js.index("if (!app.requestSingleInstanceLock()) {")
    assert "STACK" in objet(main_js[fixe:], "app.setPath(")


def test_la_stack_du_verrou_est_celle_que_start_sh_demarre_et_arrete(main_js, start_sh):
    """La frontière entre la coque et son lanceur : `start.sh` range l'état d'une
    stack (jeton, chien de garde) sous `<api>-<ui>`, et son arrêt libère ces deux
    ports. C'est donc ce couple qu'une seconde fenêtre couperait en se fermant —
    et rien d'autre : deux copies sur les mêmes ports sont **une** stack, une
    copie sur deux couples en porte deux. Réécrite d'un seul côté, la clé
    refuserait une stack voisine, ou laisserait une seconde fenêtre arrêter la
    stack de la première. Les défauts aussi : la stack par défaut de la coque doit
    être celle du lanceur."""
    assert 'ETAT_DIR="${TMPDIR:-/tmp}/maestro-controltower-${PORT_API}-${PORT_UI}"' in start_sh
    assert "const STACK = `${PORT_API}-${PORT_UI}`;" in main_js
    assert 'PORT_API="${MAESTRO_PORT_API:-8000}"' in start_sh
    assert 'PORT_UI="${MAESTRO_PORT_UI:-3000}"' in start_sh
    assert "const PORT_API_DEFAUT = '8000';" in main_js
    assert "const PORT_UI_DEFAUT = '3000';" in main_js


def test_la_stack_par_defaut_garde_le_profil_qu_elle_a_toujours_eu(main_js):
    """Le stockage local de la fenêtre — thème, projet actif, brouillons, guide
    déjà vu, colonne repliée — vit dans le profil. Déplacer celui de la stack par
    défaut l'effacerait sans rien dire à l'ouverture suivante : seule une autre
    stack prend un profil à part."""
    assert "const PORT_UI = process.env.MAESTRO_PORT_UI || PORT_UI_DEFAUT;" in main_js
    assert "const PORT_API = process.env.MAESTRO_PORT_API || PORT_API_DEFAUT;" in main_js
    garde = objet(main_js, "if (STACK !== `${PORT_API_DEFAUT}-${PORT_UI_DEFAUT}`) {")
    assert "app.setPath('userData', " in garde
    assert main_js.count("app.setPath('userData', ") == 1


# --- Le lanceur : ce qu'une coque ne peut pas faire pour elle-même --------------------------------


def test_le_lanceur_designe_a_la_coque_le_bash_qui_l_executera(desktop_sh, main_js):
    """Sous Windows, `bash` nu peut se résoudre en bash WSL — qui ne voit ni le
    même système de fichiers, ni le venv du dépôt. Le nom de la variable est une
    **frontière** entre le script et la coque : renommée d'un seul côté, la
    coque retomberait sur le `bash` du PATH sans que rien ne le dise."""
    assert "export MAESTRO_BASH=" in desktop_sh
    assert "process.env.MAESTRO_BASH" in main_js


def test_le_lanceur_retire_la_variable_qui_ferait_demarrer_electron_en_node_pur(desktop_sh):
    """`ELECTRON_RUN_AS_NODE` : posée, pas de fenêtre, et `require('electron')`
    rend le chemin du binaire au lieu du module — donc `app` vaut `undefined` et
    la coque meurt sur un message qui ne nomme pas sa cause. Or VS Code **est**
    une application Electron et pose cette variable pour les processus qu'il
    lance : le lancement le plus probable de ce script est précisément celui qui
    échouait."""
    assert "unset ELECTRON_RUN_AS_NODE" in desktop_sh


def test_le_lanceur_verifie_le_binaire_et_non_le_dossier_qui_le_contient(desktop_sh):
    """La présence de `node_modules/electron/` ne prouve rien : le paquet npm ne
    contient que quelques fichiers JS, et c'est son post-install qui télécharge
    le runtime. Mesuré sur le poste de référence : `npm install` a rendu
    « added 13 packages » sans jamais créer `dist/`."""
    assert "node_modules/electron/dist" in desktop_sh
    assert "node_modules/electron/path.txt" in desktop_sh


def test_la_coque_et_le_lanceur_lisent_les_memes_variables_de_port(main_js):
    """`worktree.sh ensure` pose ces variables par worktree (#152), sans quoi
    deux sessions se disputeraient la fenêtre et les ports."""
    assert "process.env.MAESTRO_PORT_UI" in main_js
    assert "process.env.MAESTRO_PORT_API" in main_js


# --- Le skill qui la lance (#1273) ----------------------------------------------------------------
#
# Une session qui veut « lancer Maestro » charge le skill `control-tower`. Il la mène à la fenêtre,
# et lui fait guetter sur la sortie de la tâche de fond les lignes que la coque imprime : ces
# lignes sont une frontière entre deux textes, et une coque qui en change une ferait guetter à la
# session une ligne qui ne vient plus jamais.

SKILL_CONTROL_TOWER = RACINE / ".claude" / "skills" / "control-tower" / "SKILL.md"

#: Ce qui tient lieu de valeur : `${PORT_UI}` dans la coque, `<port>` dans le skill.
_VALEUR = "◇"


def lignes_imprimees(main: str) -> list[str]:
    """Les lignes `[coque] …` que la coque écrit sur sa sortie, valeurs interpolées neutralisées.

    Un blanc est admis après la parenthèse : une écriture trop longue pour une ligne passe à la
    suivante (le verrou d'instance de #1275), et elle n'en est pas moins imprimée. Une ligne
    composée de plusieurs littéraux ne se lit que par le premier, d'où le `…` du skill.
    """
    litteraux = re.findall(
        r"""(?:process\.(?:stdout|stderr)\.write|retenir)\(\s*(?:'\w+',\s*)?"""
        r"""(['`])(\[coque\][^'`]*)\1""",
        main,
    )
    return [re.sub(r"\$\{[^}]*\}", _VALEUR, texte).removesuffix("\\n") for _, texte in litteraux]


def lignes_guettees(skill: str) -> list[str]:
    """Les lignes `[coque] …` que le skill cite entre accents graves, valeurs neutralisées."""
    return [
        re.sub(r"<[^>]*>", _VALEUR, texte) for texte in re.findall(r"`(\[coque\][^`]*)`", skill)
    ]


def orphelines(guettees: list[str], imprimees: list[str]) -> list[str]:
    """Les lignes guettées qu'aucune ligne imprimée ne rend — un `…` final vaut « commence par »."""
    return [
        ligne
        for ligne in guettees
        if not any(
            imprimee.startswith(ligne.removesuffix("…"))
            if ligne.endswith("…")
            else imprimee == ligne
            for imprimee in imprimees
        )
    ]


def premier_geste(skill: str) -> str:
    """La commande du premier bloc `bash` du skill : celle qu'une session joue d'abord."""
    return re.search(r"```bash\n(.*?)\n```", skill, re.S).group(1).strip()


def test_la_sonde_des_lignes_guettees_voit_une_ligne_que_la_coque_n_imprime_pas(main_js):
    """La moitié fautive d'abord : une ligne inventée, ou une ligne réelle dont on aurait changé un
    mot, est une orpheline — sans quoi le balayage rendrait « tout va bien » sans rien comparer."""
    imprimees = lignes_imprimees(main_js)

    assert "[coque] Maestro — UI :◇ · API :◇" in imprimees
    assert orphelines(["[coque] Maestro — port :◇"], imprimees) == ["[coque] Maestro — port :◇"]
    assert orphelines(["[coque] Maestro est ouvert…"], imprimees) == ["[coque] Maestro est ouvert…"]
    assert orphelines(["[coque] Maestro — UI…"], imprimees) == []


def test_la_sonde_lit_une_ligne_dont_l_ecriture_passe_a_la_ligne():
    """Avant ce correctif, `write(` suivi d'un saut de ligne rendait la ligne invisible : le skill
    ne pouvait plus guetter le refus du verrou d'instance, quoi qu'il en cite."""
    echantillon = (
        "process.stderr.write(\n"
        "  `[coque] Maestro est déjà ouvert sur cette stack (UI :${PORT_UI}) — ` +\n"
        "    'celle-ci se ferme.\\n',\n"
        ");\n"
    )
    assert lignes_imprimees(echantillon) == ["[coque] Maestro est déjà ouvert sur cette stack (UI :◇) — "]


def test_les_lignes_que_le_skill_fait_guetter_sont_celles_que_la_coque_imprime(main_js):
    skill = SKILL_CONTROL_TOWER.read_text(encoding="utf-8")
    guettees = lignes_guettees(skill)

    assert "[coque] Maestro — UI :◇ · API :◇" in guettees, "le skill ne dit plus quoi guetter"
    assert not orphelines(guettees, lignes_imprimees(main_js))


def test_le_premier_geste_du_skill_est_la_fenetre_et_non_l_onglet():
    """Le skill d'avant #1273 ne connaissait que `start.sh` : un onglet de navigateur, reproché deux
    fois. L'échantillon fautif est son premier bloc, tel qu'il était."""
    avant = "Quand on veut **regarder**…\n\n```bash\nbash scripts/controltower/start.sh\n```\n"
    assert premier_geste(avant) != "bash scripts/controltower/desktop.sh"

    geste = premier_geste(SKILL_CONTROL_TOWER.read_text(encoding="utf-8"))
    assert geste == "bash scripts/controltower/desktop.sh"
    assert (RACINE / geste.split()[-1]).is_file()


# ==================================================================================================
# ④ ENF-12 : aucun embranchement de code applicatif (docs/35 §2.5)
# ==================================================================================================

#: Ce qu'un front qui connaît sa coque écrirait. Les motifs cherchent un
#: **usage** et jamais une **mention** : `lib/poste.ts` explique en prose
#: pourquoi il ne fait rien de tout cela, et doit pouvoir continuer. D'où les
#: gardes d'accent grave — la prose du dépôt cite le code entre `backticks`.
USAGES_DE_PLATEFORME = {
    "navigator.userAgent": r"(?<!`)navigator\.userAgent(?!`)",
    "process.versions": r"(?<!`)process\.versions(?!`)",
    "window.electron": r"(?<!`)window\.electron\b(?!`)",
    "if (electron)": r"(?<!`)\bif\s*\(\s*electron\s*\)(?!`)",
    "require('electron')": r"require\(\s*['\"]electron['\"]\s*\)",
    "import … from 'electron'": r"from\s+['\"]electron['\"]",
}


def sources_du_front() -> list[Path]:
    """Les sources du front, `node_modules` et builds exclus."""
    return [
        chemin
        for motif in ("*.ts", "*.tsx")
        for chemin in (RACINE / "apps" / "web").rglob(motif)
        if "node_modules" not in chemin.parts and ".next" not in chemin.parts
    ]


def test_les_motifs_distinguent_l_usage_de_la_mention():
    """Prouver les motifs sur l'échantillon fautif **avant** de balayer — et
    l'échantillon est réel : c'est `lib/poste.ts` d'aujourd'hui, qui nomme les
    trois tests de plateforme qu'il refuse de faire.

    Un motif lâche (« la chaîne est quelque part dans le fichier ») rendrait ce
    fichier-là fautif, on le retirerait du balayage, et le balayage ne dirait
    plus rien du seul fichier qui touche à la coque.
    """
    texte = POSTE_TS.read_text(encoding="utf-8")

    assert "`navigator.userAgent`" in texte, "l'échantillon a changé : poste.ts ne s'explique plus"
    assert "`if (electron)`" in texte
    # Le motif serré ne voit pas ces mentions…
    for nom, motif in USAGES_DE_PLATEFORME.items():
        assert re.search(motif, texte) is None, nom
    # …et il voit l'usage, dès qu'on l'écrit sans ses accents graves.
    faute = texte.replace("`navigator.userAgent`", "navigator.userAgent")
    assert re.search(USAGES_DE_PLATEFORME["navigator.userAgent"], faute) is not None


def test_le_front_ne_sait_jamais_dans_quoi_il_tourne():
    """ENF-12 : *aucun embranchement de code applicatif du type `if (electron)`
    dans `apps/web/**`*. Le front servi dans la fenêtre est celui du mode web,
    au bit près — et le mode web reste de premier ordre (D3)."""
    fautifs = [
        f"{chemin.relative_to(RACINE).as_posix()} — {nom}"
        for chemin in sources_du_front()
        for nom, motif in USAGES_DE_PLATEFORME.items()
        if re.search(motif, chemin.read_text(encoding="utf-8"))
    ]

    assert fautifs == [], "\n".join(fautifs)


def test_le_front_teste_une_capacite_et_jamais_une_plateforme():
    """Le pendant, sans lequel le balayage ci-dessus serait vert sur un front
    qui ne saurait tout simplement rien faire : la question est « puis-je ? »,
    et elle se pose sur la **fonction**, pas sur l'objet — `window.maestro`
    pourrait exister sans le verbe, d'une version de coque à l'autre."""
    texte = POSTE_TS.read_text(encoding="utf-8")
    capacites = re.findall(
        r'typeof window\.maestro\?\.(\w+) === "function"', texte
    )

    assert sorted(capacites) == [
        "cheminDuFichier",
        "choisirDossier",
        "montrerFichier",
        "ouvrirDossier",
    ]


def test_le_mot_electron_ne_sort_pas_du_dossier_de_la_coque():
    """« Ce dossier est le seul endroit où le mot Electron a le droit
    d'exister » (`main.js`). Un import est déjà couvert ci-dessus ; ce qui est
    cherché ici est le paquet lui-même dans les dépendances du front, qui le
    ferait entrer dans le bundle servi au navigateur."""
    paquet = (RACINE / "apps" / "web" / "package.json").read_text(encoding="utf-8")

    assert '"electron"' not in paquet
