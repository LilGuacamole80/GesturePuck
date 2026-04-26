from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
import requests
import asyncio
import json
import os
from dotenv import load_dotenv

app = FastAPI(title="Gesture Puck")
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/login", response_class=HTMLResponse)
def board(request: Request, session_token: str | None = Cookie(None), conn=Depends(get_db), current_user=Depends(get_current_user)):
    #if not session_token:
        #raise HTTPException(status_code=401, detail="Not authenticated")
    return templates.TemplateResponse("index.html", {"request": request, "username": current_user["username"]})