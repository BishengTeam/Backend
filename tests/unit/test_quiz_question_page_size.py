import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.admin.quiz import admin_question_query
from app.schemas.admin_quiz_contract import AdminQuizQuestionQuery, AdminQuizBatchRequest

app = FastAPI()


@app.get("/questions")
async def questions(query: AdminQuizQuestionQuery = Depends(admin_question_query)):
    return query.model_dump()


@pytest.mark.parametrize("size", [10, 20, 50, 100, 200, 500, 1000, 1500, 2000])
def test_question_schema_and_http_query_accept_requested_page_sizes(size):
    assert AdminQuizQuestionQuery(page_size=size).page_size == size
    with TestClient(app) as client:
        response = client.get("/questions", params={"library_id": 1, "page": 2, "page_size": size})
    assert response.status_code == 200
    assert response.json()["page_size"] == size and response.json()["page"] == 2


@pytest.mark.parametrize("size", [0, 2001, -1])
def test_question_pagination_still_has_bounds(size):
    with pytest.raises(ValidationError):
        AdminQuizQuestionQuery(page_size=size)
    with TestClient(app) as client:
        assert client.get("/questions", params={"page_size": size}).status_code == 422


def test_large_pages_do_not_change_atomic_batch_limit():
    with pytest.raises(ValidationError):
        AdminQuizBatchRequest(items=[{"question_id": ident, "lock_version": 1} for ident in range(1, 102)])
