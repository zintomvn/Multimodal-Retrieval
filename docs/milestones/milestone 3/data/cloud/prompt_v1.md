Bạn là một AI engineer chuyên nghiệp, giờ tôi cần bạn làm cho tôi một pipeline ingest data lên google cloud storage, thỏa mãn các yêu cầu sau:

Cho phép ingest từ nhiều nguồn data: kaggle, google drive, upload từ máy tính nhưng ưu tiên chạy được trên kaggle trước để tôi chạy test

Ở đây là 3 bộ data tôi cần để đưa vào GSC:

Data:
L21-L30: https://www.kaggle.com/datasets/aresusayhi/ai-challenge-2025
K01-K10: https://www.kaggle.com/datasets/tuktuai/data-video-batch-2-1
K11-K20: https://www.kaggle.com/datasets/tuktuai/data-video-batch2-2

Chạy theo batch processing chia thành từng batch và có thông số, tham số, file/folder nào để tôi chọn trong quá trình ingestion

Có monitoring bài bản bẳng công nghệ Airflow / Cloud Composer, có ghi lại logs, metrics, chia thành các dashboard

Tham khảo quy trình plan trong file {`data_ingestion_plan.md`}

Ghi lại README.md để tôi có thể sử dụng và giám sát trong các công nghệ
