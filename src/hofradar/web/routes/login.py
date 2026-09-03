"""The login and logout pages.

Only mounted when a password is configured, so a localhost run never shows a
form it does not need.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from hofradar.web import auth
from hofradar.web.deps import render

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_page(request: Request, next: str = "/"):
    if auth.is_authenticated(request):
        return RedirectResponse(auth.safe_next_path(next), status_code=303)
    return render(
        request,
        "pages/login.html",
        {"error": None, "next": auth.safe_next_path(next), "locked_for": 0},
        status_code=200,
    )


@router.post("/login")
def login_submit(
    request: Request,
    password: str = Form(default=""),
    next: str = Form(default="/"),
):
    destination = auth.safe_next_path(next)
    client = auth.client_key(request)

    locked_for = auth.locked_out_for(client)
    if locked_for:
        return render(
            request,
            "pages/login.html",
            {
                "error": "Zu viele Fehlversuche. Bitte kurz warten.",
                "next": destination,
                "locked_for": locked_for,
            },
            status_code=429,
        )

    if not auth.verify_password(password):
        auth.record_failure(client)
        return render(
            request,
            "pages/login.html",
            {"error": "Falsches Passwort.", "next": destination, "locked_for": 0},
            status_code=401,
        )

    auth.clear_failures(client)
    response = RedirectResponse(destination, status_code=303)
    auth.set_session_cookie(response, request)
    return response


@router.post("/logout")
def logout(request: Request):
    response = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(response)
    return response


@router.get("/logout")
def logout_get(request: Request):
    return logout(request)
