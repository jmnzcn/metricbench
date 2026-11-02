-- One row per payment; an order can be paid in several parts (e.g. card plus voucher).
select
    p.order_id || '-' || p.payment_sequential as payment_id,
    p.order_id,
    o.purchased_at,
    p.payment_type,
    p.payment_installments,
    p.payment_value
from {{ ref('stg_payments') }} p
join {{ ref('stg_orders') }} o using (order_id)
