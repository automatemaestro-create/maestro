"""Le verdict d'attente — un run suspendu trop longtemps le dit (#737).

Décision **#651**, [docs/33 §5.3 et §5.4](../../docs/33-decision-surveillance-run.md).

Un run suspendu sur un humain portait déjà son ancienneté — `EtatExecution
.attente_depuis` (`state.py:448`) est posé et levé sur les **trois** attentes
(`STATUTS_EXECUTION_EN_ATTENTE`), sérialisé dans le résumé et affiché en huit
endroits d'`apps/web/`, **tous** via `formatHeureRelative` et **aucun** ne le
comparant à quoi que ce soit. Le fait était lisible ; il n'était pas opposable.
Ce module rend le jugement qui manquait.

**C'est le frère de `vitalite` sur l'autre question** (`battement.py:119`) : non
pas « son hôte est-il là ? » mais « ce run avance-t-il ? ». Le patron est repris
entier — une **fonction pure** de la projection, calculée **à la lecture** et
posée sur le résumé sans rien toucher d'autre — et il achète les mêmes trois
choses : aucun type d'événement nouveau (le dépôt en porte 18, dont un sans
producteur), aucun champ de projection ni migration, et une règle **testable
comme une fonction**, sans horloge ni processus.

Il vit **ici** et non dans `battement.py`, dont l'en-tête revendique de tenir en
une phrase : un battement n'entre pas dans ce verdict, et la donnée qu'il lit
vient de la projection. C'est la règle même qui a mis `vitalite` à côté du
registre qu'elle relit — le verdict habite avec ce dont il juge.

**Trois choses à ne pas défaire** ([docs/33 §9](../../docs/33-decision-surveillance-run.md)) :

- **il est dérivé, jamais stocké.** Le stocker donnerait deux vérités dont la
  seconde se périmerait ; ainsi il se reconstruit gratuitement au redémarrage de
  l'API, qui rejoue son journal durable ;
- **il n'agit sur rien** — il n'annule, ne reprend, ne relance rien. C'est
  précisément ce qui autorise son seuil serré (ci-dessous) : lui donner un geste
  renverserait l'asymétrie qui le justifie, et il faudrait alors le rendre
  généreux comme `SEUIL_ORPHELIN_S` ;
- **un seul seuil pour les trois attentes.** Le dépôt a déjà tranché que
  l'ancienneté d'attente n'a qu'une réponse (`state.py:445-447`) ; en écrire
  trois rouvrirait cette décision par la bande.

⚠ **Le verdict juge l'attente, jamais la durée.** Un run qui travaille depuis
plus longtemps que le seuil ne le porte pas — il n'attend personne. C'est la
confusion exacte que `vitalite` a évitée (`battement.py:137-141`) et qu'il serait
facile de refabriquer ici en comparant `debut` au lieu d'`attente_depuis`.
"""

from __future__ import annotations

from datetime import UTC, datetime

from maestro.controltower.state import STATUTS_EXECUTION_EN_ATTENTE

#: Seuil de souffrance, en secondes — **quinze minutes**, soit la moitié du seuil
#: d'orphelinat (`SEUIL_ORPHELIN_S`, 30 min), parce que ses erreurs coûtent moins
#: de la moitié.
#:
#: **L'asymétrie des erreurs est INVERSÉE** par rapport aux deux seuils généreux
#: du dépôt — les 30 min d'orphelinat (`battement.py:89-99`) et les 6 h de #327 —,
#: et c'est ce qui fonde une valeur aussi serrée. Là-bas, se tromper **détruit** :
#: déclarer orphelin un run vivant, c'est proposer de le reprendre depuis son
#: cadrage ; déclarer abandonné un ticket vivant, c'est le retirer à qui travaille
#: dessus. D'où leur règle, « on se trompe du côté qui ne détruit rien ».
#:
#: Ici le verdict ne fait que **trier** : un faux positif coûte une ligne signalée
#: qu'on regarde et qu'on oublie ; un faux négatif coûte ce que #568 a coûté, et
#: c'est mesuré — le run est resté figé **31 % de son temps de mur** et n'a repris
#: que par un `POST` à la main, pendant que l'écran affirmait « aucune validation
#: en attente » (docs/05 §2.6).
#:
#: C'est un **point de départ nommé, pas une loi** : ce qui compte est qu'il soit
#: une constante avec son motif écrit, comme `SEUIL_ORPHELIN_S`, et la première
#: mesure d'usage le déplacera. Une règle qui crie trop se règle **en déplaçant ce
#: chiffre** — jamais en ajoutant un juge qui trierait ses propres cris (docs/33
#: §4.3, le refus de #586 appliqué au capteur).
SEUIL_SOUFFRANCE_S = 900.0


def en_souffrance(
    statut: str,
    attente_depuis: str | None,
    *,
    maintenant: datetime | None = None,
    seuil_s: float = SEUIL_SOUFFRANCE_S,
) -> bool:
    """Ce run est-il **suspendu sur un humain au-delà du seuil** ?

    `statut` est celui de la projection (`EtatExecution.statut`) et
    `attente_depuis` l'horodatage de l'événement qui a suspendu le run
    (`EtatExecution.attente_depuis`), `None` dès qu'il repart ou qu'il est soldé.
    Les deux se lisent sur le résumé déjà servi : le verdict ne demande **rien**
    de plus que ce que `GET /api/executions` porte depuis #321.

    **Le verdict est binaire, et c'est un choix.** `vitalite` est ternaire parce
    qu'elle a trois états de *connaissance* ; ici le troisième — « il attend, mais
    pas depuis trop longtemps » — est **déjà porté par le statut**
    (`STATUTS_EXECUTION_EN_ATTENTE`). Le reporter dans le verdict serait un second
    support pour un même fait, c'est-à-dire la panne que #365 a supprimée sur le
    cycle de vie. Un run soldé rend donc `False` comme un run au travail : sans
    objet et faux se disent ici du même mot, là où `vitalite` avait besoin de les
    séparer.

    Trois écarts, et le premier est **l'inverse** de celui de `vitalite` :

    - un horodatage **illisible** — ou absent alors que le statut dit l'attente —
      rend `True`. L'inversion suit l'asymétrie des erreurs (cf. le seuil
      ci-dessus) : là-bas, affirmer la mort sur une donnée qu'on ne sait pas lire
      déclenche une reprise destructrice, donc on s'abstient ; ici « ce run est
      suspendu et on ne sait même pas depuis quand » est **pire** que « suspendu
      depuis 20 minutes », et le signaler ne casse rien ;
    - une attente **dans le futur** (horloges désaccordées entre l'hôte et l'API)
      est traitée comme fraîche, donc sans souffrance — la valeur est *lisible*,
      la règle ci-dessus ne s'y applique pas, et c'est l'arithmétique qui parle ;
    - un horodatage **sans fuseau** est lu en UTC, la seule forme que les
      événements écrivent (#46) ; une valeur venue d'ailleurs y serait de toute
      façon plus proche du vrai que naïvement comparée à un instant aware (qui
      lèverait).

    ⚠ **Une pause n'est pas une entrée de ce verdict**, et le prix est nommé
    plutôt que masqué : un run mis en pause *pendant* une attente garde son statut
    d'attente (`state.py:474-480` — la pause est un drapeau à côté du statut, pas
    dedans), donc il finit par porter le verdict. C'est un faux positif assumé, du
    côté qui ne détruit rien : la pause d'un run **au travail**, elle, n'en produit
    aucun, et c'est le cas que docs/33 §3.2 écartait en refusant d'alerter sur
    l'exercice d'une commande qu'on offre.
    """
    if statut not in STATUTS_EXECUTION_EN_ATTENTE:
        return False
    if not attente_depuis:
        return True
    try:
        depuis = datetime.fromisoformat(attente_depuis)
    except ValueError:
        return True
    if depuis.tzinfo is None:
        depuis = depuis.replace(tzinfo=UTC)
    return ((maintenant or datetime.now(UTC)) - depuis).total_seconds() > seuil_s
