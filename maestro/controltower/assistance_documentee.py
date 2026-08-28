"""Le répondeur documenté de l'assistance (#764, lot 2 de #748).

Le lot 1 (#763) a rendu la **carte** du corpus et l'**extraction** d'une section ; il
ne répondait à personne. Ce module en fait une réponse, et c'est lui qui remplace le
juge : l'assistant ne reconnaît plus la forme d'une phrase (`SUJETS_ASSISTANCE`,
#123/#684), il **lit la documentation du produit** et répond à partir d'elle, en
citant ce sur quoi il s'appuie.

## Deux appels, et pourquoi pas un

Le corpus pèse **561 838 tokens** estimés (mesure du 2026-08-28, 36 fichiers, 639
sections) : le passer au modèle à chaque question est exclu. Sa carte, elle, pèse
**11 869 tokens** — quarante-sept fois moins. D'où la forme :

1. **le choix** — le modèle reçoit la carte et le fil, et rend les identifiants des
   sections qu'il veut lire (`_PROMPT_CHOIX`) ;
2. **la réponse** — il reçoit le *texte* de ces sections-là, et répond à partir
   d'elles seules (`_CONSIGNE_DOCUMENTEE`, sur le cadre de la fiche).

Un seul appel demanderait de deviner les sections **avant** de savoir ce que la
question cherche : c'est exactement le classement lexical que le chantier supprime
(cf. le docstring de `documentation`, « aucun score lexical »). Ici rien n'est
comparé à la question par du code — c'est le modèle qui choisit, sur une carte qu'il
lit en entier.

## Les citations sont construites, jamais recopiées

Le critère demande que « les sections citées soient celles qui lui ont réellement été
passées en contexte ». La seule façon de le tenir est de **ne pas le lui demander** :
le bloc `Sources` est écrit ici, à partir des sections que `selection_sections` a retenues,
c'est-à-dire de ce qui est **effectivement entré** dans le second prompt. Un modèle à
qui l'on demanderait de citer ses sources pourrait en nommer une qu'il n'a pas eue, et
la propriété ne serait plus vérifiable — elle serait espérée.

⚠ Le bloc dit « ce que j'ai lu », jamais « ce sur quoi je me suis appuyé », et
l'écart est assumé : le second est invérifiable de l'extérieur, le premier est un
fait. C'est aussi pourquoi les sections sont citées **même quand la réponse est un
aveu d'ignorance** — « voici ce que j'ai lu, et ça ne répond pas » est une réponse
honnête et contrôlable.

## Ce qui est parsé, et ce qui ne l'est pas

Le premier appel a un **contrat de sortie** : un identifiant par ligne. Le lire est un
parseur de format connu, au même titre que `_verdict_depuis` sur le fil global — et la
résolution qui suit est un test d'appartenance à un index (`CarteDocumentation.section`).
Aucune de ces deux étapes ne juge une **intention humaine** : le texte de l'utilisateur
n'est comparé à rien, nulle part dans ce module. C'est la règle du 2026-08-28, tenue
ici par l'absence de code plutôt que par une garde.

Une ligne qui ne résout aucune section est **écartée sans bruit** : le modèle peut
recopier une clé de travers, et ce n'est pas une panne — c'est une section de moins.
Zéro section retenue n'en est pas une non plus : c'est le modèle qui dit que la
documentation ne porte pas la réponse, et le second appel n'a alors pas lieu.

## Le repli, et ce qu'il n'est pas

Sans fournisseur — la démo #65, un poste non configuré — le canal **répond quand
même** : `RepondeurAssistance`, le déterministe de #123, reste en repli. Et
l'indisponibilité **se dit dans le fil** plutôt que de lever un 502, conduite reprise
de #686 sur le fil global.

La **famille** de la cause se lit à l'**endroit** de l'échec, jamais à son texte :
résoudre le fournisseur ne touche aucun réseau, donc ce qui casse là est un réglage ;
`generate` part dehors, donc ce qui casse là est une indisponibilité. C'est la règle
de `controltower.causes` tenue d'un cran plus haut, par la structure.

⚠ **Aucun lexique ne prend le relais.** Le déterministe sert de repli **tel quel** :
on ne lui ajoute pas d'entrées pour couvrir ce que le modèle aurait su faire, et il
n'est jamais consulté sur le chemin nominal. Un juge de secours moins bon que le
titulaire, activé quand personne ne regarde, est la pire des combinaisons.

⚠ Et le repli **ne cite rien**. Il n'a rien lu — annoncer des sources sur une réponse
de table serait la seule façon de rendre la citation menteuse.

## Pourquoi `generate` et non `generate_stream`

Le canal doit pouvoir **remplacer entièrement** sa réponse par celle du repli quand le
modèle lâche. Un flux déjà diffusé l'interdit : l'utilisateur a du texte sous les yeux,
et #693 dit qu'on le lui nomme au lieu de le masquer. Un répondeur qui doit pouvoir se
replier ne peut donc pas avoir commencé à écrire — d'où l'aller simple, et
l'implémentation par défaut de `produire`, qui publie la réponse en un incrément.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from maestro.agents.catalog import Agent
from maestro.controltower.assistance import RepondeurAssistance
from maestro.controltower.causes import cause_lisible
from maestro.controltower.chat import MessageChat, RepondeurChat, transcription
from maestro.controltower.documentation import (
    CarteDocumentation,
    CarteTropGrande,
    SectionDoc,
    carte_documentation,
)
from maestro.providers.base import ModelProvider
from maestro.sources.extraction import estimer_tokens

#: Le nombre de sections que le second appel peut recevoir. C'est une **décision** et
#: non une mesure : le budget ci-dessous borne déjà le coût, ce plafond borne la
#: *dispersion* — sans lui, cinquante sections minuscules tiendraient dans le budget
#: et rendraient un contexte que personne n'a choisi de rendre. Six est la borne haute
#: du nombre d'écrans qu'une question d'aide croise.
SECTIONS_MAX = 6

#: Ce que les sections retenues peuvent peser, **en tokens estimés**. Mesuré sur le
#: corpus du 2026-08-28 (639 sections, moyenne 879, p90 1 819, p95 2 782) :
#:
#: - six sections au p90 pèsent **10 914** — le cas ordinaire passe avec le double de
#:   marge, plafond de six atteint ;
#: - la **plus grosse section du corpus** (14 301 tokens,
#:   `docs/05-interface-control-tower.md#2.4 …`) **tient seule**, et c'est ce qui
#:   décide du chiffre : une question qui porte précisément sur elle ne doit pas se
#:   voir refuser la seule section qui y répond ;
#: - elle tient même **accompagnée** de cinq sections moyennes (18 696).
#:
#: Au pire, une question coûte donc la carte (11 869) plus ce budget, soit ~36 000
#: tokens — quinze fois moins que le corpus entier.
BUDGET_SECTIONS_TOKENS = 24_000

#: Le cadre du **premier** appel : choisir, et rien d'autre. Il ne porte pas
#: l'identité de l'assistant (c'est le second qui répond à l'utilisateur) mais un
#: contrat de sortie, et il le porte seul — deux consignes dans un prompt de tri
#: rendent un tri commenté.
_PROMPT_CHOIX = f"""\
Tu prépares la réponse de l'assistant de la Control Tower de Maestro. On te donne la
CARTE de la documentation du produit — un bloc par fichier, une ligne par section,
l'indentation donnant la hiérarchie des titres — puis le fil de conversation.

Ta seule tâche : choisir les sections à lire pour répondre au dernier message de
l'utilisateur. Tu ne réponds pas à sa question ici.

Rends la liste des identifiants choisis, UN PAR LIGNE, recopiés exactement comme la
carte les écrit (`<fichier>#<titre>`). Rien d'autre : ni phrase, ni puce, ni
numérotation, ni commentaire. Au plus {SECTIONS_MAX} identifiants, du plus utile au
moins utile.

Si la carte ne porte rien qui réponde au message, rends une liste vide."""

#: Ce qui s'ajoute au cadre de la fiche pour le **second** appel. Il dit les deux
#: choses que le cadre ne peut pas dire, parce qu'elles ne valent que pour ce
#: répondeur-ci : d'où vient la matière, et qui écrit les sources.
_CONSIGNE_DOCUMENTEE = """\
Pour cette réponse, tu disposes d'extraits de la documentation de Maestro, reproduits
ci-dessous. Réponds à partir d'EUX SEULS : ils font foi, y compris contre ce que tu
crois savoir du produit. Si ce qu'ils portent ne suffit pas à répondre, dis-le
franchement et dis ce qui manque — ne comble aucun trou.

N'écris pas toi-même la liste des sources : elle est ajoutée automatiquement sous ta
réponse, à partir des extraits qui t'ont réellement été passés. Tu peux en revanche
nommer une section dans ta phrase quand cela aide à lire."""

#: L'en-tête du bloc de citations. Le mot dit **ce que l'assistant a lu**, et non ce
#: qu'il a utilisé : c'est la seule des deux affirmations qui soit vérifiable.
_TITRE_SOURCES = "Sources lues :"

#: La réponse quand le modèle n'a retenu **aucune** section : la documentation ne
#: porte pas de quoi répondre, et on le dit. Ce n'est pas un repli — le modèle a
#: répondu, et sa réponse est « rien ici » — d'où une phrase et non une table.
_AVEU_IGNORANCE = (
    "Je n'ai rien trouvé dans la documentation de Maestro (les fichiers de `docs/` et "
    "`apps/web/README.md`) qui réponde à votre message, et je préfère vous le dire "
    "plutôt que d'improviser. Reformulez-le si le sujet a un autre nom dans le "
    "produit ; s'il porte sur le contenu de votre projet plutôt que sur l'outil, "
    "c'est la page Chat qui vous met en relation avec l'orchestration."
)

#: Ce que le canal dit quand il n'a pas pu lire la documentation. Même ordre qu'à
#: #686 — la cause, ce qui n'a pas eu lieu, le geste qui répare — et le morceau du
#: milieu porte le critère : dire « je ne peux pas » sans dire « je n'ai donc pas de
#: sources » laisserait prendre la réponse de repli pour une réponse documentée.
_PHRASE_REPLI = (
    "Je ne peux pas consulter la documentation pour l'instant : {cause}. "
    "Voici ce que l'aide intégrée sait en dire — sans source, et sans avoir lu la "
    "documentation. {reparation}"
)

#: Le fournisseur est configuré mais n'a rien rendu : panne passagère, on réessaie.
_REPARATION_PASSAGERE = (
    "Reposez votre question quand le fournisseur de modèle répondra à nouveau, et "
    "j'irai la chercher dans la documentation."
)

#: Rien ne répondra tant que le réglage n'est pas posé. Les réglages sont **nommés**
#: parce qu'ici celui qui lit est celui qui répare — une Control Tower locale n'a pas
#: d'exploitant à qui transmettre. Reprise de #686, dont c'est la moitié qui vaut
#: pour tout canal servi par un modèle.
_REPARATION_CONFIGURATION = (
    "Ce n'est pas une panne passagère mais un réglage absent : renseignez le "
    "fournisseur de modèle (MAESTRO_PROVIDER et ses identifiants) dans la "
    "configuration, et je pourrai lire la documentation."
)

#: Le corpus a débordé le budget de sa carte (`CarteTropGrande`). Ni un réglage de
#: l'utilisateur ni une panne du fournisseur : un défaut **du produit**, qu'on ne lui
#: demande donc pas de réparer — le dire évite de le faire chercher.
_REPARATION_CORPUS = (
    "Ce n'est rien que vous puissiez corriger : la documentation du produit a dépassé "
    "la taille qu'elle peut occuper, et cela se répare dans Maestro."
)


class _ModeleInjoignable(RuntimeError):
    """Le modèle n'a rien rendu, et ce que le fil doit en dire (#686, #764).

    Une exception plutôt qu'un retour vide : les deux appels peuvent échouer, à trois
    endroits, et un code de retour qu'un `if` oublierait laisserait passer une réponse
    sans documentation en la faisant passer pour documentée.

    Elle ne sort jamais du module : `repondre` la rattrape, et rend la phrase de repli
    suivie de la réponse déterministe. La laisser remonter la ferait traduire en
    `ReponseIndisponible`, donc en 502 — ce que le critère 3 interdit.
    """

    def __init__(self, cause: str, reparation: str) -> None:
        super().__init__(_PHRASE_REPLI.format(cause=cause, reparation=reparation))


@dataclass(frozen=True)
class Selection:
    """Ce qui entre dans le second prompt, et ce qui n'y entre pas.

    `retenues` est **exactement** ce que le modèle recevra, donc exactement ce que le
    bloc `Sources` citera : les deux se lisent au même endroit, ce qui rend le critère
    2 vrai par construction plutôt que par vigilance.

    `ecartees` porte les sections que le modèle a demandées et que le budget ou le
    plafond a refusées. Elles sont **nommées** dans le prompt : un modèle qui sait
    qu'une section lui manque peut en tenir compte, là où l'omission lui ferait croire
    qu'il a tout lu.

    `inconnues` porte les identifiants qui ne résolvent rien — une clé recopiée de
    travers. Elles ne vont **pas** au prompt : le modèle n'apprendrait rien d'utile
    d'apprendre qu'il s'est trompé de nom, et le lui dire l'inviterait à s'expliquer
    plutôt qu'à répondre. Elles servent aux tests et à qui relit.
    """

    retenues: tuple[SectionDoc, ...] = ()
    ecartees: tuple[SectionDoc, ...] = ()
    inconnues: tuple[str, ...] = ()


def identifiants_choisis(texte: str) -> tuple[str, ...]:
    """Les identifiants de sections lus dans la réponse du premier appel.

    Un parseur du format demandé (`_PROMPT_CHOIX`), et rien de plus. Trois tolérances,
    qui couvrent ce qu'un modèle ajoute quand on lui demande une liste nue : une
    barrière de bloc de code, une puce, une numérotation.

    Ce qui **fait** un identifiant est le `#` qui sépare le fichier du titre : une
    ligne qui n'en porte pas n'en est pas un. C'est ce qui rend la liste vide gratuite
    — « aucune », « rien dans la documentation », une ligne blanche, tout cela ne
    porte pas de `#` et ne devient donc pas une section. Aucun mot n'est reconnu : il
    n'y a pas de sentinelle à tenir d'accord avec le prompt.

    L'ordre du modèle est **conservé** (« du plus utile au moins utile ») : c'est lui
    qui décide de ce que le budget garde. Les doublons partent, la première place
    gagnant.
    """
    vus: set[str] = set()
    trouves: list[str] = []
    for ligne in (texte or "").splitlines():
        candidat = ligne.strip()
        if candidat.startswith("```") or candidat.startswith("~~~"):
            continue
        candidat = _sans_puce(candidat)
        if "#" not in candidat or candidat.startswith("#"):
            # Un `#` en tête est un titre Markdown, pas un identifiant : le fichier
            # ouvre toujours la clé (`<fichier>#<titre>`).
            continue
        if candidat not in vus:
            vus.add(candidat)
            trouves.append(candidat)
    return tuple(trouves)


def _sans_puce(ligne: str) -> str:
    """La ligne débarrassée de sa puce ou de sa numérotation, et de ses accents graves.

    Rien n'est deviné : on retire des **préfixes de liste** connus, une seule fois, et
    les accents graves dont un modèle entoure volontiers une clé. Un identifiant ne
    commence par aucun de ces caractères — le corpus est un ensemble de chemins.
    """
    for prefixe in ("- ", "* ", "+ "):
        if ligne.startswith(prefixe):
            ligne = ligne[len(prefixe) :]
            break
    else:
        tete, separateur, reste = ligne.partition(". ")
        if separateur and tete.isdigit():
            ligne = reste
    return ligne.strip().strip("`").strip()


def selection_sections(
    carte: CarteDocumentation,
    identifiants: Sequence[str],
    *,
    budget_tokens: int = BUDGET_SECTIONS_TOKENS,
    maximum: int = SECTIONS_MAX,
) -> Selection:
    """Ce que le second appel recevra : les sections nommées, dans la limite du budget.

    Le parcours suit l'ordre du modèle et s'arrête **par section**, jamais au milieu
    de l'une d'elles : rien n'est amputé, pour la raison qui a fait refuser la
    troncature de la carte au lot 1 — un extrait coupé fait répondre sur ce qu'on lui
    a retiré, et l'aveu d'ignorance porterait alors sur une absence fabriquée.

    Une section trop grosse pour le reste du budget est écartée, et **on continue** :
    les suivantes sont plus loin dans l'ordre d'utilité, mais une petite qui tient
    vaut mieux qu'un budget laissé vide. C'est un premier ajustement, pas un
    remplissage optimal — trier par taille servirait ce qui est court, pas ce qui est
    utile.
    """
    retenues: list[SectionDoc] = []
    ecartees: list[SectionDoc] = []
    inconnues: list[str] = []
    vues: set[str] = set()
    reste = budget_tokens
    for identifiant in identifiants:
        section = carte.section(identifiant)
        if section is None:
            inconnues.append(identifiant)
            continue
        if section.identifiant in vues:
            # Deux clés qui résolvent la même section (la casse, un espace) : c'est
            # une seule section, et le budget ne doit la payer qu'une fois.
            continue
        vues.add(section.identifiant)
        texte = carte.textes.get(section.identifiant, "")
        cout = estimer_tokens(texte)
        if len(retenues) >= maximum or cout > reste:
            ecartees.append(section)
            continue
        reste -= cout
        retenues.append(section)
    return Selection(tuple(retenues), tuple(ecartees), tuple(inconnues))


def prompt_reponse(
    carte: CarteDocumentation, selection: Selection, fil: Sequence[MessageChat]
) -> str:
    """Le prompt du second appel : les extraits, puis la conversation.

    Les extraits viennent **en tête** et la transcription ferme, pour la raison écrite
    au fil global (`orchestration._prompt`) : la consigne de réponse termine la
    transcription, et glisser un fait après elle le ferait lire comme une instruction
    de plus.

    Chaque extrait est annoncé par son **chemin complet** et sa ligne — de quoi
    retrouver le passage —, et sa clé courte, qui est ce que le modèle a nommé.
    """
    blocs: list[str] = ["Extraits de la documentation de Maestro :", ""]
    for section in selection.retenues:
        blocs.append(f"--- {section.chemin} ({section.fichier}, ligne {section.ligne})")
        blocs.append(carte.textes.get(section.identifiant, ""))
        blocs.append("")
    if selection.ecartees:
        blocs.append(
            "Ces sections existent mais n'ont pas pu être jointes, faute de place : "
            + ", ".join(section.chemin for section in selection.ecartees)
            + ". Tiens-en compte plutôt que de supposer ce qu'elles disent."
        )
        blocs.append("")
    return "\n".join(blocs) + "\n" + transcription(fil)


def bloc_sources(sections: Sequence[SectionDoc]) -> str:
    """Le bloc de citations — une ligne par section **effectivement passée**.

    Écrit ici et jamais demandé au modèle : c'est ce qui fait que les sources citées
    sont celles qu'il a reçues (cf. le docstring du module). Sans section, pas de
    bloc — un en-tête suivi de rien laisserait croire à une citation perdue.
    """
    if not sections:
        return ""
    lignes = [f"- {section.chemin}" for section in sections]
    return "\n\n".join((_TITRE_SOURCES, "\n".join(lignes)))


class RepondeurAssistanceDocumentee(RepondeurChat):
    """L'assistance servie par le modèle, à partir de la documentation du produit.

    `provider` est le fournisseur des deux appels — résolu **paresseusement** comme
    dans `RepondeurModele` et `RepondeurOrchestration` : construire le répondeur ne
    coûte rien et ne lève aucune erreur de configuration, ce dont `create_app` dépend.

    `repli` est ce qui répond quand le modèle n'est pas joignable — `RepondeurAssistance`
    par défaut, c'est-à-dire le déterministe de #123 **tel quel**.

    `racine` est celle du corpus ; sans elle, celle du dépôt. Les tests s'en servent
    pour monter un corpus à eux, sans quoi ils écriraient leurs attentes contre la
    documentation réelle, qui bouge à chaque ticket.

    Aucun `PlaybookStore` ici, comme au fil global : l'assistant n'est pas au
    catalogue, donc n'a pas de playbook éditable — et le cadre porte un contrat
    (répondre à partir des extraits seuls) qui n'est pas un texte que l'UI doit
    pouvoir réécrire.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        repli: RepondeurChat | None = None,
        racine: Path | str | None = None,
        sections_max: int = SECTIONS_MAX,
        budget_tokens: int = BUDGET_SECTIONS_TOKENS,
    ) -> None:
        self._provider = provider
        self._repli = repli if repli is not None else RepondeurAssistance()
        self._racine = racine
        self._sections_max = sections_max
        self._budget_tokens = budget_tokens

    async def repondre(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """La réponse documentée, ou celle du repli quand le modèle manque.

        Un seul endroit rattrape, et il rattrape **tout** ce qui empêche de lire la
        documentation : c'est ce qui garantit qu'aucun chemin ne rend un 502 là où le
        canal doit rendre une phrase (critère 3).
        """
        try:
            return await self._documentee(agent, fil)
        except _ModeleInjoignable as injoignable:
            return await self._replier(agent, fil, str(injoignable))

    async def _documentee(self, agent: Agent, fil: Sequence[MessageChat]) -> str:
        """Les deux appels, et la citation de ce qui est entré dans le second.

        Le second n'a **pas lieu** quand rien n'a été retenu : le modèle vient de dire
        que la carte ne porte pas la réponse, et lui redemander à vide coûterait un
        appel pour lui faire répéter. L'aveu est alors une phrase à nous — ce qui est
        aussi ce qui le rend éprouvable au lot 3.
        """
        # Le fournisseur **avant** le corpus, et l'ordre porte une décision : sans
        # fournisseur on va replier de toute façon, et analyser 1,58 Mio pour s'en
        # apercevoir ensuite ferait payer la démo (#65) à chaque question. Résoudre,
        # lui, ne coûte rien et ne touche aucun réseau.
        fournisseur = self._fournisseur()
        carte = self._carte()
        choisis = identifiants_choisis(
            await self._appeler(
                fournisseur,
                agent,
                _PROMPT_CHOIX,
                f"{carte.markdown}\n{transcription(fil)}",
            )
        )
        selection = selection_sections(
            carte,
            choisis,
            budget_tokens=self._budget_tokens,
            maximum=self._sections_max,
        )
        if not selection.retenues:
            return _AVEU_IGNORANCE
        reponse = await self._appeler(
            fournisseur,
            agent,
            f"{agent.prompt_systeme}\n\n{_CONSIGNE_DOCUMENTEE}",
            prompt_reponse(carte, selection, fil),
        )
        return "\n\n".join((reponse.strip(), bloc_sources(selection.retenues)))

    async def _replier(self, agent: Agent, fil: Sequence[MessageChat], phrase: str) -> str:
        """La phrase qui dit l'empêchement, puis la réponse du répondeur de repli.

        Les deux, jamais l'une à la place de l'autre : la phrase seule laisserait
        l'utilisateur sans réponse là où l'aide intégrée en a une (démo #65), et la
        réponse seule ferait passer une réponse de table pour une réponse documentée.
        """
        return "\n\n".join((phrase, await self._repli.repondre(agent, fil)))

    def _carte(self) -> CarteDocumentation:
        """La carte du corpus — en cache, refaite quand un fichier change (#763).

        Un corpus qui déborde son budget est un `_ModeleInjoignable` comme les autres :
        du point de vue de l'utilisateur, la documentation est illisible, et la seule
        chose qui change est ce qu'on lui dit d'en faire — rien, c'est au produit de
        se réparer.
        """
        try:
            return carte_documentation(self._racine)
        except CarteTropGrande as echec:
            raise _ModeleInjoignable(
                f"la documentation du produit dépasse la taille lisible en une fois "
                f"({cause_lisible(echec)})",
                _REPARATION_CORPUS,
            ) from echec

    def _fournisseur(self) -> ModelProvider:
        """Le fournisseur des deux appels, résolu au premier usage.

        Ce qui casse **ici** n'a touché aucun réseau : c'est un réglage, et réessayer
        n'y changerait rien — d'où la famille de la cause, lue à l'endroit de l'échec
        et jamais à son texte (règle de `controltower.causes`, tenue par la structure).

        Un échec de résolution **ne se mémorise pas** : `self._provider` reste `None`,
        si bien que la question suivante retente. C'est ce qui rend la phrase de
        réparation vraie — corriger la configuration suffit, sans redémarrer la
        Control Tower.
        """
        if self._provider is None:
            # Import local : ne tire la couche fournisseur (SDK…) qu'au premier
            # message — l'app se construit et se teste sans elle (#84).
            from maestro.providers.factory import provider_from_settings

            try:
                self._provider = provider_from_settings()
            except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
                raise _ModeleInjoignable(
                    f"aucun fournisseur de modèle n'est utilisable ({cause_lisible(echec)})",
                    _REPARATION_CONFIGURATION,
                ) from echec
        return self._provider

    async def _appeler(
        self, fournisseur: ModelProvider, agent: Agent, systeme: str, prompt: str
    ) -> str:
        """Un appel modèle, dont les deux façons d'échouer lèvent `_ModeleInjoignable`.

        `generate` part dehors, donc ce qui casse là est une **indisponibilité** — la
        seconde famille, celle qui se réessaie. Une réponse **vide** y est rangée : un
        modèle qui n'a rien dit n'a rien choisi et n'a rien répondu, et les deux
        appels en tirent la même conclusion.
        """
        try:
            texte = await fournisseur.generate(prompt, model=agent.modele, system_prompt=systeme)
        except Exception as echec:  # noqa: BLE001 — la position classe, cf. docstring
            raise _ModeleInjoignable(
                f"le fournisseur de modèle n'a pas répondu ({cause_lisible(echec)})",
                _REPARATION_PASSAGERE,
            ) from echec
        if not (texte or "").strip():
            raise _ModeleInjoignable(
                "le fournisseur de modèle a rendu une réponse vide",
                _REPARATION_PASSAGERE,
            )
        return texte
