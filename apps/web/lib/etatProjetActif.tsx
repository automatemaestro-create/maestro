"use client";

/**
 * Le projet actif résolu — le cadre dans lequel toute la Control Tower se lit
 * (#279, lot 3 de #276).
 *
 * Le partage des rôles est celui de `useControlTower` / `etatGlobal` : la
 * mémoire du choix vit dans `lib/projetActif` (un identifiant, un stockage, un
 * événement), et ce module-ci la **confronte à l'état réel**. Un identifiant
 * retenu ne vaut rien tant que la liste servie par l'API ne l'a pas confirmé :
 * un projet supprimé entre deux visites doit ramener au choix **avec son
 * motif**, et non faire échouer l'écran ni ouvrir une Control Tower vide sur un
 * projet fantôme. Le projet actif est donc **dérivé** de la liste, jamais
 * recopié depuis le stockage.
 *
 * Ce module ne décide pas de l'écran : le shell s'en charge (pas de projet
 * actif, pas de Control Tower — `components/Shell`). Le lot 4 (#280) exposera ce
 * même contexte dans un sélecteur et le lot 5 (#281) le consommera écran par
 * écran ; tous deux passent par `useProjetActif`.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { chargerProjets } from "@/lib/api";
import {
  ecouterProjetActif,
  ecrireProjetActifId,
  lireProjetActifId,
} from "@/lib/projetActif";
import type { Projet, RefusProjet } from "@/lib/types";

export type ProjetActif = {
  /** Le projet actif — `null` tant qu'aucun choix confirmé ne tient. */
  projet: Projet | null;
  /** Les projets déclarés, tels que la dernière lecture les a rendus. */
  projets: Projet[];
  /**
   * Vrai dès que la première lecture a tranché. Avant, on ne sait pas encore
   * s'il y a un projet actif : ne rien montrer vaut mieux que faire clignoter
   * la porte d'entrée devant quelqu'un qui a déjà son projet.
   */
  pret: boolean;
  /** Vrai tant qu'une lecture de la liste est en vol (première ou non). */
  chargement: boolean;
  /** L'API n'a pas répondu — ce qui n'est pas la même chose qu'« aucun projet ». */
  erreur: string | null;
  /** Le motif du retour au choix : le projet retenu n'est plus déclaré. */
  perdu: RefusProjet | null;
  /** Entre dans un projet — celui d'une liste, ou celui qu'on vient de créer. */
  choisir: (projet: Projet) => void;
  /** Ressort du projet actif : la porte d'entrée reprend la main. */
  quitter: () => void;
  /** Relit la liste et re-tranche le choix retenu. */
  recharger: () => Promise<void>;
};

const ContexteProjetActif = createContext<ProjetActif | null>(null);

export function FournisseurProjetActif({
  children,
}: {
  children: React.ReactNode;
}) {
  const [projets, setProjets] = useState<Projet[]>([]);
  const [idActif, setIdActif] = useState<string | null>(null);
  const [pret, setPret] = useState(false);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [perdu, setPerdu] = useState<RefusProjet | null>(null);

  const recharger = useCallback(async () => {
    setChargement(true);
    try {
      const liste = await chargerProjets();
      setProjets(liste);
      setErreur(null);
      // C'est la liste qui tranche, jamais le stockage : un identifiant qui ne
      // désigne plus rien est oublié **et dit pourquoi**. Relu ici plutôt que
      // pris dans l'état React : `recharger` doit valoir aussi au tout premier
      // appel, avant que l'identifiant retenu n'y soit passé.
      const retenu = lireProjetActifId();
      if (retenu !== null && !liste.some((projet) => projet.id === retenu)) {
        ecrireProjetActifId(null);
        setPerdu({
          motif: "projet-inconnu",
          message: `Le projet ouvert la dernière fois (${retenu}) n'est plus déclaré.`,
        });
      }
    } catch (e) {
      // Une API muette ne se lit pas « aucun projet » (même règle que la liste
      // de #225) : le choix retenu est **conservé** — il redeviendra valide dès
      // que le backend répondra — mais il ne fait pas entrer, faute de pouvoir
      // nommer le projet sur lequel on entrerait.
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
      setPret(true);
    }
  }, []);

  useEffect(() => {
    // Lu après l'hydratation et différé d'un tick : le rendu serveur ne connaît
    // pas le `localStorage`, le lire pendant le rendu ferait diverger les deux
    // arbres (même mécanique que le repli de la sidebar). L'abonnement suit
    // ensuite les changements venus d'ailleurs — le sélecteur du lot 4, ou un
    // autre onglet.
    const tick = setTimeout(() => {
      setIdActif(lireProjetActifId());
      void recharger();
    }, 0);
    const detacher = ecouterProjetActif(setIdActif);
    return () => {
      clearTimeout(tick);
      detacher();
    };
  }, [recharger]);

  const choisir = useCallback((projet: Projet) => {
    // Un projet tout juste déclaré n'est pas encore dans la liste lue : on l'y
    // ajoute plutôt que de faire dépendre l'entrée d'un rechargement, dont
    // l'échec laisserait l'utilisateur devant la porte qu'il vient d'ouvrir.
    setProjets((liste) =>
      liste.some((connu) => connu.id === projet.id) ? liste : [...liste, projet],
    );
    setPerdu(null);
    // Le stockage tranche : on écrit, l'abonnement ci-dessus met l'état à jour —
    // ici comme depuis le sélecteur du lot 4, un seul chemin de bascule.
    ecrireProjetActifId(projet.id);
  }, []);

  const quitter = useCallback(() => {
    setPerdu(null);
    ecrireProjetActifId(null);
  }, []);

  // Dérivé, et non recopié : l'identifiant mémorisé n'est un projet que si la
  // liste le confirme. Rien d'autre ne peut le rendre valide.
  const projet = projets.find((connu) => connu.id === idActif) ?? null;

  return (
    <ContexteProjetActif
      value={{
        projet,
        projets,
        pret,
        chargement,
        erreur,
        perdu,
        choisir,
        quitter,
        recharger,
      }}
    >
      {children}
    </ContexteProjetActif>
  );
}

/** Le projet actif et ce qui le change. Erreur explicite hors du fournisseur. */
export function useProjetActif(): ProjetActif {
  const etat = useContext(ContexteProjetActif);
  if (etat === null) {
    throw new Error(
      "useProjetActif doit être appelé sous <FournisseurProjetActif> (components/Shell).",
    );
  }
  return etat;
}
