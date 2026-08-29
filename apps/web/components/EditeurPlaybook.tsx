"use client";

/**
 * L'éditeur du playbook d'un agent (ticket #77) : le contenu courant en
 * édition libre, publié comme nouvelle version (`PUT /api/playbooks/{agent}`),
 * et l'historique des versions consultable avec restauration d'une version
 * antérieure (`POST /api/playbooks/{agent}/restaurer`, EF-24/EF-25).
 *
 * Le dépôt est append-only (#76) : publier comme restaurer créent une version
 * de plus, rien n'est jamais réécrit — la restauration est donc toujours
 * réversible, aucune confirmation n'est demandée.
 *
 * L'historique porte aussi les **propositions** d'auto-amélioration en attente
 * (#111/#140) : des brouillons suggérés à partir des échecs d'un run, affichés
 * à part des versions humaines, avec leur justification. Ils ne sont jamais
 * chargés par le moteur tant qu'on ne les a pas appliqués au clic (le contenu
 * devient alors la version courante, chargée à chaud #78) ; un rejet les retire
 * sans toucher à la version courante.
 *
 * ## La rédaction assistée (#261)
 *
 * L'éditeur ne se contente plus d'accepter du texte, il aide à l'écrire — sans
 * jamais publier à la place de qui que ce soit. Deux aides, à deux échelles :
 *
 * - les **complétions en cours de frappe** (`lib/completionsPlaybook`), tirées du
 *   lexique du dépôt (`GET /api/playbooks/lexique`) : structures de section et
 *   tournures que les playbooks livrés ont en commun. Déterministes et locales —
 *   aucun appel modèle par frappe —, elles s'acceptent au `Tab` et s'ignorent en
 *   continuant de taper ou par `Échap` ;
 * - l'**assistant** (`POST …/redaction`), une réécriture du brouillon demandée en
 *   un clic, rendue en **différentiel** (`lib/diff`) avant toute application.
 *
 * ⚠ **Rien de tout cela ne publie.** Accepter une complétion ou appliquer une
 * réécriture ne touche que `contenu`, l'état local de la zone d'édition ; la
 * version en vigueur ne bouge que par le bouton « Publier », qui n'a pas changé.
 * C'est le troisième critère de #261, et c'est pour lui que l'assistance ne passe
 * pas par les propositions stockées de #111/#140 : les appliquer *publie* une
 * version (voir `maestro.controltower.auto_amelioration`).
 */

import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  IconeAssistant,
  IconeHistorique,
  IconePlaybooks,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  CIBLE_MINIMALE,
  CLASSE_CONTROLE,
  EnTeteSection,
  classesCarte,
} from "@/components/Primitives";
import {
  appliquerPropositionPlaybook,
  chargerLexiquePlaybook,
  chargerPlaybook,
  chargerPropositionPlaybook,
  chargerPropositionsPlaybook,
  chargerVersionPlaybook,
  chargerVersionsPlaybook,
  ecrirePlaybook,
  redigerPlaybook,
  rejeterPropositionPlaybook,
  restaurerPlaybook,
} from "@/lib/api";
import {
  accepter,
  completionsPour,
  segmentCourant,
  type Completion,
} from "@/lib/completionsPlaybook";
import { compter, condenser, differencier } from "@/lib/diff";
import { formatDateHeure } from "@/lib/format";
import {
  PLAYBOOK_SOURCE_DEFAUT,
  type LexiquePlaybook,
  type PlaybookDetail,
  type PropositionPlaybook,
  type RedactionPlaybook,
  type VersionPlaybook,
} from "@/lib/types";

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

  // Rédaction assistée (#261). Le lexique est chargé une fois — c'est le
  // vocabulaire du dépôt, pas celui de l'agent, et il ne change qu'avec les
  // documents livrés. Un lexique indisponible n'est pas une panne de l'éditeur :
  // les complétions se taisent, tout le reste fonctionne.
  const [lexique, setLexique] = useState<LexiquePlaybook | null>(null);
  const [completions, setCompletions] = useState<Completion[]>([]);
  const [choisie, setChoisie] = useState(0);
  const zone = useRef<HTMLTextAreaElement | null>(null);
  // Une **ref** et non un état : la position à restaurer est un ordre donné au
  // DOM, pas une donnée dont dépend le rendu — la stocker en état ferait un
  // rendu de plus, puis un second pour la remettre à zéro.
  const caret = useRef<number | null>(null);
  const idListe = useId();

  const [assistant, setAssistant] = useState(false);
  const [consigne, setConsigne] = useState("");
  const [redaction, setRedaction] = useState<RedactionPlaybook | null>(null);
  const [redactionEnCours, setRedactionEnCours] = useState(false);
  const [erreurRedaction, setErreurRedaction] = useState<string | null>(null);

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

  // Le lexique de complétion, chargé à part du playbook : il ne dépend d'aucun
  // agent, et son absence ne doit rien empêcher — d'où un `catch` qui se tait
  // plutôt qu'une erreur affichée pour une aide à la frappe.
  useEffect(() => {
    let abandonne = false;
    chargerLexiquePlaybook()
      .then((charge) => {
        if (!abandonne) setLexique(charge);
      })
      .catch(() => {
        /* pas de complétions : l'éditeur reste une zone de texte ordinaire */
      });
    return () => {
      abandonne = true;
    };
  }, []);

  // Repositionnement du curseur après acceptation d'une complétion : le texte
  // vient d'être remplacé par React, la position ne peut être posée qu'ensuite.
  // L'effet suit `contenu` et ne fait rien tant qu'aucune position n'attend.
  useEffect(() => {
    const position = caret.current;
    if (position === null) return;
    caret.current = null;
    zone.current?.focus();
    zone.current?.setSelectionRange(position, position);
  }, [contenu]);

  const fermerCompletions = () => {
    setCompletions([]);
    setChoisie(0);
  };

  /**
   * Les complétions se recalculent **à la frappe seulement**. Les rouvrir sur un
   * déplacement de curseur (flèches, clic) ferait surgir une liste que personne
   * n'a demandée, au milieu d'une relecture : une aide à l'écriture s'invite
   * quand on écrit.
   */
  const saisir = (valeur: string, position: number) => {
    setContenu(valeur);
    const trouvees = completionsPour(lexique, segmentCourant(valeur, position));
    setCompletions(trouvees);
    setChoisie(0);
  };

  const accepterCompletion = (completion: Completion) => {
    const position = zone.current?.selectionStart ?? contenu.length;
    const suite = accepter(contenu, position, completion);
    caret.current = suite.position;
    setContenu(suite.texte);
    fermerCompletions();
  };

  /**
   * Le clavier de la liste de complétions. `Entrée` n'y figure pas, à dessein :
   * dans une zone de texte elle insère un saut de ligne, et la voler à quelqu'un
   * qui rédige coûterait plus que l'aide n'apporte. `Tab` accepte — et seulement
   * tant que la liste est ouverte, `Échap` la refermant pour rendre la touche à
   * la navigation.
   */
  const clavier = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (completions.length === 0) return;
    if (e.key === "Tab") {
      e.preventDefault();
      accepterCompletion(completions[choisie]);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setChoisie((i) => (i + 1) % completions.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setChoisie((i) => (i - 1 + completions.length) % completions.length);
    } else if (e.key === "Escape") {
      e.preventDefault();
      fermerCompletions();
    }
  };

  /**
   * Demande une réécriture du brouillon. **N'écrit rien** : la réponse est un
   * candidat rendu en différentiel, que l'utilisateur applique à son brouillon
   * ou jette. Un échec laisse le texte exactement où il est.
   */
  const demanderRedaction = async () => {
    setRedactionEnCours(true);
    setErreurRedaction(null);
    try {
      setRedaction(await redigerPlaybook(agent, contenu, consigne.trim()));
    } catch (e) {
      setErreurRedaction(e instanceof Error ? e.message : String(e));
    } finally {
      setRedactionEnCours(false);
    }
  };

  /** Applique la réécriture **au brouillon**. La version en vigueur ne bouge pas. */
  const appliquerRedaction = () => {
    if (redaction === null) return;
    setContenu(redaction.contenu);
    setRedaction(null);
    fermerCompletions();
  };

  const fermerAssistant = () => {
    setAssistant(false);
    setRedaction(null);
    setErreurRedaction(null);
  };

  // Publication, restauration et application d'une proposition partagent la même
  // mécanique : l'action, puis rechargement (fiche + historique + propositions)
  // et resynchronisation de l'éditeur sur la nouvelle version courante.
  // `resynchroniser: false` pour le rejet, qui ne change pas la version courante :
  // l'éditeur garde alors les modifications en cours de l'utilisateur.
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
      await onPublication?.();
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setEnCours(false);
    }
  };

  if (chargement) {
    return <p className="text-sm text-neutral-500">Chargement du playbook…</p>;
  }
  if (fiche === null) {
    return (
      <p className="text-sm text-rose-600 dark:text-rose-400" role="alert">
        Playbook illisible : {erreur}
      </p>
    );
  }

  const modifie = contenu !== fiche.contenu;
  const jamaisEdite = fiche.source === PLAYBOOK_SOURCE_DEFAUT;

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
            <span className="chiffre text-annexe text-neutral-500 dark:text-neutral-400">
              {jamaisEdite
                ? "version du code (jamais édité)"
                : `version ${fiche.version}` +
                  (fiche.cree_le ? ` · ${formatDateHeure(fiche.cree_le)}` : "")}
            </span>
          }
        />
        <textarea
          ref={zone}
          value={contenu}
          onChange={(e) => saisir(e.target.value, e.target.selectionStart)}
          onKeyDown={clavier}
          onBlur={fermerCompletions}
          disabled={enCours}
          aria-label="Contenu du playbook"
          spellCheck={false}
          role="combobox"
          aria-expanded={completions.length > 0}
          aria-controls={idListe}
          aria-autocomplete="list"
          {...(completions.length > 0 && {
            "aria-activedescendant": `${idListe}-${choisie}`,
          })}
          className="h-96 w-full resize-y rounded-md border border-neutral-200 bg-white p-3 font-mono text-xs leading-relaxed shadow-sm focus:border-neutral-400 focus:outline-none disabled:opacity-50 dark:border-neutral-800 dark:bg-neutral-900 dark:focus:border-neutral-600"
        />
        {completions.length > 0 && (
          <ListeCompletions
            id={idListe}
            completions={completions}
            choisie={choisie}
            surChoix={accepterCompletion}
          />
        )}
        <div className="mt-2 flex flex-wrap items-center gap-3">
          <Bouton
            disabled={!modifie || !contenu.trim()}
            occupe={enCours}
            onClick={() => void executer(() => ecrirePlaybook(agent, contenu))}
          >
            {enCours
              ? "Envoi…"
              : `Publier la version ${fiche.version + 1}`}
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
          <Bouton
            variante="contour"
            ton="neutre"
            icone={IconeAssistant}
            disabled={enCours}
            aria-expanded={assistant}
            onClick={() => (assistant ? fermerAssistant() : setAssistant(true))}
          >
            Assistant
          </Bouton>
          <span className="text-xs text-neutral-500 dark:text-neutral-400">
            {modifie
              ? "Modifications non publiées."
              : "Une publication crée une nouvelle version ; les moteurs construits ensuite la chargent."}
          </span>
        </div>
        {assistant && (
          <PanneauAssistant
            consigne={consigne}
            setConsigne={setConsigne}
            brouillonVide={!contenu.trim()}
            enCours={redactionEnCours}
            redaction={redaction}
            avant={contenu}
            erreur={erreurRedaction}
            demander={() => void demanderRedaction()}
            appliquer={appliquerRedaction}
            fermer={fermerAssistant}
          />
        )}
        {erreur && (
          <p className="mt-2 text-xs text-rose-600 dark:text-rose-400" role="alert">
            {erreur}
          </p>
        )}
      </section>

      <section aria-label={`Historique du playbook de ${agent}`}>
        <EnTeteSection
          niveau={3}
          icone={IconeHistorique}
          className="mb-2"
          titre={
            <>
              Historique
              <BadgeEtat className="chiffre">{versions.length}</BadgeEtat>
              {propositions.length > 0 && (
                <BadgeEtat ton="accent" className="chiffre normal-case">
                  {propositions.length} en attente
                </BadgeEtat>
              )}
            </>
          }
        />
        {versions.length === 0 && (
          <p className="mb-1 text-sm text-neutral-500">
            Aucune version publiée : l&apos;agent suit encore le playbook du code.
          </p>
        )}
        {(propositions.length > 0 || versions.length > 0) && (
          <ul className="flex flex-col gap-1">
            {/* Les propositions d'abord : elles attendent une décision. */}
            {[...propositions].reverse().map((proposition) => (
              <LigneProposition
                key={`p${proposition.version}`}
                agent={agent}
                proposition={proposition}
                enCours={enCours}
                appliquer={(numero) =>
                  void executer(() => appliquerPropositionPlaybook(agent, numero))
                }
                rejeter={(numero) =>
                  void executer(
                    () => rejeterPropositionPlaybook(agent, numero),
                    { resynchroniser: false },
                  )
                }
              />
            ))}
            {[...versions].reverse().map((version) => (
              <LigneVersion
                key={version.version}
                agent={agent}
                version={version}
                courante={version.version === fiche.version}
                enCours={enCours}
                restaurer={(numero) =>
                  void executer(() => restaurerPlaybook(agent, numero))
                }
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

/**
 * Les complétions ouvertes sous la zone d'édition (#261).
 *
 * `onMouseDown` plutôt que `onClick` : le `blur` de la zone d'édition referme la
 * liste, et il part **avant** le clic — brancher le choix sur `onClick`
 * donnerait une liste qui se dérobe sous le curseur.
 */
function ListeCompletions({
  id,
  completions,
  choisie,
  surChoix,
}: {
  id: string;
  completions: Completion[];
  choisie: number;
  surChoix: (completion: Completion) => void;
}) {
  return (
    <>
      <ul
        id={id}
        role="listbox"
        aria-label="Complétions proposées"
        className="mt-1 flex flex-col overflow-hidden rounded-md border border-bord bg-surface shadow-sm"
      >
        {completions.map((completion, i) => (
          <li
            key={completion.texte}
            id={`${id}-${i}`}
            role="option"
            aria-selected={i === choisie}
            onMouseDown={(e) => {
              e.preventDefault();
              surChoix(completion);
            }}
            className={
              `flex ${CIBLE_MINIMALE} cursor-pointer items-center gap-2 px-3 py-1 ` +
              "text-annexe " +
              (i === choisie ? "bg-accent/10 text-texte" : "text-texte-secondaire")
            }
          >
            <span className="truncate font-mono">{completion.texte}</span>
            <span className="ml-auto shrink-0 text-texte-secondaire">
              {completion.famille === "structure" ? "structure" : "tournure"} ·{" "}
              {completion.roles} playbooks
            </span>
          </li>
        ))}
      </ul>
      <p className="mt-1 text-annexe text-texte-secondaire" role="status">
        {completions.length} proposition{completions.length > 1 ? "s" : ""} —
        Tab pour accepter, Échap pour ignorer.
      </p>
    </>
  );
}

/**
 * L'assistant de rédaction (#261) : une consigne libre, une réécriture, et son
 * **différentiel** avant toute application.
 *
 * Bloc d'arbitrage au sens de la règle des trois places (docs/30 §4) — il
 * n'existe que tant qu'il y a quelque chose à trancher, et disparaît dès qu'on
 * ferme l'assistant. Rendu dans la section « Playbook », jamais comme une
 * `<section>` de plus.
 */
function PanneauAssistant({
  consigne,
  setConsigne,
  brouillonVide,
  enCours,
  redaction,
  avant,
  erreur,
  demander,
  appliquer,
  fermer,
}: {
  consigne: string;
  setConsigne: (valeur: string) => void;
  brouillonVide: boolean;
  enCours: boolean;
  redaction: RedactionPlaybook | null;
  /** Le brouillon courant — la gauche du différentiel. */
  avant: string;
  erreur: string | null;
  demander: () => void;
  appliquer: () => void;
  fermer: () => void;
}) {
  const idConsigne = useId();
  return (
    <div
      className={classesCarte({ ton: "creuse", densite: "compacte", className: "mt-3" })}
      role="group"
      aria-label="Assistant de rédaction"
    >
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <label
            htmlFor={idConsigne}
            className="mb-1 block text-annexe text-texte-secondaire"
          >
            Ce que l&apos;assistant doit faire du brouillon (facultatif)
          </label>
          <input
            id={idConsigne}
            value={consigne}
            onChange={(e) => setConsigne(e.target.value)}
            onKeyDown={(e) => {
              if (e.key !== "Enter" || enCours || brouillonVide) return;
              e.preventDefault();
              demander();
            }}
            placeholder="resserre les garde-fous, ajoute une section Méthode…"
            className={CLASSE_CONTROLE}
          />
        </div>
        <Bouton
          icone={IconeAssistant}
          occupe={enCours}
          disabled={brouillonVide}
          onClick={demander}
        >
          {enCours
            ? "Rédaction…"
            : redaction
              ? "Proposer à nouveau"
              : "Proposer une réécriture"}
        </Bouton>
        <Bouton variante="discret" ton="neutre" onClick={fermer}>
          Fermer
        </Bouton>
      </div>
      {brouillonVide && (
        <p className="mt-2 text-annexe text-texte-secondaire">
          Le brouillon est vide : écrivez-en une première ligne, l&apos;assistant
          part de ce que vous avez déjà.
        </p>
      )}
      {erreur && (
        <p className="mt-2 text-annexe text-rose-600 dark:text-rose-400" role="alert">
          {erreur} — votre brouillon est intact, vous pouvez réessayer.
        </p>
      )}
      {redaction && (
        <Differentiel
          avant={avant}
          apres={redaction.contenu}
          justification={redaction.justification}
          appliquer={appliquer}
          ignorer={fermer}
        />
      )}
    </div>
  );
}

/**
 * Ce que la réécriture changerait au brouillon, ligne à ligne (#261).
 *
 * Le modèle rend un document **intégral** — c'est le contrat de l'API, partagé
 * avec les propositions d'après-run —, donc la comparaison se fait ici. Les
 * longues plages inchangées sont repliées (`condenser`) : un playbook réécrit
 * garde l'essentiel de son texte, et les montrer toutes noierait les cinq lignes
 * qui changent.
 */
function Differentiel({
  avant,
  apres,
  justification,
  appliquer,
  ignorer,
}: {
  avant: string;
  apres: string;
  justification: string;
  appliquer: () => void;
  ignorer: () => void;
}) {
  const lignes = differencier(avant, apres);
  const { ajouts, retraits } = compter(lignes);
  const entrees = condenser(lignes);
  const inchange = ajouts === 0 && retraits === 0;
  return (
    <div className="mt-3">
      <p className="text-annexe text-texte-secondaire">{justification}</p>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <BadgeEtat ton={inchange ? "neutre" : "positif"} className="chiffre">
          {inchange ? "aucun changement" : `+${ajouts} / −${retraits} lignes`}
        </BadgeEtat>
        <span className="text-annexe text-texte-secondaire">
          Différentiel avec votre brouillon — rien n&apos;est encore appliqué.
        </span>
      </div>
      <div
        role="region"
        aria-label="Différentiel de la réécriture proposée"
        className="mt-2 max-h-80 overflow-auto rounded-md border border-bord bg-surface font-mono text-annexe"
      >
        {entrees.map((entree, i) =>
          entree.type === "repli" ? (
            <p
              key={`r${i}`}
              className="bg-surface-creuse px-2 py-0.5 text-center text-texte-secondaire"
            >
              ⋯ {entree.lignes} lignes inchangées
            </p>
          ) : (
            <p
              key={`l${i}`}
              className={
                "whitespace-pre-wrap px-2 py-0.5 " +
                (entree.type === "ajout"
                  ? "bg-emerald-50 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200"
                  : entree.type === "retrait"
                    ? "bg-rose-50 text-rose-900 dark:bg-rose-950 dark:text-rose-200"
                    : "text-texte-secondaire")
              }
            >
              <span aria-hidden className="mr-2 select-none opacity-60">
                {entree.type === "ajout" ? "+" : entree.type === "retrait" ? "−" : " "}
              </span>
              {entree.texte || " "}
            </p>
          ),
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Bouton disabled={inchange} onClick={appliquer}>
          Appliquer au brouillon
        </Bouton>
        <Bouton variante="contour" ton="neutre" onClick={ignorer}>
          Ignorer
        </Bouton>
        <span className="text-annexe text-texte-secondaire">
          Appliquer ne publie rien : le texte part dans la zone d&apos;édition, et
          la publication reste un geste à part.
        </span>
      </div>
    </div>
  );
}

/**
 * Une proposition en attente : visuellement distincte des versions (cadre violet,
 * étiquette de provenance, justification en clair), et tranchée au clic —
 * appliquer (elle devient la version courante) ou rejeter (elle disparaît).
 */
function LigneProposition({
  agent,
  proposition,
  enCours,
  appliquer,
  rejeter,
}: {
  agent: string;
  proposition: PropositionPlaybook;
  enCours: boolean;
  appliquer: (numero: number) => void;
  rejeter: (numero: number) => void;
}) {
  const [contenu, setContenu] = useState<string | null>(null);
  const [ouverte, setOuverte] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Comme pour une version, le contenu candidat se charge à la première
  // consultation (la liste des propositions ne porte que les métadonnées).
  const basculer = async () => {
    if (!ouverte && contenu === null) {
      try {
        setContenu(
          (await chargerPropositionPlaybook(agent, proposition.version)).contenu,
        );
      } catch (e) {
        setErreur(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setErreur(null);
    setOuverte(!ouverte);
  };

  return (
    <li className="rounded-md border border-violet-300 bg-violet-50 px-3 py-2 text-sm shadow-sm dark:border-violet-900 dark:bg-violet-950">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-xs font-medium">
          p{proposition.version}
        </span>
        <span className="rounded-full bg-violet-200 px-2 text-xs text-violet-900 dark:bg-violet-900 dark:text-violet-200">
          {proposition.provenance}
        </span>
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {formatDateHeure(proposition.cree_le)}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => void basculer()}
            className="rounded-md border border-violet-300 px-2 py-1 text-xs text-violet-800 hover:bg-violet-100 dark:border-violet-800 dark:text-violet-300 dark:hover:bg-violet-900"
          >
            {ouverte ? "Masquer" : "Voir"}
          </button>
          <Bouton
            taille="petite"
            disabled={enCours}
            onClick={() => appliquer(proposition.version)}
          >
            Appliquer
          </Bouton>
          <Bouton
            variante="contour"
            ton="alerte"
            taille="petite"
            disabled={enCours}
            onClick={() => rejeter(proposition.version)}
          >
            Rejeter
          </Bouton>
        </div>
      </div>
      {proposition.justification && (
        <p className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap text-xs text-violet-900 dark:text-violet-200">
          {proposition.justification}
        </p>
      )}
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
      {ouverte && contenu !== null && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-white p-2 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {contenu}
        </pre>
      )}
    </li>
  );
}

function LigneVersion({
  agent,
  version,
  courante,
  enCours,
  restaurer,
}: {
  agent: string;
  version: VersionPlaybook;
  /** La version courante ne se restaure pas : elle est déjà en vigueur. */
  courante: boolean;
  enCours: boolean;
  restaurer: (version: number) => void;
}) {
  const [contenu, setContenu] = useState<string | null>(null);
  const [ouverte, setOuverte] = useState(false);
  const [erreur, setErreur] = useState<string | null>(null);

  // Le contenu d'une version passée se charge à la première consultation
  // (l'historique REST ne porte que les métadonnées).
  const basculer = async () => {
    if (!ouverte && contenu === null) {
      try {
        setContenu((await chargerVersionPlaybook(agent, version.version)).contenu);
      } catch (e) {
        setErreur(e instanceof Error ? e.message : String(e));
        return;
      }
    }
    setErreur(null);
    setOuverte(!ouverte);
  };

  return (
    <li className="rounded-md border border-neutral-200 bg-white px-3 py-2 text-sm shadow-sm dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-mono text-xs font-medium">v{version.version}</span>
        {courante && (
          <span className="rounded-full bg-emerald-100 px-2 text-xs text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300">
            courante
          </span>
        )}
        <span className="text-xs text-neutral-500 dark:text-neutral-400">
          {formatDateHeure(version.cree_le)}
        </span>
        <div className="ml-auto flex gap-2">
          <button
            type="button"
            onClick={() => void basculer()}
            className="rounded-md border border-neutral-300 px-2 py-1 text-xs text-neutral-600 hover:bg-neutral-50 dark:border-neutral-700 dark:text-neutral-400 dark:hover:bg-neutral-800"
          >
            {ouverte ? "Masquer" : "Voir"}
          </button>
          {!courante && (
            <button
              type="button"
              disabled={enCours}
              onClick={() => restaurer(version.version)}
              className="rounded-md border border-amber-300 px-2 py-1 text-xs font-medium text-amber-700 hover:bg-amber-50 disabled:opacity-50 dark:border-amber-800 dark:text-amber-400 dark:hover:bg-amber-950"
            >
              Restaurer
            </button>
          )}
        </div>
      </div>
      {erreur && (
        <p className="mt-1 text-xs text-rose-600 dark:text-rose-400">{erreur}</p>
      )}
      {ouverte && contenu !== null && (
        <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap rounded-md bg-neutral-50 p-2 font-mono text-xs text-neutral-700 dark:bg-neutral-950 dark:text-neutral-300">
          {contenu}
        </pre>
      )}
    </li>
  );
}
