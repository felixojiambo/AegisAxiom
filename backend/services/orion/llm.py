"""Orion SOW Builder — LLM call helper.

Copied from backend/routes/fireflies.py (_call_sow_drafter) and exposed
as a public module function for use by the Orion service layer.
"""

import os
import json
import logging

import httpx

logger = logging.getLogger(__name__)

SOW_DRAFTER_BASE_URL = os.getenv("SOW_DRAFTER_BASE_URL", "https://devs.ai/api/v1")
SOW_DRAFTER_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
SOW_DRAFTER_MODEL = os.getenv("SOW_DRAFTER_MODEL", "anthropic-claude-4-5-sonnet")


async def call_sow_drafter(system_prompt: str, user_message: str, parse_json: bool = True):
    """Call the SOW Drafter model and return the response.

    Args:
        system_prompt: The system prompt for the LLM.
        user_message: The user message / context to send.
        parse_json: If True, parse response as JSON (default). If False, return raw text.

    Handles: httpx call, redirect following, response format detection.
    Raises ValueError on failure.
    """
    ai_text = None
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{SOW_DRAFTER_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {SOW_DRAFTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": SOW_DRAFTER_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
    except Exception as e:
        logger.error(f"SOW Drafter API call failed: {e}")
        raise ValueError(f"AI service call failed: {str(e)[:200]}")

    # Parse the API response
    try:
        result = resp.json()
    except Exception:
        result = resp.text

    # Extract AI text from whatever format we got
    if isinstance(result, dict) and "choices" in result:
        ai_text = result["choices"][0]["message"]["content"]
    elif isinstance(result, dict) and "content" in result:
        ai_text = result["content"]
    elif isinstance(result, dict) and "response" in result:
        ai_text = result["response"]
    elif isinstance(result, str):
        ai_text = result
    else:
        logger.error(f"Unexpected response format: {str(result)[:500]}")
        raise ValueError("Unexpected AI response format")

    # Return raw text if JSON parsing not needed
    if not parse_json:
        return ai_text

    # Strip markdown fences and parse JSON
    try:
        clean = ai_text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[1] if "\n" in clean else clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
        return json.loads(clean)
    except json.JSONDecodeError:
        logger.error(f"Failed to parse AI text as JSON: {ai_text[:500]}")
        raise ValueError("AI returned invalid format. Please try again.")
