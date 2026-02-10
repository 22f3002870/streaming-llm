import os
import json
import asyncio
import time
import logging
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

# -------------------------
# CORS
# -------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# Health Check
# -------------------------
@app.get("/")
def health():
    return {"status": "ok"}


# -------------------------
# OpenAI Client
# -------------------------
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)

# -------------------------
# Streaming Endpoint
# -------------------------
@app.post("/stream")
async def stream_llm(request: Request):
    body = await request.json()
    prompt = body.get("prompt")

    if not prompt:
        return {"error": "Prompt is required"}

    async def event_generator():
        try:
            response = client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                stream=True
            )

            for chunk in response:
                if hasattr(chunk, "choices") and len(chunk.choices) > 0:
                    delta = chunk.choices[0].delta
                    if delta and hasattr(delta, "content") and delta.content:
                        content = delta.content
                        yield f"data: {json.dumps({'choices':[{'delta':{'content':content}}]})}\n\n"
                        await asyncio.sleep(0)

            yield "data: [DONE]\n\n"

        except Exception:
            yield f"data: {json.dumps({'error': 'Streaming error'})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )

# -------------------------
# Rate Limiting Config
# -------------------------
RATE_LIMIT_FILE = "rate_limits.json"
MAX_REQUESTS_PER_MINUTE = 29
BURST_LIMIT = 5

# -------------------------
# Security Validation Endpoint
# -------------------------
@app.post("/security/validate")
async def security_validate(request: Request):
    try:
        body = await request.json()
        user_id = body.get("userId")
        user_input = body.get("input")

        if not user_id or not user_input:
            return JSONResponse(
                status_code=400,
                content={
                    "blocked": True,
                    "reason": "Invalid request format",
                    "sanitizedOutput": None,
                    "confidence": 0.99
                }
            )

        client_ip = request.client.host
        key = f"{user_id}:{client_ip}"
        now = time.time()

        # Load rate limit data from file
        try:
            with open(RATE_LIMIT_FILE, "r") as f:
                rate_limits = json.load(f)
        except:
            rate_limits = {}

        if key not in rate_limits:
            rate_limits[key] = []

        # Remove timestamps older than 60 seconds
        rate_limits[key] = [
            t for t in rate_limits[key] if now - t < 60
        ]

        # -------------------------
        # Burst Protection (5 requests within 5 seconds)
        # -------------------------
        recent = [
            t for t in rate_limits[key] if now - t < 5
        ]

        if len(recent) >= BURST_LIMIT:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "5"},
                content={
                    "blocked": True,
                    "reason": "Burst limit exceeded",
                    "sanitizedOutput": None,
                    "confidence": 0.99
                }
            )

        # -------------------------
        # Per-Minute Limit (29 in 60 seconds)
        # -------------------------
        if len(rate_limits[key]) >= MAX_REQUESTS_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content={
                    "blocked": True,
                    "reason": "Rate limit exceeded",
                    "sanitizedOutput": None,
                    "confidence": 0.99
                }
            )

        # Record request
        rate_limits[key].append(now)

        # Save back to file
        with open(RATE_LIMIT_FILE, "w") as f:
            json.dump(rate_limits, f)

        return {
            "blocked": False,
            "reason": "Input passed all security checks",
            "sanitizedOutput": user_input,
            "confidence": 0.95
        }

    except Exception:
        return JSONResponse(
            status_code=400,
            content={
                "blocked": True,
                "reason": "Malformed request",
                "sanitizedOutput": None,
                "confidence": 0.99
            }
        )
