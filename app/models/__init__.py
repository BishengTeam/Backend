from app.models.activity import Activity, ActivityRegistration, ActivityReminder
from app.models.admin_user import AdminUser
from app.models.agreement import Agreement
from app.models.banner import Banner
from app.domain.certification.src.index import Certification, CompetitionReg, Course, CourseEnrollment, Job, JobApplication
from app.models.collection import Collection
from app.models.conversation import Conversation
from app.models.deleted_openid import DeletedOpenid
from app.models.points import PointsHistory, UserPoints
from app.domain.order.src.index import Coupon, Inventory, InventoryRecord, Order, PriceConfig, UserCoupon
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