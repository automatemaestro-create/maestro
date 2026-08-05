"""Les secrets du projet de l'utilisateur, couverts par la rédaction (#226, #109).

La rédaction (#109, `maestro.telemetry.redact`) masque trois familles de
secrets : les valeurs d'environnement sensibles **de Maestro**, les motifs de
clés connus (`sk-ant-…`), et les valeurs **servies** aux agents par le coffre.
Ouvrir un projet local en ajoute une quatrième — les secrets **de l'utilisateur**
([docs/24 §2.5](../../docs/24-projets-locaux-et-poste-de-travail.md)) — qu'aucune
des trois n'attrape : le `.env` d'un projet tiers ne porte ni les variables de
Maestro, ni un préfixe reconnaissable, et n'a jamais transité par le coffre.

Ce module comble ce trou avant qu'un agent ne travaille dans le projet : les
gisements que le **périmètre exclut déjà** (`.env`, `**/secrets/**` —
`EXCLUS_DEFAUT`) sont lus **sur l'hôte**, et leurs valeurs enregistrées comme
secrets servis. Elles sont dès lors masquées partout où elles réapparaîtraient —
résumé d'agent, livrable capturé, trace Langfuse.

Le geste peut surprendre : on lit des secrets pour mieux les taire. C'est
exactement ce que fait déjà `_ENV_SENSIBLES` avec l'environnement du poste, et
c'est le seul moyen de masquer une valeur qu'on ne reconnaît pas de vue. Trois
garde-fous l'encadrent :

- **la lecture reste sur l'hôte** — ces fichiers sont précisément ceux que le
  conteneur ne monte pas (`maestro.sandbox.container`) : les connaître pour les
  masquer n'est pas les exposer ;
- **rien n'est conservé** — les valeurs vont dans le registre en mémoire de
  #109, jamais persisté, jamais réémis, consulté seulement pour substituer ;
- **rien n'est journalisé** — la fonction rend un **nombre**, jamais une valeur
  ni un chemin, pour qu'aucun appelant ne puisse en faire une ligne de log.
"""

from __future__ import annotations

from pathlib import Path

from maestro.projets.modele import Projet
from maestro.projets.perimetre import fichiers_exclus
from maestro.telemetry.redact import enregistre_secret

#: Noms et suffixes des fichiers dont on lit les valeurs. Volontairement étroit :
#: hors de cette liste, un fichier exclu est un fichier qu'on ne lit pas — un
#: `node_modules` entier n'a rien à faire dans un registre de secrets.
_NOMS_PORTEURS: tuple[str, ...] = (".env", ".netrc", ".npmrc", ".pypirc", "credentials")
_SUFFIXES_PORTEURS: tuple[str, ...] = (".pem", ".key", ".token", ".secret", ".p12", ".pfx")

#: Le dossier qui rend porteur tout ce qu'il contient (docs/24 §2.5).
_DOSSIER_PORTEUR = "secrets"

#: Bornes de lecture : un registre de secrets n'a pas à absorber un dépôt. Au
#: delà, on s'arrête — la rédaction est une défense en profondeur, pas un
#: inventaire exhaustif, et un `.pem` de 4 Mo n'est pas une valeur à substituer.
_FICHIERS_MAX = 64
_TAILLE_MAX = 64 * 1024
_LIGNE_MAX = 4096
_VALEURS_MAX = 500

#: Longueur minimale d'une valeur enregistrée — même seuil anti-faux-positifs
#: que la rédaction : en deçà, substituer ferait plus de dégâts que de bien
#: (`true`, `8080`, un nom de base de données).
_LONGUEUR_MIN = 8


def enregistre_secrets_du_projet(projet: Projet) -> int:
    """Enregistre les valeurs secrètes lisibles dans `projet` ; rend leur nombre.

    Idempotent (le registre de #109 est un ensemble) et **best-effort** : un
    fichier illisible, un dossier disparu ou une racine absente sont sautés en
    silence. Faire échouer une tâche parce qu'un `.env` est verrouillé serait
    troquer une protection d'appoint contre une panne.

    Ne rend **que** le compte : ni valeur, ni chemin, rien qu'un appelant
    pourrait journaliser.
    """
    racine = projet.racine_chemin
    if not racine.is_dir():
        return 0
    enregistrees = 0
    lus = 0
    for fichier in fichiers_exclus(racine, projet.perimetre):
        if lus >= _FICHIERS_MAX or enregistrees >= _VALEURS_MAX:
            break
        if not _porte_des_secrets(fichier, racine):
            continue
        lus += 1
        for valeur in _valeurs(fichier):
            enregistre_secret(valeur)
            enregistrees += 1
            if enregistrees >= _VALEURS_MAX:
                break
    return enregistrees


def _porte_des_secrets(fichier: Path, racine: Path) -> bool:
    """`fichier` est-il d'un genre dont les valeurs méritent d'être masquées ?

    Trois signes, dans l'ordre où on les rencontre : un **nom** de gisement
    connu (`.env`, `.env.local`, `.npmrc`…), un **suffixe** de matériel
    cryptographique (`.pem`, `.key`…), ou un **dossier `secrets/`** quelque part
    au-dessus — celui que `EXCLUS_DEFAUT` retire d'office.
    """
    nom = fichier.name
    if nom in _NOMS_PORTEURS or nom.startswith(".env"):
        return True
    if fichier.suffix.lower() in _SUFFIXES_PORTEURS:
        return True
    try:
        relatif = fichier.relative_to(racine)
    except ValueError:  # hors racine : on ne lit pas ce qu'on ne borne pas
        return False
    return _DOSSIER_PORTEUR in (partie.lower() for partie in relatif.parts[:-1])


def _valeurs(fichier: Path) -> list[str]:
    """Les valeurs secrètes d'un fichier — côté droit des `CLÉ=valeur`, ou la ligne.

    Deux formes couvrent ce qui traîne dans un projet : le fichier de variables
    (`CLÉ=valeur`, guillemets retirés, `export ` toléré) et le fichier qui **est**
    le secret (un jeton seul, une clé PEM). Dans le doute on prend la ligne
    entière : enregistrer une ligne qui n'était pas secrète ne masque rien de
    plus qu'elle-même, tandis que la manquer laisserait fuir un jeton.
    """
    try:
        if fichier.stat().st_size > _TAILLE_MAX:
            return []
        texte = fichier.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    valeurs: list[str] = []
    for ligne in texte.splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or len(ligne) > _LIGNE_MAX:
            continue
        if ligne.startswith("-----") and ligne.endswith("-----"):
            continue  # les bornes d'un bloc PEM ne sont pas le secret
        _, separateur, apres = ligne.partition("=")
        valeur = apres.strip() if separateur else ligne
        valeur = _sans_guillemets(valeur)
        if len(valeur) >= _LONGUEUR_MIN:
            valeurs.append(valeur)
    return valeurs


def _sans_guillemets(valeur: str) -> str:
    """Retire la paire de guillemets qui entoure une valeur de fichier `.env`."""
    for guillemet in ('"', "'"):
        if len(valeur) >= 2 and valeur.startswith(guillemet) and valeur.endswith(guillemet):
            return valeur[1:-1]
    return valeur
