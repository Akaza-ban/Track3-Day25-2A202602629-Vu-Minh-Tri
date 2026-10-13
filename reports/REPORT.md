# Lab 25B — Report

**Họ tên / nhóm:** Vũ Minh Trí (2A202602629)  **Commit:** 18cc181  **Điểm tự động (`make grade`):** 84.0 / 88

> Mọi nhận định phải có số. Dán số từ `reports/gameday/*.json`, `reports/workflows.json`, `reports/slo.json`.

## 1. Kết quả Game Day (naive vs của bạn)

| Băng | Client | availability | p95 (s) | amplification | retry_ratio | cost/turn | recovery (s) | PASS? |
|---|---|---:|---:|---:|---:|---:|---:|:---:|
| G1 | naive | 0.7194 | 7.157 | 7.738 | 6.738 | 2.163 | 4.0 | FAIL |
| G1 | yours | 1.0000 | 4.776 | 1.062 | 0.002 | 2.101 | 0.0 | PASS |
| G2 | naive | 0.4560 | 15.034 | 9.904 | 8.904 | 4.627 | 155.0 | FAIL |
| G2 | yours | 1.0000 | 11.911 | 1.059 | 0.008 | 2.161 | 0.0 | PASS |
| G3 | naive | 1.0000 | 4.768 | 1.164 | 0.164 | 2.328 | 0.0 | FAIL |
| G3 | yours | 1.0000 | 4.738 | 1.058 | 0.034 | 2.141 | 0.0 | PASS |
| G4 | naive | 1.0000 | 13.764 | 1.004 | 0.004 | 2.008 | 0.0 | FAIL |
| G4 | yours | 1.0000 | 5.562 | 1.015 | 0.002 | 2.072 | 0.0 | PASS |
| G5 | naive | 0.3699 | 5.359 | 17.030 | 16.030 | 2.124 | None | FAIL |
| G5 | yours | 0.8179 | 5.477 | 1.003 | 0.002 | 2.504 | None | PASS |
| G6 | naive | 0.5170 | 4.836 | 12.344 | 11.344 | 2.009 | 0.0 | FAIL |
| G6 | yours | 1.0000 | 5.961 | 1.055 | 0.018 | 2.196 | 0.0 | PASS |

## 2. Cấu hình client và lý do

| Tham số | Giá trị | Vì sao (dựa trên băng nào, số liệu nào) |
|---|---:|---|
| attempt_timeout_s | 8.0 | Giới hạn thời gian chờ mỗi attempt; ở G6, nếu để timeout 30s thì khi `alpha-eu` bị treo sẽ nuốt trọn deadline 30s của turn khiến availability sập còn 94.88%. Giá trị 8.0s đủ cho độ trễ bình thường (median ~1.2-1.8s) mà vẫn kịp failover. |
| max_attempts_per_provider | 2 | Chỉ cho phép tối đa 1 retry trên cùng provider (tổng 2 attempts), tránh dồn tải liên tục vào provider đang suy thoái (ở G2 naive thử tới 36 lần khiến amplification vọt lên 9.904). |
| backoff_base_s / cap_s | 0.25 / 4.0 | Full jitter với base 0.25s giúp phân tán tức thời tải retry, cap 4.0s đảm bảo retry không delay quá lâu vượt turn deadline (30s). Giúp G1, G2 đạt 0 retry-after violations. |
| retry_budget_ratio | 0.1 | Giới hạn tổng số lượt retry không vượt quá 10% lượng request mới, trực tiếp dập tắt retry storm khi breaker bị lỗi ở G2 (retry_ratio giảm từ 0.491 xuống 0.058). |
| breaker.window_size / min_calls | 20 / 10 | Đủ mẫu thống kê để tính tỷ lệ lỗi, tránh hiện tượng "cold start" hoặc vài lỗi ngẫu nhiên ban đêm làm trip nhầm circuit breaker (G5 có lỗi 400 nhưng không làm mở breaker). |
| breaker.failure_rate_threshold | 0.5 | Ngưỡng 50% lỗi kích hoạt OPEN breaker để cắt tải sang provider dự phòng (`alpha-eu`, `beta`) khi provider chính sập (giúp G2, G6 đạt availability 100%). |
| breaker.slow_call_s | 5.0 | Bắt các call chậm quá 5s (bình thường < 2s). Ở G4 mạng chậm thêm 5s không sinh lỗi HTTP, slow-call kích hoạt breaker chuyển traffic sang `alpha-eu`, hạ p95 từ 13.764s (naive) xuống 5.562s. |
| breaker.slow_rate_threshold | 0.6 | Khi 60% call trong window bị chậm thì ngắt mạch. Đảm bảo ở G4 client nhận diện suy thoái kịp thời. |
| breaker.open_duration_s | 10.0 | Thời gian mở mạch 10s trước khi thử nghiệm HALF-OPEN. Vừa đủ để provider xả bớt hàng đợi quá tải ở G2 (recovery_s = 0s). |
| breaker.half_open_max_probes | 2 | Chỉ cho phép tối đa 2 request thăm dò khi ở HALF-OPEN, ngăn chặn hiện tượng thundering herd làm sập lại provider vừa mới hồi phục. |

Sơ đồ quyết định của `on_error`:

```
                    +---------------------------+
                    |    Nhận ProviderError     |
                    +-------------+-------------+
                                  |
                                  v
                        [Phân loại ErrorKind]
                                  |
         +------------------------+------------------------+
         |                        |                        |
         v                        v                        v
   CLIENT_ERROR         QUOTA_EXHAUSTED / RATE_LIMITED   OVERLOADED / SERVER_ERROR / TIMEOUT
         |                        |                        |
         | (release permit)       | (release permit)       | (on_failure permit)
         v                        v                        v
+------------------+     +------------------+     +----------------------------------+
| GiveUp           |     | Cooldown provider|     | tries += 1                       |
| ("client_error") |     | - Quota: 300s    |     | tries < max_attempts (2)         |
| Không retry      |     | - Rate: retry-aft|     | & budget có token & kịp deadline?|
| Không failover   |     | Failover ngay lập|     +----------------+-----------------+
+------------------+     | tức sang provider|                      |
                         | tiếp theo        |             +--------+--------+
                         +------------------+             |                 |
                                                       (Đúng)             (Sai)
                                                          v                 v
                                                 +-----------------+ +---------------+
                                                 | Retry SAME      | | Failover ngay |
                                                 | (Full jitter    | | sang provider |
                                                 |  backoff delay) | | tiếp theo     |
                                                 +-----------------+ +---------------+
```

## 3. Ba thí nghiệm (mỗi cái 3–5 câu + số)

1. **Retry budget là lớp phòng thủ cuối:**
   - Khi giả lập breaker bị vô hiệu hóa (`min_calls: 1000000000`) và cho phép `max_attempts_per_provider: 5`:
     - Khi **có** retry budget: ở G2 amplification là **1.382**, retry_ratio là **0.058**, recovery là **0.0s**, availability đạt **99.96%**; ở G3 amplification là **1.135**, retry_ratio là **0.067**.
     - Khi **tắt** retry budget: ở G2 amplification vọt lên **1.672**, retry_ratio tăng vọt gấp 8.5 lần lên **0.491**, recovery bị trễ mất **30.0s**, availability sụt xuống **96.49%**; ở G3 retry_ratio tăng lên **0.162**.
   - Khi breaker hoạt động đúng, nó ngắt mạch sớm nên retry budget ít khi bị cạn kiệt. Tuy nhiên, vẫn bắt buộc phải có cả hai vì retry budget là **chốt chặn an toàn cuối cùng (defense-in-depth)**: nếu cấu hình breaker bị sai, ngưỡng trip quá cao hoặc lỗi xảy ra phân tán chưa đủ mở breaker, retry budget đảm bảo lưu lượng retry toàn hệ thống không bao giờ vượt quá tỷ lệ an toàn (~10%), ngăn chặn hoàn toàn thảm họa Retry Storm.

2. **`attempt_timeout_s: 30`:**
   - Khi tăng timeout attempt lên 30s: ở G4 độ trễ p95 là **5.683s** (vẫn đạt); nhưng ở G6 (sự cố outage toàn diện của Provider A), availability bị rớt từ **100%** xuống **94.88%** (FAIL kiểm tra `availability_min >= 0.95`).
   - Nguyên nhân là do provider `alpha-eu` bị treo hoàn toàn (hang): khi attempt timeout bằng 30s, request đầu tiên bị giữ trọn toàn bộ thời gian cho đến khi hết deadline của turn (30s), khiến client không còn thời gian để failover sang `beta` và buộc phải bỏ cuộc.
   - Timeout "đúng" nên dựa vào phân phối độ trễ bình thường (SLO latency p99 của provider đang chạy, ví dụ 3–4 lần median ~ 4–8s) và phải nhỏ hơn đáng kể so với turn deadline tổng (30s) để dành đủ thời gian cho fallback ladder hoạt động.

3. **Bỏ lọc tool calling khỏi `ladder()`:**
   - Khi chạy G6 mà không lọc năng lực `supports_tools`, số lượng `incompatible_routes` nhảy từ **0** lên **256**, và availability tổng sụt giảm từ **100%** xuống **89.27%**.
   - Dưới góc nhìn Product Manager: Đây là lỗi nghiêm trọng ảnh hưởng trực tiếp đến trải nghiệm khách hàng. Có 256 người dùng yêu cầu xử lý hoàn tiền nhưng hệ thống lại chuyển việc kiểm tra đơn hàng đến một model giá rẻ không biết dùng công cụ (`gamma-mini`). Hậu quả là tác vụ hoàn tiền lập tức thất bại, khách hàng bị từ chối phục vụ vô lý, và uy tín của chatbot chăm sóc khách hàng bị suy giảm nặng nề.

## 4. Workflow hoàn tiền

| Executor | completion | crashes | duplicate refunds | duplicate emails | lost refunds | overpaid | wasted LLM / crash |
|---|---:|---:|---:|---:|---:|---:|---:|
| naive | 1.0000 | 81 | 128 | 81 | 0 | 1,574,200 đ | 1.123 |
| yours | 1.0000 | 66 | 0 | 0 | 0 | 0 đ | 0.303 |

- **Vì sao chỉ idempotency key là chưa đủ, và chỉ checkpoint cũng chưa đủ?**
  - Nếu chỉ có Idempotency Key mà không có Checkpoint: Khi worker crash và worker mới khởi động lại từ đầu, nó sẽ phải chạy lại toàn bộ các bước LLM phía trước (tốn chi phí và thời gian wasted LLM) và liên tục gửi lại request cho các bước đã hoàn tất.
  - Nếu chỉ có Checkpoint mà không có Idempotency Key: Khi worker gọi payment thành công nhưng response trả về bị timeout mạng (network timeout / dropped ack) hoặc worker chết ngay trước khi kịp ghi checkpoint, worker mới chạy lại bước payment sẽ tạo ra một giao dịch trừ tiền thứ hai, dẫn đến hoàn tiền trùng (duplicate refund). Cần có cả hai để vừa phục hồi điểm dừng vừa đảm bảo an toàn giao dịch tài chính.
- **Điều gì xảy ra nếu ghi checkpoint *trước* khi gọi payment?**
  - Nếu checkpoint được lưu trước khi gọi payment: Trong trường hợp worker vừa ghi checkpoint xong thì bị crash trước khi payment thực sự được thực hiện, khi worker mới khởi động lại, nó đọc thấy bước payment đã "hoàn thành" nên bỏ qua bước này. Kết quả là khách hàng không bao giờ nhận được tiền hoàn (**Lost Refund** — lỗi nghiêm trọng nhất trong thanh toán).

## 5. Alerting & error budget

| Sự cố | Mong đợi | Naive alerter | Burn-rate alerter | Độ trễ phát hiện |
|---|---|---|---|---:|
| checkout_bug | page | page (phút 1) | page | 27 phút |
| provider_outage | page | page (phút 0) | page | 4 phút |
| deploy_blip | none | page (báo động giả) | none | Không bị page |
| slow_leak | ticket | none (bỏ lọt) | ticket | 1337 phút (~22h) |
| degraded_evening | page | page (phút 214) | page | 291 phút |
| night_spike | none | page (báo động giả) | none | Không bị page |
| small_bump | none | page (báo động giả) | none | Không bị page |

- **Error budget 30 ngày:**
  - Tổng số requests: 25,901,755
  - Lượng lỗi cho phép (allowed_bad): 129,508.8 requests
  - Số lỗi thực tế (bad): 55,618 requests
  - Availability thực tế: 99.7853% (vượt mục tiêu SLO 99.5%)
  - Budget đã tiêu: **42.95%**, Budget còn lại: **57.05%**.
  - **Quyết định:** Không đóng băng release (`freeze_releases: False`) vì còn hơn 57% ngân sách lỗi.
- **Vì sao `small_bump` (20 phút ở 12% lỗi) không nên đánh thức ai lúc 11 giờ trưa?**
  - Một đợt lỗi 12% kéo dài 20 phút chỉ tiêu tốn một phần cực nhỏ ngân sách lỗi 30 ngày (~0.3% error budget). Lỗi này tự hết nhanh chóng và không đe dọa đến mục tiêu SLO tháng. Nếu dùng cảnh báo ngưỡng ngắn (như Naive alerter 5 phút), kỹ sư on-call sẽ bị đánh thức liên tục bởi các biến động tạm thời (alert fatigue), dẫn đến mất tập trung và giảm năng suất làm việc.

## 6. Postmortem — Băng G2 với client naive (blameless)

*Dùng `make plot` (hình `reports/gameday/G2_overload_storm.png`) và CSV `reports/gameday/naive_G2_overload_storm_timeseries.csv`.*

**Tóm tắt (3 câu):**
Trong kịch bản G2, provider `alpha` bị suy giảm 75% năng lực GPU trong vòng 3 phút (t=120 đến t=300). Bản client v1 (NaiveClient) với 3 tầng retry chồng chéo, không circuit breaker và không jitter đã gây ra một cơn bão retry storm lên đến hơn 254 requests/giây vào hàng đợi vốn đã nghẽn. Hậu quả là hệ thống rơi vào trạng thái lỗi siêu bền vững (metastable failure), kéo dài thời gian sập thêm 155 giây sau khi hạ tầng provider đã hoàn toàn hồi phục, khiến tỷ lệ thành công của user chỉ đạt 45.6%.

**Ảnh hưởng:**
- Tổng cộng 1,285 lượt yêu cầu của người dùng bị thất bại (trong tổng số 2,362 lượt, availability chỉ đạt 45.60%).
- Thời gian gián đoạn kéo dài 335 giây (180 giây thời gian sự cố gốc + 155 giây recovery lag).
- Lượng lỗi này tiêu tốn vượt quá **180%** error budget tháng nếu xét trong cửa sổ tương ứng, gây vi phạm cam kết chất lượng dịch vụ nghiêm trọng.

**Timeline** (giây ảo — trích xuất từ `naive_G2_overload_storm_timeseries.csv`):

| t | Sự kiện | Bằng chứng (số) |
|---:|---|---|
| 0–119 | Hệ thống vận hành bình thường | Lưu lượng ổn định ~7–10 req/s, calls_ok = 100% |
| 120 | Provider alpha mất 75% năng lực xử lý GPU | Capacity factor giảm về 0.25; requests bắt đầu dồn ứ trong queue |
| 122 | Tỷ lệ thành công sụp đổ hoàn toàn | `calls_ok` rớt về 0 req/s; các request bắt đầu bị timeout và 529 |
| 136 | Bùng phát cơn bão Retry Storm | Lượng attempts dồn vào `alpha` nhảy vọt từ 21 req/s lên 53 req/s |
| 143–145 | Đỉnh điểm bão retry (Amplification cực đại) | Lượng attempts gửi vào alpha đạt đỉnh **254 req/s** (gấp hơn 25 lần tải bình thường) |
| 300 | Provider alpha khôi phục 100% năng lực phần cứng | Sự cố gốc kết thúc, GPU hoạt động lại bình thường |
| 301–452 | Hệ thống tiếp tục tê liệt dù hạ tầng đã khỏe | `calls_ok` duy trì ở mức **0 req/s** suốt 151 giây tiếp theo do server tiếp tục xử lý các request hết hạn trong hàng đợi |
| 453 | Hàng đợi rác bắt đầu được giải phóng | Lần đầu tiên ghi nhận `calls_ok = 4` |
| 455 | Hệ thống phục hồi hoàn toàn (Recovery hoàn tất) | `calls_ok` trở lại mức bình thường ~12–16 req/s, recovery delay đo được = **155.0s** |

**Trigger vs root cause:**
- **Trigger:** Sự cố hạ tầng provider `alpha` bị mất 75% GPU trong 3 phút (t=120 đến t=300).
- **Root cause:** Thiết kế client naive với 3 tầng retry nhân tử (app × gateway × SDK = tối đa 36 attempts/call), delay cố định không có jitter ngẫu nhiên, hoàn toàn không có circuit breaker để cắt tải, và bỏ qua header `retry-after`.
- **Vì sao provider đã hồi phục mà hệ thống vẫn sập (Metastable Failure)?**
  - Server của provider áp dụng chính sách hàng đợi FIFO và server **vẫn tiếp tục xử lý các request ngay cả khi client phía trước đã timeout**.
  - Trong suốt 180s sự cố, hàng chục nghìn request retry rác liên tục được nhồi vào hàng đợi. Đến t=300 khi GPU phục hồi, toàn bộ năng lực xử lý mới lại bị lãng phí để chạy các request rác mà client đã bỏ từ lâu (deadline expired).
  - Vòng lặp tự duy trì: Server bận xử lý request cũ rác -> Request mới đến tiếp tục bị timeout -> Client lại retry request mới -> Hàng đợi luôn đầy ắp -> Tải duy trì vượt ngưỡng xử lý cho đến tận t=455.

**Điều gì giúp / điều gì làm tệ hơn:**
- *Làm tệ hơn:* Retry liên tục với delay cố định; thử lại ngay lập tức khi gặp 429 mà không tôn trọng `retry-after`; tiếp tục gửi request vào provider đang quá tải thay vì failover.
- *Giúp cải thiện:* Circuit breaker ngắt mạch sớm khi tỷ lệ lỗi vượt 50%; cơ chế backoff có full jitter; retry budget giới hạn tối đa 10% request retry; fallback ladder chuyển lưu lượng sang `alpha-eu` và `beta`.

**Action items:**

| # | Hành động | Loại | Owner | Cách kiểm chứng |
|---|---|---|---|---|
| 1 | Triển khai ResilientClient tích hợp Circuit Breaker và Retry Budget thay thế hoàn toàn NaiveClient | Mitigate | On-call / Platform Team | Chạy `make gameday`, kiểm tra amplification trên G2 ≤ 1.10 |
| 2 | Cấu hình tôn trọng `retry-after` và Full Jitter Exponential Backoff cho mọi SDK gọi LLM | Prevent | SDK Team | Đo `retry_after_violations = 0` trên toàn bộ các bài test tích hợp |
| 3 | Thiết lập hệ thống Multi-window Burn-rate Alerting theo Google SRE Workbook | Detect | SRE Team | Tự động page trong vòng < 5 phút khi xảy ra provider outage, 0 false alarm trên blip |

## 7. Một điểm yếu còn lại

- **Điểm yếu hiện tại:** Khi provider chính (`alpha`) bị sự cố và hệ thống failover sang `beta`, mặc dù độ sẵn sàng (availability) được bảo toàn ở mức 100%, chi phí trên mỗi lượt (`cost_per_good_turn`) bị đội lên đáng kể vì `beta` đắt hơn 40% (cost 1.4 so với 1.0 của `alpha`). Ngoài ra, nếu xảy ra tình huống cả 4 provider cùng bị suy thoái độ trễ đồng thời, client hiện tại vẫn phải tuần tự thử và timeout từng provider một.
- **Giải pháp khắc phục:** 
  1. Triển khai cơ chế **Cost-aware Spillover**: Chỉ chuyển sang `beta` cho các request thực sự quan trọng (VIP users hoặc tác vụ phức tạp), các tác vụ chat thông thường ưu tiên model rẻ hơn hoặc trả lời từ cache.
  2. Bổ sung kỹ thuật **Hedged Requests (Request Hedging)**: Khi request đầu tiên gửi đi mà sau khoảng p95 thời gian chưa có phản hồi, gửi thêm một request song song đến provider thứ hai và lấy kết quả của provider trả về trước, giúp triệt tiêu đuôi độ trễ (tail latency) mà không làm bùng phát tải.

## 8. Cache (Part G)

Số từ `reports/cache/*.json` (`make cache`):

| Client | availability | faq_availability | cache_hit_ratio | stale_served | wrong_answers | outdated_answers | cost/turn | PASS? |
|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| resilient (không cache) | 0.8373 | 0.8424 | 0.000 | 0 | 0 | 0 | 1.386 | FAIL |
| cached (của bạn) | 0.9124 | 0.9645 | 0.652 | 157 | 0 | 3 | 0.915 | PASS |

| Tham số `cache:` | Giá trị | Vì sao (số liệu nào) |
|---|---:|---|
| similarity_threshold | 0.92 | Ngưỡng 0.80 quá lỏng gây 175 câu trả lời sai (`other_question: 175`). Nâng lên 0.92 loại bỏ hoàn toàn câu trả lời sai (`wrong_answers: 0`) mà vẫn giữ hit ratio tốt (65.2%). |
| ttl_s | 60 | TTL 600s của starter khiến câu trả lời phí ship cũ bị dùng tới 80 lần sau khi giá đổi ở t=480. Giảm TTL xuống 60s giới hạn `outdated_answers` chỉ còn 3 (đạt chuẩn ≤ 15). |
| max_stale_s | 300 | Khi toàn bộ provider sập trong 120s (t=240 đến t=360), nếu `max_stale_s = 0` thì `faq_availability` chỉ đạt 87.33% (FAIL). Với 300s, 157 câu trả lời cũ được phục vụ qua stale-if-error, đưa `faq_availability` lên 96.45%. |

Thí nghiệm:

1. **Ngưỡng similarity:**
   - Ở `similarity_threshold = 0.80`: hit ratio là **65.9%**, chi phí 0.978, nhưng phát sinh tới **175 câu trả lời sai** (`wrong_answers_by_reason: {'other_question': 175}`). Các câu hỏi có ý nghĩa khác nhau nhưng chung từ khóa (như "thời gian hoàn tiền" bị nhầm thành "chính sách hoàn tiền") bị gán chung câu trả lời.
   - Ở `similarity_threshold = 0.92`: hit ratio đạt **65.2%**, **wrong_answers = 0**, chi phí tối ưu nhất đạt **0.915** đ/turn.
   - Ở `similarity_threshold = 0.97`: ngưỡng quá khắt khe khiến hit ratio giảm còn **57.9%**, chi phí tăng lên 0.965 vì nhiều câu hỏi cùng ý nghĩa bị miss cache và phải gọi lại LLM.

2. **TTL:**
   - Khi `ttl_s = 30`: `outdated_answers = 2`, hit ratio = **59.2%**.
   - Khi `ttl_s = 60`: `outdated_answers = 3`, hit ratio = **65.2%** (cân bằng hoàn hảo giữa độ tươi và tỷ lệ trúng cache).
   - Khi `ttl_s = 300`: `outdated_answers` vọt lên **95** (vi phạm tiêu chí ≤ 15).
   - *Vì sao webhook không giúp được gì cho `outdated_answers` và TTL là thứ duy nhất giới hạn nó?* Webhook chỉ hoạt động khi phía CMS phát ra sự kiện thông báo thay đổi nội dung (như chính sách hoàn tiền ở t=150). Đối với các thay đổi "ngầm" không báo trước từ bên thứ ba (như hãng vận chuyển âm thầm đổi bảng giá cước ở t=480), hệ thống không thể nhận được bất kỳ tín hiệu invalidation nào; do đó TTL chính là cơ chế duy nhất bảo đảm dữ liệu cũ tự động hết hạn và bị loại bỏ.

3. **Stale-if-error:**
   - Khi cấu hình `max_stale_s: 0`: trong đợt mất điện toàn bộ provider từ t=240 đến t=360, số lượng `stale_served = 0`, khiến `faq_availability` tụt từ **96.45%** xuống còn **87.33%** (không đạt mức yêu cầu ≥ 93%). Nhờ có stale-if-error với cửa sổ 300s, hệ thống đã phục vụ 157 câu trả lời cũ thay vì báo lỗi cho khách hàng, duy trì trải nghiệm liền mạch cho dịch vụ FAQ.

Trả lời:

- **Vì sao câu hỏi cá nhân ("Đơn #48213 của tôi đang ở đâu?") không được cache, kể cả khi cache theo từng user?**
  - Thứ nhất, dữ liệu cá nhân liên quan đến trạng thái thực tế thay đổi liên tục (đơn hàng vừa giao, số dư điểm thưởng vừa trừ) nên việc trả về cache rất dễ sai lệch thực tế.
  - Thứ hai và quan trọng nhất là rủi ro **rò rỉ dữ liệu nhạy cảm (Privacy / Security Leak)**: việc cache theo semantic embedding tiềm ẩn nguy cơ câu hỏi của User B bị đối sánh nhầm với User A (hoặc trong cùng user nhưng hỏi về đơn hàng khác), dẫn đến lộ thông tin đơn hàng, địa chỉ hoặc số tiền hoàn của người khác.
- **Trong lúc mọi provider sập, dashboard vẫn xanh nhờ cache. Bạn sẽ alert dựa trên số nào để on-call vẫn biết có sự cố?**
  - Không thể chỉ dựa vào metric `availability` ở tầng user-facing vì cache đang che giấu lỗi.
  - Phải thiết lập alert dựa trên:
    1. **Tỷ lệ lỗi trực tiếp của Backend/Provider (Provider Upstream Error Rate / 5xx rate)**: đo số lượng lỗi khi CachingClient thực sự gọi xuống `inner.start()` và nhận `GiveUp` hoặc lỗi từ provider.
    2. **Tốc độ phục vụ Stale (`stale_served_rate`)**: khi tốc độ trả lời bằng stale answer tăng đột biến vượt ngưỡng (ví dụ > 5 request/phút), lập tức kích hoạt cảnh báo on-call rằng backend LLM đang mất kết nối và hệ thống đang phải hoạt động ở chế độ cầm cự.
