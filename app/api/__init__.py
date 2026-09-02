from fastapi import APIRouter

from app.api.activity import router as activity_router
from app.api.auth import router as auth_router
from app.api.certification import router as cert_router
from app.api.chat import quick_router, router as chat_router
from app.api.collection import router as collection_router
from app.api.competition import router as competition_router
from app.api.coupon import router as coupon_router
from app.api.courses import router as courses_router
from app.api.classroom import router as classroom_router
from app.api.h3c import order_router as h3c_order_router
from app.api.h3c import router as h3c_router
from app.api.job import router as job_router
from app.api.orders import router as orders_router
from app.api.plans import router as plans_router
from app.api.payment import router as payment_router
from app.api.points import router as points_router
from app.api.price_config import router as prices_router
from app.api.quiz import router as quiz_router
from app.api.renshe import router as renshe_router
from app.api.share import router as share_router
from app.api.system import router as system_router
from app.api.ticket import router as ticket_router
from app.api.training import router as training_router
from app.api.upload import media_router, upload_router
from app.api.user import router as user_router
from app.api.zone import router as zone_router

router = APIRouter(prefix="/api")
router.include_router(auth_router)
router.include_router(cert_router)
router.include_router(user_router)
router.include_router(chat_router)
router.include_router(courses_router)
router.include_router(classroom_router)
router.include_router(h3c_router)
router.include_router(h3c_order_router)
router.include_router(plans_router)
router.include_router(orders_router)
router.include_router(payment_router)
router.include_router(points_router)
router.include_router(prices_router)
router.include_router(quiz_router)
router.include_router(renshe_router)
router.include_router(system_router)
router.include_router(quick_router)
router.include_router(zone_router)
router.include_router(ticket_router)
router.include_router(collection_router)
router.include_router(activity_router)
router.include_router(coupon_router)
router.include_router(competition_router)
router.include_router(job_router)
router.include_router(share_router)
router.include_router(training_router)
router.include_router(upload_router)
router.include_router(media_router)
