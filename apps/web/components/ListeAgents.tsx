"use client";

/**
 * La liste des agents (#190, lot 1 de #189) : le point d'entrée unique de
 * l'entrée de menu « Agents ». Chaque carte ouvre la fiche de l'agent, où ses
 * facettes tiennent en onglets — c'est ce qui remplace les trois sélecteurs
 * d'agent des anciennes pages Catalogue, Playbooks et Chat.
 *
 * `ongletCible` porte l'intention d'où l'on vient : une redirection depuis
 * `/playbooks` arrive ici avec `?onglet=playbook`, et les cartes visent alors
 * directement cet onglet — un signet sur l'ancienne page continue donc de
 * mener au bon endroit, sans détour par le profil.
 *
 * Depuis #254 la **création** n'est plus ici : elle a son écran
 * (`CHEMIN_CREATION_AGENT`), et cette page n'en garde que la porte — en tête,
 * avant les cartes.
 *
 * #258 lui donne ce qu'elle taisait. Une carte rendait le nom, le rôle et
 * l'origine, et rien d'autre : ni l'état de l'agent, ni sa charge, alors que
 * `GET /api/agents` les sert depuis #86 — « c'est la carte qui ne les lit pas ».
 * Trois changements, et ils se répondent :
 *
 * - **une icône par rôle** au lieu de la même sur chaque carte. `IconeAgent`
 *   répétait la seule chose que toutes les cartes ont en commun, ce qui est le
 *   reproche de la revue pris à la lettre (« les icônes sont répétitives ») ;
 * - **l'état sur la carte** — désactivé, occupé, libre —, porté par un glyphe
 *   autant que par une couleur (docs/30 §1.6), avec la charge en dessous quand
 *   elle a quelque chose à dire ;
 * - **un filtre et un tri**, dans une `<nav>` qui règle l'écran sans occuper
 *   l'une des trois places (docs/30 §4.1, même forme que la période de
 *   `/couts`). Elle n'apparaît qu'à partir de deux agents : à un seul, filtrer
 *   n'a pas d'objet, et une rangée de réglages au-dessus d'une carte unique
 *   serait le brouillon que ce ticket retire.
 *
 * ⚠ Le composant lit désormais **deux** sources : le catalogue par le REST
 * (`chargerCatalogue`, ce qu'un agent *est*) et le parc par le contexte du shell
 * (`useEtatGlobal().agents`, ce qu'il *fait* — temps réel, rechargé à chaque
 * événement). Il est donc à monter sous `FournisseurEtatGlobal`, ce que le shell
 * fait déjà pour toutes les pages.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { BanniereErreurApi } from "@/components/BanniereErreurApi";
import { IconeAgents, IconeCapacite, IconePlus } from "@/components/Icones";
import {
  BadgeEtat,
  Bouton,
  BoutonLien,
  Champ,
  ChampListe,
  classesCarte,
  EnTeteSection,
  type Icone,
} from "@/components/Primitives";
import { chargerCatalogue } from "@/lib/api";
import {
  CHEMIN_CREATION_AGENT,
  type CleOngletAgent,
  cheminOnglet,
  ONGLET_AGENT_DEFAUT,
  ONGLETS_AGENT,
} from "@/lib/agents";
import { useEtatGlobal } from "@/lib/etatGlobal";
import type { AgentCatalogue } from "@/lib/types";
import {
  type CleTriAgents,
  composerLignesAgents,
  ETATS_AGENT,
  FILTRES_VIDES,
  type FiltresAgents,
  filtresActifs,
  iconeDuRole,
  libelleOrigine,
  type LigneAgent,
  ORIGINES_AGENT,
  rolesPresents,
  TRI_AGENTS_DEFAUT,
  TRIS_AGENTS,
  vueDesAgents,
} from "@/lib/vueAgents";

/**
 * En deçà, aucun réglage n'est proposé : une liste d'un agent se lit d'un coup
 * d'œil, et le seul tri possible y est l'identité. Le ticket demande que la
 * liste tienne « au-delà d'une poignée d'agents » — c'est la barre qui doit
 * apparaître quand elle sert, pas un plafond au nombre d'agents.
 */
const MINIMUM_POUR_FILTRER = 2;

export function ListeAgents({
  ongletCible = ONGLET_AGENT_DEFAUT,
}: {
  ongletCible?: CleOngletAgent;
}) {
  const { agents } = useEtatGlobal();
  const [fiches, setFiches] = useState<AgentCatalogue[]>([]);
  const [chargement, setChargement] = useState(true);
  const [erreur, setErreur] = useState<string | null>(null);
  const [filtres, setFiltres] = useState<FiltresAgents>(FILTRES_VIDES);
  const [tri, setTri] = useState<CleTriAgents>(TRI_AGENTS_DEFAUT);

  const recharger = useCallback(async () => {
    try {
      setFiches(await chargerCatalogue());
      setErreur(null);
    } catch (e) {
      setErreur(e instanceof Error ? e.message : String(e));
    } finally {
      setChargement(false);
    }
  }, []);

  // Chargement initial différé d'un tick (même mécanique que useControlTower) :
  // l'effet lui-même ne déclenche aucun setState synchrone.
  useEffect(() => {
    const tick = setTimeout(() => void recharger(), 0);
    return () => clearTimeout(tick);
  }, [recharger]);

  // La jointure catalogue × parc se refait à chaque événement du bus (le parc
  // est temps réel) : elle est mémoïsée pour que le tri qui la suit ne reparte
  // pas d'un tableau neuf à chaque frappe dans la recherche.
  const lignes = useMemo(
    () => composerLignesAgents(fiches, agents),
    [fiches, agents],
  );
  const visibles = useMemo(
    () => vueDesAgents(lignes, filtres, tri),
    [lignes, filtres, tri],
  );

  const libelleCible = ONGLETS_AGENT.find(
    (onglet) => onglet.cle === ongletCible,
  );

  return (
    <>
      <BanniereErreurApi erreur={erreur} />
      <EnTeteSection
        titre="Agents"
        icone={IconeAgents}
        className="justify-start"
        aside={
          <span className="text-annexe text-neutral-500 dark:text-neutral-400">
            {ongletCible === ONGLET_AGENT_DEFAUT
              ? "Un agent, une fiche : profil, playbook, MCP & permissions, chat."
              : `Ouvre l'onglet « ${libelleCible?.libelle} » de l'agent choisi.`}
          </span>
        }
      />

      {/* Le geste d'abord, le catalogue ensuite (#254). Il était **sous** les
          cartes : plus il y avait d'agents, plus il fallait descendre pour en
          créer un. Il est aussi **hors** du chargement — créer un agent ne
          dépend pas de la lecture du catalogue, et un bouton qui apparaît après
          coup déplace ce qu'on s'apprêtait à cliquer. */}
      <div>
        <BoutonLien href={CHEMIN_CREATION_AGENT} icone={IconePlus}>
          Nouvel agent
        </BoutonLien>
      </div>

      {chargement ? (
        <p className="text-corps text-neutral-500">Chargement du catalogue…</p>
      ) : (
        <>
          {lignes.length >= MINIMUM_POUR_FILTRER && (
            <BarreDeReglages
              lignes={lignes}
              filtres={filtres}
              poserFiltres={setFiltres}
              tri={tri}
              poserTri={setTri}
              retenus={visibles.length}
            />
          )}
          <ul className="grid gap-3 @md:grid-cols-2 @3xl:grid-cols-3">
            {visibles.map((ligne) => (
              <li key={ligne.fiche.nom}>
                <CarteAgent ligne={ligne} onglet={ongletCible} />
              </li>
            ))}
            {lignes.length === 0 && (
              <li className="text-corps text-neutral-500">
                Aucun agent au catalogue — « Nouvel agent », en tête de page, en
                crée un.
              </li>
            )}
            {/* Deux vides, deux causes : un catalogue vide n'a rien à montrer,
                une liste filtrée en cache quelque chose. Les confondre ferait
                croire à un parc vide devant un filtre trop étroit. */}
            {lignes.length > 0 && visibles.length === 0 && (
              <li className="text-corps text-neutral-500">
                Aucun agent ne répond à ce filtre.
              </li>
            )}
          </ul>
        </>
      )}
    </>
  );
}

/**
 * Le réglage de la liste : ce qu'on cherche, puis les trois axes que le ticket
 * nomme (rôle, origine, état), puis le tri.
 *
 * C'est une `<nav>` et non une `<section>` : elle règle l'écran, elle n'occupe
 * pas l'une des trois places de docs/30 §4.1 — même statut que le filtre de
 * période de `/couts`, et la sonde de sobriété le reconnaît déjà comme tel.
 */
function BarreDeReglages({
  lignes,
  filtres,
  poserFiltres,
  tri,
  poserTri,
  retenus,
}: {
  lignes: LigneAgent[];
  filtres: FiltresAgents;
  poserFiltres: (filtres: FiltresAgents) => void;
  tri: CleTriAgents;
  poserTri: (tri: CleTriAgents) => void;
  retenus: number;
}) {
  const roles = rolesPresents(lignes);
  const actifs = filtresActifs(filtres);

  return (
    // Deux colonnes tant que la place manque, une rangée dès qu'il y en a. Un
    // simple `flex-wrap` empilait les cinq contrôles l'un sous l'autre sur un
    // écran étroit (mesuré au banc de mise en page, 390 px : ~340 px de
    // réglages avant la première carte) — la liste commençait sous le pli,
    // c'est-à-dire le contraire de ce que ce ticket vient chercher.
    <nav
      aria-label="Filtrer et trier les agents"
      className="grid grid-cols-2 gap-3 @4xl:flex @4xl:flex-wrap @4xl:items-end"
    >
      <Champ
        id="recherche-agent"
        libelle="Rechercher"
        type="search"
        className="col-span-2 @4xl:w-56 @4xl:flex-1"
        placeholder="Nom ou rôle"
        value={filtres.recherche}
        onChange={(e) => poserFiltres({ ...filtres, recherche: e.target.value })}
      />

      {/* Le rôle ne se propose qu'au-delà d'un seul : un choix unique n'est pas
          un choix, et un sélecteur qui ne peut rien changer se clique quand
          même. */}
      {roles.length > 1 && (
        <ChampListe
          id="filtre-role-agent"
          libelle="Rôle"
          className="min-w-0 @4xl:w-44"
          value={filtres.role}
          onChange={(e) => poserFiltres({ ...filtres, role: e.target.value })}
        >
          <option value="">Tous les rôles</option>
          {roles.map((role) => (
            <option key={role} value={role}>
              {role}
            </option>
          ))}
        </ChampListe>
      )}

      <ChampListe
        id="filtre-origine-agent"
        libelle="Origine"
        className="min-w-0 @4xl:w-40"
        value={filtres.origine}
        onChange={(e) => poserFiltres({ ...filtres, origine: e.target.value })}
      >
        <option value="">Toutes origines</option>
        {ORIGINES_AGENT.map((origine) => (
          <option key={origine.valeur} value={origine.valeur}>
            {origine.libelle}
          </option>
        ))}
      </ChampListe>

      <ChampListe
        id="filtre-etat-agent"
        libelle="État"
        className="min-w-0 @4xl:w-40"
        value={filtres.etat}
        onChange={(e) => poserFiltres({ ...filtres, etat: e.target.value })}
      >
        <option value="">Tous les états</option>
        {ETATS_AGENT.map((etat) => (
          <option key={etat.cle} value={etat.cle}>
            {etat.libelle}
          </option>
        ))}
      </ChampListe>

      <ChampListe
        id="tri-agents"
        libelle="Trier par"
        className="min-w-0 @4xl:w-40"
        value={tri}
        onChange={(e) => poserTri(e.target.value as CleTriAgents)}
      >
        {TRIS_AGENTS.map((option) => (
          <option key={option.cle} value={option.cle}>
            {option.libelle}
          </option>
        ))}
      </ChampListe>

      {/* Ce que le filtre laisse voir, et la sortie. Les deux n'apparaissent
          qu'une fois un filtre posé : sinon le compte redirait la longueur de
          la liste qu'on a sous les yeux. */}
      {actifs && (
        <p className="col-span-2 flex items-center gap-2 text-annexe text-texte-secondaire @4xl:col-auto">
          <span>
            {retenus} / {lignes.length} agents
          </span>
          <Bouton
            variante="discret"
            ton="neutre"
            taille="petite"
            onClick={() => poserFiltres(FILTRES_VIDES)}
          >
            Tout afficher
          </Bouton>
        </p>
      )}
    </nav>
  );
}

/**
 * Le pictogramme du rôle. Il prend l'icône en **prop** plutôt que de la résoudre
 * dans le corps de la carte, et ce n'est pas une préférence de style : une
 * variable capitalisée affectée pendant le rendu est un composant *créé* à
 * chaque rendu pour `react-hooks/static-components`. C'est la forme que
 * `LigneActivite` emploie déjà pour l'icône d'un événement.
 */
function GlypheRole({ icone: Composant }: { icone: Icone }) {
  return (
    <Composant className="size-4 shrink-0 text-neutral-400 dark:text-neutral-500" />
  );
}

function CarteAgent({
  ligne,
  onglet,
}: {
  ligne: LigneAgent;
  onglet: CleOngletAgent;
}) {
  const { fiche, etat, instances, tachesEnCours } = ligne;
  // La charge ne se dit que quand elle apprend quelque chose : « 1 instance »
  // sur un agent libre est le réglage par défaut de tout le monde, donc du bruit
  // sur chaque carte — exactement ce que ce ticket retire.
  const charge = [
    instances !== null && instances > 1 ? `${instances} instances` : "",
    tachesEnCours > 0
      ? `${tachesEnCours} tâche${tachesEnCours > 1 ? "s" : ""} en cours`
      : "",
  ].filter(Boolean);

  return (
    <Link
      href={cheminOnglet(fiche.nom, onglet)}
      // La surface vient de la primitive ; ce qui reste ici est ce qu'un lien
      // ajoute — sa hauteur dans la grille, son alignement, son survol.
      className={classesCarte({
        densite: "aucune",
        className:
          "flex h-full flex-col gap-1.5 px-3 py-2 text-left text-corps hover:bg-neutral-50 dark:hover:bg-neutral-800",
      })}
    >
      {/* L'identité à gauche, l'état à droite : une place fixe pour chacun,
          d'une carte à l'autre (docs/30 §1.6). */}
      <span className="flex items-start justify-between gap-2">
        <span className="flex min-w-0 items-center gap-1.5 font-medium">
          <GlypheRole icone={iconeDuRole(fiche.role)} />
          <span className="truncate">{fiche.nom}</span>
        </span>
        <BadgeEtat
          ton={etat.ton}
          icone={etat.icone}
          pulse={etat.pulse}
          contour
          className="shrink-0"
        >
          {etat.libelle}
        </BadgeEtat>
      </span>

      <span className="block text-annexe text-neutral-500 dark:text-neutral-400">
        {fiche.role}
        {" · "}
        {libelleOrigine(fiche.source)}
      </span>

      {charge.length > 0 && (
        <span className="flex items-center gap-1.5 text-annexe text-neutral-500 dark:text-neutral-400">
          <IconeCapacite className="size-3.5 shrink-0" />
          {charge.join(" · ")}
        </span>
      )}
    </Link>
  );
}
