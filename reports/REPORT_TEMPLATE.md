# Lab 25B — Report

**Họ tên / nhóm:** …  **Commit:** …  **Điểm tự động (`make grade`):** … / 90

> Mọi nhận định phải có số. Dán số từ `reports/gameday/*.json`, `reports/workflows.json`, `reports/slo.json`.

## 1. Kết quả Game Day (naive vs của bạn)

| Băng | Client | availability | p95 (s) | amplification | retry_ratio | cost/turn | recovery (s) | PASS? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | | | | | | | |
| G1 | yours | | | | | | | |
| G2 | naive | | | | | | | |
| G2 | yours | | | | | | | |
| G3 | … | | | | | | | |
| G4 | … | | | | | | | |
| G5 | … | | | | | | | |
| G6 | … | | | | | | | |

## 2. Cấu hình client và lý do

| Tham số | Giá trị | Vì sao (dựa trên băng nào, số liệu nào) |
|---|---:|---|
| attempt_timeout_s | | |
| max_attempts_per_provider | | |
| backoff_base_s / cap_s | | |
| retry_budget_ratio | | |
| breaker.window_size / min_calls | | |
| breaker.failure_rate_threshold | | |
| breaker.slow_call_s | | |
| breaker.open_duration_s | | |
| breaker.half_open_max_probes | | |

Sơ đồ quyết định của `on_error` (ASCII hoặc bảng: loại lỗi → ghi breaker? → retry? → failover? → cooldown?):

```
```

## 3. Ba thí nghiệm (mỗi cái 3–5 câu + số)

1. **Retry budget là lớp phòng thủ cuối.** Giả lập breaker cấu hình sai (`breaker.min_calls: 1000000000`) và đặt
   `max_attempts_per_provider: 5`. Chạy G2, G3 hai lần: (a) giữ retry budget; (b) tắt budget
   (`retry_budget_ratio: 100`, `retry_budget_max_tokens: 1.0e9`, `retry_budget_min_per_s: 1.0e6`).
   So sánh amplification, retry_ratio, recovery. Khi breaker *hoạt động đúng* thì budget còn quan trọng không — vì sao
   vẫn nên có cả hai?
2. **`attempt_timeout_s: 30`.** Chạy G4, G6. Chuyện gì xảy ra với p95 và availability? Timeout "đúng" nên dựa vào đâu?
3. **Bỏ lọc tool calling khỏi `ladder()`.** Chạy G6. Giải thích con số `incompatible_routes` bằng ngôn ngữ của
   một product manager.

## 4. Workflow hoàn tiền

| Executor | completion | crashes | duplicate refunds | duplicate emails | lost refunds | overpaid | wasted LLM / crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | | | | | | | |
| yours | | | | | | | |

Trả lời: vì sao chỉ idempotency key là **chưa đủ**, và chỉ checkpoint cũng **chưa đủ**? Điều gì xảy ra nếu ghi
checkpoint *trước* khi gọi payment?

## 5. Alerting & error budget

| Sự cố | Mong đợi | Naive alerter | Burn-rate alerter | Độ trễ phát hiện |
|---|---|---|---|---:|
| checkout_bug | page | | | |
| provider_outage | page | | | |
| deploy_blip | none | | | |
| slow_leak | ticket | | | |
| degraded_evening | page | | | |
| night_spike | none | | | |
| small_bump | none | | | |

Error budget 30 ngày: đã tiêu …%, còn …%. Có đóng băng release không? Vì sao `small_bump` (20 phút ở 12% lỗi)
**không** nên đánh thức ai lúc 11 giờ trưa?

## 6. Postmortem — Băng G2 với client naive (blameless)

*Dùng `make plot` (hình `reports/gameday/G2_overload_storm.png`) và CSV `reports/gameday/naive_G2_overload_storm_timeseries.csv`.*

**Tóm tắt (3 câu):** …

**Ảnh hưởng:** bao nhiêu lượt user thất bại, kéo dài bao lâu, vượt bao nhiêu % error budget tháng (giả sử tháng
chỉ có sự cố này).

**Timeline** (giây ảo — lấy từ CSV):

| t | Sự kiện | Bằng chứng (số) |
|---:|---|---|
| 120 | alpha mất 75% năng lực | |
| … | | |
| 300 | alpha hồi phục hoàn toàn | |
| … | hệ thống vẫn chưa hồi phục — vì sao? | |
| … | | |

**Trigger vs root cause:** trigger là gì? root cause là gì? Vì sao provider đã hồi phục mà hệ thống vẫn sập
(metastable failure — vòng lặp nào tự duy trì?).

**Điều gì giúp / điều gì làm tệ hơn:** …

**Action items** (cụ thể, có owner, đo được):

| # | Hành động | Loại (prevent / detect / mitigate) | Owner | Cách kiểm chứng |
|---|---|---|---|---|
| 1 | | | | |
| 2 | | | | |
| 3 | | | | |

## 7. Một điểm yếu còn lại

Một tình huống mà client của bạn vẫn xử lý kém (gợi ý: G2 p95, chi phí khi beta gánh tải, cả 4 provider cùng chậm…),
và bạn sẽ sửa thế nào.

## 8. *(Tùy chọn — Part G)* Cache

Số từ `reports/cache/*.json` (`make cache`).

| Client | availability | faq_availability | cache_hit_ratio | stale_served | wrong_answers | outdated_answers | cost/turn | PASS? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient (không cache) | | | | | | | | |
| cached (của bạn) | | | | | | | | |

| Tham số `cache:` | Giá trị | Vì sao (số liệu nào) |
|---|---:|---|
| similarity_threshold | | |
| ttl_s | | |
| max_stale_s | | |

Thí nghiệm (mỗi cái 2–4 câu + số):

1. **Ngưỡng.** Chạy C1 với `similarity_threshold` = 0,80 / giá trị của bạn / 0,97. Ghi hit ratio, `wrong_answers`, cost.
   Câu nào bị trả nhầm ở 0,80 (gợi ý: `wrong_answers_by_reason`, các cặp "gần mà khác")?
2. **TTL.** Chạy với `ttl_s` = 30 / 60 / 300. Vì sao webhook không giúp được gì cho `outdated_answers`, và TTL là thứ
   duy nhất giới hạn nó?
3. **Stale-if-error.** Chạy với `max_stale_s: 0`. `faq_availability` thay đổi thế nào?

Trả lời:

- Vì sao câu hỏi cá nhân ("Đơn #48213 của tôi đang ở đâu?") **không** được cache, kể cả khi cache theo từng user?
- Trong lúc mọi provider sập, dashboard vẫn xanh nhờ cache. Bạn sẽ alert dựa trên số nào để on-call vẫn biết có sự cố?
