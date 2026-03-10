from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any

from starlette.requests import Request
from app.brain.personality import PersonalityProfile
from app.brain.regime import RegimeType

router = APIRouter()

@router.get("/{symbol}", response_model=PersonalityProfile)
async def get_personality_profile(symbol: str, request: Request):
    """
    ดึงข้อมูลนิสัยตลาด (Personality Profile) ของคู่เงิน.
    """
    master_loop = request.app.state.master_loop
    if not master_loop or not master_loop.personality_engine:
        raise HTTPException(status_code=503, detail="Personality Engine not ready")

    profile = master_loop.personality_engine.get_profile(symbol)
    if not profile:
        # Try to load from DB? Or just return empty
        memory_store = request.app.state.memory
        if memory_store:
            data = memory_store.get_personality_profile(symbol)
            if data:
                return PersonalityProfile(**data)
        
        raise HTTPException(status_code=404, detail=f"No personality profile found for {symbol}")
    
    return profile

@router.get("/regime/{symbol}", response_model=Dict[str, Any])
async def get_current_regime(symbol: str, request: Request):
    """
    ดึงข้อมูล Regime ปัจจุบัน (จาก last decision).
    """
    master_loop = request.app.state.master_loop
    if not master_loop:
        raise HTTPException(status_code=503, detail="Master Loop not ready")

    decision_info = master_loop.last_decisions.get(symbol)
    if not decision_info:
        raise HTTPException(status_code=404, detail=f"No recent decision for {symbol}")

    return {
        "symbol": symbol,
        "regime": decision_info.get("regime", "UNKNOWN"),
        "session": decision_info.get("session", "UNKNOWN"),
        "timestamp": decision_info.get("cycle", 0)  # Use cycle as proxy for time or add timestamp to last_decisions
    }
