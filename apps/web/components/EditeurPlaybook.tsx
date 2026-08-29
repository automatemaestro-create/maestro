"use client";

/**
 * L'éditeur du playbook d'un agent (ticket #77) : le contenu courant en
 * édition libre, publié comme nouvelle version (`PUT /api/playbooks/{agent}`),
 * et l'historique — versions publiées **et** propositions en attente —
 * consultable depuis un sélecteur, avec restauration d'une version antérieure
 * (`POST /api/playbooks/{agent}/restaurer`, EF-24/EF-25).
 *
 * Le dépôt est append-only (#76) : publier comme restaurer créent une version
 * de plus, rien n'est jamais réécrit — la restauration est donc toujours
 * réversible, aucune confirmation n'est demandée. Depuis #260 l'écran le **dit**
 * au lieu de le supposer : c'est ce qui rend une publication sans regret.
 *
 * L'historique porte aussi les **propositions** d'auto-amélioration en attente
 * (#111/#140) : des brouillons suggérés à partir des échecs d'un run, rangés à
 * part des versions humaines, avec leur justification. Ils ne sont jamais
 * chargés par le moteur tant qu'on ne les a pas appliqués au clic (le contenu
 * devient alors la version courante, chargée à chaud #78) ; un rejet les retire
 * sans toucher à la version courante.
 *
 * Ce que #260 y change, et pourquoi — trois relevés de la revue d'usage :
 *
 * - **Le texte d'aide parlait du modèle interne.** « Une publication crée une
 *   nouvelle version ; les moteurs construits ensuite la chargent » est exact et
 *   n'apprend rien à qui regarde son agent. Ce qu'il faut dire est *à partir de
 *   quand* la publication s'applique, et la réponse vient du moteur :
 *   l'exécuteur relit la version courante **à chaque tâche** (#78), donc une
 *   tâche déjà en cours garde la version avec laquelle elle a démarré.
 *
 * - **Le numéro de version manquait dans certains états.** Il est désormais dans
 *   l'en-tête *et* sur le bouton, pendant l'envoi comme avant, et **y compris
 *   quand le playbook est encore celui d'origine** — qui est la version `v0`, pas
 *   une absence de version. Un bouton « Publier » qui ne dit pas ce qu'il va
 *   créer demande de faire confiance ; celui-ci nomme la version qu'il produit.
 *
 * Depuis #259 l'onglet sert **tous** les agents, personnalisés compris, et le
 * mot « origine » a remplacé « du code » pour cette raison : l'origine d'un
 * agent personnalisé est le playbook de sa définition (#72), pas un document du
 * dépôt. C'est ce qui a fait de cet onglet le **seul** chemin d'écriture d'un
 * playbook — le champ du Profil, qui l'ignorait, a disparu.
 *
 * - **L'historique s'empilait sous l'éditeur.** Il tient maintenant dans un
 *   sélecteur en haut à droite, et choisir une entrée **remplace** la zone
 *   d'édition par sa lecture seule, à la même hauteur. C'est le point : la
 *   consultation ne pousse plus rien vers le bas et l'écran ne change pas de
 *   taille selon le nombre de versions, là où la liste dépliée grandissait à
 *   chaque publication. Les modifications en cours ne sont pas perdues pour
 *   autant — elles vivent dans l'état de l'éditeur, pas dans le DOM affiché, et
 *   reviennent telles quelles avec lui.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

import { IconeHistorique, IconePlaybooks } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  EnTeteSection,
} from "@/components/Primitives";
import {
  appliquerPropositionPlaybook,
  chargerPlaybook,
  chargerPropositionPlaybook,
  chargerPropositionsPlaybook,
  chargerVersionPlaybook,
  chargerVersionsPlaybook,
  ecrirePlaybook,
  rejeterPropositionPlaybook,
  restaurerPlaybook,
} from "@/lib/api";
import { formatDateHeure } from "@/lib/format";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type PlaybookDetail,
  type PropositionPlaybook,
  type VersionPlaybook,
} from "@/lib/types";

/* ------------------------------------------------------------------ *
 * Ce que le sélecteur désigne
 * ------------------------------------------------------------------ */

/** L'éditeur lui-même : le texte en vigueur, modifiable. */
const CLE_COURANTE = "courante";

const cleVersion = (numero: number) => `v:${numero}`;
const cleProposition = (numero: number) => `p:${numero}`;

type Entree = { genre: "version" | "proposition"; numero: number };

/**
 * La clé d'une option relue. Les deux familles sont numérotées **séparément**
 * (`v3` et `p3` coexistent, #111) : le préfixe n'est donc pas décoratif, c'est
 * lui qui dit à quelle API la demander.
 */
function decoderCle(cle: string): Entree | null {
  const separateur = cle.indexOf(":");
  if (separateur < 0) return null;
  const numero = Number(cle.slice(separateur + 1));
  if (!Number.isFinite(numero)) return null;
  const prefixe = cle.slice(0, separateur);
  if (prefixe === "v") return { genre: "version", numero };
  if (prefixe === "p") return { genre: "proposition", numero };
  return null;
}

/** L'apparence du sélecteur d'historique — le socle, en compact. */
const CLASSE_SELECTEUR =
  `${CIBLE_MINIMALE} max-w-64 rounded-md border border-bord bg-surface px-2 py-1 ` +
  "text-annexe text-texte shadow-sm focus:border-bord-fort " +
  "focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent " +
  "disabled:opacity-50 [&>option]:bg-surface [&>optgroup]:bg-surface";

/** La zone de texte et son aperçu partagent la même hauteur : l'écran ne saute pas. */
const HAUTEUR_ZONE = "h-96";

export function EditeurPlaybook({
  agent,
  onPublication,
}: {
  agent: string;
  /**
   * Prévenir la page qu'une version a été publiée. Facultatif depuis #190 :
   * l'onglet Playbook d'une fiche agent n'a pas de liste à rafraîchir, l'éditeur
   * se resynchronisant déjà tout seul.
   */
  onPublication?: () => void | Promise<void>;
}) {
  const [fiche, setFiche] = useState<PlaybookDetail | null>(null);
  const [versions, setVersions] = useState<VersionPlaybook[]>([]);
  const [propositions, setPropositions] = useState<PropositionPlaybook[]>([]);
  const [contenu, setContenu] = useState("");
  const [chargement, setChargement] = useState(true);
  const [enCours, setEnCours] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Ce que le sélecteur montre : l'éditeur, ou une entrée de l'historique en
  // lecture seule. Son contenu se charge à la demande (les deux listes REST ne
  // portent que les métadonnées), d'où l'état de chargement propre à l'aperçu.
  const [selection, setSelection] = useState(CLE_COURANTE);
  const [apercu, setApercu] = useState<string | null>(null);
  const [chargementApercu, setChargementApercu] = useState(false);
  const [erreurApercu, setErreurApercu] = useState<string | null>(null);
  // La dernière entrée demandée : deux choix rapprochés ne doivent pas laisser
  // la réponse la plus lente s'afficher sous le libellé de l'autre.
  const demande = useRef(CLE_COURANTE);

  const recharger = useCallback(async () => {
    const [nouvelleFiche, nouvellesVersions, nouvellesPropositions] =
      await Promise.all([
        chargerPlaybook(agent),
        chargerVersionsPlaybook(agent),
        chargerPropositionsPlaybook(agent),
      ]);
    setFiche(nouvelleFiche);
    setVersions(nouvellesVersions);
    setPropositions(nouvellesPropositions);
    return nouvelleFiche;
  }, [agent]);

  // Chargement différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    let abandonne = false;
    const tick = setTimeout(() => {
      setChargement(true);
      setErreur(null);
      recharger()
        .then((nouvelleFiche) => {
          if (!abandonne) setContenu(nouvelleFiche.contenu);
        })
        .catch((e) => {
          if (!abandonne) setErreur(e instanceof Error ? e.message : String(e));
        })
        .finally(() => {
          if (!abandonne) setChargement(false);
        });
    }, 0);
    return () => {
      abandonne = true;
      clearTimeout(tick);
    };
  }, [recharger]);

  /** Refermer l'aperçu : on revient au texte en vigueur, modifications comprises. */
  const revenirALEdition = useCallback(() => {
    demande.current = CLE_COURANTE;
    setSelection(CLE_COURANTE);
    setApercu(null);
    setErreurApercu(null);
    setChargementApercu(false);
  }, []);

  const choisir = async (cle: string) => {
    demande.current = cle;
    if (cle === CLE_COURANTE) {
      revenirALEdition();
      return;
    }
    const entree = decoderCle(cle);
    if (entree === null) return;
    setSelection(cle);
    setApercu(null);
    setErreurApercu(null);
    setChargementApercu(true);
    try {
      const detail =
        entree.genre === "version"
          ? await chargerVersionPlaybook(agent, entree.numero)
          : await chargerPropositionPlaybook(agent, entree.numero);
      if (demande.current === cle) setApercu(detail.contenu);
    } catch (e) {
      if (demande.current === cle) {
        setErreurApercu(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (demande.current === cle) setChargementApercu(false);
    }
  };

  // Publication, restauration et application d'une proposition partagent la même
  // mécanique : l'action, puis rechargement (fiche + historique + propositions)
  // et resynchronisation de l'éditeur sur la nouvelle version courante.
  // `resynchroniser: false` pour le rejet, qui ne change pas la version courante :
  // l'éditeur garde alors les modifications en cours de l'utilisateur.
  //
  // Toutes referment l'aperçu — l'entrée consultée vient d'être appliquée,
  // restaurée ou écartée : la laisser à l'écran montrerait un état révolu.
  const executer = async (
    action: () => Promise<void>,
    { resynchroniser = true }: { resynchroniser?: boolean } = {},
  ) => {
    setEnCours(true);
    setErreur(null);
    try {
      await action();
      const nouvelleFiche = await recharger();
      if (resynchroniser) setContenu(nouvelleFiche.contenu);
      revenirALEdition();
      await onPublication?.();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  if (chargement) {
    return <p className="text-corps text-texte-secondaire">Chargement du playbook…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-corps text-alerte-texte" role="alert">
        Playbook illisible : {erreur}
      </p>
    );
  }

  const modifie = contenu !== fiche.contenu;
  const jamaisEdite = fiche.source === PLAYBOOK_SOURCE_DEFAUT;
  const prochaine = fiche.version + 1;

  // Le plus récent d'abord, dans les deux familles.
  const propositionsRecentes = [...propositions].reverse();
  // La version courante n'entre pas dans l'historique consultable : son contenu
  // *est* celui de l'éditeur, et l'offrir deux fois ferait chercher la
  // différence entre les deux.
  const versionsPassees = [...versions]
    .reverse()
    .filter((v) => v.version !== fiche.version);
  const historiqueVide =
    versionsPassees.length === 0 && propositionsRecentes.length === 0;

  const entree = decoderCle(selection);
  const versionConsultee =
    entree?.genre === "version"
      ? (versionsPassees.find((v) => v.version === entree.numero) ?? null)
      : null;
  const propositionConsultee =
    entree?.genre === "proposition"
      ? (propositionsRecentes.find((p) => p.version === entree.numero) ?? null)
      : null;
  const enConsultation = versionConsultee !== null || propositionConsultee !== null;

  return (
    <div className="flex min-w-0 flex-1 flex-col gap-4">
      <section aria-label={`Playbook de ${agent}`}>
        {/* Le nom de l'agent est porté par l'en-tête de la fiche (#190). */}
        <EnTeteSection
          niveau={3}
          icone={IconePlaybooks}
          titre={`Playbook${fiche.role ? ` · ${fiche.role}` : ""}`}
          className="mb-2"
          aside={
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
              {/* Critère 2 : la version en vigueur se lit sans rien ouvrir, et
                  le playbook d'origine en est une — la v0, pas un trou.
                  « d'origine » et non « du code » depuis #259 : l'onglet sert
                  aussi les agents personnalisés, dont l'origine est le playbook
                  de leur définition et non un document du dépôt. */}
              <span className="chiffre text-annexe text-texte-secondaire">
                en vigueur : v{fiche.version}
                {jamaisEdite
                  ? " · playbook d’origine"
                  : fiche.cree_le
                    ? ` · ${formatDateHeure(fiche.cree_le)}`
                    : ""}
              </span>
              {propositionsRecentes.length > 0 && (
                <BadgeEtat ton="accent" className="chiffre">
                  {propositionsRecentes.length} en attente
                </BadgeEtat>
              )}
              {historiqueVide ? (
                <span className="text-annexe text-texte-secondaire">
                  Aucune version antérieure
                </span>
              ) : (
                // Le libellé **entoure** le sélecteur plutôt que de le viser par
                // `htmlFor` : deux fiches agent montées ensemble résoudraient le
                // même identifiant (voir `CadreChamp` dans les primitives).
                <label className="flex items-center gap-1.5 text-annexe text-texte-secondaire">
                  <IconeHistorique className="size-4 shrink-0" />
                  Historique
                  <select
                    value={selection}
                    disabled={enCours}
                    onChange={(e) => void choisir(e.target.value)}
                    className={CLASSE_SELECTEUR}
                  >
                    <option value={CLE_COURANTE}>
                      Version en vigueur · v{fiche.version}
                    </option>
                    {/* Les propositions d'abord : elles attendent une décision. */}
                    {propositionsRecentes.length > 0 && (
                      <optgroup label="Propositions en attente">
                        {propositionsRecentes.map((proposition) => (
                          <option
                            key={cleProposition(proposition.version)}
                            value={cleProposition(proposition.version)}
                          >
                            p{proposition.version} · {proposition.provenance} ·{" "}
                            {formatDateHeure(proposition.cree_le)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                    {versionsPassees.length > 0 && (
                      <optgroup label="Versions publiées">
                        {versionsPassees.map((version) => (
                          <option
                            key={cleVersion(version.version)}
                            value={cleVersion(version.version)}
                          >
                            v{version.version} · {formatDateHeure(version.cree_le)}
                          </option>
                        ))}
                      </optgroup>
                    )}
                  </select>
                </label>
              )}
            </div>
          }
        />

        {enConsultation ? (
          <ApercuHistorique
            titre={
              versionConsultee
                ? `Version ${versionConsultee.version}`
                : `Proposition ${propositionConsultee?.version}`
            }
            reference={
              versionConsultee
                ? `v${versionConsultee.version}`
                : `p${propositionConsultee?.version}`
            }
            provenance={propositionConsultee?.provenance}
            creeLe={(versionConsultee ?? propositionConsultee)?.cree_le ?? ""}
            justification={propositionConsultee?.justification}
            contenu={apercu}
            chargement={chargementApercu}
            erreur={erreurApercu}
            editionModifiee={modifie}
            enCours={enCours}
            explication={
              versionConsultee
                ? `Restaurer republie ce texte comme version ${prochaine} : la version ${fiche.version} reste dans l’historique, rien n’est écrasé.`
                : `Appliquer publie ce texte comme version ${prochaine}. Rejeter l’écarte sans toucher à la version en vigueur.`
            }
            actions={
              versionConsultee ? (
                <Bouton
                  variante="contour"
                  ton="attention"
                  taille="petite"
                  disabled={enCours}
                  onClick={() =>
                    void executer(() =>
                      restaurerPlaybook(agent, versionConsultee.version),
                    )
                  }
                >
                  Restaurer la version {versionConsultee.version}
                </Bouton>
              ) : (
                propositionConsultee && (
                  <>
                    <Bouton
                      taille="petite"
                      disabled={enCours}
                      onClick={() =>
                        void executer(() =>
                          appliquerPropositionPlaybook(
                            agent,
                            propositionConsultee.version,
                          ),
                        )
                      }
                    >
                      Appliquer
                    </Bouton>
                    <Bouton
                      variante="contour"
                      ton="alerte"
                      taille="petite"
                      disabled={enCours}
                      onClick={() =>
                        void executer(
                          () =>
                            rejeterPropositionPlaybook(
                              agent,
                              propositionConsultee.version,
                            ),
                          { resynchroniser: false },
                        )
                      }
                    >
                      Rejeter
                    </Bouton>
                  </>
                )
              )
            }
            revenir={revenirALEdition}
          />
        ) : (
          <>
            <textarea
              value={contenu}
              onChange={(e) => setContenu(e.target.value)}
              disabled={enCours}
              aria-label="Contenu du playbook"
              spellCheck={false}
              className={`${HAUTEUR_ZONE} w-full resize-y rounded-md border border-bord bg-surface p-3 font-mono text-annexe leading-relaxed text-texte shadow-sm focus:border-bord-fort focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent disabled:opacity-50`}
            />
            <div className="mt-2 flex flex-wrap items-center gap-3">
              {/* Critère 2 : le numéro que le clic va créer, dans tous les états
                  — pendant l'envoi compris, où le libellé changeait de sujet. */}
              <Bouton
                disabled={!modifie || !contenu.trim()}
                occupe={enCours}
                onClick={() => void executer(() => ecrirePlaybook(agent, contenu))}
              >
                {enCours
                  ? `Publication de la version ${prochaine}…`
                  : `Publier la version ${prochaine}`}
              </Bouton>
              {modifie && !enCours && (
                <Bouton
                  variante="contour"
                  ton="neutre"
                  onClick={() => setContenu(fiche.contenu)}
                >
                  Annuler les modifications
                </Bouton>
              )}
            </div>
            {/* Critère 1 : ce que publier fait, et à partir de quand. « À partir
                de quand » vient du moteur — l'exécuteur relit la version
                courante à chaque tâche (#78) —, pas d'une formule prudente. */}
            <p className="mt-2 text-annexe text-texte-secondaire">
              {modifie && (
                <span className="font-medium text-texte">
                  Modifications non publiées.{" "}
                </span>
              )}
              Publier enregistre ce texte comme version {prochaine} : les tâches
              lancées ensuite l’utiliseront, celles déjà en cours gardent la
              version avec laquelle elles ont démarré. Rien n’est écrasé — chaque
              version reste consultable et restaurable depuis l’historique.
            </p>
          </>
        )}

        {/* L'échec d'une action se dit **hors** des deux états : une restauration
            refusée laisse l'aperçu ouvert, et son message serait alors invisible
            s'il vivait dans la seule branche de l'éditeur. */}
        {erreur && (
          <p className="mt-2 text-annexe text-alerte-texte" role="alert">
            {erreur}
          </p>
        )}
      </section>
    </div>
  );
}

/**
 * Une entrée de l'historique en lecture seule, **à la place** de l'éditeur : le
 * repère (`v2`, `p4`), sa date, ce qu'on peut en faire, et le texte à la même
 * hauteur que la zone d'édition. Occuper la place plutôt que s'ajouter dessous
 * est tout ce que le critère 3 demande — c'est la liste dépliée qui mangeait
 * l'écran, pas la consultation elle-même.
 */
function ApercuHistorique({
  titre,
  reference,
  provenance,
  creeLe,
  justification,
  contenu,
  chargement,
  erreur,
  editionModifiee,
  enCours,
  explication,
  actions,
  revenir,
}: {
  titre: string;
  reference: string;
  /** L'origine d'une proposition — absente sur une version publiée. */
  provenance?: string;
  creeLe: string;
  justification?: string;
  contenu: string | null;
  chargement: boolean;
  erreur: string | null;
  /** L'éditeur porte des modifications non publiées : le dire rassure sur leur sort. */
  editionModifiee: boolean;
  enCours: boolean;
  explication: string;
  actions: ReactNode;
  revenir: () => void;
}) {
  return (
    <div className="rounded-md border border-bord bg-surface-creuse p-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="chiffre font-mono text-annexe font-medium text-texte">
          {reference}
        </span>
        {provenance && <BadgeEtat ton="accent">{provenance}</BadgeEtat>}
        {creeLe && (
          <span className="text-annexe text-texte-secondaire">
            {formatDateHeure(creeLe)}
          </span>
        )}
        <div className="ml-auto flex flex-wrap gap-2">
          {actions}
          <Bouton
            variante="contour"
            ton="neutre"
            taille="petite"
            disabled={enCours}
            onClick={revenir}
          >
            Revenir à l’édition
          </Bouton>
        </div>
      </div>
      {justification && (
        <p className="mt-2 max-h-40 overflow-auto whitespace-pre-wrap text-annexe text-texte">
          {justification}
        </p>
      )}
      {erreur ? (
        <p className="mt-2 text-annexe text-alerte-texte" role="alert">
          {erreur}
        </p>
      ) : chargement || contenu === null ? (
        <p
          className={`mt-2 ${HAUTEUR_ZONE} rounded-md border border-bord bg-surface p-3 text-annexe text-texte-secondaire`}
        >
          Chargement du texte…
        </p>
      ) : (
        // La même zone que l'éditeur, en `readOnly` — et non un `<pre>` qui
        // défile : une zone défilante doit être atteignable au clavier (WCAG 2.2
        // §2.1.1), ce qu'un bloc de texte n'obtient qu'avec un `tabIndex` posé
        // sur un élément non interactif. Un `textarea` l'est nativement, se
        // copie, et donne à la lecture le cadre exact de l'écriture.
        // `readOnly` et surtout pas `disabled` : un champ désactivé sort de la
        // tabulation, donc son texte cesse d'être atteignable.
        <textarea
          value={contenu}
          readOnly
          aria-label={`Contenu de ${titre.toLowerCase()}`}
          spellCheck={false}
          className={`mt-2 ${HAUTEUR_ZONE} w-full resize-y rounded-md border border-bord bg-surface p-3 font-mono text-annexe leading-relaxed text-texte shadow-sm focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent`}
        />
      )}
      <p className="mt-2 text-annexe text-texte-secondaire">
        Lecture seule. {explication}
        {editionModifiee && " Vos modifications non publiées sont conservées."}
      </p>
    </div>
  );
}
