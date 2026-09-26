"use client";

/**
 * La page Projets de la Control Tower (#225, docs/05 §2.7) : déclarer et gérer
 * les projets de l'utilisateur — la racine sur le disque où Maestro travaille.
 *
 * Une coquille, comme la page Paramètres : le contenu vit dans
 * `components/projets/`, parce que c'est lui qui se teste. La page ne porte ni
 * titre ni en-tête — la barre supérieure les dérive du menu (#117), qui la
 * connaît toujours bien qu'elle en soit sortie (`HORS_MENU`, #280).
 *
 * Depuis #280 on y arrive par le **sélecteur du shell** et non plus par la barre
 * latérale, ce qui change une chose ici : la Control Tower est ouverte sur un
 * projet **pendant** qu'on gère la liste. La coquille branche donc les écritures
 * de l'écran sur la relecture du projet actif — supprimer la racine courante
 * ramène ainsi à la porte d'entrée (#279) au lieu de laisser un cadre vide.
 *
 * Et depuis #1294, « Nouveau projet » **quitte** le projet ouvert : un projet
 * naît dans la conversation, sur la porte d'entrée, hors du cadre d'un autre
 * (docs/43 §2.2). La demande de création voyage jusqu'à la porte par la mémoire
 * de session (`demanderNaissance`).
 */

import { ListeProjets } from "@/components/projets/ListeProjets";
import { useProjetActif } from "@/lib/etatProjetActif";
import { demanderNaissance } from "@/lib/naissance";

export default function PageProjets() {
  const { recharger, quitter } = useProjetActif();
  return (
    <ListeProjets
      apresEcriture={() => void recharger()}
      nouveauProjet={() => {
        demanderNaissance();
        quitter();
      }}
    />
  );
}
