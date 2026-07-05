import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User
from app.models.ticket import Ticket, TicketMessage
from app.schemas.ticket import (
    TicketResponse, TicketDetailResponse, TicketCreate, TicketUpdate,
    TicketMessageResponse, TicketMessageCreate
)
from app.ws.client_handler import forward_to_ticket_watchers

router = APIRouter(prefix="/api/tickets", tags=["tickets"])

@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    data: TicketCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ticket = Ticket(
        team_id=user.team_id,
        title=data.title,
        description=data.description,
        priority=data.priority,
        created_by_id=user.id,
        alert_id=data.alert_id,
    )
    db.add(ticket)
    await db.flush()
    
    # Load relationships for the response
    result = await db.execute(
        select(Ticket)
        .where(Ticket.id == ticket.id)
        .options(selectinload(Ticket.created_by), selectinload(Ticket.assigned_to))
    )
    db_ticket = result.scalar_one()
    return db_ticket


@router.get("", response_model=List[TicketResponse])
async def list_tickets(
    status_filter: Optional[str] = None,
    priority_filter: Optional[str] = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Ticket).where(Ticket.team_id == user.team_id)
    
    # Members can only view their own tickets
    if user.role == "member":
        query = query.where(Ticket.created_by_id == user.id)
        
    if status_filter:
        query = query.where(Ticket.status == status_filter)
    if priority_filter:
        query = query.where(Ticket.priority == priority_filter)
        
    query = query.options(
        selectinload(Ticket.created_by),
        selectinload(Ticket.assigned_to)
    ).order_by(Ticket.updated_at.desc())
    
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket(
    ticket_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id, Ticket.team_id == user.team_id)
        .options(
            selectinload(Ticket.created_by),
            selectinload(Ticket.assigned_to),
            selectinload(Ticket.messages).selectinload(TicketMessage.sender)
        )
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        
    if user.role == "member" and ticket.created_by_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        
    # Sort messages by created_at ascending
    ticket.messages = sorted(ticket.messages, key=lambda m: m.created_at)
    return ticket


@router.patch("/{ticket_id}", response_model=TicketResponse)
async def update_ticket(
    ticket_id: uuid.UUID,
    data: TicketUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ticket)
        .where(Ticket.id == ticket_id, Ticket.team_id == user.team_id)
        .options(selectinload(Ticket.created_by), selectinload(Ticket.assigned_to))
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    # Members can only update their own tickets and only change status
    is_member = user.role == "member"
    is_staff  = user.role in ("owner", "admin", "support")

    if is_member and ticket.created_by_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    sent = data.model_fields_set  # fields that were explicitly sent in the request

    # Status — any role can update
    if "status" in sent and data.status is not None:
        ticket.status = data.status

    # Priority — staff only
    if "priority" in sent and data.priority is not None:
        if is_member:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Members cannot change priority")
        ticket.priority = data.priority

    # Assignment — staff only; supports explicit null (unassign)
    if "assigned_to_id" in sent:
        if is_member:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Members cannot assign tickets")
        if data.assigned_to_id is None:
            # Explicit unassign
            ticket.assigned_to_id = None
            ticket.assigned_to     = None
        else:
            assignee_res = await db.execute(
                select(User).where(User.id == data.assigned_to_id, User.team_id == user.team_id)
            )
            assignee = assignee_res.scalar_one_or_none()
            if not assignee:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid assignee")
            ticket.assigned_to_id = data.assigned_to_id
            ticket.assigned_to    = assignee

    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()

    # Broadcast metadata update to watchers
    ticket_response = TicketResponse.model_validate(ticket)
    await forward_to_ticket_watchers(
        str(ticket.id),
        {"type": "ticket_update", "ticket": ticket_response.model_dump(mode="json")}
    )

    return ticket



@router.post("/{ticket_id}/messages", response_model=TicketMessageResponse, status_code=status.HTTP_201_CREATED)
async def create_ticket_message(
    ticket_id: uuid.UUID,
    data: TicketMessageCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Ticket).where(Ticket.id == ticket_id, Ticket.team_id == user.team_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
        
    if user.role == "member" and ticket.created_by_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        
    is_internal = data.is_internal
    if user.role == "member":
        is_internal = False  # members cannot write internal notes
        
    message = TicketMessage(
        ticket_id=ticket_id,
        sender_id=user.id,
        body=data.body,
        is_internal=is_internal,
    )
    db.add(message)
    
    # Update ticket updated_at
    ticket.updated_at = datetime.now(timezone.utc)
    await db.flush()
    
    # Load sender for response
    result_msg = await db.execute(
        select(TicketMessage)
        .where(TicketMessage.id == message.id)
        .options(selectinload(TicketMessage.sender))
    )
    db_message = result_msg.scalar_one()
    
    # Broadcast to watchers
    msg_response = TicketMessageResponse.model_validate(db_message)
    await forward_to_ticket_watchers(
        str(ticket_id),
        {"type": "ticket_message", "message": msg_response.model_dump(mode="json")}
    )
    
    return db_message
