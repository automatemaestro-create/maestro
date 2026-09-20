"""Service d'analyse d'outillage pour la Control Tower (#1030).

La pièce que la route `GET /api/projets/{id}/outillage/analyse` appelle, au
patron de [`maestro.controltower.projets`](./projets.py) : le service tient la
forme JSON et les refus, `app.py` ne fait que les traduire en codes HTTP.

**Une couche mince, et c'est voulu.** Toute l'analyse vit dans
`maestro.outillage` — qui ne connaît ni HTTP ni projet déclaré. Ce module n'en
ajoute qu'une chose : il **résout le projet** avant de regarder le disque. C'est
ce qui fait que la route n'analyse jamais un chemin qu'on lui apporte, mais
seulement la racine d'un projet déjà déclaré, donc déjà passée par
`valider_racine` (EF-38). Une route qui accepterait un chemin libre serait une
seconde porte d'entrée sur le disque à côté de l'explorateur, avec ses propres
frontières à tenir d'accord — exactement ce que `POST /api/projets/racine`
(#938) a rassemblé en une porte unique.

**Le périmètre du projet s'applique.** `Projet.perimetre` retire d'office
`.env` et `**/secrets/**` (docs/24 §2.5) : l'analyse ne peut donc pas lire les
deux gisements de secrets d'un dépôt d'utilisateur, et ce n'est pas une
précaution prise ici mais une propriété de ce qui est déclaré.

**Rien n'est écrit, ni sur le disque ni dans la forge.** L'analyse propose ; la
génération est le lot 5 du chantier (#1033), et elle passera par le régime
d'écriture de docs/24 §2.4. Deux appels sur un projet inchangé rendent le même
contenu — seuls l'`id` et l'horodatage diffèrent, parce qu'ils datent la
lecture et non le projet.
"""

from __future__ import annotations

from typing import Any

from maestro.controltower.projets import ProjetInconnu, ServiceProjets
from maestro.outillage import Bornes, analyser
from maestro.projets import Projet


class ServiceOutillage:
    """L'analyse d'un projet déclaré, servie par l'API.

    `bornes` permet de resserrer la lecture (les tests s'en servent pour
    fabriquer une troncature sur un projet minuscule) ; `None` — le cas nominal
    — laisse le défaut de `maestro.outillage`, dossiers ignorés compris.
    """

    def __init__(self, projets: ServiceProjets, *, bornes: Bornes | None = None) -> None:
        self._projets = projets
        self._bornes = bornes

    def analyser(self, id_projet: str) -> dict[str, Any]:
        """Analyse le projet `id_projet` et rend l'outillage recommandé (docs/38).

        Lève `ProjetInconnu`/`ProjetIllisible` **avant de toucher au disque** —
        un projet qu'on ne sait pas lire n'a pas de racine à parcourir —, et
        `RacineRefusee` motivée si la racine déclarée n'est plus un dossier
        lisible (`dossier-absent`, `pas-un-dossier`). Les trois portent un
        `motif` : c'est ce que `statut_http`/`detail_refus` traduisent, jamais
        un 500.

        Appelée **hors de la boucle d'événements** par la route : parcourir un
        projet réel prend des secondes, et une route qui bloquerait la boucle
        figerait les flux SSE des autres écrans.
        """
        projet = self._projet(id_projet)
        analyse = analyser(
            projet.racine_chemin,
            projet_id=projet.id,
            perimetre=projet.perimetre,
            bornes=self._bornes,
        )
        return analyse.to_dict()

    def _projet(self, id_projet: str) -> Projet:
        """Le projet déclaré, relu par le service des projets — jamais un second lecteur.

        `ServiceProjets.detail` rend un dict ; ici il faut l'entité (sa racine
        en `Path`, son périmètre). On passe donc par le **dépôt** que le service
        expose, ce qui garde un seul lecteur de projets dans la Control Tower :
        deux relectures de la même fiche finiraient par ne plus se refuser les
        mêmes fichiers.
        """
        self._projets.detail(id_projet)  # 404/422 motivés, avant toute lecture du disque
        projet = self._projets.store.lire(id_projet)
        if projet is None:  # pragma: no cover - `detail` a déjà levé dans ce cas
            raise ProjetInconnu(id_projet)
        return projet
