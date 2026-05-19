from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query

from app.middleware.auth import get_current_user
from app.models.user import User
from app.schemas.common import APIResponse, PaginatedData, success
from app.schemas.quiz import (
    QuizCategoryTreeResponse,
    QuizCheckinRequest,
    QuizCheckinResponse,
    QuizCollectionRequest,
    QuizCollectionResponse,
    QuizQuestionResponse,
    QuizQuestionType,
    QuizRecordQuestionResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    QuizWrongBookRequest,
    QuizWrongBookResponse,
)
from app.services.quiz import QuizService

router = APIRouter(prefix="/quiz", tags=["\u9898\u5e93"])


@router.get("/categories", response_model=APIResponse[list[QuizCategoryTreeResponse]])
async def list_categories() -> APIResponse[list[QuizCategoryTreeResponse]]:
    """List quiz categories as a tree."""
    result = await QuizService().list_categories()
    return success(data=result)


@router.get("/questions", response_model=APIResponse[PaginatedData[QuizQuestionResponse]])
async def list_questions(
    category_id: int | None = Query(None, ge=1, description="Category ID"),
    question_type: QuizQuestionType | None = Query(
        None,
        description="Question type: single_choice / multiple_choice / judge",
    ),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
) -> APIResponse[PaginatedData[QuizQuestionResponse]]:
    """List quiz questions."""
    result = await QuizService().list_questions(
        category_id=category_id, question_type=question_type,
        page=page, page_size=page_size,
    )
    return success(data=result)


@router.post("/submit", response_model=APIResponse[QuizSubmitResponse])
async def submit_answer(
    body: QuizSubmitRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizSubmitResponse]:
    """Submit an answer."""
    result = await QuizService().submit_answer(current_user.id, body)
    return success(data=result)


@router.get("/wrong-book", response_model=APIResponse[PaginatedData[QuizRecordQuestionResponse]])
async def list_wrong_book(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizRecordQuestionResponse]]:
    """Quiz record list."""
    result = await QuizService().list_wrong_book(current_user.id, page=page, page_size=page_size)
    return success(data=result)


@router.post("/wrong-book", response_model=APIResponse[QuizWrongBookResponse])
async def add_wrong_question(
    body: QuizWrongBookRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizWrongBookResponse]:
    """Add a question to the wrong-book."""
    result = await QuizService().add_wrong_question(current_user.id, body)
    return success(data=result)


@router.delete("/wrong-book/{id}", response_model=APIResponse[QuizWrongBookResponse])
async def remove_wrong_question(
    id: int = Path(..., ge=1, description="Quiz record ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizWrongBookResponse]:
    """Remove a question from the wrong-book."""
    result = await QuizService().remove_wrong_question(current_user.id, record_id=id)
    return success(data=result)


@router.get("/collections", response_model=APIResponse[PaginatedData[QuizRecordQuestionResponse]])
async def list_collections(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Page size"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[PaginatedData[QuizRecordQuestionResponse]]:
    """List collected questions."""
    result = await QuizService().list_collections(current_user.id, page=page, page_size=page_size)
    return success(data=result)


@router.post("/collections", response_model=APIResponse[QuizCollectionResponse])
async def add_collection(
    body: QuizCollectionRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionResponse]:
    """Add a question to collections."""
    result = await QuizService().add_collection(current_user.id, body)
    return success(data=result)


@router.delete("/collections/{id}", response_model=APIResponse[QuizCollectionResponse])
async def remove_collection(
    id: int = Path(..., ge=1, description="Quiz record ID"),
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCollectionResponse]:
    """Remove a question from collections."""
    result = await QuizService().remove_collection(current_user.id, record_id=id)
    return success(data=result)


@router.get("/checkin", response_model=APIResponse[QuizCheckinResponse])
async def get_checkin_status(
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCheckinResponse]:
    """Get check-in status."""
    result = await QuizService().get_checkin_status(current_user.id)
    return success(data=result)


@router.post("/checkin", response_model=APIResponse[QuizCheckinResponse])
async def checkin(
    body: QuizCheckinRequest,
    current_user: User = Depends(get_current_user),
) -> APIResponse[QuizCheckinResponse]:
    """Create today's check-in."""
    result = await QuizService().checkin(current_user.id, body)
    return success(data=result)
