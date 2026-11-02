select order_id, payment_sequential, payment_type, payment_installments, payment_value
from read_csv('{{ var("raw_dir") }}/olist_order_payments_dataset.csv', header = true)
