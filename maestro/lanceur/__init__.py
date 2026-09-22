"""Le **lanceur du produit** : démarrer et arrêter Maestro d'un seul geste (#640, lot de #637).

    python -m maestro.lanceur                # démarre l'API et le front, puis ouvre le navigateur
    python -m maestro.lanceur --no-browser   # démarre et rend la main sans ouvrir (la coque)
    python -m maestro.lanceur --stop         # arrête, en SOLDANT les runs en vol
    python -m maestro.lanceur --etat         # ce qui tourne ; pour ce qui est mort, sa cause
    python -m maestro.lanceur --diagnostic   # ce qu'il servirait, sans rien démarrer

Une installation l'expose sous le nom `maestro-lanceur` (`[project.scripts]`) ; un
clone l'appelle par `-m`. C'est le **même** code dans les deux cas, et c'est tout
l'objet du lot : EF-41 demande que le produit se lance **sans chaîne de
développement**, et le seul lanceur du dépôt jusqu'ici, `scripts/controltower/
start.sh`, en suppose une de bout en bout — un `bash` (sous Windows, celui de Git,
qui n'est pas installé chez l'utilisateur), un `.venv` à l'emplacement du clone, un
`npm run dev`, le `setup.sh` passé.

## Pourquoi du Python, et pas un second script shell

Parce que le lanceur doit tourner **là où le produit tournera**. Une installation
embarque déjà un runtime Python — c'est le coût caché que [docs/24 §4.6](../../
docs/24-projets-locaux-et-poste-de-travail.md) nomme, et l'API *est* ce runtime.
Un lanceur écrit dans ce runtime n'a donc rien à supposer : le Python qui le joue
est celui qui servira l'API (`sys.executable`), sans venv à deviner ni chemin de
clone à reconstituer. Un script shell, lui, aurait ajouté une dépendance que
l'utilisateur n'a pas — et sous Windows, `bash` nu est le lanceur WSL
(docs/10 §7.0), qui ne voit ni le même système de fichiers ni le même Python.

## Ce qu'il ne fait pas

- **Il n'installe rien** (#641) : ni Python, ni Node, ni les dépendances, ni le
  front. Il suppose un environnement **en place** — et il dit lequel lui manque,
  avec le geste qui le pose, plutôt que de le fabriquer.
- **Il ne construit pas le front.** `next dev` est la chaîne de développement
  elle-même ; le lanceur sert un front **déjà construit** (`next start`, ou le
  serveur autonome qu'une installation embarquera). Sans build, il refuse **en le
  disant** — jamais un repli silencieux sur le serveur de développement.
- **Il ne remplace pas `start.sh`**, qui reste l'outil de développement (rechargement
  à chaud, états du banc, fenêtre isolée à profil jetable). Les deux savent démarrer
  la même stack ; ils ne s'adressent pas à la même personne.

## Les trois promesses du ticket, et où elles vivent

1. **Un geste démarre, et les ports sont choisis ou signalés** (`lanceur.demarrer`).
   Le port de l'**UI** est *choisi* : s'il est pris, le lanceur en prend un libre et
   l'annonce — l'adresse qu'on ouvre est celle qu'il vient de dire, rien d'autre n'en
   dépend. Le port de l'**API**, lui, est *signalé* : l'URL de l'API est **inlinée
   dans le front au build** (`NEXT_PUBLIC_MAESTRO_API_URL`, `apps/web/lib/api.ts`),
   donc la déplacer en silence servirait une interface qui se charge et ne répond à
   rien — la collision silencieuse que le critère interdit. Occupé, il est nommé,
   avec ce qui le tient et les deux gestes qui lèvent le blocage.
   Et **rien n'est tué au jugé** : le lanceur n'arrête que la session qu'il a lui-même
   inscrite. Un port tenu par quelqu'un d'autre se contourne ou se dit, jamais se libère.
2. **L'arrêt est propre et complet** (`lanceur.arreter`). Il **solde les runs en vol**
   avant de libérer quoi que ce soit (`POST /api/extinction`, #486, #700, docs/28 §11) :
   un arrêt volontaire consigne ses runs « annulée » au lieu de les laisser « en cours »
   sans écran pour les suivre. Il éteint ensuite **chaque service avec sa descendance**
   (la leçon de #291, déjà écrite dans `maestro/controltower/hote_detache.py` : tuer un
   père avant ses enfants est ce qui fabrique l'orphelin qu'on veut éviter), puis
   **vérifie** qu'ils sont partis au lieu de le supposer. Un démarrage qui échoue à
   mi-chemin défait ce qu'il a monté par le même chemin — c'est le cas « un des deux
   services n'a pas démarré » du critère.
   ⚠ **Le redémarrage, lui, ne solde pas** : c'est la ligne de partage de #441/#700,
   et elle passe entre *arrêter* et *remplacer la session en place*. La déplacer
   solderait les runs à chaque relance.
3. **Un service qui meurt est signalé avec sa cause** (`lanceur.cause`, `lanceur.etat`).
   Pendant le démarrage, la mort d'un service arrête l'attente **tout de suite** —
   code de sortie, dernières lignes de son journal, chemin du journal. Après, `--etat`
   pose la même question à tout moment. Les journaux vont sous `.maestro/lanceur/`,
   en chemin **relatif** (docs/10 §8.5) : une session autonome doit pouvoir les lire.

## Le contrat avec la coque

`apps/desktop/main.js` passe `--no-browser` au démarrage et `--stop` à la fermeture,
et n'a besoin de rien d'autre (#923). Ces **deux options portent ici le même nom et
le même sens** : le jour où l'empaquetage (#641) remplacera `start.sh` par ce lanceur,
la coque changera d'exécutable et de rien d'autre. Elle n'est pas modifiée par ce lot —
tant que la stack servie est celle d'un clone, `start.sh` et son rechargement à chaud
restent ce qu'elle doit appeler.
"""

from __future__ import annotations

from maestro.lanceur.emplacement import Emplacement, Front
from maestro.lanceur.lanceur import Options, arreter, cause, demarrer, diagnostic, etat
from maestro.lanceur.session import Service, Session
from maestro.lanceur.systeme import Processus, Sortie, Systeme

#: ⚠ La **fonction** `emplacement` n'est pas réexportée ici, à dessein : le paquet
#: porte déjà un **module** de ce nom, et l'exporter le masquerait — `from
#: maestro.lanceur import emplacement` rendrait alors la fonction, et tout ce qui
#: attendait le module échouerait sur un `AttributeError` sans rapport. Elle se prend
#: où elle vit : `from maestro.lanceur.emplacement import emplacement`.
__all__ = [
    "Emplacement",
    "Front",
    "Options",
    "Processus",
    "Service",
    "Session",
    "Sortie",
    "Systeme",
    "arreter",
    "cause",
    "demarrer",
    "diagnostic",
    "etat",
]
