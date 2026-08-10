// Sonde de mise en page — à passer telle quelle en `function` de
// `mcp__chrome-maestro__browser_evaluate`. Le fichier est *exactement* une
// fonction fléchée, sans rien autour (ni `const`, ni `export`) : c'est la forme
// qu'attend `browser_evaluate`.
//
// Le premier appel **pose la sonde** dans la page (`window.__banc`) et rend son
// relevé. Aux points de rupture suivants, il suffit alors de
// `() => window.__banc()` — inutile de recoller ce fichier. Une **navigation**
// efface la sonde (contexte neuf) ; un `browser_resize`, non.
//
// Elle ne rend que des **constats mesurés dans le navigateur** : ce qui déborde
// à droite, ce qu'aucun défilement ne peut ramener, et les conteneurs qui
// rognent. Aucune interprétation — c'est le rôle de l'appelant.
() => {
  window.__banc = () => {
    const TOL = 1; // px — le rendu sous-pixel fait dépasser d'une fraction sans que rien ne soit coupé.
    const MAX = 12; // on nomme les premiers coupables, pas la page entière.

    const style = (el) => getComputedStyle(el);
    const defilableY = (cs) => /auto|scroll/.test(cs.overflowY);
    const defilableX = (cs) => /auto|scroll/.test(cs.overflowX);
    const contientY = (cs) => /auto|scroll|hidden|clip/.test(cs.overflowY);
    const contientX = (cs) => /auto|scroll|hidden|clip/.test(cs.overflowX);

    /** Un chemin lisible par un humain : trois niveaux, ou l'id s'il y en a un. */
    const designer = (el) => {
      if (!el || el.nodeType !== 1) return "?";
      if (el === document.documentElement) return "html";
      if (el === document.body) return "body";
      const bouts = [];
      let n = el;
      for (let i = 0; n && n.nodeType === 1 && i < 3; i++) {
        if (n.id) {
          bouts.unshift(`#${n.id}`);
          break;
        }
        let s = n.tagName.toLowerCase();
        const cls = (n.getAttribute("class") || "").trim().split(/\s+/).filter(Boolean);
        if (cls.length) s += `.${cls.slice(0, 3).join(".")}${cls.length > 3 ? "…" : ""}`;
        bouts.unshift(s);
        if (n === document.body) break;
        n = n.parentElement;
      }
      return bouts.join(" > ");
    };

    /** De quoi reconnaître l'élément à l'écran quand le sélecteur ne suffit pas. */
    const etiquette = (el) => {
      const propre = (v) => (v || "").toString().trim().replace(/\s+/g, " ");
      // Un `<input>` n'a ni texte ni contenu : sans le repli sur son libellé, la
      // moitié d'un formulaire se rapporterait sous un nom vide.
      const lie = el.id ? document.querySelector(`label[for="${CSS.escape(el.id)}"]`) : null;
      const t =
        propre(el.getAttribute("aria-label")) ||
        propre(el.innerText) ||
        propre(el.placeholder) ||
        propre(lie && lie.innerText) ||
        propre(el.closest("label") && el.closest("label").innerText) ||
        propre(el.value) ||
        propre(el.alt) ||
        propre(el.getAttribute("name") || el.getAttribute("title"));
      return t.slice(0, 60);
    };

    /** Hors-jeu : rien à mesurer, ou volontairement escamoté. */
    const horsJeu = (el, r) => {
      if (r.width <= 1 && r.height <= 1) return true; // `sr-only`, ancres, sondes.
      const cs = style(el);
      if (cs.display === "none" || cs.visibility === "hidden" || cs.opacity === "0") return true;
      if (el.closest("[hidden], [aria-hidden='true'], details:not([open])")) return true;
      // Un élément fixe n'est pas emporté par le défilement : il est là où il est.
      return cs.position === "fixed";
    };

    // ── 1. Ce qu'aucun défilement ne peut ramener ──────────────────────────
    // On remonte les ancêtres jusqu'au premier qui *contienne* l'élément. S'il
    // défile, l'élément est atteignable ; s'il rogne (`hidden`/`clip`), il est
    // perdu — et aucun ancêtre plus haut ne peut le rattraper.
    const verdictAcces = (el, r) => {
      for (let n = el.parentElement; n; n = n.parentElement) {
        const cs = style(n);
        const nr = n.getBoundingClientRect();
        const dehorsY = contientY(cs) && (r.bottom > nr.bottom + TOL || r.top < nr.top - TOL);
        const dehorsX = contientX(cs) && (r.right > nr.right + TOL || r.left < nr.left - TOL);
        if (!dehorsY && !dehorsX) continue;
        if (dehorsY && !defilableY(cs)) return { ok: false, rogneur: n, axe: "vertical" };
        if (dehorsX && !defilableX(cs)) return { ok: false, rogneur: n, axe: "horizontal" };
        return { ok: true }; // le conteneur défile : il peut l'amener sous les yeux.
      }
      // Plus aucun conteneur : reste le défilement du document lui-même.
      const de = document.documentElement;
      const debordeBas = r.bottom > de.clientHeight + TOL;
      const docDefile = de.scrollHeight > de.clientHeight + TOL;
      if (debordeBas && !docDefile) return { ok: false, rogneur: de, axe: "vertical" };
      return { ok: true };
    };

    // Les éléments qu'un utilisateur doit *pouvoir atteindre* : c'est là que
    // l'inaccessibilité fait mal (le bouton « Déclarer le projet » de #306).
    const SEL_ACTIONS =
      "button, a[href], input:not([type='hidden']), select, textarea, [role='button'], [tabindex]:not([tabindex='-1'])";
    const inatteignables = [];
    for (const el of document.querySelectorAll(SEL_ACTIONS)) {
      const r = el.getBoundingClientRect();
      if (horsJeu(el, r)) continue;
      const v = verdictAcces(el, r);
      if (v.ok) continue;
      const rr = v.rogneur.getBoundingClientRect();
      inatteignables.push({
        element: designer(el),
        libelle: etiquette(el),
        axe: v.axe,
        rogne_par: designer(v.rogneur),
        overflow: style(v.rogneur).overflow,
        hors_champ_px: Math.round(v.axe === "vertical" ? r.bottom - rr.bottom : r.right - rr.right),
      });
      if (inatteignables.length >= MAX) break;
    }

    // ── 2. Les conteneurs qui rognent sans laisser défiler ────────────────
    // La cause, là où le point 1 n'en donne que les symptômes : un élément dont
    // le contenu dépasse alors que son `overflow` vaut `hidden`/`clip`.
    const rogneurs = [];
    for (const el of document.querySelectorAll("body, body *")) {
      const cs = style(el);
      const r = el.getBoundingClientRect();
      if (r.width <= 1 && r.height <= 1) continue;
      const rogneY = /hidden|clip/.test(cs.overflowY) && el.scrollHeight > el.clientHeight + TOL;
      // `truncate` (`overflow:hidden` + `text-overflow:ellipsis`) rogne **à
      // dessein** et le montre par ses points de suspension : le signaler
      // noierait le rapport sous les titres tronçonnés de l'en-tête.
      const rogneX =
        /hidden|clip/.test(cs.overflowX) &&
        cs.textOverflow !== "ellipsis" &&
        el.scrollWidth > el.clientWidth + TOL;
      if (!rogneY && !rogneX) continue;
      rogneurs.push({
        element: designer(el),
        overflow: cs.overflow,
        coupe_en_bas_px: rogneY ? el.scrollHeight - el.clientHeight : 0,
        coupe_a_droite_px: rogneX ? el.scrollWidth - el.clientWidth : 0,
      });
      if (rogneurs.length >= MAX) break;
    }

    // ── 3. Débordement horizontal de la page ──────────────────────────────
    // Le coupable est le plus *haut* de sa branche : on ne descend pas sous un
    // élément déjà fautif, sinon on nommerait tous ses enfants.
    //
    // On ne descend pas non plus sous un conteneur qui **prend l'axe X en
    // charge** (`overflow-x` autre que `visible`) : ce qui dépasse à
    // l'intérieur y est voulu — une table large dans son `overflow-x-auto` est
    // le patron habituel du dépôt, et la signaler ferait crier au débordement
    // sur chaque page à tableau. S'il rogne au lieu de laisser défiler, ce sont
    // les points 1 et 2 qui le disent.
    const de = document.documentElement;
    const largeurVue = de.clientWidth;
    const coupablesX = [];
    const file = [document.body];
    while (file.length && coupablesX.length < MAX) {
      const el = file.shift();
      if (!el) continue;
      for (const enfant of el.children) {
        const r = enfant.getBoundingClientRect();
        if (horsJeu(enfant, r)) continue;
        if (r.right > largeurVue + TOL || r.left < -TOL) {
          coupablesX.push({
            element: designer(enfant),
            libelle: etiquette(enfant),
            deborde_de_px: Math.round(Math.max(r.right - largeurVue, -r.left)),
            largeur_px: Math.round(r.width),
          });
        } else if (!contientX(style(enfant))) {
          file.push(enfant);
        }
      }
    }

    return {
      fenetre: `${largeurVue}×${de.clientHeight} (css)`,
      page: {
        // Le symptôme de #248 : une page qui s'allonge au lieu de tenir.
        hauteur_document_px: de.scrollHeight,
        largeur_document_px: de.scrollWidth,
        document_defile: de.scrollHeight > de.clientHeight + TOL,
        barre_horizontale: de.scrollWidth > largeurVue + TOL,
      },
      debordement_horizontal: coupablesX,
      inatteignables,
      rogneurs,
      verdict:
        inatteignables.length === 0 && coupablesX.length === 0
          ? "RAS — rien d'inatteignable, aucun débordement horizontal"
          : `${inatteignables.length} inatteignable(s), ${coupablesX.length} débordement(s) horizontal(aux)`,
    };
  };
  return window.__banc();
}
