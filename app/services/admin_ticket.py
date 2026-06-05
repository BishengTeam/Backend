from sqlalchemy import func, select

from app.adapter.database import get_db_ctx
from app.port.exceptions import NotFoundException
from app.domain.content.src.index import Ticket
from app.schemas.admin_ticket import AdminTicketFilter, AdminTicketListItem, AdminTicketUpdate
from app.schemas.common import PaginatedData


class AdminTicketService:

    async def list_tickets(
        self, filters: AdminTicketFilter | None, page: int, page_size: int
    ) -> PaginatedData[AdminTicketListItem]:
        async with get_db_ctx() as db:
            base = select(Ticket)
            if filters and filters.status:
                base = base.where(Ticket.status == filters.status)
            count_stmt = select(func.count()).select_from(base.subquery())
            total = (await db.execute(count_stmt)).scalar() or 0
            stmt = base.order_by(Ticket.id.desc()).offset((page - 1) * page_size).limit(page_size)
            result = await db.execute(stmt)
            tickets = result.scalars().all()
            return PaginatedData[AdminTicketListItem](
                items=[AdminTicketListItem.model_validate(t) for t in tickets],
                total=total,
                page=page,
                page_size=page_size,
            )

    async def update_ticket(self, ticket_id: int, data: AdminTicketUpdate) -> AdminTicketListItem:
        async with get_db_ctx() as db:
            ticket = await db.get(Ticket, ticket_id)
            if ticket is None:
                raise NotFoundException("工单")
            update_data = data.model_dump(exclude_unset=True)
            for key, value in update_data.items():
                setattr(ticket, key, value)
            await db.commit()
            await db.refresh(ticket)
            return AdminTicketListItem.model_validate(ticket)
