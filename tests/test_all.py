import requests
import json
import time

BASE_URL = "http://127.0.0.1:8000/api"
test_email = f"test_{int(time.time())}@example.com"
token = ""

def print_result(name, res):
    print(f"[{res.status_code}] {name}")
    if res.status_code >= 400:
        print(res.text)

# 1. Auth Flow
print("\n--- AUTH FLOW ---")
r = requests.post(f"{BASE_URL}/auth/send-otp", json={"email": test_email})
print_result("Send OTP", r)

# Direct DB access to get OTP
from pymongo import MongoClient
import os
client = MongoClient("mongodb+srv://sagnikm997_db_user:qxNZsxzxx7FXHqSD@cluster0.xz63ghm.mongodb.net/")
db = client.eventora_db
otp_doc = db.otps.find_one({"email": test_email})
otp = otp_doc["otp"]

r = requests.post(f"{BASE_URL}/auth/verify-otp", json={"email": test_email, "otp": otp})
print_result("Verify OTP", r)
reg_token = r.json().get("registration_token")

# Register details
files = {"govt_id_proof": ("id.png", b"dummy_content", "image/png")}
form = {
    "name": "Test User",
    "phone_number": "1234567890",
    "date_of_birth": "1995-01-01",
    "address": "123 Test St",
}
r = requests.post(f"{BASE_URL}/auth/register-details", data=form, files=files, headers={"authorization": f"Bearer {reg_token}"})
print_result("Register Details", r)
token = r.json().get("access_token")
headers = {"Authorization": f"Bearer {token}"}

# 2. Profile & Dashboard
print("\n--- PROFILE & DASHBOARD ---")
r = requests.get(f"{BASE_URL}/profile/me", headers=headers)
print_result("Get Profile", r)

r = requests.put(f"{BASE_URL}/profile/me", data={"name": "Updated User"}, headers=headers)
print_result("Update Profile", r)

r = requests.get(f"{BASE_URL}/profile/dashboard", headers=headers)
print_result("Get Dashboard", r)

# 3. Activities
print("\n--- ACTIVITIES ---")
r = requests.get(f"{BASE_URL}/activities/events")
print_result("Get Events", r)
r = requests.get(f"{BASE_URL}/activities/tickets")
print_result("Get Tickets", r)
r = requests.get(f"{BASE_URL}/activities/competitions")
print_result("Get Competitions", r)
r = requests.get(f"{BASE_URL}/activities/courses")
print_result("Get Courses", r)
r = requests.get(f"{BASE_URL}/activities/merchandise")
print_result("Get Merchandise", r)

# 4. Create dummy data for actions
event_id = str(db.events.insert_one({"title": "Test Event", "capacity": 100, "registered_count": 0}).inserted_id)
ticket_id = str(db.tickets.insert_one({"event_id": event_id, "type": "VIP", "price": 100, "available": 50}).inserted_id)
merch_id = str(db.merchandise.insert_one({"name": "T-Shirt", "price": 20, "stock": 100}).inserted_id)

# 5. Actions
print("\n--- ACTIONS ---")
r = requests.post(f"{BASE_URL}/registrations/", json={"activity_id": event_id, "activity_type": "event"}, headers=headers)
print_result("Register Event", r)
r = requests.post(f"{BASE_URL}/registrations/", json={"activity_id": event_id, "activity_type": "event"}, headers=headers)
print_result("Duplicate Register Event (should fail)", r)

r = requests.post(f"{BASE_URL}/bookings/", json={"ticket_id": ticket_id, "quantity": 2}, headers=headers)
print_result("Book Ticket", r)

r = requests.post(f"{BASE_URL}/merchandise/", json={"merchandise_id": merch_id, "quantity": 1, "shipping_address": "Home"}, headers=headers)
print_result("Order Merchandise", r)

# Get Dashboard again
r = requests.get(f"{BASE_URL}/profile/dashboard", headers=headers)
print_result("Get Dashboard Again", r)
dash = r.json()
print("Registered Events:", len(dash['events']))
print("Booked Tickets:", len(dash['tickets']))
print("Merch Orders:", len(dash['merchandise_orders']))

print("\n--- ALL TESTS COMPLETED ---")
