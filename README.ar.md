# منظومة كنان — KenanWallet / عملة KEN (شبكة تجريبية)

**الحالة: شبكة تجريبية وتجربة عرض فقط. لا أموال حقيقية، ولا شبكة عامة، ولا إدراج.**
راجع [docs/STATUS.md](docs/STATUS.md) لمعرفة ما تم التحقق منه بالاختبارات وما بقي معلقاً — ولا تعرض أرصدة تجريبية على أنها حقيقية.

## ما هو موجود
- `blockchain/` — نواة الشبكة التجريبية وعقدة HTTP: تحويلات KEN موقعة بـ Ed25519، رسوم (نصفها يُحرق)، كتل مرتبطة بالتجزئة، حالة محفوظة.
- `wallet-core/` — محفظة ذاتية BIP39: إنشاء واستعادة وتوقيع محلي فقط.
- `rust-core/` — نواة Rust للتوقيع واشتقاق العناوين بنفس الصيغة.
- `backend/` — واجهة API + محرك تداول تجريبي DEMO + الدوائر، الدفع، الاسترداد، الأكاديمية، التحليلات، الدرع، الحوكمة (إشارية)، المساعد (قاعدي بلا مفاتيح)، الجسر (معطّل).
- `explorer/` + صفحة `/explorer` في العقدة — عرض الكتل والمعاملات والأرصدة من RPC الحي.
- `android-app/` — مصدر تطبيق Kotlin/Compose باتجاه RTL (غير مبني هنا).
- `tests/` — 13 اختباراً آلياً للسلسلة والمحفظة والتداول والوحدات وHTTP.

## التشغيل السريع
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 -m blockchain.node_server --port 8545 --data data/chain.json
KENAN_NODE_URL=http://127.0.0.1:8545 python3 -m backend.app --port 8000
python3 scripts/demo.py
python3 -m unittest discover -s tests -v
```
المستكشف: http://127.0.0.1:8545/explorer
