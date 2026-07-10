"""
Natural Language Milestone Parser for Fixed Fee Contracts

Uses AI to parse natural language descriptions into structured milestone data.
Examples:
  - "50% upfront, 50% on completion" 
  - "3 equal milestones"
  - "25% kickoff, 50% development, 25% delivery"
"""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
import json
import uuid
import os
from dotenv import load_dotenv

from auth_helper import get_current_user
try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage
    HAS_EMERGENT = True
except ImportError:
    HAS_EMERGENT = False

load_dotenv()

router = APIRouter()


class ParseMilestonesRequest(BaseModel):
    description: str
    total_amount: float


class MilestoneResponse(BaseModel):
    name: str
    percentage: float
    amount: float
    description: Optional[str] = ""


class ParseMilestonesResponse(BaseModel):
    milestones: List[MilestoneResponse]
    interpretation: str


async def parse_milestones_with_ai(description: str, total_amount: float) -> dict:
    """Use AI to parse natural language milestone description"""
    if not HAS_EMERGENT:
        raise HTTPException(
            status_code=503,
            detail="AI features require the emergentintegrations package, which is not available in this environment."
        )

    from services.ai.config import get_llm_key
    api_key = get_llm_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="LLM API key not configured")

    # Create LLM chat instance
    chat = LlmChat(
        api_key=api_key,
        session_id=f"milestone_parser_{uuid.uuid4().hex[:8]}",
        system_message="""You are a contract milestone parser. Parse natural language descriptions into structured milestone data.

Rules:
1. Generate milestone names using "Phase 1", "Phase 2", etc. format
2. Ensure percentages add up to exactly 100%
3. Calculate amounts based on percentages (amount = total_amount * percentage / 100)
4. If user says "equal" or doesn't specify splits, divide equally
5. Return valid JSON only, no markdown formatting

Output format (JSON only):
{
  "milestones": [
    {"name": "Phase 1", "percentage": 50.0, "amount": 50000.0, "description": "Initial phase"},
    {"name": "Phase 2", "percentage": 50.0, "amount": 50000.0, "description": "Final phase"}
  ],
  "interpretation": "Brief summary of what you understood"
}"""
    ).with_model("openai", "gpt-4o-mini")
    
    # Create prompt with user input
    prompt = f"""Parse this milestone description for a ${total_amount:,.2f} contract:

"{description}"

Return ONLY valid JSON with the milestone structure. No markdown, no explanations."""
    
    user_message = UserMessage(text=prompt)
    
    # Get AI response
    try:
        response = await chat.send_message(user_message)
        
        # Clean response - remove markdown code blocks if present
        cleaned = response.strip()
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        if cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()
        
        # Parse JSON
        parsed = json.loads(cleaned)
        
        # Validate structure
        if "milestones" not in parsed:
            raise ValueError("Missing 'milestones' key in response")
        
        milestones = parsed["milestones"]
        
        # Ensure all milestones have required fields and calculate amounts
        for i, ms in enumerate(milestones, 1):
            if "name" not in ms:
                ms["name"] = f"Phase {i}"
            if "percentage" not in ms:
                raise ValueError(f"Missing percentage for milestone {i}")
            if "amount" not in ms:
                ms["amount"] = total_amount * (float(ms["percentage"]) / 100.0)
            if "description" not in ms:
                ms["description"] = ""
        
        # Validate percentages sum to 100
        total_pct = sum(float(ms["percentage"]) for ms in milestones)
        if abs(total_pct - 100.0) > 0.01:
            raise ValueError(f"Percentages must sum to 100%, got {total_pct}%")
        
        return parsed
        
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to parse AI response as JSON: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error parsing milestones: {str(e)}"
        )


@router.post("/parse-milestones", response_model=ParseMilestonesResponse)
async def parse_milestones(
    data: ParseMilestonesRequest,
    current_user=Depends(get_current_user)
):
    """
    Parse natural language milestone description into structured milestone data.
    
    Examples:
    - "50% upfront, 50% on completion"
    - "3 equal milestones"
    - "25% kickoff, 50% development, 25% delivery"
    - "Four phases: 20% design, 30% build, 30% testing, 20% launch"
    """
    
    if not data.description or not data.description.strip():
        raise HTTPException(status_code=400, detail="Description is required")
    
    if data.total_amount <= 0:
        raise HTTPException(status_code=400, detail="Total amount must be greater than 0")
    
    # Parse using AI
    result = await parse_milestones_with_ai(data.description, data.total_amount)
    
    return ParseMilestonesResponse(
        milestones=[
            MilestoneResponse(
                name=ms["name"],
                percentage=float(ms["percentage"]),
                amount=float(ms["amount"]),
                description=ms.get("description", "")
            )
            for ms in result["milestones"]
        ],
        interpretation=result.get("interpretation", "Milestones parsed successfully")
    )


# Common milestone templates endpoint
@router.get("/milestone-templates")
async def get_milestone_templates(current_user=Depends(get_current_user)):
    """Get common milestone templates for quick selection"""
    return {
        "templates": [
            {
                "name": "50/50 Split",
                "description": "50% upfront, 50% on completion",
                "value": "50% upfront, 50% on completion"
            },
            {
                "name": "3 Equal Phases",
                "description": "Three equal payment milestones",
                "value": "3 equal milestones"
            },
            {
                "name": "25/50/25 Split",
                "description": "25% kickoff, 50% midpoint, 25% completion",
                "value": "25% kickoff, 50% midpoint, 25% completion"
            },
            {
                "name": "4 Equal Phases",
                "description": "Four equal payment milestones",
                "value": "4 equal milestones"
            },
            {
                "name": "Standard Project",
                "description": "20% kickoff, 30% design, 30% development, 20% launch",
                "value": "20% kickoff, 30% design complete, 30% development complete, 20% launch"
            },
            {
                "name": "Agile Sprints",
                "description": "5 equal sprint payments",
                "value": "5 equal sprint milestones"
            },
        ]
    }
