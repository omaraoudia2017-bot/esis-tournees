/* Road Optim — application mobile du livreur.

   Page servie par l'API elle-même (/livreur) : elle appelle les MÊMES routes que l'interface
   Streamlit, avec les mêmes contrôles côté serveur (2FA, rôle, conditions, cloisonnement).

   Sécurité côté navigateur :
   - le jeton de session est gardé en MÉMOIRE uniquement (jamais dans le stockage du navigateur,
     ni dans un cookie) : recharger la page déconnecte ;
   - aucune donnée n'est insérée comme du HTML brut : tout passe par textContent, ce qui
     empêche l'injection de code (XSS) via un nom de client ou une remarque ;
   - la politique CSP de l'API n'autorise que les scripts et styles de ce dossier ;
   - aucune ressource externe (ni police, ni carte en ligne) : aucune adresse ne sort vers un tiers.
*/
"use strict";

const FUSEAU = "Europe/Paris";
const STATUTS = {
  a_livrer: ["À livrer", "#1F4E79"], en_cours: ["En cours", "#B45309"], livre: ["Livré", "#15803D"],
  absent: ["Absent", "#B91C1C"], refuse: ["Refusé", "#B91C1C"], reporte: ["À reporter", "#6D28D9"],
};
const ISSUES = [["livre", "Livré"], ["absent", "Absent"], ["refuse", "Refusé"]];
const FAITES = ["livre", "absent", "refuse"];

const etat = {
  ecran: "connexion", jeton: null, preauth: null, expireLe: null, profil: null, tournee: null,
  message: null, messageConnexion: null, enrolement: null, codesSecours: null, conditions: null,
  lectureConditions: false, issue: {}, remarqueOuverte: null, occupe: false, calcul: false, domicile: null,
};

// --------------------------------------------------------------------------- outils DOM
function h(balise, attributs, ...enfants) {
  const el = document.createElement(balise);
  for (const [cle, valeur] of Object.entries(attributs || {})) {
    if (valeur === null || valeur === undefined || valeur === false) continue;
    if (cle === "classe") el.className = valeur;
    else if (cle === "texte") el.textContent = valeur;
    else if (cle.startsWith("on")) el.addEventListener(cle.slice(2), valeur);
    else if (cle === "style") Object.assign(el.style, valeur);
    else if (valeur === true) el.setAttribute(cle, "");
    else el.setAttribute(cle, String(valeur));
  }
  for (const enfant of enfants.flat()) {
    if (enfant === null || enfant === undefined || enfant === false) continue;
    el.append(enfant instanceof Node ? enfant : document.createTextNode(String(enfant)));
  }
  return el;
}

const SVG = "http://www.w3.org/2000/svg";
function s(balise, attributs) {
  const el = document.createElementNS(SVG, balise);
  for (const [cle, valeur] of Object.entries(attributs || {})) el.setAttribute(cle, String(valeur));
  return el;
}

function icone(chemins, couleur = "#16213A", taille = 22) {
  const svg = s("svg", { width: taille, height: taille, viewBox: "0 0 24 24", fill: "none", stroke: couleur,
    "stroke-width": 2, "stroke-linecap": "round", "stroke-linejoin": "round", "aria-hidden": "true" });
  for (const d of chemins) svg.append(s("path", { d }));
  return svg;
}

function heure(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: FUSEAU });
}
function dateCourte(iso) {
  return new Date(iso).toLocaleDateString("fr-FR", { timeZone: FUSEAU });
}
function nombre(x, dec = 1) { return Number(x).toFixed(dec).replace(".", ","); }

// --------------------------------------------------------------------------- appels à l'API
async function api(methode, chemin, corps, jeton) {
  const entetes = { Accept: "application/json" };
  const cle = jeton === undefined ? etat.jeton : jeton;
  if (cle) entetes.Authorization = "Bearer " + cle;
  if (corps !== undefined) entetes["Content-Type"] = "application/json";
  let r;
  try {
    r = await fetch(chemin, { method: methode, headers: entetes, credentials: "omit", cache: "no-store",
      body: corps === undefined ? undefined : JSON.stringify(corps) });
  } catch (e) {
    return { status: 0, data: null, detail: "Le serveur ne répond pas. Réessayez dans quelques instants." };
  }
  let data = null;
  try { data = await r.json(); } catch (e) { data = null; }
  let detail = data && data.detail;
  if (Array.isArray(detail)) detail = "Saisie invalide : vérifiez les champs.";
  if (r.status === 429) detail = "Trop de tentatives. Patientez une minute avant de réessayer.";
  return { status: r.status, data, detail: typeof detail === "string" ? detail : "Action refusée." };
}

/** Appel d'une route protégée : gère l'expiration de session et les conditions à accepter. */
async function appel(methode, chemin, corps) {
  const r = await api(methode, chemin, corps);
  if (r.status === 401) {
    deconnexionLocale("Votre session a expiré. Veuillez vous reconnecter.");
    return null;
  }
  if (r.status === 403 && /conditions/i.test(r.detail)) {
    await ouvrirConditions(false);
    return null;
  }
  return r;
}

function annoncer(type, texte) { etat.message = { type, texte }; }

function deconnexionLocale(message) {
  Object.assign(etat, { ecran: "connexion", jeton: null, preauth: null, expireLe: null, profil: null,
    tournee: null, message: null, enrolement: null, codesSecours: null, conditions: null, issue: {},
    remarqueOuverte: null, domicile: null, messageConnexion: message || null });
  rendre();
}

// --------------------------------------------------------------------------- navigation
async function apresConnexion(donnees) {
  etat.jeton = donnees.access_token;
  etat.expireLe = new Date(donnees.expire_le);
  etat.preauth = null;
  if (donnees.role !== "livreur") {
    // L'espace planificateur reste dans l'interface web (Streamlit), sur grand écran
    await api("POST", "/auth/deconnexion");
    deconnexionLocale("Cette application est réservée aux livreurs. L'espace planificateur s'ouvre depuis l'interface web.");
    return;
  }
  if (etat.codesSecours) { etat.ecran = "codes"; rendre(); return; }
  await chargerProfilEtTournee();
}

async function chargerProfilEtTournee() {
  const p = await appel("GET", "/auth/moi");
  if (!p) return;
  etat.profil = p.data;
  if (etat.profil.conditions_a_accepter) { await ouvrirConditions(false); return; }
  await chargerTournee();
}

async function chargerTournee() {
  const r = await appel("GET", "/tournees/du-jour");
  if (!r) return;
  etat.tournee = r.status === 200 ? r.data : null;
  etat.ecran = "tournee";
  if (r.status !== 200 && r.status !== 404) annoncer("erreur", r.detail);
  rendre();
}

async function ouvrirConditions(lecture) {
  const r = await api("GET", "/conditions");
  if (r.status === 401) { deconnexionLocale("Votre session a expiré. Veuillez vous reconnecter."); return; }
  etat.conditions = r.status === 200 ? r.data : null;
  etat.lectureConditions = lecture;
  etat.ecran = "conditions";
  rendre();
}

async function action(methode, chemin, corps, messageOk) {
  if (etat.occupe) return;                     // évite un double clic pendant l'appel
  etat.occupe = true;
  let r;
  try {
    r = await appel(methode, chemin, corps);
  } finally {
    etat.occupe = false;
  }
  if (!r) return;
  if (r.status >= 400) annoncer("erreur", r.detail);
  else if (messageOk) annoncer("ok", typeof messageOk === "function" ? messageOk(r.data) : messageOk);
  await chargerTournee();
}

// --------------------------------------------------------------------------- rendu
function rendre() {
  const app = document.getElementById("app");
  const ecrans = { connexion: ecranConnexion, verification: ecranVerification, enrolement: ecranEnrolement,
    codes: ecranCodes, conditions: ecranConditions, tournee: ecranTournee, menu: ecranMenu, domicile: ecranDomicile };
  const defile = app.querySelector(".defilement, .page");
  const position = defile ? defile.scrollTop : 0;
  const memeEcran = app.dataset.ecran === etat.ecran;
  app.replaceChildren(ecrans[etat.ecran]());
  app.dataset.ecran = etat.ecran;
  const nouveau = app.querySelector(".defilement, .page");
  if (nouveau && memeEcran) nouveau.scrollTop = position;     // on garde sa place dans la liste
}

function boiteMessage(msg, fermer) {
  if (!msg) return null;
  return h("div", { classe: "message " + msg.type, role: msg.type === "erreur" ? "alert" : "status" },
    h("div", { texte: msg.texte }),
    fermer ? h("button", { type: "button", "aria-label": "Fermer le message", texte: "×", onclick: fermer }) : null);
}

// ---------------- connexion
function ecranConnexion() {
  const identifiant = h("input", { type: "text", id: "identifiant", autocomplete: "username", autocapitalize: "none", spellcheck: "false", maxlength: 50 });
  const mdp = h("input", { type: "password", id: "mdp", autocomplete: "current-password", maxlength: 128 });
  const erreur = h("div");
  const valider = async (e) => {
    e.preventDefault();
    if (!identifiant.value.trim() || !mdp.value) {
      erreur.replaceChildren(boiteMessage({ type: "erreur", texte: "Veuillez renseigner l'identifiant et le mot de passe." }));
      return;
    }
    const r = await api("POST", "/auth/connexion", { identifiant: identifiant.value.trim(), mot_de_passe: mdp.value }, null);
    mdp.value = "";
    if (r.status !== 200) { erreur.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail })); return; }
    etat.preauth = r.data.jeton_preauth;
    etat.messageConnexion = null;
    etat.ecran = r.data.etape === "enrolement" ? "enrolement" : "verification";
    rendre();
  };
  return h("form", { classe: "centre", onsubmit: valider, autocomplete: "on" },
    h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
      h("img", { classe: "logo", src: "/livreur/logo.svg", alt: "", width: 60, height: 60 }),
      h("h1", { classe: "marque" }, "Road ", h("span", { texte: "Optim" })),
      h("p", { classe: "texte", style: { color: "#5B6472" }, texte: "Connexion à votre espace livreur" })),
    boiteMessage(etat.messageConnexion ? { type: "alerte", texte: etat.messageConnexion } : null),
    erreur,
    h("label", { classe: "champ", for: "identifiant" }, "Identifiant", identifiant),
    h("label", { classe: "champ", for: "mdp" }, "Mot de passe", mdp),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Se connecter" }),
    h("p", { classe: "petit", texte: "Après 5 erreurs, le compte est bloqué 15 minutes. Session limitée à 30 minutes." }));
}

function boutonRetourConnexion() {
  return h("button", { type: "button", classe: "btn", texte: "Annuler et revenir à la connexion",
    onclick: () => deconnexionLocale(null) });
}

// ---------------- 2FA
function ecranVerification() {
  const code = h("input", { type: "text", id: "code", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 20, style: { fontSize: "22px", letterSpacing: "6px", textAlign: "center" } });
  const erreur = h("div");
  const valider = async (e) => {
    e.preventDefault();
    if (!code.value.trim()) return;
    const r = await api("POST", "/auth/2fa/verification", { code: code.value.trim() }, etat.preauth);
    if (r.status === 200) { await apresConnexion(r.data); return; }
    if (r.status === 401 && /Session/.test(r.detail)) { deconnexionLocale("Délai dépassé, veuillez vous reconnecter."); return; }
    code.value = "";
    erreur.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail }));
  };
  setTimeout(() => code.focus(), 50);
  return h("form", { classe: "centre", onsubmit: valider },
    h("h1", { classe: "moyen", texte: "Double authentification" }),
    h("p", { classe: "texte", style: { color: "#5B6472" }, texte: "Saisissez le code à 6 chiffres affiché dans votre application d'authentification." }),
    erreur,
    h("label", { classe: "champ", for: "code" }, "Code", code),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Valider" }),
    h("p", { classe: "petit", texte: "Téléphone perdu ? Saisissez un de vos codes de secours (XXXXX-XXXXX). Un code déjà utilisé est refusé." }),
    boutonRetourConnexion());
}

function ecranEnrolement() {
  const zone = h("div", { style: { display: "flex", flexDirection: "column", gap: "12px" } }, h("div", { classe: "chargement", texte: "Préparation du QR code…" }));
  const code = h("input", { type: "text", id: "code", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, style: { fontSize: "22px", letterSpacing: "6px", textAlign: "center" } });
  const erreur = h("div");
  const afficher = (d) => {
    zone.replaceChildren(
      h("img", { classe: "qr", alt: "QR code à scanner avec l'application d'authentification", src: "data:image/png;base64," + d.qr_code_png_base64 }),
      h("details", { classe: "details-contact" }, h("summary", { texte: "Impossible de scanner ? Clé à saisir" }),
        h("div", { classe: "codes", style: { gridTemplateColumns: "1fr" } }, d.cle_manuelle)));
  };
  if (etat.enrolement) afficher(etat.enrolement);
  else api("POST", "/auth/2fa/enrolement", undefined, etat.preauth).then((r) => {
    if (r.status !== 200) { deconnexionLocale("Délai dépassé, veuillez vous reconnecter."); return; }
    etat.enrolement = r.data; afficher(r.data);
  });
  const valider = async (e) => {
    e.preventDefault();
    const r = await api("POST", "/auth/2fa/activation", { code: code.value.trim() }, etat.preauth);
    if (r.status === 200) { etat.codesSecours = r.data.codes_secours; etat.enrolement = null; await apresConnexion(r.data); return; }
    code.value = "";
    erreur.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail }));
  };
  return h("form", { classe: "page", onsubmit: valider },
    h("h1", { classe: "moyen", texte: "Activer la double authentification" }),
    h("p", { classe: "texte", texte: "Première connexion : installez Google Authenticator ou Microsoft Authenticator, scannez ce QR code puis saisissez le code à 6 chiffres affiché." }),
    zone, erreur,
    h("label", { classe: "champ", for: "code" }, "Code à 6 chiffres", code),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Activer" }),
    boutonRetourConnexion());
}

function ecranCodes() {
  const confirme = h("input", { type: "checkbox", id: "conserve" });
  const continuer = h("button", { type: "button", classe: "btn btn-principal", texte: "Continuer", disabled: true,
    onclick: async () => { etat.codesSecours = null; await chargerProfilEtTournee(); } });
  confirme.addEventListener("change", () => { continuer.disabled = !confirme.checked; });
  return h("div", { classe: "page" },
    h("h1", { classe: "moyen", texte: "Vos codes de secours" }),
    boiteMessage({ type: "alerte", texte: "Ces codes ne seront plus jamais affichés. Conservez-les hors de votre téléphone (papier, gestionnaire de mots de passe). Chacun remplace une seule fois le code de l'application." }),
    h("div", { classe: "codes" }, (etat.codesSecours || []).map((c) => h("span", { texte: c }))),
    h("label", { classe: "case", for: "conserve" }, confirme, "J'ai conservé mes codes de secours"),
    continuer);
}

// ---------------- conditions d'utilisation (texte Markdown simple, rendu sans HTML)
function enLigne(texte) {
  // **gras** uniquement ; tout le reste est du texte brut
  const morceaux = texte.split(/(\*\*[^*]+\*\*)/g).filter(Boolean);
  return morceaux.map((m) => m.startsWith("**") ? h("strong", { texte: m.slice(2, -2) }) : document.createTextNode(m));
}

function rendreMarkdown(md) {
  const bloc = h("div", { classe: "texte-conditions", tabindex: 0, "aria-label": "Texte des conditions" });
  const lignes = md.split(/\r?\n/);
  let liste = null, table = null;
  for (const brute of lignes) {
    const l = brute.trim();
    if (!l.startsWith("- ")) liste = null;
    if (!l.startsWith("|")) table = null;
    if (!l) continue;
    if (l.startsWith("#")) bloc.append(h("h4", {}, enLigne(l.replace(/^#+\s*/, ""))));
    else if (l.startsWith("- ")) {
      if (!liste) { liste = h("ul"); bloc.append(liste); }
      liste.append(h("li", {}, enLigne(l.slice(2))));
    } else if (l.startsWith("|")) {
      const cellules = l.split("|").slice(1, -1).map((c) => c.trim());
      if (cellules.every((c) => /^-+$/.test(c))) continue;      // ligne de séparation
      const premiere = !table;
      if (!table) { table = h("table"); bloc.append(table); }
      table.append(h("tr", {}, cellules.map((c) => h(premiere ? "th" : "td", {}, enLigne(c)))));
    } else bloc.append(h("p", {}, enLigne(l)));
  }
  return bloc;
}

function ecranConditions() {
  const c = etat.conditions;
  if (!c) return h("div", { classe: "page" }, boiteMessage({ type: "erreur", texte: "Conditions indisponibles." }), boutonRetourConnexion());
  const aAccepter = c.a_accepter && !etat.lectureConditions;
  const enfants = [
    h("h1", { classe: "moyen", texte: "Conditions d'utilisation et données personnelles" }),
    h("p", { classe: "petit", texte: "Version " + c.version + (aAccepter ? (c.version_acceptee ? " · nouvelle version à lire" : " · à lire et accepter avant d'accéder à votre tournée") : "") }),
    rendreMarkdown(c.texte),
  ];
  if (aAccepter) {
    const c1 = h("input", { type: "checkbox", id: "cgu1" });
    const c2 = h("input", { type: "checkbox", id: "cgu2" });
    const erreur = h("div");
    const accepter = h("button", { type: "button", classe: "btn btn-principal", texte: "Accepter et continuer", disabled: true,
      onclick: async () => {
        const r = await api("POST", "/conditions/accepter", { version: c.version, conditions_acceptees: true, information_lue: true });
        if (r.status !== 200) { erreur.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail })); return; }
        annoncer("ok", "Merci, vous pouvez accéder à votre tournée.");
        await chargerProfilEtTournee();
      } });
    const maj = () => { accepter.disabled = !(c1.checked && c2.checked); };
    c1.addEventListener("change", maj); c2.addEventListener("change", maj);
    enfants.push(erreur,
      h("label", { classe: "case", for: "cgu1" }, c1, "J'ai lu et j'accepte les conditions d'utilisation (articles 1 à 4)."),
      h("label", { classe: "case", for: "cgu2" }, c2, "J'ai pris connaissance de l'information sur le traitement de mes données personnelles (article 5)."),
      h("div", { classe: "rangee" },
        h("button", { type: "button", classe: "btn", texte: "Refuser", onclick: async () => {
          await api("POST", "/auth/deconnexion");
          deconnexionLocale("Les conditions d'utilisation doivent être acceptées pour utiliser l'application. Pour toute question, adressez-vous à votre responsable.");
        } }),
        accepter));
  } else {
    if (c.acceptee_le) enfants.push(h("p", { classe: "petit", texte: "Acceptées le " + dateCourte(c.acceptee_le) + "." }));
    enfants.push(h("button", { type: "button", classe: "btn btn-principal", texte: "Retour à ma tournée", onclick: chargerTournee }));
  }
  return h("div", { classe: "page" }, enfants);
}

// ---------------- menu
function ecranMenu() {
  const p = etat.profil || {};
  const restant = etat.expireLe ? Math.max(0, Math.floor((etat.expireLe - new Date()) / 60000)) : 0;
  const t = etat.tournee;
  return h("div", { classe: "page" },
    h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
      h("h1", { classe: "moyen", texte: "Menu" }),
      h("button", { type: "button", classe: "btn-icone", "aria-label": "Fermer le menu", onclick: () => { etat.ecran = "tournee"; rendre(); } },
        icone(["M6 6l12 12", "M18 6L6 18"]))),
    h("div", { classe: "livraison" },
      h("div", { classe: "nom", texte: (p.prenom || "") + " " + (p.nom || "") }),
      h("div", { classe: "petit", texte: (p.type_livreur === "interne" ? "Livreur interne (rentre avec le camion)" : "Livreur prestataire (rend le camion au dépôt)") + (t && t.vehicule ? " · camion " + t.vehicule : "") }),
      h("div", { classe: "petit", texte: "Session valable encore " + restant + " min" }),
      p.conditions_acceptees_le ? h("div", { classe: "petit", texte: "Conditions v" + p.conditions_version + " acceptées le " + dateCourte(p.conditions_acceptees_le) }) : null),
    p.type_livreur === "interne" ? h("button", { type: "button", classe: "btn btn-menu", texte: "Mon adresse de départ et de retour", onclick: ouvrirDomicile }) : null,
    h("button", { type: "button", classe: "btn btn-menu", texte: "Relire les conditions et mes données", onclick: () => ouvrirConditions(true) }),
    h("button", { type: "button", classe: "btn btn-menu", texte: "Actualiser ma tournée", onclick: chargerTournee }),
    h("button", { type: "button", classe: "btn btn-menu btn-danger", texte: "Se déconnecter", onclick: async () => {
      await api("POST", "/auth/deconnexion");
      deconnexionLocale("Vous êtes déconnecté.");
    } }));
}

// ---------------- domicile (livreur interne)
async function ouvrirDomicile() {
  const r = await appel("GET", "/moi/domicile");
  if (!r) return;
  etat.domicile = r.data;
  etat.ecran = "domicile";
  rendre();
}

function ecranDomicile() {
  const d = etat.domicile || {};
  const voie = h("input", { type: "text", id: "voie", maxlength: 200, autocomplete: "street-address" });
  const cp = h("input", { type: "text", id: "cp", maxlength: 5, inputmode: "numeric", autocomplete: "postal-code" });
  const ville = h("input", { type: "text", id: "ville", maxlength: 100, autocomplete: "address-level2" });
  const retour = h("div");
  const enregistrer = async (e) => {
    e.preventDefault();
    if (!voie.value.trim() || !/^[0-9]{5}$/.test(cp.value.trim()) || !ville.value.trim()) {
      retour.replaceChildren(boiteMessage({ type: "erreur", texte: "Rue, code postal (5 chiffres) et ville sont obligatoires." }));
      return;
    }
    retour.replaceChildren(boiteMessage({ type: "info", texte: "Vérification de l'adresse…" }));
    const r = await appel("PUT", "/moi/domicile", { voie: voie.value.trim(), code_postal: cp.value.trim(), ville: ville.value.trim() });
    if (!r) return;
    if (r.status !== 200) { retour.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail })); return; }
    etat.domicile = r.data;
    annoncer(r.data.avertissement ? "alerte" : "ok", r.data.avertissement || "Adresse enregistrée : appuyez sur « Optimiser » pour en tenir compte.");
    await chargerTournee();
  };
  return h("form", { classe: "page", onsubmit: enregistrer },
    h("h1", { classe: "moyen", texte: "Mon adresse de départ et de retour" }),
    h("p", { classe: "texte" }, "Vous rentrez avec le camion : domicile → dépôt (chargement) → clients → retour à votre domicile, ",
      h("strong", { texte: "sauf s'il reste des colis non livrés" }), " (absent, refus, client reporté) : le retour se fait alors au dépôt."),
    h("p", { classe: "petit", texte: "Adresse chiffrée, utilisée seulement pour votre itinéraire. Le planificateur voit seulement qu'elle est renseignée, jamais l'adresse." }),
    d.renseigne
      ? boiteMessage({ type: "ok", texte: "Adresse enregistrée : " + d.adresse + (d.trajet_depot_min !== null && d.trajet_depot_min !== undefined ? ". Trajet jusqu'au dépôt : " + nombre(d.trajet_depot_km) + " km, environ " + d.trajet_depot_min + " min." : "") })
      : boiteMessage({ type: "alerte", texte: "Aucune adresse : votre tournée part et revient au dépôt." }),
    retour,
    h("label", { classe: "champ", for: "voie" }, "Numéro et rue", voie),
    h("div", { classe: "grille-cp" },
      h("label", { classe: "champ", for: "cp" }, "Code postal", cp),
      h("label", { classe: "champ", for: "ville" }, "Ville", ville)),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Enregistrer mon adresse" }),
    h("div", { classe: "rangee" },
      d.renseigne ? h("button", { type: "button", classe: "btn", texte: "Supprimer", onclick: async () => {
        const r = await appel("DELETE", "/moi/domicile");
        if (!r) return;
        annoncer("ok", "Adresse supprimée : votre tournée part et revient au dépôt.");
        await chargerTournee();
      } }) : null,
      h("button", { type: "button", classe: "btn", texte: "Retour à ma tournée", onclick: chargerTournee })));
}

// ---------------- tournée
function ecranTournee() {
  const p = etat.profil || {};
  const t = etat.tournee;
  const entete = h("div", { classe: "entete" },
    h("div", {},
      h("div", { classe: "titre", texte: "Bonjour " + (p.prenom || "") }),
      h("div", { classe: "sous-titre", texte: t
        ? new Date(t.date_tournee + "T12:00:00").toLocaleDateString("fr-FR") + " · camion " + (t.vehicule || "—") + " (" + (t.type_camion || "—") + ") · " + (t.zone || "—")
        : new Date().toLocaleDateString("fr-FR", { timeZone: FUSEAU }) })),
    h("button", { type: "button", classe: "btn-icone", "aria-label": "Ouvrir le menu", onclick: () => { etat.ecran = "menu"; rendre(); } },
      icone(["M4 7h16", "M4 12h16", "M4 17h16"])));

  const corps = h("div", { classe: "defilement" });
  corps.append(boiteMessage(etat.message, () => { etat.message = null; rendre(); }) || "");
  if (!t) {
    corps.append(boiteMessage({ type: "info", texte: "Aucune tournée ne vous est attribuée aujourd'hui." }),
      h("button", { type: "button", classe: "btn btn-contour", texte: "Actualiser", onclick: chargerTournee }));
    return h("div", { classe: "ecran" }, entete, corps);
  }

  const livraisons = t.livraisons;
  const enCours = livraisons.find((l) => l.statut === "en_cours");
  const aFaire = livraisons.filter((l) => l.statut === "a_livrer" || l.statut === "en_cours");
  const faites = livraisons.filter((l) => FAITES.includes(l.statut));
  const reportees = livraisons.filter((l) => l.statut === "reporte");

  // Bandeau : « Terminer » cliqué par erreur (le dernier clic, dans les 10 minutes)
  const a = t.annulation_possible;
  if (a && a.action === "terminer") {
    corps.append(h("div", { classe: "bandeau" },
      h("div", { texte: "Dernier clic : « Terminer » chez " + a.client + ". Annulable jusqu'à " + heure(a.expire_le) + "." }),
      h("button", { type: "button", classe: "btn btn-contour", texte: "Annuler ce clic",
        onclick: () => action("POST", "/tournees/du-jour/annuler-dernier-pointage", undefined, (d) => d.message) })));
  }

  // Départ et retour (livreur interne)
  if (t.type_livreur === "interne") {
    if (!t.domicile) {
      corps.append(h("div", { classe: "itineraire info" }, "Vous rentrez avec le camion : ",
        h("button", { type: "button", classe: "btn-lien", texte: "renseignez votre adresse", onclick: ouvrirDomicile }),
        " pour que l'itinéraire parte et revienne chez vous."));
    } else if (t.retour === "domicile") {
      corps.append(h("div", { classe: "itineraire ok", texte: "Itinéraire : votre domicile → dépôt" + (t.trajet_domicile_depot_min ? " (≈ " + t.trajet_domicile_depot_min + " min)" : "") + " → clients → retour à votre domicile." }));
    } else {
      corps.append(h("div", { classe: "itineraire alerte", texte: "Retour au dépôt en fin de tournée : " + t.colis_a_rapporter + " colis non livré(s) à y déposer (client absent, refus ou client à reporter)." }));
    }
  } else if (t.statut === "terminee" && t.colis_a_rapporter) {
    corps.append(h("div", { classe: "itineraire alerte", texte: t.colis_a_rapporter + " colis non livré(s) à rapporter au dépôt." }));
  }

  // Indicateurs
  let retourPrevu = "—";
  if (t.duree_estimee_min) {
    const [hh, mm] = t.heure_depart.split(":").map(Number);
    const total = hh * 60 + mm + t.duree_estimee_min;
    retourPrevu = String(Math.floor(total / 60) % 24).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
  }
  const indicateur = (libelle, valeur) => h("div", { classe: "indicateur" }, h("div", { classe: "libelle", texte: libelle }), h("div", { classe: "valeur", texte: valeur }));
  corps.append(h("div", { classe: "indicateurs" },
    indicateur("Livraisons", faites.length + " / " + (livraisons.length - reportees.length)),
    indicateur("Distance", t.distance_totale_km ? nombre(t.distance_totale_km) + " km" : "—"),
    indicateur(t.retour === "domicile" ? "Retour domicile" : "Retour dépôt", retourPrevu)));

  if (t.statut === "terminee") {
    corps.append(boiteMessage({ type: "ok", texte: "Tournée terminée. Merci !" }));
  } else if (aFaire.length) {
    const demarree = t.statut === "en_cours";
    corps.append(h("button", { type: "button", classe: "btn btn-principal", disabled: !!enCours || etat.calcul,
      title: enCours ? "Terminez d'abord la livraison en cours." : null,
      texte: etat.calcul ? "Calcul de l'itinéraire…" : (demarree ? "Recalculer l'itinéraire restant" : "Optimiser"),
      onclick: optimiser }));
    if (!t.optimisee_le) corps.append(h("p", { classe: "petit", texte: "Liste dans l'ordre d'attribution, pas encore optimisée." }));
    else if (aFaire.some((l) => l.ordre_passage === null)) corps.append(h("p", { classe: "petit", texte: "Nouvelle adresse ajoutée : appuyez sur le bouton ci-dessus pour l'intégrer au meilleur endroit." }));
  }
  if (t.statut !== "terminee") corps.append(formulaireAjout(t));

  corps.append(carteGeo(t), h("p", { classe: "tres-petit", style: { marginTop: "-6px" },
    texte: "D : dépôt. " + (t.optimisee_le ? "Trait bleu : itinéraire optimisé." : "Pointillés gris : ordre d'attribution.") + (t.domicile ? " Pointillés verts : domicile → dépôt." : "") + " Tracé à vol d'oiseau." }));

  const premier = aFaire.find((l) => l.statut === "a_livrer");
  if (aFaire.length) corps.append(h("h2", { classe: "section", texte: "À faire (" + aFaire.length + ")" }));
  aFaire.forEach((l) => corps.append(carteLivraison(l, !enCours, premier && l.id === premier.id)));
  if (faites.length) corps.append(h("h2", { classe: "section", texte: "Terminées (" + faites.length + ")" }));
  faites.forEach((l) => corps.append(carteLivraison(l, false, false)));
  if (reportees.length) {
    corps.append(h("h2", { classe: "section", texte: "À reporter (" + reportees.length + ")" }),
      h("p", { classe: "petit", texte: "Clients incompatibles avec votre véhicule ou votre créneau : le planificateur les replanifie." }));
    reportees.forEach((l) => corps.append(carteLivraison(l, false, false)));
  }
  corps.append(h("p", { classe: "tres-petit", texte: "Le pointage sert à améliorer l'estimation des temps de livraison, pas à évaluer votre performance individuelle." }));
  return h("div", { classe: "ecran" }, entete, corps);
}

async function optimiser() {
  if (etat.occupe) return;
  etat.occupe = true;
  etat.calcul = true;
  rendre();
  try {
    const r = await appel("POST", "/tournees/du-jour/optimiser");
    if (!r) return;
    if (r.status !== 200) { annoncer("erreur", r.detail); }
    else {
      const res = r.data;
      let texte = res.mode === "restant"
        ? "Itinéraire restant recalculé depuis votre position en " + nombre(res.duree_calcul_s) + " s : " + nombre(res.distance_apres_km) + " km au lieu de " + nombre(res.distance_avant_km) + " km."
        : "Itinéraire optimisé en " + nombre(res.duree_calcul_s) + " s : " + nombre(res.distance_apres_km) + " km au lieu de " + nombre(res.distance_avant_km) + " km dans l'ordre initial.";
      if (res.retour === "domicile") texte += " Retour prévu à votre domicile.";
      if (res.temps_service === "modele") texte += " Temps sur place estimés client par client par le modèle d'apprentissage.";
      if (res.nb_retards) texte += " Attention : " + res.nb_retards + " créneau(x) risquent d'être dépassés.";
      if (res.a_reporter.length) texte += " " + res.a_reporter.length + " client(s) à reporter (véhicule inadapté ou créneau impossible).";
      annoncer(res.nb_retards || res.a_reporter.length ? "alerte" : "ok", texte);
    }
  } finally {
    etat.occupe = false;
    etat.calcul = false;
  }
  await chargerTournee();
}

function formulaireAjout(t) {
  const nb = t.livraisons.length;
  const details = h("details", { classe: "bloc" }, h("summary", { texte: "Ajouter une adresse (" + nb + " / " + t.max_clients + " clients)" }));
  const contenu = h("div", { classe: "contenu" });
  details.append(contenu);
  if (nb >= t.max_clients) {
    contenu.append(boiteMessage({ type: "alerte", texte: "Limite d'effectif de la zone atteinte : un nouveau client doit être reporté sur une tournée ultérieure." }));
    return details;
  }
  const champs = {
    nom: h("input", { type: "text", id: "aj-nom", maxlength: 100 }), voie: h("input", { type: "text", id: "aj-voie", maxlength: 200 }),
    cp: h("input", { type: "text", id: "aj-cp", maxlength: 5, inputmode: "numeric" }), ville: h("input", { type: "text", id: "aj-ville", maxlength: 100 }),
    digicode: h("input", { type: "text", id: "aj-digicode", maxlength: 50 }), notes: h("textarea", { id: "aj-notes", maxlength: 500 }),
  };
  let type = "normal";
  const bColis = h("button", { type: "button", classe: "choix actif", texte: "Colis (camion normal)" });
  const bPalette = h("button", { type: "button", classe: "choix", texte: "Palette (camion hayon)" });
  bColis.addEventListener("click", () => { type = "normal"; bColis.classList.add("actif"); bPalette.classList.remove("actif"); });
  bPalette.addEventListener("click", () => { type = "hayon"; bPalette.classList.add("actif"); bColis.classList.remove("actif"); });
  const retour = h("div");
  const ajouter = h("button", { type: "button", classe: "btn btn-principal", texte: "Ajouter à ma tournée", onclick: async () => {
    const v = (k) => champs[k].value.trim();
    if (!v("nom") || !v("voie") || !/^[0-9]{5}$/.test(v("cp")) || !v("ville")) {
      retour.replaceChildren(boiteMessage({ type: "erreur", texte: "Nom, rue, code postal (5 chiffres) et ville sont obligatoires." }));
      return;
    }
    retour.replaceChildren(boiteMessage({ type: "info", texte: "Vérification de l'adresse…" }));
    const r = await appel("POST", "/tournees/du-jour/clients", { nom: v("nom"), voie: v("voie"), code_postal: v("cp"), ville: v("ville"),
      type_camion_requis: type, digicode: v("digicode") || null, notes_livraison: v("notes") || null });
    if (!r) return;
    if (r.status !== 201) { retour.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail })); return; }
    annoncer(r.data.avertissement ? "alerte" : "ok", r.data.livraison.client.nom + " ajouté à votre tournée. Appuyez sur le bouton d'optimisation pour l'intégrer au meilleur endroit." + (r.data.avertissement ? " " + r.data.avertissement : ""));
    await chargerTournee();
  } });
  contenu.append(
    h("label", { classe: "champ", for: "aj-nom" }, "Nom du client", champs.nom),
    h("label", { classe: "champ", for: "aj-voie" }, "Numéro et rue", champs.voie),
    h("div", { classe: "grille-cp" }, h("label", { classe: "champ", for: "aj-cp" }, "Code postal", champs.cp), h("label", { classe: "champ", for: "aj-ville" }, "Ville", champs.ville)),
    h("div", { classe: "rangee" }, bColis, bPalette),
    h("label", { classe: "champ", for: "aj-digicode" }, "Digicode (facultatif)", champs.digicode),
    h("label", { classe: "champ", for: "aj-notes" }, "Notes de livraison (facultatif)", champs.notes),
    retour, ajouter);
  return details;
}

// ---------------- carte (sans fond de carte en ligne : rien ne part vers un service tiers)
function carteGeo(t) {
  const L = 362, H = 220, marge = 22;
  const points = [];
  const depot = [t.depot.latitude, t.depot.longitude];
  points.push(depot);
  if (t.domicile) points.push([t.domicile.latitude, t.domicile.longitude]);
  t.livraisons.forEach((l) => { if (l.client.latitude !== null) points.push([l.client.latitude, l.client.longitude]); });
  const lats = points.map((p) => p[0]), lons = points.map((p) => p[1]);
  const latMoy = (Math.min(...lats) + Math.max(...lats)) / 2;
  const kx = Math.cos(latMoy * Math.PI / 180);
  const xMin = Math.min(...lons) * kx, xMax = Math.max(...lons) * kx, yMin = Math.min(...lats), yMax = Math.max(...lats);
  const echelle = Math.min((L - 2 * marge) / Math.max(xMax - xMin, 1e-6), (H - 2 * marge) / Math.max(yMax - yMin, 1e-6));
  const dx = (L - (xMax - xMin) * echelle) / 2, dy = (H - (yMax - yMin) * echelle) / 2;
  const proj = (lat, lon) => [dx + (lon * kx - xMin) * echelle, H - (dy + (lat - yMin) * echelle)];

  const svg = s("svg", { viewBox: "0 0 " + L + " " + H, preserveAspectRatio: "none", "aria-hidden": "true" });
  [[0, 0.3], [0, 0.7], [1, 0.25], [1, 0.75]].forEach(([vertical, f]) => svg.append(s("line", vertical
    ? { x1: L * f, y1: 0, x2: L * f, y2: H, stroke: "#FFFFFF", "stroke-width": 5 }
    : { x1: 0, y1: H * f, x2: L, y2: H * f, stroke: "#FFFFFF", "stroke-width": 5 })));
  const pDepot = proj(...depot);
  const trace = [pDepot];
  t.livraisons.filter((l) => l.statut !== "reporte" && l.client.latitude !== null && (l.ordre_passage !== null || !t.optimisee_le))
    .forEach((l) => trace.push(proj(l.client.latitude, l.client.longitude)));
  const pDomicile = t.domicile ? proj(t.domicile.latitude, t.domicile.longitude) : null;
  trace.push(pDomicile && t.retour === "domicile" ? pDomicile : pDepot);
  if (pDomicile) svg.append(s("polyline", { points: [pDomicile, pDepot].map((p) => p.join(",")).join(" "), fill: "none", stroke: "#15803D", "stroke-width": 2.5, "stroke-dasharray": "4 6" }));
  const attributs = { points: trace.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" "), fill: "none", "stroke-width": 3, "stroke-linejoin": "round",
    stroke: t.optimisee_le ? "#1F4E79" : "#6B7280" };
  if (!t.optimisee_le) attributs["stroke-dasharray"] = "6 8";
  svg.append(s("polyline", attributs));

  const boite = h("div", { classe: "carte-geo", role: "img", "aria-label": "Carte de la tournée : dépôt, clients numérotés et itinéraire" }, svg);
  const placer = (el, p) => { el.style.left = (p[0] / L * 100) + "%"; el.style.top = (p[1] / H * 100) + "%"; return el; };
  boite.append(placer(h("div", { classe: "repere", style: { background: "#16213A" }, texte: "D" }), pDepot));
  if (pDomicile) boite.append(placer(h("div", { classe: "repere", style: { background: "#15803D" } }, icone(["M3 11l9-7 9 7", "M5 10v10h14V10"], "#FFFFFF", 16)), pDomicile));
  t.livraisons.forEach((l, k) => {
    if (l.client.latitude === null) return;
    const el = h("div", { classe: "epingle", style: { background: STATUTS[l.statut][1] }, texte: String(l.ordre_passage || k + 1) });
    boite.append(placer(el, proj(l.client.latitude, l.client.longitude)));
  });
  return boite;
}

// ---------------- une livraison
function carteLivraison(l, demarragePossible, prochaine) {
  const c = l.client;
  const [libelle, couleur] = STATUTS[l.statut];
  const actif = l.statut === "a_livrer" || l.statut === "en_cours";
  const infos = ["Créneau " + (c.creneau_debut || "07:00").slice(0, 5) + "–" + (c.creneau_fin || "22:00").slice(0, 5)];
  if (l.heure_arrivee_prevue && actif) infos.push("passage prévu " + heure(l.heure_arrivee_prevue));
  if (l.temps_service_predit_s && actif) infos.push("≈ " + Math.round(l.temps_service_predit_s / 60) + " min sur place");
  infos.push(c.type_camion_requis === "hayon" ? "camion hayon (palette)" : "colis");

  const carte = h("article", { classe: "livraison", "aria-label": c.nom },
    h("div", { classe: "ligne-titre" },
      h("div", { classe: "nom", texte: (l.ordre_passage || "•") + ". " + c.nom + (l.ajout_a_la_volee ? " (ajouté en cours de tournée)" : "") }),
      h("div", { classe: "statut", style: { color: couleur }, texte: "● " + libelle })),
    h("div", { classe: "adresse", texte: c.adresse }),
    h("div", { classe: "petit", texte: infos.join(" · ") }));

  if (actif) {
    const acces = [];
    if (c.digicode) acces.push("Digicode " + c.digicode);
    if (c.etage !== null) acces.push("étage " + c.etage + (c.ascenseur === null ? "" : c.ascenseur ? " avec ascenseur" : " sans ascenseur"));
    if (acces.length) carte.append(h("div", { style: { fontSize: "13.5px", fontWeight: "500" }, texte: acces.join(" · ") }));
    if (c.notes_livraison || c.telephone) {
      carte.append(h("details", { classe: "details-contact" }, h("summary", { texte: "Notes de livraison et contact" }),
        c.notes_livraison ? h("p", { texte: c.notes_livraison }) : null,
        c.telephone ? h("p", {}, "Téléphone : ", h("a", { href: "tel:" + c.telephone.replace(/[^0-9+]/g, ""), texte: c.telephone })) : null));
    }
  }
  const remarques = c.remarques || [];
  if (remarques.length) carte.append(h("div", { classe: "remarque", texte: "Remarque du " + dateCourte(remarques[0].cree_le) + " : " + remarques[0].texte }));

  if (l.statut === "a_livrer") {
    carte.append(h("button", { type: "button", classe: "btn " + (prochaine && demarragePossible ? "btn-principal" : demarragePossible ? "btn-contour" : "btn-gris"),
      disabled: !demarragePossible, title: demarragePossible ? null : "Une livraison est déjà en cours : terminez-la ou annulez son démarrage.",
      texte: "Démarrer la livraison", onclick: () => action("POST", "/livraisons/" + l.id + "/demarrer") }));
  } else if (l.statut === "en_cours") {
    const choisie = etat.issue[l.id] || "livre";
    const choix = h("div", { classe: "trio", role: "radiogroup", "aria-label": "Résultat du passage" },
      ISSUES.map(([valeur, nom]) => h("button", { type: "button", role: "radio", "aria-checked": String(choisie === valeur),
        classe: "choix" + (choisie === valeur ? " actif" : ""), texte: nom, onclick: () => { etat.issue[l.id] = valeur; rendre(); } })));
    carte.append(
      h("div", { classe: "petit", texte: "Démarrée à " + heure(l.heure_debut_livraison) + " · Résultat du passage :" }),
      choix,
      h("div", { classe: "duo" },
        h("button", { type: "button", classe: "btn btn-principal", texte: "Terminer la livraison", onclick: () => {
          const issue = etat.issue[l.id] || "livre";
          const libelleIssue = { livre: "livré", absent: "absent", refuse: "refusé" }[issue];
          action("POST", "/livraisons/" + l.id + "/terminer", { issue }, c.nom + " : " + libelleIssue + ".");
        } }),
        h("button", { type: "button", classe: "btn", style: { fontSize: "13px", lineHeight: "1.2" }, texte: "Annuler le démarrage",
          onclick: () => action("POST", "/livraisons/" + l.id + "/annuler-demarrage", undefined, c.nom + " : démarrage annulé, la livraison est de nouveau à faire.") })));
  } else if (l.heure_fin_livraison) {
    carte.append(h("div", { classe: "petit", texte: heure(l.heure_debut_livraison) + " → " + heure(l.heure_fin_livraison) + " (" + Math.floor((l.temps_service_reel_s || 0) / 60) + " min sur place)" }));
  }
  carte.append(blocRemarques(l, remarques));
  return carte;
}

function blocRemarques(l, remarques) {
  const ouvert = etat.remarqueOuverte === l.id;
  const zone = h("div", { style: { display: "flex", flexDirection: "column", gap: "8px" } },
    h("button", { type: "button", classe: "btn-lien", "aria-expanded": String(ouvert),
      texte: ouvert ? "Fermer les remarques" : (remarques.length ? "Remarques (" + remarques.length + ") · ajouter" : "Ajouter une remarque sur cette adresse"),
      onclick: () => { etat.remarqueOuverte = ouvert ? null : l.id; rendre(); } }));
  if (!ouvert) return zone;
  if (remarques.length) {
    zone.append(h("div", { classe: "liste-remarques" }, remarques.map((r) => h("div", { classe: "ligne" },
      h("span", { texte: dateCourte(r.cree_le) + " — " + r.texte }),
      r.de_moi ? h("button", { type: "button", classe: "btn-lien", texte: "Supprimer",
        onclick: () => action("DELETE", "/remarques/" + r.id, undefined, "Remarque supprimée.") }) : null))));
  }
  const saisie = h("textarea", { id: "remarque-" + l.id, maxlength: 300, placeholder: "Ex. : portail fermé le matin, sonner au 2e étage" });
  zone.append(
    h("label", { classe: "champ", for: "remarque-" + l.id }, "Nouvelle remarque", saisie),
    h("p", { classe: "tres-petit", texte: "Uniquement des informations utiles à la livraison, factuelles. Pas d'avis sur le client ni d'information sur sa santé ou sa vie privée." }),
    h("button", { type: "button", classe: "btn btn-principal", texte: "Enregistrer la remarque", onclick: () => {
      if (saisie.value.trim().length < 3) { annoncer("erreur", "La remarque est trop courte."); rendre(); return; }
      etat.remarqueOuverte = null;
      action("POST", "/livraisons/" + l.id + "/remarques", { texte: saisie.value }, "Remarque enregistrée : elle s'affichera aux prochaines livraisons à cette adresse.");
    } }));
  return zone;
}

// --------------------------------------------------------------------------- démarrage
document.addEventListener("DOMContentLoaded", () => {
  rendre();
  // Session de 30 minutes : retour automatique à la connexion à l'expiration
  setInterval(() => {
    if (etat.jeton && etat.expireLe && etat.expireLe <= new Date()) {
      deconnexionLocale("Votre session a expiré (30 minutes). Veuillez vous reconnecter.");
    }
  }, 30000);
});
