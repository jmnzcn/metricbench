select
    order_id,
    customer_id,
    order_status,
    order_purchase_timestamp as purchased_at,
    order_approved_at as approved_at,
    order_delivered_carrier_date as shipped_at,
    order_delivered_customer_date as delivered_at,
    order_estimated_delivery_date as estimated_delivery_at
from read_csv('{{ var("raw_dir") }}/olist_orders_dataset.csv', header = true)
