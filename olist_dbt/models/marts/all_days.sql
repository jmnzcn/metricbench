{{ config(materialized = 'table') }}

select cast(range as date) as date_day
from range(date '2016-01-01', date '2019-01-01', interval 1 day)
