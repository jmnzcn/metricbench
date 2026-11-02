-- One row per item sold, with the purchase time of its order.
select
    i.order_id || '-' || i.order_item_id as order_item_id,
    i.order_id,
    i.product_id,
    i.seller_id,
    o.purchased_at,
    i.price,
    i.freight_value
from {{ ref('stg_order_items') }} i
join {{ ref('stg_orders') }} o using (order_id)
