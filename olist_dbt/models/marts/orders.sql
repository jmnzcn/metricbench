-- One row per order, with the flags every metric filters on.
with items as (
    select order_id, count(*) as items, sum(price) as gmv, sum(freight_value) as freight
    from {{ ref('stg_order_items') }}
    group by 1
),

payments as (
    select order_id, sum(payment_value) as paid
    from {{ ref('stg_payments') }}
    group by 1
),

-- Some orders have several reviews; the most recently answered one counts.
reviews as (
    select order_id, review_score
    from {{ ref('stg_reviews') }}
    qualify row_number() over (
        partition by order_id order by review_answer_timestamp desc, review_creation_date desc, review_id
    ) = 1
),

base as (
    select
        o.order_id,
        c.customer_unique_id,
        c.customer_state,
        c.customer_city,
        o.order_status,
        o.purchased_at,
        o.delivered_at,
        o.estimated_delivery_at,
        o.order_status not in ('canceled', 'unavailable') as is_valid,
        o.order_status = 'canceled' as is_canceled,
        o.order_status = 'delivered' and o.delivered_at is not null as is_delivered
    from {{ ref('stg_orders') }} o
    join {{ ref('stg_customers') }} c using (customer_id)
)

select
    base.*,
    case when is_delivered then cast(delivered_at as date) > cast(estimated_delivery_at as date) end as is_late,
    case when is_delivered then date_diff('second', purchased_at, delivered_at) / 86400.0 end as delivery_days,
    coalesce(items.items, 0) as items,
    coalesce(items.gmv, 0) as gmv,
    coalesce(items.freight, 0) as freight,
    coalesce(payments.paid, 0) as paid,
    reviews.review_score
from base
left join items using (order_id)
left join payments using (order_id)
left join reviews using (order_id)
