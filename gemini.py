#!/usr/bin/env python3
"""
Gemini API client with exponential backoff and fallback.
"""
import os
import time
import requests

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

def generate_with_retry(prompt, max_retries=5, initial_delay=2):
    """Generates content using Gemini with exponential backoff for rate limiting."""
    if not GEMINI_API_KEY:
        print("  GEMINI_API_KEY not set, skipping generation.")
        return ""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-latest:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 500, "temperature": 0.3}
    }

    delay = initial_delay
    for i in range(max_retries):
        try:
            resp = requests.post(url, json=payload, timeout=45)
            resp.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
            data = resp.json()
            if "candidates" in data and data["candidates"]:
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
            else:
                print(f"  Gemini response is empty or malformed: {data}")
                return ""

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 429:  # Rate limit exceeded
                print(f"  Rate limit exceeded. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2  # Exponential backoff
            else:
                print(f"  HTTP error calling Gemini: {e}")
                return ""
        except Exception as e:
            print(f"  Error calling Gemini: {e}")
            # On other errors, retry with backoff as well
            time.sleep(delay)
            delay *= 2

    print("  Max retries reached. Failed to get response from Gemini.")
    return ""
