"""L'espace de travail d'une tâche, **dérivé du projet** (ticket #224, EF-36, D2).

C'est le module qui change ce que fait le moteur. Jusqu'ici une tâche travaillait
dans un `tempfile.mkdtemp()` **vide** (`maestro.sandbox.workspace`) : l'agent ne
voyait jamais le projet de l'utilisateur. Ici il le voit.

La décision **D2** ([docs/24 §2.4](../../docs/24-projets-locaux-et-poste-de-travail.md))
fixait le patron pour les deux cas ; depuis #839 elle ne tient plus que pour le
premier, et le second est un régime à lui (`maestro.sandbox.en_place`) :

- **projet versionné** → un **worktree Git** monté hors de la racine, sur une
  branche dédiée `maestro/<tâche>` créée depuis la branche de base déclarée
  (`Projet.vcs.branche_base`) — ou, pour une tâche qui en reprend une autre (un
  redécoupage, #1396), depuis la branche de celle-ci. En fin de tâche le
  **worktree est retiré, jamais la branche** : c'est elle qui porte le travail
  jusqu'à la fusion, que `maestro.engine.executor` demande dès que la tâche est soldée en succès
  (#705). Pour qu'elle le porte réellement, ce qui reste non commité y est
  **commité avant le démontage** (`_solder_la_branche`) — sans quoi le `--force`
  du retrait l'emporterait et la branche survivrait vide. Ce que la tâche a
  **produit** s'y lit par Git, comme ce que la branche porte
  (`EspaceCopieDeTravail`, #1388) : le `.gitignore` du projet en retire les
  dépendances et la sortie de build. Le worktree porte le nom de sa tâche, jamais
  celui du dossier qu'il copie : le message de la tâche dit donc à l'agent qu'il
  **est** la racine de ce projet (`CopieDuProjet`, #1399) ;
- **projet non versionné** → **la racine elle-même**, en place (#839) : rien
  n'est copié, rien n'est retiré, ce que l'agent écrit est dans le projet
  pendant qu'il l'écrit — avec, depuis #944, un **atelier** `.maestro/<tâche>/`
  où ranger ce qui n'est pas le livrable. La copie du périmètre que D2 prescrivait (option C)
  ne livrait rien — refermée avant qu'un diff puisse être approuvé, 8,80 $ pour
  zéro fichier sur le run `cc2d8e447f83` — et son filet (annuler) n'a pas
  d'objet sur un projet neuf. Ce que la copie garantissait par absence, la
  racine le garantit par **refus** (`FrontiereEcriture`) et le moteur
  **sérialise** les tâches d'un même projet (une seule à la fois dans l'arbre) ;
- **tâche sans projet** → le `mkdtemp()` d'avant, inchangé.

Une tâche **tuée en vol** — Maestro éteint, le process de son run achevé — ne
passe jamais par ce démontage, ou ne l'achève pas. Son travail n'en est pas perdu
pour autant (#1392) : celui qui a éteint le process le porte sur la branche dès
qu'il l'a fait (`sauver_le_travail_en_vol`), et la tâche qui remonte cette branche
**sait** ce qu'elle y retrouve — l'espace le dit à l'agent (`TravailAnterieur`) et
le compte dans ce que la tâche livre. Et un démontage coupé ne laisse jamais une
moitié de worktree (#1447) : à tout instant, le worktree est intact à son adresse,
ou il n'est plus un worktree (`_retirer_worktree`).

Trois invariants tiennent le worktree, et ce sont eux qu'il ne faut pas défaire :

1. **le chemin de travail est hors de la racine** — vérifié, pas supposé
   (`_verifie_hors_racine`), parce qu'un `TMPDIR` posé *dans* le projet suffirait
   à faire du worktree une écriture en place, dans un arbre que la branche ne
   porterait plus ;
2. **la racine est revalidée ici** (`valider_racine`, EF-38) et pas seulement à la
   déclaration : le dépôt des projets est un dossier de fichiers JSON qu'on peut
   éditer à la main (`maestro.projets.store`), donc la dernière porte avant
   qu'un agent n'écrive est celle-ci — elle vaut pour les **deux** régimes ;
3. **aucun lien symbolique n'est suivi** — c'est le vecteur d'évasion nommé par
   docs/24 §2.5 ; le régime en place le tient à l'écriture et au recensement
   (`maestro.sandbox.en_place`).

Le mécanisme est éprouvé sur ce dépôt — `scripts/git/worktree.sh` fait cela pour
les sessions Claude Code (docs/10 §9) — mais le besoin est ici plus étroit : pas
de `.env`, pas de dépendances liées, pas de ports. On s'en inspire, on ne le
réutilise pas.
"""

from __future__ import annotations

import os
import re
import secrets
import shutil
import stat
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from maestro.emplacements import jetable, repertoire_temporaire
from maestro.fichiers import retirer_arbre
from maestro.projets.application import ApplicationRefusee, commiter_en_attente
from maestro.projets.modele import Projet
from maestro.projets.racine import valider_racine
from maestro.sandbox.en_place import EspaceEnPlace, chemin_atelier, ouvre_atelier
from maestro.sandbox.ramassage import PREFIXE_COMMUN, racine_des_espaces
from maestro.sandbox.verification import miroir_de_verification
from maestro.sandbox.workspace import LieuSansTrace, Workspace, isolated_workspace

#: Préfixe des branches de tâche (docs/24 §2.4) : une branche `maestro/<tâche>`
#: par tâche, jamais supprimée par le moteur — elle est le livrable tant que la
#: fusion (lot 7) n'a pas eu lieu.
PREFIXE_BRANCHE = "maestro/"

#: Délai maximum d'une commande Git. Monter un worktree est une opération locale
#: de quelques dizaines de millisecondes ; une minute est une borne d'anomalie
#: (disque réseau, dépôt colossal), pas un budget d'attente.
_DELAI_GIT_S = 60

#: Les pauses entre deux essais d'écarter un worktree de son adresse (#1447,
#: `_ecarter`). Sous Windows, un dossier qu'un process tient encore — son
#: répertoire courant, un fichier ouvert, l'antivirus qui relit ce qu'un
#: `npm install` vient d'écrire — ne se déplace pas ; la plupart de ces prises se
#: relâchent d'elles-mêmes. Une seconde en tout : une extinction n'en accorde que
#: cinq au démontage entier (`DELAI_ANNULATION_S`), et le worktree qu'on n'écarte
#: pas reste intact, ce qui n'est pas une panne.
_PAUSES_ECART_S: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4)

#: Longueur maximale du fragment de nom engendré depuis l'identifiant de tâche —
#: assez pour rester lisible dans `git branch`, assez court pour ne pas crever
#: les limites de chemin de Windows une fois joint au répertoire temporaire.
_LONGUEUR_SLUG = 60

#: Repli de nom quand l'identifiant de tâche ne laisse aucun caractère sûr.
_SLUG_DEFAUT = "tache"

#: Ce que le message d'une tâche reprise nomme de son travail antérieur (#1392) —
#: une borne de **taille de prompt**, pas un tri : au-delà, le compte de ce qui
#: n'est pas nommé est dit, et l'agent le retrouve de toute façon dans son espace.
_FICHIERS_DITS_MAX = 40
_COMMITS_DITS_MAX = 10

#: Ce que dit chaque lettre de `git diff --name-status` ; une lettre absente d'ici
#: est rendue telle que Git l'écrit, jamais tue.
_STATUTS_GIT = {"A": "ajouté", "M": "modifié", "D": "supprimé", "T": "type changé"}

#: Ce que le message d'une tâche dit de sa **copie de travail** (#1401) : ce que
#: l'agent y laisse est commité, brouillons compris, et où vont les brouillons pour
#: ne pas l'être. Voir `EspaceCopieDeTravail.consigne_espace`.
CONSIGNE_COPIE_DE_TRAVAIL = (
    "Dans cette copie de travail du projet, tout ce que tu laisses et que le "
    "`.gitignore` du projet n'ignore pas est commité sur ta branche à la fin de ta "
    "tâche — brouillons compris —, puis appliqué au projet avec elle. Ce qui n'est pas "
    "le livrable — brouillons, essais, harnais de vérification, notes — ne se laisse "
    "donc pas ici : mets-le dans un dossier que tu crées par `mktemp -d`, dans la "
    "commande même qui s'en sert, hors de cette copie — un nom fixe du répertoire "
    "temporaire du système sort de ton espace et revient à une personne. Ce qui mérite "
    "d'être relu, dis-le dans ton compte-rendu."
)


@dataclass(frozen=True)
class TravailAnterieur:
    """Ce que la branche d'une tâche porte **avant** qu'elle ne démarre (#1392).

    Une branche `maestro/<tâche>` qui existe déjà au montage est celle d'une
    exécution précédente de **la même tâche** : interrompue en vol (Maestro éteint
    pendant qu'elle écrivait, son travail sauvé par `sauver_le_travail_en_vol`),
    en échec, ou livrée sans que sa fusion n'ait eu lieu. Son travail est sous les
    pieds de l'agent — un worktree est une copie de sa branche —, mais rien ne le
    lui disait : il le prenait pour le projet, ou le refaisait. `reference` est ce
    à quoi on compare — la branche que la tâche reprend (#1396) ou la base du
    projet —, `commits` les sujets de ce que la branche a de plus (bornés à
    `_COMMITS_DITS_MAX`, `nb_commits` les compte tous), `fichiers` ce qu'ils
    changent, `(statut, chemin)`, triés.
    """

    branche: str
    reference: str
    nb_commits: int
    commits: tuple[str, ...]
    fichiers: tuple[tuple[str, str], ...]

    @property
    def chemins(self) -> frozenset[str]:
        """Les chemins que ce travail a touchés, relatifs à la racine du worktree."""
        return frozenset(chemin for _, chemin in self.fichiers)

    def consigne(self) -> str:
        """Le paragraphe qui dit à l'agent ce qui est déjà fait, et qu'il repart de là."""
        depuis = (
            "la base du projet" if self.reference == "HEAD" else f"`{self.reference}`"
        )
        sujets = ", ".join(f"« {sujet} »" for sujet in self.commits)
        reste = self.nb_commits - len(self.commits)
        if reste > 0:
            sujets += f" et {reste} autre(s)"
        lignes = [
            f"Ta branche `{self.branche}` n'est pas neuve : une exécution précédente "
            f"de cette tâche y a laissé du travail que {depuis} n'a pas, et ton "
            f"répertoire le porte déjà — {self.nb_commits} commit(s) ({sujets}), "
            f"{len(self.fichiers)} fichier(s) :"
        ]
        lignes += [
            f"- {_STATUTS_GIT.get(statut, statut)} : `{chemin}`"
            for statut, chemin in self.fichiers[:_FICHIERS_DITS_MAX]
        ]
        if len(self.fichiers) > _FICHIERS_DITS_MAX:
            lignes.append(f"- et {len(self.fichiers) - _FICHIERS_DITS_MAX} autre(s)")
        lignes.append(
            "Pars de cet état : relis ce qui est fait, garde ce qui tient, termine ce "
            "qui manque — ne le refais pas depuis zéro."
        )
        return "\n".join(lignes)


@dataclass(frozen=True)
class CopieDuProjet:
    """Ce que le worktree d'une tâche **est** pour l'agent : la racine du projet, en copie (#1399).

    Constaté sur p5 (2026-10-01) : la tâche `socle-nextjs` disait « Installer dans
    le dossier p5 », « Commence par inspecter p5 », et l'agent travaillait dans
    `…/maestro-dev-…/socle-nextjs`, où aucun `p5` n'existe. Ses deux agents ont dû
    consigner une décision pour l'interpréter — « le dossier p5 est la racine du
    worktree courant ». Rien ne leur disait que leur répertoire courant **est** ce
    projet, ni que « la racine du projet » de son `AGENTS.md` est ce répertoire : un
    worktree porte le nom de sa tâche, jamais celui du dossier qu'il copie.

    Ce n'est pas au texte de la tâche de changer — aucune réécriture lexicale de ce
    qu'il nomme : le projet peut s'y dire de mille façons, et l'agent qui sait où il
    est les reconnaît toutes. C'est à l'espace de le **dire**, depuis ce qu'il sait
    seul : le projet par son nom et son dossier (`Projet`), la branche qu'il monte.

    `dossier` est la racine **d'origine**, nommée pour être reconnue — une tâche peut
    la citer en toutes lettres — et pour qu'on n'y écrive pas : ce qui s'écrit là
    n'est pas sur la branche de la tâche, donc ni fusionné ni défait avec elle.
    """

    nom: str
    dossier: str
    branche: str

    @classmethod
    def de(cls, projet: Projet, branche: str) -> CopieDuProjet:
        """La copie du projet `projet` montée sur `branche`."""
        return cls(nom=projet.nom.strip(), dossier=projet.racine, branche=branche)

    def consigne(self) -> str:
        """Le paragraphe qui dit à l'agent où il est, et ce que devient ce qu'il y laisse."""
        nom_du_dossier = Path(self.dossier).name
        projet = self.nom or nom_du_dossier
        # Le nom du projet et celui de son dossier, une seule fois s'ils se confondent.
        designes = " ou ".join(
            f"« {nom} »" for nom in dict.fromkeys((projet, nom_du_dossier)) if nom
        )
        return (
            f"Ton répertoire courant est la **racine du projet « {projet} »** : une copie "
            f"de travail de son dossier `{self.dossier}`, sur ta branche `{self.branche}`. "
            f"Quand ta tâche nomme ce projet ou son dossier ({designes}), c'est **ce "
            "répertoire** qu'elle désigne : travailles-y directement, sans y créer de "
            "sous-dossier de ce nom, et sans écrire dans le dossier d'origine — ce qui "
            "s'y écrirait échapperait à ta branche. Les chemins que ses instructions "
            "disent relatifs à « la racine du projet » partent d'ici. Ce que tu y "
            "laisses est commité sur ta branche à la fin de ta tâche, et c'est cette "
            "branche qui est appliquée au projet."
        )


class EspaceProjetIndisponible(RuntimeError):
    """L'espace de travail dérivé n'a pas pu être monté — **avec son motif**.

    Même parti pris que `RacineRefusee` (EF-38) : un refus porte un code court et
    stable (`git-indisponible`, `worktree-refuse`, `espace-dans-la-racine`,
    `enumeration-refusee`), pas seulement une phrase. L'exécuteur en fait un échec
    de tâche consigné (`maestro.engine.executor`), donc **visible** — là où un
    repli silencieux sur un répertoire vide ferait travailler l'agent dans le
    vide sans que personne ne l'apprenne.
    """

    def __init__(self, motif: str, message: str) -> None:
        super().__init__(message)
        self.motif = motif


@dataclass(frozen=True)
class EspaceCopieDeTravail(Workspace):
    """Le worktree d'une tâche, dont les fichiers sont **ceux que Git voit** (#1388).

    Le recensement d'un espace (`Workspace.fichiers`) parcourait tout le disque, et
    l'empreinte du worktree est prise **avant** que l'agent ne travaille : tout ce
    qu'un `npm install` ou un `next build` dépose ressortait donc en « fichier
    produit ». Mesuré sur le run `da0a8ae6f1b2` (projet p5, 2026-10-01) : 22 891
    fichiers de `node_modules/` et 483 de `.next/` au prompt du juge, qui dépassait
    le million de tokens et jetait une tâche réussie — pendant que la branche, elle,
    ne portait que les 31 fichiers de l'agent, parce que Git avait respecté le
    `.gitignore`.

    L'énumération est donc celle de Git : les fichiers **suivis**, et les **non
    suivis que le projet n'ignore pas** (`git ls-files --cached --others
    --exclude-standard`) — l'ensemble que `git add -A` porte sur la branche au
    démontage (`_solder_la_branche`) et que le diff présente (`maestro.projets.
    application._diff_worktree`). Le juge, le résultat de la tâche, le rapport du
    run et le rassemblement d'un redécoupage lisent ainsi ce que la branche
    livrera, et rien d'autre. Le point unique d'énumération de `Workspace` tient :
    `derive` et `produced_files` passent tous deux par ici.

    Le `.gitignore` est celui du **projet** — c'est lui, et seulement lui, qui dit
    ce qui n'est pas un livrable ; aucun catalogue de dossiers n'est écrit ici. Un
    projet qui n'ignore rien verra donc ses dépendances recensées, exactement comme
    il les verrait commitées. Un lien symbolique n'est pas lu (invariant 3 du
    module) : son contenu ne vit pas dans l'espace.

    Si Git ne répond pas, c'est une **erreur motivée** (`EspaceProjetIndisponible`),
    jamais un repli sur tout le disque : c'est ce repli qui faisait le défaut.

    `anterieur` (#1392) est le travail qu'une exécution précédente de la tâche a
    laissé sur sa branche, `None` sur une branche neuve. Il change deux choses, et
    seulement quand il est là : le message de la tâche le **dit** à l'agent
    (`consigne_espace`), et ses fichiers ne sont **pas** relevés au départ — ils
    sont le travail de cette tâche, fait par une tentative précédente, et la
    livraison que lit le juge les porte comme si elle les avait écrits d'un trait.
    Sans cela, une tâche reprise qui ne réécrit rien de ce qui est fait livrerait
    une moitié.

    `copie` (#1399) est ce que ce worktree est pour l'agent — la racine de quel
    projet, sur quelle branche (`CopieDuProjet`) —, et le message de la tâche le
    dit **toujours**, avant ce que la branche porte déjà. `None` pour un espace
    monté hors d'`espace_de_travail`, qui ne sait d'aucun projet qu'il le copie.
    """

    anterieur: TravailAnterieur | None = None
    copie: CopieDuProjet | None = None

    def consigne_espace(self) -> str:
        """Où l'agent est (#1399), ce que devient ce qu'il laisse (#1401), ce que sa branche porte.

        Renverse #944 pour la copie de travail : « un worktree n'a rien de
        particulier à dire » tenait tant qu'on le croyait une copie conforme dont
        l'agent n'avait rien à savoir. Il a à savoir qu'elle **est** le projet que
        sa tâche nomme — sur p5, faute de le lire, l'agent a cherché le dossier.

        ⚠ **Ce n'est plus vide sur une branche neuve.** Tout ce que l'agent laisse
        ici et que le `.gitignore` du projet n'ignore pas est commité au démontage
        (`_solder_la_branche`, `git add -A`), puis fusionné dans le projet —
        brouillons compris. Sur p5, rien ne le lui disait, et aucun atelier n'est
        ouvert ici (#944, réservé au projet non versionné) : le message le dit donc
        (`CONSIGNE_COPIE_DE_TRAVAIL`), et envoie ce qui n'est pas le livrable dans un
        dossier que l'agent crée par `mktemp -d`, hors de la copie — le geste que la
        portée lui laisse (`maestro.portee`, « l'établi »), le même qu'en place pour
        ce que personne n'aura à relire (#1348).

        Ce que le `.gitignore` retire, et ce qui s'y ajoute, est dit **une fois pour
        tous les espaces** par le cadre d'exécution (`_cadre_outille.md`) : ce
        message ne le redit pas, il dit ce que le cadre ne peut pas savoir — que
        cet espace-ci se commite.
        """
        paragraphes = (
            self.copie.consigne() if self.copie is not None else "",
            CONSIGNE_COPIE_DE_TRAVAIL,
            self.anterieur.consigne() if self.anterieur is not None else "",
        )
        return "\n\n".join(paragraphe for paragraphe in paragraphes if paragraphe)

    def _releve(self) -> dict[str, tuple[int, int]]:
        """L'empreinte de départ, **moins** ce que la tâche avait déjà fait (#1392)."""
        releve = super()._releve()
        if self.anterieur is not None:
            for chemin in self.anterieur.chemins:
                releve.pop(chemin, None)
        return releve

    def fichiers(self) -> Iterator[Path]:
        """Les fichiers que Git voit dans le worktree, triés par chemin."""
        resultat = _git(
            self.path, "ls-files", "-z", "--cached", "--others", "--exclude-standard"
        )
        if resultat.returncode != 0:
            raise EspaceProjetIndisponible(
                "enumeration-refusee",
                f"Git n'a pas pu énumérer les fichiers de l'espace {self.path} : "
                f"{_message_git(resultat)}",
            )
        # Un ensemble : `--cached` rend une entrée par étage d'un fichier en conflit.
        relatifs = {champ for champ in resultat.stdout.split("\0") if champ}
        for fichier in sorted(self.path / relatif for relatif in relatifs):
            # Un fichier suivi que l'agent a supprimé reste dans l'index : il n'est
            # plus un fichier de l'espace, et ne s'est jamais recensé comme tel.
            if fichier.is_file() and not fichier.is_symlink():
                yield fichier

    @contextmanager
    def sans_trace(self) -> Iterator[LieuSansTrace]:
        """Ce que le bloc laisse dans la copie, et que la branche porterait, est défait (#1448).

        Le bloc joue dans la copie elle-même (`LieuSansTrace.chemin` est `path`) : elle
        est déjà l'environnement de l'agent, dépendances installées comprises, et ce
        qui s'y écrit se défait — à la différence de la racine d'un projet non
        versionné, qui fait jouer le sien dans un miroir (#1496).

        La vérification d'une tâche (#1177) joue ses commandes ici, l'espace encore
        ouvert, et son prompt lui demande de constater sans rien modifier. Ce n'est
        pas une garantie : sur le run `3a7fa26ba927`, un essai de Vitest avec le
        rapporteur `json` a écrit `.vitest/json/output.json`, que `create-next-app`
        n'ignore pas — `_solder_la_branche` l'a commité sur `maestro/tests-donnees`,
        et la fusion l'a appliqué au projet. La branche d'une tâche porte le travail
        de l'agent, et rien de ce que Maestro a joué pour le juger.

        D'où un **instantané**, pris en entrant, de ce que la branche porterait —
        exactement ce que `git add -A` indexe, `.gitignore` du projet compris
        (`_Instantane`) —, rendu en sortant : ce qui est apparu est retiré, ce qui a
        changé ou disparu revient tel qu'il était, date comprise. L'agent dont la
        livraison ne tient pas repart donc de ses fichiers tels qu'il les a laissés,
        et c'est ce que son retour lui promet (`Recette.retour`). Ce que le projet
        ignore n'est pas touché : les dépendances qu'un contrôle installe ne
        partent sur aucune branche, et l'agent les trouve à son retour.

        Lève `EspaceProjetIndisponible` si l'instantané ne se prend pas, ou si une
        part de la copie n'a pas pu être rendue — un reste que la branche porterait
        se nomme, il ne se tait pas. Pas quand le bloc lève déjà : c'est sa cause qui
        condamne la tâche, et elle ne doit pas être masquée.
        """
        instantane = _Instantane.prendre(self)
        try:
            yield LieuSansTrace(self.path)
        except BaseException:
            instantane.rendre()
            raise
        restes = instantane.rendre()
        if restes:
            raise EspaceProjetIndisponible(
                "trace-de-verification",
                f"Maestro n'a pas pu défaire, dans la copie de travail {self.path}, ce "
                "qu'il y a joué pour vérifier la tâche — sa branche le porterait : "
                + " ; ".join(restes),
            )


#: Le préfixe du dossier jetable qui porte l'index d'un instantané (#1448) — sous le
#: préfixe commun des espaces, donc ramassé si son process meurt avant de le retirer.
PREFIXE_INSTANTANE = "maestro-instantane-"


@dataclass(frozen=True)
class _Instantane:
    """Ce que la branche d'une copie de travail porterait, pris à un instant (#1448).

    Le contenu vit dans un **index Git à part** (`GIT_INDEX_FILE`), copié de celui
    du worktree puis mis à jour par `git add -A` : Git ne relit que ce qui a changé
    depuis son dernier passage, et l'ensemble capturé est celui que le démontage
    commiterait (`_solder_la_branche`). Ni l'index du worktree, ni sa branche, ni
    ses commits ne sont touchés. Les dates de chaque fichier sont relevées à côté :
    un fichier rendu à son contenu mais pas à sa date ressortirait en « fichier
    produit » à la livraison suivante (`Workspace.produced_files`).
    """

    racine: Path
    dossier: Path
    dates: dict[str, tuple[int, int]]

    @property
    def index(self) -> Path:
        """L'index qui porte le contenu de l'instantané."""
        return self.dossier / "index"

    @classmethod
    def prendre(cls, espace: EspaceCopieDeTravail) -> _Instantane:
        """L'instantané de `espace` — `EspaceProjetIndisponible` si Git le refuse."""
        dossier = jetable(PREFIXE_INSTANTANE)
        try:
            source = _git(espace.path, "rev-parse", "--git-path", "index")
            index = Path(source.stdout.strip())
            if source.returncode == 0 and not index.is_absolute():
                index = espace.path / index
            # Sans l'index du worktree, Git relit tout ce qu'il ne suit pas — plus
            # lent, aussi juste.
            if source.returncode == 0 and index.is_file():
                shutil.copyfile(index, dossier / "index")
            indexe = _git(espace.path, "add", "-A", index=dossier / "index")
            if indexe.returncode != 0:
                raise EspaceProjetIndisponible(
                    "instantane-refuse",
                    f"Git n'a pas pu relever l'état de la copie de travail {espace.path} "
                    f"avant de la vérifier : {_message_git(indexe)}",
                )
            dates: dict[str, tuple[int, int]] = {}
            for fichier in espace.fichiers():
                try:
                    etat = fichier.stat()
                except OSError:
                    continue  # illisible : son contenu sera rendu, pas sa date
                dates[fichier.relative_to(espace.path).as_posix()] = (
                    etat.st_atime_ns,
                    etat.st_mtime_ns,
                )
        except BaseException:
            retirer_arbre(dossier)
            raise
        return cls(racine=espace.path, dossier=dossier, dates=dates)

    def rendre(self) -> tuple[str, ...]:
        """Rend la copie telle qu'à l'instantané, et nomme ce qui a résisté. Ne lève jamais.

        Ce qui est apparu sans que le projet l'ignore est retiré d'abord, avec les
        dossiers qu'il laisse vides — un fichier remplacé par un dossier retrouve
        ainsi sa place —, puis ce qui a changé ou disparu est réécrit depuis
        l'index (`git checkout-index`), et sa date remise.
        """
        try:
            apparus = self._chemins("ls-files", "-z", "--others", "--exclude-standard")
            changes = self._chemins("diff-files", "-z", "--name-only")
            restes = [
                relatif
                for relatif in sorted(apparus, key=lambda r: r.count("/"), reverse=True)
                if not _retirer(self.racine, relatif)
            ]
            if changes:
                rendus = _git(
                    self.racine, "checkout-index", "-f", "-z", "--stdin",
                    index=self.index, entree="\0".join(changes),
                )
                if rendus.returncode != 0:
                    restes.append(f"réécriture refusée ({_message_git(rendus)})")
                for relatif in changes:
                    if relatif in self.dates:
                        try:
                            os.utime(self.racine / relatif, ns=self.dates[relatif])
                        except OSError:
                            pass  # le contenu est rendu ; la date n'est pas la branche
            return tuple(restes)
        except (EspaceProjetIndisponible, OSError) as panne:
            return (str(panne),)
        finally:
            retirer_arbre(self.dossier)

    def _chemins(self, *arguments: str) -> list[str]:
        """Les chemins que rend `git <arguments>` sur l'index de l'instantané."""
        resultat = _git(self.racine, *arguments, index=self.index)
        if resultat.returncode != 0:
            raise EspaceProjetIndisponible(
                "instantane-illisible",
                f"Git n'a pas pu comparer la copie à son instantané : {_message_git(resultat)}",
            )
        return [champ for champ in resultat.stdout.split("\0") if champ]


def _retirer(racine: Path, relatif: str) -> bool:
    """Retire `relatif` de `racine`, puis ses dossiers restés vides — `False` s'il résiste.

    Un chemin qui finit par `/` est un dépôt imbriqué, que Git ne descend pas : il
    part entier. Un fichier tenu un instant — l'antivirus relit ce qu'un contrôle
    vient d'écrire — est réessayé après les mêmes pauses qu'un worktree à écarter.
    """
    chemin = racine / relatif.rstrip("/")
    if relatif.endswith("/"):
        if not retirer_arbre(chemin):
            return False
    else:
        for pause in (*_PAUSES_ECART_S, None):
            try:
                os.chmod(chemin, stat.S_IWRITE)
                chemin.unlink(missing_ok=True)
                break
            except FileNotFoundError:
                break
            except OSError:
                if pause is None:
                    return False
                time.sleep(pause)
    parent = chemin.parent
    while parent != racine and racine in parent.parents:
        try:
            parent.rmdir()
        except OSError:
            break  # pas vide, ou tenu : il existait, ou porte autre chose
        parent = parent.parent
    return True


@contextmanager
def espace_de_travail(
    projet: Projet | None = None,
    *,
    tache_id: str = "",
    reprend: str = "",
    prefix: str = "maestro-dev-",
    keep: bool = False,
) -> Iterator[Workspace]:
    """Ouvre l'espace de travail de la tâche et le referme en sortie (EF-36).

    `projet=None` — une tâche sans `projet_id` — rend exactement l'espace jetable
    d'avant (`isolated_workspace`) : ce chemin-là ne change pas. Sinon l'espace
    est **dérivé** du projet : worktree Git **hors de la racine** s'il est
    versionné, **la racine elle-même** sinon (#839, `maestro.sandbox.en_place`).

    `reprend` (#1396) nomme la tâche dont celle-ci reprend le travail
    (`Task.reprend`, posé aux tâches de tête d'un redécoupage) : sur un projet
    versionné, une branche **créée** ici part de celle de cette tâche, si elle
    existe, plutôt que de la base — ce qui est fait n'est pas refait. Il ne joue
    qu'à la création : une branche qui existe déjà se reprend telle quelle. Sans
    objet en place et sans projet.

    Une branche qui existe déjà porte le travail d'une exécution précédente de la
    tâche (#1392) — celle qu'une extinction a coupée, typiquement, et que la
    reprise du run (#1391) rejoue sous le même identifiant. Ce qu'elle a de plus
    que la branche reprise ou la base est relevé au montage (`TravailAnterieur`)
    et porté par l'espace, qui le dit à l'agent et le compte dans sa livraison.

    `keep=True` conserve l'espace (et, pour un projet versionné, le worktree
    monté) : l'inspection après coup en a besoin, et c'est alors à l'appelant de
    faire le ménage — y compris de décider ce qu'il advient du travail non
    commité, que le démontage aurait porté sur la branche. Sinon tout est démonté
    **même en cas d'exception** — le travail encore en attente est d'abord
    **commité sur la branche de la tâche** (`_solder_la_branche`, #705), puis le
    worktree est retiré — écarté de son adresse d'un seul geste avant d'être
    effacé, pour qu'une extinction qui coupe le démontage ne laisse pas de coquille
    (`_retirer_worktree`, #1447) —, jamais par une suppression de branche ; un
    worktree qu'un process tient encore reste monté, intact. En place, `keep` n'a
    pas d'objet : la racine n'est **jamais** démontée ni nettoyée, une exception y
    laisse ce qui a été écrit — c'est le critère du ticket (« rien n'est perdu à
    la fermeture de l'espace »), et le `rmtree` d'un `finally` est précisément ce
    qui a effacé le livrable de `squelette-p1` le 2026-08-28 (#568). Seul le
    **miroir** où ses vérifications ont joué est retiré (#1496) : il est sous la
    racine jetable, jamais dans la racine.

    Lève `RacineRefusee` si la racine du projet n'est plus admissible (EF-38) —
    dans les deux régimes, c'est la dernière porte avant qu'un agent n'écrive —
    et `EspaceProjetIndisponible` si le montage du worktree échoue (Git absent,
    worktree refusé, répertoire temporaire situé dans la racine).
    """
    if projet is None:
        with isolated_workspace(prefix=prefix, keep=keep) as ws:
            yield ws
        return

    racine = valider_racine(projet.racine)
    if not projet.versionne:
        # L'atelier de la tâche (#944) est ouvert **avant** la dérivation : c'est
        # de lui que dépend ce que `fichiers` énumère, donc l'empreinte de départ.
        atelier = chemin_atelier(_slug(tache_id))
        ouvre_atelier(racine, atelier)
        # `derive` et non `EspaceEnPlace(path=…)` : la racine n'est pas vide, et
        # sans cette empreinte de départ tout le projet de l'utilisateur
        # ressortirait en « fichiers produits » du rapport de run. Le miroir où se
        # jouent ses vérifications (#1496) vit le temps de l'espace : il naît à la
        # première, se resynchronise aux suivantes, et part avec l'espace — la
        # racine, elle, n'est toujours ni démontée ni nettoyée.
        with miroir_de_verification(racine) as miroir:
            yield EspaceEnPlace.derive(
                racine, perimetre=projet.perimetre, atelier=atelier, miroir=miroir
            )
        return

    # La racine jetable (#1455) et non le défaut de `tempfile` (#992, S13) : la
    # même racine que l'espace jetable, sous ce que le Bash de l'agent appelle
    # `/tmp` sous Windows. Le parent est marqué du pid de ce process comme tout
    # jetable : le ramassage n'emporte jamais pour autant un worktree dont le
    # dépôt existe, dont le travail non commité ne vit nulle part ailleurs
    # (`ramassage.porte_un_worktree`) — le marqueur ne sert qu'aux dépouilles
    # qui n'en portent plus, montage raté ou dépôt disparu.
    parent = jetable(prefix)
    chemin = parent / _slug(tache_id)
    branche = _branche(tache_id)
    depart = _branche(reprend) if reprend.strip() else ""
    monte = False
    try:
        _verifie_hors_racine(chemin, racine)
        # Lu **avant** le montage, qui crée la branche quand elle manque : seule une
        # branche qui existait déjà porte le travail d'une exécution précédente.
        existait = _ref_existe(racine, branche)
        _monter_worktree(racine, chemin, branche, _base(projet), depart=depart)
        monte = True
        # **Après** le montage, jamais avant : il vient de porter sur la branche ce
        # qu'un worktree abandonné retenait encore (`_liberer_la_branche`).
        anterieur = (
            _travail_anterieur(racine, branche, _reference(racine, projet, depart))
            if existait
            else None
        )
        yield EspaceCopieDeTravail.derive(
            chemin, anterieur=anterieur, copie=CopieDuProjet.de(projet, branche)
        )
    finally:
        if not keep:
            libre = True
            if monte:
                _solder_la_branche(chemin, branche)
                libre = _retirer_worktree(racine, chemin)
            # Un worktree resté intact (#1447) ne s'efface pas avec son parent : ce
            # serait refaire, à la main, la coquille que son retrait vient d'éviter.
            if libre:
                retirer_arbre(parent)


def sauver_le_travail_en_vol(projet: Projet, tache_id: str) -> bool:
    """Porte sur sa branche ce qu'une tâche **tuée en vol** a laissé dans son worktree (#1392).

    Le démontage d'`espace_de_travail` commite le travail en attente avant de
    retirer le worktree (#705) — mais il vit dans un `finally`, et un `finally` ne
    tourne que dans un process qui vit encore. Constaté sur p5 et rejoué par S12 :
    Maestro éteint pendant qu'une tâche écrit, l'hôte du run est achevé avec sa
    descendance au bout de `DELAI_ANNULATION_S`, et ce que la tâche avait écrit
    restait dans un worktree du répertoire temporaire, hors de toute branche,
    jusqu'à ce qu'une reprise vienne le chercher — ou jamais.

    C'est donc **celui qui a éteint le process** qui joue le geste, une fois
    qu'il l'a fait (`maestro.controltower.executions`) : le même commit, par la même
    orthographe (`commiter_en_attente`), sur les seuls worktrees qui ont la forme
    exacte de ceux qu'`espace_de_travail` monte pour **cette** tâche — jamais celui
    qu'une personne aurait ouvert elle-même (`_worktrees_abandonnes`). Le worktree
    reste monté : le retirer n'ajoute rien à la sauvegarde, et c'est le montage
    suivant de la tâche qui le libère (`_liberer_la_branche`). Un worktree dont Git
    dit l'adresse vide ou sans `.git` (`prunable`, #1447) n'en est pas un : rien n'y
    est commité (`_worktrees`).

    Rend `True` si un worktree de la tâche a été trouvé — son travail est alors sur
    la branche —, `False` s'il n'y en avait pas : projet non versionné (la tâche
    écrit en place, rien n'est hors du projet), ou démontage qui a eu le temps de
    se faire. Lève `RacineRefusee` si la racine n'est plus admissible,
    `EspaceProjetIndisponible` si Git manque, `ApplicationRefusee` si Git refuse
    le commit (un `pre-commit` du projet, un index verrouillé) : l'appelant le dit,
    ce n'est pas ici qu'on le tait.
    """
    if not projet.versionne:
        return False
    racine = valider_racine(projet.racine)
    branche = _branche(tache_id)
    trouve = False
    for abandonne in _worktrees_abandonnes(racine, branche):
        commiter_en_attente(abandonne, branche)
        trouve = True
    return trouve


def branche_de_tache(tache_id: str) -> str:
    """Le nom de la branche dédiée à la tâche `tache_id` — `maestro/<tâche>`.

    Exposé parce que la fusion (lot 7) et l'interface ont besoin de nommer cette
    branche sans rejouer l'assainissement : c'est ici qu'il vit.
    """
    return _branche(tache_id)


def _branche(tache_id: str) -> str:
    """`maestro/<tâche>`, fragment assaini (cf. `_slug`)."""
    return f"{PREFIXE_BRANCHE}{_slug(tache_id)}"


def _slug(tache_id: str) -> str:
    """L'identifiant de tâche réduit à ce qu'un nom de branche et de dossier accepte.

    Ne garde que `[A-Za-z0-9_-]` : ce jeu exclut d'un coup tout ce que Git refuse
    dans un nom de branche (espace, `~^:?*[`, `\\`, `..`, `@{`, suffixe `.lock`)
    et tout ce qui permettrait une traversée de chemin depuis un identifiant venu
    de l'extérieur — le point est écarté avec le reste, ce qui ferme les deux
    sujets d'un même filtre.
    """
    reduit = re.sub(r"[^A-Za-z0-9_-]+", "-", tache_id.strip()).strip("-_")
    reduit = re.sub(r"-{2,}", "-", reduit)[:_LONGUEUR_SLUG].strip("-_")
    return reduit or _SLUG_DEFAUT


def _base(projet: Projet) -> str:
    """La branche de base déclarée du projet, "" si le dépôt était en HEAD détaché."""
    return projet.vcs.branche_base if projet.vcs is not None else ""


def _reference(racine: Path, projet: Projet, depart: str) -> str:
    """Ce à quoi comparer la branche d'une tâche pour dire ce qu'elle a fait (#1392).

    La branche qu'elle reprend (#1396) si elle existe — le travail de celle-là
    n'est pas le sien —, sinon la base déclarée, sinon la tête du projet : les
    trois points d'où `_monter_worktree` a pu la faire naître, dans le même ordre.
    """
    for candidate in (depart, _base(projet)):
        if candidate and _ref_existe(racine, candidate):
            return candidate
    return "HEAD"


def _travail_anterieur(racine: Path, branche: str, reference: str) -> TravailAnterieur | None:
    """Ce que `branche` porte de plus que `reference` — `None` si rien, ou si Git ne dit rien.

    Les commits sont ceux de `reference..branche`, les fichiers ceux que `branche`
    change depuis son point de départ (`reference...branche`, trois points) : une
    base qui a avancé depuis — les fusions des tâches voisines — n'y entre pas.
    Une branche dont les commits ne changent plus rien (un travail défait) ne porte
    rien qui vaille d'être dit. Muet sur un refus de Git comme sur un Git qui ne
    répond plus : ce relevé informe l'agent, il ne doit pas faire échouer le
    montage qui vient de réussir.
    """
    try:
        return _releve_anterieur(racine, branche, reference)
    except EspaceProjetIndisponible:
        return None


def _releve_anterieur(racine: Path, branche: str, reference: str) -> TravailAnterieur | None:
    """Le corps de `_travail_anterieur`, qui laisse passer un Git qui ne répond pas."""
    plage = f"{reference}..refs/heads/{branche}"
    compte = _git(racine, "rev-list", "--count", plage)
    if compte.returncode != 0 or not compte.stdout.strip().isdigit():
        return None
    nb_commits = int(compte.stdout.strip())
    if nb_commits == 0:
        return None
    sujets = _git(racine, "log", f"-n{_COMMITS_DITS_MAX}", "--format=%s", plage)
    difference = _git(
        racine,
        "diff",
        "--name-status",
        "--no-renames",
        "-z",
        f"{reference}...refs/heads/{branche}",
    )
    if sujets.returncode != 0 or difference.returncode != 0:
        return None
    # `-z` : « statut\0chemin\0 » par fichier, sans les guillemets dont Git
    # entoure un nom non ASCII — c'est le chemin tel que le worktree l'écrit.
    champs = [champ for champ in difference.stdout.split("\0") if champ]
    paires = zip(champs[::2], champs[1::2], strict=False)
    fichiers = tuple(sorted(paires, key=lambda paire: paire[1]))
    if not fichiers:
        return None
    return TravailAnterieur(
        branche=branche,
        reference=reference,
        nb_commits=nb_commits,
        commits=tuple(ligne for ligne in sujets.stdout.splitlines() if ligne.strip()),
        fichiers=fichiers,
    )


def _verifie_hors_racine(chemin: Path, racine: Path) -> None:
    """Refuse un worktree situé **dans** la racine du projet (critère #224).

    Le cas paraît impossible — le chemin sort de `mkdtemp()` — mais il ne l'est
    pas : un `TMPDIR`/`TEMP` pointant dans le projet suffit, et le worktree
    deviendrait alors une écriture en place dans un arbre que la branche ne
    porte pas — exactement ce que D2 écarte pour un projet versionné. Le régime
    en place (#839) ne passe pas par ici : sa racine **est** son espace.
    """
    try:
        chemin.resolve().relative_to(racine)
    except ValueError:
        return
    raise EspaceProjetIndisponible(
        "espace-dans-la-racine",
        f"Espace de travail refusé : {chemin} est dans la racine du projet "
        f"({racine}) — vérifiez TMPDIR/TEMP.",
    )


# --------------------------------------------------------------------------- #
# Projet versionné : un worktree Git par tâche
# --------------------------------------------------------------------------- #


def _monter_worktree(
    racine: Path, chemin: Path, branche: str, base: str, *, depart: str = ""
) -> None:
    """Monte le worktree de la tâche en `chemin`, sur la branche `branche`.

    La branche est **créée** depuis `base` si elle n'existe pas, **reprise** telle
    quelle sinon : une tâche rejouée retrouve son travail plutôt que de l'écraser
    — conséquence directe de « le worktree est retiré, jamais la branche ». Un
    `worktree prune` préalable libère les enregistrements dont le répertoire a
    disparu (session tuée, disque nettoyé), sans quoi Git refuserait de réutiliser
    la branche pour cause de « already checked out ».

    `depart` (#1396) est la branche de la tâche dont celle-ci reprend le travail :
    quand elle existe, la branche créée part d'elle et non de `base`. Une tâche
    jamais montée n'a pas de branche, et l'on part alors de la base, comme avant.

    Le `prune` ne libère pas tout : un worktree dont le process a été **tué** — la
    tâche en vol quand Maestro s'est éteint — garde son répertoire, donc son
    enregistrement, donc la branche. Un run repris sur son plan (#1391) rejoue cette
    tâche sous le **même** identifiant, et Git refusait de la remonter
    (`_liberer_la_branche`).
    """
    _git(racine, "worktree", "prune")
    _liberer_la_branche(racine, branche, chemin)
    if _ref_existe(racine, branche):
        arguments = ["worktree", "add", str(chemin), branche]
    else:
        arguments = ["worktree", "add", "-b", branche, str(chemin)]
        if depart and _ref_existe(racine, depart):
            arguments.append(depart)
        # Une branche de base disparue depuis la déclaration du projet ne doit pas
        # faire échouer la tâche : on part de HEAD en le laissant visible dans le
        # message d'erreur éventuel, plutôt que d'exiger un projet re-déclaré.
        elif base and _ref_existe(racine, base):
            arguments.append(base)
    resultat = _git(racine, *arguments)
    if resultat.returncode != 0:
        raise EspaceProjetIndisponible(
            "worktree-refuse",
            f"Worktree refusé pour la branche {branche!r} dans {racine} : "
            f"{_message_git(resultat)}",
        )


def _liberer_la_branche(racine: Path, branche: str, chemin: Path) -> None:
    """Rend `branche` qu'un worktree **abandonné** retient — son travail sauvé d'abord (#1391).

    Constaté sur le banc (S12, 2026-10-05) : Maestro éteint pendant qu'une tâche
    écrivait, le process de son run est tué avant que le `finally` d'
    `espace_de_travail` ne démonte son worktree. Le répertoire reste, l'enregistrement
    aussi, et à la reprise du run sur son plan la même tâche — même identifiant, même
    branche — se voit refuser son worktree : « already used by worktree ». Le run
    repris tombait en échec sur la tâche même qu'il devait refaire.

    Deux gestes, dans cet ordre, et le premier est la raison du second :

    1. ce que le worktree porte encore est **commité sur la branche**
       (`_solder_la_branche`, le geste même que le démontage aurait fait) : le
       retirer d'abord emporterait le travail de la tâche, qui ne vit nulle part
       ailleurs ;
    2. le worktree est **retiré**, jamais la branche (`_retirer_worktree`) — elle se
       remonte ensuite, avec ce travail dessus.

    Ne sont libérés que les worktrees qui ont **la forme exacte** de ceux
    qu'`espace_de_travail` monte — `<racine des espaces>/maestro-…/<tâche>` : un
    worktree que la personne aurait ouvert elle-même sur une branche `maestro/…`,
    fût-ce dans son répertoire temporaire, n'est jamais touché, et le refus de Git
    s'affiche alors comme avant. Un worktree de cette forme est celui d'une
    exécution **antérieure** de cette tâche : une tâche n'est jamais exécutée deux
    fois en même temps, et sa tentative précédente a démonté le sien en sortant —
    sauf à être morte sans le pouvoir.

    Depuis #1392, le premier geste a souvent déjà été fait — par celui qui a éteint
    le process (`sauver_le_travail_en_vol`) — et il est alors sans objet : il reste
    ici pour la mort que personne n'a vue (machine éteinte, process tué hors de
    Maestro).
    """
    for abandonne in _worktrees_abandonnes(racine, branche, sauf=chemin):
        _solder_la_branche(abandonne, branche)
        _retirer_worktree(racine, abandonne)


def _worktrees_abandonnes(
    racine: Path, branche: str, *, sauf: Path | None = None
) -> list[Path]:
    """Les worktrees **de la forme d'un espace de Maestro** qui retiennent `branche`.

    La forme exacte de ce qu'`espace_de_travail` monte pour la tâche de `branche` —
    `<racine des espaces>/maestro-…/<tâche>` —, et rien d'autre : le critère qui
    tient à l'écart le worktree qu'une personne aurait ouvert elle-même sur une
    branche `maestro/…`. `sauf` est l'emplacement qu'on s'apprête à monter. Rend
    une liste vide quand Git ne sait pas répondre.

    La racine des espaces est la racine jetable depuis #1455, et le répertoire
    temporaire lui-même avant : un run repris après la mise à jour retrouve le
    worktree que sa première vie avait monté à l'ancienne adresse.
    """
    resultat = _git(racine, "worktree", "list", "--porcelain")
    if resultat.returncode != 0:
        return []
    espaces = {_normalise(racine_des_espaces()), _normalise(repertoire_temporaire())}
    cible = None if sauf is None else _normalise(sauf)
    tache = branche.removeprefix(PREFIXE_BRANCHE)
    trouves: list[Path] = []
    for dossier, reference in _worktrees(resultat.stdout):
        abandonne = Path(dossier)
        if (
            reference != f"refs/heads/{branche}"
            or _normalise(abandonne) == cible
            or abandonne.name != tache
            or not abandonne.parent.name.startswith(PREFIXE_COMMUN)
            or _normalise(abandonne.parent.parent) not in espaces
        ):
            continue
        trouves.append(abandonne)
    return trouves


def _worktrees(porcelaine: str) -> Iterator[tuple[str, str]]:
    """`(chemin, référence)` de chaque worktree **présent** d'un `git worktree list --porcelain`.

    Un worktree en HEAD détaché n'a pas de ligne `branch` : il rend une référence
    vide, qui ne désigne aucune branche.

    Un worktree que Git dit `prunable` n'est pas rendu (#1447) : son adresse est vide,
    ou n'a plus de `.git` — la coquille qu'un démontage coupé laissait avant ce
    ticket. Il n'y a rien à y commiter, et Git, lancé dedans, remonterait jusqu'au
    premier dépôt qu'il trouve au-dessus du répertoire temporaire. Le `worktree
    prune` du montage suivant l'oublie.
    """
    for bloc in porcelaine.split("\n\n"):
        lignes = bloc.splitlines()
        if any(ligne.split(" ", 1)[0] == "prunable" for ligne in lignes):
            continue
        champs = dict(ligne.split(" ", 1) for ligne in lignes if " " in ligne)
        if "worktree" in champs:
            yield champs["worktree"], champs.get("branch", "")


def _normalise(chemin: Path) -> str:
    """Un chemin comparable à un autre : absolu, résolu, casse du système de fichiers."""
    try:
        resolu = chemin.resolve()
    except OSError:  # pragma: no cover - chemin non représentable
        resolu = chemin
    return os.path.normcase(str(resolu))


def _solder_la_branche(chemin: Path, branche: str) -> None:
    """Commite sur `branche` ce que le worktree porte encore, avant de le démonter (#705).

    C'est ce qui rend **vraie** la phrase que ce module tient depuis #224 — « le
    worktree est retiré, jamais la branche : c'est elle qui porte le travail
    jusqu'à la fusion ». Elle ne l'était qu'à moitié : un agent outillé écrit des
    fichiers, il ne fait pas forcément `git add`, et `git worktree remove --force`
    emportait tout ce qui n'était pas commité. La branche survivait donc en ne
    portant rien, et la fusion de #705 aurait fusionné le vide.

    Le geste **n'est pas réécrit ici** : c'est `commiter_en_attente`
    (`maestro.projets.application`), la seule orthographe de « commiter ce que le
    worktree porte encore » — hooks de l'utilisateur non contournés, identité
    posée par `-c`.

    Best-effort et silencieux, exactement comme `_retirer_worktree` et pour la
    même raison : ce geste vit dans un `finally`, et lever ici masquerait
    l'exception qui a réellement condamné la tâche. Ce qui n'a pas pu être
    commité — `pre-commit` de l'utilisateur qui refuse, index verrouillé — reste
    hors de la branche, donc hors de la fusion, et le diff vide le dira à
    l'appelant plutôt qu'une exception venue d'un démontage.

    Il a lieu quel que soit le **verdict** de la tâche : une tâche en échec ne
    fusionne rien (c'est le critère de #705), mais son travail mérite d'exister
    sur sa branche plutôt que d'être emporté par le `--force`. Consigner l'échec
    et détruire ce qui l'explique serait le pire des deux.
    """
    try:
        commiter_en_attente(chemin, branche)
    except ApplicationRefusee:
        pass


def _retirer_worktree(racine: Path, chemin: Path) -> bool:
    """Retire le worktree de `chemin` — **jamais la branche** (EF-36) — sans laisser de coquille.

    Rend `True` quand plus rien n'est monté à `chemin`, `False` quand le worktree y
    est resté **intact** : à l'appelant de ne pas effacer alors ce qui le contient.

    **Ce geste peut être coupé à tout instant** (#1447) : il vit dans le `finally`
    d'un hôte qu'une extinction achève au bout de `DELAI_ANNULATION_S`, et effacer
    un `node_modules/` complet prend plus que cela. `git worktree remove --force`
    effaçait sur place, `.git` en premier (l'ordre où NTFS rend les entrées) : coupé
    au milieu, il laissait à l'adresse du worktree un dossier sans `.git` que Git
    enregistrait encore — `git status` y répondait « not a git repository », et S12
    s'y arrêtait. D'où trois gestes, dans cet ordre, et c'est l'ordre qui tient :

    1. le worktree est **écarté** de son adresse d'un seul geste — un renommage dans
       le même dossier (`_ecarter`), atomique : avant, il est intact ; après, il
       n'est plus là ;
    2. ce qui a été écarté **cesse d'être un worktree** — son `.git` d'abord, puis
       l'enregistrement que Git en tenait (`git worktree prune`, qui oublie un
       worktree dont l'adresse est vide) ;
    3. le reste est **effacé** (`retirer_arbre`, #992 : lecture seule comprise).

    Une mort après le premier geste laisse un enregistrement `prunable` — que ni la
    sauvegarde d'une extinction (`_worktrees`) ni l'oracle de S12 ne lisent, et que le
    montage suivant oublie — et un dossier qui n'est plus un worktree, que le
    ramassage emporte dès que son hôte est mort (`maestro.sandbox.ramassage`). Le
    ramassage ne garde que ce qui porte un `.git` : c'est pourquoi il part avant
    l'enregistrement.

    Un worktree qu'on ne peut pas écarter — un process y vit encore, et Windows ne
    déplace pas un dossier tenu — reste **intact et monté**, comme celui d'une tâche
    tuée avant son démontage : son travail est sur la branche (`_solder_la_branche`,
    ou `sauver_le_travail_en_vol` à l'extinction), et le montage suivant de la tâche
    le libère. L'effacer quand même est précisément ce qui fabriquait la coquille.

    Best-effort et silencieux à dessein : ce démontage vit dans un `finally`, et
    une erreur levée ici masquerait celle qui a réellement condamné la tâche. Aucun
    des gestes ne demande Git, sauf l'oubli, que le montage suivant refait.
    """
    if not chemin.exists():
        _oublier_les_worktrees_disparus(racine)
        return True
    a_jeter = _ecarter(chemin)
    if a_jeter is None:
        return False
    try:
        (a_jeter / ".git").unlink(missing_ok=True)
    except OSError:  # pragma: no cover - `.git` tenu : le prune ne l'oubliera pas, l'âge si
        pass
    _oublier_les_worktrees_disparus(racine)
    retirer_arbre(a_jeter)
    return True


def _ecarter(chemin: Path) -> Path | None:
    """Renomme le worktree en un nom qu'aucune tâche ne porte — `None` s'il est resté en place.

    Le nom garde le dossier parent, donc le volume (le renommage reste un seul geste)
    et le marqueur de pid que le ramassage lit. Le point qu'il porte, `_slug` l'écarte
    de tout nom de tâche : ce dossier ne passera jamais pour l'espace de quelqu'un.
    """
    cible = chemin.with_name(f"{chemin.name}.a-jeter-{secrets.token_hex(4)}")
    for pause in (*_PAUSES_ECART_S, None):
        try:
            return chemin.rename(cible)
        except OSError:
            if pause is None:
                return None
            time.sleep(pause)
    return None  # pragma: no cover - la boucle rend toujours


def _oublier_les_worktrees_disparus(racine: Path) -> None:
    """`git worktree prune` — l'enregistrement d'un worktree dont l'adresse est vide. Muet."""
    try:
        _git(racine, "worktree", "prune")
    except EspaceProjetIndisponible:
        pass


def _ref_existe(racine: Path, branche: str) -> bool:
    """La branche locale `branche` existe-t-elle dans le dépôt de `racine` ?"""
    return _git(racine, "rev-parse", "--verify", "--quiet", f"refs/heads/{branche}").returncode == 0


def _git(
    racine: Path,
    *arguments: str,
    index: Path | None = None,
    entree: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Lance `git <arguments>` dans `racine` et rend le processus achevé.

    Ne lève **que** pour ce qui n'est pas un verdict de Git (binaire absent, délai
    dépassé) : un code de retour non nul est une réponse, que l'appelant traduit
    en refus motivé — c'est ce qui permet à `_ref_existe` de poser une question
    sans avoir à attraper une exception. `index` remplace l'index du dépôt pour
    cet appel (`GIT_INDEX_FILE`, l'instantané de #1448), `entree` est ce que Git
    lit sur son entrée standard.
    """
    environ = None if index is None else {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=racine,
            input=entree,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_DELAI_GIT_S,
            check=False,
            env=environ,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EspaceProjetIndisponible(
            "git-indisponible",
            f"Git indisponible pour le projet {racine} : {exc}",
        ) from exc


def _message_git(resultat: subprocess.CompletedProcess[str]) -> str:
    """Le message d'erreur de Git, en une ligne (stderr, à défaut stdout)."""
    brut = (resultat.stderr or resultat.stdout or "").strip()
    return " ".join(brut.split()) or f"code de retour {resultat.returncode}"
