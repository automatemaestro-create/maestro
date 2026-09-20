// Le pont de la coque vers la page (#928, lot 7 de #921) — le premier, et il tient en un verbe.
//
// ## Pourquoi il existe maintenant, et pas avant
//
// Le lot 2 (#923) a écrit en toutes lettres que la coque n'exposait AUCUN pont : « ENF-12 veut que
// le front servi soit celui du mode web, donc il ne peut rien attendre de nous ». C'était juste
// tant qu'il n'y avait rien à demander. docs/35 §2.4 nomme pourtant l'ouverture de l'explorateur de
// fichiers comme l'une des TROIS capacités qui justifient une fenêtre, et renvoie au lot 7 : la
// voici. Une PWA ou un onglet ne peuvent pas l'offrir — c'est la ligne de partage.
//
// ## ENF-12 n'est pas levé, il est respecté
//
// La règle est « aucun EMBRANCHEMENT de code applicatif », pas « aucune capacité ». Le front ne
// teste jamais où il tourne : il teste si la fonction est là (`apps/web/lib/poste.ts`), comme il
// testerait `navigator.clipboard`. Dans un onglet elle n'y est pas, et le second geste — copier le
// chemin — reste, présent dans les deux régimes. Aucun `if (electron)` n'a été écrit nulle part.
//
// ## Les trois réglages de sûreté ne bougent pas
//
// `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false` (docs/19, et les tests qui
// lisent `main.js` depuis #929). Un preload sandboxé n'a PAS accès à Node : il ne peut que
// `contextBridge` + `ipcRenderer`, ce qui est exactement la surface qu'on veut. La page ne reçoit
// donc ni `fs`, ni `shell`, ni `ipcRenderer` — seulement une fonction qui envoie une chaîne et
// attend un booléen. C'est le processus principal qui décide ensuite, et qui refuse (`main.js`).
//
// ⚠ NE PAS exposer `ipcRenderer` ni un verbe générique du type `invoke(canal, …)` : ce serait
// rouvrir tout le pont derrière un nom neutre, et la page est servie par un serveur local qu'un
// autre programme du poste peut atteindre.

'use strict';

const { contextBridge, ipcRenderer } = require('electron');

// `maestro` et non `electron` : le nom dit le produit, pas la coque. Le jour où la fenêtre change
// de technologie, la page n'a rien à savoir — c'est la même raison qui fait tester la capacité
// plutôt que la plateforme.
contextBridge.exposeInMainWorld('maestro', {
  /**
   * Ouvre `chemin` dans l'explorateur de fichiers du système. Rend `true` si l'explorateur s'est
   * ouvert, `false` sinon (chemin inconnu, refusé, ou qui n'est pas un dossier).
   *
   * Ne lève jamais : un rejet d'IPC vaut « non », et l'appelant a déjà le chemin sous les yeux.
   */
  ouvrirDossier: (chemin) =>
    ipcRenderer.invoke('maestro:ouvrir-dossier', String(chemin)).catch(() => false),
});
