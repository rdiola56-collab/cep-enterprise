"""Outgoing email against a real local SMTP server (aiosmtpd): STARTTLS, login, outbox, notices."""
import datetime as dt
import email
import email.policy
import ipaddress
import re
import socket
import time

import pytest
from aiosmtpd.controller import Controller
from aiosmtpd.smtp import AuthResult, LoginPassword
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app import jobs
from app.db import models as m
from app.services import mail

from .test_api import PW, TILE, auth, client  # noqa: F401  (fixture)


class Inbox:
    def __init__(self):
        self.messages: list[email.message.Message] = []
        self.rcpt: list[list[str]] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: N802  (aiosmtpd hook name)
        self.messages.append(email.message_from_bytes(envelope.content, policy=email.policy.default))
        self.rcpt.append(list(envelope.rcpt_tos))
        return "250 Message accepted"

    def to(self, addr):
        return [msg for msg, rc in zip(self.messages, self.rcpt) if addr in rc]


def _free_port():
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0))
        return sk.getsockname()[1]


def _cert(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .sign(key, hashes.SHA256()))
    cpath, kpath = tmp_path / "smtp.crt", tmp_path / "smtp.key"
    cpath.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kpath.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                        serialization.NoEncryption()))
    return str(cpath), str(kpath)


@pytest.fixture
def smtp(monkeypatch, tmp_path):
    """A real SMTP server that requires STARTTLS and a login, and the CEP settings pointing at it."""
    import ssl

    cert, key = _cert(tmp_path)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(cert, key)
    inbox = Inbox()

    def check(server, session, envelope, mechanism, data):
        ok = isinstance(data, LoginPassword) and data.login == b"cep" and data.password == b"smtp-secret"
        return AuthResult(success=ok)

    port = _free_port()
    ctl = Controller(inbox, hostname="localhost", port=port, tls_context=ctx, require_starttls=True,
                     authenticator=check, auth_required=True)
    ctl.start()
    for k, v in {"CEP_SMTP_HOST": "localhost", "CEP_SMTP_PORT": str(port), "CEP_SMTP_SECURITY": "starttls",
                 "CEP_SMTP_USER": "cep", "CEP_SMTP_PASSWORD": "smtp-secret", "CEP_SMTP_CA_FILE": cert,
                 "CEP_MAIL_FROM": "CEP Estimating <cep@example.test>", "CEP_APP_URL": "https://cep.example.test"}.items():
        monkeypatch.setenv(k, v)
    inbox.port = port
    yield inbox
    ctl.stop()


def _off(monkeypatch):
    for k in ("CEP_SMTP_HOST", "CEP_MAIL_FROM", "CEP_APP_URL"):
        monkeypatch.delenv(k, raising=False)


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_email_is_off_until_configured(client, monkeypatch):  # noqa: F811
    _off(monkeypatch)
    assert client.get("/api/auth/sso").json()["forgot_password"] is False
    assert client.post("/api/auth/forgot", json={"email": "est@cep.test"}).status_code == 404
    admin = auth(client, "admin@cep.test")
    assert client.get("/api/admin/mail", headers=admin).json()["status"]["enabled"] is False
    assert client.post("/api/admin/mail/test", headers=admin).status_code == 409
    uid = next(u["id"] for u in client.get("/api/users", headers=admin).json() if u["email"] == "view@cep.test")
    assert client.post(f"/api/users/{uid}/reset-link", headers=admin).json()["emailed"] is False
    assert client.get("/api/admin/mail", headers=auth(client, "est@cep.test")).status_code == 403


def test_forgot_password_by_email(client, smtp, db):  # noqa: F811
    assert client.get("/api/auth/sso").json()["forgot_password"] is True
    r = client.post("/api/auth/forgot", json={"email": "EST@cep.test"})
    assert r.status_code == 202
    same = client.post("/api/auth/forgot", json={"email": "nobody@cep.test"})
    assert same.status_code == 202 and same.json() == r.json()  # no hint whether the account exists
    assert _wait(lambda: len(smtp.messages) == 1)
    msg = smtp.messages[0]
    assert smtp.rcpt[0] == ["est@cep.test"] and msg["Subject"] == "Reset your CEP password"
    assert msg["From"] == "CEP Estimating <cep@example.test>"
    token = re.search(r"https://cep\.example\.test/reset-password\?token=(\S+)", msg.get_content()).group(1)

    out = client.get("/api/admin/mail", headers=auth(client, "admin@cep.test")).json()["messages"]
    reset = next(x for x in out if x["kind"] == "reset_link")
    assert reset["status"] == "sent" and reset["body"] is None  # secret link not shown
    assert db.get(m.EmailMessage, reset["id"]).body == mail.SENT_PLACEHOLDER  # and not kept once sent

    r = client.post("/api/auth/reset", json={"token": token, "new_password": "a-whole-new-password"})
    assert r.status_code == 200
    assert _wait(lambda: any(x["Subject"] == "Your CEP password was changed" for x in smtp.to("est@cep.test")))

    # At most three self-service links an hour.
    for _ in range(4):
        client.post("/api/auth/forgot", json={"email": "est@cep.test"})
    time.sleep(0.3)
    assert len([x for x in smtp.to("est@cep.test") if x["Subject"] == "Reset your CEP password"]) == 3


def test_notices_for_lockout_membership_and_approval(client, smtp, monkeypatch):  # noqa: F811
    from app.services import users

    monkeypatch.setattr(users, "MAX_FAILED_LOGINS", 2)
    for _ in range(2):
        client.post("/api/auth/login", json={"email": "view@cep.test", "password": "wrong-password-x"})
    assert _wait(lambda: any(x["Subject"] == "Your CEP account was locked" for x in smtp.to("view@cep.test")))

    est, admin = auth(client, "est@cep.test"), auth(client, "admin@cep.test")
    pid = client.post("/api/projects", json={"code": "ML", "name": "Mail", "currency": "PHP"}, headers=est).json()["id"]
    appr = client.get("/api/auth/me", headers=auth(client, "approver@cep.test")).json()["id"]
    ce = client.get("/api/auth/me", headers=auth(client, "ce@cep.test")).json()["id"]
    client.put(f"/api/projects/{pid}/members", json={"user_id": appr, "role": "approver"}, headers=est)
    client.put(f"/api/projects/{pid}/members", json={"user_id": ce, "role": "estimator"}, headers=est)
    assert _wait(lambda: any("added to ML" in x["Subject"] for x in smtp.to("approver@cep.test")))
    client.put(f"/api/projects/{pid}/members", json={"user_id": appr, "role": "approver"}, headers=est)  # no change: no email
    v = client.post(f"/api/projects/{pid}/estimates", headers=est, json={"code": "E", "name": "Main", "pricing_date": "2026-11-01"}).json()
    client.post(f"/api/projects/{pid}/versions/{v['version_id']}/items", json=TILE, headers=est)
    r = client.post(f"/api/projects/{pid}/versions/{v['version_id']}/approve", headers=auth(client, "approver@cep.test"))
    assert r.status_code == 200, r.text
    assert _wait(lambda: any(x["Subject"] == "CEP: E v1 approved" for x in smtp.to("est@cep.test")))
    assert _wait(lambda: any(x["Subject"] == "CEP: E v1 approved" for x in smtp.to("ce@cep.test")))
    approved = next(x for x in smtp.to("est@cep.test") if x["Subject"] == "CEP: E v1 approved").get_content()
    assert "approver@cep.test approved E Main, version 1" in approved and "PHP" in approved
    assert not [x for x in smtp.to("approver@cep.test") if "approved" in x["Subject"]]  # the approver is not told
    assert len([x for x in smtp.to("approver@cep.test") if "added to ML" in x["Subject"]]) == 1

    r = client.post("/api/admin/mail/test", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "sent"


def test_an_unreachable_server_is_recorded_and_can_be_retried(client, smtp, monkeypatch):  # noqa: F811
    admin = auth(client, "admin@cep.test")
    monkeypatch.setenv("CEP_SMTP_PORT", str(_free_port()))  # the mail server is down: nothing listens there
    failed = client.post("/api/admin/mail/test", headers=admin).json()
    assert failed["status"] == "failed" and "Connection" in failed["last_error"] and failed["attempts"] == 1
    monkeypatch.setenv("CEP_SMTP_PORT", str(smtp.port))  # back up
    again = client.post(f"/api/admin/mail/{failed['id']}/retry", headers=admin).json()
    assert again["status"] == "sent" and again["attempts"] == 2 and again["last_error"] is None
    assert client.post(f"/api/admin/mail/{failed['id']}/retry", headers=admin).status_code == 422  # already sent
    monkeypatch.setenv("CEP_SMTP_SECURITY", "none")  # this server insists on STARTTLS: CEP never falls back to plain text
    plain = client.post("/api/admin/mail/test", headers=admin).json()
    assert plain["status"] == "failed"


def test_with_a_worker_mail_waits_for_commit(db, smtp, monkeypatch):
    """In celery mode a message is handed to the worker only once its transaction commits."""
    from app.services import notify
    from app.services.users import create_user

    sent: list[str] = []

    class Task:
        @staticmethod
        def delay(mid):
            sent.append(mid)

    monkeypatch.setattr(jobs, "MODE", "celery")
    monkeypatch.setattr(jobs, "send_email_task", Task, raising=False)
    u = create_user(db, "w@cep.test", "W", PW, "viewer")
    msg = notify.test_message(db, u)
    assert msg.status == "queued" and sent == []
    db.commit()
    assert sent == [str(msg.id)]
    notify.test_message(db, u)
    db.rollback()  # never stored: never sent
    db.commit()
    assert len(sent) == 1
