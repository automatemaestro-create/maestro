# agent Designer

UI/UX, maquettes, design system. Voir `docs/04-specifications-agents.md` (§3.5 : fiche
et playbook du rôle, conformes au gabarit du §1).

## Runtime (ticket #68)

Au-delà de son identité dans le catalogue (`maestro.agents.catalog`), le Designer dispose
d'un **runtime outillé** — un sous-agent du Claude Agent SDK qui traite une tâche de
design **de bout en bout** (cadrer le besoin → produire specs, wireframes et tokens →
vérifier la conformité à la charte) dans un **espace de travail isolé** :

- `maestro.agents.runtime.AgentRuntime` — le runtime **générique** (#35), paramétré
  par le profil du rôle (`maestro.agents.designer.DESIGNER_PROFILE`) ; il orchestre
  l'exécution et capture le livrable (`AgentOutcome` : compte-rendu + fichiers produits :
  spécifications d'écran, maquettes/wireframes HTML ou SVG, design tokens, guide de
  composants).
- `maestro.sandbox` — l'**isolation** : un répertoire temporaire dédié par tâche
  (niveau système de fichiers au POC ; conteneur Docker par tâche prévu ensuite).
- `maestro.providers.ModelProvider.run_agent` — la capacité d'exécution outillée à la
  frontière fournisseur (native de l'Agent SDK côté Claude), optionnelle et agnostique.

La boucle d'orchestration (`maestro.engine`) route les tâches assignées à `designer` vers
ce runtime : les fichiers produits remontent dans le `RunReport`. Si le fournisseur n'a
pas d'exécution outillée, le rôle retombe sur son livrable texte.

**Particularité du rôle** (docs/04 §3.5) : il **respecte le design system et la charte
existants** — il propose, il ne remplace pas la charte sans accord. Au POC, sans MCP
Figma (outils fichiers + shell uniquement), les maquettes se matérialisent en fichiers
et le compte-rendu signale explicitement toute évolution de charte proposée, soumise à
accord avant adoption.

Démo de bout en bout :

```bash
maestro-designer "Conçois la maquette de l'écran de connexion : champs, états d'erreur, responsive"
maestro-designer --keep --json "…"   # conserve l'espace de travail et sort le JSON
```
