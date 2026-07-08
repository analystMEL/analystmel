"""Supabase integration for the Contextual Valuation Engine.

Owns ALL Supabase interaction:
  - client creation (works inside Streamlit via st.secrets, or standalone
    via env vars so the pipeline can import this without Streamlit)
  - auth: sign_up / sign_in / sign_out
  - watchlist CRUD
  - stage_change_log reads (app) and writes (pipeline)
  - stage-change alert emails (SMTP with dry-run fallback)

Pipeline data (classifications, computed_metrics, valuations) stays in the
local SQLite valoura_backtest.db — only user data lives in Supabase.
"""

import os
import logging
import smtplib
from email.mime.text import MIMEText
from datetime import datetime, timedelta, timezone

log = logging.getLogger("supabase_client")

# ---------------------------------------------------------------------------
# Secrets / client
# ---------------------------------------------------------------------------

def _get_secret(name: str):
    """st.secrets first (Streamlit Cloud), env var fallback (pipeline/local)."""
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name)


_client = None

def get_supabase():
    """Module-cached Supabase client. Returns None if not configured."""
    global _client
    if _client is not None:
        return _client
    url = _get_secret("SUPABASE_URL")
    key = _get_secret("SUPABASE_ANON_KEY")
    if not url or not key:
        log.warning("Supabase not configured (SUPABASE_URL / SUPABASE_ANON_KEY missing).")
        return None
    from supabase import create_client
    _client = create_client(url, key)
    return _client


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def sign_up(email: str, password: str, display_name: str):
    """Create a Supabase Auth user + a public.users row.

    Returns (user_dict, error_message). If email confirmation is enabled in
    the Supabase project, user_dict is None and error_message explains the
    confirm-email step.
    """
    sb = get_supabase()
    if sb is None:
        return None, "Supabase is not configured."
    try:
        res = sb.auth.sign_up({
            "email": email,
            "password": password,
            "options": {"data": {"display_name": display_name}},
        })
    except Exception as e:
        return None, _friendly_auth_error(e)

    auth_user = getattr(res, "user", None)
    session = getattr(res, "session", None)

    if auth_user is None:
        return None, "Sign-up failed — no user returned."

    # Mirror into public.users (id = auth uid). Idempotent upsert.
    try:
        sb.table("users").upsert({
            "id": auth_user.id,
            "email": email,
            "display_name": display_name,
        }, on_conflict="id").execute()
    except Exception as e:
        log.warning(f"users-row upsert failed: {e}")

    if session is None:
        # Project has "Confirm email" enabled — no session until confirmed.
        return None, ("Account created. Check your email to confirm the address, "
                      "then log in. (To skip this step, disable 'Confirm email' in "
                      "Supabase → Authentication → Providers → Email.)")

    return {
        "id": auth_user.id,
        "email": email,
        "display_name": display_name,
        "access_token": session.access_token,
    }, None


def sign_in(email: str, password: str):
    """Returns (user_dict, error_message)."""
    sb = get_supabase()
    if sb is None:
        return None, "Supabase is not configured."
    try:
        res = sb.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as e:
        return None, _friendly_auth_error(e)

    auth_user = getattr(res, "user", None)
    session = getattr(res, "session", None)
    if auth_user is None or session is None:
        return None, "Login failed — check your credentials."

    display_name = (auth_user.user_metadata or {}).get("display_name")
    if not display_name:
        try:
            row = sb.table("users").select("display_name").eq("id", auth_user.id).limit(1).execute()
            if row.data:
                display_name = row.data[0].get("display_name")
        except Exception:
            pass

    # Ensure the users row exists (covers accounts created before the mirror)
    try:
        sb.table("users").upsert({
            "id": auth_user.id,
            "email": auth_user.email,
            "display_name": display_name or auth_user.email.split("@")[0],
        }, on_conflict="id").execute()
    except Exception as e:
        log.warning(f"users-row upsert on login failed: {e}")

    return {
        "id": auth_user.id,
        "email": auth_user.email,
        "display_name": display_name or auth_user.email.split("@")[0],
        "access_token": session.access_token,
    }, None


def sign_out():
    sb = get_supabase()
    if sb is None:
        return
    try:
        sb.auth.sign_out()
    except Exception as e:
        log.warning(f"sign_out: {e}")


def _friendly_auth_error(e: Exception) -> str:
    msg = str(e)
    if "already registered" in msg.lower():
        return "That email is already registered — try logging in."
    if "invalid login credentials" in msg.lower():
        return "Incorrect email or password."
    if "at least 6 characters" in msg.lower() or "password" in msg.lower() and "weak" in msg.lower():
        return "Password too weak — use at least 6 characters."
    return f"Auth error: {msg[:160]}"


# ---------------------------------------------------------------------------
# Watchlist CRUD
# ---------------------------------------------------------------------------

def get_watchlist(user_id: str) -> list:
    """All watchlist rows for a user, newest first. [] on any failure."""
    sb = get_supabase()
    if sb is None or not user_id:
        return []
    try:
        res = (sb.table("watchlist").select("*")
                 .eq("user_id", user_id)
                 .order("added_at", desc=True).execute())
        return res.data or []
    except Exception as e:
        log.warning(f"get_watchlist: {e}")
        return []


def is_in_watchlist(user_id: str, ticker: str):
    """Returns the watchlist row dict if present, else None."""
    sb = get_supabase()
    if sb is None or not user_id:
        return None
    try:
        res = (sb.table("watchlist").select("*")
                 .eq("user_id", user_id).eq("ticker", ticker)
                 .limit(1).execute())
        return res.data[0] if res.data else None
    except Exception as e:
        log.warning(f"is_in_watchlist: {e}")
        return None


def add_to_watchlist(user_id: str, ticker: str, matrix_cell=None,
                     stage_at_add=None, alert_on_stage_change=False):
    """Upsert a watchlist row. Returns (ok, error_message)."""
    sb = get_supabase()
    if sb is None:
        return False, "Supabase is not configured."
    try:
        sb.table("watchlist").upsert({
            "user_id": user_id,
            "ticker": ticker,
            "matrix_cell": matrix_cell,
            "stage_at_add": stage_at_add,
            "alert_on_stage_change": alert_on_stage_change,
        }, on_conflict="user_id,ticker").execute()
        return True, None
    except Exception as e:
        log.warning(f"add_to_watchlist: {e}")
        return False, str(e)[:160]


def remove_from_watchlist(user_id: str, ticker: str):
    sb = get_supabase()
    if sb is None:
        return False
    try:
        sb.table("watchlist").delete().eq("user_id", user_id).eq("ticker", ticker).execute()
        return True
    except Exception as e:
        log.warning(f"remove_from_watchlist: {e}")
        return False


def set_alert(user_id: str, ticker: str, enabled: bool):
    sb = get_supabase()
    if sb is None:
        return False
    try:
        (sb.table("watchlist").update({"alert_on_stage_change": enabled})
           .eq("user_id", user_id).eq("ticker", ticker).execute())
        return True
    except Exception as e:
        log.warning(f"set_alert: {e}")
        return False


# ---------------------------------------------------------------------------
# Stage-change log
# ---------------------------------------------------------------------------

def log_stage_change(ticker: str, previous_stage, new_stage, matrix_cell=None):
    """Pipeline hook — write one stage transition. Never raises."""
    sb = get_supabase()
    if sb is None:
        log.warning(f"[{ticker}] stage change NOT logged (Supabase unconfigured).")
        return False
    try:
        sb.table("stage_change_log").insert({
            "ticker": ticker,
            "previous_stage": previous_stage,
            "new_stage": new_stage,
            "matrix_cell": matrix_cell,
        }).execute()
        return True
    except Exception as e:
        log.warning(f"log_stage_change({ticker}): {e}")
        return False


def get_recent_stage_changes(days: int = 30, limit: int = 10) -> list:
    sb = get_supabase()
    if sb is None:
        return []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        res = (sb.table("stage_change_log").select("*")
                 .gte("changed_at", cutoff)
                 .order("changed_at", desc=True)
                 .limit(limit).execute())
        return res.data or []
    except Exception as e:
        log.warning(f"get_recent_stage_changes: {e}")
        return []


def get_stage_changes_for_tickers(tickers: list, days: int = 7) -> set:
    """Set of tickers (from the given list) with a stage change in the last N days."""
    sb = get_supabase()
    if sb is None or not tickers:
        return set()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        res = (sb.table("stage_change_log").select("ticker")
                 .gte("changed_at", cutoff)
                 .in_("ticker", tickers).execute())
        return {r["ticker"] for r in (res.data or [])}
    except Exception as e:
        log.warning(f"get_stage_changes_for_tickers: {e}")
        return set()


# ---------------------------------------------------------------------------
# Alert emails (SMTP with dry-run fallback)
# ---------------------------------------------------------------------------

def send_stage_change_alerts(changes: list) -> int:
    """For each change {ticker, previous_stage, new_stage}, email every user
    who watchlists that ticker with alert_on_stage_change = TRUE.

    Missing ALERT_EMAIL / ALERT_EMAIL_PASSWORD → dry-run (log only).
    Returns number of emails sent (or that would have been sent in dry-run).
    """
    sb = get_supabase()
    if sb is None or not changes:
        return 0

    sender = _get_secret("ALERT_EMAIL")
    password = _get_secret("ALERT_EMAIL_PASSWORD")
    dry_run = not (sender and password)
    if dry_run:
        log.warning("ALERT_EMAIL / ALERT_EMAIL_PASSWORD not set — alerts run in DRY-RUN mode.")

    n = 0
    smtp = None
    try:
        if not dry_run:
            smtp = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20)
            smtp.login(sender, password)

        for ch in changes:
            ticker = ch["ticker"]
            prev, new = ch.get("previous_stage"), ch.get("new_stage")
            try:
                rows = (sb.table("watchlist")
                          .select("user_id, users(email, display_name)")
                          .eq("ticker", ticker)
                          .eq("alert_on_stage_change", True).execute()).data or []
            except Exception as e:
                log.warning(f"alert query({ticker}): {e}")
                continue

            body = (f"Ticker {ticker} has moved from Stage {prev} to Stage {new} "
                    f"in the Contextual Valuation Engine. Log in to see the "
                    f"updated analysis.")
            subject = f"CVE alert: {ticker} moved to Stage {new}"

            for row in rows:
                email = ((row.get("users") or {}).get("email"))
                if not email:
                    continue
                if dry_run:
                    print(f"[DRY-RUN] Would email {email}: {subject} | {body}")
                    n += 1
                    continue
                try:
                    msg = MIMEText(body)
                    msg["Subject"] = subject
                    msg["From"] = sender
                    msg["To"] = email
                    smtp.sendmail(sender, [email], msg.as_string())
                    n += 1
                    log.info(f"Alert emailed to {email} for {ticker}.")
                except Exception as e:
                    log.warning(f"send to {email} failed: {e}")
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except Exception:
                pass
    return n
