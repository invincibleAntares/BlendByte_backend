"""
Agent 1 - Conversational Intake
"""
import asyncio
import json
import os
from typing import Dict, Any, List

from fastapi import APIRouter, Depends, HTTPException
from openai import AsyncOpenAI, APITimeoutError, APIConnectionError

from app.auth import verify_clerk_token
from app.database import get_or_create_user, log_error
from app.models import Agent1ChatRequest, Agent1ChatResponse, IntakeMessage

router = APIRouter()

openai_api_key = os.getenv("OPENAI_API_KEY")
openai_client = AsyncOpenAI(api_key=openai_api_key) if openai_api_key else None
model_name = "gpt-4o-mini"
MAX_CONTEXT_MESSAGES = 20
MAX_CONTEXT_CHARS = 6000

INITIAL_QUESTION = (
    "Who are you buying a gift for, and what is the occasion?"
)

SYSTEM_PROMPT = """
You are Agent 1 (Conversational Intake) for a gift recommendation app.
Your job is to ask focused follow-up questions and collect enough signal about:
- recipient relationship/context
- occasion
- personality/interests
- budget in INR

You must always return strict JSON with this shape:
{
  "is_complete": boolean,
  "next_question": string | null,
  "recipient_profile": object | null
}

Rules:
1) If you do not yet have enough signal, return:
   - is_complete: false
   - next_question: one clear question
   - recipient_profile: null
2) If enough signal is collected, return:
   - is_complete: true
   - next_question: null
   - recipient_profile: structured object with practical fields
3) Keep questions short and specific.
4) Do not add markdown, code fences, or extra keys.
5) Prefer explicit numeric budget in INR when available.
""".strip()


def _messages_to_text(messages: List[IntakeMessage]) -> str:
    lines: List[str] = []
    for message in messages:
        role = "User" if message.role == "user" else "Assistant"
        lines.append(f"{role}: {message.content}")
    return "\n".join(lines)


def _validate_message_sequence(messages: List[IntakeMessage]) -> None:
    if not messages:
        return

    # Conversation must always start with assistant first question.
    if messages[0].role != "assistant":
        raise ValueError("Conversation must start with assistant message")

    for index in range(1, len(messages)):
        if messages[index].role == messages[index - 1].role:
            raise ValueError("Conversation roles must alternate between assistant and user")

    # Client can only submit after user response.
    if messages[-1].role != "user":
        raise ValueError("Last message must be from user")


def _build_context_window(messages: List[IntakeMessage]) -> str:
    selected = messages[-MAX_CONTEXT_MESSAGES:]
    transcript = _messages_to_text(selected)
    if len(transcript) <= MAX_CONTEXT_CHARS:
        return transcript
    return transcript[-MAX_CONTEXT_CHARS:]


def _safe_parse_agent_output(raw_text: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Model returned invalid JSON") from exc

    is_complete = bool(parsed.get("is_complete", False))
    next_question = parsed.get("next_question")
    recipient_profile = parsed.get("recipient_profile")

    if is_complete and not isinstance(recipient_profile, dict):
        raise ValueError("Missing or invalid recipient_profile for completed state")

    if not is_complete:
        if not isinstance(next_question, str) or not next_question.strip():
            raise ValueError("Missing next_question for incomplete state")
        return {
            "is_complete": False,
            "next_question": next_question.strip(),
            "recipient_profile": None,
        }

    return {
        "is_complete": True,
        "next_question": None,
        "recipient_profile": recipient_profile,
    }


@router.post("/chat", response_model=Agent1ChatResponse)
async def agent1_chat(
    payload: Agent1ChatRequest,
    user_info: dict = Depends(verify_clerk_token),
):
    """
    Multi-turn conversational intake endpoint.
    """
    if not openai_client:
        raise HTTPException(
            status_code=500,
            detail="OPENAI_API_KEY is not configured",
        )

    db_user_id = None
    try:
        db_user = await get_or_create_user(
            clerk_user_id=user_info["clerk_user_id"],
            email=user_info.get("email", ""),
            name=user_info.get("name", ""),
        )
        db_user_id = db_user.get("id")

        if len(payload.messages) == 0:
            initial = IntakeMessage(role="assistant", content=INITIAL_QUESTION)
            return Agent1ChatResponse(
                is_complete=False,
                next_question=INITIAL_QUESTION,
                recipient_profile=None,
                messages=[initial],
            )

        _validate_message_sequence(payload.messages)
        transcript = _build_context_window(payload.messages)

        completion = None
        last_openai_error = None
        for attempt in range(2):
            try:
                completion = await openai_client.chat.completions.create(
                    model=model_name,
                    temperature=0.2,
                    response_format={"type": "json_object"},
                    timeout=30.0,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": (
                                "Conversation so far:\n"
                                f"{transcript}\n\n"
                                "Return the JSON response now."
                            ),
                        },
                    ],
                )
                break
            except (APITimeoutError, APIConnectionError) as exc:
                last_openai_error = exc
                if attempt == 0:
                    await asyncio.sleep(0.6)
                    continue
                raise

        if completion is None:
            raise ValueError(f"OpenAI call failed: {last_openai_error}")

        content = completion.choices[0].message.content or ""
        parsed = _safe_parse_agent_output(content)

        updated_messages = list(payload.messages)
        if not parsed["is_complete"] and parsed["next_question"]:
            updated_messages.append(
                IntakeMessage(role="assistant", content=parsed["next_question"])
            )

        return Agent1ChatResponse(
            is_complete=parsed["is_complete"],
            next_question=parsed["next_question"],
            recipient_profile=parsed["recipient_profile"],
            messages=updated_messages,
        )
    except HTTPException:
        raise
    except ValueError as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent1/chat",
            user_id=db_user_id,
        )
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        )
    except Exception as exc:
        await log_error(
            error_message=str(exc),
            endpoint="/api/v1/agent1/chat",
            user_id=db_user_id,
        )
        raise HTTPException(
            status_code=500,
            detail="Agent 1 failed to process the conversation",
        )
