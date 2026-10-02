"""Interface ESIS (Streamlit) — connexion sécurisée (module 1), tournée du livreur (modules 2 et 3),
espace administrateur (module 4, dans espace_admin.py).

Le jeton de session reste côté serveur Streamlit (st.session_state) : il n'est jamais
stocké dans le navigateur (ni cookie, ni localStorage).
"""
import base64
import html
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import folium
import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_folium import st_folium

import espace_admin

API_URL = os.environ.get("API_URL", "http://api:8000")
FUSEAU = ZoneInfo(os.environ.get("TZ", "Europe/Paris"))
DERRIERE_PROXY = os.environ.get("ESIS_DERRIERE_PROXY") == "1"   # production : derrière Caddy (HTTPS)

# Écran large pour l'administrateur (tableaux, carte), étroit pour le livreur (smartphone)
st.set_page_config(page_title="ESIS Tournées",
                   layout="wide" if st.session_state.get("role") == "admin" else "centered")

# L'interface est déjà en français : on déclare la langue et on interdit la traduction automatique
# du navigateur. Sinon Chrome réécrit les textes de la page, ce qui casse l'affichage de
# Streamlit (erreur « NotFoundError: removeChild ») et déforme heures et nombres (09h00, 49,1).
components.html(
    "<script>const r = window.parent.document.documentElement;"
    "r.lang = 'fr'; r.setAttribute('translate', 'no'); r.classList.add('notranslate');</script>",
    height=0,
)

ETAT_INITIAL = {"etape": "identifiants", "preauth": None, "jeton": None, "expire_le": None,
                "role": None, "codes_secours": None, "enrolement": None}
for cle, valeur in ETAT_INITIAL.items():
    st.session_state.setdefault(cle, valeur)


def reinitialiser(message: str | None = None) -> None:
    for cle, valeur in ETAT_INITIAL.items():
        st.session_state[cle] = valeur
    if message:
        st.session_state["message"] = message


def appel_api(methode: str, chemin: str, jeton: str | None = None, timeout: int = 10,
              **kwargs) -> requests.Response | None:
    entetes = {}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    navigateur = getattr(st, "context", None) and st.context.headers.get("User-Agent")
    if navigateur:                      # transmis pour le journal de sécurité
        entetes["User-Agent"] = navigateur
    if DERRIERE_PROXY:
        # En production, Caddy indique l'adresse IP réelle du client (journal, détection d'anomalies).
        # Seule la dernière valeur, ajoutée par Caddy lui-même, est retenue (non falsifiable).
        transmis = st.context.headers.get("X-Forwarded-For")
        if transmis:
            entetes["X-Forwarded-For"] = transmis.split(",")[-1].strip()
    try:
        return requests.request(methode, f"{API_URL}{chemin}", headers=entetes, timeout=timeout, **kwargs)
    except requests.RequestException:
        st.error("Le serveur ne répond pas. Réessayez dans quelques instants.")
        return None


def afficher_erreur(reponse: requests.Response) -> None:
    if reponse.status_code == 429:
        st.error("Trop de tentatives. Patientez une minute avant de réessayer.")
        return
    try:
        detail = reponse.json().get("detail")
    except ValueError:
        detail = None
    st.error(detail if isinstance(detail, str) else "Saisie invalide.")


def ouvrir_session(donnees: dict) -> None:
    st.session_state.jeton = donnees["access_token"]
    st.session_state.role = donnees["role"]
    st.session_state.expire_le = datetime.fromisoformat(donnees["expire_le"])
    st.session_state.preauth = None


# --------------------------------------------------------------------------- écrans
def ecran_identifiants() -> None:
    st.title("ESIS — Connexion")
    if msg := st.session_state.pop("message", None):
        st.info(msg)
    with st.form("connexion"):
        identifiant = st.text_input("Identifiant", autocomplete="username")
        mdp = st.text_input("Mot de passe", type="password", autocomplete="current-password")
        valider = st.form_submit_button("Se connecter", width="stretch")
    if valider:
        if not identifiant.strip() or not mdp:
            st.error("Veuillez renseigner l'identifiant et le mot de passe.")
            return
        r = appel_api("POST", "/auth/connexion", json={"identifiant": identifiant, "mot_de_passe": mdp})
        if r is None:
            return
        if r.status_code != 200:
            afficher_erreur(r)
            return
        st.session_state.preauth = r.json()["jeton_preauth"]
        st.session_state.etape = r.json()["etape"]
        st.rerun()


def bouton_annuler() -> None:
    if st.button("Annuler et revenir à la connexion"):
        reinitialiser()
        st.rerun()


def ecran_enrolement() -> None:
    st.title("Activer la double authentification")
    st.write("Première connexion : la double authentification est obligatoire.")
    if st.session_state.enrolement is None:
        r = appel_api("POST", "/auth/2fa/enrolement", st.session_state.preauth)
        if r is None:
            return
        if r.status_code != 200:
            reinitialiser("Délai dépassé, veuillez vous reconnecter.")
            st.rerun()
        st.session_state.enrolement = r.json()
    donnees = st.session_state.enrolement

    st.markdown("1. Installez **Google Authenticator** ou **Microsoft Authenticator** sur votre téléphone.\n"
                "2. Scannez ce QR code avec l'application.\n"
                "3. Saisissez le code à 6 chiffres affiché.")
    st.image(base64.b64decode(donnees["qr_code_png_base64"]), width=220)
    with st.expander("Impossible de scanner ? Saisir la clé manuellement"):
        st.code(donnees["cle_manuelle"], language=None)

    with st.form("activation"):
        code = st.text_input("Code à 6 chiffres", max_chars=6)
        valider = st.form_submit_button("Activer", width="stretch")
    if valider:
        r = appel_api("POST", "/auth/2fa/activation", st.session_state.preauth, json={"code": code.strip()})
        if r is not None and r.status_code == 200:
            ouvrir_session(r.json())
            st.session_state.codes_secours = r.json()["codes_secours"]
            st.session_state.etape = "codes_secours"
            st.rerun()
        elif r is not None:
            afficher_erreur(r)
    bouton_annuler()


def ecran_codes_secours() -> None:
    st.title("Vos codes de secours")
    st.warning("Ces codes ne seront plus jamais affichés. Conservez-les hors de votre téléphone "
               "(papier, gestionnaire de mots de passe). Chacun remplace une seule fois le code "
               "de l'application si vous perdez votre téléphone.")
    st.code("\n".join(st.session_state.codes_secours), language=None)
    confirme = st.checkbox("J'ai conservé mes codes de secours")
    if st.button("Continuer", disabled=not confirme, width="stretch"):
        st.session_state.codes_secours = None
        st.session_state.etape = "connecte"
        st.rerun()


def ecran_verification() -> None:
    st.title("Vérification en deux étapes")
    with st.form("verification"):
        code = st.text_input("Code à 6 chiffres de votre application d'authentification",
                             help="Téléphone perdu ? Saisissez un de vos codes de secours (XXXXX-XXXXX).")
        valider = st.form_submit_button("Valider", width="stretch")
    if valider:
        if not code.strip():
            st.error("Veuillez saisir le code.")
        else:
            r = appel_api("POST", "/auth/2fa/verification", st.session_state.preauth,
                          json={"code": code.strip()})
            if r is not None and r.status_code == 200:
                ouvrir_session(r.json())
                st.session_state.etape = "connecte"
                st.rerun()
            elif r is not None and r.status_code == 401 and "Session" in r.text:
                reinitialiser("Délai dépassé, veuillez vous reconnecter.")
                st.rerun()
            elif r is not None:
                afficher_erreur(r)
    bouton_annuler()


def ecran_connecte() -> None:
    if st.session_state.expire_le <= datetime.now(timezone.utc):
        reinitialiser("Votre session a expiré (30 minutes). Veuillez vous reconnecter.")
        st.rerun()
    r = appel_api("GET", "/auth/moi", st.session_state.jeton)
    if r is None:
        return
    if r.status_code == 401:
        reinitialiser("Votre session a expiré. Veuillez vous reconnecter.")
        st.rerun()
    profil = r.json()

    with st.sidebar:
        st.write(f"**{profil['prenom']} {profil['nom']}**")
        st.caption(f"Rôle : {profil['role']}")
        restant = int((st.session_state.expire_le - datetime.now(timezone.utc)).total_seconds() // 60)
        st.caption(f"Session valable encore {restant} min")
        if st.button("Se déconnecter", width="stretch"):
            appel_api("POST", "/auth/deconnexion", st.session_state.jeton)
            reinitialiser("Vous êtes déconnecté.")
            st.rerun()

    if profil["role"] == "admin":
        espace_admin.ecran_admin(profil, SimpleNamespace(
            appel_api=appel_api, reinitialiser=reinitialiser, carte_tournee=carte_tournee,
            heure_locale=heure_locale, fuseau=FUSEAU))
    else:
        ecran_tournee(profil)


# --------------------------------------------------------------------------- module 2 : tournée
STATUTS = {  # libellé, couleur (carte et pastilles)
    "a_livrer": ("À livrer", "#2563eb"),
    "en_cours": ("En cours", "#f59e0b"),
    "livre": ("Livré", "#16a34a"),
    "absent": ("Absent", "#dc2626"),
    "refuse": ("Refusé", "#dc2626"),
    "reporte": ("À reporter", "#7c3aed"),
}
ISSUES = {"Livré": "livre", "Absent": "absent", "Refusé": "refuse"}


def nombre_fr(valeur: float, decimales: int = 1) -> str:
    """57.0 -> « 57,0 » (affichage à la française)."""
    return f"{valeur:.{decimales}f}".replace(".", ",")


def heure_locale(iso: str | None) -> str:
    return datetime.fromisoformat(iso).astimezone(FUSEAU).strftime("%H:%M") if iso else "—"


def action_tournee(chemin: str, message_ok: str | None = None, **kwargs) -> None:
    """Appelle l'API puis recharge l'écran ; le message est affiché après le rechargement."""
    r = appel_api("POST", chemin, st.session_state.jeton, **kwargs)
    if r is None:
        return
    if r.status_code == 401:
        reinitialiser("Votre session a expiré. Veuillez vous reconnecter.")
    elif r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = None
        st.session_state.message_tournee = ("error", detail if isinstance(detail, str) else "Action refusée.")
    elif message_ok:
        st.session_state.message_tournee = ("success", message_ok)
    st.rerun()


def optimiser() -> None:
    with st.spinner("Calcul de l'itinéraire (créneaux, temps de service, véhicule)..."):
        r = appel_api("POST", "/tournees/du-jour/optimiser", st.session_state.jeton, timeout=30)
    if r is None:
        return
    if r.status_code != 200:
        afficher_erreur(r)
        return
    res = r.json()
    duree, apres, avant = (nombre_fr(res[k]) for k in ("duree_calcul_s", "distance_apres_km",
                                                         "distance_avant_km"))
    if res["mode"] == "restant":
        texte = (f"Itinéraire restant recalculé depuis votre position en {duree} s : "
                 f"{apres} km au lieu de {avant} km.")
    else:
        texte = f"Itinéraire optimisé en {duree} s : {apres} km au lieu de {avant} km dans l'ordre initial."
    if res.get("temps_service") == "modele":
        texte += " Temps sur place estimés client par client par le modèle d'apprentissage."
    if res["nb_retards"]:
        texte += f" Attention : {res['nb_retards']} créneau(x) risquent d'être dépassés."
    if res["a_reporter"]:
        texte += (f" {len(res['a_reporter'])} client(s) à reporter (véhicule inadapté ou créneau "
                  "impossible) : le planificateur est invité à les replanifier.")
    st.session_state.message_tournee = ("warning" if res["a_reporter"] or res["nb_retards"] else "success",
                                        texte)
    st.rerun()


def carte_tournee(tournee: dict) -> None:
    depot = tournee["depot"]
    carte = folium.Map(location=[depot["latitude"], depot["longitude"]], zoom_start=12,
                       control_scale=True)
    folium.Marker([depot["latitude"], depot["longitude"]], tooltip=f"Départ : {depot['nom']}",
                  icon=folium.Icon(color="black", icon="home")).add_to(carte)
    trace = [(depot["latitude"], depot["longitude"])]
    for rang, l in enumerate(tournee["livraisons"], start=1):
        c = l["client"]
        if c["latitude"] is None:
            continue
        numero = l["ordre_passage"] or rang
        couleur = STATUTS[l["statut"]][1]
        pastille = (f'<div style="background:{couleur};color:#fff;border:2px solid #fff;border-radius:50%;'
                    f'width:26px;height:26px;line-height:22px;text-align:center;font:600 13px sans-serif;'
                    f'box-shadow:0 0 4px rgba(0,0,0,.5)">{numero}</div>')
        folium.Marker([c["latitude"], c["longitude"]],
                      tooltip=f"{numero}. {html.escape(c['nom'])} ({(c['creneau_debut'] or '')[:5]}-{(c['creneau_fin'] or '')[:5]})",
                      icon=folium.DivIcon(html=pastille, icon_size=(26, 26), icon_anchor=(13, 13))).add_to(carte)
        if l["statut"] != "reporte":
            trace.append((c["latitude"], c["longitude"]))
    if len(trace) > 1:
        optimisee = tournee["optimisee_le"] is not None
        folium.PolyLine(trace + [trace[0]], weight=3, opacity=0.8,
                        color="#2563eb" if optimisee else "#6b7280",
                        dash_array=None if optimisee else "6 8").add_to(carte)
        lats, lons = [p[0] for p in trace], [p[1] for p in trace]
        carte.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]], padding=(20, 20))
    st_folium(carte, height=380, use_container_width=True, returned_objects=[], key="carte_tournee")
    st.caption("Tracé à vol d'oiseau entre les arrêts. Pointillés gris : ordre initial ; "
               "trait bleu : ordre optimisé.")


def carte_livraison(l: dict, rang: int, demarrage_possible: bool, prochaine: bool) -> None:
    c = l["client"]
    libelle, couleur = STATUTS[l["statut"]]
    with st.container(border=True):
        # Nom échappé : affiché en HTML, il ne doit pas pouvoir injecter de balises (XSS)
        ajout = " &nbsp; <span style='color:#6b7280'>(ajouté en cours de tournée)</span>" \
            if l["ajout_a_la_volee"] else ""
        st.markdown(f"**{l['ordre_passage'] or rang}. {html.escape(c['nom'])}** &nbsp; "
                    f"<span style='color:{couleur};font-weight:600'>● {libelle}</span>{ajout}",
                    unsafe_allow_html=True)
        st.write(c["adresse"])           # adresse nettoyée complète (voie, code postal, ville)
        infos = [f"Créneau {(c['creneau_debut'] or '07:00')[:5]}–{(c['creneau_fin'] or '22:00')[:5]}"]
        if l["heure_arrivee_prevue"]:
            infos.append(f"passage prévu {heure_locale(l['heure_arrivee_prevue'])}")
        if l["temps_service_predit_s"] and l["statut"] in ("a_livrer", "en_cours"):
            infos.append(f"≈ {round(l['temps_service_predit_s'] / 60)} min sur place")
        infos.append("camion hayon (palette)" if c["type_camion_requis"] == "hayon" else "colis")
        st.caption(" · ".join(infos))

        if l["statut"] in ("a_livrer", "en_cours"):
            acces = []
            if c["digicode"]:
                acces.append(f"Digicode **{c['digicode']}**")
            if c["etage"] is not None:
                acces.append(f"étage {c['etage']}" + ("" if c["ascenseur"] is None
                                                       else " avec ascenseur" if c["ascenseur"]
                                                       else " sans ascenseur"))
            if acces:
                st.markdown(" · ".join(acces))
            if c["notes_livraison"] or c["telephone"]:
                with st.expander("Notes de livraison et contact"):
                    if c["notes_livraison"]:
                        st.write(c["notes_livraison"])
                    if c["telephone"]:
                        st.write(f"Téléphone : {c['telephone']}")

        if l["statut"] == "a_livrer" and demarrage_possible:
            if st.button("Démarrer la livraison", key=f"demarrer_{l['id']}", width="stretch",
                         type="primary" if prochaine else "secondary"):
                action_tournee(f"/livraisons/{l['id']}/demarrer")
        elif l["statut"] == "en_cours":
            st.caption(f"Démarrée à {heure_locale(l['heure_debut_livraison'])}")
            issue = st.radio("Résultat du passage", list(ISSUES), horizontal=True, key=f"issue_{l['id']}")
            if st.button("Terminer la livraison", key=f"terminer_{l['id']}", type="primary",
                         width="stretch"):
                action_tournee(f"/livraisons/{l['id']}/terminer", f"{c['nom']} : {issue.lower()}.",
                               json={"issue": ISSUES[issue]})
        elif l["heure_fin_livraison"]:
            duree = (l["temps_service_reel_s"] or 0) // 60
            st.caption(f"{heure_locale(l['heure_debut_livraison'])} → "
                       f"{heure_locale(l['heure_fin_livraison'])} ({duree} min sur place)")


def formulaire_ajout(tournee: dict) -> None:
    """Module 3 : nouvelle adresse en cours de tournée (refusée si véhicule inadapté ou limite atteinte)."""
    nb = len(tournee["livraisons"])
    with st.expander(f"Ajouter une adresse ({nb} / {tournee['max_clients']} clients)"):
        if nb >= tournee["max_clients"]:
            st.warning("Limite d'effectif de la zone atteinte : un nouveau client doit être reporté "
                       "sur une tournée ultérieure.")
            return
        with st.form("ajout_client"):
            nom = st.text_input("Nom du client", max_chars=100)
            voie = st.text_input("Numéro et rue", max_chars=200)
            col1, col2 = st.columns([1, 2])
            code_postal = col1.text_input("Code postal", max_chars=5)
            ville = col2.text_input("Ville", max_chars=100)
            type_camion = st.radio("Livraison", ["Colis (camion normal)", "Palette (camion hayon)"],
                                   horizontal=True)
            avec_creneau = st.checkbox("Créneau horaire imposé")
            col3, col4 = st.columns(2)
            debut = col3.time_input("De", value=datetime.strptime("09:00", "%H:%M").time(), step=900)
            fin = col4.time_input("À", value=datetime.strptime("12:00", "%H:%M").time(), step=900)
            digicode = st.text_input("Digicode (facultatif)", max_chars=50)
            telephone = st.text_input("Téléphone (facultatif)", max_chars=20)
            notes = st.text_area("Notes de livraison (facultatif)", max_chars=500)
            valider = st.form_submit_button("Ajouter à ma tournée", width="stretch")
        if not valider:
            return
        if not (nom.strip() and voie.strip() and code_postal.strip() and ville.strip()):
            st.error("Nom, rue, code postal et ville sont obligatoires.")
            return
        demande = {"nom": nom, "voie": voie, "code_postal": code_postal.strip(), "ville": ville,
                   "type_camion_requis": "hayon" if type_camion.startswith("Palette") else "normal",
                   "digicode": digicode or None, "telephone": telephone or None,
                   "notes_livraison": notes or None}
        if avec_creneau:
            demande |= {"creneau_debut": debut.strftime("%H:%M"), "creneau_fin": fin.strftime("%H:%M")}
        with st.spinner("Vérification de l'adresse..."):
            r = appel_api("POST", "/tournees/du-jour/clients", st.session_state.jeton, timeout=30,
                          json=demande)
        if r is None:
            return
        if r.status_code == 401:
            reinitialiser("Votre session a expiré. Veuillez vous reconnecter.")
            st.rerun()
        if r.status_code != 201:
            try:
                detail = r.json().get("detail")
            except ValueError:
                detail = None
            st.error(detail if isinstance(detail, str) else "Saisie invalide : vérifiez le code postal, "
                     "le téléphone et le créneau (la fin doit être après le début).")
            return
        texte = (f"{r.json()['livraison']['client']['nom']} ajouté à votre tournée. Appuyez sur le "
                 "bouton d'optimisation pour l'intégrer au meilleur endroit.")
        if r.json()["avertissement"]:
            texte += " " + r.json()["avertissement"]
        st.session_state.message_tournee = ("warning" if r.json()["avertissement"] else "success", texte)
        st.rerun()


def ecran_tournee(profil: dict) -> None:
    st.title(f"Bonjour {profil['prenom']}")
    r = appel_api("GET", "/tournees/du-jour", st.session_state.jeton)
    if r is None:
        return
    if r.status_code == 404:
        st.info("Aucune tournée ne vous est attribuée aujourd'hui.")
        return
    if r.status_code != 200:
        afficher_erreur(r)
        return
    t = r.json()
    livraisons = t["livraisons"]

    st.caption(f"Tournée du {datetime.fromisoformat(t['date_tournee']):%d/%m/%Y} · "
               f"camion {t['vehicule'] or '—'} ({t['type_camion'] or '—'}) · zone {t['zone'] or '—'} · "
               f"{t['heure_depart'][:5]}–{t['heure_fin'][:5]}")
    if message := st.session_state.pop("message_tournee", None):
        getattr(st, message[0])(message[1])

    faites = sum(l["statut"] in ("livre", "absent", "refuse") for l in livraisons)
    a_faire = [l for l in livraisons if l["statut"] in ("a_livrer", "en_cours")]
    col1, col2, col3 = st.columns(3)
    col1.metric("Livraisons", f"{faites} / {len(livraisons) - sum(l['statut'] == 'reporte' for l in livraisons)}")
    col2.metric("Distance", f"{nombre_fr(t['distance_totale_km'])} km" if t["distance_totale_km"] else "—")
    if t["duree_estimee_min"]:
        depart = datetime.strptime(t["heure_depart"][:5], "%H:%M")
        col3.metric("Retour prévu", f"{depart + timedelta(minutes=t['duree_estimee_min']):%H:%M}")
    else:
        col3.metric("Retour prévu", "—")

    en_cours = any(l["statut"] == "en_cours" for l in livraisons)
    if t["statut"] == "terminee":
        st.success("Tournée terminée. Merci !")
    elif a_faire:
        demarree = t["statut"] == "en_cours"
        if st.button("Recalculer l'itinéraire restant" if demarree else "Optimiser", type="primary",
                     width="stretch", disabled=en_cours,
                     help="Terminez d'abord la livraison en cours." if en_cours else
                     "Réorganise la tournée : créneaux horaires, temps de service, type de "
                     "véhicule et limite d'effectif de la zone."):
            optimiser()
        if not t["optimisee_le"]:
            st.caption("Liste dans l'ordre d'attribution, pas encore optimisée.")
        elif any(l["ordre_passage"] is None for l in a_faire):
            st.caption("Nouvelle adresse ajoutée : appuyez sur le bouton ci-dessus pour l'intégrer "
                       "au meilleur endroit de l'itinéraire.")
    if t["statut"] != "terminee":
        formulaire_ajout(t)

    carte_tournee(t)

    st.subheader(f"À faire ({len(a_faire)})")
    for rang, l in enumerate(livraisons, start=1):
        if l in a_faire:
            carte_livraison(l, rang, demarrage_possible=not en_cours, prochaine=l is a_faire[0])

    terminees = [(rang, l) for rang, l in enumerate(livraisons, start=1)
                 if l["statut"] in ("livre", "absent", "refuse")]
    if terminees:
        with st.expander(f"Terminées ({len(terminees)})"):
            for rang, l in terminees:
                carte_livraison(l, rang, False, False)
    reportees = [(rang, l) for rang, l in enumerate(livraisons, start=1) if l["statut"] == "reporte"]
    if reportees:
        with st.expander(f"À reporter ({len(reportees)})"):
            st.caption("Clients incompatibles avec votre véhicule ou votre créneau : "
                       "à replanifier par le planificateur.")
            for rang, l in reportees:
                carte_livraison(l, rang, False, False)
    st.caption("Le pointage sert à améliorer l'estimation des temps de livraison, "
               "pas à évaluer votre performance individuelle.")


ECRANS = {
    "identifiants": ecran_identifiants,
    "enrolement": ecran_enrolement,
    "codes_secours": ecran_codes_secours,
    "verification": ecran_verification,
    "connecte": ecran_connecte,
}
ECRANS[st.session_state.etape]()
