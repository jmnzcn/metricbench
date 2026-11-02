-- Category in English; when Olist has no translation, the Portuguese name; when there is no category, 'unknown'.
select
    p.product_id,
    coalesce(t.product_category_name_english, p.product_category_name, 'unknown') as category
from read_csv('{{ var("raw_dir") }}/olist_products_dataset.csv', header = true) p
left join read_csv('{{ var("raw_dir") }}/product_category_name_translation.csv', header = true) t
    using (product_category_name)
