import requests
import json

BASE = "http://127.0.0.1:8000/api"
admin_token = ""
errors = []
passed = 0

def test(name, method, url, expected_status=200, json_data=None, headers=None):
    global passed
    try:
        if method == "GET":
            r = requests.get(url, headers=headers)
        elif method == "POST":
            r = requests.post(url, json=json_data, headers=headers)
        elif method == "PUT":
            r = requests.put(url, json=json_data, headers=headers)
        elif method == "PATCH":
            r = requests.patch(url, json=json_data, headers=headers)
        elif method == "DELETE":
            r = requests.delete(url, headers=headers)
        
        if r.status_code == expected_status:
            print(f"  PASS [{r.status_code}] {name}")
            passed += 1
            return r
        else:
            print(f"  FAIL [{r.status_code}] {name} (expected {expected_status})")
            print(f"       {r.text[:200]}")
            errors.append(name)
            return r
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        errors.append(name)
        return None

# ════════════════════════════════════════
print("\n=== PHASE 1: SUPER ADMIN BOOTSTRAP ===")
r = test("Bootstrap Admin", "POST", f"{BASE}/admin/bootstrap")

print("\n=== PHASE 2: DUPLICATE BOOTSTRAP (should fail) ===")
test("Duplicate Bootstrap", "POST", f"{BASE}/admin/bootstrap", expected_status=409)

print("\n=== PHASE 3: ADMIN LOGIN ===")
r = test("Admin Login", "POST", f"{BASE}/admin/login", json_data={
    "email": "your-admin-email@example.com",
    "password": "replace_with_a_strong_unique_password"
})
if r and r.status_code == 200:
    admin_token = r.json()["access_token"]
    print(f"       Token obtained: {admin_token[:20]}...")

headers = {"Authorization": f"Bearer {admin_token}"}

print("\n=== PHASE 4: ADMIN ME ===")
test("Admin Me", "GET", f"{BASE}/admin/me", headers=headers)

print("\n=== PHASE 5: WRONG LOGIN (should fail) ===")
test("Wrong Password", "POST", f"{BASE}/admin/login", expected_status=401, json_data={
    "email": "your-admin-email@example.com", "password": "wrongpassword"
})

# ════════════════════════════════════════
print("\n=== PHASE 6: WEBSITE SETTINGS ===")
test("Update Settings", "PUT", f"{BASE}/admin/website-settings", headers=headers, json_data={
    "brand_name": "Eventora",
    "tagline": "Your Event, Our Platform",
    "logo_url": "https://example.com/logo.png",
    "contact_email": "hello@eventora.com",
    "contact_phone": "+91-9876543210",
    "social_media": {"facebook": "https://fb.com/eventora", "instagram": "https://ig.com/eventora"}
})
test("Get Settings", "GET", f"{BASE}/admin/website-settings", headers=headers)

# ════════════════════════════════════════
print("\n=== PHASE 7: EVENTS CRUD ===")
r = test("Create Event", "POST", f"{BASE}/admin/events", headers=headers, json_data={
    "title": "Tech Summit 2026", "description": "Annual tech conference",
    "date": "2026-12-15", "venue": "Convention Center", "capacity": 500,
    "price": 299.0, "registration_deadline": "2026-12-10"
})
event_id = r.json()["_id"] if r and r.status_code == 200 else None

if event_id:
    test("Update Event", "PUT", f"{BASE}/admin/events/{event_id}", headers=headers, json_data={"title": "Tech Summit 2026 v2"})
    test("Publish Event", "PATCH", f"{BASE}/admin/events/{event_id}/publish", headers=headers, json_data={"is_published": True})
    test("Set Visibility", "PATCH", f"{BASE}/admin/events/{event_id}/visibility", headers=headers, json_data={"is_visible": True})
    test("Reorder Event", "PATCH", f"{BASE}/admin/events/{event_id}/reorder", headers=headers, json_data={"display_order": 1})
    test("List Events", "GET", f"{BASE}/admin/events", headers=headers)

# ════════════════════════════════════════
print("\n=== PHASE 8: LIVE EVENTS ===")
r = test("Create Live Event", "POST", f"{BASE}/admin/live-events", headers=headers, json_data={
    "title": "Live Keynote", "stream_url": "rtmp://private.stream/key123",
    "public_url": "https://youtube.com/live/abc", "start_time": "2026-12-15T10:00:00"
})
le_id = r.json()["_id"] if r and r.status_code == 200 else None
if le_id:
    test("Publish Live Event", "PATCH", f"{BASE}/admin/live-events/{le_id}/publish", headers=headers, json_data={"is_published": True})

# ════════════════════════════════════════
print("\n=== PHASE 9: BANNERS ===")
r = test("Create Banner", "POST", f"{BASE}/admin/banners", headers=headers, json_data={
    "title": "Sponsor Banner", "image_url": "https://example.com/banner.jpg",
    "sponsor_name": "TechCorp", "target_url": "https://techcorp.com",
    "is_published": True, "is_visible": True
})
banner_id = r.json()["_id"] if r and r.status_code == 200 else None

# ════════════════════════════════════════
print("\n=== PHASE 10: TESTIMONIALS ===")
r = test("Create Testimonial", "POST", f"{BASE}/admin/testimonials", headers=headers, json_data={
    "person_name": "John Doe", "role": "CEO", "message": "Amazing platform!",
    "is_approved": True, "is_published": True
})
test_id = r.json()["_id"] if r and r.status_code == 200 else None

# ════════════════════════════════════════
print("\n=== PHASE 11: COURSES ===")
r = test("Create Course", "POST", f"{BASE}/admin/courses", headers=headers, json_data={
    "title": "Python Masterclass", "description": "Learn Python end to end",
    "instructor_name": "Dr. Smith", "price": 499.0, "course_type": "course",
    "is_published": True, "is_visible": True
})
r2 = test("Create Workshop", "POST", f"{BASE}/admin/courses", headers=headers, json_data={
    "title": "UI/UX Design Workshop", "description": "Hands-on design",
    "instructor_name": "Jane Doe", "price": 199.0, "course_type": "workshop",
    "is_published": True, "is_visible": True
})

# ════════════════════════════════════════
print("\n=== PHASE 12: TICKETS ===")
if event_id:
    r = test("Create Ticket", "POST", f"{BASE}/admin/tickets", headers=headers, json_data={
        "event_id": event_id, "type": "VIP", "price": 999.0, "total_quantity": 100
    })

# ════════════════════════════════════════
print("\n=== PHASE 13: CONTEST PROMOTIONS ===")
r = test("Create Contest Promo", "POST", f"{BASE}/admin/contest-promotions", headers=headers, json_data={
    "title": "Vote for Best Speaker", "description": "Cast your vote!",
    "cta_text": "Vote Now", "is_published": True, "is_visible": True
})

# ════════════════════════════════════════
print("\n=== PHASE 14: APP PROMOTION ===")
test("Update App Promo", "PUT", f"{BASE}/admin/app-promotion", headers=headers, json_data={
    "section_title": "Download Eventora App",
    "description": "Get the best event experience",
    "android_url": "https://play.google.com/store/apps/eventora",
    "ios_url": "https://apps.apple.com/eventora",
    "cta_text": "Download Now",
    "is_enabled": True, "is_published": True
})

# ════════════════════════════════════════
print("\n=== PHASE 15: IMPORTANT LINKS ===")
test("Create Link", "POST", f"{BASE}/admin/important-links", headers=headers, json_data={
    "label": "Privacy Policy", "url": "https://eventora.com/privacy", "category": "legal"
})
test("Create Link 2", "POST", f"{BASE}/admin/important-links", headers=headers, json_data={
    "label": "Terms of Service", "url": "https://eventora.com/terms", "category": "legal"
})

# ════════════════════════════════════════
print("\n=== PHASE 16: AUDIT LOGS ===")
test("Get Audit Logs", "GET", f"{BASE}/admin/audit-logs", headers=headers)

# ════════════════════════════════════════
print("\n=== PHASE 17: PUBLIC HOMEPAGE API ===")
r = test("Public Homepage", "GET", f"{BASE}/v1/homepage")
if r and r.status_code == 200:
    data = r.json()
    print(f"       Brand: {data.get('brand', {}).get('name')}")
    print(f"       Events: {len(data.get('upcoming_events', []))}")
    print(f"       Live Events: {len(data.get('live_events', []))}")
    print(f"       Banners: {len(data.get('sponsored_banners', []))}")
    print(f"       Testimonials: {len(data.get('testimonials', []))}")
    print(f"       Courses: {len(data.get('courses', []))}")
    print(f"       Workshops: {len(data.get('workshops', []))}")
    print(f"       Contest Promos: {len(data.get('contest_promotions', []))}")
    print(f"       App Promo: {'Yes' if data.get('app_promotion') else 'No'}")
    print(f"       Links: {len(data.get('important_links', []))}")

print("\n=== PHASE 18: INDIVIDUAL PUBLIC ENDPOINTS ===")
test("Public Settings", "GET", f"{BASE}/v1/website-settings")
test("Public Upcoming Events", "GET", f"{BASE}/v1/events/upcoming")
test("Public Live Events", "GET", f"{BASE}/v1/events/live")
test("Public Banners", "GET", f"{BASE}/v1/banners")
test("Public Testimonials", "GET", f"{BASE}/v1/testimonials")
test("Public Courses", "GET", f"{BASE}/v1/courses")
test("Public Workshops", "GET", f"{BASE}/v1/workshops")
test("Public Contest Promos", "GET", f"{BASE}/v1/contest-promotions")
test("Public App Promo", "GET", f"{BASE}/v1/app-promotion")
test("Public Contact", "GET", f"{BASE}/v1/contact-info")
test("Public Links", "GET", f"{BASE}/v1/important-links")

# ════════════════════════════════════════
print("\n=== PHASE 19: SECURITY CHECKS ===")
test("No Token - Dashboard", "GET", f"{BASE}/admin/events", expected_status=403)
test("No Token - Settings", "PUT", f"{BASE}/admin/website-settings", expected_status=403, json_data={"brand_name": "Hacked"})

# Check live event stream_url is hidden in public API
r = test("Public Live Events (no stream_url)", "GET", f"{BASE}/v1/events/live")
if r and r.status_code == 200:
    for le in r.json():
        if "stream_url" in le:
            print("  FAIL: stream_url exposed in public API!")
            errors.append("stream_url_exposed")
        else:
            print("  PASS: stream_url hidden in public API")

# ════════════════════════════════════════
print(f"\n{'='*50}")
print(f"RESULTS: {passed} passed, {len(errors)} failed")
if errors:
    print(f"Failed: {errors}")
else:
    print("ALL TESTS PASSED!")
print(f"{'='*50}")
