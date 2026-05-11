from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi import Depends
import uvicorn
import requests
import asyncio
import json
import os
from dotenv import load_dotenv
import secrets
import base64
import hashlib
from urllib.parse import urlencode
import httpx
import uuid
import mysql.connector

OIDC_CLIENT_ID = os.environ["OIDC_CLIENT_ID"]
OIDC_CLIENT_SECRET = os.environ["OIDC_CLIENT_SECRET"]
OIDC_REDIRECT_URI = os.environ.get("OIDC_REDIRECT_URI", "http://localhost:5500/callback")

OIDC_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
OIDC_TOKEN_URL = "https://oauth2.googleapis.com/token"
OIDC_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

pkce_store: dict[str, str] = {}

users = {}
sessions = {}

app = FastAPI(title="Gesture Puck")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/assets", StaticFiles(directory="assets"), name="assets")

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html")

@app.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return templates.TemplateResponse(request, "settings.html")

@app.get("/team", response_class=HTMLResponse)
def settings(request: Request):
    return templates.TemplateResponse(request, "team.html")

@app.get("/careers", response_class=HTMLResponse)
def settings(request: Request):
    return templates.TemplateResponse(request, "careers.html")

def get_db():
    conn = mysql.connector.connect(
        host=os.environ["DB_HOST"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        database=os.environ["DB_NAME"],
    )
    try:
        yield conn
    finally:
        conn.close()

@app.get("/login")
def login():
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode()).digest()
    ).rstrip(b"=").decode()

    pkce_store[state] = code_verifier

    params = urlencode({
        "response_type": "code",
        "client_id": OIDC_CLIENT_ID,
        "redirect_uri": OIDC_REDIRECT_URI,
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    })
    return RedirectResponse(f"{OIDC_AUTHORIZE_URL}?{params}")

@app.get("/callback")
def callback(code: str, state: str, request: Request, conn=Depends(get_db)):
    code_verifier = pkce_store.pop(state, None)
    if not code_verifier:
        raise HTTPException(status_code=400, detail="Invalid state parameter")

    # Exchange authorization code for tokens
    token_response = httpx.post(
        OIDC_TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": OIDC_REDIRECT_URI,
            "client_id": OIDC_CLIENT_ID,
            "client_secret": OIDC_CLIENT_SECRET,
            "code_verifier": code_verifier,
        },
    )
    if token_response.status_code != 200:
        raise HTTPException(status_code=401, detail="Token exchange failed")

    tokens = token_response.json()
    access_token = tokens["access_token"]

    # Fetch user info
    userinfo_response = httpx.get(
        OIDC_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    if userinfo_response.status_code != 200:
        raise HTTPException(status_code=401, detail="Failed to fetch user info")

    userinfo = userinfo_response.json()
    sub = userinfo["sub"]
    username = userinfo.get("name", sub)
    email = userinfo.get("email", "")
    picture = userinfo.get("picture", "")

    # Upsert user: create if new, update if existing
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT id FROM users WHERE sub = %s", (sub,))
    existing = cursor.fetchone()
    if existing:
        user_id = existing["id"]
        cursor.execute(
            "UPDATE users SET username = %s, email = %s WHERE id = %s",
            (username, email, user_id),
        )
    else:
        cursor.execute(
            "INSERT INTO users (sub, username, email) VALUES (%s, %s, %s)",
            (sub, username, email),
        )
        user_id = cursor.lastrowid
    
    
    # Create session
    session_token = str(uuid.uuid4())
    users[sub] = userinfo
    sessions[session_token] = sub

    response = templates.TemplateResponse(request, "gestures.html", {"username": username, "picture": picture})
    response.set_cookie(key="session_token", value=session_token, httponly=True)
    return response

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5500)