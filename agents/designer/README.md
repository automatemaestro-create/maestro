# agent Designer

UI/UX, maquettes, design system, tokens. Voir
[`docs/04-specifications-agents.md`](../../docs/04-specifications-agents.md) (§3.5 : fiche du
rôle ; §1 : structure d'un playbook).

## Playbook

Le playbook du rôle — ce que le moteur charge tant que rien n'a été publié depuis l'UI — est un
**document Markdown livré avec le paquet** (#295) :
[`maestro/agents/playbooks_defaut/designer.md`](../../maestro/agents/playbooks_defaut/designer.md).
Il sert **les deux chemins** : `PLAYBOOK_DEFAUTS` (donc l'API et l'éditeur de la Control Tower)
et le prompt système de `DESIGNER_PROFILE`. Il porte :

- la **méthode** du métier — cadrer besoin et parcours avant de dessiner, poser les **états et
  cas limites** de chaque écran (vide, chargement, erreur, droits insuffisants, données qui
  débordent) **avant** le cas nominal, faire de toute valeur récurrente un token nommé et de
  tout motif récurrent un composant, puis vérifier l'accessibilité **en la chiffrant** ;
- le **régime sénior** commun à tous les rôles (`_socle.md`, #293) : la structure, les patrons
  d'interaction et la nomenclature des tokens se tranchent sans demander d'accord ; le
  compte-rendu porte toujours « Décisions & arbitrages » et « Recommandations » ;
- ses **garde-fous** : la charte et le design system existants font foi — il **propose** une
  évolution, il ne la remplace ni ne la réécrit **sans accord**. Choisir un parti pris *dans* la
  charte est réversible et lui appartient ; changer la charte engage tout ce qui s'appuie
  dessus. Une charte posée faute d'en avoir reçu une est **elle aussi** une proposition.

Une version publiée depuis la page `/playbooks` prime dessus et s'applique **à chaud** (#78).
Invariants testés : [`tests/test_playbooks_defaut.py`](../../tests/test_playbooks_defaut.py).
C'est aussi le rôle au **plafond de tours relevé** (120 au lieu de 40, #239) : sa boucle
*rendre → regarder → reprendre* consomme des tours bien plus lourds.

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
