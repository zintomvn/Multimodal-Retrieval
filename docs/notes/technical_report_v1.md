Prompt version 1:

role: bạn là một AI engineer&#x20;
context: dựa vào format của paper tôi truyền vào, hiện tại tôi đang cần một technical report chuyên nghiệp cho hệ thống multimodal retrieval trong link git sauhttps://github.com/zintomvn/Multimodal-Retrieval, đọc kỹ yêu cầu của ban tổ chức trong file report_requirement.md tôi gửi
task: viết cho tôi technical report cho hệ thống của tôi

contraint:

- Viết thành file latex
- hình ảnh kiến trúc thì vẽ sơ đồ bằng mermaid và để sẵn link ảnh vào file .tex, tôi sẽ thay đổi sau
- Bám theo format không chế thông tin khác
- Văn phong khoa học (tiếng việt), thuật ngữ khoa học thì không được đổi mà dùng đúng thuật ngữ
- Đọc toàn bộ repo để nắm cấu trúc và cách làm, không lấy thông tin khác repo
- font chữ dùng font thuần của latex
- Viết cấu trúc không quá 4 trang
- tách cấu trúc latex thành từng một folder cho nội dung để tôi có thể sửa trong tương lai
- Giao diện hệ thống và Phân tích một số tình huống truy vấn tiêu biểu thì để trống để tôi viết sau
- phần search mà dùng giải thuật của AST hay AIThena thì ghi dùng lại và chuyển thành Strategy 1, Strategy 2, không ghi tên của paper, không ghi vào algorithm
- Phần cải tiến nằm ở phần agent planner, ghi rõ phần này ra.

verify: check lại cho tới khi thỏa các điều kiện contraint mới được dừng
output: file zip về latex và compile một bản pdf về repo
