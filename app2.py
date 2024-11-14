from fastapi import FastAPI, HTTPException
import mysql.connector
from mysql.connector import Error
import requests
from datetime import datetime, time, timedelta
import asyncio
import json
from fastapi.middleware.cors import CORSMiddleware

# Database configuration
DATABASE_CONFIG = {
    'host': 'localhost',
    'user': 'root',
    'password': 'root',
    'database': 'meeting'
}

# Bolna API configuration
BOLNA_API_KEY = "bn-c6ad696548d646409cca9664e7bfafb2"
BOLNA_API_URL = "https://api.bolna.dev/call"
AGENT_ID = "d41f4c32-478e-406c-afad-fc3e412b5af9"

# Create a FastAPI instance
app = FastAPI()

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Database connection function
def get_db_connection():
    try:
        connection = mysql.connector.connect(**DATABASE_CONFIG)
        return connection
    except Error as e:
        print(f"Error: {e}")
        return None

# Function to initiate a call using the Bolna API
def initiate_call(recipient_phone_number):
    # Get name from database using phone number
    connection = get_db_connection()
    name = None  # Initialize as None instead of "Unknown"
    
    if connection:
        try:
            cursor = connection.cursor(dictionary=True)
            cursor.execute("SELECT name FROM contacts WHERE phone_number = %s", (recipient_phone_number,))
            result = cursor.fetchone()
            if result and result['name']:
                name = result['name']
        finally:
            cursor.close()
            connection.close()

    headers = {
        "Authorization": f"Bearer {BOLNA_API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "agent_id": AGENT_ID,
        "recipient_phone_number": recipient_phone_number,
        "user_data": {
            "variable1": "Jagadish",
            "variable2": name,  # Will be null if no name is found
        }
    }
    
    try:
        response = requests.post(BOLNA_API_URL, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as e:
        print(f"Failed to initiate call for {recipient_phone_number}: {e}")
        return {"status": "Failed", "error": str(e)}

# Function to schedule a call based on a specified datetime
async def schedule_call(phone_number, scheduled_datetime):
    delay = (scheduled_datetime - datetime.now()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)  # Wait until the scheduled time
        response = initiate_call(phone_number)
        return {
            "phone_number": phone_number,
            "status": response.get("status", "Unknown"),
            "call_id": response.get("call_id", "N/A")
        }
    else:
        return {
            "phone_number": phone_number,
            "status": "Skipped - Scheduled time is in the past",
            "call_id": "N/A"
        }

@app.post("/make-calls")
async def make_calls():
    connection = get_db_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT phone_number, scheduled_date, scheduled_time FROM contacts")
        contacts = cursor.fetchall()
        
        if not contacts:
            return {"message": "No contacts found in the database."}

        call_results = []
        current_datetime = datetime.now()

        for contact in contacts:
            if contact["scheduled_date"] is None or contact["scheduled_time"] is None:
                call_results.append({
                    "phone_number": contact["phone_number"],
                    "status": "Skipped - Missing scheduled date or time",
                    "call_id": "N/A"
                })
                continue

            # Convert scheduled_time if it's a timedelta
            scheduled_time = (datetime.min + contact["scheduled_time"]).time() \
                             if isinstance(contact["scheduled_time"], timedelta) \
                             else contact["scheduled_time"]

            scheduled_datetime = datetime.combine(contact["scheduled_date"], scheduled_time)

            # Check if the call is scheduled for the future
            if scheduled_datetime > current_datetime:
                # Schedule the call for future execution
                asyncio.create_task(schedule_call(contact["phone_number"], scheduled_datetime))
                
                call_results.append({
                    "phone_number": contact["phone_number"],
                    "status": "Scheduled",
                    "scheduled_for": scheduled_datetime.isoformat(),
                    "call_id": "Pending"
                })
            else:
                # Past datetime - skip the call
                call_results.append({
                    "phone_number": contact["phone_number"],
                    "status": "Skipped - Scheduled time is in the past",
                    "scheduled_for": scheduled_datetime.isoformat(),
                    "call_id": "N/A"
                })
        
        return {
            "message": "Call processing completed.",
            "call_results": call_results
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")

    finally:
        cursor.close()
        connection.close()

@app.post("/schedule-medicine-calls")
async def schedule_medicine_calls():
    connection = get_db_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT id, _creationTime, medicines, patient_phone 
            FROM prescriptions 
            WHERE medicineRemainder = TRUE
        """)
        prescriptions = cursor.fetchall()
        
        if not prescriptions:
            return {"message": "No prescriptions found requiring calls."}

        call_results = []
        current_datetime = datetime.now()

        for prescription in prescriptions:
            medicines_list = prescription.get('medicines')
            if not medicines_list:
                continue

            if isinstance(medicines_list, str):
                medicines_list = json.loads(medicines_list)
            
            for medicine in medicines_list:
                duration_days = int(medicine.get('durationDays', 0))
                timing_str = medicine.get('timing', '')
                
                # Split multiple times
                time_parts = timing_str.lower().split('and')
                times = []  # Will store tuples of (hour, minute)
                
                for part in time_parts:
                    part = part.strip()
                    
                    try:
                        # First, try to find any time pattern in the string
                        words = part.split()
                        time_str = None
                        
                        # Look for time pattern (either HH:MM or H)
                        for word in words:
                            if ':' in word or word.replace(':', '').isdigit():
                                time_str = word
                                break
                        
                        if time_str:
                            # Handle HH:MM format
                            if ':' in time_str:
                                hour, minute = map(int, time_str.split(':'))
                            else:
                                # Handle single number format
                                hour = int(time_str)
                                minute = 0
                            
                            # Convert to 24-hour format if needed
                            if 'pm' in part.lower() and hour != 12:
                                hour += 12
                            elif 'am' in part.lower() and hour == 12:
                                hour = 0
                            
                            # Validate hour and minute
                            if 0 <= hour <= 23 and 0 <= minute <= 59:
                                times.append((hour, minute))
                    
                    except ValueError as e:
                        print(f"Error parsing time from '{part}': {str(e)}")
                        continue
                
                if not times:
                    continue

                creation_date = prescription['_creationTime'].date()
                
                for day in range(duration_days):
                    call_date = creation_date + timedelta(days=day)
                    
                    for hour, minute in times:
                        call_time = time(hour=hour, minute=minute)
                        scheduled_datetime = datetime.combine(call_date, call_time)

                        if scheduled_datetime > current_datetime:
                            asyncio.create_task(schedule_call(
                                prescription['patient_phone'],
                                scheduled_datetime
                            ))
                            
                            call_results.append({
                                "prescription_id": prescription['id'],
                                "medicine_name": medicine.get('name'),
                                "phone_number": prescription['patient_phone'],
                                "status": "Scheduled",
                                "scheduled_for": scheduled_datetime.isoformat()
                            })

        return {
            "message": "Medicine reminder calls scheduled successfully.",
            "call_results": call_results
        }

    except Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        cursor.close()
        connection.close()

# Add new class for managing appointments using contacts table
class AppointmentManager:
    def __init__(self):
        self.connection = get_db_connection()
    
    def check_slot_availability(self, date, time):
        if not self.connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        try:
            cursor = self.connection.cursor(dictionary=True)
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM contacts 
                WHERE scheduled_date = %s AND scheduled_time = %s
            """, (date, time))
            result = cursor.fetchone()
            return result['count'] == 0
        except Error as e:
            raise HTTPException(status_code=500, detail=f"Database error: {e}")
        finally:
            cursor.close()

    def get_available_slots(self, date):
        if not self.connection:
            raise HTTPException(status_code=500, detail="Database connection failed")
        
        try:
            cursor = self.connection.cursor(dictionary=True)
            # Get all booked slots for the date
            cursor.execute("""
                SELECT scheduled_time 
                FROM contacts 
                WHERE scheduled_date = %s
            """, (date,))
            booked_slots = {row['scheduled_time'] for row in cursor.fetchall()}
            
            # Define all possible slots (9 AM to 5 PM)
            all_slots = [f"{hour:02d}:00" for hour in range(9, 17)]
            
            # Return available slots
            available_slots = [slot for slot in all_slots if slot not in booked_slots]
            return available_slots
        except Error as e:
            raise HTTPException(status_code=500, detail=f"Database error: {e}")
        finally:
            cursor.close()

# Add new endpoints for Bolna AI to call
@app.get("/check-slot")
async def check_slot(date: str, time: str):
    """
    Endpoint for Bolna AI to check if a specific slot is available
    """
    appointment_manager = AppointmentManager()
    try:
        is_available = appointment_manager.check_slot_availability(date, time)
        available_slots = appointment_manager.get_available_slots(date) if not is_available else []
        
        return {
            "available": is_available,
            "requested_slot": time,
            "date": date,
            "alternative_slots": available_slots if not is_available else [],
            "message": "Slot is available" if is_available else "Slot is not available"
        }
    finally:
        if appointment_manager.connection:
            appointment_manager.connection.close()

@app.get("/available-slots")
async def get_available_slots(date: str):
    """
    Endpoint for Bolna AI to get all available slots for a specific date
    """
    appointment_manager = AppointmentManager()
    try:
        available_slots = appointment_manager.get_available_slots(date)
        return {
            "date": date,
            "available_slots": available_slots,
            "message": f"Found {len(available_slots)} available slots"
        }
    finally:
        if appointment_manager.connection:
            appointment_manager.connection.close()

@app.post("/book-appointment")
async def book_appointment(date: str, time: str, user_phone: str, name: str = None):
    """
    Endpoint for Bolna AI to book an appointment
    """
    connection = get_db_connection()
    if connection is None:
        raise HTTPException(status_code=500, detail="Database connection failed")
    
    try:
        cursor = connection.cursor()
        
        # First check if slot is available
        appointment_manager = AppointmentManager()
        if not appointment_manager.check_slot_availability(date, time):
            return {
                "success": False,
                "message": "Slot is no longer available"
            }
        
        # Book the appointment by inserting into contacts
        cursor.execute("""
            INSERT INTO contacts (phone_number, name, scheduled_date, scheduled_time)
            VALUES (%s, %s, %s, %s)
        """, (user_phone, name, date, time))
        
        connection.commit()
        
        return {
            "success": True,
            "message": f"Appointment booked successfully for {date} at {time}",
            "appointment_details": {
                "date": date,
                "time": time,
                "user_phone": user_phone,
                "name": name
            }
        }
    except Error as e:
        return {
            "success": False,
            "message": f"Failed to book appointment: {str(e)}"
        }
    finally:
        cursor.close()
        connection.close()

@app.get("/")
async def root():
    return {"message": "API is working"}

@app.get("/test")
async def test():
    return {"message": "API is working!"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
