"""Les corps de requête que l'API accepte, un modèle par geste."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from maestro.agents.permissions import PolitiqueOutils
from maestro.controltower.bornes import BornesRun
from maestro.controltower.fournisseur_defaut import DemandeFournisseur
from maestro.decision_humaine import ETENDUE_APPEL
from maestro.engine.brief import MODE_BRIEF_HUMAIN
from maestro.equipe import GABARITS, RoleManquant, RoleValide, SkillRetenu
from maestro.equipe.composition import MembreActuel
from maestro.outillage.questionnaire import Choix
from maestro.references import ReferenceTicket


class ReferenceTicketRequete(BaseModel):
    """La référence d'un ticket externe posée au lancement d'un run (#185, contrat #183).

    Générique : un identifiant lisible (`id`, ex. « #185 », « PROJ-42 ») et son
    `url` — vide quand seul l'identifiant est connu. GitLab, Jira ou Linear
    passent par la même forme, aucun champ propre à un outil. La référence
    descend ensuite jusqu'aux tâches du run (contrat #183).
    """

    id: str
    url: str = ""

    def en_reference(self) -> ReferenceTicket | None:
        """La référence du modèle métier — None si elle n'apprend rien (ni id, ni URL).

        Passe par `ReferenceTicket.depuis`, donc par la normalisation (#187) :
        identifiant borné, URL non suivable (`javascript:`, chemin relatif)
        jetée. Ce qui entre par l'API est traité comme ce qui arrive par le
        flux — non fiable jusqu'à validation.
        """
        return ReferenceTicket.depuis({"id": self.id, "url": self.url})



class SourceRequete(BaseModel):
    """Une source déclarée au lancement (#317, EF-39) — docs/05 §6.1.

    Deux formes pour un `fichier`, et une seule est complète : le **renvoi** à un
    téléversement (`id`, rendu par `POST /api/sources`), qui seul désigne de
    vrais octets, et la déclaration nue de docs/24 §3.2 (`nom` + `taille`), qui
    résout mais ressortira `source-absente` au rapport de lecture. Un `dossier`
    porte son `chemin`, une `url` sa `valeur`.

    **Aucun champ n'est requis**, `type` compris : un corps mal formé doit
    ressortir en refus **motivé** de la résolution (`type-inconnu` à l'index
    fautif) et non en erreur de schéma Pydantic, dont la forme n'a ni motif ni
    index. Le reste des contrôles appartient à `maestro.sources.resolution` :
    ce qui arrive ici est une saisie, jamais une vérité.
    """

    type: str = ""
    id: str = ""
    nom: str = ""
    chemin: str = ""
    valeur: str = ""
    taille: int | None = None



class LancementExecutionRequete(BaseModel):
    """Corps de lancement d'une exécution (#185) : objectif, garde-fous, ticket.

    `objectif` est l'énoncé en langage naturel que l'orchestrateur décompose ;
    les garde-fous (#9) plafonnent l'exécution — coût, tokens, time-out par
    tâche, parallélisme —, chacun optionnel, None laissant le défaut du moteur.
    `ticket` rattache le run à un ticket externe (optionnel). `projet_id` (#222)
    le rattache au **projet** dans lequel il travaille (optionnel, `null` :
    aucun projet). Une requête invalide
    (objectif vide, garde-fou hors bornes) est refusée en 422 ; un `projet_id`
    mal formé, lui, est **écarté** (le run part sans projet) plutôt que de faire
    échouer le lancement — cf. `maestro.appartenance`.

    `sources` (#317, EF-39) porte la **matière** de l'objectif : fichiers
    téléversés au préalable (§6.8), dossier de références, URL. Absente ou vide,
    le lancement part sur l'objectif seul ; une source refusée
    (racine interdite, plafond dépassé, téléversement inconnu) sort en 422
    **motivé**, index de la source fautive compris.
    """

    objectif: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None
    ticket: ReferenceTicketRequete | None = None
    projet_id: str | None = None
    sources: list[SourceRequete] | None = None
    # Le régime du brief (#320) — `humain` par défaut : la Control Tower est la
    # voie de lancement qui a quelqu'un devant, et la décision D5 veut le brief
    # validé avant décomposition. `auto` rédige le brief sans attendre, `sans`
    # décompose l'objectif brut. Un mode inconnu est refusé en 422, jamais traité
    # comme un `sans` silencieux.
    brief: str | None = MODE_BRIEF_HUMAIN

    def sources_declarees(self) -> list[dict[str, Any]] | None:
        """Les sources en dicts pour la résolution — `None` quand il n'y en a pas.

        `None` et non `[]` : c'est ce qui fait qu'un lancement sans matière émet
        un événement sans le champ `sources` (#315).
        """
        if self.sources is None:
            return None
        return [source.model_dump() for source in self.sources]



class ReassignationRequete(BaseModel):
    """Corps de la réassignation manuelle : l'agent qui reprend la tâche."""

    agent: str



class DecisionRequete(BaseModel):
    """Corps de la décision humaine (#48) : approuver ou refuser l'action sensible.

    `motif` (#272) est la raison **facultative** d'un refus, telle que la personne
    l'a écrite. Il rejoint le `detail` de l'événement, donc le journal durable et la
    `decision` de la demande projetée : sans lui, un refus reviendrait plus tard
    comme un fait sans cause, et rien ne distinguerait « trop risqué avant la démo »
    d'une erreur de clic.

    ⚠ Le moteur le **lit** (#1185) : c'est une **consigne**. Elle voyage aussi
    dans `Event.motif`, le validateur la rend à l'agent, et celui-ci replanifie son
    geste à partir d'elle — « archive au lieu de supprimer » réoriente le travail au
    lieu de l'arrêter ; la nouvelle action repasse par l'arbitrage.

    `etendue` (#1185) est la portée d'une **approbation** : `appel` (le défaut, cet
    appel seul), ou l'outil pour la suite du `run` ou du `projet`. Choisie par la
    personne, jamais par défaut ; admise seulement sur une demande qui porte un
    acte, et écrite dans les permissions de l'agent, où elle se relit et se retire.

    Sur une **approbation** le motif est ignoré, sur un refus l'étendue — comme le
    `brief` d'une décision de brief l'est sur un refus (`DecisionBriefRequete`) : le
    canal porte les deux gestes, chacun ne lit que ce qui le concerne. Vides ou
    absents, la décision se réduit à `approuve` : un refus sans cause, une
    approbation pour cet appel seul.
    """

    approuve: bool
    motif: str = ""
    etendue: str = ETENDUE_APPEL



class ReponseQuestionRequete(BaseModel):
    """Corps de la réponse à une question d'agent (#1023) : du **texte**, rien d'autre.

    Pas de booléen, et c'est tout ce qui sépare ce geste de `DecisionRequete` :
    une question ne soumet aucun acte, il n'y a donc ni oui, ni non, ni motif de
    refus — il y a ce qu'on répond. Un choix retenu se répond en le recopiant :
    l'agent a écrit ses options, il sait les relire, et un index de choix ferait
    voyager un numéro dont la signification vivrait dans un autre événement.

    La réponse est **obligatoire et non vide** (refusée en 422 par le contrôle
    ci-dessous) : une réponse vide n'apprend rien à l'agent, qui reprendrait sur
    son hypothèse en croyant qu'on lui a répondu — c'est-à-dire pire que le
    silence, qui lui dit au moins la vérité.
    """

    reponse: str



class DecisionPlafondRequete(BaseModel):
    """Corps de la décision au plafond de dépense (#1182) : relever, réduire, arrêter.

    `geste` est l'une des trois réponses (`maestro.engine.plafond.GESTES_PLAFOND`).
    **Relever** et **réduire** portent le nouveau plafond — en dollars
    (`plafond_cout_usd`) et/ou en tokens (`plafond_tokens`), selon celui que le run
    a atteint ; **réduire** nomme en plus les tâches à écarter (`ecartees`, des
    identifiants pris dans la question en vol). **Arrêter** ne porte rien.

    Typé et non du texte libre, à la différence d'une réponse à une question
    d'agent : ce qui repart est un **montant** et une liste de tâches, que le
    moteur applique tels quels. Les recopier d'une phrase serait juger du texte
    par un motif (#746) ; le choix se fait sur la carte, qui a les chiffres.
    """

    geste: str
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    ecartees: list[str] = []



class DecisionBriefRequete(BaseModel):
    """Corps de la décision sur un brief (#320) : approuver — corrigé ou non — ou refuser.

    `brief` porte la **version corrigée** que l'humain veut voir décomposée ; absent
    (`null`), le brief proposé est approuvé tel quel. C'est ce qui distingue ce
    canal de la validation d'action sensible (#48), dont le corps se résume à un
    booléen : ici la personne ne fait pas que dire oui, elle réécrit avant de le
    dire — et c'est son texte qui part en décomposition.

    Le brief corrigé est validé contre la **JSON Schema partagée** (#318) avant de
    partir : une correction qui casse la forme doit coûter un 422 à celui qui la
    soumet, pas un échec de run une seconde plus tard, quand plus personne ne
    regarde. Sur un **refus**, il est ignoré — il n'y a rien à décomposer.
    """

    approuve: bool
    brief: dict[str, Any] | None = None



class ReponsesBriefRequete(BaseModel):
    """Corps des réponses aux questions de clarification d'un brief (#321).

    `reponses` est une liste de chaînes **appariée par position** aux questions du
    brief stocké du run. Pas de clé, pas d'identifiant de question : le brief est
    régénéré en entier à chaque tour, donc une question n'a pas d'identité stable
    d'une version à l'autre (#318, note du schéma) — un identifiant laisserait croire
    le contraire. Ce qui rend la position sûre est que les réponses s'adressent au
    brief **stocké**, dont la liste de questions est figée entre sa publication
    (`brief.questions`) et sa réponse.

    D'où le contrôle de longueur exigé par la route : une liste qui ne fait pas le
    compte est refusée en 422. C'est le seul moment où quelqu'un est là pour corriger
    sa requête — plus loin, en plein run, l'appariement est volontairement tolérant
    (une réponse manquante vaut « sans réponse »), parce que lever y coûterait le run.

    Une chaîne **vide est licite** et veut dire « je ne sais pas » : la question sera
    inscrite en hypothèse explicite plutôt que reposée au tour suivant. C'est ce qui
    permet de répondre à trois questions sur cinq sans bloquer le run.
    """

    reponses: list[str]



class CapaciteRequete(BaseModel):
    """Corps du contrôle de capacité (#86) : les réglages à poser, champ par champ.

    Chaque champ est optionnel — None laisse la valeur en place : on peut
    désactiver sans toucher aux instances, et inversement. Au moins un champ
    doit être renseigné (sinon 422 : il n'y a rien à régler).
    """

    actif: bool | None = None
    instances: int | None = None



class PlaybookEcritureRequete(BaseModel):
    """Corps d'écriture d'un playbook (#76) : le nouveau contenu Markdown, intégral."""

    contenu: str



class PlaybookRestaurationRequete(BaseModel):
    """Corps du retour arrière (#76) : la version passée à republier comme courante."""

    version: int



class PlaybookPropositionRequete(BaseModel):
    """Corps d'une demande de proposition d'auto-amélioration (#139) : le run à analyser.

    `run_id` désigne l'exécution dont les échecs de l'agent alimentent l'analyse. Le
    déclenchement est **à la demande** (ce POST), jamais automatique en fin de run.
    """

    run_id: str



class PlaybookRedactionRequete(BaseModel):
    """Corps d'une demande de rédaction assistée (#261) : le brouillon, et ce qu'on en veut.

    `contenu` est le texte **en cours d'édition**, pas la version publiée : l'assistance
    travaille sur ce que l'utilisateur a sous les yeux. `consigne` est libre et
    facultative — sans elle, la demande reste « complète et structure ». Les bornes
    (`BROUILLON_MAX`, `CONSIGNE_MAX`) sont vérifiées par le rédacteur, comme #257 borne
    l'intention côté générateur.
    """

    contenu: str
    consigne: str | None = None



class AgentCreationRequete(BaseModel):
    """Corps de création d'un agent personnalisé (#72) : sa définition complète.

    `modele` est optionnel (None : le modèle par défaut des exécutants) ;
    `fournisseur` est déclaratif au POC (le moteur est mono-fournisseur).
    `effort` (#253) est le niveau d'effort demandé au modèle — ce que le
    fournisseur retenu admet se lit sur `GET /api/fournisseurs`. Il n'est **pas
    validé ici** : un effort que le fournisseur ne connaît pas est ignoré à
    l'exécution, sans erreur, plutôt que de faire échouer l'enregistrement d'une
    définition par ailleurs correcte.
    """

    nom: str
    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None
    effort: str | None = None



class AgentModificationRequete(BaseModel):
    """Corps de modification d'un agent personnalisé (#72) : la définition intégrale.

    Un remplacement, pas un diff — le nom, lui, vit dans l'URL et ne change pas
    (c'est la clé de routage et de stockage).
    """

    role: str
    competences: list[str]
    playbook: str
    modele: str | None = None
    fournisseur: str | None = None
    effort: str | None = None



class SurchargeRequete(BaseModel):
    """Corps de surcharge des réglages d'un agent du code (#259) : les trois, entiers.

    Un remplacement et pas un diff, comme `AgentModificationRequete` : ce qui
    n'est pas envoyé est **hérité du code**, pas « laissé tel quel ». Sans cette
    règle, retirer une surcharge de modèle en laissant celle d'effort demanderait
    un verbe de plus, et « champ absent » voudrait dire deux choses selon le
    réglage. Pour tout rendre au code, `DELETE` sur la même URL.

    Aucun des trois n'est validé ici — même raison qu'à la création d'un agent :
    ce que le fournisseur admet se lit sur `GET /api/fournisseurs`, se trie à
    l'exécution, et une gamme qui bouge ne doit pas rendre irrecevable une
    surcharge par ailleurs correcte.
    """

    fournisseur: str | None = None
    modele: str | None = None
    effort: str | None = None



class AgentGenerationRequete(BaseModel):
    """Corps d'une génération assistée de définition (#257) : l'intention, en une phrase.

    Un seul champ, et c'est le principe du geste : décrire ce qu'on veut plutôt que
    remplir rôle, compétences et playbook. La borne de longueur est posée par le
    générateur (`INTENTION_MAX`) et non ici — c'est lui qui la connaît, et le
    dépassement est un 422 au même titre qu'une intention vide.
    """

    intention: str



class ProjetRequete(BaseModel):
    """Corps de déclaration d'un projet (#223) : sa racine sur le disque et son périmètre.

    `racine` est le chemin **absolu** du projet sur le poste ; il est
    canonicalisé et confronté aux racines interdites côté serveur (EF-38) — ce
    qui arrive ici est une saisie, jamais une vérité. `origine` vaut `existant`
    (on reprend un dossier) ou `nouveau` (on l'initie : le dossier est créé s'il
    manque). `inclus`/`exclus` sont des motifs **relatifs à la racine** ; `null`
    laisse les défauts du modèle (`.`, et l'exclusion de `.git`, `node_modules`,
    `.env`, `**/secrets/**`).

    Le `vcs` n'est délibérément **pas** un champ de requête : il est constaté sur
    le disque, jamais déclaré — un client qui l'annoncerait pourrait mentir.
    """

    nom: str
    racine: str
    origine: str = "existant"
    inclus: list[str] | None = None
    exclus: list[str] | None = None



class SelecteurRequete(BaseModel):
    """Corps (facultatif) d'une ouverture du sélecteur natif (#278).

    `depart` est le dossier où le dialogue s'ouvre — un confort, jamais une
    permission : le chemin **rendu** par le dialogue est confronté à EF-38 quel
    que soit l'endroit d'où l'utilisateur est parti.
    """

    depart: str | None = None



class RacineRequete(BaseModel):
    """Corps d'un verdict de racine (#938) : le chemin proposé, tel que l'OS l'a rendu.

    Non canonicalisé, et c'est voulu : la canonicalisation fait partie de ce
    qu'EF-38 vérifie (`valider_racine`), pas de ce que l'appelant prépare.
    """

    chemin: str



class RepertoireRequete(BaseModel):
    """Corps d'un réglage du répertoire des projets (#1022).

    `chemin` à `null` **revient au défaut** (`~/Maestro`) — ce n'est pas « pas
    de répertoire », c'est « celui que Maestro propose ». Non canonicalisé, pour
    la même raison que `RacineRequete` : la canonicalisation appartient à
    `valider_racine`.
    """

    chemin: str | None = None



class DossierAutoriseRequete(BaseModel):
    """Corps d'une autorisation de dossier (#1610) : le dossier que l'explorateur a refusé.

    Non canonicalisé, pour la même raison que `RacineRequete` : la
    canonicalisation appartient à `valider_racine`.
    """

    chemin: str



class FournisseurDefautRequete(BaseModel):
    """Corps d'une épreuve ou d'un enregistrement du fournisseur par défaut (#1609).

    `cle` n'est présente que si la personne vient de la taper : sans elle, on
    éprouve avec la clé déjà gardée par le coffre du poste. Elle ne ressort
    jamais d'une réponse.
    """

    fournisseur: str
    acces: str
    modele: str | None = None
    adresse: str | None = None
    cle: str | None = None

    def demande(self) -> DemandeFournisseur:
        return DemandeFournisseur(
            fournisseur=self.fournisseur,
            acces=self.acces,
            modele=self.modele,
            adresse=self.adresse,
            cle=self.cle,
        )



class ChatEnvoiRequete(BaseModel):
    """Corps d'un envoi de chat (#84) : le message de l'utilisateur à l'agent.

    `sources` (#482) est la matière que le message embarque — fichiers déposés,
    dossier de références, adresses —, **dans l'ordre où l'écran l'a composée** :
    c'est lui qui décide de ce qui entre quand le budget de tokens s'épuise
    (#316). Même forme qu'au lancement (`LancementExecutionRequete.sources`), et
    pour la même raison : un fichier y voyage par l'`id` que `POST /api/sources`
    lui a rendu, seule façon d'en désigner de vrais octets. Absente ou vide, le
    message part sans matière.

    `projet_id` (#683) est le **projet de la fenêtre** d'où part le message —
    à ne pas confondre avec le `?projet=` des lectures (#277), qui est une
    portée et porte les mots réservés `tous`/`aucun` : ici c'est un identifiant
    de projet, et rien d'autre. Le fil ne s'en trouve ni cadré ni filtré ; il
    n'intéresse que ce que la réponse **ouvre** — l'orchestration y rattache son
    run et y cadre son aperçu. Absent, le run part sans projet.

    `conversation` (#694) est le fil de l'agent où l'échange se range. Absent,
    l'envoi rejoint la **plus récente**.
    """

    contenu: str
    sources: list[dict[str, Any]] | None = None
    projet_id: str | None = None
    conversation: str | None = None



class CadrageDecisionRequete(BaseModel):
    """Corps du geste qui tranche une demande de cadrage du fil (#943, #990).

    La **même forme** que `DecisionBrief` (§6.10), parce que c'est la même
    décision un cran plus tôt : `approuve` dit oui ou non, et le second champ
    porte la version **corrigée** ou `null` pour « la proposition tient ». Le
    corps ne recopie jamais un objectif non touché — et `null` n'est pas une
    omission, c'est une affirmation.

    Les quatre **garde-fous** (#990) sont ceux de `LancementExecutionRequete`,
    aux mêmes noms et avec le même contrat : chacun optionnel, `null` laissant
    le run aller jusqu'au bout. Les mêmes noms parce que c'est le même moteur
    qui les reçoit — un second vocabulaire pour la même chose se paierait au
    premier écran qui voudrait afficher les deux. Ils sont **refusés hors
    bornes** par `lancer` et non ici : la règle « un plafond est un maximum »
    vit là où elle est appliquée, et le refus se raconte dans le fil comme tout
    lancement qui échoue (`orchestration._ouvrir_un_run`).

    `objectif` et les bornes sont ignorés sur un refus : il n'y a rien à lancer.
    `projet_id` et `conversation` ont exactement le sens qu'ils ont sur un envoi —
    à ceci près que le run suit le projet **de la proposition** quand elle en porte
    un (#1180) : le fil est transverse, et la fenêtre du clic n'est pas forcément
    celle où la proposition a été faite.
    """

    approuve: bool
    objectif: str | None = None
    plafond_cout_usd: float | None = None
    plafond_tokens: int | None = None
    timeout_tache_s: float | None = None
    parallelisme: int | None = None
    projet_id: str | None = None
    conversation: str | None = None

    def bornes_posees(self) -> BornesRun:
        """Les quatre garde-fous d'un bloc — `AUCUNE_BORNE` quand aucun n'est posé."""
        return BornesRun.depuis(
            {
                "plafond_cout_usd": self.plafond_cout_usd,
                "plafond_tokens": self.plafond_tokens,
                "timeout_tache_s": self.timeout_tache_s,
                "parallelisme": self.parallelisme,
            }
        )



class ReponseOutillageRequete(BaseModel):
    """Corps du geste qui répond à une question d'outillage du fil (#1031).

    Un seul champ de fond, `valeur` : la clé de la question **n'est pas** dans le
    corps, et c'est une décision. La question à laquelle on répond est celle que le
    fil porte encore (`chat.question_en_attente`) ; la laisser désigner par le client
    ouvrirait la porte à une réponse qui vise une question déjà tranchée — le geste
    tardif et le double clic, exactement ce que le `409` existe pour attraper.

    `conversation` a le sens qu'il a partout ailleurs sur ce canal.

    `libre` (#1147) dit que `valeur` est une réponse **avec ses mots** — le choix
    « Autre chose » de la carte — et non une option : toute phrase non vide est
    alors acceptée, et le questionnaire la comprend au tour suivant.
    """

    valeur: str
    libre: bool = False
    conversation: str | None = None



class ChoixOutillageRequete(BaseModel):
    """Une réponse, ou un constat compris, tel qu'il voyage vers les routes sans état (#1031).

    La forme de `maestro.outillage.questionnaire.Choix` : `cle` et `valeur` disent ce
    que vaut le sujet ; `libre` qu'il a été dit avec des mots plutôt que cliqué ;
    `deduit` et `parce_que` qu'il a été **compris** par le questionnaire plutôt que
    donné (#1147).

    Les constats compris voyagent **et font foi** vers la recommandation, la
    génération et l'équipe : ce sont eux que l'écran a montrés, et rappeler le modèle
    à l'écriture pourrait comprendre autre chose. La route du questionnaire, elle, ne
    lit que les réponses **données** et recomprend le reste.
    """

    cle: str
    valeur: str
    deduit: bool = False
    parce_que: str = ""
    libre: bool = False

    def en_choix(self) -> Choix:
        """Le choix du domaine, tel qu'il a été envoyé."""
        return Choix(
            cle=self.cle,
            valeur=self.valeur,
            deduit=self.deduit,
            parce_que=self.parce_que,
            libre=self.libre,
        )



class QuestionnaireOutillageRequete(BaseModel):
    """Les réponses acquises, dont on veut la suite ou la recommandation (#1031).

    Sans état côté serveur, et c'est le contrat : le client dit ce qu'il a, l'API dit
    ce qui en découle. C'est ce qui permet au fil (qui tient ses réponses dans ses
    messages) et au parcours de création (#1034, qui les tiendra à l'écran) de servir
    du **même** questionnaire sans partager de session.
    """

    choix: list[ChoixOutillageRequete] = []

    def choix_acquis(self) -> list[Choix]:
        """Les réponses et les constats en objets du domaine, tels qu'envoyés."""
        return [c.en_choix() for c in self.choix]



class RenfortRequete(BaseModel):
    """Le rôle manquant dont la carte du fil demande la proposition (#1227).

    C'est la forme **servie** sur la demande de recrutement du fil
    (`DemandeRecrutement.gabarit`/`raison`), rapportée telle quelle — même régime
    que `RoleEquipeRequete` (#1040), et pour la même raison : ce que la personne
    valide doit être ce qu'on lui a montré, et le manque n'est connu que du run
    qui a écrit cette demande. Le serveur revalide le `gabarit` contre son
    catalogue (422 sinon), donc rien d'inventé n'en sort.

    `raison` est la justification affichée : le **plan** qui demande ce métier, et
    les tâches qui l'attendent. Elle voyage parce qu'aucun constat du projet ne la
    porte — c'est tout ce qui distingue un renfort d'une proposition d'équipe.
    """

    gabarit: str
    raison: str = ""

    def role_manquant(self) -> RoleManquant:
        """Le manque en objet du domaine — compétences dérivées du gabarit.

        Les compétences ne voyagent pas : elles sont celles du gabarit, que le
        catalogue porte déjà (`Gabarit.competences`). Les laisser venir du
        navigateur ferait deux sources pour ce que sait faire un rôle, et la
        proposition pourrait annoncer un métier que la fiche créée n'aurait pas.
        """
        gabarit = next((g for g in GABARITS if g.nom == self.gabarit), None)
        if gabarit is None:
            raise ValueError(
                f"gabarit de renfort inconnu : {self.gabarit!r} (attendus : "
                f"{', '.join(g.nom for g in GABARITS)})."
            )
        return RoleManquant(
            competences=gabarit.competences,
            role=gabarit.role,
            gabarit=gabarit.nom,
            couvre=gabarit.competences,
        )



class PropositionEquipeRequete(QuestionnaireOutillageRequete):
    """Le corps — facultatif — de `POST …/equipe/proposition`.

    Les réponses du questionnaire (#1031), plus `renfort` (#1227) : demander **un
    seul rôle**, celui qu'un plan appelle et que l'équipe n'a pas, au lieu de
    l'équipe entière. Les deux ne se combinent pas en pratique — un renfort
    s'adresse à un projet qui a déjà une équipe, donc déjà analysé — mais rien
    n'a à l'interdire : `renfort` décide seul de ce qui est proposé, les choix
    restent la provenance de la matière.
    """

    renfort: RenfortRequete | None = None



class MembreEquipeRequete(BaseModel):
    """Un rôle de l'équipe **telle que l'écran la montre** au moment d'une demande (#1159).

    Le nom, le libellé, et les deux choses que la personne a pu ajuster : la case
    (`retenu`) et le nombre d'instances. Pas de politique : la correction ne la lit
    pas, et ce qui repart à la création reste ce que l'écran tient.
    `navigateur` (#1532) : ce rôle a-t-il, à l'écran, le navigateur de Maestro.
    `playbook` (#1521) : celui que la carte montre — le modèle répond sur ce qu'il a
    lu, et une convention fixée en mots gagne chaque rôle qu'elle contredit. Absent
    — une carte servie sans lui —, il n'est pas transmis, et le modèle le sait.
    """

    nom: str
    role: str = ""
    retenu: bool = True
    instances: int = 1
    navigateur: bool = False
    playbook: str = ""



class CorrectionEquipeRequete(QuestionnaireOutillageRequete):
    """Le corps de `POST …/equipe/correction` — la demande, l'équipe montrée, les réponses.

    `choix` a le sens qu'il a sur la proposition : vide, le projet est analysé ;
    renseigné (un projet neuf), l'équipe se corrige sur ses réponses.
    """

    demande: str = ""
    equipe: list[MembreEquipeRequete] = []

    def membres(self) -> list[MembreActuel]:
        """L'équipe montrée en objets du domaine."""
        return [
            MembreActuel(
                nom=m.nom,
                role=m.role,
                retenu=m.retenu,
                instances=m.instances,
                navigateur=m.navigateur,
                playbook=m.playbook,
            )
            for m in self.equipe
        ]



class GenerationOutillageRequete(BaseModel):
    """Corps — facultatif — de la génération d'outillage (#1034, #1100).

    `retenus` : les **chemins** des entrées que l'étape d'outillage a gardées
    cochées. Pas les entrées elles-mêmes — le quoi, le où et le contenu se
    rederivent côté serveur, et les laisser voyager depuis un écran ouvrirait une
    seconde façon de décider ce qu'on écrit dans le dossier de quelqu'un.

    `None` (le champ absent, ou pas de corps du tout) et une **liste vide** ne
    disent pas la même chose : le premier veut dire « je n'ai rien à trier, écris
    ce qui est recommandé », le second « je n'ai rien gardé ». Un défaut à `[]`
    confondrait les deux, et un appel sans corps n'écrirait plus rien.

    `choix` (#1100) : les réponses au questionnaire d'un projet **neuf**, dans la
    forme de `…/outillage/recommandation`. Ce ne sont pas des entrées, et c'est ce
    qui les laisse voyager sans rouvrir la porte fermée plus haut : une réponse
    est une **donnée d'entrée** de la dérivation, au même titre que la racine
    qu'on analyse. Vide — le défaut —, l'outillage se dérive de l'analyse, comme
    pour la proposition d'équipe.
    """

    retenus: list[str] | None = None
    choix: list[ChoixOutillageRequete] = []

    def choix_acquis(self) -> list[Choix]:
        """Les réponses et les constats en objets du domaine, tels qu'envoyés."""
        return [c.en_choix() for c in self.choix]



class SkillEquipeRequete(BaseModel):
    """Un skill de l'outillage que le rôle validé branche (#1040).

    Repris de la proposition, jamais ressaisi : `chemin` et `commandes` sont ceux
    de l'entrée d'outillage qui l'a recommandé. Le playbook du rôle porte le chemin
    (`maestro.equipe.creation.playbook_branche`) ; la commande, le skill la porte, et
    lui seul (#1520).
    """

    nom: str
    chemin: str = ""
    commandes: list[str] = []



class RoleEquipeRequete(BaseModel):
    """Un rôle que l'utilisateur a **gardé** dans l'équipe proposée (#1040).

    C'est délibérément la forme **servie** par `…/equipe/proposition`, rapportée
    telle quelle — pas un identifiant à re-dériver. Deux champs portent tout le
    poids de ce choix :

    - `playbook` est le texte qu'on a lu à l'écran. Le regénérer à la validation
      rendrait un **autre** playbook (c'est un appel modèle), donc créerait un
      agent que personne n'a validé ;
    - `politique` est la `PolitiqueOutils` que `RolePropose.politique()` a
      rendue à la proposition — la traduction « autorisations → politique » a
      donc lieu **une seule fois**, côté serveur, et ce qui est écrit est le cran
      qu'on a lu avec sa raison (#716). `None` : ce rôle ne pose aucune
      politique, ce qui n'est pas la même chose qu'une politique vide.

    `instances` est le seul champ que l'écran ajuste librement ; les bornes sont
    celles de `maestro.equipe.creation` et se vérifient là-bas, avec les autres
    refus, pour qu'un refus de forme et un refus de fond se lisent au même
    endroit.

    `navigateur` (#1532) : le rôle reçoit le navigateur de Maestro, tel que l'écran
    l'a montré — proposé, puis retiré ou donné en mots. Absent : il ne le reçoit pas.
    """

    nom: str
    role: str
    competences: list[str] = []
    playbook: str = ""
    instances: int = 1
    gabarit: str = ""
    skills: list[SkillEquipeRequete] = []
    politique: dict[str, Any] | None = None
    navigateur: bool = False



class EquipeValideeRequete(BaseModel):
    """L'équipe validée : ce qui doit être créé dans le projet (#1040).

    `proposition_id` ne conditionne rien — il **trace** de quelle proposition
    cette équipe sort, et se relit dans le rapport. Le lier à un état serveur
    demanderait de garder les propositions en mémoire, ce que #1039 a refusé
    (« l'équipe se relit, se modifie et se redemande sans conséquence »).

    Une liste `roles` vide est refusée : « je ne veux aucune équipe » se dit en
    ne validant pas, pas en validant le vide.
    """

    proposition_id: str = ""
    roles: list[RoleEquipeRequete] = []

    def roles_valides(self) -> list[RoleValide]:
        """Les rôles en objets du domaine — la politique relue, jamais reconstruite.

        `PolitiqueOutils.from_dict` est le même lecteur que celui du dépôt : une
        politique mal formée lève ici avec le motif exact (« décideur inconnu »,
        « entrée deny … »), avant qu'aucun agent ne soit écrit.
        """
        return [
            RoleValide(
                nom=r.nom,
                role=r.role,
                competences=tuple(r.competences),
                playbook=r.playbook,
                instances=r.instances,
                gabarit=r.gabarit,
                skills=tuple(
                    SkillRetenu(
                        nom=s.nom, chemin=s.chemin, commandes=tuple(s.commandes)
                    )
                    for s in r.skills
                ),
                politique=(
                    None if r.politique is None else PolitiqueOutils.from_dict(r.politique)
                ),
                navigateur=r.navigateur,
            )
            for r in self.roles
        ]



class RecrutementDecisionRequete(EquipeValideeRequete):
    """Corps du geste qui valide — ou décline — l'équipe proposée dans le fil (#1146).

    L'équipe validée **dans la forme de `POST /api/projets/{id}/equipe`**, parce
    que c'est la même création (#1040) : ce qui repart est ce qui a été montré,
    playbook et politique compris. S'y ajoutent `approuve` — décliner ne porte
    aucun rôle, `roles` est alors ignoré — et `conversation`, au sens qu'elle a
    sur un envoi.

    Ni projet ni objectif : les deux sont sur la demande que le fil porte
    (`DemandeRecrutement`), et un corps qui les redirait ouvrirait une seconde
    façon de décider où l'équipe naît.
    """

    approuve: bool
    conversation: str | None = None



class DeclarationProjetRequete(BaseModel):
    """Corps du geste qui accepte — ou refuse — le projet que le fil propose (#1294).

    `approuve`, et rien d'autre de fond : nom, dossier et versionnement sont sur la
    proposition que le fil porte (`DemandeProjet`), et c'est elle qui fait foi. Une
    correction ne passe pas par ici — elle se dit dans la conversation et appelle
    une proposition nouvelle, revérifiée (veille de #1294). `conversation` a le
    sens qu'elle a partout ailleurs sur ce canal.
    """

    approuve: bool
    conversation: str | None = None



class GesteRunRequete(BaseModel):
    """Corps du geste qui confirme — ou écarte — le geste sur un run que le fil propose (#1179).

    `approuve`, et rien d'autre de fond : l'action, le run et les bornes d'une
    relance sont sur la proposition que le fil porte (`GesteRunPropose`), et c'est
    elle qui s'exécute. « Plutôt 10 $ » ne passe pas par ici : il se dit dans la
    conversation, et appelle une carte nouvelle. `conversation` a le sens qu'elle a
    partout ailleurs sur ce canal.
    """

    approuve: bool
    conversation: str | None = None



class ReglementRequete(BaseModel):
    """Corps du geste qui confirme — ou écarte — le règlement que le fil propose (#1183).

    `approuve`, et rien d'autre de fond : la question ou la validation visée, la
    réponse qui partira et la raison d'un refus sont sur la proposition que le fil
    porte (`ReglementPropose`), et c'est elle qui se règle. « Dis-lui plutôt MySQL »
    ne passe pas par ici : il se dit dans la conversation, et appelle une carte
    nouvelle — la règle du geste sur un run (#1179). `conversation` a le sens qu'elle
    a partout ailleurs sur ce canal.
    """

    approuve: bool
    conversation: str | None = None



class DecisionPieceRequete(BaseModel):
    """Corps du geste qui tranche la pièce d'outillage que le fil propose (#1161).

    `decision` — `ecrire`, `passer` ou `plus-tard` —, et rien d'autre de fond : le
    chemin, le contenu et le diff sont sur la pièce que le fil porte
    (`PieceProposee`), et c'est elle qui s'écrit. `piece` est l'**empreinte** de la
    version que la carte montrait : une désignation, qui fait tomber sur le `409` le
    double clic qui écrirait sinon la pièce suivante, déjà proposée. Une correction
    ne passe pas par ici : elle se **dit** dans la conversation, et appelle une pièce
    revérifiée.
    """

    decision: str
    piece: str = ""
    conversation: str | None = None



class SecretPoolRequete(BaseModel):
    """Une valeur de secret saisie pour une intégration du pool (#133).

    `cle` est le nom de la variable `${VAR}` du gabarit (ex. `GITLAB_TOKEN`),
    `valeur` ce que l'humain saisit (token, canal d'appairage, token OAuth
    importé). `expire_le` (ISO 8601) n'a de sens que pour un token OAuth
    importé (`oauth_importe`) : c'est l'échéance qui rend sa validité visible.
    Une valeur vide est ignorée (le secret reste à configurer).
    """

    cle: str
    valeur: str
    expire_le: str | None = None



class IntegrationPoolRequete(BaseModel):
    """Corps d'ajout d'une intégration au pool projet (#133) depuis la bibliothèque.

    `registre_id` désigne l'entrée **curée** à instancier (garde-fou
    supply-chain, docs/19 — un id hors registre est refusé). `nom` nomme la
    liaison (le préfixe d'outils `mcp__<nom>__…`, défaut : l'id) ; `secrets`
    porte les valeurs saisies **une seule fois**, stockées chiffrées côté
    serveur dans le coffre projet partagé.
    """

    registre_id: str
    nom: str | None = None
    secrets: list[SecretPoolRequete] = []



class AdmissionRequete(BaseModel):
    """Corps de l'admission d'une entrée découverte dans l'allowlist (#678).

    `registre_id` désigne l'entrée **découverte** à admettre — celle que l'écran
    vient de lister avec ses signaux de confiance (éditeur, version, dépôt,
    statut). `par` est qui admet, `note` pourquoi : l'admission est un geste
    **tracé**, et une trace sans auteur ni raison ne sert à rien six mois plus
    tard. Aucun des deux n'est **exigé** — refuser une admission faute de nom
    n'ajouterait aucune sécurité (rien ne prouve un nom saisi ici) et
    déplacerait la porte du côté du formulaire.
    """

    registre_id: str
    par: str = ""
    note: str = ""



class RevocationRequete(BaseModel):
    """Corps de la révocation d'une admission (#678).

    `par` et `motif` sont la trace du geste inverse — pourquoi on retire de
    l'allowlist ce qu'on y avait fait entrer. Ils ne conditionnent rien : ce qui
    compte est que la révocation ait lieu, pas qu'elle soit bien remplie.
    """

    par: str = ""
    motif: str = ""



class ActivationsMcpRequete(BaseModel):
    """Corps de l'activation des intégrations du pool pour un agent (#133).

    Remplacement intégral : la liste `integrations` (des ids du pool) devient
    l'ensemble activé de l'agent — une liste vide le désactive de toutes. C'est
    ce que pose l'interrupteur par agent (critère #133).
    """

    integrations: list[str]



class PolitiquePermissionsRequete(BaseModel):
    """Corps de la politique de permissions écrite pour un agent (#262).

    Remplacement intégral, comme les activations MCP au-dessus : les trois
    listes deviennent la politique de l'agent, toutes vides valant « rien
    d'interdit, rien d'arbitré, tout ce que le profil expose ».

    Les valeurs ne sont **pas** jugées ici : le modèle ne fixe que la forme du
    corps, et c'est `PermissionStore.ecrire` qui refuse une entrée mal formée
    avec **son** motif (« entrée deny 'mon outil' — nom d'outil attendu… »).
    Recopier la règle en validateur Pydantic donnerait deux définitions de ce
    qu'est une entrée admissible, dont la seconde finirait par diverger du
    moteur qui l'applique.

    `ask` accepte les deux formes que la lecture accepte (#586) — objet
    `{"<outil>": "<décideur>"}` ou liste, `humain` par défaut : un client qui
    renvoie ce qu'il a lu, sous l'une ou l'autre forme, n'a rien à convertir.

    `portees` (#1226) est ici pour la raison qui la fait **toujours sortir** de
    `to_dict` : un écran qui renvoie ce qu'il a lu ne doit pas perdre en chemin
    la borne d'un cran. Un corps qui l'omet écrit une politique sans portée — ce
    que veut dire, par exemple, « repartir d'une politique vide ».
    """

    allow: list[str] = []
    ask: dict[str, str] | list[str] = {}
    portees: dict[str, str] = {}
    deny: list[str] = []
