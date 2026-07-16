# Run réel sur fournisseur non-Anthropic — rapport (ticket #99)

**Version :** 0.1
Cette page lève la **réserve n°4** du verdict go/no-go de fin de Phase 2
([docs/13 §5](./13-demo-v1.md)) : le fournisseur non-Anthropic (#69, bascule par
`MAESTRO_PROVIDER`) et l'observabilité Langfuse (#79-#81) n'avaient jamais été exercés
**en conditions réelles** — tous deux configuratifs, couverts par les tests seulement.
Elle consigne : deux runs réels menés de bout en bout sur un endpoint non-Anthropic
(§1-§3), les écarts observés par rapport aux runs Claude (§4), et la validation Langfuse
réelle du 2026-07-15 (§5).

> **Verdict court** : l'agnosticisme fournisseur (objectif O7, ENF-11) est démontré
> **sur pièces** — bascule 100 % configurative, moteur et logique d'agent inchangés,
> comptabilité et traces au rendez-vous. Deux limites objectivées : ce dialecte ne
> rapporte **pas de coût** (plafond de dépense sans prise → #113) et la **qualité**
> du plan comme du livrable dépend fortement du modèle servi (§4).

---

## 1. Dispositif

| Élément | Valeur |
|---------|--------|
| Fournisseur Maestro | `openai` (`OpenAICompatProvider` — dialecte chat completions, #69) |
| Endpoint | **Ollama 0.31.2 local** (`http://localhost:11434/v1`), **sans clé** — le chemin nominal « endpoint local sans auth » du fournisseur |
| Modèles exercés | `qwen2.5:1.5b` (986 Mo) puis `qwen2.5:3b` (1,9 Go) — deux runs, pour mesurer la sensibilité à la taille du modèle |
| Machine | Poste de dev local (Windows 11), inférence CPU |
| Garde-fous | `--plafond-cout 1` (1 $), `--timeout 600` s/tâche, relances par défaut (2, ENF-06) |
| Observabilité | Langfuse actif (clés du `.env` — aucune option CLI, #81) |

La bascule est **purement configurative** — aucune modification du code du moteur ni de
la logique d'agent, conformément au critère du ticket :

```bash
MAESTRO_PROVIDER=openai MAESTRO_MODEL=qwen2.5:3b OPENAI_BASE_URL=http://localhost:11434/v1 \
  maestro-run --json --trace --plafond-cout 1 --timeout 600 \
  "Rédiger une courte page Markdown présentant Maestro, un orchestrateur multi-agents : \
ce qu'il fait, pour qui, et comment l'essayer."
```

`MAESTRO_MODEL` (requis hors Claude) impose le modèle unique à l'orchestrateur **et**
aux exécutants ; le fournisseur ne sachant pas exécuter d'agent outillé, chaque rôle
retombe automatiquement sur son **livrable texte** (`UnsupportedCapability` → repli
prévu du moteur, [executor](../maestro/engine/executor.py)) — sans erreur ni code
spécifique.

## 2. Résultats chiffrés

Objectif identique pour les deux runs (simple, un seul livrable attendu) ; sorties
capturées via `--json` et `--trace`.

| Run | Modèle | `run_id` | Tâches | Appels | Tokens (entrée / sortie / total) | Coût | Durée | Exit |
|-----|--------|----------|--------|--------|----------------------------------|------|-------|------|
| A | `qwen2.5:1.5b` | `11c5fb372b4e` | 1/1 réussie | 2 | 897 / 135 / **1 032** | inconnu (`null`) | 2,9 s | 0 |
| B | `qwen2.5:3b` | `6c9b7be5928a` | 1/1 réussie | 2 | 950 / 445 / **1 395** | inconnu (`null`) | 12,1 s | 0 |

La **mécanique complète** est identique aux runs Claude : planification → routage
(développeur) → exécution → journal (#8) → rapport agrégé → trace Langfuse. Le premier
appel après démarrage paie le chargement du modèle en mémoire (~37 s observés sur le
1.5b, hors runs) ; ensuite, 1 à 8 s par appel sur CPU.

## 3. Qualité des livrables (lecture critique)

- **Run A (1,5 Md de paramètres)** : plan **dégénéré** — une seule tâche dont titre et
  description sont la recopie littérale des `"..."` de l'exemple du prompt
  orchestrateur ; livrable hors-sujet (le modèle s'excuse de « manquer de détails »).
  Le run est mécaniquement réussi (exit 0, tâche « terminée ») mais inutilisable.
- **Run B (3 Md)** : plan nommé et cohérent (« Écrire la page de présentation »),
  livrable = une vraie page Markdown structurée — mais au contenu **halluciné** (Maestro
  y devient une solution logistique) : sans ancrage documentaire, un petit modèle
  invente le sujet.
- Aucun des deux ne respecte le guidage « 3 à 5 tâches » du prompt orchestrateur —
  accepté par conception (fourchette indicative, pas une règle du schéma de plan).

## 4. Écarts observés par rapport aux runs Claude

1. **Exécution texte seule** : pas d'exécution agentique outillée (`run_agent` refusé →
   repli texte) ; aucun fichier produit, aucun outil dans le journal. Prévu et assumé
   (docs/04 §4 : l'exécution multi-fournisseurs outillée reste à construire).
2. **Coût non rapporté** : le dialecte chat completions ne porte pas de prix —
   `cout_usd` reste `null` (« inconnu », distinct de 0). Conséquence mesurée : le
   plafond de dépense (1 $) était armé mais **sans prise** — seuls les tokens sont
   comptés. Ticketisé → **#113**. À noter : Langfuse affiche `totalCost: 0` là où
   Maestro dit « inconnu ».
3. **Qualité très en deçà** des runs Claude de la démo V1 (docs/13 §4) : plans à une
   tâche, contenu halluciné (§3). L'écart est un écart de **modèle**, pas de moteur —
   la même mécanique a produit les deux.
4. **Latence et coût inversés** : 12 s et 0 $ le run complet en local, là où la démo V1
   coûtait ~1-5 $ et quelques minutes par run Claude. Un endpoint local est viable pour
   exercer la plomberie (CI, tests de charge, démos hors ligne) — pas pour produire.

## 5. Validation Langfuse en conditions réelles

- **2026-07-15 — runs Claude** (validation initiale, consignée ici) : configuration
  `.env` validée de bout en bout ; **ingestion asynchrone** confirmée — l'UI Langfuse
  affiche la trace avec quelques minutes de retard sur l'API publique, sans perte.
- **Re-confirmée sur ce dispositif non-Anthropic** : les traces des runs A et B
  (`11c5fb372b4e`, `6c9b7be5928a` — id = `run_id`, docs/07 §2.2) sont ingérées et
  vérifiées via `GET /api/public/traces/<run_id>` : 3 observations chacune
  (planification, début de tâche, tâche), scores d'évaluation (#80) posés —
  `run-reussi = 1`, `taux-reussite = 1`.

## 6. Suites

- **#113** — rendre le plafond de dépense opérant sur les fournisseurs sans coût
  rapporté (plafond en tokens ou barème de prix par modèle).
- L'exécution **outillée** multi-fournisseurs et l'ancrage documentaire des petits
  modèles restent hors périmètre du POC (docs/04 §4).
