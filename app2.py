from fastapi import FastAPI, HTTPException
import mysql.connector
from mysql.connector import Error
import requests
from datetime import datetime, time, timedelta
import asyncio
import json

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
