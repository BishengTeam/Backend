# Common / generic schemas
from app.schemas.common import APIResponse, PaginatedData, PaginatedResponse

# User-facing schemas
from app.schemas.user import (
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    PhoneDecryptRequest,
    RefreshRequest,
    RefreshResponse,
    UserIdentityCreate,
    UserIdentityResponse,
    UserProfile,
)

# Certification schemas
from app.schemas.certification import CertificationFilter, CertificationResponse

# Course schemas
from app.schemas.course import (
    CourseDetailResponse,
    CourseEnrollRequest,
    CourseEnrollmentResponse,
    CourseFilter,
    CourseListResponse,
)

# Order schemas
from app.schemas.order import (
    OrderCreate,
    OrderDetailResponse,
    OrderFilter,
    OrderResponse,
)

# Payment schemas
from app.schemas.payment import (
    PaymentCallbackRequest,
    PaymentCallbackResponse,
    PaymentPrepayRequest,
    PaymentPrepayResponse,
)

# Points schemas
from app.schemas.points import (
    PointsBalanceResponse,
    PointsClaimRequest,
    PointsClaimResponse,
    PointsHistoryResponse,
    PointsRedeemRequest,
    PointsRedeemResponse,
)

# Price config schemas
from app.schemas.price_config import PriceFilter, PriceResponse

# Quiz schemas
from app.schemas.quiz import (
    QuizCategoryResponse,
    QuizCategoryTreeResponse,
    QuizCheckinRequest,
    QuizCheckinResponse,
    QuizQuestionQuery,
    QuizQuestionResponse,
    QuizRecordQuestionResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    QuizToggleRequest,
    QuizToggleResponse,
)

# Chat schemas
from app.schemas.chat import ChatRequest, ChatResponse, QuickQuestionResponse

# System schemas
from app.schemas.system import PosterResponse

# Admin schemas
from app.schemas.admin import (
    AdminBatchDeleteRequest,
    AdminInfo,
    AdminLoginRequest,
    AdminLoginResponse,
    AdminUserFilter,
    AdminUserListItem,
    AdminUserUpdate,
)
from app.schemas.admin_agreement import (
    AdminAgreementCreate,
    AdminAgreementListItem,
    AdminAgreementReview,
)
from app.schemas.admin_banner import BannerCreate, BannerListItem, BannerUpdate
from app.schemas.admin_certification import AdminCertificationCreate, AdminCertificationUpdate
from app.schemas.admin_coupon import AdminCouponBatchCreate, AdminCouponCreate, AdminCouponListItem
from app.schemas.admin_course import AdminCourseCreate, AdminCourseListItem, AdminCourseUpdate
from app.schemas.admin_price import AdminPriceCreate, AdminPriceUpdate
from app.schemas.admin_quiz import (
    AdminQuizCategoryCreate,
    AdminQuizCategoryUpdate,
    AdminQuizImportJsonRequest,
    AdminQuizQuestionCreate,
    AdminQuizQuestionItem,
    AdminQuizQuestionUpdate,
)
from app.schemas.admin_settings import (
    AdminSettingsUserCreate,
    AdminSettingsUserListItem,
    AdminSettingsUserUpdate,
)
from app.schemas.admin_ticket import AdminTicketFilter, AdminTicketListItem, AdminTicketUpdate
from app.schemas.admin_zone import (
    AdminZoneCreate,
    AdminZoneListItem,
    AdminZoneSortItem,
    AdminZoneStatusToggle,
    AdminZoneUpdate,
)
