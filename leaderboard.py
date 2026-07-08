import os
import re
import requests
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv, dotenv_values
from slack_sdk import WebClient

load_dotenv()

client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

PAYMENTS_CHANNEL_ID = "C0AU0B20FGT"
DAILY_CHANNEL_ID = "C0A2R6H3M0C"
NET_MARGIN = 0.115
LEADERBOARD_CHANNEL_ID = "C0BFSCRDMT7"

HUBSPOT_OWNER_MAP = {
    "1343632400": "Chloe Vine",
    "227702688": "Nick Bolton",
    "77909518": "Morgan Augiron",
    "80667173": "Andy Barber",
    "83233627": "Nicole DiOrio",
    "92131949": "Nick Edwards",
    "92999163": "Stevie Ashton",
}


def start_of_current_month_ts():
    now = datetime.now(timezone.utc)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    return str(start.timestamp())


def now_ts():
    return str(datetime.now(timezone.utc).timestamp())


def parse_payment(text):
    lead_match = re.search(r"\*Lead\*\s+([^·\n]+)", text)
    pta_match = re.search(r"\*PTA\*\s+([^·\n]+)", text)
    amount_match = re.search(
        r"\*Amount paid\*\s+([A-Z$]*\$?[\d,]+\.\d{2})\s+([A-Z]{3})",
        text,
    )

    if not amount_match:
        return None

    customer = lead_match.group(1).strip() if lead_match else "Unknown"
    pta = pta_match.group(1).strip() if pta_match else "Unassigned"

    amount = float(
        amount_match.group(1)
        .replace("NZ$", "")
        .replace("A$", "")
        .replace("$", "")
        .replace(",", "")
    )

    currency = amount_match.group(2)

    return {
        "customer": customer,
        "pta": pta,
        "amount": amount,
        "currency": currency,
    }


def fetch_month_to_date_payments():
    payments = []
    cursor = None

    while True:
        response = client.conversations_history(
            channel=PAYMENTS_CHANNEL_ID,
            oldest=start_of_current_month_ts(),
            latest=now_ts(),
            limit=200,
            cursor=cursor,
            inclusive=True,
        )

        for message in response["messages"]:
            for attachment in message.get("attachments", []):
                payment = parse_payment(attachment.get("text", ""))
                if payment:
                    payments.append(payment)

        cursor = response.get("response_metadata", {}).get("next_cursor")

        if not cursor:
            break

    return payments


def fetch_daily_messages():
    messages = []
    cursor = None

    while True:
        response = client.conversations_history(
            channel=DAILY_CHANNEL_ID,
            oldest=start_of_current_month_ts(),
            latest=now_ts(),
            limit=200,
            cursor=cursor,
            inclusive=True,
        )

        messages.extend(response["messages"])

        cursor = response.get("response_metadata", {}).get("next_cursor")

        if not cursor:
            break

    return messages


def get_message_text(message):
    text_parts = [message.get("text", "")]

    for attachment in message.get("attachments", []):
        text_parts.append(attachment.get("text", ""))
        text_parts.append(attachment.get("fallback", ""))

    return "\n".join(text_parts)


def calculate_time_range_hours(start_h, start_m, start_ampm, end_h, end_m, end_ampm, text_lower):
    def to_24_hour(hour, ampm):
        if ampm == "pm" and hour != 12:
            return hour + 12
        if ampm == "am" and hour == 12:
            return 0
        return hour

    if "worked 5pm" in text_lower and end_ampm == "pm":
        end_ampm = "am"

    start = to_24_hour(start_h, start_ampm) + start_m / 60
    end = to_24_hour(end_h, end_ampm) + end_m / 60

    if end < start:
        end += 24

    if start_ampm == "pm" and end_ampm == "am" and (end - start) > 14:
        end -= 12

    hours = end - start

    break_match = re.search(r"(\d+)\s*min(?:ute)?\s*break", text_lower)
    if break_match:
        hours -= int(break_match.group(1)) / 60

    return round(hours, 2)


def parse_hours_from_text(text):
    text_lower = text.lower()

    if "worked one hour" in text_lower or "worked one hours" in text_lower:
        return 1.0

    match = re.search(r"worked[:\s]+(\d+)h\s*(\d{1,2})", text_lower)
    if match:
        return int(match.group(1)) + int(match.group(2)) / 60

    match = re.search(
        r"worked[:\s]+(?:about\s+|just over\s+|just under\s+)?(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)",
        text_lower,
    )
    if match:
        return float(match.group(1))

    match = re.search(
        r"worked[:\s]+(\d{1,2})(?::|\.)(\d{2})\s*(am|pm)?\s*(?:-|–|to)\s*(\d{1,2})(?::|\.)(\d{2})\s*(am|pm)?",
        text_lower,
    )
    if match:
        return calculate_time_range_hours(
            int(match.group(1)),
            int(match.group(2)),
            match.group(3),
            int(match.group(4)),
            int(match.group(5)),
            match.group(6),
            text_lower,
        )

    match = re.search(
        r"worked[:\s]+(\d{1,2})\s*(am|pm)\s*(?:-|–|to)\s*(\d{1,2})(?::|\.)(\d{2})\s*(am|pm)",
        text_lower,
    )
    if match:
        return calculate_time_range_hours(
            int(match.group(1)),
            0,
            match.group(2),
            int(match.group(3)),
            int(match.group(4)),
            match.group(5),
            text_lower,
        )

    return None


def get_pta_name_from_message(message):
    user_id = message.get("user")

    user_map = {
        "U073Z0MUCCD": "Chloe Vine",
        "U08G2F1PF0V": "Morgan Augiron",
        "U0903E8GN9Y": "Andy Barber",
        "U09F23JMM8R": "Nicole DiOrio",
        "U0B2XPQTYSD": "Nick Edwards",
        "U06PKFW90UV": "Stevie Ashton",
    }

    return user_map.get(user_id, "Unknown")


def build_hours_summary(daily_messages):
    hours_by_pta = defaultdict(float)

    for message in daily_messages:
        text = get_message_text(message)
        hours = parse_hours_from_text(text)

        if hours is None:
            continue

        pta = get_pta_name_from_message(message)
        hours_by_pta[pta] += hours

    return hours_by_pta


def build_payment_summary(payments):
    sales = defaultdict(lambda: defaultdict(float))
    customers = defaultdict(set)

    for payment in payments:
        pta = payment["pta"]
        currency = payment["currency"]

        sales[pta][currency] += payment["amount"]
        customers[pta].add(payment["customer"])

    return sales, customers


def fetch_hubspot_call_summary():
    env = dotenv_values(Path(__file__).parent / ".env")
    token = env.get("HUBSPOT_ACCESS_TOKEN")

    start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_ms = int(start.timestamp() * 1000)

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    url = "https://api.hubapi.com/crm/v3/objects/calls/search"

    calls_by_pta = defaultdict(int)
    duration_by_pta = defaultdict(int)

    after = None

    while True:
        payload = {
            "filterGroups": [
                {
                    "filters": [
                        {
                            "propertyName": "hs_timestamp",
                            "operator": "GTE",
                            "value": str(start_ms),
                        }
                    ]
                }
            ],
            "properties": ["hubspot_owner_id", "hs_call_duration"],
            "limit": 100,
        }

        if after:
            payload["after"] = after

        response = requests.post(url, headers=headers, json=payload)
        data = response.json()

        for item in data.get("results", []):
            props = item.get("properties", {})
            owner_id = props.get("hubspot_owner_id")
            pta = HUBSPOT_OWNER_MAP.get(owner_id, "Unknown")

            duration = int(props.get("hs_call_duration") or 0)

            calls_by_pta[pta] += 1
            duration_by_pta[pta] += duration

        after = data.get("paging", {}).get("next", {}).get("after")

        if not after:
            break

    call_summary = {}

    for pta, calls in calls_by_pta.items():
        avg_seconds = (duration_by_pta[pta] / calls) / 1000 if calls else 0

        call_summary[pta] = {
            "calls": calls,
            "avg_call_seconds": round(avg_seconds, 1),
        }

    return call_summary


payments = fetch_month_to_date_payments()
sales, customers = build_payment_summary(payments)

daily_messages = fetch_daily_messages()
hours_summary = build_hours_summary(daily_messages)

call_summary = fetch_hubspot_call_summary()

all_ptas = sorted(set(list(sales.keys()) + list(hours_summary.keys()) + list(call_summary.keys())))

leaderboard_lines = []

leaderboard_lines.append("*🏆 PLANIT LEADERBOARD - MONTH TO DATE*")
leaderboard_lines.append("")

for pta in all_ptas:
    nzd_sales = sales[pta].get("NZD", 0)
    aud_sales = sales[pta].get("AUD", 0)

    estimated_net_nzd = nzd_sales * NET_MARGIN
    estimated_net_aud = aud_sales * NET_MARGIN

    hours = hours_summary.get(pta, 0)
    calls = call_summary.get(pta, {}).get("calls", 0)
    avg_call_seconds = call_summary.get(pta, {}).get("avg_call_seconds", 0)
    customer_count = len(customers[pta])

    nzd_sales_per_hour = nzd_sales / hours if hours else 0
    aud_sales_per_hour = aud_sales / hours if hours else 0

    net_nzd_per_hour = estimated_net_nzd / hours if hours else 0
    net_aud_per_hour = estimated_net_aud / hours if hours else 0

    leaderboard_lines.append(
        f"*{pta}*\n"
        f"• NZD ${nzd_sales:,.2f} | AUD ${aud_sales:,.2f}\n"
        f"• Net NZD ${estimated_net_nzd:,.2f} | Net AUD ${estimated_net_aud:,.2f}\n"
        f"• Customers: {customer_count}\n"
        f"• Hours: {hours:.2f}\n"
        f"• Calls: {calls} (Avg {avg_call_seconds:.1f}s)\n"
        f"• NZD/hr ${nzd_sales_per_hour:,.2f} | AUD/hr ${aud_sales_per_hour:,.2f}\n"
        f"• Net/hr NZD ${net_nzd_per_hour:,.2f} | AUD ${net_aud_per_hour:,.2f}"
    )

message = "\n\n".join(leaderboard_lines)

client.chat_postMessage(
    channel=LEADERBOARD_CHANNEL_ID,
    text=message,
)

print("Leaderboard posted to Slack.")