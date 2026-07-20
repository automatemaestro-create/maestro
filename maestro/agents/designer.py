"""Profil du rôle Designer — le paramétrage de son runtime outillé (ticket #68).

Cinquième rôle outillé après le Développeur, la Base de données, le QA (#45) et le
DevOps (#67) : là où le catalogue (`maestro.agents.catalog`) décrit le Designer comme
une *identité* (compétences `ui`/`ux`/`design-system`/`figma`), ce module déclare le
**profil** de son runtime outillé : le prompt système, les outils et les consignes avec
lesquels le runtime générique (`maestro.agents.runtime.AgentRuntime`) traite une tâche
de design *de bout en bout* (spécifications d'écran, maquettes/wireframes, design
tokens, guide de composants) dans un espace de travail isolé.

Particularité du Designer (docs/04 §3.5, playbook `agents/designer/README.md`) : il
**respecte le design system et la charte existants** — il propose, il ne remplace pas
la charte sans accord. Les maquettes se matérialisent en fichiers (wireframes HTML/SVG,
specs Markdown, tokens) et le compte-rendu signale explicitement toute évolution de
charte proposée, soumise à accord avant adoption. Depuis le pilote #115 (basculé sur le
serveur MCP **officiel** Figma par #128), sa déclaration MCP versionnée
(`core/mcp/designer.json`, socle #104) lui monte en plus les outils Figma quand un
humain a fourni le token OAuth (`FIGMA_OAUTH_TOKEN`, docs/20) : il crée et lit des
éléments directement dans un fichier Figma — sans rien changer à ce profil.
"""

from __future__ import annotations

from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* Designer : il doit matérialiser un livrable en fichiers
#: (specs d'écran, wireframes HTML/SVG, design tokens, guide de composants) dans son
#: répertoire de travail, et porter la particularité du rôle : la charte existante fait
#: foi, toute évolution n'est qu'une proposition soumise à accord.
_SYSTEM_PROMPT = """\
Tu es l'agent Designer de Maestro. Tu traites une tâche de design de bout en bout : \
écrans, maquettes, parcours, composants, design tokens — et tu produis un livrable \
réellement exploitable.

Tu disposes d'outils (lecture, écriture et édition de fichiers, exploration, shell) et \
d'un répertoire de travail vide et isolé (ton répertoire courant). Matérialise TON \
livrable en fichiers dans ce répertoire (spécifications d'écran, maquettes/wireframes \
HTML ou SVG, design tokens, guide de composants) — n'affiche pas seulement des \
recommandations. Soigne l'accessibilité (contrastes, navigation clavier, libellés).

Garde-fous : reste dans ton répertoire de travail. Tu respectes le design system et la \
charte existants transmis avec la tâche : tu PROPOSES, tu ne remplaces ni ne réécris la \
charte sans accord — toute évolution du design system est signalée comme une \
proposition soumise à accord avant adoption."""

#: Profil du Designer : modèle par défaut du POC (Claude Sonnet, cf. docs/04 §2), outils
#: fichiers + shell (docs/02 §7 : permissions scopées) — les outils Figma arrivent par
#: la déclaration MCP versionnée (core/mcp/designer.json, serveur officiel #128), pas
#: par ce profil. Consignes de conformité à la charte et d'évolution soumise à accord. `nom`
#: correspond à l'agent `designer` du catalogue.
DESIGNER_PROFILE = RoleProfile(
    nom="designer",
    role="Designer",
    modele="claude-sonnet-5",
    outils=DEFAULT_TOOLS,
    prompt_systeme=_SYSTEM_PROMPT,
    intro_tache="Tâche de design (écrans, maquettes, composants) à réaliser de bout en bout :",
    consignes=(
        "Tu travailles dans un répertoire vide et isolé (le répertoire courant). "
        "Écris-y les fichiers du livrable avec tes outils (spécifications d'écran, "
        "maquettes/wireframes HTML ou SVG, design tokens, guide de composants) — ne te "
        "contente pas d'afficher des recommandations. Conforme-toi à la charte et au "
        "design system transmis avec la tâche ; à défaut, énonce les partis pris que "
        "tu proposes."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et signale explicitement toute "
        "évolution de la charte ou du design system que tu proposes — elle reste "
        "soumise à accord avant adoption."
    ),
    workspace_prefix="maestro-designer-",
)
