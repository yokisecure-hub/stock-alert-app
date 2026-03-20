from pydantic import BaseModel
from datetime import datetime


class MagicWordCreate(BaseModel):
    keyword: str
    category: str = ""


class MagicWordUpdate(BaseModel):
    keyword: str | None = None
    category: str | None = None
    is_active: bool | None = None


class MagicWordResponse(BaseModel):
    id: int
    keyword: str
    category: str
    is_active: bool
    created_at: str


class AlertResponse(BaseModel):
    id: int
    keyword_id: int
    keyword: str
    category: str
    title: str
    source: str
    url: str
    matched_at: str
    is_read: bool


class RssFeedResponse(BaseModel):
    id: int
    name: str
    url: str
    is_active: bool
    last_checked: str | None
