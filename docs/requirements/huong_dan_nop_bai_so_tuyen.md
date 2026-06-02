# Hướng dẫn nộp bài sơ tuyển

## Các loại truy vấn

Vòng sơ tuyển bao gồm 3 dạng truy vấn chính:

1. **Textual Known Item Search (Textual KIS)**: Tìm kiếm chính xác theo văn bản
2. **Visual Question Answering (Q&A)**: Truy vấn dạng Hỏi-Đáp
3. **Temporal Retrieval and Alignment of Key Events (TRAKE)**: Truy xuất và căn chỉnh sự kiện video theo thời gian

## Các gói truy vấn

Trong vòng sơ tuyển BTC sẽ cung cấp lần lượt các gói câu truy vấn theo nhiều đợt. Với mỗi gói câu truy vấn, đội thi cần trả về kết quả tương ứng cho BTC và nộp trên hệ thống Codabench.

Với mỗi gói câu truy vấn, BTC sẽ cung cấp một danh sách các câu truy vấn trong từng file text. Ví dụ trong đợt 1, BTC cung cấp gói gồm 4 câu truy vấn `query-1-kis`, `query-2-kis`, `query-3-qa`, `query-4-trake` tương ứng với nội dung trong 4 file `query-1-kis.txt`, `query-2-kis.txt`, `query-3-qa.txt`, `query-4-trake.txt`.

### Quy ước tên file truy vấn

- Hậu tố `kis`: Câu truy vấn dạng Textual KIS
- Hậu tố `qa`: Câu truy vấn dạng Q&A
- Hậu tố `trake`: Câu truy vấn dạng TRAKE

## Yêu cầu kết quả

Đối với mỗi câu truy vấn, đội thi cần nộp tương ứng một file `.csv` (comma-separated values file) với mỗi dòng tương ứng với một lần đội dự đoán kết quả. Đội thi có thể nộp file tối đa 100 dòng. Kết quả trên mỗi dòng của đội có format theo từng loại truy vấn:

### 1. Textual Known Item Search (Textual KIS)

**Format:** `<Tên file video>, <Frame Idx>`

**Ví dụ:**

```csv
L00_V000, 1234
L00_V055, 5555
L01_V028, 25300
```

### 2. Question Answering (Q&A)

**Format:** `<Tên file video>, <Frame Idx>, <Answer>`

**Quy định cho Answer:**

- Độ dài tối đa: **100 ký tự**
- Có thể bằng tiếng Việt hoặc tiếng Anh
- Được so sánh chính xác về mặt ngữ nghĩa với đáp án

**Ví dụ:**

```csv
L01_V028, 3450, "5"
L02_V011, 1200, "Năm người"
L03_V005, 2800, "Màu đỏ"
```

### 3. Temporal Retrieval and Alignment of Key Events (TRAKE)

**Format:** `<Tên file video>, <Frame ID_1>, <Frame ID_2>, ..., <Frame ID_N>`

Trong đó:

- `Frame ID_1`, `Frame ID_2`, ..., `Frame ID_N` là các keyframe tương ứng với N events trong chuỗi sự kiện
- Số lượng Frame ID phải khớp với số events được yêu cầu trong truy vấn
- Thứ tự các Frame ID phải tuân theo thứ tự thời gian của các events

**Ví dụ chuỗi 4 events:**

```csv
L10_V001, 1200, 1850, 2100, 2450
L10_V001, 1180, 1820, 2080, 2420
L11_V003, 5100, 5700, 6200, 6800
```

## Quy chuẩn định dạng CSV

> ⚠️ **Lưu ý QUAN TRỌNG cho học sinh THPT:**
>
> **CSV ≠ Excel:** Đây là hai định dạng file hoàn toàn khác nhau!

- **File CSV (`.csv`)**: Là file văn bản thuần túy, chỉ chứa dữ liệu được phân cách bằng dấu phẩy
- **File Excel (`.xlsx` / `.xls`)**: Là file nhị phân phức tạp của Microsoft Excel

**PHẢI NỘP FILE `.CSV`, KHÔNG PHẢI FILE EXCEL!**

### Cách tạo file CSV đúng

1. **Từ Excel:** File → Save As → chọn **CSV (Comma delimited) (`*.csv`)**
2. **Từ Google Sheets:** File → Download → **Comma Separated Values (`.csv`)**
3. **Từ Notepad:** Gõ trực tiếp theo format và lưu với đuôi `.csv`
4. **Từ các text editor:** VS Code, Sublime Text, Notepad++

### Kiểm tra file CSV

- Có thể mở bằng Notepad và thấy dữ liệu dạng text thuần túy
- Kích thước file nhỏ hơn nhiều so với Excel
- Đuôi file phải là `.csv` (**KHÔNG** phải `.xlsx` hoặc `.xls`)

### Quy tắc chung

1. **Encoding:** UTF-8
2. **Delimiter:** Dấu phẩy `,`
3. **Line ending:** CRLF (`\r\n`) hoặc LF (`\n`)
4. **Không có header row:** File CSV bắt đầu trực tiếp bằng dữ liệu

## Xử lý ký tự đặc biệt

**Lưu ý quan trọng:** Dấu ngoặc kép chỉ **BẮT BUỘC** khi answer chứa các ký tự đặc biệt. Nếu answer đơn giản không có ký tự đặc biệt, có thể bỏ qua dấu ngoặc kép.

### 1. Dấu phẩy trong answer: BẮT BUỘC bao quanh bằng dấu ngoặc kép

```csv
L01_V028, 3450, "Có 3 người, bao gồm nam và nữ"
```

### 2. Dấu ngoặc kép trong answer: BẮT BUỘC escape bằng double quotes

```csv
L01_V028, 3450, "Anh ấy nói ""Xin chào"""
```

### 3. Xuống dòng trong answer: BẮT BUỘC bao quanh bằng dấu ngoặc kép

```csv
L01_V028, 3450, "Dòng 1
Dòng 2"
```

### 4. Answer đơn giản: KHÔNG BẮT BUỘC dấu ngoặc kép

```csv
L01_V028, 3450, 5
L02_V011, 1200, Năm người
L03_V005, 2800, Màu đỏ
```

### 5. Khoảng trắng đầu/cuối

Khoảng trắng đầu/cuối được giữ nguyên, không tự động trim.

## Ví dụ CSV chuẩn cho từng loại

### Textual KIS (`query-1-kis.csv`)

```csv
L00_V000,1234
L00_V055,5555
L01_V028,25300
```

### Q&A (`query-2-qa.csv`) - Cả hai cách đều đúng

```csv
L01_V028,3450,5
L02_V011,1200,Năm người
L03_V005,2800,"Màu đỏ, rất đẹp"
L04_V012,4100,"Anh ấy nói ""Tuyệt vời"""
```

**HOẶC** với dấu ngoặc kép cho tất cả answer:

```csv
L01_V028,3450,"5"
L02_V011,1200,"Năm người"
L03_V005,2800,"Màu đỏ, rất đẹp"
L04_V012,4100,"Anh ấy nói ""Tuyệt vời"""
```

### TRAKE (`query-3-trake.csv` - 4 events)

```csv
L10_V001,1200,1850,2100,2450
L10_V001,1180,1820,2080,2420
L11_V003,5100,5700,6200,6800
```

## Quy tắc dấu ngoặc kép trong CSV

### KHÔNG cần ngoặc kép

- Answer đơn giản: `5`, `Năm người`, `Màu đỏ`, `Ba`
- Chỉ chứa chữ cái, số, khoảng trắng thông thường
- Không có dấu phẩy, ngoặc kép, xuống dòng

### BẮT BUỘC có ngoặc kép

- Answer có dấu phẩy: `"Có 3 người, bao gồm nam và nữ"`
- Answer có ngoặc kép: `"Anh ấy nói ""Xin chào"""`
- Answer có xuống dòng: `"Dòng 1\nDòng 2"`

### An toàn nhất

Để tránh nhầm lẫn, có thể **luôn đặt dấu ngoặc kép** cho tất cả answer trong Q&A. Cả hai cách đều được hệ thống chấp nhận.

## Hướng dẫn tạo file CSV cho học sinh THPT

### Phương pháp 1: Sử dụng Microsoft Excel

1. Mở Excel và nhập dữ liệu theo đúng format
2. File → **Save As**
3. Chọn vị trí lưu file
4. Trong mục **Save as type** → chọn **CSV (Comma delimited) (`*.csv`)**
5. Đặt tên file theo quy định, ví dụ: `query-1-kis.csv`
6. Click **Save**
7. Nếu Excel hỏi về compatibility → click **Yes**

### Phương pháp 2: Sử dụng Google Sheets

1. Mở Google Sheets và nhập dữ liệu
2. File → **Download** → **Comma Separated Values (`.csv`)**
3. File sẽ được tải về máy với đuôi `.csv`

### Phương pháp 3: Sử dụng Notepad cho người hiểu kỹ thuật

1. Mở Notepad
2. Gõ dữ liệu theo đúng format, ví dụ: `L00_V000,1234`
3. File → **Save As**
4. Trong mục **Save as type** → chọn **All Files (`*.*`)**
5. Đặt tên file với đuôi `.csv`, ví dụ: `query-1-kis.csv`
6. Trong mục **Encoding** → chọn **UTF-8**

## Kiểm tra file CSV đã đúng chưa

1. Click chuột phải vào file → **Open with** → **Notepad**
2. Nếu thấy dữ liệu dạng text thuần túy với dấu phẩy phân cách → ✅ **ĐÚNG**
3. Nếu thấy ký tự lạ hoặc không đọc được → ❌ **SAI** vì có thể vẫn là Excel format

### Lỗi thường gặp

- **Lưu nhầm file Excel:** File có đuôi `.xlsx` / `.xls` thay vì `.csv`
- **Encoding sai:** File hiển thị ký tự lạ khi mở bằng Notepad
- **Delimiter sai:** Sử dụng dấu chấm phẩy `;` thay vì dấu phẩy `,`
- **Có header:** Dòng đầu chứa tiêu đề thay vì dữ liệu

## Nộp kết quả cho gói truy vấn

Mỗi đội thi cần đăng ký một tài khoản trên Codabench và đăng ký tham gia vào cuộc thi. BTC sẽ duyệt cho đội tham gia vào cuộc thi nếu tên hoặc thông tin tài khoản Codabench trùng với thông tin đội thi đã đăng ký trước đó với BTC.

### Cách chuẩn bị file nộp

**Bước 1:** Tạo thư mục có tên `submission`

**Bước 2:** Đặt tất cả file CSV kết quả vào trong thư mục `submission`

**Bước 3:** Nén thư mục `submission` thành file `.zip`

**Bước 4 (Tùy chọn):** Đổi tên file zip thành tên phù hợp, ví dụ: `team_ABC_round1.zip`

### Cấu trúc thư mục yêu cầu

```text
submission/
├── query-1-kis.csv
├── query-2-kis.csv
├── query-3-qa.csv
├── query-4-trake.csv
└── ... (các file CSV khác)
```

### Ví dụ file nộp cuối cùng

`team_ABC_round1.zip` chứa:

```text
submission/
├── query-1-kis.csv
├── query-2-kis.csv
├── query-3-qa.csv
└── query-4-trake.csv
```

### Lưu ý quan trọng

- **PHẢI** có thư mục `submission` bên trong file zip
- **KHÔNG** nén trực tiếp các file CSV - phải nén thư mục `submission`
- Đội thi có thể xem cách đánh giá tại tab **Evaluation** để biết thêm thông tin về cách tính điểm
- Tên file video **không có phần đuôi** `.mp4`
- Frame ID sẽ được so sánh dưới dạng số nguyên
- Answer Q&A sẽ được so sánh dưới dạng chuỗi chính xác
- Answer Q&A có độ dài tối đa **100 ký tự**
- Đối với TRAKE: Số lượng Frame ID phải khớp chính xác với số events yêu cầu
- Chỉ chấp nhận file nén định dạng `.zip`
- **Khuyến cáo:** Tên file zip chỉ nên bao gồm các ký tự chữ hoặc số

## Đánh giá và xếp hạng

Kết quả đánh giá trên **Public Leaderboard** chỉ tính dựa trên 50% đáp án của BTC. Kết quả cuối cùng của đội nộp sẽ được tính trên 100% đáp án và dùng để xếp hạng vòng sơ tuyển tại **Private Leaderboard**.

### Phương pháp tính điểm

Mỗi gói truy vấn, các đội được phép nộp kết quả tối đa **3 lần**. Kết quả được dùng để xếp hạng là kết quả đội nộp **lần cuối cùng**.

### Lưu ý cuối cùng

- Mỗi đội chỉ được dùng duy nhất một tài khoản để nộp bài
- Khi nộp sai định dạng vẫn tính là 01 lần nộp
- Đội cần lưu ý chọn lựa kết quả nào để nộp lần cuối cùng
- Khuyến nghị kiểm tra kỹ format CSV trước khi nộp để tránh lỗi parse
