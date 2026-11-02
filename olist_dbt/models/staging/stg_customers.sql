select customer_id, customer_unique_id, customer_city, customer_state
from read_csv('{{ var("raw_dir") }}/olist_customers_dataset.csv', header = true, types = {'customer_zip_code_prefix': 'VARCHAR'})
