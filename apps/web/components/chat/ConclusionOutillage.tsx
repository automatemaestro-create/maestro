"use client";

/**
 * La **conclusion** du questionnaire d'outillage, avec le geste qui écrit (#1104).
 *
 * Le questionnaire d'un projet neuf se répond dans le fil (#1031) et s'y conclut :
 * « L'outillage recommandé : N entrée(s), dont N skill(s)… Rien n'est écrit dans le
 * projet tant que vous ne l'avez pas validé. » Jusqu'à ce lot, **rien ne validait** :
 * `questionEnAttente` rend `null` dès que le dernier message ne porte plus de
 * question, donc le pied du fil redevenait vide et la phrase promettait un geste qui
 * n'existait nulle part. C'est la réserve R7 du bouclage de « L'équipe sur mesure ».
 *
 * Les réponses vont désormais jusqu'à la génération **par le même corps que l'étape
 * de création** — `{retenus, choix}` sur `POST …/outillage/generation` (#1033, #1100) :
 * une seule route écrit l'outillage d'un projet, et elle ne sait pas de quelle
 * surface viennent les réponses. Depuis #1147, `choix` porte aussi **ce que Maestro a
 * compris** (la compréhension de la conclusion) : c'est elle qui fait l'outillage, et
 * aucune des deux routes ne rappelle le modèle.
 *
 * ## La forme vient d'une veille et d'un choix consigné
 *
 * Commentaires « Veille de conception » et « Variante retenue » de #1104. La retenue
 * est **B — le récapitulatif chiffré, le détail replié, deux issues nommées**. Ce
 * qu'elle tranche, et qu'on ne défait pas sans rejouer le même geste :
 *
 * - **le fil porte un récapitulatif, jamais l'inventaire d'office** — d'après *VS
 *   Code* (mode agent : « a summary after each completed request », dépliable, la
 *   revue fichier par fichier vivant dans un panneau séparé). C'est ce que le moteur
 *   avait déjà décidé — « le redire ici en entier ferait du fil un second écran de
 *   recommandation » (`_phrase_de_conclusion`, #1031) — et ce que la colonne de
 *   320 px impose : `GestesDuFil` monte ses cartes **telles quelles** sur les deux
 *   surfaces (#1106), et huit lignes de trois pas y chassent le fil de l'écran ;
 * - **le détail se déplie sur place**, dans une `Carte ton="attentionClaire"` dont le
 *   contrôle est **à gauche** — repris de `DemandeDeCadrage` sans être redessiné : au
 *   bord droit, il tombe sous le bouton flottant « ↓ Dernier message » du fil
 *   (correction du regard neuf de #990) ;
 * - **tout arrive retenu, corriger c'est décocher** (`retenueParDefaut`, partagé avec
 *   l'étape de création) — d'après *Vercel*, qui « sets the best settings for you » et
 *   range l'écart derrière un contrôle ;
 * - **les deux issues sont nommées à égalité** — d'après *Renovate*, dont la PR
 *   d'onboarding écrit les deux dans le même paragraphe (« To activate […] merge. To
 *   disable […] close this Pull Request unmerged ») : écrire, ou « Plus tard », qui
 *   passe par le **même verbe** que l'étape de création (`reporterOutillage`), pour
 *   qu'un report donné dans le fil se lise sur la carte du projet ;
 * - **ce sur quoi on va écrire est nommé avant le geste** — d'après *Renovate* encore
 *   (« Detected files », « Configuration Summary » avant l'invitation à fusionner).
 *
 * ⚠ **Le projet visé est celui de la fenêtre**, et c'est pourquoi la carte le nomme.
 * Le fil est transverse (#281) : ses messages ne portent aucun projet, et le
 * questionnaire lui-même n'en connaît pas (`ConducteurOutillage` n'a aucun attribut,
 * #1031). Le nommer est la seule chose qui empêche d'écrire dans un dossier qu'on
 * n'avait pas en tête — ce n'est pas une décoration, c'est la garde.
 *
 * Le **résumé des réponses**, lui, n'est pas recopié ici : il est dans le message de
 * conclusion, juste au-dessus. La carte dit ce que ce message ne peut pas dire — quel
 * projet, quel dossier, combien de fichiers, et lesquels.
 */

import { useEffect, useId, useMemo, useState, type ReactNode } from "react";

import { CarteDuFil } from "@/components/chat/CarteDuFil";
import { IconeChevronBas, IconeDossier } from "@/components/Icones";
import { BadgeEtat, Bouton, Carte } from "@/components/Primitives";
import {
  ListeVerifications,
  RecapitulatifVerifications,
} from "@/components/projets/VerificationsOutillage";
import {
  genererOutillage,
  recommandationOutillage,
  reporterOutillage,
} from "@/lib/api";
import { useEtatGlobal } from "@/lib/etatGlobal";
import {
  choixAValider,
  comptesParNature,
  libelleNature,
  retenueParDefaut,
} from "@/lib/outillage";
import type {
  ChoixOutillage,
  EntreeOutillage,
  Projet,
  RapportGenerationOutillage,
  RecommandationOutillage,
} from "@/lib/types";
import type { Chat } from "@/lib/useChat";

/** Ce que la conclusion a sous la main : les réponses, et ce qu'elles recommandent. */
type MatiereOutillage = {
  projetId: string;
  choix: ChoixOutillage[];
  recommandation: RecommandationOutillage;
};

/**
 * La conclusion du questionnaire d'outillage prête à passer en `pied` de
 * `Conversation` — ou `undefined` quand il n'y a rien à valider.
 *
 * Un **hook**, et non un composant, pour la raison qui vaut déjà dans
 * `GestesDuFil` : `pied` absent ne rend rien du tout, là où un composant qui
 * rendrait `null` laisserait sa boîte et son `mt-3` sous le dernier message.
 * C'est aussi ce qui permet de ne rien afficher **tant que la matière n'est pas
 * là** — la carte n'apparaît qu'une fois su ce qu'elle a à proposer, au lieu de
 * clignoter en « chargement… » sous chaque conclusion.
 *
 * `actif` dit si le fil affiché est celui de l'orchestration : elle seule mène un
 * questionnaire d'outillage, donc un aparté `@agent` n'en conclut jamais.
 */
export function useConclusionOutillage(
  fil: Chat,
  actif: boolean,
): ReactNode | undefined {
  const { projet } = useEtatGlobal();
  const idCarte = useId();

  // Les réponses que le fil porte alors que plus aucune question n'attend. La
  // règle est appelée, jamais recopiée (`lib/outillage`) : deux formulations de
  // « où en est ce questionnaire ? » finiraient par ne plus désigner le même
  // moment.
  const choix = useMemo(
    () => (actif ? choixAValider(fil.messages) : null),
    [actif, fil.messages],
  );

  // Un projet dont l'outillage est **déjà écrit** n'a rien à valider : la carte
  // se retire, et c'est la fiche du projet qui fait foi — pas un souvenir de
  // session. Un projet servi avant que la fiche ne porte l'outillage (#1034) est
  // traité comme non généré : proposer est récupérable, taire ne l'est pas.
  const dejaOutille = projet.outillage?.genere === true;

  const [matiere, setMatiere] = useState<MatiereOutillage | null>(null);
  const [retenus, setRetenus] = useState<Set<string>>(new Set());
  const [deplie, setDeplie] = useState(false);
  const [enCours, setEnCours] = useState(false);
  const [rapport, setRapport] = useState<RapportGenerationOutillage | null>(
    null,
  );
  const [reporte, setReporte] = useState(false);
  const [refus, setRefus] = useState<string | null>(null);

  useEffect(() => {
    if (choix === null || dejaOutille) return;
    // `vivant` plutôt qu'un `AbortController`, comme l'étape d'outillage : ce
    // qu'on protège n'est pas la requête (la laisser finir ne coûte rien) mais
    // l'écriture d'état sur un composant démonté — on bascule de fil d'un clic.
    let vivant = true;
    const partir = async () => {
      try {
        // La fin du questionnaire se **lit** sur le fil depuis #1147 : la
        // conclusion porte ce que Maestro a compris (`choixAValider`). Un
        // questionnaire interrompu (502 après le geste) n'a pas de conclusion,
        // donc rien n'arrive ici. On ne redemande pas au moteur s'il a fini :
        // il comprendrait une seconde fois, et pourrait comprendre autre chose.
        // Ce qui part est ce qui a été compris, et la recommandation n'appelle
        // pas le modèle : ce qu'on écrira est ce qu'on montre.
        const reco = await recommandationOutillage(projet.id, choix);
        if (!vivant) return;
        setMatiere({
          projetId: projet.id,
          choix,
          recommandation: reco.recommandation,
        });
        setRetenus(
          new Set(
            reco.recommandation.entrees
              .filter(retenueParDefaut)
              .map((e) => e.chemin),
          ),
        );
        setRapport(null);
        setReporte(false);
        setRefus(null);
      } catch {
        // Le fil n'est pas l'écran des projets : une recommandation qu'on n'a
        // pas pu servir ne doit pas poser une carte d'erreur sous la
        // conversation. On n'offre simplement pas le geste — la phrase de
        // conclusion, elle, reste vraie.
      }
    };
    void partir();
    return () => {
      vivant = false;
    };
  }, [choix, dejaOutille, projet.id]);

  // La matière **encore valable** : celle servie pour ces réponses-là et ce
  // projet-là. Dérivée au rendu plutôt que remise à `null` dans l'effet : une
  // écriture d'état synchrone dans un effet déclenche un rendu en cascade, et
  // c'est ce que le lint refuse à raison. Ce qui est périmé est simplement
  // ignoré — l'effet le remplacera, ou personne ne montrera rien.
  const courante =
    matiere !== null && matiere.choix === choix && matiere.projetId === projet.id
      ? matiere
      : null;

  if (courante === null || dejaOutille) return undefined;

  const basculer = (chemin: string) =>
    setRetenus((avant) => {
      const apres = new Set(avant);
      if (apres.has(chemin)) apres.delete(chemin);
      else apres.add(chemin);
      return apres;
    });

  const ecrire = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      // Le **même corps** que l'étape de création : les chemins retenus, et les
      // réponses d'où la liste a été dérivée (#1100). Sans elles le serveur
      // rederiverait l'outillage de l'analyse d'une racine vide, et les skills
      // qu'on vient de lire ne seraient pas écrits.
      setRapport(
        await genererOutillage(courante.projetId, [...retenus], courante.choix),
      );
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  const plusTard = async () => {
    setEnCours(true);
    setRefus(null);
    try {
      await reporterOutillage(courante.projetId);
      setReporte(true);
    } catch (e: unknown) {
      setRefus(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  // Un échange est déjà en vol sur ce fil : les gestes se désarment, comme sur
  // les trois autres cartes du pied.
  const fige = enCours || fil.envoi;
  const gardes = courante.recommandation.entrees.filter((e) =>
    retenus.has(e.chemin),
  );

  return (
    /* ⚠ Le nom du projet n'est **pas** dans le titre, et c'est un constat du
       regard neuf : `EnTeteSection` rend ses titres en capitales, si bien que
       « mini-CRM (démo) » s'y affichait « MINI-CRM (DÉMO) » — le nom perdait sa
       casse à l'endroit même où il sert à vérifier dans quel dossier on écrit,
       et ne s'accordait plus avec le sélecteur de projet du bandeau ni avec la
       liste des projets. Il va donc dans l'`aside`, qui ne transforme rien, et
       se redit dans la phrase de compte. */
    <CarteDuFil
      libelle="Outillage à écrire"
      icone={IconeDossier}
      titre="Écrire l'outillage ?"
      aside={
        <span className="text-annexe text-texte-secondaire">{projet.nom}</span>
      }
    >
      {rapport !== null ? (
        <RapportCourt rapport={rapport} />
      ) : reporte ? (
        <p className="text-corps text-texte">
          Noté — rien n&apos;a été écrit. L&apos;outillage de{" "}
          <strong>{projet.nom}</strong> vous sera rappelé sur sa fiche.
        </p>
      ) : (
        <>
          <CompteAEcrire gardes={gardes} projet={projet} />
          <Carte
            balise="div"
            ton="attentionClaire"
            densite="compacte"
            className="mt-3"
          >
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {/* Le contrôle **à gauche** : au bord droit il passe sous le
                  bouton flottant « ↓ Dernier message » du fil (#990).
                  `IconeChevronBas` est la seule du jeu qui dise « ceci
                  s'ouvre » ; l'état, lui, tient à `aria-expanded` et aux
                  lignes qui apparaissent, jamais à l'icône. */}
              <Bouton
                variante="discret"
                ton="neutre"
                taille="petite"
                icone={IconeChevronBas}
                aria-expanded={deplie}
                aria-controls={`${idCarte}-detail`}
                onClick={() => setDeplie((ouvert) => !ouvert)}
              >
                Ce qui sera écrit
              </Bouton>
              {/* Le récapitulatif se lit **sans rien ouvrir** — c'est ce qui
                  permet à la carte de tenir dans une colonne de 320 px sans
                  rien retirer de ce qu'on peut décider. */}
              <span className="min-w-0 flex-1 text-annexe text-texte-secondaire">
                {gardes.length} retenu{gardes.length > 1 ? "s" : ""} sur{" "}
                {courante.recommandation.entrees.length}
              </span>
            </div>
            {deplie && (
              <ul
                id={`${idCarte}-detail`}
                className="mt-3 flex flex-col gap-1.5"
              >
                {courante.recommandation.entrees.map((entree) => (
                  <LigneRetenue
                    key={entree.chemin}
                    entree={entree}
                    idCase={`${idCarte}-${entree.chemin}`}
                    retenue={retenus.has(entree.chemin)}
                    basculer={() => basculer(entree.chemin)}
                    fige={fige}
                  />
                ))}
              </ul>
            )}
          </Carte>
          <div className="mt-4 flex flex-wrap gap-2">
            <Bouton
              disabled={gardes.length === 0}
              occupe={enCours}
              onClick={() => void ecrire()}
            >
              Écrire l&apos;outillage ({gardes.length})
            </Bouton>
            <Bouton
              variante="contour"
              ton="neutre"
              disabled={fige}
              onClick={() => void plusTard()}
            >
              Plus tard
            </Bouton>
          </div>
          {gardes.length === 0 && (
            <p className="mt-2 text-annexe text-attention-texte">
              Tout a été retiré — il n&apos;y a rien à écrire. Reprenez une
              entrée, ou remettez à plus tard.
            </p>
          )}
        </>
      )}
      {refus !== null && (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {refus}
        </p>
      )}
    </CarteDuFil>
  );
}

/**
 * Le compte, et **où** ça s'écrit — la moitié de la question à laquelle un coup
 * d'œil doit répondre.
 *
 * La promesse de ne rien écraser (docs/38 §4.2) vient **avant** le geste et non
 * dans le rapport qui le suit : c'est ce qu'on veut savoir au moment de laisser
 * un outil écrire chez soi. Même parti pris que l'en-tête de liste de l'étape de
 * création, en une ligne de moins.
 */
function CompteAEcrire({
  gardes,
  projet,
}: {
  gardes: EntreeOutillage[];
  projet: Projet;
}) {
  if (gardes.length === 0) {
    return (
      <p className="text-corps text-attention-texte">
        Rien ne sera écrit — tout a été retiré.
      </p>
    );
  }
  return (
    <p className="text-corps text-texte">
      <strong>
        {gardes.length} fichier{gardes.length > 1 ? "s" : ""}
      </strong>{" "}
      {gardes.length > 1 ? "seront écrits" : "sera écrit"} dans {projet.nom} —{" "}
      {/* Le chemin **recule** (`text-texte-secondaire`) : en chasse fixe et en
          pleine longueur, il tirait l'œil autant que le compte qu'il accompagne
          (réserve du regard neuf). C'est une preuve qu'on veut pouvoir relire,
          pas la première chose à lire. */}
      <code className="font-mono text-annexe break-all text-texte-secondaire">
        {projet.racine}
      </code>{" "}
      : {comptesParNature(gardes)}. Rien n&apos;est écrasé : un fichier déjà là
      que Maestro n&apos;a pas écrit n&apos;est pas touché. Chaque commande est
      jouée avant d&apos;être écrite.
    </p>
  );
}

/**
 * Une entrée du déplié : sa case, son nom, sa nature, sa raison.
 *
 * Trois champs et non cinq, et c'est le parti pris n° 1 tenu jusqu'au bout : le
 * fil n'est pas l'écran de recommandation. Le chemin du fichier et la pièce qui
 * le justifie s'y lisent (`EtapeOutillage`), pas ici — ce qu'on décide au pied
 * d'une conversation est *garder ou retirer*, pas *auditer*.
 *
 * L'état retenu/retiré se lit à **deux** signaux, la case et le libellé barré :
 * un seul serait la couleur seule, que le filet a11y refuse (docs/30 §1).
 */
function LigneRetenue({
  entree,
  idCase,
  retenue,
  basculer,
  fige,
}: {
  entree: EntreeOutillage;
  idCase: string;
  retenue: boolean;
  basculer: () => void;
  fige: boolean;
}) {
  return (
    <li>
      {/* Une grille, et le nom en enfant **direct** du `<label>` : le lint a11y
          cherche le libellé de la case dans les deux premiers niveaux, et trois
          `<span>` empilés l'y cacheraient (constat de #1034). */}
      <label
        htmlFor={idCase}
        className="grid cursor-pointer grid-cols-[auto_1fr] items-start gap-x-2 gap-y-0.5"
      >
        <input
          id={idCase}
          type="checkbox"
          checked={retenue}
          disabled={fige}
          onChange={basculer}
          className="col-start-1 row-start-1 mt-1 size-4 shrink-0 rounded-controle border-bord-fort"
        />
        <span
          className={
            "col-start-2 row-start-1 flex flex-wrap items-center gap-2 text-corps font-medium " +
            (retenue ? "text-texte" : "text-texte-secondaire line-through")
          }
        >
          {entree.nom}
        </span>
        <span className="col-start-2 row-start-2 flex flex-wrap items-center gap-2">
          <BadgeEtat contour>{libelleNature(entree.type)}</BadgeEtat>
          {entree.etat === "deja-present" && (
            <BadgeEtat ton="info" contour>
              déjà présent
            </BadgeEtat>
          )}
        </span>
        <span className="col-start-2 row-start-3 min-w-0 break-words text-annexe text-texte-secondaire">
          {entree.raison}
        </span>
      </label>
    </li>
  );
}

/**
 * Ce que la génération a fait, dit au pied du fil — **court**.
 *
 * Les quatre listes de `RapportGeneration` (#1034) vivent sur l'écran de
 * création, et les recopier ici ferait du fil le second écran de recommandation
 * que #1031 a écarté. Ce qui reste est ce qu'une conversation doit dire : ce qui
 * a été écrit et où, et **ce qui ne l'a pas été** — taire un fichier non écrasé
 * ferait passer une non-écriture pour une écriture.
 *
 * Et depuis #1160, **ce que valent les commandes écrites** : le compte en badges
 * (un échec s'y lit sans rien ouvrir, en couleur d'alerte), puis la liste de la
 * page — la même, jamais une seconde — dépliée à la demande. C'est la forme repliée
 * que le choix de #1160 a écartée pour la page et réservée à ce pied (#1104).
 */
function RapportCourt({ rapport }: { rapport: RapportGenerationOutillage }) {
  const { ecrits, refuses, cible, refus } = rapport.rapport;
  const inconnus = rapport.retenus_inconnus ?? [];
  const verifications = rapport.rapport.verifications ?? [];
  const [deplie, setDeplie] = useState(false);
  const idDetail = useId();
  return (
    <div className="flex flex-col gap-2">
      <p className="text-corps text-texte">
        <strong>
          {ecrits.length} fichier{ecrits.length > 1 ? "s" : ""} écrit
          {ecrits.length > 1 ? "s" : ""}
        </strong>{" "}
        dans <code className="font-mono text-annexe break-all">{cible}</code>
        {rapport.regime === "branche" && " (après votre accord sur la fusion)"}.
      </p>
      {refuses.length > 0 && (
        <p className="text-annexe text-attention-texte">
          {refuses.length} fichier{refuses.length > 1 ? "s" : ""} non écrasé
          {refuses.length > 1 ? "s" : ""} : {refuses.join(", ")} — la version
          neuve est mise de côté plutôt que de remplacer la vôtre.
        </p>
      )}
      {inconnus.length > 0 && (
        <p className="text-annexe text-attention-texte">
          {inconnus.length} élément{inconnus.length > 1 ? "s" : ""} retenu
          {inconnus.length > 1 ? "s" : ""} non écrit
          {inconnus.length > 1 ? "s" : ""} : {inconnus.join(", ")} — la
          génération ne les recommande plus.
        </p>
      )}
      {refus !== "" && (
        <p className="text-annexe text-alerte-texte" role="alert">
          Rien n&apos;a été écrit : {refus}
        </p>
      )}
      {/* Le verdict de chaque commande écrite (#1160) : ici le récapitulatif en
          badges, la liste retenue pour la page se dépliant sous le même contrôle
          que « Ce qui sera écrit » — à gauche, hors du bouton flottant (#990). */}
      {verifications.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <Bouton
              variante="discret"
              ton="neutre"
              taille="petite"
              icone={IconeChevronBas}
              aria-expanded={deplie}
              aria-controls={idDetail}
              onClick={() => setDeplie((ouvert) => !ouvert)}
            >
              {verifications.length} commande{verifications.length > 1 ? "s" : ""}
            </Bouton>
            <RecapitulatifVerifications verifications={verifications} />
          </div>
          {deplie && (
            <div id={idDetail}>
              <ListeVerifications verifications={verifications} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
