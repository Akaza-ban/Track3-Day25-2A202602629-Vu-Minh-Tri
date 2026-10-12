"""FAQ traffic for Part G (caching). DO NOT EDIT — the grader uses its own copy.

ShopAgent's customers ask the same few questions in many different words.
This module is a fixed catalog of *intents* (what the customer means), each
with a few paraphrases, plus a simulated embedding model:

* the same text always gets the same embedding;
* paraphrases of one intent are close (cosine ~0.94-0.98);
* some *different* intents are deliberately close too (~0.80-0.86): refund
  policy vs refund time, size vs colour exchange, city vs province shipping
  fee ... — a semantic cache with a loose threshold mixes them up;
* personal questions ("Đơn #48213 của tôi đang ở đâu?") differ only in the
  order number, so two users' questions are ~0.99 similar — and must never
  share an answer ("hủy đơn #123" ≈ "hủy đơn #124").
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

from gameday_lab.sim.config import FaqTraffic
from gameday_lab.sim.contracts import Request

EMBED_DIM = 32
CATALOG_SEED = 2510
NEAR_MISS_COSINE = 0.86  # cosine between the centroids of two "near miss" intents


@dataclass(frozen=True)
class Intent:
    key: str
    source: str | None  # document the answer is grounded on; None = the user's own account data
    weight: float  # popularity within its group (shared / personal)
    texts: tuple[str, ...]  # paraphrases; "{order}" becomes the user's order number
    near: str | None = None  # put this intent's centroid close to an earlier intent


INTENTS: tuple[Intent, ...] = (
    Intent("refund_policy", "policy/refund", 8, (
        "Chính sách hoàn tiền của shop thế nào?", "Shop có cho hoàn tiền không?",
        "Điều kiện để được hoàn tiền là gì?", "Cho mình hỏi quy định hoàn tiền với")),
    Intent("refund_time", "policy/refund", 6, (
        "Bao lâu thì nhận được tiền hoàn?", "Hoàn tiền mất mấy ngày?",
        "Tiền hoàn về tài khoản trong bao lâu?"), near="refund_policy"),
    Intent("return_window", "policy/returns", 5, (
        "Được đổi trả trong bao nhiêu ngày?", "Thời hạn đổi trả là bao lâu?",
        "Mua rồi bao lâu thì hết hạn đổi trả?")),
    Intent("exchange_size", "policy/returns", 4, (
        "Đổi size áo thế nào?", "Mặc không vừa thì đổi size được không?",
        "Muốn đổi sang size khác thì làm sao?")),
    Intent("exchange_color", "policy/returns", 2, (
        "Đổi màu sản phẩm được không?", "Mình muốn đổi sang màu khác",
        "Có cho đổi màu không shop?"), near="exchange_size"),
    Intent("shipping_time", "policy/shipping", 7, (
        "Bao lâu thì nhận được hàng?", "Giao hàng mất mấy ngày?",
        "Đặt hôm nay thì khi nào tới?")),
    Intent("shipping_fee_city", "policy/shipping", 5, (
        "Phí ship nội thành bao nhiêu?", "Giao nội thành tốn bao nhiêu tiền ship?",
        "Ship trong thành phố mất phí bao nhiêu?")),
    Intent("shipping_fee_province", "policy/shipping", 3, (
        "Phí ship đi tỉnh bao nhiêu?", "Giao hàng ra tỉnh tốn bao nhiêu phí?",
        "Ship ngoại tỉnh mất bao nhiêu tiền?"), near="shipping_fee_city"),
    Intent("payment_methods", "policy/payment", 3, (
        "Shop nhận những hình thức thanh toán nào?", "Có thanh toán bằng thẻ được không?",
        "Thanh toán qua ví điện tử được không?")),
    Intent("cod", "policy/payment", 3, (
        "Có giao hàng thu tiền (COD) không?", "Nhận hàng rồi mới trả tiền được không?",
        "Shop có ship COD không?"), near="payment_methods"),
    Intent("invoice", "policy/payment", 1, (
        "Shop có xuất hóa đơn VAT không?", "Xin hóa đơn đỏ thế nào?", "Mình cần hóa đơn công ty")),
    Intent("warranty", "policy/warranty", 2, (
        "Sản phẩm bảo hành bao lâu?", "Chính sách bảo hành thế nào?", "Hàng lỗi thì bảo hành ra sao?")),
    Intent("cancel_order", "policy/orders", 4, (
        "Làm sao để hủy đơn hàng?", "Đặt nhầm thì hủy đơn thế nào?", "Hủy đơn có mất phí không?")),
    Intent("change_address", "policy/orders", 2, (
        "Đổi địa chỉ giao hàng được không?", "Mình muốn sửa địa chỉ nhận hàng",
        "Cập nhật địa chỉ giao hàng ở đâu?")),
    Intent("voucher", "policy/promotions", 3, (
        "Dùng mã giảm giá thế nào?", "Nhập voucher ở đâu?", "Mã khuyến mãi áp dụng ra sao?")),
    Intent("membership", "policy/promotions", 2, (
        "Thành viên VIP được ưu đãi gì?", "Hạng thành viên có quyền lợi gì?",
        "Tích điểm thành viên thế nào?")),
    Intent("store_hours", "policy/stores", 1, (
        "Cửa hàng mở cửa mấy giờ?", "Giờ làm việc của shop?", "Chi nhánh mở đến mấy giờ?")),
    Intent("hotline", "policy/stores", 1, (
        "Số hotline của shop là gì?", "Liên hệ chăm sóc khách hàng bằng cách nào?",
        "Gọi tổng đài số nào?")),
    # personal: the answer depends on the user's own order / account
    Intent("my_order_status", None, 10, (
        "Đơn #{order} của tôi đang ở đâu?", "Kiểm tra giúp mình đơn #{order}",
        "Đơn hàng #{order} giao tới đâu rồi?"), near="shipping_time"),
    Intent("my_refund_status", None, 5, (
        "Tiền hoàn của đơn #{order} đã về chưa?", "Yêu cầu hoàn tiền đơn #{order} xử lý tới đâu rồi?"),
        near="refund_time"),
    Intent("my_points", None, 2, (
        "Tài khoản của tôi còn bao nhiêu điểm?", "Kiểm tra giúp mình điểm tích lũy"), near="membership"),
)


def _unit(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def _orthogonal(base: list[float], rng: random.Random) -> list[float]:
    """A random unit vector orthogonal to ``base``."""
    v = [rng.gauss(0.0, 1.0) for _ in base]
    d = sum(a * b for a, b in zip(v, base))
    return _unit([a - d * b for a, b in zip(v, base)])


def _toward(base: list[float], cosine: float, rng: random.Random) -> list[float]:
    """A random unit vector whose cosine with ``base`` is exactly ``cosine``."""
    u = _orthogonal(base, rng)
    s = math.sqrt(1.0 - cosine * cosine)
    return [cosine * a + s * b for a, b in zip(base, u)]


def _freeze(v: list[float]) -> tuple[float, ...]:
    return tuple(round(x, 6) for x in v)


class FaqCatalog:
    def __init__(self, seed: int = CATALOG_SEED) -> None:
        rng = random.Random(seed)
        self.intents = {i.key: i for i in INTENTS}
        self.shared = [i for i in INTENTS if i.source is not None]
        self.personal = [i for i in INTENTS if i.source is None]
        centroid: dict[str, list[float]] = {}
        for it in INTENTS:
            if it.near is None:
                centroid[it.key] = _unit([rng.gauss(0.0, 1.0) for _ in range(EMBED_DIM)])
            else:
                centroid[it.key] = _toward(centroid[it.near], NEAR_MISS_COSINE, rng)
        self._text: dict[str, list[float]] = {}
        for it in INTENTS:
            for text in it.texts:
                self._text[text] = _toward(centroid[it.key], rng.uniform(0.975, 0.99), rng)

    def embed(self, template: str, order: int | None = None) -> tuple[float, ...]:
        base = self._text[template]
        if order is None:
            return _freeze(base)
        # the order number nudges the vector only a little: different users' questions stay ~0.99 similar
        return _freeze(_toward(base, 0.997, random.Random(f"{template}|{order}")))

    def sample(self, rng: random.Random, spec: FaqTraffic, prompt_version: str) -> tuple[Request, Intent]:
        group = self.personal if rng.random() < spec.personal_share else self.shared
        intent = rng.choices(group, weights=[i.weight for i in group])[0]
        template = rng.choice(intent.texts)
        uid = rng.randrange(spec.users)
        if "{order}" in template:
            order = 40000 + (uid * 7919) % 60000  # each user has their own order
            text, emb = template.format(order=order), self.embed(template, order)
        else:
            text, emb = template, self.embed(template)
        return Request(text, f"u{uid:04d}", intent.source is None, prompt_version, emb), intent

    @staticmethod
    def sources(intent: Intent, user_id: str) -> tuple[str, ...]:
        return (intent.source,) if intent.source is not None else (f"account/{user_id}",)
