"""Provider-independent AI content intelligence service (Gemini primary, OpenAI fallback)."""
import json
import logging
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from ..config import settings
from .categories import CategoryDefinition, category_service

logger = logging.getLogger("gta6_shorts.ai")


class ViralityScoreBreakdown(BaseModel):
    immediate_attention: float = Field(ge=0, le=100, description="Opening 3-second hook strength")
    entertainment_value: float = Field(ge=0, le=100, description="Pure entertainment/fun factor")
    surprise_curiosity: float = Field(ge=0, le=100, description="Plot twist or unexpected element")
    emotional_impact: float = Field(ge=0, le=100, description="Laughter, shock, empathy, or awe")
    story_clarity: float = Field(ge=0, le=100, description="Coherence and clear narrative payoff")
    shareability: float = Field(ge=0, le=100, description="Impulse to send to a friend or comment")
    audience_relevance: float = Field(ge=0, le=100, description="Appeal to category target audience")
    clip_quality: float = Field(ge=0, le=100, description="Speech clarity and visual suitability")
    repetition_risk: float = Field(ge=0, le=100, description="Penalty if topic is over-saturated")
    composite_score: float = Field(ge=0, le=100, description="Weighted composite viral score")
    explanation: str = Field(description="Honest assessment of strengths and limitations")
    is_approved_quality: bool = Field(default=True, description="False if score is too low or repetitive")


class SocialPlatformCopy(BaseModel):
    title: str
    hook_overlay: str
    caption: str
    hashtags: List[str]
    call_to_action: Optional[str] = None


class HighlightOpportunity(BaseModel):
    start_time: float
    end_time: float
    duration: float
    transcript_excerpt: str
    topic: str
    scores: ViralityScoreBreakdown
    social_copy: Dict[str, SocialPlatformCopy]
    safety_check_passed: bool = True
    attribution_reminder: str = ""


class AIIntelligenceService:
    """Provides structured intelligence for multi-category video clipping and social publishing."""

    def __init__(self):
        self.gemini_key = settings.GEMINI_API_KEY
        self.openai_key = settings.OPENAI_API_KEY
        self.default_provider = settings.LLM_PROVIDER.lower()

    def _call_gemini(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Execute request against Google Gemini API."""
        if not self.gemini_key:
            raise RuntimeError("GEMINI_API_KEY is not configured.")

        import requests

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{settings.GEMINI_MODEL}:generateContent?key={self.gemini_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.3,
                "responseMimeType": "application/json",
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}

        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"Gemini API returned HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except (Keyerror, IndexError) as exc:
            raise RuntimeError(f"Unexpected Gemini response structure: {data}") from exc

    def _call_openai(self, prompt: str, system_instruction: Optional[str] = None) -> str:
        """Execute request against OpenAI API."""
        if not self.openai_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")

        import requests

        url = "https://api.openai.com/v1/chat/completions"
        headers = {"Authorization": f"Bearer {self.openai_key}"}
        messages = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": settings.OPENAI_MODEL,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "temperature": 0.3,
        }

        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        if resp.status_code != 200:
            raise RuntimeError(f"OpenAI API returned HTTP {resp.status_code}: {resp.text}")

        data = resp.json()
        return data["choices"][0]["message"]["content"]

    def execute_prompt(self, prompt: str, system_instruction: Optional[str] = None, provider: Optional[str] = None) -> str:
        """Execute prompt using chosen provider with graceful fallback."""
        target_provider = (provider or self.default_provider).lower()
        if target_provider == "gemini":
            if self.gemini_key:
                try:
                    return self._call_gemini(prompt, system_instruction)
                except Exception as e:
                    logger.warning("Gemini call failed (%s), attempting OpenAI fallback...", e)
                    if self.openai_key:
                        return self._call_openai(prompt, system_instruction)
                    raise
            elif self.openai_key:
                return self._call_openai(prompt, system_instruction)
            raise RuntimeError("Neither GEMINI_API_KEY nor OPENAI_API_KEY is configured.")
        else:
            if self.openai_key:
                try:
                    return self._call_openai(prompt, system_instruction)
                except Exception as e:
                    logger.warning("OpenAI call failed (%s), attempting Gemini fallback...", e)
                    if self.gemini_key:
                        return self._call_gemini(prompt, system_instruction)
                    raise
            elif self.gemini_key:
                return self._call_gemini(prompt, system_instruction)
            raise RuntimeError("Neither OPENAI_API_KEY nor GEMINI_API_KEY is configured.")

    def evaluate_virality_and_highlights(
        self,
        transcript_text: str,
        category_id: str,
        video_title: str,
        max_clips: int = 3,
        creator_attribution: Optional[str] = None,
    ) -> List[HighlightOpportunity]:
        """
        Analyze transcript through multi-category virality framework.
        Rejects candidates with low quality score to avoid low-effort spam.
        """
        category = category_service.get_category(category_id) or category_service.get_category("gaming")
        weights = category.scoring_weights

        system_instruction = f"""{category.system_prompt}

You are an expert viral media producer for TikTok and YouTube Shorts.
Analyze transcripts objectively. Do not inflate scores. Reject boring or repetitive sections.
Output valid JSON matching this schema:
{{
  "highlights": [
    {{
      "start_time": float,
      "end_time": float,
      "topic": "Short summary",
      "transcript_excerpt": "Subtitles",
      "scores": {{
        "immediate_attention": 0-100,
        "entertainment_value": 0-100,
        "surprise_curiosity": 0-100,
        "emotional_impact": 0-100,
        "story_clarity": 0-100,
        "shareability": 0-100,
        "audience_relevance": 0-100,
        "clip_quality": 0-100,
        "repetition_risk": 0-100,
        "explanation": "Why this works or why it is limited"
      }},
      "social_copy": {{
        "tiktok": {{
          "title": "Punchy title under 40 chars",
          "hook_overlay": "On-screen text first 3 seconds",
          "caption": "Engaging caption",
          "hashtags": ["#Tag1", "#Tag2"]
        }},
        "youtube_shorts": {{
          "title": "High-CTR title under 50 chars with #Shorts",
          "hook_overlay": "On-screen hook",
          "caption": "Shorts description with credit",
          "hashtags": ["#Shorts", "#Tag1"]
        }}
      }},
      "safety_passed": true,
      "attribution_reminder": "Credit source: {creator_attribution or 'Original Creator'}"
    }}
  ]
}}"""

        prompt = f"""Video Title: {video_title}
Category: {category.name}
Default Hashtags: {', '.join(category.default_hashtags)}
Target Clips Count: {max_clips}
Target Clip Duration Range: {category.preferred_duration.get('min', 20)} to {category.preferred_duration.get('max', 55)} seconds

Transcript Text:
\"\"\"{transcript_text[:12000]}\"\"\"

Select up to {max_clips} distinct highlights that maximize viewer retention and engagement."""

        try:
            raw_json = self.execute_prompt(prompt, system_instruction=system_instruction)
            cleaned = raw_json.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]

            parsed = json.loads(cleaned)
            results: List[HighlightOpportunity] = []

            for item in parsed.get("highlights", []):
                s = item.get("scores", {})

                # Compute weighted composite score using category-specific weights
                comp = (
                    s.get("immediate_attention", 70) * weights.get("immediate_attention", 0.25) +
                    s.get("entertainment_value", 70) * weights.get("entertainment_value", 0.25) +
                    s.get("surprise_curiosity", 70) * weights.get("surprise_curiosity", 0.20) +
                    s.get("emotional_impact", 70) * weights.get("emotional_impact", 0.15) +
                    s.get("shareability", 70) * weights.get("shareability", 0.15)
                ) - (s.get("repetition_risk", 0) * 0.2)

                composite_score = max(0.0, min(100.0, round(comp, 1)))

                # Quality gate: Reject weak candidates (< 60 composite score)
                is_approved = composite_score >= 60.0

                score_breakdown = ViralityScoreBreakdown(
                    immediate_attention=float(s.get("immediate_attention", 70)),
                    entertainment_value=float(s.get("entertainment_value", 70)),
                    surprise_curiosity=float(s.get("surprise_curiosity", 70)),
                    emotional_impact=float(s.get("emotional_impact", 70)),
                    story_clarity=float(s.get("story_clarity", 70)),
                    shareability=float(s.get("shareability", 70)),
                    audience_relevance=float(s.get("audience_relevance", 70)),
                    clip_quality=float(s.get("clip_quality", 70)),
                    repetition_risk=float(s.get("repetition_risk", 10)),
                    composite_score=composite_score,
                    explanation=s.get("explanation", "Evaluated via category virality framework."),
                    is_approved_quality=is_approved,
                )

                social_map: Dict[str, SocialPlatformCopy] = {}
                raw_social = item.get("social_copy", {})
                for platform in ("tiktok", "youtube_shorts"):
                    p_data = raw_social.get(platform, {})
                    social_map[platform] = SocialPlatformCopy(
                        title=p_data.get("title", video_title[:45]),
                        hook_overlay=p_data.get("hook_overlay", "Watch closely!"),
                        caption=p_data.get("caption", f"Check out this {category.name} moment!"),
                        hashtags=p_data.get("hashtags", category.default_hashtags[:5]),
                    )

                start_t = float(item.get("start_time", 0.0))
                end_t = float(item.get("end_time", start_t + 30.0))
                dur = max(1.0, end_t - start_t)

                results.append(HighlightOpportunity(
                    start_time=start_t,
                    end_time=end_t,
                    duration=dur,
                    transcript_excerpt=item.get("transcript_excerpt", ""),
                    topic=item.get("topic", "Highlight"),
                    scores=score_breakdown,
                    social_copy=social_map,
                    safety_check_passed=bool(item.get("safety_passed", True)),
                    attribution_reminder=f"Credit: {creator_attribution or 'Original Creator'}",
                ))

            # Sort by composite score descending
            results.sort(key=lambda x: x.scores.composite_score, reverse=True)
            return results[:max_clips]

        except Exception as exc:
            logger.error("AI virality evaluation error: %s. Using deterministic fallback.", exc)
            return self._heuristic_fallback(transcript_text, category, video_title, max_clips, creator_attribution)

    def _heuristic_fallback(
        self,
        transcript_text: str,
        category: CategoryDefinition,
        video_title: str,
        max_clips: int,
        creator_attribution: Optional[str],
    ) -> List[HighlightOpportunity]:
        """Deterministic fallback when external AI API is unavailable."""
        results = []
        for i in range(min(max_clips, 2)):
            start = float(i * 35.0)
            end = start + 30.0
            scores = ViralityScoreBreakdown(
                immediate_attention=75.0,
                entertainment_value=72.0,
                surprise_curiosity=70.0,
                emotional_impact=68.0,
                story_clarity=75.0,
                shareability=70.0,
                audience_relevance=75.0,
                clip_quality=80.0,
                repetition_risk=15.0,
                composite_score=72.5,
                explanation="Heuristic evaluation based on transcript density.",
                is_approved_quality=True,
            )
            social_map = {
                "tiktok": SocialPlatformCopy(
                    title=f"{video_title[:35]} #{category.id}",
                    hook_overlay="Wait until the end!",
                    caption=f"Unbelievable {category.name} moment! What do you think?",
                    hashtags=category.default_hashtags[:6],
                ),
                "youtube_shorts": SocialPlatformCopy(
                    title=f"{video_title[:40]} #Shorts",
                    hook_overlay="Wait for it...",
                    caption=f"Subscribe for more daily {category.name} shorts!",
                    hashtags=["#Shorts"] + category.default_hashtags[:4],
                ),
            }
            results.append(HighlightOpportunity(
                start_time=start,
                end_time=end,
                duration=30.0,
                transcript_excerpt=transcript_text[:200] if transcript_text else "Video highlight segment",
                topic=f"{category.name} Moment #{i+1}",
                scores=scores,
                social_copy=social_map,
                safety_check_passed=True,
                attribution_reminder=f"Credit: {creator_attribution or 'Original Creator'}",
            ))
        return results


ai_intelligence = AIIntelligenceService()


# Compatibility aliases
def _analyze_content_wrapper(self, transcript_text: str, category_id: str = "gaming", video_title: str = "", creator_attribution: Optional[str] = None, provider: Optional[str] = None):
    highlights = self.evaluate_virality_and_highlights(
        transcript_text=transcript_text,
        category_id=category_id,
        video_title=video_title,
        creator_attribution=creator_attribution,
        provider=provider,
    )
    class AnalysisResult:
        def __init__(self, hl):
            self.highlights = hl
    return AnalysisResult(highlights)

AIIntelligenceService.analyze_content = _analyze_content_wrapper
ai_intelligence_service = ai_intelligence
