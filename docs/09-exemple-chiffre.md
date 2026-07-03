# Exemple concret & estimation de coûts — Maestro

**Version :** 0.1
Cet exemple déroule un projet réaliste de bout en bout : combien d'agents, combien de temps, combien ça coûte — en supposant un **abonnement Claude à 20 $/mois** comme point de départ.

> ⚠️ Les estimations de tokens sont des **ordres de grandeur** (le coût réel dépend de la taille du code, du nombre de relances et de l'usage du cache de prompts). Les tarifs sont ceux de **mi-2026** et peuvent évoluer — à reverifier avant de figer un budget.

---

## 1. Le projet exemple

**Objectif donné à Maestro :** « Ajoute l'authentification par e-mail / mot de passe à mon application web existante. »

C'est une fonctionnalité complète et transverse : base de données, back-end, interface, tests et déploiement — idéale pour illustrer le travail d'équipe des agents.

---

## 2. Combien d'agents ?

Le Chef de projet découpe l'objectif en **7 tickets**, mobilisant **6 agents spécialisés**, avec **jusqu'à 3 agents en parallèle**.

| # | Ticket | Agent | Modèle | Dépend de |
|---|--------|-------|--------|-----------|
| 1 | Découpage en tickets + synthèse finale | 🧭 Chef de projet | Opus | — |
| 2 | Schéma BDD (users, sessions) + migration | 🗄️ Base de données | Sonnet | 1 |
| 3 | Endpoints `/signup` `/login` `/logout` + hachage | 💻 Développeur | Sonnet | 2 |
| 4 | Spec de l'écran connexion / inscription | 🎨 Designer | Sonnet | 1 |
| 5 | Intégration de l'interface (front) | 💻 Développeur | Sonnet | 3, 4 |
| 6 | Tests e2e du parcours d'inscription | 🧪 QA | Sonnet | 5 |
| 7 | Pipeline CI + configuration de déploiement | ⚙️ DevOps | Sonnet | 6 |
| — | Routage automatique (1 appel / ticket) | Routeur | Haiku | — |

Le **Développeur** peut tourner en **2 instances** (back-end et front-end) selon la charge.

---

## 3. Combien de temps ?

Grâce au parallélisme et aux dépendances, le chemin critique est court. Déroulé typique :

```
T+0   🧭 Chef de projet découpe l'objectif ............ ~2 min
T+2   🗄️ BDD (schéma)   ∥   🎨 Designer (écran) ........ ~6 min   (2 en //)
T+8   💻 Dev back-end (dépend de la BDD) ............... ~7 min
T+15  💻 Dev front-end (dépend du back + design) ....... ~6 min
T+21  🧪 QA — tests e2e ............................... ~6 min
T+27  ⚙️ DevOps — déploiement → ⏸ validation humaine ... ~4 min + attente
```

- **Temps réel (parallélisé) :** ≈ **30 à 35 minutes** (hors temps d'attente de tes validations).
- **Temps cumulé « agent » (si séquentiel) :** ≈ **1 heure**.
- **Agents simultanés :** jusqu'à **3**.

> Ces durées supposent des tâches de taille modérée. Un gros code ou plusieurs allers-retours QA → Dev rallongent le tout.

---

## 4. Combien ça coûte ?

### 4.1 Deux façons de payer — à ne pas confondre

| | **Abonnement 20 $/mois (Pro)** | **API (paiement au token)** |
|---|---|---|
| Facturation | Forfait : **budget d'usage partagé** (fenêtre glissante de 5 h + plafond hebdomadaire), commun au chat et à Claude Code / Agent SDK | **À l'usage**, par million de tokens |
| Bon pour | Prototypage, POC, usage **ponctuel** | **Production**, agents tournant en continu |
| Limite | On atteint des **plafonds de débit**, pas une facture au token | Pas de plafond, mais coût proportionnel |

**En clair :** sur le plan à 20 $, un projet comme celui-ci ne « coûte » pas un montant en dollars — il **consomme une part de ton budget d'usage**. Un run multi-agents gourmand entame nettement la fenêtre de 5 h. Tu peux donc lancer **quelques fonctionnalités de ce type par semaine** dans les limites du Pro, mais **pas** une flotte d'agents en continu.

### 4.2 Tarifs API utilisés (mi-2026, par million de tokens)

| Modèle | Entrée | Sortie |
|--------|--------|--------|
| Haiku 4.5 | 1 $ | 5 $ |
| Sonnet 4.6 | 3 $ | 15 $ |
| Opus 4.8 | 5 $ | 25 $ |

Réductions possibles : **traitement par lots −50 %**, **cache de prompts −90 %** sur l'entrée mise en cache (très utile car les agents relisent souvent le même code).

### 4.3 Estimation détaillée (via l'API)

| Étape | Modèle | Tokens entrée | Tokens sortie | Coût |
|-------|--------|---------------|---------------|------|
| Découpage + synthèse | Opus | 70 k | 18 k | 0,80 $ |
| Schéma BDD + migration | Sonnet | 180 k | 30 k | 0,99 $ |
| Endpoints back-end | Sonnet | 240 k | 45 k | 1,40 $ |
| Spec écran connexion | Sonnet | 120 k | 25 k | 0,74 $ |
| Intégration front | Sonnet | 220 k | 40 k | 1,26 $ |
| Tests e2e (QA) | Sonnet | 160 k | 30 k | 0,93 $ |
| Pipeline CI + déploiement | Sonnet | 150 k | 25 k | 0,83 $ |
| Routage (7 appels) | Haiku | 21 k | 3 k | 0,04 $ |
| **Sous-total** | | | | **≈ 7,0 $** |

- **+ relances / itérations** (QA renvoie au Dev, corrections) : **≈ 9 $**.
- **− cache de prompts** (le code relu est mis en cache) : **≈ 4 à 5 $**.

### 4.4 Le chiffre à retenir

> **Une fonctionnalité complète comme celle-ci coûte ≈ 7 à 12 $ en API** (≈ 4–5 $ avec cache de prompts, davantage si beaucoup de relances).
> Sur l'**abonnement à 20 $/mois**, elle ne coûte rien de plus — mais consomme une **part notable** de ton budget d'usage de la fenêtre de 5 h.

---

## 5. Recommandation budgétaire

| Phase / usage | Quoi utiliser | Pourquoi |
|---------------|---------------|----------|
| **POC & développement** (Phase 0) | Abonnement **Pro 20 $** | Suffisant pour prototyper, explorer, lancer des runs ponctuels |
| **Usage interactif intensif** | **Max** (100 $ / 200 $) | Limites de débit bien plus élevées |
| **Production** (agents en continu, multi-projets) | **API au token** | Pas de plafond de débit ; coût maîtrisé par les plafonds applicatifs de Maestro |

**Leviers de coût intégrés à Maestro** (voir [doc 02 §9](./02-stack-technique.md) et [cahier des charges ENF-07](./00-cahier-des-charges.md)) : modèle léger par défaut (Haiku/Sonnet plutôt qu'Opus), **plafonds par tâche/jour**, **cache de prompts**, et privilégier l'**état partagé** plutôt que des conversations inter-agents coûteuses.

---

## 6. Comparaison avec une équipe de dev humaine

Pour la **même fonctionnalité** (authentification e-mail), voici ce qu'elle représente côté équipe humaine.

### 6.1 Effort humain typique

Une authentification e-mail / mot de passe demande **20 à 30 heures** de développement pour une version simple, et **40 à 80 heures** en intégrant les cas limites et la revue de sécurité — soit **≈ 3 à 8 jours-homme**.

### 6.2 Tarifs de référence (2026)

- **Freelance full-stack (France) :** TJM moyen **≈ 550 €/jour** (450 € junior → 650–700 € senior).
- **Agence / ESN :** souvent **700–900 €/jour**.
- **International :** freelance **30–150 $/h**, agence **100–300 $/h**.

### 6.3 Coût de la fonctionnalité côté humain

| Scénario | Effort | Coût (freelance France) |
|----------|--------|-------------------------|
| Simple (~24 h) | ~3 jours | **≈ 1 350 – 2 400 €** |
| Robuste (sécurité, cas limites, ~60 h) | ~7–8 jours | **≈ 3 400 – 5 400 €** |
| Via agence / ESN | idem | **≈ 2 600 – 9 000 €** |

### 6.4 Face à face

| | Équipe humaine (France, freelance) | Maestro (agents IA) |
|---|---|---|
| Temps écoulé | 3 à 8 jours | **~30–35 min** (parallélisé) |
| Coût direct | **~1 500 – 5 500 €** | **~7 – 12 $** (≈ 7–11 €) en API |
| Supervision | incluse dans l'effort | + revue humaine (~0,5–1 j ≈ 300–650 €) |
| **Coût réaliste total** | **~1 500 – 5 500 €** | **~300 – 660 €** |

> Même en intégrant la **revue humaine indispensable**, l'approche agents reste **~3 à 10× moins chère** et surtout **bien plus rapide** sur cette fonctionnalité.

### 6.5 Lecture honnête (à ne pas survendre)

- **Ce n'est pas strictement équivalent.** Les agents produisent une **première implémentation** ; un humain doit la **spécifier** et surtout la **relire** — l'auth est un code **sensible côté sécurité**, à ne jamais livrer sans validation.
- **Coûts cachés côté IA :** construire et maintenir Maestro, l'infra, l'abonnement / API de base. Ils s'**amortissent** sur de nombreuses fonctionnalités, mais existent.
- **Ce que l'humain apporte :** jugement, responsabilité, gestion de l'ambiguïté et des cas limites, expertise sécurité — difficile à chiffrer mais réel.
- **Alternative humaine réaliste :** souvent une **librairie / service** (ex. Clerk) ou un **boilerplate** (~200–500 €) plutôt que du code « from scratch » — l'authentification est déjà largement commoditisée.
- **À retenir :** l'IA effondre le **coût marginal et le délai** d'une première implémentation et **déplace** le rôle humain de « écrire le code » vers « **spécifier + valider** ». C'est un **multiplicateur de force**, pas un remplacement 1:1.

---

*Estimations indicatives — à valider lors du POC en mesurant les tokens réels via l'observabilité (Langfuse). Comparaison humaine : effort et tarifs de référence mi-2026, ordres de grandeur.*
