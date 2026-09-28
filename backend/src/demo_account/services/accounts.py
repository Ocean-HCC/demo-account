"""账户管理（方案 3.1、5.1；实现 5.1）。"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from ..clock import Clock
from ..config import Settings
from ..core.models import AccountStatus, EventType, FeeParams, OrderStatus
from ..store import repos
from ..store.db import Database, Tx
from ..store.records import Account
from .errors import ServiceError
from .events import EventService
from .execution import Execution
from .ids import new_account_id


class AccountService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        events: EventService,
        execution: Execution,
        settings: Settings,
    ) -> None:
        self.db = db
        self.clock = clock
        self.events = events
        self.execution = execution
        self.settings = settings

    def _load(self, tx: Tx | None, account_id: str) -> Account:
        a = repos.get_account(tx if tx is not None else self.db.read(), account_id)
        if a is None:
            raise ServiceError("ACCOUNT_NOT_FOUND", f"账户不存在: {account_id}", 404)
        return a

    def get(self, account_id: str) -> Account:
        return self._load(None, account_id)

    def list(self, include_archived: bool = False) -> list[Account]:
        return repos.list_accounts(self.db.read(), include_archived)

    def create(
        self,
        name: str,
        note: str = "",
        initial_cash: Decimal | None = None,
        commission_rate: Decimal | None = None,
        min_commission: Decimal | None = None,
        slippage_rate: Decimal | None = None,
        block_st: bool | None = None,
    ) -> Account:
        s = self.settings
        name = name.strip()
        if not name:
            raise ServiceError("INVALID_REQUEST", "账户名称不能为空")
        cash = initial_cash if initial_cash is not None else s.default_initial_cash
        if cash < s.min_initial_cash:
            raise ServiceError("INVALID_REQUEST", f"初始资金不低于 {s.min_initial_cash} 元")
        fp = FeeParams(
            commission_rate=commission_rate
            if commission_rate is not None
            else s.default_commission_rate,
            min_commission=min_commission
            if min_commission is not None
            else s.default_min_commission,
            slippage_rate=slippage_rate if slippage_rate is not None else s.default_slippage_rate,
        )
        _check_fee_params(fp)
        now = self.clock.now()
        account = Account(
            id=new_account_id(),
            name=name,
            note=note,
            initial_cash=cash,
            fee_params=fp,
            block_st=s.default_block_st if block_st is None else block_st,
            status=AccountStatus.ACTIVE,
            webhook_url=None,
            webhook_secret=None,
            created_at=now,
            updated_at=now,
        )
        with self.db.write() as tx:
            repos.insert_account(tx, account)
            self.events.emit(
                tx,
                account,
                EventType.ACCOUNT_CREATED,
                occurred_at=now,
                basis={
                    "initial_cash": cash,
                    "fee_params": _fp_dict(fp),
                    "block_st": account.block_st,
                },
                summary=f"开户「{name}」，初始资金 {cash} 元",
            )
        return account

    def update(
        self,
        account_id: str,
        *,
        name: str | None = None,
        note: str | None = None,
        commission_rate: Decimal | None = None,
        min_commission: Decimal | None = None,
        slippage_rate: Decimal | None = None,
        block_st: bool | None = None,
    ) -> Account:
        now = self.clock.now()
        with self.db.write() as tx:
            a = self._load(tx, account_id)
            if a.status is AccountStatus.ARCHIVED:
                raise ServiceError("ACCOUNT_NOT_ACTIVE", "账户已归档，只读", 409)
            before = {"fee_params": _fp_dict(a.fee_params), "block_st": a.block_st}
            fp = FeeParams(
                commission_rate=a.fee_params.commission_rate
                if commission_rate is None
                else commission_rate,
                min_commission=a.fee_params.min_commission
                if min_commission is None
                else min_commission,
                slippage_rate=a.fee_params.slippage_rate
                if slippage_rate is None
                else slippage_rate,
            )
            _check_fee_params(fp)
            if name is not None and not name.strip():
                raise ServiceError("INVALID_REQUEST", "账户名称不能为空")
            updated = replace(
                a,
                name=a.name if name is None else name.strip(),
                note=a.note if note is None else note,
                fee_params=fp,
                block_st=a.block_st if block_st is None else block_st,
                updated_at=now,
            )
            repos.update_account(tx, updated)
            after = {"fee_params": _fp_dict(fp), "block_st": updated.block_st}
            if after != before:
                self.events.emit(
                    tx,
                    updated,
                    EventType.FEE_PARAMS_CHANGED,
                    occurred_at=now,
                    basis={"before": before, "after": after},
                    summary="费用参数或风险警示股限制变更，只影响之后的成交",
                )
        return updated

    def _cancel_pending(self, tx: Tx, a: Account, code: str, reason: str) -> int:
        n = 0
        for o in repos.pending_orders(tx, account_id=a.id):
            if o.status is OrderStatus.PENDING:
                self.execution.cancel_order(tx, a, o, code, reason, self.clock.now())
                n += 1
        return n

    def freeze(self, account_id: str) -> Account:
        now = self.clock.now()
        with self.db.write() as tx:
            a = self._load(tx, account_id)
            if a.status is not AccountStatus.ACTIVE:
                raise ServiceError("ACCOUNT_NOT_ACTIVE", f"账户当前状态为 {a.status.value}", 409)
            a = replace(a, status=AccountStatus.FROZEN, updated_at=now)
            repos.update_account(tx, a)
            n = self._cancel_pending(tx, a, "ACCOUNT_FROZEN", "账户冻结，自动撤销")
            self.events.emit(
                tx,
                a,
                EventType.ACCOUNT_FROZEN,
                occurred_at=now,
                basis={"cancelled_orders": n},
                summary=f"账户冻结，撤销等待中订单 {n} 笔",
            )
        return a

    def unfreeze(self, account_id: str) -> Account:
        now = self.clock.now()
        with self.db.write() as tx:
            a = self._load(tx, account_id)
            if a.status is not AccountStatus.FROZEN:
                raise ServiceError("ACCOUNT_NOT_FROZEN", "账户不处于冻结状态", 409)
            a = replace(a, status=AccountStatus.ACTIVE, updated_at=now)
            repos.update_account(tx, a)
            self.events.emit(tx, a, EventType.ACCOUNT_UNFROZEN, occurred_at=now, summary="账户恢复")
        return a

    def archive(self, account_id: str) -> Account:
        now = self.clock.now()
        with self.db.write() as tx:
            a = self._load(tx, account_id)
            if a.status is AccountStatus.ARCHIVED:
                raise ServiceError("ACCOUNT_NOT_ACTIVE", "账户已归档", 409)
            a = replace(a, status=AccountStatus.ARCHIVED, updated_at=now)
            n = self._cancel_pending(tx, a, "ACCOUNT_ARCHIVED", "账户归档，自动撤销")
            repos.update_account(tx, a)
            self.events.emit(
                tx,
                a,
                EventType.ACCOUNT_ARCHIVED,
                occurred_at=now,
                basis={"cancelled_orders": n},
                summary="账户归档，永久只读",
            )
        return a

    def set_webhook(self, account_id: str, url: str | None, secret: str | None) -> Account:
        now = self.clock.now()
        if url is not None and not (url.startswith("http://") or url.startswith("https://")):
            raise ServiceError("INVALID_REQUEST", "回调地址必须是 http 或 https")
        with self.db.write() as tx:
            a = self._load(tx, account_id)
            if a.status is AccountStatus.ARCHIVED:
                raise ServiceError("ACCOUNT_NOT_ACTIVE", "账户已归档，只读", 409)
            a = replace(a, webhook_url=url or None, webhook_secret=secret or None, updated_at=now)
            repos.update_account(tx, a)
        return a


def _fp_dict(fp: FeeParams) -> dict[str, Decimal]:
    return {
        "commission_rate": fp.commission_rate,
        "min_commission": fp.min_commission,
        "slippage_rate": fp.slippage_rate,
    }


def _check_fee_params(fp: FeeParams) -> None:
    if fp.commission_rate < 0 or fp.min_commission < 0 or fp.slippage_rate < 0:
        raise ServiceError("INVALID_REQUEST", "费用参数不能为负数")
    if fp.commission_rate > Decimal("0.003") or fp.slippage_rate > Decimal("0.05"):
        raise ServiceError("INVALID_REQUEST", "佣金率不超过 0.3%，滑点不超过 5%")
