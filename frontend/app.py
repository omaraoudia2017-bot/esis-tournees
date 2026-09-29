"""Interface ESIS (Streamlit) — écran de connexion sécurisé (Module 1).

Le jeton de session reste côté serveur Streamlit (st.session_state) : il n'est jamais
stocké dans le navigateur (ni cookie, ni localStorage).
"""
import base64
import os
from datetime import datetime, timezone

import requests
import streamlit as st

API_URL = os.environ.get("API_URL", "http://api:8000")

st.set_page_config(page_title="ESIS Tournées", layout="centered")

ETAT_INITIAL = {"etape": "identifiants", "preauth": None, "jeton": None, "expire_le": None,
                "role": None, "codes_secours": None, "enrolement": None}
for cle, valeur in ETAT_INITIAL.items():
    st.session_state.setdefault(cle, valeur)


def reinitialiser(message: str | None = None) -> None:
    for cle, valeur in ETAT_INITIAL.items():
        st.session_state[cle] = valeur
    if message:
        st.session_state["message"] = message


def appel_api(methode: str, chemin: str, jeton: str | None = None, **kwargs) -> requests.Response | None:
    entetes = {}
    if jeton:
        entetes["Authorization"] = f"Bearer {jeton}"
    navigateur = getattr(st, "context", None) and st.context.headers.get("User-Agent")
    if navigateur:                      # transmis pour le journal de sécurité
        entetes["User-Agent"] = navigateur
    try:
        return requests.request(methode, f"{API_URL}{chemin}", headers=entetes, timeout=10, **kwargs)
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
        valider = st.form_submit_button("Se connecter", use_container_width=True)
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
        valider = st.form_submit_button("Activer", use_container_width=True)
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
    if st.button("Continuer", disabled=not confirme, use_container_width=True):
        st.session_state.codes_secours = None
        st.session_state.etape = "connecte"
        st.rerun()


def ecran_verification() -> None:
    st.title("Vérification en deux étapes")
    with st.form("verification"):
        code = st.text_input("Code à 6 chiffres de votre application d'authentification",
                             help="Téléphone perdu ? Saisissez un de vos codes de secours (XXXXX-XXXXX).")
        valider = st.form_submit_button("Valider", use_container_width=True)
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
        if st.button("Se déconnecter", use_container_width=True):
            appel_api("POST", "/auth/deconnexion", st.session_state.jeton)
            reinitialiser("Vous êtes déconnecté.")
            st.rerun()

    if profil["role"] == "admin":
        st.title("Espace administrateur")
        st.caption("Gestion des livreurs, import CSV et tableau de bord : à venir.")
        st.subheader("Journal de sécurité — 20 derniers événements")
        r = appel_api("GET", "/admin/journal-securite", st.session_state.jeton, params={"limite": 20})
        if r is not None and r.status_code == 200:
            st.dataframe(
                [{"Date": e["horodatage"][:19].replace("T", " "), "Événement": e["evenement"],
                  "Succès": e["succes"], "Compte": e["identifiant_saisi"], "IP": e["adresse_ip"]}
                 for e in r.json()],
                use_container_width=True, hide_index=True,
            )
    else:
        st.title(f"Bonjour {profil['prenom']}")
        st.subheader("Votre tournée du jour")
        st.info("L'affichage et l'optimisation de la tournée arrivent dans une prochaine version.")


ECRANS = {
    "identifiants": ecran_identifiants,
    "enrolement": ecran_enrolement,
    "codes_secours": ecran_codes_secours,
    "verification": ecran_verification,
    "connecte": ecran_connecte,
}
ECRANS[st.session_state.etape]()
