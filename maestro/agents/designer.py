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

from maestro.agents.playbook_du_code import CONSIGNE_RENDU_COMPTE, playbook_du_code
from maestro.agents.runtime import DEFAULT_TOOLS, RoleProfile

#: Prompt système du *runtime* Designer : son playbook « du code » (#295), le document
#: `playbooks_defaut/designer.md` — régime sénior commun compris, et la particularité du
#: rôle : la charte existante fait foi, toute évolution n'est qu'une proposition soumise
#: à accord. Depuis #297 il porte la part métier du spécialiste : méthode (cadrer le
#: besoin et les parcours → poser les états et les cas limites → produire écrans, tokens
#: et composants → vérifier accessibilité et cohérence), latitude nommée sur la
#: structure, les patrons d'interaction et la nomenclature, exigences d'accessibilité
#: **chiffrées** plutôt qu'invoquées, et conduite à tenir quand la charte manque.
_SYSTEM_PROMPT = playbook_du_code("designer")

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
        "design system transmis avec la tâche ; à défaut, pose toi-même le minimum "
        "viable en tokens nommés et présente-le comme une proposition, plutôt que "
        "d'attendre une charte qui ne viendra pas. Cadre les parcours avant de "
        "dessiner, et couvre les états et les cas limites — vide, chargement, erreur, "
        "droits insuffisants, données qui débordent — pas seulement le cas nominal. "
        "Tranche seul la structure, les patrons d'interaction et la nomenclature. "
        "Vérifie l'accessibilité en la chiffrant (contrastes, parcours clavier, focus "
        "visible, libellés) : « conforme AA » sans le chiffre ne vaut rien."
    ),
    consigne_finale=(
        "Quand c'est fait, résume ce que tu as produit et signale explicitement toute "
        "évolution de la charte ou du design system que tu proposes — elle reste "
        f"soumise à accord avant adoption, {CONSIGNE_RENDU_COMPTE}"
    ),
    workspace_prefix="maestro-designer-",
    # Marge accrue (#239) : concevoir est itératif — rendre, regarder, reprendre —
    # et c'est le rôle dont les tours sont les plus lourds (jusqu'à ~71 000 tokens
    # le tour, mesuré sur `concepts-esquisses`, contre ~10 000 pour une validation).
    # C'est ici que le plafond global a cédé : une tâche Figma a épuisé ses tours
    # en conception sans atteindre son livrable (`error_max_turns`, docs/15 §4.3).
    # Revérifié à #297 : la méthode de spécialiste ajoute une passe de vérification
    # d'accessibilité (relire ses écrans, calculer des contrastes) et une couverture
    # d'états plus large — les deux se paient en tours, sur le rôle qui en avait déjà
    # le plus besoin. 120 était déjà dimensionné pour la boucle itérative et garde de
    # la marge : plafond conservé, à surveiller au premier `error_max_turns` observé
    # sur une tâche de design, qui serait alors le signal de le relever — pas avant.
    plafond_tours=120,
)
