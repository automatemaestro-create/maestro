// Le pont de la coque vers la page (#928, lot 7 de #921 — puis #938, lot 8) : trois verbes, pas un
// de plus, et chacun nommé par ce qu'il fait.
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
// ## Ce que #938 y ajoute, et pourquoi le troisième verbe n'a pas d'IPC
//
// Désigner une racine depuis la fenêtre demande deux choses qu'un onglet ne sait pas faire : ouvrir
// le dialogue de dossier de l'OS **sans passer par le backend** (`choisirDossier`), et lire le
// chemin RÉEL d'un dossier qu'on dépose (`cheminDuFichier`).
//
// Le second ne traverse pas l'IPC, et ce n'est pas un raccourci : `webUtils.getPathForFile` est une
// API **du processus de rendu**, disponible dans un preload sandboxé, et c'est le motif officiel
// d'Electron — l'objet `File` ne se sérialise pas vers le processus principal, il faut donc le lire
// ici. La page nous passe le `File` que son `drop` a reçu, et récupère une chaîne.
//
// ⚠ **Ni l'un ni l'autre n'autorise quoi que ce soit.** Le chemin rendu est celui de l'OS, brut :
// c'est `POST /api/projets/racine` qui le confronte aux frontières d'EF-38 (#221). Une validation
// de plus ici ferait deux formules à tenir d'accord, et c'est la garde qui perdrait.
//
// ## Les trois réglages de sûreté ne bougent pas
//
// `contextIsolation: true`, `sandbox: true`, `nodeIntegration: false` (docs/19, et les tests qui
// lisent `main.js` depuis #929). Un preload sandboxé n'a PAS accès à Node : il ne peut que
// `contextBridge`, `ipcRenderer` et `webUtils`, ce qui est exactement la surface qu'on veut. La
// page ne reçoit donc ni `fs`, ni `shell`, ni `ipcRenderer` — seulement trois fonctions qui
// prennent une chaîne (ou un `File`) et rendent une chaîne ou un booléen. C'est le processus
// principal qui décide ensuite, et qui refuse (`main.js`).
//
// ⚠ NE PAS exposer `ipcRenderer` ni un verbe générique du type `invoke(canal, …)` : ce serait
// rouvrir tout le pont derrière un nom neutre, et la page est servie par un serveur local qu'un
// autre programme du poste peut atteindre.

'use strict';

const { contextBridge, ipcRenderer, webUtils } = require('electron');

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

  /**
   * Ouvre le dialogue de dossier de l'OS et rend le chemin choisi, `null` si la personne a annulé
   * (#938). `depart` est le dossier d'ouverture, facultatif.
   *
   * Ne lève jamais : un rejet d'IPC vaut « annulé », qui est le cas nominal et n'affiche rien.
   */
  choisirDossier: (depart) =>
    ipcRenderer
      .invoke('maestro:choisir-dossier', typeof depart === 'string' ? depart : null)
      .catch(() => null),

  /**
   * Le chemin réel du `File` qu'on vient de déposer, `null` si l'objet n'en a pas (#938).
   *
   * Un navigateur ne livre jamais ce chemin — c'est toute la raison d'être de l'explorateur servi
   * par l'API (#223). Ici, `webUtils` l'a. Rend `null` plutôt que la chaîne vide qu'Electron rend
   * pour ce qui ne vient pas du disque (un fichier glissé depuis une autre page, par exemple) :
   * l'appelant n'a alors qu'un seul cas à traiter.
   */
  cheminDuFichier: (fichier) => {
    try {
      return webUtils.getPathForFile(fichier) || null;
    } catch {
      return null;
    }
  },
});
