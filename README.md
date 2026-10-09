# Eventora — Event Management Platform Backend

![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)
![MongoDB](https://img.shields.io/badge/MongoDB-%234ea94b.svg?style=for-the-badge&logo=mongodb&logoColor=white)
![Python](https://img.shields.io/badge/python-3670A0?style=for-the-badge&logo=python&logoColor=ffdd54)

Eventora is a highly scalable, production-ready backend for a dynamic event management platform. It includes a fully functional User Dashboard, dynamic Content Management System (CMS), and robust Role-Based Access Control (Single Super Admin).

## 🚀 Key Features

### 1. 🛡️ Dynamic Website Content Management (Admin Panel)
- **Brand Information:** Manage company logo, name, tagline, and contact info.
- **Dynamic Events & Ticketing:** Create, publish, update, and soft-delete events and tickets. 
- **Live Events:** Manage stream URLs (hidden from public APIs) and visibility.
- **Sponsored Banners & Testimonials:** Add, reorder, and approve banners and testimonials.
- **Courses & Promotions:** Manage workshops, app promotions, and voting competitions.
- **Important Links:** Dynamically generate footer and social links.

### 2. 🔐 Super Admin Security
- **Single Super Admin:** Bootstrap securely from `.env`. No duplicate admins possible.
- **Bcrypt Hashing:** Passwords securely hashed with bcrypt.
- **Rate Limiting:** Protects against brute-force attacks via `slowapi`.
- **Audit Logging:** Logs all admin actions (create, update, publish, delete) safely without exposing secrets.

### 3. 👤 User Authentication & Profile Dashboard
- **OTP Verification:** Email verification powered by Resend API.
- **Full Profile Dashboard:** End-to-end integration for users to view their **My Tickets, Competitions, Ranks, Certificates, Rewards/Coins, and Merchandise**.

### 4. 🗄️ Media & Data Handling
- **Cloudinary Integration:** Direct secure uploads for user profiles, govt IDs, event banners, and logos.
- **MongoDB Async (Motor):** High-performance non-blocking queries.

---

## 🛠️ Technology Stack
- **Language:** Python 3.12+
- **Framework:** FastAPI
- **Database:** MongoDB Atlas (Motor Asyncio)
- **Authentication:** PyJWT, Bcrypt
- **Package Manager:** `uv`
- **File Storage:** Cloudinary
- **Emails:** Resend API

---

## ⚙️ Installation & Setup

### 1. Clone the repository
```bash
git clone https://github.com/ulmind-com/Event_Management_Backend.git
cd Event_Management_Backend
```

### 2. Install dependencies using `uv`
Make sure `uv` is installed, then run:
```bash
uv sync
```

### 3. Environment Variables
Create a `.env` file in the root directory and add your credentials:
```env
# Database
MONGODB_URI=mongodb+srv://<username>:<password>@cluster.mongodb.net/

# Cloudinary (Media Storage)
CLOUDINARY_CLOUD_NAME=your_cloud_name
CLOUDINARY_API_KEY=your_api_key
CLOUDINARY_API_SECRET=your_api_secret

# Resend (Emails)
RESEND_API_KEY=re_your_api_key
MAIL_ADDRESS=noreply@yourdomain.com

# JWT & Security
SECRET_KEY=your_super_secret_key_here
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
ADMIN_TOKEN_EXPIRE_MINUTES=60
CORS_ORIGINS=*

# Super Admin Bootstrap 
SUPER_ADMIN_EMAIL=admin@eventora.com
SUPER_ADMIN_PASSWORD=your_super_strong_password
```

### 4. Run the Server
```bash
uv run uvicorn app.main:app --reload --port 8000
```
Swagger UI will be available at: `http://127.0.0.1:8000/docs`

---

## 👨‍💻 Initializing the Super Admin

1. Ensure `SUPER_ADMIN_EMAIL` and `SUPER_ADMIN_PASSWORD` are set in `.env`.
2. Open Swagger UI (`/docs`).
3. Call `POST /api/admin/bootstrap`.
4. Your admin is now created! 
5. Login via `POST /api/admin/login` and paste the token into the 🔓 **Authorize** button to start managing the CMS.

---

## 📡 API Overview

- **`GET /api/v1/homepage`** - Returns all dynamic homepage content in one call.
- **`/api/admin/*`** - Admin CMS routes (requires Admin Token).
- **`/api/auth/*`** - User registration, OTP, and JWT handling.
- **`/api/profile/dashboard`** - Protected route for user-specific data and history.

---
*Built with ❤️ for Eventora.*
