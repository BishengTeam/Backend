#!/usr/bin/env bash
set -Eeuo pipefail

# Manual human-resources flow smoke test. It does not approve a refund,
# finalize a batch, or delete data.

API_BASE="${API_BASE:-http://127.0.0.1:8000}"
API_BASE="${API_BASE%/}"
COMPOSE_FILE="${COMPOSE_FILE:-/home/bisheng/work/weMiniApp/docker-compose.yml}"
FIXTURE_DIR="${FIXTURE_DIR:-}"
START_COMPOSE="${START_COMPOSE:-0}"

for command_name in curl jq; do
    command -v "$command_name" >/dev/null 2>&1 || {
        echo "missing command: $command_name" >&2
        exit 127
    }
done

if [[ -z "${ADMIN_USERNAME:-}" || -z "${ADMIN_PASSWORD:-}" ]]; then
    echo "set ADMIN_USERNAME and ADMIN_PASSWORD before running" >&2
    exit 2
fi

if [[ "$START_COMPOSE" == "1" ]]; then
    command -v docker >/dev/null 2>&1 || {
        echo "START_COMPOSE=1 requires docker" >&2
        exit 127
    }
    docker compose -f "$COMPOSE_FILE" config >/dev/null
    docker compose -f "$COMPOSE_FILE" up -d --build backend admin
fi

echo "== health and readiness =="
curl -fsS "$API_BASE/health" | jq .
curl -fsS "$API_BASE/ready" | jq .

echo "== openapi route checks =="
OPENAPI_JSON=$(curl -fsS "$API_BASE/openapi.json")
echo "$OPENAPI_JSON" | jq -r '[.paths | keys[] | select(contains("/renshe"))] | "renshe_paths=" + (length | tostring)'
echo "$OPENAPI_JSON" | jq -r '[.paths | keys[] | select(contains("/enterprise"))] | "enterprise_paths=" + (length | tostring)'

echo "== administrator login =="
ADMIN_LOGIN=$(curl -fsS -X POST "$API_BASE/admin/auth/login" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg u "$ADMIN_USERNAME" --arg p "$ADMIN_PASSWORD" \
        '{username:$u,password:$p}')")
echo "$ADMIN_LOGIN" | jq '{code,message,data:(.data | {admin,permissions})}'
ADMIN_TOKEN=$(echo "$ADMIN_LOGIN" | jq -er '.data.access_token')

echo "== create and publish a one-cent test batch =="
PLAN_RESPONSE=$(curl -fsS -X POST "$API_BASE/admin/certifications/RS-ZY/plans" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg name "manual-renshe-$(date +%s)" '
      {
        name:$name,
        apply_start:"2030-01-01T00:00:00+08:00",
        apply_end:"2030-12-31T23:59:59+08:00",
        exam_date:"2031-01-10T09:00:00+08:00",
        capacity:0,
        price_cents:1,
        exam_location:"manual-test-location",
        description:"manual human-resources smoke test",
        contact_name:"manual-admin",
        contact_phone:"13800138000"
      }')")
echo "$PLAN_RESPONSE" | jq .
PLAN_ID=$(echo "$PLAN_RESPONSE" | jq -er '.data.id')

curl -fsS -X PUT \
    "$API_BASE/admin/certifications/RS-ZY/plans/$PLAN_ID/publish" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq .

if [[ -z "${WECHAT_CODE:-}" ]]; then
    cat <<'EOF'
WECHAT_CODE is not set. The remaining user flow requires a fresh wx.login code.
Set WECHAT_CODE and rerun this script after keeping the test batch if desired.
EOF
    exit 0
fi

echo "== WeChat user login =="
USER_LOGIN=$(curl -fsS -X POST "$API_BASE/api/auth/login" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg code "$WECHAT_CODE" '{code:$code}')")
echo "$USER_LOGIN" | jq '{code,message,data:(.data | {user,expires_in})}'
USER_TOKEN=$(echo "$USER_LOGIN" | jq -er '.data.access_token')
USER_ID=$(echo "$USER_LOGIN" | jq -er '.data.user.id')

if [[ -z "$FIXTURE_DIR" ]]; then
    cat <<'EOF'
FIXTURE_DIR is not set. Provide six valid test files and rerun:
  id-card-front.jpg id-card-back.jpg portrait.jpg student-card.jpg
  xuexin-registration.pdf education-proof.jpg
EOF
    exit 0
fi

for material_file in \
    id-card-front.jpg id-card-back.jpg portrait.jpg student-card.jpg \
    xuexin-registration.pdf education-proof.jpg; do
    test -f "$FIXTURE_DIR/$material_file" || {
        echo "missing fixture: $FIXTURE_DIR/$material_file" >&2
        exit 2
    }
done

upload_key() {
    local kind="$1"
    local file="$2"
    local mime="$3"
    curl -fsS -X POST "$API_BASE/api/renshe/verification-materials/$kind" \
        -H "Authorization: Bearer $USER_TOKEN" \
        -F "file=@$file;type=$mime" | jq -er '.data.storage_key'
}

echo "== upload six required materials =="
ID_FRONT_KEY=$(upload_key id_card_front "$FIXTURE_DIR/id-card-front.jpg" image/jpeg)
ID_BACK_KEY=$(upload_key id_card_back "$FIXTURE_DIR/id-card-back.jpg" image/jpeg)
PORTRAIT_KEY=$(upload_key portrait "$FIXTURE_DIR/portrait.jpg" image/jpeg)
STUDENT_CARD_KEY=$(upload_key student_card "$FIXTURE_DIR/student-card.jpg" image/jpeg)
XUEXIN_KEY=$(upload_key xuexin_registration "$FIXTURE_DIR/xuexin-registration.pdf" application/pdf)
EDUCATION_KEY=$(upload_key education_proof "$FIXTURE_DIR/education-proof.jpg" image/jpeg)

echo "== submit identity and student information =="
curl -fsS -X POST "$API_BASE/api/user/identity" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg f "$ID_FRONT_KEY" --arg b "$ID_BACK_KEY" --arg a "$PORTRAIT_KEY" '
      {
        user_type:"student",
        real_name:"Manual Test",
        id_card_number:"11010519491231002X",
        id_card_front_oss:$f,
        id_card_back_oss:$b,
        avatar_oss:$a,
        political_status:"mass-member",
        ethnicity:"han"
      }')" | jq .

curl -fsS -X POST "$API_BASE/api/user/student" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --arg s "$STUDENT_CARD_KEY" --arg x "$XUEXIN_KEY" --arg d "$EDUCATION_KEY" '
      {
        education:"bachelor",
        school:"Manual Test University",
        major:"Information Security",
        enrollment_date:"2023-09-01",
        student_card_oss:$s,
        enrollment_pdf_oss:$x,
        degree_cert_oss:$d
      }')" | jq .

echo "== administrator verifies identity and student records =="
curl -fsS -X PUT "$API_BASE/admin/users/$USER_ID/identity/review" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"status":"verified","comment":"manual identity review passed"}' | jq .

curl -fsS -X PUT "$API_BASE/admin/users/$USER_ID/student/review" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"status":"verified","comment":"manual student review passed"}' | jq .

echo "== save draft and submit =="
DRAFT_RESPONSE=$(curl -fsS -X POST "$API_BASE/api/renshe/applications/draft" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --argjson p "$PLAN_ID" '
      {plan_id:$p,contact_phone:"13800138000",mailing_address:"manual test address",email:"manual@example.com"}')")
echo "$DRAFT_RESPONSE" | jq .
DRAFT_ID=$(echo "$DRAFT_RESPONSE" | jq -er '.data.id')

SUBMIT_RESPONSE=$(curl -fsS -X POST "$API_BASE/api/renshe/applications/$DRAFT_ID/submit" \
    -H "Authorization: Bearer $USER_TOKEN")
echo "$SUBMIT_RESPONSE" | jq .
APPLICATION_ID=$(echo "$SUBMIT_RESPONSE" | jq -er '.data.application.id')
ORDER_ID=$(echo "$SUBMIT_RESPONSE" | jq -er '.data.order_id')

echo "== expected payment V3 blocker =="
curl -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    -X POST "$API_BASE/api/payment/prepay" \
    -H "Authorization: Bearer $USER_TOKEN" \
    -H 'Content-Type: application/json' \
    --data "$(jq -n --argjson id "$ORDER_ID" '{order_id:$id}')"

echo "== pending application must not enter initial review =="
curl -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    -X POST "$API_BASE/admin/renshe/applications/$APPLICATION_ID/initial-review" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' \
    -d '{"decision":"approved"}'

echo "== private material URL and disabled enterprise route =="
curl -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    "$API_BASE/api/renshe/verification-materials/id_card_front/signed-url" \
    -H "Authorization: Bearer $USER_TOKEN"
curl -sS -w '\nHTTP_STATUS:%{http_code}\n' \
    "$API_BASE/api/user/enterprise" \
    -H "Authorization: Bearer $USER_TOKEN"

echo "== admin export and cleanup queues =="
curl -fsS "$API_BASE/admin/renshe/plans/$PLAN_ID/exports" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq .
curl -fsS "$API_BASE/admin/renshe/plans/$PLAN_ID/cleanup-runs" \
    -H "Authorization: Bearer $ADMIN_TOKEN" | jq .

echo "Created test records: plan_id=$PLAN_ID application_id=$APPLICATION_ID order_id=$ORDER_ID"
echo "Do not call refund or batch-finalize until the payment V3 credentials and callback are configured."
