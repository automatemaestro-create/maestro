"""L'équipe d'un projet, **proposée** par son analyse (#1039, lot 3 de #1021).

Le chantier de [docs/37](../../docs/37-decision-equipe-sur-mesure.md) : un projet
naît sans agent, et l'analyse lui propose son équipe — rôles, raisons, nombre
d'instances, playbooks, skills de l'outillage qu'ils branchent et autorisations.
L'utilisateur la valide, et c'est **elle seule** qui crée quoi que ce soit
(#1040) :

    from maestro.equipe import proposer_equipe

    proposition = proposer_equipe(
        analyse.constats, analyse.recommandation,
        projet_id=projet.id, source=analyse.source_manifeste(),
    )
    proposition.resume                     # "Développeur ×2, QA — 2 rôle(s), 3 instance(s) ; …"
    proposition.roles[0].raison            # pourquoi ce rôle, et le fichier qui le prouve
    proposition.roles[0].skills            # les skills du projet qu'il branche
    proposition.roles[0].autorisations     # chaque cran avec SA raison (#716)
    proposition.roles[0].politique()       # la `PolitiqueOutils` que #1040 persistera
    proposition.ecartes                    # ce qui n'est pas proposé, et pourquoi

Ce paquet vient **après** celui de l'outillage, et il en dépend dans ce sens-là
seulement : une équipe *branche* les skills que l'outillage a recommandés
(`maestro.outillage`). Les deux sources d'outillage s'y rejoignent sans qu'il
ait à les distinguer — un projet analysé (#1030) et un projet neuf dont on a
recueilli les choix (#1031) rendent la même paire `Constats` / `Recommandation`.

Cinq modules, et la frontière entre eux est celle de ce qui décide :

- `maestro.equipe.modele` — les formes, **inertes** : elles décrivent et
  sérialisent, elles ne touchent à rien ;
- `maestro.equipe.gabarits` — les cinq rôles que Maestro sait proposer, dérivés
  des agents figés devenus **gabarits** (docs/37 §2.1), et la règle qui dit si
  ce projet-là en appelle un ;
- `maestro.equipe.proposition` — des constats et de l'outillage à l'équipe, avec
  pour chaque rôle sa raison, son endroit, ses instances et ses autorisations ;
- `maestro.equipe.creation` (#1040) — de l'équipe **validée** aux trois artefacts
  d'un agent de projet : sa fiche (playbook compris, skills branchés dedans), sa
  politique d'autorisations, sa capacité. Pur lui aussi — l'écriture est le seul
  verbe de `maestro.controltower.equipe` qui touche un dépôt ;
- `maestro.equipe.manque` — le rôle qui **manquerait** à une équipe déjà créée
  pour prendre une tâche (#1041). Le seul des cinq qui regarde un run en cours
  plutôt qu'un projet qui naît, et il n'en recrute pas davantage : il nomme, le
  moteur signale, et le recrutement reste un geste validé hors du run
  (docs/37 §3.5).

⚠ **La politique proposée laisse `allow` ouvert**, et c'est une décision. Une
liste `allow` non vide est *fermée* (`maestro.agents.permissions`) : la remplir
avec les outils du rôle refuserait tout le reste, à commencer par les canaux
in-process de Maestro — poser une question (#1023), consigner une décision
(#1024), signaler un blocage —, que l'analyse d'un projet ne peut pas énumérer
et dont le refus rendrait l'agent muet. Les cinq politiques livrées avec le
dépôt (`core/permissions/`) ouvrent toutes leur `allow` pour la même raison. Ce
qu'un rôle tient se lit dans `RolePropose.outils`, qui n'est **pas** une
politique. Ne pas « compléter » l'une avec l'autre.

**Les promesses du paquet, et où elles tiennent.** *Rien n'est créé* : aucun
module n'ouvre un fichier en écriture, aucun n'appelle un `ecrire` de dépôt
d'agents, et `PropositionEquipe.to_dict()` le rend lisible par l'appelant
(`cree`, `validation`). *Rien n'est deviné* : chaque rôle porte la `Piece` du
projet qui le justifie, chaque rôle écarté sa raison, et chaque autorisation la
sienne. *L'orchestrateur n'est jamais recruté* : il n'a pas de gabarit, et il
est écarté nommément (docs/37 §4.2).

Ce que ce paquet ne fait pas : **écrire les playbooks**. Il pose celui du
gabarit et l'`intention` dont #257 tirera celui de ce projet ; l'appel modèle
vit dans `maestro.controltower.equipe`, seule couche qui connaisse un
fournisseur. Ce que la proposition rend est **servi par l'API** —
`POST /api/projets/{id}/equipe/proposition`.
"""

from __future__ import annotations

from maestro.equipe.creation import (
    AUCUN_SKILL,
    AUCUN_SKILL_ECRIT,
    INSTANCES_MAX_CREEES,
    INTRO_SKILLS,
    TITRE_SKILLS,
    AgentCree,
    EquipeCreee,
    Refus,
    RoleValide,
    SkillRetenu,
    capacite,
    definition,
    playbook_branche,
    refus_de,
    skills_constates,
)
from maestro.equipe.gabarits import (
    GABARITS,
    INSTANCES_MAX_PROPOSEES,
    LANGAGES_INTERFACE,
    PART_SUBSTANTIELLE,
    RAISON_UNE_INSTANCE,
    Gabarit,
    Justification,
)
from maestro.equipe.manque import (
    RoleManquant,
    competences_non_couvertes,
    role_manquant,
)
from maestro.equipe.modele import (
    CRANS,
    ORIGINE_PLAYBOOK_GABARIT,
    ORIGINE_PLAYBOOK_GENERE,
    ORIGINES_PLAYBOOK,
    PREFIXE_ID,
    VERSION_PROPOSITION,
    AutorisationProposee,
    PropositionEquipe,
    RoleEcarte,
    RolePropose,
    SkillBranche,
    nouvel_id,
)
from maestro.equipe.proposition import (
    ECARTE_ORCHESTRATEUR,
    OUTIL_EXECUTION,
    REGIME_EXECUTION,
    avec_playbook,
    proposer_equipe,
)

__all__ = [
    "AUCUN_SKILL",
    "AUCUN_SKILL_ECRIT",
    "CRANS",
    "ECARTE_ORCHESTRATEUR",
    "GABARITS",
    "INSTANCES_MAX_CREEES",
    "INSTANCES_MAX_PROPOSEES",
    "INTRO_SKILLS",
    "LANGAGES_INTERFACE",
    "ORIGINES_PLAYBOOK",
    "ORIGINE_PLAYBOOK_GABARIT",
    "ORIGINE_PLAYBOOK_GENERE",
    "OUTIL_EXECUTION",
    "PART_SUBSTANTIELLE",
    "PREFIXE_ID",
    "RAISON_UNE_INSTANCE",
    "REGIME_EXECUTION",
    "TITRE_SKILLS",
    "VERSION_PROPOSITION",
    "AgentCree",
    "AutorisationProposee",
    "EquipeCreee",
    "Gabarit",
    "Justification",
    "PropositionEquipe",
    "Refus",
    "RoleEcarte",
    "RoleManquant",
    "RolePropose",
    "RoleValide",
    "SkillBranche",
    "SkillRetenu",
    "avec_playbook",
    "capacite",
    "competences_non_couvertes",
    "definition",
    "nouvel_id",
    "playbook_branche",
    "proposer_equipe",
    "refus_de",
    "role_manquant",
    "skills_constates",
]
