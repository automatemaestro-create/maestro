"use client";

/**
 * **L'équipe proposée dans le fil**, avec le geste qui la valide (#1146).
 *
 * Un projet sans agent ne peut rien faire d'un run : chaque tâche part en repli
 * « à assigner » après que le cadrage et le plan ont été payés. Quand on demande
 * un travail sur un tel projet, l'orchestration ne propose donc pas de run, elle
 * propose l'**équipe** — et cette carte est l'endroit où on la relit, l'ajuste et
 * la valide, sans quitter la conversation. Validée, l'équipe est créée par la voie
 * de l'étape d'équipe (#1040), puis le fil repropose le travail demandé : c'est
 * la demande de cadrage (#943) qui prend le relais.
 *
 * ## Rien d'inventé : deux formes déjà tranchées, composées
 *
 * Ce ticket **applique** deux décisions consignées, et ne décide d'aucune forme :
 *
 * - **la carte qui écrit au pied du fil** est celle de `ConclusionOutillage`
 *   (#1104, variante retenue B) — le récapitulatif chiffré se lit sans rien
 *   ouvrir, le détail se déplie sur place avec son contrôle **à gauche** (au bord
 *   droit, il passerait sous « ↓ Dernier message », #990), tout arrive retenu et
 *   corriger c'est décocher, le projet visé est nommé, et les deux issues sont
 *   nommées à égalité. Même raison qu'elle pour replier : la carte est montée
 *   telle quelle dans la colonne de 320 px (`GestesDuFil`, #1106), et l'inventaire
 *   complet de l'équipe y chasserait le fil de l'écran ;
 * - **un rôle se lit comme à l'étape d'équipe** (#1040, variante retenue A) : le
 *   détail rend `LigneRole` elle-même — raison et endroit qui la prouve,
 *   instances, skills, autorisations dépliées, playbook replié.
 *
 * ⚠ Le repli ne cache **aucune** décision prise d'avance. Le critère de #1040 —
 * « chaque permission `auto` étant montrée » — écartait justement une variante qui
 * repliait les autorisations : un cran `auto` que personne n'a lu n'est décidé par
 * personne (#716). Les autorisations `auto` des rôles gardés sont donc **nommées
 * dans le récapitulatif**, sur le chemin par défaut ; le détail dit le reste.
 *
 * ## Ce que la carte ne décide pas
 *
 * Ni le projet — c'est celui de la **demande** (`DemandeRecrutement.projet_id`),
 * que l'API relit du fil, et pas celui de la fenêtre —, ni l'équipe : elle est
 * proposée par `POST …/equipe/proposition` (#1039) et repart telle qu'elle a été
 * montrée (`rolesValides`, partagé avec l'étape d'équipe).
 */

import { useEffect, useId, useState } from "react";

import { IconeAgents, IconeChevronBas } from "@/components/Icones";
import { Bouton, Carte, EnTeteSection } from "@/components/Primitives";
import { refusDepuis, RefusMotive } from "@/components/projets/ExplorateurDossiers";
import { LigneRole } from "@/components/projets/EtapeEquipe";
import { proposerEquipe } from "@/lib/api";
import {
  autorisationsDecideesDavance,
  composition,
  compteAgents,
  propositionPourDemande,
  rolesValides,
} from "@/lib/equipe";
import { useEtatGlobal } from "@/lib/etatGlobal";
import type {
  DemandeRecrutement,
  PropositionEquipe,
  RefusProjet,
  RoleValideEquipe,
} from "@/lib/types";

export function EquipeDansLeFil({
  demande,
  cle,
  recruter,
  enCours,
}: {
  demande: DemandeRecrutement;
  /**
   * Ce qui identifie la demande — son message. La proposition est retenue sous
   * cette clé (`propositionPourDemande`) : la carte est montée sur chaque écran,
   * et la redemander à chaque navigation paierait une analyse et un appel modèle
   * par rôle.
   */
  cle: string;
  /** Le geste du fil (`useChat.recruter`) : valider l'équipe gardée, ou décliner. */
  recruter: (
    approuve: boolean,
    roles?: RoleValideEquipe[],
    propositionId?: string,
  ) => Promise<void>;
  /** Un échange est en vol sur ce fil : les gestes se désarment. */
  enCours: boolean;
}) {
  const { projet } = useEtatGlobal();
  const idCarte = useId();
  const [proposition, setProposition] = useState<PropositionEquipe | null>(null);
  const [retenus, setRetenus] = useState<Set<string>>(new Set());
  const [instances, setInstances] = useState<Record<string, number>>({});
  const [chargement, setChargement] = useState(true);
  const [refus, setRefus] = useState<RefusProjet | null>(null);
  const [deplie, setDeplie] = useState(false);
  const [echec, setEchec] = useState<string | null>(null);

  useEffect(() => {
    // `vivant` plutôt qu'un `AbortController`, comme l'étape d'équipe : ce qu'on
    // protège est l'écriture d'état sur une carte démontée — la demande a pu être
    // tranchée depuis une autre fenêtre pendant que la proposition se composait.
    let vivant = true;
    const partir = async () => {
      try {
        const rendue = await propositionPourDemande(cle, () =>
          proposerEquipe(demande.projet_id),
        );
        if (!vivant) return;
        setProposition(rendue);
        setRetenus(new Set(rendue.roles.map((r) => r.nom)));
        setInstances(
          Object.fromEntries(rendue.roles.map((r) => [r.nom, r.instances])),
        );
      } catch (erreur) {
        if (vivant) setRefus(refusDepuis(erreur));
      } finally {
        if (vivant) setChargement(false);
      }
    };
    void partir();
    return () => {
      vivant = false;
    };
    // La clé n'entre pas en dépendance : une demande **reposée** après un refus
    // de création (même projet, nouveau message) garde la proposition déjà
    // composée et ce qu'on y a ajusté. Ce qui relance la proposition est le
    // projet — et, au montage suivant, une clé que la mémoire ne connaît pas.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demande.projet_id]);

  // Le projet **de la demande**, nommé dans sa casse. Le fil ne connaît que celui
  // de la fenêtre ; quand ce n'est pas le même, on nomme l'identifiant plutôt que
  // de prêter à l'équipe un projet qui n'est pas le sien.
  const nomProjet =
    projet.id === demande.projet_id ? projet.nom : demande.projet_id;

  const roles = proposition?.roles ?? [];
  const gardes = roles.filter((r) => retenus.has(r.nom));
  const total = gardes.reduce(
    (somme, r) => somme + (instances[r.nom] ?? r.instances),
    0,
  );
  const fige = enCours || chargement;

  const basculer = (nom: string) =>
    setRetenus((avant) => {
      const apres = new Set(avant);
      if (apres.has(nom)) apres.delete(nom);
      else apres.add(nom);
      return apres;
    });

  const valider = async () => {
    if (proposition === null) return;
    setEchec(null);
    try {
      await recruter(
        true,
        rolesValides(proposition, retenus, instances),
        proposition.id,
      );
    } catch (e: unknown) {
      setEchec(e instanceof Error ? e.message : String(e));
    }
  };

  const plusTard = async () => {
    setEchec(null);
    try {
      await recruter(false);
    } catch (e: unknown) {
      setEchec(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <Carte
      balise="section"
      ton="attention"
      densite="aeree"
      aria-label="Équipe à valider"
    >
      {/* Le nom du projet dans l'`aside` et non dans le titre : `EnTeteSection`
          rend ses titres en capitales, et le nom perdrait sa casse à l'endroit
          même où il sert à reconnaître son projet (constat du regard neuf de
          #1104, repris tel quel). */}
      <EnTeteSection
        niveau={3}
        icone={IconeAgents}
        titre="Recruter l'équipe ?"
        ton="attention"
        className="mb-3"
        aside={
          <span className="text-annexe text-texte-secondaire">{nomProjet}</span>
        }
      />

      {chargement && (
        <p className="text-corps text-texte-secondaire">
          Composition de l&apos;équipe — la racine est lue, puis un playbook est
          rédigé par rôle…
        </p>
      )}

      {refus !== null && (
        <RefusMotive refus={refus} titre="Équipe indisponible" />
      )}

      {proposition !== null && roles.length === 0 && (
        <p className="text-corps text-texte">
          L&apos;analyse de ce projet ne propose aucun rôle. Créez un agent depuis
          les écrans d&apos;agents du projet, puis redites votre demande.
        </p>
      )}

      {proposition !== null && roles.length > 0 && (
        <>
          <CompteACreer
            gardes={gardes.length}
            total={total}
            composition={composition(gardes, instances)}
            nomProjet={nomProjet}
          />
          <AutorisationsAuto roles={gardes} />
          <Carte
            balise="div"
            ton="attentionClaire"
            densite="compacte"
            className="mt-3"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {/* Le contrôle **à gauche**, comme dans `ConclusionOutillage` : au
                  bord droit il passerait sous le bouton flottant du fil (#990). */}
              <Bouton
                variante="discret"
                ton="neutre"
                taille="petite"
                icone={IconeChevronBas}
                aria-expanded={deplie}
                aria-controls={`${idCarte}-detail`}
                onClick={() => setDeplie((ouvert) => !ouvert)}
              >
                Voir l&apos;équipe
              </Bouton>
              <span className="min-w-0 flex-1 text-annexe text-texte-secondaire">
                {gardes.length} rôle{gardes.length > 1 ? "s" : ""} retenu
                {gardes.length > 1 ? "s" : ""} sur {roles.length}
              </span>
            </div>
            {deplie && (
              <div id={`${idCarte}-detail`} className="mt-3 flex flex-col gap-2">
                <ul className="flex flex-col gap-2">
                  {roles.map((role) => (
                    <LigneRole
                      key={role.nom}
                      role={role}
                      retenu={retenus.has(role.nom)}
                      basculer={() => basculer(role.nom)}
                      instances={instances[role.nom] ?? role.instances}
                      changerInstances={(valeur) =>
                        setInstances((avant) => ({ ...avant, [role.nom]: valeur }))
                      }
                      fige={fige}
                      prefixe={`fil-equipe${idCarte}`}
                    />
                  ))}
                </ul>
                {proposition.ecartes.length > 0 && (
                  <details className="text-annexe text-texte-secondaire">
                    <summary className="min-h-6 cursor-pointer">
                      {proposition.ecartes.length} rôle
                      {proposition.ecartes.length > 1 ? "s" : ""} écarté
                      {proposition.ecartes.length > 1 ? "s" : ""}, et pourquoi
                    </summary>
                    <ul className="mt-2 flex flex-col gap-1">
                      {proposition.ecartes.map((ecarte) => (
                        <li key={ecarte.nom}>
                          <span className="font-medium text-texte">
                            {ecarte.role}
                          </span>{" "}
                          — {ecarte.raison}
                        </li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}
          </Carte>
        </>
      )}

      {(proposition !== null || refus !== null) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {proposition !== null && roles.length > 0 && (
            <Bouton
              disabled={gardes.length === 0 || fige}
              occupe={enCours}
              onClick={() => void valider()}
            >
              Créer l&apos;équipe ({total})
            </Bouton>
          )}
          <Bouton
            variante="contour"
            ton="neutre"
            disabled={enCours}
            onClick={() => void plusTard()}
          >
            Plus tard
          </Bouton>
        </div>
      )}
      {proposition !== null && roles.length > 0 && gardes.length === 0 && (
        <p className="mt-2 text-annexe text-attention-texte">
          Tout a été retiré — il n&apos;y a personne à créer. Reprenez un rôle, ou
          remettez à plus tard.
        </p>
      )}
      {echec !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {echec}
        </p>
      )}
    </Carte>
  );
}

/**
 * Le compte, et **où** — ce qu'un coup d'œil doit lire avant le geste.
 *
 * La promesse « rien n'est créé tant que vous n'avez pas validé » vient avant le
 * geste et non après, comme à l'étape d'équipe : c'est ce qu'on veut savoir au
 * moment de laisser un outil recruter pour soi.
 */
function CompteACreer({
  gardes,
  total,
  composition,
  nomProjet,
}: {
  gardes: number;
  total: number;
  composition: string;
  nomProjet: string;
}) {
  if (gardes === 0) {
    return (
      <p className="text-corps text-attention-texte">
        Aucun agent ne sera créé — tout a été retiré.
      </p>
    );
  }
  return (
    <p className="text-corps text-texte">
      <strong>{compteAgents(total)}</strong>{" "}
      {total > 1 ? "seront créés" : "sera créé"} dans {nomProjet} :{" "}
      {composition}. Rien n&apos;est créé tant que vous n&apos;avez pas validé.
    </p>
  );
}

/**
 * Les autorisations **décidées d'avance** par la validation, nommées sans rien
 * ouvrir — le critère de #1040 tenu malgré le repli (voir l'en-tête).
 *
 * Muette quand il n'y en a aucune : chaque appel sera alors arbitré, et c'est le
 * régime par défaut, que le détail dit rôle par rôle.
 */
function AutorisationsAuto({
  roles,
}: {
  roles: Parameters<typeof autorisationsDecideesDavance>[0];
}) {
  const autos = autorisationsDecideesDavance(roles);
  if (autos.length === 0) return null;
  return (
    <p className="mt-2 text-annexe text-texte-secondaire">
      <span className="font-medium text-texte">Décidé d&apos;avance par vous</span>{" "}
      — l&apos;appel passe sans arbitrage, et il est tracé :{" "}
      {autos.map(({ role, autorisation }, index) => (
        <span key={`${role}-${autorisation.outil}`}>
          {index > 0 && " · "}
          <code className="font-mono break-all text-texte">
            {autorisation.outil}
          </code>{" "}
          ({role})
        </span>
      ))}
      .
    </p>
  );
}
