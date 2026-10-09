import copy
import json
from datetime import date
from enum import Enum
from pathlib import Path

DATA_FILE = Path(__file__).parent / "data" / "orders.json"

_raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
TODAY = date.fromisoformat(_raw["today"])
_FIXTURE: dict[int, dict] = {o["order_id"]: o for o in _raw["orders"]}

RETURN_DAYS = 14
REFUND_ETA = "до 5 рабочих дней после проверки товара"
CANCEL_REFUND_ETA = "до 5 рабочих дней"
ORDERS: dict[int, dict] = {}
REFUNDS: list[dict] = []

class Status(str, Enum):
    PLACED = "placed"
    ASSEMBLED = "assembled"
    HANDED_TO_DELIVERY = "handed_to_delivery"
    IN_TRANSIT = "in_transit"
    READY_FOR_PICKUP = "ready_for_pickup"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


STATUS_LABELS = {
    Status.PLACED.value: "Оформлен",
    Status.ASSEMBLED.value: "Собран",
    Status.HANDED_TO_DELIVERY.value: "Передан в доставку",
    Status.IN_TRANSIT.value: "В пути",
    Status.READY_FOR_PICKUP.value: "Ожидает в пункте выдачи",
    Status.DELIVERED.value: "Доставлен",
    Status.CANCELLED.value: "Отменён",
}
BEFORE_SHIPPING = (Status.PLACED, Status.ASSEMBLED)
IN_DELIVERY = (Status.HANDED_TO_DELIVERY, Status.IN_TRANSIT, Status.READY_FOR_PICKUP)


def reset_state() -> None:
    ORDERS.clear()
    ORDERS.update(copy.deepcopy(_FIXTURE))
    REFUNDS.clear()


reset_state()


def _not_found(order_id: int) -> dict:
    return {"error": f"Заказ {order_id} не найден. Уточните номер заказа у клиента."}


def _days_since_delivery(order: dict) -> int | None:
    return (TODAY - date.fromisoformat(order["delivered_at"])).days if order.get("delivered_at") else None


def _add_refund(order_id: int, amount_rub: int, reason: str) -> dict:
    refund = {"refund_id": f"R-{len(REFUNDS) + 1:04d}", "order_id": order_id, "amount_rub": amount_rub, "reason": reason}
    REFUNDS.append(refund)
    return refund


def get_order(order_id: int) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return _not_found(order_id)
    return {
        **copy.deepcopy(order),
        "status_label": STATUS_LABELS.get(order["status"], order["status"]),
        "days_since_delivery": _days_since_delivery(order),
    }


def cancel_order(order_id: int, reason: str) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return _not_found(order_id)
    status = order["status"]
    if status == Status.CANCELLED:
        return {"error": f"Заказ {order_id} уже отменён, повторная отмена не нужна."}
    if order.get("custom_build") and order.get("build_started"):
        return {"error": f"Заказ {order_id} собирается индивидуально, сборка уже начата: отменить его нельзя. "
                         "После получения клиент может обратиться по правилам возврата или по гарантии."}
    if status in BEFORE_SHIPPING:
        order["status"] = Status.CANCELLED.value
        order["cancel_reason"] = reason
        refund = _add_refund(order_id, order["paid_rub"], reason) if order["paid_rub"] > 0 else None
        result = {"order_id": order_id, "status": Status.CANCELLED.value, "refund": refund}
        if refund:
            result["refund_eta"] = CANCEL_REFUND_ETA
        return result
    if status == Status.DELIVERED:
        return {"error": f"Заказ {order_id} уже получен, отменить его нельзя. Предложите клиенту оформить возврат."}
    if status in IN_DELIVERY:
        return {"error": f"Заказ {order_id} уже в доставке, отменить его нельзя. Предложите клиенту отказаться "
                         "от заказа при получении: деньги вернутся после возврата заказа на склад."}
    return {"error": f"Отмена заказа {order_id} в статусе {status} невозможна."}


def change_address(order_id: int, address: str) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return _not_found(order_id)
    address = address.strip()
    if not address:
        return {"error": "Адрес не указан. Уточните у клиента новый адрес доставки."}
    status = order["status"]
    if status in BEFORE_SHIPPING:
        order["address"] = address
        return {"order_id": order_id, "address": address, "status": status}
    if status in IN_DELIVERY:
        return {"error": f"Заказ {order_id} уже передан в доставку, адрес изменить нельзя. Предложите клиенту "
                         "отказаться от заказа при получении и оформить новый заказ на нужный адрес."}
    if status == Status.DELIVERED:
        return {"error": f"Заказ {order_id} уже получен, менять адрес не нужно."}
    if status == Status.CANCELLED:
        return {"error": f"Заказ {order_id} отменён, адрес изменить нельзя."}
    return {"error": f"Смена адреса заказа {order_id} в статусе {status} невозможна."}


def create_refund(order_id: int, amount_rub: int, reason: str) -> dict:
    order = ORDERS.get(order_id)
    if order is None:
        return _not_found(order_id)
    status = order["status"]
    if status != Status.DELIVERED:
        if status in BEFORE_SHIPPING:
            return {"error": f"Заказ {order_id} ещё не получен, возврат оформляется после получения. "
                             "Пока заказ не передан в доставку, его можно отменить."}
        if status in IN_DELIVERY:
            return {"error": f"Заказ {order_id} в доставке, возврат оформляется после получения. "
                             "Клиент может отказаться от заказа при получении."}
        if status == Status.CANCELLED:
            return {"error": f"Заказ {order_id} отменён, деньги по отмене уже возвращены."}
        return {"error": f"Возврат по заказу {order_id} в статусе {status} невозможен."}
    days = _days_since_delivery(order)
    if days is None or days > RETURN_DAYS:
        return {"error": f"С получения заказа {order_id} прошло {days} дн., возврат возможен в течение "
                         f"{RETURN_DAYS} дней. Предложите клиенту гарантийное обращение."}
    if amount_rub <= 0:
        return {"error": "Сумма возврата должна быть больше нуля."}
    already = sum(r["amount_rub"] for r in REFUNDS if r["order_id"] == order_id)
    if already + amount_rub > order["paid_rub"]:
        available = order["paid_rub"] - already
        return {"error": f"Сумма вне допустимого: оплачено {order['paid_rub']}, уже возвращено {already}, "
                         f"доступно к возврату {available}."}
    refund = _add_refund(order_id, amount_rub, reason)
    return {**refund, "status": "created", "eta": REFUND_ETA}


TOOL_IMPL = {
    "get_order": get_order,
    "cancel_order": cancel_order,
    "change_address": change_address,
    "create_refund": create_refund,
}


def run_tool(name: str, arguments_json: str) -> str:
    try:
        result = TOOL_IMPL[name](**json.loads(arguments_json))
    except KeyError as e:
        if name not in TOOL_IMPL:
            result = {"error": f"Неизвестный инструмент: {name}"}
        else:
            result = {"error": f"KeyError: {e}"}
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result, ensure_ascii=False)


def _tool(name: str, description: str, properties: dict) -> dict:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        "strict": True,
    }


_ORDER_ID = {"type": "integer", "description": "Номер заказа из обращения клиента"}
_ACTION_NOTE = ("Необратимое действие: вызывайте только по прямой просьбе клиента и после get_order. "
                "Может вернуть ошибку, если действие не положено: тогда передайте клиенту подсказку из ошибки.")

TOOLS = [
    _tool(
        "get_order",
        "Возвращает заказ по номеру: состав, оплаченную сумму, статус, дату получения и сколько дней прошло "
        "с получения. Вызывайте перед любым действием с заказом.",
        {"order_id": _ORDER_ID},
    ),
    _tool(
        "cancel_order",
        "Отменяет заказ, который ещё не передан в доставку, и возвращает оплаченную сумму целиком. " + _ACTION_NOTE,
        {
            "order_id": _ORDER_ID,
            "reason": {"type": "string", "description": "Причина отмены со слов клиента одной фразой. Если клиент её не назвал, «не указана»: не придумывайте"},
        },
    ),
    _tool(
        "change_address",
        "Меняет адрес доставки заказа, который ещё не передан в доставку. " + _ACTION_NOTE,
        {
            "order_id": _ORDER_ID,
            "address": {"type": "string", "description": "Новый адрес доставки полностью, как его назвал клиент"},
        },
    ),
    _tool(
        "create_refund",
        "Оформляет возврат денег по полученному заказу: не позже 14 дней с получения и не больше оплаченного. "
        + _ACTION_NOTE,
        {
            "order_id": _ORDER_ID,
            "amount_rub": {"type": "integer", "description": "Сумма возврата в рублях, не больше оплаченной"},
            "reason": {"type": "string", "description": "Причина возврата со слов клиента одной фразой. Если клиент её не назвал, «не указана»: не придумывайте"},
        },
    ),
]
