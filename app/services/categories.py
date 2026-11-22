from .supabase_client import db
"""Multi-category architecture and dynamic category registry."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class CategoryDefinition(BaseModel):
    id: str
    name: str
    description: str
    icon: str = "sparkles"
    is_active: bool = True
    system_prompt: str
    scoring_weights: Dict[str, float] = Field(default_factory=dict)
    default_hashtags: List[str] = Field(default_factory=list)
    hook_templates: List[str] = Field(default_factory=list)
    presets: Dict[str, Any] = Field(default_factory=dict)
    preferred_duration: Dict[str, float] = Field(default_factory=lambda: {"min": 20.0, "max": 55.0})
    publishing_preferences: Dict[str, Any] = Field(default_factory=lambda: {"platforms": ["tiktok", "youtube_shorts"]})


INITIAL_CATEGORIES: Dict[str, CategoryDefinition] = {
    "gaming": CategoryDefinition(
        id="gaming",
        name="Gaming & Gaming Discoveries",
        description="High-octane gameplay clutches, Easter eggs, game physics, competitive tournaments, and gaming discoveries.",
        icon="gamepad",
        system_prompt="""You are a viral gaming content strategist. Your objective is to extract the most thrilling gameplay clips, clutch victories, unexpected bugs, shocking Easter eggs, and gaming revelations.
Analyze the video for:
- Sudden adrenaline spikes, clutch multi-kills, or narrow escapes.
- Unexpected plot revelations, secrets, and world discoveries.
- Hilarious physics glitches, AI fails, or creator voice reactions.
- High-CTR hook sentence in the first 3 seconds.""",
        scoring_weights={
            "immediate_attention": 0.25,
            "entertainment_value": 0.25,
            "surprise_curiosity": 0.20,
            "emotional_impact": 0.15,
            "shareability": 0.15,
        },
        default_hashtags=["#Gaming", "#Shorts", "#GamingClips", "#Gamer", "#ViralGaming", "#GamingCommunity"],
        hook_templates=[
            "Nobody expected this to happen in {subject}...",
            "Watch what happens when you do this in {subject}!",
            "This {subject} trick broke the entire game...",
            "I spent 100 hours testing this so you don't have to!",
        ],
        presets={
            "gta6": {
                "name": "GTA 6 & Vice City Highlights",
                "description": "Specialized preset for GTA 6 trailer breakdowns, Vice City secrets, leak analysis, and open-world gameplay.",
                "hashtags": ["#GTA6", "#GTA6Trailer", "#ViceCity", "#RockstarGames", "#GTA6Leaks", "#GrandTheftAuto", "#Shorts"],
                "custom_hook": "Look closely at this GTA 6 detail Rockstar didn't mention...",
            },
            "competitive": {
                "name": "Competitive Clutches & Esports",
                "hashtags": ["#Esports", "#ProGamer", "#Clutch", "#Ranked", "#Shorts"],
            }
        },
    ),
    "viral_entertainment": CategoryDefinition(
        id="viral_entertainment",
        name="Viral Entertainment & Unexpected Moments",
        description="Shocking twists, comedy gold, wholesome encounters, street interviews, and unforgettable pop culture moments.",
        icon="film",
        system_prompt="""You are a viral entertainment editor. Your job is to extract unpredictable, emotionally resonant, and laugh-out-loud moments that demand an immediate re-watch or share.
Focus on:
- Jaw-dropping plot twists, unexpected answers, or sudden reversals.
- Pure comedic timing and infectious laughter.
- Relatable life dilemmas and passionate debates.
- Compelling storytelling with an immediate opening question or hook.""",
        scoring_weights={
            "immediate_attention": 0.30,
            "entertainment_value": 0.25,
            "surprise_curiosity": 0.25,
            "emotional_impact": 0.10,
            "shareability": 0.10,
        },
        default_hashtags=["#Viral", "#Entertainment", "#Funny", "#PlotTwist", "#Shorts", "#TikTokMadeMeWatchIt", "#Trending"],
        hook_templates=[
            "The moment everything changed...",
            "Wait until you see how this ends!",
            "I still can't believe they actually said this...",
            "This is the wildest thing you'll see all day.",
        ],
    ),
    "animals_nature": CategoryDefinition(
        id="animals_nature",
        name="Animals & Nature",
        description="Fascinating wildlife encounters, heartwarming pet interactions, rare natural spectacles, and animal intelligence.",
        icon="paw",
        system_prompt="""You are a viral nature and wildlife documentary producer. Identify breathtaking moments of animal intelligence, predator-prey suspense, heartwarming bond moments, or bizarre creature behavior.
Focus on:
- High suspense or miraculous survival moments in nature.
- Wholesome, humorous animal reactions and intelligence displays.
- Visual wonders and jaw-dropping natural events.
- Clear, educational or emotional payoff.""",
        scoring_weights={
            "immediate_attention": 0.20,
            "entertainment_value": 0.25,
            "surprise_curiosity": 0.25,
            "emotional_impact": 0.20,
            "shareability": 0.10,
        },
        default_hashtags=["#Animals", "#Wildlife", "#Nature", "#PetLovers", "#AnimalKingdom", "#CuteAnimals", "#Shorts"],
        hook_templates=[
            "Scientists still cannot explain this animal's behavior...",
            "You won't believe what this animal did next!",
            "This creature has the most dangerous superpower in nature...",
        ],
    ),
    "tech_ai": CategoryDefinition(
        id="tech_ai",
        name="Technology, AI & Futuristic Discoveries",
        description="Cutting-edge AI agents, robotics demonstrations, futuristic gadgets, and paradigm-shifting tech breakthroughs.",
        icon="cpu",
        system_prompt="""You are a futuristic technology communicator. Your mission is to extract mind-blowing technological breakthroughs, live AI demos, humanoid robot tests, and futuristic inventions.
Focus on:
- Visual proof of groundbreaking new capabilities (robots moving, AI generating reality).
- Explanations of how this tech will transform daily life in the next 12 months.
- Fascinating ethical dilemmas or unprecedented speed comparisons.
- Clear, punchy explanations free of boring corporate jargon.""",
        scoring_weights={
            "immediate_attention": 0.25,
            "surprise_curiosity": 0.30,
            "entertainment_value": 0.20,
            "shareability": 0.15,
            "story_clarity": 0.10,
        },
        default_hashtags=["#Tech", "#ArtificialIntelligence", "#AI", "#Robotics", "#FutureTech", "#Innovation", "#Shorts"],
        hook_templates=[
            "AI just crossed a line nobody thought was possible...",
            "This new robot can officially do your job!",
            "The future just arrived and it's terrifyingly fast...",
            "Stop scrolling: this AI tool just changed everything.",
        ],
    ),
    "sports": CategoryDefinition(
        id="sports",
        name="Football & Sports Entertainment",
        description="Spectacular goals, referee drama, unbelievable buzzer-beaters, locker room stories, and athletic masterclasses.",
        icon="trophy",
        system_prompt="""You are a viral sports producer. Extract historic athletic moments, outrageous referee controversy, 90th-minute clutch winners, and locker room passion.
Focus on:
- Unbelievable physical skills, nutmegs, insane passes, and screamer goals.
- Heated arguments, red card drama, and high-stakes tension.
- Inspiring underdog stories and celebratory raw emotion.
- Fast-paced cuts with maximum crowd sound and intensity.""",
        scoring_weights={
            "immediate_attention": 0.30,
            "entertainment_value": 0.25,
            "emotional_impact": 0.25,
            "surprise_curiosity": 0.10,
            "shareability": 0.10,
        },
        default_hashtags=["#Football", "#Soccer", "#Sports", "#BuzzerBeater", "#Highlights", "#GoalOfTheDay", "#Shorts"],
        hook_templates=[
            "Is this the greatest referee blunder in history?",
            "The commentator's voice says it all...",
            "How did he pull this off in the 94th minute?!",
        ],
    ),
}


class CategoryService:
    """Manages system and custom categories."""

    def __init__(self):
        self._categories: Dict[str, CategoryDefinition] = dict(INITIAL_CATEGORIES)
        self._sync_from_db()

    def list_categories(self, active_only: bool = True) -> List[CategoryDefinition]:
        cats = list(self._categories.values())
        if active_only:
            return [c for c in cats if c.is_active]
        return cats

    def get_category(self, category_id: str) -> Optional[CategoryDefinition]:
        if not category_id:
            return self._categories.get("gaming")
        return self._categories.get(category_id.lower().strip())

    def register_category(self, category: CategoryDefinition) -> CategoryDefinition:
        self._categories[category.id.lower().strip()] = category
        try:
            db.save_category(category.model_dump())
        except Exception:
            pass
        return category
    def _sync_from_db(self):
        try:
            stored = db.list_categories()
            for s in stored:
                cid = s.get("id")
                if cid:
                    self._categories[cid.lower()] = CategoryDefinition(
                        id=cid,
                        name=s.get("name", cid),
                        description=s.get("description", ""),
                        icon=s.get("icon", "sparkles"),
                        is_active=bool(s.get("is_active", True)),
                        system_prompt=s.get("system_prompt", ""),
                        scoring_weights=s.get("scoring_weights") if isinstance(s.get("scoring_weights"), dict) else {},
                        default_hashtags=s.get("default_hashtags") if isinstance(s.get("default_hashtags"), list) else [],
                        presets=s.get("presets") if isinstance(s.get("presets"), dict) else {},
                        publishing_preferences=s.get("publishing_preferences") if isinstance(s.get("publishing_preferences"), dict) else {"platforms": ["tiktok", "youtube_shorts"]},
                    )
        except Exception:
            pass

    def update_category(self, category_id: str, updates: Dict[str, Any]) -> Optional[CategoryDefinition]:
        current = self.get_category(category_id)
        if not current:
            return None
        current_data = current.model_dump()
        for k, v in updates.items():
            if v is not None:
                current_data[k] = v
        updated_def = CategoryDefinition(**current_data)
        self.register_category(updated_def)
        return updated_def


    def get_scoring_weights(self, category_id: str) -> Dict[str, float]:
        cat = self.get_category(category_id)
        if cat and cat.scoring_weights:
            return cat.scoring_weights
        return {
            "immediate_attention": 0.25,
            "entertainment_value": 0.25,
            "surprise_curiosity": 0.20,
            "emotional_impact": 0.15,
            "shareability": 0.15,
        }


category_service = CategoryService()
