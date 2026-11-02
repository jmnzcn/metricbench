select order_id, order_item_id, product_id, seller_id, shipping_limit_date, price, freight_value
from read_csv('{{ var("raw_dir") }}/olist_order_items_dataset.csv', header = true)
