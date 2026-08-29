"""Génération assistée de la définition d'un agent (#257, lot 5 de #243).

Créer un agent demandait d'écrire soi-même un rôle, des compétences et un
playbook — c'est-à-dire de savoir écrire un playbook. Ce module rend l'autre
entrée : une **intention en une phrase** (« un agent qui relit mes migrations
SQL »), et le modèle propose la définition complète.

## Ce que ce module produit n'est PAS un agent

Rien n'est écrit. `proposer` rend un `DefinitionProposee`, l'endpoint le rend au
formulaire, et c'est l'utilisateur qui crée l'agent — par le même
`POST /api/catalogue` qu'une saisie à la main. C'est le principe des
**propositions de playbook** (#111/#140) appliqué un cran plus tôt : une
suggestion n'est pas une version, rien n'est appliqué sans geste explicite. La
différence avec #139 tient en un mot — là-bas la proposition est *stockée* en
brouillon parce qu'elle porte sur un objet qui existe déjà et qu'on veut pouvoir
la retrouver ; ici l'objet n'existe pas encore, il n'y a donc rien à quoi
rattacher un brouillon persisté, et le brouillon vit dans le formulaire.

## Le format de réponse : des en-têtes, puis le playbook

Quatre champs courts sur une ligne chacun, puis le playbook intégral après
`MARQUEUR_PLAYBOOK` — **le marqueur de #139**, importé et non recopié : c'est la
même question (« le document Markdown commence ici ») et deux marqueurs qui
divergeraient feraient deux formats à tenir d'accord.

Ce n'est **pas** du JSON, et c'est un choix. Le playbook est un document
Markdown multi-ligne, donc le seul champ lourd de la réponse : l'échapper en
chaîne JSON est la partie qu'un modèle rate le plus volontiers, et le dépôt porte
déjà trois parseurs d'objet JSON produit par un modèle
(`orchestration._objet_json`, `orchestrator`, `router.classifier`) — en ajouter un
quatrième pour un contrat à quatre champs plats serait payer un parseur pour un
format plus fragile.

## Le fournisseur suggéré est vrai, ou il n'est pas

Le modèle reçoit le **registre des fournisseurs** (#253) et ne choisit que dedans ;
ce qu'il rend est **reconfronté** au registre avant de sortir d'ici
(`_fournisseur_recevable`). Un fournisseur inventé, ou un modèle hors de la gamme
d'un fournisseur qui n'accepte pas les noms libres, est **écarté** — le champ
reste vide, ce qui veut dire « modèle par défaut des exécutants » et non « ce nom
existe ». C'est la règle de #487 tenue un cran plus haut : *le proposer serait le
seul vrai mensonge possible de cet écran*.

⚠ Et c'est le **registre seul**, jamais la sonde du poste : ce que le générateur
doit garantir est que la suggestion soit **recevable par Maestro**, et c'est le
registre qui le dit. Ce qui est *armé sur cette machine* est déjà répondu, une
fois, par le formulaire où le brouillon atterrit (`GET /api/fournisseurs`, #487 —
le résumé du poste et les deux `datalist`) : le redemander ici ferait deux
endroits qui répondent à « qu'est-ce qui est présent ? », et le premier symptôme
de deux sources est qu'elles se contredisent.

## Un échec laisse le formulaire intact, et le dit

`GenerationIndisponible` couvre les trois manières d'échouer — fournisseur
injoignable (quota, réseau), fournisseur muet, réponse hors contrat — et l'API la
traduit en 502. Aucune n'a d'effet de bord : rien n'est écrit, rien n'est proposé
à moitié. Une proposition **amputée** (sans rôle, sans playbook, sans une seule
compétence) est rangée là plutôt que rendue : le formulaire la refuserait à la
création sans pouvoir dire pourquoi, et « régénérer » est une réponse plus juste
qu'un brouillon qui ne peut pas être enregistré.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT
from maestro.controltower.auto_amelioration import MARQUEUR_PLAYBOOK
from maestro.providers.base import FournisseurDisponible, ModelProvider

#: Ce qu'une intention peut peser. Une phrase, pas un cahier des charges : au-delà
#: ce n'est plus l'entrée que ce module sert, et un texte sans borne est ce par
#: quoi un champ de formulaire devient un canal d'injection de prompt à volume
#: libre. Le refus est **franc** (422) plutôt que tronqué — répondre sur une
#: intention amputée de sa fin serait répondre à côté sans le dire.
INTENTION_MAX = 500

#: Les clés d'en-tête du contrat de réponse, dans l'ordre où le prompt les demande.
#: Chacune tient sur **une ligne** ; le playbook, seul champ multi-ligne, vient
#: après `MARQUEUR_PLAYBOOK`.
CLES_ENTETE = ("NOM", "ROLE", "COMPETENCES", "FOURNISSEUR", "MODELE")

#: Le nom d'agent recevable — **miroir** de `maestro.agents.store._NOM_AGENT`, au
#: même titre que le `SLUG_NOM` du formulaire. Il est ici pour *vérifier* ce que
#: `_slug` vient de produire, jamais pour refuser : un nom hors format est
#: remplacé (voir `_nom_propose`), le nom étant une commodité et non le contrat.
_SLUG_AGENT = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: Le cadre de l'appel : ce qu'on attend, et le format exact de la réponse.
#:
#: Il dit **deux fois** que le playbook doit être intégral, parce que la demande
#: « propose une définition » appelle spontanément un résumé de playbook, qui
#: arriverait dans le formulaire comme un texte à finir plutôt qu'à relire.
_CADRE_GENERATION = f"""\
Tu conçois la définition d'un agent IA pour Maestro, un orchestrateur qui répartit
du travail entre des agents autonomes. On te donne l'intention de l'utilisateur en
une phrase ; tu proposes la définition complète de l'agent qui la sert.

Réponds EXACTEMENT dans ce format, sans rien d'autre — pas d'introduction, pas de
commentaire, pas de bloc de code :

NOM: un identifiant court en minuscules, chiffres, tirets ou soulignés (ex. relecteur-sql)
ROLE: le rôle en quelques mots (ex. Relecteur de migrations SQL)
COMPETENCES: trois à six compétences séparées par des virgules, en minuscules
FOURNISSEUR: le nom exact d'un fournisseur de la liste fournie, ou une ligne vide
MODELE: le nom exact d'un modèle de ce fournisseur, ou une ligne vide
{MARQUEUR_PLAYBOOK}
puis le playbook INTÉGRAL en Markdown, et rien après.

Le playbook est le prompt système de l'agent : il s'adresse à lui au tutoiement,
dit ce qu'il fait, comment il procède, ce qu'il doit rendre et ce qu'il ne doit
jamais faire. Écris-le en entier — c'est le document que l'agent recevra, pas son
résumé. Reste dans le périmètre de l'intention : n'invente ni outil, ni accès, ni
intégration qu'elle ne mentionne pas.

N'écris dans FOURNISSEUR et MODELE que des noms de la liste qui t'est donnée. Si
rien n'y convient, laisse les deux lignes vides : l'agent prendra les réglages par
défaut. Ne devine jamais un nom."""


class GenerationIndisponible(RuntimeError):
    """La proposition n'a pas pu être produite — et rien n'a été écrit.

    Les trois causes sont sous le même toit à dessein : du point de vue de qui a
    cliqué sur « Générer », un quota épuisé, un réseau coupé et un modèle qui
    répond à côté produisent le même fait — pas de proposition, formulaire intact,
    on peut réessayer. L'API la traduit en 502 et le message dit laquelle.
    """


@dataclass(frozen=True)
class DefinitionProposee:
    """Ce que la génération rend : un brouillon de définition, jamais un agent.

    `nom` est une **commodité** — le formulaire l'accepte comme le reste, et il
    est déjà libre au moment où il sort d'ici (`_nom_propose`) —, les trois champs
    du contrat sont `role`, `competences` et `playbook`. `fournisseur` et `modele`
    sont `None` quand rien de recevable n'a été proposé : le formulaire les lit
    alors comme il lit un champ vide, c'est-à-dire « les réglages par défaut ».

    `intention` voyage avec la proposition : c'est ce que l'écran ré-affiche pour
    régénérer, et ce qui rend le brouillon relisable — une définition sans la
    phrase dont elle est née ne se juge pas.
    """

    intention: str
    nom: str
    role: str
    competences: tuple[str, ...]
    playbook: str
    fournisseur: str | None = None
    modele: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """La charge JSON servie par l'API — les champs du formulaire, à l'identique."""
        return {
            "intention": self.intention,
            "nom": self.nom,
            "role": self.role,
            "competences": list(self.competences),
            "playbook": self.playbook,
            "fournisseur": self.fournisseur,
            "modele": self.modele,
        }


class GenerateurDefinitionAgent:
    """Propose une définition d'agent à partir d'une intention (#257).

    `provider` est le fournisseur de l'appel — résolu **paresseusement** comme
    l'analyseur de #139 et le répondeur du chat : construire le générateur ne coûte
    rien et ne lève aucune erreur de configuration, ce dont `create_app` dépend.
    Les tests en injectent un factice.

    `fournisseurs` rend le registre à confronter (le catalogue de #253 par défaut).
    Il est injectable pour la même raison que `provider` : un test doit pouvoir
    décrire le registre auquel il confronte la réponse, sans dépendre de ce que
    l'ordre des imports a enregistré.
    """

    def __init__(
        self,
        *,
        provider: ModelProvider | None = None,
        fournisseurs: Callable[[], tuple[FournisseurDisponible, ...]] | None = None,
        modele: str = MODELE_EXECUTANT_DEFAUT,
    ) -> None:
        self._provider = provider
        self._fournisseurs = fournisseurs
        self._modele = modele

    async def proposer(
        self, intention: str, *, noms_pris: Collection[str] = ()
    ) -> DefinitionProposee:
        """La définition proposée pour `intention` — rien n'est enregistré.

        `noms_pris` sont les noms qu'un agent ne peut pas porter (agents du
        catalogue, acteurs système, personnalisés existants) : le nom proposé est
        rendu libre avant de sortir, pour qu'un « Créer l'agent » cliqué tout de
        suite ne tombe pas sur un 409 dont l'utilisateur n'est pas l'auteur.

        Lève `ValueError` si l'intention est vide ou trop longue (l'appelant en
        tire un 422 : c'est une saisie, pas une panne) et `GenerationIndisponible`
        si le modèle est injoignable, muet, ou répond hors contrat.
        """
        phrase = " ".join(intention.split())
        if not phrase:
            raise ValueError("intention vide : rien à proposer.")
        if len(phrase) > INTENTION_MAX:
            raise ValueError(
                f"intention trop longue ({len(phrase)} caractères, {INTENTION_MAX} au "
                "plus) : décrivez l'agent en une phrase."
            )
        registre = self._registre()
        try:
            texte = await self._generer(_prompt_generation(phrase, registre))
        except Exception as exc:  # noqa: BLE001 — toute panne d'appel est la même ici
            raise GenerationIndisponible(
                f"la génération de la définition a échoué : {exc}"
            ) from exc
        entetes, playbook = _decouper(texte)
        fournisseur, modele = _fournisseur_recevable(
            registre, entetes.get("FOURNISSEUR", ""), entetes.get("MODELE", "")
        )
        return DefinitionProposee(
            intention=phrase,
            nom=_nom_propose(entetes.get("NOM", ""), entetes.get("ROLE", ""), noms_pris),
            role=_role(entetes.get("ROLE", "")),
            competences=_competences(entetes.get("COMPETENCES", "")),
            playbook=playbook,
            fournisseur=fournisseur,
            modele=modele,
        )

    def _registre(self) -> tuple[FournisseurDisponible, ...]:
        """Les fournisseurs auxquels la réponse sera confrontée.

        Import local du registre — la couche fournisseur n'est tirée qu'au premier
        usage, comme partout ailleurs dans la Control Tower (#84).
        """
        if self._fournisseurs is not None:
            return self._fournisseurs()
        from maestro.providers.registry import catalogue_fournisseurs

        return catalogue_fournisseurs()

    async def _generer(self, prompt: str) -> str:
        """L'appel modèle, fournisseur résolu au premier usage (import local, comme #139).

        Une réponse **vide** est un échec au même titre qu'une exception : un
        modèle qui n'a rien dit n'a rien proposé, et laisser passer le vide ferait
        de l'échec un brouillon de formulaire effacé.
        """
        if self._provider is None:
            from maestro.providers.factory import provider_from_settings

            self._provider = provider_from_settings()
        texte = await self._provider.generate(
            prompt, model=self._modele, system_prompt=_CADRE_GENERATION
        )
        if not (texte or "").strip():
            raise RuntimeError("le fournisseur de modèle a rendu une réponse vide")
        return texte


def _prompt_generation(
    intention: str, registre: tuple[FournisseurDisponible, ...]
) -> str:
    """L'intention, puis les fournisseurs recevables — la consigne, elle, est le cadre.

    Le registre vient **après** l'intention et avant la demande finale : c'est de
    la matière, et la règle du fil global vaut ici — ce qui ferme le prompt se lit
    comme une instruction.
    """
    return "\n".join(
        (
            "Intention de l'utilisateur :",
            "",
            intention,
            "",
            "Fournisseurs que Maestro sait servir (n'écris rien d'autre dans "
            "FOURNISSEUR et MODELE) :",
            "",
            _registre_en_clair(registre),
            "",
            "Propose la définition de l'agent selon le format demandé.",
        )
    )


def _registre_en_clair(registre: tuple[FournisseurDisponible, ...]) -> str:
    """Le registre en quelques lignes : un fournisseur par ligne, sa gamme derrière.

    Un fournisseur **à noms libres** (`openai`, qui fédère des endpoints aux
    nommages hétéroclites) le dit en toutes lettres, sans quoi une gamme vide se
    lirait « aucun modèle » là où elle veut dire « saisis le nom ». Un registre
    vide se dit aussi : le silence ferait croire à un oubli de mise en forme.
    """
    if not registre:
        return "- (aucun fournisseur enregistré : laisse les deux lignes vides)"
    lignes = []
    for fiche in registre:
        gamme = ", ".join(modele.nom for modele in fiche.modeles)
        if fiche.modeles_libres:
            gamme = (
                f"{gamme} (ou tout autre nom servi par l'endpoint)"
                if gamme
                else "tout nom de modèle servi par l'endpoint"
            )
        lignes.append(f"- {fiche.nom} : {gamme or 'aucun modèle annoncé'}")
    return "\n".join(lignes)


def _decouper(texte: str) -> tuple[dict[str, str], str]:
    """Sépare la réponse en (en-têtes, playbook) autour du marqueur.

    Lève `GenerationIndisponible` dès qu'il manque une des trois pièces du contrat
    — le marqueur, le playbook, le rôle : une réponse hors contrat ne doit pas
    produire un brouillon bancal, que le formulaire refuserait ensuite sans pouvoir
    dire d'où vient le trou (même conduite que `auto_amelioration._decouper`).

    Les en-têtes se lisent **avant** le marqueur seulement : un `NOM:` cité dans le
    playbook appartient au playbook.
    """
    parties = texte.split(MARQUEUR_PLAYBOOK, 1)
    if len(parties) != 2:
        raise GenerationIndisponible(
            f"réponse du modèle sans marqueur {MARQUEUR_PLAYBOOK!r} : découpage impossible."
        )
    entetes = _entetes(parties[0])
    playbook = parties[1].strip()
    if not playbook:
        raise GenerationIndisponible(
            "le modèle n'a pas produit de playbook (contenu vide) : rien à proposer."
        )
    if not _role(entetes.get("ROLE", "")):
        raise GenerationIndisponible(
            "le modèle n'a pas nommé de rôle : définition incomplète, rien à proposer."
        )
    if not _competences(entetes.get("COMPETENCES", "")):
        raise GenerationIndisponible(
            "le modèle n'a proposé aucune compétence : définition incomplète, rien à "
            "proposer."
        )
    return entetes, playbook


def _entetes(texte: str) -> dict[str, str]:
    """Les `CLE: valeur` lus dans l'en-tête de la réponse, clés inconnues ignorées.

    Tolérant sur la forme, jamais sur le vocabulaire : la casse et les espaces ne
    comptent pas, une puce de liste ni des accents graves non plus (un modèle en
    ajoute quand on lui demande une liste), mais seule une clé de `CLES_ENTETE`
    entre — une ligne de prose qui contiendrait un deux-points ne devient pas un
    champ. La **première** occurrence gagne : un modèle qui se reprend laisse deux
    lignes, et la seconde est un repentir, pas une correction.
    """
    trouves: dict[str, str] = {}
    for ligne in texte.splitlines():
        candidat = ligne.strip().lstrip("-*+").strip().strip("`").strip()
        cle, separateur, valeur = candidat.partition(":")
        if not separateur:
            continue
        cle = _sans_accents(cle.strip()).upper()
        if cle in CLES_ENTETE and cle not in trouves:
            trouves[cle] = valeur.strip().strip("`").strip()
    return trouves


def _role(brut: str) -> str:
    """Le rôle épuré — une ligne, sans les guillemets dont un modèle l'entoure."""
    return " ".join(brut.strip().strip("\"'").split())


def _competences(brut: str) -> tuple[str, ...]:
    """Les compétences virgulées, épurées et dédoublonnées dans l'ordre proposé.

    Même découpage que le formulaire (`definitionDepuis`, une virgule sépare) et
    même dédoublonnage que le dépôt (`store._valide`) : la proposition arrive dans
    un champ texte, elle doit donc déjà avoir la forme que ce champ produirait.
    """
    morceaux = (part.strip().strip("\"'").strip() for part in brut.split(","))
    return tuple(dict.fromkeys(m for m in morceaux if m))


def _fournisseur_recevable(
    registre: tuple[FournisseurDisponible, ...], fournisseur: str, modele: str
) -> tuple[str | None, str | None]:
    """Le couple (fournisseur, modèle) **confronté au registre**, ou (None, None).

    Trois refus, et le troisième est le motif du garde-fou : un fournisseur absent
    du registre est écarté (Maestro ne saurait pas le résoudre) ; un modèle hors de
    la gamme d'un fournisseur qui n'accepte pas les noms libres est écarté (le
    fournisseur, lui, reste — il est vrai) ; et un modèle **sans** fournisseur
    reconnu part avec lui, faute de référent qui dise s'il existe.

    Écarter rend un champ vide, que le formulaire lit « réglages par défaut ». Le
    contraire — laisser passer un nom plausible — donnerait un agent qui échoue à
    sa première exécution, loin d'ici et sans rapport apparent avec ce clic.
    """
    fiches = {fiche.nom.casefold(): fiche for fiche in registre}
    fiche = fiches.get(fournisseur.strip().casefold())
    if fiche is None:
        return None, None
    voulu = modele.strip()
    if not voulu:
        return fiche.nom, None
    connu = next(
        (m.nom for m in fiche.modeles if m.nom.casefold() == voulu.casefold()), None
    )
    if connu is not None:
        return fiche.nom, connu
    return fiche.nom, (voulu if fiche.modeles_libres else None)


def _nom_propose(brut: str, role: str, noms_pris: Collection[str]) -> str:
    """Un nom d'agent **libre** et au format, dérivé de ce que le modèle a proposé.

    Trois replis en cascade, du plus fidèle au plus neutre : le nom proposé, le
    rôle slugifié, puis `agent`. Aucun ne peut échouer — le nom est une commodité,
    et refuser une définition par ailleurs bonne parce que son nom est mal formé
    ferait payer à l'utilisateur une faute du modèle.

    La collision se résout par un suffixe numérique. Elle n'est **pas** une
    validation : le dépôt reste seul juge à la création (409), et un agent créé
    entre cette réponse et le clic la rendrait de toute façon caduque. Ce qu'on
    évite est le cas fréquent et évitable — proposer « relecteur-sql » à quelqu'un
    qui en a déjà un.
    """
    pris = {nom.casefold() for nom in noms_pris}
    for candidat in (_slug(brut), _slug(role), "agent"):
        if not candidat:
            continue
        if candidat.casefold() not in pris:
            return candidat
        for suite in range(2, 100):
            suivant = f"{candidat}-{suite}"
            if suivant.casefold() not in pris:
                return suivant
    return "agent"


def _slug(brut: str) -> str:
    """`brut` ramené au format d'un nom d'agent, ou une chaîne vide s'il n'en reste rien.

    Les accents partent (« Développeur » → « developpeur ») et tout ce qui n'est
    ni lettre ni chiffre devient un tiret : le résultat satisfait `_SLUG_AGENT` par
    construction, et la vérification finale est là pour que ce soit la **règle**
    qui décide et non le raisonnement de qui a écrit la substitution.
    """
    sans_accents = _sans_accents(brut).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", sans_accents).strip("-")[:40].strip("-")
    return slug if _SLUG_AGENT.match(slug) else ""


def _sans_accents(texte: str) -> str:
    """`texte` décomposé puis débarrassé de ses diacritiques (NFKD)."""
    decompose = unicodedata.normalize("NFKD", texte)
    return "".join(c for c in decompose if not unicodedata.combining(c))
