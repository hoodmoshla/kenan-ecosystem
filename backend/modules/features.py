"""Kenan appendix modules — independent, testable, honestly labelled.

Shield: returns check levels, never "safe" without data.
  levels: ok / warning / blocked / not_checked
Intelligence: rule-based Arabic explainer. No LLM call, no keys. It rejects
  any payload containing private-key/seed fields.
Circles: payment requests + threshold approvals + per-member spending caps.
Pay: merchant invoices with a shareable payload; receipt hashes are recorded
  as unverified claims and remain pending until a node-verification flow exists.
Recovery: guardian-threshold proposal with a timelock. This is a DESIGN +
  proposal state machine only: it never holds or reconstructs anyone's key,
  and it cannot bypass a wallet's own backup.
Academy: VIRTUAL demo accounts, labelled on every record.
Insights: caller-supplied holdings distribution + fee totals. Price/PnL are
  UNAVAILABLE unless a price provider with source+timestamp is supplied.
Governance: off-chain signalling votes weighted by chain balance snapshot.
  Protocol-level governance requires the Substrate runtime (pending).
Bridge: DISABLED by design — bridge_transfer() always refuses until an
  independent security audit is recorded. No real cross-chain movement.
"""
from __future__ import annotations

import hashlib
import time

FORBIDDEN_KEY_FIELDS = {"private_key", "seed", "mnemonic", "secret"}


# ---------- Shield ----------
def shield_check(tx: dict, known_bad: set | None = None, trusted: set | None = None) -> dict:
    known_bad, trusted = known_bad or set(), trusted or set()
    warnings, level = [], "not_checked"
    to, amount, fee = tx.get("to", ""), int(tx.get("amount", 0)), int(tx.get("fee", 0))
    if not to.startswith("kenan1") or len(to) != 46:
        return {"level": "blocked", "warnings": ["invalid recipient address format"], "checked_at": int(time.time())}
    if to in known_bad:
        return {"level": "blocked", "warnings": ["recipient is on a known-risk list"], "checked_at": int(time.time())}
    if amount > 0 and fee > amount * 0.1:
        warnings.append("fee exceeds 10% of the amount — unusually high")
    if to not in trusted:
        warnings.append("recipient is not in your trusted list — double-check the address")
    level = "warning" if warnings else "ok"
    return {"level": level, "warnings": warnings,
            "note": "simulation is not a guarantee of final-transaction safety",
            "checked_at": int(time.time())}


# ---------- Intelligence (rule-based, no LLM, no keys) ----------
def explain_balance(balance_ken: float, tx_count: int) -> str:
    if tx_count == 0:
        return "رصيدك الحالي هو %.4f KEN (تجريبي) ولم تسجَّل لك معاملات بعد." % balance_ken
    return "رصيدك الحالي هو %.4f KEN (تجريبي) عبر %d معاملة مسجلة على الشبكة التجريبية." % (balance_ken, tx_count)


def explain_fee(amount_base: int, fee_base: int) -> str:
    if amount_base <= 0:
        return "لا يمكن حساب نسبة الرسوم لمبلغ غير موجب."
    return "الرسوم %.4f%% من المبلغ: رسوم الشبكة ثابتة بحد أدنى، ويُحرق نصفها ويُحوَّل النصف الآخر لمجمّع الرسوم." % (fee_base / amount_base * 100)


def draft_transfer(to: str, amount_ken: float) -> dict:
    """Unsigned DRAFT only. The user must review and sign locally; this
    function never signs and never sends anything."""
    if not to.startswith("kenan1"):
        raise ValueError("invalid recipient address")
    return {"draft": True, "signed": False, "to": to, "amount_ken": amount_ken,
            "warning": "مسودة غير موقعة — راجع المستلم والمبلغ والرسوم قبل التوقيع المحلي"}


def intelligence_answer(question: str, context: dict) -> dict:
    q = question.lower()
    for k in FORBIDDEN_KEY_FIELDS:
        if k in context:
            return {"error": "refused: private key material must never be sent to the assistant"}
    if "رسوم" in question or "fee" in q:
        return {"answer": explain_fee(context.get("amount_base", 0), context.get("fee_base", 0)),
                "source": "chain fee rule (docs/TOKENOMICS_DRAFT.md)", "is_estimate": False}
    if "رصيد" in question or "balance" in q:
        return {"answer": explain_balance(context.get("balance_ken", 0), context.get("tx_count", 0)),
                "source": "chain state via node RPC", "is_estimate": False}
    return {"answer": "المساعد القاعدي يشرح الأرصدة والرسوم والأخطاء فقط. لمزود نماذج لغوية خارجي، "
                      "يجب ربطه عبر إعدادات الخصوصية، وهو غير متصل حالياً — ووظائف المحفظة الأساسية تعمل بدونه.",
            "source": "local rule-based explainer", "is_estimate": False}


# ---------- Circles ----------
class Circle:
    def __init__(self, circle_id, name, members, threshold=2):
        self.id, self.name = circle_id, name
        self.members = set(members)
        self.threshold = threshold
        self.requests, self.caps = {}, {}
        self._seq = 0

    def set_cap(self, member, cap_ken):
        if member not in self.members:
            raise ValueError("not a circle member")
        self.caps[member] = cap_ken

    def request_payment(self, requester, to, amount_ken, note=""):
        if requester not in self.members:
            raise ValueError("not a circle member")
        if amount_ken > self.caps.get(requester, float("inf")):
            raise ValueError("amount exceeds member spending cap")
        self._seq += 1
        self.requests[self._seq] = {"id": self._seq, "requester": requester, "to": to,
                                    "amount_ken": amount_ken, "note": note,
                                    "approvals": set(), "status": "pending"}
        return self.requests[self._seq]

    def approve(self, req_id, member):
        r = self.requests[req_id]
        if member not in self.members or member == r["requester"]:
            raise ValueError("approver must be a different circle member")
        r["approvals"].add(member)
        if len(r["approvals"]) >= self.threshold:
            r["status"] = "approved"
        return r


# ---------- Pay ----------
def create_invoice(merchant, amount_ken, note=""):
    raw = f"{merchant}|{amount_ken}|{note}|{int(time.time())}"
    inv_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return {"invoice_id": inv_id, "merchant": merchant, "amount_ken": amount_ken,
            "note": note, "pay_payload": f"kenan://pay?invoice={inv_id}&amount={amount_ken}",
            "status": "unpaid"}


def receipt_for(invoice_id, tx_hash):
    if not tx_hash:
        raise ValueError("a receipt requires a transaction hash claim")
    return {"invoice_id": invoice_id, "tx_hash": tx_hash, "status": "pending-verification",
            "verified": False,
            "note": "hash not checked against the node; confirm in Kenan Explorer before treating payment as final"}


# ---------- Recovery (proposal state machine, no key custody) ----------
class RecoveryProposal:
    def __init__(self, wallet_address, guardians, threshold, timelock_seconds=72 * 3600):
        self.wallet_address, self.guardians = wallet_address, set(guardians)
        self.threshold, self.timelock = threshold, timelock_seconds
        self.approvals, self.created_at, self.status = set(), int(time.time()), "pending"

    def approve(self, guardian):
        if guardian not in self.guardians:
            raise ValueError("not a guardian")
        self.approvals.add(guardian)
        if len(self.approvals) >= self.threshold:
            self.status = "approved-waiting-timelock"
        return self.status

    def executable(self, now=None):
        now = now or int(time.time())
        return self.status == "approved-waiting-timelock" and now - self.created_at >= self.timelock


# ---------- Academy / Insights / Governance / Bridge ----------
def academy_account(user):
    return {"user": user, "account_type": "VIRTUAL-DEMO",
            "balances": {"KEN-DEMO": 10000.0, "USD-DEMO": 10000.0},
            "warning": "virtual balances only — never real money"}


def insights(holdings: dict, fees_base: int, price_provider=None):
    total = sum(holdings.values())
    dist = {k: (round(v / total * 100, 2) if total else 0) for k, v in holdings.items()}
    out = {"holdings": holdings, "distribution_pct": dist, "fees_base": fees_base,
           "source": "caller-provided demo data (not independently verified)", "timestamp": int(time.time())}
    if price_provider is None:
        out["pnl"] = None
        out["pnl_note"] = "unavailable: no price source configured (source+timestamp required)"
    else:
        out["pnl"] = price_provider(holdings)
    return out


class Governance:
    def __init__(self):
        self.proposals, self._seq = {}, 0

    def propose(self, title, description):
        self._seq += 1
        self.proposals[self._seq] = {"id": self._seq, "title": title, "description": description,
                                     "votes_for": 0, "votes_against": 0, "voters": set(),
                                     "binding": False,
                                     "note": "off-chain signalling only until protocol governance lands"}
        return self.proposals[self._seq]

    def vote(self, pid, voter, weight, support):
        p = self.proposals[pid]
        if voter in p["voters"]:
            raise ValueError("already voted")
        p["voters"].add(voter)
        if support:
            p["votes_for"] += weight
        else:
            p["votes_against"] += weight
        return p


def bridge_transfer(*args, **kwargs):
    raise RuntimeError("BRIDGE DISABLED: no real cross-chain transfer until an "
                       "independent security audit is recorded (docs/STATUS.md).")
