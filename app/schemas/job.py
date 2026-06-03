from datetime import datetime

from pydantic import BaseModel


class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    location: str | None
    salary_range: str | None
    description: str | None
    requirements: str | None
    contact_info: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class JobApplicationResponse(BaseModel):
    id: int
    job_id: int
    user_id: int
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}
