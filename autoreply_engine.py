#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AutoReplyEngine v1.0 — حسابات الرد التلقائي (الرد الأولي السريع)
[FEAT v4.4.26 AUTOREPLY-ACCOUNT] أمر المُشغّل 2026-11-20:

«الرد التلقائي هذا طبعاً بيكون اكثر من حساب اي طلب يرد عليه تلقائياً
برد مناسب ... الفكرة فقط السرعه في الرد ... فادخل عليه انا واتحاور
معاه لان البوت لايستطيع ان يتكلم مع الزبون بالسعر والتفاصيل»
(توضيح المُشغّل: نعمل كفريق — البوت يرسل الرسالة الأولى فقط، والعميل
يدخل المحادثة فيتكلم معه أحدنا نحن بالسعر والتفاصيل.)

المعمارية:
- حسابات مخصصة role='autoreply' (تُضاف من «🔐 تسجيل الدخول» → الزر
  الثالث «💬 حساب رد تلقائي») ترسل التحية الأولى لصاحب الطلب المكتشف
  فور نشر تنبيه الطلب في قناة الطلبات — السرعة هي كامل الفكرة.
- بعد التحية يتولى الفريق البشري المحادثة من نفس الحساب يدويًا.
- توزيع round-robin بين حسابات الرد المتاحة (كلها ضمن حدود تيليجرام).

حدود الأمان (منع السبام — التزام صريح بحدود تيليجرام):
- سقف يومي/حساب: AUTOREPLY_DAILY_CAP (افتراضي 25 رسالة/يوم)
- فاصل أدنى بين إرسالين لنفس الحساب: AUTOREPLY_MIN_INTERVAL_S
  (افتراضي 240ث + عشوائية 0-60ث)
- تبريد لكل عميل: AUTOREPLY_CONTACT_COOLDOWN_H (افتراضي 24 ساعة) —
  لا تُرسل تحية ثانية لنفس العميل خلالها أبدًا.
- FloodWaitError: نحترم المدة التي يطلبها تيليجرام نفسه (الحساب ينتظرها
  كاملة) ويُجرَّب الحساب التالي المتاح فقط.
- لا يوجد هنا أي منطق «التفاف على حظر» أو تبديل حساب بسبب حظر —
  الحسابات تعمل كلها ضمن الحدود، والحظر الأصلي مسؤولية المُشغّل.
- نص التحية تحية صادقة بلا ادعاءات — بلا نقاط/فواصل (نمط المُشغّل)
  لأن الفريق البشري يكمل بعدها مباشرة.

الدمج: يُستدعى monitor.autoreply.greet(...) كـ fire-and-forget بعد نجاح
إرسال تنبيه الطلب (bot.py مسار REQUEST-PATH) — فشله غير قاتل إطلاقًا.
"""

import asyncio
import html as _htmlmod
import logging
import os
import random
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, '')).strip() or default)
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, '')).strip().lower()
    if not raw:
        return default
    return raw in ('1', 'true', 'yes', 'on', 'y')


class AutoReplyEngine:
    """محرك الرد الأولي — تحية سريعة واحدة لكل عميل من حسابات مخصصة.

    كل الطرق غير قاتلة: أي فشل يُسجل ويُتخطى (التنبيه الأصلي في قناة
    الطلبات وزر 🔴 التواصل اليدوي يبقيان المسار الاحتياطي الدائم).
    """

    def __init__(self, monitor):
        # مرجع خفيف للـ Monitor (db + user_clients) — لا نملكه
        self._m = monitor
        # --- إعدادات (env-overridable) ---
        self.enabled = _env_bool('AUTOREPLY_ENABLED', True)
        self.daily_cap = max(1, _env_int('AUTOREPLY_DAILY_CAP', 25))
        self.min_interval_s = max(30, _env_int('AUTOREPLY_MIN_INTERVAL_S', 240))
        self.contact_cooldown_h = max(1, _env_int('AUTOREPLY_CONTACT_COOLDOWN_H', 24))
        # --- حالة الحسابات ---
        # phone → عدد المرسل اليوم (يُصفَّر مع اليوم الجديد)
        self._sent_today: Dict[str, int] = {}
        # phone → أول وقت مسموح للإرسال التالي (فاصل/فيض)
        self._next_allowed: Dict[str, datetime] = {}
        # phone → آخر إرسال ناجح (للحالة)
        self._last_send: Dict[str, datetime] = {}
        # --- تبريد العملاء ---
        # sender_id → وقت آخر تحية (bounded — يُقلَّم)
        self._contacted: Dict[int, datetime] = {}
        # round-robin
        self._rr = 0
        # [AUTOREPLY-READY v4.5.16] آخر تذكير للمُشغّل بغياب الحسابات
        self._noacc_last_remind: Optional[datetime] = None
        # يوم العدادات الحالي (YYYY-MM-DD)
        self._day = datetime.now().strftime('%Y-%m-%d')
        # --- إحصاءات (ذاكرة فقط — تُقرأ من /autoreply_status) ---
        self.stats = {
            'sent_total': 0,
            'skip_dedup': 0,
            'skip_no_accounts': 0,
            'skip_disabled': 0,
            'skip_entity': 0,
            'skip_privacy': 0,
            'skip_cap': 0,
            'flood_events': 0,
            'send_errors': 0,
        }

    # ------------------------------------------------------------------
    # ردود التحية — لهجة خليجية بلا علامات ترقيم (نمط المُشغّل).
    # كل رد: تحية + إشارة لطلب العميل + طلب التفاصيل — وهذا حرفيًا ما
    # يكتبه عضو الفريق كأول رد. الملف كله «الرد الأول فقط».
    # ------------------------------------------------------------------

    _GREET_GENERIC = [
        "السلام عليكم ورحمة الله حياك الله شفنا طلبك ونقدر نساعدك فيه ابشر اكتب لنا وش تحتاج بالضبط",
        "السلام عليكم هلا والله شفنا طلبك ونتقدر نخدمك ابشر بما تحتاجه بالتفصيل",
        "وعليكم السلام حياك الله نقدر نساعدك باللي تحتاجه ابشر اكتب التفاصيل",
        "السلام عليكم حياك الله وصلنا طلبك نقدر نسويه لك باحتراف ابشر وش المووضوع",
    ]
    _GREET_BY_TOPIC = [
        # (كلمات مفتاحية، ردود)
        (('بحث', 'بحوث', 'مذكره', 'رساله ماجستير', 'مقترح بحث'),
         ("السلام عليكم شفنا طلبك عن البحث نقدر نجهزه لك باحتراف ابشر كم صفحة وش المووضوع",
          "السلام عليكم حياك الله نقدر نساعدك بالبحث كامل او اي جزء منه ابشر بالتفاصيل")),
        (('بروجكتر', 'بروجكت', 'مشروع', 'بروزكت', 'project'),
         ("السلام عليكم شفنا طلبك عن البروجكتر نقدر نسويه لك كامل ابشر وش المطلوب بالضبط",
          "السلام عليكم حياك الله نقدر نجهز لك المشروع باحتراف ابشر بالتفاصيل")),
        (('تقرير', 'تقارير', 'report'),
         ("السلام عليكم شفنا طلبك عن التقرير نقدر نجهزه لك ابشر عن ايش المطلوب",
          "السلام عليكم حياك الله نقدر نسوي لك التقارير باحتراف ابشر بالتفاصيل")),
        (('واجب', 'واجبات', 'حل واجب', 'homework'),
         ("السلام عليكم شفنا طلبك عن الواجب نقدر حله لك ابشر عن ايش",
          "السلام عليكم حياك الله نقدر نخدمك بالواجبات ابشر وش المواد")),
        (('برزنتيشن', 'عرض تقديمي', 'بريزنتيشن', 'presentation', 'بوور بوينت', 'باور بوينت'),
         ("السلام عليكم شفنا طلبك عن البرزنتيشن نقدر نجهزه لك مع التصميم ابشر عن ايش",
          "السلام عليكم حياك الله نقدر نسوي لك العرض التقديمي كامل ابشر بالتفاصيل")),
        (('اختبار', 'اختبارات', 'كويز', 'ميدترم', 'فاينل', 'exam'),
         ("السلام عليكم شفنا طلبك عن الاختبار نقدر نساعدك ابشر عن ايش المادة",
          "السلام عليكم حياك الله نقدر نخدمك بالاختبارات ابشر بالتفاصيل")),
        (('تحليل', 'احصاء', 'spss', 'احصائي'),
         ("السلام عليكم شفنا طلبك عن التحليل نقدر نطلعه لك ابشر بالتفاصيل",
          "السلام عليكم حياك الله نقدر نسوي لك التحليل الاحصائي باحتراف ابشر وش البيانات")),
        (('ترجمه', 'ترجمة', 'translate'),
         ("السلام عليكم شفنا طلبك عن الترجمة نقدر نترجم لك باحتراف ابشر عن ايش",
          "السلام عليكم حياك الله نقدر نساعدك بالترجمة ابشر بلغة والكمية")),
    ]

    def pick_greeting(self, raw_text: str) -> str:
        """اختيار الرد حسب موضوع الطلب؛ وإلا فدورة على الردود العامة."""
        try:
            t = str(raw_text or '').lower()
            for keywords, replies in self._GREET_BY_TOPIC:
                if any(k in t for k in keywords):
                    return random.choice(replies)
            self._rr += 1
            return self._GREET_GENERIC[self._rr % len(self._GREET_GENERIC)]
        except Exception:
            return self._GREET_GENERIC[0]

    # ------------------------------------------------------------------
    # الحسابات
    # ------------------------------------------------------------------

    async def _autoreply_accounts(self) -> List[Dict]:
        """حسابات role='autoreply' المتصلة فعليًا (لديها client حي)."""
        out: List[Dict] = []
        try:
            accounts = await self._m.db.get_watchers_by_role('autoreply')
        except Exception as e:
            logger.debug(f"[AUTOREPLY] list accounts failed: {e}")
            return out
        clients = getattr(self._m, 'user_clients', None) or {}
        for acc in accounts or []:
            phone = str(acc.get('phone') or '')
            client = clients.get(phone)
            if phone and client is not None:
                try:
                    if client.is_connected():
                        out.append({'phone': phone, 'client': client})
                except Exception:
                    pass
        return out

    def _rollover_day_if_needed(self):
        _today = datetime.now().strftime('%Y-%m-%d')
        if _today != self._day:
            self._day = _today
            self._sent_today.clear()

    def _account_available(self, phone: str, now: datetime) -> bool:
        if self._sent_today.get(phone, 0) >= self.daily_cap:
            return False
        nxt = self._next_allowed.get(phone)
        if nxt and now < nxt:
            return False
        return True

    def _pick_account(self, accounts: List[Dict]) -> Optional[Dict]:
        """round-robin بين الحسابات المتاحة (داخل الحدود)."""
        now = datetime.now()
        avail = [a for a in accounts if self._account_available(a['phone'], now)]
        if not avail:
            return None
        self._rr += 1
        return avail[self._rr % len(avail)]

    def _prune_contacted(self):
        """إبقاء ذاكرة تبريد العملاء محدودة (أسبوع)."""
        if len(self._contacted) > 5000:
            cutoff = datetime.now() - timedelta(days=7)
            for sid in [s for s, t in self._contacted.items() if t < cutoff]:
                self._contacted.pop(sid, None)

    # ------------------------------------------------------------------
    # زرع الكيان — كي يستطيع حساب الرد مخاطبة العميل مباشرة حتى لو لم
    # يكن يشاركه مجموعة: نسخ الكيان (مع access hash) من جلسة حساب
    # الالتقاط إلى جلسة حساب الرد. (نمط Telethon القياسي، بلا رسائل.)
    # ------------------------------------------------------------------

    async def _resolve_entity(self, ar_client, sender_id: int, source_phone: str):
        try:
            ent = await asyncio.wait_for(
                ar_client.get_entity(sender_id), timeout=6)
            if ent is not None:
                return ent
        except Exception:
            pass
        # زرع من حساب الالتقاط (رأى المرسل — الـaccess hash في جلسته)
        try:
            clients = getattr(self._m, 'user_clients', None) or {}
            src = clients.get(str(source_phone or ''))
            if src is None:
                for c in clients.values():
                    try:
                        if c is not None and c.is_connected():
                            src = c
                            break
                    except Exception:
                        continue
            if src is None:
                return None
            ent = await asyncio.wait_for(src.get_entity(sender_id), timeout=8)
            if ent is None:
                return None
            sess = getattr(ar_client, 'session', None)
            if sess is not None and hasattr(sess, 'process_entities'):
                sess.process_entities(ent)
            ent2 = await asyncio.wait_for(
                ar_client.get_entity(sender_id), timeout=6)
            return ent2
        except Exception as e:
            logger.debug(f"[AUTOREPLY] entity plant failed uid={sender_id}: {e}")
            return None

    # ------------------------------------------------------------------
    # النقطة الرئيسية — تُستدعى بعد نشر تنبيه الطلب (fire-and-forget)
    # ------------------------------------------------------------------

    async def greet(self, sender_id: int, sender_display: str,
                    username: str, chat_id: int, msg_id: int,
                    raw_text: str, source_phone: str = '') -> bool:
        """يرسل التحية الأولى لصاحب الطلب من حساب رد متاح.

        غير قاتلة تمامًا: أي فشل يُسجل ويُرجع False — تنبيه القناة
        وزر 🔴 التواصل اليدوي يبقيان المسار الاحتياطي.
        """
        try:
            if not self.enabled:
                self.stats['skip_disabled'] += 1
                return False
            sid = int(sender_id or 0)
            if sid <= 0:
                return False  # مرسل مجهول/قناة — لا وجهة

            # تبريد العميل: تحية واحدة فقط لكل 24 ساعة
            self._prune_contacted()
            last = self._contacted.get(sid)
            if last and datetime.now() - last < timedelta(hours=self.contact_cooldown_h):
                self.stats['skip_dedup'] += 1
                return False

            self._rollover_day_if_needed()
            accounts = await self._autoreply_accounts()
            if not accounts:
                self.stats['skip_no_accounts'] += 1
                logger.info(
                    "[AUTOREPLY] no connected autoreply accounts — skipping "
                    f"uid={sid} (manual 🔴 button remains)")
                # [AUTOREPLY-READY v4.5.16] تذكير مُخنَّق للمُشغّل —
                # نظام صامت = نظام ميت: المُشغّل يعرف أن التحيات
                # لا تُرسل ومَنْ يضيف الحساب. مرة كل 6 ساعات كحد أقصى.
                await self._remind_owner_no_accounts()
                return False

            text = self.pick_greeting(raw_text)

            # محاولتان كحد أقصى (سرعة + بساطة): الحساب المختار ثم التالي
            tried: List[str] = []
            for _ in range(2):
                acc = self._pick_account(
                    [a for a in accounts if a['phone'] not in tried])
                if acc is None:
                    self.stats['skip_cap'] += 1
                    return False
                phone, client = acc['phone'], acc['client']
                tried.append(phone)
                # [FLOOD-SAFE v4.5] احترام سجل التقييد المشترك (DB) من كل
                # المسارات (انضمام/رد/زر أحمر) — حساب مقيّد لا يُجرَّب أصلًا:
                try:
                    _fwm = getattr(self._m, 'floodwait_mgr', None)
                    if _fwm is not None:
                        _bl, _rem = await _fwm.is_blocked(phone)
                        if _bl:
                            logger.info(
                                f"[AUTOREPLY] phone={phone} floodwait-blocked "
                                f"({_rem}s left) — skipped, no attempt")
                            continue
                except Exception:
                    pass
                try:
                    ent = await self._resolve_entity(client, sid, source_phone)
                    if ent is None:
                        self.stats['skip_entity'] += 1
                        continue  # جرّب الحساب التالي (قد يشاركه المجموعة)

                    await asyncio.wait_for(
                        client.send_message(ent, text), timeout=12)

                    # نجح ✅ — حدّث الحالة
                    now = datetime.now()
                    self._sent_today[phone] = self._sent_today.get(phone, 0) + 1
                    self._next_allowed[phone] = now + timedelta(
                        seconds=self.min_interval_s + random.randint(0, 60))
                    self._last_send[phone] = now
                    self._contacted[sid] = now
                    self.stats['sent_total'] += 1
                    logger.info(
                        f"[AUTOREPLY] ✅ greeting sent uid={sid} "
                        f"from={phone} chat={chat_id} msg={msg_id} "
                        f"today={self._sent_today[phone]}/{self.daily_cap}")
                    return True

                except Exception as e:
                    ename = type(e).__name__
                    estr = str(e) or ''
                    if 'FloodWait' in ename or 'FLOOD_WAIT' in estr.upper():
                        try:
                            wait_s = int(getattr(e, 'seconds', 0) or 60)
                        except Exception:
                            wait_s = 60
                        # [FLOOD-SAFE v4.5] احترام المدة كاملة +30ث هامش —
                        # لا تقصير أبدًا. التسجيل في floodwait_mgr (DB)
                        # يجعل كل المسارات (انضمام/رد/زر أحمر) ترفض الحساب
                        # فورًا حتى انتهاء المدة — البوت لا «يضل يحاول».
                        self._next_allowed[phone] = (
                            datetime.now() + timedelta(seconds=wait_s + 30))
                        try:
                            _fwm = getattr(self._m, 'floodwait_mgr', None)
                            if _fwm is not None:
                                await _fwm.block(phone, wait_s + 30)
                        except Exception:
                            pass
                        self.stats['flood_events'] += 1
                        logger.warning(
                            f"[AUTOREPLY] FloodWait {wait_s}s on {phone} — "
                            f"full duration registered in DB, account paused "
                            f"(no retry on this account)")
                        await self._notify_team_flood(
                            phone, wait_s + 30, f"FloodWait {wait_s} ثانية")
                        continue
                    if 'PeerFlood' in ename or 'PEER_FLOOD' in estr.upper():
                        # [FLOOD-SAFE v4.5] إجهاد حقيقي من تلغرام — الحساب
                        # يرتاح 24 ساعة كاملة + تسجيل DB لكل المسارات.
                        self._next_allowed[phone] = (
                            datetime.now() + timedelta(hours=24))
                        try:
                            _fwm = getattr(self._m, 'floodwait_mgr', None)
                            if _fwm is not None:
                                await _fwm.block(phone, 86400)
                        except Exception:
                            pass
                        self.stats['flood_events'] += 1
                        logger.warning(
                            f"[AUTOREPLY] PeerFlood on {phone} — paused 24h "
                            f"(حد تلغرام — الحساب يرتاح، لا إعادة محاولة)")
                        await self._notify_team_flood(
                            phone, 86400, "PeerFlood — 24 ساعة كاملة")
                        continue
                    if 'UserPrivacyRestricted' in ename or 'privacy' in estr.lower():
                        self.stats['skip_privacy'] += 1
                        logger.info(
                            f"[AUTOREPLY] uid={sid} PM restricted — skipped")
                        return False
                    self.stats['send_errors'] += 1
                    logger.warning(
                        f"[AUTOREPLY] send failed via {phone}: {ename}: {estr}")
                    continue

            return False
        except Exception as e:
            logger.warning(f"[AUTOREPLY] greet error (non-fatal): {e}")
            return False

    # ------------------------------------------------------------------
    # [AUTOREPLY-READY v4.5.16] تذكير المُشغّل بغياب حسابات الرد
    #
    # حين تصل طلبات جديدة ولا يوجد أي حساب رد متصل، يظل النظام صامتًا
    # في السجلات فقط (skip_no_accounts) — المُشغّل لا يقرأ السجلات.
    # هذا التذكير يصل خاص المالك مرة كل AUTOREPLY_NOACC_REMIND_H ساعة
    # (افتراضي 6، و 0 = تعطيل) بخطوات الإضافة الحرفية. غير قاتل.
    # ------------------------------------------------------------------

    async def _remind_owner_no_accounts(self):
        try:
            hours = max(0, _env_int('AUTOREPLY_NOACC_REMIND_H', 6))
            if hours <= 0:
                return
            now = datetime.now()
            last = self._noacc_last_remind
            if last and (now - last) < timedelta(hours=hours):
                return
            self._noacc_last_remind = now
            _m = self._m
            _bc = getattr(_m, 'bot_client', None)
            oid = getattr(getattr(_m, 'config', None), 'owner_id', None)
            if (not _bc or not _bc.is_connected() or not oid):
                return
            await _bc.send_message(
                int(oid),
                "⚠️ الرد التلقائي متوقف — لا يوجد حساب رد متصل\n\n"
                "وصلت طلبات جديدة الآن ولا يوجد حساب يرسل التحية الأولى "
                "(زر 🔴 التواصل اليدوي يبقى شغالًا في قناة الطلبات).\n\n"
                "➕ إضافة حساب رد (مرة واحدة لكل حساب):\n"
                "1. أرسل /login للبوت\n"
                "2. اختر «💬 حساب رد تلقائي (Auto-Reply)»\n"
                "3. أرسل رقم الحساب بالصيغة الدولية (+966...)\n"
                "4. أرسل كود تيليجرام الذي يصله الحساب\n\n"
                "بعد الاتصال يبدأ الرد فورًا — تحية أولى لكل طلب "
                "جديد خلال ثوانٍ، وحدود الأمان: 25 تحية/يوم للحساب، "
                "فاصل ≥ 4 دقائق، وتحية واحدة لكل عميل في 24 ساعة.\n\n"
                "هذا التذكير مرة كل 6 ساعات فقط.",
                link_preview=False)
            logger.info(
                "[AUTOREPLY] owner reminded: no autoreply accounts "
                f"connected (next remind ≥ {hours}h)")
        except Exception as e:
            logger.debug(f"[AUTOREPLY] no-acc remind failed: {e}")

    # ------------------------------------------------------------------
    # [FLOOD-SAFE v4.5] تنبيه فوري للفريق في قناة الطلبات عند تقييد حساب
    # ------------------------------------------------------------------
    async def _notify_team_flood(self, phone: str, wait_s: int, kind: str):
        try:
            _m = self._m
            _bc = getattr(_m, 'bot_client', None)
            target = getattr(_m, 'requests_target_channel', 0)
            if (not _bc or not _bc.is_connected() or not target):
                return
            _until = datetime.now() + timedelta(seconds=wait_s)
            await _bc.send_message(
                target,
                "⚠️ <b>حساب رد تلقائي مقيّد مؤقتًا</b>\n"
                f"📱 الرقم: <code>{_htmlmod.escape(str(phone))}</code>\n"
                f"⏳ السبب: <b>{_htmlmod.escape(kind)}</b>\n"
                f"🕒 متاح مجددًا تلقائيًا ~ {_until.strftime('%H:%M')}\n\n"
                "🛡️ أُوقف الحساب كليًا حتى نهاية المدة — لن تُرسل منه أي "
                "رسائل ولن تُعاد المحاولة. طلبات جديدة ستُرَدّ من حساب "
                "رد آخر متاح، وزر 🔴 المحادثة يبقى للفريق.",
                parse_mode='html', link_preview=False)
        except Exception as e:
            logger.debug(f"[AUTOREPLY] flood notify failed: {e}")

    # ------------------------------------------------------------------
    # تقرير الحالة — /autoreply_status
    # ------------------------------------------------------------------

    async def status_report(self) -> str:
        self._rollover_day_if_needed()
        lines = ["💬 حسابات الرد التلقائي (Auto-Reply)", ""]
        try:
            accounts = await self._m.db.get_watchers_by_role('autoreply')
        except Exception:
            accounts = []
        clients = getattr(self._m, 'user_clients', None) or {}
        if not accounts:
            lines.append("ℹ️ لا توجد حسابات رد تلقائي مسجلة.")
            lines.append("➕ أضفها: اكتب Bot ← 🔐 تسجيل الدخول ← 💬 حساب رد تلقائي")
        else:
            lines.append(f"👥 المسجلة: {len(accounts)}")
            for acc in accounts:
                p = str(acc.get('phone') or '?')
                c = clients.get(p)
                connected = False
                try:
                    connected = bool(c is not None and c.is_connected())
                except Exception:
                    pass
                sent = self._sent_today.get(p, 0)
                nxt = self._next_allowed.get(p)
                wait = ''
                if nxt and datetime.now() < nxt:
                    wait = f" ⏳ ينتظر {int((nxt - datetime.now()).total_seconds())}ث"
                lines.append(
                    f"  • {p}: {'🟢 متصل' if connected else '🔴 غير متصل'} | "
                    f"اليوم {sent}/{self.daily_cap}{wait}")
        s = self.stats
        lines += [
            "",
            f"📨 مرسل اليوم (كل الحسابات): {sum(self._sent_today.values())}",
            f"📊 الإجمالي منذ الإقلاع: {s['sent_total']}",
            f"⏭️ تخطي: تكرار {s['skip_dedup']} | بلا حسابات {s['skip_no_accounts']} "
            f"| سقف {s['skip_cap']} | كيان {s['skip_entity']} "
            f"| خصوصية {s['skip_privacy']}",
            f"⚠️ فيض (احترمناه): {s['flood_events']} | أخطاء إرسال: {s['send_errors']}",
            f"⚙️ enabled={self.enabled} | سقف/يوم={self.daily_cap} | "
            f"فاصل≥{self.min_interval_s}ث | تبريد العميل={self.contact_cooldown_h}س",
            "",
            "🎯 مهمة الحساب: التحية الأولى فقط — بعدها يكمل الفريق البشري",
        ]
        return "\n".join(lines)
