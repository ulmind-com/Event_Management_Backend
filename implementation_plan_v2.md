# Implementation Plan: Eventora Backend Enhancement

## 1. Database Collections (app/core/database.py)
Update the database connection to include references to collections for:
- `events`
- `tickets`
- `competitions`
- `courses`
- `merchandise`
- `registrations` (for events, competitions, courses)
- `bookings` (for tickets)
- `orders` (for merchandise)
- `certificates`
- `rewards`

## 2. Pydantic Schemas (app/schemas/)
Create schemas for request validation and response formatting.
- `profile.py`: ProfileUpdate, DashboardResponse
- `activity.py`: Event, Competition, Course, Merchandise, Ticket
- `action.py`: RegistrationReq, BookingReq, OrderReq

## 3. API Routes (app/api/routes/)
### 3.1 profile.py
- `GET /api/profile/me`: View own profile (Moved/Updated from auth.py)
- `PUT /api/profile/me`: Update profile (name, phone, address, profile_photo)
- `GET /api/profile/dashboard`: Returns My Events, My Tickets, My Competitions, My Courses, My Orders, Certificates, Rewards

### 3.2 activities.py
- `GET /api/activities/events`
- `GET /api/activities/competitions`
- `GET /api/activities/courses`
- `GET /api/activities/merchandise`
- `GET /api/activities/tickets`
(All support pagination and searching)

### 3.3 registration.py
- `POST /api/registrations/event`: Register for event
- `DELETE /api/registrations/event/{id}`: Cancel
- `POST /api/registrations/competition`: Register for competition
- `POST /api/registrations/course`: Enroll in course

### 3.4 bookings.py
- `POST /api/bookings/ticket`: Book a ticket
- `GET /api/bookings/ticket`: View booked tickets
- `DELETE /api/bookings/ticket/{id}`: Cancel ticket

### 3.5 merchandise.py
- `POST /api/merchandise/order`: Create order
- `GET /api/merchandise/order`: View orders

### 3.6 rewards.py
- `GET /api/rewards/certificates`
- `GET /api/rewards/coins`
- `GET /api/rewards/history`

## 4. Authentication & Security
- Add dependencies in `app/api/deps.py` to extract current authenticated user using `decode_token`.
- Enforce `email_verified` and `registration_complete` for accessing activities.

## 5. Testing
- Create a `tests` folder.
- Write Pytest functions to test the full flow end-to-end.

## 6. Execution Steps
1. Create `deps.py` for shared auth dependencies.
2. Update `app/core/database.py`.
3. Create schemas in `app/schemas/`.
4. Create new routers in `app/api/routes/`.
5. Include routers in `app/api/api.py`.
6. Write test scripts to populate data and test interactions.
