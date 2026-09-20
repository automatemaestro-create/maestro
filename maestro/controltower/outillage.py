"""Service d'outillage pour la Control Tower : l'analyser (#1030), l'écrire (#1033).

La pièce que les routes `GET /api/projets/{id}/outillage/analyse` et
`POST /api/projets/{id}/outillage/generation` appellent, au patron de
[`maestro.controltower.projets`](./projets.py) : le service tient la forme JSON
et les refus, `app.py` ne fait que les traduire en codes HTTP.

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

**L'analyse n'écrit rien** : deux appels sur un projet inchangé rendent le même
contenu — seuls l'`id` et l'horodatage diffèrent, parce qu'ils datent la lecture
et non le projet. **La génération, elle, écrit** (#1033) — et c'est ici que le
régime d'écriture de docs/24 §2.4 se referme :

- un projet **non versionné** reçoit son outillage **en place**, dans sa racine :
  il n'y a pas de moment de fusion où accrocher un accord, et ce qui garde est la
  frontière d'écriture, le manifeste qui refuse d'écraser et le rapport ;
- un projet **versionné** le reçoit sur une branche `maestro/outillage-…`, que
  `appliquer_sous_validation` propose ensuite à la **validation humaine**, diff
  sous les yeux (EF-37). C'est le geste unitaire de #227, pas un second : ce
  module marie le préparateur (`maestro.outillage.ecriture`) et le validateur,
  exactement comme `maestro.engine.executor` le fait pour le travail d'une tâche.

**Pourquoi le validateur est passé ici et non construit ici.** Il vient de
l'appelant (`create_app`, qui tient le bus d'événements) pour la raison qui vaut
déjà dans le moteur : un module qui écrit dans le projet de quelqu'un ne doit pas
être celui qui décide qu'on a le droit d'y écrire. Sans validateur, `Guardrails`
**refuse** (fail-safe, #9) : l'outillage reste sur sa branche et rien n'atteint la
racine — jamais l'inverse.
"""

from __future__ import annotations

import asyncio
from typing import Any

from maestro.controltower.projets import ProjetInconnu, ServiceProjets
from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import Validateur
from maestro.outillage import (
    REGIME_BRANCHE,
    Analyse,
    Bornes,
    analyser,
    generer_outillage,
)
from maestro.projets import Projet


class ServiceOutillage:
    """L'outillage d'un projet déclaré, servi par l'API : analysé, puis écrit.

    `bornes` permet de resserrer la lecture (les tests s'en servent pour
    fabriquer une troncature sur un projet minuscule) ; `None` — le cas nominal
    — laisse le défaut de `maestro.outillage`, dossiers ignorés compris.

    `validateur` tranche « écrire cet outillage dans mon projet ? » sur un projet
    versionné. `None` **refuse** (fail-safe des garde-fous, #9) : la branche reste
    et la racine est intacte. C'est le bon défaut — un service monté sans
    validateur ne doit pas écrire chez quelqu'un parce que personne n'a été
    câblé pour dire non.
    """

    def __init__(
        self,
        projets: ServiceProjets,
        *,
        bornes: Bornes | None = None,
        validateur: Validateur | None = None,
    ) -> None:
        self._projets = projets
        self._bornes = bornes
        self._validateur = validateur

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
        return self._analyse(self._projet(id_projet)).to_dict()

    async def generer(self, id_projet: str, *, run_id: str = "") -> dict[str, Any]:
        """Écrit dans `id_projet` l'outillage que son analyse recommande (docs/38, #1033).

        Le déroulé, dans cet ordre — il compte :

        1. le projet est **résolu** puis **analysé** (lecture seule) : on ne
           propose jamais d'écrire sans avoir regardé ce qu'il y a ;
        2. l'outillage est **préparé** au régime du projet, hors de la boucle
           d'événements (parcours du disque, et sous-processus Git sur un projet
           versionné) ;
        3. sur un projet **versionné seulement**, la branche est soumise à
           l'**accord humain**, diff en pièce jointe — et l'attente est celle du
           mécanisme existant : indéfinie, sans time-out silencieux. Un refus,
           ou un validateur absent, laisse la branche intacte : l'outillage
           proposé reste consultable et se récupère d'un `git merge`.

        Rend le rapport dans les deux régimes, `application` portant le verdict
        de la validation quand il y en a eu une. Lève les refus **motivés** de
        ses couches (`ProjetInconnu`, `RacineRefusee`, `EspaceProjetIndisponible`,
        `ApplicationRefusee`) — jamais un 500 : écrire chez quelqu'un peut être
        refusé, ce n'est pas une panne.
        """
        projet = self._projet(id_projet)
        analyse = await asyncio.to_thread(self._analyse, projet)
        preparation = await asyncio.to_thread(
            generer_outillage,
            projet,
            analyse.constats,
            analyse.recommandation,
            source=analyse.source_manifeste(),
        )
        reponse: dict[str, Any] = {
            "projet_id": projet.id,
            "analyse": analyse.id,
            **preparation.to_dict(),
            "application": None,
        }
        if preparation.regime != REGIME_BRANCHE:
            return reponse
        resultat = await appliquer_sous_validation(
            projet,
            tache_id=preparation.tache_id,
            validateur=self._validateur,
            branche=preparation.branche,
            titre=f"Écrire l'outillage dans {projet.nom}",
            run_id=run_id,
        )
        reponse["application"] = resultat.to_dict()
        return reponse

    def _analyse(self, projet: Projet) -> Analyse:
        """L'analyse du projet — **bloquante**, et le seul lecteur du disque de ce module."""
        return analyser(
            projet.racine_chemin,
            projet_id=projet.id,
            perimetre=projet.perimetre,
            bornes=self._bornes,
        )

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
