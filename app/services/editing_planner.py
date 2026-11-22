"""
Campaign-Specific Editing Planner.
Creates a comprehensive, structured pre-render editing plan tailored specifically
to the target campaign and platform (TikTok vs YouTube Shorts).
Ensures distinct campaigns produce distinctly differentiated editing decisions and packages.
"""
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gta6_shorts.services.editing_planner")


class EditingPlanner:
    """
    Generates structured editing plans prior to video rendering.
    Adapts pacing, hooks, captions, branding, zooms, and metadata to campaign rules.
    """

    def create_editing_plan(
        self,
        campaign: Dict[str, Any],
        candidate: Dict[str, Any],
        source_footage_path: str,
        platform: str = "tiktok",
        source_authorized: bool = True,
        creator_attribution: str = "Gameplay footage used with creator authorization",
        campaign_assets: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Builds a structured editing plan for a single clip.
        """
        campaign_id = campaign.get("id", "generic_camp")
        campaign_name = campaign.get("name", "Campaign")

        # Duration logic: campaign-specific bounds
        min_dur = float(campaign.get("min_clip_duration_seconds") or 20.0)
        max_dur = float(campaign.get("max_clip_duration_seconds") or 35.0)
        candidate_dur = float(candidate.get("duration") or (candidate.get("end_time", 25.0) - candidate.get("start_time", 0.0)))
        target_duration = min(max_dur, max(min_dur, candidate_dur))

        # Hook logic: campaign-specific opening style
        cand_hook = candidate.get("hook_text") or candidate.get("hook") or "Watch what happens next!"
        hook_style = "mid-action-drop"
        if "action" in campaign_name.lower():
            hook_style = "high-intensity-immediate"
        elif "story" in campaign_name.lower() or "lore" in campaign_name.lower():
            hook_style = "question-mystery-setup"

        opening_hook = {
            "text": cand_hook,
            "duration_seconds": 2.5,
            "style": hook_style,
            "visual_treatment": "bold-center-pop",
            "first_second_drop": True,
        }

        # Zooms and reframing: purposeful, not constant camera shake
        zooms_and_reframing = [
            {
                "timestamp_seconds": 0.0,
                "zoom_level": 1.15,
                "focus_area": "center_action",
                "purpose": "initial_hook_focus",
            },
            {
                "timestamp_seconds": round(target_duration * 0.6, 1),
                "zoom_level": 1.25,
                "focus_area": "climax_payoff",
                "purpose": "reaction_emphasis",
            },
        ]

        # Caption and subtitle treatment: 2-3 words per burst
        caption_color = "#FFFF00" if platform == "tiktok" else "#00FFCC"
        caption_treatment = {
            "style": "word_by_word_burst",
            "max_words_per_segment": 3,
            "highlight_color": caption_color,
            "font_family": "Montserrat-Black",
            "font_size": 52,
            "position": "bottom_third_safe_zone",
            "platform_safe_margins": {
                "top": 150,
                "bottom": 280 if platform == "tiktok" else 220,
                "left": 40,
                "right": 100,
            },
        }

        # Branding & Logos: campaign specific
        logos = []
        if campaign_assets:
            for a in campaign_assets:
                if a.get("file_type") == "logo":
                    logos.append({
                        "asset_id": a.get("asset_id"),
                        "local_path": a.get("local_path"),
                        "placement": "top_right_corner",
                        "opacity": 0.9,
                        "scale": 0.18,
                    })

        branding_and_logos = {
            "required_campaign_logo": bool(logos),
            "logo_overlays": logos,
            "watermark": {
                "text": campaign.get("required_mentions", ["@cr_gaming"])[0] if campaign.get("required_mentions") else "",
                "placement": "bottom_right_safe",
                "opacity": 0.5,
            },
        }

        # Sound effects and authorized audio
        sound_effects = [
            {"name": "whoosh_in", "timestamp_seconds": 0.0, "volume": 0.6},
            {"name": "impact_hit", "timestamp_seconds": round(target_duration * 0.6, 1), "volume": 0.75},
        ]

        # Platform specific formatting
        res_and_fps = {
            "width": 1080,
            "height": 1920,
            "aspect_ratio": "9:16",
            "frame_rate": 60 if platform == "youtube_shorts" else 30,
            "video_codec": "libx264",
            "audio_codec": "aac",
            "audio_bitrate": "192k",
        }

        # Exact campaign publication metadata (verbatim compliance)
        raw_hashtags = campaign.get("required_hashtags", ["#GTA6", "#Gaming"])
        hashtags = [h if h.startswith("#") else f"#{h}" for h in raw_hashtags]
        mentions = campaign.get("required_mentions", [])
        exact_captions = campaign.get("required_captions", [])
        base_caption = exact_captions[0] if exact_captions else f"Insane moment in {campaign_name}!"
        publication_caption = f"{base_caption} {' '.join(hashtags)} {' '.join(mentions)}".strip()

        # CTA
        cta = "Follow for daily high-tier gameplay & drop your reaction below!"
        if platform == "youtube_shorts":
            cta = "Subscribe for more GTA 6 stunts & moments!"

        candidate_id = str(candidate.get("id") or candidate.get("candidate_id") or "cand_1")
        key_str = f"{campaign_id}_{candidate_id}_{platform}"
        plan_id = f"plan_{hashlib.sha256(key_str.encode('utf-8')).hexdigest()[:12]}"

        return {
            "plan_id": plan_id,
            "campaign_id": campaign_id,
            "campaign_name": campaign_name,
            "platform": platform,
            "source_footage": source_footage_path,
            "source_authorization": {
                "is_authorized": source_authorized,
                "attribution_text": creator_attribution,
                "license": "Creator Partner Permitted Use",
            },
            "selected_timestamps": {
                "start_time": float(candidate.get("start_time", 0.0)),
                "end_time": float(candidate.get("end_time", target_duration)),
                "duration": target_duration,
            },
            "opening_hook": opening_hook,
            "scene_sequence": [
                {"scene": 1, "start_time": 0.0, "end_time": 2.5, "focus": "Hook / Intrigue"},
                {"scene": 2, "start_time": 2.5, "end_time": round(target_duration * 0.6, 1), "focus": "Build-up / Pacing"},
                {"scene": 3, "start_time": round(target_duration * 0.6, 1), "end_time": target_duration, "focus": "Climax / Payoff"},
            ],
            "cuts_and_transitions": [
                {"timestamp": 2.5, "transition": "hard_cut", "audio_duck": False},
                {"timestamp": round(target_duration * 0.6, 1), "transition": "zoom_punch", "audio_duck": True},
            ],
            "zooms_and_reframing": zooms_and_reframing,
            "caption_treatment": caption_treatment,
            "branding_and_logos": branding_and_logos,
            "sound_effects": sound_effects,
            "authorized_music": {
                "use_trending_in_app": True,
                "notes": "Add trending authorized gaming track directly in TikTok / Shorts UI to maximize reach",
            },
            "outro": {
                "duration_seconds": 1.5,
                "style": "clean_snap",
                "cta_text": cta,
            },
            "target_duration": target_duration,
            "resolution_and_framerate": res_and_fps,
            "exact_publication_caption": publication_caption,
            "required_hashtags": hashtags,
            "required_mentions": mentions,
            "cta": cta,
            "attribution": creator_attribution,
        }


editing_planner = EditingPlanner()
