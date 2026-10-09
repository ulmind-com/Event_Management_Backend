import requests
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings
from app.core.security import create_access_token, get_password_hash
from time import sleep

BASE = "http://127.0.0.1:8000/api"

async def setup():
    client = AsyncIOMotorClient(settings.MONGODB_URI)
    db = client.eventora_db
    
    # 1. Clear relevant collections
    await db["events"].delete_many({"title": "Booking Test Event"})
    await db["tickets"].delete_many({})
    await db["bookings"].delete_many({})
    await db["issued_tickets"].delete_many({})
    
    # Create Mock Admin
    await db["admin_accounts"].update_one(
        {"email": "admin@eventora.com"}, 
        {"$set": {"email": "admin@eventora.com", "password_hash": get_password_hash("test")}},
        upsert=True
    )
    
    # Create Mock User
    await db["users"].update_one(
        {"email": "sagnik@example.com"},
        {"$set": {"email": "sagnik@example.com", "full_name": "Sagnik", "password_hash": get_password_hash("test"), "is_verified": True, "registration_complete": True}},
        upsert=True
    )
    
    # Generate tokens
    admin_token = create_access_token({"sub": "admin@eventora.com", "type": "admin"})
    user_token = create_access_token({"sub": "sagnik@example.com", "type": "user"})
    
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    user_headers = {"Authorization": f"Bearer {user_token}"}
    
    return admin_headers, user_headers

def test(name, method, url, expected_status=200, json_data=None, headers=None):
    try:
        if method == "GET":
            r = requests.get(url, headers=headers)
        elif method == "POST":
            r = requests.post(url, json=json_data, headers=headers)
        
        if r.status_code == expected_status:
            print(f"  PASS [{r.status_code}] {name}")
            return r
        else:
            print(f"  FAIL [{r.status_code}] {name} (expected {expected_status})")
            print(f"       {r.text[:200]}")
            return r
    except Exception as e:
        print(f"  ERROR {name}: {e}")
        return None

async def run_tests():
    admin_headers, user_headers = await setup()
    
    # Create Event
    r_ev = test("Create Event", "POST", f"{BASE}/admin/events", headers=admin_headers, json_data={
        "title": "Booking Test Event", "description": "Test", "date": "2026-10-10", "venue": "Online",
        "is_published": True, "registration_enabled": True, "ticket_enabled": True
    })
    event_id = r_ev.json()["_id"] if r_ev and r_ev.status_code == 200 else ""
    
    # Create Tickets
    r_t1 = test("Create Paid Ticket", "POST", f"{BASE}/admin/tickets", headers=admin_headers, json_data={
        "event_id": event_id, "type": "VIP", "price": 500.0, "total_quantity": 10, "available": 10
    })
    paid_ticket_id = r_t1.json()["_id"] if r_t1 and r_t1.status_code == 200 else ""
    
    r_t2 = test("Create Free Ticket", "POST", f"{BASE}/admin/tickets", headers=admin_headers, json_data={
        "event_id": event_id, "type": "General", "price": 0.0, "total_quantity": 5, "available": 5
    })
    free_ticket_id = r_t2.json()["_id"] if r_t2 and r_t2.status_code == 200 else ""
    
    print("\n--- FREE TICKET FLOW ---")
    r_free = test("Book Free Ticket", "POST", f"{BASE}/bookings/", headers=user_headers, json_data={
        "event_id": event_id, "ticket_id": free_ticket_id, "quantity": 2, "holder_names": ["Alice", "Bob"]
    })
    
    print("\n--- PAID TICKET FLOW ---")
    r_paid = test("Book Paid Ticket", "POST", f"{BASE}/bookings/", headers=user_headers, json_data={
        "event_id": event_id, "ticket_id": paid_ticket_id, "quantity": 3
    })
    paid_booking_id = r_paid.json().get("booking_id") if r_paid and r_paid.status_code == 200 else ""
    
    print("\n--- OVERSELL PREVENTION ---")
    test("Oversell Prevented", "POST", f"{BASE}/bookings/", expected_status=409, headers=user_headers, json_data={
        "event_id": event_id, "ticket_id": paid_ticket_id, "quantity": 9
    })
    
    print("\n--- VERIFY PAYMENT ---")
    test("Verify Payment", "POST", f"{BASE}/payments/verify-mock/{paid_booking_id}", headers=user_headers, json_data={
        "transaction_id": "tx_mock_123", "status": "success"
    })
    
    sleep(1.5)
    
    print("\n--- MY TICKETS ---")
    r_tickets = test("Get My Tickets", "GET", f"{BASE}/bookings/my-tickets", headers=user_headers)
    tickets = r_tickets.json() if r_tickets else []
    print(f"Total Digital Tickets: {len(tickets)}")
    
    if tickets:
        qr_token = tickets[0]["qr_token"]
        print("\n--- ADMIN VERIFY & CHECK-IN ---")
        test("Verify QR", "POST", f"{BASE}/admin/tickets/verify", headers=admin_headers, json_data={"qr_token": qr_token})
        test("Check-in Ticket", "POST", f"{BASE}/admin/tickets/check-in", headers=admin_headers, json_data={"qr_token": qr_token})
        test("Duplicate Check-in Prevented", "POST", f"{BASE}/admin/tickets/check-in", expected_status=400, headers=admin_headers, json_data={"qr_token": qr_token})

asyncio.run(run_tests())
