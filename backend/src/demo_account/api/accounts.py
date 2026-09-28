"""账户接口（实现 6.2）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ..services.container import Container
from .deps import get_container
from .schemas import AccountCreate, AccountUpdate, WebhookSet

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.post("", status_code=201)
def create_account(body: AccountCreate, c: Container = Depends(get_container)) -> dict[str, Any]:
    a = c.accounts.create(
        body.name,
        note=body.note,
        initial_cash=body.initial_cash,
        commission_rate=body.commission_rate,
        min_commission=body.min_commission,
        slippage_rate=body.slippage_rate,
        block_st=body.block_st,
    )
    return a.to_dict()


@router.get("")
def list_accounts(
    include_archived: bool = False, c: Container = Depends(get_container)
) -> list[dict[str, Any]]:
    return [c.portfolio.overview(a) for a in c.accounts.list(include_archived)]


@router.get("/{account_id}")
def get_account(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.portfolio.overview(c.accounts.get(account_id))


@router.patch("/{account_id}")
def update_account(
    account_id: str, body: AccountUpdate, c: Container = Depends(get_container)
) -> dict[str, Any]:
    return c.accounts.update(
        account_id,
        name=body.name,
        note=body.note,
        commission_rate=body.commission_rate,
        min_commission=body.min_commission,
        slippage_rate=body.slippage_rate,
        block_st=body.block_st,
    ).to_dict()


@router.post("/{account_id}/freeze")
def freeze(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.accounts.freeze(account_id).to_dict()


@router.post("/{account_id}/unfreeze")
def unfreeze(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.accounts.unfreeze(account_id).to_dict()


@router.post("/{account_id}/archive")
def archive(account_id: str, c: Container = Depends(get_container)) -> dict[str, Any]:
    return c.accounts.archive(account_id).to_dict()


@router.put("/{account_id}/webhook")
def set_webhook(
    account_id: str, body: WebhookSet, c: Container = Depends(get_container)
) -> dict[str, Any]:
    return c.accounts.set_webhook(account_id, body.url, body.secret).to_dict()
