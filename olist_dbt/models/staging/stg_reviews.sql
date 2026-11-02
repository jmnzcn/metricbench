select review_id, order_id, review_score, review_creation_date, review_answer_timestamp
from read_csv('{{ var("raw_dir") }}/olist_order_reviews_dataset.csv', header = true)
