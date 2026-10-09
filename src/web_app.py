"""Local FastAPI dashboard for Shopify abandonment recommendations."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .predict_recommendation import HybridPredictor
from .recommendation_engine import recommend


PROJECT = Path(__file__).resolve().parents[1]
DATASET = PROJECT / "data/processed/test.csv"
PIXEL_EVENTS = PROJECT / "data/raw/real_pixel_events.jsonl"
REAL_CASES = PROJECT / "data/processed/real_checkout_cases.csv"
INTERVENTIONS = PROJECT / "data/processed/checkout_interventions.jsonl"
PIXEL_EVENT_NAMES = {
    "cart_viewed",
    "checkout_started",
    "checkout_contact_info_submitted",
    "checkout_address_info_submitted",
    "checkout_shipping_info_submitted",
    "payment_info_submitted",
    "checkout_completed",
    "alert_displayed",
}
PIXEL_WRITE_LOCK = Lock()
INTERVENTION_WRITE_LOCK = Lock()

ADMIN_ACTIONS = {
    "REMINDER_SENT",
    "DISCOUNT_OFFERED",
    "FREE_SHIPPING_OFFERED",
    "PERSONAL_CONTACT",
    "NO_ACTION",
    "RECOMMENDATION_REJECTED",
}
CHANNELS = {"email", "sms", "phone", "other", "none"}
RESULTS = {"pending", "recovered", "not_recovered"}


class InterventionInput(BaseModel):
    action: str
    channel: str = "none"
    result: str = "pending"


class InterventionResultInput(BaseModel):
    result: str


def load_cases() -> dict[str, dict[str, str]]:
    with DATASET.open(encoding="utf-8") as file:
        return {row["case_id"]: row for row in csv.DictReader(file)}


CASES = load_cases()


@lru_cache(maxsize=1)
def predictor() -> HybridPredictor:
    return HybridPredictor(PROJECT)


app = FastAPI(title="Shopify Checkout AI", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["content-type"],
)


@app.get("/", response_class=HTMLResponse)
def dashboard() -> str:
    return DASHBOARD_HTML


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "cases": len(CASES), "model": "abandonment_pattern_model.keras"}


@app.post("/api/pixel-events", status_code=202)
async def collect_pixel_event(request: Request) -> dict[str, str]:
    try:
        payload: Any = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid JSON") from error

    return store_pixel_event(payload)


def store_pixel_event(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Event must be a JSON object")
    if payload.get("eventName") not in PIXEL_EVENT_NAMES:
        raise HTTPException(status_code=422, detail="Unsupported Shopify pixel event")
    if not isinstance(payload.get("eventId"), str) or not payload["eventId"]:
        raise HTTPException(status_code=422, detail="eventId is required")

    record = {
        "receivedAt": datetime.now(UTC).isoformat(),
        **payload,
    }
    PIXEL_EVENTS.parent.mkdir(parents=True, exist_ok=True)
    with PIXEL_WRITE_LOCK, PIXEL_EVENTS.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return {"status": "accepted", "eventId": payload["eventId"]}


@app.get("/api/cases")
def cases(
    limit: int = Query(default=50, ge=1, le=200),
    pattern: str | None = None,
) -> dict:
    rows = CASES.values()
    if pattern:
        rows = (row for row in rows if row["detected_pattern"] == pattern)
    result = [{
        "case_id": row["case_id"],
        "last_funnel_stage": row["last_funnel_stage"],
        "subtotal_price": float(row["subtotal_price"]),
        "device_type": row["device_type"],
        "synthetic_label": row["detected_pattern"],
    } for row in list(rows)[:limit]]
    return {"items": result, "count": len(result)}


@app.get("/api/real-cases")
def real_cases() -> dict:
    if not REAL_CASES.exists():
        return {"items": [], "count": 0}
    with REAL_CASES.open(encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    result = [{
        "case_id": row["case_id"],
        "customer_segment": row["customer_segment"],
        "previous_orders": int(row["previous_orders"]),
        "outcome": row["outcome"],
        "label_source": row["label_source"],
        "last_funnel_stage": row["last_funnel_stage"],
        "subtotal_price": float(row["subtotal_price"]),
        "detected_pattern": row["detected_pattern"],
        "recommended_action": row["recommended_action"],
    } for row in rows]
    return {"items": result, "count": len(result)}


def load_real_case(case_id: str) -> dict[str, str] | None:
    if not REAL_CASES.exists():
        return None
    with REAL_CASES.open(encoding="utf-8") as file:
        return next((row for row in csv.DictReader(file) if row["case_id"] == case_id), None)


def load_interventions(case_id: str | None = None) -> list[dict[str, Any]]:
    if not INTERVENTIONS.exists():
        return []
    records = []
    with INTERVENTIONS.open(encoding="utf-8") as file:
        for line in file:
            if not line.strip():
                continue
            record = json.loads(line)
            if case_id is None or record.get("case_id") == case_id:
                records.append(record)
    return records


def store_intervention(case_id: str, payload: InterventionInput) -> dict[str, Any]:
    if load_real_case(case_id) is None:
        raise HTTPException(status_code=404, detail="Real checkout case not found")
    if payload.action not in ADMIN_ACTIONS:
        raise HTTPException(status_code=422, detail="Unsupported administrator action")
    if payload.channel not in CHANNELS:
        raise HTTPException(status_code=422, detail="Unsupported communication channel")
    if payload.result not in RESULTS:
        raise HTTPException(status_code=422, detail="Unsupported intervention result")

    record = {
        "intervention_id": f"int-{datetime.now(UTC).strftime('%Y%m%d%H%M%S%f')}",
        "case_id": case_id,
        "action": payload.action,
        "channel": payload.channel,
        "result": payload.result,
        "recorded_at": datetime.now(UTC).isoformat(),
    }
    INTERVENTIONS.parent.mkdir(parents=True, exist_ok=True)
    with INTERVENTION_WRITE_LOCK, INTERVENTIONS.open("a", encoding="utf-8") as file:
        file.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


@app.get("/api/interventions")
def interventions(case_id: str | None = None) -> dict:
    items = load_interventions(case_id)
    return {"items": items, "count": len(items)}


@app.post("/api/real-cases/{case_id}/interventions", status_code=201)
def create_intervention(case_id: str, payload: InterventionInput) -> dict[str, Any]:
    return store_intervention(case_id, payload)


@app.patch("/api/interventions/{intervention_id}")
def update_intervention_result(
    intervention_id: str, payload: InterventionResultInput
) -> dict[str, Any]:
    if payload.result not in {"recovered", "not_recovered"}:
        raise HTTPException(status_code=422, detail="Unsupported intervention result")
    with INTERVENTION_WRITE_LOCK:
        records = load_interventions()
        record = next(
            (item for item in records if item["intervention_id"] == intervention_id), None
        )
        if record is None:
            raise HTTPException(status_code=404, detail="Intervention not found")
        record["result"] = payload.result
        record["result_recorded_at"] = datetime.now(UTC).isoformat()
        with INTERVENTIONS.open("w", encoding="utf-8") as file:
            for item in records:
                file.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
    return record


ACTION_TEXT = {
    "NO_ACTION": ("Заказ завершён", "Ничего не отправлять клиенту."),
    "SEND_CHECKOUT_REMINDER": (
        "Мягко напомнить о checkout",
        "Отправить обычное напоминание без автоматической скидки и проверить итоговые расходы.",
    ),
    "CHECK_DELIVERY_SETTINGS": (
        "Проверить доставку",
        "Проверить стоимость, сроки и доступность доставки для региона клиента.",
    ),
    "SUGGEST_ALTERNATIVE_PAYMENT": (
        "Предложить другой способ оплаты",
        "Проверить платёжного провайдера и показать доступные альтернативные способы оплаты.",
    ),
    "CHECK_PAYMENT_PROVIDER": (
        "Проверить оплату",
        "Проверить платёжного провайдера и возможные технические ошибки checkout.",
    ),
    "CHECK_DISCOUNT_CODE": ("Проверить промокод", "Проверить код и условия акции."),
    "CHECK_PRODUCT_STOCK": ("Проверить наличие", "Проверить остаток или предложить аналог."),
    "REVIEW_CHECKOUT_ENTRY": (
        "Проверить начало checkout",
        "Проверить первый экран и отправить мягкое напоминание без скидки.",
    ),
    "REVIEW_MANUALLY": ("Проверить вручную", "Пока недостаточно подтверждённых данных."),
}


@app.get("/api/real-recommendations/{case_id}")
def real_recommendation(case_id: str) -> dict:
    row = load_real_case(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Real checkout case not found")
    result = recommend(row)
    title, advice = ACTION_TEXT.get(
        result.action,
        ("Проверить checkout", "Проверить случай и подтверждённые признаки вручную."),
    )
    confidence = {"high": 0.95, "medium": 0.75, "low": 0.5}[result.confidence]
    return {
        "case_id": case_id,
        "title": title,
        "advice": advice,
        "recommended_action": result.action,
        "confidence": confidence,
        "reasons": result.reasons,
        "alternative": result.alternative,
        "prohibited_action": result.prohibited_action,
        "model_pattern": result.pattern,
        "guardrail_applied": True,
        "customer_segment": row["customer_segment"],
        "previous_orders": int(row["previous_orders"]),
        "outcome": row["outcome"],
        "label_source": row["label_source"],
    }


@app.get("/api/recommendations/{case_id}")
def recommendation(case_id: str) -> dict:
    row = CASES.get(case_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Checkout case not found")
    return predictor().predict(row)


DASHBOARD_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shopify Checkout AI</title>
  <style>
    :root { color-scheme: light; --ink:#17211b; --muted:#647067; --green:#177c4c;
      --pale:#edf7f1; --line:#dbe5dd; --paper:#fff; --bg:#f4f6f2; --warn:#a14d16; }
    * { box-sizing:border-box } body { margin:0; font:15px/1.5 system-ui,-apple-system,sans-serif;
      color:var(--ink); background:var(--bg) } header { padding:28px clamp(20px,5vw,72px);
      color:white; background:#173f2b } header h1 { margin:0 0 4px; font-size:26px }
    header p { margin:0; opacity:.76 } main { display:grid; grid-template-columns:minmax(280px,420px) 1fr;
      gap:22px; padding:24px clamp(16px,5vw,72px) } .panel { background:var(--paper);
      border:1px solid var(--line); border-radius:16px; box-shadow:0 8px 30px #1834210b }
    .list { padding:14px; max-height:calc(100vh - 155px); overflow:auto } .case { width:100%;
      text-align:left; padding:13px; margin:0 0 8px; border:1px solid var(--line); border-radius:11px;
      background:white; cursor:pointer } .case:hover,.case.active { border-color:var(--green); background:var(--pale) }
    .case strong { display:block } .case small { color:var(--muted) } .detail { padding:30px }
    .badge { display:inline-block; padding:5px 9px; border-radius:999px; background:var(--pale);
      color:var(--green); font-weight:700; font-size:12px; text-transform:uppercase }
    h2 { margin:14px 0 6px; font-size:27px } h3 { margin:24px 0 7px; font-size:15px }
    .advice { padding:16px 18px; border-left:4px solid var(--green); background:var(--pale);
      border-radius:8px } ul { padding-left:20px } .meta { display:grid;
      grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; margin-top:22px }
    .meta div { padding:12px; border:1px solid var(--line); border-radius:10px }
    .meta small { display:block; color:var(--muted) } .prohibited { color:var(--warn) }
    .actions { display:flex; flex-wrap:wrap; gap:8px; margin-top:10px } .actions button,
    .result button { border:1px solid var(--green); border-radius:9px; padding:9px 12px;
      color:var(--green); background:white; cursor:pointer } .actions button:hover,.result button:hover {
      color:white; background:var(--green) } .result { margin-top:12px } #saved { color:var(--green); margin-top:10px }
    .empty { color:var(--muted); padding:40px 10px } @media(max-width:800px){main{grid-template-columns:1fr}
      .list{max-height:340px}.meta{grid-template-columns:1fr}}
  </style>
</head>
<body>
<header><h1>Shopify Checkout AI</h1><p>Рекомендации по брошенным корзинам и checkout’ам</p></header>
<main>
  <section class="panel list"><div id="cases" class="empty">Загружаю случаи…</div></section>
  <section class="panel detail" id="detail"><div class="empty">Выберите checkout слева.</div></section>
</main>
<script>
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
async function loadCases(){
  const data=await fetch('/api/real-cases').then(r=>r.json());
  document.querySelector('#cases').innerHTML=data.items.map((x,i)=>`<button class="case" data-id="${esc(x.case_id)}">
    <strong>${esc(x.case_id)}</strong><small>${esc(x.customer_segment)} · ${esc(x.last_funnel_stage)} · $${x.subtotal_price.toFixed(2)}</small></button>`).join('');
  document.querySelectorAll('.case').forEach(b=>b.onclick=()=>showCase(b));
  if(data.items.length) document.querySelector('.case').click();
}
async function showCase(button){
  document.querySelectorAll('.case').forEach(b=>b.classList.remove('active')); button.classList.add('active');
  const x=await fetch('/api/real-recommendations/'+encodeURIComponent(button.dataset.id)).then(r=>r.json());
  document.querySelector('#detail').innerHTML=`<span class="badge">Уверенность ${(x.confidence*100).toFixed(1)}%</span>
    <h2>${esc(x.title)}</h2><div class="advice">${esc(x.advice)}</div>
    <h3>Почему</h3><ul>${x.reasons.map(r=>`<li>${esc(r)}</li>`).join('')}</ul>
    <div class="meta"><div><small>Действие</small>${esc(x.recommended_action)}</div>
    <div><small>Закономерность модели</small>${esc(x.model_pattern)}</div>
    <div><small>Клиент</small>${esc(x.customer_segment)} · прошлых заказов: ${esc(x.previous_orders)}</div>
    <div><small>Результат / источник</small>${esc(x.outcome)} · ${esc(x.label_source)}</div>
    ${x.alternative?`<div><small>Альтернатива</small>${esc(x.alternative)}</div>`:''}
    ${x.prohibited_action?`<div class="prohibited"><small>Не рекомендуется</small>${esc(x.prohibited_action)}</div>`:''}</div>
    <h3>Действие администратора</h3><div class="actions">
      <button data-action="REMINDER_SENT">Напоминание</button><button data-action="DISCOUNT_OFFERED">Скидка</button>
      <button data-action="FREE_SHIPPING_OFFERED">Бесплатная доставка</button>
      <button data-action="PERSONAL_CONTACT">Личный контакт</button><button data-action="NO_ACTION">Ничего</button>
      <button data-action="RECOMMENDATION_REJECTED">Совет отклонён</button></div>
    <div class="result"><small>Результат последнего действия</small>
      <button data-result="recovered">Покупка завершена</button>
      <button data-result="not_recovered">Остался неоплаченным</button></div><div id="saved"></div>`;
  let lastIntervention=null;
  document.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{
    lastIntervention=await saveIntervention(x.case_id,b.dataset.action);
  });
  document.querySelectorAll('[data-result]').forEach(b=>b.onclick=async()=>{
    if(!lastIntervention){ document.querySelector('#saved').textContent='Сначала выберите действие.'; return; }
    await saveResult(lastIntervention.intervention_id,b.dataset.result);
  });
}
async function saveIntervention(caseId,action){
  const channel=['REMINDER_SENT','DISCOUNT_OFFERED','FREE_SHIPPING_OFFERED'].includes(action)?'email':
    action==='PERSONAL_CONTACT'?'phone':'none';
  const response=await fetch('/api/real-cases/'+encodeURIComponent(caseId)+'/interventions',{
    method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,channel,result:'pending'})});
  document.querySelector('#saved').textContent=response.ok?'Действие сохранено; ожидаем результат.':'Не удалось сохранить.';
  return response.ok?await response.json():null;
}
async function saveResult(interventionId,result){
  const response=await fetch('/api/interventions/'+encodeURIComponent(interventionId),{
    method:'PATCH',headers:{'content-type':'application/json'},body:JSON.stringify({result})});
  document.querySelector('#saved').textContent=response.ok?'Результат сохранён в обучающую историю.':'Не удалось сохранить.';
}
loadCases().catch(()=>document.querySelector('#cases').textContent='Не удалось загрузить данные.');
</script>
</body></html>"""
