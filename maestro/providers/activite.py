"""Ce qu'un agent fait **pendant** qu'il le fait — le geste, et son débit borné (#479).

Le silence d'un run est à la source, pas à l'écran. Un fournisseur consomme le
flux du SDK message par message et **ne publie rien au passage** : les blocs de
texte s'accumulent dans une liste locale, les outils dans une autre, et seul le
message final déclenche un `report_usage`. Entre le début d'une tâche
(`<tache>:debut`, #98) et son issue, **rien n'est émis, quelle que soit la
durée** — une tâche de dix minutes produit dix minutes de silence, et aucun
écran ne peut y remédier : la donnée n'existe pas.

Ce module porte les deux moitiés de la réponse, côté **couche fournisseur** donc
sans rien savoir du journal ni du bus :

- `Geste` : ce qu'on a observé — un appel d'outil avec sa cible, ou un jalon de
  texte. Chaque occurrence, **jamais dédupliquée** : savoir *quels* outils ont
  servi sans savoir combien de fois ni dans quel ordre, c'est exactement ce que
  la déduplication à la source détruisait ;
- `RegulateurActivite` : le **débit borné**. Un agent outillé émet vite, et
  republier tout tel quel noierait le bus comme le flot d'une ligne par appel
  d'outil noyait la console du pilote (#240, `run.sh --verbeux`). Le régulateur
  regroupe ce qui arrive dans une même fenêtre et **le dit** : « 7 gestes ·
  Read×4, Bash×3 — dernier : … ». Ce qui est publié n'est donc pas un
  échantillon muet, c'est un compte rendu qui annonce son propre regroupement.

Pourquoi une fenêtre de temps et non un plafond de gestes : le coût à borner
n'est pas le nombre de gestes mais celui des **publications**, et une
publication est un `PUBLISH` Redis synchrone appelé depuis la boucle asyncio
(c'est déjà le chemin de `<tache>:debut`, `:relance` et `:refus-outil`). Un
plafond de gestes laisserait un agent très bavard publier en rafale ; une
fenêtre garantit au plus une publication par `periode_s` et par tâche, quoi que
fasse l'agent.

Le **premier** geste part tout de suite, avant que la fenêtre ne s'applique :
c'est lui qui remplace le silence par « la tâche a commencé à travailler », et
l'attendre cinq secondes recréerait au démarrage le trou qu'on vient de combler.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic

#: Fenêtre minimale entre deux publications d'activité, par tâche. Cinq secondes
#: est un compromis mesuré sur les deux contraintes du ticket : assez court pour
#: qu'une tâche longue « ne produise plus de silence » (l'écran bouge au moins
#: toutes les cinq secondes tant que l'agent travaille), assez long pour qu'une
#: tâche de dix minutes tienne en ~120 lignes plutôt qu'en plusieurs milliers.
PERIODE_ACTIVITE_S: float = 5.0

#: Longueur maximale d'une cible d'outil conservée (chemin, commande, motif).
#: Un `Bash` porte volontiers une ligne de commande de plusieurs centaines de
#: caractères ; la ligne d'activité est lue à l'écran, pas archivée.
CIBLE_MAX = 120

#: Longueur maximale d'un jalon de texte. Plus court qu'une cible : un bloc de
#: texte du modèle fait des paragraphes, et ce qu'on en veut est de quoi
#: reconnaître où il en est — pas de quoi le relire.
JALON_MAX = 100

#: Les clés d'entrée d'outil qui portent une **cible**, dans l'ordre où on les
#: cherche. L'ordre est le contenu de la décision, et il se lit sur les outils
#: qui portent **plusieurs** de ces clés : `Grep` a un `pattern` et un `path`, et
#: c'est le motif qu'on veut voir — son `path` vaut « . » neuf fois sur dix, donc
#: le préférer rendrait « Grep · . », une ligne qui n'apprend rien. `path` reste
#: donc en fin de liste, comme repli des outils qui n'ont que lui.
#:
#: L'absence de **toutes** ces clés est un cas normal — un outil sans cible
#: existe, et un outil MCP porte les clés de son serveur, que nous ne
#: connaissons pas : le geste se publie alors avec son seul nom.
_CLES_CIBLE: tuple[str, ...] = (
    "file_path",
    "notebook_path",
    "pattern",
    "command",
    "url",
    "query",
    "prompt",
    "path",
    "description",
)


def _tronque(texte: str, maximum: int) -> str:
    """Ramène `texte` à `maximum` caractères, en disant qu'il a été coupé."""
    texte = " ".join(texte.split())
    return texte if len(texte) <= maximum else texte[:maximum].rstrip() + "…"


def cible_depuis(entree: object) -> str:
    """La cible lisible d'un appel d'outil, depuis l'entrée que le SDK en donne.

    « Read » seul n'apprend presque rien ; « Read · maestro/engine/executor.py »
    dit ce que l'agent est en train de regarder. On lit donc l'entrée de l'outil,
    qui est un objet JSON dont les clés dépendent de l'outil (`_CLES_CIBLE`).

    Tolérante par construction : une entrée qui n'est pas un objet, ou dont
    aucune clé connue n'est renseignée, rend une chaîne vide — le geste se
    publiera alors avec son seul nom d'outil. Un outil **MCP** est le cas
    courant de cette branche (ses entrées portent les clés du serveur, que nous
    ne connaissons pas), et il ne doit surtout pas faire échouer l'observation :
    tracer une activité ne doit jamais casser l'activité tracée.
    """
    if not isinstance(entree, dict):
        return ""
    for cle in _CLES_CIBLE:
        valeur = entree.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            return _tronque(valeur, CIBLE_MAX)
    return ""


@dataclass(frozen=True)
class Geste:
    """Un geste observé dans le flux du fournisseur — une occurrence, pas un type.

    `outil` est le nom de l'outil appelé ; il vaut la chaîne vide pour un **jalon
    de texte**, c'est-à-dire un bloc de prose du modèle, dont on ne garde que le
    début (`cible`). Les deux cas voyagent dans le même objet parce qu'ils
    répondent à la même question — « que fait-il en ce moment ? » — et que les
    séparer obligerait le régulateur à tenir deux comptes pour une seule cadence.

    Aucune déduplication : deux `Read` successifs sont deux gestes. C'est le
    point du ticket — la séquence est l'information, et elle était détruite à la
    source.
    """

    outil: str
    cible: str = ""

    @classmethod
    def outil_appele(cls, nom: str, entree: object) -> Geste:
        """Le geste d'un appel d'outil, cible extraite de son entrée."""
        return cls(outil=nom, cible=cible_depuis(entree))

    @classmethod
    def jalon(cls, texte: str) -> Geste:
        """Le geste d'un jalon de texte — le début de ce que le modèle vient de dire."""
        return cls(outil="", cible=_tronque(texte, JALON_MAX))

    def __str__(self) -> str:
        """Le geste en une ligne : « outil · cible », ou le jalon entre guillemets."""
        if not self.outil:
            return f"« {self.cible} »" if self.cible else "réflexion"
        return f"{self.outil} · {self.cible}" if self.cible else self.outil


class RegulateurActivite:
    """Publie ce que l'agent fait, au plus une fois par `periode_s` (#479).

    Reçoit chaque geste (`note`) et n'appelle `publier` qu'aux instants permis
    par la fenêtre ; ce qui s'est produit entre deux publications est **regroupé
    et annoncé comme tel**. `vider` force la publication du reliquat — à appeler
    quand la tâche se termine, sans quoi les derniers gestes d'une tâche courte
    ne seraient jamais dits.

    Le régulateur **ne lève jamais** : ni un geste illisible ni un publieur en
    échec ne doivent casser l'exécution observée. C'est la règle déjà posée pour
    `on_refus` (`maestro.providers.claude._hook_permissions`) et pour le pont
    télémétrie lui-même, qui avale ses erreurs dans `handleError`.

    `horloge` est injectable pour que les tests n'aient pas à dormir : c'est une
    cadence qu'on vérifie, et l'éprouver avec de vraies secondes rendrait la
    suite lente **et** dépendante de la charge de la machine.
    """

    def __init__(
        self,
        publier: Callable[[str], None],
        *,
        periode_s: float = PERIODE_ACTIVITE_S,
        horloge: Callable[[], float] = monotonic,
    ) -> None:
        self._publier = publier
        self._periode_s = max(0.0, periode_s)
        self._horloge = horloge
        self._en_attente: list[Geste] = []
        # `None` et non l'instant de construction : le **premier** geste part
        # sans attendre la fenêtre. Un agent qui met trois secondes à appeler son
        # premier outil ne doit pas en coûter cinq de plus avant que l'écran ne
        # bouge — c'est précisément le silence de départ que ce lot supprime.
        self._dernier: float | None = None

    def note(self, geste: Geste) -> None:
        """Enregistre un geste, et publie si la fenêtre le permet."""
        self._en_attente.append(geste)
        maintenant = self._horloge()
        if self._dernier is not None and maintenant - self._dernier < self._periode_s:
            return
        self._envoyer(maintenant)

    def vider(self) -> None:
        """Publie le reliquat, quelle que soit la fenêtre (fin de tâche)."""
        if self._en_attente:
            self._envoyer(self._horloge())

    def _envoyer(self, maintenant: float) -> None:
        """Compose la salve en attente, la publie, et rouvre la fenêtre."""
        salve, self._en_attente = self._en_attente, []
        self._dernier = maintenant
        try:
            self._publier(resume_salve(salve))
        except Exception:  # noqa: BLE001 — observer ne casse jamais l'observé
            pass


def resume_salve(gestes: list[Geste] | tuple[Geste, ...]) -> str:
    """Ce qu'une salve de gestes **dit** — et qu'elle est une salve.

    Un geste seul se rend tel quel : c'est le cas courant d'un agent qui prend
    son temps, et le préfixer d'un « 1 geste » n'apprendrait rien.

    Plusieurs gestes se rendent **groupés**, et le regroupement est annoncé :
    le compte, la répartition par outil (`Read×4, Bash×3`), puis le **dernier**
    geste en clair. Le dernier et non le premier — c'est lui qui dit où l'agent
    en est maintenant, ce que la ligne est là pour répondre ; le compte et la
    répartition disent ce qu'il vient de traverser.

    C'est la moitié « en le disant » du critère de débit borné : une ligne qui
    tairait son regroupement se lirait comme un geste isolé, et un observateur
    en conclurait que l'agent est huit fois plus lent qu'il ne l'est.
    """
    if not gestes:
        return ""
    if len(gestes) == 1:
        return str(gestes[0])
    comptes = Counter(geste.outil or "texte" for geste in gestes)
    repartition = ", ".join(
        f"{nom}×{n}" if n > 1 else nom for nom, n in comptes.most_common()
    )
    return f"{len(gestes)} gestes · {repartition} — dernier : {gestes[-1]}"
