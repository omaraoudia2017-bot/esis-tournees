"""Application mobile du livreur (/livreur) : fichiers servis par l'API avec une politique de
sécurité stricte (CSP). Le parcours lui-même utilise les routes déjà testées ailleurs."""


def test_application_mobile_servie_avec_csp(client):
    r = client.get("/livreur/")
    assert r.status_code == 200 and "Road Optim" in r.text
    csp = r.headers["content-security-policy"]
    for directive in ("default-src 'none'", "script-src 'self'", "connect-src 'self'", "frame-ancestors 'none'"):
        assert directive in csp
    assert r.headers["x-frame-options"] == "DENY"
    for fichier, type_attendu in (("app.js", "javascript"), ("app.css", "text/css"), ("logo.svg", "image/svg+xml")):
        f = client.get(f"/livreur/{fichier}")
        assert f.status_code == 200 and type_attendu in f.headers["content-type"]


def test_aucune_insertion_html_ni_stockage_du_jeton(client):
    js = client.get("/livreur/app.js").text
    # Pas d'innerHTML (XSS) ni de stockage du jeton dans le navigateur
    for interdit in ("innerHTML", "outerHTML", "insertAdjacentHTML", "localStorage", "sessionStorage",
                     "document.cookie", "eval("):
        assert interdit not in js


def test_racine_redirige_vers_l_application(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/livreur/"
