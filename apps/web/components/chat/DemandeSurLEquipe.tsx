"use client";

/**
 * **Ce qui manque à l'équipe proposée, dit avec ses mots** (#1159, #1331) — une
 * question, un champ, un bouton, et la phrase que le modèle rend.
 *
 * Née à l'étape d'équipe (#1159), portée dans la carte d'équipe du fil par #1331 :
 * l'étape n'était plus montée depuis #1161, et la correction n'avait plus d'écran.
 * Ce qu'on y dit est compris par `POST …/equipe/correction` sur l'équipe **telle
 * que la carte la montre**, puis appliqué par `lib/equipe.appliquerCorrection` —
 * rien n'est créé avant la validation.
 *
 * ## La forme vient de deux choix consignés
 *
 * **Sur #1159** (veille *CrewAI Crew Studio* et *Cursor Plan Mode*, variante A) :
 *
 * - **un seul champ, dans la carte, contre l'équipe qu'il corrige** : ni colonne,
 *   ni fil à part ;
 * - **la réponse de Maestro se lit à part des boutons**, sur une surface en
 *   retrait : c'est ce que Maestro a fait de la demande, y compris quand il n'a pas
 *   compris — le champ garde alors le texte, pour qu'on reformule ;
 * - **le bouton est secondaire** (`contour`) et dit l'acte : le seul geste qui
 *   crée reste celui de la carte, « Créer l'équipe (N) ».
 *
 * **Sur #1331** (veille *Replit Agent*, *VS Code* « Review Plan », *Cursor*,
 * variante C retenue par le regard neuf) : sur la carte du fil, cette demande ne
 * s'ouvre qu'**à la demande**, par le geste discret « Corriger avec vos mots » de
 * la rangée des gestes (`EquipeDansLeFil`) — la carte est montée dans la colonne
 * de 320 px, et un formulaire posé d'office sur sa face y chassait le fil. Et
 * l'exemple est une **aide** sous le champ, pas un texte indicatif : à 320 px, le
 * texte indicatif était coupé à « ajoute quelqu'un pou » (réserve du regard neuf).
 */

import { Bouton, Carte, Champ } from "@/components/Primitives";
import { RefusMotive } from "@/components/projets/ExplorateurDossiers";
import type { RefusProjet } from "@/lib/types";

export function DemandeSurLEquipe({
  id,
  demande,
  changer,
  envoyer,
  enCours,
  fige,
  reponse,
  refus,
}: {
  /** L'identifiant du champ — la carte peut être à l'écran deux fois (page et colonne). */
  id: string;
  demande: string;
  changer: (valeur: string) => void;
  envoyer: () => void;
  /** Une correction est en vol : le bouton tourne. */
  enCours: boolean;
  /** Rien ne se saisit ni ne part (chargement, création, correction en vol). */
  fige: boolean;
  /** La phrase que le modèle a rendue à la dernière demande, `null` avant. */
  reponse: string | null;
  /** Ce qui a empêché la dernière demande d'aboutir (modèle en panne, refus motivé). */
  refus: RefusProjet | null;
}) {
  return (
    <form
      aria-label="Corriger l'équipe avec vos mots"
      className="flex flex-col gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        envoyer();
      }}
    >
      <Champ
        id={id}
        libelle={
          <>
            {/* La question pèse plus que le reste de la carte en petit (réserve
                du regard neuf de #1159) : tokens du socle, pas de style à part. */}
            <span className="text-corps text-texte">
              Il manque quelqu&apos;un, ou un rôle ne convient pas ?
            </span>{" "}
            Dites-le avec vos mots.
          </>
        }
        aide="Par exemple : « ajoute quelqu'un pour la sécurité », « retire le designer », « deux développeurs »."
        value={demande}
        onChange={(e) => changer(e.target.value)}
        disabled={fige}
        maxLength={500}
        className="min-w-0"
      />
      {/* Le bouton **à gauche**, sous le champ : au bord droit d'une carte du fil,
          il passerait sous « ↓ Dernier message » (#990). */}
      <div>
        <Bouton
          type="submit"
          variante="contour"
          ton="neutre"
          occupe={enCours}
          disabled={fige || demande.trim() === ""}
        >
          {enCours ? "Composition…" : "Ajouter ou corriger"}
        </Bouton>
      </div>
      {/* Une surface en retrait, et non du texte courant : la phrase se lit
          comme la réponse à la demande, jamais comme une légende de la rangée
          de boutons qui suit (réserve du regard neuf de #1159). */}
      {reponse !== null && (
        <Carte
          balise="p"
          densite="compacte"
          ton="creuse"
          role="status"
          className="text-annexe text-texte"
        >
          <span className="font-medium">Maestro :</span> {reponse}
        </Carte>
      )}
      {refus && <RefusMotive refus={refus} titre="Demande non traitée" />}
    </form>
  );
}
