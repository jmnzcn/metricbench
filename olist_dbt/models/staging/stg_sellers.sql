select seller_id, seller_city, seller_state
from read_csv('{{ var("raw_dir") }}/olist_sellers_dataset.csv', header = true, types = {'seller_zip_code_prefix': 'VARCHAR'})
