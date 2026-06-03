from __future__ import annotations

from sqlalchemy import func, select

from app.core.database import get_db_ctx
from app.core.exceptions import NotFoundException
from app.models.ticket import Ticket
from app.schemas.common import PaginatedData
from app.schemas.ticket import TicketCreateRequest, TicketDetail, TicketListItem


class TicketService:
    async def create_ticket(self, user_id: int, data: TicketCreateRequest) -> TicketListItem:
        ticket = Ticket(
            user_id=user_id,
            content=data.content,
            status="waiting_manual",
        )
        async with get_db_ctx() as db:
            db.add(ticket)
            await db.commit()
            await db.refresh(ticket)
        return TicketListItem.model_validate(ticket)

    async def list_tickets(
        self, user_id: int, *, page: int = 1, page_size: int = 20
    ) -> PaginatedData[TicketListItem]:
        async with get_db_ctx() as db:
            stmt = select(Ticket).where(Ticket.user_id == user_id)
            total = (
                await db.execute(select(func.count()).select_from(stmt.subquery()))
            ).scalar() or 0
            tickets = (
                await db.execute(
                    stmt.order_by(Ticket.created_at.desc())
                    .offset((page - 1) * page_size)
                    .limit(page_size)
                )
            ).scalars().all()

        items = [TicketListItem.model_validate(t) for t in tickets]
        return PaginatedData[TicketListItem](
            items=items,
            total=total,
            page=page,
            page_size=page_size,
        )

    async def get_ticket(self, user_id: int, ticket_id: int) -> TicketDetail:
        async with get_db_ctx() as db:
            ticket = (
                await db.execute(select(Ticket).where(Ticket.id == ticket_id))
            ).scalar_one_or_none()
            if ticket is None:
                raise NotFoundException("工单")
            if ticket.user_id != user_id:
                raise NotFoundException("工单")
        return TicketDetail.model_validate(ticket)
