from pydantic import BaseModel, Field
from typing import Literal


class SpinRequest(BaseModel):
    wheel_type: Literal["free", "paid", "quest"]
    request_id: str = Field(min_length=8, max_length=64)


class ClaimRequest(BaseModel):
    claim_id: int = Field(gt=0)


class TaskClaimRequest(BaseModel):
    task_key: str = Field(min_length=1, max_length=64)
