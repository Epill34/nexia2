from fastapi import FastAPI, Request, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from typing import Dict, List, Optional, Any
import os
import logging
import httpx
import base64
import time
import uuid
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("twilio-handler")

app = FastAPI(title="Nexia Twilio Handler")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Twilio client setup
account_sid = os.environ.get("TWILIO_ACCOUNT_SID")
auth_token = os.environ.get("TWILIO_AUTH_TOKEN")
twilio_client = Client(account_sid, auth_token)

# Central service URL
CENTRAL_SERVICE_URL = os.environ.get("CENTRAL_SERVICE_URL", "http://central-service:8000")

# Active calls
active_calls = {}

class CallRequest(BaseModel):
    customer_id: str
    phone_number: str
    customer_data: Optional[Dict[str, Any]] = None

@app.post("/place_call")
async def place_call(request: CallRequest):
    """Initiate an outbound call using Twilio"""
    try:
        # Create a unique call ID
        call_id = f"call_{int(time.time())}_{uuid.uuid4().hex[:8]}"
        
        # Store customer data for this call
        active_calls[call_id] = {
            "customer_id": request.customer_id,
            "phone_number": request.phone_number,
            "customer_data": request.customer_data or {},
            "conversation_id": f"{request.customer_id}_{call_id}",
            "start_time": time.time(),
            "status": "initiating"
        }
        
        # Create TwiML for the call
        voice_response = VoiceResponse()
        
        # Add webhook to handle call events
        voice_response.say("Connecting to Nexia AI Receptionist", voice="alice")
        voice_response.redirect(f"https://nexia.it.com/handle_call?call_id={call_id}")


        
        # Initiate the call with Twilio
        call = twilio_client.calls.create(
            to=request.phone_number,
            from_=os.environ.get("TWILIO_PHONE_NUMBER", "+13527584282"),
            twiml=str(voice_response),
            status_callback=f"https://nexia.it.com/call_status?call_id={call_id}",
            status_callback_event=["initiated", "ringing", "answered", "completed"],
            status_callback_method="POST"
        )
        
        logger.info(f"Initiated call to {request.phone_number} with call ID {call_id}, Twilio SID: {call.sid}")
        
        # Update call record with Twilio SID
        active_calls[call_id]["twilio_sid"] = call.sid
        
        return {
            "status": "initiated",
            "call_id": call_id,
            "twilio_sid": call.sid,
            "conversation_id": active_calls[call_id]["conversation_id"]
        }
        
    except Exception as e:
        logger.error(f"Error in place_call: {e}")
        raise HTTPException(status_code=500, detail=f"Call initiation error: {str(e)}")

@app.post("/call_status")
async def call_status(request: Request, call_id: str):
    """Handle Twilio call status callback"""
    form_data = await request.form()
    call_status = form_data.get("CallStatus")
    call_sid = form_data.get("CallSid")
    
    logger.info(f"Call status update for {call_id} ({call_sid}): {call_status}")
    
    if call_id in active_calls:
        active_calls[call_id]["status"] = call_status
        
        # Handle call completion
        if call_status in ["completed", "failed", "busy", "no-answer", "canceled"]:
            active_calls[call_id]["end_time"] = time.time()
            active_calls[call_id]["duration"] = active_calls[call_id]["end_time"] - active_calls[call_id]["start_time"]
            
            # Log call details
            logger.info(f"Call {call_id} ended with status {call_status}, duration: {active_calls[call_id]['duration']:.1f}s")
    
    return Response(status_code=200)

@app.post("/handle_call")
async def handle_call(request: Request, call_id: str):
    """Main handler for call interaction"""
    if call_id not in active_calls:
        return VoiceResponse().say("Call ID not found").hangup()
    
    # Create a TwiML response
    response = VoiceResponse()
    
    # Set up Gather to capture user speech
    gather = Gather(
        input="speech",
        action=f"/process_speech?call_id={call_id}",
        method="POST",
        speechTimeout="auto",
        enhanced=True,
        speechModel="phone_call"
    )
    
    # If this is the first exchange, play greeting
    call_data = active_calls[call_id]
    customer_name = call_data.get("customer_data", {}).get("name", "")
    
    if "ai_response" not in call_data:
        # Initial greeting
        greeting = f"Hello, this is Nexia, a virtual receptionist for {call_data.get('customer_data', {}).get('business_name', 'your business')}. How may I help you today?"
        gather.say(greeting, voice="alice")
        
        # Store this as the first AI response
        call_data["ai_response"] = greeting
    else:
        # Play the last AI response
        gather.say(call_data["ai_response"], voice="alice")
    
    response.append(gather)
    
    # Add a fallback in case the caller doesn't speak
    response.redirect(f"/handle_call_fallback?call_id={call_id}")
    
    return Response(content=str(response), media_type="application/xml")

@app.post("/process_speech")
async def process_speech(request: Request, call_id: str):
    """Process speech from the caller"""
    if call_id not in active_calls:
        return VoiceResponse().say("Call ID not found").hangup()
    
    form_data = await request.form()
    speech_result = form_data.get("SpeechResult")
    call_data = active_calls[call_id]
    
    logger.info(f"Speech received for call {call_id}: {speech_result}")
    
    if not speech_result:
        # No speech detected, try again
        response = VoiceResponse()
        response.redirect(f"/handle_call?call_id={call_id}")
        return Response(content=str(response), media_type="application/xml")
    
    # Update call data with user speech
    if "conversation" not in call_data:
        call_data["conversation"] = []
    
    call_data["conversation"].append({
        "role": "user",
        "content": speech_result,
        "timestamp": time.time()
    })
    
    try:
        # Process with central service
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Create dummy audio data (since we already have the transcript)
            # In a real implementation, you'd get the audio from Twilio's recording feature
            dummy_audio = base64.b64encode(b"dummy audio data").decode("utf-8")
            
            central_response = await client.post(
                f"{CENTRAL_SERVICE_URL}/process_audio",
                json={
                    "audio_base64": dummy_audio,
                    "customer_id": call_data["customer_id"],
                    "conversation_id": call_data["conversation_id"],
                    "call_id": call_id,
                    "transcript_override": speech_result  # Pass transcript directly
                }
            )
            
            if central_response.status_code != 200:
                logger.error(f"Error from central service: {central_response.text}")
                response = VoiceResponse()
                response.say("I'm sorry, I'm having trouble understanding right now. Please try again.", voice="alice")
                response.redirect(f"/handle_call?call_id={call_id}")
                return Response(content=str(response), media_type="application/xml")
            
            ai_response_data = central_response.json()
            ai_text = ai_response_data["text"]
            
            # Update call data with AI response
            call_data["ai_response"] = ai_text
            call_data["conversation"].append({
                "role": "assistant",
                "content": ai_text,
                "timestamp": time.time()
            })
            
            # Check if call has ended
            if ai_response_data.get("call_ended", False):
                response = VoiceResponse()
                response.say(ai_text, voice="alice")
                response.hangup()
                
                # Mark call as completed
                call_data["status"] = "completed"
                call_data["end_time"] = time.time()
                call_data["duration"] = call_data["end_time"] - call_data["start_time"]
                
                logger.info(f"Call {call_id} ended by AI, duration: {call_data['duration']:.1f}s")
                
                return Response(content=str(response), media_type="application/xml")
            
            # Continue the conversation
            response = VoiceResponse()
            response.redirect(f"/handle_call?call_id={call_id}")
            return Response(content=str(response), media_type="application/xml")
            
    except Exception as e:
        logger.error(f"Error in process_speech: {e}")
        response = VoiceResponse()
        response.say("I'm sorry, I experienced a technical issue. Please try again.", voice="alice")
        response.redirect(f"/handle_call?call_id={call_id}")
        return Response(content=str(response), media_type="application/xml")

@app.post("/handle_call_fallback")
async def handle_call_fallback(request: Request, call_id: str):
    """Fallback handler if the caller doesn't speak"""
    if call_id not in active_calls:
        return VoiceResponse().say("Call ID not found").hangup()
    
    response = VoiceResponse()
    response.say("I didn't hear anything. Please speak when you're ready.", voice="alice")
    response.redirect(f"/handle_call?call_id={call_id}")
    return Response(content=str(response), media_type="application/xml")

@app.post("/incoming_call")
async def incoming_call(request: Request):
    """Handle incoming Twilio calls"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    caller_number = form_data.get("From")
    
    # Create call ID
    call_id = f"incoming_{int(time.time())}_{uuid.uuid4().hex[:8]}"
    
    # TODO: Look up which customer this call belongs to based on the called number
    # This is a simplified version that assumes a single customer
    customer_id = os.environ.get("DEFAULT_CUSTOMER_ID", "default_customer")
    
    # Store call data
    active_calls[call_id] = {
        "customer_id": customer_id,
        "phone_number": caller_number,
        "twilio_sid": call_sid,
        "conversation_id": f"{customer_id}_incoming_{call_id}",
        "start_time": time.time(),
        "status": "in-progress",
        "direction": "incoming",
        "customer_data": {
            "business_name": os.environ.get("DEFAULT_BUSINESS_NAME", "Your Business")
        }
    }
    
    logger.info(f"Incoming call from {caller_number} with SID {call_sid}, assigned call ID {call_id}")
    
    # Create TwiML to handle the call
    response = VoiceResponse()
    response.say("Thank you for calling. Connecting you to our virtual receptionist.", voice="alice")
    response.redirect(f"/handle_call?call_id={call_id}")
    
    return Response(content=str(response), media_type="application/xml")

@app.get("/get_call/{call_id}")
async def get_call(call_id: str):
    """Get details about a specific call"""
    if call_id not in active_calls:
        raise HTTPException(status_code=404, detail="Call not found")
    
    return {
        "call_id": call_id,
        "details": active_calls[call_id]
    }

@app.get("/get_calls/{customer_id}")
async def get_calls(customer_id: str, status: Optional[str] = None, limit: int = 10):
    """Get calls for a specific customer"""
    customer_calls = []
    
    for cid, call_data in active_calls.items():
        if call_data["customer_id"] == customer_id:
            if status is None or call_data["status"] == status:
                customer_calls.append({
                    "call_id": cid,
                    "details": call_data
                })
    
    # Sort by start time (newest first)
    customer_calls.sort(key=lambda x: x["details"]["start_time"], reverse=True)
    
    return {
        "customer_id": customer_id,
        "calls": customer_calls[:limit],
        "total": len(customer_calls)
    }

# Health check endpoint
@app.get("/health")
def health_check():
    return {
        "status": "ok", 
        "service": "twilio-handler",
        "active_calls": len(active_calls)
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)