#!/usr/bin/env python3
"""[DELETE-MARK-ID-ACTIVATE v4.4.24] اختبار وظيفي — حذف قبل التنشيط:
علامة الحذف تكتب ذيل «قيد التفعيل» وتحفظ الإدخال، ثم التنشيط المتأخر
يرقّي الذيل للمسار الحي (اضغط الايدي/الاسم — يعمل رغم الحذف)."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bot as bot_mod

PASS = 0
FAIL = 0


def check(name, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS: {name}")
    else:
        FAIL += 1
        print(f"  FAIL: {name} — {detail}")


class FakeBotClient:
    def __init__(self):
        self.edits = []
        self.connected = True

    def is_connected(self):
        return self.connected

    async def edit_message(self, target, alert_id, text, **kw):
        self.edits.append({'target': target, 'id': alert_id,
                           'text': text, 'kw': kw})


async def main():
    # نص تنبيه واقعي (بلا يوزرنيم): سطر مرسل بذكر tg://user?id + سطر الايدي
    alert_text = (
        '👤 <b>المرسل:</b> <a href="tg://user?id=8375413570">تعليم</a>\n'
        '🆔 <b>الايدي:</b> <a href="tg://user?id=8375413570">8375413570</a>\n'
        '👥 <b>المجموعة:</b> مبادئ الاقتصاد الجزئي\n'
        '💬 <b>نص الطلب:</b>\n<blockquote>عندي حد يحل لكم التكليف</blockquote>\n\n'
        '🔗 <a href="https://t.me/c/123/456">عرض الرسالة الأصلية</a>'
        f'\n💡 <i>{bot_mod._REQ_HINT_ACTIVATING}</i>'
    )
    entry = {
        'alert_id': 555,
        'target': -1003616135800,
        'text': alert_text,
        'buttons': [[type('B', (), {'url': '', 'data': b'cbc:1:2:3'})()]],  # صف أحمر بلا رابط t.me
        'ts': 0.0,
        'sender_meta': {'sender_id': 8375413570, 'sender_username': '',
                        'sender_name': 'تعليم', 'chat_id': -100123,
                        'chat_title': 'مبادئ الاقتصاد الجزئي',
                        'msg_id': 456, 'raw_text': 'عندي حد يحل لكم التكليف'},
        'relay': None,
    }

    mon = object.__new__(bot_mod.Monitor)  # بلا __init__
    mon.bot_client = FakeBotClient()
    mon._request_alerts = {(-100123, 456): entry}
    # لا _request_alerts_marked بعد — يجب أن يُنشأ تلقائيًا

    # (1) علامة الحذف — الحذف سبق التنشيط
    ok = await mon._mark_request_alert_deleted(-100123, 456)
    check("علامة الحذف نجحت", ok is True)
    marked_text = mon.bot_client.edits[0]['text']
    check("الذيل = قيد التفعيل (التنشيط جارٍ خلفيًا)",
          bot_mod._REQ_DELTAIL_PENDING in marked_text,
          f"text tail missing: ...{marked_text[-120:]}")
    check("الإدخال محفوظ في سجل المعلّمة",
          getattr(mon, '_request_alerts_marked', {}).get((-100123, 456)) is entry)
    check("سجل التنبيهات الحي أُفرغ (لا تكرار للعلامة)",
          (-100123, 456) not in mon._request_alerts)
    check("entry['text'] حُدّث للنص المعلّم", entry['text'] == marked_text)

    # (2) التنشيط المتأخر — نفس منطق _seed_username_upgrade_edit بلا يوزرنيم
    _old_a = str((mon._request_alerts_marked.get((-100123, 456)) or {}).get('text') or '')
    _new_a = _old_a.replace(bot_mod._REQ_HINT_ACTIVATING, bot_mod._REQ_HINT_ACTIVE)
    _new_a = _new_a.replace(bot_mod._REQ_DELTAIL_PENDING, bot_mod._REQ_DELTAIL_ACTIVE)
    check("التنشيط يبدّل التلميح قيد التفعيل → الحي",
          bot_mod._REQ_HINT_ACTIVATING not in _new_a and bot_mod._REQ_HINT_ACTIVE in _new_a)
    check("التنشيط يرقّي ذيل الحذف للمسار الحي",
          bot_mod._REQ_DELTAIL_PENDING not in _new_a and bot_mod._REQ_DELTAIL_ACTIVE in _new_a)
    check("رابط الايدي ما زال في النص (يصير حيًا بإعادة التقييم)",
          'tg://user?id=8375413570' in _new_a)

    # (3) حالة: التنشيط اكتمل قبل الحذف → الذيل الحي مباشرة
    entry2 = dict(entry)
    entry2['text'] = alert_text.replace(bot_mod._REQ_HINT_ACTIVATING, bot_mod._REQ_HINT_ACTIVE)
    entry2['buttons'] = None
    mon2 = object.__new__(bot_mod.Monitor)
    mon2.bot_client = FakeBotClient()
    mon2._request_alerts = {(-100999, 777): entry2}
    ok2 = await mon2._mark_request_alert_deleted(-100999, 777)
    mt2 = mon2.bot_client.edits[0]['text']
    check("تنشيط سابق → الذيل الحي مباشرة (اضغط الايدي/الاسم)",
          ok2 is True and bot_mod._REQ_DELTAIL_ACTIVE in mt2 and
          bot_mod._REQ_DELTAIL_PENDING not in mt2)

    # (4) حالة: بلا مسار إطلاقًا (مرسل قناة/مجهول — لا روابط tg://user)
    # → النص الصادق. ملاحظة: وجود رابط tg://user?id في النص يعني أن مهمة
    # تنشيط (بذرة/ENTITY-RESCUE) موجودة في المسار الحقيقي → الذيل
    # «قيد التفعيل» هو الصادق هناك (حالة 1 أعلاه).
    entry3 = dict(entry)
    entry3['text'] = (
        '👤 <b>المرسل:</b> قناة رسمية\n'
        '👥 <b>المجموعة:</b> مبادئ الاقتصاد الجزئي\n'
        '💬 <b>نص الطلب:</b>\n<blockquote>نص</blockquote>\n\n'
        '🔗 <a href="https://t.me/c/123/456">عرض الرسالة الأصلية</a>'
    )
    entry3['buttons'] = None
    entry3['sender_meta'] = None
    mon3 = object.__new__(bot_mod.Monitor)
    mon3.bot_client = FakeBotClient()
    mon3._request_alerts = {(-100777, 888): entry3}
    ok3 = await mon3._mark_request_alert_deleted(-100777, 888)
    mt3 = mon3.bot_client.edits[0]['text']
    check("مرسل مجهول بلا مسار → النص الصادق (لا وعود ميتة)",
          ok3 is True and 'لم يبقَ مسار تواصل تلقائي' in mt3)

    print()
    print("=" * 64)
    print(f"DELETE-MARK-ID-ACTIVATE RESULTS: {PASS}/{PASS + FAIL}")
    print("=" * 64)
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
