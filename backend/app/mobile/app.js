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
  trajets: null, aSupprimer: null,
};
const POINTS = { depot: "Dépôt", domicile: "Domicile" };
const IDEES_ETAPES = ["Garage", "Contrôle technique", "Récupérer une pièce", "Station de lavage"];

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
    remarqueOuverte: null, domicile: null, trajets: null, aSupprimer: null, messageConnexion: message || null });
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
  if (r.status === 404) {
    // Pas de tournée aujourd'hui : on regarde si le livreur a préparé une journée de trajets
    const tr = await appel("GET", "/trajets/du-jour");
    if (!tr) return;
    etat.trajets = tr.status === 200 ? tr.data : null;
  }
  etat.ecran = "tournee";
  if (r.status !== 200 && r.status !== 404) annoncer("erreur", r.detail);
  rendre();
}

async function chargerTrajets() {
  const r = await appel("GET", "/trajets/du-jour");
  if (!r) return;
  if (r.status !== 200) { annoncer("erreur", r.detail); await chargerTournee(); return; }
  etat.trajets = r.data;
  if (r.data.tournee_attribuee) {
    annoncer("info", "Une tournée vous a été attribuée aujourd'hui : elle remplace vos trajets.");
    await chargerTournee();
    return;
  }
  etat.ecran = "trajets";
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

async function action(methode, chemin, corps, messageOk, recharger = chargerTournee) {
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
  await recharger();
}

// --------------------------------------------------------------------------- rendu
function rendre() {
  const app = document.getElementById("app");
  const ecrans = { connexion: ecranConnexion, verification: ecranVerification, enrolement: ecranEnrolement,
    codes: ecranCodes, nouveauMdp: ecranNouveauMdp, conditions: ecranConditions, tournee: ecranTournee, menu: ecranMenu, domicile: ecranDomicile,
    trajets: ecranTrajets };
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
    etat.ecran = { enrolement: "enrolement", nouveau_mot_de_passe: "nouveauMdp" }[r.data.etape] || "verification";
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
    h("p", { classe: "petit", style: { marginTop: "-14px" }, texte: "Première connexion : saisissez le code d'activation remis par votre planificateur (XXXX-XXXX). Vous choisirez ensuite votre mot de passe." }),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Se connecter" }),
    h("p", { classe: "petit", texte: "Après 5 erreurs, le compte est bloqué 15 minutes. Session limitée à 30 minutes." }));
}

function boutonRetourConnexion() {
  return h("button", { type: "button", classe: "btn", texte: "Annuler et revenir à la connexion",
    onclick: () => deconnexionLocale(null) });
}

// ---------------- première connexion : choix du mot de passe
const REGLES_MDP = [
  ["Au moins 12 caractères", (m) => m.length >= 12],
  ["Des minuscules et des majuscules", (m) => /[a-zà-ÿ]/.test(m) && /[A-ZÀ-Ý]/.test(m)],
  ["Au moins un chiffre", (m) => /[0-9]/.test(m)],
];

function ecranNouveauMdp() {
  const nouveau = h("input", { type: "password", id: "nouveau-mdp", autocomplete: "new-password", maxlength: 72 });
  const confirmation = h("input", { type: "password", id: "confirmation-mdp", autocomplete: "new-password", maxlength: 72 });
  const afficher = h("input", { type: "checkbox", id: "afficher-mdp" });
  afficher.addEventListener("change", () => { nouveau.type = confirmation.type = afficher.checked ? "text" : "password"; });
  const regles = REGLES_MDP.map(([libelle]) => h("li", { texte: "○ " + libelle }));
  const identiques = h("li", { texte: "○ Les deux saisies sont identiques" });
  const majRegles = () => {
    REGLES_MDP.forEach(([libelle, test], k) => {
      const ok = test(nouveau.value);
      regles[k].textContent = (ok ? "✓ " : "○ ") + libelle;
      regles[k].className = ok ? "regle-ok" : "";
    });
    const ok = nouveau.value !== "" && nouveau.value === confirmation.value;
    identiques.textContent = (ok ? "✓ " : "○ ") + "Les deux saisies sont identiques";
    identiques.className = ok ? "regle-ok" : "";
  };
  nouveau.addEventListener("input", majRegles);
  confirmation.addEventListener("input", majRegles);
  const erreur = h("div");
  const valider = async (e) => {
    e.preventDefault();
    if (!nouveau.value || nouveau.value !== confirmation.value) {
      erreur.replaceChildren(boiteMessage({ type: "erreur", texte: "Les deux mots de passe ne sont pas identiques." }));
      return;
    }
    const r = await api("POST", "/auth/mot-de-passe-initial", { nouveau: nouveau.value, confirmation: confirmation.value }, etat.preauth);
    if (r.status === 200) {
      nouveau.value = confirmation.value = "";
      etat.preauth = r.data.jeton_preauth;
      etat.ecran = r.data.etape === "enrolement" ? "enrolement" : "verification";
      rendre();
      return;
    }
    if (r.status === 401) { deconnexionLocale("Délai dépassé : reconnectez-vous avec votre code d'activation."); return; }
    erreur.replaceChildren(boiteMessage({ type: "erreur", texte: r.detail }));
  };
  setTimeout(() => nouveau.focus(), 50);
  return h("form", { classe: "page", onsubmit: valider },
    h("h1", { classe: "moyen", texte: "Choisissez votre mot de passe" }),
    h("p", { classe: "texte", texte: "Première connexion : ce mot de passe remplace le code d'activation. Vous seul le connaîtrez, le planificateur n'y a pas accès." }),
    erreur,
    h("label", { classe: "champ", for: "nouveau-mdp" }, "Nouveau mot de passe", nouveau),
    h("label", { classe: "champ", for: "confirmation-mdp" }, "Confirmez le mot de passe", confirmation),
    h("label", { classe: "case", for: "afficher-mdp" }, afficher, "Afficher les mots de passe"),
    h("ul", { classe: "regles" }, regles, identiques, h("li", { texte: "Sans votre identifiant" })),
    h("button", { type: "submit", classe: "btn btn-principal", texte: "Enregistrer et continuer" }),
    h("p", { classe: "petit", texte: "Étape suivante : activation de la double authentification avec un QR code." }),
    boutonRetourConnexion());
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
    !t ? h("button", { type: "button", classe: "btn btn-menu", texte: "Mes trajets de la journée", onclick: () => { etat.message = null; chargerTrajets(); } }) : null,
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
    const j = etat.trajets;
    const nb = j ? j.etapes.length : 0;
    const restantes = j ? j.etapes.filter((e) => !e.faite_le).length : 0;
    corps.append(boiteMessage({ type: "info", texte: "Aucune tournée ne vous est attribuée aujourd'hui." }),
      h("div", { classe: "livraison" },
        h("div", { classe: "nom", texte: "Des déplacements à faire aujourd'hui ?" }),
        h("div", { classe: "petit", texte: nb
          ? nb + " étape(s) prévue(s), " + restantes + " restante(s)" + (j.optimisee_le ? " · itinéraire optimisé" : " · pas encore optimisé") + "."
          : "Garage, contrôle technique, récupération d'une pièce… Saisissez vos adresses et l'application calcule le meilleur ordre de passage en respectant vos rendez-vous." }),
        h("button", { type: "button", classe: "btn btn-principal", texte: nb ? "Mes trajets de la journée" : "Organiser mes trajets de la journée", onclick: () => { etat.message = null; chargerTrajets(); } })),
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
const CARTE_L = 362, CARTE_H = 220;

/** Projection simple (latitude, longitude) → pixels qui fait tenir tous les points dans la carte. */
function projecteur(points, L = CARTE_L, H = CARTE_H, marge = 22) {
  const lats = points.map((p) => p[0]), lons = points.map((p) => p[1]);
  const latMoy = (Math.min(...lats) + Math.max(...lats)) / 2;
  const kx = Math.cos(latMoy * Math.PI / 180);
  const xMin = Math.min(...lons) * kx, xMax = Math.max(...lons) * kx, yMin = Math.min(...lats), yMax = Math.max(...lats);
  const echelle = Math.min((L - 2 * marge) / Math.max(xMax - xMin, 1e-6), (H - 2 * marge) / Math.max(yMax - yMin, 1e-6));
  const dx = (L - (xMax - xMin) * echelle) / 2, dy = (H - (yMax - yMin) * echelle) / 2;
  return (lat, lon) => [dx + (lon * kx - xMin) * echelle, H - (dy + (lat - yMin) * echelle)];
}

function fondCarte(L = CARTE_L, H = CARTE_H) {
  const svg = s("svg", { viewBox: "0 0 " + L + " " + H, preserveAspectRatio: "none", "aria-hidden": "true" });
  [[0, 0.3], [0, 0.7], [1, 0.25], [1, 0.75]].forEach(([vertical, f]) => svg.append(s("line", vertical
    ? { x1: L * f, y1: 0, x2: L * f, y2: H, stroke: "#FFFFFF", "stroke-width": 5 }
    : { x1: 0, y1: H * f, x2: L, y2: H * f, stroke: "#FFFFFF", "stroke-width": 5 })));
  return svg;
}

function placer(el, p, L = CARTE_L, H = CARTE_H) {
  el.style.left = (p[0] / L * 100) + "%"; el.style.top = (p[1] / H * 100) + "%"; return el;
}

function carteGeo(t) {
  const points = [];
  const depot = [t.depot.latitude, t.depot.longitude];
  points.push(depot);
  if (t.domicile) points.push([t.domicile.latitude, t.domicile.longitude]);
  t.livraisons.forEach((l) => { if (l.client.latitude !== null) points.push([l.client.latitude, l.client.longitude]); });
  const proj = projecteur(points);
  const svg = fondCarte();
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

// ---------------- journée de trajets (jours sans tournée : garage, contrôle technique, pièce à récupérer…)
function hhmm(t) { return t ? t.slice(0, 5) : ""; }
function ajouterMinutes(debut, minutes) {
  const [hh, mm] = debut.split(":").map(Number);
  const total = hh * 60 + mm + minutes;
  return String(Math.floor(total / 60) % 24).padStart(2, "0") + ":" + String(total % 60).padStart(2, "0");
}
function cleDuPoint(p) { return p.nom === "Domicile" ? "domicile" : "depot"; }

function ecranTrajets() {
  const j = etat.trajets;
  const entete = h("div", { classe: "entete" },
    h("button", { type: "button", classe: "btn-icone", "aria-label": "Retour à l'accueil", onclick: chargerTournee },
      icone(["M15 5l-7 7 7 7"])),
    h("div", { style: { flex: "1" } },
      h("div", { classe: "titre", texte: "Mes trajets du jour" }),
      h("div", { classe: "sous-titre", texte: new Date(j.date_journee + "T12:00:00").toLocaleDateString("fr-FR") + " · journée sans tournée" })),
    h("button", { type: "button", classe: "btn-icone", "aria-label": "Ouvrir le menu", onclick: () => { etat.ecran = "menu"; rendre(); } },
      icone(["M4 7h16", "M4 12h16", "M4 17h16"])));

  const corps = h("div", { classe: "defilement" });
  corps.append(boiteMessage(etat.message, () => { etat.message = null; rendre(); }) || "");
  const etapes = j.etapes;
  const aFaire = etapes.filter((e) => !e.faite_le);
  const faites = etapes.filter((e) => e.faite_le);
  const numero = (e) => etapes.indexOf(e) + 1;

  corps.append(blocReglages(j));

  const indicateur = (libelle, valeur) => h("div", { classe: "indicateur" }, h("div", { classe: "libelle", texte: libelle }), h("div", { classe: "valeur", texte: valeur }));
  const optimise = !!j.optimisee_le;
  corps.append(h("div", { classe: "indicateurs" },
    indicateur("Étapes faites", faites.length + " / " + etapes.length),
    indicateur("Distance", optimise && j.distance_km !== null ? nombre(j.distance_km) + " km" : "—"),
    indicateur("Retour " + (cleDuPoint(j.point_arrivee) === "domicile" ? "domicile" : "dépôt"),
      optimise && j.duree_min !== null ? ajouterMinutes(hhmm(j.heure_depart), j.duree_min) : "—")));

  if (aFaire.length) {
    corps.append(h("button", { type: "button", classe: "btn btn-principal", disabled: etat.calcul,
      texte: etat.calcul ? "Calcul de l'itinéraire…" : (faites.length ? "Recalculer l'itinéraire restant" : "Optimiser mon itinéraire"),
      onclick: optimiserTrajets }));
    if (!optimise) corps.append(h("p", { classe: "petit", texte: "Étapes dans l'ordre de saisie : appuyez sur le bouton ci-dessus pour obtenir le meilleur ordre et les heures de passage, en respectant vos rendez-vous." }));
  } else if (etapes.length) {
    corps.append(boiteMessage({ type: "ok", texte: "Toutes vos étapes sont faites. Bonne fin de journée !" }));
  }

  corps.append(formulaireEtape(j));

  if (etapes.length) {
    corps.append(carteTrajets(j), h("p", { classe: "tres-petit", style: { marginTop: "-6px" },
      texte: "D : dépôt. " + (optimise ? "Trait bleu : itinéraire optimisé." : "Pointillés gris : ordre de saisie.") + " Tracé à vol d'oiseau." }));
  }
  const prochaine = aFaire[0];
  if (aFaire.length) corps.append(h("h2", { classe: "section", texte: "À faire (" + aFaire.length + ")" }));
  aFaire.forEach((e) => corps.append(carteEtape(e, numero(e), prochaine && e.id === prochaine.id)));
  if (faites.length) corps.append(h("h2", { classe: "section", texte: "Faites (" + faites.length + ")" }));
  faites.forEach((e) => corps.append(carteEtape(e, numero(e), false)));
  corps.append(h("p", { classe: "tres-petit", texte: "Vos étapes sont visibles par le planificateur, en consultation seulement. L'adresse de votre domicile ne lui est jamais montrée." }));
  return h("div", { classe: "ecran" }, entete, corps);
}

function blocReglages(j) {
  const options = ["depot"].concat(j.domicile_disponible ? ["domicile"] : []);
  const ligne = (cle, libelle, actuel) => h("div", { classe: "reglage" },
    h("span", { classe: "petit", texte: libelle }),
    h("div", { classe: "rangee" }, options.map((o) => h("button", { type: "button", classe: "choix" + (actuel === o ? " actif" : ""),
      "aria-pressed": String(actuel === o), texte: POINTS[o], onclick: () => { if (actuel !== o) reglerTrajets({ [cle]: o }); } }))));
  const champHeure = h("input", { type: "time", id: "tr-heure", value: hhmm(j.heure_depart), step: 300 });
  champHeure.addEventListener("change", () => { if (champHeure.value) reglerTrajets({ heure_depart: champHeure.value }); });
  const bloc = h("div", { classe: "livraison" }, h("div", { classe: "nom", texte: "Départ et retour" }));
  if (j.domicile_disponible) bloc.append(ligne("depart", "Je pars de", cleDuPoint(j.point_depart)), ligne("retour", "Je termine à", cleDuPoint(j.point_arrivee)));
  else bloc.append(h("div", { classe: "petit", texte: "Départ du dépôt et retour au dépôt." }));
  bloc.append(h("label", { classe: "champ", for: "tr-heure" }, "Heure de départ", champHeure));
  if (!j.domicile_disponible && etat.profil && etat.profil.type_livreur === "interne") {
    bloc.append(h("div", { classe: "petit" }, "Pour partir de chez vous ou y terminer : ",
      h("button", { type: "button", classe: "btn-lien", style: { display: "inline", minHeight: "0", fontSize: "12.5px" }, texte: "renseignez votre adresse", onclick: ouvrirDomicile }), "."));
  }
  return bloc;
}

async function reglerTrajets(modif) {
  const j = etat.trajets;
  const corps = Object.assign({ depart: cleDuPoint(j.point_depart), retour: cleDuPoint(j.point_arrivee), heure_depart: hhmm(j.heure_depart) }, modif);
  const message = j.optimisee_le && j.etapes.some((e) => !e.faite_le) ? "Réglage enregistré : appuyez sur « Optimiser » pour recalculer l'itinéraire." : null;
  await action("PUT", "/trajets/du-jour/reglages", corps, message, chargerTrajets);
}

function formulaireEtape(j) {
  const nb = j.etapes.length;
  const details = h("details", { classe: "bloc", open: nb === 0 }, h("summary", { texte: "Ajouter une étape (" + nb + " / " + j.max_etapes + ")" }));
  const contenu = h("div", { classe: "contenu" });
  details.append(contenu);
  if (nb >= j.max_etapes) {
    contenu.append(boiteMessage({ type: "alerte", texte: "Nombre maximal d'étapes atteint pour la journée." }));
    return details;
  }
  const champs = {
    libelle: h("input", { type: "text", id: "tr-libelle", maxlength: 80, placeholder: "Ex. : Contrôle technique" }),
    voie: h("input", { type: "text", id: "tr-voie", maxlength: 200 }),
    cp: h("input", { type: "text", id: "tr-cp", maxlength: 5, inputmode: "numeric" }),
    ville: h("input", { type: "text", id: "tr-ville", maxlength: 100 }),
    debut: h("input", { type: "time", id: "tr-debut", step: 300 }),
    fin: h("input", { type: "time", id: "tr-fin", step: 300 }),
    duree: h("input", { type: "number", id: "tr-duree", min: 1, max: 480, inputmode: "numeric", value: j.duree_defaut_min }),
  };
  const idees = h("div", { classe: "puces" }, IDEES_ETAPES.map((x) => h("button", { type: "button", classe: "puce", texte: x,
    onclick: () => { champs.libelle.value = x; champs.voie.focus(); } })));
  const zoneRdv = h("div", { classe: "grille-2", hidden: true },
    h("label", { classe: "champ", for: "tr-debut" }, "De", champs.debut),
    h("label", { classe: "champ", for: "tr-fin" }, "À", champs.fin));
  const caseRdv = h("input", { type: "checkbox", id: "tr-rdv" });
  caseRdv.addEventListener("change", () => { zoneRdv.hidden = !caseRdv.checked; });
  const retour = h("div");
  const erreur = (texte) => retour.replaceChildren(boiteMessage({ type: "erreur", texte }));
  const ajouter = h("button", { type: "button", classe: "btn btn-principal", texte: "Ajouter l'étape", onclick: async () => {
    const v = (k) => champs[k].value.trim();
    if (v("libelle").length < 2 || !v("voie") || !/^[0-9]{5}$/.test(v("cp")) || !v("ville")) {
      erreur("Nom de l'étape, rue, code postal (5 chiffres) et ville sont obligatoires."); return;
    }
    const rdv = caseRdv.checked;
    if (rdv && (!v("debut") || !v("fin") || v("fin") <= v("debut"))) {
      erreur("Indiquez l'heure de début et de fin du rendez-vous (la fin après le début)."); return;
    }
    const duree = Number(v("duree") || j.duree_defaut_min);
    if (!Number.isInteger(duree) || duree < 1 || duree > 480) { erreur("La durée sur place doit être comprise entre 1 et 480 minutes."); return; }
    retour.replaceChildren(boiteMessage({ type: "info", texte: "Vérification de l'adresse…" }));
    const r = await appel("POST", "/trajets/du-jour/etapes", { libelle: v("libelle"), voie: v("voie"), code_postal: v("cp"), ville: v("ville"),
      rdv_debut: rdv ? v("debut") : null, rdv_fin: rdv ? v("fin") : null, duree_min: duree });
    if (!r) return;
    if (r.status !== 201) { erreur(r.detail); return; }
    annoncer(r.data.avertissement ? "alerte" : "ok", "« " + r.data.etape.libelle + " » ajouté. Appuyez sur « Optimiser » pour calculer le meilleur ordre de passage." + (r.data.avertissement ? " " + r.data.avertissement : ""));
    await chargerTrajets();
  } });
  contenu.append(
    h("label", { classe: "champ", for: "tr-libelle" }, "Étape", champs.libelle),
    idees,
    h("label", { classe: "champ", for: "tr-voie" }, "Numéro et rue", champs.voie),
    h("div", { classe: "grille-cp" }, h("label", { classe: "champ", for: "tr-cp" }, "Code postal", champs.cp), h("label", { classe: "champ", for: "tr-ville" }, "Ville", champs.ville)),
    h("label", { classe: "case", for: "tr-rdv" }, caseRdv, "Rendez-vous à heure fixe"),
    zoneRdv,
    h("label", { classe: "champ", for: "tr-duree" }, "Temps sur place (minutes)", champs.duree),
    retour, ajouter);
  return details;
}

function carteTrajets(j) {
  const dep = [j.point_depart.latitude, j.point_depart.longitude];
  const arr = [j.point_arrivee.latitude, j.point_arrivee.longitude];
  const proj = projecteur([dep, arr].concat(j.etapes.map((e) => [e.latitude, e.longitude])));
  const svg = fondCarte();
  const pDep = proj(...dep), pArr = proj(...arr);
  const trace = [pDep].concat(j.etapes.map((e) => proj(e.latitude, e.longitude)), [pArr]);
  const attributs = { points: trace.map((p) => p.map((v) => v.toFixed(1)).join(",")).join(" "), fill: "none", "stroke-width": 3,
    "stroke-linejoin": "round", stroke: j.optimisee_le ? "#1F4E79" : "#6B7280" };
  if (!j.optimisee_le) attributs["stroke-dasharray"] = "6 8";
  svg.append(s("polyline", attributs));
  const boite = h("div", { classe: "carte-geo", role: "img", "aria-label": "Carte des trajets : point de départ, étapes numérotées et itinéraire" }, svg);
  const repere = (pt) => pt.nom === "Domicile"
    ? h("div", { classe: "repere", style: { background: "#15803D" } }, icone(["M3 11l9-7 9 7", "M5 10v10h14V10"], "#FFFFFF", 16))
    : h("div", { classe: "repere", style: { background: "#16213A" }, texte: "D" });
  boite.append(placer(repere(j.point_depart), pDep));
  if (j.point_arrivee.nom !== j.point_depart.nom) boite.append(placer(repere(j.point_arrivee), pArr));
  j.etapes.forEach((e, k) => boite.append(placer(h("div", { classe: "epingle",
    style: { background: e.faite_le ? "#15803D" : "#1F4E79" }, texte: String(k + 1) }), proj(e.latitude, e.longitude))));
  return boite;
}

function carteEtape(e, numero, prochaine) {
  const faite = !!e.faite_le;
  const infos = [];
  if (e.rdv_debut) infos.push("RDV " + hhmm(e.rdv_debut) + "–" + hhmm(e.rdv_fin));
  infos.push(e.duree_min + " min sur place");
  if (e.heure_prevue && !faite) infos.push("arrivée prévue " + heure(e.heure_prevue));
  const carte = h("article", { classe: "livraison", "aria-label": e.libelle },
    h("div", { classe: "ligne-titre" },
      h("div", { classe: "nom", texte: numero + ". " + e.libelle }),
      h("div", { classe: "statut", style: { color: faite ? "#15803D" : "#1F4E79" }, texte: faite ? "● Faite" : "● À faire" })),
    h("div", { classe: "adresse", texte: e.adresse }),
    h("div", { classe: "petit", texte: infos.join(" · ") }));
  if (!faite && e.rdv_fin && e.heure_prevue && heure(e.heure_prevue) > hhmm(e.rdv_fin)) {
    carte.append(h("div", { classe: "itineraire alerte", texte: "Rendez-vous risqué : arrivée prévue après " + hhmm(e.rdv_fin) + "." }));
  }
  if (e.position_approximative) carte.append(h("div", { classe: "tres-petit", texte: "Adresse localisée au centre de la commune : heure approximative." }));

  if (faite) {
    carte.append(h("div", { classe: "petit", texte: "Faite à " + heure(e.faite_le) }),
      h("button", { type: "button", classe: "btn-lien", texte: "Annuler (cliqué par erreur)",
        onclick: () => action("POST", "/trajets/etapes/" + e.id + "/a-faire", undefined, "« " + e.libelle + " » est de nouveau à faire.", chargerTrajets) }));
  } else if (etat.aSupprimer === e.id) {
    carte.append(h("div", { classe: "petit", texte: "Supprimer cette étape ?" }),
      h("div", { classe: "duo" },
        h("button", { type: "button", classe: "btn btn-danger", texte: "Oui, supprimer", onclick: () => {
          etat.aSupprimer = null;
          action("DELETE", "/trajets/etapes/" + e.id, undefined, "Étape supprimée.", chargerTrajets);
        } }),
        h("button", { type: "button", classe: "btn", texte: "Garder", onclick: () => { etat.aSupprimer = null; rendre(); } })));
  } else {
    carte.append(h("div", { classe: "duo" },
      h("button", { type: "button", classe: "btn " + (prochaine ? "btn-principal" : "btn-contour"), texte: "Étape faite",
        onclick: () => action("POST", "/trajets/etapes/" + e.id + "/faite", undefined, "« " + e.libelle + " » : faite.", chargerTrajets) }),
      h("button", { type: "button", classe: "btn", texte: "Supprimer", onclick: () => { etat.aSupprimer = e.id; rendre(); } })));
  }
  return carte;
}

async function optimiserTrajets() {
  if (etat.occupe) return;
  etat.occupe = true;
  etat.calcul = true;
  rendre();
  try {
    const r = await appel("POST", "/trajets/du-jour/optimiser");
    if (!r) return;
    if (r.status !== 200) { annoncer("erreur", r.detail); }
    else {
      const res = r.data;
      let texte = (res.mode === "restant" ? "Itinéraire restant recalculé depuis votre dernière étape" : "Itinéraire optimisé")
        + " en " + nombre(res.duree_calcul_s) + " s : " + nombre(res.distance_apres_km) + " km au lieu de " + nombre(res.distance_avant_km) + " km dans l'ordre de saisie.";
      if (res.nb_retards) texte += " Attention : " + res.nb_retards + " rendez-vous risque(nt) d'être manqué(s).";
      annoncer(res.nb_retards ? "alerte" : "ok", texte);
    }
  } finally {
    etat.occupe = false;
    etat.calcul = false;
  }
  await chargerTrajets();
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
