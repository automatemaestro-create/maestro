"use client";

/**
 * Le catalogue du socle (#984, lot 4 de #973) — chaque primitive exportée de
 * `components/Primitives`, dans ses variantes et dans les deux thèmes.
 *
 * ── Pourquoi cette page existe ───────────────────────────────────────────────
 *
 * Le vocabulaire du socle ne se lisait que dans le code. La personne qui écrit
 * un « rendu attendu » (#976) n'avait rien à montrer du doigt ; la session qui
 * construit recopiait l'écran voisin plutôt que la primitive (docs/30 §2.2 :
 * 18 recopies de carte, 26 boutons refaits) ; et la relecture (#980) n'avait
 * aucune référence de ce que « dans le socle » veut dire. Cette page est cette
 * référence : elle ne documente rien, elle **rend** — ce qu'on y voit est ce
 * que le produit rend, puisque c'est le même code.
 *
 * ── Ce qu'elle n'est pas ─────────────────────────────────────────────────────
 *
 * Ce n'est **pas un écran du produit**, et quatre garde-fous le savent, chacun
 * pour sa raison — nommée, jamais par un motif qui l'ignorerait en silence :
 *
 * - **Elle n'est pas servie en production.**
 *   `REDIRECTION_SOCLE_HORS_DEVELOPPEMENT` (`apps/web/next.config.ts`) renvoie
 *   `/socle` vers `/` dès que `NODE_ENV` vaut `production`. Une redirection est
 *   évaluée **avant le routage** : elle l'emporte sur tout ce que cette page
 *   pourrait faire, et le gardien vit à un seul endroit.
 * - **Elle n'est pas au menu**, donc elle n'entre ni dans la **sobriété**
 *   (`tests/sobriete.test.tsx`) ni dans l'**a11y des dix écrans**
 *   (`tests/a11y.test.tsx`) : leurs tables sont dérivées de `MENU`
 *   (`tests/ecrans.tsx`), et elle n'y est pas. Ce n'est pas une exemption —
 *   c'est la conséquence de ne pas être un écran. La règle des trois places
 *   (docs/30 §4) ne s'y applique donc pas : un catalogue **est** une liste, et
 *   la borner à trois blocs reviendrait à ne pas le rendre.
 * - **Elle n'est pas dans `lib/navigation`**, donc la frontière écrans ↔
 *   `navigation.ts` de `tests/test_retex_utilisateur.py` ne la voit pas. Le
 *   prix est visible et assumé : la barre supérieure titre « Control Tower »,
 *   faute d'entrée à elle. Le titre de la page est donc écrit ici.
 * - **Elle est écartée des deux dérivations de routes**, avec sa raison :
 *   `GL_SURFACE_ROUTES` via `HORS_PRODUIT` de `tests/test_design_veille.py`
 *   (personne ne demandera une veille de conception sur un catalogue : il ne
 *   décide de rien, il montre), et `scripts/presentation/ecrans-touches.sh`
 *   (une présentation de jalon promettrait une capture que la stack de
 *   production ne peut pas prendre).
 *
 * ── Ce qu'elle s'interdit ────────────────────────────────────────────────────
 *
 * **Rien de neuf.** Aucune couleur hors des tokens, aucun pas hors de
 * l'échelle, aucune primitive refaite à la main : une page qui inventerait pour
 * se montrer elle-même mentirait sur ce qu'elle montre. Les balayages qui
 * lisent tout `app/` + `components/` la jugent comme le reste — les **quatre
 * sondes du socle** (`couleurs.test.ts`, `typographie.test.ts`,
 * `rayons-ombres.test.ts`, `espacements.test.ts`), la garde de mouvement et les
 * contrôles de saisie d'`a11y.test.tsx` —, et c'est voulu. Elle n'est dans le
 * résidu d'aucune des quatre.
 *
 * ── Ce qui l'empêche de prendre du retard (#975) ─────────────────────────────
 *
 * `tests/catalogue-socle.test.tsx` **dérive** la liste des primitives du module
 * (`Object.keys(Primitives)`) au lieu de la recopier : un export ajouté à
 * `Primitives.tsx` sans entrée ici fait rougir. Il garde aussi la mise en regard
 * — les deux thèmes, **les mêmes spécimens des deux côtés**, chaque scène nommée
 * — et les quatre garde-fous du « ce n'est pas un écran » ci-dessus.
 *
 * ── Les deux thèmes, sans une ligne de CSS ───────────────────────────────────
 *
 * `globals.css` déclare ses tokens sur `[data-theme="clair"]` et
 * `[data-theme="sombre"]` — des sélecteurs d'**attribut**, pas de racine — et
 * le variant `dark:` matche `[data-theme="sombre"] *`. Poser `data-theme` sur
 * un `<div>` rend donc tout son sous-arbre dans ce thème-là, tokens **et**
 * `dark:` hérités des primitives d'origine. C'est ce qui rend la mise en regard
 * possible sans un octet de style en plus, et c'est la seule raison pour
 * laquelle cette page peut tenir sa promesse.
 *
 * ⚠ Un panneau ainsi bascule ne peint pas son fond tout seul : `--background`
 * n'est consommé que par la règle `body`. Chaque scène porte donc son
 * `bg-surface-creuse` explicite.
 *
 * ── Partis pris (veille du 2026-09-20, consignée sur #984) ───────────────────
 *
 * 1. Une primitive = une section ; un axe de variante = une rangée (Radix
 *    Themes, Primer) — jamais le produit cartésien des axes.
 * 2. Le nom **technique** de la variante est écrit sous le spécimen (Atlassian,
 *    Primer) : le catalogue sert à montrer du doigt.
 * 3. Le spécimen est posé sur une scène encadrée (Primer, Radix) — sans quoi
 *    une `Carte ton="pleine"` blanche sur fond blanc n'a plus de contour.
 * 4. Un sommaire ancré en tête (Primer « ON THIS PAGE », Radix « Quick nav »).
 *
 * Aucune des trois références ne rend deux thèmes à la fois : toutes basculent.
 * La mise en regard clair / sombre est donc ce que cette page invente.
 */

import { useState, type ReactNode } from "react";

import {
  IconeAgents,
  IconeAlerte,
  IconeCouts,
  IconeRuns,
  IconeStatutEchec,
  IconeStatutEnCours,
  IconeStatutTerminee,
  IconeValidations,
} from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  BoutonLien,
  CIBLE_MINIMALE,
  CLASSE_CONTROLE,
  Carte,
  Champ,
  ChampListe,
  ChampTexte,
  EnTeteSection,
  EtatVide,
  LienRenvoi,
  ListeFiltre,
  TuileChiffre,
  classesCarte,
} from "@/components/Primitives";

/* ------------------------------------------------------------------ *
 * Le modèle : une brique, ses axes, ses spécimens
 * ------------------------------------------------------------------ */

/**
 * Un spécimen : ce qu'on rend, et le **nom qu'on ira écrire dans le code**.
 *
 * `rendu` prend une clé parce que la page rend chaque spécimen **deux fois**
 * (un thème chacun) : sans elle, les identifiants de `Champ` — obligatoires, et
 * porteurs de l'`aria-describedby` — seraient dupliqués dans le document.
 */
type Specimen = { code: string; rendu: (cle: string) => ReactNode };

/** Un axe de variation : **un seul** paramètre bouge d'un spécimen à l'autre. */
type Axe = { nom: string; note?: string; specimens: Specimen[] };

/**
 * Une brique du socle : son nom, ce à quoi elle sert, ses axes.
 *
 * `fondation` distingue ce qui n'est **pas** une primitive — l'échelle
 * typographique, la palette : des suites de valeurs qui se lisent comme une
 * **progression**, donc en pleine largeur (voir `BlocVu`). C'est une propriété
 * du bloc et non une liste d'ancres tenue ailleurs : une fondation de plus
 * hérite du bon rendu du seul fait de se déclarer telle.
 */
type Bloc = {
  ancre: string;
  nom: string;
  role: string;
  fondation?: boolean;
  axes: Axe[];
};

/** Une liste de filtre est contrôlée : elle porte son état, pas le catalogue. */
function ListeFiltreVue({ cle }: { cle: string }) {
  const [valeur, setValeur] = useState("");
  return (
    <ListeFiltre
      id={`${cle}-filtre`}
      libelle="Agent"
      tout="Tous les agents"
      options={[
        { valeur: "dev", libelle: "dev" },
        { valeur: "qa", libelle: "qa" },
      ]}
      valeur={valeur}
      surChoix={setValeur}
    />
  );
}

/**
 * Un aplat de couleur et le token qui le porte — la forme d'Atlassian.
 *
 * Son rayon et sa paire de padding viennent du **barème** (`rounded-controle`,
 * `px-3 py-1.5` — #982, #983) : un catalogue du socle qui s'écrirait hors du
 * barème pour se montrer lui-même mentirait sur ce qu'il montre.
 */
function Aplat({ classe, sur }: { classe: string; sur?: string }) {
  return (
    <span
      className={`inline-flex min-w-16 items-center justify-center rounded-controle border border-bord px-3 py-1.5 text-annexe ${classe}`}
    >
      {sur ?? " "}
    </span>
  );
}

const CATALOGUE: Bloc[] = [
  {
    ancre: "echelle",
    nom: "Échelle typographique",
    fondation: true,
    role:
      "Cinq pas de texte nommés par leur rôle, plus un pas d'affichage réservé à la valeur d'une tuile de tête.",
    axes: [
      {
        nom: "les cinq pas de texte",
        note: "Du second plan au titre d'écran — jamais une taille écrite à la main.",
        specimens: [
          { code: "text-micro", rendu: () => <p className="text-micro">Horodatage, exposant</p> },
          { code: "text-annexe", rendu: () => <p className="text-annexe">Détail, aide, pastille</p> },
          { code: "text-corps", rendu: () => <p className="text-corps">Le texte courant</p> },
          { code: "text-titre", rendu: () => <p className="text-titre">Titre d&apos;une carte</p> },
          { code: "text-page", rendu: () => <p className="text-page">Titre d&apos;un écran</p> },
        ],
      },
      {
        nom: "hors échelle de texte",
        note: "Le pas d'affichage d'une tuile de tête, et lui seul.",
        specimens: [
          {
            code: "text-chiffre",
            rendu: () => <p className="chiffre text-chiffre font-semibold">12,40</p>,
          },
        ],
      },
    ],
  },
  {
    ancre: "palette",
    nom: "Palette sémantique",
    fondation: true,
    role:
      "Un rôle, pas une couleur. Les deux thèmes viennent avec le token : rien ici ne porte de variante sombre écrite à la main.",
    axes: [
      {
        nom: "les surfaces et les bords",
        specimens: [
          { code: "bg-surface", rendu: () => <Aplat classe="bg-surface" /> },
          { code: "bg-surface-creuse", rendu: () => <Aplat classe="bg-surface-creuse" /> },
          { code: "bg-survol", rendu: () => <Aplat classe="bg-survol" /> },
          { code: "bg-selectionne", rendu: () => <Aplat classe="bg-selectionne" /> },
          { code: "border-bord", rendu: () => <Aplat classe="border-bord bg-surface" /> },
          { code: "border-bord-fort", rendu: () => <Aplat classe="border-bord-fort bg-surface" /> },
        ],
      },
      {
        nom: "le texte",
        specimens: [
          { code: "text-texte", rendu: () => <p className="text-corps text-texte">Le premier plan</p> },
          {
            code: "text-texte-secondaire",
            rendu: () => <p className="text-corps text-texte-secondaire">Le second plan</p>,
          },
        ],
      },
      {
        nom: "les tons pleins",
        note: "Ce qui s'y écrit est `sur-ton`, un seul token pour les cinq — une propriété vérifiée de la palette.",
        specimens: [
          { code: "bg-accent", rendu: () => <Aplat classe="bg-accent text-sur-ton" sur="accent" /> },
          { code: "bg-info", rendu: () => <Aplat classe="bg-info text-sur-ton" sur="info" /> },
          { code: "bg-positif", rendu: () => <Aplat classe="bg-positif text-sur-ton" sur="positif" /> },
          {
            code: "bg-attention",
            rendu: () => <Aplat classe="bg-attention text-sur-ton" sur="attention" />,
          },
          { code: "bg-alerte", rendu: () => <Aplat classe="bg-alerte text-sur-ton" sur="alerte" /> },
          {
            code: "bg-provenance",
            rendu: () => <Aplat classe="bg-provenance" sur=" " />,
          },
        ],
      },
      {
        nom: "les tons écrits",
        specimens: [
          { code: "text-accent-texte", rendu: () => <p className="text-corps text-accent-texte">accent</p> },
          { code: "text-info-texte", rendu: () => <p className="text-corps text-info-texte">info</p> },
          { code: "text-positif-texte", rendu: () => <p className="text-corps text-positif-texte">positif</p> },
          {
            code: "text-attention-texte",
            rendu: () => <p className="text-corps text-attention-texte">attention</p>,
          },
          { code: "text-alerte-texte", rendu: () => <p className="text-corps text-alerte-texte">alerte</p> },
          {
            code: "text-provenance-texte",
            rendu: () => <p className="text-corps text-provenance-texte">provenance</p>,
          },
        ],
      },
      {
        nom: "les fonds creux",
        specimens: [
          { code: "bg-accent-creux", rendu: () => <Aplat classe="bg-accent-creux text-accent-texte" sur="accent" /> },
          { code: "bg-info-creux", rendu: () => <Aplat classe="bg-info-creux text-info-texte" sur="info" /> },
          {
            code: "bg-positif-creux",
            rendu: () => <Aplat classe="bg-positif-creux text-positif-texte" sur="positif" />,
          },
          {
            code: "bg-attention-creux",
            rendu: () => <Aplat classe="bg-attention-creux text-attention-texte" sur="attention" />,
          },
          { code: "bg-alerte-creux", rendu: () => <Aplat classe="bg-alerte-creux text-alerte-texte" sur="alerte" /> },
          {
            code: "bg-provenance-creux",
            rendu: () => <Aplat classe="bg-provenance-creux text-provenance-texte" sur="provenance" />,
          },
        ],
      },
      {
        nom: "les aplats survolés",
        note:
          "Quatre, et non six : `positif` est un état et `provenance` une origine — ni l'un ni l'autre ne se survole. Le trou dit quelque chose.",
        specimens: [
          { code: "bg-accent-appui", rendu: () => <Aplat classe="bg-accent-appui text-sur-ton" sur="accent" /> },
          { code: "bg-info-appui", rendu: () => <Aplat classe="bg-info-appui text-sur-ton" sur="info" /> },
          {
            code: "bg-attention-appui",
            rendu: () => <Aplat classe="bg-attention-appui text-sur-ton" sur="attention" />,
          },
          { code: "bg-alerte-appui", rendu: () => <Aplat classe="bg-alerte-appui text-sur-ton" sur="alerte" /> },
        ],
      },
    ],
  },
  {
    ancre: "carte",
    nom: "Carte",
    role: "La surface commune à tout ce qui se pose sur le fond de page.",
    axes: [
      {
        nom: "ton",
        specimens: [
          { code: 'ton="pleine"', rendu: () => <Carte>Le contenu</Carte> },
          { code: 'ton="creuse"', rendu: () => <Carte ton="creuse">Un contenant</Carte> },
          { code: 'ton="attention"', rendu: () => <Carte ton="attention">Un arbitrage</Carte> },
          {
            code: 'ton="attentionClaire"',
            rendu: () => <Carte ton="attentionClaire">Dans une zone d&apos;attention</Carte>,
          },
        ],
      },
      {
        nom: "densité",
        note: "Trois pas nommés couvrent tout le produit ; `aucune` encadre un contenu qui gère le sien.",
        specimens: [
          { code: 'densite="compacte"', rendu: () => <Carte densite="compacte">compacte</Carte> },
          { code: 'densite="normale"', rendu: () => <Carte densite="normale">normale</Carte> },
          { code: 'densite="aeree"', rendu: () => <Carte densite="aeree">aérée</Carte> },
          { code: 'densite="aucune"', rendu: () => <Carte densite="aucune">aucune</Carte> },
        ],
      },
      {
        nom: "classesCarte()",
        note: "La surface sans la balise : le seul recours de ce qui ne peut pas être une `Carte` (un `<Link>`, un `<button>`).",
        specimens: [
          {
            code: "classesCarte({ ton: 'creuse' })",
            rendu: () => (
              <button type="button" className={classesCarte({ ton: "creuse" })}>
                Un bouton qui est une carte
              </button>
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "bouton",
    nom: "Bouton",
    role: "L'action. La variante dit le rang de l'action, le ton dit sa nature — jamais son apparence.",
    axes: [
      {
        nom: "variante",
        specimens: [
          { code: 'variante="plein"', rendu: () => <Bouton>Lancer</Bouton> },
          { code: 'variante="contour"', rendu: () => <Bouton variante="contour">Annuler</Bouton> },
          { code: 'variante="discret"', rendu: () => <Bouton variante="discret">Replier</Bouton> },
        ],
      },
      {
        nom: "ton",
        specimens: [
          { code: 'ton="accent"', rendu: () => <Bouton ton="accent">accent</Bouton> },
          { code: 'ton="info"', rendu: () => <Bouton ton="info">info</Bouton> },
          { code: 'ton="attention"', rendu: () => <Bouton ton="attention">attention</Bouton> },
          { code: 'ton="alerte"', rendu: () => <Bouton ton="alerte">alerte</Bouton> },
          { code: 'ton="neutre"', rendu: () => <Bouton ton="neutre">neutre</Bouton> },
        ],
      },
      {
        nom: "taille",
        specimens: [
          { code: 'taille="petite"', rendu: () => <Bouton taille="petite">petite</Bouton> },
          { code: 'taille="normale"', rendu: () => <Bouton taille="normale">normale</Bouton> },
        ],
      },
      {
        nom: "état",
        note: "`occupe` n'est pas `disabled` : l'un dit qu'une action est en cours, l'autre qu'il n'y a rien à faire.",
        specimens: [
          { code: "icone", rendu: () => <Bouton icone={IconeRuns}>Avec icône</Bouton> },
          { code: "occupe", rendu: () => <Bouton occupe>En cours</Bouton> },
          { code: "disabled", rendu: () => <Bouton disabled>Indisponible</Bouton> },
        ],
      },
      {
        nom: "CIBLE_MINIMALE",
        note:
          "Le plancher de toute cible interactive — 24 px, WCAG 2.2 §2.5.8. `Bouton` le porte déjà ; ce qui n'en est pas un l'emprunte.",
        specimens: [
          {
            code: "CIBLE_MINIMALE",
            rendu: () => (
              <a
                href="#bouton"
                className={`inline-flex ${CIBLE_MINIMALE} items-center text-annexe font-medium text-accent-texte hover:underline`}
              >
                Un lien en petit corps, au plancher
              </a>
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "bouton-lien",
    nom: "BoutonLien",
    role: "Le même bouton quand l'action est une navigation : c'est un lien, il en a seulement l'allure.",
    axes: [
      {
        nom: "variante",
        specimens: [
          { code: 'variante="plein"', rendu: () => <BoutonLien href="/runs">Voir les runs</BoutonLien> },
          {
            code: 'variante="contour"',
            rendu: () => (
              <BoutonLien href="/runs" variante="contour" icone={IconeRuns}>
                Voir les runs
              </BoutonLien>
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "champ",
    nom: "Champ",
    role: "La saisie sur une ligne : libellé lié, aide et erreur annoncées avec le contrôle.",
    axes: [
      {
        nom: "état",
        specimens: [
          {
            code: "nu",
            rendu: (cle) => <Champ id={`${cle}-nu`} libelle="Nom du projet" defaultValue="Maestro" />,
          },
          {
            code: "aide",
            rendu: (cle) => (
              <Champ id={`${cle}-aide`} libelle="Dépôt" aide="Le clone local, pas l'URL distante." />
            ),
          },
          {
            code: "erreur",
            rendu: (cle) => (
              <Champ id={`${cle}-err`} libelle="Port" defaultValue="80" erreur="Port déjà pris." />
            ),
          },
          {
            code: "monospace",
            rendu: (cle) => (
              <Champ id={`${cle}-mono`} libelle="Chemin" monospace defaultValue="/maestro/depot" />
            ),
          },
          {
            code: "libelleMasque",
            rendu: (cle) => (
              <Champ id={`${cle}-masq`} libelle="Recherche" libelleMasque placeholder="Rechercher…" />
            ),
          },
          {
            code: "disabled",
            rendu: (cle) => <Champ id={`${cle}-off`} libelle="Jeton" disabled defaultValue="•••" />,
          },
        ],
      },
      {
        nom: "CLASSE_CONTROLE",
        note: "L'apparence d'un contrôle, pour ce qui ne passe pas par `Champ` — un cadre à deux étages, par exemple.",
        specimens: [
          {
            code: "CLASSE_CONTROLE",
            rendu: () => <div className={CLASSE_CONTROLE}>Un cadre qui porte la classe</div>,
          },
        ],
      },
    ],
  },
  {
    ancre: "champ-liste",
    nom: "ChampListe · ListeFiltre",
    role: "La liste déroulante, et sa forme de filtre — choix neutre en tête, désactivée quand il n'y a rien à proposer.",
    axes: [
      {
        nom: "ChampListe",
        specimens: [
          {
            code: "nu",
            rendu: (cle) => (
              <ChampListe id={`${cle}-liste`} libelle="Fournisseur" defaultValue="anthropic">
                <option value="anthropic">Anthropic</option>
                <option value="ollama">Ollama</option>
              </ChampListe>
            ),
          },
        ],
      },
      {
        nom: "ListeFiltre",
        specimens: [
          { code: "options", rendu: (cle) => <ListeFiltreVue cle={`${cle}-f1`} /> },
          {
            code: "sans option → désactivée",
            rendu: (cle) => (
              <ListeFiltre
                id={`${cle}-f2`}
                libelle="Tâche"
                tout="Toutes les tâches"
                options={[]}
                valeur=""
                surChoix={() => undefined}
              />
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "champ-texte",
    nom: "ChampTexte",
    role: "La saisie sur plusieurs lignes.",
    axes: [
      {
        nom: "état",
        specimens: [
          {
            code: "nu",
            rendu: (cle) => (
              <ChampTexte id={`${cle}-txt`} libelle="Objectif" rows={3} defaultValue="Rendre le socle visible." />
            ),
          },
          {
            code: "monospace",
            rendu: (cle) => (
              <ChampTexte id={`${cle}-txtm`} libelle="Consigne" monospace rows={3} defaultValue="allow: Bash(git:*)" />
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "tuile-chiffre",
    nom: "TuileChiffre",
    role: "Un chiffre de tête : la valeur, ce qu'elle compte, ce qu'elle recouvre, et où le détail se trouve.",
    axes: [
      {
        nom: "composition",
        note: "Le bandeau de tête d'un écran du produit en porte quatre au plus (docs/30 §4).",
        specimens: [
          { code: "nue", rendu: () => <TuileChiffre libelle="Tâches" valeur="12" /> },
          {
            code: "detail",
            rendu: () => <TuileChiffre libelle="Coût" valeur="12,40 $" detail="sur 24 h" icone={IconeCouts} />,
          },
          {
            code: "monospace",
            rendu: () => <TuileChiffre libelle="Run" valeur="20260920-0559" monospace />,
          },
          {
            code: "titre + renvoi",
            rendu: () => (
              <TuileChiffre
                libelle="Agents"
                valeur="3"
                titre="2 occupés · 1 libre"
                icone={IconeAgents}
                renvoi={{ href: "/agents", libelle: "Voir les agents" }}
              />
            ),
          },
        ],
      },
      {
        nom: "LienRenvoi",
        note: "Le renvoi seul — la flèche est une icône du jeu, jamais un « → » de texte.",
        specimens: [
          {
            code: "LienRenvoi",
            rendu: () => <LienRenvoi renvoi={{ href: "/couts", libelle: "Voir le grand livre" }} />,
          },
        ],
      },
    ],
  },
  {
    ancre: "en-tete-section",
    nom: "EnTeteSection",
    role: "Le titre d'une zone. `niveau` suit la hiérarchie du document, pas l'apparence.",
    axes: [
      {
        nom: "ton",
        specimens: [
          { code: 'ton="neutre"', rendu: () => <EnTeteSection titre="Activité" icone={IconeRuns} niveau={3} /> },
          {
            code: 'ton="attention"',
            rendu: () => (
              <EnTeteSection titre="À valider" ton="attention" icone={IconeValidations} niveau={3} />
            ),
          },
        ],
      },
      {
        nom: "aside",
        note: "Ce qui se pose à droite : un compte, un renvoi, un bouton.",
        specimens: [
          {
            code: "aside",
            rendu: () => (
              <EnTeteSection
                titre="Runs"
                niveau={3}
                icone={IconeRuns}
                aside={<BadgeEtat ton="info">3</BadgeEtat>}
              />
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "badge-etat",
    nom: "BadgeEtat",
    role:
      "La pastille d'état. Toujours du texte : la couleur appuie le sens, elle ne le porte jamais seule.",
    axes: [
      {
        nom: "ton (aplat)",
        specimens: [
          { code: 'ton="neutre"', rendu: () => <BadgeEtat>neutre</BadgeEtat> },
          { code: 'ton="info"', rendu: () => <BadgeEtat ton="info">info</BadgeEtat> },
          { code: 'ton="positif"', rendu: () => <BadgeEtat ton="positif">positif</BadgeEtat> },
          { code: 'ton="attention"', rendu: () => <BadgeEtat ton="attention">attention</BadgeEtat> },
          { code: 'ton="alerte"', rendu: () => <BadgeEtat ton="alerte">alerte</BadgeEtat> },
          { code: 'ton="provenance"', rendu: () => <BadgeEtat ton="provenance">provenance</BadgeEtat> },
        ],
      },
      {
        nom: "contour",
        specimens: [
          { code: "neutre", rendu: () => <BadgeEtat contour>neutre</BadgeEtat> },
          { code: "info", rendu: () => <BadgeEtat ton="info" contour>info</BadgeEtat> },
          { code: "positif", rendu: () => <BadgeEtat ton="positif" contour>positif</BadgeEtat> },
          { code: "attention", rendu: () => <BadgeEtat ton="attention" contour>attention</BadgeEtat> },
          { code: "alerte", rendu: () => <BadgeEtat ton="alerte" contour>alerte</BadgeEtat> },
          { code: "provenance", rendu: () => <BadgeEtat ton="provenance" contour>provenance</BadgeEtat> },
        ],
      },
      {
        nom: "signal",
        note: "Le glyphe l'emporte sur la pastille : la forme dit ce que la couleur seule ne dit pas (docs/30 §1.6).",
        specimens: [
          { code: "pastille", rendu: () => <BadgeEtat ton="info" pastille>en ligne</BadgeEtat> },
          {
            code: "pastille + pulse",
            rendu: () => (
              <BadgeEtat ton="attention" pastille pulse>
                en attente
              </BadgeEtat>
            ),
          },
          {
            code: "icone",
            rendu: () => (
              <BadgeEtat ton="positif" icone={IconeStatutTerminee}>
                terminé
              </BadgeEtat>
            ),
          },
          {
            code: "icone (en marche)",
            rendu: () => (
              <BadgeEtat ton="info" icone={IconeStatutEnCours} pulse>
                en cours
              </BadgeEtat>
            ),
          },
          {
            code: "icone (échec)",
            rendu: () => (
              <BadgeEtat ton="alerte" icone={IconeStatutEchec}>
                échec
              </BadgeEtat>
            ),
          },
        ],
      },
    ],
  },
  {
    ancre: "etat-vide",
    nom: "EtatVide",
    role:
      "Ce qui manque, et par où l'obtenir. Le bord en pointillés le distingue d'une carte : la place est réservée, pas remplie.",
    axes: [
      {
        nom: "composition",
        note: "Un état vide dit toujours pourquoi c'est vide — jamais un « aucune donnée » sec.",
        specimens: [
          { code: "message", rendu: () => <EtatVide message="Aucun run sur ce projet." /> },
          {
            code: "icone + releve",
            rendu: () => (
              <EtatVide
                message="Aucun agent déclaré."
                icone={IconeAgents}
                releve="En attendant : MAESTRO_AGENTS dans le .env."
              />
            ),
          },
          {
            code: "lien",
            rendu: () => (
              <EtatVide
                message="Aucune validation en attente."
                icone={IconeAlerte}
                lien={{ href: "/validations", libelle: "Voir l'historique" }}
              />
            ),
          },
          {
            code: "children",
            rendu: () => (
              <EtatVide message="Aucun projet déclaré." icone={IconeCouts}>
                <Bouton taille="petite">Déclarer un projet</Bouton>
              </EtatVide>
            ),
          },
        ],
      },
    ],
  },
];

/* ------------------------------------------------------------------ *
 * Le rendu
 * ------------------------------------------------------------------ */

/** Les deux thèmes, dans l'ordre où la page les rend. */
const THEMES = [
  { cle: "clair", libelle: "Thème clair" },
  { cle: "sombre", libelle: "Thème sombre" },
] as const;

/**
 * La scène : un panneau basculé dans un thème, qui peint son propre fond.
 * C'est le **seul** endroit de la page qui pose `data-theme`.
 *
 * `role="group"` et son nom : à l'écran, le thème d'un panneau se voit ; au
 * lecteur d'écran, deux rendus consécutifs du même axe seraient indiscernables.
 * Le nom est donc annoncé, et rien n'est écrit en plus dans la page.
 */
function Scene({
  theme,
  libelle,
  className = "",
  children,
}: {
  theme: string;
  libelle: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div
      data-theme={theme}
      role="group"
      aria-label={libelle}
      className={`rounded-carte border border-bord bg-surface-creuse p-3 text-texte ${className}`}
    >
      {children}
    </div>
  );
}

/** Un spécimen et son nom — parti pris 2 : le nom se lit avec le rendu. */
function SpecimenVu({ specimen, cle }: { specimen: Specimen; cle: string }) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-1">
      {specimen.rendu(cle)}
      <code className="text-micro text-texte-secondaire">{specimen.code}</code>
    </div>
  );
}

/** Une rangée d'axe : les spécimens d'un seul paramètre, sur une ligne. */
function AxeVu({ axe, cle }: { axe: Axe; cle: string }) {
  return (
    <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
      {axe.specimens.map((specimen, i) => (
        <SpecimenVu key={specimen.code} specimen={specimen} cle={`${cle}-${i}`} />
      ))}
    </div>
  );
}

/**
 * Une brique du socle : son nom, ce à quoi elle sert, et **chaque axe rendu
 * deux fois** — un thème par scène, les deux portant les mêmes libellés.
 *
 * C'est la variante A, choisie par le regard neuf sur pièces (commentaire
 * « ## Variante retenue » de #984). Ce qu'elle tient et que les deux autres
 * perdaient : le rendu sombre se **montre du doigt** autant que le clair, et
 * chaque spécimen sombre reste à l'aplomb de son homologue clair — les deux
 * scènes ont la même largeur, donc elles se replient au même endroit.
 *
 * ⚠ Les **fondations** (l'échelle, la palette) empilent leurs deux scènes sur
 * la pleine largeur au lieu de les mettre côte à côte, et c'est la réserve que
 * le regard neuf a nommée : à demi-largeur, l'échelle typographique casse après
 * `text-titre` et cesse de se lire comme une progression. Ce n'est pas la
 * variante C pour autant — les libellés restent des deux côtés, ce que C
 * abandonnait. La bascule est une **propriété du bloc**, `fondation`, et non
 * une liste d'ancres à tenir à jour.
 */
function BlocVu({ bloc }: { bloc: Bloc }) {
  const paire = bloc.fondation ? "grid gap-2" : "grid gap-2 lg:grid-cols-2";
  return (
    <section id={bloc.ancre} className="flex flex-col gap-3">
      <EnTeteSection titre={bloc.nom} niveau={3} />
      <p className="text-annexe text-texte-secondaire">{bloc.role}</p>
      {bloc.axes.map((axe) => (
        <div key={axe.nom} className="flex flex-col gap-2">
          <p className="text-annexe font-medium text-texte">{axe.nom}</p>
          {axe.note && <p className="text-micro text-texte-secondaire">{axe.note}</p>}
          <div className={paire}>
            {THEMES.map((theme) => (
              <Scene key={theme.cle} theme={theme.cle} libelle={theme.libelle}>
                <AxeVu axe={axe} cle={`${bloc.ancre}-${axe.nom}-${theme.cle}`} />
              </Scene>
            ))}
          </div>
        </div>
      ))}
    </section>
  );
}

export default function PageSocle() {
  return (
    // `p-4` et non un `sm:p-6` de plus : le padding d'un conteneur se prend
    // dans le barème (#983), et `aeree` en est le pas le plus large.
    <div className="flex flex-col gap-6 p-4">
      <header className="flex flex-col gap-2">
        <h2 className="text-page font-semibold tracking-tight">Catalogue du socle</h2>
        <p className="text-corps text-texte-secondaire">
          Chaque primitive de <code className="text-annexe">components/Primitives</code>{" "}
          dans ses variantes, avec l&apos;échelle typographique et la palette sémantique.{" "}
          <strong className="font-medium text-texte">
            Chaque rangée est rendue deux fois : thème clair, puis thème sombre.
          </strong>{" "}
          Page de développement : elle n&apos;est pas servie en production et n&apos;est pas au
          menu.
        </p>
        <nav aria-label="Les briques du socle" className="flex flex-wrap gap-x-3 gap-y-1">
          {CATALOGUE.map((bloc) => (
            <a
              key={bloc.ancre}
              href={`#${bloc.ancre}`}
              className={`inline-flex ${CIBLE_MINIMALE} items-center text-annexe font-medium text-accent-texte hover:underline`}
            >
              {bloc.nom}
            </a>
          ))}
        </nav>
      </header>
      {CATALOGUE.map((bloc) => (
        <BlocVu key={bloc.ancre} bloc={bloc} />
      ))}
    </div>
  );
}
