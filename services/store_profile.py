"""Non-financial Store profile context helpers extracted during Step 4."""

from extensions import mongo

def _build_store_profile_context(store, owner):
    notification_settings = mongo.store_notification_settings.find_one({
        "store_id": store["_id"]
    }) or {
        "enabled": False
    }

    checklist = [
        {
            "label": "Store name added",
            "done": bool((store.get("store_name") or "").strip())
        },
        {
            "label": "Owner name added",
            "done": bool((owner.get("name") or store.get("owner_name") or "").strip())
        },
        {
            "label": "Phone number added",
            "done": bool((owner.get("phone") or store.get("phone") or "").strip())
        },
        {
            "label": "Store address added",
            "done": bool((store.get("address") or "").strip())
        },
        {
            "label": "Pincode added",
            "done": bool((store.get("pincode") or "").strip())
        },
        {
            "label": "Latitude and longitude added",
            "done": store.get("latitude") is not None and store.get("longitude") is not None
        },
        {
            "label": "Store description added",
            "done": bool((store.get("description") or "").strip())
        },

{
    "label": "Store intro line added",
    "done": bool((store.get("profile_intro") or "").strip())
},
{
    "label": "Store banner uploaded",
    "done": bool((store.get("banner_path") or "").strip())
},

        {
            "label": "Store logo uploaded",
            "done": bool((store.get("logo_path") or "").strip())
        },
        {
            "label": "Operating time added",
            "done": bool((store.get("opening_time") or "").strip()) and bool((store.get("closing_time") or "").strip())
        },
        {
            "label": "Working days selected",
            "done": bool(store.get("working_days"))
        },
        {
            "label": "Notifications configured",
            "done": bool(notification_settings.get("enabled"))
        },
        {
            "label": "Store account active",
            "done": bool(store.get("is_active"))
        }
    ]

    done = sum(1 for item in checklist if item["done"])
    total = len(checklist)
    percent = round((done / total) * 100) if total else 0

    return {
        "profile_checklist": checklist,
        "profile_completion": {
            "done": done,
            "total": total,
            "percent": percent
        },
        "notification_settings": notification_settings
    }
