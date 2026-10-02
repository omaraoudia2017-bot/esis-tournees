"""Recherche de versions corrigées compatibles entre elles, SANS rien installer (Jour 13).

Usage :
    docker compose exec -T api python -m scripts.resoudre_versions

Les bibliothèques signalées par pip-audit reçoivent une version minimale corrigée ; les autres
restent figées. pip calcule alors un ensemble de versions compatible (option --dry-run : rien
n'est installé) et le script affiche les lignes à recopier dans les fichiers requirements.txt.
"""
import json
import subprocess  # commande pip fixe, sans entrée utilisateur  # nosec B404
import sys
import tempfile
from pathlib import Path

API = [
    "fastapi", "starlette>=1.3.1",                          # Host header, formulaires (pip-audit)
    "uvicorn[standard]==0.32.1", "SQLAlchemy==2.0.36", "psycopg[binary]==3.2.3",
    "pydantic-settings==2.6.1",
    "cryptography>=50.0.0",                                 # OpenSSL embarqué, PKCS7, X.509
    "geopy==2.4.1", "pytest>=9.0.3", "bcrypt==4.2.1",
    "PyJWT>=2.15.0",                                        # en-tête crit, signature Base64URL
    "pyotp==2.9.0", "qrcode[pil]==8.0", "pillow>=12.3.0", "httpx==0.27.2",
    "ortools==9.11.4210", "tzdata==2024.2", "scikit-learn==1.5.2",
    "bandit==1.7.10", "pip-audit==2.7.3",
]
FRONT = [
    "streamlit>=1.54.0",                                    # SSRF Windows, hachage du cache
    "requests>=2.33.0",                                     # fuite .netrc, fichier temporaire
    "folium==0.18.0", "streamlit-folium==0.23.2", "pillow>=12.3.0", "tzdata==2024.2",
]
SUIVIS = ["starlette", "pillow", "protobuf", "pydantic", "anyio", "httpx", "setuptools"]


def nom(n: str) -> str:
    return n.lower().replace("_", "-")


def resoudre(exigences: list[str]) -> dict[str, str]:
    with tempfile.TemporaryDirectory() as dossier:
        req, rapport = Path(dossier, "req.txt"), Path(dossier, "rapport.json")
        req.write_text("\n".join(exigences))
        resultat = subprocess.run(  # arguments fixes, pas de shell  # nosec B603
            [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--quiet",
             "--disable-pip-version-check", "--report", str(rapport), "-r", str(req)],
            capture_output=True, text=True)
        if resultat.returncode != 0:
            print(resultat.stderr[-3000:])
            return {}
        return {nom(i["metadata"]["name"]): i["metadata"]["version"]
                for i in json.loads(rapport.read_text())["install"]}


def afficher(titre: str, exigences: list[str]) -> None:
    print(f"\n===== {titre} =====")
    versions = resoudre(exigences)
    if not versions:
        print("Résolution impossible (voir le message ci-dessus).")
        return
    for e in exigences:
        base = e.split("[")[0].split("=")[0].split(">")[0]
        extra = e[len(base):].split("=")[0].split(">")[0] if "[" in e else ""
        print(f"{base}{extra}=={versions.get(nom(base), '?')}")
    print("-- dépendances indirectes suivies :",
          ", ".join(f"{n} {versions[n]}" for n in SUIVIS if n in versions))


if __name__ == "__main__":
    afficher("backend/requirements.txt", API)
    afficher("frontend/requirements.txt", FRONT)
