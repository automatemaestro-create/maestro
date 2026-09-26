"""L'outillage d'un projet, **pièce par pièce, dans la conversation** (#1161, docs/43 §2.2).

Jusqu'ici l'outillage s'écrivait **en une fois**, à la fin d'un formulaire à étapes
(`EtapeOutillage`, #1034) ou au pied du fil (`ConclusionOutillage`, #1104) : une liste à
cocher, validée en bloc. La personne a demandé autre chose, le 2026-09-24 : *« le chat
me pose des questions, me propose des choix, et au fur et à mesure on peut générer
l'outillage »*. Ce module est ce « au fur et à mesure » : chaque fichier — `AGENTS.md`,
un skill — est **proposé** avec ce qu'il changera sur le disque, **vérifié** avant d'être
montré, puis **écrit sur accord**, un par un.

## Ce qu'il n'invente pas

Tout ce qu'une pièce est vient des couches existantes, appelées telles quelles :

- **la matière** — ce qu'on sait du projet : la lecture d'un projet importé (#1158,
  `ServiceOutillage.analyse_de`), ou ce que le questionnaire a compris d'un projet neuf
  (#1147, la `comprehension` portée par le fil) ;
- **la recommandation** — `recommander`, commun aux deux chemins (docs/43 §3) ;
- **le texte** — `rediger`, contre le manifeste de la cible (`portees_declarees`) ;
- **le verdict des commandes** — le `Verificateur` de #1160, joué **avant** que la carte
  ne s'affiche : on accepte en sachant ce qui marche ;
- **le diff** — `prevoir`, la décision même que l'écriture prendra (docs/38 §4.2) ;
- **l'écriture** — `poser_piece`, qui écrit un fichier et fusionne le manifeste, au
  régime du projet (docs/24 §2.4) : en place, ou sur une branche fusionnée.

## Le fil est la seule mémoire, ici aussi

Ce module ne retient **aucun état de conversation**. Ce qui a été écrit ou écarté se lit
sur le fil (`pieces_tranchees`), ce qui a été corrigé aussi (`corrections_du_fil`), et
la pièce suivante se **recalcule** : la première, dans l'ordre de la recommandation, qui
changerait le disque et n'a pas encore été tranchée **telle quelle**. C'est ce qui fait
qu'une correction qui touche `AGENTS.md` déjà écrit le fait **reproposer** — son
contenu a changé, ce n'est plus la pièce tranchée — sans qu'aucune règle ne le prévoie.

Une seule chose est gardée, et elle n'est pas un état de conversation : **la lecture
d'un projet importé**, épinglée pour la conversation (`_lues`). Écrire `AGENTS.md`
change le projet, et l'analyse — qui se relit dès qu'un fichier bouge — relirait tout
par le modèle entre deux pièces : il n'est pas tenu de répondre deux fois pareil, et les
pièces changeraient sous les yeux, exactement le défaut de #1100. Une conversation neuve
relit le projet.

## Rien n'est écrit sans accord — et l'accord porte sur ce qui a été montré

- la pièce **porte son contenu** : l'accord écrit ce texte-là, jamais une seconde
  rédaction (qui rejouerait des commandes et pourrait rendre autre chose) ;
- l'accord **revérifie le disque** : un fichier qui a bougé depuis la carte ne s'écrit
  pas sur la foi d'un diff périmé (`PieceChangee`) ;
- une version dont la commande **corrigée** a échoué à l'exécution ne s'écrit pas
  (`PieceProposee.echec`) : « une correction en échec le dit, sans rien écrire ».

## Sur un projet versionné, l'accord est celui de la carte (`AccordDeLaCarte`)

Le régime ne change pas : la pièce s'écrit sur une branche `maestro/outillage-…`,
soumise à `appliquer_sous_validation` — diff calculé, périmètre vérifié, fusion sur
accord. Ce qui change est **d'où vient l'accord** : il a été donné sur la carte, diff
sous les yeux, et c'est lui que le validateur rend. Il le rend **pour ce seul
diff-là** : si la branche touche autre chose que la pièce et le manifeste, il refuse.
Redemander l'accord sur l'écran des validations, pour chaque pièce, reviendrait à faire
valider deux fois le même fichier — le second accord, sur un résumé de chemins, en
sachant moins que le premier.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from maestro.agents.catalog import MODELE_EXECUTANT_DEFAUT
from maestro.agents.playbook_du_code import registre
from maestro.controltower.chat import (
    PIECE_ECARTEE,
    MessageChat,
    PieceEcrite,
    PieceProposee,
    acquis_du_fil,
    corrections_du_fil,
    pieces_tranchees,
)
from maestro.controltower.outillage import ServiceOutillage
from maestro.controltower.projets import ServiceProjets
from maestro.controltower.validation import appliquer_sous_validation
from maestro.engine.guardrails import DemandeValidation
from maestro.outillage.correction import (
    CLES_CORRIGEABLES,
    CorrectionLue,
    constats_en_texte,
    corrections_en_texte,
    corriger,
    lire_correction,
)
from maestro.outillage.detection import CHEMIN_MANIFESTE
from maestro.outillage.ecriture import REGIME_BRANCHE, REGIME_EN_PLACE, nouvel_id_de_generation
from maestro.outillage.generation import (
    Prevision,
    empreinte,
    portees_declarees,
    poser_piece,
    prevoir,
    verifications_declarees,
)
from maestro.outillage.modele import ORIGINE_DITE, Analyse, Constats, Entree, Recommandation
from maestro.outillage.questionnaire import (
    Choix,
    acquis_de,
    constats_depuis_choix,
    schema_en_texte,
    source_manifeste_des_choix,
)
from maestro.outillage.recommandation import recommander
from maestro.outillage.redaction import Fichier, raison_stable, rediger
from maestro.outillage.verification import ECHOUEE, VERIFIEE, Verificateur, Verification
from maestro.projets import Projet
from maestro.projets.perimetre import motifs_compiles
from maestro.projets.racine import valider_racine
from maestro.providers.base import ModelProvider
from maestro.sandbox.en_place import DOSSIER_ATELIER, FrontiereEcriture, fichiers_du_perimetre
from maestro.sandbox.projet import branche_de_tache, espace_de_travail

#: Ce que le geste fera au chemin d'une pièce (`PieceProposee.sort`).
SORT_CREE = "cree"
SORT_REECRIT = "reecrit"
SORT_BLOC = "bloc"

#: Le cadre de la compréhension d'une **correction** (#1161). Concaténé, comme
#: `_PROMPT_COMPREHENSION` et pour la même raison : le contrat est un gabarit JSON,
#: dont les accolades se liraient comme des champs dans une f-string. Il finit par le
#: registre (#945) : son `message` s'affiche à la personne.
_PROMPT_CORRECTION = (
    """\
Tu aides Maestro à corriger l'OUTILLAGE d'un projet : les instructions et les commandes
qu'il écrit dans le projet pour les agents qui y travailleront (AGENTS.md, des skills).
La personne vient de dire, avec ses mots, quelque chose de cet outillage. Tu traduis sa
phrase en corrections : pour chaque sujet qu'elle change, la clé du sujet et la nouvelle
valeur.

Les sujets, et la clé à employer pour chacun :
"""
    + schema_en_texte()
    + """

Pour "installer", "construire", "tester", "lint", "formater", "types" et "demarrer", la
valeur est la commande exacte, telle qu'on la taperait. La valeur "aucun" dit qu'il n'y
en a pas. Une phrase peut changer plusieurs sujets : « on utilise pnpm, pas npm » change
le gestionnaire ET les commandes qui l'appellent (installer, tester…).

Tu reçois ce qu'on sait déjà de l'outillage et les corrections déjà prises. Ne corrige
que ce que la phrase change. N'invente rien : une commande que la phrase ne donne pas,
et qu'on ne peut pas en déduire sans ambiguïté, ne s'écrit pas.

- La phrase corrige l'outillage et tu la comprends : "comprise": true, et les
  corrections.
- La phrase parle de l'outillage sans rien y changer (« continue », « outille ce
  projet », « c'est bon ») : "comprise": true, "corrections": [].
- Tu ne sais pas la traduire en sujets de l'outillage — trop vague, contradictoire, hors
  de ces sujets : "comprise": false, et "message" dit en une phrase ce que tu n'as pas
  compris, ou ce qu'il faudrait préciser.

Réponds par un objet JSON et rien d'autre — ni texte autour, ni bloc de code :

{"comprise": true, "corrections": [{"cle": "...", "valeur": "..."}], "message": "..."}

- "message" : une phrase à la personne quand "comprise" vaut false ; vide sinon —
  Maestro lui a déjà répondu, et la correction se lira sur ce qu'il écrit.

"""
    + registre()
)


class PieceChangee(RuntimeError):
    """Le fichier d'une pièce a bougé entre la carte et l'accord : rien n'est écrit.

    L'accord portait sur un diff ; ce diff n'est plus celui qui s'appliquerait. Écrire
    quand même serait écrire ce que personne n'a vu. L'appelant repropose la pièce,
    diff à jour.
    """


class CorrectionModele:
    """Ce que Maestro comprend d'une **correction** de l'outillage, demandé au modèle (#1161).

    Un appel par correction, et c'est le prix de « se corrige en langage naturel » : le
    modèle rend la clé et la valeur qu'il a comprises, `lire_correction` ne croit rien
    sans le vérifier, et la **phrase** — la justification — est posée par le code.

    Le fournisseur est résolu **au premier usage**, comme celui de
    `ComprehensionModele` : construire le service ne coûte rien et ne lève aucune erreur
    de configuration. Un fournisseur injecté (un double de test) est pris tel quel.
    """

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self._provider = provider

    async def comprendre(
        self, *, projet: str, constats: str, corrections: str, phrase: str
    ) -> CorrectionLue:
        """Ce que `phrase` corrige, vu ce qu'on sait (`constats`) et déjà corrigé."""
        from maestro.providers.factory import modele_du_canal, provider_from_settings

        if self._provider is None:
            self._provider = provider_from_settings()
        fournisseur = self._provider
        texte = await fournisseur.generate(
            _prompt_de_correction(projet, constats, corrections, phrase),
            model=modele_du_canal(MODELE_EXECUTANT_DEFAUT, fournisseur),
            system_prompt=_PROMPT_CORRECTION,
        )
        return lire_correction(texte, phrase)


def _prompt_de_correction(projet: str, constats: str, corrections: str, phrase: str) -> str:
    """Le prompt d'utilisateur : le projet, ce qu'on sait, ce qui est corrigé, la phrase."""
    return (
        f"## Le projet\n\n{projet}\n\n"
        f"## Ce qu'on sait de son outillage\n\n{constats}\n\n"
        f"## Les corrections déjà prises\n\n{corrections}\n\n"
        f"## Ce que la personne vient de dire\n\n« {phrase} »\n"
    )


class AccordDeLaCarte:
    """Le validateur d'une pièce écrite sur un projet versionné : l'accord donné sur la carte.

    Il rend `True` **pour ce diff-là seulement** — la branche ne touche que la pièce et
    le manifeste de l'outillage. Tout autre chemin, ou un diff vide, rend `False` : un
    accord donné sur un fichier ne vaut pas pour un autre (voir le module).
    """

    def __init__(self, chemins: Iterable[str]) -> None:
        self._chemins = frozenset(chemins)

    async def __call__(self, demande: DemandeValidation) -> bool:
        """L'accord de la carte vaut-il pour ce diff ?"""
        if demande.diff is None:
            return False
        touches = {m.chemin for m in demande.diff.modifications}
        return bool(touches) and touches <= self._chemins


@dataclass(frozen=True)
class _Matiere:
    """Ce qu'on sait d'un projet à ce tour : ses constats corrigés, et ce qu'ils recommandent."""

    projet: Projet
    racine: Path
    constats: Constats
    recommandation: Recommandation
    source: dict[str, Any]
    corrections: tuple[Choix, ...] = ()


class ServicePieces:
    """L'outillage d'un projet déclaré, proposé puis écrit **une pièce à la fois** (#1161).

    `outillage` est le service de #1030 — c'est lui qui résout le projet et qui le lit,
    une seule porte sur le disque pour les deux. `projets` porte le report (« plus
    tard », docs/37 §4.6). `verificateur` joue les commandes avant qu'une pièce ne se
    montre (`None` : le réel, que la suite de tests neutralise d'un seul endroit).
    `correction` comprend une phrase de correction (`None` : le modèle du poste, résolu
    au premier usage).
    """

    def __init__(
        self,
        outillage: ServiceOutillage,
        projets: ServiceProjets,
        *,
        verificateur: Verificateur | None = None,
        correction: CorrectionModele | None = None,
    ) -> None:
        self._outillage = outillage
        self._projets = projets
        self._verificateur = verificateur
        self._correction = correction or CorrectionModele()
        self._lues: dict[tuple[str, str], Analyse] = {}

    # ── lire ────────────────────────────────────────────────────────────────

    def nom(self, projet_id: str) -> str:
        """Le nom déclaré du projet — ce que les phrases du fil en disent."""
        return self._outillage.entite(projet_id).nom

    async def a_ses_fichiers(self, projet_id: str) -> bool:
        """Le projet porte-t-il des fichiers **à lui** — hors de l'outillage de Maestro ?

        C'est ce qui départage les deux chemins : un projet qui a ses fichiers se
        **lit** (#1158), un dossier encore vide se **décrit** (#1147). Les fichiers que
        le manifeste déclare ne comptent pas : ils sont à Maestro, et un projet neuf
        dont on vient d'écrire `AGENTS.md` reste un projet neuf.
        """
        projet = self._outillage.entite(projet_id)
        return await asyncio.to_thread(_a_ses_fichiers, projet)

    async def prochaine(
        self,
        projet_id: str,
        fil: Sequence[MessageChat],
        *,
        acquis: Sequence[Choix] | None = None,
        corrections: Sequence[Choix] = (),
        tranchees: Iterable[tuple[str, str]] = (),
    ) -> PieceProposee | None:
        """La prochaine pièce à proposer — vérifiée, avec son diff —, `None` s'il n'y en a plus.

        La première, dans l'ordre de la recommandation, qui **changerait le disque** et
        qui n'a pas été tranchée **telle quelle** sur ce fil. `acquis` remplace ce que
        le fil a compris (le tour qui conclut le questionnaire ne l'a pas encore
        écrit), `corrections` s'ajoute à celles du fil (la correction qu'on vient de
        comprendre), `tranchees` à ce que le fil a tranché (le geste en cours).

        Bloquant par morceaux — la vérification joue des commandes : joué hors de la
        boucle d'événements.
        """
        matiere = await self._matiere(projet_id, fil, acquis=acquis, corrections=corrections)
        # La dernière décision par chemin (`pieces_tranchees`) — le geste en cours après
        # celles du fil, puisqu'il est la plus récente.
        dernieres = dict(pieces_tranchees(fil))
        dernieres.update(tranchees)
        deja = set(dernieres.items())
        connues = _verdicts_du_fil(fil)
        return await asyncio.to_thread(self._chercher, matiere, deja, connues)

    async def comprendre_correction(
        self, projet_id: str, fil: Sequence[MessageChat], phrase: str
    ) -> CorrectionLue:
        """Ce que `phrase` corrige de l'outillage — le modèle comprend, le code vérifie."""
        matiere = await self._matiere(projet_id, fil)
        return await self._correction.comprendre(
            projet=matiere.projet.nom,
            constats=constats_en_texte(matiere.constats),
            corrections=corrections_en_texte(matiere.corrections),
            phrase=phrase,
        )

    def sans_effet(self, corrections: Sequence[Choix], fil: Sequence[MessageChat]) -> bool:
        """Ces corrections, comprises, ne changent-elles **rien** à ce que l'outillage écrit ?

        Sur un projet **lu**, seuls les sujets corrigeables changent le texte (voir
        `maestro.outillage.correction`) ; sur un projet **décrit**, tout sujet du
        questionnaire compte. Dit plutôt que tu : une correction comprise mais sans
        effet se dit, elle ne passe pas pour appliquée.
        """
        if acquis_du_fil(fil):
            return False
        return not any(c.cle in CLES_CORRIGEABLES for c in corrections)

    # ── écrire ──────────────────────────────────────────────────────────────

    async def ecrire(self, piece: PieceProposee) -> PieceEcrite:
        """Écrit la pièce **telle que la carte la montrait** — et dit ce qui en est advenu.

        Revérifie le disque d'abord : si le fichier a bougé depuis la carte,
        `PieceChangee` et rien n'est écrit. Puis écrit au régime du projet : en place,
        ou sur une branche fusionnée sous l'accord de la carte (`AccordDeLaCarte`).
        Lève les refus motivés de ses couches (`ProjetInconnu`, `RacineRefusee`,
        `EspaceProjetIndisponible`, `ApplicationRefusee`) ; tout le reste est une ligne
        du fait rendu.
        """
        projet = self._outillage.entite(piece.projet_id)
        racine = valider_racine(projet.racine)
        fichier = Fichier(
            chemin=piece.chemin,
            role=piece.role,
            portee=piece.portee,
            contenu=piece.contenu,
            executable=piece.executable,
        )
        frontiere = FrontiereEcriture.pour(racine, projet.perimetre)
        prevision = await asyncio.to_thread(prevoir, racine, fichier, frontiere=frontiere)
        if _empreinte_de(prevision.avant) != piece.empreinte_avant:
            raise PieceChangee(
                f"{piece.chemin} a changé depuis que je vous l'ai montré : je n'écris pas "
                "sur un diff périmé."
            )
        if not prevision.ecrirait:
            return _fait(piece, prevision.etat, prevision.raison, str(racine), REGIME_EN_PLACE)
        if not projet.versionne:
            rapport = await asyncio.to_thread(
                poser_piece,
                racine,
                fichier,
                source=piece.source,
                frontiere=frontiere,
                verifications=piece.verifications,
            )
            ecriture = rapport.ecritures[0] if rapport.ecritures else None
            if rapport.refus or ecriture is None:
                return _fait(piece, "refuse", rapport.refus, str(racine), REGIME_EN_PLACE)
            return _fait(piece, ecriture.etat, ecriture.raison, str(racine), REGIME_EN_PLACE)
        return await self._ecrire_sur_branche(projet, racine, fichier, piece)

    def reporter(self, projet_id: str) -> None:
        """L'outillage entier remis à plus tard — le report de docs/37 §4.6, lu sur la fiche."""
        self._projets.reporter_outillage(projet_id)

    def reprendre(self, projet_id: str) -> None:
        """L'outillage repris : le report est levé, la fiche cesse de le rappeler."""
        self._projets.reprendre_outillage(projet_id)

    async def _ecrire_sur_branche(
        self, projet: Projet, racine: Path, fichier: Fichier, piece: PieceProposee
    ) -> PieceEcrite:
        """Le régime d'un projet versionné : la branche, fusionnée sous l'accord de la carte."""
        tache = nouvel_id_de_generation()

        def preparer() -> PieceEcrite | None:
            with espace_de_travail(projet, tache_id=tache) as espace:
                rapport = poser_piece(
                    espace.path,
                    fichier,
                    source=piece.source,
                    frontiere=FrontiereEcriture.pour(espace.path, projet.perimetre),
                    verifications=piece.verifications,
                )
            ecriture = rapport.ecritures[0] if rapport.ecritures else None
            if rapport.refus or ecriture is None or ecriture.etat != "ecrit":
                etat = ecriture.etat if ecriture is not None else "refuse"
                raison = rapport.refus or (ecriture.raison if ecriture is not None else "")
                return _fait(piece, etat, raison, str(racine), REGIME_BRANCHE)
            return None

        empechee = await asyncio.to_thread(preparer)
        if empechee is not None:
            return empechee
        resultat = await appliquer_sous_validation(
            projet,
            tache_id=tache,
            validateur=AccordDeLaCarte({piece.chemin, CHEMIN_MANIFESTE}),
            branche=branche_de_tache(tache),
            titre=f"Écrire {piece.chemin} dans {projet.nom}",
        )
        if resultat.approuvee:
            return _fait(
                piece,
                "ecrit",
                f"écrit sur la branche {branche_de_tache(tache)}, fusionnée sur votre accord.",
                str(racine),
                REGIME_BRANCHE,
            )
        return _fait(
            piece,
            "refuse",
            resultat.detail or "la fusion n'a pas eu lieu : rien n'a été écrit dans le projet.",
            str(racine),
            REGIME_BRANCHE,
        )

    # ── la matière ──────────────────────────────────────────────────────────

    async def _matiere(
        self,
        projet_id: str,
        fil: Sequence[MessageChat],
        *,
        acquis: Sequence[Choix] | None = None,
        corrections: Sequence[Choix] = (),
    ) -> _Matiere:
        """Les constats du projet à ce tour, corrigés — et ce qu'ils recommandent.

        Un projet **décrit** (le fil porte ce que le questionnaire a compris) : ses
        constats sont ceux des réponses, les sujets hors du corrigeable ajoutés à ce
        qui a été compris. Un projet **lu** : ceux de son analyse (#1158). Dans les
        deux cas, les corrections s'appliquent ensuite par `corriger`, et c'est
        `recommander` — le même pour les deux — qui tranche.
        """
        projet = self._outillage.entite(projet_id)
        racine = await asyncio.to_thread(valider_racine, projet.racine)
        prises = (*corrections_du_fil(fil), *corrections)
        compris = tuple(acquis) if acquis is not None else acquis_du_fil(fil)
        if compris:
            hors = [c for c in prises if c.cle not in CLES_CORRIGEABLES]
            choix = acquis_de([*compris, *hors])
            constats = corriger(constats_depuis_choix(choix), prises)
            source = source_manifeste_des_choix(projet.id, choix)
        else:
            analyse = await self._analyse(projet, fil)
            constats = corriger(analyse.constats, prises)
            source = analyse.source_manifeste()
        return _Matiere(
            projet=projet,
            racine=racine,
            constats=constats,
            recommandation=recommander(constats),
            source=source,
            corrections=prises,
        )

    async def _analyse(self, projet: Projet, fil: Sequence[MessageChat]) -> Analyse:
        """La lecture du projet, **épinglée pour cette conversation** (voir le module)."""
        cle = (projet.id, fil[0].conversation if fil else "")
        lue = self._lues.get(cle)
        if lue is None:
            lue = await self._outillage.analyse_de(projet)
            self._lues[cle] = lue
        return lue

    # ── chercher la pièce suivante ──────────────────────────────────────────

    def _chercher(
        self,
        matiere: _Matiere,
        deja: set[tuple[str, str]],
        connues: dict[str, Verification],
    ) -> PieceProposee | None:
        """La première pièce qui changerait le disque et n'a pas été tranchée — **bloquant**.

        Les verdicts connus sont ceux que le manifeste déclare (une pièce déjà écrite
        se reconnaît « à jour » sans rien rejouer) puis ceux que le fil a vus : une
        commande qu'`AGENTS.md` a fait jouer n'est pas rejouée pour le skill qui
        l'écrit. Une commande **corrigée** a un autre texte : elle est jouée.
        """
        racine, projet = matiere.racine, matiere.projet
        portees = portees_declarees(racine)
        frontiere = FrontiereEcriture.pour(racine, projet.perimetre)
        verdicts_connus = {v.commande: v for v in verifications_declarees(racine)}
        verdicts_connus.update(connues)
        entrees = {e.chemin: e for e in matiere.recommandation.entrees}
        plan = rediger(
            matiere.constats, matiere.recommandation, portees=portees, source=matiere.source
        )
        verificateur = self._verificateur or Verificateur()
        for rang, brouillon in enumerate(plan, start=1):
            entree = entrees.get(brouillon.chemin)
            if entree is None:  # pragma: no cover - `rediger` ne rend que des entrées
                continue
            verdicts = verificateur.verifier(
                racine,
                matiere.constats,
                Recommandation(entrees=(entree,)),
                perimetre=projet.perimetre,
                portees=portees,
                connues=verdicts_connus,
            )
            verdicts_connus.update({v.commande: v for v in verdicts if v.etat in _JOUES})
            fichier = next(
                f
                for f in rediger(
                    matiere.constats,
                    matiere.recommandation,
                    portees=portees,
                    source=matiere.source,
                    verifications=verdicts,
                )
                if f.chemin == brouillon.chemin
            )
            prevision = prevoir(racine, fichier, frontiere=frontiere)
            if not prevision.ecrirait or (fichier.chemin, empreinte(fichier.contenu)) in deja:
                continue
            return _proposee(matiere, entree, fichier, prevision, verdicts, rang, len(plan))
        return None


#: Les verdicts d'une commande **jouée** — les seuls qu'on ne rejoue pas.
_JOUES = frozenset({VERIFIEE, ECHOUEE})


def _proposee(
    matiere: _Matiere,
    entree: Entree,
    fichier: Fichier,
    prevision: Prevision,
    verdicts: Sequence[Verification],
    rang: int,
    total: int,
) -> PieceProposee:
    """La pièce telle que la carte la montrera — et telle que l'accord l'écrira."""
    projet = matiere.projet
    return PieceProposee(
        projet_id=projet.id,
        projet_nom=projet.nom,
        cible=matiere.racine.as_posix(),
        chemin=fichier.chemin,
        nom=entree.nom,
        nature=entree.type,
        raison=_raison_de_la_piece(entree, prevision),
        role=fichier.role,
        portee=fichier.portee,
        contenu=fichier.contenu,
        executable=fichier.executable,
        texte_avant=prevision.avant or "",
        texte_apres=prevision.apres or "",
        empreinte_avant=_empreinte_de(prevision.avant),
        sort=(
            SORT_CREE
            if prevision.avant is None
            else SORT_BLOC
            if prevision.bloc
            else SORT_REECRIT
        ),
        verifications=tuple(verdicts),
        correction=_phrase_portee(matiere.corrections, fichier.contenu),
        echec=_echec_de_correction(matiere.constats, verdicts),
        rang=rang,
        total=total,
        source=matiere.source,
        regime=REGIME_BRANCHE if projet.versionne else REGIME_EN_PLACE,
        corrigees=_commandes_dites(matiere.constats, verdicts),
    )


def _commandes_dites(constats: Constats, verdicts: Sequence[Verification]) -> tuple[str, ...]:
    """Les commandes de cette pièce que la personne a **dites**, dans l'ordre joué."""
    dites = {c.commande for c in constats.commandes if c.origine == ORIGINE_DITE}
    return tuple(v.commande for v in verdicts if v.commande in dites)


def _raison_de_la_piece(entree: Entree, prevision: Prevision) -> str:
    """Pourquoi cette pièce, dit pour **ce que le geste fera** — pas pour l'analyse.

    `Entree.raison` est écrite pour qui lit l'analyse, et suit l'état du projet : un
    fichier que Maestro a déjà posé s'y lit « le projet porte déjà un AGENTS.md :
    Maestro n'y écrirait qu'un bloc », ou « repris tel quel » pour un skill. Sur une
    carte qui propose de le **réécrire**, c'est faux — vu sur la vraie stack, à côté
    du badge « fichier de Maestro modifié ». La raison est alors celle de la pièce
    elle-même (`raison_stable`) ; un fichier neuf ou un bloc gardent la leur.
    """
    if prevision.bloc or prevision.avant is None:
        return entree.raison
    return raison_stable(entree)


def _phrase_portee(corrections: Sequence[Choix], contenu: str) -> str:
    """La phrase de correction que ce contenu **porte** — la plus récente —, vide sinon.

    Lue sur le texte, qui est la seule preuve qu'une correction a touché la pièce :
    une commande dite s'écrit avec sa phrase (`ORIGINE_DITE`). Une correction qui n'a
    rien changé à ce fichier n'y est pas, et la carte ne la lui attribue pas.
    """
    for choisi in reversed(corrections):
        if choisi.parce_que and choisi.parce_que in contenu:
            return choisi.parce_que
    return ""


def _echec_de_correction(constats: Constats, verdicts: Sequence[Verification]) -> str:
    """Pourquoi cette version ne s'écrit pas — une commande **corrigée** a échoué, "" sinon."""
    dites = {c.commande for c in constats.commandes if c.origine == ORIGINE_DITE}
    for verdict in verdicts:
        if verdict.etat == ECHOUEE and verdict.commande in dites:
            return (
                f"`{verdict.commande}` a échoué à l'exécution ({verdict.raison.rstrip('.')})."
            )
    return ""


def _fait(piece: PieceProposee, etat: str, raison: str, cible: str, regime: str) -> PieceEcrite:
    """Le fait d'une pièce tranchée, qui garde l'empreinte de ce qui avait été proposé."""
    return PieceEcrite(
        projet_id=piece.projet_id,
        chemin=piece.chemin,
        nom=piece.nom,
        etat=etat,
        raison=raison,
        cible=cible,
        regime=regime,
        empreinte=empreinte(piece.contenu),
    )


def piece_ecartee(piece: PieceProposee) -> PieceEcrite:
    """Le fait d'une pièce **passée** : rien n'est écrit, et elle ne revient pas telle quelle."""
    return _fait(
        piece,
        PIECE_ECARTEE,
        "écartée à votre demande : rien n'a été écrit.",
        piece.cible,
        piece.regime,
    )


def _empreinte_de(texte: str | None) -> str:
    """L'empreinte d'un texte du disque — vide pour un fichier absent."""
    return empreinte(texte) if texte is not None else ""


def _verdicts_du_fil(fil: Sequence[MessageChat]) -> dict[str, Verification]:
    """Les verdicts **joués** que les pièces du fil ont portés — le plus récent l'emporte."""
    verdicts: dict[str, Verification] = {}
    for message in fil:
        if message.piece is None:
            continue
        for verdict in message.piece.verifications:
            if verdict.etat in _JOUES:
                verdicts[verdict.commande] = verdict
    return verdicts


def _a_ses_fichiers(projet: Projet) -> bool:
    """Le dossier du projet porte-t-il un fichier que Maestro n'a pas déclaré ? — bloquant."""
    racine = valider_racine(projet.racine)
    declares = set(portees_declarees(racine))
    return any(
        relatif not in declares
        for relatif in fichiers_du_perimetre(
            racine, motifs_compiles(projet.perimetre.exclus), hors=(DOSSIER_ATELIER,)
        )
    )
