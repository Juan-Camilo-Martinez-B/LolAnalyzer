from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.core.exceptions import install_exception_handlers, public_error


def test_known_details_become_spanish_without_status_codes():
    message, code = public_error(409, "An account with this email address already exists.")
    assert code == "account_exists"
    assert message == "Ya existe una cuenta con ese correo."
    assert "409" not in message


def test_revoked_session_uses_a_stable_code():
    message, code = public_error(401, "Token has been revoked (User logged out).")
    assert code == "session_revoked"
    assert "token" not in message.lower()
    assert "Cerraste sesión" in message


def test_riot_configuration_does_not_mention_the_key():
    message, code = public_error(503, "Riot API is not configured. Set RIOT_API_KEY on the server.")
    assert code == "riot_unavailable"
    assert "key" not in message.lower()
    assert "RIOT_API_KEY" not in message


def test_spanish_domain_message_is_kept():
    original = "No se pudo verificar la identidad con esos datos."
    message, code = public_error(401, original)
    assert message == original
    assert code == "recovery_rejected"


def test_http_and_unexpected_handlers_hide_internals():
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/missing")
    def missing():
        raise HTTPException(status_code=404, detail="Not Found")

    @app.get("/boom")
    def boom():
        raise RuntimeError("secret database password")

    client = TestClient(app, raise_server_exceptions=False)
    missing_response = client.get("/missing")
    assert missing_response.status_code == 404
    assert missing_response.json()["detail"] == "No encontramos lo que buscabas."
    assert "404" not in missing_response.json()["detail"]

    boom_response = client.get("/boom")
    assert boom_response.status_code == 500
    assert boom_response.json()["code"] == "unexpected"
    assert "secret" not in boom_response.text

    ghost = client.get("/no-such-route")
    assert ghost.status_code == 404
    assert ghost.json()["code"] == "not_found"
