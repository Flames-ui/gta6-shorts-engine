"""Social media copy, viral title, and hashtag generation."""
import json
import logging
from typing import Any, Dict, List, Optional
from ..config import settings

logger = logging.getLogger("gta6_shorts.social")

DEFAULT_GTA_HASHTAGS = ["#GTA6", "#GTA6Trailer", "#Gaming", "#Shorts", "#ViceCity", "#GTA6Clips", "#Viral"]


def generate_clip_social_metadata(
    clip_text: str,
    original_title: Optional[str] = None,
    virality_reason: Optional[str] = None,
    gta_preset: bool = True,
    llm_provider: Optional[str] = None,
) -> Dict[str, Any]:
    """Generate viral title, hook, description, and hashtags using Gemini or OpenAI."""
    provider = (llm_provider or settings.LLM_PROVIDER or "gemini").lower()

    prompt = f"""You are a top-tier YouTube Shorts & TikTok growth strategist specializing in viral gaming content (especially GTA 6, action games, and gaming culture).

Analyze this clip excerpt and generate high-performing social metadata.

Video Title: {original_title or "GTA 6 Gameplay / Discussion"}
Clip Context/Reason: {virality_reason or "Intense/surprising gaming moment"}
Clip Subtitles / Transcript:
"{clip_text[:1200]}"

Requirements:
1. Title: Short, punchy, high-CTR (under 50 chars), includes 1 emoji, designed for Shorts feed.
2. Hook: First 3-second hook text displayed on screen.
3. Description: 2-3 engaging sentences with a call to action.
4. Hashtags: 5-8 relevant hashtags (must include #Shorts, #Gaming, and GTA 6 tags if relevant).

Respond with pure JSON only in this exact format:
{{
  "title": "GTA 6 Just Broke The Internet! 🤯",
  "hook": "Wait until you see what happened next...",
  "description": "Is GTA 6 about to change gaming forever? Drop your thoughts in the comments! Subscribe for daily GTA 6 leaks and clips.",
  "hashtags": ["#GTA6", "#GTA6Clips", "#Gaming", "#Shorts", "#ViceCity"]
}}
"""

    # 1. Try Gemini
    if provider == "gemini" and settings.GEMINI_API_KEY:
        try:
            from google import genai
            client = genai.Client(api_key=settings.GEMINI_API_KEY)
            res = client.models.generate_content(
                model=settings.GEMINI_MODEL,
                contents=prompt,
                config={"temperature": 0.3, "response_mime_type": "application/json"},
            )
            data = json.loads(res.text)
            return data
        except Exception as e:
            logger.warning("Gemini social metadata generation failed: %s", e)

    # 2. Try OpenAI
    if settings.OPENAI_API_KEY:
        try:
            from openai import OpenAI
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            res = client.chat.completions.create(
                model=settings.OPENAI_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                response_format={"type": "json_object"},
            )
            data = json.loads(res.choices[0].message.content)
            return data
        except Exception as e:
            logger.warning("OpenAI social metadata generation failed: %s", e)

    # 3. Rule-based fallback if no LLM key is configured
    words = clip_text.split()[:8]
    clean_title = " ".join(words).strip() or "INSANE GTA 6 Moment!"
    if len(clean_title) > 40:
        clean_title = clean_title[:37] + "..."
    title = f"{clean_title} 🤯" if gta_preset else f"{clean_title} 🔥"
    return {
        "title": title,
        "hook": "You won't believe this moment! 👀",
        "description": "Watch until the end! Subscribe for more epic clips.",
        "hashtags": DEFAULT_GTA_HASHTAGS if gta_preset else ["#Shorts", "#Viral", "#Gaming"],
    }
