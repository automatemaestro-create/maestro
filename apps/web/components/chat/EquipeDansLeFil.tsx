"use client";

/**
 * **L'équipe proposée dans le fil**, avec le geste qui la valide (#1146, #1227).
 *
 * Un projet sans agent ne peut rien faire d'un run : chaque tâche part en repli
 * « à assigner » après que le cadrage et le plan ont été payés. Quand on demande
 * un travail sur un tel projet, l'orchestration ne propose donc pas de run, elle
 * propose l'**équipe** — et cette carte est l'endroit où on la relit, l'ajuste et
 * la valide, sans quitter la conversation. Validée, l'équipe est créée par la voie
 * de l'étape d'équipe (#1040), puis le fil repropose le travail demandé : c'est
 * la demande de cadrage (#943) qui prend le relais.
 *
 * ## Le second moment : compléter une équipe, pas la créer (#1227)
 *
 * La même carte sert quand un run **suspendu** attend : la décomposition vient
 * d'écrire un plan qui appelle un métier que l'équipe n'a pas, et l'orchestrateur
 * propose de la compléter avant d'exécuter. `demande.run_id` est le témoin, et
 * trois choses en découlent — rien de plus :
 *
 * - le **pourquoi** se lit en tête (`PourquoiCeRole`) : le rôle, la raison (le
 *   *plan*, pas le projet) et les tâches qui l'attendent. Sans elles, on validerait
 *   un recrutement sans savoir ce qu'il change ;
 * - la proposition demandée est celle de **ce rôle-là** (`renfort`), pas l'équipe
 *   entière : une proposition ordinaire l'écarterait, aucun constat d'un projet en
 *   Python ne justifiant un designer ;
 * - le titre et les boutons disent *compléter*, jamais *recruter l'équipe*.
 *
 * ⚠ **Réutilisée, pas doublée** — c'est ce que le ticket demande, et ce n'est pas
 * une économie de code : deux cartes pour un même geste finiraient par ne plus
 * dire la même chose de ce qu'on crée (le défaut G10 du retex du 2026-09-11, celui
 * qui a fait `lib/brief`, `lib/outillage` et `lib/questions`). Ce qui change ici
 * est **ce qu'il y a à montrer**, et cela tient dans un en-tête et un mot.
 *
 * ## Rien d'inventé : deux formes déjà tranchées, composées
 *
 * Ce ticket **applique** deux décisions consignées, et ne décide d'aucune forme :
 *
 * - **la carte qui écrit au pied du fil** était celle de `ConclusionOutillage`
 *   (#1104, variante retenue B, retirée par #1161 avec l'écriture de l'outillage en
 *   une fois ; sa forme tient ici) — le récapitulatif chiffré se lit sans rien
 *   ouvrir, le détail se déplie sur place avec son contrôle **à gauche** (au bord
 *   droit, il passerait sous « ↓ Dernier message », #990), tout arrive retenu et
 *   corriger c'est décocher, le projet visé est nommé, et les deux issues sont
 *   nommées à égalité. Même raison qu'elle pour replier : la carte est montée
 *   telle quelle dans la colonne de 320 px (`GestesDuFil`, #1106), et l'inventaire
 *   complet de l'équipe y chasserait le fil de l'écran ;
 * - **un rôle se lit comme à l'étape d'équipe** (#1040, variante retenue A) : le
 *   détail rend `LigneRole` elle-même — raison et endroit qui la prouve,
 *   instances, skills, autorisations dépliées, playbook replié. Elle vit à côté de
 *   cette carte depuis que l'étape est partie (#1331).
 *
 * ⚠ Le repli ne cache **aucune** décision prise d'avance. Le critère de #1040 —
 * « chaque permission `auto` étant montrée » — écartait justement une variante qui
 * repliait les autorisations : un cran `auto` que personne n'a lu n'est décidé par
 * personne (#716). Les autorisations `auto` des rôles gardés sont donc **nommées
 * dans le récapitulatif**, sur le chemin par défaut ; le détail dit le reste.
 *
 * ## Corriger avec ses mots (#1331)
 *
 * Décocher et régler des instances ne disait pas « ajoute quelqu'un pour la
 * sécurité ». La demande en mots de #1159 (`DemandeSurLEquipe`), qui n'existait
 * qu'à l'étape d'équipe, est donc portée ici — et l'étape est partie, sans écran
 * depuis #1161. Sa **place** sur une carte repliée a été tranchée sur pièces
 * (commentaires « Veille de conception » et « Variante retenue » de #1331) : parmi
 * trois directions rendues sur la vraie stack, le regard neuf a retenu **C — un
 * geste discret de la carte**, « Corriger avec vos mots », qui ouvre la demande
 * sur place, contre le champ posé d'office sur la face (A : à 320 px la carte
 * doublait et chassait le fil) et le champ au pied du détail (B : rien ne disait
 * sur la face qu'on pouvait corriger). D'après *Replit* (« Revise », geste discret
 * de la carte, à côté de « Cancel ») et *VS Code* (« the card's feedback area » :
 * envoyer n'approuve rien). Ce qu'on ne défait pas :
 *
 * - **le geste vit dans la rangée des gestes**, après « Plus tard », avec sa propre
 *   icône : sous la boîte « Voir l'équipe », avec le même chevron, il se lisait
 *   comme un second repli du détail (réserve du regard neuf) — et il reste en place
 *   une fois ouvert, pour **replier** la demande ;
 * - **la correction s'applique à l'équipe montrée** — cases, instances et rôles
 *   déjà ajoutés — et y atterrit (`appliquerCorrection`) : un rôle ajouté arrive
 *   retenu et signalé, le récapitulatif compte ce qui sera créé ;
 * - **rien n'est créé avant la validation** : « Créer l'équipe (N) » reste le seul
 *   geste plein, et il est désarmé tant qu'une correction est en vol ;
 * - **ce qui a été montré et tapé est retenu par demande** (`retenirEquipe`) : la
 *   carte est remontée à chaque navigation, et une correction — un appel modèle et
 *   un playbook par rôle ajouté — ne se perd pas en changeant d'écran.
 *
 * ⚠ La saisie **du fil**, juste dessous, n'est pas la porte de la correction :
 * elle part au juge de l'orchestration, qui ne sait rien de l'équipe montrée. La
 * veille de #1331 l'a écartée par écrit ; la carte ne promet donc que son propre
 * geste.
 *
 * ## Ce que la carte ne décide pas
 *
 * Ni le projet — c'est celui de la **demande** (`DemandeRecrutement.projet_id`),
 * que l'API relit du fil, et pas celui de la fenêtre —, ni l'équipe : elle est
 * proposée par `POST …/equipe/proposition` (#1039), corrigée par `POST
 * …/equipe/correction` (#1159) et repart telle qu'elle a été montrée
 * (`rolesValides`).
 */

import { useEffect, useId, useRef, useState } from "react";

import { IconeAgents, IconeChat, IconeChevronBas } from "@/components/Icones";
import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { DemandeSurLEquipe } from "@/components/chat/DemandeSurLEquipe";
import { LigneRole } from "@/components/chat/LigneRole";
import { Bouton, Carte } from "@/components/Primitives";
import { refusDepuis, RefusMotive } from "@/components/projets/ExplorateurDossiers";
import { corrigerEquipe, proposerEquipe } from "@/lib/api";
import {
  appliquerCorrection,
  autorisationsDecideesDavance,
  composition,
  compteAgents,
  equipeRetenue,
  membresMontres,
  propositionPourDemande,
  retenirEquipe,
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
  // La correction avec ses mots (#1331) : ouverte ou non, ce qui est tapé, la
  // phrase rendue par le modèle, les lignes nées d'une demande, et ce qui a
  // empêché la dernière d'aboutir.
  const [ouverte, setOuverte] = useState(false);
  const [texte, setTexte] = useState("");
  const [reponse, setReponse] = useState<string | null>(null);
  const [ajoutes, setAjoutes] = useState<Set<string>>(new Set());
  const [enCorrection, setEnCorrection] = useState(false);
  const [refusCorrection, setRefusCorrection] = useState<RefusProjet | null>(null);
  // Le champ prend le focus quand **le geste** ouvre la correction — pas quand la
  // carte la rouvre d'elle-même après une navigation, qui volerait le focus de
  // l'écran qu'on vient d'ouvrir.
  const focaliser = useRef(false);

  // Le renfort d'un run (#1227) : un seul rôle, celui que le plan appelle.
  // Dérivé de la demande et non d'un second champ — `gabarit` renseigné est
  // exactement ce que le moteur a calculé, et l'absence de gabarit veut dire
  // qu'aucun rôle du catalogue ne couvrait le manque (le moteur ne propose alors
  // rien, donc cette carte n'est pas montée).
  const renfort =
    demande.gabarit !== undefined && demande.gabarit !== ""
      ? { gabarit: demande.gabarit, raison: demande.raison ?? "" }
      : undefined;

  useEffect(() => {
    // `vivant` plutôt qu'un `AbortController` : ce qu'on protège est l'écriture
    // d'état sur une carte démontée — la demande a pu être tranchée depuis une
    // autre fenêtre pendant que la proposition se composait.
    let vivant = true;
    const partir = async () => {
      try {
        const rendue = await propositionPourDemande(cle, () =>
          proposerEquipe(demande.projet_id, [], renfort),
        );
        if (!vivant) return;
        // Ce que la carte montrait pour cette demande avant d'être démontée —
        // corrections, cases et texte compris (#1331) —, sinon la proposition
        // telle qu'elle arrive : tout retenu.
        const deja = equipeRetenue(cle);
        if (deja !== undefined) {
          setProposition({ ...rendue, roles: deja.roles });
          setRetenus(new Set(deja.retenus));
          setInstances({ ...deja.instances });
          setAjoutes(new Set(deja.ajoutes));
          setReponse(deja.reponse);
          setTexte(deja.texte);
          setOuverte(deja.ouverte);
          return;
        }
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
    //
    // Le **gabarit** y entre depuis #1227, et il le doit : un même projet peut
    // se voir proposer son équipe entière (aucun gabarit) puis, un run plus tard,
    // un renfort (un gabarit). Sans lui, la seconde demande resservirait la
    // proposition de la première — l'équipe complète là où un seul rôle est
    // attendu.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [demande.projet_id, demande.gabarit]);

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
  // Tant qu'une correction est en vol, rien ne se coche ni ne se crée : la
  // validation porterait l'équipe d'avant la demande (parti pris 2 de la veille).
  const fige = enCours || chargement || enCorrection;

  // Chaque changement de ce qui est montré est retenu pour cette demande (#1331) :
  // la prochaine carte montée sur la même demande — un autre écran — le reprend.
  useEffect(() => {
    if (proposition === null) return;
    retenirEquipe(cle, {
      roles: proposition.roles,
      retenus,
      instances,
      ajoutes,
      reponse,
      texte,
      ouverte,
    });
  }, [cle, proposition, retenus, instances, ajoutes, reponse, texte, ouverte]);

  useEffect(() => {
    if (!ouverte || !focaliser.current) return;
    focaliser.current = false;
    document.getElementById(`${idCarte}-demande`)?.focus();
  }, [ouverte, idCarte]);

  // La correction s'applique à **ce que la carte montre** — cases, instances et
  // rôles déjà ajoutés — et y atterrit. Rien n'est créé : la validation reste le
  // seul geste qui écrit. La demande part sans réponses de questionnaire : la
  // carte n'en a pas, l'équipe se dérive de l'analyse du projet de la demande.
  const corriger = async () => {
    if (proposition === null || texte.trim() === "") return;
    setEnCorrection(true);
    setRefusCorrection(null);
    // La réponse affichée est celle de **la** demande en cours : gardée pendant
    // qu'une autre part, elle se lisait comme sa réponse — « J'ai ajouté… » sous
    // « retire… », puis la panne (✗ du regard neuf, relecture de #1331). Ce que
    // la demande précédente a changé, lui, reste sur l'équipe.
    setReponse(null);
    try {
      const montree = { roles: proposition.roles, retenus, instances };
      const correction = await corrigerEquipe(
        demande.projet_id,
        texte,
        membresMontres(montree),
      );
      const apres = appliquerCorrection(montree, correction);
      setProposition({ ...proposition, roles: apres.roles });
      setRetenus(new Set(apres.retenus));
      setInstances({ ...apres.instances });
      setAjoutes((avant) => new Set([...avant, ...apres.ajoutes]));
      setReponse(correction.reponse);
      // Le champ ne se vide que si quelque chose a changé : une demande que le
      // modèle n'a pas comprise reste là, pour qu'on la reformule.
      if (apres.change) setTexte("");
    } catch (erreur) {
      // Un modèle en panne se dit sous le champ ; l'équipe montrée et le texte
      // restent tels quels, et la demande se rejoue d'un clic.
      setRefusCorrection(refusDepuis(erreur));
    } finally {
      setEnCorrection(false);
    }
  };

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
    /* Le nom du projet dans l'`aside` et non dans le titre : `EnTeteSection`
       rend ses titres en capitales, et le nom perdrait sa casse à l'endroit
       même où il sert à reconnaître son projet (constat du regard neuf de
       #1104, repris tel quel). */
    <CarteDuFil
      libelle={renfort === undefined ? "Équipe à valider" : "Renfort à valider"}
      icone={IconeAgents}
      titre={
        renfort === undefined ? "Recruter l'équipe ?" : "Compléter l'équipe ?"
      }
      aside={
        <span className="text-annexe text-texte-secondaire">{nomProjet}</span>
      }
    >

      <PourquoiCeRole demande={demande} />

      {chargement && (
        <p className="text-corps text-texte-secondaire">
          {renfort === undefined
            ? "Composition de l'équipe — la racine est lue, puis un playbook est rédigé par rôle…"
            : "Composition du rôle — la racine est lue, puis son playbook est rédigé pour ce projet…"}
        </p>
      )}

      {refus !== null && (
        <RefusMotive
          refus={refus}
          titre={renfort === undefined ? "Équipe indisponible" : "Rôle indisponible"}
        />
      )}

      {/* Une équipe vide se corrige comme une autre (#1331) : « ajoute un
          développeur » y compose un rôle pour ce projet. La carte renvoyait
          jusque-là créer l'agent à la main, depuis un autre écran. */}
      {proposition !== null && roles.length === 0 && (
        <p className="text-corps text-texte">
          L&apos;analyse de ce projet ne propose aucun rôle. Dites qui il vous faut
          avec « Corriger avec vos mots » : le rôle sera composé pour ce projet,
          playbook compris. Rien n&apos;est créé tant que vous n&apos;avez pas validé.
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
              {/* Le contrôle **à gauche**, comme dans `PieceDOutillage` : au
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
              {/* Le compte passe à la ligne **entier** : à 320 px il se coupait
                  avant son dernier chiffre (« … retenus sur » / « 2 », relevé
                  par le regard neuf à la relecture de #1331). */}
              <span className="text-annexe whitespace-nowrap text-texte-secondaire">
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
                      ajoute={ajoutes.has(role.nom)}
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
                    {/* Le geste, nommé à côté de ce qu'il rattrape (critère de
                        #1159 : aucun texte ne promet un geste que l'écran n'offre
                        pas). La raison servie d'un écarté ne dit que le fait. */}
                    <p className="mt-2">
                      Pour en ajouter un quand même, dites-le avec « Corriger
                      avec vos mots » : il sera composé pour votre projet,
                      playbook compris.
                    </p>
                  </details>
                )}
              </div>
            )}
          </Carte>
        </>
      )}

      {proposition !== null && ouverte && (
        <div id={`${idCarte}-corriger`} className="mt-3">
          <DemandeSurLEquipe
            id={`${idCarte}-demande`}
            demande={texte}
            changer={setTexte}
            envoyer={() => void corriger()}
            enCours={enCorrection}
            fige={fige}
            reponse={reponse}
            refus={refusCorrection}
          />
        </div>
      )}

      {(proposition !== null || refus !== null) && (
        <div className="mt-4 flex flex-wrap gap-2">
          {proposition !== null && roles.length > 0 && (
            <Bouton
              disabled={gardes.length === 0 || fige}
              occupe={enCours}
              onClick={() => void valider()}
            >
              {renfort === undefined
                ? `Créer l'équipe (${total})`
                : `Recruter (${total})`}
            </Bouton>
          )}
          {/* « Plus tard » là où rien n'attend, « Continuer sans » quand un run
              attend : décliner n'y remet rien à plus tard, cela laisse partir
              l'exécution avec l'équipe qu'on a — et c'est ce que le bouton doit
              dire avant qu'on l'appuie, pas la phrase qui suivra dans le fil. */}
          <Bouton
            variante="contour"
            ton="neutre"
            disabled={enCours}
            onClick={() => void plusTard()}
          >
            {renfort === undefined ? "Plus tard" : "Continuer sans"}
          </Bouton>
          {/* Le troisième geste, discret, **après** les deux issues — comme
              « Revise » près de « Cancel » chez Replit : il ne décide de rien,
              il ouvre la demande sur place, et la replie. Offert aussi sur une
              équipe vide : c'est là qu'on a le plus à dire. */}
          {proposition !== null && (
            <Bouton
              variante="discret"
              ton="neutre"
              icone={IconeChat}
              aria-expanded={ouverte}
              aria-controls={`${idCarte}-corriger`}
              disabled={chargement}
              onClick={() => {
                focaliser.current = !ouverte;
                setOuverte((avant) => !avant);
              }}
            >
              {ouverte ? "Replier la correction" : "Corriger avec vos mots"}
            </Bouton>
          )}
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
    </CarteDuFil>
  );
}

/**
 * **Pourquoi ce rôle**, quand un run attend (#1227) — muet le reste du temps.
 *
 * Trois faits, et ils viennent tous de la demande que le moteur a écrite dans le
 * fil : le rôle, la raison (le **plan**, pas le projet) et les tâches qui
 * l'attendent. Rien n'est recomposé ici — la raison affichée est *la* raison
 * calculée (`ManqueAuPlan.raison`), et une seconde formulation côté navigateur
 * serait une phrase à tenir d'accord avec elle.
 *
 * Les tâches sont **nommées** et non comptées : « 3 tâches » ne dit pas si l'on
 * s'apprête à confier une animation à un développeur, et c'est exactement ce que
 * ce recrutement existe pour éviter. La liste est celle du plan, qui tient en
 * quelques lignes (`MAX_TASKS`) — il n'y a rien à replier.
 *
 * Muet sans `run_id` : la proposition d'une équipe entière porte ses raisons
 * rôle par rôle, dans son détail (`LigneRole`), et une raison globale y ferait
 * double emploi.
 */
function PourquoiCeRole({ demande }: { demande: DemandeRecrutement }) {
  if (demande.run_id === undefined || demande.run_id === "") return null;
  const taches = demande.taches ?? [];
  return (
    <div className="mb-3 flex flex-col gap-1">
      <p className="text-corps text-texte">
        <strong>{demande.role || "Un rôle absent de l'équipe"}</strong> —{" "}
        {demande.raison ?? ""}
      </p>
      {taches.length > 0 && (
        <p className="text-annexe text-texte-secondaire">
          <span className="font-medium text-texte">Il prendrait</span> :{" "}
          {taches.map((titre, index) => (
            <span key={titre}>
              {index > 0 && " · "}
              {titre}
            </span>
          ))}
        </p>
      )}
    </div>
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
