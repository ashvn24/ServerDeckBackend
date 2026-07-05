import datetime
import uuid
from pydantic import BaseModel
from uuid import UUID
from typing import List, Optional

class TicketSenderResponse(BaseModel):
    id: UUID
    name: str
    email: str
    role: str

    model_config = {"from_attributes": True}

class TicketMessageResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    sender_id: Optional[UUID]
    sender: Optional[TicketSenderResponse]
    body: str
    is_internal: bool
    created_at: datetime.datetime

    model_config = {"from_attributes": True}

class TicketMessageCreate(BaseModel):
    body: str
    is_internal: bool = False

class TicketResponse(BaseModel):
    id: UUID
    team_id: UUID
    title: str
    description: str
    status: str
    priority: str
    created_by_id: UUID
    created_by: TicketSenderResponse
    assigned_to_id: Optional[UUID]
    assigned_to: Optional[TicketSenderResponse]
    alert_id: Optional[UUID] = None
    created_at: datetime.datetime
    updated_at: datetime.datetime

    model_config = {"from_attributes": True}

class TicketDetailResponse(TicketResponse):
    messages: List[TicketMessageResponse] = []

    model_config = {"from_attributes": True}

class TicketCreate(BaseModel):
    title: str
    description: str
    priority: str = "Medium"
    alert_id: Optional[UUID] = None

class TicketUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    assigned_to_id: Optional[UUID] = None
