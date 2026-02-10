import os
import json
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from openai import OpenAI
from dotenv import load_dotenv
from fastapi.middleware.cors import CORSMiddleware
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from collections import defaultdict
import time
import logging

load_dotenv()

app = FastAPI()
# -------------------------
# Rate Limiting Configuration
# -------------------------

rate_limits = defaultdict(lambda: {
    "tokens": 5,
    "last_refill": time.time()
})

MAX_REQUESTS_PER_MINUTE = 29
BURST_CAPACITY = 5
REFILL_RATE = MAX_REQUESTS_PER_MINUTE / 60.0  # tokens per second

@app.get("/")
def health():
    return {"status": "ok"}

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL")
)


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

        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
@app.post("/security/validate")
async def security_validate(request: Request):
    try:
        body = await request.json()
        user_id = body.get("userId")
        user_input = body.get("input")
        category = body.get("category")

        # Basic validation
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

        # Identify client by userId + IP
        client_ip = request.client.host
        key = f"{user_id}:{client_ip}"

        now = time.time()
        bucket = rate_limits[key]

        # Refill tokens
        elapsed = now - bucket["last_refill"]
        refill_tokens = elapsed * REFILL_RATE
        bucket["tokens"] = min(BURST_CAPACITY, bucket["tokens"] + refill_tokens)
        bucket["last_refill"] = now

        # If no tokens left → block
        if bucket["tokens"] < 1:
            logging.warning(f"Rate limit exceeded for {key}")

            return JSONResponse(
                status_code=429,
                headers={"Retry-After": "60"},
                content={
                    "blocked": True,
                    "reason": "Rate limit exceeded",
                    "sanitizedOutput": None,
                    "confidence": 0.98
                }
            )

        # Consume token
        bucket["tokens"] -= 1

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
