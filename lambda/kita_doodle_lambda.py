#!/usr/bin/env python3
"""
kita_doodle_lambda.py — AWS Lambda handler for Sien-18 Kita Notbetreuung auto-registration.

Entry point: handler(event, context)

Event parameters (all optional — env vars supply defaults):
  url      : str  — Doodle URL; skips Gmail search entirely (leave empty for production)
  debug    : bool — headful browser + slow_mo; only useful locally, default False
  dry_run  : bool — check availability, no form submit, default False
  attempt  : int  — current Step Functions attempt counter (informational)

Returns dict passed back to Step Functions:
  {"action": str, "dalia_registered": bool, "seats_available": bool | null}

action values:
  submitted          — booked successfully, confirmation email received
  submitted_unverified — form submitted but page text inconclusive (treat as success)
  already_registered — Child already in the session
  no_seats           — all seats taken (Step Functions stops retrying)
  no_email           — Kita email not found yet (Step Functions retries)
  no_link            — email found but no Doodle URL inside (Step Functions retries)
  dry_run            — dry-run mode, nothing submitted
  form_error         — browser automation problem (Step Functions retries)
"""

import os
import re
import json
import time
import base64
import logging
import email.message
from datetime import datetime
from pathlib import Path

# ── logging ────────────────────────────────────────────────────────────────────
# Lambda pre-configures the root logger; basicConfig is a no-op unless we force it.
logging.getLogger().setLevel(logging.INFO)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
log = logging.getLogger("kita_doodle")

# ── config from env vars (required — injected by Terraform / GitHub Actions) ──
def _require_env(name: str) -> str:
    val = os.environ.get(name, "")
    if not val:
        raise RuntimeError(f"Required environment variable {name!r} is not set.")
    return val

CHILD_NAME            = _require_env("CHILD_NAME")
ATTENDEE_NAME         = _require_env("ATTENDEE_NAME")
ATTENDEE_EMAIL        = _require_env("ATTENDEE_EMAIL")
NOTIFY_RECIPIENTS     = _require_env("NOTIFY_RECIPIENTS").split(",")
KITA_SENDER           = _require_env("KITA_SENDER")
KITA_SUBJECT_KEYWORDS = _require_env("KITA_SUBJECT_KEYWORDS").split(",")

GMAIL_CREDENTIALS_PARAM = os.environ.get("GMAIL_CREDENTIALS_PARAM", "/kita-bot/gmail-credentials")
GMAIL_TOKEN_PARAM       = os.environ.get("GMAIL_TOKEN_PARAM",       "/kita-bot/gmail-token")

# Lambda's only writable path — used for OAuth file temp copies
GMAIL_CREDS_FILE = Path("/tmp/credentials.json")
GMAIL_TOKEN_FILE = Path("/tmp/token.json")

DOODLE_URL_PATTERN = re.compile(
    r"https://doodle\.com/sign-up-sheet/participate/[a-z0-9\-]+(?:/select)?"
)

CONFIRM_POLL_ATTEMPTS = 10
CONFIRM_POLL_INTERVAL = 5


# ── Secrets Manager ────────────────────────────────────────────────────────────

def _ssm_client():
    import boto3
    return boto3.client("ssm", region_name=os.environ.get("AWS_REGION", "eu-west-1"))


def _load_gmail_credentials():
    """Pull credentials + token from SSM Parameter Store into /tmp for the google libraries."""
    client = _ssm_client()

    creds = client.get_parameter(Name=GMAIL_CREDENTIALS_PARAM, WithDecryption=True)
    GMAIL_CREDS_FILE.write_text(creds["Parameter"]["Value"])

    try:
        token = client.get_parameter(Name=GMAIL_TOKEN_PARAM, WithDecryption=True)
        GMAIL_TOKEN_FILE.write_text(token["Parameter"]["Value"])
    except client.exceptions.ParameterNotFound:
        raise RuntimeError(
            "Gmail token not found in SSM. "
            "Run locally first to generate token.json, then upload with upload_secrets.sh."
        )


def _save_gmail_token():
    """Write refreshed token back to SSM after silent renewal."""
    if not GMAIL_TOKEN_FILE.exists():
        return
    client = _ssm_client()
    try:
        client.put_parameter(
            Name=GMAIL_TOKEN_PARAM,
            Value=GMAIL_TOKEN_FILE.read_text(),
            Type="SecureString",
            Overwrite=True,
        )
        log.info("Refreshed token saved to SSM.")
    except Exception as exc:
        log.error(f"Failed to save token: {exc}")


# ── Gmail helpers ──────────────────────────────────────────────────────────────

def get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
    _load_gmail_credentials()

    creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_FILE), SCOPES)

    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            GMAIL_TOKEN_FILE.write_text(creds.to_json())
            _save_gmail_token()
        else:
            raise RuntimeError(
                "Gmail credentials invalid and cannot be refreshed automatically. "
                "Re-run locally to re-authorise, then re-upload token.json."
            )

    return build("gmail", "v1", credentials=creds)


def find_kita_email(service) -> dict | None:
    query = f"from:{KITA_SENDER} newer_than:1d"
    result = service.users().messages().list(userId="me", q=query, maxResults=10).execute()

    for msg_ref in result.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        subject = _get_header(msg, "Subject") or ""
        if all(kw.lower() in subject.lower() for kw in KITA_SUBJECT_KEYWORDS):
            log.info(f"Found Kita email: '{subject}'")
            return msg

    # fallback — body contains doodle.com
    query2 = f"from:{KITA_SENDER} (Notbetreuung OR Doodle) newer_than:2d"
    result2 = service.users().messages().list(userId="me", q=query2, maxResults=5).execute()
    for msg_ref in result2.get("messages", []):
        msg = service.users().messages().get(
            userId="me", id=msg_ref["id"], format="full"
        ).execute()
        if "doodle.com" in _extract_body(msg):
            log.info("Found Kita email via body fallback.")
            return msg

    return None


def extract_doodle_link(msg: dict) -> str | None:
    match = DOODLE_URL_PATTERN.search(_extract_body(msg))
    if match:
        url = match.group(0)
        return url if url.endswith("/select") else url.rstrip("/") + "/select"
    return None


def find_doodle_confirmation(service) -> bool:
    query = 'from:mailer@doodle.com newer_than:1d ("Deine Buchung" OR "You have signed up")'
    result = service.users().messages().list(userId="me", q=query, maxResults=5).execute()
    return bool(result.get("messages"))


def _get_header(msg: dict, name: str) -> str | None:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return None


def _extract_body(msg: dict) -> str:
    def _decode(part):
        data = part.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="ignore")

    def _walk(part, mime_type):
        if part.get("mimeType", "") == mime_type:
            return _decode(part)
        for sub in part.get("parts", []):
            r = _walk(sub, mime_type)
            if r:
                return r
        return ""

    payload = msg.get("payload", {})
    return _walk(payload, "text/plain") or _walk(payload, "text/html")


# ── Doodle browser ─────────────────────────────────────────────────────────────

def check_and_register(doodle_url: str, dry_run: bool = False, debug: bool = False) -> dict:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not debug,
            slow_mo=500 if debug else 0,
            # Required flags for running Chromium inside Lambda (no sandbox, limited /dev/shm)
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--single-process"],
        )
        try:
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="de-DE",
                timezone_id="Europe/Berlin",
            )
            page = ctx.new_page()

            log.info(f"Opening: {doodle_url}")
            page.goto(doodle_url, wait_until="domcontentloaded", timeout=30_000)

            # Dismiss OneTrust GDPR consent modal before any interaction
            try:
                reject_btn = page.wait_for_selector(
                    "button:has-text('Alle ablehnen'), button:has-text('Reject all'), "
                    "button:has-text('Decline all')",
                    state="visible",
                    timeout=8_000,
                )
                reject_btn.click()
                log.info("Cookie consent dismissed.")
            except PWTimeout:
                pass

            # Wait for session list to mount
            try:
                page.wait_for_selector(
                    "input[type='checkbox'], [role='checkbox']",
                    state="visible",
                    timeout=20_000,
                )
            except PWTimeout:
                log.warning("Session list took too long to load.")

            page_text = page.inner_text("body")

            already_phrases = ["signed up", "you registered as", "bereits angemeldet"]
            if any(p in page_text.lower() for p in already_phrases):
                log.info("Already registered for today's session.")
                return {"dalia_registered": True, "seats_available": None, "action": "already_registered"}

            seats_available = _parse_seats(page_text)
            result: dict = {"dalia_registered": False, "seats_available": seats_available, "action": "none"}

            if seats_available is False:
                log.warning("No seats available.")
                result["action"] = "no_seats"
                return result

            if dry_run:
                result["action"] = "dry_run"
                return result

            # Click session row via JS — native checkbox is CSS-hidden; row carries the click handler
            clicked = page.evaluate("""() => {
                const targets = [
                    document.querySelector('[data-testid="time-slot-item-container"]'),
                    document.querySelector('[data-testid^="time-slot-item-checkbox-"]'),
                ];
                for (const el of targets) { if (el) { el.click(); return true; } }
                return false;
            }""")

            if not clicked:
                log.error("Session row not found.")
                result["action"] = "form_error"
                return result

            log.info("Clicked session row.")
            page.wait_for_timeout(500)
            screenshots = []
            slot = page.query_selector('[data-testid="time-slot-item-container"]')
            if slot:
                slot.scroll_into_view_if_needed()
                page.wait_for_timeout(200)
            _take_screenshot(page, "/tmp/sien18_1_session_selected.png", "1 — session selected") and screenshots.append("/tmp/sien18_1_session_selected.png")

            continue_btn = (
                page.query_selector("button:has-text('Continue')")
                or page.query_selector("button:has-text('Fortfahren')")
                or page.query_selector("button:has-text('Weiter')")
            )
            if not continue_btn:
                log.error("Continue button not found.")
                result["action"] = "form_error"
                return result

            continue_btn.click(force=True)
            log.info("Clicked Fortfahren.")

            # Page 2: name + email + optional custom question
            try:
                page.wait_for_selector(
                    "input[id*='name'], input[placeholder*='Name'], input[placeholder*='name']",
                    timeout=10_000,
                )
            except PWTimeout:
                log.error("Page 2 did not load.")
                result["action"] = "form_error"
                return result

            for sel in ["input[id*='name']", "input[placeholder*='Name']",
                        "input[placeholder*='name']", "input[type='text']"]:
                el = page.query_selector(sel)
                if el:
                    el.fill(ATTENDEE_NAME)
                    log.info(f"Name filled ({sel}).")
                    break

            for sel in ["input[type='email']", "input[placeholder*='E-Mail']",
                        "input[placeholder*='email']", "input[id*='email']"]:
                el = page.query_selector(sel)
                if el:
                    el.fill(ATTENDEE_EMAIL)
                    log.info(f"Email filled ({sel}).")
                    break

            # Kita organiser custom questions (order-independent, fields are optional)
            # Label detection: aria-label → placeholder → <label for=id> → nearest ancestor text
            for textarea in page.query_selector_all("textarea"):
                if textarea.input_value() != "":
                    continue
                _generic = {"deine antwort", "ihre antwort", "your answer", "enter your answer"}
                _aria = (textarea.get_attribute("aria-label") or "").strip()
                _ph   = (textarea.get_attribute("placeholder") or "").strip()
                label = (
                    _aria if _aria and _aria.lower() not in _generic else
                    _ph   if _ph   and _ph.lower()   not in _generic else
                    ""
                )
                if not label:
                    field_id = textarea.get_attribute("id")
                    if field_id:
                        lbl_el = page.query_selector(f"label[for='{field_id}']")
                        if lbl_el:
                            lbl_text = (lbl_el.inner_text() or "").strip()
                            if lbl_text.lower() not in _generic:
                                label = lbl_text
                if not label:
                    label = page.evaluate("""el => {
                        const generic = new Set(['deine antwort', 'ihre antwort', 'your answer', 'enter your answer']);
                        function text(node) {
                            const c = node.cloneNode(true);
                            c.querySelectorAll('input,textarea,button,script,style,svg').forEach(n => n.remove());
                            return (c.textContent || '').replace(/\\s+/g, ' ').trim();
                        }
                        let node = el;
                        for (let i = 0; i < 8; i++) {
                            node = node.parentElement;
                            if (!node) break;
                            let sib = node.previousElementSibling;
                            while (sib) {
                                const t = text(sib);
                                if (t && t.length < 100 && !generic.has(t.toLowerCase())) return t;
                                sib = sib.previousElementSibling;
                            }
                            const t = text(node);
                            const parts = t.split(/\\s{2,}|\\n/)
                                .map(s => s.trim())
                                .filter(s => s && !generic.has(s.toLowerCase()));
                            if (parts.length > 0 && parts.join(' ').length < 200) return parts[0];
                        }
                        return '';
                    }""", textarea)
                label_lower = label.lower()
                if "mail" in label_lower:
                    textarea.fill(ATTENDEE_EMAIL)
                    log.info(f"Custom question (email) filled — label: {label!r}.")
                else:
                    textarea.fill(ATTENDEE_NAME)
                    log.info(f"Custom question (child name) filled — label: {label!r}.")

            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(200)
            _take_screenshot(page, "/tmp/sien18_2_form_filled.png", "2 — form filled") and screenshots.append("/tmp/sien18_2_form_filled.png")

            confirm_btn = (
                page.query_selector("button:has-text('Buchung bestätigen')")
                or page.query_selector("button:has-text('Confirm booking')")
                or page.query_selector("button:has-text('Bestätigen')")
            )
            if not confirm_btn:
                log.error("Confirm button not found.")
                result["action"] = "form_error"
                return result

            confirm_btn.click()
            log.info("Clicked Buchung bestätigen.")

            try:
                page.wait_for_load_state("networkidle", timeout=8_000)
            except PWTimeout:
                pass
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(200)
            # Expand "Fragen anzeigen" so submitted answers are visible in the screenshot
            try:
                expanded = page.evaluate("""() => {
                    for (const el of document.querySelectorAll('button,a,summary,[role="button"],[onclick]')) {
                        if (/fragen anzeigen|show questions/i.test((el.textContent || '').trim())) {
                            el.scrollIntoView({block: 'center'});
                            el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                            return true;
                        }
                    }
                    return false;
                }""")
                if expanded:
                    page.wait_for_timeout(1000)
                    log.info("Expanded 'Fragen anzeigen'.")
                else:
                    log.warning("'Fragen anzeigen' button not found.")
            except Exception:
                pass
            _take_screenshot(page, "/tmp/sien18_3_booking_confirmed.png", "3 — booking confirmed") and screenshots.append("/tmp/sien18_3_booking_confirmed.png")

            body_text = page.inner_text("body").lower()

            already_phrases = ["bereits für diese sitzung angemeldet", "already registered for this session"]
            if any(p in body_text for p in already_phrases):
                log.info("Already registered (prior booking detected on confirm page).")
                result["dalia_registered"] = True
                result["action"] = "already_registered"
                result["screenshots"] = screenshots
                return result

            success_phrases = ["signed up", "you registered as", "angemeldet", "confirmed",
                               "vielen dank", "erfolgreich", "buchung bestätigt",
                               "edit booking", "buchung bearbeiten"]
            if any(p in body_text for p in success_phrases):
                result["action"] = "submitted"
                log.info("Booking confirmed on page.")
            else:
                log.warning(f"Page text inconclusive after submit: {body_text[:200]!r}")
                result["action"] = "submitted_unverified"

            result["screenshots"] = screenshots
            return result

        finally:
            browser.close()


def _take_screenshot(page, path: str, label: str) -> str | None:
    try:
        page.screenshot(path=path, full_page=False)
        log.info(f"Screenshot saved: {label}")
        return path
    except Exception as exc:
        log.warning(f"Screenshot failed ({label}): {exc}")
        return None


def _parse_seats(text: str) -> bool | None:
    t = text.lower()
    if any(w in t for w in ["ausgebucht", "voll", "full", "no spots", "keine plätze"]):
        return False
    m = re.search(r"(\d+)\s+seats?\s+left", t)
    if m:
        return int(m.group(1)) > 0
    return None


def _wait_and_confirm(service):
    log.info("Polling for Doodle confirmation email...")
    for attempt in range(1, CONFIRM_POLL_ATTEMPTS + 1):
        time.sleep(CONFIRM_POLL_INTERVAL)
        if find_doodle_confirmation(service):
            log.info("Doodle confirmation email received.")
            return
        log.info(f"  {attempt}/{CONFIRM_POLL_ATTEMPTS} — not yet...")
    log.warning("Confirmation email not received after polling — check inbox manually.")


def send_notification(service, result: dict, doodle_url: str):
    today = datetime.now().strftime("%A, %d %B %Y")
    screenshots = result.get("screenshots", [])

    msg = email.message.EmailMessage()
    msg["From"] = "me"
    msg["To"] = ", ".join(NOTIFY_RECIPIENTS)
    msg["Subject"] = f"Sien-18: {CHILD_NAME} ist für heute angemeldet ✓ ({today})"
    msg.set_content(
        f"Hallo,\n\n"
        f"Sien-18 hat {CHILD_NAME} erfolgreich für die heutige Kita-Notbetreuung angemeldet.\n\n"
        f"Datum:   {today}\n"
        f"Status:  {result['action']}\n"
        f"Doodle:  {doodle_url}\n\n"
        + (f"Screenshots ({len(screenshots)}) sind als Anhang beigefügt.\n\n" if screenshots else "")
        + f"Diese Nachricht wurde automatisch von Sien-18 gesendet.\n"
    )

    labels = {
        "/tmp/sien18_1_session_selected.png": "1_session_selected.png",
        "/tmp/sien18_2_form_filled.png":      "2_form_filled.png",
        "/tmp/sien18_3_booking_confirmed.png": "3_booking_confirmed.png",
    }
    for path in screenshots:
        p = Path(path)
        if p.exists():
            msg.add_attachment(
                p.read_bytes(),
                maintype="image",
                subtype="png",
                filename=labels.get(path, p.name),
            )

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info(f"Notification with {len(screenshots)} screenshot(s) sent to {NOTIFY_RECIPIENTS}.")
    except Exception as exc:
        log.error(f"Failed to send notification: {exc}")


# ── Weekly summary ─────────────────────────────────────────────────────────────

SFN_STATE_MACHINE_NAME = "kita-bot"

_DAY_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag"]

def _sfn_client():
    import boto3
    return boto3.client("stepfunctions", region_name=os.environ.get("AWS_REGION", "eu-west-1"))


def _get_account_id() -> str:
    import boto3
    return boto3.client("sts", region_name=os.environ.get("AWS_REGION", "eu-west-1")) \
                .get_caller_identity()["Account"]


def _sm_arn() -> str:
    region = os.environ.get("AWS_REGION", "eu-west-1")
    account = _get_account_id()
    return f"arn:aws:states:{region}:{account}:stateMachine:{SFN_STATE_MACHINE_NAME}"


def _get_week_executions() -> list[dict]:
    """Return all Step Functions executions that started this Mon–Fri (Berlin time)."""
    from zoneinfo import ZoneInfo
    from datetime import timezone, timedelta

    berlin = ZoneInfo("Europe/Berlin")
    now = datetime.now(berlin)
    monday = now - timedelta(days=now.weekday())
    week_start = monday.replace(hour=0, minute=0, second=0, microsecond=0)

    executions = []
    paginator = _sfn_client().get_paginator("list_executions")
    for page in paginator.paginate(stateMachineArn=_sm_arn()):
        for ex in page["executions"]:
            start = ex["startDate"]
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            if start < week_start:
                return executions  # list_executions is newest-first; nothing older needed
            executions.append(ex)
    return executions


def _classify_execution(ex: dict) -> str:
    """Map an execution to a bot action string via DescribeExecution for accuracy."""
    status = ex.get("status", "")
    if status == "RUNNING":
        return "running"
    if status == "ABORTED":
        return "aborted"
    if status == "SUCCEEDED":
        try:
            sfn = _sfn_client()
            detail = sfn.describe_execution(executionArn=ex["executionArn"])
            output = json.loads(detail.get("output") or "{}")
            action = output.get("action", "submitted")
            if action in ("already_registered", "submitted_unverified", "dry_run"):
                return action
        except Exception:
            pass
        return "submitted"
    if status == "FAILED":
        try:
            sfn = _sfn_client()
            detail = sfn.describe_execution(executionArn=ex["executionArn"])
            cause = (detail.get("cause") or "").lower()
            if "noseats" in cause or "no seats" in cause:
                return "no_seats"
            if "deadline" in cause:
                return "deadline_reached"
        except Exception:
            pass
        return "failed"
    return "unknown"


def _build_weekly_summary_body(executions: list[dict]) -> str:
    from zoneinfo import ZoneInfo
    from datetime import timezone, timedelta

    berlin = ZoneInfo("Europe/Berlin")
    now = datetime.now(berlin)
    monday = now - timedelta(days=now.weekday())

    day_results: dict[int, str] = {}  # weekday index (0=Mon) → action
    for ex in executions:
        start = ex["startDate"]
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start_berlin = start.astimezone(berlin)
        wd = start_berlin.weekday()
        if wd not in day_results:
            day_results[wd] = _classify_execution(ex)

    lines = []
    registered_count = 0
    for wd in range(5):  # Mon–Fri
        day_name = _DAY_DE[wd]
        day_date = (monday + timedelta(days=wd)).strftime("%d.%m.")
        action = day_results.get(wd)

        if action is None:
            line = f"  {day_name} ({day_date}): 😴 Kein Einsatz — wahrscheinlich Feiertag oder die Kita macht einfach was sie will."
        elif action == "submitted":
            registered_count += 1
            line = f"  {day_name} ({day_date}): ✅ Dalia wurde erfolgreich abgeliefert. Bot 1 – Chaos 0."
        elif action == "submitted_unverified":
            registered_count += 1
            line = f"  {day_name} ({day_date}): ✅ Dalia wurde angemeldet (vermutlich). Der Bot ist sich zu 90% sicher."
        elif action == "already_registered":
            registered_count += 1
            line = f"  {day_name} ({day_date}): 🤦 Dalia war schon angemeldet. Eltern: bitte nicht doppelt buchen."
        elif action == "deadline_reached":
            line = (
                f"  {day_name} ({day_date}): 🚶 Keine E-Mail von der Kita. Dalia ist höchstwahrscheinlich "
                f"einfach selbst hingegangen — ohne Erlaubnis, ohne Doodle, ohne uns."
            )
        elif action == "no_seats":
            line = f"  {day_name} ({day_date}): 😬 Kein Platz mehr. Dalia musste draußen warten. Peinlich."
        elif action == "aborted":
            line = f"  {day_name} ({day_date}): ⚡ Ausführung abgebrochen. Jemand hat den Stecker gezogen."
        elif action == "running":
            line = f"  {day_name} ({day_date}): ⏳ Läuft noch... Geduld ist eine Tugend."
        elif action == "failed":
            line = f"  {day_name} ({day_date}): 💥 Unerwarteter Fehler. Der Bot hat kurz das Bewusstsein verloren."
        else:
            line = f"  {day_name} ({day_date}): 🤷 Unbekanntes Schicksal. Der Bot schweigt."
        lines.append(line)

    week_str = f"{(monday).strftime('%d.%m.')}–{(monday + timedelta(days=4)).strftime('%d.%m.%Y')}"

    if registered_count == 5:
        verdict = "🏆 Perfekte Woche! Dalia war jeden Tag brav in der Kita. Der Bot verdient Urlaub."
    elif registered_count > 0:
        verdict = f"📊 {registered_count} von 5 Tagen erfolgreich angemeldet. Nicht schlecht, aber der Bot hat schon bessere Zeiten gesehen."
    else:
        verdict = "🤖 Null Anmeldungen diese Woche. Entweder war die Kita zu oder Dalia regelt das selbst."

    body = (
        f"Hallo zusammen,\n\n"
        f"Hier ist der Wochenbericht von Sien-18 für die Woche {week_str}:\n\n"
        + "\n".join(lines)
        + f"\n\n{verdict}\n\n"
        f"In tiefer Pflichterfüllung,\n"
        f"Sien-18 🤖\n"
        f"(offiziell zuständig für Dalias Kita-Logistik seit 2026)\n"
    )
    return body


def send_weekly_summary(service, body: str):
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Europe/Berlin"))
    week_str = now.strftime("KW %V · %d.%m.%Y")

    msg = email.message.EmailMessage()
    msg["From"] = "me"
    msg["To"] = ", ".join(NOTIFY_RECIPIENTS)
    msg["Subject"] = f"Sien-18 Wochenbericht 📋 {week_str}"
    msg.set_content(body)

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log.info(f"Weekly summary sent to {NOTIFY_RECIPIENTS}.")
    except Exception as exc:
        log.error(f"Failed to send weekly summary: {exc}")


def weekly_summary_handler(event: dict, context) -> dict:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Europe/Berlin"))
    if now.weekday() != 4:  # 4 = Friday; schedule fires Mon-Fri but we only send on Friday
        log.info(f"Skipping weekly summary — today is {_DAY_DE[now.weekday()]}, not Friday.")
        return {"action": "skipped_not_friday"}

    log.info("=== Sien-18 weekly summary starting ===")
    service = get_gmail_service()
    executions = _get_week_executions()
    log.info(f"Found {len(executions)} execution(s) this week.")
    body = _build_weekly_summary_body(executions)
    log.info(f"Summary body:\n{body}")
    send_weekly_summary(service, body)
    return {"action": "weekly_summary_sent", "executions": len(executions)}


# ── Lambda entry point ─────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    if event.get("weekly_summary"):
        return weekly_summary_handler(event, context)

    attempt = event.get("attempt", 0)
    # Event values take precedence; env vars allow direct Lambda console testing
    url     = event.get("url") or os.environ.get("URL") or None
    debug   = bool(event.get("debug", False))
    dry_run = bool(event.get("dry_run", False)) or os.environ.get("DRY_RUN", "").lower() == "true"

    log.info(f"=== Sien-18 starting (attempt {attempt}) ===")

    service = get_gmail_service()

    if url:
        doodle_url = url
        log.info(f"URL from event (test mode): {doodle_url}")
    else:
        msg = find_kita_email(service)
        if not msg:
            log.warning("No Kita email found yet.")
            return {"action": "no_email", "dalia_registered": False, "seats_available": None}

        doodle_url = extract_doodle_link(msg)
        if not doodle_url:
            log.error("Kita email found but no Doodle link inside.")
            return {"action": "no_link", "dalia_registered": False, "seats_available": None}

    log.info(f"Doodle URL: {doodle_url}")
    result = check_and_register(doodle_url, dry_run=dry_run, debug=debug)

    if result["action"] in ("submitted", "submitted_unverified"):
        _wait_and_confirm(service)
        send_notification(service, result, doodle_url)

    log.info(f"Result: {result}")
    return result
