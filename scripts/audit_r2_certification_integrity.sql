-- R2 read-only certification integrity audit.
-- Run against PostgreSQL before enabling dedicated-entry enforcement.
-- Results are for manual classification; do not use them for bulk updates.

-- 1. Dedicated certification orders without a domain registration.
SELECT
    'dedicated_order_without_registration' AS issue,
    o.id AS order_id,
    o.user_id,
    o.product_type,
    o.plan_id,
    o.status,
    o.created_at
FROM "order" o
LEFT JOIN h3c_registration h3c_reg ON h3c_reg.order_id = o.id
LEFT JOIN nisp_registration nisp_reg ON nisp_reg.order_id = o.id
WHERE (
        o.product_type LIKE 'NISP-%'
        OR o.plan_id IN (SELECT plan_id FROM nisp_exam_batch)
        OR o.plan_id IN (SELECT plan_id FROM h3c_exam_batch)
    )
  AND h3c_reg.id IS NULL
  AND nisp_reg.id IS NULL
ORDER BY o.id;

-- 2. H3C registrations whose stored candidate identity differs from the
-- account's current verified identity. A mismatch may be historical identity
-- correction and requires manual timing/evidence review.
SELECT
    'h3c_identity_mismatch' AS issue,
    reg.id AS registration_id,
    reg.user_id,
    reg.created_at,
    reg.candidate_idcard,
    identity_record.id_card_number AS current_verified_id_card,
    reg.candidate_snapshot ->> 'candidate_name' AS stored_candidate_name,
    identity_record.real_name AS current_verified_name
FROM h3c_registration reg
JOIN user_realname identity_record
  ON identity_record.user_id = reg.user_id
 AND identity_record.status = 'verified'
WHERE identity_record.id_card_number IS DISTINCT FROM reg.candidate_idcard
   OR identity_record.real_name IS DISTINCT FROM (reg.candidate_snapshot ->> 'candidate_name')
ORDER BY reg.id;

-- 3. NISP registrations with the same current-identity mismatch signal.
SELECT
    'nisp_identity_mismatch' AS issue,
    reg.id AS registration_id,
    reg.user_id,
    reg.created_at,
    reg.candidate_idcard,
    identity_record.id_card_number AS current_verified_id_card,
    reg.candidate_snapshot ->> 'name' AS stored_candidate_name,
    identity_record.real_name AS current_verified_name
FROM nisp_registration reg
JOIN user_realname identity_record
  ON identity_record.user_id = reg.user_id
 AND identity_record.status = 'verified'
WHERE identity_record.id_card_number IS DISTINCT FROM reg.candidate_idcard
   OR identity_record.real_name IS DISTINCT FROM (reg.candidate_snapshot ->> 'name')
ORDER BY reg.id;

-- 4. NISP batches whose Plan/Product ownership or level mapping is invalid.
-- `product_inactive` may be intentional delisting; do not mutate historical rows.
SELECT
    CASE
        WHEN product.id IS NULL THEN 'nisp_batch_product_missing'
        WHEN product.type <> 'nisp' THEN 'nisp_batch_product_wrong_type'
        WHEN product.code <> ('NISP-' || batch.level) THEN 'nisp_batch_product_level_mismatch'
        WHEN NOT product.is_active THEN 'nisp_batch_product_inactive'
        ELSE 'nisp_batch_product_unexpected'
    END AS issue,
    batch.id AS batch_id,
    batch.level,
    batch.plan_id,
    plan.product_type,
    plan.status AS plan_status,
    product.id AS product_id,
    product.type AS product_type,
    product.code AS product_code,
    product.is_active
FROM nisp_exam_batch batch
JOIN plan ON plan.id = batch.plan_id
LEFT JOIN cert_product product ON product.code = plan.product_type
WHERE product.id IS NULL
   OR product.type <> 'nisp'
   OR product.code <> ('NISP-' || batch.level)
   OR NOT product.is_active
ORDER BY batch.id;
