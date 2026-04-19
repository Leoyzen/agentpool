"""Question routes for OpenCode compatibility."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.models import (
    PermissionResolvedEvent,
    QuestionRejectedEvent,
    QuestionRepliedEvent,
    QuestionReply,
    QuestionRequest,
)


router = APIRouter(prefix="/question", tags=["question"])


def _find_permission_provider(
    state: StateDep,
    permission_id: str,
) -> tuple[str, OpenCodeInputProvider] | None:
    for session_id, input_provider in state.input_providers.items():
        if permission_id in input_provider._pending_permissions:
            return session_id, input_provider
    return None


def _extract_permission_reply(reply: QuestionReply) -> str | None:
    if len(reply.answers) != 1:
        return None
    selected_answers = reply.answers[0]
    if len(selected_answers) != 1:
        return None

    selected_reply = selected_answers[0]
    match selected_reply:
        case "once" | "always" | "reject":
            return selected_reply
        case _:
            return None


@router.get("/", response_model=list[QuestionRequest])
async def list_questions(state: StateDep) -> list[QuestionRequest]:
    """List all pending question requests.

    Returns a list of all pending questions awaiting user response.
    """
    return [
        QuestionRequest(id=question_id, session_id=i.session_id, questions=i.questions, tool=i.tool)
        for question_id, i in state.pending_questions.items()
    ]


@router.post("/{requestID}/reply")
async def reply_to_question(requestID: str, reply: QuestionReply, state: StateDep) -> bool:  # noqa: N803
    """Reply to a question request.

    The user provides answers to the questions. Answers must be provided
    as an array of arrays, where each inner array contains the selected
    label(s) for that question.

    Args:
        requestID: The question request ID
        reply: The user's answers
        state: Server state

    Returns:
        True if the question was resolved successfully

    Raises:
        HTTPException: If question not found or invalid provider
    """
    pending = state.pending_questions.get(requestID)
    if not pending:
        permission_target = _find_permission_provider(state, requestID)
        if permission_target is None:
            raise HTTPException(status_code=404, detail="Question request not found")

        session_id, provider = permission_target
        permission_reply = _extract_permission_reply(reply)
        if permission_reply is None:
            raise HTTPException(status_code=400, detail="Invalid permission reply")

        if not provider.resolve_permission(requestID, permission_reply):
            raise HTTPException(status_code=404, detail="Permission not found or already resolved")

        event = PermissionResolvedEvent.create(
            session_id=session_id,
            request_id=requestID,
            reply=permission_reply,
        )
        await state.broadcast_event(event)
        return True

    session_id = pending.session_id
    provider = state.input_providers.get(session_id)
    if not isinstance(provider, OpenCodeInputProvider):
        raise HTTPException(status_code=500, detail="Invalid provider for session")
    # Resolve via provider
    if not provider.resolve_question(requestID, reply.answers):
        raise HTTPException(status_code=404, detail="Question already resolved")
    # Broadcast replied event
    event = QuestionRepliedEvent.create(
        session_id=session_id,
        request_id=requestID,
        answers=reply.answers,
    )
    await state.broadcast_event(event)
    return True


@router.post("/{requestID}/reject")
async def reject_question(requestID: str, state: StateDep) -> bool:  # noqa: N803
    """Reject a question request.

    Called when the user dismisses the question without providing an answer.

    Args:
        requestID: The question request ID
        state: Server state

    Returns:
        True if the question was rejected successfully

    Raises:
        HTTPException: If question not found
    """
    pending = state.pending_questions.get(requestID)
    if not pending:
        permission_target = _find_permission_provider(state, requestID)
        if permission_target is None:
            raise HTTPException(status_code=404, detail="Question request not found")

        session_id, provider = permission_target
        if not provider.resolve_permission(requestID, "reject"):
            raise HTTPException(status_code=404, detail="Permission not found or already resolved")

        event = PermissionResolvedEvent.create(
            session_id=session_id,
            request_id=requestID,
            reply="reject",
        )
        await state.broadcast_event(event)
        return True
    # Cancel the future
    if not pending.future.done():
        pending.future.cancel()
    # Remove from pending
    del state.pending_questions[requestID]
    # Broadcast rejected event
    event = QuestionRejectedEvent.create(session_id=pending.session_id, request_id=requestID)
    await state.broadcast_event(event)
    return True
