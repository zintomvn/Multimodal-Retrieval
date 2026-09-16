# Prompt

role: bạn là một AI researcher

context: đọc kỹ repo của tôi và xem những lỗi nào còn đang gặp phải trong hệ thống

zintomvn/Multimodal-Retrieval

List ra những lỗi cần tối ưu về:

latency: duyệt xem những flow nào ảnh hưởng về vấn đề search, streaming cần tối ưu dựa trên những tài liệu về thiết kế hệ thống

Những lỗi logic trong flow search và thuật toán temporal search của từng bài toán: KIS, QA, TRAKE

Lỗi thiết kế hệ thống về các thêm, bớt strategy search, temporal search

Giao diện cần tối ưu để search nhanh và kiểm chứng, flow search, hiện UI, submission sao cho tối ưu về accuracy (search đúng frame, submission đúng )

constraint:

check với tài liệu chuyên môn về AI và Software

không được sinh thông tin mà phải dựa vào suy luận từ tài liệu

Dựa hoàn toàn vào code của dự án để suy luận

task: lập ra những lỗi và đề xuất hướng cải tiến

verify: Kiểm tra cho thỏa contraint và check vòng lặp 10 lần, gửi feedback, gọi lại model để kiểm chứng cho tới khi model verify nói đúng 98%

output: một file markdown error_analysis.md trình bày chuyên nghiệp như dân AI engineer
