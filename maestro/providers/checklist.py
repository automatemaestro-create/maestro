"""Où l'agent dit sa checklist, et comment on la lit (#489).

L'arbitrage de #489 (cf. `maestro.detail_tache`) confie à l'agent la moitié
« complétée et cochée » de la checklist d'une tâche. Restait à trouver **par où**
il la dit — et la réponse tenait en ceci : il la dit déjà.

Un agent outillé tient sa propre liste de travail avec l'outil `TodoWrite`, dont
l'entrée est **exactement** une checklist : un libellé et un avancement par
ligne. Cette entrée traverse déjà le flux du SDK et déjà la seule fonction qui
l'observe (`maestro.providers.claude._absorbe`, #479) : il n'y avait ni protocole
à inventer, ni transport à ouvrir, ni serveur MCP à monter — seulement un appel
d'outil qu'on jetait.

Trois conséquences à connaître avant d'y toucher :

- **rien n'est demandé à l'agent qu'il ne fasse déjà**. Une consigne qui lui
  imposerait un format de compte-rendu serait un pari sur sa docilité, et un
  pari perdu se solderait par une checklist vide sans que rien ne le dise. Ici,
  un agent qui ne tient pas de liste ne produit simplement aucune étape — et la
  tâche reste exactement ce qu'elle est aujourd'hui (règle de #246) ;
- **le couplage à l'outil vit ici**, pas dans le moteur. Le contrat de la couche
  fournisseur est `on_etapes(list[EtapeTache])` (`ModelProvider.run_agent`), au
  même titre que `on_refus` et `on_activite` : un fournisseur qui n'a pas d'outil
  de ce genre n'appelle jamais le canal, et le moteur ne s'en aperçoit pas ;
- **la lecture ne lève jamais**. Une entrée d'outil est une donnée qui vient du
  modèle : elle peut être tronquée, mal typée, ou porter des clés que nous ne
  connaissons pas. Observer ne doit pas casser l'observé — c'est la règle déjà
  posée pour le hook de permissions et pour le régulateur d'activité.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from maestro.detail_tache import ETAPE_A_FAIRE, ETAPE_EN_COURS, ETAPE_FAITE, EtapeTache

#: Le nom de l'outil par lequel un agent outillé tient sa liste de travail.
#: Constante nommée plutôt que littéral disséminé : c'est la **seule** chose de
#: ce module qui soit propre à l'outillage de l'agent, et elle doit se voir.
OUTIL_CHECKLIST = "TodoWrite"

#: La clé qui porte la liste dans l'entrée de l'outil.
_CLE_LISTE = "todos"

#: Les clés qui portent le libellé d'une ligne, dans l'ordre où on les cherche.
#: `content` est l'énoncé canonique ; `activeForm` (« Rédaction de la migration »)
#: est sa forme en cours d'action, prise en repli — mieux vaut une ligne au
#: gérondif qu'une ligne écartée faute d'énoncé.
_CLES_LIBELLE: tuple[str, ...] = ("content", "activeForm")

#: La clé qui porte l'avancement d'une ligne.
_CLE_ETAT = "status"

#: Traduction des avancements de l'outil vers les états du contrat (#246).
#: Volontairement **partielle** : un statut hors de cette table n'est pas refusé,
#: il passe tel quel (« rien ne se refuse ») et le front le ramènera à « à
#: faire ». Ce qui est ici est ce que nous savons traduire, pas ce que nous
#: acceptons de recevoir.
_ETATS: dict[str, str] = {
    "pending": ETAPE_A_FAIRE,
    "in_progress": ETAPE_EN_COURS,
    "completed": ETAPE_FAITE,
}


def est_checklist(outil: str) -> bool:
    """L'appel d'outil `outil` porte-t-il une checklist d'agent ?"""
    return outil == OUTIL_CHECKLIST


def etapes_depuis_outil(entree: object) -> list[EtapeTache]:
    """Les étapes lisibles dans l'entrée d'un appel `TodoWrite` — `[]` s'il n'y en a pas.

    Tolérante de bout en bout, et jamais levante : une entrée qui n'est pas un
    objet, une liste absente ou mal typée, une ligne sans énoncé rendent une
    liste vide ou une ligne de moins — jamais une exception. L'appelant
    (`SuiviChecklist.rapporte`) traite une liste vide comme « l'agent n'a rien
    dit », ce qui laisse la checklist intacte : le pire cas d'une lecture ratée
    est qu'il ne se passe rien.
    """
    if not isinstance(entree, Mapping):
        return []
    lignes = entree.get(_CLE_LISTE)
    if isinstance(lignes, str) or not isinstance(lignes, Sequence):
        return []
    etapes = (_etape_depuis_ligne(ligne) for ligne in lignes)
    return [etape for etape in etapes if etape is not None]


def _etape_depuis_ligne(ligne: object) -> EtapeTache | None:
    """Une ligne de la liste de l'agent en étape — `None` si elle n'apprend rien."""
    if not isinstance(ligne, Mapping):
        return None
    libelle = ""
    for cle in _CLES_LIBELLE:
        valeur = ligne.get(cle)
        if isinstance(valeur, str) and valeur.strip():
            libelle = valeur
            break
    if not libelle:
        return None
    brut = ligne.get(_CLE_ETAT)
    brut = brut.strip().lower() if isinstance(brut, str) else ""
    return EtapeTache(libelle=libelle, etat=_ETATS.get(brut, brut) or ETAPE_A_FAIRE).valide()
