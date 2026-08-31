from fastapi import APIRouter

from app.api.admin.activities import router as activities_router
from app.api.admin.agreements import router as agreements_router
from app.api.admin.auth import router as auth_router
from app.api.admin.banners import router as banners_router
from app.api.admin.cert_products import router as cert_products_router
from app.api.admin.certifications import router as cert_router
from app.api.admin.competition import router as competition_legacy_router
from app.api.admin.competitions import router as competitions_router
from app.api.admin.coupons import router as coupons_router
from app.api.admin.deployment_acceptance import router as deployment_acceptance_router
from app.api.admin.jobs import router as jobs_router
from app.api.admin.courses import router as courses_router
from app.api.admin.course_assignments import router as course_assignments_router
from app.api.admin.course_uploads import router as course_uploads_router
from app.api.admin.orders import router as orders_router
from app.api.admin.plans import router as plans_router
from app.api.admin.reviews import router as reviews_router
from app.api.admin.prices import router as prices_router
from app.api.admin.quiz import router as quiz_router
from app.api.admin.renshe import router as renshe_router
from app.api.admin.settings import router as settings_router
from app.api.admin.statistics import router as statistics_router
from app.api.admin.system_updates import router as system_updates_router
from app.api.admin.h3c import router as h3c_router
from app.api.admin.tickets import router as tickets_router
from app.api.admin.training import router as training_router
from app.api.admin.upload import router as upload_router
from app.api.admin.users import router as users_router
from app.api.admin.zones import router as zones_router

router = APIRouter(prefix="/admin")
router.include_router(activities_router)
router.include_router(auth_router)
router.include_router(banners_router)
router.include_router(cert_products_router)
router.include_router(users_router)
router.include_router(orders_router)
router.include_router(reviews_router)
router.include_router(courses_router)
router.include_router(course_assignments_router)
router.include_router(course_uploads_router)
router.include_router(cert_router)
router.include_router(plans_router, prefix="/certifications")
router.include_router(jobs_router)
router.include_router(prices_router)
router.include_router(quiz_router)
router.include_router(renshe_router)
router.include_router(zones_router)
router.include_router(coupons_router)
router.include_router(deployment_acceptance_router)
router.include_router(agreements_router)
router.include_router(tickets_router)
router.include_router(statistics_router)
router.include_router(system_updates_router)
router.include_router(h3c_router)
router.include_router(settings_router)
router.include_router(competition_legacy_router)
router.include_router(competitions_router)
router.include_router(training_router)
router.include_router(upload_router)
