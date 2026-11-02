-- One row per real person (customer_unique_id) with at least one valid order.
-- Olist gives every order a new customer_id, so customer_id cannot count customers.
with valid_orders as (
    select customer_unique_id, customer_state, purchased_at
    from {{ ref('orders') }}
    where is_valid
)

select
    customer_unique_id,
    min(purchased_at) as first_order_at,
    arg_min(customer_state, purchased_at) as first_order_state,
    count(*) as valid_orders,
    count(*) >= 2 as is_repeat
from valid_orders
group by 1
