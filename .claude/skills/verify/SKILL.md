---
name: verify
description: Vérifier de bout en bout la Control Tower (API FastAPI + UI Next.js) en pilotant un vrai navigateur
---

# Vérifier Maestro de bout en bout

## Ce que cette vérification apporte (et ce qu'elle ne refait pas)

Depuis le ticket #124, `apps/web` a une **suite de tests** (Vitest + Testing
Library, `apps/web/tests/`, jouée par `npm test` et par le job CI `web-build`).
Elle couvre déjà la logique et le rendu de la refonte UX #116 : navigation,
thème, notifications, paramètres, visite guidée, assistant. **Ne pas la
redoubler ici.**

Ce qui n'appartient qu'à cette vérification, parce qu'il y faut un vrai
navigateur et un vrai backend : la **WebSocket**, l'**absence de rechargement**,
la **reprise après coupure**, et le fait que l'ensemble se tienne une fois
branché sur l'API.

## Lancer l'ensemble (API + UI)

Ne pas réécrire de lanceur ad hoc : le script du ticket #65 fait tout
(nettoyage des anciennes sessions sur :8000/:3000, API de démo sur bus
mémoire — `maestro.controltower.demo`, app FastAPI réelle + scénario
d'événements factices en continu —, UI Next.js pointée dessus) :

```bash
bash scripts/controltower/start.sh --no-browser   # UI sur :3000, API sur :8000
bash scripts/controltower/start.sh --stop         # arrêt
```

`--no-browser` est important ici : sans lui, le script ouvre sa propre fenêtre
et **arrête la stack dès qu'elle est fermée** (#149) — ce qui couperait l'API
sous le navigateur qu'on pilote. Avec l'option, c'est `--stop` qui fait foi.

Dans un worktree, les ports sont dédiés (#152) : `MAESTRO_PORT_API` /
`MAESTRO_PORT_UI` sont déjà posés — viser ceux-là plutôt que 8000/3000 en dur.

Pour un scénario **sur mesure** (autres événements, autre timing), s'inspirer
de `maestro/controltower/demo.py` : `create_app(bus=InMemoryEventBus())` sous
uvicorn programmatique, événements `EVENEMENT_*` publiés depuis une tâche
asyncio du même process, toujours via `.venv/Scripts/python.exe` (jamais le
python système). L'UI lit `NEXT_PUBLIC_MAESTRO_API_URL` au démarrage du dev
server (inlinée au build en prod).

## Piloter le navigateur

Pas de navigateur Playwright téléchargé : `playwright-core` (npm, ~3 Mo) +
Edge déjà installé sur la machine —
`chromium.launch({ channel: "msedge", headless: true })`. Installer
`playwright-core` dans le scratchpad, pas dans le repo.

**Première chose à faire, avant le `goto` : neutraliser la visite guidée.** Sur
un profil neuf elle s'ouvre d'elle-même (#122), et son voile
(`fixed inset-0 z-40`) **absorbe les clics** — tout le reste du scénario
échouerait sans que la cause soit lisible.

```js
// Avant le goto : la visite se croit déjà vue et ne s'ouvre pas.
await page.addInitScript(() => localStorage.setItem("maestro.guide.vu", "1"));
// Ou, si elle est déjà ouverte : la quitter.
await page.keyboard.press("Escape");
```

C'est aussi ce qu'il faut **défaire** quand c'est la visite elle-même qu'on
vérifie : profil sans cette clé, ou relance depuis le menu d'aide.

## Flux qui valent la peine d'être pilotés

Le cœur temps réel, ce que les tests unitaires ne voient pas :

- badge « Temps réel connecté » en barre supérieure (WebSocket ouverte) ;
- apparition/évolution des cartes Kanban pendant que le scénario publie ;
- réassignation via le `<select>` d'une carte → la carte change d'agent et le
  fil d'activité affiche « réassignée à … » ;
- validation humaine (#48) : publier un `validation.demande` → la demande
  apparaît **aux deux endroits**, en tête du tableau de bord et au badge de la
  cloche (#119) ; la trancher depuis l'un ou l'autre → elle disparaît des deux,
  et un abonné du bus reçoit le `validation.decision` (c'est lui qui libère le
  moteur en pause) ;
- **absence de rechargement** : poser `window.__marqueur = 42` après le goto et
  vérifier qu'il est intact à la fin — y compris après avoir changé de page par
  la sidebar : le shell (#117) ne doit pas se remonter d'une page à l'autre ;
- **coupure/reprise** : tuer le process backend → badge « Reconnexion… » ; le
  relancer → badge revient (backoff ≤ 10 s) et l'état se recharge.

Et ce que la refonte a ajouté au shell, quand il s'agit de le voir vivre plutôt
que d'en retester la logique :

- le **coût cumulé** de la barre supérieure suit ce que les agents rapportent,
  de page en page ;
- le **thème** (#118) : basculer en sombre, changer de page, recharger — le
  choix tient, et aucun flash clair n'apparaît au chargement ;
- l'**assistant** (#123) répond vraiment (fil `/api/chat/assistance`), et son
  historique se retrouve après un changement de page.

## Pièges

- La **visite guidée bloque les clics** sur un profil neuf — voir plus haut.
  C'est la cause n°1 d'un scénario qui échoue dès sa première interaction.
- Vérifier sur une stack **de production** dès que la page doit être stable : en
  mode dev, la WebSocket de rechargement à chaud de Next échoue en navigateur
  headless et bloque l'hydratation — les pages restent en « Reconnexion… /
  Chargement de l'état… ». C'est le choix qu'a fait
  `scripts/presentation/captures.sh` : s'en inspirer plutôt que le refaire.
- Le lint React (`react-hooks/set-state-in-effect`) interdit un setState
  synchrone dans un effet : passer par un `setTimeout` (cf.
  `lib/useControlTower.ts`, `components/Shell.tsx`).
- `docker ps` échoue si Docker Desktop est arrêté — inutile pour cette
  vérification, ne pas le démarrer pour ça.
