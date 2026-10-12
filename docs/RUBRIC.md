# Rubric — Lab 25B Game Day (100 điểm)

## Tự động — 88 điểm (`make grade`)

| Phần | Điểm | Nguồn |
|---|---:|---|
| A. Phân loại lỗi & retry | 12 | `tests/test_errors_retry.py` — điểm = 12 × (test pass / tổng) |
| B. Circuit breaker | 12 | `tests/test_breaker.py` |
| C. ResilientClient | 6 | `tests/test_client.py` |
| C. Game Day | 24 | 6 băng × 4 điểm. Một băng chỉ được điểm khi PASS **mọi** check trên **mọi** seed. Giảng viên chấm bằng bản băng ẩn + 3 seed. |
| D. Workflow | 4 | `tests/test_executor.py` |
| D. Workflow | 8 | 500 workflow có crash: 0 hoàn tiền trùng, 0 email trùng, 0 hoàn tiền mất, completion ≥ 99%, wasted LLM / crash ≤ 0,6 |
| E. Alerting | 4 | `tests/test_alerting.py` |
| E. Alerting | 4 | `data/sli_30d.csv` (public): mọi sự cố page/ticket đúng hạn, 0 false page |
| E. Alerting | 4 | Dữ liệu ẩn của giảng viên, cùng tiêu chí |
| G. Cache | 4 | `tests/test_cache.py` |
| G. Cache | 6 | Băng C1 (`make cache`): PASS **mọi** check trên **mọi** seed; giảng viên chấm bằng băng ẩn + 3 seed |

## Chấm tay — 12 điểm (`reports/REPORT.md`)

| Mục | Điểm | Đạt đủ khi |
|---|---:|---|
| Bảng Game Day + cấu hình có lý do | 2 | Đủ 6 băng × 2 client; mỗi tham số chính có lý do gắn với **số liệu** của một băng |
| 3 thí nghiệm | 2 | Có số trước/sau; giải thích đúng cơ chế (budget vs breaker, timeout vs treo, tương thích tool) |
| Workflow + alerting | 1 | Trả lời đúng "vì sao cần cả hai"; tính đúng error budget và quyết định freeze |
| Cache (mục 8) | 2 | Có bảng không cache / có cache; mỗi tham số `cache:` có lý do gắn với số liệu (`wrong_answers_by_reason`, `outdated_answers`, `faq_availability`); trả lời đúng câu hỏi về câu trả lời cá nhân và về alert khi đang phục vụ stale |
| Postmortem G2 | 5 | Xem bên dưới |

### Postmortem G2 (5 điểm)

| Tiêu chí | 0 | 0,5 | 1 |
|---|---|---|---|
| Timeline | không có | có mốc nhưng không có số | ≥ 5 mốc có số từ CSV (attempts/s, success ratio) |
| Trigger vs root cause | lẫn lộn | nêu được trigger | phân biệt rõ: trigger = mất GPU; root cause = retry 3 tầng không jitter/budget/breaker → khuếch đại tải → vòng lặp metastable |
| Giải thích "vì sao không tự hồi phục sau t=300" | không | mơ hồ | nêu được: hàng đợi đầy request mà client đã bỏ, retry giữ tải > năng lực |
| Action items | chung chung | cụ thể nhưng thiếu cách đo | ≥ 3 mục, phân loại prevent/detect/mitigate, có owner và cách kiểm chứng (vd. "amplification ≤ 1,2 trên G2 trong CI") |
| Blameless | đổ lỗi cá nhân | trung tính | tập trung vào hệ thống & quy trình |

## Trừ điểm

| Vi phạm | Trừ |
|---|---:|
| Sửa file bị cấm (sim, tests, scenarios, services…) | toàn bộ phần liên quan |
| Hard-code thời điểm / tên băng trong client | Part C = 0 |
| Hard-code thời điểm, tên băng hoặc tên tài liệu trong cache | Part G = 0 |
| Report không có số liệu | tối đa −5 |
| Code không chạy trên máy sạch (`pip install -e ".[dev]" && make grade`) | −10 |
