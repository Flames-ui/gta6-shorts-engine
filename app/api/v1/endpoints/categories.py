"""Categories management endpoints."""
import logging
from typing import List
from fastapi import APIRouter, Depends, HTTPException, status

from ....auth import AuthContext, require_admin, verify_api_key
from ....schemas import CategoryCreateRequest, CategoryResponse, CategoryUpdateRequest
from ....services.categories import CategoryDefinition, category_service

logger = logging.getLogger("gta6_shorts.categories")
router = APIRouter(prefix="/categories", tags=["Categories"])


@router.get("", response_model=List[CategoryResponse])
async def list_categories(auth: AuthContext = Depends(verify_api_key)):
    """List all available content categories with their AI prompts, scoring weights, and presets."""
    cats = category_service.list_categories()
    return [
        CategoryResponse(
            id=c.id,
            name=c.name,
            description=c.description,
            icon=c.icon,
            is_active=c.is_active,
            scoring_weights=c.scoring_weights,
            default_hashtags=c.default_hashtags,
            presets=c.presets,
            publishing_preferences=c.publishing_preferences,
        )
        for c in cats
    ]


@router.get("/{category_id}", response_model=CategoryResponse)
async def get_category(category_id: str, auth: AuthContext = Depends(verify_api_key)):
    """Retrieve details and AI configuration for a specific content category."""
    c = category_service.get_category(category_id)
    if not c:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Category '{category_id}' not found.")
    return CategoryResponse(
        id=c.id,
        name=c.name,
        description=c.description,
        icon=c.icon,
        is_active=c.is_active,
        scoring_weights=c.scoring_weights,
        default_hashtags=c.default_hashtags,
        presets=c.presets,
        publishing_preferences=c.publishing_preferences,
    )


@router.post("", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
async def create_category(payload: CategoryCreateRequest, auth: AuthContext = Depends(verify_api_key)):
    """Register or customize a content category without modifying core application code."""
    if not auth.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only administrators can create categories.")

    definition = CategoryDefinition(
        id=payload.id,
        name=payload.name,
        description=payload.description,
        icon=payload.icon,
        system_prompt=payload.system_prompt,
        scoring_weights=payload.scoring_weights or {},
        default_hashtags=payload.default_hashtags or [],
        presets=payload.presets if isinstance(payload.presets, dict) else {},
    )
    saved = category_service.register_category(definition)
    return CategoryResponse(
        id=saved.id,
        name=saved.name,
        description=saved.description,
        icon=saved.icon,
        is_active=saved.is_active,
        scoring_weights=saved.scoring_weights,
        default_hashtags=saved.default_hashtags,
        presets=saved.presets,
        publishing_preferences=saved.publishing_preferences,
    )

from ....services.supabase_client import db


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: str,
    payload: CategoryUpdateRequest,
    auth: AuthContext = Depends(verify_api_key),
):
    """Enable, disable, or configure a content category (prompt, weights, hashtags, presets). Admin only."""
    require_admin(auth)

    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No configuration fields provided for update.")

    updated = category_service.update_category(category_id, updates)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Category '{category_id}' not found.")

    db.create_audit_log(
        action="category_updated",
        resource_type="category",
        user_id=auth.user_id,
        resource_id=category_id,
        details=updates,
    )

    return CategoryResponse(
        id=updated.id,
        name=updated.name,
        description=updated.description,
        icon=updated.icon,
        is_active=updated.is_active,
        scoring_weights=updated.scoring_weights,
        default_hashtags=updated.default_hashtags,
        presets=updated.presets,
        publishing_preferences=updated.publishing_preferences,
    )
