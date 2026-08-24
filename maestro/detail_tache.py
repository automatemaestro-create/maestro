"""Détail d'une tâche : étapes et liens utiles (ticket #246, contrat #183).

Une carte du Kanban ne portait que son titre, son agent, son statut, son coût et
— depuis #187 — son ticket externe. Il manquait de quoi **comprendre** la tâche
sans quitter l'écran : ce qu'elle demande (`description`), où elle en est
(`étapes`) et ce qu'il faut ouvrir pour la traiter (`liens utiles`).

Comme la référence de ticket (#187), ce sont des **données transportées**, jamais
une intégration : une étape est un libellé et un état, un lien est un libellé,
une URL et une **nature** — et c'est la nature, pas le domaine de l'URL, qui dit
au front comment rendre le lien. Deviner « Figma » ou « GitLab » d'après l'hôte
marcherait jusqu'à la première instance auto-hébergée.

Le module est **feuille** — il n'importe de `maestro` que `references`, feuille
lui aussi, pour ne pas redire la validation d'URL — et pour la même raison que
`maestro.references` : ces formes traversent des couches qui ne peuvent pas
dépendre les unes des autres (le journal les transporte, les événements les
diffusent, `controltower` importe déjà `telemetry`). `maestro.controltower.events`
les ré-exporte, comme il ré-exporte `ReferenceTicket`.

Deux règles gouvernent tout ce qui suit, et ce sont celles du contrat :

- **rien ne s'invente** — une étape sans libellé, un lien sans libellé ni URL
  suivable n'apprennent rien et sont écartés plutôt que rendus en blanc ;
- **rien ne se refuse** — la validation ne lève jamais et ne fait pas échouer un
  run : un état inconnu reste tel quel (le front le ramènera à « à faire »), une
  URL de schéma inattendu est jetée en gardant le libellé.

Qui remplit la checklist — l'arbitrage de #489
----------------------------------------------

#246 avait posé le contrat, le journal, l'événement et le panneau ; il restait
une question que le dépôt n'avait pas tranchée, et `consigne_detail` n'était donc
appelé par personne. Trois réponses étaient possibles, et deux ne tiennent pas :

- **l'orchestrateur seul** connaît les étapes d'avance, donc le dénominateur est
  stable — mais personne ne coche : la checklist resterait figée à « à faire »
  du début à la fin, ce qui est exactement ce que le ticket appelle « un contrat
  entièrement plombé et entièrement vide » ;
- **l'agent seul** produit une checklist juste, parce qu'il découvre en
  travaillant — mais une tâche qui n'a pas encore démarré n'a alors **rien** à
  montrer, et un plan illisible tant qu'il ne tourne pas est précisément ce que
  le précédent des outils cités écarte : GitHub Actions déclare ses `steps`
  d'avance, n8n déclare ses nœuds, et c'est la déclaration préalable qui rend la
  vue lisible ;
- **ossature au plan, complétée et cochée par l'agent** — celle qui est retenue.
  Le plan (`Task.etapes`, `task.schema.json`) donne de quoi lire la tâche
  **avant** qu'elle démarre ; l'agent donne la vérité **pendant** qu'elle tourne.
  Chacun répond de ce qu'il sait, et aucun des deux ne répond de ce qu'il ignore.

La couture des deux est `SuiviChecklist`, et son arbitrage propre mérite d'être
dit : le **premier relevé de l'agent supplante l'ossature** au lieu de s'y
apparier par libellé. Apparier serait un pari sur la formulation du modèle —
« Écrire la migration » contre « Rédiger la migration SQL » —, et un pari perdu
ne coûte pas rien : il **double** la checklist d'étapes qui ne se cocheront
jamais, c'est-à-dire qu'il rend le dispositif pire que les deux options pures.
Supplanter est déterministe, et gratuit tant que rien n'est acquis — au premier
relevé, l'ossature est tout entière « à faire ».

Ce que le suivi garantit ensuite, et c'est le troisième critère du ticket : **ce
qui est acquis ne se perd pas**. Un état ne redescend jamais, une étape connue ne
disparaît jamais d'un relevé qui l'oublie. Le **dénominateur, lui, peut
grandir** — un agent découvre en travaillant —, et c'est assumé plutôt que
masqué : l'écran rend une jauge **par étape** (une case gagnée reste gagnée, la
liste s'allonge) au lieu d'un pourcentage qui redescendrait à chaque ajout.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from maestro.references import LONGUEUR_MAX_ID, SCHEMAS_URL

if TYPE_CHECKING:  # pragma: no cover - annotations seulement
    from maestro.telemetry.journal import RunJournal

#: Suffixe de l'étape de journal qui pose le détail d'une tâche en cours
#: d'exécution (cf. `consigne_detail`) — même convention que `:ticket` (#187) ou
#: `:message` (#44) : une annexe rattachée à la tâche `<tache>`, sans usage.
SUFFIXE_ETAPE_DETAIL = ":detail"

#: Avancement d'une étape. Le contrat les traite comme une **chaîne libre** :
#: ces trois valeurs sont celles que le front sait rendre (#251), un état
#: inconnu n'efface pas l'étape — il la rend « à faire », même règle que la
#: colonne « Autres » du Kanban pour un statut de tâche inconnu.
ETAPE_A_FAIRE = "a_faire"
ETAPE_EN_COURS = "en_cours"
ETAPE_FAITE = "faite"

#: Nature d'un lien utile — chaîne libre elle aussi, ces trois valeurs étant
#: celles que le front associe à une icône ; toute autre retombe sur un rendu
#: générique.
LIEN_MAQUETTE = "maquette"
LIEN_TICKET = "ticket"
LIEN_DEPOT = "depot"

#: Longueur au-delà de laquelle un libellé n'est plus une ligne de checklist
#: mais un paragraphe collé par accident. La description, elle, n'est pas
#: bornée : c'est le seul champ dont on attend qu'il soit long.
LONGUEUR_MAX_LIBELLE = 200


def _texte(valeur: Any) -> str:
    """Le texte d'une valeur brute, espaces normalisés (jamais None)."""
    return " ".join(str(valeur or "").split())


@dataclass(frozen=True)
class EtapeTache:
    """Une étape de la tâche : ce qu'il y a à faire, et où on en est (#246).

    `libelle` est l'énoncé de la ligne de checklist, `etat` son avancement —
    l'un des `ETAPE_*`, mais rien n'impose la valeur : un moteur qui en
    rapporterait une autre ne doit pas voir son étape disparaître.
    """

    libelle: str
    etat: str = ETAPE_A_FAIRE

    def to_dict(self) -> dict[str, str]:
        """Réémet l'étape en dict JSON-sérialisable (`{libelle, etat}`)."""
        return {"libelle": self.libelle, "etat": self.etat}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EtapeTache:
        """Reconstruit une étape depuis sa forme `to_dict` (clés absentes → défauts)."""
        return cls(
            libelle=data.get("libelle", ""),
            etat=data.get("etat", ETAPE_A_FAIRE) or ETAPE_A_FAIRE,
        )

    @property
    def vide(self) -> bool:
        """L'étape n'apprend-elle rien (pas d'énoncé) ?

        L'état seul ne fait pas une étape : une case à cocher sans énoncé n'a
        rien à lire et fausserait l'avancement en gonflant le dénominateur.
        """
        return not self.libelle

    def valide(self) -> EtapeTache:
        """Rend l'étape **normalisée** : libellé borné sur une ligne, état en minuscules."""
        return EtapeTache(
            libelle=_texte(self.libelle)[:LONGUEUR_MAX_LIBELLE],
            etat=_texte(self.etat).lower() or ETAPE_A_FAIRE,
        )

    @classmethod
    def depuis(cls, data: Any) -> EtapeTache | None:
        """Construit une étape depuis un dict brut — **None** si elle n'apprend rien.

        Le pendant tolérant de `from_dict`, pour tout ce qui arrive de
        l'extérieur : une ligne de journal relue, un événement JSON, un corps de
        requête.
        """
        if not isinstance(data, Mapping):
            return None
        etape = cls.from_dict(data).valide()
        return None if etape.vide else etape


@dataclass(frozen=True)
class LienUtile:
    """Un lien utile porté par la tâche : maquette, ticket, dépôt (#246).

    Générique par construction, comme `ReferenceTicket` : un `libelle`, une
    `url` et une `nature`. La nature est ce qui permet au front de choisir son
    icône sans inspecter l'URL — le seul champ que ce modèle ajoute à un lien
    ordinaire, et toute sa raison d'être.

    `libelle` peut être vide (le front retombe sur le nom de la nature) et `url`
    aussi (le lien n'est alors qu'une mention) — mais pas les deux.
    """

    libelle: str = ""
    url: str = ""
    nature: str = ""

    def to_dict(self) -> dict[str, str]:
        """Réémet le lien en dict JSON-sérialisable (`{libelle, url, nature}`)."""
        return {"libelle": self.libelle, "url": self.url, "nature": self.nature}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> LienUtile:
        """Reconstruit un lien depuis sa forme `to_dict` (clés absentes → vides)."""
        return cls(
            libelle=data.get("libelle", ""),
            url=data.get("url", ""),
            nature=data.get("nature", ""),
        )

    @property
    def vide(self) -> bool:
        """Le lien n'apprend-il rien (ni libellé, ni URL suivable) ?

        La nature seule ne fait pas un lien : il ne resterait à rendre que
        « Maquette », que l'icône dit déjà.
        """
        return not self.libelle and not self.url

    def valide(self) -> LienUtile:
        """Rend le lien **normalisé** : libellé borné, URL suivable ou jetée.

        Même arbitrage que `ReferenceTicket.valide` — une URL de schéma
        inattendu (`javascript:`, chemin relatif) est **jetée**, le libellé
        restant : un lien mort vaut mieux détruit qu'affiché, et un run ne doit
        pas échouer pour une URL douteuse. Le filtrage est **redit** côté UI
        (`apps/web/lib/liens.ts`, #192) : ce qui arrive par le flux n'est pas
        fiable, et un `href` non filtré exécuterait du code.
        """
        url = str(self.url or "").strip()
        if not url.lower().startswith(SCHEMAS_URL) or any(c.isspace() for c in url):
            url = ""
        return LienUtile(
            libelle=_texte(self.libelle)[:LONGUEUR_MAX_LIBELLE],
            url=url,
            nature=_texte(self.nature).lower()[:LONGUEUR_MAX_ID],
        )

    @classmethod
    def depuis(cls, data: Any) -> LienUtile | None:
        """Construit un lien depuis un dict brut — **None** s'il n'apprend rien."""
        if not isinstance(data, Mapping):
            return None
        lien = cls.from_dict(data).valide()
        return None if lien.vide else lien


def _sequence(data: Any) -> Sequence[Any]:
    """La valeur brute vue comme une séquence, ou une séquence vide.

    Une chaîne **est** une `Sequence` : l'exclure évite d'itérer ses caractères,
    ce qui rendrait autant d'entrées illisibles que de lettres.
    """
    return [] if isinstance(data, str) or not isinstance(data, Sequence) else data


def etapes_depuis(data: Any) -> list[EtapeTache]:
    """Les étapes lisibles d'une valeur brute — **liste vide** quand il n'y en a pas.

    L'ordre est celui du flux : une checklist se lit dans l'ordre où elle a été
    posée, pas trié. Ce qui n'apprend rien est écarté ligne à ligne — une entrée
    illisible ne fait pas perdre les autres.
    """
    lues = (EtapeTache.depuis(brut) for brut in _sequence(data))
    return [etape for etape in lues if etape is not None]


def liens_depuis(data: Any) -> list[LienUtile]:
    """Les liens lisibles d'une valeur brute — **liste vide** quand il n'y en a pas."""
    lus = (LienUtile.depuis(brut) for brut in _sequence(data))
    return [lien for lien in lus if lien is not None]


def etapes_en_liste(etapes: Sequence[EtapeTache]) -> list[dict[str, str]]:
    """La forme JSON d'une liste d'étapes — `[]` quand il n'y en a aucune.

    Le pendant de `ticket_en_dict` (#187) : côté client, une liste vide dit
    « aucune étape » et la vue reste strictement inchangée (#251).
    """
    return [etape.to_dict() for etape in etapes]


def liens_en_liste(liens: Sequence[LienUtile]) -> list[dict[str, str]]:
    """La forme JSON d'une liste de liens utiles — `[]` quand il n'y en a aucun."""
    return [lien.to_dict() for lien in liens]


#: Rang d'avancement des trois états connus — ce qui permet de dire qu'une étape
#: a **progressé**, et donc de refuser qu'elle recule. Un état inconnu n'y figure
#: pas à dessein : on ne sait pas le classer, donc on ne le laisse pas défaire un
#: état connu (cf. `SuiviChecklist`). C'est la seule façon de tenir ensemble les
#: deux règles du contrat — « rien ne se refuse » le fait passer, « la
#: progression ne recule pas » l'empêche d'effacer un acquis.
_RANGS_ETAT: dict[str, int] = {ETAPE_A_FAIRE: 0, ETAPE_EN_COURS: 1, ETAPE_FAITE: 2}


def _cle(libelle: str) -> str:
    """L'identité d'une étape à travers deux relevés : son libellé, casse neutralisée.

    Un agent qui rapporte trois fois sa liste ne réécrit pas ses libellés à la
    virgule près ; l'apparier sur la chaîne exacte ferait apparaître trois étapes
    là où il y en a une. La normalisation reste **minimale** — espaces et casse,
    rien de plus : deux formulations réellement différentes sont deux étapes, et
    c'est la raison pour laquelle l'ossature du plan est *supplantée* plutôt
    qu'appariée (cf. l'arbitrage, en tête de module).
    """
    return " ".join(libelle.split()).casefold()


def _avance(connue: EtapeTache, relevee: EtapeTache) -> EtapeTache:
    """La même étape, avancée du relevé — jamais reculée.

    Garde le **libellé déjà connu** : la clé étant la même à la casse et aux
    espaces près, réécrire le libellé ne changerait rien au fond mais ferait
    scintiller la ligne à l'écran d'un relevé à l'autre.
    """
    rang_connu = _RANGS_ETAT.get(connue.etat)
    rang_releve = _RANGS_ETAT.get(relevee.etat)
    if rang_releve is None:
        # État inconnu : il passe (« rien ne se refuse »), mais seulement là où
        # il n'y a rien à défaire — sur une étape déjà en cours ou faite, on ne
        # saurait pas dire s'il avance ou recule, donc on garde l'acquis.
        return relevee if rang_connu in (None, 0) else connue
    if rang_connu is not None and rang_releve < rang_connu:
        return connue
    return EtapeTache(libelle=connue.libelle, etat=relevee.etat)


class SuiviChecklist:
    """L'état courant de la checklist d'une tâche, de l'ossature au dernier relevé (#489).

    Monté avec l'**ossature** que le plan déclare (libellés seuls, tous « à
    faire ») : c'est ce que la tâche montre avant même d'avoir démarré. Puis
    `rapporte` intègre chaque relevé de l'agent, tel que le fournisseur l'observe
    pendant l'exécution.

    Trois règles, et ce sont les critères du ticket :

    - le **premier relevé supplante l'ossature**. Le plan annonçait une
      intention, l'agent dit ce qu'il fait vraiment ; et comme rien n'est encore
      acquis à cet instant, remplacer ne perd rien. Les relevés suivants, eux, ne
      remplacent plus jamais : ils fusionnent ;
    - **rien ne recule** : un état ne redescend pas (`_avance`), et une étape
      connue qu'un relevé oublie garde sa place et son état plutôt que de
      disparaître. Le numérateur de l'avancement est donc monotone, y compris à
      travers une relance de la tâche — qui repart de zéro côté agent ;
    - **le dénominateur peut grandir** : une étape inédite est ajoutée. C'est le
      prix d'une checklist juste, et il est payé à l'écran (jauge par étape) et
      non ici, en bridant ce que l'agent a le droit de découvrir.

    `rapporte` rend `None` quand la checklist n'a **pas changé** : un agent
    rappelle volontiers sa liste à l'identique, et republier à chaque fois
    coûterait une ligne de journal, un événement de bus et un rendu pour rien.
    """

    def __init__(self, ossature: Sequence[str] = ()) -> None:
        self._etapes: dict[str, EtapeTache] = {}
        # Tant qu'aucun relevé n'est venu, ce qu'on porte est l'annonce du plan —
        # et c'est ce drapeau, pas le contenu, qui dit si le prochain relevé
        # supplante : une ossature vide se remplace aussi bien qu'une pleine.
        self._ossature = True
        for libelle in ossature:
            etape = EtapeTache(libelle=libelle).valide()
            if not etape.vide:
                self._etapes.setdefault(_cle(etape.libelle), etape)

    @property
    def vide(self) -> bool:
        """La checklist n'a-t-elle rien à montrer (règle de #246) ?"""
        return not self._etapes

    def etapes(self) -> list[EtapeTache]:
        """La checklist courante, dans l'ordre où elle se lit."""
        return list(self._etapes.values())

    def rapporte(self, relevees: Sequence[EtapeTache]) -> list[EtapeTache] | None:
        """Intègre un relevé — rend la checklist si elle a **changé**, `None` sinon.

        Un relevé qui n'apprend rien (vide, ou fait entièrement d'étapes sans
        libellé) laisse la checklist intacte, ossature comprise : c'est un agent
        qui n'a rien dit, pas un agent qui annonce n'avoir plus rien à faire.
        """
        lues = [etape for etape in (brute.valide() for brute in relevees) if not etape.vide]
        if not lues:
            return None
        avant = self.etapes()
        if self._ossature:
            self._etapes = {}
            self._ossature = False
        fusion: dict[str, EtapeTache] = {}
        for etape in lues:
            cle = _cle(etape.libelle)
            connue = self._etapes.get(cle)
            fusion[cle] = etape if connue is None else _avance(connue, etape)
        # Ce qu'un relevé oublie n'est pas ce qu'il retire : une étape connue
        # reprend sa place à la suite, avec l'état qu'elle avait.
        for cle, etape in self._etapes.items():
            fusion.setdefault(cle, etape)
        self._etapes = fusion
        apres = self.etapes()
        return apres if apres != avant else None


def consigne_detail(
    journal: RunJournal,
    tache_id: str,
    *,
    description: str = "",
    etapes: Sequence[Any] = (),
    liens: Sequence[Any] = (),
    agent: str = "",
    role: str = "",
) -> None:
    """Pose le détail d'une tâche **en cours d'exécution**, par le journal (#8).

    Le pendant exact de `consigne_ticket` (#187) pour ce qui se lit dans le
    panneau de détail (#251) : l'agent qui découvre en cours de route une étape
    à cocher ou une maquette à ouvrir — typiquement via le serveur MCP de son
    outil (#104) — consigne une étape `<tache>:detail`. La ligne part dans les
    traces comme les autres, le pont Control Tower (#46) la convertit en
    événement `tache.detail` et la projection la pose sur la carte.

    Le journal est le **seul chemin** par lequel ces champs atteignent la
    Control Tower (le pont ne lit que ces lignes), et c'est pourquoi ils
    voyagent par lui plutôt que par le seul plan. Le moteur, lui, n'est jamais au
    courant : il ne voit qu'une étape de plus, sans usage — rien n'a été dépensé
    pour la poser, elle n'entre pas au grand livre.

    Un détail qui n'apprend rien n'est pas consigné : il n'y a rien à montrer.
    """
    # Import local : le module reste une feuille (cf. docstring), le journal
    # dépendant lui-même de ce module.
    from maestro.telemetry.usage import StepUsage

    description = str(description or "").strip()
    etapes_lues = etapes_depuis(list(etapes))
    liens_lus = liens_depuis(list(liens))
    if not tache_id or not (description or etapes_lues or liens_lus):
        return
    journal.consigne(
        etape=f"{tache_id}{SUFFIXE_ETAPE_DETAIL}",
        nom="",
        agent=agent,
        role=role,
        statut="",
        entree="",
        sortie=_resume(description, etapes_lues, liens_lus),
        usage=StepUsage(),
        description=description,
        etapes=etapes_lues,
        liens=liens_lus,
    )


def _resume(description: str, etapes: Sequence[EtapeTache], liens: Sequence[LienUtile]) -> str:
    """Ce que la ligne de journal dit en clair — le fil d'activité n'affiche que ça."""
    parts = []
    if description:
        parts.append("description")
    if etapes:
        parts.append(f"{len(etapes)} étape(s)")
    if liens:
        parts.append(f"{len(liens)} lien(s)")
    return "détail de tâche : " + ", ".join(parts)
