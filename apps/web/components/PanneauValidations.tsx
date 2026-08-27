"use client";

/**
 * Les validations humaines (docs/05 §2.6, #48) — **une** présentation, deux
 * surfaces (#272, lot 5 de #244).
 *
 * C'est l'écran le plus coûteux à mal rendre : une demande de validation est
 * bloquante, un moteur est en pause et un run attend derrière. Ce fichier porte
 * donc trois décisions, et une seule fois chacune :
 *
 * 1. **La plus ancienne d'abord.** La file est triée par horodatage croissant et
 *    la tête est rendue en plein — c'est elle qui retient un moteur depuis le
 *    plus longtemps, et rien d'autre dans la demande ne dit l'urgence. Le tri
 *    n'existait pas : l'ordre était celui du backend, donc celui de personne.
 * 2. **Le temps d'attente est au premier plan** (`formatAttente`), là où la
 *    carte ne montrait que l'heure de la demande — un chiffre dont il fallait
 *    faire la soustraction soi-même pour savoir s'il y avait urgence.
 * 3. **Deux surfaces, une carte.** `PanneauValidations` est l'**aperçu** du
 *    tableau de bord (la plus ancienne, décidable sur place, et une ligne de
 *    renvoi pour le reste — la règle des trois places, docs/30 §4) ;
 *    `FileValidations` est le **plein format** de la page. Les deux montent la
 *    même `CarteValidation`, avec les mêmes champs dans le même ordre : ce qui
 *    change est ce qu'on voit autour, jamais ce qu'on lit pour trancher.
 *
 * Le **refus motivé** (critère 2) est offert sans coûter un geste à qui n'en
 * veut pas : « Refuser » refuse, en un clic, comme avant ; un bouton discret
 * ouvre à côté un motif facultatif, qui part avec ce même bouton. Rien dans
 * l'écran ne bouge tant que rien n'est tranché — la note technique du ticket
 * l'exige, et un formulaire ouvert n'est pas une décision prise.
 *
 * Le **temps réel** est tenu par la clé de React et par elle seule : chaque carte
 * est keyée sur `tache_id`, donc une demande tranchée ailleurs démonte *sa*
 * carte et emporte son état local (motif en cours de frappe, erreur, envoi en
 * vol). Sans cette clé, la file se décalant d'un cran, un motif écrit pour une
 * demande se retrouverait attaché à la suivante — un refus motivé à côté de la
 * plaque, sans que rien ne le signale.
 *
 * Ce qui **n'a pas bougé** : une demande peut porter un **diff** (#227, EF-37)
 * ou un **acte** (#581, l'outil appelé et ses arguments), et c'est l'acte qui
 * prend la tête de la carte quand il y en a un — depuis #573 le déclencheur de
 * l'arbitrage est l'acte et non le texte de la tâche, si bien qu'afficher
 * « Rédiger le README » au-dessus d'un `rm -rf` ferait trancher à côté.
 */

import { useState } from "react";

import { IconeAgent, IconeAlerte } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  Carte,
  ChampTexte,
  CIBLE_MINIMALE,
  EnTeteSection,
  LienRenvoi,
} from "@/components/Primitives";
import { formatAttente } from "@/lib/format";
import { useHorloge } from "@/lib/horloge";
import { entreeParLibelle } from "@/lib/navigation";
import {
  NATURE_AJOUT,
  NATURE_MODIFICATION,
  NATURE_SUPPRESSION,
  VALIDATION_EN_ATTENTE,
  type DiffProjet,
  type Validation,
} from "@/lib/types";

/**
 * Trancher une demande. `motif` accompagne un **refus** et reste facultatif :
 * omis, l'appel est celui d'avant #272 — c'est la signature du contexte global
 * (`lib/useControlTower`), reprise telle quelle plutôt que redéclarée au plus
 * étroit, sans quoi la carte ne pourrait plus proposer de motiver.
 */
type Decider = (
  tacheId: string,
  approuve: boolean,
  motif?: string,
) => Promise<void>;

/** Le libellé du menu qui mène à la page — le chemin n'est jamais écrit ici. */
const PAGE_VALIDATIONS = "Validations";

/**
 * Les demandes qui attendent, **la plus ancienne en tête**.
 *
 * Le tri se fait sur la chaîne ISO du backend, comparée telle quelle : à fuseau
 * égal — et le backend n'en émet qu'un, UTC — l'ordre lexicographique d'un ISO 8601
 * *est* l'ordre chronologique, sans construire une `Date` par comparaison. Une
 * demande sans horodatage (donnée ancienne, événement amputé) passe **en
 * dernier** : elle n'a pas d'âge à faire valoir, et la mettre en tête ferait
 * traiter d'abord celle dont on sait le moins.
 */
export function fileDAttente(validations: Validation[]): Validation[] {
  return validations
    .filter((v) => v.statut === VALIDATION_EN_ATTENTE)
    .sort((a, b) => {
      if (a.horodatage === b.horodatage) return 0;
      if (!a.horodatage) return 1;
      if (!b.horodatage) return -1;
      return a.horodatage < b.horodatage ? -1 : 1;
    });
}

/** L'en-tête commun aux deux surfaces : ce qui attend, et combien. */
function EnTeteFile({ nombre, renvoi }: { nombre: number; renvoi?: boolean }) {
  const page = renvoi ? entreeParLibelle(PAGE_VALIDATIONS) : undefined;
  return (
    <EnTeteSection
      titre={
        <>
          Validations en attente
          <BadgeEtat ton="attention" className="chiffre">
            {nombre}
          </BadgeEtat>
        </>
      }
      icone={IconeAlerte}
      ton="attention"
      className="mb-2"
      aside={
        page && (
          <LienRenvoi
            renvoi={{ href: page.href, libelle: "Ouvrir les validations" }}
          />
        )
      }
    />
  );
}

/**
 * L'**aperçu** du tableau de bord : la demande la plus ancienne, entière et
 * décidable sur place, puis une ligne pour le reste.
 *
 * Montrer toute la file ici serait la refaire — et le tableau de bord n'est pas
 * l'écran des validations. Ce que la règle des trois places prescrit dans ce cas
 * est exactement ce qu'on fait : ce qui ne tient pas devient une ligne avec un
 * renvoi (docs/30 §4). Ce qui compte est qu'on puisse trancher **la plus
 * urgente** sans quitter l'écran, et c'est le critère du ticket.
 */
export function PanneauValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const maintenant = useHorloge();
  const file = fileDAttente(validations);
  if (file.length === 0) return null;
  const [premiere, ...suivantes] = file;

  return (
    <Carte
      balise="section"
      ton="attention"
      data-guide="validations"
      aria-label="Validations en attente"
    >
      <EnTeteFile nombre={file.length} renvoi />
      <CarteValidation
        key={premiere.tache_id}
        validation={premiere}
        decider={decider}
        maintenant={maintenant}
        premiere
      />
      {suivantes.length > 0 && (
        <p className="mt-3 text-annexe text-texte-secondaire">
          {suivantes.length === 1
            ? "1 autre demande attend son tour."
            : `${suivantes.length} autres demandes attendent leur tour.`}
        </p>
      )}
    </Carte>
  );
}

/**
 * Le **plein format** de la page Validations : la plus ancienne en tête, aérée,
 * puis les suivantes sous leur propre titre.
 *
 * Les suivantes ne sont pas résumées — même carte, mêmes champs, seule la
 * densité change. C'est ce que demande le critère 3 : ce qu'on lit pour trancher
 * ne doit pas dépendre de la place qu'une demande occupe dans la file, sans quoi
 * la deuxième se déciderait sur moins d'information que la première.
 */
export function FileValidations({
  validations,
  decider,
}: {
  validations: Validation[];
  decider: Decider;
}) {
  const maintenant = useHorloge();
  const file = fileDAttente(validations);
  if (file.length === 0) return null;
  const [premiere, ...suivantes] = file;

  return (
    <Carte balise="section" ton="attention" aria-label="Validations en attente">
      <EnTeteFile nombre={file.length} />
      <CarteValidation
        key={premiere.tache_id}
        validation={premiere}
        decider={decider}
        maintenant={maintenant}
        premiere
      />
      {suivantes.length > 0 && (
        <>
          <EnTeteSection
            niveau={3}
            titre={suivantes.length === 1 ? "La suivante" : "Les suivantes"}
            ton="attention"
            className="mt-4 mb-2"
          />
          <ul className="grid grid-cols-1 gap-3 @3xl:grid-cols-2">
            {suivantes.map((validation) => (
              <li key={validation.tache_id} className="flex">
                <CarteValidation
                  validation={validation}
                  decider={decider}
                  maintenant={maintenant}
                />
              </li>
            ))}
          </ul>
        </>
      )}
    </Carte>
  );
}

/**
 * Une demande, telle qu'on la lit pour trancher — la seule présentation du
 * produit, montée aussi bien dans l'aperçu que dans la page.
 *
 * L'ordre des blocs est celui de la décision : **ce qu'on approuve** (l'acte ou
 * le titre) et **depuis quand ça attend**, puis **qui** le demande, puis **ce
 * que ça ferait** (diff, arguments ou description), puis **pourquoi c'est
 * sensible** (la raison de la classification), puis seulement les gestes. Un
 * bouton lu avant sa question est un bouton qu'on clique sans lire.
 *
 * `premiere` n'ajoute ni ne retire rien : elle aère la carte de tête. Ce qui met
 * la plus ancienne en avant est sa **place**, pas un contenu réservé.
 */
function CarteValidation({
  validation,
  decider,
  maintenant,
  premiere = false,
}: {
  validation: Validation;
  decider: Decider;
  maintenant: number | null;
  premiere?: boolean;
}) {
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);
  const [motifOuvert, setMotifOuvert] = useState(false);
  const [motif, setMotif] = useState("");

  const surDecision = async (approuve: boolean) => {
    setEnCours(true);
    setErreur(null);
    // Le motif n'accompagne que le refus, et n'est **passé** que s'il y en a
    // un : sans lui l'appel est exactement celui d'avant #272, ce qui garde
    // « approuver » et « refuser sec » hors de portée d'une régression du canal
    // motivé.
    const raison = approuve ? "" : motif.trim();
    try {
      if (raison) await decider(validation.tache_id, false, raison);
      else await decider(validation.tache_id, approuve);
      // Succès : la demande sort de « en attente » au rechargement et la carte
      // se démonte — inutile de rendre la main. On ne la rend qu'en cas d'échec,
      // sans quoi un rechargement lent rouvrirait les boutons sur une décision
      // déjà partie, et le second clic reviendrait en 409.
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
      setEnCours(false);
    }
  };

  // L'acte a la tête quand il y en a un (#581) ; sinon on retombe sur le titre
  // de la tâche, qui est ce que la demande portait de plus parlant avant #573.
  const acte = validation.outil;
  const attente = formatAttente(validation.horodatage, maintenant);
  const idMotif = `motif-refus-${validation.tache_id}`;

  return (
    <Carte
      ton="attentionClaire"
      densite={premiere ? "aeree" : "normale"}
      className="w-full text-corps"
    >
      {/* La question et son ancienneté sur la même ligne, et le titre seul dans
          son paragraphe : c'est ce qu'on lit en premier, ça ne se partage pas
          avec une pastille. */}
      <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
        <p className="min-w-0 font-medium" title={validation.tache_id}>
          {acte ? (
            <>
              <span className="text-texte-secondaire">Appel de </span>
              <span className="font-mono">{acte}</span>
            </>
          ) : (
            validation.titre || validation.tache_id
          )}
        </p>
        {attente && (
          <BadgeEtat ton="attention" className="chiffre shrink-0">
            {attente}
          </BadgeEtat>
        )}
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-1 text-annexe text-texte-secondaire">
        <IconeAgent className="size-3.5 shrink-0" />
        Agent {validation.agent}
        {validation.role ? ` · ${validation.role}` : ""}
        {/* Le titre de la tâche reste lisible, une place plus bas : il dit d'où
            vient l'acte, il ne dit pas ce qu'on approuve. */}
        {acte && validation.titre ? ` · ${validation.titre}` : ""}
      </p>
      {validation.diff ? (
        <DiffApplication diff={validation.diff} />
      ) : acte ? (
        <ArgumentsActe arguments={validation.arguments} />
      ) : (
        validation.description && (
          <p className="mt-2 whitespace-pre-wrap text-annexe text-texte-secondaire">
            {validation.description}
          </p>
        )
      )}
      {validation.raison && (
        <p className="mt-2 text-annexe text-attention-texte italic">
          Motif : {validation.raison}
        </p>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Bouton occupe={enCours} onClick={() => void surDecision(true)}>
          {enCours ? "Envoi…" : "Approuver"}
        </Bouton>
        <Bouton
          variante="contour"
          ton="alerte"
          disabled={enCours}
          onClick={() => void surDecision(false)}
        >
          Refuser
        </Bouton>
        {/* Le motif n'est pas une étape du refus : il s'ouvre à côté, et c'est
            toujours « Refuser » qui tranche. Rendre le refus conditionnel à une
            saisie ferait payer à chaque demande le prix de celles qu'on veut
            expliquer.

            Refermer **efface** ce qui a été écrit, et ce n'est pas un détail :
            un motif conservé hors de l'écran partirait quand même avec le refus,
            c'est-à-dire un texte envoyé au journal du run que plus personne
            n'avait sous les yeux. « Sans motif » doit vouloir dire sans motif. */}
        <Bouton
          variante="discret"
          ton="alerte"
          taille="petite"
          className={CIBLE_MINIMALE}
          disabled={enCours}
          aria-expanded={motifOuvert}
          // Posé seulement quand la zone existe : `aria-controls` visant un
          // identifiant absent est une référence morte, et axe la tolère au
          // repli sans qu'on ait à s'en remettre à cette tolérance.
          aria-controls={motifOuvert ? idMotif : undefined}
          onClick={() => {
            setMotifOuvert((avant) => !avant);
            if (motifOuvert) setMotif("");
          }}
        >
          {motifOuvert ? "Sans motif" : "Motiver le refus"}
        </Bouton>
      </div>
      {motifOuvert && (
        <ChampTexte
          id={idMotif}
          className="mt-2"
          libelle="Motif du refus (facultatif)"
          aide="Il part avec le refus, dans le journal du run — l'approbation l'ignore."
          rows={2}
          maxLength={500}
          disabled={enCours}
          value={motif}
          onChange={(e) => setMotif(e.target.value)}
        />
      )}
      {erreur && (
        <p className="mt-2 text-annexe font-medium text-alerte-texte">{erreur}</p>
      )}
    </Carte>
  );
}

/**
 * Ce qu'on passe à l'outil (#581) : une ligne par argument, la clé puis sa
 * valeur. Le nom de l'outil n'y est pas répété — il est en tête de la carte,
 * c'est lui la question.
 *
 * La valeur est rendue **telle qu'elle a été composée**, sauts de ligne compris
 * (`whitespace-pre-wrap`) : un script passé à `Bash` aplati en une ligne se lit
 * autrement qu'il ne s'exécutera, et approuver ce qu'on lit mal n'est pas
 * approuver. Le backend l'a déjà bornée et expurgée (`maestro.acte`,
 * `evenement_demande`) — il n'y a donc rien à couper ici.
 *
 * Comme le diff, le bloc **défile** au-delà d'une poignée de lignes plutôt que
 * de pousser Approuver/Refuser hors de l'écran : une demande dont on ne voit
 * plus la réponse n'en est plus une.
 */
function ArgumentsActe({ arguments: args }: { arguments: Record<string, string> | null }) {
  const entrees = Object.entries(args ?? {});
  if (entrees.length === 0) {
    // Un outil sans paramètre existe, et un producteur qui n'en rapporte aucun
    // aussi : les deux se disent du même mot, aucun n'est une anomalie à signaler.
    return (
      <p className="mt-2 text-annexe text-texte-secondaire italic">
        Aucun argument
      </p>
    );
  }
  return (
    <dl className="mt-2 max-h-48 overflow-y-auto rounded-md border border-bord bg-surface-creuse px-2 py-1.5 font-mono text-annexe">
      {entrees.map(([cle, valeur]) => (
        <div key={cle} className="flex items-baseline gap-2 py-0.5">
          <dt className="shrink-0 text-texte-secondaire">{cle}</dt>
          <dd className="min-w-0 flex-1 whitespace-pre-wrap break-words text-texte">
            {valeur}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/**
 * Le diff d'une demande d'application dans le projet (#227, EF-37) : l'en-tête
 * dit ce qui se produirait à l'accord — fusion d'une branche pour un projet
 * versionné, écriture des fichiers sinon — puis chaque fichier avec ses lignes.
 *
 * Le tableau **défile** au-delà d'une quinzaine de fichiers plutôt que de
 * pousser les boutons Approuver/Refuser hors de l'écran : une demande de
 * validation dont on ne voit plus la réponse n'en est plus une.
 */
function DiffApplication({ diff }: { diff: DiffProjet }) {
  return (
    <div className="chiffre mt-2 rounded-md border border-bord bg-surface-creuse text-annexe">
      <p className="border-b border-bord px-2 py-1.5 text-texte">
        <span className="font-medium">
          {diff.fichiers} fichier{diff.fichiers > 1 ? "s" : ""}
        </span>{" "}
        <span className="text-emerald-600 dark:text-emerald-400">+{diff.ajouts}</span>{" "}
        <span className="text-rose-600 dark:text-rose-400">−{diff.suppressions}</span>
        <br />
        <span className="text-texte-secondaire">
          {diff.branche
            ? `Fusion de ${diff.branche} vers ${diff.base}`
            : "Écriture des fichiers dans le projet (non versionné)"}
        </span>
      </p>
      <ul className="max-h-48 overflow-y-auto px-2 py-1.5 font-mono">
        {diff.modifications.map((modification) => (
          <li key={modification.chemin} className="flex items-baseline gap-2 py-0.5">
            <span
              aria-hidden
              className={`w-3 shrink-0 text-center ${couleurNature(modification.nature)}`}
            >
              {SIGNE_NATURE[modification.nature] ?? "~"}
            </span>
            <span className="min-w-0 flex-1 break-all text-texte">
              {modification.chemin}
            </span>
            <span className="shrink-0 text-texte-secondaire">
              {modification.binaire
                ? "binaire"
                : `+${modification.ajouts} −${modification.suppressions}`}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const SIGNE_NATURE: Record<string, string> = {
  [NATURE_AJOUT]: "+",
  [NATURE_MODIFICATION]: "~",
  [NATURE_SUPPRESSION]: "−",
};

function couleurNature(nature: string): string {
  if (nature === NATURE_AJOUT) return "text-emerald-600 dark:text-emerald-400";
  if (nature === NATURE_SUPPRESSION) return "text-rose-600 dark:text-rose-400";
  return "text-amber-600 dark:text-amber-400";
}
