import requests
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from dotenv import dotenv_values

env = dotenv_values(Path(__file__).parent / ".env")
token = env.get("HUBSPOT_ACCESS_TOKEN")

start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
start_ms = int(start.timestamp() * 1000)

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
}

url = "https://api.hubapi.com/crm/v3/objects/calls/search"

calls_by_owner = defaultdict(int)
duration_by_owner = defaultdict(int)

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
        "properties": [
            "hubspot_owner_id",
            "hs_call_duration",
        ],
        "limit": 100,
    }

    if after:
        payload["after"] = after

    response = requests.post(url, headers=headers, json=payload)
    data = response.json()

    for item in data.get("results", []):
        props = item.get("properties", {})
        owner_id = props.get("hubspot_owner_id") or "Unknown"
        duration = int(props.get("hs_call_duration") or 0)

        calls_by_owner[owner_id] += 1
        duration_by_owner[owner_id] += duration

    after = data.get("paging", {}).get("next", {}).get("after")

    if not after:
        break

print("\nCalls by HubSpot owner:\n")

for owner_id, calls in sorted(calls_by_owner.items()):
    total_duration_ms = duration_by_owner[owner_id]
    avg_duration_seconds = (total_duration_ms / calls) / 1000 if calls else 0

    print(owner_id, "-", calls, "calls,", round(avg_duration_seconds, 1), "sec avg")