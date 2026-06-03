from app.models.activity import Activity, ActivityRegistration, ActivityReminder
from app.models.admin_user import AdminUser
from app.models.agreement import Agreement
from app.models.banner import Banner
from app.models.certification import Certification
from app.models.collection import Collection
from app.models.competition import CompetitionReg
from app.models.conversation import Conversation
from app.models.coupon import Coupon, UserCoupon
from app.models.course import Course, CourseEnrollment
from app.models.deleted_openid import DeletedOpenid
from app.models.inventory import Inventory, InventoryRecord
from app.models.job import Job, JobApplication
from app.models.order import Order
from app.models.points import PointsHistory, UserPoints
from app.models.price_config import PriceConfig
from app.models.quick_question import QuickQuestion
from app.models.quiz import QuizCategory, QuizCheckin, QuizQuestion, QuizRecord
from app.models.share import Share
from app.models.ticket import Ticket
from app.models.user import User
from app.models.user_identity import UserIdentity
from app.models.zone import Zone

__all__ = [
    "Activity",
    "ActivityRegistration",
    "ActivityReminder",
    "AdminUser",
    "Agreement",
    "Banner",
    "Certification",
    "Collection",
    "CompetitionReg",
    "Conversation",
    "Coupon",
    "Course",
    "CourseEnrollment",
    "DeletedOpenid",
    "Inventory",
    "InventoryRecord",
    "Job",
    "JobApplication",
    "Order",
    "PointsHistory",
    "PriceConfig",
    "QuickQuestion",
    "QuizCategory",
    "QuizCheckin",
    "QuizQuestion",
    "QuizRecord",
    "Share",
    "Ticket",
    "User",
    "UserCoupon",
    "UserIdentity",
    "UserPoints",
    "Zone",
]