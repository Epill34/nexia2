from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import os
import logging
import re
import json
from groq import Groq

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ai-model-service")

app = FastAPI(title="Nexia AI Model Service")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

try:
    client = Groq(api_key=os.environ.get("GROQ_API_KEY"))
except TypeError:
    # For older groq client versions
    import httpx
    client = Groq(
        api_key=os.environ.get("GROQ_API_KEY"),
        http_client=httpx.Client()
    )

# Store customer-specific system prompts
customer_prompts = {}

# Default prompt template (similar to your original system prompt)
DEFAULT_SYSTEM_PROMPT = """SYSTEM PROMPT FOR NEXIA AI RECEPTIONIST

1. CORE IDENTITY & BASICS
------------------------
Role: Nexia - Advanced AI receptionist for [BUSINESS_NAME]
Languages: English/Spanish and others
Response Format: 
- One short sentence only
- Must end with a question
- Natural conversational tone (phone call style, not email)

2. BRACKET COMMANDS
------------------
[call_ended] - Use when detecting goodbye/end of call
[message] - Use for answering machines
[note] - For important information (callbacks, do-not-call requests, special instructions)
[CONFIRMED] - ONLY when ALL appointment details are finalized
[CALENDAR] [SCHEDULE] - Check availability for date/time

3. SCHEDULING PROTOCOL
---------------------
Available Times: 8:00 AM to 7 PM, 7 days/week

When Customer Mentions Date/Time:
1. Say: "Let me check the [CALENDAR] [SCHEDULE] for availability"
2. Then EITHER:
   - If available: "Perfect! You're scheduled for [date] at [Time]"
   - If not: "That time isn't available, let me check the [CALENDAR] [SCHEDULE] for the next opening"

4. BUSINESS SERVICES
------------------
[SERVICES_LIST]

5. CONVERSATION FLOW
------------------
GET NAME FIRST: "Can I get your full name please, and have you done business with us before?"
GET ADDRESS: "What's the complete address where we'll be providing service?"
VERIFY ADDRESS: Always repeat address with digits separated
GET SERVICE DETAILS: "What service would you like?"
QUOTE PRICE: Provide clear pricing based on quantity/service
UPSELL: "We also offer [seasonal service]. Would you like to add that today?"
SCHEDULE: "What date and time works best for you?"
CONFIRMATION: "Great! We've [CONFIRMED] your appointment for [day], [month] [date], at [time] at [complete address] for [list ALL services with quantities], estimated at $[total cost]."
END CALL: Thank customer, then [call_ended]

CRITICAL REQUIREMENTS:
ALWAYS get all customer information
ALWAYS attempt at least two upsells
ALWAYS include COMPLETE DETAILS in [CONFIRMED] tag
NEVER end call until all details are confirmed
ALWAYS ask if customer needs anything else before ending call
Use ONE SENTENCE responses ending with questions
"""

class ModelRequest(BaseModel):
    customer_id: str
    user_text: str
    conversation_history: List[Dict[str, Any]]
    customer_data: Optional[Dict[str, Any]] = None
    system_prompt: Optional[str] = None

@app.post("/generate_response")
async def generate_response(request: ModelRequest):
    """Generate AI response using Groq API"""
    try:
        # Get customer-specific system prompt or use provided/default
        system_prompt = request.system_prompt
        if not system_prompt:
            system_prompt = customer_prompts.get(request.customer_id, DEFAULT_SYSTEM_PROMPT)
            
            # Customize with business name if available
            if request.customer_data and "business_name" in request.customer_data:
                system_prompt = system_prompt.replace("[BUSINESS_NAME]", request.customer_data["business_name"])
            else:
                system_prompt = system_prompt.replace("[BUSINESS_NAME]", "Your Business")
                
            # Customize with services if available
            if request.customer_data and "services" in request.customer_data:
                services_text = "\n".join([f"- {service}" for service in request.customer_data["services"]])
                system_prompt = system_prompt.replace("[SERVICES_LIST]", services_text)
            else:
                system_prompt = system_prompt.replace("[SERVICES_LIST]", "- Standard service offerings")
        
        # Prepare messages array
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add conversation history (last 20 entries)
        recent_history = request.conversation_history[-20:] if request.conversation_history else []
        for entry in recent_history:
            messages.append({
                "role": entry.get("role", "user"),
                "content": entry.get("content", "")
            })
        
        # Add current user message if not already in history
        if not recent_history or recent_history[-1].get("role") != "user":
            messages.append({"role": "user", "content": request.user_text})
        
        # Add profile context if available
        if request.customer_data and "profile" in request.customer_data:
            profile_text = request.customer_data["profile"]
            # Add profile as a system message right before the user's latest message
            messages.insert(-1, {"role": "system", "content": f"CUSTOMER PROFILE:\n{profile_text}"})
        
        logger.info(f"Sending request to Groq for customer {request.customer_id}")
        
        # Send to Groq
        response = client.chat.completions.create(
            messages=messages,
            model="llama3-70b-8192",
            temperature=0.2
        )
        
        ai_text = response.choices[0].message.content if response.choices else ""
        
        # Parse brackets from the response
        brackets = parse_brackets(ai_text)
        
        logger.info(f"Groq response for customer {request.customer_id}: '{ai_text[:50]}...'")
        
        return {
            "response": ai_text,
            "customer_id": request.customer_id,
            "brackets": brackets
        }
        
    except Exception as e:
        logger.error(f"Error in generate_response: {e}")
        raise HTTPException(status_code=500, detail=f"AI model error: {str(e)}")

@app.post("/set_system_prompt")
async def set_system_prompt(customer_id: str, system_prompt: str):
    """Set a custom system prompt for a specific customer"""
    customer_prompts[customer_id] = system_prompt
    return {"status": "ok", "customer_id": customer_id}

def parse_brackets(text: str) -> dict:
    """Parse special brackets commands from AI response"""
    brackets = {
        'calendar_check': False,
        'message': None,
        'notes': [],
        'confirmed': False,
        'call_ended': False,
        'appointment_details': {},
        'callback_requested': False,
        'callback_data': None
    }

    # Check for calendar check
    if '[CALENDAR]' in text and '[SCHEDULE]' in text:
        brackets['calendar_check'] = True
        
        # Extract date if present
        date_pattern = r'for\s+(\w+day,\s+\w+\s+\d+(?:st|nd|rd|th)?(?:,\s+\d{4})?)'
        date_match = re.search(date_pattern, text)
        if date_match:
            brackets['requested_date'] = date_match.group(1)

    # Check for message
    msg_match = re.search(r"\[message\](.*?)(?=\])", text)
    if msg_match:
        brackets['message'] = msg_match.group(1).strip()

    # Check for appointment details
    apt_match = re.search(r"Perfect! You\'re scheduled for (.*?) at (.*? [APM]+)", text)
    if apt_match:
        date_str = apt_match.group(1).strip()
        time_str = apt_match.group(2).strip()
        brackets['appointment_details'] = {
            "date": date_str,
            "time": time_str,
            "timestamp": None  # Will be set by central service
        }

    # Check for notes
    note_iter = re.finditer(r"\[note\](.*?)(?=\])", text)
    new_notes = [n.group(1).strip() for n in note_iter]
    if new_notes:
        brackets['notes'].extend(new_notes)

    # Check for confirmation
    if '[CONFIRMED]' in text:
        brackets['confirmed'] = True

    # Check for call end
    if '[call_ended]' in text:
        brackets['call_ended'] = True
        
    # Check for callback requests
    callback_match = re.search(r"\[call_back\]\s*\[(.*?)\]\s*\[(.*?)\]\s*\[(\d+(?:\.\d+)?)\](?:\s*\[(.*?)\])?", text)
    if callback_match:
        brackets['callback_requested'] = True
        brackets['callback_data'] = {
            "customer_name": callback_match.group(1).strip(),
            "reason": callback_match.group(2).strip(),
            "urgency": float(callback_match.group(3)),
            "phone": callback_match.group(4).strip() if callback_match.group(4) else None
        }

    return brackets

# Health check endpoint
@app.get("/health")
def health_check():
    return {"status": "ok", "service": "ai-model-service"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)