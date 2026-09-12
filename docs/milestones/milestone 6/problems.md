# Problems

1. **Temporal query decomposition chưa robust.** Parser hiện phụ thuộc nhiều vào cue rõ như `then`, `after`, `sau đó`, `E1/E2...`; query dạng nhiều câu mô tả các cảnh liên tiếp có thể bị hiểu thành một event duy nhất. Temporal repair bằng LLM có hỗ trợ nhưng vẫn phụ thuộc chất lượng agent.

2. **Không phân biệt rõ “tìm đúng video/sequence” và “tìm đúng frame”.** Với query mô tả cả đoạn clip nhưng không nói frame đích, hệ thống vẫn cần `temporal_anchor_index`; fallback có thể chọn event giữa, dẫn tới tìm đúng video nhưng trả sai frame.

3. **Agent tạo `must_have` nhưng downstream gần như không dùng.** Các constraint quan trọng như màu sắc, vật thể, hình dạng, pattern chưa được kiểm tra bắt buộc; retrieval chủ yếu dựa trên similarity nên frame “gần nghĩa” vẫn có thể thắng frame đúng.

4. **Multi-view fusion thiên quá nhiều về best matching view.** Công thức hiện cho `best.final_score` trọng số lớn, nên candidate chỉ match mạnh một phần của query có thể đứng cao dù thiếu nhiều chi tiết quan trọng.

5. **Visual retrieval mặc định chưa khai thác hết ensemble.** `competition_default` chủ yếu dùng CLIP; SigLIP2 đã có nhưng bị disable trong profile. Điều này làm retrieval các chi tiết fine-grained như màu, patchwork, geometric pattern, đồ thủ công chưa tối ưu.

6. **Temporal search đang dựa vào `frame_idx` và giả định 30 FPS.** `delta_t_max_ms` được đổi sang frame bằng `*30`, không robust với video khác FPS hoặc keyframe sampling không đều. Nên dùng `timestamp_ms`/`pts_time`.

7. **Temporal gap quá rộng và giống nhau cho mọi event pair.** Default 180 giây có thể ghép các cảnh đúng thứ tự nhưng không thật sự thuộc cùng một đoạn ngữ cảnh. Nên dùng per-edge temporal constraints.

8. **`prefer_full_sequences=True` có thể loại đúng video.** Nếu video đúng match mạnh 2/3 events nhưng miss một event do retrieval, trong khi video sai match yếu cả 3, full-sequence filtering có thể ưu tiên video sai.

9. **Partial-sequence scoring chưa phạt missing event hợp lý.** `_sequence_score()` chia cho số event đã match, nên sequence thiếu event vẫn có thể có average score cao. Nên normalize theo tổng số expected events và thêm coverage penalty/reward.

10. **Score calibration giữa events chưa tốt.** Semantic/text score được normalize theo max của chính candidate pool; event khó với raw similarity thấp vẫn có thể thành score `1.0`. Temporal layer vì vậy có thể hiểu nhầm candidate yếu thành candidate rất chắc chắn.

11. **Reranker mặc định đang tắt.** `competition_default.reranking.enabled = false`, nên nhiều candidate sau retrieval không có lớp kiểm tra precision đủ mạnh.

12. **Reranker hiện tại chủ yếu text-based, chưa phải true visual reranker.** Cross-encoder rerank caption/OCR/object text; branch MLLM ở pipeline hiện cũng chưa thực sự nhận pixel image tại điểm rerank. Vì vậy hệ thống chưa kiểm chứng trực tiếp hình ảnh.

13. **Chưa có VLM constraint checker ở cuối pipeline.** Không có lớp kiểm tra kiểu: “có doll không?”, “có fabric ball không?”, “áo có cream + geometric patches không?”. Đây là nguyên nhân chính khiến semantic-near nhưng visually-wrong frame lọt lên top.

14. **Retrieval đang thiên về frame-first thay vì video-first.** Với query multi-event, tốt hơn nên tìm event hiếm nhất để xác định candidate videos trước, rồi search các event còn lại trong những video đó.

15. **Chưa tận dụng diagnostic/rare event.** Mọi event gần như được xử lý tương đương, trong khi các event hiếm như “handmade dolls + fabric spheres” có khả năng xác định đúng video tốt hơn event generic như “crowd outdoors”.

16. **Chưa có local dense frame refinement.** Nếu Milvus chỉ index sparse keyframes, exact frame đúng có thể không nằm trong candidate pool. Dù reranker tốt đến đâu cũng không chọn được frame chưa được retrieve. Cần decode thêm các frame quanh timestamp top candidate.

17. **Candidate duplication có thể làm lãng phí budget.** Nhiều frame gần nhau trong cùng shot/video có thể chiếm nhiều vị trí top-k. Nên thêm temporal NMS / shot-level dedup trước VLM.

18. **VLM nếu tích hợp cần đặt sau retrieval, không dùng trên toàn bộ 500 frame.** Pipeline nên thu hẹp `500 frames → candidate videos → temporal sequences → local frames → VLM`, để tối ưu latency và GPU/API cost.

Tóm gọn thành 5 nhóm problem lớn nhất là: **Query Planning chưa hiểu narrative tốt → Temporal scoring chưa robust → Retrieval thiếu constraint awareness → Final reranking chưa nhìn trực tiếp pixel → Exact-frame refinement chưa có.**

Những problem quan trọng:

1. **Temporal query decomposition chưa robust.** Parser hiện phụ thuộc nhiều vào cue rõ như `then`, `after`, `sau đó`, `E1/E2...`; query dạng nhiều câu mô tả các cảnh liên tiếp có thể bị hiểu thành một event duy nhất. Temporal repair bằng LLM có hỗ trợ nhưng vẫn phụ thuộc chất lượng agent.
2. **Không phân biệt rõ “tìm đúng video/sequence” và “tìm đúng frame”.** Với query mô tả cả đoạn clip nhưng không nói frame đích, hệ thống vẫn cần `temporal_anchor_index`; fallback có thể chọn event giữa, dẫn tới tìm đúng video nhưng trả sai frame.

temporal query, dùng LLM để phân tách, không gán cứng 6. **Temporal search đang dựa vào `frame_idx` và giả định 30 FPS.** `delta_t_max_ms` được đổi sang frame bằng `*30`, không robust với video khác FPS hoặc keyframe sampling không đều. Nên dùng `timestamp_ms`/`pts_time`.

7. **Temporal gap quá rộng và giống nhau cho mọi event pair.** Default 180 giây có thể ghép các cảnh đúng thứ tự nhưng không thật sự thuộc cùng một đoạn ngữ cảnh. Nên dùng per-edge temporal constraints.

8. **`prefer_full_sequences=True` có thể loại đúng video.** Nếu video đúng match mạnh 2/3 events nhưng miss một event do retrieval, trong khi video sai match yếu cả 3, full-sequence filtering có thể ưu tiên video sai -> ưu tiên phần đúng nhiều event hơn

9. **Partial-sequence scoring chưa phạt missing event hợp lý.** `_sequence_score()` chia cho số event đã match, nên sequence thiếu event vẫn có thể có average score cao. Nên normalize theo tổng số expected events và thêm coverage penalty/reward.

10. **Score calibration giữa events chưa tốt.** Semantic/text score được normalize theo max của chính candidate pool; event khó với raw similarity thấp vẫn có thể thành score `1.0`. Temporal layer vì vậy có thể hiểu nhầm candidate yếu thành candidate rất chắc chắn.

11. **Retrieval đang thiên về frame-first thay vì video-first.** Với query multi-event, tốt hơn nên tìm event hiếm nhất để xác định candidate videos trước, rồi search các event còn lại trong những video đó.

12. **Chưa tận dụng diagnostic/rare event.** Mọi event gần như được xử lý tương đương, trong khi các event hiếm như “handmade dolls + fabric spheres” có khả năng xác định đúng video tốt hơn event generic như “crowd outdoors”.

13. **Candidate duplication có thể làm lãng phí budget.** Nhiều frame gần nhau trong cùng shot/video có thể chiếm nhiều vị trí top-k. Nên thêm trước VLM.
